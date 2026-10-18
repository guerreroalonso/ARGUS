"""La carpeta de datos de ARGUS de cada usuario (0.16.0).

Lo que el usuario guarda para todos sus casos (los materiales personalizados) va en su perfil, no en la carpeta del
programa: así vale igual con ARGUS ejecutado desde su código, instalado para todos o descargado por otra persona, y
cada usuario del ordenador tiene lo suyo.

* Windows: ``%APPDATA%\\ARGUS`` (``C:\\Users\\<usuario>\\AppData\\Roaming\\ARGUS``: viaja con el perfil);
* macOS: ``~/Library/Application Support/ARGUS``;
* Linux: ``$XDG_DATA_HOME/ARGUS`` (``~/.local/share/ARGUS``).

Con la variable de entorno ``ARGUS_DATA_DIR`` se usa esa carpeta (las pruebas lo hacen para no tocar la del usuario).

Lo que se puede borrar sin perder nada (la caché de Numba del ejecutable, 0.17.0) va aparte, en la carpeta de cachés
(:func:`user_cache_dir`): ``%LOCALAPPDATA%\\ARGUS`` (no viaja con el perfil), ``~/Library/Caches/ARGUS`` y
``$XDG_CACHE_HOME/ARGUS`` (``~/.cache/ARGUS``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV = "ARGUS_DATA_DIR"


def user_data_dir() -> Path:
    """La carpeta de datos de ARGUS del usuario (no se crea aquí)."""
    env = os.environ.get(ENV, "").strip()
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "ARGUS"


def user_cache_dir() -> Path:
    """La carpeta de cachés de ARGUS del usuario (no se crea aquí)."""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Caches"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "")
        base = Path(xdg) if os.path.isabs(xdg) else home / ".cache"  # la norma XDG manda no usar una relativa
    return base / "ARGUS"


__all__ = ["ENV", "user_cache_dir", "user_data_dir"]
