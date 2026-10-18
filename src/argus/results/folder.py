"""Los resultados de una carpeta: qué ficheros hay, de qué .in son y cómo va la simulación.

Se buscan los ficheros de resultados por su contenido (:mod:`catalog`), el .in
que los produjo (el que tiene el mismo título que ``penhmain-res.dat``, o el
único .in de la carpeta) y las subcarpetas con resultados de otras ejecuciones
(simulaciones en paralelo o variantes de una campaña, con o sin el manifiesto
que escribe ARGUS al prepararlas).

El estado de la simulación se deduce de los ficheros, porque ARGUS no lanza
penhmain:

* ``terminada``: ya tiene las lluvias de NSIMSH (o el tiempo de TIME), o
  penhmain ha escrito «END» en su registro o en la salida en pantalla;
* ``simulando``: hay resultados que se han escrito hace poco (penhmain los
  vuelve a escribir cada DUMPP segundos);
* ``parada``: hay resultados pero hace mucho que no cambian;
* ``error``: la salida en pantalla (run.log) acaba con un error de penhmain;
* ``empezando``: aún no hay resultados, pero penhmain ya ha escrito su
  registro (se está inicializando);
* ``vacia``: nada de lo anterior.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.numfmt import fmt_count
from .catalog import KINDS, Identified, identify
from .data import PARTICLE_NAMES
from .formats import read_head

MANIFEST_NAMES = ("argus-paralelo.json", "argus-campana.json")
STALE_MARGIN = 120.0  # s: margen sobre DUMPP para dar por parada una simulación
PENHMAIN_DUMPP = 2.0e9  # DUMPP de penhmain si el .in no lo dice
NO_DUMPS = 1.0e10  # con DUMPP de 1e10 o más, penhmain solo escribe los resultados al acabar
# Palabras clave que cambian de una ejecución a otra del mismo caso (simulaciones en paralelo).
RUN_KEYWORDS = frozenset({"RSEED", "NSIMSH", "TIME", "DUMPP", "DUMPTO", "RESUME"})
_ID_CACHE: dict[tuple[str, int, int], Identified | None] = {}
_CASE_CACHE: dict[tuple[str, int, int], tuple | None] = {}
_LAST_ID: dict[str, Identified] = {}  # lo último que fue cada fichero reconocido


def kind_of(key: str) -> str:
    """El tipo de resultado de una clave («spc-impdet#2@1~copia.dat» → «spc-impdet»)."""
    return re.split(r"[#@~]", key, maxsplit=1)[0]


def dump_period(dumpp: float | None) -> float | None:
    """Cada cuántos segundos vuelca penhmain sus resultados (None: solo al acabar), como en penhmain.f."""
    d = PENHMAIN_DUMPP if dumpp is None else float(dumpp)
    if d >= NO_DUMPS:
        return None
    return min(max(d, 30.0), 86400.0)


@dataclass
class OutputFile:
    path: Path
    ident: Identified
    size: int
    mtime: float
    copy: bool = False  # hay otro fichero del mismo resultado en la carpeta y este no es el de penhmain
    twin: bool = False  # hay varios ficheros de este resultado (el rótulo lleva el nombre del fichero)

    @property
    def kind(self) -> str:
        return self.ident.kind

    @property
    def family(self) -> str:
        return KINDS[self.kind].family

    @property
    def base_key(self) -> str:
        k = self.kind
        if self.ident.detector is not None:
            k += f"#{self.ident.detector}"
        if self.ident.particle is not None:
            k += f"@{self.ident.particle}"
        return k

    @property
    def key(self) -> str:
        """Identificador del resultado dentro de su carpeta (el mismo en todas las ejecuciones). Una copia (otro
        fichero del mismo resultado, p. ej. «E100_depth-dose.dat» junto a «depth-dose.dat») lleva su nombre."""
        return self.base_key + (f"~{self.path.name}" if self.copy else "")

    @property
    def label(self) -> str:
        text = self._label()
        return f"{text} ({self.path.name})" if self.twin else text

    def _label(self) -> str:
        kind = KINDS[self.kind]
        det, kp = self.ident.detector, self.ident.particle
        if self.kind == "trk":
            return f"Trayectorias de {PARTICLE_NAMES.get(kp, 'partículas (tipo desconocido)')}"
        if self.kind == "polar-angle":
            return f"Ángulo polar de los {PARTICLE_NAMES.get(kp, 'partículas')} que salen"
        if self.kind == "energy-equivalence":
            return "Equivalencias de energía de " + ("protones" if kp == 4 else "alfas")
        if det is not None and self.kind in ("spc-impdet", "fln-impdet", "age-impdet", "spc-angdet", "spc-enddet",
                                             "psf"):
            return f"{kind.title} n.º {det}"
        return kind.title

    @property
    def renamed(self) -> bool:
        default = KINDS[self.kind].default_name
        if "*" in default or "##" in default:
            pat = re.escape(default).replace(r"\#\#", r"\d+").replace(r"\*", ".*")
            return re.fullmatch(pat, self.path.name) is None
        return self.path.name != default

    @property
    def hidden(self) -> bool:
        return KINDS[self.kind].hidden


@dataclass
class InputInfo:
    path: Path
    title: str = ""
    nsimsh: float | None = None
    time_limit: float | None = None
    dumpp: float | None = None
    dumpto: str | None = None
    resume: str | None = None
    kparp: int | None = None
    nuclide: str | None = None
    geomfn: str | None = None
    ltrack: int | None = None
    ok: bool = True


def read_input_info(path: str | os.PathLike) -> InputInfo | None:
    from ..penhan import sections as sc
    from ..penhan.inputfile import parse_in_text

    p = Path(path)
    try:
        doc = parse_in_text(p.read_bytes().decode("latin-1"), str(p))
    except (OSError, ValueError):
        return None
    info = InputInfo(p, title=(doc.model.title or "").rstrip(), ok=doc.ok)

    def first(key):
        v = sc.value(doc, key)
        return float(v[0]) if v else None

    info.nsimsh = first("NSIMSH")
    info.time_limit = first("TIME")
    info.dumpp = first("DUMPP")
    info.dumpto = sc.text(doc, "DUMPTO")
    info.resume = sc.text(doc, "RESUME")
    k = first("SKPAR")
    info.kparp = int(k) if k is not None else 1
    info.nuclide = sc.text(doc, "SRNUCL")
    info.geomfn = sc.text(doc, "GEOMFN")
    lt = first("LTRACK")
    info.ltrack = int(lt) if lt is not None else None
    if sc.psf_files(doc):
        info.kparp = None
    return info


def _title_of_res(path: Path) -> str:
    lines = read_head(path, 2048).splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("date and time"):
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return nxt.strip()
    return ""


def _mark_copies(files: list[OutputFile]) -> None:
    """Si hay varios ficheros del mismo resultado (una copia renombrada de una simulación anterior, «E100_penhmain-res
    .dat», junto al de ahora), el de penhmain es el que tiene su nombre (o, si ninguno lo tiene, el más reciente); los
    demás son copias, con su propia clave."""
    groups: dict[str, list[OutputFile]] = {}
    for f in files:
        f.copy = f.twin = False
        groups.setdefault(f.base_key, []).append(f)
    for group in groups.values():
        if len(group) < 2:
            continue
        named = [f for f in group if not f.renamed]
        main = named[0] if named else max(group, key=lambda f: f.mtime)
        for f in group:
            f.twin = True
            f.copy = f is not main


# -- ¿Son ejecuciones del mismo caso? ------------------------------------------------------------------------------

def _cached(path: Path, fn):
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, int(st.st_mtime_ns))
    if key not in _CASE_CACHE:
        if len(_CASE_CACHE) > 5000:
            _CASE_CACHE.clear()
        _CASE_CACHE[key] = fn(path)
    return _CASE_CACHE[key]


def _file_hash(path: Path) -> str | None:
    import hashlib

    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _log_case(path: Path) -> tuple | None:
    """Lo que penhmain dice en su registro (penhmain.dat) que va a simular, hasta «Job characteristics» y sin la
    fecha, el fichero de geometría y las semillas con que empieza; None si aún no lo ha escrito."""
    i, m, head = -1, None, ""
    for size in (65536, 1 << 20):
        head = read_head(path, size)
        i = head.find(">>>>>>  Job characteristics")
        m = re.search(r"Random-number generator seeds\s*=\s*(-?\d+)\s*,\s*(-?\d+)", head[i:]) if i >= 0 else None
        if m is not None or len(head) < size:
            break
    if i < 0 or m is None:
        return None
    case = "\n".join(ln.rstrip() for ln in head[:i].splitlines() if not ln.strip().lower().startswith("date and time"))
    g = re.search(r"PENGEOM's geometry file:\s*(\S+)", head[:i])
    return case, (int(m.group(1)), int(m.group(2))), g.group(1) if g else None


def _in_case(path: Path) -> tuple | None:
    """Lo que define el caso en un .in: todas sus líneas salvo las de cada ejecución (semillas, lluvias, tiempo y
    volcados), las semillas y el fichero de geometría."""
    from ..penhan.inputfile import parse_in_text

    try:
        doc = parse_in_text(path.read_bytes().decode("latin-1"), str(path))
    except (OSError, ValueError):
        return None
    case, seeds, geo = [], [], None
    for ln in doc.lines:
        if ln.is_comment:
            continue
        kw = ln.kw.strip().upper()
        if kw == "RSEED":
            seeds.append(tuple(ln.values))
        elif kw not in RUN_KEYWORDS:
            case.append((kw, tuple(ln.values), ln.text.strip(), ln.label, ln.kb))
        if kw == "GEOMFN" and geo is None:
            geo = ln.text.strip() or None
    return tuple(case), tuple(seeds), geo


def replica_problem(runs: list[ResultsFolder]) -> str | None:
    """None si las carpetas son ejecuciones del mismo caso con semillas distintas (se pueden combinar); si no, por
    qué no se combinan.

    Lo que penhmain va a simular se mira en su registro (penhmain.dat, hasta «Job characteristics») o, si alguna aún
    no lo tiene, en su .in, sin las palabras clave de cada ejecución (RSEED, NSIMSH, TIME, DUMPP, DUMPTO y RESUME); y
    la geometría, en el fichero de cada carpeta. Como penhmain-sum, que suma los volcados de ejecuciones «que solo se
    diferencian en las semillas», pero comprobándolo.
    """
    if len(runs) < 2:
        return None
    logs = []
    for r in runs:
        f = r.find("log")
        logs.append(_cached(f.path, _log_case) if f is not None else None)
    if all(x is not None for x in logs):
        sigs = logs
    else:
        sigs = [_cached(r.input.path, _in_case) if r.input is not None else None for r in runs]
        if any(x is None for x in sigs):
            return ("no se puede comprobar que sean el mismo caso: a alguna le falta el .in o el registro de penhmain "
                    "(penhmain.dat)")
    geos = []
    for r, s in zip(runs, sigs):
        name = s[2]
        path = r.path / name if name else None
        if path is None or not path.is_file():
            path = r.path / "geometry.rep"
        geos.append(_cached(path, _file_hash) if path.is_file() else None)
    if len({s[0] for s in sigs}) > 1:
        return "no simulan el mismo caso (sus .in no son iguales, aparte de las semillas, las partículas y el tiempo)"
    if len(set(geos)) > 1:
        return "no simulan el mismo caso (sus geometrías no son iguales)"
    seeds = [s[1] for s in sigs]
    if len(set(seeds)) < len(seeds):
        return "varias empiezan con las mismas semillas: son la misma simulación repetida, no independientes"
    return None


@dataclass
class RunState:
    status: str  # 'vacia', 'empezando', 'simulando', 'terminada', 'parada', 'error', 'resultados'
    text: str
    showers: float = 0.0
    target: float | None = None
    progress: float | None = None  # 0..1
    eta: float | None = None  # s
    speed: float | None = None  # lluvias/s (de CPU, la de penhmain)
    updated: float | None = None  # fecha del último resumen (s desde 1970)
    error: str | None = None
    decays: bool = False  # fuente radiactiva (KPARP = 0): lo que se cuenta son desintegraciones


def count_text(n: float, decays: bool = False) -> str:
    """«1 partícula», «300 desintegraciones»… (con una fuente radiactiva, penhmain cuenta desintegraciones)."""
    if decays:
        return f"{fmt_count(n)} {'desintegración' if n == 1 else 'desintegraciones'}"
    return f"{fmt_count(n)} {'partícula' if n == 1 else 'partículas'}"


# Los volcados del resumen que se han visto (de cualquier ResultsFolder de esta sesión): con dos de la misma
# ejecución, la velocidad es la de verdad (lluvias por segundo de reloj entre los dos) y se sabe cuánto ha tardado la
# inicialización. Resumen -> [(fecha de la ejecución, lluvias, tiempo de cálculo, tiempo de CPU)], los dos últimos.
_DUMPS: dict[str, list[tuple[str, float, float, float]]] = {}


def _previous_dump(path: Path, s) -> tuple[str, float, float, float] | None:
    """Guarda este volcado del resumen ``s`` y devuelve el anterior de la misma ejecución (None si no se vio)."""
    key = str(path)
    cur = (s.date, float(s.showers), float(s.time_total), float(s.time_cpu))
    seen = _DUMPS.get(key, [])
    if seen and (seen[-1][0] != cur[0] or cur[1] < seen[-1][1] or cur[2] < seen[-1][2]):
        seen = []  # otra ejecución (o se ha vuelto a empezar)
    if not seen or (cur[1], cur[2]) != (seen[-1][1], seen[-1][2]):
        seen = (seen + [cur])[-2:]
    if len(_DUMPS) > 5000:
        _DUMPS.clear()
    _DUMPS[key] = seen
    prev = seen[-2] if len(seen) >= 2 else None
    return prev if prev is not None and prev[1] < cur[1] and prev[2] < cur[2] else None


def simulation_clock(s, prev: tuple | None, tlimit: float) -> tuple[float, float | None]:
    """(Tiempo que lleva simulando penhmain sin la inicialización, lluvias por segundo de reloj).

    TIME cuenta desde que acaba la inicialización (TSECA = TIME + TSECIN, penhmain.f), pero el «Calculation time» del
    resumen la incluye (PEINIT/PHINIT; minutos con protones y datos nucleares), y penhmain no la escribe. El tiempo
    de CPU de la simulación («Effective (CPU) simulation time») no la incluye, pero es menor que el de reloj si el
    ordenador está ocupado. Con dos volcados de la misma ejecución, el reloj por segundo de CPU entre ellos da las
    dos cosas; con uno solo: el tiempo total mientras no pase de TIME (nada dice que la inicialización haya sido
    larga), y si no, el de CPU; la velocidad, la de penhmain (de CPU).
    """
    total, cpu = float(s.time_total), float(s.time_cpu)
    rate = s.showers / cpu if cpu > 0 else None
    if prev is not None:
        dt, dcpu, dn = total - prev[2], cpu - prev[3], s.showers - prev[1]
        if dt > 0 and dn > 0:
            rate = dn / dt
            if dcpu > 0:
                return min(total, max(cpu, cpu * dt / dcpu)), rate
    return (total if total < tlimit else min(cpu, total)), rate


def _fmt_duration(t: float) -> str:
    """«2 min», «1,5 h», «6,8 días» (con coma decimal, como el resto de la interfaz)."""
    if t >= 86400:
        v = round(t / 86400, 1)
        return f"{v:.1f}".replace(".", ",").removesuffix(",0") + (" día" if v == 1 else " días")
    if t >= 3600:
        return f"{t / 3600:.1f}".replace(".", ",").removesuffix(",0") + " h"
    if t >= 60:
        return f"{t / 60:.0f} min"
    return f"{t:.0f} s"


def log_error(text: str) -> str | None:
    """Mensaje de error de penhmain en su salida en pantalla (o None)."""
    low = text.lower()
    if "iretrn =" in low:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].lower().startswith("iretrn ="):
                reason = lines[i - 1] if i > 0 else ""
                return f"{reason} ({lines[i]})".strip()
    m = re.search(r"(at line \d+ of file [^\n]+)\n(fortran runtime error: [^\n]+)", text, re.I)
    if m:
        return f"{m.group(2).strip()} ({m.group(1).strip()})"
    m = re.search(r"(fortran runtime error: [^\n]+)", text, re.I)
    if m:
        return m.group(1).strip()
    if "segmentation fault" in low or "sigsegv" in low:
        return "penhmain se ha detenido por un fallo de memoria (segmentation fault)"
    # penhmain no ha llegado a empezar (lo dice el cargador del sistema o la orden del guion).
    m = re.search(r"error while loading shared libraries: ([^:\s]+)", text, re.I)
    if m:
        return (f"penhmain no arranca: falta la biblioteca {m.group(1)} (en Linux o WSL, instálela en la distribución: "
                "«sudo apt install libgfortran5» si es la de gfortran)")
    if "exec format error" in low or "cannot execute binary file" in low:
        return "penhmain no arranca: no es un ejecutable de este sistema (compílelo en este ordenador)"
    if "illegal instruction" in low or "sigill" in low:
        return ("penhmain se ha detenido: instrucción no válida (compilado para otro procesador: compílelo en este "
                "ordenador)")
    from ..penhan.run import ERROR_MARK

    m = re.search(re.escape(ERROR_MARK) + r" (-?\d+)", text)
    if m:
        if "permission denied" in low:
            return "penhmain no arranca: no tiene permiso de ejecución"
        return f"penhmain ha terminado con el código de salida {m.group(1)}"
    return None


class ResultsFolder:
    """Una carpeta con resultados de penhmain."""

    def __init__(self, path: str | os.PathLike, input_path: str | os.PathLike | None = None, *, runs: bool = True):
        self.path = Path(path)
        self.forced_input = Path(input_path) if input_path else None
        self.look_for_runs = runs
        self.files: list[OutputFile] = []
        self.inputs: list[Path] = []
        self.input: InputInfo | None = None
        self.runs: list[ResultsFolder] = []
        self.label = ""  # nombre de la ejecución o de la variante (del índice de ARGUS)
        self.manifest: dict | None = None
        self.not_replicas: str | None = None  # por qué sus subcarpetas no se combinan (casos distintos…)
        self.scanned_at = 0.0
        self.scan()

    # -- búsqueda ----------------------------------------------------------------
    def scan(self) -> None:
        files: list[OutputFile] = []
        inputs: list[Path] = []
        subdirs: list[Path] = []
        manifest = None
        try:
            entries = sorted(self.path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            entries = []
        for p in entries:
            try:
                if p.is_dir():
                    subdirs.append(p)
                    continue
                if not p.is_file():
                    continue
                st = p.stat()
            except OSError:
                continue
            name = p.name.lower()
            if name.endswith(".in"):
                inputs.append(p)
                continue
            if name in MANIFEST_NAMES:
                try:
                    manifest = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = None
                if not isinstance(manifest, dict):
                    manifest = None
                continue
            key = (str(p), st.st_size, int(st.st_mtime_ns))
            if key in _ID_CACHE:
                ident = _ID_CACHE[key]
            else:
                ident = identify(p)
                if len(_ID_CACHE) > 20000:
                    _ID_CACHE.clear()
                _ID_CACHE[key] = ident
            if ident is not None:
                if len(_LAST_ID) > 20000:
                    _LAST_ID.clear()
                _LAST_ID[str(p)] = ident
            elif time.time() - st.st_mtime <= STALE_MARGIN and str(p) in _LAST_ID:
                # penhmain lo está reescribiendo (vacío o con la cabecera a medias): sigue siendo lo que era.
                ident = _LAST_ID[str(p)]
            if ident is not None:
                files.append(OutputFile(p, ident, st.st_size, st.st_mtime))
        _mark_copies(files)
        self.files = files
        self.inputs = inputs
        self.manifest = manifest
        self.input = self._choose_input()
        if self.look_for_runs:
            # En una campaña, cada variante puede tener a su vez ejecuciones en paralelo.
            nested = bool(self.manifest and self.manifest.get("kind") == "campana")
            runs = []
            for d, label in self._run_dirs(subdirs):
                try:
                    r = ResultsFolder(d, runs=nested)
                except OSError:
                    continue
                r.label = label
                runs.append(r)
            # Con el índice de ARGUS se ven todas, también las que aún no han empezado.
            self.runs = runs if self.manifest else [r for r in runs if r.files]
            # Sin el índice de ARGUS, solo se combinan si son ejecuciones del mismo caso con semillas distintas.
            self.not_replicas = None if self.manifest else replica_problem(self.runs)
        self.scanned_at = time.time()

    def _run_dirs(self, subdirs: list[Path]) -> list[tuple[Path, str]]:
        if self.manifest and isinstance(self.manifest.get("runs"), list):
            out = []
            for r in self.manifest["runs"]:
                folder = r.get("folder") if isinstance(r, dict) else r
                label = str(r.get("label", "")) if isinstance(r, dict) else ""
                if isinstance(folder, str) and folder and ".." not in Path(folder).parts:
                    out.append((self.path / folder, label or Path(folder).name))
            return out
        from ..penhan.run import STASH_NOTE

        out = []
        for d in subdirs:
            res = d / "penhmain-res.dat"
            if res.is_file() and "penhmain-sum" in read_head(res, 600):
                continue  # resultados ya combinados con penhmain-sum: no son otra ejecución
            if (d / STASH_NOTE).is_file():
                continue  # resultados anteriores que ARGUS guardó al volver a ejecutar: no son otra ejecución
            if res.is_file() or (d / "penhmain.dat").is_file():
                out.append((d, d.name))
        return out

    def leaves(self) -> list[ResultsFolder]:
        """Las carpetas donde simula penhmain (las ejecuciones; en una campaña, las de cada variante)."""
        if not self.runs:
            return [self]
        return [leaf for r in self.runs for leaf in r.leaves()]

    @property
    def parameters(self) -> list[dict]:
        """Los parámetros de una campaña (del índice de ARGUS)."""
        if not self.is_campaign:
            return []
        ps = self.manifest.get("parameters")
        return [p for p in ps if isinstance(p, dict)] if isinstance(ps, list) else []

    def variant_values(self, i: int) -> dict:
        """Valores de los parámetros de la variante ``i`` de una campaña."""
        runs = self.manifest.get("runs") if self.manifest else None
        if not isinstance(runs, list) or not 0 <= i < len(runs) or not isinstance(runs[i], dict):
            return {}
        v = runs[i].get("values")
        return dict(v) if isinstance(v, dict) else {}

    def _choose_input(self) -> InputInfo | None:
        if self.forced_input is not None:
            return read_input_info(self.forced_input)
        res = self.find("res")
        if res is not None:
            title = _title_of_res(res.path)
            for p in self.inputs:
                info = read_input_info(p)
                if info is not None and info.title.strip() == title.strip() and title:
                    return info
        if len(self.inputs) == 1:
            return read_input_info(self.inputs[0])
        return None

    # -- consultas -----------------------------------------------------------------
    def find(self, key: str) -> OutputFile | None:
        """El fichero con esa clave (``kind#detector@partícula``) o, si no lo hay, el primero de ese tipo
        (``kind``) que no sea una copia."""
        for f in self.files:
            if f.key == key:
                return f
        for f in self.files:
            if f.kind == key and not f.copy:
                return f
        return None

    def visible(self) -> list[OutputFile]:
        return [f for f in self.files if not f.hidden]

    def signature(self) -> tuple:
        """Cambia si cambia algún resultado (para volver a leer)."""
        own = tuple((f.path.name, f.size, f.mtime) for f in self.files)
        return own + tuple(r.signature() for r in self.runs)

    @property
    def writing(self) -> bool:
        """penhmain está volcando sus resultados: el resumen, que es lo último que cierra, es reciente y está a
        medias (los demás ficheros también pueden estarlo)."""
        from .summary import summary_complete

        res = self.find("res")
        if res is None or time.time() - res.mtime > STALE_MARGIN:
            return False
        return not summary_complete(res.path)

    @property
    def is_sum(self) -> bool:
        """Resultados combinados con penhmain-sum."""
        res = self.find("res")
        return res is not None and "penhmain-sum" in read_head(res.path, 600)

    @property
    def is_campaign(self) -> bool:
        return bool(self.manifest and self.manifest.get("kind") == "campana")

    @property
    def combines(self) -> bool:
        """Sus resultados son los de sus ejecuciones combinadas (en paralelo, del mismo caso; sin resultados
        propios)."""
        return bool(self.runs) and not self.files and not self.is_campaign and self.not_replicas is None

    @property
    def is_group(self) -> bool:
        """Subcarpetas con resultados que no se combinan: casos distintos (o copias con las mismas semillas)."""
        return bool(self.runs) and not self.files and not self.is_campaign and self.not_replicas is not None

    @property
    def is_parallel(self) -> bool:
        return bool(self.manifest and self.manifest.get("kind") == "paralelo") or (
            len(self.runs) >= 2 and self.combines)

    # -- estado de la simulación ---------------------------------------------------------
    def state(self, now: float | None = None) -> RunState:
        from .summary import read_summary

        now = time.time() if now is None else now
        info = self.input
        known = info is not None
        # Como penhmain: NSIMSH < 1 son 2e9 lluvias; TIME < 1, 100 s (sin TIME, 2e9 s).
        target = info.nsimsh if info and info.nsimsh is not None and info.nsimsh >= 1.0 else 2.0e9
        tlimit = 2.0e9 if not info or info.time_limit is None else max(info.time_limit, 0.0)
        if tlimit < 1.0:
            tlimit = 100.0
        if info and info.resume:
            # TIME cuenta desde que empieza esta ejecución, pero el tiempo del resumen suma el de las anteriores
            # (TIMET = TSEC + TIMETA en penhmain.f): el final por tiempo solo se sabe por el «END» del registro.
            tlimit = float("inf")
        period = dump_period(info.dumpp if info else None)
        run_log = next((f for f in self.files if f.kind == "run-log" and f.ident.variant != "sum" and not f.copy),
                       None)
        log_text = ""
        if run_log is not None:
            try:
                log_text = run_log.path.read_text(encoding="latin-1", errors="replace")[-20000:]
            except OSError:
                log_text = ""
        err = log_error(log_text) if log_text else None
        ended = bool(log_text) and "*** end ***" in log_text.lower()
        pen_log = self.find("log")
        if pen_log is not None and not ended:
            try:
                with open(pen_log.path, "rb") as fh:
                    fh.seek(max(pen_log.size - 4096, 0))
                    ended = b"***  END  ***" in fh.read()
            except OSError:
                pass
        res = self.find("res")
        if res is None:
            if err:
                return RunState("error", f"penhmain se ha detenido: {err}", error=err)
            if pen_log is not None or run_log is not None:
                return RunState("empezando", "penhmain se está inicializando o no ha volcado todavía resultados.",
                                target=target)
            return RunState("vacia", "No hay resultados de penhmain en esta carpeta.", target=target)
        try:
            s = read_summary(res.path)
        except (OSError, ValueError):
            return RunState("simulando", "El resumen se está escribiendo.", target=target, updated=res.mtime)
        n = s.showers
        decays = (info is not None and info.kparp == 0) or s.kparp == 0
        # Lo que cuenta para TIME es el tiempo desde que acabó la inicialización, no el «Calculation time».
        sim_time, rate = simulation_clock(s, _previous_dump(res.path, s), tlimit)
        progress = min(max(n / target, sim_time / tlimit), 1.0) if target else None
        eta = (target - n) / rate if rate and target > n else None
        if math.isfinite(tlimit) and tlimit > sim_time and (eta is None or tlimit - sim_time < eta):
            eta = tlimit - sim_time  # TIME acabará antes que NSIMSH
        base = dict(showers=n, target=target, progress=progress, eta=eta, speed=s.speed or None,
                    updated=res.mtime, decays=decays)
        count = count_text(n, decays) + (f" de {fmt_count(target)}" if info and info.nsimsh else "")
        # ¿Lo escribió penhmain al acabar? Los volcados parciales solo se escriben pasados DUMPP segundos desde el
        # principio (TSECAD = 0 al empezar a simular) y mientras no se ha acabado el tiempo de TIME (el de CPU de la
        # simulación nunca pasa del de reloj): sin volcados, o con un tiempo total que no llega a DUMPP o uno de CPU
        # que ya llega a TIME, el resumen es el del final.
        final = period is None or s.time_total <= period or s.time_cpu >= tlimit
        by_time = math.isfinite(tlimit) and n < target and (s.time_cpu >= tlimit or (final and s.time_total >= tlimit))
        if self.is_sum:
            return RunState("terminada", f"Resultados combinados con penhmain-sum: {count}.",
                            **{**base, "progress": None, "eta": None, "target": None})
        if not known and not ended:
            fresh = now - res.mtime <= STALE_MARGIN or not s.complete
            text = (f"{count}; no se sabe cuántas se pedían (no se ha encontrado el .in)."
                    + (" Se ha escrito hace poco: puede que siga simulando." if fresh else ""))
            return RunState("simulando" if fresh else "resultados", text,
                            **{**base, "progress": None, "eta": None, "target": None})
        if n >= target or by_time or ended:
            why = "" if n >= target else (" (se acabó el tiempo de TIME)"
                                          if by_time or (math.isfinite(tlimit) and s.time_total >= tlimit) else "")
            return RunState("terminada", f"Terminada: {count}{why}.", **{**base, "progress": 1.0, "eta": None})
        if err:
            return RunState("error", f"penhmain se ha detenido tras {count}: {err}", error=err, **base)
        age = now - res.mtime
        if not s.complete or period is None or age <= period + STALE_MARGIN:
            text = f"Simulando: {count}"
            if eta is not None:
                text += f"; faltan unos {_fmt_duration(eta)}"
            if period is not None:
                text += f" (resultados cada {_fmt_duration(period)})"
            return RunState("simulando", text + ".", **base)
        # Sin el registro de penhmain no se sabe si acabó por TIME (el tiempo total incluye la inicialización).
        maybe = " o ha terminado por el tiempo de TIME" if math.isfinite(tlimit) and s.time_total >= tlimit else ""
        return RunState("parada", f"Sin cambios desde hace {_fmt_duration(age)}: {count}. ¿Se ha detenido "
                                  f"penhmain{maybe}?", **base)


def aggregate_state(folder: ResultsFolder, now: float | None = None) -> RunState:
    """Estado de todas las ejecuciones de una carpeta en paralelo o de una campaña, juntas."""
    leaves = folder.leaves()
    if leaves == [folder]:
        return folder.state(now)
    states = [f.state(now) for f in leaves]
    counts: dict[str, int] = {}
    for st in states:
        counts[st.status] = counts.get(st.status, 0) + 1
    showers = sum(st.showers for st in states)
    target = sum(st.target or 0 for st in states)
    what = "variantes" if folder.is_campaign else "casos" if folder.is_group else "ejecuciones"
    words = _STATUS_WORDS_M if folder.is_group else _STATUS_WORDS
    parts = ", ".join(f"{n} {words.get(k, k)}" for k, n in counts.items())
    n_var = f"{len(folder.runs)} {what}" + (f" ({len(leaves)} ejecuciones)" if folder.is_campaign and
                                              len(leaves) != len(folder.runs) else "")
    decays = bool(states) and all(st.decays for st in states if st.showers) and any(st.showers for st in states)
    text = f"{n_var}: {parts}; {count_text(showers, decays)}" + (f" de {fmt_count(target)}" if target else "")
    etas = [st.eta for st in states if st.eta]
    running = counts.get("simulando", 0) + counts.get("empezando", 0)
    if etas and running:
        text += f"; faltan unos {_fmt_duration(max(etas))}"
    err = next((st for st in states if st.status == "error"), None)
    if err is not None:
        text += f". Error en una ejecución: {err.error}"
    if folder.is_group:
        text += f". No se combinan: {folder.not_replicas}"
    status = "simulando" if running else ("error" if err is not None else
                                          "terminada" if counts.get("terminada") == len(states) else "parada"
                                          if counts.get("parada") else "resultados")
    prog = showers / target if target else None
    return RunState(status, text, showers, target or None, prog, max(etas) if etas and running else None,
                    error=err.error if err is not None else None, decays=decays)


_STATUS_WORDS = {"vacia": "sin empezar", "empezando": "empezando", "simulando": "simulando", "terminada": "terminadas",
                 "parada": "paradas", "error": "con error", "resultados": "con resultados"}
_STATUS_WORDS_M = {**_STATUS_WORDS, "terminada": "terminados", "parada": "parados"}

__all__ = ["InputInfo", "MANIFEST_NAMES", "OutputFile", "RUN_KEYWORDS", "ResultsFolder", "RunState", "aggregate_state",
           "count_text", "dump_period", "kind_of", "log_error", "read_input_info", "replica_problem",
           "simulation_clock"]
