"""Ventana con el resultado del índice gamma: γ a lo largo de la curva o el mapa de γ, y su histograma.

Colores: la rampa divergente centrada en γ = 1 (azul, pasa; rojo, no pasa; gris
en el límite). En las curvas, los puntos que pasan en azul y los que no en rojo,
con leyenda: el color nunca es la única pista (la línea γ = 1 y el histograma
dicen lo mismo).
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from ...results.compare import GammaResult
from ..screen_fit import fit_to_screen
from .canvas import PlotCanvas
from .style import CATEGORICAL, INK, INK_2, MUTED, colormap

PASS_COLOR = CATEGORICAL[0]  # azul
FAIL_COLOR = CATEGORICAL[7]  # rojo


class GammaDialog(QDialog):
    """Una por ventana de resultados: se reutiliza en cada cálculo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ARGUS · Índice gamma")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        lay = QVBoxLayout(self)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.info)
        self.plot = PlotCanvas(self)
        lay.addWidget(self.plot, 1)
        self.plot.hovered.connect(self._on_hover)
        self.result: GammaResult | None = None
        self._gamma_ax = None
        self.axes: list[np.ndarray] = []
        self.names: list[str] = []
        fit_to_screen(self, 900, 620)

    # -- acceso -----------------------------------------------------------------------------
    @classmethod
    def instance(cls, parent) -> GammaDialog:
        dlg = getattr(parent, "_gamma_dialog", None)
        if dlg is None:
            dlg = cls(parent)
            parent._gamma_dialog = dlg
        return dlg

    @classmethod
    def show_1d(cls, parent, res: GammaResult, x, ref_y, name: str = "Posición (cm)",
                ref_label: str = "Referencia") -> GammaDialog:
        dlg = cls.instance(parent)
        dlg.set_1d(res, np.asarray(x, dtype=np.float64), np.asarray(ref_y, dtype=np.float64), name, ref_label)
        dlg.show()
        dlg.raise_()
        return dlg

    @classmethod
    def show_2d(cls, parent, res: GammaResult, axes, names=("u (cm)", "v (cm)"), ref=None) -> GammaDialog:
        dlg = cls.instance(parent)
        if res.gamma.ndim == 1:
            dlg.set_1d(res, np.asarray(axes[0], dtype=np.float64),
                       None if ref is None else np.asarray(ref, dtype=np.float64), names[0], "Referencia")
        else:
            dlg.set_2d(res, [np.asarray(a, dtype=np.float64) for a in axes], list(names))
        dlg.show()
        dlg.raise_()
        return dlg

    # -- dibujo ---------------------------------------------------------------------------------
    def _header(self, res: GammaResult) -> None:
        if res.evaluated == 0:
            self.info.setText(f"<b>{res.criteria}</b>: ningún punto de la referencia supera el umbral.")
            return
        self.info.setText(
            f"<b>{res.criteria}</b><br>{res.passing:.1f} % de {res.evaluated} puntos con γ ≤ 1 · "
            f"γ medio {res.mean:.2f} · γ máximo {res.maximum:.2f} · percentil 99: {res.p99:.2f}")

    def set_1d(self, res: GammaResult, x: np.ndarray, ref_y: np.ndarray | None, name: str, ref_label: str) -> None:
        self.result, self.axes, self.names = res, [x], [name]
        self._header(res)
        fig = self.plot.figure
        self.plot.clear()
        g = np.asarray(res.gamma, dtype=np.float64)
        rows = 2 if ref_y is not None else 1
        axs = fig.subplots(rows, 1, sharex=True, squeeze=False, gridspec_kw={"height_ratios": [2, 1.4][:rows]})[:, 0]
        if ref_y is not None:
            a0 = axs[0]
            a0.plot(x, ref_y, color=INK, label=ref_label)
            a0.set_ylabel(ref_label)
            a0.set_title("Distribución de referencia", loc="left")
        ag = axs[-1]
        ok = np.isfinite(g)
        good = ok & (g <= 1.0)
        bad = ok & (g > 1.0)
        ag.axhline(1.0, color=INK_2, lw=0.8)
        ag.plot(x[good], g[good], "o", ms=4, color=PASS_COLOR, label="γ ≤ 1 (pasa)")
        ag.plot(x[bad], g[bad], "o", ms=4, color=FAIL_COLOR, label="γ > 1 (no pasa)")
        if (~ok).any():
            ag.plot(x[~ok], np.zeros(int((~ok).sum())), "|", ms=6, color=MUTED, label="bajo el umbral")
        ag.set_ylabel("γ")
        ag.set_xlabel(name)
        ag.set_ylim(bottom=0)
        ag.legend(loc="upper right")
        self._gamma_ax = ag
        self.plot.draw()

    def set_2d(self, res: GammaResult, axes: list[np.ndarray], names: list[str]) -> None:
        from matplotlib.colors import TwoSlopeNorm

        self.result, self.axes, self.names = res, axes, names
        self._header(res)
        fig = self.plot.figure
        self.plot.clear()
        g = np.asarray(res.gamma, dtype=np.float64)
        ok = np.isfinite(g)
        top = max(2.0, float(np.nanpercentile(g[ok], 99)) if ok.any() else 2.0)
        axm, axh = fig.subplots(1, 2, gridspec_kw={"width_ratios": [3, 1.3]})
        norm = TwoSlopeNorm(vmin=0.0, vcenter=1.0, vmax=top)
        cmap = colormap("divergente").with_extremes(bad=(0.0, 0.0, 0.0, 0.0))  # sin dato: transparente
        u, v = axes[0], axes[1]
        mesh = axm.pcolormesh(u, v, np.ma.masked_invalid(g).T, cmap=cmap, norm=norm, shading="nearest")
        axm.set_aspect("equal", adjustable="box")
        axm.set_xlabel(names[0] if names else "u (cm)")
        axm.set_ylabel(names[1] if len(names) > 1 else "v (cm)")
        axm.set_title("Índice gamma", loc="left")
        axm.grid(False)
        cb = fig.colorbar(mesh, ax=axm, shrink=0.9, extend="max")
        cb.set_label("γ (azul: pasa · rojo: no pasa)")
        vals = g[ok]
        if len(vals):
            bins = np.linspace(0.0, top, 41)
            axh.hist(np.clip(vals, 0, top), bins=bins[bins <= 1.0 + 1e-9], color=PASS_COLOR, label="γ ≤ 1")
            axh.hist(np.clip(vals, 0, top), bins=bins[bins >= 1.0 - 1e-9], color=FAIL_COLOR, label="γ > 1")
            axh.axvline(1.0, color=INK_2, lw=0.8)
            axh.legend(loc="upper right")
        axh.set_xlabel("γ")
        axh.set_ylabel("Puntos")
        axh.set_title("Histograma", loc="left")
        self._gamma_ax = axm
        self.plot.draw()

    def _on_hover(self, ax, x: float, y: float) -> None:
        res = self.result
        if res is None or not self.axes:
            return
        g = res.gamma
        if g.ndim > 1 and ax is not self._gamma_ax:
            return
        if g.ndim == 1:
            i = int(np.argmin(np.abs(self.axes[0] - x)))
            v = g[i]
            where = f"{self.names[0].split(' (')[0]} = {self.axes[0][i]:.4g}"
        else:
            i = int(np.argmin(np.abs(self.axes[0] - x)))
            j = int(np.argmin(np.abs(self.axes[1] - y)))
            v = g[i, j]
            where = f"({self.axes[0][i]:.4g}, {self.axes[1][j]:.4g})"
        self.plot.readout.setText(f"{where}: γ = {v:.3f}" if np.isfinite(v) else f"{where}: bajo el umbral")


__all__ = ["GammaDialog"]
