"""Diálogo «Ejecutar penhmain» (0.13.0, ADR-017): ejecutar el .in en su carpeta, con la ventana de resultados y la
cascada de partículas.

Dice qué se va a hacer antes de hacerlo: qué penhmain (el elegido se copia a la carpeta del .in) y en qué terminal,
qué ficheros faltan o se copian, qué resultados pide el .in, cuántas partículas y cuánto tiempo, cuántas lluvias se
siguen para verlas en 3D (la cascada: las primeras, simuladas aparte con el mismo penhmain) y qué pasa con los
resultados de una simulación anterior. Al aceptar, lo hace (``penhan.run``) y abre el terminal; la ventana principal
abre después la de resultados.
"""

from __future__ import annotations

import html
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..penhan import cascade as cs
from ..penhan import goals as gl
from ..penhan import programs as pg
from ..penhan import run as rn
from .screen_fit import fit_to_screen
from .settings import penhan_setup

TERMINAL_KEY = "ejecutar/terminal"  # Windows: «linux» (WSL) o «windows» (cmd), lo último que se eligió

OK_COLOR = "#1a7f37"
WARN_COLOR = "#9a6700"
BAD_COLOR = "#b42318"


def _span(text: str, color: str) -> str:
    return f"<span style='color:{color}'>{text}</span>"


def _names(names: list[str], limit: int = 0, escape: bool = True) -> str:
    """«a, b y c», o «a, b, c y 3 más» con ``limit``."""
    names = [html.escape(n) if escape else n for n in names]
    if limit and len(names) > limit:
        return ", ".join(names[:limit]) + f" y {len(names) - limit} más"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


class _Copier(QObject):
    """Copia en otro hilo lo que penhmain necesita y está en otro sitio (ENDF de la base de datos…)."""

    done = Signal(object, str, bool)  # (ficheros copiados, error, cancelada)
    progress = Signal(int, int)

    def __init__(self, plan: rn.RunPlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="argus-ejecutar", daemon=True).start()

    def _run(self) -> None:
        try:
            out = rn.copy_needed(self.plan, lambda a, b, _t: self._emit(self.progress, a, b), self.cancel.is_set)
        except rn.CopyCancelled:
            self._emit(self.done, None, "", True)
            return
        except Exception as exc:  # noqa: BLE001 - tiene que llegar a la ventana
            self._emit(self.done, None, f"{exc.strerror or exc}" if isinstance(exc, OSError) and exc.strerror
                       else str(exc), False)
            return
        self._emit(self.done, out, "", False)

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:  # el diálogo ya no existe
            pass


class _WslCheck(QObject):
    """Pregunta a WSL en otro hilo si está y qué puede ejecutar (unos segundos la primera vez)."""

    done = Signal(object)

    def start(self) -> None:
        threading.Thread(target=self._run, name="argus-wsl", daemon=True).start()

    def _run(self) -> None:
        try:
            st = pg.wsl_status()
        except Exception as exc:  # noqa: BLE001 - tiene que llegar a la ventana
            st = pg.WslStatus(False, str(exc))
        try:
            self.done.emit(st)
        except RuntimeError:  # el diálogo ya no existe
            pass


def results_summary(doc, geo) -> list[str]:
    """Lo que pide el .in, por objetivos («Dosis en 3D (caja de vóxeles): 51 × 51 × 101 vóxeles»…)."""
    from ..core.numfmt import plural
    from ..penhan import sections as sc

    out = []
    for g in gl.GOALS:
        try:
            st = gl.status(doc, g.key, geo)
        except (KeyError, ValueError):
            st = ""
        if st:
            out.append(f"{g.title}: {st}")
    try:
        ang = sc.angular_detectors(doc)
    except (KeyError, ValueError, TypeError):
        ang = []
    if ang:
        out.append(f"Lo que sale en una dirección: {plural(len(ang), 'detector angular', 'detectores angulares')} "
                   "(ANGDET)")
    return out


class RunDialog(QDialog):
    """Ejecutar penhmain con el .in abierto en su carpeta."""

    def __init__(self, parent, ctrl, session, settings, *, save=None, outputs_bounds=None):
        """``ctrl``: el .in (InputController); ``save()``: guarda lo cambiado (True si se ha guardado);
        ``outputs_bounds``: la caja de lo que tiene material (para la matriz de dosis de «Elegir resultados»)."""
        super().__init__(parent)
        self.setWindowTitle("Ejecutar penhmain")
        self.ctrl = ctrl
        self.session = session
        self.settings = settings
        self.save = save
        self.outputs_bounds = outputs_bounds
        self.in_path = Path(ctrl.path)
        self.plan: rn.RunPlan | None = None
        self._folder_exe: rn.ExeChoice | None = None  # en un conflicto, el penhmain que ya está en la carpeta
        self.cascade_exe: Path | None = None  # el penhmain que se ha ejecutado (para la cascada)
        self._closing = False  # se ha pedido cerrar mientras se copiaba
        self.handle: rn.RunHandle | None = None  # al ejecutar
        self.stash: Path | None = None  # dónde se guardaron los resultados anteriores
        self.cascade_n: int | None = None  # lluvias de la cascada que hay que simular (al ejecutar)
        self._copier: _Copier | None = None
        self._errors: list = []
        lay = QVBoxLayout(self)
        intro = QLabel(f"Se ejecuta penhmain con <b>{html.escape(self.in_path.name)}</b> en su carpeta, "
                       f"<tt>{html.escape(str(self.in_path.parent))}</tt>, en un terminal donde se ve al momento. "
                       "Antes se copian allí el penhmain elegido y lo que falte; al empezar se abre la ventana de "
                       "resultados, que sigue la simulación.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        body = QVBoxLayout(inner)
        area.setWidget(inner)
        lay.addWidget(area, 1)
        # penhmain
        box = QGroupBox("penhmain")
        bl = QVBoxLayout(box)
        row = QHBoxLayout()
        setup = penhan_setup(settings)
        self.exe = QLineEdit(str(setup.penhmain) if setup.penhmain else "")
        self.exe.setPlaceholderText("el de la carpeta del .in (o elija uno: penhmain.x, penhmain.exe…)")
        self.exe.textChanged.connect(lambda _t: self._timer.start())
        b = QPushButton("Examinar…")
        b.setAutoDefault(False)  # Intro ejecuta (el botón «Ejecutar»); no abre «Examinar…»
        b.clicked.connect(self._browse_exe)
        row.addWidget(self.exe, 1)
        row.addWidget(b)
        bl.addLayout(row)
        self.exe_state = QLabel("")
        self.exe_state.setWordWrap(True)
        self.exe_state.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.exe_state)
        self.conflict = QWidget()
        cl = QVBoxLayout(self.conflict)
        cl.setContentsMargins(12, 0, 0, 0)
        self.use_folder = QRadioButton("Usar el que ya está en la carpeta")
        self.replace = QRadioButton("Sustituirlo por el elegido (el de ahora se guarda con «.bak»)")
        self.use_folder.setChecked(True)
        self.use_folder.toggled.connect(lambda _on: self._exe_choice_changed())
        g = QButtonGroup(self)
        g.addButton(self.use_folder)
        g.addButton(self.replace)
        cl.addWidget(self.use_folder)
        cl.addWidget(self.replace)
        bl.addWidget(self.conflict)
        # El terminal donde se ve la simulación (en Windows se elige: el de Linux, con WSL, o la consola).
        self.term_row = QWidget()
        tl = QHBoxLayout(self.term_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("Terminal:"))
        self.rb_linux = QRadioButton("Linux (WSL)")
        self.rb_windows = QRadioButton("Windows (cmd)")
        g = QButtonGroup(self)
        g.addButton(self.rb_linux)
        g.addButton(self.rb_windows)
        tl.addWidget(self.rb_linux)
        tl.addWidget(self.rb_windows)
        tl.addStretch(1)
        self.rb_linux.toggled.connect(self._shell_toggled)
        bl.addWidget(self.term_row)
        self.term_state = QLabel("")
        self.term_state.setWordWrap(True)
        self.term_state.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.term_state)
        self._wsl_check: _WslCheck | None = None
        self._wsl_result: pg.WslStatus | None = None  # lo que ha dicho WSL (por si no quedó en su caché)
        self._shells: list[rn.ShellChoice] = []
        body.addWidget(box)
        # Ficheros
        box = QGroupBox("Ficheros que lee penhmain")
        bl = QVBoxLayout(box)
        self.files_state = QLabel("")
        self.files_state.setWordWrap(True)
        self.files_state.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.files_state)
        self.gen = QPushButton("Generar los .mat que faltan…")
        self.gen.setAutoDefault(False)
        self.gen.setToolTip("Con su penhmat, en la carpeta del .in (y se copian los ENDF que necesitan)")
        self.gen.clicked.connect(self.generate_materials)
        bl.addWidget(self.gen, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(box)
        # Resultados que pide
        box = QGroupBox("Resultados que pide el .in")
        bl = QVBoxLayout(box)
        self.results = QLabel("")
        self.results.setWordWrap(True)
        self.results.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.results)
        self.choose = QPushButton("Elegir resultados…")
        self.choose.setAutoDefault(False)
        self.choose.setToolTip("Dosis en 3D con su matriz de vóxeles, espectros y fluencia en un cuerpo, espacio de "
                               "fases, distribuciones angulares…")
        self.choose.clicked.connect(self._choose_outputs)
        bl.addWidget(self.choose, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(box)
        # Trabajo (partículas, tiempo, semillas, volcado…): la misma sección que en el panel «Simulación», con las
        # lluvias que se siguen para verlas en 3D (la cascada) debajo del tiempo.
        from .cascade_ui import default_showers, with_run
        from .sim_outputs import JobSection
        from .sim_widgets import row as form_row

        box = QGroupBox("Trabajo")
        bl = QVBoxLayout(box)
        self.job = JobSection()
        self.job.apply.connect(self._apply)
        self.showers = QSpinBox()
        self.showers.setRange(0, cs.MAX_SHOWERS)
        self.showers.setSingleStep(100)
        self.showers.setSpecialValueText("ninguna")
        self.showers.setValue(default_showers(settings) if with_run(settings) else 0)
        self.showers_text = QLabel()
        self.showers_text.setStyleSheet("color: #666;")
        self.showers.valueChanged.connect(lambda _v: self._showers_text())
        self.job.form.insertRow(2, "Lluvias:", form_row(self.showers, self.showers_text))
        bl.addWidget(self.job)
        self.showers_note = note = QLabel()
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        bl.addWidget(note)
        self._showers_text()
        body.addWidget(box)
        # Resultados anteriores y otra simulación en marcha
        self.prev_box = QGroupBox("Resultados de una simulación anterior en la carpeta")
        bl = QVBoxLayout(self.prev_box)
        self.prev_state = QLabel("")
        self.prev_state.setWordWrap(True)
        bl.addWidget(self.prev_state)
        self.keep_prev = QRadioButton("Guardarlos en una subcarpeta «anterior-…» (recomendado)")
        self.overwrite = QRadioButton("Dejar que penhmain los sobrescriba")
        self.keep_prev.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.keep_prev)
        g.addButton(self.overwrite)
        bl.addWidget(self.keep_prev)
        bl.addWidget(self.overwrite)
        body.addWidget(self.prev_box)
        self.running = QLabel("")
        self.running.setWordWrap(True)
        self.running.setTextFormat(Qt.TextFormat.RichText)
        body.addWidget(self.running)
        self.run_anyway = QCheckBox("Ejecutarlo igualmente")
        body.addWidget(self.run_anyway)
        body.addStretch(1)
        # Errores, estado y botones
        self.errors = QLabel("")
        self.errors.setWordWrap(True)
        self.errors.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.errors)
        self.force = QCheckBox("Ejecutar aunque ARGUS vea errores (penhmain se pararía al leer el .in)")
        lay.addWidget(self.force)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Ejecutar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.clicked.connect(self.run)
        self.cancel_btn = bb.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self.reject)
        lay.addWidget(bb)
        self.go.setDefault(True)  # ya dentro del diálogo (antes, Qt no lo tomaba por el botón por defecto)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.refresh)
        ctrl.changed.connect(self._doc_changed)
        self.run_anyway.toggled.connect(lambda _on: self._update_go())
        self.force.toggled.connect(lambda _on: self._update_go())
        fit_to_screen(self, 820, 760)
        self.refresh()

    # -- estado -------------------------------------------------------------------------------------------------
    def _exe_path(self) -> Path | None:
        text = self.exe.text().strip().strip('"')
        if not text:
            return None
        p = Path(text).expanduser()
        return p if p.is_absolute() else self.in_path.parent / p

    def _browse_exe(self) -> None:
        start = str(self._exe_path() or self.in_path.parent)
        path, _ = QFileDialog.getOpenFileName(self, "Ejecutable de penhmain", start)
        if path:
            self.exe.setText(path)

    def _doc_changed(self) -> None:
        if self._copier is None:
            self._timer.start()

    def _apply(self, text: str, fn) -> None:
        try:
            self.ctrl.edit(text, fn)
        except ValueError as exc:
            QMessageBox.warning(self, "ARGUS", str(exc))

    def refresh(self) -> None:
        if self._copier is not None:
            return
        self._timer.stop()
        doc = self.ctrl.doc
        setup = penhan_setup(self.settings)
        geo = self.session.geo if self.session is not None and self.session.ok else None
        # Sin esperar a WSL en la interfaz (la primera vez tarda lo que tarde en arrancar, hasta 90 s): se le pregunta
        # en otro hilo y, mientras, un penhmain de Linux queda «comprobando…» (0.16.3).
        kw = {"launch_fn": self._launch_while_checking}
        try:
            self.plan = rn.plan_run(self.in_path, doc, penhmain=self._exe_path(), database=setup.database,
                                    endf_dir=setup.endf, **kw)
        except OSError as exc:
            self.plan = None
            self.status.setText(f"No se puede preparar la ejecución: {exc}")
            self.go.setEnabled(False)
            return
        plan = self.plan
        self._folder_exe = (rn.use_folder_exe(plan.exe, self._launch_while_checking)
                            if plan.exe.status == "conflict" else None)
        self.job.set_context(doc, geo, self.in_path.parent)
        self._fill_exe(plan)
        self._fill_terminal()
        self._fill_files(plan)
        lines = results_summary(doc, geo)
        self.results.setText("<br>".join(html.escape(t) for t in lines) if lines else _span(
            "Solo el resumen (penhmain-res.dat) y las distribuciones de lo que sale: el .in no pide nada más.",
            WARN_COLOR))
        self._fill_previous(plan)
        self._fill_errors()
        self._showers_text()  # (las tandas dependen del penhmain que se usará)
        self._update_go()

    def _launch_while_checking(self, info: pg.ExeInfo) -> pg.Launch:
        """Cómo se ejecutará ``info``, sin esperar a WSL en el hilo de la interfaz: si hace falta y aún no se sabe si
        funciona, se le pregunta en otro hilo y, mientras, ``pg.WSL_CHECKING`` (al saberlo, ``_wsl_known``)."""
        if not pg.needs_wsl(info, pg.HOST):
            return pg.launch_for(info, host=pg.HOST)
        known = rn.pg_wsl_cached() or self._wsl_result
        if known is None:
            self._check_wsl()
            return pg.WSL_CHECKING
        return pg.launch_for(info, host=pg.HOST, wsl=lambda: known)

    def _check_wsl(self) -> None:
        """Pregunta a WSL en otro hilo (una sola vez a la vez)."""
        if self._wsl_check is None:
            self._wsl_check = _WslCheck(self)
            self._wsl_check.done.connect(self._wsl_known)
            self._wsl_check.start()

    def _fill_exe(self, plan: rn.RunPlan) -> None:
        exe = plan.exe
        name = html.escape(exe.target.name if exe.target is not None else (exe.chosen.name if exe.chosen else ""))
        how = ""
        if exe.launch is not None and exe.launch.ok:
            how = f" ({html.escape(exe.launch.note)})"
        if exe.status == "none":
            text = _span("Falta elegir penhmain: no hay ninguno en la carpeta del .in ni en «Programas de penhan».",
                         BAD_COLOR)
        elif exe.status == "missing":
            text = _span(f"No se encuentra {html.escape(str(exe.chosen))}.", BAD_COLOR)
        elif exe.status == "inside":
            text = f"Se ejecuta <b>{name}</b>, que ya está en la carpeta{how}."
            if exe.from_folder:
                text = f"Se ejecuta <b>{name}</b>, el que está en la carpeta del .in{how}."
        elif exe.status == "same":
            text = f"<b>{name}</b> ya está en la carpeta (un fichero igual){how}."
        elif exe.status == "copy":
            text = f"Se copia <b>{name}</b> a la carpeta del .in{how}."
            if exe.libs:
                text += " Con sus bibliotecas: " + ", ".join(html.escape(p.name) for p in exe.libs) + "."
        else:  # conflict
            text = _span(f"En la carpeta ya hay otro <b>{name}</b>, distinto del elegido:", WARN_COLOR)
            fe = self._folder_exe
            for label, choice in (("el de la carpeta", fe), ("el elegido", exe)):
                if choice is not None and choice.launch is not None:
                    note = choice.launch.note if choice.launch.ok else choice.problem
                    text += "<br>· " + (html.escape(f"{label}: {note}") if choice.launch.ok else
                                        _span(html.escape(f"{label}: {note}"),
                                              WARN_COLOR if choice.launch.pending else BAD_COLOR))
        if exe.launch is not None and not exe.launch.ok and exe.status not in ("none", "missing", "conflict"):
            text += "<br>" + _span(html.escape(exe.problem[:1].upper() + exe.problem[1:] + "…" if exe.launch.pending
                                               else exe.problem), WARN_COLOR if exe.launch.pending else BAD_COLOR)
        self.exe_state.setText(text)
        self.conflict.setVisible(exe.status == "conflict")

    # -- el terminal ----------------------------------------------------------------------------------------------
    def _exe_choice_changed(self) -> None:
        self._fill_terminal()
        self._showers_text()  # las tandas son las del penhmain que se usará
        self._update_go()

    def _fill_terminal(self) -> None:
        """El terminal donde se verá penhmain: en Linux y macOS, el que hay; en Windows, el de Linux (WSL) o la consola
        de Windows, según el penhmain y lo que se elija."""
        run_exe = self._exe_to_run()
        launch = run_exe.launch if run_exe is not None else None
        if pg.HOST != "windows":
            self.term_row.setVisible(False)
            term = rn.describe_terminal(launch, host=pg.HOST)
            if term.background:
                text = "Terminal: " + _span("no se ha encontrado ninguno: penhmain se ejecutará en segundo plano y su "
                                            "salida irá a penhmain.out (la ventana de resultados la sigue).",
                                            WARN_COLOR)
            else:
                what = term.name if pg.HOST == "macos" else f"un terminal ({term.name})"
                text = (f"Terminal: se abre {html.escape(what)}; la simulación se ve al momento y la salida se guarda "
                        "también en penhmain.out.")
            self.term_state.setText(text)
            return
        self.term_row.setVisible(True)
        info = run_exe.info if run_exe is not None else None
        known = rn.pg_wsl_cached() or self._wsl_result
        if known is None:
            self._shells = []
            for b in (self.rb_linux, self.rb_windows):
                b.setEnabled(False)
            self.term_state.setText(_span("Comprobando WSL (el Linux de Windows)…", WARN_COLOR))
            self._check_wsl()
            return
        self._shells = rn.shell_choices(info, host=pg.HOST, wsl=lambda: known)
        by_key = {c.key: c for c in self._shells}
        for key, b in ((rn.SHELL_LINUX, self.rb_linux), (rn.SHELL_WINDOWS, self.rb_windows)):
            c = by_key.get(key)
            b.setText(c.label if c is not None else b.text())
            b.setEnabled(bool(c and c.ok))
            b.setToolTip(c.note if c is not None else "")
        pref = str(self.settings.value(TERMINAL_KEY, "") or "")
        current = rn.SHELL_LINUX if self.rb_linux.isChecked() else (
            rn.SHELL_WINDOWS if self.rb_windows.isChecked() else None)
        want = rn.default_shell(self._shells, current if current is not None and by_key.get(current) and
                                by_key[current].ok else pref)
        for key, b in ((rn.SHELL_LINUX, self.rb_linux), (rn.SHELL_WINDOWS, self.rb_windows)):
            b.blockSignals(True)
            b.setChecked(key == want)
            b.blockSignals(False)
        self._show_shell_note()

    def _wsl_known(self, st) -> None:
        """WSL ha contestado (en otro hilo): el plan otra vez, ya con lo que dice (el penhmain de Linux, el
        terminal…)."""
        self._wsl_check = None
        self._wsl_result = st
        self.refresh()

    def _shell(self) -> str | None:
        """El terminal elegido (Windows): rn.SHELL_LINUX, rn.SHELL_WINDOWS o None (sin poder elegir)."""
        if pg.HOST != "windows":
            return None
        if self.rb_linux.isChecked() and self.rb_linux.isEnabled():
            return rn.SHELL_LINUX
        if self.rb_windows.isChecked() and self.rb_windows.isEnabled():
            return rn.SHELL_WINDOWS
        return None

    def _shell_toggled(self, _on: bool) -> None:
        shell = self._shell()
        if shell is not None:
            self.settings.setValue(TERMINAL_KEY, shell)
        self._show_shell_note()
        self._update_go()

    def _show_shell_note(self) -> None:
        shell = self._shell()
        c = next((c for c in self._shells if c.key == shell), None)
        others = [c for c in self._shells if not c.ok and c.key != shell]
        parts = []
        if c is not None:
            note = c.note if c.note.startswith("penhmain") else c.note[:1].upper() + c.note[1:]
            parts.append(html.escape(note + "."))
        for o in others:
            parts.append(_span(html.escape(f"{o.label}: no, {o.note}."), "#666"))
        if c is None and self._shells:
            parts.insert(0, _span("No hay ningún terminal en que se pueda ejecutar este penhmain.", BAD_COLOR))
        self.term_state.setText("<br>".join(parts))

    def _fill_files(self, plan: rn.RunPlan) -> None:
        parts = []
        n_ok = sum(1 for it in plan.files.items if it.status in ("same", "absolute", "up")
                   and it.kind not in ("exe", "dll", "txt", "ctjson"))  # lo que lee penhmain (no materiales.txt…)
        parts.append(f"{n_ok} ya {'está' if n_ok == 1 else 'están'} en la carpeta.")
        copy = plan.to_copy
        if copy:
            parts.append(f"Se {'copia' if len(copy) == 1 else 'copian'} {len(copy)}: "
                         + _names([it.name for it in copy], 6) + ".")
        mats = plan.missing_mats
        if mats:
            parts.append(_span(f"{'Falta' if len(mats) == 1 else 'Faltan'} {len(mats)} .mat: "
                               + _names([it.name for it in mats]) + ".", BAD_COLOR))
        others = [it for it in plan.missing if it.kind != "mat"]
        if others:
            parts.append(_span(f"{'Falta' if len(others) == 1 else 'Faltan'}: "
                               + _names([it.name for it in others]) + ".", BAD_COLOR))
        # Nombres que penhmain no encontraría (un blanco delante, una ruta de Windows o tildes para un penhmain de
        # Linux): hay que cambiarlos en el .in.
        for p in plan.name_problems(self._exe_to_run() or plan.exe):
            parts.append(_span(html.escape(p[:1].upper() + p[1:] + "."), BAD_COLOR))
        if plan.resume:
            text = f"RESUME: penhmain sigue la simulación guardada en {html.escape(plan.resume)}"
            if plan.resume_diffs:  # con las rejillas y los intervalos del volcado (DOSER, IMDETR…)
                text += (", con las rejillas y los intervalos de entonces, no con los del .in ("
                         + html.escape("; ".join(plan.resume_diffs)) + ")")
            parts.append(_span(text + ".", WARN_COLOR))
        elif plan.resume_missing:
            parts.append(f"RESUME: {html.escape(plan.resume_missing)} {html.escape(plan.resume_note or 'no existe')}"
                         ": penhmain empieza desde cero.")
        parts += [html.escape(n) for n in plan.notes]
        self.files_state.setText(" ".join(parts))
        # Con ENDF que faltan, el mismo diálogo («Generar .mat y ENDF…») dice dónde se buscan y deja elegir su carpeta
        # (revisión de 0.16: «Ejecutar» no se podía usar y no decía cómo arreglarlo).
        endf = [it for it in others if it.kind == "endf"]
        self.gen.setVisible(bool(mats or endf))
        if mats:
            self.gen.setText("Generar los .mat que faltan…")
            self.gen.setToolTip("Con su penhmat, en la carpeta del .in (y se copian los ENDF que necesitan)")
        else:
            self.gen.setText("Buscar los ENDF que faltan…")
            self.gen.setToolTip("«Generar .mat y ENDF…»: dónde se buscan los ficheros ENDF y «Carpeta de los ENDF…» "
                                "para elegir la suya")

    def _fill_previous(self, plan: rn.RunPlan) -> None:
        prev = plan.previous
        self.prev_box.setVisible(bool(prev))
        if prev:
            stamps = []
            for p in prev:
                try:
                    stamps.append(p.stat().st_mtime)
                except OSError:
                    pass
            when = time.strftime("%d/%m/%Y %H:%M", time.localtime(max(stamps))) if stamps else ""
            self.prev_state.setText(f"{len(prev)} {'fichero' if len(prev) == 1 else 'ficheros'}"
                                    + (f" del {when}" if when else "") + ": "
                                    + _names([rn.relative_name(plan.folder, p) for p in prev], 5, escape=False) + ".")
        self.running.setVisible(bool(plan.running))
        self.run_anyway.setVisible(bool(plan.running))
        if plan.running:
            self.running.setText(_span("Parece que penhmain ya está simulando en esta carpeta ("
                                       + html.escape(plan.running.rstrip(". ")) + "): las dos simulaciones "
                                       "escribirían los mismos ficheros.", BAD_COLOR))

    def _diagnostics(self) -> list:
        from ..penhan.validate import validate_case
        from .settings import cttrack_fixed

        doc = self.ctrl.doc
        geo = self.session.geo if self.session is not None and self.session.ok else None
        out = [d for d in doc.diagnostics if d.severity == 2]
        try:
            out += [d for d in validate_case(doc, geo, self.in_path.parent,
                                             cttrack_fixed=cttrack_fixed(self.settings)) if d.severity == 2]
        except (OSError, ValueError):
            pass
        if self.session is not None and not self.session.ok:
            out += [d for d in self.session.diagnostics if d.severity == 2]
        # Los .mat que faltan se generan aquí y los ENDF que faltan se copian al ejecutar: no cuentan como error.
        mats = {it.name.lower() for it in self.plan.missing_mats} if self.plan is not None else set()
        copied = {Path(it.name).name.lower() for it in self.plan.to_copy} if self.plan is not None else set()

        def solved(d) -> bool:
            text = d.message.lower()
            if mats and any(m in text for m in mats):
                return True
            endf = set(re.findall(r"[\w.+\-]+\.endf", text))
            return bool(endf) and endf <= copied

        return [d for d in out if not solved(d)]

    def _fill_errors(self) -> None:
        errs = self._diagnostics()
        self._errors = errs
        if errs:
            items = "".join(f"<li>{html.escape(str(d))}</li>" for d in errs[:6])
            more = f"<li>… y {len(errs) - 6} más (panel de mensajes)</li>" if len(errs) > 6 else ""
            self.errors.setText(_span("penhmain se pararía con este .in:", BAD_COLOR) + f"<ul>{items}{more}</ul>")
        else:
            self.errors.setText("")
        self.errors.setVisible(bool(errs))
        self.force.setVisible(bool(errs))

    def _exe_to_run(self) -> rn.ExeChoice | None:
        """El penhmain que se ejecutará según lo elegido (en un conflicto, el de la carpeta o el elegido)."""
        plan = self.plan
        if plan is None:
            return None
        if plan.exe.status == "conflict" and self.use_folder.isChecked() and self._folder_exe is not None:
            return self._folder_exe
        return plan.exe

    def problems(self) -> list[str]:
        """Lo que impide ejecutar ahora."""
        plan = self.plan
        if plan is None:
            return ["no se ha podido preparar la ejecución"]
        plan.shell = self._shell()
        run_exe = self._exe_to_run()
        out = plan.problems_with(run_exe)  # en un conflicto, lo del que se usará (el de la carpeta o el elegido)
        if pg.HOST == "windows" and self._shells and self._shell() is None and run_exe.runnable:
            out.append("elija el terminal")  # (mientras se pregunta a WSL, el de siempre: no se espera)
        if self.ctrl.changed_on_disk():
            out.append("el .in ha cambiado en el disco (otro programa) y penhmain leería ese: recárguelo (Archivo → "
                       "Recargar, F5) y revíselo")
        if plan.missing_mats:
            out.append("faltan .mat: genérelos con «Generar los .mat que faltan…»")
        if plan.running and not self.run_anyway.isChecked():
            out.append("penhmain parece estar simulando ya en esta carpeta")
        if self._errors and not self.force.isChecked():
            out.append("el .in tiene errores")
        return out

    def _update_go(self) -> None:
        probs = self.problems()
        self.go.setEnabled(not probs)
        self.status.setText("No se puede ejecutar: " + "; ".join(probs) + "." if probs else "")

    # -- acciones ------------------------------------------------------------------------------------------------
    def generate_materials(self) -> None:
        from .penhan_dialogs import CompleteMaterialsDialog

        geo = self.session.geo if self.session is not None and self.session.ok else None
        dlg = CompleteMaterialsDialog(self, self.ctrl.doc, geo, self.in_path.parent, self.settings,
                                      in_name=self.in_path.name)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()
        self.refresh()

    def _cascade_cap(self) -> int:
        """Cuántas lluvias guarda por ejecución el penhmain que se usará (lo aprendido con su prueba; si no se sabe,
        las 50 de penhmain.f)."""
        from .cascade_ui import known_cap

        plan = self.plan
        exe = None
        if plan is not None:
            run_exe = self._exe_to_run() or plan.exe
            if plan.exe.status == "conflict" and self.replace.isChecked():
                exe = plan.exe.chosen  # el elegido sustituirá al de la carpeta
            else:
                exe = run_exe.target if run_exe.target is not None and run_exe.target.is_file() else run_exe.chosen
        cap = None
        if exe is not None:
            try:
                st = Path(exe).stat()
                key = (str(exe), st.st_size, st.st_mtime_ns)
            except OSError:
                key = None
            if key is not None:
                memo = getattr(self, "_sha_memo", None)
                if memo is None or memo[0] != key:  # (su sha1 lee el ejecutable entero: no a cada cambio)
                    self._sha_memo = memo = (key, cs.file_sha1(exe))
                cap = known_cap(exe, self.settings, sha=memo[1]) if memo[1] else None
        return int(cap) if cap else cs.NPSHOW_DEFAULT

    def _showers_text(self) -> None:
        """Lo que se hará con las lluvias pedidas: cuántas tandas de penhmain (con las que guarda el penhmain que se
        usará, si ya se sabe: revisión de 0.16)."""
        from ..core.numfmt import plural

        cap = self._cascade_cap()
        self._showers_help(cap)
        n = int(self.showers.value())
        if n <= 0:
            self.showers_text.setText(f"(como mucho {cs.MAX_SHOWERS}): sin cascada")
            return
        k = len(cs.runs_for(n, cap))
        tandas = f"{plural(k, 'tanda')} de {cap}" if k > 1 else "una tanda"
        self.showers_text.setText(f"para verlas en 3D (como mucho {cs.MAX_SHOWERS}): {tandas}")

    def _showers_help(self, cap: int) -> None:
        """La ayuda de «Lluvias» y la nota de debajo, con las lluvias que guarda por ejecución este penhmain."""
        default = "" if cap != cs.NPSHOW_DEFAULT else f" (NPSHOW = {cs.NPSHOW_DEFAULT} en penhmain.f)"
        self.showers.setToolTip(
            "La cascada: cuántas de las primeras lluvias (cada partícula con todo lo que crea) se siguen para verlas "
            f"en 3D, animadas (como mucho {cs.MAX_SHOWERS}; 0, ninguna). penhmain guarda las trayectorias de {cap} "
            f"lluvias en cada ejecución{default}, así que ARGUS lo ejecuta aparte varias veces, en tandas de {cap} "
            f"con otras semillas: 1000 lluvias son {len(cs.runs_for(1000, cap))} tandas. La primera tanda es "
            f"idéntica a las primeras {cap} lluvias de la simulación.")
        self.showers_note.setText(
            "«Lluvias» es la cascada: se simula aparte con el mismo penhmain (en la carpeta «cascada» junto al .in), a "
            f"la vez que la simulación, en tandas de {cap} lluvias porque penhmain no guarda las trayectorias de más "
            "por ejecución, y se ve en la ventana de resultados. «Trayectorias» (abajo) son las que guarda la propia "
            f"simulación: sus primeras LTRACK + 1 lluvias (penhmain escribe también la siguiente a la última pedida; "
            f"como mucho {cap + 1}). Con protones, alfas o neutrones cada tanda tarda más: penhmain prepara sus datos "
            "cada vez (unos minutos).")

    def _choose_outputs(self) -> None:
        """«Elegir resultados…»: el mismo diálogo que en la ventana, aquí modal (este lo es)."""
        from .outputs_dialog import OutputsDialog

        main = self.parent()
        dlg = OutputsDialog(self, self.ctrl, self.session if self.session is not None and self.session.ok else None,
                            bounds=self.outputs_bounds, undo_action=getattr(main, "act_undo", None))
        for sig, name in ((dlg.showGridRequested, "show_dose_grid"), (dlg.fitGridRequested, "fit_dose_grid")):
            fn = getattr(main, name, None)
            if fn is not None:
                sig.connect(fn)
        try:
            dlg.exec()
            dlg.flush_pending()
        finally:
            dlg.deleteLater()
        self.refresh()

    def run(self) -> None:
        """Copia lo que falta, pone penhmain en la carpeta, guarda los resultados anteriores y abre el terminal."""
        if self._copier is not None:
            return
        if self.ctrl.dirty and self.save is not None:
            if not self.save():
                self.status.setText("No se ha guardado el .in: no se ejecuta.")
                return
        self.refresh()
        if self.problems():
            self._update_go()
            return
        plan = self.plan
        if plan.to_copy:
            self.go.setEnabled(False)
            self.progress.setVisible(True)
            self.progress.setRange(0, len(plan.to_copy))
            self.progress.setValue(0)
            self.status.setText("Copiando a la carpeta del .in lo que necesita penhmain…")
            c = _Copier(plan, self)
            self._copier = c
            c.progress.connect(lambda a, _b: self.progress.setValue(a))
            c.done.connect(self._copied)
            c.start()
            return
        self._launch()

    def _copied(self, out, err: str, cancelled: bool = False) -> None:
        self._copier = None
        self.progress.setVisible(False)
        if self._closing:
            # Se pulsó «Cancelar» (o se cerró) mientras se copiaba: no se ejecuta, aunque la copia hubiera llegado al
            # final antes de pararla (lo copiado se queda en la carpeta).
            super().reject()
            return
        if cancelled:
            self.status.setText("Copia cancelada: no se ha ejecutado penhmain (lo copiado se queda en la carpeta).")
            self.go.setEnabled(True)
            return
        if err:
            self.status.setText(f"No se ha ejecutado penhmain: {err}.")
            QMessageBox.warning(self, "ARGUS", f"No se ha ejecutado penhmain: {err}.")
            self.go.setEnabled(True)
            return
        self._launch()

    def _launch(self) -> None:
        plan = self.plan
        try:
            exe = plan.exe
            run_exe = self._exe_to_run() or exe
            if not run_exe.runnable:  # antes de tocar nada
                raise ValueError(run_exe.problem or "no se puede ejecutar penhmain")
            if exe.status == "conflict":
                if self.replace.isChecked():
                    rn.install_exe(exe, replace=True)
                else:
                    plan.exe = run_exe
            elif exe.status == "copy":
                rn.install_exe(exe)
            if plan.previous and self.keep_prev.isChecked():
                self.stash = rn.stash_outputs(plan.folder, plan.previous)
            plan.shell = self._shell()
            self.handle = rn.start(plan)
            self.cascade_exe = plan.exe.target
        except (OSError, ValueError) as exc:
            self.status.setText(f"No se ha podido ejecutar penhmain: {exc}")
            QMessageBox.warning(self, "ARGUS", f"No se ha podido ejecutar penhmain: {exc}")
            self.go.setEnabled(True)
            return
        from .cascade_ui import set_default_showers, set_with_run

        n = int(self.showers.value())
        set_with_run(n > 0, self.settings)
        if n > 0:
            self.cascade_n = n
            set_default_showers(n, self.settings)
        self.accept()

    def reject(self) -> None:
        if self._copier is not None:
            self._copier.cancel.set()
            self._closing = True  # al acabar de cancelar, se cierra
            self.status.setText("Cancelando la copia…")
            return
        super().reject()


__all__ = ["RunDialog", "results_summary"]
