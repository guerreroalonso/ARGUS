"""Undécima revisión (0.17.3): PENGEOM, la cascada y los vóxeles, en la ventana.

Cada prueba falla con el código de 0.17.2."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

pytest.importorskip("PySide6")

from argus.penhan import sections as sc  # noqa: E402
from argus.render.track_layer import TrackStyle, layer_from_showers  # noqa: E402
from conftest import CORPUS  # noqa: E402
from test_gui_modules import window  # noqa: E402, F401
from test_track_layer import _showers, _view  # noqa: E402

sh_only = pytest.mark.skipif(os.name == "nt", reason="el penhmain de mentira es un guion de sh")

pytestmark = pytest.mark.gui

CAJA = CORPUS / "resultados" / "caja"


@pytest.fixture
def hooked(monkeypatch):
    """Lo que llega al aviso de «error inesperado» (sys.excepthook)."""
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: seen.append(f"{t.__name__}: {v}"))
    return seen


# -- la cascada sin GPU ------------------------------------------------------------------------------------------------

def _paint_overlays(v) -> None:
    """Un repintado de las superposiciones, como su paintEvent (``repaint()`` no pinta en el momento con todas las
    plataformas de Qt: con xcb, no)."""
    from PySide6.QtGui import QImage, QPainter

    w, h = v.overlay.width(), v.overlay.height()
    img = QImage(max(w, 1), max(h, 1), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        v.paint_overlays(p, w, h)
    finally:
        p.end()


def test_without_gpu_the_tracks_are_drawn_once_per_view(qtbot, tmp_path, monkeypatch):
    """El trazado progresivo de la imagen repinta las superposiciones cada ~40 ms y las trayectorias se volvían a
    dibujar cada vez (con las 1000 lluvias de siempre, ~1 s cada una: más de un minuto con la ventana a tirones). Ahora
    se dibujan una vez por cámara, tamaño, estilo y lo que se ve, y se reutilizan; las lluvias que caben se siguen
    diciendo."""
    v = _view(qtbot, tmp_path, "cpu")
    d = layer_from_showers(_showers())
    monkeypatch.setattr(type(v), "MAX_CPU_SEGMENTS", 40)  # (que no quepan todas: la lluvia 1 no se dibuja)
    calls = []
    draw = v._draw_track_layer_cpu
    monkeypatch.setattr(v, "_draw_track_layer_cpu", lambda p, w, h: (calls.append(1), draw(p, w, h)))
    v.set_track_layer(d)
    v.fit()
    qtbot.waitUntil(v.is_idle, timeout=60000)
    first = v.grab_image()
    calls.clear()
    for _ in range(5):
        _paint_overlays(v)
    again = v.grab_image()
    assert len(calls) == 0
    assert v.cpu_showers == (0, 1)
    assert again == first
    # Lo que lo cambia, sí: la cámara, el estilo y la geometría que se ve.
    v.camera.yaw += 10.0
    v._camera_changed()
    _paint_overlays(v)
    _paint_overlays(v)
    assert len(calls) == 1
    v.set_track_style(TrackStyle(particles=frozenset({2})))
    _paint_overlays(v)
    assert len(calls) == 2
    v.set_body_hidden(1, True)
    _paint_overlays(v)
    assert len(calls) == 3


def _shower_view(qtbot, data):
    from argus.ui.results.shower_view import ShowerView
    from argus.ui.settings import app_settings

    v = ShowerView(settings=app_settings())
    qtbot.addWidget(v)
    v.resize(900, 600)
    v.show()
    v.set_data(data, "Cascada")
    return v


def test_tracks_on_the_geometry_are_whole_showers_and_say_which(qtbot):
    """«Ver sobre la geometría» con muchas lluvias: los cortes y la vista 3D de la ventana principal repartían sus
    puntos por tipo de partícula (con 1000 lluvias, fotones de casi todas y electrones de las 46 primeras) y la barra
    decía «Trayectorias sobre la geometría: 17523», sin más. Ahora, las primeras lluvias enteras que caben, y lo
    dice."""
    from argus.results.case import load_case_geometry
    from argus.ui.results.shower_view import CascadeData

    s = _showers()
    in_path = CAJA / "caja.in"
    data = CascadeData(CAJA, in_path, showers=s, layer=layer_from_showers(s).prepare(),
                       case=load_case_geometry(in_path))
    v = _shower_view(qtbot, data)
    first = int(np.sum(s.lengths()[s.shower == 0]))
    tracks = v.visible_tracks(first + 1)
    assert sum(len(t.points) for t in tracks) == first  # (la lluvia 1, entera; la 2 no cabe)
    assert "la lluvia 1 de las 2" in tracks.note
    assert not v.visible_tracks(10 ** 6).note
    v.shutdown()


def test_the_main_window_says_how_many_tracks_with_thousands_and_what_is_left(qtbot, window):  # noqa: F811
    from argus.results.data import TrackList, Tracks

    pts = np.zeros((20000, 4))
    pts[:, 0] = np.arange(20000) * 1e-3
    tracks = TrackList([Tracks(2, pts, np.arange(0, 20001, 2))])
    tracks.note = "las lluvias 1–3 de las 9 que se ven en la cascada (no caben más)"
    window.show_tracks_on_geometry(tracks)
    msg = window.statusBar().currentMessage()
    assert "10 000" in msg or "10 000" in msg.replace(" ", " ")
    assert "lluvias 1–3 de las 9" in msg


@sh_only
def test_cascade_texts_follow_the_number_and_a_cascade_without_tracks_says_so(qtbot, tmp_path):
    """«1 de 1 lluvias simuladas», «cascada (1 lluvias, …)»; y una cascada terminada sin ningún .trk se daba por
    simulada y el visor enseñaba la presentación («Simular la cascada»: «ya simulada») sin decir nada."""
    from argus.penhan import cascade as cs
    from argus.ui.results.shower_view import load_cascade_data
    from test_cascade import NATIVE, _case, _fake

    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    for n in (1, 5):
        job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=n, cap=50, jobs=1))
        assert job.wait(60) and job.status.stage == "terminada"
    infos = {i.showers: i for i in cs.find_cascades(in_path.parent)}
    v = _shower_view(qtbot, load_cascade_data(in_path.parent, in_path, prefer=infos[1].path))
    assert "1 de 1 lluvia simulada" in v.status.text()
    assert any("(1 lluvia," in v.which.itemText(i) for i in range(v.which.count()))
    v.shutdown()
    for p in infos[5].run_paths[0].glob("*.trk"):
        p.unlink()
    v = _shower_view(qtbot, load_cascade_data(in_path.parent, in_path, prefer=infos[5].path))
    assert "0 de 5" in v.status.text() and "ningún .trk" in v.status.text()
    assert v.pages.currentWidget() is v.msg_page and "ningún .trk" in v.msg.text()
    v.shutdown()


# -- la matriz de dosis en la ventana --------------------------------------------------------------------------------

def test_a_dose_grid_position_penhmain_would_not_accept_is_not_an_unexpected_error(qtbot, window, hooked):  # noqa: F811
    """Al arrastrar una rejilla de vóxeles diminutos, set_dose_grid rechaza una posición (eje de menos de 1e-6 cm) y la
    excepción llegaba al aviso de «error inesperado» en cada movimiento. Ahora se dice en la barra y se sigue."""
    w = window
    w.open_file(CAJA / "caja.in")

    def bad(_doc):
        raise ValueError("La rejilla de dosis tiene un eje de menos de 1e-6 cm: penhmain no la acepta.")

    w._drag_edit("Rejilla de dosis", bad, False)
    w._drag_edit("Rejilla de dosis", bad, True)
    assert not hooked
    assert "1e-6 cm" in w.statusBar().currentMessage()


def test_with_resume_the_views_draw_the_grid_of_the_dump_without_handles(qtbot, window, tmp_path):  # noqa: F811
    """Con RESUME de un volcado con otra rejilla, penhmain sigue con la del volcado: los cortes y la vista 3D
    dibujaban (y dejaban estirar) la del .in."""
    from test_matrix_review6 import _resume_case

    in_path, _doc = _resume_case(tmp_path)
    w = window
    w.open_file(in_path)
    qtbot.waitUntil(lambda: w.view.dose_grid is not None and w.view.dose_grid != {}, timeout=10000)
    assert w.view.dose_grid["GRIDZ"][2] == 25 and not w.view.grid_editable and "volcado" in w.view.grid_label
    assert w.view3d.dose_grid["GRIDZ"][2] == 25 and not w.view3d.grid_editable


def test_moving_the_dose_grid_in_3d_follows_the_mouse(qtbot):
    """En perspectiva, «mover» la rejilla iba a la escala del objetivo de la cámara: con el objetivo 30 cm detrás de
    la rejilla, 2,4 veces más deprisa que el ratón."""
    from PySide6.QtCore import Qt

    from argus.penhan.inputfile import parse_in_file
    from argus.ui.view3d import View3D

    v = View3D()
    qtbot.addWidget(v)
    v.resize(800, 600)
    v.show()
    qtbot.waitExposed(v)
    src = parse_in_file(CAJA / "caja.in").model.source
    v.set_source(src, [], {"GRIDX": [-1.0, 1.0, 10], "GRIDY": [-1.0, 1.0, 10], "GRIDZ": [-1.0, 1.0, 10]})
    cam = v.camera
    cam.ortho = False
    cam.target = np.array([0.0, 0.0, 0.0])
    cam.distance = 40.0
    right, up, fwd = cam.basis()
    cam.target = cam.target + 30.0 * np.asarray(fwd)  # (el objetivo, 30 cm detrás de la rejilla)
    v._camera_changed()
    w, h = v.canvas.width(), v.canvas.height()
    (p0,), _z = cam.project(np.zeros((1, 3)), w, h)
    got = []
    v.gridChanged.connect(lambda g, fin: got.append(g))
    v._grid_drag = {"handle": "move", "g0": None, "p0": (float(p0[0]), float(p0[1]))}
    v._drag_grid(float(p0[0]) + 120.0, float(p0[1]), Qt.KeyboardModifier.NoModifier, True)
    g = got[-1]
    c = np.array([0.5 * (g.x[0] + g.x[1]), 0.5 * (g.y[0] + g.y[1]), 0.5 * (g.z[0] + g.z[1])])
    (p1,), _z = cam.project(c[None, :], w, h)
    assert abs((p1[0] - p0[0]) - 120.0) < 8.0 and abs(p1[1] - p0[1]) < 8.0


def test_a_dose_change_that_cannot_be_written_is_said_in_its_card(qtbot, tmp_path, monkeypatch):
    """«Resultados de la simulación»: un cambio del editor de la dosis que no se puede escribir se descartaba sin
    decir nada (en el panel «Simulación» sí se decía)."""
    import shutil

    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow
    from argus.ui.outputs_dialog import OutputsDialog
    from argus.ui.settings import app_settings

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    locate_fast.warmup()
    app_settings().clear()
    res = CORPUS / "resultados"
    for f in ("cilindro.in", "cilindro.geo"):
        shutil.copy2(res / "cilindro" / f, tmp_path / f)
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_file(str(tmp_path / "cilindro.in"))
    dlg = OutputsDialog(w, w.input, w.session)
    qtbot.addWidget(dlg)
    dlg.show()

    def bad(_doc):
        raise ValueError("GRIDR: el radio tiene que ser mayor que 0.")

    dlg.cards["dosis"].editor.apply.emit("Rejilla", bad)
    qtbot.waitUntil(lambda: dlg.cards["dosis"].message.isVisibleTo(dlg), timeout=5000)
    assert "el radio tiene que ser mayor que 0" in dlg.cards["dosis"].message.text()


def test_the_voxel_field_says_the_voxel_there_is(qtbot):
    """Con un eje que ya tiene los intervalos de más que caben, escribir un vóxel menor no cambia la rejilla (ni
    hay paso de deshacer) y el campo se quedaba con lo escrito, no con el vóxel que hay."""
    from argus.penhan.inputfile import parse_in_file
    from argus.ui.sim_outputs import DoseSection

    doc = parse_in_file(CAJA / "caja.in")
    sc.set_dose_grid(doc, sc.DoseGrid((10.0, 20.0, 101), (-5.0, 5.0, 11), (0.0, 5.0, 25)))
    s = DoseSection()
    qtbot.addWidget(s)
    s.edit = lambda text, fn: (fn(doc), s.set_context(doc, None, CAJA))
    s.set_context(doc, None, CAJA)
    field = s.voxel_size["x"]
    field.setText("0.001")
    field.setModified(True)
    field._commit()
    assert sc.dose_grid(doc).x == (10.0, 20.0, 101)
    assert field.value() == pytest.approx(10.0 / 101, rel=1e-5)


# -- el TAC ---------------------------------------------------------------------------------------------------------

def _ct_dialog(qtbot, folder):
    from argus.ui.ct_import_dialog import CtImportDialog

    dlg = CtImportDialog(None, None, None, folder)
    qtbot.addWidget(dlg)
    dlg.scan()
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    dlg._update_all()
    return dlg


def _water(p):
    hu = np.full(p[..., 0].shape, -1000.0)
    hu[p[..., 0] ** 2 + p[..., 1] ** 2 < 0.4 * np.abs(p[..., 0]).max() ** 2 + 1e-12] = 0.0
    return hu


def test_the_crop_of_a_ct_is_at_its_scale_and_too_much_memory_is_said(qtbot, tmp_path, monkeypatch):
    """El recorte admitía ±1 km (con un vóxel de 0,01 mm, 300 millones de vóxeles: ARGUS se quedaba sin memoria);
    ahora llega al TAC más su tamaño a cada lado, y si lo que se pide no cabe en la memoria libre, se dice antes."""
    pytest.importorskip("pydicom")
    from dicom_synth import write_series

    write_series(tmp_path / "dcm", _water, rows=40, cols=40, nslices=6, spacing=(1.0, 1.0), dz=2.0,
                 origin=(-19.5, -19.5, 0.0), description="Agua")
    dlg = _ct_dialog(qtbot, tmp_path / "dcm")
    lo, hi = dlg.vol.bounds()
    for i, a in enumerate("xyz"):
        assert dlg.crop_lo[a].minimum() >= lo[i] - 2 * 40.0 - 1 and dlg.crop_hi[a].maximum() <= hi[i] + 2 * 40.0 + 1
    import argus.ui.ct_import_dialog as cid

    monkeypatch.setattr(cid, "available_memory", lambda: 1000)
    assert any("memoria" in p for p in dlg.problems())


def test_a_micro_ct_of_half_a_micron_is_read_and_its_voxel_can_be_kept(qtbot, tmp_path):
    """Un micro-TAC con cortes de 0,5 µm: todos eran «cortes repetidos» (el umbral era 1 µm); y el vóxel del diálogo
    no bajaba de 0,01 mm, con tres decimales."""
    pytest.importorskip("pydicom")
    from argus.ct.dicom import read_series, scan_folder
    from dicom_synth import write_series

    sp = 0.0005
    write_series(tmp_path / "dcm", _water, rows=16, cols=16, nslices=6, spacing=(sp, sp), dz=sp,
                 origin=(-7.5 * sp, -7.5 * sp, 0.0), description="Micro")
    series = scan_folder(tmp_path / "dcm")
    vol = read_series(series[0])
    assert not any("repetidos" in p for p in series[0].problems) and vol is not None
    dlg = _ct_dialog(qtbot, tmp_path / "dcm")
    spin = dlg.voxel["x"]
    assert spin.minimum() <= sp / 10 and spin.decimals() >= 5
    spin.setValue(sp)
    assert spin.value() == pytest.approx(sp)
