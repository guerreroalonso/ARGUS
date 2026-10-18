"""Colocar módulos y clones: moverlos y girarlos como un todo (punto 4 del editor).

Un módulo o un clon (``CLONE``) se coloca con su transformación (giro de
Euler y desplazamiento, tras la línea de unos), que está en el sistema de su
módulo madre. Un movimiento global G (girar alrededor de un eje que pasa por
un punto, desplazar...) se escribe como la transformación nueva

    T' = F⁻¹ · G · F · T

donde F es el sistema de la madre (``edit.local_frame``) y T la
transformación actual. En un clon, T es la que se aplica a las superficies
del módulo original en el momento de clonarlo; la fórmula es la misma
(comprobado con PENGEOM real).

Las superficies ``SURFACE*`` no se mueven con los módulos: un módulo que
las usa no se puede mover ni girar como un todo.

PENGEOM mueve con un módulo todas las superficies de lo que tiene dentro: si
un cuerpo de fuera usa alguna (una lámina usa las de su contenedor, dos
módulos pegados comparten un plano...), también cambiaría. Antes de mover, el
módulo recibe copias propias de esas superficies (``separate_surfaces``;
mientras no se mueve, GEOMIN funde las copias con las originales y nada
cambia). Los clones que se leen después del módulo copian sus superficies ya
movidas: se mueven con él (``moving_copies``) y también se comprueba dónde
quedan.
"""

from __future__ import annotations

import math

import numpy as np

from .compiler import CompiledGeometry, body_title
from .edit import (
    EditError,
    PlacementCheck,
    _clean,
    _lab,
    _set_params,
    body_users,
    clone_users,
    contains_ct,
    editable_body,
    euler_angles,
    hosts,
    label_key,
    local_frame,
    module_transform,
    new_label,
    set_description,
)
from .extent import element_box, locator_for, scene_box, solid_bodies
from .model import (
    BodyElement,
    CloneElement,
    Element,
    ElementKind,
    GeoDocument,
    IncludeElement,
    ModuleElement,
    ParamLine,
    SurfaceElement,
)

Frame = tuple[np.ndarray, np.ndarray]  # x_padre = R·x + d
TRANSFORM_ORDER = ("OMEGA", "THETA", "PHI", "X-SHIFT", "Y-SHIFT", "Z-SHIFT")


# ---------------------------------------------------------------------------
# Sistemas de referencia
# ---------------------------------------------------------------------------

def identity() -> Frame:
    return np.eye(3), np.zeros(3)


def compose(a: Frame, b: Frame) -> Frame:
    """a ∘ b (primero b, después a)."""
    return a[0] @ b[0], a[0] @ b[1] + a[1]


def inverse(a: Frame) -> Frame:
    return a[0].T, -(a[0].T @ a[1])


def rotation(axis, angle_deg: float) -> np.ndarray:
    """Matriz de giro de ``angle_deg`` grados alrededor de ``axis`` (regla de la mano derecha)."""
    k = np.asarray(axis, dtype=float)
    k = k / np.linalg.norm(k)
    a = math.radians(float(angle_deg))
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    r = np.eye(3) + math.sin(a) * kx + (1.0 - math.cos(a)) * (kx @ kx)
    # Múltiplos de 90°: exactos (sin ruido de sin/cos).
    if abs(round(float(angle_deg) / 90.0) * 90.0 - float(angle_deg)) < 1e-12:
        r = np.round(r, 15) + 0.0
        r[np.abs(r) < 1e-15] = 0.0
    return r


def rotation_about(axis, angle_deg: float, center) -> Frame:
    """Giro alrededor del eje ``axis`` que pasa por ``center`` (coordenadas globales)."""
    r = rotation(axis, angle_deg)
    c = np.asarray(center, dtype=float)
    return r, c - r @ c


def translation(delta) -> Frame:
    return np.eye(3), np.asarray(delta, dtype=float)


# ---------------------------------------------------------------------------
# Elementos que se pueden colocar: módulos y clones
# ---------------------------------------------------------------------------

def placeable(geo: CompiledGeometry, kb: int) -> Element:
    """El MODULE o el CLONE del fichero principal que coloca a ``kb``; si no, EditError."""
    if not 1 <= kb <= geo.nbodys:
        raise EditError("No hay ningún elemento seleccionado.")
    b = geo.body(kb)
    if b.origin == "clone" and isinstance(b.element, CloneElement) and b.element is b.clone_element:
        if b.document is not geo.document:
            raise EditError("Está definido en un fichero INCLUDE: ábralo para editarlo.")
        return b.element
    el = editable_body(geo, kb)
    if not isinstance(el, ModuleElement):
        raise EditError("Solo se pueden mover y girar módulos y clones (un cuerpo se puede convertir antes en "
                        "módulo).")
    if contains_ct(geo, kb):
        raise EditError("Contiene la caja CT: no se puede mover, girar ni clonar (PENGEOM movería las caras de la "
                        "caja pero no los vóxeles, y solo admite un CT).")
    return el


def element_transform(el: Element, parinp=None) -> Frame:
    """Transformación actual (R, d) de un módulo o de un clon."""
    if isinstance(el, ModuleElement):
        return module_transform(el, parinp)
    if isinstance(el, CloneElement):
        tmp = ModuleElement(el.kind, has_ones_line=el.has_ones_line, transform=el.transform)
        return module_transform(tmp, parinp)
    raise EditError("Solo los módulos y los clones tienen giro y desplazamiento.")


def mother_frame(geo: CompiledGeometry, kb: int, parinp=None) -> Frame:
    """Sistema (global ← local) de la madre de ``kb`` (identidad en la envolvente)."""
    m = geo.body(kb).mother
    if not m or (geo.enclosure_added and m == geo.nbodys):
        return identity()
    return local_frame(geo, m, parinp)


def starred_surfaces(geo: CompiledGeometry, kb: int) -> list[int]:
    """Superficies SURFACE* (que no se mueven con los módulos) que usa el elemento o lo que tiene dentro."""
    out = []
    for k in solid_bodies(geo, kb):
        for ks, fl in zip(geo.body(k).surfaces, geo.body(k).flags):
            if fl < 3 and geo.surface(ks).starred and ks not in out:
                out.append(ks)
    return out


def set_transform(el: Element, values: dict[str, float]) -> None:
    """Giro (grados) y desplazamiento de un módulo o de un clon; los que valen 0 se quitan."""
    if not isinstance(el, ModuleElement | CloneElement):
        raise EditError("Solo los módulos y los clones tienen giro y desplazamiento.")
    params = _set_params(el.transform, values, TRANSFORM_ORDER)
    el.transform = [p for p in params if p.value != 0.0 or p.ipar]
    el.has_ones_line = bool(el.transform) or (isinstance(el, ModuleElement) and el.has_ones_line
                                              and bool(el.transform))
    el.modified = True


def transform_values(t: Frame) -> dict[str, float]:
    """OMEGA, THETA, PHI (grados) y desplazamientos de una transformación, sin ruido de redondeo."""
    om, th, ph = euler_angles(t[0])
    out = {"OMEGA": om, "THETA": th, "PHI": ph,
           "X-SHIFT": float(t[1][0]), "Y-SHIFT": float(t[1][1]), "Z-SHIFT": float(t[1][2])}
    return {k: _clean(v, 10 if k in ("OMEGA", "THETA", "PHI") else 12) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Lo que se mueve con un elemento: sus descendientes, sus superficies y sus clones
# ---------------------------------------------------------------------------

def descendants(geo: CompiledGeometry, kb: int) -> set[int]:
    """Cuerpos que PENGEOM mueve con la transformación de ``kb``: él y los que lo tienen en su cadena de madres."""
    reach: dict[int, bool] = {kb: True}
    for b in geo.bodies:
        chain = []
        k = b.index
        while 0 < k <= geo.nbodys and k not in reach and len(chain) <= geo.nbodys:
            chain.append(k)
            k = geo.body(k).mother
        r = reach.get(k, False)
        for c in chain:
            reach[c] = r
    return {k for k, r in reach.items() if r}


def _surface_index(geo: CompiledGeometry) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in geo.surfaces:
        if s.origin == "user":
            out.setdefault(s.alias, s.index)
    return out


def _named_surfaces(geo: CompiledGeometry, kb: int, index: dict[str, int]) -> list[int]:
    """Superficies propias de ``kb`` tal como las nombra su elemento (antes de que GEOMIN funda las repetidas)."""
    b = geo.body(kb)
    if b.origin != "user" or not isinstance(b.element, BodyElement | ModuleElement):
        return []
    sfx = b.alias[4:]
    return [index[r.label + sfx] for r in b.element.items if r.is_surface and r.label + sfx in index]


def _limiting_closure(geo: CompiledGeometry, kb: int) -> list[int]:
    """Limitantes de un cuerpo y los de estos (GEOMIN copia en su lista todas sus superficies)."""
    out: list[int] = []
    stack = list(geo.body(kb).limiting)
    while stack:
        k = stack.pop()
        if 1 <= k <= geo.nbodys and k not in out:
            out.append(k)
            if not geo.body(k).is_module:
                stack.extend(geo.body(k).limiting)
    return out


def shared_surfaces(geo: CompiledGeometry, kb: int) -> dict[int, list[int]]:
    """Superficies que se moverían con ``kb`` y que también limitan a cuerpos de fuera: {KS: [cuerpos de fuera]}.

    PENGEOM mueve con un módulo (o un clon) las superficies de la lista de
    cada cuerpo que tiene dentro; si un cuerpo de fuera usa una de ellas,
    también cambia de forma. Se comparan tal como las nombra el fichero (dos
    superficies iguales que GEOMIN funde no están compartidas).
    """
    desc = descendants(geo, kb)
    index = _surface_index(geo)
    moved: set[int] = set()
    for k in desc:
        moved.update(_named_surfaces(geo, k, index))
        for lb in _limiting_closure(geo, k):
            if lb not in desc:  # un limitante de fuera: sus superficies van en la lista del de dentro
                moved.update(_named_surfaces(geo, lb, index))
    out: dict[int, list[int]] = {}
    for b in geo.bodies:
        if b.index in desc:
            continue
        for ks in _named_surfaces(geo, b.index, index):
            if ks in moved and not geo.surface(ks).starred and b.index not in out.get(ks, []):
                out.setdefault(ks, []).append(b.index)
    return out


def _shared_text(geo: CompiledGeometry, shared: dict[int, list[int]]) -> tuple[str, str]:
    """(«1», «5»; Módulo «3», ...) para los mensajes."""
    labels = ", ".join(f"«{geo.surface(ks).alias[:4].strip()}»" for ks in sorted(shared)[:5])
    others = sorted({k for users in shared.values() for k in users})
    names = ", ".join(body_title(geo, k) for k in others[:3]) + (" y otros" if len(others) > 3 else "")
    return labels, names


def _index_of(doc: GeoDocument, el) -> int | None:
    for i, e in enumerate(doc.elements):
        if e is el:
            return i
    return None


def _copy_param(p: ParamLine) -> ParamLine:
    return ParamLine(p.name, p.value, p.ipar, p.unit)


def copy_surface(s: SurfaceElement, label: str) -> SurfaceElement:
    """Superficie nueva con la misma definición (y los mismos PARINP) que ``s``."""
    return SurfaceElement(ElementKind.SURFACE, label=label, description=s.description, starred=s.starred,
                          indices=tuple(s.indices), params=[_copy_param(p) for p in s.params],
                          has_ones_line=s.has_ones_line, transform=[_copy_param(p) for p in s.transform],
                          modified=True)


def separate_surfaces(doc: GeoDocument, geo: CompiledGeometry, kb: int) -> list[str]:
    """Da a ``kb`` (y a lo que tiene dentro) copias propias de las superficies que comparte con cuerpos de fuera.

    Así, al moverlo o girarlo, PENGEOM no mueve también las de los demás. Las
    copias tienen la misma definición (y los mismos PARINP) y van justo
    detrás de la original: mientras no se mueva, GEOMIN las funde con ella y
    la geometría (tablas y LOCATE) no cambia. Devuelve avisos para el usuario.
    """
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    shared = shared_surfaces(geo, kb)
    if not shared:
        return []
    desc = descendants(geo, kb)
    name = body_title(geo, kb)
    for k in sorted(desc):
        for lb in geo.body(k).limiting:
            if lb not in desc:
                raise EditError(f"{body_title(geo, k)} está limitado por {body_title(geo, lb)}, que no está dentro de "
                                f"{name}: al moverlo, PENGEOM también movería a {body_title(geo, lb)}. Declárelo hija "
                                "del mismo módulo (o edite el fichero).")
    inside = [geo.body(k).element for k in sorted(desc) if geo.body(k).origin == "user"
              and isinstance(geo.body(k).element, BodyElement | ModuleElement)]
    taken: set[str] = set()
    done = []
    for ks in sorted(shared):
        s = geo.surface(ks)
        el = s.element
        pos = _index_of(doc, el) if isinstance(el, SurfaceElement) and s.document is doc else None
        label = _lab(el.label) if el is not None else s.alias[:4].strip()
        key = label_key(el.label if el is not None else s.alias[:4])  # (con sus cuatro columnas, como PENGEOM)
        users = [e for e in inside if any(r.is_surface and label_key(r.label) == key for r in e.items)]
        if pos is None or any(_index_of(doc, e) is None for e in users):
            raise EditError(f"{name} comparte la superficie «{label}» con otros cuerpos y está en un fichero INCLUDE: "
                            "no se puede mover sin cambiarlos a ellos (edite el fichero).")
        new = new_label(doc, "surface", geo, taken)
        taken.add(new.strip())
        copy = copy_surface(el, new)
        text = " ".join(el.description.split())
        set_description(copy, f"copia de «{label}»" + (f": {text}" if text else ""))
        doc.elements.insert(pos + 1, copy)
        for e in users:
            for r in e.items:
                if r.is_surface and label_key(r.label) == key:
                    r.label = new
            e.modified = True
        done.append(f"«{new.strip()}» (copia de «{label}»)")
    _labels, names = _shared_text(geo, shared)
    verb = "sigue" if len({k for users in shared.values() for k in users}) == 1 else "siguen"
    return [f"{name} tiene ahora superficies propias: {', '.join(done)}; {names} {verb} con las de antes."]


def _read_position(doc: GeoDocument, el) -> int | None:
    """Posición en la que GEOMIN lee ``el``: la suya en el fichero principal o la del INCLUDE que lo trae."""
    pos = _index_of(doc, el)
    if pos is not None:
        return pos
    for i, e in enumerate(doc.elements):
        if isinstance(e, IncludeElement) and e.document is not None and any(x is el for x in e.document.elements):
            return i
    return None


def frame_after(doc: GeoDocument, geo: CompiledGeometry, kb: int, index: int, parinp=None) -> Frame:
    """Transformaciones de los módulos que contienen a ``kb`` y se leen después de la posición ``index``."""
    out = identity()
    m = geo.body(kb).mother
    seen = 0
    while m and not (geo.enclosure_added and m == geo.nbodys) and seen <= geo.nbodys:
        b = geo.body(m)
        el = b.element
        pos = _index_of(doc, el) if isinstance(el, ModuleElement) else None
        if pos is not None and pos >= index:
            out = compose(element_transform(el, parinp), out)
        m = b.mother
        seen += 1
    return out


def clone_map(geo: CompiledGeometry, clone_el: CloneElement, parinp=None) -> Frame:
    """Lo que lleva cada punto del módulo original de un CLONE a su copia (x_copia = H·x_original, globales).

    H = B_K·T_K·B⁻¹, con B_K el sistema de la madre del clon, T_K su
    transformación y B la de los módulos del original que se leen después
    del clon (las de antes ya estaban aplicadas al clonarlo).
    """
    top = next((b for b in geo.bodies if b.origin == "clone" and b.element is clone_el
                and b.clone_element is clone_el), None)
    if top is None or not top.clone_of:
        return identity()
    doc = geo.document
    pos = _read_position(doc, clone_el)
    after = frame_after(doc, geo, top.clone_of, pos if pos is not None else len(doc.elements), parinp)
    return compose(mother_frame(geo, top.index, parinp), compose(element_transform(clone_el, parinp), inverse(after)))


def moving_copies(geo: CompiledGeometry, kb: int, parinp=None) -> list[tuple[int, Frame]]:
    """Copias de clones que se mueven con ``kb``: (cuerpo de arriba de cada grupo, H) con x_copia = H·x_original.

    Un CLONE copia las superficies del original tal como están al leerlo: si
    se lee después de ``kb``, lo que copia de dentro de ``kb`` se mueve con él
    (con el movimiento conjugado H·G·H⁻¹); y los clones de esos clones, igual.
    """
    doc = geo.document
    desc = descendants(geo, kb)
    pos = _read_position(doc, geo.body(kb).element)
    frame: dict[int, Frame] = {k: identity() for k in desc}
    group: dict[int, tuple] = {k: () for k in desc}
    maps: dict[tuple, Frame] = {}
    members: dict[tuple, list[int]] = {}
    for b in geo.bodies:  # por orden: el original va siempre antes que sus copias
        src = b.clone_of
        if b.origin != "clone" or b.index in desc or src not in frame or b.clone_element is None:
            continue
        k_el = b.clone_element
        if src in desc:
            kpos = _read_position(doc, k_el)
            if pos is not None and kpos is not None and kpos < pos:
                continue  # clonado antes de leer lo que se mueve: su copia no cambia
        key = (id(k_el),) + group[src]
        if key not in maps:
            maps[key] = compose(clone_map(geo, k_el, parinp), frame[src])
        frame[b.index] = maps[key]
        group[b.index] = key
        members.setdefault(key, []).append(b.index)
    out = []
    for key, mem in members.items():
        ms = set(mem)
        out.extend((k, maps[key]) for k in mem if geo.body(k).mother not in ms)
    return out


def around(geo: CompiledGeometry, kb: int, closure=None) -> set[int]:
    """Lo que aloja a ``kb``: su madre y los cuerpos que lo excluyen, también a través de otro limitante (un
    limitante que asoma de su cuerpo está en la lista del que contiene a ese cuerpo). ``closure``: el de
    ``overlap._closure`` (si ya se tiene)."""
    if closure is None:
        from .overlap import _closure

        closure = _closure(geo)
    out = set(hosts(geo, kb))
    out.update(b.index for b in geo.bodies if b.index != kb and kb in closure[b.index])
    return out


def _copies_of(geo: CompiledGeometry, kbs: set[int]) -> set[int]:
    """Copias (de clones, a cualquier profundidad) de los cuerpos ``kbs``."""
    cur = set(kbs)
    out: set[int] = set()
    for b in geo.bodies:
        if b.origin == "clone" and b.clone_of in cur:
            cur.add(b.index)
            out.add(b.index)
    return out


def move_element(doc: GeoDocument, geo: CompiledGeometry, kb: int, motion: Frame, parinp=None,
                 bounds=None) -> dict[str, float]:
    """Aplica el movimiento global ``motion`` (R, d) a un módulo o clon (con todo lo que contiene).

    Devuelve los valores escritos. Si el giro no cambia, los ángulos no se
    tocan (se conservan sus PARINP); un valor fijado por PARINP que tenga que
    cambiar da ``EditError``. Si comparte superficies con cuerpos de fuera,
    antes recibe copias propias (``separate_surfaces``). Después se comprueba
    que solo cambian lo movido, sus clones y lo que lo aloja (si no,
    ``EditError``); ``bounds`` es la caja de la escena (si no, se estima).
    """
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    el = placeable(geo, kb)
    changed = _transform_change(geo, kb, motion, parinp)
    if changed:
        separate_surfaces(doc, geo, kb)
        set_transform(el, changed)
        _verify_motion(geo, doc, kb, parinp, bounds)
    return changed


def set_placement(doc: GeoDocument, geo: CompiledGeometry, kb: int, values: dict[str, float]) -> list[str]:
    """Valores de la colocación de un módulo o clon (panel «Colocación»), con superficies propias si compartía alguna.

    Devuelve los avisos de ``separate_surfaces``.
    """
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    el = placeable(geo, kb)
    notes = separate_surfaces(doc, geo, kb)
    set_transform(el, values)
    return notes


def moved_values(geo: CompiledGeometry, kb: int, motion: Frame, parinp=None, separate: bool = False
                 ) -> dict[str, float]:
    """Valores de la transformación que cambian al mover ``kb`` con ``motion`` (sin tocar el documento).

    Es lo que usa el arrastre en vivo, que solo cambia la transformación: si
    ``kb`` comparte superficies con cuerpos de fuera, ``EditError``, salvo con
    ``separate`` (quien aplica los valores le da antes superficies propias con
    ``separate_surfaces``: los valores son los mismos).
    """
    placeable(geo, kb)
    shared = {} if separate else shared_surfaces(geo, kb)
    if shared:
        labels, names = _shared_text(geo, shared)
        raise EditError(f"{body_title(geo, kb)} comparte superficies ({labels}) con {names}: al moverlo, PENGEOM las "
                        "movería también para ellos. Arrastrándolo no se puede; «Girar» y «Pegar a…» le dan antes "
                        "superficies propias.")
    return _transform_change(geo, kb, motion, parinp)


def _transform_change(geo: CompiledGeometry, kb: int, motion: Frame, parinp=None) -> dict[str, float]:
    el = placeable(geo, kb)
    stars = starred_surfaces(geo, kb)
    if stars:
        raise EditError(f"Usa superficies SURFACE* (n.º {', '.join(map(str, stars[:5]))}), que PENGEOM no mueve "
                        "con los módulos: no se puede mover ni girar como un todo.")
    rot, shift = (np.asarray(v, dtype=float) for v in motion)
    if not (np.all(np.isfinite(rot)) and np.all(np.isfinite(shift))):
        raise EditError("Movimiento no válido.")
    f = mother_frame(geo, kb, parinp)
    t = element_transform(el, parinp)
    new = compose(inverse(f), compose((rot, shift), compose(f, t)))
    vals = transform_values(new)
    old = transform_values(t)
    changed = {}
    same_rot = np.allclose(new[0], t[0], atol=1e-13)
    for name in TRANSFORM_ORDER:
        if name in ("OMEGA", "THETA", "PHI") and same_rot:
            continue
        if abs(vals[name] - old[name]) > 1e-12 * max(1.0, abs(old[name])) or \
                (name in ("OMEGA", "THETA", "PHI") and not same_rot):
            changed[name] = vals[name]
    bound = {p.name: p.ipar for p in el.transform if p.ipar > 0} if el.has_ones_line else {}
    for name in changed:
        if name in bound:
            raise EditError(f"{name} lo fija el .in (PARINP {bound[name]}): cámbielo allí.")
    return changed


def element_center(extent) -> np.ndarray:
    """Centro de la caja de un elemento, sin el ruido de los rayos (se redondea a 1e-8 de su tamaño).

    Las caras de un módulo girado se encuentran con rayos, con errores de
    ~1e-10 del tamaño: sin redondear, el centro de una caja centrada en 700
    saldría 699.99999995 y cada giro alrededor de él añadiría ese ruido.
    """
    lo, hi = (np.asarray(v, dtype=float) for v in extent)
    size = float(np.max(hi - lo))
    c = 0.5 * (lo + hi)
    if not np.isfinite(size) or size <= 0:
        return np.array([_clean(float(v)) for v in c])
    unit = 10.0 ** (math.floor(math.log10(size)) - 8)
    return np.array([_clean(round(float(v) / unit) * unit) for v in c])


def box_image(motion: Frame, lo, hi) -> tuple[np.ndarray, np.ndarray]:
    """Caja que contiene la imagen de la caja [lo, hi] por ``motion``."""
    lo, hi = (np.asarray(v, dtype=float) for v in (lo, hi))
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    moved = corners @ np.asarray(motion[0], dtype=float).T + np.asarray(motion[1], dtype=float)
    return moved.min(axis=0), moved.max(axis=0)


def check_motion(geo: CompiledGeometry, locator, kb: int, motion: Frame, extent, n: int = 20000,
                 seed: int = 5) -> PlacementCheck:
    """¿Se puede mover (o girar) el elemento ``kb`` sin que se solape con nada?

    Puntos del volumen del elemento, ya movidos, tienen que caer en el propio
    elemento (en su sitio de antes) o en lo que lo aloja (el cuerpo que lo
    excluye o su módulo madre). Lo mismo para las copias de los clones que se
    mueven con él (``moving_copies``), cada una con su movimiento.

    El único elemento del nivel superior (sin la envolvente que GEOMIN añade
    cuando hay varios) es toda la geometría: fuera de él no hay nada con lo
    que solaparse, así que se puede llevar a cualquier sitio.
    """
    from .edit import logical_parent

    rot, shift = (np.asarray(v, dtype=float) for v in motion)
    if not (np.all(np.isfinite(rot)) and np.all(np.isfinite(shift))):
        return PlacementCheck(False, "Movimiento no válido.", 1.0)
    lo, hi = (np.asarray(v, dtype=float) for v in extent)
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))) or np.any(hi < lo):
        return PlacementCheck(False, "No se ha podido muestrear el volumen del elemento.", 1.0)
    rng = np.random.default_rng(seed)
    groups = [(kb, (rot, shift), (lo, hi), None)]
    for top, h in moving_copies(geo, kb):
        groups.append((top, compose(h, compose((rot, shift), inverse(h))), box_image(h, lo, hi), top))
    passed = []
    for k, (r, d), (glo, ghi), clone in groups:
        solid = solid_bodies(geo, k)
        pts = rng.uniform(glo, ghi, (n if clone is None else max(2000, n // 2), 3))
        ib, _ = locator.locate(pts)
        mine = np.isin(ib, list(solid))
        if not mine.any():
            if clone is None:
                return PlacementCheck(False, "No se ha podido muestrear el volumen del elemento.", 1.0)
            continue
        moved = pts[mine] @ r.T + d
        ib2, _ = locator.locate(moved)
        allowed = solid | around(geo, k)
        if not geo.enclosure_added and logical_parent(geo, k) == 0:
            allowed.add(geo.nbodys + 1)  # la raíz: lo de fuera está vacío (no es de nadie)
        bad = ~np.isin(ib2, list(allowed))
        if bad.any():
            frac = float(bad.mean())
            offenders: dict[int, float] = {}
            for b in ib2[bad]:
                offenders[int(b)] = offenders.get(int(b), 0.0) + 1.0 / len(moved)
            worst = sorted(offenders.items(), key=lambda kv: -kv[1])[:3]
            names = ", ".join(f"{body_title(geo, b) if b <= geo.nbodys else 'fuera de la geometría'} "
                              f"({100 * f:.1f} %)" for b, f in worst)
            who = "Se solaparía" if clone is None else \
                f"Su clon {body_title(geo, clone)} se movería con él y se solaparía"
            return PlacementCheck(False, f"{who}: {100 * frac:.1f} % de su volumen caería en {names}.", frac,
                                  offenders)
        passed.append((solid, allowed, r, d, box_image((r, d), glo, ghi)))
    # Lo que el muestreo no ve: láminas finas en el sitio nuevo (con rayos).
    from .edit import thin_message
    from .raycheck import intruders

    for solid, allowed, r, d, (mlo, mhi) in passed:
        sol = list(solid)
        found = intruders(geo, mlo, mhi, lambda p, r=r, d=d, sol=sol: np.isin(locator.locate((p - d) @ r)[0], sol),
                          allowed)
        msg = thin_message(geo, found)
        if msg is not None:
            return msg
    return PlacementCheck(True, "No se solapa con nada.")


# ---------------------------------------------------------------------------
# Comprobación después de mover: solo cambia lo movido (y sus clones) y lo que lo aloja
# ---------------------------------------------------------------------------

def _halfspaces(geo: CompiledGeometry, kb: int) -> np.ndarray:
    """Superficies propias de ``kb`` como semiespacios normalizados (lado·F, dividido por su coeficiente mayor)."""
    rows = []
    for ks, side in geo.body(kb).own_surfaces():
        q = side * np.asarray(geo.surface(ks).q, dtype=float)
        m = float(np.max(np.abs(q)))
        rows.append(q / m if m > 0 else q)
    return np.array(rows).reshape(-1, 10)


def same_shape(a: CompiledGeometry, ka: int, b: CompiledGeometry, kb: int, tol: float = 1e-10) -> bool:
    """¿Tienen ``ka`` (en ``a``) y ``kb`` (en ``b``) las mismas superficies propias con los mismos lados?

    Se comparan como semiespacios (así da igual el número de cada superficie
    y que GEOMIN haya fundido dos iguales).
    """
    ha, hb = _halfspaces(a, ka), _halfspaces(b, kb)
    if len(ha) != len(hb):
        return False
    used = np.zeros(len(hb), dtype=bool)
    for row in ha:
        d = np.max(np.abs(hb - row), axis=1) if len(hb) else np.zeros(0)
        cand = np.nonzero((d <= tol) & ~used)[0]
        if not cand.size:
            return False
        used[cand[0]] = True
    return True


def motion_problems(old: CompiledGeometry, new: CompiledGeometry, kb: int, bounds=None, n: int = 12000,
                    seed: int = 29) -> str | None:
    """Lo que cambia en ``new`` fuera de ``kb`` (movido), sus clones y lo que lo aloja; None si nada.

    1. Exacto: los cuerpos de fuera conservan sus superficies propias (si un
       módulo compartía una, PENGEOM la habría movido también para ellos).
    2. Con LOCATE, en las cajas de lo movido (antes y después): los puntos
       que no están en lo movido ni en lo que lo aloja conservan su cuerpo y
       su material.
    """
    from .identity import body_keys
    from .locate import Locator

    k_old, k_new = body_keys(old), body_keys(new)
    new_of: dict = {}
    for i, key in enumerate(k_new, start=1):
        new_of.setdefault(key, i)
    old_of: dict = {}
    for i, key in enumerate(k_old, start=1):
        old_of.setdefault(key, i)
    moving = descendants(old, kb)
    moving |= _copies_of(old, moving)
    changed = []
    for b in old.bodies:
        j = new_of.get(k_old[b.index - 1])
        if b.index in moving or j is None or b.origin == "enclosure":
            continue
        if not same_shape(old, b.index, new, j):
            changed.append(b.index)
    if changed:
        names = ", ".join(body_title(old, k) for k in changed[:3]) + (" y otros" if len(changed) > 3 else "")
        return (f"Al moverlo cambiaría también la forma de {names} (comparten superficies con lo que se mueve): "
                "PENGEOM las movería con él.")
    from .overlap import _closure

    closure = _closure(old)
    allowed = set(moving)
    for k in moving:
        allowed.update(around(old, k, closure))
    allowed.add(old.nbodys + 1)
    if old.enclosure_added:
        allowed.add(old.nbodys)
    scene = bounds if bounds is not None else scene_box(old)
    s_lo, s_hi = (np.asarray(v, dtype=float) for v in scene)
    boxes = []
    tops = [k for k in sorted(moving) if old.body(k).mother not in moving]
    for k in tops:
        for geo, idx in ((old, k), (new, new_of.get(k_old[k - 1]))):
            if idx is None:
                continue
            lo, hi = element_box(geo, idx)
            lo = np.where(np.isfinite(lo), np.maximum(lo, s_lo), s_lo)
            hi = np.where(np.isfinite(hi), np.minimum(hi, s_hi), s_hi)
            if np.all(hi >= lo):
                pad = 0.05 * (hi - lo) + 1e-9 * max(1.0, float(np.max(np.abs(np.r_[lo, hi]))))
                boxes.append((lo - pad, hi + pad))
    if not boxes:
        return None
    rng = np.random.default_rng(seed)
    per = max(200, n // len(boxes))
    pts = np.vstack([rng.uniform(lo, hi, (per, 3)) for lo, hi in boxes])
    ib0, m0 = locator_for(old).locate(pts)
    ib1, m1 = Locator(new).locate(pts)

    def old_index(j: int) -> int:
        return old_of.get(k_new[j - 1], -1) if 1 <= j <= new.nbodys else old.nbodys + 1

    bad: dict[int, int] = {}
    for a, b, ma, mb in zip(ib0, ib1, m0, m1):
        a, b = int(a), int(b)
        ob = old_index(b)
        if (ob == a or (a > old.nbodys and b > new.nbodys)) and ma == mb:
            continue
        if a in allowed and ob in allowed:
            continue
        bad[a] = bad.get(a, 0) + 1
    if not bad:
        return None
    worst = sorted(bad.items(), key=lambda kv: -kv[1])[:3]
    names = ", ".join(body_title(old, k) if k <= old.nbodys else "fuera de la geometría" for k, _c in worst)
    return f"Al moverlo cambiarían también otros cuerpos: {names}."


def motion_guard(label: str, bounds=None):
    """Comprobación ``check(antes, después)`` de mover o girar el elemento ``label`` (``motion_problems``).

    Para ``GeoController.commit_live`` (arrastre) y ``edit`` (atributo ``check``).
    """
    def check(old: CompiledGeometry, new: CompiledGeometry) -> None:
        b = old.find_body(label)
        if b is None:
            return
        msg = motion_problems(old, new, b.index, bounds)
        if msg:
            raise EditError(msg)

    return check


def _verify_motion(old: CompiledGeometry, doc: GeoDocument, kb: int, parinp=None, bounds=None) -> None:
    """Compila el documento ya movido y comprueba que solo cambia lo que tiene que cambiar."""
    from .compiler import compile_geometry

    ct = old.ct
    new = compile_geometry(doc, parinp=parinp if parinp is not None else (old.parinp or None),
                           ct_loader=(lambda _name: ct) if ct is not None else None)
    if not new.ok:
        return  # quien aplica el cambio lo compila y da el mensaje de GEOMIN
    msg = motion_problems(old, new, kb, bounds)
    if msg:
        raise EditError(msg)


# ---------------------------------------------------------------------------
# Clones
# ---------------------------------------------------------------------------

def delete_clone(doc: GeoDocument, geo: CompiledGeometry, kb: int) -> list[str]:
    """Quita un clon: su volumen vuelve al cuerpo o módulo que lo contenía."""
    el = placeable(geo, kb)
    if not isinstance(el, CloneElement):
        raise EditError("No es un clon.")
    lab = _lab(el.label)
    if clone_users(doc, el.label):  # (las etiquetas, con sus cuatro columnas)
        raise EditError(f"Otro clon copia a «{lab}»: quítelo antes.")
    notes = []
    key = label_key(el.label)
    for user in body_users(doc, el.label):
        user.items = [r for r in user.items if r.is_surface or label_key(r.label) != key]
        user.modified = True
        what = "módulo" if isinstance(user, ModuleElement) else "cuerpo"
        notes.append(f"El {what} «{_lab(user.label)}» ocupa ahora su volumen.")
    doc.elements.remove(el)
    return notes
