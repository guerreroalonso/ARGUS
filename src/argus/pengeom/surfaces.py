"""Superficies, lados y etiquetas (punto 4 del editor).

* **Etiquetas**: cambiar la de un cuerpo, módulo, clon o superficie, con todas
  las referencias del fichero (cuerpos limitantes, hijas, ``CLONE``). Las del
  .in las cambia ``penhan.follow``.
* **Lados**: el puntero de lado (``SIDE POINTER``) de cada superficie que
  limita un cuerpo; quitar una superficie de un cuerpo o añadirle otra (una
  que ya exista, un plano nuevo o una cuádrica en forma implícita).
* **Forma implícita**: los diez coeficientes ``AXX...A0`` y la transformación
  opcional (tras la línea de unos) de una superficie; pasar una superficie en
  forma reducida a implícita sin cambiarla (mismos coeficientes en PENGEOM).
* **Qué cuádrica es**: clasificación por los autovalores (esfera, elipsoide,
  cilindro, cono, hiperboloides, paraboloides, planos...).

Las etiquetas de PENGEOM ocupan 4 columnas y se comparan tal cual; ARGUS las
escribe alineadas a la derecha (``"   7"``). No se admiten los caracteres que
romperían las líneas del .geo o del .in (apóstrofo, paréntesis, coma).
"""

from __future__ import annotations

import math

import numpy as np

from .compiler import CompiledGeometry
from .edit import (
    EditError,
    _clean,
    _direction_angles,
    _insert_before,
    _lab,
    _set_params,
    editable_body,
    effective_value,
    euler_angles,
    find_element,
    label_key,
    local_frame,
    new_label,
    set_description,
    surface_users,
)
from .model import (
    COEF_NAMES,
    BodyElement,
    CloneElement,
    CtBoxElement,
    Element,
    ElementKind,
    GeoDocument,
    ModuleElement,
    ParamLine,
    RefLine,
    SurfaceElement,
)
from .quadric import reduced_name

FORBIDDEN = set("'(),")
TRANSFORM_ORDER = ("OMEGA", "THETA", "PHI", "X-SHIFT", "Y-SHIFT", "Z-SHIFT")


# ---------------------------------------------------------------------------
# Etiquetas
# ---------------------------------------------------------------------------

def label_text(text: str) -> str:
    """Etiqueta de 4 caracteres (alineada a la derecha) a partir de lo que escribe el usuario."""
    t = (text or "").strip()
    if not t:
        raise EditError("La etiqueta no puede estar vacía.")
    if len(t) > 4:
        raise EditError("Las etiquetas de PENGEOM tienen como máximo 4 caracteres.")
    bad = sorted({c for c in t if c in FORBIDDEN or not 32 <= ord(c) < 127})
    if bad:
        shown = " ".join(repr(c) for c in bad)
        raise EditError(f"La etiqueta no puede llevar {shown} (solo letras, números y signos sencillos sin "
                        "acentos; ni apóstrofos, paréntesis ni comas).")
    return t.rjust(4)


def _body_like(doc: GeoDocument) -> list[Element]:
    return [e for e in doc.elements if isinstance(e, BodyElement | ModuleElement | CloneElement | CtBoxElement)]


def renamable_body(geo: CompiledGeometry, kb: int) -> Element:
    """Elemento cuya etiqueta se puede cambiar: cuerpos y módulos del fichero principal y clones."""
    b = geo.body(kb)
    if b.origin == "clone" and isinstance(b.element, CloneElement) and b.element is b.clone_element \
            and b.document is geo.document:
        return b.element
    return editable_body(geo, kb)


def rename_body(doc: GeoDocument, geo: CompiledGeometry, kb: int, text: str) -> tuple[str, str]:
    """Cambia la etiqueta de un cuerpo, módulo o clon; devuelve (etiqueta de antes, etiqueta nueva)."""
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    el = renamable_body(geo, kb)
    old = el.label
    new = label_text(text)
    # Las etiquetas, con sus cuatro columnas (como PENGEOM: «   1» y «1   » son distintas).
    if label_key(new) == label_key(old):
        return old, old
    for other in _body_like(doc):
        if other is not el and label_key(other.label) == label_key(new):
            raise EditError(f"Ya hay otro cuerpo o módulo con la etiqueta «{new.strip()}».")
    if geo.find_body(new) is not None:
        raise EditError(f"Ya hay otro cuerpo o módulo con la etiqueta «{new.strip()}».")
    el.label = new
    el.modified = True
    for other in doc.elements:
        if isinstance(other, BodyElement | ModuleElement):
            changed = False
            for r in other.items:
                if not r.is_surface and label_key(r.label) == label_key(old):
                    r.label = new
                    changed = True
            if changed:
                other.modified = True
        elif isinstance(other, CloneElement) and label_key(other.module_label) == label_key(old):
            other.module_label = new
            other.modified = True
    return old, new


def rename_surface(doc: GeoDocument, geo: CompiledGeometry | None, old_label: str, text: str) -> tuple[str, str]:
    """Cambia la etiqueta de una superficie del fichero principal y la de todas sus referencias."""
    el = find_element(doc, "surface", old_label)
    if not isinstance(el, SurfaceElement):
        raise EditError(f"No hay ninguna superficie «{_lab(old_label)}» en el fichero principal.")
    old = el.label
    new = label_text(text)
    if label_key(new) == label_key(old):
        return old, old
    for other in doc.surfaces():
        if other is not el and label_key(other.label) == label_key(new):
            raise EditError(f"Ya hay otra superficie con la etiqueta «{new.strip()}».")
    if geo is not None and any(s.alias == new + "0" for s in geo.surfaces):
        raise EditError(f"Ya hay otra superficie con la etiqueta «{new.strip()}».")
    el.label = new
    el.modified = True
    for user in surface_users(doc, old):
        for r in user.items:
            if r.is_surface and label_key(r.label) == label_key(old):
                r.label = new
        user.modified = True
    return old, new


# ---------------------------------------------------------------------------
# Lados y superficies de un cuerpo
# ---------------------------------------------------------------------------

def _surface_ref(el: Element, label: str) -> RefLine:
    for r in el.items:
        if r.is_surface and label_key(r.label) == label_key(label):
            return r
    raise EditError(f"La superficie «{_lab(label)}» no limita a «{_lab(el.label)}».")


def set_side(doc: GeoDocument, geo: CompiledGeometry, kb: int, surface_label: str, side: int) -> None:
    """Puntero de lado (-1: F < 0, dentro; +1: F > 0, fuera) de una superficie de un cuerpo o módulo."""
    if side not in (-1, 1):
        raise EditError("El lado tiene que ser -1 o +1.")
    el = editable_body(geo, kb)
    r = _surface_ref(el, surface_label)
    if r.side != side:
        r.side = side
        el.modified = True


def remove_surface(doc: GeoDocument, geo: CompiledGeometry, kb: int, surface_label: str) -> list[str]:
    """Quita una superficie de las que limitan un cuerpo; si ya no la usa nadie, se borra."""
    el = editable_body(geo, kb)
    r = _surface_ref(el, surface_label)
    if sum(1 for x in el.items if x.is_surface) <= 1:
        raise EditError("Es la única superficie que lo limita: un cuerpo necesita al menos una.")
    el.items.remove(r)
    el.modified = True
    notes = [f"«{_lab(el.label)}» ya no está limitado por la superficie «{_lab(surface_label)}»."]
    if not surface_users(doc, surface_label):
        s = find_element(doc, "surface", surface_label)
        if s is not None:
            doc.elements.remove(s)
            notes.append(f"Superficie «{_lab(surface_label)}» borrada (no la usa nadie más).")
    return notes


def add_surface(doc: GeoDocument, geo: CompiledGeometry, kb: int, surface_label: str, side: int, *,
                as_seen: bool = False, parinp=None) -> list[str]:
    """Añade una superficie que ya existe a las que limitan un cuerpo o módulo (con su lado); devuelve avisos.

    Si la superficie está definida después del cuerpo, se mueve justo antes
    de él (GEOMIN solo acepta superficies ya leídas).

    ``as_seen``: la superficie tiene que quedar donde se ve ahora. GEOMIN gira
    y desplaza las superficies de lo que hay dentro de un módulo con el
    módulo: una que no usa nadie (está donde está escrita) se reescribe en el
    sistema del cuerpo para que, tras moverla GEOMIN, quede en su sitio.
    """
    if side not in (-1, 1):
        raise EditError("El lado tiene que ser -1 o +1.")
    el = editable_body(geo, kb)
    s = find_element(doc, "surface", surface_label)
    if not isinstance(s, SurfaceElement):
        raise EditError(f"No hay ninguna superficie «{_lab(surface_label)}» en el fichero principal.")
    if any(r.is_surface and label_key(r.label) == label_key(surface_label) for r in el.items):
        raise EditError(f"La superficie «{_lab(surface_label)}» ya limita a «{_lab(el.label)}»: cambie su lado.")
    notes: list[str] = []
    if as_seen and not surface_users(doc, surface_label) and not s.starred:
        rot, shift = _frame_for(geo, kb, parinp)
        if not (np.allclose(rot, np.eye(3), atol=1e-14) and np.allclose(shift, 0.0, atol=1e-14)):
            notes = transform_surface(s, rot.T, -(rot.T @ shift), parinp)
            s.modified = True
    if doc.elements.index(s) > doc.elements.index(el):
        doc.elements.remove(s)
        doc.elements.insert(doc.elements.index(el), s)
    pos = max((i for i, r in enumerate(el.items) if r.is_surface), default=-1) + 1
    el.items.insert(pos, RefLine("SURFACE", s.label, side))
    el.modified = True
    return notes


def _frame_for(geo: CompiledGeometry, kb: int, parinp=None):
    """Sistema en el que están escritas las superficies propias de ``kb``.

    El de su cadena de módulos; en un módulo, incluida su propia transformación
    (GEOMIN también gira y desplaza las superficies propias del módulo).
    """
    return local_frame(geo, kb, parinp)


def new_plane(doc: GeoDocument, geo: CompiledGeometry, kb: int, point, normal, description: str = "",
              parinp=None) -> str:
    """Superficie nueva: el plano que pasa por ``point`` con normal ``normal`` (globales).

    Se escribe en forma reducida (``INDICES=(0,0,0,1,0)``, z' = 0 girado y
    desplazado) en el sistema local del cuerpo ``kb``, y justo antes de él.
    F > 0 del lado hacia el que apunta la normal.
    """
    el = editable_body(geo, kb)
    n = np.asarray(normal, dtype=float)
    if not np.all(np.isfinite(n)) or np.linalg.norm(n) < 1e-300:
        raise EditError("La normal del plano no puede ser cero.")
    p = np.asarray(point, dtype=float)
    if not np.all(np.isfinite(p)):
        raise EditError("Punto no válido.")
    rot, shift = _frame_for(geo, kb, parinp)
    n_loc = rot.T @ (n / np.linalg.norm(n))
    p_loc = rot.T @ (p - shift)
    params = {**_direction_angles(n_loc),
              "X-SHIFT": _clean(float(p_loc[0])), "Y-SHIFT": _clean(float(p_loc[1])),
              "Z-SHIFT": _clean(float(p_loc[2]))}
    params = {k: v for k, v in params.items() if v != 0.0 or k in ("THETA", "PHI")}
    lab = new_label(doc, "surface", geo)
    s = SurfaceElement(ElementKind.SURFACE, label=lab, indices=(0, 0, 0, 1, 0),
                       params=_set_params([], params, ("X-SCALE", "Y-SCALE", "Z-SCALE", *TRANSFORM_ORDER)),
                       modified=True)
    pg = ", ".join(f"{v:g}" for v in p)
    ng = ", ".join(f"{v:.4g}" for v in n / np.linalg.norm(n))
    set_description(s, description or f"plano por ({pg}), normal ({ng})")
    _insert_before(doc, el, [s])
    return lab


def new_implicit(doc: GeoDocument, geo: CompiledGeometry, kb: int, coefs: dict[str, float],
                 transform: dict[str, float] | None = None, description: str = "") -> str:
    """Superficie nueva en forma implícita (coeficientes en el sistema local del cuerpo), antes de ``kb``."""
    el = editable_body(geo, kb)
    lab = new_label(doc, "surface", geo)
    s = SurfaceElement(ElementKind.SURFACE, label=lab, indices=(0, 0, 0, 0, 0), modified=True)
    set_implicit(s, coefs, transform or {})
    set_description(s, description or ("cuádrica: " + classify(implicit_quadric(s))))
    _insert_before(doc, el, [s])
    return lab


# ---------------------------------------------------------------------------
# Forma implícita
# ---------------------------------------------------------------------------

def implicit_values(el: SurfaceElement, parinp=None) -> tuple[dict[str, float], dict[str, float]]:
    """(coeficientes, transformación con ángulos en grados) efectivos de una superficie implícita."""
    coefs = dict.fromkeys(COEF_NAMES, 0.0)
    for p in el.params:
        if p.name in coefs:
            coefs[p.name] = effective_value(p, parinp)
    tr = dict.fromkeys(TRANSFORM_ORDER, 0.0)
    if el.has_ones_line:
        for p in el.transform:
            if p.name in tr:
                v = effective_value(p, parinp)
                tr[p.name] = math.degrees(v) if p.is_angle and p.unit == "RAD" else v
    return coefs, tr


def implicit_quadric(el: SurfaceElement, parinp=None):
    """Coeficientes finales (tras la transformación) de una superficie implícita, como GEOMIN."""
    from .quadric import rotshf

    coefs, tr = implicit_values(el, parinp)
    q = tuple(coefs[n] for n in COEF_NAMES)
    if el.has_ones_line:
        q = rotshf(math.radians(tr["OMEGA"]), math.radians(tr["THETA"]), math.radians(tr["PHI"]),
                   tr["X-SHIFT"], tr["Y-SHIFT"], tr["Z-SHIFT"], q)
    return q


def set_implicit(el: SurfaceElement, coefs: dict[str, float], transform: dict[str, float] | None = None) -> None:
    """Cambia coeficientes (``AXX``...) y, con ``transform``, la transformación (ángulos en grados).

    Solo se cambian los valores dados; los fijados por PARINP no se pueden cambiar.
    """
    if not el.implicit:
        raise EditError("La superficie está en forma reducida: se editan sus escalas, giros y desplazamientos "
                        "(o conviértala antes a forma implícita).")
    current = {p.name: p for p in el.params}
    for name, v in coefs.items():
        if name not in COEF_NAMES:
            raise EditError(f"Coeficiente desconocido: {name}.")
        v = float(v)
        if not math.isfinite(v):
            raise EditError(f"Valor no válido para {name}.")
        p = current.get(name)
        if p is not None and p.ipar > 0:
            if v != p.value:
                raise EditError(f"{name} lo fija el .in (PARINP {p.ipar}): cámbielo allí.")
            continue
        if p is None:
            current[name] = ParamLine(name, v)
        else:
            p.value = v
    # Se escriben en el orden canónico; los ceros sin PARINP sobran (GEOMIN pone 0 a los que faltan).
    el.params = [current[n] for n in COEF_NAMES if n in current and (current[n].value != 0.0 or current[n].ipar)]
    if not el.params or all(p.value == 0.0 and not p.ipar for p in el.params):
        raise EditError("Todos los coeficientes son cero: eso no es una superficie.")
    if transform is not None:
        el.transform = [p for p in _set_params(el.transform, transform, TRANSFORM_ORDER)
                        if p.value != 0.0 or p.ipar]
        el.has_ones_line = bool(el.transform)
    el.modified = True


def to_implicit(el: SurfaceElement, parinp=None) -> None:
    """Pasa una superficie en forma reducida a forma implícita sin cambiarla.

    Coeficientes de la forma reducida (I1/xs², I2/ys², I3/zs², I4/zs, I5) y la
    misma transformación (giro y desplazamiento, con sus PARINP) tras la línea
    de unos: GEOMIN obtiene los mismos coeficientes.
    """
    if el.implicit:
        return
    for p in el.params:
        if p.name.endswith("SCALE") and p.ipar > 0:
            raise EditError(f"{p.name} lo fija el .in (PARINP {p.ipar}): en forma implícita no se puede "
                            "conservar esa relación.")
    xs = el.param("X-SCALE", 1.0)
    ys = el.param("Y-SCALE", 1.0)
    zs = el.param("Z-SCALE", 1.0)
    i1, i2, i3, i4, i5 = el.indices
    values = {"AXX": i1 / (xs * xs), "AYY": i2 / (ys * ys), "AZZ": i3 / (zs * zs), "AZ": i4 / zs, "A0": float(i5)}
    transform = [ParamLine(p.name, p.value, p.ipar, p.unit) for p in el.params if p.name in TRANSFORM_ORDER]
    el.indices = (0, 0, 0, 0, 0)
    el.params = [ParamLine(n, values[n]) for n in COEF_NAMES if n in values and values[n] != 0.0]
    order = {n: i for i, n in enumerate(TRANSFORM_ORDER)}
    el.transform = sorted(transform, key=lambda p: order[p.name])
    el.has_ones_line = bool(el.transform)
    el.modified = True


# ---------------------------------------------------------------------------
# Qué cuádrica es
# ---------------------------------------------------------------------------

def classify(q, tol: float = 1e-10) -> str:
    """Nombre de la cuádrica F(r) = 0 (esfera, cilindro, cono, plano...)."""
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = (float(v) for v in q)
    a = np.array([[axx, 0.5 * axy, 0.5 * axz], [0.5 * axy, ayy, 0.5 * ayz], [0.5 * axz, 0.5 * ayz, azz]])
    b = np.array([ax, ay, az])
    scale = max(float(np.abs(a).max()), float(np.abs(b).max()), abs(a0), 1e-300)
    if float(np.abs(a).max()) <= tol * scale:
        if float(np.abs(b).max()) <= tol * scale:
            return "sin superficie (constante)"
        return "plano"
    lam, vec = np.linalg.eigh(a)
    lmax = float(np.abs(lam).max())
    nz = np.abs(lam) > tol * lmax
    bb = vec.T @ b
    # Completar cuadrados en las direcciones con autovalor no nulo.
    c = a0 - float(np.sum(bb[nz] ** 2 / (4.0 * lam[nz])))
    lin = bool(np.any(np.abs(bb[~nz]) > tol * max(scale, 1.0)))
    r = int(nz.sum())
    pos = int((lam[nz] > 0).sum())
    neg = r - pos
    cscale = max(abs(c), 1e-300)
    czero = abs(c) <= tol * max(scale, lmax * 1.0)
    del cscale
    same = pos == r or neg == r
    lnz = np.abs(lam[nz])
    equal = bool(lnz.max() - lnz.min() <= 1e-9 * lnz.max())
    sign_major = 1.0 if pos >= neg else -1.0
    if r == 3:
        if czero:
            return "punto" if same else "cono"
        if same:
            if (c > 0) == (pos == 3):
                return "vacía (sin puntos reales)"
            return "esfera" if equal else "elipsoide"
        # Con dos autovalores positivos (cambiando el signo si hace falta): x² + y² − z² = −c.
        rhs = -c * sign_major
        return "hiperboloide de una hoja" if rhs > 0 else "hiperboloide de dos hojas"
    if r == 2:
        if lin:
            if same:
                return "paraboloide de revolución" if equal else "paraboloide elíptico"
            return "paraboloide hiperbólico"
        if czero:
            return "recta" if same else "par de planos que se cortan"
        if same:
            if (c > 0) == (pos == 2):
                return "vacía (sin puntos reales)"
            return "cilindro" if equal else "cilindro elíptico"
        return "cilindro hiperbólico"
    # r == 1
    if lin:
        return "cilindro parabólico"
    if czero:
        return "plano (doble)"
    if (c > 0) == (pos == 1):
        return "vacía (sin puntos reales)"
    return "par de planos paralelos"


def surface_kind(el: SurfaceElement, parinp=None) -> str:
    """«cilindro», «cono (implícita)»... para mostrar."""
    if el.implicit:
        return classify(implicit_quadric(el, parinp)) + " (implícita)"
    return reduced_name(el.indices)


# ---------------------------------------------------------------------------
# Transformar una superficie (copias de cuerpos)
# ---------------------------------------------------------------------------

def transform_surface(el: SurfaceElement, rot: np.ndarray, shift: np.ndarray, parinp=None) -> list[str]:
    """Aplica x → rot·x + shift a una superficie (en su propio sistema); devuelve avisos.

    Forma reducida: se componen su giro y su desplazamiento. Forma implícita:
    se compone (o se añade) la transformación tras la línea de unos. Un valor
    fijado por PARINP que tenga que cambiar se escribe como número (se avisa).
    """
    notes: list[str] = []
    rot = np.asarray(rot, dtype=float)
    shift = np.asarray(shift, dtype=float)
    if el.implicit:
        params = el.transform if el.has_ones_line else []
    else:
        params = [p for p in el.params if p.name in TRANSFORM_ORDER]
    vals = dict.fromkeys(TRANSFORM_ORDER, 0.0)
    for p in params:
        v = effective_value(p, parinp)
        vals[p.name] = (v if p.unit == "RAD" else math.radians(v)) if p.is_angle else v
    from .quadric import rotation_matrix

    r0 = np.array(rotation_matrix(vals["OMEGA"], vals["THETA"], vals["PHI"]), dtype=float)
    d0 = np.array([vals["X-SHIFT"], vals["Y-SHIFT"], vals["Z-SHIFT"]], dtype=float)
    r1 = rot @ r0
    d1 = rot @ d0 + shift
    new = {}
    if not np.allclose(rot, np.eye(3), atol=1e-14):
        om, th, ph = euler_angles(r1)
        new.update({"OMEGA": _clean(om), "THETA": _clean(th), "PHI": _clean(ph)})
    for i, n in enumerate(("X-SHIFT", "Y-SHIFT", "Z-SHIFT")):
        if abs(d1[i] - d0[i]) > 1e-12 * max(1.0, abs(d0[i])):
            new[n] = _clean(float(d1[i]))
    current = {p.name: p for p in params}
    for n in new:
        p = current.get(n)
        if p is not None and p.ipar > 0:
            notes.append(f"{n} de la superficie «{_lab(el.label)}» usaba PARINP({p.ipar}); en la copia es un número.")
            p.ipar = 0
    if el.implicit:
        el.transform = [p for p in _set_params(el.transform if el.has_ones_line else [], new, TRANSFORM_ORDER)
                        if p.value != 0.0 or p.ipar]
        el.has_ones_line = bool(el.transform)
    else:
        el.params = [p for p in _set_params(el.params, new, ("X-SCALE", "Y-SCALE", "Z-SCALE", *TRANSFORM_ORDER))
                     if p.value != 0.0 or p.ipar or p.name.endswith("SCALE")]
    el.modified = True
    return notes
