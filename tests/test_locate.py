import numpy as np
import pytest

from argus.pengeom.bounds import estimate_bounds
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.locate import Locator
from argus.pengeom.locate_fast import HAVE_NUMBA
from argus.pengeom.parser import load_geometry
from conftest import CORPUS, MANIFEST, MANIFEST_IDS

BACKENDS = ["numpy"] + (["numba"] if HAVE_NUMBA else [])


def _geo(item):
    g = compile_geometry(load_geometry(CORPUS / item["path"]), parinp=item.get("parinp"))
    assert g.ok
    return g


def test_locate_cubo_esfera():
    g = _geo({"path": "usuario/100Mev15-174/cubo-esfera.geo"})
    loc = Locator(g)
    pts = np.array([[0, 0, 250], [0, 0, 1000], [0, 0, 6000], [0, 0, 2e7]], dtype=float)
    ib, mat = loc.locate(pts)
    assert ib.tolist() == [1, 2, 3, 4]
    assert mat.tolist() == [1, 0, 0, 0]


def test_on_surface_point_uses_direction():
    g = _geo({"path": "usuario/100Mev15-174/cubo-esfera.geo"})
    loc = Locator(g)
    p = np.array([[0.0, 0.0, 0.0]])  # sobre el plano z=0 (cara inferior del cubo)
    ib_up, _ = loc.locate(p, np.array([0.0, 0.0, 1.0]))
    ib_down, _ = loc.locate(p, np.array([0.0, 0.0, -1.0]))
    assert ib_up[0] == 1 and ib_down[0] == 2


def _sample(g, loc, n, rng):
    lo, hi = estimate_bounds(g, locator=loc)
    pts = rng.uniform(lo, hi, size=(n, 3))
    d = rng.normal(size=(n, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    return np.hstack([pts, d])


def _slice_points(g, loc, rng):
    """Puntos en planos que contienen superficies (casos límite de LOCATE)."""
    lo, hi = estimate_bounds(g, locator=loc)
    c = 0.5 * (lo + hi)
    xs = np.linspace(lo[0], hi[0], 60)
    ys = np.linspace(lo[1], hi[1], 60)
    zs = np.array([c[2], 0.0])
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
    pts = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)
    d = np.tile([0.0, 0.0, 1.0], (pts.shape[0], 1))
    d[::2] = [0.0, 0.0, -1.0]
    return np.hstack([pts, d])


@pytest.mark.parametrize("item", MANIFEST, ids=MANIFEST_IDS)
def test_backends_agree(item):
    if len(BACKENDS) < 2:
        pytest.skip("Numba no instalado")
    g = _geo(item)
    ln, lf = Locator(g, "numpy"), Locator(g, "numba")
    rng = np.random.default_rng(7)
    pts = np.vstack([_sample(g, ln, 20000, rng), _slice_points(g, ln, rng)])
    a = ln.locate(pts[:, :3], pts[:, 3:])
    b = lf.locate(pts[:, :3], pts[:, 3:])
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


@pytest.mark.oracle
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("item", MANIFEST, ids=MANIFEST_IDS)
def test_locate_matches_pengeom(item, backend, need_probe):
    from argus.oracle.probe import run_probe

    g = _geo(item)
    loc = Locator(g, backend)
    rng = np.random.default_rng(2024)
    pts = np.vstack([_sample(g, loc, 5000, rng), _slice_points(g, loc, rng)])
    res = run_probe(CORPUS / item["path"], parinp=item.get("parinp"), points=pts, tables=False)
    ib, mat = loc.locate(pts[:, :3], pts[:, 3:])
    bad = np.nonzero(ib != res.locate_body)[0]
    assert bad.size == 0, (f"{bad.size} puntos distintos; p. ej. {pts[bad[0]]}: "
                           f"{ib[bad[0]]} vs {res.locate_body[bad[0]]}")
    assert np.array_equal(mat, res.locate_mat)
