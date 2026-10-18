"""Cortes 2D exactos: cada píxel se localiza con LOCATE (CPU).

No depende de Qt: produce matrices de índices de cuerpo y material y las
colorea en RGBA. La interfaz (``argus.ui.slice_view``) solo las muestra.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .colors import EDGE_COLOR, SELECT_COLOR, hex_to_rgb

AXES = {"x": np.array([1.0, 0.0, 0.0]), "y": np.array([0.0, 1.0, 0.0]), "z": np.array([0.0, 0.0, 1.0])}


@dataclass
class SlicePlane:
    """Plano de corte: ``p(a, b) = origin + a·u + b·v``."""

    origin: np.ndarray
    u: np.ndarray
    v: np.ndarray
    name: str = ""  # 'XY', 'XZ', 'YZ' o '' si es oblicuo
    u_label: str = "x"
    v_label: str = "y"

    @property
    def normal(self) -> np.ndarray:
        n = np.cross(self.u, self.v)
        return n / np.linalg.norm(n)

    def point(self, a: float, b: float) -> np.ndarray:
        return self.origin + a * self.u + b * self.v

    def coords(self, p: np.ndarray) -> tuple[float, float]:
        d = np.asarray(p, dtype=float) - self.origin
        return float(d @ self.u), float(d @ self.v)

    @property
    def depth_axis(self) -> int | None:
        """Índice del eje perpendicular (0,1,2) en los planos XY/XZ/YZ."""
        return {"XY": 2, "XZ": 1, "YZ": 0}.get(self.name)

    @property
    def depth(self) -> float:
        k = self.depth_axis
        return float(self.origin[k]) if k is not None else float(self.origin @ self.normal)

    def with_depth(self, value: float) -> SlicePlane:
        o = self.origin.copy()
        k = self.depth_axis
        if k is not None:
            o[k] = value
        else:
            o = o + (value - self.depth) * self.normal
        return SlicePlane(o, self.u.copy(), self.v.copy(), self.name, self.u_label, self.v_label)


def standard_plane(name: str, depth: float = 0.0) -> SlicePlane:
    """Planos XY (z=cte), XZ (y=cte) y YZ (x=cte), vistos como en PENGEOM."""
    name = name.upper()
    if name == "XY":
        return SlicePlane(np.array([0.0, 0.0, depth]), AXES["x"].copy(), AXES["y"].copy(), "XY", "x", "y")
    if name == "XZ":
        return SlicePlane(np.array([0.0, depth, 0.0]), AXES["x"].copy(), AXES["z"].copy(), "XZ", "x", "z")
    if name == "YZ":
        return SlicePlane(np.array([depth, 0.0, 0.0]), AXES["y"].copy(), AXES["z"].copy(), "YZ", "y", "z")
    raise ValueError(name)


@dataclass
class SliceView:
    """Encuadre: centro (a, b) en coordenadas del plano y escala (unidades/píxel)."""

    plane: SlicePlane
    center: tuple[float, float]
    scale: float

    def pixel_to_plane(self, px: float, py: float, width: int, height: int) -> tuple[float, float]:
        return (self.center[0] + (px - 0.5 * width) * self.scale,
                self.center[1] + (0.5 * height - py) * self.scale)

    def plane_to_pixel(self, a: float, b: float, width: int, height: int) -> tuple[float, float]:
        return (0.5 * width + (a - self.center[0]) / self.scale,
                0.5 * height - (b - self.center[1]) / self.scale)


@dataclass
class SliceResult:
    body: np.ndarray  # (H, W) int32
    mat: np.ndarray  # (H, W) int32
    view: SliceView
    width: int
    height: int
    ctmat: np.ndarray | None = None  # (H, W) material del vóxel del CT (−1 fuera del CT)
    ctden: np.ndarray | None = None  # (H, W) densidad del vóxel (g/cm³)
    ctshade: np.ndarray | None = None  # (H, W) 0..1: densidad dentro del intervalo de su material


def slice_points(view: SliceView, width: int, height: int) -> np.ndarray:
    """Centros de los píxeles en 3D, (H·W, 3)."""
    a = view.center[0] + (np.arange(width) + 0.5 - 0.5 * width) * view.scale
    b = view.center[1] + (0.5 * height - (np.arange(height) + 0.5)) * view.scale
    aa, bb = np.meshgrid(a, b)
    p = view.plane
    pts = p.origin[None, :] + aa.reshape(-1, 1) * p.u[None, :] + bb.reshape(-1, 1) * p.v[None, :]
    return pts


def compute_slice(locator, view: SliceView, width: int, height: int) -> SliceResult:
    pts = slice_points(view, width, height)
    ib, mat = locator.locate(pts, view.plane.normal)
    res = SliceResult(ib.reshape(height, width), mat.reshape(height, width), view, width, height)
    geo = getattr(locator, "geo", None)
    if geo is not None and getattr(geo, "ct", None) is not None and geo.ct_body is not None:
        inct = ib == geo.ct_body
        if inct.any():
            from ..pengeom.ct import voxel_values

            cm, cd = voxel_values(geo, pts, view.plane.normal, mask=inct)
            res.ctmat = cm.reshape(height, width)
            res.ctden = cd.astype(np.float32).reshape(height, width)
            res.ctshade = density_shade(geo.ct, cm, cd).reshape(height, width)
    return res


def density_ranges(ct) -> dict[int, tuple[float, float]]:
    """Densidad mínima y máxima de cada material del CT (se calcula una vez por CT).

    Los materiales de un mismo tejido partido por densidad (según la ficha) comparten el intervalo del tejido
    entero: así el sombreado sigue sin saltos de un trozo al siguiente.
    """
    from ..pengeom.ct import ct_families

    cache = getattr(ct, "_argus_ranges", None)
    if cache is not None:
        return cache
    m = ct.material.ravel()
    d = ct.density.ravel()
    out: dict[int, tuple[float, float]] = {}
    for k in np.unique(m):
        sel = d[m == k]
        out[int(k)] = (float(sel.min()), float(sel.max())) if sel.size else (0.0, 0.0)
    fam = ct_families(ct)
    groups: dict[str, list[int]] = {}
    for k in out:
        if k in fam:
            groups.setdefault(fam[k], []).append(k)
    for members in groups.values():
        if len(members) > 1:
            lo = min(out[k][0] for k in members)
            hi = max(out[k][1] for k in members)
            for k in members:
                out[k] = (lo, hi)
    try:
        object.__setattr__(ct, "_argus_ranges", out)
    except (AttributeError, TypeError):  # pragma: no cover
        pass
    return out


def density_shade(ct, mat: np.ndarray, den: np.ndarray) -> np.ndarray:
    """0..1: dónde está la densidad de cada vóxel dentro del intervalo de las de su material."""
    out = np.full(mat.shape, 0.5, dtype=np.float32)
    ranges = density_ranges(ct)
    for k, (lo, hi) in ranges.items():
        sel = mat == k
        if not sel.any() or hi <= lo:
            continue
        out[sel] = np.clip((den[sel] - lo) / (hi - lo), 0.0, 1.0)
    return out


def edges(a: np.ndarray) -> np.ndarray:
    """Píxeles en los que cambia el valor respecto al vecino derecho o inferior."""
    e = np.zeros(a.shape, dtype=bool)
    e[:, :-1] |= a[:, :-1] != a[:, 1:]
    e[:-1, :] |= a[:-1, :] != a[1:, :]
    return e


HIGHLIGHT_COLOR = (214, 51, 132)  # cuerpos resaltados (p. ej. los de un detector)


def colorize(res: SliceResult, lut: np.ndarray, draw_edges: bool = True,
             selected: int | None = None, dim_others: float = 0.55,
             highlight: set[int] | None = None, mat_lut: np.ndarray | None = None) -> np.ndarray:
    """Imagen RGBA (H, W, 4) uint8 lista para mostrar.

    ``highlight``: cuerpos que se tiñen y se bordean (los de un detector...).
    ``mat_lut``: colores por material para los vóxeles de un CT (con él, cada
    vóxel se pinta con el color de su material, más claro cuanto más denso
    dentro de su material; sin él, el color de la caja CT sombreado igual).
    """
    body = np.clip(res.body, 0, lut.shape[0] - 1)
    rgb = lut[body].astype(np.float32)
    ctm = None
    if res.ctmat is not None:
        ctm = res.ctmat >= 0
        if ctm.any():
            shade = (0.62 + 0.38 * res.ctshade[ctm]) if res.ctshade is not None else 1.0
            if mat_lut is not None:
                base = mat_lut[np.clip(res.ctmat[ctm], 0, mat_lut.shape[0] - 1)].astype(np.float32)
            else:
                base = rgb[ctm]
            rgb[ctm] = base * (shade[:, None] if np.ndim(shade) else shade)
    hl = None
    if highlight:
        hl = np.isin(res.body, np.fromiter(highlight, dtype=np.int64))
        if hl.any():
            col = np.array(HIGHLIGHT_COLOR, dtype=np.float32)
            rgb[hl] = rgb[hl] * 0.5 + col * 0.5
        else:
            hl = None
    if selected is not None:
        sel = res.body == selected
        if sel.any():
            dim = ~sel & (res.body < lut.shape[0] - 1)  # el exterior no se atenúa
            rgb[dim] = rgb[dim] * (1.0 - dim_others) + 255.0 * dim_others
    if draw_edges:
        eb = edges(res.body)
        em = edges(res.mat)
        rgb[eb & ~em] *= 0.72
        rgb[em] = hex_to_rgb(EDGE_COLOR)
        if ctm is not None and ctm.any():
            ev = edges(np.where(ctm, res.ctmat, -2)) & ctm & ~em  # fronteras suaves entre materiales del CT
            rgb[ev] *= 0.8
        if hl is not None:
            rgb[edges(hl)] = HIGHLIGHT_COLOR
        if selected is not None:
            es = edges(res.body == selected)
            rgb[es] = hex_to_rgb(SELECT_COLOR)
    out = np.empty(body.shape + (4,), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out


def nice_length(target: float) -> float:
    """Longitud 'redonda' (1, 2, 5 × 10^n) cercana a ``target``."""
    if target <= 0:
        return 1.0
    e = np.floor(np.log10(target))
    base = target / 10 ** e
    for m in (1.0, 2.0, 5.0, 10.0):
        if base <= m:
            return float(m * 10 ** e)
    return float(10 ** (e + 1))
