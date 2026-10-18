"""Parámetros PARINP de una geometría: qué índices usa y qué números trae escritos.

Una línea del .geo como ``X-SCALE=(+1.0E+00,   2)`` toma su valor de
``PARINP(2)``, que da el .in (líneas ``PARINP`` tras ``GEOMFN``); GEOMIN no usa
el número escrito. Sin .in, PENGEOM no puede construir la geometría
(«NPINP is too small»), así que para verla ARGUS usa provisionalmente esos
números escritos, avisando de ello.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from .model import EndElement, GeoDocument, IncludeElement, ParamLine


@dataclass
class ParinpUse:
    index: int
    value: float  # primer número escrito para este índice (el que se usa sin .in)
    file: str | None  # fichero y línea (desde 0) de ese primer uso
    line: int | None
    names: list[str] = field(default_factory=list)  # parámetros que lo usan ('X-SCALE', 'THETA'...)
    others: list[float] = field(default_factory=list)  # otros números distintos escritos para el mismo índice


def _param_lines(doc: GeoDocument) -> Iterator[tuple[GeoDocument, ParamLine]]:
    """Líneas de parámetros en el orden en que las lee GEOMIN (los INCLUDE, en su sitio)."""
    for el in doc.elements:
        if isinstance(el, EndElement):
            break
        if isinstance(el, IncludeElement):
            if el.document is not None:
                yield from _param_lines(el.document)
            continue
        for attr in ("params", "transform"):
            for p in getattr(el, attr, None) or ():
                yield doc, p


def parinp_uses(doc: GeoDocument) -> dict[int, ParinpUse]:
    """Índices PARINP que usa la geometría, con el primer número escrito para cada uno."""
    out: dict[int, ParinpUse] = {}
    for d, p in _param_lines(doc):
        if p.ipar <= 0:
            continue
        u = out.get(p.ipar)
        if u is None:
            out[p.ipar] = ParinpUse(p.ipar, float(p.value), d.path, p.line, [p.name])
            continue
        if p.name not in u.names:
            u.names.append(p.name)
        if p.value != u.value and p.value not in u.others:
            u.others.append(float(p.value))
    return dict(sorted(out.items()))


def provisional_parinp(doc: GeoDocument) -> list[float]:
    """PARINP(1..n) con los números escritos en el .geo (0 en los índices que no usa)."""
    uses = parinp_uses(doc)
    if not uses:
        return []
    return [uses[i].value if i in uses else 0.0 for i in range(1, max(uses) + 1)]
