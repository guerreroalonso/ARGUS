"""Diálogos «Simulaciones en paralelo» y «Campaña de simulaciones» (menú Resultados).

ARGUS prepara las carpetas (cada una con lo que necesita penhmain), el .in de
cada ejecución y las órdenes para lanzarlas todas; el usuario las lanza y la
ventana de resultados las sigue. Nunca se sobrescribe ni se borra nada.
"""

from __future__ import annotations

import html
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import fmt_count, plural
from ..pengeom.ct import human_size
from ..penhan import batch as bt
from ..penhan import sections as sc
from .screen_fit import fit_to_screen

ENERGY_UNITS = {"eV": 1.0, "keV": 1e3, "MeV": 1e6}


class _Writer(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, plan, jobs: int, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.jobs = jobs
        self.cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="argus-lote", daemon=True).start()

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    def _run(self) -> None:
        try:
            w = bt.write_batch(self.plan, self.jobs, lambda a, b, t: self._emit(self.progress, a, b, t),
                               cancel=self.cancel.is_set)
        except Exception as exc:  # que llegue a la ventana
            self._emit(self.failed, f"{exc.strerror or exc}" if isinstance(exc, OSError) else f"{exc}")
            return
        self._emit(self.done, w)


class ParamEditor(QGroupBox):
    """Un parámetro de la campaña: qué se cambia y qué valores toma."""

    changed = Signal()

    def __init__(self, title: str, doc, optional: bool = False, parent=None):
        super().__init__(title, parent)
        self.doc = doc
        self.setCheckable(optional)
        if optional:
            self.setChecked(False)
        f = QFormLayout(self)
        self.kind = QComboBox()
        has_senerg = sc.value(doc, "SENERG") is not None
        items = [("energy", "Energía de la fuente (SENERG)"), ("pos_z", "Posición de la fuente: z (SPOSIT)"),
                 ("pos_x", "Posición de la fuente: x (SPOSIT)"), ("pos_y", "Posición de la fuente: y (SPOSIT)"),
                 ("aperture", "Apertura del haz: α (SCONE)"), ("parinp", "Parámetro de la geometría (PARINP)"),
                 ("material", "Fichero de un material (MFNAME)"), ("keyword", "Otra palabra clave del .in")]
        for k, t in items:
            self.kind.addItem(t, k)
        if not has_senerg:
            i = self.kind.findData("energy")
            self.kind.model().item(i).setEnabled(False)
            self.kind.setItemData(i, "La fuente no es monoenergética (SENERG): su espectro se cambia en el .in",
                                  Qt.ItemDataRole.ToolTipRole)
            self.kind.setCurrentIndex(self.kind.findData("pos_z"))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        f.addRow("Qué cambia:", self.kind)
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("p. ej. SBOX")
        self.keyword.setMaxLength(6)
        self.index = QSpinBox()
        self.index.setRange(1, 20)
        self.number = QSpinBox()
        self.number.setRange(1, 50)
        self.kw_row = QWidget()
        kl = QHBoxLayout(self.kw_row)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(QLabel("palabra"))
        kl.addWidget(self.keyword)
        kl.addWidget(QLabel("valor n.º"))
        kl.addWidget(self.index)
        kl.addStretch(1)
        f.addRow(self.kw_row)
        self.num_label = QLabel("Número:")
        f.addRow(self.num_label, self.number)
        vrow = QHBoxLayout()
        self.values = QLineEdit()
        self.values.setToolTip("Valores separados por espacios; «A:B:C» de A a B de C en C; «A-B/N» N valores de A a B")
        vrow.addWidget(self.values, 1)
        self.unit = QComboBox()
        for u in ENERGY_UNITS:
            self.unit.addItem(u, u)
        self.unit.setCurrentIndex(2)
        vrow.addWidget(self.unit)
        self.unit_text = QLabel("")
        vrow.addWidget(self.unit_text)
        f.addRow("Valores:", vrow)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #666;")
        f.addRow(self.info)
        for w in (self.values, self.keyword):
            w.textChanged.connect(lambda _t: self.changed.emit())
        for w in (self.index, self.number):
            w.valueChanged.connect(lambda _v: self._kind_changed(keep=True))
        self.unit.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.toggled.connect(lambda _on: self.changed.emit())
        self._kind_changed()

    def _kind_changed(self, *_args, keep: bool = False) -> None:
        k = self.kind.currentData()
        doc = self.doc
        self.kw_row.setVisible(k == "keyword")
        self.number.setVisible(k in ("parinp", "material"))
        self.num_label.setVisible(k in ("parinp", "material"))
        self.unit.setVisible(k == "energy")
        self.unit_text.setText({"pos_x": "cm", "pos_y": "cm", "pos_z": "cm", "aperture": "grados"}.get(k, ""))
        now = ""
        if k == "energy":
            v = sc.value(doc, "SENERG")
            now = f"ahora {v[0] / 1e6:g} MeV" if v else ""
            if not keep and v:
                self.values.setText(f"{v[0] / 1e6:g}")
        elif k in ("pos_x", "pos_y", "pos_z"):
            v = sc.value(doc, "SPOSIT") or [0.0, 0.0, 0.0]
            i = {"pos_x": 0, "pos_y": 1, "pos_z": 2}[k]
            now = f"ahora {v[i]:g} cm"
            if not keep:
                self.values.setText(f"{v[i]:g}")
        elif k == "aperture":
            v = sc.value(doc, "SCONE")
            now = f"ahora {v[2]:g}°" if v else "la fuente no usa SCONE"
        elif k == "parinp":
            pv = sc.parinp(doc)
            n = self.number.value()
            now = f"PARINP {n} vale ahora {pv[n]:g}" if n in pv else f"el .in no tiene PARINP {n}"
        elif k == "material":
            mats = [m.fname for m in doc.model.materials]
            n = self.number.value()
            now = (f"el material {n} es ahora {mats[n - 1]}; escriba los .mat separados por espacios"
                   if 1 <= n <= len(mats) else f"el .in tiene {plural(len(mats), 'material', 'materiales')}")
        elif k == "keyword":
            kw = self.keyword.text().strip().upper()
            v = sc.value(doc, kw) if kw else None
            now = f"{kw}: {' '.join(f'{x:g}' if isinstance(x, float) else str(x) for x in v)}" if v else \
                ("" if not kw else f"el .in no tiene {kw}: se usa el valor por defecto de penhmain")
        self.info.setText(now)
        self.changed.emit()

    def param(self) -> bt.Param | None:
        """El parámetro (None si no está activo); ValueError si los valores no valen."""
        if self.isCheckable() and not self.isChecked():
            return None
        k = self.kind.currentData()
        text = self.values.text().strip()
        if not text:
            raise ValueError("Escriba los valores")
        if k == "material":
            vals = text.replace(",", " ").replace(";", " ").split()
            return bt.Param("material", vals, number=self.number.value())
        vals = bt.parse_values(text)
        if k == "energy":
            f = ENERGY_UNITS[self.unit.currentData()]
            p = bt.Param("keyword", [v * f for v in vals], keyword="SENERG", index=0, unit="eV")
            return p
        if k in ("pos_x", "pos_y", "pos_z"):
            return bt.Param("keyword", vals, keyword="SPOSIT", index={"pos_x": 0, "pos_y": 1, "pos_z": 2}[k],
                            unit="cm")
        if k == "aperture":
            return bt.Param("keyword", vals, keyword="SCONE", index=2, unit="grados")
        if k == "parinp":
            return bt.Param("parinp", vals, number=self.number.value())
        kw = self.keyword.text().strip().upper()
        if not kw:
            raise ValueError("Escriba la palabra clave")
        return bt.Param("keyword", vals, keyword=kw, index=self.index.value() - 1)


class BatchDialog(QDialog):
    """Prepara simulaciones en paralelo (``mode='paralelo'``) o una campaña (``mode='campana'``)."""

    openResults = Signal(object)  # carpeta preparada, para seguirla en la ventana de resultados

    def __init__(self, parent, in_path: Path, doc, settings, mode: str = "paralelo"):
        super().__init__(parent)
        self.mode = mode
        self.in_path = Path(in_path)
        self.doc = doc
        self.settings = settings
        self.plan: bt.BatchPlan | None = None
        self._writer: _Writer | None = None
        self._closing = False
        self.written: bt.Written | None = None
        campaign = mode == "campana"
        self.setWindowTitle("ARGUS · " + ("Campaña de simulaciones" if campaign else "Simulaciones en paralelo"))
        lay = QVBoxLayout(self)
        name = html.escape(self.in_path.name)
        if campaign:
            text = (f"Variantes de <b>{name}</b> cambiando uno o dos parámetros, cada una en su carpeta (y, si quiere, "
                    "con varias ejecuciones en paralelo). ARGUS prepara las carpetas y las órdenes para lanzarlas; "
                    "después compara las variantes y dibuja una medida frente al parámetro.")
        else:
            text = (f"El mismo <b>{name}</b> en varias carpetas con semillas independientes (como MultiRuns de "
                    "penhan), para usar varios procesadores. ARGUS prepara las carpetas y las órdenes para lanzarlas; "
                    "mientras simulan, la ventana de resultados las combina (como penhmain-sum).")
        intro = QLabel(text)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        form = QFormLayout()
        lay.addLayout(form)
        cpu = os.cpu_count() or 4
        nsim = sc.value(doc, "NSIMSH")
        nsim = float(nsim[0]) if nsim else float(sc.default("NSIMSH")[0])  # sin NSIMSH, las de penhmain (2e9)
        if campaign:
            self.p1 = ParamEditor("Parámetro", doc)
            self.p2 = ParamEditor("Segundo parámetro (opcional)", doc, optional=True)
            lay.insertWidget(1, self.p1)
            lay.insertWidget(2, self.p2)
            self.p1.changed.connect(self._preview)
            self.p2.changed.connect(self._preview)
            self.replicas = QSpinBox()
            self.replicas.setRange(1, 64)
            self.replicas.setValue(1)
            self.replicas.setToolTip("Ejecuciones en paralelo de cada variante (con semillas distintas)")
            self.replicas.valueChanged.connect(self._preview)
            form.addRow("Ejecuciones por variante:", self.replicas)
            self.showers = QDoubleSpinBox()
            self.showers.setDecimals(0)
            self.showers.setRange(1, 1e15)
            self.showers.setValue(nsim)
            form.addRow("Lluvias por variante:", self.showers)
            self.same_seeds = QCheckBox("Las mismas semillas en todas las variantes (diferencias con menos ruido)")
            self.same_seeds.toggled.connect(self._preview)
            form.addRow(self.same_seeds)
        else:
            self.nruns = QSpinBox()
            self.nruns.setRange(2, 1000)
            self.nruns.setValue(max(2, min(cpu - 1 if cpu > 2 else cpu, 16)))
            self.nruns.valueChanged.connect(self._preview)
            form.addRow("Ejecuciones:", self.nruns)
            srow = QHBoxLayout()
            self.shower_mode = QComboBox()
            self.shower_mode.addItem("repartir entre todas", "total")
            self.shower_mode.addItem("cada una simula", "each")
            self.shower_mode.currentIndexChanged.connect(self._preview)
            srow.addWidget(self.shower_mode)
            self.showers = QDoubleSpinBox()
            self.showers.setDecimals(0)
            self.showers.setRange(1, 1e15)
            self.showers.setValue(nsim)
            self.showers.valueChanged.connect(self._preview)
            srow.addWidget(self.showers)
            srow.addWidget(QLabel("partículas (NSIMSH)"))
            srow.addStretch(1)
            form.addRow("Partículas:", srow)
        self.seed = QSpinBox()
        self.seed.setRange(1, bt.MAX_SEED_PAIR)
        self.seed.setToolTip("Las ejecuciones usan los pares de semillas precalculados de penhmain (RSEED -n 0) a "
                             "partir de este")
        self.seed.valueChanged.connect(self._preview)
        form.addRow("Primer par de semillas:", self.seed)
        self.dumpp = QSpinBox()
        self.dumpp.setRange(30, 86400)
        self.dumpp.setValue(60)
        self.dumpp.setSuffix(" s")
        self.dumpp.setToolTip("DUMPP: cada cuánto escribe penhmain los resultados y el volcado (para seguirlas y poder "
                              "reanudarlas)")
        form.addRow("Resultados cada:", self.dumpp)
        self.jobs = QSpinBox()
        self.jobs.setRange(1, 1024)
        self.jobs.setValue(max(1, cpu - 1 if cpu > 2 else cpu))
        self.jobs.setToolTip("Cuántas lanzan a la vez las órdenes lanzar.bat y lanzar.sh (se puede cambiar al lanzar)")
        form.addRow("A la vez:", self.jobs)
        self.dest = self._path_row(form, "Carpeta:", str(bt.default_batch_folder(self.in_path, mode)), "dir")
        self.exe = self._path_row(form, "penhmain:", str(settings.value("sim/penhmain", "") or ""), "file")
        db = str(settings.value("sim/database", "") or "")
        self.database = self._path_row(form, "Base de datos de penhan:", db, "dir")
        for edit in (self.dest, self.exe, self.database):  # otra carpeta (también tras preparar una), otro penhmain…
            edit.textChanged.connect(self._preview)
        self.preview = QListWidget()
        self.preview.setMaximumHeight(140)
        lay.addWidget(self.preview)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Preparar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.clicked.connect(self.prepare)
        self.follow = bb.addButton("Seguirlas en Resultados", QDialogButtonBox.ButtonRole.ActionRole)
        self.follow.setVisible(False)
        self.follow.clicked.connect(lambda: self.openResults.emit(self.plan.dest))
        self.open_btn = bb.addButton("Abrir la carpeta", QDialogButtonBox.ButtonRole.ActionRole)
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.plan.dest))))
        self.close_btn = bb.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.close_btn.clicked.connect(self.reject)
        lay.addWidget(bb)
        fit_to_screen(self, 760, 760 if campaign else 560)
        self._preview()

    def _path_row(self, form, label: str, value: str, kind: str) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(value)
        b = QPushButton("Examinar…")

        def browse():
            start = str(self._path(edit) or self.in_path.parent)
            d = QFileDialog.getExistingDirectory(self, label, start) if kind == "dir" else \
                QFileDialog.getOpenFileName(self, label, start)[0]
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

    def _path(self, edit: QLineEdit) -> Path | None:
        text = edit.text().strip().strip('"')
        if not text:
            return None
        p = Path(text).expanduser()
        return p if p.is_absolute() else self.in_path.parent / p

    # -- el plan -------------------------------------------------------------------------------------------------
    def build(self) -> bt.BatchPlan:
        dest = self._path(self.dest)
        if dest is None:
            raise ValueError("Elija la carpeta")
        try:
            used = dest.exists() and (not dest.is_dir() or any(dest.iterdir()))
        except OSError as exc:
            raise ValueError(f"No se puede usar {dest}: {exc.strerror or exc}") from exc
        if used:
            raise ValueError(f"{dest.name} ya existe y no es una carpeta vacía: elija otra (nunca se sobrescribe)")
        if self.mode == "campana":
            params = [p for p in (self.p1.param(), self.p2.param()) if p is not None]
            return bt.make_campaign(self.doc, self.in_path, dest, params, replicas=self.replicas.value(),
                                    showers=self.showers.value(), same_seeds=self.same_seeds.isChecked(),
                                    first_seed=self.seed.value(), dumpp=float(self.dumpp.value()))
        each = self.shower_mode.currentData() == "each"
        return bt.make_parallel(self.doc, self.in_path, dest, self.nruns.value(),
                                total_showers=None if each else self.showers.value(),
                                each_showers=self.showers.value() if each else None, first_seed=self.seed.value(),
                                dumpp=float(self.dumpp.value()))

    def _preview(self, *_args) -> None:
        if self._writer is not None:
            return
        self.preview.clear()
        try:
            plan = self.build()
        except ValueError as exc:
            self.status.setText(f"<span style='color:#b42318'>{html.escape(str(exc))}</span>")
            self.go.setEnabled(False)
            return
        for v in plan.variants:
            for r in v.runs:
                self.preview.addItem(f"{r.folder}   ·   RSEED -{r.seed} 0" + (f"   ·   {v.label}" if v.label else ""))
        n = len(plan.runs)
        per = (f"{fmt_count(plan.showers)} partículas cada una" if plan.showers else
               "las partículas del .in cada una")
        text = f"{plural(n, 'ejecución', 'ejecuciones')} ({per})"
        if self.mode == "campana":
            text = f"{plural(len(plan.variants), 'variante')}, " + text
        warn = []
        exe = self._path(self.exe)
        if exe is None:
            warn.append("Sin penhmain: habrá que copiarlo en cada carpeta antes de lanzarlas.")
        elif not exe.is_file():
            warn.append(f"No se encuentra {exe}: las carpetas se prepararían sin penhmain.")
        db = self._path(self.database)
        if db is not None and not db.is_dir():
            warn.append(f"No se encuentra la carpeta {db}.")
        self.status.setText(html.escape(text + ".") + "".join(
            f"<br><span style='color:#9a6700'>{html.escape(w)}</span>" for w in warn))
        self.go.setEnabled(True)

    def prepare(self) -> None:
        if self._writer is not None:
            return
        try:
            plan = self.build()
            # Lo que penhmain no aceptaría de alguna variante (un PARINP fuera de rango, SPECTR con SENERG…): no se
            # prepara nada.
            errors = bt.check_variants(plan)
            if errors:
                self.status.setText("<span style='color:#b42318'>penhmain no aceptaría estas ejecuciones:<br>"
                                    + "<br>".join(html.escape(e) for e in errors[:8])
                                    + (" …" if len(errors) > 8 else "") + "</span>")
                return
            exe = self._path(self.exe)
            from .settings import penhan_setup

            bt.plan_files(plan, database=self._path(self.database), penhmain=exe if exe and exe.is_file() else None,
                          endf_dir=penhan_setup(self.settings).endf)
        except (ValueError, OSError) as exc:
            self.status.setText(f"<span style='color:#b42318'>{html.escape(str(exc))}</span>")
            return
        self.plan = plan
        exe, db = self._path(self.exe), self._path(self.database)
        if exe is not None and exe.is_file():  # las de «Programas de penhan»: solo si existen
            self.settings.setValue("sim/penhmain", str(exe))
        if db is not None and db.is_dir():
            self.settings.setValue("sim/database", str(db))
        self.go.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(plan.runs))
        self.status.setText(html.escape(f"Preparando {len(plan.runs)} carpetas "
                                        f"({human_size(plan.copy_bytes)} que copiar)…"))
        w = _Writer(plan, self.jobs.value(), self)
        self._writer = w
        w.progress.connect(lambda a, _b, _t: self.progress.setValue(a))
        w.done.connect(self._done)
        w.failed.connect(self._failed)
        w.start()

    def busy(self) -> bool:
        return self._writer is not None

    def reject(self) -> None:
        if self._writer is not None:
            self._writer.cancel.set()
            self._closing = True
            self.close_btn.setEnabled(False)
            return
        super().reject()

    def _done(self, w: bt.Written) -> None:
        self._writer = None
        self.written = w
        self.progress.setVisible(False)
        plan = self.plan
        msgs = []
        if w.cancelled:
            msgs.append("Cancelado: lo que ya se había copiado se queda en la carpeta.")
        else:
            exe = plan.exe_system
            how = ("en Windows, doble clic en <b>lanzar.bat</b>" if exe == "windows" else
                   "en Windows, doble clic en <b>lanzar.bat</b> (usa WSL); en Linux o macOS, <b>sh lanzar.sh</b>"
                   if plan.with_wsl else "<b>sh lanzar.sh</b> (Linux, macOS o WSL) o <b>lanzar.bat</b> (Windows)")
            one = len(plan.runs) == 1
            msgs.append(f"{'Preparada' if one else 'Preparadas'} {plural(len(plan.runs), 'carpeta')} en "
                        f"{html.escape(str(plan.dest))}. Para {'lanzarla' if one else 'lanzarlas'}: {how}. ARGUS no "
                        f"{'la' if one else 'las'} lanza: cuando {'empiece' if one else 'empiecen'}, «Seguirlas en "
                        "Resultados».")
            if plan.exe_system is None:
                msgs.append("<span style='color:#9a6700'>Sin penhmain: cópielo en cada carpeta o elíjalo arriba y "
                            "vuelva a preparar en otra carpeta.</span>")
            if any(len(v.runs) > 1 for v in plan.variants):
                msgs.append("Para combinar las ejecuciones con penhmain-sum (no hace falta para verlas en ARGUS): en "
                            "Windows, <b>combinar.bat</b> con la ruta de penhmain-sum (o arrastrándolo encima); en "
                            "Linux, macOS o WSL, <b>sh combinar.sh</b> /ruta/de/penhmain-sum.")
        probs = plan.problems
        if probs:
            msgs.append("<span style='color:#b42318'>Faltan ficheros: " + html.escape("; ".join(probs[:5]))
                        + (" …" if len(probs) > 5 else "") + "</span>")
        if w.failed:
            msgs.append("<span style='color:#b42318'>No se ha podido escribir: " + html.escape("; ".join(w.failed[:5]))
                        + "</span>")
        msgs += [html.escape(n) for n in plan.notes]
        self.status.setText("<br>".join(msgs))
        self.follow.setVisible(not w.cancelled)
        self.open_btn.setVisible(True)
        self.close_btn.setText("Cerrar")
        self.close_btn.setEnabled(True)
        if self._closing:
            super().reject()

    def _failed(self, msg: str) -> None:
        self._writer = None
        self.progress.setVisible(False)
        self.close_btn.setEnabled(True)
        self.status.setText(f"<span style='color:#b42318'>No se ha podido preparar: {html.escape(msg)}</span>")
        self.go.setEnabled(True)
        if self._closing:
            super().reject()


__all__ = ["BatchDialog", "ParamEditor"]
