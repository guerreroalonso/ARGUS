"""Las siete pestañas de edición de PenGeomJar («Edition Tabs»): Surface, Surface (implicit), Body, Module, Clone,
Include y CT, con sus campos, listas y tablas como en PenGeomJar 1.21.

Los nombres de las pestañas, de los campos y de los botones son los de PenGeomJar (las palabras clave de PENGEOM);
las ayudas emergentes, en español. Cada pestaña sabe enseñar un ``blocks.*Form`` (``set_form``) y leer lo escrito
(``form``); lo demás (qué hace cada botón, la vista previa) lo decide el espacio de trabajo.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...pengeom import blocks as bk
from ...pengeom.model import ANGLE_PARAMS, COEF_NAMES, REDUCED_PARAMS, TRANSFORM_PARAMS

EMPTY_BG = "#dcc8c8"  # PenGeomJar: los campos vacíos, rosados (220, 200, 200)

BUTTONS = ("delete", "modify", "new", "add", "cancel")
BUTTON_TEXT = {"delete": "Delete", "modify": "Modify", "new": "New", "add": "Add", "cancel": "Cancel"}
BUTTON_TIPS = {
    "delete": "Borra el bloque elegido (y los comentarios que lo preceden). No se borra lo que otro bloque usa.",
    "modify": "Reescribe el bloque elegido con lo que hay en la pestaña.",
    "new": "Vacía la pestaña para escribir un bloque nuevo (las otras pestañas quedan bloqueadas hasta «Add» o "
           "«Cancel»).",
    "add": "Añade lo que hay en la pestaña como un bloque nuevo, detrás del bloque elegido.",
    "cancel": "Descarta lo escrito y vuelve a enseñar el bloque elegido.",
}
KEYWORD_TIPS = {
    "SURFACE": "Pulse para cambiar entre SURFACE y SURFACE* (una superficie fija: no la mueven las "
               "transformaciones de los módulos ni de los clones).",
    "INCLUDE": "Pulse para cambiar entre INCLUDE e INCLUDE* (el fichero incluido comparte las etiquetas con este).",
}
PARAM_TIPS = {
    "X-SCALE": "Escala en x (cm), > 0", "Y-SCALE": "Escala en y (cm), > 0", "Z-SCALE": "Escala en z (cm), > 0",
    "OMEGA": "Ángulo de Euler ω (giro alrededor de z)", "THETA": "Ángulo de Euler θ (giro alrededor de y)",
    "PHI": "Ángulo de Euler φ (giro alrededor de z)", "X-SHIFT": "Desplazamiento en x (cm)",
    "Y-SHIFT": "Desplazamiento en y (cm)", "Z-SHIFT": "Desplazamiento en z (cm)",
}
INDEX_TIP = ("Índice PARINP: con un número n > 0, penhmain sustituye el valor por PARINP(n) del .in "
             "(vacío o 0: se usa el valor escrito).")


class Field(QLineEdit):
    """Campo de texto; con ``pink`` (los valores de los parámetros), vacío se ve rosado como en PenGeomJar."""

    def __init__(self, width: int | None = None, parent=None, pink: bool = False):
        super().__init__(parent)
        if width:
            self.setFixedWidth(width)
        self.pink = pink
        if pink:
            self.textChanged.connect(self._paint)
            self._paint()

    def _paint(self, *args) -> None:
        self.setStyleSheet(f"QLineEdit {{ background: {EMPTY_BG}; }}" if not self.text().strip() else "")


class ChoiceList(QListWidget):
    """Una lista corta de valores (los índices -1, 0, 1 o el lado -1, 1), como las de PenGeomJar."""

    picked = Signal()

    def __init__(self, values: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.values = values
        self.addItems(list(values))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        h = self.sizeHintForRow(0) * len(values) + 2 * self.frameWidth() + 2
        self.setFixedSize(QSize(36, h))
        self.itemSelectionChanged.connect(self.picked.emit)

    def value(self) -> int | None:
        items = self.selectedItems()
        return int(items[0].text()) if items else None

    def set_value(self, v: int | None) -> None:
        self.blockSignals(True)
        self.clearSelection()
        if v is not None and str(v) in self.values:
            self.item(self.values.index(str(v))).setSelected(True)
        self.blockSignals(False)


class KeywordLabel(QLabel):
    """«SURFACE :» o «INCLUDE :»: al pulsarla pasa a la forma con asterisco (como en PenGeomJar)."""

    toggled = Signal(bool)

    def __init__(self, keyword: str, toggles: bool = True, parent=None):
        super().__init__(parent)
        self.keyword = keyword
        self.toggles = toggles
        self.starred = False
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if toggles:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(KEYWORD_TIPS.get(keyword, ""))
        self._show()

    def _show(self) -> None:
        self.setText(f"{self.keyword}{'*' if self.starred else ' '}:")

    def set_starred(self, on: bool) -> None:
        self.starred = bool(on)
        self._show()

    def mousePressEvent(self, e) -> None:
        if self.toggles and e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.set_starred(not self.starred)
            self.toggled.emit(self.starred)


class ParamRow:
    """Una fila de parámetro: «X-SCALE:», el valor, la unidad (ángulos) y el índice PARINP."""

    def __init__(self, name: str, grid: QGridLayout, row: int, changed, value_width: int = 186):
        self.name = name
        self.label = QLabel(f"{name}:")
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value = Field(value_width, pink=True)
        self.value.setToolTip(PARAM_TIPS.get(name, f"Coeficiente {name} de la forma implícita"))
        self.unit: QComboBox | None = None
        self.index = Field(57, pink=True)
        self.index.setToolTip(INDEX_TIP)
        grid.addWidget(self.label, row, 0)
        grid.addWidget(self.value, row, 1)
        if name in ANGLE_PARAMS:
            self.unit = QComboBox()
            self.unit.addItems(["DEG", "RAD"])
            self.unit.setToolTip("Unidad del ángulo: grados (DEG, la de PENGEOM por defecto) o radianes (RAD)")
            self.unit.currentIndexChanged.connect(changed)
            grid.addWidget(self.unit, row, 2)
        grid.addWidget(self.index, row, 3)
        self.value.textEdited.connect(changed)
        self.index.textEdited.connect(changed)

    def set(self, p: bk.Param | None) -> None:
        self.value.setText(p.value if p else "")
        self.index.setText(p.index if p else "")
        if self.unit is not None:
            self.unit.blockSignals(True)
            self.unit.setCurrentIndex(1 if p is not None and p.unit == "RAD" else 0)
            self.unit.blockSignals(False)

    def get(self) -> bk.Param | None:
        v, i = self.value.text(), self.index.text()
        if not v.strip() and not i.strip():
            return None
        return bk.Param(self.name, v.strip(), i.strip(), self.unit.currentText() if self.unit is not None else "DEG")

    def widgets(self) -> list[QWidget]:
        return [w for w in (self.value, self.unit, self.index) if w is not None]


def set_completions(field: QLineEdit, entries: list[tuple[str, str]]) -> None:
    """Las etiquetas que propone el campo al escribir (se rehace el mismo modelo: no se acumulan)."""
    c = field.completer()
    model = c.model() if c is not None else None
    if not isinstance(model, QStandardItemModel):
        model = QStandardItemModel(field)
        c = QCompleter(model, field)
        c.setCompletionRole(Qt.ItemDataRole.UserRole)
        c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        c.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        field.setCompleter(c)
        # Elegir de la lista pone el texto sin «textEdited»: se avisa igual (la vista previa, lo escrito sin aplicar).
        c.activated.connect(lambda *_a, f=field: f.textEdited.emit(f.text()))
    model.clear()
    for lab, desc in entries:
        it = QStandardItem(f"{lab:>4}   {desc}" if desc else lab)
        it.setData(lab, Qt.ItemDataRole.UserRole)
        model.appendRow(it)


class RefTable(QWidget):
    """Tabla de referencias (superficies con su lado, cuerpos o módulos) con su campo y los botones Add y Delete."""

    changed = Signal()
    problem = Signal(str)

    def __init__(self, title: str, entry: str, sides: bool = False, parent=None):
        super().__init__(parent)
        self.sides = sides
        self.entry_name = entry
        self.known: set[str] = set()
        self.table = QTableWidget(0, 2 if sides else 1)
        self.table.setHorizontalHeaderLabels([title, "SIDE POINTER"] if sides else [title])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 2)
        self.table.setFixedSize(QSize(240 if sides else 146, 104))
        self.table.cellClicked.connect(self._read_row)
        self.table.cellDoubleClicked.connect(self._toggle_side)
        if sides:
            self.table.setToolTip("Pulse una fila para pasarla al campo; doble clic en el lado lo cambia (-1 ↔ 1).")
        self.field = Field(64)
        self.field.returnPressed.connect(self.add)
        self.side = ChoiceList(("-1", "1")) if sides else None
        self.btn_add = QPushButton("Add")
        self.btn_add.setToolTip(f"Añade a la tabla {entry.lower()} del campo")
        self.btn_add.clicked.connect(self.add)
        self.btn_del = QPushButton("Delete")
        self.btn_del.setToolTip("Quita de la tabla la fila elegida")
        self.btn_del.clicked.connect(self.delete)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(8)
        lay.addWidget(self.table, 0, 0, 4, 1, Qt.AlignmentFlag.AlignTop)
        lab = QLabel(f"{entry}:")
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(lab, 0, 1)
        lay.addWidget(self.field, 0, 2)
        r = 1
        if self.side is not None:
            sl = QLabel("Side pointer:")
            sl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(sl, 1, 1)
            lay.addWidget(self.side, 1, 2)
            r = 2
        btns = QHBoxLayout()
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_del)
        lay.addLayout(btns, r, 1, 1, 2)
        lay.setRowStretch(3, 1)
        lay.setColumnStretch(3, 1)

    def set_known(self, entries: list[tuple[str, str]]) -> None:
        """Las etiquetas que existen (para completar lo que se escribe y avisar si no existe)."""
        self.known = {lab for lab, _ in entries}
        set_completions(self.field, entries)

    def rows(self) -> list:
        """Las filas: la etiqueta tal como está en el fichero (4 caracteres) o, si es nueva, como se ha escrito."""
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            raw = it.data(Qt.ItemDataRole.UserRole)
            lab = raw if isinstance(raw, str) and raw.strip() == it.text().strip() else it.text().strip()
            if self.sides:
                out.append((lab, int(self.table.item(r, 1).text())))
            else:
                out.append(lab)
        return out

    def set_rows(self, rows) -> None:
        self.table.setRowCount(0)
        for row in rows:
            self._append(row)
        self.field.clear()
        if self.side is not None:
            self.side.set_value(None)

    def _append(self, row) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        lab, side = (row if self.sides else (row, None))
        it = QTableWidgetItem(str(lab).strip())
        if len(str(lab)) == 4:  # la etiqueta del fichero, con sus blancos (PENGEOM compara las 4 columnas)
            it.setData(Qt.ItemDataRole.UserRole, str(lab))
        self.table.setItem(r, 0, it)
        if self.sides:
            s = QTableWidgetItem(f"{int(side):d}")
            s.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 1, s)

    def add(self) -> None:
        lab = self.field.text().strip()
        if not lab:
            return
        if self.known and lab not in self.known:
            # PenGeomJar: «This surface number doesn't exits!»
            self.problem.emit(f"No hay {'ninguna superficie' if self.sides else 'ningún elemento'} con la "
                              f"etiqueta «{lab}» ({'definida' if self.sides else 'definido'} antes de este bloque o "
                              "en un INCLUDE*).")
            return
        labels = [(row[0] if self.sides else row).strip() for row in self.rows()]
        if lab in labels:
            self.problem.emit(f"«{lab}» ya está en la tabla.")
            return
        side = 1
        if self.side is not None and self.side.value() is not None:
            side = self.side.value()
        self._append((lab, side) if self.sides else lab)
        self.field.clear()
        if self.side is not None:
            self.side.set_value(None)
        self.changed.emit()

    def delete(self) -> None:
        rows = sorted({it.row() for it in self.table.selectedItems()})
        if not rows:
            return
        self.table.removeRow(rows[0])
        # Nada elegido después: otro Delete no se lleva la fila que Qt elige sola (la siguiente).
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.field.clear()
        if self.side is not None:
            self.side.set_value(None)
        self.changed.emit()

    def _read_row(self, r: int, c: int) -> None:
        it = self.table.item(r, 0)
        if it is None:
            return
        self.field.setText(it.text().strip())
        if self.side is not None:
            self.side.set_value(int(self.table.item(r, 1).text()))

    def _toggle_side(self, r: int, c: int) -> None:
        if not self.sides or c != 1 or not self.btn_add.isEnabled():  # (una pestaña que no se puede editar)
            return
        it = self.table.item(r, 1)
        if it is None:
            return
        it.setText("1" if it.text().strip() == "-1" else "-1")
        self.changed.emit()

    def set_editable(self, on: bool) -> None:
        for w in (self.field, self.btn_add, self.btn_del, self.side):
            if w is not None:
                w.setEnabled(on)


class BlockTab(QWidget):
    """Lo común de las pestañas: la fila de botones, la etiqueta y la descripción."""

    tab_id = ""
    changed = Signal()  # el usuario ha cambiado algo
    problem = Signal(str)  # algo que decir (p. ej. una etiqueta que no existe)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        row.addStretch(1)
        for name in BUTTONS:
            b = QPushButton(BUTTON_TEXT[name])
            b.setToolTip(BUTTON_TIPS[name])
            b.setAutoDefault(False)
            self.buttons[name] = b
            row.addWidget(b)
        row.addStretch(1)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(8, 8, 8, 8)
        self.outer.addLayout(row)
        # Lo que dicen los botones (un error, lo hecho), junto a ellos (también va debajo del texto).
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.setVisible(False)
        self.outer.addWidget(self.message)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        self.outer.addLayout(self.grid)
        self._loading = False

    def _changed(self, *args) -> None:
        if not self._loading:
            self.changed.emit()

    def show_message(self, text: str, style: str = "") -> None:
        self.message.setStyleSheet(style)
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _header(self, keyword: str, row: int, toggles: bool = False, desc_width: int = 300) -> None:
        # Sin límite de caracteres en los campos: lo que no quepa se dice (no se corta en silencio al pegar).
        self.keyword = KeywordLabel(keyword, toggles)
        self.keyword.toggled.connect(self._changed)
        self.label = Field(50)
        self.label.setToolTip("Etiqueta (hasta 4 caracteres)")
        self.desc = Field(desc_width)
        self.desc.setToolTip(f"Descripción (texto libre, hasta {bk.MAX_BLOCK_DESCRIPTION} caracteres)")
        self.label.textEdited.connect(self._changed)
        self.desc.textEdited.connect(self._changed)
        self.grid.addWidget(self.keyword, row, 0)
        h = QHBoxLayout()
        h.addWidget(self.label)
        h.addWidget(self.desc)
        h.addStretch(1)
        self.grid.addLayout(h, row, 1, 1, 3)

    def set_form(self, form) -> None:
        self._loading = True
        try:
            self._set(form)
        finally:
            self._loading = False

    def clear(self) -> None:
        self.set_form(bk.empty_form(self.tab_id))

    def _set(self, form) -> None:  # pragma: no cover - cada pestaña
        raise NotImplementedError

    def form(self):  # pragma: no cover - cada pestaña
        raise NotImplementedError

    def set_context(self, doc, material_names: dict[int, str]) -> None:
        """Etiquetas que existen (para completar) y nombres de los materiales."""

    def set_editable(self, on: bool) -> None:
        for w in self.findChildren(QLineEdit) + self.findChildren(QComboBox) + self.findChildren(QListWidget):
            w.setEnabled(on)
        for w in self.findChildren(RefTable):
            w.set_editable(on)
        if hasattr(self, "keyword"):
            self.keyword.setEnabled(on)
        if hasattr(self, "btn_browse"):
            self.btn_browse.setEnabled(on)

    def focus_field(self, name: str | None) -> None:
        """Lleva el cursor al campo de un error (``BlockError.field``)."""
        w = self._field_widget(name)
        if w is not None:
            w.setFocus()
            if isinstance(w, QLineEdit):
                w.selectAll()

    def _param_rows(self) -> list[ParamRow]:
        return [*getattr(self, "rows", {}).values(), *getattr(self, "transform", {}).values()]

    def _field_widget(self, name: str | None):
        if name == "label" and hasattr(self, "label"):
            return self.label
        if name == "description" and hasattr(self, "desc"):
            return self.desc
        base, _, part = (name or "").partition(".")  # «X-SCALE» (el valor) o «X-SCALE.index» (el índice PARINP)
        for row in self._param_rows():
            if row.name == base:
                return row.index if part == "index" else row.value
        return None


def _transform_rows(tab: BlockTab, start: int, value_width: int = 186) -> dict[str, ParamRow]:
    return {name: ParamRow(name, tab.grid, start + i, tab._changed, value_width)
            for i, name in enumerate(TRANSFORM_PARAMS)}


class SurfaceTab(BlockTab):
    """Surface: una superficie en forma reducida (INDICES, tipo de cuádrica, escalas, giro y desplazamiento)."""

    tab_id = bk.TAB_SURFACE

    def __init__(self, parent=None):
        super().__init__(parent)
        self._header("SURFACE", 0, toggles=True)
        lab = QLabel("INDICES:")
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.grid.addWidget(lab, 1, 0)
        h = QHBoxLayout()
        h.setSpacing(14)
        self.indices = [ChoiceList(("-1", "0", "1")) for _ in range(5)]
        for k, lst in enumerate(self.indices):
            lst.setToolTip(f"I{k + 1}: la superficie reducida es I1·x² + I2·y² + I3·z² + I4·z + I5 = 0 "
                           "(x, y, z divididos por las escalas)")
            lst.picked.connect(self._on_index)
            h.addWidget(lst)
        h.addStretch(1)
        self.grid.addLayout(h, 1, 1, 1, 3)
        self.quadric = QComboBox()
        self.quadric.addItems([name for name, _ in bk.QUADRICS] + [bk.USER_INDICES])
        self.quadric.setToolTip("Tipo de cuádrica: pone sus índices (como en PenGeomJar)")
        self.quadric.currentIndexChanged.connect(self._on_quadric)
        self.grid.addWidget(self.quadric, 2, 1, 1, 3, Qt.AlignmentFlag.AlignHCenter)
        self.rows = {name: ParamRow(name, self.grid, 3 + i, self._changed) for i, name in enumerate(REDUCED_PARAMS)}
        self.grid.setColumnStretch(4, 1)
        self.outer.addStretch(1)

    def _on_index(self) -> None:
        if self._loading:
            return
        vals = [lst.value() for lst in self.indices]
        if None not in vals:
            self.quadric.blockSignals(True)
            self.quadric.setCurrentText(bk.quadric_name(vals))
            self.quadric.blockSignals(False)
        self._changed()

    def _on_quadric(self, i: int) -> None:
        if self._loading or i >= len(bk.QUADRICS):
            return
        for lst, v in zip(self.indices, bk.QUADRICS[i][1], strict=True):
            lst.set_value(v)
        self._changed()

    def _set(self, form: bk.SurfaceForm) -> None:
        self.keyword.set_starred(form.starred)
        self.label.setText(form.label)
        self.desc.setText(form.description)
        for lst, v in zip(self.indices, form.indices, strict=True):
            lst.set_value(v)
        self.quadric.setCurrentText(bk.quadric_name(form.indices))
        for name, row in self.rows.items():
            row.set(form.params.get(name))

    def form(self) -> bk.SurfaceForm:
        vals = [lst.value() for lst in self.indices]
        if None in vals:
            k = vals.index(None)
            raise bk.BlockError(f"Elija el índice I{k + 1} (-1, 0 o 1).", "indices")
        params = {n: p for n, r in self.rows.items() if (p := r.get()) is not None}
        return bk.SurfaceForm(self.label.text(), self.desc.text(), self.keyword.starred, False, tuple(vals), params)

    def _field_widget(self, name):
        if name == "indices":
            return self.indices[0]
        return super()._field_widget(name)


class ImplicitTab(BlockTab):
    """Surface (implicit): los diez coeficientes AXX…A0 y, tras la línea de unos, el giro y el desplazamiento."""

    tab_id = bk.TAB_IMPLICIT

    def __init__(self, parent=None):
        super().__init__(parent)
        self._header("SURFACE", 0, toggles=True)
        self.rows = {name: ParamRow(name, self.grid, 1 + i, self._changed) for i, name in enumerate(COEF_NAMES)}
        self.transform = _transform_rows(self, 1 + len(COEF_NAMES))
        for row in self.transform.values():
            row.label.setToolTip("Tras la línea de unos (1111…): giro y desplazamiento de la cuádrica")
        self.grid.setColumnStretch(4, 1)
        self.outer.addStretch(1)

    def _set(self, form: bk.SurfaceForm) -> None:
        self.keyword.set_starred(form.starred)
        self.label.setText(form.label)
        self.desc.setText(form.description)
        for name, row in self.rows.items():
            row.set(form.params.get(name))
        for name, row in self.transform.items():
            row.set(form.transform.get(name))

    def form(self) -> bk.SurfaceForm:
        params = {n: p for n, r in self.rows.items() if (p := r.get()) is not None}
        transform = {n: p for n, r in self.transform.items() if (p := r.get()) is not None}
        return bk.SurfaceForm(self.label.text(), self.desc.text(), self.keyword.starred, True, (0, 0, 0, 0, 0),
                              params, transform)

    def _field_widget(self, name):
        if name in getattr(self, "transform", {}):
            return self.transform[name].value
        return super()._field_widget(name)


class _BodyLike(BlockTab):
    """Body y Module: material, tablas de limitantes (e hijas) y, en Module, la transformación."""

    module = False

    def __init__(self, parent=None):
        super().__init__(parent)
        kw = "MODULE" if self.module else "BODY"
        self._header(kw, 0)
        lab = QLabel("MATERIAL:")
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.material = Field(50)
        self.material.setToolTip("Número de material (0: vacío; el n-ésimo MFNAME del .in)")
        self.material.textEdited.connect(self._changed)
        self.material.textChanged.connect(self._show_material)
        self.material_name = QLabel("")
        self.material_name.setStyleSheet("color: #5b6270;")
        h = QHBoxLayout()
        h.addWidget(self.material)
        h.addWidget(self.material_name)
        h.addStretch(1)
        self.grid.addWidget(lab, 1, 0)
        self.grid.addLayout(h, 1, 1, 1, 3)
        if self.module:
            titles = ("LIMITING SURFACE", "INNER BODY", "INNER MODULE")
        else:
            titles = ("LIMITING SURFACE", "LIMITING BODY", "LIMITING MODULE")
        self.t_surf = RefTable(titles[0], "SURFACE", sides=True)
        self.t_body = RefTable(titles[1], "BODY")
        self.t_mod = RefTable(titles[2], "MODULE")
        for k, t in enumerate((self.t_surf, self.t_body, self.t_mod)):
            t.changed.connect(self._changed)
            t.problem.connect(self.problem.emit)
            self.grid.addWidget(t, 2 + k, 0, 1, 4, Qt.AlignmentFlag.AlignRight if k else Qt.AlignmentFlag.AlignLeft)
        self.rows = _transform_rows(self, 5) if self.module else {}
        self._names: dict[int, str] = {}
        self.grid.setColumnStretch(4, 1)
        self.outer.addStretch(1)

    def _show_material(self, *args) -> None:
        t = self.material.text().strip()
        try:
            m = int(t)
        except ValueError:
            self.material_name.setText("")
            return
        if 0 < m != 9999 and self._names and m not in self._names:
            # penhmain se detendría (3054): el .in no declara tantos materiales (sus líneas MFNAME).
            self.material_name.setStyleSheet("color: #b3261e;")
            n = len(self._names)
            self.material_name.setText(f"no está en el .in (declara {n} material{'es' if n != 1 else ''})")
            return
        self.material_name.setStyleSheet("color: #5b6270;")
        self.material_name.setText("vacío" if m == 0 else "" if m < 0 else self._names.get(m, ""))

    def set_context(self, doc, material_names: dict[int, str]) -> None:
        self._names = dict(material_names)
        self._show_material()
        if doc is not None:
            self.t_surf.set_known(bk.labels_of(doc, "surface"))
            self.t_body.set_known(bk.labels_of(doc, "body"))
            self.t_mod.set_known(bk.labels_of(doc, "module"))

    def _set(self, form: bk.BodyForm) -> None:
        self.label.setText(form.label)
        self.desc.setText(form.description)
        self.material.setText(form.material)
        self.t_surf.set_rows(form.surfaces)
        self.t_body.set_rows(form.bodies)
        self.t_mod.set_rows(form.modules)
        for name, row in self.rows.items():
            row.set(form.transform.get(name))

    def form(self) -> bk.BodyForm:
        transform = {n: p for n, r in self.rows.items() if (p := r.get()) is not None}
        return bk.BodyForm(self.label.text(), self.desc.text(), self.material.text(), self.module,
                           self.t_surf.rows(), self.t_body.rows(), self.t_mod.rows(), transform)

    def _field_widget(self, name):
        if name == "material":
            return self.material
        if name == "surface":
            return self.t_surf.field
        if name in ("body", "module"):
            return self.t_body.field if name == "body" else self.t_mod.field
        return super()._field_widget(name)


class BodyTab(_BodyLike):
    tab_id = bk.TAB_BODY
    module = False


class ModuleTab(_BodyLike):
    tab_id = bk.TAB_MODULE
    module = True


class CloneTab(BlockTab):
    """Clone: el módulo que se copia y la transformación de la copia."""

    tab_id = bk.TAB_CLONE

    def __init__(self, parent=None):
        super().__init__(parent)
        self._header("CLONE", 0)
        lab = QLabel("ORIG. MODULE:")
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.module = Field(50)
        self.module.setToolTip("Etiqueta del módulo que se copia (definido antes)")
        self.note = Field(268)
        self.note.setToolTip("Texto libre detrás de la etiqueta del módulo (PENGEOM no lo lee)")
        self.module.textEdited.connect(self._changed)
        self.note.textEdited.connect(self._changed)
        h = QHBoxLayout()
        h.addWidget(self.module)
        h.addWidget(self.note)
        h.addStretch(1)
        self.grid.addWidget(lab, 1, 0)
        self.grid.addLayout(h, 1, 1, 1, 3)
        self.grid.setRowMinimumHeight(2, 14)
        self.rows = _transform_rows(self, 3)
        self.grid.setColumnStretch(4, 1)
        self.outer.addStretch(1)

    def set_context(self, doc, material_names) -> None:
        if doc is not None:
            set_completions(self.module, bk.labels_of(doc, "module"))

    def _set(self, form: bk.CloneForm) -> None:
        self.label.setText(form.label)
        self.desc.setText(form.description)
        self.module.setText(form.module.strip())
        self.note.setText(form.module_note)
        for name, row in self.rows.items():
            row.set(form.transform.get(name))

    def form(self) -> bk.CloneForm:
        transform = {n: p for n, r in self.rows.items() if (p := r.get()) is not None}
        return bk.CloneForm(self.label.text(), self.desc.text(), self.module.text(), self.note.text(), transform)

    def _field_widget(self, name):
        if name == "module":
            return self.module
        return super()._field_widget(name)


class IncludeTab(BlockTab):
    """Include: el fichero incluido (en la carpeta de trabajo; INCLUDE* comparte etiquetas)."""

    tab_id = bk.TAB_INCLUDE
    browse = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyword = KeywordLabel("INCLUDE", toggles=True)
        self.keyword.toggled.connect(self._changed)
        self.file = Field(330)
        self.file.setToolTip("Fichero .geo incluido, en la carpeta del .in (como mucho 12 caracteres)")
        self.file.textEdited.connect(self._changed)
        self.btn_browse = QPushButton("…")
        self.btn_browse.setFixedWidth(30)
        self.btn_browse.setToolTip("Elegir el fichero (en la carpeta de trabajo)")
        self.btn_browse.clicked.connect(self.browse.emit)
        h = QHBoxLayout()
        h.addWidget(self.file)
        h.addWidget(self.btn_browse)
        h.addStretch(1)
        self.grid.addWidget(self.keyword, 0, 0)
        self.grid.addLayout(h, 0, 1, 1, 3)
        self.grid.setColumnStretch(4, 1)
        self.outer.addStretch(1)

    def _set(self, form: bk.IncludeForm) -> None:
        self.keyword.set_starred(form.starred)
        self.file.setText(form.filename)

    def form(self) -> bk.IncludeForm:
        return bk.IncludeForm(self.file.text(), self.keyword.starred)

    def _field_widget(self, name):
        return self.file if name == "filename" else super()._field_widget(name)


class CtTab(BlockTab):
    """CT: la caja del CT (CTBOX), su fichero y su colocación final."""

    tab_id = bk.TAB_CT

    def __init__(self, parent=None):
        super().__init__(parent)
        self._header("CT", 0, desc_width=260)
        lab = QLabel("CT File Name:")
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.file = Field(264)
        self.file.setToolTip("Fichero .ct (en la carpeta del .in; como mucho 12 caracteres)")
        self.file.textEdited.connect(self._changed)
        h = QHBoxLayout()
        h.addWidget(self.file)
        h.addStretch(1)
        self.grid.addWidget(lab, 1, 0)
        self.grid.addLayout(h, 1, 1, 1, 3)
        self.grid.setRowMinimumHeight(2, 14)
        self.rows = _transform_rows(self, 3)
        self.grid.setColumnStretch(4, 1)
        self.outer.addStretch(1)

    def _set(self, form: bk.CtForm) -> None:
        self.label.setText(form.label)
        self.desc.setText(form.description)
        self.file.setText(form.filename)
        for name, row in self.rows.items():
            row.set(form.transform.get(name))

    def form(self) -> bk.CtForm:
        transform = {n: p for n, r in self.rows.items() if (p := r.get()) is not None}
        return bk.CtForm(self.label.text(), self.desc.text(), self.file.text(), transform)

    def _field_widget(self, name):
        return self.file if name == "filename" else super()._field_widget(name)


TAB_CLASSES = (SurfaceTab, ImplicitTab, BodyTab, ModuleTab, CloneTab, IncludeTab, CtTab)
TAB_TITLES = {bk.TAB_SURFACE: "Surface", bk.TAB_IMPLICIT: "Surface (implicit)", bk.TAB_BODY: "Body",
              bk.TAB_MODULE: "Module", bk.TAB_CLONE: "Clone", bk.TAB_INCLUDE: "Include", bk.TAB_CT: "CT"}

