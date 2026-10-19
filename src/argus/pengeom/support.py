"""La caja exacta de una región de PENGEOM: los puntos que cumplen a la vez varias desigualdades cuádricas.

Un cuerpo de PENGEOM está dentro de sus superficies, cada una por su lado: g_i(r) = rᵀA_i r + b_i·r + c_i ≤ 0. Hasta
0.17.2 su caja se buscaba con rayos en una rejilla y con LOCATE en puntos al azar, y no veía lo fino ni lo inclinado
(una lámina de 50 µm girada 80°: 0,37 cm de menos; «Envolver en un módulo» cortaba la lámina sin avisar). Aquí sale de
las ecuaciones (undécima revisión).

El máximo de e·r en una región cerrada y acotada está en un punto de Karush-Kuhn-Tucker: en una superficie, donde e
es perpendicular a ella; en la curva de dos superficies, donde e está en el plano de sus normales; o donde se cortan
tres. Se calculan todos (con planos, con fórmulas; con dos o tres cuádricas, como cortes de tres cuádricas, por
homotopía: en la curva de dos, donde e·(∇g1 × ∇g2) = 0, que es otra) y la caja es la de los que están en la región.
Para que todo sea genérico (que el máximo sea un punto y no una cara entera), las direcciones se tuercen un poco
(1e-6); cada lado de la caja es el valor de los puntos encontrados en ese eje, sin el giro. La región se recorta con
una caja enorme (``REACH`` veces su tamaño): si llega a ella, no tiene cota en ese sentido.
"""

from __future__ import annotations

import itertools

import numpy as np

BIG = 2.0e7  # cm: más allá de la envolvente de PENGEOM (esfera de 1e7)
REACH = 1.0e5  # la caja de las cuentas, en tamaños de la región: lo que llega a ella no tiene cota
_TILT = np.array([0.3141592653589793, -0.5772156649015329, 0.7548776662466927])
_EPS_TILT = 1.0e-6
_ROT = 0.6180339887498949  # giro de la base de cada plano (que las cónicas tengan término en t²)


class Constraint:
    """g(r) = rᵀAr + b·r + c ≤ 0 (en coordenadas normalizadas)."""

    __slots__ = ("a", "b", "c", "plane", "degenerate")

    def __init__(self, a, b, c):
        self.a = np.asarray(a, dtype=float)
        self.b = np.asarray(b, dtype=float)
        self.c = float(c)
        scale = max(float(np.abs(self.a).max()), float(np.abs(self.b).max()))
        if scale > 0:
            self.a, self.b, self.c = self.a / scale, self.b / scale, self.c / scale
        self.plane = float(np.abs(self.a).max()) <= 1e-12 * max(float(np.abs(self.b).max()), 1e-300)
        if self.plane:
            self.a = np.zeros((3, 3))
        self.degenerate = scale == 0 or (self.plane and float(np.abs(self.b).max()) == 0.0)

    def value(self, p: np.ndarray) -> np.ndarray:
        p = np.atleast_2d(p)
        return np.einsum("ij,jk,ik->i", p, self.a, p) + p @ self.b + self.c

    def grad(self, p: np.ndarray) -> np.ndarray:
        return 2.0 * np.atleast_2d(p) @ self.a + self.b

    def tol(self, p: np.ndarray) -> np.ndarray:
        """Lo que se admite fuera (redondeo): relativo al tamaño de los términos."""
        p = np.atleast_2d(p)
        r = np.linalg.norm(p, axis=1)
        terms = r * r * float(np.abs(self.a).max()) + r * float(np.abs(self.b).max()) + abs(self.c)
        return 1e-9 * (1.0 + r) * (np.linalg.norm(self.grad(p), axis=1) + 1e-300) + 1e-11 * terms


def _perp(e: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Una base ortonormal (girada ``_ROT``) del plano perpendicular a e."""
    k = int(np.argmin(np.abs(e)))
    t = np.zeros(3)
    t[k] = 1.0
    p = np.cross(e, t)
    p /= np.linalg.norm(p)
    q = np.cross(e / np.linalg.norm(e), p)
    c, s = np.cos(_ROT), np.sin(_ROT)
    return c * p + s * q, -s * p + c * q


def _line_roots(g: Constraint, r0: np.ndarray, d: np.ndarray) -> list[np.ndarray]:
    """Puntos de la recta r0 + t·d en g = 0."""
    a = float(d @ g.a @ d)
    b = float(2.0 * d @ g.a @ r0 + g.b @ d)
    c = float(g.value(r0)[0])
    out = []
    scale = max(abs(a), abs(b), abs(c), 1e-300)
    if abs(a) <= 1e-14 * scale:
        if abs(b) > 1e-14 * scale:
            out.append(-c / b)
    else:
        disc = b * b - 4.0 * a * c
        if disc >= -1e-12 * b * b:
            sq = np.sqrt(max(disc, 0.0))
            q = -0.5 * (b + np.copysign(sq, b))
            out += [q / a, c / q] if q != 0.0 else [-b / (2.0 * a)]
    return [r0 + t * d for t in out if np.isfinite(t)]


def _cross3(a, b) -> np.ndarray:
    return np.array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])


def _solve_line(m: np.ndarray, rhs: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """La recta de soluciones de m·r = rhs (m: 2×3 de rango 2): su punto más cerca del origen y su dirección (por
    productos vectoriales: con LAPACK, cada una de estas cuentas tan pequeñas tardaba diez veces más)."""
    m0, m1 = m[0], m[1]
    d = _cross3(m0, m1)
    dd = float(d @ d)
    n0, n1 = float(m0 @ m0), float(m1 @ m1)
    if not np.isfinite(dd) or dd <= 1e-22 * n0 * n1 or not np.all(np.isfinite(rhs)):
        return None
    r0 = (float(rhs[0]) * _cross3(m1, d) + float(rhs[1]) * _cross3(d, m0)) / dd
    return r0, d / np.sqrt(dd)


def _single(g: Constraint, e: np.ndarray) -> list[np.ndarray]:
    """Puntos de g = 0 con la normal paralela a e."""
    if g.plane or g.degenerate:
        return []
    p, q = _perp(e)
    line = _solve_line(2.0 * np.array([p @ g.a, q @ g.a]), -np.array([p @ g.b, q @ g.b]))
    return [] if line is None else _line_roots(g, *line)


def _plane_curve(pl: Constraint, g: Constraint, e: np.ndarray) -> list[np.ndarray]:
    """Puntos de la curva plano ∩ g donde e está en el plano de las dos normales."""
    w = _cross3(e, pl.b)
    if np.linalg.norm(w) <= 1e-12 * np.linalg.norm(pl.b):
        return []
    line = _solve_line(np.array([2.0 * w @ g.a, pl.b]), -np.array([w @ g.b, pl.c]))
    return [] if line is None else _line_roots(g, *line)


_GAMMA = np.exp(1j * 2.399963229728653)  # (el «truco de gamma»: con un complejo cualquiera, ningún camino se cruza)
_START = np.array([[a, b, c] for a in (1, -1) for b in (1, -1) for c in (1, -1)], dtype=complex)


def solve_quadrics(systems: list[tuple], max_iter: int = 800) -> list[list[np.ndarray]]:
    """Las soluciones reales de cada sistema de tres ecuaciones cuadráticas en tres incógnitas, (A, B, C) con
    f_k(x) = xᵀA_k x + B_k·x + C_k: homotopía desde x_i² = 1, cuyas 8 soluciones (las de Bézout) llevan cada una a
    una solución del sistema o al infinito. Todos los caminos de todos los sistemas a la vez."""
    n = len(systems)
    if not n:
        return []
    a_all = np.repeat(np.array([s[0] for s in systems], dtype=complex), 8, axis=0)
    b_all = np.repeat(np.array([s[1] for s in systems], dtype=complex), 8, axis=0)
    c_all = np.repeat(np.array([s[2] for s in systems], dtype=complex), 8, axis=0)
    owner = np.repeat(np.arange(n), 8)
    x = np.tile(_START, (n, 1))
    t = np.zeros(len(x))
    dt = np.full(len(x), 0.05)
    alive = np.ones(len(x), dtype=bool)
    done = np.zeros(len(x), dtype=bool)
    with np.errstate(all="ignore"):
        for _ in range(max_iter):
            act = np.flatnonzero(alive & ~done)
            if not len(act):
                break
            xa, ta = x[act], t[act]
            da = np.minimum(dt[act], 1.0 - ta)
            a, b, c = a_all[act], b_all[act], c_all[act]
            # Predictor (tangente) y tres pasos de Newton en t + dt.
            f, jf = _fj(xa, a, b, c)
            dx = _solve(_hjac(xa, jf, ta), f - _GAMMA * (xa * xa - 1.0))
            t1 = ta + da
            x1 = xa - da[:, None] * dx
            for _k in range(3):
                f, jf = _fj(x1, a, b, c)
                x1 = x1 - _solve(_hjac(x1, jf, t1), (1 - t1)[:, None] * _GAMMA * (x1 * x1 - 1.0) + t1[:, None] * f)
            f, _jf = _fj(x1, a, b, c)
            res = np.sqrt(np.sum(np.abs((1 - t1)[:, None] * _GAMMA * (x1 * x1 - 1.0) + t1[:, None] * f) ** 2, axis=1))
            nx = np.sqrt(np.sum(np.abs(x1) ** 2, axis=1))
            conv = np.isfinite(nx) & np.isfinite(res) & (res <= 1e-10 * (1.0 + nx * nx))
            acc, rej = act[conv], act[~conv]
            x[acc] = x1[conv]
            t[acc] = t1[conv]
            dt[acc] = np.minimum(dt[acc] * 1.5, 0.2)
            dt[rej] *= 0.5
            alive[rej[dt[rej] < 1e-13]] = False
            alive[acc[nx[conv] > 1e8]] = False
            done |= t >= 1.0
        sel = np.flatnonzero(done & alive)
        xs, a, b, c = x[sel], a_all[sel], b_all[sel], c_all[sel]
        for _ in range(8):
            f, jf = _fj(xs, a, b, c)
            xs = xs - _solve(jf, f)
    out: list[list[np.ndarray]] = [[] for _ in range(n)]
    for k, s in zip(owner[sel], xs):
        if np.all(np.isfinite(s)) and np.max(np.abs(s.imag)) <= 1e-6 * (1.0 + np.max(np.abs(s.real))):
            out[k].append(s.real.copy())
    return out


def _fj(x: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """f(x) y su jacobiano para cada camino (a: (P, 3, 3, 3), b: (P, 3, 3), c: (P, 3))."""
    ax = np.matmul(a, x[:, None, :, None])[..., 0]  # (A_k x)_i
    f = np.sum((ax + b) * x[:, None, :], axis=2) + c
    return f, 2.0 * ax + b


def _hjac(x: np.ndarray, jf: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Jacobiano de H = (1 − t)·γ·(x² − 1) + t·f."""
    jac = t[:, None, None] * jf
    diag = (1 - t)[:, None] * _GAMMA * 2.0 * x
    jac[:, 0, 0] += diag[:, 0]
    jac[:, 1, 1] += diag[:, 1]
    jac[:, 2, 2] += diag[:, 2]
    return jac


def _cross(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.stack([u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1], u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2],
                     u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]], axis=1)


def _solve(jac: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    """jac⁻¹·rhs para cada fila (3×3, por la regla de Cramer: con matrices tan pequeñas, más deprisa que
    linalg.solve; 0 donde jac es singular)."""
    r0, r1, r2 = jac[:, 0], jac[:, 1], jac[:, 2]
    c12, c20, c01 = _cross(r1, r2), _cross(r2, r0), _cross(r0, r1)
    det = np.sum(r0 * c12, axis=1)
    ok = np.isfinite(det) & (np.abs(det) > 1e-300)
    out = (c12 * rhs[:, 0:1] + c20 * rhs[:, 1:2] + c01 * rhs[:, 2:3]) / np.where(ok, det, 1.0)[:, None]
    out[~ok] = 0.0
    return out


_LEVI = np.zeros((3, 3, 3))
for _i, _j, _k in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
    _LEVI[_i, _j, _k], _LEVI[_i, _k, _j] = 1.0, -1.0


def _tangency(g1: Constraint, g2: Constraint, e: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """h(r) = e·(∇g1 × ∇g2) (una cuádrica): en la curva g1 = g2 = 0, cero donde e está en el plano de las normales
    (donde e·r es máximo o mínimo en la curva)."""
    k = np.einsum("lmn,l,mi,nj->ij", _LEVI, e, g1.a, g2.a)
    quad = 4.0 * 0.5 * (k + k.T)
    lin = 2.0 * (np.einsum("lmn,l,mi,n->i", _LEVI, e, g1.a, g2.b) + np.einsum("lmn,l,m,ni->i", _LEVI, e, g1.b, g2.a))
    const = float(np.einsum("lmn,l,m,n->", _LEVI, e, g1.b, g2.b))
    scale = max(float(np.abs(quad).max()), float(np.abs(lin).max()), abs(const), 1e-300)
    return quad / scale, lin / scale, const / scale


def _system(gs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (np.array([g[0] for g in gs]), np.array([g[1] for g in gs]), np.array([g[2] for g in gs]))


def _conic(g: Constraint, o: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    """g en el plano o + s·p1 + t·p2: coeficientes de s², st, t², s, t y 1."""
    pm = np.stack([p1, p2], axis=1)
    q = pm.T @ g.a @ pm
    lin = pm.T @ (2.0 * g.a @ o + g.b)
    return np.array([q[0, 0], 2.0 * q[0, 1], q[1, 1], lin[0], lin[1], float(g.value(o)[0])])


def _cval(c: np.ndarray, s: float, t: float) -> float:
    return c[0] * s * s + c[1] * s * t + c[2] * t * t + c[3] * s + c[4] * t + c[5]


def _polish_conics(c2, c3, s: float, t: float) -> tuple[float, float] | None:
    for _ in range(30):
        f = np.array([_cval(c2, s, t), _cval(c3, s, t)])
        jac = np.array([[2 * c2[0] * s + c2[1] * t + c2[3], c2[1] * s + 2 * c2[2] * t + c2[4]],
                        [2 * c3[0] * s + c3[1] * t + c3[3], c3[1] * s + 2 * c3[2] * t + c3[4]]])
        if abs(np.linalg.det(jac)) <= 1e-300:
            break
        step = np.linalg.solve(jac, f)
        if not np.all(np.isfinite(step)):
            break
        s, t = s - step[0], t - step[1]
        if np.max(np.abs(step)) <= 1e-15 * (1.0 + abs(s) + abs(t)):
            break
    scale = 1.0 + s * s + t * t
    if abs(_cval(c2, s, t)) <= 1e-7 * scale * (np.abs(c2).max() + 1e-300) and \
            abs(_cval(c3, s, t)) <= 1e-7 * scale * (np.abs(c3).max() + 1e-300):
        return s, t
    return None


def _conic_conic(c2: np.ndarray, c3: np.ndarray) -> list[tuple[float, float]]:
    """Puntos (s, t) de dos cónicas (por la resultante en t: un polinomio de grado 4 en s)."""
    if not (np.all(np.isfinite(c2)) and np.all(np.isfinite(c3))):
        return []
    P = np.polynomial.polynomial
    a2, b2, g2 = c2[2], np.array([c2[4], c2[1]]), np.array([c2[5], c2[3], c2[0]])
    a3, b3, g3 = c3[2], np.array([c3[4], c3[1]]), np.array([c3[5], c3[3], c3[0]])
    x = P.polysub(a2 * g3, a3 * g2)
    y = P.polysub(a2 * b3, a3 * b2)
    z = P.polysub(P.polymul(b2, g3), P.polymul(b3, g2))
    res = np.trim_zeros(np.asarray(P.polysub(P.polymul(x, x), P.polymul(y, z)), dtype=float), "b")
    scale = float(np.abs(np.r_[c2, c3]).max()) or 1.0
    if len(res) < 2 or float(np.abs(res).max()) <= 1e-24 * scale ** 4:
        return []
    out = []
    for s in np.roots(res[::-1]):
        if abs(s.imag) > 1e-6 * (1.0 + abs(s.real)):
            continue
        s = float(s.real)
        for c in (c2, c3):
            qa, qb, qc = c[2], c[1] * s + c[4], c[0] * s * s + c[3] * s + c[5]
            ts = []
            if abs(qa) > 1e-14 * max(abs(qb), abs(qc), 1e-300):
                disc = qb * qb - 4 * qa * qc
                if disc >= -1e-8 * qb * qb:
                    sq = np.sqrt(max(disc, 0.0))
                    ts = [(-qb + sq) / (2 * qa), (-qb - sq) / (2 * qa)]
            elif abs(qb) > 1e-300:
                ts = [-qc / qb]
            for t in ts:
                st = _polish_conics(c2, c3, s, t)
                if st is not None:
                    out.append(st)
    return out


def _plane_basis(pl: Constraint) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = pl.b / np.linalg.norm(pl.b)
    o = -pl.c * pl.b / float(pl.b @ pl.b)
    p1, p2 = _perp(n)
    return o, p1, p2


def _three(cs: list[Constraint]) -> list[np.ndarray]:
    """Puntos donde se cortan tres superficies."""
    if any(c.degenerate for c in cs):
        return []
    planes = [c for c in cs if c.plane]
    curved = [c for c in cs if not c.plane]
    if len(planes) == 3:
        b0, b1, b2 = planes[0].b, planes[1].b, planes[2].b
        c12, c20, c01 = _cross3(b1, b2), _cross3(b2, b0), _cross3(b0, b1)
        det = float(b0 @ c12)
        if abs(det) <= 1e-11 * float(np.linalg.norm(b0) * np.linalg.norm(b1) * np.linalg.norm(b2)):
            return []
        return [-(planes[0].c * c12 + planes[1].c * c20 + planes[2].c * c01) / det]
    if len(planes) == 2:
        line = _solve_line(np.array([planes[0].b, planes[1].b]), -np.array([planes[0].c, planes[1].c]))
        return [] if line is None else _line_roots(curved[0], *line)
    if len(planes) == 1:
        o, p1, p2 = _plane_basis(planes[0])
        st = _conic_conic(_conic(curved[0], o, p1, p2), _conic(curved[1], o, p1, p2))
        return [o + s * p1 + t * p2 for s, t in st]
    return []  # (tres cuádricas: con solve_quadrics, todas a la vez)


def _directions() -> list[np.ndarray]:
    """Los tres ejes, torcidos. (Los puntos de −e son los mismos que los de e: los de máximo y los de mínimo.)"""
    out = []
    for k in range(3):
        e = np.zeros(3)
        e[k] = 1.0
        e = e + _EPS_TILT * _TILT
        out.append(e / np.linalg.norm(e))
    return out


def region_box(constraints: list[tuple], big: float = BIG) -> tuple[np.ndarray, np.ndarray]:
    """Caja [lo, hi] de la región {r : g(r) ≤ 0 para cada (A, b, c)}, con g(r) = rᵀAr + b·r + c. Infinito donde no
    tiene cota; vacía (lo > hi) si no tiene ningún punto.

    Las cuentas, en coordenadas normalizadas (la región, de tamaño ~1 cerca del origen: ``_frame``) y dentro de la caja
    ±``REACH`` (o ``big`` cm, si es menos): lo que llega a ella no tiene cota (con una caja de 2e7 cm alrededor de una
    región de 1 cm, los redondeos no dejaban ver sus puntos)."""
    if not constraints:
        return np.full(3, -np.inf), np.full(3, np.inf)
    x0, scale = _frame(constraints)
    base = []
    for a, b, c in constraints:
        a = np.asarray(a, dtype=float)
        b = np.asarray(b, dtype=float)
        base.append(Constraint(scale * scale * a, scale * (2.0 * a @ x0 + b), float(x0 @ a @ x0 + b @ x0 + c)))
    if _bounded(base):  # (la caja de cada superficie ya acota la región: no hace falta la de las cuentas)
        n_lo, n_hi, _t = _box_in(base, None)
        if np.any(n_hi < n_lo):
            return np.full(3, np.inf), np.full(3, -np.inf)
        return x0 + scale * n_lo, x0 + scale * n_hi
    lim = min(REACH, big / scale)
    n_lo, n_hi, touched = _box_in(base, lim)
    if touched:
        # Sin cota en algún sentido: los ejes en que la caja crece con la de las cuentas tampoco la tienen (un cilindro
        # oblicuo sin tapas no tiene cota en ningún eje, aunque llegue antes a la caja por z).
        s_lo, s_hi, _t = _box_in(base, lim / 8.0)
        tol = 1e-6 * lim
        # (En los ejes con cota, lo mayor de las dos: lejos, los puntos tienen menos cifras.)
        n_lo = np.where(np.abs(n_lo - s_lo) > tol, -np.inf, np.minimum(n_lo, s_lo))
        n_hi = np.where(np.abs(n_hi - s_hi) > tol, np.inf, np.maximum(n_hi, s_hi))
    if np.any(n_hi < n_lo):
        return np.full(3, np.inf), np.full(3, -np.inf)
    with np.errstate(invalid="ignore"):
        return (np.where(np.isfinite(n_lo), x0 + scale * n_lo, -np.inf),
                np.where(np.isfinite(n_hi), x0 + scale * n_hi, np.inf))


def _bounded(cons: list[Constraint]) -> bool:
    """¿Acotan la región las cajas de sus superficies (una esfera, un cilindro con tapas en su eje…)?"""
    from .extent import halfspace_box

    lo, hi = np.full(3, -np.inf), np.full(3, np.inf)
    for c in cons:
        a = c.a
        q = np.array([a[0, 0], 2 * a[0, 1], 2 * a[0, 2], a[1, 1], 2 * a[1, 2], a[2, 2], c.b[0], c.b[1], c.b[2], c.c])
        l2, h2 = halfspace_box(q, -1)
        lo, hi = np.maximum(lo, l2), np.minimum(hi, h2)
    return bool(np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)))


def _box_in(base: list[Constraint], lim: float | None) -> tuple[np.ndarray, np.ndarray, bool]:
    """La caja (en coordenadas normalizadas) de la región dentro de |r_k| ≤ lim (sin ``lim``: la región está acotada)
    y si llega a ella."""
    cons = list(base)
    for k in range(3 if lim is not None else 0):
        for sgn in (1.0, -1.0):
            b = np.zeros(3)
            b[k] = sgn
            cons.append(Constraint(np.zeros((3, 3)), b, -lim))
    pts = []
    systems = []
    for e in _directions():
        for g in cons:
            pts += _single(g, e)
        for g1, g2 in itertools.combinations(cons, 2):
            if g1.plane and g2.plane:
                continue
            if g1.plane or g2.plane:
                pl, g = (g1, g2) if g1.plane else (g2, g1)
                if not pl.degenerate and not g.degenerate:
                    pts += _plane_curve(pl, g, e)
            elif not g1.degenerate and not g2.degenerate:
                systems.append(_system([(g1.a, g1.b, g1.c), (g2.a, g2.b, g2.c), _tangency(g1, g2, e)]))
    for trio in itertools.combinations(cons, 3):
        if all(not c.plane for c in trio):
            if not any(c.degenerate for c in trio):
                systems.append(_system([(c.a, c.b, c.c) for c in trio]))
        else:
            pts += _three(list(trio))
    for sols in solve_quadrics(systems):
        pts += sols
    allp = np.array(pts, dtype=float).reshape(-1, 3)
    allp = allp[np.all(np.isfinite(allp), axis=1)]
    if len(allp):
        feas = np.ones(len(allp), dtype=bool)
        for c in cons:
            feas &= c.value(allp) <= c.tol(allp)
        good = allp[feas]
        if len(good):
            n_lo, n_hi = good.min(axis=0), good.max(axis=0)
            touched = lim is not None and bool(np.any(n_lo <= -0.999 * lim) or np.any(n_hi >= 0.999 * lim))
            return n_lo, n_hi, touched
    return np.full(3, np.inf), np.full(3, -np.inf), False


def _frame(constraints) -> tuple[np.ndarray, float]:
    """Un centro y una escala para normalizar: los puntos notables de las superficies (el centro de una cuádrica, en
    las direcciones en que lo tiene; el punto de un plano más cerca del origen) y sus tamaños (semiejes, la caja de
    lo que acotan)."""
    from .extent import halfspace_box

    lo, hi = np.full(3, -np.inf), np.full(3, np.inf)
    pts = []
    sizes = []
    for a, b, c in constraints:
        a = np.asarray(a, dtype=float)
        bb = np.asarray(b, dtype=float)
        c = float(c)
        q = np.array([a[0, 0], 2 * a[0, 1], 2 * a[0, 2], a[1, 1], 2 * a[1, 2], a[2, 2], bb[0], bb[1], bb[2], c])
        l2, h2 = halfspace_box(q, -1)  # (g ≤ 0: el lado F < 0 de F = g)
        lo, hi = np.maximum(lo, l2), np.minimum(hi, h2)
        amax = float(np.abs(a).max())
        if amax <= 1e-12 * max(float(np.abs(bb).max()), 1e-300):
            if float(bb @ bb) > 0:
                pts.append(-c * bb / float(bb @ bb))
            continue
        lam, vec = np.linalg.eigh(a)
        nz = np.abs(lam) > 1e-9 * float(np.abs(lam).max())
        rhs = vec.T @ (-0.5 * bb)
        u = np.zeros(3)
        u[nz] = rhs[nz] / lam[nz]
        center = vec @ u
        f0 = float(center @ a @ center + bb @ center + c)
        pts.append(center)
        sizes += [float(np.sqrt(abs(f0 / v))) for v in lam[nz] if f0 != 0.0]
    fin = np.isfinite(lo) & np.isfinite(hi) & (hi >= lo)
    with np.errstate(invalid="ignore"):
        x0 = np.where(fin, 0.5 * (lo + hi), 0.0)
    sizes += [float(v) for v in (hi - lo)[fin]]
    if pts:
        p = np.array(pts)
        p = p[np.all(np.isfinite(p), axis=1)]
        if len(p):
            x0 = np.where(fin, x0, np.median(p, axis=0))
            sizes += [float(v) for v in np.ptp(p, axis=0)] + [float(np.max(np.abs(p - x0)))]
    scale = max([v for v in sizes if np.isfinite(v) and v > 0], default=1.0)
    return x0, float(scale)


__all__ = ["BIG", "region_box"]
