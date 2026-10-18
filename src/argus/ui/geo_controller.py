"""Geometría (.geo) abierta en la interfaz: ediciones (F6), deshacer y guardado.

Cada paso de deshacer guarda el **texto** completo del .geo antes y después
del cambio: tras cada edición el texto se vuelve a leer (``edit.reparse``),
así los números de línea del panel de texto son los del fichero actual y un
documento sin cambios se reescribe byte a byte.

Un paso que el .in abierto ha seguido (renumerado, ADR-008 §4) lleva el
número de grupo (``link``) de su paso del .in: los dos ficheros se guardan
juntos (véase ``MainWindow._pair_mismatch``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..core.files import FileChangedError, file_changed, file_stamp, latin1_text, write_files
from ..pengeom.edit import EditError, compile_checked, reparse
from ..pengeom.validate import new_ct_errors, new_overflows
from ..pengeom.writer import write_geo_text
from ..session import GeometrySession, session_from_document


def path_key(path: str | os.PathLike) -> str:
    """Clave de una ruta para compararla (mayúsculas y separadores como los trata el sistema)."""
    return os.path.normcase(os.path.abspath(str(path)))


def stack_links(stack: QUndoStack, applied: bool = True) -> set[int]:
    """Grupos (``link``) de los pasos hechos de ``stack`` o, con ``applied=False``, de todos los que tiene."""
    n = stack.index() if applied else stack.count()
    return {g for g in (getattr(stack.command(i), "link", 0) for i in range(n)) if g}


def link_last(stack: QUndoStack, group: int) -> None:
    """El último paso hecho de ``stack`` va junto con un paso del otro fichero (el mismo ``group``)."""
    if group and stack.index() > 0:
        stack.command(stack.index() - 1).link = group


class _TextCommand(QUndoCommand):
    def __init__(self, ctrl: GeoController, before: str, after: str, text: str):
        super().__init__(text)
        self._ctrl = ctrl
        self._before = before
        self._after = after
        self._applied = True  # el cambio ya está hecho al apilarlo
        self.link = 0  # grupo del paso del .in que lo sigue (0: ninguno)

    def redo(self) -> None:
        if self._applied:
            self._applied = False
            return
        self._ctrl._set_text(self._after)

    def undo(self) -> None:
        self._ctrl._set_text(self._before)


def body_finder(geo):
    """``find(g, kb)``: el cuerpo ``kb`` de ``geo`` en la geometría de trabajo ``g`` de una edición (el mismo texto,
    compilado otra vez), por su identidad (``pengeom.identity``) y no por su etiqueta: las copias de un CLONE, la
    envolvente y los cuerpos de un INCLUDE sin «*» no tienen etiqueta (BALIAS en blanco)."""
    from ..pengeom.compiler import body_title
    from ..pengeom.identity import body_keys

    keys = body_keys(geo)
    seen: dict = {}  # la última geometría de trabajo y su índice identidad -> número

    def find(g, kb: int) -> int:
        if seen.get("geo") is not g:
            index: dict = {}
            for i, k in enumerate(body_keys(g), start=1):
                index.setdefault(k, i)
            seen.update(geo=g, index=index)
        i = seen["index"].get(keys[kb - 1])
        if i is None:
            raise EditError(f"{body_title(geo, kb)} ya no está en la geometría.")
        return i

    return find


def parinp_for(session, doc) -> list[float]:
    """Los PARINP con que se compila ``doc`` (una versión editada de la geometría de ``session``): los de la sesión
    o, si no hay .in (``parinp_provisional``), los números escritos en ``doc`` (un índice nuevo trae su valor)."""
    if session.parinp_provisional:
        from ..pengeom.parinp import provisional_parinp

        return provisional_parinp(doc)
    return session.parinp


def trial_edit(session, fn, check=None) -> str | None:
    """Lo que diría :meth:`GeoController.edit` de ``fn`` (el mensaje de su ``EditError``), probado en una copia sin
    cambiar nada; None si se puede hacer. Para que un diálogo no se cierre con algo que después se rechazaría."""
    check = check or getattr(fn, "check", None)
    before = write_geo_text(session.doc)
    try:
        doc = reparse(before, session.doc)
        geo = compile_checked(doc, session.parinp, session.base_dir)
        fn(doc, geo)
        after = write_geo_text(doc)
        if after == before:
            return None
        new_doc = reparse(after, session.doc, session.base_dir)
        new_geo = compile_checked(new_doc, parinp_for(session, new_doc), session.base_dir)
        overflow = new_overflows(session.geo, new_geo)
        if overflow:
            return " ".join(overflow)
        ct = new_ct_errors(session.geo, new_geo)
        if ct:
            return ct[0]
        if check is not None:
            check(session.geo, new_geo)
    except EditError as exc:
        return str(exc)
    return None


def preview_edit(session, fn, check=None) -> tuple[str, GeometrySession]:
    """Lo que haría :meth:`GeoController.edit` con ``fn`` sin hacerlo: (texto nuevo, sesión con la geometría nueva)
    para enseñarlo mientras se escribe (vista previa de la pestaña «Geometrías complejas»). Lanza ``EditError`` si
    no se podría hacer. La caja de la escena es la de ahora (no se recalcula)."""
    check = check or getattr(fn, "check", None)
    before = write_geo_text(session.doc)
    doc = reparse(before, session.doc)
    geo = compile_checked(doc, session.parinp, session.base_dir)
    fn(doc, geo)
    after = write_geo_text(doc)
    new_doc = reparse(after, session.doc, session.base_dir)
    parinp = parinp_for(session, new_doc)
    new_geo = compile_checked(new_doc, parinp, session.base_dir)
    overflow = new_overflows(session.geo, new_geo)
    if overflow:
        raise EditError(" ".join(overflow))
    ct = new_ct_errors(session.geo, new_geo)
    if ct:
        raise EditError(ct[0])
    if check is not None:
        check(session.geo, new_geo)
    return after, session_from_document(session.path, new_doc, parinp, session.input_file, new_geo,
                                        provisional=session.parinp_provisional, bounds=session.bounds, quick=True,
                                        work_dir=session.work_dir)


def preview_text(session, text: str) -> GeometrySession:
    """La geometría de un texto escrito a mano (Manual edit), la acepte PENGEOM o no (sus errores, en la sesión)."""
    doc = reparse(text, session.doc, session.base_dir)
    return session_from_document(session.path, doc, parinp_for(session, doc), session.input_file,
                                 provisional=session.parinp_provisional,
                                 bounds=session.bounds if session.ok else None, quick=True,
                                 work_dir=session.work_dir)


class GeoController(QObject):
    """La geometría abierta: su texto, su pila de deshacer y la sesión compilada."""

    changed = Signal(object)  # nueva GeometrySession tras editar, deshacer o rehacer
    dirtyChanged = Signal(bool)

    def __init__(self, session: GeometrySession, parent=None):
        super().__init__(parent)
        self.session = session
        self.path = Path(session.path)
        # Un .geo que no se ha podido leer entero (p. ej. para arreglarlo a mano en «Geometrías complejas») se
        # guarda tal cual estaba: el escritor solo sabe escribir los elementos que se han leído.
        self.text = write_geo_text(session.doc) if session.doc.ok else "".join(session.doc.lines)
        self.stack = QUndoStack(self)
        self.stack.cleanChanged.connect(self._on_clean_changed)
        self.last_result: object = None
        # Cómo estaba el .geo en el disco al leerlo o guardarlo (para ver si otro programa lo ha cambiado).
        self.disk_stamp = session.disk_stamp if session.disk_stamp is not None else file_stamp(self.path)
        # Grupos enlazados con el .in que hay en cada fichero guardado desde que se abrió (ruta -> grupos).
        self.saved_links: frozenset[int] = frozenset()
        self.disk_links: dict[str, frozenset[int]] = {path_key(self.path): self.saved_links}

    def _on_clean_changed(self, clean: bool) -> None:
        try:
            self.dirtyChanged.emit(self.dirty)
        except RuntimeError:  # la pila ya se ha destruido (cierre de la aplicación)
            pass

    @property
    def dirty(self) -> bool:
        return not self.stack.isClean()

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def doc(self):
        return self.session.doc

    @property
    def geo(self):
        return self.session.geo

    @property
    def work_dir(self) -> Path:
        """Carpeta de trabajo de penhmain: allí busca PENGEOM los INCLUDE y el .ct (véase ``GeometrySession``)."""
        return self.session.base_dir

    def set_work_dir(self, folder: str | os.PathLike | None) -> None:
        """Otra carpeta de trabajo (p. ej. tras «Guardar como…»): la de la geometría si es None o la misma."""
        if folder is not None and path_key(folder) == path_key(self.path.parent):
            folder = None
        self.session.work_dir = Path(folder) if folder is not None else None

    def move_work_dir(self, folder: str | os.PathLike | None) -> None:
        """La carpeta de trabajo de penhmain cambia (el .in se ha guardado en otra carpeta): los INCLUDE se vuelven
        a leer de allí y la geometría se vuelve a compilar (no es un paso de deshacer)."""
        from ..session import parse_with_includes

        self.set_work_dir(folder)
        self._apply(self.text, parse_with_includes(self.text, self.path, self.session.work_dir))

    # -- pasos enlazados con el .in ------------------------------------------------------------
    def links(self, applied: bool = True) -> set[int]:
        return stack_links(self.stack, applied)

    def link_last(self, group: int) -> None:
        link_last(self.stack, group)

    def disk_links_of(self, path: str | os.PathLike) -> frozenset[int] | None:
        """Grupos enlazados del fichero ``path`` guardado (None si no es uno de esta geometría)."""
        return self.disk_links.get(path_key(path))

    # -- ediciones -------------------------------------------------------------------
    def edit(self, text: str, fn: Callable, check: Callable | None = None) -> object:
        """Aplica ``fn(doc, geo)`` como un paso de deshacer y devuelve su resultado.

        Si PENGEOM no aceptaría la geometría resultante, se deja todo como
        estaba y se lanza ``EditError``. ``check(antes, después)`` (o el
        atributo ``check`` de ``fn``) puede rechazar el cambio con ``EditError``
        (p. ej. si crea solapes).
        """
        check = check or getattr(fn, "check", None)
        before = self.text
        doc = reparse(before, self.session.doc)  # copia de trabajo (el documento actual no se toca)
        geo = compile_checked(doc, self.session.parinp, self.work_dir)
        result = fn(doc, geo)
        after = write_geo_text(doc)
        if after == before:
            return result
        new_doc = reparse(after, self.session.doc, self.work_dir)
        new_geo = compile_checked(new_doc, parinp_for(self.session, new_doc), self.work_dir)  # EditError si no vale
        overflow = new_overflows(self.session.geo, new_geo)
        if overflow:  # GEOMIN la leería sin error, pero con las tablas corrompidas
            raise EditError(" ".join(overflow))
        ct = new_ct_errors(self.session.geo, new_geo)
        if ct:  # PENGEOM la leería sin error, pero los vóxeles del CT no estarían donde su caja
            raise EditError(ct[0])
        if check is not None:
            check(self.session.geo, new_geo)
        self.last_result = result
        self._apply(after, new_doc, new_geo)
        self.stack.push(_TextCommand(self, before, after, text))
        return result

    def last_good_geometry(self):
        """La geometría más reciente que PENGEOM aceptaba antes de la de ahora, en lo hecho (lo que deshacer
        recorrería hacia atrás), o None. Mientras la geometría no se puede construir, el .in no la sigue: sus cuerpos
        se nombran como en esa (0.16.3)."""
        from ..pengeom.compiler import compile_geometry

        s = self.session
        for i in range(self.stack.index() - 1, -1, -1):
            before = getattr(self.stack.command(i), "_before", None)
            if before is None:
                return None
            try:
                doc = reparse(before, s.doc, self.work_dir)
                geo = compile_geometry(doc, parinp_for(s, doc), self.work_dir)
            except Exception:  # noqa: BLE001 - un texto que no se puede leer: el anterior
                continue
            if geo.ok:
                return geo
        return None

    def replace_text(self, text: str, label: str) -> bool:
        """El texto entero pasa a ser ``text`` (un paso de deshacer), lo acepte PENGEOM o no: «Manual edit» en
        «Geometrías complejas» (los errores se ven en los mensajes). False si no cambia nada."""
        before = self.text
        if text == before:
            return False
        self._apply(text, reparse(text, self.session.doc, self.work_dir))
        self.stack.push(_TextCommand(self, before, text, label))
        return True

    # -- cambios en vivo (arrastrar con el ratón) -------------------------------------------
    @property
    def in_live(self) -> bool:
        return self._live_before is not None

    _live_before: str | None = None
    _live_session = None
    _live_base = None

    def live_transform(self, label: str, values: dict[str, float]) -> None:
        """Arrastre de un módulo o clon: su transformación con ``values`` (sobre la de antes del arrastre).

        Rápido: la geometría del principio del arrastre se lee y se compila una
        vez; en cada paso solo cambia la transformación de un elemento, se
        compila y se muestra (sin recalcular la caja de la escena). Se consolida
        con :meth:`commit_live` (un paso de deshacer) o se anula con
        :meth:`cancel_live`.
        """
        from ..pengeom.model import ParamLine
        from ..pengeom.placement import placeable, separate_surfaces, set_transform

        if self._live_before is None:
            self._live_before = self.text
            self._live_session = self.session
            doc0 = reparse(self.text, self.session.doc)
            geo0 = compile_checked(doc0, self.session.parinp, self.work_dir)
            # Si comparte superficies con cuerpos de fuera, antes recibe copias propias (si no, PENGEOM las
            # movería también para ellos): mismas tablas mientras no se mueva.
            if separate_surfaces(doc0, geo0, geo0.find_body(label).index):
                geo0 = compile_checked(doc0, self.session.parinp, self.work_dir)
            self._live_base = (doc0, geo0)
        start = self._live_session
        doc0, geo0 = self._live_base
        el = placeable(geo0, geo0.find_body(label).index)
        saved = ([ParamLine(p.name, p.value, p.ipar, p.unit, p.line) for p in el.transform], el.has_ones_line,
                 el.modified)
        try:
            if values:
                set_transform(el, values)
            text = write_geo_text(doc0)
            geo = compile_checked(doc0, start.parinp, start.base_dir)
        finally:
            el.transform, el.has_ones_line, el.modified = saved
        self.text = text
        self.session = session_from_document(self.path, doc0, start.parinp, start.input_file, geo,
                                             provisional=start.parinp_provisional, bounds=start.bounds, quick=True,
                                             work_dir=start.work_dir)
        self.changed.emit(self.session)

    def commit_live(self, text: str, check: Callable | None = None) -> bool:
        """Consolida el arrastre como un paso de deshacer; con ``check(antes, después)`` que falle, lo anula."""
        if self._live_before is None:
            return False
        before, start = self._live_before, self._live_session
        self._live_before = self._live_session = self._live_base = None
        if self.text == before:
            self._restore(before, start)
            return False
        try:
            ct = new_ct_errors(start.geo, self.session.geo)
            if ct:
                raise EditError(ct[0])
            if check is not None:
                check(start.geo, self.session.geo)
        except EditError:
            self._restore(before, start)
            raise
        after = self.text
        self.stack.push(_TextCommand(self, before, after, text))
        self._apply(after, reparse(after, start.doc))  # la sesión definitiva (documento y caja de la escena)
        return True

    def cancel_live(self) -> None:
        if self._live_before is None:
            return
        before, start = self._live_before, self._live_session
        self._live_before = self._live_session = self._live_base = None
        if self.text != before:
            self._restore(before, start)

    def _restore(self, text: str, session) -> None:
        """Vuelve a una sesión anterior (la del principio de un arrastre)."""
        self.text = text
        self.session = session
        self.changed.emit(session)

    @property
    def live_start(self):
        """Sesión del principio del arrastre (o None)."""
        return self._live_session

    def set_parinp(self, parinp: list[float]) -> None:
        """Recalcula la geometría con otros PARINP (han cambiado en el .in; no es un paso de deshacer del .geo)."""
        s = self.session
        self.session = session_from_document(self.path, s.doc, list(parinp), s.input_file, provisional=False,
                                             work_dir=s.work_dir)
        self.changed.emit(self.session)

    def _set_text(self, text: str) -> None:
        self._apply(text, reparse(text, self.session.doc, self.work_dir))

    def _apply(self, text: str, doc, geo=None) -> None:
        s = self.session
        self.text = text
        self.session = session_from_document(self.path, doc, parinp_for(s, doc), s.input_file, geo,
                                             provisional=s.parinp_provisional, work_dir=s.work_dir)
        self.changed.emit(self.session)

    # -- ficheros --------------------------------------------------------------------------
    def changed_on_disk(self) -> bool:
        """¿Otro programa ha cambiado el .geo desde que se leyó o se guardó?"""
        return file_changed(self.path, self.disk_stamp)

    def encoded(self) -> tuple[bytes, str]:
        """Lo que se escribiría (Latin-1) y los caracteres que no caben y se escribirían como «?»."""
        text, bad = latin1_text(self.text)
        return text.encode("latin-1", errors="replace"), bad

    def save(self, path: str | Path | None = None, backup: bool = True, force: bool = False) -> Path:
        """Escribe el .geo (entero o nada); si ya existía, antes copia la versión anterior a ``.bak``.

        Si otro programa lo ha cambiado desde que se leyó o se guardó, lanza ``FileChangedError`` sin escribir
        nada (salvo con ``force``): la ventana pregunta antes qué hacer.
        """
        target = Path(path) if path is not None else self.path
        if not force and path_key(target) == path_key(self.path) and self.changed_on_disk():
            raise FileChangedError(target)
        data, bad = self.encoded()
        write_files([(target, data)], backup=backup)
        self.mark_saved(target, data, bad)
        return target

    def mark_saved(self, target: str | Path, data: bytes, replaced: str = "") -> None:
        """El .geo ya está escrito en ``target`` con ``data`` (aquí o, con el .in, desde la ventana)."""
        target = Path(target)
        # Caracteres que no caben en Latin-1 (p. ej. en una descripción): se han escrito como «?» y se avisa.
        self.replaced_chars = replaced
        if target != self.path:
            self.path = target
            self.session.doc.path = str(target)
            self.session.path = target
        self.disk_stamp = file_stamp(target, data)
        self.saved_links = frozenset(self.links())
        self.disk_links[path_key(target)] = self.saved_links
        self.stack.setClean()
        self.dirtyChanged.emit(False)


__all__ = ["GeoController", "EditError"]
