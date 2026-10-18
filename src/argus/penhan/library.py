"""Materiales personalizados: las composiciones propias que el usuario guarda para volver a usarlas (0.16.0, ADR-020).

Un material de composición propia (penhmat, opción 1: elementos con su cantidad, densidad y energía media de
excitación) se puede guardar con un nombre en la biblioteca del usuario y elegirse después en cualquier caso, como
uno de la lista de PENELOPE; también editarlo, copiarlo (una aleación con otra proporción), borrarlo, exportarlo e
importarlo.

La biblioteca es de cada usuario (``materiales_personalizados.json`` en su carpeta de datos de ARGUS,
:func:`argus.core.userdata.user_data_dir`): JSON en UTF-8, que se escribe entero o nada (un temporal que sustituye al
de antes). Si el fichero no se puede leer, no se pisa: se guarda aparte («….dañado») antes de escribir uno nuevo. Lo
que esta versión no entiende (un material que no se puede leer, campos o valores de una versión más nueva) se vuelve a
escribir tal cual, y antes de cada cambio se relee el fichero (otro ARGUS abierto a la vez puede haberlo cambiado):
revisión de 0.16 (0.16.2).
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..core.files import new_temp
from ..core.userdata import user_data_dir
from .composition import BY_ATOMS, BY_MASS, I_ARGUS, I_PENELOPE, I_VALUE, Composition

FILE_NAME = "materiales_personalizados.json"
KIND = "argus-materiales-personalizados"
VERSION = 1
MAX_NAME = 60  # como el nombre que se le da a penhmat (PEMATW)


_KNOWN = ("name", "elements", "by", "density", "i_mode", "i_value", "color", "note", "modified")
_TEXT = ("color", "note", "modified")  # los que esta versión lee como texto: con otro tipo, se escriben tal cual
_LISTED = re.compile(r"\s*\(\s*PENELOPE\s+\d+\s*\)", re.IGNORECASE)


def clean_name(name: str) -> str:
    """El nombre de un material personalizado: sin saltos de línea ni espacios de más, de hasta 60 caracteres y sin
    «(PENELOPE n)», que es de los de la lista de PENELOPE (con él, el personalizado se confundiría con el n.º n)."""
    return " ".join(_LISTED.sub(" ", str(name)).split())[:MAX_NAME].strip()


@dataclass
class LibraryMaterial:
    name: str
    composition: Composition
    color: str | None = None  # «#rrggbb» (el color en las vistas, si se guardó)
    note: str = ""
    modified: str = ""  # fecha ISO de la última vez que se guardó
    problem: str | None = field(default=None, compare=False)  # por qué no vale (leída de un fichero)
    extra: dict = field(default_factory=dict, compare=False)  # campos de otra versión de ARGUS: se escriben tal cual

    def summary(self) -> str:
        if self.problem:
            return f"✖ {self.problem}"
        try:
            return self.composition.summary()
        except Exception:  # noqa: BLE001 - una composición escrita a mano que no se entiende no rompe la lista
            return "✖ composición que no se entiende"


def library_file() -> Path:
    return user_data_dir() / FILE_NAME


def _now() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


# -- de y a JSON -------------------------------------------------------------------------------------------------------

def to_json(m: LibraryMaterial) -> dict:
    c = m.composition
    out = {"name": m.name, "elements": [[int(z), float(v)] for z, v in c.elements], "by": c.by,
           "density": float(c.density), "i_mode": c.i_mode, "i_value": c.i_value}
    if m.color:
        out["color"] = m.color
    if m.note:
        out["note"] = m.note
    if m.modified or "modified" not in m.extra:  # (una fecha de otra versión, sin tocar el material: tal cual)
        out["modified"] = m.modified or _now()
    for k, v in m.extra.items():  # lo de una versión más nueva de ARGUS, tal cual
        out.setdefault(k, v)
    return out


def from_json(d: dict) -> LibraryMaterial:
    """Un material de la biblioteca (ValueError si no tiene lo que hace falta)."""
    if not isinstance(d, dict):
        raise ValueError("no es un material")
    name = clean_name(d.get("name", ""))
    if not name:
        raise ValueError("un material sin nombre")
    try:
        elements = [(int(z), float(v)) for z, v in d.get("elements", [])]
        by = str(d.get("by", BY_ATOMS))
        density = float(d.get("density"))
        i_mode = str(d.get("i_mode", I_ARGUS))
        iv = d.get("i_value")
        i_value = float(iv) if iv is not None else None
    except (TypeError, ValueError, OverflowError):  # (OverflowError: «Z» 1e999, que es infinito)
        raise ValueError(f"«{name}»: composición ilegible") from None
    if by not in (BY_ATOMS, BY_MASS):
        raise ValueError(f"«{name}»: cantidades «{by}» desconocidas")
    if i_mode not in (I_ARGUS, I_PENELOPE, I_VALUE):
        raise ValueError(f"«{name}»: energía media de excitación «{i_mode}» desconocida")
    comp = Composition(elements, by, density, i_mode, i_value)
    # Lo que no es de esta versión (campos nuevos, o el color, la nota o la fecha con otro tipo) se escribe tal cual.
    extra = {k: v for k, v in d.items() if k not in _KNOWN or (k in _TEXT and v is not None and not isinstance(v, str))}
    text = {k: (d.get(k) if isinstance(d.get(k), str) else "") for k in _TEXT}
    m = LibraryMaterial(name, comp, text["color"] or None, text["note"], text["modified"], extra=extra)
    try:
        probs = comp.problems() if math.isfinite(density) else ["la densidad no es un número"]
    except Exception:  # noqa: BLE001 - escrito a mano: se lista con su problema y se puede arreglar
        probs = ["composición que no se entiende"]
    m.problem = "; ".join(probs) if probs else None
    return m


@dataclass
class _Content:
    items: list[LibraryMaterial]
    problems: list[str]  # lo que no se ha podido leer
    unread: list  # esos materiales, tal como estaban en el fichero (se vuelven a escribir así)
    version: int
    extra: dict = field(default_factory=dict)  # lo de fuera de «materials» de una versión más nueva (tal cual)


def _parse(text: str) -> _Content:
    try:
        data = json.loads(text)
    except RecursionError:
        raise ValueError("no es un fichero de materiales personalizados de ARGUS") from None
    if not isinstance(data, dict) or data.get("kind") != KIND or not isinstance(data.get("materials"), list):
        raise ValueError("no es un fichero de materiales personalizados de ARGUS")
    out = _Content([], [], [], VERSION, {k: v for k, v in data.items() if k not in ("kind", "version", "materials")})
    v = data.get("version")
    if isinstance(v, int) and not isinstance(v, bool) and v > VERSION:
        out.version = v  # de una versión más nueva: no se le baja el número
    for d in data["materials"]:
        try:
            out.items.append(from_json(d))
        except ValueError as exc:
            out.problems.append(str(exc))
            out.unread.append(d)
    return out


def _read(path: str | os.PathLike) -> _Content:
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        return _parse(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no es un fichero JSON válido ({exc.msg}, línea {exc.lineno})") from None


def read_file(path: str | os.PathLike) -> tuple[list[LibraryMaterial], list[str]]:
    """Los materiales de un fichero (de la biblioteca o exportado) y lo que no se ha podido leer.

    OSError o ValueError si el fichero no se puede leer entero (no es de materiales de ARGUS)."""
    c = _read(path)
    return c.items, c.problems


def _stamp(st: os.stat_result) -> tuple[int, int, int]:
    """Lo que dice si el fichero es otro: fecha, tamaño y nodo (cada escritura es un fichero nuevo; con fechas poco
    finas, en FAT o en algunas unidades de red, un cambio del mismo tamaño solo se nota en el nodo)."""
    return st.st_mtime_ns, st.st_size, st.st_ino


def write_file(path: str | os.PathLike, items: list[LibraryMaterial], unread: list | None = None,
               version: int = VERSION, extra: dict | None = None) -> tuple[int, int, int]:
    """Escribe los materiales enteros o nada (un temporal en la misma carpeta que sustituye al fichero, con los
    permisos de un fichero nuevo o los del que había). ``unread``: los que no se han podido leer, que se escriben tal
    cual estaban; ``extra``: lo de fuera de la lista de una versión más nueva. Devuelve la marca del fichero escrito
    (:func:`_stamp`, tomada antes de darle su nombre: otro ARGUS que lo cambie justo después no pasa inadvertido)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**(extra or {}), "kind": KIND, "version": max(VERSION, version),
            "materials": [to_json(m) for m in items] + list(unread or [])}
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    tmp, out = new_temp(path)
    try:
        with out:
            out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        if path.is_file():
            try:
                shutil.copymode(path, tmp)
            except OSError:
                pass
        stamp = _stamp(os.stat(tmp))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return stamp


# -- la biblioteca del usuario ----------------------------------------------------------------------------------------

class Library:
    """La biblioteca de materiales personalizados del usuario (``path``: otra, p. ej. en las pruebas).

    Los nombres son únicos sin distinguir mayúsculas (si el fichero, escrito a mano, repite uno, el segundo se lee
    como «nombre (2)»). Antes de cada cambio se relee el fichero si ha cambiado (otro ARGUS abierto a la vez)."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path is not None else library_file()
        self.items: list[LibraryMaterial] = []
        self.problems: list[str] = []  # lo que no se ha podido leer
        self.unreadable: str | None = None  # el fichero entero no se puede leer: no se pisa sin guardarlo aparte
        self._unread: list = []  # los materiales que no se han podido leer, tal cual (se vuelven a escribir así)
        self._version = VERSION
        self._extra: dict = {}  # lo de fuera de la lista de una versión más nueva (se vuelve a escribir así)
        self._stamp: tuple[int, int, int] | None = None  # el fichero tal como se leyó o se escribió por última vez
        self.load()

    def _signature(self) -> tuple[int, int, int] | None:
        try:
            return _stamp(self.path.stat())
        except OSError:
            return None

    def load(self) -> None:
        self.items, self.problems, self.unreadable, self._unread, self._version = [], [], None, [], VERSION
        self._extra = {}
        self._stamp = self._signature()
        if not self.path.is_file():
            return
        try:
            c = _read(self.path)
        except (OSError, ValueError, OverflowError) as exc:
            self.unreadable = str(exc)
            return
        self.items, self.problems, self._unread, self._version = c.items, c.problems, c.unread, c.version
        self._extra = c.extra
        self._unique_names()

    def _unique_names(self) -> None:
        seen: set[str] = set()
        for m in self.items:
            if m.name.casefold() in seen:
                k = 2
                while (cand := _numbered(m.name, k)).casefold() in seen or \
                        any(x.name.casefold() == cand.casefold() for x in self.items):
                    k += 1
                m.name = cand
            seen.add(m.name.casefold())

    def _sync(self) -> None:
        """Relee el fichero si ha cambiado desde la última vez (lo que guardó otro ARGUS no se pierde)."""
        if self._signature() != self._stamp:
            self.load()

    def save(self) -> None:
        if self.unreadable is not None and self.path.is_file():
            # No se pisa lo que no se ha podido leer: se guarda aparte («materiales_personalizados.json.dañado»).
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            os.replace(self.path, self.path.with_name(f"{self.path.name}.dañado-{stamp}"))
            self.unreadable = None
        self._stamp = write_file(self.path, self.items, self._unread, self._version, self._extra)

    # -- consultas --------------------------------------------------------------------------------------------------
    def names(self) -> list[str]:
        return [m.name for m in self.items]

    def find(self, name: str) -> LibraryMaterial | None:
        key = clean_name(name).casefold()
        return next((m for m in self.items if m.name.casefold() == key), None) if key else None

    def free_name(self, base: str) -> str:
        """``base`` si está libre; si no, «base (2)», «base (3)»…"""
        base = clean_name(base) or "Material"
        if self.find(base) is None:
            return base
        stem = base
        if stem.endswith(")") and " (" in stem:
            head, _sep, tail = stem.rpartition(" (")
            if tail[:-1].isdigit():
                stem = head
        k = 2
        while self.find(cand := _numbered(stem, k)) is not None:
            k += 1
        return cand

    # -- cambios (cada uno relee el fichero si ha cambiado y lo guarda) -----------------------------------------------
    def put(self, m: LibraryMaterial, replace: str | None = None) -> LibraryMaterial:
        """Añade ``m`` o, con ``replace``, sustituye al material de ese nombre (que puede cambiar de nombre; su nota y
        lo de otras versiones se conservan)."""
        self._sync()
        m.name = clean_name(m.name)
        if not m.name:
            raise ValueError("El material necesita un nombre.")
        other = self.find(m.name)
        old = self.find(replace) if replace is not None else None
        if other is not None and other is not old:
            raise ValueError(f"Ya hay un material personalizado «{other.name}».")
        m.modified = _now()
        m.problem = None
        if old is not None:
            m.note = m.note or old.note
            m.extra = {**old.extra, **m.extra}
            self.items[self.items.index(old)] = m
        else:
            self.items.append(m)
        self.save()
        return m

    def remove(self, name: str) -> bool:
        self._sync()
        m = self.find(name)
        if m is None:
            return False
        self.items.remove(m)
        self.save()
        return True

    def duplicate(self, name: str) -> LibraryMaterial | None:
        """Una copia del material (para cambiarla: otra proporción de una aleación), con un nombre libre."""
        self._sync()
        m = self.find(name)
        if m is None:
            return None
        c = m.composition
        copy = LibraryMaterial(self.free_name(f"{m.name} (copia)"),
                               Composition(list(c.elements), c.by, c.density, c.i_mode, c.i_value), m.color, m.note)
        return self.put(copy)

    def merge(self, items: list[LibraryMaterial]) -> tuple[int, int, int]:
        """Importa ``items``: (añadidos, iguales que ya estaban, añadidos con otro nombre porque ya había uno
        distinto con el suyo). Los nombres se limpian como al guardar uno (:func:`clean_name`)."""
        self._sync()
        added = same = renamed = 0
        for m in items:
            name = clean_name(m.name) or "Material"
            have = self.find(name)
            key = name.casefold()
            twin = next((x for x in self.items if x.composition == m.composition
                         and (x.name.casefold() == key or x.name.casefold().startswith(key + " ("))), None)
            if twin is not None:  # ya lo tiene (también con el nombre que le dio otra importación)
                same += 1
                continue
            new = LibraryMaterial(name, m.composition, m.color, m.note, m.modified or _now(), extra=dict(m.extra))
            if have is not None:
                new.name = self.free_name(f"{name} (importado)")
                renamed += 1
            else:
                added += 1
            self.items.append(new)
        if added or renamed:
            self.save()
        return added, same, renamed


def _numbered(stem: str, k: int) -> str:
    """«stem (k)», recortando ``stem`` para que quepa en los 60 caracteres."""
    tail = f" ({k})"
    return f"{stem[:MAX_NAME - len(tail)].rstrip()}{tail}"


__all__ = ["FILE_NAME", "KIND", "Library", "LibraryMaterial", "MAX_NAME", "clean_name", "from_json", "library_file",
           "read_file", "to_json", "write_file"]
