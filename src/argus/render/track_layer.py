"""Trayectorias para dibujarlas sobre la geometría en 3D (cascada de partículas, 0.13.0).

:class:`TrackLayerData` prepara una vez los vértices (posición relativa a su centro, tiempo, energía, partícula,
lluvia y generación) y los tramos que los unen; :class:`TrackStyle` dice cómo se ven (qué partículas y lluvias,
color, instante de la animación…) y se cambia sin volver a preparar nada. Lo usan la GPU (``ui.gpu_tracks``) y la
alternativa con QPainter de la vista 3D sin GPU.

Lo que se calcula de los datos (los límites, el final de cada lluvia, los datos de cada tramo, qué tramos se ven con
cada filtro) se guarda en el propio :class:`TrackLayerData`, que no cambia: al girar la cámara o en cada imagen de la
animación no se vuelve a recorrer todo (0.16.0).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

COLOR_MODES = {"particle": "por partícula", "energy": "por energía", "shower": "por lluvia",
               "generation": "por generación"}
ALL_PARTICLES = frozenset({1, 2, 3, 4, 5, 6})
# Colores por lluvia o generación (la primaria, del primero): categóricos y distinguibles sobre fondo claro u oscuro.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948", "#0aa6b8",
           "#8c564b", "#b8860b", "#6b6b6b"]
_ids = itertools.count(1)
# Orden de dibujo por KPAR (0…6): lo de número menor, debajo.
DRAW_PRIORITY = np.array([0, 2, 0, 3, 4, 5, 1], dtype=np.int64)


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def particle_rgb(kp: int) -> tuple[float, float, float]:
    from ..ui.results.style import particle_color

    return hex_rgb(particle_color(kp))


@dataclass
class TrackLayerData:
    origin: np.ndarray  # (3,) centro de los datos (cm)
    vertices: np.ndarray  # (n, 8) float32: x, y, z (respecto a origin), t (ns), log10(E/eV), KPAR, lluvia, generación
    seg_a: np.ndarray  # (m,) primer punto de cada tramo
    seg_b: np.ndarray  # (m,) segundo punto
    t_max: float
    log_e: tuple[float, float]
    n_showers: int
    kinds: list[int]
    uid: int = field(default_factory=lambda: next(_ids))  # para saber si la GPU ya tiene estos datos

    @property
    def indices(self) -> np.ndarray:
        """Pares de índices de los tramos (GL_LINES)."""
        return np.column_stack([self.seg_a, self.seg_b]).astype(np.uint32).ravel()

    def gpu_buffers(self) -> tuple[bytes, bytes]:
        """Lo que se sube a la GPU: los vértices (8 float32 por punto) y los índices de los tramos (dos uint32 por
        tramo, GL_LINES). Se prepara al leer la cascada, en otro hilo (:meth:`prepare`), y se suelta al subirlo
        (:meth:`release_gpu`)."""
        cached = self.__dict__.get("_gpu")
        if cached is None:
            cached = (np.ascontiguousarray(self.vertices, dtype=np.float32).tobytes(),
                      np.ascontiguousarray(self.indices, dtype=np.uint32).tobytes())
            self.__dict__["_gpu"] = cached
        return cached

    def release_gpu(self) -> None:
        self.__dict__.pop("_gpu", None)

    def prepare(self) -> TrackLayerData:
        """Calcula ya (en el hilo que lee la cascada) lo que se va a pedir al verla: así la ventana no se para."""
        self.bounds()
        self.shower_ends()
        self.kind_runs()
        self.gpu_buffers()
        return self

    @property
    def points(self) -> np.ndarray:
        return self.vertices[:, :3].astype(np.float64) + self.origin

    @property
    def n_segments(self) -> int:
        return len(self.seg_a)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        cached = self.__dict__.get("_bounds")
        if cached is None:  # (se pide en cada movimiento de la cámara)
            if not len(self.vertices):
                cached = (self.origin.copy(), self.origin.copy())
            else:
                p = self.vertices[:, :3]
                cached = (p.min(axis=0).astype(np.float64) + self.origin,
                          p.max(axis=0).astype(np.float64) + self.origin)
            self.__dict__["_bounds"] = cached
        return cached[0].copy(), cached[1].copy()

    def segment_data(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Por tramo: KPAR (int64), lluvia y tiempo (ns) al empezar y al acabar (float32, como en los vértices)."""
        cached = self.__dict__.get("_segments")
        if cached is None:
            v = self.vertices
            cached = (v[self.seg_a, 5].astype(np.int64), v[self.seg_a, 6], v[self.seg_a, 3], v[self.seg_b, 3])
            for arr in cached:
                arr.setflags(write=False)
            self.__dict__["_segments"] = cached
        return cached

    def kind_runs(self) -> list[tuple[int, int, int, bool]]:
        """Los tramos seguidos de un mismo tipo de partícula, en el orden de dibujo: (desde, hasta, KPAR, si sus
        lluvias van en orden)."""
        cached = self.__dict__.get("_runs")
        if cached is None:
            kind, shower, _ta, _tb = self.segment_data()
            cached = []
            if len(kind):
                cut = np.concatenate([[0], np.flatnonzero(kind[1:] != kind[:-1]) + 1, [len(kind)]])
                for s, e in zip(cut[:-1].tolist(), cut[1:].tolist()):
                    sh = shower[s:e]
                    cached.append((s, e, int(kind[s]), bool(np.all(sh[1:] >= sh[:-1]))))
            self.__dict__["_runs"] = cached
        return cached

    def draw_ranges(self, style: TrackStyle) -> list[tuple[int, int]]:
        """Los tramos que pueden verse con las partículas y las lluvias de ``style``: (primero, cuántos), en el orden
        de dibujo. Los demás no se dibujarían (el shader de la GPU los descarta igual: ``vDrop``), así que la imagen es
        la misma; con una sola lluvia o sin la partícula que más tramos tiene, la GPU trabaja mucho menos."""
        runs = self.kind_runs()
        if len(runs) > 64:
            return [(0, self.n_segments)] if self.n_segments else []
        _kind, shower, _ta, _tb = self.segment_data()
        # Como en el shader: las lluvias que se ven son las de lo - 0,5 <= lluvia < hi - 0,5 (en float32).
        lo = np.float32(float(style.shower_lo)) - np.float32(0.5)
        hi = np.float32(float(min(style.shower_hi, 2 ** 30))) - np.float32(0.5)
        out: list[tuple[int, int]] = []
        for s, e, k, ordered in runs:
            if 0 <= k < 31 and k not in style.particles:
                continue
            if ordered:
                sh = shower[s:e]
                a = s + int(np.searchsorted(sh, lo, side="left"))
                b = s + int(np.searchsorted(sh, hi, side="left"))
            else:
                a, b = s, e
            if b <= a:
                continue
            if out and out[-1][0] + out[-1][1] == a:
                out[-1] = (out[-1][0], b - out[-1][0])
            else:
                out.append((a, b - a))
        return out

    def shower_ends(self) -> np.ndarray:
        """Cuándo acaba (ns) cada lluvia: el tiempo de su último punto (0 si no tiene trayectorias)."""
        cached = self.__dict__.get("_ends")
        if cached is not None:
            return cached
        v = self.vertices
        ends = np.zeros(max(self.n_showers, 1), dtype=np.float64)
        if len(v):
            sh = np.clip(v[:, 6].astype(np.int64), 0, len(ends) - 1)
            np.maximum.at(ends, sh, v[:, 3].astype(np.float64))
        self.__dict__["_ends"] = ends
        return ends


def layer_from_showers(s) -> TrackLayerData:
    """De una cascada (``results.shower.ShowerSet``). Los puntos que no pueden ser de penhmain (``bad_mask``: un .trk
    dañado) no cuentan para el origen ni se dibujan sus tramos (hasta 0.17.2, uno NaN o de 1e30 cm hacía desaparecer
    o aplastaba toda la cascada: los vértices son float32 relativos al centro)."""
    pts = np.asarray(s.points, dtype=np.float64)
    bad = s.bad_mask() if hasattr(s, "bad_mask") else np.zeros(len(pts), dtype=bool)
    if bad.any():
        pts = pts.copy()
    good = ~bad
    if good.any():
        origin = 0.5 * (pts[good].min(axis=0) + pts[good].max(axis=0))
    else:
        origin = np.zeros(3)
    if bad.any():
        pts[bad] = origin  # (finitos: no se dibujan, pero no deben estropear nada)
    n = len(pts)
    track_of = np.repeat(np.arange(s.count, dtype=np.int64), np.diff(s.starts))
    v = np.zeros((n, 8), dtype=np.float32)
    v[:, :3] = pts - origin
    t = np.asarray(s.time, dtype=np.float64)
    v[:, 3] = np.where(np.isfinite(t), t, 0.0)
    e = np.asarray(s.energy, dtype=np.float64)
    e = np.maximum(np.where(np.isfinite(e), e, 1.0), 1.0)
    v[:, 4] = np.log10(e)
    v[:, 5] = s.particle[track_of]
    v[:, 6] = s.shower[track_of]
    v[:, 7] = s.generation[track_of]
    a, b = s.segments()
    if bad.any():
        ok = good[a] & good[b]
        a, b = a[ok], b[ok]
    # Primero los fotones y los neutrones y encima las partículas cargadas: con muchas lluvias, los fotones (que van
    # lejos) taparían las trayectorias cortas de los electrones o los protones.
    prio = DRAW_PRIORITY[np.clip(v[a, 5].astype(np.int64), 0, len(DRAW_PRIORITY) - 1)]
    order = np.argsort(prio, kind="stable")
    a, b = a[order], b[order]
    positive = e[e > 1.0]
    lo = float(np.log10(positive.min())) if len(positive) else 0.0
    hi = float(np.log10(positive.max())) if len(positive) else 1.0
    return TrackLayerData(origin, v, a.astype(np.int64), b.astype(np.int64), float(s.t_max), (lo, max(hi, lo + 1e-3)),
                          int(s.n_showers), sorted({int(k) for k in s.particle}))


def layer_from_tracks(tracks) -> TrackLayerData:
    """De trayectorias de LTRACK sin reconstruir (``results.data.Tracks`` por tipo de partícula)."""
    from ..results.shower import reconstruct

    return layer_from_showers(reconstruct(list(tracks)))


@dataclass
class TrackStyle:
    particles: frozenset = ALL_PARTICLES
    shower_lo: int = 0
    shower_hi: int = 10 ** 9  # lluvias [lo, hi)
    color_mode: str = "particle"
    animate: bool = False
    time: float = 0.0  # ns (con animate)
    stagger: float = 0.0  # ns entre lluvias (0: todas a la vez)
    trail: float = 0.0  # ns: lo reciente, más intenso
    heads: bool = True  # las partículas en vuelo, como puntos (con animate)
    hidden_alpha: float = 0.0  # lo tapado por la geometría opaca: 0 no se ve
    alpha: float = 0.85
    head_size: float = 6.0
    line_width: float = 1.6  # píxeles (en la GPU; sin ella, 1)

    def start_offsets(self, data: TrackLayerData, idx: np.ndarray) -> np.ndarray:
        """Cuándo sale (ns) la lluvia de los puntos ``idx`` (escalonadas: la primera que se ve, en 0)."""
        return np.maximum(data.vertices[idx, 6] - self.shower_lo, 0.0) * self.stagger

    def end_time(self, data: TrackLayerData) -> float:
        """Hasta cuándo dura la animación de las lluvias que se ven: hasta que acaba la última (la que más dura, con
        su retraso si salen escalonadas)."""
        lo = max(int(self.shower_lo), 0)
        hi = min(int(self.shower_hi), int(data.n_showers))
        ends = data.shower_ends()
        if hi <= lo or lo >= len(ends):
            return float(data.t_max)
        e = ends[lo:hi] + self.stagger * np.arange(hi - lo)
        end = float(e.max()) if len(e) else 0.0
        return end if end > 0.0 else float(data.t_max)

    def visible_mask(self, data: TrackLayerData) -> np.ndarray:
        """Qué tramos se ven (partícula y lluvia), sin la animación (se guarda en ``data``: no se puede cambiar)."""
        key = (self.particles, self.shower_lo, self.shower_hi)
        cache = data.__dict__.setdefault("_masks", {})
        m = cache.get(key)
        if m is None:
            kind, sh, _ta, _tb = data.segment_data()
            m = np.isin(kind, list(self.particles)) if self.particles != ALL_PARTICLES else np.ones(len(kind), bool)
            m = m & (sh >= self.shower_lo - 0.5) & (sh < self.shower_hi - 0.5)
            m.setflags(write=False)
            if len(cache) >= 8:
                cache.clear()
            cache[key] = m
        return m

    def colors(self, data: TrackLayerData, idx: np.ndarray) -> np.ndarray:
        """Color (r, g, b en [0, 1]) de los puntos ``idx``, como en el shader."""
        v = data.vertices[idx]
        if self.color_mode == "energy":
            lo, hi = data.log_e
            u = np.clip((v[:, 4] - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
            return np.asarray(energy_lut()[np.round(u * 255).astype(int), :3], dtype=float) / 255.0
        if self.color_mode in ("shower", "generation"):
            pal = np.array([hex_rgb(h) for h in PALETTE])
            k = v[:, 6] if self.color_mode == "shower" else np.minimum(v[:, 7], len(PALETTE) - 1)
            return pal[np.mod(k.astype(int), len(PALETTE))]
        table = np.array([particle_rgb(k) for k in range(8)])
        return table[np.clip(v[:, 5].astype(int), 0, 7)]


_LUT: np.ndarray | None = None


def energy_lut() -> np.ndarray:
    """256 colores (RGBA8) de la escala de energía, de baja a alta (viridis: de azul oscuro a amarillo; se lee igual
    sobre fondo claro u oscuro y en escala de grises)."""
    global _LUT
    if _LUT is None:
        import matplotlib

        cmap = matplotlib.colormaps["viridis"]
        # Sin el principio (casi negro): las energías bajas se tienen que ver sobre el fondo oscuro de la vista 3D.
        _LUT = (np.asarray(cmap(np.linspace(0.2, 1.0, 256))) * 255.0 + 0.5).astype(np.uint8)
    return _LUT


def heads_at(data: TrackLayerData, style: TrackStyle, mask: np.ndarray | None = None
             ) -> tuple[np.ndarray, np.ndarray]:
    """Dónde están las partículas en vuelo en el instante ``style.time``: (puntos relativos a ``data.origin``,
    colores). Una por tramo que se está recorriendo."""
    v = data.vertices
    if not style.animate or not len(data.seg_a):
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.float32)
    a, b = data.seg_a, data.seg_b
    _kind, shower, ta0, tb0 = data.segment_data()  # (no se vuelven a sacar de los vértices en cada imagen)
    off = np.maximum(shower - style.shower_lo, 0.0) * style.stagger
    ta = ta0 + off
    tb = tb0 + off
    on = (ta <= style.time) & (tb > style.time)
    if mask is not None:
        on &= mask
    else:
        on &= style.visible_mask(data)
    idx = np.flatnonzero(on)
    if not len(idx):
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.float32)
    f = ((style.time - ta[idx]) / np.maximum(tb[idx] - ta[idx], 1e-12)).astype(np.float32)[:, None]
    pa = v[a[idx], :3]
    pb = v[b[idx], :3]
    pos = pa + f * (pb - pa)
    col = style.colors(data, a[idx]).astype(np.float32)
    return pos.astype(np.float32), col


def time_ticks(t_max: float) -> list[float]:
    """Instantes «redondos» para la escala de tiempo."""
    if t_max <= 0:
        return [0.0]
    step = 10 ** np.floor(np.log10(t_max))
    for m in (1, 2, 5):
        if t_max / (m * step) <= 6:
            step *= m
            break
    return [float(x) for x in np.arange(0.0, t_max + 0.5 * step, step)]


def format_time(t: float) -> str:
    """«0,35 ns», «12 ns», «4,1 µs»…"""
    if t >= 1e6:
        s, u = t / 1e6, "ms"
    elif t >= 1e3:
        s, u = t / 1e3, "µs"
    else:
        s, u = t, "ns"
    text = f"{s:.3g}" if s < 100 else f"{s:.0f}"
    return text.replace(".", ",") + " " + u


__all__ = ["ALL_PARTICLES", "COLOR_MODES", "PALETTE", "TrackLayerData", "TrackStyle", "energy_lut", "format_time",
           "heads_at", "hex_rgb", "layer_from_showers", "layer_from_tracks", "particle_rgb", "time_ticks"]
