"""Gráfica del espectro de la fuente tal como lo muestrea penhmain.

Espectro continuo: una barra por intervalo [E_i, E_(i+1)]; en modo
«probabilidad» su altura es la probabilidad del intervalo y en modo
«densidad», la probabilidad por MeV. Los intervalos de anchura cero y las
líneas (SLINES) son deltas: una raya vertical con su probabilidad.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..penhan.sections import SpectrumInfo

BAR = QColor(78, 121, 167)
BAR_EDGE = QColor(40, 70, 110)
DELTA = QColor(200, 60, 50)
AXIS = QColor(90, 90, 90)
GRID = QColor(225, 225, 225)


def energy_unit(emax: float) -> tuple[float, str]:
    for f, u in ((1e9, "GeV"), (1e6, "MeV"), (1e3, "keV")):
        if emax >= f:
            return f, u
    return 1.0, "eV"


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step - 1e-9) * step
    ticks = []
    # Por índice (no sumando el paso): con un intervalo de pocas cifras, t + paso puede no avanzar y el bucle no
    # acabaría nunca.
    for i in range(4 * max(n, 1) + 2):
        t = start + i * step
        if t > hi + 1e-9 * step:
            break
        ticks.append(round(t, 12))
    return ticks or [lo]


class SpectrumPlot(QWidget):
    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self.info: SpectrumInfo | None = None
        self.mode = "prob"  # 'prob' o 'density'
        self.compact = compact
        self.setMouseTracking(True)
        self.setMinimumSize(160 if compact else 360, 70 if compact else 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._shapes: list[tuple[QRectF, str]] = []

    def set_info(self, info: SpectrumInfo | None) -> None:
        self.info = info
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    # -- dibujo ------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        self._shapes = []
        info = self.info
        finite = info is not None and all(math.isfinite(v) for b in info.bins for v in b) and \
            math.isfinite(info.emin) and math.isfinite(info.emax)
        if info is None or not info.bins or not finite:
            p.setPen(AXIS)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Sin espectro" if info is None or not info.bins else "Espectro con Inf o NaN")
            p.end()
            return
        f, unit = energy_unit(max(abs(info.emax), abs(info.emin), 1.0))
        e0, e1 = info.emin / f, info.emax / f
        if e1 <= e0:
            e0, e1 = e0 - 0.5, e1 + 0.5
        pad = 0.04 * (e1 - e0)
        x0, x1 = e0 - pad, e1 + pad
        density = self.mode == "density" and not info.lines
        heights = []
        for a, b, pr in info.bins:
            if b > a and density:
                heights.append(100.0 * pr / ((b - a) / f))
            else:
                heights.append(100.0 * pr)
        cont = [h for (a, b, _), h in zip(info.bins, heights) if b > a or not density]
        ymax = max(cont + [1e-12]) * 1.12
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * (0.8 if self.compact else 0.9)))
        p.setFont(font)
        fm = p.fontMetrics()
        left = 6 if self.compact else fm.horizontalAdvance("100.0 %") + 12
        bottom = 4 if self.compact else fm.height() * 2 + 6
        top = 6
        right = 8
        plot = QRectF(left, top, max(10, self.width() - left - right), max(10, self.height() - top - bottom))

        def X(e):
            return plot.left() + (e - x0) / (x1 - x0) * plot.width()

        def Y(h):
            return plot.bottom() - min(h, ymax) / ymax * plot.height()

        if not self.compact:
            p.setPen(QPen(GRID, 1))
            for t in nice_ticks(0.0, ymax / 1.12, 4):
                y = Y(t)
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
                p.setPen(AXIS)
                lab = f"{t:g} %" if not density else f"{t:g}"
                p.drawText(QRectF(0, y - fm.height() / 2, left - 4, fm.height()),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lab)
                p.setPen(QPen(GRID, 1))
        # Barras de los intervalos.
        for (a, b, pr), h in zip(info.bins, heights):
            if b > a:
                r = QRectF(QPointF(X(a / f), Y(h)), QPointF(X(b / f), plot.bottom()))
                p.setPen(QPen(BAR_EDGE, 0.8))
                p.setBrush(BAR)
                p.drawRect(r)
                self._shapes.append((r.adjusted(-1, -3, 1, 0),
                                     f"{a / f:.6g}–{b / f:.6g} {unit}: {100 * pr:.4g} % "
                                     f"({100 * pr / ((b - a) / f):.4g} %/{unit})"))
        # Deltas (líneas o intervalos de anchura cero).
        for a, b, pr in info.bins:
            if b == a:
                x = X(a / f)
                y = Y(100.0 * pr) if not density else plot.top() + 0.1 * plot.height()
                p.setPen(QPen(DELTA, 2.2))
                p.drawLine(QPointF(x, plot.bottom()), QPointF(x, y))
                p.setBrush(DELTA)
                p.drawEllipse(QPointF(x, y), 3.2, 3.2)
                if not self.compact and pr > 0.001:
                    p.setPen(DELTA)
                    label = f"{100 * pr:.3g} %"
                    w = fm.horizontalAdvance(label)
                    tx = x + 5 if x + 5 + w <= self.width() - 2 else x - 5 - w
                    p.drawText(QPointF(tx, y + fm.ascent() / 2), label)
                self._shapes.append((QRectF(x - 4, y - 4, 8, plot.bottom() - y + 4),
                                     f"{a / f:.6g} {unit} exactos: {100 * pr:.4g} %"))
        # Ejes.
        p.setPen(QPen(AXIS, 1))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        if not self.compact:
            for t in nice_ticks(e0, e1, max(2, int(plot.width() / 90))):
                x = X(t)
                p.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 3))
                p.drawText(QRectF(x - 40, plot.bottom() + 3, 80, fm.height()), Qt.AlignmentFlag.AlignHCenter,
                           f"{t:g}")
            p.drawText(QRectF(plot.left(), plot.bottom() + fm.height() + 3, plot.width(), fm.height()),
                       Qt.AlignmentFlag.AlignHCenter, f"Energía ({unit})")
            mean_x = X(info.mean / f)
            p.setPen(QPen(QColor(30, 140, 80), 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(mean_x, plot.top()), QPointF(mean_x, plot.bottom()))
            p.drawText(QPointF(mean_x + 3, plot.top() + fm.ascent()), f"media {info.mean / f:.4g}")
            if density:
                p.setPen(AXIS)
                p.drawText(QRectF(2, 0, left + 60, fm.height()), Qt.AlignmentFlag.AlignLeft, f"%/{unit}")
        p.end()

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        for r, text in reversed(self._shapes):
            if r.contains(pos):
                QToolTip.showText(e.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()
