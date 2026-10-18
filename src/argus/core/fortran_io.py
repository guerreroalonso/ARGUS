"""Lectura y escritura con las reglas de E/S con formato de Fortran.

PENGEOM y penhmain leen sus ficheros con formatos fijos (``A8``, ``E22.15``,
``I4``...). Para que ARGUS vea exactamente lo mismo que el programa Fortran,
este módulo imita la lectura con formato de gfortran:

* Los blancos dentro de un campo numérico se ignoran (``BLANK='NULL'``) y un
  campo totalmente en blanco vale 0.
* En los campos ``Ew.d``/``Fw.d`` sin punto decimal se aplica el punto
  implícito: los ``d`` dígitos de la derecha son decimales. Por ejemplo,
  ``'250'`` leído con ``E22.15`` vale ``2.5E-13``.
* Una coma dentro del ancho de un campo numérico es un error de lectura: PENGEOM
  lee cada línea en una variable de texto (``READ(BLINE,...)``, lectura
  interna) y, en las lecturas internas, gfortran no toma la coma como fin del
  campo (comprobado con gfortran: ``Z-SHIFT=(2.5,    0)`` o un «2,5» escrito con
  coma decimal dan «Wrong input format», IRETRN = 2075).
* Los registros se leen como ``A72``: todo lo que haya después de la
  columna 72 se ignora.
* Fin de registro: «\\n» y «\\r\\n» siempre. Un «\\r» suelto (sin «\\n» detrás)
  también termina el registro si está dentro de las columnas que lee el READ
  (72 en PENGEOM y en el TITLE del .in, 127 en el resto del .in): gfortran
  para ahí y lo que sigue en la misma línea es el registro siguiente. Más allá
  de esas columnas, gfortran salta hasta el «\\n» y el «\\r» se ignora
  (comprobado con PENGEOM y penhmain). Un fichero de Mac antiguo (solo «\\r»)
  se lee, pues, línea a línea; «\\r\\r\\n» añade un registro en blanco.

Números de línea: los diagnósticos de ARGUS cuentan registros, como gfortran.
Los paneles de texto muestran una línea por registro (un «\\r» que gfortran
ignora se ve como «␍»), de modo que la línea de un mensaje es la que se ve.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

RECORD_LENGTH = 72

_RECORD = re.compile(r"[^\n]*\n|[^\n]+\Z")
_LONE_CR = re.compile(r"\r(?!\n)")
CR_SYMBOL = "␍"  # «␍»: un CR que gfortran ignora, en los paneles de texto


def split_records(text: str, width: int | None = None, first_width: int | None = None) -> list[str]:
    """Registros de un fichero de texto como los lee gfortran (cada uno con su fin de línea).

    Sin ``width`` (lecturas dirigidas por lista, ficheros que no son de Fortran) solo parte en «\\n».
    ``str.splitlines`` también parte en \\x0b, \\x0c, \\x1c-\\x1e, \\x85 (el «…» de Windows leído
    como latin-1), \\u2028 y \\u2029, que para Fortran son caracteres normales dentro de una línea:
    un comentario con «…» partía la línea y ARGUS rechazaba una geometría que PENGEOM lee.

    Con ``width`` (columnas del READ con formato: 72 en el .geo, 127 en el .in), un «\\r» suelto en
    esas columnas termina el registro, como en gfortran; ``first_width`` es la anchura del primer
    registro (el TITLE del .in se lee con 72 columnas). ``"".join(split_records(t, ...)) == t``.
    """
    if width is None or _LONE_CR.search(text) is None:
        return _RECORD.findall(text)
    out: list[str] = []
    n = len(text)
    pos = 0
    w = first_width or width
    while pos < n:
        nl = text.find("\n", pos)
        end = n if nl < 0 else nl + 1
        cr = text.find("\r", pos, min(pos + w, end))
        if cr >= 0 and cr + 1 != nl:  # CR suelto dentro de las columnas que se leen
            end = cr + 1
        out.append(text[pos:end])
        pos = end
        w = width
    return out


def text_records(text: str, width: int | None = None, first_width: int | None = None) -> list[str]:
    """Como :func:`split_records`, sin el fin de línea (ni el «\\r» de «\\r\\n» o el «\\r» suelto)."""
    return [r.rstrip("\n").removesuffix("\r") for r in split_records(text, width, first_width)]


def record_end(raw: str, width: int = RECORD_LENGTH) -> str:
    """Fin de registro de ``raw`` (un registro de :func:`split_records`): «\\r\\n», «\\n», «\\r» o «» si no tiene.

    Un «\\r» final más allá de la columna ``width`` no termina nada (gfortran lo ignora): si detrás se
    escribe otra línea, hay que añadir un fin de línea o se uniría a este registro.
    """
    if raw.endswith("\n"):
        return "\r\n" if raw.endswith("\r\n") else "\n"
    if raw.endswith("\r") and len(raw) <= width:
        return "\r"
    return ""


def lone_cr_record(records: list[str]) -> int | None:
    """Índice del primer registro con un «\\r» suelto (fin de registro o ignorado), o None."""
    for i, r in enumerate(records):
        if _LONE_CR.search(r):
            return i
    return None


def newline_of(records: list[str]) -> str:
    """Fin de línea para las líneas nuevas: «\\r\\n» si el fichero usa «\\r», si no «\\n».

    Nunca un «\\r» suelto: una línea nueva más larga que las columnas que se leen se uniría a la siguiente.
    """
    return "\r\n" if any(r.endswith("\r") or r.endswith("\r\n") for r in records) else "\n"


def display_record(raw: str) -> str:
    """Texto de un registro para mostrarlo en una sola línea: sin fin de línea y con los «\\r» que quedan como «␍».

    Qt parte las líneas en cualquier «\\r»; así, cada registro es una línea del panel.
    """
    return raw.rstrip("\n").removesuffix("\r").replace("\r", CR_SYMBOL)


class FortranReadError(ValueError):
    """El contenido de un campo no se puede leer con el formato indicado."""


class NonFiniteFieldError(FortranReadError):
    """Un campo real con Inf, NaN o un número fuera de rango (1E+999): gfortran lo lee sin avisar, ARGUS no."""


class NonFiniteValueError(ValueError):
    """Un valor no finito (Inf o NaN) no se puede escribir en un fichero de PENGEOM."""


def record(line: str, length: int = RECORD_LENGTH) -> str:
    """Devuelve la línea tal como la vería un ``READ(...,'(A72)')``.

    Se quitan los finales de línea, se recorta a ``length`` columnas y se
    rellena con blancos por la derecha.
    """
    line = line.rstrip("\r\n")
    if len(line) > length:
        line = line[:length]
    return line.ljust(length)


_REAL_RE = re.compile(
    r"^(?P<sign>[+-]?)(?P<int>\d*)(?:\.(?P<frac>\d*))?"
    r"(?:(?:[EeDdQq](?P<esign1>[+-]?)|(?P<esign2>[+-]))(?P<exp>\d+))?$"
)
_INF_NAN_RE = re.compile(r"^[+-]?(inf|infinity|nan(\([^)]*\))?)$", re.IGNORECASE)


def parse_real(field: str, decimals: int = 0) -> float:
    """Convierte un campo ``Ew.d``/``Fw.d``/``Dw.d`` como lo haría gfortran.

    ``decimals`` es el ``d`` del descriptor; solo se usa si el campo no
    contiene punto decimal. Un valor no finito (``Inf``, ``NaN`` o un número
    fuera de rango como ``1.0E+999``, que gfortran lee como infinito sin avisar)
    lanza :class:`NonFiniteFieldError`.
    """
    text = field.replace(" ", "")
    if text == "":
        return 0.0
    if _INF_NAN_RE.match(text):
        raise NonFiniteFieldError(f"valor no finito: {field.strip()!r}")
    value = _parse_finite(field, text, decimals)
    if not math.isfinite(value):
        raise NonFiniteFieldError(f"valor fuera de rango: {field.strip()!r}")
    return value


# Una mantisa sin cifras (con o sin signo y punto, y quizá un exponente: «.», «-.», «E5», «.E5», «--1», «+-1»)
# gfortran la lee como 0: un signo tras el de la mantisa empieza el exponente (read_f de libgfortran).
_NO_DIGITS_RE = re.compile(r"^[+-]?\.?(?:(?:[EeDdQq][+-]?|[+-])\d+)?$")


def _parse_finite(field: str, text: str, decimals: int) -> float:
    m = _REAL_RE.match(text)
    if not m or (m.group("int") == "" and not m.group("frac")):
        if _NO_DIGITS_RE.match(text):
            return 0.0
        raise FortranReadError(f"campo real no válido: {field!r}")
    sign = -1.0 if m.group("sign") == "-" else 1.0
    int_part = m.group("int") or ""
    frac_part = m.group("frac")
    exp_digits = m.group("exp")
    exp_sign = m.group("esign1") if m.group("esign1") is not None else m.group("esign2")
    exponent = 0
    if exp_digits:
        exponent = int(exp_digits)
        if exp_sign == "-":
            exponent = -exponent
    # Como libgfortran, se construye el número decimal completo y se convierte
    # con redondeo correcto (strtod); multiplicar por 10**e no lo sería.
    if frac_part is None:
        # Sin punto decimal: punto implícito con 'decimals' cifras decimales.
        digits = int_part or "0"
        value = float(f"{digits}e{exponent - decimals}")
    else:
        value = float(f"{int_part or '0'}.{frac_part or '0'}e{exponent}")
    return sign * value


_EXP3_RE = re.compile(r"^([+-]?\d*\.?\d*)([+-]\d{3,})$")


def output_float(text: str) -> float:
    """Un real escrito por Fortran con formato E/ES: con exponente de tres cifras, gfortran quita la «E»
    («-2.0000000000000000+160»)."""
    try:
        return float(text)
    except ValueError:
        m = _EXP3_RE.match(text.strip())
        if m is None:
            raise
        return float(f"{m.group(1)}e{m.group(2)}")


def parse_int(field: str) -> int:
    """Convierte un campo ``Iw`` (blancos ignorados; todo blanco = 0).

    Como gfortran (read_decimal): un signo que acaba el campo es un error (« +», «   -»); seguido de blancos,
    vale 0 («+  »).
    """
    if field.lstrip(" ") in ("+", "-"):
        raise FortranReadError(f"campo entero no válido: {field!r}")
    text = field.replace(" ", "")
    if text in {"", "+", "-"}:
        return 0
    if not re.fullmatch(r"[+-]?\d+", text):
        raise FortranReadError(f"campo entero no válido: {field!r}")
    return int(text)


@dataclass(frozen=True)
class Item:
    """Descriptor de edición de un formato Fortran sencillo."""

    kind: str  # 'A', 'X', 'I', 'E'
    width: int
    decimals: int = 0


def A(width: int) -> Item:
    return Item("A", width)


def X(width: int) -> Item:
    return Item("X", width)


def I(width: int) -> Item:  # noqa: E743 - nombre del descriptor Fortran
    return Item("I", width)


def E(width: int, decimals: int) -> Item:
    return Item("E", width, decimals)


def read_record(line: str, items: list[Item]) -> list:
    """Lee un registro con una lista de descriptores (A, X, I, E).

    Imita la lectura interna con formato de gfortran (como ``READ(BLINE,...)``
    en PENGEOM): una coma dentro de un campo numérico es un error. Devuelve la
    lista de valores leídos (los ``X`` no producen valor).

    Al leer de una variable, gfortran no cuenta los blancos del final (``is_trim_ok`` en libgfortran): un campo
    numérico que llega más allá solo tiene lo que hay antes. Así, un signo que acaba el registro es un error
    («INDICES=( 1, 1,-»: IRETRN 2075 en PENGEOM), aunque seguido de blancos dentro del registro valga 0.
    """
    rec = record(line)
    end = len(rec.rstrip(" "))
    pos = 0
    values: list = []
    for it in items:
        if it.kind == "X":
            pos += it.width
            continue
        field = rec[pos : pos + it.width]
        if it.kind == "A":
            values.append(field.ljust(it.width))
            pos += it.width
            continue
        # Campo numérico. En una lectura interna, gfortran no acepta una coma dentro del campo.
        if "," in field:
            raise FortranReadError(f"coma dentro de un campo numérico: {field!r}")
        field = rec[pos : max(min(pos + it.width, end), pos)]  # sin los blancos del final del registro
        pos += it.width
        if it.kind == "I":
            values.append(parse_int(field))
        elif it.kind == "E":
            values.append(parse_real(field, it.decimals))
        else:  # pragma: no cover - descriptores no soportados
            raise ValueError(f"descriptor no soportado: {it.kind}")
    return values


# ---------------------------------------------------------------------------
# Escritura con formato (equivalente a los WRITE de PENGEOM/PenGeomJar).
# ---------------------------------------------------------------------------

def format_e22_15(value: float) -> str:
    """Escribe un real como el ``1P,E22.15`` de gfortran (22 caracteres).

    Es el formato que usan PENGEOM (geometry.rep) y PenGeomJar:
    ``' 2.500000000000000E+02'`` (blanco en lugar de '+'),
    ``'-3.000000000000000E+00'``. Con exponentes de tres cifras se quita un
    decimal para mantener el ancho de 22 columnas. Inf y NaN lanzan
    :class:`NonFiniteValueError` (con un mensaje para el usuario).
    """
    value = float(value)
    if not math.isfinite(value):
        raise NonFiniteValueError(f"El valor {value} no es un número finito (Inf o NaN): no se puede escribir en el "
                                  ".geo (formato E22.15 de PENGEOM).")
    if value == 0.0:
        return " 0.000000000000000E+00"
    text = f"{value:.15E}"
    mant, exp = text.split("E")
    exp_val = int(exp)
    if abs(exp_val) >= 100:
        text = f"{value:.14E}"
        mant, exp = text.split("E")
        exp_val = int(exp)
        out = f"{mant}E{exp_val:+04d}"
    else:
        out = f"{mant}E{exp_val:+03d}"
    return out.rjust(22)


def format_i(value: int, width: int) -> str:
    text = str(int(value))
    if len(text) > width:
        return "*" * width
    return text.rjust(width)
