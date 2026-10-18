"""Lectura dirigida por lista (``READ(BUFFER,*)``) como la hace gfortran.

Solo lo que necesitan los ficheros .in de penhmain: enteros, reales y
cadenas, separadores (blancos y comas), factores de repetición ``r*c`` y
``r*`` (valor nulo), barra ``/`` que termina la lectura y el hecho de que
leer de una variable interna de un solo registro falle si faltan valores.

Como en libgfortran, un factor de repetición no se expande: el valor cuenta
``r`` veces y solo se toman los que pide el READ (``SPOSIT 200000000*1.5``
no ocupa memoria). ``r`` va de 1 a 200 000 000 («Zero repeat count» y
«Repeat count overflow»); lo que va detrás de los valores que se leen no se
interpreta, como en gfortran.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_INT = re.compile(r"^[+-]?\d+$")
_REAL = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([EeDdQq][+-]?\d+|[+-]\d+)?$")
_REPEAT = re.compile(r"(\d+)\*")
SEPARATORS = " \t,/"
MAX_REPEAT = 200000000  # libgfortran (list_read.c): «Repeat count overflow» por encima


class ListReadError(ValueError):
    """penhmain se detendría con un error de ejecución de Fortran."""


INT4_MAX = 2147483647  # penhmain lee los enteros en INTEGER*4


@dataclass
class Token:
    text: str | None  # None = valor nulo
    start: int
    end: int
    quoted: bool = False  # entre apóstrofos o comillas: para gfortran es texto, no un número
    count: int = 1  # factor de repetición (r*c): el valor cuenta r veces
    error: str | None = None  # error de gfortran al leer este valor (solo si se llega a leer)
    bad_string: str | None = None  # cadena entre apóstrofos seguida de algo que no es un separador


def _quoted(buf: str, i: int) -> tuple[str, int, bool]:
    """Cadena entre apóstrofos o comillas que empieza en ``buf[i]``: (texto, fin, cerrada).

    Sin la comilla final, la cadena llega hasta el final del registro (blancos incluidos), como en gfortran.
    """
    q = buf[i]
    n = len(buf)
    j = i + 1
    chars = []
    while j < n:
        if buf[j] == q:
            if j + 1 < n and buf[j + 1] == q:
                chars.append(q)
                j += 2
                continue
            return "".join(chars), j + 1, True
        chars.append(buf[j])
        j += 1
    return "".join(chars), n, False


def _word_end(buf: str, j: int) -> int:
    n = len(buf)
    while j < n and buf[j] not in SEPARATORS:
        j += 1
    return j


def tokenize(buf: str, lead_null: bool = True) -> tuple[list[Token], int]:
    """Tokens hasta el final o hasta una barra. Devuelve (tokens, fin).

    ``lead_null``: una coma al principio es un valor nulo (primer registro de la lectura); en un registro en el
    que la lectura continúa, el fin del registro anterior ya separa y la coma no añade un valor.
    """
    toks: list[Token] = []
    i = 0
    n = len(buf)
    prev_comma = lead_null  # una coma inicial produce un valor nulo
    while i < n:
        c = buf[i]
        if c in " \t":
            i += 1
            continue
        if c == ",":
            if prev_comma:
                toks.append(Token(None, i, i))
            prev_comma = True
            i += 1
            continue
        if c == "/":
            return toks, i
        start = i
        count = 1
        error = None
        m = _REPEAT.match(buf, i)
        if m:
            count = int(m.group(1))
            if count == 0:
                error = f"'{buf[start:_word_end(buf, i)]}': factor de repetición cero (gfortran: «Zero repeat count»)"
            elif count > MAX_REPEAT:
                error = (f"'{m.group(0)}': factor de repetición mayor que {MAX_REPEAT} (gfortran: «Repeat count "
                         "overflow»)")
            i = m.end()
            if i >= n or buf[i] in SEPARATORS:  # «r*»: r valores nulos
                toks.append(Token(None, start, i, count=count, error=error))
                prev_comma = False
                continue
        if buf[i] in "'\"":
            text, j, closed = _quoted(buf, i)
            bad = None
            if closed and j < n and buf[j] not in SEPARATORS:
                k = _word_end(buf, j)
                bad = (f"tras la cadena '{text}' viene «{buf[j:k]}» sin un blanco o una coma en medio (gfortran: "
                       "«Invalid string input»)")
                j = k
            toks.append(Token(text, start, j, quoted=True, count=count, error=error, bad_string=bad))
            i = j
        else:
            j = _word_end(buf, i)
            toks.append(Token(buf[i:j], start, j, count=count, error=error))
            i = j
        prev_comma = False
    return toks, n


SEMICOLON_TEXT = ("el «;» no separa valores en la lectura de penhmain (con punto decimal, gfortran lo rechaza o lo "
                  "lee mal según su versión): use blancos o comas")


def to_int(text: str) -> int:
    if ";" in text:
        raise ListReadError(f"'{text}': {SEMICOLON_TEXT}")
    if not _INT.match(text):
        raise ListReadError(f"'{text}' no es un entero válido")
    v = int(text)
    if not -INT4_MAX - 1 <= v <= INT4_MAX:
        raise ListReadError(f"'{text}' no cabe en un entero de 4 bytes (máximo {INT4_MAX}; gfortran: «Integer "
                            "overflow»)")
    return v


_INF_NAN = re.compile(r"^([+-]?)(inf|infinity|nan(\([^)]*\))?)$", re.IGNORECASE)


def to_real(text: str) -> float:
    if ";" in text:
        raise ListReadError(f"'{text}': {SEMICOLON_TEXT}")
    t = text
    m = _INF_NAN.match(t)
    if m:  # gfortran también los acepta en la lectura dirigida por lista
        if m.group(2).lower().startswith("nan"):
            return float("nan")
        return float("-inf") if m.group(1) == "-" else float("inf")
    if not _REAL.match(t):
        raise ListReadError(f"'{text}' no es un número real válido")
    t = re.sub(r"[DdQq]", "E", t)
    # Exponente sin letra (p. ej. 1.0+05): forma válida en Fortran.
    m = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+))([+-]\d+)$", t)
    if m:
        t = f"{m.group(1)}E{m.group(2)}"
    return float(t)


_TYPE_NAME = {"int": "INTEGER", "real": "REAL", "str": "CHARACTER"}
_TYPE_WORD = {"int": "un entero", "real": "un real", "str": "un texto"}


def repeat_type_error(count: int, first: str, kind: str, item: int) -> str:
    """Mensaje de gfortran cuando un factor de repetición ``r*c`` pasa a una variable de otro tipo (la lectura se
    detiene con un error)."""
    return (f"el factor de repetición «{count}*» da el mismo valor a {_TYPE_WORD.get(first, first)} y a "
            f"{_TYPE_WORD.get(kind, kind)} (gfortran: «Read type {_TYPE_NAME.get(first, first)} where "
            f"{_TYPE_NAME.get(kind, kind)} was expected for item {item}»)")


def read_list(buf: str, kinds: list[str], defaults: list | None = None,
              nulls: list[int] | None = None) -> tuple[list, int]:
    """Lee ``len(kinds)`` valores ('int', 'real', 'str').

    Devuelve los valores y la posición donde termina el último leído (lo que
    sigue es comentario). Los valores nulos (y los que una barra deja sin leer)
    conservan ``defaults``; si se da ``nulls``, se le añaden sus índices (en
    Fortran la variable conserva el valor que tuviera).
    """
    toks, stop = tokenize(buf)
    need = len(kinds)
    vals = list(defaults) if defaults is not None else [None] * need
    k = 0
    end = 0
    for tok in toks:
        if k >= need:
            break
        if tok.error:
            raise ListReadError(tok.error)
        end = tok.end
        k0 = k
        for _ in range(min(tok.count, need - k)):
            kind = kinds[k]
            if k > k0 and tok.text is not None and kind != kinds[k0]:
                raise ListReadError(repeat_type_error(tok.count, kinds[k0], kind, k + 1))
            if tok.text is None:
                if nulls is not None:
                    nulls.append(k)
            elif tok.quoted and kind in ("int", "real"):
                raise ListReadError(f"'{tok.text}' está entre apóstrofos y aquí va un número (gfortran: «Bad "
                                    f"{'integer' if kind == 'int' else 'real'}»)")
            elif tok.bad_string:
                raise ListReadError(tok.bad_string)
            elif kind == "int":
                vals[k] = to_int(tok.text)
            elif kind == "real":
                vals[k] = to_real(tok.text)
            else:
                vals[k] = tok.text
            k += 1
    if k < need:
        if stop >= len(buf):
            # Sin barra, faltan datos: el READ interno llega al final del registro.
            raise ListReadError(f"faltan valores: se esperan {need} y hay {k}")
        if nulls is not None:
            nulls.extend(range(k, need))
    return vals, end


def read_pairs(text: str) -> tuple[int, float]:
    """(pares leídos, mayor segundo valor) de ``READ(u,*,ERR=1,END=2) X,Y`` repetido hasta el final del fichero,
    como la tabla de sección eficaz de EPROF en penhmain (PMAP0).

    Cada READ empieza en un registro nuevo y sigue en los siguientes hasta tener sus dos valores (los que sobran
    en el registro se pierden); una barra lo termina (Y conserva el valor anterior); con un error (``ERR=1``) se
    vuelve a leer desde el registro siguiente; y si el fichero se acaba a medias (``END=2``), ese par no cuenta.
    """
    records = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if records and records[-1] == "":
        records.pop()
    n, ymax, y = 0, 0.0, 0.0
    i = 0
    while i < len(records):
        vals: list[float] = []
        first = True
        failed = slash = False
        while len(vals) < 2 and i < len(records):
            toks, stop = tokenize(records[i], lead_null=first)
            first = False
            i += 1
            try:
                for tok in toks:
                    if tok.error or tok.quoted or tok.bad_string:
                        raise ListReadError(tok.error or "texto")
                    for _ in range(tok.count):
                        if len(vals) >= 2:
                            break
                        vals.append(math.nan if tok.text is None else to_real(tok.text))
                    if len(vals) >= 2:
                        break
            except ListReadError:
                failed = True
                break
            if stop < len(records[i - 1]) and len(vals) < 2:
                slash = True
                break
        if failed:
            continue
        if len(vals) < 2 and not slash:
            break  # END: el fichero se acaba a medias
        if len(vals) >= 2 and not math.isnan(vals[1]):
            y = vals[1]
        n += 1
        ymax = max(ymax, y)
    return n, ymax
