"""Diálogo de un material: «Añadir un material» (una línea MFNAME más en el .in, sin tener que crear antes un cuerpo)
y «Composición del material N» (el mismo, para uno que ya está).

penhmain numera los materiales por el orden de sus líneas MFNAME: el nuevo es el siguiente número libre. El .mat se
genera con penhmat de una de sus dos maneras:

* **de la lista de PENELOPE** (opción 2 de penhmat): el campo del material despliega los 280 materiales de
  ``database/material-list.txt`` (con su nombre en español) y se acota al escribir (0.16.0); el elegido queda como
  «Agua líquida (PENELOPE 278)», y ese número es el que ARGUS le da a penhmat;
* **composición propia** (opción 1): fórmula química o elementos con su cantidad (átomos por molécula o fracción en
  masa), densidad y energía media de excitación (la regla de ARGUS, la que calcula PENELOPE u otro valor). Se guarda
  en el .in, en comentarios junto a su MFNAME (:mod:`argus.penhan.composition`) y, si se pide, en los materiales
  personalizados del usuario (:mod:`argus.penhan.library`), que el mismo campo ofrece después en cualquier caso.

El .mat lo genera después «Generar .mat y ENDF…» (panel Materiales) o «Ejecutar» (F9), con el penhmat del usuario; si
no está, materiales.txt dice qué contestar a penhmat.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..penhan import composition as cp
from ..penhan.library import Library, LibraryMaterial, clean_name
from ..penhan.material_names import ListEntry, penelope_entries, penelope_label, personal_entry
from ..penhan.materials import (
    known_number,
    listed_number,
    mat_filename_problem,
    match_material,
    read_mat_header,
    suggest_mat_filename,
)
from .material_picker import CompositionEditor, MaterialPicker, recipe_text
from .panels import swatch
from .screen_fit import fit_to_screen

OK_COLOR, BAD_COLOR, WARN_COLOR, GREY = "#1a7f37", "#b42318", "#8a5a00", "#666666"
LIST, OWN = "lista", "propia"


def _span(text: str, color: str) -> str:
    return f"<span style='color:{color}'>{text}</span>"


def _radio_row(*buttons) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for b in buttons:
        lay.addWidget(b)
    lay.addStretch(1)
    return w


def find_list_item(name: str, mlist: list[tuple[int, str]]) -> tuple[int, str] | None:
    """El material de la lista de PENELOPE de un nombre: «… (PENELOPE n)», el nombre de la lista o su nombre en
    español."""
    return match_material(name, mlist) if name else None


def library_entries(library: Library | None) -> list[ListEntry]:
    """Los materiales personalizados que se pueden elegir (los que tienen una composición que vale)."""
    if library is None:
        return []
    return [personal_entry(m.name, m.summary()) for m in library.items if m.problem is None]


@dataclass
class MaterialChoice:
    """Lo elegido en el diálogo de un material, para aplicarlo después al .in («Asignar materiales…» lo aplica con
    los demás, en un solo paso de deshacer)."""

    fname: str
    description: str
    color: str
    composition: cp.Composition | None  # la composición propia (None: de la lista de PENELOPE)
    saved_to_library: str | None = None


class MaterialDialog(QDialog):
    """Nombre, de dónde sale su composición (lista de PENELOPE o propia), fichero .mat y color de un material."""

    def __init__(self, parent, number: int, material_list: list[tuple[int, str]], folder: str | Path | None,
                 taken_files: set[str] | None = None, used_by: str = "", color: str = "#888888", *,
                 fname: str | None = None, description: str = "", recipe: cp.Composition | None = None,
                 edit: bool = False, library: Library | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Composición del material {number}" if edit else "Añadir un material")
        self.number = number
        self.mlist = material_list
        self.folder = Path(folder) if folder else None
        self.taken = set(taken_files or ())
        self.edit = edit
        self.library = library if library is not None else Library()
        self._color = QColor(color)
        self._auto_file = not edit  # el nombre del fichero sigue al del material hasta que el usuario lo escribe
        self._orig = (fname, description, recipe)
        self._problem: str | None = None
        self.saved_to_library: str | None = None  # el nombre con que se ha guardado en los personalizados
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>Material n.º {number}</b> (penhmain numera los materiales por el orden de sus líneas MFNAME "
                      "en el .in). Su .mat lo genera penhmat de una de sus dos maneras: con un material de la lista de "
                      "PENELOPE o con la composición que se le dé.")
        head.setWordWrap(True)
        lay.addWidget(head)
        if used_by:
            note = QLabel(f"La geometría ya usa este número: {html.escape(used_by)}.")
            note.setWordWrap(True)
            note.setStyleSheet("color: #555;")
            lay.addWidget(note)
        form = QFormLayout()
        lay.addLayout(form)
        self.name = MaterialPicker()
        self.name.setPlaceholderText("Escriba para buscar (agua, hueso, PMMA, plomo…) o pulse ▾ para ver todos")
        self.name.picked.connect(self._on_picked)
        form.addRow("Material:", self.name)
        self.rb_list = QRadioButton("De la lista de PENELOPE")
        self.rb_own = QRadioButton("Composición propia")
        self.rb_list.setToolTip("penhmat, opción 2: un material de database/material-list.txt, con su composición, "
                                "su densidad y su energía media de excitación")
        self.rb_own.setToolTip("penhmat, opción 1: la fórmula química o los elementos con su cantidad, la densidad y "
                               "la energía media de excitación (también los materiales personalizados)")
        g = QButtonGroup(self)
        g.addButton(self.rb_list)
        g.addButton(self.rb_own)
        form.addRow("Composición:", _radio_row(self.rb_list, self.rb_own))
        self.list_state = QLabel()
        self.list_state.setWordWrap(True)
        self.list_state.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.list_state)
        # Composición propia
        self.own_box = QGroupBox("Composición propia (penhmat, opción 1)")
        ov = QVBoxLayout(self.own_box)
        self.comp_editor = CompositionEditor()
        ov.addWidget(self.comp_editor)
        lib_row = QHBoxLayout()
        self.save_lib = QCheckBox("Guardarlo también en mis materiales personalizados")
        self.save_lib.setToolTip("Con su nombre, para elegirlo después en cualquier caso (en el campo del material, "
                                 "con «★») sin escribirlo otra vez; se puede editar o copiar en «Materiales "
                                 "personalizados…»")
        self.btn_library = QPushButton("Materiales personalizados…")
        self.btn_library.setAutoDefault(False)
        self.btn_library.setToolTip("Ver, editar, copiar, borrar, exportar o importar sus materiales personalizados")
        self.btn_library.clicked.connect(self.open_library)
        lib_row.addWidget(self.save_lib)
        lib_row.addStretch(1)
        lib_row.addWidget(self.btn_library)
        ov.addLayout(lib_row)
        lay.addWidget(self.own_box)
        # Los campos de la composición, también con sus nombres de siempre (los usan las pruebas y quien los llama).
        e = self.comp_editor
        self.formula, self.rb_atoms, self.rb_mass, self.table = e.formula, e.rb_atoms, e.rb_mass, e.table
        self.density, self.i_value, self.own_state = e.density, e.i_value, e.state
        self.rb_i_argus, self.rb_i_pen, self.rb_i_val = e.rb_i_argus, e.rb_i_pen, e.rb_i_val
        # Fichero y color
        form2 = QFormLayout()
        lay.addLayout(form2)
        self.file = QLineEdit()
        self.file.setMaxLength(20)
        self.file.setPlaceholderText("nombre.mat (hasta 20 caracteres)")
        form2.addRow("Fichero .mat:", self.file)
        self.btn_color = QPushButton()
        self.btn_color.setToolTip("Color del material en las vistas (solo en ARGUS)")
        self.btn_color.clicked.connect(self._pick_color)
        form2.addRow("Color:", self.btn_color)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setMinimumWidth(420)
        lay.addWidget(self.status)
        self.btn_newfile = QPushButton("Usar otro nombre de fichero (el que hay no se toca)")
        self.btn_newfile.setAutoDefault(False)
        self.btn_newfile.clicked.connect(self.propose_new_file)
        self.btn_newfile.setVisible(False)
        lay.addWidget(self.btn_newfile, 0, Qt.AlignmentFlag.AlignLeft)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Guardar" if edit else "Añadir")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        # Valores iniciales
        self._fill_entries()
        self.name.setText(description)
        if recipe is not None:
            self.rb_own.setChecked(True)
            self.comp_editor.set_composition(recipe)
        else:
            self.rb_list.setChecked(True)
        self.file.setText(fname if fname else self._free_file(f"material{number}"))
        # Señales
        self.name.textChanged.connect(self._on_name)
        self.file.textEdited.connect(self._on_file_edited)
        self.file.textChanged.connect(lambda _t: self._update())
        self.rb_own.toggled.connect(lambda _on: self._on_mode())
        self.comp_editor.changed.connect(self._on_composition)
        self.save_lib.toggled.connect(lambda _on: self._update())
        self._paint_color()
        self._on_mode(user=False)
        fit_to_screen(self, 700, self.sizeHint().height())

    # -- valores ------------------------------------------------------------------
    def fname(self) -> str:
        return self.file.text().strip()

    def description(self) -> str:
        return self.name.text().strip()

    def color(self) -> str:
        return self._color.name()

    def mode(self) -> str:
        return OWN if self.rb_own.isChecked() else LIST

    def list_item(self) -> tuple[int, str] | None:
        """El material de la lista de PENELOPE que corresponde al nombre (con «… (PENELOPE n)», el nombre de la lista
        o su nombre en español)."""
        return find_list_item(self.description(), self.mlist)

    def composition(self) -> cp.Composition | None:
        """La composición propia (None con la lista de PENELOPE o si no vale todavía)."""
        if self.mode() != OWN:
            return None
        return self.comp_editor.composition()

    def _own(self) -> tuple[cp.Composition | None, str | None]:
        return self.comp_editor.parse()

    def material_changed(self) -> bool:
        """¿Cambia lo que hay que generar? (otra composición, o pasar de la lista a propia o al revés)."""
        _f, desc, recipe = self._orig
        if self.mode() == OWN:
            return self.composition() != recipe
        if recipe is not None:
            return True
        return find_list_item(self.description(), self.mlist) != find_list_item(desc or "", self.mlist)

    # -- interfaz -----------------------------------------------------------------------
    def _fill_entries(self) -> None:
        self.name.set_entries(library_entries(self.library) + penelope_entries(self.mlist))

    def _on_picked(self, entry: ListEntry) -> None:
        if entry.personal:
            m = self.library.find(entry.key or entry.text)
            if m is None:
                return
            self.rb_own.setChecked(True)
            self.comp_editor.set_composition(m.composition)
            if m.color:
                self._color = QColor(m.color)
                self._paint_color()
            self.save_lib.setChecked(False)  # ya está guardado
        else:
            self.rb_list.setChecked(True)
        self._suggest_file()
        self._update()

    def _on_mode(self, user: bool = True) -> None:
        own = self.mode() == OWN
        if user:
            self._name_for_mode(own)
        self.own_box.setVisible(own)
        self.list_state.setVisible(not own)
        if own:
            self.name.setToolTip("Nombre del material (se guarda en el .in y en el .mat). Al escribir se proponen sus "
                                 "materiales personalizados (★) y los de la lista de PENELOPE")
        elif self.mlist:
            self.name.setToolTip("Escriba para buscar en la lista de materiales de PENELOPE (en español o en inglés) "
                                 "o pulse ▾ para verlos todos")
        else:
            self.name.setToolTip("Nombre del material (no se encuentra la lista de PENELOPE)")
        self._suggest_file()
        self._update()

    def _name_for_mode(self, own: bool) -> None:
        """Al pasar a «Composición propia», el nombre deja de decir que es de la lista («Agua líquida (PENELOPE 278)»
        → «Agua líquida»: con otra composición ya no es el n.º 278, y así tampoco se guarda en los personalizados con
        el nombre de uno de la lista); al volver a la lista, si el nombre es el de uno de ella, lleva su número, y si
        es el que tenía (un nombre propio, «Agua del maniquí (PENELOPE 278)»), vuelve a ser el de antes."""
        text = self.description()
        if own and listed_number(text) is not None:
            self._label_before = (clean_name(text), text)
            self.name.setText(clean_name(text))
        elif not own and text and listed_number(text) is None:
            before = getattr(self, "_label_before", None)
            if before is not None and before[0] == text:
                self.name.setText(before[1])
                return
            item = self.list_item()
            if item is not None:
                self.name.setText(penelope_label(item))

    def _on_name(self, _text: str) -> None:
        self._suggest_file()
        self._update()

    def _on_composition(self) -> None:
        self._suggest_file()  # con la composición ya se sabe si el .mat que hay en la carpeta es este
        self._update()

    def _suggest_file(self) -> None:
        if not self._auto_file:
            return
        text = self.description()
        if not text:
            return
        item = self.list_item() if self.mode() == LIST else None
        base = text
        if item is not None:
            base = text.split(" (PENELOPE")[0] if "(PENELOPE" in text else item[1]
        self.file.blockSignals(True)
        self.file.setText(self._free_file(base))
        self.file.blockSignals(False)

    def _free_file(self, base: str) -> str:
        """El fichero que se propone: el primero de «base.mat», «base2.mat»… que no es el de otro material del .in
        ni un .mat de otro material que ya está en la carpeta (de otro .in, de antes…); si el que está es este mismo
        material, se aprovecha. Sin distinguir mayúsculas (en Windows son el mismo fichero). Si aún no se sabe qué
        material es (un nombre que no es de la lista, una composición a medias), los que están no se proponen: no se
        sabe si son este (sin la lista de PENELOPE, con un nombre propio, sí: los .mat los genera el usuario)."""
        skip = set(self.taken)
        listing: dict[str, Path] = {}
        compare = self._can_compare()
        if self.folder is not None and (compare or self.mode() == OWN or self.mlist):
            try:
                listing = {p.name.lower(): p for p in self.folder.iterdir() if p.is_file()}
            except OSError:
                listing = {}
        cand = suggest_mat_filename(base, skip)
        for _ in range(200):
            have = listing.get(cand.lower())
            if have is None:
                return cand
            if compare and self._mismatch(have) is None:
                return have.name  # ya está y es este material: no hace falta generarlo
            skip.add(cand)
            cand = suggest_mat_filename(base, skip)
        return cand

    def _can_compare(self) -> bool:
        """¿Se sabe ya qué material es, para compararlo con un .mat que ya está? (con la lista, el número; con
        composición propia, una composición completa)."""
        if self.mode() == OWN:
            return self.composition() is not None
        return self.list_item() is not None

    def _mismatch(self, path: Path) -> str | None:
        """Por qué el .mat que ya está en ``path`` no es el material elegido (None si lo es). Solo con
        :meth:`_can_compare`."""
        if self.mode() == OWN:
            comp = self.composition()
            if comp is None:
                return None
            mc = cp.read_mat_composition(path)
            if mc is None:
                return "no es un fichero de material de PENELOPE: penhmain no lo podría leer"
            diffs = comp.differences(mc)
            return f"no tiene esta composición ({', '.join(diffs)}): penhmain usaría ese" if diffs else None
        item = self.list_item()
        if item is None:
            return None
        head = read_mat_header(path)
        if head is None:
            return "no es un fichero de material de PENELOPE: penhmain no lo podría leer"
        if head.penelope_id == item[0]:
            return None
        what = (f"es el n.º {head.penelope_id} de la lista de PENELOPE ({head.name})" if head.penelope_id
                else f"es otro material ({head.name}, de composición propia)")
        return f"{what}: penhmain usaría ese"

    @staticmethod
    def _file_what(path: Path) -> str:
        """Qué es un .mat que ya está, por su cabecera: «el n.º 82 de la lista de PENELOPE (LEAD)»…"""
        head = read_mat_header(path)
        if head is None:
            return "no es un fichero de material de PENELOPE"
        if head.penelope_id:
            return f"es el n.º {head.penelope_id} de la lista de PENELOPE: {head.name}"
        return f"es un material de composición propia: {head.name}"

    def _on_file_edited(self, _text: str) -> None:
        self._auto_file = False  # el usuario lo ha escrito: no se cambia solo

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(self._color, self, f"Color del material {self.number}")
        if c.isValid():
            self._color = c
            self._paint_color()

    def _paint_color(self) -> None:
        self.btn_color.setIcon(swatch(self._color.name(), 16))
        self.btn_color.setText(self._color.name())

    def open_library(self) -> None:
        """«Materiales personalizados…»: el gestor; al volver, el campo ofrece los de ahora."""
        from .material_library_dialog import LibraryDialog

        dlg = LibraryDialog(self, self.library, current=self.composition(), current_name=self.description(),
                            current_color=self.color())
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()
        self._fill_entries()
        self._update()

    def _update(self) -> None:
        name = self.fname()
        problem = mat_filename_problem(name)
        if problem is None and name.lower() in {t.lower() for t in self.taken}:
            problem = f"{name} ya es el fichero de otro material."
        own_error = self.comp_editor.refresh() if self.mode() == OWN else None
        item = self.list_item()
        # De dónde sale la composición.
        if self.mode() == LIST:
            if item is not None:
                builtin = (" La lista es la que trae ARGUS (no se ha encontrado la de su penhan): para generarlo hará "
                           "falta la carpeta de penhan (Opciones → Programas de penhan…)."
                           if getattr(self.mlist, "builtin", False) else "")
                self.list_state.setText(_span(html.escape(
                    f"✔ n.º {item[0]} de la lista de PENELOPE: {item[1]}. penhmat lo genera con su composición, su "
                    f"densidad y su energía media de excitación (opción 2, con ese número).{builtin}"), OK_COLOR))
            elif not self.mlist:
                self.list_state.setText(_span(html.escape(
                    "No se encuentra la lista de materiales de PENELOPE (database/material-list.txt; Opciones → "
                    "Programas de penhan…). Sin ella, ARGUS no sabe el número de este material."), WARN_COLOR))
            elif self.description():
                self.list_state.setText(_span(html.escape(
                    "No está en la lista de PENELOPE con ese nombre: elíjalo en la lista (▾, o al escribir) o use "
                    "«Composición propia». Si no, ARGUS no sabrá generarlo."), WARN_COLOR))
            else:
                self.list_state.setText(html.escape("Escriba para buscar en la lista de PENELOPE (en español o en "
                                                    "inglés: «agua», «hueso», «PMMA», «plomo»…) o pulse ▾ para ver "
                                                    "los 280."))
        else:
            self.list_state.setText("")
        lib_problem = None
        if self.mode() == OWN and self.save_lib.isChecked() and not self.description():
            lib_problem = "escriba el nombre del material para guardarlo en sus materiales personalizados"
        self._problem = problem or (f"composición: {own_error}" if own_error else None) or lib_problem
        # El fichero.
        exists = self.folder is not None and not problem and (self.folder / name).is_file()
        other_needed = False
        if problem is not None:
            msg = _span(f"✖ {html.escape(problem)}", BAD_COLOR)
        elif exists:
            msg = _span(f"✔ {html.escape(name)} está en la carpeta del .in.", OK_COLOR)
            if self._can_compare():
                why = self._mismatch(self.folder / name)
                if why:
                    other_needed = True
                    msg = _span(html.escape(f"⚠ {name} ya está en la carpeta y {why}. Póngale otro nombre para "
                                            "generar el nuevo (ARGUS no sustituye ficheros)."), WARN_COLOR)
            elif self.edit and self.material_changed():
                other_needed = True
                msg = _span(html.escape(f"⚠ {name} ya está en la carpeta, con el material de antes: penhmain usaría "
                                        "ese. Póngale otro nombre para generar el nuevo (ARGUS no sustituye "
                                        "ficheros)."), WARN_COLOR)
            elif not (self.edit and name.lower() == (self._orig[0] or "").strip().lower()):
                # Aún no se sabe qué material es: se dice qué es el que está (penhmain usaría ese).
                other_needed = True
                msg = _span(html.escape(f"⚠ {name} ya está en la carpeta ({self._file_what(self.folder / name)}) y "
                                        "ARGUS aún no sabe si es este material: penhmain usaría ese. Elija el material "
                                        "en la lista o complete su composición, o póngale otro nombre."), WARN_COLOR)
        else:
            msg = _span(f"{html.escape(name)} no está en la carpeta del .in: se genera con «Generar .mat y ENDF…» "
                        "(o al ejecutar), con su penhmat.", WARN_COLOR)
        if problem is None and not name.lower().endswith(".mat"):
            msg += " (Los ficheros de material suelen acabar en .mat.)"
        if own_error and problem is None:
            msg = _span("✖ Falta completar la composición propia.", BAD_COLOR) + "<br>" + msg
        elif lib_problem and problem is None:
            msg = _span(f"✖ {html.escape(lib_problem[:1].upper() + lib_problem[1:])}.", BAD_COLOR) + "<br>" + msg
        self.status.setText(msg)
        self.btn_newfile.setVisible(other_needed)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self._problem is None)
        QTimer.singleShot(0, self._grow)

    def _grow(self) -> None:
        """Crece si lo que enseña ya no cabe (la tabla de elementos, textos de dos líneas…); no encoge mientras se
        escribe."""
        lay = self.layout()
        if lay is None:
            return
        lay.activate()
        need = lay.heightForWidth(self.width()) if lay.hasHeightForWidth() else lay.sizeHint().height()
        need = max(need, lay.minimumSize().height())
        if need > self.height():
            screen = self.screen()
            top = screen.availableGeometry().height() if screen is not None else need
            self.resize(self.width(), min(need, int(top * 0.95)))

    def propose_new_file(self) -> None:
        """Otro nombre de fichero (el siguiente libre: «pmma2.mat»), para generar el material nuevo sin tocar el que
        hay."""
        stem = self.fname().rsplit(".", 1)[0] or f"material{self.number}"
        taken = set(self.taken)
        if self.folder is not None:
            try:
                taken |= {p.name for p in self.folder.iterdir()}
            except OSError:
                pass
        self.file.setText(suggest_mat_filename(stem, taken))
        self._auto_file = False

    # -- guardar en los materiales personalizados ---------------------------------------------------------------------
    def _save_to_library(self) -> bool:
        """Guarda la composición propia en los materiales personalizados (si se ha pedido). False si el usuario
        cancela (el diálogo sigue abierto)."""
        comp = self.composition()
        if self.mode() != OWN or not self.save_lib.isChecked() or comp is None:
            return True
        name = self.description()
        have = self.library.find(name)
        replace = None
        if have is not None and have.composition == comp:
            if have.color != self.color():
                replace = have.name  # la misma composición: se guarda el color de ahora
                name = have.name
            else:
                self.saved_to_library = have.name
                return True
        elif have is not None:
            other = self.library.free_name(name)
            answer = self.ask_library_conflict(have, other)
            if answer == "replace":
                replace = have.name
            elif answer == "other":
                name = other
            else:
                return False
        try:
            m = self.library.put(LibraryMaterial(name, comp, self.color()), replace=replace)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Materiales personalizados", f"No se ha podido guardar en sus materiales "
                                f"personalizados: {exc}")
            return True  # el material del .in se añade igual
        self.saved_to_library = m.name
        return True

    def ask_library_conflict(self, have: LibraryMaterial, other: str) -> str | None:
        """Ya hay un material personalizado con ese nombre y otra composición: «replace» (sustituirlo), «other»
        (guardar este con el nombre ``other``) o None (cancelar: el diálogo sigue abierto)."""
        box = QMessageBox(QMessageBox.Icon.Question, "Materiales personalizados",
                          f"Ya tiene un material personalizado «{have.name}» con otra composición ({have.summary()}).",
                          parent=self)
        b_replace = box.addButton("Sustituirlo", QMessageBox.ButtonRole.AcceptRole)
        b_other = box.addButton(f"Guardar este como «{other}»", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_other)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        return "replace" if clicked is b_replace else "other" if clicked is b_other else None

    def accept(self) -> None:
        self._update()
        if self._problem is None and self._save_to_library():
            super().accept()

    def choice(self) -> MaterialChoice:
        """Lo elegido (después de «Añadir» o «Guardar»)."""
        return MaterialChoice(self.fname(), self.description(), self.color(), self.composition(),
                              self.saved_to_library)


AddMaterialDialog = MaterialDialog  # el nombre de antes (0.10–0.13)


def describe_origin(row, mlist: list[tuple[int, str]]) -> str:
    """De dónde sale la composición de un material (para las ayudas del panel Materiales)."""
    if getattr(row, "recipe", None) is not None:
        return f"Composición propia (penhmat, opción 1): {row.recipe.summary()}."
    if getattr(row, "recipe_error", None):
        return f"Su composición en el .in no se entiende: {row.recipe_error}."
    if getattr(row, "tissue", None) is not None:
        return f"Tejido del TAC «{row.tissue.name}»: composición de la importación."
    item = find_list_item(row.description, mlist) if row.description else None
    if item is not None:
        return f"Lista de PENELOPE: n.º {item[0]} ({item[1]}; penhmat, opción 2)."
    k = known_number(row.description, mlist)
    if k is not None:  # sin la lista de PENELOPE: el número está en el nombre
        return f"Lista de PENELOPE: n.º {k} (penhmat, opción 2)."
    if (k := listed_number(row.description)) is not None:
        return (f"El n.º {k} no está en su lista de PENELOPE (database/material-list.txt): ARGUS no sabe su "
                "composición. Asígnele un material de la lista o de sus materiales personalizados («Asignar "
                "materiales…» o menú contextual → «Composición…»).")
    return ("ARGUS no sabe su composición: asígnele un material de la lista de PENELOPE o de sus materiales "
            "personalizados («Asignar materiales…» o menú contextual → «Composición…»).")


__all__ = ["AddMaterialDialog", "MaterialChoice", "MaterialDialog", "describe_origin", "find_list_item",
           "library_entries", "recipe_text"]
