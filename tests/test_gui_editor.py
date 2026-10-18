"""El editor completo en la ventana (punto 4): geometría nueva, etiquetas, lados, implícitas, girar y mover
con el ratón, duplicar, clonar, envolver, y el .in que sigue a la geometría."""

import shutil

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QMenu, QMessageBox  # noqa: E402

from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"
STACK = CORPUS / "penhan/stack"


@pytest.fixture
def case_dir(tmp_path):
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def stack_dir(tmp_path):
    for f in STACK.iterdir():
        shutil.copy2(f, tmp_path / f.name)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    msgs = []
    answers = {"question": QMessageBox.StandardButton.Discard}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers["question"])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: msgs.append(a[2]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: msgs.append(a[2]) or QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._answers = answers
    w._msgs = msgs
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def dialogs(w, monkeypatch, fn):
    seen = []

    def run(dlg):
        seen.append(dlg)
        return fn(dlg)

    monkeypatch.setattr(w, "_exec_dialog", run)
    return seen


def menu_actions(w, monkeypatch):
    captured = {}

    def fake_exec(menu: QMenu, *args):
        acts = {}

        def walk(m, prefix=""):
            for a in m.actions():
                if a.menu() is not None:
                    walk(a.menu(), prefix + a.text() + " › ")
                elif a.text():
                    acts[prefix + a.text()] = a
        walk(menu)
        captured["actions"] = acts

    monkeypatch.setattr(w, "_exec_menu", fake_exec)
    return captured


def mats(w, pts):
    return list(w.session.locator.locate(np.asarray(pts, dtype=float))[1])


def test_new_geometry_from_scratch(qtbot, window, tmp_path, monkeypatch):
    w = window
    target = tmp_path / "nueva.geo"

    def fill(dlg):
        dlg.path_edit.setText(str(target))
        dlg.title_edit.setText("Maniquí de prueba")
        dlg.shape.sx.setText("20")
        dlg.name.setText("agua")
        return dlg._check()

    seen = dialogs(w, monkeypatch, fill)
    w.new_geometry()
    assert seen and target.is_file()
    assert w.session is not None and w.session.ok and w.session.path == target
    assert w.session.geo.materials() == [1]
    assert w.input is not None and w.input.doc.model.geomfn == "nueva.geo"  # y su .in nuevo
    assert "Maniquí de prueba" in target.read_text(encoding="latin-1")
    # Vacía: se abre con un encuadre de 20 cm y se le pueden añadir cosas.
    empty = tmp_path / "vacia.geo"

    def fill_empty(dlg):
        dlg.path_edit.setText(str(empty))
        dlg.start.setCurrentIndex(dlg.start.findData("empty"))
        dlg.make_in.setChecked(False)
        return dlg._check()

    w._answers["question"] = QMessageBox.StandardButton.Discard
    dialogs(w, monkeypatch, fill_empty)
    w.new_geometry()
    assert w.session.path == empty and w.session.geo.nbodys == 1 and w.input is None
    assert float(np.max(w.session.bounds[1])) == 10.0


def test_in_follows_the_numbering_and_undo_is_one_step(qtbot, window, stack_dir, monkeypatch):
    w = window
    w.open_file(stack_dir / "stack.in")
    before_in = w.input.doc.text()
    m2 = w.session.geo.find_body("2").index

    def fill(dlg):
        dlg.kind.setCurrentIndex(dlg.kind.findData("sphere"))
        dlg.container_box.setCurrentIndex(dlg.container_box.findData(m2))
        for ed, v in zip((dlg.cx, dlg.cy, dlg.cz), (0.0, 0.0, 0.003)):
            ed.setText(str(v))
        dlg.radius.setText("0.0008")
        dlg._check()
        return dlg._ok

    dialogs(w, monkeypatch, fill)
    w.add_body_dialog(m2, np.array([0.0, 0.0, 0.003]))
    text = w.input.doc.text()
    assert "DSMAX  3 0.0001" in text and "DSMAX  4 0.0002" in text and "EDBODY 4" in text
    assert "referencias por número actualizadas" in w.statusBar().currentMessage()
    assert "Añadir" in w.act_undo.text()
    w.undo()  # deshace el .in y la geometría a la vez
    assert w.input.doc.text() == before_in and w.session.geo.nbodys == 3
    w.redo()
    assert "DSMAX  4 0.0002" in w.input.doc.text() and w.session.geo.nbodys == 4


def test_rename_from_the_panel_and_the_in_labels(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    # Un SBODY por etiqueta en el .in (fuente extensa limitada al cubo).
    from argus.penhan import source_edit as se

    w.input.edit("fuente en el cubo", lambda d: (se.set_box(d, [10.0, 10.0, 10.0]),
                                                  se.set_active_bodies(d, w.session.geo, [1])))
    assert "'   1'" in w.input.doc.text()
    w.select_body(1)
    panel = w.geo_panel
    panel.label_edit.setText("CUBO")
    panel.label_edit.editingFinished.emit()
    qtbot.waitUntil(lambda: w.session.geo.find_body("CUBO") is not None, timeout=3000)
    assert "BODY    (CUBO)" in w.geo_ctrl.text and "'CUBO'" in w.input.doc.text()
    assert w.view.selected == w.session.geo.find_body("CUBO").index
    w.undo()
    assert w.session.geo.find_body("1") is not None and "'   1'" in w.input.doc.text()
    # Etiqueta no válida: no cambia nada.
    w.select_body(1)
    panel.label_edit.setText("A'B")
    panel.label_edit.editingFinished.emit()
    assert w.session.geo.find_body("1") is not None


def test_sides_surfaces_and_implicit_in_the_panel(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index
    # Recortar el cubo con un plano x = 100 (se queda con x < 100).
    def fill(dlg):
        dlg.rb_plane.setChecked(True)
        for ed, v in zip((dlg.px, dlg.py, dlg.pz), (100.0, 0.0, 250.0)):
            ed.setText(str(v))
        dlg._preset("x")
        dlg._check()
        import re

        m = re.search(r"(\d+),(\d) %", dlg.status.text())
        assert m and 68 <= float(f"{m.group(1)}.{m.group(2)}") <= 72  # se queda con ~70 % (x de −250 a 100)
        return dlg._ok

    dialogs(w, monkeypatch, fill)
    w.add_surface_dialog(cube)
    assert mats(w, [[150.0, 0, 250], [50.0, 0, 250]]) == [0, 1]
    w.select_body(w.session.geo.find_body("1").index)
    panel = w.geo_panel
    assert panel.table.rowCount() == 5
    # Cambiar el lado del plano nuevo: el cubo pasa a ser la parte x > 100 (dentro de la esfera, sin solapes).
    panel.table.selectRow(4)
    panel._on_flip()
    assert mats(w, [[150.0, 0, 250], [50.0, 0, 250]]) == [1, 0]
    # Pasar la esfera a forma implícita y agrandarla con AXX.
    w.select_body(w.session.geo.find_body("2").index)
    panel.table.selectRow(0)
    panel._on_to_implicit()
    panel.table.selectRow(0)
    assert panel.implicit_widget.isVisibleTo(panel) and "(implícita)" in panel.surface_note.text()
    ed = panel.coef_edits["AXX"]
    ed.setText("2.5e-8")
    ed.setModified(True)
    ed.editingFinished.emit()
    assert "    AXX=(+2.500000000000000E-08" in w.geo_ctrl.text
    assert mats(w, [[5500.0, 0, 0]]) == [0] and w.session.locator.locate(np.array([[5500.0, 0, 0]]))[0][0] == \
        w.session.geo.find_body("2").index


def test_overlapping_side_change_is_refused(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    from argus.pengeom.edit import Primitive, add_primitive

    # Dos cajas en la esfera; darle la vuelta a una la haría invadir a la otra.
    for c, m in (((1000.0, 0, 0), 1), ((1200.0, 0, 0), 2)):
        w.edit_geometry("caja", lambda doc, g, c=c, m=m: add_primitive(
            doc, g, Primitive("box", center=c, sides=(100.0, 100.0, 100.0)), m, g.find_body("2").index))
    kb = w.session.geo.find_body("3").index
    w.select_body(kb)
    panel = w.geo_panel
    panel.table.selectRow(0)
    before = w.geo_ctrl.text
    panel._on_flip()
    assert w.geo_ctrl.text == before
    assert any("solapan" in m for m in w._msgs)


def test_rotate_and_move_a_module_with_the_mouse(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.set_view_mode("2d")
    qtbot.wait(50)  # el reencuadre tras cambiar de modo
    cube = w.session.geo.find_body("1").index
    w.convert_to_module(cube)
    cube = w.session.geo.find_body("1").index
    w.select_body(cube)
    view = w.view
    w.set_plane("XZ")
    view.set_depth(0.0)
    view.fit()
    assert view.module_handle is not None and view.module_handle["kb"] == cube
    view.repaint()
    qtbot.waitUntil(lambda: "mod_move" in view._handles, timeout=3000)
    c = view._handles["mod_move"]
    # Mover 100 cm en x arrastrando la cruz del centro... se saldría de la esfera no, pero se sale del sitio: vale.
    dx = 100.0 / view.scale
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=c.toPoint())
    for f in (0.3, 0.7, 1.0):
        qtbot.mouseMove(view, pos=QPointF(c.x() + f * dx, c.y()).toPoint())
    qtbot.waitUntil(lambda: w.geo_ctrl.in_live, timeout=3000)  # en vivo (los movimientos se agrupan)
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPointF(c.x() + dx, c.y()).toPoint())
    assert not w.geo_ctrl.in_live and w.geo_ctrl.dirty
    lo, hi = w.element_extent(w.session.geo.find_body("1").index)
    assert abs(lo[0] - (-250 + 100)) < 2 and abs(hi[0] - (250 + 100)) < 2  # redondeado al paso del ratón
    assert "Mover" in w.act_undo.text()
    w.undo()
    lo, hi = w.element_extent(w.session.geo.find_body("1").index)
    assert lo[0] == -250.0 and hi[0] == 250.0
    # Girar 90° con el anillo: el cubo (simétrico) sigue ocupando lo mismo.
    w.select_body(w.session.geo.find_body("1").index)
    view.repaint()
    knob, center = view._handles["mod_rot"], view._handles["mod_move"]
    r = np.hypot(knob.x() - center.x(), knob.y() - center.y())
    end = QPointF(center.x() + r * np.cos(np.radians(135)), center.y() - r * np.sin(np.radians(135)))
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=knob.toPoint())
    qtbot.mouseMove(view, pos=end.toPoint())
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton, pos=end.toPoint())
    assert "Girar" in w.act_undo.text()
    lo, hi = w.element_extent(w.session.geo.find_body("1").index)
    assert np.allclose(lo, [-250, -250, 0], atol=1e-6) and np.allclose(hi, [250, 250, 500], atol=1e-6)


def test_drag_that_would_overlap_is_undone(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    from argus.pengeom.edit import Primitive, add_primitive

    w.edit_geometry("módulo", lambda doc, g: add_primitive(
        doc, g, Primitive("box", center=(600.0, 0, 250), sides=(100.0, 100.0, 100.0)), 2, g.find_body("2").index,
        module=True))
    kb = w.session.geo.find_body("3").index
    w.select_body(kb)
    before = w.geo_ctrl.text
    # Llevarlo hasta el cubo (se solaparía): al soltar, vuelve a su sitio.
    w._on_module_dragged(kb, ("move", np.array([-200.0, 0, 0])), False)
    qtbot.waitUntil(lambda: w.geo_ctrl.in_live, timeout=3000)
    w._on_module_dragged(kb, ("move", np.array([-400.0, 0, 0])), True)
    assert not w.geo_ctrl.in_live and w.geo_ctrl.text == before
    assert any("solapar" in m for m in w._msgs)


def test_rotate_menu_duplicate_clone_and_materialize(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    from argus.pengeom.edit import Primitive, add_primitive

    w.edit_geometry("módulo", lambda doc, g: add_primitive(
        doc, g, Primitive("box", center=(600.0, 0, 250), sides=(100.0, 40.0, 200.0)), 2, g.find_body("2").index,
        module=True))
    kb = w.session.geo.find_body("3").index
    captured = menu_actions(w, monkeypatch)
    w._on_context_menu(None, np.array([600.0, 0.0, 250.0]), kb)
    acts = captured["actions"]
    rot = next(a for t, a in acts.items() if "+90° alrededor de z" in t)
    rot.trigger()
    lo, hi = w.element_extent(w.session.geo.find_body("3").index)
    assert np.allclose(hi - lo, [40, 100, 200], atol=1e-6)
    # Duplicar: dos clones pegados por la cara +y.
    def fill(dlg):
        dlg.count.setValue(2)
        dlg.face.setCurrentIndex(dlg.face.findData("+y"))
        dlg.rb_clone.setChecked(True)
        dlg._check()
        return dlg._ok

    dialogs(w, monkeypatch, fill)
    w.duplicate_dialog(w.session.geo.find_body("3").index)
    assert w.geo_ctrl.text.count("CLONE") == 2
    assert mats(w, [[600.0, 100.0, 250], [600.0, 200.0, 250], [600.0, 260.0, 250]]) == [2, 2, 0]
    clone = w.view.selected
    assert w.session.geo.body(clone).origin == "clone"
    panel = w.geo_panel
    assert panel.box_body.title() == "Clon" and panel.btn_materialize.isVisibleTo(panel)
    # Convertir el clon seleccionado en copia independiente: nada cambia de sitio.
    pts = np.random.default_rng(0).uniform([450, -100, 100], [750, 400, 400], (4000, 3))
    before = mats(w, pts)
    w.materialize_clone(clone)
    assert w.geo_ctrl.text.count("CLONE") == 1 and mats(w, pts) == before


def test_wrap_dialog_and_overlap_search(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    from argus.pengeom.edit import Primitive, add_primitive

    w.edit_geometry("caja", lambda doc, g: add_primitive(
        doc, g, Primitive("box", center=(700.0, 0, 250), sides=(200.0, 200.0, 200.0)), 2, g.find_body("2").index))
    cube = w.session.geo.find_body("1").index
    a = w.session.geo.find_body("3").index
    pts = np.random.default_rng(1).uniform(-1000, 1000, (5000, 3))
    before = mats(w, pts)

    def fill(dlg):
        for i in range(dlg.items.count()):
            dlg.items.item(i).setCheckState(Qt.CheckState.Checked)
        dlg._fit()
        dlg._check()
        return dlg._ok

    seen = dialogs(w, monkeypatch, fill)
    w.wrap_dialog(cube)
    assert seen and w.geo_ctrl.text.count("MODULE  (") >= 1
    m = w.session.geo.body(w.view.selected)
    assert m.is_module and {w.session.geo.find_body("1").index, w.session.geo.find_body("3").index} <= set(m.daughters)
    assert mats(w, pts) == before
    del a
    # Buscar solapes: no hay.
    w._msgs.clear()
    w.check_overlaps_now()
    assert w._msgs and "ningún solape" in w._msgs[-1]


def test_context_menu_offers_the_new_editor_actions(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    captured = menu_actions(w, monkeypatch)
    w._on_context_menu(None, np.array([0.0, 0.0, 250.0]), 1)
    texts = list(captured["actions"])
    for part in ("Duplicar", "Envolver", "Añadir una superficie", "Cambiar la etiqueta", "Convertir"):
        assert any(part in t for t in texts), part
    assert not any("Girar" in t for t in texts)  # un cuerpo no se gira (se convierte antes en módulo)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: ("C1", True))
    w.rename_dialog(1)
    assert w.session.geo.find_body("C1") is not None


def test_saving_the_geometry_with_another_name_updates_geomfn(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    new = case_dir / "maniqui2.geo"
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **k: (str(new), ""))
    assert w.save_geometry_as()
    assert new.is_file() and w.input.doc.model.geomfn == "maniqui2.geo" and w.input.dirty
    assert "GEOMFN" in w.act_undo.text()
    # En otra carpeta: aviso y el .in no cambia.
    other = case_dir / "otra"
    other.mkdir()
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                        lambda *a, **k: (str(other / "g.geo"), ""))
    w._msgs.clear()
    assert w.save_geometry_as()
    assert w.input.doc.model.geomfn == "maniqui2.geo" and any("otra carpeta" in m for m in w._msgs)
