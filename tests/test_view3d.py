"""Vista 3D (F3): cámara, datos para la GPU, trazado en color y widget."""

import math

import numpy as np
import pytest

from argus.pengeom.bounds import estimate_bounds
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.locate import Locator
from argus.pengeom.parser import load_geometry
from argus.render.raytrace import RayTracer
from argus.render.scene3d import (
    HIDDEN,
    OPAQUE,
    TRANSLUCENT,
    Camera,
    body_rgba,
    clip_planes,
    describe_clip,
    gpu_coefficients,
    gpu_tree_tables,
    ortho_back,
    surface_centers,
    translated_coefficients,
    visibility_array,
    visible_bounds,
)
from conftest import CORPUS, MANIFEST, MANIFEST_IDS

CASE = CORPUS / "usuario/100Mev15-174"


def _geo(item):
    path = CORPUS / item["path"]
    g = compile_geometry(load_geometry(path), parinp=item.get("parinp"), base_dir=path.parent)
    assert g.ok
    return g


@pytest.fixture(scope="module")
def cubo():
    return RayTracer(_geo({"path": "usuario/100Mev15-174/cubo-esfera.geo"}))


# -- cámara -------------------------------------------------------------------------

@pytest.mark.parametrize("ortho", [False, True])
def test_camera_rays_project_back_to_their_pixel(ortho):
    cam = Camera(target=np.array([1.0, -2.0, 3.0]), distance=50.0, yaw=37.0, pitch=-20.0, ortho=ortho,
                 ortho_back=400.0)
    w, h = 120, 80
    px = np.array([0, 17, 60, 119])
    py = np.array([0, 40, 11, 79])
    o, d = cam.rays(px, py, w, h)
    for t in (5.0, 80.0):
        pts = o + t * d
        pix, depth = cam.project(pts, w, h)
        assert pix == pytest.approx(np.stack([px + 0.5, py + 0.5], axis=1), abs=1e-6)
        assert np.all(depth > 0)
    ro, rd, dist = cam.rays_to(o + 30.0 * d)
    assert rd == pytest.approx(d, abs=1e-9)
    assert (ro + dist[:, None] * rd) == pytest.approx(o + 30.0 * d)
    if ortho:
        assert np.allclose(d, d[0]) and dist == pytest.approx(np.full(4, 30.0))


def test_camera_fit_orbit_pan_zoom():
    cam = Camera()
    lo, hi = np.array([-1.0, -2.0, 0.0]), np.array([3.0, 2.0, 10.0])
    cam.fit(lo, hi, aspect=1.5)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    pix, depth = cam.project(corners, 300, 200)
    assert np.all(depth > 0) and np.all((pix >= 0) & (pix <= [300, 200]))
    right, up, fwd = cam.basis()
    assert np.dot(right, up) == pytest.approx(0, abs=1e-12) and up[2] > 0  # Z hacia arriba
    cam.orbit(0, 500)
    assert cam.pitch == pytest.approx(89.9)
    t0 = cam.target.copy()
    cam.pan(10, 0, 200)  # arrastrar la imagen a la derecha: el objetivo va a la izquierda
    assert (cam.target - t0) @ cam.basis()[0] < 0
    d0 = cam.distance
    cam.zoom(0.5)
    assert cam.distance == pytest.approx(0.5 * d0)


def test_clip_planes():
    assert clip_planes("none", [0, 0, 0]) == []
    assert clip_planes("x", [2.5, 0, 0]) == [(1.0, 0.0, 0.0, 2.5)]
    assert clip_planes("x", [2.5, 0, 0], invert=True) == [(-1.0, -0.0, -0.0, -2.5)]
    corner = clip_planes("corner", [1, 2, 3])
    assert len(corner) == 3 and corner[2] == (0.0, 0.0, 1.0, 3.0)
    sl = clip_planes("slice", [0, 0, 0], slice_normal=[0, 2, 0], slice_origin=[0, -4, 0])
    assert sl == [(0.0, 1.0, 0.0, -4.0)]
    assert describe_clip(clip_planes("z", [0, 0, 7])) == "oculto: z > 7 cm"
    assert describe_clip(clip_planes("y", [0, 1, 0], invert=True)) == "oculto: y < 1 cm"


# -- datos para la GPU ----------------------------------------------------------------------

def _f(coef, p):
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = coef.T
    x, y, z = p.T
    return axx * x * x + axy * x * y + axz * x * z + ayy * y * y + ayz * y * z + azz * z * z + ax * x + ay * y \
        + az * z + a0


@pytest.mark.parametrize("item", MANIFEST, ids=MANIFEST_IDS)
def test_centered_coefficients_describe_the_same_surfaces(item):
    g = _geo(item)
    rt = RayTracer(g)
    raw = rt.t["coef"][1:]
    centers = surface_centers(raw, rt.t["kplane"][1:])
    lo, hi = estimate_bounds(g, locator=Locator(g))
    origin = 0.5 * (lo + hi)  # origen flotante (el objetivo de la cámara)
    tex = gpu_coefficients(rt.t, origin, centers).reshape(-1, 16)[1:raw.shape[0] + 1]
    coef = tex[:, 0:10].astype(float)
    assert np.all(np.max(np.abs(coef), axis=1) <= 1.0 + 1e-6)
    rng = np.random.default_rng(3)
    p = rng.uniform(lo, hi, size=(raw.shape[0], 3))
    centered = translated_coefficients(raw, centers)
    scale = np.max(np.abs(centered), axis=1)
    scale[scale == 0] = 1.0
    f_ref = _f(centered, p - centers) / scale  # float64 (con los coeficientes sin centrar habría cancelaciones)
    f_new = _f(coef, (p - origin) - tex[:, 12:15].astype(float))  # lo que evalúa el shader (datos en float32)
    ok = np.abs(f_ref - f_new) <= 1e-4 * (1.0 + np.abs(f_ref))
    assert ok.mean() > 0.97, item["path"]


def test_translated_coefficients_identity():
    rng = np.random.default_rng(5)
    coef = rng.normal(size=(20, 10))
    o = rng.normal(size=(20, 3)) * 3
    r = rng.normal(size=(20, 3)) * 2
    assert _f(translated_coefficients(coef, o), r) == pytest.approx(_f(coef, r + o))
    assert _f(translated_coefficients(coef, o[0]), r) == pytest.approx(_f(coef, r + o[0]))


def test_tree_tables_layout(cubo):
    tabs = gpu_tree_tables(cubo.t, cubo.nbodys)
    body = tabs["body"].reshape(-1, 4)
    nb = cubo.nbodys
    # Fuera de la envolvente se buscan los cortes con las superficies de la raíz.
    assert tuple(body[2 * (nb + 1)][2:4]) == tuple(body[2 * nb][2:4])
    cons = tabs["cons"].ravel()
    kb = 1
    c0, c1 = body[2 * kb][0], body[2 * kb][1]
    assert {1, 2, 3, 4} <= {int(v) >> 2 for v in cons[c0:c1]}  # las caras del cubo (y la envolvente)
    assert {int(v) & 3 for v in cons[c0:c1]} <= {1, 2}


def test_colors_and_visibility():
    g = compile_geometry(load_geometry(CASE / "prueba.geo"))
    rgba = body_rgba(g, visibility={2: TRANSLUCENT}, hidden_bodies={1})
    vis = visibility_array(g, {2: HIDDEN})
    for b in g.bodies:
        if b.material == 0 or b.origin == "enclosure":
            assert rgba[b.index, 3] == HIDDEN and vis[b.index] == 0
        elif b.index == 1:
            assert rgba[b.index, 3] == HIDDEN
        elif b.material == 2:
            assert rgba[b.index, 3] == TRANSLUCENT and vis[b.index] == 0
        else:
            assert rgba[b.index, 3] == OPAQUE and vis[b.index] == 1


def test_visible_bounds_ignore_void_bodies(cubo):
    lo, hi = estimate_bounds(cubo.geo, locator=Locator(cubo.geo))
    assert hi[2] - lo[2] > 5000  # la esfera de vacío de radio 5000
    vlo, vhi = visible_bounds(cubo, lo, hi)
    assert vlo == pytest.approx([-250, -250, 0], abs=150) and vhi == pytest.approx([250, 250, 500], abs=150)


def test_render_color_layers(cubo):
    o = np.array([[0.0, 0.0, -1000.0], [2000.0, 0.0, -1000.0]])
    d = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]])
    cols = body_rgba(cubo.geo).astype(float) / 255.0
    out = cubo.render_color(o, d, cols)
    assert out[0, 4] == 1 and out[0, 3] == pytest.approx(1.0)  # opaco
    assert out[1, 4] == 0 and out[1, 3] == 0.0  # no ve nada
    cols[1, 3] = TRANSLUCENT / 255.0
    out_t = cubo.render_color(o, d, cols)
    # Translúcido: dos capas (entrada y ninguna más: el cubo es el único cuerpo visible) -> alfa < 1.
    assert out_t[0, 4] == 1 and 0.3 < out_t[0, 3] < 0.99
    out_c = cubo.render_color(o, d, body_rgba(cubo.geo).astype(float) / 255.0, clip=[(0, 0, -1, -200)])
    assert out_c[0, 5] != out[0, 5]  # la cara del corte tiene otra huella (frontera)


# -- widget (en la CPU si no hay OpenGL) ------------------------------------------------------

@pytest.fixture
def view3d(qtbot):
    from argus.render import raytrace
    from argus.ui.view3d import View3D

    raytrace.warmup()
    v = View3D()
    qtbot.addWidget(v)
    v.resize(640, 480)
    v.show()
    qtbot.waitExposed(v)
    return v


@pytest.mark.gui
def test_view3d_pick_clip_and_visibility(qtbot, view3d):
    from argus.penhan import source_edit as se
    from argus.session import open_case

    case = open_case(CASE / "maniqui.in")
    v = view3d
    v.set_session(case.session)
    m = case.input_doc.model
    v.set_source(m.source, se.active_bodies(case.input_doc, case.session.geo), m.grids)
    qtbot.waitUntil(v.is_idle, timeout=30000)
    cw, ch = v.canvas.width(), v.canvas.height()
    info = v.pick(cw / 2, ch / 2)
    assert info is not None and info["body"] == 1 and info["mat"] == 1
    assert np.all(np.abs(info["point"][:2]) <= 250 + 1e-6)
    picked = []
    v.bodyPicked.connect(picked.append)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(v.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(cw // 2, ch // 2))
    assert picked == [1]
    # Menú contextual: la vista emite el punto 3D y el cuerpo.
    menus = []
    v.contextRequested.connect(lambda gp, inf: menus.append(inf))
    QTest.mouseClick(v.canvas, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(cw // 2, ch // 2))
    assert menus and menus[0]["body"] == 1
    # Arrastrar gira la cámara.
    yaw0 = v.camera.yaw
    QTest.mousePress(v.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(100, 100))
    QTest.mouseMove(v.canvas, QPoint(160, 100))
    QTest.mouseRelease(v.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(160, 100))
    assert v.camera.yaw == pytest.approx(yaw0 - 0.4 * 60)
    assert picked == [1]  # un arrastre no selecciona
    v.set_view("+Z")
    v.set_clip("z", [0, 0, 250])
    qtbot.waitUntil(v.is_idle, timeout=30000)
    info = v.pick(cw / 2, ch / 2)
    assert info["body"] == 1 and info["surface"] == -1 and info["point"][2] == pytest.approx(250)
    v.set_material_visibility({1: HIDDEN})
    assert v.pick(cw / 2, ch / 2) is None
    assert v.act_show_all.isVisible()
    v.show_all()
    assert v.pick(cw / 2, ch / 2)["body"] == 1
    v.set_body_hidden(1, True)
    assert v.pick(cw / 2, ch / 2) is None
    v.show_all()
    # La fuente (debajo del cubo, z = -100) queda tapada desde arriba y se ve desde abajo.
    pos = np.asarray(m.source.position, dtype=float)
    assert v._occluded(pos[None, :])[0]
    v.set_clip("none")
    v.set_view("-Z")
    assert not v._occluded(pos[None, :])[0]
    v.grab()  # dibuja las superposiciones sin errores


@pytest.mark.gui
def test_view3d_cpu_backend_renders(qtbot, view3d):
    from argus.session import open_geometry

    v = view3d
    v.use_backend("cpu")
    assert v.backend == "CPU"
    v.set_session(open_geometry(CASE / "cubo-esfera.geo"))
    qtbot.waitUntil(v.is_idle, timeout=30000)
    img = v.canvas._img
    h, w = img.shape[:2]
    assert img[h // 2, w // 2].tolist() != img[2, 2].tolist()  # el cubo no es el fondo
    c = img[h // 2, w // 2].astype(int)
    assert c[2] > c[0]  # material 1: azul


# -- GPU (se omite si no hay OpenGL 4.1: pantalla virtual sin GL, escritorio remoto...) ----------

@pytest.fixture(scope="module")
def gpu(qapp):
    from argus.ui.gpu_tracer import OffscreenGpu, probe_gl

    ok, why = probe_gl()
    if not ok:
        pytest.skip(f"Sin OpenGL 4.1: {why}")
    env = OffscreenGpu()
    yield env
    env.close()


def _gpu_ids(env, rt, cam, w, h, clip=()):
    from argus.render.scene3d import grid
    from argus.ui.gpu_tracer import GpuTracer, id_image

    env.current()
    gpu = env.tracer
    origin = np.asarray(cam.target, dtype=float)
    gpu.set_tree(gpu_tree_tables(rt.t, rt.nbodys), rt.nbodys)
    gpu.upload("coef", gpu_coefficients(rt.t, origin))
    gpu.upload("color", grid(body_rgba(rt.geo), 4, np.uint8))
    u = GpuTracer.frame_uniforms(cam, w, h, origin, 1e-5 * cam.distance, clip=clip, mode=1)
    return id_image(gpu.render_image(u, w, h))


def _cpu_ids(rt, cam, w, h, clip=()):
    py, px = np.mgrid[0:h, 0:w]
    o, d = cam.rays(px.ravel(), py.ravel(), w, h)
    return rt.render(o.reshape(-1, 3), d.reshape(-1, 3), clip=list(clip))[1].reshape(h, w)


@pytest.mark.gui
def test_gpu_matches_cpu_on_the_corpus(gpu):
    """El *shader* ve los mismos cuerpos que el trazador de referencia (salvo en bordes y tangencias)."""
    w, h = 96, 72
    total = bad = 0
    for item in MANIFEST:
        g = _geo(item)
        rt = RayTracer(g)
        lo, hi = estimate_bounds(g, locator=Locator(g))
        cam = Camera()
        vlo, vhi = visible_bounds(rt, lo, hi)
        if item["path"] == "pengeomjar/tshell":
            # Capa de 0.1 a 10⁶ del origen: en float32 solo se resuelve de cerca (origen flotante), y en
            # ortográfica, mirando desde fuera (los rayos salen justo delante de lo visible).
            cam.target, cam.distance, cam.pitch = np.array([0.0, 0.0, 1e6]), 5.0, 80.0
        else:
            cam.fit(vlo, vhi, aspect=w / h)
        cam.ortho_back = ortho_back(cam, vlo, vhi)
        for ortho in (False, True):
            cam.ortho = ortho
            # Con el corte, en tshell se vería la cara opuesta de la capa, a 2·10⁶: fuera del alcance de float32.
            clip = clip_planes("x", cam.target) if ortho and item["path"] != "pengeomjar/tshell" else ()
            a = _gpu_ids(gpu, rt, cam, w, h, clip)
            b = _cpu_ids(rt, cam, w, h, clip)
            n = int(np.sum(a != b))
            assert n <= 0.01 * w * h, f"{item['path']} ortho={ortho}: {n} píxeles distintos"
            total += w * h
            bad += n
    assert bad <= 0.002 * total


@pytest.mark.gui
def test_gpu_precision_far_from_origin(gpu):
    """Bola de 1 cm a 5·10⁶ cm (tball): con el origen flotante se ve igual que en float64."""
    g = _geo({"path": "pengeomjar/tball"})
    rt = RayTracer(g)
    lo, hi = estimate_bounds(g, locator=Locator(g))
    cam = Camera()
    cam.fit(*visible_bounds(rt, lo, hi), aspect=1.0)
    assert np.linalg.norm(cam.target) > 1e6 and cam.distance < 100
    a = _gpu_ids(gpu, rt, cam, 64, 64)
    b = _cpu_ids(rt, cam, 64, 64)
    assert np.count_nonzero(b) > 500 and np.sum(a != b) <= 20
    assert math.isfinite(cam.distance)


@pytest.mark.gui
def test_gpu_textures_are_freed_when_the_context_goes_first(qtbot, gpu):
    """Si se borra la ventana sin cerrarla (sin release_gl), las texturas se liberan al destruirse el contexto.

    Una QOpenGLTexture que sobrevive a su contexto guarda un puntero a él y su destructor lo usa si hay otro
    contexto activo: fallos de segmentación al azar cuando el recolector de Python la liberaba más tarde.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QMainWindow

    from argus.ui.gpu_tracer import OffscreenGpu
    from argus.ui.view3d import View3D

    w = QMainWindow()
    v = View3D()
    w.setCentralWidget(v)
    v.use_backend("gpu")
    w.resize(300, 200)
    w.show()
    qtbot.waitExposed(w)
    qtbot.waitUntil(lambda: v.canvas.gpu is not None)
    tracer = v.canvas.gpu
    assert tracer.textures and tracer.ct_tex is not None
    gpu.current()  # otro contexto activo, como cuando fallaba
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert not tracer.textures and tracer.ct_tex is None
    # Lo mismo con un contexto fuera de pantalla que se libera sin close().
    env = OffscreenGpu()
    tr = env.tracer
    del env
    assert not tr.textures and tr.ct_tex is None
