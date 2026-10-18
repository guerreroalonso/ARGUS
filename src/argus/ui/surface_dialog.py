"""Diálogo «Añadir una superficie» a un cuerpo: una que ya existe, un plano nuevo o una cuádrica implícita.

Mientras se escribe se dice qué parte del cuerpo quedaría fuera (con puntos de
su volumen y la ecuación de la superficie en coordenadas globales).
"""

from __future__ import annotations

import html
import math

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..pengeom import edit as ge
from ..pengeom import surfaces as sf
from ..pengeom.compiler import body_title
from ..pengeom.edit import EditError
from ..pengeom.model import COEF_NAMES
from ..pengeom.quadric import rotshf
from .geo_panel import COEF_LABELS, PARAM_LABELS, TRANSFORM
from .source_panel import NumberEdit, parse_number


def _row(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch(1)
    return w


class SurfaceDialog(QDialog):
    def __init__(self, parent, session, kb: int, point=None):
        super().__init__(parent)
        self.session = session
        self.geo = geo = session.geo
        self.kb = kb
        self.el = ge.editable_body(geo, kb)
        self._ok = False
        self.setWindowTitle("Añadir una superficie")
        lay = QVBoxLayout(self)
        head = QLabel(f"Limitar <b>{html.escape(body_title(geo, kb))}</b> con otra superficie: el cuerpo se queda "
                      "con la parte que está del lado elegido (lo que pierde vuelve a su contenedor).")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.rb_existing = QRadioButton("Una superficie que ya existe")
        self.rb_plane = QRadioButton("Un plano nuevo")
        self.rb_implicit = QRadioButton("Una cuádrica nueva (forma implícita)")
        self.mode = QButtonGroup(self)
        for b in (self.rb_plane, self.rb_existing, self.rb_implicit):
            self.mode.addButton(b)
        self.rb_plane.setChecked(True)
        # Existentes.
        lay.addWidget(self.rb_existing)
        self.existing = QComboBox()
        used = {ge.label_key(r.label) for r in self.el.items if r.is_surface}  # (con sus cuatro columnas)
        for s in session.doc.surfaces():
            if ge.label_key(s.label) in used:
                continue
            self.existing.addItem(f"«{s.label.strip()}» · {sf.surface_kind(s, session.parinp)}"
                                  + (f" · {s.description.strip()[:40]}" if s.description.strip() else ""), s.label)
        self.rb_existing.setEnabled(self.existing.count() > 0)
        lay.addWidget(self.existing)
        # Plano.
        lay.addWidget(self.rb_plane)
        self.plane_box = QWidget()
        pf = QFormLayout(self.plane_box)
        pf.setContentsMargins(18, 0, 0, 0)
        p = np.zeros(3) if point is None else np.asarray(point, dtype=float)
        self.px, self.py, self.pz = (NumberEdit(width=80) for _ in range(3))
        for ed, v in zip((self.px, self.py, self.pz), p):
            ed.set_value(ge._clean(float(v), 6))
        pf.addRow("Pasa por:", _row(QLabel("x"), self.px, QLabel("y"), self.py, QLabel("z"), self.pz, QLabel("cm")))
        self.nx, self.ny, self.nz = (NumberEdit(width=80) for _ in range(3))
        for ed, v in zip((self.nx, self.ny, self.nz), (0.0, 0.0, 1.0)):
            ed.set_value(v)
        presets = []
        for a in "xyz":
            b = QPushButton(f"⊥ {a}")
            b.setToolTip(f"Normal a lo largo del eje {a}")
            b.clicked.connect(lambda _c=False, a=a: self._preset(a))
            presets.append(b)
        pf.addRow("Normal:", _row(QLabel("x"), self.nx, QLabel("y"), self.ny, QLabel("z"), self.nz, *presets))
        lay.addWidget(self.plane_box)
        # Implícita.
        lay.addWidget(self.rb_implicit)
        self.implicit_box = QWidget()
        iv = QVBoxLayout(self.implicit_box)
        iv.setContentsMargins(18, 0, 0, 0)
        grid = QGridLayout()
        self.coef = {}
        for i, name in enumerate(COEF_NAMES):
            ed = NumberEdit(width=80)
            ed.set_value({"AXX": 1.0, "AYY": 1.0, "AZZ": 1.0, "A0": -1.0}.get(name, 0.0))
            self.coef[name] = ed
            grid.addWidget(QLabel(f"{name} ({COEF_LABELS[name]}):"), i // 2, 2 * (i % 2))
            grid.addWidget(ed, i // 2, 2 * (i % 2) + 1)
        iv.addLayout(grid)
        grid = QGridLayout()
        self.tr = {}
        rot, shift = ge.local_frame(geo, kb, session.parinp)
        p_loc = rot.T @ (p - shift)  # la cuádrica por defecto, centrada en el punto pulsado
        for i, name in enumerate(TRANSFORM):
            ed = NumberEdit(width=80)
            ed.set_value(ge._clean(float(p_loc[i - 3]), 6) if i >= 3 else 0.0)
            self.tr[name] = ed
            grid.addWidget(QLabel(PARAM_LABELS[name] + ":"), i % 3, 2 * (i // 3))
            grid.addWidget(ed, i % 3, 2 * (i // 3) + 1)
        iv.addLayout(grid)
        self.kind_label = QLabel()
        self.kind_label.setStyleSheet("color: #555;")
        self.kind_label.setWordWrap(True)
        iv.addWidget(self.kind_label)
        if not np.allclose(rot, np.eye(3)) or np.any(shift != 0):
            note = QLabel("Ojo: este cuerpo está dentro de un módulo girado o desplazado; los coeficientes y el giro "
                          "de la cuádrica están en el sistema de ese módulo (como en el .geo). El plano, en cambio, "
                          "se da en coordenadas globales.")
            note.setWordWrap(True)
            note.setStyleSheet("color: #8a5a00;")
            iv.addWidget(note)
        lay.addWidget(self.implicit_box)
        # Lado.
        self.rb_minus = QRadioButton("−1: el cuerpo se queda donde F < 0")
        self.rb_plus = QRadioButton("+1: donde F > 0")
        self.side = QButtonGroup(self)
        self.side.addButton(self.rb_minus)
        self.side.addButton(self.rb_plus)
        self.rb_minus.setChecked(True)
        lay.addWidget(_row(QLabel("Lado:"), self.rb_minus, self.rb_plus))
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setMinimumWidth(460)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._check)
        for ed in (self.px, self.py, self.pz, self.nx, self.ny, self.nz, *self.coef.values(), *self.tr.values()):
            ed.valueEdited.connect(lambda _v: self._timer.start())
            ed.textEdited.connect(lambda _t: self._timer.start())
        self.existing.currentIndexChanged.connect(lambda _i: self._timer.start())
        self.mode.buttonToggled.connect(lambda _b, on: on and self._on_mode())
        self.side.buttonToggled.connect(lambda _b, on: on and self._timer.start())
        # Puntos del cuerpo (para decir cuánto quedaría): en su caja, si sus superficies la acotan.
        from ..pengeom.extent import analytic_box

        lo, hi = session.bounds
        a_lo, a_hi = analytic_box(geo, kb)
        if np.all(np.isfinite(a_lo)) and np.all(np.isfinite(a_hi)) and np.all(a_hi > a_lo):
            lo, hi = a_lo, a_hi
        rng = np.random.default_rng(1)
        pts = rng.uniform(lo, hi, (60000, 3))
        ib, _ = session.locator.locate(pts)
        self.body_points = pts[ib == kb]
        self._on_mode()

    # -- valores -------------------------------------------------------------------------
    @staticmethod
    def _num(ed) -> float:
        try:
            return parse_number(ed.text())
        except ValueError:
            raise EditError("Faltan números (o hay algo que no es un número).") from None

    def _preset(self, axis: str) -> None:
        for ed, a in zip((self.nx, self.ny, self.nz), "xyz"):
            ed.set_value(1.0 if a == axis else 0.0)
        self._timer.start()

    def kind(self) -> str:
        if self.rb_existing.isChecked():
            return "existing"
        return "plane" if self.rb_plane.isChecked() else "implicit"

    def side_value(self) -> int:
        return -1 if self.rb_minus.isChecked() else 1

    def surface_label(self) -> str:
        return str(self.existing.currentData())

    def plane(self) -> tuple[np.ndarray, np.ndarray]:
        p = np.array([self._num(ed) for ed in (self.px, self.py, self.pz)])
        n = np.array([self._num(ed) for ed in (self.nx, self.ny, self.nz)])
        if np.linalg.norm(n) == 0:
            raise EditError("La normal del plano no puede ser cero.")
        return p, n

    def implicit(self) -> tuple[dict[str, float], dict[str, float]]:
        return ({n: self._num(ed) for n, ed in self.coef.items()}, {n: self._num(ed) for n, ed in self.tr.items()})

    def global_quadric(self):
        """La superficie elegida en coordenadas globales (para ver qué parte del cuerpo queda)."""
        kind = self.kind()
        if kind == "existing":
            alias = self.surface_label()
            for e in self.geo.surfaces:
                if e.alias == alias.rjust(4)[:4] + "0":
                    return e.q
            raise EditError("No se encuentra la superficie.")
        if kind == "plane":
            p, n = self.plane()
            n = n / np.linalg.norm(n)
            return (0.0,) * 6 + (float(n[0]), float(n[1]), float(n[2]), -float(n @ p))
        coefs, tr = self.implicit()
        q = tuple(coefs[n] for n in COEF_NAMES)
        q = rotshf(math.radians(tr["OMEGA"]), math.radians(tr["THETA"]), math.radians(tr["PHI"]),
                   tr["X-SHIFT"], tr["Y-SHIFT"], tr["Z-SHIFT"], q)
        rot, shift = ge.local_frame(self.geo, self.kb, self.session.parinp)
        if not np.allclose(rot, np.eye(3)) or np.any(shift != 0):
            om, th, ph = ge.euler_angles(rot)
            q = rotshf(math.radians(om), math.radians(th), math.radians(ph), *shift, q)
        return q

    # -- interfaz -----------------------------------------------------------------------
    def _on_mode(self) -> None:
        kind = self.kind()
        self.existing.setEnabled(kind == "existing")
        self.plane_box.setEnabled(kind == "plane")
        self.implicit_box.setEnabled(kind == "implicit")
        if kind == "plane":
            self.rb_minus.setText("−1: el cuerpo se queda del lado contrario a la normal")
            self.rb_plus.setText("+1: del lado hacia el que apunta la normal")
        else:
            self.rb_minus.setText("−1: el cuerpo se queda donde F < 0")
            self.rb_plus.setText("+1: donde F > 0")
        self._timer.start()

    def _check(self) -> None:
        try:
            q = self.global_quadric()
        except (EditError, ValueError) as exc:
            self._set(False, html.escape(str(exc)))
            return
        if self.kind() == "implicit":
            self.kind_label.setText("Es un(a) " + sf.classify(q) + ".")
        pts = self.body_points
        if len(pts) == 0:
            self._set(True, "No se ha podido muestrear el cuerpo (es muy pequeño): se añadirá sin comprobar cuánto "
                            "recorta.")
            return
        axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = q
        x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
        f = axx * x * x + axy * x * y + axz * x * z + ayy * y * y + ayz * y * z + azz * z * z + ax * x + ay * y \
            + az * z + a0
        keep = float(np.mean(self.side_value() * f > 0))
        if keep == 0.0:
            self._set(False, "El cuerpo desaparecería entero: con ese lado no queda nada de él. Cambie el lado.")
        elif keep == 1.0:
            self._set(True, "La superficie no recorta el cuerpo (queda entero).")
        else:
            self._set(True, f"El cuerpo se queda con el {100 * keep:.1f} % de su volumen.".replace(".", ",", 1))

    def _set(self, ok: bool, message: str) -> None:
        self._ok = ok
        color = "#1a7f37" if ok else "#b42318"
        self.status.setText(f"<span style='color:{color}'>{'✔' if ok else '✖'} {message}</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def done(self, r: int) -> None:
        # Cerrado (Aceptar, Cancelar o Esc): la comprobación que quedaba pendiente ya no es de nadie; si saltara
        # después, miraría los cuerpos de entonces en la geometría de ahora (décima revisión).
        self._timer.stop()
        super().done(r)

    def accept(self) -> None:
        self._timer.stop()
        self._check()
        if not self._ok:
            return
        # La edición de verdad, probada en una copia (PENGEOM, los lados de las superficies, los solapes): si se
        # rechazaría, el diálogo sigue abierto con lo escrito y dice por qué.
        from ..pengeom.overlap import guard
        from .geo_controller import trial_edit

        try:
            problem = trial_edit(self.session, self.edit_fn(), guard(self.session.bounds, [self.geo.balias(self.kb)]))
        except (EditError, ValueError) as exc:
            problem = str(exc)
        if problem:
            self._set(False, html.escape(problem))
            return
        super().accept()

    # -- aplicar -------------------------------------------------------------------------
    def edit_fn(self):
        """La edición que añade la superficie elegida (para ``MainWindow.edit_geometry``)."""
        kind, side, lab = self.kind(), self.side_value(), self.el.label
        parinp = self.session.parinp
        if kind == "existing":
            s_lab = self.surface_label()
            return lambda doc, geo: sf.add_surface(doc, geo, geo.find_body(lab).index, s_lab, side, as_seen=True,
                                                   parinp=parinp)
        if kind == "plane":
            p, n = self.plane()

            def fn(doc, geo):
                kb = geo.find_body(lab).index
                new = sf.new_plane(doc, geo, kb, p, n, parinp=parinp)
                sf.add_surface(doc, geo, kb, new, side)
            return fn
        coefs, tr = self.implicit()

        def fn2(doc, geo):
            kb = geo.find_body(lab).index
            new = sf.new_implicit(doc, geo, kb, coefs, tr)
            sf.add_surface(doc, geo, kb, new, side)
        return fn2

