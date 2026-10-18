"""Panel «Fuente»: la fuente del .in en un formulario.

Cada cambio se traduce en líneas del .in con las operaciones de
``argus.penhan.source_edit`` y pasa por el controlador del .in (se puede
deshacer). El panel no guarda estado propio: al cambiar el documento se
vuelve a leer todo de él.
"""

from __future__ import annotations

import html
import math
import re
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.diagnostics import Severity
from ..core.numfmt import plural
from ..penhan import sections as sc
from ..penhan import source_edit as se
from ..penhan.inputfile import fmt_real
from ..penhan.sections import analyze_spectrum
from ..render.slice2d import nice_length
from ..render.source2d import direction
from .deferred import DeferredEdits
from .panels import body_swatch_color, body_title, swatch
from .spectrum_plot import SpectrumPlot

PARTICLES = [(1, "Electrones"), (2, "Fotones"), (3, "Positrones"), (4, "Protones"), (5, "Partículas alfa"),
             (0, "Fuente radiactiva (nucleido)")]
EMISSIONS = [("pencil", "Haz lápiz (una dirección)"), ("cone", "Cono (haz con apertura)"),
             ("isotropic", "Isótropa (todas las direcciones)"), ("rect", "Ventana θ-φ (SRECTA)")]
SOURCE_KEYWORDS = {"SKPAR ", "SRNUCL", "SENERG", "SPECTR", "SLINES", "SGPOL ", "SPOSIT", "SBOX  ", "SBODY ",
                   "SCONE ", "SRECTA", "IPSFN ", "IPSPLI", "WGTWIN", "EPMAX "}
SOURCE_CODES = set(range(3002, 3012)) | set(range(3040, 3052))
ROLE_REF = Qt.ItemDataRole.UserRole + 2

_NUMBER = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
_ENERGY = re.compile(r"^\s*([-+]?(?:\d+[.,]?\d*|[.,]\d+)(?:[eEdD][-+]?\d+)?)\s*([a-zA-Z]*)\s*$")
_UNITS = {"": 1, "ev": 1, "kev": 10 ** 3, "mev": 10 ** 6, "gev": 10 ** 9}


def _normalize_number(text: str) -> str:
    t = text.strip().replace(" ", "")
    if "," in t and "." not in t and t.count(",") == 1:
        t = t.replace(",", ".")  # coma decimal
    return t.replace("d", "e").replace("D", "e")


def _finite(v: float, text: str) -> float:
    """Un número que no cabe en un real de doble precisión (1e400) sería Inf: no se acepta."""
    if not math.isfinite(v):
        raise ValueError(f"«{text.strip()}» es demasiado grande (el máximo es ±1.79E+308)")
    return v


def parse_number(text: str) -> float:
    """Número escrito como en Fortran (1.E6, 1d3, -100) o con coma decimal."""
    t = _normalize_number(text)
    if not _NUMBER.match(t):
        raise ValueError(f"«{text.strip()}» no es un número")
    return _finite(float(t), text)


def parse_energy(text: str) -> float:
    """Energía en eV a partir de «100 MeV», «15 keV», «1e8»... (exacta en decimal)."""
    m = _ENERGY.match(text)
    if m is None:
        raise ValueError(f"«{text.strip()}» no es una energía (p. ej. 100 MeV, 15 keV o 1e6)")
    num = m.group(1).replace(",", ".").replace("d", "e").replace("D", "e")
    unit = m.group(2).lower()
    if unit not in _UNITS:
        raise ValueError(f"Unidad desconocida «{m.group(2)}» (use eV, keV, MeV o GeV)")
    try:
        return _finite(float(Decimal(num) * _UNITS[unit]), text)
    except InvalidOperation:  # pragma: no cover - el patrón ya lo impide
        raise ValueError(f"«{text.strip()}» no es una energía") from None


def format_energy(v: float) -> str:
    a = abs(v)
    for factor, unit in ((10 ** 9, "GeV"), (10 ** 6, "MeV"), (10 ** 3, "keV")):
        if a >= factor:
            q = (Decimal(repr(float(v))) / factor).normalize()
            return f"{format(q, 'f')} {unit}"
    q = Decimal(repr(float(v))).normalize()
    return f"{format(q, 'f')} eV"


class NumberEdit(QLineEdit):
    """Campo numérico que acepta el formato de Fortran (1.E6, 1d3) y la coma decimal."""

    valueEdited = Signal(float)
    cleared = Signal()  # con ``allow_empty``: se ha vaciado el campo (se quita la línea; décima revisión)

    def __init__(self, parent=None, *, minimum: float | None = None, maximum: float | None = None,
                 width: int = 74, tooltip: str = "", allow_empty: bool = False):
        super().__init__(parent)
        self._value: float | None = None
        self._allow_empty = allow_empty
        self._problem: str | None = None  # por qué no sirve el valor (se marca en rojo mientras dure)
        self._min, self._max = minimum, maximum
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setMinimumWidth(width)
        self._tooltip = tooltip
        if tooltip:
            self.setToolTip(tooltip)
        self.editingFinished.connect(self._commit)
        self._flash = QTimer(self)
        self._flash.setSingleShot(True)
        self._flash.setInterval(1600)
        self._flash.timeout.connect(self._end_flash)

    def _end_flash(self) -> None:
        self._show_problem()  # el error se ha visto: vuelve la ayuda del campo (o lo que no sirve del valor)

    def set_problem(self, text: str | None) -> None:
        """Marca el campo en rojo, con el porqué en la ayuda, mientras el valor no sirva (None: lo desmarca)."""
        if text == self._problem:
            return
        self._problem = text
        if not self._flash.isActive():
            self._show_problem()

    def _show_problem(self) -> None:
        self.setStyleSheet("QLineEdit { background: #f8d7da; }" if self._problem else "")
        self.setToolTip(self._problem or self._tooltip)

    def format(self, v: float) -> str:
        return fmt_real(v)

    def parse(self, text: str) -> float:
        return parse_number(text)

    def value(self) -> float | None:
        return self._value

    def set_value(self, v: float | None) -> None:
        self._value = v
        if self.hasFocus() and self.isModified():
            return  # no pisar lo que el usuario está escribiendo
        self.setText("" if v is None else self.format(v))
        self.setModified(False)

    def _commit(self) -> None:
        if not self.isModified():
            return
        self.setModified(False)
        if self._allow_empty and not self.text().strip():
            self.setText("")
            if self._value is not None:
                self._value = None
                self.cleared.emit()
            return
        try:
            v = self.parse(self.text())
            if self._min is not None and v < self._min:
                raise ValueError(f"el valor mínimo es {self.format(self._min)}")
            if self._max is not None and v > self._max:
                raise ValueError(f"el valor máximo es {self.format(self._max)}")
        except ValueError as exc:
            self.setStyleSheet("QLineEdit { background: #f8d7da; }")
            self.setToolTip(str(exc))
            self._flash.start()
            self.setText("" if self._value is None else self.format(self._value))
            return
        self.setText(self.format(v))
        if self._value is None or v != self._value:
            self._value = v
            self.valueEdited.emit(v)


class EnergyEdit(NumberEdit):
    def format(self, v: float) -> str:
        return format_energy(v)

    def parse(self, text: str) -> float:
        return parse_energy(text)


def _unit(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet("color: #666;")
    return lab


def _row(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for x in widgets:
        if isinstance(x, str):
            lay.addWidget(_unit(x))
        else:
            lay.addWidget(x)
    lay.addStretch(1)
    return w


class SourcePanel(QScrollArea):
    """Formulario de la fuente primaria del .in."""

    newInputRequested = Signal()
    spectrumRequested = Signal()  # abrir el editor de espectros

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.ctrl = None
        self.session = None
        # De la ventana principal (0.16.1), que sabe la caja exacta de cada cuerpo: los cuerpos activos que la caja de
        # la fuente no cubre enteros, ajustar la caja a ellos y cambiarlos ajustando la caja si hace falta (sin la
        # ventana, el panel no avisa y cambia solo las líneas SBODY).
        self.uncovered_fn = None
        self.fit_fn = None
        self.bodies_fn = None
        self._updating = False
        self._deferred = DeferredEdits()
        self._diagnostics: list = []
        inner = QWidget()
        self.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        # -- sin .in ------------------------------------------------------------------
        self.empty = QWidget()
        le = QVBoxLayout(self.empty)
        le.setContentsMargins(0, 0, 0, 0)
        self.empty_label = QLabel()
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color: #555;")
        le.addWidget(self.empty_label)
        self.btn_new = QPushButton("Crear un .in para esta geometría")
        self.btn_new.clicked.connect(self.newInputRequested)
        le.addWidget(self.btn_new, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.empty)

        # -- fuente de ficheros de espacio de fases ---------------------------------
        self.psf_label = QLabel()
        self.psf_label.setWordWrap(True)
        self.psf_label.setStyleSheet("color: #555;")
        root.addWidget(self.psf_label)
        self.g_psf = QGroupBox("Ficheros de espacio de fases (IPSFN)")
        f = QFormLayout(self.g_psf)
        self.psf_list = QListWidget()
        self.psf_list.setMaximumHeight(110)
        self.psf_list.itemChanged.connect(self._on_psf_item)
        f.addRow(self.psf_list)
        self.btn_psf_add = QPushButton("Añadir fichero")
        self.btn_psf_add.clicked.connect(self._on_psf_add)
        self.btn_psf_remove = QPushButton("Quitar")
        self.btn_psf_remove.clicked.connect(self._on_psf_remove)
        f.addRow(_row(self.btn_psf_add, self.btn_psf_remove))
        self.psf_split = NumberEdit(minimum=1.0, width=60, tooltip="IPSPLI: cada partícula del fichero se divide "
                                                                   "en este número de copias")
        self.psf_split.format = lambda x: str(int(x))  # type: ignore[method-assign]
        self.psf_split.valueEdited.connect(lambda v: self._edit("Splitting del espacio de fases",
                                                                lambda d: sc.set_value(d, "IPSPLI", [int(v)])))
        f.addRow("Splitting:", _row(self.psf_split, "copias (IPSPLI)"))
        self.psf_w = [NumberEdit(minimum=0.0, width=64) for _ in range(2)]
        for e in self.psf_w:
            e.valueEdited.connect(lambda v: self._on_psf_window())
        f.addRow("Pesos:", _row(self.psf_w[0], "a", self.psf_w[1], "(WGTWIN)"))
        self.psf_e = [EnergyEdit(minimum=0.0, width=92) for _ in range(2)]
        self.psf_e[0].setToolTip("EPMAX: energía máxima de electrones, fotones y positrones de los ficheros")
        self.psf_e[1].setToolTip("EHMAX: energía máxima de protones, alfas y neutrones de los ficheros")
        for e in self.psf_e:
            e.valueEdited.connect(lambda v: self._on_psf_emax())
        f.addRow("E máx.:", _row("e⁻, γ, e⁺", self.psf_e[0], "hadrones", self.psf_e[1], "(EPMAX)"))
        self.btn_psf_off = QPushButton("Volver a una fuente normal")
        self.btn_psf_off.setToolTip("Quita las líneas IPSFN, IPSPLI, WGTWIN y EPMAX")
        self.btn_psf_off.clicked.connect(self._on_psf_off)
        f.addRow(self.btn_psf_off)
        root.addWidget(self.g_psf)

        # -- partícula y energía --------------------------------------------------------
        self.g_particle = QGroupBox("Partícula y energía")
        f = QFormLayout(self.g_particle)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.particle = QComboBox()
        for k, name in PARTICLES:
            self.particle.addItem(name, k)
        self.particle.setToolTip("SKPAR: tipo de partícula primaria (0 = fuente radiactiva con SRNUCL).")
        self.particle.activated.connect(self._on_particle)
        f.addRow("Partícula:", self.particle)
        self.nuclide = QLineEdit()
        self.nuclide.setToolTip("SRNUCL: fichero de datos del nucleido (PenNuc), en la carpeta del .in.")
        self.nuclide.editingFinished.connect(self._on_nuclide)
        f.addRow("Nucleido:", self.nuclide)
        self.energy = EnergyEdit(minimum=50.0, width=120,
                                 tooltip="SENERG: energía inicial (admite unidades: eV, keV, MeV, GeV; un número "
                                 "solo va en eV). Con menos de 50 eV penhmain usaría 1 GeV.")
        self.energy.valueEdited.connect(self._on_energy)
        self.btn_to_spectrum = QToolButton()
        self.btn_to_spectrum.setText("Espectro…")
        self.btn_to_spectrum.setToolTip("Sustituye la energía única por un espectro (SPECTR o SLINES), escrito a "
                                         "mano o leído de un fichero")
        self.btn_to_spectrum.clicked.connect(self.spectrumRequested)
        f.addRow("Energía:", _row(self.energy, self.btn_to_spectrum))
        self.spectrum_label = QLabel()
        self.spectrum_label.setWordWrap(True)
        self.spectrum_label.setTextFormat(Qt.TextFormat.RichText)
        self.spectrum_plot = SpectrumPlot(compact=True)
        self.spectrum_plot.setFixedHeight(64)
        self.spectrum_plot.setToolTip("Espectro tal como lo muestreará penhmain (rojo: energías exactas)")
        self.btn_spectrum = QPushButton("Editar el espectro…")
        self.btn_spectrum.setToolTip("Tabla del espectro con su gráfica: escribir, pegar o importar")
        self.btn_spectrum.clicked.connect(self.spectrumRequested)
        self.btn_mono = QPushButton("Usar una energía única…")
        self.btn_mono.setToolTip("Sustituye las líneas SPECTR/SLINES por una línea SENERG.")
        self.btn_mono.clicked.connect(self._on_mono)
        spec = QWidget()
        ls = QVBoxLayout(spec)
        ls.setContentsMargins(0, 0, 0, 0)
        ls.addWidget(self.spectrum_label)
        ls.addWidget(self.spectrum_plot)
        ls.addWidget(_row(self.btn_spectrum, self.btn_mono))
        f.addRow("Espectro:", spec)
        self.btn_psf_on = QPushButton("Usar ficheros de espacio de fases…")
        self.btn_psf_on.setToolTip("Las partículas primarias se leen de ficheros de espacio de fases (IPSFN)")
        self.btn_psf_on.clicked.connect(self._on_psf_on)
        self.psf_on_row = _row(self.btn_psf_on)
        f.addRow(self.psf_on_row)
        self.form_particle = f
        root.addWidget(self.g_particle)

        # -- posición y volumen -------------------------------------------------------
        self.g_position = QGroupBox("Posición y volumen")
        f = QFormLayout(self.g_position)
        self.pos = [NumberEdit(tooltip=f"SPOSIT: coordenada {c} (cm) del centro de la fuente.") for c in "xyz"]
        for i, e in enumerate(self.pos):
            e.valueEdited.connect(lambda v, i=i: self._on_position(i, v))
        f.addRow("Centro:", _row("x", self.pos[0], "y", self.pos[1], "z", self.pos[2], "cm"))
        self.rb_point = QRadioButton("Puntual")
        self.rb_box = QRadioButton("Extensa (caja SBOX)")
        self.kind_group = QButtonGroup(self)
        self.kind_group.addButton(self.rb_point, 0)
        self.kind_group.addButton(self.rb_box, 1)
        self.kind_group.idClicked.connect(self._on_kind)
        f.addRow("Tipo:", _row(self.rb_point, self.rb_box))
        self.box = [NumberEdit(minimum=0.0, tooltip=f"SBOX: lado de la caja en {c} (cm), centrada en SPOSIT.")
                    for c in "xyz"]
        for i, e in enumerate(self.box):
            e.valueEdited.connect(lambda v, i=i: self._on_box(i, v))
        f.addRow("Lados:", _row("x", self.box[0], "y", self.box[1], "z", self.box[2], "cm"))
        bodies = QWidget()
        lb = QVBoxLayout(bodies)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(3)
        self.bodies_hint = QLabel()
        self.bodies_hint.setWordWrap(True)
        self.bodies_hint.setStyleSheet("color: #666;")
        lb.addWidget(self.bodies_hint)
        self.body_filter = QLineEdit()
        self.body_filter.setPlaceholderText("Filtrar cuerpos…")
        self.body_filter.setClearButtonEnabled(True)
        self.body_filter.textChanged.connect(self._filter_bodies)
        lb.addWidget(self.body_filter)
        self.body_list = QListWidget()
        self.body_list.setMinimumHeight(110)
        self.body_list.itemChanged.connect(self._on_body_item)
        lb.addWidget(self.body_list)
        self.btn_fit_box = QPushButton("Ajustar la caja a los cuerpos activos")
        self.btn_fit_box.setToolTip("La caja SBOX justo alrededor de los cuerpos marcados (y SPOSIT en su centro): "
                                    "las partículas salen de todo su volumen")
        self.btn_fit_box.clicked.connect(self.fit_box)
        lb.addWidget(self.btn_fit_box, 0, Qt.AlignmentFlag.AlignLeft)
        f.addRow("Cuerpos\nactivos:", bodies)
        self.form_position = f
        root.addWidget(self.g_position)

        # -- emisión --------------------------------------------------------------------
        self.g_emission = QGroupBox("Emisión")
        f = QFormLayout(self.g_emission)
        self.emission = QComboBox()
        for k, name in EMISSIONS:
            self.emission.addItem(name, k)
        self.emission.setToolTip("Distribución angular: SCONE (lápiz, cono, isótropa) o SRECTA (ventana θ-φ).")
        self.emission.activated.connect(self._on_emission_kind)
        f.addRow("Tipo:", self.emission)
        self.theta = NumberEdit(minimum=0.0, maximum=180.0, width=64,
                                tooltip="θ: ángulo polar del eje del haz respecto a +Z (0-180°).")
        self.phi = NumberEdit(minimum=0.0, maximum=360.0, width=64,
                              tooltip="φ: ángulo azimutal del eje del haz desde +X hacia +Y (0-360°).")
        self.theta.valueEdited.connect(lambda v: self._on_axis())
        self.phi.valueEdited.connect(lambda v: self._on_axis())
        f.addRow("Eje:", _row("θ", self.theta, "°  φ", self.phi, "°"))
        self.alpha = NumberEdit(minimum=0.0, maximum=180.0, width=64,
                                tooltip="α: semiapertura del cono (0 = haz lápiz, 180 = isótropa).")
        self.alpha.valueEdited.connect(self._on_alpha)
        f.addRow("Semiapertura:", _row("α", self.alpha, "°"))
        self.rect = [NumberEdit(minimum=0.0, maximum=180.0 if i < 2 else 360.0, width=58) for i in range(4)]
        for e in self.rect:
            e.valueEdited.connect(lambda v: self._on_rect())
        f.addRow("θ entre:", _row(self.rect[0], "y", self.rect[1], "°"))
        f.addRow("φ entre:", _row(self.rect[2], "y", self.rect[3], "°"))
        presets = QWidget()
        gp = QGridLayout(presets)
        gp.setContentsMargins(0, 0, 0, 0)
        gp.setSpacing(3)
        self.preset_buttons = []
        for i, name in enumerate(("+X", "-X", "+Y", "-Y", "+Z", "-Z")):
            b = QToolButton()
            b.setText(name.replace("-", "−"))
            b.setToolTip(f"Eje del haz hacia {name.replace('-', '−')}")
            b.clicked.connect(lambda checked=False, n=name: self._on_preset(n))
            gp.addWidget(b, 0, i)
            self.preset_buttons.append(b)
        f.addRow("Apuntar a:", presets)
        self.emission_info = QLabel()
        self.emission_info.setWordWrap(True)
        self.emission_info.setStyleSheet("color: #555;")
        f.addRow(self.emission_info)
        self.form_emission = f
        root.addWidget(self.g_emission)

        # -- estado ---------------------------------------------------------------------
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self.status)
        tip = QLabel("En el corte: arrastre la estrella para mover la fuente, la punta de la flecha para "
                     "girar el haz y el cuadrado de la caja para cambiar su tamaño (Mayús: pasos grandes; "
                     "Ctrl: finos). Clic derecho para colocar la fuente o apuntar el haz a un punto.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #777; font-size: 11px;")
        root.addWidget(tip)
        self.tip = tip
        root.addStretch(1)
        self.set_context(None, None)

    # -- contexto -------------------------------------------------------------------------
    def set_context(self, ctrl, session) -> None:
        self.ctrl = ctrl
        self.session = session
        self._build_body_list()
        self.refresh()

    def set_diagnostics(self, diagnostics) -> None:
        self._diagnostics = list(diagnostics)
        self._update_status()

    @property
    def geo(self):
        s = self.session
        return s.geo if s is not None and s.ok else None

    def _edit(self, text: str, fn) -> None:
        if self.ctrl is None or self._updating:
            return
        try:
            self.ctrl.edit(text, fn)
        except ValueError as exc:  # p. ej. un nombre que no cabe en las columnas que lee penhmain: no cambia nada
            self.refresh()
            self.status.setVisible(True)
            self.status.setText(f"<span style='color:#b42318'>✖ {html.escape(str(exc))}</span>")

    def _defer_edit(self, text: str, fn) -> None:
        """Una edición pedida desde la señal de una lista (que la edición rehace): en la vuelta siguiente."""
        ctrl = self.ctrl

        def run():
            if self.ctrl is ctrl:
                self._edit(text, fn)

        self._deferred.defer(run)

    def flush_pending(self) -> None:
        """Aplica ya las ediciones pendientes (antes de guardar, cerrar, abrir o deshacer)."""
        self._deferred.flush()

    # -- lectura del documento ------------------------------------------------------------
    def refresh(self) -> None:
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _refresh(self) -> None:
        has_doc = self.ctrl is not None
        if has_doc and not (self.ctrl.doc.model.ok or self.ctrl.doc.model.source_read):
            # penhmain se para antes de terminar de leer la fuente: lo que hay son valores por defecto, no los del
            # fichero (no se enseñan ni se editan: cambiar uno sustituiría lo que hay escrito; 0.16.3).
            self.empty.setVisible(True)
            self.empty_label.setText("<span style='color:#b42318'>penhmain no puede leer la fuente del .in (vea el "
                                     "panel de mensajes): corríjala en el texto para verla y editarla aquí.</span>")
            self.btn_new.setVisible(False)
            for w in (self.psf_label, self.g_psf, self.g_particle, self.g_position, self.g_emission, self.tip,
                      self.status):
                w.setVisible(False)
            return
        src = self.ctrl.doc.model.source if has_doc else None
        psf = has_doc and src.from_psf
        self.empty.setVisible(not has_doc)
        if not has_doc:
            if self.session is not None and self.session.ok:
                self.empty_label.setText("Esta geometría no tiene fichero de entrada (.in) en su carpeta. "
                                         "Cree uno para definir la fuente y verla en el corte.")
                self.btn_new.setVisible(True)
            else:
                self.empty_label.setText("Abra un fichero de entrada (.in) para ver y editar su fuente.")
                self.btn_new.setVisible(False)
        self.psf_label.setVisible(bool(psf))
        self.g_psf.setVisible(bool(psf))
        if psf:
            self.psf_label.setText("<b>Fuente de ficheros de espacio de fases.</b> penhmain lee las partículas de "
                                   "esos ficheros (en la carpeta del .in) y no usa la posición ni la emisión de la "
                                   "fuente.")
            self._fill_psf()
        for g in (self.g_particle, self.g_position, self.g_emission):
            g.setVisible(has_doc and not psf)
        self.tip.setVisible(has_doc and not psf)
        self.status.setVisible(has_doc)
        if not has_doc or psf:
            self._update_status()
            return
        # Partícula y energía.
        i = self.particle.findData(src.kparp)
        self.particle.setCurrentIndex(max(i, 0))
        radio = src.radioactive
        self.form_particle.setRowVisible(1, radio)
        self.form_particle.setRowVisible(self.psf_on_row, not radio)
        self.nuclide.setText(src.nuclide or "")
        spectrum = bool(src.spectrum or src.lines)
        self.form_particle.setRowVisible(2, not radio and not spectrum)
        self.form_particle.setRowVisible(3, not radio and spectrum)
        if not radio and not spectrum:
            self.energy.set_value(src.energy if self.ctrl.doc.first("SENERG") is not None else None)
            if self.ctrl.doc.first("SENERG") is None:
                self.energy.setPlaceholderText("1 GeV (por defecto)")
        if spectrum:
            pts = src.spectrum or src.lines
            es = [e for e, _ in pts]
            n = len(pts) - 1 if src.spectrum else len(pts)
            kind = ("intervalo" if n == 1 else "intervalos") if src.spectrum else ("línea" if n == 1 else "líneas")
            info = analyze_spectrum(pts, lines=not src.spectrum)
            text = (f"{n} {kind} entre {format_energy(min(es))} y {format_energy(max(es))} "
                    f"({'SPECTR' if src.spectrum else 'SLINES'}); energía media {format_energy(round(info.mean, 3))}.")
            if info.problems:
                text += f" <span style='color:#9a6700'>⚠ {plural(len(info.problems), 'aviso')}: vea el editor.</span>"
            self.spectrum_label.setText(text)
            self.spectrum_plot.set_info(info)
        # Posición y volumen.
        for e, v in zip(self.pos, src.position):
            e.set_value(v)
        ext = src.extended
        (self.rb_box if ext else self.rb_point).setChecked(True)
        for e, v in zip(self.box, src.box):
            e.set_value(v)
            e.setEnabled(ext)
        self.form_position.setRowVisible(2, ext)
        self.form_position.setRowVisible(3, ext)
        self._sync_body_checks()
        # Emisión.
        kind, info = se.emission_kind(src)
        self.emission.setCurrentIndex(self.emission.findData(kind))
        self.g_emission.setEnabled(not radio)
        if kind == "rect":
            for e, key in zip(self.rect, ("thl", "thu", "phl", "phu")):
                e.set_value(info[key])
        else:
            self.theta.set_value(info["theta"])
            self.phi.set_value(info["phi"])
            self.alpha.set_value(info["alpha"])
        self.form_emission.setRowVisible(1, kind in ("pencil", "cone"))
        self.form_emission.setRowVisible(2, kind == "cone")
        self.form_emission.setRowVisible(3, kind == "rect")
        self.form_emission.setRowVisible(4, kind == "rect")
        self.form_emission.setRowVisible(5, kind != "isotropic")
        self.emission_info.setText(self._emission_text(src, kind, info))
        self._update_status()

    def _emission_text(self, src, kind: str, info: dict) -> str:
        if src.radioactive:
            return "Fuente radiactiva: los productos de desintegración salen en todas las direcciones " \
                   "(penhmain no usa SCONE ni SRECTA)."
        if kind == "isotropic":
            return "Todas las direcciones con la misma probabilidad (4π sr)."
        angles = [v for k, v in info.items() if k in ("theta", "phi", "alpha", "thl", "thu", "phl", "phu")]
        if not all(math.isfinite(float(v)) for v in angles):
            return "Dirección no válida: los ángulos tienen Inf o NaN (véanse los mensajes)."
        if kind == "rect":
            thl, thu, phl, phu = info["thl"], info["thu"], info["phl"], info["phu"]
            omega = abs(math.cos(math.radians(thl)) - math.cos(math.radians(thu))) * abs(math.radians(phu - phl))
            return (f"cos θ uniforme entre cos {thl:g}° y cos {thu:g}°, φ uniforme entre {phl:g}° y {phu:g}°. "
                    f"Ángulo sólido {omega:.4g} sr ({100 * omega / (4 * math.pi):.3g} % de 4π).")
        d = direction(info["theta"], info["phi"])
        comps = ", ".join(f"{0.0 if abs(c) < 5e-13 else c:.4g}" for c in d)
        text = f"Dirección del eje u = ({comps})."
        if kind == "cone":
            omega = 2 * math.pi * (1 - math.cos(math.radians(info["alpha"])))
            text += (f" Direcciones uniformes dentro del cono: {omega:.4g} sr "
                     f"({100 * omega / (4 * math.pi):.3g} % de 4π).")
        if info.get("default"):
            text += " Sin línea SCONE: penhmain usa un haz lápiz hacia +Z."
        return text

    def _update_status(self) -> None:
        if self.ctrl is None:
            self.status.setText("")
            return
        path = self.ctrl.doc.path
        doc = self.ctrl.doc
        items = []
        for d in self._diagnostics:
            # Solo los del .in: los de la geometría (con otro fichero) no, tampoco con un .in sin guardar.
            if d.severity < Severity.WARNING or (d.file is not None and (path is None or str(d.file) != str(path))):
                continue
            ln = doc.lines[d.line] if d.line is not None and 0 <= d.line < len(doc.lines) else None
            if (d.code in SOURCE_CODES) or (ln is not None and ln.kw in SOURCE_KEYWORDS):
                items.append(d)
        errors = [d for d in self._diagnostics if d.severity == Severity.ERROR]
        if not items:
            if errors:
                self.status.setText(f"<span style='color:#1a7f37'>✔ La fuente es correcta.</span> "
                                    f"<span style='color:#b42318'>Hay {plural(len(errors), 'error', 'errores')} en "
                                    "otras partes: "
                                    "vea el panel de mensajes.</span>")
            else:
                self.status.setText("<span style='color:#1a7f37'>✔ penhmain leería esta fuente sin errores.</span>")
            return
        rows = []
        for d in items[:3]:
            color = "#b42318" if d.severity == Severity.ERROR else "#9a6700"
            mark = "✖" if d.severity == Severity.ERROR else "⚠"
            code = f" [IRETRN {d.code}]" if d.code else ""
            rows.append(f"<span style='color:{color}'>{mark} {d.message}{code}</span>")
        if len(items) > 3:
            rows.append(f"… y {len(items) - 3} más en el panel de mensajes.")
        self.status.setText("<br>".join(rows))

    # -- cuerpos activos --------------------------------------------------------------------
    def _build_body_list(self) -> None:
        self._updating = True
        try:
            self.body_list.clear()
            geo = self.geo
            if geo is None:
                return
            for b in geo.bodies:
                if b.origin == "enclosure":
                    continue
                mat = "vacío" if b.material == 0 else ("CT" if b.material == 9999 else f"material {b.material}")
                desc = se.body_description(geo, b.index)  # (el órgano en un maniquí: «BODY LUNGS»; se puede filtrar)
                text = f"{body_title(geo, b.index)} · " + (f"{desc} · " if desc else "") + mat
                it = QListWidgetItem(swatch(body_swatch_color(geo, b.index)), text)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked)
                it.setData(ROLE_REF, ("kb", b.index))
                self.body_list.addItem(it)
        finally:
            self._updating = False

    def _sync_body_checks(self) -> None:
        src = self.ctrl.doc.model.source
        geo = self.geo
        # Quitar las referencias de fuera de la lista de la vez anterior.
        for i in reversed(range(self.body_list.count())):
            if self.body_list.item(i).data(ROLE_REF)[0] == "ref":
                self.body_list.takeItem(i)
        listed = {self.body_list.item(i).data(ROLE_REF)[1] for i in range(self.body_list.count())}
        active = set()
        others = []  # (referencia, cuerpo al que la lleva penhmain o 0): se muestran aparte y no se pierden
        for ref in src.bodies:
            kb = ref.resolve(geo) if geo is not None else 0
            if kb in listed:
                active.add(kb)
            else:
                others.append((ref, kb if geo is not None and kb and 1 <= kb <= geo.nbodys else 0))
        for i in range(self.body_list.count()):
            it = self.body_list.item(i)
            it.setCheckState(Qt.CheckState.Checked if it.data(ROLE_REF)[1] in active else Qt.CheckState.Unchecked)
        for n, (ref, kb) in enumerate(others):
            if ref.label is not None:
                what = f"«{ref.label.strip()}»" if ref.label.strip() else "etiqueta en blanco"
            else:
                what = f"n.º {ref.kb}"
            if kb:  # p. ej. SBODY 0: el último cuerpo sin etiqueta, la envolvente que añade PENGEOM
                it = QListWidgetItem(f"{what} → {body_title(geo, kb)}")
                it.setToolTip("Así lo lee penhmain: el 0 y una etiqueta en blanco son el último cuerpo sin "
                              "etiqueta. Desmárquelo para quitarlo del .in.")
                it.setForeground(QBrush(QColor("#8a5a00")))
            else:
                it = QListWidgetItem(f"{what} (no existe en la geometría)")
                it.setForeground(QBrush(QColor("#b42318")))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            it.setData(ROLE_REF, ("ref", {"label": ref.label, "kb": ref.kb}))
            self.body_list.insertItem(n, it)
        ext = src.extended
        self.body_list.setEnabled(ext)
        self.body_filter.setEnabled(ext)
        self.btn_fit_box.setVisible(ext and bool(active) and self.fit_fn is not None)
        self.bodies_hint.setStyleSheet("color: #666;")
        if not ext:
            self.bodies_hint.setText("Solo para fuentes extensas.")
        elif active or others:
            n = len(active) + len(others)
            text = ("Se generan partículas solo en los puntos de la caja que están en "
                    + ("el cuerpo marcado (SBODY)." if n == 1 else f"los {n} cuerpos marcados (SBODY)."))
            short = self._uncovered(active)
            if short:
                names = ", ".join(body_title(geo, kb) for kb in short[:3]) + (" …" if len(short) > 3 else "")
                text = (f"⚠ La caja no cubre entero{'s' if len(short) > 1 else ''} {names}: de "
                        f"{'ellos' if len(short) > 1 else 'él'} solo sale lo que queda dentro de la caja. " + text)
                self.bodies_hint.setStyleSheet("color: #8a5a00;")
            self.bodies_hint.setText(text)
        else:
            self.bodies_hint.setText("Ningún cuerpo marcado: se generan partículas en toda la caja. Marque "
                                     "cuerpos para limitar la fuente a ellos (SBODY).")
        self._filter_bodies(self.body_filter.text())

    def _uncovered(self, active: set[int]) -> list[int]:
        """Los cuerpos activos que la caja no cubre enteros (si la ventana lo sabe)."""
        if self.uncovered_fn is None or not active:
            return []
        try:
            return list(self.uncovered_fn(sorted(active)))
        except Exception:  # noqa: BLE001 - sin la caja de los cuerpos no se avisa (no impide editar la fuente)
            return []

    def fit_box(self) -> None:
        """La caja de la fuente justo alrededor de los cuerpos activos (la de cada uno, exacta)."""
        if self.ctrl is None or self.fit_fn is None or self._updating:
            return
        self._deferred.flush()  # antes, lo que estuviera pendiente de la lista, en su orden
        self.fit_fn()

    def _filter_bodies(self, text: str) -> None:
        t = text.strip().lower()
        for i in range(self.body_list.count()):
            it = self.body_list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _on_body_item(self, item) -> None:
        if self._updating or self.ctrl is None:
            return
        kbs, extra = [], []
        for i in range(self.body_list.count()):
            it = self.body_list.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            kind, val = it.data(ROLE_REF)
            if kind == "kb":
                kbs.append(val)
            else:
                extra.append(val)
        geo = self.geo
        if self.bodies_fn is not None:  # la ventana: si un cuerpo nuevo no cabe entero en la caja, la ajusta
            ctrl = self.ctrl
            fn = self.bodies_fn

            def run():
                if self.ctrl is ctrl:
                    fn(kbs, extra)

            self._deferred.defer(run)
            return
        self._defer_edit("Cuerpos activos de la fuente", lambda d: se.set_active_bodies(d, geo, kbs, extra))

    # -- acciones -------------------------------------------------------------------------------
    def _on_particle(self, index: int) -> None:
        k = self.particle.itemData(index)
        self._edit("Cambiar la partícula", lambda d: se.set_particle(d, k))

    def _on_nuclide(self) -> None:
        if not self.nuclide.isModified():
            return
        self.nuclide.setModified(False)
        name = self.nuclide.text()
        self._edit("Cambiar el nucleido", lambda d: se.set_nuclide(d, name))

    def _on_energy(self, v: float) -> None:
        self._edit("Cambiar la energía", lambda d: se.set_energy(d, v))

    def _on_mono(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        src = self.ctrl.doc.model.source
        pts = src.spectrum or src.lines
        emax = max(e for e, _ in pts) if pts else 1e6
        text, ok = QInputDialog.getText(self, "Energía única",
                                        "Energía de la fuente (se borrarán las líneas del espectro):",
                                        text=format_energy(emax))
        if not ok:
            return
        try:
            v = parse_energy(text)
        except ValueError as exc:
            self.status.setText(f"<span style='color:#b42318'>✖ {exc}</span>")
            return
        self._edit("Usar una energía única", lambda d: se.set_energy(d, v))

    # -- espacio de fases ---------------------------------------------------------------------
    def _fill_psf(self) -> None:
        doc = self.ctrl.doc
        self.psf_list.blockSignals(True)
        self.psf_list.clear()
        folder = self.ctrl.folder
        for name in sc.psf_files(doc):
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            if folder is not None and not (folder / name).is_file():
                it.setForeground(QBrush(QColor("#b42318")))
                it.setToolTip("No existe en la carpeta del .in")
            self.psf_list.addItem(it)
        self.psf_list.blockSignals(False)
        v = sc.value(doc, "IPSPLI")
        self.psf_split.set_value(float(v[0]) if v else None)
        self.psf_split.setPlaceholderText("1")
        w = sc.value(doc, "WGTWIN") or list(sc.default("WGTWIN"))
        for e, x in zip(self.psf_w, w):
            e.set_value(float(x))
        em = sc.value(doc, "EPMAX") or list(sc.default("EPMAX"))
        for e, x in zip(self.psf_e, em):
            e.set_value(float(x))

    def _psf_names(self) -> list[str]:
        return [self.psf_list.item(i).text().strip() for i in range(self.psf_list.count())
                if self.psf_list.item(i).text().strip()]

    def _on_psf_item(self, _item) -> None:
        names = self._psf_names()
        if names:
            self._defer_edit("Ficheros de espacio de fases", lambda d: sc.set_psf_files(d, names))

    def _on_psf_add(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Espacio de fases", "Nombre del fichero (hasta 20 caracteres):")
        if ok and name.strip():
            names = self._psf_names() + [name.strip()[:20]]
            self._edit("Añadir un fichero de espacio de fases", lambda d: sc.set_psf_files(d, names))

    def _on_psf_remove(self) -> None:
        r = self.psf_list.currentRow()
        names = self._psf_names()
        if 0 <= r < len(names) and len(names) > 1:
            del names[r]
            self._edit("Quitar un fichero de espacio de fases", lambda d: sc.set_psf_files(d, names))

    def _on_psf_window(self) -> None:
        a, b = self.psf_w[0].value(), self.psf_w[1].value()
        if a is not None and b is not None:
            self._edit("Ventana de pesos", lambda d: sc.set_value(d, "WGTWIN", [a, b]))

    def _on_psf_emax(self) -> None:
        a, b = self.psf_e[0].value(), self.psf_e[1].value()
        if a is not None and b is not None:
            self._edit("Energía máxima del espacio de fases", lambda d: sc.set_value(d, "EPMAX", [a, b]))

    def _on_psf_on(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Espacio de fases",
                                        "Fichero de espacio de fases (en la carpeta del .in, hasta 20 caracteres):",
                                        text="psf-impdet.dat")
        if not ok or not name.strip():
            return
        src = self.ctrl.doc.model.source
        pts = src.spectrum or src.lines
        emax = max((e for e, _ in pts), default=src.energy) or 1.0e9

        def fn(d):
            sc.set_psf_files(d, [name.strip()[:20]])
            sc.set_value(d, "EPMAX", [emax, emax])

        self._edit("Fuente de espacio de fases", fn)

    def _on_psf_off(self) -> None:
        def fn(d):
            for key in ("IPSFN", "IPSPLI", "WGTWIN", "EPMAX"):
                d.remove(key)

        self._edit("Fuente normal (sin espacio de fases)", fn)

    def _on_position(self, i: int, v: float) -> None:
        pos = list(self.ctrl.doc.model.source.position)
        pos[i] = v
        self._edit("Mover la fuente", lambda d: se.set_position(d, pos))

    def _default_box(self) -> list[float]:
        s = self.session
        if s is not None and s.ok:
            lo, hi = s.bounds
            side = nice_length(0.1 * float(max(hi - lo)))
        else:
            side = 1.0
        return [side, side, side]

    def _on_kind(self, kind_id: int) -> None:
        if kind_id == 1:
            if not self.ctrl.doc.model.source.extended:
                size = self._default_box()
                self._edit("Fuente extensa", lambda d: se.set_box(d, size))
        else:
            self._edit("Fuente puntual", lambda d: se.set_box(d, None))

    def _on_box(self, i: int, v: float) -> None:
        size = list(self.ctrl.doc.model.source.box)
        size[i] = v
        self._edit("Cambiar la caja de la fuente", lambda d: se.set_box(d, size))

    def _axis(self) -> tuple[float, float]:
        kind, info = se.emission_kind(self.ctrl.doc.model.source)
        if kind == "rect":
            return 0.5 * (info["thl"] + info["thu"]), 0.5 * (info["phl"] + info["phu"])
        return info["theta"], info["phi"]

    def _on_emission_kind(self, index: int) -> None:
        kind = self.emission.itemData(index)
        cur, info = se.emission_kind(self.ctrl.doc.model.source)
        if kind == cur and not info.get("default"):
            return
        th, ph = self._axis()
        if kind == "rect":
            if th < 10.0:
                rect = [0.0, 10.0, 0.0, 360.0]
            elif th > 170.0:
                rect = [170.0, 180.0, 0.0, 360.0]
            else:
                lo, hi = se._shift_window(th, 20.0, 180.0)
                plo, phu = se._shift_window(ph, 20.0, 360.0)
                rect = [lo, hi, plo, phu]
            self._edit("Emisión en ventana θ-φ", lambda d: se.set_emission(d, "rect", rect=rect))
        else:
            alpha = info.get("alpha", 0.0) if cur == "cone" else 10.0
            names = {"pencil": "Haz lápiz", "cone": "Haz cónico", "isotropic": "Emisión isótropa"}
            self._edit(names[kind], lambda d: se.set_emission(d, kind, th, ph, alpha))

    def _on_axis(self) -> None:
        th, ph = self.theta.value(), self.phi.value()
        if th is None or ph is None:
            return
        self._edit("Cambiar la dirección", lambda d: se.set_direction(d, th, ph))

    def _on_alpha(self, v: float) -> None:
        th, ph = self._axis()
        kind = "pencil" if v <= 0.0 else ("isotropic" if v >= 180.0 else "cone")
        self._edit("Cambiar la apertura", lambda d: se.set_emission(d, kind, th, ph, v))

    def _on_rect(self) -> None:
        vals = [e.value() for e in self.rect]
        if any(v is None for v in vals):
            return
        self._edit("Cambiar la ventana θ-φ", lambda d: se.set_emission(d, "rect", rect=vals))

    def _on_preset(self, name: str) -> None:
        th, ph = se.AXIS_ANGLES[name]
        self._edit(f"Haz hacia {name}", lambda d: se.set_direction(d, th, ph))
