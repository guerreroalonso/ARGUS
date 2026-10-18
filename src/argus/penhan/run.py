"""Ejecutar penhmain en la carpeta del .in (0.13.0, ADR-017).

Rafael simula en la carpeta de cada caso, con el .in, el .geo, los .mat, los ENDF y penhmain.x juntos. El botón
«Ejecutar» de ARGUS hace eso:

* comprueba que están los ficheros que leerá penhmain (los mismos que «Preparar la carpeta de simulación», pero en
  la propia carpeta del .in) y copia allí los que se encuentran en otro sitio (los ENDF de la base de datos de
  penhan, el .nuc de una fuente radiactiva…);
* copia allí el penhmain elegido (``penhmain.x`` de Linux, ``penhmain.exe`` de Windows…), y nunca sustituye otro con
  el mismo nombre sin que lo diga el usuario (el de antes se queda como «.bak»);
* si en la carpeta hay resultados de una simulación anterior, los guarda en una subcarpeta
  («anterior-AAAA-MM-DD_HH-MM-SS») o deja que penhmain los sobrescriba, según elija el usuario (con RESUME y su
  volcado, penhmain sigue la simulación anterior y no se pregunta);
* escribe un guion que ejecuta ``penhmain < caso.in`` («argus-ejecutar.sh», «.bat» o «.command») y lo abre en un
  terminal, donde se ve la simulación al momento: en Linux el terminal que haya, en macOS el Terminal y en Windows
  el que se elija (0.14.0): **el terminal de Linux** (WSL: el guion de sh corre en la distribución de WSL, en una
  ventana de consola) o **la consola de Windows** (cmd). Un penhmain de Linux solo puede ir en el de Linux; uno de
  Windows, en los dos (en el de Linux, WSL lo ejecuta como programa de Windows). La ventana se queda abierta al
  terminar y dice si penhmain ha acabado con un error. Con el guion de sh (Linux, WSL, macOS) la salida en pantalla se
  guarda también en «penhmain.out» (como en las simulaciones en paralelo), donde ARGUS ve los errores; en la consola
  de Windows solo se ve en ella (penhmain escribe además su registro, penhmain.dat).

No se borra nada: los resultados anteriores se mueven y el guion solo se reescribe si lo escribió ARGUS (si se ha
cambiado a mano, el de antes se guarda como «.bak»).
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ..core.files import CopyCancelled, copy_new, same_content, write_new
from ..core.numfmt import lower_first, plural
from .inputfile import InDocument
from .programs import (
    HOST,
    NO_WINDOW,
    ExeInfo,
    Launch,
    WslStatus,
    exe_info,
    find_program,
    launch_for,
    wsl_executable,
    wsl_status,
)
from .simfolder import SimPlan, classify, plan_folder

RUN_OUTPUT = "penhmain.out"  # la salida en pantalla (el mismo nombre que en las simulaciones en paralelo)
RUN_LOG = "argus-ejecutar.log"  # sin terminal: lo que escribe el guion
DEFAULT_DUMP = "dump.dmp"  # el volcado de penhmain sin DUMPTO (penhmain.f)
# Palabras clave con el nombre de un fichero que escribe penhmain (además de los de nombre fijo).
OUTPUT_NAME_KEYWORDS = ("IDSPC", "IDPSF", "IDFLNC", "IDAGEF", "ADSPC", "EDSPC")
LAUNCHER_STEM = "argus-ejecutar"
LAUNCHER_MARK = "Generado por ARGUS"
STASH_PREFIX = "anterior-"
STASH_NOTE = "argus-anterior.txt"  # en la subcarpeta de los resultados anteriores (ARGUS no la toma por otra ejecución)
NOT_PENHMAIN = ("penhmain-sum", "penhmain_sum", "penhmain-layout")
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
SHELL_LINUX = "linux"  # Windows: el terminal de Linux (WSL)
SHELL_WINDOWS = "windows"  # Windows: la consola de Windows (cmd)
RUNNING_MARGIN = 120.0  # s: resultados más recientes que esto (y sin terminar) = penhmain está simulando
STARTING_MARGIN = 3600.0  # s: un registro sin resultados (preparando los datos: con protones, minutos) tan reciente
# Ficheros que abre penhmain (o PENGEOM) con el nombre que dice el .in o la geometría, guardados en Latin-1: con un
# penhmain que no es de Windows, un nombre con tildes no es el del fichero en el disco (en UTF-8).
LATIN1_KINDS = ("geo", "include", "ct", "mat", "nuc", "psf", "eprof", "dump")


# ---------------------------------------------------------------------------------------------------------------------
# penhmain: el elegido y el de la carpeta
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class ExeChoice:
    """Qué penhmain se va a ejecutar y qué hay que hacer para tenerlo en la carpeta del caso.

    ``status``:

    * ``inside``: el elegido ya está en la carpeta (es ese fichero);
    * ``same``: en la carpeta hay uno con su nombre y el mismo contenido;
    * ``copy``: se copiará a la carpeta;
    * ``conflict``: en la carpeta hay OTRO fichero con su nombre (se pregunta qué hacer);
    * ``missing``: el elegido no existe;
    * ``none``: no se ha elegido ninguno y en la carpeta no hay.
    """

    chosen: Path | None
    target: Path | None
    status: str
    info: ExeInfo | None = None
    launch: Launch | None = None
    libs: list[Path] = field(default_factory=list)  # DLL de MinGW junto a un penhmain.exe que no es estático
    from_folder: bool = False  # no había ninguno elegido: se usa el que ya estaba en la carpeta

    @property
    def runnable(self) -> bool:
        return self.status in ("inside", "same", "copy", "conflict") and self.launch is not None and self.launch.ok

    @property
    def problem(self) -> str:
        """Por qué no se puede ejecutar ('' si se puede)."""
        if self.status == "none":
            return "falta elegir el ejecutable de penhmain (penhmain.x, penhmain.exe…)"
        if self.status == "missing":
            return f"no se encuentra penhmain en {self.chosen}"
        if self.launch is not None and not self.launch.ok:
            if self.launch.pending:  # aún no se sabe (se le está preguntando a WSL)
                return self.launch.note
            return f"penhmain no se puede ejecutar en este ordenador: {self.launch.note}"
        return ""


def folder_penhmain(folder: str | os.PathLike) -> list[Path]:
    """Los ejecutables de penhmain que hay en la carpeta (los que se pueden ejecutar aquí y los más recientes,
    primero)."""
    return find_program([Path(folder)], "penhmain", exclude=NOT_PENHMAIN)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _libraries(exe: Path) -> list[Path]:
    from .simfolder import _exe_libraries

    return _exe_libraries(exe)


def choose_exe(folder: str | os.PathLike, configured: str | os.PathLike | None, *,
               launch_fn: Callable[[ExeInfo], Launch] = launch_for) -> ExeChoice:
    """El penhmain que se ejecutará en ``folder``: el elegido en Opciones (``configured``), que se copia allí, o, si
    no hay ninguno elegido, el que ya está en la carpeta."""
    folder = Path(folder)
    if configured:
        chosen = Path(configured)
        if not chosen.is_file():
            return ExeChoice(chosen, None, "missing")
        target = folder / chosen.name
        if _same_dir(chosen.parent, folder):
            status = "inside"
        elif os.path.lexists(target):
            status = "same" if same_content(chosen, target) else "conflict"
        else:
            status = "copy"
        # Se ejecutará el de la carpeta: si es otro (conflict), lo que diga el usuario; mientras, el elegido.
        info = exe_info(target if status in ("inside", "same") else chosen)
        libs = ([lib for lib in _libraries(chosen) if not (folder / lib.name).exists()]
                if status in ("copy", "conflict") else [])
        return ExeChoice(chosen, target, status, info, launch_fn(info), libs)
    found = folder_penhmain(folder)
    if not found:
        return ExeChoice(None, None, "none")
    exe = found[0]
    info = exe_info(exe)
    return ExeChoice(exe, exe, "inside", info, launch_fn(info), from_folder=True)


def install_exe(choice: ExeChoice, *, replace: bool = False) -> list[Path]:
    """Copia el penhmain elegido (y sus DLL) a la carpeta; devuelve lo copiado.

    Con ``status == 'conflict'`` solo si ``replace``: el que había se guarda antes como «.bak» y se copia el elegido.
    Sin ``replace``, en un conflicto no se toca nada (se usará el de la carpeta: ``use_folder_exe``).
    """
    out: list[Path] = []
    if choice.chosen is None or choice.target is None:
        return out
    if choice.status == "conflict":
        if not replace:
            return out
        # El de antes cambia de nombre («.bak», o «.2.bak»… si ya hay uno): no se borra nada.
        bak = backup_name(choice.target)
        os.rename(choice.target, bak)
        try:
            if not copy_new(choice.chosen, choice.target, executable=True):
                raise OSError(f"{choice.target.name} ha vuelto a aparecer mientras se copiaba")
        except BaseException:
            if not os.path.lexists(choice.target):
                os.rename(bak, choice.target)
            raise
        out.append(choice.target)
    elif choice.status == "copy":
        if copy_new(choice.chosen, choice.target, executable=True):
            out.append(choice.target)
        elif not same_content(choice.chosen, choice.target):
            raise OSError(f"en la carpeta ha aparecido otro {choice.target.name} mientras se copiaba")
    for lib in choice.libs:
        if copy_new(lib, choice.target.parent / lib.name):
            out.append(choice.target.parent / lib.name)
    choice.status = "same"
    choice.info = exe_info(choice.target)
    return out


def backup_name(path: Path) -> Path:
    """Un nombre libre para guardar ``path`` sin borrarlo: «nombre.bak», «nombre.2.bak», «nombre.3.bak»… (siempre
    acabado en «.bak», para que no se tome por el programa)."""
    cand = path.with_name(path.name + ".bak")
    k = 2
    while os.path.lexists(cand):
        cand = path.with_name(f"{path.name}.{k}.bak")
        k += 1
    return cand


def use_folder_exe(choice: ExeChoice, launch_fn: Callable[[ExeInfo], Launch] = launch_for) -> ExeChoice:
    """En un conflicto, usar el penhmain que ya hay en la carpeta (con su nombre) en lugar del elegido."""
    target = choice.target
    info = exe_info(target)
    return ExeChoice(target, target, "inside", info, launch_fn(info))


# ---------------------------------------------------------------------------------------------------------------------
# Resultados de una simulación anterior en la carpeta
# ---------------------------------------------------------------------------------------------------------------------

def _rel_key(name: str) -> str:
    """Un nombre relativo a la carpeta del caso, para compararlo con otros («./a.dat» es «a.dat», «sub\\a.dat» es
    «sub/a.dat»; en Windows, sin mirar las mayúsculas)."""
    return os.path.normcase(posixpath.normpath(name.strip().replace("\\", "/")))


def relative_name(folder: str | os.PathLike, path: str | os.PathLike) -> str:
    """«salida/psf.dat»: el nombre de ``path`` dentro de ``folder`` (solo su nombre si no está dentro)."""
    try:
        return Path(path).relative_to(folder).as_posix()
    except ValueError:
        return Path(path).name


def _input_names(files: SimPlan) -> set[str]:
    """Lo que lee penhmain (no son resultados aunque lo parezcan: un espacio de fases de entrada, el volcado de
    RESUME…), con su ruta relativa a la carpeta (``_rel_key``)."""
    return {_rel_key(it.name) for it in files.items}


def output_names(doc: InDocument | None) -> list[str]:
    """Los nombres de los ficheros que escribe penhmain y se pueden cambiar en el .in (espectros de los detectores,
    espacio de fases…) y el volcado: el de DUMPTO o, sin DUMPTO, «dump.dmp» si penhmain vuelca (DUMPP < 1e10, también
    sin DUMPP: penhmain.f)."""
    from ..results.folder import dump_period
    from . import sections as sc

    if doc is None:
        return []
    out = [ln.text.strip(" ") for kw in OUTPUT_NAME_KEYWORDS for ln in doc.find(sc.kw6(kw)) if ln.text.strip(" ")]
    dump = sc.text(doc, "DUMPTO")
    if dump:
        out.append(dump.strip())
    else:
        dp = sc.value(doc, "DUMPP")
        try:
            dumpp = float(dp[0]) if dp else None
        except (TypeError, ValueError):
            dumpp = None
        if dump_period(dumpp) is not None:
            out.append(DEFAULT_DUMP)
    return out


def previous_outputs(folder: str | os.PathLike, doc: InDocument | None = None,
                     inputs: set[str] | None = None) -> list[Path]:
    """Los resultados de penhmain que ya hay en la carpeta y que la simulación nueva sobrescribiría: los que
    reconoce ARGUS por su contenido con el nombre que les da penhmain (o el del .in), el volcado y la salida en
    pantalla; también los que el .in escribe en una subcarpeta («IDPSF salida/psf.dat»: 0.16.3). Una copia
    renombrada por el usuario («E100_depth-dose.dat») no se toca, ni lo que lee penhmain (``inputs``, con
    ``_rel_key``), ni lo que el .in escribe fuera de la carpeta (una ruta que sube de carpeta o absoluta)."""
    from ..results.folder import ResultsFolder

    folder = Path(folder)
    inputs = inputs or set()
    out: dict[str, Path] = {}
    try:
        rf = ResultsFolder(folder, runs=False)
    except OSError:
        return []
    named = {_rel_key(n) for n in output_names(doc)}
    for f in rf.files:
        if not f.renamed or _rel_key(f.path.name) in named:
            out[_rel_key(f.path.name)] = f.path
    names = [RUN_OUTPUT, RUN_LOG, *output_names(doc)]
    for n in names:
        if classify(n) != "ok":  # fuera de la carpeta del caso: no es de ella
            continue
        p = folder / posixpath.normpath(n.strip().replace("\\", "/"))
        if p.is_file():
            out.setdefault(_rel_key(n), p)
    return sorted((p for k, p in out.items() if k not in inputs), key=lambda p: relative_name(folder, p).lower())


def stash_outputs(folder: str | os.PathLike, files: list[Path], when: float | None = None) -> Path:
    """Mueve ``files`` a una subcarpeta nueva «anterior-AAAA-MM-DD_HH-MM-SS» (con la fecha del más reciente) y la
    devuelve. Uno de una subcarpeta del caso va a la misma subcarpeta dentro de ella («salida/psf.dat» →
    «anterior-…/salida/psf.dat»; la del caso se queda: penhmain escribe en ella). No sobrescribe nada; si algo no se
    puede mover (abierto por otro programa…), se para y lo dice. La nota de ARGUS se escribe antes de mover nada:
    aunque se pare a medias, la subcarpeta no se toma por otra ejecución."""
    folder = Path(folder)
    files = [f for f in files if os.path.lexists(f)]  # lo que ya no está (borrado o movido mientras) no cuenta
    if when is None:
        stamps = []
        for f in files:
            try:
                stamps.append(f.stat().st_mtime)
            except OSError:
                pass
        when = max(stamps) if stamps else time.time()
    base = STASH_PREFIX + time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(when))
    dest = folder / base
    k = 2
    while os.path.lexists(dest):
        dest = folder / f"{base}-{k}"
        k += 1
    dest.mkdir()
    note = ("Resultados de una simulación anterior de esta carpeta, guardados aquí por ARGUS antes de volver a "
            "ejecutar penhmain.\r\n\r\n" + "\r\n".join(relative_name(folder, f) for f in files) + "\r\n")
    try:
        write_new(dest / STASH_NOTE, note.encode("utf-8"))
    except OSError:
        pass
    for f in files:
        rel = relative_name(folder, f)
        target = dest / rel
        if os.path.lexists(target):
            continue  # dos ficheros con el mismo nombre en distinta mayúscula: el segundo se queda
        try:
            if "/" in rel:
                target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(f, target)  # misma carpeta madre y destino nuevo: nunca sobrescribe
        except FileNotFoundError:
            continue  # ya no está
        except OSError as exc:
            raise OSError(f"no se ha podido mover {rel} a {dest.name} ({exc.strerror or exc}); lo movido hasta "
                          f"ahora está en esa carpeta") from exc
    return dest


def is_stash(folder: str | os.PathLike) -> bool:
    """¿Es una subcarpeta de resultados anteriores guardados por ARGUS?"""
    return (Path(folder) / STASH_NOTE).is_file()


def running_state(folder: str | os.PathLike, now: float | None = None,
                  in_path: str | os.PathLike | None = None) -> str | None:
    """Si parece que penhmain está simulando ahora en la carpeta, lo que se sabe de ello; si no, None.

    Lo mismo que dice la ventana de resultados («Simulando»: resultados sin terminar escritos hace menos que el
    periodo de los volcados, DUMPP, más un margen), siempre que algo de penhmain se haya escrito hace poco: los
    resultados en ese plazo o, si aún no hay resultados (preparando los datos), el registro en la última hora."""
    from ..results.folder import STALE_MARGIN, ResultsFolder, dump_period

    now = time.time() if now is None else now
    try:
        rf = ResultsFolder(folder, in_path, runs=False)
    except OSError:
        return None
    st = rf.state(now)
    if st.status not in ("simulando", "empezando"):
        return None
    period = dump_period(rf.input.dumpp if rf.input is not None else None)
    window = max(RUNNING_MARGIN, (period or 0.0) + STALE_MARGIN)
    for f in rf.files:
        age = now - f.mtime
        if f.kind == "res" and age <= window:
            return st.text
        if f.kind in ("log", "run-log") and age <= (STARTING_MARGIN if st.status == "empezando" else window):
            return st.text
    return None


# ---------------------------------------------------------------------------------------------------------------------
# El plan
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class RunPlan:
    in_path: Path
    folder: Path
    files: SimPlan  # lo que lee penhmain, en la propia carpeta del .in ('same', 'copy' desde otro sitio, 'missing')
    exe: ExeChoice
    previous: list[Path] = field(default_factory=list)
    resume: str | None = None  # RESUME con su volcado en la carpeta: penhmain sigue la simulación anterior
    resume_missing: str | None = None  # RESUME sin un volcado que se pueda seguir: penhmain empieza desde cero
    resume_note: str = ""  # por qué («no existe», «está vacío»…)
    resume_diffs: list[str] = field(default_factory=list)  # rejillas e intervalos del volcado que no son los del .in
    running: str | None = None  # penhmain parece estar simulando ya en esta carpeta
    notes: list[str] = field(default_factory=list)
    shell: str | None = None  # Windows: SHELL_LINUX (WSL) o SHELL_WINDOWS (cmd); None: el de siempre

    @property
    def to_copy(self) -> list:
        """Ficheros que penhmain necesita y se copiarán a la carpeta (ENDF de la base de datos, .nuc…)."""
        return [it for it in self.files.to_copy if it.kind not in ("in", "exe", "dll", "txt", "ctjson")]

    @property
    def missing(self) -> list:
        return [it for it in self.files.missing if it.kind not in ("exe", "dll")]

    @property
    def missing_mats(self) -> list:
        return [it for it in self.missing if it.kind == "mat"]

    @property
    def problems(self) -> list[str]:
        """Lo que impide ejecutar (sin contar los .mat que faltan, que se pueden generar)."""
        return self.problems_with(self.exe)

    def name_problems(self, exe: ExeChoice | None = None) -> list[str]:
        """Nombres del .in (o de la geometría) que el penhmain ``exe`` no encontraría (0.16.3):

        * los que empiezan por un blanco (la columna 8): penhmain los lee con (A20) y los abre tal cual;
        * con un penhmain de Linux o de macOS (también con WSL), las rutas de Windows («C:\\m\\agua.mat»,
          «mats\\agua.mat»): para Linux son el nombre de un fichero de su carpeta;
        * con un penhmain que no es de Windows, los que tienen tildes o eñes: el .in y la geometría se guardan en
          Latin-1 y penhmain busca esos bytes, pero en Linux, en WSL y en macOS el nombre del fichero va en UTF-8."""
        exe = self.exe if exe is None else exe
        out = [it.lead_problem for it in self.files.blank]
        info = exe.info
        if info is None or info.system in (None, "windows"):
            return out
        who = ("un penhmain de macOS" if info.system == "macos" else "un penhmain de Linux") + (
            " (WSL)" if HOST == "windows" else "")
        for it in self.files.winpath:
            if PureWindowsPath(it.name).drive:
                out.append(f"{who} no puede abrir «{it.name}»: para Linux, una ruta de Windows es el nombre de un "
                           "fichero más; copie el fichero a la carpeta del caso y cítelo solo por su nombre")
            else:
                out.append(f"{who} no encontraría «{it.name}»: en Linux «\\» no separa carpetas; escriba "
                           f"«{it.name.replace(chr(92), '/')}»")
        for it in self.files.items:
            if it.kind in LATIN1_KINDS and not it.name.isascii():
                where = "la geometría" if it.kind in ("include", "ct") else "el .in"
                out.append(f"{who} no encontraría «{it.name}»: {where} se guarda en Latin-1 y ese nombre tiene "
                           "tildes o eñes; cámbielo por uno sin ellas")
        return out

    def problems_with(self, exe: ExeChoice) -> list[str]:
        """Lo que impide ejecutar con el penhmain ``exe`` (en un conflicto, el de la carpeta o el elegido)."""
        out = []
        p = exe.problem
        if p:
            out.append(p)
        endf = [it for it in self.missing if it.kind == "endf"]
        for it in self.missing:
            if it.kind in ("mat", "endf"):
                continue
            out.append(f"falta {it.name} ({lower_first(it.kind_text)}" + (f": {it.note})" if it.note else ")"))
        if endf:
            names = ", ".join(it.name for it in endf[:4]) + (f" y {len(endf) - 4} más" if len(endf) > 4 else "")
            one = len(endf) == 1
            out.append(f"{'falta' if one else 'faltan'} {plural(len(endf), 'fichero')} ENDF ({names}): "
                       f"diga dónde {'está' if one else 'están'} en Opciones → Programas de penhan («Ficheros ENDF») o "
                       "con «Generar .mat y ENDF…» («Carpeta de los ENDF…»)")
        for it in self.files.outside:
            if it.required:
                out.append(f"{it.name}: la ruta sube de carpeta o depende de la unidad actual")
        out += self.name_problems(exe)
        net = windows_path_problem(self.folder) if HOST == "windows" else None
        wsl = (exe.launch is not None and exe.launch.how == "wsl") or self.shell == SHELL_LINUX
        if net == "carpeta de red":
            out.append("la carpeta está en la red (\\\\servidor\\…): " + (
                "WSL no puede trabajar en ella; copie el caso a un disco del ordenador" if wsl else
                "la consola de Windows no puede trabajar en ella; cópiela a un disco del ordenador o asígnele una "
                "letra de unidad"))
        elif net == "unidad de red" and wsl:
            out.append("la carpeta está en una unidad de red: WSL no puede trabajar en ella; copie el caso a un disco "
                       "del ordenador")
        return out


def plan_run(in_path: str | os.PathLike, doc: InDocument, *, penhmain: str | os.PathLike | None = None,
             database: str | os.PathLike | None = None, extra_dirs: list | None = None,
             launch_fn: Callable[[ExeInfo], Launch] = launch_for, endf_dir: str | os.PathLike | None = None) -> RunPlan:
    """Qué hace falta para ejecutar penhmain con ``in_path`` en su carpeta (sin copiar ni mover nada todavía)."""
    in_path = Path(in_path)
    folder = in_path.parent
    exe = choose_exe(folder, penhmain, launch_fn=launch_fn)
    # Los nombres, como los entiende el penhmain que se ejecutará: uno de Linux o de macOS (también con WSL) no
    # entiende las rutas de Windows y sí las de Linux (0.16.3).
    linux = exe.info is not None and exe.info.system in ("linux", "macos", "script")
    files = plan_folder(in_path, doc, folder, database=database, extra_dirs=extra_dirs, endf_dir=endf_dir,
                        linux=linux)
    plan = RunPlan(in_path, folder, files, exe)
    line = doc.first("RESUME")
    raw = line.text.rstrip() if line is not None else ""
    res = raw.strip()
    if res and raw[:1].isspace():
        # penhmain abre el nombre con el blanco del principio: no encuentra el volcado, empieza desde cero y
        # sobrescribe los resultados anteriores (se ofrecen para guardarlos). Es un problema (``name_problems``).
        plan.resume_missing = res
        plan.resume_note = "no se encontraría (el nombre empieza por un espacio)"
    elif res:
        from .dumpfile import read_dump, tally_differences

        # Un volcado vacío («Empty.»: penhmain lo deja así si no había nada que seguir) o dañado no se sigue: penhmain
        # empieza desde cero y sobrescribe los resultados anteriores (se guardan en «anterior-…»).
        info = read_dump(folder / res.replace("\\", "/"))
        if info.usable:
            plan.resume = res
            plan.resume_diffs = tally_differences(info, doc)  # penhmain sigue con los del volcado
        else:
            plan.resume_missing = res
            plan.resume_note = {"missing": "no existe", "empty": "está vacío"}.get(
                info.state, "está dañado o incompleto")
    inputs = _input_names(files)
    if exe.target is not None:
        inputs.add(_rel_key(exe.target.name))
    if plan.resume is None:
        plan.previous = previous_outputs(folder, doc, inputs)
    plan.running = running_state(folder, in_path=in_path)
    plan.notes += files.notes
    return plan


def copy_needed(plan: RunPlan, progress: Callable[[int, int, str], None] | None = None,
                cancel: Callable[[], bool] | None = None) -> list[Path]:
    """Copia a la carpeta del .in lo que penhmain necesita y estaba en otro sitio (nunca sobrescribe). Si se cancela,
    :class:`CopyCancelled`; si algo no se ha podido copiar (sin sitio en el disco…), OSError con lo que falta: en los
    dos casos no hay que ejecutar penhmain (se pararía sin esos ficheros)."""
    from .simfolder import execute

    keep = {id(it) for it in plan.to_copy}
    sub = SimPlan(plan.folder, [it for it in plan.files.items if id(it) in keep])
    out = execute(sub, progress, cancel)
    if sub.cancelled:
        raise CopyCancelled("se ha cancelado la copia: no se ejecuta penhmain")
    if sub.failed:
        raise OSError("no se ha podido copiar " + "; ".join(
            f"{it.name}" + (f" ({it.note})" if it.note else "") for it in sub.failed))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# El guion y el terminal
# ---------------------------------------------------------------------------------------------------------------------

def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _bat_quote(s: str) -> str:
    return '"' + s.replace("%", "%%") + '"'


def _bat_text(s: str) -> str:
    """Texto fuera de comillas en una línea de un .bat (title, echo): sin que «&», «|», «<», «>», «^» o «%» de un
    nombre de fichero se tomen por órdenes de cmd.exe."""
    for c in "^&|<>":
        s = s.replace(c, "^" + c)
    return s.replace("%", "%%")


def launcher_kind(info: ExeInfo | None, host: str = HOST, shell: str | None = None) -> str:
    """'bat' (penhmain de Windows en la consola de Windows), 'command' (macOS) o 'sh' (Linux, o en el terminal de
    Linux de WSL)."""
    if host == "windows" and shell != SHELL_LINUX and (info is None or info.system == "windows"):
        return "bat"
    if host == "macos":
        return "command"
    return "sh"


ERROR_MARK = "ARGUS: penhmain ha terminado con el codigo de salida"  # en penhmain.out (results.folder.log_error)


def launcher_text(kind: str, in_name: str, exe_name: str, windows_exe: bool = False) -> str:
    """El guion que ejecuta ``exe_name < in_name`` en su carpeta, dice si penhmain ha acabado con un error y espera al
    final. El .bat va en UTF-8 con «chcp 65001» (cmd.exe lee cada línea con la página de códigos de la consola: con la
    OEM, 850 en español, un nombre con tildes no llegaría bien); lo que escribe ARGUS, sin tildes (las consolas no
    siempre las muestran bien). ``windows_exe``: el guion de sh ejecuta un penhmain de Windows (en el terminal de Linux
    de WSL): WSL le pasa GFORTRAN_UNBUFFERED_PRECONNECTED si está en WSLENV."""
    if kind == "bat":
        return "\r\n".join([
            "@echo off",
            "chcp 65001 >nul",
            f"rem {LAUNCHER_MARK}: ejecuta penhmain con {_bat_text(in_name)} en esta carpeta (se puede volver a usar).",
            'cd /d "%~dp0"',
            f"title ARGUS - {_bat_text(in_name)}",
            f"echo ARGUS: {_bat_text(exe_name)} ^< {_bat_text(in_name)}",
            "echo.",
            "set GFORTRAN_UNBUFFERED_PRECONNECTED=y",  # la salida al momento, también si no la toma por una consola
            f"{_bat_quote(exe_name)} < {_bat_quote(in_name)}",
            "set ARGUS_RC=%errorlevel%",
            "echo.",
            'if not "%ARGUS_RC%"=="0" goto argus_error',
            "echo ARGUS: penhmain ha terminado; los resultados estan en esta carpeta.",
            "goto argus_end",
            ":argus_error",
            "echo ARGUS: penhmain ha terminado con un error (codigo %ARGUS_RC%): mire los mensajes de arriba.",
            ":argus_end",
            "pause",
            "",
        ])
    title = f"ARGUS - {in_name}"
    return "\n".join([
        "#!/bin/sh",
        f"# {LAUNCHER_MARK}: ejecuta penhmain con {in_name} en esta carpeta (se puede volver a usar: sh {LAUNCHER_STEM}"
        f".{'command' if kind == 'command' else 'sh'}).",
        "# La salida en pantalla se guarda tambien en " + RUN_OUTPUT + " (ARGUS la lee para avisar de los errores).",
        'cd "$(dirname "$0")" || exit 1',
        f"printf '\\033]0;%s\\007' {_sh_quote(title)}",
        f"echo {_sh_quote('ARGUS: ./' + exe_name + ' < ' + in_name)}",
        'echo "Carpeta: $(pwd)"',
        "echo",
        f"chmod +x {_sh_quote('./' + exe_name)} 2>/dev/null",
        *(['WSLENV="GFORTRAN_UNBUFFERED_PRECONNECTED${WSLENV:+:$WSLENV}"; export WSLENV'] if windows_exe else []),
        'argus_rc_file="${TMPDIR:-/tmp}/argus-rc-$$"',
        "{",
        f"  GFORTRAN_UNBUFFERED_PRECONNECTED=y ./{_sh_quote(exe_name)} < {_sh_quote(in_name)} 2>&1",
        "  argus_rc=$?",
        '  echo "$argus_rc" > "$argus_rc_file"',
        f'  if [ "$argus_rc" -ne 0 ]; then echo "{ERROR_MARK} $argus_rc"; fi',
        f"}} | tee {RUN_OUTPUT}",
        'argus_rc=$(cat "$argus_rc_file" 2>/dev/null || echo 0)',
        'rm -f "$argus_rc_file"',
        "echo",
        'if [ "$argus_rc" = "0" ]; then',
        "  echo 'ARGUS: penhmain ha terminado; los resultados estan en esta carpeta.'",
        "else",
        '  echo "ARGUS: penhmain ha terminado con un error (codigo $argus_rc): mire los mensajes de arriba."',
        "fi",
        "printf 'Pulse Intro para cerrar esta ventana. '",
        "read dummy",
        "",
    ])


def write_launcher(folder: str | os.PathLike, kind: str, in_name: str, exe_name: str,
                   windows_exe: bool = False) -> Path:
    """Escribe el guion en la carpeta (o lo deja si ya está igual) y lo devuelve. Uno anterior de ARGUS se sustituye;
    un fichero con ese nombre que no es de ARGUS no se toca (se usa otro nombre)."""
    folder = Path(folder)
    ext = {"bat": ".bat", "command": ".command", "sh": ".sh"}[kind]
    data = launcher_text(kind, in_name, exe_name, windows_exe and kind == "sh").encode("utf-8", errors="replace")
    executable = kind != "bat"
    k = 1
    while True:
        target = folder / (LAUNCHER_STEM + ("" if k == 1 else f"-{k}") + ext)
        if not os.path.lexists(target):
            if write_new(target, data, executable=executable):
                return target
        else:
            try:
                old = target.read_bytes()
            except OSError:
                old = b""
            if old == data:
                if executable:
                    try:
                        os.chmod(target, 0o755)
                    except OSError:
                        pass
                return target
            if LAUNCHER_MARK.encode() in old[:400]:
                if not _launcher_like(old, kind):
                    # Retocado a mano (otra orden, «ulimit»…): se guarda antes de escribir el de ARGUS.
                    try:
                        shutil.copy2(target, backup_name(target))
                    except OSError:
                        k += 1
                        continue
                tmp = target.with_name(target.name + ".argus-tmp")
                tmp.write_bytes(data)
                if executable:
                    os.chmod(tmp, 0o755)
                os.replace(tmp, target)
                return target
        k += 1
        if k > 50:
            raise OSError("no se ha podido escribir el guion para ejecutar penhmain")


# La línea que ejecuta penhmain en cada guion (con el ejecutable y el .in entre comillas: ``launcher_text``).
_BAT_RUN = re.compile(r'^"(.*)" < "(.*)"$', re.MULTILINE)
_SH_QUOTED = r"'((?:[^']|'\\'')*)'"
_SH_RUN = re.compile(rf"^  GFORTRAN_UNBUFFERED_PRECONNECTED=y \./{_SH_QUOTED} < {_SH_QUOTED} 2>&1$", re.MULTILINE)


def _launcher_like(data: bytes, kind: str) -> bool:
    """¿Es ``data`` un guion de ARGUS sin retocar (el de otro .in u otro penhmain)? Se sacan de su línea de penhmain el
    ejecutable y el .in y se compara con el guion que escribe ARGUS para ellos: cualquier otro cambio (una orden más,
    otra línea de penhmain, una comentada, «nice»…) es un retoque del usuario, y el guion se guarda como «.bak» antes
    de sustituirlo (0.16.3; antes bastaba con que cada línea empezara como una de ARGUS). El de una versión anterior
    de ARGUS que fuera distinto también se guarda: ante la duda, no se pierde nada."""
    try:
        text = data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return False
    if kind == "bat":
        found = [(exe.replace("%%", "%"), name.replace("%%", "%")) for exe, name in _BAT_RUN.findall(text)]
    else:
        found = [(exe.replace("'\\''", "'"), name.replace("'\\''", "'")) for exe, name in _SH_RUN.findall(text)]
    return any(launcher_text(kind, in_name, exe_name, windows_exe).replace("\r\n", "\n") == text
               for exe_name, in_name in found for windows_exe in (False, True))


@dataclass
class Terminal:
    argv: list[str]
    cwd: Path
    name: str  # cómo se llama el terminal, para los mensajes («penhmain se está ejecutando en <name>»)
    new_console: bool = False  # Windows: una consola nueva
    background: bool = False  # sin terminal: en segundo plano (la salida va a penhmain.out)


# Terminales de Linux, en orden de preferencia, y cómo se les pasa la orden.
_LINUX_TERMINALS: list[tuple[str, Callable[[str], list[str]]]] = [
    ("x-terminal-emulator", lambda s: ["-e", "sh", s]),
    ("gnome-terminal", lambda s: ["--", "sh", s]),
    ("konsole", lambda s: ["-e", "sh", s]),
    ("xfce4-terminal", lambda s: ["-x", "sh", s]),
    ("mate-terminal", lambda s: ["-x", "sh", s]),
    ("tilix", lambda s: ["-e", f"sh {_sh_quote(s)}"]),
    ("kitty", lambda s: ["sh", s]),
    ("alacritty", lambda s: ["-e", "sh", s]),
    ("wezterm", lambda s: ["start", "--", "sh", s]),
    ("foot", lambda s: ["sh", s]),
    ("lxterminal", lambda s: ["-e", f"sh {_sh_quote(s)}"]),
    ("xterm", lambda s: ["-e", "sh", s]),
]


def linux_terminal_name(status: WslStatus | None = None) -> str:
    """«el terminal de Linux (WSL: Ubuntu 22.04.4 LTS)»."""
    distro = status.note if status is not None and status.ok and status.note else ""
    return "el terminal de Linux (WSL" + (f": {distro})" if distro else ")")


def terminal_command(script: str | os.PathLike, launch: Launch, *, host: str = HOST,
                     which: Callable[[str], str | None] = shutil.which,
                     env: dict | None = None, shell: str | None = None,
                     wsl: Callable[[], WslStatus] | None = None) -> Terminal:
    """Cómo abrir un terminal que ejecute ``script`` (en su carpeta). Sin terminal en Linux, en segundo plano.

    En Windows, ``shell``: SHELL_LINUX (el guion de sh en WSL, en una ventana de consola nueva) o SHELL_WINDOWS (el .bat
    en la consola de Windows); sin decirlo, el de Linux para un penhmain de Linux y la consola para uno de Windows."""
    script = Path(script)
    folder = script.parent
    env = os.environ if env is None else env
    if host == "windows":
        if launch.how == "wsl" or shell == SHELL_LINUX:
            exe = str(wsl_executable() or "wsl.exe")
            known = pg_wsl_cached() if wsl is None else wsl()
            return Terminal([exe, "--cd", str(folder), "-e", "sh", script.name], folder, linux_terminal_name(known),
                            new_console=True)
        return Terminal(["cmd.exe", "/c", script.name], folder, "la consola de Windows (cmd)", new_console=True)
    if host == "macos":
        return Terminal(["open", "-a", "Terminal", str(script)], folder, "el Terminal de macOS")
    pref = (env.get("TERMINAL") or "").strip()
    if pref and which(pref.split()[0]):
        exe = which(pref.split()[0])
        return Terminal([exe, *pref.split()[1:], "-e", "sh", str(script)], folder, Path(pref.split()[0]).name)
    for name, args in _LINUX_TERMINALS:
        exe = which(name)
        if exe:
            return Terminal([exe, *args(str(script))], folder, name)
    return Terminal(["sh", str(script)], folder, "", background=True)


@dataclass
class RunHandle:
    """Una ejecución lanzada desde ARGUS (el proceso del terminal o de penhmain en segundo plano)."""

    process: subprocess.Popen | None
    folder: Path
    in_name: str
    terminal: Terminal
    started: float = field(default_factory=time.time)

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


def open_terminal(term: Terminal) -> subprocess.Popen:
    """Lanza el terminal (o, sin terminal, el guion en segundo plano con su salida en penhmain.out)."""
    if os.name == "nt" and term.new_console:
        # Una consola nueva, con SUS entrada y salida: sin stdin, stdout ni stderr. Con uno solo, CPython pone
        # STARTF_USESTDHANDLES con los otros dos de ARGUS: la salida de penhmain iría a la consola de ARGUS y «pause»
        # leería de NUL (la ventana se cerraría sola).
        return subprocess.Popen(term.argv, cwd=str(term.cwd), creationflags=CREATE_NEW_CONSOLE)
    kwargs: dict = {"cwd": str(term.cwd), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = NO_WINDOW
    else:
        kwargs["start_new_session"] = True  # cerrar ARGUS no cierra el terminal ni penhmain
    if term.background:
        log = open(Path(term.cwd) / RUN_LOG, "ab")  # el guion ya guarda la salida en penhmain.out
        kwargs.update(stdout=log, stderr=subprocess.STDOUT)
        try:
            return subprocess.Popen(term.argv, **kwargs)
        finally:
            log.close()
    if os.name != "nt":
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.Popen(term.argv, **kwargs)


def start(plan: RunPlan, *, host: str = HOST, which: Callable[[str], str | None] = shutil.which,
          env: dict | None = None, popen: Callable[[Terminal], subprocess.Popen] = open_terminal) -> RunHandle:
    """Escribe el guion y abre el terminal. ``plan.exe`` tiene que estar ya en la carpeta (``install_exe``)."""
    exe = plan.exe
    if not exe.runnable or exe.target is None:
        raise ValueError(exe.problem or "no se puede ejecutar penhmain")
    shell = plan.shell if host == "windows" else None
    if shell == SHELL_WINDOWS and exe.info is not None and exe.info.system != "windows":
        raise ValueError("un penhmain de Linux solo se puede ejecutar en el terminal de Linux (WSL)")
    kind = launcher_kind(exe.info, host, shell)
    windows_exe = exe.info is not None and exe.info.system == "windows"
    script = write_launcher(plan.folder, kind, plan.in_path.name, exe.target.name, windows_exe=windows_exe)
    term = terminal_command(script, exe.launch, host=host, which=which, env=env, shell=shell)
    proc = popen(term)
    return RunHandle(proc, plan.folder, plan.in_path.name, term)


# ---------------------------------------------------------------------------------------------------------------------
# En Windows: el terminal de Linux (WSL) o la consola de Windows
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ShellChoice:
    key: str  # SHELL_LINUX o SHELL_WINDOWS
    label: str  # «Linux (WSL: Ubuntu 22.04.4 LTS)», «Windows (cmd)»
    ok: bool
    note: str  # lo que hace, o por qué no se puede


def pg_wsl_cached() -> WslStatus | None:
    """Lo que se sabe ya de WSL (sin preguntarle: None si aún no se sabe)."""
    from . import programs

    return programs._WSL_CACHE.get("status")


def shell_choices(info: ExeInfo | None, *, host: str = HOST,
                  wsl: Callable[[], WslStatus] | None = None) -> list[ShellChoice]:
    """En Windows, los dos terminales en que se puede ver penhmain (vacío en Linux y macOS, donde hay uno).

    Un penhmain de Linux solo puede ir en el terminal de Linux (WSL). Uno de Windows, en la consola de Windows o en el
    de Linux, si WSL puede ejecutar programas de Windows (interop): entonces la salida se guarda también en
    penhmain.out. ``wsl``: el estado de WSL (por defecto se pregunta, unos segundos la primera vez)."""
    if host != "windows":
        return []
    st = (wsl or wsl_status)()
    linux_exe = info is not None and info.system in ("linux", "script")
    win_exe = info is not None and info.system == "windows"
    name = f"Linux (WSL: {st.note})" if st.ok and st.note else "Linux (WSL)"
    if not st.ok:
        linux = ShellChoice(SHELL_LINUX, name, False, st.note)
    elif linux_exe:
        linux = ShellChoice(SHELL_LINUX, name, True, "penhmain es un programa de Linux: corre en WSL y su salida se "
                                                     "guarda también en penhmain.out")
    elif win_exe and not st.interop:
        linux = ShellChoice(SHELL_LINUX, name, False, "WSL no puede ejecutar programas de Windows (la integración "
                                                      "con Windows, «interop», está desactivada)")
    else:
        linux = ShellChoice(SHELL_LINUX, name, bool(win_exe), "WSL ejecuta el penhmain de Windows y su salida se "
                                                              "guarda también en penhmain.out")
    if linux_exe:
        windows = ShellChoice(SHELL_WINDOWS, "Windows (cmd)", False, "penhmain es un programa de Linux: en Windows "
                                                                     "solo se ejecuta con WSL, en el terminal de Linux")
    else:
        windows = ShellChoice(SHELL_WINDOWS, "Windows (cmd)", bool(win_exe), "la consola de Windows: la salida solo "
                                                                             "se ve en ella")
    return [linux, windows]


def default_shell(choices: list[ShellChoice], preferred: str | None = None) -> str | None:
    """El terminal que se usa: el preferido si se puede; si no, el primero que se puede (con un penhmain de Windows,
    la consola de Windows antes que el de Linux, salvo que se prefiera el de Linux)."""
    ok = [c.key for c in choices if c.ok]
    if not ok:
        return None
    if preferred in ok:
        return preferred
    return SHELL_WINDOWS if SHELL_WINDOWS in ok else ok[0]


def describe_terminal(launch: Launch | None, *, host: str = HOST, shell: str | None = None,
                      which: Callable[[str], str | None] = shutil.which, env: dict | None = None) -> Terminal:
    """El terminal que se abriría (sin abrirlo), para decirlo antes de ejecutar."""
    return terminal_command(Path("argus-ejecutar.sh"), launch or Launch("native", ""), host=host, which=which,
                            env=env, shell=shell)


def is_launcher(path: str | os.PathLike) -> bool:
    p = Path(path)
    if not p.name.lower().startswith(LAUNCHER_STEM):
        return False
    try:
        return LAUNCHER_MARK.encode() in p.read_bytes()[:400]
    except OSError:
        return False


def windows_path_problem(folder: str | os.PathLike) -> str | None:
    """Rutas de Windows con las que no funciona la consola: «carpeta de red» (\\\\servidor\\…: ni la consola ni
    WSL) o «unidad de red» (una letra asignada a una carpeta de red: la consola sí, WSL no, porque solo monta los discos
    del ordenador)."""
    w = PureWindowsPath(str(folder))
    if str(folder).startswith("\\\\") or (w.drive and not w.drive.endswith(":")):
        return "carpeta de red"
    if os.name == "nt" and w.drive.endswith(":"):
        try:
            import ctypes

            if ctypes.windll.kernel32.GetDriveTypeW(w.drive + "\\") == 4:  # DRIVE_REMOTE
                return "unidad de red"
        except (AttributeError, OSError):
            pass
    return None


__all__ = ["CREATE_NEW_CONSOLE", "DEFAULT_DUMP", "ERROR_MARK", "OUTPUT_NAME_KEYWORDS", "SHELL_LINUX", "SHELL_WINDOWS",
           "CopyCancelled", "ExeChoice", "LAUNCHER_MARK", "LAUNCHER_STEM", "RUN_LOG", "RUN_OUTPUT", "RunHandle",
           "RunPlan", "STASH_NOTE", "STASH_PREFIX", "ShellChoice", "Terminal", "backup_name", "choose_exe",
           "copy_needed", "default_shell", "describe_terminal", "folder_penhmain", "install_exe", "is_launcher",
           "is_stash", "launcher_kind", "launcher_text", "linux_terminal_name", "open_terminal", "output_names",
           "plan_run", "previous_outputs", "relative_name", "running_state", "shell_choices", "start",
           "stash_outputs", "terminal_command", "use_folder_exe", "windows_path_problem", "write_launcher"]
