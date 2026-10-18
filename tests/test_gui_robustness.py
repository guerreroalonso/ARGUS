"""Robustez de la ventana (revisión de la 0.7): arrastres interrumpidos, valores tecleados sin confirmar,
la rueda del ratón sobre campos, «Nueva geometría» y «Nuevo .in» con cambios, guardar con ficheros de
solo lectura y cuerpos inexistentes."""

import os
import shutil
import stat

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"
STACK = CORPUS / "penhan/stack"
SB = QMessageBox.StandardButton


def _copy(src, dst):
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
            os.chmod(dst / f.name, 0o644)
    return dst


@pytest.fixture
def case_dir(tmp_path):
    d = tmp_path / "caso"
    d.mkdir()
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, d / name)
        os.chmod(d / name, 0o644)
    return d


@pytest.fixture
def stack_dir(tmp_path):
    d = tmp_path / "stack"
    d.mkdir()
    return _copy(STACK, d)


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    msgs = []
    answers = {"question": SB.Discard, "questions": []}

    def question(*a, **k):
        msgs.append(("question", a[2]))
        return answers["questions"].pop(0) if answers["questions"] else answers["question"]

    monkeypatch.setattr(QMessageBox, "question", question)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2])) or SB.Ok)
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


def _module_ready(qtbot, w, case_dir):
    """El cubo del caso del usuario convertido en módulo, seleccionado y con sus asas en el corte XZ."""
    w.open_file(case_dir / "maniqui.in")
    w.set_view_mode("2d")
    qtbot.wait(50)
    w.convert_to_module(w.session.geo.find_body("1").index)
    cube = w.session.geo.find_body("1").index
    w.select_body(cube)
    view = w.view
    w.set_plane("XZ")
    view.set_depth(0.0)
    view.fit()
    view.repaint()
    qtbot.waitUntil(lambda: "mod_move" in view._handles, timeout=5000)
    return cube


# -- arrastres de módulos interrumpidos ------------------------------------------------------------
def test_escape_before_the_first_drag_step_cancels_it(qtbot, window, case_dir):
    """Esc con el primer paso del arrastre aún en cola: nada queda «en vivo» ni se aplica después."""
    w = window
    _module_ready(qtbot, w, case_dir)
    view = w.view
    text0 = w.geo_ctrl.text
    c = view._handles["mod_move"]
    to = QPointF(c.x() + 100.0, c.y()).toPoint()
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c.toPoint())
    QTest.mouseMove(view, to)
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, to)
    qtbot.wait(200)
    assert not w.geo_ctrl.in_live and w._mdrag is None
    assert w.geo_ctrl.text == text0


def test_switching_to_3d_during_a_drag_cancels_it(qtbot, window, case_dir):
    w = window
    _module_ready(qtbot, w, case_dir)
    view = w.view
    c = view._handles["mod_move"]
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c.toPoint())
    QTest.mouseMove(view, QPointF(c.x() + 100.0, c.y()).toPoint())
    qtbot.waitUntil(lambda: w.geo_ctrl.in_live, timeout=5000)
    w.mode_actions["3d"].trigger()
    qtbot.wait(50)
    assert not w.geo_ctrl.in_live and w._mdrag is None and view._drag_mode is None


def test_undo_and_save_during_a_drag_cancel_it_first(qtbot, window, case_dir):
    w = window
    cube = _module_ready(qtbot, w, case_dir)
    before = w.geo_ctrl.text
    w._on_module_dragged(cube, ("move", np.array([100.0, 0.0, 0.0])), False)
    qtbot.waitUntil(lambda: w.geo_ctrl.in_live, timeout=5000)
    assert w.save_all()  # Ctrl+S con el botón aún pulsado: se guarda sin el movimiento a medias
    assert not w.geo_ctrl.in_live
    assert (case_dir / "cubo-esfera.geo").read_bytes().decode("latin-1") == before == w.geo_ctrl.text
    assert not w.geo_ctrl.dirty
    w._on_module_dragged(cube, ("move", np.array([100.0, 0.0, 0.0])), False)
    qtbot.waitUntil(lambda: w.geo_ctrl.in_live, timeout=5000)
    w.act_undo.trigger()  # Ctrl+Z: anula el arrastre y deshace el paso anterior (convertir en módulo)
    assert not w.geo_ctrl.in_live
    assert "MODULE  (   1)" not in w.geo_ctrl.text


def test_a_drag_does_not_survive_opening_another_file(qtbot, window, case_dir, tmp_path):
    w = window
    cube = _module_ready(qtbot, w, case_dir)
    w._on_module_dragged(cube, ("move", np.array([100.0, 0.0, 0.0])), False)
    w.view.dragCancelled.emit()  # Esc antes del primer paso
    other = tmp_path / "otro"
    other.mkdir()
    _copy(case_dir, other)
    w._answers["question"] = SB.Discard
    cube = _module_ready(qtbot, w, other)
    assert w._mdrag is None and w.session.path.parent == other
    w._on_module_dragged(cube, ("move", np.array([100.0, 0.0, 0.0])), False)
    qtbot.waitUntil(lambda: w.geo_ctrl.in_live, timeout=5000)
    w._on_module_dragged(cube, ("move", np.array([100.0, 0.0, 0.0])), True)
    assert not w.geo_ctrl.in_live and w.geo_ctrl.dirty
    assert w.act_undo.text().endswith("Mover Módulo «1»")


# -- valores tecleados sin confirmar ------------------------------------------------------------
def _focus(qtbot, widget):
    """Da el foco a un campo (con una pantalla X sin gestor de ventanas, antes hay que activar la ventana)."""
    import warnings

    win = widget.window()
    win.activateWindow()
    widget.setFocus()
    if not widget.hasFocus() and hasattr(QApplication, "setActiveWindow"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            QApplication.setActiveWindow(win)
        widget.setFocus()
    qtbot.waitUntil(widget.hasFocus, timeout=2000)


def _type(qtbot, ed, text):
    _focus(qtbot, ed)
    ed.selectAll()
    QTest.keyClicks(ed, text)


def test_toolbar_save_includes_the_value_being_typed(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    _type(qtbot, w.source_panel.pos[2], "123")  # sin Enter
    w.act_save.trigger()  # botón de la barra: no se lleva el foco
    sposit = [ln for ln in (case_dir / "maniqui.in").read_bytes().decode("latin-1").splitlines()
              if ln.startswith("SPOSIT")][0]
    assert "123" in sposit and not w.input.dirty


def test_closing_asks_about_the_value_being_typed(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    _type(qtbot, w.source_panel.pos[2], "77")
    w._answers["question"] = SB.Cancel
    assert not w.maybe_save()  # como al cerrar: pregunta (y el usuario cancela)
    assert any(k == "question" for k, _ in w._msgs)
    assert w.input.doc.model.source.position[2] == 77.0


def test_value_being_typed_goes_to_its_own_file_not_to_the_next_one(qtbot, window, case_dir, tmp_path):
    other = tmp_path / "disc"
    other.mkdir()
    _copy(CORPUS / "penhan/disc", other)
    w = window
    w.open_file(case_dir / "maniqui.in")
    _type(qtbot, w.source_panel.pos[0], "12")
    w._answers["question"] = SB.Save
    w.open_file(other / "disc.in")
    assert w.input.path.name == "disc.in"
    assert w.input.doc.model.source.position[0] != 12.0
    w.tree.setFocus()
    qtbot.wait(50)
    assert w.input.doc.model.source.position[0] != 12.0 and not w.input.dirty
    saved = (case_dir / "maniqui.in").read_bytes().decode("latin-1")
    assert float([ln for ln in saved.splitlines() if ln.startswith("SPOSIT")][0].split()[1]) == 12.0


# -- la rueda del ratón ---------------------------------------------------------------------------
def _wheel(widget, dy=-120):
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    ev = QWheelEvent(pos, QPointF(widget.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, dy),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_wheel_over_fields_that_were_not_clicked_changes_nothing(qtbot, window, case_dir):
    from argus.ui.sim_outputs import DoseSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.sim_panel.frame(DoseSection).set_expanded(True)
    w.select_body(w.session.geo.find_body("1").index)
    in0, geo0 = w.input.doc.text(), w.geo_ctrl.text
    for widget in (w.source_panel.particle, w.sim_panel.section(DoseSection).kind, w.geo_panel.material):
        assert widget.focusPolicy() != Qt.FocusPolicy.WheelFocus
        _wheel(widget)
        _wheel(widget, 120)
    qtbot.wait(50)
    w.tree.setFocus()
    qtbot.wait(50)
    assert w.input.doc.text() == in0 and w.geo_ctrl.text == geo0
    # Con el campo pulsado (con el foco), la rueda sí cambia el valor.
    combo = w.source_panel.particle
    _focus(qtbot, combo)
    _wheel(combo)
    qtbot.wait(50)
    assert w.input.doc.text() != in0


# -- geometría nueva y .in nuevo con cambios sin guardar ------------------------------------------
def _dirty_case(w, case_dir):
    from argus.pengeom import edit as ge
    from argus.penhan import source_edit as se

    w.open_file(case_dir / "maniqui.in")
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [1.0, 0.0, -100.0]))
    w.edit_geometry("Cambiar el nombre",
                    lambda doc, g: ge.set_description(ge.find_element(doc, "body", "   1"), "Cubo"))
    assert w.input.dirty and w.geo_ctrl.dirty


def _fill_new(target):
    def fill(dlg):
        dlg.path_edit.setText(str(target))
        dlg.title_edit.setText("Nueva")
        dlg.shape.sx.setText("20")
        dlg.make_in.setChecked(True)
        return dlg._check()
    return fill


def test_new_geometry_asks_once_about_unsaved_changes(qtbot, window, case_dir, tmp_path, monkeypatch):
    w = window
    _dirty_case(w, case_dir)
    target = tmp_path / "nueva.geo"
    dialogs(w, monkeypatch, _fill_new(target))
    w._msgs.clear()
    w.new_geometry()
    assert sum(1 for k, _ in w._msgs if k == "question") == 2  # geometría y .in, una vez cada uno
    assert w.session.path == target and w.input is not None and w.input.doc.model.geomfn == "nueva.geo"


def test_new_geometry_cancelled_keeps_the_old_case(qtbot, window, case_dir, tmp_path, monkeypatch):
    w = window
    _dirty_case(w, case_dir)
    dialogs(w, monkeypatch, _fill_new(tmp_path / "nueva.geo"))
    w._answers["questions"] = [SB.Cancel]
    w.new_geometry()
    assert w.session.path.name == "cubo-esfera.geo" and w.input.path.name == "maniqui.in"
    assert w.input.dirty and w.geo_ctrl.dirty


def test_undo_after_new_input_does_not_desync_the_closed_in(qtbot, window, stack_dir, monkeypatch):
    """«Nuevo .in» cierra el .in anterior: los pasos de la geometría que lo renumeraron ya no se deshacen."""
    from argus.pengeom import edit as ge
    from argus.penhan.inputfile import parse_in_file

    w = window
    in_file = stack_dir / "stack.in"
    w.open_file(in_file)
    m2 = w.session.geo.find_body("2").index
    w.edit_geometry("Añadir esfera", lambda doc, g: ge.add_primitive(
        doc, g, ge.Primitive("sphere", center=(0.0, 0.0, 0.003), radius=0.0008), 1, m2))
    assert "EDBODY 4" in w.input.doc.text()
    w._answers["questions"] = [SB.Save]
    w.new_input()
    assert not w.act_undo.isEnabled()
    w.save_geometry()
    doc = parse_in_file(in_file)
    refs = [ln.kb for ln in doc.lines if ln.kw and ln.kw.strip() in ("DSMAX", "EDBODY")]
    assert all(kb is None or kb <= w.session.geo.nbodys for kb in refs)


def test_selecting_a_body_the_geometry_does_not_have(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    w.select_body(9)  # p. ej. una línea DSMAX 9 escrita para otra versión de la geometría
    assert w.view.selected is None and w.geo_panel.kb is None
    assert "no tiene el cuerpo" in w.statusBar().currentMessage()


# -- guardar ------------------------------------------------------------------------------------
def test_backup_of_a_read_only_file_is_writable(tmp_path):
    from argus.core.files import backup_file

    f = tmp_path / "maniqui.in"
    f.write_text("TITLE  x\n")
    os.chmod(f, stat.S_IREAD)
    bak = backup_file(f)
    assert bak.read_text() == "TITLE  x\n" and os.stat(bak).st_mode & stat.S_IWRITE
    os.chmod(bak, stat.S_IREAD)  # un .bak viejo de solo lectura tampoco impide la copia siguiente
    f.chmod(0o644)
    f.write_text("TITLE  y\n")
    backup_file(f)
    assert bak.read_text() == "TITLE  y\n"


def test_save_all_does_not_write_the_in_when_the_geometry_fails(qtbot, window, stack_dir, monkeypatch):
    from argus.pengeom import edit as ge

    w = window
    w.open_file(stack_dir / "stack.in")
    in_disk = (stack_dir / "stack.in").read_bytes()
    m2 = w.session.geo.find_body("2").index
    w.edit_geometry("Añadir esfera", lambda doc, g: ge.add_primitive(
        doc, g, ge.Primitive("sphere", center=(0.0, 0.0, 0.003), radius=0.0008), 1, m2))
    assert "EDBODY 4" in w.input.doc.text()
    real_replace = os.replace

    def fail(src, dst, *a, **k):  # la geometría no se puede sustituir
        if os.path.basename(dst) == "3discs.geo":
            raise PermissionError(13, "Permission denied", str(dst))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", fail)
    assert not w.save_all()
    assert (stack_dir / "stack.in").read_bytes() == in_disk and w.input.dirty and w.geo_ctrl.dirty


def test_parallel_kernels_from_two_threads_with_the_workqueue_layer(tmp_path):
    """La capa «workqueue» de Numba (Windows sin TBB ni OpenMP) se cierra si dos hilos lanzan núcleos
    paralelos a la vez: el corte 2D (hilo propio) y la vista 3D (hilo de la ventana) pasan por un cerrojo."""
    import subprocess
    import sys

    pytest.importorskip("numba")
    script = tmp_path / "hilos.py"
    script.write_text(f"""
import sys, threading, time
sys.path.insert(0, {str(CORPUS.parents[1] / 'src')!r})
import numpy as np
from argus.session import open_geometry
from argus.render.raytrace import RayTracer
s = open_geometry({str(CORPUS / 'ornl/adult.geo')!r})
rt = RayTracer(s.geo)
lo, hi = s.bounds
rng = np.random.default_rng(1)
pts = rng.uniform(lo, hi, (20000, 3))
o = np.tile(0.5 * (lo + hi) - [0, 0, 300.0], (5000, 1))
d = rng.normal(size=(5000, 3)); d /= np.linalg.norm(d, axis=1)[:, None]
stop = False
def worker():
    while not stop:
        s.locator.locate(pts)
th = threading.Thread(target=worker, daemon=True)
th.start()
t0 = time.time()
while time.time() - t0 < 2:
    rt.render(o, d)
stop = True
th.join()
print("bien")
""")
    env = dict(os.environ, NUMBA_THREADING_LAYER="workqueue")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=600)
    assert r.returncode == 0 and "bien" in r.stdout, r.stdout + r.stderr
