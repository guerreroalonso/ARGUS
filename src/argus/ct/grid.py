"""Recorte, tamaño de vóxel y colocación del TAC en la geometría.

La rejilla del .ct es uniforme (DX = XM/NX...). Se define por su esquina
inferior ``lo`` (mm, en el sistema de la matriz del TAC), el tamaño del vóxel
y el número de vóxeles. Cada vóxel nuevo toma la **media de los HU** de los
vóxeles del TAC que cubre, pesada por el volumen que comparten (promedio por
solape). Lo que queda fuera del campo del TAC (en x e y) se trata como aire
(−1000 HU); más allá del primer o del último corte (en z) no se sabe qué hay
—el paciente sigue—, así que un vóxel que asoma por esos extremos toma la
media de la parte que sí cubre. Si el vóxel nuevo coincide con uno del TAC,
su valor es exactamente el del TAC.

Promediar HU en vez de densidades da lo mismo dentro de cada tramo recto de
la curva de calibración (la curva de Schneider es casi recta en cada tejido).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from . import Cancelled
from .dicom import HuVolume

AIR_HU = -1000.0
BODY_HU = -400.0  # umbral para «Ajustar al cuerpo» (por encima: paciente, mesa, maniquí)


@dataclass
class GridSpec:
    lo: np.ndarray  # (3,) mm: esquina inferior (bordes) en el sistema de la matriz
    voxel: np.ndarray  # (3,) mm
    shape: tuple[int, int, int]  # NX, NY, NZ

    @property
    def hi(self) -> np.ndarray:
        return self.lo + self.voxel * np.array(self.shape)

    @property
    def size_mm(self) -> np.ndarray:
        return self.voxel * np.array(self.shape)

    @property
    def nvoxels(self) -> int:
        return int(np.prod(self.shape))

    def centers(self, axis: int) -> np.ndarray:
        return self.lo[axis] + (np.arange(self.shape[axis]) + 0.5) * self.voxel[axis]


def make_grid(lo, hi, voxel) -> GridSpec:
    """Rejilla con vóxeles de ``voxel`` mm que cubre la caja [lo, hi] (centrada en ella).

    Si la caja mide un número entero de vóxeles, la rejilla coincide exactamente con ella.
    """
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    v = np.asarray(voxel, dtype=float)
    if np.any(v <= 0) or np.any(hi <= lo):
        raise ValueError("Recorte o tamaño de vóxel no válido.")
    span = hi - lo
    ratio = span / v
    n = np.maximum(1, np.where(np.abs(ratio - np.rint(ratio)) < 1e-6, np.rint(ratio), np.ceil(ratio))).astype(int)
    center = 0.5 * (lo + hi)
    new_lo = np.where(np.abs(n * v - span) < 1e-9 * np.maximum(1.0, span), lo, center - 0.5 * n * v)
    return GridSpec(new_lo, v, (int(n[0]), int(n[1]), int(n[2])))


def auto_crop(vol: HuVolume, threshold: float = BODY_HU, margin_mm: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Caja (bordes de vóxel, mm) de lo que está por encima de ``threshold`` HU, con un margen.

    El margen se redondea a vóxeles enteros del TAC para que la rejilla pueda coincidir con ellos.
    """
    hu = vol.hu
    lo_all, hi_all = vol.bounds()
    mask_z = np.array([np.any(hu[k] > threshold) for k in range(hu.shape[0])])
    if not mask_z.any():
        return lo_all, hi_all
    mask_y = np.any(hu > threshold, axis=(0, 2))
    mask_x = np.any(hu > threshold, axis=(0, 1))
    out_lo, out_hi = [], []
    for a, (c, m) in enumerate(((vol.x, mask_x), (vol.y, mask_y), (vol.z, mask_z))):
        e = vol.edges(a)
        idx = np.flatnonzero(m)
        step = abs(c[1] - c[0]) if c.size > 1 else float(vol.single[a])
        extra = int(math.ceil(margin_mm / step - 1e-9)) if margin_mm > 0 else 0
        i0 = max(0, int(idx[0]) - extra)
        i1 = min(c.size - 1, int(idx[-1]) + extra)
        out_lo.append(e[i0])
        out_hi.append(e[i1 + 1])
    return np.array(out_lo), np.array(out_hi)


def _weights(edges_in: np.ndarray, lo: float, step: float, n: int) -> np.ndarray:
    """Matriz (n, len(edges_in)−1): fracción de cada vóxel nuevo que ocupa cada vóxel del TAC."""
    a = lo + np.arange(n) * step
    b = a + step
    ei0 = edges_in[:-1][None, :]
    ei1 = edges_in[1:][None, :]
    ov = np.clip(np.minimum(b[:, None], ei1) - np.maximum(a[:, None], ei0), 0.0, None)
    w = ov / step
    # Rejilla que coincide con la del TAC: pesos exactos (0 o 1), así los HU no cambian por el redondeo.
    w[w < 1e-9] = 0.0
    w[np.abs(w - 1.0) < 1e-9] = 1.0
    return w


def resample(vol: HuVolume, grid: GridSpec, progress: Callable[[int, int, str], None] | None = None,
             cancel: Callable[[], bool] | None = None) -> np.ndarray:
    """HU medios de cada vóxel de la rejilla: (NZ, NY, NX) float32.

    Fuera del campo (x, y): aire. Más allá del primer o del último corte (z):
    la media de la parte cubierta (el paciente no acaba donde acaba el TAC).
    """
    wx = _weights(vol.edges(0), grid.lo[0], grid.voxel[0], grid.shape[0])
    wy = _weights(vol.edges(1), grid.lo[1], grid.voxel[1], grid.shape[1])
    wz = _weights(vol.edges(2), grid.lo[2], grid.voxel[2], grid.shape[2])
    cx, cy = wx.sum(axis=1), wy.sum(axis=1)
    cxy = np.outer(cy, cx)  # fracción cubierta en el plano (NY, NX)
    nx, ny, nz = grid.shape
    out = np.empty((nz, ny, nx), dtype=np.float32)
    wx32 = wx.T.astype(np.float32)
    wy32 = wy.astype(np.float32)
    for k in range(nz):
        if cancel is not None and cancel():
            raise Cancelled()
        if progress is not None and (k % 4 == 0 or k == nz - 1):
            progress(k + 1, nz, "")
        idx = np.flatnonzero(wz[k] > 0)
        cz = float(wz[k].sum())
        if idx.size == 0:
            out[k] = AIR_HU
            continue
        acc = np.zeros((vol.hu.shape[1], vol.hu.shape[2]), dtype=np.float32)
        for i in idx:
            acc += np.float32(wz[k, i]) * vol.hu[i].astype(np.float32)
        plane = wy32 @ acc @ wx32  # (NY, NX): suma pesada de HU
        # En z se promedia solo lo cubierto; en x e y, lo que falta es aire.
        out[k] = plane / np.float32(cz) + (1.0 - cxy).astype(np.float32) * np.float32(AIR_HU)
    return out


def estimate(grid: GridSpec) -> dict:
    """Números que interesan antes de importar: vóxeles, tamaño del fichero y memoria de penhmain."""
    from ..pengeom.ct import NDD, estimated_file_size, penhmain_memory

    nx, ny, nz = grid.shape
    return {"voxels": grid.nvoxels, "file": estimated_file_size(grid.shape), "memory": penhmain_memory(grid.shape),
            "nz_ok": nz <= NDD, "size_cm": grid.size_mm / 10.0}


def suggest_voxel(vol: HuVolume, lo, hi, max_voxels: int = 6_000_000,
                  choices=(1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)) -> np.ndarray:
    """Tamaño de vóxel propuesto: el del TAC si cabe; si no, el menor cúbico de ``choices`` que cabe.

    Cabe = como mucho ``max_voxels`` vóxeles y como mucho 512 cortes (penhmain).
    """
    from ..pengeom.ct import NDD

    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    native = np.array(vol.spacing())
    g = make_grid(lo, hi, native)
    if g.nvoxels <= max_voxels and g.shape[2] <= NDD:
        return native
    for c in choices:
        v = np.array([max(c, native[0]), max(c, native[1]), max(c, native[2])])
        g = make_grid(lo, hi, v)
        if g.nvoxels <= max_voxels and g.shape[2] <= NDD:
            return v
    return np.full(3, choices[-1])


# ---------------------------------------------------------------------------
# Colocación
# ---------------------------------------------------------------------------

PLACEMENTS = {"centro": "Centro del TAC en el origen", "dicom": "Coordenadas del TAC (DICOM)",
              "punto": "Un punto del TAC en el origen (p. ej. el isocentro)"}


def placement(vol: HuVolume, grid: GridSpec, mode: str = "centro", point_mm=None
              ) -> tuple[np.ndarray, np.ndarray]:
    """Giro R y desplazamiento D (cm) del CTBOX: r_lab = R·r_CT + D.

    * ``dicom``: las coordenadas del paciente (LPS) en cm;
    * ``centro``: el centro de la caja en el origen;
    * ``punto``: el punto ``point_mm`` del paciente (LPS, mm) en el origen.
    """
    r = np.asarray(vol.rotation, dtype=float)
    corner = r @ grid.lo / 10.0  # esquina de la caja en el sistema del paciente (cm)
    if mode == "dicom":
        d = corner
    elif mode == "centro":
        d = -r @ (grid.size_mm / 20.0)
    elif mode == "punto":
        if point_mm is None:
            raise ValueError("Falta el punto que va al origen.")
        d = corner - np.asarray(point_mm, dtype=float) / 10.0
    else:
        raise ValueError(mode)
    return r, d


__all__ = ["AIR_HU", "BODY_HU", "GridSpec", "PLACEMENTS", "auto_crop", "estimate", "make_grid", "placement",
           "resample", "suggest_voxel"]
