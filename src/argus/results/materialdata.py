"""Tablas de los materiales que escribe penhmain en ``material.dat``.

PEINIT y PHINIT escriben, para cada material, sus datos (fichero, nombre,
densidad) y, si la simulación tiene **un solo material** (INFO = 3 en
penhmain.f), las tablas que usan: poderes de frenado y alcances de electrones,
positrones, protones y alfas, recorridos libres medios y coeficientes de
atenuación de fotones. «mtu» es la unidad de espesor másico (g/cm²).

Aquí se leen las tablas que empiezan por «PENELOPE >>>» o «PENHAN  >>>».
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .formats import NUMBER, numbers, parse_float, read_text

_ORDINAL = re.compile(r"\*\*\s+(\d+)(?:st|nd|rd|th)\s+material\s+\*\*", re.I)


@dataclass
class MaterialTable:
    title: str  # «Stopping powers for electrons»
    source: str  # 'PENELOPE' o 'PENHAN'
    columns: list[str]
    units: list[str]
    data: np.ndarray  # (n, len(columns))

    def column(self, name: str) -> np.ndarray | None:
        for i, c in enumerate(self.columns):
            if c.lower() == name.lower():
                return self.data[:, i]
        return None


@dataclass
class MaterialInfo:
    index: int
    file: str = ""
    name: str = ""
    density: float | None = None  # g/cm³
    tables: list[MaterialTable] = field(default_factory=list)

    def table(self, title_part: str) -> MaterialTable | None:
        for t in self.tables:
            if title_part.lower() in t.title.lower():
                return t
        return None


@dataclass
class MaterialData:
    materials: list[MaterialInfo]
    emin: float | None = None
    emax: float | None = None

    @property
    def has_tables(self) -> bool:
        return any(m.tables for m in self.materials)


def _field_centers(line: str) -> list[float]:
    return [0.5 * (m.start() + m.end()) for m in re.finditer(NUMBER, line)]


def _nearest(centers: list[float], pos: float) -> int:
    return min(range(len(centers)), key=lambda k: abs(centers[k] - pos))


def _table_at(lines: list[str], i: int) -> tuple[MaterialTable | None, int]:
    head = lines[i].strip()
    source, _, title = head.partition(">>>")
    title = title.strip().rstrip(".")
    j = i + 1
    header = None
    while j < len(lines) and j < i + 12:
        s = lines[j].strip()
        if s.lower().startswith("energy") or s.startswith("E ("):
            header = lines[j]
            j += 1
            break
        j += 1
    if header is None:
        return None, i + 1
    unit_line = None
    if j < len(lines) and lines[j].strip().startswith("("):
        unit_line = lines[j]
        j += 1
    if j < len(lines) and set(lines[j].strip()) == {"-"}:
        j += 1
    rows: list[list[float]] = []
    first_row = None
    while j < len(lines):
        s = lines[j].strip()
        if not s or not re.match(NUMBER, s):
            break
        vals = numbers(s)
        if rows and len(vals) != len(rows[0]):
            break
        if first_row is None:
            first_row = lines[j]
        rows.append(vals)
        j += 1
    if not rows:
        return None, j
    n = len(rows[0])
    centers = _field_centers(first_row)
    # Nombres: separados por dos o más espacios («Rad. Yield»), con la unidad entre paréntesis si la llevan.
    names = [t for t in re.split(r"\s{2,}", header.strip()) if t]
    if len(names) != n:
        names = header.split()
    if len(names) != n:
        names = [f"columna {k + 1}" for k in range(n)]
    units = [""] * n
    clean = []
    for k, nm in enumerate(names):
        m = re.match(r"^(.*?)\s*\(([^)]*)\)$", nm)
        if m and m.group(1):
            clean.append(m.group(1).strip())
            units[k] = m.group(2).strip()
        elif nm.lower().startswith("energy/"):
            clean.append("Energy")
            units[k] = nm.split("/", 1)[1]
        else:
            clean.append(nm)
    if clean and clean[0] == "E":
        clean[0] = "Energy"
    if unit_line is not None and centers:
        for m in re.finditer(r"\(([^)]*)\)", unit_line):
            k = _nearest(centers, 0.5 * (m.start() + m.end()))
            if k < n and not units[k]:
                units[k] = m.group(1).strip()
    return MaterialTable(title, source.strip(), clean, units, np.array(rows, dtype=np.float64)), j


def parse_material_dat(text: str) -> MaterialData:
    lines = text.splitlines()
    mats: dict[int, MaterialInfo] = {}
    cur: MaterialInfo | None = None
    emin = emax = None
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = _ORDINAL.search(s)
        if m:
            k = int(m.group(1))
            cur = mats.setdefault(k, MaterialInfo(k))
            i += 1
            continue
        if s.startswith("EMIN ="):
            v = re.findall(NUMBER, s)
            if len(v) >= 2:
                emin, emax = parse_float(v[0]), parse_float(v[1])
        elif cur is not None and s.startswith("Material data file:"):
            cur.file = cur.file or s.split(":", 1)[1].strip()
        elif cur is not None and s.startswith("Material:") and not cur.name:
            cur.name = s.split(":", 1)[1].strip()
        elif cur is not None and s.startswith("Mass density =") and cur.density is None:
            v = re.findall(NUMBER, s)
            if v:
                cur.density = parse_float(v[0])
        elif cur is not None and ">>>" in s and (s.startswith("PENELOPE") or s.startswith("PENHAN")):
            table, j = _table_at(lines, i)
            if table is not None:
                cur.tables.append(table)
            i = max(j, i + 1)
            continue
        i += 1
    return MaterialData([mats[k] for k in sorted(mats)], emin, emax)


def read_material_dat(path: str | os.PathLike) -> MaterialData:
    return parse_material_dat(read_text(Path(path)))


def table_value(table: MaterialTable, column: str, energy: float) -> float | None:
    """Valor de una columna a la energía dada (eV), por interpolación log-log; None fuera de la tabla."""
    e = table.column("Energy")
    v = table.column(column)
    if e is None or v is None or not energy > 0:
        return None
    ok = (v > 1e-30) & (e > 0)
    if ok.sum() < 2 or energy < e[ok][0] or energy > e[ok][-1]:
        return None
    return float(np.exp(np.interp(np.log(energy), np.log(e[ok]), np.log(v[ok]))))


# Tabla de cada tipo de partícula con su poder de frenado (columnas que se suman) y su alcance CSDA.
STOPPING_TABLES = {
    1: ("Stopping powers for electrons", ("Scol", "Srad")),
    3: ("Stopping powers for positrons", ("Scol", "Srad")),
    4: ("Stopping powers for protons", ("Scol",)),
    5: ("Stopping powers for alphas", ("Scol",)),
}
ATTENUATION_TABLE = "Photon mass attenuation coefficients"


def stopping_power(info: MaterialInfo, kpar: int, energy: float) -> float | None:
    """Poder de frenado másico total (eV·cm²/g) de la partícula ``kpar`` a la energía dada (eV)."""
    spec = STOPPING_TABLES.get(kpar)
    t = info.table(spec[0]) if spec else None
    if t is None:
        return None
    vals = [table_value(t, c, energy) for c in spec[1]]
    if vals[0] is None:
        return None
    return float(sum(v for v in vals if v is not None))


def csda_range(table: MaterialTable, energy: float, density: float | None = None) -> float | None:
    """Alcance CSDA a la energía dada (interpolación log-log de la columna «range»); en cm si se da la densidad
    (la tabla está en g/cm²)."""
    val = table_value(table, "range", energy)
    if val is None:
        return None
    return val / density if density else val


def energy_for_range(table: MaterialTable, rng: float, density: float | None = None) -> float | None:
    """Energía con la que el alcance CSDA es ``rng`` (cm si se da la densidad; g/cm² si no)."""
    e = table.column("Energy")
    r = table.column("range")
    if e is None or r is None or rng <= 0:
        return None
    target = rng * density if density else rng
    ok = r > 1e-30
    if ok.sum() < 2 or target < r[ok][0] or target > r[ok][-1]:
        return None
    return float(np.exp(np.interp(np.log(target), np.log(r[ok]), np.log(e[ok]))))


__all__ = ["ATTENUATION_TABLE", "MaterialData", "MaterialInfo", "MaterialTable", "STOPPING_TABLES", "csda_range",
           "energy_for_range", "parse_material_dat", "read_material_dat", "stopping_power", "table_value"]
