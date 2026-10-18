"""Secciones del panel «Simulación» sobre la física: materiales, geometría, campos y reducción de varianza.

* Parámetros de simulación de cada material (MSIMPA, MSIMPH, MSIMPN), con los
  valores que usará penhmain de verdad (recorta algunos sin avisar).
* PARINP y parámetros locales por cuerpo (DSMAX, EABSB, EABSBH).
* Campos electromagnéticos (PFIELD, EFIELD, BFIELD).
* Reducción de varianza (IFORCE, IBRSPL, IXRSPL).
* Distribuciones de partículas emergentes (NBE, NBANGL).
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..core.numfmt import fmt_num, plural
from ..penhan import sections as sc
from ..penhan.simcheck import ICOL_NAMES, ICOL_OK
from .sim_outputs import Section
from .sim_widgets import BinsEdit, BodyListEditor, Column, compact_combo, hint, row
from .source_panel import EnergyEdit, NumberEdit, format_energy

MSIMPA_FIELDS = [("EABS1", "e−", "energy"), ("EABS2", "γ", "energy"), ("EABS3", "e+", "energy"),
                 ("C1", "C1", "real"), ("C2", "C2", "real"), ("WCC", "WCC", "energy"), ("WCR", "WCR", "energy")]
MSIMPH_FIELDS = [("EABSH1", "p", "energy"), ("EABSH2", "α", "energy"), ("EABSH3", "n", "energy"),
                 ("C1H", "C1", "real"), ("C2H", "C2", "real"), ("WCCH", "WCC", "energy")]
MSIMPN_FIELDS = [("FNABS", "FNABS", "real"), ("FORCEN", "FORCEN", "real")]
FIELD_HELP = {
    "EABS1": "Energía de absorción de electrones: por debajo se dejan de seguir y depositan su energía ahí.",
    "EABS2": "Energía de absorción de fotones.", "EABS3": "Energía de absorción de positrones.",
    "C1": "Desviación angular media en un paso (0 a 0,2): mayor = más rápido y menos detallado.",
    "C2": "Pérdida fraccional máxima de energía en un paso (0 a 0,2).",
    "WCC": "Energía de corte de las colisiones inelásticas duras (eV).",
    "WCR": "Energía de corte de la emisión radiativa dura (eV); negativa: sin bremsstrahlung blando.",
    "EABSH1": "Energía de absorción de protones (mínimo 100 keV).",
    "EABSH2": "Energía de absorción de partículas alfa (mínimo 100 keV).",
    "EABSH3": "Energía de absorción de neutrones (mínimo 100 keV).",
    "C1H": "C1 de protones y alfas (0 a 0,2).", "C2H": "C2 de protones y alfas (0 a 0,2).",
    "WCCH": "WCC de protones y alfas (eV).",
    "FNABS": "Parámetro de absorción de neutrones (FNABS).",
    "FORCEN": "Factor de forzado de las colisiones elásticas de neutrones.",
}


# ---------------------------------------------------------------------------
# Materiales
# ---------------------------------------------------------------------------

class MaterialsSection(Section):
    title = "Materiales: parámetros de simulación"
    keywords = {"MFNAME", "MSIMPA", "MSIMPH", "MSIMPN"}

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        top = QHBoxLayout()
        top.addWidget(QLabel("Material:"))
        self.mat = compact_combo(QComboBox(), 12)
        self.mat.currentIndexChanged.connect(lambda _i: self._fill())
        top.addWidget(self.mat, 1)
        lay.addLayout(top)
        self.edits: dict[str, NumberEdit] = {}
        self.groups = {}
        layout_rows = {
            "MSIMPA": [("Absorción:", ["EABS1", "EABS2", "EABS3"]), ("Pasos:", ["C1", "C2"]),
                       ("Cortes:", ["WCC", "WCR"])],
            "MSIMPH": [("Absorción:", ["EABSH1", "EABSH2", "EABSH3"]), ("Pasos:", ["C1H", "C2H"]),
                       ("Corte:", ["WCCH"])],
            "MSIMPN": [("Neutrones:", ["FNABS", "FORCEN"])],
        }
        for key, title, fields in (("MSIMPA", "Electrones, fotones y positrones (MSIMPA)", MSIMPA_FIELDS),
                                   ("MSIMPH", "Protones, alfas y neutrones (MSIMPH)", MSIMPH_FIELDS),
                                   ("MSIMPN", "Neutrones (MSIMPN)", MSIMPN_FIELDS)):
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setContentsMargins(6, 4, 6, 4)
            kinds = {name: (label, kind) for name, label, kind in fields}
            for caption, names in layout_rows[key]:
                parts = []
                for name in names:
                    label, kind = kinds[name]
                    cls = EnergyEdit if kind == "energy" else NumberEdit
                    e = cls(width=76 if kind == "energy" else 52, tooltip=f"{name}: {FIELD_HELP[name]}")
                    e.valueEdited.connect(lambda v, key=key, name=name: self._on_value(key, name, v))
                    self.edits[name] = e
                    parts += [label, e]
                form.addRow(caption, row(*parts))
            status = hint("")
            status.setTextFormat(Qt.TextFormat.RichText)
            form.addRow(status)
            btn_default = QPushButton("Quitar la línea (valores por defecto)")
            btn_default.clicked.connect(lambda _c=False, key=key: self._remove_line(key))
            form.addRow(row(btn_default))
            self.groups[key] = (box, status, btn_default)
            lay.addWidget(box)
        btns = QHBoxLayout()
        self.btn_all = QPushButton("Aplicar estos valores a todos los materiales")
        self.btn_all.clicked.connect(self._apply_all)
        btns.addWidget(self.btn_all)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.note = hint("")
        self.note.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.note)

    def number(self) -> int:
        return int(self.mat.currentData() or 0)

    def refresh(self) -> None:
        doc = self.doc
        n = len(doc.find("MFNAME")) if doc is not None else 0
        cur = self.number() or 1
        self.mat.blockSignals(True)
        self.mat.clear()
        for m, ln in enumerate(doc.find("MFNAME") if doc is not None else [], start=1):
            self.mat.addItem(f"{m}: {ln.text.strip()}", m)
        if n:
            self.mat.setCurrentIndex(min(cur, n) - 1)
        self.mat.blockSignals(False)
        self._fill()

    def _fill(self) -> None:
        doc = self.doc
        n = self.number()
        has = doc is not None and n > 0
        for box, _, _ in self.groups.values():
            box.setEnabled(has)
        self.btn_all.setEnabled(has and self.mat.count() > 1)
        if not has:
            self.note.setText("El .in no declara materiales (líneas MFNAME).")
            return
        par = sc.material_params(doc, n)
        eff = sc.effective_material(doc, n)
        kparp = doc.model.source.kparp
        hadrons = kparp == 0 or kparp > 3
        for key, fields in (("MSIMPA", MSIMPA_FIELDS), ("MSIMPH", MSIMPH_FIELDS), ("MSIMPN", MSIMPN_FIELDS)):
            vals = par[key]
            defaults = sc.default(key)
            box, status, btn = self.groups[key]
            for i, (name, _, _) in enumerate(fields):
                e = self.edits[name]
                e.set_value(float(vals[i]) if vals is not None else None)
                d = defaults[i]
                e.setPlaceholderText(format_energy(d) if isinstance(e, EnergyEdit) else f"{d:g}")
            btn.setVisible(vals is not None)
            notes = []
            if vals is None:
                notes.append("Sin línea: penhmain usa los valores por defecto (en gris).")
            changed = []
            for i, (name, _, kind) in enumerate(fields):
                given = float(vals[i]) if vals is not None else float(defaults[i])
                if name in eff and abs(eff[name] - given) > 1e-12 * max(abs(given), 1.0):
                    shown = format_energy(eff[name]) if kind == "energy" else f"{eff[name]:g}"
                    changed.append(f"{name} = {shown}")
            if changed:
                notes.append("<span style='color:#9a6700'>penhmain usará " + ", ".join(changed) + ".</span>")
            if key != "MSIMPA" and not hadrons:
                notes.append("Solo se usan con protones, alfas o fuentes radiactivas.")
            status.setText(" ".join(notes))
        src = doc.model.source
        emax = max((e for e, _ in (src.spectrum or src.lines)), default=src.energy) if not src.psf else None
        msg = []
        if emax and 1 <= kparp <= 3:
            e = eff[f"EABS{kparp}"]
            msg.append(f"Energía de la fuente: {format_energy(emax)}; absorción de las primarias en este material: "
                       f"{format_energy(e)} ({100 * e / emax:.3g} %).")
        elif emax and kparp in (4, 5):
            e = eff[f"EABSH{kparp - 3}"]
            msg.append(f"Energía de la fuente: {format_energy(emax)}; absorción de las primarias en este material: "
                       f"{format_energy(e)}. Los electrones secundarios se absorben por debajo de "
                       f"{format_energy(eff['EABS1'])}.")
        self.note.setText(" ".join(html.escape(m) for m in msg))

    def _current_values(self, key: str) -> list:
        par = sc.material_params(self.doc, self.number())
        return list(par[key]) if par[key] is not None else list(sc.default(key))

    def _on_value(self, key: str, name: str, v: float) -> None:
        fields = {"MSIMPA": MSIMPA_FIELDS, "MSIMPH": MSIMPH_FIELDS, "MSIMPN": MSIMPN_FIELDS}[key]
        vals = self._current_values(key)
        i = [f[0] for f in fields].index(name)
        vals[i] = v
        n = self.number()
        self.edit(f"Material {n}: {name}", lambda doc: sc.set_material_param(doc, n, key, vals))

    def _remove_line(self, key: str) -> None:
        n = self.number()
        self.edit(f"Material {n}: quitar {key}", lambda doc: sc.set_material_param(doc, n, key, None))

    def _apply_all(self) -> None:
        n = self.number()
        par = sc.material_params(self.doc, n)

        def fn(doc):
            for key in sc.MATERIAL_PARAMS:
                sc.set_all_material_params(doc, key, par[key])

        self.edit(f"Parámetros del material {n} en todos", fn)

    def summary(self) -> str:
        doc = self.doc
        if doc is None:
            return ""
        n = len(doc.find("MFNAME"))
        if not n:
            return "ningún material"
        eff = sc.effective_material(doc, 1)
        kp = doc.model.source.kparp
        if kp in (4, 5):
            txt = f"p {format_energy(eff['EABSH1'])}, e− {format_energy(eff['EABS1'])}"
        else:
            txt = f"e− {format_energy(eff['EABS1'])}, γ {format_energy(eff['EABS2'])}"
        return (f"{plural(n, 'material', 'materiales')} · absorción (mat. 1): {txt} · C1 {fmt_num(eff['C1'], 6)}, "
                f"C2 {fmt_num(eff['C2'], 6)}")


# ---------------------------------------------------------------------------
# Geometría: PARINP y parámetros locales
# ---------------------------------------------------------------------------

class GeometrySection(Section):
    title = "Geometría: PARINP y parámetros por cuerpo"
    keywords = {"GEOMFN", "PARINP", "DSMAX ", "EABSB ", "EABSBH"}
    bodySelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        self.parinp_box = QGroupBox("Parámetros de la geometría (PARINP)")
        pl = QVBoxLayout(self.parinp_box)
        self.parinp_grid = QGridLayout()
        pl.addLayout(self.parinp_grid)
        self.parinp_hint = hint("")
        pl.addWidget(self.parinp_hint)
        lay.addWidget(self.parinp_box)
        self.editors = {}
        for key, title, cols, text in (
                ("DSMAX", "Paso máximo de las partículas cargadas (DSMAX)",
                 [Column("DSMAX (cm)", "real", 1.0e-1, "Longitud máxima de paso en el cuerpo (cm); penhmain "
                         "recomienda del orden de 1/10 de su grosor", minimum=0.0, width=96)],
                 "Solo importa en cuerpos delgados. Un valor menor que 1e-7 cm significa «sin límite»."),
                ("EABSB", "Energías de absorción locales (EABSB)",
                 [Column("e−", "energy", 1.0e4, width=92), Column("γ", "energy", 1.0e3, width=92),
                  Column("e+", "energy", 1.0e4, width=92)],
                 "Sustituyen a las del material solo si son mayores (para ahorrar trabajo en zonas sin interés)."),
                ("EABSBH", "Energías de absorción locales de hadrones (EABSBH)",
                 [Column("p", "energy", 1.0e6, width=92), Column("α", "energy", 1.0e6, width=92),
                  Column("n", "energy", 1.0e6, width=92)],
                 "Igual que EABSB para protones, alfas y neutrones.")):
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            ed = BodyListEditor(key, cols, max_rows=sc.MAX_BODY_LIST[key])
            ed.changed.connect(lambda entries, key=key: self.edit(f"Líneas {key}",
                                                                  lambda doc: sc.set_body_list(doc, key, entries)))
            ed.bodySelected.connect(self.bodySelected)
            bl.addWidget(ed)
            bl.addWidget(hint(text))
            self.editors[key] = ed
            lay.addWidget(box)

    def refresh(self) -> None:
        doc = self.doc
        for key, ed in self.editors.items():
            ed.set_entries(sc.body_list(doc, key) if doc is not None else [], self.geo)
        self._fill_parinp()

    def _fill_parinp(self) -> None:
        from ..pengeom.parinp import parinp_uses

        while self.parinp_grid.count():
            w = self.parinp_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        doc = self.doc
        values = sc.parinp(doc) if doc is not None else {}
        uses = parinp_uses(self.geo.document) if self.geo is not None else {}
        idx = sorted(set(values) | set(uses))
        if not idx:
            self.parinp_hint.setText("La geometría no tiene parámetros sustituibles (números marcados con un índice "
                                     "en el .geo).")
            return
        for r, i in enumerate(idx):
            self.parinp_grid.addWidget(QLabel(f"PARINP({i})"), r, 0)
            e = NumberEdit(width=90)
            e.set_value(values.get(i))
            if i in uses:
                e.setPlaceholderText(f"falta (.geo: {uses[i].value:g})")
            e.valueEdited.connect(lambda v, i=i: self._on_parinp(i, v))
            self.parinp_grid.addWidget(e, r, 1)
            who = ", ".join(uses[i].names) if i in uses else "no lo usa la geometría"
            lab = QLabel(who)
            lab.setStyleSheet("color: #666;")
            self.parinp_grid.addWidget(lab, r, 2)
        self.parinp_hint.setText("Cada PARINP(i) sustituye a los números del .geo marcados con el índice i. Al "
                                 "cambiarlos se recalcula la geometría.")

    def _on_parinp(self, i: int, v: float) -> None:
        values = sc.parinp(self.doc)
        values[i] = v
        self.edit(f"PARINP({i})", lambda doc: sc.set_parinp(doc, values))

    def summary(self) -> str:
        doc = self.doc
        if doc is None:
            return ""
        parts = [doc.model.geomfn or "sin GEOMFN"]
        if sc.parinp(doc):
            parts.append(f"{len(sc.parinp(doc))} PARINP")
        for key in ("DSMAX", "EABSB", "EABSBH"):
            n = len(doc.find(key))
            if n:
                parts.append(f"{n} {key}")
        return " · ".join(parts)


# ---------------------------------------------------------------------------
# Campos electromagnéticos
# ---------------------------------------------------------------------------

class FieldsSection(Section):
    title = "Campos electromagnéticos"
    keywords = {"PFIELD", "EFIELD", "BFIELD"}
    bodySelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        f = QFormLayout()
        self.ulde = NumberEdit(minimum=0.0, width=70, tooltip="ULDE: variación relativa máxima de la energía en un "
                                                              "paso")
        self.uldv = NumberEdit(minimum=0.0, width=70, tooltip="ULDV: variación relativa máxima de la velocidad")
        self.ulde.valueEdited.connect(lambda v: self._on_pfield())
        self.uldv.valueEdited.connect(lambda v: self._on_pfield())
        f.addRow("Precisión:", row("ULDE", self.ulde, "ULDV", self.uldv, "(PFIELD)"))
        lay.addLayout(f)
        self.editors = {}
        for key, title, unit in (("EFIELD", "Campo eléctrico (V/cm)", "V/cm"), ("BFIELD", "Campo magnético (gauss)",
                                                                                 "G")):
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            ed = BodyListEditor(key, [Column(f"{c} ({unit})", "real", 0.0, width=86) for c in ("x", "y", "z")],
                                max_rows=sc.MAX_BODY_LIST[key])
            ed.changed.connect(lambda entries, key=key: self.edit(f"Líneas {key}",
                                                                  lambda doc: sc.set_body_list(doc, key, entries)))
            ed.bodySelected.connect(self.bodySelected)
            bl.addWidget(ed)
            self.editors[key] = ed
            lay.addWidget(box)
        lay.addWidget(hint("El campo es uniforme dentro de cada cuerpo. En el vacío no se transporta con campo: "
                           "rellene esas zonas con un material de densidad muy baja. Un cuerpo con campo no admite "
                           "forzado de interacciones ni splitting."))

    def refresh(self) -> None:
        doc = self.doc
        v = sc.value(doc, "PFIELD") if doc is not None else None
        self.ulde.set_value(v[0] if v else None)
        self.uldv.set_value(v[1] if v else None)
        self.ulde.setPlaceholderText("0.05")
        self.uldv.setPlaceholderText("0.05")
        for key, ed in self.editors.items():
            ed.set_entries(sc.body_list(doc, key) if doc is not None else [], self.geo)

    def _on_pfield(self) -> None:
        a = self.ulde.value() if self.ulde.value() is not None else 0.05
        b = self.uldv.value() if self.uldv.value() is not None else 0.05
        self.edit("Precisión del campo", lambda doc: sc.set_value(doc, "PFIELD", [a, b]))

    def summary(self) -> str:
        doc = self.doc
        if doc is None:
            return ""
        ne, nb = len(doc.find("EFIELD")), len(doc.find("BFIELD"))
        if not ne and not nb:
            return "sin campos"
        parts = (f"E en {plural(ne, 'cuerpo')}" if ne else "", f"B en {plural(nb, 'cuerpo')}" if nb else "")
        return ", ".join(t for t in parts if t)


# ---------------------------------------------------------------------------
# Reducción de varianza
# ---------------------------------------------------------------------------

SHORT = {1: "e−", 2: "γ", 3: "e+", 4: "p", 5: "α"}
FORCE_CHOICES = [((kp, icol), f"{SHORT[kp]} · {ICOL_NAMES[kp][icol]}")
                 for kp in range(1, 6) for icol in ICOL_OK[kp]]


class VarianceSection(Section):
    title = "Reducción de varianza"
    keywords = {"IFORCE", "IBRSPL", "IXRSPL"}
    bodySelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        box = QGroupBox("Forzado de interacciones (IFORCE)")
        bl = QVBoxLayout(box)
        self.iforce = BodyListEditor("IFORCE", [
            Column("Interacción", "choice", (1, 4), "Partícula (KP) e interacción forzada (ICOL)",
                   choices=FORCE_CHOICES, width=140, nvals=2),
            Column("FORCER", "real", 10.0, "Factor de forzado (> 1). Negativo, −N: N interacciones por partícula de "
                   "energía máxima (o recorrido libre N cm para fotones)", width=64),
            Column("WLOW", "real", 0.1, "Peso mínimo al que se aplica", minimum=0.0, width=46),
            Column("WHIG", "real", 2.0, "Peso máximo al que se aplica", minimum=0.0, width=46)],
            allow_material=True, max_rows=sc.MAX_BODY_LIST["IFORCE"])
        self.iforce.changed.connect(self._on_iforce)
        self.iforce.bodySelected.connect(self.bodySelected)
        bl.addWidget(self.iforce)
        bl.addWidget(hint("La simulación hace FORCER veces más interacciones de ese tipo, con pesos corregidos. "
                          "Úselo con cuidado: compruebe con pruebas cortas que mejora la eficiencia."))
        lay.addWidget(box)
        self.split = {}
        for key, title, text in (("IBRSPL", "Splitting de bremsstrahlung (IBRSPL)",
                                  "En penhmain solo actúa en cuerpos con forzado de interacciones de partículas "
                                  "cargadas (si no, se detiene: IRETRN 3068)."),
                                 ("IXRSPL", "Splitting de rayos X característicos (IXRSPL)",
                                  "Cada rayo X de segunda generación se divide en IXRSPL con direcciones "
                                  "isótropas.")):
            b = QGroupBox(title)
            v = QVBoxLayout(b)
            ed = BodyListEditor(key, [Column("Factor", "int", 2, "Número de copias (≥ 1)", minimum=1.0, width=70)],
                                allow_material=True, max_rows=sc.MAX_BODY_LIST[key])
            ed.changed.connect(lambda e, key=key: self.edit(f"Líneas {key}",
                                                            lambda doc: sc.set_body_list(doc, key, e)))
            ed.bodySelected.connect(self.bodySelected)
            v.addWidget(ed)
            v.addWidget(hint(text))
            self.split[key] = ed
            lay.addWidget(b)
        lay.addWidget(hint("Con forzado o splitting no se pueden usar detectores de energía depositada."))

    def _on_iforce(self, entries) -> None:
        for e in entries:
            e.values[0], e.values[1] = int(e.values[0]), int(e.values[1])
        self.edit("Forzado de interacciones", lambda doc: sc.set_body_list(doc, "IFORCE", entries))

    def refresh(self) -> None:
        doc = self.doc
        self.iforce.set_entries(sc.body_list(doc, "IFORCE") if doc is not None else [], self.geo)
        for key, ed in self.split.items():
            ed.set_entries(sc.body_list(doc, key) if doc is not None else [], self.geo)

    def summary(self) -> str:
        doc = self.doc
        if doc is None:
            return ""
        parts = [f"{len(doc.find(k))} {k}" for k in ("IFORCE", "IBRSPL", "IXRSPL") if doc.find(k)]
        return " · ".join(parts) if parts else "no"


# ---------------------------------------------------------------------------
# Partículas emergentes
# ---------------------------------------------------------------------------

class EmergingSection(Section):
    title = "Partículas que salen de la geometría"
    keywords = {"NBE   ", "NBANGL"}

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self)
        f.setContentsMargins(4, 2, 4, 4)
        self.el = EnergyEdit(minimum=0.0, width=96)
        self.eu = EnergyEdit(minimum=0.0, width=96)
        self.el.valueEdited.connect(lambda v: self._on_nbe())
        self.eu.valueEdited.connect(lambda v: self._on_nbe())
        f.addRow("Energía:", row(self.el, "a", self.eu))
        self.nbe = BinsEdit()
        self.nbe.valueEdited.connect(lambda n: self._on_nbe())
        f.addRow("Intervalos:", self.nbe)
        self.nth = BinsEdit(maximum=900)
        self.nth.valueEdited.connect(lambda n: self._on_angles())
        f.addRow("θ:", self.nth)
        self.nph = QSpinBox()
        self.nph.setRange(1, 180)
        self.nph.setKeyboardTracking(False)
        self.nph.valueChanged.connect(lambda n: self._on_angles())
        f.addRow("φ:", row(self.nph, "intervalos (1 = promedio azimutal)"))
        f.addRow(hint("Distribuciones en energía y ángulo de las partículas que salen del sistema (NBE y NBANGL). "
                      "Con EU = 0 el límite es la energía máxima de la fuente."))

    def refresh(self) -> None:
        doc = self.doc
        if doc is None:
            return
        v = sc.value(doc, "NBE") or list(sc.default("NBE"))
        self.el.set_value(float(v[0]))
        self.eu.set_value(float(v[1]) if float(v[1]) >= 50.0 else None)
        self.eu.setPlaceholderText("E máx.")
        self.nbe.set_value(int(v[2]))
        a = sc.value(doc, "NBANGL") or list(sc.default("NBANGL"))
        self.nth.set_value(int(a[0]))
        self.nph.blockSignals(True)
        self.nph.setValue(max(1, int(a[1])))
        self.nph.blockSignals(False)

    def _on_nbe(self) -> None:
        a, b = self.el.value(), self.eu.value()
        if a is None:
            return
        b = 0.0 if b is None else b
        n = self.nbe.value()
        self.edit("Energía de las partículas emergentes", lambda doc: sc.set_value(doc, "NBE", [a, b, n]))

    def _on_angles(self) -> None:
        n1, n2 = self.nth.value(), self.nph.value()
        self.edit("Ángulos de las partículas emergentes", lambda doc: sc.set_value(doc, "NBANGL", [n1, n2]))

    def summary(self) -> str:
        doc = self.doc
        if doc is None:
            return ""
        v = sc.value(doc, "NBE") or list(sc.default("NBE"))
        a = sc.value(doc, "NBANGL") or list(sc.default("NBANGL"))
        hi = format_energy(float(v[1])) if float(v[1]) >= 50 else "E máx."
        return f"{abs(int(v[2]))} intervalos hasta {hi} · θ {abs(int(a[0]))}, φ {int(a[1])}"
