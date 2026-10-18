"""Geometría nueva desde cero.

Un .geo de PENGEOM mínimo es una cabecera (texto libre hasta la primera
línea de ceros) y la línea ``END``: GEOMIN lo acepta (solo tiene la
envolvente automática, vacía). Se puede empezar con un primer cuerpo o
módulo (esfera, cilindro o caja) centrado donde se quiera.
"""

from __future__ import annotations

from .edit import Primitive, add_primitive, compile_checked
from .model import GeoDocument
from .parser import parse_geo_text
from .writer import END_LINE, SEPARATOR_LINE, write_geo_text

DEFAULT_TITLE = "Geometría creada con ARGUS"


def empty_text(title: str = DEFAULT_TITLE, lines: list[str] | None = None) -> str:
    """Texto de un .geo vacío: la cabecera (título y líneas de comentario) y END."""
    head = [" ".join((title or DEFAULT_TITLE).split())[:120]]
    head += [ln.rstrip()[:120] for ln in (lines or [])]
    return "\n".join(head + [SEPARATOR_LINE, END_LINE]) + "\n"


def new_geometry(title: str = DEFAULT_TITLE, prim: Primitive | None = None, material: int = 1,
                 module: bool = False, name: str = "", path: str | None = None,
                 comments: list[str] | None = None) -> GeoDocument:
    """Documento nuevo, vacío o con un primer cuerpo (o módulo) ``prim`` de material ``material``."""
    doc = parse_geo_text(empty_text(title, comments), path)
    if prim is not None:
        geo = compile_checked(doc)
        add_primitive(doc, geo, prim, material, container=geo.nbodys, name=name, module=module)
        doc = parse_geo_text(write_geo_text(doc), path)
    return doc
