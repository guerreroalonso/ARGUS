"""Edición de geometrías PENGEOM (F6): cambiar, añadir y quitar cuerpos respetando las reglas de GEOMIN.

Reglas de PENGEOM que se respetan (manual de PENELOPE, cap. 6, y GEOMIN):

* un cuerpo limitante o una hija tienen que estar definidos **antes** del
  cuerpo o módulo que los nombra;
* un cuerpo nuevo **dentro de un cuerpo** C se define antes de C y C lo
  nombra como cuerpo limitante (``BODY (N)``): C deja de ocupar ese volumen.
  Si C es hija de un módulo M, N también se declara hija de M (si no, LOCATE
  no lo encontraría);
* un cuerpo nuevo **dentro de un módulo** M (en la parte que no ocupan sus
  hijas) se declara hija de M;
* un cuerpo en la envolvente automática no necesita referencias;
* un **módulo** nuevo sigue las mismas reglas con ``MODULE (N)``: dentro de
  un cuerpo C, C lo nombra como módulo limitante (y el módulo madre de C,
  como hija); dentro de un módulo, es hija suya (comprobado con PENGEOM
  real: si el módulo madre de C no lo declara, LOCATE no lo encuentra);
* los cuerpos no pueden solaparse: antes de añadir se comprueba con LOCATE
  que todo el volumen nuevo está dentro del contenedor elegido;
* **convertir un cuerpo en módulo**: sus cuerpos limitantes (y los de estos)
  pasan a ser hijas del módulo y dejan de estarlo del módulo madre (PENGEOM
  no admite dos madres); quien lo nombraba con ``BODY`` pasa a ``MODULE``;
* **mover un módulo** es cambiar su desplazamiento (en el sistema de su
  módulo madre); para pegarlo a otro elemento se usan las cajas exactas de
  los dos (``extent``).

Solo se edita el fichero principal: los elementos de ficheros INCLUDE y los
clones se muestran pero no se cambian desde aquí.

Todas las operaciones modifican el ``GeoDocument`` en su sitio (los
elementos cambiados quedan con ``modified = True`` y el escritor conserva
el resto del texto byte a byte) y comprueban que la geometría resultante
compila; si no, lanzan ``EditError`` y el llamante deshace el cambio.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from .compiler import CompiledGeometry, body_title, compile_geometry
from .model import (
    BodyElement,
    CloneElement,
    Element,
    ElementKind,
    EndElement,
    GeoDocument,
    IncludeElement,
    ModuleElement,
    ParamLine,
    RefLine,
    SurfaceElement,
)
from .quadric import rotation_matrix

PRIMITIVES = {
    "sphere": "Esfera",
    "cylinder": "Cilindro",
    "box": "Caja",
    "prism3": "Prisma triangular",
    "prism4": "Prisma cuadrado",
    "prism5": "Prisma pentagonal",
    "prism6": "Prisma hexagonal",
    "slab": "Lámina",
}
# Prismas rectos de base un polígono regular (formas habituales de los detectores): número de lados.
PRISM_SIDES = {"prism3": 3, "prism4": 4, "prism5": 5, "prism6": 6}
# Un módulo tiene que ser cerrado (la lámina es infinita).
MODULE_KINDS = ("sphere", "cylinder", "box", "prism3", "prism4", "prism5", "prism6")
# Medidas con que se puede dar el tamaño de un polígono regular.
PRISM_MEASURES = ("side", "flats", "corners")


def prism_radius(n: int, measure: str, value: float) -> float:
    """Radio circunscrito de un polígono regular de ``n`` lados a partir de una medida: el lado (``side``), la
    distancia entre caras opuestas o, con un número impar de lados, del vértice a la cara opuesta (``flats``), o el
    diámetro de la circunferencia circunscrita (``corners``)."""
    if measure == "side":
        return float(value) / (2.0 * math.sin(math.pi / n))
    if measure == "flats":
        c = math.cos(math.pi / n)
        return float(value) / (2.0 * c) if n % 2 == 0 else float(value) / (1.0 + c)
    if measure == "corners":
        return 0.5 * float(value)
    raise ValueError(f"Medida desconocida: {measure!r}.")


def prism_measure(n: int, measure: str, radius: float) -> float:
    """La medida ``measure`` de un polígono regular de ``n`` lados y radio circunscrito ``radius``."""
    return float(radius) / prism_radius(n, measure, 1.0)


def prism_measure_name(n: int, measure: str) -> str:
    return {"side": "Lado", "corners": "Diámetro circunscrito",
            "flats": "Entre caras" if n % 2 == 0 else "Altura (vértice a cara)"}[measure]
FACES = {"+x": (0, 1), "-x": (0, -1), "+y": (1, 1), "-y": (1, -1), "+z": (2, 1), "-z": (2, -1)}
FACE_NAMES = {"+x": "+x", "-x": "−x", "+y": "+y", "-y": "−y", "+z": "+z (encima)", "-z": "−z (debajo)"}
AXES = ("x", "y", "z")
REDUCED = ("X-SCALE", "Y-SCALE", "Z-SCALE", "OMEGA", "THETA", "PHI", "X-SHIFT", "Y-SHIFT", "Z-SHIFT")
DEFAULTS = {"X-SCALE": 1.0, "Y-SCALE": 1.0, "Z-SCALE": 1.0, "OMEGA": 0.0, "THETA": 0.0, "PHI": 0.0,
            "X-SHIFT": 0.0, "Y-SHIFT": 0.0, "Z-SHIFT": 0.0}


class EditError(Exception):
    """Cambio que PENGEOM no aceptaría (o que daría una geometría incorrecta)."""


# ---------------------------------------------------------------------------
# Búsqueda y etiquetas
# ---------------------------------------------------------------------------

def _lab(label: str) -> str:
    """La etiqueta sin los blancos, para los mensajes (para compararlas, ``label_key``)."""
    return (label or "").strip()


def label_key(label: str | None) -> str:
    """Una etiqueta tal como la compara PENGEOM: sus cuatro columnas («   1» y «1   » son etiquetas distintas). La que
    se da con menos caracteres (p. ej. escrita sin los blancos) se alinea a la derecha, como las escribe ARGUS (y
    como las busca ``CompiledGeometry.find_body``)."""
    return (label or "").rjust(4)[:4]


def same_label(a: str | None, b: str | None) -> bool:
    """¿Es la misma etiqueta para PENGEOM? (con sus cuatro columnas: véase ``label_key``)."""
    return label_key(a) == label_key(b)


def find_element(doc: GeoDocument, kind: str, label: str) -> Element | None:
    """Elemento del fichero principal: ``kind`` 'surface' o 'body' (cuerpos y módulos), con la etiqueta ``label``
    (con sus cuatro columnas, como PENGEOM: «1   » no es «   1»)."""
    lab = label_key(label)
    for el in doc.elements:
        if kind == "surface" and isinstance(el, SurfaceElement) and label_key(el.label) == lab:
            return el
        if kind == "body" and isinstance(el, BodyElement | ModuleElement) and label_key(el.label) == lab:
            return el
    return None


def shared_body_element(doc: GeoDocument, label: str) -> Element | None:
    """El cuerpo o módulo ``label`` tal como lo nombra el fichero principal: el suyo o el de un fichero incluido con
    INCLUDE* (comparte las etiquetas con el principal; los de un INCLUDE sin «*» llevan otro sufijo y desde el
    principal no se pueden nombrar)."""
    el = find_element(doc, "body", label)
    if el is not None:
        return el
    lab = label_key(label)
    for inc in doc.elements:
        if isinstance(inc, IncludeElement) and inc.starred and inc.document is not None:
            for sel in inc.document.elements:
                if isinstance(sel, BodyElement | ModuleElement) and label_key(sel.label) == lab:
                    return sel
    return None


_NUMERIC_LABEL = re.compile(r"[0-9]+")
MAX_LABEL = 9999


class LabelPool:
    """Etiquetas numéricas libres (4 caracteres) para superficies o cuerpos, de una en una: primero las que siguen
    a la mayor y, cuando se acaban (9999), las libres por debajo."""

    def __init__(self, doc: GeoDocument, kind: str, geo: CompiledGeometry | None = None,
                 taken: set[str] | None = None):
        used = {_lab(t) for t in (taken or ())}
        for el in doc.elements:
            if kind == "surface" and isinstance(el, SurfaceElement):
                used.add(_lab(el.label))
            elif kind == "body" and el.kind in (ElementKind.BODY, ElementKind.MODULE, ElementKind.CLONE,
                                                ElementKind.CTBOX):
                used.add(_lab(el.label))
        if geo is not None:  # también las de los INCLUDE* (que conservan sus etiquetas)
            items = geo.surfaces if kind == "surface" else geo.bodies
            used.update(_lab(x.alias[:4]) for x in items)
        self.used = used
        self.top = max((int(u) for u in used if _NUMERIC_LABEL.fullmatch(u)), default=0)
        self.low = 0  # hasta dónde se han buscado huecos por debajo de la mayor

    def free(self) -> int:
        """Cuántas quedan (hasta 9999)."""
        return MAX_LABEL - sum(1 for u in self.used if _NUMERIC_LABEL.fullmatch(u) and 1 <= int(u) <= MAX_LABEL
                               and str(int(u)) == u)

    def next(self) -> str:
        n = self.top + 1
        while n <= MAX_LABEL and str(n) in self.used:
            n += 1
        if n <= MAX_LABEL:
            self.top = n
        else:
            n = self.low + 1
            while n <= MAX_LABEL and str(n) in self.used:
                n += 1
            if n > MAX_LABEL:
                raise EditError(f"No quedan etiquetas numéricas libres (máximo {MAX_LABEL}).")
            self.low = n
        self.used.add(str(n))
        return f"{n:4d}"


def new_label(doc: GeoDocument, kind: str, geo: CompiledGeometry | None = None, taken: set[str] | None = None
              ) -> str:
    """Primera etiqueta numérica libre (4 caracteres) para superficies o cuerpos (la que sigue a la mayor o, si
    ya se ha llegado a 9999, la primera libre)."""
    return LabelPool(doc, kind, geo, taken).next()


def editable_body(geo: CompiledGeometry, kb: int) -> Element:
    """Elemento del fichero principal de un cuerpo o módulo, o EditError si no se puede editar."""
    if not 1 <= kb <= geo.nbodys:
        raise EditError("No hay ningún cuerpo seleccionado.")
    b = geo.body(kb)
    if b.origin == "enclosure":
        raise EditError("La envolvente la añade PENGEOM automáticamente: no es un elemento del fichero.")
    if b.origin == "clone":
        raise EditError("Es un clon (CLONE): se edita el módulo original.")
    if b.origin == "ct":
        raise EditError("Es la caja de vóxeles del TAC (CTBOX): no se puede editar; para cambiarla, elimínela y "
                        "vuelva a importar el TAC.")
    if b.document is not geo.document or b.element is None:
        raise EditError("Está definido en un fichero INCLUDE: ábralo para editarlo.")
    return b.element


def body_users(doc: GeoDocument, label: str) -> list[Element]:
    """Cuerpos y módulos que nombran a ``label`` (como cuerpo limitante o como hija; la etiqueta con sus cuatro
    columnas)."""
    lab = label_key(label)
    out = []
    for el in doc.elements:
        if isinstance(el, BodyElement | ModuleElement):
            if any(not r.is_surface and label_key(r.label) == lab for r in el.items):
                out.append(el)
    return out


def surface_users(doc: GeoDocument, label: str) -> list[Element]:
    """Cuerpos y módulos limitados por la superficie ``label`` (con sus cuatro columnas)."""
    lab = label_key(label)
    return [el for el in doc.elements if isinstance(el, BodyElement | ModuleElement)
            and any(r.is_surface and label_key(r.label) == lab for r in el.items)]


def clone_users(doc: GeoDocument, label: str) -> list[CloneElement]:
    """Clones que copian el módulo ``label`` (con sus cuatro columnas)."""
    return [e for e in doc.elements if isinstance(e, CloneElement) and same_label(e.module_label, label)]


# ---------------------------------------------------------------------------
# Cambios sencillos
# ---------------------------------------------------------------------------

# La descripción va, tras tres blancos, de la columna 15 a la 70 del .geo (PENGEOM solo lee hasta la 14; ARGUS la
# vuelve a leer de ahí): más larga, se perdería el final al abrir el fichero otra vez.
MAX_DESCRIPTION = 53


def set_description(el: Element, text: str) -> None:
    text = " ".join(text.split())[:MAX_DESCRIPTION]
    el.description = ("   " + text) if text else ""
    el.modified = True


MAX_BODY_MATERIAL = 9998  # 9999 es la marca de la caja CT de PENGEOM


def check_material(material: int) -> None:
    if material == 9999:
        raise EditError("El material 9999 es la marca de la caja CT de PENGEOM: no se puede usar en un cuerpo o "
                        "módulo.")
    if material < 0 or material > MAX_BODY_MATERIAL:
        raise EditError(f"El material tiene que estar entre 0 (vacío) y {MAX_BODY_MATERIAL}.")


def set_material(el: Element, material: int) -> None:
    if not isinstance(el, BodyElement | ModuleElement):
        raise EditError("Solo los cuerpos y los módulos tienen material.")
    check_material(material)
    el.material = int(material)
    el.modified = True


def _set_params(params: list[ParamLine], values: dict[str, float], order: tuple[str, ...]) -> list[ParamLine]:
    """Sustituye o añade parámetros (ángulos en grados) en el orden de PENGEOM; no toca los de PARINP."""
    current = {p.name: p for p in params}
    for name, v in values.items():
        v = float(v)
        p = current.get(name)
        if p is not None and p.ipar > 0:
            raise EditError(f"{name} lo fija el .in (PARINP {p.ipar}): cámbielo allí.")
        if not math.isfinite(v):
            raise EditError(f"Valor no válido para {name}.")
        if name in ("X-SCALE", "Y-SCALE", "Z-SCALE") and not v >= 1e-15:
            raise EditError(f"{name} tiene que ser mayor que 1e-15.")
        if p is None:
            current[name] = ParamLine(name, v)
        else:
            p.value = v
            if p.is_angle:
                p.unit = "DEG"
    return [current[n] for n in order if n in current]


def set_surface_params(el: SurfaceElement, values: dict[str, float]) -> None:
    """Parámetros de una superficie en forma reducida (escalas, ángulos en grados, desplazamientos)."""
    if el.implicit:
        raise EditError("Superficie en forma implícita: se editan sus coeficientes.")
    el.params = _set_params(el.params, values, REDUCED)
    el.modified = True


def set_module_transform(el: ModuleElement, values: dict[str, float]) -> None:
    """Giro (grados) y desplazamiento de un módulo (se mueven con él sus hijas)."""
    order = ("OMEGA", "THETA", "PHI", "X-SHIFT", "Y-SHIFT", "Z-SHIFT")
    el.transform = _set_params(el.transform, values, order)
    el.has_ones_line = bool(el.transform) or el.has_ones_line
    el.modified = True


def effective_value(p: ParamLine, parinp=None) -> float:
    """Valor de una línea tal como lo usa GEOMIN (el de PARINP si lo tiene), en las unidades de la línea."""
    return float(parinp[p.ipar - 1]) if p.ipar > 0 and parinp and p.ipar <= len(parinp) else p.value


def reduced_values(el: SurfaceElement, parinp=None) -> dict[str, float]:
    """Valores (ángulos en grados) de una superficie en forma reducida; con ``parinp``, los efectivos."""
    out = dict(DEFAULTS)
    for p in el.params:
        if p.name in out:
            v = effective_value(p, parinp)
            out[p.name] = math.degrees(v) if p.is_angle and p.unit == "RAD" else v
    return out


# ---------------------------------------------------------------------------
# Sistema de referencia de los módulos
# ---------------------------------------------------------------------------

def _param_value(p: ParamLine, parinp) -> float:
    v = effective_value(p, parinp)
    if p.is_angle:
        return v if p.unit == "RAD" else math.radians(v)
    return v


def module_transform(el: ModuleElement, parinp=None) -> tuple[np.ndarray, np.ndarray]:
    """(R, d) de la transformación de un módulo: un punto de sus hijas va a R·x + d (como ROTSHF)."""
    vals = {"OMEGA": 0.0, "THETA": 0.0, "PHI": 0.0, "X-SHIFT": 0.0, "Y-SHIFT": 0.0, "Z-SHIFT": 0.0}
    if el.has_ones_line:
        for p in el.transform:
            if p.name in vals:
                vals[p.name] = _param_value(p, parinp)
    r = np.array(rotation_matrix(vals["OMEGA"], vals["THETA"], vals["PHI"]), dtype=float)
    return r, np.array([vals["X-SHIFT"], vals["Y-SHIFT"], vals["Z-SHIFT"]], dtype=float)


def local_frame(geo: CompiledGeometry, container: int, parinp=None) -> tuple[np.ndarray, np.ndarray]:
    """Transformación (R, d) que PENGEOM aplicará a las superficies de un cuerpo nuevo en ``container``.

    Las superficies de las hijas de un módulo con giro o desplazamiento se
    escriben en el sistema del módulo: hay que dar las coordenadas locales
    x_l = Rᵀ(x - d) para que el cuerpo quede donde se ha pedido.
    """
    rot = np.eye(3)
    shift = np.zeros(3)
    if not 1 <= container <= geo.nbodys:
        return rot, shift
    if parinp is None:  # los PARINP con los que se compiló la geometría (los del .in)
        parinp = getattr(geo, "parinp", None) or None
    b = geo.body(container)
    kb = container if b.is_module else b.mother
    while kb:
        m = geo.body(kb)
        if m.is_module and isinstance(m.element, ModuleElement):
            r_i, d_i = module_transform(m.element, parinp)
            rot = r_i @ rot
            shift = r_i @ shift + d_i
        kb = m.mother
    return rot, shift


def euler_angles(m: np.ndarray) -> tuple[float, float, float]:
    """(OMEGA, THETA, PHI) en grados tales que rotation_matrix(ω, θ, φ) = m (convención de PENGEOM)."""
    m = np.asarray(m, dtype=float)
    # sin θ a partir de la tercera columna (no con acos de m33: cerca de 1 amplifica el ruido de redondeo y
    # daría ω y φ sin sentido para giros que son casi la identidad).
    s = math.hypot(m[0, 2], m[1, 2])
    th = math.atan2(s, m[2, 2])
    if s > 1e-12:
        ph = math.atan2(m[1, 2], m[0, 2])
        om = math.atan2(m[2, 1], -m[2, 0])
    else:
        om = 0.0
        ph = math.atan2(m[1, 0], m[0, 0]) if m[2, 2] > 0 else math.atan2(-m[0, 1], -m[0, 0])
    return math.degrees(om), math.degrees(th), math.degrees(ph)


def _direction_angles(a: np.ndarray) -> dict[str, float]:
    """THETA y PHI (grados) que llevan el eje z local a la dirección ``a``."""
    a = np.asarray(a, dtype=float) / np.linalg.norm(a)
    th = math.degrees(math.acos(max(-1.0, min(1.0, float(a[2])))))
    ph = math.degrees(math.atan2(float(a[1]), float(a[0]))) if th > 1e-9 else 0.0
    out = {}
    if abs(th) > 1e-12:
        out["THETA"] = _clean(th)
    if abs(ph) > 1e-12:
        out["PHI"] = _clean(ph)
    return out


def _clean(v: float, digits: int = 12) -> float:
    """Quita el ruido de redondeo (89.99999999999999 -> 90)."""
    r = round(v, digits)
    return 0.0 if r == 0 else r


# ---------------------------------------------------------------------------
# Primitivas
# ---------------------------------------------------------------------------

@dataclass
class Primitive:
    """Cuerpo sencillo que se añade a la geometría (medidas en cm).

    Los prismas (``prism3`` a ``prism6``) son rectos, de base un polígono
    regular: ``radius`` es el radio de la circunferencia circunscrita (de los
    vértices), ``height`` la altura a lo largo de ``axis`` y ``angle`` el giro
    (grados, sentido positivo alrededor del eje) desde la posición de partida,
    con un lado abajo: la cara de normal −v, siendo (u, v) = (x, y) con el
    eje z, (y, z) con el eje x y (z, x) con el eje y.
    """

    kind: str  # 'sphere', 'cylinder', 'box', 'prism3'…'prism6' o 'slab'
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 1.0  # esfera, cilindro y prismas (circunscrito)
    height: float = 1.0  # cilindro y prismas (a lo largo del eje) y lámina (espesor)
    sides: tuple[float, float, float] = (1.0, 1.0, 1.0)  # caja (lados completos)
    axis: str = "z"  # cilindro, prismas y lámina
    angle: float = 0.0  # prismas: giro alrededor del eje (grados)

    def validate(self) -> None:
        if self.kind not in PRIMITIVES:
            raise EditError(f"Tipo de cuerpo desconocido: {self.kind}.")
        radial = ("sphere", "cylinder", *PRISM_SIDES)
        if self.kind in radial and not (math.isfinite(self.radius) and self.radius > 0):
            raise EditError("El radio tiene que ser positivo." if self.kind in ("sphere", "cylinder") else
                            "El tamaño del polígono tiene que ser positivo.")
        if self.kind in ("cylinder", "slab", *PRISM_SIDES) and not (math.isfinite(self.height) and self.height > 0):
            raise EditError("La altura (o el espesor) tiene que ser positiva.")
        if self.kind == "box" and not all(math.isfinite(s) and s > 0 for s in self.sides):
            raise EditError("Los lados de la caja tienen que ser positivos.")
        if self.axis not in AXES:
            raise EditError("El eje tiene que ser x, y o z.")
        if not all(math.isfinite(c) for c in self.center):
            raise EditError("Centro no válido.")
        if not math.isfinite(self.angle):
            raise EditError("Giro no válido.")

    # -- prismas ---------------------------------------------------------------------------
    @property
    def nsides(self) -> int:
        """Lados del polígono de un prisma (0 si no lo es)."""
        return PRISM_SIDES.get(self.kind, 0)

    @property
    def is_prism(self) -> bool:
        return self.kind in PRISM_SIDES

    def plane_axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(a, u, v): el eje y dos direcciones perpendiculares a él (u × v = a), en el sistema del laboratorio."""
        k = "xyz".index(self.axis)
        a, u, v = np.zeros(3), np.zeros(3), np.zeros(3)
        a[k], u[(k + 1) % 3], v[(k + 2) % 3] = 1.0, 1.0, 1.0
        return a, u, v

    @property
    def apothem(self) -> float:
        """Distancia del centro a cada cara lateral de un prisma."""
        return float(self.radius) * math.cos(math.pi / self.nsides)

    @property
    def side(self) -> float:
        """Lado del polígono de un prisma."""
        return 2.0 * float(self.radius) * math.sin(math.pi / self.nsides)

    def face_angles(self) -> list[float]:
        """Ángulos (grados, desde u hacia v) de las normales de las caras laterales de un prisma."""
        n = self.nsides
        turn = math.fmod(float(self.angle), 360.0)  # exacto: un giro enorme no deforma el polígono
        return [270.0 + turn + 360.0 * j / n for j in range(n)]

    def face_normals(self) -> list[np.ndarray]:
        _a, u, v = self.plane_axes()
        return [math.cos(math.radians(t)) * u + math.sin(math.radians(t)) * v for t in self.face_angles()]

    def vertices_2d(self) -> np.ndarray:
        """Vértices del polígono (n × 2) en (u, v), respecto al centro."""
        r = float(self.radius)
        ang = np.radians(np.array(self.face_angles()) + 180.0 / self.nsides)
        return np.stack([r * np.cos(ang), r * np.sin(ang)], axis=1)

    @property
    def axis_vector(self) -> np.ndarray:
        v = np.zeros(3)
        v["xyz".index(self.axis)] = 1.0
        return v

    def half_extent(self, axis: int) -> float:
        """Distancia del centro a la cara del cuerpo a lo largo del eje ``axis`` (0, 1, 2), la mayor de los dos
        sentidos (un prisma de lados impares no es simétrico: véase :meth:`reach`)."""
        if self.is_prism:
            return max(self.reach(axis, +1), self.reach(axis, -1))
        return self.reach(axis, +1)

    def reach(self, axis: int, sign: int = +1) -> float:
        """Hasta dónde llega el cuerpo desde su centro en el sentido ``sign`` (+1 o −1) del eje ``axis``."""
        if self.kind == "sphere":
            return float(self.radius)
        if self.is_prism:
            a, u, v = self.plane_axes()
            if a[axis]:
                return 0.5 * float(self.height)
            vert = self.vertices_2d()
            return float(np.max(sign * (vert[:, 0] * u[axis] + vert[:, 1] * v[axis])))
        if self.kind == "box":
            return 0.5 * float(self.sides[axis])
        if self.kind == "cylinder":
            return 0.5 * float(self.height) if axis == "xyz".index(self.axis) else float(self.radius)
        raise EditError("Una lámina no tiene caras que pegar: es la parte de un cuerpo entre dos planos.")

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """La caja que lo contiene justo (la lámina, infinita)."""
        c = np.asarray(self.center, dtype=float)
        if self.kind == "slab":
            k = "xyz".index(self.axis)
            h = np.full(3, np.inf)
            h[k] = 0.5 * float(self.height)
            return c - h, c + h
        lo = np.array([self.reach(i, -1) for i in range(3)])
        hi = np.array([self.reach(i, +1) for i in range(3)])
        return c - lo, c + hi

    def describe(self) -> str:
        c = ", ".join(f"{v:g}" for v in self.center)
        if self.kind == "sphere":
            return f"Esfera r={self.radius:g} en ({c})"
        if self.kind == "cylinder":
            return f"Cilindro r={self.radius:g} h={self.height:g} eje {self.axis} en ({c})"
        if self.kind == "box":
            return f"Caja {self.sides[0]:g}x{self.sides[1]:g}x{self.sides[2]:g} en ({c})"
        if self.is_prism:
            turn = f", girado {_clean(float(self.angle), 9):g}°" if _clean(float(self.angle) % 360.0, 9) else ""
            return (f"{PRIMITIVES[self.kind]} lado={_clean(self.side, 9):g} h={self.height:g} eje {self.axis}{turn} "
                    f"en ({c})")
        k = "xyz".index(self.axis)
        return f"Lámina de {self.height:g} cm en {self.axis}={self.center[k]:g}"

    # -- superficies (forma reducida de PENGEOM) ---------------------------------------
    def surfaces(self, frame: tuple[np.ndarray, np.ndarray] | None = None
                 ) -> list[tuple[tuple[int, int, int, int, int], dict[str, float], int, str]]:
        """(INDICES, parámetros, puntero de lado del cuerpo, descripción) de cada superficie.

        ``frame`` = (R, d): transformación de los módulos que contienen el
        cuerpo (véase ``local_frame``); las superficies se escriben en su
        sistema local para que, tras la transformación, queden en su sitio.
        """
        rot, shift = frame if frame is not None else (np.eye(3), np.zeros(3))
        identity = np.allclose(rot, np.eye(3), atol=1e-14)
        c_glob = np.array(self.center, dtype=float)
        c = rot.T @ (c_glob - shift)
        cx, cy, cz = (_clean(float(v)) for v in c)
        pos = {"X-SHIFT": cx, "Y-SHIFT": cy, "Z-SHIFT": cz}
        k = "xyz".index(self.axis)
        axis_local = rot.T @ self.axis_vector
        if self.kind == "sphere":
            r = float(self.radius)
            return [((1, 1, 1, 0, -1), {"X-SCALE": r, "Y-SCALE": r, "Z-SCALE": r, **pos}, -1, f"esfera r={r:g}")]
        if self.kind == "box":
            a, b, cc = (0.5 * float(v) for v in self.sides)
            ang = {}
            if not identity:
                om, th, ph = euler_angles(rot.T)
                ang = {n: _clean(v) for n, v in (("OMEGA", om), ("THETA", th), ("PHI", ph)) if abs(v) > 1e-12}
            g = c_glob
            return [((1, 0, 0, 0, -1), {"X-SCALE": a, **ang, **pos}, -1, f"planos x = {g[0]:g} ± {a:g}"),
                    ((0, 1, 0, 0, -1), {"Y-SCALE": b, **ang, **pos}, -1, f"planos y = {g[1]:g} ± {b:g}"),
                    ((0, 0, 1, 0, -1), {"Z-SCALE": cc, **ang, **pos}, -1, f"planos z = {g[2]:g} ± {cc:g}")]
        if self.is_prism:
            return self._prism_surfaces(rot, c, c_glob)
        h = 0.5 * float(self.height)
        if self.kind == "slab":
            desc = f"planos {self.axis} = {c_glob[k]:g} ± {h:g}"
            if identity:
                idx = [0, 0, 0, 0, -1]
                idx[k] = 1
                scale = {("X-SCALE", "Y-SCALE", "Z-SCALE")[k]: h}
                return [(tuple(idx), {**scale, **pos}, -1, desc)]
            return [((0, 0, 1, 0, -1), {"Z-SCALE": h, **_direction_angles(axis_local), **pos}, -1, desc)]
        r = float(self.radius)
        rot_ang = _direction_angles(axis_local)
        lo = c - h * axis_local
        hi = c + h * axis_local
        glo = c_glob - h * self.axis_vector
        ghi = c_glob + h * self.axis_vector

        def at(p):
            return {"X-SHIFT": _clean(float(p[0])), "Y-SHIFT": _clean(float(p[1])), "Z-SHIFT": _clean(float(p[2]))}

        return [((1, 1, 0, 0, -1), {"X-SCALE": r, "Y-SCALE": r, **rot_ang, **pos}, -1, f"cilindro r={r:g}"),
                ((0, 0, 0, 1, 0), {**rot_ang, **at(lo)}, +1, f"base del cilindro ({self.axis} = {glo[k]:g})"),
                ((0, 0, 0, 1, 0), {**rot_ang, **at(hi)}, -1, f"tapa del cilindro ({self.axis} = {ghi[k]:g})")]

    def _prism_surfaces(self, rot: np.ndarray, c_local: np.ndarray, c_glob: np.ndarray):
        """Superficies de un prisma: las caras laterales (con un número par de lados, un par de planos paralelos
        por cada dos caras opuestas) y las dos bases (un par de planos), en el sistema local ``rot``."""
        a, _u, _v = self.plane_axes()
        a_l = rot.T @ a
        r = self.apothem
        n = self.nsides
        k = "xyz".index(self.axis)
        h = 0.5 * float(self.height)
        pos = {"X-SHIFT": _clean(float(c_local[0])), "Y-SHIFT": _clean(float(c_local[1])),
               "Z-SHIFT": _clean(float(c_local[2]))}
        out = []
        normals = self.face_normals()
        angles = self.face_angles()
        if n % 2 == 0:
            for j in range(n // 2):
                n_l = rot.T @ normals[j]
                m = np.column_stack([n_l, np.cross(a_l, n_l), a_l])
                out.append(((1, 0, 0, 0, -1), {"X-SCALE": _clean(r), **_euler_params(m), **pos}, -1,
                            f"caras laterales a ±{_clean(r, 9):g} (normal a {_clean(angles[j] % 180.0, 9):g}°)"))
        else:
            for j in range(n):
                n_l = rot.T @ normals[j]
                q = c_local + r * n_l
                m = np.column_stack([a_l, np.cross(n_l, a_l), n_l])
                out.append(((0, 0, 0, 1, 0), {**_euler_params(m), "X-SHIFT": _clean(float(q[0])),
                                               "Y-SHIFT": _clean(float(q[1])), "Z-SHIFT": _clean(float(q[2]))},
                            -1, f"cara lateral (normal a {_clean(angles[j] % 360.0, 9):g}°)"))
        out.append(((0, 0, 1, 0, -1), {"Z-SCALE": _clean(h), **_direction_angles(a_l), **pos}, -1,
                    f"bases {self.axis} = {c_glob[k]:g} ± {h:g}"))
        return out

    def contains(self, pts: np.ndarray, tol: float = 0.0) -> np.ndarray:
        """¿Están los puntos dentro (con una holgura ``tol`` en cm)? La lámina no está acotada."""
        p = np.asarray(pts, dtype=float) - np.asarray(self.center, dtype=float)
        if self.is_prism:
            a, _u, _v = self.plane_axes()
            inside = np.abs(p @ a) <= 0.5 * self.height + tol
            r = self.apothem
            for nrm in self.face_normals():
                inside &= p @ nrm <= r + tol
            return inside
        if self.kind == "sphere":
            return np.linalg.norm(p, axis=1) <= self.radius + tol
        if self.kind == "box":
            return np.all(np.abs(p) <= 0.5 * np.asarray(self.sides, dtype=float) + tol, axis=1)
        k = "xyz".index(self.axis)
        axial = np.abs(p[:, k]) <= 0.5 * self.height + tol
        if self.kind == "slab":
            return axial
        radial = np.linalg.norm(np.delete(p, k, axis=1), axis=1) <= self.radius + tol
        return axial & radial

    # -- muestreo del volumen ---------------------------------------------------------------
    def sample(self, n: int, rng: np.random.Generator, bounds=None) -> np.ndarray:
        """Puntos uniformes en el volumen (la lámina, dentro de ``bounds``)."""
        c = np.asarray(self.center, dtype=float)
        if self.is_prism:
            return self._prism_sample(n, rng, c)
        if self.kind == "sphere":
            d = rng.normal(size=(n, 3))
            d /= np.linalg.norm(d, axis=1)[:, None]
            r = self.radius * rng.random(n) ** (1 / 3)
            pts = c + d * r[:, None]
            # También puntos casi en la superficie (donde suelen estar los solapes).
            pts[: n // 5] = c + d[: n // 5] * self.radius * (1 - 1e-9)
            return pts
        if self.kind == "box":
            s = np.asarray(self.sides, dtype=float)
            u = rng.random((n, 3)) - 0.5
            m = n // 5
            # Puntos en las caras: una coordenada a ±0.5.
            k = rng.integers(0, 3, m)
            u[np.arange(m), k] = np.sign(u[np.arange(m), k]) * 0.5 * (1 - 1e-9)
            return c + u * s
        k = "xyz".index(self.axis)
        if self.kind == "cylinder":
            ang = 2 * np.pi * rng.random(n)
            r = self.radius * np.sqrt(rng.random(n))
            r[: n // 5] = self.radius * (1 - 1e-9)
            t = (rng.random(n) - 0.5) * self.height
            i, j = [a for a in range(3) if a != k]
            pts = np.tile(c, (n, 1))
            pts[:, i] += r * np.cos(ang)
            pts[:, j] += r * np.sin(ang)
            pts[:, k] += t
            return pts
        lo, hi = (np.asarray(b, dtype=float) for b in bounds)
        pts = lo + rng.random((n, 3)) * (hi - lo)
        pts[:, k] = c[k] + (rng.random(n) - 0.5) * self.height
        return pts


    def _prism_sample(self, n: int, rng: np.random.Generator, c: np.ndarray) -> np.ndarray:
        """Puntos uniformes en un prisma; una quinta parte, en sus caras (donde suelen estar los solapes)."""
        a, u, v = self.plane_axes()
        vert = self.vertices_2d()
        r = float(self.radius)
        nrm2 = np.array([[float(x @ u), float(x @ v)] for x in self.face_normals()])
        ap = self.apothem
        got: list[np.ndarray] = []
        need = n
        while need > 0:
            st = (rng.random((2 * need + 16, 2)) - 0.5) * (2.0 * r)
            ok = np.all(st @ nrm2.T <= ap, axis=1)
            got.append(st[ok][:need])
            need -= int(min(ok.sum(), need))
        st = np.concatenate(got)[:n]
        t = (rng.random(n) - 0.5) * float(self.height)
        m = n // 5
        if m:
            # La mitad en las caras laterales (un punto de un lado del polígono) y la otra mitad en las bases.
            k = m // 2
            j = rng.integers(0, self.nsides, k)
            w = rng.random(k)
            p0, p1 = vert[j], vert[(j + 1) % self.nsides]
            st[:k] = (p0 + (p1 - p0) * w[:, None]) * (1 - 1e-9)
            t[k:m] = np.sign(rng.random(m - k) - 0.5) * 0.5 * float(self.height) * (1 - 1e-9)
        return c + np.outer(st[:, 0], u) + np.outer(st[:, 1], v) + np.outer(t, a)


def _euler_params(m: np.ndarray) -> dict[str, float]:
    """OMEGA, THETA y PHI (grados, los que no son 0) de la matriz de giro ``m``."""
    om, th, ph = euler_angles(m)
    return {name: _clean(v) for name, v in (("OMEGA", om), ("THETA", th), ("PHI", ph)) if abs(_clean(v)) > 0}


@dataclass
class PlacementCheck:
    ok: bool
    message: str
    outside_fraction: float = 0.0
    offenders: dict[int, float] = field(default_factory=dict)  # cuerpo -> fracción del volumen


def is_editable(geo: CompiledGeometry, kb: int) -> bool:
    try:
        editable_body(geo, kb)
        return True
    except EditError:
        return False


def hosts(geo: CompiledGeometry, kb: int) -> list[int]:
    """Elementos que «alojan» a ``kb``: los cuerpos que lo tienen como limitante y su módulo madre."""
    out = [c.index for c in geo.bodies if c.index != kb and not c.is_module and kb in c.limiting]
    m = geo.body(kb).mother
    if m and m != kb and m not in out:
        out.append(m)
    return out


def logical_parent(geo: CompiledGeometry, kb: int) -> int:
    """Dónde está ``kb``: el cuerpo que lo excluye, su módulo madre o la envolvente (0 si es la raíz).

    Si varios cuerpos lo tienen como limitante (una lámina copia los
    limitantes de su contenedor), se elige el más pequeño cuya caja contiene
    la de ``kb``.
    """
    from .extent import analytic_box

    b = geo.body(kb)
    if b.origin == "enclosure":
        return 0
    cands = [c.index for c in geo.bodies if c.index != kb and not c.is_module and kb in c.limiting]
    if len(cands) > 1:
        lo, hi = analytic_box(geo, kb)
        best = None
        for c in cands:
            clo, chi = analytic_box(geo, c)
            fits = np.all(np.isfinite(lo) & np.isfinite(hi)) and np.all(clo <= lo + 1e-9 * np.abs(lo) + 1e-12) \
                and np.all(chi >= hi - 1e-9 * np.abs(hi) - 1e-12)
            if fits:
                with np.errstate(over="ignore"):  # sin cota: 1e300³ = inf, el más grande
                    vol = float(np.prod(np.where(np.isfinite(chi - clo), chi - clo, 1e300)))
                if best is None or vol < best[0]:
                    best = (vol, c)
        if best is not None:
            return best[1]
    if cands:
        return cands[-1]
    m = b.mother
    return m if m and m != kb else 0


def container_chain(geo: CompiledGeometry, kb: int | None) -> list[int]:
    """Contenedores posibles al pulsar en ``kb``: él mismo y los que lo contienen, hasta la envolvente.

    Solo los que se pueden editar (los clones, los INCLUDE y la caja CT se
    saltan) y la envolvente automática.
    """
    top = top_level(geo)
    start = kb if kb is not None and 1 <= kb <= geo.nbodys else top
    out: list[int] = []
    seen: set[int] = set()
    cur = start
    while cur and cur <= geo.nbodys and cur not in seen:
        seen.add(cur)
        if (geo.enclosure_added and cur == geo.nbodys) or is_editable(geo, cur):
            out.append(cur)
        cur = logical_parent(geo, cur)
    if not geo.enclosure_added:
        # Sin envolvente (un solo módulo arriba, p. ej. solo el CT): se puede añadir al lado, en el nivel
        # superior; GEOMIN añade entonces la envolvente.
        out.append(top)
    return out


def top_level(geo: CompiledGeometry) -> int:
    """Contenedor «nivel superior»: la envolvente automática o, si no la hay, NBODYS+1 (fuera de todo)."""
    return geo.nbodys if geo.enclosure_added else geo.nbodys + 1


def is_top_level(geo: CompiledGeometry, container: int) -> bool:
    return container > geo.nbodys or (geo.enclosure_added and container == geo.nbodys)


def children_of(geo: CompiledGeometry, container: int) -> list[int]:
    """Elementos que están directamente dentro de ``container`` (a los que se puede pegar algo nuevo)."""
    target = 0 if container > geo.nbodys else container  # nivel superior sin envolvente: los de arriba
    return [b.index for b in geo.bodies
            if b.origin != "enclosure" and b.index != container and logical_parent(geo, b.index) == target]


def element_name(geo: CompiledGeometry, kb: int, width: int = 36) -> str:
    """«Cuerpo «2» · Esfera Gigante (con hueco)»: el título y, si lo tiene, el comentario del .geo."""
    title = body_title(geo, kb)
    if kb > geo.nbodys:
        return title
    el = geo.body(kb).element
    desc = " ".join((el.description if el is not None else "").split())
    if desc:
        title += " · " + (desc[:width].rstrip() + "…" if len(desc) > width else desc)
    return title


def container_description(geo: CompiledGeometry, kb: int, module: bool = False) -> str:
    """«Módulo «2»: el nuevo será una hija suya», para el diálogo de añadir."""
    what = "el módulo nuevo" if module else "el cuerpo nuevo"
    if geo.enclosure_added and kb == geo.nbodys:
        return "la envolvente: " + what + " será de primer nivel, fuera de todos los demás"
    if kb > geo.nbodys:
        return ("el nivel superior: " + what + " irá al lado de lo que hay, fuera de todo (PENGEOM añadirá la "
                "envolvente)")
    b = geo.body(kb)
    name = body_title(geo, kb)
    if b.is_module:
        return f"{name}: {what} será una hija suya"
    return f"{name}: lo excluirá de su volumen ({'módulo' if module else 'cuerpo'} limitante)"


def container_of(geo: CompiledGeometry, kb: int | None) -> int:
    """Contenedor efectivo: el cuerpo o módulo pulsado, o el nivel superior si no hay ninguno."""
    if kb is None or kb <= 0 or kb > geo.nbodys:
        return top_level(geo)
    return kb


ENCLOSURE_RADIUS = 1.0e7  # esfera de la envolvente automática de GEOMIN (x² + y² + z² = 1e14)


def box_reach(lo, hi) -> float:
    """Distancia al origen del punto más lejano de la caja [lo, hi]."""
    lo, hi = (np.asarray(v, dtype=float) for v in (lo, hi))
    far = np.maximum(np.abs(lo), np.abs(hi))
    return float(np.linalg.norm(far))


def outside_enclosure(geo: CompiledGeometry, container: int, reach: float) -> PlacementCheck | None:
    """En el nivel superior de una geometría sin envolvente, GEOMIN la añade al haber dos elementos arriba: lo
    nuevo (hasta ``reach`` cm del origen) tiene que quedar dentro de su esfera; si no, PENGEOM lo recortaría."""
    if geo.enclosure_added or not is_top_level(geo, container) or (math.isfinite(reach) and
                                                                    reach < ENCLOSURE_RADIUS):
        return None
    return PlacementCheck(False, "Quedaría fuera de la envolvente que PENGEOM añadirá al ponerlo en el nivel superior "
                                 "(una esfera de 1e7 cm de radio alrededor del origen): acérquelo al origen.", 1.0)


def check_placement(geo: CompiledGeometry, locator, prim: Primitive, container: int, bounds=None,
                    n: int = 20000, seed: int = 11) -> PlacementCheck:
    """¿Cabe el cuerpo nuevo en el contenedor sin solaparse con nada? (LOCATE de puntos del volumen).

    Además de los puntos del cuerpo nuevo, se miran los elementos que ya hay
    cerca: ninguno puede quedar dentro de él (uno pequeño casi nunca recibe
    un punto de muestra).
    """
    prim.validate()
    rng = np.random.default_rng(seed)
    if prim.kind != "slab":
        reach = (float(np.linalg.norm(np.asarray(prim.center, dtype=float))) + float(prim.radius)
                 if prim.kind == "sphere" else box_reach(*prim.box()))
        far = outside_enclosure(geo, container, reach)
        if far is not None:
            return far
    pts = prim.sample(n, rng, bounds)
    kb, _ = locator.locate(pts)
    if prim.kind == "slab":
        # La lámina es la parte del contenedor entre los dos planos: no puede salirse.
        inside = kb == container
        if not inside.any():
            return PlacementCheck(False, "La lámina no corta al cuerpo elegido.", 1.0)
        return PlacementCheck(True, "La lámina ocupará la parte del cuerpo entre los dos planos.")
    inside = kb == container
    frac = 1.0 - float(inside.mean())
    if frac == 0.0:
        # Lo que el muestreo del volumen nuevo no ve: elementos pequeños dentro de él (se muestrea cada uno en su
        # caja) y cuerpos finos que lo cruzan (con rayos).
        met = enclosed_bodies(geo, locator, prim, {container}, rng)
        whole = [b for b, full in met.items() if full]
        if whole:
            names = ", ".join(body_title(geo, b) for b in whole[:3]) + (" y otros" if len(whole) > 3 else "")
            what = "está" if len(whole) == 1 else "están"
            return PlacementCheck(False, f"Rodearía a {names}, que ya {what} en la geometría: el cuerpo nuevo tiene "
                                         "que quedar en el espacio libre del contenedor (para meter en un módulo "
                                         "elementos que ya existen, use «Envolver»).", 0.0, {b: 0.0 for b in whole})
        thin = _thin_intruders(geo, prim, {container}, bounds)
        if thin:
            return thin
        if met:
            names = ", ".join(body_title(geo, b) for b in list(met)[:3])
            return PlacementCheck(False, f"Cortaría a {names}: el cuerpo nuevo tiene que quedar en el espacio libre "
                                         "del contenedor.", 0.0, {b: 0.0 for b in met})
        return PlacementCheck(True, "Cabe entero en el contenedor.")
    offenders: dict[int, float] = {}
    for b in kb[~inside]:
        offenders[int(b)] = offenders.get(int(b), 0.0) + 1.0 / n
    worst = sorted(offenders.items(), key=lambda kv: -kv[1])[:3]
    names = ", ".join(f"{body_title(geo, b) if b <= geo.nbodys else 'fuera de la envolvente'} "
                      f"({100 * f:.1f} %)" for b, f in worst)
    return PlacementCheck(False, f"Se sale del contenedor: {100 * frac:.1f} % del volumen está en {names}.",
                          frac, offenders)


def enclosed_bodies(geo: CompiledGeometry, locator, prim: Primitive, allowed: set[int], rng: np.random.Generator,
                    n: int = 600) -> dict[int, bool]:
    """Elementos (salvo ``allowed``) con puntos dentro de ``prim``: {elemento: ¿todos sus puntos dentro?}.

    Se muestrea cada uno en su caja ∩ la de ``prim``: un cuerpo pequeño dentro
    del nuevo (el maniquí dentro de un «mundo» nuevo) casi nunca recibe un
    punto de los del volumen nuevo.
    """
    from .extent import analytic_box

    lo, hi = prim.box()
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
        return {}
    size = float(np.max(hi - lo))
    tol = 1e-9 * max(size, float(np.max(np.abs(np.r_[lo, hi]))))
    found: dict[int, bool] = {}
    for b in geo.bodies:
        if b.index in allowed or b.origin == "enclosure":
            continue
        blo, bhi = analytic_box(geo, b.index)
        if np.any(bhi < blo):
            continue
        ilo, ihi = np.maximum(blo, lo), np.minimum(bhi, hi)
        if np.any(ihi <= ilo):
            continue
        pts = rng.uniform(ilo, ihi, (n, 3))
        ib, _ = locator.locate(pts)
        mine = pts[ib == b.index]
        if not len(mine):
            continue
        inside = prim.contains(mine, -tol)
        if inside.any():
            full = bool(inside.all()) and np.all(np.isfinite(blo)) and np.all(np.isfinite(bhi)) \
                and bool(np.all(prim.contains(_corners(blo, bhi), -tol)))
            found[b.index] = full
    return found


def _corners(lo, hi) -> np.ndarray:
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def _thin_intruders(geo: CompiledGeometry, prim: Primitive, allowed: set[int], bounds=None) -> PlacementCheck | None:
    """Lo que el muestreo no ve: cuerpos muy finos (láminas) que cortan la forma ``prim`` (con rayos)."""
    from .raycheck import intruders

    lo, hi = prim.box()
    if bounds is not None:
        b_lo, b_hi = (np.asarray(v, dtype=float) for v in bounds)
        lo = np.where(np.isfinite(lo), lo, b_lo)
        hi = np.where(np.isfinite(hi), hi, b_hi)
    tol = 1e-9 * float(np.max(np.abs(np.r_[lo, hi]))) if np.all(np.isfinite(np.r_[lo, hi])) else 0.0
    found = intruders(geo, lo, hi, lambda p: prim.contains(p, -tol), allowed)
    return thin_message(geo, found)


def thin_message(geo: CompiledGeometry, found: dict[int, float]) -> PlacementCheck | None:
    if not found:
        return None
    names = ", ".join(body_title(geo, b) if b <= geo.nbodys else "fuera de la geometría"
                      for b, _f in sorted(found.items(), key=lambda kv: -kv[1])[:3])
    return PlacementCheck(False, f"Cortaría a {names} (muy fino: los puntos de muestra no lo ven, un rayo sí).",
                          0.0, {b: 0.0 for b in found})


# ---------------------------------------------------------------------------
# Añadir y quitar cuerpos
# ---------------------------------------------------------------------------

def _mother_module_element(doc: GeoDocument, label: str) -> ModuleElement | None:
    """Módulo que declara ``label`` como hija (con BODY o MODULE; la etiqueta con sus cuatro columnas)."""
    lab = label_key(label)
    for el in doc.elements:
        if isinstance(el, ModuleElement) and label_key(el.label) != lab \
                and any(not r.is_surface and label_key(r.label) == lab for r in el.items):
            return el
    return None


def _insert_before(doc: GeoDocument, anchor: Element | None, new: list[Element]) -> None:
    if anchor is None:
        idx = next((i for i, e in enumerate(doc.elements) if isinstance(e, EndElement)), len(doc.elements))
    else:
        idx = doc.elements.index(anchor)
    doc.elements[idx:idx] = new


def add_primitive(doc: GeoDocument, geo: CompiledGeometry, prim: Primitive, material: int,
                  container: int, name: str = "", parinp=None, module: bool = False) -> str:
    """Añade el cuerpo (o el módulo) ``prim`` con material ``material`` dentro de ``container``.

    Devuelve la etiqueta del elemento nuevo. El contenedor puede ser un
    cuerpo (el nuevo pasa a ser limitante suyo y, si el cuerpo es hija de un
    módulo, hija de ese módulo), un módulo (hija) o la envolvente automática
    (primer nivel). Un módulo nuevo empieza sin hijas: se le pueden añadir
    cuerpos dentro después.
    """
    prim.validate()
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    check_material(material)
    if module and prim.kind not in MODULE_KINDS:
        raise EditError("Un módulo tiene que ser una esfera, un cilindro, una caja o un prisma (la lámina solo puede "
                        "ser un cuerpo).")
    enclosure = is_top_level(geo, container)
    anchor: Element | None = None
    cont_el: Element | None = None
    if not enclosure:
        cont_el = editable_body(geo, container)
        anchor = cont_el
    elif prim.kind == "slab":
        raise EditError("Una lámina tiene que ir dentro de un cuerpo (es infinita).")
    if prim.kind == "slab" and not isinstance(cont_el, BodyElement):
        raise EditError("Una lámina tiene que ir dentro de un cuerpo, no de un módulo.")
    new_els: list[Element] = []
    taken: set[str] = set()
    refs: list[RefLine] = []
    frame = local_frame(geo, container, parinp) if not enclosure else None
    for idx, params, side, desc in prim.surfaces(frame):
        lab = new_label(doc, "surface", geo, taken)
        taken.add(lab.strip())
        s = SurfaceElement(ElementKind.SURFACE, label=lab, indices=idx, params=_set_params([], params, REDUCED),
                           modified=True)
        set_description(s, desc)
        new_els.append(s)
        refs.append(RefLine("SURFACE", lab, side))
    if prim.kind == "slab":
        # Lámina = contenedor ∩ par de planos: se copian las superficies propias del contenedor.
        for r in cont_el.items:
            if r.is_surface:
                refs.append(RefLine(r.keyword, r.label, r.side))
        # Y los cuerpos que el contenedor ya excluye, para no solaparse con ellos.
        for r in cont_el.items:
            if not r.is_surface:
                refs.append(RefLine(r.keyword, r.label))
    blab = new_label(doc, "body", geo)
    kw = "MODULE" if module else "BODY"
    if module:
        new: Element = ModuleElement(ElementKind.MODULE, label=blab, material=int(material), items=refs,
                                     modified=True)
        default = f"módulo: {prim.describe()[0].lower()}{prim.describe()[1:]}, material {material}"
    else:
        new = BodyElement(ElementKind.BODY, label=blab, material=int(material), items=refs, modified=True)
        default = f"{prim.describe()}, material {material}"
    set_description(new, name.strip() or default)
    new_els.append(new)
    _insert_before(doc, anchor, new_els)
    if cont_el is not None:
        cont_el.items.append(RefLine(kw, blab))
        cont_el.modified = True
        if isinstance(cont_el, BodyElement):
            mother = _mother_module_element(doc, cont_el.label)
            if mother is not None:
                mother.items.append(RefLine(kw, blab))
                mother.modified = True
    return blab


def delete_problem(doc: GeoDocument, geo: CompiledGeometry, kb: int) -> str | None:
    """Por qué no se puede eliminar el elemento ``kb`` (None si se puede).

    Para decirlo antes de pedir confirmación: un módulo con cuerpos dentro, un
    módulo (o un clon) que copia un CLONE, la envolvente, un elemento de un
    INCLUDE o de un clon...
    """
    if not 1 <= kb <= geo.nbodys:
        return "No hay ningún cuerpo seleccionado."
    b = geo.body(kb)
    sole = _sole_top_module_after(geo, kb)
    if sole is not None:
        out = root_escapes(geo, sole)
        if out:
            names = ", ".join(body_title(geo, k) for k in out[:3])
            one = len(out) == 1
            return (f"Al eliminarlo, {body_title(geo, sole)} quedaría solo arriba y PENGEOM no le añadiría la "
                    f"envolvente: sería toda la geometría, y {names}, que {'asoma' if one else 'asoman'} fuera de "
                    f"él, {'ocuparía' if one else 'ocuparían'} también lo de fuera (lejos de lo eliminado, donde no "
                    f"había nada). Corrija antes {'esa hija' if one else 'esas hijas'} (Editar → «Buscar solapes "
                    "en la geometría») o envuelva lo que queda en un módulo.")
    if kb == getattr(geo, "ct_body", None):
        if b.element is None or b.document is not geo.document:
            return "La caja CT está definida en un fichero INCLUDE: quítela allí."
        return None
    if b.origin == "clone" and isinstance(b.element, CloneElement) and b.element is b.clone_element:
        if b.document is not geo.document:
            return "Está definido en un fichero INCLUDE: ábralo para editarlo."
        el: Element = b.element
    else:
        try:
            el = editable_body(geo, kb)
        except EditError as exc:
            return str(exc)
    lab = _lab(el.label)
    users = clone_users(doc, el.label)
    if users:
        what = "el clon" if isinstance(el, CloneElement) else "el módulo"
        names = ", ".join(f"«{_lab(u.label)}»" for u in users[:3])
        one = len(users) == 1
        return (f"{'El clon' if one else 'Los clones'} {names} {'copia' if one else 'copian'} {what} «{lab}»: "
                f"{'quítelo' if one else 'quítelos'} antes (o {'conviértalo' if one else 'conviértalos'} en "
                f"{'copia independiente' if one else 'copias independientes'}).")
    if isinstance(el, ModuleElement) and any(not r.is_surface for r in el.items):
        return "El módulo tiene cuerpos dentro: quítelos antes (o edite el fichero)."
    return None


def _sole_top_module_after(geo: CompiledGeometry, kb: int) -> int | None:
    """El módulo que quedaría solo arriba al quitar el elemento de primer nivel ``kb`` (None si no es el caso). Con
    un solo elemento arriba, si es un módulo, GEOMIN no añade la envolvente y ese módulo es toda la geometría."""
    if not getattr(geo, "enclosure_added", False):
        return None
    enc = next((b.index for b in geo.bodies if b.origin == "enclosure"), None)
    if enc is None or geo.body(kb).mother != enc:
        return None
    tops = [b.index for b in geo.bodies if b.mother == enc and b.index not in (kb, enc)]
    if len(tops) != 1 or not geo.body(tops[0]).is_module:
        return None
    return tops[0]


def root_escapes(geo: CompiledGeometry, kb: int, n: int = 20000, seed: int = 31) -> list[int]:
    """Hijas del módulo ``kb`` que asoman fuera de él, con puntos de muestra alrededor (hasta una vez su tamaño por
    cada lado). Como raíz (sin la envolvente), LOCATE no mira sus superficies antes de buscar en sus hijas: lo que
    asoma de una hija se convierte en material hasta donde llegue (décima revisión: con una hija sin tapa, hasta el
    infinito)."""
    from .extent import analytic_box, scene_box
    from .overlap import _envelope, surface_signs

    lo, hi = analytic_box(geo, kb)
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.all(hi >= lo)):
        lo, hi = scene_box(geo)
    lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
        return []
    size = np.maximum(hi - lo, 1e-6 * max(float(np.max(np.abs(np.r_[lo, hi]))), 1.0))
    rng = np.random.default_rng(seed)
    pts = rng.uniform(lo - size, hi + size, size=(n, 3))
    tol = 1e-9 * max(float(np.max(size)), float(np.max(np.abs(np.r_[lo, hi]))), 1.0)
    signs = surface_signs(geo, pts, tol)
    em = _envelope(geo, kb, signs)
    out = []
    for d in dict.fromkeys(geo.body(kb).daughters):
        if d != kb and np.any(_envelope(geo, d, signs) & ~em):
            out.append(d)
    return out


def delete_body(doc: GeoDocument, geo: CompiledGeometry, kb: int) -> list[str]:
    """Quita un cuerpo; su volumen pasa a ser del cuerpo o módulo que lo contenía.

    Los cuerpos que lo tenían como limitante pasan a tener sus limitantes
    (si no, se solaparían con ellos), y se borran las superficies que solo
    usaba él. Devuelve una lista de cambios para el usuario.
    """
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    el = editable_body(geo, kb)
    problem = delete_problem(doc, geo, kb)
    if problem:
        raise EditError(problem)
    # Las etiquetas, con sus cuatro columnas (como PENGEOM: quitar «1   » no toca las referencias a «   1»).
    lab = label_key(el.label)
    own_limits = [] if isinstance(el, ModuleElement) else [RefLine(r.keyword, r.label) for r in el.items
                                                           if not r.is_surface]
    notes = []
    for user in body_users(doc, lab):
        new_items = []
        for r in user.items:
            if not r.is_surface and label_key(r.label) == lab:
                present = {label_key(x.label) for x in user.items if not x.is_surface}
                for lim in own_limits:
                    if label_key(lim.label) not in present:
                        new_items.append(RefLine(lim.keyword, lim.label))
                continue
            new_items.append(r)
        user.items = new_items
        user.modified = True
        what = "módulo" if isinstance(user, ModuleElement) else "cuerpo"
        notes.append(f"El {what} «{_lab(user.label)}» ocupa ahora su volumen.")
    surfaces = [r.label for r in el.items if r.is_surface]
    doc.elements.remove(el)
    for s in surfaces:
        if not surface_users(doc, s):
            sel = find_element(doc, "surface", s)
            if sel is not None:
                doc.elements.remove(sel)
                notes.append(f"Superficie «{_lab(s)}» borrada (no la usa nadie más).")
    return notes


def delete_ct(doc: GeoDocument, geo: CompiledGeometry) -> list[str]:
    """Quita la caja CT (CTBOX); su volumen vuelve al cuerpo o módulo que la contenía. El .ct no se borra."""
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    kb = getattr(geo, "ct_body", None)
    if not kb:
        raise EditError("La geometría no tiene CT.")
    b = geo.body(kb)
    el = b.element
    if el is None or b.document is not geo.document:
        raise EditError("La caja CT está definida en un fichero INCLUDE: quítela allí.")
    lab = label_key(el.label)
    notes = []
    for user in body_users(doc, lab):
        user.items = [r for r in user.items if r.is_surface or label_key(r.label) != lab]
        user.modified = True
        what = "módulo" if isinstance(user, ModuleElement) else "cuerpo"
        notes.append(f"El {what} «{_lab(user.label)}» ocupa ahora su volumen.")
    doc.elements.remove(el)
    notes.append(f"El fichero {getattr(el, 'filename', '') or '.ct'} no se borra.")
    return notes


def contains_ct(geo: CompiledGeometry, kb: int) -> bool:
    """¿Está la caja CT dentro de ``kb`` (o es ``kb``)?"""
    ct = getattr(geo, "ct_body", None)
    k = ct
    seen = 0
    while k and seen <= geo.nbodys:
        if k == kb:
            return True
        k = geo.body(k).mother
        seen += 1
    return False


# ---------------------------------------------------------------------------
# Módulos: convertir, mover y pegar
# ---------------------------------------------------------------------------

def _limiting_closure(doc: GeoDocument, el: BodyElement) -> list[RefLine]:
    """Limitantes de un cuerpo y, de los que son cuerpos, sus limitantes (en orden, sin repetir).

    También los de los cuerpos de un fichero incluido con INCLUDE* (comparte las etiquetas con el principal):
    revisión de 0.16.2, si un limitante venía de un INCLUDE* sus propios limitantes se quedaban fuera del módulo
    nuevo y GEOMIN les daba madre sin hacerlos hijas (LOCATE ya no los encontraba).
    """
    out: list[RefLine] = []
    seen: set[str] = {label_key(el.label)}

    def visit(e: Element) -> None:
        for r in e.items:
            if r.is_surface or label_key(r.label) in seen:
                continue
            seen.add(label_key(r.label))
            out.append(RefLine(r.keyword, r.label))
            sub = shared_body_element(doc, r.label)
            if r.keyword == "BODY" and isinstance(sub, BodyElement):
                visit(sub)

    visit(el)
    return out


def _compiled_closure(geo: CompiledGeometry, kb: int) -> list[int]:
    """Los limitantes de ``kb`` en GEOMIN y, de los que son cuerpos, los suyos (números de cuerpo)."""
    out: list[int] = []
    stack = list(geo.body(kb).limiting)
    while stack:
        k = stack.pop()
        if 1 <= k <= geo.nbodys and k not in out:
            out.append(k)
            if not geo.body(k).is_module:
                stack.extend(geo.body(k).limiting)
    return out


def closure_problem(geo: CompiledGeometry, kb: int, closure: list[RefLine] | None = None) -> str | None:
    """Por qué no se pueden declarar como hijas de un módulo los limitantes del cuerpo ``kb`` y los de estos (lo que
    hacen «Convertir en módulo» y «Envolver»); None si se puede.

    GEOMIN da el módulo como madre a los limitantes de sus hijas, pero LOCATE solo encuentra las hijas que el módulo
    declara: todos los limitantes que tiene GEOMIN (también los de los limitantes) tienen que estar en ``closure``
    (por defecto, lo que se escribirá: ``_limiting_closure``).
    """
    b = geo.body(kb)
    el = b.element
    if not isinstance(el, BodyElement) or b.document is not geo.document:
        return None  # no es un cuerpo del fichero principal: lo dice quien lo cambia
    if closure is None:
        closure = _limiting_closure(geo.document, el)
    named = {x.index for x in (geo.find_body(r.label) for r in closure) if x is not None}
    missing = [k for k in _compiled_closure(geo, kb) if k not in named]
    if not missing:
        return None
    one = len(missing) == 1
    names = ", ".join(body_title(geo, k) for k in missing[:3]) + (" y otros" if len(missing) > 3 else "")
    return (f"{names} {'está' if one else 'están'} dentro de {body_title(geo, kb)}, pero desde el fichero principal "
            f"no se {'puede' if one else 'pueden'} declarar como {'hija' if one else 'hijas'} del módulo (LOCATE no "
            f"{'lo' if one else 'los'} encontraría): edite el fichero.")


def inside_own_surfaces(geo: CompiledGeometry, kb: int, pts: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """¿Están los puntos del lado de todas las superficies propias de ``kb``? (su «envoltura»)."""
    from .extent import _matrix

    pts = np.asarray(pts, dtype=float)
    ok = np.ones(len(pts), dtype=bool)
    for ks, side in geo.body(kb).own_surfaces():
        a, b, c = _matrix(geo.surface(ks).q)
        f = np.einsum("ij,jk,ik->i", pts, a, pts) + pts @ b + c
        scale = max(float(np.abs(a).max()), float(np.abs(b).max()), abs(c), 1e-300)
        ok &= side * f > -tol * scale
    return ok


def check_convert(geo: CompiledGeometry, locator, kb: int, fallback, n: int = 6000,
                  seed: int = 3) -> PlacementCheck:
    """¿Se puede convertir el cuerpo ``kb`` en módulo? Sus limitantes tienen que estar enteros dentro de él.

    Cada limitante se muestrea en su caja: la analítica o, si sus superficies
    no la acotan (un cubo girado), una que contiene sus puntos
    (``extent.own_box``, dentro de ``fallback``, la escena). Si no se puede
    muestrear, no se da por bueno.
    """
    from .extent import own_box, sample_members

    problem = closure_problem(geo, kb)  # sus limitantes (y los de estos) serán hijas declaradas del módulo
    if problem:
        return PlacementCheck(False, problem, 1.0)
    rng = np.random.default_rng(seed)
    b = geo.body(kb)
    f_lo, f_hi = (np.asarray(v, dtype=float) for v in fallback)
    for lim in b.limiting:
        lo, hi = own_box(geo, lim, (f_lo, f_hi))
        pts, missing = sample_members(geo, locator, lim, lo, hi, n, rng, (f_lo, f_hi))
        if missing:
            names = ", ".join(body_title(geo, k) for k in missing[:3])
            return PlacementCheck(False, f"No se ha podido comprobar que {body_title(geo, lim)} quede entero dentro de "
                                         f"{body_title(geo, kb)}: no se ha podido muestrear {names} (¿está vacío?).",
                                  1.0, {k: 1.0 for k in missing})
        if not len(pts):
            continue
        out = ~inside_own_surfaces(geo, kb, pts)
        if out.any():
            frac = float(out.mean())
            return PlacementCheck(False, f"{body_title(geo, lim)} no está entero dentro de {body_title(geo, kb)} "
                                         f"(sobresale un {100 * frac:.1f} %): en un módulo, las hijas tienen que "
                                         "estar dentro.", frac, {lim: frac})
    return PlacementCheck(True, "Todo lo que tiene dentro quedará como hijas del módulo.")


def convert_to_module(doc: GeoDocument, geo: CompiledGeometry, kb: int) -> list[str]:
    """Convierte el cuerpo ``kb`` en un módulo con el mismo volumen, material y etiqueta.

    Sus cuerpos limitantes (y los limitantes de estos) pasan a ser hijas del
    módulo; dejan de declararse en el módulo madre (PENGEOM no admite dos
    madres) y quien lo nombraba con ``BODY`` lo nombra con ``MODULE``.
    Comprobado con PENGEOM real: LOCATE da los mismos cuerpos y materiales.
    """
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    el = editable_body(geo, kb)
    if isinstance(el, ModuleElement):
        raise EditError("Ya es un módulo.")
    lab = _lab(el.label)
    key = label_key(el.label)
    closure = _limiting_closure(doc, el)
    problem = closure_problem(geo, kb, closure)
    if problem:
        raise EditError(problem)
    members = {label_key(r.label) for r in closure}
    module = ModuleElement(ElementKind.MODULE, label=el.label, description=el.description, first_line=el.first_line,
                           last_line=el.last_line, terminator_line=el.terminator_line, lead_line=el.lead_line,
                           modified=True, material=el.material, material_line=el.material_line,
                           items=[RefLine(r.keyword, r.label, r.side) for r in el.items if r.is_surface]
                           + [RefLine(r.keyword, r.label) for r in closure])
    doc.elements[doc.elements.index(el)] = module
    notes = [f"«{lab}» es ahora un módulo."]
    if closure:
        notes.append("Sus hijas: " + ", ".join(f"«{_lab(r.label)}»" for r in closure) + ".")
    for other in doc.elements:
        if other is module or not isinstance(other, BodyElement | ModuleElement):
            continue
        changed = False
        items = []
        for r in other.items:
            if not r.is_surface and label_key(r.label) == key and r.keyword != "MODULE":
                items.append(RefLine("MODULE", r.label))
                changed = True
                continue
            if isinstance(other, ModuleElement) and not r.is_surface and label_key(r.label) in members:
                changed = True  # ahora es hija del módulo nuevo
                continue
            items.append(r)
        if changed:
            other.items = items
            other.modified = True
    return notes


def move_module(doc: GeoDocument, geo: CompiledGeometry, kb: int, delta, parinp=None) -> None:
    """Desplaza el módulo ``kb`` (con todo lo que contiene) ``delta`` cm en coordenadas globales.

    Es ``placement.move_element`` con una traslación: si comparte superficies
    con cuerpos de fuera, antes recibe copias propias.
    """
    from .placement import move_element, translation

    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    el = editable_body(geo, kb)
    if not isinstance(el, ModuleElement):
        raise EditError("Solo se pueden mover módulos (un cuerpo se puede convertir antes en módulo).")
    delta = np.asarray(delta, dtype=float)
    if not np.all(np.isfinite(delta)):
        raise EditError("Desplazamiento no válido.")
    move_element(doc, geo, kb, translation(delta), parinp)


def check_move(geo: CompiledGeometry, locator, kb: int, delta, extent, n: int = 20000,
               seed: int = 5) -> PlacementCheck:
    """¿Se puede mover el módulo ``kb`` sin que se solape con nada? (``placement.check_motion`` con una traslación).

    Los puntos del volumen, ya desplazados, tienen que caer en el propio
    módulo (en su sitio de antes) o en lo que lo aloja (el cuerpo que lo
    excluye o su módulo madre); también los de sus clones, que se mueven con él.
    """
    from .placement import check_motion, translation

    delta = np.asarray(delta, dtype=float)
    if not np.all(np.isfinite(delta)):
        return PlacementCheck(False, "Desplazamiento no válido.", 1.0)
    return check_motion(geo, locator, kb, translation(delta), extent, n, seed)


def nearest_face(extent, point) -> str:
    """Cara de la caja más cercana a un punto («+x», «-z»...)."""
    lo, hi = (np.asarray(v, dtype=float) for v in extent)
    p = np.asarray(point, dtype=float)
    best = None
    for key, (axis, sign) in FACES.items():
        dist = abs((hi[axis] if sign > 0 else lo[axis]) - p[axis])
        if best is None or dist < best[0]:
            best = (dist, key)
    return best[1]


def face_from_normal(normal) -> str:
    """Cara («+x»...) a la que apunta una normal (el eje de su componente mayor)."""
    n = np.asarray(normal, dtype=float)
    axis = int(np.argmax(np.abs(n)))
    return ("+" if n[axis] > 0 else "-") + "xyz"[axis]


def glue_center(prim: Primitive, target_extent, face: str) -> float:
    """Coordenada del centro de ``prim`` a lo largo del eje de ``face`` para que toque esa cara del objetivo."""
    axis, sign = FACES[face]
    lo, hi = target_extent
    value = float(hi[axis]) if sign > 0 else float(lo[axis])
    # Toca con su lado de sentido contrario (en un prisma de lados impares, el vértice y la cara no están a la misma
    # distancia del centro).
    return _clean(value + sign * prim.reach(axis, -sign))


def glue_delta(moving_extent, target_extent, face: str, align: str = "center") -> np.ndarray:
    """Desplazamiento que deja el elemento que se mueve pegado a la cara ``face`` del objetivo.

    ``align``: 'center' (centrado en esa cara) o 'keep' (sin moverlo en los otros dos ejes).
    """
    axis, sign = FACES[face]
    m_lo, m_hi = (np.asarray(v, dtype=float) for v in moving_extent)
    t_lo, t_hi = (np.asarray(v, dtype=float) for v in target_extent)
    delta = np.zeros(3)
    target_face = t_hi[axis] if sign > 0 else t_lo[axis]
    moving_face = m_lo[axis] if sign > 0 else m_hi[axis]
    delta[axis] = target_face - moving_face
    if align == "center":
        for j in range(3):
            if j != axis:
                delta[j] = 0.5 * (t_lo[j] + t_hi[j]) - 0.5 * (m_lo[j] + m_hi[j])
    return np.array([_clean(float(v)) for v in delta])


# ---------------------------------------------------------------------------
# Comprobación
# ---------------------------------------------------------------------------

def reparse(text: str, template: GeoDocument, base_dir=None) -> GeoDocument:
    """Lee de nuevo el texto de un documento editado, con los mismos ficheros INCLUDE ya leídos.

    Así los números de línea (resaltado del texto) y los finales de línea
    corresponden al texto actual, y un documento recién leído se reescribe
    byte a byte. Un INCLUDE que ``template`` no tenía (añadido o con otro
    fichero) se lee de la carpeta de trabajo ``base_dir``, si se da.
    """
    from pathlib import Path

    from .parser import parse_geo_text

    doc = parse_geo_text(text, template.path)
    # Cada INCLUDE con su propio documento (el mismo fichero incluido dos veces son dos documentos: si lo compartieran,
    # los cuerpos de los dos serían el mismo para ``identity``). La vez n-ésima de un fichero toma el de la vez
    # n-ésima de ``template``; una vez más (p. ej. un INCLUDE duplicado), una copia leída de su texto.
    subs: dict[str, list] = {}
    for el in template.elements:
        if isinstance(el, IncludeElement) and el.document is not None:
            subs.setdefault(el.filename, []).append(el.document)
    seen: dict[str, int] = {}
    for el in doc.elements:
        if isinstance(el, IncludeElement):
            k = seen.get(el.filename, 0)
            seen[el.filename] = k + 1
            have = subs.get(el.filename, [])
            if k < len(have):
                el.document = have[k]
            elif have:
                src = have[0]
                el.document = parse_geo_text("".join(src.lines), src.path, included=True)
            else:
                el.document = None
            if el.document is None and base_dir is not None and el.filename:
                target = Path(base_dir) / el.filename
                try:
                    el.document = parse_geo_text(target.read_bytes().decode("latin-1"), str(target), included=True)
                except OSError:
                    el.document = None
            if el.document is not None:
                for d in el.document.diagnostics:
                    doc.diagnostics.items.append(d)
    return doc


def compile_checked(doc: GeoDocument, parinp=None, base_dir=None) -> CompiledGeometry:
    """Compila y lanza EditError con el mensaje de GEOMIN si la geometría no es válida."""
    geo = compile_geometry(doc, parinp=parinp, base_dir=base_dir)
    if not geo.ok:
        msgs = "; ".join(d.message for d in geo.diagnostics.errors) or "error desconocido"
        raise EditError("PENGEOM no aceptaría la geometría: " + msgs)
    return geo
