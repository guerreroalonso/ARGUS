"""Diálogo «Duplicar»: copias independientes o clones enlazados, desplazados o pegados por una cara."""

from __future__ import annotations

import html

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..pengeom import edit as ge
from ..pengeom import placement as pl
from ..pengeom.duplicate import MAX_COPIES, check_copies
from ..pengeom.edit import FACE_NAMES, FACES, EditError
from .source_panel import NumberEdit, parse_number


def _row(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch(1)
    return w


class DuplicateDialog(QDialog):
    """``extent_fn(kb) -> (lo, hi)``: caja exacta de un elemento."""

    def __init__(self, parent, session, kb: int, extent_fn):
        super().__init__(parent)
        self.session = session
        self.geo = session.geo
        self.kb = kb
        self.extent = extent_fn(kb)
        self._ok = False
        geo = self.geo
        self.setWindowTitle("Duplicar")
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{html.escape(ge.element_name(geo, kb, 50))}</b>")
        head.setWordWrap(True)
        lay.addWidget(head)
        form = QFormLayout()
        lay.addLayout(form)
        self.count = QSpinBox()
        self.count.setRange(1, MAX_COPIES)
        form.addRow("Copias:", self.count)
        # Colocación.
        self.rb_glue = QRadioButton("Pegadas por la cara")
        self.rb_shift = QRadioButton("Desplazadas")
        self.place = QButtonGroup(self)
        self.place.addButton(self.rb_glue)
        self.place.addButton(self.rb_shift)
        self.rb_glue.setChecked(True)
        self.face = QComboBox()
        for key, text in FACE_NAMES.items():
            self.face.addItem(f"cara {text}", key)
        self.gap = NumberEdit(width=70)
        self.gap.set_value(0.0)
        self.gap.setToolTip("Hueco entre copias (cm); 0 = cara con cara")
        form.addRow(self.rb_glue, _row(self.face, QLabel("separadas"), self.gap, QLabel("cm")))
        self.dx, self.dy, self.dz = (NumberEdit(width=80) for _ in range(3))
        for ed in (self.dx, self.dy, self.dz):
            ed.set_value(0.0)
        form.addRow(self.rb_shift, _row(QLabel("x"), self.dx, QLabel("y"), self.dy, QLabel("z"), self.dz, QLabel("cm")))
        # Tipo.
        linkable = True
        try:
            pl.placeable(geo, kb)
        except EditError:
            linkable = False
        self.rb_copy = QRadioButton("Copias independientes (cada una se edita por separado)")
        self.rb_clone = QRadioButton("Clones enlazados (CLONE): repiten el original; si cambia el original, cambian "
                                     "todos")
        self.kind = QButtonGroup(self)
        self.kind.addButton(self.rb_copy)
        self.kind.addButton(self.rb_clone)
        self.rb_copy.setChecked(True)
        self.rb_clone.setEnabled(linkable)
        if not linkable:
            self.rb_clone.setToolTip("Solo se pueden clonar módulos (un cuerpo se puede convertir antes en módulo)")
        box = QVBoxLayout()
        box.addWidget(self.rb_copy)
        box.addWidget(self.rb_clone)
        w = QWidget()
        box.setContentsMargins(0, 0, 0, 0)
        w.setLayout(box)
        form.addRow("Tipo:", w)
        # Dónde.
        self.container_box = QComboBox()
        # Sin envolvente, el de arriba no tiene contenedor: las copias van al nivel superior (fuera de todo).
        parent_kb = ge.logical_parent(geo, kb) or ge.top_level(geo)
        for c in ge.container_chain(geo, parent_kb) or [parent_kb]:
            if geo.enclosure_added and c == geo.nbodys:
                title = "La envolvente (fuera de todo lo demás)"
            elif c > geo.nbodys:
                title = "El nivel superior (al lado de lo que hay, fuera de todo)"
            else:
                title = ge.element_name(geo, c)
            self.container_box.addItem(title, c)
        form.addRow("Dentro de:", self.container_box)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setMinimumWidth(460)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Duplicar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._check)
        for ed in (self.gap, self.dx, self.dy, self.dz):
            ed.valueEdited.connect(lambda _v: self._timer.start())
            ed.textEdited.connect(lambda _t: self._timer.start())
        self.count.valueChanged.connect(lambda _v: self._timer.start())
        self.face.currentIndexChanged.connect(lambda _i: self._on_face())
        self.place.buttonToggled.connect(lambda _b, on: on and self._on_place())
        self.kind.buttonToggled.connect(lambda _b, on: on and self._timer.start())
        self.container_box.currentIndexChanged.connect(lambda _i: self._timer.start())
        self._on_face()
        self._on_place()

    # -- valores ---------------------------------------------------------------------
    @staticmethod
    def _num(ed) -> float:
        try:
            return parse_number(ed.text())
        except ValueError:
            raise EditError("Escriba números en el desplazamiento y la separación.") from None

    def step(self) -> np.ndarray:
        """Desplazamiento entre una copia y la siguiente (cm, global)."""
        if self.rb_glue.isChecked():
            axis, sign = FACES[self.face.currentData()]
            lo, hi = self.extent
            out = np.zeros(3)
            out[axis] = sign * (float(hi[axis] - lo[axis]) + self._num(self.gap))
            return np.array([ge._clean(float(v)) for v in out])
        return np.array([self._num(ed) for ed in (self.dx, self.dy, self.dz)])

    def copies(self) -> int:
        return int(self.count.value())

    def linked(self) -> bool:
        return self.rb_clone.isChecked()

    def container(self) -> int:
        return int(self.container_box.currentData())

    # -- interfaz -----------------------------------------------------------------------
    def _on_face(self) -> None:
        if self.rb_glue.isChecked():
            try:
                s = self.step()
            except EditError:
                s = None
            if s is not None:
                for ed, v in zip((self.dx, self.dy, self.dz), s):
                    ed.set_value(float(v))
        self._timer.start()

    def _on_place(self) -> None:
        glue = self.rb_glue.isChecked()
        self.face.setEnabled(glue)
        self.gap.setEnabled(glue)
        for ed in (self.dx, self.dy, self.dz):
            ed.setEnabled(not glue)
        self._on_face()

    def _check(self) -> None:
        try:
            step = self.step()
        except EditError as exc:
            self._set(False, html.escape(str(exc)))
            return
        if not np.any(step != 0.0):
            self._set(False, "El desplazamiento no puede ser cero.")
            return
        try:
            chk = check_copies(self.geo, self.session.locator, self.kb, step, self.copies(), self.container(),
                               self.extent, bounds=getattr(self.session, "bounds", None))
        except (EditError, ValueError) as exc:  # una caja degenerada no rompe el diálogo (ranura del temporizador)
            self._set(False, html.escape(f"No se ha podido comprobar: {exc}"))
            return
        msg = html.escape(chk.message)
        if chk.ok and self.linked():
            msg += " Serán clones: en el .in, los cuerpos de dentro de un clon se nombran por número."
        self._set(chk.ok, msg)

    def _set(self, ok: bool, message: str) -> None:
        self._ok = ok
        color = "#1a7f37" if ok else "#b42318"
        self.status.setText(f"<span style='color:{color}'>{'✔' if ok else '✖'} {message}</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def edit_fn(self):
        """La edición que hace las copias o los clones (la que hace ``MainWindow``)."""
        from ..pengeom.duplicate import duplicate
        from .geo_controller import body_finder

        step, count, linked, cont = self.step(), self.copies(), self.linked(), self.container()
        geo, kb, parinp = self.geo, self.kb, self.session.parinp
        find = body_finder(geo)
        top = ge.is_top_level(geo, cont)  # la envolvente o, si no la hay, «fuera de todo» (NBODYS+1)

        def fn(doc, g):
            c = ge.top_level(g) if top else find(g, cont)
            return duplicate(doc, g, find(g, kb), step, count, c, linked=linked, parinp=parinp)

        return fn

    def accept(self) -> None:
        self._timer.stop()
        self._check()
        if not self._ok:
            return
        from .geo_controller import trial_edit  # probado en una copia: si no se puede, se dice aquí

        try:
            problem = trial_edit(self.session, self.edit_fn())
        except (EditError, ValueError) as exc:
            problem = str(exc)
        if problem:
            self._set(False, html.escape(problem))
            return
        super().accept()
