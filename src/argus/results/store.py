"""Caché de resultados leídos y resultados combinados de varias ejecuciones.

Un fichero se vuelve a leer solo si ha cambiado (tamaño o fecha). Los mapas
grandes ocupan mucha memoria: se guarda como mucho ``max_bytes`` y se olvidan
primero los menos usados. Es seguro llamarlo desde varios hilos.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .combine import combine
from .data import Curves, Grid, PhaseSpace, Tracks
from .folder import OutputFile, ResultsFolder
from .readers import RECENT_S, IncompleteFile, read_output
from .summary import Summary, summary_complete

DEFAULT_MAX_BYTES = 1_500_000_000
# s: una curva con menos puntos que la última vez que sigue igual (tamaño y fecha) este tiempo ya no se está
# escribiendo (penhmain escribe cada fichero de una vez, en milésimas): es así (otra rejilla, otra simulación).
STABLE_S = 2.0


def _summary_path(folder: ResultsFolder, f: OutputFile | None = None):
    """El resumen de la carpeta (para saber si un fichero se escribió entero), o None."""
    res = folder.find("res")
    return None if res is None or res is f else res.path


def _size_of(obj) -> int:
    if isinstance(obj, Grid):
        n = obj.value.nbytes + obj.err.nbytes
        return n + sum(v.nbytes + e.nbytes for v, e, _ in obj.extra.values())
    if isinstance(obj, Curves):
        return sum(s.y.nbytes + (0 if s.err is None else s.err.nbytes) for s in obj.series) + obj.x.nbytes
    if isinstance(obj, Tracks):
        return obj.points.nbytes
    if isinstance(obj, PhaseSpace):
        return obj.e.nbytes * 14
    if isinstance(obj, np.ndarray):
        return obj.nbytes
    return 10_000


class ResultsStore:
    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES):
        self.max_bytes = max_bytes
        self._cache: OrderedDict[tuple, tuple[object, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self._points: dict[str, int] = {}  # puntos de cada curva la última vez que se leyó
        self._short: dict[str, tuple[tuple, float]] = {}  # curva con menos puntos: (su clave, cuándo se vio así)

    def _get(self, key):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key][0]
        return None

    def _put(self, key, obj) -> None:
        size = _size_of(obj)
        with self._lock:
            old = self._cache.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            # Las versiones anteriores del mismo fichero ya no sirven.
            for k in [k for k in self._cache if k[0] == key[0]]:
                self._bytes -= self._cache.pop(k)[1]
            self._cache[key] = (obj, size)
            self._bytes += size
            while self._bytes > self.max_bytes and len(self._cache) > 1:
                _, (_, s) = self._cache.popitem(last=False)
                self._bytes -= s

    def load(self, output: OutputFile | str | os.PathLike, writing: bool = False,
             summary: str | os.PathLike | None = None):
        """Los datos de un fichero de resultados (de la caché si no ha cambiado).

        Con ``writing`` (penhmain está volcando los resultados de su carpeta), un fichero reciente se está
        escribiendo; también si es una curva con menos puntos que la última vez que se leyó: :class:`IncompleteFile`
        (quien lo ve se queda con lo anterior y lo vuelve a leer en la próxima vuelta). Salvo que se sepa que está
        entero (otra simulación con menos intervalos en la misma carpeta): el resumen de su carpeta (``summary``),
        que penhmain cierra el último en cada volcado, está completo y es posterior; o lleva ``STABLE_S`` sin cambiar.
        """
        path = output.path if isinstance(output, OutputFile) else Path(output)
        ident = output.ident if isinstance(output, OutputFile) else None
        st = os.stat(path)
        key = (str(path), st.st_size, st.st_mtime_ns)
        hit = self._get(key)
        if hit is not None:
            return hit
        recent = time.time() - st.st_mtime <= RECENT_S
        if writing and recent:
            raise IncompleteFile(f"{path.name}: penhmain está escribiendo los resultados")
        data = read_output(path, ident)
        if isinstance(data, Curves):
            prev = self._points.get(str(path))
            if recent and prev is not None and len(data.x) < prev and not self._settled(path, st, summary):
                seen = self._short.get(str(path))
                now = time.time()
                if seen is None or seen[0] != key:
                    self._short[str(path)] = (key, now)
                if seen is None or seen[0] != key or now - seen[1] < STABLE_S:
                    raise IncompleteFile(f"{path.name}: tiene menos puntos que antes (penhmain lo está escribiendo)")
            self._short.pop(str(path), None)
            self._points[str(path)] = len(data.x)
        self._put(key, data)
        return data

    @staticmethod
    def _settled(path: Path, st: os.stat_result, summary) -> bool:
        """¿Se escribió entero el fichero (el que se ha leído, de fecha ``st``)? Su resumen está completo y se cerró
        después (o a la vez): el fichero es de ese volcado o de uno anterior, y no ha cambiado mientras se leía."""
        if summary is None:
            return False
        try:
            now = os.stat(path)
            rs = os.stat(summary)
        except OSError:
            return False
        if (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
            return False
        return rs.st_mtime_ns >= st.st_mtime_ns and summary_complete(summary)

    def summary(self, folder: ResultsFolder) -> Summary | None:
        f = folder.find("res")
        if f is None:
            return None
        try:
            return self.load(f)
        except (OSError, ValueError):
            return None

    def combined(self, parent: ResultsFolder, key: str):
        """Un resultado combinado de todas las ejecuciones de ``parent`` que lo tienen (y su número de lluvias).

        Si alguna lo está escribiendo, :class:`IncompleteFile` (no se combinan solo las demás)."""
        items = []
        for run in parent.runs:
            f = run.find(key)
            res = run.find("res")
            if f is None or res is None:
                continue
            try:
                s = self.load(res, writing=run.writing)
            except IncompleteFile:
                raise
            except (OSError, ValueError):
                continue  # un resumen que no se puede leer (y no se está escribiendo): esa ejecución no cuenta
            if s.showers <= 0:
                continue
            items.append((s.showers, s if key == "res" else self.load(f, writing=run.writing,
                                                                       summary=_summary_path(run, f))))
        if not items:
            raise ValueError("Ninguna ejecución tiene este resultado")
        if key == "res":
            from .combine import combine_summaries

            return combine_summaries([d for _, d in items])
        if len(items) == 1:
            return items[0][1]
        return combine(items)

    def get(self, folder: ResultsFolder, key: str):
        """Un resultado de una carpeta: el suyo o, si son ejecuciones en paralelo, el de todas combinadas."""
        if folder.combines:
            return self.combined(folder, key)
        f = folder.find(key)
        if f is None:
            raise ValueError("Esta carpeta no tiene ese resultado")
        return self.load(f, writing=folder.writing, summary=_summary_path(folder, f))

    def forget(self, path: str | os.PathLike | None = None) -> None:
        with self._lock:
            if path is None:
                self._cache.clear()
                self._bytes = 0
                return
            p = str(path)
            for k in [k for k in self._cache if k[0] == p]:
                self._bytes -= self._cache.pop(k)[1]

    @property
    def used_bytes(self) -> int:
        return self._bytes


__all__ = ["ResultsStore"]
