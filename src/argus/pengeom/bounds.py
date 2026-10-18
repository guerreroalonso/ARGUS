"""Caja que contiene la geometría (para encuadrar las vistas).

PENGEOM no necesita conocer la extensión de la geometría, pero el visor sí.
Se estima en dos pasos:

1. Puntos característicos de las superficies cerradas o casi cerradas:
   centro y semiejes de elipsoides y cilindros, punto más cercano al origen
   de los planos y centro de conos e hiperboloides bien condicionados.
2. Muestreo con LOCATE en una rejilla que cubre esos puntos: la caja final
   es la de las celdas que caen en algún cuerpo que no sea la envolvente
   automática (esfera de radio 1e7) ni el exterior.
"""

from __future__ import annotations

import numpy as np

from .compiler import CompiledGeometry


def _surface_points(q: np.ndarray, kplane: int) -> list[np.ndarray]:
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = q
    b = np.array([ax, ay, az])
    quad = np.array([axx, axy, axz, ayy, ayz, azz])
    if kplane or np.max(np.abs(quad)) < 1e-30:
        nn = float(b @ b)
        if nn == 0.0:
            return []
        return [-a0 * b / nn]
    a2 = np.array([[axx, 0.5 * axy, 0.5 * axz],
                   [0.5 * axy, ayy, 0.5 * ayz],
                   [0.5 * axz, 0.5 * ayz, azz]])
    lam, vec = np.linalg.eigh(a2)
    lmax = float(np.max(np.abs(lam)))
    nonzero = np.abs(lam) > 1e-6 * lmax  # casi degenerado = cilindro
    # Centro: mínimos cuadrados en el subespacio no degenerado.
    inv = np.zeros(3)
    rhs = vec.T @ (-0.5 * b)
    inv[nonzero] = rhs[nonzero] / lam[nonzero]
    center = vec @ inv
    # Parte lineal residual en direcciones degeneradas (paraboloides): sin centro.
    if np.any(~nonzero) and np.any(np.abs(rhs[~nonzero]) > 1e-9 * max(1.0, float(np.abs(b).max()))):
        return []
    f0 = float(center @ a2 @ center + b @ center + a0)
    lnz = lam[nonzero]
    same_sign = bool(np.all(lnz > 0) or np.all(lnz < 0))
    if same_sign and f0 != 0.0 and np.sign(f0) != np.sign(lnz[0]):
        pts = [center]
        for i in np.nonzero(nonzero)[0]:
            r = np.sqrt(abs(f0 / lam[i]))
            pts.append(center + r * vec[:, i])
            pts.append(center - r * vec[:, i])
        return pts
    if nonzero.all():
        return [center]  # cono o hiperboloide: solo el centro
    return []


def feature_box(geo: CompiledGeometry) -> tuple[np.ndarray, np.ndarray]:
    pts = []
    for s in geo.surfaces:
        if s.origin == "enclosure" or s.replaced_by is not None:
            continue
        for p in _surface_points(np.asarray(s.q, dtype=float), s.kplane):
            if np.all(np.isfinite(p)):
                pts.append(p)
    if not pts:
        return np.array([-1.0, -1.0, -1.0]), np.array([1.0, 1.0, 1.0])
    arr = np.array(pts)
    lo, hi = arr.min(axis=0), arr.max(axis=0)
    size = hi - lo
    span = float(size.max())
    if span <= 1e-12 * max(1.0, float(np.abs(arr).max())):
        span = 1.0
    # Dimensiones planas (p. ej. solo planos z=cte): dar algo de grosor.
    pad = np.where(size < 1e-3 * span, 0.5 * span, 0.0)
    return lo - pad, hi + pad


def estimate_bounds(geo: CompiledGeometry, samples: int = 40, locator=None,
                    material_only: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Caja [lo, hi] que contiene los cuerpos de la geometría.

    Con ``material_only`` solo cuentan los cuerpos con material (MAT > 0).
    """
    lo, hi = feature_box(geo)
    center = 0.5 * (lo + hi)
    half = 0.5 * (hi - lo) * 1.25
    lo2, hi2 = center - half, center + half
    try:
        if locator is None:
            from .locate import Locator

            locator = Locator(geo)
        axes = [np.linspace(lo2[i], hi2[i], samples) for i in range(3)]
        gx, gy, gz = np.meshgrid(*axes, indexing="ij")
        pts = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)
        ib, mat = locator.locate(pts, np.array([0.5773502691896258, 0.5773502691896258, 0.5773502691896258]))
        enc = geo.nbodys if geo.enclosure_added else -1
        inside = (ib <= geo.nbodys) & (ib != enc)
        if material_only:
            inside &= mat > 0
        if inside.any():
            sel = pts[inside]
            step = (hi2 - lo2) / (samples - 1)
            lo3 = np.maximum(sel.min(axis=0) - step, lo2)
            hi3 = np.minimum(sel.max(axis=0) + step, hi2)
            return lo3, hi3
    except Exception:  # pragma: no cover - la estimación nunca debe romper la carga
        pass
    return lo, hi
