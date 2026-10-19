"""Cabecera de los ficheros de volcado de penhmain (RESUME / DUMPTO).

penhmain escribe el volcado con escrituras dirigidas por lista (``WRITE(27,*)``)
y lo relee en el mismo orden al reanudar (penhmain.f 2025, «If 'RESUME' is
active»). Aquí se lee la cabecera: lluvias simuladas, tiempos, título,
semillas, partícula primaria, espectro de la fuente (energías,
probabilidades y contadores de cada intervalo) y número de cuerpos. Basta
para saber si penhmain podrá reanudar con ese fichero y si el volcado
corresponde al .in actual (penhmain solo compara el título).

Después vienen los resultados, cada uno con sus intervalos, y al reanudar
penhmain los toma del volcado y no del .in (HENANR, IMDETR, ANDETR, ENDETR
y DOSER): el espectro y los ángulos de lo que sale (NBE, NBANGL), los
detectores (cuántos y sus intervalos) y la rejilla de dosis. También se leen
(``DumpInfo.tallies``), sin los contadores (la dosis de un TAC ocupa cientos de
MB), para avisar cuando no son los del .in (``tally_differences``).

Estados:

* ``missing``: no existe (penhmain lo crea con el texto «Empty.»);
* ``empty``: la primera lectura falla y no hay nada que perder (vacío, en
  blanco o el «Empty.» que deja penhmain): penhmain avisa y empieza desde cero
  (y vuelve a escribir «Empty.» en él);
* ``foreign``: la primera lectura falla y el fichero tiene otra cosa (un .mat,
  el .in, un espacio de fases…): penhmain escribe «Empty.» encima y se pierde
  (duodécima revisión: se tomaba por vacío);
* ``folder``: es una carpeta: penhmain no puede abrirlo y se detiene;
* ``ok``: se ha leído el título (lo primero que comprueba penhmain);
* ``corrupt``: ni siquiera tiene título (o no se puede leer): penhmain fallaría al leerlo. Su ``message`` es una
  frase para ir tras el nombre («está incompleto (se acaba tras la primera línea)»).

Con ``ok``, ``problem`` dice si el resto se podrá leer: ``format`` (un valor
que no es un número: en la cabecera, IRETRN 3113; en los resultados, que
penhmain lee sin ERR=, un error de ejecución: ``problem_in``) o ``truncated``
(el fichero se acaba antes de los datos: penhmain se detendría con un error de
lectura, End of file). Un volcado completo acaba en «*** END ***» (penhmain no
lee esa línea: sin ella, se comprueba que estén todos los datos).
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.numfmt import fmt_energy, fmt_int, fmt_num, plural

if TYPE_CHECKING:
    from .sections import DoseGrid

_CACHE: dict[tuple[str, int, int], DumpInfo] = {}


@dataclass
class Bins:
    """Intervalos de energía de un resultado, como los guarda penhmain (NE, EL, EU y LLE; con escala logarítmica,
    EL y EU ya en eV, no sus logaritmos)."""
    n: int
    lo: float
    hi: float
    log: bool = False


@dataclass
class ImpactDump:
    """Lo demás que penhmain toma del volcado de cada detector de impacto (IMPDETR): IDCUT, la ventana y la escala de
    las edades y los nombres de sus ficheros (duodécima revisión: no se comparaban)."""
    idcut: int
    age: Bins | None  # (NAGE, AGEL, AGEU, LLAGE; en segundos) o None sin edades
    spectrum: str  # SPCDIO
    fluence: str | None  # SPCFLO (con IDCUT 2)
    ages: str | None  # SPCAGE (con edades)


@dataclass
class DumpTallies:
    """Lo que penhmain toma del volcado al reanudar, en lugar de lo que dice el .in."""
    energy: Bins  # NBE: espectro de lo que sale
    nth: int  # NBANGL: intervalos de θ…
    nph: int  # … y de φ
    log_theta: bool
    impact: list[tuple[Bins, int]] = field(default_factory=list)  # IMPDET (e IDAGEL: intervalos de edad, 0 sin ella)
    angular: list[tuple[Bins, tuple[float, float, float, float]]] = field(default_factory=list)  # ADENER, ANGDET (°)
    deposit: list[Bins] = field(default_factory=list)  # ENDETC
    grid: DoseGrid | None = None  # None: sin rejilla
    epro: bool = False
    ct: bool = False
    # Duodécima revisión: lo demás que penhmain toma del volcado.
    rwrite: float = 0.0  # partículas escritas en el espacio de fases de un detector de impacto (IPSF)
    impact_more: list[ImpactDump] = field(default_factory=list)
    angular_kpar: list[tuple[int, ...]] = field(default_factory=list)  # KKAD de cada detector angular (6 indicadores)
    angular_names: list[str] = field(default_factory=list)  # SPCDAO
    deposit_names: list[str] = field(default_factory=list)  # SPCDEO
    ndb: tuple[int, int, int] | None = None  # NDB de la rejilla de dosis (para comprobar lo que sigue)


@dataclass
class DumpInfo:
    path: Path
    state: str  # 'missing', 'empty', 'foreign', 'folder', 'ok', 'corrupt'
    message: str = ""
    problem: str | None = None  # None, 'format' (IRETRN 3113) o 'truncated' (error de lectura)
    problem_in: str = "header"  # dónde está ``problem``: 'header' (lo que lee PMRDR con ERR=) o 'results'
    showers: float = 0.0  # SHN: lluvias ya simuladas
    time_total: float = 0.0  # TIMET
    time_sim: float = 0.0  # TIMES
    title: str = ""  # 65 caracteres (sin los blancos finales)
    seeds: tuple[int, int] | None = None
    kparp: int | None = None
    nseb: int | None = None
    lspec: bool = False
    lline: bool = False
    energies: list[float] = field(default_factory=list)  # ESRC (ordenadas por SORT2)
    probs: list[float] = field(default_factory=list)  # PSRC (sin normalizar)
    counts: list[float] = field(default_factory=list)  # SHIST: partículas emitidas en cada intervalo
    einitial: float | None = None
    nbody: int | None = None
    tallies: DumpTallies | None = None  # None: no se han podido leer (no se sabe)
    npsn: int | None = None  # NPSN: el espacio de fases (IPSFN) por el que va
    rlread: float | None = None  # RLREAD: partículas leídas de él
    lpsf: bool | None = None  # LPSF: la fuente es un espacio de fases
    no_end: bool = False  # completo, pero sin la línea «*** END ***» (penhmain no la lee)

    @property
    def usable(self) -> bool:
        """Cabecera leída y fichero completo: penhmain lo podrá leer entero."""
        return self.state == "ok" and self.problem is None


MAX_VALUES = 1_000_000  # ningún bloque de la cabecera de un volcado de penhmain tiene tantos valores
MAX_DATA_VALUES = 1_000_000_000  # los contadores de la dosis de un TAC (al comprobar que no falta nada)
FULL_CHECK_BYTES = 64 << 20  # sin «*** END ***», hasta este tamaño se comprueba que estén todos los datos
SCAN_BYTES = 16 << 20  # con «*** END ***», hasta este tamaño también se mira que los contadores sean números
# Un contador que no es un número (penhmain lo lee sin ERR=: error de ejecución). Lo que puede escribir Fortran:
# cifras, signo, punto, exponente (E/D), repeticiones «r*c» de ifort, NaN e Infinity.
_NOT_NUMBER = re.compile(r"[^0-9eEdDnNaAiIfFtTyY+\-.*,/\s]")
# Lo que deja penhmain (o MultiRuns) en un volcado sin nada: no se pierde nada si se escribe «Empty.» encima.
_EMPTY_TEXTS = ("", "Empty.", "Empty file. Initialisation of MultiRuns.")
_ONLY_NUMBERS = re.compile(r"^[0-9eEdD+\-.\s]+$")


class _Records:
    """Lectura de registros como la de Fortran: dirigida por lista o con formato (A65), de uno en uno (un volcado
    con la dosis de un TAC ocupa cientos de MB y la cabecera, unos pocos KB)."""

    def __init__(self, lines):
        self._lines = iter(lines)
        self.first: str | None = None  # el primer registro
        self.lineno = 0  # el último registro leído (desde 1)
        self.seen: list[str] = []  # los primeros registros (para saber si un fichero es algo más que «Empty.»)

    def _next(self) -> str:
        for raw in self._lines:
            rec = raw.rstrip("\n").rstrip("\r")
            if self.first is None:
                self.first = rec
            self.lineno += 1
            if len(self.seen) < 8:
                self.seen.append(rec)
            return rec
        raise EOFError

    @staticmethod
    def _repeat(t: str) -> int:
        """Cuántos valores son «r*c» o «r*» (ifort repite así los valores iguales); 1 si es un valor sin más."""
        star = t.find("*")
        return int(t[:star]) if star > 0 and t[:star].isdigit() else 1

    def _tokens(self, n: int) -> list[str | None]:
        """Los n valores de un READ(9,*): empieza en un registro nuevo, sigue en los siguientes si hace falta y
        descarta el resto del último (un READ sin valores también pasa un registro). Los nulos («r*») son None."""
        toks: list[str | None] = []
        rec = self._next()
        while len(toks) < n:
            for t in rec.replace(",", " ").split():
                if t.startswith("/"):
                    raise ValueError("barra")
                r = self._repeat(t)
                if r == 1 and not t.endswith("*"):
                    toks.append(t)
                else:
                    val = t[t.find("*") + 1:] or None
                    toks.extend([val] * min(r, n - len(toks)))
                if len(toks) >= n:
                    break
            else:
                rec = self._next()
        return toks

    def items(self, kinds: list[str]) -> list:
        """Un READ(9,*) de len(kinds) valores: «int», «real», «logical» o «any» (cualquier cosa: no hace falta).
        Un valor que no se puede leer: ValueError con el valor («abc»), para los mensajes."""
        out = []
        for t, k in zip(self._tokens(len(kinds)), kinds):
            if k == "any":
                out.append(t)
                continue
            if t is None:
                raise ValueError("un valor nulo")
            try:
                if k == "int":
                    out.append(int(t))
                elif k == "real":
                    out.append(float(t.replace("D", "E").replace("d", "e")))
                else:  # lógico
                    u = t.upper().lstrip(".")
                    if not u or u[0] not in "TF":
                        raise ValueError(t)
                    out.append(u[0] == "T")
            except ValueError:
                raise ValueError(f"«{t[:24]}»") from None
        return out

    @staticmethod
    def _check(n: int) -> None:
        if not 0 <= n <= MAX_VALUES:  # un contador absurdo: el volcado está dañado
            raise ValueError(f"se piden {n} valores")

    def reals(self, n: int) -> list[float]:
        self._check(n)
        return self.items(["real"] * n)

    def ints(self, n: int) -> list[int]:
        self._check(n)
        return self.items(["int"] * n)

    def skip(self, n: int, limit: int = MAX_VALUES) -> None:
        """Un READ(9,*) de n valores que no hacen falta (los contadores): solo se cuentan, y se mira que sean números
        (penhmain los lee sin ERR=: uno que no lo es lo detiene con un error de ejecución)."""
        if not 0 <= n <= limit:
            raise ValueError(f"se piden {n} valores")
        left = n
        if n == 0:
            self._next()  # (un READ sin valores también pasa un registro)
            return
        rec = self._next()
        while True:
            bad = _NOT_NUMBER.search(rec)
            if bad:
                tok = next((t for t in rec.replace(",", " ").split() if _NOT_NUMBER.search(t)), bad.group(0))
                raise ValueError(f"«{tok[:24]}»")
            toks = rec.replace(",", " ").split()
            if "*" in rec or "/" in rec:
                for t in toks:
                    if t.startswith("/"):
                        raise ValueError("barra")
                    left -= self._repeat(t)
                    if left <= 0:
                        return
            else:
                left -= len(toks)
            if left <= 0:
                return
            rec = self._next()

    def a65(self) -> str:
        return self._next()[:65].ljust(65)

    def text(self) -> str:
        """Un READ(9,'(A)'): el registro entero."""
        return self._next()


def read_dump(path: str | os.PathLike) -> DumpInfo:
    """Cabecera del volcado (se guarda en caché mientras el fichero no cambie)."""
    p = Path(path)
    try:
        st = p.stat()
    except (OSError, ValueError):  # ValueError: un nombre con un carácter NUL
        return DumpInfo(p, "missing", "El fichero no existe.")
    if p.is_dir():
        return DumpInfo(p, "folder", "Es una carpeta.")
    key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        with open(p, "rb") as f:
            if b"\0" in f.read(4096):  # un fichero binario (una imagen…): no se lee por líneas
                info = DumpInfo(p, "foreign", "es un fichero binario")
                _CACHE[key] = info
                return info
            f.seek(max(st.st_size - 256, 0))
            tail = f.read().decode("latin-1")
            f.seek(0)
            # Registro a registro: la cabecera y los intervalos (los contadores solo se cuentan; los de la dosis, que
            # son casi todo el fichero, solo si falta la última línea).
            info = _parse(_Records(raw.decode("latin-1") for raw in f), p, tail, st.st_size)
    except OSError as exc:
        return DumpInfo(p, "corrupt", f"no se puede leer ({_os_reason(exc)})")
    except Exception:  # noqa: BLE001 - un volcado dañado no debe impedir abrir el .in
        info = DumpInfo(p, "corrupt", "no se puede interpretar")
    if len(_CACHE) > 64:
        _CACHE.clear()
    _CACHE[key] = info
    return info


def _os_reason(exc: OSError) -> str:
    """Por qué no se puede leer, en castellano y sin la ruta (el texto del sistema va en inglés)."""
    import errno

    return {errno.EACCES: "permiso denegado", errno.EPERM: "permiso denegado", errno.EISDIR: "es una carpeta",
            errno.ENOENT: "no existe", errno.EIO: "error de lectura del disco"}.get(exc.errno or 0, "error del sistema")


def parse_dump_text(text: str, path: Path | None = None, tail: str | None = None) -> DumpInfo:
    """Cabecera de un volcado; ``tail`` es el final del fichero (por defecto, el del propio texto)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return _parse(_Records(lines), path, tail if tail is not None else text[-256:], len(text.encode("latin-1",
                                                                                                    "replace")))


def _first_fails(r: _Records, info: DumpInfo) -> DumpInfo:
    """La primera lectura falla: penhmain escribe «Empty.» encima y empieza desde cero. Si era otra cosa (un .mat, el
    .in, un espacio de fases…), se pierde (``foreign``)."""
    for _ in range(4):  # unos registros más, para ver si hay algo más que «Empty.»
        try:
            r.text()
        except EOFError:
            break
    texts = [t.strip() for t in r.seen if t.strip()]
    first = (r.first or "").strip()
    if len(texts) <= 1 and all(t in _EMPTY_TEXTS for t in texts):
        info.state = "empty"
        info.message = f"«{first[:40]}»" if first else "vacío"
    elif len(texts) == 1 and _ONLY_NUMBERS.match(texts[0]):
        # La primera línea de un volcado, a medias (se interrumpió al empezar a escribirlo): no hay nada que perder.
        info.state = "empty"
        info.message = "cortado en la primera línea"
    else:
        info.state = "foreign"
        info.message = f"empieza por «{first[:40]}»"
    return info


def _parse(r: _Records, path: Path | None, tail: str, size: int | None = None) -> DumpInfo:
    info = DumpInfo(path or Path("dump"), "ok")
    # READ (9,*,ERR=91,END=91) SHNA,TIMETA,TIMESA -> si falla, penhmain empieza de cero.
    try:
        info.showers, info.time_total, info.time_sim = r.items(["real", "real", "real"])
    except (EOFError, ValueError):
        return _first_fails(r, info)
    try:
        info.title = r.a65().rstrip()
    except EOFError:
        info.state = "corrupt"
        info.message = "está incompleto (se acaba tras la primera línea)"
        return info
    try:
        s1, s2 = r.items(["int", "int"])
        info.seeds = (s1, s2)
        info.npsn, info.rlread, info.lpsf = r.items(["int", "real", "logical"])  # NPSN, RLREAD, LPSF
        (info.kparp,) = r.items(["int"])
        if info.kparp == 0:
            nsde, _, _ = r.items(["int", "real", "real"])
            r.reals(6 * nsde)
            r.reals(6 * nsde)
        else:
            info.nseb, info.lspec, info.lline = r.items(["int", "logical", "logical"])
            n = info.nseb + 1
            info.energies = r.reals(n)
            info.probs = r.reals(n)
            info.counts = r.reals(n)
        vals = r.items(["real"] * 7 + ["int", "int"])
        info.einitial = vals[0]
        for n in (3, 3, 18, 18, 2, 2, 2, 2, 2, 2, 24, 24, 3, 3):  # PRIM ... AVTL2
            r.reals(n)
        (info.nbody,) = r.items(["int"])
    except EOFError:
        info.problem = "truncated"
        info.message = "Se acaba antes de tiempo (en la línea " + f"{r.lineno})."
    except (ValueError, OverflowError, MemoryError) as exc:
        info.problem = "format"
        info.message = f"Tiene un valor que no se puede leer ({_what(exc)}, línea {r.lineno})."
    if info.problem is None and info.nbody is not None:
        # Lo que viene después lo leen HENANR, IMDETR… sin ERR=: un valor que no se puede leer o un fichero que se
        # acaba antes detienen penhmain con un error de ejecución (duodécima revisión: se daba por bueno).
        try:
            info.tallies = _tallies(r, info.nbody)
            if "*** END ***" not in tail:
                # PMRDR no lee esa línea: sin ella vale si están todos los datos (hasta FULL_CHECK_BYTES, se
                # comprueba; uno mayor se da por incompleto, como uno que penhmain está escribiendo).
                if size is None or size > FULL_CHECK_BYTES:
                    raise EOFError
                _rest(r, info.tallies)
                info.no_end = True
            elif size is not None and size <= SCAN_BYTES:
                _rest(r, info.tallies)  # los contadores de la dosis: uno dañado también detiene penhmain (DOSER)
        except EOFError:
            info.problem, info.problem_in = "truncated", "results"
            info.message = ("No termina en «*** END ***» como los volcados completos y le faltan datos (¿se "
                            "interrumpió al escribirlo?)." if "*** END ***" not in tail else
                            f"Se acaba antes de tiempo (en la línea {r.lineno}).")
        except (ValueError, OverflowError, MemoryError, IndexError) as exc:
            info.problem, info.problem_in = "format", "results"
            info.message = f"Tiene un valor que no se puede leer en los resultados ({_what(exc)}, línea {r.lineno})."
    elif info.problem is None and "*** END ***" not in tail:
        info.problem = "truncated"
        info.message = "No termina en «*** END ***» como los volcados completos (¿se interrumpió al escribirlo?)."
    return info


def _what(exc: BaseException) -> str:
    """El valor que no se puede leer, para el mensaje («abc»), sin el texto de Python."""
    text = str(exc)
    if text.startswith("«") or text in ("un valor nulo", "barra"):
        return text if text != "barra" else "una barra «/»"
    if isinstance(exc, MemoryError):
        return "un número de valores enorme"
    return "un número de valores que no tiene sentido" if "valores" in text else "un valor"


def _rest(r: _Records, t: DumpTallies) -> None:
    """Lo que sigue al principio de DOSED (penhmain.f 9378-9389): los contadores de la dosis (DOSER), el mapa del
    proceso externo (P3DMR, 10376-10388) y la dosis del TAC (CTDOSER, 11685-11690, tras XM… y NX…): solo se cuentan,
    para saber si un volcado sin «*** END ***» está completo."""
    if t.ndb is not None:
        n = t.ndb[0] * t.ndb[1] * t.ndb[2]
        for _ in range(3):  # VMASS, DOSE, DOSE2
            r.skip(n, MAX_DATA_VALUES)
        for _ in range(2):  # DDOSE, DDOSE2
            r.skip(t.ndb[2], MAX_DATA_VALUES)
    if t.epro:
        ndb = r.ints(3)
        r.reals(3)
        r.reals(3)
        r.skip(6)  # BP3DM, RBP3DM
        r.skip(2)  # KP3DM, IWREND
        n = ndb[0] * ndb[1] * ndb[2]
        for _ in range(3):  # VVOL, P3DM, P3DM2
            r.skip(n, MAX_DATA_VALUES)
        for _ in range(2):  # DP3DM, DP3DM2
            r.skip(ndb[2], MAX_DATA_VALUES)
    if t.ct:
        r.reals(3)  # XM, YM, ZM
        nxyz = r.ints(4)[3]  # NX, NY, NZ, NXYZ
        r.skip(3)  # DX, DY, DZ
        r.skip(3)  # RDX, RDY, RDZ
        for _ in range(3):  # DC, DOSECT, DOSECT2
            r.skip(nxyz, MAX_DATA_VALUES)


_BINS = ["int", "real", "real", "any", "any", "any"]  # NE, EL, EU, BSE, RBSE, LLE


def _flag(t) -> bool:
    """LLE, LLTH…: un entero (1: escala logarítmica) en penhmain 2025; también se acepta un lógico."""
    u = str(t or "").strip().upper().lstrip(".")
    return u[:1] == "T" or u.lstrip("+") == "1"


def _bins(vals: list) -> Bins:
    n, el, eu, log = int(vals[0]), float(vals[1]), float(vals[2]), _flag(vals[5])
    if log:  # penhmain guarda los logaritmos
        el, eu = math.exp(el), math.exp(eu)
    return Bins(n, el, eu, log)


def _tallies(r: _Records, nbody: int) -> DumpTallies:
    """Lo que viene tras NBODY, en el orden de penhmain.f (2025): TDEBO… (5465-5468), HENAND (6902-6910),
    NID… (5470), IMDETD (7816-7839), ANDETD (8492-8501), ENDETD (8859-8865) y el principio de DOSED (9379-9383).
    Los contadores solo se cuentan; la dosis (VMASS, DOSE…), lo último, no se lee."""
    from .sections import DoseGrid

    for _ in range(4):  # TDEBO, TDEBO2, TCHBO, TCHBO2
        r.skip(nbody)
    energy = _bins(r.items(_BINS))  # NBE
    for _ in range(2):  # PDE, PDE2
        r.skip(12 * energy.n)
    nth, _, _, _, _, nph, _, _, llth = r.items(["int", "any", "any", "any", "any", "int", "any", "any", "any"])
    for _ in range(2):  # PDA, PDA2
        r.skip(6 * nth * nph)
    for _ in range(3):  # PDETH, PDETH2, TW
        r.skip(6 * nth)
    nid, nad, ned, ldosem, lepro, lct = r.items(["int", "int", "int", "logical", "logical", "logical"])
    t = DumpTallies(energy, nth, nph, _flag(llth), epro=bool(lepro), ct=bool(lct))
    if nid > 0:
        _rlast, t.rwrite = r.items(["real", "real"])  # RLAST, RWRITE (partículas escritas en el espacio de fases)
        (n,) = r.ints(1)
        r.skip(n)  # EDEP
        r.skip(n)  # EDEP2
        idcut = r.ints(n)
        for i in range(n):
            spc = r.text().rstrip()  # SPCDIO
            b = _bins(r.items(_BINS))
            for k in (1, 1, 6, 6):  # DIT, DIT2, DIP, DIP2
                r.skip(k * b.n)
            flu = None
            if idcut[i] == 2:
                flu = r.text().rstrip()  # SPCFLO
                for k in (1, 1, 6, 6):  # FLT, FLT2, FLP, FLP2
                    r.skip(k * b.n)
            nage, agel, ageu, _, _, llage = r.items(["int", "real", "real", "any", "any", "any"])
            ages = None
            age = None
            if nage > 0:
                ages = r.text().rstrip()  # SPCAGE
                r.skip(nage)
                r.skip(nage)
                log = _flag(llage)
                age = Bins(nage, math.exp(agel) if log else agel, math.exp(ageu) if log else ageu, log)
            t.impact.append((b, max(nage, 0)))
            t.impact_more.append(ImpactDump(idcut[i], age, spc, flu, ages))
    if nad > 0:
        (n,) = r.ints(1)
        r.skip(n)
        r.skip(n)
        for _ in range(n):
            t.angular_names.append(r.text().rstrip())  # SPCDAO
            th = r.reals(4)  # THETA1, THETA2, PHI1, PHI2 (radianes)
            t.angular_kpar.append(tuple(r.ints(6)))  # KKAD: las partículas que cuenta
            b = _bins(r.items(_BINS))
            r.skip(6 * b.n)
            r.skip(6 * b.n)
            angles = tuple(math.degrees(v) for v in th)
            t.angular.append((b, (angles[0], angles[1], angles[2], angles[3])))
    if ned > 0:
        (n,) = r.ints(1)
        r.skip(n)
        r.skip(n)
        for _ in range(n):
            t.deposit_names.append(r.text().rstrip())  # SPCDEO
            b = _bins(r.items(_BINS))
            r.skip(b.n)  # DET
            t.deposit.append(b)
    if ldosem:
        ndb, dxl, dxu = r.ints(3), r.reals(3), r.reals(3)
        t.ndb = (ndb[0], ndb[1], ndb[2])
        r.skip(6)  # BDOSE, RBDOSE
        (kdose,) = r.ints(1)
        if kdose == 1:
            t.grid = DoseGrid((dxl[0], dxu[0], ndb[0]), (dxl[1], dxu[1], ndb[1]), (dxl[2], dxu[2], ndb[2]))
        elif kdose == 2:
            t.grid = DoseGrid(z=(dxl[2], dxu[2], ndb[2]), r=(dxu[0], ndb[0]))
        elif kdose == 3:
            t.grid = DoseGrid(r=(dxu[0], ndb[0]))
        else:
            raise ValueError(f"KDOSE = {kdose}")
    return t


# ---------------------------------------------------------------------------
# ¿Corresponde el volcado al .in actual?
# ---------------------------------------------------------------------------

def _close(a: float, b: float) -> bool:
    return a == b or abs(a - b) <= 1e-12 * max(abs(a), abs(b))


PARTICLE_NAMES = {0: "fuente radiactiva", 1: "electrones", 2: "fotones", 3: "positrones", 4: "protones",
                  5: "alfas"}


def source_differences(info: DumpInfo, doc, geo=None) -> list[str]:
    """Diferencias entre la fuente/geometría del volcado y las del .in (vacía si coinciden)."""
    return [text for _key, text in source_diffs(info, doc, geo)]


def source_diffs(info: DumpInfo, doc, geo=None) -> list[tuple[str, str]]:
    """Como ``source_differences``, con qué es cada una: «psf» (uno con espacio de fases y el otro no), «particle»,
    «spectrum_count» (otro número de intervalos o de líneas, o una energía única), «spectrum» (otras energías o
    probabilidades), «energy» (otra energía única) y «geometry» (otro número de cuerpos).

    Al reanudar, penhmain toma del volcado la partícula (KPARP) y el número de intervalos del espectro (NSEB, LSPEC,
    LLINE), y del .in la energía y las energías y probabilidades del espectro (penhmain.f 4306-4316: las del volcado
    se leen y se descartan)."""
    from .sections import sort2

    if not info.usable:
        return []
    out: list[tuple[str, str]] = []
    src = doc.model.source
    psf_in = bool(src.psf)
    if info.lpsf is not None and info.lpsf != psf_in:
        out.append(("psf", "fuente: el volcado se hizo con espacios de fases (IPSFN) y el .in no tiene ninguno"
                    if info.lpsf else "fuente: el .in lee espacios de fases (IPSFN) y el volcado no se hizo con ellos"))
    elif info.lpsf or psf_in:
        pass  # con espacios de fases, la partícula y la energía son las de cada partícula del fichero
    elif info.kparp is not None and info.kparp != src.kparp:
        out.append(("particle", f"partícula primaria: {PARTICLE_NAMES.get(info.kparp, info.kparp)} en el volcado, "
                    f"{PARTICLE_NAMES.get(src.kparp, src.kparp)} en el .in"))
    elif info.kparp not in (None, 0):
        if src.spectrum or src.lines:
            rows = src.spectrum or src.lines
            es, ps = sort2([e for e, _ in rows], [max(p, 0.0) for _, p in rows])
            want_n = len(rows) - 1 if src.spectrum else len(rows)
            kind_ok = info.lspec if src.spectrum else info.lline
            if not kind_ok or info.nseb != want_n:
                def count(n: int, spectrum: bool) -> str:
                    return f"{n} " + (("intervalo" if n == 1 else "intervalos") if spectrum else
                                      ("línea" if n == 1 else "líneas"))

                got = ("un espectro continuo" if info.lspec else "un espectro de líneas" if info.lline
                       else "una energía única")
                out.append(("spectrum_count", f"espectro: el volcado tiene {got}"
                            + (f" con {count(info.nseb, info.lspec)}" if (info.lspec or info.lline) else "")
                            + f"; el .in, {count(want_n, bool(src.spectrum))}"))
            else:
                n = len(es)
                if not all(_close(a, b) for a, b in zip(es, info.energies[:n])) or \
                        not all(_close(a, b) for a, b in zip(ps, info.probs[:n])):
                    out.append(("spectrum", "espectro: las energías o las probabilidades no son las mismas"))
        elif info.lspec or info.lline:
            out.append(("spectrum_count", "espectro: el volcado se hizo con un espectro y el .in tiene una energía "
                        "única"))
        elif info.einitial is not None and not _close(info.einitial, src.energy):
            out.append(("energy", f"energía: {fmt_energy(info.einitial)} en el volcado, {fmt_energy(src.energy)} en "
                        "el .in"))
    if geo is not None and getattr(geo, "ok", False) and info.nbody is not None and info.nbody != geo.nbodys:
        out.append(("geometry", f"geometría: {fmt_int(info.nbody)} cuerpos en el volcado, {fmt_int(geo.nbodys)} en "
                    "la geometría actual"))
    return out


# ---------------------------------------------------------------------------
# ¿Son los resultados del volcado los que pide el .in?
# ---------------------------------------------------------------------------

def _near(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b)) + 1e-12


def _bins_words(n: int, log: bool) -> str:
    return plural(n, "intervalo") + (" logarítmicos" if log and n != 1 else " logarítmico" if log else "")


def _bins_diff(b: Bins, el: float, eu: float, nbe: int, log_min: float) -> str | None:
    """Los intervalos del volcado frente a los del .in (EL, EU, NBE), con los arreglos de penhmain: EL ≥ 0 (con
    escala logarítmica, ≥ ``log_min``) y, con EU < 50 eV, la energía máxima de la fuente (no se compara)."""
    n, log = abs(int(nbe)), int(nbe) < 0
    lo = max(float(el), 0.0)
    if log:
        lo = max(lo, log_min)
    hi = float(eu) if float(eu) >= 50.0 else None
    if b.n != n or b.log != log:
        return f"{_bins_words(b.n, b.log)} en el volcado y {_bins_words(n, log)} en el .in"
    if not _near(b.lo, lo) or (hi is not None and not _near(b.hi, hi)):
        return (f"de {fmt_energy(b.lo)} a {fmt_energy(b.hi)} en el volcado y de {fmt_energy(lo)} a "
                + (fmt_energy(hi) if hi is not None else "la energía máxima") + " en el .in")
    return None


def _penhmain_grid(g):
    """La rejilla del .in como la guarda penhmain (DOSE0): ejes con el menor primero (penhmain los ordena)."""
    from .sections import DoseGrid

    def axis(t):
        return None if t is None else (min(t[0], t[1]), max(t[0], t[1]), max(int(t[2]), 1))

    return DoseGrid(axis(g.x), axis(g.y), axis(g.z), None if g.r is None else (g.r[0], max(int(g.r[1]), 1)))


def _same_grid(a, b) -> bool:
    for k in ("x", "y", "z"):
        ta, tb = getattr(a, k), getattr(b, k)
        if (ta is None) != (tb is None):
            return False
        if ta is not None and (ta[2] != tb[2] or not _near(ta[0], tb[0]) or not _near(ta[1], tb[1])):
            return False
    if (a.r is None) != (b.r is None):
        return False
    return a.r is None or (a.r[1] == b.r[1] and _near(a.r[0], b.r[0]))


def _grid_where(g) -> str:
    """Dónde está la rejilla: «x −6…6, y −6…6, z 0…15 cm»."""
    def span(k: str, t) -> str:
        return f"{k} {fmt_num(t[0], 6)}…{fmt_num(t[1], 6)}".replace("-", "−")

    parts = [span(k, getattr(g, k)) for k in ("x", "y", "z") if getattr(g, k) is not None]
    if g.r is not None:
        parts.insert(0, f"r 0…{fmt_num(g.r[0], 6)}")
    return ", ".join(parts) + " cm"


def tally_differences(info: DumpInfo, doc, keys: tuple[str, ...] | None = None) -> list[str]:
    """Lo que penhmain tomará del volcado al reanudar y no es lo que pide el .in: la rejilla de dosis, los detectores
    y sus intervalos, el espectro y los ángulos de lo que sale (vacía si coincide todo o no se sabe).

    ``keys`` elige qué se compara: «grid», «impact» (también IDCUT y las edades), «angular» (también las
    partículas que cuenta, ADKPAR), «deposit», «energy», «angles», «epro» y «names» (los nombres de los ficheros de
    los detectores, que penhmain también toma del volcado)."""
    return [f"{label}: {detail}" for _key, label, detail in tally_diffs(info, doc, keys)]


def tally_diffs(info: DumpInfo, doc, keys: tuple[str, ...] | None = None) -> list[tuple[str, str, str]]:
    """Como ``tally_differences``, en partes: (clave, qué, diferencia)."""
    from . import sections as sc

    t = info.tallies
    if not info.usable or t is None:
        return []
    out: list[tuple[str, str, str]] = []

    def want(key: str) -> bool:
        return keys is None or key in keys

    g = sc.dose_grid(doc)
    if want("grid") and g.kind != "invalid":
        mine = None if g.kind == "none" else _penhmain_grid(g)
        label = "rejilla de dosis"
        if t.grid is None and mine is not None:
            out.append(("grid", label, f"el .in tiene una ({sc.grid_text(mine)}) y el volcado no (penhmain no "
                                       "calculará la dosis)"))
        elif t.grid is not None and mine is None:
            out.append(("grid", label, f"el volcado tiene una ({sc.grid_text(t.grid)}) y el .in no (penhmain la "
                                       "seguirá calculando)"))
        elif t.grid is not None and mine is not None and not _same_grid(t.grid, mine):
            a, b = sc.grid_text(t.grid), sc.grid_text(mine)
            if a == b:
                a, b = _grid_where(t.grid), _grid_where(mine)
            out.append(("grid", label, f"{a} en el volcado y {b} en el .in"))
    imp = sc.impact_detectors(doc)
    ang = sc.angular_detectors(doc)
    dep = sc.energy_detectors(doc)
    if want("impact"):
        if len(imp) != len(t.impact):
            out.append(("impact", "detectores de impacto", _count_diff(len(t.impact), len(imp))))
        else:
            for i, (d, (b, nage)) in enumerate(zip(imp, t.impact), start=1):
                more = t.impact_more[i - 1] if i <= len(t.impact_more) else None
                diff = _bins_diff(b, d.el, d.eu, d.nbe, 1.0)
                age = abs(int(d.age[2])) if d.age is not None else 0
                if diff is None and nage != age:
                    diff = (f"{plural(nage, 'intervalo')} de edad en el volcado y "
                            f"{plural(age, 'intervalo') if age else 'ninguno'} en el .in")
                if diff is None and more is not None and more.age is not None and d.age is not None:
                    diff = _age_diff(more.age, d.age)
                if diff:
                    out.append(("impact", f"detector de impacto {i}", diff))
                if more is not None and more.idcut != d.idcut and d.idcut in (0, 1, 2):
                    out.append(("impact", f"detector de impacto {i}", _idcut_diff(more.idcut, d.idcut, i, d)))
    if want("angular"):
        if len(ang) != len(t.angular):
            out.append(("angular", "detectores angulares", _count_diff(len(t.angular), len(ang))))
        else:
            for i, (a, (b, angles)) in enumerate(zip(ang, t.angular), start=1):
                el, eu, nbe = a.energy if a.energy is not None else sc.default("ADENER")
                diff = _bins_diff(b, el, eu, nbe, 50.0)
                mine = (a.theta1, a.theta2, a.phi1, a.phi2)
                if diff is None and not all(_near(x, y) for x, y in zip(angles, mine)):
                    diff = f"{_window(angles)} en el volcado y {_window(mine)} en el .in"
                if diff:
                    out.append(("angular", f"detector angular {i}", diff))
                kkad = t.angular_kpar[i - 1] if i <= len(t.angular_kpar) else None
                kka = _kka(a.kpar)
                if kkad is not None and kka is not None and tuple(kkad) != kka:
                    out.append(("angular", f"detector angular {i}", f"cuenta {_kka_text(kkad)} en el volcado y "
                                f"{_kka_text(kka)} en el .in"))
    if want("deposit"):
        if len(dep) != len(t.deposit):
            out.append(("deposit", "detectores de energía depositada", _count_diff(len(t.deposit), len(dep))))
        else:
            for i, (e, b) in enumerate(zip(dep, t.deposit), start=1):
                diff = _bins_diff(b, e.el, e.eu, e.nbe, 50.0)
                if diff:
                    out.append(("deposit", f"detector de energía depositada {i}", diff))
    if want("names"):
        out += _name_diffs(t, imp, ang, dep)
    if want("energy"):
        v = sc.value(doc, "NBE")
        el, eu, nbe = v[:3] if v is not None and len(v) >= 3 else sc.default("NBE")
        diff = _bins_diff(t.energy, el, eu, nbe, 1.0)
        if diff:
            out.append(("energy", "espectro de lo que sale (NBE)", diff))
    if want("angles"):
        v = sc.value(doc, "NBANGL")
        nbth, nbph = v[:2] if v is not None and len(v) >= 2 else sc.default("NBANGL")
        nth, nph, log = abs(int(nbth)), abs(int(nbph)), int(nbth) < 0
        if (t.nth, t.nph, t.log_theta) != (nth, nph, log):
            out.append(("angles", "ángulos de lo que sale (NBANGL)",
                        f"{_angles(t.nth, t.nph, t.log_theta)} intervalos en el volcado y {_angles(nth, nph, log)} "
                        "en el .in"))
    if want("epro"):
        proc = sc.external_process(doc)
        if t.epro != (proc is not None):
            out.append(("epro", "proceso externo (EPROF)", "el volcado lo tiene y el .in no" if t.epro
                        else "el .in lo pide y el volcado no lo tiene"))
    return out


def _count_diff(saved: int, mine: int) -> str:
    return f"{fmt_int(saved)} en el volcado y {fmt_int(mine)} en el .in"


def _age_diff(saved: Bins, age: tuple[float, float, int]) -> str | None:
    """La ventana y la escala de las edades del volcado frente a las del .in (IDAGEL), con los arreglos de PMRDR:
    AGEL ≥ 1e-20 s con escala logarítmica y ≥ 0 con lineal (penhmain.f 3661-3671)."""
    log = int(age[2]) < 0
    lo = max(float(age[0]), 1.0e-20 if log else 0.0)
    hi = float(age[1])
    if saved.log == log and _near(saved.lo, lo) and _near(saved.hi, hi):
        return None
    return (f"edades {_age_window(saved.lo, saved.hi, saved.log)} en el volcado y {_age_window(lo, hi, log)} en "
            "el .in")


def _age_window(lo: float, hi: float, log: bool) -> str:
    return f"de {fmt_num(lo, 4)} a {fmt_num(hi, 4)} s (escala {'logarítmica' if log else 'lineal'})"


def _idcut_diff(saved: int, mine: int, i: int, det) -> str:
    """IDCUT: penhmain transporta con el del .in (absorber o no lo que entra, contar la fluencia) y escribe los
    resultados con el del volcado (penhmain.f 4758, 4980, 5091 frente a IMDETR, 7851)."""
    text = f"IDCUT {saved} en el volcado y {mine} en el .in ("
    if mine == 2 and saved != 2:
        return text + "penhmain contaría la fluencia y no la escribiría: el volcado no la tiene)"
    if saved == 2 and mine != 2:
        tail = " y absorbería lo que entra en el detector" if mine == 0 else ""
        return text + "penhmain dejaría de contar la fluencia, seguiría escribiendo la del volcado" + tail + ")"
    return text + ("penhmain transportaría con el del .in, " + ("absorbiendo" if mine == 0 else "sin absorber")
                   + " lo que entra en el detector, y lo guardado se hizo con el otro: mezclaría dos simulaciones)")


def _kka(kpar: int | None) -> tuple[int, ...] | None:
    """Las partículas que cuenta un detector angular, como KKA de PMRDR (penhmain.f 3875-3889): la de ADKPAR o, sin
    ADKPAR, todas. None si ADKPAR no es válido (eso ya es un error del .in)."""
    if kpar is None:
        return (1, 1, 1, 1, 1, 1)
    if not 1 <= int(kpar) <= 6:
        return None
    return tuple(1 if k == int(kpar) else 0 for k in range(1, 7))


def _kka_text(kka) -> str:
    from .sections import PARTICLE_NAMES

    names = [PARTICLE_NAMES[k] for k in range(1, 7) if k <= len(kka) and int(kka[k - 1]) == 1]
    if len(names) == 6:
        return "todas las partículas"
    if not names:
        return "ninguna partícula"
    return " y ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else names[0]


def _in_name(det, kw: str, default: str) -> str:
    """El nombre con que penhmain escribiría el fichero según el .in: las columnas 8-27 sin los blancos finales (con
    los iniciales) o, si están en blanco, el de penhmain."""
    ln = det.lines.get(kw) if det is not None else None
    text = ln.text.rstrip(" ") if ln is not None and ln.text is not None else ""
    return text if text.strip() else default


def _name_diffs(t: DumpTallies, imp, ang, dep) -> list[tuple[str, str, str]]:
    """Los nombres de los ficheros de los detectores: al reanudar, penhmain escribe con los del volcado (SPCDIO,
    SPCFLO, SPCAGE, SPCDAO y SPCDEO) y no con los del .in (duodécima revisión)."""
    pairs: list[tuple[str, str]] = []
    if len(imp) == len(t.impact_more):
        for i, (d, more) in enumerate(zip(imp, t.impact_more), start=1):
            pairs.append((more.spectrum, _in_name(d, "IDSPC", f"spc-impdet-{i:02d}.dat")))
            if more.fluence is not None:  # (penhmain llama al de la fluencia como al de IDPSF: penhmain.f 3652)
                pairs.append((more.fluence, _in_name(d, "IDPSF", f"fln-impdet-{i:02d}.dat")))
            if more.ages is not None:
                pairs.append((more.ages, _in_name(d, "IDAGEF", f"age-impdet-{i:02d}.dat")))
    if len(ang) == len(t.angular_names):
        for i, (a, name) in enumerate(zip(ang, t.angular_names), start=1):
            pairs.append((name, _in_name(a, "ADSPC", f"spc-angdet-{i:02d}.dat")))
    if len(dep) == len(t.deposit_names):
        for i, (e, name) in enumerate(zip(dep, t.deposit_names), start=1):
            pairs.append((name, _in_name(e, "EDSPC", f"spc-enddet-{i:02d}.dat")))
    moved = [f"«{saved.rstrip()}» y no «{mine}»" for saved, mine in pairs if saved.rstrip(" ") != mine]
    if not moved:
        return []
    return [("names", "ficheros de los detectores", "penhmain seguirá escribiendo en los del volcado: "
             + ", ".join(moved))]


def _window(a) -> str:
    return f"θ de {fmt_num(a[0], 6)} a {fmt_num(a[1], 6)}°, φ de {fmt_num(a[2], 6)} a {fmt_num(a[3], 6)}°"


def _angles(nth: int, nph: int, log: bool) -> str:
    return f"{fmt_int(nth)} × {fmt_int(nph)}" + (" (θ logarítmico)" if log else "")
