"""Trazador de rayos de referencia (CPU) de la vista 3D."""

import numpy as np
import pytest

from argus.pengeom.bounds import estimate_bounds
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.locate import Locator
from argus.pengeom.parser import load_geometry
from argus.render.raytrace import RayTracer
from argus.render.scene3d import Camera
from conftest import CORPUS, MANIFEST

CUBO = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"


def _geo(item):
    path = CORPUS / item["path"]
    g = compile_geometry(load_geometry(path), parinp=item.get("parinp"), base_dir=path.parent)
    assert g.ok
    return g


@pytest.fixture(scope="module")
def cubo():
    return RayTracer(_geo({"path": "usuario/100Mev15-174/cubo-esfera.geo"}))


def test_axis_ray_through_cube(cubo):
    # Cubo (cuerpo 1, material 1) en |x|,|y| <= 250, 0 <= z <= 500 dentro de una esfera de vacío de radio 5000.
    segs = cubo.trace([0, 0, -1000], [0, 0, 1])
    assert [(s.body, s.surface) for s in segs[:4]] == [(2, 0), (1, 4), (2, 3), (3, 5)]
    assert [s.t for s in segs[:4]] == pytest.approx([0, 1000, 1500, 6000])
    assert segs[-1].body == cubo.nbodys + 1  # sale de la envolvente
    stops = cubo.step_stops([0, 0, -1000], [0, 0, 1])
    assert [(s.t, s.body, s.material) for s in stops] == [(pytest.approx(1000), 1, 1)]


def test_ray_starting_on_a_surface_uses_its_direction(cubo):
    up = cubo.trace([0, 0, 0], [0, 0, 1])
    down = cubo.trace([0, 0, 0], [0, 0, -1])
    assert up[0].body == 1 and down[0].body == 2
    assert up[1].t == pytest.approx(500)


def test_all_crossings_reports_same_body(cubo):
    # Por encima del cubo el rayo cruza los planos x = ±250 sin cambiar de cuerpo (vacío).
    a = cubo.trace([-1000, 0, 600], [1, 0, 0])
    b = cubo.trace([-1000, 0, 600], [1, 0, 0], all_crossings=True)
    assert [s.body for s in a] == [2, 3, 4]
    assert [(s.body, s.surface) for s in b[:3]] == [(2, 0), (2, 1), (2, 1)]
    assert [s.t for s in b[1:3]] == pytest.approx([750, 1250])
    assert cubo.step_stops([-1000, 0, 600], [1, 0, 0]) == []  # STEP no se para en el vacío
    c = cubo.trace([-1000, 0, 250], [1, 0, 0])
    assert [(s.body, s.surface) for s in c[:3]] == [(2, 0), (1, 1), (2, 1)]


def test_first_visible_and_clip(cubo):
    t, kb, ks = cubo.first_visible([0, 0, -1000], [0, 0, 1])
    assert (kb, ks) == (1, 4) and t == pytest.approx(1000)
    # Se oculta z < 200 (n = -z, d = -200): se ve la cara del corte.
    t, kb, ks = cubo.first_visible([0, 0, -1000], [0, 0, 1], clip=[(0, 0, -1, -200)])
    assert (kb, ks) == (1, -1) and t == pytest.approx(1200)
    # El corte no toca el cubo: se ve la cara de siempre.
    t, kb, ks = cubo.first_visible([0, 0, -1000], [0, 0, 1], clip=[(0, 0, 1, 2000)])
    assert (kb, ks) == (1, 4)
    # Cuerpo oculto: el rayo no ve nada.
    vis = cubo.default_visibility()
    vis[1] = 0
    assert cubo.first_visible([0, 0, -1000], [0, 0, 1], visible=vis)[1] == 0
    # Todo el cubo oculto por el corte.
    assert cubo.first_visible([0, 0, -1000], [0, 0, 1], clip=[(0, 0, -1, -600)])[1] == 0
    # Dos planos: se oculta la esquina x > 0, z < 200; el rayo por x = 100 ve la cara del segundo plano.
    corner = [(1, 0, 0, 0), (0, 0, -1, -200)]
    t, kb, ks = cubo.first_visible([100, 0, -1000], [0, 0, 1], clip=corner)
    assert (kb, ks) == (1, -2) and t == pytest.approx(1200)
    t, kb, ks = cubo.first_visible([-100, 0, -1000], [0, 0, 1], clip=corner)
    assert (kb, ks) == (1, 4) and t == pytest.approx(1000)


def test_render_image(cubo):
    cam = Camera()
    cam.fit(np.array([-250, -250, 0]), np.array([250, 250, 500]))
    w, h = 40, 30
    py, px = np.mgrid[0:h, 0:w]
    o, d = cam.rays(px.ravel(), py.ravel(), w, h)
    t, kb, ks = cubo.render(o, d.reshape(-1, 3))
    assert t.shape == kb.shape == ks.shape == (w * h,)
    img = kb.reshape(h, w)
    assert img[h // 2, w // 2] == 1  # el centro de la imagen es el cubo
    assert img[0, 0] == 0  # la esquina no ve nada
    hit = kb == 1
    pts = o.reshape(-1, 3)[hit] + t[hit, None] * d.reshape(-1, 3)[hit]
    assert np.all(np.abs(pts[:, :2]) <= 250 + 1e-6) and np.all((pts[:, 2] >= -1e-6) & (pts[:, 2] <= 500 + 1e-6))
    # Cada punto visto está en una cara del cubo (en la superficie por la que entra el rayo).
    on_face = np.isclose(np.abs(pts[:, 0]), 250) | np.isclose(np.abs(pts[:, 1]), 250) \
        | np.isclose(pts[:, 2], 0) | np.isclose(pts[:, 2], 500)
    assert on_face.all()
    # Mismo resultado rayo a rayo (sin paralelismo).
    for i in np.flatnonzero(hit)[::97]:
        assert cubo.first_visible(o.reshape(-1, 3)[i], d.reshape(-1, 3)[i])[1] == 1


def test_normal_faces_the_viewer(cubo):
    n = cubo.normal(4, np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    assert n == pytest.approx([0, 0, -1])
    n = cubo.normal(4, np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, -1.0]))
    assert n == pytest.approx([0, 0, 1])
    p = np.array([0.0, 3000.0, 4000.0])  # sobre la esfera de radio 5000
    n = cubo.normal(5, p, -p / 5000)
    assert n == pytest.approx(p / 5000)


def _random_rays(g, n, seed):
    lo, hi = estimate_bounds(g, locator=Locator(g))
    rng = np.random.default_rng(seed)
    c = 0.5 * (lo + hi)
    o = c + (rng.random((n, 3)) - 0.5) * (hi - lo) * 1.6
    d = rng.normal(size=(n, 3))
    d /= np.linalg.norm(d, axis=1)[:, None]
    return o, d


def test_segments_agree_with_locate():
    """El cuerpo de cada tramo es el que da LOCATE en su punto medio."""
    total = bad = 0
    for item in MANIFEST:
        g = _geo(item)
        rt, loc = RayTracer(g), Locator(g)
        o, d = _random_rays(g, 150, 7)
        pts, dirs, expect = [], [], []
        for k in range(len(o)):
            segs = rt.trace(o[k], d[k])
            for s0, s1 in zip(segs[:-1], segs[1:]):
                if s1.t - s0.t > 1e-9 * max(1.0, s1.t):
                    pts.append(o[k] + 0.5 * (s0.t + s1.t) * d[k])
                    dirs.append(d[k])
                    expect.append(s0.body)
        if pts:
            kb, _ = loc.locate(np.array(pts), np.array(dirs))
            total += len(pts)
            nbad = int(np.sum(kb != np.array(expect)))
            bad += nbad
            assert nbad <= max(2, 0.01 * len(pts)), item["path"]
    assert total > 5000 and bad <= 0.001 * total


@pytest.mark.oracle
def test_step_matches_pengeom(need_probe):
    """Paradas de STEP (paso infinito, sin detectores) iguales a las de PENGEOM.

    Las pocas diferencias que quedan son rayos que pasan a menos de 1e-12 de
    una superficie (esferas tangentes de ``spheres``, láminas de ``mlc``) o
    muy lejos del origen (``tball``), donde el propio PENGEOM no es coherente
    entre STEP y LOCATE.
    """
    from argus.oracle.probe import run_probe

    total = bad = 0
    for item in MANIFEST:
        path = CORPUS / item["path"]
        g = _geo(item)
        rt = RayTracer(g)
        o, d = _random_rays(g, 300, 1)
        res = run_probe(path, parinp=item.get("parinp"), tables=False, rays=np.hstack([o, d]), max_segments=5000)
        nbad = 0
        for k in range(len(o)):
            ours = [(s.t, s.body, s.material) for s in rt.step_stops(o[k], d[k])]
            pr = res.rays[k]
            pen = [(a, b, c) for a, b, c in zip(pr.s, pr.ibody, pr.mat) if a < 1e29]
            same = len(ours) == len(pen) and all(
                b1 == b2 and m1 == m2 and abs(t1 - t2) <= 1e-8 * max(1.0, abs(t2))
                for (t1, b1, m1), (t2, b2, m2) in zip(ours, pen))
            nbad += not same
        assert nbad <= 0.05 * len(o), f"{item['path']}: {nbad} rayos distintos"
        total += len(o)
        bad += nbad
    assert bad <= 0.005 * total, f"{bad} de {total} rayos distintos"


def test_the_startup_warmup_compiles_what_a_ct_uses():
    """Numba compila una versión por cada tipo de matriz: la del arranque (sin CT) tiene que ser la de los CT
    (material de solo lectura), o el primer CT abierto congelaría la ventana unos segundos."""
    from argus.render import raytrace as R

    if not R.HAVE_NUMBA:
        pytest.skip("sin Numba")
    _i, _f, mat = R.ct_tables(None)
    assert not mat.flags.writeable and mat.dtype == np.uint8
