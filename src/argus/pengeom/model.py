"""Modelo sintáctico de un fichero de geometría PENGEOM (.geo).

El modelo conserva el texto original de cada línea y la posición de cada
elemento, de modo que un fichero no modificado se reescribe byte a byte. Los
valores numéricos se guardan tal como los leería PENGEOM (formato Fortran).

Terminología (manual de PENELOPE 2024, cap. 6):

* *Elemento*: bloque que empieza con SURFACE, SURFACE*, BODY, MODULE, CLONE,
  INCLUDE, INCLUDE*, CTBOX o END y termina en una línea de ceros.
* *Etiqueta*: cadena de 4 caracteres (columnas 10-13). Se compara tal cual,
  con blancos incluidos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core.diagnostics import DiagnosticList

# Palabras clave exactas (8 caracteres) que reconoce GEOMIN.
KW_SEPARATOR = "00000000"
KW_ONES = "11111111"
KW_SURFACE = "SURFACE "
KW_SURFACE_STAR = "SURFACE*"
KW_BODY = "BODY    "
KW_MODULE = "MODULE  "
KW_CLONE = "CLONE   "
KW_INCLUDE = "INCLUDE "
KW_INCLUDE_STAR = "INCLUDE*"
KW_CTBOX = "CTBOX   "
KW_END = "END     "
KW_INDICES = "INDICES="
KW_MATERIAL = "MATERIAL"
KW_FILE = "   FILE="
KW_CTFNAME = "CTFNAME="

# Parámetros de superficies en forma reducida y de transformaciones.
PARAM_KEYWORDS = {
    "X-SCALE=": "X-SCALE",
    "Y-SCALE=": "Y-SCALE",
    "Z-SCALE=": "Z-SCALE",
    "  OMEGA=": "OMEGA",
    "  THETA=": "THETA",
    "    PHI=": "PHI",
    "X-SHIFT=": "X-SHIFT",
    "Y-SHIFT=": "Y-SHIFT",
    "Z-SHIFT=": "Z-SHIFT",
}
# Coeficientes de la forma implícita.
COEF_KEYWORDS = {
    "    AXX=": "AXX",
    "    AXY=": "AXY",
    "    AXZ=": "AXZ",
    "    AYY=": "AYY",
    "    AYZ=": "AYZ",
    "    AZZ=": "AZZ",
    "     AX=": "AX",
    "     AY=": "AY",
    "     AZ=": "AZ",
    "     A0=": "A0",
}
# Palabra clave canónica (8 columnas) para cada nombre.
KEYWORD_OF = {v: k for k, v in {**PARAM_KEYWORDS, **COEF_KEYWORDS}.items()}

REDUCED_PARAMS = ("X-SCALE", "Y-SCALE", "Z-SCALE", "OMEGA", "THETA", "PHI",
                  "X-SHIFT", "Y-SHIFT", "Z-SHIFT")
TRANSFORM_PARAMS = ("OMEGA", "THETA", "PHI", "X-SHIFT", "Y-SHIFT", "Z-SHIFT")
ANGLE_PARAMS = ("OMEGA", "THETA", "PHI")
COEF_NAMES = ("AXX", "AXY", "AXZ", "AYY", "AYZ", "AZZ", "AX", "AY", "AZ", "A0")


class ElementKind(str, Enum):
    SURFACE = "SURFACE"
    BODY = "BODY"
    MODULE = "MODULE"
    CLONE = "CLONE"
    INCLUDE = "INCLUDE"
    CTBOX = "CTBOX"
    END = "END"


@dataclass
class ParamLine:
    """Línea ``NOMBRE=(valor, IP) [DEG|RAD]``."""

    name: str  # 'X-SCALE', 'OMEGA', 'AXX'...
    value: float
    ipar: int = 0  # índice PARINP (>0: el valor se sustituye desde el .in)
    unit: str = "DEG"  # solo relevante para ángulos: 'DEG' o 'RAD'
    line: int | None = None

    @property
    def is_angle(self) -> bool:
        return self.name in ANGLE_PARAMS

    def radians(self) -> float:
        import math

        if self.unit == "RAD":
            return self.value
        return self.value * math.pi / 180.0


@dataclass
class RefLine:
    """Referencia dentro de un cuerpo o módulo."""

    keyword: str  # 'SURFACE', 'SURFACE*', 'BODY', 'MODULE'
    label: str  # 4 caracteres, tal cual
    side: int | None = None  # puntero de lado (-1/+1) para superficies
    line: int | None = None

    @property
    def is_surface(self) -> bool:
        return self.keyword in ("SURFACE", "SURFACE*")


@dataclass
class Element:
    kind: ElementKind
    label: str | None = None  # 4 caracteres (None en INCLUDE y END)
    description: str = ""  # texto tras la etiqueta en la primera línea
    first_line: int = 0  # línea de la palabra clave
    last_line: int = 0  # última línea del elemento (antes del separador)
    terminator_line: int | None = None  # línea de ceros que lo cierra
    lead_line: int | None = None  # primera línea de los comentarios que lo preceden
    modified: bool = False  # True si hay que regenerar su texto

    @property
    def display_label(self) -> str:
        return (self.label or "").strip()


@dataclass
class SurfaceElement(Element):
    starred: bool = False
    indices: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
    params: list[ParamLine] = field(default_factory=list)  # antes de '1111'
    has_ones_line: bool = False
    transform: list[ParamLine] = field(default_factory=list)  # tras '1111'

    @property
    def implicit(self) -> bool:
        return all(i == 0 for i in self.indices)

    def param(self, name: str, default: float) -> float:
        value = default
        for p in self.params:
            if p.name == name:
                value = p.radians() if p.is_angle else p.value
        return value


@dataclass
class BodyElement(Element):
    material: int = 0
    material_line: int | None = None
    items: list[RefLine] = field(default_factory=list)


@dataclass
class ModuleElement(Element):
    material: int = 0
    material_line: int | None = None
    items: list[RefLine] = field(default_factory=list)
    has_ones_line: bool = False
    transform: list[ParamLine] = field(default_factory=list)


@dataclass
class CloneElement(Element):
    module_label: str = ""
    module_line: int | None = None
    module_note: str = ""  # lo que va detrás de «MODULE  (   6)» (columnas 15-70; PENGEOM no lo lee)
    has_ones_line: bool = False
    transform: list[ParamLine] = field(default_factory=list)


@dataclass
class IncludeElement(Element):
    starred: bool = False
    filename: str = ""
    file_line: int | None = None
    document: GeoDocument | None = None  # documento incluido ya leído


@dataclass
class CtBoxElement(Element):
    filename: str = ""
    file_line: int | None = None
    has_ones_line: bool = False
    transform: list[ParamLine] = field(default_factory=list)


@dataclass
class EndElement(Element):
    pass


def transform_values(params: list[ParamLine]) -> tuple[float, float, float, float, float, float]:
    """(omega, theta, phi) en radianes y (dx, dy, dz) a partir de líneas."""
    vals = {"OMEGA": 0.0, "THETA": 0.0, "PHI": 0.0, "X-SHIFT": 0.0, "Y-SHIFT": 0.0, "Z-SHIFT": 0.0}
    for p in params:
        if p.name in vals:
            vals[p.name] = p.radians() if p.is_angle else p.value
    return (vals["OMEGA"], vals["THETA"], vals["PHI"],
            vals["X-SHIFT"], vals["Y-SHIFT"], vals["Z-SHIFT"])


@dataclass
class GeoDocument:
    """Fichero .geo leído, con su texto original y sus elementos."""

    path: str | None
    lines: list[str]  # líneas originales, cada una con su fin de línea
    header_end: int | None = None  # línea de ceros que cierra la cabecera
    elements: list[Element] = field(default_factory=list)
    diagnostics: DiagnosticList = field(default_factory=DiagnosticList)
    newline: str = "\n"
    included: bool = False  # True si es un fichero INCLUDE
    # Elementos que el parser dejó a medias por un error de formato, con lo que se leyó antes del error (para dar,
    # como PENGEOM, el primer error del fichero aunque sea de otro tipo: compiler._error_before_format_error).
    broken: list[Element] = field(default_factory=list)

    @property
    def header_lines(self) -> list[str]:
        end = self.header_end if self.header_end is not None else len(self.lines)
        return self.lines[:end]

    @property
    def title(self) -> str:
        for ln in self.header_lines:
            text = ln.strip()
            if text:
                return text
        return ""

    def surfaces(self) -> list[SurfaceElement]:
        return [e for e in self.elements if isinstance(e, SurfaceElement)]

    def bodies(self) -> list[BodyElement]:
        return [e for e in self.elements if isinstance(e, BodyElement)]

    def modules(self) -> list[ModuleElement]:
        return [e for e in self.elements if isinstance(e, ModuleElement)]

    @property
    def ok(self) -> bool:
        return not self.diagnostics.has_errors
