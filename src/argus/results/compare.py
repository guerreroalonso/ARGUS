"""Comparar dos resultados: diferencia, cociente, compatibilidad estadística e índice gamma.

* :func:`on_grid`: pasa una curva a las posiciones de otra (interpolación lineal
  de los valores y de sus incertidumbres).
* :func:`difference`, :func:`ratio`, :func:`zscores`: punto a punto, con la
  incertidumbre propagada (independientes).
* :func:`chi2_test`: χ² de la diferencia con sus grados de libertad y la
  probabilidad de un χ² mayor si las dos son la misma distribución.
* :func:`gamma_index`: índice gamma (Low et al. 1998) en 1, 2 o 3 dimensiones,
  global o local, con umbral de dosis: el mínimo de
  Γ = sqrt((Δr/DTA)² + (ΔD/ΔD_max)²) sobre la distribución evaluada, hasta
  ``search`` veces DTA. En 1D y 2D es exacto sobre la distribución evaluada
  interpolada linealmente (en 1D, el mínimo de cada tramo es el de una
  parábola; en 2D, cada celda se parte en dos triángulos y Γ es la distancia
  del punto a cada triángulo en el espacio (x/DTA, y/DTA, D/ΔD), como en el
  método geométrico de Ju et al. 2008). En 3D, una búsqueda en una rejilla
  de paso DTA/``resolution`` (interpolación trilineal) que luego se afina
  desde el mejor punto y desde el propio punto. Con solo una rejilla, en las
  caídas de dosis (la dosis cambia mucho de un punto al siguiente) saldrían
  fallos que no lo son.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def on_grid(x_to: np.ndarray, x_from: np.ndarray, y: np.ndarray, err: np.ndarray | None = None
            ) -> tuple[np.ndarray, np.ndarray | None]:
    """Valores de (x_from, y) en las posiciones x_to (NaN fuera de su intervalo)."""
    x_from = np.asarray(x_from, dtype=np.float64)
    order = np.argsort(x_from)
    xf, yf = x_from[order], np.asarray(y, dtype=np.float64)[order]
    out = np.interp(x_to, xf, yf, left=np.nan, right=np.nan)
    e = None
    if err is not None:
        ef = np.asarray(err, dtype=np.float64)[order]
        e = np.interp(x_to, xf, ef, left=np.nan, right=np.nan)
    return out, e


def paired(ax, ay, aerr, bx, by, berr, at_b: bool = False) -> tuple:
    """Las dos curvas en las mismas posiciones: (x, a, σa, b, σb). Normalmente, las de ``a`` (la que se analiza) con
    ``b`` interpolada; con ``at_b`` (datos medidos), las de ``b`` con ``a`` interpolada: cada medida es un punto
    independiente, y entre dos medidas no hay más datos (interpolarlas en la rejilla de la simulación contaría como
    puntos independientes lo que solo es la recta entre dos medidas)."""
    if at_b:
        ya, ea = on_grid(bx, ax, ay, aerr)
        return (np.asarray(bx, dtype=np.float64), ya, ea, np.asarray(by, dtype=np.float64),
                None if berr is None else np.asarray(berr, dtype=np.float64))
    yb, eb = on_grid(ax, bx, by, berr)
    return (np.asarray(ax, dtype=np.float64), np.asarray(ay, dtype=np.float64),
            None if aerr is None else np.asarray(aerr, dtype=np.float64), yb, eb)


def _e(err, like):
    return np.zeros_like(like) if err is None else np.asarray(err, dtype=np.float64)


def difference(a, ea, b, eb) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    return a - b, np.sqrt(_e(ea, a) ** 2 + _e(eb, b) ** 2)


def ratio(a, ea, b, eb) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(b != 0, a / b, np.nan)
        # σ_r = sqrt(σa² + r²·σb²)/|b|: también vale con a = 0 (0 ± σa/|b|, no 0 ± 0).
        err = np.where(b != 0, np.sqrt(_e(ea, a) ** 2 + (r * _e(eb, b)) ** 2) / np.abs(b), np.nan)
    return r, err


def zscores(a, ea, b, eb) -> np.ndarray:
    d, s = difference(a, ea, b, eb)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(s > 0, d / s, np.nan)


def _gammq(a: float, x: float) -> float:
    """Q(a, x): función gamma incompleta regularizada superior (Numerical Recipes)."""
    if x < 0 or a <= 0:
        return math.nan
    if x == 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1.0
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-14:
                break
        return max(0.0, 1.0 - s * math.exp(-x + a * math.log(x) - gln))
    b = x + 1.0 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


@dataclass
class Chi2:
    chi2: float
    ndf: int
    p_value: float

    @property
    def reduced(self) -> float:
        return self.chi2 / self.ndf if self.ndf else math.nan


def chi2_test(a, ea, b, eb, mask=None) -> Chi2:
    z = zscores(a, ea, b, eb)
    ok = np.isfinite(z)
    if mask is not None:
        ok &= mask
    n = int(ok.sum())
    c2 = float(np.sum(z[ok] ** 2))
    return Chi2(c2, n, _gammq(0.5 * n, 0.5 * c2) if n else math.nan)


@dataclass
class GammaResult:
    gamma: np.ndarray  # un valor por punto de referencia (NaN: bajo el umbral o sin distribución evaluada)
    passing: float  # % de puntos evaluados con γ ≤ 1
    mean: float
    maximum: float
    p99: float  # percentil 99
    evaluated: int
    criteria: str

    def summary(self) -> str:
        return (f"{self.criteria}: {self.passing:.1f} % de {self.evaluated} puntos con γ ≤ 1; γ medio {self.mean:.2f}, "
                f"γ máximo {self.maximum:.2f}")


def _interp_nd(axes: list[np.ndarray], values: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Interpolación multilineal en una rejilla de puntos (``axes`` crecientes); NaN fuera."""
    d = len(axes)
    inside = np.ones(len(pts), dtype=bool)
    base, frac = [], []
    for k in range(d):
        ax = axes[k]
        inside &= (pts[:, k] >= ax[0] - 1e-12) & (pts[:, k] <= ax[-1] + 1e-12)
        if len(ax) == 1:
            base.append(np.zeros(len(pts), dtype=np.int64))
            frac.append(np.zeros(len(pts)))
            continue
        t = np.interp(pts[:, k], ax, np.arange(len(ax), dtype=np.float64))
        i0 = np.clip(np.floor(t).astype(np.int64), 0, len(ax) - 2)
        base.append(i0)
        frac.append(np.clip(t - i0, 0.0, 1.0))
    acc = np.zeros(len(pts))
    for corner in range(1 << d):
        w = np.ones(len(pts))
        idx = []
        for k in range(d):
            bit = (corner >> k) & 1
            idx.append(np.minimum(base[k] + bit, values.shape[k] - 1))
            w = w * (frac[k] if bit else (1.0 - frac[k]))
        acc += w * values[tuple(idx)]
    return np.where(inside, acc, np.nan)


def gamma_index(ref_axes: list[np.ndarray], ref: np.ndarray, eval_axes: list[np.ndarray], evaluated: np.ndarray, *,
                dose: float = 0.03, dta: float = 0.3, threshold: float = 0.10, local: bool = False,
                resolution: int | None = None, search: float = 3.0, norm: float | None = None) -> GammaResult:
    """Índice gamma de ``evaluated`` respecto de ``ref`` (1D, 2D o 3D).

    ``ref_axes``/``eval_axes``: posiciones (cm) de los puntos en cada eje;
    ``dose``: criterio de dosis (fracción: 0,03 = 3 %); ``dta``: distancia
    (cm); ``threshold``: se ignoran los puntos de referencia con menos de esa
    fracción del máximo; ``local``: ΔD relativo a la dosis de cada punto (si
    no, al máximo de la referencia o a ``norm``); ``resolution``: pasos por
    DTA al buscar (10 en 1D y 2D, 5 en 3D).
    """
    ref = np.asarray(ref, dtype=np.float64)
    evaluated = np.asarray(evaluated, dtype=np.float64)
    d = ref.ndim
    if len(ref_axes) != d or len(eval_axes) != d or evaluated.ndim != d:
        raise ValueError("Las dos distribuciones deben tener las mismas dimensiones")
    dmax = float(np.nanmax(ref)) if norm is None else float(norm)
    grids = np.meshgrid(*ref_axes, indexing="ij")
    pts = np.stack([g.ravel() for g in grids], axis=1)
    rv = ref.ravel()
    active = np.isfinite(rv) & (rv >= threshold * dmax)
    gamma = np.full(rv.shape, np.nan)
    if not active.any() or dmax <= 0:
        return GammaResult(gamma.reshape(ref.shape), math.nan, math.nan, math.nan, math.nan, 0,
                           _criteria(dose, dta, threshold, local))
    idx = np.nonzero(active)[0]
    p = pts[idx]
    r = rv[idx]
    dd = (dose * np.abs(r)) if local else np.full(len(r), dose * dmax)
    dd = np.where(dd > 0, dd, np.inf)
    if d == 1:
        best = _gamma2_1d(p[:, 0], r, dd, np.asarray(eval_axes[0], dtype=np.float64), evaluated, dta, search)
        return _gamma_result(best, idx, gamma, ref.shape, dose, dta, threshold, local)
    if d == 2:
        best = _gamma2_2d(p, r, dd, [np.asarray(a, dtype=np.float64) for a in eval_axes], evaluated, dta, search)
        return _gamma_result(best, idx, gamma, ref.shape, dose, dta, threshold, local)
    if resolution is None:
        resolution = 10 if d < 3 else 5  # en 3D, DTA/5 (el número de desplazamientos crece con el cubo)
    step = dta / max(int(resolution), 1)
    nmax = int(math.ceil(search * dta / step))
    rng = np.arange(-nmax, nmax + 1)
    mesh = np.meshgrid(*([rng] * d), indexing="ij")
    offs = np.stack([m.ravel() for m in mesh], axis=1).astype(np.float64) * step
    dist = np.sqrt((offs ** 2).sum(axis=1))
    keep = dist <= search * dta + 1e-12
    offs, dist = offs[keep], dist[keep]
    order = np.argsort(dist, kind="stable")
    offs, dist = offs[order], dist[order]
    best = np.full(len(idx), np.inf)
    best_off = np.zeros((len(idx), d))
    live = np.ones(len(idx), dtype=bool)
    k = 0
    batch = 200_000  # evaluaciones mínimas por lote (varias capas de distancia juntas si hay pocos puntos)
    while k < len(offs) and live.any():
        sel = np.nonzero(live)[0]
        # Desplazamientos de este lote: capas de distancia enteras hasta ~batch evaluaciones.
        j = k + 1
        while j < len(offs) and abs(dist[j] - dist[k]) < 1e-12:
            j += 1
        while j < len(offs) and (j - k) * len(sel) < batch:
            j += 1
            while j < len(offs) and abs(dist[j] - dist[j - 1]) < 1e-12:
                j += 1
        o = offs[k:j]
        q = (p[sel][:, None, :] + o[None, :, :]).reshape(-1, d)
        ev = _interp_nd(eval_axes, evaluated, q).reshape(len(sel), len(o))
        g2 = (dist[k:j][None, :] / dta) ** 2 + ((ev - r[sel][:, None]) / dd[sel][:, None]) ** 2
        g2 = np.where(np.isfinite(g2), g2, np.inf)
        am = g2.argmin(axis=1)
        gm = g2[np.arange(len(sel)), am]
        better = gm < best[sel]
        best[sel] = np.where(better, gm, best[sel])
        best_off[sel[better]] = o[am[better]]
        k = j
        if k < len(offs):
            # Un punto ya no puede mejorar si su γ² es menor que (distancia siguiente / DTA)².
            live &= best > (dist[k] / dta) ** 2
    best = _refine(p, r, dd, eval_axes, evaluated, dta, search, step, best, best_off)
    return _gamma_result(best, idx, gamma, ref.shape, dose, dta, threshold, local)


def _gamma_result(best: np.ndarray, idx: np.ndarray, gamma: np.ndarray, shape, dose, dta, threshold, local
                  ) -> GammaResult:
    g = np.sqrt(best)
    g[~np.isfinite(g)] = np.nan
    gamma[idx] = g
    ok = np.isfinite(g)
    n = int(ok.sum())
    passing = 100.0 * float(np.mean(g[ok] <= 1.0)) if n else math.nan
    return GammaResult(gamma.reshape(shape), passing, float(np.mean(g[ok])) if n else math.nan,
                       float(np.max(g[ok])) if n else math.nan, float(np.percentile(g[ok], 99)) if n else math.nan, n,
                       _criteria(dose, dta, threshold, local))


def _gamma2_1d(px: np.ndarray, r: np.ndarray, dd: np.ndarray, ex: np.ndarray, ev: np.ndarray, dta: float,
               search: float) -> np.ndarray:
    """Γ² mínimo de cada punto sobre la curva evaluada interpolada linealmente: en cada tramo, Γ² es una parábola
    del parámetro del tramo (su mínimo, recortado al tramo)."""
    order = np.argsort(ex, kind="stable")
    ex, ev = ex[order], np.asarray(ev, dtype=np.float64)[order]
    best = np.full(len(px), np.inf)
    if len(ex) == 0:
        return best
    if len(ex) == 1:
        g2 = ((ex[0] - px) / dta) ** 2 + ((ev[0] - r) / dd) ** 2
        return np.where(np.isfinite(g2), g2, np.inf)
    reach = search * dta
    lo = np.clip(np.searchsorted(ex, px - reach, side="right") - 1, 0, len(ex) - 2)
    hi = np.clip(np.searchsorted(ex, px + reach, side="left"), 1, len(ex) - 1)  # tramos lo .. hi-1
    width = int(np.max(hi - lo)) if len(px) else 0
    for k in range(max(width, 1)):
        i = lo + k
        valid = i < hi
        i = np.minimum(i, len(ex) - 2)
        x0, x1, d0, d1 = ex[i], ex[i + 1], ev[i], ev[i + 1]
        ax, bx = (x1 - x0) / dta, (x0 - px) / dta
        ad, bd = (d1 - d0) / dd, (d0 - r) / dd
        a = ax * ax + ad * ad
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(a > 0, -(ax * bx + ad * bd) / a, 0.0)
        t = np.clip(np.nan_to_num(t), 0.0, 1.0)
        g2 = (ax * t + bx) ** 2 + (ad * t + bd) ** 2
        g2 = np.where(valid & np.isfinite(g2), g2, np.inf)
        best = np.minimum(best, g2)
    return best


def _dist2_segment(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distancia² del origen a cada segmento AB (filas de ``a`` y ``b``)."""
    e = b - a
    den = (e * e).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(den > 0, -(a * e).sum(axis=1) / den, 0.0)
    t = np.clip(np.nan_to_num(t), 0.0, 1.0)
    q = a + t[:, None] * e
    return (q * q).sum(axis=1)


def _dist2_triangle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Distancia² del origen a cada triángulo ABC: su proyección sobre el plano si cae dentro; si no, la del borde
    más cercano."""
    e0, e1 = b - a, c - a
    a00, a01, a11 = (e0 * e0).sum(axis=1), (e0 * e1).sum(axis=1), (e1 * e1).sum(axis=1)
    b0, b1 = -(e0 * a).sum(axis=1), -(e1 * a).sum(axis=1)
    det = a00 * a11 - a01 * a01
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (a11 * b0 - a01 * b1) / det
        t = (a00 * b1 - a01 * b0) / det
    inside = (det > 1e-300) & (s >= 0) & (t >= 0) & (s + t <= 1)
    q = a + np.nan_to_num(s)[:, None] * e0 + np.nan_to_num(t)[:, None] * e1
    edges = np.minimum(np.minimum(_dist2_segment(a, b), _dist2_segment(b, c)), _dist2_segment(c, a))
    return np.where(inside, (q * q).sum(axis=1), edges)


def _cell_ranges(x: np.ndarray, pos: np.ndarray, reach: float) -> tuple[np.ndarray, np.ndarray]:
    """Celdas [i, i+1] de un eje que pueden quedar a menos de ``reach`` de cada posición: de lo a hi-1."""
    lo = np.clip(np.searchsorted(x, pos - reach, side="right") - 1, 0, len(x) - 2)
    hi = np.clip(np.searchsorted(x, pos + reach, side="left"), 1, len(x) - 1)
    return lo, hi


def _gamma2_2d(p: np.ndarray, r: np.ndarray, dd: np.ndarray, axes: list[np.ndarray], ev: np.ndarray, dta: float,
               search: float) -> np.ndarray:
    """Γ² mínimo de cada punto sobre la distribución evaluada interpolada linealmente en triángulos (dos por celda):
    la distancia del punto a cada triángulo en el espacio (x/DTA, y/DTA, D/ΔD). Se saltan las celdas que no pueden
    mejorar lo que ya se tiene (su caja y el intervalo de sus dosis)."""
    ev = np.asarray(ev, dtype=np.float64)
    u, v = axes
    if np.any(np.diff(u) < 0):
        o = np.argsort(u, kind="stable")
        u, ev = u[o], ev[o, :]
    if np.any(np.diff(v) < 0):
        o = np.argsort(v, kind="stable")
        v, ev = v[o], ev[:, o]
    n = len(p)
    best = np.full(n, np.inf)
    if len(u) < 2 or len(v) < 2:  # una sola fila o columna: es una curva
        if len(u) == 1 and len(v) >= 2:
            g2 = _gamma2_1d(p[:, 1], r, dd, v, ev[0, :], dta, search)
            return g2 + ((p[:, 0] - u[0]) / dta) ** 2
        if len(v) == 1 and len(u) >= 2:
            g2 = _gamma2_1d(p[:, 0], r, dd, u, ev[:, 0], dta, search)
            return g2 + ((p[:, 1] - v[0]) / dta) ** 2
        g2 = ((p[:, 0] - u[0]) / dta) ** 2 + ((p[:, 1] - v[0]) / dta) ** 2 + ((ev.ravel()[0] - r) / dd) ** 2
        return np.where(np.isfinite(g2), g2, np.inf)
    reach = search * dta
    lou, hiu = _cell_ranges(u, p[:, 0], reach)
    lov, hiv = _cell_ranges(v, p[:, 1], reach)
    wu, wv = int(np.max(hiu - lou)), int(np.max(hiv - lov))
    for ku in range(max(wu, 1)):
        iu = lou + ku
        ok_u = iu < hiu
        iu = np.minimum(iu, len(u) - 2)
        u0, u1 = (u[iu] - p[:, 0]) / dta, (u[iu + 1] - p[:, 0]) / dta
        gu = np.maximum(np.maximum(u0, -u1), 0.0)
        for kv in range(max(wv, 1)):
            iv = lov + kv
            ok = ok_u & (iv < hiv)
            iv = np.minimum(iv, len(v) - 2)
            v0, v1 = (v[iv] - p[:, 1]) / dta, (v[iv + 1] - p[:, 1]) / dta
            gv = np.maximum(np.maximum(v0, -v1), 0.0)
            d00, d10 = (ev[iu, iv] - r) / dd, (ev[iu + 1, iv] - r) / dd
            d01, d11 = (ev[iu, iv + 1] - r) / dd, (ev[iu + 1, iv + 1] - r) / dd
            lo_d = np.minimum(np.minimum(d00, d10), np.minimum(d01, d11))
            hi_d = np.maximum(np.maximum(d00, d10), np.maximum(d01, d11))
            gap = np.maximum(np.maximum(lo_d, -hi_d), 0.0)
            with np.errstate(invalid="ignore"):
                m = ok & (gu * gu + gv * gv + gap * gap < best)  # NaN (sin dosis): fuera
            if not m.any():
                continue
            k = np.nonzero(m)[0]
            a = np.stack([u0[k], v0[k], d00[k]], axis=1)
            b = np.stack([u1[k], v0[k], d10[k]], axis=1)
            c = np.stack([u1[k], v1[k], d11[k]], axis=1)
            e = np.stack([u0[k], v1[k], d01[k]], axis=1)
            g2 = np.minimum(_dist2_triangle(a, b, c), _dist2_triangle(a, c, e))
            best[k] = np.minimum(best[k], np.where(np.isfinite(g2), g2, np.inf))
    return best


def _refine(p: np.ndarray, r: np.ndarray, dd: np.ndarray, axes: list[np.ndarray], evaluated: np.ndarray,
            dta: float, search: float, step: float, best: np.ndarray, best_off: np.ndarray, rounds: int = 12
            ) -> np.ndarray:
    """Afina el mínimo de Γ² de cada punto: búsqueda por patrones (en cada vuelta, los vecinos a medio paso del
    anterior) desde el mejor desplazamiento de la rejilla y desde el propio punto (en una caída de dosis muy fuerte
    el mínimo está muy cerca del punto, entre dos de la rejilla)."""
    out = _pattern(p, r, dd, axes, evaluated, dta, search, step, best, best_off, rounds)
    zero = np.zeros_like(best_off)
    g0 = ((_interp_nd(axes, evaluated, p) - r) / dd) ** 2
    g0 = np.where(np.isfinite(g0), g0, np.inf)
    return np.minimum(out, _pattern(p, r, dd, axes, evaluated, dta, search, step, g0, zero, rounds))


def _pattern(p: np.ndarray, r: np.ndarray, dd: np.ndarray, axes: list[np.ndarray], evaluated: np.ndarray,
             dta: float, search: float, step: float, best: np.ndarray, best_off: np.ndarray, rounds: int
             ) -> np.ndarray:
    d = p.shape[1]
    cur, off = best.copy(), best_off.copy()
    todo = np.isfinite(cur) & (cur > 0)
    if not todo.any():
        return cur
    nb = np.stack([m.ravel() for m in np.meshgrid(*([np.array([-1.0, 0.0, 1.0])] * d), indexing="ij")], axis=1)
    nb = nb[np.any(nb != 0, axis=1)]
    sel = np.nonzero(todo)[0]
    h = step
    limit = (search * dta) ** 2
    for _ in range(rounds):
        h *= 0.5
        cand = off[sel][:, None, :] + h * nb[None, :, :]  # (puntos, vecinos, d)
        dist2 = (cand ** 2).sum(axis=2)
        q = (p[sel][:, None, :] + cand).reshape(-1, d)
        ev = _interp_nd(axes, evaluated, q).reshape(len(sel), len(nb))
        g2 = dist2 / dta ** 2 + ((ev - r[sel][:, None]) / dd[sel][:, None]) ** 2
        g2 = np.where(np.isfinite(g2) & (dist2 <= limit + 1e-12), g2, np.inf)
        am = g2.argmin(axis=1)
        gm = g2[np.arange(len(sel)), am]
        better = gm < cur[sel]
        cur[sel] = np.where(better, gm, cur[sel])
        off[sel[better]] = cand[np.nonzero(better)[0], am[better]]
    return cur


def _criteria(dose: float, dta: float, threshold: float, local: bool) -> str:
    return (f"{100 * dose:g} % / {10 * dta:g} mm ({'local' if local else 'global'}, umbral {100 * threshold:g} %)")


__all__ = ["Chi2", "GammaResult", "chi2_test", "difference", "gamma_index", "on_grid", "paired", "ratio", "zscores"]
