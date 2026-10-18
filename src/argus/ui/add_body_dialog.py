"""Diálogo «Añadir un cuerpo o un módulo» (F6).

Se elige qué crear (un cuerpo, o un módulo que después puede tener cuerpos
dentro), dónde (el cuerpo o módulo que lo contendrá: el pulsado o cualquiera
de los que lo contienen, hasta la envolvente), si va pegado a la cara de otro
elemento, las medidas y el material. Mientras se escribe se comprueba con
LOCATE que cabe entero en el contenedor (PENGEOM no admite solapes); si no
cabe, se dice dónde cabría. El botón «Añadir» solo se activa si cabe.
"""

from __future__ import annotations

import html
import math
import re

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
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..pengeom import edit as ge
from ..pengeom.compiler import body_title
from ..pengeom.edit import (
    FACE_NAMES,
    FACES,
    PRIMITIVES,
    PRISM_MEASURES,
    PRISM_SIDES,
    EditError,
    Primitive,
    check_placement,
    prism_measure_name,
    prism_radius,
)
from ..render.slice2d import nice_length
from .source_panel import NumberEdit, parse_number


def container_description(geo, kb: int, module: bool = False) -> str:
    return ge.container_description(geo, kb, module)


class AddBodyDialog(QDialog):
    """Pide qué crear, dónde, las medidas y el material del cuerpo o módulo nuevo.

    ``extent_fn(kb) -> (lo, hi)``: caja exacta de un elemento (para pegar).
    """

    def __init__(self, parent, session, container: int, center, material_names: dict[int, str] | None = None,
                 material: int | None = None, *, module: bool = False, glue_target: int | None = None,
                 glue_face: str | None = None, extent_fn=None):
        super().__init__(parent)
        self.session = session
        self.geo = session.geo
        self.extent_fn = extent_fn
        self._ok = False
        self._updating = False
        self._extents: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        lo, hi = session.bounds
        self.bounds = (np.asarray(lo, dtype=float), np.asarray(hi, dtype=float))
        chain_from = container
        if glue_target is not None:
            parent_kb = ge.logical_parent(self.geo, glue_target)
            chain_from = parent_kb or container
        self.chain = ge.container_chain(self.geo, chain_from) or [container]

        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.form = form
        lay.addLayout(form)
        # Qué se crea.
        self.rb_body = QRadioButton("Cuerpo")
        self.rb_module = QRadioButton("Módulo")
        self.rb_body.setToolTip("Un volumen de un material (puede tener otros cuerpos dentro).")
        self.rb_module.setToolTip("Un contenedor: puede tener cuerpos dentro y se mueve o se gira con todo lo que "
                                  "tiene; dos módulos pueden ir pegados uno junto a otro.")
        self.mode = QButtonGroup(self)
        self.mode.addButton(self.rb_body)
        self.mode.addButton(self.rb_module)
        (self.rb_module if module else self.rb_body).setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.rb_body)
        row.addWidget(self.rb_module)
        row.addStretch(1)
        self.mode_row = _wrap(row)
        form.addRow("Crear:", self.mode_row)
        self.kind = QComboBox()
        for key, text in PRIMITIVES.items():
            self.kind.addItem(text, key)
        form.addRow("Forma:", self.kind)
        # Dónde.
        self.container_box = QComboBox()
        for kb in self.chain:
            self.container_box.addItem(self._container_title(kb), kb)
        self.container_box.setCurrentIndex(max(0, self.container_box.findData(
            self.chain[0] if glue_target is not None else container)))
        form.addRow("Dentro de:", self.container_box)
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.where.setStyleSheet("color: #555;")
        form.addRow("", self.where)
        self.glue_box = QComboBox()
        self.face_box = QComboBox()
        for key, text in FACE_NAMES.items():
            self.face_box.addItem(f"cara {text}", key)
        self.btn_center = QPushButton("Centrar en la cara")
        self.btn_center.setToolTip("Pone el centro del nuevo en el centro de esa cara (en los otros dos ejes)")
        row = QHBoxLayout()
        row.addWidget(self.glue_box, 1)
        row.addWidget(self.face_box)
        row.addWidget(self.btn_center)
        self.glue_row = _wrap(row)
        form.addRow("Pegado a:", self.glue_row)
        row = QHBoxLayout()
        self.cx, self.cy, self.cz = (NumberEdit(width=90) for _ in range(3))
        for lab, ed in (("x", self.cx), ("y", self.cy), ("z", self.cz)):
            row.addWidget(QLabel(lab))
            row.addWidget(ed)
        row.addWidget(QLabel("cm"))
        self.center_row = _wrap(row)
        form.addRow("Centro:", self.center_row)
        # Medidas.
        self.radius = NumberEdit(minimum=1e-12, width=90)
        form.addRow("Radio (cm):", self.radius)
        row = QHBoxLayout()
        self.pmeasure = QComboBox()
        self.psize = NumberEdit(minimum=1e-12, width=90)
        row.addWidget(self.pmeasure)
        row.addWidget(self.psize)
        row.addWidget(QLabel("cm"))
        self.psize_row = _wrap(row)
        form.addRow("Tamaño:", self.psize_row)
        self.height = NumberEdit(minimum=1e-12, width=90)
        form.addRow("Altura (cm):", self.height)
        row = QHBoxLayout()
        self.sx, self.sy, self.sz = (NumberEdit(minimum=1e-12, width=80) for _ in range(3))
        for lab, ed in (("x", self.sx), ("y", self.sy), ("z", self.sz)):
            row.addWidget(QLabel(lab))
            row.addWidget(ed)
        row.addWidget(QLabel("cm"))
        self.sides_row = _wrap(row)
        form.addRow("Lados:", self.sides_row)
        self.axis = QComboBox()
        for a in "xyz":
            self.axis.addItem(f"eje {a}", a)
        self.axis.setCurrentIndex(2)
        form.addRow("Orientación:", self.axis)
        self.angle = NumberEdit(width=90)
        self.angle.set_value(0.0)
        self.angle.setToolTip("Giro del polígono alrededor de su eje (grados). Con 0, un lado queda abajo (cuadrado "
                              "con los lados paralelos a los ejes, hexágono con dos caras arriba y abajo).")
        form.addRow("Giro (°):", self.angle)
        # Material y nombre.
        self.material = QComboBox()
        self.material.setEditable(True)
        # Lo que se escribe (un número) no se añade como elemento de la lista (sin dato, daba TypeError al
        # pulsar Enter): se lee en material_number().
        self.material.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        names = material_names or {}
        used = sorted(set(self.geo.materials()) | set(names))
        for m in used:
            self.material.addItem(f"{m}" + (f" · {names[m]}" if m in names else ""), m)
        nxt = new_material_number(used)
        if nxt is not None:
            self.material.addItem(f"{nxt} (material nuevo)", nxt)
        self.material.addItem("0 (vacío)", 0)
        if material is not None:
            idx = self.material.findData(material)
            self.material.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Material:", self.material)
        self.name = QLineEdit()
        self.name.setPlaceholderText("opcional (comentario del elemento en el .geo)")
        form.addRow("Nombre:", self.name)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.linkActivated.connect(self._on_link)
        self.status.setMinimumWidth(420)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._check)
        for ed in (self.cx, self.cy, self.cz, self.radius, self.height, self.sx, self.sy, self.sz, self.psize,
                   self.angle):
            ed.valueEdited.connect(lambda _v: self._changed())
            ed.textEdited.connect(lambda _t: self._changed())
        self.pmeasure.currentIndexChanged.connect(lambda _i: self._changed())
        self.material.editTextChanged.connect(lambda _t: self._changed())
        self.kind.currentIndexChanged.connect(lambda _i: self._on_kind())
        self.axis.currentIndexChanged.connect(lambda _i: self._changed())
        self.mode.buttonToggled.connect(lambda _b, on: on and self._on_mode())
        self.container_box.currentIndexChanged.connect(lambda _i: self._on_container())
        self.glue_box.currentIndexChanged.connect(lambda _i: self._on_glue())
        self.face_box.currentIndexChanged.connect(lambda _i: self._changed())
        self.btn_center.clicked.connect(self._center_on_face)

        c = [float(v) for v in center]
        for ed, v in zip((self.cx, self.cy, self.cz), c):
            ed.set_value(_round(v, self._step()))
        self._fill_glue(glue_target)
        if glue_face in FACES:
            self.face_box.setCurrentIndex(self.face_box.findData(glue_face))
        if module:
            self.kind.setCurrentIndex(self.kind.findData("box"))  # lo normal al pegar módulos: cajas
        if glue_target is not None:
            self._center_on_face()  # centrado en la cara (se puede cambiar)
        self._defaults()
        self._on_mode()

    # -- valores -------------------------------------------------------------------
    def _container_title(self, kb: int) -> str:
        if self.geo.enclosure_added and kb == self.geo.nbodys:
            return "La envolvente (fuera de todo lo demás)"
        if kb > self.geo.nbodys:
            return "El nivel superior (al lado de lo que hay, fuera de todo)"
        return ge.element_name(self.geo, kb)

    def _step(self) -> float:
        """Paso para redondear el punto pulsado (quita el ruido de la posición del ratón)."""
        lo, hi = self.bounds
        return 10.0 ** math.floor(math.log10(max(float(np.max(hi - lo)), 1e-9) / 1e5))

    def _defaults(self) -> None:
        """Medidas iniciales redondas que quepan en el contenedor (si es posible)."""
        lo, hi = self.bounds
        size = nice_length(0.1 * float(np.max(hi - lo)) or 1.0)
        target = self.glue_target()
        if target is not None:
            t_lo, t_hi = self._extent(target)
            size = nice_length(0.5 * float(np.max(t_hi - t_lo)) or size)
        for _ in range(12):
            self.radius.set_value(size / 2)
            self.height.set_value(size)
            self.psize.set_value(size / 2)
            for ed in (self.sx, self.sy, self.sz):
                ed.set_value(size)
            self._update_glue_center()
            prim = self._primitive_or_none("sphere" if target is None else None)
            if prim is not None and check_placement(self.geo, self.session.locator, prim, self.container(),
                                                    self.bounds, n=3000).ok:
                return
            size = nice_length(size / 2.5)

    def _primitive_or_none(self, kind: str | None = None) -> Primitive | None:
        try:
            return self.primitive(kind)
        except (EditError, TypeError, ValueError):
            return None

    @staticmethod
    def _num(ed: NumberEdit) -> float | None:
        """Valor escrito en el campo (aunque todavía no se haya confirmado con Intro)."""
        try:
            return parse_number(ed.text())
        except ValueError:
            return None

    def primitive(self, kind: str | None = None) -> Primitive:
        vals = [self._num(ed) for ed in (self.cx, self.cy, self.cz)]
        if any(v is None for v in vals):
            raise EditError("Falta el centro (o no es un número).")
        k = kind or self.kind.currentData()
        radius = float(self._num(self.radius) or 0.0)
        angle = 0.0
        if k in PRISM_SIDES:
            size = self._num(self.psize)
            if size is None or not size > 0:
                raise EditError("Falta el tamaño del polígono (o no es un número positivo).")
            radius = prism_radius(PRISM_SIDES[k], self.pmeasure.currentData() or "side", size)
            angle = float(self._num(self.angle) or 0.0)
        prim = Primitive(k, center=tuple(float(v) for v in vals), radius=radius,
                         height=float(self._num(self.height) or 0.0),
                         sides=tuple(float(self._num(ed) or 0.0) for ed in (self.sx, self.sy, self.sz)),
                         axis=self.axis.currentData(), angle=angle)
        prim.validate()
        return prim

    def is_module(self) -> bool:
        return self.rb_module.isChecked()

    def container(self) -> int:
        return int(self.container_box.currentData())

    def glue_target(self) -> int | None:
        kb = self.glue_box.currentData()
        return int(kb) if kb else None

    def glue_face(self) -> str:
        return str(self.face_box.currentData())

    def material_number(self) -> int:
        return combo_material(self.material)

    def body_name(self) -> str:
        return self.name.text().strip()

    def _extent(self, kb: int):
        if kb not in self._extents:
            if self.extent_fn is not None:
                self._extents[kb] = self.extent_fn(kb)
            else:
                from ..pengeom.extent import element_extent

                self._extents[kb] = element_extent(self.geo, None, kb, self.bounds)
        return self._extents[kb]

    # -- interfaz ----------------------------------------------------------------------
    def _changed(self) -> None:
        if self._updating:
            return
        self._update_glue_center()
        self._timer.start()

    def _on_mode(self) -> None:
        module = self.is_module()
        self.setWindowTitle("Añadir un módulo" if module else "Añadir un cuerpo")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        item = self.kind.model().item(self.kind.findData("slab"))
        if item is not None:
            item.setEnabled(not module)
        if module and self.kind.currentData() == "slab":
            self.kind.setCurrentIndex(self.kind.findData("box"))
        self._on_container()

    def _on_container(self) -> None:
        self.where.setText(container_description(self.geo, self.container(), self.is_module()))
        self._fill_glue(self.glue_target())
        self._on_kind()

    def _fill_glue(self, keep: int | None) -> None:
        """Elementos a los que se puede pegar: los que están directamente dentro del contenedor elegido."""
        self._updating = True
        try:
            self.glue_box.clear()
            self.glue_box.addItem("nada (centro libre)", 0)
            cands = ge.children_of(self.geo, self.container())
            if keep and keep not in cands:
                cands.insert(0, keep)
            for kb in cands:
                self.glue_box.addItem(ge.element_name(self.geo, kb), kb)
            idx = self.glue_box.findData(keep) if keep else 0
            self.glue_box.setCurrentIndex(max(0, idx))
        finally:
            self._updating = False
        self._on_glue()

    def _on_glue(self) -> None:
        if self._updating:
            return
        glued = self.glue_target() is not None
        self.face_box.setEnabled(glued)
        self.btn_center.setEnabled(glued)
        for ed in (self.cx, self.cy, self.cz):
            ed.setEnabled(True)
        self._changed()

    def _on_kind(self) -> None:
        k = self.kind.currentData()
        form = self.form
        prism = k in PRISM_SIDES
        form.setRowVisible(self.radius, k in ("sphere", "cylinder"))
        form.setRowVisible(self.psize_row, prism)
        form.setRowVisible(self.height, k in ("cylinder", "slab") or prism)
        form.setRowVisible(self.sides_row, k == "box")
        form.setRowVisible(self.axis, k in ("cylinder", "slab") or prism)
        form.setRowVisible(self.angle, prism)
        form.setRowVisible(self.glue_row, k != "slab")
        if prism:
            self._fill_measures(PRISM_SIDES[k])
        label = form.labelForField(self.height)
        if label is not None:
            label.setText("Espesor (cm):" if k == "slab" else "Altura (cm):")
        self._changed()

    def _fill_measures(self, n: int) -> None:
        """Las medidas del polígono (sus nombres dependen de si tiene un número par de lados)."""
        keep = self.pmeasure.currentData() or "side"
        self.pmeasure.blockSignals(True)
        self.pmeasure.clear()
        for m in PRISM_MEASURES:
            self.pmeasure.addItem(prism_measure_name(n, m), m)
        self.pmeasure.setCurrentIndex(max(0, self.pmeasure.findData(keep)))
        self.pmeasure.blockSignals(False)

    def _update_glue_center(self) -> None:
        """Con «pegado a», la coordenada del eje de la cara se calcula para que toque esa cara."""
        target = self.glue_target()
        eds = (self.cx, self.cy, self.cz)
        if target is None or self.kind.currentData() == "slab":
            for ed in eds:
                ed.setEnabled(True)
            return
        axis, _sign = FACES[self.glue_face()]
        for i, ed in enumerate(eds):
            ed.setEnabled(i != axis)
            ed.setToolTip("Se calcula para que toque la cara elegida" if i == axis else "")
        prim = self._primitive_or_none()
        if prim is None:
            return
        try:
            value = ge.glue_center(prim, self._extent(target), self.glue_face())
        except EditError:
            return
        eds[axis].set_value(value)

    def _center_on_face(self) -> None:
        target = self.glue_target()
        if target is None:
            return
        lo, hi = self._extent(target)
        axis, _sign = FACES[self.glue_face()]
        for i, ed in enumerate((self.cx, self.cy, self.cz)):
            if i != axis:
                ed.set_value(ge._clean(0.5 * float(lo[i] + hi[i])))
        self._changed()

    def _on_link(self, link: str) -> None:
        if link.startswith("c:"):
            idx = self.container_box.findData(int(link[2:]))
            if idx >= 0:
                self.container_box.setCurrentIndex(idx)

    def _check(self) -> None:
        try:
            prim = self.primitive()
            self.material_number()
        except (EditError, ValueError, TypeError) as exc:
            self._set_status(False, html.escape(str(exc)))
            return
        cont = self.container()
        if prim.kind == "slab" and (ge.is_top_level(self.geo, cont) or self.geo.body(cont).is_module):
            self._set_status(False, "Una lámina es la parte de un <b>cuerpo</b> entre dos planos: elija como "
                                    "contenedor un cuerpo (no un módulo ni la envolvente).")
            return
        chk = check_placement(self.geo, self.session.locator, prim, cont, self.bounds)
        what = "el módulo" if self.is_module() else "el cuerpo"
        if chk.ok:
            msg = html.escape(chk.message)
            target = self.glue_target()
            if target is not None and prim.kind != "slab":
                face = html.escape(self.face_box.currentText())
                msg += f" Tocará la {face} de {html.escape(body_title(self.geo, target))}."
            self._set_status(True, msg)
            return
        if chk.offenders and all(f > 0 for f in chk.offenders.values()):
            msg = (f"No cabe entero en {html.escape(self._container_title(cont))}: {self._offenders_text(chk)}. "
                   "Tiene que quedar entero dentro del contenedor, porque PENGEOM no admite volúmenes que se solapen.")
        else:  # lo que no sale de las muestras (un cuerpo pequeño o fino dentro, fuera de la envolvente...)
            msg = f"No cabe en {html.escape(self._container_title(cont))}: {html.escape(chk.message)}"
        # ¿Dónde cabría?
        others = [kb for kb in self.chain if kb != cont]
        for kb in others:
            if check_placement(self.geo, self.session.locator, prim, kb, self.bounds, n=4000).ok:
                msg += (f"<br>Cabría dentro de {html.escape(self._container_title(kb))}: "
                        f"<a href='c:{kb}'>poner {what} ahí</a>.")
                break
        else:
            msg += "<br>No cabe en ninguno de los contenedores posibles: hágalo más pequeño o cambie el centro."
        self._set_status(False, msg)

    def _offenders_text(self, chk) -> str:
        """«el 17,5 % de su volumen caería en Cuerpo «2»» (o varios, de mayor a menor)."""
        def where(kb: int) -> str:
            if kb > self.geo.nbodys:
                return "fuera de la geometría"
            return html.escape(self._container_title(kb) if self.geo.enclosure_added and kb == self.geo.nbodys
                               else body_title(self.geo, kb))

        parts = sorted(chk.offenders.items(), key=lambda kv: -kv[1])
        if len(parts) == 1:
            kb, f = parts[0]
            return f"el {_pct(f)} de su volumen caería en {where(kb)}"
        detail = ", ".join(f"{_pct(f)} en {where(kb)}" for kb, f in parts[:3])
        return f"el {_pct(chk.outside_fraction)} de su volumen caería fuera ({detail})"

    def _set_status(self, ok: bool, message: str) -> None:
        self._ok = ok
        color = "#1a7f37" if ok else "#b42318"
        self.status.setText(f"<span style='color:{color}'>{'✔' if ok else '✖'} {message}</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def edit_fn(self):
        """La edición que añade el cuerpo o el módulo (la que hace ``MainWindow``)."""
        prim, material, name = self.primitive(), self.material_number(), self.body_name()
        module, cont, parinp = self.is_module(), self.container(), self.session.parinp
        return lambda doc, g: ge.add_primitive(doc, g, prim, material, cont, name, parinp=parinp, module=module)

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
        # Lo que se va a hacer, probado en una copia: si PENGEOM no lo aceptaría, se dice aquí (sin cerrar).
        from .geo_controller import trial_edit

        try:
            problem = trial_edit(self.session, self.edit_fn())
        except (EditError, ValueError) as exc:
            problem = str(exc)
        if problem:
            self._set_status(False, html.escape(problem))
            return
        super().accept()


def _pct(f: float) -> str:
    return f"{100 * f:.1f} %".replace(".", ",")


def _wrap(layout) -> QWidget:
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w


def _round(v: float, step: float) -> float:
    if step <= 0 or not math.isfinite(v):
        return v
    n = math.floor(abs(v) / step + 0.5)  # redondeo «de toda la vida» (no al par)
    return round(math.copysign(n * step, v), 12)


def combo_material(combo: QComboBox) -> int:
    """Número del material elegido o escrito en un desplegable editable («5», «5 · agua», «0 (vacío)»); EditError
    si no es uno que PENGEOM admita en un cuerpo (0 a 9998)."""
    text = combo.currentText().strip()
    idx = combo.findText(text)
    if idx >= 0 and combo.itemData(idx) is not None:
        number = int(combo.itemData(idx))
    else:
        m = re.match(r"(\d+)(\D|$)", text)
        if m is None:
            raise EditError("Escriba el número del material (0 = vacío), no su nombre.")
        number = int(m.group(1))
    ge.check_material(number)
    return number


def new_material_number(used) -> int | None:
    """El número para un material nuevo: el que sigue al mayor o, si ya se ha llegado a 9998, el primero libre
    (9999 es la marca de la caja del TAC)."""
    nums = {int(m) for m in used if 0 < int(m) <= ge.MAX_BODY_MATERIAL}
    n = max(nums, default=0) + 1
    if n <= ge.MAX_BODY_MATERIAL:
        return n
    return next((k for k in range(1, ge.MAX_BODY_MATERIAL + 1) if k not in nums), None)
