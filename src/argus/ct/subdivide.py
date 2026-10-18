"""Partir los tejidos de un TAC en varios materiales por densidad (mitigación del fallo de CTTRACK).

**El problema.** En CTTRACK (``penhmain.f`` 2025, líneas 11082–11084) la
pérdida continua (blanda) de energía de las partículas cargadas dentro del CT
usa la densidad nominal del .mat del tejido y no la del vóxel; solo las
colisiones duras usan la del vóxel. Con el modelo de rayos que reprodujo dos
simulaciones reales (el 72 % del frenado con la densidad del .mat y el resto
con la del vóxel), el error de alcance en TAC de tórax reales es grande, sobre
todo por el pulmón, cuyos vóxeles van de 0,06 a 0,9 g/cm³ dentro de un mismo
tejido.

**La mitigación general** (para cualquier TAC y cualquier calibración). Cada
tejido se parte en varios materiales por la densidad de sus vóxeles, con la
misma composición y, como densidad del .mat, la media de los vóxeles de cada
trozo: la media es lo que minimiza la suma de (ρ_mat − ρ_vóxel)², y el error
de CTTRACK solo depende de esa diferencia. El número de materiales está
limitado por MAXMAT = 10 de PENELOPE (menos los demás materiales del .in) y
cada material más alarga la preparación de penhmain (con protones, unos 33 s
por elemento del material).

Pasos de :func:`analyse`:

1. **Histogramas.** Por tejido, 1024 intervalos fijos entre las densidades que
   da la curva en su rango de HU (:func:`density_ranges`; no dependen de los
   datos, así que el resultado es determinista). Una pasada vectorizada acumula
   en cada intervalo el número de vóxeles, Σ(ρ − c) y Σ(ρ − c)², centradas en
   el centro c del rango para no perder cifras. Se parte por densidad y no por
   HU porque la curva de Schneider no es monótona (dentro del tejido blando
   baja de 1,1199 a 1,0762 g/cm³ entre 100 y 101 HU).
2. **Partición de cada tejido** (:func:`optimal_partitions`). k-medias 1-D
   exacta y ponderada sobre los intervalos no vacíos, por programación
   dinámica: la mínima suma de cuadrados dentro de los trozos, J_t(k), y dónde
   cortar para cada k. J_t(k) es convexa en k (el coste de un tramo cumple la
   desigualdad de Monge).
3. **Reparto entre tejidos** (:func:`greedy_allocation`). Cada material más va
   al tejido con más ganancia S_t²·(J_t(k_t) − J_t(k_t + 1)), con S_t el poder
   de frenado relativo al agua (empates: el tejido de menor índice). Con J_t
   convexas, esto da el mínimo de Σ S_t²·J_t(k_t) para cada número de
   materiales, y los repartos quedan anidados. Un tejido deja de partirse si ya
   es homogéneo (S_t·√(J_t(k)/N_t) < 0,01 g/cm³), si la partición óptima con un
   trozo más tendría uno de menos de max(5 cm³, 1 % del tejido) o si no le
   quedan densidades distintas. Los cortes son bordes del histograma
   redondeados a 5 cifras significativas; el número de vóxeles y la densidad
   media de cada trozo se calculan después, exactamente, con esos cortes
   (:func:`assign`: una densidad justo en un corte va al trozo de arriba).
4. **Modelo de rayos** (:class:`RayModel`). Para cada reparto, el error de
   alcance en todos los puntos en que un protón podría pararse (ρ ≥ 0,5 g/cm³
   y al menos 3 cm de agua equivalente recorridos) con haces en ±x y ±y de la
   rejilla del TAC: 0,72·Σ S_t·(ρ_mat − ρ_vóxel)·ds a lo largo del rayo. Se da
   la mediana, el percentil 95 y el valor cuadrático medio de su valor
   absoluto (mm de agua).
5. **Recomendación.** El primer reparto tras el cual un material más mejora el
   valor cuadrático medio en menos de max(3 % del de sin partir, 0,1 mm).

Los tejidos se dan con :class:`TissueInfo` (en el orden de la calibración) y
el TAC como dos matrices (NZ, NY, NX) como las de ``CtData``: el índice del
tejido de cada vóxel (−1: vacío o fuera) y su densidad (g/cm³).
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from .elements import mean_excitation_energy, relative_stopping_power

F_SOFT = 0.72  # parte del frenado (colisiones blandas) con la densidad del .mat en CTTRACK
PHINIT_S_PER_ELEMENT = 33.0  # s de preparación de penhmain (protones) por elemento de cada material más
NBINS = 1024  # intervalos del histograma de cada tejido
HOMOGENEOUS_RMS = 0.01  # g/cm³: S_t·√(J_t/N_t) por debajo, el tejido ya es homogéneo
MIN_PIECE_CM3 = 5.0  # trozo más pequeño (cm³)...
MIN_PIECE_FRACTION = 0.01  # ... y fracción de los vóxeles del tejido
STOP_MIN_DENSITY = 0.5  # g/cm³: puntos de parada solo en tejido (no en aire ni en pulmón)
STOP_MIN_WET_CM = 3.0  # cm de agua equivalente recorridos, como mínimo, hasta el punto de parada
MAX_RAY_VOXELS = 3_000_000  # vóxeles por dirección, como mucho (se toman rayos alternos)
DIRECTIONS = ("+x", "-x", "+y", "-y")
CUT_DIGITS = 5  # cifras significativas de los cortes
IMPROVEMENT_FRACTION = 0.03  # recomendación: mejora mínima del valor cuadrático medio...
IMPROVEMENT_MIN_MM = 0.1  # ... y como mínimo, en mm
REFERENCE_ENERGY_MEV = 70.0  # energía a la que se calcula S_t

# Por qué un tejido no admite más trozos (Analysis.limits).
LIMIT_VOID = "vacío"
LIMIT_ABSENT = "ausente"
LIMIT_LOCKED = "bloqueado"
LIMIT_HOMOGENEOUS = "homogéneo"
LIMIT_VALUES = "pocos valores"
LIMIT_SMALL = "trozo pequeño"
LIMIT_BUDGET = "presupuesto"

_AXIS = {"z": 0, "y": 1, "x": 2}
_CHUNK = 1 << 21


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------

@dataclass
class TissueInfo:
    """Un tejido de la calibración, con lo necesario para partirlo.

    * ``index``: su posición en la calibración, que es el valor que tiene en
      ``tissue_idx``; la lista de tejidos va en ese orden (0, 1, ...).
    * ``composition``: Z → fracción en masa. None si no se conoce: entonces
      S_t = 1 y 0 elementos, salvo que se den ``stopping`` y ``n_elements``.
    * ``i_ev``: la I (eV) con la que se generará su .mat; None: la regla de
      :func:`~argus.ct.elements.mean_excitation_energy` con la densidad media
      de sus vóxeles.
    * ``density_range``: (mín, máx) de la curva HU → densidad en su intervalo
      de HU (:func:`density_ranges`); fija la rejilla del histograma. None: el
      de sus vóxeles (entonces la rejilla depende de los datos).
    * ``locked``: no se parte (un solo material). Un tejido que reutiliza un
      material que ya tiene el .in y que ya se ha contado fuera del presupuesto
      se bloquea, y se suma uno a ``max_ct_materials``.
    * ``void``: va al material 0 (vacío): sus vóxeles no cuentan.
    * ``stopping``: poder de frenado másico relativo al agua (None: se calcula
      con la composición y la I a 70 MeV).
    * ``n_elements``: elementos de su .mat, para el tiempo de preparación (None:
      los de la composición).
    """

    index: int
    composition: Mapping[int, float] | None = None
    i_ev: float | None = None
    density_range: tuple[float, float] | None = None
    locked: bool = False
    void: bool = False
    name: str = ""
    stopping: float | None = None
    n_elements: int | None = None

    def elements(self) -> int:
        """Número de elementos de su .mat (0 si no se sabe)."""
        if self.n_elements is not None:
            return int(self.n_elements)
        if self.composition:
            return sum(1 for w in self.composition.values() if float(w) > 0.0)
        return 0


@dataclass(frozen=True)
class RangeError:
    """Error de alcance estimado (mm de agua) en los puntos de parada: mediana, p95 y valor cuadrático medio."""

    p50_mm: float
    p95_mm: float
    rms_mm: float
    n_stop: int  # puntos de parada (en todas las direcciones)


@dataclass(frozen=True)
class Allocation:
    """Un reparto de materiales entre los tejidos (todas las tuplas van en el orden de los tejidos).

    * ``pieces``: materiales (trozos) de cada tejido; 0 si está ausente o es vacío.
    * ``cuts``: cortes de cada tejido (g/cm³, crecientes, ``pieces − 1``); el
      trozo i tiene las densidades de ``cuts[i−1]`` (incluida) a ``cuts[i]``.
    * ``means``: densidad de cada trozo para su .mat: la media de sus vóxeles.
    * ``voxels``: vóxeles de cada trozo.
    * ``objective``: Σ S_t²·J_t(k_t), lo que minimiza el reparto ((g/cm³)²·vóxel).
    * ``error``: error de alcance estimado con el modelo de rayos.
    * ``setup_s``: preparación de penhmain de más respecto a no partir (s).
    """

    pieces: tuple[int, ...]
    cuts: tuple[tuple[float, ...], ...]
    means: tuple[tuple[float, ...], ...]
    voxels: tuple[tuple[int, ...], ...]
    objective: float
    error: RangeError
    setup_s: float

    @property
    def n_materials(self) -> int:
        """Materiales del CT en este reparto."""
        return int(sum(self.pieces))


@dataclass
class Analysis:
    """Resultado de :func:`analyse` (las tuplas por tejido van en el orden de los tejidos).

    * ``voxels``: vóxeles de cada tejido (0 en los vacíos).
    * ``absent``: tejidos (no vacíos) sin ningún vóxel: no llevan material
      (no hace falta su MFNAME).
    * ``stopping``, ``i_ev``: S_t e I (eV) usadas para cada tejido.
    * ``spread``: desviación típica de la densidad de cada tejido (g/cm³).
    * ``J``: J_t(k) para k = 1, 2, ... (suma de cuadrados dentro de los trozos
      de la partición óptima con k trozos, (g/cm³)²·vóxel).
    * ``kcap``: los trozos que admite cada tejido como mucho y ``limits`` por
      qué no más (``LIMIT_*``; «presupuesto»: con más materiales, admitiría más).
    * ``candidates``: los repartos anidados que caben en ``max_ct_materials``;
      el primero es «sin partir» (un material por tejido presente), aunque no
      quepa (``over_budget``).
    * ``recommended``: índice del reparto recomendado en ``candidates``.
    * ``strides``: rayos que se han tomado en cada eje (cada cuántos, en los
      dos ejes transversales), ``elapsed_s``: segundos del análisis.
    """

    tissues: tuple[TissueInfo, ...]
    voxels: tuple[int, ...]
    absent: tuple[int, ...]
    stopping: tuple[float, ...]
    i_ev: tuple[float | None, ...]
    spread: tuple[float, ...]
    J: tuple[tuple[float, ...], ...]
    kcap: tuple[int, ...]
    limits: tuple[str, ...]
    candidates: tuple[Allocation, ...]
    recommended: int
    max_ct_materials: int
    directions: tuple[str, ...]
    strides: dict[str, tuple[int, int]]
    elapsed_s: float = field(default=0.0, compare=False)

    @property
    def best(self) -> Allocation:
        """El reparto recomendado."""
        return self.candidates[self.recommended]

    @property
    def unsplit(self) -> Allocation:
        """El reparto sin partir (un material por tejido presente)."""
        return self.candidates[0]

    @property
    def extra_setup_s(self) -> float:
        """Preparación de penhmain de más con el reparto recomendado (s)."""
        return self.best.setup_s

    @property
    def over_budget(self) -> bool:
        """¿No cabe ni sin partir? (más tejidos presentes que ``max_ct_materials``)."""
        return self.unsplit.n_materials > self.max_ct_materials


# ---------------------------------------------------------------------------
# Rango de densidades de cada tejido
# ---------------------------------------------------------------------------

def density_ranges(curve: Sequence[tuple[float, float]], hu_starts: Sequence[float]) -> list[tuple[float, float]]:
    """(mín, máx) de la curva HU → densidad en el intervalo de HU de cada tejido.

    ``curve``: puntos (HU, densidad) en HU creciente; fuera de ellos, el valor
    del extremo (como ``Calibration.density``). ``hu_starts``: el HU desde el
    que empieza cada tejido, en orden (el del primero puede ser −∞); cada uno
    acaba donde empieza el siguiente y el último, en +∞. Con una calibración:
    ``density_ranges(cal.curve, [t.hu_min for t in cal.tissues])``.
    """
    h = np.array([float(p[0]) for p in curve], dtype=float)
    r = np.array([float(p[1]) for p in curve], dtype=float)
    if h.size == 0:
        raise ValueError("La curva no tiene puntos.")
    starts = [float(v) for v in hu_starts]
    out = []
    for i, a in enumerate(starts):
        b = starts[i + 1] if i + 1 < len(starts) else math.inf
        if not b > a:
            raise ValueError("Los HU de los tejidos tienen que ir en orden creciente.")
        xs = np.concatenate(([np.clip(a, h[0], h[-1]), np.clip(b, h[0], h[-1])], h[(h > a) & (h < b)]))
        vals = np.interp(xs, h, r)
        out.append((float(vals.min()), float(vals.max())))
    return out


# ---------------------------------------------------------------------------
# Partición óptima de un tejido y reparto entre tejidos
# ---------------------------------------------------------------------------

def _interval_costs(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    """C[i, j] = suma de cuadrados de los intervalos i..j−1 (+∞ si i ≥ j)."""
    n = p0.size
    dw = p0[None, :] - p0[:, None]
    ds = p1[None, :] - p1[:, None]
    c = p2[None, :] - p2[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        c -= ds * ds / dw
    np.maximum(c, 0.0, out=c)
    idx = np.arange(n)
    c[idx[:, None] >= idx[None, :]] = np.inf
    return c


def optimal_partitions(count, s1, s2, kmax: int) -> tuple[np.ndarray, list[np.ndarray]]:
    """k-medias 1-D exacta y ponderada sobre los intervalos no vacíos de un histograma.

    ``count``, ``s1``, ``s2``: vóxeles, Σ(ρ − c) y Σ(ρ − c)² de cada intervalo
    (c: un centro fijo cualquiera). Programación dinámica con el coste de cada
    tramo, S2 − S1²/W; entre particiones igual de buenas se queda con la del
    primer mínimo, así que el resultado es determinista.

    Devuelve ``J`` (``J[k−1]``: la mínima suma de cuadrados con k trozos, para
    k = 1 ... min(kmax, intervalos no vacíos)) y, para cada k, los límites de
    los trozos como posiciones en la lista de intervalos no vacíos:
    ``[0, p1, ..., m]`` (el trozo g tiene los intervalos no vacíos
    ``p_g ... p_{g+1} − 1``).
    """
    cnt = np.asarray(count, dtype=np.float64).reshape(-1)
    nz = np.flatnonzero(cnt > 0)
    m = int(nz.size)
    if m == 0:
        return np.zeros(0), []
    kk = max(1, min(int(kmax), m))
    p0 = np.concatenate(([0.0], np.cumsum(cnt[nz])))
    p1 = np.concatenate(([0.0], np.cumsum(np.asarray(s1, dtype=np.float64).reshape(-1)[nz])))
    p2 = np.concatenate(([0.0], np.cumsum(np.asarray(s2, dtype=np.float64).reshape(-1)[nz])))
    j1 = max(float(p2[m] - p1[m] * p1[m] / p0[m]), 0.0)
    if kk == 1:
        return np.array([j1]), [np.array([0, m])]
    c = _interval_costs(p0, p1, p2)
    d = c[0].copy()  # D_1[j]: los j primeros intervalos en un trozo
    js = [j1]
    back: list[np.ndarray] = []
    cols = np.arange(m + 1)
    for _k in range(2, kk + 1):
        a = d[:, None] + c
        arg = np.argmin(a, axis=0)  # primer mínimo
        d = a[arg, cols]
        back.append(arg)
        js.append(max(float(d[m]), 0.0))
    parts = [np.array([0, m])]
    for k in range(2, kk + 1):
        bounds = [m]
        pos = m
        for level in range(k, 1, -1):
            pos = int(back[level - 2][pos])
            bounds.append(pos)
        bounds.append(0)
        parts.append(np.array(bounds[::-1]))
    return np.array(js), parts


def greedy_allocation(J: Sequence[Sequence[float]], weights: Sequence[float], kcap: Sequence[int],
                      max_total: int) -> list[tuple[int, ...]]:
    """Repartos anidados: trozos de cada tejido para M = T0, T0 + 1, ... materiales (hasta ``max_total``).

    Empieza con un trozo por tejido con ``kcap ≥ 1`` (0 para los demás) y cada
    trozo más va al tejido con más ganancia ``weights[t]·(J[t][k−1] − J[t][k])``
    (``J[t][k−1]`` = J_t(k)) mientras tenga menos de ``kcap[t]`` trozos; en caso
    de empate, al de menor índice. Para en ``max_total`` materiales o cuando
    ningún tejido gana nada.
    """
    k = [1 if int(c) >= 1 else 0 for c in kcap]
    seq = [tuple(k)]
    while sum(k) < max_total:
        best, gain = -1, 0.0
        for t, (jt, wt, cap) in enumerate(zip(J, weights, kcap)):
            if 1 <= k[t] < int(cap):
                g = float(wt) * (float(jt[k[t] - 1]) - float(jt[k[t]]))
                if g > gain:
                    best, gain = t, g
        if best < 0:
            break
        k[best] += 1
        seq.append(tuple(k))
    return seq


def setup_seconds(pieces: Sequence[int], n_elements: Sequence[int]) -> float:
    """Preparación de penhmain de más (s): Σ_t (k_t − 1)·elementos_t·33 s."""
    return float(sum(max(int(k) - 1, 0) * int(n) for k, n in zip(pieces, n_elements)) * PHINIT_S_PER_ELEMENT)


# ---------------------------------------------------------------------------
# Asignación de los vóxeles a los trozos
# ---------------------------------------------------------------------------

def _cut_items(cuts_per_tissue) -> list[tuple[int, np.ndarray]]:
    items = cuts_per_tissue.items() if isinstance(cuts_per_tissue, Mapping) else enumerate(cuts_per_tissue)
    out = []
    for t, cuts in items:
        c = np.asarray(cuts if cuts is not None else (), dtype=np.float64).reshape(-1)
        if c.size and not (np.all(np.isfinite(c)) and np.all(np.diff(c) > 0)):
            raise ValueError(f"Los cortes del tejido {t} tienen que ser números en orden creciente.")
        out.append((int(t), c))
    return out


def assign(tissue_idx, density, cuts_per_tissue) -> np.ndarray:
    """Trozo de cada vóxel dentro de su tejido (0, 1, ...; −1 donde ``tissue_idx`` < 0).

    ``cuts_per_tissue``: los cortes de cada tejido (una lista por índice de
    tejido, como ``Allocation.cuts``, o un diccionario índice → cortes); un
    tejido sin cortes es un solo trozo. Una densidad justo en un corte va al
    trozo de arriba. Vale para matrices de cualquier forma.
    """
    ti = np.asarray(tissue_idx)
    rho = np.asarray(density, dtype=np.float64)
    if ti.shape != rho.shape:
        raise ValueError("tissue_idx y density tienen que tener la misma forma.")
    items = _cut_items(cuts_per_tissue)
    kmax = 1 + max((c.size for _t, c in items), default=0)
    out = np.where(ti < 0, -1, 0).astype(np.int8 if kmax <= 127 else np.int16)
    for t, c in items:
        if c.size == 0:
            continue
        sel = ti == t
        if sel.any():
            out[sel] = np.searchsorted(c, rho[sel], side="right")
    return out


def piece_stats(tissue_idx, density, cuts_per_tissue) -> dict[int, tuple[tuple[int, ...], tuple[float, ...]]]:
    """Vóxeles y densidad media de cada trozo con unos cortes: {tejido: (vóxeles, medias)}.

    La media es la densidad que se da a penhmat para el .mat del trozo (NaN si
    el trozo no tiene vóxeles). Solo los tejidos de ``cuts_per_tissue``.
    """
    ti = np.asarray(tissue_idx)
    rho = np.asarray(density, dtype=np.float64)
    pieces = assign(ti, rho, cuts_per_tissue)
    out = {}
    for t, c in _cut_items(cuts_per_tissue):
        sel = ti == t
        p = pieces[sel].astype(np.intp)
        n = np.bincount(p, minlength=c.size + 1)
        s = np.bincount(p, weights=rho[sel], minlength=c.size + 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = s / n
        out[t] = (tuple(int(v) for v in n), tuple(float(v) for v in mean))
    return out


# ---------------------------------------------------------------------------
# Modelo de rayos
# ---------------------------------------------------------------------------

def _check_directions(directions) -> tuple[str, ...]:
    dirs = tuple(str(d) for d in directions)
    if not dirs:
        raise ValueError("Hace falta al menos una dirección.")
    for d in dirs:
        if len(d) != 2 or d[0] not in "+-" or d[1] not in _AXIS:
            raise ValueError(f"Dirección no válida: {d!r} (+x, -x, +y, -y, +z o -z).")
    if len(set(dirs)) != len(dirs):
        raise ValueError("Hay direcciones repetidas.")
    return dirs


def _ray_view(shape: tuple[int, int, int], axis: int, max_voxels: int) -> tuple[tuple[slice, ...], tuple[int, int]]:
    """Rayos a lo largo de ``axis``: uno de cada (s1, s2) en los ejes transversales, con ≤ ``max_voxels`` vóxeles.

    De los pasos que caben, los que dejan más rayos; a igualdad, los más
    parecidos entre sí (y luego el menor s1).
    """
    a1, a2 = (a for a in (0, 1, 2) if a != axis)
    n1, n2, length = shape[a1], shape[a2], max(shape[axis], 1)
    limit = max(max_voxels // length, 1)  # rayos
    best = (1, 1)
    best_key = None
    for s1 in range(1, n1 + 1):
        r1 = -(-n1 // s1)
        if r1 > limit:
            continue
        s2 = max(1, -(-n2 // (limit // r1)))  # el menor paso con r1·⌈n2/s2⌉ ≤ limit
        while r1 * -(-n2 // s2) > limit:
            s2 += 1
        key = (-(r1 * -(-n2 // s2)), abs(s1 - s2), s1)
        if best_key is None or key < best_key:
            best, best_key = (s1, s2), key
        if s2 == 1:
            break  # con pasos s1 mayores solo quedan menos rayos
    if best_key is None:  # ni un rayo por fila cabe: uno solo
        best = (max(n1, 1), max(n2, 1))
    sl = [slice(None)] * 3
    sl[a1] = slice(None, None, best[0])
    sl[a2] = slice(None, None, best[1])
    return tuple(sl), best


class RayModel:
    """Error de alcance de CTTRACK a lo largo de rayos paralelos a los ejes de la rejilla del TAC.

    Error por vóxel: ``f_soft``·S_t·(ρ_mat − ρ_vóxel)·ds; el de un punto de
    parada es la suma desde que el rayo entra en el CT hasta ese vóxel
    (incluido). Puntos de parada: vóxeles con ρ ≥ 0,5 g/cm³ y al menos 3 cm de
    agua equivalente (Σ S_t·ρ_vóxel·ds) recorridos, y dentro de ``stop_mask``
    si se da. Los rayos se toman alternos (``strides``) para que cada dirección
    recorra como mucho ``max_voxels`` vóxeles. Las sumas de agua equivalente y
    los puntos de parada se calculan una vez; cada configuración (``rho_mat``)
    cuesta una suma acumulada en float32 por dirección.

    ``tissue_idx``, ``density``: (NZ, NY, NX); un tejido < 0 es vacío (no frena
    ni tiene puntos de parada). ``stopping``: S_t de cada tejido.
    ``voxel_size_cm``: (dx, dy, dz), como ``CtData.voxel_size``.
    """

    def __init__(self, tissue_idx, density, stopping: Sequence[float], voxel_size_cm, directions=DIRECTIONS, *,
                 max_voxels: int = MAX_RAY_VOXELS, stop_mask=None, f_soft: float = F_SOFT):
        ti = np.asarray(tissue_idx)
        s = np.asarray(stopping, dtype=np.float64).reshape(-1)
        if ti.size and int(ti.max()) >= s.size:
            raise ValueError("Hay vóxeles de un tejido sin poder de frenado.")
        self._setup(np.where(ti < 0, s.size, ti), density, np.append(s, 0.0), voxel_size_cm, directions,
                    max_voxels, stop_mask, f_soft)

    @classmethod
    def _from_codes(cls, code, density, s_ext, voxel_size_cm, directions, max_voxels, stop_mask, f_soft
                    ) -> RayModel:
        obj = cls.__new__(cls)
        obj._setup(code, density, s_ext, voxel_size_cm, directions, max_voxels, stop_mask, f_soft)
        return obj

    def _setup(self, code, density, s_ext, voxel_size_cm, directions, max_voxels, stop_mask, f_soft) -> None:
        code = np.asarray(code)
        if code.ndim != 3:
            raise ValueError("El TAC tiene que ser una matriz (NZ, NY, NX).")
        rho = np.asarray(density)
        if rho.shape != code.shape:
            raise ValueError("tissue_idx y density tienen que tener la misma forma.")
        vs = tuple(float(v) for v in voxel_size_cm)
        if len(vs) != 3 or not all(math.isfinite(v) and v > 0 for v in vs):
            raise ValueError("El tamaño de vóxel tiene que ser (dx, dy, dz) positivos.")
        self.shape = tuple(int(n) for n in code.shape)
        self.voxel_size_cm = vs
        self.directions = _check_directions(directions)
        self._rho = rho.astype(np.float32)
        self._rho[code == s_ext.size - 1] = 0.0  # vacío: no frena
        self._fs = (float(f_soft) * s_ext).astype(np.float32)[code]
        stop = None if stop_mask is None else np.asarray(stop_mask, dtype=bool)
        if stop is not None and stop.shape != code.shape:
            raise ValueError("stop_mask tiene que tener la forma del TAC.")
        ds_axis = {2: vs[0], 1: vs[1], 0: vs[2]}
        self.strides: dict[str, tuple[int, int]] = {}
        self._axes: dict[int, tuple[tuple[slice, ...], float, dict[str, np.ndarray]]] = {}
        for name, axis in _AXIS.items():
            signs = [d[0] for d in self.directions if d[1] == name]
            if not signs:
                continue
            sl, steps = _ray_view(self.shape, axis, int(max_voxels))
            ds = ds_axis[axis]
            rv = self._rho[sl].astype(np.float64)
            w = s_ext[code[sl]] * rv * ds  # agua equivalente de cada vóxel (cm)
            dense = rv >= STOP_MIN_DENSITY
            if stop is not None:
                dense &= stop[sl]
            masks = {}
            for sign in signs:
                if sign == "+":
                    wet = np.cumsum(w, axis=axis)
                else:
                    wet = np.flip(np.cumsum(np.flip(w, axis=axis), axis=axis), axis=axis)
                masks[sign] = dense & (wet >= STOP_MIN_WET_CM)
            self._axes[axis] = (sl, ds, masks)
            self.strides[name] = steps
        self.n_stop = int(sum(int(m.sum()) for _sl, _ds, ms in self._axes.values() for m in ms.values()))

    def errors(self, rho_mat) -> dict[str, np.ndarray]:
        """Error con signo (mm de agua) en cada punto de parada; positivo: ρ_mat > ρ_vóxel, el protón se para antes.

        ``rho_mat``: densidad del .mat de cada vóxel (NZ, NY, NX). Devuelve
        {dirección: errores} con los puntos en el orden de la matriz (C).
        """
        rm = np.asarray(rho_mat)
        if rm.shape != self.shape:
            raise ValueError("rho_mat tiene que tener la forma del TAC.")
        out: dict[str, np.ndarray] = {}
        for name, axis in _AXIS.items():
            if axis not in self._axes:
                continue
            sl, ds, masks = self._axes[axis]
            e = self._fs[sl] * (rm[sl].astype(np.float32, copy=False) - self._rho[sl])
            scale = np.float32(10.0 * ds)
            for sign, mask in masks.items():
                if sign == "+":
                    c = np.cumsum(e, axis=axis, dtype=np.float32)
                else:
                    c = np.flip(np.cumsum(np.flip(e, axis=axis), axis=axis, dtype=np.float32), axis=axis)
                out[sign + name] = c[mask] * scale
        return {d: out[d] for d in self.directions}

    def estimate(self, rho_mat) -> RangeError:
        """Mediana, p95 y valor cuadrático medio de |error| (mm) en todos los puntos de parada."""
        errs = self.errors(rho_mat)
        a = np.abs(np.concatenate([v.reshape(-1) for v in errs.values()])) if errs else np.zeros(0, np.float32)
        if a.size == 0:
            return RangeError(0.0, 0.0, 0.0, 0)
        p50, p95 = np.percentile(a, [50.0, 95.0])
        rms = math.sqrt(float(np.mean(np.square(a, dtype=np.float64))))
        return RangeError(float(p50), float(p95), rms, int(a.size))


# ---------------------------------------------------------------------------
# Análisis completo
# ---------------------------------------------------------------------------

def _codes(ti: np.ndarray, t_count: int, void: np.ndarray, mask) -> np.ndarray:
    """Tejido de cada vóxel (int16, plano) con ``t_count`` para vacío, fuera o un tejido vacío."""
    flat = ti.reshape(-1)
    lut = np.full(t_count + 1, t_count, dtype=np.int16)
    for t in range(t_count):
        if not void[t]:
            lut[t + 1] = t
    code = np.empty(flat.size, dtype=np.int16)
    for a in range(0, flat.size, _CHUNK):
        x = flat[a:a + _CHUNK].astype(np.intp)
        np.maximum(x, -1, out=x)
        x += 1
        code[a:a + _CHUNK] = lut[x]
    if mask is not None:
        m = np.asarray(mask, dtype=bool)
        if m.shape != ti.shape:
            raise ValueError("mask tiene que tener la forma del TAC.")
        code[~m.reshape(-1)] = t_count
    return code


def _grid(lo: np.ndarray, hi: np.ndarray, nbins: int) -> tuple[np.ndarray, np.ndarray]:
    """(centro, intervalos por g/cm³) de la rejilla de cada tejido (0 si el rango no tiene anchura)."""
    width = hi - lo
    return 0.5 * (lo + hi), np.where(width > 0, nbins / np.where(width > 0, width, 1.0), 0.0)


def _bins(d: np.ndarray, scale, nbins: int) -> np.ndarray:
    """Intervalo de cada vóxel a partir de d = ρ − centro (la misma cuenta en todas partes: monótona en ρ)."""
    x = d * scale
    x += 0.5 * nbins
    np.clip(x, 0.0, nbins - 1, out=x)
    return x.astype(np.intp)


def _histograms(code: np.ndarray, rho: np.ndarray, lo: np.ndarray, hi: np.ndarray, nbins: int
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(vóxeles, Σ(ρ−c), Σ(ρ−c)², c) por tejido e intervalo, en una pasada (por bloques)."""
    t_count = lo.size
    center, scale = _grid(lo, hi, nbins)
    c_x = np.append(center, 0.0)  # el último, «ningún tejido», se descarta
    sc_x = np.append(scale, 0.0)
    size = (t_count + 1) * nbins
    cnt = np.zeros(size, dtype=np.int64)
    s1 = np.zeros(size)
    s2 = np.zeros(size)
    for a in range(0, code.size, _CHUNK):
        cc = code[a:a + _CHUNK].astype(np.intp)
        d = rho[a:a + _CHUNK] - c_x[cc]
        b = _bins(d, sc_x[cc], nbins)
        cc *= nbins
        b += cc
        cnt += np.bincount(b, minlength=size)
        s1 += np.bincount(b, weights=d, minlength=size)
        d *= d
        s2 += np.bincount(b, weights=d, minlength=size)
    shape = (t_count + 1, nbins)
    return (cnt.reshape(shape)[:t_count], s1.reshape(shape)[:t_count], s2.reshape(shape)[:t_count], center)


def _round_cuts(values: Sequence[float], digits: int) -> tuple[float, ...]:
    return tuple(float(f"{v:.{digits}g}") for v in values)


class _TissueVoxels:
    """Vóxeles de un tejido (índices y densidades), para las estadísticas exactas de sus trozos."""

    def __init__(self, code: np.ndarray, rho: np.ndarray, t: int):
        self.idx = np.flatnonzero(code == t)
        self.rho = rho[self.idx]


def _pieces(tv: _TissueVoxels, raw_cuts: list[float], group_first_bins: list[int], scale: float, nbins: int,
            center: float) -> tuple[tuple[float, ...], tuple[float, ...], tuple[int, ...], np.ndarray]:
    """Cortes redondeados y estadísticas exactas (medias, vóxeles) de los trozos, y el trozo de cada vóxel.

    Se prueban 5 cifras significativas, luego más; si ni los bordes exactos del
    histograma dan trozos con vóxeles (datos justo en los bordes), el corte es la
    menor densidad del trozo de arriba (la partición del histograma, exacta).
    """
    k = len(raw_cuts) + 1
    for digits in range(CUT_DIGITS, 18):
        cuts = _round_cuts(raw_cuts, digits)
        if any(b <= a for a, b in zip(cuts, cuts[1:])):
            continue
        piece = np.searchsorted(np.asarray(cuts), tv.rho, side="right")
        counts = np.bincount(piece, minlength=k)
        if np.all(counts > 0):
            break
    else:
        b = _bins(tv.rho - center, scale, nbins)
        cuts = tuple(float(tv.rho[b >= fb].min()) for fb in group_first_bins)
        piece = np.searchsorted(np.asarray(cuts), tv.rho, side="right")
        counts = np.bincount(piece, minlength=k)
    sums = np.bincount(piece, weights=tv.rho - center, minlength=k)
    means = tuple(float(center + s / n) for s, n in zip(sums, counts))
    return cuts, means, tuple(int(n) for n in counts), piece


def _stopping(info: TissueInfo, mean_density: float | None) -> tuple[float, float | None]:
    """(S_t, I) de un tejido; ``mean_density`` decide si la I es la de un gas (sin ella, condensado)."""
    if info.stopping is not None:
        return float(info.stopping), info.i_ev
    if not info.composition:
        return 1.0, info.i_ev
    i_ev = info.i_ev
    if i_ev is None:
        rho = mean_density if mean_density is not None and mean_density > 0 else 1.0
        i_ev = mean_excitation_energy(info.composition, rho)[0]
    return relative_stopping_power(info.composition, i_ev, REFERENCE_ENERGY_MEV), float(i_ev)


def recommend(candidates: Sequence[Allocation]) -> int:
    """Índice del reparto recomendado: el primero tras el cual un material más mejora el valor cuadrático
    medio en menos de max(3 % del de sin partir, 0,1 mm); sin puntos de parada, el de sin partir."""
    if not candidates or candidates[0].error.n_stop == 0:
        return 0
    thr = max(IMPROVEMENT_FRACTION * candidates[0].error.rms_mm, IMPROVEMENT_MIN_MM)
    for i in range(len(candidates) - 1):
        if candidates[i].error.rms_mm - candidates[i + 1].error.rms_mm < thr:
            return i
    return len(candidates) - 1


def analyse(tissue_idx, density, voxel_size_cm, tissues: Sequence[TissueInfo], max_ct_materials: int,
            directions: Sequence[str] = DIRECTIONS, *, mask=None, stop_mask=None,
            max_ray_voxels: int = MAX_RAY_VOXELS) -> Analysis:
    """Cómo partir los tejidos de un TAC en materiales por densidad y cuánto error de alcance deja cada reparto.

    ``tissue_idx``: índice del tejido de cada vóxel (NZ, NY, NX), −1 si es
    vacío o está fuera (o fuera de ``mask``, si se da). ``density``: densidad
    de cada vóxel (g/cm³). ``voxel_size_cm``: (dx, dy, dz) en cm, como
    ``CtData.voxel_size``. ``tissues``: los tejidos en el orden de la
    calibración (``tissues[i].index == i``). ``max_ct_materials``: los
    materiales que puede usar el CT (MAXMAT menos los demás materiales del .in);
    cuenta todos los materiales de los tejidos presentes, también los de los
    bloqueados. ``directions``: de los haces del modelo de rayos (+x, -x, +y,
    -y, +z, -z). ``stop_mask``: dónde puede haber puntos de parada (por
    ejemplo, sin la mesa); por defecto, en todo el TAC.

    Véase el comentario del módulo. Tarda unos segundos con 10 millones de
    vóxeles.
    """
    t_start = time.perf_counter()
    ti = np.asarray(tissue_idx)
    if ti.ndim != 3:
        raise ValueError("El TAC tiene que ser una matriz (NZ, NY, NX).")
    if not np.issubdtype(ti.dtype, np.integer):
        raise ValueError("tissue_idx tiene que ser de enteros.")
    rho = np.asarray(density, dtype=np.float64)
    if rho.shape != ti.shape:
        raise ValueError("tissue_idx y density tienen que tener la misma forma.")
    vs = tuple(float(v) for v in voxel_size_cm)
    if len(vs) != 3 or not all(math.isfinite(v) and v > 0 for v in vs):
        raise ValueError("El tamaño de vóxel tiene que ser (dx, dy, dz) positivos.")
    infos = tuple(tissues)
    t_count = len(infos)
    if t_count == 0:
        raise ValueError("Hace falta al menos un tejido.")
    if any(int(info.index) != i for i, info in enumerate(infos)):
        raise ValueError("Los tejidos tienen que ir en orden: tissues[i].index == i.")
    if ti.size and int(ti.max()) >= t_count:
        raise ValueError("Hay vóxeles con un tejido que no está en la lista.")
    budget = int(max_ct_materials)
    dirs = _check_directions(directions)
    voxel_volume = vs[0] * vs[1] * vs[2]

    # Tejido de cada vóxel (t_count: ninguno) y densidades válidas.
    void = np.array([bool(info.void) for info in infos])
    code = _codes(ti, t_count, void, mask)
    flat_rho = rho.reshape(-1)
    finite = np.isfinite(flat_rho)
    if not finite.all():
        code[~finite] = t_count
        flat_rho = np.where(finite, flat_rho, 0.0)

    # Rejilla de cada tejido: el rango de la curva o, si no se da, el de sus vóxeles.
    lo = np.zeros(t_count)
    hi = np.zeros(t_count)
    for t, info in enumerate(infos):
        if info.density_range is not None:
            a, b = (float(v) for v in info.density_range)
            if not (math.isfinite(a) and math.isfinite(b) and b >= a):
                raise ValueError(f"Rango de densidades no válido para el tejido {t}: {info.density_range!r}.")
            lo[t], hi[t] = a, b
        elif not void[t]:
            sel = flat_rho[code == t]
            if sel.size:
                lo[t], hi[t] = float(sel.min()), float(sel.max())
    nbins = NBINS
    cnt, s1, s2, center = _histograms(code, flat_rho, lo, hi, nbins)
    width = hi - lo
    scale = _grid(lo, hi, nbins)[1]
    n_vox = cnt.sum(axis=1)
    tot1 = s1.sum(axis=1)
    tot2 = s2.sum(axis=1)
    present = [(not void[t]) and n_vox[t] > 0 for t in range(t_count)]
    mean1 = [float(center[t] + tot1[t] / n_vox[t]) if present[t] else None for t in range(t_count)]
    j1 = [max(float(tot2[t] - tot1[t] * tot1[t] / n_vox[t]), 0.0) if present[t] else 0.0 for t in range(t_count)]

    stopping = []
    i_used: list[float | None] = []
    for t, info in enumerate(infos):
        if void[t]:
            s, i_ev = 0.0, None
        else:
            rho_rule = mean1[t] if present[t] else (float(lo[t]) if info.density_range else None)
            s, i_ev = _stopping(info, rho_rule)
        stopping.append(s)
        i_used.append(i_ev)
    n_elem = [info.elements() for info in infos]

    # Partición óptima de cada tejido y cuántos trozos admite.
    t0 = sum(present)
    kmax_budget = max(1, budget - t0 + 1)
    j_all: list[tuple[float, ...]] = []
    parts_all: list[list[np.ndarray]] = []
    kcap: list[int] = []
    limits: list[str] = []
    for t, info in enumerate(infos):
        if void[t]:
            j_all.append(())
            parts_all.append([])
            kcap.append(0)
            limits.append(LIMIT_VOID)
            continue
        if not present[t]:
            j_all.append(())
            parts_all.append([])
            kcap.append(0)
            limits.append(LIMIT_ABSENT)
            continue
        n_t = float(n_vox[t])
        m_t = int(np.count_nonzero(cnt[t]))
        min_piece = max(MIN_PIECE_CM3 / voxel_volume, MIN_PIECE_FRACTION * n_t)
        k_small = int(n_t // min_piece)  # con más trozos, alguno sería pequeño
        kmax_t = min(kmax_budget, k_small, m_t)
        homogeneous = stopping[t] * math.sqrt(j1[t] / n_t) < HOMOGENEOUS_RMS
        if info.locked or homogeneous or kmax_t < 2:
            jt, parts = np.array([j1[t]]), [np.array([0, m_t])]
        else:
            jt, parts = optimal_partitions(cnt[t], s1[t], s2[t], kmax_t)
        j_all.append(tuple(float(v) for v in jt))
        parts_all.append(parts)
        if info.locked:  # si no haría falta partirlo, se dice eso (no es un problema)
            kcap.append(1)
            limits.append(LIMIT_HOMOGENEOUS if homogeneous else LIMIT_VALUES if m_t < 2 else LIMIT_LOCKED)
            continue
        p0 = np.concatenate(([0], np.cumsum(cnt[t][cnt[t] > 0])))
        k = 1
        while True:
            if stopping[t] * math.sqrt(jt[k - 1] / n_t) < HOMOGENEOUS_RMS:
                why = LIMIT_HOMOGENEOUS
                break
            if m_t < k + 1:
                why = LIMIT_VALUES
                break
            if k_small < k + 1:
                why = LIMIT_SMALL
                break
            if jt.size < k + 1:
                why = LIMIT_BUDGET
                break
            if float(np.diff(p0[parts[k]]).min()) < min_piece:
                why = LIMIT_SMALL
                break
            k += 1
        kcap.append(k)
        limits.append(why)

    weights = [s * s for s in stopping]
    seq = greedy_allocation(j_all, weights, kcap, budget)

    # Modelo de rayos y evaluación de cada reparto (anidados: cambia un tejido cada vez).
    s_ext = np.append(np.asarray(stopping, dtype=np.float64), 0.0)
    ray = RayModel._from_codes(code.reshape(ti.shape), flat_rho.reshape(ti.shape), s_ext, vs, dirs, max_ray_voxels,
                               stop_mask, F_SOFT)
    mean_ext = np.array([m if m is not None else 0.0 for m in mean1] + [0.0], dtype=np.float32)
    rho_mat = mean_ext[code]
    state: list[tuple[tuple[float, ...], tuple[float, ...], tuple[int, ...]]] = []
    for t in range(t_count):
        state.append(((), (mean1[t],), (int(n_vox[t]),)) if present[t] else ((), (), ()))
    voxels_cache: dict[int, _TissueVoxels] = {}
    candidates: list[Allocation] = []
    prev = seq[0]
    for i, kvec in enumerate(seq):
        if i > 0:
            t = next(tt for tt in range(t_count) if kvec[tt] != prev[tt])
            k = kvec[t]
            nzb = np.flatnonzero(cnt[t] > 0)
            bounds = parts_all[t][k - 1]
            first = [int(nzb[p]) for p in bounds[1:-1]]
            last = [int(nzb[p - 1]) for p in bounds[1:-1]]
            raw = [float(lo[t] + ((a + 1 + b) // 2) * (width[t] / nbins)) for a, b in zip(last, first)]
            if t not in voxels_cache:
                voxels_cache[t] = _TissueVoxels(code, flat_rho, t)
            tv = voxels_cache[t]
            cuts, means, counts, piece = _pieces(tv, raw, first, float(scale[t]), nbins, float(center[t]))
            rho_mat[tv.idx] = np.asarray(means, dtype=np.float32)[piece]
            state[t] = (cuts, means, counts)
        prev = kvec
        err = ray.estimate(rho_mat.reshape(ti.shape))
        objective = float(sum(weights[t] * j_all[t][kvec[t] - 1] for t in range(t_count) if kvec[t] >= 1))
        candidates.append(Allocation(pieces=tuple(kvec), cuts=tuple(s[0] for s in state),
                                     means=tuple(s[1] for s in state), voxels=tuple(s[2] for s in state),
                                     objective=objective, error=err, setup_s=setup_seconds(kvec, n_elem)))

    spread = tuple(math.sqrt(j1[t] / n_vox[t]) if present[t] else 0.0 for t in range(t_count))
    return Analysis(tissues=infos, voxels=tuple(int(v) if not void[t] else 0 for t, v in enumerate(n_vox)),
                    absent=tuple(t for t in range(t_count) if not void[t] and n_vox[t] == 0),
                    stopping=tuple(float(s) for s in stopping), i_ev=tuple(i_used), spread=spread,
                    J=tuple(j_all), kcap=tuple(kcap), limits=tuple(limits), candidates=tuple(candidates),
                    recommended=recommend(candidates), max_ct_materials=budget, directions=dirs,
                    strides=dict(ray.strides), elapsed_s=time.perf_counter() - t_start)


__all__ = ["DIRECTIONS", "F_SOFT", "MAX_RAY_VOXELS", "NBINS", "PHINIT_S_PER_ELEMENT", "Allocation", "Analysis",
           "RangeError", "RayModel", "TissueInfo", "analyse", "assign", "density_ranges", "greedy_allocation",
           "optimal_partitions", "piece_stats", "recommend", "setup_seconds"]
