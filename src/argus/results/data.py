"""Modelo de datos de los resultados de penhmain.

* :class:`Curves`: una o varias series sobre el mismo eje (dosis en profundidad,
  espectros por tipo de partícula, distribuciones angulares…).
* :class:`Grid`: valores en una rejilla (caja, cilindro, esfera o vóxeles del
  TAC) con su incertidumbre.
* :class:`Tracks`: trayectorias de LTRACK.
* :class:`PhaseSpace`: partículas de un fichero de espacio de fases.
* :class:`TextOutput`: ficheros de texto (registro de penhmain, geometry.rep…).

Las incertidumbres son siempre de 1 sigma (penhmain escribe 3 sigma). Los
valores son los de penhmain: por partícula primaria (o por desintegración, con
una fuente radiactiva), en eV, cm, g y s.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

EV_PER_G_TO_GY = 1.602176634e-16  # 1 eV/g en Gy (penhmain: «1 eV/g = 1.602176634D-16 Gy»)
TINY = 1.0e-35  # penhmain escribe max(valor, 1e-35): un 1e-35 es un cero
# Un intervalo de anchura cero de un espectro (SPECTR con una energía repetida: esa fracción sale con la energía
# exacta) va en psource.dat con su probabilidad multiplicada por FACT = 1e15 (PMWRT), como si fuera una densidad por
# eV: su probabilidad es el valor por 1e-15 eV.
ZERO_WIDTH_EV = 1.0e-15

PARTICLE_NAMES = {1: "electrones", 2: "fotones", 3: "positrones", 4: "protones", 5: "alfas", 6: "neutrones"}
PARTICLE_SINGULAR = {1: "electrón", 2: "fotón", 3: "positrón", 4: "protón", 5: "alfa", 6: "neutrón"}
PARTICLE_SYMBOLS = {1: "e⁻", 2: "γ", 3: "e⁺", 4: "p", 5: "α", 6: "n"}
PARTICLE_KEYS = {"el": 1, "ph": 2, "po": 3, "h": 4, "a": 5, "n": 6}  # sufijos de polar-angle-*.dat
PARTICLE_FILE_WORDS = {"electron": 1, "photon": 2, "positron": 3, "proton": 4, "alpha": 5, "neutron": 6}


def zero_small(a: np.ndarray) -> np.ndarray:
    """Los 1e-35 de penhmain (valores nulos) como ceros."""
    a = np.asarray(a, dtype=np.float64)
    return np.where(np.abs(a) <= 1.0000001 * TINY, 0.0, a)


@dataclass
class Series:
    """Una serie de valores sobre el eje de su :class:`Curves`."""

    name: str
    y: np.ndarray
    err: np.ndarray | None = None  # 1 sigma
    particle: int | None = None  # KPAR (1..6)
    unit: str | None = None  # si no es la de la curva
    quantity: str | None = None  # si no es la de la curva
    # Media condicionada (p. ej. la energía media de las partículas de cada intervalo de ángulo): al combinar
    # ejecuciones, se pondera con los valores de esta otra serie (por el número de lluvias).
    weight_of: str | None = None

    @property
    def empty(self) -> bool:
        return not np.any(np.abs(self.y) > 1.0000001 * TINY)

    def rel_err(self) -> np.ndarray:
        if self.err is None:
            return np.zeros_like(self.y)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(np.abs(self.y) > TINY, self.err / np.abs(self.y), 0.0)
        return r


@dataclass
class Curves:
    """Series sobre un mismo eje: posición, energía, ángulo, edad…"""

    kind: str
    title: str
    x: np.ndarray  # centros de los intervalos (o posiciones de las líneas)
    x_name: str
    x_unit: str
    y_name: str
    y_unit: str
    series: list[Series]
    edges: np.ndarray | None = None  # bordes de los intervalos (len(x) + 1) si se conocen
    histogram: bool = True  # dibujar como escalones
    log_x: bool = False  # intervalos logarítmicos
    discrete: bool = False  # líneas discretas (SLINES): barras verticales
    meta: dict = field(default_factory=dict)

    def get(self, name: str) -> Series | None:
        for s in self.series:
            if s.name == name:
                return s
        return None

    def by_particle(self, kpar: int) -> Series | None:
        for s in self.series:
            if s.particle == kpar and s.weight_of is None:
                return s
        return None

    def nonempty(self) -> list[Series]:
        return [s for s in self.series if not s.empty]

    @property
    def widths(self) -> np.ndarray | None:
        return None if self.edges is None else np.diff(self.edges)

    def exact_energies(self) -> np.ndarray | None:
        """Los intervalos de anchura cero de un histograma en energía (energías exactas: ver ``ZERO_WIDTH_EV``), o
        None si no tiene."""
        if not self.histogram or self.x_unit != "eV" or self.edges is None or len(self.edges) != len(self.x) + 1:
            return None
        zw = np.diff(np.asarray(self.edges, dtype=np.float64)) <= 0.0
        return zw if zw.any() else None


def bin_probabilities(edges: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Probabilidad de cada intervalo de un histograma de densidades por eV: valor por anchura, o por 1e-15 eV si la
    anchura es cero (una energía exacta, como la escribe penhmain en psource.dat)."""
    w = np.diff(np.asarray(edges, dtype=np.float64))
    return np.asarray(y, dtype=np.float64) * np.where(w > 0.0, w, ZERO_WIDTH_EV)


@dataclass
class Grid:
    """Valores en una rejilla regular.

    ``geometry``:

    * ``box``: ejes (x, y, z), rejilla de GRIDX/GRIDY/GRIDZ;
    * ``cylinder``: ejes (r, z), GRIDZ y GRIDR (simetría axial alrededor de Z);
    * ``sphere``: eje (r,), GRIDR solo (simetría esférica alrededor del origen);
    * ``ct``: ejes (x, y, z) de los vóxeles del TAC, en el sistema del propio TAC
      (de 0 a XM, YM, ZM); la colocación en la geometría la da el CTBOX.

    ``value`` y ``err`` tienen la forma de los ejes en ese orden.
    """

    kind: str
    title: str
    geometry: str
    axes: tuple[str, ...]
    edges: tuple[np.ndarray, ...]
    value: np.ndarray
    err: np.ndarray  # 1 sigma
    quantity: str
    unit: str
    extra: dict[str, tuple[np.ndarray, np.ndarray, str]] = field(default_factory=dict)  # nombre -> (v, err, unidad)
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(int(v) for v in self.value.shape)

    def centers(self, i: int) -> np.ndarray:
        e = self.edges[i]
        return 0.5 * (e[:-1] + e[1:])

    def steps(self, i: int) -> np.ndarray:
        return np.diff(self.edges[i])

    def voxel_volumes(self) -> np.ndarray:
        """Volumen de cada celda (cm³), con la forma de ``value``."""
        if self.geometry in ("box", "ct"):
            dx, dy, dz = (self.steps(i) for i in range(3))
            return dx[:, None, None] * dy[None, :, None] * dz[None, None, :]
        if self.geometry == "cylinder":
            r = self.edges[0]
            ring = np.pi * (r[1:] ** 2 - r[:-1] ** 2)
            return ring[:, None] * self.steps(1)[None, :]
        r = self.edges[0]
        return 4.0 / 3.0 * np.pi * (r[1:] ** 3 - r[:-1] ** 3)

    def rel_err(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(np.abs(self.value) > TINY, self.err / np.abs(self.value), 0.0)


@dataclass
class Tracks:
    """Trayectorias de un tipo de partícula (fichero .trk de LTRACK): puntos (x, y, z, E) por tramos."""

    particle: int | None
    points: np.ndarray  # (n, 4): x, y, z (cm) y E (eV)
    starts: np.ndarray  # índice del primer punto de cada trayectoria, y al final len(points)

    @property
    def count(self) -> int:
        return max(len(self.starts) - 1, 0)

    def track(self, i: int) -> np.ndarray:
        return self.points[self.starts[i]:self.starts[i + 1]]

    def ranges(self, max_points: int) -> list[tuple[int, int]]:
        """(inicio, fin) de las primeras trayectorias enteras que caben en ``max_points`` puntos (la primera,
        recortada, si ella sola ya tiene más), sin las de un solo punto."""
        if self.count == 0 or max_points < 2:
            return []
        lengths = np.diff(self.starts)
        k = int(np.searchsorted(np.cumsum(lengths), max_points, side="right"))
        if k == 0:
            a = int(self.starts[0])
            return [(a, a + int(max_points))] if lengths[0] >= 2 else []
        return [(int(self.starts[i]), int(self.starts[i + 1])) for i in range(k) if lengths[i] >= 2]

    def segments(self) -> tuple[np.ndarray, np.ndarray]:
        """Todos los tramos rectos como (inicios, finales), sin unir trayectorias distintas."""
        if len(self.points) < 2:
            return np.zeros((0, 4)), np.zeros((0, 4))
        keep = np.ones(len(self.points) - 1, dtype=bool)
        ends = self.starts[1:-1] - 1  # el último punto de cada trayectoria no se une al primero de la siguiente
        keep[ends[(ends >= 0) & (ends < len(keep))]] = False
        return self.points[:-1][keep], self.points[1:][keep]


def fair_shares(sizes, total: int) -> list[int]:
    """Reparte ``total`` (puntos o tramos que se pueden dibujar) entre grupos que tienen ``sizes``: a partes
    iguales, y lo que no necesita un grupo pequeño, para los demás. Así un tipo de partícula con muchas
    trayectorias no deja sin dibujar a los otros."""
    sizes = [max(int(s), 0) for s in sizes]
    out = [0] * len(sizes)
    left = max(int(total), 0)
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    for n, i in enumerate(order):
        out[i] = min(sizes[i], left // (len(order) - n))
        left -= out[i]
    return out


@dataclass
class PhaseSpace:
    """Partículas de un espacio de fases de penhmain (``psf-impdet.dat``)."""

    detector: int | None
    kpar: np.ndarray
    e: np.ndarray
    pos: np.ndarray  # (n, 3)
    dir: np.ndarray  # (n, 3)
    weight: np.ndarray
    ilb: np.ndarray  # (n, 4)
    nshi: np.ndarray  # incremento del número de lluvia respecto a la partícula anterior
    total: int = 0  # partículas del fichero (si solo se han leído algunas)

    @property
    def count(self) -> int:
        return len(self.e)

    @property
    def truncated(self) -> bool:
        return self.total > self.count

    def showers(self) -> int:
        """Lluvias distintas que tienen alguna partícula (suma de los incrementos)."""
        return int(np.count_nonzero(self.nshi)) if len(self.nshi) else 0


@dataclass
class TextOutput:
    kind: str
    title: str
    text: str
    meta: dict = field(default_factory=dict)


__all__ = ["Curves", "EV_PER_G_TO_GY", "Grid", "PARTICLE_FILE_WORDS", "PARTICLE_KEYS", "PARTICLE_NAMES",
           "PARTICLE_SINGULAR", "PARTICLE_SYMBOLS", "PhaseSpace", "Series", "TINY", "TextOutput", "Tracks",
           "ZERO_WIDTH_EV", "bin_probabilities", "zero_small"]
