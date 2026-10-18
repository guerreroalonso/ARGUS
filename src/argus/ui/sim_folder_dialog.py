"""Diálogo «Preparar la carpeta de simulación»: copia todo lo que necesita penhmain para el .in abierto."""

from __future__ import annotations

import html
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import plural
from ..pengeom.ct import human_size
from ..penhan import simfolder as sf
from .screen_fit import fit_to_screen

COLORS = {"copy": "#2a7", "copied": "#2a7", "same": "#888", "conflict": "#d80", "missing": "#d33", "outside": "#d33",
          "failed": "#d33", "absolute": "#27a", "blank": "#d33", "winpath": "#d33"}


class _Copier(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="argus-sim", daemon=True).start()

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:  # el diálogo (y este objeto) ya se han borrado: no hay a quién avisar
            pass

    def _run(self) -> None:
        try:
            out = sf.execute(self.plan, lambda a, b, t: self._emit(self.progress, a, b, t), cancel=self.cancel.is_set)
        except Exception as exc:  # cualquier fallo tiene que llegar a la ventana
            self._emit(self.failed,
                       f"{exc.strerror or exc}" if isinstance(exc, OSError) else f"{type(exc).__name__}: {exc}")
            return
        self._emit(self.done, out)


class SimFolderDialog(QDialog):
    def __init__(self, parent, in_path: Path, doc, settings, geo=None):
        super().__init__(parent)
        self.setWindowTitle("Preparar la carpeta de simulación")
        self.in_path = Path(in_path)
        self.doc = doc
        self.geo = geo  # para los tejidos de un TAC (su ficha): «Generar los .mat que faltan»
        self.settings = settings
        self.plan: sf.SimPlan | None = None
        self._copier: _Copier | None = None
        self._closing = False
        self.copied: list[Path] | None = None
        lay = QVBoxLayout(self)
        name = html.escape(self.in_path.name)
        intro = QLabel(f"Se copia a una carpeta todo lo que necesita penhmain para <b>{name}</b>: el propio .in, la "
                       "geometría con sus INCLUDE y su .ct, los .mat, los ficheros ENDF que listan (con protones o "
                       "alfas), los de una fuente radiactiva o de espacio de fases y el ejecutable. Nunca se "
                       "sobrescribe ni se borra nada.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        self.hint = QLabel("")  # cómo se lanza penhmain (según sea de Windows, Linux o macOS)
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.hint)
        form = QFormLayout()
        lay.addLayout(form)
        self.dest = self._path_row(form, "Carpeta de destino:", str(sf.default_destination(self.in_path)), "dir",
                                   "Carpeta de destino")
        saved_db = str(settings.value("sim/database", "") or "")
        db = saved_db if saved_db and Path(saved_db).is_dir() else str(sf.find_database(self.in_path.parent) or "")
        self.database = self._path_row(form, "Base de datos de penhan:", db, "dir",
                                       "Carpeta database de penhan (ENDF_data, nucleide-files, pdfiles)")
        self.extra = self._path_row(form, "Más .mat en (opcional):", settings.value("sim/mat_dir", ""), "dir",
                                    "Carpeta donde buscar los .mat que no estén junto al .in")
        self.exe = self._path_row(form, "Ejecutable de penhmain:", settings.value("sim/penhmain", ""), "file",
                                  "Ejecutable de penhmain (penhmain.x o penhmain.exe)")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Fichero", "Qué es", "Estado", "Desde"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.buttons = QDialogButtonBox()
        self.gen = self.buttons.addButton("Generar los .mat que faltan…", QDialogButtonBox.ButtonRole.ActionRole)
        self.gen.setToolTip("Genera con su penhmat los .mat que faltan junto al .in (tejidos de un TAC y materiales de "
                            "la lista de PENELOPE); después se copian aquí con sus ENDF")
        self.gen.setVisible(False)
        self.gen.clicked.connect(self.generate_materials)
        self.go = self.buttons.addButton("Preparar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.open_btn = self.buttons.addButton("Abrir la carpeta", QDialogButtonBox.ButtonRole.ActionRole)
        self.open_btn.setVisible(False)
        self.close_btn = self.buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.go.clicked.connect(self.prepare)
        self.open_btn.clicked.connect(self.open_folder)
        self.close_btn.clicked.connect(self.reject)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        fit_to_screen(self, 980, 600)
        self.refresh()

    def _path_row(self, form, label: str, value: str, kind: str, title: str) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(str(value or ""))
        edit.textChanged.connect(lambda _t: self._timer.start())
        b = QPushButton("Examinar…")

        def browse():
            start = str(self._path(edit) or self.in_path.parent)
            if kind == "dir":
                d = QFileDialog.getExistingDirectory(self, title, start)
            else:
                d, _ = QFileDialog.getOpenFileName(self, title, start)
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
        """Ruta de un campo; una relativa es relativa a la carpeta del .in (no a la de ARGUS)."""
        text = edit.text().strip().strip('"')
        if not text:
            return None
        p = Path(text).expanduser()
        return p if p.is_absolute() else self.in_path.parent / p

    def refresh(self) -> None:
        if self._copier is not None:
            return
        self._timer.stop()
        dest = self._path(self.dest)
        if dest is None:
            self.plan = None
            self.table.setRowCount(0)
            self.status.setText("Elija la carpeta de destino.")
            self.go.setEnabled(False)
            return
        extra = self._path(self.extra)
        try:
            from .settings import penhan_setup

            self.plan = sf.plan_folder(self.in_path, self.doc, dest, database=self._path(self.database),
                                       extra_dirs=[extra] if extra else [], penhmain=self._path(self.exe),
                                       endf_dir=penhan_setup(self.settings).endf)
        except OSError as exc:
            self.plan = None
            self.table.setRowCount(0)
            self.status.setText(f"No se puede preparar la lista de ficheros: {exc}")
            self.go.setEnabled(False)
            return
        self._fill()

    def generate_materials(self) -> None:
        """Los .mat que faltan, con penhmat, en la carpeta del .in (de allí se copian aquí)."""
        from .penhan_dialogs import CompleteMaterialsDialog

        if self._copier is not None:
            return
        dlg = CompleteMaterialsDialog(self, self.doc, self.geo, self.in_path.parent, self.settings, copy_endf=False,
                                      in_name=self.in_path.name)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()
        self.refresh()

    def _update_hint(self) -> None:
        from ..penhan.programs import exe_info, run_hint

        exe = self._path(self.exe)
        info = exe_info(exe) if exe is not None and exe.is_file() else None
        dest = self._path(self.dest)
        self.hint.setText("Después, en la carpeta de simulación, se lanza penhmain con el .in como entrada, "
                          + run_hint(info, self.in_path.name, dest) + ".")

    def _fill(self) -> None:
        plan = self.plan
        self._update_hint()
        missing_mats = [i for i in plan.missing if i.kind == "mat"]
        self.gen.setVisible(bool(missing_mats))
        t = self.table
        t.setRowCount(len(plan.items))
        for i, it in enumerate(plan.items):
            state = it.status_text + ("" if it.required or it.status != "missing" else " (opcional)")
            if it.note:
                state += f" — {it.note}"
            vals = [it.name, it.kind_text, state, str(it.source.parent) if it.source is not None else ""]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j == 2:
                    item.setForeground(QColor(COLORS.get(it.status, "#888")))
                t.setItem(i, j, item)
        t.resizeColumnsToContents()
        msg = [f"{plural(len(plan.to_copy), 'fichero')} para copiar ({human_size(plan.copy_bytes)})."]
        if plan.missing:
            msg.append(f"{'Falta' if len(plan.missing) == 1 else 'Faltan'} {len(plan.missing)}: "
                       + ", ".join(i.name for i in plan.missing[:6])
                       + (" …" if len(plan.missing) > 6 else "")
                       + f". Se puede preparar igualmente y {'añadirlo' if len(plan.missing) == 1 else 'añadirlos'} "
                       "después" + (" (los .mat, con «Generar los .mat que faltan…»)." if missing_mats else "."))
        if plan.outside:
            one = len(plan.outside) == 1
            msg.append(f"{plural(len(plan.outside), 'fichero')} con una ruta que sube de carpeta: no se "
                       f"{'copia' if one else 'copian'} y penhmain no {'lo' if one else 'los'} encontraría desde la "
                       "carpeta nueva (" + ", ".join(i.name for i in plan.outside[:3])
                       + f"). {'Póngale' if one else 'Póngales'} en el .in un nombre sin carpeta.")
        for it in plan.blank:  # penhmain buscaría el nombre con el blanco delante: no se copia, hay que corregir el .in
            msg.append(it.lead_problem[:1].upper() + it.lead_problem[1:] + ".")
        if plan.conflicts:
            one = len(plan.conflicts) == 1
            msg.append(f"{plural(len(plan.conflicts), 'fichero')} no se {'copiará' if one else 'copiarán'}: en la "
                       "carpeta ya hay otro fichero con ese nombre.")
        if not self.exe.text().strip():
            msg.append("Sin ejecutable de penhmain: elíjalo para que se copie también.")
        msg += plan.notes
        self.status.setText(" ".join(msg))
        self.go.setEnabled(bool(plan.to_copy))

    def prepare(self) -> None:
        if self._copier is not None:
            return
        self.refresh()  # con lo que haya en los campos ahora (el temporizador puede no haber saltado)
        if self.plan is None or not self.plan.to_copy:
            return
        # Las rutas que se usan, para la próxima vez; las claves son las de «Programas de penhan», así que una vacía o
        # que no existe ahora (una unidad sin conectar) no sustituye a la que había.
        for key, edit, ok in (("sim/database", self.database, Path.is_dir), ("sim/mat_dir", self.extra, Path.is_dir),
                              ("sim/penhmain", self.exe, Path.is_file)):
            text = edit.text().strip().strip('"')
            if text and ok(Path(text).expanduser()):
                self.settings.setValue(key, text)
        self.go.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self.plan.to_copy))
        self.progress.setValue(0)
        c = _Copier(self.plan, self)
        self._copier = c
        c.progress.connect(lambda a, b, _t: self.progress.setValue(a))
        c.done.connect(self._done)
        c.failed.connect(self._failed)
        c.start()

    def busy(self) -> bool:
        return self._copier is not None

    def reject(self) -> None:
        """Cancelar/cerrar: si está copiando, para (se borra la copia a medias) y cierra al terminar."""
        if self._copier is not None:
            self._copier.cancel.set()
            self._closing = True
            self.close_btn.setEnabled(False)
            self.status.setText("Cancelando: se deja de copiar y se borra la copia a medias…")
            return
        super().reject()

    def _done(self, out) -> None:
        self._copier = None
        self.copied = out
        self.progress.setVisible(False)
        self._fill()
        self.status.setText(sf.summary(self.plan).replace("\n", " · "))
        retry = bool(self.plan.failed or self.plan.cancelled)  # se puede volver a intentar lo que falta
        self.go.setVisible(retry)
        self.go.setEnabled(retry)
        self.open_btn.setVisible(True)
        self.close_btn.setText("Cerrar")
        self.close_btn.setEnabled(True)
        if self._closing:
            super().reject()

    def _failed(self, msg: str) -> None:
        self._copier = None
        self.progress.setVisible(False)
        self.close_btn.setEnabled(True)
        self.status.setText(f"No se ha podido copiar: {msg}")
        self.go.setEnabled(True)
        if self._closing:
            super().reject()

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.plan.dest)))


__all__ = ["SimFolderDialog"]
