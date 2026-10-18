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
* ``empty``: la primera lectura falla (p. ej. «Empty.»): penhmain avisa y
  empieza desde cero (y vuelve a escribir «Empty.» en él);
* ``ok``: se ha leído el título (lo primero que comprueba penhmain);
* ``corrupt``: ni siquiera tiene título: penhmain fallaría al leerlo.

Con ``ok``, ``problem`` dice si el resto se podrá leer: ``format`` (un valor
que no es un número: IRETRN 3113) o ``truncated`` (el fichero se acaba o no
termina en «*** END ***», como todos los volcados completos: penhmain se
detendría con un error de lectura, End of file).
"""

from __future__ import annotations

import math
import os
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


@dataclass
class DumpInfo:
    path: Path
    state: str  # 'missing', 'empty', 'ok', 'corrupt'
    message: str = ""
    problem: str | None = None  # None, 'format' (IRETRN 3113) o 'truncated' (error de lectura)
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

    @property
    def usable(self) -> bool:
        """Cabecera leída y fichero completo: penhmain lo podrá leer entero."""
        return self.state == "ok" and self.problem is None


MAX_VALUES = 1_000_000  # ningún bloque de la cabecera de un volcado de penhmain tiene tantos valores


class _Records:
    """Lectura de registros como la de Fortran: dirigida por lista o con formato (A65), de uno en uno (un volcado
    con la dosis de un TAC ocupa cientos de MB y la cabecera, unos pocos KB)."""

    def __init__(self, lines):
        self._lines = iter(lines)
        self.first: str | None = None  # el primer registro

    def _next(self) -> str:
        for raw in self._lines:
            rec = raw.rstrip("\n").rstrip("\r")
            if self.first is None:
                self.first = rec
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
        """Un READ(9,*) de len(kinds) valores: «int», «real», «logical» o «any» (cualquier cosa: no hace falta)."""
        out = []
        for t, k in zip(self._tokens(len(kinds)), kinds):
            if k == "any":
                out.append(t)
            elif t is None:
                raise ValueError("valor nulo")
            elif k == "int":
                out.append(int(t))
            elif k == "real":
                out.append(float(t.replace("D", "E").replace("d", "e")))
            else:  # lógico
                u = t.upper().lstrip(".")
                if not u or u[0] not in "TF":
                    raise ValueError(t)
                out.append(u[0] == "T")
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

    def skip(self, n: int) -> None:
        """Un READ(9,*) de n valores que no hacen falta (los contadores): solo se cuentan."""
        self._check(n)
        left = n
        rec = self._next()
        while True:
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
    key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        with open(p, "rb") as f:
            f.seek(max(st.st_size - 256, 0))
            tail = f.read().decode("latin-1")
            f.seek(0)
            # Registro a registro: solo se lee la cabecera (el resto del volcado no hace falta).
            info = _parse(_Records(raw.decode("latin-1") for raw in f), p, tail)
    except OSError as exc:
        return DumpInfo(p, "corrupt", f"No se puede leer: {exc}")
    except Exception as exc:  # noqa: BLE001 - un volcado dañado no debe impedir abrir el .in
        info = DumpInfo(p, "corrupt", f"No se puede interpretar: {exc}")
    if len(_CACHE) > 64:
        _CACHE.clear()
    _CACHE[key] = info
    return info


def parse_dump_text(text: str, path: Path | None = None, tail: str | None = None) -> DumpInfo:
    """Cabecera de un volcado; ``tail`` es el final del fichero (por defecto, el del propio texto)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return _parse(_Records(lines), path, tail if tail is not None else text[-256:])


def _parse(r: _Records, path: Path | None, tail: str) -> DumpInfo:
    info = DumpInfo(path or Path("dump"), "ok")
    # READ (9,*,ERR=91,END=91) SHNA,TIMETA,TIMESA -> si falla, penhmain empieza de cero.
    try:
        info.showers, info.time_total, info.time_sim = r.items(["real", "real", "real"])
    except (EOFError, ValueError):
        first = (r.first or "").strip()
        info.state = "empty"
        info.message = f"«{first[:40]}»" if first else "vacío"
        return info
    try:
        info.title = r.a65().rstrip()
    except EOFError:
        info.state = "corrupt"
        info.message = "Termina tras la primera línea: penhmain fallaría al leer el título."
        return info
    try:
        s1, s2 = r.items(["int", "int"])
        info.seeds = (s1, s2)
        r.items(["int", "real", "logical"])  # NPSN, RLREAD, LPSF
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
        info.message = "El fichero se acaba antes de tiempo."
    except (ValueError, OverflowError, MemoryError) as exc:
        info.problem = "format"
        info.message = f"Tiene un valor que no es un número o no tiene sentido ({exc})."
    if info.problem is None and "*** END ***" not in tail:
        info.problem = "truncated"
        info.message = "No termina en «*** END ***» como los volcados completos (¿se interrumpió al escribirlo?)."
    if info.problem is None and info.nbody is not None:
        try:
            info.tallies = _tallies(r, info.nbody)
        except (EOFError, ValueError, OverflowError, MemoryError, IndexError):
            info.tallies = None  # otra versión de penhmain u otro formato: no se sabe (no se avisa de nada)
    return info


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
        r.skip(2)  # RLAST, RWRITE
        (n,) = r.ints(1)
        r.skip(n)  # EDEP
        r.skip(n)  # EDEP2
        idcut = r.ints(n)
        for i in range(n):
            r.text()  # SPCDIO
            b = _bins(r.items(_BINS))
            for k in (1, 1, 6, 6):  # DIT, DIT2, DIP, DIP2
                r.skip(k * b.n)
            if idcut[i] == 2:
                r.text()  # SPCFLO
                for k in (1, 1, 6, 6):  # FLT, FLT2, FLP, FLP2
                    r.skip(k * b.n)
            nage = int(r.items(["int", "any", "any", "any", "any", "any"])[0])
            if nage > 0:
                r.text()  # SPCAGE
                r.skip(nage)
                r.skip(nage)
            t.impact.append((b, max(nage, 0)))
    if nad > 0:
        (n,) = r.ints(1)
        r.skip(n)
        r.skip(n)
        for _ in range(n):
            r.text()  # SPCDAO
            th = r.reals(4)  # THETA1, THETA2, PHI1, PHI2 (radianes)
            r.skip(6)  # KKAD
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
            r.text()  # SPCDEO
            b = _bins(r.items(_BINS))
            r.skip(b.n)  # DET
            t.deposit.append(b)
    if ldosem:
        ndb, dxl, dxu = r.ints(3), r.reals(3), r.reals(3)
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


def source_differences(info: DumpInfo, doc, geo=None) -> list[str]:
    """Diferencias entre la fuente/geometría del volcado y las del .in (vacía si coinciden)."""
    from .sections import sort2

    if not info.usable:
        return []
    out: list[str] = []
    src = doc.model.source
    if info.kparp is not None and info.kparp != src.kparp:
        names = {0: "fuente radiactiva", 1: "electrones", 2: "fotones", 3: "positrones", 4: "protones",
                 5: "alfas"}
        out.append(f"partícula primaria: {names.get(info.kparp, info.kparp)} en el volcado, "
                   f"{names.get(src.kparp, src.kparp)} en el .in")
    elif info.kparp not in (None, 0) and not src.psf:
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
                out.append(f"espectro: el volcado tiene {got}"
                           + (f" con {count(info.nseb, info.lspec)}" if (info.lspec or info.lline) else "")
                           + f"; el .in, {count(want_n, bool(src.spectrum))}")
            else:
                n = len(es)
                if not all(_close(a, b) for a, b in zip(es, info.energies[:n])) or \
                        not all(_close(a, b) for a, b in zip(ps, info.probs[:n])):
                    out.append("espectro: las energías o las probabilidades no son las mismas")
        elif info.lspec or info.lline:
            out.append("espectro: el volcado se hizo con un espectro y el .in tiene una energía única")
        elif info.einitial is not None and not _close(info.einitial, src.energy):
            out.append(f"energía: {fmt_energy(info.einitial)} en el volcado, {fmt_energy(src.energy)} en el .in")
    if geo is not None and getattr(geo, "ok", False) and info.nbody is not None and info.nbody != geo.nbodys:
        out.append(f"geometría: {fmt_int(info.nbody)} cuerpos en el volcado, {fmt_int(geo.nbodys)} en la geometría "
                   "actual")
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

    ``keys`` elige qué se compara: «grid», «impact», «angular», «deposit», «energy», «angles» y «epro»."""
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
    if want("impact"):
        imp = sc.impact_detectors(doc)
        if len(imp) != len(t.impact):
            out.append(("impact", "detectores de impacto", _count_diff(len(t.impact), len(imp))))
        else:
            for i, (d, (b, nage)) in enumerate(zip(imp, t.impact), start=1):
                diff = _bins_diff(b, d.el, d.eu, d.nbe, 1.0)
                age = abs(int(d.age[2])) if d.age is not None else 0
                if diff is None and nage != age:
                    diff = (f"{plural(nage, 'intervalo')} de edad en el volcado y "
                            f"{plural(age, 'intervalo') if age else 'ninguno'} en el .in")
                if diff:
                    out.append(("impact", f"detector de impacto {i}", diff))
    if want("angular"):
        ang = sc.angular_detectors(doc)
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
    if want("deposit"):
        dep = sc.energy_detectors(doc)
        if len(dep) != len(t.deposit):
            out.append(("deposit", "detectores de energía depositada", _count_diff(len(t.deposit), len(dep))))
        else:
            for i, (e, b) in enumerate(zip(dep, t.deposit), start=1):
                diff = _bins_diff(b, e.el, e.eu, e.nbe, 50.0)
                if diff:
                    out.append(("deposit", f"detector de energía depositada {i}", diff))
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


def _window(a) -> str:
    return f"θ de {fmt_num(a[0], 6)} a {fmt_num(a[1], 6)}°, φ de {fmt_num(a[2], 6)} a {fmt_num(a[3], 6)}°"


def _angles(nth: int, nph: int, log: bool) -> str:
    return f"{fmt_int(nth)} × {fmt_int(nph)}" + (" (θ logarítmico)" if log else "")
