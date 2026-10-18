"""Simulaciones en paralelo (como MultiRuns de penhan) y campañas de simulaciones.

ARGUS prepara las carpetas y las órdenes para lanzarlas; el usuario las lanza
(ARGUS no ejecuta penhmain) y la ventana de resultados las sigue mientras
simulan.

* **En paralelo**: el mismo .in en N carpetas ``run-01``… con semillas
  independientes (``RSEED -i 0``: el par i de la tabla de RAND0 de penhmain,
  de 1 a 1001) y las lluvias repartidas. Sus resultados se combinan como hace
  penhmain-sum (la ventana de resultados, o el propio penhmain-sum con la orden
  ``combinar``).
* **Campaña**: variantes del caso cambiando uno o dos parámetros (la energía,
  un material, un PARINP de la geometría o cualquier valor de una palabra clave
  del .in), cada una en su carpeta y, si se pide, con varias ejecuciones en
  paralelo; la ventana de resultados las compara y dibuja una medida frente al
  parámetro.

En cada ejecución: DUMPTO (``dump.dmp``) y DUMPP (resultados cada DUMPP s, para
seguirla y poder reanudarla), sin RESUME. Nada se sobrescribe: si una carpeta
o un fichero ya existen, no se tocan.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..core.numfmt import lower_first, plural
from . import sections as sc
from .inputfile import InDocument, parse_in_text, write_in_text
from .sections import kw6

MAX_SEED_PAIR = 1001  # pares de semillas de RAND0 en penhmain
RUN_SET_BY_ARGUS = {"RSEED", "RESUME", "DUMPTO"}  # cada ejecución lleva los suyos
MIN_DUMPP = 30.0
MANIFEST_PARALLEL = "argus-paralelo.json"
MANIFEST_CAMPAIGN = "argus-campana.json"
OUTPUT_NAME = "penhmain.out"  # salida en pantalla de cada ejecución


# ---------------------------------------------------------------------------------------------------------------------
# Parámetros de una campaña
# ---------------------------------------------------------------------------------------------------------------------

# Nombre de cada valor de las palabras clave que más se cambian (para los rótulos de las variantes).
VALUE_NAMES = {
    "SENERG": [""], "SPOSIT": ["x", "y", "z"], "SCONE": ["θ", "φ", "α"], "SRECTA": ["θ1", "θ2", "φ1", "φ2"],
    "SBOX": ["ancho x", "ancho y", "ancho z"], "NSIMSH": [""], "TIME": [""], "DUMPP": [""],
}

@dataclass
class Param:
    """Lo que cambia de una variante a otra.

    ``kind``: 'keyword' (el valor ``index``, desde 0, de la línea ``keyword``), 'parinp' (PARINP ``number``) o
    'material' (el fichero .mat del material ``number``).
    """

    kind: str
    values: list
    keyword: str = ""
    index: int = 0
    number: int = 0
    unit: str = ""

    @property
    def name(self) -> str:
        if self.kind == "parinp":
            return f"PARINP {self.number}"
        if self.kind == "material":
            return f"material {self.number}"
        kw = self.keyword.strip().upper()
        names = VALUE_NAMES.get(kw)
        if names and self.index < len(names):
            return f"{kw} {names[self.index]}" if names[self.index] else kw
        return kw + (f"[{self.index + 1}]" if self.index else "")

    def text(self, v) -> str:
        if self.kind == "material":
            return f"{self.name} = {v}"
        return f"{self.name} = {float(v):g}" + (f" {self.unit}" if self.unit else "")

    def slug(self, v) -> str:
        """Trozo del nombre de la carpeta de la variante."""
        if self.kind == "material":
            s = Path(str(v)).stem
        else:
            s = f"{float(v):g}".replace("+", "")
        base = self.keyword.strip() or ("P" if self.kind == "parinp" else "M")
        if self.kind == "parinp":
            base = f"P{self.number}"
        elif self.kind == "material":
            base = f"M{self.number}"
        return "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in f"{base}_{s}")

    def apply(self, doc: InDocument, v) -> None:
        """Pone el valor ``v`` en el .in (``ValueError`` si no se puede o no tendría efecto)."""
        if self.kind == "parinp":
            vals = sc.parinp(doc)
            vals[int(self.number)] = float(v)
            sc.set_parinp(doc, vals)
            return
        if self.kind == "material":
            from .materials import mat_filename_problem, set_material

            n = len(doc.model.materials)
            if not 1 <= int(self.number) <= n:
                raise ValueError(f"El .in tiene {plural(n, 'material', 'materiales')}: no hay material "
                                 f"{int(self.number)} que cambiar")
            why = mat_filename_problem(str(v))
            if why:
                raise ValueError(why)
            set_material(doc, int(self.number), fname=str(v))
            return
        kw = self.keyword.strip().upper()
        if kw6(kw) not in sc.SPEC:
            raise ValueError(f"«{kw}» no es una palabra clave del .in de penhmain")
        if kw in RUN_SET_BY_ARGUS:
            raise ValueError(f"{kw} lo pone ARGUS en cada ejecución (semillas distintas, volcados, sin RESUME): no "
                             "puede ser un parámetro de la campaña")
        if kw == "SCONE" and sc.value(doc, "SRECTA") and not sc.value(doc, "SCONE"):
            raise ValueError("La fuente emite en una ventana rectangular (SRECTA): un cono (SCONE) la sustituiría")
        cur = sc.value(doc, kw)
        try:
            default = sc.default(kw)
        except KeyError as exc:
            raise ValueError(f"«{kw}» no es una palabra clave del .in de penhmain") from exc
        vals = list(cur) if cur else list(default)
        if not vals:
            raise ValueError(f"{kw} no tiene valores numéricos que cambiar")
        if self.index >= len(vals):
            raise ValueError(f"{kw} no tiene {self.index + 1} valores")
        old = vals[self.index]
        vals[self.index] = int(round(float(v))) if isinstance(old, int) else float(v)
        sc.set_value(doc, kw, vals)


# ---------------------------------------------------------------------------------------------------------------------
# El plan
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class RunPlan:
    folder: str  # relativa a la carpeta de la campaña o del paralelo («run-01», «v01-SENERG_5e7/run-02»)
    in_text: str
    seed: int
    label: str = ""
    values: dict = field(default_factory=dict)
    files: object | None = None  # simfolder.SimPlan (qué copiar a la carpeta)


@dataclass
class Variant:
    folder: str
    label: str
    values: dict
    runs: list[RunPlan]


@dataclass
class BatchPlan:
    kind: str  # 'paralelo' o 'campana'
    dest: Path
    in_path: Path
    in_name: str
    variants: list[Variant]
    showers: float | None  # lluvias de cada ejecución (None: las del .in)
    dumpp: float
    params: list[Param] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    exe_name: str = "penhmain"
    exe_system: str | None = None  # 'windows', 'linux', 'macos', 'script' (#!) o None (sin penhmain)
    sum_exe: Path | None = None  # penhmain-sum, si se sabe dónde está

    @property
    def runs(self) -> list[RunPlan]:
        return [r for v in self.variants for r in v.runs]

    @property
    def with_wsl(self) -> bool:
        """¿En Windows se lanza con WSL? Un penhmain de Linux o un guion con «#!» (como lo ejecuta ARGUS: 0.16.3)."""
        return self.exe_system in ("linux", "script")

    @property
    def copy_bytes(self) -> int:
        return sum(r.files.copy_bytes for r in self.runs if r.files is not None)

    @property
    def problems(self) -> list[str]:
        out = []
        for r in self.runs:
            if r.files is None:
                continue
            for it in r.files.problems:
                out.append(f"{r.folder}: {lower_first(it.kind_text)} {it.name} ({it.status_text})")
        return out


def _prepare_doc(doc: InDocument, *, showers: float | None, seed: int, dumpp: float, notes: list[str]) -> InDocument:
    d = parse_in_text(write_in_text(doc), doc.path)
    if sc.text(d, "RESUME"):
        sc.set_text(d, "RESUME", None)
        if "RESUME" not in " ".join(notes):
            notes.append("Se ha quitado RESUME: cada ejecución empieza desde cero (con sus propias semillas).")
    sc.set_text(d, "DUMPTO", "dump.dmp")
    sc.set_value(d, "DUMPP", [max(float(dumpp), MIN_DUMPP)])
    if showers is not None:
        sc.set_value(d, "NSIMSH", [float(showers)])
    sc.set_seeds(d, [(-int(seed), 0)])
    return d


def _check_seeds(first: int, count: int) -> None:
    if first < 1 or first + count - 1 > MAX_SEED_PAIR:
        raise ValueError(f"penhmain tiene {MAX_SEED_PAIR} pares de semillas: con {count} ejecuciones, la primera "
                         f"puede ser como mucho la {MAX_SEED_PAIR - count + 1}")


def make_parallel(doc: InDocument, in_path: str | os.PathLike, dest: str | os.PathLike, n: int, *,
                  total_showers: float | None = None, each_showers: float | None = None, first_seed: int = 1,
                  dumpp: float = 60.0) -> BatchPlan:
    """N ejecuciones del mismo .in con semillas independientes (``total_showers``: repartidas entre todas;
    ``each_showers``: las de cada una; ninguna: las del .in en cada una, como MultiRuns)."""
    if n < 2:
        raise ValueError("Hacen falta al menos dos ejecuciones")
    _check_seeds(first_seed, n)
    in_path = Path(in_path)
    notes: list[str] = []
    per = math.ceil(total_showers / n) if total_showers else each_showers
    width = max(2, len(str(n)))
    runs = []
    for i in range(n):
        seed = first_seed + i
        d = _prepare_doc(doc, showers=per, seed=seed, dumpp=dumpp, notes=notes)
        runs.append(RunPlan(f"run-{i + 1:0{width}d}", write_in_text(d), seed))
    return BatchPlan("paralelo", Path(dest), in_path, in_path.name, [Variant("", "", {}, runs)], per, dumpp,
                     notes=notes)


def make_campaign(doc: InDocument, in_path: str | os.PathLike, dest: str | os.PathLike, params: list[Param], *,
                  replicas: int = 1, showers: float | None = None, same_seeds: bool = False, first_seed: int = 1,
                  dumpp: float = 60.0) -> BatchPlan:
    """Una variante por cada combinación de los valores de ``params`` (uno o dos), cada una con ``replicas``
    ejecuciones en paralelo (``showers``: las lluvias de cada variante, repartidas entre sus ejecuciones)."""
    if not params or len(params) > 2:
        raise ValueError("Una campaña cambia uno o dos parámetros")
    if any(not p.values for p in params):
        raise ValueError("Cada parámetro necesita al menos un valor")
    combos = list(itertools.product(*[p.values for p in params]))
    replicas = max(1, int(replicas))
    total_runs = replicas if same_seeds else len(combos) * replicas
    _check_seeds(first_seed, total_runs)
    in_path = Path(in_path)
    notes: list[str] = []
    per = math.ceil(showers / replicas) if showers else None
    width = max(2, len(str(len(combos))))
    rwidth = max(2, len(str(replicas)))
    variants = []
    k = 0
    for i, combo in enumerate(combos):
        vd = parse_in_text(write_in_text(doc), doc.path)
        v_showers, v_dumpp = showers, dumpp
        for p, v in zip(params, combo):
            p.apply(vd, v)
            kw = p.keyword.strip().upper() if p.kind == "keyword" else ""
            if kw == "NSIMSH":  # las lluvias de esta variante (repartidas entre sus ejecuciones)
                v_showers = float(v)
            elif kw == "DUMPP":
                v_dumpp = float(v)
        v_per = math.ceil(v_showers / replicas) if v_showers else None
        label = ", ".join(p.text(v) for p, v in zip(params, combo))
        folder = f"v{i + 1:0{width}d}-" + "-".join(p.slug(v) for p, v in zip(params, combo))
        values = {p.name: (v if p.kind == "material" else float(v)) for p, v in zip(params, combo)}
        runs = []
        for r in range(replicas):
            seed = first_seed + (r if same_seeds else k)
            k += 1
            d = _prepare_doc(vd, showers=v_per, seed=seed, dumpp=v_dumpp, notes=notes)
            sub = f"{folder}/run-{r + 1:0{rwidth}d}" if replicas > 1 else folder
            runs.append(RunPlan(sub, write_in_text(d), seed, label, values))
        variants.append(Variant(folder, label, values, runs))
    if same_seeds:
        notes.append("Todas las variantes usan las mismas semillas: las diferencias entre ellas tienen menos ruido, "
                      "pero sus resultados no son independientes.")
    if any(p.kind == "keyword" and p.keyword.strip().upper() == "NSIMSH" for p in params):
        per = None  # cada variante, las suyas
    return BatchPlan("campana", Path(dest), in_path, in_path.name, variants, per, dumpp, params=params, notes=notes)


def check_variants(batch: BatchPlan, limit: int = 60) -> list[str]:
    """Lo que penhmain no aceptaría de cada variante (como al abrir su .in): errores de lectura (PMRDR0) y de las
    comprobaciones con su geometría (con sus PARINP). Una ejecución de cada variante (las demás solo cambian las
    semillas)."""
    from ..pengeom.compiler import compile_geometry
    from ..pengeom.parser import load_geometry
    from .validate import validate_case

    out: list[str] = []
    folder = batch.in_path.parent
    geos: dict[tuple, object] = {}
    for v in batch.variants:
        if not v.runs or len(out) >= limit:
            break
        d = parse_in_text(v.runs[0].in_text, str(batch.in_path))
        who = v.label or v.folder or "las ejecuciones"
        errs = [x.message for x in d.diagnostics.errors]
        geo = None
        if d.model.ok and d.model.geomfn.strip():
            key = (d.model.geomfn.strip(), tuple(d.model.parinp_list()))
            if key not in geos:
                try:
                    gdoc = load_geometry(folder / key[0])
                    geos[key] = compile_geometry(gdoc, parinp=list(key[1]), base_dir=folder)
                except (OSError, ValueError) as exc:
                    geos[key] = str(exc)
            geo = geos[key]
            if isinstance(geo, str):
                errs.append(f"la geometría: {geo}")
                geo = None
            elif not geo.ok:
                errs += [f"la geometría: {x.message}" for x in geo.diagnostics.errors][:3]
        if d.model.ok:
            errs += [x.message for x in validate_case(d, geo, folder).errors]
        out += [f"{who}: {e}" for e in errs[:4]]
    return out


def plan_files(batch: BatchPlan, *, database=None, extra_dirs=None, penhmain=None, endf_dir=None) -> None:
    """Qué hay que copiar a la carpeta de cada ejecución (lo que lea su .in, y penhmain), sin copiar nada."""
    from .simfolder import plan_folder

    if penhmain:
        from .programs import exe_info

        info = exe_info(penhmain)
        batch.exe_name = Path(penhmain).name
        batch.exe_system = info.system
    # Los nombres, como los entiende ese penhmain (uno de Linux o de macOS no entiende las rutas de Windows: 0.16.3).
    linux = batch.exe_system in ("linux", "macos", "script")
    for r in batch.runs:
        d = parse_in_text(r.in_text, str(batch.in_path))
        plan = plan_folder(batch.in_path, d, batch.dest / r.folder, database=database, extra_dirs=extra_dirs,
                           penhmain=penhmain, endf_dir=endf_dir, linux=linux)
        plan.items = [it for it in plan.items if it.kind != "in"]  # el .in de cada ejecución lo escribe ARGUS
        r.files = plan


# ---------------------------------------------------------------------------------------------------------------------
# Órdenes para lanzarlas
# ---------------------------------------------------------------------------------------------------------------------

def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _sh_single(s: str) -> str:
    """Nombre para una orden de sh que va entre comillas dobles: entre comillas simples de sh."""
    return "'" + s.replace("'", "'\\''").replace('"', '\\"') + "'"


def _dq(s: str) -> str:
    """Texto para ir entre comillas dobles en sh."""
    return "".join("\\" + ch if ch in '"$`\\' else ch for ch in s)


def launch_sh(batch: BatchPlan, jobs: int) -> str:
    folders = " ".join(_sh_quote(r.folder) for r in batch.runs)
    exe = batch.exe_name if batch.exe_system != "windows" else "penhmain"
    # Dentro de la orden entre comillas simples de «sh -c»: nombres entre comillas dobles. Una carpeta que ya se
    # lanzó (tiene su salida o sus resultados) no se vuelve a lanzar: empezaría desde cero encima de lo que hay.
    inner = (f'cd "$1" || exit 1; if [ -e {OUTPUT_NAME} ] || [ -e penhmain-res.dat ]; then '
             f'echo "Ya lanzada (no se vuelve a lanzar): $1"; else ./"{_dq(exe)}" < "{_dq(batch.in_name)}" '
             f'> {OUTPUT_NAME} 2>&1; echo "Terminada: $1"; fi')
    return f"""#!/bin/sh
# Generado por ARGUS: lanza las {len(batch.runs)} simulaciones de esta carpeta, {jobs} a la vez (Linux, macOS o WSL).
# Uso: sh lanzar.sh [cuántas a la vez]
# Cada una escribe sus resultados en su carpeta; ARGUS los va enseñando (Resultados → Abrir los resultados de una
# carpeta… y elija esta). Las que ya se lanzaron (tienen {OUTPUT_NAME} o penhmain-res.dat) no se vuelven a lanzar.
cd "$(dirname "$0")" || exit 1
J=${{1:-{jobs}}}
export GFORTRAN_UNBUFFERED_PRECONNECTED=y
for d in {folders}; do printf '%s\\n' "$d"; done | xargs -P "$J" -I{{}} sh -c {_sh_quote(inner)} _ {{}}
"""


def launch_ps1(batch: BatchPlan, jobs: int) -> str:
    folders = ", ".join("'" + r.folder.replace("/", "\\").replace("'", "''") + "'" for r in batch.runs)
    in_name = batch.in_name.replace("'", "''")
    # Start-Process une los argumentos con espacios sin comillas: se le pasa una sola cadena ya entrecomillada, y la
    # carpeta de cada ejecución como carpeta de trabajo (WSL empieza en la misma carpeta, vista desde Linux).
    if batch.with_wsl:
        sh_cmd = (f"GFORTRAN_UNBUFFERED_PRECONNECTED=y ./{_sh_single(batch.exe_name)} < {_sh_single(batch.in_name)} "
                  f"> {OUTPUT_NAME} 2>&1")
        args = f'-e sh -c "{sh_cmd}"'.replace("'", "''")
        start = (f"Start-Process -FilePath 'wsl.exe' -ArgumentList '{args}' -WorkingDirectory $dir "
                 "-WindowStyle Hidden -PassThru")
        what = "con WSL (penhmain de Linux)"
    else:
        exe = (batch.exe_name if batch.exe_system == "windows" else "penhmain.exe").replace("'", "''")
        # cmd /c quita la primera y la última comilla: todo entre un par de comillas más (nombres con espacios o
        # paréntesis, como «penhmain (1).exe»).
        args = f'/c ""{exe}" < "{in_name}" > {OUTPUT_NAME} 2>&1"'
        start = (f"Start-Process -FilePath 'cmd.exe' -ArgumentList '{args}' -WorkingDirectory $dir "
                 "-WindowStyle Hidden -PassThru")
        what = "con el penhmain de Windows"
    return f"""# Generado por ARGUS: lanza las {len(batch.runs)} simulaciones de esta carpeta, {jobs} a la vez, {what}.
# Uso: lanzar.bat (o, en PowerShell: .\\lanzar.ps1 -Jobs 4)
# Las que ya se lanzaron (tienen {OUTPUT_NAME} o penhmain-res.dat) no se vuelven a lanzar.
param([int]$Jobs = {jobs})
$env:GFORTRAN_UNBUFFERED_PRECONNECTED = 'y'
Set-Location -LiteralPath $PSScriptRoot
if ($PSScriptRoot.StartsWith('\\\\')) {{
    Write-Host 'Esta carpeta está en la red (\\\\servidor\\...): cmd.exe y WSL no pueden trabajar en ella.'
    Write-Host 'Cópiela a un disco del equipo o asígnele una letra de unidad.'
    exit 1
}}
$runs = @({folders})
$procs = @()
foreach ($r in $runs) {{
    $dir = Join-Path $PSScriptRoot $r
    $out = Join-Path $dir '{OUTPUT_NAME}'
    $res = Join-Path $dir 'penhmain-res.dat'
    if ((Test-Path -LiteralPath $out) -or (Test-Path -LiteralPath $res)) {{
        Write-Host "Ya lanzada (no se vuelve a lanzar): $r"
        continue
    }}
    while (@($procs | Where-Object {{ -not $_.HasExited }}).Count -ge $Jobs) {{ Start-Sleep -Seconds 2 }}
    $procs += {start}
    Write-Host "Lanzada: $r"
}}
if ($procs.Count) {{ $procs | Wait-Process }}
Write-Host 'Terminadas todas.'
"""


LAUNCH_BAT = """@echo off
rem Generado por ARGUS: lanza las simulaciones de esta carpeta (lanzar.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lanzar.ps1" %*
pause
"""
# Como lanzar.bat: con la política de guiones de Windows por defecto, «.\\combinar.ps1» no se ejecuta (0.16.3).
COMBINE_BAT = """@echo off
rem Generado por ARGUS: combina las ejecuciones con penhmain-sum (combinar.ps1).
rem Uso: combinar.bat {usage} (o arrastre penhmain-sum sobre este fichero)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0combinar.ps1" %*
pause
"""


def combine_scripts(batch: BatchPlan) -> dict[str, str]:
    """Órdenes para combinar las ejecuciones de cada variante con penhmain-sum (en una carpeta nueva «suma»)."""
    out = {}
    groups = [(v.folder, [r for r in v.runs]) for v in batch.variants if len(v.runs) > 1]
    if not groups:
        return out
    sh = ["#!/bin/sh", "# Generado por ARGUS: combina los volcados de las ejecuciones con penhmain-sum (en «suma»).",
          "# Uso: sh combinar.sh /ruta/a/penhmain-sum",
          'SUM=${1:-penhmain-sum}',
          'case "$SUM" in /*) ;; */*) SUM="$PWD/$SUM";; *) SUM="$(command -v "$SUM" || echo "$PWD/$SUM")";; esac',
          'cd "$(dirname "$0")" || exit 1',
          '[ -x "$SUM" ] || { echo "No se encuentra penhmain-sum: $SUM"; exit 1; }']
    ps = ["# Generado por ARGUS: combina los volcados de las ejecuciones con penhmain-sum (en «suma»).",
          "# Uso: combinar.bat C:\\ruta\\penhmain-sum.exe (o, en PowerShell: .\\combinar.ps1 -Sum "
          "C:\\ruta\\penhmain-sum.exe)",
          "param([string]$Sum = 'penhmain-sum.exe')",
          "if (Test-Path -LiteralPath $Sum) { $Sum = (Resolve-Path -LiteralPath $Sum).Path }",
          "elseif (-not (Get-Command $Sum -ErrorAction SilentlyContinue)) { Write-Host \"No se encuentra penhmain-sum: "
          "$Sum\"; exit 1 }",
          "Set-Location -LiteralPath $PSScriptRoot"]
    for folder, runs in groups:
        base = folder or "."
        target = f"{base}/suma" if folder else "suma"
        depth = "../" * (target.count("/") + 1)
        dumps = [depth + r.folder + "/dump.dmp" for r in runs]
        sh.append(f"mkdir {_sh_quote(target)} 2>/dev/null || {{ echo 'Ya existe {target}: no se toca.'; exit 1; }}")
        listing = "\\n".join(dumps)
        sh.append(f"(cd {_sh_quote(target)} && printf '{listing}\\n' | \"$SUM\" > penhmain-sum.out 2>&1) && "
                  f"echo 'Combinadas en {target}'")
        win_target = target.replace("/", "\\")
        ps.append(f"if (Test-Path -LiteralPath '{win_target}') {{ Write-Host 'Ya existe {win_target}: no se toca.'; "
                  "exit 1 }")
        ps.append(f"New-Item -ItemType Directory -Path '{win_target}' | Out-Null")
        ps_list = "`n".join(d.replace("/", "\\") for d in dumps)
        ps.append(f"Push-Location -LiteralPath '{win_target}'; \"{ps_list}\" | & $Sum > penhmain-sum.out 2>&1; "
                  "$ok = $?; Pop-Location")
        ps.append(f"if ($ok) {{ Write-Host 'Combinadas en {win_target}' }} else {{ Write-Host 'penhmain-sum ha "
                  f"fallado: vea {win_target}\\penhmain-sum.out' }}")
    out["combinar.sh"] = "\n".join(sh) + "\n"
    if batch.with_wsl:
        # penhmain (y penhmain-sum) de Linux en Windows: se combina con combinar.sh dentro de WSL.
        out["combinar.ps1"] = ("# Generado por ARGUS: combina con penhmain-sum de Linux (WSL) usando combinar.sh.\n"
                               "# Uso: combinar.bat /ruta/de/linux/penhmain-sum (o, en PowerShell: .\\combinar.ps1 "
                               "-Sum /ruta/de/linux/penhmain-sum)\n"
                               "param([string]$Sum = 'penhmain-sum')\n"
                               "wsl.exe --cd $PSScriptRoot -e sh combinar.sh $Sum\n")
        out["combinar.bat"] = COMBINE_BAT.format(usage="/ruta/de/linux/penhmain-sum")
    else:
        out["combinar.ps1"] = "\n".join(ps) + "\n"
        out["combinar.bat"] = COMBINE_BAT.format(usage="C:\\ruta\\penhmain-sum.exe")
    return out


def manifest(batch: BatchPlan) -> dict:
    """El índice de la carpeta: lo lee la ventana de resultados para encontrar y comparar las ejecuciones."""
    base = {"argus": 1, "kind": batch.kind, "input": batch.in_name, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dumpp": batch.dumpp, "showers_per_run": batch.showers}
    if batch.kind == "paralelo":
        base["runs"] = [{"folder": r.folder, "seed": [-r.seed, 0]} for r in batch.runs]
        return base
    base["parameters"] = [{"name": p.name, "kind": p.kind, "keyword": p.keyword.strip(), "index": p.index,
                           "number": p.number, "unit": p.unit,
                           "values": [v if p.kind == "material" else float(v) for v in p.values]}
                          for p in batch.params]
    base["runs"] = [{"folder": v.folder, "label": v.label, "values": v.values,
                     "runs": [r.folder for r in v.runs] if len(v.runs) > 1 else []} for v in batch.variants]
    return base


def sub_manifest(v: Variant, batch: BatchPlan) -> dict:
    """Índice de las ejecuciones en paralelo de una variante de la campaña."""
    return {"argus": 1, "kind": "paralelo", "input": batch.in_name, "label": v.label,
            "runs": [{"folder": r.folder.split("/", 1)[1], "seed": [-r.seed, 0]} for r in v.runs]}


# ---------------------------------------------------------------------------------------------------------------------
# Escribir
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Written:
    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)  # ya existían: no se han tocado
    failed: list[str] = field(default_factory=list)
    cancelled: bool = False


def write_batch(batch: BatchPlan, jobs: int, progress: Callable[[int, int, str], None] | None = None,
                cancel: Callable[[], bool] | None = None) -> Written:
    """Crea las carpetas, copia lo que necesita cada ejecución, escribe sus .in, las órdenes y el índice.

    Nunca sobrescribe: lo que ya exista se deja como está (y se cuenta en ``skipped``).
    """
    from ..core.files import latin1_text, write_new
    from .simfolder import execute

    w = Written()
    batch.dest.mkdir(parents=True, exist_ok=True)
    runs = batch.runs
    total = len(runs)

    def put(path: Path, data: bytes, executable: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if write_new(path, data, executable=executable):
                w.created.append(path)
            else:
                w.skipped.append(path)
        except OSError as exc:
            w.failed.append(f"{path.name}: {exc.strerror or exc}")

    for i, r in enumerate(runs):
        if cancel is not None and cancel():
            w.cancelled = True
            break
        if progress is not None:
            progress(i, total, r.folder)
        folder = batch.dest / r.folder
        folder.mkdir(parents=True, exist_ok=True)
        put(folder / batch.in_name, latin1_text(r.in_text)[0].encode("latin-1", errors="replace"))
        if r.files is not None:
            copied = execute(r.files, cancel=cancel)
            w.created.extend(copied)
            if r.files.cancelled:
                w.cancelled = True
                break
            w.failed.extend(f"{r.folder}/{it.name}: {it.note}" for it in r.files.failed)
    if progress is not None:
        progress(total, total, "")
    if w.cancelled:
        return w
    put(batch.dest / "lanzar.sh", launch_sh(batch, jobs).encode("utf-8"), executable=True)
    put(batch.dest / "lanzar.ps1", _ps1_bytes(launch_ps1(batch, jobs)))
    put(batch.dest / "lanzar.bat", LAUNCH_BAT.replace("\n", "\r\n").encode("latin-1"))
    for name, text in combine_scripts(batch).items():
        if name.endswith(".ps1"):
            data = _ps1_bytes(text)
        elif name.endswith(".bat"):  # como lanzar.bat: fin de línea de Windows y solo ASCII
            data = text.replace("\n", "\r\n").encode("latin-1")
        else:
            data = text.encode("utf-8")
        put(batch.dest / name, data, executable=name.endswith(".sh"))
    name = MANIFEST_PARALLEL if batch.kind == "paralelo" else MANIFEST_CAMPAIGN
    put(batch.dest / name, json.dumps(manifest(batch), ensure_ascii=False, indent=2).encode("utf-8"))
    if batch.kind == "campana":
        for v in batch.variants:
            if len(v.runs) > 1:
                put(batch.dest / v.folder / MANIFEST_PARALLEL,
                    json.dumps(sub_manifest(v, batch), ensure_ascii=False, indent=2).encode("utf-8"))
    return w


def _ps1_bytes(text: str) -> bytes:
    """Windows PowerShell 5.1 lee como ANSI los .ps1 sin BOM: UTF-8 con BOM (y fin de línea de Windows)."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig")


def default_batch_folder(in_path: str | os.PathLike, kind: str) -> Path:
    """Carpeta nueva junto al .in: «caso-paralelo» o «caso-campana» (con un número si ya existe)."""
    p = Path(in_path)
    base = p.parent / f"{p.stem}-{kind}"
    cand, k = base, 2
    while cand.exists():
        cand = p.parent / f"{p.stem}-{kind}{k}"
        k += 1
    return cand


def parse_values(text: str) -> list[float]:
    """«50 100 150», «50; 100», «0,5 1,5» (coma decimal), «50:150:25» (de 50 a 150 de 25 en 25) o «50-150/5»
    (5 valores)."""
    out: list[float] = []
    toks: list[str] = []
    for tok in text.replace(";", " ").split():
        # La coma es decimal («0,5»); varias comas en un mismo trozo separan valores («50,100,150»).
        if tok.count(",") == 1 and "." not in tok:
            toks.append(tok.replace(",", "."))
        else:
            toks.extend(t for t in tok.split(",") if t)
    for tok in toks:
        tok = tok.strip()
        if not tok:
            continue
        if tok.count(":") == 2:
            a, b, c = (float(x) for x in tok.split(":"))
            if c <= 0 or b < a:
                raise ValueError(f"«{tok}»: de A a B con paso C (A:B:C)")
            n = int(math.floor((b - a) / c + 1e-9)) + 1
            out.extend(a + k * c for k in range(n))
        elif "/" in tok and "-" in tok[1:]:
            rng, n = tok.split("/")
            i = rng.index("-", 1)
            a, b = float(rng[:i]), float(rng[i + 1:])
            m = int(n)
            if m < 2:
                raise ValueError(f"«{tok}»: al menos 2 valores")
            out.extend(a + (b - a) * k / (m - 1) for k in range(m))
        else:
            out.append(float(tok))
    return out


__all__ = ["BatchPlan", "MANIFEST_CAMPAIGN", "MANIFEST_PARALLEL", "Param", "RunPlan", "Variant", "Written",
           "check_variants", "combine_scripts", "default_batch_folder", "launch_ps1", "launch_sh", "make_campaign",
           "make_parallel", "manifest", "parse_values", "plan_files", "write_batch"]
