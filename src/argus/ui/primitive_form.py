"""Formulario de una forma sencilla (esfera, cilindro o caja): centro y medidas en cm."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QWidget

from ..pengeom.edit import PRIMITIVES, EditError, Primitive
from .source_panel import NumberEdit, parse_number


def _row(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch(1)
    return w


class PrimitiveForm(QWidget):
    """Forma, centro, medidas y orientación; ``changed`` con cada tecla (para comprobar en vivo)."""

    changed = Signal()

    def __init__(self, kinds=("box", "cylinder", "sphere"), parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.form = form
        self.kind = QComboBox()
        for k in kinds:
            self.kind.addItem(PRIMITIVES[k], k)
        form.addRow("Forma:", self.kind)
        self.cx, self.cy, self.cz = (NumberEdit(width=80) for _ in range(3))
        form.addRow("Centro:", _row(QLabel("x"), self.cx, QLabel("y"), self.cy, QLabel("z"), self.cz, QLabel("cm")))
        self.radius = NumberEdit(minimum=1e-12, width=90)
        form.addRow("Radio (cm):", self.radius)
        self.height = NumberEdit(minimum=1e-12, width=90)
        form.addRow("Altura (cm):", self.height)
        self.sx, self.sy, self.sz = (NumberEdit(minimum=1e-12, width=80) for _ in range(3))
        self.sides_row = _row(QLabel("x"), self.sx, QLabel("y"), self.sy, QLabel("z"), self.sz, QLabel("cm"))
        form.addRow("Lados:", self.sides_row)
        self.axis = QComboBox()
        for a in "xyz":
            self.axis.addItem(f"eje {a}", a)
        self.axis.setCurrentIndex(2)
        form.addRow("Orientación:", self.axis)
        for ed in (self.cx, self.cy, self.cz, self.radius, self.height, self.sx, self.sy, self.sz):
            ed.valueEdited.connect(lambda _v: self.changed.emit())
            ed.textEdited.connect(lambda _t: self.changed.emit())
        self.kind.currentIndexChanged.connect(lambda _i: self._on_kind())
        self.axis.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.set_primitive(Primitive("box", sides=(10.0, 10.0, 10.0), radius=5.0, height=10.0))

    def _on_kind(self) -> None:
        k = self.kind.currentData()
        self.form.setRowVisible(self.radius, k in ("sphere", "cylinder"))
        self.form.setRowVisible(self.height, k == "cylinder")
        self.form.setRowVisible(self.sides_row, k == "box")
        self.form.setRowVisible(self.axis, k == "cylinder")
        self.changed.emit()

    @staticmethod
    def _num(ed: NumberEdit) -> float | None:
        try:
            return parse_number(ed.text())
        except ValueError:
            return None

    def set_primitive(self, prim: Primitive) -> None:
        idx = self.kind.findData(prim.kind)
        if idx >= 0:
            self.kind.blockSignals(True)
            self.kind.setCurrentIndex(idx)
            self.kind.blockSignals(False)
        for ed, v in zip((self.cx, self.cy, self.cz), prim.center):
            ed.set_value(float(v))
        self.radius.set_value(float(prim.radius))
        self.height.set_value(float(prim.height))
        for ed, v in zip((self.sx, self.sy, self.sz), prim.sides):
            ed.set_value(float(v))
        self.axis.blockSignals(True)
        self.axis.setCurrentIndex(max(0, self.axis.findData(prim.axis)))
        self.axis.blockSignals(False)
        self._on_kind()

    def primitive(self) -> Primitive:
        """La forma escrita (aunque no se haya confirmado con Intro); EditError si falta algo."""
        c = [self._num(ed) for ed in (self.cx, self.cy, self.cz)]
        if any(v is None for v in c):
            raise EditError("Falta el centro (o no es un número).")
        prim = Primitive(self.kind.currentData(), center=tuple(float(v) for v in c),
                         radius=float(self._num(self.radius) or 0.0), height=float(self._num(self.height) or 0.0),
                         sides=tuple(float(self._num(ed) or 0.0) for ed in (self.sx, self.sy, self.sz)),
                         axis=self.axis.currentData())
        prim.validate()
        return prim
