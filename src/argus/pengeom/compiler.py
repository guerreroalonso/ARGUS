"""Compilador de geometrías PENGEOM: reproduce las tablas internas de GEOMIN.

A partir del modelo sintáctico (:mod:`argus.pengeom.model`) construye las
mismas tablas que GEOMIN deja en memoria (pengeom.f 2024):

* superficies numeradas (``KS``) con sus 10 coeficientes y ``KPLANE``,
* cuerpos y módulos numerados (``KB``) con material, madre (``KMOTH``),
  hijas (``KDGHT``), superficies (``KSURF``), indicadores (``KFLAG``) y
  cuerpos limitantes (``KBODY``),
* clones, ficheros incluidos, cajas CT, envolvente automática y eliminación
  de superficies duplicadas.

La numeración es la de PENGEOM (1-based) y coincide con la de
``geometry.rep``. El orden de las operaciones y las rarezas del código
Fortran se reproducen a propósito: las tablas se verifican contra la sonda
PENGEOM (``argus.oracle``) para todo el corpus de pruebas.

Valores de KFLAG (manual de PENELOPE, cap. 6):
1 = superficie limitante con puntero de lado -1; 2 = puntero +1;
3 = superficie de un cuerpo limitante; 4 = superficie de una hija de un
módulo; 5 = otras.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.diagnostics import Diagnostic, DiagnosticList, Severity
from .ct import CtData, CtFormatError, load_ct
from .model import (
    BodyElement,
    CloneElement,
    CtBoxElement,
    Element,
    EndElement,
    GeoDocument,
    IncludeElement,
    ModuleElement,
    ParamLine,
    SurfaceElement,
)
from .quadric import COEF_INDEX, PI, Quadric, is_plane, reduced_to_implicit, rotshf

# Dimensiones de PENGEOM_mod.
NS = 10000
NB = 5000
NXG = 250
# Tolerancia de GEOMIN para superficies duplicadas.
DUPLICATE_TOL = 1.0e-14
# Envolvente automática: esfera de radio 1e7 centrada en el origen.
ENCLOSURE_QUADRIC: Quadric = (1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0e14)
# Caracteres que GEOMIN añade a las etiquetas de los ficheros incluidos.
_CA = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# ---------------------------------------------------------------------------
# Tablas
# ---------------------------------------------------------------------------

@dataclass
class SurfaceEntry:
    index: int  # KS (1-based)
    alias: str  # 5 caracteres: etiqueta + sufijo ('0' en el fichero principal)
    q: Quadric
    kplane: int = 0
    starred: bool = False  # SURFACE* (KSFF): no se mueve con los módulos
    origin: str = "user"  # 'user', 'clone', 'ct', 'enclosure'
    element: SurfaceElement | None = None
    document: GeoDocument | None = None
    clone_of: int | None = None  # superficie original si origin == 'clone'
    clone_element: CloneElement | None = None
    replaced_by: int | None = None  # duplicada eliminada: sustituida por esta

    @property
    def label(self) -> str:
        return self.alias[:4]


@dataclass
class BodyEntry:
    index: int  # KB (1-based)
    alias: str  # 5 caracteres
    is_module: bool = False
    material: int = 0
    mother: int = 0
    daughters: list[int] = field(default_factory=list)
    surfaces: list[int] = field(default_factory=list)
    flags: list[int] = field(default_factory=list)
    limiting: list[int] = field(default_factory=list)
    origin: str = "user"  # 'user', 'clone', 'ct', 'enclosure'
    element: Element | None = None
    document: GeoDocument | None = None
    clone_of: int | None = None
    clone_element: CloneElement | None = None
    # El INCLUDE (o INCLUDE*) del fichero principal de donde viene el cuerpo (None si es del principal). Dos INCLUDE
    # del mismo fichero son dos cuerpos distintos: su identidad (``pengeom.identity``) sale de aquí.
    include: IncludeElement | None = None

    @property
    def label(self) -> str:
        return self.alias[:4]

    @property
    def kind(self) -> str:
        if self.origin == "enclosure":
            return "envolvente"
        if self.origin == "ct":
            return "CT"
        return "módulo" if self.is_module else "cuerpo"

    def own_surfaces(self) -> Iterator[tuple[int, int]]:
        """Superficies que lo limitan directamente: (KS, lado -1/+1)."""
        for ks, fl in zip(self.surfaces, self.flags):
            if fl == 1:
                yield ks, -1
            elif fl == 2:
                yield ks, +1


@dataclass
class CompiledGeometry:
    document: GeoDocument
    surfaces: list[SurfaceEntry] = field(default_factory=list)
    bodies: list[BodyEntry] = field(default_factory=list)
    nmat: int = 0  # NMATG
    diagnostics: DiagnosticList = field(default_factory=DiagnosticList)
    ok: bool = False
    enclosure_added: bool = False
    ct: CtData | None = None
    ct_body: int | None = None
    ct_transform: tuple[float, float, float, float, float, float] | None = None
    ct_file: str | None = None  # nombre del fichero .ct (CTFNAME)
    ct_path: Path | None = None  # dónde se ha leído (None si lo dio ct_loader)
    parinp_used: list[int] = field(default_factory=list)  # índices PARINP usados
    parinp: list[float] = field(default_factory=list)  # valores PARINP con los que se ha compilado

    @property
    def nsurf(self) -> int:
        return len(self.surfaces)

    @property
    def nbodys(self) -> int:
        return len(self.bodies)

    def surface(self, ks: int) -> SurfaceEntry:
        return self.surfaces[ks - 1]

    def body(self, kb: int) -> BodyEntry:
        return self.bodies[kb - 1]

    def balias(self, kb: int) -> str:
        """Etiqueta de usuario (BALIAS) o '' si PENGEOM no la conserva."""
        a = self.bodies[kb - 1].alias
        return a[:4] if a[4] == "0" and self.bodies[kb - 1].origin != "enclosure" else ""

    def coefficients(self) -> np.ndarray:
        return np.array([s.q for s in self.surfaces], dtype=np.float64).reshape(-1, 10)

    def kplane_array(self) -> np.ndarray:
        return np.array([s.kplane for s in self.surfaces], dtype=np.int32)

    def materials(self) -> list[int]:
        """Materiales usados por cuerpos (sin 0 ni la marca CT 9999)."""
        return sorted({b.material for b in self.bodies if 0 < b.material < 9999})

    def used_materials(self) -> list[int]:
        """Materiales que usa la geometría: los de los cuerpos y los de los vóxeles del CT."""
        out = set(self.materials())
        if self.ct is not None:
            out |= set(self.ct.materials())
        return sorted(out)

    def find_body(self, label: str) -> BodyEntry | None:
        lab = label.rjust(4)[:4]
        for b in self.bodies:
            if b.alias == lab + "0":
                return b
        return None


def body_title(geo: CompiledGeometry, kb: int) -> str:
    """Nombre para mostrar. La etiqueta de usuario es la que se usa en el .in
    (penhmain identifica los cuerpos por su etiqueta, BALIAS)."""
    if kb > geo.nbodys:
        return "Nivel superior (fuera de todo)"
    b = geo.body(kb)
    lab = geo.balias(kb).strip()
    if b.origin == "enclosure":
        return f"Envolvente (automática, n.º {kb})"
    if b.origin == "ct":
        kind = "CT"
    else:
        kind = "Módulo" if b.is_module else "Cuerpo"
    if lab:
        text = f"{kind} «{lab}»"
        if lab != str(kb):
            text += f" (n.º {kb})"
    else:
        text = f"{kind} n.º {kb}"
    if b.origin == "clone":
        text += f", clon de {b.clone_of}"
    return text


class _CompileError(Exception):
    def __init__(self, code: int | None, message: str, reason: str | None = None,
                 doc: GeoDocument | None = None, line: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.doc = doc
        self.line = line


# ---------------------------------------------------------------------------
# Compilador
# ---------------------------------------------------------------------------

class _Compiler:
    def __init__(self, doc: GeoDocument, parinp: Sequence[float] | None,
                 base_dir: Path | None, ct_loader: Callable[[str], CtData] | None):
        self.doc = doc
        self.parinp = list(parinp or [])
        self.base_dir = base_dir
        self.ct_loader = ct_loader
        self.geo = CompiledGeometry(document=doc)
        self.S = self.geo.surfaces
        self.B = self.geo.bodies
        self.nmat = 0
        self.lct = False
        self.nincl = 0
        self.c1 = "0"
        self.keepl = False
        self.cur_doc = doc
        self.cur_main = True
        self.cur_include: IncludeElement | None = None  # el INCLUDE que se está leyendo
        self.parinp_used: set[int] = set()
        # Índices de búsqueda (primera coincidencia, como los bucles de GEOMIN).
        self.surface_by_alias: dict[str, int] = {}
        self.body_by_alias: dict[str, int] = {}

    # -- utilidades -------------------------------------------------------
    def fail(self, code: int | None, message: str, reason: str | None = None,
             line: int | None = None) -> None:
        raise _CompileError(code, message, reason, self.cur_doc, line)

    def suffix(self) -> str:
        if self.cur_main or self.keepl:
            return "0"
        return self.c1

    def value(self, p: ParamLine, code: int) -> float:
        """Valor efectivo de un parámetro (sustitución PARINP)."""
        if p.ipar > 0:
            if p.ipar <= len(self.parinp):
                self.parinp_used.add(p.ipar)
                return float(self.parinp[p.ipar - 1])
            self.fail(code, f"El parámetro usa PARINP({p.ipar}), pero el fichero .in solo define "
                            f"{len(self.parinp)} valores PARINP.",
                      "GEOMIN: NPINP is too small (check PARINP).", p.line)
        return p.value

    def angle(self, p: ParamLine, code: int) -> float:
        v = self.value(p, code)
        return v if p.unit == "RAD" else v * PI / 180.0

    def transform(self, params: list[ParamLine], code: int) -> tuple[float, ...]:
        om = th = ph = dx = dy = dz = 0.0
        for p in params:
            if p.name == "OMEGA":
                om = self.angle(p, code)
            elif p.name == "THETA":
                th = self.angle(p, code)
            elif p.name == "PHI":
                ph = self.angle(p, code)
            elif p.name == "X-SHIFT":
                dx = self.value(p, code)
            elif p.name == "Y-SHIFT":
                dy = self.value(p, code)
            elif p.name == "Z-SHIFT":
                dz = self.value(p, code)
        return om, th, ph, dx, dy, dz

    def find_surface(self, alias: str) -> int | None:
        return self.surface_by_alias.get(alias)

    def find_body_before_last(self, alias: str) -> int | None:
        """Búsqueda de GEOMIN en KB0=1..NBODYS-1 (excluye el elemento en curso)."""
        kb = self.body_by_alias.get(alias)
        if kb is None or kb >= len(self.B):
            return None
        return kb

    def undefined_ref(self, code: int, message: str, line: int | None) -> None:
        """Una referencia a un cuerpo o módulo que no está definido antes. GEOMIN solo la busca si ya hay otros
        elementos (``IF(NBODYS.GT.1)``, pengeom.f 925, 973 y 1222): en el primero no la comprueba y sigue con datos
        que no existen. ARGUS la rechaza igual, pero sin el código de PENGEOM (décima revisión)."""
        if len(self.B) <= 1:
            self.fail(None, message + " PENGEOM no lo comprueba en el primer cuerpo o módulo del fichero: seguiría con "
                                      "datos que no existen.", None, line)
        self.fail(code, message, "GEOMIN: Undefined body label.", line)

    def check_new_body_label(self, alias: str, code: int, line: int | None) -> None:
        if alias in self.body_by_alias:
            self.fail(code, f"La etiqueta '{alias[:4].strip()}' ya se usa en otro cuerpo o módulo.",
                      "GEOMIN: Same label for two bodies (or modules).", line)

    def new_body(self, alias: str, el: Element | None, code_nb: int) -> BodyEntry:
        if len(self.B) + 1 > NB:
            self.fail(code_nb, f"Demasiados cuerpos y módulos (máximo NB={NB}).",
                      "GEOMIN: The parameter NB must be increased.", el.first_line if el else None)
        b = BodyEntry(index=len(self.B) + 1, alias=alias, element=el, document=self.cur_doc,
                      include=self.cur_include)
        self.B.append(b)
        self.body_by_alias.setdefault(alias, b.index)
        return b

    def new_surface(self, alias: str, q: Quadric, **kw) -> SurfaceEntry:
        s = SurfaceEntry(index=len(self.S) + 1, alias=alias, q=q, **kw)
        self.S.append(s)
        self.surface_by_alias.setdefault(alias, s.index)
        return s

    # -- elementos --------------------------------------------------------
    def run(self) -> None:
        for el in self.doc.elements:
            if isinstance(el, EndElement):
                self.end()
                return
            self.element(el)
        # Sin END: el parser ya lo diagnostica; no se completa la geometría.
        self.fail(None, "Falta la línea END.")

    def element(self, el: Element) -> None:
        if isinstance(el, SurfaceElement):
            self.surface(el)
        elif isinstance(el, BodyElement):
            self.body(el)
        elif isinstance(el, ModuleElement):
            self.module(el)
        elif isinstance(el, CloneElement):
            self.clone(el)
        elif isinstance(el, IncludeElement):
            self.include(el)
        elif isinstance(el, CtBoxElement):
            self.ctbox(el)

    def surface(self, el: SurfaceElement) -> None:
        alias = el.label + self.suffix()
        if self.find_surface(alias) is not None:
            self.fail(2004, f"La etiqueta de superficie '{el.label.strip()}' está repetida.",
                      "GEOMIN: Same label for two surfaces.", el.first_line)
        if len(self.S) + 1 > NS:
            self.fail(2005, f"Demasiadas superficies (máximo NS={NS}).",
                      "GEOMIN: The parameter NS must be increased.", el.first_line)
        if not el.implicit:
            xs = ys = zs = 1.0
            om = th = ph = dx = dy = dz = 0.0
            for p in el.params:
                if p.name in ("X-SCALE", "Y-SCALE", "Z-SCALE"):
                    v = self.value(p, 2008)
                    if v < 1.0e-15:
                        code = {"X-SCALE": 2009, "Y-SCALE": 2010, "Z-SCALE": 2011}[p.name]
                        self.fail(code, f"{p.name} menor que 1.0E-15.",
                                  "GEOMIN: Scale factor less than 1.0D-15.", p.line)
                    if p.name == "X-SCALE":
                        xs = v
                    elif p.name == "Y-SCALE":
                        ys = v
                    else:
                        zs = v
                elif p.name == "OMEGA":
                    om = self.angle(p, 2008)
                elif p.name == "THETA":
                    th = self.angle(p, 2008)
                elif p.name == "PHI":
                    ph = self.angle(p, 2008)
                elif p.name == "X-SHIFT":
                    dx = self.value(p, 2008)
                elif p.name == "Y-SHIFT":
                    dy = self.value(p, 2008)
                elif p.name == "Z-SHIFT":
                    dz = self.value(p, 2008)
            q = rotshf(om, th, ph, dx, dy, dz, reduced_to_implicit(el.indices, xs, ys, zs))
        else:
            coefs = [0.0] * 10
            for p in el.params:
                coefs[COEF_INDEX[p.name]] = self.value(p, 2013)
            q = tuple(coefs)  # type: ignore[assignment]
            if el.has_ones_line:
                q = rotshf(*self.transform(el.transform, 2015), q)
            if not any(q):
                self.geo.diagnostics.warning(
                    f"La superficie «{el.label.strip()}» tiene los 10 coeficientes nulos (F = 0 en todo el espacio): "
                    "no define ninguna superficie. PENGEOM no avisa y lo que hace depende de cómo se compile (con "
                    "«gfortran -Os», como indica penhmain.f, y en ARGUS, se toma por repetida de otra superficie). "
                    "Corrija sus coeficientes o los PARINP que los dan.", file=self.cur_doc.path, line=el.first_line)
        self.new_surface(alias, q, kplane=1 if is_plane(q) else 0, starred=el.starred,
                         element=el, document=self.cur_doc)

    def _add_surface_ref(self, b: BodyEntry, ks: int, side: int | None, codes: tuple[int, int, int],
                         line: int | None) -> None:
        """Superficie limitante con puntero de lado (cuerpo o módulo)."""
        pos = None
        for k, s in enumerate(b.surfaces):
            if s == ks:
                if b.flags[k] < 3:
                    self.fail(codes[0], f"La superficie {ks} aparece dos veces en el mismo elemento.",
                              "GEOMIN: The last limiting surface has been defined twice.", line)
                pos = k
                break
        if pos is None:
            if len(b.surfaces) + 1 >= NXG:
                self.fail(codes[1], f"Demasiadas superficies en un elemento (máximo {NXG - 1}).",
                          "GEOMIN: The number of limiting surfaces is too large.", line)
            b.surfaces.append(ks)
            b.flags.append(5)
            pos = len(b.surfaces) - 1
        if side == -1:
            b.flags[pos] = 1
        elif side == 1:
            b.flags[pos] = 2
        else:
            self.fail(codes[2], f"Puntero de lado no válido ({side}); debe ser -1 o +1.",
                      "GEOMIN: Check side pointer value.", line)

    def _append_surfaces(self, b: BodyEntry, src: BodyEntry, max_flag: int, new_flag: int,
                         code: int, line: int | None, strict_ge: bool = True) -> None:
        for ks, fl in zip(list(src.surfaces), list(src.flags)):
            if fl > max_flag:
                continue
            if ks in b.surfaces:
                continue
            if len(b.surfaces) + 1 >= NXG:
                self.fail(code, f"Demasiadas superficies en un elemento (máximo {NXG - 1}).",
                          "GEOMIN: The number of limiting surfaces is too large.", line)
            b.surfaces.append(ks)
            b.flags.append(new_flag)

    def body(self, el: BodyElement) -> None:
        sfx = self.suffix()
        alias = el.label + sfx
        self.check_new_body_label(alias, 2018, el.first_line)
        b = self.new_body(alias, el, 2019)
        imat = max(el.material, 0)
        b.material = imat
        self.nmat = max(self.nmat, imat)
        for it in el.items:
            a = it.label + sfx
            if it.is_surface:
                ks = self.find_surface(a)
                if ks is None:
                    self.fail(2021, f"La superficie '{it.label.strip()}' no está definida (antes de este cuerpo).",
                              "GEOMIN: Undefined surface label.", it.line)
                self._add_surface_ref(b, ks, it.side, (2022, 2023, 2024), it.line)
            elif it.keyword == "BODY":
                kb = self.find_body_before_last(a)
                if kb is None:
                    self.undefined_ref(2025, f"El cuerpo limitante '{it.label.strip()}' no está definido antes.",
                                       it.line)
                src = self.B[kb - 1]
                if src.is_module:
                    self.fail(2026, f"'{it.label.strip()}' es un módulo; use 'MODULE  (...)' para referirse a él.",
                              "GEOMIN: This body is a module.", it.line)
                self._append_surfaces(b, src, 99, 3, 2027, it.line)
                b.limiting.append(kb)
            elif it.keyword == "MODULE":
                kb = self.find_body_before_last(a)
                if kb is None:
                    self.undefined_ref(2028, f"El módulo limitante '{it.label.strip()}' no está definido antes.",
                                       it.line)
                src = self.B[kb - 1]
                if not src.is_module:
                    self.fail(2029, f"'{it.label.strip()}' es un cuerpo; use 'BODY    (...)' para referirse a él.",
                              "GEOMIN: This module is a body.", it.line)
                self._append_surfaces(b, src, 2, 3, 2030, it.line)
                b.limiting.append(kb)

    def module(self, el: ModuleElement) -> None:
        sfx = self.suffix()
        alias = el.label + sfx
        self.check_new_body_label(alias, 2033, el.first_line)
        m = self.new_body(alias, el, 2034)
        imat = el.material
        if imat < 0:
            imat = 0
        m.material = imat
        if imat == 9999:
            self.lct = True
        else:
            self.nmat = max(self.nmat, imat)
        m.daughters = [m.index]
        for it in el.items:
            a = it.label + sfx
            if it.is_surface:
                ks = self.find_surface(a)
                if ks is None:
                    self.fail(2036, f"La superficie '{it.label.strip()}' no está definida (antes de este módulo).",
                              "GEOMIN: Undefined surface label.", it.line)
                self._add_surface_ref(m, ks, it.side, (2037, 2038, 2039), it.line)
            elif it.keyword == "BODY":
                kb = self.find_body_before_last(a)
                if kb is None:
                    self.undefined_ref(2040, f"El cuerpo '{it.label.strip()}' no está definido antes de este módulo.",
                                       it.line)
                d = self.B[kb - 1]
                if d.is_module:
                    self.fail(2041, f"'{it.label.strip()}' es un módulo; use 'MODULE  (...)'.",
                              "GEOMIN: This body is a module.", it.line)
                if d.mother > 0 and d.mother != m.index:
                    self.fail(2042, f"El cuerpo '{it.label.strip()}' ya está dentro de {self._block_name(d.mother)}; "
                                    "un cuerpo solo puede estar en un módulo.",
                              "GEOMIN: You are trying to assign two mothers to the last body.", it.line)
                d.mother = m.index
                m.daughters.append(kb)
                kdt = len(m.daughters)
                for lb in d.limiting:
                    if self.B[lb - 1].mother == 0:
                        self.B[lb - 1].mother = m.index
                # Rareza de GEOMIN: si un cuerpo limitante no figura entre las
                # hijas, se añade otra vez el propio cuerpo KB (no el limitante).
                for lb in d.limiting:
                    if self.B[lb - 1].mother == 0:
                        self.B[lb - 1].mother = m.index
                    if lb not in m.daughters[:kdt]:
                        m.daughters.append(kb)
                self._append_surfaces(m, d, 3, 4, 2043, it.line)
            elif it.keyword == "MODULE":
                kb = self.find_body_before_last(a)
                if kb is None:
                    self.undefined_ref(2044, f"El módulo '{it.label.strip()}' no está definido antes de este módulo.",
                                       it.line)
                d = self.B[kb - 1]
                if not d.is_module:
                    self.fail(2045, f"'{it.label.strip()}' es un cuerpo; use 'BODY    (...)'.",
                              "GEOMIN: This module is a body.", it.line)
                if d.mother > 0 and d.mother != m.index:
                    self.fail(2046, f"El módulo '{it.label.strip()}' ya está dentro de {self._block_name(d.mother)}; "
                                    "un módulo solo puede estar en otro.",
                              "GEOMIN: You are trying to assign two mothers to the last module.", it.line)
                d.mother = m.index
                m.daughters.append(kb)
                self._append_surfaces(m, d, 2, 4, 2047, it.line)
        m.daughters.sort()
        if el.has_ones_line:
            t = self.transform(el.transform, 2049)
            self._transform_descendants(m.index, t)
        m.is_module = True

    def _transform_descendants(self, kbm: int, t: tuple[float, ...]) -> None:
        """Mueve las superficies del módulo y de todos sus descendientes
        (cuerpos cuya cadena de madres llega al módulo, incluido él mismo)."""
        done: set[int] = set()
        reach: dict[int, bool] = {kbm: True}
        B = self.B
        for b in B:
            chain = []
            kb = b.index
            while kb > 0 and kb not in reach:
                chain.append(kb)
                kb = B[kb - 1].mother
            r = reach.get(kb, False) if kb > 0 else False
            for c in chain:
                reach[c] = r
            if not r:
                continue
            for ks, fl in zip(b.surfaces, b.flags):
                if fl < 5 and ks not in done:
                    s = self.S[ks - 1]
                    if not s.starred:
                        s.q = rotshf(*t, s.q)
                        done.add(ks)

    def clone(self, el: CloneElement) -> None:
        sfx = self.suffix()
        alias_c = el.label + sfx
        self.check_new_body_label(alias_c, 2052, el.first_line)
        target = el.module_label + sfx
        if not self.B:
            self.fail(2054, "No hay ningún módulo definido que clonar.",
                      "GEOMIN: This module is not defined.", el.module_line)
        korig = None
        for b in self.B:
            if b.alias == target:
                if not b.is_module:
                    self.fail(2055, f"'{el.module_label.strip()}' es un cuerpo; solo se pueden clonar módulos.",
                              "GEOMIN: The selected object is a body.", el.module_line)
                korig = b.index
                break
        if korig is None:
            self.fail(2056, f"'{el.module_label.strip()}' no corresponde a ningún módulo.",
                      "GEOMIN: The label does not correspond to a module.", el.module_line)
        t = self.transform(el.transform, 2058) if el.has_ones_line else (0.0,) * 6
        # Descendientes (búsqueda en anchura de GEOMIN).
        idesc = [korig]
        idone = [False]
        while True:
            ndc = len(idesc)
            kdg = 0
            for i in range(ndc):
                if not idone[i]:
                    kb = idesc[i]
                    b = self.B[kb - 1]
                    if b.limiting:
                        for lb in b.limiting:
                            idesc.append(lb)
                            idone.append(False)
                            kdg += 1
                    elif b.daughters:
                        for d in b.daughters:
                            if d != kb:
                                idesc.append(d)
                                idone.append(False)
                                kdg += 1
                    idone[i] = True
            if kdg == 0:
                break
        desc = set(idesc)
        nbodys = len(self.B)
        ibcl: dict[int, int] = {}
        ibor: dict[int, int] = {}
        iscl: dict[int, int] = {}
        kbd = nbodys
        new_surfaces: list[SurfaceEntry] = []
        for kbb in range(1, nbodys + 1):
            if kbb not in desc:
                continue
            kbd += 1
            ibcl[kbb] = kbd
            ibor[kbd] = kbb
            src = self.B[kbb - 1]
            for kss in range(len(src.surfaces) - 1, -1, -1):
                ks = src.surfaces[kss]
                if src.flags[kss] < 3 and ks not in iscl:
                    s0 = self.S[ks - 1]
                    if not s0.starred:
                        ksd = len(self.S) + 1
                        if ksd > NS:
                            self.fail(2060, f"Demasiadas superficies (máximo NS={NS}).",
                                      "GEOMIN: The parameter NS must be increased.", el.first_line)
                        iscl[ks] = ksd
                        new = self.new_surface("    0", rotshf(*t, s0.q), kplane=0, starred=False,
                                               origin="clone", element=s0.element, document=s0.document,
                                               clone_of=ks, clone_element=el)
                        new_surfaces.append(new)
                    else:
                        iscl[ks] = ks
        for kb in range(nbodys + 1, kbd + 1):
            if kb > NB:
                self.fail(2061, f"Demasiados cuerpos y módulos (máximo NB={NB}).",
                          "GEOMIN: The parameter NB must be increased.", el.first_line)
            kbo = ibor[kb]
            o = self.B[kbo - 1]
            nb = BodyEntry(index=kb, alias="    0", is_module=o.is_module, material=o.material,
                           origin="clone", element=o.element, document=o.document, clone_of=kbo,
                           clone_element=el, include=self.cur_include)
            nb.mother = ibcl.get(o.mother, 0) if o.mother > 0 else 0
            nb.surfaces = [iscl.get(ks, 0) for ks in o.surfaces]
            nb.flags = list(o.flags)
            self.B.append(nb)
            self.body_by_alias.setdefault(nb.alias, nb.index)
            if not o.is_module:
                nb.limiting = [ibcl.get(x, 0) for x in o.limiting]
                for x in nb.limiting:
                    if x >= kb:
                        self.fail(2063, "Clon incoherente: un cuerpo limitante no está definido antes.",
                                  "GEOMIN: The limiting body or module is not yet defined", el.first_line)
            else:
                nb.daughters = [ibcl.get(x, 0) for x in o.daughters]
                for x in nb.daughters:
                    if x != kb and x >= kb:
                        self.fail(2064, "Clon incoherente: una hija no está definida antes.",
                                  "GEOMIN: The limiting body or module is not yet defined", el.first_line)
        top = self.B[kbd - 1]
        blank = top.alias
        top.alias = alias_c
        if self.body_by_alias.get(blank) == top.index:
            # La copia principal se registró con la etiqueta en blanco antes de renombrarla (clon de un módulo sin
            # hijas): en GEOMIN ya no hay ningún cuerpo con esa etiqueta.
            first = next((b.index for b in self.B if b.alias == blank), None)
            if first is None:
                del self.body_by_alias[blank]
            else:
                self.body_by_alias[blank] = first
        self.body_by_alias.setdefault(alias_c, top.index)
        top.mother = 0
        top.element = el
        top.document = self.cur_doc
        top.include = self.cur_include

    def _block_name(self, kb: int) -> str:
        """El bloque del cuerpo ``kb`` como lo escribe el usuario («MODULE (55)»), no su número de PENGEOM."""
        b = self.B[kb - 1]
        el = b.element
        if isinstance(el, CloneElement):
            kw = "CLONE"
        elif isinstance(el, CtBoxElement):
            kw = "CTBOX"
        else:
            kw = "MODULE" if b.is_module else "BODY"
        if b.origin == "clone" and el is not b.clone_element:
            return f"una copia del clon {b.clone_element.label.strip()}" if b.clone_element is not None \
                else f"el cuerpo n.º {kb}"
        where = f" de {Path(b.document.path).name}" if b.document is not None and b.document is not self.doc \
            and b.document.path else ""
        return f"{kw} ({(el.label if el is not None else b.alias[:4]).strip()}){where}"

    def include(self, el: IncludeElement) -> None:
        if not self.cur_main:
            self.fail(2067, "Solo se admite un nivel de INCLUDE.", "GEOMIN: Too many include levels.",
                      el.first_line)
        sub = el.document
        if sub is None:
            self.fail(None, f"No se ha podido leer el fichero incluido '{el.filename}'.", None, el.file_line)
        self.keepl = el.starred
        if not el.starred:
            self.nincl += 1
            if self.nincl > 35:
                self.fail(2068, "Demasiados ficheros incluidos (máximo 35).",
                          "GEOMIN: Too many include levels.", el.first_line)
            self.c1 = _CA[self.nincl]
        prev_doc = self.cur_doc
        self.cur_doc = sub
        self.cur_main = False
        self.cur_include = el
        ended = False
        for sel in sub.elements:
            if isinstance(sel, EndElement):
                ended = True
                break
            if isinstance(sel, IncludeElement):
                self.fail(2067, "Un fichero incluido no puede incluir otros ficheros.",
                          "GEOMIN: Too many include levels.", sel.first_line)
            self.element(sel)
        if not ended:
            self.fail(None, f"El fichero incluido '{el.filename}' no termina con END.", None, None)
        self.cur_doc = prev_doc
        self.cur_main = True
        self.cur_include = None
        self.c1 = "0"

    def _load_ct(self, filename: str, line: int | None) -> CtData:
        if self.ct_loader is not None:
            try:
                return self.ct_loader(filename)
            except FileNotFoundError:
                self.fail(2106, f"No existe el fichero CT '{filename}'.",
                          "GEOMIN: CT definition file does not exist.", line)
        base = self.base_dir or Path(".")
        path = base / filename
        self.geo.ct_path = path
        if not path.is_file():
            self.fail(2106, f"No existe el fichero CT '{filename}' en {base}.",
                      "GEOMIN: CT definition file does not exist.", line)
        try:
            return load_ct(path)
        except (CtFormatError, ValueError, OSError) as exc:
            self.fail(None, f"No se puede leer el fichero CT '{filename}': {exc}", None, line)
        raise AssertionError  # pragma: no cover

    def ctbox(self, el: CtBoxElement) -> None:
        sfx = self.suffix()
        alias = el.label + sfx
        self.check_new_body_label(alias, 2102, el.first_line)
        m = self.new_body(alias, el, 2103)
        m.origin = "ct"
        m.is_module = True
        m.daughters = [m.index]
        ct = self._load_ct(el.filename, el.file_line)
        if len(self.S) + 6 > NS:
            self.fail(2109, f"Demasiadas superficies (máximo NS={NS}).",
                      "GEOMIN: The parameter NS must be increased.", el.first_line)
        xm, ym, zm = ct.size
        planes = [
            (0.0,) * 6 + (1.0, 0.0, 0.0, 0.0),
            (0.0,) * 6 + (-1.0, 0.0, 0.0, xm),
            (0.0,) * 6 + (0.0, 1.0, 0.0, 0.0),
            (0.0,) * 6 + (0.0, -1.0, 0.0, ym),
            (0.0,) * 6 + (0.0, 0.0, 1.0, 0.0),
            (0.0,) * 6 + (0.0, 0.0, -1.0, zm),
        ]
        first = len(self.S) + 1
        for i, q in enumerate(planes):
            self.new_surface(" CT" + _CA[i + 1] + "0", q, kplane=1, origin="ct", element=None,
                             document=self.cur_doc)
        m.surfaces = list(range(first, first + 6))
        m.flags = [2] * 6
        m.material = 9999
        self.lct = True
        t = (0.0,) * 6
        if el.has_ones_line:
            t = self.transform(el.transform, 2111)
            for ks in m.surfaces:
                s = self.S[ks - 1]
                s.q = rotshf(*t, s.q)
        self.geo.ct = ct
        self.geo.ct_body = m.index
        self.geo.ct_transform = t  # type: ignore[assignment]
        self.geo.ct_file = el.filename

    # -- END: envolvente y limpieza ---------------------------------------
    def end(self) -> None:
        B = self.B
        if len(B) == 1:
            B[0].daughters = [1]
        motherless = [b.index for b in B if b.mother == 0]
        if not (len(motherless) == 1 and B[motherless[0] - 1].is_module):
            if len(B) > NB - 1:
                self.fail(2069, f"Demasiados cuerpos para añadir la envolvente (máximo NB={NB}).",
                          "GEOMIN: The parameter NB must be increased.")
            if len(self.S) > NS - 1:
                self.fail(2070, f"Demasiadas superficies para añadir la envolvente (máximo NS={NS}).",
                          "GEOMIN: The parameter NS must be increased.")
            sph = self.new_surface("    0", ENCLOSURE_QUADRIC, kplane=0, origin="enclosure")
            enc = BodyEntry(index=len(B) + 1, alias="    0", is_module=True, material=0, origin="enclosure")
            enc.surfaces = [sph.index]
            enc.flags = [1]
            enc.daughters = [enc.index]
            for b in B:
                if b.mother != 0:
                    continue
                enc.daughters.append(b.index)
                for ks, fl in zip(list(b.surfaces), list(b.flags)):
                    if fl > 3 or ks in enc.surfaces:
                        continue
                    if len(enc.surfaces) + 1 >= NXG:
                        self.fail(2071, "Demasiadas superficies en la envolvente (parámetro NXG).",
                                  "GEOMIN: The parameter NXG is too small.")
                    enc.surfaces.append(ks)
                    enc.flags.append(4)
                b.mother = enc.index
                b.surfaces.append(sph.index)
                b.flags.append(1)
            B.append(enc)
            self.body_by_alias.setdefault(enc.alias, enc.index)
            enc.daughters.sort()
            self.geo.enclosure_added = True
        self.remove_duplicates()
        self.consistency()
        nmat = self.nmat
        if self.lct and self.geo.ct is not None:
            nmat = max(nmat, self.geo.ct.max_material)
        self.geo.nmat = nmat

    def remove_duplicates(self) -> None:
        S, B = self.S, self.B
        n = len(S)
        if n < 2:
            return
        coef = np.array([s.q for s in S], dtype=np.float64)
        fmax = coef.max(axis=1)
        fmin = coef.min(axis=1)
        fsel = np.where(np.abs(fmin) > np.abs(fmax), fmin, fmax)
        km = np.zeros(n, dtype=np.int64)  # 0 o índice (1-based) del sustituto
        # Índice inverso: superficie -> cuerpos que la usan.
        users: dict[int, set[int]] = {}
        for b in B:
            for ks in b.surfaces:
                users.setdefault(ks, set()).add(b.index)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            for i in range(n - 1):
                if km[i]:
                    continue
                cand = np.nonzero(km[i + 1:] == 0)[0] + i + 1
                if cand.size == 0:
                    continue
                f = fsel[i]
                fp = fsel[cand]
                ffp = f / fp
                rffp = 1.0 / ffp
                a = coef[i]
                bm = coef[cand]
                tst = np.zeros(cand.size)
                for c in range(10):
                    ac = a[c]
                    if abs(ac) > 1.0e-16:
                        val = np.abs((ac - bm[:, c] * ffp) / ac)
                        tst = _fmax(tst, val)
                    else:
                        bc = bm[:, c]
                        mask = np.abs(bc) > 1.0e-16
                        if mask.any():
                            val = np.abs((ac * rffp - bc) / bc)
                            tst = np.where(mask, _fmax(tst, val), tst)
                dups = cand[tst < DUPLICATE_TOL]
                for j in dups:
                    self._replace_surface(i + 1, int(j) + 1, coef, f, km, users)

    def _replace_surface(self, ks: int, kst: int, coef: np.ndarray, f: float,
                         km: np.ndarray, users: dict[int, set[int]]) -> None:
        S, B = self.S, self.B
        a = coef[ks - 1]
        b = coef[kst - 1]
        tf = DUPLICATE_TOL * f
        ispf = any(abs(a[c]) > tf and a[c] * b[c] < 0.0 for c in range(10))
        coef[kst - 1] = a
        S[kst - 1].q = S[ks - 1].q
        S[kst - 1].kplane = S[ks - 1].kplane
        S[kst - 1].replaced_by = ks
        km[kst - 1] = ks
        for kb in sorted(users.get(kst, ())):
            body = B[kb - 1]
            ksl = -1
            kslp = -1
            for k, s in enumerate(body.surfaces):
                if s == ks:
                    ksl = k
                if s == kst:
                    kslp = k
            if kslp < 0:
                continue
            body.surfaces[kslp] = ks
            if ispf:
                if body.flags[kslp] == 1:
                    body.flags[kslp] = 2
                elif body.flags[kslp] == 2:
                    body.flags[kslp] = 1
            users.setdefault(ks, set()).add(kb)
            if ksl >= 0:
                kfl = body.flags[ksl]
                kflp = body.flags[kslp]
                if min(kfl, kflp) < 3:
                    body.flags[ksl] = min(kfl, kflp)
                    if kfl != kflp and max(kfl, kflp) < 3:
                        what = "El módulo" if body.is_module else "El cuerpo"
                        self.fail(2072, f"{what} {kb} está limitado por dos superficies equivalentes "
                                        f"({ks} y {kst}) con lados opuestos; probablemente es muy pequeño o "
                                        "está muy lejos del origen.",
                                  "GEOMIN: Possibly unresolved body or module.")
                elif not body.is_module:
                    body.flags[ksl] = 3
                else:
                    body.flags[ksl] = 4
                del body.surfaces[kslp]
                del body.flags[kslp]
        users.pop(kst, None)

    def consistency(self) -> None:
        """Prueba de coherencia de F. Tola (IRETRN 2074)."""
        B = self.B
        for b in B[:-1]:
            if b.mother <= 0:
                continue
            mo = B[b.mother - 1]
            for ks, kf in zip(b.surfaces, b.flags):
                for ks2, kf2 in zip(mo.surfaces, mo.flags):
                    if ks2 == ks:
                        if (kf == 1 and kf2 == 2) or (kf == 2 and kf2 == 1):
                            what = "cuerpo" if not b.is_module else "módulo"
                            self.fail(2074, f"La superficie {ks} limita al {what} {b.index} y a su madre "
                                            f"{mo.index} con punteros de lado opuestos.",
                                      "GEOMIN: Inconsistent side pointers.")
                        break


def _fmax(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """MAX de Fortran (gfortran devuelve el otro argumento si uno es NaN)."""
    return np.fmax(a, b)


# Errores de un CLONE que GEOMIN da al copiar, al leer su línea de ceros: no cuentan si el clon está a medias.
_CLONE_COPY_CODES = frozenset({2060, 2061, 2063, 2064})


def _read_until(el: Element, cut: int) -> Element:
    """El elemento con lo que GEOMIN habría leído antes de la línea ``cut`` (donde está el error de formato)."""
    import copy

    t = copy.copy(el)
    if isinstance(el, (BodyElement, ModuleElement)):
        t.items = [it for it in el.items if it.line is not None and it.line < cut]
    if isinstance(el, SurfaceElement):
        t.params = [p for p in el.params if p.line is not None and p.line < cut]
    if isinstance(el, (SurfaceElement, ModuleElement, CloneElement)):
        t.transform = [p for p in el.transform if p.line is not None and p.line < cut]
    return t


# El código del parámetro no reconocido → el de «NPINP is too small» en el mismo sitio de GEOMIN (pengeom.f compara
# ICHPAR con NPINP antes de mirar la palabra: 461-476, 612-626, 694-708, 1390-1406, 1645-1658, 2258-2270).
_NPINP_CODES = {2012: 2008, 2014: 2013, 2016: 2015, 2050: 2049, 2059: 2058, 2112: 2111}


def _npinp_error(doc: GeoDocument, parinp) -> Diagnostic | None:
    """Si el primer error de formato es un parámetro no reconocido cuya línea pide un PARINP que el .in no define,
    lo que diría PENGEOM ahí (otro código; décima revisión, frente a GEOMIN). Lo del parser se queda: dice qué está
    mal escrito."""
    errs = [d for d in doc.diagnostics.errors if d.line is not None and d.file in (None, doc.path)]
    if not errs:
        return None
    first = min(errs, key=lambda d: len(doc.lines) if d.at_end else d.line)
    n = len(parinp or [])
    if first.ichpar is None or first.ichpar <= n or first.code not in _NPINP_CODES:
        return None
    return Diagnostic(Severity.ERROR, f"Para PENGEOM, el error de esta línea es otro: usa PARINP({first.ichpar}) "
                                      f"(columnas 33-36) y el .in define {n}.",
                      file=first.file, line=first.line, code=_NPINP_CODES[first.code],
                      fortran_reason="GEOMIN: NPINP is too small (check PARINP).")


def _error_before_format_error(doc: GeoDocument, parinp, base, ct_loader) -> _CompileError | None:
    """El error que daría PENGEOM antes del primer error de formato (None si no hay): con los elementos de antes y
    con lo que se leyó del elemento donde está (GEOMIN lee línea a línea: una etiqueta repetida en la cabecera, una
    superficie que no existe o un lado 0 en una línea anterior se dan antes que el error de formato)."""
    import copy

    lines = [len(doc.lines) if d.at_end else d.line for d in doc.diagnostics.errors
             if d.line is not None and d.file in (None, doc.path)]
    if not lines:
        return None
    cut = min(lines)
    part = copy.copy(doc)
    part.elements = [e for e in doc.elements if e.last_line < cut and (e.terminator_line is None
                                                                       or e.terminator_line < cut)]
    started = [e for e in doc.broken if e.first_line < cut]
    partial = None
    if started and isinstance(started[-1], (SurfaceElement, BodyElement, ModuleElement, CloneElement)) \
            and not any(e.first_line > started[-1].first_line for e in part.elements):
        partial = _read_until(started[-1], cut)
        part.elements.append(partial)
    part.diagnostics = type(doc.diagnostics)()
    try:
        _Compiler(part, parinp, base, ct_loader).run()
    except _CompileError as exc:
        same = exc.doc is None or exc.doc is part or exc.doc is doc
        if exc.line is None or not same or exc.line >= cut:
            return None
        if isinstance(partial, CloneElement) and exc.code in _CLONE_COPY_CODES and exc.line == partial.first_line:
            return None
        exc.doc = doc
        return exc
    except Exception:  # noqa: BLE001 - un documento a medias puede no compilarse por otras razones: no cuenta
        return None
    return None


def compile_geometry(doc: GeoDocument, parinp: Sequence[float] | None = None,
                     base_dir: str | os.PathLike | None = None,
                     ct_loader: Callable[[str], CtData] | None = None) -> CompiledGeometry:
    """Construye las tablas de PENGEOM para ``doc``.

    ``parinp`` son los valores PARINP del fichero .in (índices 1..n).
    ``base_dir`` es la carpeta de trabajo de penhmain (la del .geo), donde se
    buscan los ficheros CT.
    """
    base = Path(base_dir) if base_dir is not None else (Path(doc.path).parent if doc.path else None)
    comp = _Compiler(doc, parinp, base, ct_loader)
    geo = comp.geo
    if doc.diagnostics.has_errors:
        # PENGEOM lee en orden y se para en el primer error: si antes del primer error de formato hay otro (un lado
        # 0, una superficie que no existe…), ese es el que daría.
        early = _error_before_format_error(doc, parinp, base, ct_loader)
        if early is not None:
            geo.diagnostics.error(early.message, file=early.doc.path if early.doc else doc.path, line=early.line,
                                  code=early.code, fortran_reason=early.reason)
        else:
            npinp = _npinp_error(doc, parinp)
            if npinp is not None:
                geo.diagnostics.items.append(npinp)
        geo.diagnostics.error("La geometría tiene errores de formato; corríjalos antes de compilarla.")
        return geo
    try:
        comp.run()
        geo.ok = True
    except _CompileError as exc:
        geo.diagnostics.error(exc.message, file=exc.doc.path if exc.doc else doc.path, line=exc.line,
                              code=exc.code, fortran_reason=exc.reason)
        geo.ok = False
    geo.nmat = max(geo.nmat, comp.nmat) if not geo.ok else geo.nmat
    geo.parinp_used = sorted(comp.parinp_used)
    geo.parinp = list(parinp or [])
    return geo
