"""Ajustes sencillos de una medida frente a un parámetro (campañas): recta, parábola y potencia.

Mínimos cuadrados ponderados con las incertidumbres (1σ) si las hay; la
incertidumbre de los coeficientes sale de la matriz de covarianza (escalada con
χ²/n si no hay incertidumbres).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MODELS = {"lineal": "Recta: a + b·x", "parabola": "Parábola: a + b·x + c·x²", "potencia": "Potencia: a·x^b"}


@dataclass
class FitResult:
    model: str
    params: np.ndarray
    errors: np.ndarray
    chi2: float
    ndf: int

    @property
    def reduced(self) -> float:
        return self.chi2 / self.ndf if self.ndf > 0 else math.nan

    def predict(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        p = self.params
        if self.model == "lineal":
            return p[0] + p[1] * x
        if self.model == "parabola":
            return p[0] + p[1] * x + p[2] * x * x
        return p[0] * np.power(x, p[1])

    def text(self) -> str:
        names = "abc"
        coef = ", ".join(f"{names[i]} = {v:.5g} ± {e:.2g}" for i, (v, e) in enumerate(zip(self.params, self.errors)))
        red = f"; χ²/n = {self.reduced:.3g}" if self.ndf > 0 and math.isfinite(self.reduced) else ""
        return f"{MODELS[self.model]}: {coef}{red}"


def _wls(a: np.ndarray, y: np.ndarray, w: np.ndarray | None) -> tuple[np.ndarray, np.ndarray, float, int]:
    """Mínimos cuadrados (ponderados con 1/σ² si ``w``): coeficientes, covarianza, χ², grados de libertad.

    La covarianza sale de la descomposición en valores singulares de la matriz ponderada (estable aunque las
    columnas tengan escalas muy distintas).
    """
    sw = np.sqrt(w) if w is not None else np.ones_like(y)
    aw = a * sw[:, None]
    yw = y * sw
    u, sv, vt = np.linalg.svd(aw, full_matrices=False)
    tol = sv.max() * max(aw.shape) * np.finfo(float).eps if sv.size else 0.0
    inv = np.where(sv > tol, 1.0 / np.where(sv > tol, sv, 1.0), 0.0)
    coef = vt.T @ (inv * (u.T @ yw))
    cov = (vt.T * inv ** 2) @ vt
    res = y - a @ coef
    chi2 = float(np.sum((sw * res) ** 2))
    return coef, cov, chi2, len(y) - a.shape[1]


def _poly(x: np.ndarray, y: np.ndarray, e: np.ndarray | None, degree: int) -> tuple[np.ndarray, np.ndarray, float,
                                                                                         int]:
    """Polinomio de grado ``degree`` en x (con x centrada y escalada para el cálculo): coeficientes en potencias de
    x, sus incertidumbres, χ² y grados de libertad."""
    x0 = float(np.mean(x))
    sx = float(np.max(np.abs(x - x0))) or 1.0
    t = (x - x0) / sx
    a = np.stack([t ** k for k in range(degree + 1)], axis=1)
    w = 1.0 / e ** 2 if e is not None else None
    c, cov, chi2, ndf = _wls(a, y, w)
    if e is None:
        cov = cov * (chi2 / ndf) if ndf > 0 else np.full_like(cov, np.nan)
    # De t = (x − x0)/sx a potencias de x: y = Σ c_k ((x − x0)/sx)^k = Σ b_j x^j (transformación lineal).
    n = degree + 1
    m = np.zeros((n, n))
    from math import comb

    for k in range(n):
        for j in range(k + 1):
            m[j, k] = comb(k, j) * (-x0) ** (k - j) / sx ** k
    b = m @ c
    cov_b = m @ cov @ m.T
    return b, np.sqrt(np.clip(np.diag(cov_b), 0, None)), chi2, ndf


def fit(x, y, err=None, model: str = "lineal") -> FitResult:
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    e = None if err is None else np.asarray(err, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    if e is not None:
        ok &= np.isfinite(e)
    x, y = x[ok], y[ok]
    e = e[ok] if e is not None else None
    have_err = e is not None and len(e) > 0 and np.all(e > 0)
    need = {"lineal": 2, "parabola": 3, "potencia": 2}[model]
    if len(x) < need:
        raise ValueError(f"Hacen falta al menos {need} puntos")
    if np.ptp(x) == 0:
        raise ValueError("Todos los puntos tienen el mismo valor del parámetro")
    if model == "potencia":
        if np.any(x <= 0) or np.any(y <= 0):
            raise ValueError("La potencia necesita x e y positivos")
        # log y = log a + b log x; σ(log y) = σ/y
        b, errs, _chi2, ndf = _poly(np.log(x), np.log(y), (e / y) if have_err else None, 1)
        amp = math.exp(b[0])
        r = FitResult(model, np.array([amp, b[1]]), np.array([amp * errs[0], errs[1]]), math.nan, ndf)
        pred = r.predict(x)
        r.chi2 = float(np.sum(((y - pred) / e) ** 2)) if have_err else float(np.sum((y - pred) ** 2))
        return r
    b, errs, chi2, ndf = _poly(x, y, e if have_err else None, 1 if model == "lineal" else 2)
    return FitResult(model, b, errs, chi2, ndf)


__all__ = ["FitResult", "MODELS", "fit"]
