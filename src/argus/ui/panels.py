"""Paneles laterales: árbol de la geometría, propiedades, texto, mensajes y materiales."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPixmap, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
)

from ..core.diagnostics import Severity
from ..core.fortran_io import display_record
from ..pengeom.compiler import body_title
from ..pengeom.model import SurfaceElement
from ..pengeom.quadric import reduced_name
from ..render.colors import ENCLOSURE_COLOR, VOID_COLOR, body_color, material_color

ROLE_BODY = Qt.ItemDataRole.UserRole + 1
MAX_PARINP_SHOWN = 12  # valores PARINP que se escriben en el resumen de la geometría

__all__ = ["body_title"]  # se importa desde aquí en otros módulos de la interfaz


def swatch(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def body_swatch_color(geo, kb: int, mode: str = "material", custom: dict | None = None) -> str:
    b = geo.body(kb)
    if b.origin == "enclosure":
        return ENCLOSURE_COLOR
    if mode == "body":
        return body_color(kb) if b.material != 0 else VOID_COLOR
    return (custom or {}).get(b.material) or material_color(b.material)


class GeometryTree(QTreeWidget):
    """Árbol de módulos y cuerpos (hijas de cada módulo, como en PENGEOM)."""

    bodySelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Elemento", "Material"])
        self.setColumnWidth(0, 230)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.itemSelectionChanged.connect(self._on_selection)
        self._items: dict[int, QTreeWidgetItem] = {}
        self._geo = None
        self._blocking = False
        self.material_names: dict[int, str] = {}

    def set_material_names(self, names: dict[int, str]) -> None:
        """Nombre de cada material (fichero .mat y descripción) para la ayuda emergente."""
        self.material_names = dict(names)
        for kb, item in self._items.items():
            m = self._geo.body(kb).material if self._geo is not None else 0
            item.setToolTip(1, self.material_names.get(m, ""))

    def set_geometry(self, geo, mode: str = "material", custom: dict | None = None) -> None:
        self.clear()
        self._items.clear()
        self._geo = geo
        if geo is None or not geo.ok:
            return
        root = geo.nbodys
        self._add(None, root, mode, custom, depth=0)
        self.expandToDepth(1)

    def _add(self, parent, kb: int, mode, custom, depth: int) -> None:
        geo = self._geo
        b = geo.body(kb)
        item = QTreeWidgetItem([body_title(geo, kb), "—" if b.material == 0 else
                                ("CT" if b.material == 9999 else str(b.material))])
        item.setData(0, ROLE_BODY, kb)
        item.setIcon(0, swatch(body_swatch_color(geo, kb, mode, custom)))
        desc = " ".join((b.element.description if b.element is not None else "").split())
        if desc:  # el comentario del .geo (p. ej. la fila y la columna de un detector de una celda)
            item.setToolTip(0, f"{body_title(geo, kb)} · {desc}")
        if b.material in self.material_names:
            item.setToolTip(1, self.material_names[b.material])
        if b.is_module:
            f = item.font(0)
            f.setBold(True)
            item.setFont(0, f)
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._items.setdefault(kb, item)
        if b.is_module and depth < 60:
            seen = set()
            for d in b.daughters:
                if d == kb or d in seen:
                    continue
                seen.add(d)
                self._add(item, d, mode, custom, depth + 1)

    def select_body(self, kb: int | None) -> None:
        self._blocking = True
        try:
            self.clearSelection()
            if kb and kb in self._items:
                it = self._items[kb]
                it.setSelected(True)
                self.scrollToItem(it)
        finally:
            self._blocking = False

    def _on_selection(self) -> None:
        if self._blocking:
            return
        items = self.selectedItems()
        kb = items[0].data(0, ROLE_BODY) if items else 0
        self.bodySelected.emit(int(kb or 0))


class PropertiesPanel(QTextBrowser):
    """Propiedades del cuerpo o módulo seleccionado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.show_body(None, None)

    material_names: dict[int, str] = {}

    def show_body(self, session, kb: int | None) -> None:
        if session is None or not session.ok:
            self.setHtml("<p style='color:#777'>Sin geometría.</p>")
            return
        geo = session.geo
        if not kb:
            self.setHtml(self._summary(session))
            return
        b = geo.body(kb)
        rows = []
        rows.append(("Índice PENGEOM", str(kb)))
        lab = geo.balias(kb).strip()
        rows.append(("Etiqueta", html.escape(lab) if lab else "<i>sin etiqueta propia</i>"))
        kind = "envolvente" if b.origin == "enclosure" else ("caja CT" if b.origin == "ct" else
                                                            ("módulo" if b.is_module else "cuerpo"))
        if b.origin == "clone":
            kind += f" (clon del n.º {b.clone_of})"
        rows.append(("Tipo", kind))
        mat = "0 (vacío)" if b.material == 0 else ("CT (vóxeles)" if b.material == 9999 else str(b.material))
        if b.material in self.material_names:
            mat += f" · {html.escape(self.material_names[b.material])}"
        rows.append(("Material", mat))
        rows.append(("Madre", str(b.mother) if b.mother else "—"))
        if b.is_module:
            ds = [d for d in dict.fromkeys(b.daughters) if d != kb]
            rows.append(("Hijas", ", ".join(map(str, ds)) or "—"))
        else:
            rows.append(("Cuerpos limitantes", ", ".join(map(str, b.limiting)) or "—"))
        if b.element is not None and b.document is not None:
            name = b.document.path or ""
            rows.append(("Definido en", f"{html.escape(name.split('/')[-1].split(chr(92))[-1])}, línea "
                                        f"{b.element.first_line + 1}"))
        out = [f"<h3 style='margin:4px 0'>{html.escape(body_title(geo, kb))}</h3><table cellspacing=3>"]
        for k, v in rows:
            out.append(f"<tr><td style='color:#666'>{k}</td><td><b>{v}</b></td></tr>")
        out.append("</table>")
        own = list(b.own_surfaces())
        if own:
            out.append("<h4 style='margin:8px 0 2px 0'>Superficies limitantes</h4>")
            out.append("<table cellspacing=2><tr style='color:#666'><td>Sup.</td><td>Etiqueta</td>"
                       "<td>Tipo</td><td>Lado</td></tr>")
            for ks, side in own:
                s = geo.surface(ks)
                slab = s.label.strip() if s.origin == "user" else ("clon" if s.origin == "clone" else "—")
                kind = "?"
                el = s.element
                if isinstance(el, SurfaceElement):
                    kind = "implícita" if el.implicit else reduced_name(el.indices)
                elif s.origin == "ct":
                    kind = "plano (CT)"
                elif s.origin == "enclosure":
                    kind = "esfera envolvente"
                sgn = "−1 (dentro, F&lt;0)" if side < 0 else "+1 (fuera, F&gt;0)"
                out.append(f"<tr><td>{ks}</td><td>{html.escape(slab)}</td><td>{kind}</td><td>{sgn}</td></tr>")
            out.append("</table>")
        self.setHtml("".join(out))

    def _summary(self, session) -> str:
        geo = session.geo
        nb = sum(1 for b in geo.bodies if not b.is_module and b.origin != "enclosure")
        nm = sum(1 for b in geo.bodies if b.is_module and b.origin != "enclosure")
        mats = geo.materials()
        rows = [("Fichero", html.escape(session.name)),
                ("Título", html.escape(session.doc.title)),
                ("Superficies", str(geo.nsurf)),
                ("Cuerpos", str(nb)),
                ("Módulos", str(nm)),
                ("Materiales", ", ".join(map(str, mats)) or "—"),
                ("Envolvente", "añadida por PENGEOM (esfera R=1e7)" if geo.enclosure_added
                 else f"módulo raíz {geo.nbodys}")]
        if session.parinp:
            if session.parinp_provisional:
                src = "provisionales: los escritos en el .geo, sin .in"
            elif session.input_file is not None:
                src = "de " + session.input_file.path.name
            else:
                src = "del .in"
            vals = session.parinp
            shown = ", ".join(f"{v:g}" for v in vals[:MAX_PARINP_SHOWN])
            if len(vals) > MAX_PARINP_SHOWN:  # un .in con PARINP enormes no debe congelar el panel
                shown += f", … ({len(vals)} valores)"
            rows.append(("PARINP", shown + f"  ({html.escape(src)})"))
        lo, hi = session.bounds
        rows.append(("Extensión (cm)", " × ".join(f"{h - l:.4g}" for l, h in zip(lo, hi))))
        out = ["<h3 style='margin:4px 0'>Geometría</h3><table cellspacing=3>"]
        for k, v in rows:
            out.append(f"<tr><td style='color:#666'>{k}</td><td><b>{v}</b></td></tr>")
        out.append("</table><p style='color:#777'>Haga clic en un cuerpo (en el corte o en el árbol) "
                   "para ver sus propiedades.</p>")
        return "".join(out)


class SourceText(QPlainTextEdit):
    """Texto de un fichero (solo lectura) con líneas marcadas y resaltado de la selección."""

    HIGHLIGHT = "#fff1c2"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(10)
        self.setFont(f)
        self._path = None
        self._marks: list[tuple[int, str]] = []  # (línea, color) permanentes
        self._sel: tuple[int, int] | None = None

    def show_document(self, doc) -> None:
        self._path = doc.path if doc is not None else None
        self._marks = []
        self._sel = None
        if doc is None:
            self.setPlainText("")
            return
        # Una línea por registro de Fortran (los números de línea de los mensajes son de registros): un CR que
        # termina un registro no se ve y uno que gfortran ignora se ve como «␍» (Qt partiría la línea en él).
        self.setPlainText("".join(display_record(ln) + "\n" for ln in doc.lines))

    def set_marks(self, marks: list[tuple[int, str]]) -> None:
        self._marks = list(marks)
        self._apply()

    def _selection(self, line: int, color: str) -> QTextEdit.ExtraSelection | None:
        block = self.document().findBlockByNumber(line)
        if not block.isValid():
            return None
        fmt = QTextCharFormat()
        fmt.setBackground(QBrush(QColor(color)))
        fmt.setProperty(QTextFormat.Property.FullWidthSelection, True)
        s = QTextEdit.ExtraSelection()
        s.cursor = QTextCursor(block)
        s.format = fmt
        return s

    def _apply(self) -> None:
        sels = []
        for line, color in self._marks:
            s = self._selection(line, color)
            if s is not None:
                sels.append(s)
        if self._sel is not None:
            first, last = self._sel
            for ln in range(first, last + 1):
                s = self._selection(ln, self.HIGHLIGHT)
                if s is None:
                    break
                sels.append(s)
        self.setExtraSelections(sels)

    def highlight(self, first: int | None, last: int | None, path: str | None = None) -> None:
        if first is None or (path is not None and self._path is not None and path != self._path):
            self._sel = None
            self._apply()
            return
        self._sel = (first, last if last is not None else first)
        self._apply()
        block = self.document().findBlockByNumber(first)
        if block.isValid():
            cur = QTextCursor(block)
            self.setTextCursor(cur)
            self.centerCursor()


class InputTextView(SourceText):
    """Texto actual del .in: líneas cambiadas en amarillo y la línea en la que penhmain se pararía en rojo."""

    MODIFIED = "#fff7d6"
    STOP = "#f8d7da"

    def show_input(self, ctrl) -> None:
        if ctrl is None:
            self._path = None
            self._marks = []
            self._sel = None
            self.setPlainText("")
            return
        doc = ctrl.doc
        self._path = doc.path
        text = "\n".join(doc.display_lines())
        if text != self.toPlainText():
            bar = self.verticalScrollBar()
            pos = bar.value()
            self.setPlainText(text)
            bar.setValue(pos)
        marks = [(i, self.MODIFIED) for i, ln in enumerate(doc.lines) if ln.modified or ln.raw is None]
        row = doc.row(doc.stop_line)
        if row is not None:
            marks.append((row, self.STOP))
        self._marks = marks
        self._apply()


class MessagesPanel(QListWidget):
    """Errores y avisos con su línea y código IRETRN."""

    lineRequested = Signal(object, int)  # (fichero, línea 0-based)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.itemDoubleClicked.connect(self._on_double)

    def set_messages(self, diagnostics, extra_info: list[str] | None = None) -> None:
        self.clear()
        icons = {Severity.ERROR: "#d62728", Severity.WARNING: "#e8a33d", Severity.INFO: "#4e79a7"}
        for d in diagnostics:
            name = d.file.replace("\\", "/").split("/")[-1] if d.file else ""
            loc = f"{name}:{d.line + 1}" if name and d.line is not None else (name or d.location())
            text = (f"{loc}: " if loc else "") + d.message
            if d.code:
                text += f"   [IRETRN {d.code}]"
            it = QListWidgetItem(swatch(icons[d.severity], 10), text)
            it.setData(ROLE_BODY, (d.file, d.line))
            it.setToolTip(d.fortran_reason or text)
            self.addItem(it)
        for msg in extra_info or []:
            self.addItem(QListWidgetItem(swatch(icons[Severity.INFO], 10), msg))

    def _on_double(self, item: QListWidgetItem) -> None:
        data = item.data(ROLE_BODY)
        if data and data[1] is not None:
            self.lineRequested.emit(data[0], int(data[1]))
