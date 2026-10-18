"""Los ajustes de ARGUS (colores, ficheros recientes, elección de .in, vistas...)."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings

from ..penhan.programs import PenhanSetup


def app_settings() -> QSettings:
    """Los ajustes del usuario (en Windows, en el registro).

    Con la variable de entorno ``ARGUS_SETTINGS=<fichero.ini>`` se usan los de ese
    fichero: las pruebas lo hacen para no tocar (ni borrar) los del usuario.
    """
    ini = os.environ.get("ARGUS_SETTINGS")
    if ini:
        return QSettings(ini, QSettings.Format.IniFormat)
    return QSettings("ARGUS", "ARGUS")


# penhmain con la corrección de CTTRACK (DEE = EORIG − E; ADR-010 y ADR-011): no hace falta partir los tejidos de
# un TAC por densidad ni avisar.
CTTRACK_KEY = "penhmain/cttrack_corregido"


def cttrack_fixed(settings: QSettings | None = None) -> bool:
    """¿Ha dicho el usuario que su penhmain tiene corregido CTTRACK?"""
    s = settings or app_settings()
    return str(s.value(CTTRACK_KEY, "false")).lower() in ("true", "1")


def set_cttrack_fixed(value: bool, settings: QSettings | None = None) -> None:
    s = settings or app_settings()
    s.setValue(CTTRACK_KEY, "true" if value else "false")
    s.sync()


# Los programas de penhan del usuario (ADR-013). «sim/database» y «sim/penhmain» son los de «Preparar la carpeta de
# simulación» (0.8.0): se comparten.
PENHAN_DB_KEY = "sim/database"
PENHMAIN_KEY = "sim/penhmain"
PENHMAT_KEY = "sim/penhmat"
PENHMAT_OFF_KEY = "sim/penhmat_no_hace_falta"
ENDF_KEY = "sim/endf"  # la carpeta de los ficheros ENDF (0.16.0; vacío: database/ENDF_data)


def penhan_setup(settings: QSettings | None = None) -> PenhanSetup:
    """Dónde están la base de datos de penhan, penhmat y penhmain del usuario (o que no hace falta penhmat) y la
    carpeta de los ENDF."""
    s = settings or app_settings()

    def path(key: str) -> Path | None:
        v = str(s.value(key, "") or "").strip()
        return Path(v) if v else None

    off = str(s.value(PENHMAT_OFF_KEY, "false")).lower() in ("true", "1")
    return PenhanSetup(path(PENHAN_DB_KEY), path(PENHMAT_KEY), path(PENHMAIN_KEY), off, path(ENDF_KEY))


def save_penhan_setup(setup: PenhanSetup, settings: QSettings | None = None) -> None:
    """Guarda dónde está todo. La carpeta de los ENDF que es la ENDF_data de la base de datos se guarda vacía («la
    de la base de datos»): así sigue a la base de datos si esta cambia (revisión de 0.16)."""
    s = settings or app_settings()
    if setup.endf is not None and setup.database is not None:
        try:
            same = Path(setup.endf).resolve() == (Path(setup.database) / "ENDF_data").resolve()
        except OSError:
            same = False
        if same:
            setup = PenhanSetup(setup.database, setup.penhmat, setup.penhmain, setup.penhmat_off, None)
    for key, value in ((PENHAN_DB_KEY, setup.database), (PENHMAT_KEY, setup.penhmat), (PENHMAIN_KEY, setup.penhmain),
                       (ENDF_KEY, setup.endf)):
        s.setValue(key, str(value) if value else "")
    s.setValue(PENHMAT_OFF_KEY, "true" if setup.penhmat_off else "false")
    s.sync()


# Carpetas de simulación preparadas para cada .in (las más recientes primero): «Ver los resultados de este caso»
# las busca ahí además de en la carpeta del propio .in.
SIM_FOLDERS_KEY = "resultados/carpetas_de_simulacion"
_MAX_PER_INPUT = 8
_MAX_INPUTS = 200
_RESULT_MARKS = ("penhmain-res.dat", "penhmain.dat")


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _sim_folders_map(s: QSettings) -> dict[str, list[str]]:
    import json

    try:
        data = json.loads(str(s.value(SIM_FOLDERS_KEY, "") or "{}"))
    except ValueError:
        return {}
    return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) \
        else {}


def remember_sim_folder(settings: QSettings | None, in_path: Path, dest: Path) -> None:
    import json

    s = settings or app_settings()
    data = _sim_folders_map(s)
    key = _norm(in_path)
    lst = [d for d in data.pop(key, []) if _norm(Path(d)) != _norm(dest)]
    data[key] = [str(dest)] + lst[:_MAX_PER_INPUT - 1]
    while len(data) > _MAX_INPUTS:  # los más antiguos fuera (el diccionario conserva el orden de inserción)
        data.pop(next(iter(data)))
    s.setValue(SIM_FOLDERS_KEY, json.dumps(data))
    s.sync()


def sim_folders_for(settings: QSettings | None, in_path: Path) -> list[Path]:
    s = settings or app_settings()
    return [Path(d) for d in _sim_folders_map(s).get(_norm(in_path), [])]


def _results_time(folder: Path) -> float:
    """Fecha del resultado más reciente de penhmain en la carpeta o en sus subcarpetas (ejecuciones en paralelo)."""
    best = 0.0
    try:
        subs = [folder] + [d for d in folder.iterdir() if d.is_dir()]
    except OSError:
        return 0.0
    for d in subs[:200]:
        for name in _RESULT_MARKS:
            try:
                best = max(best, (d / name).stat().st_mtime)
            except OSError:
                pass
    return best


def results_folder_for(settings: QSettings | None, in_path: Path) -> Path:
    """Dónde mirar los resultados de un .in: la carpeta (la suya o una de simulación preparada para él) con los
    resultados más recientes; si ninguna tiene aún, la última carpeta de simulación preparada o la del .in."""
    sims = [d for d in sim_folders_for(settings, in_path) if d.is_dir()]
    cands = sims + [in_path.parent]
    times = [(_results_time(d), -i, d) for i, d in enumerate(cands)]
    t, _i, best = max(times, key=lambda x: (x[0], x[1]))
    if t > 0:
        return best
    return sims[0] if sims else in_path.parent


# Colores de los materiales: los que eligió el usuario para cada geometría (por su ruta), sobre los de por defecto.
def colors_key(session) -> str | None:
    """Clave de los colores de una geometría (su sesión o su ruta)."""
    path = getattr(session, "path", session)
    if path is None:
        return None
    return "colors/" + os.path.normcase(os.path.abspath(str(path))).replace("\\", "/")


def default_material_colors(session) -> dict[int, str]:
    """Colores por defecto que no son los de la paleta: los trozos de un tejido partido por densidad al importar un
    TAC llevan el de su primer material (según la ficha del TAC)."""
    from ..pengeom.ct import ct_families
    from ..render.colors import material_color

    geo = getattr(session, "geo", None) if session is not None else None
    ct = getattr(geo, "ct", None) if geo is not None and getattr(geo, "ok", False) else None
    if ct is None:
        return {}
    groups: dict[str, list[int]] = {}
    for n, fam in ct_families(ct).items():
        groups.setdefault(fam, []).append(int(n))
    out: dict[int, str] = {}
    for nums in groups.values():
        if len(nums) > 1:
            n0 = min(nums)
            out.update({n: material_color(n0) for n in nums if n != n0})
    return out


def saved_material_colors(session, settings: QSettings | None = None) -> dict[int, str]:
    """Los colores por defecto (:func:`default_material_colors`) con los que ha elegido el usuario encima."""
    import json

    colors = default_material_colors(session)
    key = colors_key(session)
    if key is None:
        return colors
    s = settings or app_settings()
    raw = s.value(key, "")
    try:
        colors.update({int(k): str(v) for k, v in json.loads(raw).items()} if raw else {})
    except (ValueError, TypeError, AttributeError):
        pass
    return colors
