"""Comprobación con rayos de lo que el muestreo por puntos no ve.

Las comprobaciones de «¿cabe aquí?» (añadir, duplicar, mover, envolver) usan
decenas de miles de puntos al azar: una lámina de 1 µm dentro de un volumen de
centímetros casi nunca recibe un punto, y un cuerpo nuevo que la atraviesa se
daría por bueno (y quedaría solapado). Un rayo que cruza la lámina la ve
siempre: aquí se lanzan rayos paralelos a los tres ejes por la caja de la
región y se mira qué cuerpos cortan dentro de ella.
"""

from __future__ import annotations

import numpy as np


def tracer_for(geo):
    """Trazador de rayos de ``geo`` (se guarda con la geometría para no rehacerlo en cada comprobación)."""
    tr = getattr(geo, "_argus_tracer", None)
    if tr is None:
        from ..render.raytrace import RayTracer

        tr = RayTracer(geo)
        try:
            geo._argus_tracer = tr
        except (AttributeError, TypeError):  # pragma: no cover - geometría sin atributos libres
            pass
    return tr


def ray_samples(geo, lo, hi, m: int = 16, seed: int = 0, skip=()) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Puntos de los tramos de rayos paralelos a los ejes (m×m por eje) por la caja [lo, hi].

    Tres puntos por tramo (cerca de cada extremo y en medio). Devuelve
    (puntos, cuerpo de cada punto, rayo de cada punto, número de rayos); los
    tramos de los cuerpos ``skip`` no se muestrean.
    """
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    empty = (np.zeros((0, 3)), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), 0)
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))) or np.any(hi <= lo):
        return empty
    tr = tracer_for(geo)
    rng = np.random.default_rng(seed)
    size = hi - lo
    pad = 1e-6 * float(size.max())
    skip = set(int(k) for k in skip)
    tests: list[np.ndarray] = []
    bodies: list[int] = []
    rays: list[int] = []
    nrays = 0
    for axis in range(3):
        u, v = (i for i in range(3) if i != axis)
        d = np.zeros(3)
        d[axis] = 1.0
        length = float(size[axis]) + 2 * pad
        for a in (np.arange(m) + rng.random(m)) / m:
            for b in (np.arange(m) + rng.random(m)) / m:
                o = lo.copy()
                o[axis] -= pad
                o[u] += a * size[u]
                o[v] += b * size[v]
                segs = tr.trace(o, d)
                for s0, s1 in zip(segs, segs[1:] + [None]):
                    if s0.body in skip:
                        continue
                    t0 = max(s0.t, 0.0)
                    t1 = min(s1.t if s1 is not None else np.inf, length)
                    if t1 <= t0:
                        continue
                    eps = 1e-3 * (t1 - t0)
                    for t in (t0 + eps, 0.5 * (t0 + t1), t1 - eps):
                        tests.append(o + t * d)
                        bodies.append(s0.body)
                        rays.append(nrays)
                nrays += 1
    if not tests:
        return empty[0], empty[1], empty[2], nrays
    return np.vstack(tests), np.asarray(bodies, dtype=np.int64), np.asarray(rays, dtype=np.int64), nrays


def intruders(geo, lo, hi, inside, allowed, m: int = 16, seed: int = 0) -> dict[int, float]:
    """Cuerpos fuera de ``allowed`` que cortan la región ``inside`` en rayos por la caja [lo, hi].

    ``inside(points) -> bool[]``: si cada punto está en la región. Devuelve
    {cuerpo: fracción de rayos en que aparece}.
    """
    pts, bodies, rays, nrays = ray_samples(geo, lo, hi, m, seed, skip=allowed)
    return count_hits(inside(pts) if len(pts) else np.zeros(0, dtype=bool), bodies, rays, nrays)


def count_hits(ok, bodies, rays, nrays: int) -> dict[int, float]:
    """{cuerpo: fracción de rayos con algún punto ``ok``} a partir de ``ray_samples``."""
    ok = np.asarray(ok, dtype=bool)
    hits: dict[int, set] = {}
    for i in np.flatnonzero(ok):
        hits.setdefault(int(bodies[i]), set()).add(int(rays[i]))
    return {kb: len(s) / max(nrays, 1) for kb, s in hits.items()}


def ray_points(geo, lo, hi, bodies, m: int = 16, seed: int = 0) -> np.ndarray:
    """Puntos de los cuerpos ``bodies`` en rayos por la caja [lo, hi] (para cuerpos que el muestreo no ve)."""
    pts, kbs, _rays, _n = ray_samples(geo, lo, hi, m, seed)
    return pts[np.isin(kbs, list(bodies))] if len(pts) else pts
