"""Un lector por cada tipo de fichero de resultados de penhmain.

El formato de cada fichero es el de las subrutinas de escritura de
``penhmain.f`` (2025): PMWRT (fuente), HENANW (partículas que salen), IMDETW,
ANDETW y ENDETW (detectores), DOSEW (dosis), P3DMW (EPROF), CTDOSEW (TAC) y
PTRACK (trayectorias). Los mapas se colocan por los índices que escribe
penhmain en cada fila (IX, IY, IZ), no por el orden de las filas, y sus bordes
salen de la cabecera (los centros de las filas se escriben con solo cuatro
cifras). Un mapa con menos filas de las que anuncia su cabecera se está
escribiendo todavía: :class:`IncompleteFile`.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .catalog import KINDS, Identified, identify
from .data import PARTICLE_NAMES, Curves, Grid, PhaseSpace, Series, TextOutput, Tracks, zero_small
from .formats import (
    FormatError,
    block_lengths,
    centers_to_edges,
    comment_lines,
    header_int,
    header_number,
    key_values,
    load_table,
    load_table_text,
    read_head,
    read_text,
)

HEADER_LINES = 40
PSF_MAX_ROWS = 2_000_000  # partículas de un espacio de fases que se leen como mucho


class IncompleteFile(FormatError):
    """El fichero tiene menos datos de los que anuncia (penhmain lo está escribiendo)."""


def _head_lines(path: Path) -> list[str]:
    return comment_lines(read_head(path, 16384), limit=HEADER_LINES)


def _sigma(err3: np.ndarray) -> np.ndarray:
    return np.abs(np.asarray(err3, dtype=np.float64)) / 3.0


def _need_cols(t: np.ndarray, n: int, path: Path, allow_empty: bool = False) -> None:
    if t.ndim != 2 or (t.shape[0] and t.shape[1] < n):
        raise FormatError(f"{path.name}: se esperaban al menos {n} columnas")
    if t.shape[0] == 0 and not allow_empty:
        raise IncompleteFile(f"{path.name}: todavía no tiene datos (penhmain lo está escribiendo)")


def _particle_series(t: np.ndarray, first: int, kinds=(1, 2, 3, 4, 5, 6)) -> list[Series]:
    out = []
    for i, kp in enumerate(kinds):
        c = first + 2 * i
        out.append(Series(PARTICLE_NAMES[kp], zero_small(t[:, c]), _sigma(t[:, c + 1]), particle=kp))
    return out


def _curves(kind: str, x: np.ndarray, series: list[Series], x_name: str, x_unit: str, y_name: str, y_unit: str,
            *, title: str | None = None, edges: np.ndarray | None = None, log: bool | None = False,
            histogram: bool = True, meta: dict | None = None) -> Curves:
    lg = False
    if edges is None and histogram and len(x):
        edges, lg = centers_to_edges(x, log)
    return Curves(kind, title or KINDS[kind].title, np.asarray(x, dtype=np.float64), x_name, x_unit, y_name, y_unit,
                  series, edges=edges, histogram=histogram, log_x=bool(lg), meta=meta or {})


# ---------------------------------------------------------------------------
# Dosis
# ---------------------------------------------------------------------------

def read_depth_dose(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 3, path)
    ct = ident.kind == "ct-ddose"
    s = Series("dosis", zero_small(t[:, 1]), _sigma(t[:, 2]))
    meta = {"note": "Incertidumbre subestimada (lo advierte penhmain)."} if ct else {}
    if not ct:
        meta["note"] = "Correcta solo si cada capa en Z tiene densidad uniforme."
    return _curves(ident.kind, t[:, 0], [s], "profundidad" if ct else "z", "cm", "Dosis en profundidad",
                   "eV·cm²/g", meta=meta)


def read_axis_dose(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 3, path)
    axis = ident.kind.split("-")[-2] if ident.kind.startswith("ct-") else ident.kind[0]
    s = Series("dosis", zero_small(t[:, 1]), _sigma(t[:, 2]))
    meta = {"axis": axis}
    if ident.kind.startswith("ct-"):
        meta["ct_local"] = True
    return _curves(ident.kind, t[:, 0], [s], axis, "cm", "Dosis", "eV/g", meta=meta)


def _check_header(path: Path, limits, counts) -> None:
    """Límites finitos y al menos un intervalo en cada eje (si no, el fichero está dañado)."""
    if any(not np.isfinite(v) for v in limits) or any(n < 1 for n in counts):
        raise FormatError(f"{path.name}: la cabecera tiene límites o números de intervalos que no tienen sentido")


def _box_header(lines: list[str], path: Path) -> tuple[list[float], list[int]]:
    lims = [header_number(lines, k) for k in ("XL =", "XU =", "YL =", "YU =", "ZL =", "ZU =")]
    nb = [header_int(lines, k) for k in ("NBX =", "NBY =", "NBZ =")]
    if any(v is None for v in lims) or any(v is None for v in nb):
        raise FormatError(f"{path.name}: falta la cabecera con los límites y el número de intervalos")
    _check_header(path, lims, nb)
    return [float(v) for v in lims], [int(v) for v in nb]


def _fill_box(t: np.ndarray, nb: list[int], icols: tuple[int, int, int], vcols: list[int], path: Path
              ) -> list[np.ndarray]:
    n = nb[0] * nb[1] * nb[2]
    if len(t) < n:
        raise IncompleteFile(f"{path.name}: tiene {len(t)} de {n} vóxeles (¿se está escribiendo?)")
    idx = t[:, icols].astype(np.int64) - 1
    if idx.min(initial=0) < 0 or np.any(idx.max(axis=0) >= np.array(nb)):
        raise FormatError(f"{path.name}: índices de vóxel fuera de la rejilla")
    out = []
    for c in vcols:
        a = np.zeros(nb, dtype=np.float64)
        a[idx[:, 0], idx[:, 1], idx[:, 2]] = t[:, c]
        out.append(a)
    return out


def read_3d_dose(path: Path, ident: Identified) -> Grid:
    lines = _head_lines(path)
    (xl, xu, yl, yu, zl, zu), nb = _box_header(lines, path)
    t = load_table(path)
    _need_cols(t, 10, path)
    dose, derr, edep, eerr = _fill_box(t, nb, (7, 8, 9), [3, 4, 5, 6], path)
    edges = (np.linspace(xl, xu, nb[0] + 1), np.linspace(yl, yu, nb[1] + 1), np.linspace(zl, zu, nb[2] + 1))
    return Grid("3d-dose-map", KINDS["3d-dose-map"].title, "box", ("x", "y", "z"), edges, zero_small(dose),
                _sigma(derr), "Dosis", "eV/g",
                extra={"Energía depositada": (zero_small(edep), _sigma(eerr), "eV")})


def read_2d_dose(path: Path, ident: Identified) -> Grid:
    lines = _head_lines(path)
    ru = header_number(lines, "RU =")
    zl, zu = header_number(lines, "ZL ="), header_number(lines, "ZU =")
    nbr, nbz = header_int(lines, "NBR ="), header_int(lines, "NBZ =")
    if None in (ru, zl, zu, nbr, nbz):
        raise FormatError(f"{path.name}: falta la cabecera del cilindro de dosis")
    _check_header(path, (ru, zl, zu), (nbr, nbz))
    t = load_table(path)
    _need_cols(t, 6, path)
    n = nbr * nbz
    if len(t) < n:
        raise IncompleteFile(f"{path.name}: tiene {len(t)} de {n} celdas")
    t = t[:n]
    shape = (nbr, nbz)
    edges = (np.linspace(0.0, ru, nbr + 1), np.linspace(zl, zu, nbz + 1))
    return Grid("2d-dose-map", KINDS["2d-dose-map"].title, "cylinder", ("r", "z"), edges,
                zero_small(t[:, 2].reshape(shape)), _sigma(t[:, 3].reshape(shape)), "Dosis", "eV/g",
                extra={"Energía depositada": (zero_small(t[:, 4].reshape(shape)), _sigma(t[:, 5].reshape(shape)),
                                              "eV")})


def read_radial_dose(path: Path, ident: Identified) -> Grid:
    lines = _head_lines(path)
    ru, nbr = header_number(lines, "RU ="), header_int(lines, "NBR =")
    if ru is None or nbr is None:
        raise FormatError(f"{path.name}: falta la cabecera de la esfera de dosis")
    _check_header(path, (ru,), (nbr,))
    t = load_table(path)
    _need_cols(t, 5, path)
    if len(t) < nbr:
        raise IncompleteFile(f"{path.name}: tiene {len(t)} de {nbr} cascarones")
    t = t[:nbr]
    return Grid("radial-dose", KINDS["radial-dose"].title, "sphere", ("r",), (np.linspace(0.0, ru, nbr + 1),),
                zero_small(t[:, 1]), _sigma(t[:, 2]), "Dosis", "eV/g",
                extra={"Energía por unidad de radio": (zero_small(t[:, 3]), _sigma(t[:, 4]), "eV/cm")})


def read_key_values(path: Path, ident: Identified) -> TextOutput:
    text = read_text(path)
    return TextOutput(ident.kind, KINDS[ident.kind].title, text, meta=key_values(text))


# ---------------------------------------------------------------------------
# TAC
# ---------------------------------------------------------------------------

def read_ct_3d(path: Path, ident: Identified) -> Grid:
    lines = _head_lines(path)
    (xl, xu, yl, yu, zl, zu), nb = _box_header(lines, path)
    t = load_table(path)
    _need_cols(t, 5, path)
    dose, derr = _fill_box(t, nb, (0, 1, 2), [3, 4], path)
    edges = (np.linspace(xl, xu, nb[0] + 1), np.linspace(yl, yu, nb[1] + 1), np.linspace(zl, zu, nb[2] + 1))
    return Grid("ct-3d-dose", KINDS["ct-3d-dose"].title, "ct", ("x", "y", "z"), edges, zero_small(dose),
                _sigma(derr), "Dosis", "eV/g", meta={"ct_local": True})


def read_ct_dvh(path: Path, ident: Identified) -> Curves:
    lines = _head_lines(path)
    t = load_table(path)
    _need_cols(t, 4, path)
    dmax = header_number(lines, "Maximum dose =")
    series = [Series("acumulado", t[:, 3], None, quantity="Fracción del volumen con más dosis", unit=""),
              Series("diferencial", t[:, 2], None, quantity="DVH diferencial", unit="cm³/(eV/g)")]
    return _curves("ct-dvh", t[:, 0], series, "dosis", "eV/g", "Fracción del volumen", "",
                   meta={"max_dose": dmax, "note": "DVH de todos los vóxeles del TAC."})


# ---------------------------------------------------------------------------
# Partículas que salen
# ---------------------------------------------------------------------------

def read_energy_updown(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 13, path)
    return _curves(ident.kind, t[:, 0], _particle_series(t, 1), "E", "eV", "Densidad de probabilidad",
                   "1/(eV·partícula)", log=None)


def read_angle(path: Path, ident: Identified) -> Grid:
    text = read_text(path)
    t = load_table_text(text)
    _need_cols(t, 14, path)
    lengths = block_lengths(text)
    nph = lengths[0] if lengths else 1
    if nph < 1 or len(t) % nph:
        raise IncompleteFile(f"{path.name}: bloques incompletos")
    nth = len(t) // nph
    theta = t[::nph, 0]
    phi = t[:nph, 1]
    th_edges, log = centers_to_edges(theta)
    ph_edges = np.linspace(0.0, 360.0, nph + 1)
    extra = {}
    layers = []
    for i, kp in enumerate(range(1, 7)):
        v = zero_small(t[:, 2 + 2 * i]).reshape(nth, nph)
        e = _sigma(t[:, 3 + 2 * i]).reshape(nth, nph)
        extra[PARTICLE_NAMES[kp]] = (v, e, "1/(sr·partícula)")
        layers.append(PARTICLE_NAMES[kp])
    main = max(layers, key=lambda n: float(extra[n][0].sum()))  # la partícula que más sale
    v, e, _ = extra[main]
    return Grid("angle", KINDS["angle"].title, "angles", ("θ", "φ"), (th_edges, ph_edges), v, e,
                "Densidad de probabilidad", "1/(sr·partícula)", extra=extra,
                meta={"layers": layers, "main": main, "log_theta": log, "phi_centers": phi})


def read_polar_angle(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 5, path)
    kp = ident.particle
    name = PARTICLE_NAMES.get(kp, "partículas")
    series = [Series(name, zero_small(t[:, 1]), _sigma(t[:, 2]), particle=kp),
              Series("energía media", zero_small(t[:, 3]), _sigma(t[:, 4]), particle=kp, unit="eV",
                     quantity="Energía media", weight_of=name)]
    title = f"Ángulo polar de los {name} que salen" if kp else KINDS["polar-angle"].title
    return _curves("polar-angle", t[:, 0], series, "θ", "°", "Densidad de probabilidad", "1/sr", title=title,
                   log=None, meta={"particle": kp})


# ---------------------------------------------------------------------------
# Detectores
# ---------------------------------------------------------------------------

def read_impdet(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 15, path)
    fl = ident.kind == "fln-impdet"
    series = [Series("total", zero_small(t[:, 1]), _sigma(t[:, 2]))] + _particle_series(t, 3)
    title = KINDS[ident.kind].title + (f" n.º {ident.detector}" if ident.detector else "")
    if fl:
        return _curves(ident.kind, t[:, 0], series, "E", "eV", "Fluencia integrada en el volumen",
                       "cm/(eV·partícula)", title=title, log=None, meta={"detector": ident.detector})
    return _curves(ident.kind, t[:, 0], series, "E", "eV", "Densidad de probabilidad", "1/(eV·partícula)",
                   title=title, log=None, meta={"detector": ident.detector})


def read_age(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 3, path)
    title = KINDS["age-impdet"].title + (f" n.º {ident.detector}" if ident.detector else "")
    return _curves("age-impdet", t[:, 0], [Series("edad", zero_small(t[:, 1]), _sigma(t[:, 2]))], "edad", "s",
                   "Densidad de probabilidad", "1/s", title=title, log=None, meta={"detector": ident.detector})


_ANG_PART = re.compile(r"2nd-3rd columns: (electron|photon|positron|proton|alpha|neutron) spectrum", re.I)


def read_angdet(path: Path, ident: Identified) -> Curves:
    lines = _head_lines(path)
    t = load_table(path)
    meta: dict = {"detector": ident.detector}
    for key, name in (("theta_1 =", "theta1"), ("theta_2 =", "theta2"), ("phi_1 =", "phi1"), ("phi_2 =", "phi2")):
        meta[name] = header_number(lines, key)
    head = " ".join(lines)
    m = _ANG_PART.search(head)
    if m and "4th-5th columns" not in head:
        kp = {"electron": 1, "photon": 2, "positron": 3, "proton": 4, "alpha": 5, "neutron": 6}[m.group(1).lower()]
        _need_cols(t, 3, path)
        series = [Series(PARTICLE_NAMES[kp], zero_small(t[:, 1]), _sigma(t[:, 2]), particle=kp)]
        meta["particle"] = kp
    else:
        _need_cols(t, 13, path)
        series = _particle_series(t, 1)
    title = KINDS["spc-angdet"].title + (f" n.º {ident.detector}" if ident.detector else "")
    return _curves("spc-angdet", t[:, 0], series, "E", "eV", "Densidad de probabilidad", "1/(eV·sr·partícula)",
                   title=title, log=None, meta=meta)


def read_enddet(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 3, path)
    title = KINDS["spc-enddet"].title + (f" n.º {ident.detector}" if ident.detector else "")
    return _curves("spc-enddet", t[:, 0], [Series("energía depositada", zero_small(t[:, 1]), _sigma(t[:, 2]))],
                   "E depositada", "eV", "Densidad de probabilidad", "1/(eV·partícula)", title=title, log=None,
                   meta={"detector": ident.detector})


# ---------------------------------------------------------------------------
# Fuente
# ---------------------------------------------------------------------------

def read_psource(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 4, path)
    if ident.variant == "lines":
        mid = 0.5 * (t[:, 2] + t[:, 3])
        series = [Series("definido", t[:, 1], None), Series("muestreado", mid, (t[:, 3] - t[:, 2]) / 6.0)]
        return Curves("psource", KINDS["psource"].title, t[:, 0], "E", "eV", "Probabilidad", "", series,
                      histogram=False, discrete=True, meta={"lines": True})
    rows = t[1:-1] if len(t) >= 4 else t
    if len(rows) % 2:
        raise FormatError(f"{path.name}: el histograma no tiene pares de puntos")
    lo, hi = rows[0::2], rows[1::2]
    edges = np.append(lo[:, 0], hi[-1, 0]) if len(lo) else np.zeros(0)
    centers = 0.5 * (lo[:, 0] + hi[:, 0])
    mid = 0.5 * (lo[:, 2] + lo[:, 3])
    series = [Series("definido", lo[:, 1], None), Series("muestreado", mid, (lo[:, 3] - lo[:, 2]) / 6.0)]
    # Un intervalo de anchura cero (una energía repetida en SPECTR) es una energía exacta: penhmain escribe su
    # probabilidad por 1e15 (FACT); los valores se dejan como están (es lo que se importa como espectro) y al verlo
    # se dibuja como una línea con su probabilidad (Curves.exact_energies).
    meta = {}
    if len(edges) > 1 and np.any(np.diff(edges) <= 0):
        meta["note"] = ("Energía exacta (dos energías iguales en SPECTR): una línea con su probabilidad, no una "
                        "densidad.")
    return Curves("psource", KINDS["psource"].title, centers, "E", "eV", "Densidad de probabilidad", "1/eV", series,
                  edges=edges, histogram=True, meta=meta)


def read_usource(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 13, path)
    n = len(t)
    step = t[1, 0] - t[0, 0] if n > 1 else 2.0 * t[0, 0]
    edges = np.arange(n + 1) * step
    return _curves("usource", t[:, 0], _particle_series(t, 1), "E", "eV", "Densidad de probabilidad",
                   "1/(eV·desintegración)", edges=edges)


# ---------------------------------------------------------------------------
# Proceso externo (EPROF)
# ---------------------------------------------------------------------------

def _p3d_units(ident: Identified, dim: int) -> tuple[str, str]:
    if ident.variant == "fluence":
        return "Fluencia", {3: "1/cm²", 1: "", 0: "1/cm²"}[dim]
    return "Densidad de probabilidad", {3: "1/cm³", 1: "1/cm", 0: "1/cm³"}[dim]


def read_p3d_curve(path: Path, ident: Identified) -> Curves:
    t = load_table(path)
    _need_cols(t, 3, path)
    if ident.kind == "p3d-depth":
        name, unit = _p3d_units(ident, 1)
        x_name = "z"
        if ident.variant == "fluence":
            name = "Fluencia integrada en X e Y"
    else:
        name, unit = _p3d_units(ident, 3)
        x_name = ident.kind[-1]
    return _curves(ident.kind, t[:, 0], [Series(name.lower(), zero_small(t[:, 1]), _sigma(t[:, 2]))], x_name, "cm",
                   name, unit, meta={"variant": ident.variant})


def read_p3d_map(path: Path, ident: Identified) -> Grid:
    lines = _head_lines(path)
    (xl, xu, yl, yu, zl, zu), nb = _box_header(lines, path)
    t = load_table(path)
    _need_cols(t, 8, path)
    v, e = _fill_box(t, nb, (5, 6, 7), [3, 4], path)
    name, unit = _p3d_units(ident, 3)
    edges = (np.linspace(xl, xu, nb[0] + 1), np.linspace(yl, yu, nb[1] + 1), np.linspace(zl, zu, nb[2] + 1))
    return Grid("p3d-map", KINDS["p3d-map"].title, "box", ("x", "y", "z"), edges, zero_small(v), _sigma(e), name,
                unit, meta={"variant": ident.variant})


def read_p3d_cyl(path: Path, ident: Identified) -> Grid:
    lines = _head_lines(path)
    ru = header_number(lines, "RU =")
    zl, zu = header_number(lines, "ZL ="), header_number(lines, "ZU =")
    nbr, nbz = header_int(lines, "NBR ="), header_int(lines, "NBZ =")
    if None in (ru, zl, zu, nbr, nbz):
        raise FormatError(f"{path.name}: falta la cabecera del cilindro")
    _check_header(path, (ru, zl, zu), (nbr, nbz))
    t = load_table(path)
    _need_cols(t, 4, path)
    n = nbr * nbz
    if len(t) < n:
        raise IncompleteFile(f"{path.name}: tiene {len(t)} de {n} celdas")
    t = t[:n]
    name, unit = _p3d_units(ident, 3)
    return Grid("p3d-map-cyl", KINDS["p3d-map-cyl"].title, "cylinder", ("r", "z"),
                (np.linspace(0.0, ru, nbr + 1), np.linspace(zl, zu, nbz + 1)),
                zero_small(t[:, 2].reshape(nbr, nbz)), _sigma(t[:, 3].reshape(nbr, nbz)), name, unit,
                meta={"variant": ident.variant})


def read_p3d_sphere(path: Path, ident: Identified) -> Grid:
    lines = _head_lines(path)
    ru, nbr = header_number(lines, "RU ="), header_int(lines, "NBR =")
    if ru is None or nbr is None:
        raise FormatError(f"{path.name}: falta la cabecera de la esfera")
    _check_header(path, (ru,), (nbr,))
    t = load_table(path)
    _need_cols(t, 5, path)
    if len(t) < nbr:
        raise IncompleteFile(f"{path.name}: tiene {len(t)} de {nbr} cascarones")
    t = t[:nbr]
    name, unit = _p3d_units(ident, 3)
    per_r = ("Por unidad de radio", "" if ident.variant == "fluence" else "1/cm")
    return Grid("p3d-sphere", KINDS["p3d-sphere"].title, "sphere", ("r",), (np.linspace(0.0, ru, nbr + 1),),
                zero_small(t[:, 1]), _sigma(t[:, 2]), name, unit,
                extra={per_r[0]: (zero_small(t[:, 3]), _sigma(t[:, 4]), per_r[1])}, meta={"variant": ident.variant})


# ---------------------------------------------------------------------------
# Trayectorias y espacio de fases
# ---------------------------------------------------------------------------

def read_tracks(path: Path, ident: Identified) -> Tracks:
    text = read_text(path)
    if text and not text.endswith("\n"):  # penhmain la está escribiendo: sin la línea a medias
        text = text[: text.rfind("\n") + 1]
    lengths = block_lengths(text)
    t = load_table_text(text)
    if len(t) == 0:
        return Tracks(ident.particle, np.zeros((0, 4)), np.zeros(1, dtype=np.int64))
    _need_cols(t, 4, path)
    n = sum(lengths)
    if n != len(t):  # no debería pasar: cada fila numérica cuenta en su bloque
        lengths = [len(t)]
    starts = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
    return Tracks(ident.particle, t[:, :4].astype(np.float64), starts)


def _count_lines(path: Path) -> int:
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += chunk.count(b"\n")
    return n


def read_psf(path: Path, ident: Identified, max_rows: int = PSF_MAX_ROWS) -> PhaseSpace:
    import warnings

    partial = _ends_mid_line(path)  # la última partícula está a medias (penhmain sigue escribiendo): fuera
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            t = np.loadtxt(path, comments="#", ndmin=2, max_rows=max_rows)
        except ValueError:
            text = read_text(path)
            t = load_table_text(text[: text.rfind("\n") + 1] if partial else text)[:max_rows]
            partial = False
    if t.size == 0:
        t = np.zeros((0, 14))
    if partial and len(t) and len(t) < max_rows:
        t = t[:-1]
    _need_cols(t, 14, path, allow_empty=True)
    total = len(t)
    if len(t) >= max_rows:
        total = _count_lines(path) - sum(1 for ln in comment_lines(read_head(path, 4096)))
    return PhaseSpace(ident.detector, t[:, 0].astype(np.int8), t[:, 1], t[:, 2:5], t[:, 5:8], t[:, 8],
                      t[:, 9:13].astype(np.int64), t[:, 13].astype(np.int64), total=max(total, len(t)))


# ---------------------------------------------------------------------------
# Otros
# ---------------------------------------------------------------------------

def read_energy_equivalence(path: Path, ident: Identified) -> Curves:
    text = read_text(path)
    lines = comment_lines(text)
    t = load_table_text(text)
    _need_cols(t, 2, path)
    names = []
    for ln in lines:
        toks = ln.replace("EP/eV", "").split()
        if toks and all(re.fullmatch(r"[A-Za-z0-9]+", x) for x in toks) and len(toks) == t.shape[1] - 1:
            names = toks
    if not names:
        names = [f"columna {i}" for i in range(2, t.shape[1] + 1)]
    ref = PARTICLE_NAMES.get(ident.particle or 4, "protones")
    series = [Series(n, t[:, i + 1], None) for i, n in enumerate(names)]
    return _curves("energy-equivalence", t[:, 0], series, "E del producto", "eV", f"E equivalente ({ref})", "eV",
                   title=lines[0] if lines else KINDS["energy-equivalence"].title, histogram=False)


def read_text_output(path: Path, ident: Identified) -> TextOutput:
    return TextOutput(ident.kind, KINDS[ident.kind].title, read_text(path))


READERS: dict[str, Callable[[Path, Identified], object]] = {
    "depth-dose": read_depth_dose, "ct-ddose": read_depth_dose,
    "x-dose": read_axis_dose, "y-dose": read_axis_dose, "z-dose": read_axis_dose,
    "ct-x-dose": read_axis_dose, "ct-y-dose": read_axis_dose, "ct-z-dose": read_axis_dose,
    "3d-dose-map": read_3d_dose, "2d-dose-map": read_2d_dose, "radial-dose": read_radial_dose,
    "gnu-dose": read_key_values, "gnu-ct-dose": read_key_values,
    "ct-3d-dose": read_ct_3d, "ct-dvh": read_ct_dvh,
    "energy-up": read_energy_updown, "energy-down": read_energy_updown, "angle": read_angle,
    "polar-angle": read_polar_angle,
    "spc-impdet": read_impdet, "fln-impdet": read_impdet, "age-impdet": read_age, "spc-angdet": read_angdet,
    "spc-enddet": read_enddet, "psf": read_psf,
    "psource": read_psource, "usource": read_usource,
    "p3d-depth": read_p3d_curve, "p3d-x": read_p3d_curve, "p3d-y": read_p3d_curve, "p3d-z": read_p3d_curve,
    "p3d-map": read_p3d_map, "p3d-map-cyl": read_p3d_cyl, "p3d-sphere": read_p3d_sphere,
    "trk": read_tracks, "energy-equivalence": read_energy_equivalence,
    "log": read_text_output, "run-log": read_text_output, "geometry-rep": read_text_output,
    "pennuc": read_text_output, "pmap-input": read_text_output,
}


RECENT_S = 120.0  # s: un fichero cambiado hace menos puede estar escribiéndose
# Textos, y ficheros que penhmain va alargando mientras simula (se lee lo que ya tiene, sin la línea a medias).
TEXT_KINDS = {"log", "run-log", "geometry-rep", "pennuc", "pmap-input", "gnu-dose", "gnu-ct-dose"}
GROWING_KINDS = {"psf", "trk"}


def _ends_mid_line(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def _check_written(p: Path) -> bool:
    """Si el fichero es reciente, que no esté vacío ni acabe a mitad de línea (penhmain siempre acaba sus líneas):
    si no, lo está escribiendo. Devuelve si es reciente."""
    try:
        st = p.stat()
    except OSError:
        return False
    if time.time() - st.st_mtime > RECENT_S:
        return False
    if st.st_size == 0:
        raise IncompleteFile(f"{p.name}: está vacío (penhmain lo está escribiendo)")
    with open(p, "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        if fh.read(1) != b"\n":
            raise IncompleteFile(f"{p.name}: la última línea está a medias (penhmain lo está escribiendo)")
    return True


def read_output(path: str | os.PathLike, ident: Identified | None = None):
    """Lee un fichero de resultados (lo reconoce si no se dice qué es).

    Un fichero reciente vacío, con la última línea a medias o (el resumen) sin acabar se está escribiendo:
    :class:`IncompleteFile`.
    """
    p = Path(path)
    ident = ident or identify(p)
    if ident is None:
        raise FormatError(f"{p.name} no es un fichero de resultados de penhmain")
    recent = _check_written(p) if ident.kind not in TEXT_KINDS | GROWING_KINDS else False
    if ident.kind == "res":
        from .summary import read_summary

        try:
            s = read_summary(p)
        except ValueError as exc:
            if recent:
                raise IncompleteFile(f"{p.name}: penhmain lo está escribiendo") from exc
            raise FormatError(f"{p.name}: {exc}") from exc
        if recent and not s.complete:
            raise IncompleteFile(f"{p.name}: penhmain lo está escribiendo")
        return s
    if ident.kind == "material":
        from .materialdata import read_material_dat

        return read_material_dat(p)
    reader = READERS.get(ident.kind)
    if reader is None:
        raise FormatError(f"No se sabe leer {p.name} ({ident.kind})")
    return reader(p, ident)


__all__ = ["IncompleteFile", "PSF_MAX_ROWS", "READERS", "read_output"]
