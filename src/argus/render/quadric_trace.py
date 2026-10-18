"""Traza de una cuádrica en el plano del corte 2D (la superficie elegida en «Geometrías complejas», ADR-019).

Las superficies no se ven en el corte (solo los cuerpos que limitan): para construir una geometría bloque a bloque
ayuda ver dónde queda una superficie antes de usarla. Se evalúa ``F(r)`` en una rejilla de píxeles del corte y se
une con segmentos donde cambia de signo (cuadrados en marcha), con interpolación lineal en cada lado.
"""

from __future__ import annotations

import numpy as np

from ..pengeom.quadric import evaluate
from .slice2d import SlicePlane, SliceView


def trace_segments(coef, plane: SlicePlane, view: SliceView, width: int, height: int,
                   step: int = 2) -> np.ndarray:
    """Segmentos ``(x1, y1, x2, y2)`` en píxeles de la curva ``F = 0`` en el corte que se ve."""
    q = np.asarray(coef, dtype=float)
    if q.shape != (10,) or not np.any(q) or width < 2 or height < 2:
        return np.zeros((0, 4))
    xs = np.arange(0.0, width + step, step)
    ys = np.arange(0.0, height + step, step)
    a = view.center[0] + (xs - 0.5 * width) * view.scale
    b = view.center[1] + (0.5 * height - ys) * view.scale
    aa, bb = np.meshgrid(a, b)
    pts = plane.origin[None, None, :] + aa[..., None] * plane.u[None, None, :] + bb[..., None] * plane.v[None, None, :]
    with np.errstate(over="ignore", invalid="ignore"):
        f = evaluate(q, pts[..., 0], pts[..., 1], pts[..., 2])
    f = np.nan_to_num(np.asarray(f, dtype=float), nan=0.0, posinf=1e300, neginf=-1e300)
    pos = f > 0
    f00, f10, f01, f11 = f[:-1, :-1], f[:-1, 1:], f[1:, :-1], f[1:, 1:]
    p00, p10, p01, p11 = pos[:-1, :-1], pos[:-1, 1:], pos[1:, :-1], pos[1:, 1:]
    x0 = xs[None, :-1] + np.zeros_like(f00)
    y0 = ys[:-1, None] + np.zeros_like(f00)

    def frac(fa, fb):
        with np.errstate(divide="ignore", invalid="ignore"):
            t = fa / (fa - fb)
        return np.clip(np.nan_to_num(t, nan=0.5), 0.0, 1.0)

    # Cruces en los cuatro lados de cada celda: arriba, derecha, abajo e izquierda.
    edges = {
        "t": (p00 != p10, x0 + step * frac(f00, f10), y0),
        "r": (p10 != p11, x0 + step, y0 + step * frac(f10, f11)),
        "b": (p01 != p11, x0 + step * frac(f01, f11), y0 + step),
        "l": (p00 != p01, x0, y0 + step * frac(f00, f01)),
    }
    count = sum(e[0].astype(int) for e in edges.values())
    segs = []
    pairs = (("t", "r"), ("t", "b"), ("t", "l"), ("r", "b"), ("r", "l"), ("b", "l"))
    two = count == 2
    for e1, e2 in pairs:
        m = two & edges[e1][0] & edges[e2][0]
        if m.any():
            segs.append(np.stack([edges[e1][1][m], edges[e1][2][m], edges[e2][1][m], edges[e2][2][m]], axis=1))
    four = count == 4  # punto de silla: dos segmentos
    if four.any():
        for e1, e2 in (("t", "r"), ("b", "l")):
            segs.append(np.stack([edges[e1][1][four], edges[e1][2][four], edges[e2][1][four], edges[e2][2][four]],
                                 axis=1))
    return np.concatenate(segs) if segs else np.zeros((0, 4))
