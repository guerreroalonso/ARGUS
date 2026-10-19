"""Caja que contiene la geometría (para encuadrar las vistas).

PENGEOM no necesita conocer la extensión de la geometría, pero el visor sí.
Se estima en dos pasos:

1. Puntos característicos de las superficies cerradas o casi cerradas:
   centro y semiejes de elipsoides y cilindros, punto más cercano al origen
   de los planos y centro de conos e hiperboloides bien condicionados.
2. Muestreo con LOCATE en una rejilla que cubre esos puntos: la caja final
   es la de las celdas que caen en algún cuerpo que no sea la envolvente
   automática (esfera de radio 1e7) ni el exterior.
3. (Undécima revisión.) La unión con la caja de cada cuerpo que sus
   superficies acotan: la de cada superficie y, si alguna está girada, la
   exacta (``extent.exact_box``). La rejilla no veía lo fino ni las esquinas
   de lo girado: la copa de PenGeomJar quedaba sin el pie y el tallo, y la
   fuente de «Nuevo .in» caía dentro del tallo.
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
        found = None
        if inside.any():
            sel = pts[inside]
            step = (hi2 - lo2) / (samples - 1)
            found = (np.maximum(sel.min(axis=0) - step, lo2), np.minimum(sel.max(axis=0) + step, hi2))
        exact = body_boxes(geo, material_only)
        if exact is not None:
            if found is None:
                return exact
            return np.minimum(found[0], exact[0]), np.maximum(found[1], exact[1])
        if found is not None:
            return found
    except Exception:  # pragma: no cover - la estimación nunca debe romper la carga
        pass
    return lo, hi


def body_boxes(geo: CompiledGeometry, material_only: bool = False) -> tuple[np.ndarray, np.ndarray] | None:
    """La unión de las cajas de los elementos de arriba (las hijas de la raíz o de la envolvente, y la raíz si no es la
    envolvente) que sus superficies acotan: la de cada superficie (al momento) y, si no basta (superficies giradas),
    la exacta; de un módulo sin cota, las de lo que tiene dentro. Lo de dentro de un módulo acotado ya está en su caja.
    None si no hay ninguno."""
    from .extent import analytic_box, exact_box

    n = geo.nbodys
    enc = n if geo.enclosure_added else -1
    root = n  # (la envolvente o el módulo de arriba: el último)
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    stack = [k for k in range(1, n + 1) if geo.body(k).mother == root and k != root]
    if root != enc and 1 <= root <= n:
        stack.append(root)
    seen: set[int] = set()
    while stack:
        kb = stack.pop()
        if kb in seen or not 1 <= kb <= n:
            continue
        seen.add(kb)
        b = geo.body(kb)
        if kb == enc or b.origin == "enclosure":
            continue
        a_lo, a_hi = analytic_box(geo, kb)
        if np.any(a_hi < a_lo):
            continue
        if not (np.all(np.isfinite(a_lo)) and np.all(np.isfinite(a_hi))):
            a_lo, a_hi = exact_box(geo, kb)
        bounded = bool(np.all(a_hi >= a_lo) and np.all(np.isfinite(a_lo)) and np.all(np.isfinite(a_hi)))
        counts = not material_only or b.material > 0
        if bounded and counts:
            lo, hi = np.minimum(lo, a_lo), np.maximum(hi, a_hi)
        if b.is_module and not (bounded and counts):  # (sin cota o vacío: lo que tiene dentro)
            stack += [d for d in b.daughters if d != kb]
    return None if np.any(hi < lo) else (lo, hi)
