"""Lienzo de matplotlib para el visor de resultados: barra de zum y desplazamiento, lectura con el ratón y
exportación de la figura (PNG, PDF o SVG)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .style import INK_2, apply_style

_TIPS = {
    "Home": "Volver a la vista completa",
    "Back": "Vista anterior",
    "Forward": "Vista siguiente",
    "Pan": "Desplazar (botón izquierdo) y ampliar/reducir (botón derecho)",
    "Zoom": "Ampliar un rectángulo",
}


class PlotCanvas(QWidget):
    """Una figura de matplotlib con su barra de herramientas y una línea de lectura."""

    hovered = Signal(object, float, float)  # (ejes, x, y) en coordenadas de datos
    clicked = Signal(object, float, float, int)  # (ejes, x, y, botón)
    left = Signal()

    def __init__(self, parent=None, *, toolbar: bool = True):
        super().__init__(parent)
        apply_style()
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        from matplotlib.figure import Figure

        self.figure = Figure(figsize=(6.4, 4.2), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = NavigationToolbar2QT(self.canvas, self, coordinates=False)
        for a in list(self.nav.actions()):
            text = a.text()
            if text in ("Subplots", "Customize", "Save"):
                self.nav.removeAction(a)
            elif text in _TIPS:
                a.setToolTip(_TIPS[text])
        self.nav.setIconSize(self.nav.iconSize() * 0.8)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 4, 0)
        top.addWidget(self.nav)
        top.addStretch(1)
        self.readout = QLabel("")
        self.readout.setStyleSheet(f"color: {INK_2};")
        self.readout.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.readout)
        bar = QWidget()
        bar.setLayout(top)
        bar.setVisible(toolbar)
        lay.addWidget(bar)
        lay.addWidget(self.canvas, 1)
        self.canvas.mpl_connect("motion_notify_event", self._on_move)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.canvas.mpl_connect("axes_leave_event", lambda _e: self.left.emit())

    def clear(self) -> None:
        self.figure.clear()

    def draw(self) -> None:
        self.canvas.draw_idle()

    def _on_move(self, event) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        self.hovered.emit(event.inaxes, float(event.xdata), float(event.ydata))

    def _on_click(self, event) -> None:
        if event.inaxes is None or event.xdata is None or self.nav.mode:
            return
        self.clicked.emit(event.inaxes, float(event.xdata), float(event.ydata), int(event.button or 1))

    def export(self, path: str | Path, dpi: int = 200) -> None:
        """Guarda la figura: PNG (con ``dpi``), PDF o SVG (vectoriales)."""
        p = Path(path)
        kwargs = {"dpi": dpi} if p.suffix.lower() in (".png", ".jpg", ".tif", ".tiff") else {}
        self.figure.savefig(p, bbox_inches="tight", **kwargs)


__all__ = ["PlotCanvas"]
