"""Lectura rápida de un .in de penhmain: fichero de geometría y PARINP.

Es un subconjunto del lector completo del .in (fase F4). Reproduce cómo lee
penhmain estas líneas (penhmain.f 2024):

* registro ``(A6,1X,A120)``: palabra clave en las columnas 1-6 y datos desde
  la columna 8 (los registros se separan como en el lector completo: un CR
  suelto en las columnas que se leen también termina el registro);
* ``GEOMFN``: nombre ``A20`` (columnas 8-27); solo se quitan los blancos
  finales, como en el OPEN de penhmain (un tabulador final es parte del nombre);
* las líneas ``PARINP IP, valor`` deben seguir a ``GEOMFN`` (las líneas con la
  palabra clave en blanco se saltan) y se leen como el lector completo;
  ``NPINP`` es el mayor índice (hasta NPINPM = 50) y los índices no dados
  valen 0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.fortran_io import text_records
from .inputfile import LINE_WIDTH, NPINPM, TITLE_WIDTH
from .listdirected import ListReadError, read_list


@dataclass
class InputSummary:
    path: Path
    geomfn: str | None = None
    parinp: dict[int, float] = field(default_factory=dict)
    geomfn_line: int | None = None
    mtime: float | None = None  # fecha de modificación del fichero (s desde 1970)

    def parinp_list(self) -> list[float]:
        """PARINP(1..n) para PENGEOM (los índices mayores que NPINPM = 50 no caben en la tabla de penhmain)."""
        idx = [i for i in self.parinp if 1 <= i <= NPINPM]
        if not idx:
            return []
        return [self.parinp.get(i, 0.0) for i in range(1, max(idx) + 1)]


def scan_input_text(text: str, path: str | os.PathLike = "") -> InputSummary:
    res = InputSummary(Path(path))
    # Como el lector completo: solo «\\n» y un CR suelto en las columnas que se leen parten la línea
    # (un «…» de Windows no la parte).
    lines = text_records(text, LINE_WIDTH, first_width=TITLE_WIDTH)
    i = 0
    while i < len(lines):
        rec = lines[i].ljust(127)
        kw, buf = rec[:6], rec[7:127]
        if kw == "GEOMFN":
            res.geomfn = buf[:20].rstrip(" ")
            res.geomfn_line = i
            j = i + 1
            while j < len(lines):
                r = lines[j].ljust(127)
                k2, b2 = r[:6], r[7:127]
                if k2 == "      ":
                    j += 1
                    continue
                if k2 != "PARINP":
                    break
                nulls: list[int] = []
                try:
                    (ip, val), _ = read_list(b2, ["int", "real"], [0, 0.0], nulls)
                except ListReadError:
                    break
                if ip >= 1 and 0 not in nulls:
                    res.parinp[ip] = float(val)
                j += 1
            i = j
            continue
        i += 1
    return res


def scan_input(path: str | os.PathLike) -> InputSummary:
    p = Path(path)
    res = scan_input_text(p.read_bytes().decode("latin-1"), p)
    try:
        res.mtime = p.stat().st_mtime
    except OSError:  # pragma: no cover - el fichero se acaba de leer
        pass
    return res


def names_geometry(folder: Path, geomfn: str | None, geo_path: Path) -> bool:
    """¿El GEOMFN de un .in de ``folder`` abre este fichero de geometría?

    penhmain abre GEOMFN en su carpeta de trabajo (la del .in). En Windows y
    en macOS los nombres no distinguen mayúsculas: ``CUBO.GEO`` abre
    ``cubo.geo``; por eso se compara el fichero, no solo el nombre.
    """
    if not geomfn:
        return False
    if geomfn == geo_path.name and folder == geo_path.parent:
        return True
    try:
        return os.path.samefile(folder / geomfn, geo_path)
    except (OSError, ValueError):
        return False


def inputs_for_geometry(geo_path: str | os.PathLike) -> list[InputSummary]:
    """Ficheros .in de la carpeta de la geometría cuyo GEOMFN es ella (por orden alfabético)."""
    g = Path(geo_path)
    out = []
    try:
        candidates = sorted((f for f in g.parent.iterdir() if f.suffix.lower() == ".in" and f.is_file()),
                            key=lambda f: f.name.lower())
    except OSError:
        return out
    for f in candidates:
        try:
            s = scan_input(f)
        except OSError:
            continue
        if names_geometry(f.parent, s.geomfn, g):
            out.append(s)
    return out


def looks_like_input(path) -> bool:
    """¿Es ``path`` un fichero de entrada de penhmain que ya existe? (con TITLE y palabras clave suyas en las
    primeras líneas). Para no escribir encima una geometría."""
    p = Path(path)
    try:
        if not p.is_file():
            return False
        with open(p, "rb") as f:
            head = f.read(65536).decode("latin-1", errors="replace")
    except OSError:
        return False
    keys = {ln[:6].strip() for ln in head.splitlines()}
    return "TITLE" in keys and bool(keys & {"GEOMFN", "MFNAME", "SKPAR", "NSIMSH", "SENERG"})
