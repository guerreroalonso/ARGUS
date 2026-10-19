"""Secciones del panel «Simulación» sobre lo que registra penhmain y el trabajo.

Detectores de impacto, angulares y de energía depositada; distribución de
dosis; proceso externo (EPROF) y propiedades del trabajo (partículas, tiempo,
semillas, volcados). Cada sección lee sus datos del .in con
``penhan.sections`` y emite los cambios como ``apply(texto, fn)``.
"""

from __future__ import annotations

import html
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import fmt_count, fmt_int, fmt_num, plural
from ..penhan import sections as sc
from ..penhan.batch import MAX_SEED_PAIR
from ..penhan.dumpfile import read_dump, source_differences, tally_diffs
from .sim_widgets import (
    MAX_TIME,
    BinsEdit,
    BodyChecklist,
    DurationEdit,
    NameEdit,
    compact_combo,
    hint,
    resolve_ref,
    row,
)
from .source_panel import EnergyEdit, NumberEdit, format_energy

PARTICLES = [(1, "e−"), (2, "γ"), (3, "e+"), (4, "p"), (5, "α"), (6, "n")]
# Semillas de RANECU (rita.f, FUNCTION RAND): de 1 a sus módulos menos 1. Pares precalculados de RAND0: 1001.
SEED1_MAX = 2147483562
SEED2_MAX = 2147483398
PARTICLE_NAMES = {1: "electrones", 2: "fotones", 3: "positrones", 4: "protones", 5: "alfas", 6: "neutrones"}
IDCUT_TEXT = {0: "se absorben al entrar (IDCUT 0)", 1: "siguen su camino (IDCUT 1)",
              2: "siguen y se registra su fluencia (IDCUT 2)"}


class Section(QWidget):
    """Una sección del panel: lee del .in y emite los cambios."""

    apply = Signal(str, object)  # (texto para deshacer, fn(doc))
    highlight = Signal(object)  # cuerpos que resaltar en las vistas (set de índices)
    title = ""
    keywords: set[str] = set()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.geo = None
        self.folder = None
        self._updating = False

    def edit(self, text: str, fn) -> None:
        if not self._updating and self.doc is not None:
            self.apply.emit(text, fn)

    def set_context(self, doc, geo, folder) -> None:
        self.doc, self.geo, self.folder = doc, geo, folder
        self._updating = True
        try:
            self.refresh()
        finally:
            self._updating = False

    def refresh(self) -> None:  # pragma: no cover - lo implementa cada sección
        pass

    def summary(self) -> str:
        return ""

    def highlighted(self) -> set[int]:
        return set()


def _bytes(n: float) -> str:
    """«4,15 MB»; se redondea a tres cifras antes de elegir la unidad («1 MB», no «1e+03 kB»)."""
    v = float(f"{float(n):.3g}")
    for unit, f in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if v >= f:
            return f"{fmt_num(v / f, 3)} {unit}"
    return f"{int(v)} B"


def _mark_window(el_edit, eu_edit, el: float, eu: float, lo_min: float, why: str) -> None:
    """Marca en rojo una ventana de energía vacía: con EL subido a ``lo_min`` (como penhmain), EU no pasa de EL + 1 eV.
    Con EU < 50 eV (la energía máxima de la fuente) no se sabe aquí: lo dice la comprobación del .in."""
    lo = max(float(el), float(lo_min))
    bad = float(eu) >= 50.0 and float(eu) < lo + 1.0
    text = f"La ventana de energía está vacía (EU tiene que pasar de EL): {why}" if bad else None
    el_edit.set_problem(text)
    eu_edit.set_problem(text)


def _window_text(el: float, eu: float, auto_upper: str = "E máx. de la fuente") -> str:
    lo = format_energy(el) if el > 0 else "0"
    hi = format_energy(eu) if eu >= 50.0 else auto_upper
    return f"{lo} – {hi}"


# ---------------------------------------------------------------------------
# Detectores de impacto
# ---------------------------------------------------------------------------

class _DetectorSection(Section):
    """Base de las secciones de detectores: selector, añadir y quitar."""

    kind_name = "detector"
    blank_search = True  # una etiqueta en blanco es la del último cuerpo sin etiqueta (no en IDBODY)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dets: list = []
        self.current = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        top = QHBoxLayout()
        self.selector = compact_combo(QComboBox(), 12)
        self.selector.currentIndexChanged.connect(self._on_select)
        top.addWidget(self.selector, 1)
        self.btn_add = QPushButton("Añadir")
        self.btn_add.clicked.connect(self._add)
        self.btn_remove = QPushButton("Quitar")
        self.btn_remove.clicked.connect(self._remove)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_remove)
        lay.addLayout(top)
        self.form_box = QWidget()
        lay.addWidget(self.form_box)
        self.empty = hint("")
        lay.addWidget(self.empty)
        self.body = lay

    # A implementar: read(doc) -> list, write(doc, list), new_detector(), label(i, det), fill(det), intro text
    def refresh(self) -> None:
        self.dets = self.read(self.doc) if self.doc is not None else []
        self.selector.blockSignals(True)
        self.selector.clear()
        for i, d in enumerate(self.dets, start=1):
            self.selector.addItem(self.label(i, d))
        self.current = min(self.current, max(len(self.dets) - 1, 0))
        if self.dets:
            self.selector.setCurrentIndex(self.current)
        self.selector.blockSignals(False)
        has = bool(self.dets)
        self.selector.setVisible(has)
        self.form_box.setVisible(has)
        self.btn_remove.setEnabled(has)
        self.btn_add.setEnabled(self.doc is not None and len(self.dets) < sc.MAX_DETECTORS)
        self.empty.setText(self.intro())
        if has:
            self.fill(self.dets[self.current])
        self.highlight.emit(self.highlighted())

    def _on_select(self, i: int) -> None:
        if 0 <= i < len(self.dets):
            self.current = i
            self._updating = True
            try:
                self.fill(self.dets[i])
            finally:
                self._updating = False
            self.highlight.emit(self.highlighted())

    def _write(self, text: str) -> None:
        dets = [d for d in self.dets]
        self.edit(text, lambda doc: self.write(doc, dets))

    def _add(self) -> None:
        self.dets.append(self.new_detector())
        self.current = len(self.dets) - 1
        self._write(f"Añadir un {self.kind_name}")

    def _remove(self) -> None:
        if 0 <= self.current < len(self.dets):
            del self.dets[self.current]
            self.current = max(self.current - 1, 0)
            self._write(f"Quitar un {self.kind_name}")

    def _det(self):
        return self.dets[self.current] if 0 <= self.current < len(self.dets) else None

    def change(self, text: str, **values) -> None:
        det = self._det()
        if det is None or self._updating:
            return
        for k, v in values.items():
            setattr(det, k, v)
        self._write(text)

    def highlighted(self) -> set[int]:
        det = self._det()
        if det is None or not hasattr(det, "bodies") or self.geo is None:
            return set()
        return {resolve_ref(self.geo, b.label, b.kb, self.blank_search) for b in det.bodies} - {0}


class ImpactSection(_DetectorSection):
    title = "Detectores de impacto"
    kind_name = "detector de impacto"
    keywords = set(sc.IMPACT_KEYWORDS)
    blank_search = False  # IDBODY con la etiqueta en blanco: IRETRN 3082

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self.form_box)
        f.setContentsMargins(0, 0, 0, 0)
        self.el = EnergyEdit(minimum=0.0, width=96, tooltip="EL: límite inferior de la ventana de energía")
        self.eu = EnergyEdit(minimum=0.0, width=96, tooltip="EU: límite superior (0 = la energía máxima de la "
                                                             "fuente)")
        self.el.valueEdited.connect(lambda v: self.change("Ventana del detector", el=v))
        self.eu.valueEdited.connect(lambda v: self.change("Ventana del detector", eu=v))
        f.addRow("Energía:", row(self.el, "a", self.eu))
        self.bins = BinsEdit()
        self.bins.valueEdited.connect(lambda n: self.change("Intervalos del detector", nbe=n))
        f.addRow("Intervalos:", self.bins)
        pw = QWidget()
        pl = QHBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        self.kpar = {}
        for k, name in PARTICLES:
            cb = QCheckBox(name)
            cb.setToolTip(f"IDKPAR {k}: detectar {PARTICLE_NAMES[k]}")
            cb.toggled.connect(self._on_kpar)
            pl.addWidget(cb)
            self.kpar[k] = cb
        pl.addStretch(1)
        f.addRow("Partículas:", pw)
        self.kpar_hint = hint("Sin marcar ninguna, se detectan todas.")
        f.addRow(self.kpar_hint)
        self.idcut = compact_combo(QComboBox(), 12)
        for k, t in IDCUT_TEXT.items():
            self.idcut.addItem(t, k)
        self.idcut.activated.connect(lambda _i: self.change("Qué pasa al entrar en el detector",
                                                            idcut=int(self.idcut.currentData())))
        f.addRow("Al entrar:", self.idcut)
        self.ipsf = QCheckBox("Guardar las partículas que entran en un espacio de fases (IPSF 1)")
        self.ipsf.toggled.connect(lambda on: self.change("Espacio de fases del detector", ipsf=1 if on else 0))
        f.addRow(self.ipsf)
        self.psf = NameEdit("psf-impdet.dat")
        self.psf.nameEdited.connect(lambda t: self.change("Fichero de espacio de fases", psf=t))
        f.addRow("Fichero PSF:", self.psf)
        self.spc = NameEdit()
        self.spc.nameEdited.connect(lambda t: self.change("Fichero del espectro", spc=t))
        f.addRow("Espectro:", self.spc)
        self.age_on = QCheckBox("Distribución de edades (IDAGEL)")
        self.age_on.toggled.connect(self._on_age_toggle)
        f.addRow(self.age_on)
        self.agel = NumberEdit(minimum=0.0, width=80, tooltip="AGEL: edad mínima (s)")
        self.ageu = NumberEdit(minimum=0.0, width=80, tooltip="AGEU: edad máxima (s)")
        self.age_bins = BinsEdit()
        self.agel.valueEdited.connect(lambda v: self._on_age())
        self.ageu.valueEdited.connect(lambda v: self._on_age())
        self.age_bins.valueEdited.connect(lambda n: self._on_age())
        self.age_row = row(self.agel, "a", self.ageu, "s en", self.age_bins)
        f.addRow("Edades:", self.age_row)
        self.bodies = BodyChecklist(blank_search=False)
        self.bodies.changed.connect(lambda refs: self.change("Cuerpos del detector", bodies=refs))
        f.addRow("Cuerpos:", self.bodies)
        self.note = hint("")
        f.addRow(self.note)
        self.form = f

    def read(self, doc):
        return sc.impact_detectors(doc)

    def write(self, doc, dets):
        sc.set_impact_detectors(doc, dets)

    def new_detector(self):
        return sc.ImpactDetector(0.0, 0.0, 100, 0, 1)

    def label(self, i, d) -> str:
        return f"Detector {i}: {plural(len(d.bodies), 'cuerpo')} · {_window_text(d.el, d.eu)}"

    def intro(self) -> str:
        if self.dets:
            return ""
        return ("Un detector de impacto registra el espectro de las partículas que entran en sus cuerpos desde "
                "fuera de ellos (hasta 5 detectores). Los cuerpos no pueden ser vacíos.")

    def fill(self, d) -> None:
        self.el.set_value(d.el)
        self.eu.set_value(d.eu if d.eu >= 50.0 else None)
        self.eu.setPlaceholderText("E máx.")
        _mark_window(self.el, self.eu, d.el, d.eu, 1.0 if d.nbe < 0 else 0.0, "penhmain se detendría (error 3077).")
        self.bins.set_value(d.nbe)
        for k, cb in self.kpar.items():
            cb.blockSignals(True)
            cb.setChecked(k in d.kpar)
            cb.blockSignals(False)
        i = self.idcut.findData(d.idcut)
        self.idcut.setCurrentIndex(i if i >= 0 else 1)
        self.ipsf.blockSignals(True)
        self.ipsf.setChecked(d.ipsf == 1)
        self.ipsf.blockSignals(False)
        self.psf.set_name(d.psf)
        self.form.setRowVisible(self.psf, d.ipsf == 1 or bool(d.psf))
        n = self.current + 1
        self.spc.setPlaceholderText(f"spc-impdet-{n:02d}.dat")
        self.spc.set_name(d.spc)
        on = d.age is not None
        self.age_on.blockSignals(True)
        self.age_on.setChecked(on)
        self.age_on.blockSignals(False)
        self.form.setRowVisible(self.age_row, on)
        if on:
            self.agel.set_value(d.age[0])
            self.ageu.set_value(d.age[1])
            self.age_bins.set_value(d.age[2])
        self.bodies.set_geometry(self.geo)
        self.bodies.set_refs(d.bodies)
        notes = []
        if d.flnc:
            notes.append(f"IDFLNC {html.escape(d.flnc)}: penhmain 2025 no usa este nombre.")
        if d.idcut == 2:
            notes.append("La fluencia se escribe en " + (html.escape(d.psf) if d.psf else f"fln-impdet-{n:02d}.dat")
                         + " (penhmain toma el nombre de IDPSF).")
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))

    def _on_kpar(self) -> None:
        kp = [k for k, cb in self.kpar.items() if cb.isChecked()]
        self.change("Partículas del detector", kpar=kp)

    def _on_age_toggle(self, on: bool) -> None:
        self.change("Distribución de edades", age=(0.0, 1.0e-6, 100) if on else None,
                    agef="" if not on else (self._det().agef if self._det() else ""))

    def _on_age(self) -> None:
        a, b, n = self.agel.value(), self.ageu.value(), self.age_bins.value()
        if a is None or b is None:
            return
        self.change("Distribución de edades", age=(a, b, n))

    def summary(self) -> str:
        dets = self.read(self.doc) if self.doc is not None else []
        if not dets:
            return "ninguno"
        return "; ".join(f"{i}: {plural(len(d.bodies), 'cuerpo')}" + (" + PSF" if d.ipsf == 1 else "")
                         for i, d in enumerate(dets, start=1))


# ---------------------------------------------------------------------------
# Detectores angulares
# ---------------------------------------------------------------------------

class AngularSection(_DetectorSection):
    title = "Detectores angulares"
    kind_name = "detector angular"
    keywords = set(sc.ANGULAR_KEYWORDS)

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self.form_box)
        f.setContentsMargins(0, 0, 0, 0)
        self.th = [NumberEdit(minimum=0.0, maximum=180.0, width=60) for _ in range(2)]
        self.ph = [NumberEdit(minimum=-180.0, maximum=360.0, width=60) for _ in range(2)]
        for i, e in enumerate(self.th):
            e.valueEdited.connect(lambda v, i=i: self.change("Ángulos del detector", **{f"theta{i + 1}": v}))
        for i, e in enumerate(self.ph):
            e.valueEdited.connect(lambda v, i=i: self.change("Ángulos del detector", **{f"phi{i + 1}": v}))
        f.addRow("θ entre:", row(self.th[0], "y", self.th[1], "°"))
        f.addRow("φ entre:", row(self.ph[0], "y", self.ph[1], "° (los dos en [0, 360] o en [−180, 180])"))
        self.en_on = QCheckBox("Ventana de energía propia (ADENER)")
        self.en_on.toggled.connect(lambda on: self.change("Energía del detector angular",
                                                          energy=(0.0, 0.0, 100) if on else None))
        f.addRow(self.en_on)
        self.el = EnergyEdit(minimum=0.0, width=96)
        self.eu = EnergyEdit(minimum=0.0, width=96)
        self.bins = BinsEdit()
        self.el.valueEdited.connect(lambda v: self._on_energy())
        self.eu.valueEdited.connect(lambda v: self._on_energy())
        self.bins.valueEdited.connect(lambda n: self._on_energy())
        self.en_row = row(self.el, "a", self.eu, "en", self.bins)
        f.addRow("Energía:", self.en_row)
        self.kpar = compact_combo(QComboBox(), 8)
        self.kpar.addItem("todas", None)
        for k, _ in PARTICLES:
            self.kpar.addItem(PARTICLE_NAMES[k], k)
        self.kpar.activated.connect(lambda _i: self.change("Partícula del detector angular",
                                                           kpar=self.kpar.currentData()))
        f.addRow("Partícula:", self.kpar)
        self.spc = NameEdit()
        self.spc.nameEdited.connect(lambda t: self.change("Fichero del espectro angular", spc=t))
        f.addRow("Espectro:", self.spc)
        self.solid = hint("")
        f.addRow(self.solid)
        self.form = f

    def read(self, doc):
        return sc.angular_detectors(doc)

    def write(self, doc, dets):
        sc.set_angular_detectors(doc, dets)

    def new_detector(self):
        return sc.AngularDetector(0.0, 90.0, 0.0, 360.0)

    def label(self, i, d) -> str:
        return (f"Detector {i}: θ {fmt_num(d.theta1, 6)}–{fmt_num(d.theta2, 6)}°, "
                f"φ {fmt_num(d.phi1, 6)}–{fmt_num(d.phi2, 6)}°")

    def intro(self) -> str:
        if self.dets:
            return ""
        return ("Un detector angular registra el espectro de las partículas que salen de la geometría con "
                "direcciones dentro de una ventana (θ, φ) (hasta 5).")

    def fill(self, d) -> None:
        for e, v in zip(self.th, (d.theta1, d.theta2)):
            e.set_value(v)
        for e, v in zip(self.ph, (d.phi1, d.phi2)):
            e.set_value(v)
        on = d.energy is not None
        self.en_on.blockSignals(True)
        self.en_on.setChecked(on)
        self.en_on.blockSignals(False)
        self.form.setRowVisible(self.en_row, on)
        if on:
            el, eu, nbe = d.energy
            self.el.set_value(el)
            self.eu.set_value(eu if eu >= 50.0 else None)  # EU < 50 eV: la energía máxima de la fuente
            self.eu.setPlaceholderText("E máx.")
            self.bins.set_value(nbe)
            _mark_window(self.el, self.eu, el, eu, 50.0 if nbe < 0 else 0.0,
                         "penhmain no lo comprueba y el espectro saldría vacío.")
        i = self.kpar.findData(d.kpar)
        self.kpar.setCurrentIndex(max(i, 0))
        self.spc.setPlaceholderText(f"spc-angdet-{self.current + 1:02d}.dat")
        self.spc.set_name(d.spc)
        try:
            omega = abs(math.cos(math.radians(d.theta1)) - math.cos(math.radians(d.theta2))) * \
                abs(math.radians(d.phi2 - d.phi1))
            self.solid.setText(f"Ángulo sólido: {fmt_num(omega)} sr ({fmt_num(100 * omega / (4 * math.pi), 3)} % "
                               "de 4π). El espectro sale por unidad de ángulo sólido.")
        except ValueError:
            self.solid.setText("")

    def _on_energy(self) -> None:
        a, b = self.el.value(), self.eu.value()
        a = 0.0 if a is None else a
        b = 0.0 if b is None else b  # vacío: la energía máxima de la fuente (EU = 0)
        self.change("Energía del detector angular", energy=(a, b, self.bins.value()))

    def summary(self) -> str:
        dets = self.read(self.doc) if self.doc is not None else []
        return "ninguno" if not dets else f"{len(dets)}"


# ---------------------------------------------------------------------------
# Detectores de energía depositada
# ---------------------------------------------------------------------------

class EnergySection(_DetectorSection):
    title = "Detectores de energía depositada"
    kind_name = "detector de energía"
    keywords = set(sc.ENERGY_KEYWORDS)

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self.form_box)
        f.setContentsMargins(0, 0, 0, 0)
        self.el = EnergyEdit(minimum=0.0, width=96)
        self.eu = EnergyEdit(minimum=0.0, width=96)
        self.el.valueEdited.connect(lambda v: self.change("Ventana del detector", el=v))
        self.eu.valueEdited.connect(lambda v: self.change("Ventana del detector", eu=v))
        f.addRow("Energía:", row(self.el, "a", self.eu))
        self.bins = BinsEdit()
        self.bins.valueEdited.connect(lambda n: self.change("Intervalos del detector", nbe=n))
        f.addRow("Intervalos:", self.bins)
        self.spc = NameEdit()
        self.spc.nameEdited.connect(lambda t: self.change("Fichero del espectro", spc=t))
        f.addRow("Espectro:", self.spc)
        self.bodies = BodyChecklist()
        self.bodies.changed.connect(lambda refs: self.change("Cuerpos del detector", bodies=refs))
        f.addRow("Cuerpos:", self.bodies)
        f.addRow(hint("No se puede usar con forzado de interacciones ni splitting (el espectro saldría sesgado)."))

    def read(self, doc):
        return sc.energy_detectors(doc)

    def write(self, doc, dets):
        sc.set_energy_detectors(doc, dets)

    def new_detector(self):
        return sc.EnergyDetector(0.0, 0.0, 100)

    def label(self, i, d) -> str:
        return f"Detector {i}: {plural(len(d.bodies), 'cuerpo')} · {_window_text(d.el, d.eu)}"

    def intro(self) -> str:
        if self.dets:
            return ""
        return ("Un detector de energía depositada registra la distribución de la energía que cada lluvia deja "
                "en sus cuerpos (hasta 5).")

    def fill(self, d) -> None:
        self.el.set_value(d.el)
        self.eu.set_value(d.eu if d.eu >= 50.0 else None)
        self.eu.setPlaceholderText("E máx.")
        _mark_window(self.el, self.eu, d.el, d.eu, 50.0 if d.nbe < 0 else 0.0, "penhmain se detendría (error 3097).")
        self.bins.set_value(d.nbe)
        self.spc.setPlaceholderText(f"spc-enddet-{self.current + 1:02d}.dat")
        self.spc.set_name(d.spc)
        self.bodies.set_geometry(self.geo)
        self.bodies.set_refs(d.bodies)

    def summary(self) -> str:
        dets = self.read(self.doc) if self.doc is not None else []
        return "ninguno" if not dets else "; ".join(f"{i}: {plural(len(d.bodies), 'cuerpo')}"
                                                    for i, d in enumerate(dets, start=1))


# ---------------------------------------------------------------------------
# Distribución de dosis
# ---------------------------------------------------------------------------

class DoseSection(Section):
    title = "Distribución de dosis"
    keywords = {"GRIDX ", "GRIDY ", "GRIDZ ", "GRIDR "}
    showGridRequested = Signal()
    fitRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self)
        f.setContentsMargins(4, 2, 4, 4)
        self.kind = compact_combo(QComboBox(), 12)
        for k, t in sc.GRID_KINDS.items():
            self.kind.addItem(t, k)
        self.kind.activated.connect(self._on_kind)
        f.addRow("Rejilla:", self.kind)
        self.lo = {k: NumberEdit(width=70) for k in "xyz"}
        self.hi = {k: NumberEdit(width=70) for k in "xyz"}
        self.n = {k: BinsEdit(maximum=401 if k == "z" else 101, allow_log=False) for k in "xyz"}
        self.rows = {}
        for k in "xyz":
            self.lo[k].valueEdited.connect(lambda v: self._on_values())
            self.hi[k].valueEdited.connect(lambda v: self._on_values())
            self.n[k].valueEdited.connect(lambda v: self._on_values())
            self.rows[k] = row(self.lo[k], "a", self.hi[k], "cm en", self.n[k])
            f.addRow(f"{k.upper()}:", self.rows[k])
        self.r = NumberEdit(minimum=0.0, width=70, tooltip="RU: radio (cm)")
        self.nr = BinsEdit(maximum=101, allow_log=False)
        self.r.valueEdited.connect(lambda v: self._on_values())
        self.nr.valueEdited.connect(lambda v: self._on_values())
        self.row_r = row(self.r, "cm en", self.nr)
        f.addRow("Radio:", self.row_r)
        # El tamaño del vóxel: se escribe y se calcula el número de intervalos (y el eje se ajusta para que el vóxel
        # mida eso justo: los laterales, con el mismo centro; la profundidad z, desde el mismo principio).
        self.voxel_size = {k: NumberEdit(minimum=0.0, width=64, tooltip=f"Tamaño del vóxel en {k.upper()} (cm): se "
                                   "calcula el número de intervalos") for k in "xyzr"}
        for k, w in self.voxel_size.items():
            w.valueEdited.connect(lambda v, k=k: self._on_size(k, v))
        self.size_labels = {k: QLabel(t) for k, t in (("x", "Δx"), ("y", "Δy"), ("z", "Δz"), ("r", "Δr"))}
        self.odd = QCheckBox("impar")
        self.odd.setChecked(True)
        self.odd.setToolTip("Número impar de intervalos en X e Y: el eje central pasa por el centro de un vóxel (lo "
                            "que recomienda penhmain)")
        parts = []
        for k in "xyzr":
            parts += [self.size_labels[k], self.voxel_size[k]]
        self.row_size = row(*parts, "cm", self.odd)
        f.addRow("Vóxel:", self.row_size)
        self.size_note = hint("")
        self._note_grid: sc.DoseGrid | None = None
        f.addRow(self.size_note)
        self.voxel = hint("")
        f.addRow(self.voxel)
        self.resume_note = hint("")  # con RESUME, penhmain sigue con la rejilla del volcado
        self.resume_note.setTextFormat(Qt.TextFormat.RichText)
        f.addRow(self.resume_note)
        btns = QWidget()
        bl = QHBoxLayout(btns)
        bl.setContentsMargins(0, 0, 0, 0)
        self.btn_fit = QPushButton("Ajustar a la geometría")
        self.btn_fit.setToolTip("Caja: la de los cuerpos con material; cilindro (eje en x = y = 0) y esfera (centro "
                                "en el origen): el mayor que cabe en ella")
        self.btn_fit.clicked.connect(self.fitRequested)
        self.btn_show = QPushButton("Ver en el corte")
        self.btn_show.clicked.connect(self.showGridRequested)
        bl.addWidget(self.btn_fit)
        bl.addWidget(self.btn_show)
        bl.addStretch(1)
        f.addRow(btns)
        f.addRow(hint("penhmain recomienda un número impar de intervalos en la caja, para que los ejes centrales "
                      "pasen por el centro de una fila de vóxeles. El cilindro tiene el eje en Z y la esfera, el "
                      "centro en el origen."))
        self.form = f
        f.setRowVisible(self.size_note, False)
        f.setRowVisible(self.resume_note, False)

    def grid(self) -> sc.DoseGrid:
        return sc.dose_grid(self.doc) if self.doc is not None else sc.DoseGrid()

    def refresh(self) -> None:
        g = self.grid()
        if self.size_note.text() and g != self._note_grid:
            self._set_size_note("")
        kind = g.kind
        i = self.kind.findData(kind)
        if i < 0:
            if self.kind.findData("invalid") < 0:
                self.kind.addItem("combinación no válida", "invalid")
            i = self.kind.findData("invalid")
        self.kind.setCurrentIndex(i)
        show_xyz = {"box": "xyz", "cylinder": "z", "sphere": "", "none": "", "invalid": "xyz"}[kind]
        for k in "xyz":
            self.form.setRowVisible(self.rows[k], k in show_xyz)
            t = getattr(g, k)
            if t is not None:
                self.lo[k].set_value(t[0])
                self.hi[k].set_value(t[1])
                self.n[k].set_value(t[2])
        self.form.setRowVisible(self.row_r, kind in ("cylinder", "sphere", "invalid"))
        if g.r is not None:
            self.r.set_value(g.r[0])
            self.nr.set_value(g.r[1])
        axes = {"box": "xyz", "cylinder": "rz", "sphere": "r"}.get(kind, "")
        self.form.setRowVisible(self.row_size, bool(axes))
        for k in "xyzr":
            on = k in axes
            self.voxel_size[k].setVisible(on)
            self.size_labels[k].setVisible(on)
            if on:
                self.voxel_size[k].set_value(self._size_of(g, k))
        self.odd.setVisible(kind == "box")
        self.voxel.setText(self._voxel_text(g))
        note = self._resume_text()
        self.resume_note.setText(note)
        self.form.setRowVisible(self.resume_note, bool(note))
        self.btn_fit.setEnabled(kind in ("box", "cylinder", "sphere") and self.geo is not None)
        self.btn_show.setEnabled(kind in ("box", "cylinder", "sphere"))

    def _set_size_note(self, text: str) -> None:
        self.size_note.setText(text)
        self.form.setRowVisible(self.size_note, bool(text))  # vacía, sin su línea

    def _resume_text(self) -> str:
        """Con RESUME, penhmain toma la rejilla del volcado (DOSER) y no la del .in: se dice si no son la misma (lo
        mismo que los mensajes: ``resume.resume_outcome``)."""
        from ..penhan import resume as rs

        doc = self.doc
        out = rs.resume_outcome(doc, self.folder) if doc is not None else None
        if out is None or not out.follows or out.info.tallies is None:
            return ""  # penhmain empieza de cero (o se detiene: lo dice el panel de mensajes)
        diffs = tally_diffs(out.info, doc, ("grid",))
        if not diffs:
            return ""
        name = html.escape(out.shown)
        saved = out.info.tallies.grid
        if saved is None:
            text = (f"Se reanuda de {name} (RESUME): el volcado no tiene rejilla de dosis y penhmain no la calculará. "
                    "Para usar esta, empiece sin RESUME.")
        elif sc.dose_grid(doc).kind == "none":
            text = (f"Se reanuda de {name} (RESUME): penhmain seguirá calculando la dosis en la rejilla del volcado "
                    f"({html.escape(sc.grid_text(saved))}), aunque el .in no tenga ninguna. Para no calcularla, "
                    "empiece sin RESUME.")
        else:
            text = (f"Se reanuda de {name} (RESUME): penhmain sigue con la rejilla del volcado, no con esta "
                    f"({html.escape(diffs[0][2])}). Para usar esta, empiece sin RESUME.")
        return f"<span style='color:#9a6700'>{text}</span>"

    @staticmethod
    def _size_of(g: sc.DoseGrid, k: str) -> float | None:
        if k == "r":
            return float(f"{g.r[0] / g.r[1]:.6g}") if g.r is not None and g.r[1] > 0 else None
        t = getattr(g, k)
        return float(f"{abs(t[1] - t[0]) / t[2]:.6g}") if t is not None and t[2] > 0 else None

    def _on_size(self, k: str, v) -> None:
        """Otro tamaño de vóxel en el eje ``k``: otro número de intervalos (y el eje justo para ese tamaño)."""
        if v is None or self.doc is None:
            return
        g = self.grid()
        note = None
        try:
            if k == "r":
                if g.r is None:
                    return
                rad, n, note = sc.radial_bins_for(g.r[0], float(v), sc.GRID_MAX_BINS["r"])
                new = sc.DoseGrid(g.x, g.y, g.z, (rad, n))
            else:
                t = getattr(g, k)
                if t is None:
                    return
                lateral = k in "xy" and g.kind == "box"
                a, b, n, note = sc.bins_for_voxel(t[0], t[1], float(v), sc.GRID_MAX_BINS[k],
                                                  odd=lateral and self.odd.isChecked(),
                                                  anchor="centre" if lateral else "start")
                new = sc.DoseGrid(**{**{q: getattr(g, q) for q in ("x", "y", "z", "r")}, k: (a, b, n)})
        except ValueError as exc:
            self._set_size_note(str(exc))
            self._note_grid = g
            self.voxel_size[k].set_value(self._size_of(g, k))  # el campo dice el vóxel que hay, no lo escrito
            return
        if new == g:
            # La rejilla no cambia (p. ej. ya tenía los intervalos de más que caben): no hay paso de deshacer ni se
            # relee el .in, así que el campo se pone aquí con el vóxel que hay.
            self.voxel_size[k].set_value(self._size_of(g, k))
        self.edit("Tamaño del vóxel", lambda doc: sc.set_dose_grid(doc, new))
        self._set_size_note(note or "")
        self._note_grid = new  # la nota es de esta rejilla: con otra (deshacer, otro .in…) se quita

    @staticmethod
    def _voxel_text(g: sc.DoseGrid) -> str:
        def d(t):
            return abs(t[1] - t[0]) / t[2] if t is not None and t[2] > 0 else None

        if g.kind == "box":
            dx, dy, dz = d(g.x), d(g.y), d(g.z)
            even = [k.upper() for k in "xy" if getattr(g, k)[2] % 2 == 0]
            n = g.x[2] * g.y[2] * g.z[2]
            size = n * sc.DOSE_MAP_BYTES_PER_VOXEL
            text = (f"Vóxel de {fmt_num(dx)} × {fmt_num(dy)} × {fmt_num(dz)} cm: {fmt_int(g.x[2])} × "
                    f"{fmt_int(g.y[2])} × {fmt_int(g.z[2])} = {fmt_int(n)} {'vóxel' if n == 1 else 'vóxeles'} "
                    f"(3d-dose-map.dat de unos {_bytes(size)}).")
            if even:
                text += f" Número par de intervalos en {' y en '.join(even)}."
            return text
        if g.kind == "cylinder":
            return (f"Anillos de {fmt_num(g.r[0] / g.r[1])} cm y capas de {fmt_num(d(g.z))} cm "
                    f"({plural(g.r[1] * g.z[2], 'celda')}).")
        if g.kind == "sphere":
            return f"Capas esféricas de {fmt_num(g.r[0] / g.r[1])} cm ({plural(g.r[1], 'capa')})."
        return ""

    def _on_kind(self, _i: int) -> None:
        kind = self.kind.currentData()
        g = self.grid()
        z = g.z or (0.0, 10.0, 101)
        r = g.r or (5.0, 50)
        new = {"none": sc.DoseGrid(),
               "box": sc.DoseGrid(g.x or (-5.0, 5.0, 11), g.y or (-5.0, 5.0, 11), z),
               "cylinder": sc.DoseGrid(z=z, r=r),
               "sphere": sc.DoseGrid(r=r)}.get(kind)
        if new is not None:
            self.edit("Tipo de rejilla de dosis", lambda doc: sc.set_dose_grid(doc, new))

    def _on_values(self) -> None:
        kind = self.kind.currentData()

        def t(k):
            a, b = self.lo[k].value(), self.hi[k].value()
            return (a, b, self.n[k].value()) if a is not None and b is not None else None

        rr = (self.r.value(), self.nr.value()) if self.r.value() is not None else None
        if kind == "box":
            g = sc.DoseGrid(t("x"), t("y"), t("z"))
        elif kind == "cylinder":
            g = sc.DoseGrid(z=t("z"), r=rr)
        elif kind == "sphere":
            g = sc.DoseGrid(r=rr)
        else:
            return
        if g.kind != kind:
            return
        try:
            self.edit("Rejilla de dosis", lambda doc: sc.set_dose_grid(doc, g))
        except ValueError:
            pass

    def summary(self) -> str:
        g = self.grid()

        def n(v: float) -> str:
            return fmt_num(v, 6)

        if g.kind == "box":
            return (f"caja {n(g.x[0])}…{n(g.x[1])} × {n(g.y[0])}…{n(g.y[1])} × {n(g.z[0])}…{n(g.z[1])} cm "
                    f"({fmt_int(g.x[2])} × {fmt_int(g.y[2])} × {fmt_int(g.z[2])})")
        if g.kind == "cylinder":
            return f"cilindro r {n(g.r[0])} cm, z {n(g.z[0])}…{n(g.z[1])} cm ({fmt_int(g.r[1])} × {fmt_int(g.z[2])})"
        if g.kind == "sphere":
            return f"esfera r {n(g.r[0])} cm ({fmt_int(g.r[1])})"
        if g.kind == "invalid":
            return "combinación de líneas GRID no válida"
        return "sin rejilla"


# ---------------------------------------------------------------------------
# Proceso externo (EPROF)
# ---------------------------------------------------------------------------

class ExternalSection(Section):
    title = "Proceso externo"
    keywords = {"EPROF ", "EPROPM"}

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self)
        f.setContentsMargins(4, 2, 4, 4)
        self.on = QCheckBox("Registrar un proceso externo en la rejilla de dosis (EPROF)")
        self.on.toggled.connect(self._on_toggle)
        f.addRow(self.on)
        self.fname = NameEdit("sección eficaz (E, σ)")
        self.fname.nameEdited.connect(lambda t: self._set(fname=t))
        f.addRow("Fichero:", self.fname)
        self.kp = QComboBox()
        for k, _ in PARTICLES:
            self.kp.addItem(PARTICLE_NAMES[k], k)
        self.kp.activated.connect(lambda _i: self._set(kp=int(self.kp.currentData())))
        f.addRow("Partícula:", self.kp)
        self.mat = QComboBox()
        self.mat.activated.connect(lambda _i: self._set(mat=int(self.mat.currentData())))
        f.addRow("Material:", self.mat)
        f.addRow(hint("Tabla de dos columnas: energía (eV) y sección eficaz por molécula (cm²), en orden creciente "
                      "de energía. Con sección eficaz nula, se registra la fluencia de la partícula."))
        self.form = f

    def refresh(self) -> None:
        ep = sc.external_process(self.doc) if self.doc is not None else None
        self.on.blockSignals(True)
        self.on.setChecked(ep is not None)
        self.on.blockSignals(False)
        for w in (self.fname, self.kp, self.mat):
            self.form.setRowVisible(w, ep is not None)
        self.mat.clear()
        nmat = len(self.doc.find("MFNAME")) if self.doc is not None else 0
        for m in range(1, nmat + 1):
            self.mat.addItem(f"material {m}", m)
        if ep is not None:
            self.fname.set_name(ep[0])
            self.kp.setCurrentIndex(max(self.kp.findData(ep[1]), 0))
            i = self.mat.findData(ep[2])
            if i < 0:
                self.mat.addItem(f"material {ep[2]} (no existe)", ep[2])
                i = self.mat.count() - 1
            self.mat.setCurrentIndex(i)

    def _on_toggle(self, on: bool) -> None:
        self.edit("Proceso externo", lambda doc: sc.set_external_process(doc, ("xs.dat", 4, 1) if on else None))

    def _set(self, **kw) -> None:
        ep = sc.external_process(self.doc)
        if ep is None:
            return
        fname, kp, mat = ep
        fname, kp, mat = kw.get("fname", fname) or fname, kw.get("kp", kp), kw.get("mat", mat)
        self.edit("Proceso externo", lambda doc: sc.set_external_process(doc, (fname, kp, mat)))

    def summary(self) -> str:
        ep = sc.external_process(self.doc) if self.doc is not None else None
        return "no" if ep is None else f"{ep[0]} ({PARTICLE_NAMES.get(ep[1], ep[1])}, material {ep[2]})"


# ---------------------------------------------------------------------------
# Trabajo
# ---------------------------------------------------------------------------

class JobSection(Section):
    title = "Trabajo"
    keywords = {"RESUME", "DUMPTO", "DUMPP ", "LTRACK", "NSIMSH", "TIME  ", "RSEED "}

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self)
        f.setContentsMargins(4, 2, 4, 4)
        self.nsimsh = NumberEdit(minimum=0.0, width=90, allow_empty=True,
                                 tooltip="NSIMSH: partículas primarias que se simulan (cada una con su lluvia de "
                                         "secundarias; con una fuente radiactiva, desintegraciones). Vacío: 2e9")
        self.nsimsh.valueEdited.connect(lambda v: self.edit("Número de partículas", lambda d: sc.set_value(
            d, "NSIMSH", [v])))
        # Vacío, sin la línea (lo que dice la ayuda; antes no se podía vaciar: décima revisión).
        self.nsimsh.cleared.connect(lambda: self.edit("Número de partículas",
                                                      lambda d: sc.set_value(d, "NSIMSH", None)))
        f.addRow("Partículas:", row(self.nsimsh, "(NSIMSH)"))
        self.time = DurationEdit(maximum=MAX_TIME, allow_empty=True,
                                 tooltip="TIME: tiempo máximo de simulación, en la unidad elegida (en el .in, en "
                                         "segundos; como mucho 10⁹ s, unos 31,7 años). Vacío: sin límite (penhmain "
                                         "toma 2e9 s)")
        self.time.secondsEdited.connect(lambda v: self.edit("Tiempo de simulación", lambda d: sc.set_value(
            d, "TIME", [v])))
        self.time.cleared.connect(lambda: self.edit("Tiempo de simulación", lambda d: sc.set_value(d, "TIME", None)))
        self.time_text = QLabel()
        self.time_text.setStyleSheet("color: #666;")
        self.time.unit.currentIndexChanged.connect(lambda _i: self.refresh())
        f.addRow("Tiempo máx.:", row(self.time, self.time_text))
        self.seed_kind = compact_combo(QComboBox(), 8)
        self.seed_kind.addItem("semillas", "seeds")
        self.seed_kind.addItem("par precalculado n.º", "pair")
        self.seed_kind.activated.connect(self._on_seed_kind)
        # Las del generador de PENELOPE (RANECU: de 1 a m − 1 con sus módulos) o el número del par precalculado (de 1 a
        # 1001): una semilla que no cabe en un entero de 4 bytes dejaba el .in ilegible para penhmain (décima
        # revisión); se dice en el campo, como con el tiempo de más.
        self.s1 = NumberEdit(width=90, minimum=1.0, maximum=float(SEED1_MAX))
        self.s2 = NumberEdit(width=90, minimum=1.0, maximum=float(SEED2_MAX))
        self.s2.setToolTip(f"Segunda semilla (de 1 a {SEED2_MAX})")
        self.s1.format = lambda x: str(int(x))  # type: ignore[method-assign]
        self.s2.format = lambda x: str(int(x))  # type: ignore[method-assign]
        self.s1.valueEdited.connect(lambda v: self._on_seeds())
        self.s2.valueEdited.connect(lambda v: self._on_seeds())
        f.addRow("Aleatorios:", row(self.seed_kind, self.s1, self.s2))
        self.resume = NameEdit("(no se reanuda)")
        self.resume.nameEdited.connect(lambda t: self.edit("Reanudar", lambda d: sc.set_text(d, "RESUME", t)))
        f.addRow("Reanudar de:", row(self.resume, "(RESUME)"))
        self.resume_state = hint("")
        self.resume_state.setTextFormat(Qt.TextFormat.RichText)
        f.addRow(self.resume_state)
        # Sin DUMPTO, penhmain vuelca en dump.dmp (salvo con DUMPP ≥ 1e10): el marcador lo dice (duodécima revisión:
        # decía «(sin volcado)»).
        self.dumpto = NameEdit("dump.dmp")
        self.dumpto.nameEdited.connect(lambda t: self.edit("Fichero del volcado",
                                                           lambda d: sc.set_text(d, "DUMPTO", t)))
        f.addRow("Volcar en:", row(self.dumpto, "(DUMPTO)"))
        self.dumpto_state = hint("")
        self.dumpto_state.setTextFormat(Qt.TextFormat.RichText)
        f.addRow(self.dumpto_state)
        self.dumpp = NumberEdit(minimum=0.0, width=90, allow_empty=True,
                                tooltip="DUMPP: escribe el volcado y los resultados cada DUMPP segundos (de 30 s a un "
                                        "día; 0 o vacío: sin DUMPP)")
        # 0: sin la línea (con DUMPP 0, penhmain volcaría cada 30 s, lo contrario de lo que se quiere decir).
        self.dumpp.valueEdited.connect(lambda v: self.edit("Periodo de volcado", lambda d: sc.set_value(
            d, "DUMPP", [v] if v > 0 else None)))
        self.dumpp.cleared.connect(lambda: self.edit("Periodo de volcado", lambda d: sc.set_value(d, "DUMPP", None)))
        self.dumpp_text = QLabel()
        self.dumpp_text.setStyleSheet("color: #666;")
        f.addRow("Cada:", row(self.dumpp, "s (DUMPP)", self.dumpp_text))
        # Las lluvias para verlas en 3D se eligen al ejecutar («Lluvias», de 1 a 1000): ARGUS las simula con el
        # penhmain del usuario. LTRACK (las trayectorias en los .trk de la propia simulación, como mucho 51) ya no se
        # enseña aparte, que eran dos números para lo mismo (Rafael, 18-10-2026); si el .in lo trae, se respeta.
        self.form = f

    def refresh(self) -> None:
        doc = self.doc
        if doc is None:
            return
        v = sc.value(doc, "NSIMSH")
        self.nsimsh.set_value(v[0] if v else None)
        self.nsimsh.setPlaceholderText("2e9")
        v = sc.value(doc, "TIME")
        self.time.set_seconds(v[0] if v else None)
        self.time.setPlaceholderText("sin límite")
        t = v[0] if v else 2e9
        if not v:
            self.time_text.setText("sin límite (penhmain toma 2e9 s)")
        elif t < 1:
            self.time_text.setText("menos de 1 s: penhmain toma 100 s")
        else:
            self.time_text.setText(_time_text(t, self.time.factor))
        seeds = sc.seeds(doc)
        s1, s2 = seeds[-1] if seeds else (1, 1)
        pair = s1 < 0
        self.seed_kind.setCurrentIndex(1 if pair else 0)
        self.s1.set_value(float(-s1 if pair else s1))
        self.s2.set_value(float(s2))
        self.s2.setVisible(not pair)
        self.s1._max = float(MAX_SEED_PAIR if pair else SEED1_MAX)
        self.s1._tooltip = (f"Número del par de semillas precalculado de penhmain (RSEED −n: de 1 a {MAX_SEED_PAIR})"
                            if pair else f"Primera semilla (de 1 a {SEED1_MAX})")
        self.s1.setToolTip(self.s1._tooltip)
        self.resume.set_name(sc.text(doc, "RESUME") or "")
        self.dumpto.set_name(sc.text(doc, "DUMPTO") or "")
        v = sc.value(doc, "DUMPP")
        self.dumpp.set_value(v[0] if v else None)
        self.dumpp.setPlaceholderText("86400")  # sin DUMPP, penhmain vuelca una vez al día (y al acabar)
        self.dumpp_text.setText(_dump_period_text(doc))
        from ..penhan import goals as gl
        from ..results.folder import dump_period

        self.dumpto.setPlaceholderText("dump.dmp" if dump_period(gl.dumpp(doc)) is not None else "(sin volcado)")
        for label, text in ((self.resume_state, self._resume_state()), (self.dumpto_state, self._dumpto_state())):
            label.setText(text)
            self.form.setRowVisible(label, bool(text))  # vacía, sin su línea (duodécima revisión: dejaba un hueco)

    def _resume_state(self) -> str:
        """Lo que hará penhmain con RESUME (lo mismo que los mensajes y «Ejecutar»: ``resume.resume_outcome``)."""
        from ..penhan import resume as rs

        out = rs.resume_outcome(self.doc, self.folder, self.geo)
        if out is None:
            return ""
        e = html.escape(out.shown)
        red, amber = "#b42318", "#9a6700"
        if out.blank:
            return (f"<span style='color:{red}'>penhmain buscaría «{html.escape(out.name)}», con el blanco delante, y "
                    f"no {e}: {html.escape(out.why)}, se empezaría desde cero. Quite el blanco.</span>")
        if out.action == rs.FRESH:
            return f"{e} {html.escape(out.why)}: se empieza desde cero."
        if out.action == rs.DESTROY:
            return (f"<span style='color:{red}'>{e} {html.escape(out.why)}: penhmain escribiría «Empty.» encima (se "
                    "perdería).</span>")
        if out.action == rs.STOP:
            return f"<span style='color:{red}'>{e} {html.escape(out.why)}: penhmain se detendría.</span>"
        if out.action == rs.DONE:
            return (f"<span style='color:{amber}'>{e}: ya está terminada ({html.escape(out.why)}); penhmain solo "
                    "volverá a escribir los resultados (con el tiempo de cálculo a 0).</span>")
        diffs = [text for _key, text in out.diffs] + out.tallies
        text = f"{e}: {rs.showers_note(out)}"
        text += (" con otra configuración (" + html.escape("; ".join(diffs)) + ")") if diffs else \
            " con esta configuración"
        return f"<span style='color:{amber}'>{text}.</span>"

    def _dumpto_state(self) -> str:
        """Dónde escribirá penhmain el volcado y qué pasará con lo que ya hay en ese fichero."""
        from ..penhan import resume as rs

        doc = self.doc
        target = rs.dump_target(doc, self.folder) if self.folder is not None else None
        if target is None:
            return ""
        e = html.escape(target.shown)
        red, amber, grey = "#b42318", "#9a6700", "#666"
        if target.problem:
            tail = ": penhmain se detendría al escribir el primer volcado" if target.stops else ""
            return f"<span style='color:{red}'>{e} {html.escape(target.problem)}{tail}.</span>"
        res = rs.open_name(doc, "RESUME")
        if res is not None and rs.rel_key(res) == rs.rel_key(target.name):
            return ""  # el mismo que «Reanudar de» (lo dice su línea)
        info = read_dump(self.folder / target.name)
        if not target.explicit and info.state in ("missing", "empty"):
            return ""  # el «dump.dmp» de siempre, que aún no hay
        if info.state == "missing":
            return f"{e} no existe: se creará al simular."
        if info.state == "empty":
            return f"{e} no tiene una simulación guardada."
        if info.state == "foreign":
            return (f"<span style='color:{red}'>{e} no es un volcado de penhmain ({html.escape(info.message)}): "
                    "penhmain lo sustituiría por el volcado (se perdería).</span>")
        if not info.usable:
            return f"<span style='color:{grey}'>{e} está dañado o incompleto: penhmain lo sustituirá al simular.</span>"
        n = rs.count_text(info.showers, info.kparp == 0)
        if info.title != doc.model.title[:65].rstrip():
            return f"<span style='color:{grey}'>{e}: {n} de otra simulación (otro título); se sustituirá.</span>"
        diffs = source_differences(info, doc, self.geo)
        if diffs:
            return (f"<span style='color:{grey}'>{e}: {n} con otra configuración (" + html.escape("; ".join(diffs))
                    + "); se sustituirá.</span>")
        return (f"<span style='color:{amber}'>{e}: {n} de esta simulación; sin RESUME, penhmain empieza desde cero y "
                "lo sustituirá (para seguirla, póngalo en «Reanudar de»).</span>")

    def _on_seed_kind(self, _i: int) -> None:
        seeds = sc.seeds(self.doc)
        s1, s2 = seeds[-1] if seeds else (1, 1)
        if self.seed_kind.currentData() == "pair":
            new = (-max(abs(s1) if abs(s1) <= MAX_SEED_PAIR else 1, 1), 1)
        else:
            new = (1, 1) if s1 < 0 else (s1, s2)
        self.edit("Semillas", lambda d: sc.set_seeds(d, [new]))

    def _on_seeds(self) -> None:
        a, b = self.s1.value(), self.s2.value()
        if a is None or b is None:
            return
        pair = self.seed_kind.currentData() == "pair"
        new = (-int(abs(a)), 1) if pair else (int(a), int(b))
        self.edit("Semillas", lambda d: sc.set_seeds(d, [new]))

    def summary(self) -> str:
        doc = self.doc
        if doc is None:
            return ""
        v = sc.value(doc, "NSIMSH")
        n = v[0] if v else 2e9
        t = sc.value(doc, "TIME")
        tt = t[0] if t else 2e9
        from ..penhan import resume as rs

        src = doc.model.source
        parts = [rs.count_text(n, src.radioactive and not src.psf), f"máx. {_duration(tt if tt >= 1 else 100.0)}"]

        out = rs.resume_outcome(doc, self.folder, self.geo)
        if out is not None:
            parts.append(rs.short_state(out))  # solo «reanuda de …» si penhmain la sigue (duodécima revisión)
        return ", ".join(parts)


def _dump_period_text(doc) -> str:
    """Lo que hará penhmain con DUMPP (penhmain.f 4226-4245)."""
    from ..penhan import goals as gl

    d = gl.dumpp(doc)
    period = gl.follow_period(doc)
    if d is None:
        return "sin DUMPP: una vez al día y al acabar"
    if period is None:  # DUMPP ≥ 1e10
        return "solo al acabar" + ("" if sc.text(doc, "DUMPTO") else " (y sin volcado)")
    if d < 30.0:
        return "penhmain no baja de 30 s"
    if d > 86400.0:
        return "penhmain no pasa de un día"
    return ""


def _count(n: float) -> str:
    """«20 000», «2e9» (no «2e+04» ni «2e+09»)."""
    return fmt_count(n)


def _time_text(t: float, factor: float) -> str:
    """Lo que es ``t`` segundos, para leerlo junto al campo: «= 1 209 600 s» si se escribe en otra unidad, o «= 2
    semanas» si se escribe en segundos."""
    if factor != 1.0:
        return f"= {fmt_count(t)} s"
    return f"= {_duration(t)}" if t >= 60 else ""


def _duration(t: float) -> str:
    if t >= 3.15e7:
        return f"{fmt_num(t / 3.15e7, 3)} años"
    if t >= 14 * 86400:
        return f"{fmt_num(t / 604800, 3)} semanas"
    if t >= 86400:
        return f"{fmt_num(t / 86400, 3)} días"
    if t >= 3600:
        return f"{fmt_num(t / 3600, 3)} h"
    if t >= 60:
        return f"{fmt_num(t / 60, 3)} min"
    return f"{fmt_num(t, 6)} s"


__all__ = ["Section", "ImpactSection", "AngularSection", "EnergySection", "DoseSection", "ExternalSection",
           "JobSection"]
