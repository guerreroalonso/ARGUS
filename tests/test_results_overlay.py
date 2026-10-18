"""Mapas de resultados sobre los cortes de la geometría: colocación (caja, cilindro, esfera, TAC), isolíneas,
perfiles y la unión de la ventana de resultados con la principal."""

from __future__ import annotations

import numpy as np
import pytest

from argus.render.dose_overlay import DoseOverlay, apply_overlay, isoline_masks, segment_profile
from argus.results.data import Grid
from argus.results.readers import read_output
from conftest import CORPUS

RES = CORPUS / "resultados"


def _box(values: np.ndarray, lo=(-2.0, -2.0, 0.0), hi=(2.0, 2.0, 4.0)) -> Grid:
    edges = tuple(np.linspace(a, b, n + 1) for a, b, n in zip(lo, hi, values.shape))
    return Grid("3d-dose-map", "prueba", "box", ("x", "y", "z"), edges, values, 0.01 * values, "Dosis", "eV/g")


def test_box_overlay_colors_and_isolines():
    v = np.zeros((4, 4, 4))
    v[1:3, 1:3, :] = 10.0
    v[2, 2, :] = 20.0
    ov = DoseOverlay(_box(v), levels=(40.0, 90.0))
    # Corte z = 1 visto de x, y en [-2, 2] con 40 × 40 píxeles.
    xs = -2 + (np.arange(40) + 0.5) * 0.1
    ys = 2 - (np.arange(40) + 0.5) * 0.1
    xx, yy = np.meshgrid(xs, ys)
    pts = np.stack([xx.ravel(), yy.ravel(), np.full(xx.size, 1.0)], axis=1)
    base = np.full((40, 40, 4), 200, dtype=np.uint8)
    out, labels = apply_overlay(base, pts, ov)
    changed = np.any(out[..., :3] != base[..., :3], axis=2)
    assert changed[20, 20] and changed[15, 15]  # dentro de la zona con dosis
    assert not changed[2, 2] and not changed[37, 2]  # sin dosis: la geometría se ve igual
    assert np.array_equal(out[..., 3], base[..., 3])  # la transparencia de la imagen no cambia
    assert {lv for lv, _x, _y in labels} == {40.0, 90.0}


def test_isolines_do_not_follow_the_edge_of_the_grid():
    v = np.full((6, 6), 5.0)
    v[0, :] = np.nan
    masks = isoline_masks(v, [1.0])
    assert not masks[0].any()  # todo por encima del nivel: no hay isolínea, aunque el mapa acabe ahí


def test_cylinder_and_sphere_are_placed_around_z_and_the_origin():
    r = np.linspace(0, 2, 5)
    z = np.linspace(0, 4, 5)
    val = np.arange(16, dtype=float).reshape(4, 4) + 1
    g = Grid("2d-dose-map", "c", "cylinder", ("r", "z"), (r, z), val, 0.1 * val, "Dosis", "eV/g")
    ov = DoseOverlay(g)
    ang = 0.7
    p = np.array([[1.2 * np.cos(ang), 1.2 * np.sin(ang), 2.6]])
    assert ov.sample(p)[0] == val[2, 2]
    s = Grid("radial-dose", "s", "sphere", ("r",), (np.linspace(0, 3, 4),), np.array([3.0, 2.0, 1.0]),
             np.array([0.1, 0.1, 0.1]), "Dosis", "eV/g")
    ov = DoseOverlay(s)
    assert ov.sample(np.array([[0.0, 1.5, 0.9]]))[0] == 2.0
    assert np.isnan(ov.sample(np.array([[0.0, 3.5, 0.0]]))[0])


def test_ct_dose_is_placed_with_the_ct_frame():
    from argus.pengeom.ct import ct_frame
    from argus.session import open_case

    grid = read_output(RES / "tac" / "ct-3d-dose.dat")
    cf = open_case(RES / "tac" / "tac.in")
    geo = cf.session.geo
    ov = DoseOverlay(grid)
    assert ov.check_ct(geo.ct) is not None  # sin colocación, no
    ov.frame = ct_frame(geo)
    assert ov.check_ct(geo.ct) is None
    v = grid.value
    ix, iy, iz = np.unravel_index(int(np.argmax(v)), v.shape)
    local = np.array([[grid.centers(0)[ix], grid.centers(1)[iy], grid.centers(2)[iz]]])
    world = ov.frame.to_lab(local)
    assert ov.sample(world)[0] == pytest.approx(v[ix, iy, iz])
    # Un TAC de otro tamaño no se acepta.
    other = Grid("ct-3d-dose", "t", "ct", ("x", "y", "z"), tuple(np.arange(n + 1.0) for n in (3, 3, 3)),
                 np.ones((3, 3, 3)), np.zeros((3, 3, 3)), "Dosis", "eV/g")
    assert "no es el de" in DoseOverlay(other, frame=ov.frame).check_ct(geo.ct)


def test_segment_profile_matches_the_voxels():
    v = np.random.default_rng(3).random((4, 4, 8)) + 1
    ov = DoseOverlay(_box(v, hi=(2.0, 2.0, 8.0)))
    c = segment_profile(ov, (0.5, -0.5, -5.0), (0.5, -0.5, 20.0), n=2001, axis_name="z", x0=-5.0)
    assert c.x[0] == pytest.approx(0.0, abs=0.02) and c.x[-1] == pytest.approx(8.0, abs=0.02)
    got = c.series[0].y[np.searchsorted(c.x, np.arange(8) + 0.5)]
    np.testing.assert_allclose(got, v[2, 1, :])
    with pytest.raises(ValueError):
        segment_profile(ov, (10, 10, 0), (10, 10, 5))


@pytest.mark.gui
def test_results_window_shows_the_dose_on_the_main_window(qtbot, monkeypatch):
    pytest.importorskip("matplotlib")
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow
    from argus.ui.results.window import Ref
    from argus.ui.settings import app_settings

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    w.open_file(str(RES / "tac" / "tac.in"))
    rw = w.results_window()
    rw.open_folder(RES / "tac", RES / "tac" / "tac.in")
    qtbot.waitUntil(lambda: rw.loaded_token >= rw._token, timeout=20000)
    rw.select(Ref(rw.primary().key, "ct-3d-dose"))
    qtbot.waitUntil(lambda: rw.loaded_token >= rw._token, timeout=20000)
    assert rw.act_geo.isEnabled()
    rw.act_geo.setChecked(True)
    ov = w.view.results_overlay
    assert ov is not None and ov.frame is not None
    assert "por partícula" in ov.label
    w.view.set_plane("XY")
    w.view.set_depth(6.0)
    qtbot.waitUntil(w.view.is_idle, timeout=20000)
    assert w.view._overlay_problem is None
    # En el punto de más dosis, la barra de estado dice la dosis.
    v = ov.grid.value
    ix, iy, iz = np.unravel_index(int(np.argmax(v)), v.shape)
    world = ov.frame.to_lab(np.array([[ov.grid.centers(0)[ix], ov.grid.centers(1)[iy], ov.grid.centers(2)[iz]]]))[0]
    assert "Dosis" in w._overlay_text(world)
    # Perfil por ese punto: en la ventana de resultados, con medidas.
    w.results_profile(world, w.view.plane.u, "x")
    assert rw.views.currentWidget() is rw.curves_view
    assert rw.curves_view.curves.kind == "profile" and rw.metrics.results
    # Las unidades cambian también sobre la geometría; al apagarlo, se quita.
    rw.units.dose.setCurrentIndex(rw.units.dose.findData("Gy"))
    assert w.view.results_overlay.unit == "Gy"
    rw.act_geo.setChecked(False)
    assert w.view.results_overlay is None
    rw.close()
    w.close()


@pytest.mark.gui
def test_tracks_are_drawn_on_the_geometry(qtbot, monkeypatch):
    pytest.importorskip("matplotlib")
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow
    from argus.ui.results.window import Ref
    from argus.ui.settings import app_settings

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    w.open_file(str(RES / "caja" / "caja.in"))
    rw = w.results_window()
    rw.open_folder(RES / "caja", RES / "caja" / "caja.in")
    qtbot.waitUntil(lambda: rw.loaded_token >= rw._token, timeout=20000)
    key = next(o.key for o in rw.primary().folder.files if o.kind == "trk")
    rw.select(Ref(rw.primary().key, key))
    qtbot.waitUntil(lambda: rw.loaded_token >= rw._token, timeout=20000)
    assert rw.act_geo.isEnabled()
    rw.act_geo.setChecked(True)
    assert {t.particle for t in w.view.tracks} == {1, 2} and w.view3d.tracks is not None
    w.view.grab()  # se dibujan sin errores
    w.view3d.overlay.grab()
    # Ocultar una partícula en la vista de trayectorias la quita también de la geometría.
    tv = rw.views.currentWidget()
    tv._checks[2].setChecked(False)
    assert {t.particle for t in w.view.tracks} == {1}
    rw.act_geo.setChecked(False)
    assert w.view.tracks is None and w.view3d.tracks is None
    rw.close()
    w.close()
