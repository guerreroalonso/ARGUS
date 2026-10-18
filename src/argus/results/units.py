"""Unidades y normalizaciones de los resultados.

penhmain da todo por partícula primaria (por desintegración con una fuente
radiactiva): dosis en eV/g, energías en eV, longitudes en cm. Aquí se cambian
de unidad (Gy y sus submúltiplos, keV, MeV…) y se normalizan:

* ``raw``: por partícula (o por desintegración);
* ``count``: multiplicado por un número de partículas (p. ej. 1e9 protones);
* ``activity``: una fuente radiactiva de A Bq durante t s (A·t desintegraciones);
* ``max``: porcentaje del máximo de la curva;
* ``point``: porcentaje del valor en una posición;
* ``area``: área 1 (para comparar formas de espectros).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .data import EV_PER_G_TO_GY

DOSE_UNITS: list[tuple[str, float]] = [
    ("eV/g", 1.0),
    ("Gy", EV_PER_G_TO_GY),
    ("mGy", EV_PER_G_TO_GY * 1e3),
    ("µGy", EV_PER_G_TO_GY * 1e6),
    ("nGy", EV_PER_G_TO_GY * 1e9),
    ("pGy", EV_PER_G_TO_GY * 1e12),
    ("fGy", EV_PER_G_TO_GY * 1e15),
]
DOSE_FACTORS = dict(DOSE_UNITS)
ENERGY_UNITS: list[tuple[str, float]] = [("eV", 1.0), ("keV", 1e-3), ("MeV", 1e-6), ("GeV", 1e-9)]
ENERGY_FACTORS = dict(ENERGY_UNITS)


def auto_dose_unit(max_ev_per_g: float) -> str:
    """El múltiplo del gray con el que el máximo queda entre 1 y 1000."""
    gy = abs(max_ev_per_g) * EV_PER_G_TO_GY
    if gy <= 0:
        return "Gy"
    for name, f in DOSE_UNITS[1:]:
        if gy * (f / EV_PER_G_TO_GY) >= 1.0:
            return name
    return DOSE_UNITS[-1][0]


def auto_energy_unit(max_ev: float) -> str:
    v = abs(max_ev)
    for name, f in reversed(ENERGY_UNITS):
        if v * f >= 1.0:
            return name
    return "eV"


def dose_unit_label(unit: str, per: str) -> str:
    return unit if not per else f"{unit} {per}"


@dataclass
class Normalization:
    mode: str = "raw"  # 'raw', 'count', 'activity', 'max', 'point', 'area'
    count: float = 1.0e9
    activity: float = 1.0e6  # Bq
    duration: float = 1.0  # s
    point: float | None = None  # posición (en las unidades del eje x) para 'point'

    @property
    def relative(self) -> bool:
        return self.mode in ("max", "point", "area")

    def factor(self, x: np.ndarray, y: np.ndarray, edges: np.ndarray | None = None, extra_area: float = 0.0
               ) -> float:
        """Factor por el que se multiplican los valores (y sus incertidumbres). Los valores NaN no cuentan;
        ``extra_area``: lo que hay que sumar al área (la probabilidad de las energías exactas de un espectro, que no
        tienen anchura). Con «% del valor en un punto» que no se puede aplicar (:meth:`point_problem`), 1."""
        y = np.asarray(y, dtype=np.float64)
        if self.mode == "count":
            return float(self.count)
        if self.mode == "activity":
            return float(self.activity * self.duration)
        if self.mode == "max":
            m = float(np.nanmax(np.abs(y))) if y.size and np.isfinite(y).any() else 0.0
            return 100.0 / m if m > 0 else 1.0
        if self.mode == "point":
            ref = self.point_value(x, y, edges)
            return 100.0 / ref if ref is not None and ref != 0 and math.isfinite(ref) else 1.0
        if self.mode == "area":
            w = np.diff(edges) if edges is not None and len(edges) == len(y) + 1 else None
            if w is not None:
                a = float(np.nansum(y * w))
            else:
                ok = np.isfinite(y)
                a = float(np.trapezoid(y[ok], np.asarray(x, dtype=np.float64)[ok])) if ok.sum() > 1 else 0.0
            a += extra_area
            return 1.0 / a if a > 0 else 1.0
        return 1.0

    def point_value(self, x: np.ndarray, y: np.ndarray, edges: np.ndarray | None = None) -> float | None:
        """El valor de la curva en la posición de «% del valor en un punto» (None si está fuera de ella)."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        ok = np.isfinite(x) & np.isfinite(y)
        if self.point is None or not ok.any():
            return None
        lo, hi = float(np.min(x[ok])), float(np.max(x[ok]))
        if edges is not None and len(edges) == len(y) + 1 and np.isfinite(edges).all():
            lo, hi = min(lo, float(np.min(edges))), max(hi, float(np.max(edges)))  # el intervalo de cada extremo
        if not lo <= self.point <= hi:
            return None
        order = np.argsort(x[ok], kind="stable")
        return float(np.interp(self.point, x[ok][order], y[ok][order]))

    def point_problem(self, x: np.ndarray, y: np.ndarray, edges: np.ndarray | None = None) -> str | None:
        """Por qué no se puede dar la curva en % del valor en el punto (fuera de la curva, o vale 0 allí), o None."""
        if self.mode != "point":
            return None
        x = np.asarray(x, dtype=np.float64)
        ok = np.isfinite(x)
        if self.point is None or not ok.any():
            return "no hay ningún punto elegido"
        ref = self.point_value(x, y, edges)
        if ref is None:
            lo, hi = float(np.min(x[ok])), float(np.max(x[ok]))
            if edges is not None and len(edges) == len(x) + 1 and np.isfinite(edges).all():
                lo, hi = min(lo, float(np.min(edges))), max(hi, float(np.max(edges)))
            return f"x = {self.point:.4g} está fuera de la curva (de {lo:.4g} a {hi:.4g})"
        if ref == 0 or not math.isfinite(ref):
            return f"la curva vale 0 en x = {self.point:.4g}"
        return None

    def label(self, per_decay: bool = False) -> str:
        """Lo que se añade a la unidad: «por partícula», «(1e9 protones)», «%»…"""
        if self.mode == "raw":
            return "por desintegración" if per_decay else "por partícula"
        if self.mode == "count":
            return f"para {self.count:.3g} " + ("desintegraciones" if per_decay else "partículas")
        if self.mode == "activity":
            return f"para {self.activity:.3g} Bq durante {self.duration:.3g} s"
        if self.mode == "max":
            return "% del máximo"
        if self.mode == "point":
            return f"% del valor en {self.point:.4g}" if self.point is not None else "% de un punto"
        return "área 1"


def normalize(x: np.ndarray, y: np.ndarray, err: np.ndarray | None, norm: Normalization,
              edges: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray | None, float]:
    f = norm.factor(x, y, edges)
    return np.asarray(y) * f, (None if err is None else np.asarray(err) * f), f


__all__ = ["DOSE_FACTORS", "DOSE_UNITS", "ENERGY_FACTORS", "ENERGY_UNITS", "Normalization", "auto_dose_unit",
           "auto_energy_unit", "dose_unit_label", "normalize"]
