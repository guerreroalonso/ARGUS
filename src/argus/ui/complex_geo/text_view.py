"""Texto del .geo con los números de línea, el bloque elegido resaltado como en PenGeomJar y las líneas con errores.

Es de solo lectura salvo con «Manual edit» (como en PenGeomJar): entonces se escribe libremente y el texto escrito
se devuelve con los finales de línea del fichero (``file_text``).
"""

from __future__ import annotations

import difflib

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from ...core.fortran_io import CR_SYMBOL, display_record

# Colores de PenGeomJar (Nimbus): la selección es azul con letra blanca.
SELECTED_BG = "#39698a"
SELECTED_FG = "#ffffff"
PENDING_BG = "#f3c98b"  # el bloque de la vista previa (sin aplicar)
ERROR_BG = "#f8d7da"
EDIT_BG = "#fffdf2"  # «Manual edit»


def _ending(raw: str) -> str:
    """El fin de línea de un registro: «\\r\\n», «\\n», un «\\r» suelto o nada (la última línea sin fin)."""
    return raw[len(raw.rstrip("\n").removesuffix("\r")):]


class _LineNumbers(QWidget):
    def __init__(self, view: GeoTextView):
        super().__init__(view)
        self.view = view

    def sizeHint(self) -> QSize:
        return QSize(self.view.number_width(), 0)

    def paintEvent(self, event) -> None:
        self.view.paint_numbers(event)

    def mousePressEvent(self, e) -> None:  # pulsar en el número también elige el bloque
        if e.button() == Qt.MouseButton.LeftButton and self.view.isReadOnly():
            cur = self.view.cursorForPosition(QPoint(0, int(e.position().y())))
            self.view.lineClicked.emit(cur.blockNumber())


class GeoTextView(QPlainTextEdit):
    """El .geo, una línea por registro de Fortran, con su número a la izquierda."""

    lineClicked = Signal(int)  # línea (0-based) pulsada con el botón izquierdo (solo lectura)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(10)
        self.setFont(f)
        self._numbers = _LineNumbers(self)
        pal = self.palette()  # la selección de texto, del azul de PenGeomJar (como el bloque elegido)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            pal.setColor(group, QPalette.ColorRole.Highlight, QColor(SELECTED_BG))
            pal.setColor(group, QPalette.ColorRole.HighlightedText, QColor(SELECTED_FG))
        self.setPalette(pal)
        self._block: tuple[int, int, str] | None = None  # (primera, última, «selected»/«pending»)
        self._errors: set[int] = set()
        self._warnings: set[int] = set()
        self.newline = "\n"
        self._orig_raw: list[str] | None = None  # las líneas del fichero al empezar a escribir a mano
        self._orig_shown: list[str] = []
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._on_update_request)
        self._update_margin()

    # -- texto -----------------------------------------------------------------------------------------------------
    def show_lines(self, lines: list[str], newline: str = "\n") -> None:
        """Las líneas (con su fin de línea) de un .geo; se conserva lo que se estaba viendo si no cambia nada."""
        self.newline = newline or "\n"
        text = "\n".join(display_record(ln) for ln in lines)
        if text == "\n".join(self.shown_lines()):  # (``toPlainText`` cambia los blancos duros por blancos)
            return
        bar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        pos, hpos = bar.value(), hbar.value()
        self.setPlainText(text)
        bar.setValue(pos)
        hbar.setValue(hpos)
        self._apply()

    def show_text(self, text: str, newline: str = "\n") -> None:
        from ...pengeom.parser import split_lines

        self.show_lines(split_lines(text) if text else [], newline)

    def begin_edit(self, raw_lines: list[str]) -> None:
        """Empieza «Manual edit» sobre estas líneas del fichero (con sus finales de línea)."""
        self._orig_raw = list(raw_lines)
        self._orig_shown = [display_record(ln) for ln in raw_lines]

    def shown_lines(self) -> list[str]:
        """Las líneas que se ven, tal cual (``toPlainText`` cambiaría los blancos duros por blancos)."""
        out = []
        block = self.document().begin()
        while block.isValid():
            out.append(block.text())
            block = block.next()
        return out

    def file_text(self) -> str:
        """Lo escrito, como se guardaría: las líneas que no se han tocado, byte a byte como estaban (con su fin de
        línea, también la última sin él); las cambiadas, con el fin de línea que tenían; las nuevas, con el del
        fichero."""
        new = self.shown_lines()
        if new == [""]:
            new = []
        if self._orig_raw is None:
            return "".join(ln.replace(CR_SYMBOL, "\r") + self.newline for ln in new)
        old = self._orig_shown
        if new == old:
            return "".join(self._orig_raw)
        # Lo que no ha cambiado al principio y al final se copia sin comparar (difflib con el fichero entero tarda
        # segundos en los .geo de miles de líneas); se compara solo lo de en medio.
        n = min(len(old), len(new))
        a = 0
        while a < n and old[a] == new[a]:
            a += 1
        b = 0
        while b < n - a and old[len(old) - 1 - b] == new[len(new) - 1 - b]:
            b += 1
        out: list[str] = list(self._orig_raw[:a])
        mid_old, mid_new = old[a:len(old) - b], new[a:len(new) - b]
        ops = difflib.SequenceMatcher(a=mid_old, b=mid_new, autojunk=False).get_opcodes() if mid_old and mid_new \
            else [("replace", 0, len(mid_old), 0, len(mid_new))]
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal":
                out.extend(self._orig_raw[a + i1:a + i2])
                continue
            for k, ln in enumerate(mid_new[j1:j2]):  # una línea cambiada conserva su fin de línea (o su falta)
                end = _ending(self._orig_raw[a + i1 + k]) if k < i2 - i1 else self.newline
                out.append(ln.replace(CR_SYMBOL, "\r") + end)
        out.extend(self._orig_raw[len(old) - b:])
        for k in range(len(out) - 1):  # una línea sin fin (la última de antes) que ya no es la última
            if not out[k].endswith(("\n", "\r")):
                out[k] += self.newline
        return "".join(out)

    def set_editing(self, on: bool) -> None:
        self.setReadOnly(not on)
        pal = self.palette()
        pal.setColor(self.viewport().backgroundRole(), QColor(EDIT_BG) if on else self.style().standardPalette()
                     .color(self.viewport().backgroundRole()))
        self.viewport().setPalette(pal)
        self.viewport().setAutoFillBackground(True)
        if on:
            self._block = None
            self._apply()
        else:
            self._orig_raw = None

    # -- marcas ----------------------------------------------------------------------------------------------------
    def mark_block(self, first: int | None, last: int | None, kind: str = "selected", scroll: bool = True) -> None:
        """Resalta las líneas de un bloque (o nada); ``kind`` «selected» (azul) o «pending» (vista previa). El bloque
        elegido queda además seleccionado como texto (como en PenGeomJar): Ctrl+C lo copia."""
        self._block = None if first is None else (first, last if last is not None else first, kind)
        self._apply()
        if self.isReadOnly():
            self._select_text(first, last if last is not None else first, kind == "selected")
        if scroll and first is not None:
            self.ensure_visible(first, last if last is not None else first)

    def _select_text(self, first: int | None, last: int | None, on: bool) -> None:
        """La selección de texto del bloque elegido (sin mover lo que se ve)."""
        bar, hbar = self.verticalScrollBar(), self.horizontalScrollBar()
        pos, hpos = bar.value(), hbar.value()
        doc = self.document()
        a = doc.findBlockByNumber(first) if on and first is not None else None
        b = doc.findBlockByNumber(last) if on and last is not None else None
        if a is not None and a.isValid():
            cur = QTextCursor(a)
            end = b if b is not None and b.isValid() else a
            cur.setPosition(end.position() + max(0, end.length() - 1), QTextCursor.MoveMode.KeepAnchor)
        else:
            cur = self.textCursor()
            cur.clearSelection()
        self.setTextCursor(cur)
        bar.setValue(pos)
        hbar.setValue(hpos)

    def set_problem_lines(self, errors, warnings=()) -> None:
        errors, warnings = set(errors), set(warnings) - set(errors)
        if errors != self._errors or warnings != self._warnings:
            self._errors, self._warnings = errors, warnings
            self._apply()
            self._numbers.update()

    def ensure_visible(self, first: int, last: int) -> None:
        """El bloque entero a la vista, centrado (o su principio arriba, si no cabe)."""
        doc = self.document()
        if not doc.findBlockByNumber(first).isValid():
            return
        bar = self.verticalScrollBar()  # (en QPlainTextEdit la barra va por líneas: su valor es la primera visible)
        shown = self.firstVisibleBlock()
        line_h = self.blockBoundingRect(shown).height() if shown.isValid() else 0.0
        if line_h <= 0:
            line_h = float(self.fontMetrics().lineSpacing())
        rows = max(1, int(self.viewport().height() // line_h))  # las líneas que caben enteras
        top = bar.value()
        if first >= top and last <= top + rows - 1:
            return
        n = max(1, last - first + 1)
        if n >= rows:
            self._scroll_to_top(first)
        else:
            bar.setValue(max(0, first - (rows - n) // 2))

    def _scroll_to_top(self, line: int) -> None:
        self.verticalScrollBar().setValue(max(0, line - 2))

    def _selection(self, line: int, bg: str, fg: str | None = None) -> QTextEdit.ExtraSelection | None:
        block = self.document().findBlockByNumber(line)
        if not block.isValid():
            return None
        fmt = QTextCharFormat()
        fmt.setBackground(QBrush(QColor(bg)))
        if fg:
            fmt.setForeground(QBrush(QColor(fg)))
        fmt.setProperty(QTextFormat.Property.FullWidthSelection, True)
        s = QTextEdit.ExtraSelection()
        s.cursor = QTextCursor(block)
        s.format = fmt
        return s

    def _apply(self) -> None:
        sels = []
        for ln in sorted(self._errors):
            s = self._selection(ln, ERROR_BG)
            if s is not None:
                sels.append(s)
        if self._block is not None:
            first, last, kind = self._block
            bg, fg = (SELECTED_BG, SELECTED_FG) if kind == "selected" else (PENDING_BG, None)
            for ln in range(first, last + 1):
                s = self._selection(ln, bg, fg)
                if s is None:
                    break
                sels.append(s)
        self.setExtraSelections(sels)

    # -- números de línea ------------------------------------------------------------------------------------------
    def number_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self, *args) -> None:
        self.setViewportMargins(self.number_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._numbers.scroll(0, dy)
        else:
            self._numbers.update(0, rect.y(), self._numbers.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._numbers.setGeometry(QRect(cr.left(), cr.top(), self.number_width(), cr.height()))

    def paint_numbers(self, event) -> None:
        p = QPainter(self._numbers)
        p.fillRect(event.rect(), QColor("#eceef2"))
        p.setPen(QColor("#c4c8d0"))
        p.drawLine(self._numbers.width() - 1, event.rect().top(), self._numbers.width() - 1, event.rect().bottom())
        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        fm = self.fontMetrics()
        w = self._numbers.width() - 6
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if n in self._errors or n in self._warnings:
                    p.fillRect(0, top, self._numbers.width() - 1, bottom - top,
                               QColor("#e06666" if n in self._errors else "#f0b65a"))
                    p.setPen(QColor("#ffffff"))
                elif self._block is not None and self._block[0] <= n <= self._block[1]:
                    p.setPen(QColor("#1f3b52"))
                else:
                    p.setPen(QColor("#7a808c"))
                p.drawText(0, top, w, fm.height(), Qt.AlignmentFlag.AlignRight, str(n + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1
        p.end()

    # -- ratón -----------------------------------------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        super().mousePressEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and self.isReadOnly():
            self.lineClicked.emit(self.cursorForPosition(e.position().toPoint()).blockNumber())
