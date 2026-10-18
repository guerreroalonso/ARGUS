"""Cómo se muestran los resultados: unidades, normalización e incertidumbres (sin Qt).

* Dosis (eV/g) en eV/g, keV/g, MeV/g o Gy y sus submúltiplos; la dosis en
  profundidad (eV·cm²/g) con las mismas unidades por cm².
* Energías del eje x en eV, keV, MeV o GeV; las densidades por eV se pasan a la
  misma unidad (1/(keV·partícula) si el eje está en keV).
* Tiempos (edad de las partículas) en s, ms, µs, ns o ps.
* Normalización de cada serie (:class:`~argus.results.units.Normalization`).
* Con una fuente radiactiva, «por partícula» es «por desintegración».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import numpy as np

from .data import EV_PER_G_TO_GY, ZERO_WIDTH_EV, Curves, Series
from .units import Normalization

DOSE_SCALES: list[tuple[str, float]] = [
    ("eV/g", 1.0), ("keV/g", 1e-3), ("MeV/g", 1e-6),
    ("Gy", EV_PER_G_TO_GY), ("mGy", EV_PER_G_TO_GY * 1e3), ("µGy", EV_PER_G_TO_GY * 1e6),
    ("nGy", EV_PER_G_TO_GY * 1e9), ("pGy", EV_PER_G_TO_GY * 1e12), ("fGy", EV_PER_G_TO_GY * 1e15),
]
DOSE_SCALE = dict(DOSE_SCALES)
ENERGY_SCALE = {"eV": 1.0, "keV": 1e-3, "MeV": 1e-6, "GeV": 1e-9}
TIME_SCALE = {"s": 1.0, "ms": 1e3, "µs": 1e6, "ns": 1e9, "ps": 1e12}


def auto_scale(value: float, scales: dict[str, float], candidates: list[str]) -> str:
    """La unidad de ``candidates`` (de la mayor a la menor) con la que ``value`` queda entre 1 y 1000."""
    v = abs(value)
    if v <= 0 or not np.isfinite(v):
        return candidates[0]
    for c in candidates:
        if 1.0 <= v * scales[c] < 1000.0:
            return c
    return candidates[-1] if v * scales[candidates[-1]] < 1.0 else candidates[0]


def auto_dose(value: float) -> str:
    return auto_scale(value, DOSE_SCALE, ["Gy", "mGy", "µGy", "nGy", "pGy", "fGy"])


def auto_energy(value: float) -> str:
    return auto_scale(value, ENERGY_SCALE, ["GeV", "MeV", "keV", "eV"])


def auto_time(value: float) -> str:
    return auto_scale(value, TIME_SCALE, ["s", "ms", "µs", "ns", "ps"])


@dataclass
class DisplayOptions:
    dose_unit: str = "auto"
    energy_unit: str = "auto"
    time_unit: str = "auto"
    norm: Normalization = field(default_factory=Normalization)
    sigma: float = 1.0  # bandas de ±k·σ (0: sin bandas)
    per_decay: bool = False


@dataclass
class ExactEnergy:
    """Una energía exacta dentro de un histograma (un intervalo de anchura cero de psource.dat): una línea, no una
    densidad. Se dibuja aparte y su probabilidad cuenta en las medidas (energía media, área, ventanas)."""

    index: int  # el intervalo de anchura cero
    x: float  # en las unidades del eje que se ve
    p: float  # probabilidad (por partícula o por desintegración)
    err: float | None = None  # 1 sigma


@dataclass
class ShownSeries:
    name: str
    label: str
    x: np.ndarray
    y: np.ndarray  # NaN en las energías exactas (``lines``)
    err: np.ndarray | None  # 1 sigma, en las unidades mostradas
    edges: np.ndarray | None
    particle: int | None
    source: Series
    lines: list[ExactEnergy] = field(default_factory=list)
    scale: float = 1.0  # factor de la normalización que lleva ``y`` (el área de una línea es p·scale)
    unit: str = ""  # la unidad de los valores, como en el rótulo del eje (para exportar)


@dataclass
class ShownCurves:
    curves: Curves
    x_label: str
    y_label: str
    x_unit: str
    y_unit: str
    series: list[ShownSeries]
    extra: list[ShownSeries]  # series con otras unidades (se dibujan en otro panel)
    extra_label: str = ""
    x_scale: float = 1.0
    log_x: bool = False
    note: str = ""  # lo que hay que saber de cómo se muestra (una normalización que no se ha podido aplicar…)


def _per(unit: str, per_decay: bool) -> str:
    return unit.replace("partícula", "desintegración") if per_decay else unit


def _x_conversion(c: Curves, opts: DisplayOptions) -> tuple[float, str]:
    xmax = float(np.nanmax(np.abs(c.x))) if len(c.x) else 1.0
    if c.x_unit == "eV":
        u = auto_energy(xmax) if opts.energy_unit == "auto" else opts.energy_unit
        return ENERGY_SCALE[u], u
    if c.x_unit == "eV/g":  # DVH: dosis en el eje x
        u = auto_dose(xmax) if opts.dose_unit == "auto" else opts.dose_unit
        return DOSE_SCALE[u], u
    if c.x_unit == "s":
        u = auto_time(xmax) if opts.time_unit == "auto" else opts.time_unit
        return TIME_SCALE[u], u
    return 1.0, c.x_unit


def _y_conversion(unit: str, ymax: float, opts: DisplayOptions, x_energy_unit: str | None,
                  x_other: tuple[str, str] | None = None) -> tuple[float, str]:
    """Factor y unidad mostrada para valores en ``unit`` (``x_other``: unidad original y mostrada de un eje x de
    tiempos o de dosis, para las densidades por segundo o por eV/g)."""
    if unit == "eV/g":
        u = auto_dose(ymax) if opts.dose_unit == "auto" else opts.dose_unit
        return DOSE_SCALE[u], u
    if unit == "eV·cm²/g":
        if opts.dose_unit == "auto":
            u = "MeV/g"
        else:
            u = opts.dose_unit
        f = DOSE_SCALE[u]
        return f, (u.replace("/g", "·cm²/g") if u.endswith("/g") else f"{u}·cm²")
    if unit == "eV":
        u = auto_energy(ymax) if opts.energy_unit == "auto" else opts.energy_unit
        return ENERGY_SCALE[u], u
    m = re.match(r"^(1|cm)/(?:\(eV(.*)\)|eV)$", unit)
    if m and x_energy_unit and x_energy_unit != "eV":
        # Densidad por eV → por la unidad del eje (por keV: ×1000); «1/eV» (psource.dat) → «1/keV».
        rest = m.group(2)
        shown = f"{m.group(1)}/({x_energy_unit}{rest})" if rest else f"{m.group(1)}/{x_energy_unit}"
        return 1.0 / ENERGY_SCALE[x_energy_unit], shown
    if unit == "1/s" and x_other and x_other[0] == "s" and x_other[1] != "s":
        return 1.0 / TIME_SCALE[x_other[1]], f"1/({x_other[1]}·partícula)"
    if unit == "cm³/(eV/g)" and x_other and x_other[0] == "eV/g" and x_other[1] != "eV/g":
        return 1.0 / DOSE_SCALE[x_other[1]], f"cm³/{x_other[1]}"
    return 1.0, unit


def strip_per_particle(u: str) -> str:
    """La unidad sin «por partícula»: 1/(keV·partícula) → 1/keV (para los valores de N partículas)."""
    for w in ("partícula", "desintegración"):
        u = u.replace(f"·{w})", ")").replace(f"/({w})", "").replace(f"/{w}", "")
    u = re.sub(r"/\(([^()·]+)\)$", r"/\1", u)
    return u


def resolve_units(c: Curves, opts: DisplayOptions) -> DisplayOptions:
    """Las unidades «automáticas» que tomaría esta curva, ya fijadas: para dibujar otras con las mismas
    (comparaciones, variantes de una campaña) y que todas vayan en la misma escala."""
    o = replace(opts)
    norm = opts.norm
    dvh = is_dvh(c)
    g = norm.factor(np.zeros(0), np.zeros(0)) if norm.mode in ("count", "activity") else 1.0
    xmax = float(np.nanmax(np.abs(c.x))) if len(c.x) else 0.0

    def ymax(unit: str) -> float:
        vals = [float(np.nanmax(np.abs(s.y))) for s in c.series
                if (s.unit if s.unit is not None else c.y_unit) == unit and len(s.y) and np.isfinite(s.y).any()]
        return max(vals) if vals else 0.0

    if o.energy_unit == "auto":
        if c.x_unit == "eV":
            o.energy_unit = auto_energy(xmax)
        elif ymax("eV") > 0:
            o.energy_unit = auto_energy(ymax("eV") * (g if c.y_unit == "eV" and not dvh else 1.0))
    if o.dose_unit == "auto":
        if c.x_unit == "eV/g":
            o.dose_unit = auto_dose(xmax * (g if dvh else 1.0))
        elif ymax("eV/g") > 0:
            o.dose_unit = auto_dose(ymax("eV/g") * (g if c.y_unit == "eV/g" else 1.0))
    if o.time_unit == "auto" and c.x_unit == "s":
        o.time_unit = auto_time(xmax)
    return o


def is_dvh(c: Curves) -> bool:
    return c.kind == "ct-dvh" or c.kind.startswith("dvh")


def show_curves(c: Curves, opts: DisplayOptions, visible: set[str] | None = None) -> ShownCurves:
    """Series de una curva preparadas para dibujar (con la unidad, la normalización y los nombres).

    En los histogramas dosis-volumen la normalización a N partículas (o a una actividad) cambia la dosis del eje x;
    el volumen no se normaliza.
    """
    dvh = is_dvh(c)
    norm = opts.norm
    gx = norm.factor(np.zeros(0), np.zeros(0)) if dvh and norm.mode in ("count", "activity") else 1.0
    if gx != 1.0 and c.x_unit == "eV/g":
        xmax = float(np.nanmax(np.abs(c.x))) if len(c.x) else 1.0
        u = auto_dose(xmax * gx) if opts.dose_unit == "auto" else opts.dose_unit
        xs, xu = DOSE_SCALE[u] * gx, u
    else:
        xs, xu = _x_conversion(c, opts)
    x = c.x * xs
    edges = None if c.edges is None else c.edges * xs
    x_energy = xu if c.x_unit == "eV" else None
    x_other = (c.x_unit, xu) if c.x_unit in ("s", "eV/g") else None
    main: list[ShownSeries] = []
    extra: list[ShownSeries] = []
    y_unit_shown = c.y_unit
    extra_label = ""
    absolute = norm.mode in ("count", "activity")
    g_const = norm.factor(np.zeros(0), np.zeros(0)) if absolute else 1.0
    # Los datos medidos son valores absolutos (Gy, cuentas…): «para N partículas» o «para una actividad» no los
    # cambia (las normalizaciones relativas, sí: para comparar formas).
    measured = c.kind == "measured"
    exact = c.exact_energies()
    prepared = []
    for s in c.series:
        if visible is not None and s.name not in visible:
            continue
        unit = s.unit if s.unit is not None else c.y_unit
        is_extra = s.unit is not None and s.unit != c.y_unit
        scaled = not is_extra and not dvh and not (measured and absolute)
        sy, serr, lines = np.asarray(s.y, dtype=np.float64), s.err, []
        if exact is not None and not is_extra:
            # Una energía exacta no es una densidad: su probabilidad aparte (no un pico de 1e14 que aplasta el resto).
            for i in np.nonzero(exact)[0]:
                e = None if s.err is None else float(s.err[i]) * ZERO_WIDTH_EV
                lines.append(ExactEnergy(int(i), float(c.edges[i]) * xs, float(sy[i]) * ZERO_WIDTH_EV, e))
            sy = np.where(exact, np.nan, sy)
            serr = None if s.err is None else np.where(exact, np.nan, s.err)
        ymax = float(np.nanmax(np.abs(sy))) if len(sy) and np.isfinite(sy).any() else 0.0
        # Con N partículas (o una actividad), la unidad se elige para los valores ya multiplicados.
        f, u = _y_conversion(unit, ymax * (g_const if scaled else 1.0), opts, x_energy, x_other)
        if dvh and gx != 1.0 and is_extra:
            f /= gx  # densidad por unidad de dosis: la dosis del eje x va multiplicada por gx
        prepared.append((s, is_extra, scaled, sy * f, None if serr is None else serr * f, u, lines))
    # «% del valor en un punto» que no se puede aplicar (el punto está fuera de alguna curva o vale 0 allí): no se
    # normaliza ninguna (el eje no puede decir «%» con unas sí y otras no) y se dice por qué.
    eff = norm
    note = ""
    if norm.mode == "point":
        why = next((w for w in (norm.point_problem(x, y, edges) for _s, _ex, sc, y, _e, _u, _l in prepared if sc)
                    if w), None)
        if why:
            eff = Normalization()
            note = f"No se normaliza al valor en un punto: {why}. Se muestran los valores sin normalizar."
    for s, is_extra, scaled, y, err, u, lines in prepared:
        g = 1.0
        if scaled and eff.mode != "raw":
            g = eff.factor(x, y, edges, extra_area=sum(ln.p for ln in lines) if eff.mode == "area" else 0.0)
            y, err = y * g, (None if err is None else err * g)
            if eff.relative:
                u = "%" if eff.mode in ("max", "point") else ""
            else:
                u = strip_per_particle(u)  # ya no es por partícula: es para N partículas
        shown = ShownSeries(s.name, _series_label(s, c), x, y, err, edges, s.particle, s, lines, g, u)
        if is_extra:
            extra.append(shown)
            extra_label = f"{s.quantity or s.name} ({u})" if u else (s.quantity or s.name)
        else:
            main.append(shown)
            y_unit_shown = u
    per = eff.label(opts.per_decay)
    y_unit_shown = _per(y_unit_shown, opts.per_decay)
    if dvh:
        y_label = f"{c.y_name} ({y_unit_shown})" if y_unit_shown else c.y_name
        x_label = f"{c.x_name} ({xu} {eff.label(opts.per_decay)})" if xu and not eff.relative else \
            (f"{c.x_name} ({xu})" if xu else c.x_name)
        for s in main:
            s.unit = y_unit_shown
        return ShownCurves(c, x_label, y_label, xu, y_unit_shown, main, extra, extra_label, xs, c.log_x, note)
    if eff.relative:
        y_units = per
    else:
        extra_per = "" if "partícula" in y_unit_shown or "desintegración" in y_unit_shown or not y_unit_shown else (
            f" {per}")
        y_units = f"{y_unit_shown}{extra_per}" if y_unit_shown else ""
    y_label = f"{c.y_name} ({y_units})" if y_units else c.y_name
    for s in main:
        s.unit = y_units
    x_label = f"{c.x_name} ({xu})" if xu else c.x_name
    return ShownCurves(c, x_label, y_label, xu, y_unit_shown, main, extra, extra_label, xs, c.log_x, note)


def line_text(line: ExactEnergy, scale: float, opts: DisplayOptions, x_unit: str = "") -> str:
    """El rótulo de una energía exacta: su probabilidad (en %) o, para N partículas o una actividad, cuántas."""
    who = "desintegraciones" if opts.per_decay else "partículas"
    where = f"{line.x:.6g} {x_unit}".strip()
    if opts.norm.mode in ("count", "activity"):
        g = opts.norm.factor(np.zeros(0), np.zeros(0))
        txt = f"{line.p * g:.4g}"
        if line.err:
            txt += f" ± {line.err * g:.2g}"
        return f"{where}: {txt} {who}"
    f = scale if opts.norm.mode == "area" else 1.0
    txt = f"{100 * line.p * f:.4g}"
    if line.err:
        txt += f" ± {100 * line.err * f:.2g}"
    return f"{where}: {txt} % de las {who}"


def _series_label(s: Series, c: Curves) -> str:
    if s.name in ("dosis", "edad", "energía depositada") or len(c.series) == 1:
        return c.title
    return s.name


def grid_scale(unit: str, vmax: float, opts: DisplayOptions) -> tuple[float, str]:
    """Factor y unidad de los valores de un mapa (``vmax``: su máximo, en las unidades de penhmain).

    Con la normalización a N partículas o a una actividad, los valores son los de todas ellas; con «máximo», en %
    del máximo del mapa. Los otros modos (un punto, área 1) son de las curvas: el mapa queda por partícula.
    """
    norm = opts.norm
    if norm.mode == "max":
        return (100.0 / vmax if vmax > 0 else 1.0), "%"
    absolute = norm.mode in ("count", "activity")
    g = norm.factor(np.zeros(0), np.zeros(0)) if absolute else 1.0
    f, u = _y_conversion(unit, vmax * g, opts, None)
    return f * g, (strip_per_particle(u) if absolute else _per(u, opts.per_decay))


def grid_label(quantity: str, unit: str, opts: DisplayOptions) -> str:
    """Rótulo de la escala de un mapa: «Dosis (µGy por partícula)», «Dosis (% del máximo)»…"""
    norm = opts.norm
    if norm.mode == "max":
        return f"{quantity} (% del máximo)"
    per = norm.label(opts.per_decay) if norm.mode in ("raw", "count", "activity") else \
        ("por desintegración" if opts.per_decay else "por partícula")
    if not unit:
        return f"{quantity} ({per})"
    if "partícula" in unit or "desintegración" in unit:
        return f"{quantity} ({unit})"
    return f"{quantity} ({unit} {per})"


__all__ = ["DOSE_SCALE", "DOSE_SCALES", "DisplayOptions", "ENERGY_SCALE", "ExactEnergy", "ShownCurves", "ShownSeries",
           "TIME_SCALE", "auto_dose", "auto_energy", "auto_time", "grid_label", "grid_scale", "is_dvh", "line_text",
           "resolve_units", "show_curves", "strip_per_particle"]
