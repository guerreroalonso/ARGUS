"""Cuádricas de PENGEOM: forma reducida, rotación/traslación (ROTSHF).

Las funciones reproducen las de pengeom.f operación a operación y en el mismo
orden, de modo que los coeficientes que calcula ARGUS coinciden bit a bit con
los de PENGEOM (en la misma máquina y sin contracciones FMA). Esto es lo que
permite comparar tablas con el oráculo y detectar superficies duplicadas
exactamente igual que GEOMIN.

Una cuádrica se representa como una tupla de 10 coeficientes en el orden de
PENGEOM::

    AXX AXY AXZ AYY AYZ AZZ AX AY AZ A0
    F(r) = AXX x² + AXY xy + AXZ xz + AYY y² + AYZ yz + AZZ z²
           + AX x + AY y + AZ z + A0
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from .model import COEF_NAMES

Quadric = tuple[float, float, float, float, float, float, float, float, float, float]

COEF_INDEX = {name: i for i, name in enumerate(COEF_NAMES)}
PI = 3.1415926535897932  # mismo literal que en pengeom.f
ZERO_QUADRIC: Quadric = (0.0,) * 10  # type: ignore[assignment]


def rotation_matrix(omega: float, theta: float, phi: float) -> list[list[float]]:
    """Matriz R(ω, θ, φ) = Rz(φ)·Ry(θ)·Rz(ω) tal como la construye ROTSHF."""
    stheta = math.sin(theta)
    ctheta = math.cos(theta)
    sphi = math.sin(phi)
    cphi = math.cos(phi)
    somega = math.sin(omega)
    comega = math.cos(omega)
    return [
        [cphi * ctheta * comega - sphi * somega,
         -cphi * ctheta * somega - sphi * comega,
         cphi * stheta],
        [sphi * ctheta * comega + cphi * somega,
         -sphi * ctheta * somega + cphi * comega,
         sphi * stheta],
        [-stheta * comega,
         stheta * somega,
         ctheta],
    ]


def rotshf(omega: float, theta: float, phi: float, dx: float, dy: float, dz: float,
           q: Sequence[float]) -> Quadric:
    """Gira (ángulos de Euler) y traslada una cuádrica, como ROTSHF.

    Los coeficientes con valor absoluto menor que 1e-16 se anulan, igual que
    en PENGEOM.
    """
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = (float(v) for v in q)
    b2 = [[axx, 0.5 * axy, 0.5 * axz],
          [0.0, ayy, 0.5 * ayz],
          [0.0, 0.0, azz]]
    b2[1][0] = b2[0][1]
    b2[2][0] = b2[0][2]
    b2[2][1] = b2[1][2]
    b1 = [ax, ay, az]
    b0 = a0
    d1 = [dx, dy, dz]
    r = rotation_matrix(omega, theta, phi)

    a1 = [0.0, 0.0, 0.0]
    a2 = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        acc1 = 0.0
        ri = r[i]
        for j in range(3):
            acc1 = acc1 + ri[j] * b1[j]
            rj = r[j]
            acc = 0.0
            for m in range(3):
                rjm = rj[m]
                for k in range(3):
                    acc = acc + ri[k] * b2[k][m] * rjm
            a2[i][j] = acc
        a1[i] = acc1

    out_b1 = [0.0, 0.0, 0.0]
    for i in range(3):
        a2d = 0.0
        for j in range(3):
            a2d = a2d + a2[i][j] * d1[j]
        out_b1[i] = a1[i] - 2.0 * a2d
        b0 = b0 + d1[i] * (a2d - a1[i])

    res = [a2[0][0], a2[0][1] + a2[1][0], a2[0][2] + a2[2][0],
           a2[1][1], a2[1][2] + a2[2][1], a2[2][2],
           out_b1[0], out_b1[1], out_b1[2], b0]
    return tuple(0.0 if abs(v) < 1.0e-16 else v for v in res)  # type: ignore[return-value]


def reduced_to_implicit(indices: Sequence[int], xscale: float = 1.0, yscale: float = 1.0,
                        zscale: float = 1.0) -> Quadric:
    """Cuádrica expandida (antes de girar/trasladar) a partir de la forma reducida.

    ``I1 (x/xs)² + I2 (y/ys)² + I3 (z/zs)² + I4 (z/zs) + I5 = 0``.
    """
    i1, i2, i3, i4, i5 = (int(v) for v in indices)
    return (i1 / (xscale * xscale), 0.0, 0.0, i2 / (yscale * yscale), 0.0,
            i3 / (zscale * zscale), 0.0, 0.0, i4 / zscale, float(i5))


def is_plane(q: Sequence[float]) -> bool:
    """KPLANE de PENGEOM: todos los coeficientes cuadráticos < 1e-30."""
    quad = q[:6]
    return max(abs(min(quad)), abs(max(quad))) < 1.0e-30


def degrees_to_radians(value: float) -> float:
    """Conversión de GEOMIN: ``VALUE*PI/180.0D0`` (mismo orden de operaciones)."""
    return value * PI / 180.0


def quadric_matrix(q: Sequence[float]) -> np.ndarray:
    """Matriz simétrica 4x4 Q tal que F(r) = [r 1]·Q·[r 1]ᵀ (para el render)."""
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = q
    return np.array([
        [axx, 0.5 * axy, 0.5 * axz, 0.5 * ax],
        [0.5 * axy, ayy, 0.5 * ayz, 0.5 * ay],
        [0.5 * axz, 0.5 * ayz, azz, 0.5 * az],
        [0.5 * ax, 0.5 * ay, 0.5 * az, a0],
    ], dtype=np.float64)


def evaluate(q: Sequence[float], x, y, z):
    """Valor de F en uno o varios puntos (numpy o escalares)."""
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = q
    return (axx * x * x + axy * x * y + axz * x * z + ayy * y * y + ayz * y * z + azz * z * z
            + ax * x + ay * y + az * z + a0)


def gradient(q: Sequence[float], x, y, z):
    """Gradiente de F (sin normalizar) en uno o varios puntos."""
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, _ = q
    gx = 2.0 * axx * x + axy * y + axz * z + ax
    gy = axy * x + 2.0 * ayy * y + ayz * z + ay
    gz = axz * x + ayz * y + 2.0 * azz * z + az
    return gx, gy, gz


# Nombres de las cuádricas en forma reducida (tabla 6.1 del manual).
REDUCED_NAMES: dict[tuple[int, int, int, int, int], str] = {
    (0, 0, 0, 1, 0): "plano",
    (0, 0, 0, 1, -1): "plano",  # plano desplazado
    (1, 1, 1, 0, -1): "esfera",
    (1, 1, 1, 0, 1): "esfera imaginaria",
    (1, 1, 0, 0, -1): "cilindro",
    (1, 0, 0, 0, -1): "par de planos",
    (0, 1, 0, 0, -1): "par de planos",
    (0, 0, 1, 0, -1): "par de planos",
    (1, 1, -1, 0, -1): "hiperboloide de una hoja",
    (1, 1, -1, 0, 1): "hiperboloide de dos hojas",
    (1, 1, -1, 0, 0): "cono",
    (1, 1, 0, -1, 0): "paraboloide",
    (1, -1, 0, 0, -1): "cilindro hiperbólico",
    (1, 0, 0, -1, 0): "cilindro parabólico",
    (1, -1, 0, -1, 0): "paraboloide hiperbólico",
}


def reduced_name(indices: Sequence[int]) -> str:
    """Nombre descriptivo de una cuádrica en forma reducida."""
    key = tuple(int(v) for v in indices)
    if key in REDUCED_NAMES:
        return REDUCED_NAMES[key]
    if all(v == 0 for v in key[:3]) and key[3] != 0:
        return "plano"
    return "cuádrica"
