"""Comparación de las tablas de ARGUS con las de PENGEOM (sonda)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..pengeom.compiler import CompiledGeometry
from .probe import ProbeTables


@dataclass
class TableComparison:
    ok: bool = True
    differences: list[str] = field(default_factory=list)
    max_coef_diff: float = 0.0
    n_coef_mismatch: int = 0

    def add(self, text: str) -> None:
        self.ok = False
        if len(self.differences) < 40:
            self.differences.append(text)


def compare_tables(geo: CompiledGeometry, tab: ProbeTables, exact: bool = True,
                   rtol: float = 1e-13) -> TableComparison:
    """Compara superficies y árbol. Con ``exact`` los coeficientes deben ser
    idénticos bit a bit; si no, se admite una tolerancia relativa ``rtol``."""
    res = TableComparison()
    if geo.nsurf != tab.nsurf:
        res.add(f"NSURF: ARGUS={geo.nsurf} PENGEOM={tab.nsurf}")
    if geo.nbodys != tab.nbodys:
        res.add(f"NBODYS: ARGUS={geo.nbodys} PENGEOM={tab.nbodys}")
    n = min(geo.nsurf, tab.nsurf)
    if n:
        a = geo.coefficients()[:n]
        b = tab.coefficients[:n]
        diff = np.abs(a - b)
        scale = np.maximum(np.abs(a).max(axis=1, keepdims=True), np.abs(b).max(axis=1, keepdims=True))
        scale[scale == 0] = 1.0
        rel = diff / scale
        res.max_coef_diff = float(rel.max()) if rel.size else 0.0
        bad = np.nonzero((a != b).any(axis=1) if exact else (rel > rtol).any(axis=1))[0]
        res.n_coef_mismatch = int(bad.size)
        for i in bad[:10]:
            res.add(f"Superficie {i + 1}: coeficientes distintos (dif. rel. máx. {rel[i].max():.3e})\n"
                    f"   ARGUS  ={a[i].tolist()}\n   PENGEOM={b[i].tolist()}")
        kp = geo.kplane_array()[:n]
        badk = np.nonzero(kp != tab.kplane[:n])[0]
        for i in badk[:10]:
            res.add(f"Superficie {i + 1}: KPLANE ARGUS={kp[i]} PENGEOM={tab.kplane[i]}")
    for kb in range(1, min(geo.nbodys, tab.nbodys) + 1):
        g = geo.body(kb)
        t = tab.bodies[kb - 1]
        where = f"Cuerpo {kb}"
        if g.material != t.material:
            res.add(f"{where}: MAT ARGUS={g.material} PENGEOM={t.material}")
        if g.mother != t.mother:
            res.add(f"{where}: KMOTH ARGUS={g.mother} PENGEOM={t.mother}")
        if g.is_module != t.is_module:
            res.add(f"{where}: KBOMO ARGUS={int(g.is_module)} PENGEOM={int(t.is_module)}")
        alias = geo.balias(kb).ljust(4)
        if alias != t.alias.ljust(4):
            res.add(f"{where}: BALIAS ARGUS=[{alias}] PENGEOM=[{t.alias}]")
        gd = g.daughters if (g.is_module or kb == 1 and geo.nbodys == 1) else g.daughters
        if list(gd) != list(t.daughters):
            res.add(f"{where}: KDGHT ARGUS={gd} PENGEOM={t.daughters}")
        if list(g.surfaces) != list(t.surfaces):
            res.add(f"{where}: KSURF ARGUS={g.surfaces} PENGEOM={t.surfaces}")
        if list(g.flags) != list(t.flags):
            res.add(f"{where}: KFLAG ARGUS={g.flags} PENGEOM={t.flags}")
        if list(g.limiting) != list(t.limiting):
            res.add(f"{where}: KBODY ARGUS={g.limiting} PENGEOM={t.limiting}")
    return res
