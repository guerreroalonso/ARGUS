"""Opciones → «Programas de penhan…» y panel Materiales → «Generar .mat y ENDF…» (ADR-013).

ARGUS no incluye penhmat ni penhmain: cada usuario los compila en su ordenador. El primer diálogo guarda dónde
están (o que no hace falta penhmat) y dice qué es cada ejecutable y cómo se ejecutará aquí (directamente o con WSL);
el segundo genera con penhmat los .mat que faltan y copia a la carpeta del .in los ficheros ENDF que necesitan.
"""

from __future__ import annotations

import html
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import plural
from ..pengeom.ct import human_size
from ..penhan import matgen as mg
from ..penhan import programs as pg
from ..penhan.materials import no_endf_reason
from .screen_fit import fit_to_screen
from .settings import penhan_setup, save_penhan_setup

OK_COLOR, BAD_COLOR, WAIT_COLOR, GREY = "#1a7f37", "#b42318", "#8a5a00", "#777777"
TEST_MATERIAL = 278  # agua líquida en la lista de PENELOPE: rápida (solo O tiene datos ENDF)


class _Task(QObject):
    """Ejecuta ``fn(progress, cancel)`` en un hilo y entrega el resultado en el hilo de la interfaz."""

    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="argus-penhan", daemon=True).start()

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:  # el diálogo ya no existe
            pass

    def _run(self) -> None:
        try:
            res = self._fn(lambda a, b, t: self._emit(self.progress, int(a), int(b), str(t)), self.cancel.is_set)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo tiene que llegar a la ventana
            self._emit(self.failed, f"{type(exc).__name__}: {exc}")
            return
        self._emit(self.done, res)


def _colored(text: str, color: str) -> str:
    return f"<span style='color:{color}'>{html.escape(text)}</span>"


def _needs_wsl_check(info: pg.ExeInfo) -> bool:
    """¿Hace falta preguntar a WSL (unos segundos) para saber cómo se ejecuta?"""
    return pg.HOST == "windows" and info.system in ("linux", "script") and "status" not in pg._WSL_CACHE


def _path_row(owner, form: QFormLayout, label: str, value, kind: str, title: str, start) -> QLineEdit:
    row = QHBoxLayout()
    edit = QLineEdit(str(value or ""))
    b = QPushButton("Examinar…")

    def browse():
        here = edit.text().strip().strip('"') or str(start() or "")
        if kind == "dir":
            d = QFileDialog.getExistingDirectory(owner, title, here)
        else:
            d, _ = QFileDialog.getOpenFileName(owner, title, here)
        if d:
            edit.setText(d)

    b.clicked.connect(browse)
    row.addWidget(edit, 1)
    row.addWidget(b)
    w = QWidget()
    row.setContentsMargins(0, 0, 0, 0)
    w.setLayout(row)
    form.addRow(label, w)
    return edit


def _field_path(edit: QLineEdit) -> Path | None:
    text = edit.text().strip().strip('"')
    return Path(text).expanduser() if text else None


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def _is_database(path: Path) -> bool:
    """¿Parece la carpeta database de penhan? (con pdfiles o pdhan; ENDF_data puede faltar todavía)."""
    return (path / "pdfiles").is_dir() or (path / "pdhan").is_dir()


def _inside(path: Path, folder: Path) -> bool:
    """¿``path`` es ``folder`` o está dentro de ella?"""
    try:
        a, b = path.resolve(), folder.resolve()
    except OSError:
        a, b = path, folder
    return a == b or b in a.parents


# ---------------------------------------------------------------------------------------------------------------------
# Opciones → «Programas de penhan…»
# ---------------------------------------------------------------------------------------------------------------------

class PenhanSetupDialog(QDialog):
    """Dónde están la carpeta de penhan (su ``database``), penhmat y penhmain, o que no hace falta penhmat."""

    def __init__(self, parent, settings, start: str | Path | None = None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Programas de penhan")
        self.settings = settings
        self._tasks: set[_Task] = set()
        self._auto: dict[str, str] = {}  # campo -> lo que ha puesto ARGUS (se cambia si cambia la carpeta)
        setup = penhan_setup(settings)
        if setup.database is None:  # primera vez: se busca junto a los casos (…/ProgramasBase/penhan/database)
            from ..penhan.simfolder import find_database

            db = find_database(start) if start else None
            if db is not None:
                setup.database = db
                setup.penhmat = setup.penhmat or pg.find_penhmat(db)
                setup.penhmain = setup.penhmain or pg.find_penhmain(db)
        lay = QVBoxLayout(self)
        text = ("ARGUS no incluye penhmat ni penhmain: cada usuario los compila en su ordenador (optimizados para "
                "él) dentro de su carpeta de penhan, y pueden ser de Windows, de Linux o de macOS. Diga dónde está esa "
                "carpeta: ARGUS busca en ella la base de datos (database), penhmat y penhmain. Con penhmat genera los "
                ".mat que faltan (los tejidos de un TAC y los materiales de la lista de PENELOPE) y copia los ficheros "
                "ENDF que necesitan a la carpeta donde se simula.")
        if reason:
            text = f"<b>{html.escape(reason)}</b><br>{html.escape(text)}"
        else:
            text = html.escape(text)
        intro = QLabel(text)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        form = QFormLayout()
        lay.addLayout(form)
        here = lambda: _field_path(self.root) or start  # noqa: E731
        root = setup.database.parent if setup.database is not None else ""
        self.root = _path_row(self, form, "Carpeta de penhan:", root, "dir", "Carpeta de penhan", here)
        self.database = _path_row(self, form, "Base de datos:", setup.database, "dir",
                                  "Carpeta database de penhan (pdfiles, pdhan, ENDF_data)", here)
        self.db_state = self._state(form)
        self.penhmat = _path_row(self, form, "penhmat:", setup.penhmat, "file", "Ejecutable de penhmat", here)
        self.penhmat_state = self._state(form)
        self.penhmain = _path_row(self, form, "penhmain:", setup.penhmain, "file", "Ejecutable de penhmain", here)
        self.penhmain_state = self._state(form)
        endf = setup.endf or (setup.database / "ENDF_data" if setup.database is not None else None)
        self.endf = _path_row(self, form, "Ficheros ENDF:", endf, "dir", "Carpeta de los ficheros ENDF", here)
        self.endf.setToolTip("Con protones o alfas, penhmain lee de la carpeta donde se simula los ficheros ENDF que "
                             "lista cada .mat: ARGUS los copia de esta carpeta (por defecto, database/ENDF_data)")
        if setup.endf is None and endf is not None:
            self._auto["endf"] = str(endf)
        # Lo que había al abrir: los campos que estaban dentro de la carpeta de penhan (o de la base de datos) la
        # siguen cuando se cambia por otra (revisión de 0.16: cambiar «Carpeta de penhan» no cambiaba nada más).
        self._root_prev = _field_path(self.root)
        self._db_prev = _field_path(self.database)
        self.endf_state = self._state(form)
        self.rb_use = QRadioButton("Generar con este penhmat los .mat que falten (panel Materiales → «Generar .mat y "
                                   "ENDF…» y «Preparar la carpeta de simulación»)")
        self.rb_off = QRadioButton("No hace falta: los .mat los genero yo (ARGUS solo copia los ficheros ENDF)")
        group = QButtonGroup(self)
        group.addButton(self.rb_use)
        group.addButton(self.rb_off)
        (self.rb_off if setup.penhmat_off else self.rb_use).setChecked(True)
        lay.addWidget(self.rb_use)
        lay.addWidget(self.rb_off)
        test_row = QHBoxLayout()
        self.btn_test = QPushButton("Probar penhmat")
        self.btn_test.setToolTip("Genera agua líquida (n.º 278 de la lista de PENELOPE) en la carpeta database con un "
                                 "nombre temporal, comprueba el .mat y lo borra")
        self.btn_test.clicked.connect(self.test_penhmat)
        self.test_state = QLabel("")
        self.test_state.setWordWrap(True)
        self.test_state.setTextFormat(Qt.TextFormat.RichText)
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.test_state, 1)
        lay.addLayout(test_row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Guardar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._update)
        self.root.textChanged.connect(lambda _t: self._on_root())
        self.database.textChanged.connect(lambda _t: self._on_database())
        for e in (self.database, self.penhmat, self.penhmain, self.endf):
            e.textChanged.connect(lambda _t: self._timer.start())
        self.rb_use.toggled.connect(lambda _on: self._update())
        fit_to_screen(self, 820, 420)
        self._update()

    @staticmethod
    def _state(form: QFormLayout) -> QLabel:
        lab = QLabel("")
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", lab)
        return lab

    # -- valores ----------------------------------------------------------------------------
    def setup(self) -> pg.PenhanSetup:
        db, endf = _field_path(self.database), _field_path(self.endf)
        if endf is not None and db is not None and _same_dir(endf, db / "ENDF_data"):
            endf = None  # la de la base de datos: se guarda como «la de siempre» (sigue a la base de datos)
        return pg.PenhanSetup(db, _field_path(self.penhmat), _field_path(self.penhmain), self.rb_off.isChecked(),
                              endf)

    def _follows(self, edit: QLineEdit, key: str, prev: Path | None) -> bool:
        """¿El campo sigue a la carpeta de la que sale? (vacío, puesto por ARGUS o dentro de ``prev``, la carpeta de
        penhan o la base de datos de antes, si lo era de verdad)."""
        current = edit.text().strip()
        if not current or self._auto.get(key) == current:
            return True
        path = _field_path(edit)
        return prev is not None and path is not None and _inside(path, prev)

    def _set_auto(self, edit: QLineEdit, key: str, value: Path | None, prev: Path | None = None) -> None:
        """Pone lo encontrado en la carpeta de penhan si el campo la sigue (:meth:`_follows`)."""
        if value is not None and self._follows(edit, key, prev):
            edit.setText(str(value))
            self._auto[key] = str(value)

    def _on_root(self) -> None:
        root = _field_path(self.root)
        db = pg.database_folder(root) if root else None
        prev = self._root_prev if self._root_prev is not None and pg.database_folder(self._root_prev) else None
        if db is not None:
            self._set_auto(self.database, "db", db, prev)
            self._set_auto(self.penhmat, "penhmat", pg.find_penhmat(db), prev)
            self._set_auto(self.penhmain, "penhmain", pg.find_penhmain(db), prev)
            self._root_prev = root
        self._timer.start()

    def _on_database(self) -> None:
        """La carpeta de los ENDF sigue a la base de datos (su ENDF_data) si no se ha elegido otra; si la nueva aún no
        tiene ENDF_data, se vacía (revisión de 0.16: se quedaba en la de la base de datos vieja, y se guardaba)."""
        db = _field_path(self.database)
        if db is None or not _is_database(db):
            return
        prev = self._db_prev if self._db_prev is not None and _is_database(self._db_prev) else None
        self._db_prev = db
        if not self._follows(self.endf, "endf", prev):
            return
        new = db / "ENDF_data"
        value = str(new) if new.is_dir() else ""
        if self.endf.text().strip() != value:
            self.endf.setText(value)
        self._auto["endf"] = value

    # -- estado ------------------------------------------------------------------------------
    def _update(self) -> None:
        self._timer.stop()
        db = _field_path(self.database)
        probs = pg.database_problems(db) if db else ["elija la carpeta database de penhan"]
        if probs:
            self.db_state.setText(_colored("✖ " + probs[0], BAD_COLOR))
        else:
            n = len(mg.material_list(db))
            self.db_state.setText(_colored(f"✔ pdfiles, pdhan y ENDF_data; {n} materiales en la lista de PENELOPE"
                                           if n else "✔ pdfiles, pdhan y ENDF_data (sin material-list.txt)", OK_COLOR))
        endf = _field_path(self.endf)
        if endf is None and db is not None and (db / "ENDF_data").is_dir():
            self.endf_state.setText(_colored("vacío: los ENDF que falten se copian de los de la base de datos "
                                             "(database/ENDF_data)", GREY))
        elif endf is None:
            self.endf_state.setText(_colored("no se ha elegido: sin ella, los ENDF que falten no se pueden copiar",
                                             WAIT_COLOR))
        elif not endf.is_dir():
            self.endf_state.setText(_colored(f"✖ no existe la carpeta {endf}", BAD_COLOR))
        else:
            try:
                n = sum(1 for f in endf.iterdir() if f.is_file() and f.suffix.lower() in ("", ".dat", ".txt", ".endf")
                        and not f.name.lower().startswith(("readme", "isotopic")))
            except OSError:
                n = 0
            self.endf_state.setText(_colored(f"✔ {plural(n, 'fichero')} ENDF" if n else
                                             "⚠ no parece tener ficheros ENDF", OK_COLOR if n else WAIT_COLOR))
        off = self.rb_off.isChecked()
        self._exe_state(self.penhmat, self.penhmat_state, "penhmat", off)
        self._exe_state(self.penhmain, self.penhmain_state, "penhmain", False)
        self.btn_test.setEnabled(not off and _field_path(self.penhmat) is not None and not probs)

    def _exe_state(self, edit: QLineEdit, label: QLabel, what: str, off: bool) -> None:
        p = _field_path(edit)
        if p is None:
            label.setText(_colored("no se ha elegido" + (" (no hace falta)" if off else ""), GREY))
            return
        info = pg.exe_info(p)
        if info.system is None:
            label.setText(_colored(f"✖ {info.problem}", GREY if off else BAD_COLOR))
            return

        def show(launch: pg.Launch) -> None:
            if what == "penhmain":
                hint = pg.run_hint(info, "caso.in")
                ok = launch.ok
                text = f"{info.description}; se lanza {hint}" + ("" if ok else f" ({launch.note})")
                label.setText(_colored(("✔ " if ok else "⚠ ") + text, OK_COLOR if ok else WAIT_COLOR))
            else:
                label.setText(_colored(("✔ " if launch.ok else "✖ ") + launch.note,
                                       GREY if off else (OK_COLOR if launch.ok else BAD_COLOR)))

        if _needs_wsl_check(info):
            label.setText(_colored(f"{info.description}: comprobando WSL…", WAIT_COLOR))
            self._run(lambda _p, _c: pg.launch_for(info), lambda launch: _alive(label) and show(launch))
        else:
            show(pg.launch_for(info))

    def _run(self, fn, on_done, on_failed=None) -> _Task:
        task = _Task(fn, self)
        self._tasks.add(task)

        def finish(res):
            self._tasks.discard(task)
            on_done(res)

        def fail(msg):
            self._tasks.discard(task)
            if on_failed is not None:
                on_failed(msg)

        task.done.connect(finish)
        task.failed.connect(fail)
        task.start()
        return task

    def test_penhmat(self) -> None:
        setup = self.setup()
        pid = TEST_MATERIAL
        ids = [k for k, _n in mg.material_list(setup.database)]
        if ids and pid not in ids:
            pid = ids[0]
        self.btn_test.setEnabled(False)
        self.test_state.setText(_colored("Ejecutando penhmat…", WAIT_COLOR))

        def work(_progress, cancel):
            info = pg.exe_info(setup.penhmat)
            launch = pg.launch_for(info)
            if not launch.ok:
                return False, launch.note, 0.0
            t0 = time.monotonic()
            tmp, problem, _out = mg.run_penhmat(setup.penhmat, launch, setup.database, mg.list_answers(pid),
                                                cancel=cancel, timeout=600)
            dt = time.monotonic() - t0
            if tmp is None:
                return False, problem, dt
            from ..penhan.materials import read_mat_header, read_mat_nuclear

            head, nuc = read_mat_header(tmp), read_mat_nuclear(tmp)
            try:
                tmp.unlink()
            except OSError:
                pass
            n = nuc.nisot if nuc is not None and nuc.uses_endf else 0
            return True, f"{head.name if head else 'material'}, {plural(n, 'fichero')} ENDF", dt

        def done(res):
            ok, text, dt = res
            secs = f" ({dt:.1f} s)".replace(".", ",") if dt else ""
            self.test_state.setText(_colored(("✔ penhmat funciona: " if ok else "✖ ") + text + secs,
                                             OK_COLOR if ok else BAD_COLOR))
            self.btn_test.setEnabled(True)

        def failed(msg):
            self.test_state.setText(_colored(f"✖ {msg}", BAD_COLOR))
            self.btn_test.setEnabled(True)

        self._run(work, done, failed)

    def accept(self) -> None:
        save_penhan_setup(self.setup(), self.settings)
        super().accept()

    def reject(self) -> None:
        for t in list(self._tasks):
            t.cancel.set()
        super().reject()


def _alive(label: QLabel) -> bool:
    """¿Sigue existiendo la etiqueta? (la comprobación de WSL puede acabar después de cerrar el diálogo)."""
    import shiboken6

    return shiboken6.isValid(label)


# ---------------------------------------------------------------------------------------------------------------------
# Panel Materiales → «Generar .mat y ENDF…»
# ---------------------------------------------------------------------------------------------------------------------

STATUS_TEXT = {"done": "generado", "exists": "ya había otro con ese nombre: no se ha tocado", "failed": "ERROR",
               "cancelled": "cancelado"}
ENDF_TEXT = {"done": "copiado", "same": "ya estaba (igual)", "conflict": "ya hay OTRO con ese nombre: no se toca",
             "failed": "no se ha podido copiar", "missing": "NO ESTÁ en la carpeta de los ENDF"}
COLORS = {"done": OK_COLOR, "same": GREY, "exists": WAIT_COLOR, "conflict": WAIT_COLOR, "failed": BAD_COLOR,
          "missing": BAD_COLOR, "cancelled": GREY}


class CompleteMaterialsDialog(QDialog):
    """Genera con penhmat los .mat que faltan en la carpeta del .in y copia allí los ENDF que necesitan."""

    def __init__(self, parent, doc, geo, folder: str | Path, settings, copy_endf: bool = True, in_name: str = "",
                 assign=None):
        super().__init__(parent)
        self.setWindowTitle("Generar .mat y ENDF")
        self.assign = assign  # () -> bool: «Asignar materiales…» de la ventana (los que ARGUS no ha identificado)
        self.doc = doc
        self.geo = geo
        self.folder = Path(folder)
        self.settings = settings
        self.plan: mg.MatPlan | None = None
        self.finished_plan: mg.MatPlan | None = None
        self.setup: pg.PenhanSetup = penhan_setup(settings)
        self._launch: pg.Launch | None = None
        self._task: _Task | None = None
        self._check: _Task | None = None
        self._closing = False
        lay = QVBoxLayout(self)
        what = f" de <b>{html.escape(in_name)}</b>" if in_name else ""
        intro = QLabel(f"Los .mat{what} que faltan en <tt>{html.escape(str(self.folder))}</tt> se generan con su "
                       "penhmat (en su carpeta database, con las respuestas de materiales.txt) y los ficheros ENDF que "
                       "listan se copian desde la carpeta de los ENDF (Opciones → Programas de penhan): con protones o "
                       "alfas, penhmain los lee de la carpeta donde se ejecuta. No se sustituye ningún fichero.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        self.penhmat_label = QLabel("")
        self.penhmat_label.setWordWrap(True)
        self.penhmat_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.penhmat_label)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Fichero", "Qué es", "Estado"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        lay.addWidget(self.table, 1)
        self.copy_endf = QCheckBox("Copiar también a esta carpeta los ficheros ENDF que listan los .mat (hacen falta "
                                   "si penhmain se ejecuta aquí)")
        self.copy_endf.setChecked(copy_endf)
        self.copy_endf.toggled.connect(lambda _on: self._fill())
        lay.addWidget(self.copy_endf)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.buttons = QDialogButtonBox()
        self.btn_assign = self.buttons.addButton("Asignar materiales…", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_assign.setToolTip("Elegir de la lista de PENELOPE o de sus materiales personalizados los que ARGUS no "
                                   "sabe de qué están hechos")
        self.btn_assign.clicked.connect(self.assign_materials)
        self.btn_endf = self.buttons.addButton("Carpeta de los ENDF…", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_endf.setToolTip("Dónde están sus ficheros ENDF (si no, se usan los de database/ENDF_data)")
        self.btn_endf.clicked.connect(self.choose_endf_folder)
        self.btn_setup = self.buttons.addButton("Programas de penhan…", QDialogButtonBox.ButtonRole.ActionRole)
        self.go = self.buttons.addButton("Generar y copiar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_btn = self.buttons.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_setup.clicked.connect(self.configure)
        self.go.clicked.connect(self.run)
        self.close_btn.clicked.connect(self.reject)
        lay.addWidget(self.buttons)
        fit_to_screen(self, 900, 520)
        self.refresh()

    # -- plan ----------------------------------------------------------------------------------
    def refresh(self) -> None:
        from ..penhan.simfolder import find_database

        self.setup = penhan_setup(self.settings)
        self.database = self.setup.database or find_database(self.folder)  # para los ENDF, aunque no se haya dicho
        self.plan = mg.plan_materials(self.doc, self.geo, self.folder, self.database, endf_dir=self.setup.endf)
        self._launch = None
        problem = self.setup.penhmat_problem()
        needed = bool(self.plan.to_generate)
        if problem or not needed:
            self._launch = pg.Launch("", problem) if problem else None
            self._fill()
            return
        info = pg.exe_info(self.setup.penhmat)
        if _needs_wsl_check(info):
            self._launch = None
            self._fill(checking=True)
            task = _Task(lambda _p, _c: pg.launch_for(info), self)
            self._check = task
            task.done.connect(self._on_launch)
            task.start()
        else:
            self._launch = pg.launch_for(info)
            self._fill()

    def _on_launch(self, launch) -> None:
        self._check = None
        self._launch = launch
        self._fill()

    @property
    def can_generate(self) -> bool:
        return self._launch is not None and self._launch.ok and bool(self.plan and self.plan.to_generate)

    def _fill(self, checking: bool = False) -> None:
        plan = self.plan
        if plan is None:
            return
        rows = []
        if self.setup.penhmat is not None and not self.setup.penhmat_off:
            head = f"penhmat: {self.setup.penhmat}"
            if checking:
                self.penhmat_label.setText(_colored(head + " (comprobando cómo se ejecuta…)", WAIT_COLOR))
            elif self._launch is not None:
                ok = self._launch.ok
                self.penhmat_label.setText(_colored(("✔ " if ok else "✖ ") + f"{head}: {self._launch.note}",
                                                    OK_COLOR if ok else BAD_COLOR))
            else:
                self.penhmat_label.setText(_colored(head, GREY))
        else:
            self.penhmat_label.setText(_colored(self.setup.penhmat_problem() or "sin penhmat", GREY))
        for j in plan.mats:
            if j.status:
                state = STATUS_TEXT.get(j.status, j.status) + (f": {j.message}" if j.message else "")
                color = COLORS.get(j.status, GREY)
            elif not j.possible:
                state, color = f"no se puede generar: {j.reason}", BAD_COLOR
            elif checking:
                state, color = "se generará con penhmat (comprobando penhmat…)", WAIT_COLOR
            elif self.can_generate:
                state, color = "se generará con penhmat", "#2a7"
            else:
                why = self._launch.note if self._launch is not None else self.setup.penhmat_problem()
                state, color = f"no se puede generar: {why}", BAD_COLOR
            rows.append((j.fname, j.what or "material", state, color))
        for e in plan.endf:
            if e.status:
                state, color = ENDF_TEXT.get(e.status, e.status) + (f": {e.message}" if e.message else ""), \
                    COLORS.get(e.status, GREY)
            elif e.source is None:
                state, color = e.message or ENDF_TEXT["missing"], BAD_COLOR
            elif self.copy_endf.isChecked():
                state, color = f"se copiará desde {e.source.parent} ({human_size(e.size)})", "#2a7"
            else:
                state, color = "no se copia (casilla sin marcar)", GREY
            rows.append((e.name, "datos ENDF de " + ", ".join(e.materials), state, color))
        t = self.table
        t.setRowCount(len(rows))
        for i, (name, what, state, color) in enumerate(rows):
            for k, v in enumerate((name, what, state)):
                item = QTableWidgetItem(v)
                item.setToolTip(v)
                if k == 2:
                    item.setForeground(QColor(color))
                t.setItem(i, k, item)
        t.resizeColumnsToContents()
        self.copy_endf.setVisible(plan.hadrons is not False)
        busy = self._task is not None
        self.btn_assign.setVisible(self.assign is not None and any(j.unknown and not j.status for j in plan.mats))
        self.btn_assign.setEnabled(not busy)
        self.btn_endf.setVisible(plan.hadrons is not False and bool(plan.endf_missing))
        self.btn_endf.setEnabled(not busy)
        if self.finished_plan is not None:
            return
        n_gen = len(plan.to_generate) if self.can_generate else 0
        n_endf = len(plan.endf_to_copy) if self.copy_endf.isChecked() else 0
        msg = []
        if plan.empty:
            msg.append("No falta ningún .mat ni ningún fichero ENDF en la carpeta del .in.")
        if plan.hadrons is False:
            msg.append(no_endf_reason(self.doc) + " (no se copian).")
        elif plan.mats and not self.copy_endf.isChecked() and plan.to_generate:
            msg.append("Los ENDF de los .mat que se generen no se copiarán (casilla sin marcar).")
        copy = self.copy_endf.isChecked() and plan.hadrons is not False
        if n_gen:
            msg.append(f"Se generará{'n' if n_gen > 1 else ''} {n_gen} .mat"
                       + (" y se copiarán los ficheros ENDF que listen." if copy else "."))
        if n_endf:
            msg.append(f"Se {'copiará' if n_endf == 1 else 'copiarán'} {plural(n_endf, 'fichero')} ENDF de los .mat "
                       "que ya están "
                       f"({human_size(plan.endf_bytes)}).")
        self.status.setText(html.escape(" ".join(msg)))
        self.go.setEnabled(bool(n_gen or n_endf) and self._task is None and not checking)

    # -- acciones ------------------------------------------------------------------------------
    def assign_materials(self) -> None:
        """«Asignar materiales…»: la ventana lo hace (un paso de deshacer en el .in) y aquí se vuelve a planear."""
        if self.assign is None or self._task is not None:
            return
        if self.assign():
            self.finished_plan = None
            self.refresh()

    def choose_endf_folder(self) -> None:
        """«Carpeta de los ENDF…»: se guarda (como en Opciones → Programas de penhan) y se vuelve a planear."""
        start = self.setup.endf_folder() or self.setup.database or self.folder
        d = QFileDialog.getExistingDirectory(self, "Carpeta de los ficheros ENDF", str(start))
        if not d:
            return
        setup = penhan_setup(self.settings)
        setup.endf = Path(d)
        save_penhan_setup(setup, self.settings)
        self.finished_plan = None
        self.refresh()

    def configure(self) -> None:
        dlg = PenhanSetupDialog(self, self.settings, start=self.folder)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted and self._task is None:
                self.finished_plan = None
                self.refresh()
        finally:
            dlg.deleteLater()

    def run(self) -> None:
        plan, setup, launch = self.plan, self.setup, self._launch
        if plan is None or self._task is not None:
            return
        generate = self.can_generate
        copy = self.copy_endf.isChecked() and plan.hadrons is not False
        if not (generate and plan.to_generate) and not (copy and plan.endf_to_copy):
            return
        if not generate:  # sin penhmat: solo los ENDF de los .mat que ya están
            for j in plan.to_generate:
                j.reason = j.reason or (launch.note if launch is not None else setup.penhmat_problem())
                j.answers = None

        def work(progress, cancel):
            if generate:
                mg.generate(plan, setup.penhmat, launch, setup.database, progress=progress, cancel=cancel,
                            copy_endf=copy, doc=self.doc, endf_dir=setup.endf)
            elif copy:
                mg.copy_endf_files(plan, progress=progress, cancel=cancel)
            return plan

        task = _Task(work, self)
        self._task = task
        task.progress.connect(self._on_progress)
        task.done.connect(self._done)
        task.failed.connect(self._failed)
        self.go.setEnabled(False)
        self.btn_setup.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setText("Trabajando…")
        task.start()

    def _on_progress(self, a: int, b: int, text: str) -> None:
        if b > 0:
            self.progress.setRange(0, b)
            self.progress.setValue(a)
        if text:
            self.status.setText(html.escape(text + "…"))

    def _done(self, plan) -> None:
        self._task = None
        self.finished_plan = plan
        self.progress.setVisible(False)
        self._fill()
        lines = mg.summary(plan)
        bad = any(j.status == "failed" for j in plan.mats) or any(e.status in ("failed", "missing")
                                                                   for e in plan.endf)
        self.status.setText(_colored(" ".join(lines) or "Nada que hacer.", BAD_COLOR if bad else OK_COLOR))
        self.close_btn.setText("Cerrar")
        self.btn_setup.setEnabled(True)
        if self._closing:
            super().reject()

    def _failed(self, msg: str) -> None:
        self._task = None
        self.progress.setVisible(False)
        self.status.setText(_colored(f"Error inesperado: {msg}", BAD_COLOR))
        self.btn_setup.setEnabled(True)
        if self._closing:
            super().reject()

    def busy(self) -> bool:
        return self._task is not None

    def reject(self) -> None:
        """Cerrar: si está trabajando, se cancela (penhmat se detiene) y se cierra al terminar."""
        if self._task is not None:
            self._task.cancel.set()
            self._closing = True
            self.close_btn.setEnabled(False)
            self.status.setText("Cancelando…")
            return
        if self._check is not None:
            self._check.cancel.set()
        super().reject()


__all__ = ["CompleteMaterialsDialog", "PenhanSetupDialog"]
