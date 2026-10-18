"""Cliente Python de la sonda PENGEOM (``argus_probe``).

La sonda es un pequeño programa Fortran que enlaza el ``pengeom.f`` del
usuario sin modificarlo. ARGUS la usa **solo para verificarse**: compara sus
propias tablas, su localizador y su trazador de rayos con los de PENGEOM.
El programa ARGUS no la necesita para funcionar.

Uso típico::

    from argus.oracle.probe import run_probe
    res = run_probe("cubo-esfera.geo", points=pts)
    res.status.iretrn, res.tables.bodies[0].surfaces, res.locate_body

La sonda se ejecuta con el directorio de trabajo en la carpeta del ``.geo``
(PENGEOM abre los ficheros INCLUDE y CTBOX relativos a ella). Todo lo que
escribe la sonda (informe, avisos, binarios) va a un directorio temporal, de
modo que nunca se ensucia la carpeta del usuario.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.fortran_io import output_float

PROBE_NAME = "argus_probe.exe" if os.name == "nt" else "argus_probe"
FORTRAN_DIR = Path(__file__).with_name("fortran")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "probe"

_SEGMENT_DTYPE = np.dtype([("s", "<f8"), ("ibody", "<i4"), ("mat", "<i4"), ("kslast", "<i4")])


class ProbeError(RuntimeError):
    """La sonda no está disponible o ha fallado."""


# ---------------------------------------------------------------------------
# Compilación y localización del ejecutable
# ---------------------------------------------------------------------------

def probe_dir() -> Path:
    """Carpeta de las sondas: la variable ``ARGUS_PROBE_DIR`` (p. ej. otra compilación) o ``build/probe``."""
    env = os.environ.get("ARGUS_PROBE_DIR")
    return Path(env) if env else DEFAULT_BUILD_DIR


def find_probe(executable: str | os.PathLike | None = None) -> Path | None:
    """Ruta de la sonda: argumento, variable ``ARGUS_PROBE``, ``ARGUS_PROBE_DIR`` o ``build/probe``."""
    candidates = []
    if executable:
        candidates.append(Path(executable))
    env = os.environ.get("ARGUS_PROBE")
    if env:
        candidates.append(Path(env))
    candidates.append(probe_dir() / PROBE_NAME)
    for c in candidates:
        if c.is_file():
            return c
    return None


# Opciones de gfortran de las sondas: las que indica penhmain.f para compilar penhmain («gfortran -Wall -Os»).
# Importan: con -O2, MAX(a, NaN) no es el de -Os y GEOMIN trata distinto una superficie con los 10 coeficientes nulos.
PROBE_FLAGS: tuple[str, ...] = ("-Os",)


def build_probe(pengeom_source: str | os.PathLike,
                build_dir: str | os.PathLike | None = None,
                gfortran: str = "gfortran",
                flags: tuple[str, ...] = PROBE_FLAGS) -> Path:
    """Compila la sonda con gfortran a partir del ``pengeom.f`` indicado.

    ``pengeom.f`` no se copia ni se modifica: se compila desde su carpeta.
    """
    src = Path(pengeom_source).resolve()
    if not src.is_file():
        raise ProbeError(f"No se encuentra pengeom.f en {src}")
    compiler = shutil.which(gfortran)
    if compiler is None:
        raise ProbeError("No se encuentra gfortran en el PATH.")
    out = Path(build_dir) if build_dir else DEFAULT_BUILD_DIR
    out.mkdir(parents=True, exist_ok=True)
    steps = [
        (FORTRAN_DIR / "argus_modules.f90", out / "argus_modules.o"),
        (src, out / "pengeom.o"),
        (FORTRAN_DIR / "argus_probe.f90", out / "argus_probe.o"),
    ]
    for source, obj in steps:
        cmd = [compiler, *flags, "-J", str(out), "-I", str(out), "-c", str(source), "-o", str(obj)]
        _run_compiler(cmd)
    exe = out / PROBE_NAME
    _run_compiler([compiler, *flags, "-o", str(exe), *(str(o) for _, o in steps)])
    exe.with_name(exe.stem + ".flags").write_text(" ".join(flags) + "\n", encoding="ascii")
    return exe


def probe_flags(exe: str | os.PathLike | None) -> tuple[str, ...] | None:
    """Opciones con las que se compiló la sonda PENGEOM (None si no se sabe: compilada antes de anotarlas)."""
    if exe is None:
        return None
    marker = Path(exe).with_name(Path(exe).stem + ".flags")
    try:
        return tuple(marker.read_text(encoding="ascii").split())
    except OSError:
        return None


def _run_compiler(cmd: list[str], cwd: str | os.PathLike | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        raise ProbeError("Error de compilación:\n" + " ".join(cmd) + "\n" + proc.stderr)


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------

@dataclass
class ProbeStatus:
    iretrn: int
    reason: str
    nmat: int
    nbodys: int

    @property
    def ok(self) -> bool:
        return self.iretrn == 0


@dataclass
class ProbeBody:
    """Una fila de las tablas internas de PENGEOM (índices 1-based)."""

    index: int
    material: int
    mother: int
    is_module: bool
    alias: str
    daughters: list[int]
    surfaces: list[int]
    flags: list[int]
    limiting: list[int]


@dataclass
class ProbeTables:
    coefficients: np.ndarray  # (NSURF, 10): AXX AXY AXZ AYY AYZ AZZ AX AY AZ A0
    kplane: np.ndarray  # (NSURF,)
    bodies: list[ProbeBody]

    @property
    def nsurf(self) -> int:
        return int(self.coefficients.shape[0])

    @property
    def nbodys(self) -> int:
        return len(self.bodies)


@dataclass
class RayTrace:
    """Recorrido de un rayo con STEP: dónde entra en cada material nuevo."""

    ibody0: int
    mat0: int
    segments: np.ndarray  # estructurado: s, ibody, mat, kslast

    @property
    def s(self) -> np.ndarray:
        return self.segments["s"]

    @property
    def ibody(self) -> np.ndarray:
        return self.segments["ibody"]

    @property
    def mat(self) -> np.ndarray:
        return self.segments["mat"]


@dataclass
class ProbeResult:
    status: ProbeStatus
    tables: ProbeTables | None = None
    locate_body: np.ndarray | None = None
    locate_mat: np.ndarray | None = None
    rays: list[RayTrace] | None = None
    ct_voxels: np.ndarray | None = None  # CTLOC: ibody, mat, ix, iy, iz (desde 1; 0 fuera del CT), dens
    report: str = ""
    stdout: str = ""
    log: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------

def _as_rays(points: np.ndarray) -> np.ndarray:
    p = np.asarray(points, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] not in (3, 6):
        raise ValueError("Se esperan puntos (N,3) o puntos+direcciones (N,6).")
    if p.shape[1] == 3:
        d = np.zeros_like(p)
        d[:, 2] = 1.0
        p = np.hstack([p, d])
    return np.ascontiguousarray(p)


def _write_points(path: Path, pts: np.ndarray) -> None:
    with open(path, "wb") as f:
        f.write(np.int32(pts.shape[0]).tobytes())
        # Fortran lee P(6,N) en orden de columnas: x,y,z,u,v,w por punto.
        f.write(pts.astype("<f8").tobytes(order="C"))


def run_probe(geo_path: str | os.PathLike, *,
              parinp: list[float] | None = None,
              tables: bool = True,
              points: np.ndarray | None = None,
              rays: np.ndarray | None = None,
              max_segments: int = 10000,
              ct_points: np.ndarray | None = None,
              executable: str | os.PathLike | None = None,
              timeout: float = 600.0) -> ProbeResult:
    """Carga ``geo_path`` con GEOMIN y responde a las consultas pedidas.

    ``ct_points``: puntos (y direcciones) que se localizan con LOCATE y, si
    caen en la caja CT, con CTLOCATE (vóxel, material y densidad).
    """
    exe = find_probe(executable)
    if exe is None:
        raise ProbeError("Sonda PENGEOM no compilada (ver tools/build_probe.py).")
    geo = Path(geo_path).resolve()
    with tempfile.TemporaryDirectory(prefix="argus_probe_") as tmp:
        t = Path(tmp)
        cmds = [f"GEOM {geo.name}", f"REPORT {t / 'geometry.rep'}", f"LOG {t / 'fort26.txt'}"]
        if parinp:
            vals = " ".join(repr(float(v)) for v in parinp)
            cmds.append(f"PARINP {len(parinp)} {vals}")
        cmds += ["LOAD", f"STATUS {t / 'status.txt'}"]
        if tables:
            cmds.append(f"TABLES {t / 'tables.txt'}")
        if points is not None:
            pts = _as_rays(points)
            _write_points(t / "loc_in.bin", pts)
            cmds += ["LOCATE", str(t / "loc_in.bin"), str(t / "loc_out.bin")]
        if rays is not None:
            rr = _as_rays(rays)
            _write_points(t / "ray_in.bin", rr)
            cmds += [f"RAYS {int(max_segments)}", str(t / "ray_in.bin"), str(t / "ray_out.bin")]
        if ct_points is not None:
            cp = _as_rays(ct_points)
            _write_points(t / "ct_in.bin", cp)
            cmds += ["CTLOC", str(t / "ct_in.bin"), str(t / "ct_out.bin")]
        cmds.append("QUIT")
        try:
            proc = subprocess.run([str(exe)], input="\n".join(cmds) + "\n", cwd=str(geo.parent),
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - raro
            raise ProbeError(f"La sonda no terminó en {timeout} s") from exc
        status_file = t / "status.txt"
        if not status_file.is_file():
            raise ProbeError("La sonda no produjo estado.\n" + proc.stdout + proc.stderr)
        result = ProbeResult(status=_read_status(status_file), stdout=proc.stdout + proc.stderr)
        rep = t / "geometry.rep"
        if rep.is_file():
            result.report = rep.read_text(encoding="latin-1", errors="replace")
        log = t / "fort26.txt"
        if log.is_file():
            result.log = log.read_text(encoding="latin-1", errors="replace")
        if not result.status.ok:
            return result
        if tables:
            result.tables = _read_tables(t / "tables.txt")
        if points is not None:
            data = (t / "loc_out.bin").read_bytes()
            n = int(np.frombuffer(data[:4], "<i4")[0])
            arr = np.frombuffer(data[4:4 + 8 * n], "<i4").reshape(n, 2)
            result.locate_body = arr[:, 0].copy()
            result.locate_mat = arr[:, 1].copy()
        if rays is not None:
            result.rays = _read_rays(t / "ray_out.bin", rr.shape[0])
        if ct_points is not None:
            data = (t / "ct_out.bin").read_bytes()
            n = int(np.frombuffer(data[:4], "<i4")[0])
            result.ct_voxels = np.frombuffer(data[4:4 + _CTLOC_DTYPE.itemsize * n], _CTLOC_DTYPE).copy()
    return result


_CTLOC_DTYPE = np.dtype([("ibody", "<i4"), ("mat", "<i4"), ("ix", "<i4"), ("iy", "<i4"), ("iz", "<i4"),
                         ("dens", "<f8")])


def _read_status(path: Path) -> ProbeStatus:
    lines = path.read_text(encoding="latin-1").splitlines()
    while len(lines) < 4:
        lines.append("0")
    return ProbeStatus(iretrn=int(lines[0]), reason=lines[1].strip(),
                       nmat=int(lines[2]), nbodys=int(lines[3]))


def _ints(text: str) -> list[int]:
    vals = [int(v) for v in text.split()]
    return vals[1:1 + vals[0]] if vals else []


def _read_tables(path: Path) -> ProbeTables:
    lines = path.read_text(encoding="latin-1").splitlines()
    i = 0
    nsurf = int(lines[i].split()[1])
    i += 1
    coef = np.zeros((nsurf, 10))
    kplane = np.zeros(nsurf, dtype=np.int32)
    for k in range(nsurf):
        parts = lines[i].split()
        i += 1
        kplane[k] = int(parts[1])
        coef[k] = [output_float(v) for v in parts[2:12]]
    nbod = int(lines[i].split()[1])
    i += 1
    bodies = []
    for _ in range(nbod):
        head = lines[i]
        i += 1
        tok = head.split()
        alias = head[head.index("[") + 1:head.rindex("]")]
        body = ProbeBody(index=int(tok[1]), material=int(tok[3]), mother=int(tok[5]),
                         is_module=int(tok[7]) == 1, alias=alias,
                         daughters=_ints(lines[i][6:]), surfaces=_ints(lines[i + 1][6:]),
                         flags=_ints(lines[i + 2][6:]), limiting=_ints(lines[i + 3][6:]))
        i += 4
        bodies.append(body)
    return ProbeTables(coefficients=coef, kplane=kplane, bodies=bodies)


def _read_rays(path: Path, n: int) -> list[RayTrace]:
    data = path.read_bytes()
    pos = 0
    out = []
    for _ in range(n):
        ib0, im0, nseg = np.frombuffer(data[pos:pos + 12], "<i4")
        pos += 12
        size = int(nseg) * _SEGMENT_DTYPE.itemsize
        seg = np.frombuffer(data[pos:pos + size], _SEGMENT_DTYPE).copy()
        pos += size
        out.append(RayTrace(int(ib0), int(im0), seg))
    return out


# ---------------------------------------------------------------------------
# Sonda del lector de .in de penhmain (PMRDR0)
# ---------------------------------------------------------------------------

READER_PROBE_NAME = "argus_inprobe.exe" if os.name == "nt" else "argus_inprobe"


def extract_pmrdr0(penhmain_text: str) -> str:
    """Extrae sin cambios el módulo PENHMAIN_input y las subrutinas PMRDR0 y FWORD."""
    import re

    lines = penhmain_text.splitlines()

    def find(pattern: str, start: int = 0) -> int:
        rx = re.compile(pattern)
        for i in range(start, len(lines)):
            if rx.match(lines[i]):
                return i
        raise ProbeError(f"No se encuentra '{pattern}' en penhmain.f")

    m0 = find(r"^\s+MODULE PENHMAIN_input\b")
    m1 = find(r"^\s+END MODULE PENHMAIN_input", m0)
    p0 = find(r"^\s+SUBROUTINE PMRDR0\b")
    p1 = find(r"^      END\s*$", p0)
    f0 = find(r"^\s+SUBROUTINE FWORD\b")
    f1 = find(r"^      END\s*$", f0)
    parts = lines[m0:m1 + 1] + [""] + lines[p0:p1 + 1] + [""] + lines[f0:f1 + 1]
    return "\n".join(parts) + "\n"


def find_reader_probe() -> Path | None:
    exe = probe_dir() / READER_PROBE_NAME
    return exe if exe.is_file() else None


def build_reader_probe(penhmain_source: str | os.PathLike, build_dir: str | os.PathLike | None = None,
                       gfortran: str = "gfortran") -> Path:
    """Compila la sonda del lector de .in con el PMRDR0 del penhmain.f indicado."""
    src = Path(penhmain_source).resolve()
    if not src.is_file():
        raise ProbeError(f"No se encuentra penhmain.f en {src}")
    compiler = shutil.which(gfortran)
    if compiler is None:
        raise ProbeError("No se encuentra gfortran en el PATH.")
    out = Path(build_dir) if build_dir else DEFAULT_BUILD_DIR
    out.mkdir(parents=True, exist_ok=True)
    extract = out / "pmrdr0_extract.f"
    extract.write_text(extract_pmrdr0(src.read_text(encoding="latin-1")), encoding="latin-1")
    inc = ["-J", str(out), "-I", str(out)]
    objs = []
    for source, obj in ((FORTRAN_DIR / "penelope_stub.f90", out / "penelope_stub.o"),
                        (FORTRAN_DIR / "argus_modules.f90", out / "argus_modules_in.o"),
                        (extract, out / "pmrdr0_extract.o"),
                        (FORTRAN_DIR / "argus_inprobe.f90", out / "argus_inprobe.o")):
        _run_compiler([compiler, *PROBE_FLAGS, *inc, "-c", str(source), "-o", str(obj)])
        objs.append(str(obj))
    exe = out / READER_PROBE_NAME
    _run_compiler([compiler, "-o", str(exe), *objs])
    return exe


@dataclass
class ReaderResult:
    crashed: bool
    iretrn: int
    reason: str
    values: dict[str, list[str]]
    fort26: str
    stderr: str
    hang: bool = False  # penhmain no termina (bucle infinito)


def run_reader_probe(in_path: str | os.PathLike | None = None, text: str | None = None,
                     executable: str | os.PathLike | None = None, timeout: float = 5.0) -> ReaderResult:
    """Pasa un .in por el PMRDR0 real de penhmain y devuelve lo que ha leído."""
    exe = Path(executable) if executable else find_reader_probe()
    if exe is None or not exe.is_file():
        raise ProbeError("Sonda del lector de .in no compilada (tools/build_probe.py --penhmain).")
    data = text.encode("latin-1") if text is not None else Path(in_path).read_bytes()
    with tempfile.TemporaryDirectory(prefix="argus_inprobe_") as tmp:
        try:
            proc = subprocess.run([str(exe)], input=data, cwd=tmp, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ReaderResult(True, -1, "HANG", {}, "", "timeout", hang=True)
        fort26 = ""
        f = Path(tmp) / "fort26.txt"
        if f.is_file():
            fort26 = f.read_text(encoding="latin-1", errors="replace")
    out = proc.stdout.decode("latin-1", errors="replace")
    err = proc.stderr.decode("latin-1", errors="replace")
    values: dict[str, list[str]] = {}
    for ln in out.splitlines():
        key, _, rest = ln.partition(" ")
        # Solo los blancos finales (TRIM de Fortran): un tabulador o un NBSP al final de un nombre es parte de él
        # (penhmain lo usa en el OPEN) y los blancos iniciales también se conservan.
        values.setdefault(key, []).append(rest.rstrip(" "))
    crashed = proc.returncode != 0 or "Fortran runtime error" in err or "IRETRN" not in values
    iretrn = int(values["IRETRN"][0]) if "IRETRN" in values else -1
    reason = values.get("REASON", [""])[0]
    return ReaderResult(crashed, iretrn, reason, values, fort26, err)


# ---------------------------------------------------------------------------
# Sonda de las comprobaciones de penhmain (PMRDR0 + PMRDR, sin simular)
# ---------------------------------------------------------------------------

SIM_PROBE_NAME = "argus_simprobe.exe" if os.name == "nt" else "argus_simprobe"
_SIM_RENAMES = (("CALL PEINIT(", "CALL XEINIT("), ("CALL PHINIT(", "CALL XHINIT("), ("PHMFP(", "XHMFP("),
                ("AVNCOLH(", "XVNCOLH("), ("AVNCOL(", "XVNCOL("))


def prepare_sim_source(penhmain_text: str) -> str:
    """penhmain.f sin el programa principal y con la física de PMRDR sustituida.

    Los nombres nuevos tienen la misma longitud (no se pasa de la columna 72)
    y empiezan por X, que el IMPLICIT de penhmain hace DOUBLE PRECISION.
    """
    import re

    lines = penhmain_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    try:
        mp = next(i for i, ln in enumerate(lines) if ln.startswith("C") and "MAIN PROGRAM" in ln)
        start = mp - 1
        end = next(i for i in range(mp, len(lines)) if re.match(r"^      END\s*$", lines[i]))
        p0 = next(i for i, ln in enumerate(lines) if re.match(r"^      SUBROUTINE PMRDR\s*$", ln))
        p1 = next(i for i in range(p0, len(lines)) if re.match(r"^      END\s*$", lines[i]))
    except StopIteration:
        raise ProbeError("penhmain.f no tiene la estructura esperada (programa principal y PMRDR).") from None
    for i in range(p0, p1 + 1):
        ln = lines[i]
        if ln[:1] in ("C", "c", "*", "!"):
            continue
        for a, b in _SIM_RENAMES:
            ln = re.sub(r"(?<![A-Z0-9_])" + re.escape(a), b, ln)
        lines[i] = ln
    return "\n".join(lines[:start] + lines[end + 1:]) + "\n"


def find_sim_probe() -> Path | None:
    exe = probe_dir() / SIM_PROBE_NAME
    return exe if exe.is_file() else None


def build_sim_probe(penhmain_source: str | os.PathLike, build_dir: str | os.PathLike | None = None,
                    gfortran: str = "gfortran") -> Path:
    """Compila la sonda de PMRDR con el penhmain.f indicado y las bibliotecas de su carpeta.

    penhmain.f incluye penelope.f, pengeom.f, penhan.f, penvared2.f,
    penhfield.f, rita.f, timer.f y pennuca.f: tienen que estar a su lado.
    """
    src = Path(penhmain_source).resolve()
    if not src.is_file():
        raise ProbeError(f"No se encuentra penhmain.f en {src}")
    missing = [n for n in ("penelope.f", "pengeom.f", "penhan.f", "penvared2.f", "penhfield.f", "rita.f",
                           "timer.f", "pennuca.f") if not (src.parent / n).is_file()]
    if missing:
        raise ProbeError("Faltan junto a penhmain.f: " + ", ".join(missing))
    compiler = shutil.which(gfortran)
    if compiler is None:
        raise ProbeError("No se encuentra gfortran en el PATH.")
    out = Path(build_dir) if build_dir else DEFAULT_BUILD_DIR
    # Carpeta propia: los módulos de las otras sondas (PENERROR_mod de prueba...) no deben mezclarse.
    work = out / "sim"
    if work.is_dir():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    prepared = work / "penhmain_sim.f"
    prepared.write_text(prepare_sim_source(src.read_text(encoding="latin-1")), encoding="latin-1")
    inc = ["-J", str(work), "-I", str(work), "-I", str(src.parent)]
    objs = []
    for source, obj in ((prepared, work / "penhmain_sim.o"),
                        (FORTRAN_DIR / "argus_simstubs.f90", work / "argus_simstubs.o"),
                        (FORTRAN_DIR / "argus_simprobe.f90", work / "argus_simprobe.o")):
        _run_compiler([compiler, *PROBE_FLAGS, "-w", *inc, "-c", str(source), "-o", str(obj)], cwd=work)
        objs.append(str(obj))
    exe = out / SIM_PROBE_NAME
    _run_compiler([compiler, "-o", str(exe), *objs], cwd=work)
    return exe


@dataclass
class SimResult:
    iretrn: int
    reason: str
    output: str  # penhmain.dat
    crashed: bool
    stderr: str
    hang: bool = False
    files: dict[str, bytes] = field(default_factory=dict)  # ficheros que ha dejado (volcados...)


def run_sim_probe(text: str, files: dict[str, bytes | str | os.PathLike] | None = None,
                  executable: str | os.PathLike | None = None, timeout: float = 20.0) -> SimResult:
    """Pasa un .in por PMRDR0 y PMRDR en un directorio temporal con los ``files`` indicados.

    ``files``: nombre -> contenido (bytes/str) o ruta de un fichero a copiar (el
    .geo, volcados, ficheros de espacio de fases...).
    """
    exe = Path(executable) if executable else find_sim_probe()
    if exe is None or not exe.is_file():
        raise ProbeError("Sonda de PMRDR no compilada (tools/build_probe.py --penhmain).")
    with tempfile.TemporaryDirectory(prefix="argus_simprobe_") as tmp:
        t = Path(tmp)
        for name, content in (files or {}).items():
            if isinstance(content, bytes):
                (t / name).write_bytes(content)
            elif isinstance(content, str) and "\n" in content:
                (t / name).write_text(content, encoding="latin-1")
            else:
                shutil.copy2(content, t / name)
        try:
            proc = subprocess.run([str(exe)], input=text.encode("latin-1"), cwd=tmp, capture_output=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return SimResult(-1, "HANG", "", True, "timeout", hang=True)
        out_file = t / "penhmain.dat"
        output = out_file.read_text(encoding="latin-1", errors="replace") if out_file.is_file() else ""
        left = {p.name: p.read_bytes() for p in t.iterdir() if p.is_file() and p.suffix == ".dmp"}
    out = proc.stdout.decode("latin-1", errors="replace")
    err = proc.stderr.decode("latin-1", errors="replace")
    vals = {}
    for ln in out.splitlines():
        key, _, rest = ln.partition(" ")
        vals.setdefault(key, rest.strip(" "))  # solo blancos, como en run_reader_probe
    crashed = proc.returncode != 0 or "IRETRN" not in vals
    return SimResult(int(vals["IRETRN"]) if "IRETRN" in vals else -1, vals.get("REASON", ""), output, crashed,
                     err, files=left)
