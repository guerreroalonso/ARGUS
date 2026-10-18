"""Moverse por la vista 3D sin perder lo pequeño (0.17.1).

Rafael (18-10-2026): «cuando zoomeas la velocidad de movimiento tiene que ser proporcional al zoom… para cosas
muy chicas pierdo muy rápido el objeto». La cámara giraba alrededor de su objetivo y la rueda la acercaba hacia el
plano del objetivo: algo pequeño delante o detrás de ese plano no se acercaba (la cámara llegaba al plano y se paraba)
y al menor giro se salía de la imagen (se giraba alrededor de un punto lejos de él). Ahora la rueda acerca hacia lo
que hay bajo el cursor, a su profundidad; girar es alrededor del punto cogido y desplazar lo mueve con el ratón."""

import numpy as np
import pytest

from argus.render.scene3d import Camera
from test_track_layer import need_gpu  # noqa: F401 - la prueba con la GPU

W, H = 640, 480


def _pixel(cam, p):
    px, z = cam.project(np.asarray(p, dtype=float)[None, :], W, H)
    return px[0], z[0]


@pytest.mark.parametrize("ortho", [False, True])
def test_orbit_about_keeps_the_grabbed_point_in_its_pixel(ortho):
    cam = Camera(np.array([1.0, -2.0, 3.0]), 80.0, -60.0, 25.0, ortho=ortho)
    p = np.array([10.0, 4.0, -6.0])
    before, _ = _pixel(cam, p)
    for dyaw, dpitch in ((-12.0, 0.0), (0.0, 7.5), (30.0, -40.0), (5.0, 200.0)):  # (el último llega al tope)
        cam.orbit_about(p, dyaw, dpitch)
        after, z = _pixel(cam, p)
        assert np.allclose(after, before, atol=1e-6) and (ortho or z > 0)
    assert cam.pitch == pytest.approx(89.9)


def test_pan_at_a_depth_moves_that_point_with_the_mouse():
    cam = Camera(np.zeros(3), 100.0, -60.0, 25.0)
    fwd = cam.basis()[2]
    near = cam.position + 5.0 * fwd + np.array([0.3, -0.2, 0.1])  # algo pequeño muy cerca de la cámara
    (x0, y0), _ = _pixel(cam, near)
    old = cam.copy()
    old.pan(25.0, -10.0, H)  # antes: a la escala del objetivo, 20 veces más lejos
    (xo, yo), _ = _pixel(old, near)
    assert abs(xo - x0) > 200  # se iba de la imagen
    cam.pan(25.0, -10.0, H, depth=cam.depth_of(near))
    (x1, y1), _ = _pixel(cam, near)
    assert x1 - x0 == pytest.approx(25.0, abs=1e-6) and y1 - y0 == pytest.approx(-10.0, abs=1e-6)


def test_dolly_toward_a_point_never_passes_it_and_keeps_it_in_its_pixel():
    cam = Camera(np.zeros(3), 100.0, -60.0, 25.0)
    p = np.array([18.0, -7.0, 22.0])  # lejos del centro de la imagen y delante del objetivo
    pix0, z0 = _pixel(cam, p)
    for _ in range(40):
        cam.dolly_toward(p, 0.85)
    pix1, z1 = _pixel(cam, p)
    assert np.allclose(pix1, pix0, atol=1e-6)
    assert z1 == pytest.approx(z0 * 0.85 ** 40, rel=1e-9) and z1 > 0
    assert cam.distance == pytest.approx(z1) and cam.depth_of(cam.target) == pytest.approx(cam.distance)
    cam.dolly_toward(p, 1 / 0.85)  # alejarse vuelve por la misma recta
    assert np.allclose(_pixel(cam, p)[0], pix0, atol=1e-6)



def test_the_lines_near_plane_follows_what_is_looked_at():
    """Las trayectorias en la GPU no se dibujaban a menos de 1 µm de la cámara; acercándose con la rueda hacia una
    trayectoria pequeña, desaparecía lo que se miraba. Ahora el plano cercano baja con la distancia (0.17.1)."""
    pytest.importorskip("PySide6")
    from argus.ui.gpu_tracks import GpuTrackLayer, track_near

    assert track_near(100.0) == track_near(1.0) == pytest.approx(1e-4)  # como antes
    assert track_near(0.01) == pytest.approx(1e-6) and track_near(0.0) > 0
    cam = Camera(np.zeros(3), 100.0, -60.0, 25.0)
    for _ in range(90):  # 90 pasos de la rueda hacia un punto: a menos de medio µm de él
        cam.dolly_toward(np.array([0.2, 0.1, -0.3]), 0.85)
    frame = {k: 0 for k in ("uCamPos", "uRight", "uUp", "uFwd")} | {
        "uTan": cam.tan_half(), "uHalf": cam.half_height(), "uOrtho": 0}
    near = GpuTrackLayer.camera_uniforms(frame, W, H)["uNear"]
    assert 1.5 * near < cam.distance < 1.5e-4  # (con 1 µm fijo, ya no se vería)


# -- en la ventana (en la CPU) ---------------------------------------------------------------------------------------

GEO = """Navegación: un cilindro grande y una esfera diminuta lejos del centro
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (  P0)   Plane Z=0
INDICES=( 0, 0, 0, 1, 0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE ( P50)   Plane Z=50
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(+5.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE ( C40)   Cylinder R=40
INDICES=( 1, 1, 0, 0,-1)
X-SCALE=(+4.000000000000000E+01,   0)
Y-SCALE=(+4.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (  SP)   Sphere R=0.5
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+5.000000000000000E-01,   0)
Y-SCALE=(+5.000000000000000E-01,   0)
Z-SCALE=(+5.000000000000000E-01,   0)
X-SHIFT=(+3.000000000000000E+01,   0)
Y-SHIFT=(-2.000000000000000E+01,   0)
Z-SHIFT=(+7.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (CILI)   Cilindro grande
MATERIAL(   1)
SURFACE (  P0), SIDE POINTER=(+1)
SURFACE ( P50), SIDE POINTER=(-1)
SURFACE ( C40), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (ESFE)   Esfera diminuta
MATERIAL(   2)
SURFACE (  SP), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""
SPHERE = np.array([30.0, -20.0, 70.0])


class _Wheel:
    def __init__(self, x, y, steps=1):
        from PySide6.QtCore import QPoint, QPointF

        self._p, self._d = QPointF(float(x), float(y)), QPoint(0, int(120 * steps))

    def position(self):
        return self._p

    def angleDelta(self):  # noqa: N802 - como Qt
        return self._d


class _Mouse:
    def __init__(self, x, y, button=None):
        from PySide6.QtCore import QPointF, Qt

        self._p = QPointF(float(x), float(y))
        self._b = button if button is not None else Qt.MouseButton.LeftButton

    def position(self):
        return self._p

    def button(self):
        return self._b

    def buttons(self):
        return self._b

    def modifiers(self):
        from PySide6.QtCore import Qt

        return Qt.KeyboardModifier.NoModifier


@pytest.fixture
def small_scene(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from argus.render import raytrace
    from argus.session import open_geometry
    from argus.ui.view3d import View3D

    path = tmp_path / "navegacion.geo"
    path.write_text(GEO, encoding="latin-1")
    raytrace.warmup()
    v = View3D(backend="cpu")
    qtbot.addWidget(v)
    v.resize(W, H)
    v.show()
    qtbot.waitExposed(v)
    s = open_geometry(path)
    assert s.ok
    v.set_session(s)
    qtbot.waitUntil(v.is_idle, timeout=30000)
    v.fit_box(np.array([-40.0, -40.0, 0.0]), np.array([40.0, 40.0, 71.0]))
    return v


def _canvas_pixel(v, p):
    px, z = v.camera.project(np.asarray(p, dtype=float)[None, :], v.canvas.width(), v.canvas.height())
    return px[0], z[0]


@pytest.mark.gui
def test_zooming_toward_something_tiny_and_turning_around_it(small_scene):
    v = small_scene
    (sx, sy), _ = _canvas_pixel(v, SPHERE)
    info = v.pick(sx, sy)
    assert info is not None and info["body"] == 2  # la esfera, lejos del centro y por encima del cilindro
    p0 = v._nav_point(sx, sy, translucent=False)  # el punto de su superficie bajo el cursor
    assert p0 is not None and np.linalg.norm(p0 - SPHERE) == pytest.approx(0.5, abs=1e-6)
    (px0, py0), z0 = _canvas_pixel(v, p0)
    for _ in range(30):  # la rueda, hacia la esfera
        v.on_wheel(_Wheel(sx, sy))
    (px1, py1), z1 = _canvas_pixel(v, p0)
    assert abs(px1 - px0) < 0.5 and abs(py1 - py0) < 0.5  # sigue bajo el cursor
    assert z1 == pytest.approx(z0 * 0.85 ** 30, rel=1e-6)  # 130 veces más cerca (antes, hasta el plano del objetivo)
    assert v.pick(sx, sy)["body"] == 2 and v.camera.distance == pytest.approx(z1)
    # Girar arrastrando desde la esfera: gira alrededor del punto cogido y la esfera no se sale de la imagen.
    pivot = v._nav_point(sx, sy)
    (qx0, qy0), _ = _canvas_pixel(v, pivot)
    v.on_press(_Mouse(sx, sy))
    v.on_move(_Mouse(sx + 60, sy + 25))
    v.on_release(_Mouse(sx + 60, sy + 25))
    (qx1, qy1), zq = _canvas_pixel(v, pivot)
    assert abs(qx1 - qx0) < 0.5 and abs(qy1 - qy0) < 0.5 and zq > 0
    assert v.pick(sx, sy) is not None and v.pick(sx, sy)["body"] == 2
    # Desplazar (botón derecho): lo cogido sigue al ratón, a su escala.
    from PySide6.QtCore import Qt

    p = v.pick(sx, sy)["point"]
    (x0, y0), _ = _canvas_pixel(v, p)
    v.on_press(_Mouse(sx, sy, Qt.MouseButton.RightButton))
    v.on_move(_Mouse(sx + 30, sy - 20, Qt.MouseButton.RightButton))
    v.on_release(_Mouse(sx + 30, sy - 20, Qt.MouseButton.RightButton))
    (x3, y3), _ = _canvas_pixel(v, p)
    assert x3 - x0 == pytest.approx(30, abs=0.5) and y3 - y0 == pytest.approx(-20, abs=0.5)


@pytest.mark.gui
def test_with_nothing_under_the_cursor_it_works_as_before(small_scene):
    v = small_scene
    cam0 = v.camera.copy()
    assert v._nav_point(2, 2) is None  # una esquina vacía
    v.on_wheel(_Wheel(2, 2))
    assert v.camera.distance == pytest.approx(cam0.distance * 0.85)
    yaw0 = v.camera.yaw
    v.on_press(_Mouse(2, 2))
    v.on_move(_Mouse(62, 2))
    v.on_release(_Mouse(62, 2))
    assert v.camera.yaw == pytest.approx(yaw0 - 0.4 * 60)


@pytest.mark.gui
def test_zooming_toward_a_tiny_track(small_scene):
    """Una trayectoria diminuta (de 1 µm) dibujada lejos del centro: la rueda acerca hacia ella (el punto de la
    trayectoria más cercano al cursor, a menos de 10 píxeles), aunque detrás haya geometría o no haya nada."""
    from argus.results.data import Tracks

    v = small_scene
    p = np.array([-30.0, 35.0, 65.0])  # fuera del cilindro y por encima de él
    pts = np.array([[*p, 1e6], [*(p + [1e-4, 0.0, 0.0]), 9e5], [*(p + [1e-4, 1e-4, 0.0]), 8e5]])
    v.set_tracks([Tracks(1, pts, np.array([0, 3]))])
    (sx, sy), z0 = _canvas_pixel(v, p)
    assert 0 < sx < v.canvas.width() and 0 < sy < v.canvas.height()
    cx, cy = sx + 4, sy - 3  # el cursor, cerca de la trayectoria
    q = v._nav_point(cx, cy, translucent=False)
    assert q is not None and np.linalg.norm(q - p) < 2e-4
    for _ in range(30):
        v.on_wheel(_Wheel(cx, cy))
    (qx, qy), z1 = _canvas_pixel(v, q)
    assert abs(qx - sx) < 0.5 and abs(qy - sy) < 0.5  # sigue donde estaba, junto al cursor
    assert z1 == pytest.approx(z0 * 0.85 ** 30, rel=1e-3) and v.camera.distance == pytest.approx(z1, rel=1e-3)
    v.set_tracks(None)
    assert v._nav_point(cx, cy, translucent=False) is None or v.pick(cx, cy) is not None  # sin ella, lo de antes


@pytest.mark.gui
def test_the_cascade_tracks_count_with_the_viewers_filters(small_scene):
    """Las trayectorias de la cascada también son «lo que se mira»; las que el visor no enseña (una partícula
    desmarcada), no."""
    from dataclasses import replace

    from argus.render.track_layer import layer_from_showers
    from argus.results.data import Tracks
    from argus.results.shower import reconstruct

    v = small_scene
    p0, p1 = np.array([-30.0, 35.0, 65.0]), np.array([-29.0, 35.0, 65.5])
    t = np.linspace(0.0, 1.0, 11)[:, None]
    line = np.hstack([p0 + t * (p1 - p0), np.linspace(1e6, 5e5, 11)[:, None]])
    v.set_track_layer(layer_from_showers(reconstruct([Tracks(2, line, np.array([0, 11]))])))
    mid = 0.5 * (p0 + p1)
    (sx, sy), _ = _canvas_pixel(v, mid)
    q = v._nav_point(sx, sy, translucent=False)
    assert q is not None and np.linalg.norm(q - mid) < 0.06  # el punto de la línea más cercano al cursor
    pts = v._track_points()
    assert pts is not None and len(pts) == 11 and v._track_points() is pts  # (guardados entre pasos de la rueda)
    v.set_track_style(replace(v.track_style, particles=frozenset({1})))  # sin fotones
    q2 = v._nav_point(sx, sy, translucent=False)
    assert q2 is None or np.linalg.norm(q2 - mid) > 1.0  # (lo de detrás, si hay algo)


@pytest.mark.gui
def test_on_the_gpu_a_tiny_track_is_still_drawn_close_up(qtbot, tmp_path, monkeypatch, need_gpu):  # noqa: F811
    """En la GPU, acercándose con la rueda a una trayectoria de 2 µm hasta tenerla a medio µm, se sigue dibujando
    bajo el cursor; con el plano cercano de antes (1 µm fijo), desaparecía."""
    from argus.render.track_layer import TrackStyle, layer_from_showers, particle_rgb
    from argus.results.data import Tracks
    from argus.results.shower import reconstruct
    from argus.ui import gpu_tracks
    from test_track_layer import _closest, _rgb, _unchanged, _view

    v = _view(qtbot, tmp_path, "gpu")  # la bola de radio 1, vista desde +Z
    qtbot.waitUntil(lambda: v.canvas.gpu is not None, timeout=30000)
    p0, p1 = np.array([0.0, 0.0, 2.0]), np.array([2e-4, 0.0, 2.0])  # entre la bola y la cámara
    t = np.linspace(0.0, 1.0, 5)[:, None]
    line = np.hstack([p0 + t * (p1 - p0), np.full((5, 1), 1e6)])
    v.set_track_layer(layer_from_showers(reconstruct([Tracks(1, line, np.array([0, 5]))])))
    v.fit()
    qtbot.waitUntil(v.is_idle, timeout=60000)
    mid = line[2, :3]
    (sx, sy), _ = _canvas_pixel(v, mid)
    while v.camera.depth_of(mid) > 5e-5:
        v.on_wheel(_Wheel(sx, sy))
    (x, y), z = _canvas_pixel(v, mid)
    assert abs(x - sx) < 0.5 and abs(y - sy) < 0.5 and 0 < z <= 5e-5
    x, y = int(round(x)), int(round(y))
    layer = v.track_layer
    v.set_track_layer(None)
    base = v.grab_image()
    v.set_track_layer(layer)
    img = v.grab_image()
    want = TrackStyle().alpha * np.array(particle_rgb(1)) * 255.0 + (1 - TrackStyle().alpha) * _rgb(base, x, y)
    assert _closest(img, x, y, want) < 70 and v.canvas.track_error is None
    monkeypatch.setattr(gpu_tracks, "track_near", lambda _d: 1.0e-4)  # el de antes
    v.set_track_style(TrackStyle())
    assert _unchanged(base, v.grab_image(), x, y)
    v.shutdown()


@pytest.mark.gui
def test_only_what_is_drawn_can_be_picked(small_scene):
    """Revisión de 0.17.1: lo que se mira tiene que estar dibujado. De las de LTRACK, solo las que caben en la imagen
    (``MAX_TRACK_POINTS``, como al dibujarlas); de la cascada animada, solo lo ya recorrido."""
    from dataclasses import replace

    from argus.render.track_layer import layer_from_showers
    from argus.results.data import Tracks
    from argus.results.shower import reconstruct

    v = small_scene
    a = np.array([[-30.0, 35.0, 65.0, 1e6], [-29.0, 35.0, 65.0, 9e5], [-28.0, 35.0, 65.0, 8e5]])
    b = a + np.array([0.0, -5.0, 0.0, 0.0])
    v.set_tracks([Tracks(1, np.vstack([a, b]), np.array([0, 3, 6]))])
    v.MAX_TRACK_POINTS = 4  # cabe la primera trayectoria entera; la segunda, no
    pts = v._track_points()
    assert len(pts) == 3 and np.allclose(pts, a[:, :3])
    (sx, sy), _ = _canvas_pixel(v, b[1, :3])
    q = v._nav_point(sx, sy, translucent=False)
    assert q is None or np.linalg.norm(q - b[1, :3]) > 1.0  # la que no se dibuja no cuenta
    del v.MAX_TRACK_POINTS  # (el de la clase)
    assert len(v._track_points()) == 6
    v.set_tracks(None)
    # La cascada, animada: antes de que salga la partícula, no está.
    v.set_track_layer(layer_from_showers(reconstruct([Tracks(2, a.copy(), np.array([0, 3]))])))
    (sx, sy), _ = _canvas_pixel(v, a[1, :3])
    assert v._nav_point(sx, sy, translucent=False) is not None
    v.set_track_style(replace(v.track_style, animate=True, time=-1.0))
    pts = v._track_points()
    assert pts is None or len(pts) == 0
    q = v._nav_point(sx, sy, translucent=False)
    assert q is None or np.linalg.norm(q - a[1, :3]) > 1.0
    v.set_track_style(replace(v.track_style, animate=True, time=1e9))
    assert len(v._track_points()) == 3
