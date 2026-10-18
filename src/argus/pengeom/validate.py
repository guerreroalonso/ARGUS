"""Comprobaciones adicionales: problemas que PENGEOM no detecta al leer.

GEOMIN acepta estas geometrías sin error, pero la simulación no hace lo que
el usuario espera. Todas se han comprobado con la sonda PENGEOM.
"""

from __future__ import annotations

import math

import numpy as np

from ..core.diagnostics import DiagnosticList
from .compiler import NXG, CompiledGeometry

# Límites de penhmain (penhmain.f 2024).
PENHMAIN_MAXMAT = 10


def _where(geo: CompiledGeometry, kb: int) -> dict:
    b = geo.body(kb)
    el = b.element
    kw = {}
    if el is not None and b.document is not None:
        kw["file"] = b.document.path
        kw["line"] = el.first_line
    return kw


def _name(geo: CompiledGeometry, kb: int) -> str:
    b = geo.body(kb)
    lab = geo.balias(kb).strip()
    kind = "módulo" if b.is_module else "cuerpo"
    return f"{kind} {kb}" + (f" («{lab}»)" if lab else "")


def table_overflows(geo: CompiledGeometry) -> dict[tuple, tuple[str, int, int, int]]:
    """Listas de GEOMIN que se desbordan sin aviso: {(identidad, qué): (qué, cuántas, máximo, número de cuerpo)}.

    GEOMIN guarda en KDGHT(KB, NXG) el número de hijas de un módulo (con él mismo
    y las repeticiones que añade por sus limitantes) y en KBODY(KB, NXG) el de
    cuerpos limitantes de un cuerpo, pero no comprueba que quepan (NXG = 250 en
    pengeom.f): al llegar a NXG, el último se escribe encima del contador, GEOMIN
    no da error y LOCATE deja partículas fuera de la geometría o en otro cuerpo
    (comprobado con la sonda). La envolvente automática reúne todos los elementos
    del nivel superior. La clave es la identidad del cuerpo (``identity.body_keys``):
    no cambia aunque cambie su número.
    """
    out = {}
    if not geo.ok:
        return out
    from .identity import body_keys

    keys = body_keys(geo)
    for b in geo.bodies:
        ident = keys[b.index - 1]
        if len(b.daughters) >= NXG:
            out[(ident, "hijas")] = ("hijas", len(b.daughters) - 1, NXG - 2, b.index)
        if len(b.limiting) >= NXG:
            out[(ident, "limitantes")] = ("limitantes", len(b.limiting), NXG - 1, b.index)
        if len(b.surfaces) >= NXG:
            # Solo pasa en el nivel superior: al leer, GEOMIN no deja pasar de 249 (2038/2043), pero después añade
            # la esfera de la envolvente a cada elemento sin madre sin comprobarlo.
            out[(ident, "superficies")] = ("superficies", len(b.surfaces) - 1, NXG - 2, b.index)
    return out


def _overflow_text(geo: CompiledGeometry, kb: int, what: str, n: int, most: int) -> str:
    who = "La envolvente (el nivel superior de la geometría)" if geo.body(kb).origin == "enclosure" else \
        f"El {_name(geo, kb)}"
    if what == "hijas":
        fix = "Agrupe elementos en módulos («Envolver») para que ninguno pase de ese número."
        return (f"{who} tiene {n} hijas y PENGEOM solo admite {most}: GEOMIN no da error, pero su lista de hijas "
                f"(KDGHT, NXG = {NXG}) se desborda y LOCATE deja partículas fuera de la geometría o en otro cuerpo. "
                + fix)
    if what == "superficies":
        return (f"{who} reúne {n} superficies (las suyas y las de sus hijas) y no está dentro de ningún módulo: "
                "GEOMIN le añade la esfera de la envolvente sin comprobar que quepa y su lista de superficies "
                f"(KSURF, NXG = {NXG}) se desborda; en el nivel superior PENGEOM solo admite {most}, y con más "
                "LOCATE deja partículas fuera de la geometría o en otro cuerpo. Métalo en un módulo («Envolver») o "
                "use menos superficies.")
    return (f"{who} está limitado por {n} cuerpos y PENGEOM solo admite {most}: GEOMIN no da error, pero su "
            f"lista de limitantes (KBODY, NXG = {NXG}) se desborda y la geometría queda mal construida.")


def new_overflows(old: CompiledGeometry | None, new: CompiledGeometry) -> list[str]:
    """Desbordamientos que ``new`` tiene y ``old`` no (o que crecen): mensajes para rechazar una edición.

    Se reconocen por la identidad del cuerpo (al quitar una hija, el módulo
    cambia de número y el desbordamiento que ya tenía no es nuevo); si esa
    identidad no estaba antes (p. ej. se ha cambiado la etiqueta), por su
    número, pero solo entre los que ya no están.
    """
    before = table_overflows(old) if old is not None and old.ok else {}
    out = []
    after = table_overflows(new)
    if not after:
        return out
    from .identity import body_keys

    old_ids = set(body_keys(old)) if old is not None and old.ok else set()
    new_ids = set(body_keys(new))
    gone = {(v[3], v[0]): v for key, v in before.items() if key[0] not in new_ids}
    for key, (what, n, most, kb) in after.items():
        prev = before.get(key)
        if prev is None and key[0] not in old_ids:
            prev = gone.get((kb, what))
        if prev is None or n > prev[1]:
            out.append(_overflow_text(new, kb, what, n, most))
    return out


def _planes_of(q) -> list[tuple[np.ndarray, float]]:
    """Los planos de una superficie: uno si es un plano y dos si es un par de planos paralelos (x² − a² = 0 girado y
    desplazado), como (n, d) con n·x = d, n unitario y su primera componente apreciable positiva; ninguno si no."""
    q = np.asarray(q, dtype=float)
    m = np.array([[q[0], 0.5 * q[1], 0.5 * q[2]], [0.5 * q[1], q[3], 0.5 * q[4]], [0.5 * q[2], 0.5 * q[4], q[5]]])
    lin, c0 = q[6:9], float(q[9])
    scale = float(np.max(np.abs(q))) or 1.0

    def fixed(n, d):
        k = int(np.argmax(np.abs(n) > 1e-6))
        return (n, d) if n[k] > 0 else (-n, -d)

    if float(np.max(np.abs(m))) <= 1e-14 * scale:
        norm = float(np.linalg.norm(lin))
        return [fixed(lin / norm, -c0 / norm)] if norm > 0 else []
    w, v = np.linalg.eigh(m)
    big = np.abs(w) > 1e-9 * float(np.max(np.abs(w)))
    if int(big.sum()) != 1:
        return []
    lam = float(w[big][0])
    n = v[:, big][:, 0]
    mu = float(lin @ n)
    if float(np.linalg.norm(lin - mu * n)) > 1e-9 * scale:
        return []
    disc = mu * mu - 4.0 * lam * c0
    if disc <= 0:
        return []
    r = math.sqrt(disc)
    return [fixed(n, (-mu - r) / (2.0 * lam)), fixed(n, (-mu + r) / (2.0 * lam))]


def coincident_walls(geo: CompiledGeometry) -> list[tuple[str, int]]:
    """Módulos con una pared en el mismo sitio que una cara de una de sus hijas, pero escritas como superficies
    distintas: (mensaje, módulo).

    GEOMIN lo acepta y LOCATE lo ve bien, pero al transportar (STEP) una partícula que cruza las dos a la vez
    puede notar solo la de la hija y seguir en el material del módulo, fuera de él, hasta la siguiente superficie
    (comprobado con PENGEOM: hasta varios cm). Pasa con una hija pegada a la pared, o al envolver cuerpos sin
    margen. Las superficies iguales ya las junta GEOMIN; aquí se buscan planos de superficies distintas (un plano
    suelto y uno de un par de planos, o dos pares que comparten un plano).
    """
    out: list[tuple[str, int]] = []
    if not geo.ok:
        return out
    planes: dict[int, list] = {}

    def own_planes(b) -> list[tuple[np.ndarray, float, int]]:
        got = []
        for ks, f in zip(b.surfaces, b.flags):
            if f >= 3 or geo.surface(ks).origin == "enclosure":
                continue
            if ks not in planes:
                planes[ks] = _planes_of(geo.surface(ks).q)
            got.extend((n, d, ks) for n, d in planes[ks])
        return got

    seen: set[tuple] = set()
    for m in geo.bodies:
        if not m.is_module or m.origin in ("enclosure", "ct"):
            continue
        walls = own_planes(m)
        if not walls:
            continue
        wn = np.array([n for n, _d, _k in walls])
        wd = np.array([d for _n, d, _k in walls])
        for kd in m.daughters:
            if kd == m.index:
                continue
            for n, d, ks in own_planes(geo.body(kd)):
                same = (np.abs(wn @ n) > 1.0 - 1e-12) & (np.abs(wd - d) <= 1e-9 * max(1.0, abs(d)))
                for i in np.flatnonzero(same):
                    k_wall = walls[i][2]
                    if k_wall == ks:
                        continue
                    # Una vez por pareja de superficies (los clones repiten las mismas).
                    key = (geo.surface(k_wall).clone_of or k_wall, geo.surface(ks).clone_of or ks)
                    if key in seen:
                        continue
                    seen.add(key)
                    wall = geo.surface(k_wall).label.strip() or k_wall
                    face = geo.surface(ks).label.strip() or ks
                    out.append((f"Una pared del {_name(geo, m.index)} (superficie {wall}) está en el mismo sitio que "
                                f"una cara del {_name(geo, kd)} (superficie {face}), pero son superficies distintas: "
                                "PENGEOM no da error, pero al transportar una partícula que las cruza a la vez puede "
                                "no notar la pared del módulo y seguir con su material fuera de él. Use la misma "
                                "superficie en los dos o separe un poco las caras (basta 1e-5 cm).", m.index))
    return out


def validate(geo: CompiledGeometry) -> DiagnosticList:
    diags = DiagnosticList()
    if not geo.ok:
        return diags
    # 0. Listas de GEOMIN que se desbordan sin aviso (NXG).
    for (_ident, _what), (what, n, most, kb) in table_overflows(geo).items():
        where = _where(geo, kb) if geo.body(kb).origin != "enclosure" else {}
        diags.error(_overflow_text(geo, kb, what, n, most), **where)
    # 1. Cuerpos limitantes que no son hijas del módulo de la hija que limitan.
    for m in geo.bodies:
        if not m.is_module:
            continue
        own = set(m.daughters)
        seen = set()
        for kb in m.daughters:
            if kb == m.index or kb in seen:
                continue
            seen.add(kb)
            d = geo.body(kb)
            if d.is_module:
                continue
            for lb in d.limiting:
                if lb not in own:
                    diags.warning(
                        f"El {_name(geo, lb)} limita al {_name(geo, kb)}, pero no está declarado como hija del "
                        f"{_name(geo, m.index)}. PENGEOM no da error, pero LOCATE no lo encuentra: un punto dentro "
                        f"de él se asigna al {_name(geo, kb)} (y a su material), mientras que las partículas que "
                        f"entran desde fuera sí lo ven. Añada 'BODY    ({geo.balias(lb) or lb:>4})' al módulo.",
                        **_where(geo, m.index))
    # 2. Demasiados materiales para penhmain.
    if geo.nmat > PENHMAIN_MAXMAT:
        diags.warning(f"La geometría usa materiales hasta el {geo.nmat}; penhmain admite como máximo "
                      f"{PENHMAIN_MAXMAT} (MAXMAT).")
    # 3. Cuerpos con material que no están limitados por ninguna superficie propia (la esfera que GEOMIN añade a
    # los del nivel superior no cuenta: sin superficies suyas, uno de ellos se queda con todo el espacio).
    for b in geo.bodies:
        if b.origin != "user" or b.is_module:
            continue
        if not any(f < 3 and geo.surface(ks).origin != "enclosure" for ks, f in zip(b.surfaces, b.flags)):
            diags.warning(f"El {_name(geo, b.index)} no tiene superficies limitantes propias.",
                          **_where(geo, b.index))
    # 3b. Paredes de un módulo en el mismo sitio que una cara de una hija, pero con otra superficie.
    for msg, kb in coincident_walls(geo):
        diags.warning(msg, **_where(geo, kb))
    # 4. Caja CT (vóxeles).
    _validate_ct(geo, diags)
    return diags


def _validate_ct(geo: CompiledGeometry, diags: DiagnosticList) -> None:
    ct = getattr(geo, "ct", None)
    if ct is None or geo.ct_body is None:
        # Sin CT también: un MATERIAL(9999) hace que GEOMIN active el modo CT sin caja CT.
        marked = [b.index for b in geo.bodies if b.material == 9999]
        for msg in ct_placement_errors(geo):
            diags.error(msg, **(_where(geo, marked[0]) if marked else {}))
        return
    from .ct import NDD

    where = _where(geo, geo.ct_body)
    nx, ny, nz = ct.shape
    if nz > NDD:
        diags.error(f"El CT tiene {nz} cortes (NZ) y penhmain admite como mucho {NDD}: se detendría al empezar "
                    "(CTDOSE0, IRETRN 3148). Vuelva a importarlo con vóxeles más gruesos en z.", code=3148, **where)
    checks = ct.__dict__.get("_argus_checks")
    if checks is None:  # una vez por CT (sus matrices no cambian)
        checks = (int(ct.material.min()) < 0, int(((ct.material > 0) & ~(ct.density > 0)).sum()))
        ct.__dict__["_argus_checks"] = checks
    if checks[0]:
        diags.error("El CT tiene vóxeles con material negativo.", **where)
    if checks[1]:
        diags.error(f"El CT tiene {checks[1]} vóxeles con material y densidad nula o negativa.", **where)
    for msg in ct_placement_errors(geo):
        diags.error(msg, **where)


def _ct_faces_moved(geo: CompiledGeometry) -> bool:
    """¿Están las caras compiladas de la caja CT donde no las pone su propia transformación?

    Así las pondría un módulo girado o desplazado que la contiene (también
    con PARINP): PENGEOM mueve las caras pero no el sistema de los vóxeles
    (SHPRD usa solo la transformación del CTBOX).
    """
    import numpy as np

    from .quadric import rotshf

    b = geo.body(geo.ct_body)
    xm, ym, zm = geo.ct.size
    base = [(0.0,) * 6 + (1.0, 0.0, 0.0, 0.0), (0.0,) * 6 + (-1.0, 0.0, 0.0, xm),
            (0.0,) * 6 + (0.0, 1.0, 0.0, 0.0), (0.0,) * 6 + (0.0, -1.0, 0.0, ym),
            (0.0,) * 6 + (0.0, 0.0, 1.0, 0.0), (0.0,) * 6 + (0.0, 0.0, -1.0, zm)]
    t = tuple(geo.ct_transform or (0.0,) * 6)
    ct_surfaces = [ks for ks in b.surfaces if geo.surface(ks).origin == "ct"][:6]
    if len(ct_surfaces) != 6:
        return False
    for q0, ks in zip(base, ct_surfaces):
        exp = np.asarray(rotshf(*t, q0), dtype=float)
        got = np.asarray(geo.surface(ks).q, dtype=float)
        if float(np.max(np.abs(exp - got))) > 1e-9 * max(1.0, float(np.max(np.abs(exp)))):
            return True
    return False


def _transformed_ancestor(geo: CompiledGeometry, kb: int) -> int | None:
    """El primer módulo que contiene a ``kb`` y lo gira o lo desplaza (con los PARINP de la geometría)."""
    import numpy as np

    from .edit import module_transform
    from .model import ModuleElement

    k = geo.body(kb).mother
    while k:
        m = geo.body(k)
        if m.is_module and isinstance(m.element, ModuleElement):
            r, d = module_transform(m.element, getattr(geo, "parinp", None) or None)
            if not (np.allclose(r, np.eye(3), atol=1e-12) and np.allclose(d, 0.0, atol=1e-12)):
                return k
        k = m.mother
    return None


def ct_placement_errors(geo: CompiledGeometry) -> list[str]:
    """Lo que PENGEOM no comprueba y dejaría mal el CT: caras de la caja movidas y otras cajas con MATERIAL(9999)."""
    out: list[str] = []
    if not geo.ok:
        return out
    kb = getattr(geo, "ct_body", None)
    if getattr(geo, "ct", None) is not None and kb and _ct_faces_moved(geo):
        k = _transformed_ancestor(geo, kb)
        inside = (f"dentro del {_name(geo, k)}, que tiene giro o desplazamiento" if k else
                  "dentro de un módulo girado o desplazado")
        out.append(f"La caja CT está {inside}: PENGEOM movería las caras de la caja pero no los vóxeles (penhmain "
                   "se detendría con «CTLOCATE: The particle is outside the CT box», IRETRN 2113). El CT tiene que "
                   "estar en su posición final.")
    others = [b.index for b in geo.bodies if b.material == 9999 and b.index != kb]
    modules = [k for k in others if geo.body(k).is_module] if not kb else []
    if modules:  # GEOMIN (pengeom.f) pone LCT=.TRUE. con un MODULE de material 9999, haya o no CTBOX
        names = ", ".join(_name(geo, k) for k in modules[:3])
        out.append(f"{names}: MATERIAL(9999) es la marca de la caja CT y la geometría no tiene ninguna (CTBOX). "
                   "GEOMIN activa igualmente el modo CT (LCT) y penhmain prepararía una caja CT que no existe y "
                   "usaría 9999 como material. Póngale su material de verdad.")
    others = [k for k in others if k not in modules]
    if others:
        names = ", ".join(_name(geo, k) for k in others[:3])
        out.append(f"{names}: MATERIAL(9999) es la marca de la caja CT y no lo es (¿un clon o una copia de la caja "
                   "CT?). penhmain lo trataría como un material que no existe.")
    return out


def new_ct_errors(old: CompiledGeometry | None, new: CompiledGeometry) -> list[str]:
    """Errores de colocación del CT que ``new`` tiene y ``old`` no: para rechazar una edición."""
    before = set(ct_placement_errors(old)) if old is not None else set()
    return [m for m in ct_placement_errors(new) if m not in before]
