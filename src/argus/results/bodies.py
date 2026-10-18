"""Volumen, masa y dosis media de cada cuerpo de la geometría.

penhmain da la energía depositada en cada cuerpo (``penhmain-res.dat``); la
dosis media es esa energía entre la masa del cuerpo. La masa es el volumen por
la densidad de su material (``material.dat`` o la cabecera del .mat); en el TAC,
la suma de las masas de sus vóxeles.

El volumen de un cuerpo cualquiera de PENGEOM se mide con LOCATE: puntos
estratificados (uno al azar en cada celda de una rejilla fina) en una caja algo
mayor que la escena. Un cuerpo que llega al borde de esa caja sigue más allá (es
infinito o más grande que la escena): su volumen y su masa no se pueden medir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

DEFAULT_POINTS = 4_000_000
BATCH = 1_000_000


@dataclass
class Volumes:
    volume: dict[int, float]  # cm³
    error: dict[int, float]  # 1 sigma
    unbounded: set[int] = field(default_factory=set)  # llegan al borde de la caja: sin volumen
    points: int = 0
    box: tuple[np.ndarray, np.ndarray] | None = None


@dataclass
class BodyDose:
    kb: int
    material: int | None
    density: float | None  # g/cm³
    volume: float | None  # cm³
    volume_err: float | None
    mass: float | None  # g
    mass_err: float | None
    energy: float | None = None  # eV por primaria
    energy_err: float | None = None
    dose: float | None = None  # eV/g por primaria
    dose_err: float | None = None
    note: str = ""


def _grid_counts(lo: np.ndarray, hi: np.ndarray, n: int) -> tuple[int, int, int]:
    """Celdas por eje (casi cúbicas) para unos ``n`` puntos en la caja."""
    size = np.maximum(hi - lo, 1e-9)
    cell = (float(np.prod(size)) / n) ** (1.0 / 3.0)
    return tuple(int(max(1, math.ceil(s / cell))) for s in size)  # type: ignore[return-value]


def _count(loc, nb: int, lo: np.ndarray, hi: np.ndarray, n: int, rng, track_box: bool = False):
    """Puntos estratificados (uno al azar por celda) en [lo, hi]: cuántos caen en cada cuerpo, en qué cuerpos cae
    la capa de celdas del borde y (con ``track_box``) la caja de los puntos de cada cuerpo."""
    nx, ny, nz = _grid_counts(lo, hi, n)
    step = (hi - lo) / np.array([nx, ny, nz], dtype=np.float64)
    counts = np.zeros(nb + 2, dtype=np.int64)
    edge = np.zeros(nb + 2, dtype=bool)
    bmin = np.full((nb + 2, 3), np.inf)
    bmax = np.full((nb + 2, 3), -np.inf)
    per_slab = nx * ny
    slabs = max(1, BATCH // max(per_slab, 1))
    ix, iy = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    ix, iy = ix.ravel().astype(np.float64), iy.ravel().astype(np.float64)
    border_xy = (ix == 0) | (ix == nx - 1) | (iy == 0) | (iy == ny - 1)
    for z0 in range(0, nz, slabs):
        zs = np.arange(z0, min(z0 + slabs, nz), dtype=np.float64)
        m = len(zs)
        cx = np.tile(ix, m)
        cy = np.tile(iy, m)
        cz = np.repeat(zs, per_slab)
        u = rng.random((len(cx), 3))
        pts = np.stack([lo[0] + (cx + u[:, 0]) * step[0], lo[1] + (cy + u[:, 1]) * step[1],
                        lo[2] + (cz + u[:, 2]) * step[2]], axis=1)
        ib, _mat = loc.locate(pts)
        ib = np.clip(np.asarray(ib, dtype=np.int64), 0, nb + 1)
        counts += np.bincount(ib, minlength=nb + 2)
        border = np.tile(border_xy, m) | (cz == 0) | (cz == nz - 1)
        edge[np.unique(ib[border])] = True
        if track_box:
            np.minimum.at(bmin, ib, pts)
            np.maximum.at(bmax, ib, pts)
    return counts, edge, nx * ny * nz, step, (bmin, bmax)


REFINE_BELOW = 5000  # cuerpos con menos puntos: se vuelven a medir en su propia caja
REFINE_POINTS = 400_000


def sample_volumes(session, n: int = DEFAULT_POINTS, seed: int = 20260930, margin: float = 0.04) -> Volumes:
    """Volumen de cada cuerpo por muestreo estratificado con LOCATE en la caja de la escena (algo agrandada).

    Los cuerpos pequeños (pocos puntos) se vuelven a medir con más puntos en su propia caja: la que dan sus
    superficies (acotada por la de sus puntos si los hay).
    """
    from ..pengeom.extent import element_box

    loc = session.locator
    geo = session.geo
    lo, hi = (np.asarray(v, dtype=np.float64) for v in session.bounds)
    size = np.maximum(hi - lo, 1e-6)
    nx, ny, nz = _grid_counts(lo, hi, n)
    cells = size / np.array([nx, ny, nz], dtype=np.float64)
    # Margen: un 4 % y al menos dos celdas (en una escena muy plana, la celda puede ser mayor que el 4 %).
    pad = np.maximum(margin * size, 2.0 * cells)
    lo, hi = lo - pad, hi + pad
    nb = geo.nbodys
    rng = np.random.default_rng(seed)
    counts, edge, total, step, (bmin, bmax) = _count(loc, nb, lo, hi, n, rng, track_box=True)
    box_vol = float(np.prod(hi - lo))
    cell_vol = box_vol / total
    vol, err = {}, {}
    unbounded = set()
    for kb in range(1, nb + 1):
        c = int(counts[kb])
        vol[kb] = c * cell_vol
        p = c / total
        err[kb] = box_vol * math.sqrt(max(p * (1.0 - p), 1.0 / total) / total)
        if edge[kb]:
            unbounded.add(kb)
    # Cuerpos pequeños: otra vez, en su caja.
    for kb in range(1, nb + 1):
        if kb in unbounded or counts[kb] >= REFINE_BELOW or geo.body(kb).origin == "enclosure":
            continue
        try:
            blo, bhi = (np.asarray(v, dtype=np.float64) for v in element_box(geo, kb))
        except Exception:  # noqa: BLE001 - la cota analítica es una ayuda: sin ella, la caja de la escena
            blo, bhi = lo.copy(), hi.copy()
        blo = np.where(np.isfinite(blo), np.maximum(blo, lo), lo)
        bhi = np.where(np.isfinite(bhi), np.minimum(bhi, hi), hi)
        if counts[kb] > 0:  # los puntos que han caído en él, más una celda de margen
            blo = np.maximum(blo, bmin[kb] - step)
            bhi = np.minimum(bhi, bmax[kb] + step)
        if np.any(bhi <= blo):
            continue
        sub_vol = float(np.prod(bhi - blo))
        if sub_vol >= 0.5 * box_vol:
            continue
        c2, _e2, t2, _s2, _b2 = _count(loc, nb, blo, bhi, REFINE_POINTS, rng)
        p2 = int(c2[kb]) / t2
        vol[kb] = p2 * sub_vol
        err[kb] = sub_vol * math.sqrt(max(p2 * (1.0 - p2), 1.0 / t2) / t2)
    return Volumes(vol, err, unbounded, total, (lo, hi))


def ct_mass(ct) -> float:
    """Masa del TAC (g): la suma de densidad × volumen de sus vóxeles con material."""
    dv = float(np.prod(ct.voxel_size))
    m = np.asarray(ct.material)
    d = np.asarray(ct.density, dtype=np.float64)
    return float(d[m > 0].sum() * dv)


def body_doses(case, volumes: Volumes, densities: dict[int, float], summary=None) -> list[BodyDose]:
    """Masa y dosis media (si se da el resumen) de cada cuerpo con material."""
    geo = case.geo
    out: list[BodyDose] = []
    for kb in range(1, geo.nbodys + 1):
        b = geo.body(kb)
        if b.origin == "enclosure":
            continue
        mat = int(b.material)
        v = volumes.volume.get(kb)
        ve = volumes.error.get(kb)
        rho = None
        mass = mass_err = None
        note = ""
        if geo.ct is not None and kb == geo.ct_body:
            mass = ct_mass(geo.ct)  # exacta: la de sus vóxeles
            mass_err = 0.0
        elif kb in volumes.unbounded:
            note = "sigue más allá de la escena (infinito o muy grande): sin masa"
            v = ve = None
        elif v is not None and v <= 0:
            note = "demasiado pequeño para medir su volumen por muestreo"
            v = ve = None
        elif mat == 0:
            note = "vacío"
        else:
            rho = densities.get(mat)
            if rho is None:
                note = f"sin la densidad del material {mat}"
            elif v is not None:
                mass, mass_err = v * rho, (ve or 0.0) * rho
        bd = BodyDose(kb, mat, rho, v, ve, mass, mass_err, note=note)
        if summary is not None:
            m = summary.body_energy.get(kb)
            if m is not None:
                bd.energy, bd.energy_err = m.value, m.err
                if mass and mass > 0:
                    bd.dose = m.value / mass
                    rel = math.hypot(m.err / m.value if m.value else 0.0, (mass_err or 0.0) / mass)
                    bd.dose_err = abs(bd.dose) * rel
        out.append(bd)
    return out


def material_densities(material_data=None, case=None) -> dict[int, float]:
    """Densidad (g/cm³) de cada material: de ``material.dat`` y, si falta, de la cabecera del .mat del .in."""
    out: dict[int, float] = {}
    if material_data is not None:
        for m in material_data.materials:
            if m.density:
                out[m.index] = float(m.density)
    if case is not None:
        from ..penhan.materials import read_mat_header

        for i, name in enumerate(case.material_files, start=1):
            if i in out:
                continue
            h = read_mat_header(case.input_path.parent / name)
            if h is not None and h.density:
                out[i] = float(h.density)
    return out


__all__ = ["BodyDose", "Volumes", "body_doses", "ct_mass", "material_densities", "sample_volumes"]
