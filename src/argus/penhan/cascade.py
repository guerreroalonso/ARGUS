"""La cascada: las primeras lluvias de un caso simuladas con el penhmain del usuario, para ver sus trayectorias
(0.13.0, ADR-017).

penhmain escribe las trayectorias de LTRACK en un fichero por tipo de partícula (electron.trk…), pero como mucho de
NPSHOW = 50 lluvias (``NPSHOW = MIN(LTRACK, 50)`` en penhmain.f). Para ver 1000, ARGUS lanza varias ejecuciones
cortas del mismo caso, cada una en su carpeta (``cascada/run-01``…), en paralelo y en segundo plano:

* la primera con las semillas del .in y todo lo demás igual: son exactamente las primeras lluvias de la simulación
  de verdad (comprobado con penhmain: los mismos .trk byte a byte);
* las demás con pares de semillas precalculados de penhmain (``RSEED -n``), independientes entre sí y de la primera
  (sin RSEED o con «RSEED 1 1», el caso empieza con el par 1, que es (1, 1): ese no se repite).

Si el penhmain del usuario guarda otro número de lluvias (ha cambiado NPSHOW y lo ha recompilado), ARGUS lo ve con
una ejecución de prueba de una lluvia de electrones con LTRACK 99 (sin los datos de hadrones: unos segundos).
penhmain escribe ``Files with NPSHOW = n`` con dos cifras: si guarda menos de 99, ARGUS sabe cuántas; si no, que
guarda al menos 99, y las ejecuciones son de 99 lluvias.

El .in de cada ejecución es el del caso con LTRACK y NSIMSH = sus lluvias, sin límite de tiempo, sin volcado
(DUMPP 1e12: sin DUMPP, penhmain vuelca igual en dump.dmp) y con sus semillas. La primera, nada más (la rejilla de
dosis, EPROF y las edades gastan números aleatorios: sin ellos serían otras lluvias), salvo que los ficheros de
resultados que el .in escribe en otra carpeta («IDPSF ../fase.psf») se quedan en la suya, con su nombre. Las demás,
además, sin lo que solo registra resultados: rejilla de dosis, detectores de energía depositada y angulares,
distribuciones de las partículas que salen, EPROF y el espacio de fases y los ficheros de los detectores de impacto
(estos se quedan: con IDCUT 0 absorben lo que entra).

Con una fuente de espacio de fases (IPSFN), cada ejecución lee un trozo con sus lluvias (``argus-psf.dat``, las
líneas tal cual): la primera, el principio del fichero del caso; las demás, las siguientes. Una lluvia es un grupo de
partículas del fichero (la que tiene NSHI > 0 y las que la siguen con NSHI 0) y las lluvias pedidas son grupos: el N de
penhmain cuenta también las lluvias de la simulación que no llegaron al espacio de fases (los saltos de NSHI), que no
tienen nada que ver (hasta 0.17.2 se pedían en N y salían menos, con huecos al final de cada ejecución). El trozo
acaba con la última lluvia de la ejecución: penhmain no puede pasarse, ni con la ruleta rusa de WGTWIN (al matar
una partícula del fichero sigue leyendo dentro de la misma lluvia: con el fichero entero, la primera ejecución
acababa más allá de su NSIMSH).

**De qué lluvia es cada trayectoria** (``results.shower``): penhmain no lo escribe. Casi siempre se sabe con certeza
(cada secundaria sale de un vértice de su madre, y la partícula de la fuente que empieza cada lluvia se reconoce),
pero con una fuente radiactiva puntual (o algún caso raro) los .trk no lo dicen de todas: las demás partículas de cada
desintegración salen todas del mismo punto, sin trayectoria propia. Entonces ARGUS repite esa ejecución con el mismo
penhmain, el mismo .in y las mismas semillas, parando tras b lluvias (NSIMSH = b; con un espacio de fases, el N de
penhmain al acabar la lluvia b): penhmain es determinista y sus .trk son el principio exacto de los de la ejecución
entera (comprobado byte a byte), así que su tamaño dice dónde acaba la lluvia b en el fichero de cada tipo de
partícula. Son los «cortes»: cada uno, en una carpeta temporal dentro de la de la cascada (con enlaces a los ficheros
de la ejecución) que se borra al terminar; lo que dicen se apunta en ``argus-cortes.json`` de la ejecución. Solo se
hacen los que hacen falta y van en paralelo, como las ejecuciones. Una ejecución cuenta como terminada cuando se sabe
la lluvia de todas sus trayectorias.

Los ficheros que lee penhmain se enlazan (enlace duro: no ocupan más) o, si no se puede, se copian a cada carpeta;
los que el .in cita con una ruta que sube de carpeta («MFNAME ../mats/agua.mat»), con su nombre (y así los cita el
.in de la ejecución). Nada del caso se sobrescribe: una cascada de otra versión del caso va a otra carpeta
(``cascada-2``…); el índice ``argus-cascada.json`` dice de qué caso es cada una. En la carpeta de cada ejecución,
ARGUS apunta el proceso de penhmain (``argus-pid``) y cómo ha terminado (``argus-rc``).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ..core.files import copy_new, write_new
from ..core.numfmt import fmt_int, plural
from . import sections as sc
from .inputfile import InDocument, parse_in_text, write_in_text
from .materials import hadron_data_needed
from .programs import HOST, NO_WINDOW, Launch, decode_output, exe_info, launch_for, wsl_executable
from .simfolder import SimPlan, plan_folder

CASCADE_DIR = "cascada"
MANIFEST = "argus-cascada.json"
MANIFEST_VERSION = 2  # 1: ARGUS 0.13.0 (otra firma; sin RSEED, la segunda ejecución repetía las lluvias de la primera)
DEFAULT_SHOWERS = 1000
NPSHOW_DEFAULT = 50  # penhmain.f: NPSHOW = MIN(LTRACK, 50)
PROBE_LTRACK = 99  # penhmain escribe NPSHOW con dos cifras (I2): con 100 o más, «**»
MAX_SHOWERS = 1000  # las lluvias que se pueden pedir en la interfaz (Rafael, 10-10-2026: «como máximo 1000»)
SHOWER_LIMIT = 50000  # lo que admite el núcleo (las cascadas de antes de 0.14.0 podían tener más de 1000)
MAX_SEED_PAIR = 1001  # pares precalculados de RAND0
RUN_SCRIPT = "argus-run.sh"
PID_FILE = "argus-pid"
RC_FILE = "argus-rc"
LAUNCH_LOG = "argus-lanzador.out"
OUTPUT = "penhmain.out"
PSF_CHUNK = "argus-psf.dat"
PSF_RECORD = 136  # caracteres de cada línea del espacio de fases que lee penhmain (RDPSF)
CUT_PREFIX = "argus-corte-"  # carpetas temporales de los cortes (dentro de la de la cascada)
CUT_ROUNDS = 3  # vueltas de cortes por ejecución (la segunda y la tercera, solo si algo no cuadra)
PROBE_TIMEOUT = 300.0
PID_WAIT = 20.0  # lo que se espera a que WSL arranque y apunte el PID de penhmain (para pararlo)
ORPHAN_POLL = 5.0  # cada cuánto se mira si siguen las ejecuciones de otra sesión
# Solo registran resultados: no cambian la física (la rejilla de dosis, EPROF e IDAGEL sí gastan números aleatorios).
SCORING_ONLY = ("GRIDX", "GRIDY", "GRIDZ", "GRIDR", "ENDETC", "EDSPC", "EDBODY", "ANGDET", "ADENER", "ADKPAR",
                "ADSPC", "NBE", "NBANGL", "EPROF", "EPROPM", "DUMPTO", "DUMPP", "RESUME", "IDSPC", "IDPSF", "IDFLNC",
                "IDAGEL", "IDAGEF", "LTRACK", "NSIMSH", "TIME")
JOB_ONLY = ("LTRACK", "NSIMSH", "TIME", "DUMPTO", "DUMPP", "RESUME")
SOURCE_ONLY = ("SKPAR", "SRNUCL", "SENERG", "SPECTR", "SLINES", "SGPOL", "IPSFN", "IPSPLI", "WGTWIN", "EPMAX")
# Ficheros que escribe penhmain con el nombre que dice el .in.
OUTPUT_NAMES = ("IDSPC", "IDPSF", "IDFLNC", "IDAGEF", "ADSPC", "EDSPC")
# Ficheros que lee penhmain con el nombre que dice el .in (por el tipo de fichero de ``simfolder``).
INPUT_NAMES = {"mat": "MFNAME", "geo": "GEOMFN", "nuc": "SRNUCL", "psf": "IPSFN", "eprof": "EPROF"}
# Lo que no cambia las trayectorias de la primera ejecución ni gasta números aleatorios: solo dice qué se registra y
# dónde se escribe (no cuenta en la firma del caso).
RECORD_ONLY = ("TITLE", "ENDETC", "EDSPC", "EDBODY", "ANGDET", "ADENER", "ADKPAR", "ADSPC", "NBE", "NBANGL",
               "IDSPC", "IDPSF", "IDFLNC", "IDAGEF", "DUMPP")  # (DUMPP: el «sin volcado» de ``cascade_doc``)
NO_DUMP = 1.0e12  # DUMPP con el que penhmain no escribe ningún volcado (≥ 1e10)
_NPSHOW = re.compile(r"Files with NPSHOW\s*=\s*(\S+)\s+showers")


# ---------------------------------------------------------------------------------------------------------------------
# El .in de cada ejecución
# ---------------------------------------------------------------------------------------------------------------------

def case_seeds(doc: InDocument) -> tuple[int, int]:
    """Las semillas con que empieza la simulación del .in (las de penhmain si no hay RSEED; con varias, la última:
    la que lee penhmain)."""
    s = sc.seeds(doc)
    return (int(s[-1][0]), int(s[-1][1])) if s else (1, 1)


def seed_pair(seeds: tuple[int, int]) -> int | None:
    """El par precalculado de penhmain (RAND0) con que empiezan estas semillas: con «RSEED -n …», el n; sin RSEED o
    con «RSEED 1 1», el 1 (el primero de la lista es (1, 1)); con otras, None."""
    if int(seeds[0]) < 0:
        return -int(seeds[0])
    if (int(seeds[0]), int(seeds[1])) == (1, 1):
        return 1
    return None


def _base_name(name: str) -> str:
    """El nombre sin carpeta («../a/b.dat», «D:\\x\\b.dat» o «D:b.dat» → «b.dat»)."""
    return PureWindowsPath(name.strip().replace("/", "\\")).name


def _unique_name(name: str, taken: set[str], limit: int = 20) -> str:
    """``name`` o, si ya lo tiene otro fichero (``taken``, en minúsculas), «nombre-2.ext»… (sin pasar de ``limit``
    caracteres: penhmain lee los nombres con A20)."""
    if name.lower() not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    k = 2
    while True:
        tail = f"-{k}" + (f".{ext}" if ext else "")
        cand = stem[: max(1, limit - len(tail))] + tail
        if cand.lower() not in taken:
            return cand
        k += 1


def _local_outputs(d: InDocument) -> None:
    """Los ficheros de resultados que el .in escribe en otra carpeta («IDPSF ../fase.psf», «D:\\psf\\a.psf»), solo
    con su nombre: que penhmain los escriba en la carpeta de la ejecución y no encima de los del caso (o del de la
    simulación de verdad, que puede estar escribiéndolo a la vez). No gastan números aleatorios: las trayectorias
    son las mismas."""
    taken: set[str] = set()
    for kw in OUTPUT_NAMES:
        for ln in d.find(sc.kw6(kw)):
            name = (ln.text or "").strip()
            if not name:
                continue
            new = _unique_name(_base_name(name) or name, taken)
            taken.add(new.lower())
            if new != name:
                d.modify(ln, text=new)


def cascade_doc(doc: InDocument, showers: int, seeds: tuple[int, int], *, exact: bool = False,
                probe: bool = False) -> InDocument:
    """El .in de una ejecución de la cascada.

    Con ``exact`` (la primera ejecución), el del caso tal cual salvo el trabajo (LTRACK, NSIMSH, TIME, volcados y
    semillas) y los ficheros de resultados con carpeta (ver ``_local_outputs``): con las semillas del caso son
    exactamente sus primeras lluvias. La rejilla de dosis, EPROF y las edades de un detector de impacto gastan
    números aleatorios (penhmain sortea un punto del tramo para cada depósito): sin ellas las trayectorias serían
    otras igual de válidas, pero no las de la simulación.

    Sin ``exact`` (las demás, con otras semillas), sin lo que solo registra resultados (su física es la misma y se
    ahorran ficheros grandes, como el mapa de dosis de cada ejecución).

    ``probe``: la de prueba, con una lluvia de electrones de 1 MeV y LTRACK = 99, para saber cuántas lluvias guarda
    este penhmain.
    """
    d = parse_in_text(write_in_text(doc), doc.path)
    for kw in (JOB_ONLY if exact and not probe else SCORING_ONLY):
        d.remove(sc.kw6(kw))
    if exact and not probe:
        _local_outputs(d)
    if not exact or probe:
        for ln in d.find("IMPDET"):
            vals = list(ln.values)
            if len(vals) >= 4 and int(vals[3]) != 0:
                vals[3] = 0  # sin espacio de fases (IPSF): solo escribiría un fichero grande
                d.modify(ln, values=vals)
    if probe:
        for kw in SOURCE_ONLY:
            d.remove(sc.kw6(kw))
        sc.set_value(d, "SKPAR", [1])
        sc.set_value(d, "SENERG", [1.0e6])
        sc.set_value(d, "NSIMSH", [1.0])
        sc.set_value(d, "LTRACK", [PROBE_LTRACK])
    else:
        sc.set_value(d, "NSIMSH", [float(showers)])
        sc.set_value(d, "LTRACK", [int(showers)])
    # Sin volcado: sin DUMPP, penhmain vuelca igual en dump.dmp (una vez al día y al acabar: su DUMPP por defecto es
    # 2e9); con DUMPP ≥ 1e10, no (penhmain.f 4235-4245). Con un TAC, cientos de MB por ejecución (duodécima revisión).
    sc.set_value(d, "DUMPP", [NO_DUMP])
    sc.set_seeds(d, [(int(seeds[0]), int(seeds[1]))])
    return d


def run_seeds(case: tuple[int, int], n: int) -> list[tuple[int, int]]:
    """Semillas de ``n`` ejecuciones: la primera, las del caso; las demás, pares precalculados distintos del suyo."""
    out = [(int(case[0]), int(case[1]))]
    pair = seed_pair(case)
    used = {pair} if pair is not None else set()
    k = 1
    while len(out) < n:
        if k > MAX_SEED_PAIR:
            raise ValueError(f"penhmain tiene {MAX_SEED_PAIR} pares de semillas: no caben {fmt_int(n)} ejecuciones")
        if k not in used:
            out.append((-k, 0))
        k += 1
    return out


def runs_for(showers: int, cap: int) -> list[int]:
    """Lluvias de cada ejecución para tener ``showers`` si cada una guarda como mucho ``cap``."""
    cap = max(int(cap), 1)
    n = max(1, math.ceil(showers / cap))
    return [min(cap, showers - i * cap) for i in range(n)]


def read_npshow(log_text: str) -> int | None:
    """Cuántas lluvias dice penhmain que guarda (``Files with NPSHOW = 50 showers``); con «**» (100 o más, el
    formato I2 de penhmain no cabe), None."""
    m = _NPSHOW.search(log_text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# La firma del caso (para saber si una cascada sirve todavía)
# ---------------------------------------------------------------------------------------------------------------------

def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def file_sha1(path: str | os.PathLike) -> str | None:
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _physics_lines(d: InDocument) -> list[str]:
    """Las líneas del .in que cambian las trayectorias, en su orden (sin comentarios, ni el título, ni lo que solo
    dice qué se registra y dónde: ``RECORD_ONLY``). De un detector de impacto solo cuenta IDCUT (con 0 absorbe lo
    que entra) y a qué cuerpos y partículas se aplica."""
    out = []
    for ln in d.lines:
        kw = ln.kw.strip()
        if ln.is_comment or kw in RECORD_ONLY:
            continue
        vals = list(ln.values)
        if kw == "IMPDET":
            vals = vals[4:5]
        out.append(f"{ln.kw}|{vals!r}|{(ln.text or '').strip()}|{ln.label}|{ln.kb}")
    return out


_CONTENT: dict[str, tuple[tuple[int, int], str]] = {}  # fichero → ((tamaño, fecha en ns), huella de su contenido)
_SAMPLED = 512 << 20  # desde este tamaño, la huella es de trozos repartidos por el fichero (un PSF de varios GB)


def content_sha1(path: str | os.PathLike) -> str | None:
    """Huella del contenido de un fichero (None si no se puede leer), guardada mientras no cambien su tamaño y su
    fecha. Hasta ``_SAMPLED``, el fichero entero; de más, su tamaño, los primeros y los últimos 16 MB y 32 trozos de
    1 MB repartidos (leerlo entero cada vez que se abre ARGUS serían segundos)."""
    p = os.path.abspath(path)
    try:
        st = os.stat(p)
    except OSError:
        return None
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _CONTENT.get(p)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    h = hashlib.sha1()
    try:
        with open(p, "rb") as f:
            if st.st_size <= _SAMPLED:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            else:
                h.update(f"muestras:{st.st_size}".encode())
                block = 1 << 20
                spans = [(0, 16 * block), (max(0, st.st_size - 16 * block), 16 * block)]
                spans += [(k * max(0, st.st_size - block) // 33, block) for k in range(1, 33)]
                for at, size in spans:
                    f.seek(at)
                    h.update(f.read(size))
    except OSError:
        return None
    if len(_CONTENT) > 500:
        _CONTENT.clear()
    _CONTENT[p] = (stamp, h.hexdigest())
    return _CONTENT[p][1]


def _file_parts(in_path: Path, doc: InDocument, endf: bool = False, dated: bool = False) -> list[bytes]:
    """Lo que lee penhmain: su contenido (la huella de :func:`content_sha1`) o, con ``dated`` (la firma de 0.16.0 a
    0.17.2), su fecha y su tamaño: al copiar el caso sin las fechas (``cp -r``, git, un pendrive), la cascada se
    daba por «de otro caso» y se simulaba otra vez entera (undécima revisión). Sin los ENDF (``endf``: con los que
    haya junto al .in, como hasta 0.16.1): sus nombres los dicen los .mat, que ya cuentan, y si se buscan aquí la
    firma depende de dónde están (en la carpeta de los ENDF del usuario o, después de «Ejecutar», copiados junto al
    .in) y una cascada ya simulada no se reutilizaba (revisión de 0.16)."""
    parts = []
    plan = plan_folder(in_path, doc, in_path.parent, legacy=endf)
    for it in sorted(plan.items, key=lambda i: i.name.lower()):
        if it.kind in ("in", "txt", "ctjson", "exe", "dll", "dump") or it.source is None:
            continue
        if it.kind == "endf" and not endf:
            continue
        if not dated:
            sha = content_sha1(it.source)
            parts.append(f"{it.kind}:{it.name}:{sha or '?'}".encode())
            continue
        try:
            st = it.source.stat()
            parts.append(f"{it.kind}:{it.name}:{st.st_size}:{int(st.st_mtime)}".encode())
        except OSError:
            parts.append(f"{it.kind}:{it.name}:?".encode())
    return parts


def case_signature(in_path: str | os.PathLike, doc: InDocument, exe: str | os.PathLike | None, showers: int, *,
                   legacy: bool = False, dated: bool = False) -> str:
    """Cambia si cambia algo que afecta a las trayectorias: las líneas del .in que cambian la física (no el trabajo,
    ni el título, ni lo que solo dice qué se registra: detectores de energía y angulares, intervalos, nombres de los
    ficheros de resultados…), la geometría y lo que incluye, los .mat y demás ficheros que lee penhmain (su
    contenido; los ENDF, por los .mat que los nombran), el penhmain y las lluvias pedidas. ``dated``: la de 0.16.2 a
    0.17.2, con la fecha y el tamaño de los ficheros; ``legacy``: la de 0.16.0 y 0.16.1, además con los ENDF que
    hubiera junto al .in (para reconocer las cascadas simuladas con ellas).

    Con un espacio de fases, desde 0.17.3 las lluvias son grupos del fichero y no el N de penhmain (ver el principio
    del módulo): la firma lo dice, y las cascadas de antes ya no son las del caso."""
    in_path = Path(in_path)
    d = cascade_doc(doc, showers, case_seeds(doc), exact=True)
    parts = ["\n".join(_physics_lines(d)).encode("latin-1", errors="replace"), str(int(showers)).encode()]
    parts += _file_parts(in_path, doc, endf=legacy, dated=dated or legacy)
    if exe:
        parts.append((content_sha1(exe) or str(exe)).encode())
    if doc.model.source.from_psf and not (dated or legacy):
        parts.append(b"psf:grupos")
    return _sha1(b"\n".join(parts))


def _signature_v1(in_path: Path, doc: InDocument, exe: str | os.PathLike | None, showers: int) -> str:
    """La firma de ARGUS 0.13.0 (índice versión 1): el .in de la primera ejecución entero."""
    d = parse_in_text(write_in_text(doc), doc.path)
    for kw in JOB_ONLY:
        d.remove(sc.kw6(kw))
    sc.set_value(d, "NSIMSH", [float(showers)])
    sc.set_value(d, "LTRACK", [int(showers)])
    sc.set_seeds(d, [case_seeds(doc)])
    parts = [write_in_text(d).encode("latin-1", errors="replace"), str(int(showers)).encode()]
    parts += _file_parts(in_path, doc, endf=True)
    if exe:
        parts.append((file_sha1(exe) or str(exe)).encode())
    return _sha1(b"\n".join(parts))


# ---------------------------------------------------------------------------------------------------------------------
# Carpetas e índice
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class CascadeRun:
    folder: str  # relativa a la de la cascada (run-01…)
    showers: int  # NSIMSH (las lluvias de penhmain)
    seeds: tuple[int, int]
    # Las lluvias que tiene, como las cuenta el visor: con un espacio de fases, sus grupos (el N de penhmain cuenta
    # también las que no llegaron al fichero); sin él, ``showers``. None en los índices de 0.17.2 y antes: ver
    # :func:`run_counts`.
    groups: int | None = None

    @property
    def count(self) -> int:
        return self.groups if self.groups is not None else self.showers


@dataclass
class CascadeInfo:
    """Una cascada en disco (su índice)."""

    path: Path
    case: str
    signature: str
    showers: int
    per_run: int
    cap: int | None
    exe: str
    runs: list[CascadeRun]
    created: float
    hadrons: bool | None = None
    version: int = MANIFEST_VERSION

    @property
    def run_paths(self) -> list[Path]:
        return [self.path / r.folder for r in self.runs]

    @property
    def repeats_showers(self) -> bool:
        """¿Dos ejecuciones con el mismo par de semillas (las mismas lluvias)? Pasaba con ARGUS 0.13.0 si el caso no
        tenía RSEED o tenía «RSEED 1 1»."""
        pairs = [seed_pair(r.seeds) for r in self.runs]
        known = [p for p in pairs if p is not None]
        return len(known) != len(set(known))


def read_manifest(folder: str | os.PathLike) -> CascadeInfo | None:
    p = Path(folder) / MANIFEST
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("kind") != "cascada":
        return None
    try:
        runs = [CascadeRun(str(r["folder"]), int(r["showers"]), (int(r["seeds"][0]), int(r["seeds"][1])),
                           int(r["groups"]) if r.get("groups") is not None else None)
                for r in data.get("runs", []) if ".." not in Path(str(r["folder"])).parts]
        return CascadeInfo(Path(folder), str(data.get("case", "")), str(data.get("signature", "")),
                           int(data.get("showers", 0)), int(data.get("per_run", 0)),
                           int(data["cap"]) if data.get("cap") is not None else None, str(data.get("penhmain", "")),
                           runs, float(data.get("created", 0.0)), data.get("hadrons"), int(data.get("version", 1)))
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def cascade_matches(info: CascadeInfo, in_path: str | os.PathLike, doc: InDocument,
                    exe: str | os.PathLike | None) -> bool:
    """¿Es la cascada del caso de ahora (mismo .in, geometría, materiales, penhmain y lluvias)?"""
    if info.version >= 2:
        return info.signature in signatures(in_path, doc, exe, info.showers)
    return info.signature == _signature_v1(Path(in_path), doc, exe, info.showers)


def signatures(in_path: str | os.PathLike, doc: InDocument, exe: str | os.PathLike | None, showers: int
               ) -> tuple[str, ...]:
    """La firma de ahora (la primera: la de las cascadas nuevas), la de 0.16.2–0.17.2 y la de 0.16.0–0.16.1
    (:func:`case_signature`): una cascada es del caso con cualquiera. Con un espacio de fases, solo la de ahora (las
    de antes contaban otras lluvias)."""
    new = case_signature(in_path, doc, exe, showers)
    if doc.model.source.from_psf:
        return (new,)
    return (new, case_signature(in_path, doc, exe, showers, dated=True),
            case_signature(in_path, doc, exe, showers, legacy=True))


def psf_counted_in_n(info: CascadeInfo, in_path: str | os.PathLike, doc: InDocument,
                     exe: str | os.PathLike | None) -> bool:
    """¿Es una cascada de este caso con un espacio de fases simulada con ARGUS 0.17.2 o antes, que pedía las lluvias
    en el N de penhmain (con NSHI > 1, menos de las pedidas)?"""
    if info.version < 2 or not doc.model.source.from_psf:
        return False
    return info.signature in (case_signature(in_path, doc, exe, info.showers, dated=True),
                              case_signature(in_path, doc, exe, info.showers, legacy=True))


def find_cascades(case_folder: str | os.PathLike, case: str | None = None) -> list[CascadeInfo]:
    """Las cascadas de una carpeta (de ``case`` si se dice), la más reciente primero."""
    out = []
    try:
        subs = sorted(Path(case_folder).iterdir())
    except OSError:
        return []
    for d in subs:
        if d.is_dir() and d.name.lower().startswith(CASCADE_DIR):
            info = read_manifest(d)
            if info is not None and (case is None or os.path.normcase(info.case) == os.path.normcase(case)):
                out.append(info)
    return sorted(out, key=lambda i: i.created, reverse=True)


def new_cascade_folder(case_folder: str | os.PathLike, *, create: bool = False) -> Path:
    """La primera carpeta libre: «cascada», «cascada-2»… Con ``create``, la crea (de forma atómica: si otra
    cascada del mismo caso o de otro .in de la carpeta empieza a la vez, cada una se queda con la suya)."""
    base = Path(case_folder) / CASCADE_DIR
    k = 1
    while True:
        cand = base if k == 1 else base.with_name(f"{CASCADE_DIR}-{k}")
        if create:
            try:
                cand.mkdir()
                return cand
            except FileExistsError:
                pass
        elif not os.path.lexists(cand):
            return cand
        k += 1


@dataclass
class RunState:
    status: str  # 'pendiente', 'simulando', 'terminada', 'error'
    error: str = ""


def _read_small(p: Path, limit: int = 20000) -> str | None:
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return decode_output(f.read())
    except OSError:
        return None


def _last_lines(text: str | None, n: int = 3) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out = " · ".join(lines[-n:])
    return out if len(out) <= 300 else "…" + out[-299:]


def run_state(run_dir: str | os.PathLike) -> RunState:
    """Estado de una ejecución por sus ficheros (también de una lanzada en otra sesión de ARGUS): su salida en
    pantalla (``penhmain.out``) y cómo ha terminado su proceso (``argus-rc``, que ARGUS deja vacío al lanzarla)."""
    from ..results.folder import log_error

    d = Path(run_dir)
    text = _read_small(d / OUTPUT)
    if text is not None and "*** end ***" in text.lower():
        return RunState("terminada")
    rc = _read_small(d / RC_FILE, 2000)
    rc = rc.strip() if rc is not None else None
    if rc and rc.startswith("lanzar:"):
        return RunState("error", f"no se ha podido lanzar penhmain:{rc[len('lanzar:'):]}")
    if rc == "cancelada":
        return RunState("pendiente")
    err = log_error(text) if text else None
    if not err and rc:
        err = log_error(_read_small(d / LAUNCH_LOG) or "")
    if err:
        return RunState("error", err)
    if rc:  # el proceso ha terminado sin «*** END ***»
        why = _last_lines(text) or _last_lines(_read_small(d / LAUNCH_LOG))
        code = f" (código {rc})" if rc not in ("0", "tiempo") else ""
        if rc == "tiempo":
            return RunState("error", "penhmain no ha terminado a tiempo" + (f": {why}" if why else ""))
        return RunState("error", f"penhmain ha terminado sin acabar la simulación{code}" + (f": {why}" if why else ""))
    if text is None:
        return RunState("pendiente")
    return RunState("simulando")


def run_exact(run_dir: str | os.PathLike, case: str, showers: int) -> bool:
    """¿Se sabe de qué lluvia es cada trayectoria de esta ejecución (con los cortes que tenga)?"""
    from ..results.shower import load_run

    try:
        return load_run(run_dir, in_name=case or None, showers=showers).exact
    except (OSError, ValueError):
        return False


def run_has_tracks(run_dir: str | os.PathLike) -> bool:
    """¿Ha escrito penhmain alguna trayectoria en esta ejecución (algún .trk o ``fort.7x`` con algo)? Sin ninguna, una
    ejecución terminada no sirve: se han borrado o el penhmain no escribe las de LTRACK."""
    from ..results.shower import trk_files

    return any(size > 0 for size in trk_files(run_dir).values())


def cascade_progress(info: CascadeInfo) -> tuple[int, int, list[RunState]]:
    """(lluvias terminadas, lluvias pedidas, estado de cada ejecución). Una ejecución está terminada cuando penhmain
    ha acabado, ha escrito sus trayectorias y se sabe de qué lluvia es cada una (``run_exact``)."""
    states = [run_state(p) for p in info.run_paths]
    counts = run_counts(info)
    done = sum(c for r, c, s, d in zip(info.runs, counts, states, info.run_paths)
               if s.status == "terminada" and run_has_tracks(d) and run_exact(d, info.case, r.showers))
    return done, sum(counts), states


def unverified_showers(info: CascadeInfo) -> int:
    """Lluvias de ejecuciones que penhmain ha terminado pero de las que aún no se sabe de qué lluvia es cada
    trayectoria (faltan cortes)."""
    return sum(c for r, c, d in zip(info.runs, run_counts(info), info.run_paths)
               if run_state(d).status == "terminada" and run_has_tracks(d)
               and not run_exact(d, info.case, r.showers))


def untracked_runs(info: CascadeInfo) -> list[str]:
    """Las ejecuciones que penhmain ha terminado sin escribir ninguna trayectoria (``run_has_tracks``)."""
    return [r.folder for r, d in zip(info.runs, info.run_paths)
            if run_state(d).status == "terminada" and not run_has_tracks(d)]


# ---------------------------------------------------------------------------------------------------------------------
# Procesos (también los de otra sesión de ARGUS)
# ---------------------------------------------------------------------------------------------------------------------

def _read_pid(run_dir: Path) -> int | None:
    try:
        pid = int((Path(run_dir) / PID_FILE).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _write_control(path: Path, text: str) -> None:
    """Los ficheros de control de ARGUS en la carpeta de una ejecución (el PID, cómo ha terminado): se reescriben en
    cada lanzamiento."""
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _windows_process(pid: int, access: int):  # pragma: no cover - solo en Windows
    import ctypes

    return ctypes.windll.kernel32.OpenProcess(access, False, int(pid))


def _windows_alive(pid: int, run_dir: Path) -> bool:  # pragma: no cover - solo en Windows
    """¿Sigue el proceso ``pid`` y es un programa de la carpeta de la ejecución (no otro con el PID reutilizado)?"""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    h = _windows_process(pid, 0x1000)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return False
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            image = os.path.normcase(os.path.abspath(os.path.dirname(buf.value)))
            return image == os.path.normcase(os.path.abspath(str(run_dir)))
        return True
    finally:
        k32.CloseHandle(h)


def _wsl_same_folder(pid: int) -> str:
    """La orden de sh (sin comillas: wsl.exe las pasaría mal) que comprueba, dentro de WSL, que el proceso ``pid``
    sigue y trabaja en la carpeta de la ejecución (la de «--cd»), como en Linux con /proc/<pid>/cwd: tras un cierre de
    golpe de ARGUS, ese PID puede ser ya de otro proceso de Linux (0.16.3). Uno que ha terminado (zombi) no tiene
    carpeta de trabajo."""
    return f"[ /proc/{int(pid)}/cwd -ef . ]"


def _wsl_run(run_dir: Path, script: str) -> int:
    # «--cd» quiere una ruta completa de Windows (WSL la traduce a la suya).
    r = subprocess.run([str(wsl_executable() or "wsl.exe"), "--cd", os.path.abspath(run_dir), "-e", "sh", "-c", script],
                       timeout=20, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=NO_WINDOW)
    return r.returncode


def pid_alive(run_dir: str | os.PathLike, launch: Launch) -> bool:
    """¿Sigue en marcha el penhmain de esta ejecución (el del PID apuntado, en esta carpeta)?"""
    run_dir = Path(run_dir)
    pid = _read_pid(run_dir)
    if pid is None:
        return False
    try:
        if launch.how == "wsl":
            return _wsl_run(run_dir, _wsl_same_folder(pid)) == 0
        if os.name == "nt":
            return _windows_alive(pid, run_dir)
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):  # no existe, o es de otro usuario (no es el nuestro)
        return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    proc = Path(f"/proc/{pid}")
    if proc.is_dir():  # Linux: que no haya terminado ya y trabaje en la carpeta de la ejecución (no otro con el PID
        try:          # reutilizado)
            if (proc / "stat").read_text().rsplit(")", 1)[-1].split()[0] in ("Z", "X"):
                return False
            return os.path.samefile(proc / "cwd", run_dir)
        except (OSError, IndexError):
            return True
    return True


def kill_pid(run_dir: str | os.PathLike, launch: Launch) -> None:
    """Para el penhmain de una ejecución por su PID (una de otra sesión de ARGUS, o la que lanza WSL)."""
    run_dir = Path(run_dir)
    pid = _read_pid(run_dir)
    if pid is None:
        return
    try:
        if launch.how == "wsl":
            # Solo si es el de la ejecución (sigue en su carpeta): no otro proceso de Linux con el PID reutilizado.
            _wsl_run(run_dir, f"{_wsl_same_folder(pid)} && kill -9 {int(pid)}")
        elif os.name == "nt":  # pragma: no cover - solo en Windows
            import ctypes

            if _windows_alive(pid, run_dir):
                h = _windows_process(pid, 0x0001)  # PROCESS_TERMINATE
                if h:
                    ctypes.windll.kernel32.TerminateProcess(h, 1)
                    ctypes.windll.kernel32.CloseHandle(h)
        elif pid_alive(run_dir, launch):
            try:
                os.killpg(pid, 9)
            except OSError:
                os.kill(pid, 9)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---------------------------------------------------------------------------------------------------------------------
# El espacio de fases de entrada (IPSFN)
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class PsfShower:
    """Una lluvia del espacio de fases: su número (N de penhmain: la suma de los NSHI) y sus partículas."""

    n: int
    lines: list[str]


def psf_record(line: str) -> tuple[int, float, float, float, float, int, float] | None:
    """(KPAR, E, X, Y, Z, NSHI, WGHT) de una línea del espacio de fases, o None si penhmain la salta (comentario,
    línea en blanco o que no se puede leer: ``READ(…,*,ERR=…) KPAR,E,X,Y,Z,U,V,W,WGHT,ILB(1:4),NSHI`` en RDPSF).
    penhmain lee los primeros 136 caracteres de cada línea (``CHARACTER PSFR*136``): lo de después no existe para
    él."""
    s = line.rstrip("\r\n")[:PSF_RECORD].strip()
    if not s or s.startswith("#"):
        return None
    tok = [t for t in re.split(r"[\s,]+", s) if t]
    if len(tok) < 14:
        return None
    try:
        kpar = int(tok[0])
        e, x, y, z, _u, _v, _w, wght = (float(t.replace("D", "E").replace("d", "e")) for t in tok[1:9])
        _ilb = [int(t) for t in tok[9:13]]
        nshi = int(tok[13])
    except ValueError:
        return None
    return kpar, e, x, y, z, nshi, wght


def psf_window(doc: InDocument) -> tuple[float, float, int]:
    """(WGMIN, WGMAX, NSPLIT) con que penhmain trata las partículas del espacio de fases (como en PMRDR0: WGTWIN
    0,5 y 1,5 por defecto, aunque su ayuda diga otra cosa; IPSPLI 1). Con un peso menor que WGMIN, ruleta rusa (la
    partícula puede no simularse); mayor que WGMAX, se divide en hasta NSPLIT copias del mismo punto."""
    wmin, wmax, nsplit = 0.5, 1.5, 1
    v = sc.value(doc, "WGTWIN")
    try:
        if v and len(v) >= 2 and v[0] is not None and v[1] is not None:
            wmin, wmax = abs(float(v[0])), min(abs(float(v[1])), 1.0e10)
    except (TypeError, ValueError):
        pass
    v = sc.value(doc, "IPSPLI")
    try:
        if v and v[0] is not None:
            n = int(v[0])
            nsplit = n if 1 < n <= 1000 else min(1000, abs(n))
    except (TypeError, ValueError):
        pass
    return wmin, wmax, nsplit


def psf_one_each(weights, window: tuple[float, float, int]) -> bool:
    """¿Sale de cada partícula del espacio de fases exactamente una trayectoria (sin ruleta rusa ni copias)?"""
    wmin, wmax, nsplit = window
    for w in weights:
        if w < wmin:
            return False
        if w > wmax and wmin > 0 and min(nsplit, int(min(w / wmin, 2.0e9))) > 1:
            return False
    return True


def read_psf_showers(paths: list[Path], up_to: int | None = None, *, groups: int | None = None
                     ) -> list[PsfShower]:
    """Las lluvias de los espacios de fases en el orden en que las lee penhmain (un fichero detrás de otro), hasta la
    primera con N ≥ ``up_to`` (incluida) o, con ``groups``, las ``groups`` primeras (sin límite si no se dice
    ninguno). Una lluvia es una partícula con NSHI > 0 y las que la siguen con NSHI 0."""
    out: list[PsfShower] = []
    n = 0
    cur: PsfShower | None = None
    for p in paths:
        try:
            f = open(p, encoding="latin-1", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                rec = psf_record(line)
                if rec is None:
                    continue
                nshi = rec[5]
                if cur is None or nshi > 0:
                    if cur is not None and ((up_to is not None and cur.n >= up_to)
                                            or (groups is not None and len(out) >= groups)):
                        return out
                    n += max(nshi, 0)
                    cur = PsfShower(n, [])
                    out.append(cur)
                cur.lines.append(line.rstrip("\r\n"))
    return out


def split_psf(showers: list[PsfShower], cap: int | None) -> list[list[PsfShower]]:
    """Las lluvias (grupos) del espacio de fases de cada ejecución, en orden: las siguientes mientras quepan sus
    trayectorias. penhmain deja de escribirlas al acabar una lluvia con N > NPSHOW (``cap``), y N, contando desde
    la última lluvia de la ejecución anterior (el trozo empieza con el NSHI de su primera partícula, tal cual), sube
    con el NSHI de cada grupo, que salta las lluvias de la simulación que no llegaron al fichero: cada ejecución
    tiene la siguiente mientras la anterior acabe con N ≤ ``cap`` y, como mucho, ``cap`` lluvias (None: todas en
    una)."""
    out: list[list[PsfShower]] = []
    cap = max(int(cap), 1) if cap else None
    i = 0
    while i < len(showers):
        prev = showers[i - 1].n if i else 0
        j = i + 1
        while j < len(showers) and (cap is None or (showers[j - 1].n - prev <= cap and j - i < cap)):
            j += 1
        out.append(showers[i:j])
        i = j
    return out


def psf_nsimsh(parts: list[list[PsfShower]], k: int) -> int:
    """NSIMSH de la ejecución ``k`` (el N de penhmain al acabar su última lluvia, contando desde la anterior; al
    menos 1: con 0, penhmain simularía 2·10⁹)."""
    prev = parts[k - 1][-1].n if k else 0
    return max(1, parts[k][-1].n - prev)


def run_psf_showers(folder: str | os.PathLike, in_name: str, showers: int | None = None) -> list[PsfShower] | None:
    """Las lluvias del espacio de fases que lee una ejecución con su .in (su trozo o, en las cascadas de 0.17.2 y
    antes, la primera, el fichero del caso), hasta la primera con N ≥ ``showers``. None si su fuente no es un
    espacio de fases o no se puede leer el .in."""
    from .inputfile import parse_in_file

    folder = Path(folder)
    try:
        doc = parse_in_file(folder / in_name)
    except (OSError, ValueError):
        return None
    src = doc.model.source
    if not src.from_psf:
        return None
    paths = []
    for n in src.psf:
        n = n.strip()
        if not n:
            continue
        absolute = PureWindowsPath(n).is_absolute() or n.startswith("/")
        paths.append(Path(n) if absolute else folder / n.replace("\\", "/"))
    return read_psf_showers(paths, int(showers) if showers else None)


_COUNTS: dict[str, tuple[tuple, list[int]]] = {}


def run_counts(info: CascadeInfo) -> list[int]:
    """Las lluvias de cada ejecución como las cuenta el visor (:attr:`CascadeRun.count`). En los índices de 0.17.2 y
    antes no estaban: con un espacio de fases, son los grupos que lee cada ejecución (su ``showers`` era el N de
    penhmain); si no, ``showers``."""
    if all(r.groups is not None for r in info.runs):
        return [r.groups for r in info.runs]
    key = str(info.path)
    stamp = (tuple((r.folder, r.showers) for r in info.runs), info.case)
    hit = _COUNTS.get(key)
    if hit is not None and hit[0] == stamp:
        return list(hit[1])
    out = [r.showers for r in info.runs]
    first = run_psf_showers(info.run_paths[0], info.case, 1) if info.runs and info.case else None
    if first is not None:  # (todas leen un espacio de fases o ninguna: son del mismo caso)
        for k, (r, d) in enumerate(zip(info.runs, info.run_paths)):
            if r.groups is not None:
                out[k] = r.groups
                continue
            found = run_psf_showers(d, info.case, r.showers)
            if found is not None:
                out[k] = len(found)
    if len(_COUNTS) > 50:
        _COUNTS.clear()
    _COUNTS[key] = (stamp, out)
    return list(out)


def _psf_chunk(sel: list[PsfShower], first: int, last: int, names: list[str]) -> bytes:
    """El trozo del espacio de fases de una ejecución (o de un corte): sus lluvias, con las líneas tal cual (en
    latin-1, como se leen: byte a byte). La cabecera, en latin-1 (con un guion largo, que no lo es, salía
    «lluvias 51?100»: 0.17.3)."""
    shown = ", ".join(n.strip() for n in names)
    head = (f"# ARGUS (cascada): las lluvias con N = {first} a {last} del espacio de fases {shown} (las líneas, "
            "tal cual).\n")
    body = "\n".join(ln for s in sel for ln in s.lines) + "\n"
    return (head + body).encode("latin-1", errors="replace")


# ---------------------------------------------------------------------------------------------------------------------
# Cortes: dónde acaba cada lluvia en los .trk (ver el principio del módulo)
# ---------------------------------------------------------------------------------------------------------------------

_CUTS_LOCK = threading.Lock()


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(p: Path, data: dict) -> None:
    """Escribe un fichero de control de ARGUS de la cascada entero o nada (temporal y cambio de nombre)."""
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def record_cut(run_dir: str | os.PathLike, b: int, points: dict[int, int] | None, error: str = "") -> None:
    """Apunta en ``argus-cortes.json`` de la ejecución lo que ha dicho el corte tras ``b`` lluvias (los puntos que había
    escrito penhmain de cada tipo de partícula) o por qué no ha servido. Si los .trk de la ejecución han cambiado desde
    los cortes anteriores, estos ya no valen."""
    from ..results.shower import CUTS_FILE, trk_files

    run_dir = Path(run_dir)
    with _CUTS_LOCK:
        p = run_dir / CUTS_FILE
        files = trk_files(run_dir)
        data = _read_json(p)
        if data.get("kind") != "cortes" or data.get("files") != files:
            data = {"kind": "cortes", "version": 1, "files": files, "cuts": {}, "failed": {}}
        data.setdefault("cuts", {})
        data.setdefault("failed", {})
        if points is not None:
            data["cuts"][str(int(b))] = {str(int(k)): int(v) for k, v in points.items()}
            data["failed"].pop(str(int(b)), None)
        else:
            data["failed"][str(int(b))] = error or "no ha servido"
        try:
            _write_json(p, data)
        except OSError:
            pass


def failed_cuts(run_dir: str | os.PathLike) -> dict[int, str]:
    """Los cortes de una ejecución que no han servido (y por qué), si son de sus trayectorias de ahora."""
    from ..results.shower import CUTS_FILE, trk_files

    data = _read_json(Path(run_dir) / CUTS_FILE)
    if data.get("kind") != "cortes" or data.get("files") != trk_files(run_dir):
        return {}
    try:
        return {int(b): str(t) for b, t in dict(data.get("failed", {})).items()}
    except (TypeError, ValueError):
        return {}


def _is_prefix(part: Path, whole: Path) -> bool:
    try:
        if part.stat().st_size > whole.stat().st_size:
            return False
        with open(part, "rb") as a, open(whole, "rb") as w:
            while True:
                x = a.read(1 << 20)
                if not x:
                    return True
                if w.read(len(x)) != x:
                    return False
    except OSError:
        return False


def check_cut(run_dir: str | os.PathLike, cut_dir: str | os.PathLike) -> tuple[dict[int, int] | None, str]:
    """Lo que dice un corte: cuántos puntos había escrito penhmain de cada tipo de partícula (su ``fort.7x`` y su
    .trk). Sus ficheros tienen que ser el principio exacto de los de la ejecución entera; si no, (None, por qué)."""
    from ..results.shower import FORT_UNITS, stream_points, trk_files

    run_dir, cut_dir = Path(run_dir), Path(cut_dir)
    whole = trk_files(run_dir)
    part = trk_files(cut_dir)
    for name, size in part.items():
        if name not in whole:
            if size:
                return None, f"{name} no está en la ejecución entera"
            continue
        if not _is_prefix(cut_dir / name, run_dir / name):
            return None, (f"{name} no es el principio del de la ejecución entera: penhmain no ha repetido las mismas "
                          "trayectorias con las mismas semillas")
    # Si el corte ya había abierto el .trk de un tipo, su fort.7x tiene que estar entero.
    from ..results.data import PARTICLE_FILE_WORDS

    for unit, kp in FORT_UNITS.items():
        trk = [n for n, k in PARTICLE_FILE_WORDS.items() if k == kp]
        opened = any(part.get(f"{w}.trk", 0) for w in trk)
        if opened and part.get(f"fort.{unit}", 0) != whole.get(f"fort.{unit}", 0):
            return None, f"fort.{unit} no está entero aunque ya se escribía su .trk"
    return stream_points(cut_dir), ""


def cut_nsimsh(run_dir: str | os.PathLike, case: str, showers: int, b: int) -> float | None:
    """NSIMSH para parar tras ``b`` lluvias: b o, con un espacio de fases, el N de penhmain al acabar su lluvia b."""
    from ..results.shower import run_source

    src = run_source(run_dir, case, showers)
    if src is not None and src.psf_n is not None:
        return float(src.psf_n[b - 1]) if 0 < b <= len(src.psf_n) else None
    return float(b)


def cut_psf(run_dir: str | os.PathLike, case: str, showers: int, b: int, folder: str = "") -> bytes | None:
    """Con un espacio de fases, el trozo que lee el corte que para tras la lluvia ``b``: las lluvias de la ejecución
    hasta esa, nada más. Con NSIMSH no basta: si la ruleta rusa (WGTWIN) mata la partícula de la lluvia b, penhmain
    sigue leyendo en la misma lluvia y para más allá (undécima revisión). None si la fuente no es un espacio de
    fases (o no tiene la lluvia b)."""
    found = run_psf_showers(run_dir, case, showers)
    if found is None or not 0 < b <= len(found):
        return None
    return _psf_chunk(found[:b], found[0].n, found[b - 1].n, [f"de {folder}" if folder else "de la ejecución"])


def write_cut_folder(run_dir: Path, cut_dir: Path, items, in_name: str, exe_name: str, nsimsh: float,
                     psf: bytes | None = None) -> None:
    """La carpeta de un corte: enlaces a lo que lee penhmain en la carpeta de la ejecución, su .in con NSIMSH =
    ``nsimsh`` (lo demás, igual) y el guion. ``psf``: con un espacio de fases, el trozo que lee el corte (las lluvias
    de la ejecución hasta la b; ver :func:`cut_psf`)."""
    for it in items:
        if it.kind in ("in", "txt", "ctjson") or it.status in ("", "absolute", "missing", "outside", "failed"):
            continue
        if psf is not None and it.kind == "psf":
            continue
        name = it.name.replace("\\", "/")
        src = run_dir / name
        if not src.is_file():
            src = it.source
        if src is None:
            continue
        target = cut_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        link_new(src, target, executable=it.kind == "exe")
    d = parse_in_text((run_dir / in_name).read_text(encoding="latin-1"), str(cut_dir / in_name))
    if psf is not None:
        write_new(cut_dir / PSF_CHUNK, psf)
        sc.set_psf_files(d, [PSF_CHUNK])
    elif (run_dir / PSF_CHUNK).is_file():
        link_new(run_dir / PSF_CHUNK, cut_dir / PSF_CHUNK)
    sc.set_value(d, "NSIMSH", [float(nsimsh)])
    write_new(cut_dir / in_name, write_in_text(d).encode("latin-1", errors="replace"))
    write_new(cut_dir / RUN_SCRIPT, _run_script(exe_name, in_name), executable=True)


# ---------------------------------------------------------------------------------------------------------------------
# Preparar las carpetas
# ---------------------------------------------------------------------------------------------------------------------

def link_new(src: str | os.PathLike, target: str | os.PathLike, *, executable: bool = False) -> bool:
    """Enlace duro de ``src`` como ``target`` (no ocupa más) o, si no se puede (otro disco, FAT…), una copia.
    Nunca sobrescribe: False si ``target`` ya existe."""
    target = Path(target)
    if os.path.lexists(target):
        return False
    try:
        os.link(src, target)
        return True
    except FileExistsError:
        return False
    except OSError:
        return copy_new(src, target, executable=executable)


def _run_script(exe_name: str, in_name: str) -> bytes:
    def q(s: str) -> str:
        return "'" + s.replace("'", "'\\''") + "'"

    return ("#!/bin/sh\n# Generado por ARGUS: una ejecución de la cascada (las trayectorias de unas lluvias).\n"
            f"echo $$ > {PID_FILE}\n"
            f"chmod +x ./{q(exe_name)} 2>/dev/null\n"
            f"exec ./{q(exe_name)} < {q(in_name)} > {OUTPUT} 2>&1\n").encode()


def _lower_first(text: str) -> str:
    """«Material» → «material», «Datos ENDF» → «datos ENDF», «TAC (vóxeles)» se queda."""
    return text[:1].lower() + text[1:] if len(text) > 1 and text[1:2].islower() else text


@dataclass
class CascadePlan:
    in_path: Path
    folder: Path
    exe: Path
    showers: int
    cap: int | None
    runs: list[CascadeRun]
    docs: list[InDocument]
    signature: str
    hadrons: bool | None
    files: list[SimPlan] = field(default_factory=list)
    chunks: list[bytes | None] = field(default_factory=list)  # el trozo del espacio de fases de cada ejecución
    notes: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        out = []
        for it in (self.files[0].problems if self.files else []):
            if it.status == "outside":
                out.append(f"{it.name} ({_lower_first(it.kind_text)}): la ruta sube de carpeta o depende de la unidad "
                           "y la cascada no puede cambiarla (cópielo junto al .in y cítelo solo con su nombre)")
            elif it.status == "blank":
                out.append(it.lead_problem)
            elif it.status == "winpath":
                out.append(f"{it.name} ({_lower_first(it.kind_text)}): {it.status_text}")
            elif it.status == "failed":
                out.append(f"no se ha podido preparar {it.name}" + (f" ({it.note})" if it.note else ""))
            else:
                out.append(f"no se encuentra {it.name} ({_lower_first(it.kind_text)}"
                           + (f": {it.note})" if it.note else ")"))
        return out


def _inside_names(in_path: Path, doc: InDocument, database,
                  endf_dir=None) -> tuple[InDocument, dict[str, Path], SimPlan]:
    """Los ficheros que el .in cita con una ruta que sube de carpeta («MFNAME ../mats/agua.mat»): desde la carpeta
    de una ejecución apuntaría a otro sitio. Se les da su nombre (otro si ya lo tiene otro fichero) en el .in y se
    enlazan con él. Devuelve (el .in así, {nombre nuevo en minúsculas: fichero}, lo que lee el caso)."""
    plan = plan_folder(in_path, doc, in_path.parent, database=database, endf_dir=endf_dir)
    d = parse_in_text(write_in_text(doc), doc.path)
    taken = {it.name.replace("\\", "/").lower() for it in plan.items if it.status != "up"}
    sources: dict[str, Path] = {}
    for it in plan.items:
        kw = INPUT_NAMES.get(it.kind)
        if it.status != "up" or kw is None or it.source is None:
            continue
        new = _unique_name(_base_name(it.name), taken)
        taken.add(new.lower())
        old = it.name.replace("\\", "/").lower()
        for ln in d.find(sc.kw6(kw)):
            if (ln.text or "").strip().replace("\\", "/").lower() == old:
                d.modify(ln, text=new)
        sources[new.lower()] = it.source
    return d, sources, plan


def plan_cascade(in_path: str | os.PathLike, doc: InDocument, exe: str | os.PathLike, *, showers: int = DEFAULT_SHOWERS,
                 cap: int | None = NPSHOW_DEFAULT, database: str | os.PathLike | None = None,
                 folder: str | os.PathLike | None = None, signature: str | None = None,
                 endf_dir: str | os.PathLike | None = None) -> CascadePlan:
    """Las ejecuciones que hacen falta para ``showers`` lluvias si cada una guarda ``cap`` (None: sin límite)."""
    in_path = Path(in_path)
    showers = int(min(max(showers, 1), SHOWER_LIMIT))
    base, sources, case_files = _inside_names(in_path, doc, database, endf_dir)
    # Los nombres, como los entiende el penhmain que se ejecutará (uno de Linux o de macOS, también con WSL, no
    # entiende las rutas de Windows: 0.16.3).
    linux = exe_info(exe).system in ("linux", "macos", "script")
    per = runs_for(showers, cap if cap else showers)
    groups = list(per)
    psf_names = [n for n in doc.model.source.psf if n.strip()] if doc.model.source.from_psf else []
    psf_parts: list[list[PsfShower]] | None = None
    notes: list[str] = []
    if psf_names:
        # Las lluvias pedidas son grupos del fichero (las que se ven): cada ejecución, las que le caben (split_psf) y
        # NSIMSH, el N de penhmain al acabar la última.
        by_name = {it.name.replace("\\", "/").lower(): it.source for it in case_files.items if it.kind == "psf"}
        paths = [by_name.get(n.strip().replace("\\", "/").lower()) for n in psf_names]
        found = read_psf_showers([p for p in paths if p is not None], groups=showers)
        if found:
            psf_parts = split_psf(found, cap)
            per = [psf_nsimsh(psf_parts, k) for k in range(len(psf_parts))]
            groups = [len(part) for part in psf_parts]
            if len(found) < showers:
                notes.append(f"El espacio de fases solo tiene {plural(len(found), 'lluvia')}: la cascada, también.")
    seeds = run_seeds(case_seeds(doc), len(per))
    folder = Path(folder) if folder is not None else new_cascade_folder(in_path.parent)
    width = max(2, len(str(len(per))))
    runs = [CascadeRun(f"run-{i + 1:0{width}d}", n, s, g) for i, (n, s, g) in enumerate(zip(per, seeds, groups))]
    docs, chunks = [], []
    for i, r in enumerate(runs):
        d = cascade_doc(base, r.showers, r.seeds, exact=i == 0)
        chunk = None
        if psf_parts is not None:  # (también la primera: ver el principio del módulo)
            prev = psf_parts[i - 1][-1].n if i else 0
            chunk = _psf_chunk(psf_parts[i], prev + 1, psf_parts[i][-1].n, psf_names)
            sc.set_psf_files(d, [PSF_CHUNK])
        docs.append(d)
        chunks.append(chunk)
    plan = CascadePlan(in_path, folder, Path(exe), showers, cap, runs, docs,
                       signature or case_signature(in_path, doc, exe, showers), hadron_data_needed(doc, database),
                       chunks=chunks, notes=notes)
    for r, d, chunk in zip(runs, docs, chunks):
        fp = plan_folder(in_path, d, folder / r.folder, database=database, penhmain=exe, sources=sources,
                         endf_dir=endf_dir, linux=linux)
        fp.items = [it for it in fp.items if it.kind not in ("in", "txt", "ctjson")
                    and not (chunk is not None and it.kind == "psf")]
        plan.files.append(fp)
    return plan


def write_cascade(plan: CascadePlan, cancel: Callable[[], bool] | None = None) -> None:
    """Crea el índice y las carpetas de las ejecuciones con sus ficheros (enlaces), su .in y el guion. Primero el
    índice: si se cancela a medias, la cascada se reconoce y se completa después (lo que ya está no se toca: nada
    se sobrescribe)."""
    plan.folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "kind": "cascada", "version": MANIFEST_VERSION, "case": plan.in_path.name, "signature": plan.signature,
        "showers": plan.showers, "per_run": max((r.showers for r in plan.runs), default=0), "cap": plan.cap,
        "penhmain": plan.exe.name, "hadrons": plan.hadrons, "created": time.time(),
        "runs": [{"folder": r.folder, "showers": r.showers, "seeds": list(r.seeds), "groups": r.count}
                 for r in plan.runs],
    }
    write_new(plan.folder / MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8"))
    chunks = plan.chunks or [None] * len(plan.runs)
    for r, d, fp, chunk in zip(plan.runs, plan.docs, plan.files, chunks):
        if cancel is not None and cancel():
            return
        rd = plan.folder / r.folder
        rd.mkdir(parents=True, exist_ok=True)
        for it in fp.items:
            if it.status != "copy" or it.source is None:
                continue
            target = rd / it.name.replace("\\", "/")
            target.parent.mkdir(parents=True, exist_ok=True)
            link_new(it.source, target, executable=it.kind == "exe")
        if chunk is not None:
            write_new(rd / PSF_CHUNK, chunk)
        write_new(rd / plan.in_path.name, write_in_text(d).encode("latin-1", errors="replace"))
        write_new(rd / RUN_SCRIPT, _run_script(plan.exe.name, plan.in_path.name), executable=True)


# ---------------------------------------------------------------------------------------------------------------------
# Ejecutar
# ---------------------------------------------------------------------------------------------------------------------

def run_argv(run_dir: Path, exe_name: str, in_name: str, launch: Launch, host: str = HOST) -> tuple[list[str], dict]:
    """La orden de una ejecución en segundo plano y cómo lanzarla (entrada y salida, si no las pone el guion)."""
    if host == "windows" and launch.how == "native":
        return [str(run_dir / exe_name)], {"stdin": in_name, "stdout": OUTPUT}
    if launch.how == "wsl":
        return [str(wsl_executable() or "wsl.exe"), "--cd", str(run_dir), "-e", "sh", RUN_SCRIPT], {}
    return ["sh", RUN_SCRIPT], {}


def available_memory() -> int | None:
    """Memoria libre del ordenador (bytes), o None si no se sabe."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullAvailPhys)
            return None
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as f:
                for ln in f:
                    if ln.startswith("MemAvailable:"):
                        return int(ln.split()[1]) * 1024
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_AVPHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and size > 0:
                return int(pages * size)
    except (OSError, ValueError, AttributeError):
        return None
    return None


def parallel_jobs(n_runs: int, hadrons: bool | None, cpus: int | None = None, memory: int | None = None) -> int:
    """Cuántas ejecuciones a la vez: deja dos procesadores libres (la simulación y la interfaz) y no pasa de la
    memoria libre (unos 0,9 GB cada una con los datos de hadrones; 0,25 GB sin ellos)."""
    cpus = cpus if cpus is not None else (os.cpu_count() or 2)
    jobs = max(1, cpus - 2)
    mem = memory if memory is not None else available_memory()
    if mem:
        per = 0.9e9 if hadrons is not False else 0.25e9
        jobs = min(jobs, max(1, int(0.7 * mem / per)))
    return max(1, min(jobs, n_runs, 16))


@dataclass
class JobStatus:
    stage: str  # 'sonda', 'preparando', 'simulando', 'terminada', 'cancelada', 'error'
    text: str
    runs: list[RunState] = field(default_factory=list)
    showers_done: int = 0
    showers_total: int = 0
    folder: Path | None = None
    cap: int | None = None
    error: str = ""
    started: float = 0.0
    finished: float = 0.0
    cuts_done: int = 0  # cortes hechos (ejecuciones cortas para saber de qué lluvia es cada trayectoria)
    cuts_total: int = 0


def _showers_text(done: int, total: int) -> str:
    """«3 de 100 lluvias», «0 de 1 lluvia»."""
    return f"{fmt_int(done)} de {fmt_int(total)} {'lluvia' if total == 1 else 'lluvias'}"


class CascadeJob:
    """Simula una cascada en segundo plano (hilos y procesos; sin Qt): sonda de NPSHOW si hace falta, carpetas y
    ejecuciones en paralelo. ``status`` dice cómo va; ``cancel`` lo para todo."""

    def __init__(self, in_path: str | os.PathLike, doc: InDocument, exe: str | os.PathLike, launch: Launch, *,
                 showers: int = DEFAULT_SHOWERS, cap: int | None = None, database: str | os.PathLike | None = None,
                 jobs: int | None = None, host: str = HOST, on_cap: Callable[[int | None], None] | None = None,
                 reuse: bool = True, endf_dir: str | os.PathLike | None = None):
        self.in_path = Path(in_path)
        self.doc = parse_in_text(write_in_text(doc), doc.path)
        self.exe = Path(exe)
        self.launch = launch
        self.showers = int(min(max(showers, 1), SHOWER_LIMIT))
        self.cap = cap
        self.database = database
        self.endf_dir = endf_dir  # la carpeta de los ENDF del usuario (si no, database/ENDF_data)
        self.jobs = jobs
        self.host = host
        self.on_cap = on_cap
        self.reuse = reuse
        self.plan: CascadePlan | None = None
        self.info: CascadeInfo | None = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._procs: dict[int, tuple[subprocess.Popen, Path]] = {}
        self._others: set[int] = set()  # ejecuciones en marcha desde otra sesión de ARGUS
        self._thread: threading.Thread | None = None
        # Cortes (ver el principio del módulo), por ejecución: si ya se sabe la lluvia de cada trayectoria, los que
        # faltan, cuántas vueltas van, los que no han servido (en esta sesión) y por qué no se ha podido saber.
        self._exact: dict[int, bool] = {}
        self._cuts_left: dict[int, set[int]] = {}
        self._rounds: dict[int, int] = {}
        self._cut_failed: dict[int, dict[int, str]] = {}
        self._cut_error: dict[int, str] = {}
        self._cuts_total = 0
        self._cuts_done = 0
        self._cuts_running = 0
        self._status = JobStatus("preparando", "Preparando la cascada…", showers_total=self.showers,
                                 started=time.time())

    # -- estado ------------------------------------------------------------------------------------------------
    @property
    def status(self) -> JobStatus:
        with self._lock:
            st = self._status
            return JobStatus(st.stage, st.text, list(st.runs), st.showers_done, st.showers_total, st.folder,
                             st.cap, st.error, st.started, st.finished, st.cuts_done, st.cuts_total)

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self._status, k, v)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- control -----------------------------------------------------------------------------------------------
    def start(self) -> CascadeJob:
        self._thread = threading.Thread(target=self._main, name="argus-cascada", daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.running

    def cancel(self, block: bool = False) -> None:
        """Para la cascada. Cada ejecución para su proceso en su hilo (en una décima de segundo; con WSL, al apuntar
        este su PID, unos segundos si acaba de arrancar): no se espera aquí, que se llama desde la interfaz. Con
        ``block`` (al cerrar ARGUS) se paran todos antes de volver."""
        self._cancel.set()
        if not block:
            return
        with self._lock:
            procs = list(self._procs.items())
        threads = [threading.Thread(target=self._kill, args=(i, p, d), daemon=True) for i, (p, d) in procs]
        for th in threads:
            th.start()
        for th in threads:
            th.join(PID_WAIT + 5)

    def _kill(self, i: int, p: subprocess.Popen, run_dir: Path) -> None:
        if p.poll() is not None:
            return
        if self.launch.how == "wsl":
            # wsl.exe no es penhmain: hay que parar el proceso de Linux, por el PID que apunta el guion (ARGUS deja
            # vacío el fichero al lanzarlo: se espera a que WSL arranque y lo escriba).
            t0 = time.monotonic()
            while _read_pid(run_dir) is None and p.poll() is None and time.monotonic() - t0 < PID_WAIT:
                time.sleep(0.2)
            kill_pid(run_dir, self.launch)
        try:
            if os.name != "nt":
                os.killpg(p.pid, 9)
            else:
                p.kill()
        except (OSError, ProcessLookupError):
            try:
                p.kill()
            except OSError:
                pass

    # -- trabajo -----------------------------------------------------------------------------------------------
    def _main(self) -> None:
        try:
            self._work()
        except Exception as exc:  # noqa: BLE001 - se enseña en la interfaz
            self._set(stage="error", text=f"La cascada no ha podido simularse: {str(exc).rstrip('.')}.",
                      error=str(exc), finished=time.time())

    def _work(self) -> None:
        if self.launch.pending:
            # Aún no se sabía si funciona WSL: se le pregunta aquí y no en la interfaz (la primera vez tarda lo que
            # tarde en arrancar; 0.16.3).
            self._set(stage="preparando", text="Comprobando si funciona WSL (el Linux de Windows)…")
            launch = launch_for(exe_info(self.exe), host=self.host)
            if not launch.ok:
                raise RuntimeError(f"penhmain no se puede ejecutar en este ordenador: {launch.note}")
            self.launch = launch
        sigs = signatures(self.in_path, self.doc, self.exe, self.showers)
        sig = sigs[0]
        if self.reuse:
            for info in find_cascades(self.in_path.parent, self.in_path.name):
                if info.version >= MANIFEST_VERSION and info.signature in sigs:
                    done, total, states = cascade_progress(info)
                    self.info = info
                    if done >= total:
                        self._clean_cuts(info.path)  # (las de una sesión que se cerró de golpe mientras cortaba)
                        self._set(stage="terminada", text=f"Cascada ya simulada: {plural(done, 'lluvia')} "
                                                          f"({info.path.name})." + psf_short_note(total, info.showers),
                                  runs=states, showers_done=done, showers_total=total, folder=info.path,
                                  cap=info.cap, finished=time.time())
                        return
                    # A medias (se cerró ARGUS, se canceló…): se completan las carpetas (si se canceló al
                    # prepararlas) y se terminan las ejecuciones que faltan, en la misma carpeta.
                    self.plan = plan_cascade(self.in_path, self.doc, self.exe, showers=self.showers, cap=info.cap,
                                             database=self.database, folder=info.path, signature=info.signature,
                                             endf_dir=self.endf_dir)
                    simulated = all(st.status == "terminada" for st in states)
                    self._set(stage="preparando", cap=info.cap,
                              text="Comprobando de qué lluvia es cada trayectoria…" if simulated
                              else "Completando la cascada a medias…")
                    write_cascade(self.plan, cancel=self._cancel.is_set)
                    self._run_all(skip_done=True)
                    return
        cap = self.cap
        # Antes de la prueba, que no falte nada (si no, el error sería el de Fortran al abrir el fichero).
        first = plan_cascade(self.in_path, self.doc, self.exe, showers=self.showers, cap=cap or NPSHOW_DEFAULT,
                             database=self.database, folder=new_cascade_folder(self.in_path.parent), signature=sig,
                             endf_dir=self.endf_dir)
        if first.problems:
            raise RuntimeError("; ".join(first.problems))
        if cap is None:
            self._set(stage="sonda", text="Comprobando cuántas lluvias guarda este penhmain (una lluvia de "
                                          "prueba)…")
            cap = self._probe()
            if self._cancel.is_set():
                self._set(stage="cancelada", text="Cascada cancelada.", finished=time.time())
                return
            if self.on_cap is not None:
                self.on_cap(cap)
        self._set(stage="preparando", text="Preparando las carpetas de la cascada…", cap=cap)
        folder = new_cascade_folder(self.in_path.parent, create=True)
        self.plan = plan_cascade(self.in_path, self.doc, self.exe, showers=self.showers, cap=cap,
                                 database=self.database, folder=folder, signature=sig, endf_dir=self.endf_dir)
        if self.plan.problems:
            raise RuntimeError("; ".join(self.plan.problems))
        write_cascade(self.plan, cancel=self._cancel.is_set)
        self.info = read_manifest(self.plan.folder)
        self._run_all(skip_done=False)

    def _probe(self) -> int:
        """Una lluvia de electrones con LTRACK 99: penhmain dice en penhmain.dat cuántas guardaría (NPSHOW, con dos
        cifras). Sin datos de hadrones: unos segundos. Se hace en una carpeta temporal de ARGUS (con enlaces a los
        ficheros del caso), que se borra al terminar."""
        import tempfile

        from ..results.folder import log_error

        with tempfile.TemporaryDirectory(prefix="argus-sonda-", ignore_cleanup_errors=True) as tmp:
            folder = Path(tmp)
            base, sources, _files = _inside_names(self.in_path, self.doc, self.database, self.endf_dir)
            d = cascade_doc(base, self.showers, case_seeds(self.doc), probe=True)
            fp = plan_folder(self.in_path, d, folder, database=self.database, penhmain=self.exe, sources=sources,
                             endf_dir=self.endf_dir)
            for it in fp.items:
                if it.status == "copy" and it.source is not None and it.kind not in ("in", "txt", "ctjson"):
                    target = folder / it.name.replace("\\", "/")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    link_new(it.source, target, executable=it.kind == "exe")
            write_new(folder / self.in_path.name, write_in_text(d).encode("latin-1", errors="replace"))
            write_new(folder / RUN_SCRIPT, _run_script(self.exe.name, self.in_path.name), executable=True)
            rc = self._run_one(-1, folder, timeout=PROBE_TIMEOUT)
            if self._cancel.is_set():
                return NPSHOW_DEFAULT
            text = _read_small(folder / "penhmain.dat", 1 << 20) or ""
            state = run_state(folder)
        n = read_npshow(text)
        if n is not None and n > 0:
            return n
        if state.status == "error":
            raise RuntimeError(f"la ejecución de prueba de penhmain ha fallado: {state.error}")
        if rc:
            raise RuntimeError(f"la ejecución de prueba de penhmain ha fallado (código {rc})")
        err = log_error(text)
        if err:
            raise RuntimeError(f"la ejecución de prueba de penhmain ha fallado: {err}")
        return NPSHOW_DEFAULT

    def _run_one(self, i: int, run_dir: Path, timeout: float | None = None) -> int | None:
        """Lanza penhmain en la carpeta de una ejecución y espera a que termine. Apunta en ``argus-rc`` cómo ha
        terminado (su código, «cancelada», «tiempo» o por qué no se ha podido lanzar)."""
        argv, io = run_argv(run_dir, self.exe.name, self.in_path.name, self.launch, self.host)
        kwargs: dict = {"cwd": str(run_dir)}
        _write_control(run_dir / RC_FILE, "")  # en marcha
        _write_control(run_dir / PID_FILE, "")  # el de este lanzamiento (lo apunta el guion o, en Windows, ARGUS)
        fin = fout = None
        try:
            if io:
                fin = open(run_dir / io["stdin"], "rb")
                fout = open(run_dir / io["stdout"], "wb")
                kwargs.update(stdin=fin, stdout=fout, stderr=subprocess.STDOUT)
            else:  # el guion pone la entrada y la salida de penhmain; aquí, lo que digan sh o wsl.exe
                fout = open(run_dir / LAUNCH_LOG, "wb")
                kwargs.update(stdin=subprocess.DEVNULL, stdout=fout, stderr=subprocess.STDOUT)
            if os.name == "nt":
                kwargs["creationflags"] = NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            p = subprocess.Popen(argv, **kwargs)
        except OSError as exc:
            _write_control(run_dir / RC_FILE, f"lanzar: {exc.strerror or exc}")
            return None
        finally:
            for f in (fin, fout):
                if f is not None:
                    f.close()
        if io:
            _write_control(run_dir / PID_FILE, str(p.pid))
        with self._lock:
            self._procs[i] = (p, run_dir)
        t0 = time.monotonic()
        outcome = None
        try:
            while True:
                try:
                    rc = p.wait(timeout=0.2)
                    outcome = str(rc)
                    return rc
                except subprocess.TimeoutExpired:
                    late = timeout is not None and time.monotonic() - t0 > timeout
                    if self._cancel.is_set() or late:
                        self._kill(i, p, run_dir)
                        rc = p.wait()
                        outcome = "tiempo" if late and not self._cancel.is_set() else "cancelada"
                        return rc
        finally:
            with self._lock:
                self._procs.pop(i, None)
            _write_control(run_dir / RC_FILE, outcome if outcome is not None else "cancelada")

    def _run_safe(self, i: int, run_dir: Path) -> None:
        try:
            self._run_one(i, run_dir)
        except Exception as exc:  # noqa: BLE001 - que no muera en silencio: queda en su carpeta
            _write_control(run_dir / RC_FILE, f"lanzar: {exc}")

    def _task(self, task: tuple[str, int, int], dirs: list[Path]) -> None:
        kind, i, b = task
        if kind == "run":
            self._run_safe(i, dirs[i])
        else:
            self._cut_safe(i, b, dirs[i])

    def _plan_cuts(self, i: int, dirs: list[Path], queue: list[tuple[str, int, int]]) -> None:
        """Una ejecución que penhmain ha terminado: ¿se sabe de qué lluvia es cada trayectoria? Si no, los cortes que
        hacen falta, a la cola (y, si ya se han hecho y aún falta algo, otra vuelta: hasta ``CUT_ROUNDS``)."""
        from ..results.shower import load_run

        plan = self.plan
        assert plan is not None
        r = plan.runs[i]
        if not run_has_tracks(dirs[i]):
            self._exact[i] = False
            self._cut_error[i] = ("penhmain ha terminado sin escribir ninguna trayectoria (ningún .trk): ¿escribe este "
                                  "penhmain las de LTRACK?")
            return
        try:
            s = load_run(dirs[i], in_name=self.in_path.name, showers=r.showers)
        except Exception as exc:  # noqa: BLE001 - un fallo al leer una ejecución no para las demás (se apunta en ella)
            self._exact[i] = False
            self._cut_error[i] = f"no se han podido leer sus trayectorias: {exc}"
            return
        if s.exact:
            self._exact[i] = True
            self._cut_error.pop(i, None)
            return
        self._exact[i] = False
        failed = self._cut_failed.get(i, {})
        need = [b for b in s.needed if b not in failed]
        rounds = self._rounds.get(i, 0)
        if not need or rounds >= CUT_ROUNDS:
            why = next(iter(failed.values()), "") if failed else ""
            self._cut_error[i] = ("no se ha podido saber de qué lluvia es cada trayectoria"
                                  + (f" ({why})" if why else ""))
            return
        self._rounds[i] = rounds + 1
        with self._lock:
            self._cuts_left[i] = set(need)
            self._cuts_total += len(need)
        queue.extend(("cut", i, b) for b in need)

    def _cut_safe(self, i: int, b: int, run_dir: Path) -> None:
        try:
            self._cut_one(i, b, run_dir)
        except Exception as exc:  # noqa: BLE001 - que no muera en silencio: se apunta en la ejecución
            self._cut_failed.setdefault(i, {})[b] = f"no se ha podido hacer: {exc}"
            record_cut(run_dir, b, None, f"no se ha podido hacer: {exc}")

    def _cut_one(self, i: int, b: int, run_dir: Path) -> None:
        """Un corte: la ejecución ``i`` otra vez, parando tras ``b`` lluvias, en una carpeta temporal dentro de la de la
        cascada (se borra al terminar). Apunta lo que dice en ``argus-cortes.json`` de la ejecución."""
        import shutil
        import tempfile

        plan = self.plan
        assert plan is not None
        r = plan.runs[i]
        nsimsh = cut_nsimsh(run_dir, self.in_path.name, r.showers, b)
        psf = cut_psf(run_dir, self.in_path.name, r.showers, b, r.folder)
        if nsimsh is None or (psf is None and run_psf_showers(run_dir, self.in_path.name, 1) is not None):
            self._cut_failed.setdefault(i, {})[b] = "no se sabe dónde parar"
            record_cut(run_dir, b, None, "no se sabe dónde parar")
            return
        cut_dir = Path(tempfile.mkdtemp(prefix=f"{CUT_PREFIX}{r.folder}-{b}-", dir=plan.folder))
        try:
            write_cut_folder(run_dir, cut_dir, plan.files[i].items if i < len(plan.files) else [], self.in_path.name,
                             self.exe.name, nsimsh, psf)
            with self._lock:
                self._cuts_running += 1
            try:
                self._run_one(-(1_000_000 + i * 100_000 + b), cut_dir)
            finally:
                with self._lock:
                    self._cuts_running -= 1
            if self._cancel.is_set():
                return  # sin apuntar nada: se hará la próxima vez
            st = run_state(cut_dir)
            if st.status != "terminada":
                why = st.error or "penhmain no ha terminado"
                self._cut_failed.setdefault(i, {})[b] = why
                record_cut(run_dir, b, None, why)
                return
            pts, err = check_cut(run_dir, cut_dir)
            if pts is None:
                self._cut_failed.setdefault(i, {})[b] = err
            record_cut(run_dir, b, pts, err)
            with self._lock:
                self._cuts_done += 1
        finally:
            shutil.rmtree(cut_dir, ignore_errors=True)

    def _exact_showers(self, dirs: list[Path], states: list[RunState]) -> int:
        plan = self.plan
        assert plan is not None
        done = 0
        for i, (r, st) in enumerate(zip(plan.runs, states)):
            if st.status != "terminada":
                continue
            ok = self._exact.get(i)
            if ok is None:
                ok = run_has_tracks(dirs[i]) and run_exact(dirs[i], self.in_path.name, r.showers)
            if ok:
                done += r.count
        return done

    def _clean_cuts(self, folder: Path | None = None) -> None:
        """Las carpetas de cortes que se quedaron a medias (se cerró ARGUS mientras se hacían): se borran, salvo si su
        penhmain sigue en marcha (de otra sesión de ARGUS: se borran la próxima vez, también si la cascada ya está
        terminada, y al acabar esta; hasta 0.17.2 se quedaban para siempre). Son de ARGUS y no tienen nada del caso
        (solo enlaces y lo que escribe penhmain)."""
        import shutil

        if folder is None:
            assert self.plan is not None
            folder = self.plan.folder
        with self._lock:
            mine = {str(d) for _p, d in self._procs.values()}
        try:
            left = [p for p in Path(folder).iterdir() if p.is_dir() and p.name.startswith(CUT_PREFIX)]
        except OSError:
            return
        for p in left:
            if str(p) in mine or pid_alive(p, self.launch) or _launching(p):
                continue
            shutil.rmtree(p, ignore_errors=True)

    def _elsewhere(self, run_dir: Path) -> bool:
        """¿Está penhmain en marcha en la carpeta de esta ejecución desde otra sesión de ARGUS, o lo está lanzando
        ahora? Al lanzar una ejecución, ARGUS deja vacío «argus-rc» y el PID llega después (con WSL, cuando arranca):
        uno vacío de hace menos de ``PID_WAIT`` es un lanzamiento en curso."""
        st = run_state(run_dir)
        if st.status not in ("simulando", "pendiente"):
            return False
        if st.status == "simulando" and pid_alive(run_dir, self.launch):
            return True
        rc = run_dir / RC_FILE
        try:
            return rc.read_text(encoding="utf-8").strip() == "" and time.time() - rc.stat().st_mtime < PID_WAIT
        except OSError:
            return False

    def _taken(self, i: int, dirs: list[Path], queue: list[tuple[str, int, int]], others: set[int]) -> bool:
        """Justo antes de lanzar la ejecución ``i``: ¿la ha lanzado o terminado mientras otra sesión de ARGUS con la
        misma cascada (las dos la tenían en su cola: se lanzaba dos veces en la misma carpeta, 0.16.3)? Si está en
        marcha allí, se espera a que termine (como las de una sesión cerrada); si ha terminado, se miran sus cortes."""
        if self._elsewhere(dirs[i]):
            others.add(i)
            with self._lock:
                self._others = set(others)
            return True
        if run_state(dirs[i]).status == "terminada" and run_has_tracks(dirs[i]):
            self._plan_cuts(i, dirs, queue)
            return True
        return False

    def _run_all(self, skip_done: bool) -> None:
        plan = self.plan
        assert plan is not None
        self._clean_cuts()
        dirs = [plan.folder / r.folder for r in plan.runs]
        states = [run_state(d) for d in dirs]
        # (Una terminada sin ninguna trayectoria, otra vez: se habrán borrado.)
        todo = [i for i, s in enumerate(states)
                if not (skip_done and s.status == "terminada" and run_has_tracks(dirs[i]))]
        # Las que siguen en marcha desde otra sesión de ARGUS (se cerró sin parar la cascada) no se lanzan otra vez
        # (serían dos penhmain en la misma carpeta): se espera a que terminen.
        others = {i for i in todo if skip_done and states[i].status == "simulando" and pid_alive(dirs[i], self.launch)}
        with self._lock:
            self._others = set(others)
        queue: list[tuple[str, int, int]] = [("run", i, 0) for i in todo if i not in others]
        jobs = self.jobs or parallel_jobs(max(len(todo), len(dirs)), plan.hadrons)
        total = sum(r.count for r in plan.runs)
        self._set(stage="simulando", folder=plan.folder, showers_total=total, cap=plan.cap, runs=states)
        for i, st in enumerate(states):  # las simuladas antes: ¿hace falta algún corte?
            if st.status == "terminada" and i not in todo:
                self._plan_cuts(i, dirs, queue)
        self._update_text(dirs, jobs)
        active: dict[tuple[str, int, int], threading.Thread] = {}
        last_look = time.monotonic()
        while (queue or active or others) and not self._cancel.is_set():
            # Primero lo de las primeras ejecuciones (sus cortes, en cuanto se puede): así se ven antes sus lluvias.
            queue.sort(key=lambda t: (t[1], 0 if t[0] == "run" else 1, t[2]))
            while queue and len(active) + len(others) < jobs and not self._cancel.is_set():
                task = queue.pop(0)
                if task[0] == "run" and self._taken(task[1], dirs, queue, others):
                    continue
                th = threading.Thread(target=self._task, args=(task, dirs), daemon=True,
                                      name=f"argus-cascada-{task[1] + 1}" + (f"-corte-{task[2]}" if task[2] else ""))
                active[task] = th
                th.start()
            time.sleep(0.25)
            for task in [t for t, th in active.items() if not th.is_alive()]:
                del active[task]
                kind, i, b = task
                if self._cancel.is_set():
                    continue
                if kind == "run":
                    if run_state(dirs[i]).status == "terminada":
                        self._plan_cuts(i, dirs, queue)
                else:
                    with self._lock:
                        left = self._cuts_left.setdefault(i, set())
                        left.discard(b)
                        again = not left
                    if again:
                        self._plan_cuts(i, dirs, queue)
            if others and time.monotonic() - last_look > ORPHAN_POLL:
                last_look = time.monotonic()
                for i in sorted(others):
                    if not self._elsewhere(dirs[i]):
                        others.discard(i)
                        if run_state(dirs[i]).status != "terminada":
                            queue.append(("run", i, 0))  # se interrumpió: otra vez
                        else:
                            self._plan_cuts(i, dirs, queue)
                with self._lock:
                    self._others = set(others)
            self._update_text(dirs, jobs)
        if self._cancel.is_set():
            for i in others:
                kill_pid(dirs[i], self.launch)
        for th in active.values():
            th.join(timeout=PID_WAIT + 10)
        self._clean_cuts()  # (las de otra sesión cuyo penhmain ha acabado entretanto)
        states = [run_state(d) for d in dirs]
        done = self._exact_showers(dirs, states)
        if self._cancel.is_set():
            self._set(stage="cancelada", text=f"Cascada cancelada: {_showers_text(done, total)} simuladas."
                      if total != 1 else f"Cascada cancelada: {_showers_text(done, total)} simulada.",
                      runs=states, showers_done=done, finished=time.time())
            return
        bad = [(r.folder, s.error) for r, s in zip(plan.runs, states) if s.status == "error"]
        bad += [(plan.runs[i].folder, why) for i, why in sorted(self._cut_error.items())
                if states[i].status == "terminada" and not self._exact.get(i)]
        if bad or done < total:
            if bad:
                why = f"{bad[0][0]}: {bad[0][1]}" + (f" (y {plural(len(bad) - 1, 'ejecución', 'ejecuciones')} más con "
                                                     "errores)" if len(bad) > 1 else "")
            else:
                left = [r.folder for r, s in zip(plan.runs, states) if s.status != "terminada"]
                why = (f"{', '.join(left[:3])}{'…' if len(left) > 3 else ''}: "
                       f"{'no ha terminado' if len(left) == 1 else 'no han terminado'}")
            self._set(stage="error", text=f"Cascada con errores: {_showers_text(done, total)}. {why}", error=why,
                      runs=states, showers_done=done, finished=time.time())
            return
        with self._lock:
            n_cuts = self._cuts_done
        cuts = f" y {plural(n_cuts, 'corte')}" if n_cuts else ""
        self._set(stage="terminada", text=f"Cascada simulada: {plural(done, 'lluvia')} en "
                                          f"{plural(len(dirs), 'ejecución', 'ejecuciones')} de penhmain{cuts} "
                                          f"({_fmt_time(time.time() - self._status.started)})."
                                          + psf_short_note(total, plan.showers),
                  runs=states, showers_done=done, finished=time.time())

    def _update_text(self, dirs: list[Path], jobs: int) -> None:
        plan = self.plan
        states = [run_state(d) for d in dirs]
        done = self._exact_showers(dirs, states)
        n_done = sum(1 for s in states if s.status == "terminada")
        with self._lock:
            running = sum(1 for i in self._procs if i >= 0)
            others = len(self._others)
            cuts_total, cuts_done, cuts_running = self._cuts_total, self._cuts_done, self._cuts_running
        total = sum(r.count for r in plan.runs)
        if len(dirs) == 1:
            runs = "una ejecución, " + ("terminada" if n_done else "en marcha" if running or others else "pendiente")
        else:
            runs = f"{n_done} de {len(dirs)} ejecuciones terminadas, {running} en marcha"
            if others:
                runs += f" y {others} de otra sesión de ARGUS"
            runs += f", {jobs} a la vez"
        text = f"Simulando la cascada con {self.exe.name}: {_showers_text(done, total)} ({runs})."
        if cuts_total:
            text += (" Para saber de qué lluvia es cada trayectoria, penhmain repite algunas ejecuciones parando "
                     f"antes: {fmt_int(cuts_done)} de {fmt_int(cuts_total)} "
                     + ("corte hecho" if cuts_total == 1 else "cortes hechos")
                     + (f", {cuts_running} en marcha." if cuts_running else "."))
        if plan.hadrons and n_done < len(dirs):
            text += " Con protones, alfas o neutrones, cada ejecución de penhmain prepara antes los datos nucleares " \
                    "(unos minutos)."
        self._set(text=text, runs=states, showers_done=done, cuts_done=cuts_done, cuts_total=cuts_total)


def _launching(run_dir: Path) -> bool:
    """¿Se está lanzando penhmain en esta carpeta (``argus-rc`` vacío de hace menos de ``PID_WAIT``: el PID llega
    después)?"""
    rc = run_dir / RC_FILE
    try:
        return rc.read_text(encoding="utf-8").strip() == "" and time.time() - rc.stat().st_mtime < PID_WAIT
    except OSError:
        return False


def psf_short_note(total: int, asked: int) -> str:
    """« El espacio de fases solo tiene N lluvias.» si la cascada tiene menos de las pedidas (solo pasa con un
    espacio de fases: no tiene más)."""
    if 0 < total < asked:
        return f" El espacio de fases solo tiene {plural(total, 'lluvia')} (se pidieron {fmt_int(asked)})."
    return ""


def _fmt_time(t: float) -> str:
    if t >= 3600:
        return f"{t / 3600:.1f} h".replace(".", ",")
    if t >= 60:
        return f"{t / 60:.1f} min".replace(".", ",")
    return f"{t:.0f} s"


# Cascadas en marcha en esta sesión de ARGUS (carpeta del caso → trabajo).
_ACTIVE: dict[str, CascadeJob] = {}


def _key(in_path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(str(in_path)))


def start_job(job: CascadeJob) -> CascadeJob:
    old = _ACTIVE.get(_key(job.in_path))
    if old is not None and old.running:
        # Se para (sin esperar mucho: se llama desde la interfaz). Si sus procesos siguen un momento, la nueva los ve
        # en marcha y espera a que paren antes de lanzar otros en las mismas carpetas.
        old.cancel()
        old.wait(3.0)
    _ACTIVE[_key(job.in_path)] = job
    return job.start()


def active_job(in_path: str | os.PathLike) -> CascadeJob | None:
    return _ACTIVE.get(_key(in_path))


def active_jobs() -> list[CascadeJob]:
    return [j for j in _ACTIVE.values() if j.running]


def cancel_all() -> None:
    """Para todas las cascadas de esta sesión (al cerrar ARGUS: se espera a que paren sus procesos)."""
    jobs = [j for j in list(_ACTIVE.values()) if j.running]
    threads = [threading.Thread(target=j.cancel, kwargs={"block": True}, daemon=True) for j in jobs]
    for th in threads:
        th.start()
    for th in threads:
        th.join(PID_WAIT + 10)


__all__ = ["CASCADE_DIR", "CascadeInfo", "CascadeJob", "CascadePlan", "CascadeRun", "DEFAULT_SHOWERS", "JobStatus",
           "MANIFEST", "NPSHOW_DEFAULT", "PROBE_LTRACK", "PsfShower", "RunState", "active_job", "active_jobs",
           "available_memory", "cancel_all", "cascade_doc", "cascade_matches", "cascade_progress", "case_seeds",
           "case_signature", "find_cascades", "kill_pid", "link_new", "new_cascade_folder", "parallel_jobs",
           "pid_alive", "plan_cascade", "psf_record", "read_manifest", "read_npshow", "read_psf_showers",
           "run_seeds", "run_state", "runs_for", "seed_pair", "signatures", "split_psf", "start_job", "write_cascade",
           "CUT_PREFIX", "check_cut", "cut_nsimsh", "failed_cuts", "record_cut", "run_exact", "unverified_showers",
           "write_cut_folder", "content_sha1", "cut_psf", "psf_counted_in_n", "psf_nsimsh", "psf_short_note",
           "run_counts", "run_has_tracks", "run_psf_showers", "untracked_runs", "psf_window", "psf_one_each"]
