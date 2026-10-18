"""Partir los tejidos de un TAC en materiales por densidad (argus.ct.subdivide)."""

from __future__ import annotations

import itertools
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

from argus.ct import subdivide as sd

# Curva de Schneider tal como la distribuye GATE y los límites de los cinco tejidos (copiados aquí para que la
# prueba no dependa de la calibración por defecto).
CURVE = ((-1000.0, 0.00121), (-98.0, 0.93), (-97.0, 0.930486), (14.0, 1.03), (23.0, 1.031), (100.0, 1.1199),
         (101.0, 1.0762), (1600.0, 1.9642), (3000.0, 2.8))
STARTS = (-np.inf, -950.0, -120.0, -22.0, 120.0)
SOFT = {1: 0.103831, 6: 0.135081, 7: 0.030242, 8: 0.728831, 15: 0.002016}


# -- utilidades ----------------------------------------------------------------------------------------------

def _hist_from_values(values, nbins=16, lo=0.0, hi=1.0):
    """Histograma (vóxeles, Σ(ρ−c), Σ(ρ−c)²) de unos valores, con el centro c del rango."""
    c = 0.5 * (lo + hi)
    b = np.clip(((values - lo) / (hi - lo) * nbins).astype(int), 0, nbins - 1)
    cnt = np.bincount(b, minlength=nbins)
    s1 = np.bincount(b, weights=values - c, minlength=nbins)
    s2 = np.bincount(b, weights=(values - c) ** 2, minlength=nbins)
    return cnt, s1, s2, b


def _ssd(values, groups):
    return sum(float(((values[g] - values[g].mean()) ** 2).sum()) for g in groups if g.any())


def _brute_force(values, bins, kmax):
    """Mínima suma de cuadrados con k trozos de intervalos consecutivos (todas las particiones)."""
    nz = np.unique(bins)
    out = []
    for k in range(1, min(kmax, nz.size) + 1):
        best = np.inf
        for cuts in itertools.combinations(range(1, nz.size), k - 1):
            edges = [0, *cuts, nz.size]
            groups = [np.isin(bins, nz[a:b]) for a, b in zip(edges, edges[1:])]
            best = min(best, _ssd(values, groups))
        out.append(best)
    return np.array(out)


def _synthetic_ct(shape=(12, 48, 64), seed=0):
    """TAC de tórax de juguete: cuerpo blando con grasa, dos pulmones con gradiente, un hueso y aire."""
    rng = np.random.default_rng(seed)
    nz, ny, nx = shape
    z, y, x = np.meshgrid(np.linspace(-1, 1, nz), np.linspace(-1, 1, ny), np.linspace(-1, 1, nx), indexing="ij")
    hu = np.full(shape, -1000.0) + 5 * rng.standard_normal(shape)
    body = (x / 0.9) ** 2 + (y / 0.7) ** 2 < 1
    hu[body] = 40 + 20 * rng.standard_normal(body.sum())
    fat = body & ((x / 0.9) ** 2 + (y / 0.7) ** 2 > 0.8)
    hu[fat] = -100 + 10 * rng.standard_normal(fat.sum())
    lung = (((x - 0.4) / 0.3) ** 2 + (y / 0.45) ** 2 < 1) | (((x + 0.4) / 0.3) ** 2 + (y / 0.45) ** 2 < 1)
    hu[lung] = -800 + 120 * rng.standard_normal(lung.sum()) + 250 * (y[lung] + 0.5)
    bone = (x / 0.12) ** 2 + ((y - 0.5) / 0.12) ** 2 < 1
    hu[bone] = 700 + 300 * rng.standard_normal(bone.sum())
    h = np.array([p[0] for p in CURVE])
    r = np.array([p[1] for p in CURVE])
    idx = np.searchsorted(np.array(STARTS[1:]), hu, side="right").astype(np.int16)
    return idx, np.interp(hu, h, r)


def _infos(**kw):
    rng = sd.density_ranges(CURVE, STARTS)
    names = ("Aire", "Pulmón", "Adiposo", "Tejido blando", "Hueso")
    comps = ({7: 0.755, 8: 0.232, 18: 0.013}, {1: 0.10404, 6: 0.106061, 7: 0.031313, 8: 0.756566, 15: 0.00202},
             {1: 0.116349, 6: 0.683049, 7: 0.002006, 8: 0.198596}, SOFT,
             {1: 0.056338, 6: 0.2666, 7: 0.036217, 8: 0.407445, 15: 0.073441, 20: 0.15996})
    return [sd.TissueInfo(i, comps[i], None, rng[i], name=names[i], **kw) for i in range(5)]


VOXEL = (0.25, 0.25, 0.5)


# -- rangos de densidad --------------------------------------------------------------------------------------

def test_density_ranges_of_the_schneider_tissues():
    rng = sd.density_ranges(CURVE, STARTS)
    expected = [(0.00121, 0.00121 + 50 / 902 * (0.93 - 0.00121)),
                (0.00121 + 50 / 902 * (0.93 - 0.00121), 0.00121 + 880 / 902 * (0.93 - 0.00121)),
                (0.00121 + 880 / 902 * (0.93 - 0.00121), 0.930486 + 75 / 111 * (1.03 - 0.930486)),
                (0.930486 + 75 / 111 * (1.03 - 0.930486), 1.1199),  # la curva sube a 1,1199 y baja (no monótona)
                (1.0762 + 19 / 1499 * (1.9642 - 1.0762), 2.8)]
    for (a, b), (ea, eb) in zip(rng, expected):
        assert a == pytest.approx(ea, abs=1e-12) and b == pytest.approx(eb, abs=1e-12)
    with pytest.raises(ValueError):
        sd.density_ranges(CURVE, (-np.inf, 10.0, 5.0))


# -- partición óptima de un tejido ----------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(6))
def test_dp_equals_brute_force(seed):
    rng = np.random.default_rng(seed)
    values = np.concatenate([rng.normal(rng.uniform(0.1, 0.9), rng.uniform(0.01, 0.1), rng.integers(1, 30))
                             for _ in range(rng.integers(1, 5))])
    values = np.clip(values, 0.0, 0.999)
    cnt, s1, s2, bins = _hist_from_values(values, nbins=int(rng.integers(6, 12)))
    J, parts = sd.optimal_partitions(cnt, s1, s2, 6)
    brute = _brute_force(values, bins, 6)
    assert J.size == brute.size
    np.testing.assert_allclose(J, brute, rtol=1e-9, atol=1e-12)
    nz = np.flatnonzero(cnt)
    for k, p in enumerate(parts, start=1):  # la partición que devuelve tiene el coste J(k)
        assert p[0] == 0 and p[-1] == nz.size and np.all(np.diff(p) > 0) and p.size == k + 1
        groups = [np.isin(bins, nz[a:b]) for a, b in zip(p, p[1:])]
        assert _ssd(values, groups) == pytest.approx(J[k - 1], rel=1e-9, abs=1e-12)


@pytest.mark.parametrize("seed", range(8))
def test_J_is_convex(seed):
    rng = np.random.default_rng(100 + seed)
    nbins = int(rng.integers(20, 80))
    cnt = rng.integers(0, 50, nbins) * (rng.random(nbins) < 0.7)
    centers = (np.arange(nbins) + rng.random(nbins)) / nbins - 0.5
    s1 = cnt * centers
    s2 = cnt * (centers ** 2 + rng.random(nbins) * 1e-4)  # algo de dispersión dentro de cada intervalo
    J, _parts = sd.optimal_partitions(cnt, s1, s2, 40)
    assert np.all(np.diff(J) <= 1e-12 * J[0])  # no crece
    assert np.all(np.diff(J, 2) >= -1e-9 * J[0])  # convexa


def test_dp_ties_and_degenerate_histograms():
    J, parts = sd.optimal_partitions(np.zeros(8), np.zeros(8), np.zeros(8), 3)
    assert J.size == 0 and parts == []
    J, parts = sd.optimal_partitions([0, 5, 0], [0, 0.5, 0], [0, 0.05, 0], 4)  # un solo intervalo
    assert J.size == 1 and J[0] == pytest.approx(0.0, abs=1e-15) and parts[0].tolist() == [0, 1]
    # tres intervalos iguales y simétricos: con 2 trozos hay dos soluciones; se queda con la del primer mínimo
    cnt = np.array([1.0, 1.0, 1.0])
    s1 = np.array([-1.0, 0.0, 1.0])
    J, parts = sd.optimal_partitions(cnt, s1, s1 ** 2, 2)
    assert parts[1].tolist() == [0, 1, 3] and J[1] == pytest.approx(0.5)
    assert sd.optimal_partitions(cnt, s1, s1 ** 2, 2)[1][1].tolist() == [0, 1, 3]  # determinista


# -- reparto voraz -------------------------------------------------------------------------------------------

def _exhaustive(J, w, cap, total):
    best = np.inf
    ranges = [range(1, c + 1) if c >= 1 else range(0, 1) for c in cap]
    for ks in itertools.product(*ranges):
        if sum(ks) == total:
            best = min(best, sum(w[t] * J[t][k - 1] for t, k in enumerate(ks) if k >= 1))
    return best


@pytest.mark.parametrize("seed", range(10))
def test_greedy_equals_exhaustive(seed):
    rng = np.random.default_rng(200 + seed)
    t_count = int(rng.integers(2, 5))
    J, w, cap = [], [], []
    for _t in range(t_count):
        values = rng.normal(0.0, rng.uniform(0.01, 0.3), int(rng.integers(20, 200)))
        cnt, s1, s2, _b = _hist_from_values(values, nbins=int(rng.integers(8, 20)), lo=-1.0, hi=1.0)
        jt, _parts = sd.optimal_partitions(cnt, s1, s2, 5)
        J.append(tuple(jt))
        w.append(float(rng.uniform(0.8, 1.1) ** 2))
        cap.append(int(rng.integers(0, jt.size + 1)) if rng.random() < 0.2 else jt.size)
    seq = sd.greedy_allocation(J, w, cap, 99)
    assert seq[0] == tuple(1 if c >= 1 else 0 for c in cap)
    for a, b in zip(seq, seq[1:]):  # anidados: un trozo más cada vez
        assert sum(b) == sum(a) + 1 and all(y >= x for x, y in zip(a, b))
    for ks in seq:
        assert all(k <= max(c, 0) for k, c in zip(ks, cap))
        obj = sum(w[t] * J[t][k - 1] for t, k in enumerate(ks) if k >= 1)
        assert obj == pytest.approx(_exhaustive(J, w, cap, sum(ks)), rel=1e-9, abs=1e-12)


def test_greedy_ties_go_to_the_lower_index_and_stop_without_gain():
    J = [(4.0, 1.0, 0.0), (4.0, 1.0, 0.0), (2.0, 2.0)]
    seq = sd.greedy_allocation(J, [1.0, 1.0, 1.0], [3, 3, 2], 10)
    assert seq == [(1, 1, 1), (2, 1, 1), (2, 2, 1), (3, 2, 1), (3, 3, 1)]  # el tercero no gana nada
    assert sd.greedy_allocation(J, [1.0, 1.0, 1.0], [3, 3, 2], 4) == seq[:2]
    assert sd.greedy_allocation(J, [1.0, 1.0, 1.0], [3, 3, 2], 2) == seq[:1]  # sin partir aunque no quepa


def test_setup_seconds():
    assert sd.setup_seconds((1, 3, 1, 1, 2), (3, 5, 4, 5, 6)) == (2 * 5 + 1 * 6) * 33.0
    assert sd.setup_seconds((0, 1, 1), (3, 5, 4)) == 0.0


# -- asignación ------------------------------------------------------------------------------------------------

def test_assign_equals_digitize_per_tissue():
    rng = np.random.default_rng(3)
    ti = rng.integers(-1, 4, (5, 6, 7))
    cuts = [(0.2, 0.5), (), (1.05,), (0.1, 0.4, 0.8)]
    rho = rng.random(ti.shape) * 1.2
    ti.reshape(-1)[:12] = [0, 0, 2, 3, 3, 3, 0, 0, 2, 3, 3, 3]
    rho.reshape(-1)[:12] = [0.2, 0.5, 1.05, 0.1, 0.4, 0.8, 0.2, 0.5, 1.05, 0.1, 0.4, 0.8]  # justo en sus cortes
    pieces = sd.assign(ti, rho, cuts)
    for t, c in enumerate(cuts):
        sel = ti == t
        assert np.array_equal(pieces[sel], np.digitize(rho[sel], c) if c else np.zeros(sel.sum()))
    assert np.all(pieces[ti < 0] == -1)
    assert pieces.reshape(-1)[:12].tolist() == [1, 2, 1, 1, 2, 3] * 2  # en el corte, el trozo de arriba
    assert np.array_equal(sd.assign(ti, rho, {0: cuts[0], 2: cuts[2], 3: cuts[3]}), pieces)
    with pytest.raises(ValueError):
        sd.assign(ti, rho, [(0.5, 0.2)])
    with pytest.raises(ValueError):
        sd.assign(ti, rho[:2], cuts)


def test_same_density_same_piece_whatever_the_hu():
    """La curva no es monótona: 1,0815 g/cm³ sale a 66,7 HU y a 110 HU; el trozo depende solo de la densidad."""
    h = np.array([p[0] for p in CURVE])
    r = np.array([p[1] for p in CURVE])
    target = 1.0762 + 9 / 1499 * (1.9642 - 1.0762)  # 110 HU, tras la bajada
    rho = np.interp([23 + (target - 1.031) / (1.1199 - 1.031) * 77, 110.0], h, r)  # 66,8 HU, antes de la bajada
    assert rho[0] == pytest.approx(rho[1], abs=1e-9)
    rho[0] = rho[1]
    pieces = sd.assign(np.array([3, 3]), rho, {3: (1.05, 1.1)})
    assert pieces[0] == pieces[1] == 1


def test_piece_stats():
    ti = np.array([0, 0, 0, 1, 1, -1, 0])
    rho = np.array([0.1, 0.3, 0.5, 1.0, 1.2, 0.7, 0.3])
    st = sd.piece_stats(ti, rho, [(0.3,), ()])
    assert st[0][0] == (1, 3) and st[0][1] == pytest.approx((0.1, (0.3 + 0.5 + 0.3) / 3))
    assert st[1] == ((2,), (pytest.approx(1.1),))


# -- modelo de rayos -------------------------------------------------------------------------------------------

def _loop_errors(ti, den, rho_mat, stopping, vs, direction, steps):
    """Lo mismo que RayModel.errors, rayo a rayo y vóxel a vóxel."""
    axis = {"x": 2, "y": 1, "z": 0}[direction[1]]
    ds = {2: vs[0], 1: vs[1], 0: vs[2]}[axis]
    a1, a2 = (a for a in range(3) if a != axis)
    out = []
    for i1 in range(0, ti.shape[a1], steps[0]):
        for i2 in range(0, ti.shape[a2], steps[1]):
            order = range(ti.shape[axis]) if direction[0] == "+" else range(ti.shape[axis] - 1, -1, -1)
            wet = err = 0.0
            for j in order:
                p = [0, 0, 0]
                p[axis], p[a1], p[a2] = j, i1, i2
                p = tuple(p)
                t = int(ti[p])
                s = stopping[t] if t >= 0 else 0.0
                rho = float(np.float32(den[p])) if t >= 0 else 0.0
                wet += s * rho * ds
                err += 0.72 * s * (float(np.float32(rho_mat[p])) - rho) * ds * 10.0
                if t >= 0 and rho >= 0.5 and wet >= 3.0:
                    out.append(err)
    return np.sort(np.array(out))


@pytest.mark.parametrize("max_voxels", [10 ** 6, 40])
def test_ray_errors_equal_a_plain_loop(max_voxels):
    rng = np.random.default_rng(7)
    shape = (5, 6, 7)
    ti = rng.integers(-1, 3, shape)
    den = rng.random(shape) * 2.0
    rho_mat = den + rng.normal(0, 0.2, shape)
    stopping = [1.0, 0.9, 1.1]
    vs = (0.7, 0.9, 1.3)
    dirs = ("+x", "-x", "+y", "-y", "+z", "-z")
    model = sd.RayModel(ti, den, stopping, vs, dirs, max_voxels=max_voxels)
    errs = model.errors(rho_mat)
    assert tuple(errs) == dirs
    total = 0
    for d in dirs:
        expected = _loop_errors(ti, den, rho_mat, stopping, vs, d, model.strides[d[1]])
        assert expected.size > 0
        np.testing.assert_allclose(np.sort(errs[d]), expected, atol=2e-4, rtol=1e-5)
        total += expected.size
    assert model.n_stop == total
    if max_voxels == 40:
        assert all(s != (1, 1) for s in model.strides.values())


def test_ray_strides_keep_the_most_rays():
    for shape, axis, limit in (((120, 256, 330), 2, 3_000_000), ((140, 156, 200), 1, 3_000_000), ((7, 9, 11), 0, 20),
                               ((1, 1, 50), 2, 10), ((3, 40, 2), 1, 1_000)):
        sl, (s1, s2) = sd._ray_view(shape, axis, limit)
        a1, a2 = (a for a in range(3) if a != axis)
        rays = -(-shape[a1] // s1) * -(-shape[a2] // s2)
        best = max((-(-shape[a1] // u)) * (-(-shape[a2] // v)) for u in range(1, shape[a1] + 1)
                   for v in range(1, shape[a2] + 1)
                   if (-(-shape[a1] // u)) * (-(-shape[a2] // v)) * shape[axis] <= limit) \
            if shape[axis] <= limit else 1
        assert rays == best
        assert np.zeros(shape)[sl].size == rays * shape[axis]


def test_zero_error_when_every_voxel_has_its_piece_density():
    rng = np.random.default_rng(11)
    shape = (6, 20, 24)
    ti = rng.integers(0, 3, shape).astype(np.int8)
    levels = {0: (1.0, 1.1), 1: (0.15, 0.3, 0.45), 2: (1.3, 1.8)}
    den = np.zeros(shape)
    for t, vals in levels.items():
        sel = ti == t
        den[sel] = rng.choice(vals, sel.sum())
    infos = [sd.TissueInfo(0, stopping=1.0, density_range=(0.9, 1.2)),
             sd.TissueInfo(1, stopping=0.9, density_range=(0.05, 0.9)),
             sd.TissueInfo(2, stopping=0.93, density_range=(1.1, 2.8))]
    a = sd.analyse(ti, den, (1.0, 1.0, 1.0), infos, 7)
    last = a.candidates[-1]
    assert last.pieces == (2, 3, 2) and last.n_materials == 7
    assert a.unsplit.error.rms_mm > 1.0
    assert last.error.rms_mm < 1e-4 and last.error.p95_mm < 1e-4
    for t, vals in levels.items():
        assert last.means[t] == pytest.approx(vals, abs=1e-12)
    model = sd.RayModel(ti, den, a.stopping, (1.0, 1.0, 1.0))
    assert model.estimate(den).rms_mm < 1e-5


def test_analytic_slabs():
    """Dos filas: pulmón de 0,2 o de 0,4 g/cm³ (10 vóxeles) entre tejido blando; el .mat del pulmón, 0,3."""
    nx = 40
    ti = np.zeros((1, 2, nx), dtype=np.int16)
    ti[:, :, 10:20] = 1
    den = np.ones((1, 2, nx))
    den[0, 0, 10:20] = 0.2
    den[0, 1, 10:20] = 0.4
    vs = (0.4, 5.0, 5.0)
    infos = [sd.TissueInfo(0, stopping=1.0, density_range=(0.9, 1.2)),
             sd.TissueInfo(1, stopping=0.9, density_range=(0.05, 0.9))]
    a = sd.analyse(ti, den, vs, infos, 3, ("+x", "-x"))
    un = a.unsplit
    assert un.pieces == (1, 1) and un.means[1] == pytest.approx((0.3,))
    err = 0.72 * 0.9 * 0.1 * 0.4 * 10 * 10  # mm tras cruzar 10 vóxeles de pulmón
    # +x: 3 puntos antes del pulmón (0,4·8 ≥ 3 cm) sin error y 20 después; −x: 13 antes y 10 después; dos filas
    assert un.error.n_stop == 2 * (3 + 20 + 13 + 10)
    assert un.error.p50_mm == pytest.approx(err, rel=1e-5) and un.error.p95_mm == pytest.approx(err, rel=1e-5)
    assert un.error.rms_mm == pytest.approx(err * np.sqrt(60 / 92), rel=1e-5)
    signed = sd.RayModel(ti, den, a.stopping, vs, ("+x",)).errors(np.where(ti == 1, 0.3, 1.0))["+x"]
    assert signed.max() == pytest.approx(err, rel=1e-5) and signed.min() == pytest.approx(-err, rel=1e-5)
    split = a.candidates[1]
    assert split.pieces == (1, 2) and split.cuts[1][0] > 0.2 and split.cuts[1][0] <= 0.4
    assert split.means[1] == pytest.approx((0.2, 0.4)) and split.voxels[1] == (10, 10)
    assert split.error.rms_mm < 1e-5 and a.recommended == 1
    assert split.setup_s == 0.0  # sin composición ni n_elements: 0 elementos


# -- el análisis completo --------------------------------------------------------------------------------------

def test_analyse_on_a_synthetic_thorax():
    ti, den = _synthetic_ct()
    a = sd.analyse(ti, den, VOXEL, _infos(), 10)
    assert a.voxels == tuple(int((ti == t).sum()) for t in range(5)) and a.absent == ()
    un = a.unsplit
    assert un.n_materials == 5 and un.pieces == (1, 1, 1, 1, 1)
    for t in range(5):
        assert un.means[t][0] == pytest.approx(den[ti == t].mean(), rel=1e-12)
    ms = [c.n_materials for c in a.candidates]
    assert ms == list(range(5, 5 + len(ms))) and ms[-1] <= 10
    for prev, cur in zip(a.candidates, a.candidates[1:]):
        assert sum(cur.pieces) == sum(prev.pieces) + 1 and all(y >= x for x, y in zip(prev.pieces, cur.pieces))
        assert cur.objective <= prev.objective
    for c in a.candidates:  # medias y vóxeles exactos con los cortes redondeados
        for t in range(5):
            assert len(c.cuts[t]) == c.pieces[t] - 1
            assert all(float(f"{v:.5g}") == v for v in c.cuts[t])
            if c.pieces[t] > 1:
                p = sd.assign(ti, den, {t: c.cuts[t]})[ti == t]
                vals = den[ti == t]
                assert c.voxels[t] == tuple(int((p == i).sum()) for i in range(c.pieces[t]))
                assert c.means[t] == pytest.approx(tuple(vals[p == i].mean() for i in range(c.pieces[t])),
                                                   rel=1e-12)
        assert c.setup_s == sd.setup_seconds(c.pieces, [3, 5, 4, 5, 6])
    lung = [c.pieces[1] for c in a.candidates]
    assert lung[1] == 2  # el pulmón es lo primero que se parte
    assert a.limits[0] == "homogéneo" and a.kcap[0] == 1  # el aire no
    assert a.candidates[2].error.rms_mm < 0.5 * un.error.rms_mm
    assert 0 <= a.recommended < len(a.candidates)
    assert a.best is a.candidates[a.recommended] and a.extra_setup_s == a.best.setup_s
    assert a.i_ev[0] == pytest.approx(85.68, abs=0.01)  # aire: gas, valor de PENELOPE
    assert a.i_ev[3] == pytest.approx(76.71, abs=0.01)  # blando: condensado


def test_determinism():
    ti, den = _synthetic_ct(seed=5)
    a = sd.analyse(ti, den, VOXEL, _infos(), 9)
    b = sd.analyse(ti.copy(), den.copy(), VOXEL, _infos(), 9)
    assert a == b


def test_no_split_for_uniform_tiny_or_locked_tissues():
    rng = np.random.default_rng(1)
    shape = (8, 30, 30)
    ti = np.zeros(shape, dtype=np.int8)
    ti[:, :15, :] = 1
    ti[:, 15:, :15] = 2
    ti[0, 0, :5] = 3  # 5 vóxeles
    den = np.empty(shape)
    den[ti == 0] = 0.3  # una sola densidad
    den[ti == 1] = rng.uniform(1.0, 1.01, (ti == 1).sum())  # poca dispersión
    den[ti == 2] = rng.uniform(0.1, 0.9, (ti == 2).sum())  # mucha, pero bloqueado
    den[ti == 3] = [0.1, 0.9, 0.1, 0.9, 0.5]  # muy pequeño
    infos = [sd.TissueInfo(0, SOFT, density_range=(0.05, 0.9)), sd.TissueInfo(1, SOFT, density_range=(0.99, 1.02)),
             sd.TissueInfo(2, SOFT, density_range=(0.05, 0.9), locked=True),
             sd.TissueInfo(3, SOFT, density_range=(0.05, 0.9))]
    a = sd.analyse(ti, den, (0.2, 0.2, 0.2), infos, 10)
    assert a.kcap == (1, 1, 1, 1)
    assert a.limits == ("homogéneo", "homogéneo", "bloqueado", "trozo pequeño")
    # Bloqueado pero homogéneo: no haría falta partirlo, y se dice eso.
    infos[1] = sd.TissueInfo(1, SOFT, density_range=(0.99, 1.02), locked=True)
    assert sd.analyse(ti, den, (0.2, 0.2, 0.2), infos, 10).limits[1] == "homogéneo"
    assert len(a.candidates) == 1 and a.recommended == 0 and a.extra_setup_s == 0.0
    # el mismo tejido pequeño, con vóxeles grandes (5 cm³ caben en un vóxel), sí se parte
    b = sd.analyse(ti, den, (2.0, 2.0, 2.0), infos, 10)
    assert b.kcap[3] > 1 and b.candidates[1].pieces[3] == 2


def test_absent_void_budget_and_mask():
    ti, den = _synthetic_ct(seed=2)
    ti = np.where(ti == 2, 3, ti).astype(np.int16)  # sin grasa: el tejido 2 queda ausente
    infos = _infos()
    infos[0].void = True  # el aire va al material 0
    a = sd.analyse(ti, den, VOXEL, infos, 5)
    assert a.absent == (2,) and a.voxels[0] == 0 and a.voxels[2] == 0
    assert a.limits[0] == "vacío" and a.limits[2] == "ausente" and a.kcap[0] == a.kcap[2] == 0
    assert a.unsplit.pieces == (0, 1, 0, 1, 1) and a.unsplit.n_materials == 3
    assert [c.n_materials for c in a.candidates] == [3, 4, 5]
    assert a.stopping[0] == 0.0 and a.unsplit.means[0] == () and a.unsplit.cuts[2] == ()
    # el aire vacío no frena: igual que sin aire (tejido −1)
    b = sd.analyse(np.where(ti == 0, -1, ti), den, VOXEL, infos, 5)
    assert b.candidates == a.candidates
    # presupuesto menor que los tejidos presentes: solo «sin partir», avisado
    c = sd.analyse(ti, den, VOXEL, infos, 2)
    assert len(c.candidates) == 1 and c.over_budget and not a.over_budget
    # la máscara saca vóxeles igual que un tejido −1
    mask = np.ones(ti.shape, dtype=bool)
    mask[:, :, :10] = False
    d = sd.analyse(ti, den, VOXEL, infos, 5, mask=mask)
    e = sd.analyse(np.where(mask, ti, -1), den, VOXEL, infos, 5)
    assert d.candidates == e.candidates and d.voxels == e.voxels


def test_stop_mask_and_directions():
    ti, den = _synthetic_ct(seed=3)
    infos = _infos()
    full = sd.analyse(ti, den, VOXEL, infos, 6)
    half = np.zeros(ti.shape, dtype=bool)
    half[:, : ti.shape[1] // 2] = True
    part = sd.analyse(ti, den, VOXEL, infos, 6, stop_mask=half)
    assert 0 < part.unsplit.error.n_stop < full.unsplit.error.n_stop
    xz = sd.analyse(ti, den, VOXEL, infos, 6, ("+x", "-z"))
    assert xz.directions == ("+x", "-z") and set(xz.strides) == {"x", "z"}
    for bad in (("+w",), ("x",), ("+x", "+x"), ()):
        with pytest.raises(ValueError):
            sd.analyse(ti, den, VOXEL, infos, 6, bad)


def test_invalid_input():
    ti, den = _synthetic_ct(shape=(2, 8, 8))
    infos = _infos()
    with pytest.raises(ValueError):
        sd.analyse(ti, den[:, :, :4], VOXEL, infos, 6)
    with pytest.raises(ValueError):
        sd.analyse(ti, den, VOXEL, infos[:3], 6)  # hay vóxeles de los tejidos 3 y 4
    with pytest.raises(ValueError):
        sd.analyse(ti, den, VOXEL, list(reversed(infos)), 6)
    with pytest.raises(ValueError):
        sd.analyse(ti, den, (0.1, 0.0, 0.1), infos, 6)
    with pytest.raises(ValueError):
        sd.analyse(ti.astype(float), den, VOXEL, infos, 6)
    with pytest.raises(ValueError):
        sd.analyse(ti[0], den[0], VOXEL, infos, 6)


def test_recommendation_rule():
    def cand(rms, n=100):
        return sd.Allocation((1,), ((),), ((1.0,),), ((1,),), 0.0, sd.RangeError(0.0, 0.0, rms, n), 0.0)

    assert sd.recommend([cand(10.0), cand(4.0), cand(2.0), cand(1.8), cand(1.0)]) == 2  # 0,2 < 0,3
    assert sd.recommend([cand(10.0), cand(4.0), cand(2.0)]) == 2
    assert sd.recommend([cand(1.0), cand(0.95), cand(0.5)]) == 0  # 0,05 < 0,1 mm
    assert sd.recommend([cand(5.0), cand(6.0)]) == 0  # empeora
    assert sd.recommend([cand(0.0, 0), cand(0.0, 0)]) == 0  # sin puntos de parada


def test_performance_smoke():
    ti, den = _synthetic_ct(shape=(24, 96, 128), seed=4)  # 295 000 vóxeles
    t0 = time.perf_counter()
    a = sd.analyse(ti, den, VOXEL, _infos(), 10)
    assert time.perf_counter() - t0 < 10.0 and len(a.candidates) >= 3


@pytest.mark.skipif(not os.environ.get("ARGUS_SLOW_TESTS"), reason="prueba lenta (ARGUS_SLOW_TESTS=1)")
def test_performance_ten_million_voxels():
    ti, den = _synthetic_ct(shape=(120, 256, 330), seed=4)
    t0 = time.perf_counter()
    a = sd.analyse(ti, den, VOXEL, _infos(), 10)
    dt = time.perf_counter() - t0
    assert dt < 15.0 and len(a.candidates) >= 3
    for name, axis in (("x", 2), ("y", 1)):  # cada dirección recorre como mucho MAX_RAY_VOXELS vóxeles
        sl, _steps = sd._ray_view(ti.shape, axis, sd.MAX_RAY_VOXELS)
        assert a.strides[name] == _steps and np.empty(ti.shape, dtype=np.int8)[sl].size <= sd.MAX_RAY_VOXELS


# -- TAC reales (opcional: ARGUS_CT_SAMPLES = carpeta con */sim/tac.ct y su ficha) ---------------------------

def _samples() -> list[Path]:
    root = os.environ.get("ARGUS_CT_SAMPLES")
    if not root or not Path(root).is_dir():
        pytest.skip("TAC de ejemplo no disponibles (ARGUS_CT_SAMPLES)")
    found = sorted(Path(root).glob("*/sim/tac.ct"))
    if not found:
        pytest.skip(f"No hay */sim/tac.ct en {root}")
    return found


def test_real_thorax_cts():
    """Tres TAC de tórax (LCTSC) importados con schneider5: sin partir, p95 de 13–26 mm; con el pulmón en 3, ~3–4."""
    from argus.pengeom.ct import read_ct_file

    for path in _samples():
        ct = read_ct_file(path)
        card = json.loads(path.with_name(path.name + ".json").read_text(encoding="utf-8"))
        cal = card["calibracion"]
        tissues = cal["tejidos"]
        lut = np.full(100, -1, dtype=np.int16)
        for i, t in enumerate(tissues):
            if t["material"] > 0:
                lut[t["material"]] = i
        starts = [-np.inf if t["hu_desde"] is None else t["hu_desde"] for t in tissues]
        rng = sd.density_ranges(cal["curva"], starts)
        infos = [sd.TissueInfo(i, {int(z): f for z, f in t["composicion"].items()}, None, rng[i], name=t["nombre"])
                 for i, t in enumerate(tissues)]
        a = sd.analyse(lut[ct.material], ct.density, ct.voxel_size, infos, 10)
        lung = next(i for i, t in enumerate(tissues) if t["nombre"].startswith("Pulm"))
        un = a.unsplit.error
        assert 8.0 < un.p95_mm < 40.0, (path, un)
        three = next(c for c in a.candidates if c.pieces[lung] == 3)
        assert three.error.p95_mm < 6.0, (path, three.error)
        assert a.best.error.p95_mm < un.p95_mm / 3
        assert a.elapsed_s < 30.0
