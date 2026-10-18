"""Panel «Materiales»: número, fichero .mat, nombre, color y estado de cada material.

El nombre del fichero es el de la línea ``MFNAME`` correspondiente del .in y
el nombre descriptivo se guarda como comentario de esa línea (penhmain no lo
lee). Los colores son solo de la vista y se recuerdan por geometría.

De dónde sale la composición de cada material (lo que usa «Generar .mat y
ENDF…»): la lista de PENELOPE (el nombre elegido de la lista lleva su número,
«… (PENELOPE 278)»), una composición propia (menú contextual →
«Composición…») o un TAC importado. La ayuda emergente del nombre lo dice.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import plural
from ..penhan.material_names import penelope_entries
from ..penhan.materials import (
    builtin_material_list,
    find_material_list,
    mat_filename_problem,
    material_list_or_builtin,
    material_recipe,
    materials_table,
    set_material,
    set_recipe,
    unidentified,
)
from ..render.colors import VOID_COLOR, material_color
from ..render.scene3d import HIDDEN, OPAQUE, TRANSLUCENT, VISIBILITY_NAMES
from .deferred import DeferredEdits
from .flow_layout import FlowLayout, keep_min_height_for_width
from .panels import body_title, swatch

COL_N, COL_FILE, COL_NAME, COL_VIS = range(4)
VIS_CYCLE = {OPAQUE: TRANSLUCENT, TRANSLUCENT: HIDDEN, HIDDEN: OPAQUE}
VIS_GLYPH = {OPAQUE: "●", TRANSLUCENT: "◐", HIDDEN: "○"}
ROLE_NUMBER = Qt.ItemDataRole.UserRole + 3
ADD_ROW = -1  # ROLE_NUMBER de la fila «＋ Añadir un material…»
STATE_COLORS = {"existe": "#1a7f37", "falta": "#b42318", "sin línea MFNAME": "#b42318",
                "con problemas": "#b42318"}


class _NameDelegate(QStyledItemDelegate):
    """Editor del nombre: el campo que busca en la lista de PENELOPE (en español o en inglés) y en los materiales
    personalizados al escribir, con ▾ para verlos todos (0.16.0)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []  # ListEntry de la lista de PENELOPE y de los materiales personalizados

    def createEditor(self, parent, option, index):
        from .material_picker import MaterialPicker

        ed = MaterialPicker(parent)
        ed.set_entries(self.entries)
        ed.setAutoFillBackground(True)
        ed.setFocusProxy(ed.edit)
        ed.picked.connect(lambda _e, ed=ed: self.commitData.emit(ed))
        # El foco lo tiene su campo de texto (no el editor): al salir de él (o con Intro) se confirma, como con un
        # QLineEdit (Guardar lo hace quitándole el foco).
        ed.edit.editingFinished.connect(lambda ed=ed: self._finish(ed))
        return ed

    def _finish(self, ed) -> None:
        if ed.completer.popup().isVisible():
            return
        self.commitData.emit(ed)
        self.closeEditor.emit(ed, QStyledItemDelegate.EndEditHint.NoHint)

    def setEditorData(self, editor, index) -> None:
        if hasattr(editor, "edit"):
            editor.setText(index.data(Qt.ItemDataRole.EditRole) or "")
            editor.edit.selectAll()
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index) -> None:
        if hasattr(editor, "edit"):
            model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)


class MaterialsPanel(QWidget):
    colorsChanged = Signal(object)  # {número de material: "#rrggbb"}
    visibilityChanged = Signal(object)  # {número de material: OPAQUE, TRANSLUCENT o HIDDEN} (vista 3D)
    newInputRequested = Signal()
    writeTxtRequested = Signal()
    addMaterialRequested = Signal()
    removeMaterialRequested = Signal(int)  # quitar el último material declarado
    completeRequested = Signal()  # generar con penhmat los .mat que faltan y copiar los ENDF (ADR-013)
    compositionRequested = Signal(int)  # «Composición del material N…»: de la lista de PENELOPE o propia
    assignRequested = Signal()  # «Asignar materiales…»: los que ARGUS no ha identificado (0.16.0)
    libraryRequested = Signal()  # «Materiales personalizados…» (0.16.0)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctrl = None
        self.session = None
        self.colors: dict[int, str] = {}
        self.default_colors: dict[int, str] = {}  # los que no son de la paleta (trozos de un tejido partido)
        self.visibility: dict[int, int] = {}
        self._updating = False
        self._deferred = DeferredEdits()
        # La lista de PENELOPE para el desplegable: la de la base de datos o, si no se encuentra, la que trae ARGUS.
        self._mlist: list[tuple[int, str]] = builtin_material_list()
        self._mlist_path = None
        self.unknown: list = []  # materiales que ARGUS no sabe de qué están hechos (para «Asignar materiales…»)
        self.library = None  # los materiales personalizados del usuario (argus.penhan.library.Library)
        self.database_fn = None  # () -> carpeta database de penhan del usuario (Opciones → Programas de penhan)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["N.º", "Fichero .mat", "Material", "3D"])
        self.table.horizontalHeaderItem(COL_VIS).setToolTip(
            "Cómo se ve el material en la vista 3D: opaco, translúcido u oculto (clic para cambiar)")
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.SelectedClicked)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_N, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_VIS, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(COL_FILE, 112)
        self.delegate = _NameDelegate(self.table)
        self.table.setItemDelegateForColumn(COL_NAME, self.delegate)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.cellClicked.connect(self._on_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_menu)
        lay.addWidget(self.table, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setStyleSheet("color: #555;")
        self.info.linkActivated.connect(self._on_link)
        lay.addWidget(self.info)
        row = FlowLayout()  # los botones siguen en otra línea si no caben (pantallas al 125 o 150 %)
        self.btn_txt = QPushButton("Escribir materiales.txt")
        self.btn_txt.setToolTip("Lista de ficheros .mat que necesita el .in y cuáles faltan (se escribe también "
                                "al guardar el .in).")
        self.btn_txt.clicked.connect(self.writeTxtRequested)
        self.btn_new = QPushButton("Crear .in")
        self.btn_new.clicked.connect(self.newInputRequested)
        self.btn_colors = QPushButton("Colores por defecto")
        self.btn_colors.clicked.connect(self._reset_colors)
        self.btn_add = QPushButton("＋ Material")
        self.btn_add.setToolTip("Añadir un material (una línea MFNAME más en el .in), sin tener que crear antes "
                                "un cuerpo")
        self.btn_add.clicked.connect(self.addMaterialRequested)
        self.btn_gen = QPushButton("Generar .mat y ENDF…")
        self.btn_gen.setToolTip("Genera con su penhmat los .mat que faltan (tejidos de un TAC y materiales de la lista "
                                "de PENELOPE) y copia a la carpeta del .in los ficheros ENDF que necesitan; no "
                                "sustituye ningún fichero")
        self.btn_gen.clicked.connect(self.completeRequested)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_gen)
        row.addWidget(self.btn_txt)
        row.addWidget(self.btn_new)
        row.addStretch(1)
        row.addWidget(self.btn_colors)
        lay.addLayout(row)
        keep_min_height_for_width(self)
        self.set_context(None, None, {})

    # -- contexto ---------------------------------------------------------------------
    def set_context(self, ctrl, session, colors: dict[int, str] | None = None, keep_visibility: bool = False,
                    defaults: dict[int, str] | None = None) -> None:
        if session is not self.session and not keep_visibility:
            self.visibility = ct_air_hidden(session)
        self.ctrl = ctrl
        self.session = session
        if colors is not None:
            self.colors = dict(colors)
        if defaults is not None:
            self.default_colors = dict(defaults)
        self._load_material_list()
        self.refresh()

    @property
    def geo(self):
        s = self.session
        return s.geo if s is not None and s.ok else None

    def _folder(self):
        if self.ctrl is not None and self.ctrl.folder is not None:
            return self.ctrl.folder
        return self.session.path.parent if self.session is not None else None

    def _load_material_list(self) -> None:
        path = None
        db = self.database_fn() if self.database_fn is not None else None
        if db is not None and (Path(db) / "material-list.txt").is_file():
            path = Path(db) / "material-list.txt"
        path = path or find_material_list(self._folder())
        if path != self._mlist_path:
            self._mlist_path = path
            self._mlist = material_list_or_builtin(path)
        self.reload_library()

    def reload_library(self) -> None:
        """Lo que ofrece el nombre al editarlo: los materiales personalizados (★) y los de la lista de PENELOPE."""
        from ..penhan.library import Library
        from .add_material_dialog import library_entries

        self.library = Library()
        self.delegate.entries = library_entries(self.library) + penelope_entries(self._mlist)

    # -- tabla ------------------------------------------------------------------------------
    def refresh(self) -> None:
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _refresh(self) -> None:
        geo = self.geo
        doc = self.ctrl.doc if self.ctrl is not None else None
        rows = materials_table(doc, geo, self._folder(),
                               self.database_fn() if self.database_fn is not None else None)
        editable = self.ctrl is not None
        void_bodies = []
        if geo is not None:
            void_bodies = [b.index for b in geo.bodies if b.material == 0 and b.origin != "enclosure"
                           and not b.is_module]
        self.table.setRowCount(0)
        if void_bodies:
            self._add_row(0, "—", "Vacío (MATERIAL 0)", "—", VOID_COLOR, False,
                          f"Cuerpos sin material: {self._bodies(void_bodies)}")
        for r in rows:
            color = self.colors.get(r.number) or material_color(r.number)
            tip = []
            if r.bodies:
                tip.append(f"Cuerpos con este material: {self._bodies(r.bodies)}")
            elif geo is not None:
                tip.append("Ningún cuerpo de la geometría usa este material.")
            if r.header is not None:
                dens = f", {r.header.density:.4g} g/cm³" if r.header.density else ""
                tip.append(f"{r.fname}: {r.header.name}{dens} ({r.header.version})")
            if r.nuclear is not None and r.nuclear.nisot is not None:
                tip.append(f"Isótopos con datos ENDF: {r.nuclear.nisot}" if r.nuclear.uses_endf else
                           "Reacciones nucleares absorbentes, sin datos ENDF (NISOT < 0)" if r.nuclear.nisot < 0
                           else "Sin reacciones nucleares (NISOT = 0)")
            if r.declared:
                from .add_material_dialog import describe_origin

                tip.append(describe_origin(r, self._mlist))
            for prob in r.problems:
                tip.append(("✖ " if prob.error else "⚠ ") + prob.message
                           + (f" [IRETRN {prob.code}]" if prob.code else ""))
            state = r.status
            if r.declared and not r.used and geo is not None:
                state += " · sin uso"
            placeholder = r.display_name if not r.description else ""
            note = "leído del fichero .mat"
            if not r.description and r.recipe is not None:
                placeholder, note = r.recipe.formula(), "su composición propia"
            self._add_row(r.number, r.fname or "—", r.description, state, color, editable, "\n".join(tip),
                          placeholder=placeholder, placeholder_note=note)
        if geo is not None or self.ctrl is not None:
            self._add_new_row()
        # Resumen.
        self.unknown = unidentified(rows, self._mlist) if self.ctrl is not None else []
        missing = [r for r in rows if r.declared and not r.exists]
        undeclared = [r for r in rows if not r.declared]
        broken = [r for r in rows if r.problems]
        parts = []
        if self.ctrl is None:
            parts.append("Para dar nombre a los materiales hace falta un .in: el nombre del fichero .mat es el de "
                         "su línea MFNAME.")
        else:
            if missing:
                parts.append(f"<span style='color:#b42318'>{'Falta' if len(missing) == 1 else 'Faltan'} "
                             f"{plural(len(missing), 'fichero')} .mat en la carpeta</span>"
                             " (se anotan en materiales.txt al guardar).")
            elif rows and not broken:
                parts.append("<span style='color:#1a7f37'>Están todos los ficheros .mat.</span>")
            if broken:
                names = ", ".join(r.fname for r in broken)
                parts.append(f"<span style='color:#b42318'>Con problemas: {names}</span> (detalles en la ayuda "
                             "emergente y en el panel de mensajes).")
            if self.unknown:
                nums = ", ".join(f"{r.number} ({r.fname})" for r in self.unknown[:4]) + (
                    " …" if len(self.unknown) > 4 else "")
                what = "está hecho el material" if len(self.unknown) == 1 else "están hechos los materiales"
                parts.append(f"<span style='color:#b42318'>ARGUS no sabe de qué {what} {html.escape(nums)}: no "
                             "puede generar su .mat.</span> <a href='asignar'>Asignar materiales…</a>")
            if undeclared:
                nums = ", ".join(str(r.number) for r in undeclared)
                parts.append(f"<span style='color:#b42318'>La geometría usa el material {nums} y el .in no tiene "
                             "su línea MFNAME (IRETRN 3054): escriba su fichero en la tabla.</span>")
            parts.append("Doble clic en el fichero o en el nombre para cambiarlos; en el color para elegir otro. "
                         "Composición (lista de PENELOPE o propia) y <a href='biblioteca'>materiales "
                         "personalizados</a>: menú contextual.")
        notice, self._notice = getattr(self, "_notice", None), None
        if notice:
            parts.insert(0, f"<span style='color:#b42318'>✖ {html.escape(notice)}</span>")
        self.info.setText("<br>".join(parts))
        self.btn_txt.setVisible(self.ctrl is not None)
        self.btn_gen.setVisible(self.ctrl is not None)
        wanted = bool(missing) or any(r.stale or r.missing_endf for r in rows)
        self.btn_gen.setEnabled(wanted)
        self.btn_gen.setStyleSheet("font-weight: bold;" if wanted else "")
        self.btn_new.setVisible(self.ctrl is None and geo is not None)
        self.btn_add.setVisible(geo is not None or self.ctrl is not None)
        self.btn_colors.setEnabled(self.colors != self.default_colors)

    def _bodies(self, kbs: list[int]) -> str:
        geo = self.geo
        if geo is None:
            return ", ".join(map(str, kbs))
        names = [body_title(geo, kb) for kb in kbs[:8]]
        return ", ".join(names) + (f" y {len(kbs) - 8} más" if len(kbs) > 8 else "")

    def _add_row(self, number: int, fname: str, name: str, state: str, color: str, editable: bool,
                 tip: str, placeholder: str = "", placeholder_note: str = "leído del fichero .mat") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        it_n = QTableWidgetItem(swatch(color, 14), str(number))
        it_n.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        it_n.setToolTip("Doble clic para cambiar el color en la vista" if number > 0 else tip)
        it_f = QTableWidgetItem(fname)
        it_name = QTableWidgetItem(name or placeholder)
        if not name and placeholder:
            it_name.setForeground(QBrush(QColor("#888")))
            it_name.setToolTip("\n".join(x for x in (f"{placeholder} ({placeholder_note})", tip) if x))
        # Estado: el nombre del fichero en rojo si falta, y el detalle en la ayuda emergente.
        key = state.split(" ·")[0]
        if number > 0 and key in STATE_COLORS:
            it_f.setForeground(QBrush(QColor(STATE_COLORS[key])))
            if key != "existe":
                f = it_f.font()
                f.setBold(True)
                it_f.setFont(f)
        state_text = {"existe": "El fichero está en la carpeta.",
                      "falta": "Falta el fichero en la carpeta: hay que generarlo con penhmat.",
                      "sin línea MFNAME": "El .in no tiene línea MFNAME para este material (IRETRN 3054).",
                      "con problemas": "El fichero está en la carpeta, pero penhmain no podría usarlo:"}
        tip_f = state_text.get(key, "")
        if "sin uso" in state:
            tip_f += " Ningún cuerpo lo usa."
        for it in (it_n, it_f, it_name):
            it.setData(ROLE_NUMBER, number)
        it_f.setToolTip("\n".join(x for x in (tip_f.strip(), tip) if x))
        if not it_name.toolTip():
            it_name.setToolTip("\n".join(x for x in (name or placeholder, tip) if x))
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for it in (it_f, it_name):
            it.setFlags(flags | (Qt.ItemFlag.ItemIsEditable if editable and number > 0 else Qt.ItemFlag.NoItemFlags))
        self.table.setItem(row, COL_N, it_n)
        self.table.setItem(row, COL_FILE, it_f)
        self.table.setItem(row, COL_NAME, it_name)
        it_v = QTableWidgetItem()
        it_v.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        it_v.setData(ROLE_NUMBER, number)
        if number > 0:
            v = self.visibility.get(number, OPAQUE)
            it_v.setText(f"{VIS_GLYPH[v]} {VISIBILITY_NAMES[v]}")
            it_v.setToolTip("Vista 3D: clic para pasar a " + VISIBILITY_NAMES[VIS_CYCLE[v]])
            if v != OPAQUE:
                it_v.setForeground(QBrush(QColor("#8a5a00")))
        else:
            it_v.setText("—")
            it_v.setToolTip("Los cuerpos sin material no se dibujan en 3D.")
        self.table.setItem(row, COL_VIS, it_v)

    def _add_new_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        it = QTableWidgetItem("＋ Añadir un material…")
        it.setFlags(Qt.ItemFlag.ItemIsEnabled)
        it.setData(ROLE_NUMBER, ADD_ROW)
        it.setForeground(QBrush(QColor("#2f6db3")))
        f = it.font()
        f.setItalic(True)
        it.setFont(f)
        it.setToolTip("Clic para declarar un material nuevo en el .in (no hace falta crear antes un cuerpo)")
        self.table.setItem(row, 0, it)
        self.table.setSpan(row, 0, 1, self.table.columnCount())

    def _on_link(self, href: str) -> None:
        if href == "asignar":
            self.assignRequested.emit()
        elif href == "biblioteca":
            self.libraryRequested.emit()

    def declared_count(self) -> int:
        return len(self.ctrl.doc.find("MFNAME")) if self.ctrl is not None else 0

    def _on_menu(self, pos) -> None:
        menu = self.build_menu(pos)
        try:
            menu.exec(self.table.viewport().mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def build_menu(self, pos) -> QMenu:
        """El menú contextual de la tabla en ``pos`` (coordenadas de su área visible)."""
        item = self.table.itemAt(pos)
        n = item.data(ROLE_NUMBER) if item is not None else None
        menu = QMenu(self)
        a = menu.addAction("Añadir un material…")
        a.triggered.connect(self.addMaterialRequested)
        geo = self.geo
        last = self.declared_count()
        if n and 0 < n <= last and self.ctrl is not None:
            a = menu.addAction(f"Composición del material {n}…")
            a.setToolTip("De la lista de PENELOPE o propia (fórmula o elementos, densidad y energía media de "
                         "excitación)")
            a.triggered.connect(lambda: self.compositionRequested.emit(n))
        if self.unknown and self.ctrl is not None:
            a = menu.addAction("Asignar materiales…")
            a.setToolTip("Elegir de la lista de PENELOPE o de sus materiales personalizados los que ARGUS no sabe de "
                         "qué están hechos (para generar su .mat)")
            a.triggered.connect(self.assignRequested)
        a = menu.addAction("Materiales personalizados…")
        a.setToolTip("Sus materiales de composición propia, para usarlos en cualquier caso")
        a.triggered.connect(self.libraryRequested)
        if n and n > 0 and self.ctrl is not None:
            if n == last and last > 1 and (geo is None or n not in geo.used_materials()):
                a = menu.addAction(f"Quitar el material {n}")
                a.setToolTip("Quita su línea MFNAME (y sus parámetros) del .in")
                a.triggered.connect(lambda: self.removeMaterialRequested.emit(n))
            else:
                a = menu.addAction(f"Quitar el material {n}")
                a.setEnabled(False)
                why = ("lo usa algún cuerpo o el CT" if geo is not None and n in geo.used_materials() else
                       "solo se puede quitar el último (los demás cambiarían de número)" if n != last else
                       "penhmain necesita al menos un material")
                a.setToolTip(f"No se puede: {why}.")
                a.setText(f"Quitar el material {n} (no se puede: {why})")
        return menu

    # -- ediciones ----------------------------------------------------------------------------
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or self.ctrl is None:
            return
        n = item.data(ROLE_NUMBER)
        if not n:
            return
        text = item.text().strip()
        if item.column() == COL_FILE:
            if text in ("", "—"):
                QTimer.singleShot(0, self.refresh)
                return
            problem = mat_filename_problem(text)
            if problem:
                # Se muestra en el próximo refresco (que vuelve a poner el nombre de antes en la tabla): si
                # se escribía aquí directamente, ese refresco lo borraba sin que se llegase a ver.
                self._notice = f"No se ha cambiado el fichero del material {n}: {problem}"
                QTimer.singleShot(0, self.refresh)
                return
            self._defer_edit(f"Fichero del material {n}", lambda d: set_material(d, n, fname=text))
        elif item.column() == COL_NAME:
            listed = re.search(r"\(PENELOPE\s+\d+\)", text) is not None
            mine = self.library.find(text) if self.library is not None and not listed else None
            if mine is not None and mine.problem is None and text == mine.name:
                # Uno de sus materiales personalizados: con su composición (un material, un origen).
                def fn(d, n=n, m=mine):
                    set_material(d, n, description=m.name)
                    set_recipe(d, n, m.composition)

                self._defer_edit(f"Material {n}: {mine.name}", fn)
                if mine.color:
                    self.colors[n] = mine.color
                    self.colorsChanged.emit(dict(self.colors))
                return
            if listed and material_recipe(self.ctrl.doc, n) is not None:
                # Elegido de la lista de PENELOPE: deja de tener la composición propia (un material, un origen).
                def fn(d, n=n, text=text):
                    set_material(d, n, description=text)
                    set_recipe(d, n, None)

                self._defer_edit(f"Material {n} de la lista de PENELOPE", fn)
            else:
                self._defer_edit(f"Nombre del material {n}", lambda d: set_material(d, n, description=text))

    def _defer_edit(self, text: str, fn) -> None:
        ctrl = self.ctrl

        def run():
            if self.ctrl is ctrl and ctrl is not None:
                try:
                    ctrl.edit(text, fn)
                except ValueError as exc:  # no cabe en las columnas que lee penhmain: el .in no cambia
                    self._notice = str(exc)
                    self.refresh()

        self._deferred.defer(run)  # fuera de la señal de la tabla (la edición la rehace)

    def flush_pending(self) -> None:
        """Aplica ya las ediciones pendientes (antes de guardar, cerrar, abrir o deshacer)."""
        self._deferred.flush()

    def set_visibility(self, visibility: dict[int, int]) -> None:
        """Estado de cada material en 3D (desde la vista 3D, p. ej. su menú contextual)."""
        vis = {int(k): int(v) for k, v in visibility.items() if int(v) != OPAQUE}
        if vis != self.visibility:
            self.visibility = vis
            self.refresh()

    def _on_click(self, row: int, col: int) -> None:
        first = self.table.item(row, 0)
        if first is not None and first.data(ROLE_NUMBER) == ADD_ROW:
            self.addMaterialRequested.emit()
            return
        if col != COL_VIS:
            return
        n = self.table.item(row, COL_VIS).data(ROLE_NUMBER)
        if not n:
            return
        new = VIS_CYCLE[self.visibility.get(n, OPAQUE)]
        if new == OPAQUE:
            self.visibility.pop(n, None)
        else:
            self.visibility[n] = new
        self.refresh()
        self.visibilityChanged.emit(dict(self.visibility))

    def _on_double_click(self, row: int, col: int) -> None:
        if col != COL_N:
            return
        n = self.table.item(row, COL_N).data(ROLE_NUMBER)
        if not n or n < 0:
            return
        current = QColor(self.colors.get(n) or material_color(n))
        c = QColorDialog.getColor(current, self, f"Color del material {n}")
        if c.isValid():
            self.colors[n] = c.name()
            self.refresh()
            self.colorsChanged.emit(dict(self.colors))

    def _reset_colors(self) -> None:
        self.colors = dict(self.default_colors)
        self.refresh()
        self.colorsChanged.emit(dict(self.colors))


def ct_air_hidden(session) -> dict[int, int]:
    """Visibilidad inicial en 3D: el aire de un CT (materiales solo de vóxeles, casi sin densidad) oculto.

    Si no, la vista 3D mostraría la caja del CT llena de aire en vez del paciente.
    """
    geo = getattr(session, "geo", None) if session is not None else None
    ct = getattr(geo, "ct", None) if geo is not None and getattr(geo, "ok", False) else None
    if ct is None:
        return {}
    from ..penhan.materials import ct_tissues
    from ..render.scene3d import HIDDEN

    used = {b.material for b in geo.bodies if b.origin != "ct"}
    tissues = ct_tissues(geo)  # de la ficha del .ct (si la importó ARGUS)
    out: dict[int, int] = {}
    for m in ct.materials():
        if m in used:
            continue
        t = tissues.get(m)
        if t is not None:  # el primer tejido (desde −∞ HU) y casi sin densidad: el aire de la calibración
            if t.hu_min == float("-inf") and t.density < 0.01:
                out[m] = HIDDEN
            continue
        # Sin ficha: por la densidad media. El aire real tiene ruido (−980 HU ± 30, hasta −950 en la curva de
        # Schneider): su media anda por 0,01-0,02 g/cm³; la del pulmón, por encima de 0,2.
        sel = ct.density[ct.material == m]
        if sel.size and float(sel.mean()) < 0.06:
            out[m] = HIDDEN
    return out
