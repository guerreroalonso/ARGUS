"""Diálogo «Pegar un módulo a otro elemento»: lo desplaza hasta que toca una cara del otro.

Se elige el elemento al que pegarlo, la cara (±x, ±y, ±z) y si queda
centrado en esa cara o solo se mueve a lo largo del eje. Las caras salen de
las cajas exactas de los dos elementos (``pengeom.extent``) y antes de mover
se comprueba con LOCATE que el módulo no se solapa con nada.
"""

from __future__ import annotations

import html

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from ..pengeom import edit as ge
from ..pengeom.compiler import body_title
from ..pengeom.edit import FACE_NAMES, FACES
from ..pengeom.extent import solid_bodies


def glue_candidates(geo, moving: int) -> list[int]:
    """Elementos a los que se puede pegar ``moving``: ni lo que tiene dentro ni lo que lo contiene."""
    inner = solid_bodies(geo, moving)
    outer = set()
    kb = ge.logical_parent(geo, moving)
    while kb:
        outer.add(kb)
        kb = ge.logical_parent(geo, kb)
    return [b.index for b in geo.bodies
            if b.origin != "enclosure" and b.index not in inner and b.index not in outer]


def facing_face(target_extent, moving_extent) -> str:
    """Cara del objetivo que mira hacia el elemento que se mueve."""
    t = 0.5 * (np.asarray(target_extent[0]) + np.asarray(target_extent[1]))
    m = 0.5 * (np.asarray(moving_extent[0]) + np.asarray(moving_extent[1]))
    return ge.face_from_normal(m - t) if np.any(m != t) else "+x"


class GlueDialog(QDialog):
    def __init__(self, parent, session, moving: int, extent_fn, target: int | None = None, face: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Pegar un módulo")
        self.session = session
        self.geo = session.geo
        self.moving = moving
        self.extent_fn = extent_fn
        self._extents: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self._ok = False
        self._delta = np.zeros(3)
        lay = QVBoxLayout(self)
        intro = QLabel(f"Se moverá {html.escape(body_title(self.geo, moving))}, con todo lo que tiene dentro, "
                       "hasta tocar la cara elegida.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        lay.addLayout(form)
        self.target_box = QComboBox()
        cands = glue_candidates(self.geo, moving)
        m_ext = self._extent(moving)
        m_center = 0.5 * (m_ext[0] + m_ext[1])

        def dist(kb):  # los más cercanos primero (con la caja analítica, rápida)
            from ..pengeom.extent import element_box

            lo, hi = element_box(self.geo, kb)
            with np.errstate(invalid="ignore", over="ignore"):  # cajas infinitas (semiespacios)
                c = np.where(np.isfinite(lo) & np.isfinite(hi), 0.5 * (lo + hi), m_center)
                d = float(np.linalg.norm(c - m_center))
            return d if np.isfinite(d) else float("inf")

        cands.sort(key=dist)
        if target is not None and target in cands:
            cands.remove(target)
            cands.insert(0, target)
        for kb in cands:
            self.target_box.addItem(ge.element_name(self.geo, kb), kb)
        form.addRow("Pegar a:", self.target_box)
        self.face_box = QComboBox()
        for key, text in FACE_NAMES.items():
            self.face_box.addItem(f"su cara {text}", key)
        form.addRow("Por:", self.face_box)
        self.align_box = QComboBox()
        self.align_box.addItem("centrado en esa cara", "center")
        self.align_box.addItem("sin moverlo en los otros dos ejes", "keep")
        form.addRow("Alineación:", self.align_box)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.setMinimumWidth(420)
        lay.addWidget(self.result)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Pegar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._update)
        self.target_box.currentIndexChanged.connect(self._on_target)
        self.face_box.currentIndexChanged.connect(lambda _i: self._timer.start())
        self.align_box.currentIndexChanged.connect(lambda _i: self._timer.start())
        self._face_given = face if face in FACES else None
        self._on_target()

    def _extent(self, kb: int):
        if kb not in self._extents:
            self._extents[kb] = tuple(np.asarray(v, dtype=float) for v in self.extent_fn(kb))
        return self._extents[kb]

    def target(self) -> int | None:
        kb = self.target_box.currentData()
        return int(kb) if kb else None

    def delta(self) -> np.ndarray:
        return self._delta.copy()

    def _on_target(self, *_args) -> None:
        t = self.target()
        if t is None:
            self._set_result(False, "No hay ningún elemento al que pegarlo.")
            return
        face = self._face_given or facing_face(self._extent(t), self._extent(self.moving))
        self._face_given = None  # solo la primera vez
        self.face_box.blockSignals(True)
        self.face_box.setCurrentIndex(self.face_box.findData(face))
        self.face_box.blockSignals(False)
        self._timer.start(0)

    def _update(self) -> None:
        t = self.target()
        if t is None:
            return
        face = str(self.face_box.currentData())
        delta = ge.glue_delta(self._extent(self.moving), self._extent(t), face, str(self.align_box.currentData()))
        self._delta = delta
        d = ", ".join(f"{v:g}" for v in delta)
        if not np.any(delta):
            self._set_result(True, "Ya está pegado ahí.")
            return
        chk = ge.check_move(self.geo, self.session.locator, self.moving, delta, self._extent(self.moving))
        msg = f"Se desplazará ({html.escape(d)}) cm. "
        if chk.ok:
            self._set_result(True, msg + "No se solapa con nada.")
        else:
            self._set_result(False, msg + html.escape(chk.message) + " Pruebe otra cara u otra alineación.")

    def _set_result(self, ok: bool, message: str) -> None:
        self._ok = ok
        color = "#1a7f37" if ok else "#b42318"
        self.result.setText(f"<span style='color:{color}'>{'✔' if ok else '✖'} {message}</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok and bool(np.any(self._delta)))

    def edit_fn(self):
        """La edición que mueve el módulo (la que hace ``MainWindow``)."""
        from ..pengeom import placement as pl

        delta, label, s = self.delta(), self.geo.balias(self.moving), self.session
        return lambda doc, g: pl.move_element(doc, g, g.find_body(label).index, pl.translation(delta), s.parinp,
                                              bounds=s.bounds) or True

    def done(self, r: int) -> None:
        # Cerrado (Aceptar, Cancelar o Esc): la comprobación que quedaba pendiente ya no es de nadie; si saltara
        # después, miraría los cuerpos de entonces en la geometría de ahora (décima revisión).
        self._timer.stop()
        super().done(r)

    def accept(self) -> None:
        self._timer.stop()
        self._update()
        if not (self._ok and np.any(self._delta)):
            return
        from .geo_controller import trial_edit  # probado en una copia: si no se puede, se dice aquí

        try:
            problem = trial_edit(self.session, self.edit_fn())
        except (ge.EditError, ValueError) as exc:
            problem = str(exc)
        if problem:
            self._set_result(False, html.escape(problem))
            return
        super().accept()
