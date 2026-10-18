"""Bloques del .geo como los edita PenGeomJar: la pestaña «Geometrías complejas» (0.15.0, ADR-019).

PenGeomJar edita el .geo bloque a bloque. Un *bloque* va de la palabra clave (``SURFACE``, ``SURFACE*``, ``BODY``,
``MODULE``, ``CLONE``, ``INCLUDE``, ``INCLUDE*`` o ``CTBOX``) a la línea de ceros que lo cierra, con los comentarios
que lo preceden. Cada pestaña de sus «Edition Tabs» muestra los campos de un tipo de bloque (Surface, Surface
(implicit), Body, Module, Clone, Include y CT); **Modify** reescribe el bloque elegido con los campos, **Add** (que
se llama **Duplicate** mientras hay un bloque elegido) añade los campos como un bloque nuevo detrás del elegido, y
**Delete** lo borra.

Aquí están esas operaciones sobre el modelo de ARGUS, para usarlas dentro de ``GeoController.edit`` (en una copia
del documento: si PENGEOM no aceptara el resultado, no se hace nada):

* ``*Form``: lo que hay escrito en cada pestaña, como texto (``form_of`` lo saca de un bloque y ``element_of`` lo
  convierte en un elemento, con los mensajes de lo que esté mal escrito: ``BlockError``).
* Un bloque reescrito conserva sus comentarios y, línea a línea, el texto de lo que no cambia (un bloque que se
  reescribe sin cambiar nada queda igual byte a byte; también el orden y las repeticiones de sus parámetros).
* ``modify_block``, ``add_block``, ``delete_block``, ``move_block`` y ``relabel``. Al cambiar una etiqueta se cambian
  también sus referencias; al añadir, una etiqueta que ya existe se cambia por una libre y el bloque baja, si hace
  falta, hasta después de lo que nombra (PENGEOM exige que esté definido antes); no se borra lo que otro bloque usa.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..core.fortran_io import record
from .compiler import CompiledGeometry
from .edit import MAX_BODY_MATERIAL, EditError, LabelPool
from .model import (
    ANGLE_PARAMS,
    COEF_NAMES,
    REDUCED_PARAMS,
    TRANSFORM_PARAMS,
    BodyElement,
    CloneElement,
    CtBoxElement,
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
from .surfaces import label_text
from .writer import format_value

TAB_SURFACE = "surface"
TAB_IMPLICIT = "implicit"
TAB_BODY = "body"
TAB_MODULE = "module"
TAB_CLONE = "clone"
TAB_INCLUDE = "include"
TAB_CT = "ct"
TABS = (TAB_SURFACE, TAB_IMPLICIT, TAB_BODY, TAB_MODULE, TAB_CLONE, TAB_INCLUDE, TAB_CT)

MAX_PARINP_INDEX = 50  # penhmain admite como mucho 50 parámetros PARINP
MAX_FILENAME = 12  # INCLUDE y CTFNAME: CHARACTER*12 en GEOMIN
MAX_BLOCK_DESCRIPTION = 55  # columnas 15 a 70, con un blanco delante como poco
SHOWN_DESCRIPTION = 56  # lo que se enseña (lo que hay escrito en esas columnas)

# Los tipos de cuádrica de PenGeomJar (su lista «quadric»): índices de la forma reducida.
QUADRICS = (
    ("Plane (z=0)", (0, 0, 0, 1, 0)),
    ("Sphere", (1, 1, 1, 0, -1)),
    ("Cylinder", (1, 1, 0, 0, -1)),
    ("Hyperbolic cylinder", (1, -1, 0, 0, -1)),
    ("Cone", (1, 1, -1, 0, 0)),
    ("One-sheet hyperboloid", (1, 1, -1, 0, -1)),
    ("Two-sheet hyperboloid", (1, 1, -1, 0, 1)),
    ("Paraboloid", (1, 1, 0, -1, 0)),
    ("Parabolic cylinder", (1, 0, 0, -1, 0)),
    ("Hyperbolic paraboloid", (1, -1, 0, -1, 0)),
)
USER_INDICES = "User-selected indices"


class BlockError(EditError):
    """Algo mal escrito en una pestaña: ``field`` dice qué campo (para llevar allí el cursor)."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


# ---------------------------------------------------------------------------------------------------------------------
# Bloques y líneas
# ---------------------------------------------------------------------------------------------------------------------

def block_tab(el: Element | None) -> str | None:
    """Pestaña de PenGeomJar que muestra el bloque ``el`` (None para END)."""
    if isinstance(el, SurfaceElement):
        return TAB_IMPLICIT if el.implicit else TAB_SURFACE
    if isinstance(el, BodyElement):
        return TAB_BODY
    if isinstance(el, ModuleElement):
        return TAB_MODULE
    if isinstance(el, CloneElement):
        return TAB_CLONE
    if isinstance(el, IncludeElement):
        return TAB_INCLUDE
    if isinstance(el, CtBoxElement):
        return TAB_CT
    return None


def block_end(el: Element) -> int:
    """Última línea del bloque (la de ceros que lo cierra, si la tiene)."""
    if isinstance(el, EndElement):
        return el.first_line
    return el.terminator_line if el.terminator_line is not None else el.last_line


def block_lines(el: Element) -> tuple[int, int]:
    """Líneas que se resaltan al elegir el bloque: de los comentarios que lo preceden (van con él al borrarlo o
    moverlo) a la línea de ceros, como en PenGeomJar."""
    lead = el.first_line
    if el.lead_line is not None and lead is not None and el.lead_line < lead:
        lead = el.lead_line
    return lead, block_end(el)


def block_at_line(doc: GeoDocument | None, line: int) -> int | None:
    """Índice en ``doc.elements`` del bloque al que pertenece la línea ``line`` (0-based), con los comentarios que lo
    preceden; None en la cabecera, en END y detrás de END."""
    if doc is None:
        return None
    for i, el in enumerate(doc.elements):
        if isinstance(el, EndElement):
            return None
        lead = el.lead_line if el.lead_line is not None else el.first_line
        if lead <= line <= block_end(el):
            return i
    return None


def block_title(el: Element) -> str:
    """«SURFACE (   5)», «BODY ( AB1)», «INCLUDE* (example1)»… para los mensajes."""
    if isinstance(el, SurfaceElement):
        kw = "SURFACE*" if el.starred else "SURFACE"
    elif isinstance(el, IncludeElement):
        return ("INCLUDE*" if el.starred else "INCLUDE") + f" ({el.filename})"
    elif isinstance(el, CtBoxElement):
        kw = "CTBOX"
    else:
        kw = el.kind.value
    return f"{kw} ({(el.label or '').strip()})"


def editable_blocks(doc: GeoDocument) -> list[int]:
    """Índices de los bloques que se pueden elegir (todos menos END)."""
    return [i for i, el in enumerate(doc.elements) if not isinstance(el, EndElement)]


# ---------------------------------------------------------------------------------------------------------------------
# Lo escrito en las pestañas
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Param:
    """Una fila de parámetro: valor e índice PARINP tal como están escritos; sin valor, la línea no se escribe."""

    name: str
    value: str = ""
    index: str = ""
    unit: str = "DEG"  # ángulos: «DEG» o «RAD»


@dataclass
class SurfaceForm:
    """Pestañas Surface (forma reducida) y Surface (implicit)."""

    label: str = ""
    description: str = ""
    starred: bool = False
    implicit: bool = False
    indices: tuple[int, int, int, int, int] = (0, 0, 0, 1, 0)
    params: dict[str, Param] = field(default_factory=dict)  # reducida: X-SCALE…Z-SHIFT; implícita: AXX…A0
    transform: dict[str, Param] = field(default_factory=dict)  # implícita: OMEGA…Z-SHIFT tras la línea de unos

    @property
    def tab(self) -> str:
        return TAB_IMPLICIT if self.implicit else TAB_SURFACE


@dataclass
class BodyForm:
    """Pestañas Body y Module (``module``): limitantes (y, en un módulo, hijas) y la transformación del módulo."""

    label: str = ""
    description: str = ""
    material: str = ""
    module: bool = False
    surfaces: list[tuple[str, int]] = field(default_factory=list)  # (etiqueta, lado)
    bodies: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    transform: dict[str, Param] = field(default_factory=dict)

    @property
    def tab(self) -> str:
        return TAB_MODULE if self.module else TAB_BODY


@dataclass
class CloneForm:
    label: str = ""
    description: str = ""
    module: str = ""
    module_note: str = ""  # lo que va detrás de «MODULE  (   6)» (PENGEOM no lo lee)
    transform: dict[str, Param] = field(default_factory=dict)

    tab = TAB_CLONE


@dataclass
class IncludeForm:
    filename: str = ""
    starred: bool = False

    tab = TAB_INCLUDE


@dataclass
class CtForm:
    label: str = ""
    description: str = ""
    filename: str = ""
    transform: dict[str, Param] = field(default_factory=dict)

    tab = TAB_CT


Form = SurfaceForm | BodyForm | CloneForm | IncludeForm | CtForm


def empty_form(tab: str) -> Form:
    """Pestaña vacía («New»): una superficie nueva es un plano (como en PenGeomJar)."""
    if tab in (TAB_SURFACE, TAB_IMPLICIT):
        return SurfaceForm(implicit=tab == TAB_IMPLICIT, indices=(0, 0, 0, 0, 0) if tab == TAB_IMPLICIT
                           else QUADRICS[0][1])
    if tab in (TAB_BODY, TAB_MODULE):
        return BodyForm(material="0", module=tab == TAB_MODULE)  # (PenGeomJar también propone el 0)
    if tab == TAB_CLONE:
        return CloneForm()
    if tab == TAB_INCLUDE:
        return IncludeForm()
    if tab == TAB_CT:
        return CtForm()
    raise ValueError(tab)


def quadric_name(indices) -> str:
    """Nombre de la lista de PenGeomJar para unos índices (o «User-selected indices»)."""
    t = tuple(int(i) for i in indices)
    return next((name for name, ind in QUADRICS if ind == t), USER_INDICES)


# -- del bloque a la pestaña ------------------------------------------------------------------------------------------

def _raw(doc: GeoDocument | None, line: int | None) -> str | None:
    if doc is None or line is None or not 0 <= line < len(doc.lines):
        return None
    return record(doc.lines[line])


def param_text(doc: GeoDocument | None, p: ParamLine) -> str:
    """El valor de un parámetro como está escrito (columnas 10-31), como lo enseña PenGeomJar."""
    raw = _raw(doc, p.line)
    text = raw[9:31].strip() if raw is not None else ""
    return text or format_value(p.value).strip()


def _params_form(doc: GeoDocument | None, params: list[ParamLine], names) -> dict[str, Param]:
    out: dict[str, Param] = {}
    for p in params:  # el último de cada nombre es el que vale (PENGEOM)
        if p.name in names:
            out[p.name] = Param(p.name, param_text(doc, p), str(p.ipar), p.unit if p.is_angle else "DEG")
    return out


def form_of(el: Element, doc: GeoDocument | None = None) -> Form:
    """Lo que enseña la pestaña de ``el`` (``doc``: su documento, para los valores tal como están escritos)."""
    if isinstance(el, SurfaceElement):
        if el.implicit:
            return SurfaceForm(el.display_label, el.description.strip(), el.starred, True, tuple(el.indices),
                               _params_form(doc, el.params, COEF_NAMES),
                               _params_form(doc, el.transform, TRANSFORM_PARAMS))
        return SurfaceForm(el.display_label, el.description.strip(), el.starred, False, tuple(el.indices),
                           _params_form(doc, el.params, REDUCED_PARAMS))
    if isinstance(el, BodyElement | ModuleElement):
        module = isinstance(el, ModuleElement)
        return BodyForm(el.display_label, el.description.strip(), str(el.material), module,
                        [(r.label, r.side if r.side is not None else 1) for r in el.items if r.is_surface],
                        [r.label for r in el.items if r.keyword == "BODY"],
                        [r.label for r in el.items if r.keyword == "MODULE"],
                        _params_form(doc, el.transform, TRANSFORM_PARAMS) if module else {})
    if isinstance(el, CloneElement):
        return CloneForm(el.display_label, el.description.strip(), el.module_label, el.module_note.strip(),
                         _params_form(doc, el.transform, TRANSFORM_PARAMS))
    if isinstance(el, IncludeElement):
        return IncludeForm(el.filename, el.starred)
    if isinstance(el, CtBoxElement):
        return CtForm(el.display_label, el.description.strip(), el.filename,
                      _params_form(doc, el.transform, TRANSFORM_PARAMS))
    raise BlockError("Este bloque no tiene pestaña.")


# -- de la pestaña al bloque ------------------------------------------------------------------------------------------

_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
# Lo que escribe gfortran (y ARGUS) con exponentes de tres cifras: «1.0-100», «+2.5+123» (sin la «E»).
_FORTRAN_EXP = re.compile(r"([+-]?(\d+\.\d*|\.\d+))([+-]\d{3})")


def parse_number(text: str, what: str = "El valor", field: str | None = None) -> float:
    """Un número escrito en una pestaña: «2.5», «2,5», «+2.500000000000000E+00», «1e3», «1.0D0» o «1.0-100».

    Más estricto que la lectura de Fortran (que vale para los números del fichero): sin cifras («-», «.») no es un
    número (no se toma como 0), y el exponente sin «E» solo vale como lo escribe gfortran («1.0-100»), no «10-2».
    """
    t = (text or "").strip().replace("−", "-").replace(" ", "")
    if not t:
        raise BlockError(f"{what}: falta el número.", field)
    if "." in t and "," in t:  # «1.000,5» o «1,000.5»: el último es el decimal
        k = max(t.rfind("."), t.rfind(","))
        t = t[:k].replace(".", "").replace(",", "") + "." + t[k + 1:]
    else:
        t = t.replace(",", ".")
    t = t.replace("d", "e").replace("D", "e")
    m = _FORTRAN_EXP.fullmatch(t)
    if m is not None:
        t = f"{m.group(1)}e{m.group(3)}"
    if _NUMBER.fullmatch(t) is None:
        raise BlockError(f"{what}: «{text.strip()}» no es un número.", field)
    value = float(t)
    if not math.isfinite(value) or abs(value) >= 1e300:
        raise BlockError(f"{what}: «{text.strip()}» no es un número que se pueda escribir en el .geo.", field)
    return value


def e22(value: float) -> float:
    """El valor que se lee de vuelta de su E22.15 en el fichero (lo que leerá PENGEOM): así el valor que se escribe
    y el que se guarda en el modelo son el mismo (si no, una línea no se reconocería como suya al volver a leerla)."""
    text = format_value(value).strip()
    m = _FORTRAN_EXP.fullmatch(text)  # exponentes de tres cifras: «+1.000000000000000-100»
    return float(f"{m.group(1)}e{m.group(3)}" if m is not None else text)


def parse_index(text: str, what: str, old: int | None = None) -> int:
    """Índice PARINP (vacío o 0: ninguno). ``old``: el índice que tenía la línea; si no se ha tocado, vale tal cual
    (también uno negativo, que GEOMIN toma como «ninguno» y que escribe así en geometry.rep)."""
    t = (text or "").strip()
    if not t:
        return 0
    try:
        k = int(t.lstrip("+"))
    except ValueError:
        raise BlockError(f"{what}: el índice PARINP «{t}» no es un número entero.", f"{what}.index") from None
    if old is not None and k == old:
        return k
    if not 0 <= k <= MAX_PARINP_INDEX:
        raise BlockError(f"{what}: el índice PARINP {k} tiene que estar entre 0 (ninguno) y {MAX_PARINP_INDEX} "
                         f"(penhmain admite {MAX_PARINP_INDEX} valores PARINP).", f"{what}.index")
    return k


def _label(text: str, what: str, old: str | None = None, field_name: str = "label") -> str:
    """Etiqueta de 4 caracteres; si es la de antes (sin mirar los blancos), la de antes tal cual."""
    if old is not None and (text or "").strip() == old.strip() and old.strip():
        return old
    try:
        return label_text(text)
    except EditError as exc:
        raise BlockError(f"{what}: {exc}", field_name) from None


def _description(text: str, old: str | None) -> str:
    """La descripción (o la nota de un clon) tal como va tras la etiqueta: de la columna 15 a la 70, con tres blancos
    delante si caben (al menos uno)."""
    text = " ".join((text or "").split())
    if old is not None and text == " ".join(old.split()):
        return old
    if len(text) > MAX_BLOCK_DESCRIPTION:
        raise BlockError(f"La descripción tiene {len(text)} caracteres: caben {MAX_BLOCK_DESCRIPTION} (las columnas "
                         "15 a 70 de la línea, las que vuelve a leer ARGUS).", "description")
    if any(not 32 <= ord(c) < 256 for c in text):
        raise BlockError("La descripción tiene caracteres que no caben en el .geo (Latin-1).", "description")
    return " " * max(1, min(3, 56 - len(text))) + text if text else ""


def _param_values(rows: dict[str, Param], names, old: dict[str, ParamLine] | None = None,
                  doc: GeoDocument | None = None) -> dict[str, tuple[str, float, int, str]]:
    """(texto del valor, valor, índice PARINP, unidad) de cada parámetro escrito. ``old``: nombre → la línea de
    antes; un valor o un índice que no se han tocado no se vuelven a leer (valen lo que leyó GEOMIN: p. ej. «250»
    sin punto decimal es 2.5E-13 en un campo E22.15, y un índice negativo es «ninguno»)."""
    out: dict[str, tuple[str, float, int, str]] = {}
    for name in names:
        r = rows.get(name)
        if r is None:
            continue
        prev = (old or {}).get(name)
        idx = parse_index(r.index, name, prev.ipar if prev is not None else None)
        if not r.value.strip():
            if idx > 0:
                raise BlockError(f"{name}: escriba también un valor (con el índice {idx}, penhmain lo sustituye por "
                                 f"PARINP({idx}) del .in, pero la línea lleva un número).", name)
            continue
        if prev is not None and r.value.strip() == param_text(doc, prev):
            value = prev.value
        else:
            value = e22(parse_number(r.value, name, name))
        if name.endswith("SCALE") and idx <= 0 and value < 1.0e-15:
            raise BlockError(f"{name} tiene que ser mayor que 1.0E-15 (PENGEOM no admite escalas nulas ni "
                             "negativas).", name)
        out[name] = (r.value.strip(), value, idx, (r.unit if r.unit in ("DEG", "RAD") else "DEG")
                     if name in ANGLE_PARAMS else "DEG")
    return out


def _same_param(p: ParamLine, value: float, ipar: int, unit: str) -> bool:
    return ipar == p.ipar and (not p.is_angle or unit == p.unit) and value == p.value


def _adopt_params(doc: GeoDocument | None, rows: dict[str, Param], names, old: list[ParamLine] | None
                  ) -> list[ParamLine]:
    """Las líneas de parámetros escritas en la pestaña. Las de antes que no cambian se conservan tal cual (con sus
    repeticiones y en su orden); las que cambian se reescriben en su sitio; las nuevas van al final, en el orden
    de PENGEOM."""
    old = old or []
    last = {p.name: p for p in old}
    want = _param_values(rows, names, last, doc)
    out: list[ParamLine] = []
    done: set[str] = set()
    same = {n: _same_param(p, *want[n][1:]) for n, p in last.items() if n in want}
    for p in old:
        if p.name not in want:
            continue  # la fila se ha vaciado: la línea se quita
        if same[p.name]:
            out.append(p)
        elif p is last[p.name]:  # cambia: la última (la que vale) se reescribe en su sitio
            _text, value, ipar, unit = want[p.name]
            out.append(ParamLine(p.name, value, ipar, unit, p.line))
        done.add(p.name)
    for name in names:
        if name in want and name not in done:
            _text, value, ipar, unit = want[name]
            out.append(ParamLine(name, value, ipar, unit))
    return out


def _adopt_refs(want: list[tuple[str, str, int | None]], old: list[RefLine] | None) -> list[RefLine]:
    """Las referencias de la pestaña (palabra clave, etiqueta, lado), en el orden de sus tablas. Cada clase
    (superficies, cuerpos, módulos) ocupa los sitios que tenía en el bloque (así un bloque que no cambia queda igual
    aunque mezcle las clases) y lo que sobra va detrás de las de su clase; una línea que sigue en la tabla conserva
    su texto (con su lado nuevo, si cambia)."""
    def kind_of(r: RefLine) -> str:
        return "SURFACE" if r.is_surface else r.keyword

    queues: dict[str, list[tuple[str, str, int | None]]] = {"SURFACE": [], "BODY": [], "MODULE": []}
    for w in want:
        queues[w[0]].append(w)
    old = list(old or [])
    spare = list(old)

    def ref(w: tuple[str, str, int | None]) -> RefLine:
        kind, lab, side = w
        side = side if kind == "SURFACE" else None
        same = next((r for r in spare if kind_of(r) == kind and r.label == lab and r.side == side), None)
        if same is None:
            same = next((r for r in spare if kind_of(r) == kind and r.label == lab), None)
        if same is not None:
            spare.remove(same)
            return same if same.side == side else RefLine(same.keyword, same.label, side, same.line)
        return RefLine(kind, lab, side)

    out: list[RefLine] = []
    for r in old:  # los sitios de cada clase, en el orden del bloque
        q = queues[kind_of(r)]
        if q:
            out.append(ref(q.pop(0)))
    order = {"SURFACE": 0, "BODY": 1, "MODULE": 2}
    for kind in ("SURFACE", "BODY", "MODULE"):
        for w in queues[kind]:
            pos = 0
            for i, r in enumerate(out):
                if order[kind_of(r)] <= order[kind]:
                    pos = i + 1
            out.insert(pos, ref(w))
    return out


def _namespace(doc: GeoDocument | None, kind: str) -> list[str]:
    """Etiquetas (tal como están escritas) de las superficies (``kind`` 'SURFACE') o de los cuerpos, módulos, clones y
    CT, del fichero y de sus INCLUDE* (que comparten etiquetas con él)."""
    if doc is None:
        return []
    cls = SurfaceElement if kind == "SURFACE" else BODY_LIKE
    docs = [doc] + [el.document for el in doc.elements if isinstance(el, IncludeElement) and el.starred
                    and el.document is not None]
    return [el.label for d in docs for el in d.elements if isinstance(el, cls) and el.label is not None]


def resolve_label(doc: GeoDocument | None, kind: str, text: str, what: str = "La etiqueta") -> str:
    """La etiqueta de 4 caracteres a la que se refiere lo escrito. PENGEOM compara las cuatro columnas: «S1» es la
    «S1  » del fichero si es la única «S1» (con blancos a un lado o al otro); si no hay ninguna, se alinea a la
    derecha como escribe ARGUS."""
    t = text or ""
    names = _namespace(doc, kind)
    if len(t) == 4 and t in names:
        return t
    try:
        canon = label_text(t)
    except EditError as exc:
        raise BlockError(f"{what} «{t.strip()}»: {exc}", kind.lower()) from None
    cands = [n for n in names if n.strip() == t.strip()]
    return canon if canon in cands or not cands else cands[0]


def _ref_list(form: BodyForm, doc: GeoDocument | None) -> list[tuple[str, str, int | None]]:
    out: list[tuple[str, str, int | None]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, lab: str, side: int | None, what: str) -> None:
        raw = resolve_label(doc, kind, lab, what)
        key = ("SURFACE" if kind == "SURFACE" else "BODY", raw)
        if key in seen:
            raise BlockError(f"{what} «{raw.strip()}» está dos veces en las tablas.", kind.lower())
        seen.add(key)
        out.append((kind, raw, side))

    for lab, side in form.surfaces:
        if side not in (-1, 1):
            raise BlockError(f"El lado de la superficie «{lab.strip()}» tiene que ser -1 o +1.", "surface")
        add("SURFACE", lab, side, "La superficie")
    for lab in form.bodies:
        add("BODY", lab, None, "El cuerpo")
    for lab in form.modules:
        add("MODULE", lab, None, "El módulo")
    return out


def _material(text: str, old: int | None) -> int:
    t = (text or "").strip()
    if not t:
        raise BlockError("Falta el número de material (0: vacío).", "material")
    try:
        m = int(t.lstrip("+"))
    except ValueError:
        raise BlockError(f"El material «{t}» no es un número entero.", "material") from None
    if old is not None and m == old:
        return m
    if m == 9999:
        raise BlockError("El material 9999 es la marca de la caja CT de PENGEOM: no se puede usar en un cuerpo o "
                         "módulo.", "material")
    if not 0 <= m <= MAX_BODY_MATERIAL:
        raise BlockError(f"El material {m} tiene que estar entre 0 (vacío) y {MAX_BODY_MATERIAL}.", "material")
    return m


def _filename(text: str, what: str, old: str | None = None) -> str:
    """Nombre de un fichero incluido o del CT (``old``: el que tenía el bloque; si no se ha tocado, vale tal cual,
    como lo leyó PENGEOM)."""
    t = (text or "").strip()
    if old is not None and t == old.strip() and t:
        return old
    if not t:
        raise BlockError(f"Falta el nombre del fichero {what}.", "filename")
    if len(t) > MAX_FILENAME:
        raise BlockError(f"El nombre del fichero {what} tiene {len(t)} caracteres: PENGEOM lee {MAX_FILENAME} "
                         "(CHARACTER*12). Cámbiele el nombre al fichero.", "filename")
    if any(c in t for c in "()") or any(not 32 < ord(c) < 127 for c in t):
        raise BlockError(f"El nombre del fichero {what} no puede llevar paréntesis, blancos ni acentos.", "filename")
    return t


def _copy_lines(new: Element, old: Element | None) -> Element:
    """El elemento nuevo ocupa el sitio de ``old`` en el fichero (comentarios incluidos) y se reescribe línea a
    línea."""
    if old is not None:
        new.first_line, new.last_line = old.first_line, old.last_line
        new.terminator_line, new.lead_line = old.terminator_line, old.lead_line
    new.modified = True
    return new


def element_of(form: Form, doc: GeoDocument | None = None, old: Element | None = None) -> Element:
    """El elemento que dice la pestaña; con ``old`` (Modify), en su sitio y conservando lo que no cambia."""
    if isinstance(form, SurfaceForm):
        prev = old if isinstance(old, SurfaceElement) else None
        label = _label(form.label, "SURFACE", prev.label if prev else None)
        desc = _description(form.description, prev.description if prev else None)
        indices = tuple(int(i) for i in form.indices)
        if len(indices) != 5 or any(i not in (-1, 0, 1) for i in indices):
            raise BlockError("Los índices de la superficie son -1, 0 o 1.", "indices")
        if form.implicit:
            if any(indices):
                raise BlockError("En la forma implícita los índices son ( 0, 0, 0, 0, 0).", "indices")
            same_kind = prev is not None and prev.implicit
            params = _adopt_params(doc, form.params, COEF_NAMES, prev.params if same_kind else None)
            transform = _adopt_params(doc, form.transform, TRANSFORM_PARAMS, prev.transform if same_kind else None)
            if not params:
                raise BlockError("Escriba al menos un coeficiente (AXX…A0): sin ninguno no hay superficie.", "AXX")
            el = SurfaceElement(ElementKind.SURFACE, label, desc, starred=form.starred, indices=indices,
                                params=params, transform=transform,
                                has_ones_line=bool(transform) or bool(same_kind and prev.has_ones_line))
        else:
            if not any(indices):
                raise BlockError("Con los cinco índices a 0 la superficie está en forma implícita: escríbala en la "
                                 "pestaña «Surface (implicit)».", "indices")
            same_kind = prev is not None and not prev.implicit
            params = _adopt_params(doc, form.params, REDUCED_PARAMS, prev.params if same_kind else None)
            el = SurfaceElement(ElementKind.SURFACE, label, desc, starred=form.starred, indices=indices,
                                params=params)
        return _copy_lines(el, prev)
    if isinstance(form, BodyForm):
        cls = ModuleElement if form.module else BodyElement
        prev = old if isinstance(old, cls) else None
        what = "MODULE" if form.module else "BODY"
        label = _label(form.label, what, prev.label if prev else None)
        desc = _description(form.description, prev.description if prev else None)
        material = _material(form.material, prev.material if prev else None)
        items = _adopt_refs(_ref_list(form, doc), prev.items if prev else None)
        if form.module:
            transform = _adopt_params(doc, form.transform, TRANSFORM_PARAMS, prev.transform if prev else None)
            el = ModuleElement(ElementKind.MODULE, label, desc, material=material, items=items, transform=transform,
                               has_ones_line=bool(transform) or bool(prev and prev.has_ones_line))
        else:
            el = BodyElement(ElementKind.BODY, label, desc, material=material, items=items)
        if prev is not None:
            el.material_line = prev.material_line
        return _copy_lines(el, prev)
    if isinstance(form, CloneForm):
        prev = old if isinstance(old, CloneElement) else None
        label = _label(form.label, "CLONE", prev.label if prev else None)
        desc = _description(form.description, prev.description if prev else None)
        if prev is not None and form.module.strip() == prev.module_label.strip():
            module = prev.module_label
        else:
            module = resolve_label(doc, "MODULE", form.module, "ORIG. MODULE")
        note = _description(form.module_note, prev.module_note if prev else None)
        transform = _adopt_params(doc, form.transform, TRANSFORM_PARAMS, prev.transform if prev else None)
        el = CloneElement(ElementKind.CLONE, label, desc, module_label=module, module_note=note,
                          transform=transform, has_ones_line=bool(transform) or bool(prev and prev.has_ones_line))
        if prev is not None:
            el.module_line = prev.module_line
        return _copy_lines(el, prev)
    if isinstance(form, IncludeForm):
        prev = old if isinstance(old, IncludeElement) else None
        name = _filename(form.filename, "incluido", prev.filename if prev is not None else None)
        el = IncludeElement(ElementKind.INCLUDE, None, starred=form.starred, filename=name)
        if prev is not None:
            el.file_line = prev.file_line
            if prev.filename == name:
                el.document = prev.document
        return _copy_lines(el, prev)
    if isinstance(form, CtForm):
        prev = old if isinstance(old, CtBoxElement) else None
        label = _label(form.label, "CT", prev.label if prev else None)
        desc = _description(form.description, prev.description if prev else None)
        name = _filename(form.filename, "del CT", prev.filename if prev is not None else None)
        transform = _adopt_params(doc, form.transform, TRANSFORM_PARAMS, prev.transform if prev else None)
        el = CtBoxElement(ElementKind.CTBOX, label, desc, filename=name, transform=transform,
                          has_ones_line=bool(transform) or bool(prev and prev.has_ones_line))
        if prev is not None:
            el.file_line = prev.file_line
        return _copy_lines(el, prev)
    raise BlockError("Pestaña desconocida.")


# ---------------------------------------------------------------------------------------------------------------------
# Quién usa a quién
# ---------------------------------------------------------------------------------------------------------------------

BODY_LIKE = (BodyElement, ModuleElement, CloneElement, CtBoxElement)


def _same(a: str | None, b: str | None) -> bool:
    """La misma etiqueta para PENGEOM: las cuatro columnas («   1» y «1   » son dos etiquetas distintas)."""
    return (a or "") == (b or "")


def users_of(doc: GeoDocument, el: Element) -> list[Element]:
    """Bloques del fichero que nombran a ``el`` (como superficie limitante, cuerpo o módulo, o módulo clonado)."""
    out: list[Element] = []
    if isinstance(el, SurfaceElement):
        for o in doc.elements:
            if o is not el and isinstance(o, BodyElement | ModuleElement) and any(
                    r.is_surface and _same(r.label, el.label) for r in o.items):
                out.append(o)
    elif isinstance(el, BODY_LIKE):
        for o in doc.elements:
            if o is el:
                continue
            if isinstance(o, BodyElement | ModuleElement) and any(
                    not r.is_surface and _same(r.label, el.label) for r in o.items):
                out.append(o)
            elif isinstance(o, CloneElement) and _same(o.module_label, el.label):
                out.append(o)
    return out


def _definitions(doc: GeoDocument) -> tuple[dict[str, int], dict[str, int]]:
    """Dónde se define cada etiqueta (superficies; cuerpos, módulos, clones y CT): índice del bloque. Las de un
    INCLUDE* (comparte etiquetas con el fichero) cuentan en la posición del INCLUDE*."""
    surf: dict[str, int] = {}
    body: dict[str, int] = {}
    for i, el in enumerate(doc.elements):
        if isinstance(el, SurfaceElement):
            surf.setdefault(el.label, i)
        elif isinstance(el, BODY_LIKE):
            body.setdefault(el.label or "", i)
        elif isinstance(el, IncludeElement) and el.starred and el.document is not None:
            for sel in el.document.elements:
                if isinstance(sel, SurfaceElement):
                    surf.setdefault(sel.label, i)
                elif isinstance(sel, BODY_LIKE):
                    body.setdefault(sel.label or "", i)
    return surf, body


def _needs(el: Element) -> tuple[set[str], set[str]]:
    """Etiquetas que nombra ``el`` (superficies, cuerpos/módulos)."""
    if isinstance(el, BodyElement | ModuleElement):
        return ({r.label for r in el.items if r.is_surface}, {r.label for r in el.items if not r.is_surface})
    if isinstance(el, CloneElement):
        return set(), {el.module_label}
    return set(), set()


def _end_index(doc: GeoDocument) -> int:
    return next((i for i, e in enumerate(doc.elements) if isinstance(e, EndElement)), len(doc.elements))


# ---------------------------------------------------------------------------------------------------------------------
# Las operaciones (sobre una copia del documento, dentro de GeoController.edit)
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class BlockResult:
    """Qué ha pasado: el índice del bloque (para elegirlo después), las etiquetas de cuerpos cambiadas (para que el
    .in las siga) y lo que conviene decir."""

    index: int | None
    renames: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _rename_refs(doc: GeoDocument, el: Element, old: str, new: str) -> list[Element]:
    """Las referencias a la etiqueta ``old`` de ``el`` pasan a ``new``; devuelve los bloques cambiados."""
    changed: list[Element] = []
    for o in doc.elements:
        if o is el:
            continue
        hit = False
        if isinstance(o, BodyElement | ModuleElement):
            for r in o.items:
                if r.is_surface == isinstance(el, SurfaceElement) and _same(r.label, old):
                    r.label = new
                    hit = True
        elif isinstance(o, CloneElement) and not isinstance(el, SurfaceElement) and _same(o.module_label, old):
            o.module_label = new
            hit = True
        if hit:
            o.modified = True
            changed.append(o)
    return changed


def _check_label_free(doc: GeoDocument, el: Element, geo: CompiledGeometry | None) -> None:
    """La etiqueta de ``el`` no la tiene ya otro bloque de su clase (en el fichero o en un INCLUDE*)."""
    lab = (el.label or "").strip()
    # Sin mirar los blancos (más estricto que PENGEOM): «1» y «1   » se confundirían.
    if isinstance(el, SurfaceElement):
        clash = [o for o in doc.elements if o is not el and isinstance(o, SurfaceElement)
                 and (o.label or "").strip() == lab]
        what = "otra superficie"
        shared = geo is not None and any(s.alias[:4].strip() == lab and s.alias[4:] == "0" and s.document is not doc
                                         for s in geo.surfaces)
    else:
        clash = [o for o in doc.elements if o is not el and isinstance(o, BODY_LIKE) and (o.label or "").strip() == lab]
        what = "otro cuerpo, módulo, clon o CT"
        shared = geo is not None and any(b.alias[:4].strip() == lab and b.alias[4:] == "0" and b.document is not doc
                                         for b in geo.bodies)
    if clash:
        raise BlockError(f"Ya hay {what} con la etiqueta «{lab}» ({block_title(clash[0])}).", "label")
    if shared:
        raise BlockError(f"Ya hay {what} con la etiqueta «{lab}» en un fichero incluido con INCLUDE*.", "label")


def modify_block(doc: GeoDocument, index: int, form: Form, geo: CompiledGeometry | None = None) -> BlockResult:
    """Modify: el bloque ``index`` pasa a ser el de la pestaña. Si cambia su etiqueta, también sus referencias."""
    old = doc.elements[index]
    if block_tab(old) != form.tab:
        raise BlockError(f"El bloque elegido es {block_title(old)}: no se puede reescribir desde esta pestaña.")
    new = element_of(form, doc, old)
    res = BlockResult(index)
    if not isinstance(new, IncludeElement) and not _same(new.label, old.label):
        _check_label_free(doc, new, geo)
        users = _rename_refs(doc, old, old.label, new.label)
        if users:
            res.notes.append(f"La etiqueta «{old.label.strip()}» se ha cambiado también en "
                             + ", ".join(block_title(u) for u in users[:6]) + (" …" if len(users) > 6 else "") + ".")
        if isinstance(new, BODY_LIKE):
            res.renames[old.label] = new.label
    doc.elements[index] = new
    return res


def add_block(doc: GeoDocument, after: int | None, form: Form, geo: CompiledGeometry | None = None,
              strict_label: bool = False) -> BlockResult:
    """Add / Duplicate: el bloque de la pestaña, nuevo, detrás del bloque ``after`` (None: al final, antes de END).

    Si su etiqueta ya la tiene otro bloque, se le da una libre (Duplicate) o, con ``strict_label`` (un bloque nuevo
    escrito con New), se dice. Si nombra algo definido más abajo, baja hasta después de ello (PENGEOM exige que esté
    definido antes)."""
    new = element_of(form, doc, None)
    res = BlockResult(None)
    if isinstance(new, CtBoxElement) and (any(isinstance(e, CtBoxElement) for e in doc.elements)
                                          or (geo is not None and geo.ct is not None)):
        raise BlockError("PENGEOM admite una sola caja CT por geometría, y esta ya tiene una.")
    if not isinstance(new, IncludeElement):
        kind = "surface" if isinstance(new, SurfaceElement) else "body"
        try:
            _check_label_free(doc, new, geo)
        except BlockError:
            if strict_label:
                raise
            old_label = new.label
            new.label = LabelPool(doc, kind, geo).next()
            res.notes.append(f"La etiqueta «{old_label.strip()}» ya existe: el bloque nuevo es {block_title(new)}.")
    if isinstance(new, BodyElement | ModuleElement) and new.items:
        refs = sorted((r.keyword, r.label, r.side) for r in new.items)
        twin = next((o for o in doc.elements if type(o) is type(new)
                     and sorted((r.keyword, r.label, r.side) for r in o.items) == refs), None)
        if twin is not None:  # PENGEOM lo acepta, pero los dos ocupan el mismo sitio (las partículas verían uno)
            res.notes.append(f"Atención: tiene las mismas superficies, cuerpos y módulos que {block_title(twin)} y "
                             "ocupa el mismo sitio; cámbielos (las tablas) para que no se solapen.")
    end = _end_index(doc)
    pos = end if after is None else min(after + 1, end)
    surf, body = _definitions(doc)
    need_s, need_b = _needs(new)
    last = max([surf[s] for s in need_s if s in surf] + [body[b] for b in need_b if b in body], default=-1)
    if last >= pos:
        pos = min(last + 1, end)
        res.notes.append(f"Va detrás de {block_title(doc.elements[last])}: PENGEOM exige que lo que nombra un bloque "
                         "esté definido antes.")
    doc.elements.insert(pos, new)
    res.index = pos
    return res


def delete_block(doc: GeoDocument, index: int) -> BlockResult:
    """Delete: quita el bloque (con los comentarios que lo preceden). No se borra lo que otro bloque usa."""
    el = doc.elements[index]
    if isinstance(el, EndElement):
        raise BlockError("La línea END no se borra.")
    users = users_of(doc, el)
    if users:
        names = ", ".join(block_title(u) for u in users[:6]) + (" …" if len(users) > 6 else "")
        raise BlockError(f"{block_title(el)} lo usa{'n' if len(users) > 1 else ''} {names}: quite antes esa"
                         f"{'s' if len(users) > 1 else ''} referencia{'s' if len(users) > 1 else ''} (o borre "
                         f"{'esos bloques' if len(users) > 1 else 'ese bloque'}).")
    del doc.elements[index]
    return BlockResult(None)


def move_block(doc: GeoDocument, index: int, delta: int) -> BlockResult:
    """Move block up/down: cambia el bloque de sitio con el de arriba (``delta`` = -1) o el de abajo (+1)."""
    j = index + delta
    end = _end_index(doc)
    if not (0 <= index < end and 0 <= j < end):
        return BlockResult(index)
    doc.elements[index], doc.elements[j] = doc.elements[j], doc.elements[index]
    return BlockResult(j)


def relabel_plan(doc: GeoDocument, geo: CompiledGeometry) -> tuple[dict[int, str], dict[int, str]]:
    """Etiquetas nuevas (``id`` del elemento → 4 caracteres) con los números que les da PENGEOM: los de
    geometry.rep y los que usa penhmain en el .in (superficies por un lado; cuerpos, módulos, clones y CT por otro)."""
    surf: dict[int, str] = {}
    body: dict[int, str] = {}
    for s in geo.surfaces:
        el = s.element
        if s.origin == "user" and el is not None and s.document is doc and id(el) not in surf:
            surf[id(el)] = f"{s.index:4d}"
    for b in geo.bodies:
        el = b.element
        if el is None or b.document is not doc or id(el) in body:
            continue
        if b.origin == "clone" and not (isinstance(el, CloneElement) and el is b.clone_element):
            continue
        if b.origin in ("user", "ct", "clone"):
            body[id(el)] = f"{b.index:4d}"
    # Lo que no tenga número (no debería pasar) recibe uno libre por encima de todos: etiquetas siempre distintas.
    for kind, plan, cls in (("surface", surf, SurfaceElement), ("body", body, BODY_LIKE)):
        top = max((int(v) for v in plan.values()), default=0)
        for el in doc.elements:
            if isinstance(el, cls) and id(el) not in plan:
                top += 1
                plan[id(el)] = f"{top:4d}"
        if any(len(v.strip()) > 4 for v in plan.values()):
            raise BlockError(f"Hay más de 9999 {'superficies' if kind == 'surface' else 'cuerpos y módulos'}: las "
                             "etiquetas de 4 caracteres no bastan para numerarlos.")
    return surf, body


def relabel(doc: GeoDocument, geo: CompiledGeometry) -> BlockResult:
    """Relabel: las etiquetas pasan a ser los números de PENGEOM (``relabel_plan``), con todas sus referencias."""
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    surf, body = relabel_plan(doc, geo)
    s_map = {el.label: surf[id(el)] for el in doc.elements if isinstance(el, SurfaceElement)}
    b_map = {el.label: body[id(el)] for el in doc.elements if isinstance(el, BODY_LIKE)}
    # Un fichero incluido con INCLUDE* comparte las etiquetas con este: si nombra alguna que cambia, sus
    # referencias (que no se pueden cambiar desde aquí) pasarían a nombrar otra cosa.
    for inc in doc.elements:
        if not (isinstance(inc, IncludeElement) and inc.starred and inc.document is not None):
            continue
        for sel in inc.document.elements:
            refs = [(True, r.label) for r in getattr(sel, "items", []) if r.is_surface] + \
                [(False, r.label) for r in getattr(sel, "items", []) if not r.is_surface]
            if isinstance(sel, CloneElement):
                refs.append((False, sel.module_label))
            for is_surf, lab in refs:
                new = (s_map if is_surf else b_map).get(lab)
                if new is not None and new != lab:
                    raise BlockError(f"Relabel no se puede hacer: {inc.filename} (INCLUDE*) nombra "
                                     f"{'la superficie' if is_surf else 'el elemento'} «{lab.strip()}» de este "
                                     "fichero, y sus referencias no se pueden cambiar desde aquí (pasarían a nombrar "
                                     "otra cosa).")
            own = surf if isinstance(sel, SurfaceElement) else body if isinstance(sel, BODY_LIKE) else {}
            if (sel.label or "") in own.values():
                raise BlockError(f"Relabel no se puede hacer: la etiqueta nueva «{sel.label.strip()}» ya la tiene un "
                                 f"bloque de {inc.filename} (INCLUDE*), que comparte las etiquetas con este fichero.")
    res = BlockResult(None)
    for el in doc.elements:
        if isinstance(el, SurfaceElement):
            new = surf[id(el)]
            if new != el.label:
                el.label = new
                el.modified = True
        elif isinstance(el, BODY_LIKE):
            new = body[id(el)]
            if new != el.label:
                res.renames[el.label] = new
                el.label = new
                el.modified = True
        if isinstance(el, BodyElement | ModuleElement):
            for r in el.items:
                new = s_map.get(r.label) if r.is_surface else b_map.get(r.label)
                if new is not None and new != r.label:
                    r.label = new
                    el.modified = True
        elif isinstance(el, CloneElement):
            new = b_map.get(el.module_label)
            if new is not None and new != el.module_label:
                el.module_label = new
                el.modified = True
    return res


def free_label(doc: GeoDocument, tab: str, geo: CompiledGeometry | None = None) -> str:
    """La etiqueta que se propone al pulsar New (la que sigue a la mayor de su clase)."""
    if tab == TAB_INCLUDE:
        return ""
    kind = "surface" if tab in (TAB_SURFACE, TAB_IMPLICIT) else "body"
    return LabelPool(doc, kind, geo).next().strip()


def labels_of(doc: GeoDocument, kind: str) -> list[tuple[str, str]]:
    """(etiqueta, descripción) del fichero y de sus INCLUDE*, para completar lo que se escribe: ``kind`` 'surface'
    (superficies), 'body' (cuerpos: las líneas «BODY»), 'module' (módulos, clones y CT: las líneas «MODULE») o
    'any' (todo lo que tiene etiqueta de cuerpo)."""
    docs = [doc] + [el.document for el in doc.elements if isinstance(el, IncludeElement) and el.starred
                    and el.document is not None]
    kinds = {"surface": SurfaceElement, "body": BodyElement, "module": (ModuleElement, CloneElement, CtBoxElement),
             "any": BODY_LIKE}
    out: list[tuple[str, str]] = []
    for d in docs:
        for el in d.elements:
            if isinstance(el, kinds[kind]):
                out.append(((el.label or "").strip(), el.description.strip()))
    return out

