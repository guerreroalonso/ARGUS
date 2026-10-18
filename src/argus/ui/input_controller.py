"""Fichero de entrada (.in) abierto en la interfaz: ediciones, deshacer y guardado.

Un paso que sigue a un cambio de la geometría (renumerar cuerpos, materiales de
un TAC) lleva el número de grupo (``link``) del paso del .geo: los dos ficheros
se guardan juntos (véase ``MainWindow._pair_mismatch``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..core.files import FileChangedError, FileStamp, file_changed, file_stamp, latin1_text, write_files
from ..penhan.inputfile import InDocument, InLine, write_in_text
from ..penhan.quickscan import scan_input_text
from .geo_controller import link_last, path_key, stack_links


def input_geomfn(doc: InDocument) -> str:
    """El fichero de geometría (GEOMFN) del .in, también si tiene errores de lectura (como al abrirlo)."""
    return doc.model.geomfn or scan_input_text(doc.text()).geomfn or ""


class _SnapshotCommand(QUndoCommand):
    """Cambio del .in guardado como «antes/después» de todas sus líneas."""

    def __init__(self, ctrl: InputController, before: list[InLine], after: list[InLine], text: str):
        super().__init__(text)
        self._ctrl = ctrl
        self._before = before
        self._after = after
        self._applied = True  # el cambio ya está hecho al apilarlo
        self.link = 0  # grupo del paso de la geometría al que sigue (0: ninguno)

    def redo(self) -> None:
        if self._applied:
            self._applied = False
            return
        self._ctrl._restore(self._after)

    def undo(self) -> None:
        self._ctrl._restore(self._before)


class InputController(QObject):
    """Un .in abierto: el documento, su pila de deshacer y su fichero.

    Las ediciones se hacen con :meth:`edit` (un paso de deshacer) o, al
    arrastrar en la vista, con :meth:`live` y :meth:`commit_live` (todo el
    arrastre es un único paso).
    """

    changed = Signal()  # el documento ha cambiado (edición, deshacer, rehacer)
    dirtyChanged = Signal(bool)
    pathChanged = Signal(object)

    def __init__(self, doc: InDocument, path: str | Path | None = None, *, new: bool = False,
                 stamp: FileStamp | None = None, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.path = Path(path) if path is not None else None
        self.stack = QUndoStack(self)
        self.stack.cleanChanged.connect(self._on_clean_changed)
        self._new = new  # nunca guardado
        self._live_before: list[InLine] | None = None
        # Cómo estaba el .in en el disco al leerlo o guardarlo (para ver si otro programa lo ha cambiado).
        if stamp is None and self.path is not None and not new:
            stamp = file_stamp(self.path)
        self.disk_stamp = stamp
        # Lo que tiene el fichero guardado: grupos enlazados con la geometría y la geometría que nombra.
        self.saved_links: frozenset[int] = frozenset()
        self.saved_geomfn = "" if new else input_geomfn(doc)

    def _on_clean_changed(self, clean: bool) -> None:
        try:
            self.dirtyChanged.emit(self.dirty)
        except RuntimeError:  # la pila ya se ha destruido (cierre de la aplicación)
            pass

    # -- estado -----------------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self._new or not self.stack.isClean()

    @property
    def is_new(self) -> bool:
        """¿Nunca se ha guardado (no hay fichero)?"""
        return self._new

    @property
    def name(self) -> str:
        return self.path.name if self.path is not None else "sin título.in"

    @property
    def folder(self) -> Path | None:
        return self.path.parent if self.path is not None else None

    # -- pasos enlazados con la geometría ---------------------------------------------
    def links(self, applied: bool = True) -> set[int]:
        return stack_links(self.stack, applied)

    def link_last(self, group: int) -> None:
        link_last(self.stack, group)

    # -- ediciones ----------------------------------------------------------------
    def edit(self, text: str, fn: Callable[[InDocument], object]) -> bool:
        """Aplica ``fn`` al documento como un paso de deshacer; False si no cambia nada."""
        self.commit_live(text)
        before = self.doc.snapshot()
        try:
            fn(self.doc)
        except Exception:
            self.doc.restore(before)
            raise
        after = self.doc.snapshot()
        if after == before:
            return False
        self.stack.push(_SnapshotCommand(self, before, after, text))
        self.changed.emit()
        return True

    @property
    def in_live(self) -> bool:
        """¿Hay un arrastre (fuente, haz, rejilla) a medias?"""
        return getattr(self, "_live_before", None) is not None

    def live(self, fn: Callable[[InDocument], object]) -> None:
        """Cambio provisional durante un arrastre (se consolida con :meth:`commit_live`)."""
        if self._live_before is None:
            self._live_before = self.doc.snapshot()
        fn(self.doc)
        self.changed.emit()

    def commit_live(self, text: str) -> None:
        if self._live_before is None:
            return
        before, self._live_before = self._live_before, None
        after = self.doc.snapshot()
        if after != before:
            self.stack.push(_SnapshotCommand(self, before, after, text))
        self.changed.emit()

    def cancel_live(self) -> None:
        if self._live_before is None:
            return
        before, self._live_before = self._live_before, None
        self.doc.restore(before)
        self.changed.emit()

    def _restore(self, lines: list[InLine]) -> None:
        self.doc.restore(lines)
        self.changed.emit()

    # -- ficheros -----------------------------------------------------------------
    def changed_on_disk(self) -> bool:
        """¿Otro programa ha cambiado el .in desde que se leyó o se guardó? (uno nuevo, nunca)"""
        if self.path is None or self._new:
            return False
        return file_changed(self.path, self.disk_stamp)

    def encoded(self) -> tuple[bytes, str]:
        """Lo que se escribiría (Latin-1) y los caracteres que no caben y se escribirían como «?»."""
        self.commit_live("Editar la fuente")
        text, bad = latin1_text(write_in_text(self.doc))
        return text.encode("latin-1", errors="replace"), bad

    def save(self, path: str | Path | None = None, backup: bool = True, force: bool = False) -> Path:
        """Escribe el .in (entero o nada); si ya existía, antes copia la versión anterior a ``.bak``.

        Si otro programa lo ha cambiado desde que se leyó o se guardó, lanza ``FileChangedError`` sin escribir
        nada (salvo con ``force``): la ventana pregunta antes qué hacer.
        """
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("El fichero de entrada no tiene nombre")
        if not force and self.path is not None and path_key(target) == path_key(self.path) and self.changed_on_disk():
            raise FileChangedError(target)
        data, bad = self.encoded()
        write_files([(target, data)], backup=backup)
        self.mark_saved(target, data, bad)
        return target

    def mark_saved(self, target: str | os.PathLike, data: bytes, replaced: str = "") -> None:
        """El .in ya está escrito en ``target`` con ``data`` (aquí o, con la geometría, desde la ventana)."""
        target = Path(target)
        self.replaced_chars = replaced
        if target != self.path:
            self.path = target
            self.doc.path = str(target)
            self.doc.analyze()
            self.pathChanged.emit(target)
        self._new = False
        self.disk_stamp = file_stamp(target, data)
        self.saved_links = frozenset(self.links())
        self.saved_geomfn = input_geomfn(self.doc)
        self.stack.setClean()
        self.dirtyChanged.emit(False)
        self.changed.emit()
