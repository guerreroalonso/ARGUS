"""Vóxeles del CT en los cortes 2D y en la vista 3D (CPU y GPU)."""

import numpy as np
import pytest

from argus.pengeom import ct as C
from argus.pengeom.locate import Locator
from argus.render.colors import build_lut, material_lut
from argus.render.raytrace import RayTracer, default_material_rgba, default_material_visibility
from argus.render.scene3d import Camera, body_rgba, clip_planes, gpu_coefficients, gpu_tree_tables, grid
from argus.render.slice2d import SliceView, colorize, compute_slice, standard_plane

pytest.importorskip("pydicom")

from test_ct_import import _geometry_with_ct, _import  # noqa: E402


@pytest.fixture(scope="module")
def ct_geo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ctview")
    imp, built = _import(tmp)
    folder = tmp / "sim"
    folder.mkdir()
    imp.write(folder, built)
    geo = _geometry_with_ct(folder, built)
    assert geo.ok
    return geo


def test_slices_show_the_voxel_materials(ct_geo):
    geo = ct_geo
    loc = Locator(geo)
    fr = C.ct_frame(geo)
    center = fr.to_lab(np.array(fr.size) / 2)
    for name in ("XY", "XZ", "YZ"):
        k = {"XY": 2, "XZ": 1, "YZ": 0}[name]
        view = SliceView(standard_plane(name, float(center[k])), (0.0, 0.0), 0.02)
        res = compute_slice(loc, view, 160, 120)
        inct = res.body == geo.ct_body
        assert inct.sum() > 1000
        from argus.render.slice2d import slice_points

        pts = slice_points(view, 160, 120)
        mat, den = C.voxel_values(geo, pts, view.plane.normal, mask=inct.ravel())
        assert np.array_equal(res.ctmat, mat.reshape(120, 160))
        assert np.all(res.ctmat[~inct] == -1) and np.all(res.ctmat[inct] > 0)
        img = colorize(res, build_lut(geo), draw_edges=False, mat_lut=material_lut())
        lut = material_lut()
        # Cada vóxel lleva el color de su material, más o menos claro según su densidad.
        for m in np.unique(res.ctmat[inct]):
            sel = res.ctmat == m
            ratio = img[sel][:, :3].astype(float).sum(axis=1) / max(1.0, float(lut[m].astype(float).sum()))
            assert np.all((ratio > 0.6) & (ratio < 1.01))


def _ray_first_non_air(geo, o, d, hidden: set[int], n=120000, span=60.0):
    """Primer punto (por muestreo fino) en un vóxel de material visible."""
    t = np.linspace(0.0, span, n)
    pts = o[None, :] + t[:, None] * d[None, :]
    kb, _ = Locator(geo).locate(pts, d)
    inct = kb == geo.ct_body
    mat, _ = C.voxel_values(geo, pts, d, mask=inct)
    vis = inct & ~np.isin(mat, list(hidden))
    idx = np.flatnonzero(vis)
    return (t[idx[0]], span / n) if idx.size else (None, span / n)


def test_cpu_tracer_finds_the_first_visible_voxel(ct_geo):
    geo = ct_geo
    rt = RayTracer(geo)
    assert rt.has_ct
    fr = C.ct_frame(geo)
    rng = np.random.default_rng(4)
    mv = default_material_visibility()
    mv[2] = 0  # aire (material 2 en este caso) oculto
    c = fr.to_lab(np.array(fr.size) / 2)
    checked = 0
    for _ in range(60):
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        o = c - d * 30.0 + rng.normal(scale=0.5, size=3)
        t, kb, _ks = rt.first_visible(o, d, mat_vis=mv)
        tb, step = _ray_first_non_air(geo, o, d, {0, 2})
        if tb is None:
            assert kb != geo.ct_body
            continue
        checked += 1
        assert kb == geo.ct_body and abs(t - tb) <= step + 1e-9
    assert checked > 30
    # Con todos los materiales ocultos, el CT no se ve; con el aire visible, se ve la cara de la caja.
    none = np.zeros_like(mv)
    o = c - np.array([0.0, 0.0, 30.0])
    d = np.array([0.0, 0.0, 1.0])
    assert rt.first_visible(o, d, mat_vis=none)[1] != geo.ct_body
    t_all, kb_all, _ = rt.first_visible(o, d)
    seg = rt.segment_through(o, d, geo.ct_body)
    assert kb_all == geo.ct_body and t_all == pytest.approx(seg[0])


def test_cpu_colors_with_hidden_and_translucent_materials(ct_geo):
    geo = ct_geo
    rt = RayTracer(geo)
    cam = Camera(target=C.ct_frame(geo).to_lab(np.array(C.ct_frame(geo).size) / 2), distance=15.0)
    h, w = 40, 50
    py, px = np.mgrid[0:h, 0:w]
    o, d = cam.rays(px.ravel(), py.ravel(), w, h)
    cols = body_rgba(geo).astype(float) / 255.0
    mr = default_material_rgba()
    a = rt.render_color(o, d, cols, mat_rgba=mr)
    assert np.all(a[:, 4][a[:, 3] > 0] == geo.ct_body)
    mr[2, 3] = 0.0
    b = rt.render_color(o, d, cols, mat_rgba=mr)
    assert not np.allclose(a[:, :3], b[:, :3])  # sin el aire se ve el interior
    mr[1:, 3] = 90 / 255.0  # todo translúcido: se acumulan varias capas
    c = rt.render_color(o, d, cols, mat_rgba=mr)
    hit = c[:, 3] > 0
    assert hit.any() and np.all(c[hit, 3] < 0.99 + 1e-9)
    # Selección del CT: se tiñe.
    s = rt.render_color(o, d, cols, selected=geo.ct_body, mat_rgba=default_material_rgba())
    assert not np.allclose(s[:, :3], a[:, :3])


def _voxel_plane(geo, axis: int, frac: float = 0.5) -> float:
    """Coordenada (cm) de un plano entre vóxeles del CT (sin giro) cerca de ``frac`` de la caja."""
    fr = C.ct_frame(geo)
    n = fr.shape[axis]
    j = max(1, min(n - 1, int(round(frac * n))))
    return float(fr.shift[axis] + j * fr.size[axis] / n)


def test_the_voxel_under_the_cursor_is_the_one_the_ray_enters(ct_geo):
    geo = ct_geo
    fr = C.ct_frame(geo)
    assert np.allclose(fr.rotation, np.eye(3))
    c = fr.to_lab(np.array(fr.size) / 2)
    x = _voxel_plane(geo, 0)
    p = np.array([x, c[1], c[2]])  # en la cara entre dos vóxeles
    fwd = C.voxel_at(geo, p, [1.0, 0.0, 0.0], nudge=1e-6)
    back = C.voxel_at(geo, p, [-1.0, 0.0, 0.0], nudge=1e-6)
    assert fwd[0] == back[0] + 1 and fwd[1:3] == back[1:3]


def test_a_cut_on_a_voxel_plane_is_not_speckled(ct_geo):
    """Con el corte justo en un plano entre vóxeles (lo habitual), la cara del corte es uniforme."""
    from argus.render.scene3d import key_light

    geo = ct_geo
    rt = RayTracer(geo)
    fr = C.ct_frame(geo)
    target = fr.to_lab(np.array(fr.size) / 2)
    cam = Camera(target=target, distance=14.0, yaw=50, pitch=30)  # desde +y: se ve la cara del corte
    w, h = 90, 70
    py, px = np.mgrid[0:h, 0:w]
    o, d = cam.rays(px.ravel(), py.ravel(), w, h)
    cols = body_rgba(geo).astype(float) / 255.0
    y = _voxel_plane(geo, 1)
    imgs = []
    for dy in (0.0, -1e-7):  # justo en el plano y un poco antes (el mismo vóxel): tiene que salir lo mismo
        clip = clip_planes("y", np.array([target[0], y + dy, target[2]]))
        imgs.append(rt.render_color(o, d, cols, 0, list(clip), key_light(cam), mat_rgba=default_material_rgba()))
    diff = np.abs(imgs[0][:, :3] - imgs[1][:, :3]).max(axis=1)
    assert (imgs[0][:, 4] == geo.ct_body).sum() > 500
    assert np.mean(diff > 0.02) <= 0.002


@pytest.mark.gui
def test_detector_and_source_tint_reach_the_voxels(qtbot, ct_geo):
    """El CT resaltado (cuerpo de un detector o cuerpo activo de la fuente) se tiñe también en los vóxeles."""
    from argus.session import session_from_document
    from argus.ui.view3d import View3D

    v = View3D()
    qtbot.addWidget(v)
    v.set_session(session_from_document(ct_geo.document.path, ct_geo.document, [], None, ct_geo))
    before = v.material_rgba().copy()
    v.set_highlight({ct_geo.ct_body})
    after = v.material_rgba()
    assert not np.allclose(before[1:10, :3], after[1:10, :3])
    assert np.array_equal(before[:, 3], after[:, 3])  # la visibilidad no cambia


# -- GPU ----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gpu(qapp):
    from argus.ui.gpu_tracer import OffscreenGpu, probe_gl

    ok, why = probe_gl()
    if not ok:
        pytest.skip(f"Sin OpenGL 4.1: {why}")
    env = OffscreenGpu()
    yield env
    env.close()


@pytest.mark.gui
@pytest.mark.parametrize("clip_axis,ortho", [(None, False), ("y", False), (None, True), ("y-voxel", True),
                                             ("y-voxel", False)])
def test_gpu_voxels_match_cpu(gpu, ct_geo, clip_axis, ortho):
    """Perspectiva y ortográfica (los vóxeles delante del objetivo también), y el corte en un plano de vóxeles."""
    from argus.render.scene3d import key_light
    from argus.ui.gpu_tracer import GpuTracer, id_image

    geo = ct_geo
    rt = RayTracer(geo)
    fr = C.ct_frame(geo)
    target = fr.to_lab(np.array(fr.size) / 2)
    cam = Camera(target=target, distance=14.0, yaw=-50, pitch=30, ortho=ortho, ortho_back=60.0)
    if clip_axis == "y-voxel":
        target = np.array([target[0], _voxel_plane(geo, 1), target[2]])
        clip_axis = "y"
    w, h = 96, 72
    mr = default_material_rgba()
    mr[2, 3] = 0.0
    mr[3, 3] = 90 / 255.0
    matcol = np.zeros((1, 128, 4), np.uint8)
    matcol[0, :100] = np.rint(mr * 255).astype(np.uint8)
    gpu.current()
    g = gpu.tracer
    origin = np.asarray(cam.target, dtype=float)
    g.set_tree(gpu_tree_tables(rt.t, rt.nbodys), rt.nbodys)
    g.upload("coef", gpu_coefficients(rt.t, origin))
    cols = body_rgba(geo)
    g.upload("color", grid(cols, 4, np.uint8))
    g.upload("matcolor", matcol)
    g.set_ct(geo, origin)
    clip = clip_planes(clip_axis, target) if clip_axis else ()
    u = GpuTracer.frame_uniforms(cam, w, h, origin, 1e-5 * cam.distance, clip=clip, mode=1)
    ids = id_image(g.render_image(u, w, h))
    py, px = np.mgrid[0:h, 0:w]
    o, d = cam.rays(px.ravel(), py.ravel(), w, h)
    vis = (np.rint(mr[:, 3] * 255) > 0).astype(np.int64)
    cpu = rt.render(o, d, clip=list(clip), mat_vis=vis)[1].reshape(h, w)
    assert (cpu == geo.ct_body).sum() > 500
    assert int(np.sum(ids != cpu)) <= 0.005 * w * h
    u0 = GpuTracer.frame_uniforms(cam, w, h, origin, 1e-5 * cam.distance, clip=clip, mode=0)
    img = g.render_image(u0, w, h)[..., :3].astype(int)
    res = rt.render_color(o, d, cols.astype(float) / 255.0, 0, list(clip), key_light(cam),
                          mat_rgba=np.rint(mr * 255) / 255.0)
    from argus.render.scene3d import BACKGROUND

    ny = 1.0 - 2.0 * (py.ravel() + 0.5) / h
    b0, b1 = np.array(BACKGROUND[0]), np.array(BACKGROUND[1])
    bg = b0[None, :] + (b1 - b0)[None, :] * (0.5 * (ny + 1.0))[:, None]
    ref = (np.clip(res[:, :3] + (1.0 - res[:, 3:4]) * bg, 0, 1) * 255 + 0.5).astype(int).reshape(h, w, 3)
    diff = np.abs(img - ref).max(axis=2)
    assert np.mean(diff > 12) <= 0.01


@pytest.mark.gui
@pytest.mark.parametrize("backend", ["cpu", "gpu"])
def test_view3d_shows_and_picks_voxels(qtbot, ct_geo, backend):
    """La vista 3D (lienzos de la CPU y de la GPU) dibuja los vóxeles, los elige con el ratón y oculta materiales."""
    from argus.session import session_from_document
    from argus.ui.view3d import HIDDEN, View3D

    if backend == "gpu":
        from argus.ui.gpu_tracer import probe_gl

        ok, why = probe_gl()
        if not ok:
            pytest.skip(f"Sin OpenGL 4.1: {why}")
    v = View3D()
    qtbot.addWidget(v)
    v.resize(500, 400)
    v.show()
    qtbot.waitExposed(v)
    v.use_backend(backend)
    s = session_from_document(ct_geo.document.path, ct_geo.document, geo=ct_geo)
    v.set_session(s)
    v.set_material_visibility({2: HIDDEN})  # el aire del CT
    qtbot.waitUntil(v.is_idle, timeout=60000)
    cw, ch = v.canvas.width(), v.canvas.height()
    info = v.pick(cw / 2, ch / 2)
    assert info is not None and info["body"] == ct_geo.ct_body
    v.set_material_visibility({m: HIDDEN for m in range(1, 10)})
    assert v.pick(cw / 2, ch / 2) is None
    v.show_all()
    qtbot.waitUntil(v.is_idle, timeout=60000)
    img = v.canvas.grabFramebuffer() if backend == "gpu" else None
    if img is None:
        arr = v.canvas._img
        assert arr[arr.shape[0] // 2, arr.shape[1] // 2].tolist() != arr[2, 2].tolist()
    else:
        assert img.pixelColor(img.width() // 2, img.height() // 2) != img.pixelColor(2, 2)
    v.shutdown()


@pytest.mark.gui
def test_a_ct_larger_than_the_3d_texture_limit_is_shown_coarser(gpu, ct_geo, monkeypatch):
    """Más vóxeles por eje que GL_MAX_3D_TEXTURE_SIZE: la vista 3D lo muestra con menos resolución (no desaparece)."""
    from argus.ui.gpu_tracer import GpuTracer, id_image

    geo = ct_geo
    rt = RayTracer(geo)
    fr = C.ct_frame(geo)
    target = fr.to_lab(np.array(fr.size) / 2)
    cam = Camera(target=target, distance=14.0, yaw=-50, pitch=30)
    w, h = 64, 48
    gpu.current()
    g = gpu.tracer
    monkeypatch.setattr(GpuTracer, "_max_3d", lambda self: 8)  # el CT de prueba tiene 16-18 vóxeles por eje
    g._ct_key = None
    origin = np.asarray(cam.target, dtype=float)
    g.set_tree(gpu_tree_tables(rt.t, rt.nbodys), rt.nbodys)
    g.upload("coef", gpu_coefficients(rt.t, origin))
    g.upload("color", grid(body_rgba(geo), 4, np.uint8))
    matcol = np.zeros((1, 128, 4), np.uint8)
    matcol[0, :100] = np.rint(default_material_rgba() * 255).astype(np.uint8)
    g.upload("matcolor", matcol)
    g.set_ct(geo, origin)
    assert max(g.ct_factor) >= 2 and all(-(-n // k) <= 8 for n, k in zip(fr.shape, g.ct_factor))
    u = GpuTracer.frame_uniforms(cam, w, h, origin, 1e-5 * cam.distance, mode=1)
    ids = id_image(g.render_image(u, w, h))
    assert (ids == geo.ct_body).sum() > 200
    monkeypatch.undo()
    g._ct_key = None
    g.set_ct(geo, origin)  # vuelve a la resolución completa
    assert g.ct_factor == (1, 1, 1)
