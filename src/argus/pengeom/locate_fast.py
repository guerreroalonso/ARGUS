"""LOCATE compilado con Numba (opcional): la misma lógica punto a punto.

Si Numba está instalado, :class:`argus.pengeom.locate.Locator` usa este
núcleo, que recorre el árbol para cada punto exactamente como la subrutina
LOCATE de PENGEOM (con las superficies difusas y la caché de lados KSP) y
reparte los puntos entre los núcleos de la CPU. Es entre 10 y 100 veces más
rápido que la versión NumPy y da los mismos resultados (se verifica contra
PENGEOM en las pruebas).
"""

from __future__ import annotations

import numpy as np

try:  # pragma: no cover - depende del entorno
    import numba
    from numba import njit, prange

    HAVE_NUMBA = True
except Exception:  # pragma: no cover
    HAVE_NUMBA = False

FUZZL = 1.0e-12


def build_tables(geo) -> dict[str, np.ndarray]:
    """Tablas planas (CSR) para el núcleo compilado."""
    nb = geo.nbodys
    cons_ptr = np.zeros(nb + 2, dtype=np.int64)
    eval_ptr = np.zeros(nb + 2, dtype=np.int64)
    dght_ptr = np.zeros(nb + 2, dtype=np.int64)
    cons_ks, cons_kf, eval_ks, dght = [], [], [], []
    ndght = np.zeros(nb + 2, dtype=np.int64)
    mater = np.zeros(nb + 2, dtype=np.int64)
    for b in geo.bodies:
        kb = b.index
        for ks, kf in zip(b.surfaces, b.flags):
            if kf < 3:
                cons_ks.append(ks)
                cons_kf.append(kf)
            if kf <= 4:
                eval_ks.append(ks)
        dght.extend(b.daughters)
        cons_ptr[kb + 1] = len(cons_ks)
        eval_ptr[kb + 1] = len(eval_ks)
        dght_ptr[kb + 1] = len(dght)
        ndght[kb] = len(b.daughters)
        mater[kb] = b.material
    cons_ptr[nb + 1] = len(cons_ks)
    eval_ptr[nb + 1] = len(eval_ks)
    dght_ptr[nb + 1] = len(dght)
    # Superficies ya evaluadas al llegar a cada nodo: las de KFLAG<=4 de todos
    # los nodos del camino desde la raíz (el camino es la cadena de madres).
    # Una hija solo puede encajar si todas sus superficies con KFLAG<3 están
    # evaluadas; se precalcula para no evaluar superficies innecesarias.
    evaluated_at: dict[int, set[int]] = {}

    def evaluated(kb: int) -> set[int]:
        chain = []
        k = kb
        while k > 0 and k not in evaluated_at:
            chain.append(k)
            k = geo.bodies[k - 1].mother
        acc = set(evaluated_at[k]) if k > 0 else set()
        for c in reversed(chain):
            b = geo.bodies[c - 1]
            acc = acc | {ks for ks, kf in zip(b.surfaces, b.flags) if kf <= 4}
            evaluated_at[c] = acc
        return evaluated_at[kb]

    dok = np.zeros(len(dght), dtype=np.int8)
    for b in geo.bodies:
        if len(b.daughters) == 0:
            continue
        ev = evaluated(b.index)
        for j in range(dght_ptr[b.index], dght_ptr[b.index + 1]):
            d = geo.bodies[dght[j] - 1]
            dok[j] = all(ks in ev for ks, kf in zip(d.surfaces, d.flags) if kf < 3)
    coef = np.zeros((geo.nsurf + 1, 10), dtype=np.float64)
    coef[1:] = geo.coefficients()
    kplane = np.zeros(geo.nsurf + 1, dtype=np.int64)
    kplane[1:] = geo.kplane_array()
    return {
        "coef": coef, "kplane": kplane,
        "cons_ptr": cons_ptr, "cons_ks": np.array(cons_ks, dtype=np.int64),
        "cons_kf": np.array(cons_kf, dtype=np.int64),
        "eval_ptr": eval_ptr, "eval_ks": np.array(eval_ks, dtype=np.int64),
        "dght_ptr": dght_ptr, "dght": np.array(dght, dtype=np.int64), "dok": dok,
        "ndght": ndght, "mater": mater,
    }


if HAVE_NUMBA:  # pragma: no branch

    @njit(cache=True, nogil=True)
    def _side(coef, kplane, ks, x, y, z, u, v, w):
        axx = coef[ks, 0]
        axy = coef[ks, 1]
        axz = coef[ks, 2]
        ayy = coef[ks, 3]
        ayz = coef[ks, 4]
        azz = coef[ks, 5]
        ax = coef[ks, 6]
        ay = coef[ks, 7]
        az = coef[ks, 8]
        a0 = coef[ks, 9]
        if kplane[ks] == 0:
            a = u * (axx * u + axy * v + axz * w) + v * (ayy * v + ayz * w) + w * azz * w
            xxx = axx * x + axy * y + axz * z + ax
            yyy = ayy * y + ayz * z + ay
            zzz = azz * z + az
            b = u * (axx * x + xxx) + v * (axy * x + ayy * y + yyy) + w * (axz * x + ayz * y + azz * z + zzz)
            c = x * xxx + y * yyy + z * zzz + a0
        else:
            a = 0.0
            b = u * ax + v * ay + w * az
            c = x * ax + y * ay + z * az + a0
        absa = abs(a)
        if absa > 1.0e-36:
            fuzz = FUZZL * abs(b * b - 4.0 * a * c) / absa
        else:
            fuzz = FUZZL * abs(b)
        if c < -fuzz:
            return 1
        if c > fuzz:
            return 2
        if b < 0.0:
            return 1
        return 2

    @njit(cache=True, nogil=True)
    def _locate_range(i0, i1, pos, dirs, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf,
                      dght_ptr, dght, dok, ndght, out):
        ns = coef.shape[0]
        ksp = np.zeros(ns, dtype=np.int8)
        stamp = np.zeros(ns, dtype=np.int64)
        for p in range(i0, i1):
            cur = p + 1
            x = pos[p, 0]
            y = pos[p, 1]
            z = pos[p, 2]
            u = dirs[p, 0]
            v = dirs[p, 1]
            w = dirs[p, 2]
            kb0 = nbodys
            result = nbodys + 1
            while True:
                descend = -1
                done = False
                for jd in range(dght_ptr[kb0], dght_ptr[kb0 + 1]):
                    if dok[jd] == 0:
                        continue  # alguna superficie sin evaluar: KSP=0, no encaja
                    kb = dght[jd]
                    ok = True
                    for c in range(cons_ptr[kb], cons_ptr[kb + 1]):
                        ks = cons_ks[c]
                        if stamp[ks] != cur:
                            ksp[ks] = _side(coef, kplane, ks, x, y, z, u, v, w)
                            stamp[ks] = cur
                        if ksp[ks] != cons_kf[c]:
                            ok = False
                            break
                    if not ok:
                        continue
                    if kb == kb0:
                        result = kb
                        done = True
                    elif ndght[kb] > 1:
                        descend = kb
                    else:
                        result = kb
                        done = True
                    break
                if done or descend < 0:
                    break
                kb0 = descend
            out[p] = result

    @njit(parallel=True, cache=True)
    def _locate_parallel(pos, dirs, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf,
                         dght_ptr, dght, dok, ndght, out, nchunks):
        n = pos.shape[0]
        size = (n + nchunks - 1) // nchunks
        for ch in prange(nchunks):
            i0 = ch * size
            i1 = min(n, i0 + size)
            if i0 < i1:
                _locate_range(i0, i1, pos, dirs, nbodys, coef, kplane, cons_ptr, cons_ks, cons_kf,
                              dght_ptr, dght, dok, ndght, out)


def locate_numba(tables: dict[str, np.ndarray], nbodys: int, pos: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    n = pos.shape[0]
    out = np.empty(n, dtype=np.int64)
    if n == 0:
        return out
    t = tables
    args = (nbodys, t["coef"], t["kplane"], t["cons_ptr"], t["cons_ks"], t["cons_kf"],
            t["dght_ptr"], t["dght"], t["dok"], t["ndght"], out)
    if n < 2048:
        _locate_range(0, n, pos, dirs, *args)
    else:
        from ..core.parallel import PARALLEL_LOCK

        with PARALLEL_LOCK:  # la capa «workqueue» de Numba no admite dos lanzamientos a la vez
            nchunks = max(1, min(n // 256, numba.get_num_threads() * 8))
            _locate_parallel(pos, dirs, *args, nchunks)
    return out


def warmup() -> None:
    """Compila el núcleo (la primera vez tarda unos segundos; luego queda en caché)."""
    if not HAVE_NUMBA:
        return
    from types import SimpleNamespace

    body = SimpleNamespace(index=1, surfaces=[1], flags=[1], daughters=[1], material=1, mother=0)
    geo = SimpleNamespace(nbodys=1, nsurf=1, bodies=[body],
                          coefficients=lambda: np.array([[1.0, 0, 0, 1.0, 0, 1.0, 0, 0, 0, -1.0]]),
                          kplane_array=lambda: np.array([0]))
    t = build_tables(geo)
    pts = np.zeros((4096, 3))
    d = np.tile([0.0, 0.0, 1.0], (4096, 1))
    locate_numba(t, 1, pts[:10], d[:10])
    locate_numba(t, 1, pts, d)
