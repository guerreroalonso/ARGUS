"""Piezas comunes del panel «Simulación»: secciones plegables, selectores de cuerpos y tablas de líneas.

Todas trabajan con los datos de ``penhan.sections``; no tocan el documento:
emiten los datos nuevos y el panel los aplica como un paso de deshacer.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.diagnostics import Severity
from ..penhan import sections as sc
from ..penhan.inputfile import resolve_body_ref
from ..penhan.source_edit import body_reference
from .panels import body_swatch_color, body_title, swatch
from .source_panel import EnergyEdit, NumberEdit

LEVEL_MARK = {Severity.ERROR: ("✖", "#b42318"), Severity.WARNING: ("⚠", "#9a6700"), Severity.INFO: ("ℹ", "#2f6db3")}


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: #666; font-size: 11px;")
    return lab


def row(*widgets, stretch: bool = True) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for x in widgets:
        if isinstance(x, str):
            lab = QLabel(x)
            lab.setStyleSheet("color: #666;")
            lay.addWidget(lab)
        else:
            lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


# ---------------------------------------------------------------------------
# Sección plegable con resumen
# ---------------------------------------------------------------------------

class CollapsibleSection(QWidget):
    toggled = Signal(bool)

    def __init__(self, title: str, content: QWidget, expanded: bool = False, parent=None):
        super().__init__(parent)
        self.title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QWidget()
        head.setObjectName("sectionHead")
        head.setStyleSheet("#sectionHead { background: palette(alternate-base); border-radius: 3px; }")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(2, 1, 6, 1)
        hl.setSpacing(4)
        self.button = QToolButton()
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setAutoRaise(True)
        self.button.setCheckable(True)
        self.button.setText(title)
        f = self.button.font()
        f.setBold(True)
        self.button.setFont(f)
        self.button.toggled.connect(self.set_expanded)
        hl.addWidget(self.button)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        hl.addWidget(self.status)
        self.summary = QLabel()
        self.summary.setStyleSheet("color: #666;")
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hl.addWidget(self.summary, 1)
        head.mousePressEvent = lambda e: self.button.toggle()
        lay.addWidget(head)
        self.content = content
        lay.addWidget(content)
        self.button.setChecked(expanded)
        self.set_expanded(expanded)

    def is_expanded(self) -> bool:
        return self.button.isChecked()

    def set_expanded(self, on: bool) -> None:
        if self.button.isChecked() != on:
            self.button.setChecked(on)
            return
        self.button.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.content.setVisible(on)
        self.toggled.emit(on)

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)
        self.summary.setToolTip(text)

    def set_status(self, level: Severity | None, tooltip: str = "") -> None:
        if level is None:
            self.status.setText("")
            self.status.setToolTip("")
            return
        mark, color = LEVEL_MARK[level]
        self.status.setText(f"<span style='color:{color}'>{mark}</span>")
        self.status.setToolTip(tooltip)


# ---------------------------------------------------------------------------
# Cuerpos
# ---------------------------------------------------------------------------

def body_ref_for(geo, kb: int) -> tuple[str | None, int | None]:
    """Cómo escribir el cuerpo ``kb`` en el .in: por su etiqueta (no cambia al editar la geometría) si la tiene y
    penhmain la lleva a ese cuerpo; si no, por su número. Las copias de un CLONE, la envolvente y los cuerpos de
    un INCLUDE sin «*» no tienen etiqueta: una en blanco sería la del último de ellos."""
    if geo is None or not 1 <= kb <= geo.nbodys:
        return None, kb
    ref = body_reference(geo, kb)
    return ref["label"], ref["kb"]


def resolve_ref(geo, label: str | None, kb: int | None, blank_search: bool = True) -> int:
    """Cuerpo al que penhmain aplica la referencia (0: ninguno). ``blank_search=False`` en los detectores de
    impacto (IDBODY), que no buscan una etiqueta en blanco."""
    return resolve_body_ref(geo, label, kb, blank_search=blank_search)


def ref_text(label: str | None, kb: int | None) -> str:
    return f"«{label.strip()}»" if label is not None else f"n.º {kb}"


def compact_combo(combo: QComboBox, chars: int = 10) -> QComboBox:
    """Que el desplegable no imponga su texto más largo como anchura mínima del panel."""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    return combo


class BodyCombo(QComboBox):
    """Cuerpo de la geometría (o todos los de un material, si se permite)."""

    def __init__(self, geo, allow_material: bool = False, parent=None):
        super().__init__(parent)
        self.geo = geo
        self._refs: dict[str, tuple[str | None, int | None]] = {}
        compact_combo(self, 10)
        if geo is not None:
            for b in geo.bodies:
                if b.origin == "enclosure":
                    continue
                mat = "vacío" if b.material == 0 else ("CT" if b.material == 9999 else f"mat. {b.material}")
                self.addItem(swatch(body_swatch_color(geo, b.index)), f"{body_title(geo, b.index)} · {mat}",
                             f"kb:{b.index}")
            if allow_material:
                for m in geo.materials():
                    self.addItem(f"Todos los cuerpos del material {m}", f"mat:{m}")

    def set_ref(self, label: str | None, kb: int | None) -> None:
        if kb is not None and kb < 0:
            i = self.findData(f"mat:{-kb}")
        else:
            k = resolve_ref(self.geo, label, kb)
            i = self.findData(f"kb:{k}") if k else -1
        if i < 0:
            # Referencia que no existe en la geometría: se muestra en rojo tal cual.
            key = f"ref:{len(self._refs)}"
            self._refs[key] = (label, kb)
            self.addItem(f"{ref_text(label, kb)} (no existe)", key)
            i = self.count() - 1
            self.setItemData(i, QBrush(QColor("#b42318")), Qt.ItemDataRole.ForegroundRole)
        self.setCurrentIndex(i)

    def ref(self) -> tuple[str | None, int | None]:
        kind, _, val = str(self.currentData()).partition(":")
        if kind == "kb":
            return body_ref_for(self.geo, int(val))
        if kind == "mat":
            return None, -int(val)
        return self._refs[f"ref:{val}"]


class BodyChecklist(QWidget):
    """Lista de cuerpos con casillas (para los cuerpos de un detector)."""

    changed = Signal(list)  # list[sc.BodyRef]

    def __init__(self, parent=None, allow_void: bool = False, blank_search: bool = True):
        super().__init__(parent)
        self.geo = None
        self.allow_void = allow_void
        self.blank_search = blank_search  # False en los detectores de impacto (IDBODY)
        self._updating = False
        # Las referencias como están escritas en el .in (con el cuerpo al que llevan, o None): marcar o desmarcar un
        # cuerpo no reescribe las de los demás (0.16.3).
        self._written: list[tuple[int | None, sc.BodyRef]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtrar cuerpos…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter)
        lay.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setMinimumHeight(100)
        self.list.itemChanged.connect(self._on_item)
        lay.addWidget(self.list)

    def sizeHint(self) -> QSize:
        return QSize(260, 150)

    def set_geometry(self, geo) -> None:
        self.geo = geo
        self._written = []
        self._updating = True
        try:
            self.list.clear()
            if geo is None:
                return
            for b in geo.bodies:
                if b.origin == "enclosure":
                    continue
                void = b.material == 0
                mat = "vacío" if void else ("CT" if b.material == 9999 else f"material {b.material}")
                it = QListWidgetItem(swatch(body_swatch_color(geo, b.index)), f"{body_title(geo, b.index)} · {mat}")
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked)
                it.setData(Qt.ItemDataRole.UserRole, ("kb", b.index))
                if void and not self.allow_void:
                    it.setToolTip("Un cuerpo vacío no puede formar parte de un detector (IRETRN 3084/3100).")
                    it.setForeground(QBrush(QColor("#999999")))
                self.list.addItem(it)
        finally:
            self._updating = False

    def set_refs(self, refs: list[sc.BodyRef]) -> None:
        self._updating = True
        try:
            for i in reversed(range(self.list.count())):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole)[0] == "ref":
                    self.list.takeItem(i)
            active = set()
            missing = []
            self._written = []
            for r in refs:
                k = resolve_ref(self.geo, r.label, r.kb, self.blank_search)
                if self.geo is not None and 1 <= k <= self.geo.nbodys:
                    active.add(k)
                    self._written.append((k, r))
                else:
                    missing.append(r)
                    self._written.append((None, r))
            for i in range(self.list.count()):
                it = self.list.item(i)
                it.setCheckState(Qt.CheckState.Checked if it.data(Qt.ItemDataRole.UserRole)[1] in active
                                 else Qt.CheckState.Unchecked)
            for n, r in enumerate(missing):
                it = QListWidgetItem(f"{ref_text(r.label, r.kb)} (no existe en la geometría)")
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked)
                it.setForeground(QBrush(QColor("#b42318")))
                it.setData(Qt.ItemDataRole.UserRole, ("ref", (r.label, r.kb)))
                self.list.insertItem(n, it)
        finally:
            self._updating = False
        self._filter(self.filter.text())

    def refs(self) -> list[sc.BodyRef]:
        """Las referencias de los cuerpos marcados: las que ya estaban escritas, como están y en su orden; detrás, las
        de los que se han marcado ahora."""
        kbs: list[int] = []
        others: list[tuple] = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            kind, val = it.data(Qt.ItemDataRole.UserRole)
            (kbs if kind == "kb" else others).append(val)
        from ..penhan.source_edit import merge_refs

        kept: list[tuple[int | None, sc.BodyRef]] = []
        done: set[int] = set()
        for k, r in self._written:
            if k is not None and k in kbs and k not in done:
                kept.append((k, sc.BodyRef(r.label, r.kb)))
                done.add(k)
            elif k is None and (r.label, r.kb) in others:
                kept.append((None, sc.BodyRef(r.label, r.kb)))
                others.remove((r.label, r.kb))
        kept += [(None, sc.BodyRef(*val)) for val in others]
        return merge_refs(kept, [(k, sc.BodyRef(*body_ref_for(self.geo, k))) for k in kbs if k not in done], kbs)

    def checked_bodies(self) -> set[int]:
        return {resolve_ref(self.geo, r.label, r.kb, self.blank_search) for r in self.refs()} - {0}

    def _filter(self, text: str) -> None:
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _on_item(self, item) -> None:
        if self._updating:
            return
        kind, val = item.data(Qt.ItemDataRole.UserRole)
        if (kind == "kb" and not self.allow_void and item.checkState() == Qt.CheckState.Checked
                and self.geo is not None and self.geo.body(val).material == 0):
            # Un cuerpo vacío no puede ir en un detector: se desmarca (sí se puede desmarcar uno que ya lo esté).
            self._updating = True
            try:
                item.setCheckState(Qt.CheckState.Unchecked)
            finally:
                self._updating = False
            from PySide6.QtWidgets import QToolTip

            QToolTip.showText(self.list.mapToGlobal(self.list.visualItemRect(item).center()),
                              "Un cuerpo vacío no puede formar parte de un detector (IRETRN 3084/3100).", self.list)
            return
        self.changed.emit(self.refs())


# ---------------------------------------------------------------------------
# Tabla de líneas «palabra cuerpo valores» (DSMAX, EABSB, EFIELD, IFORCE...)
# ---------------------------------------------------------------------------

class Column:
    """Columna de valores: 'real', 'energy', 'int' o 'choice' (con ``choices`` [(valor, texto)]).

    ``nvals``: cuántos valores de la línea ocupa (una elección puede fijar dos,
    p. ej. partícula e interacción de IFORCE; entonces su valor es una tupla).
    """

    def __init__(self, title: str, kind: str = "real", default=0.0, tooltip: str = "", minimum=None,
                 choices=None, width: int = 80, nvals: int = 1):
        self.title, self.kind, self.default, self.tooltip = title, kind, default, tooltip
        self.minimum, self.choices, self.width, self.nvals = minimum, choices, width, nvals


def _same_value(a, b) -> bool:
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        try:
            return [float(x) for x in a] == [float(x) for x in b]
        except TypeError:
            return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


class BodyListEditor(QWidget):
    """Una fila por línea del .in: el cuerpo y sus valores. Emite la lista completa al cambiar."""

    changed = Signal(list)  # list[sc.BodyValue]
    bodySelected = Signal(int)  # cuerpo de la fila seleccionada (0: ninguno)

    def __init__(self, keyword: str, columns: list[Column], allow_material: bool = False, max_rows: int = 50,
                 choices_for=None, parent=None):
        super().__init__(parent)
        self.keyword = keyword
        self.columns = columns
        self.allow_material = allow_material
        self.max_rows = max_rows
        self.choices_for = choices_for  # (columna, valores de la fila) -> choices (p. ej. ICOL según KP)
        self.geo = None
        self._entries: list[sc.BodyValue] = []
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.table = QTableWidget(0, 1 + len(columns))
        self.table.setHorizontalHeaderLabels(["Cuerpo"] + [c.title for c in columns])
        for i, c in enumerate(columns, start=1):
            self.table.horizontalHeaderItem(i).setToolTip(c.tooltip)
            self.table.setColumnWidth(i, c.width)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selection)
        lay.addWidget(self.table)
        btns = QHBoxLayout()
        self.btn_add = QPushButton("Añadir")
        self.btn_add.clicked.connect(self.add_row)
        self.btn_remove = QPushButton("Quitar")
        self.btn_remove.clicked.connect(self._remove)
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_remove)
        btns.addStretch(1)
        lay.addLayout(btns)
        self._resize()

    # -- datos -------------------------------------------------------------------------
    def set_entries(self, entries: list[sc.BodyValue], geo) -> None:
        same = geo is self.geo and [(e.label, e.kb, list(e.values)) for e in entries] == \
            [(e.label, e.kb, list(e.values)) for e in self._entries]
        if same:
            return
        self.geo = geo
        self._entries = [sc.BodyValue(e.label, e.kb, list(e.values)) for e in entries]
        self._rebuild()

    def entries(self) -> list[sc.BodyValue]:
        return [sc.BodyValue(e.label, e.kb, list(e.values)) for e in self._entries]

    def _defaults(self) -> list:
        out = []
        for c in self.columns:
            out += list(c.default) if c.nvals > 1 else [c.default]
        return out

    def _offsets(self) -> list[int]:
        offs, o = [], 0
        for c in self.columns:
            offs.append(o)
            o += c.nvals
        return offs

    def _rebuild(self) -> None:
        self._updating = True
        try:
            self.table.setRowCount(len(self._entries))
            for r, e in enumerate(self._entries):
                combo = BodyCombo(self.geo, self.allow_material)
                combo.set_ref(e.label, e.kb)
                combo.currentIndexChanged.connect(lambda _i, r=r: self._on_body(r))
                self.table.setCellWidget(r, 0, combo)
                vals = list(e.values) + self._defaults()[len(e.values):]
                for c, (col, o) in enumerate(zip(self.columns, self._offsets()), start=1):
                    v = vals[o] if col.nvals == 1 else tuple(vals[o:o + col.nvals])
                    self.table.setCellWidget(r, c, self._value_widget(r, c, col, v, vals))
        finally:
            self._updating = False
        self.btn_add.setEnabled(len(self._entries) < self.max_rows and self.geo is not None)
        self._resize()

    def _value_widget(self, r: int, c: int, col: Column, v, vals) -> QWidget:
        if col.kind == "choice":
            w = compact_combo(QComboBox(), 8)
            choices = self.choices_for(c - 1, vals) if self.choices_for else col.choices
            for val, text in choices:
                w.addItem(text, val)
            i = next((k for k in range(w.count()) if _same_value(w.itemData(k), v)), -1)
            if i < 0:
                w.addItem(f"{v} (no válido)", v)
                i = w.count() - 1
            w.setCurrentIndex(i)
            w.currentIndexChanged.connect(lambda _i, r=r, c=c, w=w: self._on_value(r, c, w.currentData()))
            return w
        cls = EnergyEdit if col.kind == "energy" else NumberEdit
        w = cls(minimum=col.minimum, width=col.width - 6, tooltip=col.tooltip)
        if col.kind == "int":
            w.format = lambda x: str(int(x))  # type: ignore[method-assign]
        w.set_value(float(v))
        w.valueEdited.connect(lambda x, r=r, c=c, col=col: self._on_value(r, c, int(x) if col.kind == "int" else x))
        return w

    def _resize(self) -> None:
        n = self.table.rowCount()
        h = self.table.horizontalHeader().height() + 4 + max(n, 1) * 30
        self.table.setFixedHeight(min(h, 260))

    # -- cambios -------------------------------------------------------------------------
    def _emit(self) -> None:
        self.changed.emit(self.entries())

    def _on_body(self, r: int) -> None:
        if self._updating or r >= len(self._entries):
            return
        combo = self.table.cellWidget(r, 0)
        label, kb = combo.ref()
        self._entries[r].label, self._entries[r].kb = label, kb
        self._emit()

    def _on_value(self, r: int, c: int, v) -> None:
        if self._updating or r >= len(self._entries):
            return
        e = self._entries[r]
        vals = list(e.values) + self._defaults()[len(e.values):]
        o = self._offsets()[c - 1]
        if isinstance(v, (tuple, list)):
            vals[o:o + len(v)] = list(v)
        else:
            vals[o] = v
        e.values = vals
        if self.choices_for is not None:  # p. ej. al cambiar la partícula cambian las interacciones posibles
            QTimer.singleShot(0, self._rebuild)  # no se destruye el widget dentro de su propia señal
        self._emit()

    def add_row(self, kb: int | None = None) -> None:
        if self.geo is None or len(self._entries) >= self.max_rows:
            return
        if not kb:
            used = {resolve_ref(self.geo, e.label, e.kb) for e in self._entries}
            cands = [b.index for b in self.geo.bodies if b.material > 0 and b.origin != "enclosure"
                     and b.index not in used] or [1]
            kb = cands[0]
        label, num = body_ref_for(self.geo, kb)
        self._entries.append(sc.BodyValue(label, num, self._defaults()))
        self._rebuild()
        self._emit()

    def _remove(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self._entries):
            del self._entries[r]
            self._rebuild()
            self._emit()

    def _on_selection(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self._entries):
            e = self._entries[r]
            self.bodySelected.emit(resolve_ref(self.geo, e.label, e.kb) if (e.kb or 0) >= 0 else 0)
        else:
            self.bodySelected.emit(0)


# ---------------------------------------------------------------------------
# Número de intervalos (con escala lineal o logarítmica: el signo en el .in)
# ---------------------------------------------------------------------------

class BinsEdit(QWidget):
    """Número de intervalos y escala; en el .in, un número negativo es escala logarítmica."""

    valueEdited = Signal(int)

    def __init__(self, maximum: int = 1000, allow_log: bool = True, parent=None, minimum: int = 1,
                 strict_max: bool = True):
        """``strict_max``: más de ``maximum`` en el .in es un error de penhmain (se marca); si no, penhmain se queda
        con ``maximum`` (LTRACK: guarda como mucho 50 lluvias) y se enseña ese."""
        super().__init__(parent)
        from PySide6.QtWidgets import QSpinBox

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.spin = QSpinBox()
        self.spin.setRange(minimum, maximum)  # 0 intervalos no vale (penhmain se detendría)
        self._minimum = minimum
        self.spin.setKeyboardTracking(False)
        self._strict_max = strict_max
        lay.addWidget(self.spin)
        self.scale = QComboBox()
        self.scale.addItem("lineal", 1)
        self.scale.addItem("logarítmica", -1)
        self.scale.setVisible(allow_log)
        lay.addWidget(self.scale)
        lay.addStretch(1)
        self._updating = False
        self.spin.valueChanged.connect(self._emit)
        self.scale.currentIndexChanged.connect(self._emit)

    def set_value(self, n: int) -> None:
        self._updating = True
        try:
            self.spin.setValue(abs(int(n)))
            self.scale.setCurrentIndex(1 if n < 0 else 0)
            # Un 0 del .in no cabe en el cuadro (mínimo 1), ni uno de más (máximo): se marcan para que no pasen por
            # buenos (el cuadro enseñaría el mínimo o el máximo).
            low = abs(int(n)) < self._minimum
            high = self._strict_max and abs(int(n)) > self.spin.maximum()
            self.spin.setStyleSheet("QSpinBox { background: #f8d7da; }" if low or high else "")
            if low:
                self.spin.setToolTip(f"El .in dice {int(n)}: penhmain se detendría. Elija otro número.")
            elif high:
                self.spin.setToolTip(f"El .in dice {abs(int(n))}: penhmain admite como mucho {self.spin.maximum()}. "
                                     "Elija otro número.")
            else:
                self.spin.setToolTip("")
        finally:
            self._updating = False

    def value(self) -> int:
        return self.spin.value() * int(self.scale.currentData())

    def _emit(self, *_a) -> None:
        if not self._updating:
            self.spin.setStyleSheet("")
            self.spin.setToolTip("")
            self.valueEdited.emit(self.value())


class NameEdit(QLineEdit):
    """Nombre de fichero (hasta 20 caracteres); vacío = el nombre por defecto de penhmain."""

    nameEdited = Signal(str)

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setMaxLength(20)
        self.setPlaceholderText(placeholder)
        self.editingFinished.connect(self._commit)

    def set_name(self, name: str) -> None:
        if self.hasFocus() and self.isModified():
            return
        self.setText(name)
        self.setModified(False)

    def _commit(self) -> None:
        if not self.isModified():
            return
        self.setModified(False)
        self.nameEdited.emit(self.text().strip())


# ---------------------------------------------------------------------------------------------------------------------
# Tiempo con unidades (TIME): de segundos a semanas
# ---------------------------------------------------------------------------------------------------------------------

TIME_UNITS: tuple[tuple[str, float], ...] = (("s", 1.0), ("min", 60.0), ("h", 3600.0), ("días", 86400.0),
                                             ("semanas", 604800.0))
MAX_TIME = 1.0e9  # s: unos 31,7 años (lo que pidió Rafael como máximo)
TIME_UNIT_KEY = "trabajo/unidad_tiempo"


def _short_number(v: float) -> str:
    """Seis cifras como mucho, como se escribe en el .in: «3306.88», «2e9», «1209600»."""
    text = f"{v:.6g}"
    if "e" in text:
        mant, exp = text.split("e")
        text = f"{mant}e{int(exp)}"
    return text


class DurationEdit(QWidget):
    """Un tiempo en la unidad que se elija (s, min, h, días o semanas), guardado en segundos y como mucho
    ``maximum`` segundos. ``secondsEdited`` da los segundos de lo escrito."""

    secondsEdited = Signal(float)

    def __init__(self, parent=None, *, maximum: float = MAX_TIME, tooltip: str = "", settings=None):
        super().__init__(parent)
        self.maximum = float(maximum)
        self._seconds: float | None = None
        self._settings = settings
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = NumberEdit(minimum=0.0, width=90, tooltip=tooltip)
        self.edit.format = _short_number  # type: ignore[method-assign]  # sin decimales de más al cambiar
        self.unit = QComboBox()
        for name, factor in TIME_UNITS:
            self.unit.addItem(name, factor)
        self.unit.setToolTip("Unidad del tiempo (en el .in va en segundos)")
        lay.addWidget(self.edit)
        lay.addWidget(self.unit)
        saved = self._load_unit()
        i = self.unit.findText(saved) if saved else -1
        self.unit.setCurrentIndex(i if i >= 0 else 0)
        self.edit.valueEdited.connect(self._on_value)
        self.unit.currentIndexChanged.connect(self._on_unit)

    def _store(self):
        if self._settings is not None:
            return self._settings
        from .settings import app_settings

        return app_settings()

    def _load_unit(self) -> str:
        try:
            return str(self._store().value(TIME_UNIT_KEY, "") or "")
        except Exception:  # noqa: BLE001 - sin ajustes, en segundos
            return ""

    @property
    def factor(self) -> float:
        return float(self.unit.currentData() or 1.0)

    def seconds(self) -> float | None:
        return self._seconds

    def set_seconds(self, value: float | None) -> None:
        self._seconds = value
        self.edit.set_value(None if value is None else value / self.factor)

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - como QLineEdit
        self.edit.setPlaceholderText(text)

    def _on_unit(self, _i: int) -> None:
        self.edit.set_value(None if self._seconds is None else self._seconds / self.factor)
        try:
            self._store().setValue(TIME_UNIT_KEY, self.unit.currentText())
        except Exception:  # noqa: BLE001
            pass

    def _on_value(self, v: float) -> None:
        secs = v * self.factor
        if secs > self.maximum * (1 + 1e-12):
            from ..core.numfmt import fmt_num

            years = self.maximum / 3.15576e7
            msg = (f"Como mucho {self.maximum:.0e} s ({fmt_num(years, 3)} años)".replace("e+0", "e")
                   .replace("1e9", "10⁹"))
            self.edit.setStyleSheet("QLineEdit { background: #f8d7da; }")
            self.edit.setToolTip(msg)
            self.edit._flash.start()
            self.edit.set_value(None if self._seconds is None else self._seconds / self.factor)
            return
        if abs(secs - round(secs)) < 1e-6 * max(1.0, secs):
            secs = float(round(secs))  # 2 semanas = 1209600 s, no 1209599.9999
        self._seconds = secs
        self.secondsEdited.emit(secs)

