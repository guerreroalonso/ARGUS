"""Edición de la fuente de un .in con operaciones de alto nivel.

Cada operación deja el fichero como lo leería penhmain (PMRDR0/PMRDR):

* partícula: ``SKPAR`` y, para una fuente radiactiva, ``SRNUCL`` (que
  además fuerza KPARP = 0, por lo que se borra al elegir otra partícula);
* energía única: ``SENERG`` y se quitan ``SPECTR``/``SLINES`` (juntas
  darían IRETRN 3004/3006);
* posición: ``SPOSIT`` (centro de la caja si la fuente es extensa);
* fuente extensa: ``SBOX``; sin caja se quitan también las líneas
  ``SBODY`` (sin caja darían IRETRN 3049);
* cuerpos activos: una línea ``SBODY`` por cuerpo, con su etiqueta si la
  etiqueta identifica al cuerpo (penhmain toma la última coincidencia de
  BALIAS) y con su número en otro caso;
* emisión: ``SCONE θ φ α`` (lápiz α = 0, cono, isótropa α = 180) o
  ``SRECTA θL θU φL φU``; se borra la otra línea porque, si aparecen las
  dos, penhmain usa SCONE.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from .inputfile import BodyRef, InDocument, SourceDef

DEFAULT_NUCLIDE = "nucleido.nuc"

# Direcciones de los ejes como (θ, φ) en grados.
AXIS_ANGLES: dict[str, tuple[float, float]] = {
    "+X": (90.0, 0.0), "-X": (90.0, 180.0),
    "+Y": (90.0, 90.0), "-Y": (90.0, 270.0),
    "+Z": (0.0, 0.0), "-Z": (180.0, 0.0),
}


def clean(v: float, digits: int = 12) -> float:
    """Quita el ruido de coma flotante (p. ej. 2.9999999999999996 → 3.0)."""
    v = float(v)
    if v == 0.0 or not math.isfinite(v):
        return 0.0 if v == 0.0 else v
    return float(f"{v:.{digits}g}")


def clean_angles(theta: float, phi: float) -> tuple[float, float]:
    """θ en [0, 180] y φ en [0, 360), sin ruido; φ = 0 en los polos."""
    th = min(180.0, max(0.0, clean(theta, 10)))
    ph = clean(phi % 360.0, 10)
    if ph >= 360.0:
        ph = 0.0
    if th in (0.0, 180.0):
        ph = 0.0
    return th, ph


# ---------------------------------------------------------------------------
# Lectura para la interfaz
# ---------------------------------------------------------------------------

def emission_kind(src: SourceDef) -> tuple[str, dict]:
    """Tipo de emisión para mostrarlo: 'pencil', 'cone', 'isotropic' o 'rect'.

    Devuelve también los parámetros: theta, phi, alpha (grados) o
    thl, thu, phl, phu para SRECTA; ``default`` indica que no hay SCONE ni
    SRECTA (haz lápiz hacia +Z por defecto).
    """
    kind, par = src.angular()
    if src.radioactive:
        return "isotropic", {"theta": 0.0, "phi": 0.0, "alpha": 180.0, "locked": True}
    if kind == "rect":
        thl, thu, phl, phu = par
        return "rect", {"thl": thl, "thu": thu, "phl": phl, "phu": phu}
    th, ph, al = par
    info = {"theta": th, "phi": ph, "alpha": al, "default": src.cone is None}
    if al <= 1e-9:
        return "pencil", info
    if al >= 180.0 - 1e-9:
        return "isotropic", info
    return "cone", info


def source_kind(src: SourceDef) -> str:
    """'psf' (ficheros de espacio de fases), 'extended' o 'point'."""
    if src.from_psf:
        return "psf"
    return "extended" if src.extended else "point"


def body_reference(geo, kb: int) -> dict:
    """Referencia para una línea SBODY/DSMAX...: etiqueta si identifica al cuerpo."""
    label = geo.balias(kb)
    if label and label.strip() and "'" not in label and len(label) == 4:
        if BodyRef(label, None, None).resolve(geo) == kb:  # type: ignore[arg-type]
            return {"label": label, "kb": None}
    return {"label": None, "kb": int(kb)}


def active_bodies(doc: InDocument, geo) -> set[int]:
    """Índices PENGEOM de los cuerpos SBODY que se pueden resolver."""
    out = set()
    if geo is None or not geo.ok:
        return out
    for ref in doc.model.source.bodies:
        kb = ref.resolve(geo)
        if kb is not None and 1 <= kb <= geo.nbodys:
            out.add(kb)
    return out


MAX_SOURCE_BODIES = 200  # MKBS de penhmain.f: más líneas SBODY son el error 3009 («Too many bodies in the list»)


def unresolved_refs(doc: InDocument, geo) -> list[dict]:
    """Las referencias SBODY que no llevan a ningún cuerpo de ``geo`` ({'label', 'kb'}): se conservan tal cual al
    cambiar los demás cuerpos activos (pueden ser de otra versión de la geometría)."""
    out = []
    for ref in doc.model.source.bodies:
        kb = ref.resolve(geo) if geo is not None and getattr(geo, "ok", False) else None
        if kb is None or not 1 <= kb <= geo.nbodys:
            out.append({"label": ref.label, "kb": ref.kb})
    return out


def body_description(geo, kb: int) -> str:
    """La descripción del cuerpo en el .geo (lo que va detrás de su etiqueta: «BODY LUNGS»), sin blancos de más."""
    if geo is None or not 1 <= kb <= geo.nbodys:
        return ""
    el = geo.body(kb).element
    return " ".join((getattr(el, "description", "") or "").split()) if el is not None else ""


def body_group(geo, kb: int) -> list[int]:
    """Los cuerpos con la misma descripción y el mismo material que ``kb`` (un órgano partido en varios cuerpos, como
    los pulmones de los maniquíes del ORNL: siete cuerpos «BODY LUNGS»), en orden; solo ``kb`` si no hay más."""
    desc = body_description(geo, kb).casefold()
    if not desc:
        return [kb]
    me = geo.body(kb)
    out = [b.index for b in geo.bodies
           if b.origin != "enclosure" and b.is_module == me.is_module and b.material == me.material
           and body_description(geo, b.index).casefold() == desc]
    return out if kb in out else [kb]


def connected_parts(kbs: Sequence[int], boxes, rel: float = 1e-6) -> list[list[int]]:
    """Los cuerpos ``kbs`` (los de un órgano) en sus partes separadas: los dos pulmones, los dos riñones, cada brazo.

    ``boxes[kb]`` es la caja exacta [lo, hi] de cada cuerpo. Dos cuerpos van en la misma parte si sus cajas se tocan o
    se solapan (con una tolerancia de ``rel`` del tamaño del órgano), y una parte es todo lo que se alcanza así. Dos
    cuerpos que se tocan nunca quedan en partes distintas (sus cajas también se tocan); dos que solo tienen las cajas
    juntas pueden quedar en la misma. Las partes, cada una en orden, ordenadas por su primer cuerpo."""
    kbs = [int(k) for k in kbs]
    if not kbs:
        return []
    lo = [min(float(boxes[k][0][i]) for k in kbs) for i in range(3)]
    hi = [max(float(boxes[k][1][i]) for k in kbs) for i in range(3)]
    span = max(b - a for a, b in zip(lo, hi))
    tol = rel * span if math.isfinite(span) else 0.0
    parent = {k: k for k in kbs}

    def root(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(kbs):
        la, ha = boxes[a]
        for b in kbs[i + 1:]:
            lb, hb = boxes[b]
            if all(float(la[j]) <= float(hb[j]) + tol and float(lb[j]) <= float(ha[j]) + tol for j in range(3)):
                parent[root(a)] = root(b)
    parts: dict[int, list[int]] = {}
    for k in kbs:
        parts.setdefault(root(k), []).append(k)
    return sorted(sorted(p) for p in parts.values())


def fitted_box(lo: Sequence[float], hi: Sequence[float]) -> tuple[list[float], list[float]]:
    """(centro, lados) de una caja SBOX que contiene la caja [lo, hi] (la de unos cuerpos), en números redondos
    (una rejilla de cuatro cifras del tamaño) y con un margen de una o dos marcas de esa rejilla por cada lado (entre
    una diezmilésima y dos milésimas del tamaño): así la fuente llega a todo el cuerpo y penhmain, que sortea puntos
    en la caja hasta que uno cae en un cuerpo activo, apenas pierde intentos."""
    lo = [float(v) for v in lo]
    hi = [float(v) for v in hi]
    span = max(b - a for a, b in zip(lo, hi))
    step = 10.0 ** (math.floor(math.log10(span)) - 3) if span > 0 and math.isfinite(span) else 1e-4
    # En pasos enteros (el redondeo quita el ruido de 4.0/0.001 = 4000.0000000000005, que si no daría una caja
    # torcida): el borde en la marca de la rejilla anterior (o siguiente) y una marca más de margen.
    a = [(math.floor(round(v / step, 6)) - 1) * step for v in lo]
    b = [(math.ceil(round(v / step, 6)) + 1) * step for v in hi]
    return [clean(0.5 * (x + y)) for x, y in zip(a, b)], [clean(y - x) for x, y in zip(a, b)]


def box_contains(src: SourceDef, lo: Sequence[float], hi: Sequence[float]) -> bool:
    """¿La caja de la fuente extensa contiene entera la caja [lo, hi]?"""
    if not src.extended:
        return False
    c = [float(v) for v in src.position]
    h = [0.5 * abs(float(v)) for v in src.box]
    tol = 1e-9 * max(max(abs(v) for v in h), 1e-12)
    return all(c[i] - h[i] <= lo[i] + tol and hi[i] <= c[i] + h[i] + tol for i in range(3))


# ---------------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------------

def set_particle(doc: InDocument, kparp: int, nuclide: str | None = None) -> None:
    kparp = int(kparp)
    if not 0 <= kparp <= 5:
        raise ValueError("KPARP debe estar entre 0 y 5")
    doc.set("SKPAR", [kparp])
    if kparp == 0:
        current = doc.first("SRNUCL")
        name = nuclide or (current.text if current is not None and current.text else DEFAULT_NUCLIDE)
        doc.set("SRNUCL", text=name.strip())
    else:
        doc.remove("SRNUCL")


def set_nuclide(doc: InDocument, name: str) -> None:
    doc.set("SRNUCL", text=name.strip() or DEFAULT_NUCLIDE)


def set_energy(doc: InDocument, energy_ev: float) -> None:
    """Fuente monoenergética (eV); sustituye un espectro si lo había."""
    doc.remove("SPECTR")
    doc.remove("SLINES")
    doc.set("SENERG", [clean(energy_ev)])


def set_position(doc: InDocument, pos: Sequence[float]) -> None:
    doc.set("SPOSIT", [clean(v) for v in pos])


def set_box(doc: InDocument, size: Sequence[float] | None) -> None:
    """Caja de la fuente extensa; ``None`` (o lados nulos) la convierte en puntual."""
    if size is None or max(abs(float(v)) for v in size) <= 1e-16:
        doc.remove("SBODY")
        doc.remove("SBOX")
        return
    doc.set("SBOX", [clean(abs(float(v))) for v in size])


def set_box_around(doc: InDocument, lo: Sequence[float], hi: Sequence[float]) -> None:
    """La caja de la fuente extensa justo alrededor de [lo, hi] (:func:`fitted_box`), centrada en ella (SPOSIT)."""
    center, sides = fitted_box(lo, hi)
    set_position(doc, center)
    set_box(doc, sides)


def merge_refs(kept: list, new: list, order: list[int]) -> list:
    """Las referencias que se quedan (``kept``: (cuerpo o None, referencia), en el orden del .in) y las nuevas
    (``new``: (cuerpo, referencia)), cada nueva delante de la primera que se queda que va después de ella en ``order``
    (el orden de la lista de cuerpos): desmarcar un cuerpo y volver a marcarlo deja las líneas como estaban."""
    pos = {k: i for i, k in enumerate(order)}
    out = list(kept)
    for k, ref in new:
        j = next((j for j, (kk, _r) in enumerate(out) if kk is not None and pos.get(kk, -1) > pos.get(k, -1)),
                 len(out))
        out.insert(j, (k, ref))
    return [ref for _k, ref in out]


def set_active_bodies(doc: InDocument, geo, kbs: Iterable[int], extra_refs: Iterable[dict] = ()) -> None:
    """Cuerpos activos (SBODY) de la fuente extensa, en el orden dado.

    ``extra_refs``: referencias que se conservan tal cual ({'label': ...} o
    {'kb': ...}), p. ej. etiquetas que no están en la geometría abierta.
    """
    kbs = list(dict.fromkeys(int(k) for k in kbs))
    extra = [dict(label=r.get("label"), kb=r.get("kb")) for r in extra_refs]
    if geo is not None and getattr(geo, "ok", False):
        # Lo que ya estaba escrito se queda como está (por número o por etiqueta, y en su orden): solo cambian las
        # líneas de los cuerpos que se añaden o se quitan (revisión de 0.16.2: se reescribían todas por etiqueta).
        kept: list[tuple[int | None, dict]] = []
        wanted, done = set(kbs), set()
        for r in doc.model.source.bodies:
            e = {"label": r.label, "kb": r.kb}
            if e in extra:  # una que se conserva tal cual (p. ej. SBODY 0, la envolvente), en su sitio
                kept.append((None, e))
                extra.remove(e)
                continue
            k = r.resolve(geo)
            if k is not None and 1 <= k <= geo.nbodys and k in wanted and k not in done:
                kept.append((k, e))
                done.add(k)
        kept += [(None, e) for e in extra]
        entries = merge_refs(kept, [(kb, body_reference(geo, kb)) for kb in kbs if kb not in done], kbs)
    else:
        entries = extra + [{"label": None, "kb": kb} for kb in kbs]
    if not entries:
        doc.remove("SBODY")
        return
    doc.set_repeated("SBODY", entries)


def set_emission(doc: InDocument, kind: str, theta: float = 0.0, phi: float = 0.0, alpha: float = 0.0,
                 rect: Sequence[float] | None = None) -> None:
    """Distribución angular de las partículas primarias.

    ``kind``: 'pencil' (SCONE θ φ 0), 'cone' (SCONE θ φ α), 'isotropic'
    (SCONE θ φ 180) o 'rect' (SRECTA θL θU φL φU).
    """
    if kind == "rect":
        if rect is None:
            raise ValueError("SRECTA necesita θL, θU, φL y φU")
        doc.remove("SCONE")
        doc.set("SRECTA", [clean(v, 10) for v in rect])
        return
    th, ph = clean_angles(theta, phi)
    if kind == "pencil":
        al = 0.0
    elif kind == "isotropic":
        al = 180.0
    elif kind == "cone":
        al = min(180.0, max(0.0, clean(alpha, 10)))
    else:
        raise ValueError(f"tipo de emisión desconocido: {kind}")
    doc.remove("SRECTA")
    doc.set("SCONE", [th, ph, al])


def set_direction(doc: InDocument, theta: float, phi: float) -> None:
    """Cambia el eje de la emisión conservando su tipo (y la apertura)."""
    src = doc.model.source
    kind, info = emission_kind(src)
    if kind == "rect":
        # Desplaza la ventana para que su centro apunte a (θ, φ).
        th, ph = clean_angles(theta, phi)
        lo, hi = _shift_window(th, abs(info["thu"] - info["thl"]), 180.0)
        plo, phu = _shift_window(ph, abs(info["phu"] - info["phl"]), 360.0)
        set_emission(doc, "rect", rect=[lo, hi, plo, phu])
        return
    set_emission(doc, kind, theta, phi, info.get("alpha", 0.0))


def _shift_window(center: float, width: float, top: float) -> tuple[float, float]:
    """Intervalo de anchura ``width`` centrado en ``center`` y dentro de [0, top].

    SRECTA no admite ventanas que crucen 0 o ``top`` (IRETRN 3047/3048), así
    que en los bordes la ventana se desplaza hacia dentro.
    """
    width = min(width, top)
    lo = center - 0.5 * width
    lo = min(max(lo, 0.0), top - width)
    return clean(lo, 10), clean(lo + width, 10)


def aim_at(doc: InDocument, target: Sequence[float]) -> tuple[float, float] | None:
    """Orienta el eje de emisión desde la posición de la fuente hacia ``target``."""
    src = doc.model.source
    d = [float(t) - float(p) for t, p in zip(target, src.position)]
    n = math.sqrt(sum(c * c for c in d))
    if n < 1e-12:
        return None
    th = math.degrees(math.acos(max(-1.0, min(1.0, d[2] / n))))
    ph = math.degrees(math.atan2(d[1], d[0])) % 360.0
    th, ph = clean_angles(round(th, 6), round(ph, 6))
    set_direction(doc, th, ph)
    return th, ph
