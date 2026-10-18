"""Capa de trayectorias de la cascada (0.13.0): datos, estilo y dibujo sobre la geometría (sin GPU y en la GPU)."""

import numpy as np
import pytest

from argus.render.track_layer import (
    ALL_PARTICLES,
    PALETTE,
    TrackStyle,
    energy_lut,
    format_time,
    heads_at,
    hex_rgb,
    layer_from_showers,
    particle_rgb,
    time_ticks,
)
from argus.results.data import Tracks
from argus.results.shower import reconstruct

BALL = """Bola de radio 1 en el origen (pruebas de las trayectorias)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   Esfera R=1
INDICES=( 1, 1, 1, 0,-1)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)   Bola
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def _line(p0, p1, n, e0, e1):
    t = np.linspace(0.0, 1.0, n)[:, None]
    xyz = np.asarray(p0, float) + t * (np.asarray(p1, float) - np.asarray(p0, float))
    return np.hstack([xyz, np.linspace(e0, e1, n)[:, None]])


def _showers():
    """Lluvia 0: un electrón por delante de la bola (z = 3, mirando desde +Z) que deja un fotón en su vértice
    central; lluvia 1: un fotón por detrás (z = -3)."""
    el = _line((-3, 0, 3), (3, 0, 3), 31, 1.0e6, 7.0e5)
    child = _line(el[15, :3], (0, 3, 3), 11, 2.0e5, 2.0e5)
    back = _line((-3, 0.3, -3), (3, 0.3, -3), 31, 5.0e5, 5.0e5)
    photons = np.vstack([child, back])
    return reconstruct([Tracks(1, el, np.array([0, 31])), Tracks(2, photons, np.array([0, 11, 42]))])


def test_layer_data_from_a_cascade():
    s = _showers()
    assert s.n_showers == 2 and s.shower.tolist() == [0, 0, 1] and s.generation.tolist() == [0, 1, 0]
    d = layer_from_showers(s)
    assert d.vertices.shape == (73, 8) and d.vertices.dtype == np.float32
    assert d.n_segments == 30 + 10 + 30
    track_of = np.repeat(np.arange(s.count), s.lengths())
    assert np.all(track_of[d.seg_a] == track_of[d.seg_b])  # ningún tramo une dos trayectorias
    assert np.allclose(d.origin, [0.0, 1.5, 0.0])
    assert np.allclose(d.points, s.points, atol=1e-5)
    lo, hi = d.bounds()
    assert np.allclose(lo, [-3, 0, -3]) and np.allclose(hi, [3, 3, 3])
    assert d.t_max == pytest.approx(s.t_max) and d.kinds == [1, 2] and d.n_showers == 2
    assert d.log_e[0] == pytest.approx(np.log10(2e5)) and d.log_e[1] == pytest.approx(6.0)
    # El fotón hijo sale cuando el electrón pasa por su vértice central.
    assert s.time[31] == pytest.approx(s.time[15]) and s.time[15] > 0
    pairs = sorted(zip(d.seg_a.tolist(), d.seg_b.tolist()))
    assert pairs[:2] == [(0, 1), (1, 2)] and d.indices.tolist()[:2] == [d.seg_a[0], d.seg_b[0]]
    # Primero (debajo) los fotones; encima, los electrones.
    kinds = d.vertices[d.seg_a, 5]
    assert np.all(kinds[:40] == 2) and np.all(kinds[40:] == 1)


def test_style_filters_colours_and_animation():
    s = _showers()
    d = layer_from_showers(s)
    st = TrackStyle()
    assert st.visible_mask(d).all()
    only_e = TrackStyle(particles=frozenset({1}))
    assert int(only_e.visible_mask(d).sum()) == 30
    second = TrackStyle(shower_lo=1, shower_hi=2)
    m = second.visible_mask(d)
    assert int(m.sum()) == 30 and np.all(d.vertices[d.seg_a[m], 6] == 1)
    # Colores como en el shader.
    a = np.array([0, 31, 42])  # electrón, fotón hijo, fotón de la lluvia 1
    assert np.allclose(st.colors(d, a)[0], particle_rgb(1)) and np.allclose(st.colors(d, a)[1], particle_rgb(2))
    by_shower = TrackStyle(color_mode="shower").colors(d, a)
    assert np.allclose(by_shower, [hex_rgb(PALETTE[0]), hex_rgb(PALETTE[0]), hex_rgb(PALETTE[1])])
    by_gen = TrackStyle(color_mode="generation").colors(d, a)
    assert np.allclose(by_gen, [hex_rgb(PALETTE[0]), hex_rgb(PALETTE[1]), hex_rgb(PALETTE[0])])
    lut = energy_lut()
    assert lut.shape == (256, 4) and lut.dtype == np.uint8
    by_e = TrackStyle(color_mode="energy").colors(d, np.array([0, 31]))
    assert np.allclose(by_e[0], lut[255, :3] / 255.0) and np.allclose(by_e[1], lut[0, :3] / 255.0)
    # Escalonadas: la lluvia 1 sale «stagger» después.
    stag = TrackStyle(stagger=1.0)
    assert stag.start_offsets(d, np.array([0, 42])).tolist() == [0.0, 1.0]
    # La animación acaba con la última lluvia que se ve (con su retraso), no con la que más dura de toda la cascada.
    ends = d.shower_ends()
    assert stag.end_time(d) == pytest.approx(max(ends[0], ends[1] + 1.0))
    assert TrackStyle(shower_lo=1, stagger=1.0).end_time(d) == pytest.approx(ends[1])
    assert TrackStyle(shower_lo=0, shower_hi=1).end_time(d) == pytest.approx(ends[0])
    assert max(ends) == pytest.approx(d.t_max)
    # Partículas en vuelo: en el instante del vértice central, el electrón está ahí (y el fotón hijo, saliendo).
    t = float(s.time[15]) + 1e-6
    pos, col = heads_at(d, TrackStyle(animate=True, time=t))
    pts = pos.astype(float) + d.origin
    assert np.min(np.linalg.norm(pts - [0.0, 0.0, 3.0], axis=1)) < 0.05
    assert len(pos) == 3 and col.shape == (3, 3)  # electrón, fotón hijo y fotón de la otra lluvia
    pos2, _ = heads_at(d, TrackStyle(animate=True, time=t, stagger=10.0 * d.t_max))
    assert len(pos2) == 2  # la lluvia 1 aún no ha salido
    assert heads_at(d, TrackStyle(animate=True, time=t, particles=frozenset({2})))[0].shape == (2, 3)
    assert heads_at(d, TrackStyle(animate=False, time=t))[0].shape == (0, 3)
    assert ALL_PARTICLES == frozenset(range(1, 7))


def test_time_scale_text():
    ticks = time_ticks(0.35)
    assert ticks[0] == 0.0 and ticks[-1] <= 0.35 + 1e-12 and len(ticks) >= 3
    steps = np.diff(ticks)
    assert np.allclose(steps, steps[0]) and steps[0] in (0.05, 0.1)
    assert time_ticks(0.0) == [0.0]
    assert format_time(0.35) == "0,35 ns" and format_time(12) == "12 ns" and format_time(250) == "250 ns"
    assert format_time(4100) == "4,1 µs" and format_time(2.5e6) == "2,5 ms"


# -- dibujo sobre la geometría ----------------------------------------------------------------------------------------

def _view(qtbot, tmp_path, backend):
    from argus.render import raytrace
    from argus.session import open_geometry
    from argus.ui.view3d import View3D

    raytrace.warmup()
    p = tmp_path / "bola.geo"
    p.write_text(BALL, encoding="utf-8")
    v = View3D()
    qtbot.addWidget(v)
    v.resize(640, 480)
    v.show()
    qtbot.waitExposed(v)
    v.use_backend(backend)
    v.set_session(open_geometry(p))
    v.set_view("+Z")
    return v


def _pixels(v, pts):
    w, h = v.overlay.width(), v.overlay.height()
    px, z = v.camera.project(np.asarray(pts, float), w, h)
    assert np.all(z > 0)
    return np.round(px).astype(int)


def _rgb(img, x, y):
    c = img.pixelColor(int(x), int(y))
    return np.array([c.red(), c.green(), c.blue()], dtype=float)


def _closest(img, x, y, target, r=3):
    """La distancia (suma de |Δ| por canal) del píxel más parecido a ``target`` alrededor de (x, y)."""
    best = 1e9
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if 0 <= x + dx < img.width() and 0 <= y + dy < img.height():
                best = min(best, float(np.abs(_rgb(img, x + dx, y + dy) - target).sum()))
    return best


def _unchanged(base, img, x, y, r=1, tol=24.0):
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if np.abs(_rgb(base, x + dx, y + dy) - _rgb(img, x + dx, y + dy)).sum() > tol:
                return False
    return True


def _check_drawing(qtbot, v):
    """Lo de delante se ve, lo de detrás de la bola no y lo de detrás fuera de la bola sí; con la animación, solo lo
    recorrido."""
    s = _showers()
    d = layer_from_showers(s)
    v.set_track_layer(d)
    v.fit()
    qtbot.waitUntil(v.is_idle, timeout=60000)
    v.set_track_layer(None)
    base = v.grab_image()
    v.set_track_layer(d)
    img = v.grab_image()
    assert base.size() == img.size()
    e_col = np.array(particle_rgb(1)) * 255.0
    g_col = np.array(particle_rgb(2)) * 255.0
    alpha = TrackStyle().alpha
    front_out, front_in, back_hidden, back_out, right = _pixels(
        v, [(-2.0, 0, 3), (0.5, 0, 3), (0.4, 0.3, -3), (2.4, 0.3, -3), (2.0, 0, 3)])
    for (x, y), col in ((front_out, e_col), (front_in, e_col), (back_out, g_col)):
        want = alpha * col + (1 - alpha) * _rgb(base, x, y)
        assert _closest(img, x, y, want) < 70, (x, y, want)
        # La imagen sigue opaca (si el alfa bajase donde hay líneas, al copiarla o exportarla se vería más clara).
        assert all(img.pixelColor(x + dx, y + dy).alpha() == 255 for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    assert _unchanged(base, img, *back_hidden)  # tapado por la bola: no se dibuja
    # Animación: en el instante del vértice central, el electrón ha recorrido la mitad izquierda.
    t = float(s.time[15]) * 0.999
    v.set_track_style(TrackStyle(animate=True, time=t, heads=False))
    anim = v.grab_image()
    x, y = front_out
    want = alpha * e_col + (1 - alpha) * _rgb(base, x, y)
    assert _closest(anim, x, y, want) < 70
    assert _unchanged(base, anim, *right)
    # Solo los fotones.
    v.set_track_style(TrackStyle(particles=frozenset({2})))
    only_g = v.grab_image()
    assert _unchanged(base, only_g, *front_out)
    return d


@pytest.mark.gui
def test_tracks_without_gpu_are_hidden_behind_the_geometry(qtbot, tmp_path):
    v = _view(qtbot, tmp_path, "cpu")
    assert v.backend == "CPU"
    _check_drawing(qtbot, v)
    v.set_track_layer(None)
    v.grab_image()


def test_fit_includes_the_tracks_but_not_what_flies_far_away(qapp):
    from argus.ui.view3d import View3D

    v = View3D()
    v.scene_lo, v.scene_hi = np.array([-1.0, -1.0, -1.0]), np.array([1.0, 1.0, 1.0])
    d = layer_from_showers(_showers())
    v.track_layer = d
    lo, hi = v._content_bounds()
    assert np.allclose(lo, [-3, -1, -3]) and np.allclose(hi, [3, 3, 3])
    far = _line((0, 0, 0), (1e4, 0, 0), 5, 1e6, 1e6)
    v.track_layer = layer_from_showers(reconstruct([Tracks(2, far, np.array([0, 5]))]))
    lo, hi = v._content_bounds()
    assert hi[0] <= 1.0 + 1.5 * np.sqrt(12) + 1e-9
    v.deleteLater()


@pytest.fixture
def need_gpu(qapp):
    from argus.ui.gpu_tracer import probe_gl

    ok, why = probe_gl()
    if not ok:
        pytest.skip(f"Sin OpenGL 4.1: {why}")


@pytest.mark.gui
def test_tracks_on_the_gpu_are_hidden_behind_the_geometry(qtbot, tmp_path, need_gpu):
    v = _view(qtbot, tmp_path, "gpu")
    assert v.backend != "CPU"
    qtbot.waitUntil(lambda: v.canvas.gpu is not None, timeout=30000)
    _check_drawing(qtbot, v)
    assert v.canvas.track_error is None and v.canvas._tracks is not None
    # Copiar el lienzo con QWidget.grab da lo mismo que grab_image (antes, con el alfa por debajo de 1, otros colores).
    v.set_track_style(TrackStyle())
    a = v.grab_image()
    b = v.canvas.grab().toImage().convertToFormat(a.format())
    x, y = _pixels(v, [(-2.0, 0, 3)])[0]
    assert np.abs(_rgb(a, x, y) - _rgb(b, x, y)).sum() < 12
    # Las líneas finas (sin geometry shader) también.
    v.set_track_style(TrackStyle(line_width=1.0))
    v.grab_image()
    assert v.canvas.track_error is None
    v.shutdown()
    assert v.canvas._tracks is None


@pytest.mark.gui
def test_if_the_gpu_rejects_the_track_program_they_are_drawn_without_it(qtbot, tmp_path, need_gpu, monkeypatch):
    """Una tarjeta que no acepta el programa de las trayectorias: antes no se veía ninguna y nadie lo decía."""
    from argus.ui import gpu_tracks
    from argus.ui.gpu_tracer import GpuError

    def broken(*_a, **_k):
        raise GpuError("programa de mentira que no compila")

    monkeypatch.setattr(gpu_tracks.GpuTrackLayer, "draw", broken)
    v = _view(qtbot, tmp_path, "gpu")
    qtbot.waitUntil(lambda: v.canvas.gpu is not None, timeout=30000)
    said = []
    v.renderInfo.connect(said.append)
    _check_drawing(qtbot, v)
    assert v.canvas.track_error
    qtbot.waitUntil(lambda: any(t.startswith("Las trayectorias se dibujan sin GPU") for t in said), timeout=5000)
    v.shutdown()
