"""Diálogos «Añadir un detector» y «Añadir una celda de detectores».

Un detector es un cuerpo con una de las formas habituales (cuadrado,
rectángulo, círculo, triángulo, pentágono o hexágono, con su altura) que se
añade a la geometría y, en el mismo paso de deshacer, a un detector de
penhmain del .in (energía depositada, ENDETC, o impacto, IMPDET). Una celda
es una caja (un módulo) llena de detectores iguales en filas, columnas y
capas (:mod:`argus.pengeom.detcell`).

penhmain admite 5 detectores de cada tipo, de hasta 100 cuerpos los de
energía y 50 los de impacto: los diálogos no dejan pasar de ahí. Además, la
energía media depositada en cada cuerpo sale siempre en penhmain-res.dat.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..pengeom import detcell as dc
from ..pengeom import edit as ge
from ..pengeom.edit import PRISM_MEASURES, PRISM_SIDES, EditError, check_placement, prism_measure_name, prism_radius
from ..penhan import sections as sc
from ..render.slice2d import nice_length
from .add_body_dialog import AddBodyDialog, _round, combo_material, new_material_number
from .sim_widgets import BinsEdit, row
from .source_panel import EnergyEdit, NumberEdit, parse_number

DETECTOR_KINDS = {"energy": "Energía depositada (ENDETC)", "impact": "Impacto (IMPDET)",
                  "none": "Ninguno: solo el cuerpo"}
KIND_NAMES = {"energy": "de energía", "impact": "de impacto"}


def existing_detectors(doc, kind: str) -> list:
    """Los detectores de ``kind`` que ya tiene el .in."""
    if doc is None or kind not in ("energy", "impact"):
        return []
    try:
        return sc.energy_detectors(doc) if kind == "energy" else sc.impact_detectors(doc)
    except Exception:  # noqa: BLE001 - un .in que no se entiende: como si no tuviera
        return []


@dataclass
class DetectorChoice:
    """Qué hacer en el .in con el cuerpo (o los cuerpos) nuevos."""

    kind: str  # 'energy', 'impact' o 'none'
    target: int = 0  # un detector: el que ya existe (1, 2…) o 0 = uno nuevo
    grouping: str = "cell"  # una celda: cómo se reparten sus detectores (dc.GROUPINGS)
    el: float = 0.0
    eu: float = 0.0
    nbe: int = 100


def unreadable_line(doc, kind: str):
    """La primera línea de los detectores de ``kind`` que penhmain no podría leer (o None): al escribir los
    detectores de nuevo se perdería lo que tiene escrito."""
    if doc is None or kind not in ("energy", "impact"):
        return None
    keys = sc.ENERGY_KEYWORDS if kind == "energy" else sc.IMPACT_KEYWORDS
    return next((ln for ln in doc.lines if ln.kw.strip() in keys and (ln.error or ln.hang)), None)


def unreadable_text(doc, ln) -> str:
    row = doc.row(ln)
    where = f"la línea {row + 1}" if row is not None else "una línea"
    return (f"En el .in, {where} ({ln.kw.strip()}) tiene un error ({ln.error or 'penhmain se quedaría esperando'}): "
            "corríjala antes de añadir detectores de ese tipo (se escribirían de nuevo y se perdería lo que tiene).")


def apply_choice(doc, choice: DetectorChoice, groups: list[list[str]]) -> int:
    """Pone en el .in los cuerpos (etiquetas de 4 caracteres) de cada grupo: cada grupo, en un detector nuevo, o,
    con un solo grupo y ``choice.target``, en ese detector. Devuelve cuántos detectores nuevos ha creado.
    ``ValueError`` si penhmain no lo admitiría o si hay líneas de esos detectores que no se pueden leer (el
    documento no cambia)."""
    if choice.kind == "none" or not groups:
        return 0
    bad = unreadable_line(doc, choice.kind)
    if bad is not None:
        raise ValueError(unreadable_text(doc, bad))
    dets = existing_detectors(doc, choice.kind)
    created = 0
    for i, labels in enumerate(groups):
        refs = [sc.BodyRef(lab, None) for lab in labels]
        if choice.target and i == 0 and len(groups) == 1:
            dets[choice.target - 1].bodies.extend(refs)
            continue
        if choice.kind == "energy":
            det = sc.EnergyDetector(float(choice.el), float(choice.eu), int(choice.nbe))
        else:
            det = sc.ImpactDetector(float(choice.el), float(choice.eu), int(choice.nbe), 0, 1)
        det.bodies = refs
        dets.append(det)
        created += 1
    max_det, max_bodies = dc.DETECTOR_LIMITS[choice.kind]
    if len(dets) > max_det:
        raise ValueError(f"penhmain admite como máximo {max_det} detectores {KIND_NAMES[choice.kind]}.")
    if any(len(d.bodies) > max_bodies for d in dets):
        raise ValueError(f"Un detector {KIND_NAMES[choice.kind]} admite como máximo {max_bodies} cuerpos.")
    (sc.set_energy_detectors if choice.kind == "energy" else sc.set_impact_detectors)(doc, dets)
    return created


class DetectorOptions(QWidget):
    """Tipo de detector de penhmain, en cuál (o cómo se agrupan los de una celda) y su ventana de energía."""

    changed = Signal()

    def __init__(self, input_doc, *, cell: bool, parent=None):
        super().__init__(parent)
        self.doc = input_doc
        self.cell = cell
        self._grouping_auto = True
        self._before_text: str | None = None  # el .in sin los detectores nuevos, y sus errores
        self._before: set = set()
        box = QGroupBox("Detector de penhmain")
        form = QFormLayout(box)
        self.form = form
        self.kind = QComboBox()
        for key, text in DETECTOR_KINDS.items():
            self.kind.addItem(text, key)
        form.addRow("Tipo:", self.kind)
        self.target = QComboBox()
        form.addRow("Cuáles:" if cell else "En:", self.target)
        self.el = EnergyEdit(minimum=0.0, width=96)
        self.eu = EnergyEdit(minimum=0.0, width=96)
        self.el.set_value(0.0)
        self.eu.set_value(0.0)
        self.eu.setToolTip("0: la energía máxima de la fuente (penhmain la pone si es menor de 50 eV)")
        self.energy_row = row(self.el, "a", self.eu)
        form.addRow("Energía:", self.energy_row)
        self.bins = BinsEdit()
        self.bins.set_value(100)
        form.addRow("Intervalos:", self.bins)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #555;")
        form.addRow(self.note)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(box)
        if input_doc is None:
            self.kind.setCurrentIndex(self.kind.findData("none"))
            self.kind.setEnabled(False)
        self.kind.currentIndexChanged.connect(lambda _i: self._on_kind())
        self.target.currentIndexChanged.connect(lambda _i: self._on_target())
        for ed in (self.el, self.eu):
            ed.valueEdited.connect(lambda _v: self.changed.emit())
            ed.textEdited.connect(lambda _t: self.changed.emit())
        self.bins.valueEdited.connect(lambda _n: self.changed.emit())
        self._fill_targets()

    def kind_key(self) -> str:
        return str(self.kind.currentData())

    def existing(self) -> list:
        return existing_detectors(self.doc, self.kind_key())

    def _on_kind(self) -> None:
        self._fill_targets()
        self.changed.emit()

    def _on_target(self) -> None:
        if self.cell:
            self._grouping_auto = False
        self._update_rows()
        self.changed.emit()

    def _fill_targets(self) -> None:
        kind = self.kind_key()
        keep = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        if self.cell:
            for key, text in dc.GROUPINGS.items():
                if key != "none":
                    self.target.addItem(text, key)
            idx = self.target.findData(keep)
            self.target.setCurrentIndex(max(0, idx))
        elif kind != "none":
            dets = self.existing()
            max_det, max_bodies = dc.DETECTOR_LIMITS[kind]
            self.target.addItem("Uno nuevo" if len(dets) < max_det else f"Uno nuevo (ya hay {max_det})", 0)
            if len(dets) >= max_det:
                self.target.model().item(0).setEnabled(False)
            for i, d in enumerate(dets, start=1):
                full = len(d.bodies) >= max_bodies
                self.target.addItem(f"El detector {KIND_NAMES[kind]} {i} ({len(d.bodies)} cuerpos"
                                    + (", lleno)" if full else ")"), i)
                if full:
                    self.target.model().item(self.target.count() - 1).setEnabled(False)
            first = next((i for i in range(self.target.count()) if self.target.model().item(i).isEnabled()), 0)
            self.target.setCurrentIndex(first)
        self.target.blockSignals(False)
        self._update_rows()

    def _update_rows(self) -> None:
        kind = self.kind_key()
        new = self.cell or self.target.currentData() == 0
        on = kind != "none"
        self.form.setRowVisible(self.target, on)
        self.form.setRowVisible(self.energy_row, on and new)
        self.form.setRowVisible(self.bins, on and new)
        if self.doc is None:
            self.note.setText("Para añadirlo a un detector de penhmain hace falta un .in que use esta geometría: "
                              "ábralo o créelo (Archivo → Nuevo .in).")
        elif on:
            self.note.setText("La energía media depositada en cada cuerpo sale además en penhmain-res.dat. penhmain "
                              "admite 5 detectores de cada tipo, de hasta 100 cuerpos los de energía y 50 los de "
                              "impacto.")
        else:
            self.note.setText("")

    def set_grouping(self, key: str) -> None:
        """Agrupación propuesta (solo si el usuario no ha elegido otra)."""
        if not self.cell or not self._grouping_auto:
            return
        idx = self.target.findData(key)
        if idx >= 0 and idx != self.target.currentIndex():
            self.target.blockSignals(True)
            self.target.setCurrentIndex(idx)
            self.target.blockSignals(False)

    @staticmethod
    def _energy(ed: EnergyEdit) -> float:
        """Lo escrito (aunque no se haya confirmado con Intro), o lo último confirmado."""
        try:
            return float(ed.parse(ed.text()))
        except ValueError:
            return float(ed.value() or 0.0)

    def choice(self) -> DetectorChoice:
        kind = self.kind_key()
        if kind == "none":
            return DetectorChoice("none")
        el, eu = self._energy(self.el), self._energy(self.eu)
        nbe = self.bins.value()
        if self.cell:
            return DetectorChoice(kind, 0, str(self.target.currentData()), float(el), float(eu), int(nbe))
        return DetectorChoice(kind, int(self.target.currentData() or 0), "cell", float(el), float(eu), int(nbe))

    def problem(self, plan: dc.CellPlan | None = None) -> str | None:
        """Por qué no se puede (o None)."""
        kind = self.kind_key()
        if kind == "none":
            return None
        bad = unreadable_line(self.doc, kind)
        if bad is not None:
            return unreadable_text(self.doc, bad)
        if self.cell:
            if plan is None or not plan.ok:
                return None
            grouping = str(self.target.currentData())
            if grouping == "layers" and plan.spec.layers < 2:
                return "La celda tiene una sola capa: elija otra forma de agrupar los detectores."
            problem = dc.detector_problem(plan, grouping, kind, len(self.existing()))
            if problem:
                return problem
            count = len(dc.detector_groups(plan, grouping))
        else:
            idx = self.target.currentIndex()
            if idx < 0 or not self.target.model().item(idx).isEnabled():
                max_det, _ = dc.DETECTOR_LIMITS[kind]
                return f"penhmain admite {max_det} detectores {KIND_NAMES[kind]} y el .in ya los tiene todos llenos."
            count = 1
        return self._new_problems(count)

    def _new_problems(self, count: int) -> str | None:
        """Lo que penhmain diría del .in con los detectores nuevos (``count`` grupos), probado en una copia con las
        comprobaciones de siempre (las de PMRDR): la ventana de energía como la calcula penhmain (con 0, la energía
        máxima de la fuente), los intervalos, detectores de energía con forzado de interacciones o splitting...
        Solo los errores que el .in no tenía ya."""
        from ..penhan.inputfile import parse_in_text
        from ..penhan.simcheck import check_sections

        doc = self.doc
        folder = Path(doc.path).parent if doc.path else None
        text = doc.text()
        if self._before_text != text:
            self._before = {(dg.code, dg.message) for dg in check_sections(doc, None, folder).errors}
            self._before_text = text
        trial = parse_in_text(text, doc.path)
        try:
            apply_choice(trial, self.choice(), [[f"{9999 - i:4d}"] for i in range(count)])
        except ValueError as exc:
            return str(exc)
        for dg in check_sections(trial, None, folder).errors:
            if (dg.code, dg.message) not in self._before:
                return dg.message
        return None


# ---------------------------------------------------------------------------
# Un detector
# ---------------------------------------------------------------------------

class DetectorDialog(AddBodyDialog):
    """«Añadir un detector»: un cuerpo de una forma habitual y, en el .in, su detector de penhmain."""

    def __init__(self, parent, session, container: int, center, material_names: dict[int, str] | None = None,
                 material: int | None = None, *, input_doc=None, extent_fn=None, glue_target: int | None = None,
                 glue_face: str | None = None):
        self._options: DetectorOptions | None = None
        super().__init__(parent, session, container, center, material_names, material, module=False,
                         glue_target=glue_target, glue_face=glue_face, extent_fn=extent_fn)
        self.form.setRowVisible(self.mode_row, False)
        self.kind.blockSignals(True)
        self.kind.clear()
        for key, text in dc.CELL_SHAPES.items():
            self.kind.addItem(text, key)
        self.kind.setCurrentIndex(0)
        self.kind.blockSignals(False)
        self._options = DetectorOptions(input_doc, cell=False)
        lay = self.layout()
        lay.insertWidget(lay.indexOf(self.status), self._options)
        self._options.changed.connect(self._changed)
        if material is None or material == 0:  # un detector suele ser de un material propio
            idx = next((i for i in range(self.material.count()) if "nuevo" in self.material.itemText(i)), -1)
            if idx >= 0:
                self.material.setCurrentIndex(idx)
        self._defaults()
        self._on_kind()
        self.setWindowTitle("Añadir un detector")

    @property
    def options(self) -> DetectorOptions:
        return self._options

    def _on_mode(self) -> None:
        super()._on_mode()
        self.setWindowTitle("Añadir un detector")

    def detector_choice(self) -> DetectorChoice:
        return self._options.choice()

    def _check(self) -> None:
        super()._check()
        if not self._ok or self._options is None:
            return
        if self._options.kind_key() != "none":
            try:
                if self.material_number() == 0:
                    self._set_status(False, "Un cuerpo de vacío (material 0) no puede formar parte de un detector de "
                                            "penhmain: elija su material.")
                    return
            except (EditError, ValueError):
                return
        problem = self._options.problem()
        if problem:
            self._set_status(False, html.escape(problem))


# ---------------------------------------------------------------------------
# Una celda de detectores
# ---------------------------------------------------------------------------

LAYOUT_NAMES = {"prism6": "En panal", "cylinder": "Al tresbolillo", "prism3": "Alternos, sin huecos",
                "prism4": "Como ladrillos", "box": "Como ladrillos", "prism5": "Como ladrillos"}


class CellPreview(QWidget):
    """Una capa de la celda vista a lo largo del eje de los detectores (u a la derecha, v arriba)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plan: dc.CellPlan | None = None
        self.setMinimumSize(300, 260)

    def set_plan(self, plan: dc.CellPlan | None) -> None:
        self.plan = plan
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802 - nombre de Qt
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.palette().base())
        plan = self.plan
        if plan is None or not plan.ok or plan.housing is None:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sin vista previa")
            return
        spec = plan.spec
        a, u, v = spec.axes()
        hs = plan.housing
        c = np.asarray(hs.center, dtype=float)
        k = "xyz".index(spec.axis)
        su, sv = float(hs.sides[(k + 1) % 3]), float(hs.sides[(k + 2) % 3])
        pad = 24.0
        w, h = max(self.width() - 2 * pad, 10.0), max(self.height() - 2 * pad, 10.0)
        scale = min(w / max(su, 1e-12), h / max(sv, 1e-12))
        ox = pad + 0.5 * (w - su * scale)
        oy = pad + 0.5 * (h - sv * scale)

        def to_screen(du: float, dv: float) -> QPointF:
            return QPointF(ox + (du + 0.5 * su) * scale, oy + (0.5 * sv - dv) * scale)

        p.setPen(QPen(QColor("#666"), 1))
        p.setBrush(QBrush(QColor("#e8e8e8")))
        p.drawRect(QRectF(to_screen(-0.5 * su, 0.5 * sv), to_screen(0.5 * su, -0.5 * sv)))
        first = [e for e in plan.elements if e.layer == 1]
        p.setPen(QPen(QColor("#1f4e79"), 1))
        p.setBrush(QBrush(QColor("#9ecae1")))
        step = max(1, len(first) // 4000)
        for e in first[::step]:
            prim = e.prim
            d = np.asarray(prim.center, dtype=float) - c
            du, dv = float(d @ u), float(d @ v)
            if prim.kind == "cylinder":
                r = float(prim.radius) * scale
                ctr = to_screen(du, dv)
                p.drawEllipse(ctr, r, r)
            elif prim.kind == "box":
                hu, hv = 0.5 * float(prim.sides[(k + 1) % 3]), 0.5 * float(prim.sides[(k + 2) % 3])
                p.drawRect(QRectF(to_screen(du - hu, dv + hv), to_screen(du + hu, dv - hv)))
            else:
                poly = QPolygonF([to_screen(du + float(x), dv + float(y)) for x, y in prim.vertices_2d()])
                p.drawPolygon(poly)
        names = {"x": ("y", "z"), "y": ("z", "x"), "z": ("x", "y")}[spec.axis]
        p.setPen(QColor("#444"))
        p.drawText(QRectF(0, self.height() - pad + 4, self.width(), pad - 4), Qt.AlignmentFlag.AlignHCenter,
                   f"{names[0]} →   (vista a lo largo de {spec.axis}; capa 1)")
        p.save()
        p.translate(12, self.height() / 2)
        p.rotate(-90)
        p.drawText(QRectF(-80, -10, 160, 20), Qt.AlignmentFlag.AlignCenter, f"{names[1]} →")
        p.restore()


class CellDialog(QDialog):
    """«Añadir una celda de detectores»: forma y tamaño de cada detector, filas, columnas y capas, huecos, caja,
    dónde va, materiales y los detectores de penhmain."""

    def __init__(self, parent, session, container: int, center, material_names: dict[int, str] | None = None,
                 material: int | None = None, *, input_doc=None):
        super().__init__(parent)
        self.setWindowTitle("Añadir una celda de detectores")
        self.session = session
        self.geo = session.geo
        self._ok = False
        self._plan: dc.CellPlan | None = None
        lo, hi = session.bounds
        self.bounds = (np.asarray(lo, dtype=float), np.asarray(hi, dtype=float))
        self.chain = ge.container_chain(self.geo, container) or [container]
        outer = QHBoxLayout(self)
        left = QVBoxLayout()
        outer.addLayout(left, 3)
        form = QFormLayout()
        self.form = form
        left.addLayout(form)
        # Cada detector.
        self.shape = QComboBox()
        for key, text in dc.CELL_SHAPES.items():
            self.shape.addItem(text, key)
        self.shape.setCurrentIndex(self.shape.findData("prism6"))
        form.addRow("Forma:", self.shape)
        self.measure = QComboBox()
        self.size = NumberEdit(minimum=1e-12, width=80)
        self.size_row = row(self.measure, self.size, "cm")
        form.addRow("Tamaño:", self.size_row)
        self.radius = NumberEdit(minimum=1e-12, width=80)
        form.addRow("Radio (cm):", self.radius)
        self.su, self.sv = NumberEdit(minimum=1e-12, width=70), NumberEdit(minimum=1e-12, width=70)
        self.sides_row = row(self.su, "×", self.sv, "cm")
        form.addRow("Lados:", self.sides_row)
        self.height = NumberEdit(minimum=1e-12, width=80)
        form.addRow("Altura (cm):", self.height)
        self.axis = QComboBox()
        for a in "xyz":
            self.axis.addItem(f"eje {a}", a)
        self.axis.setCurrentIndex(2)
        form.addRow("Orientación:", self.axis)
        # Cuántos y cómo.
        self.rows, self.cols, self.layers = (QSpinBox() for _ in range(3))
        for sp, top in ((self.rows, dc.MAX_ROWS), (self.cols, dc.MAX_ROWS), (self.layers, dc.MAX_LAYERS)):
            sp.setRange(1, top)
            sp.setKeyboardTracking(False)
        self.rows.setValue(4)
        self.cols.setValue(4)
        self.layers.setValue(1)
        form.addRow("Filas × columnas × capas:", row(self.rows, "×", self.cols, "×", self.layers))
        self.layout_box = QComboBox()
        self.layout_box.addItem("En cuadrícula", "grid")
        self.layout_box.addItem("Filas desplazadas", "offset")
        form.addRow("Disposición:", self.layout_box)
        self.gap = NumberEdit(minimum=0.0, width=70)
        self.layer_gap = NumberEdit(minimum=0.0, width=70)
        form.addRow("Hueco entre detectores:", row(self.gap, "cm; entre capas", self.layer_gap, "cm"))
        self.margin = NumberEdit(minimum=0.0, width=70)
        form.addRow("Pared de la caja:", row(self.margin, "cm"))
        # Dónde.
        self.container_box = QComboBox()
        for kb in self.chain:
            self.container_box.addItem(self._container_title(kb), kb)
        form.addRow("Dentro de:", self.container_box)
        self.cx, self.cy, self.cz = (NumberEdit(width=80) for _ in range(3))
        form.addRow("Centro:", row("x", self.cx, "y", self.cy, "z", self.cz, "cm"))
        # Materiales y nombre.
        self.mat_det = self._material_combo(material_names, None, new=True)
        form.addRow("Material de los detectores:", self.mat_det)
        self.mat_box = self._material_combo(material_names, material, new=False)
        form.addRow("Material de la caja:", self.mat_box)
        self.name = QLineEdit()
        self.name.setPlaceholderText("opcional (se usa en la descripción de cada detector en el .geo)")
        form.addRow("Nombre:", self.name)
        self.options = DetectorOptions(input_doc, cell=True)
        left.addWidget(self.options)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setMinimumWidth(420)
        self.status.linkActivated.connect(self._on_link)
        left.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        left.addWidget(self.buttons)
        right = QVBoxLayout()
        outer.addLayout(right, 2)
        self.preview = CellPreview()
        right.addWidget(self.preview, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #333;")
        right.addWidget(self.summary)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._update)
        for ed in (self.size, self.radius, self.su, self.sv, self.height, self.gap, self.layer_gap, self.margin,
                   self.cx, self.cy, self.cz):
            ed.valueEdited.connect(lambda _v: self._changed())
            ed.textEdited.connect(lambda _t: self._changed())
        for sp in (self.rows, self.cols, self.layers):
            sp.valueChanged.connect(lambda _v: self._changed())
        for cb in (self.measure, self.axis, self.layout_box, self.container_box):
            cb.currentIndexChanged.connect(lambda _i: self._changed())
        for cb in (self.mat_det, self.mat_box):
            cb.editTextChanged.connect(lambda _t: self._changed())
        self.shape.currentIndexChanged.connect(lambda _i: self._on_shape())
        self.options.changed.connect(self._changed)

        step = 10.0 ** math.floor(math.log10(max(float(np.max(self.bounds[1] - self.bounds[0])), 1e-9) / 1e5))
        for ed, val in zip((self.cx, self.cy, self.cz), center):
            ed.set_value(_round(float(val), step))
        self.gap.set_value(0.0)
        self.layer_gap.set_value(0.0)
        self.margin.set_value(0.0)
        self._defaults()
        self._on_shape()

    # -- valores -------------------------------------------------------------------------
    def _container_title(self, kb: int) -> str:
        if self.geo.enclosure_added and kb == self.geo.nbodys:
            return "La envolvente (fuera de todo lo demás)"
        if kb > self.geo.nbodys:
            return "El nivel superior (al lado de lo que hay, fuera de todo)"
        return ge.element_name(self.geo, kb)

    def _material_combo(self, names: dict[int, str] | None, material: int | None, new: bool) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        names = names or {}
        used = sorted(set(self.geo.materials()) | set(names))
        for m in used:
            combo.addItem(f"{m}" + (f" · {names[m]}" if m in names else ""), m)
        nxt = new_material_number(used)
        if nxt is not None:
            combo.addItem(f"{nxt} (material nuevo)", nxt)
        combo.addItem("0 (vacío)", 0)
        if new and nxt is not None:
            combo.setCurrentIndex(combo.findData(nxt))
        elif material is not None:
            idx = combo.findData(material)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        return combo

    @staticmethod
    def _num(ed: NumberEdit) -> float | None:
        try:
            return parse_number(ed.text())
        except ValueError:
            return None

    def container(self) -> int:
        return int(self.container_box.currentData())

    def spec(self) -> dc.CellSpec:
        """Lo escrito (aunque no se haya confirmado con Intro); EditError si falta algo."""
        shape = str(self.shape.currentData())
        c = [self._num(ed) for ed in (self.cx, self.cy, self.cz)]
        if any(x is None for x in c):
            raise EditError("Falta el centro (o no es un número).")
        height = self._num(self.height)
        if height is None:
            raise EditError("Falta la altura de los detectores.")
        size = 1.0
        sides = (1.0, 1.0)
        if shape in PRISM_SIDES:
            val = self._num(self.size)
            if val is None or not val > 0:
                raise EditError("Falta el tamaño de los detectores (o no es un número positivo).")
            size = prism_radius(PRISM_SIDES[shape], str(self.measure.currentData() or "side"), val)
        elif shape == "cylinder":
            val = self._num(self.radius)
            if val is None:
                raise EditError("Falta el radio de los detectores.")
            size = val
        else:
            a, b = self._num(self.su), self._num(self.sv)
            if a is None or b is None:
                raise EditError("Faltan los lados de los detectores.")
            sides = (a, b)
        gap, lgap, margin = (self._num(ed) for ed in (self.gap, self.layer_gap, self.margin))
        if gap is None or lgap is None or margin is None:
            raise EditError("Faltan los huecos o la pared de la caja (0 si no hay).")
        return dc.CellSpec(shape=shape, size=float(size), sides=(float(sides[0]), float(sides[1])),
                           height=float(height), axis=str(self.axis.currentData()), rows=self.rows.value(),
                           cols=self.cols.value(), layers=self.layers.value(), gap=float(gap), layer_gap=float(lgap),
                           layout=str(self.layout_box.currentData()), margin=float(margin),
                           center=(float(c[0]), float(c[1]), float(c[2])))

    def plan(self) -> dc.CellPlan:
        """El plan de lo escrito (el mismo objeto mientras no cambie: con muchos detectores tarda)."""
        spec = self.spec()
        if self._plan is None or self._plan.spec != spec:
            self._plan = dc.plan_cell(spec)
        return self._plan

    def element_material(self) -> int:
        return combo_material(self.mat_det)

    def housing_material(self) -> int:
        return combo_material(self.mat_box)

    def cell_name(self) -> str:
        return self.name.text().strip()

    def detector_choice(self) -> DetectorChoice:
        return self.options.choice()

    # -- interfaz ------------------------------------------------------------------------------
    def _defaults(self) -> None:
        """Un tamaño redondo con el que la celda quepa en el contenedor (si es posible)."""
        lo, hi = self.bounds
        size = nice_length(0.05 * float(np.max(hi - lo)) or 1.0)
        for _ in range(12):
            self.size.set_value(size)
            self.radius.set_value(size / 2)
            self.su.set_value(size)
            self.sv.set_value(size)
            self.height.set_value(size)
            try:
                plan = self.plan()
            except EditError:
                return
            if plan.ok and check_placement(self.geo, self.session.locator, plan.housing, self.container(),
                                           self.bounds, n=3000).ok:
                return
            size = nice_length(size / 2.5)

    def _on_shape(self) -> None:
        shape = str(self.shape.currentData())
        prism = shape in PRISM_SIDES
        self.form.setRowVisible(self.size_row, prism)
        self.form.setRowVisible(self.radius, shape == "cylinder")
        self.form.setRowVisible(self.sides_row, shape == "box")
        if prism:
            n = PRISM_SIDES[shape]
            keep = self.measure.currentData() or "side"
            self.measure.blockSignals(True)
            self.measure.clear()
            for m in PRISM_MEASURES:
                self.measure.addItem(prism_measure_name(n, m), m)
            self.measure.setCurrentIndex(max(0, self.measure.findData(keep)))
            self.measure.blockSignals(False)
        self.layout_box.setItemText(1, LAYOUT_NAMES.get(shape, "Filas desplazadas"))
        self._changed()

    def _changed(self) -> None:
        self._timer.start()

    def _update(self) -> None:
        try:
            plan = self.plan()
        except (EditError, ValueError) as exc:
            self._plan = None
            self.preview.set_plan(None)
            self.summary.setText("")
            self._set_status(False, html.escape(str(exc)))
            return
        self.preview.set_plan(plan)
        if not plan.ok:
            self.summary.setText("")
            self._set_status(False, html.escape(plan.problems[0]))
            return
        kind = self.options.kind_key()
        if kind != "none":
            self.options.set_grouping(dc.suggest_grouping(plan, kind, len(self.options.existing())))
        size = " × ".join(f"{_short(v)}" for v in plan.size())
        text = (f"{len(plan.elements)} detectores ({plan.spec.rows} × {plan.spec.cols}"
                + (f" × {plan.spec.layers}" if plan.spec.layers > 1 else "") + f"); caja de {size} cm; "
                f"{plan.surfaces} superficies y {plan.modules} módulo{'s' if plan.modules != 1 else ''} nuevos.")
        if plan.notes:
            text += " " + " ".join(plan.notes)
        self.summary.setText(text)
        try:
            mat_det, _mat_box = self.element_material(), self.housing_material()
        except (EditError, ValueError) as exc:
            self._set_status(False, html.escape(str(exc)))
            return
        if mat_det == 0:
            self._set_status(False, "Los detectores no pueden ser de vacío (material 0): elija su material.")
            return
        problem = dc.total_problem(self.geo, plan) or self.options.problem(plan)
        if problem:
            self._set_status(False, html.escape(problem))
            return
        chk = check_placement(self.geo, self.session.locator, plan.housing, self.container(), self.bounds)
        if not chk.ok:
            msg = f"La caja no cabe en {html.escape(self._container_title(self.container()))}: " \
                  f"{html.escape(chk.message)}"
            for kb in self.chain:
                if kb != self.container() and check_placement(self.geo, self.session.locator, plan.housing, kb,
                                                              self.bounds, n=4000).ok:
                    msg += (f"<br>Cabría dentro de {html.escape(self._container_title(kb))}: "
                            f"<a href='c:{kb}'>ponerla ahí</a>.")
                    break
            self._set_status(False, msg)
            return
        self._set_status(True, "La celda cabe entera en el contenedor.")

    def _on_link(self, link: str) -> None:
        if link.startswith("c:"):
            idx = self.container_box.findData(int(link[2:]))
            if idx >= 0:
                self.container_box.setCurrentIndex(idx)

    def _set_status(self, ok: bool, message: str) -> None:
        self._ok = ok
        color = "#1a7f37" if ok else "#b42318"
        self.status.setText(f"<span style='color:{color}'>{'✔' if ok else '✖'} {message}</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def accept(self) -> None:
        self._timer.stop()
        self._update()
        if self._ok:
            super().accept()


def _short(v: float) -> str:
    return f"{float(v):.4g}".replace(".", ",")


__all__ = ["CellDialog", "CellPreview", "DETECTOR_KINDS", "DetectorChoice", "DetectorDialog", "DetectorOptions",
           "apply_choice", "existing_detectors"]
