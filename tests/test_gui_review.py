"""Paneles, diálogos y vistas (revisión de la 0.7): la sección de dosis tras arrastrar la rejilla, el
material escrito a mano, el zoom extremo, las asas de la rejilla sin fuente, la caja de la escena 3D con
materiales ocultos, el redondeo con la pantalla al 150 %, el aviso de un .mat rechazado y la vista 3D sin
geometría."""

import math
import os
import shutil
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"
SB = QMessageBox.StandardButton


@pytest.fixture
def case_dir(tmp_path):
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
        os.chmod(tmp_path / name, 0o644)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    msgs = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: msgs.append(("question", a[2])) or SB.Discard)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2])) or SB.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._msgs = msgs
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def test_dose_section_follows_a_grid_drag_in_the_slice(qtbot, window, case_dir):
    from argus.penhan import sections as sc
    from argus.ui.sim_outputs import DoseSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.sim_panel.frame(DoseSection).set_expanded(True)
    sec = w.sim_panel.section(DoseSection)
    g0 = sc.dose_grid(w.input.doc)
    new = sc.DoseGrid(**{**g0.__dict__, "z": (g0.z[0], g0.z[1] + 40.0, g0.z[2])}) if hasattr(g0, "__dict__") \
        else None
    if new is None:
        pytest.skip("rejilla sin atributos")
    w._on_grid_dragged(new, False)
    w._on_grid_dragged(new, True)  # al soltar: el corte aún se da por «arrastrando»
    assert sc.dose_grid(w.input.doc).z[1] == pytest.approx(g0.z[1] + 40.0)
    assert sec.hi["z"].value() == pytest.approx(g0.z[1] + 40.0)  # la sección no se queda con el valor de antes
    sec.nr.spin.setValue(sec.nr.spin.value() + 1)  # otro cambio en la sección no deshace el arrastre
    qtbot.wait(50)
    assert sc.dose_grid(w.input.doc).z[1] == pytest.approx(g0.z[1] + 40.0)


def test_material_typed_in_the_add_dialog(qtbot, window, case_dir, monkeypatch):
    from argus.ui.add_body_dialog import AddBodyDialog

    w = window
    w.open_file(case_dir / "maniqui.in")
    s = w.session
    dlg = AddBodyDialog(w, s, 1, np.array([0.0, 0.0, 250.0]), w.material_names, material=1,
                        extent_fn=w.element_extent)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.radius.setText("40")
    le = dlg.material.lineEdit()
    le.setFocus()
    le.selectAll()
    QTest.keyClicks(le, "5")
    QTest.keyClick(le, Qt.Key.Key_Return)
    assert dlg.material.findText("5") < 0 or dlg.material.itemData(dlg.material.findText("5")) is not None
    assert dlg.material_number() == 5
    dlg.material.setEditText("agua")
    dlg._check()
    assert not dlg._ok and "número del material" in dlg.status.text()


def test_slice_zoom_is_bounded(qtbot):
    from argus.session import open_geometry
    from argus.ui.slice_view import MAX_SCALE, MIN_SCALE, SliceViewWidget

    v = SliceViewWidget()
    qtbot.addWidget(v)
    v.resize(400, 300)
    v.set_session(open_geometry(CORPUS / "pengeomjar/sphere"))
    for _ in range(4000):
        v.zoom(1.25)
    assert v.scale == MAX_SCALE and all(math.isfinite(c) for c in v.center)
    for _ in range(8000):
        v.zoom(0.8)
    assert v.scale == MIN_SCALE and all(math.isfinite(c) for c in v.center)


def test_dose_grid_handles_work_with_the_source_hidden(qtbot):
    from argus.penhan import source_edit as se
    from argus.session import open_case
    from argus.ui.slice_view import SliceViewWidget

    case = open_case(CASE / "maniqui.in")
    v = SliceViewWidget()
    qtbot.addWidget(v)
    v.resize(800, 600)
    v.show()
    v.set_session(case.session)
    m = case.input_doc.model
    v.set_source(m.source, se.active_bodies(case.input_doc, case.session.geo), m.grids)
    v.fit()
    v.set_show_source(False)
    for _ in range(30):
        QApplication.processEvents()
        time.sleep(0.01)
    v.grab()
    h = v._handles.get("grid_r")
    assert h is not None and v._handle_at(h) == "grid_r"
    got = []
    v.gridChanged.connect(lambda g, fin: got.append(fin))
    p0 = QPoint(round(h.x()), round(h.y()))
    QTest.mousePress(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0)
    QTest.mouseMove(v, p0 + QPoint(40, 0))
    QTest.mouseRelease(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0 + QPoint(40, 0))
    assert got and got[-1] is True


def test_scene_box_includes_hidden_materials(qtbot):
    from argus.render import raytrace
    from argus.session import open_geometry
    from argus.ui.view3d import HIDDEN, View3D

    raytrace.warmup()
    s = open_geometry(CORPUS / "pengeomjar/onion")
    v = View3D(backend="cpu")
    qtbot.addWidget(v)
    v.resize(500, 400)
    v.show()
    v.set_session(s)
    lo0, hi0 = v.scene_lo.copy(), v.scene_hi.copy()
    v.set_material_visibility({21: HIDDEN})  # la capa de fuera
    v.set_session(s, keep_camera=True)  # como tras editar la geometría
    v.set_material_visibility({})
    assert np.allclose(v.scene_lo, lo0) and np.allclose(v.scene_hi, hi0)


def test_device_pixels_round_like_qt():
    from argus.ui.view3d import _qround

    assert [_qround(x) for x in (0.5, 1.5, 2.5, 616.5, 604.5)] == [1, 2, 3, 617, 605]


def test_rejected_mat_name_message_is_visible(qtbot, window, case_dir):
    from argus.ui.materials_panel import COL_FILE, ROLE_NUMBER

    w = window
    w.open_file(case_dir / "maniqui.in")
    mp = w.materials
    row = next(r for r in range(mp.table.rowCount())
               if mp.table.item(r, COL_FILE) is not None and mp.table.item(r, COL_FILE).data(ROLE_NUMBER) == 1)
    mp.table.item(row, COL_FILE).setText("agua líquida.mat")
    qtbot.wait(50)
    assert "✖" in mp.info.text() and "material 1" in mp.info.text()


def test_3d_view_without_geometry_shows_the_background(qtbot):
    from argus.render import raytrace
    from argus.session import open_geometry
    from argus.ui.view3d import View3D

    raytrace.warmup()
    v = View3D(backend="cpu")
    qtbot.addWidget(v)
    v.resize(300, 200)
    v.show()
    v.set_session(open_geometry(CORPUS / "pengeomjar/sphere"))
    qtbot.waitUntil(lambda: v.canvas._shown is not None, timeout=10000)
    v.set_session(None)
    qtbot.waitUntil(lambda: v.canvas._shown is None, timeout=5000)
