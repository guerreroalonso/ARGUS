"""Identidad de los cuerpos entre dos versiones de una geometría.

PENGEOM numera los cuerpos y módulos por el orden en que se definen: al
añadir un cuerpo dentro de otro (que tiene que ir definido antes que su
contenedor), todos los que van detrás cambian de número. penhmain acepta en
el .in tanto la etiqueta de un cuerpo (entre apóstrofos) como su número, y
muchos .in usan números (``DSMAX 1``, ``EDBODY 2``...): tras editar la
geometría hay que saber qué número tiene ahora cada cuerpo de antes.

La identidad de un cuerpo es:

* su etiqueta completa (BALIAS con el sufijo del fichero), si la tiene;
* en un fichero incluido (con «*» o sin él), el fichero, la vez que aparece y su etiqueta: así sigue siendo el
  mismo si el INCLUDE cambia de sitio, se quita otro, se pasa de INCLUDE a INCLUDE* (PENGEOM los numera igual) o
  el mismo fichero se incluye dos veces (0.15.0, la pestaña «Geometrías complejas» mueve, añade, duplica y borra
  bloques INCLUDE). Las etiquetas de un fichero sin «*» llevan un sufijo que depende de cuántos INCLUDE van antes,
  y dos INCLUDE del mismo fichero dan cuerpos con la misma etiqueta: no sirven para identificarlos;
* la envolvente automática, si la hay;
* en las copias de un CLONE (que no tienen etiqueta), la identidad del clon y la del cuerpo original que copian.
"""

from __future__ import annotations

from .compiler import CompiledGeometry
from .model import IncludeElement

Key = tuple


def _include_places(geo: CompiledGeometry) -> dict[int, tuple[str, int]]:
    """``id`` de cada bloque INCLUDE (o INCLUDE*) del fichero → (fichero, vez que aparece ese fichero)."""
    out: dict[int, tuple[str, int]] = {}
    seen: dict[str, int] = {}
    doc = geo.document
    for el in doc.elements if doc is not None else ():
        if isinstance(el, IncludeElement):
            seen[el.filename] = seen.get(el.filename, 0) + 1
            out[id(el)] = (el.filename, seen[el.filename])
    return out


def body_keys(geo: CompiledGeometry) -> list[Key]:
    """Identidad de cada cuerpo (en el orden de PENGEOM)."""
    places = _include_places(geo)

    def own(b) -> Key:
        inc = getattr(b, "include", None)
        where = places.get(id(inc)) if inc is not None else None
        if where is not None:
            return ("include", where[0], where[1], b.alias[:4])
        return ("alias", b.alias)

    tops: dict[int, Key] = {}
    for b in geo.bodies:
        if b.origin == "clone" and b.clone_element is not None and b.element is b.clone_element:
            tops[id(b.clone_element)] = own(b)
    keys: list[Key] = []
    for b in geo.bodies:
        if b.origin == "enclosure":
            key: Key = ("enclosure",)
        elif b.origin == "clone" and b.element is not b.clone_element and b.clone_of:
            top = tops.get(id(b.clone_element), ("?",))
            key = ("clone", top, keys[b.clone_of - 1] if b.clone_of <= len(keys) else ("?", b.clone_of))
        else:
            key = own(b)
        keys.append(key)
    return keys


def _rename_key(key: Key, renames: dict[str, str]) -> Key:
    """La identidad de antes con las etiquetas cambiadas (``renames``: etiqueta de 4 caracteres → nueva; solo las
    del fichero principal: las de un INCLUDE sin «*» no cambian)."""
    if not renames:
        return key
    if key[0] == "alias":
        alias = key[1]
        new = renames.get(alias[:4])
        return ("alias", new + alias[4:]) if new is not None else key
    if key[0] == "clone":
        return ("clone", _rename_key(key[1], renames), _rename_key(key[2], renames))
    return key


def _map_key(key: Key, key_map: dict[Key, Key]) -> Key:
    """La identidad de antes con las identidades cambiadas (``key_map``), también dentro de las copias de un clon:
    al convertir en copia independiente un clon que otro CLONE copia, las copias del segundo clon copian ahora los
    cuerpos nuevos (revisión de 0.16.2: si no, sus líneas del .in se quitaban)."""
    if not key_map:
        return key
    if key in key_map:
        return key_map[key]
    if key[0] == "clone":
        return ("clone", _map_key(key[1], key_map), _map_key(key[2], key_map))
    return key


def renumber_map(old: CompiledGeometry, new: CompiledGeometry, renames: dict[str, str] | None = None,
                 key_map: dict[Key, Key] | None = None) -> dict[int, int | None]:
    """Número nuevo de cada cuerpo de ``old`` (None si ya no existe).

    ``renames``: etiquetas cambiadas (4 caracteres de antes → 4 de ahora).
    ``key_map``: identidades que cambian (p. ej. las copias de un clon que
    pasa a ser una copia independiente: de «copia del clon» a su etiqueta nueva;
    también dentro de las copias de otro clon que lo copia).
    """
    new_index: dict[Key, int] = {}
    for kb, key in enumerate(body_keys(new), start=1):
        new_index.setdefault(key, kb)
    out: dict[int, int | None] = {}
    for kb, key in enumerate(body_keys(old), start=1):
        out[kb] = new_index.get(_rename_key(_map_key(key, key_map or {}), renames or {}))
    return out


def is_identity(mapping: dict[int, int | None]) -> bool:
    return all(k == v for k, v in mapping.items())


def _close(a, b) -> bool:
    return all(abs(x - y) <= 1e-12 * max(1.0, abs(x), abs(y)) for x, y in zip(a, b, strict=True))


def geometry_change(old: CompiledGeometry, new: CompiledGeometry,
                    mapping: dict[int, int | None] | None = None) -> str | None:
    """None si ``new`` es la misma geometría que ``old`` con otra numeración (los mismos cuerpos, con el mismo
    material, la misma madre, las mismas hijas y limitantes y las mismas superficies, ya colocadas); si no, qué
    cambia (el primer cuerpo distinto). Para cambios que solo deberían renumerar o cambiar etiquetas: mover un
    bloque (PENGEOM aplica las transformaciones de los módulos en el orden en que se definen: con superficies
    compartidas, el orden puede cambiar dónde quedan) y Relabel. ``mapping``: el de ``renumber_map`` (con las
    etiquetas cambiadas, si las hay)."""
    from .compiler import body_title

    if mapping is None:
        mapping = renumber_map(old, new)
    if old.nbodys != new.nbodys:
        return f"pasa de {old.nbodys} a {new.nbodys} cuerpos y módulos"

    def mapped(kb: int) -> int:
        return (mapping.get(kb) or -1) if kb > 0 else 0

    def faces(geo: CompiledGeometry, b) -> list:
        out = []
        for ks, flag in zip(b.surfaces, b.flags, strict=False):
            if 0 < ks <= geo.nsurf:
                out.append((tuple(geo.surfaces[ks - 1].q), flag))
        return sorted(out)

    for kb in range(1, old.nbodys + 1):
        nk = mapping.get(kb)
        name = body_title(old, kb)
        if nk is None:
            return f"{name} ya no está"
        a, b = old.body(kb), new.body(nk)
        if a.is_module != b.is_module or a.material != b.material:
            return f"{name} cambia de material o de clase"
        if mapped(a.mother) != b.mother:
            return f"{name} cambia de madre"
        if sorted(mapped(x) for x in a.daughters) != sorted(b.daughters) \
                or sorted(mapped(x) for x in a.limiting) != sorted(b.limiting):
            return f"{name} cambia de hijas o de limitantes"
        fa, fb = faces(old, a), faces(new, b)
        if len(fa) != len(fb) or any(x[1] != y[1] or not _close(x[0], y[0]) for x, y in zip(fa, fb, strict=True)):
            return f"las superficies de {name} quedan en otro sitio"
    return None
