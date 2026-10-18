"""Panel «Editar»: el cuerpo, módulo o clon seleccionado (etiqueta, material, colocación y superficies).

Los cambios pasan por el controlador de la geometría (se pueden deshacer y
se comprueba que PENGEOM aceptaría el resultado; los que cambian la forma de
un cuerpo, que no crean solapes). Solo se editan los elementos del fichero
principal; los de ficheros INCLUDE y la envolvente automática se muestran
como no editables. De un clon se edita su colocación (el resto es el del
módulo original).
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..pengeom import edit as ge
from ..pengeom import placement as pl
from ..pengeom import surfaces as sf
from ..pengeom.compiler import body_title
from ..pengeom.model import COEF_NAMES, CloneElement, ModuleElement, SurfaceElement
from ..pengeom.overlap import guard
from .source_panel import NumberEdit

PARAM_LABELS = {
    "X-SCALE": "Escala x", "Y-SCALE": "Escala y", "Z-SCALE": "Escala z",
    "OMEGA": "ω (°)", "THETA": "θ (°)", "PHI": "φ (°)",
    "X-SHIFT": "Desplaz. x", "Y-SHIFT": "Desplaz. y", "Z-SHIFT": "Desplaz. z",
}
TRANSFORM = ("OMEGA", "THETA", "PHI", "X-SHIFT", "Y-SHIFT", "Z-SHIFT")
COEF_LABELS = {"AXX": "x²", "AXY": "xy", "AXZ": "xz", "AYY": "y²", "AYZ": "yz", "AZZ": "z²", "AX": "x", "AY": "y",
               "AZ": "z", "A0": "1"}


def _parinp_tip(ipar: int | None) -> str:
    if not ipar:
        return ""
    return f"Lo fija PARINP({ipar}) del .in: cámbielo allí (sin .in se usa el número escrito en el .geo)."


def with_(fn, *, check=None, renames=None):
    """Adjunta a una edición su comprobación (solapes) y las etiquetas que cambia (para el .in)."""
    if check is not None:
        fn.check = check
    if renames is not None:
        fn.renames = renames
    return fn


class GeometryEditPanel(QScrollArea):
    """Formulario de edición del elemento seleccionado."""

    editRequested = Signal(str, object)  # (texto para deshacer, fn(doc, geo))
    addRequested = Signal(int)  # contenedor
    deleteRequested = Signal(int)
    glueRequested = Signal(int)  # pegar este módulo a otro elemento
    convertRequested = Signal(int)  # convertir este cuerpo en módulo
    duplicateRequested = Signal(int)
    wrapRequested = Signal(int)
    rotateRequested = Signal(int, object, float)  # (elemento, eje, grados) alrededor de su centro
    surfaceAddRequested = Signal(int)
    materializeRequested = Signal(int)
    selectRequested = Signal(int)
    saveRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ctrl = None
        self.kb: int | None = None
        self._surface_label: str | None = None
        self._updating = False
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        self.reason.setStyleSheet("color: #8a5a00;")
        lay.addWidget(self.reason)
        # Cuerpo, módulo o clon.
        self.box_body = QGroupBox("Cuerpo")
        f = QFormLayout(self.box_body)
        self.label_edit = QLineEdit()
        self.label_edit.setMaxLength(4)
        self.label_edit.setMaximumWidth(80)
        self.label_edit.setToolTip("Etiqueta de PENGEOM (hasta 4 caracteres). Al cambiarla se cambian también las "
                                   "referencias del .geo y las del .in")
        self.label_edit.editingFinished.connect(self._on_label)
        f.addRow("Etiqueta:", self.label_edit)
        self.material = QSpinBox()
        self.material.setRange(0, 9998)  # 9999 es la marca de la caja CT
        self.material.setToolTip("Número de material (0 = vacío); penhmain admite hasta 10 (MAXMAT)")
        self.material.editingFinished.connect(self._on_material)
        f.addRow("Material:", self.material)
        self.description = QLineEdit()
        self.description.setToolTip("Comentario tras la etiqueta en el .geo (PENGEOM no lo lee)")
        self.description.editingFinished.connect(self._on_description)
        f.addRow("Nombre:", self.description)
        self.original = QPushButton()
        self.original.setToolTip("Seleccionar el módulo que copia este clon")
        self.original.clicked.connect(self._on_original)
        f.addRow("Original:", self.original)
        lay.addWidget(self.box_body)
        # Colocación del módulo o del clon.
        self.box_transform = QGroupBox("Colocación (mueve también todo lo que tiene dentro)")
        v = QVBoxLayout(self.box_transform)
        grid = QGridLayout()
        self.transform_edits: dict[str, NumberEdit] = {}
        for i, name in enumerate(TRANSFORM):
            ed = NumberEdit(width=96)
            ed.valueEdited.connect(lambda val, n=name: self._on_transform(n, val))
            self.transform_edits[name] = ed
            grid.addWidget(QLabel(PARAM_LABELS[name] + ":"), i % 3, 2 * (i // 3))
            grid.addWidget(ed, i % 3, 2 * (i // 3) + 1)
        v.addLayout(grid)
        rot = QHBoxLayout()
        rot.addWidget(QLabel("Girar alrededor de su centro:"))
        self.rot_axis = QComboBox()
        for a in "xyz":
            self.rot_axis.addItem(f"eje {a}", a)
        self.rot_axis.setCurrentIndex(2)
        self.rot_angle = NumberEdit(width=60)
        self.rot_angle.set_value(90.0)
        self.rot_angle.setToolTip("Grados (positivo: sentido antihorario mirando desde la punta del eje)")
        self.btn_rotate = QPushButton("Girar")
        self.btn_rotate.clicked.connect(self._on_rotate)
        rot.addWidget(self.rot_axis)
        rot.addWidget(self.rot_angle)
        rot.addWidget(QLabel("°"))
        rot.addWidget(self.btn_rotate)
        rot.addStretch(1)
        v.addLayout(rot)
        hint = QLabel("En el corte 2D: arrastre la cruz del centro para moverlo y la bola del anillo para girarlo "
                      "(Mayús: pasos de 15°; Ctrl: finos).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        v.addWidget(hint)
        lay.addWidget(self.box_transform)
        # Superficies propias.
        self.box_surfaces = QGroupBox("Superficies que lo limitan")
        v = QVBoxLayout(self.box_surfaces)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Etiqueta", "Tipo", "Lado", "Usada también por"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(170)
        self.table.itemSelectionChanged.connect(self._on_surface_selected)
        self.table.cellDoubleClicked.connect(lambda r, c: c == 2 and self._on_flip())
        v.addWidget(self.table)
        row = QHBoxLayout()
        self.btn_flip = QPushButton("Cambiar el lado")
        self.btn_flip.setToolTip("−1: el cuerpo está donde F < 0; +1: donde F > 0 (doble clic en la columna Lado)")
        self.btn_flip.clicked.connect(self._on_flip)
        self.btn_remove_surface = QPushButton("Quitar")
        self.btn_remove_surface.setToolTip("Deja de limitar este cuerpo (si no la usa nadie más, se borra)")
        self.btn_remove_surface.clicked.connect(self._on_remove_surface)
        self.btn_add_surface = QPushButton("Añadir…")
        self.btn_add_surface.setToolTip("Limitar el cuerpo con otra superficie: una que ya exista, un plano nuevo o "
                                        "una cuádrica en forma implícita")
        self.btn_add_surface.clicked.connect(lambda: self.kb and self.surfaceAddRequested.emit(self.kb))
        for b in (self.btn_flip, self.btn_remove_surface, self.btn_add_surface):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.surface_note = QLabel()
        self.surface_note.setWordWrap(True)
        self.surface_note.setStyleSheet("color: #555;")
        v.addWidget(self.surface_note)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Etiqueta de la superficie:"))
        self.surface_label = QLineEdit()
        self.surface_label.setMaxLength(4)
        self.surface_label.setMaximumWidth(80)
        self.surface_label.editingFinished.connect(self._on_surface_label)
        srow.addWidget(self.surface_label)
        srow.addStretch(1)
        self.surface_label_row = QWidget()
        srow.setContentsMargins(0, 0, 0, 0)
        self.surface_label_row.setLayout(srow)
        v.addWidget(self.surface_label_row)
        self.params_widget = QWidget()
        pf = QFormLayout(self.params_widget)
        pf.setContentsMargins(0, 0, 0, 0)
        self.param_edits: dict[str, NumberEdit] = {}
        for name in ge.REDUCED:
            ed = NumberEdit(width=110, minimum=1e-15 if name.endswith("SCALE") else None)
            ed.valueEdited.connect(lambda val, n=name: self._on_param(n, val))
            self.param_edits[name] = ed
            pf.addRow(PARAM_LABELS[name] + ":", ed)
        v.addWidget(self.params_widget)
        self.btn_implicit = QPushButton("Pasar a forma implícita")
        self.btn_implicit.setToolTip("Escribe la misma superficie con sus diez coeficientes (AXX…A0): PENGEOM la lee "
                                     "igual y se pueden editar uno a uno")
        self.btn_implicit.clicked.connect(self._on_to_implicit)
        v.addWidget(self.btn_implicit, 0, Qt.AlignmentFlag.AlignLeft)
        # Forma implícita: F = AXX x² + AXY xy + ... + A0, y giro/desplazamiento opcional.
        self.implicit_widget = QWidget()
        iv = QVBoxLayout(self.implicit_widget)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.addWidget(QLabel("F(x, y, z) = Σ coeficiente · término (en su sistema, antes del giro):"))
        grid = QGridLayout()
        self.coef_edits: dict[str, NumberEdit] = {}
        for i, name in enumerate(COEF_NAMES):
            ed = NumberEdit(width=96)
            ed.valueEdited.connect(lambda val, n=name: self._on_coef(n, val))
            self.coef_edits[name] = ed
            grid.addWidget(QLabel(f"{name} ({COEF_LABELS[name]}):"), i // 2, 2 * (i % 2))
            grid.addWidget(ed, i // 2, 2 * (i % 2) + 1)
        iv.addLayout(grid)
        iv.addWidget(QLabel("Giro y desplazamiento de la superficie:"))
        grid = QGridLayout()
        self.implicit_transform: dict[str, NumberEdit] = {}
        for i, name in enumerate(TRANSFORM):
            ed = NumberEdit(width=96)
            ed.valueEdited.connect(lambda val, n=name: self._on_implicit_transform(n, val))
            self.implicit_transform[name] = ed
            grid.addWidget(QLabel(PARAM_LABELS[name] + ":"), i % 3, 2 * (i // 3))
            grid.addWidget(ed, i % 3, 2 * (i // 3) + 1)
        iv.addLayout(grid)
        v.addWidget(self.implicit_widget)
        lay.addWidget(self.box_surfaces)
        # Acciones.
        grid = QGridLayout()
        self.btn_add = QPushButton("Añadir dentro…")
        self.btn_add.setToolTip("Un cuerpo (esfera, cilindro, caja o lámina) o un módulo dentro de este elemento")
        self.btn_add.clicked.connect(lambda: self.addRequested.emit(self.kb or 0))
        self.btn_delete = QPushButton("Eliminar…")
        self.btn_delete.clicked.connect(lambda: self.kb and self.deleteRequested.emit(self.kb))
        self.btn_duplicate = QPushButton("Duplicar…")
        self.btn_duplicate.setToolTip("Copias independientes o clones enlazados (CLONE), desplazadas o pegadas por "
                                      "una cara")
        self.btn_duplicate.clicked.connect(lambda: self.kb and self.duplicateRequested.emit(self.kb))
        self.btn_wrap = QPushButton("Envolver en un módulo…")
        self.btn_wrap.setToolTip("Un módulo nuevo alrededor de este elemento (y de otros que estén a su lado), para "
                                 "moverlos, girarlos o duplicarlos juntos")
        self.btn_wrap.clicked.connect(lambda: self.kb and self.wrapRequested.emit(self.kb))
        self.btn_glue = QPushButton("Pegar a otro…")
        self.btn_glue.setToolTip("Mueve este módulo, con todo lo que tiene dentro, hasta que toca una cara de otro "
                                 "cuerpo o módulo")
        self.btn_glue.clicked.connect(lambda: self.kb and self.glueRequested.emit(self.kb))
        self.btn_convert = QPushButton("Convertir en módulo")
        self.btn_convert.setToolTip("Mismo volumen y material; lo que tiene dentro pasa a ser hijas. Un módulo se "
                                    "puede mover, girar y pegar a otros.")
        self.btn_convert.clicked.connect(lambda: self.kb and self.convertRequested.emit(self.kb))
        self.btn_materialize = QPushButton("Convertir en copia independiente")
        self.btn_materialize.setToolTip("El clon pasa a ser una copia normal del módulo (se puede editar por separado "
                                        "y ya no cambia con el original)")
        self.btn_materialize.clicked.connect(lambda: self.kb and self.materializeRequested.emit(self.kb))
        self._action_grid = grid
        self._actions = (self.btn_add, self.btn_duplicate, self.btn_wrap, self.btn_glue, self.btn_convert,
                         self.btn_materialize, self.btn_delete)
        for i, b in enumerate(self._actions):
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        self.btn_save = QPushButton("Guardar la geometría (.geo)")
        self.btn_save.setToolTip("Escribe el .geo (la versión anterior queda en .geo.bak)")
        self.btn_save.clicked.connect(self.saveRequested)
        lay.addWidget(self.btn_save)
        self.help = QLabel("Seleccione un cuerpo o módulo (en el árbol, en el corte o en la vista 3D) para "
                           "editarlo. Con clic derecho en el corte o en 3D: añadir cuerpos o módulos, duplicar, "
                           "envolver en un módulo, girar…")
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: #666;")
        lay.addWidget(self.help)
        lay.addStretch(1)
        self.refresh()

    # -- contexto ----------------------------------------------------------------------
    def set_context(self, ctrl) -> None:
        if self.ctrl is not None and self.ctrl is not ctrl:
            try:
                self.ctrl.dirtyChanged.disconnect(self._on_dirty)
            except (RuntimeError, TypeError):  # ya destruido o no conectado
                pass
        if ctrl is not None and ctrl is not self.ctrl:
            # El botón «Guardar» sigue a la pila de deshacer: «changed» llega antes de que el cambio entre en ella.
            ctrl.dirtyChanged.connect(self._on_dirty)
        self.ctrl = ctrl
        self.kb = None
        self.refresh()

    def _on_dirty(self, dirty: bool) -> None:
        self.btn_save.setEnabled(self.ctrl is not None and bool(dirty))

    def show_body(self, kb: int | None) -> None:
        if kb != self.kb:
            self._surface_label = None
        self.kb = kb or None
        self.refresh()

    @property
    def geo(self):
        return self.ctrl.geo if self.ctrl is not None else None

    @property
    def parinp(self):
        return self.ctrl.session.parinp if self.ctrl is not None else None

    def refresh(self) -> None:
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _element(self):
        """(elemento editable, motivo si no lo es)."""
        geo = self.geo
        if geo is None or not geo.ok or not self.kb or self.kb > geo.nbodys:
            return None, ""
        try:
            return sf.renamable_body(geo, self.kb), ""
        except ge.EditError as exc:
            return None, str(exc)

    def _layout_actions(self) -> None:
        """Los botones visibles, de dos en dos y sin huecos."""
        grid = self._action_grid
        for b in self._actions:
            grid.removeWidget(b)
        shown = [b for b in self._actions if not b.isHidden()]
        for i, b in enumerate(shown):
            grid.addWidget(b, i // 2, i % 2)

    def _refresh(self) -> None:
        self._refresh_fields()
        self._layout_actions()

    def _refresh_fields(self) -> None:
        geo = self.geo
        has = geo is not None and geo.ok
        self.btn_save.setVisible(self.ctrl is not None)
        self.btn_save.setEnabled(self.ctrl is not None and self.ctrl.dirty)
        kb = self.kb
        el, reason = self._element()
        if has and kb and kb <= geo.nbodys:
            self.title.setText(f"<b>{ge.element_name(geo, kb, 50)}</b>")
        else:
            self.title.setText("<b>Geometría</b>" if has else "<b>Sin geometría editable</b>")
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self.help.setVisible(el is None)
        editable = el is not None
        is_clone = isinstance(el, CloneElement)
        is_module = isinstance(el, ModuleElement)
        self.box_body.setVisible(editable)
        self.box_body.setTitle("Clon" if is_clone else ("Módulo" if is_module else "Cuerpo"))
        form: QFormLayout = self.box_body.layout()
        form.setRowVisible(self.material, editable and not is_clone)
        form.setRowVisible(self.original, is_clone)
        self.box_transform.setVisible(is_module or is_clone)
        self.box_surfaces.setVisible(editable and not is_clone)
        empty = not is_module or not any(not r.is_surface for r in el.items)
        self.btn_delete.setVisible(editable and (empty or is_clone))
        self.btn_delete.setText("Eliminar el clon…" if is_clone else
                                ("Eliminar este módulo…" if is_module else "Eliminar este cuerpo…"))
        self.btn_glue.setVisible(editable and (is_module or is_clone))
        self.btn_convert.setVisible(editable and not is_module and not is_clone)
        self.btn_materialize.setVisible(is_clone)
        self.btn_duplicate.setVisible(editable)
        self.btn_wrap.setVisible(editable)
        can_add = has and ((editable and not is_clone) or (kb is not None and kb <= geo.nbodys
                                                           and geo.body(kb).origin == "enclosure") or not kb)
        self.btn_add.setEnabled(bool(can_add))
        self.btn_add.setVisible(not is_clone)
        self.btn_add.setText("Añadir dentro…" if editable else "Añadir un cuerpo o módulo…")
        if not editable:
            return
        self.label_edit.setText(el.label.strip())
        self.description.setText(el.description.strip())
        if is_clone:
            orig = geo.find_body(el.module_label)
            self.original.setText(body_title(geo, orig.index) if orig is not None else el.module_label.strip())
        else:
            self.material.setValue(el.material)
        if is_module or is_clone:
            vals = dict.fromkeys(TRANSFORM, 0.0)
            locked: dict[str, int] = {}
            if el.has_ones_line:
                for p in el.transform:
                    if p.name in vals:
                        v = ge.effective_value(p, self.parinp)
                        vals[p.name] = math.degrees(v) if p.is_angle and p.unit == "RAD" else v
                        if p.ipar > 0:
                            locked[p.name] = p.ipar
            for n, ed in self.transform_edits.items():
                ed.set_value(vals[n])
                ed.setEnabled(n not in locked)
                ed.setToolTip(_parinp_tip(locked.get(n)))
            stars = pl.starred_surfaces(geo, kb)
            self.btn_rotate.setEnabled(not stars)
            self.btn_rotate.setToolTip("Usa superficies SURFACE*, que no se mueven con los módulos" if stars else "")
        if not is_clone:
            self._fill_surfaces(el)

    def _fill_surfaces(self, el) -> None:
        doc = self.ctrl.doc
        refs = [r for r in el.items if r.is_surface]
        self.table.setRowCount(len(refs))
        sel_row = None
        for i, r in enumerate(refs):
            s = ge.find_element(doc, "surface", r.label)
            kind = "?"
            if isinstance(s, SurfaceElement):
                kind = sf.surface_kind(s, self.parinp)
            users = [u for u in ge.surface_users(doc, r.label) if u is not el]
            names = ", ".join(("módulo " if isinstance(u, ModuleElement) else "") + u.label.strip()
                              for u in users[:6]) + (" …" if len(users) > 6 else "")
            side = "−1 (F < 0)" if (r.side or 1) < 0 else "+1 (F > 0)"
            for c, text in enumerate((r.label.strip(), kind, side, names or "—")):
                it = QTableWidgetItem(text)
                it.setData(Qt.ItemDataRole.UserRole, r.label)
                self.table.setItem(i, c, it)
            if self._surface_label is not None and ge.same_label(r.label, self._surface_label):
                sel_row = i
        self.table.resizeColumnsToContents()
        if sel_row is None and refs:
            sel_row = 0
        if sel_row is not None:
            self.table.selectRow(sel_row)
        self.btn_remove_surface.setEnabled(len(refs) > 1)
        self._show_surface()

    def _selected_surface(self) -> SurfaceElement | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or self.ctrl is None:
            return None
        lab = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        el = ge.find_element(self.ctrl.doc, "surface", lab)
        return el if isinstance(el, SurfaceElement) else None

    def _on_surface_selected(self) -> None:
        if self._updating:
            return
        s = self._selected_surface()
        self._surface_label = s.label if s is not None else None
        self._updating = True
        try:
            self._show_surface()
        finally:
            self._updating = False

    def _show_surface(self) -> None:
        s = self._selected_surface()
        self.surface_label_row.setVisible(s is not None)
        self.btn_flip.setEnabled(s is not None)
        if s is None:
            self.params_widget.setVisible(False)
            self.implicit_widget.setVisible(False)
            self.btn_implicit.setVisible(False)
            self.surface_note.setText("")
            return
        self._surface_label = s.label
        self.surface_label.setText(s.label.strip())
        users = ge.surface_users(self.ctrl.doc, s.label)
        note = f"Superficie «{s.label.strip()}»: {sf.surface_kind(s, self.parinp)}."
        if len(users) > 1:
            note += (f" La usan {len(users)} elementos: al cambiarla cambian todos (se comprueba que no se "
                     "solapen).")
        self.surface_note.setText(note)
        self.params_widget.setVisible(not s.implicit)
        self.implicit_widget.setVisible(s.implicit)
        self.btn_implicit.setVisible(not s.implicit)
        if s.implicit:
            coefs, tr = sf.implicit_values(s, self.parinp)
            locked = {p.name: p.ipar for p in s.params if p.ipar > 0}
            locked.update({p.name: p.ipar for p in s.transform if p.ipar > 0})
            for n, ed in self.coef_edits.items():
                ed.set_value(coefs[n])
                ed.setEnabled(n not in locked)
                ed.setToolTip(_parinp_tip(locked.get(n)))
            for n, ed in self.implicit_transform.items():
                ed.set_value(tr[n])
                ed.setEnabled(n not in locked)
                ed.setToolTip(_parinp_tip(locked.get(n)))
            return
        vals = ge.reduced_values(s, self.parinp)  # los de PARINP, con su valor efectivo
        locked = {p.name: p.ipar for p in s.params if p.ipar > 0}
        i1, i2, i3, i4, _ = s.indices
        # Las escalas que no aparecen en la ecuación reducida no hacen nada: no se muestran.
        relevant = {"X-SCALE": i1 != 0, "Y-SCALE": i2 != 0, "Z-SCALE": i3 != 0 or i4 != 0}
        form: QFormLayout = self.params_widget.layout()
        for n, ed in self.param_edits.items():
            ed.set_value(vals[n])
            ed.setEnabled(n not in locked)
            ed.setToolTip(_parinp_tip(locked.get(n)))
            form.setRowVisible(ed, relevant.get(n, True))

    # -- ediciones ---------------------------------------------------------------------------
    def _body_label(self) -> str | None:
        el, _ = self._element()
        return el.label if el is not None else None

    def _guard_for_surface(self, surface_label: str):
        """Comprobación de solapes de un cambio en una superficie: los cuerpos que la usan."""
        doc = self.ctrl.doc
        labels = [u.label for u in ge.surface_users(doc, surface_label)]
        return guard(self.ctrl.session.bounds, labels)

    def _guard_for_body(self, label: str):
        return guard(self.ctrl.session.bounds, [label])

    def _on_label(self) -> None:
        if self._updating:
            return
        old = self._body_label()
        if old is None:
            return
        text = self.label_edit.text()
        if text.strip() == old.strip():
            return
        try:
            new = sf.label_text(text)
        except ge.EditError as exc:
            self.label_edit.setText(old.strip())
            self.label_edit.setToolTip(str(exc))
            self.label_edit.setStyleSheet("QLineEdit { background: #f8d7da; }")
            return
        self.label_edit.setStyleSheet("")

        def fn(doc, geo):
            b = geo.find_body(old)
            return sf.rename_body(doc, geo, b.index, new)[1]

        self.editRequested.emit(f"Etiqueta «{old.strip()}» → «{new.strip()}»", with_(fn, renames={old: new}))

    def _on_material(self) -> None:
        if self._updating:
            return
        lab = self._body_label()
        if lab is None:
            return
        m = self.material.value()
        el = ge.find_element(self.ctrl.doc, "body", lab)
        if el is None or el.material == m:
            return
        self.editRequested.emit(f"Material del cuerpo {lab.strip()}",
                                lambda doc, geo: ge.set_material(ge.find_element(doc, "body", lab), m))

    def _on_description(self) -> None:
        if self._updating:
            return
        el, _ = self._element()
        if el is None:
            return
        text = self.description.text()
        if el.description.strip() == " ".join(text.split()):
            return
        lab = el.label

        def fn(doc, geo):
            ge.set_description(sf.renamable_body(geo, geo.find_body(lab).index), text)

        self.editRequested.emit(f"Nombre de «{lab.strip()}»", fn)

    def _on_original(self) -> None:
        el, _ = self._element()
        if isinstance(el, CloneElement):
            orig = self.geo.find_body(el.module_label)
            if orig is not None:
                self.selectRequested.emit(orig.index)

    def _on_transform(self, name: str, value: float) -> None:
        if self._updating:
            return
        lab = self._body_label()
        if lab is None:
            return

        def fn(doc, geo):  # con copias propias de las superficies que comparta con cuerpos de fuera
            pl.set_placement(doc, geo, geo.find_body(lab).index, {name: value})

        self.editRequested.emit(f"Colocar «{lab.strip()}»", with_(fn, check=self._guard_for_body(lab)))

    def _on_rotate(self) -> None:
        if not self.kb:
            return
        axis = self.rot_axis.currentData()
        vec = [1.0 if a == axis else 0.0 for a in "xyz"]
        ang = self.rot_angle.value() or 0.0
        if ang:
            self.rotateRequested.emit(self.kb, vec, float(ang))

    def _on_param(self, name: str, value: float) -> None:
        if self._updating:
            return
        s = self._selected_surface()
        if s is None:
            return
        lab = s.label
        self.editRequested.emit(f"Superficie {lab.strip()}: {PARAM_LABELS[name]}",
                                with_(lambda doc, geo: ge.set_surface_params(ge.find_element(doc, "surface", lab),
                                                                             {name: value}),
                                      check=self._guard_for_surface(lab)))

    def _on_coef(self, name: str, value: float) -> None:
        if self._updating:
            return
        s = self._selected_surface()
        if s is None:
            return
        lab = s.label
        self.editRequested.emit(f"Superficie {lab.strip()}: {name}",
                                with_(lambda doc, geo: sf.set_implicit(ge.find_element(doc, "surface", lab),
                                                                       {name: value}),
                                      check=self._guard_for_surface(lab)))

    def _on_implicit_transform(self, name: str, value: float) -> None:
        if self._updating:
            return
        s = self._selected_surface()
        if s is None:
            return
        lab = s.label
        self.editRequested.emit(f"Superficie {lab.strip()}: {PARAM_LABELS[name]}",
                                with_(lambda doc, geo: sf.set_implicit(ge.find_element(doc, "surface", lab), {},
                                                                       {name: value}),
                                      check=self._guard_for_surface(lab)))

    def _on_to_implicit(self) -> None:
        s = self._selected_surface()
        if s is None:
            return
        lab = s.label
        parinp = self.parinp
        self.editRequested.emit(f"Superficie {lab.strip()} en forma implícita",
                                lambda doc, geo: sf.to_implicit(ge.find_element(doc, "surface", lab), parinp))

    def _on_surface_label(self) -> None:
        if self._updating:
            return
        s = self._selected_surface()
        if s is None:
            return
        text = self.surface_label.text()
        if text.strip() == s.label.strip():
            return
        old = s.label
        try:
            new = sf.label_text(text)
        except ge.EditError as exc:
            self.surface_label.setText(old.strip())
            self.surface_label.setToolTip(str(exc))
            return
        self._surface_label = new
        self.editRequested.emit(f"Etiqueta de la superficie «{old.strip()}» → «{new.strip()}»",
                                lambda doc, geo: sf.rename_surface(doc, geo, old, new))

    def _on_flip(self) -> None:
        s = self._selected_surface()
        lab = self._body_label()
        if s is None or lab is None:
            return
        el = ge.find_element(self.ctrl.doc, "body", lab)
        ref = next((r for r in el.items if r.is_surface and ge.same_label(r.label, s.label)), None)
        if ref is None:
            return
        side = -(ref.side or 1)
        slab = s.label
        self.editRequested.emit(f"Lado de la superficie {slab.strip()} en «{lab.strip()}»",
                                with_(lambda doc, geo: sf.set_side(doc, geo, geo.find_body(lab).index, slab, side),
                                      check=self._guard_for_body(lab)))

    def _on_remove_surface(self) -> None:
        s = self._selected_surface()
        lab = self._body_label()
        if s is None or lab is None:
            return
        slab = s.label
        self._surface_label = None
        self.editRequested.emit(f"Quitar la superficie {slab.strip()} de «{lab.strip()}»",
                                with_(lambda doc, geo: sf.remove_surface(doc, geo, geo.find_body(lab).index, slab),
                                      check=self._guard_for_body(lab)))
