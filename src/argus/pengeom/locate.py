"""Localizador vectorizado: la subrutina LOCATE de PENGEOM para muchos puntos.

Reproduce exactamente la lógica de LOCATE (pengeom.f 2024):

* se recorre el árbol desde el módulo raíz (``KB0 = NBODYS``);
* en cada nivel se evalúan las superficies del nodo con ``KFLAG <= 4`` que
  aún no se hayan evaluado (el conjunto evaluado depende solo del camino);
* las hijas se prueban en orden; una hija encaja si todas sus superficies con
  ``KFLAG < 3`` están evaluadas y el punto está en el lado indicado;
* superficie "difusa": ``FUZZ = 1e-12·|B²-4AC|/|A|`` (o ``1e-12·|B|`` si
  ``|A| <= 1e-36``); dentro de la franja decide el sentido de la dirección.

La evaluación de F(r) sigue el mismo orden de operaciones que FSURF, de modo
que los puntos situados justo sobre una superficie se resuelven como en
PENGEOM.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .compiler import CompiledGeometry

FUZZL = 1.0e-12


@dataclass
class _Node:
    constraints_ks: np.ndarray  # superficies con KFLAG < 3
    constraints_kf: np.ndarray  # su KFLAG (1 = lado -1, 2 = lado +1)
    evaluated: np.ndarray  # superficies con KFLAG <= 4 (se evalúan en este nodo)
    daughters: list[int]
    is_container: bool  # KDGHT(KB,NXG) > 1


class Locator:
    """LOCATE sobre una geometría compilada.

    ``backend``: 'numba' (compilado, en paralelo), 'numpy' (vectorizado) o
    'auto' (Numba si está instalado).
    """

    def __init__(self, geo: CompiledGeometry, backend: str = "auto"):
        if not geo.ok:
            raise ValueError("La geometría no está compilada correctamente.")
        from . import locate_fast

        if backend == "auto":
            backend = "numba" if locate_fast.HAVE_NUMBA else "numpy"
        if backend == "numba" and not locate_fast.HAVE_NUMBA:
            raise RuntimeError("Numba no está instalado.")
        self.backend = backend
        self._tables = locate_fast.build_tables(geo) if backend == "numba" else None
        self.geo = geo
        self.nbodys = geo.nbodys
        self.coef = geo.coefficients()
        self.kplane = geo.kplane_array()
        self.material = np.array([0] + [b.material for b in geo.bodies] + [0], dtype=np.int32)
        self.nodes: list[_Node | None] = [None]
        for b in geo.bodies:
            ks = np.array(b.surfaces, dtype=np.int64)
            kf = np.array(b.flags, dtype=np.int64)
            cons = kf < 3
            self.nodes.append(_Node(ks[cons], kf[cons], ks[kf <= 4], list(b.daughters),
                                    len(b.daughters) > 1))

    # -- evaluación de superficies -----------------------------------------
    def side(self, ks: int, x: np.ndarray, y: np.ndarray, z: np.ndarray,
             u: np.ndarray, v: np.ndarray, w: np.ndarray) -> np.ndarray:
        """KSP de la superficie ``ks`` (1: F<0, lado -1; 2: F>0, lado +1)."""
        axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = self.coef[ks - 1]
        if self.kplane[ks - 1] == 0:
            a = u * (axx * u + axy * v + axz * w) + v * (ayy * v + ayz * w) + w * azz * w
            xxx = axx * x + axy * y + axz * z + ax
            yyy = ayy * y + ayz * z + ay
            zzz = azz * z + az
            b = u * (axx * x + xxx) + v * (axy * x + ayy * y + yyy) + w * (axz * x + ayz * y + azz * z + zzz)
            c = x * xxx + y * yyy + z * zzz + a0
            absa = np.abs(a)
            with np.errstate(divide="ignore", invalid="ignore"):
                fuzz = np.where(absa > 1.0e-36, FUZZL * np.abs(b * b - 4.0 * a * c) / absa,
                                FUZZL * np.abs(b))
        else:
            b = u * ax + v * ay + w * az
            c = x * ax + y * ay + z * az + a0
            fuzz = FUZZL * np.abs(b)
        ksp = np.where(b < 0.0, 1, 2).astype(np.int8)
        ksp[c < -fuzz] = 1
        ksp[c > fuzz] = 2
        return ksp

    # -- LOCATE ---------------------------------------------------------------
    def locate(self, pos: np.ndarray, direction: np.ndarray | None = None
               ) -> tuple[np.ndarray, np.ndarray]:
        """Cuerpo (IBODY) y material (MAT) de cada punto.

        ``pos`` es (N,3); ``direction`` (N,3) o (3,) — por defecto +Z. Los
        puntos fuera de la envolvente devuelven ``NBODYS+1`` y material 0.
        """
        p = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
        n = p.shape[0]
        if direction is None:
            d = np.broadcast_to(np.array([0.0, 0.0, 1.0]), (n, 3))
        else:
            d = np.broadcast_to(np.asarray(direction, dtype=np.float64), (n, 3))
        ibody = np.full(n, self.nbodys + 1, dtype=np.int32)
        if n == 0 or self.nbodys == 0:
            return ibody, np.zeros(n, dtype=np.int32)
        if self.backend == "numba":
            from .locate_fast import locate_numba

            pos_c = np.ascontiguousarray(p)
            dir_c = np.ascontiguousarray(d)
            ib = locate_numba(self._tables, self.nbodys, pos_c, dir_c).astype(np.int32)
            mat = self.material[ib]
            return ib, mat
        x, y, z = (np.ascontiguousarray(p[:, i]) for i in range(3))
        u, v, w = (np.ascontiguousarray(d[:, i]) for i in range(3))
        stack: list[tuple[int, np.ndarray, frozenset[int]]] = [(self.nbodys, np.arange(n), frozenset())]
        while stack:
            kb0, idx, done = stack.pop()
            node0 = self.nodes[kb0]
            evaluated = done | frozenset(node0.evaluated.tolist())
            xs, ys, zs = x[idx], y[idx], z[idx]
            us, vs, ws = u[idx], v[idx], w[idx]
            cache: dict[int, np.ndarray] = {}
            rem = np.arange(idx.size)
            for kb in node0.daughters:
                if rem.size == 0:
                    break
                nd = self.nodes[kb]
                ks_list = nd.constraints_ks
                if any(int(k) not in evaluated for k in ks_list):
                    continue  # KSP = 0: ningún punto encaja en esta hija
                match = np.ones(rem.size, dtype=bool)
                for k, f in zip(ks_list.tolist(), nd.constraints_kf.tolist()):
                    s = cache.get(k)
                    if s is None:
                        s = self.side(k, xs, ys, zs, us, vs, ws)
                        cache[k] = s
                    match &= s[rem] == f
                    if not match.any():
                        break
                if not match.any():
                    continue
                sel = rem[match]
                if kb == kb0:
                    ibody[idx[sel]] = kb
                elif nd.is_container:
                    stack.append((kb, idx[sel], evaluated))
                else:
                    ibody[idx[sel]] = kb
                rem = rem[~match]
        mat = self.material[np.clip(ibody, 0, self.nbodys + 1)]
        mat[ibody > self.nbodys] = 0
        return ibody, mat
