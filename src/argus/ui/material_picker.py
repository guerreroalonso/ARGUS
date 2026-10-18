"""Elegir un material (0.16.0, ADR-020): el campo que busca en la lista de PENELOPE y en los materiales personalizados
mientras se escribe, y el editor de una composición propia.

:class:`MaterialPicker` es un campo de texto con «▾»: la flecha (o ↓) despliega todos los materiales, y al escribir
la lista se queda con los que corresponden (en español o en inglés, sin tildes, cada palabra por separado:
«hueso cort» → «Hueso cortical (ICRP)»). Elegir uno emite :attr:`MaterialPicker.picked`. Lo escrito sin elegir se
queda como está (el nombre de una composición propia).

:class:`CompositionEditor` es la composición propia (penhmat, opción 1): fórmula o elementos con su cantidad
(átomos por molécula o fracción en masa), densidad y energía media de excitación, con la tabla de los elementos y lo
que falta. La usan «Añadir un material» y los materiales personalizados.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCompleter,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QRadioButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import fmt_num
from ..penhan import composition as cp
from ..penhan.material_names import ListEntry, search
from ..penhan.materials import MNIS

OK_COLOR, BAD_COLOR, WARN_COLOR = "#1a7f37", "#b42318", "#8a5a00"
ROLE_SECONDARY = Qt.ItemDataRole.UserRole + 1
ROLE_PERSONAL = Qt.ItemDataRole.UserRole + 2
ROLE_INDEX = Qt.ItemDataRole.UserRole + 3
POPUP_MIN_WIDTH = 560
VISIBLE_ITEMS = 14


def _span(text: str, color: str) -> str:
    return f"<span style='color:{color}'>{text}</span>"


def _number(text: str) -> float | None:
    from .source_panel import parse_number

    try:
        return parse_number(text) if text.strip() else None
    except ValueError:
        return None


def _radio_row(*buttons) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for b in buttons:
        lay.addWidget(b)
    lay.addStretch(1)
    return w


def recipe_text(comp: cp.Composition) -> str:
    """Lo que se escribe en «Fórmula o elementos» para una composición: la fórmula si son átomos enteros, si no los
    pares «elemento cantidad» (con punto decimal, como se leen)."""
    if comp.by == cp.BY_ATOMS and all(float(v).is_integer() and v < 1e6 for _z, v in comp.elements):
        return comp.formula()
    return " ".join(f"{cp.symbol(z)} {cp._num(v)}" for z, v in comp.elements)


class _Delegate(QStyledItemDelegate):
    """Cada material con lo que lo aclara en gris a su lado (el nombre en inglés de la lista de PENELOPE, o la
    composición de uno personalizado, que lleva «★»)."""

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = ("★ " if index.data(ROLE_PERSONAL) else "") + (opt.text or "")
        secondary = index.data(ROLE_SECONDARY) or ""
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        rect = opt.rect.adjusted(6, 0, -6, 0)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        pal = opt.palette
        painter.save()
        painter.setFont(opt.font)
        fm = opt.fontMetrics
        painter.setPen(pal.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
        main = fm.elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), main)
        used = fm.horizontalAdvance(main)
        if secondary and used + 40 < rect.width():
            r2 = rect.adjusted(used + 14, 0, 0, 0)
            color = pal.color(QPalette.ColorRole.HighlightedText) if selected else pal.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
            painter.setPen(color)
            painter.drawText(r2, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             fm.elidedText(secondary, Qt.TextElideMode.ElideRight, r2.width()))
        painter.restore()


class MaterialPicker(QWidget):
    """Campo del material con la lista desplegable que se acota al escribir (con la API de un QLineEdit: ``text``,
    ``setText``, ``textChanged``, ``textEdited``)."""

    picked = Signal(object)  # el ListEntry elegido
    textChanged = Signal(str)
    textEdited = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.edit = QLineEdit()
        self.button = QToolButton()
        self.button.setText("▾")
        self.button.setToolTip("Ver todos los materiales (también con ↓)")
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)
        self.entries: list[ListEntry] = []
        self._shown: list[ListEntry] = []
        self.model = QStandardItemModel(self)
        # El completador no filtra (lo hace :func:`search`: sin tildes, cada palabra): enseña lo que hay en el
        # modelo. Su padre es este widget, no el campo: QLineEdit borra el completador que es hijo suyo al
        # quitárselo (el fallo de 0.14–0.15 al pasar de «lista de PENELOPE» a «composición propia» y volver).
        self.completer = QCompleter(self.model, self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setMaxVisibleItems(VISIBLE_ITEMS)
        self.completer.setWidget(self.edit)
        popup = self.completer.popup()
        popup.setItemDelegate(_Delegate(popup))
        popup.setUniformItemSizes(True)
        self.completer.activated[QModelIndex].connect(self._on_activated)
        self.edit.textChanged.connect(self.textChanged)
        self.edit.textEdited.connect(self._on_edited)
        self.button.clicked.connect(self.show_all)
        self.edit.installEventFilter(self)

    # -- API de QLineEdit ---------------------------------------------------------------------------------------------
    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:  # noqa: N802 - como QLineEdit
        self.edit.setText(text)

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self.edit.setPlaceholderText(text)

    def setToolTip(self, text: str) -> None:  # noqa: N802
        super().setToolTip(text)
        self.edit.setToolTip(text)

    def setFocus(self, *args) -> None:  # noqa: N802
        self.edit.setFocus(*args)

    # -- lista --------------------------------------------------------------------------------------------------------
    def set_entries(self, entries: list[ListEntry]) -> None:
        self.entries = list(entries)
        self.button.setEnabled(bool(self.entries))
        self._fill(self.entries if not self.edit.text().strip() else search(self.entries, self.edit.text()))

    def _fill(self, entries: list[ListEntry]) -> None:
        self._shown = list(entries)
        self.model.clear()
        for i, e in enumerate(self._shown):
            it = QStandardItem(e.text)
            it.setData(e.secondary, ROLE_SECONDARY)
            it.setData(e.personal, ROLE_PERSONAL)
            it.setData(i, ROLE_INDEX)
            it.setToolTip(e.text + (f"\n{e.secondary}" if e.secondary else ""))
            self.model.appendRow(it)

    def _popup(self) -> None:
        if not self._shown:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix("")
        width = max(self.width(), POPUP_MIN_WIDTH)
        self.completer.complete(QRect(0, 0, width, self.edit.height()))
        # Sin fila elegida: Entrar o Tab no eligen el primero de la lista sin haberlo señalado (lo escrito se queda,
        # p. ej. el nombre de una composición propia); con ↓ o el ratón se elige.
        self.completer.popup().setCurrentIndex(QModelIndex())

    def show_all(self) -> None:
        """Despliega la lista entera (o la de lo escrito, si lo escrito tiene resultados)."""
        text = self.edit.text().strip()
        found = search(self.entries, text) if text else []
        self._fill(found if found else search(self.entries, ""))
        self.edit.setFocus()
        self._popup()

    def visible_entries(self) -> list[ListEntry]:
        return list(self._shown)

    def _on_edited(self, text: str) -> None:
        self._fill(search(self.entries, text))
        if text.strip():
            self._popup()
        else:
            self.completer.popup().hide()
        self.textEdited.emit(text)

    def _on_activated(self, index: QModelIndex) -> None:
        # Por la fila elegida, no por su texto: un material personalizado y uno de la lista no se confunden aunque
        # se escriban igual.
        i = index.data(ROLE_INDEX) if index.isValid() else None
        if not isinstance(i, int) or not 0 <= i < len(self._shown):
            return
        entry = self._shown[i]
        if self.edit.text() != entry.text:
            self.edit.setText(entry.text)
        self.picked.emit(entry)

    def pick(self, entry: ListEntry) -> None:
        """Como si el usuario lo eligiera de la lista."""
        self.edit.setText(entry.text)
        self.picked.emit(entry)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.edit and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Down \
                and not self.completer.popup().isVisible() and self.entries:
            self.show_all()
            return True
        return super().eventFilter(obj, event)


class CompositionEditor(QWidget):
    """La composición propia de un material: fórmula o elementos, cantidades, densidad y energía media de
    excitación, con la tabla de los elementos y lo que falta o conviene saber."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._by_user = False  # el usuario ha elegido átomos o fracciones a mano
        ov = QVBoxLayout(self)
        ov.setContentsMargins(0, 0, 0, 0)
        of = QFormLayout()
        ov.addLayout(of)
        self.formula = QLineEdit()
        self.formula.setPlaceholderText("C5H8O2, H2O, Ca10(PO4)6(OH)2… o H 0.111894 O 0.888106")
        self.formula.setToolTip("Una fórmula química (átomos por molécula) o pares «elemento cantidad»: el elemento "
                                "con su símbolo o su número atómico y la cantidad en átomos por molécula o en "
                                "fracción en masa (también en %)")
        of.addRow("Fórmula o elementos:", self.formula)
        self.rb_atoms = QRadioButton("átomos por molécula")
        self.rb_mass = QRadioButton("fracción en masa")
        g = QButtonGroup(self)
        g.addButton(self.rb_atoms)
        g.addButton(self.rb_mass)
        self.rb_atoms.setChecked(True)
        of.addRow("Cantidades:", _radio_row(self.rb_atoms, self.rb_mass))
        self.table = QTableWidget(0, 4)
        ov.addWidget(self.table)
        of = QFormLayout()
        ov.addLayout(of)
        self.table.setHorizontalHeaderLabels(["Elemento", "Z", "Átomos/molécula", "% en masa"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.density = QLineEdit()
        self.density.setPlaceholderText("p. ej. 1.19")
        self.density.setMaximumWidth(110)
        dens_row = QWidget()
        dl = QHBoxLayout(dens_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(self.density)
        dl.addWidget(QLabel("g/cm³"))
        dl.addStretch(1)
        of.addRow("Densidad:", dens_row)
        self.rb_i_argus = QRadioButton("regla de ARGUS")
        self.rb_i_argus.setToolTip("Sólidos y líquidos: aditividad de Bragg con los valores atómicos de fase "
                                   "condensada de ICRU 37, escalada para que el agua líquida dé 78 eV (ICRU 90); gases "
                                   "y elementos: la que calcula PENELOPE. La misma regla que para los tejidos de un "
                                   "TAC.")
        self.rb_i_pen = QRadioButton("la que calcula PENELOPE")
        self.rb_i_pen.setToolTip("Regla de Bragg con los valores de los elementos de PENELOPE (los de fase gaseosa): "
                                 "en sólidos y líquidos sale un 10 % más baja que la de ICRU")
        self.rb_i_val = QRadioButton("otra:")
        self.i_value = QLineEdit()
        self.i_value.setPlaceholderText("p. ej. 74")
        self.i_value.setMaximumWidth(80)
        self.i_value.setToolTip("Energía media de excitación en eV (p. ej. la de ICRU 37 o ICRU 90 de este material)")
        g = QButtonGroup(self)
        for b in (self.rb_i_argus, self.rb_i_pen, self.rb_i_val):
            g.addButton(b)
        self.rb_i_argus.setChecked(True)
        i_row = _radio_row(self.rb_i_argus, self.rb_i_pen, self.rb_i_val)
        i_row.layout().insertWidget(3, self.i_value)
        i_row.layout().insertWidget(4, QLabel("eV"))
        of.addRow("Energía media de excitación:", i_row)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.state.setTextFormat(Qt.TextFormat.RichText)
        ov.addWidget(self.state)
        self.formula.textChanged.connect(self._on_formula)
        self.rb_atoms.clicked.connect(self._on_by_clicked)
        self.rb_mass.clicked.connect(self._on_by_clicked)
        for w in (self.density, self.i_value):
            w.textChanged.connect(lambda _t: self._changed())
        for b in (self.rb_i_argus, self.rb_i_pen, self.rb_i_val):
            b.toggled.connect(lambda _on: self._changed())
        self.i_value.textEdited.connect(lambda _t: self.rb_i_val.setChecked(True))
        self._loading = False
        self.refresh()

    # -- valores ----------------------------------------------------------------------------------------------------
    def set_composition(self, comp: cp.Composition | None) -> None:
        """Rellena los campos con ``comp`` (None: vacíos)."""
        self._loading = True
        try:
            if comp is None:
                self.formula.setText("")
                self.density.setText("")
                self.rb_atoms.setChecked(True)
                self.rb_i_argus.setChecked(True)
                self.i_value.setText("")
                self._by_user = False
            else:
                self.formula.setText(recipe_text(comp))
                (self.rb_mass if comp.by == cp.BY_MASS else self.rb_atoms).setChecked(True)
                self._by_user = True
                self.density.setText(cp._num(comp.density))
                if comp.i_mode == cp.I_VALUE:
                    self.rb_i_val.setChecked(True)
                    # (sin valor solo en un fichero de materiales personalizados mal escrito: se ve que falta)
                    self.i_value.setText(cp._num(comp.i_value) if comp.i_value is not None else "")
                else:
                    (self.rb_i_pen if comp.i_mode == cp.I_PENELOPE else self.rb_i_argus).setChecked(True)
                    self.i_value.setText("")
        finally:
            self._loading = False
        self._changed()

    def parse(self) -> tuple[cp.Composition | None, str | None]:
        """(composición, por qué no vale) de lo que hay escrito."""
        text = self.formula.text().strip()
        if not text:
            return None, "escriba la fórmula química o los elementos con su cantidad"
        try:
            elements, _kind = cp.parse_elements(text)
        except ValueError as exc:
            return None, str(exc)
        rho = _number(self.density.text())
        if rho is None:
            return None, "falta la densidad" if not self.density.text().strip() else "la densidad no es un número"
        by = cp.BY_MASS if self.rb_mass.isChecked() else cp.BY_ATOMS
        if self.rb_i_val.isChecked():
            i_mode, i_value = cp.I_VALUE, _number(self.i_value.text())
            if i_value is None:
                return None, "falta la energía media de excitación (o elija otra opción)"
        else:
            i_mode, i_value = (cp.I_PENELOPE if self.rb_i_pen.isChecked() else cp.I_ARGUS), None
        comp = cp.Composition([(int(z), float(v)) for z, v in elements], by, float(rho), i_mode, i_value)
        probs = comp.problems()
        if probs:
            return None, "; ".join(probs)
        return comp, None

    def composition(self) -> cp.Composition | None:
        return self.parse()[0]

    def error(self) -> str | None:
        return self.parse()[1]

    # -- interfaz ---------------------------------------------------------------------------------------------------
    def _changed(self) -> None:
        if self._loading:
            return
        self.refresh()
        self.changed.emit()

    def _on_by_clicked(self) -> None:
        self._by_user = True
        self._changed()

    def _on_formula(self, text: str) -> None:
        if not self._loading:
            try:
                elements, kind = cp.parse_elements(text)
            except ValueError:
                elements, kind = [], None
            if kind is not None:  # una fórmula (átomos) o con «%» (masa): está claro
                (self.rb_mass if kind == cp.BY_MASS else self.rb_atoms).setChecked(True)
            elif not self._by_user and elements:
                guess = cp.guess_by(elements)
                if guess is not None:
                    (self.rb_mass if guess == cp.BY_MASS else self.rb_atoms).setChecked(True)
        self._changed()

    def _fill_table(self, comp: cp.Composition | None, elements) -> None:
        rows = comp.elements if comp is not None else elements
        self.table.setRowCount(len(rows))
        # Tan alta como sus filas (hasta 8; con más, barra de desplazamiento).
        h = self.table.horizontalHeader().sizeHint().height() + 2 * self.table.frameWidth()
        row_h = self.table.verticalHeader().defaultSectionSize()
        self.table.setFixedHeight(h + row_h * max(1, min(len(rows), 8)))
        atoms = comp.atoms() if comp is not None else {}
        mass = comp.mass_fractions() if comp is not None else {}
        for i, (z, _v) in enumerate(rows):
            vals = [cp.symbol(z), str(z), fmt_num(atoms[z], 6) if z in atoms else "",
                    f"{100 * mass[z]:.4f}".replace(".", ",") if z in mass else ""]
            for k, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, k, it)

    def refresh(self) -> str | None:
        """Rellena la tabla y lo que se dice debajo; devuelve por qué no vale (None si vale)."""
        text = self.formula.text().strip()
        try:
            elements, _kind = cp.parse_elements(text) if text else ([], None)
        except ValueError:
            elements = []
        comp, err = self.parse()
        self._fill_table(comp, elements)
        # Lo que daría cada opción de la energía media de excitación (con la densidad, si ya está).
        probe = comp
        if probe is None and elements:
            try:
                probe = cp.Composition([(int(z), float(v)) for z, v in elements],
                                       cp.BY_MASS if self.rb_mass.isChecked() else cp.BY_ATOMS,
                                       _number(self.density.text()) or 1.0)
                if probe.problems():
                    probe = None
            except (TypeError, ValueError):
                probe = None
        if probe is not None:
            argus = cp.Composition(probe.elements, probe.by, probe.density).excitation()[0]
            pen = cp.Composition(probe.elements, probe.by, probe.density, cp.I_PENELOPE).excitation()[0]
            self.rb_i_argus.setText(f"regla de ARGUS ({argus:.1f} eV)".replace(".", ","))
            self.rb_i_pen.setText(f"la que calcula PENELOPE ({pen:.1f} eV)".replace(".", ","))
        else:
            self.rb_i_argus.setText("regla de ARGUS")
            self.rb_i_pen.setText("la que calcula PENELOPE")
        notes = []
        if err:
            notes.append(_span("✖ " + html.escape(err[:1].upper() + err[1:]) + ".", BAD_COLOR))
        if comp is not None:
            notes.append(_span(html.escape(f"✔ {comp.summary()}."), OK_COLOR))
            total = comp.mass_sum()
            if total is not None and "%" not in text and abs(total - 1.0) > 0.01:
                notes.append(_span(html.escape(f"Las fracciones en masa suman {fmt_num(total, 6)}: penhmat las "
                                               "normaliza."), WARN_COLOR))
            _i, _yes, why = comp.excitation()
            if comp.i_mode == cp.I_ARGUS and "gas" in why:
                notes.append(html.escape("Menos de 0,02 g/cm³: es un gas y la regla de ARGUS usa la que calcula "
                                         "PENELOPE (la de fase gaseosa)."))
            n_iso = len(comp.isotopes())
            if n_iso > MNIS:
                notes.append(_span(html.escape(f"Con protones o alfas, este material tiene {n_iso} isótopos con datos "
                                               f"ENDF y penhmain admite {MNIS} entre todos los materiales."),
                                   WARN_COLOR))
        self.state.setText("<br>".join(notes))
        return err


__all__ = ["CompositionEditor", "MaterialPicker", "recipe_text"]
