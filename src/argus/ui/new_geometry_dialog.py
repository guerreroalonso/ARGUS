"""Diálogo «Nueva geometría»: un .geo desde cero, vacío o con un primer cuerpo o módulo."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..pengeom.edit import EditError, Primitive
from ..pengeom.newgeo import DEFAULT_TITLE
from ..penhan.quickscan import looks_like_input
from .primitive_form import PrimitiveForm

GEO_FILTER = "Geometrías PENGEOM (*.geo);;Todos los ficheros (*)"
MAX_GEOMFN = 20


class NewGeometryDialog(QDialog):
    def __init__(self, parent, path: Path, start: str = "body"):
        super().__init__(parent)
        self.setWindowTitle("Nueva geometría")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)
        self.title_edit = QLineEdit(DEFAULT_TITLE)
        self.title_edit.setToolTip("Primera línea del .geo (PENGEOM no la lee; es para usted)")
        form.addRow("Título:", self.title_edit)
        row = QHBoxLayout()
        self._base = Path(path).parent  # carpeta de los nombres relativos
        self.path_edit = QLineEdit(str(path))
        self.path_edit.textChanged.connect(lambda _t: self._check())
        browse = QPushButton("Examinar…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        w = QWidget()
        row.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row)
        form.addRow("Fichero:", w)
        self.start = QComboBox()
        self.start.addItem("Un cuerpo", "body")
        self.start.addItem("Un módulo (para poner cuerpos dentro)", "module")
        self.start.addItem("Nada (geometría vacía)", "empty")
        self.start.setCurrentIndex(max(0, self.start.findData(start)))
        self.start.currentIndexChanged.connect(lambda _i: self._on_start())
        form.addRow("Empezar con:", self.start)
        self.shape = PrimitiveForm()
        self.shape.set_primitive(Primitive("box", center=(0.0, 0.0, 15.0), sides=(30.0, 30.0, 30.0), radius=15.0,
                                           height=30.0))
        self.shape.changed.connect(self._check)
        form.addRow(self.shape)
        self.material = QSpinBox()
        self.material.setRange(0, 9998)  # 9999 es la marca de la caja CT
        self.material.setValue(1)
        self.material.setToolTip("Número de material (0 = vacío); el fichero .mat de cada número se elige en el .in")
        form.addRow("Material:", self.material)
        self.name = QLineEdit()
        self.name.setPlaceholderText("opcional (p. ej. maniquí de agua)")
        form.addRow("Nombre:", self.name)
        self.make_in = QCheckBox("Crear también un .in para esta geometría (fuente, materiales...)")
        self.make_in.setChecked(True)
        self.make_in.toggled.connect(lambda _on: self._check())
        lay.addWidget(self.make_in)
        note = QLabel("PENGEOM trabaja en centímetros. La geometría se guarda al crearla; después se le añaden "
                      "cuerpos y módulos con clic derecho en el corte o en la vista 3D.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")
        lay.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Crear")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._on_start()

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Nueva geometría", self.path_edit.text(), GEO_FILTER)
        if path:
            if not Path(path).suffix:
                path += ".geo"
            self.path_edit.setText(path)

    def _on_start(self) -> None:
        empty = self.start.currentData() == "empty"
        self.shape.setVisible(not empty)
        self.material.setEnabled(not empty)
        self.name.setEnabled(not empty)
        self._check()

    # -- resultado -----------------------------------------------------------------
    def path(self) -> Path:
        """Ruta elegida; un nombre sin carpeta va en la carpeta propuesta (no en la de ARGUS)."""
        p = Path(self.path_edit.text().strip().strip('"')).expanduser()
        if not p.is_absolute():
            p = self._base / p
        return p if p.suffix else p.with_suffix(".geo")

    def title(self) -> str:
        return self.title_edit.text().strip() or DEFAULT_TITLE

    def primitive(self) -> Primitive | None:
        return None if self.start.currentData() == "empty" else self.shape.primitive()

    def is_module(self) -> bool:
        return self.start.currentData() == "module"

    def material_number(self) -> int:
        return int(self.material.value())

    def body_name(self) -> str:
        return self.name.text().strip()

    def create_input(self) -> bool:
        return self.make_in.isChecked()

    def _check(self) -> bool:
        msgs = []
        ok = True
        text = self.path_edit.text().strip()
        if not text:
            ok = False
            msgs.append("✖ Elija dónde guardar la geometría.")
        else:
            p = self.path()
            if not p.parent.is_dir():
                ok = False
                msgs.append(f"✖ No existe la carpeta {html.escape(str(p.parent))}.")
            from ..penhan.simfolder import in_name_problem

            why = in_name_problem(p.name, MAX_GEOMFN)  # GEOMFN es A20 y el .in va en Latin-1
            if why and self.make_in.isChecked():
                ok = False  # el .in nuevo no la encontraría
                msgs.append(f"✖ {html.escape(why[:1].upper() + why[1:])}: el .in (GEOMFN) no la encontraría. Use "
                            f"un nombre de {MAX_GEOMFN} caracteres o menos sin tildes ni eñes (o no cree el .in).")
            elif why:
                msgs.append(f"⚠ {html.escape(why[:1].upper() + why[1:])}: use otro nombre si va a simularla (sin "
                            f"tildes ni eñes y de {MAX_GEOMFN} caracteres o menos).")
            if p.suffix.lower() == ".in" or looks_like_input(p):
                ok = False
                msgs.append("✖ Ese fichero es (o se llama como) un fichero de entrada de penhmain (.in): la "
                            "geometría va en un .geo.")
            elif p.exists():
                msgs.append("⚠ Ya existe: se sustituirá (la versión anterior queda en .bak).")
        if self.start.currentData() != "empty":
            try:
                self.shape.primitive()
            except (EditError, ValueError) as exc:
                ok = False
                msgs.append("✖ " + html.escape(str(exc)))
        color = "#1a7f37" if ok and not msgs else ("#8a5a00" if ok else "#b42318")
        self.status.setText(f"<span style='color:{color}'>" + "<br>".join(msgs) + "</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
        return ok

    def accept(self) -> None:
        if self._check():
            super().accept()
