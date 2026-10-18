"""Lector de ficheros .geo que reproduce la secuencia de lectura de GEOMIN.

La lectura sigue, línea a línea, el orden y los formatos fijos de la
subrutina GEOMIN de PENGEOM 2024 (pengeom.f, líneas 290-2230), incluidos los
códigos de error ``IRETRN``. A diferencia de GEOMIN, que se detiene en el
primer error, este lector registra el diagnóstico, salta al siguiente
separador y continúa, para poder mostrar todos los problemas a la vez.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..core.diagnostics import DiagnosticList
from ..core.fortran_io import (
    RECORD_LENGTH,
    A,
    E,
    FortranReadError,
    I,
    NonFiniteFieldError,
    X,
    lone_cr_record,
    newline_of,
    read_record,
    record,
    split_records,
)
from .model import (
    COEF_KEYWORDS,
    KW_BODY,
    KW_CLONE,
    KW_CTBOX,
    KW_CTFNAME,
    KW_END,
    KW_FILE,
    KW_INCLUDE,
    KW_INCLUDE_STAR,
    KW_INDICES,
    KW_MATERIAL,
    KW_MODULE,
    KW_ONES,
    KW_SEPARATOR,
    KW_SURFACE,
    KW_SURFACE_STAR,
    PARAM_KEYWORDS,
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

# Formato de las líneas de parámetros: '(A8,1X,E22.15,1X,I4,A8)'.
_PARAM_FMT = [A(8), X(1), E(22, 15), X(1), I(4), A(8)]
# Formato de la línea INDICES: '(A8,5(A1,I2),A1)'.
_INDICES_FMT = [A(8)] + [A(1), I(2)] * 5 + [A(1)]


class _EOF(Exception):
    pass


class _Abort(Exception):
    """Error de sintaxis: se abandona el elemento actual."""


def is_comment(rec: str) -> bool:
    """Líneas de comentario de GEOMIN: 'C ', 'c ' o '#' en la columna 1."""
    return (rec[0] in "Cc" and rec[1] == " ") or rec[0] == "#"


def split_lines(text: str) -> list[str]:
    """Divide el texto en registros como los lee GEOMIN (``READ(IR,'(A72)')``), cada uno con su fin de línea.

    «\\n» y «\\r\\n» terminan el registro; un «\\r» suelto, solo si está en las 72 columnas que se leen.
    """
    return split_records(text, RECORD_LENGTH)


LONE_CR_INFO = ("El fichero tiene retornos de carro (CR) sueltos, sin salto de línea detrás (el primero, en esta "
                "línea). PENGEOM (gfortran) termina la línea en un CR que esté en las 72 columnas que lee y toma lo "
                "que sigue como la línea siguiente; más allá de la columna 72 lo ignora. ARGUS lo lee igual y numera "
                "las líneas como PENGEOM (los CR que se ignoran se ven como «␍»).")


class _Reader:
    def __init__(self, doc: GeoDocument, diags: DiagnosticList):
        self.doc = doc
        self.lines = doc.lines
        self.i = 0
        self.diags = diags
        self.file = doc.path
        self.current: Element | None = None  # elemento que se está leyendo (lo que lleva leído si hay un error)

    def peek_raw(self) -> str:
        return self.lines[self.i]

    def read(self) -> tuple[int, str]:
        """Lee el siguiente registro (A72), sin saltar comentarios."""
        if self.i >= len(self.lines):
            raise _EOF()
        idx = self.i
        self.i += 1
        return idx, record(self.lines[idx])

    def read_nc(self) -> tuple[int, str]:
        """Lee el siguiente registro que no sea un comentario."""
        while True:
            idx, rec = self.read()
            if not is_comment(rec):
                return idx, rec

    def error(self, line: int | None, message: str, code: int | None = None, reason: str | None = None):
        self.diags.error(message, file=self.file, line=line, code=code, fortran_reason=reason)

    def warning(self, line: int | None, message: str, code: int | None = None):
        self.diags.warning(message, file=self.file, line=line, code=code)

    def skip_to_separator(self) -> int | None:
        """Avanza hasta la siguiente línea de ceros (incluida)."""
        while self.i < len(self.lines):
            idx, rec = self.read()
            if rec[:8] == KW_SEPARATOR:
                return idx
            if rec[:8] == KW_END:
                self.i = idx  # que el bucle principal vea el END
                return None
        return None


def _check_label(reader: _Reader, idx: int, rec: str, code: int) -> str:
    if rec[8] != "(" or rec[13] != ")":
        reader.error(idx, "Formato de etiqueta incorrecto: se esperaba '(' en la columna 9 y ')' en la 14.",
                     code, "GEOMIN: Incorrect label format.")
        raise _Abort()
    return rec[9:13]


def _read_param(reader: _Reader, idx: int, rec: str) -> tuple[str, float, int, str]:
    try:
        kw, value, ipar, angle = read_record(rec, _PARAM_FMT)
    except NonFiniteFieldError as exc:
        reader.error(idx, f"El valor no es un número finito ({exc}): PENGEOM lo leería sin avisar (Inf, NaN o un "
                          "número fuera de rango, como 1.0E+999) y la geometría no tendría sentido. Escriba un número "
                          "normal.")
        raise _Abort() from None
    except FortranReadError as exc:
        reader.error(idx, f"No se puede leer el valor numérico ({exc}).", 2075,
                     "GEOMIN: Wrong input format.")
        raise _Abort() from None
    unit = "RAD" if angle == ") RAD   " else "DEG"
    return kw, value, ipar, unit


def _warn_param_format(reader: _Reader, idx: int, rec: str) -> None:
    """Avisos de formato que PENGEOM acepta pero que suelen ser errores."""
    field = rec[9:31]
    if rec[8] != "(":
        reader.warning(idx, "Falta '(' en la columna 9; PENGEOM lee el valor en las columnas 10-31.")
    if field.strip() and "." not in field:
        reader.warning(idx, "El valor no tiene punto decimal: con E22.15 PENGEOM aplica 15 decimales "
                            "implícitos (p. ej. '250' se lee como 2.5E-13).")


def _read_transform(reader: _Reader, code_unknown: int) -> tuple[list[ParamLine], int]:
    """Lee parámetros de transformación hasta la línea de ceros."""
    params: list[ParamLine] = []
    while True:
        idx, rec = reader.read_nc()
        kw, value, ipar, unit = _read_param(reader, idx, rec)
        if kw == KW_SEPARATOR:
            return params, idx
        name = PARAM_KEYWORDS.get(kw)
        if name is None or name not in TRANSFORM_PARAMS:
            reader.error(idx, f"Parámetro de transformación no reconocido: '{kw}'.", code_unknown,
                         "GEOMIN: What do you mean?")
            raise _Abort()
        _warn_param_format(reader, idx, rec)
        params.append(ParamLine(name, value, ipar, unit, idx))


def _parse_surface(reader: _Reader, idx0: int, rec0: str, starred: bool) -> SurfaceElement:
    el = SurfaceElement(ElementKind.SURFACE, first_line=idx0, starred=starred)
    reader.current = el
    el.label = _check_label(reader, idx0, rec0, 2003)
    el.description = rec0[14:70].rstrip()
    idx, rec = reader.read_nc()
    try:
        vals = read_record(rec, _INDICES_FMT)
    except FortranReadError as exc:
        reader.error(idx, f"Índices ilegibles ({exc}).", 2075, "GEOMIN: Wrong input format.")
        raise _Abort() from None
    kw = vals[0]
    chars = [vals[1], vals[3], vals[5], vals[7], vals[9], vals[11]]
    kq = (vals[2], vals[4], vals[6], vals[8], vals[10])
    if chars[0] != "(" or chars[1] != "," or chars[2] != "," or chars[3] != "," or chars[4] != "," or chars[5] != ")":
        reader.error(idx, "Formato incorrecto de los índices: se espera 'INDICES=( I, I, I, I, I)'.",
                     2006, "GEOMIN: Incorrect format of surface indices.")
        raise _Abort()
    imode = max(abs(k) for k in kq)
    if imode > 1 or kw != KW_INDICES:
        reader.error(idx, "Índices de superficie incorrectos (deben ser -1, 0 o 1).",
                     2007, "GEOMIN: Incorrect surface indices.")
        raise _Abort()
    el.indices = kq
    el.last_line = idx
    if imode != 0:
        # Forma reducida.
        while True:
            idx, rec = reader.read_nc()
            kw, value, ipar, unit = _read_param(reader, idx, rec)
            if kw == KW_SEPARATOR:
                el.terminator_line = idx
                return el
            name = PARAM_KEYWORDS.get(kw)
            if name is None:
                hint = ""
                if kw.strip() and kw.strip() in {k.strip() for k in PARAM_KEYWORDS}:
                    hint = " (la palabra debe ir alineada a la derecha en 8 columnas)"
                reader.error(idx, f"Parámetro no reconocido en superficie reducida: '{kw.rstrip()}'{hint}.",
                             2012, "GEOMIN: What do you mean?")
                raise _Abort()
            # Con IPAR>0 el valor del fichero no se usa (lo sustituye PARINP):
            # la comprobación la hace el compilador con el valor efectivo.
            if name.endswith("SCALE") and value < 1.0e-15 and ipar <= 0:
                code = {"X-SCALE": 2009, "Y-SCALE": 2010, "Z-SCALE": 2011}[name]
                reader.error(idx, f"{name} menor que 1.0E-15.", code, "GEOMIN: Scale factor less than 1.0D-15.")
                raise _Abort()
            _warn_param_format(reader, idx, rec)
            el.params.append(ParamLine(name, value, ipar, unit, idx))
            el.last_line = idx
    # Forma implícita.
    while True:
        idx, rec = reader.read_nc()
        kw, value, ipar, unit = _read_param(reader, idx, rec)
        if kw == KW_SEPARATOR:
            el.terminator_line = idx
            return el
        if kw == KW_ONES:
            el.has_ones_line = True
            el.last_line = idx
            el.transform, term = _read_transform(reader, 2016)
            if el.transform:
                el.last_line = el.transform[-1].line or idx
            el.terminator_line = term
            return el
        name = COEF_KEYWORDS.get(kw)
        if name is None:
            reader.error(idx, f"Coeficiente no reconocido: '{kw.rstrip()}'.", 2014, "GEOMIN: What do you mean?")
            raise _Abort()
        _warn_param_format(reader, idx, rec)
        el.params.append(ParamLine(name, value, ipar, unit, idx))
        el.last_line = idx


def _parse_items(reader: _Reader, el, allow_ones: bool, code_unknown: int) -> str:
    """Lee referencias (SURFACE/BODY/MODULE) hasta '0000' o '1111'."""
    while True:
        idx, rec = reader.read_nc()
        kw = rec[:8]
        if kw == KW_SEPARATOR:
            el.terminator_line = idx
            return "sep"
        if allow_ones and kw == KW_ONES:
            el.has_ones_line = True
            el.last_line = idx
            return "ones"
        label = rec[9:13]
        if kw in (KW_SURFACE, KW_SURFACE_STAR):
            try:
                side = read_record(rec, [X(30), I(2)])[0]
            except FortranReadError:
                reader.error(idx, "Puntero de lado ilegible (columnas 31-32).", 2075,
                             "GEOMIN: Wrong input format.")
                raise _Abort() from None
            el.items.append(RefLine("SURFACE*" if kw == KW_SURFACE_STAR else "SURFACE", label, side, idx))
        elif kw == KW_BODY:
            el.items.append(RefLine("BODY", label, None, idx))
        elif kw == KW_MODULE:
            el.items.append(RefLine("MODULE", label, None, idx))
        else:
            reader.error(idx, f"Línea no reconocida dentro del elemento: '{rec.rstrip()[:40]}'.",
                         code_unknown, "GEOMIN: What do you mean?")
            raise _Abort()
        el.last_line = idx


def _read_material(reader: _Reader, el, code: int) -> None:
    idx, rec = reader.read_nc()
    try:
        kw, imat = read_record(rec, [A(8), X(1), I(4)])
    except FortranReadError:
        reader.error(idx, "Número de material ilegible (columnas 10-13).", 2075,
                     "GEOMIN: Wrong input format.")
        raise _Abort() from None
    if kw != KW_MATERIAL:
        reader.error(idx, "Línea de material incorrecta: se esperaba 'MATERIAL(   n)'.", code,
                     "GEOMIN: Incorrect material definition line.")
        raise _Abort()
    el.material = imat
    el.material_line = idx
    el.last_line = idx


def _parse_body(reader: _Reader, idx0: int, rec0: str) -> BodyElement:
    el = BodyElement(ElementKind.BODY, first_line=idx0)
    reader.current = el
    el.label = _check_label(reader, idx0, rec0, 2017)
    el.description = rec0[14:70].rstrip()
    el.last_line = idx0
    _read_material(reader, el, 2020)
    _parse_items(reader, el, allow_ones=False, code_unknown=2031)
    return el


def _parse_module(reader: _Reader, idx0: int, rec0: str) -> ModuleElement:
    el = ModuleElement(ElementKind.MODULE, first_line=idx0)
    reader.current = el
    el.label = _check_label(reader, idx0, rec0, 2032)
    el.description = rec0[14:70].rstrip()
    el.last_line = idx0
    _read_material(reader, el, 2035)
    end = _parse_items(reader, el, allow_ones=True, code_unknown=2048)
    if end == "ones":
        el.transform, term = _read_transform(reader, 2050)
        if el.transform:
            el.last_line = el.transform[-1].line or el.last_line
        el.terminator_line = term
    return el


def _parse_clone(reader: _Reader, idx0: int, rec0: str) -> CloneElement:
    el = CloneElement(ElementKind.CLONE, first_line=idx0)
    reader.current = el
    el.label = _check_label(reader, idx0, rec0, 2051)
    el.description = rec0[14:70].rstrip()
    idx, rec = reader.read_nc()
    if rec[:8] != KW_MODULE:
        reader.error(idx, "El objeto clonado debe ser un módulo ('MODULE  ( A4)').", 2053,
                     "GEOMIN: The cloned object must be a module.")
        raise _Abort()
    el.module_label = rec[9:13]
    el.module_note = rec[14:70].rstrip()
    el.module_line = idx
    el.last_line = idx
    idx, rec = reader.read_nc()
    if rec[:8] == KW_SEPARATOR:
        el.terminator_line = idx
        return el
    if rec[:8] != KW_ONES:
        reader.error(idx, "Tras la línea MODULE de un CLONE se espera '1111...' o '0000...'.", 2057,
                     "GEOMIN: What do you mean?")
        raise _Abort()
    el.has_ones_line = True
    el.last_line = idx
    el.transform, term = _read_transform(reader, 2059)
    if el.transform:
        el.last_line = el.transform[-1].line or idx
    el.terminator_line = term
    return el


def _read_filename_line(reader: _Reader, keyword: str, code_fmt: int, code_kw: int) -> tuple[int, str]:
    idx, rec = reader.read_nc()
    if rec[8] != "(" or rec[21] != ")":
        reader.error(idx, f"Formato incorrecto: se espera '{keyword}(nombre      )' con ')' en la columna 22.",
                     code_fmt, "GEOMIN: Incorrect label format.")
        raise _Abort()
    if rec[:8] != keyword:
        reader.error(idx, f"Se esperaba '{keyword.strip()}'.", code_kw, "GEOMIN: What do you mean?")
        raise _Abort()
    name = rec[9:21].lstrip().rstrip()
    return idx, name


def _parse_include(reader: _Reader, idx0: int, rec0: str, starred: bool) -> IncludeElement:
    el = IncludeElement(ElementKind.INCLUDE, first_line=idx0, starred=starred)
    el.file_line, el.filename = _read_filename_line(reader, KW_FILE, 2065, 2066)
    el.last_line = el.file_line
    idx, rec = reader.read_nc()
    if rec[:8] != KW_SEPARATOR:
        reader.error(idx, "Tras 'FILE=(...)' se espera la línea de ceros.", 2066, "GEOMIN: What do you mean?")
        raise _Abort()
    el.terminator_line = idx
    return el


def _parse_ctbox(reader: _Reader, idx0: int, rec0: str) -> CtBoxElement:
    el = CtBoxElement(ElementKind.CTBOX, first_line=idx0)
    el.label = _check_label(reader, idx0, rec0, 2101)
    el.description = rec0[14:70].rstrip()
    el.file_line, el.filename = _read_filename_line(reader, KW_CTFNAME, 2104, 2105)
    el.last_line = el.file_line
    idx, rec = reader.read_nc()
    if rec[:8] == KW_SEPARATOR:
        el.terminator_line = idx
        return el
    if rec[:8] != KW_ONES:
        reader.error(idx, "Tras CTFNAME se espera '1111...' o '0000...'.", 2110, "GEOMIN: What do you mean?")
        raise _Abort()
    el.has_ones_line = True
    el.last_line = idx
    el.transform, term = _read_transform(reader, 2112)
    if el.transform:
        el.last_line = el.transform[-1].line or idx
    el.terminator_line = term
    return el


def parse_geo_text(text: str, path: str | None = None, included: bool = False) -> GeoDocument:
    """Lee el texto de un .geo y devuelve el documento con sus diagnósticos."""
    lines = split_lines(text)
    doc = GeoDocument(path=path, lines=lines, newline=newline_of(lines), included=included)
    diags = doc.diagnostics
    reader = _Reader(doc, diags)
    name = os.path.basename(path) if path else "geometría"
    lone = lone_cr_record(lines)
    if lone is not None:
        diags.info(LONE_CR_INFO, file=path, line=lone)

    # Cabecera: hasta la primera línea que empieza por 8 ceros.
    try:
        while True:
            idx, rec = reader.read()
            if rec[:8] == KW_SEPARATOR:
                doc.header_end = idx
                break
    except _EOF:
        diags.error(f"{name}: no hay ninguna línea de ceros; PENGEOM no puede leer el fichero.", file=path)
        return doc

    # Elementos.
    while True:
        try:
            idx, rec = reader.read_nc()
        except _EOF:
            diags.error("Fin de fichero sin línea END; PENGEOM se detendría con un error de lectura.",
                        file=path, line=len(doc.lines) - 1)
            break
        kw = rec[:8]
        start = reader.i
        reader.current = None
        try:
            if kw in (KW_SURFACE, KW_SURFACE_STAR):
                el: Element = _parse_surface(reader, idx, rec, starred=(kw == KW_SURFACE_STAR))
            elif kw == KW_BODY:
                el = _parse_body(reader, idx, rec)
            elif kw == KW_MODULE:
                el = _parse_module(reader, idx, rec)
            elif kw == KW_CLONE:
                el = _parse_clone(reader, idx, rec)
            elif kw in (KW_INCLUDE, KW_INCLUDE_STAR):
                el = _parse_include(reader, idx, rec, starred=(kw == KW_INCLUDE_STAR))
            elif kw == KW_CTBOX:
                el = _parse_ctbox(reader, idx, rec)
            elif kw == KW_END:
                el = EndElement(ElementKind.END, first_line=idx, last_line=idx)
                doc.elements.append(el)
                break
            else:
                reader.error(idx, f"Palabra clave no reconocida: '{rec.rstrip()[:40]}'.", 2002,
                             "GEOMIN: What do you mean?")
                raise _Abort()
            doc.elements.append(el)
        except _Abort:
            if reader.current is not None:
                doc.broken.append(reader.current)
            term = reader.skip_to_separator()
            if term is None and reader.i >= len(doc.lines):
                diags.error("Fin de fichero sin línea END.", file=path, line=len(doc.lines) - 1)
                break
        except _EOF:
            diags.error("Fin de fichero dentro de un elemento (falta la línea de ceros o END).",
                        file=path, line=len(doc.lines) - 1)
            break
        del start
    _assign_lead_lines(doc)
    return doc


def _assign_lead_lines(doc: GeoDocument) -> None:
    """Cada elemento "posee" los comentarios que lo preceden (para reescribirlo)."""
    prev_end = doc.header_end if doc.header_end is not None else -1
    for el in doc.elements:
        el.lead_line = prev_end + 1
        if isinstance(el, EndElement):
            prev_end = el.first_line
        elif el.terminator_line is not None:
            prev_end = el.terminator_line
        else:
            prev_end = el.last_line


def parse_geo_file(path: str | os.PathLike, encoding: str = "latin-1") -> GeoDocument:
    """Lee un .geo del disco. Se usa latin-1 para aceptar cualquier byte."""
    p = Path(path)
    text = p.read_bytes().decode(encoding)
    return parse_geo_text(text, str(p))


def load_geometry(path: str | os.PathLike,
                  loader: Callable[[str], str] | None = None) -> GeoDocument:
    """Lee un .geo y los ficheros que incluya (un solo nivel, como PENGEOM).

    Los INCLUDE se buscan en el directorio del fichero principal, que es el
    directorio de trabajo de penhmain.
    """
    return load_includes(parse_geo_file(path), Path(path).parent, loader)


def load_includes(main: GeoDocument, base: Path, loader: Callable[[str], str] | None = None) -> GeoDocument:
    """Lee los ficheros INCLUDE de ``main`` en la carpeta ``base`` (o con ``loader``), como PENGEOM."""
    for el in main.elements:
        if isinstance(el, IncludeElement):
            target = base / el.filename
            try:
                if loader is not None:
                    text = loader(el.filename)
                else:
                    text = target.read_bytes().decode("latin-1")
            except OSError:
                main.diagnostics.error(f"No existe el fichero incluido '{el.filename}'.",
                                       file=main.path, line=el.file_line)
                continue
            sub = parse_geo_text(text, str(target), included=True)
            el.document = sub
            for d in sub.diagnostics:
                main.diagnostics.items.append(d)
            for sel in sub.elements:
                if isinstance(sel, IncludeElement):
                    main.diagnostics.error("Solo se admite un nivel de INCLUDE: el fichero incluido "
                                           "contiene otro INCLUDE.", file=sub.path, line=sel.first_line,
                                           code=2067, fortran_reason="GEOMIN: Too many include levels.")
    return main
