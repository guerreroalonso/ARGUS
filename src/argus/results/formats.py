"""Lectura rápida de los ficheros de resultados de penhmain.

penhmain escribe sus resultados en ficheros de texto para gnuplot: unas líneas
de comentario que empiezan por «#» y describen las columnas, y filas de
números con formato Fortran (``1P,E14.6``, las incertidumbres con ``E10.2``).
Las líneas en blanco separan bloques (planos de un mapa, intervalos de ángulo,
trayectorias). Un mapa 3D de dosis puede tener millones de filas: los números se
leen con ``numpy.loadtxt`` (en C, ~1 millón de filas de 10 columnas por
segundo) y solo si falla se corrigen antes las rarezas de Fortran:

* un exponente de tres cifras pierde la «E» (``1.234567-100``);
* exponentes con «D» (``1.0D-16``).

penhmain nunca escribe valores menores que 1e-35 en las columnas de valores,
pero sí puede hacerlo en las incertidumbres.
"""

from __future__ import annotations

import io
import os
import re
import warnings
from pathlib import Path

import numpy as np

HEAD_BYTES = 8192
# Número real en cualquiera de las formas que escribe Fortran (con E, con D o sin letra con tres cifras).
NUMBER = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+|[-+]\d{3})?"
_NUMBER_RE = re.compile(NUMBER)
_FORTRAN_EXP3 = re.compile(r"(?<=[\d.])([-+])(\d{3})(?=\s|$)", re.M)
_D_EXP = re.compile(r"(?<=[\d.])[Dd](?=[-+]?\d)")


class FormatError(ValueError):
    """El fichero no tiene la forma esperada."""


def read_bytes(path: str | os.PathLike) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def read_text(path: str | os.PathLike) -> str:
    return read_bytes(path).decode("latin-1")


def read_head(path: str | os.PathLike, nbytes: int = HEAD_BYTES) -> str:
    """Los primeros bytes de un fichero (para reconocerlo)."""
    try:
        with open(path, "rb") as f:
            return f.read(nbytes).decode("latin-1")
    except OSError:
        return ""


def comment_lines(text: str, limit: int | None = None) -> list[str]:
    """Texto de las líneas de comentario (lo que sigue a «#»), en orden, sin espacios a los lados."""
    out = []
    for i, ln in enumerate(text.splitlines()):
        if limit is not None and i >= limit:
            break
        s = ln.lstrip()
        if s.startswith("#"):
            out.append(s[1:].strip())
    return out


def fix_fortran_numbers(text: str) -> str:
    """Pone la «E» que Fortran quita en los exponentes de tres cifras y cambia «D» por «E»."""
    text = _D_EXP.sub("E", text)
    return _FORTRAN_EXP3.sub(r"E\1\2", text)


def parse_float(token: str) -> float:
    return float(fix_fortran_numbers(token.strip()))


def numbers(line: str) -> list[float]:
    """Todos los números de una línea (con las formas de Fortran)."""
    return [parse_float(t) for t in _NUMBER_RE.findall(line)]


def _loadtxt(source, usecols) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # «Empty input file» / «no data»: se trata abajo
        return np.loadtxt(source, comments="#", ndmin=2, usecols=usecols, dtype=np.float64)


def load_table(path: str | os.PathLike, usecols: tuple[int, ...] | None = None) -> np.ndarray:
    """Todas las filas numéricas de un fichero (sin comentarios ni líneas en blanco) como una matriz 2D.

    Una matriz de 0 filas si no hay números.
    """
    try:
        arr = _loadtxt(Path(path), usecols)
    except ValueError:
        return load_table_text(read_text(path), usecols)
    return _empty(arr, usecols)


def load_table_text(text: str, usecols: tuple[int, ...] | None = None) -> np.ndarray:
    """Como :func:`load_table`, con el texto ya leído."""
    try:
        arr = _loadtxt(io.StringIO(text), usecols)
    except ValueError:
        if text and not text.endswith("\n"):
            # Última línea a medias: penhmain está escribiendo el fichero. Se lee lo que ya está completo
            # (los mapas comprueban luego que estén todas sus filas).
            text = text[: text.rfind("\n") + 1]
        try:
            arr = _loadtxt(io.StringIO(fix_fortran_numbers(text)), usecols)
        except ValueError as exc:
            raise FormatError(f"Hay una línea que no es de números: {exc}") from exc
    return _empty(arr, usecols)


def _empty(arr: np.ndarray, usecols) -> np.ndarray:
    if arr.size == 0:
        return np.zeros((0, len(usecols) if usecols else 0))
    return arr


def blocks(text: str) -> list[np.ndarray]:
    """Bloques de filas numéricas separados por líneas en blanco (para ficheros pequeños)."""
    out: list[np.ndarray] = []
    cur: list[list[float]] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            if cur:
                out.append(np.array(cur, dtype=np.float64))
                cur = []
            continue
        if s.startswith("#"):
            continue
        cur.append(numbers(s))
    if cur:
        out.append(np.array(cur, dtype=np.float64))
    return out


FAST_LINES_FROM = 4096  # caracteres: a partir de aquí, block_lengths con numpy (las trayectorias de una cascada)
_OTHER_BREAKS = "\v\f\x1c\x1d\x1e\x1f"  # otros saltos de línea de splitlines() o espacios de strip()


def _block_lengths_fast(text: str) -> list[int] | None:
    """:func:`block_lengths` con numpy (lo mismo, varias veces más deprisa) para texto ASCII con saltos de línea \\n o
    \\r\\n; None si tiene algo más raro o líneas que empiezan con muchos blancos (entonces, línea a línea)."""
    if not text.isascii() or any(ch in text for ch in _OTHER_BREAKS):
        return None
    b = np.frombuffer(text.encode("ascii"), dtype=np.uint8)
    if not len(b):
        return []
    if "\r" in text:
        cr = np.flatnonzero(b == 13)
        if cr[-1] + 1 >= len(b) or np.any(b[cr + 1] != 10):
            return None  # un \r suelto también separa líneas
    nl = np.flatnonzero(b == 10)
    ends = np.concatenate([nl, [len(b)]])
    pos = np.concatenate([[0], nl + 1])
    kind = np.zeros(len(pos), dtype=np.int8)  # 1 en blanco, 2 comentario, 3 números
    active = np.arange(len(pos))
    for _ in range(64):  # el primer carácter que no es un blanco (espacio, tabulador o el \r de \r\n) de cada línea
        p = pos[active]
        done = p >= ends[active]
        kind[active[done]] = 1
        active, p = active[~done], p[~done]
        c = b[p]
        blank = (c == 32) | (c == 9) | (c == 13)
        hit = ~blank
        kind[active[hit]] = np.where(c[hit] == 35, 2, 3)  # «#»: comentario
        active = active[blank]
        if not len(active):
            break
        pos[active] += 1
    else:
        return None
    data = kind[kind != 2] == 3  # sin los comentarios (no separan bloques): números o en blanco
    edges = np.diff(np.concatenate([[0], data.astype(np.int8), [0]]))
    return (np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)).tolist()


def block_lengths(text: str) -> list[int]:
    """Número de filas numéricas de cada bloque (bloques separados por líneas en blanco)."""
    if len(text) >= FAST_LINES_FROM:
        fast = _block_lengths_fast(text)
        if fast is not None:
            return fast
    lengths: list[int] = []
    n = 0
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            if n:
                lengths.append(n)
                n = 0
            continue
        if s.startswith("#"):
            continue
        n += 1
    if n:
        lengths.append(n)
    return lengths


def header_number(lines: list[str], key: str, default: float | None = None) -> float | None:
    """El número que sigue a ``key`` (p. ej. «RU =») en alguna línea de comentario."""
    pat = re.compile(re.escape(key) + r"\s*(" + NUMBER + ")")
    for ln in lines:
        m = pat.search(ln)
        if m:
            return parse_float(m.group(1))
    return default


def header_int(lines: list[str], key: str, default: int | None = None) -> int | None:
    v = header_number(lines, key)
    return default if v is None else int(round(v))


def key_values(text: str) -> dict[str, float]:
    """Ficheros como ``gnu-dose.dat`` («XL=-5.000000E+00», «Gy= 6.24E15    # eV/g»)."""
    out: dict[str, float] = {}
    for ln in text.splitlines():
        if "=" not in ln:
            continue
        k, _, rest = ln.partition("=")
        k = k.strip()
        m = _NUMBER_RE.search(rest)
        if k and m and re.fullmatch(r"[A-Za-z]\w*", k):
            out[k] = parse_float(m.group(0))
    return out


def centers_to_edges(centers: np.ndarray, log: bool | None = None) -> tuple[np.ndarray, bool]:
    """Bordes de los intervalos a partir de sus centros, como los calcula penhmain.

    En una escala lineal los centros están igual de separados; en una
    logarítmica (NBE < 0) cada intervalo es r veces el anterior y penhmain da
    como «centro» la media aritmética de sus bordes: ``c = (a + r·a) / 2``.
    Devuelve (bordes, logarítmica).
    """
    c = np.asarray(centers, dtype=np.float64)
    n = len(c)
    if n == 0:
        return np.zeros(0), False
    if n == 1:
        return np.array([c[0] * 0.5, c[0] * 1.5]) if c[0] > 0 else np.array([c[0] - 0.5, c[0] + 0.5]), False
    d = np.diff(c)
    if log is None:
        lin_dev = float(np.max(np.abs(d - d.mean())) / (abs(d.mean()) or 1.0))
        log = False
        if np.all(c > 0):
            r = c[1:] / c[:-1]
            log_dev = float(np.max(np.abs(r - r.mean())) / r.mean())
            log = log_dev < lin_dev and log_dev < 1e-3 and r.mean() > 1.0 + 1e-9
    if log:
        r = float(np.exp(np.mean(np.log(c[1:] / c[:-1]))))
        lo = 2.0 * c / (1.0 + r)
        return np.append(lo, lo[-1] * r), True
    step = (c[-1] - c[0]) / (n - 1)
    return np.concatenate([c - 0.5 * step, [c[-1] + 0.5 * step]]), False


__all__ = ["FormatError", "HEAD_BYTES", "NUMBER", "block_lengths", "blocks", "centers_to_edges", "comment_lines",
           "fix_fortran_numbers", "header_int", "header_number", "key_values", "load_table", "load_table_text",
           "numbers", "parse_float", "read_bytes", "read_head", "read_text"]
