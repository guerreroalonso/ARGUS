"""Extensión de un cuerpo o módulo: la caja [lo, hi] que lo contiene justo.

Sirve para pegar módulos uno junto a otro (cara con cara) y para encuadrar.
El trazador de rayos se recibe como argumento (``render.raytrace.RayTracer``).
Se calcula en dos pasos:

1. **Analítico**, con las superficies propias del elemento (las que lo
   limitan, con su lado): el interior de una esfera o elipsoide acota las
   tres coordenadas; el de un cilindro, las dos perpendiculares a su eje; un
   plano o un par de planos perpendiculares a un eje acotan ese eje. La
   caja del elemento está dentro de la intersección de esas cajas (y es
   exactamente ella en cajas, cilindros y esferas).
2. **Con rayos** (el trazador de la CPU, que sigue la lógica de STEP):
   desde fuera de esa caja, rayos paralelos a cada eje contra el elemento y
   todo lo que tiene dentro; el punto más avanzado de todos, refinando la
   rejilla alrededor del mejor rayo, da cada cara. Corrige lo que el paso 1
   no puede saber (una esfera cortada por un plano, un cuerpo girado).

Si los dos pasos coinciden (a 1e-9 del tamaño), se toma el valor analítico,
que es exacto (y «redondo»).

Desde la undécima revisión (0.17.3), la caja sale primero de las ecuaciones
(``exact_box``: el máximo de cada coordenada en las superficies propias de
cada cuerpo, ``support.region_box``): los rayos no veían lo fino ni lo
inclinado (una lámina de 50 µm girada 80° salía 0,37 cm más corta y
«Envolver» la cortaba). Los rayos y el muestreo quedan para los ejes en que
las superficies no acotan nada (un cuerpo que solo cierra su módulo).
"""

from __future__ import annotations

import numpy as np

AXIS_TOL = 1e-12


def solid_bodies(geo, kb: int) -> set[int]:
    """El elemento y todo lo que tiene dentro (hijas de un módulo, cuerpos limitantes de un cuerpo)."""
    out = {kb}
    stack = [kb]
    while stack:
        b = geo.body(stack.pop())
        kids = b.daughters if b.is_module else b.limiting
        for k in kids:
            if 1 <= k <= geo.nbodys and k not in out:
                out.add(k)
                stack.append(k)
    return out


def members_box(geo, kb: int, extent, bounds=None) -> tuple[np.ndarray, np.ndarray]:
    """La caja ``extent`` del elemento ampliada con las de sus cuerpos limitantes (y los de estos).

    Un cuerpo limitante puede asomar fuera del cuerpo que limita (p. ej. una esfera
    que recortan dos cajas vecinas): al copiar o envolver el cuerpo, esa parte va
    con él y también hay que comprobar dónde queda. Si sus superficies no lo acotan
    (un cubo girado: planos oblicuos), su caja se busca con LOCATE (``own_box``,
    dentro de ``bounds``, la escena). En un módulo no hace falta: su caja
    (``element_extent``) ya es la de todo lo que tiene dentro.
    """
    lo = np.asarray(extent[0], dtype=float).copy()
    hi = np.asarray(extent[1], dtype=float).copy()
    if geo.body(kb).is_module:
        return lo, hi
    for k in solid_bodies(geo, kb):
        if k == kb:
            continue
        try:
            blo, bhi = own_box(geo, k, bounds)
        except Exception:  # noqa: BLE001 - sin cota: se usa la del elemento
            continue
        if np.all(np.isfinite(blo)) and np.all(np.isfinite(bhi)) and np.all(bhi >= blo):
            lo, hi = np.minimum(lo, blo), np.maximum(hi, bhi)
    return lo, hi


def scene_box(geo) -> tuple[np.ndarray, np.ndarray]:
    """Caja de la escena (``bounds.estimate_bounds``, ampliada un 30 %); se guarda con la geometría."""
    box = getattr(geo, "_argus_scene", None)
    if box is None:
        from .bounds import estimate_bounds

        lo, hi = (np.asarray(v, dtype=float) for v in estimate_bounds(geo, locator=locator_for(geo)))
        size = float(np.max(hi - lo)) if np.all(np.isfinite(hi - lo)) else 0.0
        pad = 0.3 * size + 1e-6 * max(1.0, float(np.max(np.abs(np.r_[lo, hi]))))
        box = (lo - pad, hi + pad)
        _keep(geo, "_argus_scene", box)
    return box


def locator_for(geo):
    """LOCATE de ``geo`` (se guarda con la geometría para no rehacerlo en cada comprobación)."""
    loc = getattr(geo, "_argus_locator", None)
    if loc is None:
        from .locate import Locator

        loc = Locator(geo)
        _keep(geo, "_argus_locator", loc)
    return loc


def _keep(geo, name: str, value) -> None:
    try:
        setattr(geo, name, value)
    except (AttributeError, TypeError):  # pragma: no cover - geometría sin atributos libres
        pass


def own_constraints(geo, kb: int) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Las superficies propias de ``kb`` (sin la esfera de la envolvente) como regiones g(r) = rᵀAr + b·r + c ≤ 0."""
    out = []
    for ks, side in geo.body(kb).own_surfaces():
        sf = geo.surface(ks)
        if sf.origin == "enclosure":
            continue
        a, b, c = _matrix(sf.q)
        out.append((-side * a, -side * b, -side * c))
    return out


def exact_box(geo, kb: int) -> tuple[np.ndarray, np.ndarray]:
    """Caja exacta de lo que está dentro de las superficies propias de ``kb`` (``support.region_box``): infinita en los
    ejes que no acotan; vacía (lo > hi) si no dejan nada. Se guarda con la geometría."""
    cache = getattr(geo, "_argus_exact", None)
    if cache is None:
        cache = {}
        _keep(geo, "_argus_exact", cache)
    if kb not in cache:
        from .support import region_box

        try:
            cache[kb] = region_box(own_constraints(geo, kb))
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):  # pragma: no cover - nunca debería pasar
            cache[kb] = analytic_box(geo, kb)
    lo, hi = cache[kb]
    return lo.copy(), hi.copy()


def exact_element_box(geo, kb: int) -> tuple[np.ndarray, np.ndarray]:
    """Caja exacta del elemento: dentro de sus superficies y de las de los módulos que lo contienen
    (``locate_bound``: un cuerpo de un módulo no pasa de él; en el maniquí adulto, los trozos del pulmón llegaban a
    20 cm del pecho). Un cuerpo, con los huecos de sus limitantes (lo que asoma de ellos lo añade ``members_box``);
    un módulo, con sus hijas (también lo que asome). Infinita en los ejes en que ni así tiene cota; vacía si no hay
    nada."""
    lo, hi = locate_bound(geo, kb)
    if not geo.body(kb).is_module:
        return lo, hi
    if np.any(hi < lo):
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    members = solid_bodies(geo, kb)
    quick: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def analytic_in(k: int) -> tuple[np.ndarray, np.ndarray]:
        """La caja de sus superficies dentro de la de sus módulos (dentro del elemento): al momento."""
        if k not in quick:
            a_lo, a_hi = analytic_box(geo, k)
            m = geo.body(k).mother
            if m in members and m != k:
                m_lo, m_hi = analytic_in(m)
                a_lo, a_hi = np.maximum(a_lo, m_lo), np.minimum(a_hi, m_hi)
            quick[k] = (a_lo, a_hi)
        return quick[k]

    for k in sorted(members - {kb}):
        # (Lo que sus superficies y las de sus módulos ya dejan dentro de lo de hasta ahora no puede asomar: sin la
        # cuenta exacta.)
        a_lo, a_hi = analytic_in(k)
        if np.any(a_hi < a_lo) or (np.all(a_lo >= lo) and np.all(a_hi <= hi)):
            continue
        k_lo, k_hi = locate_bound(geo, k)
        if np.any(k_hi < k_lo):
            continue
        lo, hi = np.minimum(lo, k_lo), np.maximum(hi, k_hi)
    return lo, hi


def own_box(geo, kb: int, bounds=None) -> tuple[np.ndarray, np.ndarray]:
    """Caja finita del elemento ``kb`` (con lo que tiene dentro): la analítica y, en los ejes que sus superficies
    no acotan (un cubo girado: planos oblicuos), una que contiene sus puntos.

    Esos puntos se buscan con LOCATE alrededor de puntos que seguro que están
    en él (``_seed_points``), en cajas cada vez mayores hasta que no tocan
    sus caras (y la caja se amplía lo que separa las muestras); si no hay
    semillas, en ``bounds`` (la escena; si no se da, ``scene_box``) y, si ni
    así, con rayos. Es para muestrear: puede ser algo mayor que el elemento.
    Se guarda con la geometría.
    """
    lo, hi = exact_element_box(geo, kb)
    if (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))) or np.any(hi < lo):
        return lo, hi
    a_lo, a_hi = element_box(geo, kb)
    lo, hi = np.where(np.isfinite(lo), lo, a_lo), np.where(np.isfinite(hi), hi, a_hi)
    if (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))) or np.any(hi < lo):
        return lo, hi
    if bounds is None:
        bounds = scene_box(geo)
    key = (kb, tuple(np.asarray(bounds[0], dtype=float)), tuple(np.asarray(bounds[1], dtype=float)))
    cache = getattr(geo, "_argus_own_boxes", None)
    if cache is None:
        cache = {}
        _keep(geo, "_argus_own_boxes", cache)
    if key not in cache:
        box = _sampled_box(geo, kb, lo, hi, bounds)
        if box is None:
            from .raycheck import tracer_for

            box = element_extent(geo, tracer_for(geo), kb, bounds)
        cache[key] = box
    return cache[key]


def _seed_points(geo, kb: int) -> np.ndarray:
    """Puntos que LOCATE sitúa en ``kb`` (o en lo que tiene dentro), buscados en los puntos notables de sus superficies.

    Centros de elipsoides y cilindros, puntos de los planos más cercanos al
    origen, vértices de tres planos y sus centros: un cuerpo pequeño que las
    muestras al azar de la escena no ven.
    """
    from .bounds import _surface_points

    members = solid_bodies(geo, kb)
    cands: list[np.ndarray] = []
    planes: list[np.ndarray] = []
    for k in members:
        for ks, _side in geo.body(k).own_surfaces():
            s = geo.surface(ks)
            if s.origin == "enclosure":
                continue
            q = np.asarray(s.q, dtype=float)
            try:
                cands.extend(np.asarray(p, dtype=float) for p in _surface_points(q, s.kplane))
            except (ValueError, np.linalg.LinAlgError):  # pragma: no cover - superficie degenerada
                pass
            if float(np.max(np.abs(q[:6]))) <= 1e-14 * max(1.0, float(np.max(np.abs(q)))) and len(planes) < 12:
                planes.append(q[6:])
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            for k in range(j + 1, len(planes)):
                a = np.array([planes[i][:3], planes[j][:3], planes[k][:3]])
                if abs(np.linalg.det(a)) > 1e-12 * max(1.0, float(np.max(np.abs(a)))) ** 3:
                    cands.append(np.linalg.solve(a, -np.array([planes[i][3], planes[j][3], planes[k][3]])))
    pts = np.array([c for c in cands if c.shape == (3,) and np.all(np.isfinite(c))]).reshape(-1, 3)
    if not len(pts):
        return pts
    pts = np.vstack([pts, pts.mean(axis=0)[None, :]])
    scale = 1e-7 * max(1.0, float(np.max(np.abs(pts))))
    rng = np.random.default_rng(0)
    pts = np.vstack([pts] + [pts + rng.normal(scale=scale, size=pts.shape) for _ in range(2)])
    ib, _ = locator_for(geo).locate(pts)
    return pts[np.isin(ib, list(members))]


def _sampled_box(geo, kb: int, a_lo, a_hi, bounds, n: int = 20000):
    """Caja (con holgura) de los puntos de ``kb`` en los ejes que no acotan sus superficies (None: no se encuentran)."""
    members = list(solid_bodies(geo, kb))
    loc = locator_for(geo)
    rng = np.random.default_rng(1)
    f_lo, f_hi = (np.asarray(v, dtype=float) for v in bounds)
    free_lo, free_hi = ~np.isfinite(a_lo), ~np.isfinite(a_hi)
    seeds = _seed_points(geo, kb)
    if not len(seeds):
        lo, hi = np.where(free_lo, f_lo, a_lo), np.where(free_hi, f_hi, a_hi)
        if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))) or np.any(hi <= lo):
            return None
        pts = rng.uniform(lo, hi, (5 * n, 3))
        seeds = pts[np.isin(loc.locate(pts)[0], members)]
        if not len(seeds):
            return None
    c = 0.5 * (seeds.min(axis=0) + seeds.max(axis=0))
    r = max(float(np.max(np.ptp(seeds, axis=0))), 1e-6 * max(1.0, float(np.max(np.abs(c)))))
    limit = 4 * float(np.max(f_hi - f_lo)) if np.all(np.isfinite(f_hi - f_lo)) else 1e7
    while r <= limit:
        lo, hi = np.where(free_lo, c - r, a_lo), np.where(free_hi, c + r, a_hi)
        pts = rng.uniform(lo, hi, (n, 3))
        mine = pts[np.isin(loc.locate(pts)[0], members)]
        if len(mine):
            m_lo, m_hi = mine.min(axis=0), mine.max(axis=0)
            edge = 0.05 * (hi - lo)
            if not (np.any(free_lo & (m_lo <= lo + edge)) or np.any(free_hi & (m_hi >= hi - edge))):
                pad = 2.0 * (hi - lo) / n ** (1 / 3)  # lo que separa las muestras
                return np.where(free_lo, m_lo - pad, a_lo), np.where(free_hi, m_hi + pad, a_hi)
            c = np.where(free_lo | free_hi, 0.5 * (m_lo + m_hi), c)
        r *= 4.0
    return None


def sample_members(geo, locator, kb: int, lo, hi, n: int, rng: np.random.Generator, bounds=None
                   ) -> tuple[np.ndarray, list[int]]:
    """Puntos de ``kb`` y de lo que tiene dentro: (puntos, miembros que no se han podido muestrear).

    Primero ``n`` puntos al azar en [lo, hi]; cada miembro con pocos puntos
    (pequeño o fino) se muestrea además en su propia caja (``own_box``). Si
    LOCATE no lo encuentra (tapado por otro, como en algunos maniquíes), se
    usan los puntos de dentro de sus superficies; si tampoco, los de rayos.
    Un miembro de caja acotada que no tiene ninguno está vacío; uno sin cota
    del que no se encuentra nada no se ha podido muestrear. Un módulo con
    hijas no hace falta verlo: lo ven sus hijas.
    """
    from .raycheck import ray_points

    members = solid_bodies(geo, kb)
    lo, hi = (np.asarray(v, dtype=float) for v in (lo, hi))
    parts = []
    count: dict[int, int] = {}
    if np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.all(hi >= lo):
        pts = rng.uniform(lo, hi, (n, 3))
        ib, _ = locator.locate(pts)
        keep = np.isin(ib, list(members))
        parts.append(pts[keep])
        ks, cs = np.unique(ib[keep], return_counts=True)
        count = {int(k): int(c) for k, c in zip(ks, cs)}
    missing = []
    for k in sorted(members):
        b = geo.body(k)
        if (b.is_module and any(d != k for d in b.daughters)) or count.get(k, 0) >= 50:
            continue
        a_lo, a_hi = analytic_box(geo, k)
        bounded = bool(np.all(np.isfinite(a_lo)) and np.all(np.isfinite(a_hi)))
        if bounded and np.any(a_hi < a_lo):
            continue  # sus superficies no dejan nada: vacío
        try:
            klo, khi = own_box(geo, k, bounds)
        except Exception:  # noqa: BLE001
            klo, khi = np.full(3, np.inf), np.full(3, -np.inf)
        found = np.zeros((0, 3))
        if np.all(np.isfinite(klo)) and np.all(np.isfinite(khi)) and np.all(khi >= klo):
            pts = rng.uniform(klo, khi, (max(500, n // 10), 3))
            ib, _ = locator.locate(pts)
            found = pts[ib == k]
            if not len(found) and not count.get(k):
                size = float(np.max(khi - klo))
                env = _inside_envelope(geo, k, pts, 1e-9 * max(size, float(np.max(np.abs(np.r_[klo, khi])))))
                found = pts[env] if env.any() else ray_points(geo, klo, khi, {k})
        if len(found):
            parts.append(found)
        elif not count.get(k) and not bounded:
            missing.append(k)
    return (np.vstack(parts) if parts else np.zeros((0, 3))), missing


def _inside_envelope(geo, kb: int, pts: np.ndarray, margin: float) -> np.ndarray:
    """¿Están los puntos dentro de todas las superficies propias de ``kb``, a más de ``margin`` cm de ellas?"""
    ok = np.ones(len(pts), dtype=bool)
    for ks, side in geo.body(kb).own_surfaces():
        a, b, c = _matrix(geo.surface(ks).q)
        f = np.einsum("ij,jk,ik->i", pts, a, pts) + pts @ b + c
        grad = 2.0 * pts @ a + b
        g = np.linalg.norm(grad, axis=1)
        ok &= side * f > margin * g
    return ok


def _matrix(q) -> tuple[np.ndarray, np.ndarray, float]:
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = (float(v) for v in q)
    a = np.array([[axx, 0.5 * axy, 0.5 * axz], [0.5 * axy, ayy, 0.5 * ayz], [0.5 * axz, 0.5 * ayz, azz]])
    return a, np.array([ax, ay, az]), a0


def halfspace_box(q, side: int) -> tuple[np.ndarray, np.ndarray]:
    """Caja [lo, hi] (con infinitos) del lado ``side`` de una cuádrica.

    Puntero de lado de PENGEOM: -1 es la región F(r) < 0 y +1 la región
    F(r) > 0; es decir, la región -side·F(r) < 0.
    """
    lo = np.full(3, -np.inf)
    hi = np.full(3, np.inf)
    a, b, c = _matrix(q)
    a, b, c = -side * a, -side * b, -side * c
    scale = max(float(np.abs(a).max()), float(np.abs(b).max()), 1e-300)
    if float(np.abs(a).max()) <= 1e-14 * scale:  # plano: b·r + c < 0
        k = int(np.argmax(np.abs(b)))
        if b[k] == 0.0:  # sin términos en r: F es constante y el lado es todo el espacio o nada
            return (lo, hi) if c < 0 else (np.full(3, np.inf), np.full(3, -np.inf))
        if np.abs(np.delete(b, k)).max() <= AXIS_TOL * abs(b[k]):
            x = -c / b[k]
            if b[k] > 0:
                hi[k] = x
            else:
                lo[k] = x
        return lo, hi
    lam, vec = np.linalg.eigh(a)
    lmax = float(np.abs(lam).max())
    pos = lam > 1e-9 * lmax
    if np.any(lam < -1e-9 * lmax):
        return lo, hi  # hiperboloides, conos y exteriores: no acotan
    # Direcciones acotadas: las de autovalor positivo; en las nulas no puede haber término lineal.
    rhs = vec.T @ (-0.5 * b)
    if np.any(~pos) and np.any(np.abs(rhs[~pos]) > 1e-12 * max(1.0, float(np.abs(b).max()))):
        return lo, hi  # paraboloide
    u = np.zeros(3)
    u[pos] = rhs[pos] / lam[pos]
    r0 = vec @ u
    k_val = -(c + float(b @ r0) + float(r0 @ a @ r0))  # región: (r-r0)ᵀA(r-r0) < k_val
    if k_val <= 0:
        return np.full(3, np.inf), np.full(3, -np.inf)  # vacía
    # Pseudoinversa en el subespacio acotado: (A⁺)_ii por eje.
    ainv = (vec[:, pos] / lam[pos]) @ vec[:, pos].T
    null = vec[:, ~pos]
    for i in range(3):
        if null.size and np.abs(null[i]).max() > AXIS_TOL:
            continue  # el eje tiene componente a lo largo de una dirección no acotada
        h = float(np.sqrt(k_val * max(ainv[i, i], 0.0)))
        lo[i] = r0[i] - h
        hi[i] = r0[i] + h
    return lo, hi


def analytic_box(geo, kb: int) -> tuple[np.ndarray, np.ndarray]:
    """Intersección de las cajas de las superficies propias del elemento (paso 1).

    La esfera de la envolvente automática (radio 1e7, que GEOMIN añade a los
    elementos de primer nivel) no cuenta: no dice nada de su tamaño.
    """
    lo = np.full(3, -np.inf)
    hi = np.full(3, np.inf)
    for ks, side in geo.body(kb).own_surfaces():
        if geo.surface(ks).origin == "enclosure":
            continue
        l2, h2 = halfspace_box(geo.surface(ks).q, side)
        lo = np.maximum(lo, l2)
        hi = np.minimum(hi, h2)
    return lo, hi


def _support(tracer, visible, lo, hi, axis: int, sign: int, grid: int = 40, fine: int = 12,
             levels: int = 30) -> float | None:
    """Coordenada más avanzada del elemento en el sentido sign·eje (rayos paralelos al eje).

    Primero una rejilla de ``grid``×``grid`` rayos en toda la sección de la
    caja; después, rejillas de ``fine``×``fine`` cada vez más pequeñas
    alrededor del mejor rayo, hasta 1e-10 del tamaño.
    """
    others = [i for i in range(3) if i != axis]
    size = float(np.max(hi - lo))
    margin = 0.05 * size + 1e-6 * max(1.0, float(np.abs(np.r_[lo, hi]).max()))
    start = (hi[axis] + margin) if sign > 0 else (lo[axis] - margin)
    d = np.zeros(3)
    d[axis] = -sign
    box_lo = np.array([lo[others[0]], lo[others[1]]], dtype=float)
    box_hi = np.array([hi[others[0]], hi[others[1]]], dtype=float)
    c_lo, c_hi = box_lo.copy(), box_hi.copy()
    best = None
    best_uv = None
    n = grid
    for _ in range(levels):
        u = np.linspace(c_lo[0], c_hi[0], n)
        v = np.linspace(c_lo[1], c_hi[1], n)
        uu, vv = np.meshgrid(u, v, indexing="ij")
        o = np.empty((uu.size, 3))
        o[:, axis] = start
        o[:, others[0]] = uu.ravel()
        o[:, others[1]] = vv.ravel()
        t, kbs, _ = tracer.render(o, np.broadcast_to(d, o.shape), visible)
        hit = kbs > 0
        if not hit.any():
            if best is None and n < 4 * grid:
                n *= 2  # objeto pequeño: rejilla más fina
                continue
            break
        x = start - sign * t[hit]
        i = int(np.argmax(sign * x))
        val = float(x[i])
        if best is None or sign * val > sign * best:
            best = val
            best_uv = np.array([uu.ravel()[hit][i], vv.ravel()[hit][i]], dtype=float)
        # Rejilla más fina alrededor del mejor rayo.
        step = (c_hi - c_lo) / max(n - 1, 1)
        if float(step.max()) <= 1e-10 * max(size, 1e-300):
            break
        c_lo = np.maximum(best_uv - 2 * step, box_lo)
        c_hi = np.minimum(best_uv + 2 * step, box_hi)
        n = fine
    return best


def element_box(geo, kb: int) -> tuple[np.ndarray, np.ndarray]:
    """Cota analítica (paso 1) del elemento con todo lo que tiene dentro.

    Un cuerpo no pasa de sus superficies propias (sus limitantes pueden
    sobresalir: son vecinos que lo recortan). Un módulo es la unión de sus
    superficies y las de sus hijas (en PENGEOM una hija puede asomar fuera de
    las superficies del módulo, como en el ejemplo example2 de PenGeomJar).
    """
    if not geo.body(kb).is_module:
        return analytic_box(geo, kb)
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for k in solid_bodies(geo, kb):
        l2, h2 = analytic_box(geo, k)
        if np.any(h2 < l2):
            continue
        lo = np.minimum(lo, l2)
        hi = np.maximum(hi, h2)
    return lo, hi


def element_extent(geo, tracer, kb: int, fallback: tuple[np.ndarray, np.ndarray]
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Caja [lo, hi] del cuerpo o módulo ``kb`` (con todo lo que tiene dentro): la exacta (``exact_element_box``) y,
    en los ejes que sus superficies no acotan, la de los rayos (paso 2, dentro de ``fallback``: la escena)."""
    e_lo, e_hi = exact_element_box(geo, kb)
    if np.all(np.isfinite(e_lo)) and np.all(np.isfinite(e_hi)) and np.all(e_hi >= e_lo):
        lo, hi = _with_ct(geo, kb, e_lo, e_hi)
        size = float(np.max(hi - lo)) or 1.0
        return snap(lo, size), snap(hi, size)
    a_lo, a_hi = element_box(geo, kb)
    if np.all(e_hi >= e_lo):
        a_lo, a_hi = np.where(np.isfinite(e_lo), e_lo, a_lo), np.where(np.isfinite(e_hi), e_hi, a_hi)
    f_lo, f_hi = (np.asarray(v, dtype=float) for v in fallback)
    lo = np.where(np.isfinite(a_lo), a_lo, f_lo)
    hi = np.where(np.isfinite(a_hi), a_hi, f_hi)
    if np.any(hi < lo):  # caja analítica vacía o fuera de la geometría: la de la escena
        lo, hi = f_lo.copy(), f_hi.copy()
    if tracer is None:
        return lo, hi
    visible = np.zeros(geo.nbodys + 2, dtype=np.int64)
    for k in solid_bodies(geo, kb):
        visible[k] = 1
    size = float(np.max(hi - lo)) or 1.0
    out_lo, out_hi = lo.copy(), hi.copy()
    have = bool(np.all(e_hi >= e_lo))
    for axis in range(3):
        for sign in (1, -1):
            if have and np.isfinite(e_hi[axis] if sign > 0 else e_lo[axis]):
                continue  # (el exacto: los rayos se quedaban cortos con lo fino o inclinado)
            val = _support(tracer, visible, lo, hi, axis, sign)
            if val is None:
                continue
            exact = a_hi[axis] if sign > 0 else a_lo[axis]
            if np.isfinite(exact):
                if abs(val - exact) <= 1e-9 * size:
                    val = float(exact)  # el analítico es exacto
                else:
                    val = min(val, exact) if sign > 0 else max(val, exact)
            if sign > 0:
                out_hi[axis] = val
            else:
                out_lo[axis] = val
    out_lo, out_hi = _with_ct(geo, kb, out_lo, out_hi)
    # Un cuerpo sin cota que en esa sección está fuera de la caja de partida: los rayos de un lado lo encuentran
    # más allá del otro. La caja no puede quedar del revés.
    out_lo, out_hi = np.minimum(out_lo, out_hi), np.maximum(out_lo, out_hi)
    return snap(out_lo, size), snap(out_hi, size)


def _with_ct(geo, kb: int, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Con la caja CT dentro: entera. Los rayos atraviesan los vóxeles vacíos (material 0), pero la caja CT ocupa
    entera su caja: pegar algo al CT es pegarlo a sus caras, no al contorno del paciente."""
    ct = getattr(geo, "ct_body", None)
    if ct is not None and ct in solid_bodies(geo, kb):
        c_lo, c_hi = analytic_box(geo, ct)
        if np.all(np.isfinite(c_lo)) and np.all(np.isfinite(c_hi)):
            lo, hi = np.minimum(lo, c_lo), np.maximum(hi, c_hi)
    return lo, hi


SOURCE_SAMPLES = 60_000  # puntos al azar en cada búsqueda (LOCATE: menos de 1 µs cada uno en el adulto)
SOURCE_SLAB = 10_000  # puntos en cada franja entre lo encontrado y el borde de la búsqueda
SOURCE_LOCAL = 600  # puntos en cada caja, cada vez más pequeña, alrededor del punto más avanzado de cada cara
SOURCE_GROW = (0.1, 0.7, 1.9, 4.0)  # donde el cuerpo no tiene cota, la escena ampliada esas veces su tamaño
SOURCE_FACE = (0.1, 0.01, 0.001)  # franjas por fuera de cada cara, en toda su sección (de lo que mide en ese eje)


def locate_bound(geo, kb: int) -> tuple[np.ndarray, np.ndarray]:
    """Cota de lo que LOCATE puede situar en ``kb``: la caja exacta del cuerpo dentro de las de los módulos que lo
    contienen (LOCATE solo baja a un módulo desde dentro de él). Puede tener infinitos; lo > hi: vacío.

    La raíz (el módulo de arriba cuando PENGEOM no añade la envolvente, o la envolvente) no cuenta: es donde LOCATE
    empieza y no mira sus superficies antes de buscar en sus hijas (una hija que asoma fuera del módulo raíz, en una
    geometría mal hecha, se encuentra igual fuera)."""
    cache = getattr(geo, "_argus_locate_bounds", None)
    if cache is None:
        cache = {}
        _keep(geo, "_argus_locate_bounds", cache)
    if kb not in cache:
        # La caja exacta de sus superficies (``exact_box``; hasta 0.17.2, la intersección de las cajas de cada
        # superficie, infinita en cuanto una estaba girada) dentro de la de su módulo.
        lo, hi = exact_box(geo, kb)
        m = geo.body(kb).mother
        if m and 1 <= m <= geo.nbodys and m != kb:
            b = geo.body(m)
            if b.origin != "enclosure" and b.mother and 1 <= b.mother <= geo.nbodys:
                m_lo, m_hi = locate_bound(geo, m)
                lo, hi = np.maximum(lo, m_lo), np.minimum(hi, m_hi)
        cache[kb] = (lo, hi)
    lo, hi = cache[kb]
    return lo.copy(), hi.copy()


def source_extent(geo, kb: int, bounds, n: int = SOURCE_SAMPLES, seed: int = 0
                  ) -> tuple[np.ndarray | None, np.ndarray | None, str]:
    """Caja [lo, hi] de los puntos que LOCATE sitúa en el cuerpo ``kb``: los que penhmain acepta en una fuente extensa
    con ``kb`` como cuerpo activo (SOURC0/SOURCE sortean en la caja y miran IBODY). Un cuerpo, sin sus limitantes; un
    módulo, sin sus hijas. Devuelve (lo, hi, estado): «ok»; «vacío» (sus superficies no dejan nada o LOCATE no lo
    encuentra en ningún punto: lo, hi = None) o «sin cota» (en algún eje sigue más allá de lo que se busca, cuatro veces
    la escena ``bounds`` por cada lado: la caja es la parte de dentro).

    No usa la lógica de STEP (``element_extent``): en geometrías con solapes (los maniquíes del ORNL), STEP y LOCATE no
    dan el mismo cuerpo en algunos sitios, y lo que cuenta para la fuente es LOCATE. Todo está dentro de la cota del
    cuerpo (``locate_bound``: sus superficies y las de los módulos que lo contienen), así que sus puntos se buscan ahí:
    entera donde es finita (aunque quede fuera de la escena) y, donde no lo es, en la escena, ampliada mientras el
    cuerpo llegue a su borde. Primero en toda la búsqueda (y en los puntos notables de sus superficies, o con rayos si
    es muy fino); luego alrededor de lo encontrado, ampliándolo mientras crezca (un cuerpo de unos mm³ con una cota de
    kilómetros), y en las franjas entre lo encontrado y el borde de la búsqueda (trozos lejos); por último, cada cara:
    en cajas cada vez más pequeñas alrededor de su punto más avanzado (hasta 1e-8 del tamaño) y en franjas finas por
    fuera de ella en toda su sección (otra parte del cuerpo que llegue más lejos en otro sitio: una punta estrecha).
    """
    from .raycheck import ray_points

    a_lo, a_hi = locate_bound(geo, kb)
    if np.any(a_hi < a_lo):
        return None, None, "vacío"
    f_lo, f_hi = (np.asarray(v, dtype=float) for v in bounds)
    if not (np.all(np.isfinite(f_lo)) and np.all(np.isfinite(f_hi))) or np.any(f_hi <= f_lo):
        f_lo, f_hi = np.where(np.isfinite(a_lo), a_lo, -1.0), np.where(np.isfinite(a_hi), a_hi, 1.0)
    loc = locator_for(geo)
    rng = np.random.default_rng(seed)
    scene = float(np.max(f_hi - f_lo))
    fin_lo, fin_hi = np.isfinite(a_lo), np.isfinite(a_hi)
    reach = SOURCE_GROW[-1] * scene
    levels = (*SOURCE_GROW, np.inf)

    def region(g: float):
        """La búsqueda: la cota del cuerpo; donde no la tiene, la escena ampliada ``g`` veces su tamaño por cada lado
        (como mucho ``reach``). Una cota finita más lejos que eso (kilómetros) se mira primero cerca de la escena."""
        w = min(g, SOURCE_GROW[-1]) * scene
        lo = np.where(fin_lo, a_lo, f_lo - w)
        hi = np.where(fin_hi, a_hi, f_hi + w)
        if np.isfinite(g):
            lo = np.where(fin_lo & (a_lo < f_lo - reach), f_lo - g * scene, lo)
            hi = np.where(fin_hi & (a_hi > f_hi + reach), f_hi + g * scene, hi)
        return lo, hi

    def mine(boxes, m):
        """Puntos al azar (m en cada caja) que LOCATE sitúa en kb, con una sola llamada; uno por caja."""
        pts, owner = [], []
        for j, (lo, hi) in enumerate(boxes):
            if np.all(hi > lo):
                pts.append(rng.uniform(lo, hi, (m, 3)))
                owner.append(np.full(m, j))
        if not pts:
            return [np.zeros((0, 3)) for _ in boxes]
        pts = np.vstack(pts)
        owner = np.concatenate(owner)
        ok = loc.locate(pts)[0] == kb
        return [pts[ok & (owner == j)] for j in range(len(boxes))]

    # Lo primero, puntos del cuerpo: al azar en la búsqueda, los notables de sus superficies y, si son pocos (fino o
    # pequeño), los de rayos, que lo cruzan aunque los puntos no caigan en él. Si no hay nada cerca de la escena, en
    # toda su cota (un cuerpo lejos de lo demás).
    seeds = _seed_points(geo, kb)
    seeds = seeds[loc.locate(seeds)[0] == kb] if len(seeds) else np.zeros((0, 3))
    level = 0
    while True:
        u_lo, u_hi = region(levels[level])
        found = [seeds]
        if np.all(u_hi > u_lo):
            found.append(mine([(u_lo, u_hi)], n)[0])
            if sum(len(h) for h in found) < 50:
                try:
                    pts = ray_points(geo, u_lo, u_hi, {kb}, m=32)
                except Exception:  # noqa: BLE001 - sin trazador, solo los puntos
                    pts = np.zeros((0, 3))
                if len(pts):
                    found.append(pts[loc.locate(pts)[0] == kb])
        hits = np.vstack(found)
        if len(hits):
            break
        if level == len(levels) - 1:
            return None, None, "vacío"
        level += 1
    if np.all(fin_lo) and np.all(fin_hi):
        # La cota es exacta (``locate_bound``: las superficies del cuerpo y de sus módulos a la vez) y el cuerpo
        # existe: esa es la caja (si sus limitantes le quitan un extremo, algo mayor que él; nunca menor: el
        # muestreo se quedaba corto con lo fino, una lámina inclinada de 0,9 µm dejaba fuera el 64 %).
        size = float(np.max(a_hi - a_lo)) or 1.0
        return snap(a_lo, size), snap(a_hi, size), "ok"
    b_lo, b_hi = hits.min(axis=0), hits.max(axis=0)
    u_lo, u_hi = np.minimum(u_lo, b_lo), np.maximum(u_hi, b_hi)
    faces = [(i, s) for i in range(3) for s in (1, -1)]
    best = {(i, s): hits[np.argmax(s * hits[:, i])] for i, s in faces}

    def take(h) -> bool:
        nonlocal b_lo, b_hi
        if not len(h):
            return False
        grown = bool(np.any(h.min(axis=0) < b_lo) or np.any(h.max(axis=0) > b_hi))
        b_lo, b_hi = np.minimum(b_lo, h.min(axis=0)), np.maximum(b_hi, h.max(axis=0))
        for (i, s), p in best.items():
            q = h[np.argmax(s * h[:, i])]
            if s * q[i] > s * p[i]:
                best[(i, s)] = q
        return grown

    def size_now() -> float:
        return float(np.max(b_hi - b_lo)) or float(np.max(u_hi - u_lo))

    def explore() -> None:
        """Alrededor de lo encontrado (con la mitad de su tamaño de margen) mientras crezca de forma apreciable y,
        más allá, hasta el borde de la búsqueda (con los otros dos ejes enteros): trozos lejos de lo encontrado."""
        for _ in range(12):
            for _ in range(12):
                span = float(np.max(b_hi - b_lo))
                pad = max(0.5 * span, 1e-6 * scene)
                before = (b_lo.copy(), b_hi.copy())
                take(mine([(np.maximum(b_lo - pad, u_lo), np.minimum(b_hi + pad, u_hi))], n)[0])
                if max(float(np.max(before[0] - b_lo)), float(np.max(b_hi - before[1]))) <= 1e-2 * max(span, 1e-300):
                    break  # lo que falta de cada cara lo afinan refine_faces
            size = size_now()
            pad = max(0.5 * size, 1e-6 * scene)
            boxes = []
            for i, s in faces:
                lo, hi = u_lo.copy(), u_hi.copy()
                if s > 0:
                    lo[i] = min(b_hi[i] + pad, u_hi[i])
                else:
                    hi[i] = max(b_lo[i] - pad, u_lo[i])
                boxes.append((lo, hi) if hi[i] - lo[i] > 1e-9 * size else (lo, lo))
            if not any([take(h) for h in mine(boxes, SOURCE_SLAB)]):
                break

    def shrink(live) -> None:
        """Alrededor del punto más avanzado de cada cara, en cajas cada vez más pequeñas (como _support con rayos)."""
        size = size_now()
        r = {f: 0.05 * size for f in live}
        for _ in range(60):
            live = [f for f in live if r[f] > 1e-8 * size]
            if not live:
                break
            boxes = []
            for i, s in live:
                p = best[(i, s)]
                lo, hi = p - r[(i, s)], p + r[(i, s)]
                if s > 0:
                    lo[i] = p[i]
                else:
                    hi[i] = p[i]
                boxes.append((np.maximum(lo, u_lo), np.minimum(hi, u_hi)))
            before = {f: best[f][f[0]] for f in live}
            for h in mine(boxes, SOURCE_LOCAL):
                take(h)
            for f in live:
                i, s = f
                r[f] *= 0.7 if s * best[f][i] > s * before[f] else 0.4  # se acerca al borde: más despacio

    def face_slabs() -> list:
        """Franjas por fuera de cada cara en toda su sección (cada vez más finas): las caras que llegan más lejos."""
        ext = np.maximum(b_hi - b_lo, 1e-6 * size_now())
        before = {f: best[f][f[0]] for f in faces}
        for frac in SOURCE_FACE:
            boxes = []
            for i, s in faces:
                lo, hi = b_lo - 0.05 * ext, b_hi + 0.05 * ext
                if s > 0:
                    lo[i], hi[i] = b_hi[i], b_hi[i] + frac * ext[i]
                else:
                    lo[i], hi[i] = b_lo[i] - frac * ext[i], b_lo[i]
                boxes.append((np.maximum(lo, u_lo), np.minimum(hi, u_hi)))
            for h in mine(boxes, SOURCE_SLAB // 2):
                take(h)
        return [f for f in faces if f[1] * best[f][f[0]] > f[1] * before[f]]

    def refine_faces() -> None:
        live = list(faces)
        for _ in range(4):
            shrink(live)
            live = face_slabs()
            if not live:
                break

    while True:
        explore()
        refine_faces()
        # ¿Llega al borde de la búsqueda donde la cota del cuerpo sigue? Entonces, más allá (la tolerancia es la del
        # muestreo: lo encontrado se queda algo antes del borde).
        tol = np.maximum(0.02 * (u_hi - u_lo), 1e-6 * size_now())
        open_lo = (b_lo <= u_lo + tol) & (u_lo > a_lo)
        open_hi = (b_hi >= u_hi - tol) & (u_hi < a_hi)
        if not (open_lo.any() or open_hi.any()):
            break
        wider = False
        while level < len(levels) - 1 and not wider:
            level += 1
            n_lo, n_hi = region(levels[level])
            wider = bool(np.any(open_lo & (n_lo < u_lo)) or np.any(open_hi & (n_hi > u_hi)))
        if not wider:
            break
        u_lo, u_hi = np.minimum(n_lo, b_lo), np.maximum(n_hi, b_hi)
    size = size_now()
    out_lo, out_hi = b_lo.copy(), b_hi.copy()
    # Donde la cota está a un paso (una cara plana, una esfera), su valor: sin el ruido del muestreo y sin quedarse
    # corta (la cota contiene al cuerpo).
    near = 1e-3 * size
    out_lo = np.where(fin_lo & (out_lo - a_lo <= near), a_lo, out_lo)
    out_hi = np.where(fin_hi & (a_hi - out_hi <= near), a_hi, out_hi)
    tol = np.maximum(0.02 * (u_hi - u_lo), 1e-6 * size)
    unbounded = bool(np.any((out_lo <= u_lo + tol) & (u_lo > a_lo)) or np.any((out_hi >= u_hi - tol) & (u_hi < a_hi)))
    return snap(out_lo, size), snap(out_hi, size), ("sin cota" if unbounded else "ok")


def snap(values, size: float) -> np.ndarray:
    """Quita el ruido de redondeo de las coordenadas (1599.999999999998 -> 1600).

    Se redondea a 1e-11 del tamaño del elemento: los coeficientes de las
    cuádricas llevan errores de ~1e-15 relativos que, al calcular el centro y
    los semiejes, se convierten en ~1e-12 del tamaño.
    """
    v = np.asarray(values, dtype=float)
    unit = 10.0 ** (np.floor(np.log10(max(float(size), 1e-300))) - 11)
    out = np.round(v / unit) * unit
    digits = int(max(0, -np.floor(np.log10(unit))))
    return np.round(out, digits) + 0.0
