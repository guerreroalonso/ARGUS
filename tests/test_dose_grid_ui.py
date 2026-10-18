"""La matriz de dosis (0.13.0): tamaño del vóxel, los vóxeles en el corte y en 3D, y estirarla o moverla con el ratón
sin cambiar el vóxel."""

from __future__ import annotations

import shutil

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt  # noqa: E402

from argus.penhan import sections as sc  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

CASE = CORPUS / "usuario/100Mev15-174"


def test_stretching_an_axis_keeps_the_voxel_or_the_number_of_voxels():
    # El vóxel (0,5 cm) se queda: cambia el número de intervalos y el extremo cae en un número entero de vóxeles.
    assert sc.stretch_axis(-5.0, 5.0, 20, 7.3, "hi", 101) == (-5.0, 7.5, 25)
    assert sc.stretch_axis(-5.0, 5.0, 20, -8.1, "lo", 101) == (-8.0, 5.0, 26)
    assert sc.stretch_axis(-5.0, 5.0, 20, -4.9, "hi", 101) == (-5.0, -4.5, 1)  # al menos un vóxel
    assert sc.stretch_axis(0.0, 10.0, 100, 500.0, "hi", 401) == (0.0, 40.1, 401)  # como mucho 401
    # Sin conservar el vóxel: los mismos intervalos.
    assert sc.stretch_axis(-5.0, 5.0, 20, 7.0, "hi", 101, keep_voxel=False) == (-5.0, 7.0, 20)
    # Un eje escrito al revés en el .in (de mayor a menor) se trata igual.
    assert sc.stretch_axis(5.0, -5.0, 20, 7.3, "hi", 101) == (-5.0, 7.5, 25)
    assert sc.stretch_radius(5.0, 10, 7.2) == (7.0, 14)
    assert sc.stretch_radius(5.0, 10, 0.01) == (0.5, 1)
    assert sc.stretch_radius(5.0, 10, 7.2, keep_voxel=False) == (7.2, 10)


def test_the_grid_never_collapses_and_odd_voxels_do_not_break_anything():
    """Revisión de 0.13.0: con Ctrl, llevar una cara hasta la otra dejaba el eje sin longitud (penhmain: 3102/3106)
    y el siguiente arrastre se rompía (división por cero); un vóxel de 1e-310 cm, OverflowError; lejos del origen, el
    redondeo a 6 cifras cambiaba el vóxel."""
    a, b, n = sc.stretch_axis(-5.0, 5.0, 11, 7.0, "lo", 101, keep_voxel=False)
    assert b - a >= 1e-5 and n == 11
    a, b, n = sc.stretch_axis(0.0, 10.0, 25, -3.0, "hi", 401, keep_voxel=False)
    assert b - a >= 25 * sc.GRID_MIN_LENGTH
    assert sc.stretch_axis(6.0, 6.0, 24, 8.0, "hi", 101)[1] == 8.0  # un eje sin longitud se puede estirar
    assert sc.stretch_axis(-5.0, 5.0, 20, float("nan"), "hi", 101) == (-5.0, 5.0, 20)
    assert sc.stretch_radius(5.0, 10, -1.0, keep_voxel=False)[0] >= 1e-5
    assert sc.stretch_radius(0.0, 10, 3.0)[0] == 3.0
    assert sc.bins_for_voxel(0.0, 10.0, 1e-310, 401)[2] == 401
    assert sc.radial_bins_for(5.0, 1e-310)[1] == 101
    a, b, n, _ = sc.bins_for_voxel(1000.0, 1010.0, 0.03, 401, odd=False)
    assert (b - a) / n == pytest.approx(0.03, rel=1e-9)
    # Lo que penhmain no aceptaría no se escribe: un eje sin longitud, o quitar la rejilla que usa EPROF (3110).
    from argus.penhan.inputfile import parse_in_file

    doc = parse_in_file(CORPUS / "resultados" / "cilindro" / "cilindro.in")
    with pytest.raises(ValueError, match="1e-6"):
        sc.set_dose_grid(doc, sc.DoseGrid(z=(3.0, 3.0, 30), r=(5.0, 10)))
    with pytest.raises(ValueError, match="EPROF"):
        sc.set_dose_grid(doc, sc.DoseGrid())
    sc.set_external_process(doc, None)
    sc.set_dose_grid(doc, sc.DoseGrid())
    assert sc.dose_grid(doc).kind == "none"


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    w.open_file(tmp_path / "maniqui.in")
    yield w
    app_settings().clear()


BOX = sc.DoseGrid((-10.0, 10.0, 21), (-10.0, 10.0, 21), (0.0, 20.0, 40))


@pytest.mark.gui
def test_dragging_a_corner_in_the_slice_keeps_the_voxel(qtbot, window):
    w = window
    w.input.edit("caja", lambda d: sc.set_dose_grid(d, BOX))
    v = w.view
    v.set_plane("XZ")
    v.set_depth(0.0)
    v.scale = 0.05
    v._grid_origin = sc.dose_grid_from_model(v.dose_grid)
    v._drag_mode = "grid_hi"
    v._drag_start = QPointF(0, 0)
    got = []
    v.gridChanged.connect(lambda g, fin: got.append(g))
    # Al soltar en x = 13,1 y z = 25,3 cm: 24 vóxeles de 20/21 cm en x (23,1 cm) y 51 de 0,5 cm en z.
    u, vv = 13.1, 25.3
    x, y = v.view().plane_to_pixel(u, vv, v.width(), v.height())
    v._drag_modifiers = Qt.KeyboardModifier.NoModifier
    v._drag_grid(QPointF(x, y), True)
    g = got[-1]
    assert g.x[2] == 24 and g.x[1] == pytest.approx(-10.0 + 24 * 20.0 / 21, abs=1e-4)
    assert g.z == (0.0, 25.5, 51) and g.y == BOX.y
    assert "vóxeles de" in v._drag_info
    # Con Ctrl: los mismos intervalos (cambia el vóxel).
    v._drag_modifiers = Qt.KeyboardModifier.ControlModifier
    v._drag_grid(QPointF(x, y), True)
    g = got[-1]
    assert g.x[2] == 21 and g.z[2] == 40 and g.z[1] == pytest.approx(25.3, abs=0.06)


@pytest.mark.gui
def test_voxels_are_drawn_in_the_slice_when_they_are_big_enough(qtbot, window, monkeypatch):
    from argus.ui import slice_view as svm

    w = window
    v = w.view
    calls = []
    real = svm.SliceViewWidget._draw_voxels_box
    monkeypatch.setattr(svm.SliceViewWidget, "_draw_voxels_box",
                        lambda self, p, g, iu, iv, rect: (calls.append((iu, iv)), real(self, p, g, iu, iv, rect)))
    for grid in (BOX, sc.DoseGrid(z=(0.0, 20.0, 40), r=(10.0, 20)), sc.DoseGrid(r=(10.0, 20))):
        w.input.edit("rejilla", lambda d, g=grid: sc.set_dose_grid(d, g))
        for plane in ("XZ", "XY"):
            v.set_plane(plane)
            v.set_depth(1.0 if plane == "XZ" else 5.0)
            for scale in (0.02, 1.0):  # vóxeles grandes en pantalla y diminutos
                v.scale = scale
                v.grab()
    assert calls  # la caja, en los cortes que la cortan
    assert svm.SliceViewWidget._voxel_text(BOX) == "vóxel 0,952 × 0,952 × 0,5 cm"
    assert "anillos de 0,5 cm" in svm.SliceViewWidget._voxel_text(sc.DoseGrid(z=(0.0, 20.0, 40), r=(10.0, 20)))


@pytest.mark.gui
def test_dragging_the_grid_in_3d(qtbot, window):
    w = window
    w.input.edit("caja", lambda d: sc.set_dose_grid(d, BOX))
    v3 = w.view3d
    w.set_view_mode("3d") if hasattr(w, "set_view_mode") else None
    qtbot.waitUntil(lambda: v3.isVisible(), timeout=10000)
    v3.fit_box(np.array([-15.0, -15.0, -5.0]), np.array([15.0, 15.0, 30.0]))  # de cerca, como se haría
    hs = v3.grid_handles()
    assert set(hs) == {"move", "lo0", "hi0", "lo1", "hi1", "lo2", "hi2"}
    assert np.allclose(hs["hi2"], [0.0, 0.0, 20.0]) and np.allclose(hs["move"], [0.0, 0.0, 10.0])
    cw, ch = v3.canvas.width(), v3.canvas.height()
    px, _ = v3.camera.project(np.array([hs["hi2"], hs["hi2"] + [0.0, 0.0, 5.0]]), cw, ch)
    assert v3.grid_handle_at(px[0][0], px[0][1]) == "hi2"
    v3.grab()  # dibuja la celosía y las asas sin errores

    class Ev:
        def __init__(self, q, mods=Qt.KeyboardModifier.NoModifier, held=True):
            self._p = QPointF(float(q[0]), float(q[1]))
            self._m = mods
            self._held = held

        def position(self):
            return self._p

        def button(self):
            return Qt.MouseButton.LeftButton

        def buttons(self):
            return Qt.MouseButton.LeftButton if self._held else Qt.MouseButton.NoButton

        def modifiers(self):
            return self._m

    # Arrastrar la cara z = 20 hasta z ≈ 25: 10 vóxeles más de 0,5 cm, un solo paso de deshacer.
    v3.on_press(Ev(px[0]))
    assert v3.dragging
    v3.on_move(Ev(0.5 * (px[0] + px[1])))
    assert w.input.in_live
    v3.on_release(Ev(px[1]))
    assert not v3.dragging and not w.input.in_live
    g = sc.dose_grid(w.input.doc)
    assert g.z[0] == 0.0 and g.z[1] in (24.5, 25.0, 25.5) and g.z[2] == round(g.z[1] / 0.5) and g.x == BOX.x
    w.undo()
    assert sc.dose_grid(w.input.doc).z == BOX.z
    # Con Ctrl: cambia el vóxel, no el número de vóxeles.
    v3.on_press(Ev(px[0]))
    v3.on_move(Ev(px[1], Qt.KeyboardModifier.ControlModifier))
    v3.on_release(Ev(px[1], Qt.KeyboardModifier.ControlModifier))
    g = sc.dose_grid(w.input.doc)
    assert g.z[2] == 40 and g.z[1] == pytest.approx(25.0, abs=0.3)
    w.undo()
    # Moverla entera (en el plano de la pantalla).
    m, _ = v3.camera.project(np.array([v3.grid_handles()["move"]]), cw, ch)
    v3.on_press(Ev(m[0]))
    v3.on_move(Ev(m[0] + [40.0, 0.0]))
    v3.on_release(Ev(m[0] + [40.0, 0.0]))
    g = sc.dose_grid(w.input.doc)
    assert g.x[2] == 21 and (g.x[0], g.y[0]) != (BOX.x[0], BOX.y[0])
    assert g.x[1] - g.x[0] == pytest.approx(20.0)
    w.undo()
    # Un clic (con Ctrl o sin él) sin moverse no cambia nada ni deja un paso en el historial.
    n_steps = w.input.stack.count()
    v3.on_press(Ev(px[0]))
    v3.on_move(Ev(px[0] + [1.0, 1.0], Qt.KeyboardModifier.ControlModifier))
    v3.on_release(Ev(px[0] + [1.0, 1.0], Qt.KeyboardModifier.ControlModifier))
    assert sc.dose_grid(w.input.doc).z == BOX.z and w.input.stack.count() == n_steps
    # Esc a mitad del arrastre lo anula (como en el corte).
    v3.on_press(Ev(px[0]))
    v3.on_move(Ev(px[1]))
    assert w.input.in_live
    from PySide6.QtGui import QKeyEvent

    assert v3.on_key(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not v3.dragging and not w.input.in_live and sc.dose_grid(w.input.doc).z == BOX.z
    # Un movimiento sin el botón (la suelta se perdió fuera de la ventana): se termina el arrastre.
    v3.on_press(Ev(px[0]))
    v3.on_move(Ev(px[1]))
    v3.on_move(Ev(px[1] + [5.0, 5.0], held=False))
    assert not v3.dragging and not w.input.in_live and sc.dose_grid(w.input.doc).z != BOX.z
    w.undo()
    # Vistas de los ejes: las caras que se ven de frente no se cogen (en el centro, el rombo de mover).
    for name in ("+Z", "+X", "-Y"):
        v3.set_view(name)
        m, _ = v3.camera.project(np.array([v3.grid_handles()["move"]]), v3.canvas.width(), v3.canvas.height())
        assert all(v3.grid_handle_at(m[0][0] + dx, m[0][1] + dy) == "move" for dx in (-2, 0, 2) for dy in (-2, 0, 2))
        assert len(v3.usable_grid_handles()) == 5
    # Sin .in (o sin rejilla), sin asas.
    w.input.edit("sin rejilla", lambda d: sc.set_dose_grid(d, sc.DoseGrid()))
    assert v3.grid_handles() == {}


def _mouse(kind, pos, held=True, mods=Qt.KeyboardModifier.NoModifier):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent

    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
         "release": QEvent.Type.MouseButtonRelease}[kind]
    button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.LeftButton if held and kind != "release" else Qt.MouseButton.NoButton
    return QMouseEvent(t, pos, pos, button, buttons, mods)


@pytest.mark.gui
def test_a_drag_starts_from_what_was_just_typed_and_the_handle_does_not_jump(qtbot, window):
    """Revisión de 0.13.0: un valor escrito sin Intro se aplica justo después del clic que empieza el arrastre (al
    perder el foco) y el arrastre lo pisaba; y la esquina saltaba al cursor (se cogía a unos píxeles de ella)."""
    w = window
    w.input.edit("caja", lambda d: sc.set_dose_grid(d, BOX))
    v = w.view
    v.set_plane("XZ")
    v.set_depth(0.0)
    v.scale = 0.05
    v.grab()
    h = v._handles["grid_hi"]
    press = QPointF(h.x() - 5, h.y() + 4)
    v.mousePressEvent(_mouse("press", press))
    assert v._drag_mode == "grid_hi"
    typed = sc.DoseGrid(BOX.x, BOX.y, (0.0, 20.0, 80))  # Δz = 0,25 cm, escrito en el panel sin Intro
    w.input.edit("Δz", lambda d: sc.set_dose_grid(d, typed))
    v.mouseMoveEvent(_mouse("move", press + QPointF(1.0, 0.0)))
    v.mouseReleaseEvent(_mouse("release", press + QPointF(1.0, 0.0)))
    g = sc.dose_grid(w.input.doc)
    assert g.z == typed.z  # el arrastre parte de lo escrito (y apenas se ha movido: la esquina no salta)
    assert g.x[1] == pytest.approx(BOX.x[1], abs=0.06)


@pytest.mark.gui
def test_in_a_slice_where_the_source_is_not_the_grid_handle_wins(qtbot, window):
    """Corte XY con un haz centrado y la fuente detrás: el rombo de mover la rejilla quedaba debajo de la estrella
    de la fuente y arrastrarlo movía la fuente."""
    w = window
    w.input.edit("caja", lambda d: sc.set_dose_grid(d, BOX))
    v = w.view
    v.set_plane("XY")
    v.set_depth(10.0)
    v.scale = 0.05
    v.grab()
    src = np.asarray(v.source.position, dtype=float)
    if abs(src[2] - 10.0) < 1.0:
        pytest.skip("la fuente está en ese corte")
    if v._handles.get("grid_move") is None or v._handles.get("source") is None:
        pytest.skip("sin las dos asas en el corte")
    g, s_ = v._handles["grid_move"], v._handles["source"]
    if abs(g.x() - s_.x()) + abs(g.y() - s_.y()) > 4:
        pytest.skip("las asas no se tapan en este caso")
    assert v._handle_at(g) == "grid_move"
