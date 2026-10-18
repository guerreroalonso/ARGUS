"""Ediciones que se aplican «fuera de la señal» del widget que las pide, pero que se pueden forzar.

Una edición del .in refresca el panel entero (y puede rehacer el widget que la
pidió), así que no se aplica dentro de su señal sino en la siguiente vuelta
del bucle de eventos. Guardar, cerrar, abrir o deshacer llegan antes de esa
vuelta cuando el valor se confirma al perder el foco (sin Enter): por eso la
ventana llama a :meth:`DeferredEdits.flush` antes de cualquiera de esas
acciones, y la edición pendiente se aplica en su orden.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer


class DeferredEdits:
    def __init__(self) -> None:
        self._queue: list[Callable[[], None]] = []

    def defer(self, fn: Callable[[], None]) -> None:
        self._queue.append(fn)
        QTimer.singleShot(0, self.flush)

    def flush(self) -> None:
        """Aplica ya lo pendiente, en orden (si una lanza, las siguientes esperan al próximo ``flush``)."""
        while self._queue:
            fn = self._queue.pop(0)
            fn()

    def __len__(self) -> int:
        return len(self._queue)


__all__ = ["DeferredEdits"]
