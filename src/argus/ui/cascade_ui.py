"""Lanzar y seguir la cascada desde la interfaz (ventana principal y ventana de resultados).

La cascada son las primeras lluvias del caso simuladas con el penhmain del usuario (``penhan.cascade``). Aquí se
decide con qué penhmain (el de la carpeta del .in o el elegido en Opciones), cuántas lluvias (lo último que se
pidió) y se recuerda cuántas guarda cada penhmain (NPSHOW), para no volver a probarlo.
"""

from __future__ import annotations

from pathlib import Path

from ..penhan import cascade as cs
from ..penhan.inputfile import parse_in_file
from ..penhan.programs import WSL_CHECKING, Launch, exe_info, launch_for, needs_wsl
from ..penhan.run import pg_wsl_cached
from .settings import app_settings, penhan_setup

SHOWERS_KEY = "cascada/lluvias"
WITH_RUN_KEY = "cascada/al_ejecutar"
# Cuántas lluvias guarda cada penhmain (por su sha1). «npshow99»: lo que dice la prueba con LTRACK 99 (lo apuntado
# por ARGUS 0.13.0 en «npshow/» podía ser menos: probaba con LTRACK = las lluvias pedidas).
CAP_KEY = "cascada/npshow99/"


def default_showers(settings=None) -> int:
    s = settings or app_settings()
    try:
        n = int(float(s.value(SHOWERS_KEY, cs.DEFAULT_SHOWERS)))
    except (TypeError, ValueError):
        n = cs.DEFAULT_SHOWERS
    return min(max(n, 1), cs.MAX_SHOWERS)


def set_default_showers(n: int, settings=None) -> None:
    s = settings or app_settings()
    s.setValue(SHOWERS_KEY, int(n))


def with_run(settings=None) -> bool:
    """¿Se simula la cascada al pulsar «Ejecutar»? (sí, salvo que el usuario lo haya desmarcado)."""
    s = settings or app_settings()
    return str(s.value(WITH_RUN_KEY, "true")).lower() in ("true", "1")


def set_with_run(on: bool, settings=None) -> None:
    s = settings or app_settings()
    s.setValue(WITH_RUN_KEY, "true" if on else "false")


def known_cap(exe: str | Path, settings=None, sha: str | None = None) -> int | None:
    """Cuántas lluvias guarda este penhmain (lo que dijo la última prueba), o None si no se sabe. ``sha``: su sha1,
    si ya se tiene (calcularlo lee el ejecutable entero)."""
    s = settings or app_settings()
    sha = sha or cs.file_sha1(exe)
    if sha is None:
        return None
    v = s.value(CAP_KEY + sha, "")
    try:
        return int(v) if str(v).strip() else None
    except (TypeError, ValueError):
        return None


def remember_cap(exe: str | Path, cap: int | None, settings=None) -> None:
    if cap is None:
        return
    s = settings or app_settings()
    sha = cs.file_sha1(exe)
    if sha is not None:
        s.setValue(CAP_KEY + sha, int(cap))


def cascade_exe(in_path: str | Path, settings=None) -> Path | None:
    """El penhmain para la cascada de ``in_path``: el de su carpeta si hay uno (es con el que se simula el caso;
    también si es distinto del elegido en Opciones, como en «Ejecutar», que por defecto usa el de la carpeta) o, si
    no, el elegido en Opciones."""
    from ..penhan.run import choose_exe, folder_penhmain

    s = settings or app_settings()
    folder = Path(in_path).parent
    setup = penhan_setup(s)
    # Solo hace falta qué fichero es, no cómo se ejecuta: sin preguntar a WSL (la ventana de resultados llama a esto
    # en el hilo de la interfaz, y la primera vez WSL tarda en contestar lo que tarde en arrancar).
    choice = choose_exe(folder, setup.penhmain, launch_fn=lambda _info: Launch("", ""))
    if choice.status in ("inside", "same", "conflict") and choice.target is not None and choice.target.is_file():
        return choice.target
    if choice.status == "copy" and choice.chosen is not None and choice.chosen.is_file():
        return choice.chosen
    found = folder_penhmain(folder)  # el elegido en Opciones ya no existe: el de la carpeta, si hay
    return found[0] if found else None


def start_cascade(in_path: str | Path, *, showers: int | None = None, settings=None, exe: str | Path | None = None,
                  doc=None) -> cs.CascadeJob:
    """Empieza a simular la cascada de ``in_path`` (en segundo plano). ``ValueError`` con el motivo si no se puede
    (no hay penhmain, no se puede ejecutar aquí…)."""
    from ..penhan.simfolder import find_database

    s = settings or app_settings()
    in_path = Path(in_path)
    if doc is None:
        doc = parse_in_file(in_path)
    exe = Path(exe) if exe else cascade_exe(in_path, s)
    if exe is None:
        raise ValueError("no se sabe qué penhmain usar: elíjalo en Opciones → «Programas de penhan (penhmat y "
                         "penhmain)…» o póngalo en la carpeta del .in")
    info = exe_info(exe)
    if needs_wsl(info) and pg_wsl_cached() is None:
        # Sin esperar a WSL en la interfaz (la primera vez tarda lo que tarde en arrancar): se le pregunta en el hilo
        # de la cascada, que se para con el motivo si no funciona (0.16.3).
        launch = WSL_CHECKING
    else:
        launch = launch_for(info)
        if not launch.ok:
            raise ValueError(f"penhmain no se puede ejecutar en este ordenador: {launch.note}")
    setup = penhan_setup(s)
    db = setup.database if setup.database is not None and setup.database.is_dir() else find_database(in_path.parent)
    n = showers or default_showers(s)
    job = cs.CascadeJob(in_path, doc, exe, launch, showers=n, cap=known_cap(exe, s), database=db, endf_dir=setup.endf)
    return cs.start_job(job)


def save_learned_cap(job: cs.CascadeJob, settings=None) -> None:
    """Guarda lo que ha averiguado la cascada sobre su penhmain (se llama desde la interfaz, no desde su hilo)."""
    st = job.status
    if st.cap is not None and job.cap is None:
        remember_cap(job.exe, st.cap, settings)
        job.cap = st.cap


__all__ = ["cascade_exe", "default_showers", "known_cap", "remember_cap", "save_learned_cap", "set_default_showers",
           "set_with_run", "start_cascade", "with_run"]
