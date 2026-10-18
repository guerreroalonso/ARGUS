"""«Envolver»: un módulo nuevo alrededor de cuerpos o módulos que ya existen.

Sirve para mover, girar o duplicar varios elementos juntos. Los elementos
envueltos (hermanos: están dentro del mismo contenedor) pasan a ser hijas
del módulo nuevo, con sus cuerpos limitantes (como al convertir un cuerpo en
módulo: si no, GEOMIN les daría dos madres). El módulo se llena del material
del contenedor, así que lo que se simula no cambia.

El módulo se define justo antes del primer elemento que nombra a alguno de
los envueltos (el contenedor u otro cuerpo que los excluía) y todos ellos
pasan a nombrar al módulo: en PENGEOM, dentro de un módulo gana la hija con
el número más bajo, y el módulo tiene que ir antes que los cuerpos que lo
excluyen.
"""

from __future__ import annotations

import math

import numpy as np

from .compiler import CompiledGeometry, body_title
from .edit import (
    REDUCED,
    EditError,
    PlacementCheck,
    Primitive,
    _clean,
    _insert_before,
    _lab,
    _limiting_closure,
    _set_params,
    children_of,
    closure_problem,
    editable_body,
    is_top_level,
    label_key,
    local_frame,
    logical_parent,
    new_label,
    set_description,
    top_level,
)
from .extent import members_box, solid_bodies
from .model import (
    BodyElement,
    CloneElement,
    Element,
    ElementKind,
    GeoDocument,
    ModuleElement,
    RefLine,
    SurfaceElement,
)

WRAP_KINDS = ("box", "cylinder", "sphere")


def _wrappable(geo: CompiledGeometry, kb: int) -> Element:
    """Cuerpo, módulo o clon del fichero principal."""
    b = geo.body(kb)
    if b.origin == "clone" and isinstance(b.element, CloneElement) and b.element is b.clone_element \
            and b.document is geo.document:
        return b.element
    if b.origin == "ct" and b.element is not None and b.document is geo.document:
        return b.element  # la caja CT se puede envolver (el módulo nuevo no la mueve)
    return editable_body(geo, kb)


def wrap_candidates(geo: CompiledGeometry, kb: int) -> tuple[int, list[int]]:
    """(contenedor, elementos que se pueden envolver junto con ``kb``: sus hermanos editables)."""
    cont = logical_parent(geo, kb) or top_level(geo)
    out = []
    for k in children_of(geo, cont):
        try:
            _wrappable(geo, k)
        except EditError:
            continue
        out.append(k)
    return cont, out


MIN_CLEARANCE = 1e-5  # cm entre los elementos envueltos y las caras del módulo, como mínimo


def fitted_primitive(extents, kind: str = "box", margin: float = 0.0, axis: str = "z", points=None,
                     clearance: float = MIN_CLEARANCE) -> Primitive:
    """La caja, el cilindro (eje ``axis``) o la esfera que contienen las cajas ``extents``.

    Con ``points`` (puntos de los elementos), el cilindro y la esfera se
    ajustan a ellos: si caben en el inscrito en la caja (un disco, una bola),
    se usa ese; si no, el circunscrito.

    Las caras planas quedan al menos a ``clearance`` de los elementos: una
    pared del módulo en el mismo sitio que una cara de una hija (con otra
    superficie) confunde a STEP de PENGEOM (véase
    ``validate.coincident_walls``). Una esfera o un cilindro iguales a los de
    una hija son la misma superficie (GEOMIN las junta).
    """
    flat = max(float(margin), float(clearance))
    lo = np.min([np.asarray(e[0], dtype=float) for e in extents], axis=0)
    hi = np.max([np.asarray(e[1], dtype=float) for e in extents], axis=0)
    center = 0.5 * (lo + hi)
    c = tuple(_clean(float(v)) for v in center)
    size = hi - lo
    if kind == "box":
        return Primitive("box", center=c, sides=tuple(_clean(float(v) + 2 * flat) for v in size))
    pts = None if points is None or len(points) == 0 else np.asarray(points, dtype=float) - center
    if kind == "sphere":
        inscribed = 0.5 * float(size.max())
        r = 0.5 * float(np.linalg.norm(size))
        if pts is not None and float(np.linalg.norm(pts, axis=1).max()) <= inscribed * (1 + 1e-9):
            r = inscribed
        return Primitive("sphere", center=c, radius=_nice_up(r + margin))
    k = "xyz".index(axis)
    others = [i for i in range(3) if i != k]
    inscribed = 0.5 * float(max(size[others[0]], size[others[1]]))
    r = 0.5 * math.hypot(size[others[0]], size[others[1]])
    if pts is not None and float(np.hypot(pts[:, others[0]], pts[:, others[1]]).max()) <= inscribed * (1 + 1e-9):
        r = inscribed
    return Primitive("cylinder", center=c, radius=_nice_up(r + margin), height=_clean(float(size[k]) + 2 * flat),
                     axis=axis)


def sample_solids(geo: CompiledGeometry, locator, kbs: list[int], extents, n: int = 20000, seed: int = 3,
                  bounds=None):
    """Puntos (N, 3) que caen dentro de los elementos ``kbs`` (con lo que tienen dentro).

    También lo que asoman sus limitantes aunque sus superficies no los acoten
    (``extent.sample_members``, dentro de ``bounds``, la escena).
    """
    from .extent import sample_members

    rng = np.random.default_rng(seed)
    out = []
    for kb, ext in zip(kbs, extents):
        lo, hi = members_box(geo, kb, ext, bounds)  # con lo que asoman sus cuerpos limitantes
        pts, _missing = sample_members(geo, locator, kb, lo, hi, max(1000, n // max(1, len(kbs))), rng, bounds)
        out.append(pts)
    return np.vstack(out) if out else np.zeros((0, 3))


def _nice_up(v: float) -> float:
    """Redondea hacia arriba a 6 cifras significativas (un radio que siempre contiene)."""
    if v <= 0:
        return v
    q = 10.0 ** (math.floor(math.log10(v)) - 5)
    return float(f"{math.ceil(v / q - 1e-9) * q:.12g}")


def check_wrap(geo: CompiledGeometry, locator, kbs: list[int], prim: Primitive, container: int, extents,
               bounds, n: int = 20000, seed: int = 13) -> PlacementCheck:
    """¿El módulo ``prim`` contiene enteros los elementos ``kbs`` y no se solapa con nada más?

    Los elementos se muestrean con todo lo que tienen dentro (también los
    limitantes sin cota analítica, ``extent.sample_members``); lo que no se
    puede muestrear no se da por bueno. Tampoco puede quedar dentro otro
    elemento pequeño que no sea de los envueltos.
    """
    prim.validate()
    from .extent import sample_members

    rng = np.random.default_rng(seed)
    size = max(float(np.max(np.asarray(e[1]) - np.asarray(e[0]))) for e in extents) or 1.0
    tol = 1e-9 * max(size, float(np.abs(np.asarray(prim.center)).max()))
    solids: set[int] = set()
    for kb in kbs:  # sus limitantes (y los de estos) serán hijas declaradas del módulo
        problem = closure_problem(geo, kb)
        if problem:
            return PlacementCheck(False, problem, 1.0)
    for kb, ext in zip(kbs, extents):
        solids |= solid_bodies(geo, kb)
        lo, hi = members_box(geo, kb, ext, bounds)  # sus limitantes van dentro del módulo: también lo que asoman
        mine, missing = sample_members(geo, locator, kb, lo, hi, max(2000, n // len(kbs)), rng, bounds)
        if missing:
            names = ", ".join(body_title(geo, k) for k in missing[:3])
            return PlacementCheck(False, f"No se ha podido comprobar que {names} quede dentro del módulo (no se ha "
                                         "podido muestrear: ¿está vacío?).", 1.0, {k: 1.0 for k in missing})
        if len(mine) and not prim.contains(mine, tol).all():
            frac = 1.0 - float(prim.contains(mine, tol).mean())
            return PlacementCheck(False, f"{body_title(geo, kb)} no queda entero dentro del módulo (sobresale un "
                                         f"{100 * frac:.1f} %): hágalo más grande.", frac, {kb: frac})
    pts = prim.sample(n, rng, bounds)
    ib, _ = locator.locate(pts)
    allowed = solids | {container}
    bad = ~np.isin(ib, list(allowed))
    if bad.any():
        frac = float(bad.mean())
        offenders: dict[int, float] = {}
        for b in ib[bad]:
            offenders[int(b)] = offenders.get(int(b), 0.0) + 1.0 / n
        worst = sorted(offenders.items(), key=lambda kv: -kv[1])[:3]
        names = ", ".join(f"{body_title(geo, b) if b <= geo.nbodys else 'fuera de la geometría'} ({100 * f:.1f} %)"
                          for b, f in worst)
        return PlacementCheck(False, f"El módulo se solaparía con otros elementos: {names}. Hágalo más pequeño o "
                                     "envuelva también a esos.", frac, offenders)
    # Elementos pequeños que quedarían dentro sin ser de los envueltos (el muestreo del módulo no los ve).
    from .edit import enclosed_bodies

    inner = list(enclosed_bodies(geo, locator, prim, allowed, rng))
    if inner:
        names = ", ".join(body_title(geo, b) for b in inner[:3])
        return PlacementCheck(False, f"El módulo se solaparía con {names}, que no está entre los elementos elegidos: "
                                     "envuélvalo también o haga el módulo más pequeño.", 0.0, {b: 0.0 for b in inner})
    # Lo que el muestreo no ve: láminas finas dentro del módulo que no son de los envueltos (con rayos).
    from .edit import thin_message
    from .raycheck import intruders

    b_lo, b_hi = prim.box()
    found = intruders(geo, b_lo, b_hi, lambda p: prim.contains(p, -tol), allowed)
    return thin_message(geo, found) or \
        PlacementCheck(True, "El módulo contiene enteros los elementos elegidos y no se solapa con nada más.")


def wrap(doc: GeoDocument, geo: CompiledGeometry, kbs: list[int], prim: Primitive, material: int | None = None,
         name: str = "", parinp=None) -> str:
    """Crea un módulo con la forma ``prim`` alrededor de ``kbs`` (hermanos); devuelve su etiqueta."""
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    kbs = sorted(set(int(k) for k in kbs))
    if not kbs:
        raise EditError("Elija al menos un elemento que envolver.")
    prim.validate()
    if prim.kind not in WRAP_KINDS:
        raise EditError("El módulo tiene que ser una caja, un cilindro o una esfera.")
    parents = {logical_parent(geo, k) or top_level(geo) for k in kbs}
    if len(parents) != 1:
        raise EditError("Los elementos que se envuelven tienen que estar dentro del mismo contenedor.")
    container = parents.pop()
    enclosure = is_top_level(geo, container)
    cont_el = None if enclosure else editable_body(geo, container)
    wrapped = [_wrappable(geo, k) for k in kbs]
    # Miembros: los envueltos y sus cuerpos limitantes (y los de estos).
    members: list[RefLine] = []
    seen: set[str] = set()
    # (las etiquetas, con sus cuatro columnas, como PENGEOM: «   1» y «1   » son distintas)
    for k, el in zip(kbs, wrapped):
        kw = "BODY" if isinstance(el, BodyElement) else "MODULE"
        if label_key(el.label) not in seen:
            seen.add(label_key(el.label))
            members.append(RefLine(kw, el.label))
        if isinstance(el, BodyElement):
            # Sus limitantes (y los de estos) serán hijas declaradas del módulo: todos, también los de un INCLUDE*.
            problem = closure_problem(geo, k)
            if problem:
                raise EditError(problem)
            for r in _limiting_closure(doc, el):
                if label_key(r.label) not in seen:
                    seen.add(label_key(r.label))
                    members.append(RefLine(r.keyword, r.label))
    labs = {label_key(r.label) for r in members}
    # Los elementos que nombran a alguno de los envueltos (el contenedor, su módulo, otros que los excluían).
    users = [e for e in doc.elements if isinstance(e, BodyElement | ModuleElement)
             and label_key(e.label) not in labs
             and any(not r.is_surface and label_key(r.label) in labs for r in e.items)]
    member_pos = [i for i, e in enumerate(doc.elements)
                  if isinstance(e, BodyElement | ModuleElement | CloneElement) and label_key(e.label) in labs]
    last = max(member_pos) if member_pos else -1
    early = [u for u in users if doc.elements.index(u) < last]
    if early:
        raise EditError(f"«{_lab(early[0].label)}» nombra a uno de los elementos antes de que estén todos "
                        "definidos: el orden del fichero no permite envolverlos (edite el fichero).")
    anchor = users[0] if users else cont_el
    # Superficies del módulo, en el sistema del contenedor.
    frame = local_frame(geo, container, parinp) if not enclosure else None
    new_els: list[Element] = []
    refs: list[RefLine] = []
    taken: set[str] = set()
    for idx, params, side, desc in prim.surfaces(frame):
        lab = new_label(doc, "surface", geo, taken)
        taken.add(lab.strip())
        s = SurfaceElement(ElementKind.SURFACE, label=lab, indices=idx, params=_set_params([], params, REDUCED),
                           modified=True)
        set_description(s, desc)
        new_els.append(s)
        refs.append(RefLine("SURFACE", lab, side))
    mlab = new_label(doc, "body", geo)
    if material is None:
        material = geo.body(container).material if not enclosure else 0
    module = ModuleElement(ElementKind.MODULE, label=mlab, material=int(material),
                           items=refs + [RefLine(r.keyword, r.label) for r in members], modified=True)
    default = "módulo que envuelve a " + ", ".join(f"«{_lab(el.label)}»" for el in wrapped)
    set_description(module, name.strip() or default)
    new_els.append(module)
    _insert_before(doc, anchor, new_els)
    # Quien nombraba a los envueltos nombra ahora al módulo (una vez); los módulos dejan de tenerlos de hijas.
    for user in users:
        items = []
        added = False
        for r in user.items:
            if not r.is_surface and label_key(r.label) in labs:
                if not added:
                    items.append(RefLine("MODULE", mlab))
                    added = True
                continue
            items.append(r)
        user.items = items
        user.modified = True
    return mlab
