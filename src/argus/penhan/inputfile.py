"""Fichero de entrada de penhmain (.in): lectura, validación del orden y escritura.

La lectura reproduce la subrutina PMRDR0 de penhmain.f 2025:

* registros ``(A6,1X,A120)``: palabra clave en las columnas 1-6 y datos en
  las 8-127; las líneas con la palabra clave en blanco son comentarios;
* la primera línea debe ser ``TITLE`` (IRETRN 3001);
* las palabras clave deben seguir el orden de penhmain: una palabra fuera de
  sitio o desconocida hace que penhmain termine con «Unexpected file
  termination» (IRETRN 3035) en esa línea;
* los valores se leen en formato libre (``READ(BUFFER,*)``); los nombres de
  fichero, con ``(A20)`` en las columnas 8-27; los cuerpos, con número o con
  etiqueta de 4 caracteres entre apóstrofos (subrutina FWORD).

Los errores de ejecución de Fortran (un entero con decimales, valores que
faltan...) también se detectan: penhmain se detendría en esa línea.

La escritura conserva byte a byte las líneas no modificadas.
"""

from __future__ import annotations

import copy
import difflib
import math
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from ..core.diagnostics import DiagnosticList
from ..core.fortran_io import display_record, lone_cr_record, newline_of, record_end, split_records
from .keywords import KNOWN_TYPOS, ORDER, SPEC, KeywordSpec
from .listdirected import ListReadError, read_list

BLANK_KW = "      "
COMMENT_END = 69  # columna del «]» final en penhmain-layout.in
TITLE_WIDTH = 72  # READ(5,'(A6,1X,A65)'): columnas que lee PMRDR0 en la línea TITLE
LINE_WIDTH = 127  # READ(5,'(A6,1X,A120)'): columnas que lee en el resto
A20_WIDTH = 20  # READ(BUFFER,'(A20)'): columnas 8-27
SRNUCL_WIDTH = 45  # SRNUCL_FNAME es CHARACTER*45
NPINPM = 50  # penhmain: tabla PARINP(NPINPM) que se pasa a PENGEOM (PMRDR no comprueba el índice)
LONE_CR_INFO = ("El fichero tiene retornos de carro (CR) sueltos, sin salto de línea detrás (el primero, en esta "
                "línea). penhmain (gfortran) termina la línea en un CR que esté en las columnas que lee (72 en TITLE, "
                "127 en el resto) y toma lo que sigue como la línea siguiente; más allá, lo ignora. ARGUS lo lee igual "
                "y numera las líneas como penhmain (los CR que se ignoran se ven como «␍»).")


def split_in_records(text: str) -> list[str]:
    """Registros del .in como los lee PMRDR0 (un CR suelto en las columnas que se leen termina el registro)."""
    return split_records(text, LINE_WIDTH, first_width=TITLE_WIDTH)


# ---------------------------------------------------------------------------
# Líneas
# ---------------------------------------------------------------------------

@dataclass
class InLine:
    kw: str  # 6 caracteres
    raw: str | None = None  # texto original (con fin de línea) o None si es nueva
    index: int | None = None  # línea en el fichero original (0-based)
    values: list = field(default_factory=list)
    text: str = ""  # nombre de fichero (A20), título o token
    label: str | None = None  # etiqueta de cuerpo (4 caracteres) si va entre apóstrofos
    kb: int | None = None  # número de cuerpo si no hay etiqueta
    comment: str = ""  # resto de la línea tras los datos
    error: str | None = None  # error de lectura (penhmain se detendría)
    warning: str | None = None
    modified: bool = False
    hang: bool = False  # penhmain se quedaría colgado en esta línea (FWORD)
    nulls: tuple[int, ...] = ()  # valores nulos («,» o «r*»): la variable de penhmain conserva su valor
    formatted: str | None = None  # línea cambiada que no se puede leer: su texto (no se pierden sus valores)

    @property
    def spec(self) -> KeywordSpec | None:
        return SPEC.get(self.kw)

    @property
    def is_comment(self) -> bool:
        return self.kw == BLANK_KW

    @property
    def body_ref(self) -> str:
        if self.label is not None:
            return f"'{self.label}'"
        return str(self.kb) if self.kb is not None else "?"


def fmt_real(v: float) -> str:
    """Real legible y exacto para la lectura en formato libre de Fortran.

    Entre 1e-3 y 1e4 se escribe en notación decimal (``-100.0``, ``0.05``);
    fuera de ese intervalo se elige la forma más corta entre la decimal y la
    científica mínima (``1e8``, ``2.5e-5``), que se lee con el mismo valor.
    """
    v = float(v)
    if v == 0.0:
        return "0.0"
    if not math.isfinite(v):  # gfortran lee «Inf», «-Inf» y «NaN» en formato libre
        return "NaN" if v != v else ("-Inf" if v < 0 else "Inf")
    a = abs(v)
    r = repr(v)
    if 1e-3 <= a < 1e4 and "e" not in r:
        return r
    d = Decimal(r).normalize()
    sign, digits, exp = d.as_tuple()
    n = len(digits)
    e = exp + n - 1
    mant = str(digits[0]) + ("." + "".join(str(x) for x in digits[1:]) if n > 1 else "")
    sci = ("-" if sign else "") + mant + "e" + str(e)
    if "e" not in r and len(r) <= len(sci):
        return r
    return sci


def fmt_value(v, kind: str) -> str:
    if kind == "int":
        return str(int(v))
    if kind == "real":
        return fmt_real(v)
    return str(v)


def format_line(line: InLine) -> str:
    """Texto canónico de una línea (sin fin de línea).

    Lanza ``ValueError`` (con un mensaje para el usuario) si penhmain no leería lo que se pide: un nombre de
    fichero de más de 20 caracteres (A20), un nucleido de más de 45 (SRNUCL) o un salto de línea en el texto.
    """
    text = _format_line(line)
    if "\r" in text or "\n" in text:
        raise ValueError(f"{line.kw.strip() or 'Comentario'}: el texto no puede tener saltos de línea (penhmain lo "
                         "leería como otra línea).")
    return text


def _format_line(line: InLine) -> str:
    if line.formatted is not None and line.error:
        return line.formatted
    spec = line.spec
    kw = line.kw
    if spec is None:
        body = line.text
    elif spec.mode == "title":
        return f"{kw} {line.text}".rstrip()
    elif spec.mode == "a20":
        name = line.text.rstrip(" ")
        if len(name) > A20_WIDTH:
            raise ValueError(f"{kw.strip()}: el nombre «{name.strip()}» tiene {len(name)} caracteres y penhmain solo "
                             f"lee {A20_WIDTH} caracteres (columnas 8-27): use un nombre más corto.")
        body = name.ljust(A20_WIDTH)
    elif spec.mode == "token":
        body = line.text
        if len(body.rstrip(" ")) > SRNUCL_WIDTH:
            raise ValueError(f"{kw.strip()}: el nombre «{body.strip()}» tiene más de {SRNUCL_WIDTH} caracteres y "
                             f"penhmain solo lee {SRNUCL_WIDTH} (CHARACTER*45): use un nombre más corto.")
        # penhmain lo lee dirigido por lista: un blanco, una coma o una barra cortarían el nombre
        # («Co 60.nuc» se leería «Co»). Entre apóstrofos se lee entero.
        if body and (any(c in body for c in " ,/\t;") or body[0] in "'\"" or re.match(r"\d+\*", body)):
            body = "'" + body.replace("'", "''") + "'"
    elif spec.mode == "body":
        ref = f"'{line.label}'" if line.label is not None else str(line.kb if line.kb is not None else 0)
        body = " ".join([ref] + _values_text(line, spec, kw, len(kw) + 2 + len(ref)))
    else:
        body = " ".join(_values_text(line, spec, kw, len(kw) + 1))
    text = f"{kw} {body}"
    c = line.comment.strip()
    if spec is not None and spec.mode == "body" and line.label is not None and not spec.kinds and not c:
        c = spec.comment or "[body]"  # evita el bucle infinito de FWORD
    if c:
        pad = COMMENT_END - len(text) - len(c)
        text = text.rstrip() if spec is not None and spec.mode != "a20" else text
        text += " " * max(pad, 1) + c
    return text.rstrip()


def _values_text(line: InLine, spec, kw: str, start: int) -> list[str]:
    """Los valores de la línea, que tienen que acabar en la columna 127 (penhmain no lee más): si no caben con
    todas sus cifras, los reales se escriben con menos (15, 12, 10, 8 o 6 significativas)."""
    pairs = list(zip(line.values, spec.kinds, strict=False))
    texts = [fmt_value(v, k) for v, k in pairs]
    for digits in (15, 12, 10, 8, 6):
        if start + len(" ".join(texts)) <= LINE_WIDTH:
            return texts
        texts = [_short_real(v, digits) if k == "real" else t for (v, k), t in zip(pairs, texts)]
    if start + len(" ".join(texts)) <= LINE_WIDTH:
        return texts
    raise ValueError(f"{kw.strip()}: los valores no caben en las columnas que lee penhmain (8 a {LINE_WIDTH}).")


def _short_real(v, digits: int) -> str:
    v = float(v)
    if not math.isfinite(v):
        return fmt_real(v)
    short = f"{v:.{digits}g}"
    return fmt_real(float(short)) if len(fmt_real(float(short))) <= len(short) else short


class FwordHang(Exception):
    """FWORD entra en un bucle infinito: penhmain se queda colgado."""


HANG_MESSAGE = ("Tras la etiqueta entre apóstrofos no hay nada más en la línea: la subrutina FWORD de "
                "penhmain entra en un bucle infinito y el programa se queda colgado sin mensaje. Añada un "
                "comentario después de la etiqueta (p. ej. [Active body]) o use el número del cuerpo.")


def _fword(buf: str) -> tuple[str | None, str]:
    """FWORD de penhmain: primera palabra entre apóstrofos y el resto.

    Reproduce también su defecto: el bucle que quita blancos, comas y
    apóstrofos del resto no termina si el resto solo contiene esos caracteres.
    """
    i1 = buf.find("'")
    if i1 < 0:
        return None, buf
    tmp = buf[i1 + 1:]
    i2 = tmp.find("'")
    length = i2  # LENGTH = I2-1 en índices Fortran (1-based)
    if length < 1:
        return None, buf
    word = tmp[:length]
    tail = tmp[length:].ljust(120)[:120]
    if tail.strip("' ,") == "":
        raise FwordHang()
    while tail and tail[0] in "' ,":
        tail = tail[1:]
    return word[:4].ljust(4) if len(word) < 4 else word, tail


_WHITESPACE_NAMES = {"\t": "un tabulador", "\xa0": "un espacio no separable (NBSP)"}
# Los nombres de fichero (A20) de lo que escribe penhmain (los demás son de lo que lee).
OUTPUT_A20 = {"DUMPTO", "IDSPC", "IDPSF", "IDFLNC", "IDAGEF", "ADSPC", "EDSPC"}
_COMMENT_START = "[(!#;{"
_DECIMAL_COMMA = re.compile(r"(\d+),(\d+)")
_MORE_NUMBERS = re.compile(r"[ \t]*,?[ \t]*[+-]?\.?\d")
_BLANK_BETWEEN_VALUES = re.compile(r"[^\s,][ \t]+[^\s,]")


def _parse_a20(line: InLine, buf: str) -> None:
    """``READ(BUFFER,'(A20)')``: el nombre son las columnas 8-27 enteras; OPEN solo quita los blancos finales."""
    name = buf[:A20_WIDTH]
    line.text = name.rstrip(" ")
    line.comment = buf[A20_WIDTH:].rstrip()
    shown = line.text.strip()
    if line.text and line.text[-1].isspace():
        what = _WHITESPACE_NAMES.get(line.text[-1], "un carácter en blanco que no es un espacio")
        line.error = (f"El nombre del fichero ('{shown}') termina en {what} dentro de las columnas 8-27: penhmain lo "
                      "incluye en el nombre (solo quita los espacios finales) y no encontrará el fichero. Separe el "
                      "comentario con espacios.")
    elif name[:1].isspace() and shown:
        if line.kw.strip() in OUTPUT_A20:  # un fichero que escribe penhmain: no lo busca, lo crea con ese nombre
            line.warning = ("El nombre de fichero no empieza en la columna 8: penhmain conserva los blancos iniciales "
                            f"y escribirá «{line.text}» (con el blanco delante).")
        else:
            line.warning = ("El nombre de fichero no empieza en la columna 8: penhmain conserva los "
                            "blancos iniciales y no encontrará el fichero.")
    elif any(c.isspace() for c in shown):
        # penhmain lee las columnas 8-27 enteras (A20): un nombre con espacios vale tal cual. Solo es
        # un error si lo que sigue al espacio es un comentario que empieza antes de la columna 28.
        rest = shown.split(None, 1)[1]
        if rest.startswith(("[", "(", "!")):
            line.error = ("El comentario empieza antes de la columna 28 y se mezcla con el nombre del "
                          f"fichero ('{shown}'): penhmain no lo encontrará.")
        else:
            line.warning = (f"El nombre del fichero tiene espacios ('{shown}'): penhmain lo usa tal "
                            "cual (columnas 8-27). Si lo que va detrás del espacio es un comentario, "
                            "empiécelo en la columna 28.")
    elif name[-1] != " " and not buf[A20_WIDTH].isspace() and buf[A20_WIDTH] not in _COMMENT_START:
        more = buf[A20_WIDTH:].split(None, 1)[0]
        line.warning = (f"El nombre del fichero sigue después de la columna 27 ('{name}{more}'): penhmain solo lee "
                        f"las columnas 8-27 y usará '{name}'.")


def _decimal_comma(kw: str, data: str, rest: str, values: list, kinds) -> str | None:
    """Aviso si un «d,d» de la línea parece un número con coma decimal (penhmain lo lee como dos valores).

    Solo cuando la línea tiene más números que los que se leen o mezcla blancos y comas entre los valores:
    ``SPOSIT 0,0,1`` es una lista separada por comas y no se avisa.
    """
    more = _MORE_NUMBERS.match(rest)
    joined = data + (rest.split(None, 1)[0] if rest.strip() else "")
    m = _DECIMAL_COMMA.search(joined)
    if m is None or not (more or (_DECIMAL_COMMA.search(data) and _BLANK_BETWEEN_VALUES.search(data.strip()))):
        return None
    used = ", ".join(fmt_value(v, k) for v, k in zip(values, kinds, strict=False))
    a, b = m.groups()
    return (f"{kw}: «{m.group(0)}» parece un número con coma decimal. penhmain separa los valores con blancos o "
            f"comas: lee {a} y {b} como dos valores (en esta línea usa {used}). Los decimales se escriben con "
            f"punto ({a}.{b}).")


def parse_line(text: str, index: int | None = None) -> InLine:
    """Interpreta un registro según su palabra clave (sin contexto)."""
    rec = text.rstrip("\r\n")
    kw = rec[:6].ljust(6)
    buf = rec[7:127].ljust(120)
    line = InLine(kw=kw, raw=text, index=index)
    if kw == BLANK_KW:
        line.text = rec[7:]
        return line
    spec = SPEC.get(kw)
    if spec is None:
        line.text = rec[7:]
        return line
    nulls: list[int] = []
    data, rest, kinds = "", "", spec.kinds
    try:
        if spec.mode == "title":
            line.text = rec[7:72].rstrip()
        elif spec.mode == "a20":
            _parse_a20(line, buf)
        elif spec.mode == "token":
            vals, end = read_list(buf, ["str"], [""])
            full = (vals[0] or "").rstrip(" ")  # sin comilla final: hasta el final del registro, blancos incluidos
            line.text = full[:SRNUCL_WIDTH].rstrip(" ")
            line.comment = buf[end:].rstrip()
            if len(full) > SRNUCL_WIDTH:
                line.warning = (f"{spec.keyword.strip()}: el nombre tiene {len(full)} caracteres y penhmain solo lee "
                                f"{SRNUCL_WIDTH} (CHARACTER*45): usará '{line.text}'.")
        elif spec.mode == "body":
            try:
                word, tail = _fword(buf)
            except FwordHang:
                line.error = HANG_MESSAGE
                line.hang = True
                return line
            if word is not None and len(buf[buf.find("'") + 1:].split("'")[0]) == 4:
                line.label = word
                data = tail
                vals, end = read_list(tail, list(spec.kinds), list(spec.defaults), nulls)
                line.values = vals
                line.comment = tail[end:].rstrip()
                data, rest = tail[:end], tail[end:]
            else:
                data = buf
                vals, end = read_list(buf, ["int", *spec.kinds], [0, *spec.defaults], nulls)
                line.kb = vals[0]
                line.values = vals[1:]
                nulls = [k - 1 for k in nulls if k > 0]
                line.comment = buf[end:].rstrip()
                data, rest, kinds = buf[:end], buf[end:], ("int", *spec.kinds)
        else:
            data = buf
            vals, end = read_list(buf, list(spec.kinds), list(spec.defaults), nulls)
            line.values = vals
            line.comment = buf[end:].rstrip()
            data, rest = buf[:end], buf[end:]
    except ListReadError as exc:
        line.error = f"{spec.keyword.strip()}: {exc}"
        if _DECIMAL_COMMA.search(data.split("[", 1)[0]):
            line.error += (". ¿Coma decimal? penhmain separa los valores con blancos o comas; los decimales se "
                           "escriben con punto")
    line.nulls = tuple(nulls)
    if line.error is None and line.warning is None and spec.mode in ("list", "body"):
        values = ([line.kb] if line.label is None and spec.mode == "body" else []) + list(line.values)
        line.warning = _decimal_comma(spec.keyword.strip(), data, rest, values, kinds)
    if line.error is None and line.warning is None and any(
            isinstance(v, float) and not math.isfinite(v) for v in line.values):
        line.warning = (f"{spec.keyword.strip()}: hay un valor que no es un número finito (Inf, NaN o fuera de "
                        "rango, como 1e999): penhmain lo lee así y la simulación no tendría sentido.")
    return line


# ---------------------------------------------------------------------------
# Modelo semántico (lo que penhmain carga en PENHMAIN_input)
# ---------------------------------------------------------------------------

def resolve_body_ref(geo, label: str | None, kb: int | None, *, blank_search: bool = True) -> int:
    """Cuerpo al que penhmain (PMRDR) aplica una referencia del .in, o 0 si no hay ninguno.

    Un número distinto de 0 es ese cuerpo (el rango lo comprueba quien llama). Con una etiqueta, o con el
    número 0, PMRDR busca la etiqueta en BALIAS y gana la última coincidencia; una etiqueta en blanco (o el
    número 0) coincide con los cuerpos que PENGEOM deja sin etiqueta: las copias de un CLONE, la envolvente que
    añade PENGEOM y los cuerpos de un INCLUDE sin «*». Los detectores de impacto (IDBODY) no buscan una
    etiqueta en blanco: con ``blank_search=False`` da 0 (IRETRN 3082).
    """
    if geo is None:
        return 0
    if label is None and kb:
        return int(kb)
    word = label or ""
    found = 0
    if not word.strip():
        if blank_search:
            for i in range(1, geo.nbodys + 1):
                if not geo.balias(i).strip():
                    found = i
        return found
    for i in range(1, geo.nbodys + 1):
        if geo.balias(i) == word:
            found = i
    return found


@dataclass
class BodyRef:
    label: str | None
    kb: int | None
    line: InLine

    def resolve(self, geo, *, blank_search: bool = True) -> int | None:
        """Índice PENGEOM del cuerpo (como PMRDR: etiqueta vía BALIAS o número; 0 = ninguno)."""
        return resolve_body_ref(geo, self.label, self.kb, blank_search=blank_search)


@dataclass
class SourceDef:
    kparp: int = 1
    nuclide: str | None = None
    energy: float = 0.0  # SE0 (penhmain pone 1e9 si no hay energía ni espectro)
    spectrum: list[tuple[float, float]] = field(default_factory=list)
    lines: list[tuple[float, float]] = field(default_factory=list)
    polarization: tuple[float, float, float] = (0.0, 0.0, 0.0)
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    box: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bodies: list[BodyRef] = field(default_factory=list)
    cone: tuple[float, float, float] | None = None  # SCONE (θ, φ, α) en grados
    rect: tuple[float, float, float, float] | None = None  # SRECTA (θL, θU, φL, φU)
    psf: list[str] = field(default_factory=list)

    @property
    def extended(self) -> bool:
        return max(abs(v) for v in self.box) > 1e-16

    @property
    def radioactive(self) -> bool:
        return self.kparp == 0

    @property
    def from_psf(self) -> bool:
        return bool(self.psf)

    def angular(self) -> tuple[str, tuple]:
        """Distribución angular efectiva: ('cone', (θ,φ,α)) o ('rect', (...))."""
        if self.radioactive:
            return "isotropic", (0.0, 0.0, 180.0)
        if self.cone is not None:  # SCONE gana si aparecen las dos
            return "cone", self.cone
        if self.rect is not None:
            return "rect", self.rect
        return "cone", (0.0, 0.0, 0.0)


@dataclass
class MaterialDef:
    fname: str
    line: InLine
    msimpa: list[float] | None = None
    msimph: list[float] | None = None
    msimpn: list[float] | None = None


@dataclass
class PenhmainInput:
    title: str = ""
    source: SourceDef = field(default_factory=SourceDef)
    materials: list[MaterialDef] = field(default_factory=list)
    geomfn: str = ""
    parinp: dict[int, float] = field(default_factory=dict)
    body_refs: list[tuple[str, BodyRef]] = field(default_factory=list)  # (palabra clave, cuerpo)
    grids: dict[str, list] = field(default_factory=dict)
    ok: bool = False
    # penhmain ha leído entera la fuente (aunque se pare después): si no (``ok`` False y esto False), lo que hay en
    # ``source`` son valores por defecto, no los del fichero (no se enseñan ni se editan: 0.16.3).
    source_read: bool = False

    def parinp_list(self) -> list[float]:
        """PARINP(1..n) que recibe PENGEOM (n: el mayor índice hasta NPINPM = 50; los que faltan valen 0).

        ``parinp`` se guarda disperso (índice -> valor); los índices mayores que 50 no caben en la tabla de
        penhmain (son un error del .in) y no se usan.
        """
        idx = [i for i in self.parinp if 1 <= i <= NPINPM]
        if not idx:
            return []
        return [self.parinp.get(i, 0.0) for i in range(1, max(idx) + 1)]


# ---------------------------------------------------------------------------
# Máquina de estados de PMRDR0
# ---------------------------------------------------------------------------

_TAB_HINT = ("Las 6 primeras columnas (la palabra clave, o en blanco en un comentario) llevan un tabulador: penhmain "
             "las lee tal cual («{}», ⇥ es el tabulador) y no reconoce la línea; escríbala con espacios.")


def _stop_hint(lines: list, st) -> str:
    """Lo que ayuda a entender una parada que no dice dónde está el problema (0.16.3): un tabulador en las 6 primeras
    columnas o, si falta MFNAME o GEOMFN (3013, 3016) pero están más abajo, la línea fuera de su sitio que hace que
    penhmain no llegue a ellas."""
    ln = st.line
    if ln is None or st.code == 3035:  # (la 3035 ya lo dice)
        return ""
    field = (ln.raw or "")[:6]
    if "\t" in field:
        return " " + _TAB_HINT.format(field.replace("\t", "⇥"))
    want = {3013: "MFNAME", 3016: "GEOMFN"}.get(st.code or 0)
    i = next((k for k, x in enumerate(lines) if x is ln), None)
    if want is None or i is None:
        return ""
    later = next((k for k in range(i + 1, len(lines)) if lines[k].kw == want), None)
    if later is None:
        return ""
    where = f" (sección «{SPEC[ln.kw].section}»)" if ln.kw in SPEC else ""
    return (f" Hay una línea {want} más abajo (línea {later + 1}), pero penhmain no llega a ella: «{ln.kw.strip()}» "
            f"(línea {i + 1}) está fuera de su sitio{where}; penhmain lee las palabras clave en un orden fijo.")


class _Stop(Exception):
    def __init__(self, code: int | None, message: str, reason: str | None, line: InLine | None):
        super().__init__(message)
        self.code, self.message, self.reason, self.line = code, message, reason, line


class _Done(Exception):
    pass


NO_STOP_REASON = "penhmain no se detiene aquí: sigue con un valor sin sentido o indefinido"


def _buffer(ln: InLine) -> str:
    """Columnas 8-127 de una línea tal como se escribiría."""
    text = format_line(ln) if ln.modified or ln.raw is None else ln.raw
    return text.rstrip("\r\n")[7:127].ljust(120)


class _Reader:
    def __init__(self, lines: list[InLine]):
        self.lines = lines
        self.pos = 0
        self.cur: InLine | None = None
        self.m = PenhmainInput()
        self.errors: list[tuple[InLine, str]] = []  # errores que no detienen PMRDR0 (resultado indefinido)
        self.notes: list[tuple[InLine, str]] = []  # avisos que dependen de las líneas anteriores
        self.kpard: int | None = None  # variable local KPARD de PMRDR0 (IDKPAR y ADKPAR)

    def keep_previous(self, previous: list, what: str) -> list:
        """Valores de la línea en curso: los nulos conservan el valor que tenía la variable (``previous``)."""
        ln = self.cur
        # Los valores como se leyeron: un análisis anterior ya pudo poner en ``ln.values`` los que conserva, y el
        # aviso se tiene que dar en cada análisis (y con el valor que haya ahora antes).
        defaults = list(ln.spec.defaults) if ln.spec is not None else []
        read = list(ln.values)
        for k in ln.nulls:
            if k < len(read) and k < len(defaults):
                read[k] = defaults[k]
        vals = list(read)
        for k in ln.nulls:
            if k < len(vals) and k < len(previous):
                vals[k] = previous[k]
        if vals != read:
            self.notes.append((ln, f"{ln.kw.strip()}: un valor nulo (una coma sin número o «r*») conserva el valor "
                                   f"de {what}; penhmain usa {', '.join(str(v) for v in vals)}."))
        ln.values = vals
        return vals

    def particle(self, key: str) -> int | None:
        """KPARD de una línea IDKPAR/ADKPAR (un valor nulo conserva el de la anterior); None si es indefinido."""
        ln = self.cur
        self.vals()
        if ln.nulls:
            if self.kpard is None:
                self.errors.append((ln, f"{key}: falta el tipo de partícula (valor nulo) y no hay ninguna línea "
                                        "IDKPAR ni ADKPAR antes: penhmain usaría una variable sin valor definido."))
                return None
            self.keep_previous([self.kpard], "la línea IDKPAR o ADKPAR anterior")
        self.kpard = int(ln.values[0])
        return self.kpard

    def parinp(self, n_parin: int, prev_ip: int | None) -> tuple[int, int | None]:
        """Una línea PARINP como PMRDR0: ``READ(BUFFER,*) IP``, comprobaciones y ``READ`` de IP y el valor.

        Devuelve el número de líneas PARINP leídas y el valor de la variable local IP.
        """
        ln = self.cur
        m = self.m
        if ln.error and not ln.hang:  # PMRDR0 lee primero solo el índice: se comprueba antes que el valor
            nul: list[int] = []
            try:
                ip = read_list(_buffer(ln), ["int"], [0], nul)[0][0]
            except ListReadError:
                ip = None
            if ip is None:
                self.vals()  # error de lectura del índice: penhmain se detiene
            null_ip = bool(nul)
        else:
            ip, null_ip = int(self.vals()[0]), 0 in ln.nulls
        if not null_ip:
            prev_ip = ip
        if prev_ip is not None and prev_ip < 1:
            self.stop(3014, "El índice de PARINP debe ser positivo.", "The PARINP index must be positive.")
        n_parin += 1  # NPARIN cuenta líneas, no índices distintos
        if n_parin > 50:
            self.stop(3015, "Demasiados parámetros PARINP (máximo 50 líneas).", "Too many modified parameters.")
        ip, val = self.vals()
        if null_ip:
            self.errors.append((ln, "PARINP: falta el índice IP (valor nulo). penhmain comprueba el IP de la línea "
                                    "PARINP anterior, guarda IP = 0 y escribe el valor en PARINP(0), fuera de la "
                                    "tabla (en memoria que no es suya): no sustituye ningún parámetro."))
            return n_parin, prev_ip
        if ip > NPINPM:
            self.errors.append((ln, f"PARINP: el índice {ip} pasa de {NPINPM}, el tamaño de la tabla PARINP de "
                                    "penhmain (NPINPM): penhmain escribe el valor fuera de la tabla, en memoria que "
                                    "no es suya (o se detiene), y PENGEOM no puede usarlo."))
        m.parinp[int(ip)] = float(val)
        return n_parin, prev_ip

    def nxt(self, eof_ok: bool) -> str:
        while self.pos < len(self.lines):
            ln = self.lines[self.pos]
            self.pos += 1
            if ln.kw == BLANK_KW:
                continue
            self.cur = ln
            return ln.kw
        if eof_ok:
            raise _Done()
        raise _Stop(None, "El fichero termina aquí y penhmain espera más líneas: se detendría con un error "
                          "de fin de fichero.", None, self.cur)

    def vals(self) -> list:
        ln = self.cur
        if ln.hang:
            raise _Stop(None, ln.error, "HANG", ln)
        if ln.error and ln.spec is not None and ln.spec.mode == "a20":
            # READ(BUFFER,'(A20)') no falla: el nombre está mal (penhmain no encontrará el fichero), pero sigue.
            self.errors.append((ln, ln.error))
        elif ln.error:
            tail = "" if "«;»" in ln.error else " (penhmain se detendría con un error de lectura)"
            raise _Stop(None, f"{ln.error}{tail}.", None, ln)
        return ln.values

    def stop(self, code: int, message: str, reason: str) -> None:
        raise _Stop(code, message, reason, self.cur)

    def body(self) -> BodyRef:
        self.vals()
        return BodyRef(self.cur.label, self.cur.kb, self.cur)

    def run(self) -> None:  # noqa: C901 - reproduce la estructura de PMRDR0
        m = self.m
        src = m.source
        if not self.lines or self.lines[0].kw != "TITLE ":
            self.cur = self.lines[0] if self.lines else None
            first = (self.cur.raw or "") if self.cur is not None else ""
            bom = first.startswith(("\ufeff", "\xef\xbb\xbf"))  # (leído en Latin-1, «ï»¿»)
            self.stop(3001, "La primera línea debe ser TITLE." + (
                " El fichero empieza por la marca BOM de UTF-8 (la ponen algunos editores al guardar en UTF-8): "
                "penhmain lee «ï»¿TITLE» y no TITLE. Guárdelo sin BOM (en ANSI o Latin-1)." if bom else ""),
                "The input file must begin with the TITLE line.")
        m.title = self.lines[0].text
        self.pos = 1
        kw = self.nxt(False)
        n_spec = n_line = 0
        se0 = 0.0
        jump = kw == "IPSFN "
        if not jump and kw == "SKPAR ":
            src.kparp = int(self.vals()[0])
            if src.kparp < 0 or src.kparp > 5:
                self.stop(3002, f"Tipo de partícula incorrecto (KPARP={src.kparp}); debe estar entre 0 y 5.",
                          "Incorrect particle type.")
            kw = self.nxt(False)
            jump = kw == "IPSFN "
        if not jump:
            if kw == "SRNUCL":
                self.vals()
                src.nuclide = self.cur.text
                src.kparp = 0
                kw = self.nxt(False)
                jump = kw == "IPSFN "
            elif src.kparp == 0:
                self.stop(3003, "SKPAR 0 (fuente radiactiva) exige la línea SRNUCL a continuación.",
                          "The required radionuclide data are missing.")
        if not jump:
            if kw == "SENERG":
                se0 = float(self.vals()[0])
                kw = self.nxt(False)
                jump = kw == "IPSFN "
            elif kw == "SPECTR":
                if se0 > 50.0:
                    self.stop(3004, "SENERG y SPECTR no pueden ir juntos.", "Conflicting SENERG and SPECTR lines.")
                while True:
                    n_spec += 1
                    if n_spec > 250:
                        self.stop(3005, "Demasiados intervalos de energía (máximo 250).",
                                  "The number of energy bins is too large.")
                    e, pr = self.vals()
                    src.spectrum.append((float(e), float(pr)))
                    kw = self.nxt(False)
                    if kw == "SPECTR":
                        continue
                    jump = kw == "IPSFN "
                    break
            elif kw == "SLINES":
                if se0 > 50.0:
                    self.stop(3006, "SENERG y SLINES no pueden ir juntos.", "Conflicting SENERG and SLINES lines.")
                while True:
                    n_line += 1
                    if n_line > 250:
                        self.stop(3008, "Demasiadas líneas de energía (máximo 250).",
                                  "The number of energy lines is too large.")
                    e, pr = self.vals()
                    src.lines.append((float(e), max(float(pr), 0.0)))
                    kw = self.nxt(False)
                    if kw == "SLINES":
                        continue
                    jump = kw == "IPSFN "
                    break
        if not jump and kw == "SGPOL ":
            src.polarization = tuple(float(v) for v in self.vals())  # type: ignore[assignment]
            kw = self.nxt(False)
            jump = kw == "IPSFN "
        if not jump and kw == "SPOSIT":
            src.position = tuple(float(v) for v in self.vals())  # type: ignore[assignment]
            kw = self.nxt(False)
            jump = kw == "IPSFN "
        if not jump and kw == "SBOX  ":
            src.box = tuple(abs(float(v)) for v in self.vals())  # type: ignore[assignment]
            kw = self.nxt(False)
            jump = kw == "IPSFN "
        if not jump and kw == "SBODY ":
            while True:
                if len(src.bodies) + 1 > 200:
                    self.stop(3009, "Demasiados cuerpos en SBODY (máximo 200).", "Too many bodies in the list.")
                src.bodies.append(self.body())
                kw = self.nxt(False)
                if kw == "SBODY ":
                    continue
                jump = kw == "IPSFN "
                break
        if not jump and kw == "SCONE ":
            src.cone = tuple(float(v) for v in self.vals())  # type: ignore[assignment]
            kw = self.nxt(False)
            jump = kw == "IPSFN "
        if not jump and kw == "SRECTA":
            src.rect = tuple(float(v) for v in self.vals())  # type: ignore[assignment]
            kw = self.nxt(False)
        if not jump:
            if se0 < 50.0 and n_line == 0 and n_spec == 0:
                se0 = 1.0e9
        src.energy = se0
        # 116: ficheros de espacio de fases.
        if kw == "IPSFN ":
            if src.kparp == 0:
                self.stop(3010, "Con SKPAR 0 no se pueden leer partículas de un fichero de espacio de fases.",
                          "Inconsistent definition of the primary source.")
            while True:
                if len(src.psf) + 1 > 100:
                    self.stop(3011, "Demasiados ficheros de espacio de fases (máximo 100).",
                              "Too many phase-space files")
                self.vals()
                src.psf.append(self.cur.text)
                kw = self.nxt(False)
                if kw != "IPSFN ":
                    break
        for key in ("IPSPLI", "WGTWIN", "EPMAX "):
            if kw == key:
                self.vals()
                kw = self.nxt(False)
        m.source_read = True
        # 201: materiales.
        while True:
            if kw == "MFNAME":
                if len(m.materials) + 1 > 10:
                    self.stop(3012, "Demasiados materiales (máximo MAXMAT = 10).", "Too many materials.")
                self.vals()
                m.materials.append(MaterialDef(self.cur.text, self.cur))
                kw = self.nxt(True)
                if kw == "MFNAME":
                    continue
            again = False
            for key, attr in (("MSIMPA", "msimpa"), ("MSIMPH", "msimph"), ("MSIMPN", "msimpn")):
                if kw == key:
                    v = self.vals()
                    if m.materials:
                        setattr(m.materials[-1], attr, [float(x) for x in v])
                    kw = self.nxt(True)
                    if kw == "MFNAME":
                        again = True
                        break
            if not again:
                break
        if not m.materials:
            self.stop(3013, "Falta el fichero de material (línea MFNAME).",
                      "You have to specify a material file (line MFNAME).")
        # Geometría.
        if kw == "GEOMFN":
            self.vals()
            m.geomfn = self.cur.text
            n_parin, ip = 0, None
            while True:
                kw = self.nxt(True)
                if kw != "PARINP":
                    break
                n_parin, ip = self.parinp(n_parin, ip)
        else:
            self.stop(3016, "Falta el fichero de geometría (línea GEOMFN) después de los materiales.",
                      "You have to specify a geometry file.")
        # 205/207/217: DSMAX, EABSB, EABSBH.
        label = 205
        counts = {"DSMAX ": 0, "EABSB ": 0, "EABSBH": 0}
        codes = {"DSMAX ": 3017, "EABSB ": 3018, "EABSBH": 3019}
        while True:
            key = {205: "DSMAX ", 207: "EABSB ", 217: "EABSBH"}[label]
            if kw == key:
                counts[key] += 1
                if counts[key] > 50:
                    self.stop(codes[key], "Demasiados cuerpos en la lista (máximo 50).", "Too many bodies in the list.")
                m.body_refs.append((key.strip(), self.body()))
                kw = self.nxt(key != "EABSBH")
                if kw == key:
                    continue
                if label >= 207 and kw == "DSMAX ":
                    label = 205
                    continue
                if label == 217 and kw == "EABSB ":
                    label = 207
                    continue
            if label == 205:
                label = 207
            elif label == 207:
                label = 217
            else:
                break
        if kw == "PFIELD":
            self.vals()
            kw = self.nxt(True)
        nle = nlb = 0
        while kw in ("EFIELD", "BFIELD"):
            if kw == "EFIELD":
                nle += 1
                if nle > 100:
                    self.stop(3020, "Demasiados cuerpos con campo eléctrico (máximo 100).",
                              "Too many bodies in the list.")
            else:
                nlb += 1
                if nlb > 100:
                    self.stop(3021, "Demasiados cuerpos con campo magnético (máximo 100).",
                              "Too many bodies in the list.")
            m.body_refs.append((kw.strip(), self.body()))
            kw = self.nxt(True)
        for key, code in (("IFORCE", 3022), ("IBRSPL", 3023), ("IXRSPL", 3024)):
            n = 0
            while kw == key:
                n += 1
                if n > 100:
                    self.stop(code, "Demasiados cuerpos en la lista (máximo 100).", "Too many bodies in the list.")
                m.body_refs.append((key, self.body()))
                kw = self.nxt(True)
        if kw == "NBE   ":
            v = self.vals()
            if v[2] == 0:
                self.stop(3025, "NBE (número de intervalos de energía) es cero.", "NBE equal to 0.")
            kw = self.nxt(True)
        if kw == "NBANGL":
            v = self.vals()
            if v[0] == 0:
                self.stop(3026, "NBTH (intervalos del ángulo θ) es cero.", "NBTH equal to 0.")
            kw = self.nxt(True)
        # 401: detectores de impacto.
        nidet = 0
        while kw == "IMPDET":
            nidet += 1
            if nidet > 5:
                self.stop(3027, "Demasiados detectores de impacto (máximo 5).", "Too many impact detectors.")
            self.vals()
            kw = self.nxt(True)
            for key in ("IDSPC ", "IDPSF ", "IDFLNC", "IDAGEL", "IDAGEF"):
                if kw == key:
                    self.vals()
                    kw = self.nxt(True)
            nb = 0
            while kw == "IDBODY":
                nb += 1
                if nb > 50:
                    self.stop(3028, "Demasiados cuerpos en el detector (máximo 50).", "Too many bodies in the list.")
                m.body_refs.append(("IDBODY", self.body()))
                kw = self.nxt(True)
            while kw == "IDKPAR":
                kp = self.particle("IDKPAR")
                if kp is not None and not 0 < kp < 7:
                    self.stop(3029, f"IDKPAR: tipo de partícula no definido ({kp}).",
                              "IDKPAR: Undefined particle type.")
                kw = self.nxt(True)
        # 412: detectores angulares.
        nadet = 0
        while kw == "ANGDET":
            nadet += 1
            if nadet > 5:
                self.stop(3030, "Demasiados detectores angulares (máximo 5).", "Too many angular detectors.")
            self.vals()
            kw = self.nxt(True)
            if kw == "ADENER":
                self.vals()
                kw = self.nxt(True)
            if kw == "ADKPAR":
                kp = self.particle("ADKPAR")
                if kp is not None and not 0 < kp < 7:
                    self.stop(30301, f"ADKPAR: tipo de partícula no definido ({kp}).",
                              "ADKPAR: Undefined particle type.")
                kw = self.nxt(True)
                if kw == "ADKPAR":
                    self.stop(30302, "Solo se puede seleccionar un tipo de partícula por detector angular.",
                              "Trying to select more than one particle type?")
            if kw == "ANGDET":
                continue
            if kw == "ADSPC ":
                self.vals()
                kw = self.nxt(True)
        # 416: detectores de energía depositada.
        nedet = 0
        while kw == "ENDETC":
            nedet += 1
            if nedet > 5:
                self.stop(3031, "Demasiados detectores de energía depositada (máximo 5).",
                          "Too many energy-deposition detectors.")
            self.vals()
            kw = self.nxt(True)
            if kw == "EDSPC ":
                self.vals()
                kw = self.nxt(True)
            nb = 0
            while kw == "EDBODY":
                nb += 1
                if nb > 100:
                    self.stop(3032, "Demasiados cuerpos en el detector (máximo 100).", "Too many bodies in the list.")
                m.body_refs.append(("EDBODY", self.body()))
                kw = self.nxt(True)
        for key in ("GRIDX ", "GRIDY ", "GRIDZ ", "GRIDR "):
            if kw == key:
                m.grids[key.strip()] = list(self.vals())
                kw = self.nxt(True)
        if kw == "EPROF ":
            neprom = 0
            while True:
                neprom += 1
                if neprom > 1:
                    self.stop(3033, "EPROF aparece más de una vez.", "EPROF defined more than once.")
                self.vals()
                kw = self.nxt(True)
                if kw != "EPROPM":
                    self.stop(3034, "Tras EPROF se necesita una línea EPROPM.", "A line EPROPM is required.")
                self.vals()
                kw = self.nxt(True)
                if kw != "EPROF ":
                    break
        for key in ("RESUME", "DUMPTO", "DUMPP ", "LTRACK", "NSIMSH", "TIME  "):
            if kw == key:
                self.vals()
                kw = self.nxt(True)
        if kw == "RSEED ":
            seeds = list(SPEC["RSEED "].defaults)  # RSEED_ISEED1 y RSEED_ISEED2 del módulo
            first = True
            while True:
                self.vals()
                seeds = self.keep_previous(seeds, "el valor inicial" if first else "la línea RSEED anterior")
                first = False
                kw = self.nxt(True)
                if kw != "RSEED ":
                    break
        hint = ""
        if "\t" in kw:
            hint = " " + _TAB_HINT.format(kw.replace("\t", "⇥"))
        elif kw.strip() in KNOWN_TYPOS:
            hint = (f" La palabra correcta es '{KNOWN_TYPOS[kw.strip()]}' (penhmain-layout.in dice "
                    f"'{kw.strip()}', pero el código no la reconoce).")
        elif kw not in SPEC:
            hint = f" '{kw.strip()}' no es una palabra clave de penhmain."
        else:
            hint = (f" '{kw.strip()}' está fuera de su sitio: penhmain lee las secciones en un orden fijo "
                    f"(sección «{SPEC[kw].section}»).")
        self.stop(3035, "penhmain no espera esta línea aquí y termina con «Unexpected file termination»." + hint,
                  "Unexpected file termination.")


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------

def _same(a: InLine, b: InLine) -> bool:
    return (a.kw == b.kw and list(a.values) == list(b.values) and (a.text or "") == (b.text or "")
            and a.label == b.label and a.kb == b.kb)


def _reuse(old: list[InLine], new: list[InLine]) -> list[InLine]:
    """Las líneas nuevas iguales a una que ya estaba se sustituyen por la antigua (sin cambios)."""
    pool = list(old)
    out = []
    for ln in new:
        match = next((o for o in pool if _same(o, ln) and not o.error), None)
        if match is not None:
            pool.remove(match)
            out.append(match)
        else:
            ln.modified = True
            out.append(ln)
    return out


def _apply(ln: InLine, values, text, label, kb, comment) -> None:
    """Pone los datos pedidos en ``ln`` y la marca como modificada; antes comprueba con una copia que la línea
    se puede escribir (``format_line`` lanza ``ValueError``), para no dejar la línea a medias."""
    trial = copy.copy(ln)
    if values is not None:
        trial.values = list(values)
    if text is not None:
        trial.text = text
    if label is not None or kb is not None:
        trial.label, trial.kb = label, kb
    if comment is not None:
        trial.comment = comment
    format_line(trial)
    ln.values, ln.text, ln.label, ln.kb, ln.comment = trial.values, trial.text, trial.label, trial.kb, trial.comment
    ln.modified = True
    ln.error = None


def _same_line(ln: InLine, values, text, label, kb, comment) -> bool:
    """¿Pedir estos datos para la línea ``ln`` la dejaría igual? (Una con error o aviso se reescribe siempre.)"""
    if ln.error is not None or ln.warning is not None or ln.hang:
        return False
    if values is not None and list(values) != list(ln.values):
        return False
    if text is not None and text != ln.text:
        return False
    if (label is not None or kb is not None) and (label, kb) != (ln.label, ln.kb):
        return False
    return comment is None or comment == ln.comment


@dataclass
class InDocument:
    path: str | None
    lines: list[InLine]
    newline: str = "\n"
    diagnostics: DiagnosticList = field(default_factory=DiagnosticList)
    model: PenhmainInput = field(default_factory=PenhmainInput)
    stop_line: InLine | None = None  # línea en la que penhmain se detendría

    @property
    def ok(self) -> bool:
        return not self.diagnostics.has_errors

    # -- análisis -------------------------------------------------------------
    def analyze(self) -> None:
        """Vuelve a interpretar las líneas modificadas y recorre el orden."""
        for i, ln in enumerate(self.lines):
            if ln.modified or ln.raw is None:
                text = format_line(ln)
                fresh = parse_line(text, ln.index)
                fresh.modified = True
                fresh.raw = ln.raw
                if fresh.error:  # penhmain no la leería: se escribe tal cual (con el valor que falla, a la vista)
                    fresh.formatted = text
                self.lines[i] = fresh
        self.diagnostics = DiagnosticList()
        for i, ln in enumerate(self.lines):
            if ln.warning:
                self.diagnostics.warning(ln.warning, file=self.path, line=i)
        r = _Reader(self.lines)
        self.stop_line = None
        try:
            r.run()
        except _Done:
            r.m.ok = True
        except _Stop as st:
            self.stop_line = st.line
            self.diagnostics.error(st.message + _stop_hint(self.lines, st), file=self.path, line=self.row(st.line),
                                   code=st.code, fortran_reason=st.reason)
        self.model = r.m
        # Errores que no detienen PMRDR0 (penhmain sigue con un valor sin sentido o indefinido) y avisos que
        # dependen de las líneas anteriores (valores nulos).
        for ln, msg in r.errors:
            self.diagnostics.error(msg, file=self.path, line=self.row(ln), fortran_reason=NO_STOP_REASON)
        for ln, msg in r.notes:
            self.diagnostics.warning(msg, file=self.path, line=self.row(ln))
        # Líneas con errores de lectura que penhmain no llega a leer: aviso.
        reported = {id(ln) for ln, _ in r.errors}
        for i, ln in enumerate(self.lines):
            if ln.error and ln is not self.stop_line and id(ln) not in reported:
                self.diagnostics.warning(ln.error, file=self.path, line=i)
        lone = next((i for i, ln in enumerate(self.lines)
                     if not ln.modified and ln.raw is not None and lone_cr_record([ln.raw]) is not None), None)
        if lone is not None:
            self.diagnostics.info(LONE_CR_INFO, file=self.path, line=lone)

    # -- consultas ---------------------------------------------------------------
    def row(self, line: InLine | None) -> int | None:
        """Línea (0-based) que ocupa ``line`` en el texto actual.

        Los diagnósticos usan esta posición: coincide con la del fichero
        mientras no se inserten o borren líneas y, tras editar, con la del
        texto que se guardaría.
        """
        if line is None:
            return None
        for i, ln in enumerate(self.lines):
            if ln is line:
                return i
        return None

    def text(self) -> str:
        return write_in_text(self)

    def display_lines(self) -> list[str]:
        """Texto de cada línea (sin fin de línea), una por elemento de ``lines`` (un CR ignorado se ve como «␍»)."""
        return [display_record(ln.raw) if not ln.modified and ln.raw is not None else format_line(ln)
                for ln in self.lines]

    def snapshot(self) -> list[InLine]:
        """Copia de las líneas para deshacer."""
        return copy.deepcopy(self.lines)

    def restore(self, lines: list[InLine]) -> None:
        self.lines = copy.deepcopy(lines)
        self.analyze()

    def find(self, keyword: str) -> list[InLine]:
        kw = keyword.ljust(6)
        return [ln for ln in self.lines if ln.kw == kw]

    def first(self, keyword: str) -> InLine | None:
        found = self.find(keyword)
        return found[0] if found else None

    # -- edición -----------------------------------------------------------------
    def _insert_index(self, keyword: str) -> int:
        order = ORDER[keyword.ljust(6)]
        pos = None
        for i, ln in enumerate(self.lines):
            o = ORDER.get(ln.kw)
            if o is not None and o <= order:
                pos = i
        if pos is None:
            return 1 if self.lines else 0
        # Tras la palabra anterior, comentarios (la cabecera de la sección siguiente, «>>>>>>>> Job properties.») y
        # luego una palabra de la sección de la nueva: la nueva va con ella, tras la cabecera (duodécima revisión:
        # RESUME quedaba encima de «Job properties», en la sección anterior).
        sec = SPEC[keyword.ljust(6)].section
        prev = SPEC.get(self.lines[pos].kw)
        j = pos + 1
        while j < len(self.lines) and ORDER.get(self.lines[j].kw) is None:
            j += 1
        if pos + 1 < j < len(self.lines):
            nxt = SPEC.get(self.lines[j].kw)
            if nxt is not None and sec and nxt.section == sec and (prev is None or prev.section != sec):
                return j
        return pos + 1

    def set(self, keyword: str, values: list | None = None, *, text: str | None = None,
            label: str | None = None, kb: int | None = None, comment: str | None = None) -> InLine:
        """Crea o modifica la (primera) línea de ``keyword``.

        ``ValueError`` (sin tocar el documento) si penhmain no podría leer la línea (p. ej. un nombre de
        fichero de más de 20 caracteres).
        """
        kw = keyword.ljust(6)
        spec = SPEC[kw]
        ln = self.first(kw)
        if ln is not None and _same_line(ln, values, text, label, kb, comment):
            return ln  # nada cambia: la línea se queda como está (ni se marca ni se reescribe)
        new = ln is None
        if new:
            ln = InLine(kw=kw, comment=spec.comment if comment is None else comment)
        _apply(ln, values, text, label, kb, comment)
        if new:
            self.lines.insert(self._insert_index(kw), ln)
        self.analyze()
        return self.first(kw)  # type: ignore[return-value]

    def modify(self, line: InLine, values: list | None = None, *, text: str | None = None,
               label: str | None = None, kb: int | None = None, comment: str | None = None) -> None:
        """Cambia una línea concreta (p. ej. la segunda MFNAME). ``ValueError`` como en :meth:`set`."""
        if _same_line(line, values, text, label, kb, comment):
            return
        _apply(line, values, text, label, kb, comment)
        self.analyze()

    def insert_lines(self, pos: int, lines: list[InLine]) -> None:
        for ln in lines:
            format_line(ln)  # ValueError antes de tocar el documento
        for ln in lines:
            ln.modified = True
        self.lines[pos:pos] = lines
        self.analyze()

    def remove(self, keyword: str) -> int:
        kw = keyword.ljust(6)
        n = len(self.lines)
        self.lines = [ln for ln in self.lines if ln.kw != kw]
        removed = n - len(self.lines)
        if removed:
            self.analyze()
        return removed

    def set_repeated(self, keyword: str, entries: list[dict]) -> None:
        """Sustituye todas las líneas de una palabra repetible (p. ej. SBODY), sin mover los comentarios.

        Cada entrada: {'values': [...], 'label': 'ABCD' | None, 'kb': int | None}.
        """
        kw = keyword.ljust(6)
        spec = SPEC[kw]
        new_lines = [InLine(kw=kw, values=list(e.get("values", [])), label=e.get("label"), kb=e.get("kb"),
                            text=e.get("text", ""), comment=spec.comment if i == 0 else "", modified=True)
                     for i, e in enumerate(entries)]
        self.replace_block([kw], new_lines)

    def replace_block(self, keywords, new: list[InLine], anchor: str | None = None) -> bool:
        """Sustituye todas las líneas de ``keywords`` por ``new`` sin mover las demás (comentarios incluidos).

        Una línea nueva igual a una que ya estaba se queda como estaba (con su texto). Las demás se colocan
        como una comparación de las dos listas: la que cambia ocupa el sitio de la que sustituye (y conserva su
        comentario), y solo se añaden (detrás de la línea anterior del bloque) o se quitan las que sobran o
        faltan. Si no había ninguna, van donde las leería penhmain (tras la última palabra anterior en el orden
        de PMRDR0; ``anchor`` es la palabra que decide ese sitio). ``ValueError`` (sin tocar el documento) si una
        línea no se puede escribir. Devuelve False si nada cambia.
        """
        kws = {k.ljust(6)[:6] for k in keywords}
        idx = [i for i, ln in enumerate(self.lines) if ln.kw in kws]
        old = [self.lines[i] for i in idx]
        new = _reuse(old, [copy.copy(ln) for ln in new])
        if len(new) == len(old) and all(a is b for a, b in zip(new, old)):
            return False  # nada cambia: no se mueven las líneas de en medio (p. ej. un «       .» entre detectores)
        ops = difflib.SequenceMatcher(None, [id(x) for x in old], [id(x) for x in new], autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in ops:  # la que sustituye a otra conserva su comentario
            if tag == "replace":
                for o, ln in zip(old[i1:i2], new[j1:j2]):
                    spec = SPEC.get(ln.kw)
                    if o.kw == ln.kw and ln.comment.strip() in ("", spec.comment if spec else ""):
                        ln.comment = o.comment
        for ln in new:
            if ln.modified:
                format_line(ln)  # ValueError antes de tocar el documento
        if not idx:
            pos = self._insert_index((anchor or next(iter(keywords))).ljust(6)[:6])
            self.lines[pos:pos] = new
            self.analyze()
            return True
        before: dict[int, list[InLine]] = {}
        slot: dict[int, list[InLine]] = {}  # línea del documento -> lo que la sustituye
        for tag, i1, i2, j1, j2 in ops:
            if tag == "insert":
                if i1 > 0:
                    slot[idx[i1 - 1]].extend(new[j1:j2])
                else:
                    before.setdefault(idx[0], []).extend(new[j1:j2])
                continue
            common = min(i2 - i1, j2 - j1)
            for t in range(i2 - i1):
                slot[idx[i1 + t]] = [new[j1 + t]] if t < common else []
            if j2 - j1 > common:
                slot[idx[i1 + common - 1]].extend(new[j1 + common:j2])
        out: list[InLine] = []
        for i, ln in enumerate(self.lines):
            out.extend(before.get(i, ()))
            out.extend(slot.get(i, (ln,)))
        self.lines = out
        self.analyze()
        return True


# ---------------------------------------------------------------------------
# Lectura y escritura
# ---------------------------------------------------------------------------



def parse_in_text(text: str, path: str | None = None) -> InDocument:
    raw_lines = split_in_records(text)
    lines = [parse_line(t, i) for i, t in enumerate(raw_lines)]
    doc = InDocument(path=path, lines=lines, newline=newline_of(raw_lines))
    doc.analyze()
    return doc


def parse_in_file(path: str | os.PathLike) -> InDocument:
    p = Path(path)
    return parse_in_text(p.read_bytes().decode("latin-1"), str(p))


def write_in_text(doc: InDocument) -> str:
    """Texto del .in; las líneas sin modificar se copian tal cual.

    Un registro original sin fin de línea (el último, o uno que acaba en un CR que penhmain ignora) lleva uno
    si detrás va otra línea: si no, penhmain las leería como una sola.
    """
    out = []
    n = len(doc.lines)
    for i, ln in enumerate(doc.lines):
        if not ln.modified and ln.raw is not None:
            s = ln.raw
            if i < n - 1 and not record_end(s, TITLE_WIDTH if ln.index == 0 else LINE_WIDTH):
                s += doc.newline
            out.append(s)
        else:
            out.append(format_line(ln) + doc.newline)
    return "".join(out)


def write_in_file(doc: InDocument, path: str | os.PathLike) -> str:
    """Escribe el .in (en Latin-1 y entero o nada). Devuelve los caracteres escritos como «?»."""
    from ..core.files import write_latin1

    return write_latin1(path, write_in_text(doc))
