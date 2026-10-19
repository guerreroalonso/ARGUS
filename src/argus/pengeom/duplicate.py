"""Duplicar cuerpos y módulos: copias independientes y clones enlazados (``CLONE``).

**Copia independiente.** Se copian el elemento y todo lo que tiene dentro
(las hijas de un módulo; los cuerpos limitantes de un cuerpo) con etiquetas
nuevas, y sus superficies. El elemento de arriba se coloca en su sitio
nuevo: un módulo, componiendo su transformación; un cuerpo, moviendo sus
superficies (lo que va dentro de un módulo copiado se copia tal cual, porque
está en el sistema del módulo). Cada copia se edita después por separado.

**Clon enlazado.** Un elemento ``CLONE`` que repite un módulo con otra
transformación. PENGEOM copia el módulo al leer el fichero, así que si se
cambia el original cambian todos los clones. Las copias de un clon no tienen
etiqueta (en el .in se nombran por número) y las superficies ``SURFACE*`` no
se clonan (se comparten).

La transformación de un clon se aplica a las superficies del original tal
como están en el momento de clonar: con la transformación del propio módulo
y las de los módulos que lo contienen ya leídos; los que se leen después
(los que contienen al clon) se aplican luego al clon. Por eso

    T_clon = B_clon⁻¹ · G · B_orig

con G el movimiento global que se quiere, B_clon el sistema de los módulos
que contendrán al clon y B_orig el de los módulos del original que se leen
después del clon (comprobado con PENGEOM real).
"""

from __future__ import annotations

import numpy as np

from .compiler import CompiledGeometry, body_title
from .edit import (
    EditError,
    PlacementCheck,
    _insert_before,
    _lab,
    _mother_module_element,
    box_reach,
    contains_ct,
    editable_body,
    is_top_level,
    label_key,
    local_frame,
    new_label,
    outside_enclosure,
    set_description,
    top_level,
)
from .extent import solid_bodies
from .model import (
    BodyElement,
    CloneElement,
    Element,
    ElementKind,
    EndElement,
    GeoDocument,
    ModuleElement,
    RefLine,
    SurfaceElement,
)
from .placement import (
    Frame,
    _copy_param,
    compose,
    copy_surface,
    element_transform,
    frame_after,
    identity,
    inverse,
    mother_frame,
    placeable,
    set_transform,
    starred_surfaces,
    transform_values,
    translation,
)
from .surfaces import transform_surface

MAX_COPIES = 200


class Copies(list):
    """Las etiquetas de las copias (una lista) y, en ``notes``, los avisos (p. ej. un valor que usaba PARINP y en la
    copia es un número: cambiar PARINP ya no la mueve)."""

    def __init__(self, labels=(), notes=()):
        super().__init__(labels)
        self.notes: list[str] = list(notes)


_copy_surface = copy_surface  # la copia de una superficie está en placement (también la usa separate_surfaces)


def _own_box(geo: CompiledGeometry, kb: int):
    """Caja de lo que está dentro de las superficies propias del cuerpo o módulo, sin la esfera de la envolvente (que
    acota a todos): la exacta (``extent.exact_box``; hasta 0.17.2, la de cada superficie, que con un cilindro girado
    es infinita: «no es un volumen cerrado»)."""
    from .extent import exact_box

    return exact_box(geo, kb)


def _element_of(geo: CompiledGeometry, kb: int) -> Element:
    b = geo.body(kb)
    if b.origin == "clone":
        raise EditError(f"{body_title(geo, kb)} es un clon: para copiar algo que contiene clones, conviértalos "
                        "antes en copias independientes.")
    return editable_body(geo, kb)


class _Tree:
    """Un elemento y todo lo que tiene dentro, listo para copiarlo."""

    def __init__(self, doc: GeoDocument, geo: CompiledGeometry, kb: int):
        self.doc, self.geo, self.kb = doc, geo, kb
        if contains_ct(geo, kb):
            raise EditError("Contiene la caja CT y PENGEOM admite un solo CT: no se puede duplicar.")
        top = _element_of(geo, kb)
        # Un cuerpo o un módulo que sus superficies no cierran no se puede copiar: las copias, desplazadas, podrían
        # quedar encima de otras cosas fuera de lo que se comprueba (hasta 0.17.2, un módulo abierto sí se dejaba).
        lo, hi = _own_box(geo, kb)
        if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
            kind = "un volumen cerrado" if not geo.body(kb).is_module else "un módulo cerrado"
            raise EditError(f"{body_title(geo, kb)} no es {kind} (sus superficies no lo acotan): no se puede "
                            "duplicar.")
        stars = starred_surfaces(geo, kb)
        if stars:
            raise EditError(f"Usa superficies SURFACE* (n.º {', '.join(map(str, stars[:5]))}), que no se mueven: "
                            "no se puede duplicar.")
        members = solid_bodies(geo, kb)
        self.members = members
        # Las etiquetas, con sus cuatro columnas (como PENGEOM: «   1» y «1   » son distintas): ``label_key``.
        self.elements: dict[str, Element] = {}  # etiqueta -> elemento
        for k in sorted(members):
            el = _element_of(geo, k)
            self.elements[label_key(el.label)] = el
        # Todo lo que nombran tiene que estar dentro.
        for el in self.elements.values():
            for r in el.items:
                if not r.is_surface and label_key(r.label) not in self.elements:
                    raise EditError(f"«{_lab(el.label)}» está recortado por «{_lab(r.label)}», que no está dentro de "
                                    f"{body_title(geo, kb)}: conviértalo antes en módulo o duplique el contenedor.")
        # Los que están en el sistema de la madre del de arriba («de primer nivel») y los que van dentro de
        # un módulo copiado (se copian tal cual).
        self.frame_level = {lab for lab, el in self.elements.items()
                            if geo.find_body(el.label) is not None
                            and geo.find_body(el.label).mother not in members}
        self.surface_frame: dict[str, bool] = {}  # etiqueta -> ¿se mueve con el primer nivel?
        for lab, el in self.elements.items():
            moves = lab in self.frame_level and isinstance(el, BodyElement)
            for r in el.items:
                if r.is_surface:
                    s = label_key(r.label)
                    if s in self.surface_frame and self.surface_frame[s] != moves:
                        raise EditError(f"La superficie «{s.strip()}» la comparten elementos de niveles distintos: no "
                                        "se puede duplicar (edite el fichero).")
                    self.surface_frame[s] = moves
        self.top = top

    def order(self) -> list[Element]:
        """Superficies y elementos en el orden del fichero."""
        labs = set(self.elements)
        surfs = set(self.surface_frame)
        out = []
        for el in self.doc.elements:
            if isinstance(el, SurfaceElement) and label_key(el.label) in surfs:
                out.append(el)
            elif isinstance(el, BodyElement | ModuleElement) and label_key(el.label) in labs:
                out.append(el)
        return out


def _container_element(doc: GeoDocument, geo: CompiledGeometry, container: int) -> Element | None:
    """Elemento del contenedor; None en el nivel superior (la envolvente o, sin ella, fuera de todo)."""
    if is_top_level(geo, container):
        return None
    return editable_body(geo, container)


def _copy_tree(tree: _Tree, motion: Frame, taken_b: set[str], taken_s: set[str], suffix: str,
               parinp=None) -> tuple[list[Element], dict[str, str], list[str]]:
    """Copia del árbol movida por ``motion`` (en el sistema de la madre); (elementos, etiquetas, avisos)."""
    doc, geo = tree.doc, tree.geo
    bmap: dict[str, str] = {}
    smap: dict[str, str] = {}
    notes: list[str] = []
    for lab in tree.elements:
        new = new_label(doc, "body", geo, taken_b)
        taken_b.add(new.strip())
        bmap[lab] = new
    for s in tree.surface_frame:
        new = new_label(doc, "surface", geo, taken_s)
        taken_s.add(new.strip())
        smap[s] = new
    out: list[Element] = []
    rot, shift = motion
    identity_rot = np.allclose(rot, np.eye(3), atol=1e-14)
    for el in tree.order():
        if isinstance(el, SurfaceElement):
            c = _copy_surface(el, smap[label_key(el.label)])
            if tree.surface_frame[label_key(el.label)] and (not identity_rot or np.any(shift != 0.0)):
                notes += transform_surface(c, rot, shift, parinp)
            out.append(c)
            continue
        items = []
        for r in el.items:
            if r.is_surface:
                items.append(RefLine("SURFACE", smap[label_key(r.label)], r.side))
            else:
                items.append(RefLine(r.keyword, bmap[label_key(r.label)]))
        lab = bmap[label_key(el.label)]
        if isinstance(el, ModuleElement):
            c = ModuleElement(ElementKind.MODULE, label=lab, material=el.material, items=items,
                              has_ones_line=el.has_ones_line, transform=[_copy_param(p) for p in el.transform],
                              modified=True)
            if label_key(el.label) in tree.frame_level:
                t = compose(motion, element_transform(el, parinp))
                vals = transform_values(t)
                keep = {p.name for p in el.transform if p.ipar > 0}
                old = transform_values(element_transform(el, parinp))
                changed = {k: v for k, v in vals.items() if abs(v - old[k]) > 1e-12 * max(1.0, abs(old[k]))}
                for p in c.transform:
                    if p.name in changed and p.name in keep:
                        notes.append(f"{p.name} del módulo «{_lab(el.label)}» usaba PARINP({p.ipar}); en la copia es "
                                     "un número.")
                        p.ipar = 0
                if changed:
                    set_transform(c, changed)
        else:
            c = BodyElement(ElementKind.BODY, label=lab, material=el.material, items=items, modified=True)
        desc = " ".join(el.description.split())
        set_description(c, (desc + " " if desc else "") + suffix)
        out.append(c)
    return out, bmap, notes


def _declare(doc: GeoDocument, geo: CompiledGeometry, container: int, refs: list[RefLine], top: RefLine) -> None:
    """Declara las copias en su contenedor (como ``add_primitive``)."""
    cont = _container_element(doc, geo, container)
    if cont is None:
        return
    if isinstance(cont, ModuleElement):
        cont.items.extend(refs)
        cont.modified = True
        return
    cont.items.append(top)
    cont.modified = True
    mother = _mother_module_element(doc, cont.label)
    if mother is not None:
        mother.items.extend(RefLine(r.keyword, r.label) for r in refs)
        mother.modified = True


def check_copies(geo: CompiledGeometry, locator, kb: int, step, count: int, container: int, extent,
                 n: int = 12000, seed: int = 9, bounds=None) -> PlacementCheck:
    """¿Caben las ``count`` copias (desplazadas k·step) en el espacio libre del contenedor?

    Se muestrean el elemento y todo lo que tiene dentro, también lo que asoman
    sus cuerpos limitantes aunque sus superficies no los acoten
    (``extent.sample_members``, dentro de ``bounds``, la escena); lo que no se
    puede muestrear no se da por bueno. En el nivel superior de una geometría
    sin envolvente, las copias tienen que quedar dentro de la que añadirá GEOMIN.
    """
    from .extent import members_box, sample_members

    step = np.asarray(step, dtype=float)
    if not np.all(np.isfinite(step)) or not np.any(step != 0.0):
        return PlacementCheck(False, "Las copias tienen que ir desplazadas (el desplazamiento es cero).", 1.0)
    lo, hi = members_box(geo, kb, extent, bounds)  # con lo que asoman sus cuerpos limitantes (también se copian)
    rng = np.random.default_rng(seed)
    solid = solid_bodies(geo, kb)
    mine, missing = sample_members(geo, locator, kb, lo, hi, n, rng, bounds)
    if missing:
        names = ", ".join(body_title(geo, k) for k in missing[:3])
        return PlacementCheck(False, f"No se ha podido comprobar dónde quedarían las copias de {names} (no se ha "
                                     "podido muestrear: ¿está vacío?).", 1.0, {k: 1.0 for k in missing})
    if len(mine) == 0:
        return PlacementCheck(False, "No se ha podido muestrear el volumen del elemento.", 1.0)
    p_lo, p_hi = mine.min(axis=0), mine.max(axis=0)
    if np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.all(hi >= lo):
        p_lo, p_hi = np.minimum(p_lo, lo), np.maximum(p_hi, hi)
    far = outside_enclosure(geo, container, max(box_reach(p_lo + k * step, p_hi + k * step)
                                                for k in range(1, count + 1)))
    if far is not None:
        return far
    for k in range(1, count + 1):
        moved = mine + k * step
        ib2, _ = locator.locate(moved)
        bad = ib2 != container
        if bad.any():
            frac = float(bad.mean())
            offenders: dict[int, float] = {}
            for b in ib2[bad]:
                offenders[int(b)] = offenders.get(int(b), 0.0) + 1.0 / len(moved)
            worst = sorted(offenders.items(), key=lambda kv: -kv[1])[:3]
            names = ", ".join(f"{body_title(geo, b) if b <= geo.nbodys else 'fuera de la geometría'} "
                              f"({100 * f:.1f} %)" for b, f in worst)
            which = "La copia" if count == 1 else f"La copia {k}"
            return PlacementCheck(False, f"{which} se solaparía: {100 * frac:.1f} % de su volumen caería en {names}.",
                                  frac, offenders)
    # Lo que el muestreo no ve: láminas finas en el sitio de las copias (con rayos).
    from .edit import thin_message
    from .raycheck import intruders

    sol = list(solid)
    for k in range(1, count + 1):
        shift = k * step
        found = intruders(geo, p_lo + shift, p_hi + shift,
                          lambda p, s=shift: np.isin(locator.locate(p - s)[0], sol), {container})
        msg = thin_message(geo, found)
        if msg is not None:
            return msg
    what = "La copia cabe" if count == 1 else f"Las {count} copias caben"
    return PlacementCheck(True, f"{what} en el espacio libre del contenedor.")


def duplicate(doc: GeoDocument, geo: CompiledGeometry, kb: int, step, count: int = 1, container: int | None = None,
              linked: bool = False, parinp=None) -> Copies:
    """Duplica el cuerpo o módulo ``kb`` ``count`` veces, desplazando cada copia ``step`` cm más.

    ``linked``: clones (CLONE) en vez de copias independientes (solo módulos).
    ``container``: dónde van las copias (por defecto, en el mismo contenedor
    que el original). Devuelve las etiquetas de las copias (con los avisos en
    ``notes``: revisión de 0.16.2, se perdían).
    """
    from .edit import logical_parent

    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    if not 1 <= count <= MAX_COPIES:
        raise EditError(f"El número de copias tiene que estar entre 1 y {MAX_COPIES}.")
    step = np.asarray(step, dtype=float)
    if not np.all(np.isfinite(step)) or not np.any(step != 0.0):
        raise EditError("Las copias tienen que ir desplazadas (el desplazamiento no puede ser cero).")
    if container is None:
        container = logical_parent(geo, kb) or top_level(geo)
    if 1 <= container <= geo.nbodys and container in solid_bodies(geo, kb):
        raise EditError("Las copias no pueden ir dentro del propio elemento: elija un contenedor de fuera.")
    if linked:
        return Copies(_clone(doc, geo, kb, step, count, container, parinp))
    tree = _Tree(doc, geo, kb)
    cont_el = _container_element(doc, geo, container)
    f_orig = mother_frame(geo, kb, parinp)
    f_copy = local_frame(geo, container, parinp) if cont_el is not None else identity()
    taken_b: set[str] = set()
    taken_s: set[str] = set()
    labels = Copies()
    for k in range(1, count + 1):
        g = translation(k * step)
        motion = compose(inverse(f_copy), compose(g, f_orig))
        suffix = "(copia)" if count == 1 else f"(copia {k})"
        els, bmap, notes = _copy_tree(tree, motion, taken_b, taken_s, suffix, parinp)
        labels.notes += [n for n in notes if n not in labels.notes]  # (los mismos en cada copia: una vez)
        _insert_before(doc, cont_el, els)
        top = bmap[label_key(tree.top.label)]
        kw = "MODULE" if isinstance(tree.top, ModuleElement) else "BODY"
        level = [RefLine("MODULE" if isinstance(tree.elements[lab], ModuleElement) else "BODY", bmap[lab])
                 for lab in tree.elements if lab in tree.frame_level]
        _declare(doc, geo, container, level, RefLine(kw, top))
        labels.append(top)
    return labels


def _insertion_index(doc: GeoDocument, anchor: Element | None) -> int:
    if anchor is None:
        return next((i for i, e in enumerate(doc.elements) if isinstance(e, EndElement)), len(doc.elements))
    return doc.elements.index(anchor)


_frame_after = frame_after  # está en placement (también lo usan las comprobaciones de los clones)


def clone_transform(doc: GeoDocument, geo: CompiledGeometry, kb: int, motion: Frame, container: int,
                    parinp=None) -> Frame:
    """Transformación de un CLONE del módulo ``kb`` que lo deja movido ``motion`` (global) en ``container``."""
    anchor = _container_element(doc, geo, container)
    index = _insertion_index(doc, anchor)
    b_orig = _frame_after(doc, geo, kb, index, parinp)
    b_clone = local_frame(geo, container, parinp) if anchor is not None else identity()
    return compose(inverse(b_clone), compose(motion, b_orig))


def _clone(doc: GeoDocument, geo: CompiledGeometry, kb: int, step, count: int, container: int,
           parinp=None) -> list[str]:
    el = placeable(geo, kb)  # un módulo o un clon (PENGEOM admite clonar un clon)
    orig_label = el.label
    stars = starred_surfaces(geo, kb)
    if stars:
        raise EditError("El módulo usa superficies SURFACE*: un clon las compartiría en su sitio (no se mueven). "
                        "Use una copia independiente.")
    anchor = _container_element(doc, geo, container)
    index = _insertion_index(doc, anchor)
    if doc.elements.index(el) >= index:
        raise EditError("El clon tiene que ir después del original en el fichero y el contenedor elegido está "
                        "antes: use una copia independiente.")
    taken: set[str] = set()
    labels = []
    new_els = []
    for k in range(1, count + 1):
        t = clone_transform(doc, geo, kb, translation(k * step), container, parinp)
        lab = new_label(doc, "body", geo, taken)
        taken.add(lab.strip())
        c = CloneElement(ElementKind.CLONE, label=lab, module_label=orig_label, modified=True)
        vals = {n: v for n, v in transform_values(t).items() if v != 0.0}
        if vals:
            set_transform(c, vals)
        name = " ".join((el.description or "").split())
        set_description(c, f"clon de «{_lab(orig_label)}»" + (f" ({name})" if name else "")
                        + (f" {k}" if count > 1 else ""))
        new_els.append(c)
        labels.append(lab)
    _insert_before(doc, anchor, new_els)
    for lab in labels:
        _declare(doc, geo, container, [RefLine("MODULE", lab)], RefLine("MODULE", lab))
    return labels


def materialize_clone(doc: GeoDocument, geo: CompiledGeometry, kb: int, parinp=None
                      ) -> tuple[list[str], dict[tuple, tuple]]:
    """Convierte un clon en una copia independiente (con la misma etiqueta), en el mismo sitio.

    Devuelve (avisos, identidades): cada cuerpo copiado por el clon (que no
    tenía etiqueta) y su copia nueva, para que el .in siga nombrando los
    mismos cuerpos.
    """
    from .identity import body_keys

    k_el = placeable(geo, kb)
    if not isinstance(k_el, CloneElement):
        raise EditError("No es un clon.")
    orig = geo.find_body(k_el.module_label)
    if orig is None:
        raise EditError("No se encuentra el módulo original.")
    tree = _Tree(doc, geo, orig.index)
    index = doc.elements.index(k_el)
    # Colocación del clon: B_K · T_K · A_O, con A_O lo ya aplicado al original al clonarlo.
    b_after = _frame_after(doc, geo, orig.index, index, parinp)
    f_orig = mother_frame(geo, orig.index, parinp)
    a_before = compose(inverse(b_after), f_orig)  # módulos del original leídos antes del clon
    t_k = element_transform(k_el, parinp)
    # La copia va en el sistema de la madre del clon (B_K) con T' = T_K · A_O · T_O: el «movimiento» que
    # recibe el módulo de arriba al copiarlo es T_K · A_O.
    taken_b = {_lab(k_el.label)}
    els, bmap, notes = _copy_tree(tree, compose(t_k, a_before), taken_b, set(), "(antes clon)", parinp)
    # La copia de arriba toma la etiqueta del clon.
    top_old = label_key(tree.top.label)
    new_top = bmap[top_old]
    for e in els:
        if isinstance(e, BodyElement | ModuleElement) and label_key(e.label) == label_key(new_top):
            e.label = k_el.label
            set_description(e, k_el.description.strip() or e.description.strip())
    pos = doc.elements.index(k_el)
    doc.elements[pos:pos + 1] = els
    # Las demás referencias al clon siguen valiendo (misma etiqueta). Identidades para el .in.
    keys = body_keys(geo)
    key_map: dict[tuple, tuple] = {}
    for b in geo.bodies:
        if b.origin == "clone" and b.clone_element is k_el and b.element is not k_el and b.clone_of:
            src = geo.body(b.clone_of)
            src_lab = label_key(src.alias[:4]) if src.alias[4] == "0" else None
            if src_lab in bmap:
                key_map[keys[b.index - 1]] = ("alias", bmap[src_lab] + "0")
    return notes, key_map
