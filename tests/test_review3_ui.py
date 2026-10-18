"""Interfaz (revisión 3): números no finitos, geometría rota por un PARINP, reencuadres al restaurar la ventana,
arrastres interrumpidos por un menú, atajos de una tecla en los paneles, resaltado de los detectores, cuerpos
ocultos en 3D y selección tras editar (por identidad, no por etiqueta), Duplicar en el nivel superior, barra de
estado al deshacer, referencias a las copias de un clon y Supr en lo que no se puede eliminar."""

import math
import os
import shutil
import warnings

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QShowEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox  # noqa: E402

from argus.penhan import sections as sc  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"
STACK = CORPUS / "penhan/stack"
SB = QMessageBox.StandardButton


@pytest.fixture
def case_dir(tmp_path):
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
        os.chmod(tmp_path / name, 0o644)
    return tmp_path


@pytest.fixture
def stack_dir(tmp_path):
    for f in STACK.iterdir():
        shutil.copy2(f, tmp_path / f.name)
        os.chmod(tmp_path / f.name, 0o644)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    msgs = []
    answers = {"question": SB.No, "queue": []}

    def question(*a, **k):
        msgs.append(("question", a[2]))
        return answers["queue"].pop(0) if answers["queue"] else answers["question"]

    monkeypatch.setattr(QMessageBox, "question", question)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2])) or SB.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._msgs = msgs
    w._answers = answers
    dialogs = []
    w._dialogs = dialogs
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def _focus(qtbot, widget):
    win = widget.window()
    win.activateWindow()
    widget.setFocus()
    if not widget.hasFocus() and hasattr(QApplication, "setActiveWindow"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            QApplication.setActiveWindow(win)
        widget.setFocus()
    qtbot.waitUntil(widget.hasFocus, timeout=2000)


def _type(ed, text):
    ed.setText(text)
    ed.setModified(True)
    ed._commit()


def _no_dialogs(w, monkeypatch):
    """Los diálogos no se abren: se anotan (y se cancelan)."""
    monkeypatch.setattr(w, "_exec_dialog", lambda dlg: w._dialogs.append(type(dlg).__name__) or False)
    monkeypatch.setattr(w, "_exec_menu", lambda menu, *a: w._dialogs.append("menu"))


def _write(path, text):
    path.write_bytes(text.encode("latin-1"))
    return path


# ---------------------------------------------------------------------------------------------------------------
# 1. Números no finitos
# ---------------------------------------------------------------------------------------------------------------
def test_number_parsers_reject_non_finite_values():
    from argus.ui.source_panel import parse_energy, parse_number

    for text in ("1e400", "-1e400", "1E999", "1d400", "9" * 400):
        with pytest.raises(ValueError, match="demasiado grande"):
            parse_number(text)
    for text in ("1e400", "1e400 MeV", "-1e999 keV"):
        with pytest.raises(ValueError, match="demasiado grande"):
            parse_energy(text)
    assert parse_number("1e308") == 1e308 and parse_number("-2,5") == -2.5
    assert parse_energy("100 MeV") == 1e8


def test_overflowing_numbers_typed_in_the_panels_are_rejected(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    sp = w.source_panel
    _type(sp.pos[0], "1e400")
    sp.flush_pending()
    assert w.input.doc.first("SPOSIT").values == [0.0, 0.0, -100.0]
    assert "demasiado grande" in sp.pos[0].toolTip() and sp.pos[0].text() == "0.0"
    w.set_plane("YZ")  # antes: la profundidad del corte pasaba a ser Inf
    assert math.isfinite(w.view.plane.depth)
    # Colocación de un módulo y ángulo de giro del panel Editar.
    cube = w.session.geo.find_body("1").index
    w.convert_to_module(cube)
    cube = w.session.geo.find_body("1").index
    w.select_body(cube)
    text = w.geo_ctrl.text
    gp = w.geo_panel
    _type(gp.transform_edits["X-SHIFT"], "1e400")
    _type(gp.rot_angle, "1e400")
    assert gp.rot_angle.value() == 90.0
    gp.btn_rotate.click()  # gira los 90° que se ven, no Inf
    assert w.geo_ctrl.text != text and "Girar" in w.act_undo.text()
    assert not [m for k, m in w._msgs if "no válido" in m]


def test_rotation_with_a_non_finite_angle_does_nothing(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index
    w.convert_to_module(cube)
    cube = w.session.geo.find_body("1").index
    text = w.geo_ctrl.text
    for angle in (math.inf, -math.inf, math.nan):
        w.rotate_element(cube, [0.0, 0.0, 1.0], angle)
    assert w.geo_ctrl.text == text


@pytest.mark.parametrize("value", ["Inf", "NaN", "1e999"])
def test_non_finite_source_position_and_the_slice_plane(qtbot, window, case_dir, value):
    p = case_dir / "maniqui.in"
    _write(p, p.read_bytes().decode("latin-1").replace("SPOSIT 0.0 0.0 -100.0", f"SPOSIT {value} 0.0 -100.0"))
    w = window
    w.set_view_mode("2d")
    w.open_file(p)
    for name in ("YZ", "XY", "XZ", "YZ"):
        w.set_plane(name)
        lo, hi = w.view.depth_range()
        assert math.isfinite(w.view.plane.depth) and math.isfinite(lo) and math.isfinite(hi)
        assert math.isfinite(w.depth_spin.value()) and 0 <= w.depth_slider.value() <= 1000
    for pos in (0, 500, 1000):
        w.depth_slider.setValue(pos)
        assert math.isfinite(w.view.plane.depth)
    w.view.setFocus()
    QTest.keyClick(w.view, Qt.Key.Key_PageUp)
    assert math.isfinite(w.view.plane.depth)
    w.view.set_depth(math.inf)  # ni desde fuera
    w.view.set_depth(math.nan)
    assert math.isfinite(w.view.plane.depth)


def test_non_finite_dose_grid_in_the_file(qtbot, window, case_dir, qtlog):
    p = case_dir / "maniqui.in"
    text = p.read_bytes().decode("latin-1")
    text = text.replace("GRIDZ  0.0 9.0E+0 200", "GRIDZ  -1e999 9.0E+0 200").replace("GRIDR  15.0 40", "GRIDR  Inf 40")
    _write(p, text)
    w = window
    w.view3d.use_backend("cpu")
    w.set_view_mode("split")
    w.open_file(p)
    for name in ("XY", "XZ", "YZ"):
        w.set_plane(name)
        w.show_dose_grid()  # «Ver en el corte»
        assert math.isfinite(w.view.plane.depth)
        assert 0 <= w.depth_slider.value() <= 1000
        w.view.repaint()
        w.view3d.overlay.repaint()
    assert not [r for r in qtlog.records if "NaN" in r.message or "nan" in r.message]


# ---------------------------------------------------------------------------------------------------------------
# 2. Un PARINP que GEOMIN no acepta: la geometría se ve rota y no se edita
# ---------------------------------------------------------------------------------------------------------------
def test_parinp_that_breaks_the_geometry_disables_editing(qtbot, window, stack_dir, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    w = window
    w.open_file(stack_dir / "stack.in")
    w.select_body(2)
    assert w.geo_panel.kb == 2 and w.act_add_body.isEnabled()
    vals = sc.parinp(w.input.doc)
    vals[4] = 0.0  # radio 0: X-SCALE < 1e-15
    w.input.edit("PARINP", lambda doc: sc.set_parinp(doc, vals))
    assert not w.session.ok
    gp = w.geo_panel
    assert gp.kb is None and not gp.box_body.isVisible() and "Sin geometría" in gp.title.text()
    assert "Módulo" not in w.props.toPlainText()
    for a in (w.act_add_body, w.act_delete_body, w.act_duplicate, w.act_wrap):
        assert not a.isEnabled(), a.text()
    assert w.act_save_geo_as.isEnabled() and w.act_undo.isEnabled()
    _no_dialogs(w, monkeypatch)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("x", True))
    before = w.geo_ctrl.text
    w.act_add_body.trigger()
    w.add_body_dialog(2)
    w.add_body_dialog(None)
    w.delete_body(2)
    w.duplicate_dialog(2)
    w.wrap_dialog(2)
    w.convert_to_module(1)
    w.rotate_element(2, [0.0, 0.0, 1.0], 90.0)
    w.glue_module_dialog(2)
    w.add_surface_dialog(1)
    w.materialize_clone(2)
    w.rename_dialog(1)
    gp.btn_add.click()
    gp._on_rotate()
    w._on_tree_menu(QPoint(5, 5))
    assert w._dialogs == [] and w.geo_ctrl.text == before
    assert not [m for k, m in w._msgs if k == "question"]
    # Deshacer el PARINP: todo vuelve.
    w.undo()
    assert w.session.ok and w.act_add_body.isEnabled()
    w.select_body(2)
    assert w.geo_panel.kb == 2 and w.act_delete_body.isEnabled()


# ---------------------------------------------------------------------------------------------------------------
# 3. Reencuadres
# ---------------------------------------------------------------------------------------------------------------
def _view_state(w):
    cam = w.view3d.camera
    return w.view.scale, tuple(w.view.center), cam.distance, tuple(np.asarray(cam.target, float))


def test_restoring_the_window_keeps_zoom_and_camera(qtbot, window, case_dir):
    w = window
    w.set_view_mode("split")
    w.open_file(case_dir / "maniqui.in")
    qtbot.wait(50)
    w.view.zoom(0.2)
    w.view.center = (w.view.center[0] + 3.0, w.view.center[1] - 2.0)
    w.view3d.camera.zoom(0.3)
    w.view3d.look_at([1.0, 2.0, 3.0])
    before = _view_state(w)
    w.showMinimized()
    qtbot.wait(30)
    w.showNormal()
    qtbot.wait(50)
    assert _view_state(w) == before
    w.showEvent(QShowEvent())  # lo que llega al restaurar la ventana
    qtbot.wait(50)
    assert _view_state(w) == before


def test_view_mode_keys_do_not_refit_views_already_fitted(qtbot, window, case_dir):
    w = window
    w.set_view_mode("2d")
    w.open_file(case_dir / "maniqui.in")
    qtbot.wait(50)
    w.view.zoom(0.25)
    s2d = (w.view.scale, tuple(w.view.center))
    w.mode_actions["3d"].trigger()  # la vista 3D no se había encuadrado a la vista: se encuadra
    qtbot.wait(50)
    cam = w.view3d.camera
    fitted = (cam.distance, np.array(cam.target, dtype=float))
    w.view3d.fit()
    assert cam.distance == pytest.approx(fitted[0]) and np.allclose(cam.target, fitted[1])
    cam.zoom(0.5)
    before = _view_state(w)
    for mode in ("split", "2d", "3d", "split"):
        w.mode_actions[mode].trigger()
        qtbot.wait(50)
        assert _view_state(w) == before, mode
    assert (w.view.scale, tuple(w.view.center)) == s2d


# ---------------------------------------------------------------------------------------------------------------
# 4. Arrastres interrumpidos (un menú contextual se queda con el botón soltado)
# ---------------------------------------------------------------------------------------------------------------
def _mouse(v, kind, pos, button, buttons):
    ev = QMouseEvent(kind, QPointF(pos), v.mapToGlobal(QPointF(pos)), button, buttons,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(v, ev)


def _context(v, pos):
    p = QPointF(pos).toPoint()
    QApplication.sendEvent(v, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, p, v.mapToGlobal(p)))


L, N = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
MOVE, PRESS, RELEASE = QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease


def _ready_source(qtbot, w, case_dir, monkeypatch):
    w.set_view_mode("2d")
    w.open_file(case_dir / "maniqui.in")
    v = w.view
    qtbot.waitUntil(v.is_idle, timeout=15000)
    v.repaint()
    qtbot.waitUntil(lambda: "source" in v._handles, timeout=5000)
    menus = []
    monkeypatch.setattr(w, "_exec_menu", lambda menu, *a: menus.append([x.text() for x in menu.actions()]))
    return v, v._handles["source"], menus


def test_right_click_during_a_source_drag(qtbot, window, case_dir, monkeypatch):
    w = window
    v, h, menus = _ready_source(qtbot, w, case_dir, monkeypatch)
    _mouse(v, PRESS, h, L, L)
    _mouse(v, MOVE, h + QPointF(20, 0), N, L)
    _mouse(v, MOVE, h + QPointF(40, 0), N, L)
    during = list(w.input.doc.first("SPOSIT").values)
    assert during != [0.0, 0.0, -100.0] and w.input.in_live
    _context(v, h + QPointF(40, 0))  # clic derecho con el izquierdo aún pulsado
    assert not menus
    # El menú (u otra ventana) se ha quedado con la suelta: el ratón se mueve sin botones.
    for dx in (80, 120, 160):
        _mouse(v, MOVE, h + QPointF(dx, 30), N, N)
    assert not v.dragging and not w.input.in_live
    assert list(w.input.doc.first("SPOSIT").values) == during
    assert "Mover la fuente" in w.act_undo.text() and w.input.dirty
    _mouse(v, PRESS, QPointF(10, 10), L, L)
    _mouse(v, RELEASE, QPointF(10, 10), L, N)
    assert list(w.input.doc.first("SPOSIT").values) == during
    _context(v, QPointF(200, 200))  # sin arrastre, el menú sale
    assert menus


def test_a_new_press_after_a_lost_release_ends_the_drag_first(qtbot, window, case_dir, monkeypatch):
    w = window
    v, h, menus = _ready_source(qtbot, w, case_dir, monkeypatch)
    _mouse(v, PRESS, h, L, L)
    _mouse(v, MOVE, h + QPointF(40, 0), N, L)
    during = list(w.input.doc.first("SPOSIT").values)
    _mouse(v, PRESS, QPointF(10, 10), L, L)  # la suelta no llegó
    _mouse(v, RELEASE, QPointF(10, 10), L, N)
    assert not w.input.in_live and list(w.input.doc.first("SPOSIT").values) == during
    assert "Mover la fuente" in w.act_undo.text()


def test_right_click_during_a_pan(qtbot, window, case_dir, monkeypatch):
    w = window
    v, h, menus = _ready_source(qtbot, w, case_dir, monkeypatch)
    start = QPointF(30, v.height() - 80)
    _mouse(v, PRESS, start, L, L)
    _mouse(v, MOVE, start + QPointF(40, 0), N, L)
    _context(v, start + QPointF(40, 0))
    assert not menus
    center = tuple(v.center)
    for dx in (80, 160):
        _mouse(v, MOVE, start + QPointF(dx, 20), N, N)
    assert tuple(v.center) == center


# ---------------------------------------------------------------------------------------------------------------
# 5. Atajos de una tecla dentro de los paneles
# ---------------------------------------------------------------------------------------------------------------
def test_single_key_shortcuts_do_not_fire_inside_panels(qtbot, window, case_dir):
    from argus.ui.sim_physics import MaterialsSection

    w = window
    w.set_view_mode("split")
    w.open_file(case_dir / "maniqui.in")
    qtbot.wait(50)
    # F2 en la tabla de materiales edita la celda.
    t = w.materials.table
    w.dock_mats.raise_()
    _focus(qtbot, t)
    row = next(r for r in range(t.rowCount())
               if t.item(r, 1) is not None and t.item(r, 1).flags() & Qt.ItemFlag.ItemIsEditable)
    t.setCurrentCell(row, 1)  # el fichero .mat del material 1
    QTest.keyClick(t, Qt.Key.Key_F2)
    assert w.view_mode == "split" and t.state() == QAbstractItemView.State.EditingState
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    # «3» en un desplegable no cambia el plano del corte; F4 tampoco el modo.
    w.dock_sim.raise_()
    w.sim_panel.frame(MaterialsSection).set_expanded(True)
    combo = w.sim_panel.section(MaterialsSection).mat
    _focus(qtbot, combo)
    plane = w.view.plane.name
    QTest.keyClick(combo, Qt.Key.Key_3)
    assert w.view.plane.name == plane
    # Supr en el texto (solo lectura) del .geo no pregunta si se elimina el cuerpo seleccionado.
    w.select_body(1)
    w.dock_source.raise_()
    _focus(qtbot, w.source)
    QTest.keyClick(w.source, Qt.Key.Key_Delete)
    assert not [m for k, m in w._msgs if k == "question"]
    # «F» en la lista de mensajes no reencuadra.
    _focus(qtbot, w.messages)
    w.view.zoom(0.3)
    scale = w.view.scale
    QTest.keyClick(w.messages, Qt.Key.Key_F)
    assert w.view.scale == scale


def test_view_mode_keys_work_from_a_plain_field(qtbot, window, case_dir):
    """Revisión final: F2-F4 cambian la vista también desde un campo (la profundidad del corte en la barra), que
    no las usa; F4 sigue abriendo un desplegable y F2 editando una celda."""
    from argus.ui.sim_physics import MaterialsSection

    w = window
    w.set_view_mode("split")
    w.open_file(case_dir / "maniqui.in")
    qtbot.wait(50)
    _focus(qtbot, w.depth_spin)
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_F3)
    assert w.view_mode == "3d"
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_F4)
    assert w.view_mode == "split"
    w.dock_sim.raise_()
    w.sim_panel.frame(MaterialsSection).set_expanded(True)
    combo = w.sim_panel.section(MaterialsSection).mat
    _focus(qtbot, combo)
    QTest.keyClick(combo, Qt.Key.Key_F2)  # un desplegable no usa F2: cambia la vista
    assert w.view_mode == "2d"
    w.set_view_mode("split")
    _focus(qtbot, combo)
    QTest.keyClick(combo, Qt.Key.Key_F4)  # F4 es del desplegable
    assert w.view_mode == "split"


def test_single_key_shortcuts_still_work_in_the_views_and_the_tree(qtbot, window, case_dir):
    w = window
    w.set_view_mode("split")
    w.open_file(case_dir / "maniqui.in")
    qtbot.wait(50)
    _focus(qtbot, w.view)
    QTest.keyClick(w.view, Qt.Key.Key_1)
    assert w.view.plane.name == "XY"
    w.view.zoom(0.3)
    scale = w.view.scale
    QTest.keyClick(w.view, Qt.Key.Key_F)
    assert w.view.scale != scale
    QTest.keyClick(w.view, Qt.Key.Key_F2)
    assert w.view_mode == "2d"
    w.set_view_mode("split")
    _focus(qtbot, w.tree)
    w.select_body(1)
    QTest.keyClick(w.tree, Qt.Key.Key_Delete)
    assert [m for k, m in w._msgs if k == "question"]  # pregunta (y se contesta que no)
    QTest.keyClick(w.tree, Qt.Key.Key_3)
    assert w.view.plane.name == "YZ"


def test_shortcut_guard_covers_floating_panels_but_not_dialogs(qtbot, window):
    from PySide6.QtWidgets import QDialog, QListWidget

    from argus.ui.wheel_guard import ShortcutGuard

    w = window
    guard = ShortcutGuard(w, exempt=(w.tree,))
    dlg = QDialog(w)  # un diálogo puede tener sus propios atajos: no se toca
    lst = QListWidget(dlg)
    assert guard._owns(w.messages) and not guard._owns(w.tree) and not guard._owns(lst)
    w.dock_mats.setFloating(True)
    try:
        assert guard._owns(w.materials.table)
    finally:
        w.dock_mats.setFloating(False)


# ---------------------------------------------------------------------------------------------------------------
# 6. Resaltado de los detectores
# ---------------------------------------------------------------------------------------------------------------
def test_detector_highlight_stays_with_the_section_last_used(qtbot, window, stack_dir):
    from argus.ui.sim_outputs import EnergySection, ImpactSection

    w = window
    w.open_file(stack_dir / "stack.in")
    sp = w.sim_panel
    imp, en = sp.section(ImpactSection), sp.section(EnergySection)
    sp.frame(EnergySection).set_expanded(True)
    en.selector.setCurrentIndex(2)  # EDBODY 3
    sp.frame(ImpactSection).set_expanded(True)
    imp.selector.setCurrentIndex(0)  # IDBODY 2
    assert w.view.highlight == {2}
    w.input.edit("NSIMSH", lambda doc: sc.set_value(doc, "NSIMSH", [2e6]))
    assert w.view.highlight == {2} and w.view3d.highlight == {2}
    en.selector.setCurrentIndex(0)  # EDBODY 1
    assert w.view.highlight == {1}
    w.input.edit("NSIMSH", lambda doc: sc.set_value(doc, "NSIMSH", [3e6]))
    assert w.view.highlight == {1}
    sp.frame(EnergySection).set_expanded(False)
    assert w.view.highlight == set()


# ---------------------------------------------------------------------------------------------------------------
# 7 y 8. Cuerpos ocultos en 3D y selección tras editar: por identidad
# ---------------------------------------------------------------------------------------------------------------
def _add_sphere_in_2(w):
    from argus.pengeom import edit as ge

    m2 = w.session.geo.find_body("2").index
    w.edit_geometry("Añadir esfera", lambda doc, g: ge.add_primitive(
        doc, g, ge.Primitive("sphere", center=(0.0, 0.0, 0.003), radius=0.0008), 1, m2))


def test_hidden_3d_bodies_follow_edits_undo_and_redo(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    g = w.session.geo
    three, one = g.find_body("3").index, g.find_body("1").index
    w.view3d.set_body_hidden(three, True)
    w.view3d.set_body_hidden(one, True)
    _add_sphere_in_2(w)  # renumera: «3» pasa a ser el n.º 4
    g = w.session.geo
    assert w.view3d.hidden_bodies == {g.find_body("3").index, g.find_body("1").index} != {three, one}
    w.undo()
    assert w.view3d.hidden_bodies == {three, one}
    w.redo()
    g = w.session.geo
    assert w.view3d.hidden_bodies == {g.find_body("3").index, g.find_body("1").index}
    w._answers["queue"] = [SB.Yes]
    w.delete_body(g.find_body("1").index)  # el oculto que ya no existe se olvida
    g = w.session.geo
    assert g.find_body("1") is None and w.view3d.hidden_bodies == {g.find_body("3").index}


def test_hidden_3d_bodies_during_a_module_drag(qtbot, window, case_dir):
    w = window
    w.set_view_mode("split")
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index
    w.convert_to_module(cube)
    cube = w.session.geo.find_body("1").index
    sphere = next(b.index for b in w.session.geo.bodies if b.index != cube and b.origin != "enclosure")
    w.view3d.set_body_hidden(sphere, True)
    w.select_body(cube)
    w._on_module_dragged(cube, ("move", np.array([10.0, 0.0, 0.0])), False)
    qtbot.waitUntil(lambda: w.geo_ctrl.in_live, timeout=5000)
    assert w.view3d.hidden_bodies == {sphere}
    w._on_module_dragged(cube, ("move", np.array([20.0, 0.0, 0.0])), True)
    assert not w.geo_ctrl.in_live and w.view3d.hidden_bodies == {sphere}


def _two_clones():
    from test_duplicate import CLONED

    text = CLONED.replace("BODY    (   4)   mundo", "CLONE   (   5)   copia 2\nMODULE  (   2)\n" + "1" * 64
                          + "\nX-SHIFT=(-3.000000000000000E+01,   0)\n" + "0" * 64 + "\nBODY    (   4)   mundo")
    return text.replace("MODULE  (   3)\n0000", "MODULE  (   3)\nMODULE  (   5)\n0000")


def test_selection_of_a_clone_copy_after_an_unrelated_edit(qtbot, window, tmp_path):
    from argus.pengeom.edit import find_element, set_description

    w = window
    w.open_file(_write(tmp_path / "clones.geo", _two_clones()), with_input=False)
    g = w.session.geo
    copies = [b.index for b in g.bodies if b.origin == "clone" and b.element is not b.clone_element]
    target = copies[-1]  # la esfera que copia el clon «5»
    assert g.body(target).clone_element.label.strip() == "5"
    w.select_body(target)
    w.view3d.set_body_hidden(target, True)
    w.edit_geometry("Nombre", lambda doc, gg: set_description(find_element(doc, "body", "4"), "mundo nuevo"))
    g2 = w.session.geo
    sel = w.view.selected
    assert sel == target and g2.body(sel).clone_element.label.strip() == "5"
    assert w.view3d.hidden_bodies == {target}


MAIN_INCLUDE = """Principal con un INCLUDE normal
0000000000000000000000000000000000000000000000000000000000000000
INCLUDE
   FILE=(example1.geo)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   esfera r=1 en x=10
INDICES=( 1, 1, 1, 0,-1)
X-SHIFT=(+1.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)   bola
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def test_duplicate_or_wrap_a_body_of_a_plain_include(qtbot, window, tmp_path, monkeypatch):
    w = window
    shutil.copy2(CORPUS / "pengeomjar/example1", tmp_path / "example1.geo")
    w.open_file(_write(tmp_path / "main.geo", MAIN_INCLUDE), with_input=False)
    g = w.session.geo
    assert g.ok
    kb = [b.index for b in g.bodies if b.origin == "user" and b.document is not g.document][-1]
    assert g.balias(kb) == ""
    w.select_body(kb)
    _no_dialogs(w, monkeypatch)
    w.act_duplicate.trigger()
    w.act_wrap.trigger()
    assert w._dialogs == []
    infos = [m for k, m in w._msgs if k in ("information", "warning")]
    assert len(infos) == 2 and all("INCLUDE" in m and "envolvente" not in m for m in infos)


def test_duplicate_finds_the_element_by_identity(qtbot, window, tmp_path, monkeypatch):
    """Ctrl+D sobre la copia de un clon... no se puede; sobre el cuerpo de un clon, el del clon correcto."""
    w = window
    w.open_file(_write(tmp_path / "clones.geo", _two_clones()), with_input=False)
    g = w.session.geo
    sphere = g.find_body("1").index
    seen = {}

    def fake_duplicate(doc, gg, kb, step, count, container, linked=False, parinp=None):
        seen["kb"], seen["container"], seen["nbodys"] = kb, container, gg.nbodys
        return []

    monkeypatch.setattr("argus.pengeom.duplicate.duplicate", fake_duplicate)  # el de DuplicateDialog.edit_fn

    def accept(dlg):
        dlg.rb_shift.setChecked(True)
        dlg.dx.setText("0")
        dlg.dy.setText("0")
        dlg.dz.setText("20")
        return True

    monkeypatch.setattr(w, "_exec_dialog", accept)
    w.duplicate_dialog(sphere)
    assert seen["kb"] == sphere


def test_duplicate_the_root_module_into_the_top_level_without_enclosure(qtbot, window, tmp_path, monkeypatch):
    from test_modules import NESTED

    w = window
    w.open_file(_write(tmp_path / "anidados.geo", NESTED), with_input=False)
    g = w.session.geo
    assert not g.enclosure_added
    m = g.find_body("4").index
    seen = {}

    def fake_duplicate(doc, gg, kb, step, count, container, linked=False, parinp=None):
        seen["kb"], seen["container"], seen["nbodys"] = kb, container, gg.nbodys
        return []

    monkeypatch.setattr("argus.pengeom.duplicate.duplicate", fake_duplicate)  # el de DuplicateDialog.edit_fn

    def accept(dlg):
        dlg.container_box.setCurrentIndex(dlg.container_box.count() - 1)  # «Nivel superior (fuera de todo)»
        dlg.rb_shift.setChecked(True)
        dlg.dx.setText("50")
        return True

    monkeypatch.setattr(w, "_exec_dialog", accept)
    w.duplicate_dialog(m)
    assert seen == {"kb": m, "container": g.nbodys + 1, "nbodys": g.nbodys}


# ---------------------------------------------------------------------------------------------------------------
# 10. Barra de estado al deshacer y rehacer
# ---------------------------------------------------------------------------------------------------------------
def test_status_bar_after_undo_and_redo(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    w._answers["queue"] = [SB.Yes]
    w.delete_body(w.session.geo.find_body("1").index)
    assert "ocupa ahora su volumen" in w.statusBar().currentMessage()
    w.undo()
    msg = w.statusBar().currentMessage()
    assert "Deshecho" in msg and "Eliminar el cuerpo 1" in msg and "ocupa ahora" not in msg
    w.redo()
    msg = w.statusBar().currentMessage()
    assert "Rehecho" in msg and "Eliminar el cuerpo 1" in msg


# ---------------------------------------------------------------------------------------------------------------
# 11. Las copias de un clon se escriben por número (su etiqueta es blanca)
# ---------------------------------------------------------------------------------------------------------------
CLONE_IN = ("TITLE  clones\r\nSKPAR  2\r\nSENERG 1.0E6\r\nSPOSIT 0 0 -40\r\n"
            "MFNAME m1.mat                         [Material file, up to 20 chars]\r\n"
            "MFNAME m2.mat                         [Material file, up to 20 chars]\r\n"
            "MFNAME m3.mat                         [Material file, up to 20 chars]\r\n"
            "GEOMFN clones.geo                     [Geometry file, up to 20 chars]\r\n"
            "NSIMSH 1.0E6\r\n")


def test_body_references_of_clone_copies():
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import parse_geo_text
    from argus.ui.sim_widgets import body_ref_for, resolve_ref
    from test_duplicate import CLONED

    g = compile_geometry(parse_geo_text(CLONED, "c.geo"))
    copy = next(b.index for b in g.bodies if b.origin == "clone" and b.element is not b.clone_element)
    assert g.balias(copy).strip() == ""
    assert body_ref_for(g, copy) == (None, copy)
    assert body_ref_for(g, g.find_body("1").index) == ("   1", None)
    last_blank = max(k for k in range(1, g.nbodys + 1) if not g.balias(k).strip())
    assert resolve_ref(g, "    ", None) == last_blank  # como PMRDR: la última sin etiqueta
    assert resolve_ref(g, "    ", None, blank_search=False) == 0  # IDBODY: IRETRN 3082
    assert resolve_ref(g, None, copy) == copy


def test_detectors_name_clone_copies_by_number(qtbot, window, tmp_path):
    from argus.ui.sim_outputs import EnergySection, ImpactSection
    from test_duplicate import CLONED

    w = window
    _write(tmp_path / "clones.geo", CLONED)
    w.open_file(_write(tmp_path / "c.in", CLONE_IN))
    g = w.session.geo
    copy = next(b.index for b in g.bodies if b.origin == "clone" and b.element is not b.clone_element)
    w.toggle_detector_body("energy", 0, copy)
    w.toggle_detector_body("impact", 0, copy)
    text = w.input.doc.text()
    assert f"EDBODY {copy}" in text.replace("  ", " ") and f"IDBODY {copy}" in text.replace("  ", " ")
    assert "'    '" not in text
    sp = w.sim_panel
    sp.frame(EnergySection).set_expanded(True)
    assert w.view.highlight == {copy}
    sp.frame(ImpactSection).set_expanded(True)
    assert w.view.highlight == {copy}
    # Un IDBODY con la etiqueta en blanco no es ningún cuerpo (penhmain lo rechaza); EDBODY sí busca.
    w.input.edit("x", lambda d: sc.set_impact_detectors(d, [sc.ImpactDetector(0.0, 0.0, 100, 0, 1,
                                                                               bodies=[sc.BodyRef("    ", None)])]))
    assert sp.section(ImpactSection).highlighted() == set()


# ---------------------------------------------------------------------------------------------------------------
# 12. Supr: primero se dice por qué no se puede, sin preguntar
# ---------------------------------------------------------------------------------------------------------------
def test_delete_refuses_before_asking(qtbot, window, tmp_path, monkeypatch):
    from test_duplicate import CLONED
    from test_modules import NESTED

    w = window
    w.open_file(_write(tmp_path / "anidados.geo", NESTED), with_input=False)
    m = w.session.geo.find_body("4").index
    w.select_body(m)
    w._answers["question"] = SB.Yes
    before = w.geo_ctrl.text
    w.act_delete_body.trigger()
    assert not [x for x in w._msgs if x[0] == "question"]
    assert [x for x in w._msgs if x[0] == "information"] and w.geo_ctrl.text == before
    # Un módulo vacío que copia un clon.
    w._msgs.clear()
    text = CLONED.replace("MATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\nSURFACE (   2), SIDE POINTER=(-1)\n"
                          "SURFACE (   3), SIDE POINTER=(-1)\nBODY    (   1)\n",
                          "MATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\nSURFACE (   2), SIDE POINTER=(-1)\n"
                          "SURFACE (   3), SIDE POINTER=(-1)\n")
    sphere = "BODY    (   1)   esfera\nMATERIAL(   2)\nSURFACE (   4), SIDE POINTER=(-1)\n" + "0" * 64 + "\n"
    text = text.replace(sphere, "")
    w.open_file(_write(tmp_path / "vacio.geo", text), with_input=False, ask=False)
    assert w.session.ok, [str(d) for d in w.session.diagnostics]
    m2 = w.session.geo.find_body("2").index
    before = w.geo_ctrl.text
    w.delete_body(m2)
    assert not [x for x in w._msgs if x[0] == "question"]
    infos = [x[1] for x in w._msgs if x[0] == "information"]
    assert infos and "clon" in infos[0] and w.geo_ctrl.text == before


# ---------------------------------------------------------------------------------------------------------------
# SBODY que no está en la lista (la envolvente que añade PENGEOM) o que no existe: se ve y no se pierde
# ---------------------------------------------------------------------------------------------------------------
def test_sbody_references_outside_the_list_are_kept(qtbot, window, case_dir):
    from argus.ui.source_panel import ROLE_REF

    p = case_dir / "maniqui.in"
    _write(p, p.read_bytes().decode("latin-1").replace(
        "SPOSIT 0.0 0.0 -100.0", "SPOSIT 0.0 0.0 -100.0\nSBOX   10 10 10\nSBODY  0\nSBODY  9"))
    w = window
    w.open_file(p)
    g = w.session.geo
    assert g.enclosure_added and [r.resolve(g) for r in w.input.doc.model.source.bodies] == [g.nbodys, 9]
    lst = w.source_panel.body_list

    def items():
        return [lst.item(i) for i in range(lst.count())]

    extra = [it for it in items() if it.data(ROLE_REF)[0] == "ref"]
    assert len(extra) == 2 and all(it.checkState() == Qt.CheckState.Checked for it in extra)
    assert "envolvente" in extra[0].text().lower() and "no existe" in extra[1].text()
    assert w.source_panel.bodies_hint.text().count("2 cuerpos")  # los dos cuentan
    one = next(it for it in items() if it.data(ROLE_REF) == ("kb", g.find_body("1").index))
    one.setCheckState(Qt.CheckState.Checked)  # marcar otro cuerpo no borra los de fuera de la lista
    w.source_panel.flush_pending()
    refs = [(r.label, r.kb) for r in w.input.doc.model.source.bodies]
    assert (None, 0) in refs and (None, 9) in refs and len(refs) == 3
    extra = [it for it in items() if it.data(ROLE_REF)[0] == "ref"]
    extra[0].setCheckState(Qt.CheckState.Unchecked)  # y se pueden quitar
    w.source_panel.flush_pending()
    assert (None, 0) not in [(r.label, r.kb) for r in w.input.doc.model.source.bodies]
