"""Cortes y perfiles de los mapas (dosis, fluencia, EPROF, TAC).

* :func:`plane`: un plano de un mapa 3D (caja o TAC) perpendicular a X, Y o Z.
* :func:`axis_profile`: un perfil paralelo a un eje que pasa por un punto,
  promediando los vóxeles que caen dentro de una anchura alrededor de él.
* :func:`line_profile`: un perfil a lo largo de cualquier segmento (en 3D o en
  el plano R-Z de un mapa cilíndrico), con el valor del vóxel de cada punto o
  interpolado.

Las posiciones son las del propio mapa (para el TAC, las de sus vóxeles, de 0 a
XM). Las incertidumbres de una media de vóxeles se suman en cuadratura, como si
fueran independientes (los vóxeles vecinos están algo correlacionados).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import Curves, Grid, Series

AXES = {"x": 0, "y": 1, "z": 2}


@dataclass
class Plane:
    """Un plano de un mapa: valores (n_u, n_v) sobre los ejes u y v."""

    axis: str  # eje perpendicular
    index: int
    position: float  # centro del plano (cm)
    u: str
    v: str
    u_edges: np.ndarray
    v_edges: np.ndarray
    value: np.ndarray
    err: np.ndarray
    unit: str
    quantity: str


def layer(grid: Grid, name: str | None = None) -> tuple[np.ndarray, np.ndarray, str, str]:
    """Valores, incertidumbres, magnitud y unidad de una capa del mapa (la principal o una de ``extra``)."""
    if name and name in grid.extra:
        v, e, unit = grid.extra[name]
        return v, e, name, unit
    return grid.value, grid.err, grid.quantity, grid.unit


def index_at(edges: np.ndarray, pos: float) -> int:
    i = int(np.searchsorted(edges, pos, side="right") - 1)
    return min(max(i, 0), len(edges) - 2)


def plane(grid: Grid, axis: str, *, index: int | None = None, position: float | None = None,
          layer_name: str | None = None) -> Plane:
    if grid.geometry not in ("box", "ct"):
        raise ValueError("Solo los mapas en caja o en el TAC tienen planos")
    k = AXES[axis]
    edges = grid.edges[k]
    if index is None:
        index = index_at(edges, 0.5 * (edges[0] + edges[-1]) if position is None else position)
    index = min(max(int(index), 0), len(edges) - 2)
    v, e, qty, unit = layer(grid, layer_name)
    sl = [slice(None)] * 3
    sl[k] = index
    others = [a for a in "xyz" if a != axis]
    return Plane(axis, index, float(0.5 * (edges[index] + edges[index + 1])), others[0], others[1],
                 grid.edges[AXES[others[0]]], grid.edges[AXES[others[1]]], v[tuple(sl)], e[tuple(sl)], unit, qty)


def _band(edges: np.ndarray, center: float, width: float) -> np.ndarray:
    """Índices de los intervalos cuyo centro está a menos de width/2 de ``center`` (al menos el que lo contiene)."""
    c = 0.5 * (edges[:-1] + edges[1:])
    sel = np.nonzero(np.abs(c - center) <= 0.5 * width + 1e-12)[0]
    if len(sel) == 0:
        sel = np.array([index_at(edges, center)])
    return sel


def axis_profile(grid: Grid, axis: str, at: tuple[float, ...], width: float = 0.0, *,
                 layer_name: str | None = None) -> Curves:
    """Perfil a lo largo de ``axis`` que pasa por ``at``.

    En una caja o en el TAC, ``at`` son las coordenadas de los otros dos ejes
    (en orden x, y, z); en un cilindro, el eje es 'r' o 'z' y ``at`` es la otra
    coordenada; en una esfera, el eje es 'r' y ``at`` no se usa.
    """
    v, e, qty, unit = layer(grid, layer_name)
    if grid.geometry == "sphere":
        edges = grid.edges[0]
        x = 0.5 * (edges[:-1] + edges[1:])
        return Curves("profile", f"{qty} radial", x, "r", "cm", qty, unit, [Series(qty.lower(), v.copy(), e.copy())],
                      edges=edges, meta={"axis": "r", "source": grid.kind})
    if grid.geometry == "cylinder":
        k = {"r": 0, "z": 1}[axis]
        o = 1 - k
        sel = _band(grid.edges[o], at[0], width)
        vv = np.take(v, sel, axis=o)
        ee = np.take(e, sel, axis=o)
        w = np.take(grid.voxel_volumes(), sel, axis=o)
        y, err = _weighted(vv, ee, w, axis=o)
        edges = grid.edges[k]
        x = 0.5 * (edges[:-1] + edges[1:])
        other = "z" if axis == "r" else "r"
        title = f"{qty} a lo largo de {axis} ({other} = {at[0]:.4g} cm)"
        return Curves("profile", title, x, axis, "cm", qty, unit, [Series(qty.lower(), y, err)], edges=edges,
                      meta={"axis": axis, "at": tuple(at), "width": width, "source": grid.kind})
    if grid.geometry not in ("box", "ct"):
        raise ValueError("Este mapa no tiene perfiles")
    k = AXES[axis]
    others = [a for a in "xyz" if a != axis]
    coords = dict(zip(others, at)) if len(at) == 2 else {a: at[AXES[a]] for a in others}
    sels = {a: _band(grid.edges[AXES[a]], coords[a], width) for a in others}
    sl: list = [slice(None)] * 3
    for a in others:
        sl[AXES[a]] = sels[a]
    vv = v[np.ix_(*[np.arange(v.shape[i]) if isinstance(sl[i], slice) else sl[i] for i in range(3)])]
    ee = e[np.ix_(*[np.arange(e.shape[i]) if isinstance(sl[i], slice) else sl[i] for i in range(3)])]
    axes_avg = tuple(i for i in range(3) if i != k)
    n = np.prod([vv.shape[i] for i in axes_avg])
    y = vv.mean(axis=axes_avg)
    err = np.sqrt((ee ** 2).sum(axis=axes_avg)) / n
    edges = grid.edges[k]
    x = 0.5 * (edges[:-1] + edges[1:])
    where = ", ".join(f"{a} = {coords[a]:.4g}" for a in others)
    title = f"{qty} a lo largo de {axis} ({where} cm)"
    return Curves("profile", title, x, axis, "cm", qty, unit, [Series(qty.lower(), y, err)], edges=edges,
                  meta={"axis": axis, "at": tuple(coords[a] for a in others), "width": width, "source": grid.kind,
                        "voxels": int(n)})


def _weighted(v: np.ndarray, e: np.ndarray, w: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    wt = w.sum(axis=axis)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = np.where(wt > 0, (v * w).sum(axis=axis) / wt, 0.0)
        err = np.where(wt > 0, np.sqrt((e * e * w * w).sum(axis=axis)) / wt, 0.0)
    return y, err


def _interp_regular(edges: tuple[np.ndarray, ...], values: np.ndarray, pts: np.ndarray, nearest: bool
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Valor en ``pts`` (n, d) de una rejilla de celdas: el de la celda (``nearest``) o interpolado entre centros.

    Devuelve (valores, dentro); fuera de la rejilla el valor es NaN.
    """
    d = len(edges)
    inside = np.ones(len(pts), dtype=bool)
    for k in range(d):
        inside &= (pts[:, k] >= edges[k][0]) & (pts[:, k] <= edges[k][-1])
    out = np.full(len(pts), np.nan)
    if nearest:
        idx = [np.clip(np.searchsorted(edges[k], pts[:, k], side="right") - 1, 0, len(edges[k]) - 2) for k in range(d)]
        out[inside] = values[tuple(i[inside] for i in idx)]
        return out, inside
    # Interpolación lineal entre los centros de las celdas (en los bordes, el valor de la celda).
    base = []
    frac = []
    for k in range(d):
        c = 0.5 * (edges[k][:-1] + edges[k][1:])
        if len(c) == 1:
            base.append(np.zeros(len(pts), dtype=np.int64))
            frac.append(np.zeros(len(pts)))
            continue
        t = np.interp(pts[:, k], c, np.arange(len(c), dtype=np.float64))
        i0 = np.clip(np.floor(t).astype(np.int64), 0, len(c) - 2)
        base.append(i0)
        frac.append(np.clip(t - i0, 0.0, 1.0))
    acc = np.zeros(len(pts))
    for corner in range(1 << d):
        w = np.ones(len(pts))
        idx = []
        for k in range(d):
            bit = (corner >> k) & 1
            n = values.shape[k]
            idx.append(np.minimum(base[k] + bit, n - 1))
            w = w * (frac[k] if bit else (1.0 - frac[k]))
        acc += w * values[tuple(idx)]
    out[inside] = acc[inside]
    return out, inside


def line_profile(grid: Grid, p0, p1, n: int | None = None, *, nearest: bool = True,
                 layer_name: str | None = None) -> Curves:
    """Perfil a lo largo del segmento p0→p1 (3D en una caja o en el TAC; (r, z) en un cilindro).

    El eje de la curva es la distancia desde p0 (cm).
    """
    v, e, qty, unit = layer(grid, layer_name)
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    if grid.geometry == "cylinder":
        a, b = a[:2], b[:2]
    elif grid.geometry not in ("box", "ct"):
        raise ValueError("Este mapa no admite perfiles a lo largo de una recta")
    length = float(np.linalg.norm(b - a))
    if length <= 0:
        raise ValueError("El segmento tiene longitud cero")
    if n is None:
        step = min(float(np.min(np.diff(ed))) for ed in grid.edges) / 2.0
        n = int(min(max(length / step, 2), 5000))
    s = np.linspace(0.0, length, n)
    pts = a[None, :] + (b - a)[None, :] * (s / length)[:, None]
    y, inside = _interp_regular(grid.edges, v, pts, nearest)
    err, _ = _interp_regular(grid.edges, e, pts, nearest)
    y = np.where(inside, y, np.nan)
    title = f"{qty} a lo largo de un segmento de {length:.4g} cm"
    return Curves("profile", title, s, "distancia", "cm", qty, unit, [Series(qty.lower(), y, err)], histogram=False,
                  meta={"p0": tuple(a), "p1": tuple(b), "source": grid.kind, "nearest": nearest})


__all__ = ["AXES", "Plane", "axis_profile", "index_at", "layer", "line_profile", "plane"]
