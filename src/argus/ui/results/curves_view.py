"""Vista de curvas: dosis en profundidad, perfiles, espectros, distribuciones angulares…

Dibuja las series visibles de un resultado (escalones si son histogramas,
barras si son líneas discretas) con su banda de incertidumbre, y encima las
mismas series de otras carpetas o datos medidos para comparar. Las series con
otra unidad (la energía media por ángulo, el DVH diferencial) van en un panel
aparte debajo, con el mismo eje x: nunca dos escalas en el mismo panel.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...results.compare import difference, paired, ratio, zscores
from ...results.data import Curves
from ...results.display import DisplayOptions, ShownCurves, ShownSeries, line_text, resolve_units, show_curves
from ..flow_layout import FlowLayout, group
from .canvas import PlotCanvas
from .style import BAND_ALPHA, CATEGORICAL, INK_2, MUTED, dataset_color, particle_color

COMPARE_MODES = {"overlay": "Superponer", "diff": "Diferencia", "ratio": "Cociente", "z": "Diferencia / σ (z)"}


@dataclass
class Overlay:
    """Otra curva para comparar: la misma salida de otra carpeta, o datos medidos."""

    label: str
    curves: Curves
    measured: bool = False
    color: str | None = None  # si no, el del orden en la comparación
    resolution: bool = False  # el espectro que se ve, con la resolución del detector (panel «Espectro»)


def series_color(c: Curves, name: str, particle: int | None) -> str:
    """Color de una serie: el de su partícula; si las series no son de partículas (cuerpos, tejidos…), el de su
    posición en la curva (siempre el mismo para la misma serie, se vean las que se vean)."""
    if particle is not None or len([x for x in c.series if x.unit is None or x.unit == c.y_unit]) < 2:
        return particle_color(particle)
    names = [x.name for x in c.series if x.particle is None and (x.unit is None or x.unit == c.y_unit)]
    i = names.index(name) if name in names else 0
    return CATEGORICAL[i % len(CATEGORICAL)] if i < len(CATEGORICAL) else MUTED


def fmt(v: float | None, err: float | None = None, digits: int = 4) -> str:
    if v is None or not np.isfinite(v):
        return "—"
    s = f"{v:.{digits}g}"
    if err is not None and np.isfinite(err) and err > 0:
        s += f" ± {err:.2g}"
    return s


class CurvesView(QWidget):
    seriesChanged = Signal()
    activeCurveChanged = Signal(object)  # ShownSeries de la curva principal que se analiza

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = FlowLayout()  # sigue en otra línea si no cabe (pantallas al 125 o 150 %)
        top.setContentsMargins(6, 4, 6, 0)
        # Lo que hay que saber de la curva (meta['note'] del resultado: «Incertidumbre subestimada»…).
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {MUTED};")
        top.addWidget(self.note, 1)
        self.log_x = QCheckBox("x log")
        self.log_y = QCheckBox("y log")
        for w in (self.log_x, self.log_y):
            w.toggled.connect(self.redraw)
        top.addWidget(group(self.log_x, self.log_y))
        self.mode = QComboBox()
        for k, t in COMPARE_MODES.items():
            self.mode.addItem(t, k)
        self.mode.currentIndexChanged.connect(self.redraw)
        top.addWidget(group(QLabel("Comparar:"), self.mode))
        self.table_toggle = QCheckBox("Tabla")
        self.table_toggle.setToolTip("Ver los números en una tabla")
        self.table_toggle.toggled.connect(self._toggle_table)
        top.addWidget(self.table_toggle)
        lay.addLayout(top)
        # Las series (una casilla por partícula), en su propia fila para que quepan los nombres.
        self.series_row = QWidget()
        self.series_box = FlowLayout(self.series_row, spacing=12)
        self.series_box.setContentsMargins(6, 0, 6, 0)
        self.series_row.setVisible(False)
        lay.addWidget(self.series_row)
        self.stack = QStackedWidget()
        self.plot = PlotCanvas()
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stack.addWidget(self.plot)
        self.stack.addWidget(self.table)
        lay.addWidget(self.stack, 1)
        self.plot.hovered.connect(self._on_hover)
        self.plot.left.connect(self._clear_cross)
        self.curves: Curves | None = None
        self.title = ""
        self.primary_label = ""  # nombre de la carpeta principal en las comparaciones
        self.primary_color: str | None = None  # color de la curva principal al comparar (variantes de una campaña)
        self.opts = DisplayOptions()
        self.overlays: list[Overlay] = []
        self._curve_note = ""
        self.visible: set[str] = set()
        self._auto_hidden: set[str] = set()  # series ocultas por estar vacías (no porque se hayan quitado)
        self._empty: set[str] = set()
        self.shown: ShownCurves | None = None
        self.shown_opts: DisplayOptions = self.opts  # las opciones con las unidades ya fijadas
        self.shown_overlays: list[tuple[Overlay, ShownCurves]] = []
        self._checks: dict[str, QCheckBox] = {}
        self._cross: list = []
        self.axes_main = None
        self.axes_extra = None
        self.axes_cmp = None

    # -- datos -------------------------------------------------------------------
    def set_curves(self, curves: Curves | None, title: str = "", *, keep_series: bool = False) -> None:
        old_names = [s.name for s in self.curves.series] if self.curves is not None else []
        self.curves = curves
        self.title = title or (curves.title if curves is not None else "")
        self._curve_note = str(curves.meta.get("note", "")) if curves is not None else ""
        self._set_note("")
        names = [s.name for s in curves.series] if curves is not None else []
        nonempty = {s.name for s in curves.nonempty()} if curves is not None else set()
        empty = set(names) - nonempty
        if not keep_series or names != old_names:
            self.visible = nonempty or set(names)
            self._auto_hidden = set(names) - self.visible
            self.log_x.blockSignals(True)
            self.log_x.setChecked(bool(curves is not None and curves.log_x))
            self.log_x.blockSignals(False)
            self._empty = empty
            self._rebuild_checks()
        elif empty != self._empty:
            # Siguiendo la simulación: una serie que estaba a cero ya tiene datos (se enseña, si no se había quitado
            # a mano), o al revés.
            woke = self._auto_hidden & nonempty
            self.visible |= woke
            self._auto_hidden -= woke
            self._empty = empty
            self._rebuild_checks()
        self.redraw()

    def _set_note(self, shown_note: str) -> None:
        """Lo que hay que saber de la curva (de su fichero) y de cómo se muestra (una normalización que no se ha
        podido aplicar…)."""
        note = " ".join(t for t in (self._curve_note, shown_note) if t)
        self.note.setText(note)
        self.note.setToolTip(note)

    def set_overlays(self, overlays: list[Overlay]) -> None:
        self.overlays = overlays
        self.redraw()

    def set_options(self, opts: DisplayOptions) -> None:
        self.opts = opts
        self.redraw()

    def _rebuild_checks(self) -> None:
        while self.series_box.count():
            it = self.series_box.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self._checks = {}
        c = self.curves
        self.series_row.setVisible(False)
        if c is None or len([s for s in c.series if s.weight_of is None and s.unit is None]) < 2:
            return
        self.series_row.setVisible(True)
        for s in c.series:
            if s.unit is not None and s.unit != c.y_unit:
                continue
            cb = QCheckBox(s.name)
            color = series_color(c, s.name, s.particle)
            cb.setStyleSheet(f"QCheckBox {{ color: {INK_2}; }} QCheckBox::indicator:checked {{ "
                             f"background: {color}; border: 1px solid {color}; }}")
            cb.setChecked(s.name in self.visible)
            if s.empty:
                cb.setToolTip("Todo ceros: penhmain no ha registrado ninguna")
                cb.setStyleSheet(f"color: {MUTED};")
            cb.toggled.connect(lambda on, n=s.name: self._toggle_series(n, on))
            self._checks[s.name] = cb
            self.series_box.addWidget(cb)

    def _toggle_series(self, name: str, on: bool) -> None:
        self._auto_hidden.discard(name)
        if on:
            self.visible.add(name)
        else:
            self.visible.discard(name)
        self.redraw()
        self.seriesChanged.emit()

    def active_series(self) -> ShownSeries | None:
        """La serie que se analiza (medidas, comparación): la primera visible de la curva principal."""
        if self.shown is None or not self.shown.series:
            return None
        return self.shown.series[0]

    # -- dibujo ------------------------------------------------------------------------
    def redraw(self) -> None:
        pc = self.plot
        pc.clear()
        self._cross = []
        self.axes_main = self.axes_extra = self.axes_cmp = None
        c = self.curves
        if c is None:
            pc.draw()
            return
        extra_names = {s.name for s in c.series if s.unit is not None and s.unit != c.y_unit}
        vis = set(self.visible) | extra_names
        # Las unidades automáticas, las de la curva principal: las comparadas van en la misma escala.
        opts = resolve_units(c, self.opts)
        self.shown_opts = opts
        shown = show_curves(c, opts, vis)
        self.shown = shown
        self.shown_overlays = [(o, show_curves(o.curves, opts, None if o.measured else vis))
                               for o in self.overlays]
        notes = []
        for n in [shown.note] + [so.note for _o, so in self.shown_overlays]:
            if n and n not in notes:
                notes.append(n)
        self._set_note(" ".join(notes))
        mode = self.mode.currentData()
        comparing = bool(self.overlays)
        n_panels = 1 + (1 if shown.extra else 0) + (1 if comparing and mode != "overlay" else 0)
        ratios = [3] + [1.4] * (n_panels - 1)
        axes = pc.figure.subplots(n_panels, 1, sharex=True, squeeze=False,
                                  gridspec_kw={"height_ratios": ratios})[:, 0]
        ax = axes[0]
        self.axes_main = ax
        k = self.opts.sigma
        single = len(shown.series) == 1
        for s in shown.series:
            color = (self.primary_color or dataset_color(0)) if comparing and single else \
                series_color(c, s.name, s.particle)
            label = (self.primary_label or s.label) if comparing and single else s.label
            self._draw_series(ax, s, color, k, c.histogram, c.discrete, label=label)
        for j, (o, so) in enumerate(self.shown_overlays, start=1):
            color = o.color or dataset_color(j)
            for s in so.series:
                if not o.measured and s.name not in {x.name for x in shown.series}:
                    continue
                lab = o.label if len(so.series) == 1 else f"{o.label} · {s.label}"
                if o.measured:
                    ax.errorbar(s.x, s.y, yerr=None if s.err is None or k <= 0 else k * s.err, fmt="o", ms=4,
                                color=color, ecolor=color, elinewidth=1, capsize=2, label=lab)
                else:
                    self._draw_series(ax, s, color, k, o.curves.histogram, o.curves.discrete, label=lab, dashed=True)
        ax.set_ylabel(shown.y_label)
        if shown.extra:
            ax2 = axes[1]
            self.axes_extra = ax2
            for s in shown.extra:
                self._draw_series(ax2, s, particle_color(s.particle), k, c.histogram, False, label=s.label)
            ax2.set_ylabel(shown.extra_label)
        if comparing and mode != "overlay":
            axc = axes[-1]
            self.axes_cmp = axc
            self._draw_comparison(axc, mode, k)
        axes[-1].set_xlabel(shown.x_label)
        positive = all(np.all(s.x > 0) for s in shown.series + shown.extra)
        if self.log_x.isChecked() and positive:
            for a in axes:
                a.set_xscale("log")
        if self.log_y.isChecked():
            ax.set_yscale("log")
        n_leg = len(shown.series) + sum(len(so.series) for _, so in self.shown_overlays)
        if n_leg >= 2:
            ax.legend(loc="best")
        ax.set_title(self.title, loc="left")
        ax.margins(x=0.01)
        for a in axes:
            line = a.axvline(np.nan, color=MUTED, lw=0.8, zorder=10)
            line.set_visible(False)
            self._cross.append(line)
        pc.draw()
        self._fill_table()
        self.activeCurveChanged.emit(self.active_series())

    def _draw_series(self, ax, s: ShownSeries, color: str, k: float, histogram: bool, discrete: bool, *,
                     label: str, dashed: bool = False) -> None:
        ls = (0, (5, 3)) if dashed else "-"
        y = np.asarray(s.y, dtype=np.float64)
        if discrete:
            ax.vlines(s.x, 0, y, color=color, lw=2.0, label=label, linestyles=ls)
            ax.plot(s.x, y, "o", color=color, ms=5)
            if s.err is not None and k > 0:
                ax.errorbar(s.x, y, yerr=k * s.err, fmt="none", ecolor=color, elinewidth=1, capsize=3)
            return
        self._draw_lines(ax, s, color, ls)
        if histogram and s.edges is not None and len(s.edges) == len(y) + 1:
            ax.stairs(y, s.edges, color=color, lw=1.5, label=label, linestyle=ls, baseline=None)
            if s.err is not None and k > 0:
                ax.stairs(y + k * s.err, s.edges, baseline=y - k * s.err, fill=True, color=color, alpha=BAND_ALPHA,
                          lw=0)
            return
        if len(y) == 1:  # un punto (p. ej. una curva de un solo intervalo de anchura desconocida): con su marca
            ax.plot(s.x, y, "o", color=color, label=label, ms=5)
            if s.err is not None and k > 0:
                ax.errorbar(s.x, y, yerr=k * np.asarray(s.err), fmt="none", ecolor=color, elinewidth=1, capsize=3)
            return
        ax.plot(s.x, y, color=color, label=label, linestyle=ls)
        if s.err is not None and k > 0:
            ax.fill_between(s.x, y - k * s.err, y + k * s.err, color=color, alpha=BAND_ALPHA, lw=0)

    def _draw_lines(self, ax, s: ShownSeries, color: str, ls) -> None:
        """Las energías exactas de un espectro (intervalos de anchura cero de psource.dat): una línea vertical con su
        probabilidad escrita, no un pico de altura arbitraria (su densidad sería infinita)."""
        x_unit = self.shown.x_unit if self.shown is not None else ""
        for line in s.lines:
            ax.axvline(line.x, color=color, lw=2.0, linestyle=ls, zorder=3)
            k = sum(1 for t in ax.texts if getattr(t, "_argus_line", False))
            t = ax.annotate(line_text(line, s.scale, self.shown_opts, x_unit), xy=(line.x, 1.0),
                            xycoords=ax.get_xaxis_transform(), xytext=(-4, -4 - 12 * k), textcoords="offset points",
                            ha="right", va="top", fontsize=8, color=color)
            t._argus_line = True

    def _draw_comparison(self, ax, mode: str, k: float) -> None:
        ref = self.active_series()
        if ref is None:
            return
        ylabel = {"diff": "Diferencia", "ratio": "Cociente", "z": "z"}.get(mode, "")
        for j, (o, so) in enumerate(self.shown_overlays, start=1):
            other = next((s for s in so.series if s.name == ref.name), so.series[0] if so.series else None)
            if other is None:
                continue
            # Con datos medidos, en las posiciones de las medidas (la simulación interpolada en ellas).
            x, a, ea, b, eb = paired(ref.x, ref.y, ref.err, other.x, other.y, other.err, at_b=o.measured)
            if mode == "diff":
                y, e = difference(b, eb, a, ea)
                ylabel = "Diferencia"
            elif mode == "ratio":
                y, e = ratio(b, eb, a, ea)
                ylabel = "Cociente"
            else:
                y, e = zscores(b, eb, a, ea), None
                ylabel = "z"
                for lv in (-2, 2):
                    ax.axhline(lv, color=MUTED, lw=0.8)
            color = o.color or dataset_color(j)
            if o.measured:
                ax.errorbar(x, y, yerr=None if e is None or k <= 0 else k * e, fmt="o", ms=4, color=color,
                            ecolor=color, elinewidth=1, capsize=2, label=o.label)
                continue
            ax.plot(x, y, color=color, label=o.label, drawstyle="steps-mid")
            if e is not None and k > 0:
                ax.fill_between(x, y - k * e, y + k * e, color=color, alpha=BAND_ALPHA, lw=0, step="mid")
        ax.axhline(1.0 if mode == "ratio" else 0.0, color=INK_2, lw=0.8)
        ax.set_ylabel(f"{ylabel} (frente a {self.title})" if len(self.title) < 30 else ylabel)

    # -- lectura con el ratón -------------------------------------------------------------
    def _on_hover(self, axes, x: float, y: float) -> None:
        if self.shown is None:
            return
        for line in self._cross:
            line.set_xdata([x, x])
            line.set_visible(True)
        parts = [f"{self.shown.x_label.split(' (')[0]} = {x:.4g}"]
        for s in self.shown.series + self.shown.extra:
            if not len(s.x):
                continue
            i = int(np.argmin(np.abs(s.x - x)))
            parts.append(f"{s.label}: {fmt(s.y[i], None if s.err is None else s.err[i])}")
        for o, so in self.shown_overlays:
            for s in so.series[:1]:
                if len(s.x):
                    i = int(np.argmin(np.abs(s.x - x)))
                    parts.append(f"{o.label}: {fmt(s.y[i], None if s.err is None else s.err[i])}")
        self.plot.readout.setText("   ".join(parts[:6]))
        self.plot.canvas.draw_idle()

    def _clear_cross(self) -> None:
        for line in self._cross:
            line.set_visible(False)
        self.plot.readout.setText("")
        self.plot.canvas.draw_idle()

    # -- tabla ---------------------------------------------------------------------------
    def _toggle_table(self, on: bool) -> None:
        self.stack.setCurrentWidget(self.table if on else self.plot)

    def _fill_table(self) -> None:
        t = self.table
        t.clear()
        cols, data = self._columns("σ", overlays=False)
        if not cols:
            t.setRowCount(0)
            t.setColumnCount(0)
            return
        t.setColumnCount(len(cols))
        t.setRowCount(len(data[0]))
        t.setHorizontalHeaderLabels(cols)
        for j, col in enumerate(data):
            for i, v in enumerate(col):
                it = QTableWidgetItem(f"{v:.6g}" if np.isfinite(v) else "—")
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(i, j, it)
        t.resizeColumnsToContents()

    def _columns(self, sigma: str, overlays: bool) -> tuple[list[str], list[np.ndarray]]:
        """Columnas de lo que se ve, con la unidad de cada una (la del rótulo del eje) y, al comparar, de qué carpeta
        o variante es cada curva (como en la leyenda)."""
        if self.shown is None:
            return [], []
        series = self.shown.series + self.shown.extra
        if not series:
            return [], []
        cols = [self.shown.x_label]
        data = [series[0].x]
        single = len(self.shown.series) == 1
        mode = self.shown_opts.norm.mode
        who = "desintegraciones" if self.shown_opts.per_decay else "partículas"
        for k, s in enumerate(series):
            name = s.label
            if self.shown_overlays and single and k == 0 and self.primary_label:
                name = f"{self.primary_label} · {s.label}"  # como en la leyenda: de qué carpeta o variante es
            unit = f" ({s.unit})" if s.unit else ""
            cols.append(f"{name}{unit}")
            data.append(s.y)
            if s.err is not None:
                cols.append(f"{sigma} {name}{unit}")
                data.append(s.err)
            if s.lines:
                # Las energías exactas (intervalos de anchura cero): su probabilidad, no una densidad.
                f = s.scale if mode in ("count", "activity", "area") else 1.0
                what = who if mode in ("count", "activity") else "probabilidad"
                col = np.full(len(s.x), np.nan)
                for ln in s.lines:
                    col[ln.index] = ln.p * f
                cols.append(f"{name} · energía exacta ({what})")
                data.append(col)
        if overlays:
            for o, so in self.shown_overlays:
                for s in so.series:
                    unit = f" ({s.unit})" if s.unit else ""
                    cols.append(f"{o.label} · {so.x_label}")
                    data.append(s.x)
                    cols.append(f"{o.label} · {s.label}{unit}")
                    data.append(s.y)
        return cols, data

    def table_rows(self) -> tuple[list[str], list[np.ndarray]]:
        """Columnas (con sus nombres y unidades) de lo que se ve, para exportar a CSV."""
        return self._columns("sigma", overlays=True)


__all__ = ["COMPARE_MODES", "CurvesView", "Overlay", "fmt"]
