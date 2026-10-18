"""Celdas de detectores: un módulo con forma de caja lleno de detectores iguales, en filas, columnas y capas.

Cada detector es un cuerpo (un prisma regular, un cilindro o una caja) con su
etiqueta y la descripción «fila i, columna j» en el .geo: penhmain da la
energía media depositada en cada cuerpo (penhmain-res.dat), y los detectores
de penhmain (``ENDETC``/``IMPDET``) se pueden formar con ellos (véase
:func:`detector_groups`).

Disposición en el plano perpendicular al eje (u, v; ver
:meth:`argus.pengeom.edit.Primitive.plane_axes`), con ``hueco`` entre
caras vecinas:

* ``grid`` (cuadrícula): filas a lo largo de u, una encima de otra en v.
* ``offset`` (filas desplazadas): las filas impares, media separación a la
  derecha. Los hexágonos quedan en panal (con un vértice arriba), los
  círculos al tresbolillo y los triángulos alternan hacia arriba y hacia
  abajo, sin huecos más que el pedido; los cuadrados, los rectángulos y los
  pentágonos, como ladrillos.

PENGEOM limita cada módulo a 249 superficies propias y de sus hijas y a 248
hijas (``NXG``; el número de hijas no lo comprueba: con más, escribe fuera de
sus tablas). Las superficies iguales se escriben una vez (una cara compartida
por toda una fila, las bases de toda una capa) y, si hace falta, los
detectores se agrupan en módulos por capas y por filas (cuando las filas no
encajan unas en otras).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .compiler import NB, NS, NXG, CompiledGeometry
from .edit import (
    MAX_DESCRIPTION,
    PRISM_SIDES,
    REDUCED,
    EditError,
    LabelPool,
    Primitive,
    _clean,
    _direction_angles,
    _insert_before,
    _mother_module_element,
    _set_params,
    check_material,
    editable_body,
    is_top_level,
    local_frame,
    set_description,
)
from .model import BodyElement, ElementKind, GeoDocument, ModuleElement, RefLine, SurfaceElement

CELL_SHAPES = {"prism4": "Cuadrado", "box": "Rectángulo", "cylinder": "Círculo", "prism3": "Triángulo",
               "prism5": "Pentágono", "prism6": "Hexágono"}
LAYOUTS = {"grid": "Cuadrícula", "offset": "Filas desplazadas"}
MAX_DAUGHTERS = NXG - 2  # KDGHT(KB,NXG) cuenta el propio módulo: 249 en total
MAX_MODULE_SURFACES = NXG - 1  # KSURF(KB,NXG): 249
MAX_ROWS = 200
MAX_LAYERS = 100
MAX_ELEMENTS = NB - 10  # PENGEOM: NB = 5000 cuerpos y módulos en toda la geometría
MIN_LENGTH = 1e-6  # cm: medidas, huecos y pared (0 o más); caras más cercanas se tomarían por la misma
SQRT3 = math.sqrt(3.0)


@dataclass
class CellSpec:
    """Lo que se pide: forma y tamaño de cada detector, cuántos y cómo se colocan, y la caja."""

    shape: str = "prism6"  # clave de CELL_SHAPES
    size: float = 1.0  # prismas: radio circunscrito; círculo: radio (cm)
    sides: tuple[float, float] = (1.0, 1.0)  # rectángulo: lados en (u, v) (cm)
    height: float = 1.0  # a lo largo del eje (cm)
    axis: str = "z"
    rows: int = 4  # en v
    cols: int = 4  # en u
    layers: int = 1  # a lo largo del eje
    gap: float = 0.0  # entre caras vecinas en el plano (cm)
    layer_gap: float = 0.0  # entre capas (cm)
    layout: str = "grid"
    margin: float = 0.0  # pared de la caja alrededor de los detectores (cm)
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def problems(self) -> list[str]:
        out = []
        if self.shape not in CELL_SHAPES:
            out.append(f"Forma desconocida: {self.shape}.")
        if self.layout not in LAYOUTS:
            out.append(f"Disposición desconocida: {self.layout}.")
        if self.axis not in ("x", "y", "z"):
            out.append("El eje tiene que ser x, y o z.")
        nums = [self.size, self.height, *self.sides, self.gap, self.layer_gap, self.margin, *self.center]
        if not all(isinstance(v, int | float) and math.isfinite(v) for v in nums):
            out.append("Hay medidas que no son números.")
            return out
        small = f"{MIN_LENGTH:g} cm"
        if self.shape == "box" and not all(s >= MIN_LENGTH for s in self.sides):
            out.append(f"Los lados de cada detector tienen que ser de al menos {small}.")
        if self.shape != "box" and not self.size >= MIN_LENGTH:
            out.append(f"El tamaño de cada detector tiene que ser de al menos {small}.")
        if not self.height >= MIN_LENGTH:
            out.append(f"La altura de cada detector tiene que ser de al menos {small}.")
        if min(self.gap, self.layer_gap, self.margin) < 0:
            out.append("Los huecos y la pared no pueden ser negativos.")
        elif any(0 < v < MIN_LENGTH for v in (self.gap, self.layer_gap, self.margin)):
            out.append(f"Los huecos y la pared tienen que ser 0 o de al menos {small} (PENGEOM no distinguiría "
                       "caras tan cercanas).")
        for name, n, top in (("filas", self.rows, MAX_ROWS), ("columnas", self.cols, MAX_ROWS),
                             ("capas", self.layers, MAX_LAYERS)):
            if not isinstance(n, int) or not 1 <= n <= top:
                out.append(f"El número de {name} tiene que estar entre 1 y {top}.")
        if not out and self.count > MAX_ELEMENTS:
            out.append(f"Serían {self.count} detectores y PENGEOM admite {NB} cuerpos en toda la geometría: use "
                       "menos filas, columnas o capas.")
        return out

    @property
    def count(self) -> int:
        return int(self.rows) * int(self.cols) * int(self.layers)

    def axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return Primitive("cylinder", axis=self.axis).plane_axes()


@dataclass
class CellElement:
    row: int  # desde 1, en v
    col: int  # desde 1, en u
    layer: int  # desde 1, a lo largo del eje
    prim: Primitive


@dataclass
class CellPlan:
    """Dónde va cada detector, la caja y cómo se agrupan en módulos."""

    spec: CellSpec
    elements: list[CellElement] = field(default_factory=list)
    housing: Primitive | None = None
    structure: str = "plano"  # 'plano', 'capas', 'filas' o 'capas y filas'
    surfaces: int = 0  # superficies nuevas (las iguales, una vez)
    modules: int = 1  # módulos nuevos (la caja y, si hace falta, los de las capas y las filas)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def size(self) -> np.ndarray:
        """Medidas de la caja (cm) en x, y, z."""
        return np.asarray(self.housing.sides, dtype=float) if self.housing is not None else np.zeros(3)


# ---------------------------------------------------------------------------
# Disposición
# ---------------------------------------------------------------------------

def _element(spec: CellSpec, center: np.ndarray, angle: float) -> Primitive:
    c = tuple(_clean(float(v)) for v in center)
    if spec.shape == "cylinder":
        return Primitive("cylinder", center=c, radius=float(spec.size), height=float(spec.height), axis=spec.axis)
    if spec.shape == "box":
        k = "xyz".index(spec.axis)
        sides = [0.0, 0.0, 0.0]
        sides[k], sides[(k + 1) % 3], sides[(k + 2) % 3] = float(spec.height), *(float(s) for s in spec.sides)
        return Primitive("box", center=c, sides=tuple(sides))
    return Primitive(spec.shape, center=c, radius=float(spec.size), height=float(spec.height), axis=spec.axis,
                     angle=float(angle))


def _extent_uv(spec: CellSpec, angle: float) -> tuple[float, float, float, float]:
    """Hasta dónde llega el detector desde su centro: (−u, +u, −v, +v). Un triángulo o un pentágono no llega lo
    mismo hacia su vértice que hacia la cara de enfrente."""
    if spec.shape == "cylinder":
        r = float(spec.size)
        return r, r, r, r
    if spec.shape == "box":
        hu, hv = 0.5 * float(spec.sides[0]), 0.5 * float(spec.sides[1])
        return hu, hu, hv, hv
    p = Primitive(spec.shape, radius=float(spec.size), height=1.0, axis="z", angle=float(angle))
    return p.reach(0, -1), p.reach(0, +1), p.reach(1, -1), p.reach(1, +1)


def interlocks(spec: CellSpec) -> bool:
    """¿Las filas encajan unas en otras (no se pueden separar en módulos con forma de caja)?"""
    return spec.layout == "offset" and spec.shape in ("prism6", "cylinder", "prism3")


def layout_positions(spec: CellSpec) -> list[tuple[int, int, float, float, float]]:
    """(fila, columna, u, v, giro) de cada detector de una capa, sin centrar (el primero cerca del origen)."""
    g = float(spec.gap)
    out = []
    if spec.layout == "offset" and spec.shape == "prism6":
        # Panal: un vértice arriba (giro de 30°); vecinos en una fila, cara con cara a 2·apotema + hueco.
        r = float(spec.size) * math.cos(math.pi / 6)
        pu = 2.0 * r + g
        pv = pu * SQRT3 / 2.0
        for i in range(spec.rows):
            for j in range(spec.cols):
                out.append((i + 1, j + 1, j * pu + (0.5 * pu if i % 2 else 0.0), i * pv, 30.0))
        return out
    if spec.layout == "offset" and spec.shape == "cylinder":
        pu = 2.0 * float(spec.size) + g
        pv = pu * SQRT3 / 2.0
        for i in range(spec.rows):
            for j in range(spec.cols):
                out.append((i + 1, j + 1, j * pu + (0.5 * pu if i % 2 else 0.0), i * pv, 0.0))
        return out
    if spec.layout == "offset" and spec.shape == "prism3":
        # Triángulos alternos (hacia arriba y hacia abajo) que llenan cada fila; las filas impares, desplazadas
        # medio lado. Se colocan como las piezas de un mosaico de lado s + √3·hueco y cada una se encoge el hueco:
        # entre caras vecinas queda el hueco pedido.
        r = float(spec.size) * math.cos(math.pi / 3)  # apotema
        st = 2.0 * SQRT3 * (r + 0.5 * g)  # lado de la pieza del mosaico
        ht = st * SQRT3 / 2.0
        for i in range(spec.rows):
            x0 = 0.5 * st if i % 2 else 0.0
            for j in range(spec.cols):
                up = j % 2 == 0
                v = i * ht + (ht / 3.0 if up else 2.0 * ht / 3.0)
                out.append((i + 1, j + 1, x0 + (j + 1) * st / 2.0, v, 0.0 if up else 180.0))
        return out
    # Cuadrícula (y ladrillos con «offset»): la separación es la anchura del detector más el hueco.
    angle = 0.0
    mu, pu_, mv, pv_ = _extent_uv(spec, angle)
    pu, pv = mu + pu_ + g, mv + pv_ + g
    for i in range(spec.rows):
        for j in range(spec.cols):
            shift = 0.5 * pu if (spec.layout == "offset" and i % 2) else 0.0
            out.append((i + 1, j + 1, j * pu + shift, i * pv, angle))
    return out


def plan_cell(spec: CellSpec) -> CellPlan:
    """Detectores y caja de la celda (sin tocar ninguna geometría)."""
    plan = CellPlan(spec)
    plan.problems = spec.problems()
    if plan.problems:
        return plan
    a, u, v = spec.axes()
    pos = layout_positions(spec)
    by_angle = {ang: _extent_uv(spec, ang) for ang in {p[4] for p in pos}}
    ext = np.array([by_angle[p[4]] for p in pos])
    us = np.array([p[2] for p in pos])
    vs = np.array([p[3] for p in pos])
    lo_u, hi_u = float(np.min(us - ext[:, 0])), float(np.max(us + ext[:, 1]))
    lo_v, hi_v = float(np.min(vs - ext[:, 2])), float(np.max(vs + ext[:, 3]))
    mid_u, mid_v = 0.5 * (lo_u + hi_u), 0.5 * (lo_v + hi_v)
    h = float(spec.height)
    lg = float(spec.layer_gap)
    depth = spec.layers * h + (spec.layers - 1) * lg
    c = np.asarray(spec.center, dtype=float)
    for k in range(spec.layers):
        w = -0.5 * depth + 0.5 * h + k * (h + lg)
        for (i, j, pu, pv, ang) in pos:
            center = c + (pu - mid_u) * u + (pv - mid_v) * v + w * a
            plan.elements.append(CellElement(i, j, k + 1, _element(spec, center, ang)))
    m = float(spec.margin)
    size_u, size_v, size_a = hi_u - lo_u + 2 * m, hi_v - lo_v + 2 * m, depth + 2 * m
    sides = [0.0, 0.0, 0.0]
    kk = "xyz".index(spec.axis)
    sides[kk], sides[(kk + 1) % 3], sides[(kk + 2) % 3] = size_a, size_u, size_v
    plan.housing = Primitive("box", center=tuple(_clean(float(x)) for x in c),
                             sides=tuple(_clean(float(s)) for s in sides))
    _choose_structure(plan)
    return plan


# ---------------------------------------------------------------------------
# Superficies (cada plano, una vez) y módulos
# ---------------------------------------------------------------------------
#
# PENGEOM sigue a las partículas (STEP) cruzando superficies: si dos superficies distintas coinciden (la cara de un
# detector y la pared de la caja, o las bases de dos capas que se tocan), a veces cruza una y no se entera de la
# otra, y la partícula sigue en el cuerpo equivocado hasta la siguiente superficie. Por eso todas las caras planas
# se escriben como planos sueltos y cada plano del espacio es una sola superficie (con la normal en un sentido fijo;
# cada cuerpo lo nombra con su lado), y los módulos intermedios llegan hasta las paredes de la caja (usan sus
# mismos planos) y se separan a mitad del hueco entre capas o filas.

PLANE_QUANTUM = 1e-8  # cm: dos planos paralelos más cerca que esto son el mismo


def _canonical_plane(n: np.ndarray, d: float) -> tuple[np.ndarray, float, int]:
    """(n, d, s): el plano n·x = d con la normal en su sentido fijo (primera componente apreciable positiva);
    s = −1 si se le ha dado la vuelta."""
    for c in n:
        if abs(c) > 1e-6:
            return (n, d, 1) if c > 0 else (-n, -d, -1)
    return n, d, 1


def _q(x) -> tuple:
    return tuple(float(v) + 0.0 for v in np.round(np.atleast_1d(np.asarray(x, dtype=float)), 9))


def _plane_text(n: np.ndarray, d: float) -> str:
    k = int(np.argmax(np.abs(n)))
    if abs(abs(float(n[k])) - 1.0) < 1e-12:
        return f"plano {'xyz'[k]} = {_clean(d * float(np.sign(n[k])), 9):g}"
    return f"plano n=({', '.join(f'{_clean(float(c), 4):g}' for c in n)}) a {_clean(d, 6):g}"


class _Surfaces:
    """Superficies de la celda: planos sueltos (uno por cada plano del espacio) y los cilindros, escritos en el
    sistema del contenedor ``frame`` = (R, d) (véase ``local_frame``)."""

    def __init__(self, frame):
        rot, shift = frame if frame is not None else (np.eye(3), np.zeros(3))
        self.frame = frame
        self.rot = np.asarray(rot, dtype=float)
        self.shift = np.asarray(shift, dtype=float)
        self.items: list[tuple[tuple, dict[str, float], str]] = []
        self.planes: dict[tuple, int] = {}
        self.shapes: dict[tuple, tuple[list[np.ndarray], float]] = {}

    def plane(self, n: np.ndarray, d: float) -> tuple[int, int]:
        """(índice, puntero de lado) del semiespacio n·x < d (en el laboratorio; ``n`` unitario)."""
        n, d, s = _canonical_plane(np.asarray(n, dtype=float), float(d))
        key = _q(n)
        cell = math.floor(d / PLANE_QUANTUM)
        for c in (cell, cell - 1, cell + 1):
            k = self.planes.get((key, c))
            if k is not None:
                return k, -s
        k = len(self.items)
        # En el sistema del contenedor: x = R·x_l + d  =>  n_l = Rᵀ·n y el punto d_l·n_l del plano.
        n_l = self.rot.T @ n
        d_l = d - float(n @ self.shift)
        p = d_l * n_l
        params = {**_direction_angles(n_l), "X-SHIFT": _clean(float(p[0])), "Y-SHIFT": _clean(float(p[1])),
                  "Z-SHIFT": _clean(float(p[2]))}
        self.items.append(((0, 0, 0, 1, 0), {k2: v for k2, v in params.items() if v}, _plane_text(n, d)))
        self.planes[(key, cell)] = k
        return k, -s

    def quadric(self, idx: tuple, params: dict[str, float], desc: str) -> int:
        self.items.append((idx, params, desc))
        return len(self.items) - 1

    def _faces(self, prim: Primitive) -> tuple[list[np.ndarray], float]:
        """Normales exteriores de las caras laterales de un prisma y su distancia al centro (una vez por forma)."""
        key = (prim.kind, float(prim.radius), prim.axis, float(prim.angle))
        got = self.shapes.get(key)
        if got is None:
            got = self.shapes[key] = (prim.face_normals(), prim.apothem)
        return got

    def refs(self, prim: Primitive) -> list[tuple[int, int]]:
        """(índice de la superficie, puntero de lado) de las superficies de ``prim`` (prisma, caja o cilindro)."""
        c = np.asarray(prim.center, dtype=float)
        out: list[tuple[int, int]] = []
        if prim.kind == "box":
            half = 0.5 * np.asarray(prim.sides, dtype=float)
            for k in range(3):
                e = np.zeros(3)
                e[k] = 1.0
                out.append(self.plane(-e, -(c[k] - half[k])))
                out.append(self.plane(e, c[k] + half[k]))
            return out
        a = prim.axis_vector
        h = 0.5 * float(prim.height)
        if prim.kind == "cylinder":
            idx, params, side, desc = prim.surfaces(self.frame)[0]
            out.append((self.quadric(idx, params, desc), side))
        else:
            normals, ap = self._faces(prim)
            out.extend(self.plane(n, float(n @ c) + ap) for n in normals)
        out.append(self.plane(-a, -(float(a @ c) - h)))
        out.append(self.plane(a, float(a @ c) + h))
        return out


def _groups(plan: CellPlan, how: str) -> list[list[int]]:
    """Índices de los detectores de cada módulo intermedio: por capas o por filas de cada capa."""
    out: dict[tuple, list[int]] = {}
    for n, e in enumerate(plan.elements):
        key = (e.layer,) if how == "capas" else (e.layer, e.row)
        out.setdefault(key, []).append(n)
    return list(out.values())


def _box_between(plan: CellPlan, lo: np.ndarray, hi: np.ndarray) -> Primitive:
    c = 0.5 * (lo + hi)
    return Primitive("box", center=tuple(float(x) for x in c), sides=tuple(float(x) for x in hi - lo))


def _bands(plan: CellPlan, groups: list[list[int]], how: str, parent: tuple[np.ndarray, np.ndarray] | None = None,
           ) -> list[Primitive]:
    """Las cajas de los módulos intermedios: capas (a lo largo del eje) o filas (a lo largo de v), una junto a
    otra y separadas a mitad del hueco entre sus detectores, con las primeras y las últimas hasta las paredes de la
    caja; en las otras direcciones, todo lo ancho de la caja (o de la capa ``parent`` = (lo, hi) en la que están)."""
    spec = plan.spec
    a, _u, v = spec.axes()
    lo, hi = plan.housing.box() if parent is None else parent
    d = a if how == "capas" else v
    extents: dict[float, tuple] = {}

    def reach(n: int) -> tuple[float, float]:
        """Hasta dónde llega el detector ``n`` a lo largo de ``d``."""
        p = plan.elements[n].prim
        x = float(np.asarray(p.center) @ d)
        if how == "capas":
            h = 0.5 * float(spec.height)
            return x - h, x + h
        if p.angle not in extents:
            extents[p.angle] = _extent_uv(spec, p.angle)
        _mu, _pu, mv, pv = extents[p.angle]
        return x - mv, x + pv

    spans = []
    for g in groups:
        r = [reach(n) for n in g]
        spans.append((min(x for x, _ in r), max(y for _, y in r)))
    order = sorted(range(len(groups)), key=lambda i: spans[i][0])
    walls = [float(lo @ d)] + [0.5 * (spans[order[i]][1] + spans[order[i + 1]][0]) for i in range(len(order) - 1)] \
        + [float(hi @ d)]
    out: list[Primitive | None] = [None] * len(groups)
    for pos, i in enumerate(order):
        b_lo, b_hi = lo.copy(), hi.copy()
        b_lo = b_lo + (walls[pos] - float(b_lo @ d)) * d
        b_hi = b_hi + (walls[pos + 1] - float(b_hi @ d)) * d
        out[i] = _box_between(plan, b_lo, b_hi)
    return out


@dataclass
class _Structure:
    """Una agrupación de los detectores en módulos con sus superficies (índices de un :class:`_Surfaces`)."""

    how: str  # 'plano', 'capas' o 'filas' (con varias capas, las filas van en módulos de capa)
    surfaces: int = 0  # superficies distintas que se escriben
    modules: int = 1
    problems: list[str] = field(default_factory=list)
    groups: list[list[int]] = field(default_factory=list)  # detectores de cada módulo intermedio (capa o fila)
    group_refs: list[list[tuple[int, int]]] = field(default_factory=list)
    layers: list[list[int]] = field(default_factory=list)  # con filas y varias capas: detectores de cada capa
    layer_refs: list[list[tuple[int, int]]] = field(default_factory=list)


MAX_TOP_SURFACES = NXG - 2  # la caja: si no tiene madre, GEOMIN le añade la esfera de la envolvente sin comprobar


def _structure(plan: CellPlan, how: str, surf: _Surfaces, elem: list[set[int]], cell: set[int]) -> _Structure:
    """Cuenta como GEOMIN: cada módulo reúne sus superficies y las propias de sus hijas (no las que estas heredan
    de las suyas), sin repetir, y cuenta sus hijas."""
    st = _Structure(how)
    used = set(cell)
    for e in elem:
        used |= e

    def keys(refs) -> set[int]:
        return {k for k, _s in refs}

    def check(own: set[int], daughters: list[set[int]], what: str, most: int = MAX_MODULE_SURFACES) -> None:
        ks = set(own)
        for d in daughters:
            ks |= d
        used.update(own)
        if len(ks) > most:
            st.problems.append(f"{what} tendría {len(ks)} superficies")
        if len(daughters) > MAX_DAUGHTERS:
            st.problems.append(f"{what} tendría {len(daughters)} hijas")

    if how == "plano":
        check(cell, elem, "la caja", MAX_TOP_SURFACES)
    elif how == "capas" or plan.spec.layers == 1:
        st.groups = _groups(plan, how)
        st.group_refs = [surf.refs(b) for b in _bands(plan, st.groups, how)]
        own = [keys(r) for r in st.group_refs]
        for g, o in zip(st.groups, own):
            check(o, [elem[n] for n in g], "una capa" if how == "capas" else "una fila")
        check(cell, own, "la caja", MAX_TOP_SURFACES)
        st.modules = 1 + len(st.groups)
    else:  # filas dentro de capas
        st.layers = _groups(plan, "capas")
        layer_boxes = _bands(plan, st.layers, "capas")
        st.layer_refs = [surf.refs(b) for b in layer_boxes]
        layer_own = [keys(r) for r in st.layer_refs]
        for lg, lbox, lo_ in zip(st.layers, layer_boxes, layer_own):
            members = set(lg)
            rows = [g for g in _groups(plan, "filas") if g[0] in members]
            refs = [surf.refs(b) for b in _bands(plan, rows, "filas", lbox.box())]
            st.groups.extend(rows)
            st.group_refs.extend(refs)
            row_own = [keys(r) for r in refs]
            for g, o in zip(rows, row_own):
                check(o, [elem[n] for n in g], "una fila")
            check(lo_, row_own, "una capa")
        check(cell, layer_own, "la caja", MAX_TOP_SURFACES)
        st.modules = 1 + len(st.groups) + len(st.layers)
    st.surfaces = len(used)
    return st


def _options(spec: CellSpec) -> list[str]:
    options = ["plano"]
    if spec.layers > 1:
        options.append("capas")
    if not interlocks(spec):
        options.append("filas")
    return options


def _structure_name(spec: CellSpec, how: str) -> str:
    return "capas y filas" if how == "filas" and spec.layers > 1 else how


def _choose_structure(plan: CellPlan) -> None:
    """La agrupación más sencilla que cabe en los límites de PENGEOM (NXG)."""
    spec = plan.spec
    surf = _Surfaces(None)
    elem = [{k for k, _s in surf.refs(e.prim)} for e in plan.elements]
    cell = {k for k, _s in surf.refs(plan.housing)}
    last: list[str] = []
    for how in _options(spec):
        st = _structure(plan, how, surf, elem, cell)
        if not st.problems:
            plan.structure = _structure_name(spec, how)
            plan.surfaces, plan.modules = st.surfaces, st.modules
            if how != "plano":
                plan.notes.append(f"Los detectores se agrupan en módulos por {plan.structure} (PENGEOM admite 248 "
                                  "hijas y 249 superficies por módulo).")
            return
        last = st.problems
    why = "; ".join(dict.fromkeys(last))
    tip = ("con esta disposición las filas encajan unas en otras y no se pueden separar en módulos: use menos "
           "detectores por capa o la cuadrícula" if interlocks(spec) else "use menos detectores por capa o por fila")
    plan.problems.append(f"PENGEOM no admitiría la celda ({why}; como mucho, 248 hijas y 249 superficies por "
                         f"módulo, 248 en la caja): {tip}.")


# ---------------------------------------------------------------------------
# Escribir la celda en la geometría
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    cell: str  # etiqueta del módulo de la celda
    elements: list[str]  # etiquetas de los detectores, en el orden de ``plan.elements``
    modules: list[str]  # etiquetas de los módulos intermedios


def total_problem(geo: CompiledGeometry, plan: CellPlan) -> str | None:
    """Por qué no cabría la celda en toda la geometría (PENGEOM: NB cuerpos y módulos y NS superficies, con sitio
    para la envolvente si todavía no la tiene), o None. Los clones del contenedor los cuenta la compilación."""
    spare = 0 if geo.enclosure_added else 1
    bodies = geo.nbodys + len(plan.elements) + plan.modules
    if bodies > NB - spare:
        return (f"La geometría tendría {bodies} cuerpos y módulos y PENGEOM admite {NB - spare} (NB"
                + (", con la envolvente" if spare else "") + "): use menos detectores.")
    surfaces = geo.nsurf + plan.surfaces
    if surfaces > NS - spare:
        return (f"La geometría tendría {surfaces} superficies y PENGEOM admite {NS - spare} (NS"
                + (", con la envolvente" if spare else "") + "): use menos detectores.")
    if geo.document is not None:
        for kind, need, what in (("body", len(plan.elements) + plan.modules, "cuerpos y módulos"),
                                 ("surface", plan.surfaces, "superficies")):
            free = LabelPool(geo.document, kind, geo).free()
            if need > free:
                return (f"Harían falta {need} etiquetas de {what} y solo quedan {free} libres (de 1 a 9999): use "
                        "menos detectores.")
    return None


def _described(title: str, where: str) -> str:
    """«título: dónde», recortando el título (no el dónde) para que quepa en la descripción del .geo."""
    room = MAX_DESCRIPTION - len(where) - 2
    title = " ".join(title.split())
    if len(title) > room:
        title = title[:max(room, 0)].rstrip()
    return f"{title}: {where}" if title else where


def add_cell(doc: GeoDocument, geo: CompiledGeometry, plan: CellPlan, element_material: int,
             housing_material: int, container: int, name: str = "", parinp=None) -> CellResult:
    """Añade la celda (un módulo con los detectores dentro) en ``container``, como ``add_primitive``."""
    if not plan.ok:
        raise EditError(plan.problems[0])
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    check_material(element_material)
    check_material(housing_material)
    if element_material == 0:
        raise EditError("Los detectores no pueden ser de vacío (material 0): penhmain no los admite en un detector.")
    problem = total_problem(geo, plan)
    if problem:
        raise EditError(problem)
    enclosure = is_top_level(geo, container)
    cont_el = None if enclosure else editable_body(geo, container)
    frame = local_frame(geo, container, parinp) if not enclosure else None
    # Las superficies, en el sistema del contenedor (las iguales, una vez) y, por si acaso, los límites de PENGEOM
    # otra vez con ellas.
    surf = _Surfaces(frame)
    elem_refs = [surf.refs(e.prim) for e in plan.elements]
    cell_refs = surf.refs(plan.housing)
    how = {"plano": "plano", "capas": "capas", "filas": "filas", "capas y filas": "filas"}[plan.structure]
    st = _structure(plan, how, surf, [{k for k, _s in r} for r in elem_refs], {k for k, _s in cell_refs})
    if st.problems:
        raise EditError(f"PENGEOM no admitiría la celda: {st.problems[0]}.")
    groups, band_refs, layer_groups, layer_refs = st.groups, st.group_refs, st.layers, st.layer_refs
    slabels = LabelPool(doc, "surface", geo)
    blabels = LabelPool(doc, "body", geo)
    s_lab = [slabels.next() for _ in surf.items]
    new_els = []
    for (idx, params, desc), lab in zip(surf.items, s_lab):
        s = SurfaceElement(ElementKind.SURFACE, label=lab, indices=idx, params=_set_params([], params, REDUCED),
                           modified=True)
        set_description(s, desc)
        new_els.append(s)
    cell_label = blabels.next()
    title = name.strip() or f"celda {cell_label.strip()}"
    spec = plan.spec
    multi = spec.layers > 1

    def refs_of(pairs):
        return [RefLine("SURFACE", s_lab[k], side) for k, side in pairs]

    labels = []
    for e, pairs in zip(plan.elements, elem_refs):
        lab = blabels.next()
        body = BodyElement(ElementKind.BODY, label=lab, material=int(element_material), items=refs_of(pairs),
                           modified=True)
        where = f"fila {e.row}, columna {e.col}" + (f", capa {e.layer}" if multi else "")
        set_description(body, _described(title, where))
        new_els.append(body)
        labels.append(lab)
    mids: list[str] = []
    group_label: dict[int, str] = {}
    for g, pairs in zip(groups, band_refs):
        lab = blabels.next()
        items = refs_of(pairs) + [RefLine("BODY", labels[n]) for n in g]
        mod = ModuleElement(ElementKind.MODULE, label=lab, material=int(housing_material), items=items, modified=True)
        e0 = plan.elements[g[0]]
        what = f"capa {e0.layer}" if how == "capas" else (f"fila {e0.row}" + (f", capa {e0.layer}" if multi else ""))
        set_description(mod, _described(title, what))
        new_els.append(mod)
        mids.append(lab)
        for n in g:
            group_label[n] = lab
    top_items: list[RefLine] = []
    if layer_groups:
        for g, pairs in zip(layer_groups, layer_refs):
            lab = blabels.next()
            rows_here = list(dict.fromkeys(group_label[n] for n in g))
            items = refs_of(pairs) + [RefLine("MODULE", r) for r in rows_here]
            mod = ModuleElement(ElementKind.MODULE, label=lab, material=int(housing_material), items=items,
                                modified=True)
            set_description(mod, _described(title, f"capa {plan.elements[g[0]].layer}"))
            new_els.append(mod)
            mids.append(lab)
            top_items.append(RefLine("MODULE", lab))
    elif groups:
        top_items = [RefLine("MODULE", group_label[g[0]]) for g in groups]
    else:
        top_items = [RefLine("BODY", lab) for lab in labels]
    cell = ModuleElement(ElementKind.MODULE, label=cell_label, material=int(housing_material),
                         items=refs_of(cell_refs) + top_items, modified=True)
    shape = CELL_SHAPES[spec.shape].lower()
    default = (f"celda de detectores: {spec.rows}×{spec.cols}" + (f"×{spec.layers}" if multi else "")
               + f" {shape}s")
    set_description(cell, name.strip() or default)
    new_els.append(cell)
    _insert_before(doc, cont_el, new_els)
    if cont_el is not None:
        cont_el.items.append(RefLine("MODULE", cell_label))
        cont_el.modified = True
        if isinstance(cont_el, BodyElement):
            mother = _mother_module_element(doc, cont_el.label)
            if mother is not None:
                mother.items.append(RefLine("MODULE", cell_label))
                mother.modified = True
    return CellResult(cell_label, labels, mids)


# ---------------------------------------------------------------------------
# Detectores de penhmain con los de la celda
# ---------------------------------------------------------------------------

DETECTOR_LIMITS = {"energy": (5, 100), "impact": (5, 50)}  # detectores y cuerpos por detector (penhmain.f)
GROUPINGS = {"cell": "Un detector con toda la celda", "rows": "Uno por fila", "cols": "Uno por columna",
             "layers": "Uno por capa", "each": "Uno por cada detector de la celda", "none": "Ninguno"}


def detector_groups(plan: CellPlan, how: str) -> list[list[int]]:
    """Índices de los detectores de la celda que van en cada detector de penhmain."""
    if how == "none" or not plan.elements:
        return []
    if how == "cell":
        return [list(range(len(plan.elements)))]
    if how == "each":
        return [[n] for n in range(len(plan.elements))]
    key = {"rows": lambda e: (e.layer, e.row), "cols": lambda e: (e.layer, e.col), "layers": lambda e: e.layer}[how]
    out: dict = {}
    for n, e in enumerate(plan.elements):
        out.setdefault(key(e), []).append(n)
    return list(out.values())


def detector_problem(plan: CellPlan, how: str, kind: str, existing: int) -> str | None:
    """Por qué penhmain no admitiría esos detectores (``existing``: los de ese tipo que ya tiene el .in)."""
    if how == "none":
        return None
    max_det, max_bodies = DETECTOR_LIMITS[kind]
    groups = detector_groups(plan, how)
    name = "de energía depositada" if kind == "energy" else "de impacto"
    free = max_det - existing
    if len(groups) > free:
        have = f" y el .in ya tiene {existing}" if existing else ""
        return (f"Harían falta {len(groups)} detectores {name} y penhmain admite {max_det}{have}: agrupe los "
                "detectores de otra forma (por ejemplo, toda la celda en uno) o use menos.")
    big = max((len(g) for g in groups), default=0)
    if big > max_bodies:
        return (f"Un detector {name} tendría {big} cuerpos y penhmain admite {max_bodies} por detector: agrupe los "
                "detectores de otra forma (por filas o por capas) o use menos.")
    return None


def suggest_grouping(plan: CellPlan, kind: str, existing: int) -> str:
    """La agrupación más sencilla que cabe en penhmain (``none`` si ninguna)."""
    for how in ("cell", "layers", "rows", "cols", "each"):
        if how == "layers" and plan.spec.layers < 2:
            continue
        if detector_problem(plan, how, kind, existing) is None:
            return how
    return "none"


__all__ = ["CELL_SHAPES", "CellElement", "CellPlan", "CellResult", "CellSpec", "DETECTOR_LIMITS", "GROUPINGS",
           "LAYOUTS", "MAX_DAUGHTERS", "MAX_ELEMENTS", "MAX_MODULE_SURFACES", "PRISM_SIDES", "add_cell",
           "detector_groups", "detector_problem", "interlocks", "layout_positions", "plan_cell", "suggest_grouping",
           "total_problem"]
