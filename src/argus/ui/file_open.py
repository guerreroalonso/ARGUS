"""Ficheros que el sistema pide abrir sin pasarlos en la línea de órdenes (0.17.0).

En macOS, un fichero abierto desde el Finder con ARGUS (doble clic, «Abrir con», arrastrarlo al icono del Dock)
no llega en ``sys.argv``: llega a la aplicación como un ``QFileOpenEvent``, también cuando ARGUS ya está abierto. En
Windows y Linux no ocurre (el fichero va en la línea de órdenes), así que allí no hace nada.

El evento se recoge en ``event()`` de la aplicación (:class:`ArgusApplication`) y no con un filtro de eventos: un
filtro instalado en la aplicación vería todos los eventos de todos los objetos.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication


class FileOpenRequests(QObject):
    """Lo que pide abrir el sistema: se abre con ``opener(ruta)`` en la vuelta siguiente del bucle de eventos (abrir
    puede preguntar: guardar los cambios, qué .in usar...). Lo que llega antes de haber ventana (al abrir ARGUS con
    un fichero desde el Finder) espera en :attr:`pending` hasta :meth:`set_opener`."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._opener: Callable[[str], None] | None = None
        self.pending: list[str] = []
        self.requested: list[str] = []  # todo lo pedido, en orden

    def set_opener(self, opener: Callable[[str], None]) -> None:
        self._opener = opener
        waiting, self.pending = self.pending, []
        for path in waiting:
            QTimer.singleShot(0, lambda p=path: opener(p))

    def handle(self, event) -> bool:
        """¿Era un fichero que abrir? (y entonces se encarga de él)."""
        if event.type() != QEvent.Type.FileOpen:
            return False
        path = event.file() or event.url().toLocalFile()
        if path:
            self.requested.append(path)
            if self._opener is None:
                self.pending.append(path)
            else:
                opener = self._opener
                QTimer.singleShot(0, lambda p=path: opener(p))
        return True


class ArgusApplication(QApplication):
    """La aplicación de ARGUS: una QApplication que además recoge los ficheros que pide abrir macOS.

    Qt ya manda eventos a la aplicación mientras se construye (y al crearle un hijo): ``event()`` no puede contar
    con que :attr:`file_open_requests` exista todavía, y este no es hijo suyo (lo guarda el atributo)."""

    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.file_open_requests = FileOpenRequests()

    def event(self, event) -> bool:
        requests = getattr(self, "file_open_requests", None)
        if requests is not None and requests.handle(event):
            return True
        return super().event(event)


__all__ = ["ArgusApplication", "FileOpenRequests"]
