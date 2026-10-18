"""Datos medidos (o de otro programa) para comparar con penhmain: ficheros de texto o CSV de columnas.

Se admiten los separadores habituales (coma, punto y coma, tabulador o
espacios), la coma decimal española («1,25;3,4») y los números entre comillas
(«"1,25";"3,4"», como exportan algunas hojas de cálculo). Las líneas que no
empiezan por un número (cabeceras, comentarios con #, % o !) se saltan, y se
recuerda la última cabecera para nombrar las columnas. Una fila con menos
columnas que las demás (sin incertidumbre, o con «NaN») las tiene vacías.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .data import Curves, Series

_NUM = re.compile(r"^[-+]?(?:\d+[.,]?\d*|[.,]\d+)(?:[EeDd][-+]?\d+)?$")


@dataclass
class Table:
    columns: list[str]
    data: np.ndarray  # (n, m)
    delimiter: str
    decimal_comma: bool


def _unquote(tok: str) -> str:
    return tok.strip().strip('"').strip("'").strip()


def _is_number(tok: str) -> bool:
    return bool(_NUM.match(_unquote(tok)))


def _split(line: str, delim: str) -> list[str]:
    if delim == " ":
        return [_unquote(t) for t in line.split()]
    return [_unquote(t) for t in next(csv.reader([line], delimiter=delim))]


_GAPS = ("nan", "-", "n/a", "na")


def _cell_ok(tok: str) -> bool:
    """¿Es un número, o un hueco («», «NaN»…)?"""
    t = _unquote(tok)
    return not t or _is_number(t) or t.lower() in _GAPS


def _guess(lines: list[str]) -> tuple[str, bool]:
    """Separador y si la coma es decimal, a partir de las primeras líneas de números."""
    raw = [ln for ln in lines if ln.strip() and _is_number(re.split(r"[;\t, ]+", ln.strip().replace('"', ""))[0])]
    sample = [ln.replace('"', "").replace("'", "") for ln in raw][:20]
    if not sample:
        return " ", False
    if any('"' in ln for ln in raw[:20]):
        # Números entre comillas (hojas de cálculo en español: «"0,5","50,1"» o «"0,5";"50,1"»): el separador es el
        # que queda fuera de las comillas, y una coma dentro de ellas es la decimal.
        for delim in (";", "\t", ","):
            rows = [next(csv.reader([ln.strip()], delimiter=delim)) for ln in raw[:20]]
            if all(len(r) >= 2 and all(_cell_ok(c) for c in r) for r in rows):
                return delim, any("," in c for r in rows for c in r)
    for delim in (";", "\t"):
        if all(delim in ln for ln in sample):
            return delim, any("," in ln for ln in sample)
    if all("," in ln for ln in sample):
        parts = [_split(ln, ",") for ln in sample]
        if all(all(_is_number(p) for p in row if p) for row in parts) and len({len(r) for r in parts}) == 1 \
                and len(parts[0]) >= 2:
            # «1,5 2,3» (coma decimal con espacios) frente a «1.5,2.3» (CSV).
            if all(" " in ln.strip() for ln in sample) and not any("." in ln for ln in sample):
                return " ", True
            return ",", False
        return " ", True
    return " ", False


def read_table_text(text: str) -> Table:
    lines = text.splitlines()
    delim, dec_comma = _guess(lines)
    rows: list[list[float]] = []
    header: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s or s[0] in "#%!":
            if s and s[0] in "#%!":
                header = _split(s[1:].strip(), delim if delim != " " else " ")
            continue
        toks = [t for t in _split(s, delim) if t != ""]
        if not toks or not _is_number(toks[0]):
            header = toks
            continue
        vals = []
        for t in toks:
            if not _is_number(t):
                if t.lower() in _GAPS:
                    vals.append(float("nan"))  # un hueco (sin incertidumbre, por ejemplo)
                    continue
                break
            # Con la coma de separador, una coma dentro de un número solo puede venir de un campo entre comillas: es
            # la coma decimal («"0,5","50,1"», como guardan Google Sheets o LibreOffice en español).
            t = t.replace(",", ".") if dec_comma or delim == "," else t
            try:
                vals.append(float(t.replace("D", "E").replace("d", "e")))
            except ValueError:
                raise ValueError(f"No se entiende el número «{t}» de la línea «{s[:60]}»") from None
        if len(vals) >= 2 and np.isfinite(vals[0]) and np.isfinite(vals[1]):
            rows.append(vals)
    if not rows:
        raise ValueError("No hay filas con al menos dos números")
    # Las columnas de la mayoría de las filas; a las que tienen menos les quedan vacías (NaN).
    lengths = [len(r) for r in rows]
    width = max(set(lengths), key=lambda n: (lengths.count(n), n))
    data = np.array([(r + [float("nan")] * width)[:width] for r in rows], dtype=np.float64)
    cols = header[:width] if len(header) >= width else [f"columna {i + 1}" for i in range(width)]
    return Table(cols, data, delim, dec_comma)


def read_table(path: str | os.PathLike) -> Table:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return read_table_text(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return read_table_text(raw.decode("latin-1", errors="replace"))


def position_scale(label: str) -> float | None:
    """Factor para pasar a cm la posición de una columna según su título («z (mm)», «depth_cm», «x [m]»…); None si
    no dice la unidad. Solo cuenta la unidad como palabra: «gamma» o «comment» no son milímetros."""
    low = label.lower()
    if re.search(r"(?<![a-zµ])mm(?![a-z])", low):
        return 0.1
    if re.search(r"(?<![a-z])cm(?![a-z])", low):
        return 1.0
    if re.search(r"(?<![a-z])m(?![a-z])", low) and re.search(r"[\(\[/_]\s*m\s*[\)\]]?\s*$", low):
        return 100.0
    return None


def measured_curve(path: str | os.PathLike, x_col: int = 0, y_col: int = 1, err_col: int | None = None, *,
                   x_scale: float = 1.0, name: str | None = None, table: Table | None = None) -> Curves:
    """Curva medida: ``x_scale`` pasa la posición a cm (0,1 si está en mm)."""
    t = table or read_table(path)
    x = t.data[:, x_col] * x_scale
    y = t.data[:, y_col]
    err = t.data[:, err_col] if err_col is not None and err_col < t.data.shape[1] else None
    if err is not None and not np.isfinite(err).any():
        err = None
    order = np.argsort(x, kind="stable")
    title = name or Path(path).name
    return Curves("measured", title, x[order], t.columns[x_col] if x_col < len(t.columns) else "x", "cm",
                  t.columns[y_col] if y_col < len(t.columns) else "valor", "",
                  [Series("medida", y[order], None if err is None else err[order])], histogram=False,
                  meta={"path": str(path)})



__all__ = ["Table", "measured_curve", "position_scale", "read_table", "read_table_text"]
