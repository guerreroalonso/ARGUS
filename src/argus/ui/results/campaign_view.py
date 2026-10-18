"""Campañas: una medida de cada variante frente al parámetro que cambia, con su incertidumbre y un ajuste.

Se elige el resultado (una curva: dosis en profundidad, un espectro…), la serie
y la medida (las del panel «Medidas»: alcance R80, energía media, FWHM…). Con
dos parámetros, una línea por cada valor del segundo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...results import metrics as mt
from ...results.data import Curves
from ...results.display import DisplayOptions, resolve_units, show_curves
from ...results.fit import MODELS, fit
from .canvas import PlotCanvas
from .style import BAND_ALPHA, CATEGORICAL, INK, INK_2


@dataclass
class CampaignData:
    """Lo que necesita la vista: las variantes (nombre y valores) y cómo leer un resultado de cada una."""

    labels: list[str]
    values: list[dict]
    parameters: list[dict]  # del índice de ARGUS: name, kind, unit…
    outputs: list[tuple[str, str]]  # (clave, nombre) de los resultados que son curvas
    load: object  # load(clave, i) -> Curves (o lanza ValueError)
    primary: int | None = None  # partícula primaria (para elegir las medidas)


class CampaignView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setContentsMargins(6, 4, 6, 0)
        row.addWidget(QLabel("Resultado:"))
        self.output = QComboBox()
        self.output.currentIndexChanged.connect(self._output_changed)
        row.addWidget(self.output, 2)
        row.addWidget(QLabel("Serie:"))
        self.series = QComboBox()
        self.series.currentIndexChanged.connect(self.recompute)
        row.addWidget(self.series, 1)
        row.addWidget(QLabel("Medida:"))
        self.metric_box = QComboBox()
        self.metric_box.currentIndexChanged.connect(self.redraw)
        row.addWidget(self.metric_box, 2)
        row.addWidget(QLabel("Ajuste:"))
        self.model = QComboBox()
        self.model.addItem("ninguno", "")
        for k, t in MODELS.items():
            self.model.addItem(t, k)
        self.model.currentIndexChanged.connect(self.redraw)
        row.addWidget(self.model, 1)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Vertical)
        self.plot = PlotCanvas()
        split.addWidget(self.plot)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(6, 0, 6, 4)
        self.fit_text = QLabel("")
        self.fit_text.setWordWrap(True)
        self.fit_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.fit_text.setStyleSheet(f"color: {INK_2};")
        bl.addWidget(self.fit_text)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        bl.addWidget(self.table, 1)
        split.addWidget(bottom)
        split.setSizes([520, 220])
        lay.addWidget(split, 1)
        self.data: CampaignData | None = None
        self.opts = DisplayOptions()
        self.results: list[dict] = []  # por variante: {key: Metric}
        self.units = ("", "")
        self.title = ""

    # -- datos -------------------------------------------------------------------------------------------
    def set_data(self, data: CampaignData, title: str, opts: DisplayOptions | None = None, context=None) -> None:
        self.data = data
        self.title = title
        self.opts = opts or DisplayOptions()
        cur = self.output.currentData()
        self.output.blockSignals(True)
        self.output.clear()
        for key, label in data.outputs:
            self.output.addItem(label, key)
        k = self.output.findData(cur)
        self.output.setCurrentIndex(k if k >= 0 else self._default_output())
        self.output.blockSignals(False)
        self._output_changed()

    def set_options(self, opts: DisplayOptions) -> None:
        self.opts = opts
        self.recompute()

    def _default_output(self) -> int:
        for pref in ("depth-dose", "spc-enddet", "spc-impdet", "z-dose", "energy-up"):
            for i in range(self.output.count()):
                if str(self.output.itemData(i)).startswith(pref):
                    return i
        return 0

    def _curves(self, i: int) -> Curves | None:
        key = self.output.currentData()
        if key is None or self.data is None:
            return None
        try:
            c = self.data.load(key, i)
        except (OSError, ValueError):
            return None
        return c if isinstance(c, Curves) else None

    def _output_changed(self, *_args) -> None:
        c = self._curves(0)
        cur = self.series.currentText()
        self.series.blockSignals(True)
        self.series.clear()
        if c is not None:
            for s in c.series:
                if s.unit is None or s.unit == c.y_unit:
                    self.series.addItem(s.name, s.name)
            k = self.series.findText(cur)
            if k < 0:
                nonempty = [s.name for s in c.nonempty() if s.unit is None or s.unit == c.y_unit]
                k = self.series.findText(nonempty[0]) if nonempty else 0
            self.series.setCurrentIndex(max(k, 0))
        self.series.blockSignals(False)
        self.recompute()

    def recompute(self, *_args) -> None:
        self.results = []
        d = self.data
        if d is None:
            self.redraw()
            return
        name = self.series.currentData()
        preset = None
        opts = None  # las unidades de la primera variante, para todas
        for i in range(len(d.labels)):
            c = self._curves(i)
            if c is None:
                self.results.append({})
                continue
            if opts is None:
                opts = resolve_units(c, self.opts)
            sc = show_curves(c, opts, {name} if name else None)
            s = next((x for x in sc.series if x.name == name), sc.series[0] if sc.series else None)
            if s is None:
                self.results.append({})
                continue
            if preset is None:
                preset = mt.guess_preset(c.kind, c.x_name, d.primary)
                self.units = (sc.x_unit, sc.y_unit)
            # Las energías exactas de un espectro (NaN en la curva) cuentan aparte, con su probabilidad.
            xs, ys, es, edges, lines = mt.usable(s.x, s.y, s.err, s.edges, [(ln.x, ln.p * s.scale) for ln in s.lines])
            ms = mt.compute(preset, xs, ys, es, edges, lines=lines)
            self.results.append({m.key: m for m in ms})
        cur = self.metric_box.currentData()
        self.metric_box.blockSignals(True)
        self.metric_box.clear()
        first = next((r for r in self.results if r), {})
        for key, m in first.items():
            unit = mt.unit_of(m, *self.units)
            self.metric_box.addItem(m.name + (f" ({unit})" if unit else ""), key)
        k = self.metric_box.findData(cur)
        self.metric_box.setCurrentIndex(max(k, 0))
        self.metric_box.blockSignals(False)
        self.redraw()

    # -- dibujo -----------------------------------------------------------------------------------------------
    def _x_values(self) -> tuple[list[float], str, list[str], str]:
        """Posición de cada variante en el eje x (el primer parámetro) y el grupo (el segundo)."""
        d = self.data
        params = d.parameters
        p1 = params[0] if params else {"name": "variante", "kind": ""}
        xs, groups = [], []
        cats: list = []
        for i, vals in enumerate(d.values):
            v = vals.get(p1.get("name"), i)
            if p1.get("kind") == "material":
                if v not in cats:
                    cats.append(v)
                xs.append(float(cats.index(v)))
            else:
                try:
                    xs.append(float(v))
                except (TypeError, ValueError):
                    xs.append(float(i))
            if len(params) > 1:
                groups.append(str(vals.get(params[1].get("name"), "")))
            else:
                groups.append("")
        unit = p1.get("unit") or ""
        xlabel = p1.get("name", "variante") + (f" ({unit})" if unit else "")
        return xs, xlabel, groups, "material" if p1.get("kind") == "material" else ""

    def redraw(self, *_args) -> None:
        pc = self.plot
        pc.clear()
        d = self.data
        key = self.metric_box.currentData()
        self.fit_text.setText("")
        if d is None or key is None:
            pc.draw()
            self._fill_table(None)
            return
        xs, xlabel, groups, kind = self._x_values()
        ax = pc.figure.add_subplot()
        ys = [r.get(key).value if r.get(key) is not None and r.get(key).value is not None else math.nan
              for r in self.results]
        es = [r.get(key).err if r.get(key) is not None and r.get(key).err else 0.0 for r in self.results]
        m0 = next((r[key] for r in self.results if key in r), None)
        unit = mt.unit_of(m0, *self.units) if m0 is not None else ""
        order = list(dict.fromkeys(groups))
        fits = []
        for gi, g in enumerate(order):
            idx = [i for i, gg in enumerate(groups) if gg == g]
            x = np.array([xs[i] for i in idx])
            y = np.array([ys[i] for i in idx], dtype=np.float64)
            e = np.array([es[i] for i in idx], dtype=np.float64)
            srt = np.argsort(x)
            x, y, e = x[srt], y[srt], e[srt]
            color = INK if len(order) == 1 else CATEGORICAL[gi % len(CATEGORICAL)]
            label = g if g else None
            ax.errorbar(x, y, yerr=np.where(e > 0, e, np.nan), fmt="o", ms=5, color=color, ecolor=color,
                        elinewidth=1, capsize=3, label=label)
            model = self.model.currentData()
            if model and kind != "material":
                try:
                    r = fit(x, y, e if np.all(e > 0) else None, model)
                except (ValueError, np.linalg.LinAlgError) as exc:
                    fits.append(f"{g + ': ' if g else ''}{exc}")
                else:
                    xx = np.linspace(float(np.nanmin(x)), float(np.nanmax(x)), 200)
                    ax.plot(xx, r.predict(xx), color=color, lw=1.2, alpha=0.8)
                    fits.append(f"{g + ': ' if g else ''}{r.text()}")
            elif len(x) > 1:
                ax.plot(x, y, color=color, lw=1.0, alpha=0.5)
            if np.any(e > 0):
                ax.fill_between(x, y - e, y + e, color=color, alpha=BAND_ALPHA, lw=0)
        if kind == "material":
            cats = list(dict.fromkeys(v.get(d.parameters[0]["name"]) for v in d.values))
            ax.set_xticks(range(len(cats)))
            ax.set_xticklabels([str(c) for c in cats], rotation=20)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(self.metric_box.currentText().split(" (")[0] + (f" ({unit})" if unit else ""))
        ax.set_title(f"{self.metric_box.currentText().split(' (')[0]} · {self.output.currentText()}", loc="left")
        if len(order) > 1:
            ax.legend(title=d.parameters[1].get("name") if len(d.parameters) > 1 else None, loc="best")
        pc.draw()
        self.fit_text.setText("\n".join(fits))
        self._fill_table(key)

    def _fill_table(self, key) -> None:
        t = self.table
        d = self.data
        t.clear()
        if d is None or key is None:
            t.setRowCount(0)
            t.setColumnCount(0)
            return
        pnames = [p.get("name", "") for p in d.parameters]
        cols = ["Variante"] + pnames + [self.metric_box.currentText(), "σ"]
        t.setColumnCount(len(cols))
        t.setRowCount(len(d.labels))
        t.setHorizontalHeaderLabels(cols)
        for i, lab in enumerate(d.labels):
            m = self.results[i].get(key) if i < len(self.results) else None
            row = [lab] + [str(d.values[i].get(n, "")) for n in pnames]
            row += [f"{m.value:.6g}" if m is not None and m.value is not None else "—",
                    f"{m.err:.2g}" if m is not None and m.err else ""]
            for j, v in enumerate(row):
                it = QTableWidgetItem(v)
                if j >= 1:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(i, j, it)
        t.resizeColumnsToContents()

    def table_rows(self) -> tuple[list[str], list[np.ndarray]]:
        d = self.data
        key = self.metric_box.currentData()
        if d is None or key is None:
            return [], []
        xs, xlabel, _g, _k = self._x_values()
        ys = [r.get(key).value if r.get(key) is not None and r.get(key).value is not None else math.nan
              for r in self.results]
        es = [r.get(key).err if r.get(key) is not None and r.get(key).err else math.nan for r in self.results]
        return [xlabel, self.metric_box.currentText(), "sigma"], [np.array(xs), np.array(ys), np.array(es)]


__all__ = ["CampaignData", "CampaignView"]
