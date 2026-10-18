"""Panel «Simulación»: el resto del .in (todo lo que no es la fuente) en formularios.

Cada sección es plegable y muestra en su cabecera un resumen y, si penhmain
se quejaría de algo de esa sección, un aviso. Los cambios pasan por el
controlador del .in (un paso de deshacer cada uno) y se escriben en el sitio
y en el orden en que los lee penhmain (``penhan.sections``).
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..core.diagnostics import Severity
from .deferred import DeferredEdits
from .settings import app_settings
from .sim_outputs import AngularSection, DoseSection, EnergySection, ExternalSection, ImpactSection, JobSection
from .sim_physics import EmergingSection, FieldsSection, GeometrySection, MaterialsSection, VarianceSection
from .sim_widgets import CollapsibleSection

# Códigos IRETRN de cada sección (además de las líneas de sus palabras clave).
SECTION_CODES = {
    "MaterialsSection": set(range(3052, 3055)) | {3012, 3013},
    "GeometrySection": set(range(3055, 3058)) | {3014, 3015, 3016, 3017, 3018, 3019},
    "FieldsSection": {3058, 3020, 3021},
    "VarianceSection": set(range(3059, 3074)) | {3022, 3023, 3024},
    "EmergingSection": {3025, 3026, 3074, 3075, 3121, 3122, 3123},
    "ImpactSection": set(range(3076, 3086)) | {3027, 3028, 3029, 3124, 3125, 3126},
    "AngularSection": set(range(3086, 3095)) | {3030, 30301, 30302, 3127, 3128, 3129},
    "EnergySection": set(range(3095, 3102)) | {3031, 3032, 3130, 3131, 3132},
    "DoseSection": set(range(3102, 3110)) | {3133, 3134, 3135, 3136},
    "ExternalSection": {3110, 3111, 3033, 3034, 3137, 3138, 3139},
    "JobSection": {3112, 3113},
}


class SimulationPanel(QScrollArea):
    highlightChanged = Signal(object)  # cuerpos que resaltar en las vistas (set)
    bodySelected = Signal(int)
    doseGridShow = Signal()
    doseGridFit = Signal()
    newInputRequested = Signal()
    editFailed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ctrl = None
        self.session = None
        self.settings = app_settings()
        self._deferred = DeferredEdits()
        inner = QWidget()
        self.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(0, 0, 0, 0)
        self.empty_label = QLabel("Abra un fichero de entrada (.in) para ver y editar sus parámetros de simulación.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color: #555;")
        el.addWidget(self.empty_label)
        self.btn_new = QPushButton("Crear un .in para esta geometría")
        self.btn_new.clicked.connect(self.newInputRequested)
        el.addWidget(self.btn_new, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.empty)
        self.sections = [MaterialsSection(), GeometrySection(), FieldsSection(), VarianceSection(), EmergingSection(),
                         ImpactSection(), AngularSection(), EnergySection(), DoseSection(), ExternalSection(),
                         JobSection()]
        self.frames: list[CollapsibleSection] = []
        self._highlighter = None
        for s in self.sections:
            key = f"sim/expanded/{type(s).__name__}"
            expanded = str(self.settings.value(key, "false")).lower() == "true"
            frame = CollapsibleSection(s.title, s, expanded)
            frame.toggled.connect(lambda on, s=s, key=key: self._on_toggle(s, key, on))
            s.apply.connect(self._apply)
            s.highlight.connect(lambda kbs, s=s: self._on_highlight(s, kbs))
            if hasattr(s, "bodySelected"):
                s.bodySelected.connect(self.bodySelected)
            lay.addWidget(frame)
            self.frames.append(frame)
        dose = self.section(DoseSection)
        dose.showGridRequested.connect(self.doseGridShow)
        dose.fitRequested.connect(self.doseGridFit)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self._diags: list = []
        self.set_context(None, None)

    def section(self, cls):
        return next(s for s in self.sections if isinstance(s, cls))

    def frame(self, cls) -> CollapsibleSection:
        return self.frames[self.sections.index(self.section(cls))]

    # -- contexto ------------------------------------------------------------------------
    def set_context(self, ctrl, session) -> None:
        if ctrl is not self.ctrl:
            self._highlighter = None  # otro .in: aún no se ha trabajado con ninguna sección
        self.ctrl = ctrl
        self.session = session
        self.refresh()

    @property
    def geo(self):
        s = self.session
        return s.geo if s is not None and s.ok else None

    def refresh(self) -> None:
        has = self.ctrl is not None
        self.empty.setVisible(not has)
        self.btn_new.setVisible(not has and self.geo is not None)
        for f in self.frames:
            f.setVisible(has)
        if not has:
            self.status.setText("")
            self._on_highlight(None, set())
            return
        doc = self.ctrl.doc
        folder = self.ctrl.folder or (self.session.path.parent if self.session is not None else None)
        ok = doc.model.ok
        self._refreshing = True
        try:
            for s, f in zip(self.sections, self.frames):
                s.set_context(doc if ok else None, self.geo, folder)
                f.setEnabled(ok)
                f.set_summary(s.summary() if ok else "")
        finally:
            self._refreshing = False
        if not ok:
            self.status.setText("<span style='color:#b42318'>penhmain no puede leer el .in (vea el panel de "
                                "mensajes): corríjalo en el texto para editar aquí.</span>")
        else:
            self.status.setText("")
        self._update_status()

    def set_diagnostics(self, diagnostics) -> None:
        self._diags = list(diagnostics)
        self._update_status()

    def _update_status(self) -> None:
        doc = self.ctrl.doc if self.ctrl is not None else None
        for s, f in zip(self.sections, self.frames):
            if doc is None:
                f.set_status(None)
                continue
            codes = SECTION_CODES.get(type(s).__name__, set())
            kws = {k.strip() for k in s.keywords}
            mine = []
            for d in self._diags:
                # Solo los del .in: los de la geometría (con otro fichero) no, tampoco con un .in sin guardar.
                if d.file is not None and (doc.path is None or str(d.file) != str(doc.path)):
                    continue
                ln = doc.lines[d.line] if d.line is not None and 0 <= d.line < len(doc.lines) else None
                if (d.code is not None and d.code in codes) or (ln is not None and ln.kw.strip() in kws):
                    mine.append(d)
            worst = max((d.severity for d in mine), default=None)
            if worst is None or worst < Severity.WARNING:
                f.set_status(None if not mine else Severity.INFO,
                             "\n".join(d.message for d in mine))
            else:
                f.set_status(worst, "\n".join(("✖ " if d.severity == Severity.ERROR else "⚠ ") + d.message
                                              for d in mine if d.severity >= Severity.WARNING))

    # -- cambios ---------------------------------------------------------------------------
    def _apply(self, text: str, fn) -> None:
        ctrl = self.ctrl
        if ctrl is None:
            return

        def run():
            if self.ctrl is not ctrl:
                return
            try:
                ctrl.edit(text, fn)
            except ValueError as exc:
                self.refresh()  # vuelve a poner lo que dice el .in (y borra el estado): el error, después
                self.status.setText(f"<span style='color:#b42318'>✖ {html.escape(str(exc))}</span>")
                self.editFailed.emit(str(exc))

        self._deferred.defer(run)  # fuera de la señal del widget que lo pide

    def flush_pending(self) -> None:
        """Aplica ya las ediciones pendientes (antes de guardar, cerrar, abrir o deshacer)."""
        self._deferred.flush()

    def _on_toggle(self, s, key: str, on: bool) -> None:
        self.settings.setValue(key, "true" if on else "false")
        if on:
            s.highlight.emit(s.highlighted())
        elif self._highlighter is s:
            self._on_highlight(s, set())

    _refreshing = False  # releyendo el .in (no es el usuario quien cambia de sección)

    def _on_highlight(self, s, kbs) -> None:
        if s is not None:
            frame = self.frames[self.sections.index(s)]
            if not frame.is_expanded() and kbs:
                return
            if self._refreshing and self._highlighter is not None and self._highlighter is not s:
                return  # al releer el .in manda la sección con la que se ha trabajado la última vez
        if not kbs and s is not None and self._highlighter is not s:
            return
        self._highlighter = s if kbs else None
        self.highlightChanged.emit(set(kbs or ()))
