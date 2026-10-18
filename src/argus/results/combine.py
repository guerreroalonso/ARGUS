"""Juntar los resultados de ejecuciones independientes del mismo caso (como penhmain-sum).

Cada resultado de penhmain es una media por lluvia: con N lluvias, el valor es
``m = S1/N`` y su incertidumbre (1 sigma) ``σ² = (S2/N − m²)/N``, donde S1 y S2
son las sumas de las contribuciones de cada lluvia y de sus cuadrados. De cada
ejecución se conocen N, m y σ, así que se recupera su segundo momento
``q = N σ² + m²`` y se combinan como si fuera una sola simulación con todas las
lluvias:

    m = Σ Nᵢ mᵢ / N,      σ² = (Σ Nᵢ qᵢ / N − m²) / N,      N = Σ Nᵢ.

Es lo mismo que hace penhmain-sum con los contadores de los volcados (salvo por
las cifras con que se escriben los ficheros). Las medias condicionadas (la
energía media de las partículas que salen en cada ángulo) se ponderan con el
número de partículas de cada ejecución; las medias por primaria de cada destino
del resumen (recorrido, energía final, ángulo de salida, interacciones), con
las primarias de ese destino, PRIM(I).
"""

from __future__ import annotations

import copy
from dataclasses import replace

import numpy as np

from .data import Curves, Grid, Series
from .summary import Measure, Summary


class Incompatible(ValueError):
    """Las ejecuciones no tienen la misma rejilla o las mismas series."""


def pooled(values: list[np.ndarray], sigmas: list[np.ndarray | None], ns: list[float]
           ) -> tuple[np.ndarray, np.ndarray]:
    ns_arr = np.asarray(ns, dtype=np.float64)
    ntot = float(ns_arr.sum())
    if ntot <= 0:
        raise Incompatible("Ninguna ejecución tiene lluvias simuladas")
    v = np.stack([np.asarray(x, dtype=np.float64) for x in values])
    s = np.stack([np.zeros_like(v[0]) if e is None else np.asarray(e, dtype=np.float64) for e in sigmas])
    w = ns_arr.reshape((-1,) + (1,) * (v.ndim - 1))
    m = (w * v).sum(axis=0) / ntot
    q = w * s * s + v * v
    var = ((w * q).sum(axis=0) / ntot - m * m) / ntot
    return m, np.sqrt(np.clip(var, 0.0, None))


def _conditional_mean(values: list[np.ndarray], sigmas: list[np.ndarray | None], weights: list[np.ndarray]
                      ) -> tuple[np.ndarray, np.ndarray]:
    """Media condicionada, como la energía media de las partículas de un intervalo (polar-angle-*.dat).

    penhmain la calcula con el peso total TW de las partículas del intervalo:
    Z = S1/TW y 3σ = 3·sqrt(S2 − S1²/TW)/TW. De cada ejecución se recuperan
    S1 = Z·TW y S2 = (σ·TW)² + Z²·TW, se suman y se vuelve a calcular.
    """
    z = np.stack(values)
    s = np.stack([np.zeros_like(values[0]) if e is None else e for e in sigmas])
    tw = np.stack(weights)
    s1 = (z * tw).sum(axis=0)
    s2 = (s * s * tw * tw + z * z * tw).sum(axis=0)
    twt = tw.sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = np.where(twt > 0, s1 / twt, 0.0)
        err = np.where(twt > 0, np.sqrt(np.abs(s2 - s1 * s1 / np.where(twt > 0, twt, 1.0))) / twt, 0.0)
    return m, err


def _bin_measure(c: Curves) -> np.ndarray:
    """Tamaño de cada intervalo con el que penhmain divide para dar densidades: el ángulo sólido en las
    distribuciones angulares (θ en grados) y la anchura en las demás."""
    if c.edges is None:
        return np.ones_like(c.x)
    if c.kind == "polar-angle":
        th = np.radians(c.edges)
        return (np.cos(th[:-1]) - np.cos(th[1:])) * 2.0 * np.pi
    return np.diff(c.edges)


def _same_axis(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a.shape == b.shape and np.allclose(a, b, rtol=1e-5, atol=1e-12)


def combine_curves(items: list[tuple[float, Curves]]) -> Curves:
    if not items:
        raise Incompatible("No hay nada que combinar")
    n0, c0 = items[0]
    for _, c in items[1:]:
        if c.kind != c0.kind or not _same_axis(c.x, c0.x):
            raise Incompatible(f"{c0.title}: las ejecuciones no tienen los mismos intervalos")
    ns = [n for n, _ in items]
    out: list[Series] = []
    by_name = {s.name: i for i, s in enumerate(c0.series)}
    for s0 in c0.series:
        group = []
        for _, c in items:
            s = c.get(s0.name)
            if s is None:
                raise Incompatible(f"{c0.title}: falta la serie «{s0.name}» en alguna ejecución")
            group.append(s)
        if s0.weight_of is not None and s0.weight_of in by_name:
            weights = [n * c.get(s0.weight_of).y * _bin_measure(c) for n, c in items]
            m, e = _conditional_mean([g.y for g in group], [g.err for g in group], weights)
        elif s0.err is None and c0.kind == "psource" and s0.name == "definido":
            if any(not _same_axis(g.y, s0.y) for g in group[1:]):
                raise Incompatible("Las ejecuciones no tienen la misma fuente: no son del mismo caso")
            m, e = s0.y.copy(), None
        else:
            m, e = pooled([g.y for g in group], [g.err for g in group], ns)
            if s0.err is None:
                e = None
        out.append(replace(s0, y=m, err=e))
    meta = dict(c0.meta)
    meta["runs"] = len(items)
    return replace(c0, series=out, meta=meta)


def combine_grids(items: list[tuple[float, Grid]]) -> Grid:
    if not items:
        raise Incompatible("No hay nada que combinar")
    n0, g0 = items[0]
    for _, g in items[1:]:
        if g.kind != g0.kind or g.shape != g0.shape or not all(_same_axis(a, b) for a, b in zip(g.edges, g0.edges)):
            raise Incompatible(f"{g0.title}: las ejecuciones no tienen la misma rejilla")
    ns = [n for n, _ in items]
    v, e = pooled([g.value for _, g in items], [g.err for _, g in items], ns)
    extra = {}
    for name, (_, _, unit) in g0.extra.items():
        ev, ee = pooled([g.extra[name][0] for _, g in items], [g.extra[name][1] for _, g in items], ns)
        extra[name] = (ev, ee, unit)
    meta = dict(g0.meta)
    meta["runs"] = len(items)
    out = replace(g0, value=v, err=e, extra=extra, meta=meta)
    if "main" in g0.meta and g0.meta["main"] in extra:
        out.value, out.err = extra[g0.meta["main"]][0], extra[g0.meta["main"]][1]
    return out


def _pool_measures(ms: list[Measure | None], ns: list[float]) -> Measure | None:
    present = [(m, n) for m, n in zip(ms, ns) if m is not None]
    if not present:
        return None
    v, e = pooled([np.array(m.value) for m, _ in present], [np.array(m.err) for m, _ in present],
                  [n for _, n in present])
    return Measure(float(v), float(e), present[0][0].unit)


def _pool_dict(dicts: list[dict], ns: list[float]) -> dict:
    keys: list = []
    for d in dicts:
        for k in d:
            if k not in keys:
                keys.append(k)
    return {k: _pool_measures([d.get(k) for d in dicts], ns) for k in keys}


def _pool_where(dicts: list[dict], summaries: list[Summary]) -> dict:
    """Medias por primaria de cada destino (las que salen hacia arriba, hacia abajo o se absorben): penhmain las
    divide entre las primarias de ese destino, PRIM(I), y escribe 0 si no hay ninguna (penhmain-sum igual)."""
    keys: list = []
    for d in dicts:
        for k in d:
            if k not in keys:
                keys.append(k)
    out = {}
    for w in keys:
        ms = [d.get(w) for d in dicts]
        ns = [s.primaries.get(w, 0.0) for s in summaries]
        unit = next((m.unit for m in ms if m is not None), "")
        present = [(m, n) for m, n in zip(ms, ns) if m is not None and n > 0]
        out[w] = _pool_measures([m for m, _ in present], [n for _, n in present]) if present else Measure(0.0, 0.0,
                                                                                                              unit)
    return out


def _check_same_case(summaries: list[Summary]) -> None:
    if len({s.title.strip() for s in summaries}) > 1 or len({s.kparp for s in summaries}) > 1:
        raise Incompatible("Las ejecuciones no son del mismo caso (título o partícula de la fuente distintos)")
    if len({tuple(sorted(s.body_energy)) for s in summaries}) > 1:
        raise Incompatible("Las ejecuciones no son del mismo caso (no tienen los mismos cuerpos)")


def combine_summaries(summaries: list[Summary]) -> Summary:
    if not summaries:
        raise Incompatible("No hay nada que combinar")
    _check_same_case(summaries)
    ns = [s.showers for s in summaries]
    out = copy.copy(summaries[0])
    out.text = ""
    out.showers = float(sum(ns))
    out.time_total = max(s.time_total for s in summaries)  # las ejecuciones van a la vez: tiempo de reloj
    out.time_cpu = float(sum(s.time_cpu for s in summaries))
    out.speed = float(sum(s.speed for s in summaries))
    out.primaries = {k: float(sum(s.primaries.get(k, 0.0) for s in summaries)) for k in summaries[0].primaries}
    for name in ("fractions", "secondaries", "body_energy", "body_charge", "impact_energy", "impact_area",
                 "angular_energy", "enddet_energy", "enddet_area"):
        setattr(out, name, _pool_dict([getattr(s, name) for s in summaries], ns))
    for name in ("track_length", "final_energy", "polar_cosine", "polar_angle"):
        setattr(out, name, _pool_where([getattr(s, name) for s in summaries], summaries))
    out.events = {}
    for w in summaries[0].events:
        keep = [(s.events.get(w, {}), s.primaries.get(w, 0.0)) for s in summaries]
        pairs = [(d, n) for d, n in keep if n > 0]
        out.events[w] = _pool_dict([d for d, _ in pairs], [n for _, n in pairs]) if pairs else \
            {k: Measure(0.0, 0.0, m.unit) for k, m in summaries[0].events[w].items()}
    out.dose_efficiency = out.dose_intrinsic = out.dose_uncertainty_pct = None
    out.last_seeds = None
    out.complete = all(s.complete for s in summaries)
    out.date = max((s.date for s in summaries), default="")
    return out


def combine(items: list[tuple[float, object]]):
    """Combina resultados del mismo tipo (curvas, rejillas o resúmenes) de varias ejecuciones."""
    if not items:
        raise Incompatible("No hay nada que combinar")
    first = items[0][1]
    if isinstance(first, Curves):
        if first.kind == "ct-dvh":
            raise Incompatible("El histograma dosis-volumen se recalcula con la dosis combinada del TAC")
        return combine_curves(items)  # type: ignore[arg-type]
    if isinstance(first, Grid):
        return combine_grids(items)  # type: ignore[arg-type]
    if isinstance(first, Summary):
        return combine_summaries([s for _, s in items])  # type: ignore[misc]
    return first  # trayectorias, textos…: los de la primera ejecución


__all__ = ["Incompatible", "combine", "combine_curves", "combine_grids", "combine_summaries", "pooled"]
