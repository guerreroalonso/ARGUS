"""Diálogo «Envolver en un módulo»: un módulo nuevo alrededor de cuerpos que ya existen."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..pengeom import edit as ge
from ..pengeom.edit import EditError
from ..pengeom.extent import members_box
from ..pengeom.wrap import MIN_CLEARANCE, check_wrap, fitted_primitive, sample_solids, wrap_candidates
from .add_body_dialog import combo_material
from .primitive_form import PrimitiveForm
from .source_panel import NumberEdit, parse_number


class WrapDialog(QDialog):
    """``extent_fn(kb) -> (lo, hi)``: caja exacta de un elemento."""

    def __init__(self, parent, session, kb: int, extent_fn, material_names: dict[int, str] | None = None):
        super().__init__(parent)
        self.session = session
        self.geo = geo = session.geo
        self.extent_fn = extent_fn
        self._ok = False
        self.container, cands = wrap_candidates(geo, kb)
        self.setWindowTitle("Envolver en un módulo")
        lay = QVBoxLayout(self)
        cont = ("la envolvente" if geo.enclosure_added and self.container == geo.nbodys
                else ge.element_name(geo, self.container))
        head = QLabel(f"Un módulo nuevo dentro de <b>{html.escape(cont)}</b> alrededor de los elementos marcados "
                      "(pasan a ser sus hijas). Así se pueden mover, girar o duplicar juntos. El hueco del módulo "
                      "se llena del material del contenedor: lo que se simula no cambia.")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.items = QListWidget()
        self.items.setMaximumHeight(140)
        for k in cands:
            it = QListWidgetItem(ge.element_name(geo, k, 50))
            it.setData(Qt.ItemDataRole.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if k == kb else Qt.CheckState.Unchecked)
            self.items.addItem(it)
        self.items.itemChanged.connect(lambda _it: self._fit())
        lay.addWidget(self.items)
        form = QFormLayout()
        lay.addLayout(form)
        row = QHBoxLayout()
        self.margin = NumberEdit(width=70, minimum=0.0)
        self.margin.set_value(0.0)
        self.margin.setToolTip("Espacio (cm) entre los elementos y las caras del módulo (con 0 se deja 1e-5 cm: "
                               "una pared en el mismo sitio que una cara confundiría a PENGEOM al transportar)")
        self.btn_fit = QPushButton("Ajustar a los elementos")
        self.btn_fit.clicked.connect(self._fit)
        row.addWidget(self.margin)
        row.addWidget(QLabel("cm"))
        row.addWidget(self.btn_fit)
        row.addStretch(1)
        w = QWidget()
        row.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row)
        form.addRow("Margen:", w)
        self.shape = PrimitiveForm()
        self.shape.kind.currentIndexChanged.connect(lambda _i: self._fit())
        self.shape.axis.currentIndexChanged.connect(lambda _i: self._fit())
        self.shape.changed.connect(lambda: self._timer.start())
        form.addRow(self.shape)
        self.material = QComboBox()
        self.material.setEditable(True)
        self.material.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        names = material_names or {}
        cm = 0 if ge.is_top_level(geo, self.container) else geo.body(self.container).material
        self.material.addItem(f"{cm} (el del contenedor)" + (f" · {names[cm]}" if cm in names else ""), cm)
        for m in sorted(set(geo.materials()) | set(names) | {0}):
            if m != cm:
                self.material.addItem(f"{m}" + (" (vacío)" if m == 0 else "") + (f" · {names[m]}" if m in names
                                                                                  else ""), m)
        self.material.setToolTip("Material del hueco del módulo (lo que no ocupan sus hijas)")
        form.addRow("Material:", self.material)
        self.name = QLineEdit()
        self.name.setPlaceholderText("opcional (comentario del módulo en el .geo)")
        form.addRow("Nombre:", self.name)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setMinimumWidth(460)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Envolver")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._check)
        self.material.editTextChanged.connect(lambda _t: self._timer.start())
        self.margin.valueEdited.connect(lambda _v: self._fit())
        self._fit()

    # -- valores ---------------------------------------------------------------------
    def chosen(self) -> list[int]:
        return [int(self.items.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.items.count())
                if self.items.item(i).checkState() == Qt.CheckState.Checked]

    def primitive(self):
        return self.shape.primitive()

    def material_number(self) -> int:
        return combo_material(self.material)

    def module_name(self) -> str:
        return self.name.text().strip()

    # -- interfaz -----------------------------------------------------------------------
    def _fit(self) -> None:
        kbs = self.chosen()
        if not kbs:
            self._set(False, "Marque al menos un elemento.")
            return
        # Con lo que asoman sus cuerpos limitantes (van dentro del módulo).
        bounds = getattr(self.session, "bounds", None)
        exts = [members_box(self.geo, k, self.extent_fn(k), bounds) for k in kbs]
        try:
            margin = parse_number(self.margin.text())
        except ValueError:
            margin = self.margin.value() or 0.0
        margin = max(margin, 0.0)  # el campo no admite negativos (se corrige al salir de él)
        pts = sample_solids(self.geo, self.session.locator, kbs, exts, n=6000, bounds=bounds)
        kind, axis = self.shape.kind.currentData(), self.shape.axis.currentData()
        prim = fitted_primitive(exts, kind, margin, axis, pts)
        if 0.0 <= margin < MIN_CLEARANCE:
            # Si no cabe con la holgura (los elementos tocan la pared del contenedor), justo.
            ext_fn = [self.extent_fn(k) for k in kbs]

            def fits(p) -> bool:
                try:
                    return check_wrap(self.geo, self.session.locator, kbs, p, self.container, ext_fn, bounds).ok
                except EditError:  # forma imposible: ya lo dirá _check
                    return False

            if not fits(prim):
                exact = fitted_primitive(exts, kind, margin, axis, pts, clearance=0.0)
                if fits(exact):
                    prim = exact
        self.shape.blockSignals(True)
        self.shape.set_primitive(prim)
        self.shape.blockSignals(False)
        self._timer.start()

    def _check(self) -> None:
        kbs = self.chosen()
        if not kbs:
            self._set(False, "Marque al menos un elemento.")
            return
        try:
            prim = self.primitive()
            self.material_number()
        except (EditError, ValueError) as exc:
            self._set(False, html.escape(str(exc)))
            return
        exts = [self.extent_fn(k) for k in kbs]
        chk = check_wrap(self.geo, self.session.locator, kbs, prim, self.container, exts, self.session.bounds)
        self._set(chk.ok, html.escape(chk.message))

    def _set(self, ok: bool, message: str) -> None:
        self._ok = ok
        color = "#1a7f37" if ok else "#b42318"
        self.status.setText(f"<span style='color:{color}'>{'✔' if ok else '✖'} {message}</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def edit_fn(self):
        """La edición que crea el módulo alrededor de lo elegido (la que hace ``MainWindow``)."""
        from ..pengeom.wrap import wrap
        from .geo_controller import body_finder

        chosen = list(self.chosen())
        prim, mat, name = self.primitive(), self.material_number(), self.module_name()
        find, parinp = body_finder(self.geo), self.session.parinp
        return lambda doc, g: wrap(doc, g, [find(g, k) for k in chosen], prim, mat, name, parinp)

    def accept(self) -> None:
        self._timer.stop()
        self._check()
        if not self._ok:
            return
        from .geo_controller import trial_edit  # probado en una copia: si no se puede, se dice aquí

        try:
            problem = trial_edit(self.session, self.edit_fn())
        except (EditError, ValueError) as exc:
            problem = str(exc)
        if problem:
            self._set(False, html.escape(problem))
            return
        super().accept()
