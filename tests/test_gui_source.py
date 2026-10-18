"""Interfaz (F5): la fuente del .in en el corte, arrastres, panel, deshacer y guardado."""

import math
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from argus.penhan.inputfile import parse_in_file  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"


@pytest.fixture
def case_dir(tmp_path):
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    # Nunca un diálogo modal en las pruebas (p. ej. «¿guardar los cambios?» al cerrar).
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Save)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def settle(qtbot, w):
    qtbot.waitUntil(w.view.is_idle, timeout=15000)
    w.view.repaint()  # dibuja la fuente y calcula las asas


def drag(view, start: QPointF, end: QPointF, steps: int = 5, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.mousePress(view, Qt.MouseButton.LeftButton, modifiers, start.toPoint())
    for i in range(1, steps + 1):
        p = start + (end - start) * (i / steps)
        QTest.mouseMove(view, p.toPoint())
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifiers, end.toPoint())


def test_open_input_shows_source(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    assert w.session is not None and w.session.ok
    assert w.input is not None and w.input.name == "maniqui.in"
    settle(qtbot, w)
    assert w.view.source.position == (0.0, 0.0, -100.0)
    assert {"source", "direction"} <= set(w.view._handles)
    assert "maniqui.in" in w.windowTitle() and "*" not in w.windowTitle()
    assert w.source_panel.particle.currentData() == 4
    assert w.source_panel.pos[2].text() == "-100.0"


def test_open_geometry_attaches_its_input(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "cubo-esfera.geo")
    assert w.input is not None and w.input.name == "maniqui.in"


def test_drag_source_undo_redo_and_save(qtbot, window, case_dir):
    w = window
    path = case_dir / "maniqui.in"
    original = path.read_bytes()
    w.open_file(path)
    settle(qtbot, w)
    view = w.view
    start = view._handles["source"]
    drag(view, start, start + QPointF(60, 0))
    x, y, z = w.input.doc.model.source.position
    assert x > 0 and y == 0.0 and z == -100.0
    assert w.input.dirty and w.windowTitle().startswith("ARGUS — maniqui.in*")
    assert w.input.stack.count() == 1  # todo el arrastre es un solo paso
    w.act_undo.trigger()
    assert w.input.doc.model.source.position == (0.0, 0.0, -100.0)
    assert w.input.doc.text().encode("latin-1") == original
    assert not w.input.dirty
    w.act_redo.trigger()
    assert w.input.doc.model.source.position == (x, 0.0, -100.0)
    assert w.save_input()
    assert (case_dir / "maniqui.in.bak").read_bytes() == original
    saved = parse_in_file(path)
    assert saved.ok and saved.model.source.position == (x, 0.0, -100.0)
    changed = [a for a, b in zip(original.decode("latin-1").splitlines(), path.read_text("latin-1").splitlines())
               if a != b]
    assert len(changed) == 1 and changed[0].startswith("SPOSIT")
    assert not w.input.dirty and "*" not in w.windowTitle()


def test_drag_direction_in_xz_plane(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    settle(qtbot, w)
    view = w.view
    c = view._handles["source"]
    tip = view._handles["direction"]
    drag(view, tip, c + QPointF(80, 0))  # hacia +x en el plano XZ
    assert w.input.doc.model.source.cone == (90.0, 0.0, 0.0)
    settle(qtbot, w)
    tip = view._handles["direction"]
    drag(view, tip, c + QPointF(-50, -50))  # 45° entre −x y +z
    th, ph, al = w.input.doc.model.source.cone
    assert (th, ph, al) == (45.0, 180.0, 0.0)


def test_escape_cancels_drag(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    settle(qtbot, w)
    view = w.view
    start = view._handles["source"]
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start.toPoint())
    QTest.mouseMove(view, (start + QPointF(40, 30)).toPoint())
    assert w.input.doc.model.source.position != (0.0, 0.0, -100.0)
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start.toPoint())
    assert w.input.doc.model.source.position == (0.0, 0.0, -100.0)
    assert w.input.stack.count() == 0 and not w.input.dirty


def test_box_handle_resizes_extended_source(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    from argus.penhan import source_edit as se

    w.input.edit("caja", lambda d: se.set_box(d, [100.0, 100.0, 50.0]))
    view = w.view
    view.center = (0.0, -100.0)  # la fuente, a 0.5 cm por píxel
    view.scale = 0.5
    view._request(quick=True)
    settle(qtbot, w)
    corner = view._handles["box"]
    c = view._handles["source"]
    drag(view, corner, c + (corner - c) * 2.0)
    sx, sy, sz = w.input.doc.model.source.box
    assert sx == pytest.approx(200.0, rel=0.05) and sz == pytest.approx(100.0, rel=0.05) and sy == 100.0


def test_panel_edits(qtbot, window, tmp_path):
    w = window
    for name in ("disc.in", "disc.geo"):
        shutil.copy2(CORPUS / "penhan/disc" / name, tmp_path / name)
    w.open_file(tmp_path / "disc.in")
    panel = w.source_panel
    panel.energy.setFocus()
    panel.energy.setText("150 MeV")
    panel.energy.setModified(True)
    panel.energy.editingFinished.emit()
    assert w.input.doc.model.source.energy == 150e6
    assert w.input.doc.first("SENERG").values == [150e6]
    panel.pos[0].setText("2,5")
    panel.pos[0].setModified(True)
    panel.pos[0].editingFinished.emit()
    assert w.input.doc.model.source.position == (2.5, 0.0, 0.0)
    panel.emission.setCurrentIndex(panel.emission.findData("cone"))
    panel.emission.activated.emit(panel.emission.currentIndex())
    assert w.input.doc.model.source.cone == (0.0, 0.0, 10.0)
    panel.preset_buttons[0].click()  # +X
    assert w.input.doc.model.source.cone == (90.0, 0.0, 10.0)
    panel.rb_box.click()
    assert w.input.doc.model.source.extended
    item = panel.body_list.item(0)
    item.setCheckState(Qt.CheckState.Checked)
    qtbot.waitUntil(lambda: bool(w.input.doc.model.source.bodies), timeout=3000)
    assert panel.status.text().count("✔") == 1
    assert w.input.stack.count() == 6
    for _ in range(6):
        w.act_undo.trigger()
    assert not w.input.dirty


def test_new_input_for_geometry(qtbot, window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w = window
    shutil.copy2(CASE / "cubo-esfera.geo", tmp_path / "cubo-esfera.geo")
    w.open_file(tmp_path / "cubo-esfera.geo")
    assert w.input is None
    w.new_input()
    assert w.input is not None and w.input.dirty and w.input.doc.ok
    src = w.input.doc.model.source
    assert src.position[2] < 0.0  # delante de la geometría (que empieza en z = 0)
    target = tmp_path / "cubo-esfera.in"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert w.save_input()
    assert target.is_file() and not (tmp_path / "cubo-esfera.in.bak").exists()
    doc = parse_in_file(target)
    assert doc.ok and doc.model.geomfn == "cubo-esfera.geo"
    assert w.input.name == "cubo-esfera.in" and not w.input.dirty


def test_context_menu_aim_and_place(qtbot, window, case_dir, monkeypatch):
    from PySide6.QtWidgets import QMenu

    w = window
    w.open_file(case_dir / "maniqui.in")
    captured = {}

    def fake_exec(menu: QMenu, *args):
        captured["actions"] = {a.text(): a for a in menu.actions()}

    monkeypatch.setattr(w, "_exec_menu", fake_exec)
    import numpy as np

    w._on_context_menu(None, np.array([100.0, 0.0, 0.0]), 0)
    aim = next(a for t, a in captured["actions"].items() if t.startswith("Apuntar"))
    aim.trigger()
    th, ph, al = w.input.doc.model.source.cone
    assert th == pytest.approx(45.0) and ph == 0.0 and al == 0.0
    w._on_context_menu(None, np.array([5.0, 0.0, -20.0]), 0)
    place = next(a for t, a in captured["actions"].items() if t.startswith("Colocar"))
    place.trigger()
    assert w.input.doc.model.source.position == (5.0, 0.0, -20.0)


# ---------------------------------------------------------------------------
# Materiales (F7)
# ---------------------------------------------------------------------------

def material_row(panel, number: int) -> int:
    from argus.ui.materials_panel import COL_N

    for r in range(panel.table.rowCount()):
        if panel.table.item(r, COL_N).text() == str(number):
            return r
    raise AssertionError(f"no hay fila para el material {number}")


def test_materials_panel_names_and_txt(qtbot, window, case_dir):
    from argus.penhan.materials import TXT_MARK
    from argus.ui.materials_panel import COL_FILE, COL_NAME

    w = window
    w.open_file(case_dir / "maniqui.in")
    panel = w.materials
    r = material_row(panel, 1)
    assert panel.table.item(r, COL_FILE).text() == "water.mat"
    panel.table.item(r, COL_FILE).setText("agua.mat")
    qtbot.waitUntil(lambda: w.input.doc.model.materials[0].fname == "agua.mat", timeout=3000)
    r = material_row(panel, 1)
    panel.table.item(r, COL_NAME).setText("Water, liquid (PENELOPE 278)")
    qtbot.waitUntil(lambda: "PENELOPE 278" in w.input.doc.first("MFNAME").comment, timeout=3000)
    panel.table.item(material_row(panel, 1), COL_FILE).setText("nombre con espacios.mat")  # no válido
    qtbot.wait(50)
    assert w.input.doc.model.materials[0].fname == "agua.mat"
    assert w.input.stack.count() == 2
    assert w.save_input()
    txt = case_dir / "materiales.txt"
    text = txt.read_text(encoding="utf-8-sig")
    assert text.startswith(TXT_MARK + " de maniqui.in")
    assert "agua.mat" in text and "Water, liquid (PENELOPE 278)" in text and "FALTA" in text
    saved = parse_in_file(case_dir / "maniqui.in")
    assert saved.model.materials[0].fname == "agua.mat"


def test_material_colors_are_remembered(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    settle(qtbot, w)
    w.materials.colors = {1: "#ff0000"}
    w.materials.colorsChanged.emit({1: "#ff0000"})
    assert tuple(w.view._lut[1]) == (255, 0, 0)
    w.open_file(case_dir / "maniqui.in")  # se vuelve a abrir: el color se recuerda
    assert w.material_colors == {1: "#ff0000"} and tuple(w.view._lut[1]) == (255, 0, 0)
    w.materials._reset_colors()
    assert w.material_colors == {} and tuple(w.view._lut[1]) != (255, 0, 0)


# ---------------------------------------------------------------------------
# Vista 3D en la ventana (F3)
# ---------------------------------------------------------------------------

def settle3d(qtbot, w):
    qtbot.waitUntil(lambda: w.view.is_idle() and w.view3d.is_idle(), timeout=30000)


def test_view_modes_and_3d_session(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.set_view_mode("split")
    settle3d(qtbot, w)
    assert w.view.isVisible() and w.view3d.isVisible()
    v3 = w.view3d
    assert v3.session is w.session and v3.source is w.input.doc.model.source
    assert v3.slice_plane is w.view.plane  # el plano del corte 2D se ve en 3D
    w.set_view_mode("3d")
    assert not w.view.isVisible() and v3.isVisible()
    w.set_view_mode("2d")
    assert w.view.isVisible() and not v3.isVisible()
    w.mode_actions["split"].trigger()
    assert w.view.isVisible() and v3.isVisible()
    # Seleccionar en 3D selecciona en el árbol y en el corte.
    v3.bodyPicked.emit(1)
    assert w.view.selected == 1 and v3.selected == 1
    # Cambiar el corte 2D mueve el plano dibujado en 3D.
    w.set_plane("XY")
    assert v3.slice_plane.name == "XY"


def test_context_menu_3d(qtbot, window, case_dir, monkeypatch):
    import numpy as np
    from PySide6.QtWidgets import QMenu

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.set_view_mode("split")
    settle3d(qtbot, w)
    captured = {}

    def fake_exec(menu: QMenu, *args):
        acts = {}
        for a in menu.actions():
            acts[a.text()] = a
            if a.menu() is not None:
                for b in a.menu().actions():
                    acts[a.text() + " / " + b.text()] = b
        captured["actions"] = acts

    monkeypatch.setattr(w, "_exec_menu", fake_exec)
    v3 = w.view3d
    # Clic derecho sobre la cara x = -250 del cubo, mirando hacia +x.
    o, d = np.array([-1000.0, 0.0, 250.0]), np.array([1.0, 0.0, 0.0])
    t, kb, ks = v3.tracer.first_visible(o, d)
    info = {"point": o + t * d, "body": kb, "surface": ks, "t": t, "origin": o, "direction": d, "mat": 1}
    w._on_context_menu_3d(None, info)
    acts = captured["actions"]
    next(a for k, a in acts.items() if k.startswith("Apuntar")).trigger()
    th, ph, al = w.input.doc.model.source.cone
    assert al == 0.0 and th == pytest.approx(math.degrees(math.atan2(250, 350)))  # desde (0, 0, -100)
    w._on_context_menu_3d(None, info)
    next(a for k, a in captured["actions"].items() if k.startswith("Colocar la fuente dentro")).trigger()
    assert w.input.doc.model.source.position == (0.0, 0.0, 250.0)  # centro del tramo dentro del cubo
    w._on_context_menu_3d(None, info)
    next(a for k, a in captured["actions"].items() if k.startswith("Llevar el corte 2D")).trigger()
    assert w.view.plane.depth == pytest.approx(0.0)
    w._on_context_menu_3d(None, info)
    next(a for k, a in captured["actions"].items() if k.endswith("/ Translúcido")).trigger()
    assert v3.material_visibility == {1: 90}
    assert "translúcido" in w.materials.table.item(material_row(w.materials, 1), 3).text()
    w._on_context_menu_3d(None, info)
    next(a for k, a in captured["actions"].items() if k.startswith("Ocultar")).trigger()
    assert v3.hidden_bodies == {1}
    w._on_context_menu_3d(None, info)
    next(a for k, a in captured["actions"].items() if k.startswith("Mostrar todos")).trigger()
    assert not v3.hidden_bodies and not v3.material_visibility
    # Sin nada debajo del cursor: solo encuadrar.
    w._on_context_menu_3d(None, None)
    assert "Encuadrar" in captured["actions"]


def test_materials_panel_visibility_column(qtbot, window, case_dir):
    from argus.ui.materials_panel import COL_VIS

    w = window
    w.open_file(case_dir / "maniqui.in")
    panel = w.materials
    r = material_row(panel, 1)
    assert "opaco" in panel.table.item(r, COL_VIS).text()
    panel._on_click(r, COL_VIS)
    assert w.view3d.material_visibility == {1: 90}
    panel._on_click(material_row(panel, 1), COL_VIS)
    assert w.view3d.material_visibility == {1: 0}
    assert "oculto" in panel.table.item(material_row(panel, 1), COL_VIS).text()
    panel._on_click(material_row(panel, 1), COL_VIS)
    assert w.view3d.material_visibility == {}
