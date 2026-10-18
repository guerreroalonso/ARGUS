"""La pestaña «Geometrías complejas» de la ventana principal (ADR-019).

Tres columnas: el texto del .geo (con los números de línea; al pulsar una línea se elige su bloque), las «Edition
Tabs» de PenGeomJar (con su «File Viewer») y la geometría en 2D y 3D (las vistas de la ventana, que se traen aquí).
Los botones hacen lo que hacen en PenGeomJar (Delete, Modify, New, Add/Duplicate, Cancel, Relabel, Manual edit,
End manual edit, elegir y mover bloques); cada cambio es un paso de deshacer de la ventana, solo se hace si PENGEOM
aceptaría la geometría y el .in abierto sigue a los cuerpos. Con «Vista previa al escribir», lo que se escribe en
una pestaña (o en «Manual edit») se ve ya en el texto y en la geometría, sin aplicarlo.
"""

from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.diagnostics import Severity
from ...core.numfmt import plural
from ...pengeom import blocks as bk
from ...pengeom.edit import EditError
from ...pengeom.model import CloneElement, EndElement, SurfaceElement
from ..geo_controller import preview_edit, preview_text
from .forms import TAB_CLASSES, TAB_TITLES, BlockTab
from .text_view import GeoTextView

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow

PREVIEW_DELAY_MS = 350
MANUAL_DELAY_MS = 600
VIEWER_HINT = ("Pulse «Update the File Viewer» (Alt+U) para ver aquí una copia del texto que se está editando.\n\n"
               "La copia es la del momento en que se pulsa y no cambia mientras se edita: sirve para tener delante "
               "cómo estaba.")
ERROR_STYLE = "color: #b3261e;"
NOTE_STYLE = "color: #2d5f2e;"
INFO_STYLE = "color: #5b6270;"


class _Preview:
    """Lo que se está enseñando sin aplicar: el texto, la geometría y dónde está el bloque."""

    def __init__(self, op: str, text: str, session, index: int | None, lines: tuple[int, int] | None):
        self.op = op  # «modify», «add» o «manual»
        self.text = text
        self.session = session
        self.index = index  # el bloque nuevo o cambiado, en el texto de la vista previa
        self.lines = lines


class ComplexGeometryWorkspace(QWidget):
    """El .geo bloque a bloque con el formato de PenGeomJar, junto a la geometría en vivo."""

    def __init__(self, host: MainWindow):
        super().__init__(host)
        self.host = host
        self.sel: int | None = None  # bloque elegido (índice en los elementos del documento)
        self.new_tab: str | None = None  # pestaña con «New» pulsado (hasta Add o Cancel)
        self.manual = False
        self.preview: _Preview | None = None
        self.active = False
        self._dirty: set[str] = set()  # pestañas con algo escrito sin aplicar
        self._syncing = False
        self._kb_cache: tuple[object, dict[int, int]] | None = None
        self._loaded = None  # lo que enseñaba la pestaña del bloque elegido al cargarlo

        # -- columna del texto ---------------------------------------------------------------------------------------
        self.text = GeoTextView()
        self.text.lineClicked.connect(self._on_line_clicked)
        self.text.textChanged.connect(self._on_text_edited)
        self.btn_relabel = QPushButton("Relabel")
        self.btn_relabel.setToolTip("Cambia todas las etiquetas por los números que les da PENGEOM (los de "
                                    "geometry.rep y los que usa penhmain en el .in), con sus referencias")
        self.btn_relabel.clicked.connect(self.relabel)
        self.btn_manual = QPushButton("Manual edit")
        self.btn_manual.setToolTip("Escribir directamente en el texto (la geometría se ve al momento si PENGEOM "
                                   "la acepta)")
        self.btn_manual.clicked.connect(self.toggle_manual)
        self.btn_end = QPushButton("End manual edit")
        self.btn_end.setToolTip("Aplica lo escrito a mano (un paso de deshacer)")
        self.btn_end.clicked.connect(lambda: self.end_manual())
        top = QHBoxLayout()
        top.addWidget(self.btn_relabel)
        top.addStretch(1)
        top.addWidget(self.btn_manual)
        top.addWidget(self.btn_end)
        nav = QHBoxLayout()
        self.nav_buttons: dict[str, QToolButton] = {}
        for key, text, tip, slot in (
                ("first", "⏮", "Select first Block (Alt+F)", self.first_block),
                ("prev", "▲", "Select previous Block (Alt+P)", self.previous_block),
                ("next", "▼", "Select next Block (Alt+N)", self.next_block),
                ("last", "⏭", "Select last Block (Alt+L)", self.last_block),
                ("up", "⇑", "Move block up (Alt+↑)", lambda: self.move_block(-1)),
                ("down", "⇓", "Move block down (Alt+↓)", lambda: self.move_block(+1))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self.nav_buttons[key] = b
            nav.addWidget(b)
            if key == "last":
                nav.addSpacing(10)
        nav.addStretch(1)
        self.position = QLabel("")
        self.position.setStyleSheet(INFO_STYLE)
        nav.addWidget(self.position)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        editor = QWidget()
        lay = QVBoxLayout(editor)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(top)
        lay.addWidget(self.text, 1)
        lay.addLayout(nav)
        lay.addWidget(self.status)
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        msg = QLabel("No geometry loaded")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet("font-size: 15pt; color: #5b6270;")
        el.addWidget(msg)
        sub = QLabel("Cree una geometría nueva (vacía) o abra un .geo o un .in para construirla aquí bloque a "
                     "bloque, con las pestañas de PenGeomJar.")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(sub)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_new_geo = QPushButton("Nueva geometría…")
        self.btn_new_geo.clicked.connect(lambda: self.host.new_geometry("empty"))
        self.btn_open = QPushButton("Abrir…")
        self.btn_open.clicked.connect(self.host.open_dialog)
        row.addWidget(self.btn_new_geo)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        el.addLayout(row)
        el.addStretch(2)
        self.left = QStackedWidget()
        self.left.addWidget(empty)
        self.left.addWidget(editor)

        # -- columna de las pestañas ----------------------------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tab_widgets: dict[str, BlockTab] = {}
        self._pages: dict[str, QScrollArea] = {}  # cada pestaña, en un área con barras (pantallas pequeñas)
        for cls in TAB_CLASSES:
            w = cls()
            self.tab_widgets[w.tab_id] = w
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setFrameShape(QScrollArea.Shape.NoFrame)
            page.setWidget(w)
            self._pages[w.tab_id] = page
            self.tabs.addTab(page, TAB_TITLES[w.tab_id])
            w.changed.connect(lambda t=w.tab_id: self._on_form_changed(t))
            w.problem.connect(lambda text: self._say(text, ERROR_STYLE))
            b = w.buttons
            b["delete"].clicked.connect(lambda checked=False, t=w.tab_id: self.delete(t))
            b["modify"].clicked.connect(lambda checked=False, t=w.tab_id: self.modify(t))
            b["new"].clicked.connect(lambda checked=False, t=w.tab_id: self.new(t))
            b["add"].clicked.connect(lambda checked=False, t=w.tab_id: self.add(t))
            b["cancel"].clicked.connect(lambda checked=False, t=w.tab_id: self.cancel(t))
        self.tab_widgets[bk.TAB_INCLUDE].browse.connect(self._browse_include)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        # File Viewer, como en PenGeomJar: una copia fija del texto que se edita, tomada al pulsar «Update the File
        # Viewer» (Alt+U), con sus números de línea, para tenerla delante mientras se cambia.
        self.file_viewer = GeoTextView()
        self.file_viewer.show_text(VIEWER_HINT)
        self.btn_update_viewer = QPushButton("Update the File Viewer")
        self.btn_update_viewer.setToolTip("Copia aquí el texto de la geometría tal como está ahora (con los cambios "
                                          "sin guardar); la copia no cambia mientras se edita (Alt+U)")
        self.btn_update_viewer.clicked.connect(self.update_file_viewer)
        self.viewer_info = QLabel("")
        self.viewer_info.setStyleSheet(INFO_STYLE)
        fv = QWidget()
        fl = QVBoxLayout(fv)
        fl.addWidget(self.file_viewer, 1)
        fr = QHBoxLayout()
        fr.addStretch(1)
        fr.addWidget(self.btn_update_viewer)
        fr.addWidget(self.viewer_info)
        fl.addLayout(fr)
        self.outer_tabs = QTabWidget()
        self.outer_tabs.addTab(fv, "File Viewer")
        self.outer_tabs.addTab(self.tabs, "Edition Tabs")
        self.outer_tabs.setCurrentIndex(1)
        self.chk_preview = QCheckBox("Vista previa al escribir")
        self.chk_preview.setToolTip("Lo que se escribe en una pestaña se ve ya en el texto (en naranja) y en la "
                                    "geometría, antes de pulsar Modify o Add")
        self.chk_preview.setChecked(str(self.host.settings.value("complex/preview", "true")) != "false")
        self.chk_preview.toggled.connect(self._on_preview_toggled)
        middle = QWidget()
        ml = QVBoxLayout(middle)
        ml.setContentsMargins(0, 6, 0, 6)
        ml.addWidget(self.outer_tabs, 1)
        ml.addWidget(self.chk_preview)
        self.middle = middle

        # -- columna de la geometría (las vistas de la ventana se ponen aquí al entrar) --------------------------------
        self.view_holder = QWidget()
        self.view_layout = QVBoxLayout(self.view_holder)
        self.view_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(middle)
        self.splitter.addWidget(self.view_holder)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setStretchFactor(2, 3)
        saved = self.host.settings.value("complex/splitter")
        if saved is not None:
            self.splitter.restoreState(saved)
        else:
            self.splitter.setSizes([520, 470, 520])
        self.splitter.splitterMoved.connect(
            lambda *a: self.host.settings.setValue("complex/splitter", self.splitter.saveState()))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.splitter)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(
            lambda: self._timed(self._preview_timer, PREVIEW_DELAY_MS, self.run_preview))
        self._manual_timer = QTimer(self)
        self._manual_timer.setSingleShot(True)
        self._manual_timer.setInterval(MANUAL_DELAY_MS)
        self._manual_timer.timeout.connect(
            lambda: self._timed(self._manual_timer, MANUAL_DELAY_MS, self._manual_preview))
        # Los atajos de PenGeomJar. Mover bloques (Alt+↑/↓), solo desde la columna del texto: en las pestañas,
        # Alt+↓ abre las listas desplegables (el tipo de cuádrica, DEG/RAD).
        for keys, slot, owner in (("Alt+F", self.first_block, self), ("Alt+P", self.previous_block, self),
                                  ("Alt+N", self.next_block, self), ("Alt+L", self.last_block, self),
                                  ("Alt+U", self.update_file_viewer, self),
                                  ("Alt+Up", lambda: self.move_block(-1), self.left),
                                  ("Alt+Down", lambda: self.move_block(+1), self.left)):
            sc = QShortcut(QKeySequence(keys), owner)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        self.text.cursorPositionChanged.connect(self._on_cursor_moved)
        self.refresh()

    # ------------------------------------------------------------------------------------------------------------------
    # Lo que hay abierto
    # ------------------------------------------------------------------------------------------------------------------
    @property
    def session(self):
        return self.host.session

    @property
    def doc(self):
        s = self.host.session
        return s.doc if s is not None else None

    def committed_text(self) -> str:
        ctrl = self.host.geo_ctrl
        if ctrl is not None:
            return ctrl.text
        doc = self.doc
        return "".join(doc.lines) if doc is not None else ""

    @staticmethod
    def _kb_in(session, el) -> int | None:
        """Número de cuerpo de ``el`` en la geometría de ``session`` (p. ej. la de una vista previa)."""
        if session is None or not session.ok or el is None:
            return None
        for b in session.geo.bodies:
            if b.document is session.doc and b.element is el and (b.origin != "clone"
                                                                    or isinstance(el, CloneElement)):
                return b.index
        return None

    def _kb_of(self, el) -> int | None:
        """Número de cuerpo (en la geometría compilada) del bloque ``el`` de un cuerpo, módulo, clon o CT."""
        s = self.host.session
        if s is None or not s.ok:
            return None
        if self._kb_cache is None or self._kb_cache[0] is not s.geo:
            index: dict[int, int] = {}
            for b in s.geo.bodies:
                if b.element is None or b.document is not s.doc:
                    continue
                if b.origin == "clone" and not (isinstance(b.element, CloneElement) and b.element is b.clone_element):
                    continue
                index.setdefault(id(b.element), b.index)
            self._kb_cache = (s.geo, index)
        return self._kb_cache[1].get(id(el))

    def _say(self, text: str, style: str = INFO_STYLE) -> None:
        """Lo que hay que decir: debajo del texto y, para que se vea junto a los botones, en la pestaña que se ve."""
        self.status.setStyleSheet(style)
        self.status.setText(text)
        cur = self.current_tab()
        for t in self.tab_widgets.values():
            t.show_message(text if t is cur else "", style)

    # -- lo escrito sin aplicar ---------------------------------------------------------------------------------------
    def pending_tab(self) -> str | None:
        """La pestaña con algo escrito sin aplicar (un bloque nuevo con New, o cambios sin Modify), o None."""
        if self.new_tab is not None:
            return self.new_tab
        return next(iter(sorted(self._dirty)), None)

    def confirm_discard(self, action: str | None = None) -> bool:
        """Si hay algo escrito sin aplicar en una pestaña, pregunta si se descarta (False: no se sigue). Con «Sí», se
        descarta (la pestaña vuelve a enseñar el bloque elegido)."""
        tab = self.pending_tab()
        if tab is None or self.doc is None:
            return True
        what = "un bloque nuevo" if self.new_tab is not None else "cambios"
        r = QMessageBox.question(
            self, "Geometrías complejas",
            f"En la pestaña «{TAB_TITLES[tab]}» hay {what} sin aplicar (se aplica"
            + (" con Add" if self.new_tab is not None else "n con Modify")
            + f").\n\n¿Descartar{'lo' if self.new_tab is not None else 'los'}"
            + (f" para {action}?" if action else "?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return False
        self._discard_pending()
        return True

    def _discard_pending(self) -> None:
        self._preview_timer.stop()
        self.drop_preview()
        self.new_tab = None
        self._dirty.clear()
        self.select_block(self.sel, scroll=False)
        self._say("Descartado lo escrito.", INFO_STYLE)

    # ------------------------------------------------------------------------------------------------------------------
    # Entrar, salir y lo que pasa fuera
    # ------------------------------------------------------------------------------------------------------------------
    def activate(self) -> None:
        self.active = True
        QTimer.singleShot(0, self._fit_middle)
        self.refresh()
        if self.sel is None and self.new_tab is None:
            self.first_block()  # algo elegido desde el principio (PenGeomJar no elige nada hasta que se pulsa)
        else:
            self._sync_views()

    def deactivate(self) -> None:
        """Se sale de la pestaña: lo escrito a mano se aplica (o se pregunta) y la vista previa se quita."""
        self.settle()
        self.drop_preview()
        self.host.set_highlight(set())
        self.host.view.set_surface_traces([])
        self.active = False

    def _fit_middle(self) -> None:
        """Las siete pestañas a la vista (en ventanas estrechas, no tras unas flechas): si la columna del centro es
        más estrecha que su barra, se ensancha a costa del texto y de las vistas (sin fijar un mínimo: se puede
        estrechar a mano y la ventana de siempre no se ensancha por esta pestaña)."""
        sizes = self.splitter.sizes()
        need = self.tabs.tabBar().sizeHint().width() + 16
        if len(sizes) != 3 or sizes[1] >= need or sum(sizes) <= 0:
            return
        extra = need - sizes[1]
        room = [max(0, sizes[0] - 200), max(0, sizes[2] - 200)]  # el texto y las vistas, con 200 px como poco
        if sum(room) <= 0:
            return
        take = [min(room[k], round(extra * room[k] / sum(room))) for k in (0, 1)]
        self.splitter.setSizes([sizes[0] - take[0], sizes[1] + take[0] + take[1], sizes[2] - take[1]])

    def settle(self) -> None:
        """Antes de guardar, deshacer, abrir o salir de la pestaña: «Manual edit» se termina y la vista previa se
        quita (lo escrito en las pestañas sigue ahí; se aplica con Modify o Add)."""
        if self.manual:
            self.end_manual(ask_to_continue=True)
        self._preview_timer.stop()
        self.drop_preview()

    def reset(self) -> None:
        """Otra geometría (o ninguna): nada elegido ni a medias."""
        self._preview_timer.stop()
        self._manual_timer.stop()
        if self.manual:
            self.manual = False
            self.text.set_editing(False)
        self.preview = None
        self.sel = None
        self.new_tab = None
        self._dirty.clear()
        for k in range(self.tabs.count()):
            self.tabs.setTabEnabled(k, True)
        self.text.mark_block(None, None)
        self.file_viewer.show_text(VIEWER_HINT)  # (como PenGeomJar al abrir un fichero)
        self.viewer_info.setText("")
        self._say("")
        self.refresh()
        if self.active:
            self.host.set_highlight(set())
            self.host.view.set_surface_traces([])
            self.first_block()

    def refresh(self) -> None:
        """La geometría ha cambiado (un cambio, deshacer, abrir otra): texto, mensajes y el bloque elegido. Con la
        pestaña oculta, se deja para cuando se vea (con geometrías grandes, el texto cuesta)."""
        self._kb_cache = None
        self._preview_timer.stop()
        if self.preview is not None and self.preview.op != "manual" and self.status.text().startswith("Vista previa"):
            self._say("")  # (la vista previa ya no está: su mensaje tampoco)
        self.preview = None
        if not self.active:
            self._stale = True
            return
        self._stale = False
        doc = self.doc
        self.left.setCurrentIndex(1 if doc is not None else 0)
        if doc is None:
            self.sel = None
            self.new_tab = None
            self._dirty.clear()
            for t in self.tab_widgets.values():
                t.clear()
            self._update_buttons()
            return
        if not self.manual:
            self.text.show_text(self.committed_text(), doc.newline)
        self._show_problems(self.session)
        names = getattr(self.host, "material_names", {}) or {}
        for t in self.tab_widgets.values():
            t.set_context(doc, names)
        # El bloque elegido: el mismo si sigue ahí (por su sitio y su nombre), si no, el del mismo nombre.
        if self.sel is not None:
            title = getattr(self, "_sel_title", None)
            blocks = bk.editable_blocks(doc)
            if not (self.sel in blocks and bk.block_title(doc.elements[self.sel]) == title):
                self.sel = next((i for i in blocks if bk.block_title(doc.elements[i]) == title), None)
        if self.new_tab is not None:  # escribiendo un bloque nuevo: sigue ahí
            self._mark_selected(scroll=False)
            self._sync_views()
        elif self._dirty and self.sel is not None and self._loaded == bk.form_of(doc.elements[self.sel], doc):
            self._mark_selected(scroll=False)  # el bloque no ha cambiado: lo escrito sin aplicar sigue ahí
            self._sync_views()
        else:
            had = bool(self._dirty)
            self.select_block(self.sel, scroll=False)
            if had:
                self._say("El bloque ha cambiado (p. ej. al deshacer): se ha vuelto a cargar y lo escrito sin aplicar "
                          "se ha descartado.", INFO_STYLE)
        if not bk.editable_blocks(doc) and self.new_tab is None and self.session is not None and self.session.ok:
            self._say("Geometría vacía: para empezar, pulse New en la pestaña Surface y escriba la primera superficie "
                      "(después, los cuerpos que limita, en Body). Cada bloque se añade con Add.", INFO_STYLE)
        self._update_buttons()

    def _show_problems(self, session) -> None:
        if session is None:
            self.text.set_problem_lines(())
            return
        path = session.doc.path
        errs = [d.line for d in session.diagnostics if d.severity == Severity.ERROR and d.line is not None
                and (d.file in (None, path))]
        warns = [d.line for d in session.diagnostics if d.severity == Severity.WARNING and d.line is not None
                 and (d.file in (None, path))]
        self.text.set_problem_lines(errs, warns)
        if not session.ok and not self.preview:
            first = next((d for d in session.diagnostics if d.severity == Severity.ERROR), None)
            ctrl = self.host.geo_ctrl
            undo = ctrl is not None and ctrl.stack.canUndo()
            self._say("PENGEOM no acepta la geometría: " + (self.where(first, path) + first.message if first else "")
                      + " Corríjala con «Manual edit»" + (" o deshaga el último cambio." if undo else "."),
                      ERROR_STYLE)

    @staticmethod
    def where(diag, path) -> str:
        """«línea 12: » (del texto que se ve) o «inc.geo, línea 8: » (de un fichero incluido)."""
        if diag is None or diag.line is None:
            return ""
        if diag.file not in (None, path):
            return f"{Path(str(diag.file)).name}, línea {diag.line + 1}: "
        return f"línea {diag.line + 1}: "

    def follow_body(self, kb: int | None) -> None:
        """Se ha elegido un cuerpo en las vistas: se elige su bloque (si no hay nada escrito sin aplicar)."""
        if not self.active or self._syncing or self.manual or self.new_tab is not None or self._dirty:
            return
        s = self.host.session
        if not kb or s is None or not s.ok or not 1 <= kb <= s.geo.nbodys:
            return
        b = s.geo.body(kb)
        el = b.clone_element if b.origin == "clone" and b.clone_element is not None else b.element
        i = next((k for k, e in enumerate(s.doc.elements) if e is el), None) if el is not None else None
        if i is not None and i != self.sel:
            self.select_block(i, sync=False)
            # Lo que enseñaba la superficie que estaba elegida (su traza y los cuerpos que limita) ya no vale.
            self.host.view.set_surface_traces([])
            self.host.set_highlight(set())

    # ------------------------------------------------------------------------------------------------------------------
    # Elegir un bloque
    # ------------------------------------------------------------------------------------------------------------------
    def _on_line_clicked(self, line: int) -> None:
        if self.manual or self.doc is None:
            return
        p = self.preview
        if p is not None and p.op != "manual":
            if p.lines is not None and p.lines[0] <= line <= p.lines[1]:
                self._remark()
                return  # el bloque de la vista previa (sin aplicar): se sigue escribiendo
            line = self._committed_line(line)
        i = bk.block_at_line(self.doc, line) if line is not None else None
        pending = self.pending_tab() is not None
        if i is None:
            if not pending:  # (la cabecera o END: nada elegido)
                self.select_block(None)
            else:
                self._remark()
            return
        if pending and (i == self.sel or not self.confirm_discard("elegir otro bloque")):
            self._remark()  # lo escrito sin aplicar se queda (el clic en el texto no lo tira)
            return
        self.select_block(i)

    def _remark(self) -> None:
        """Vuelve a marcar en el texto lo que se ve (un clic en el texto mueve el cursor y quita la marca)."""
        p = self.preview
        if p is not None and p.op != "manual" and p.lines is not None:
            self.text.mark_block(*p.lines, kind="pending", scroll=False)
        else:
            self._mark_selected(scroll=False)

    def _committed_line(self, line: int) -> int | None:
        """La línea del texto aplicado que corresponde a una de la vista previa (el bloque nuevo o cambiado no tiene
        una: se queda el elegido)."""
        p = self.preview
        if p is None or p.lines is None:
            return line
        first, last = p.lines
        if first <= line <= last:
            return None
        if line < first:
            return line
        doc = self.doc
        if p.op == "add":
            return line - (last - first + 1)
        old_first, old_last = bk.block_lines(doc.elements[self.sel]) if self.sel is not None else (first, last)
        return line - (last - first) + (old_last - old_first)

    def select_block(self, i: int | None, scroll: bool = True, sync: bool = True) -> None:
        """Elige el bloque ``i`` (o ninguno): su pestaña lo enseña y se marca en el texto y en la geometría."""
        doc = self.doc
        self._preview_timer.stop()
        self.drop_preview()
        self.new_tab = None
        self._dirty.clear()
        for k in range(self.tabs.count()):
            self.tabs.setTabEnabled(k, True)
        if doc is None or i is None or not 0 <= i < len(doc.elements) or isinstance(doc.elements[i], EndElement):
            i = None
        self.sel = i
        self._sel_title = bk.block_title(doc.elements[i]) if i is not None else None
        tab = bk.block_tab(doc.elements[i]) if i is not None else None
        for tid, w in self.tab_widgets.items():
            if tid != tab:
                w.clear()
        self._loaded = bk.form_of(doc.elements[i], doc) if tab is not None else None
        if tab is not None:
            self.tab_widgets[tab].set_form(self._loaded)
            with QSignalBlocker(self.tabs):
                self.tabs.setCurrentWidget(self._pages[tab])
            self.outer_tabs.setCurrentIndex(1)
        self._mark_selected(scroll=scroll)
        self._update_buttons()
        if sync:
            self._sync_views()

    def _mark_selected(self, scroll: bool = True) -> None:
        doc = self.doc
        if doc is None or self.sel is None or self.sel >= len(doc.elements):
            self.text.mark_block(None, None)
            self.position.setText("")
            return
        first, last = bk.block_lines(doc.elements[self.sel])
        self.text.mark_block(first, last, "selected", scroll=scroll)
        self.position.setText(f"Block starts: line {first + 1}")

    def _sync_views(self) -> None:
        """La geometría enseña el bloque elegido: el cuerpo seleccionado o, con una superficie, los cuerpos que limita
        (resaltados) y su traza en el corte."""
        doc = self.doc
        s = self.host.session
        el = doc.elements[self.sel] if doc is not None and self.sel is not None else None
        self._syncing = True
        try:
            if isinstance(el, SurfaceElement):
                self.host.select_body(0)
                self.host.set_highlight({k for k in (self._kb_of(u) for u in bk.users_of(doc, el)) if k})
                self._trace(s, el)
            else:
                self.host.set_highlight(set())
                self.host.view.set_surface_traces([])
                self.host.select_body((self._kb_of(el) if el is not None else None) or 0)
        finally:
            self._syncing = False

    def _trace(self, session, el, pending: bool = False) -> None:
        """La traza de la superficie ``el`` (de la sesión ``session``) en el corte."""
        if session is None or not session.ok or el is None:
            self.host.view.set_surface_traces([])
            return
        qs = [s.q for s in session.geo.surfaces if s.element is el and s.origin == "user"]
        star = "*" if el.starred else ""
        self.host.view.set_surface_traces([(q, f"SURFACE{star} {el.label.strip()}", pending) for q in qs[:1]])

    # ------------------------------------------------------------------------------------------------------------------
    # Botones
    # ------------------------------------------------------------------------------------------------------------------
    def current_tab(self) -> BlockTab | None:
        """La pestaña que se ve."""
        page = self.tabs.currentWidget()
        return page.widget() if isinstance(page, QScrollArea) else None

    def _selected_tab(self) -> str | None:
        doc = self.doc
        if doc is None or self.sel is None:
            return None
        return bk.block_tab(doc.elements[self.sel])

    def _update_buttons(self) -> None:
        has = self.doc is not None
        editable = has and not self.manual and self.host.geo_ctrl is not None and self.host.session is not None \
            and self.host.session.ok
        sel_tab = self._selected_tab()
        for tid, w in self.tab_widgets.items():
            b = w.buttons
            new_mode = self.new_tab == tid
            loaded = sel_tab == tid and self.new_tab is None
            b["delete"].setEnabled(editable and loaded)
            b["modify"].setEnabled(editable and loaded)
            b["new"].setEnabled(editable and self.new_tab is None)
            b["add"].setEnabled(editable and (new_mode or loaded))
            b["add"].setText("Add" if new_mode or not loaded else "Duplicate")
            b["add"].setToolTip("Añade el bloque nuevo detrás del bloque elegido" if new_mode or not loaded else
                                "Añade una copia de lo que hay en la pestaña detrás del bloque elegido (con otra "
                                "etiqueta si la suya ya existe)")
            b["cancel"].setEnabled(has and not self.manual and (new_mode or tid in self._dirty))
            w.set_editable(editable and (new_mode or loaded))
        for k in range(self.tabs.count()):
            tid = self.tabs.widget(k).widget().tab_id
            self.tabs.setTabEnabled(k, not self.manual and (self.new_tab is None or self.new_tab == tid))
        self.tabs.setVisible(not self.manual)
        for b in self.nav_buttons.values():
            b.setEnabled(has and not self.manual and self.new_tab is None)
        self.btn_relabel.setEnabled(editable and self.new_tab is None)
        self.btn_manual.setEnabled(has and self.new_tab is None and self.host.session is not None)
        self.btn_manual.setText("Cancel edit" if self.manual else "Manual edit")
        self.btn_manual.setToolTip("Descarta lo escrito a mano" if self.manual else
                                   "Escribir directamente en el texto (la geometría se ve al momento si PENGEOM la "
                                   "acepta)")
        self.btn_end.setEnabled(self.manual)

    def _form(self, tab: str):
        w = self.tab_widgets[tab]
        try:
            return w.form()
        except bk.BlockError as exc:
            self._say(str(exc), ERROR_STYLE)
            w.focus_field(exc.field)
            return None

    def _edit(self, text: str, fn, renames: dict | None = None):
        """Un cambio de la geometría como paso de deshacer de la ventana (None si no se ha hecho)."""
        self._preview_timer.stop()
        self.drop_preview()
        self._errors_before = {d.message for d in getattr(self.host, "case_errors", [])}
        try:
            return self.host.edit_geometry(text, fn, renames=renames, errors=self._edit_error)
        finally:
            self._update_buttons()

    def _new_case_errors(self) -> list[str]:
        """Errores del caso que ha traído el último cambio (p. ej. un material que el .in no declara): el panel de
        mensajes no se ve en esta pestaña."""
        before = getattr(self, "_errors_before", set())
        return [d.message for d in getattr(self.host, "case_errors", []) if d.message not in before]

    def _edit_error(self, exc: Exception) -> None:
        text = str(exc)
        tab = self.new_tab or self._selected_tab()
        if "ya está dentro de" in text and tab == bk.TAB_MODULE and self.new_tab is None:
            text += (" Un Duplicate copia también las hijas de la tabla, que ya están en el módulo de antes: para "
                     "copiar un módulo con todo lo que tiene dentro, use un CLONE (pestaña Clone).")
        self._say(f"No se ha hecho: {text}", ERROR_STYLE)
        field = getattr(exc, "field", None)
        if field and tab:
            self.tab_widgets[tab].focus_field(field)

    def _report(self, what: str, res) -> None:
        notes = list(getattr(res, "notes", []) or [])
        follow = list(getattr(self.host, "last_follow_notes", []) or [])
        text = what + (" " + " ".join(notes) if notes else "")
        if follow:
            text += " En el .in: " + "; ".join(follow) + "."
        new = self._new_case_errors()
        if new:  # hecho, pero penhmain no podría simular así: se dice aquí (el panel de mensajes no se ve)
            text += " Atención: " + new[0] + (f" (y {len(new) - 1} más en el panel de mensajes)" if len(new) > 1
                                               else "")
            self._say(text, ERROR_STYLE)
            return
        self._say(text, NOTE_STYLE)

    def modify(self, tab: str) -> None:
        i = self.sel
        if i is None or self._selected_tab() != tab:
            return
        form = self._form(tab)
        if form is None:
            return
        title = bk.block_title(self.doc.elements[i])
        renames: dict[str, str] = {}
        before = self.committed_text()

        def fn(doc, geo):
            res = bk.modify_block(doc, i, form, geo)
            renames.update(res.renames)
            return res

        # Un Modify también puede hacer desaparecer cuerpos que nombra el .in (p. ej. el módulo de un CLONE, el
        # fichero de un INCLUDE): como al borrar, se pregunta antes.
        if not self._confirm_in_loss(fn, "Modify", f"Reescribir {title}", renames):
            return
        res = self._edit(f"Modify {title}", fn, renames)
        if res is not None:
            self.select_block(res.index)
            if self.committed_text() == before:
                self._say("Sin cambios: el bloque ya estaba así.", INFO_STYLE)
            else:
                self._report(f"{bk.block_title(self.doc.elements[res.index])} reescrito.", res)

    def add(self, tab: str) -> None:
        form = self._form(tab)
        if form is None:
            return
        after = self.sel
        strict = self.new_tab == tab  # un bloque nuevo (New): su etiqueta, la que se ha escrito (no se cambia)

        def fn(doc, geo):
            return bk.add_block(doc, after, form, geo, strict_label=strict)

        what = TAB_TITLES[tab]
        res = self._edit(f"Add {what}", fn)
        if res is not None:
            self.new_tab = None
            self.select_block(res.index)
            self._report(f"Añadido {bk.block_title(self.doc.elements[res.index])}.", res)

    def delete(self, tab: str) -> None:
        i = self.sel
        if i is None or self._selected_tab() != tab:
            return
        title = bk.block_title(self.doc.elements[i])

        def fn(doc, geo):
            return bk.delete_block(doc, i)

        if not self._confirm_in_loss(fn, "Delete", f"Borrar {title}"):
            return
        blocks = self._blocks()
        before = [k for k in blocks if k < i]
        res = self._edit(f"Delete {title}", fn)
        if res is not None:
            # Queda elegido el bloque de antes: un Add después va donde estaba el borrado.
            self.select_block(before[-1] if before else None)
            self._report(f"Borrado {title}.", res)

    def _confirm_in_loss(self, fn, title: str, doing: str, renames: dict | None = None) -> bool:
        """Si el cambio ``fn`` hace desaparecer cuerpos que nombra el .in (el del bloque, los de un módulo, las copias
        de un clon, los de un fichero incluido), sus líneas se quitarán (y las de los detectores que se queden sin
        cuerpos), como al eliminar un cuerpo en ARGUS: se dicen y se pregunta antes."""
        host = self.host
        s = host.session
        if host.input is None or s is None or not s.ok or not host._input_uses_geometry():
            return True
        try:
            _, new = preview_edit(s, fn)
        except EditError:
            return True  # no se puede hacer: lo dirá al intentarlo
        lost = host.lost_in_lines(s.geo, new.geo, renames)
        if not lost:
            return True
        shown = "\n".join(f"    {t.strip()}" for t in lost[:10]) + ("\n    …" if len(lost) > 10 else "")
        r = QMessageBox.question(
            self, title, f"{doing} quita del .in {plural(len(lost), 'línea', 'líneas')} (las de los cuerpos que "
            f"desaparecen y las de los detectores que se quedan sin cuerpos):\n\n{shown}\n\nSe quitan en el mismo paso "
            "de deshacer. ¿Seguir?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes

    def new(self, tab: str) -> None:
        """New: la pestaña vacía (con una etiqueta libre propuesta); las demás, bloqueadas hasta Add o Cancel."""
        if not self.confirm_discard("empezar un bloque nuevo"):
            return
        self._preview_timer.stop()
        self.drop_preview()
        self.new_tab = tab
        self._dirty.discard(tab)
        w = self.tab_widgets[tab]
        form = bk.empty_form(tab)
        s = self.host.session
        try:
            label = bk.free_label(self.doc, tab, s.geo if s is not None and s.ok else None)
        except EditError:
            label = ""
        if hasattr(form, "label"):
            form.label = label
        w.set_form(form)
        self._update_buttons()
        self._say(f"Bloque nuevo: escriba los campos de «{TAB_TITLES[tab]}» y pulse Add (irá detrás del bloque "
                  "elegido); Cancel para dejarlo.", INFO_STYLE)
        if hasattr(w, "label"):
            w.label.setFocus()
            w.label.selectAll()

    def cancel(self, tab: str) -> None:
        self.select_block(self.sel, scroll=False)
        self._say("Descartado lo escrito.", INFO_STYLE)

    def _on_form_changed(self, tab: str) -> None:
        self._dirty.add(tab)
        self.tab_widgets[tab].buttons["cancel"].setEnabled(True)
        self._schedule_preview()

    def _on_tab_changed(self, index: int) -> None:
        """Otra pestaña: se quita la vista previa (es de la pestaña de antes)."""
        self._preview_timer.stop()
        self.drop_preview()

    def _browse_include(self) -> None:
        s = self.host.session
        folder = str(s.base_dir) if s is not None else ""
        path, _ = QFileDialog.getOpenFileName(self, "Fichero incluido", folder, "Todos los ficheros (*)")
        if not path:
            return
        p = Path(path)
        if s is not None and p.parent.resolve() != Path(s.base_dir).resolve():
            QMessageBox.information(self, "ARGUS", "PENGEOM busca los ficheros incluidos en la carpeta de trabajo de "
                                                   f"penhmain ({s.base_dir}): copie allí el fichero y elíjalo de "
                                                   "nuevo.")
            return
        if len(p.name) > bk.MAX_FILENAME:
            QMessageBox.information(self, "ARGUS", f"«{p.name}» tiene {len(p.name)} caracteres y PENGEOM solo lee "
                                                   f"{bk.MAX_FILENAME} (CHARACTER*12): cámbiele el nombre al fichero "
                                                   "y elíjalo de nuevo.")
            return
        w = self.tab_widgets[bk.TAB_INCLUDE]
        w.file.setText(p.name)
        self._on_form_changed(bk.TAB_INCLUDE)

    # ------------------------------------------------------------------------------------------------------------------
    # Navegar y mover bloques; Relabel
    # ------------------------------------------------------------------------------------------------------------------
    def _blocks(self) -> list[int]:
        doc = self.doc
        return bk.editable_blocks(doc) if doc is not None else []

    def first_block(self) -> None:
        if self._can_navigate() and self._blocks():
            self._go(self._blocks()[0])

    def last_block(self) -> None:
        if self._can_navigate() and self._blocks():
            self._go(self._blocks()[-1])

    def next_block(self) -> None:
        blocks = self._blocks()
        if not self._can_navigate() or not blocks:
            return
        nxt = [i for i in blocks if self.sel is None or i > self.sel]
        self._go(nxt[0] if nxt else blocks[-1])

    def previous_block(self) -> None:
        blocks = self._blocks()
        if not self._can_navigate() or not blocks:
            return
        prv = [i for i in blocks if self.sel is not None and i < self.sel]
        self._go(prv[-1] if prv else blocks[0])

    def _go(self, i: int) -> None:
        if i == self.sel and self.pending_tab() is not None:
            return  # ya está elegido (y lo escrito sin aplicar se queda)
        if self.confirm_discard("elegir otro bloque"):
            self.select_block(i)

    def _can_navigate(self) -> bool:
        return self.doc is not None and not self.manual and self.new_tab is None

    def move_block(self, delta: int) -> None:
        """Move block up/down (``delta`` -1 o +1). Si eso cambiara la geometría (no solo la numeración), se
        pregunta: PENGEOM aplica las transformaciones de los módulos en el orden en que se definen, y con
        superficies compartidas el orden puede cambiar dónde quedan."""
        from ...pengeom.identity import geometry_change, renumber_map

        i = self.sel
        if i is None or not self._can_navigate():
            return
        blocks = self._blocks()
        title = bk.block_title(self.doc.elements[i])
        if i in blocks and not 0 <= blocks.index(i) + delta < len(blocks):
            self._say(f"{title} ya es el {'primer' if delta < 0 else 'último'} bloque.", INFO_STYLE)
            return
        if not self.confirm_discard("mover el bloque"):
            return
        s = self.host.session

        def fn(doc, geo):
            return bk.move_block(doc, i, delta)

        if s is not None and s.ok:
            try:
                _, new = preview_edit(s, fn)
            except EditError:
                new = None  # lo dirá al intentarlo
            change = geometry_change(s.geo, new.geo, renumber_map(s.geo, new.geo)) if new is not None else None
            if change is not None:
                r = QMessageBox.question(
                    self, "Move block",
                    f"Mover {title} cambia la geometría, no solo el orden: {change} (PENGEOM aplica las "
                    "transformaciones de los módulos en el orden en que se definen, y con superficies compartidas el "
                    "orden importa).\n\n¿Moverlo igualmente?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if r != QMessageBox.StandardButton.Yes:
                    return
        res = self._edit(f"Move block {'up' if delta < 0 else 'down'}: {title}", fn)
        if res is not None:
            self.select_block(res.index)
            self._report(f"{title}: {'subido' if delta < 0 else 'bajado'} un bloque.", res)

    def relabel(self) -> None:
        s = self.host.session
        if s is None or not s.ok or self.host.geo_ctrl is None or self.manual:
            return
        if not self.confirm_discard("hacer Relabel"):
            return
        r = QMessageBox.question(
            self, "Relabel",
            "Relabel cambia las etiquetas de las superficies, los cuerpos, los módulos, los clones y el CT por los "
            "números que les da PENGEOM (los de geometry.rep, y los que usa penhmain en el .in), con todas sus "
            "referencias. Los clones y los INCLUDE se quedan como están (sus copias cuentan en la numeración, así "
            "que puede haber saltos). El .in abierto sigue a los cuerpos.\n\nSe puede deshacer. ¿Seguir?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        if r != QMessageBox.StandardButton.Yes:
            return
        renames: dict[str, str] = {}

        def fn(doc, geo):
            res = bk.relabel(doc, geo)
            renames.update(res.renames)
            return res

        def same_geometry(before, after) -> None:  # Relabel solo cambia etiquetas: la geometría tiene que ser la misma
            from ...pengeom.identity import geometry_change, renumber_map

            change = geometry_change(before, after, renumber_map(before, after, renames))
            if change is not None:
                raise EditError(f"Relabel cambiaría la geometría ({change}).")

        fn.check = same_geometry
        sel = self.sel
        ctrl = self.host.geo_ctrl
        before = ctrl.text if ctrl is not None else None
        res = self._edit("Relabel", fn, renames)
        if res is not None:
            self.select_block(sel, scroll=False)
            if ctrl is not None and ctrl.text == before:  # ya lo estaban: no se ha cambiado nada
                self._say("Las etiquetas ya son los números de PENGEOM: no ha cambiado nada.", NOTE_STYLE)
            else:
                self._report("Etiquetas renumeradas con los números de PENGEOM.", res)

    # ------------------------------------------------------------------------------------------------------------------
    # Manual edit
    # ------------------------------------------------------------------------------------------------------------------
    def toggle_manual(self) -> None:
        if self.manual:
            self.cancel_manual()
        else:
            self.start_manual()

    def start_manual(self) -> None:
        if self.doc is None or not self.confirm_discard("escribir a mano"):
            return
        self._preview_timer.stop()
        self.drop_preview()
        self.new_tab = None
        self._dirty.clear()
        from ...pengeom.parser import split_lines

        self.manual = True
        self.text.mark_block(None, None)
        self.position.setText("")
        self.text.show_text(self.committed_text(), self.doc.newline)
        self.text.begin_edit(split_lines(self.committed_text()))
        self.text.set_editing(True)
        self.text.setFocus()
        self._syncing = True
        try:  # nada elegido mientras se escribe a mano
            self.host.select_body(0)
            self.host.set_highlight(set())
            self.host.view.set_surface_traces([])
        finally:
            self._syncing = False
        self._update_buttons()
        self.host._update_actions()  # (las ediciones de la geometría desde otros sitios, bloqueadas)
        self._on_cursor_moved()
        self._say("Manual edit: escriba en el texto; la geometría se ve al momento si PENGEOM la acepta. «End manual "
                  "edit» lo aplica (un paso de deshacer); «Cancel edit» lo descarta. Mientras tanto no se edita la "
                  "geometría desde otros sitios.", INFO_STYLE)

    def _on_cursor_moved(self) -> None:
        """Escribiendo a mano, dónde está el cursor (las columnas importan en un .geo), como en PenGeomJar."""
        if not self.manual:
            return
        cur = self.text.textCursor()
        self.position.setText(f"Cursor: line {cur.blockNumber() + 1}, column {cur.positionInBlock() + 1}")

    def _on_text_edited(self) -> None:
        if self.manual and not self.text.isReadOnly():
            self._manual_timer.start()

    def _manual_preview(self) -> None:
        if not self.manual or self.host.session is None:
            return
        if self.host.geo_ctrl is not None and self.host.geo_ctrl.in_live:
            self._manual_timer.start()
            return
        text = self.text.file_text()
        try:
            s = preview_text(self.host.session, text)
        except Exception as exc:  # noqa: BLE001 - lo que no se pueda ni leer se dice y ya
            self._say(f"No se puede leer el texto: {exc}", ERROR_STYLE)
            return
        self._show_problems(s)
        if s.ok:
            self.preview = _Preview("manual", text, s, None, None)
            self.host.show_preview(s)
            self._say("Manual edit: PENGEOM acepta el texto (se ve en la geometría). «End manual edit» lo aplica.",
                      NOTE_STYLE)
        else:
            if self.preview is not None:
                self.preview = None
                self.host.show_preview(None)
            first = next((d for d in s.diagnostics if d.severity == Severity.ERROR), None)
            where = self.where(first, s.doc.path)
            self._say("Manual edit: PENGEOM no aceptaría el texto — " + (where + first.message if first else "")
                      + " (la geometría sigue como estaba).", ERROR_STYLE)

    def end_manual(self, ask_to_continue: bool = True) -> None:
        """End manual edit: el texto escrito pasa a ser la geometría (si PENGEOM no lo acepta, se pregunta)."""
        if not self.manual:
            return
        self._manual_timer.stop()
        text = self.text.file_text()
        s = self.host.session
        if s is not None and text != self.committed_text():
            check = preview_text(s, text)
            if not check.ok:
                first = next((d for d in check.diagnostics if d.severity == Severity.ERROR), None)
                where = self.where(first, check.doc.path)
                msg = (where[:1].upper() + where[1:] + first.message) if first is not None else "error desconocido"
                if ask_to_continue and not self.ask_apply_broken(msg):
                    return
            line = self.text.textCursor().blockNumber()
            self._leave_manual()
            self._errors_before = {d.message for d in getattr(self.host, "case_errors", [])}
            self.host.apply_geometry_text(text, "Manual edit")
            self.refresh()
            if self.session is not None and self.session.ok:
                self.select_block(bk.block_at_line(self.doc, line))  # como PenGeomJar: el bloque del cursor
                self._report("Aplicado lo escrito a mano (un paso de deshacer).", None)
        else:
            self._leave_manual()
            self.refresh()

    def ask_apply_broken(self, msg: str) -> bool:
        """¿Aplicar un texto que PENGEOM no acepta? (False: seguir editando)."""
        box = QMessageBox(QMessageBox.Icon.Warning, "End manual edit",
                          f"PENGEOM no aceptaría la geometría:\n\n{msg}\n\nSi se aplica así, la geometría no se verá "
                          "ni se podrá editar con las pestañas hasta corregirla.", parent=self)
        keep = box.addButton("Seguir editando", QMessageBox.ButtonRole.RejectRole)
        apply = box.addButton("Aplicar así", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep)
        box.exec()
        return box.clickedButton() is apply

    def cancel_manual(self) -> None:
        if not self.manual:
            return
        self._manual_timer.stop()
        self._leave_manual()
        self.refresh()
        self._say("Descartado lo escrito a mano.", INFO_STYLE)

    def _leave_manual(self) -> None:
        self.manual = False
        self.text.set_editing(False)
        self.position.setText("")
        if self.preview is not None:
            self.preview = None
            self.host.show_preview(None)
        self._update_buttons()
        self.host._update_actions()

    # ------------------------------------------------------------------------------------------------------------------
    # Vista previa
    # ------------------------------------------------------------------------------------------------------------------
    def _on_preview_toggled(self, on: bool) -> None:
        self.host.settings.setValue("complex/preview", "true" if on else "false")
        if on:
            self._schedule_preview()
        else:
            self._preview_timer.stop()
            self.drop_preview()

    def _schedule_preview(self) -> None:
        if self.chk_preview.isChecked() and not self.manual and self.active:
            self._preview_timer.start()

    @staticmethod
    def _timed(timer: QTimer, base_ms: int, fn) -> None:
        """Una vista previa; si tarda (geometrías de miles de líneas), la siguiente espera más a que se deje de
        escribir (hasta 3 s): así escribir no se interrumpe a cada pausa."""
        t0 = time.perf_counter()
        try:
            fn()
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            timer.setInterval(int(min(3000.0, max(base_ms, 1.5 * ms))))

    def preview_active(self) -> bool:
        return self.preview is not None

    def run_preview(self) -> None:
        """Lo escrito en la pestaña, sin aplicarlo: en el texto (en naranja) y en la geometría."""
        s = self.host.session
        w = self.current_tab()
        if s is None or not s.ok or self.manual or not isinstance(w, BlockTab):
            return
        if self.host.geo_ctrl is not None and self.host.geo_ctrl.in_live:
            self._schedule_preview()  # arrastrando un módulo: la geometría de ahora es la del arrastre (después)
            return
        tab = w.tab_id
        if self.new_tab == tab:
            op = "add"
        elif self.sel is not None and self._selected_tab() == tab and tab in self._dirty:
            op = "modify"
        else:
            return
        try:
            form = w.form()
        except bk.BlockError as exc:
            self.drop_preview()
            self._say(f"Vista previa: {exc}", INFO_STYLE)
            return
        sel = self.sel
        out: list = []

        def fn(doc, geo):
            res = bk.add_block(doc, sel, form, geo) if op == "add" else bk.modify_block(doc, sel, form, geo)
            out.append(res)
            return res

        try:
            text, new = preview_edit(s, fn)
        except EditError as exc:
            self.drop_preview()
            self._say(f"Vista previa: no se podría hacer — {exc}", ERROR_STYLE)
            return
        res = out[-1]
        el = new.doc.elements[res.index] if res.index is not None and res.index < len(new.doc.elements) else None
        lines = bk.block_lines(el) if el is not None else None
        self.preview = _Preview(op, text, new, res.index, lines)
        self.text.show_text(text, new.doc.newline)
        self.text.mark_block(*(lines or (None, None)), kind="pending")
        self._show_problems(new)
        self.host.show_preview(new)
        if isinstance(el, SurfaceElement):
            self._trace(new, el, pending=True)
        else:
            self.host.view.set_surface_traces([])
        how = ("pulse Add para añadirlo" if op == "add" else
               "pulse Modify para aplicarlo a este bloque (o Duplicate para añadirlo como un bloque nuevo)")
        self._say(f"Vista previa (sin aplicar): {how}, o Cancel para descartarlo."
                  + (" " + " ".join(res.notes) if res.notes else ""), NOTE_STYLE)

    def drop_preview(self) -> None:
        """Quita la vista previa: el texto y la geometría vuelven a lo aplicado."""
        p = self.preview
        if p is None:
            return
        self.preview = None
        self.host.show_preview(None)
        if p.op != "manual" and self.doc is not None:
            self.text.show_text(self.committed_text(), self.doc.newline)
            self._show_problems(self.session)
            self._mark_selected(scroll=False)
            self._sync_views()

    def preview_body_map(self) -> dict[int, int | None] | None:
        """Número en la geometría aplicada de cada cuerpo de la vista previa (None: el nuevo)."""
        from ...pengeom.identity import renumber_map

        p, s = self.preview, self.host.session
        if p is None or s is None or not s.ok or not p.session.ok:
            return None
        return renumber_map(p.session.geo, s.geo)

    # ------------------------------------------------------------------------------------------------------------------
    # File Viewer
    # ------------------------------------------------------------------------------------------------------------------
    def update_file_viewer(self) -> None:
        """«Update the File Viewer»: copia el texto que se edita (con los cambios sin guardar) al File Viewer."""
        if self.doc is None:
            self.file_viewer.show_text(VIEWER_HINT)
            self.viewer_info.setText("")
            return
        self.file_viewer.show_text(self.committed_text(), self.doc.newline)
        self.file_viewer.verticalScrollBar().setValue(0)
        now = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        self.viewer_info.setText(f"Last update of File Viewer: {now}")
