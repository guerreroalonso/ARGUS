"""Solapes entre cuerpos: lo que PENGEOM no comprueba pero hace fallar la simulación.

Dentro de cada módulo (y en la envolvente), LOCATE prueba las hijas por orden
y se queda con la primera cuya «envoltura» (sus superficies propias, con su
lado) contiene el punto; STEP, en cambio, solo vigila las superficies de la
lista del cuerpo en el que está la partícula (las propias y las de sus
cuerpos limitantes). Por eso, en PENGEOM, dos hijas de un mismo módulo solo
pueden compartir volumen si una es limitante (directa o indirecta) de la
otra; si no, una partícula puede atravesar la frontera sin enterarse. Y una
hija que asoma fuera de su módulo es invisible por fuera de él.

Aquí se comprueba, con puntos de muestra, que no hay:

* **solapes**: puntos dentro de las envolturas de dos hijas del mismo módulo
  sin relación de cuerpo limitante;
* **salientes**: puntos de una hija fuera de la envoltura de su módulo.

Las comprobaciones de los cambios comparan con la geometría de antes: solo
cuentan los problemas nuevos (un fichero antiguo puede traer los suyos).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .compiler import CompiledGeometry, body_title


@dataclass
class OverlapReport:
    """Pares de cuerpos que se solapan y cuerpos que asoman, con la fracción de puntos."""

    overlaps: dict[tuple[str, str], float] = field(default_factory=dict)  # (clave, clave) -> fracción
    escapes: dict[tuple[str, str], float] = field(default_factory=dict)  # (hija, módulo) -> fracción
    names: dict[str, str] = field(default_factory=dict)  # clave -> nombre para mostrar

    @property
    def ok(self) -> bool:
        return not self.overlaps and not self.escapes

    def minus(self, other: OverlapReport) -> OverlapReport:
        """Lo que hay en este informe y no en ``other`` (los problemas nuevos)."""
        out = OverlapReport(names=dict(self.names))
        out.overlaps = {k: v for k, v in self.overlaps.items() if k not in other.overlaps}
        out.escapes = {k: v for k, v in self.escapes.items() if k not in other.escapes}
        return out

    def message(self, limit: int = 3) -> str:
        parts = []
        for (a, b), _f in sorted(self.overlaps.items(), key=lambda kv: -kv[1])[:limit]:
            parts.append(f"{self.names.get(a, a)} y {self.names.get(b, b)} se solapan")
        for (a, b), _f in sorted(self.escapes.items(), key=lambda kv: -kv[1])[:limit]:
            parts.append(f"{self.names.get(a, a)} asoma fuera de {self.names.get(b, b)}")
        return "; ".join(parts)


def surface_signs(geo: CompiledGeometry, pts: np.ndarray, tol: float) -> np.ndarray:
    """Lado de cada punto respecto a cada superficie: -1, +1 o 0 (a menos de ``tol`` cm de ella)."""
    p = np.asarray(pts, dtype=float)
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    out = np.zeros((geo.nsurf, len(p)), dtype=np.int8)
    for i, s in enumerate(geo.surfaces):
        axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = s.q
        f = (axx * x * x + axy * x * y + axz * x * z + ayy * y * y + ayz * y * z + azz * z * z
             + ax * x + ay * y + az * z + a0)
        gx = 2.0 * axx * x + axy * y + axz * z + ax
        gy = axy * x + 2.0 * ayy * y + ayz * z + ay
        gz = axz * x + ayz * y + 2.0 * azz * z + az
        g = np.sqrt(gx * gx + gy * gy + gz * gz)
        near = np.abs(f) <= tol * g
        out[i] = np.where(near, 0, np.where(f < 0, -1, 1))
    return out


def _envelope(geo: CompiledGeometry, kb: int, signs: np.ndarray) -> np.ndarray:
    inside = np.ones(signs.shape[1], dtype=bool)
    for ks, side in geo.body(kb).own_surfaces():
        inside &= signs[ks - 1] == side
    return inside


def _closure(geo: CompiledGeometry) -> list[set[int]]:
    """Cuerpos limitantes (directos e indirectos) de cada cuerpo."""
    out: list[set[int]] = [set() for _ in range(geo.nbodys + 1)]
    for b in geo.bodies:  # los limitantes se definen antes: su cierre ya está
        acc: set[int] = set()
        for lb in b.limiting:
            if 1 <= lb <= geo.nbodys:
                acc.add(lb)
                if not geo.body(lb).is_module:
                    acc |= out[lb]
        out[b.index] = acc
    return out


def body_label_keys(geo: CompiledGeometry) -> list[str]:
    """Clave estable de cada cuerpo para comparar informes (etiqueta, clon o envolvente)."""
    from .identity import body_keys

    return [repr(k) for k in body_keys(geo)]


def check_overlaps(geo: CompiledGeometry, pts: np.ndarray, tol: float, keys: list[str] | None = None,
                   max_points: int = 4000) -> OverlapReport:
    """Solapes y salientes que se ven en los puntos ``pts``."""
    keys = keys if keys is not None else body_label_keys(geo)
    rep = OverlapReport()
    n = len(pts)
    if n == 0 or not geo.ok:
        return rep
    signs = surface_signs(geo, pts, tol)
    closure = _closure(geo)
    env_cache: dict[int, np.ndarray] = {}

    def env(kb: int) -> np.ndarray:
        e = env_cache.get(kb)
        if e is None:
            e = env_cache[kb] = _envelope(geo, kb, signs)
        return e

    def name(kb: int) -> str:
        k = keys[kb - 1]
        rep.names.setdefault(k, body_title(geo, kb))
        return k

    for m in geo.bodies:
        if not m.is_module or m.origin == "ct":
            continue
        kids = [d for d in dict.fromkeys(m.daughters) if d != m.index]
        if not kids:
            continue
        stack = np.array([env(d) for d in kids])
        count = stack.sum(axis=0)
        cand = np.nonzero(count > 1)[0]
        if cand.size:
            if cand.size > max_points:
                cand = cand[:: cand.size // max_points + 1]
            cols = stack[:, cand].T
            uniq, freq = np.unique(cols, axis=0, return_counts=True)
            for row, cnt in zip(uniq, freq):
                idx = [kids[i] for i in np.nonzero(row)[0]]
                for a in range(len(idx)):
                    for b in range(a + 1, len(idx)):
                        i, j = idx[a], idx[b]
                        if j in closure[i] or i in closure[j]:
                            continue
                        key = tuple(sorted((name(i), name(j))))
                        rep.overlaps[key] = rep.overlaps.get(key, 0.0) + cnt / n
        if m.origin != "enclosure":
            em = env(m.index)
            for d in kids:
                out = env(d) & ~em
                if out.any():
                    rep.escapes[(name(d), name(m.index))] = float(out.mean())
    return rep


def sample_box(lo, hi, n: int, rng: np.random.Generator) -> np.ndarray:
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    return lo + rng.random((n, 3)) * (hi - lo)


def new_problems(old: CompiledGeometry, new: CompiledGeometry, boxes, n: int = 30000, seed: int = 17,
                 renames: dict[str, str] | None = None) -> OverlapReport:
    """Solapes y salientes que hay en ``new`` y no había en ``old``, con puntos de las cajas ``boxes``.

    ``boxes``: lista de cajas (lo, hi) finitas; los puntos se reparten entre ellas.
    ``renames``: etiquetas cambiadas (para reconocer los mismos cuerpos).
    """
    from .identity import _rename_key, body_keys

    rng = np.random.default_rng(seed)
    boxes = [(np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)) for lo, hi in boxes]
    boxes = [(lo, hi) for lo, hi in boxes if np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))
             and np.all(hi >= lo)]
    if not boxes:
        return OverlapReport()
    per = max(1, n // len(boxes))
    pts = np.vstack([sample_box(lo, hi, per, rng) for lo, hi in boxes])
    size = max(float(np.max(hi - lo)) for lo, hi in boxes) or 1.0
    tol = 1e-9 * max(size, max(float(np.abs(np.r_[lo, hi]).max()) for lo, hi in boxes))
    old_keys = [repr(_rename_key(k, renames or {})) for k in body_keys(old)]
    before = check_overlaps(old, pts, tol, old_keys)
    after = check_overlaps(new, pts, tol)
    return after.minus(before)


def changed_elsewhere(old: CompiledGeometry, new: CompiledGeometry, boxes, labels: list[str],
                      renames: dict[str, str] | None = None, n: int = 30000, seed: int = 23) -> dict[str, float]:
    """Cuerpos que cambian aunque no sean los editados: {nombre: fracción de puntos}.

    Al cambiar la forma de un elemento solo pueden cambiar de cuerpo los puntos
    que estaban en él (o en lo que tiene dentro) o que pasan a estarlo. Si
    cambian otros (p. ej. porque el cambio mueve una superficie que otro cuerpo
    también usa dentro de un módulo girado), el cambio no es local. Los clones
    se ignoran (copian lo que clonan y cambian con ello).
    """
    from .extent import solid_bodies
    from .identity import _rename_key, body_keys

    renames = renames or {}
    rng = np.random.default_rng(seed)
    boxes = [(np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)) for lo, hi in boxes]
    boxes = [(lo, hi) for lo, hi in boxes if np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))
             and np.all(hi >= lo)]
    if not boxes or not old.ok or not new.ok:
        return {}
    per = max(1, n // len(boxes))
    pts = np.vstack([sample_box(lo, hi, per, rng) for lo, hi in boxes])
    from .locate import Locator

    ib_old, _ = Locator(old).locate(pts)
    ib_new, _ = Locator(new).locate(pts)
    k_old = [_rename_key(k, renames) for k in body_keys(old)]
    k_new = body_keys(new)
    edited: set = set()
    for lab in labels:
        for geo, keys, name in ((old, k_old, lab), (new, k_new, renames.get(lab, lab))):
            b = geo.find_body(name)
            if b is not None:
                edited |= {keys[k - 1] for k in solid_bodies(geo, b.index)}

    def key(keys, kb):
        return keys[kb - 1] if 1 <= kb <= len(keys) else ("fuera",)

    def clone_related(k) -> bool:
        return k[0] == "clone" or any(t[0] == "clone" and t[1] == k[1] for t in k_old + k_new if k[0] == "alias")

    out: dict[str, float] = {}
    for a, b in zip(ib_old, ib_new):
        ka, kb_ = key(k_old, int(a)), key(k_new, int(b))
        if ka == kb_ or ka in edited or kb_ in edited or clone_related(ka) or clone_related(kb_):
            continue
        name = body_title(old, int(a)) if 1 <= a <= old.nbodys else "fuera de la geometría"
        out[name] = out.get(name, 0.0) + 1.0 / len(pts)
    return out


def focus_box(geo: CompiledGeometry, kb: int, bounds) -> tuple[np.ndarray, np.ndarray]:
    """Caja de un elemento (con lo que tiene dentro), recortada a la escena ``bounds``."""
    from .extent import element_box

    lo, hi = element_box(geo, kb)
    b_lo, b_hi = (np.asarray(v, dtype=float) for v in bounds)
    lo = np.where(np.isfinite(lo), np.maximum(lo, b_lo), b_lo)
    hi = np.where(np.isfinite(hi), np.minimum(hi, b_hi), b_hi)
    if np.any(hi < lo):  # fuera de la escena: su propia caja (si es finita)
        lo2, hi2 = element_box(geo, kb)
        if np.all(np.isfinite(lo2)) and np.all(np.isfinite(hi2)):
            return lo2, hi2
        return b_lo, b_hi
    return lo, hi


def _edited(geo: CompiledGeometry, names: list[str]) -> set[int]:
    """Los elementos ``names`` con lo que tienen dentro y las copias de sus clones (cambian con ellos)."""
    from .extent import solid_bodies
    from .placement import _copies_of

    out: set[int] = set()
    for name in names:
        b = geo.find_body(name)
        if b is not None:
            out |= solid_bodies(geo, b.index)
    return out | _copies_of(geo, out)


def reshaped_elsewhere(old: CompiledGeometry, new: CompiledGeometry, labels: list[str],
                       renames: dict[str, str] | None = None) -> list[int]:
    """Cuerpos de ``old`` que no son los editados (ni copias suyas) y cuyas superficies propias cambian.

    Exacto (sin muestreo): se comparan sus superficies propias con su lado
    (``placement.same_shape``). Pasa si el cambio mueve una superficie que
    también usan ellos (un módulo movido que comparte un plano con su madre,
    una superficie que gira con un módulo...).
    """
    from .identity import _rename_key, body_keys
    from .placement import same_shape

    renames = renames or {}
    k_old = [_rename_key(k, renames) for k in body_keys(old)]
    k_new = body_keys(new)
    new_of: dict = {}
    for i, key in enumerate(k_new, start=1):
        new_of.setdefault(key, i)
    edited = {k_old[k - 1] for k in _edited(old, labels)}
    edited |= {k_new[k - 1] for k in _edited(new, [renames.get(lab, lab) for lab in labels])}
    out = []
    for b in old.bodies:
        key = k_old[b.index - 1]
        j = new_of.get(key)
        if b.origin == "enclosure" or key in edited or j is None:
            continue
        if not same_shape(old, b.index, new, j):
            out.append(b.index)
    return out


def _strictly_inside(geo: CompiledGeometry, kb: int, pts: np.ndarray, rel: float = 1e-9) -> np.ndarray:
    """¿Están los puntos dentro de todas las superficies propias de ``kb`` (no sobre ellas)?"""
    from .edit import inside_own_surfaces

    return inside_own_surfaces(geo, kb, pts, tol=-rel)


def thin_intrusions(old: CompiledGeometry, new: CompiledGeometry, labels: list[str], bounds,
                    renames: dict[str, str] | None = None) -> dict[int, float]:
    """Cuerpos de ``old`` (finos: el muestreo no los ve) que los elementos editados pasan a cubrir.

    Rayos por la caja nueva de cada elemento, en la geometría de antes: los
    tramos de cuerpos que no son el elemento ni lo que lo aloja y que quedan
    dentro de sus superficies nuevas (y antes no lo estaban) serían solapes.
    """
    from .extent import solid_bodies
    from .placement import around
    from .raycheck import count_hits, ray_samples

    renames = renames or {}
    found: dict[int, float] = {}
    m = 16 if len(labels) <= 2 else 8
    closure = _closure(old)
    for lab in labels:
        bo, bn = old.find_body(lab), new.find_body(renames.get(lab, lab))
        if bo is None or bn is None:
            continue
        allowed = solid_bodies(old, bo.index) | around(old, bo.index, closure) | {old.nbodys + 1}
        lo, hi = focus_box(new, bn.index, bounds)
        pts, bodies, rays, nrays = ray_samples(old, lo, hi, m=m, skip=allowed)
        if not len(pts):
            continue
        now = _strictly_inside(new, bn.index, pts) & ~_strictly_inside(old, bo.index, pts)
        for k, f in count_hits(now, bodies, rays, nrays).items():
            found[k] = max(found.get(k, 0.0), f)
    return found


def guard(bounds, labels: list[str], renames: dict[str, str] | None = None, n: int = 30000):
    """Comprobación para ``GeoController.edit``: el cambio no puede crear solapes ni salientes.

    Se muestrean la escena (``bounds``, un poco ampliada) y las cajas de los
    elementos ``labels`` (y de sus clones) antes y después del cambio; se
    comprueba sin muestreo que los demás cuerpos conservan sus superficies y,
    con rayos, que no se cubre un cuerpo fino.
    """
    from .edit import EditError

    b_lo, b_hi = (np.asarray(v, dtype=float) for v in bounds)
    pad = 0.05 * (b_hi - b_lo) + 1e-9
    scene = (b_lo - pad, b_hi + pad)

    def check(old: CompiledGeometry, new: CompiledGeometry) -> None:
        boxes = [scene]
        for geo, names in ((old, labels), (new, [(renames or {}).get(lab, lab) for lab in labels])):
            edited = _edited(geo, names)
            tops = [k for k in sorted(edited) if geo.body(k).mother not in edited]
            boxes.extend(focus_box(geo, k, scene) for k in tops)
        rep = new_problems(old, new, boxes, n=n, renames=renames)
        if not rep.ok:
            raise EditError("El cambio dejaría la geometría mal hecha: " + rep.message() + ". PENGEOM no lo "
                            "comprueba, pero las partículas cruzarían esas fronteras sin verlas.")
        reshaped = reshaped_elsewhere(old, new, labels, renames)
        others = changed_elsewhere(old, new, boxes, labels, renames, n=n)
        if reshaped or others:
            worst = [body_title(old, k) for k in reshaped]
            worst += [f"{k} ({100 * v:.1f} %)" for k, v in sorted(others.items(), key=lambda kv: -kv[1])
                      if k not in worst]
            raise EditError(f"El cambio también cambiaría otros cuerpos: {', '.join(worst[:3])}. Suele pasar al usar "
                            "una superficie que comparten con otros elementos (al moverla o al girarla con un "
                            "módulo); use una superficie nueva, o mueva el módulo con «Girar» o «Pegar a…», que "
                            "antes le dan superficies propias.")
        thin = thin_intrusions(old, new, labels, scene, renames)
        if thin:
            names = ", ".join(body_title(old, k) if k <= old.nbodys else "fuera de la geometría"
                              for k, _f in sorted(thin.items(), key=lambda kv: -kv[1])[:3])
            raise EditError(f"El cambio dejaría la geometría mal hecha: se solaparía con {names} (muy fino: los "
                            "puntos de muestra no lo ven, un rayo sí). PENGEOM no lo comprueba, pero las partículas "
                            "cruzarían esa frontera sin verla.")

    return check
