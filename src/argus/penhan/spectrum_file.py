"""Espectro de energía de la fuente desde un fichero: el formato de ARGUS, tablas y salidas de penhmain.

Lo que se lee se convierte en las filas SPECTR (espectro continuo) o SLINES
(líneas) de penhmain: como mucho 250 (MISPEC, MLINE). Si hay más, se agrupan
intervalos vecinos conservando la probabilidad de cada zona.

**Formato de ARGUS** (texto; las líneas que empiezan por «#» son comentarios y
las de la forma «# clave: valor» dicen cómo leer los números)::

    # Espectro de la fuente (ARGUS)
    # unidad: MeV          eV, keV, MeV o GeV
    # tipo: continuo       continuo (SPECTR) o líneas (SLINES)
    # energía: borde       borde inferior de cada intervalo (como SPECTR), centro, o bordes (tres columnas)
    # valor: probabilidad  probabilidad de cada intervalo, densidad (por unidad de energía) o cuentas
    90      0.10
    95      0.25
    100     -1             en un espectro continuo, la última fila es la energía máxima (su valor no se usa)

**También se leen**, sin esa cabecera:

* tablas de dos columnas (energía y valor) o de tres con los dos bordes de cada
  intervalo y su valor («Emin Emax P»), de una hoja de cálculo o de texto
  (tabuladores, «;», comas o blancos; coma o punto decimal). La unidad se busca
  en los títulos de las columnas («E (keV)», «Energy [MeV]»…); si el de la
  segunda es por unidad de energía («/keV», «keV^-1», «por MeV», «densidad»),
  se toma como densidad, en los centros de los intervalos;
* líneas SPECTR o SLINES de otro .in (en eV);
* salidas de penhmain con un espectro de energía (``spc-impdet``,
  ``spc-angdet``, ``energy-up``/``energy-down``, ``psource``, ``usource``…), de
  la partícula que se elija: así el espectro que llega a un detector en una
  simulación puede ser la fuente de otra.
"""

from __future__ import annotations

import codecs
import heapq
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.numfmt import plural
from .sections import MAX_SPECTRUM

UNITS = {"eV": 1.0, "keV": 1e3, "MeV": 1e6, "GeV": 1e9}
KIND_NAMES = {"continuous": "continuo (SPECTR)", "lines": "líneas (SLINES)"}
POSITION_NAMES = {"edge": "borde inferior de cada intervalo", "center": "centro de cada intervalo",
                  "edges": "los dos bordes (columnas 1 y 2)"}
VALUE_NAMES = {"probability": "probabilidad de cada intervalo", "density": "densidad (por unidad de energía)",
               "counts": "cuentas o pesos de cada intervalo"}
PARTICLE_NAMES = {1: "electrones", 2: "fotones", 3: "positrones", 4: "protones", 5: "alfas", 6: "neutrones"}
EDGE_TOL = 1e-6  # bordes que coinciden (relativo a la energía más alta)


def decode_text(raw: bytes) -> str:
    """Texto de un fichero hecho en Windows: UTF-8 (con o sin BOM), UTF-16 («Texto Unicode» de Excel) o cp1252."""
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


# ---------------------------------------------------------------------------------------------------------------------
# Lo leído
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class SpectrumData:
    """Un fichero de espectro leído, antes de pasarlo a filas SPECTR o SLINES."""

    name: str
    what: str  # qué es: «Espectro de ARGUS», «Tabla de 2 columnas», «Salida de penhmain: …»
    columns: list[np.ndarray] = field(default_factory=list)  # tabla: columnas numéricas (NaN: celda vacía)
    unit: str | None = None  # lo que dice el fichero (None: no lo dice)
    kind: str | None = None
    position: str | None = None
    value: str | None = None
    edges: np.ndarray | None = None  # salida de penhmain: bordes de los intervalos (eV)
    series: list[tuple[str, int | None, np.ndarray]] = field(default_factory=list)  # (nombre, partícula, por eV)
    value_unit: str = ""  # unidad de la salida de penhmain («1/(eV·partícula)»)
    notes: list[str] = field(default_factory=list)
    # Como SPECTR: la fila de la energía más alta es la energía máxima, tenga el valor que tenga (líneas de un .in,
    # el formato de ARGUS o «# energía: borde»). En otras tablas, si tiene valor, su intervalo mide como el anterior.
    last_is_max: bool = False

    @property
    def from_penhmain(self) -> bool:
        return self.edges is not None

    @property
    def ncols(self) -> int:
        return len(self.columns)

    @property
    def nrows(self) -> int:
        return len(self.columns[0]) if self.columns else (len(self.edges) - 1 if self.edges is not None else 0)


@dataclass
class ImportOptions:
    unit: str = "MeV"
    kind: str = "continuous"
    position: str = "edge"
    value: str = "probability"
    series: int = 0
    max_rows: int = MAX_SPECTRUM


@dataclass
class Converted:
    rows: list[tuple[float, float]]  # (energía en eV, probabilidad): las líneas SPECTR o SLINES
    lines: bool
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    particle: int | None = None  # la de la serie elegida de una salida de penhmain


# ---------------------------------------------------------------------------------------------------------------------
# Leer
# ---------------------------------------------------------------------------------------------------------------------

def read_spectrum_file(path: str | Path) -> SpectrumData:
    """Lee un fichero de espectro: salida de penhmain (por su contenido) o tabla de texto (``ValueError`` si no)."""
    p = Path(path)
    data = _from_penhmain(p)
    if data is not None:
        return data
    raw = p.read_bytes()
    if b"\x00" in raw[:4096] and not raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        raise ValueError(f"{p.name} no es un fichero de texto (¿una hoja de Excel? Guárdela como CSV o texto)")
    return parse_table_text(decode_text(raw), p.name)


def _from_penhmain(p: Path) -> SpectrumData | None:
    """La salida de penhmain (reconocida por su contenido), o None si no lo es (se lee como tabla)."""
    from ..results.catalog import KINDS, identify
    from ..results.data import Curves
    from ..results.readers import IncompleteFile, read_output

    ident = identify(p)
    if ident is None:
        return None
    try:
        c = read_output(p, ident)
    except IncompleteFile as exc:
        raise ValueError(f"penhmain todavía está escribiendo {p.name}: vuelva a intentarlo cuando termine") from exc
    except (OSError, ValueError) as exc:  # FormatError es un ValueError
        raise ValueError(f"No se puede leer {p.name}: {exc}") from exc
    if isinstance(c, Curves) and c.kind == "psource" and c.discrete and c.series:
        # La fuente de líneas (SLINES) que simuló penhmain: la probabilidad de cada línea.
        y = np.asarray(c.series[0].y, dtype=np.float64)
        return SpectrumData(p.name, "Salida de penhmain: espectro de la fuente (líneas)",
                            [np.asarray(c.x, dtype=np.float64), y], unit="eV", kind="lines", position="edge",
                            value="probability")
    if not isinstance(c, Curves) or c.x_unit != "eV" or c.edges is None or not c.series \
            or len(c.edges) != len(c.x) + 1:
        raise ValueError(f"{p.name} es un resultado de penhmain, pero no un espectro de energía")
    kind = KINDS.get(c.kind)
    title = kind.title if kind is not None else c.title
    series = [(s.name, s.particle, np.asarray(s.y, dtype=np.float64)) for s in c.series
              if s.unit is None or s.unit == c.y_unit]
    data = SpectrumData(p.name, f"Salida de penhmain: {title.lower() if title else c.kind}", edges=np.asarray(c.edges,
                        dtype=np.float64), series=series, value_unit=c.y_unit, kind="continuous", value="density",
                        unit="eV")
    if c.kind == "fln-impdet":
        data.notes.append("Es la fluencia por energía (recorrido de las partículas), no el número de partículas que "
                          "entran: como espectro de una fuente, no es lo mismo.")
    elif c.kind == "spc-enddet":
        data.notes.append("Es el espectro de energía depositada en el detector, no el de las partículas.")
    return data


_NUM = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
_THOUSANDS_DOT = re.compile(r"^[-+]?\d{1,3}(?:\.\d{3})+$")
_THOUSANDS_COMMA = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})+$")
_UNIT = re.compile(r"(?<![A-Za-z/])(gev|mev|kev|ev)(?![A-Za-z])", re.IGNORECASE)
_PER_UNIT = re.compile(r"(?:/\s*\(?|per\s+|por\s+|1/\(?)(gev|mev|kev|ev)(?![A-Za-z])|(gev|mev|kev|ev)\s*(?:\^?-1|⁻¹)",
                       re.IGNORECASE)
_UNIT_NAMES = {"ev": "eV", "kev": "keV", "mev": "MeV", "gev": "GeV"}


def _plain(text: str) -> str:
    """Sin tildes y en minúsculas (para las claves de la cabecera)."""
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch)).lower()


def _tokens(line: str, tabular: bool, csv: bool = False) -> list[str]:
    if ";" in line or "\t" in line:
        return [t.strip() for t in re.split(r"[;\t]", line)]
    if csv:  # CSV con la coma como separador (en todo el fichero: «20,1» son dos números)
        return [t.strip() for t in line.split(",")]
    if not tabular and "," in line and " " not in line.strip() and re.search(r"[.eEdD]", line):
        return [t.strip() for t in line.split(",")]  # «1e6,0.5,0.01»: la coma separa
    return [t for t in re.split(r"\s+", line.replace(", ", " ").strip()) if t]


def comma_separated(lines: list[str]) -> bool:
    """¿Un CSV con la coma como separador? Sin «;» ni tabuladores, todas las filas de números tienen comas, ningún
    blanco por dentro y el mismo número de comas (con coma decimal harían falta otros separadores)."""
    data = [ln.strip() for ln in lines if ln.strip() and re.match(r"^\s*[-+]?[\d.]", ln)]
    if not data or any(";" in ln or "\t" in ln for ln in lines):
        return False
    counts = {ln.count(",") for ln in data}
    return len(counts) == 1 and counts != {0} and not any(re.search(r"\S\s+\S", ln) for ln in data)


def _column_titles(line: str, tabular: bool, csv: bool) -> list[str]:
    """Los títulos de las columnas de una línea de cabecera («E (keV)  P», «Energy [MeV];Fluence [1/MeV]»…)."""
    t = line.strip().lstrip("#!%").strip()
    if not t:
        return []
    if tabular:
        parts = re.split(r"[;\t]", t)
    elif csv or ("," in t and not re.search(r"\d,\d", t)):
        parts = t.split(",")
    else:
        parts = re.split(r"\s{2,}", t)
        if len(parts) < 2:
            parts = t.split()
    return [x.strip() for x in parts if x.strip()]


def _energy_unit(title: str) -> str | None:
    """La unidad de energía en el título de la columna de la energía: «E (keV)», «E/MeV», «E_kin/MeV»,
    «Energie/keV» (lo que va tras «/» en este título es su unidad, no una densidad)."""
    t = re.sub(r"(?i)/\s*\(?\s*(gev|mev|kev|ev)\b", r" [\1]", title)
    u = _UNIT.search(t)
    return _UNIT_NAMES[u.group(1).lower()] if u else None


def _directive(line: str, info: dict) -> bool:
    """Una línea «# clave: valor» de la cabecera de ARGUS (o de otro programa): la anota en ``info``."""
    m = re.match(r"^\s*[#!%]+\s*([^:=]{1,40}?)\s*[:=]\s*(.+?)\s*$", line)
    if not m:
        return False
    key, val = _plain(m.group(1)).strip(), _plain(m.group(2))
    # Cuenta la primera palabra del valor: detrás puede venir una explicación («continuo  (o líneas)»).
    first = (re.findall(r"[a-z0-9/]+", val) or [""])[0]
    if key in ("unidad", "unidades", "unit", "units", "unidad de energia", "energy unit"):
        u = _UNIT.search(val)
        if u:
            info["unit"] = _UNIT_NAMES[u.group(1).lower()]
            return True
    if key in ("tipo", "type", "kind", "espectro", "spectrum"):
        if re.match(r"line|linea|slines|discret", first):
            info["kind"] = "lines"
            return True
        if re.match(r"contin|spectr|histog|interval", first):
            info["kind"] = "continuous"
            return True
    if key in ("energia", "energy", "posicion", "position", "columna 1", "column 1", "e"):
        if re.match(r"bordes|edges|emin", first):
            info["position"] = "edges"
        elif re.match(r"centr|center|centre|medio|mid", first):
            info["position"] = "center"
        elif re.match(r"borde|inferior|lower|edge|low", first):
            info["position"] = "edge"
        return "position" in info
    if key in ("valor", "valores", "value", "values", "columna 2", "column 2", "p", "y"):
        if re.match(r"densi|/", first) or val.startswith(("por unidad", "per unit")):
            info["value"] = "density"
        elif re.match(r"cuenta|count|peso|weight", first):
            info["value"] = "counts"
        elif re.match(r"prob", first):
            info["value"] = "probability"
        return "value" in info
    return False


def parse_table_text(text: str, name: str = "") -> SpectrumData:
    """Una tabla de texto: el formato de ARGUS, dos o tres columnas, o líneas SPECTR/SLINES de un .in."""
    raws = text.lstrip("\ufeff").replace("\r", "\n").split("\n")
    tabular = any(";" in r or "\t" in r for r in raws)
    csv = comma_separated([r for r in raws if not r.lstrip().startswith(("#", "!", "%"))])
    info: dict = {}
    header: list[str] = []
    rows: list[list[str]] = []
    keyword_rows = False
    for raw in raws:
        line = raw.strip(" \n\ufeff")
        if not line.strip():
            continue
        if line.lstrip().startswith(("#", "!", "%")):
            if not _directive(line, info) and not rows:
                header.append(line)
            continue
        # Como penhmain: la palabra clave en las columnas 1 a 6 y los datos desde la 8 (A6,1X,A120). Con blancos
        # delante es un comentario.
        head = raw.lstrip("\ufeff")[:6]
        if head in ("SPECTR", "SLINES"):
            kind = "lines" if head == "SLINES" else "continuous"
            if not keyword_rows:
                rows = []  # en un .in solo cuentan sus líneas de espectro
                keyword_rows = True
                info["kind"] = kind
            elif kind != info["kind"]:  # penhmain no admite las dos
                info["mixed"] = True
                continue
            body = raw.lstrip("\ufeff")[7:127].split("[", 1)[0]
            rows.append([t for t in re.split(r"[\s,]+", body.strip()) if t][:2])
            continue
        if keyword_rows:
            continue  # el resto del .in
        toks = _tokens(line.split("#", 1)[0], tabular, csv)
        if toks and _looks_numeric(toks[0]):
            rows.append(toks)
        elif not rows:
            header.append(line)
    if keyword_rows:
        info.update(unit="eV", position="edge", value="probability")
    # ¿Coma o punto decimal?
    dec_comma = dec_point = 0
    for toks in rows:
        for t in toks[:3]:
            if re.fullmatch(r"[-+]?\d*,\d+(?:[eEdD][-+]?\d+)?", t) and not _THOUSANDS_COMMA.match(t):
                dec_comma += 1
            elif re.fullmatch(r"[-+]?\d*\.\d+(?:[eEdD][-+]?\d+)?", t) and not _THOUSANDS_DOT.match(t):
                dec_point += 1
    spanish = dec_comma > 0 and dec_point == 0
    english = dec_point > 0 and dec_comma == 0

    def number(tok: str) -> float:
        t = tok.strip().strip('"').strip("'").replace("D", "E").replace("d", "e")
        if not t:
            return math.nan
        if "," in t and "." in t:
            t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
        elif "," in t:
            t = t.replace(",", "") if english and _THOUSANDS_COMMA.match(t) else t.replace(",", ".")
        elif spanish and _THOUSANDS_DOT.match(t):
            t = t.replace(".", "")
        if not _NUM.match(t):
            return math.nan
        v = float(t)
        return v if math.isfinite(v) else math.nan

    values = [[number(t) for t in toks] for toks in rows]
    unread = sum(1 for v in values if not v or not math.isfinite(v[0]))
    values = [v for v in values if v and math.isfinite(v[0])]
    if not values:
        raise ValueError(f"{name or 'El texto'} no tiene filas que empiecen por un número (energía y valor)")
    notes = [f"{plural(unread, 'fila')} cuya energía no se puede leer: no se {'usa' if unread == 1 else 'usan'}."] \
        if unread else []
    if info.get("mixed"):
        first = "SLINES" if info["kind"] == "lines" else "SPECTR"
        other = "SPECTR" if first == "SLINES" else "SLINES"
        notes.append(f"Tiene líneas {first} y {other}: penhmain no las admite juntas (las {other} de después no "
                     f"las leería y se detendría con un error). Se usan las {first}, las primeras.")
    ncols = max(len(v) for v in values)
    # Columnas que de verdad tienen números (una tercera columna de comentarios no cuenta).
    cols = [np.array([v[i] if i < len(v) else math.nan for v in values], dtype=np.float64) for i in range(ncols)]
    while len(cols) > 1 and not np.isfinite(cols[-1]).any():
        cols.pop()
    # Lo que dicen los títulos: primero la última línea de la cabecera (los títulos de las columnas); la unidad, del
    # título de la columna de la energía; densidad, solo si lo dice el título de la columna del valor.
    titles = _column_titles(header[-1], tabular, csv) if header else []
    if titles and "unit" not in info:
        u = _energy_unit(titles[0])
        if u:
            info["unit"] = u
    value_titles = titles[1:]
    if len(value_titles) >= 2 and _energy_unit(value_titles[0]) and not _PER_UNIT.search(value_titles[0]):
        value_titles = value_titles[1:]  # «Emin Emax valor»: la segunda columna es otra energía
    vtitle = value_titles[0] if value_titles else ""
    if "value" not in info and vtitle and (_PER_UNIT.search(vtitle) or re.search(r"densi|dn/de|d\s*n\s*/\s*d\s*e",
                                                                                  _plain(vtitle))):
        info["value"] = "density"
        if "unit" not in info:
            m = _PER_UNIT.search(vtitle)
            if m:
                info["unit"] = _UNIT_NAMES[(m.group(1) or m.group(2)).lower()]
    free = " ".join(header)
    if "unit" not in info:  # en las demás líneas de la cabecera, de la más cercana a los números hacia arriba
        for h in reversed(header):
            u = _energy_unit(h)
            if u:
                info["unit"] = u
                break
    if "value" not in info and not titles and (_PER_UNIT.search(free) or
                                               re.search(r"densi|dn/de|d\s*n\s*/\s*d\s*e", _plain(free))):
        info["value"] = "density"
    if "kind" not in info and re.search(r"slines|lineas|discret", _plain(free)):
        info["kind"] = "lines"
    if "position" not in info and len(cols) >= 3 and re.search(
            r"e_?min\b.*e_?max\b|\bmin\b.*\bmax\b|\blow(er)?\b.*\b(high|upper)\b|inferior.*superior|desde.*hasta",
            _plain(free)):
        info["position"] = "edges"
    if "position" not in info and re.search(r"centr|center|centre|midpoint", _plain(free)):
        info["position"] = "center"
    argus = any("argus" in h.lower() for h in header)
    if argus:
        what = "Espectro de ARGUS"
    elif keyword_rows:
        what = "Líneas SPECTR/SLINES de un .in"
    else:
        what = f"Tabla de {len(cols)} columna{'s' if len(cols) > 1 else ''}"
    data = SpectrumData(name, what, cols, info.get("unit"), info.get("kind"), info.get("position"),
                        info.get("value"), notes=notes)
    data.last_is_max = keyword_rows or argus or info.get("position") == "edge"
    return data


def _looks_numeric(tok: str) -> bool:
    t = tok.strip().replace(",", ".").replace("D", "E").replace("d", "e")
    return bool(re.match(r"^[-+]?(?:\d|\.\d)", t))


# ---------------------------------------------------------------------------------------------------------------------
# Opciones por defecto
# ---------------------------------------------------------------------------------------------------------------------

def default_options(data: SpectrumData, particle: int | None = None) -> ImportOptions:
    """Lo que dice el fichero y, si no lo dice, lo más probable (se puede cambiar en la vista previa)."""
    opt = ImportOptions()
    if data.from_penhmain:
        opt.unit, opt.kind, opt.position, opt.value = "eV", "continuous", "edges", "density"
        opt.series = default_series(data, particle)
        return opt
    e = data.columns[0]
    emax = float(np.nanmax(np.abs(e))) if np.isfinite(e).any() else 1.0
    if data.unit:
        opt.unit = data.unit
    elif emax >= 1e4:
        opt.unit = "eV"
    elif particle in (1, 2, 3):
        opt.unit = "MeV" if emax <= 20.0 else "keV"
    else:
        opt.unit = "MeV" if emax <= 1000.0 else "keV"
    opt.kind = data.kind or "continuous"
    opt.value = data.value or "probability"
    if data.position:
        opt.position = data.position
    elif data.ncols >= 3 and _looks_like_edges(data.columns[0], data.columns[1]):
        opt.position = "edges"
    else:
        last = data.columns[1][-1] if data.ncols >= 2 else math.nan
        signature = not math.isfinite(last) or last < 0  # la última fila de SPECTR
        opt.position = "edge" if signature or opt.value != "density" else "center"
    if opt.position == "edges" and data.ncols < 3:
        opt.position = "edge"
    return opt


def default_series(data: SpectrumData, particle: int | None) -> int:
    """La serie de la partícula de la fuente si tiene algo; si no, la total o la que más tiene."""
    if not data.series:
        return 0
    w = np.diff(data.edges)
    tot = [float(np.nansum(np.clip(y, 0, None) * w)) for _n, _p, y in data.series]
    for i, (_n, p, _y) in enumerate(data.series):
        if particle is not None and p == particle and tot[i] > 0:
            return i
    for want in ("total", "definido", "muestreado"):  # en psource.dat, el definido (el muestreado tiene ruido)
        for i, (n, p, _y) in enumerate(data.series):
            if p is None and tot[i] > 0 and n == want:
                return i
    return int(np.argmax(tot))


def _looks_like_edges(a: np.ndarray, b: np.ndarray) -> bool:
    """¿Columnas «Emin Emax»? La segunda es siempre mayor que la primera y los intervalos no se solapan (uno
    empieza donde acaba el anterior, o más arriba)."""
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 1 or not np.all(b[ok] > a[ok]):
        return False
    if ok.sum() == 1:
        return True
    aa, bb = a[ok], b[ok]
    order = np.argsort(aa, kind="stable")
    aa, bb = aa[order], bb[order]
    scale = max(float(np.max(np.abs(bb))), 1e-30)
    return bool(np.all(aa[1:] >= bb[:-1] - EDGE_TOL * scale))


# ---------------------------------------------------------------------------------------------------------------------
# Convertir en filas SPECTR o SLINES
# ---------------------------------------------------------------------------------------------------------------------

def convert(data: SpectrumData, opt: ImportOptions) -> Converted:
    """Las filas SPECTR (continuo) o SLINES (líneas) del espectro, con lo que se ha hecho para llegar a ellas."""
    notes = list(data.notes)
    try:
        if data.from_penhmain:
            if not data.series:
                raise ValueError("La salida no tiene ninguna serie")
            i = min(max(int(opt.series), 0), len(data.series) - 1)
            name, particle, y = data.series[i]
            # penhmain divide entre la anchura de cada intervalo, o entre 1e-15 si es menor (la energía exacta de un
            # SPECTR con dos energías iguales, en psource.dat: FACT = 1e15).
            w = np.diff(data.edges)
            p = np.where(np.isfinite(y), y, 0.0) * np.where(w > 1e-15, w, 1e-15)
            rows = _continuous_rows(data.edges[:-1].copy(), data.edges[1:].copy(), p, opt.max_rows, notes)
            return Converted(rows, False, notes, particle=particle)
        f = UNITS[opt.unit]
        cols = data.columns
        if opt.kind == "lines":
            if data.ncols < 2:
                raise ValueError("Hacen falta dos columnas: la energía de cada línea y su probabilidad")
            if opt.value == "density":
                notes.append("En un espectro de líneas, la segunda columna es la probabilidad de cada línea (no "
                             "una densidad).")
            return Converted(_line_rows(cols[0] * f, cols[1], opt.max_rows, notes), True, notes)
        if opt.position == "edges":
            if data.ncols < 3:
                raise ValueError("Con los dos bordes de cada intervalo hacen falta tres columnas: Emin, Emax y valor")
            lo, hi, v = cols[0] * f, cols[1] * f, cols[2]
            lo, hi, v = _with_gaps(lo, hi, v, notes)
            p = _bin_values(v, hi - lo, opt.value, notes)
        elif opt.position == "center":
            if data.ncols < 2:
                raise ValueError("Hacen falta dos columnas: energía y valor")
            e, v = _sorted_pairs(cols[0] * f, cols[1], notes)
            if len(e) < 2:
                raise ValueError("Con los centros de los intervalos hacen falta al menos dos filas")
            edges = edges_from_centers(e)
            lo, hi = edges[:-1], edges[1:]
            if lo[0] < 0:
                lo[0] = 0.0
                notes.append("El primer intervalo empezaría en una energía negativa: empieza en 0.")
            p = _bin_values(v, hi - lo, opt.value, notes)
        else:  # borde inferior, como SPECTR
            if data.ncols < 2:
                raise ValueError("Hacen falta dos columnas: energía y valor (la de la última fila puede faltar)")
            lo, hi, v = _lower_edges(cols[0] * f, cols[1], notes, data.last_is_max)
            p = _bin_values(v, hi - lo, opt.value, notes)
        rows = _continuous_rows(lo, hi, p, opt.max_rows, notes)
        return Converted(rows, False, notes)
    except ValueError as exc:
        return Converted([], opt.kind == "lines" and not data.from_penhmain, notes, error=str(exc))


def edges_from_centers(c: np.ndarray) -> np.ndarray:
    """Bordes de unos intervalos a partir de sus centros: uniformes, logarítmicos (como penhmain) o, si no son
    ninguna de las dos cosas, a mitad de camino entre centros."""
    from ..results.formats import centers_to_edges

    c = np.asarray(c, dtype=np.float64)
    if len(c) < 3:
        return centers_to_edges(c)[0]
    d = np.diff(c)
    uniform = float(np.max(np.abs(d - d.mean())) / (abs(d.mean()) or 1.0)) < 1e-3
    log = False
    if np.all(c > 0):
        r = c[1:] / c[:-1]
        log = float(np.max(np.abs(r - r.mean())) / r.mean()) < 1e-3 and r.mean() > 1.0 + 1e-9
    if uniform or log:
        return centers_to_edges(c, log=log and not uniform)[0]
    mid = 0.5 * (c[1:] + c[:-1])
    return np.concatenate([[c[0] - (mid[0] - c[0])], mid, [c[-1] + (c[-1] - mid[-1])]])


def _sorted_pairs(e: np.ndarray, v: np.ndarray, notes: list[str]) -> tuple[np.ndarray, np.ndarray]:
    ok = np.isfinite(e)
    e, v = e[ok], v[ok]
    if np.any(np.diff(e) < 0):
        notes.append("Las energías no estaban en orden: se han ordenado.")
    order = np.argsort(e, kind="stable")
    e, v = e[order], v[order]
    if np.any(np.diff(e) == 0):
        raise ValueError("Hay energías repetidas: con los centros de los intervalos cada energía va una vez")
    return e, v


def _lower_edges(e: np.ndarray, v: np.ndarray, notes: list[str], last_is_max: bool = False
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bordes inferiores (como SPECTR): la fila de la energía más alta (la última de las iguales, como ordena
    penhmain) es el final del espectro si su valor falta o es negativo, o siempre con ``last_is_max`` (lo que hace
    penhmain); si no, su intervalo mide lo mismo que el anterior."""
    ok = np.isfinite(e)
    e, v = e[ok], v[ok]
    if len(e) == 0:
        raise ValueError("No hay energías")
    if np.any(np.diff(e) < 0):
        notes.append("Las energías no estaban en orden: se han ordenado (penhmain también lo hace).")
    order = np.argsort(e, kind="stable")
    e, v = e[order], v[order]
    if last_is_max or not math.isfinite(v[-1]) or v[-1] < 0:
        hi_end = e[-1]
        if math.isfinite(v[-1]) and v[-1] >= 0 and len(e) > 1:
            notes.append(f"La última fila ({_fmt_e(hi_end)}) es la energía máxima del espectro, como en SPECTR: su "
                         f"valor ({v[-1]:g}) no cuenta (penhmain tampoco lo usa).")
        e, v = e[:-1], v[:-1]
        if len(e) == 0:
            raise ValueError("Solo hay la energía máxima: hace falta al menos un intervalo")
    else:
        if len(e) < 2:
            raise ValueError("Con una sola energía y su valor no hay intervalo: use una energía única o una línea")
        width = e[-1] - e[-2]
        if width <= 0:
            raise ValueError("Las dos energías más altas son iguales: falta la energía máxima del espectro")
        hi_end = e[-1] + width
        notes.append(f"La última fila tenía valor: se ha supuesto que su intervalo mide lo mismo que el anterior "
                     f"(el espectro acaba en {_fmt_e(hi_end)}). En SPECTR la última fila es la energía máxima.")
    hi = np.append(e[1:], hi_end)
    return e, hi, v


def _with_gaps(lo: np.ndarray, hi: np.ndarray, v: np.ndarray, notes: list[str]
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Intervalos «Emin Emax valor» en orden y sin huecos (un hueco es un intervalo de probabilidad 0)."""
    ok = np.isfinite(lo) & np.isfinite(hi)
    lo, hi, v = lo[ok], hi[ok], v[ok]
    if len(lo) == 0:
        raise ValueError("No hay intervalos")
    bad = np.nonzero(hi <= lo)[0]
    if len(bad):
        raise ValueError(f"En la fila {int(bad[0]) + 1} el segundo borde no es mayor que el primero")
    order = np.argsort(lo, kind="stable")
    lo, hi, v = lo[order], hi[order], v[order]
    scale = max(float(np.max(np.abs(hi))), 1e-30)
    tol = EDGE_TOL * scale  # la misma tolerancia con que se reconocen las columnas «Emin Emax»
    out_lo, out_hi, out_v = [lo[0]], [hi[0]], [v[0]]
    gaps = 0
    for a, b, x in zip(lo[1:], hi[1:], v[1:]):
        if a < out_hi[-1] - tol:
            raise ValueError(f"Los intervalos se solapan (uno empieza en {_fmt_e(a)} antes de que acabe el anterior, "
                             f"en {_fmt_e(out_hi[-1])})")
        if a > out_hi[-1] + tol:
            out_lo.append(out_hi[-1])
            out_hi.append(a)
            out_v.append(0.0)
            gaps += 1
        out_lo.append(out_hi[-1] if abs(a - out_hi[-1]) <= tol else a)
        out_hi.append(b)
        out_v.append(x)
    if gaps:
        notes.append(f"Había {plural(gaps, 'hueco')} entre intervalos: {'va' if gaps == 1 else 'van'} con "
                     "probabilidad 0.")
    return np.array(out_lo), np.array(out_hi), np.array(out_v, dtype=np.float64)


def _bin_values(v: np.ndarray, width: np.ndarray, what: str, notes: list[str]) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64).copy()
    missing = ~np.isfinite(v)
    if missing.any():
        notes.append(f"{plural(int(missing.sum()), 'intervalo')} sin valor: "
                     f"{'va' if int(missing.sum()) == 1 else 'van'} con probabilidad 0.")
        v[missing] = 0.0
    if np.any(v < 0):
        notes.append(f"{plural(int(np.sum(v < 0)), 'valor negativo', 'valores negativos')}: penhmain "
                     f"{'lo' if int(np.sum(v < 0)) == 1 else 'los'} toma como 0.")
        v = np.clip(v, 0.0, None)
    return v * width if what == "density" else v


def _continuous_rows(lo: np.ndarray, hi: np.ndarray, p: np.ndarray, max_rows: int,
                     notes: list[str]) -> list[tuple[float, float]]:
    p = np.clip(np.where(np.isfinite(p), p, 0.0), 0.0, None)
    if not np.any(p > 0):
        raise ValueError("Todas las probabilidades son cero")
    nz = np.nonzero(p > 0)[0]
    first, last = int(nz[0]), int(nz[-1])
    if first > 0 or last < len(p) - 1:
        parts = []
        if first:
            parts.append(f"{first} al principio")
        if last < len(p) - 1:
            parts.append(f"{len(p) - 1 - last} al final")
        notes.append("Se han quitado los intervalos vacíos de los extremos (" + " y ".join(parts) + ").")
    lo, hi, p = lo[first:last + 1], hi[first:last + 1], p[first:last + 1]
    n0 = len(p)
    lo, hi, p = regroup_bins(lo, hi, p, max(max_rows - 1, 1))
    if len(p) < n0:
        notes.append(f"Había {n0} intervalos y penhmain admite {max_rows} filas: se han juntado vecinos de poca "
                     f"probabilidad hasta quedar {len(p)}, conservando la probabilidad de cada zona.")
    total = float(p.sum())
    rows = [(float(a), float(x / total)) for a, x in zip(lo, p)]
    rows.append((float(hi[-1]), -1.0))
    return rows


def _line_rows(e: np.ndarray, v: np.ndarray, max_rows: int, notes: list[str]) -> list[tuple[float, float]]:
    ok = np.isfinite(e)
    e, v = e[ok], np.asarray(v, dtype=np.float64)[ok]
    missing = ~np.isfinite(v)
    if missing.any():
        notes.append(f"{plural(int(missing.sum()), 'línea')} sin probabilidad: no se "
                     f"{'usa' if int(missing.sum()) == 1 else 'usan'}.")
        e, v = e[~missing], v[~missing]
    if np.any(v < 0):
        notes.append(f"{plural(int(np.sum(v < 0)), 'probabilidad negativa', 'probabilidades negativas')}: penhmain "
                     f"{'la' if int(np.sum(v < 0)) == 1 else 'las'} toma como 0.")
        v = np.clip(v, 0.0, None)
    if len(e) == 0 or not np.any(v > 0):
        raise ValueError("No hay ninguna línea con probabilidad")
    keep = v > 0
    if not keep.all():
        notes.append(f"{plural(int((~keep).sum()), 'línea')} con probabilidad 0: se "
                     f"{'quita' if int((~keep).sum()) == 1 else 'quitan'}.")
        e, v = e[keep], v[keep]
    order = np.argsort(e, kind="stable")
    e, v = e[order], v[order]
    # Energías repetidas: una sola línea con la suma.
    ue, inv = np.unique(e, return_inverse=True)
    if len(ue) < len(e):
        notes.append("Había energías repetidas: van juntas, con la suma de sus probabilidades.")
        e, v = ue, np.bincount(inv, weights=v)
    n0 = len(e)
    e, v = merge_lines(e, v, max_rows)
    if len(e) < n0:
        notes.append(f"Había {n0} líneas y penhmain admite {max_rows}: se han juntado las más próximas (en la energía "
                     "media de las dos, con la suma de sus probabilidades).")
    total = float(v.sum())
    return [(float(a), float(x / total)) for a, x in zip(e, v)]


# ---------------------------------------------------------------------------------------------------------------------
# Agrupar
# ---------------------------------------------------------------------------------------------------------------------

def regroup_bins(lo: np.ndarray, hi: np.ndarray, p: np.ndarray, max_bins: int
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Junta intervalos vecinos, primero los de menos probabilidad, hasta que quedan ``max_bins``.

    La probabilidad de cada zona se conserva; los picos conservan su detalle y
    se ensanchan los intervalos donde casi no hay nada.
    """
    lo, hi, p = (np.asarray(a, dtype=np.float64).copy() for a in (lo, hi, p))
    n = len(p)
    if n <= max_bins:
        return lo, hi, p
    left = list(range(-1, n - 1))
    right = list(range(1, n + 1))
    right[-1] = -1
    alive = [True] * n
    ver = [0] * n
    gl, gh, gp = lo.tolist(), hi.tolist(), p.tolist()
    heap = [(gp[i] + gp[i + 1], i, i + 1, 0, 0) for i in range(n - 1)]
    heapq.heapify(heap)
    count = n
    while count > max_bins and heap:
        _s, i, j, vi, vj = heapq.heappop(heap)
        if not (alive[i] and alive[j] and ver[i] == vi and ver[j] == vj and right[i] == j):
            continue
        gh[i] = gh[j]
        gp[i] += gp[j]
        alive[j] = False
        ver[i] += 1
        r = right[j]
        right[i] = r
        if r != -1:
            left[r] = i
        count -= 1
        li = left[i]
        if li != -1:
            heapq.heappush(heap, (gp[li] + gp[i], li, i, ver[li], ver[i]))
        if r != -1:
            heapq.heappush(heap, (gp[i] + gp[r], i, r, ver[i], ver[r]))
    idx = [k for k in range(n) if alive[k]]
    return np.array([gl[k] for k in idx]), np.array([gh[k] for k in idx]), np.array([gp[k] for k in idx])


def merge_lines(e: np.ndarray, v: np.ndarray, max_rows: int) -> tuple[np.ndarray, np.ndarray]:
    """Junta las líneas más próximas (en energía relativa) hasta que quedan ``max_rows``: la nueva línea está en la
    energía media de las dos (pesada con su probabilidad) y tiene la suma de las probabilidades."""
    e, v = np.asarray(e, dtype=np.float64).copy(), np.asarray(v, dtype=np.float64).copy()
    n = len(e)
    if n <= max_rows:
        return e, v
    ge, gv = e.tolist(), v.tolist()
    left = list(range(-1, n - 1))
    right = list(range(1, n + 1))
    right[-1] = -1
    alive = [True] * n
    ver = [0] * n

    def gap(i: int, j: int) -> float:
        return (ge[j] - ge[i]) / max(abs(ge[j]), 1e-30)

    heap = [(gap(i, i + 1), i, i + 1, 0, 0) for i in range(n - 1)]
    heapq.heapify(heap)
    count = n
    while count > max_rows and heap:
        _g, i, j, vi, vj = heapq.heappop(heap)
        if not (alive[i] and alive[j] and ver[i] == vi and ver[j] == vj and right[i] == j):
            continue
        w = gv[i] + gv[j]
        ge[i] = (gv[i] * ge[i] + gv[j] * ge[j]) / w if w > 0 else 0.5 * (ge[i] + ge[j])
        gv[i] = w
        alive[j] = False
        ver[i] += 1
        r = right[j]
        right[i] = r
        if r != -1:
            left[r] = i
        count -= 1
        li = left[i]
        if li != -1:
            heapq.heappush(heap, (gap(li, i), li, i, ver[li], ver[i]))
        if r != -1:
            heapq.heappush(heap, (gap(i, r), i, r, ver[i], ver[r]))
    idx = [k for k in range(n) if alive[k]]
    return np.array([ge[k] for k in idx]), np.array([gv[k] for k in idx])


# ---------------------------------------------------------------------------------------------------------------------
# Escribir
# ---------------------------------------------------------------------------------------------------------------------

def format_spectrum(rows: list[tuple[float, float]], lines: bool, unit: str = "MeV", title: str = "") -> str:
    """El espectro (filas SPECTR o SLINES, energías en eV) en el formato de ARGUS, con la energía en ``unit``."""
    f = UNITS[unit]
    if not lines and rows:
        # Como penhmain: en orden de energía (las iguales, en su orden) y la última, la energía máxima (P = -1).
        rows = sorted(rows, key=lambda r: r[0])
        rows = rows[:-1] + [(rows[-1][0], -1.0)]
    out = ["# Espectro de la fuente para penhmain (ARGUS)"]
    if title.strip():
        out.append(f"# {' '.join(title.split())}")
    out += [f"# unidad: {unit}",
            f"# tipo: {'líneas' if lines else 'continuo'}",
            "# energía: " + ("la de cada línea" if lines else
                             "borde inferior de cada intervalo (la última fila es la energía máxima)"),
            "# valor: probabilidad",
            f"# E ({unit})\tP"]
    for e, p in rows:
        out.append(f"{_num(e / f)}\t{_num(p)}")
    return "\n".join(out) + "\n"


def _num(v: float) -> str:
    return f"{v:.12g}"


def _fmt_e(e: float) -> str:
    from ..core.numfmt import fmt_energy

    return fmt_energy(e)


__all__ = ["Converted", "ImportOptions", "KIND_NAMES", "POSITION_NAMES", "SpectrumData", "UNITS", "VALUE_NAMES",
           "comma_separated", "convert", "decode_text", "default_options", "default_series", "edges_from_centers",
           "format_spectrum", "merge_lines", "parse_table_text", "read_spectrum_file", "regroup_bins"]
