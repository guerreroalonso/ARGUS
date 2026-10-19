"""La cascada en datos: las trayectorias de las primeras lluvias con su lluvia, su madre, su generación y su tiempo de
vuelo (0.13.0, ADR-017; la lluvia de cada trayectoria, exacta desde 0.13.2).

penhmain escribe las trayectorias de LTRACK en un fichero por tipo de partícula (electron.trk, photon.trk…): cada
trayectoria son sus puntos (x, y, z, E), separada de la siguiente por una línea en blanco, sin decir a qué lluvia
pertenece ni qué partícula la creó. Pero toda secundaria empieza exactamente en un vértice de la trayectoria que la
creó (penhmain escribe el punto de cada interacción con PTRACK y la secundaria sale de ese mismo X, Y, Z): con los
seis dígitos que escribe penhmain, el primer punto de cada secundaria coincide con un vértice (no el primero) de su
madre y con ningún otro. Así se reconstruye el árbol de cada lluvia:

* la madre de una trayectoria es la otra que tiene su primer punto como vértice interior o final (las hermanas que
  salen del mismo punto no cuentan: para ellas también es el primero; un fotón que crea una partícula pierde
  energía en ese punto);
* las que no tienen madre son las primarias (salen de la fuente);
* la generación es la distancia a la primaria.

Lo que penhmain no escribe no se puede reconstruir: una partícula que se absorbe donde nace (por debajo de EABS) no
deja trayectoria, y lo que salga de ella cuelga de la anterior. Por ejemplo, con EABS de positrones alto, los
fotones de aniquilación de un positrón absorbido al nacer salen del vértice del fotón que creó el par y se cuentan
como sus hijos (una generación menos).

**Partículas pegadas.** Con una fuente radiactiva (SRNUCL), las demás partículas de cada desintegración salen de la
pila (ILB(1) = 1) sin empezar trayectoria: sus puntos van a continuación de la última trayectoria de su tipo, o a
``fort.71``…``76`` si su fichero no está abierto todavía. ARGUS lee también esos ``fort`` y las separa
(``_split_pieces``: donde la energía sube, donde un fotón vuelve a un punto suyo, donde empieza la desintegración,
donde entra en línea recta desde ella, donde acaba una lluvia según los cortes). Son primarias (generación 0). (Las
copias de una partícula de un espacio de fases que se divide, IPSPLI, no: penhmain las trata como secundarias y
empiezan su trayectoria; ARGUS también las cuenta como primarias de su lluvia.)

**La lluvia de cada trayectoria, sin adivinar** (``_intervals``). Solo con lo que es seguro: en el fichero de cada
tipo, penhmain escribe una lluvia entera antes de la siguiente; la partícula de la fuente que empieza cada lluvia
(PTRACK(…, 2) al salir de la fuente) es lo primero que escribe de ella y se reconoce (un haz: las primarias del
tipo de la fuente; una fuente radiactiva puntual: las que salen de su punto, que dentro de la materia lo llevan dos
veces; una extensa: lo que sale del punto de cada desintegración es suyo; un espacio de fases: cada partícula del
fichero, con su lluvia); lo que sale de una trayectoria es de su lluvia. Así se acota, para cada trayectoria, de qué
lluvias puede ser. Si es una, es esa. Si no (sobre todo con una fuente radiactiva puntual: las demás partículas de
cada desintegración salen todas del mismo punto, y de una que no deja descendientes nada dice de cuál es), no se
reparte «por las energías» como antes: su lluvia es -1 y ``needed`` dice qué **cortes** hacen falta. Un corte es la
misma ejecución repetida con el mismo penhmain y las mismas semillas parando tras b lluvias (``penhan.cascade``):
sus .trk son el principio exacto de los de la ejecución entera, y su tamaño dice dónde acaba la lluvia b en el
fichero de cada tipo (``cuts``). Con todos los cortes, la lluvia de cada trayectoria es la de su sitio. Si lo que
se sabe no cuadra (``conflict``: una coincidencia de seis cifras, unos datos raros), no se usa nada más que el orden
y los cortes, y hacen falta todos.

Comprobado con un penhmain que escribe la lluvia y la generación de cada trayectoria (truth.txt; cada partícula de la
pila, en la suya): Co-60 puntual en la materia y en el vacío, Co-60 extensa en la materia y en el vacío, I-125
puntual, espacio de fases con y sin IPSPLI, haces de electrones y de fotones (extensa) y protones de 200 MeV:
11 574 trayectorias, todas en su lluvia, con su generación y sin tramos que no recorrió nadie.

El tiempo de vuelo de cada punto se calcula con la velocidad de la partícula en cada tramo (la de su energía al
empezar el tramo; la luz, c) y empieza en el de su madre en el vértice donde nació: las primarias salen en t = 0.
Es para ver la cascada desarrollarse en el tiempo: los fotones se alejan a la velocidad de la luz, los protones de
100 MeV van a casi la mitad…
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .data import PARTICLE_FILE_WORDS, Tracks

C_CM_NS = 29.9792458  # velocidad de la luz (cm/ns)
MASS_EV = {1: 510998.95, 2: 0.0, 3: 510998.95, 4: 938272088.16, 5: 3727379405.7, 6: 939565420.52}


def speed(kp: int, energy: np.ndarray) -> np.ndarray:
    """Velocidad (cm/ns) de una partícula de tipo ``kp`` con energía cinética ``energy`` (eV)."""
    e = np.maximum(np.asarray(energy, dtype=np.float64), 0.0)
    m = MASS_EV.get(int(kp), 0.0)
    if m <= 0.0:
        return np.full(e.shape, C_CM_NS)
    gamma = 1.0 + e / m
    beta = np.sqrt(np.maximum(1.0 - 1.0 / (gamma * gamma), 0.0))
    return C_CM_NS * beta


@dataclass
class ShowerSet:
    """Trayectorias de varias lluvias, con todo lo que hace falta para dibujarlas y animarlas.

    Por punto: ``points`` (x, y, z en cm), ``energy`` (eV) y ``time`` (ns desde que sale la primaria de su lluvia).
    Por trayectoria (``starts`` da el primer punto de cada una y, al final, el total): ``particle`` (KPAR),
    ``shower`` (0, 1…), ``parent`` (índice de la madre o -1) y ``generation`` (0 las primarias).
    """

    points: np.ndarray
    energy: np.ndarray
    time: np.ndarray
    starts: np.ndarray
    particle: np.ndarray
    shower: np.ndarray
    parent: np.ndarray
    generation: np.ndarray
    n_showers: int
    runs: list[str] = field(default_factory=list)
    orphans: int = 0  # secundarias sin madre reconocida (su lluvia, la que dicen su sitio y los cortes)
    ambiguous: int = 0  # secundarias con más de una madre posible en su lluvia (se toma la primera)
    pending: int = 0  # trayectorias de las que aún no se sabe la lluvia (``shower`` -1): faltan cortes
    needed: tuple[int, ...] = ()  # los cortes que faltan: tras cuántas lluvias (una sola ejecución)
    conflict: bool = False  # lo que se sabía no cuadraba: la lluvia, solo por los cortes (hacen falta todos)
    cuts: tuple[int, ...] = ()  # los cortes que había (una sola ejecución)

    @property
    def exact(self) -> bool:
        """¿Se sabe la lluvia de todas las trayectorias?"""
        return self.pending == 0

    @property
    def count(self) -> int:
        return max(len(self.starts) - 1, 0)

    def track(self, i: int) -> slice:
        return slice(int(self.starts[i]), int(self.starts[i + 1]))

    def lengths(self) -> np.ndarray:
        return np.diff(self.starts)

    def point_track(self) -> np.ndarray:
        """El índice de trayectoria de cada punto."""
        return np.repeat(np.arange(self.count, dtype=np.int32), self.lengths())

    def counts(self) -> dict[int, int]:
        """Trayectorias por tipo de partícula."""
        kp, n = np.unique(self.particle, return_counts=True)
        return {int(k): int(v) for k, v in zip(kp, n)}

    def segments(self) -> tuple[np.ndarray, np.ndarray]:
        """Índices (a, b) de los puntos de cada tramo recto (sin unir trayectorias distintas)."""
        n = len(self.points)
        if n < 2:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        a = np.arange(n - 1, dtype=np.int64)
        keep = np.ones(n - 1, dtype=bool)
        ends = self.starts[1:-1] - 1
        keep[ends[(ends >= 0) & (ends < n - 1)]] = False
        a = a[keep]
        return a, a + 1

    @property
    def t_max(self) -> float:
        t = self.time[np.isfinite(self.time)] if len(self.time) else self.time
        return float(t.max()) if len(t) else 0.0

    def bad_mask(self) -> np.ndarray:
        """Los puntos que no puede haber escrito penhmain (un .trk dañado): NaN, infinitos o a más de ``FAR_POINT``
        cm. No se dibujan sus tramos ni cuentan para el tiempo (hasta 0.17.2, uno solo hacía desaparecer o aplastaba
        toda la cascada en el visor)."""
        p = np.asarray(self.points, dtype=np.float64)
        if not len(p):
            return np.zeros(0, dtype=bool)
        return ~np.isfinite(p).all(axis=1) | (np.abs(np.nan_to_num(p, nan=0.0)) > FAR_POINT).any(axis=1)

    @property
    def bad_points(self) -> int:
        return int(np.count_nonzero(self.bad_mask()))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        good = ~self.bad_mask() if len(self.points) else np.zeros(0, dtype=bool)
        if not good.any():
            return np.zeros(3), np.zeros(3)
        p = self.points[good]
        return p.min(axis=0), p.max(axis=0)

    def subset(self, showers: range | slice | None = None, particles: set[int] | None = None) -> ShowerSet:
        """Las trayectorias de unas lluvias o partículas (para exportarlas o verlas en los cortes)."""
        keep = np.ones(self.count, dtype=bool)
        if showers is not None:
            r = range(*showers.indices(self.n_showers)) if isinstance(showers, slice) else showers
            keep &= (self.shower >= r.start) & (self.shower < r.stop)
        if particles is not None:
            keep &= np.isin(self.particle, list(particles))
        idx = np.flatnonzero(keep)
        lengths = self.lengths()[idx]
        pts = np.concatenate([np.arange(self.starts[i], self.starts[i + 1]) for i in idx]) if len(idx) else \
            np.zeros(0, dtype=np.int64)
        remap = -np.ones(self.count, dtype=np.int64)
        remap[idx] = np.arange(len(idx))
        parent = np.where(self.parent[idx] >= 0, remap[np.maximum(self.parent[idx], 0)], -1)
        return ShowerSet(self.points[pts], self.energy[pts], self.time[pts],
                         np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64), self.particle[idx],
                         self.shower[idx], parent.astype(np.int32), self.generation[idx], self.n_showers,
                         list(self.runs), pending=int(np.count_nonzero(self.shower[idx] < 0)))

    def to_tracks(self) -> list[Tracks]:
        """Como trayectorias de LTRACK (una lista por tipo de partícula), para dibujarlas sobre los cortes."""
        out = []
        for kp in sorted(set(int(k) for k in self.particle)):
            idx = np.flatnonzero(self.particle == kp)
            lengths = self.lengths()[idx]
            pts = np.concatenate([np.arange(self.starts[i], self.starts[i + 1]) for i in idx])
            arr = np.column_stack([self.points[pts], self.energy[pts].astype(np.float64)])
            out.append(Tracks(kp, arr, np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)))
        return out


# ---------------------------------------------------------------------------------------------------------------------
# Leer y reconstruir
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class FortTracks(Tracks):
    """Puntos de ``fort.71``…``76``: los que penhmain escribe con su unidad (71 electrones… 76 neutrones) antes de abrir
    el .trk de ese tipo (partículas de una desintegración, o copias de una del espacio de fases, que no empiezan
    trayectoria). Van antes que las del .trk y su primera trayectoria continúa una que no está: es un trozo."""


FORT_UNITS = {71: 1, 72: 2, 73: 3, 74: 4, 75: 5, 76: 6}


def read_run_tracks(folder: str | os.PathLike, *, fort: bool = False) -> list[Tracks]:
    """Las trayectorias de una carpeta (un .trk por tipo de partícula), en el orden de KPAR. Con ``fort``, también
    las de ``fort.71``…``76`` (cada una antes que las de su .trk)."""
    from .catalog import Identified, identify
    from .readers import read_tracks

    out = []
    folder = Path(folder)
    for p in sorted(folder.glob("*.trk")):
        ident = identify(p)
        if ident is None or ident.kind != "trk":
            continue
        kp = ident.particle or PARTICLE_FILE_WORDS.get(p.stem.lower())
        try:
            t = read_tracks(p, ident)
        except (OSError, ValueError):
            continue
        out.append(Tracks(kp, t.points, t.starts))
    if fort:
        for unit, kp in FORT_UNITS.items():
            p = folder / f"fort.{unit}"
            if not p.is_file():
                continue
            try:
                t = read_tracks(p, Identified("trk", particle=kp))
            except (OSError, ValueError):
                continue
            if t.count:
                out.append(FortTracks(kp, t.points, t.starts))
    # Por KPAR y, en cada uno, primero los fort (penhmain los escribió antes de abrir el .trk).
    return sorted(out, key=lambda t: (t.particle or 0, 0 if isinstance(t, FortTracks) else 1))


def _point_keys(pts: np.ndarray) -> np.ndarray:
    """Cada punto como una clave (sus tres números, con -0 igual a 0) para buscar iguales."""
    a = np.ascontiguousarray(np.asarray(pts, dtype=np.float64)[:, :3] + 0.0)
    return a.view(np.dtype((np.void, a.dtype.itemsize * 3))).ravel()


def _print6(a) -> np.ndarray:
    """Como los escribe penhmain (``1P,E13.5``: seis cifras), para comparar con los puntos de los .trk."""
    return np.array([float(f"{float(v):.5E}") for v in np.ravel(a)], dtype=np.float64).reshape(np.shape(a))


def _from_points(pts: np.ndarray, track_of: np.ndarray, particle: np.ndarray, sources: np.ndarray) -> np.ndarray:
    """Para cada punto interior (de 1 a n-2) de un fotón: ¿viene en línea recta de uno de ``sources`` y sigue igual
    hasta el punto siguiente (y el anterior no es ese)? Los fotones van de un punto a otro en recta, a centímetros:
    con las seis cifras de penhmain, la dirección se sabe a una diezmilésima de radián."""
    out = np.zeros(max(len(pts) - 2, 0), dtype=bool)
    if len(pts) < 3:
        return out
    idx = np.flatnonzero((particle[track_of[1:-1]] == 2) & (track_of[2:] == track_of[1:-1])) + 1
    if not len(idx):
        return out
    p1 = pts[idx]
    b = pts[idx + 1] - p1
    prev = pts[idx - 1]
    for d0 in sources:
        a = p1 - d0
        na = np.linalg.norm(a, axis=1)
        u = a / np.maximum(na, 1e-300)[:, None]
        along = (b * u).sum(axis=1)
        perp = np.linalg.norm(b - along[:, None] * u, axis=1)
        scale = np.abs(p1).max(axis=1) + np.abs(pts[idx + 1]).max(axis=1) + np.abs(d0).max() + 1e-3
        line = (na > 0) & (along > 0) & (perp <= 3e-5 * scale)
        line &= ~np.all(prev == d0, axis=1)
        out[idx[line] - 1] = True
    return out


def _split_pieces(pts: np.ndarray, energy: np.ndarray, starts: np.ndarray, particle: np.ndarray,
                  loose: np.ndarray, source_points: np.ndarray | None = None, *, forced: np.ndarray | None = None,
                  rules: bool = True, decay_points: np.ndarray | None = None, line_points: np.ndarray | None = None
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Separa las trayectorias que penhmain escribe juntas con una fuente radiactiva o un espacio de fases.

    Las demás partículas de una desintegración (y las copias de una partícula del espacio de fases que se divide)
    salen de la pila con ILB(1) = 1 y penhmain las simula sin PTRACK(…, 2): sus puntos van a continuación de la
    última trayectoria de su tipo, sin línea en blanco, y empiezan donde la desintegración (o la partícula del espacio
    de fases), con su energía. Se corta:

    * donde acaba una lluvia (``forced``: lo dicen los cortes, ver ``reconstruct``);
    * donde la energía sube (dentro de una trayectoria nunca sube; no se mira en los neutrones, que en un gas pueden
      ganarla);
    * donde un fotón vuelve a un punto suyo anterior (no el de justo antes): dos fotones de la misma desintegración
      seguidos (en los electrones no: los lentos dan pasos más cortos que lo que se ve con seis cifras y repiten
      puntos);
    * en los puntos de la fuente que se saben (``source_points``: el de una fuente puntual, las partículas del espacio
      de fases; ``decay_points``: el de cada desintegración de una fuente extensa) y, con una fuente puntual fuera de
      la materia, donde entra un fotón que viene en línea recta desde ella;
    * y, después, en cualquier otro punto donde se haya cortado (el mismo punto de partida, en otra trayectoria).

    Con ``rules`` falso (una fuente de una partícula por lluvia), solo donde acaba una lluvia. ``loose``: por
    trayectoria, si continúa una que no está (la primera de un ``fort.7x``). Devuelve (inicios, KPAR y si es un
    trozo, por trayectoria).
    """
    npts = len(pts)
    m = len(starts) - 1
    first = np.zeros(npts, dtype=bool)
    first[starts[:-1]] = True
    track_of = np.repeat(np.arange(m, dtype=np.int64), np.diff(starts))
    same_prev = np.zeros(npts, dtype=bool)
    if npts > 1:
        same_prev[1:] = np.all(pts[1:] == pts[:-1], axis=1)
    inner = ~first & ~same_prev
    cut = np.zeros(npts, dtype=bool)
    fc = np.zeros(npts, dtype=bool)
    if forced is not None and len(forced):
        fc[np.asarray(forced, dtype=np.int64)] = True
    if rules and npts > 1:
        keys = _point_keys(pts)
        rise = np.zeros(npts, dtype=bool)
        rise[1:] = energy[1:] > energy[:-1] * (1.0 + 2e-5) + 1e-9  # penhmain escribe 6 cifras
        cut |= rise & (particle[track_of] != 6)
        # Un fotón que vuelve a un punto suyo anterior (no el de justo antes): en cada trayectoria, puntos iguales que
        # no van seguidos. Solo fotones: van de un vértice a otro, a centímetros; un electrón lento da pasos más cortos
        # que lo que se ve con seis cifras y repite puntos.
        order = np.lexsort((np.arange(npts), keys, track_of))
        same_group = np.zeros(npts, dtype=bool)
        same_group[1:] = (track_of[order][1:] == track_of[order][:-1]) & (keys[order][1:] == keys[order][:-1])
        prev_idx = np.full(npts, -1, dtype=np.int64)
        prev_idx[order[1:][same_group[1:]]] = order[:-1][same_group[1:]]
        cut |= (prev_idx >= 0) & (prev_idx != np.arange(npts) - 1) & (particle[track_of] == 2)
        known = [np.asarray(a, dtype=np.float64).reshape(-1, 3) for a in (source_points, decay_points)
                 if a is not None and len(a)]
        if known:
            cut |= np.isin(keys, _point_keys(np.concatenate(known)))
        # Fuera de la materia, las demás partículas de una desintegración vuelan en el vacío y penhmain escribe su
        # primer punto donde entran en la geometría. Un fotón que entra viene en línea recta desde donde se
        # desintegró el núcleo y sigue igual hasta su primer punto: ese punto, el de entrada y el siguiente, en línea
        # (y el anterior no es aquel: entonces es la propia primaria que entra). ``line_points``: los puntos de donde
        # puede venir (el de una fuente puntual; con una extensa, donde empieza cada partícula que sale de la fuente).
        lp = line_points if line_points is not None else (source_points if source_points is not None and
                                                             len(source_points) == 1 else None)
        if lp is not None and len(lp) and npts > 2:
            cut[1:-1] |= _from_points(pts, track_of, particle, np.asarray(lp, dtype=np.float64).reshape(-1, 3))
        cut &= inner
        glued = cut | (fc & ~first)  # (donde acaba una lluvia y empieza otra trayectoria, no: es una secundaria)
        if glued.any():  # el mismo punto de partida en otras trayectorias
            cut |= np.isin(keys, np.unique(keys[glued])) & inner
    cut |= fc & ~first
    s0 = starts[:-1]
    new_starts = np.union1d(s0, np.flatnonzero(cut)).astype(np.int64)
    piece = np.isin(new_starts, np.flatnonzero(cut)) | np.isin(new_starts, s0[np.asarray(loose, dtype=bool)])
    new_particle = particle[track_of[new_starts]]
    return np.concatenate([new_starts, [npts]]).astype(np.int64), new_particle, piece


def _parents(pts: np.ndarray, starts: np.ndarray, skip: np.ndarray, energy: np.ndarray | None = None,
             particle: np.ndarray | None = None) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """La madre de cada trayectoria: la otra que tiene su primer punto como vértice (ver el principio del módulo).

    No son vértices el primer punto de cada trayectoria (las hermanas que salen del mismo punto), el segundo si repite
    el primero (penhmain escribe dos veces el de salida de una partícula de la fuente que empieza dentro de la
    materia) ni, en un fotón, un punto donde no pierde energía (un fotón que crea una partícula pierde al menos su
    energía: si no, es otro fotón de la fuente pegado a él que empieza ahí). ``skip``: las que son de la fuente (los
    trozos; las que salen de su punto o de una partícula del espacio de fases): no tienen madre. Devuelve (madre o -1,
    {trayectoria: madres posibles} cuando hay varias)."""
    m = len(starts) - 1
    npts = len(pts)
    track_of = np.repeat(np.arange(m, dtype=np.int64), np.diff(starts))
    first = np.zeros(npts, dtype=bool)
    first[starts[:-1]] = True
    head2 = np.zeros(npts, dtype=bool)
    s0 = starts[:-1]
    s1 = s0 + 1
    ok = s1 < starts[1:]
    if ok.any():
        eq = np.all(pts[s1[ok]] == pts[s0[ok]], axis=1)
        head2[s1[ok][eq]] = True
    vertex = ~first & ~head2
    if energy is not None and particle is not None and npts > 1:
        drop = np.zeros(npts, dtype=bool)
        drop[1:] = energy[1:] < energy[:-1] * (1.0 - 2e-5) - 1e-9  # penhmain escribe 6 cifras
        vertex &= drop | (particle[track_of] != 2)
    parent = -np.ones(m, dtype=np.int64)
    multi: dict[int, np.ndarray] = {}
    if npts < 2:
        return parent, multi
    # Los puntos del principio de una trayectoria que repiten el primero (una alfa lenta de una reacción nuclear no se
    # mueve en sus primeros pasos con las seis cifras de penhmain): son vértices «débiles». Con otro vértice en ese
    # punto, la madre es la otra (revisión de 0.16: la alfa pasaba a ser madre posible de sus hermanas, que salen del
    # mismo punto, y la cascada de protones necesitaba todos los cortes).
    idx = np.arange(npts, dtype=np.int64)
    moved = ~np.all(pts == pts[starts[:-1]][track_of], axis=1)
    stop = np.minimum.reduceat(np.where(moved, idx, npts), starts[:-1]) if m else np.zeros(0, dtype=np.int64)
    weak = ~first & (idx < stop[track_of])
    order = np.lexsort((pts[:, 2], pts[:, 1], pts[:, 0]))
    sp = pts[order]
    same_next = np.all(sp[1:] == sp[:-1], axis=1)
    if not same_next.any():
        return parent, multi
    edges = np.flatnonzero(np.diff(np.concatenate([[0], same_next.astype(np.int8), [0]])))
    for g0, g1 in zip(edges[0::2], edges[1::2] + 1):
        members = order[g0:g1]
        heads = members[first[members]]
        if not len(heads):
            continue
        inner = members[vertex[members]]
        if not len(inner):
            continue
        for h in heads:
            tr = int(track_of[h])
            if skip[tr]:
                continue  # de la fuente: una primaria (también los trozos: otra partícula de la desintegración, o una
                # copia de una del espacio de fases)
            cands = inner[track_of[inner] != tr]
            if not len(cands):
                continue
            strong = cands[~weak[cands]]
            if len(strong):
                cands = strong
            owners = np.unique(track_of[cands])
            if len(owners) == 1:
                parent[tr] = int(owners[0])
            else:
                multi[tr] = owners
    return parent, multi


FAR_POINT = 1.0e10  # cm: más lejos no hay geometría de PENGEOM (un punto así es de un .trk dañado)
SOURCE_WINDOW = 2000  # partículas del espacio de fases que se miran por delante de la última encontrada


def _match_source(roots: np.ndarray, particle: np.ndarray, first_pts: np.ndarray, first_e: np.ndarray,
                  source: list[tuple], one_each: bool = False) -> dict[int, int]:
    """La lluvia de cada primaria por su partícula del espacio de fases: [(KPAR, E, x, y, z, lluvia)] en el orden
    del fichero. penhmain las lee en orden y las copias de una (IPSPLI) salen del mismo punto con la misma energía:
    para cada tipo de partícula, cada primaria es la primera partícula de su tipo, desde la de la anterior, que sale
    de su punto con su energía (penhmain escribe 6 cifras).

    Si puede haber copias o partículas que la ruleta rusa no deja simular, una primaria que sale igual que otras
    partículas de lluvias distintas puede ser de cualquiera: no se da por sabida (los cortes lo dicen). Hasta 0.17.2
    se quedaba con la primera, y con un espacio de fases de partículas que salen todas del mismo punto con la misma
    energía todas sus trayectorias iban a la lluvia 1 (dándose por exactas). Con ``one_each`` (ni ruleta ni copias:
    :func:`penhan.cascade.psf_one_each`) cada primaria es la partícula siguiente de las que salen así."""
    out: dict[int, int] = {}
    if not len(roots):
        return out
    kinds = particle[roots]
    for kp in np.unique(kinds):
        recs = [r for r in source if r[0] == int(kp)]
        if not recs:
            continue
        arr = np.array([[r[1], r[2], r[3], r[4]] for r in recs], dtype=np.float64)
        sh = np.array([r[5] for r in recs], dtype=np.int64)
        j = 0
        for k in np.flatnonzero(kinds == kp):
            q = np.array([first_e[k], *first_pts[k]], dtype=np.float64)
            win = arr[j:j + SOURCE_WINDOW]
            ok = np.all(np.abs(win - q) <= 1e-5 * np.maximum(np.abs(win), np.abs(q)) + 1e-12, axis=1)
            hit = np.flatnonzero(ok)
            if not len(hit):
                continue
            h = int(hit[0])
            if one_each:
                out[int(roots[k])] = int(sh[j + h])
                j += h + 1
            elif np.all(sh[j + hit] == sh[j + h]):
                j += h
                out[int(roots[k])] = int(sh[j])
    return out


class _Union:
    """Unión de conjuntos (las trayectorias que son de la misma lluvia)."""

    def __init__(self, n: int):
        self.p = np.arange(n, dtype=np.int64)

    def find(self, a: int) -> int:
        p = self.p
        r = a
        while p[r] != r:
            r = p[r]
        while p[a] != r:
            p[a], a = r, p[a]
        return int(r)

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)

    def labels(self) -> np.ndarray:
        return np.array([self.find(i) for i in range(len(self.p))], dtype=np.int64)


HALL_CELLS = 4_000_000  # tamaño máximo de la tabla de _hall (valores de lo × valores de hi); más, con _hall_loop


def _hall(lo: np.ndarray, hi: np.ndarray) -> bool:
    """Lluvias distintas (``lo``…``hi`` de cada una): si k de ellas solo caben en k lluvias seguidas, las demás no
    pueden ser ninguna de esas (consistencia de límites de «todas distintas»). Cambia ``lo`` y ``hi``; False si no
    caben (contradicción).

    Es el mismo resultado que :func:`_hall_loop` (0.13.2), que mira los intervalos [a, b] uno a uno, pero mucho más
    deprisa (0.16.0: con 50 lluvias por ejecución era lo que más tardaba al abrir la cascada). Cada regla («si en
    [a, b] caben justo b - a + 1, las demás no pueden tener ninguna de esas lluvias»; «si caben más, no cuadra») solo
    estrecha los intervalos y estrecha más cuanto más estrechos son: aplicadas en cualquier orden, hasta que ninguna
    cambia nada, se llega siempre al mismo sitio (el punto fijo más amplio de todas) o a la misma contradicción. Aquí
    se aplican todas a la vez en cada vuelta, con una tabla de cuántas caben en cada [a, b] (a, los valores de
    ``lo``; b, los de ``hi``: con los demás no cambia nada)."""
    if not len(lo):
        return True
    if np.any(lo > hi):
        return False  # (como _hall_loop: _intervals no cuadra)
    while True:
        a_vals = np.unique(lo)
        b_vals = np.unique(hi)
        na, nb = len(a_vals), len(b_vals)
        if na * nb > HALL_CELLS:
            return _hall_loop(lo, hi)
        ia = np.searchsorted(a_vals, lo)  # lo == a_vals[ia]
        ib = np.searchsorted(b_vals, hi)  # hi == b_vals[ib]
        table = np.zeros((na, nb), dtype=np.int64)
        np.add.at(table, (ia, ib), 1)
        inside = np.cumsum(np.cumsum(table[::-1], axis=0)[::-1], axis=1)  # con lo >= a_vals[i] y hi <= b_vals[j]
        size = b_vals[None, :] - a_vals[:, None] + 1
        valid = size >= 1
        if np.any(valid & (inside > size)):
            return False
        hall = valid & (inside == size)
        if not hall.any():
            return True
        # lo sube a b + 1 si hay un [a, b] justo con a <= lo <= b < hi (la mayor de esas b).
        rows = np.logical_or.accumulate(hall, axis=0)  # algún a <= a_vals[i] con [a, b_vals[j]] justo
        last = np.maximum.accumulate(np.where(rows, np.arange(nb)[None, :], -1), axis=1)
        jlo = np.searchsorted(b_vals, lo, side="left")  # la primera b >= lo
        j = np.where(ib > 0, last[ia, np.maximum(ib - 1, 0)], -1)  # la mayor b < hi
        up = (ib > 0) & (j >= jlo)
        new_lo = np.where(up, b_vals[np.maximum(j, 0)] + 1, lo)
        # hi baja a a - 1 si hay un [a, b] justo con lo < a <= hi <= b (la menor de esas a).
        cols = np.logical_or.accumulate(hall[:, ::-1], axis=1)[:, ::-1]  # algún b >= b_vals[j] con [a_vals[i], b]
        first = np.minimum.accumulate(np.where(cols, np.arange(na)[:, None], na)[::-1], axis=0)[::-1]
        i0 = ia + 1  # la primera a > lo
        i1 = np.searchsorted(a_vals, hi, side="right")  # las a <= hi
        k = np.where(i0 < na, first[np.minimum(i0, na - 1), ib], na)
        down = (i0 < na) & (k < i1)
        new_hi = np.where(down, a_vals[np.minimum(k, na - 1)] - 1, hi)
        if np.array_equal(new_lo, lo) and np.array_equal(new_hi, hi):
            return True
        lo[:] = new_lo
        hi[:] = new_hi
        if np.any(lo > hi):
            return False


def _hall_loop(lo: np.ndarray, hi: np.ndarray) -> bool:
    """:func:`_hall` mirando cada [a, b] (``a`` de los valores de ``lo``, ``b`` de los de ``hi``) uno a uno (como hasta
    0.15): para tablas muy grandes, que no pasan con 50 lluvias por ejecución."""
    while True:
        changed = False
        for a in np.unique(lo):
            for b in np.unique(hi[hi >= a]):
                inside = (lo >= a) & (hi <= b)
                k = int(inside.sum())
                if k > b - a + 1:
                    return False
                if k == b - a + 1:
                    out = ~inside
                    m1 = out & (lo >= a) & (lo <= b)
                    m2 = out & (hi >= a) & (hi <= b)
                    if m1.any() or m2.any():
                        lo[m1] = b + 1
                        hi[m2] = a - 1
                        changed = True
                        if np.any(lo > hi):
                            return False
        if not changed:
            return True


def _intervals(n: int, comp: np.ndarray, streams: list[np.ndarray], group: np.ndarray, lo: np.ndarray,
               hi: np.ndarray, ulo: np.ndarray, uhi: np.ndarray, first: np.ndarray | None = None) -> bool:
    """De qué lluvias puede ser cada grupo de trayectorias (``comp``: el grupo de cada una; ``lo``…``hi``, por grupo,
    se van estrechando). Solo con lo que es seguro:

    * en el fichero de cada tipo de partícula, penhmain escribe las lluvias en orden (una después de otra);
    * la partícula de la fuente que empieza una lluvia (``first``: PTRACK(…, 2) al salir de la fuente) es lo primero
      que escribe penhmain de ella: lo que va antes en su fichero es de lluvias anteriores;
    * los cortes (``ulo``…``uhi``, por trayectoria: lo que dicen de su sitio en su fichero);
    * los grupos que son lluvias distintas (``group``: cada partícula de la fuente que empieza una lluvia, con lo que
      se sabe que es suyo) van en orden estricto en cada fichero y no comparten lluvia.

    False si no cuadra (lo que se ha supuesto de algún grupo es falso)."""
    np.maximum.at(lo, comp, ulo)
    np.minimum.at(hi, comp, uhi)
    gcomp = np.unique(comp[group[comp]]) if len(comp) else np.zeros(0, dtype=np.int64)
    if first is None:
        first = np.zeros(len(comp), dtype=bool)
    for _ in range(4 * n + 20):
        before = (lo.copy(), hi.copy())
        for idx in streams:
            if not len(idx):
                continue
            c = comp[idx]
            f = first[idx].astype(np.int64)
            prev = np.concatenate([[-1], np.maximum.accumulate(lo[c])[:-1]])  # lo más alto de lo anterior
            np.maximum.at(lo, c, np.maximum(prev + f, 0))
            b = hi[c] - f
            nxt = np.concatenate([np.minimum.accumulate(b[::-1])[::-1][1:], [n - 1]])  # lo más bajo de lo posterior
            np.minimum.at(hi, c, nxt)
            gi = idx[group[c]]
            if len(gi) > 1:
                gc = comp[gi]
                new = np.concatenate([[True], gc[1:] != gc[:-1]])
                bc = gc[new]  # el grupo de cada tramo seguido
                blo = lo[bc]
                prev = np.concatenate([[-1], np.maximum.accumulate(blo)[:-1]])
                np.maximum.at(lo, bc, prev + 1)
                bhi = hi[bc]
                nxt = np.concatenate([np.minimum.accumulate(bhi[::-1])[::-1][1:], [n]])
                np.minimum.at(hi, bc, nxt - 1)
        if len(gcomp) > 1:
            gl, gh = lo[gcomp].copy(), hi[gcomp].copy()
            if not _hall(gl, gh):
                return False
            lo[gcomp], hi[gcomp] = gl, gh
        if np.any(lo > hi):
            return False
        if np.array_equal(before[0], lo) and np.array_equal(before[1], hi):
            return True
    return not np.any(lo > hi)


def _cut_bounds(n: int, cuts: dict[int, dict[int, int]] | None, unit_kp: np.ndarray, unit_pos: np.ndarray
                ) -> tuple[np.ndarray, np.ndarray, set[int]]:
    """Lo que dicen los cortes de cada trayectoria: tras el corte b (las b primeras lluvias), lo que va antes de su
    sitio en el fichero de su tipo es de las lluvias 0…b-1 y lo de después, de b en adelante. Devuelve (desde, hasta
    y qué cortes hay)."""
    m = len(unit_kp)
    ulo = np.zeros(m, dtype=np.int64)
    uhi = np.full(m, max(n - 1, 0), dtype=np.int64)
    have = {int(b) for b in (cuts or {}) if 0 < int(b) < n}
    if not have:
        return ulo, uhi, have
    bs = np.array(sorted(have), dtype=np.int64)
    for kp in np.unique(unit_kp):
        sel = np.flatnonzero(unit_kp == kp)
        pos = np.array([int(cuts[int(b)].get(int(kp), 0)) for b in bs], dtype=np.int64)
        pos = np.maximum.accumulate(pos)  # (los cortes de un fichero nunca retroceden)
        k = np.searchsorted(pos, unit_pos[sel], side="right")  # cortes que quedan antes (o justo en su sitio)
        ulo[sel] = np.where(k > 0, bs[np.maximum(k - 1, 0)], 0)
        uhi[sel] = np.where(k < len(bs), bs[np.minimum(k, len(bs) - 1)] - 1, n - 1)
    return ulo, uhi, have


@dataclass
class _Mains:
    """Las partículas de la fuente que empiezan cada lluvia (penhmain les escribe PTRACK(…, 2) al salir de la fuente)
    y lo que se sabe que es de su lluvia."""

    units: list[int]  # trayectorias de lluvias distintas (una por lluvia: con lo suyo, grupos distintos)
    joins: list[tuple[int, int]]  # (trayectoria, otra de su misma lluvia)
    exact: dict[int, int]  # trayectoria → lluvia (espacio de fases)
    first: list[int] = field(default_factory=list)  # las que son lo primero que escribe penhmain de su lluvia


def _first_keys(pts: np.ndarray, starts: np.ndarray, units: np.ndarray) -> np.ndarray:
    return _point_keys(pts[starts[units]]) if len(units) else np.zeros(0, dtype=np.dtype((np.void, 24)))


def _continuations(out: _Mains, pts: np.ndarray, energy: np.ndarray, starts: np.ndarray, particle: np.ndarray,
                   roots: np.ndarray, *, same_point: bool) -> None:
    """La partícula de la fuente que llega a la materia por debajo de EABS: penhmain le escribe otra trayectoria de un
    punto (PTRACK(…, 2)) justo después de la suya, con la misma energía: donde empieza (``same_point``: una fuente
    radiactiva puntual dentro de la materia) o donde entra (un haz). Es la misma partícula: de su lluvia."""
    m = len(starts) - 1
    lengths = np.diff(starts)
    mains = set(out.units)
    is_root = np.zeros(m, dtype=bool)
    is_root[roots] = True
    for r in out.units:
        q = r + 1
        if lengths[r] != 1 or q >= m or q in mains or not is_root[q] or lengths[q] != 1 or particle[q] != particle[r]:
            continue
        if energy[starts[q]] != energy[starts[r]]:
            continue
        if same_point and not np.all(pts[starts[q]] == pts[starts[r]]):
            continue
        out.joins.append((q, r))


def _source_mains(pts: np.ndarray, energy: np.ndarray, starts: np.ndarray, particle: np.ndarray,
                  roots: np.ndarray, n: int | None, *, primary: int | None, pieces: bool, point: np.ndarray | None,
                  extended: bool) -> _Mains:
    """Qué trayectorias empiezan una lluvia, solo cuando es seguro.

    * Haz (una partícula por lluvia): las primarias del tipo de la fuente, si son tantas como lluvias (o, con una
      fuente puntual, las que salen de su punto).
    * Fuente radiactiva puntual: penhmain escribe el punto de la fuente al empezar cada desintegración. Fuera de la
      materia, solo la primera partícula de cada una sale de ahí (las demás empiezan donde entran en la geometría);
      dentro, salen todas, y la primera lleva el punto dos veces (o, por debajo de EABS, en dos trayectorias seguidas
      de un punto con la misma energía). Si las que lo cumplen son tantas como desintegraciones, son ellas.
    * Fuente radiactiva extensa: cada desintegración sale de un punto distinto. Las que salen de un punto donde
      empieza una primera partícula (su punto dos veces) son de su desintegración.
    """
    out = _Mains([], [], {})
    if not len(roots):
        return out
    m = len(starts) - 1
    lengths = np.diff(starts)
    s0 = starts[roots]
    nx = np.minimum(s0 + 1, len(pts) - 1)
    two = (lengths[roots] >= 2) & np.all(pts[nx] == pts[s0], axis=1) & (energy[nx] == energy[s0])
    nxt = np.minimum(roots + 1, m - 1)
    single = (lengths[roots] == 1) & (roots + 1 < m) & (lengths[nxt] == 1) & (particle[nxt] == particle[roots]) & \
        np.all(pts[starts[nxt]] == pts[s0], axis=1) & (energy[starts[nxt]] == energy[s0])
    pattern = two | single  # lo que escribe penhmain de una partícula de la fuente que empieza dentro de la materia
    rk = _first_keys(pts, starts, roots)
    if not pieces:
        cand = roots[particle[roots] == primary] if primary is not None else roots
        if point is not None and (n is None or len(cand) != n):
            at = roots[(rk == _point_keys(point)[0]) & ((particle[roots] == primary) if primary is not None else True)]
            if n is not None and len(at) == n:
                cand = at
        if n is None or len(cand) == n:
            out.units = [int(r) for r in cand]
            out.first = list(out.units)
        _continuations(out, pts, energy, starts, particle, roots, same_point=False)
        return out
    if point is not None and not extended:
        at = rk == _point_keys(point)[0]
        if n is None:  # sin saber cuántas son (fuera de la cascada): las que lo parecen
            out.units = [int(r) for r in roots[at & pattern]] if (at & pattern).any() else [int(r) for r in roots[at]]
        elif int(at.sum()) == n:
            out.units = [int(r) for r in roots[at]]
        elif int((at & pattern).sum()) == n:
            out.units = [int(r) for r in roots[at & pattern]]
        out.first = list(out.units)
        _continuations(out, pts, energy, starts, particle, roots, same_point=True)
        return out
    if extended:
        # Cada punto de salida de una primera partícula (su punto dos veces) es una desintegración; lo que sale de él,
        # suyo.
        pk = rk[pattern]
        uniq, first_at, counts = np.unique(pk, return_index=True, return_counts=True)
        owner = {bytes(k): int(roots[pattern][i]) for k, i in zip(uniq, first_at)}
        for r, k in zip(roots, rk):
            o = owner.get(bytes(k))
            if o is not None and o != int(r):
                out.joins.append((int(r), o))
        if n is None or len(uniq) == n:
            out.units = sorted(owner.values())
            # La que sale de la fuente es la única con su punto dos veces (si hay otra, no se sabe cuál es).
            out.first = sorted(o for k, o, c in zip(uniq, (owner[bytes(k)] for k in uniq), counts) if c == 1)
    return out


def _source_heads(pts: np.ndarray, energy: np.ndarray, starts: np.ndarray, particle: np.ndarray,
                  records: list[tuple] | None, points: np.ndarray | None) -> np.ndarray:
    """Las trayectorias que empiezan como una partícula de la fuente: en uno de sus puntos (``points``: el de una
    fuente radiactiva puntual o los de las desintegraciones de una extensa) o como una partícula del espacio de fases
    (``records``: su tipo, energía y posición, con las seis cifras de penhmain)."""
    m = len(starts) - 1
    out = np.zeros(m, dtype=bool)
    if not m:
        return out
    s0 = starts[:-1]
    if points is not None and len(points):
        out |= np.isin(_point_keys(pts[s0]), _point_keys(np.asarray(points, dtype=np.float64).reshape(-1, 3)))
    if records:
        rec = _print6(np.array([[r[1], r[2], r[3], r[4]] for r in records], dtype=np.float64))
        known = {(int(r[0]), *map(float, v)) for r, v in zip(records, rec)}
        for u in range(m):
            if (int(particle[u]), float(energy[s0[u]]), *map(float, pts[s0[u]])) in known:
                out[u] = True
    return out


def _decay_points(pts: np.ndarray, energy: np.ndarray, starts: np.ndarray, roots: np.ndarray) -> np.ndarray:
    """Los puntos donde empieza una primaria (sin madre) dentro de la materia: penhmain escribe su primer punto dos
    veces con la misma energía (al sacarla de la fuente y al empezar a simularla). Con una fuente extensa, el de cada
    desintegración. (Un electrón lento puede dar un primer paso más corto que seis cifras: también lo parece, pero
    tiene madre.)"""
    if not len(roots):
        return np.zeros((0, 3))
    lengths = np.diff(starts)
    s0 = starts[roots]
    nx = np.minimum(s0 + 1, len(pts) - 1)
    two = (lengths[roots] >= 2) & np.all(pts[nx] == pts[s0], axis=1) & (energy[nx] == energy[s0])
    return np.unique(pts[s0[two]], axis=0) if two.any() else np.zeros((0, 3))


def reconstruct(tracks: list[Tracks], primary: int | None = None, *, pieces: bool = False,
                source: list[tuple] | None = None, n_source: int | None = None,
                source_points: np.ndarray | None = None, extended: bool = False,
                cuts: dict[int, dict[int, int]] | None = None, one_each: bool = False) -> ShowerSet:
    """El árbol de las lluvias de una ejecución (ver el principio del módulo).

    ``primary``: el tipo de partícula de la fuente (SKPAR), si es uno. ``pieces``: fuente radiactiva (penhmain
    escribe juntas algunas trayectorias: se separan) o espacio de fases (con ``source``: sus partículas, [(KPAR, E, x,
    y, z, lluvia)] en el orden del fichero). ``n_source``: cuántas lluvias son (las de la ejecución, NSIMSH; con el
    espacio de fases, las de sus partículas; sin ella, las primarias que lo parecen). ``source_points``: el punto de
    una fuente puntual (como lo escribe penhmain). ``extended``: fuente extensa (SBOX). ``cuts``: {b: {KPAR:
    puntos}}, cuántos puntos había escrito penhmain en el fichero de cada tipo de partícula al acabar la lluvia b (lo
    dice la misma ejecución repetida con NSIMSH = b, ``penhan.cascade``). ``one_each``: con un espacio de fases, de
    cada partícula sale exactamente una trayectoria (``RunSource.one_each``).

    La lluvia de cada trayectoria sale solo de lo que es seguro (``_intervals``); la de las que no se sabe es -1 y
    ``needed`` dice qué cortes faltan para saberla."""
    parts = [t for t in tracks if t.count]
    n = int(n_source) if n_source is not None else None
    if not parts:
        empty = np.zeros(0)
        return ShowerSet(np.zeros((0, 3)), empty.astype(np.float32), empty.astype(np.float32),
                         np.zeros(1, dtype=np.int64), np.zeros(0, dtype=np.int8), np.zeros(0, dtype=np.int32),
                         np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.int16), int(n or 0))
    pts = np.concatenate([t.points[:, :3] for t in parts]).astype(np.float64)
    energy = np.concatenate([t.points[:, 3] for t in parts]).astype(np.float64)
    starts_l, particle_l, loose_l = [], [], []
    stream0: dict[int, int] = {}  # primer punto (índice global) del fichero de cada tipo de partícula
    stream_len: dict[int, int] = {}
    off = 0
    for t in parts:
        kp = int(t.particle or 0)
        stream0.setdefault(kp, off)
        stream_len[kp] = stream_len.get(kp, 0) + len(t.points)
        starts_l.append(t.starts[:-1] + off)
        particle_l.append(np.full(t.count, kp, dtype=np.int8))
        lz = np.zeros(t.count, dtype=bool)
        lz[0] = isinstance(t, FortTracks)
        loose_l.append(lz)
        off += len(t.points)
    starts = np.concatenate(starts_l + [np.array([off])]).astype(np.int64)
    particle = np.concatenate(particle_l)
    loose = np.concatenate(loose_l)
    npts = len(pts)
    # Donde acaba cada lluvia, según los cortes: ahí empieza un trozo (si no empieza ya una trayectoria).
    forced: list[int] = []
    for b, pos in (cuts or {}).items():
        if n is not None and not 0 < int(b) < n:
            continue
        for kp, p in pos.items():
            if int(kp) in stream0 and 0 < int(p) < stream_len[int(kp)]:
                forced.append(stream0[int(kp)] + int(p))
    forced_arr = np.unique(np.array(forced, dtype=np.int64)) if forced else None
    psf = bool(pieces and source)
    point = None
    if source_points is not None and len(source_points) and not psf:
        point = np.asarray(source_points, dtype=np.float64).reshape(-1, 3)[:1]
    if psf and source_points is None:
        source_points = _print6(np.array([[r[2], r[3], r[4]] for r in source], dtype=np.float64))
    piece = np.zeros(len(particle), dtype=bool)
    # Solo con una fuente radiactiva penhmain escribe juntas trayectorias distintas (las copias de IPSPLI de un
    # espacio de fases son secundarias para penhmain, ILB(1) + 1, y empiezan la suya): las reglas, solo entonces.
    rules = bool(pieces and not psf)
    if rules or forced_arr is not None:
        starts, particle, piece = _split_pieces(pts, energy, starts, particle, loose,
                                                source_points if rules else None, forced=forced_arr, rules=rules)
    heads = _source_heads(pts, energy, starts, particle, source if psf else None, point if rules else None)
    parent, multi = _parents(pts, starts, piece | heads, energy, particle)
    if rules and extended:
        # Fuente radiactiva extensa: el punto de cada desintegración (donde empieza su primera partícula, dos veces)
        # separa también lo que salga de él pegado a otra trayectoria; y un fotón que entra desde el vacío viene en
        # línea recta de donde empieza una primaria (la que empieza una desintegración fuera de la materia sale de
        # donde se desintegra el núcleo).
        roots0 = np.flatnonzero((parent < 0) & ~np.isin(np.arange(len(parent)), list(multi)) & ~piece)
        if len(roots0):
            dp = _decay_points(pts, energy, starts, roots0)
            lp = np.unique(pts[starts[roots0]], axis=0)
            starts, particle, piece = _split_pieces(pts, energy, np.concatenate(
                [np.concatenate(starts_l), [off]]).astype(np.int64), np.concatenate(particle_l), loose,
                source_points, forced=forced_arr, rules=True, decay_points=dp if len(dp) else None, line_points=lp)
            heads = _source_heads(pts, energy, starts, particle, None, dp if len(dp) else None)
            parent, multi = _parents(pts, starts, piece | heads, energy, particle)
    m = len(starts) - 1
    track_of = np.repeat(np.arange(m, dtype=np.int64), np.diff(starts))
    unit_kp = particle.astype(np.int64)
    unit_pos = starts[:-1] - np.array([stream0[int(k)] for k in unit_kp], dtype=np.int64)
    # Árboles por las madres seguras (las que tienen varias posibles, aparte de momento).
    tree_root = np.arange(m, dtype=np.int64)
    roots = np.flatnonzero(parent < 0)
    children: dict[int, list[int]] = {}
    for c in np.flatnonzero(parent >= 0):
        children.setdefault(int(parent[c]), []).append(int(c))
    reached = np.zeros(m, dtype=bool)
    for r in roots:
        reached[r] = True
        stack = [int(r)]
        while stack:
            tr = stack.pop()
            for c in children.get(tr, ()):
                if not reached[c]:
                    reached[c] = True
                    tree_root[c] = tree_root[tr]
                    stack.append(c)
    unreached = np.flatnonzero(~reached)  # ciclos (no debería haber): cada una, aparte
    tree_root[unreached] = unreached
    # La lluvia de cada trayectoria.
    if n is None:
        mains0 = _source_mains(pts, energy, starts, particle, roots, None, primary=primary, pieces=pieces,
                               point=point, extended=extended)
        n = len(mains0.units) if mains0.units else (len(roots) if not pieces else 1)
    n = max(int(n), 1)
    if psf:
        roots_src = roots[~np.isin(roots, list(multi))]
        exact = _match_source(roots_src, particle, pts[starts[roots_src]] if len(roots_src) else np.zeros((0, 3)),
                              energy[starts[roots_src]] if len(roots_src) else np.zeros(0), source, one_each)
        info = _Mains([], [], exact)
    else:
        info = _source_mains(pts, energy, starts, particle, roots, n, primary=primary, pieces=pieces, point=point,
                             extended=extended)
    ulo, uhi, have = _cut_bounds(n, cuts, unit_kp, unit_pos)
    full = len(have) >= n - 1
    streams = [np.flatnonzero(unit_kp == kp) for kp in np.unique(unit_kp)]
    conflict = False
    if full:
        # Con todos los cortes, la lluvia de cada trayectoria es la de su sitio en su fichero: nada más.
        unit_lo, unit_hi = ulo, uhi
        ok_unit = ulo == uhi
        sh_unit = np.where(ok_unit, ulo, -1)
    else:
        uf = _Union(m)
        for c in range(m):
            if tree_root[c] != c:
                uf.union(c, int(tree_root[c]))
        for a, b in info.joins:
            uf.union(a, b)
        by_shower: dict[int, int] = {}
        for r, k in info.exact.items():
            if k in by_shower:
                uf.union(r, by_shower[k])
            else:
                by_shower[k] = r
        comp = uf.labels()
        group = np.zeros(m, dtype=bool)
        group[[uf.find(u) for u in info.units]] = True
        group[[uf.find(r) for r in info.exact]] = True
        lo = np.zeros(m, dtype=np.int64)
        hi = np.full(m, n - 1, dtype=np.int64)
        for r, k in info.exact.items():
            c = uf.find(r)
            lo[c] = max(lo[c], k)
            hi[c] = min(hi[c], k)
        firsts = np.zeros(m, dtype=bool)
        firsts[info.first] = True
        if _intervals(n, comp, streams, group, lo, hi, ulo, uhi, firsts):
            unit_lo, unit_hi = lo[comp], hi[comp]
        else:
            # Algo de lo supuesto no cuadra (una coincidencia de seis cifras, un dato raro): solo el orden de cada
            # fichero y los cortes, trayectoria a trayectoria, y hacen falta todos los cortes.
            conflict = True
            unit_lo, unit_hi = ulo.copy(), uhi.copy()
            if not _intervals(n, np.arange(m, dtype=np.int64), streams, np.zeros(m, dtype=bool), unit_lo, unit_hi,
                              ulo, uhi):
                unit_lo, unit_hi = np.zeros(m, dtype=np.int64), np.full(m, n - 1, dtype=np.int64)
        ok_unit = unit_lo == unit_hi
        sh_unit = np.where(ok_unit, unit_lo, -1)
    needed: set[int] = set()
    if conflict:
        needed = set(range(1, n)) - have
    else:
        for a, b in zip(unit_lo[~ok_unit], unit_hi[~ok_unit]):
            needed.update(range(int(a) + 1, int(b) + 1))
        needed -= have
    # Las madres con varias posibles: la de su misma lluvia (si es una, la madre; si son varias, la primera).
    ambiguous = 0
    for c, owners in multi.items():
        k = sh_unit[c]
        same = [int(o) for o in owners if k >= 0 and sh_unit[o] == k]
        if len(same) == 1:
            parent[c] = same[0]
        elif same:
            parent[c] = same[0]
            ambiguous += 1
        elif k < 0:
            parent[c] = int(owners[0])
            ambiguous += 1
    # Una madre de otra lluvia (solo con cortes que lo dicen): no lo es.
    both = (parent >= 0) & (sh_unit >= 0)
    bad = both & (sh_unit != np.where(parent >= 0, sh_unit[np.maximum(parent, 0)], -1)) & \
        (sh_unit[np.maximum(parent, 0)] >= 0)
    parent[bad] = -1
    # Las que siguen sin madre y no son de la fuente: secundarias cuya madre no se ha reconocido.
    roots = np.flatnonzero(parent < 0)
    primaries = set(info.units) | set(info.exact) | {a for a, _b in info.joins}
    orphans = 0
    if not pieces:
        orphans = int(sum(1 for r in roots if int(r) not in primaries))
    # Generación y tiempo de vuelo (recorrido desde las primarias).
    generation = np.zeros(m, dtype=np.int16)
    children = {}
    for c in np.flatnonzero(parent >= 0):
        children.setdefault(int(parent[c]), []).append(int(c))
    seg_len = np.zeros(npts, dtype=np.float64)
    if npts > 1:
        seg_len[1:] = np.linalg.norm(pts[1:] - pts[:-1], axis=1)
    seg_len[starts[:-1]] = 0.0  # el primer punto de cada trayectoria no viene de la anterior
    # Un punto que no puede ser de penhmain (NaN, infinito, a más de FAR_POINT cm: un .trk dañado) no cuenta para el
    # tiempo: con él, el de todo lo que viene detrás sería NaN o enorme.
    bad = ~np.isfinite(pts).all(axis=1) | (np.abs(np.nan_to_num(pts, nan=0.0)) > FAR_POINT).any(axis=1)
    if bad.any():
        seg_len[bad] = 0.0
        seg_len[np.flatnonzero(bad[:-1]) + 1] = 0.0
    v = np.empty(npts, dtype=np.float64)
    for kp in np.unique(particle):
        mask = particle[track_of] == kp
        v[mask] = speed(int(kp), energy[mask])
    # Velocidad del tramo que llega al punto i: la de la energía en el punto anterior (al empezar el tramo).
    v_prev = np.empty(npts, dtype=np.float64)
    v_prev[0] = v[0]
    v_prev[1:] = v[:-1]
    dt = np.where(seg_len > 0, seg_len / np.maximum(v_prev, 1e-9), 0.0)
    dt = np.where(np.isfinite(dt), dt, 0.0)  # (una energía NaN en un .trk dañado)
    local = np.zeros(npts, dtype=np.float64)
    for i in range(m):  # tiempos dentro de cada trayectoria (desde su primer punto)
        a, b = starts[i], starts[i + 1]
        local[a:b] = np.cumsum(dt[a:b])
    t0 = np.zeros(m, dtype=np.float64)
    reached = np.zeros(m, dtype=bool)
    for r in roots:
        reached[r] = True
        stack = [int(r)]
        while stack:
            tr = stack.pop()
            for c in children.get(tr, ()):
                if reached[c]:
                    continue
                reached[c] = True
                generation[c] = generation[tr] + 1
                # Tiempo de la madre en el vértice donde nace: el primer punto interior de la madre con esas
                # coordenadas.
                head = starts[c]
                a, b = starts[tr], starts[tr + 1]
                hits = np.flatnonzero(np.all(pts[a + 1:b] == pts[head], axis=1))
                t0[c] = t0[tr] + (local[a + 1 + hits[0]] if len(hits) else local[b - 1])
                stack.append(c)
    time = local + np.repeat(t0, np.diff(starts))
    pending = int(np.count_nonzero(sh_unit < 0))
    return ShowerSet(pts, energy.astype(np.float32), time.astype(np.float32), starts, particle,
                     sh_unit.astype(np.int32), parent.astype(np.int32), generation, int(n), orphans=orphans,
                     ambiguous=ambiguous, pending=pending, needed=tuple(sorted(needed)), conflict=conflict,
                     cuts=tuple(sorted(have)))


def merge(sets: list[ShowerSet], names: list[str] | None = None, offsets: list[int] | None = None) -> ShowerSet:
    """Varias ejecuciones en una sola cascada: las lluvias de cada una, a continuación de las anteriores o, con
    ``offsets``, desde la que diga (la de su sitio en la cascada, aunque falten las de antes)."""
    keep = [i for i, s in enumerate(sets) if s.count]
    if offsets is not None:
        offsets = [int(offsets[i]) for i in keep]
    if names is not None and len(names) == len(sets):
        names = [names[i] for i in keep]
    sets = [sets[i] for i in keep]
    if not sets:
        return reconstruct([])
    off_pts = 0
    off_tr = 0
    off_sh = 0
    n_showers = 0
    starts, parent, shower = [], [], []
    for k, s in enumerate(sets):
        sh0 = offsets[k] if offsets is not None else off_sh
        starts.append(s.starts[:-1] + off_pts)
        parent.append(np.where(s.parent >= 0, s.parent + off_tr, -1))
        shower.append(np.where(s.shower >= 0, s.shower + sh0, -1))  # -1: aún no se sabe
        off_pts += len(s.points)
        off_tr += s.count
        off_sh += s.n_showers
        n_showers = max(n_showers, sh0 + s.n_showers)
    return ShowerSet(np.concatenate([s.points for s in sets]), np.concatenate([s.energy for s in sets]),
                     np.concatenate([s.time for s in sets]),
                     np.concatenate(starts + [np.array([off_pts])]).astype(np.int64),
                     np.concatenate([s.particle for s in sets]), np.concatenate(shower).astype(np.int32),
                     np.concatenate(parent).astype(np.int32), np.concatenate([s.generation for s in sets]),
                     n_showers, list(names or []), orphans=sum(s.orphans for s in sets),
                     ambiguous=sum(s.ambiguous for s in sets), pending=sum(s.pending for s in sets),
                     conflict=any(s.conflict for s in sets))


# Lo reconstruido de cada ejecución (carpeta → (firma de sus .trk, su .in, sus cortes y las lluvias; la primaria con
# que se ha reconstruido o _ANY; ShowerSet)): al seguir una cascada que se está simulando solo se leen las ejecuciones
# nuevas, y al abrir la cascada cada ejecución se lee una sola vez.
_ANY = object()  # la primaria no cuenta: la fuente sale del .in de la ejecución
_CACHE: dict[str, tuple[tuple, object, ShowerSet]] = {}
CACHE_ITEMS = 400
CACHE_BYTES = 768 * 2 ** 20  # lo que se guarda como mucho (sin contar la última): se va lo más antiguo
# Lo leen a la vez el hilo de la cascada que se simula, el del visor y los de la ventana de resultados: sin cerrojo,
# recorrer el diccionario mientras otro lo cambia da «dictionary keys changed during iteration» (y paraba la cascada).
_CACHE_LOCK = threading.RLock()
# Carpetas de cascadas que se están leyendo (cuántas veces a la vez: el visor y la ventana de resultados pueden leer
# la misma): lo suyo no se expulsa mientras tanto.
_PINNED: dict[str, int] = {}


def _set_bytes(s: ShowerSet) -> int:
    return sum(int(np.asarray(getattr(s, k)).nbytes) for k in ("points", "energy", "time", "starts", "particle",
                                                                 "shower", "parent", "generation"))


def _pinned(key: str) -> bool:
    return any(key == p or key.startswith(p + os.sep) for p in _PINNED)


def _remember(key: str, value: tuple) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        _CACHE[key] = value
        _evict(key)


def _evict(keep: str | None = None) -> None:
    """Expulsa lo más antiguo mientras se pase de CACHE_ITEMS o CACHE_BYTES, salvo ``keep`` (la última) y lo de las
    cascadas que se están leyendo (si todo lo es, se queda hasta que se guarde otra cosa, y así volver a abrir la
    cascada no lee nada). Sin esto, una cascada que no cabe se reconstruía entera una vez por cada recorrido de sus
    ejecuciones al abrirla (tres)."""
    with _CACHE_LOCK:
        if keep is None and _CACHE:
            keep = next(reversed(_CACHE))  # la más reciente
        total = sum(_set_bytes(v[2]) for v in _CACHE.values())
        while len(_CACHE) > 1 and (len(_CACHE) > CACHE_ITEMS or total > CACHE_BYTES):
            old = next((k for k in _CACHE if k != keep and not _pinned(k)), None)
            if old is None:
                break
            total -= _set_bytes(_CACHE.pop(old)[2])


def _cached(key: str, sig: tuple, primary) -> ShowerSet | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == sig and (hit[1] is _ANY or hit[1] == primary):
            _CACHE[key] = _CACHE.pop(key)  # la más reciente: la última que se va
            return hit[2]
    return None


@contextmanager
def keep_cascade(folder: str | os.PathLike):
    """Mientras dura, lo reconstruido de las ejecuciones de esa carpeta no se expulsa (abrir una cascada recorre sus
    ejecuciones varias veces: lo terminado, lo que falta por comprobar y las lluvias)."""
    key = os.path.normcase(os.path.abspath(folder))
    with _CACHE_LOCK:
        _PINNED[key] = _PINNED.get(key, 0) + 1
    try:
        yield
    finally:
        with _CACHE_LOCK:
            n = _PINNED.get(key, 1) - 1
            if n > 0:
                _PINNED[key] = n
            else:
                _PINNED.pop(key, None)  # (lo que sobre se va al guardar otra cosa: volver a abrirla no lee nada)


def _trk_signature(folder: Path, extra: tuple = ()) -> tuple:
    """Nombre, tamaño, fecha, nodo y fecha de cambio de cada fichero: una copia que conserva el tamaño y la fecha
    (otra versión restaurada de una copia de seguridad) cambia de nodo o de fecha de cambio."""
    out = []
    names = sorted(folder.glob("*.trk")) + [folder / f"fort.{u}" for u in FORT_UNITS] + [folder / n for n in extra]
    for p in names:
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((p.name, st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns))
    return tuple(out)


@dataclass
class RunSource:
    """La fuente de una ejecución, por su .in: el tipo de partícula de las primarias (si es uno), si penhmain escribe
    juntas algunas trayectorias (fuente radiactiva o espacio de fases) y, con un espacio de fases, sus partículas
    [(KPAR, E, x, y, z, lluvia)]; cuántas lluvias son."""

    primary: int | None = None
    pieces: bool = False
    particles: list[tuple] | None = None
    n_showers: int | None = None
    points: np.ndarray | None = None  # el punto de una fuente puntual (como lo escribe penhmain)
    extended: bool = False  # fuente extensa (SBOX)
    psf_n: list[int] | None = None  # con un espacio de fases, el N de penhmain al acabar cada lluvia (para cortar)
    # Con un espacio de fases: ¿sale de cada partícula exactamente una trayectoria (sin ruleta rusa ni copias de
    # IPSPLI)? Entonces cada primaria es la partícula siguiente del fichero que sale de su punto con su energía,
    # aunque la siguiente sea igual (un haz fino: las que no chocan llegan al mismo punto con la misma energía).
    one_each: bool = False


def run_source(folder: str | os.PathLike, in_name: str, showers: int | None = None) -> RunSource | None:
    """Lo que dice de su fuente el .in de una ejecución (None si no se puede leer). Con un espacio de fases, lee sus
    lluvias (hasta la primera con N ≥ ``showers``) en el fichero que leyó penhmain: el del caso o su trozo."""
    from ..penhan.cascade import psf_one_each, psf_record, psf_window, run_psf_showers
    from ..penhan.inputfile import parse_in_file

    folder = Path(folder)
    try:
        doc = parse_in_file(folder / in_name)
    except (OSError, ValueError):
        return None
    src = doc.model.source
    if src.from_psf:
        groups = run_psf_showers(folder, in_name, int(showers) if showers else None) or []
        parts = []
        weights = []
        for g_idx, g in enumerate(groups):
            for ln in g.lines:
                rec = psf_record(ln)
                if rec is not None:
                    kpar, e, x, y, z, _n, w = rec
                    parts.append((kpar, e, x, y, z, g_idx))
                    weights.append(w)
        return RunSource(None, True, parts, len(groups), psf_n=[g.n for g in groups],
                         one_each=psf_one_each(weights, psf_window(doc)))
    # Fuente puntual (sin SBOX): todo sale de SPOSIT, tal cual. Una lluvia por desintegración o partícula de la fuente:
    # las de la ejecución (NSIMSH).
    extended = bool(src.extended or src.bodies)
    pts = None if extended else _print6(np.array([src.position], dtype=np.float64))
    n = int(showers) if showers else None
    if src.radioactive:
        return RunSource(None, True, n_showers=n, points=pts, extended=extended)
    return RunSource(int(src.kparp), False, n_showers=n, points=pts, extended=extended)


CUTS_FILE = "argus-cortes.json"  # los cortes de una ejecución de la cascada (los escribe ``penhan.cascade``)


def trk_files(folder: str | os.PathLike) -> dict[str, int]:
    """Los ficheros de trayectorias de una carpeta (los .trk y los ``fort.71``…``76``) y su tamaño."""
    out = {}
    folder = Path(folder)
    for p in sorted(folder.glob("*.trk")) + [folder / f"fort.{u}" for u in FORT_UNITS]:
        try:
            if p.is_file():
                out[p.name] = p.stat().st_size
        except OSError:
            continue
    return out


def stream_points(folder: str | os.PathLike) -> dict[int, int]:
    """Cuántos puntos ha escrito penhmain de cada tipo de partícula (su ``fort.7x`` y su .trk)."""
    out: dict[int, int] = {}
    for t in read_run_tracks(folder, fort=True):
        kp = int(t.particle or 0)
        out[kp] = out.get(kp, 0) + len(t.points)
    return out


def read_cuts(folder: str | os.PathLike) -> dict[int, dict[int, int]]:
    """Los cortes de una ejecución ({b: {KPAR: puntos}}), si son de sus trayectorias de ahora (los ficheros, del
    mismo tamaño que cuando se cortaron)."""
    import json

    folder = Path(folder)
    try:
        data = json.loads((folder / CUTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("kind") != "cortes":
        return {}
    try:
        files = {str(k): int(v) for k, v in dict(data.get("files", {})).items()}
        if files != trk_files(folder):
            return {}
        return {int(b): {int(k): int(v) for k, v in dict(pos).items()} for b, pos in dict(data.get("cuts", {})).items()}
    except (TypeError, ValueError, AttributeError):
        return {}


def load_run(folder: str | os.PathLike, primary: int | None = None, *, in_name: str | None = None,
             showers: int | None = None) -> ShowerSet:
    """Las lluvias de una ejecución. Con ``in_name`` (su .in), la fuente sale de él (``run_source``); si no, de
    ``primary``. Usa los cortes que tenga (``read_cuts``).

    Se guarda lo reconstruido mientras no cambien sus ficheros. ``primary`` solo cuenta si la fuente no sale del .in:
    antes de 0.16.0 también contaba y, como al abrir la cascada se pide una vez sin ella (¿está terminada?) y otra con
    ella (sus lluvias), cada ejecución se reconstruía dos veces cada vez."""
    folder = Path(folder)
    sig = _trk_signature(folder, ((in_name,) if in_name else ()) + (CUTS_FILE,)) + (showers,)
    key = os.path.normcase(os.path.abspath(folder))
    hit = _cached(key, sig, primary)
    if hit is not None:
        return hit
    src = run_source(folder, in_name, showers) if in_name else None
    cuts = read_cuts(folder)
    if src is None:
        s = reconstruct(read_run_tracks(folder), primary, n_source=showers, cuts=cuts)
        used = primary
    else:
        own = src.primary is not None or src.pieces  # la primaria del .in (o ninguna): ``primary`` no cuenta
        s = reconstruct(read_run_tracks(folder, fort=src.pieces or bool(cuts)),
                        src.primary if src.primary is not None else (None if src.pieces else primary),
                        pieces=src.pieces, source=src.particles, n_source=src.n_showers, source_points=src.points,
                        extended=src.extended, cuts=cuts, one_each=src.one_each)
        used = _ANY if own else primary
    _remember(key, (sig, used, s))
    return s


_SEPARATED: dict[str, tuple[tuple, list[Tracks]]] = {}


def separate_tracks(folder: str | os.PathLike, in_name: str) -> list[Tracks] | None:
    """Las trayectorias de LTRACK de una simulación con una fuente radiactiva, cada partícula en la suya (también las
    de ``fort.71``…``76``): penhmain escribe las demás partículas de cada desintegración pegadas a la trayectoria
    anterior de su tipo y, dibujadas tal cual, saldrían unidas por tramos que no recorrió nadie. None si la fuente no es
    radiactiva (entonces no hay nada pegado: los .trk, tal cual) o no se puede leer el .in."""
    folder = Path(folder)
    src = run_source(folder, in_name)
    if src is None or not src.pieces or src.particles is not None:
        return None
    sig = _trk_signature(folder, (in_name,))
    key = os.path.normcase(os.path.abspath(folder))
    hit = _SEPARATED.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    s = reconstruct(read_run_tracks(folder, fort=True), None, pieces=True, source_points=src.points,
                    extended=src.extended)
    out = s.to_tracks()
    if len(_SEPARATED) > 50:
        _SEPARATED.clear()
    _SEPARATED[key] = (sig, out)
    return out


def load_cascade(info, primary: int | None = None, only_finished: bool = True) -> ShowerSet:
    """Las lluvias de una cascada (``penhan.cascade.CascadeInfo``) de las ejecuciones que ya han terminado y en las
    que se sabe la lluvia de cada trayectoria (o todas las que tengan trayectorias, sin ``only_finished``). La fuente
    de cada ejecución sale de su .in (``primary``, si no se puede leer)."""
    from ..penhan.cascade import run_counts, run_state

    sets, names, offsets = [], [], []
    off = 0
    # Cada ejecución, en su sitio: el de sus lluvias como se ven (con un espacio de fases, los grupos del fichero;
    # hasta 0.17.2 se reservaban las de penhmain, N, y quedaban lluvias vacías al final de cada una).
    for r, d, count in zip(info.runs, info.run_paths, run_counts(info)):
        if only_finished and run_state(d).status != "terminada":
            off += count  # su sitio se queda: las lluvias de las demás no cambian de número al terminar esta
            continue
        s = load_run(d, primary, in_name=info.case or None, showers=r.showers)
        if s.count and (s.exact or not only_finished):
            sets.append(s)
            names.append(r.folder)
            offsets.append(off)
        off += max(count, s.n_showers)
    return merge(sets, names, offsets)


__all__ = ["CUTS_FILE", "C_CM_NS", "FORT_UNITS", "FortTracks", "MASS_EV", "RunSource", "ShowerSet", "load_cascade",
           "load_run", "merge", "read_cuts", "read_run_tracks", "reconstruct", "run_source", "separate_tracks", "speed",
           "stream_points", "trk_files"]
