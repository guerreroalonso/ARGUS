"""Qué hará penhmain con RESUME y con DUMPTO (duodécima revisión).

Lo usan las comprobaciones del .in (``simcheck``), «Ejecutar» (``run.plan_run``), la sección «Trabajo», la nota de la
dosis, «Elegir resultados…» y las vistas (la rejilla del volcado). Antes cada uno lo decidía a su manera y a veces se
contradecían: un volcado dañado «empezaba desde cero» en «Ejecutar» y «detenía penhmain» en los mensajes; un nombre
con un blanco delante «continuaba» en unos sitios y «empezaba desde cero» en otros.

Lo que hace penhmain (penhmain.f 2025):

* PMRDR0 lee RESUME y DUMPTO con (A20): el nombre son las columnas 8-27 sin los blancos finales (un blanco delante
  es parte del nombre). Al acabar de leer el .in abre DUMPTO con IOSTAT (lo crea vacío si no existe) y lo cierra; lo
  sobrescribe en el primer volcado (PMWRT, sin IOSTAT: si no puede, se detiene antes de escribir ningún resultado).
* PMRDR (4291-4377) abre RESUME sin IOSTAT (en una carpeta que no existe, o si es una carpeta: error de ejecución).
  Si la primera lectura falla, escribe «Empty.» encima y empieza desde cero; luego compara el título (IRETRN 3112),
  lee la cabecera con ERR=90 (3113) y los resultados sin ERR= (un valor dañado o un fichero cortado: error de
  ejecución). No lee la línea «*** END ***».
* Con un detector de impacto que escribe espacio de fases (IPSF 1, del .in) y RWRITE > 0 (del volcado), relee el
  fichero con RDPSF (4392-4404), que lee bloques de 1000 registros y vuelve a empezar en cada vuelta (KODEPS = 0):
  llega al final del fichero y la unidad queda tras la marca de fin; gfortran no deja escribir después («Sequential
  READ or WRITE not allowed after EOF marker») y penhmain se detiene al escribir los primeros resultados tras
  reanudar: se pierde todo lo simulado desde entonces.
* Con espacios de fases como fuente, LPSF y NPSN son los del volcado (4305): si NPSN > NPSF (las líneas IPSFN del
  .in) la simulación ya está hecha (4421-4431); con LPSF falso y un .in con IPSFN, la fuente del volcado no la define
  el .in (penhmain falla). Con SHN ≥ NSIMSH (4512-4518), también está hecha. Hecha (JOBEND), penhmain vuelve a
  escribir los resultados y el volcado sin sumar el tiempo de esta ejecución: TIMET se queda en 0 (1272, 1322-1324).
"""

from __future__ import annotations

import math
import os
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from ..core.numfmt import fmt_count

FRESH = "fresh"  # penhmain empieza desde cero (no hay volcado: lo que haya en la carpeta se sobrescribirá)
CONTINUE = "continue"  # sigue la simulación guardada
DONE = "done"  # la simulación guardada ya está hecha: solo vuelve a escribir los resultados (con el tiempo a 0)
STOP = "stop"  # penhmain se detiene (al empezar o, con IPSF 1, al escribir los primeros resultados)
DESTROY = "destroy"  # no es un volcado: penhmain escribe «Empty.» encima (se pierde) y empieza desde cero


@dataclass
class ResumeOutcome:
    """Lo que hará penhmain con la línea RESUME del .in."""
    name: str  # como lo abre penhmain: las columnas 8-27 sin los blancos finales (con los del principio)
    action: str  # FRESH, CONTINUE, DONE, STOP o DESTROY
    why: str  # frase corta tras el nombre: «no existe», «es una carpeta», «es de otra simulación (otro título)»…
    detail: str = ""  # lo que hará penhmain, para los mensajes largos (sin el nombre)
    info: object | None = None  # el DumpInfo del fichero (None si penhmain no puede ni abrirlo)
    code: int | None = None  # IRETRN de penhmain, si lo hay
    reason: str | None = None  # su REASON
    diffs: list[tuple[str, str]] = field(default_factory=list)  # fuente o geometría distintas (``source_diffs``)
    tallies: list[str] = field(default_factory=list)  # cómo se cuentan los resultados (``tally_differences``)
    ltrack: bool = False  # el .in pide trayectorias (LTRACK) y penhmain no las escribirá (reanuda)

    @property
    def shown(self) -> str:
        """El nombre para los mensajes (sin blancos)."""
        return self.name.strip()

    @property
    def blank(self) -> bool:
        """El nombre empieza por un blanco: penhmain busca otro fichero (« dump.dmp»)."""
        return self.name[:1].isspace()

    @property
    def follows(self) -> bool:
        """penhmain toma del volcado los resultados (sus rejillas, intervalos y detectores): sigue o reescribe."""
        return self.action in (CONTINUE, DONE)

    @property
    def showers(self) -> float:
        return float(getattr(self.info, "showers", 0.0) or 0.0)

    @property
    def decays(self) -> bool:
        """La simulación guardada es de una fuente radiactiva (se cuentan desintegraciones)."""
        return getattr(self.info, "kparp", None) == 0


def count_text(n: float, decays: bool) -> str:
    """«1 partícula», «300 partículas», «1000 desintegraciones»."""
    from ..results.folder import count_text as ct

    return ct(n, decays)


def open_name(doc, keyword: str) -> str | None:
    """El nombre de RESUME o DUMPTO como lo abre penhmain: las columnas 8-27 sin los blancos finales, con los del
    principio (``sections.text`` los quita: sirve para enseñarlo, no para abrirlo). None sin la línea o en blanco."""
    from . import sections as sc

    ln = doc.first(sc.kw6(keyword))
    if ln is None:
        return None
    name = (ln.text or "").rstrip(" ")
    return name if name.strip() else None


def nsimsh(doc) -> float:
    """Las lluvias que pide el .in, como penhmain: sin NSIMSH o con menos de 1, 2e9."""
    from . import sections as sc

    v = sc.value(doc, "NSIMSH")
    try:
        n = float(v[0]) if v else 2.0e9
    except (TypeError, ValueError):
        n = 2.0e9
    return n if n >= 1.0 else 2.0e9


def _title(doc) -> str:
    return (doc.model.title or "")[:65].rstrip()


def resume_outcome(doc, folder, geo=None) -> ResumeOutcome | None:
    """Qué hará penhmain con el RESUME del .in en ``folder`` (None sin RESUME o sin carpeta)."""
    from . import sections as sc
    from .dumpfile import read_dump, source_diffs, tally_differences

    name = open_name(doc, "RESUME")
    if name is None or folder is None:
        return None
    path = Path(folder) / name
    if not _parent_exists(path):
        return ResumeOutcome(name, STOP, "está en una carpeta que no existe",
                             "penhmain no puede abrirlo y se detendría al empezar (error de ejecución)")
    info = read_dump(path)
    if info.state == "folder":
        return ResumeOutcome(name, STOP, "es una carpeta", "penhmain no puede abrirla y se detendría al empezar "
                             "(error de ejecución)", info)
    if info.state == "missing":
        return ResumeOutcome(name, FRESH, "no existe", "penhmain lo crea (con el texto «Empty.») y empieza desde cero",
                             info)
    if info.state == "empty":
        return ResumeOutcome(name, FRESH, "está vacío", f"no contiene una simulación guardada ({info.message}); "
                             "penhmain empieza desde cero", info)
    if info.state == "foreign":
        return ResumeOutcome(name, DESTROY, f"no es un volcado de penhmain ({info.message})",
                             "penhmain no lo puede leer como volcado: escribiría «Empty.» encima (se perdería lo que "
                             "tiene) y empezaría desde cero", info)
    if info.state == "corrupt":
        cut = (info.message or "").startswith("está incompleto")
        return ResumeOutcome(name, STOP, info.message or "no se puede leer",
                             "penhmain se detendría al leer el título (End of file)" if cut else
                             "penhmain se detendría al leerlo (error de ejecución)", info)
    title = _title(doc)
    if info.title != title:
        return ResumeOutcome(name, STOP, "es de otra simulación (otro título)",
                             f"su título es «{info.title}» y el del .in «{title}»", info,
                             3112, "The dump file is corrupted (the TITLE does not match).")
    if info.problem == "format":
        if info.problem_in == "header":
            return ResumeOutcome(name, STOP, "está dañado", f"{info.message} penhmain se detendría", info, 3113,
                                 "The dump file is empty or corrupted.")
        return ResumeOutcome(name, STOP, "está dañado", f"{info.message} penhmain lee esa parte sin comprobarla y se "
                             "detendría con un error de ejecución", info)
    if info.problem is not None:
        return ResumeOutcome(name, STOP, "está incompleto", f"{info.message} penhmain se detendría con un error de "
                             "lectura (End of file)", info)
    src = doc.model.source
    npsf = len(src.psf)
    done = ""  # por qué ya está terminada
    if info.lpsf and (info.npsn or 0) > npsf:
        done = ("ya se leyeron todos los espacios de fases de IPSFN" if npsf else
                "se hizo con espacios de fases de IPSFN y el .in no tiene ninguno")
    elif info.lpsf is False and npsf:
        return ResumeOutcome(name, STOP, "no se hizo con espacios de fases y el .in los lee (IPSFN)",
                             "penhmain seguiría con la fuente del volcado, que el .in no define, y fallaría "
                             "(Segmentation fault)", info)
    elif info.showers > nsimsh(doc) - 0.5:
        done = f"tiene {count_text(info.showers, info.kparp == 0)} y NSIMSH = {fmt_count(nsimsh(doc))}"
    t = info.tallies
    psf = _psf_writer(doc)
    if psf is not None and t is not None and t.rwrite > 0.5:
        i, fname = psf
        missing = not (Path(folder) / fname).is_file()
        if not done or missing:
            when = ("al empezar: no está el espacio de fases que tiene que seguir escribiendo" if missing else
                    "al escribir los primeros resultados tras reanudar (Sequential READ or WRITE not allowed after "
                    "EOF marker), y se perdería todo lo simulado desde que reanuda")
            return ResumeOutcome(name, STOP, f"no se puede seguir con el detector de impacto {i}, que escribe espacio "
                                 "de fases (IPSF 1)",
                                 f"penhmain relee {fname} para seguir escribiéndolo y se detendría {when}. Para "
                                 f"reanudar, ponga IPSF 0 en ese detector (el espacio de fases se queda como estaba) o "
                                 "empiece sin RESUME", info)
    out = ResumeOutcome(name, DONE if done else CONTINUE, done or f"tiene {count_text(info.showers, info.kparp == 0)}",
                        info=info)
    out.diffs = source_diffs(info, doc, geo) if not done else []
    out.tallies = tally_differences(info, doc)
    if not done:
        lt = sc.value(doc, "LTRACK")
        try:
            wants_tracks = lt is not None and int(lt[0]) > 0
        except (TypeError, ValueError):
            wants_tracks = False
        out.ltrack = wants_tracks and int(math.fmod(info.showers, 2.0e9) + 0.5) != 0  # N ≠ 0: sin .trk (1275)
    return out


def _parent_exists(path: Path) -> bool:
    try:
        return path.parent.is_dir()
    except (OSError, ValueError):
        return False


def _psf_writer(doc) -> tuple[int, str] | None:
    """El detector de impacto que escribe espacio de fases (IPSF 1) y su fichero, como lo abre penhmain."""
    from . import sections as sc

    for i, d in enumerate(sc.impact_detectors(doc), start=1):
        if d.ipsf == 1:
            ln = d.lines.get("IDPSF")
            text = (ln.text or "").rstrip(" ") if ln is not None else ""
            return i, text if text.strip() else "psf-impdet.dat"
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Las consecuencias de cada diferencia de la fuente (al seguir)
# ---------------------------------------------------------------------------------------------------------------------

def diff_consequence(key: str, info) -> str:
    """Lo que hará penhmain con cada diferencia de ``source_diffs`` al seguir la simulación (comprobado con el
    penhmain real: la partícula y el número de intervalos salen del volcado; la energía y el espectro, del .in)."""
    from .dumpfile import PARTICLE_NAMES

    if key == "particle":
        return f"penhmain seguirá simulando {PARTICLE_NAMES.get(info.kparp, 'la del volcado')}, la del volcado"
    if key == "spectrum_count":
        return ("penhmain toma del volcado el número de intervalos y del .in las energías: sortearía un espectro "
                "que no es ninguno de los dos")
    if key in ("spectrum", "energy"):
        return "las partículas nuevas saldrán con la del .in: mezclaría las dos"
    if key == "geometry":
        return f"penhmain sigue con los {fmt_count(info.nbody)} cuerpos del volcado al contar la energía de cada uno"
    return "penhmain mezclaría las dos simulaciones"


def diffs_text(out: ResumeOutcome) -> str:
    """«partícula primaria: fotones en el volcado, electrones en el .in (penhmain seguirá simulando fotones…)»."""
    return "; ".join(f"{text} ({diff_consequence(key, out.info)})" for key, text in out.diffs)


# ---------------------------------------------------------------------------------------------------------------------
# DUMPTO
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class DumpTarget:
    """El fichero en que penhmain escribirá el volcado."""
    name: str  # como lo abre penhmain (DUMPTO, o «dump.dmp» sin DUMPTO)
    explicit: bool  # lo dice DUMPTO
    problem: str | None = None  # por qué no se puede (penhmain se detendría o destruiría otro fichero)
    stops: bool = False  # el problema es que penhmain se detendría (si no, sobrescribiría otro fichero)

    @property
    def shown(self) -> str:
        return self.name.strip()


def dump_target(doc, folder=None, inputs: set[str] | None = None) -> DumpTarget | None:
    """Dónde escribirá penhmain el volcado: DUMPTO o, sin DUMPTO y con DUMPP < 1e10 (también sin DUMPP), «dump.dmp».
    None si no escribe ninguno. ``problem``: con la carpeta del caso, si no podrá (una carpeta, o en una carpeta que
    no existe: penhmain simularía y se detendría al escribir el primero, sin ningún resultado) o si es uno de los
    ficheros que lee penhmain (``inputs``, nombres de ``run._rel_key``; si no se dan, los del .in) o uno de los
    resultados que escribe."""
    from ..results.folder import dump_period
    from . import goals as gl

    name = open_name(doc, "DUMPTO")
    explicit = name is not None
    if name is None:
        if dump_period(gl.dumpp(doc)) is None:
            return None
        name = "dump.dmp"
    out = DumpTarget(name, explicit)
    if folder is None:
        return out
    path = Path(folder) / name
    if path.is_dir():
        out.problem, out.stops = "es una carpeta", True
    elif not _parent_exists(path):
        out.problem, out.stops = "está en una carpeta que no existe", True
    if out.problem:
        return out
    key = rel_key(name)
    reads = inputs if inputs is not None else input_names(doc)
    if key in reads:
        out.problem = "es uno de los ficheros que lee penhmain: escribiría el volcado encima"
        return out
    res = _result_name(doc, name)
    if res:
        out.problem = f"es {res}: penhmain escribiría ahí el volcado y lo otro, uno encima del otro"
    return out


def rel_key(name: str) -> str:
    """Como ``run._rel_key``: el nombre relativo a la carpeta del caso, para compararlo con otros."""
    return os.path.normcase(posixpath.normpath(name.strip().replace("\\", "/")))


def input_names(doc) -> set[str]:
    """Lo que lee penhmain según el .in (``rel_key``): el propio .in, la geometría, los materiales, los espacios de
    fases, la tabla de EPROF y el nucleido (no el volcado de RESUME, que se lee y se escribe). (Sin los INCLUDE ni el
    .ct de la geometría: «Ejecutar» los añade.)"""
    m = doc.model
    names = [Path(doc.path).name if doc.path else "", m.geomfn]
    names += [mat.fname for mat in m.materials]
    names += list(m.source.psf)
    names += [ln.text for ln in doc.find("EPROF ")]
    if m.source.kparp == 0 and m.source.nuclide:
        names.append(m.source.nuclide)
    return {rel_key(n) for n in names if n and n.strip()}


# Los resultados de nombre fijo que escribe penhmain (y la salida en pantalla de ARGUS).
_FIXED_OUTPUTS = {
    "penhmain-res.dat": "el resumen de la simulación (penhmain-res.dat)",
    "penhmain.dat": "el registro de penhmain (penhmain.dat)",
    "penhmain.out": "la salida en pantalla que guarda ARGUS (penhmain.out)",
    "argus-ejecutar.log": "la salida que guarda ARGUS (argus-ejecutar.log)",
    "geometry.rep": "el informe de la geometría (geometry.rep)",
    "material.dat": "uno de los resultados de penhmain (material.dat)",
}


def _result_name(doc, name: str) -> str | None:
    """Si ``name`` es uno de los resultados que escribe penhmain, cuál («el resumen de la simulación…»)."""
    import fnmatch

    from ..results.catalog import KINDS
    from . import sections as sc
    from .run import OUTPUT_NAME_KEYWORDS

    key = rel_key(name)
    base = posixpath.basename(key)
    if key in _FIXED_OUTPUTS:
        return _FIXED_OUTPUTS[key]
    for kind in KINDS.values():
        pattern = kind.default_name.replace("##", "[0-9][0-9]").lower()
        if "/" not in key and fnmatch.fnmatchcase(base.lower(), pattern):
            return f"uno de los resultados de penhmain ({kind.title.lower()})"
    for kw in OUTPUT_NAME_KEYWORDS:
        for ln in doc.find(sc.kw6(kw)):
            if (ln.text or "").strip() and rel_key(ln.text) == key:
                return f"el fichero de {kw} de un detector"
    return None


def short_state(out: ResumeOutcome) -> str:
    """Para la cabecera de «Trabajo»: «reanuda de dump.dmp», «dump.dmp ya terminada», «desde cero (RESUME sin
    volcado)»…"""
    if out.action == CONTINUE:
        return f"reanuda de {out.shown}"
    if out.action == DONE:
        return f"{out.shown} ya terminada"
    if out.action == FRESH:
        return "desde cero (RESUME sin volcado)"
    return "RESUME: penhmain se detendría" if out.action == STOP else "RESUME: no es un volcado"


def showers_note(out: ResumeOutcome) -> str:
    """«300 partículas ya simuladas» (o desintegraciones)."""
    n = out.showers
    return f"{count_text(n, out.decays)} ya {'simulada' if round(n) == 1 else 'simuladas'}"


__all__ = ["CONTINUE", "DESTROY", "DONE", "FRESH", "STOP", "DumpTarget", "ResumeOutcome", "count_text",
           "diff_consequence", "diffs_text", "dump_target", "input_names", "nsimsh", "open_name", "rel_key",
           "resume_outcome", "short_state", "showers_note"]
