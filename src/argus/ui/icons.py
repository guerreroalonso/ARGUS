"""Icono de ARGUS, dibujado con QPainter (nítido a cualquier tamaño, sin ficheros de imagen).

Tres estilos (Ayuda → Icono de ARGUS):

* **trébol**: el símbolo de la radiación ionizante (proporciones de la norma
  ISO 361: círculo central de radio R y aspas de 60° entre 1,5 R y 5 R);
* **átomo**: núcleo y tres órbitas;
* **ojo**: el de las primeras versiones (Argos Panoptes, el de los cien ojos).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTransform

from .settings import app_settings

ICON_STYLES = {"trefoil": "Trébol radiactivo", "atom": "Átomo", "eye": "Ojo (el de antes)"}
DEFAULT_STYLE = "trefoil"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def icon_style() -> str:
    style = str(app_settings().value("ui/icon", DEFAULT_STYLE))
    return style if style in ICON_STYLES else DEFAULT_STYLE


def set_icon_style(style: str) -> None:
    app_settings().setValue("ui/icon", style if style in ICON_STYLES else DEFAULT_STYLE)


def argus_icon(style: str | None = None) -> QIcon:
    style = style or icon_style()
    icon = QIcon()
    for s in SIZES:
        icon.addPixmap(argus_pixmap(style, s))
    return icon


def argus_pixmap(style: str, size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    {"trefoil": _trefoil, "atom": _atom, "eye": _eye}.get(style, _trefoil)(p, float(size))
    p.end()
    return pm


def _trefoil(p: QPainter, s: float) -> None:
    c = QPointF(s / 2, s / 2)
    big = s * 0.47
    dark = QColor("#1d1d1b")
    p.setPen(QPen(dark, max(1.0, s * 0.035)))
    p.setBrush(QColor("#f7c600"))
    p.drawEllipse(c, big, big)
    rc = big * 0.16  # círculo central (R de la norma)
    r1, r2 = 1.5 * rc, 5.0 * rc
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(dark)
    p.drawEllipse(c, rc, rc)
    outer = QRectF(c.x() - r2, c.y() - r2, 2 * r2, 2 * r2)
    inner = QRectF(c.x() - r1, c.y() - r1, 2 * r1, 2 * r1)
    for center in (270.0, 30.0, 150.0):  # un aspa hacia abajo y dos hacia arriba
        a0 = center - 30.0
        path = QPainterPath()
        path.arcMoveTo(outer, a0)
        path.arcTo(outer, a0, 60.0)
        path.arcTo(inner, a0 + 60.0, -60.0)
        path.closeSubpath()
        p.drawPath(path)


def _atom(p: QPainter, s: float) -> None:
    c = QPointF(s / 2, s / 2)
    p.setPen(QPen(QColor("#1f3b57"), max(1.0, s * 0.03)))
    p.setBrush(QColor("#e8eef5"))
    p.drawEllipse(c, s * 0.47, s * 0.47)
    orbit = QPen(QColor("#2f6db3"), max(1.0, s * 0.045))
    p.setBrush(Qt.BrushStyle.NoBrush)
    a, b = s * 0.40, s * 0.15
    for ang in (0.0, 60.0, 120.0):
        p.save()
        p.setTransform(QTransform().translate(c.x(), c.y()).rotate(ang), True)
        p.setPen(orbit)
        p.drawEllipse(QPointF(0, 0), a, b)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1f3b57"))
        p.drawEllipse(QPointF(a, 0), s * 0.045, s * 0.045)  # electrón
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.restore()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#d9480f"))
    p.drawEllipse(c, s * 0.085, s * 0.085)  # núcleo


def _eye(p: QPainter, s: float) -> None:
    p.setPen(QPen(QColor("#1f3b57"), s * 0.06))
    p.setBrush(QColor("#e8eef5"))
    path = QPainterPath()
    path.moveTo(s * 0.06, s * 0.5)
    path.quadTo(s * 0.5, s * 0.08, s * 0.94, s * 0.5)
    path.quadTo(s * 0.5, s * 0.92, s * 0.06, s * 0.5)
    p.drawPath(path)
    p.setBrush(QColor("#4e79a7"))
    p.drawEllipse(QRectF(s * 0.33, s * 0.33, s * 0.34, s * 0.34))
    p.setBrush(QColor("#10161d"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(s * 0.43, s * 0.43, s * 0.14, s * 0.14))
