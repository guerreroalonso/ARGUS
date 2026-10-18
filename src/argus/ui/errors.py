"""Errores inesperados de la interfaz: se guardan en un registro y se avisan (una vez), en vez de perderse.

Sin esto, una excepción dentro de una señal de Qt solo se escribe en la consola (que en Windows no se ve) y lo
que se estaba haciendo queda a medias sin que el usuario lo sepa.

Un error que se repite (p. ej. uno al pintar, que se repite en cada repintado) no llena la pantalla de avisos
ni el disco: se identifica por su tipo y el sitio del código donde salta (no por el mensaje, que puede cambiar),
el aviso se muestra fuera del evento que falló (con un temporizador), hay un límite de avisos por sesión y el
registro no pasa de :data:`LOG_MAX_BYTES` (el anterior se guarda como ``argus_errores.log.1``).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPEAT_S = 60.0  # el mismo error no se vuelve a mostrar ni a anotar entero hasta pasado este tiempo
DIALOG_GAP_S = 5.0  # entre dos avisos cualesquiera
MAX_DIALOGS = 5  # avisos por sesión como mucho (después, solo el registro)
LOG_REPEATS = 20  # repeticiones que se anotan (una línea cada una) antes de solo contarlas
LOG_MAX_BYTES = 1_000_000

_last: dict[str, float] = {}  # error -> cuándo se mostró
_logged: dict[str, float] = {}  # error -> cuándo se anotó entero
_repeats: dict[str, int] = {}  # error -> repeticiones desde entonces
_showing = False
_last_dialog = float("-inf")
_dialogs = 0


def log_path() -> Path:
    """Fichero del registro de errores (en la carpeta de datos de ARGUS del usuario)."""
    try:
        from PySide6.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    except Exception:  # pragma: no cover - sin Qt
        base = ""
    return Path(base or tempfile.gettempdir()) / "argus_errores.log"


def error_key(exc_type, tb) -> str:
    """El tipo del error y el sitio del código donde salta (el último marco): «ValueError en view3d.py:120»."""
    frames = traceback.extract_tb(tb) if tb is not None else []
    where = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "?"
    return f"{getattr(exc_type, '__name__', exc_type)} en {where}"


def _write(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size + len(text) > LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        return True
    except OSError:
        return False


def _log(path: Path, key: str, exc_type, exc, tb, now: float) -> bool:
    """Anota el error: entero la primera vez (y cada :data:`REPEAT_S`), una línea en las repeticiones siguientes
    (hasta :data:`LOG_REPEATS`) y después solo se cuentan."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if now - _logged.get(key, float("-inf")) >= REPEAT_S:
        extra = _repeats.pop(key, 0)
        _logged[key] = now
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        note = f" (antes se repitió {extra} veces más)" if extra > LOG_REPEATS else ""
        return _write(path, f"--- {stamp}{note}\n{text}\n")
    n = _repeats.get(key, 0) + 1
    _repeats[key] = n
    if n <= LOG_REPEATS:
        return _write(path, f"--- {stamp} otra vez: {getattr(exc_type, '__name__', exc_type)}: {exc} ({key})\n")
    return True


def report(exc_type, exc, tb) -> Path | None:
    """Anota el error en el registro y lo avisa (fuera del evento que ha fallado), salvo si se avisó hace poco del
    mismo, si se acaba de avisar de otro o si ya se ha avisado de muchos en esta sesión."""
    global _last_dialog, _dialogs
    key = error_key(exc_type, tb)
    now = time.monotonic()
    path: Path | None = log_path()
    if not _log(path, key, exc_type, exc, tb, now):
        path = None
    if (_showing or _dialogs >= MAX_DIALOGS or now - _last_dialog < DIALOG_GAP_S
            or now - _last.get(key, float("-inf")) < REPEAT_S):
        return path
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover
        return path
    if QApplication.instance() is None:
        return path
    _last[key] = now
    _last_dialog = now
    _dialogs += 1
    msg = ("Ha ocurrido un error inesperado; lo que se estaba haciendo puede no haberse completado.\n\n"
           f"{getattr(exc_type, '__name__', exc_type)}: {exc}")
    if path is not None:
        msg += f"\n\nEl detalle está en {path}."
    if _dialogs >= MAX_DIALOGS:
        msg += "\n\n(No se avisará de más errores en esta sesión; se seguirán anotando en el registro.)"
    QTimer.singleShot(0, lambda: _show(msg))
    return path


def _show(msg: str) -> None:
    global _showing
    if _showing:
        return
    from PySide6.QtWidgets import QMessageBox

    _showing = True
    try:
        QMessageBox.critical(None, "ARGUS", msg)
    finally:
        _showing = False


def reset() -> None:
    """Olvida los errores avisados (para las pruebas)."""
    global _last_dialog, _dialogs, _showing
    _last.clear()
    _logged.clear()
    _repeats.clear()
    _last_dialog = float("-inf")
    _dialogs = 0
    _showing = False


def install_excepthook() -> None:
    """Sustituye ``sys.excepthook``: lo de antes (la consola) y, además, :func:`report`."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            previous(exc_type, exc, tb)
        except Exception:  # pragma: no cover
            pass
        if issubclass(exc_type, KeyboardInterrupt):
            return
        try:
            report(exc_type, exc, tb)
        except Exception:  # pragma: no cover - el aviso nunca debe romper nada
            pass

    sys.excepthook = hook
