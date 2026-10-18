"""Diálogo «Resultados que pedir a penhmain» (0.13.0): cada resultado con su casilla y, al marcarlo, su editor.

Por objetivos del investigador (la dosis, lo que entra en un cuerpo, la energía depositada, lo que sale…): marcar
uno lo pide en el .in con valores razonables y enseña debajo su editor (la matriz de vóxeles con su tamaño, las
ventanas de energía y los intervalos de los detectores, los cuerpos…), el mismo que en el panel «Simulación»;
desmarcarlo quita del .in sus líneas. Cada cambio es un paso de deshacer del .in y se ve enseguida en el corte y en
3D (la matriz de dosis se mueve y se estira allí con el ratón). No es modal: se puede dejar abierto mientras se
mira la geometría.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import fmt_int, plural
from ..penhan import goals as gl
from ..penhan import sections as sc
from ..penhan.dumpfile import read_dump, tally_differences
from .deferred import DeferredEdits
from .screen_fit import fit_to_screen
from .sim_outputs import AngularSection, DoseSection, EnergySection, ImpactSection, Section
from .sim_physics import EmergingSection
from .sim_widgets import NameEdit, row
from .source_panel import NumberEdit

OK_COLOR = "#1a7f37"
WARN_COLOR = "#9a6700"
BAD_COLOR = "#b42318"


def matter_bounds(session):
    """Caja de lo que tiene material (sin el vacío de alrededor), como «Ajustar a la geometría» del panel
    «Simulación»; si no se puede saber, la de la escena."""
    import numpy as np

    from ..render.raytrace import RayTracer
    from ..render.scene3d import matter_bounds as traced

    lo, hi = session.bounds
    try:
        tracer = RayTracer(session.geo)
        vb = traced(tracer, lo, hi, tracer.default_visibility())
    except Exception:  # noqa: BLE001 - sin trazador: la caja de la escena
        vb = None
    if vb is None:
        return lo, hi
    vlo, vhi = (np.asarray(v, dtype=float) for v in vb)
    return (vlo, vhi) if np.all(np.isfinite(vlo)) and np.all(np.isfinite(vhi)) else (lo, hi)


# ---------------------------------------------------------------------------------------------------------------------
# Editores pequeños (lo que no tiene sección propia en el panel)
# ---------------------------------------------------------------------------------------------------------------------

class FollowSection(Section):
    """Cada cuánto escribe penhmain los resultados y el volcado (DUMPP, DUMPTO)."""

    title = "Seguir la simulación"
    keywords = {"DUMPTO", "DUMPP "}

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        self.every = NumberEdit(minimum=0.0, width=80, tooltip="DUMPP: cada cuántos segundos escribe penhmain los "
                                                               "resultados (de 30 s a un día)")
        self.every.valueEdited.connect(lambda v: self.edit("Periodo de volcado", lambda d: sc.set_value(
            d, "DUMPP", [v] if v else None)))
        self.dump = NameEdit("dump.dmp")
        self.dump.nameEdited.connect(lambda t: self.edit("Volcado", lambda d: sc.set_text(d, "DUMPTO", t)))
        lay.addWidget(row("Cada", self.every, "s, con el volcado en", self.dump))

    def refresh(self) -> None:
        doc = self.doc
        if doc is None:
            return
        v = sc.value(doc, "DUMPP")
        self.every.set_value(float(v[0]) if v else None)
        self.dump.set_name(sc.text(doc, "DUMPTO") or "")


# ---------------------------------------------------------------------------------------------------------------------
# Los resultados que se pueden pedir
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Card:
    key: str
    title: str
    files: str
    body: bool = False  # para pedirlo hay que elegir un cuerpo
    always: bool = False  # penhmain siempre lo escribe (solo se ajusta)


CARDS = [
    Card("dosis", "Dosis: matriz de vóxeles",
         "3d-dose-map.dat, depth-dose.dat y los perfiles (caja GRIDX, GRIDY, GRIDZ); 2d-dose-map.dat (cilindro GRIDZ y "
         "GRIDR) o radial-dose.dat (esfera GRIDR). Se ve en el corte y en 3D: se mueve y se estira con el ratón."),
    Card("impacto", "Lo que entra en un cuerpo: espectro, fluencia y espacio de fases",
         "spc-impdet-##.dat (energía de lo que entra), fln-impdet-##.dat (fluencia, IDCUT = 2) y psf-impdet.dat "
         "(espacio de fases, IPSF = 1, en un solo detector): detectores de impacto IMPDET con sus cuerpos.",
         body=True),
    Card("deposito", "Energía depositada en un cuerpo (altura de pulsos)",
         "spc-enddet-##.dat: la energía que deja cada lluvia en el cuerpo, como un detector real (ENDETC, EDBODY).",
         body=True),
    Card("angular", "Lo que sale en una dirección (detectores angulares)",
         "spc-angdet-##.dat: espectro de lo que sale de la geometría dentro de una ventana (θ, φ) (ANGDET, ADENER)."),
    Card("salen", "Lo que sale de la geometría: energía y ángulos",
         "energy-up.dat, energy-down.dat y polar-angle-*.dat (NBE, NBANGL): penhmain los escribe siempre; angle.dat "
         "(θ y φ), solo con más de un intervalo de φ.",
         always=True),
    Card("seguir", "Seguir la simulación mientras corre",
         "Todos los resultados y un volcado cada DUMPP segundos: ARGUS los va enseñando y la simulación se puede "
         "reanudar (RESUME)."),
]  # (las lluvias para verlas en 3D se eligen al ejecutar: «Lluvias», de 1 a 1000)
CARDS_BY_KEY = {c.key: c for c in CARDS}


def resume_note(doc, folder) -> str:
    """Con RESUME y un volcado que penhmain va a seguir, lo que se elige aquí no cuenta: penhmain toma del volcado las
    rejillas y los intervalos de los resultados (HENANR, IMDETR, ANDETR, ENDETR y DOSER). Se dice, y qué no
    coincide."""
    name = sc.text(doc, "RESUME")
    if not name or folder is None:
        return ""
    info = read_dump(Path(folder) / name)
    if not info.usable or info.title != doc.model.title[:65].rstrip():
        return ""  # penhmain empieza de cero (o se detiene: lo dice el panel de mensajes)
    text = (f"Se reanuda la simulación de {html.escape(name)} (RESUME): penhmain sigue con los resultados que pidió "
            "entonces, con sus rejillas y sus intervalos, y no con los de aquí. Para que cuenten los cambios, quite "
            "RESUME (panel «Simulación», sección «Trabajo») y empiece de cero.")
    diffs = tally_differences(info, doc)
    if diffs:
        text += " Ahora no coinciden: " + html.escape("; ".join(diffs)) + "."
    return f"<span style='color:{WARN_COLOR}'>{text}</span>"


def present(doc, key: str) -> str:
    """Lo que el .in ya pide de ese resultado («» si nada)."""
    if key == "dosis":
        g = sc.dose_grid(doc)
        if g.kind == "none":
            return ""
        if g.kind == "invalid":
            return "líneas GRID que penhmain no acepta"
        n = sc.dose_grid_cells(g)
        what, cells = {"box": ("caja", "vóxeles"), "cylinder": ("cilindro", "anillos"),
                       "sphere": ("esfera", "capas")}[g.kind]
        return f"{what}, {fmt_int(n)} {cells}"
    if key == "impacto":
        dets = sc.impact_detectors(doc)
        return plural(len(dets), "detector", "detectores") if dets else ""
    if key == "deposito":
        dets = sc.energy_detectors(doc)
        return plural(len(dets), "detector", "detectores") if dets else ""
    if key == "angular":
        dets = sc.angular_detectors(doc)
        return plural(len(dets), "detector", "detectores") if dets else ""
    if key == "salen":
        return "siempre"
    if key == "seguir":
        return gl.status(doc, "seguir")
    raise KeyError(key)


def add(doc, key: str, *, geo=None, bounds=None, kb: int | None = None) -> str:
    """Lo pide en el .in con valores razonables. Devuelve qué se ha hecho."""
    if key == "dosis":
        return gl.apply(doc, "dosis-caja", geo=geo, bounds=bounds)
    if key == "impacto":
        return gl.apply(doc, "espectro", geo=geo, kb=kb)
    if key == "deposito":
        return gl.apply(doc, "deposito", geo=geo, kb=kb)
    if key == "angular":
        dets = sc.angular_detectors(doc)
        dets.append(sc.AngularDetector(0.0, 90.0, 0.0, 360.0))
        sc.set_angular_detectors(doc, dets)
        return f"detector angular {len(dets)} (θ de 0 a 90°: lo que sale hacia delante)"
    if key == "seguir":
        return gl.apply(doc, key)
    raise KeyError(key)


def remove(doc, key: str) -> None:
    """Quita del .in las líneas de ese resultado."""
    if key == "dosis":
        sc.set_dose_grid(doc, sc.DoseGrid())
    elif key == "impacto":
        sc.set_impact_detectors(doc, [])
    elif key == "deposito":
        sc.set_energy_detectors(doc, [])
    elif key == "angular":
        sc.set_angular_detectors(doc, [])
    elif key == "seguir":
        gl.unfollow(doc)
    else:
        raise KeyError(key)


EDITORS = {"dosis": DoseSection, "impacto": ImpactSection, "deposito": EnergySection, "angular": AngularSection,
           "salen": EmergingSection, "seguir": FollowSection}


class _CardWidget(QFrame):
    toggled = Signal(str, bool)

    def __init__(self, card: Card, bodies: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.card = card
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)
        head = QHBoxLayout()
        self.check = QCheckBox(card.title)
        self.check.setStyleSheet("QCheckBox { font-weight: 600; }")
        if card.always:
            self.check.setChecked(True)
            self.check.setEnabled(False)
        self.check.toggled.connect(lambda on: self.toggled.emit(card.key, on))
        head.addWidget(self.check)
        head.addStretch(1)
        self.state = QLabel("")
        self.state.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(self.state)
        lay.addLayout(head)
        files = QLabel(html.escape(card.files))
        files.setWordWrap(True)
        files.setStyleSheet("color: #666;")
        lay.addWidget(files)
        self.body_row = None
        if card.body:
            self.body = QComboBox()
            for kb, text in bodies:
                self.body.addItem(text, kb)
            if not bodies:
                self.body.addItem("(no hay cuerpos con material)", None)
            self.body.setEnabled(bool(bodies))
            self.body_row = row("Con el cuerpo:", self.body, "(al marcarlo se añade un detector con él)")
            lay.addWidget(self.body_row)
        self.editor: Section = EDITORS[card.key]()
        lay.addWidget(self.editor)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.setVisible(False)
        lay.addWidget(self.message)

    def show_message(self, text: str, ok: bool = True) -> None:
        self.message.setText(f"<span style='color:{OK_COLOR if ok else BAD_COLOR}'>{html.escape(text)}</span>")
        self.message.setVisible(bool(text))


class OutputsDialog(QDialog):
    """Qué resultados pide el .in, con su editor al lado (no modal)."""

    showGridRequested = Signal()  # «Ver en el corte» de la matriz de dosis
    fitGridRequested = Signal()  # «Ajustar a la geometría»

    def __init__(self, parent, ctrl, session=None, bounds=None, undo_action=None):
        """``bounds``: la caja de lo que tiene material (la de la vista 3D), para la matriz de dosis; sin ella, la de
        la escena (que incluye el vacío de alrededor). ``undo_action``: el «Deshacer» de la ventana principal (por
        defecto, el de ``parent``), que deshace a la vez el .geo y el .in que lo sigue y conserva «Rehacer»."""
        super().__init__(parent)
        self.undo_action = undo_action if undo_action is not None else getattr(parent, "act_undo", None)
        self.setWindowTitle("ARGUS · Resultados que pedir a penhmain")
        self.ctrl = None
        self.session = None
        self.geo = None
        self.bounds = None
        self._deferred = DeferredEdits()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)
        lay = QVBoxLayout(self)
        intro = QLabel("Marque lo que quiere que calcule penhmain: se pide en el .in con valores razonables y debajo "
                       "aparece su editor para ajustarlo (la matriz de vóxeles y su tamaño, las ventanas de energía y "
                       "los intervalos, los cuerpos…). Desmarcarlo lo quita del .in. Cada cambio se puede deshacer.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.cards_layout = QVBoxLayout(self.inner)
        self.cards_layout.setSpacing(6)
        self.area.setWidget(self.inner)
        lay.addWidget(self.area, 1)
        self.cards: dict[str, _CardWidget] = {}
        bb = QDialogButtonBox()
        self.btn_undo = bb.addButton("Deshacer", QDialogButtonBox.ButtonRole.ResetRole)
        self.btn_undo.setToolTip("Deshace el último cambio, como Ctrl+Z en la ventana principal")
        self.btn_undo.clicked.connect(self._undo)
        close = bb.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        for b in (self.btn_undo, close):
            b.setAutoDefault(False)  # Intro en un campo confirma el campo; no pulsa «Deshacer»
        lay.addWidget(bb)
        if self.undo_action is not None:
            self.undo_action.changed.connect(self._update_undo)
        self.error_note = QLabel("")
        self.error_note.setWordWrap(True)
        self.error_note.setTextFormat(Qt.TextFormat.RichText)
        self.error_note.setVisible(False)
        lay.insertWidget(1, self.error_note)
        self.resume_note = QLabel("")  # con RESUME, penhmain sigue con las rejillas y los intervalos del volcado
        self.resume_note.setWordWrap(True)
        self.resume_note.setTextFormat(Qt.TextFormat.RichText)
        self.resume_note.setVisible(False)
        lay.insertWidget(2, self.resume_note)
        fit_to_screen(self, 820, 820)
        self.set_context(ctrl, session, bounds)

    # -- contexto ------------------------------------------------------------------------------------------------
    def set_context(self, ctrl, session=None, bounds=None) -> None:
        """Otro .in (o la misma con otra geometría): se rehacen las tarjetas."""
        if self.ctrl is not None and self.ctrl is not ctrl:
            try:
                self.ctrl.changed.disconnect(self._doc_changed)
            except (RuntimeError, TypeError):
                pass
        new_ctrl = ctrl is not self.ctrl
        self.ctrl = ctrl
        self.session = session
        self.geo = session.geo if session is not None and session.ok else None
        if bounds is not None:
            self.bounds = bounds
        else:
            self.bounds = matter_bounds(session) if self.geo is not None else None
        if ctrl is not None and new_ctrl:
            ctrl.changed.connect(self._doc_changed)
        self._build_cards()
        self.refresh()

    def _build_cards(self) -> None:
        while self.cards_layout.count():
            it = self.cards_layout.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.cards = {}
        bodies = gl.detector_bodies(self.geo)
        for card in CARDS:
            w = _CardWidget(card, bodies)
            w.toggled.connect(self._on_toggle)
            w.editor.apply.connect(self._apply)
            if isinstance(w.editor, DoseSection):
                w.editor.showGridRequested.connect(self.showGridRequested)
                w.editor.fitRequested.connect(self.fitGridRequested)
            self.cards_layout.addWidget(w)
            self.cards[card.key] = w
        self.cards_layout.addStretch(1)
        for b in self.inner.findChildren(QPushButton):
            b.setAutoDefault(False)

    def _doc_changed(self) -> None:
        self._refresh_timer.start()

    def refresh(self) -> None:
        ctrl = self.ctrl
        if ctrl is None:
            return
        doc = ctrl.doc
        ok = doc.model.ok
        folder = ctrl.folder
        self.error_note.setVisible(not ok)
        if not ok:
            self.error_note.setText(f"<span style='color:{BAD_COLOR}'>El .in tiene errores de lectura (los dice el "
                                    "panel de mensajes): corríjalos para elegir aquí los resultados.</span>")
        note = resume_note(doc, folder) if ok else ""
        self.resume_note.setText(note)
        self.resume_note.setVisible(bool(note))
        for key, w in self.cards.items():
            card = w.card
            st = present(doc, key) if ok else ""
            w.check.blockSignals(True)
            w.check.setChecked(bool(st) or card.always)
            w.check.setEnabled(ok and not card.always)
            w.check.blockSignals(False)
            if key == "dosis" and not st and self.bounds is None:
                w.state.setText("<span style='color:#666'>hace falta la geometría</span>")
                w.check.setEnabled(False)
            else:
                w.state.setText(f"<span style='color:{OK_COLOR}'>{html.escape(st)}</span>" if st and not card.always
                                else "")
            on = bool(st) or card.always
            w.editor.setVisible(on and ok)
            if w.body_row is not None:
                w.body_row.setVisible(not st and ok)
            if on and ok:
                w.editor.set_context(doc, self.geo, folder)
        self._update_undo()

    def _update_undo(self) -> None:
        try:
            if self.undo_action is not None:
                self.btn_undo.setEnabled(self.undo_action.isEnabled())
            elif self.ctrl is not None:
                self.btn_undo.setEnabled(self.ctrl.stack.canUndo())
        except RuntimeError:  # la ventana ya no existe
            pass

    # -- cambios -------------------------------------------------------------------------------------------------
    def _edit(self, text: str, fn, card: str | None = None) -> bool:
        ctrl = self.ctrl
        if ctrl is None:
            return False
        try:
            ctrl.edit(text, fn)
        except ValueError as exc:
            if card in self.cards:
                self.cards[card].show_message(str(exc), ok=False)
            self.refresh()
            return False
        self._refresh_timer.stop()
        self.refresh()
        return True

    def _apply(self, text: str, fn) -> None:
        """Un cambio de un editor (fuera de la señal del widget que lo pide)."""
        self._deferred.defer(lambda: self._edit(text, fn))

    def _on_toggle(self, key: str, on: bool) -> None:
        card = CARDS_BY_KEY[key]
        w = self.cards[key]
        if on:
            kb = w.body.currentData() if card.body else None
            if card.body and kb is None:
                w.check.blockSignals(True)
                w.check.setChecked(False)
                w.check.blockSignals(False)
                w.show_message("No hay cuerpos con material que puedan ser detectores.", ok=False)
                return
            done: list[str] = []
            self._deferred.defer(lambda: self._edit(f"Pedir: {card.title}", lambda doc: done.append(add(
                doc, key, geo=self.geo, bounds=self.bounds, kb=kb)), key) and w.show_message(done[0] if done else ""))
        else:
            self._deferred.defer(lambda: self._edit(f"Quitar: {card.title}", lambda doc: remove(doc, key), key)
                                 and w.show_message(""))

    def flush_pending(self) -> None:
        self._deferred.flush()

    def _undo(self) -> None:
        """El deshacer de la ventana principal (si no, el del .in)."""
        self.flush_pending()
        if self.undo_action is not None:
            if self.undo_action.isEnabled():
                self.undo_action.trigger()
        elif self.ctrl is not None and self.ctrl.stack.canUndo():
            self.ctrl.stack.undo()
        self._refresh_timer.stop()
        self.refresh()

    # -- compatibilidad con la versión anterior (pruebas y el diálogo «Ejecutar») -----------------------------
    @property
    def checks(self) -> dict[str, QCheckBox]:
        return {k: w.check for k, w in self.cards.items()}


__all__ = ["CARDS", "Card", "FollowSection", "OutputsDialog", "add", "matter_bounds", "present",
           "remove"]
