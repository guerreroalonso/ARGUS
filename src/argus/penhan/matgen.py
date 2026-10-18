"""Generar con el penhmat del usuario los .mat que faltan y copiar los ENDF que necesitan (ADR-013).

penhmat es interactivo: pregunta la composición (opción 1) o el número de la lista de PENELOPE (opción 2), el
plasmón y el nombre del fichero de salida. ARGUS le da las respuestas de ``materiales.txt`` por la entrada
estándar, sin que el usuario tenga que escribir nada, para los materiales cuya composición conoce:

* **composición propia** (0.14.0): la que el usuario ha dado en ARGUS (fórmula o elementos, densidad y energía media
  de excitación), guardada en el .in junto a su MFNAME (:mod:`argus.penhan.composition`): opción 1;
* **tejidos de un TAC importado con ARGUS**: la composición de la ficha ``.ct.json`` y la densidad media de sus
  vóxeles, con la energía media de excitación de la regla de ARGUS (ADR-011);
* **materiales de la lista de PENELOPE** (``database/material-list.txt``), por el nombre que tienen en el panel
  Materiales (el comentario de su línea MFNAME: «… (PENELOPE 278)» o el nombre exacto de la lista): opción 2 con su
  número.

De los demás no sabe la composición: se le da en el panel Materiales (menú contextual → «Composición…») o se generan a
mano (los pasos generales están en materiales.txt).

penhmat se ejecuta en su carpeta ``database`` (lee ``./pdfiles``, ``./pdhan`` y ``./ENDF_data``) y escribe allí
el .mat con un nombre temporal único, ``argus-xxxxxxxx.mat``: corto, sin tildes ni ruta, así sirve con cualquier
sistema y con WSL, y no tropieza con los 80 caracteres que admite penhmat. ARGUS comprueba que es un .mat
completo, lo copia con su nombre a la carpeta del .in sin sustituir nunca otro fichero y borra el temporal.

Con protones o alfas, penhmain abre por su nombre, en la carpeta donde se ejecuta, los ficheros ENDF que lista
cada .mat al final: se copian desde la carpeta de los ENDF del usuario (Opciones → Programas de penhan; si no la ha
elegido, ``database/ENDF_data``) o desde la carpeta del .mat, tampoco sustituyendo ninguno.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..core.files import CopyCancelled, copy_new, same_content
from ..core.numfmt import plural
from .inputfile import InDocument
from .materials import (
    find_material_list,
    hadron_data_needed,
    listed_number,
    mat_filename_problem,
    match_material,
    material_list_or_builtin,
    materials_table,
    read_mat_header,
    read_mat_nuclear,
    stale_reason,
)
from .programs import Launch, last_lines, run_program

TEMP_PREFIX = "argus-"
PENHMAT_TIMEOUT = 1800.0  # s: un material con muchos elementos tarda como mucho unos minutos
_IRETRN = re.compile(r"IRETRN\s*=\s*(-?\d+)")


@dataclass
class MatJob:
    number: int  # material (el primero que usa este fichero)
    fname: str  # como en MFNAME
    what: str = ""  # qué es y de dónde sale su composición
    answers: Callable[[str], str] | None = None  # respuestas a penhmat con este nombre de salida
    reason: str = ""  # por qué no se puede generar
    status: str = ""  # '', 'done', 'exists', 'failed', 'cancelled'
    message: str = ""
    target: Path | None = None
    unknown: bool = False  # ARGUS no sabe de qué está hecho: hay que asignarle un material (0.16.0)

    @property
    def possible(self) -> bool:
        return self.answers is not None


@dataclass
class EndfJob:
    name: str
    source: Path | None
    materials: list[str] = field(default_factory=list)  # los .mat que lo listan
    status: str = ""  # '', 'done', 'same', 'conflict', 'failed', 'missing'
    message: str = ""
    size: int = 0


@dataclass
class MatPlan:
    folder: Path
    mats: list[MatJob] = field(default_factory=list)
    endf: list[EndfJob] = field(default_factory=list)
    hadrons: bool | None = True
    cancelled: bool = False

    @property
    def to_generate(self) -> list[MatJob]:
        return [j for j in self.mats if j.possible and not j.status]

    @property
    def impossible(self) -> list[MatJob]:
        return [j for j in self.mats if not j.possible]

    @property
    def endf_to_copy(self) -> list[EndfJob]:
        return [e for e in self.endf if e.source is not None and not e.status]

    @property
    def endf_missing(self) -> list[EndfJob]:
        return [e for e in self.endf if e.source is None]

    @property
    def endf_bytes(self) -> int:
        return sum(e.size for e in self.endf_to_copy)

    @property
    def empty(self) -> bool:
        return not (self.mats or self.endf)


def _where_ok(name: str) -> bool:
    from .simfolder import classify

    return classify(name) == "ok"


def list_answers(penelope_id: int) -> Callable[[str], str]:
    """Respuestas a penhmat para un material de la lista de PENELOPE (opción 2)."""
    return lambda out: f"2\n{int(penelope_id)}\n2\n{out}\n\n"


def material_list(database: str | os.PathLike | None, folder: str | os.PathLike | None = None
                  ) -> list[tuple[int, str]]:
    """La lista de materiales de PENELOPE de la base de datos del usuario (o la que se encuentre)."""
    path = Path(database) / "material-list.txt" if database else None
    if path is None or not path.is_file():
        path = find_material_list(folder)
    return material_list_or_builtin(path)


def plan_materials(doc: InDocument, geo, folder: str | os.PathLike, database: str | os.PathLike | None = None,
                   mlist: list[tuple[int, str]] | None = None, endf_dir: str | os.PathLike | None = None) -> MatPlan:
    """Los .mat que faltan en ``folder`` (la carpeta del .in) y cómo generarlos, y los ENDF que faltan (``endf_dir``:
    la carpeta de los ENDF del usuario, si no es la de la base de datos)."""
    folder = Path(folder)
    mlist = material_list(database, folder) if mlist is None else mlist
    plan = MatPlan(folder, hadrons=hadron_data_needed(doc, database))
    seen: set[str] = set()
    for r in materials_table(doc, geo, folder, database):
        if not r.declared or not r.fname:
            continue
        key = r.fname.lower() if os.name == "nt" else r.fname
        if key in seen:
            continue
        seen.add(key)
        if r.exists and not r.stale:
            continue
        job = MatJob(r.number, r.fname)
        if r.stale:
            job.reason = stale_reason(r)
        elif "/" in r.fname or "\\" in r.fname or not _where_ok(r.fname):
            job.reason = "su nombre en el .in tiene una carpeta: ARGUS solo genera los .mat de la carpeta del .in"
        elif (problem := mat_filename_problem(r.fname)) is not None:
            job.reason = problem
        elif r.recipe is not None:
            comp = r.recipe
            job.what = f"composición propia: {comp.summary()}"
            job.answers = lambda out, comp=comp, name=(r.description or r.fname): comp.penhmat_input(out, name)
        elif r.recipe_error:
            job.reason = (f"su composición en el .in no se entiende ({r.recipe_error}): corríjala en el panel "
                          "Materiales (menú contextual → «Composición…»)")
        elif r.tissue is not None:
            from ..ct.calibration import penhmat_composition, penhmat_input

            t = r.tissue
            rho = t.voxel_density or t.density
            if penhmat_composition(t):
                job.what = f"tejido del TAC «{t.name}»: composición de la importación y {rho:.4g} g/cm³"
            else:
                job.what = f"tejido del TAC «{t.name}»: n.º {t.penelope_id} de la lista de PENELOPE"
            job.answers = lambda out, t=t: penhmat_input(t, out, density=t.voxel_density)
        else:
            item = match_material(r.description, mlist) if r.description else None
            if item is None and not mlist and (k := listed_number(r.description)) is not None:
                item = (k, "")  # sin la lista: el número está en el nombre (penhmat no necesita la lista)
            if item is not None:
                job.what = f"n.º {item[0]} de la lista de PENELOPE" + (f" ({item[1]})" if item[1] else "")
                job.answers = list_answers(item[0])
            elif r.description and not mlist:
                job.reason = ("no se encuentra la lista de materiales de PENELOPE (database/material-list.txt) para "
                              "saber su número")
            else:
                job.unknown = True
                job.reason = ("ARGUS no sabe de qué está hecho: asígnele un material de la lista de PENELOPE o de sus "
                              "materiales personalizados («Asignar materiales…»)")
        plan.mats.append(job)
    plan.endf = plan_endf(doc, folder, database, endf_dir)
    return plan


def endf_sources(database: str | os.PathLike | None = None, endf_dir: str | os.PathLike | None = None) -> list[Path]:
    """Dónde se buscan los ENDF (además de junto a cada .mat): la carpeta de los ENDF del usuario y la de la base de
    datos de penhan (``database/ENDF_data``)."""
    out: list[Path] = []
    for d in (Path(endf_dir) if endf_dir else None, Path(database) / "ENDF_data" if database else None):
        if d is not None and d not in out:
            out.append(d)
    return out


def plan_endf(doc: InDocument, folder: str | os.PathLike, database: str | os.PathLike | None = None,
              endf_dir: str | os.PathLike | None = None) -> list[EndfJob]:
    """Ficheros ENDF que listan los .mat del .in y no están en ``folder`` (vacío si penhmain no los lee)."""
    folder = Path(folder)
    if hadron_data_needed(doc, database) is False:
        return []
    from .simfolder import find_endf

    dirs = endf_sources(database, endf_dir)
    jobs: dict[str, EndfJob] = {}
    for md in doc.model.materials:
        name = (md.fname or "").strip()
        if not name:
            continue
        p = Path(name) if not _where_ok(name) and Path(name).is_absolute() else folder / name.replace("\\", "/")
        nuc = read_mat_nuclear(p) if p.is_file() else None
        if nuc is None:
            continue
        for ename, _iza, _ab in nuc.isotopes:
            if (folder / ename).is_file():
                continue
            job = jobs.get(ename)
            if job is None:
                src = find_endf(ename, [p.parent, *dirs])  # (también con otras mayúsculas)
                job = jobs[ename] = EndfJob(ename, src)
                if src is None:
                    job.message = (f"no está en la carpeta de los ENDF ({', '.join(str(d) for d in dirs)})" if dirs
                                   else "no se sabe dónde están los ficheros ENDF: elija su carpeta (Opciones → "
                                        "Programas de penhan…)")
                else:
                    try:
                        job.size = src.stat().st_size
                    except OSError:
                        job.source = None
            if name not in job.materials:
                job.materials.append(name)
    return list(jobs.values())


_FORTRAN_ERROR = re.compile(r"Fortran runtime error:\s*(.*)", re.IGNORECASE)
_FORTRAN_AT = re.compile(r"At line \d+ of file \S+(?: \(unit = -?\d+, file = '([^']*)'\))?", re.IGNORECASE)


def fortran_error(text: str) -> tuple[str, str, str]:
    """El error de Fortran de la salida de un programa de penhan compilado con gfortran: (la línea «At line … of file
    …», el error, el fichero que leía o escribía), p. ej. («At line 516 of file penelope.f (unit = 3, file =
    './pdfiles/pdesi11.p14')», «End of file», «./pdfiles/pdesi11.p14»); vacíos si no hay ninguno. La entrada estándar
    (las respuestas de ARGUS) es «stdin»."""
    m = _FORTRAN_ERROR.search(text)
    if m is None:
        return "", "", ""
    found = list(_FORTRAN_AT.finditer(text[:m.start()]))
    at = found[-1] if found else None  # la más cercana al error
    return (at.group(0) if at is not None else "", m.group(1).strip(),
            (at.group(1) or "").strip() if at is not None else "")


def _tail(text: str) -> str:
    """Las últimas líneas de la salida y, si no están entre ellas, las del error de Fortran (gfortran lo escribe
    antes de que salga lo que quedaba por escribir de la salida normal: se quedaba fuera; 0.16.3)."""
    tail = last_lines(text)
    at, what, _f = fortran_error(text)
    if what and what not in tail:
        tail = "\n".join(x for x in (at, f"Fortran runtime error: {what}") if x) + "\n…\n" + tail
    return tail


def penhmat_problem(res, out: Path) -> str:
    """Por qué no ha salido bien penhmat ('' si ha escrito un .mat completo en ``out``)."""
    text = res.output or ""
    tail = _tail(text)
    if res.error:
        return res.error
    if res.cancelled:
        return "cancelado"
    if res.timed_out:
        return f"penhmat no ha terminado en {PENHMAT_TIMEOUT / 60:.0f} minutos"
    m = _IRETRN.search(text)
    if m:
        lines = [ln.strip() for ln in text[:m.start()].splitlines() if ln.strip()]
        why = lines[-1] if lines else ""
        return f"penhmat se ha detenido: {why} (IRETRN {m.group(1)})"
    if res.returncode != 0:
        hint = _hint(text)
        return f"penhmat ha terminado con un error (código {res.returncode}){hint}:\n{tail}"
    if not out.is_file():
        return f"penhmat no ha escrito el .mat{_hint(text)}:\n{tail}"
    if read_mat_header(out) is None:
        return "lo que ha escrito penhmat no es un fichero de material de PENELOPE"
    nuc = read_mat_nuclear(out)
    if nuc is None or not nuc.complete:
        return "el .mat de penhmat está incompleto (falta la lista final de ficheros ENDF)"
    return ""


def _hint(text: str) -> str:
    low = text.lower()
    if "error while loading shared libraries" in low or "cannot open shared object" in low:
        return (" (faltan bibliotecas de Fortran en Linux: instale gfortran en esa distribución, p. ej. «sudo apt "
                "install gfortran», o compile penhmat con -static)")
    if "permission denied" in low:
        return " (no tiene permiso de ejecución: «chmod +x penhmat…» en un terminal de Linux)"
    if "not found" in low or "no such file" in low:
        return " (no se encuentra el programa o alguna de sus carpetas de datos)"
    _at, what, fname = fortran_error(text)
    if what and fname and fname.lower() not in ("stdin", "/dev/stdin"):
        # Leyendo un fichero de la base de datos de penhan (pdfiles/…), no las respuestas de ARGUS (0.16.3).
        why = "está vacío o incompleto" if "end of file" in what.lower() else f"«{what}»"
        return (f" (no ha podido leer {fname}: {why}; si es de la base de datos de penhan, cópielo otra vez desde "
                "la carpeta de penhan)")
    if "end of file" in low:
        return " (penhmat ha pedido algo que ARGUS no sabía: puede ser otra versión de penhmat)"
    return ""


def run_penhmat(penhmat: str | os.PathLike, launch: Launch, database: str | os.PathLike,
                answers: Callable[[str], str], *, cancel: Callable[[], bool] | None = None,
                timeout: float = PENHMAT_TIMEOUT) -> tuple[Path | None, str, str]:
    """Ejecuta penhmat en ``database`` con salida a un temporal de esa carpeta.

    Devuelve (temporal, problema, salida de penhmat): el temporal es None si no ha salido bien (y ya no existe).
    Quien lo recibe lo tiene que borrar.
    """
    database = Path(database)
    for _ in range(20):
        tmp_name = f"{TEMP_PREFIX}{secrets.token_hex(4)}.mat"
        if not (database / tmp_name).exists():
            break
    tmp = database / tmp_name
    res = run_program(penhmat, database, answers(tmp_name), launch, timeout=timeout, cancel=cancel)
    problem = penhmat_problem(res, tmp)
    if problem:
        _remove(tmp)
        if res.cancelled or res.timed_out:  # con WSL, penhmat puede seguir un momento tras cerrar wsl.exe
            time.sleep(1.0)
            _remove(tmp)
        return None, problem, res.output
    return tmp, "", res.output


def _remove(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except OSError:  # pragma: no cover - un antivirus que lo tiene abierto: se queda con su nombre de ARGUS
        pass


def generate(plan: MatPlan, penhmat: str | os.PathLike, launch: Launch, database: str | os.PathLike, *,
             progress: Callable[[int, int, str], None] | None = None, cancel: Callable[[], bool] | None = None,
             copy_endf: bool = True, doc: InDocument | None = None,
             timeout: float = PENHMAT_TIMEOUT, endf_dir: str | os.PathLike | None = None) -> MatPlan:
    """Genera los .mat de ``plan`` y copia los ENDF que necesitan (``copy_endf``) a ``plan.folder``.

    Nunca sustituye un fichero: si al terminar ya hay uno con ese nombre, se deja (estado 'exists' o 'conflict').
    Con ``doc`` los ENDF se vuelven a calcular al final (los de los .mat recién generados).
    """
    jobs = plan.to_generate
    total = len(jobs)
    done = 0
    for j in jobs:
        if cancel is not None and cancel():
            plan.cancelled = True
            break
        if progress is not None:
            progress(done, total, f"penhmat: {j.fname}")
        tmp, problem, _out = run_penhmat(penhmat, launch, database, j.answers, cancel=cancel, timeout=timeout)
        done += 1
        if tmp is None:
            j.status = "cancelled" if problem == "cancelado" else "failed"
            j.message = problem
            if j.status == "cancelled":
                plan.cancelled = True
                break
            continue
        target = plan.folder / j.fname
        try:
            fresh = copy_new(tmp, target, cancel=cancel)
        except CopyCancelled:
            j.status = "cancelled"
            plan.cancelled = True
            break
        except OSError as exc:
            j.status = "failed"
            j.message = f"no se ha podido escribir {target.name} ({exc.strerror or exc})"
            continue
        finally:
            _remove(tmp)
        j.target = target
        j.status = "done" if fresh else "exists"
        if not fresh:
            j.message = "apareció otro fichero con ese nombre mientras se generaba: no se ha tocado"
    if copy_endf and not plan.cancelled:
        if doc is not None:
            plan.endf = plan_endf(doc, plan.folder, database, endf_dir)
        copy_endf_files(plan, progress=progress, cancel=cancel)
    if progress is not None:
        progress(total, total, "")
    return plan


def copy_endf_files(plan: MatPlan, *, progress: Callable[[int, int, str], None] | None = None,
                    cancel: Callable[[], bool] | None = None) -> None:
    """Copia a ``plan.folder`` los ENDF de ``plan.endf`` que no están (sin sustituir ninguno)."""
    todo = plan.endf_to_copy
    for i, e in enumerate(todo):
        if cancel is not None and cancel():
            plan.cancelled = True
            return
        if progress is not None:
            progress(i, len(todo), f"ENDF: {e.name}")
        target = plan.folder / e.name
        try:
            fresh = copy_new(e.source, target, cancel=cancel)
        except CopyCancelled:
            plan.cancelled = True
            return
        except OSError as exc:
            e.status = "failed"
            e.message = exc.strerror or str(exc)
            continue
        e.status = "done" if fresh else ("same" if same_content(e.source, target) else "conflict")
    for e in plan.endf:
        if e.source is None and not e.status:
            e.status = "missing"
            e.message = e.message or "no está en la carpeta de los ENDF"


def summary(plan: MatPlan) -> list[str]:
    """Qué ha pasado, para el usuario (una línea por cosa)."""
    out = []
    done = [j for j in plan.mats if j.status == "done"]
    if done:
        out.append(f"{'Generado' if len(done) == 1 else 'Generados'} con penhmat: " + ", ".join(j.fname for j in done)
                   + ".")
    for j in plan.mats:
        if j.status in ("failed", "exists"):
            out.append(f"{j.fname}: {j.message}")
    copied = [e for e in plan.endf if e.status == "done"]
    if copied:
        out.append(f"{'Copiado' if len(copied) == 1 else 'Copiados'} {plural(len(copied), 'fichero')} ENDF.")
    for e in plan.endf:
        if e.status == "failed":
            out.append(f"No se ha podido copiar {e.name}: {e.message}")
        elif e.status == "conflict":
            out.append(f"{e.name}: en la carpeta ya hay otro fichero con ese nombre (no se ha tocado).")
        elif e.status == "missing":
            out.append(f"{e.name} (de {', '.join(e.materials)}): {e.message}.")
    if plan.cancelled:
        out.append("Cancelado.")
    return out


__all__ = ["EndfJob", "MatJob", "MatPlan", "PENHMAT_TIMEOUT", "TEMP_PREFIX", "copy_endf_files", "endf_sources",
           "generate", "list_answers", "material_list", "penhmat_problem", "plan_endf", "plan_materials", "run_penhmat",
           "summary"]
