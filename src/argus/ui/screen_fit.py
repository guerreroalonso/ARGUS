"""Ventanas y diálogos que caben en la pantalla.

Con la pantalla al 150 %, un portátil de 1920 × 1080 tiene 1280 × 720 puntos (y algo menos sin la barra de tareas): las
ventanas de ARGUS (1400 × 880) y algunos diálogos (820 × 820) se salían por abajo, con los botones fuera de la pantalla.
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget

__all__ = ["FRACTION", "fit_to_screen", "screen_for"]

FRACTION = 0.92  # como mucho, esta parte del sitio libre de la pantalla


def screen_for(widget: QWidget) -> QScreen | None:
    """La pantalla donde va a salir ``widget``: la de su ventana madre (un diálogo sale encima) o la suya."""
    parent = widget.parentWidget()
    for w in (parent.window() if parent is not None else None, widget):
        if w is None:
            continue
        try:
            s = w.screen()
        except RuntimeError:
            s = None
        if s is not None:
            return s
    return QGuiApplication.primaryScreen()


def fit_to_screen(widget: QWidget, width: int, height: int, fraction: float = FRACTION) -> None:
    """``widget.resize(width, height)`` sin pasar de ``fraction`` del sitio libre de su pantalla (sin la barra de
    tareas). Si la ventana no puede ser tan pequeña, Qt la deja en su mínimo."""
    screen = screen_for(widget)
    if screen is not None:
        g = screen.availableGeometry()
        if g.width() > 0 and g.height() > 0:
            width = min(int(width), int(g.width() * fraction))
            height = min(int(height), int(g.height() * fraction))
    widget.resize(max(int(width), 1), max(int(height), 1))
