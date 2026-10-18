"""Filas de controles que siguen en la línea de abajo cuando no caben.

Con la pantalla al 125 o 150 % (un portátil de 1920 × 1080 al 150 % tiene 1280 × 720 puntos), una fila de controles en
un ``QHBoxLayout`` no se encoge por debajo de la suma de sus controles y la ventana entera no cabía en la pantalla.
``FlowLayout`` se usa como ``QHBoxLayout`` (``addWidget(w, stretch)``, ``insertWidget``, ``addStretch``,
``addSpacing``) pero, si no cabe, sigue en otra línea; ``group()`` junta lo que debe ir siempre en la misma línea (una
etiqueta y su campo).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLayout, QLayoutItem, QSizePolicy, QSpacerItem, QWidget

__all__ = ["FlowLayout", "group", "keep_min_height_for_width"]


class FlowLayout(QLayout):
    """Como ``QHBoxLayout``, pero lo que no cabe pasa a la línea siguiente.

    En cada línea, lo que sobra se reparte como en ``QHBoxLayout``: entre lo que tiene factor de estiramiento (un
    ``addStretch`` empuja lo que va detrás a la derecha de su línea) o, si nada lo tiene, entre lo que se expande (un
    deslizador); un espacio fijo (``addSpacing``) al principio o al final de una línea no se pone. Cada control, con su
    tamaño preferido; si uno solo no cabe en la línea, se estrecha hasta su mínimo. La altura depende de la anchura
    (``heightForWidth``): el ``QVBoxLayout`` de encima le da las líneas que necesite."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6, line_spacing: int = 3):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._stretch: list[int] = []
        self._h = spacing
        self._v = line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # -- como QHBoxLayout ----------------------------------------------------------------------------------------
    def addWidget(self, widget: QWidget, stretch: int = 0) -> None:
        self.insertWidget(len(self._items), widget, stretch)

    def insertWidget(self, index: int, widget: QWidget, stretch: int = 0) -> None:
        # El elemento lo crea Qt (como en QHBoxLayout): Qt lo borra si se borra el control.
        QLayout.addWidget(self, widget)
        item = self._items.pop()
        self._stretch.pop()
        index = len(self._items) if index < 0 else min(index, len(self._items))
        self._items.insert(index, item)
        self._stretch.insert(index, int(stretch))
        self.invalidate()

    def addStretch(self, stretch: int = 1) -> None:
        self._items.append(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self._stretch.append(max(int(stretch), 1))
        self.invalidate()

    def addSpacing(self, size: int) -> None:
        self._items.append(QSpacerItem(int(size), 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum))
        self._stretch.append(0)
        self.invalidate()

    def setSpacing(self, spacing: int) -> None:
        self._h = int(spacing)
        self.invalidate()

    def spacing(self) -> int:
        return self._h

    def setLineSpacing(self, spacing: int) -> None:
        self._v = int(spacing)
        self.invalidate()

    def lineSpacing(self) -> int:
        return self._v

    # -- lo que pide QLayout -------------------------------------------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:  # QLayout.addWidget llama aquí
        self._items.append(item)
        self._stretch.append(0)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if not 0 <= index < len(self._items):
            return None
        self._stretch.pop(index)
        item = self._items.pop(index)
        self.invalidate()
        return item

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), apply=False)

    def minimumSize(self) -> QSize:
        """Lo más ancho que hay (cada cosa puede ir sola en su línea) y una línea de alto."""
        w = h = 0
        for item, _st, spacer in self._entries():
            if spacer is None:  # los espacios fijos no se ponen al principio ni al final de una línea
                s = item.minimumSize()
                w, h = max(w, s.width()), max(h, s.height())
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def sizeHint(self) -> QSize:
        """Todo en una sola línea."""
        w = h = 0
        prev_widget = False
        for item, st, spacer in self._entries():
            if spacer is None:
                s = item.sizeHint()
                w += s.width() + (self._h if prev_widget else 0)
                h = max(h, s.height())
                prev_widget = True
            elif not self._flexible(spacer, st):
                w += spacer.sizeHint().width()
                prev_widget = False
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, apply=True)
        # Lo que no se coloca y no está oculto (un grupo con todo oculto, un contenedor de casillas vacío) se queda sin
        # tamaño: con su geometría de antes (o la de 640 × 480 de un QWidget nuevo) taparía los controles de la fila y
        # se quedaría con sus clics.
        placed = {id(item) for item, _st, _sp in self._entries()}
        m = self.contentsMargins()
        for item in self._items:
            w = item.widget()
            if w is not None and id(item) not in placed and not w.isHidden():
                w.setGeometry(QRect(rect.x() + m.left(), rect.y() + m.top(), 0, 0))

    # -- colocar ------------------------------------------------------------------------------------------------
    def _entries(self):
        """(elemento, estiramiento, espacio o None) de lo que se ve (los controles ocultos no ocupan sitio)."""
        for item, st in zip(self._items, self._stretch):
            spacer = item.spacerItem()
            if spacer is None:
                if item.isEmpty():
                    continue
                w = item.widget()
                if w is not None and w.layout() is not None and w.layout().isEmpty():
                    continue  # un grupo con todo oculto
            yield item, st, spacer

    @staticmethod
    def _flexible(spacer: QSpacerItem, stretch: int) -> bool:
        return stretch > 0 or bool(spacer.expandingDirections() & Qt.Orientation.Horizontal)

    def _place(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        left, top = rect.x() + m.left(), rect.y() + m.top()
        avail = max(rect.width() - m.left() - m.right(), 1)
        # Repartir en líneas: [elemento, anchura, estiramiento, espacio o None].
        lines: list[list[list]] = []
        line: list[list] = []
        used = 0
        for item, st, spacer in self._entries():
            if spacer is not None:
                flexible = self._flexible(spacer, st)
                if not line and not flexible:
                    continue  # un espacio fijo al principio de una línea no se pone
                w = 0 if flexible else spacer.sizeHint().width()
                line.append([item, w, st if st > 0 else (1 if flexible else 0), spacer])
                used += w
                continue
            w = item.sizeHint().width()
            gap = self._h if any(e[3] is None for e in line) and line[-1][3] is None else 0
            if any(e[3] is None for e in line) and used + gap + w > avail:
                lines.append(line)
                line, used, gap = [], 0, 0
            line.append([item, w, st, None])
            used += gap + w
        if line:
            lines.append(line)
        y = top
        first = True
        for line in lines:
            while line and line[-1][3] is not None and not self._flexible(line[-1][3], line[-1][2]):
                line.pop()  # ni al final
            widgets = [e for e in line if e[3] is None]
            if not widgets:
                continue
            gaps = [self._h if (i > 0 and e[3] is None and line[i - 1][3] is None) else 0 for i, e in enumerate(line)]
            extra = avail - sum(e[1] for e in line) - sum(gaps)
            if extra < 0 and len(widgets) == 1:
                e = widgets[0]
                e[1] = max(e[1] + extra, e[0].minimumSize().width())
            elif extra > 0:
                self._spread(line, extra)
            heights = []
            for e in line:
                if e[3] is not None:
                    heights.append(0)
                    continue
                item = e[0]
                ih = item.heightForWidth(e[1]) if item.hasHeightForWidth() else item.sizeHint().height()
                ih = min(max(ih, item.minimumSize().height()), item.maximumSize().height())
                heights.append(ih)
            lh = max(heights)
            if not first:
                y += self._v
            first = False
            if apply:
                x = left
                for e, g, ih in zip(line, gaps, heights):
                    x += g
                    if e[3] is None:
                        e[0].setGeometry(QRect(x, y + (lh - ih) // 2, e[1], ih))
                    x += e[1]
            y += lh
        return y - rect.y() + m.bottom() if not first else m.top() + m.bottom()

    @staticmethod
    def _spread(line: list[list], extra: int) -> None:
        """Lo que sobra en la línea: a lo que tiene estiramiento o, si nada, a lo que se expande (con su máximo)."""
        cands = [e for e in line if e[2] > 0]
        if not cands:
            cands = [e for e in line if e[3] is None and e[0].expandingDirections() & Qt.Orientation.Horizontal]
        while extra > 0 and cands:
            weight = sum(max(e[2], 1) for e in cands)
            given = 0
            capped = []
            for e in cands:
                share = extra * max(e[2], 1) // weight
                cap = (e[0].maximumSize().width() - e[1]) if e[3] is None else extra
                take = max(min(share, cap), 0)
                e[1] += take
                given += take
                if take >= cap:
                    capped.append(e)
            extra -= given
            if not capped:
                if extra > 0:  # el resto del redondeo, al primero
                    e = cands[0]
                    cap = (e[0].maximumSize().width() - e[1]) if e[3] is None else extra
                    e[1] += max(min(extra, cap), 0)
                break
            cands = [e for e in cands if e not in capped]


def group(*widgets: QWidget | int, spacing: int = 4) -> QWidget:
    """Controles que van siempre juntos en la misma línea de un :class:`FlowLayout` (una etiqueta y su campo); un
    número es un espacio fijo."""
    box = QWidget()
    # No crece: si no, en una línea sin estiramiento un campo de texto del grupo se llevaría lo que sobra y separaría
    # la etiqueta de su campo.
    box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        if isinstance(w, int):
            lay.addSpacing(w)
        else:
            lay.addWidget(w)
    return box


class _MinHeightForWidth(QObject):
    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self._w = widget
        self._pending = False
        widget.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 (Qt)
        if obj is self._w and ev.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest) and not self._pending:
            self._pending = True
            QTimer.singleShot(0, self, self._apply)
        return False

    def _apply(self) -> None:
        self._pending = False
        w = self._w
        lay = w.layout()
        if lay is None or w.width() <= 0:
            return
        h = lay.totalMinimumHeightForWidth(w.width())
        if h > 0 and h != w.minimumHeight():
            w.setMinimumHeight(h)


def keep_min_height_for_width(widget: QWidget) -> None:
    """La altura mínima de ``widget``, la que pide su layout para su anchura. Un QDockWidget (y la ventana principal)
    no usan heightForWidth: sin esto, una fila de :class:`FlowLayout` que pasa a dos líneas en un panel estrecho se
    saldría por abajo (los botones de «Materiales» quedaban cortados con la pantalla al 150 %)."""
    _MinHeightForWidth(widget)
