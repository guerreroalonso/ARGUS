"""Histogramas dosis-volumen (DVH) de estructuras: cuerpos de la geometría, tejidos del TAC o el TAC entero.

* Rejilla de caja (GRIDX/Y/Z): cada vóxel de dosis se reparte entre los cuerpos
  que corta, con puntos repartidos por el vóxel y LOCATE (volumen parcial).
* TAC: cada vóxel es de un material; los tejidos partidos por densidad (según
  la ficha del TAC) se juntan.

Las curvas son acumuladas: el % del volumen de la estructura que recibe una
dosis igual o mayor que la del eje x (eV/g por primaria, como penhmain).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import Curves, Grid, Series

NBINS = 400


@dataclass
class Structure:
    name: str
    doses: np.ndarray  # eV/g de cada trozo (vóxel o parte de vóxel)
    volumes: np.ndarray  # cm³ de cada trozo
    kb: int | None = None  # cuerpo de la geometría (si lo es)

    @property
    def volume(self) -> float:
        return float(self.volumes.sum())


def mark_partial(structures: list[Structure], volumes, tolerance: float = 0.02) -> list[str]:
    """Los cuerpos que la rejilla cubre solo en parte (su volumen en la rejilla es menor que el suyo, ``volumes``:
    :class:`bodies.Volumes`): se les pone en el nombre qué parte se ve. Devuelve sus nombres."""
    out = []
    for st in structures:
        if st.kb is None or volumes is None:
            continue
        total = volumes.volume.get(st.kb)
        if not total or st.kb in volumes.unbounded:
            continue
        err = volumes.error.get(st.kb, 0.0) or 0.0
        if st.volume < total * (1.0 - tolerance) - 3.0 * err:
            st.name = f"{st.name} ({100.0 * st.volume / total:.0f} % dentro de la rejilla)"
            out.append(st.name)
    return out


def dose_at_volume(doses: np.ndarray, volumes: np.ndarray, pct: float) -> float:
    """D_pct: la dosis mínima que recibe el ``pct`` % del volumen que más recibe (D98, D50, D2…)."""
    if not len(doses) or volumes.sum() <= 0:
        return float("nan")
    order = np.argsort(doses)[::-1]
    d = doses[order]
    cum = np.cumsum(volumes[order]) / volumes.sum() * 100.0
    k = int(np.searchsorted(cum, pct - 1e-12))
    return float(d[min(k, len(d) - 1)])


def volume_at_dose(doses: np.ndarray, volumes: np.ndarray, dose: float) -> float:
    """V_D: % del volumen con dosis ≥ ``dose``."""
    tot = volumes.sum()
    return float(volumes[doses >= dose].sum() / tot * 100.0) if tot > 0 else float("nan")


def stats(st: Structure) -> dict[str, float]:
    d, v = st.doses, st.volumes
    tot = v.sum()
    if tot <= 0 or not len(d):
        return {}
    return {"volume": float(tot), "mean": float((d * v).sum() / tot), "min": float(d.min()), "max": float(d.max()),
            "D98": dose_at_volume(d, v, 98.0), "D95": dose_at_volume(d, v, 95.0), "D50": dose_at_volume(d, v, 50.0),
            "D2": dose_at_volume(d, v, 2.0)}


def cumulative(doses: np.ndarray, volumes: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """% del volumen con dosis ≥ cada borde de ``edges``."""
    tot = volumes.sum()
    if tot <= 0:
        return np.zeros(len(edges))
    order = np.argsort(doses)
    d = doses[order]
    above = np.cumsum(volumes[order][::-1])[::-1]  # volumen con dosis ≥ d[i]
    idx = np.searchsorted(d, edges, side="left")
    out = np.zeros(len(edges))
    ok = idx < len(d)
    out[ok] = above[idx[ok]]
    return 100.0 * out / tot


def dvh_curves(structures: list[Structure], kind: str, title: str, nbins: int = NBINS) -> Curves:
    """Curvas acumuladas de las estructuras (con sus medidas en ``meta['stats']``)."""
    structures = [s for s in structures if s.volume > 0]
    dmax = max((float(s.doses.max()) for s in structures if len(s.doses)), default=1.0)
    edges = np.linspace(0.0, dmax * 1.0001 if dmax > 0 else 1.0, nbins + 1)
    series = [Series(s.name, cumulative(s.doses, s.volumes, edges[:-1]), None) for s in structures]
    meta = {"stats": {s.name: stats(s) for s in structures}, "volumes": {s.name: s.volume for s in structures}}
    return Curves(kind, title, edges[:-1], "dosis", "eV/g", "Volumen", "%", series, histogram=False, meta=meta)


def _voxel_volumes(grid: Grid) -> np.ndarray:
    return grid.voxel_volumes()


def box_structures(grid: Grid, session, case=None, max_points: int = 2_000_000,
                   layer_values: np.ndarray | None = None) -> list[Structure]:
    """Cuerpos que corta una rejilla de caja, con el volumen de cada vóxel que cae en cada uno."""
    if grid.geometry != "box":
        raise ValueError("Solo para mapas 3D de caja (GRIDX/GRIDY/GRIDZ)")
    geo = session.geo
    nb = geo.nbodys
    v = grid.value if layer_values is None else layer_values
    nx, ny, nz = grid.shape
    nvox = nx * ny * nz
    s = int(np.clip(np.floor((max_points / max(nvox, 1)) ** (1.0 / 3.0)), 1, 4))
    frac_pts = (np.arange(s) + 0.5) / s
    fx, fy, fz = np.meshgrid(frac_pts, frac_pts, frac_pts, indexing="ij")
    sub = np.stack([fx.ravel(), fy.ravel(), fz.ravel()], axis=1)  # (s³, 3) en [0, 1)
    ex, ey, ez = grid.edges
    nk = nb + 2
    keys_all: list[np.ndarray] = []
    cnt_all: list[np.ndarray] = []
    # Por capas de z para no ocupar demasiada memoria; se guardan solo los pares (vóxel, cuerpo) que aparecen.
    ix, iy = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    ix, iy = ix.ravel(), iy.ravel()
    per_layer = nx * ny
    layers = max(1, 1_000_000 // max(per_layer * len(sub), 1))
    for z0 in range(0, nz, layers):
        zs = np.arange(z0, min(z0 + layers, nz))
        cx = np.tile(ix, len(zs))
        cy = np.tile(iy, len(zs))
        cz = np.repeat(zs, per_layer)
        lo = np.stack([ex[cx], ey[cy], ez[cz]], axis=1)
        size = np.stack([ex[cx + 1] - ex[cx], ey[cy + 1] - ey[cy], ez[cz + 1] - ez[cz]], axis=1)
        pts = (lo[:, None, :] + size[:, None, :] * sub[None, :, :]).reshape(-1, 3)
        ib, _m = session.locator.locate(pts)
        ib = np.clip(np.asarray(ib, dtype=np.int64), 0, nb + 1).reshape(len(cx), len(sub))
        flat = ((cx * ny + cy) * nz + cz).astype(np.int64)  # índice del vóxel en el orden de value.ravel()
        k, c = np.unique((flat[:, None] * nk + ib).ravel(), return_counts=True)
        keys_all.append(k)
        cnt_all.append(c)
    keys = np.concatenate(keys_all) if keys_all else np.zeros(0, dtype=np.int64)
    cnts = np.concatenate(cnt_all) if cnt_all else np.zeros(0, dtype=np.int64)
    vox, body = keys // nk, keys % nk
    vol = _voxel_volumes(grid).ravel()
    dose = np.asarray(v, dtype=np.float64).ravel()
    out = []
    for kb in range(1, nb + 1):
        sel = body == kb
        if not sel.any():
            continue
        if geo.body(kb).origin == "enclosure":
            continue
        name = case.body_name(kb) if case is not None else f"n.º {kb}"
        idx = vox[sel]
        out.append(Structure(name, dose[idx], vol[idx] * cnts[sel] / len(sub), kb=kb))
    return out


def ct_structures(grid: Grid, ct, families: dict[int, str] | None = None, material_names: dict[int, str] | None = None,
                  layer_values: np.ndarray | None = None) -> list[Structure]:
    """Tejidos del TAC (materiales, con los partidos por densidad juntos si se dan sus ``families``)."""
    if grid.geometry != "ct":
        raise ValueError("Solo para la dosis del TAC")
    v = np.asarray(grid.value if layer_values is None else layer_values, dtype=np.float64)
    mat = np.transpose(np.asarray(ct.material), (2, 1, 0))  # (NX, NY, NZ), como la rejilla
    if mat.shape != v.shape:
        raise ValueError("El TAC de la geometría no es el de estos resultados")
    dv = float(np.prod(ct.voxel_size))
    families = families or {}
    material_names = material_names or {}
    groups: dict[str, list[int]] = {}
    for m in sorted(int(x) for x in np.unique(mat) if x > 0):
        name = families.get(m) or material_names.get(m) or f"material {m}"
        groups.setdefault(name, []).append(m)
    out = []
    for name, mats in groups.items():
        sel = np.isin(mat, mats)
        d = v[sel]
        out.append(Structure(name, d, np.full(d.shape, dv)))
    return out


def whole_ct(grid: Grid, ct=None) -> Structure:
    """El TAC entero (sin los vóxeles de vacío si se da el TAC)."""
    v = np.asarray(grid.value, dtype=np.float64)
    if ct is not None:
        mat = np.transpose(np.asarray(ct.material), (2, 1, 0))
        sel = mat > 0 if mat.shape == v.shape else np.ones(v.shape, dtype=bool)
    else:
        sel = np.ones(v.shape, dtype=bool)
    vol = grid.voxel_volumes()
    return Structure("TAC", v[sel], vol[sel])


__all__ = ["NBINS", "Structure", "box_structures", "ct_structures", "cumulative", "dose_at_volume", "dvh_curves",
           "mark_partial", "stats", "volume_at_dose", "whole_ct"]
