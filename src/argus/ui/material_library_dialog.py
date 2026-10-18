"""«Materiales personalizados…» (0.16.0, ADR-020): las composiciones propias que el usuario guarda para todos sus
casos, con sus botones: nuevo, editar, duplicar y editar (una aleación con otra proporción), borrar, exportar e
importar. La biblioteca es :class:`argus.penhan.library.Library` (un fichero en la carpeta de datos del usuario).
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.numfmt import fmt_num, plural
from ..penhan import composition as cp
from ..penhan.library import MAX_NAME, Library, LibraryMaterial, read_file, write_file
from ..render.colors import material_color
from .material_picker import CompositionEditor
from .panels import swatch
from .screen_fit import fit_to_screen

BAD_COLOR, GREY = "#b42318", "#666666"


def _safe(fn, default: str = "—") -> str:
    """Lo que se enseña de un material de un fichero escrito a mano: si no se puede calcular, «—»."""
    try:
        return str(fn())
    except Exception:  # noqa: BLE001
        return default


class LibraryMaterialDialog(QDialog):
    """Un material personalizado: su nombre, su composición y su color. «Guardar» lo escribe en la biblioteca
    (``replace``: el nombre del que se está editando)."""

    def __init__(self, parent, library: Library, *, title: str, name: str = "",
                 composition: cp.Composition | None = None, color: str | None = None, replace: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.library = library
        self.replace = replace
        self.saved: LibraryMaterial | None = None
        self._color = QColor(color or material_color(len(library.items) + 1))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)
        self.name = QLineEdit(name)
        self.name.setMaxLength(MAX_NAME)
        self.name.setPlaceholderText("p. ej. Latón 70/30, acero inoxidable 316L…")
        form.addRow("Nombre:", self.name)
        self.editor = CompositionEditor()
        lay.addWidget(self.editor)
        form2 = QFormLayout()
        lay.addLayout(form2)
        self.btn_color = QPushButton()
        self.btn_color.setAutoDefault(False)
        self.btn_color.setToolTip("El color que tendrá en las vistas al elegirlo para un material")
        self.btn_color.clicked.connect(self._pick_color)
        form2.addRow("Color:", self.btn_color)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Guardar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.editor.set_composition(composition)
        self.name.textChanged.connect(lambda _t: self._update())
        self.editor.changed.connect(self._update)
        self._paint_color()
        self._update()
        fit_to_screen(self, 640, self.sizeHint().height())

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(self._color, self, "Color del material")
        if c.isValid():
            self._color = c
            self._paint_color()

    def _paint_color(self) -> None:
        self.btn_color.setIcon(swatch(self._color.name(), 16))
        self.btn_color.setText(self._color.name())

    def problem(self) -> str | None:
        name = " ".join(self.name.text().split())
        if not name:
            return "Escriba el nombre del material."
        other = self.library.find(name)
        if other is not None and (self.replace is None or other.name.casefold() != self.replace.casefold()):
            return f"Ya hay un material personalizado «{other.name}»: elija otro nombre."
        err = self.editor.error()
        return f"Composición: {err}." if err else None

    def _update(self) -> None:
        p = self.problem()
        self.status.setText(f"<span style='color:{BAD_COLOR}'>✖ {html.escape(p)}</span>" if p else "")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(p is None)

    def accept(self) -> None:
        if self.problem() is not None:
            self._update()
            return
        m = LibraryMaterial(" ".join(self.name.text().split()), self.editor.composition(), self._color.name())
        try:
            self.saved = self.library.put(m, replace=self.replace)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Materiales personalizados", f"No se ha podido guardar: {exc}")
            return
        super().accept()


class LibraryDialog(QDialog):
    """La lista de los materiales personalizados y lo que se puede hacer con ellos. ``current``: la composición que
    se está escribiendo en otro diálogo (para guardarla desde aquí)."""

    def __init__(self, parent, library: Library | None = None, *, current: cp.Composition | None = None,
                 current_name: str = "", current_color: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Materiales personalizados")
        self.library = library if library is not None else Library()
        self.current = current
        self.current_name = current_name
        self.current_color = current_color
        lay = QVBoxLayout(self)
        intro = QLabel("Sus materiales de composición propia, para elegirlos en cualquier caso (en el campo del "
                       "material aparecen con «★», junto a los de la lista de PENELOPE) sin escribirlos otra vez. Para "
                       "variar un poco una composición (otra proporción de una aleación), «Duplicar y editar…».")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.where.setTextFormat(Qt.TextFormat.RichText)
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.where)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Nombre", "Composición", "Densidad (g/cm³)", "I (eV)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.edit_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_new = QPushButton("Nuevo…")
        self.btn_edit = QPushButton("Editar…")
        self.btn_dup = QPushButton("Duplicar y editar…")
        self.btn_dup.setToolTip("Una copia para cambiarla (p. ej. otra proporción de la aleación); el original no se "
                                "toca")
        self.btn_del = QPushButton("Borrar")
        self.btn_current = QPushButton("Guardar el que estoy escribiendo…")
        self.btn_current.setToolTip("La composición propia del material que está añadiendo o cambiando")
        for b in (self.btn_new, self.btn_edit, self.btn_dup, self.btn_del, self.btn_current):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.btn_new.clicked.connect(self.new)
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_dup.clicked.connect(self.duplicate_selected)
        self.btn_del.clicked.connect(self.delete_selected)
        self.btn_current.clicked.connect(self.save_current)
        self.btn_current.setVisible(current is not None)
        self.buttons = QDialogButtonBox()
        self.btn_export = self.buttons.addButton("Exportar…", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_import = self.buttons.addButton("Importar…", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_export.setToolTip("Guardarlos en un fichero (para otro ordenador o para un compañero)")
        self.btn_import.setToolTip("Añadir los de un fichero exportado (los que ya tiene no se tocan)")
        close = self.buttons.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        self.btn_export.clicked.connect(self.export)
        self.btn_import.clicked.connect(self.import_)
        lay.addWidget(self.buttons)
        self.refresh()
        fit_to_screen(self, 760, 460)

    # -- lista -------------------------------------------------------------------------------------------------------
    def refresh(self, select: str | None = None) -> None:
        lib = self.library
        text = f"Se guardan en <tt>{html.escape(str(lib.path))}</tt> (son suyos: valen para todos sus casos)."
        if lib.unreadable:
            why = html.escape(lib.unreadable)
            text += (f"<br><span style='color:{BAD_COLOR}'>✖ El fichero no se puede leer ({why}): al guardar algo se "
                     "aparta (no se borra) y se empieza uno nuevo.</span>")
        if lib.problems:
            text += (f"<br><span style='color:{BAD_COLOR}'>✖ No se han podido leer: "
                     f"{html.escape('; '.join(lib.problems[:3]))}</span>")
        self.where.setText(text)
        t = self.table
        t.setRowCount(len(lib.items))
        for i, m in enumerate(lib.items):
            c = m.composition
            it = QTableWidgetItem(swatch(m.color or "#888888", 12), m.name)
            it.setData(Qt.ItemDataRole.UserRole, m.name)
            # Uno escrito a mano que no vale (Z fuera de 1-99, densidad 0 o NaN…) se lista con su problema, para
            # abrirlo y arreglarlo: nada de lo que se calcula de él puede impedir que se abra el diálogo.
            if m.problem:
                comp = QTableWidgetItem(f"✖ {m.problem}")
                comp.setForeground(QColor(BAD_COLOR))
            else:
                comp = QTableWidgetItem(_safe(c.formula))
            dens = QTableWidgetItem(_safe(lambda c=c: fmt_num(c.density, 6)))
            ii = QTableWidgetItem("—" if m.problem else _safe(lambda c=c: fmt_num(c.excitation()[0], 4)))
            tip = f"{m.name}: {m.summary()}" + (f"\n{m.note}" if m.note else "")
            for k, item in enumerate((it, comp, dens, ii)):
                item.setToolTip(tip)
                t.setItem(i, k, item)
        if select is not None:
            for i, m in enumerate(lib.items):
                if m.name == select:
                    t.selectRow(i)
                    break
        self._update_buttons()

    def selected(self) -> LibraryMaterial | None:
        rows = {it.row() for it in self.table.selectedItems()}
        if not rows:
            return None
        item = self.table.item(min(rows), 0)
        return self.library.find(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _update_buttons(self) -> None:
        sel = self.selected() is not None
        for b in (self.btn_edit, self.btn_dup, self.btn_del):
            b.setEnabled(sel)
        self.btn_export.setEnabled(bool(self.library.items))

    # -- acciones ----------------------------------------------------------------------------------------------------
    def _edit_dialog(self, **kw) -> LibraryMaterial | None:
        dlg = LibraryMaterialDialog(self, self.library, **kw)
        try:
            ok = dlg.exec() == QDialog.DialogCode.Accepted
            return dlg.saved if ok else None
        finally:
            dlg.deleteLater()

    def new(self) -> None:
        m = self._edit_dialog(title="Material personalizado nuevo")
        if m is not None:
            self.refresh(select=m.name)

    def edit_selected(self) -> None:
        m = self.selected()
        if m is None:
            return
        saved = self._edit_dialog(title=f"Editar «{m.name}»", name=m.name, composition=m.composition, color=m.color,
                                  replace=m.name)
        if saved is not None:
            self.refresh(select=saved.name)

    def duplicate_selected(self) -> None:
        m = self.selected()
        if m is None:
            return
        name = self.library.free_name(f"{m.name} (copia)")
        saved = self._edit_dialog(title=f"Copia de «{m.name}»", name=name, composition=m.composition, color=m.color)
        if saved is not None:
            self.refresh(select=saved.name)

    def delete_selected(self) -> None:
        m = self.selected()
        if m is None:
            return
        r = QMessageBox.question(self, "Materiales personalizados",
                                 f"¿Borrar «{m.name}» de sus materiales personalizados?\n\nLos .in que ya lo usan no "
                                 "cambian (llevan su composición).",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.remove(m.name)
        except OSError as exc:
            QMessageBox.warning(self, "Materiales personalizados", f"No se ha podido borrar: {exc}")
        self.refresh()

    def save_current(self) -> None:
        if self.current is None:
            return
        name = self.library.free_name(self.current_name or "Material")
        saved = self._edit_dialog(title="Guardar en materiales personalizados", name=name,
                                  composition=self.current, color=self.current_color)
        if saved is not None:
            self.refresh(select=saved.name)

    def export(self) -> None:
        start = str(Path.home() / "materiales_personalizados.json")
        path, _ = QFileDialog.getSaveFileName(self, "Exportar los materiales personalizados", start,
                                              "Materiales de ARGUS (*.json)")
        if not path:
            return
        try:
            write_file(path, self.library.items)
        except OSError as exc:
            QMessageBox.warning(self, "Materiales personalizados", f"No se ha podido exportar: {exc}")
            return
        QMessageBox.information(self, "Materiales personalizados",
                                f"Exportados {plural(len(self.library.items), 'material', 'materiales')} a {path}.")

    def import_(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar materiales personalizados", str(Path.home()),
                                              "Materiales de ARGUS (*.json);;Todos los ficheros (*)")
        if not path:
            return
        try:
            items, problems = read_file(path)
            added, same, renamed = self.library.merge([m for m in items if m.problem is None])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Materiales personalizados", f"No se ha podido importar {Path(path).name}: {exc}")
            return
        bad = len([m for m in items if m.problem is not None]) + len(problems)
        parts = [f"Añadidos: {added}."]
        if renamed:
            parts.append(f"Con otro nombre (ya tenía uno distinto con el suyo): {renamed}.")
        if same:
            parts.append(f"Ya los tenía: {same}.")
        if bad:
            parts.append(f"No se han podido leer: {bad}.")
        self.refresh()
        QMessageBox.information(self, "Materiales personalizados", " ".join(parts))


__all__ = ["LibraryDialog", "LibraryMaterialDialog"]
