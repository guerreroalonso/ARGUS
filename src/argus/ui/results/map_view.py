"""Vista de mapas: planos de un mapa 3D (caja o TAC), el mapa R-Z de un cilindro, la dosis radial de una esfera y
la distribución angular (θ, φ).

Los ejes son los de los cortes de ARGUS: plano z → (x, y); plano y → (x, z);
plano x → (y, z); en un cilindro, r en horizontal (reflejado, de −R a R) y z
en vertical. Al pulsar en el mapa se saca un perfil por ese punto (horizontal o
vertical) que se puede medir en el panel «Medidas».
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...results.data import Curves, Grid, Series
from ...results.display import DisplayOptions, grid_label, grid_scale
from ...results.profiles import AXES, axis_profile, layer, plane
from ..flow_layout import FlowLayout, group
from .canvas import PlotCanvas
from .curves_view import fmt
from .style import BAND_ALPHA, CMAP_TITLES, INK, INK_2, colormap

REL = "Incertidumbre relativa (%)"


def _levels(text: str) -> list[float]:
    out = []
    for t in text.replace(";", " ").replace(",", " ").split():
        try:
            v = float(t)
        except ValueError:
            continue
        if 0 < v < 100.0001:
            out.append(v)
    return sorted(set(out))


class MapView(QWidget):
    profileChanged = Signal(object)  # Curves del perfil actual (o None)
    pointPicked = Signal(object)  # punto (x, y, z) del mapa en el sistema del mapa

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # Las filas de controles siguen en otra línea si no caben (pantallas al 125 o 150 %).
        row = FlowLayout()
        row.setContentsMargins(6, 4, 6, 0)
        self.layer = QComboBox()
        self.layer.currentIndexChanged.connect(self.redraw)
        row.addWidget(group(QLabel("Capa:"), self.layer))
        self.axis_label = QLabel("Plano:")
        self.axis = QComboBox()
        for a in ("z", "y", "x"):
            self.axis.addItem(f"{a} constante", a)
        self.axis.currentIndexChanged.connect(self._axis_changed)
        row.addWidget(group(self.axis_label, self.axis))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(140)
        self.slider.valueChanged.connect(self.redraw)
        row.addWidget(self.slider, 1)
        self.pos_label = QLabel("")
        self.pos_label.setMinimumWidth(90)
        row.addWidget(self.pos_label)
        self.filler = QWidget()  # ocupa el sitio del deslizador cuando no hay planos que elegir
        self.filler.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(self.filler, 1)
        lay.addLayout(row)
        row2 = FlowLayout()
        row2.setContentsMargins(6, 0, 6, 0)
        self.cmap = QComboBox()
        for k, t in CMAP_TITLES.items():
            self.cmap.addItem(t, k)
        self.cmap.currentIndexChanged.connect(self.redraw)
        row2.addWidget(group(QLabel("Colores:"), self.cmap))
        self.log = QCheckBox("Escala logarítmica")
        self.log.toggled.connect(self.redraw)
        row2.addWidget(self.log)
        self.common = QCheckBox("Misma escala en todos los planos")
        self.common.setChecked(True)
        self.common.toggled.connect(self.redraw)
        row2.addWidget(self.common)
        self.iso = QCheckBox("Isolíneas (%):")
        self.iso.setChecked(True)
        self.iso.toggled.connect(self.redraw)
        self.iso_levels = QLineEdit("20 50 80 95")
        self.iso_levels.setMaximumWidth(120)
        self.iso_levels.setToolTip("Porcentajes del máximo del mapa, separados por espacios")
        self.iso_levels.editingFinished.connect(self.redraw)
        row2.addWidget(group(self.iso, self.iso_levels))
        self.prof_dir = QComboBox()
        self.prof_dir.addItem("horizontal", "h")
        self.prof_dir.addItem("vertical", "v")
        self.prof_dir.addItem("sin perfil", "")
        self.prof_dir.currentIndexChanged.connect(self.redraw)
        row2.addWidget(group(QLabel("Perfil:"), self.prof_dir))
        self.mirror = QCheckBox("Reflejar r")
        self.mirror.setChecked(True)
        self.mirror.toggled.connect(self.redraw)
        row2.addWidget(self.mirror)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.plot = PlotCanvas()
        lay.addWidget(self.plot, 1)
        self.plot.clicked.connect(self._on_click)
        self.plot.hovered.connect(self._on_hover)
        self.grid: Grid | None = None
        self.title = ""
        self.opts = DisplayOptions()
        self.pick: tuple[float, float] | None = None  # punto del perfil en (u, v) del mapa mostrado
        self.profile: Curves | None = None
        self.ax_map = None
        self.ax_prof = None
        self._shown = None  # (u_edges, v_edges, values, err, factor, unit, label)

    # -- datos --------------------------------------------------------------------------
    def set_grid(self, grid: Grid | None, title: str = "") -> None:
        same = (self.grid is not None and grid is not None and self.grid.kind == grid.kind
                and self.grid.shape == grid.shape)
        self.grid = grid
        self.title = title or (grid.title if grid is not None else "")
        if not same:
            self.pick = None
            self._setup_controls()
        self.redraw()

    def set_options(self, opts: DisplayOptions) -> None:
        self.opts = opts
        self.redraw()

    def _setup_controls(self) -> None:
        g = self.grid
        self.layer.blockSignals(True)
        self.layer.clear()
        if g is not None:
            if g.geometry == "angles":
                for name in g.meta.get("layers", []):
                    self.layer.addItem(name, name)
                self.layer.setCurrentIndex(max(0, self.layer.findData(g.meta.get("main"))))
            else:
                self.layer.addItem(g.quantity, None)
                for name in g.extra:
                    self.layer.addItem(name, name)
            self.layer.addItem(REL, REL)
        self.layer.blockSignals(False)
        box = g is not None and g.geometry in ("box", "ct")
        for w in (self.axis_label, self.axis, self.slider, self.pos_label, self.common):
            w.setVisible(box)
        self.filler.setVisible(not box)
        self.mirror.setVisible(g is not None and g.geometry == "cylinder")
        self._axis_changed(redraw=False)

    def _axis_changed(self, *_args, redraw: bool = True) -> None:
        g = self.grid
        if g is None or g.geometry not in ("box", "ct"):
            return
        k = AXES[self.axis.currentData()]
        n = g.shape[k]
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(n - 1, 0))
        # Al empezar, el plano con más dosis.
        v = g.value
        other = tuple(i for i in range(3) if i != k)
        per_plane = np.nansum(np.abs(v), axis=other) if v.size else np.zeros(n)
        self.slider.setValue(int(np.argmax(per_plane)) if per_plane.size else 0)
        self.slider.blockSignals(False)
        self.pick = None
        if redraw:
            self.redraw()

    def current_layer(self) -> tuple[np.ndarray, np.ndarray, str, str]:
        g = self.grid
        data = self.layer.currentData()
        if data == REL:
            v, e, _, _ = layer(g, None if g.geometry != "angles" else g.meta.get("main"))
            with np.errstate(divide="ignore", invalid="ignore"):
                rel = np.where(np.abs(v) > 0, 100.0 * e / np.abs(v), np.nan)
            return rel, np.zeros_like(rel), REL, "%"
        return layer(g, data)

    # -- dibujo ---------------------------------------------------------------------------------
    def redraw(self) -> None:
        pc = self.plot
        pc.clear()
        self.ax_map = self.ax_prof = None
        self._shown = None  # el mapa que se ve (lo pone el dibujo de un corte; la esfera y el vacío no tienen)
        g = self.grid
        if g is None:
            self.profile = None
            pc.draw()
            return
        if g.geometry == "sphere":
            self._draw_radial(pc)
            return
        values, errs, qty, unit = self.current_layer()
        rel = self.layer.currentData() == REL
        if g.geometry in ("box", "ct"):
            k = AXES[self.axis.currentData()]
            idx = self.slider.value()
            pl = plane(g, self.axis.currentData(), index=idx, layer_name=None)
            sl = [slice(None)] * 3
            sl[k] = idx
            pv, pe = values[tuple(sl)], errs[tuple(sl)]
            u_edges, v_edges = pl.u_edges, pl.v_edges
            u_name, v_name = pl.u, pl.v
            self.pos_label.setText(f"{self.axis.currentData()} = {pl.position:.4g} cm")
            ref_all = values
        elif g.geometry == "cylinder":
            # (r, z) → r en horizontal, z en vertical.
            pv, pe = values, errs
            u_edges, v_edges = g.edges[0], g.edges[1]
            u_name, v_name = "r", "z"
            if self.mirror.isChecked():
                u_edges = np.concatenate([-u_edges[::-1], u_edges[1:]])
                pv = np.concatenate([pv[::-1], pv], axis=0)
                pe = np.concatenate([pe[::-1], pe], axis=0)
            ref_all = values
        else:  # ángulos (θ, φ)
            pv, pe = values, errs
            u_edges, v_edges = g.edges[0], g.edges[1]
            u_name, v_name = "θ (°)", "φ (°)"
            ref_all = values
        vmax_all = float(np.nanmax(np.abs(ref_all))) if np.isfinite(ref_all).any() else 0.0
        f, shown_unit = (1.0, "%") if rel else grid_scale(unit, vmax_all, self.opts)
        pv = pv * f
        pe = pe * f
        ref = vmax_all * f if (self.common.isChecked() or g.geometry not in ("box", "ct")) else \
            float(np.nanmax(np.abs(pv))) if np.isfinite(pv).any() else 0.0
        with_profile = bool(self.prof_dir.currentData()) and self.pick is not None
        if with_profile:
            axes = pc.figure.subplots(1, 2, gridspec_kw={"width_ratios": [1.35, 1]})
            ax, axp = axes
        else:
            ax, axp = pc.figure.subplots(1, 1), None
        self.ax_map, self.ax_prof = ax, axp
        cmap = colormap("azul" if rel else self.cmap.currentData())
        from matplotlib.colors import LogNorm, Normalize

        if self.log.isChecked() and ref > 0:
            norm = LogNorm(vmin=ref * 1e-3, vmax=ref, clip=True)
            data = np.where(pv > 0, pv, np.nan)
        else:
            norm = Normalize(vmin=0.0, vmax=ref if ref > 0 else 1.0)
            data = pv
        mesh = ax.pcolormesh(u_edges, v_edges, data.T, cmap=cmap, norm=norm, shading="flat", rasterized=True)
        loc = self._colorbar_location(u_edges, v_edges, 0.57 if with_profile else 1.0, g.geometry != "angles")
        cb = pc.figure.colorbar(mesh, ax=ax, location=loc, shrink=0.9, aspect=40 if loc == "bottom" else 20)
        cb.set_label(REL if rel else grid_label(qty, shown_unit, self.opts), color=INK_2)
        cb.outline.set_edgecolor("#c3c2b7")
        if self.iso.isChecked() and not rel and ref > 0:
            lv = _levels(self.iso_levels.text())
            if lv:
                uc = 0.5 * (u_edges[:-1] + u_edges[1:])
                vc = 0.5 * (v_edges[:-1] + v_edges[1:])
                levels = [ref * p / 100.0 for p in lv]
                if len(uc) > 1 and len(vc) > 1 and np.nanmax(pv) >= levels[0]:
                    # Isolíneas en tinta (legibles sobre cualquier rampa), más gruesas cuanto más alto el nivel.
                    widths = [0.6 + 0.8 * i / max(len(levels) - 1, 1) for i in range(len(levels))]
                    cs = ax.contour(uc, vc, pv.T, levels=levels, colors=INK, linewidths=widths)
                    ax.clabel(cs, fmt={lev: f"{p:g} %" for lev, p in zip(levels, lv)}, fontsize=8, colors=INK)
        ax.set_xlabel(u_name if "(" in u_name else f"{u_name} (cm)")
        ax.set_ylabel(v_name if "(" in v_name else f"{v_name} (cm)")
        if g.geometry != "angles":
            ax.set_aspect("equal", adjustable="box")
        ax.grid(False)
        ax.set_title(self.title, loc="left")
        self._shown = (u_edges, v_edges, pv, pe, f, shown_unit, qty, u_name, v_name)
        if with_profile:
            self._draw_profile(axp, pv, pe, u_edges, v_edges, u_name, v_name, qty, shown_unit)
            pu, pvv = self.pick
            ax.plot([pu], [pvv], "o", color=INK, ms=5, mfc="none")
            if self.prof_dir.currentData() == "h":
                ax.axhline(pvv, color=INK, lw=0.8)
            else:
                ax.axvline(pu, color=INK, lw=0.8)
        else:
            self.profile = None
            self.profileChanged.emit(None)
        pc.draw()

    def _colorbar_location(self, u_edges, v_edges, share: float, equal: bool) -> str:
        """Barra de colores debajo si el mapa (con x e y a la misma escala) queda más ancho que el sitio que tiene,
        para que mida lo mismo que el mapa; si no, a la derecha."""
        if not equal:
            return "right"
        w = float(u_edges[-1] - u_edges[0])
        h = float(v_edges[-1] - v_edges[0])
        fw, fh = self.plot.figure.get_size_inches()
        room = (fw * share) / max(fh, 1e-6)
        return "bottom" if h > 0 and w / h > 1.25 * room else "right"

    def _draw_radial(self, pc) -> None:
        g = self.grid
        values, errs, qty, unit = self.current_layer()
        rel = self.layer.currentData() == REL
        vmax = float(np.nanmax(np.abs(values))) if values.size else 0.0
        f, shown_unit = (1.0, "%") if rel else grid_scale(unit, vmax, self.opts)
        ax = pc.figure.subplots(1, 1)
        r = g.edges[0]
        y = values * f
        e = errs * f
        ax.stairs(y, r, color=INK, lw=1.5, baseline=None)
        k = self.opts.sigma
        if k > 0 and not rel:
            ax.stairs(y + k * e, r, baseline=y - k * e, fill=True, color=INK, alpha=BAND_ALPHA, lw=0)
        if self.log.isChecked():
            ax.set_yscale("log")
        ax.set_xlabel("r (cm)")
        ax.set_ylabel(REL if rel else grid_label(qty, shown_unit, self.opts))
        ax.set_title(self.title, loc="left")
        self.ax_map = ax
        c = Curves("profile", f"{qty} radial", 0.5 * (r[:-1] + r[1:]), "r", "cm", qty, shown_unit,
                   [Series(qty.lower(), y, e)], edges=r, meta={"axis": "r", "source": g.kind})
        self.profile = c
        self.profileChanged.emit(c)
        pc.draw()

    def _draw_profile(self, axp, pv, pe, u_edges, v_edges, u_name, v_name, qty, unit) -> None:
        pu, pvv = self.pick
        horizontal = self.prof_dir.currentData() == "h"
        if horizontal:
            j = int(np.clip(np.searchsorted(v_edges, pvv) - 1, 0, pv.shape[1] - 1))
            y, e, edges, name = pv[:, j], pe[:, j], u_edges, u_name
            at = f"{v_name.split(' ')[0]} = {0.5 * (v_edges[j] + v_edges[j + 1]):.4g}"
        else:
            i = int(np.clip(np.searchsorted(u_edges, pu) - 1, 0, pv.shape[0] - 1))
            y, e, edges, name = pv[i, :], pe[i, :], v_edges, v_name
            at = f"{u_name.split(' ')[0]} = {0.5 * (u_edges[i] + u_edges[i + 1]):.4g}"
        k = self.opts.sigma
        axp.stairs(y, edges, color=INK, lw=1.5, baseline=None)
        if k > 0:
            axp.stairs(y + k * e, edges, baseline=y - k * e, fill=True, color=INK, alpha=BAND_ALPHA, lw=0)
        axp.set_xlabel(name if "(" in name else f"{name} (cm)")
        # Como la barra de color: «Dosis (nGy por partícula)».
        axp.set_ylabel(REL if qty == REL else grid_label(qty, unit, self.opts))
        axp.set_title(f"Perfil en {at}", loc="left")
        axis_name = name.split(" ")[0]
        c = Curves("profile", f"{qty} a lo largo de {axis_name} ({at})", 0.5 * (edges[:-1] + edges[1:]), axis_name,
                   "cm" if "(" not in name else "°", qty, unit, [Series(qty.lower(), y, e)], edges=edges,
                   meta={"axis": axis_name, "source": self.grid.kind if self.grid else ""})
        self.profile = c
        self.profileChanged.emit(c)

    # -- ratón ------------------------------------------------------------------------------------------
    def _on_click(self, axes, x: float, y: float, button: int) -> None:
        if axes is not self.ax_map or self.grid is None or self.grid.geometry == "sphere":
            return
        self.pick = (x, y)
        if not self.prof_dir.currentData():
            self.prof_dir.setCurrentIndex(0)
        self.redraw()
        self.pointPicked.emit(self.map_point(x, y))

    def map_point(self, u: float, v: float) -> tuple[float, ...]:
        """Punto del mapa (en su sistema: x, y, z en una caja o en el TAC; r, z en un cilindro)."""
        g = self.grid
        if g is None:
            return ()
        if g.geometry in ("box", "ct"):
            a = self.axis.currentData()
            k = AXES[a]
            pos = 0.5 * (g.edges[k][self.slider.value()] + g.edges[k][self.slider.value() + 1])
            others = [c for c in "xyz" if c != a]
            p = [0.0, 0.0, 0.0]
            p[k] = pos
            p[AXES[others[0]]] = u
            p[AXES[others[1]]] = v
            return tuple(p)
        return (abs(u), v)

    def _on_hover(self, axes, x: float, y: float) -> None:
        if self._shown is None or axes is not self.ax_map:
            return
        u_edges, v_edges, pv, pe, _f, unit, qty, un, vn = self._shown
        i = int(np.searchsorted(u_edges, x) - 1)
        j = int(np.searchsorted(v_edges, y) - 1)
        if 0 <= i < pv.shape[0] and 0 <= j < pv.shape[1]:
            self.plot.readout.setText(f"{un.split(' ')[0]} = {x:.4g}, {vn.split(' ')[0]} = {y:.4g}:  "
                                      f"{qty} {fmt(pv[i, j], pe[i, j])} {unit}")

    def plane_rows(self) -> tuple[list[str], list[np.ndarray]]:
        """El plano que se ve como columnas (u, v, valor, σ) para exportar; en una esfera, la curva radial."""
        if self._shown is None:
            p = self.profile
            if p is None or self.grid is None or self.grid.geometry != "sphere":
                return [], []
            s = p.series[0]
            err = s.err if s.err is not None else np.full_like(s.y, np.nan)
            return ([f"{p.x_name} ({p.x_unit})", f"{p.y_name} ({p.y_unit})", "sigma"], [p.x, s.y, err])
        u_edges, v_edges, pv, pe, _f, unit, qty, un, vn = self._shown
        uc = 0.5 * (u_edges[:-1] + u_edges[1:])
        vc = 0.5 * (v_edges[:-1] + v_edges[1:])
        U, V = np.meshgrid(uc, vc, indexing="ij")
        return ([un, vn, f"{qty} ({unit})", "sigma"], [U.ravel(), V.ravel(), pv.ravel(), pe.ravel()])


def profile_through(grid: Grid, axis: str, point: tuple[float, float, float], width: float = 0.0) -> Curves:
    """Perfil de un mapa 3D a lo largo de ``axis`` que pasa por ``point`` (ayuda para las pruebas y el panel)."""
    return axis_profile(grid, axis, point, width)


__all__ = ["MapView", "REL", "profile_through"]
