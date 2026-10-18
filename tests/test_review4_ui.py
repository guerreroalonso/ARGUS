"""Interfaz (cuarta revisión, 0.10): menús y diálogos que se quedaban vivos con la geometría de antes, Rehacer con
Ctrl+Y y Ctrl+Mayús+Z, geometrías rotas por un PARINP al abrirlas, el corte y el botón de guardar tras deshacer,
«Guardar la geometría como…» sobre el .in, diálogos del editor que se cerraban con un cambio que después se
rechazaba, valores enormes de la fuente, las asas de la rejilla de dosis con un PSF, la rueda sobre una tabla,
números en español en los cuadros numéricos y lo que queda en la memoria del TAC anterior."""

import os
import shutil
import threading
import types
import warnings

import numpy as np
import pytest

pytest.importorskip("PySide6")

import shiboken6  # noqa: E402
from PySide6.QtCore import QEvent, QLocale, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox  # noqa: E402

from argus.penhan import sections as sc  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"
STACK = CORPUS / "penhan/stack"
DISC = CORPUS / "penhan/disc"
SB = QMessageBox.StandardButton


def _copy(src, dest, names=None):
    dest.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and (names is None or f.name in names):
            shutil.copy2(f, dest / f.name)
            os.chmod(dest / f.name, 0o644)
    return dest


@pytest.fixture
def case_dir(tmp_path):
    return _copy(CASE, tmp_path, {"maniqui.in", "cubo-esfera.geo"})


@pytest.fixture
def stack_dir(tmp_path):
    return _copy(STACK, tmp_path)


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
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def _flush():
    """Borra lo que está pendiente de borrar (``deleteLater``)."""
    for _ in range(3):
        QApplication.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


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


# ---------------------------------------------------------------------------------------------------------------------
# Menús y diálogos: no se quedan como hijos ocultos de la ventana (con la geometría y el .in de entonces)
# ---------------------------------------------------------------------------------------------------------------------

def _close_later(what, seen):
    def close():
        w = QApplication.activeModalWidget() if what == "dialog" else QApplication.activePopupWidget()
        if w is None:
            QTimer.singleShot(20, close)
            return
        seen.append(type(w).__name__)
        w.reject() if what == "dialog" else w.close()

    QTimer.singleShot(20, close)


def test_menus_are_deleted_after_use(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    before = len(w.findChildren(QMenu))
    seen = []
    _close_later("menu", seen)
    w._on_context_menu(QPoint(10, 10), np.array([0.0, 0.0, 0.0]), 1)
    _flush()
    assert seen and len(w.findChildren(QMenu)) == before


def test_dialogs_are_released(qtbot, window, case_dir, stack_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    seen = []
    _close_later("dialog", seen)
    w.add_body_dialog(1)
    first = w._last_dialog
    assert seen == ["AddBodyDialog"] and first is not None
    _close_later("dialog", seen)
    w.edit_spectrum()  # el siguiente diálogo libera el anterior
    _flush()
    assert not shiboken6.isValid(first)
    second = w._last_dialog
    assert second is not None and shiboken6.isValid(second)
    w._answers["question"] = SB.Discard
    w.open_file(stack_dir / "stack.in")  # abrir otro caso libera el último
    _flush()
    assert not shiboken6.isValid(second) and w._last_dialog is None


# ---------------------------------------------------------------------------------------------------------------------
# Rehacer: Ctrl+Y y Ctrl+Mayús+Z (una sola vez cada atajo: con dos iguales Qt no dispara ninguno)
# ---------------------------------------------------------------------------------------------------------------------

def test_redo_shortcuts(qtbot, window, case_dir):
    from argus.penhan import source_edit as se

    w = window
    keys = [s.toString() for s in w.act_redo.shortcuts()]
    assert "Ctrl+Y" in keys and "Ctrl+Shift+Z" in keys and len(keys) == len(set(keys))
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("Mover la fuente", lambda doc: se.set_position(doc, [1.0, 2.0, -100.0]))
    after = list(w.input.doc.model.source.position)
    ctrl = Qt.KeyboardModifier.ControlModifier
    for key, mods in ((Qt.Key.Key_Y, ctrl), (Qt.Key.Key_Z, ctrl | Qt.KeyboardModifier.ShiftModifier)):
        w.undo()
        assert list(w.input.doc.model.source.position) != after
        _focus(qtbot, w.view)
        QTest.keyClick(w.view, key, mods)
        assert list(w.input.doc.model.source.position) == after


# ---------------------------------------------------------------------------------------------------------------------
# PARINP: una geometría que no se puede usar al abrirla se arregla desde el .in; el corte no se queda con lo de antes
# ---------------------------------------------------------------------------------------------------------------------

def _set_parinp(w, ip, value):
    values = dict(sc.parinp(w.input.doc))
    values[ip] = value
    w.input.edit("PARINP", lambda doc: sc.set_parinp(doc, values))


def test_a_geometry_broken_by_parinp_at_open_recovers(qtbot, window, tmp_path):
    d = _copy(DISC, tmp_path)
    p = d / "disc.in"
    p.write_bytes(p.read_bytes().replace(b"PARINP 2  40.0", b"PARINP 2   0.0"))
    w = window
    w.open_file(p)
    assert not w.session.ok and w.geo_ctrl is None
    _set_parinp(w, 2, 40.0)
    assert w.session.ok and w.geo_ctrl is not None and w.session.parinp[1] == 40.0
    qtbot.waitUntil(lambda: w.view._image is not None, timeout=5000)


def test_the_slice_is_cleared_when_the_geometry_breaks(qtbot, window, tmp_path):
    w = window
    w.open_file(_copy(DISC, tmp_path) / "disc.in")
    qtbot.waitUntil(lambda: w.view._image is not None, timeout=5000)
    _set_parinp(w, 2, 0.0)
    assert not w.session.ok
    assert w.view._image is None and w.view._status_text


# ---------------------------------------------------------------------------------------------------------------------
# El botón «Guardar la geometría» del panel Editar sigue a deshacer y rehacer
# ---------------------------------------------------------------------------------------------------------------------

def test_the_panel_save_button_follows_undo(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.select_body(1)
    b = w.geo_panel.btn_save
    assert not b.isEnabled()
    w.geo_panel.material.setValue(5)
    w.geo_panel._on_material()
    assert w.geo_ctrl.dirty and b.isEnabled()
    w.undo()
    assert not w.geo_ctrl.dirty and not b.isEnabled()
    w.redo()
    assert w.geo_ctrl.dirty and b.isEnabled()


# ---------------------------------------------------------------------------------------------------------------------
# «Guardar la geometría como…» y «Nueva geometría» no escriben una geometría encima de un .in
# ---------------------------------------------------------------------------------------------------------------------

def test_save_geometry_as_never_writes_over_an_input(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    orig = (case_dir / "maniqui.in").read_bytes()
    other = case_dir / "otro.dat"  # un .in con otra extensión
    other.write_bytes(orig)
    for target in (case_dir / "maniqui.in", other, case_dir / "nuevo.in"):
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, t=target, **k: (str(t), "")))
        w._msgs.clear()
        assert not w.save_geometry_as()
        assert w._msgs, target
    assert (case_dir / "maniqui.in").read_bytes() == orig and other.read_bytes() == orig
    assert not (case_dir / "nuevo.in").exists()
    assert w.geo_ctrl.path.name == "cubo-esfera.geo"


def test_new_geometry_is_not_named_like_an_input(qtbot, window, case_dir):
    from argus.ui.new_geometry_dialog import NewGeometryDialog

    w = window
    w.open_file(case_dir / "maniqui.in")
    dlg = NewGeometryDialog(w, case_dir)
    for name in ("maniqui.in", "otro.IN"):
        dlg.path_edit.setText(str(case_dir / name))
        assert not dlg._check()
    dlg.path_edit.setText(str(case_dir / "nueva.geo"))
    assert dlg._check()
    dlg.deleteLater()


def test_save_geometry_as_the_same_file_keeps_the_work_folder(qtbot, window, tmp_path, monkeypatch):
    """GEOMFN con subcarpeta: el INCLUDE se lee de la carpeta del .in, como hace penhmain."""
    import re

    zero = "0" * 64
    end = "END      " + "0" * 55
    d = tmp_path / "caso"
    (d / "sub").mkdir(parents=True)
    text = (STACK / "stack.in").read_text(encoding="latin-1")
    (d / "case.in").write_text(re.sub(r"^GEOMFN 3discs\.geo    ", "GEOMFN sub/3discs.geo", text, flags=re.M),
                               encoding="latin-1")
    (d / "sub/3discs.geo").write_text(f"envoltura\n{zero}\nINCLUDE*\n   FILE=(part.geo    )\n{zero}\n{end}\n")
    shutil.copy2(STACK / "3discs.geo", d / "part.geo")
    w = window
    w.open_file(d / "case.in")
    assert w.session.ok
    work = w.geo_ctrl.work_dir
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(d / "sub/3discs.geo"), "")))
    w._msgs.clear()
    assert w.save_geometry_as()
    assert w.geo_ctrl.work_dir == work and not (d / "sub/part.geo").exists()
    assert not any("no encontrar" in m for _k, m in w._msgs)


# ---------------------------------------------------------------------------------------------------------------------
# Diálogos del editor: si el cambio se rechazaría, el diálogo no se cierra y dice por qué
# ---------------------------------------------------------------------------------------------------------------------

def test_the_surface_dialog_stays_open_when_the_edit_would_be_refused(qtbot, window, stack_dir):
    from argus.ui.surface_dialog import SurfaceDialog

    w = window
    w.open_file(stack_dir / "stack.in")
    s = w.session
    kb = s.geo.find_body("3").index  # módulo «3» (z de 0 a 0,008 cm), con el módulo «2» dentro (hasta 0,004)
    dlg = SurfaceDialog(w, s, kb)
    dlg.rb_plane.setChecked(True)
    for ed, v in zip((dlg.px, dlg.py, dlg.pz, dlg.nx, dlg.ny, dlg.nz), ("0", "0", "0,003", "0", "0", "1")):
        _type(ed, v)
    dlg.rb_plus.setChecked(True)  # se queda con z > 0,003
    dlg._check()
    assert dlg._ok  # el material propio del módulo (z > 0,004) queda entero: el diálogo solo ve eso...
    text = w.geo_ctrl.text
    dlg.accept()  # ...pero el módulo «2» asomaría por debajo del «3»
    assert dlg.result() == 0 and not dlg._ok and "✖" in dlg.status.text()
    assert w.geo_ctrl.text == text
    dlg.deleteLater()


def test_duplicating_a_clone_as_copies_is_refused_in_the_dialog(qtbot, window, tmp_path):
    from argus.ui.duplicate_dialog import DuplicateDialog
    from test_review3_ui import _two_clones

    w = window
    path = tmp_path / "clones.geo"
    path.write_bytes(_two_clones().encode("latin-1"))
    w.open_file(path, with_input=False)
    s = w.session
    kb = 4  # Módulo «3» (n.º 4), clon de 2
    assert s.geo.body(kb).origin == "clone"
    dlg = DuplicateDialog(w, s, kb, w.element_extent)
    dlg.rb_shift.setChecked(True)
    for ed, v in zip((dlg.dx, dlg.dy, dlg.dz), ("0", "0", "40")):
        _type(ed, v)
    dlg.rb_copy.setChecked(True)
    dlg.accept()
    assert dlg.result() == 0 and "clon" in dlg.status.text()
    dlg.deleteLater()


def test_editor_dialogs_edit_what_they_tested(qtbot, window, stack_dir, monkeypatch):
    """Lo que hace la ventana al aceptar es la edición de ``edit_fn`` del diálogo (la que se ha probado)."""
    w = window
    w.open_file(stack_dir / "stack.in")
    s = w.session
    kb = s.geo.find_body("3").index

    def run(dlg):
        dlg.rb_shift.setChecked(True)
        for ed, v in zip((dlg.dx, dlg.dy, dlg.dz), ("30", "0", "0")):
            _type(ed, v)
        dlg.accept()
        return dlg.result() == 1

    monkeypatch.setattr(w, "_exec_dialog", run)
    n = s.geo.nbodys
    w.duplicate_dialog(kb)
    assert w.session.geo.nbodys > n


def test_the_wrap_dialog_refuses_the_ct_mark_and_negative_margins(qtbot, window, stack_dir):
    from argus.ui.wrap_dialog import WrapDialog

    w = window
    w.open_file(stack_dir / "stack.in")
    s = w.session
    dlg = WrapDialog(w, s, s.geo.find_body("3").index, w.element_extent, w.material_names)
    dlg.material.setEditText("9999")
    dlg._check()
    assert not dlg._ok and "9999" in dlg.status.text()
    dlg.material.setCurrentIndex(0)
    dlg.margin.setText("-3,75")  # escrito sin salir del campo
    dlg.shape.kind.setCurrentIndex(dlg.shape.kind.findData("cylinder"))
    dlg.shape.axis.setCurrentIndex((dlg.shape.axis.currentIndex() + 1) % dlg.shape.axis.count())
    dlg._fit()  # antes: EditError («El radio tiene que ser positivo») sin capturar
    dlg._check()
    assert dlg.primitive().radius > 0
    dlg.deleteLater()


GLUE_GEO = """Una lámina infinita (solo dos planos) y una esfera que se pega a ella
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   z = 0
INDICES=( 0, 0, 0, 1, 0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   2)   z = 1
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(+1.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   3)   esfera
INDICES=( 1, 1, 1, 0,-1)
Z-SHIFT=(+5.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)   lamina
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(+1)
SURFACE (   2), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
MODULE  (   2)   esfera
MATERIAL(   2)
SURFACE (   3), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def test_gluing_next_to_an_unbounded_body_gives_no_warnings(qtbot, window, tmp_path):
    from argus.ui.glue_dialog import GlueDialog

    w = window
    path = tmp_path / "lamina.geo"
    path.write_text(GLUE_GEO, encoding="latin-1")
    w.open_file(path, with_input=False)
    s = w.session
    assert s.ok, [d.message for d in s.diagnostics]
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        dlg = GlueDialog(w, s, s.geo.find_body("2").index, w.element_extent)
    assert dlg.target() == s.geo.find_body("1").index
    dlg.deleteLater()


def test_a_constant_surface_bounds_all_or_nothing():
    from argus.pengeom.extent import halfspace_box

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        lo, hi = halfspace_box((0.0,) * 9 + (-1.0,), -1)  # F = -1 < 0 en todas partes: el lado -1 es todo
        assert np.all(np.isinf(lo) & (lo < 0)) and np.all(np.isinf(hi) & (hi > 0))
        lo, hi = halfspace_box((0.0,) * 9 + (-1.0,), +1)  # y el +1, nada
        assert np.all(lo > hi)


# ---------------------------------------------------------------------------------------------------------------------
# Fuente con valores enormes, asas de la rejilla de dosis con un PSF y la rueda sobre una tabla
# ---------------------------------------------------------------------------------------------------------------------

def _result(scale=0.1, w=120, h=90):
    from argus.render.slice2d import SliceResult, SliceView, standard_plane

    view = SliceView(standard_plane("XZ", 0.0), (0.0, 0.0), scale)
    return SliceResult(body=np.ones((h, w), np.int32), mat=np.ones((h, w), np.int32), view=view, width=w, height=h)


@pytest.mark.parametrize("pos, box, inside", [
    ((1e308, 0.0, -100.0), (2000.0, 2000.0, 2000.0), 0),  # lejísimos
    ((0.0, 0.0, 0.0), (1e308, 2000.0, 2000.0), 120 * 90),  # enorme en x: todo el corte
    ((1e308, 0.0, 0.0), (1.7e308, 10.0, 10.0), None),  # sus esquinas no caben en un float
])
def test_huge_source_boxes_do_not_break_the_slice(pos, box, inside):
    from argus.render.source2d import source_region

    src = types.SimpleNamespace(position=pos, box=box)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        reg = source_region(_result(), src, set())
    got = 0 if reg is None else int(reg[2].sum())
    if inside is not None:
        assert got == inside


def test_dose_grid_handles_work_with_a_psf_source(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.set_plane("XZ")
    w.view.set_depth(0.0)
    w.input.edit("PSF", lambda doc: (sc.set_psf_files(doc, ["psf.dat"]), sc.set_value(doc, "EPMAX", [1e8, 1e8])))
    assert w.input.doc.model.source.from_psf and not w.view.source_editable
    w.view.repaint()
    QApplication.processEvents()
    h = w.view._handles.get("grid_r")
    assert h is not None and w.view._handle_at(h) == "grid_r"


def test_the_wheel_over_a_combo_in_a_table_scrolls_the_panel(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    w.dock_sim.raise_()
    sp = w.sim_panel
    sp.frames[1].set_expanded(True)
    ed = sp.sections[1].editors["DSMAX"]
    ed.add_row()
    sp.flush_pending()
    sp.resize(400, 300)
    QApplication.processEvents()
    combo = ed.table.cellWidget(0, 0)
    bar = sp.verticalScrollBar()
    assert bar.maximum() > 0 and ed.table.verticalScrollBar().maximum() == 0
    bar.setValue(bar.maximum() // 2)
    v0, idx = bar.value(), combo.currentIndex()
    ev = QWheelEvent(QPointF(5, 5), QPointF(combo.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(combo, ev)
    assert combo.currentIndex() == idx and bar.value() > v0


# ---------------------------------------------------------------------------------------------------------------------
# Cuadros numéricos con el idioma español
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed, value", [
    ("12.5", 12.5), ("12,5", 12.5), ("-0,001", -0.001), ("1.000,5", 1000.5), ("1,000.5", 1000.5), ("1e3", 1000.0),
    ("2,5E-2", 0.025), ("-1.5e2", -150.0), ("0.0000001", 0.0),  # la séptima cifra decimal no entra
])
def test_decimal_spinbox_reads_numbers_in_any_language(qtbot, typed, value):
    from argus.ui.spinbox import DecimalSpinBox

    s = DecimalSpinBox()
    s.setLocale(QLocale(QLocale.Language.Spanish, QLocale.Country.Spain))
    s.setDecimals(6)
    s.setRange(-1e9, 1e9)
    s.setSuffix(" cm")
    s.setKeyboardTracking(False)
    qtbot.addWidget(s)
    s.setValue(7.0)
    s.show()
    s.setFocus()
    s.lineEdit().selectAll()
    QTest.keyClicks(s.lineEdit(), typed)
    QTest.keyClick(s.lineEdit(), Qt.Key.Key_Return)
    assert s.value() == pytest.approx(value, abs=1e-12)


def test_plain_number_rules():
    from argus.ui.spinbox import plain_number

    assert plain_number("12,5") == "12.5" and plain_number("1.000,5") == "1000.5"
    assert plain_number("1,000.5e3") == "1000.5e3" and plain_number("-1.234.567,5") == "-1234567.5"
    assert plain_number("1.00,5") is None and plain_number("1,5.0,5") is None


# ---------------------------------------------------------------------------------------------------------------------
# Memoria: la textura del TAC en la tarjeta, la caché de .ct y el hilo del corte
# ---------------------------------------------------------------------------------------------------------------------

def test_the_ct_texture_is_released_for_a_geometry_without_ct():
    from argus.ui.gpu_tracer import GpuTracer

    gpu = GpuTracer.__new__(GpuTracer)  # sin contexto de OpenGL: solo la lógica
    uploads = []
    gpu.upload_ct_materials = lambda mat: uploads.append(mat.shape)
    gpu._ct_key = object()  # el CT de la geometría anterior
    gpu.set_ct(types.SimpleNamespace(ct=None, ct_body=None), np.zeros(3))
    assert gpu._ct_key is None and uploads == [(1, 1, 1)] and gpu.ct_uniforms == {"uCtBody": 0}
    gpu.set_ct(types.SimpleNamespace(ct=None, ct_body=None), np.zeros(3))
    assert uploads == [(1, 1, 1)]  # una sola vez


def test_the_ct_cache_is_bounded_by_size(tmp_path, monkeypatch):
    from argus.pengeom import ct as ctmod

    src = CORPUS / "penhan/emfield_ct/box.ct"
    a, b = tmp_path / "a.ct", tmp_path / "b.ct"
    shutil.copy2(src, a)
    shutil.copy2(src, b)
    ctmod.clear_cache()
    try:
        one = ctmod.load_ct(a)
        size = one.material.nbytes + one.density.nbytes
        monkeypatch.setattr(ctmod, "_CACHE_BYTES", size + 1)
        ctmod.load_ct(b)
        assert len(ctmod._CACHE) == 1  # el anterior no cabe junto al nuevo
        monkeypatch.setattr(ctmod, "_CACHE_BYTES", 10 * size)
        ctmod.load_ct(a)
        assert len(ctmod._CACHE) == 2
    finally:
        ctmod.clear_cache()


def test_the_slice_thread_does_not_fail_when_its_view_is_gone(monkeypatch):
    import argus.ui.slice_view as sv

    worker = sv.SliceRenderer()
    worker.shutdown()
    worker._thread.join(timeout=5)
    assert not worker._thread.is_alive()
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args))

    def compute(locator, view, width, height):
        shiboken6.delete(worker)  # la vista (y su hilo) se destruyen a mitad del cálculo
        return "corte"

    monkeypatch.setattr(sv, "compute_slice", compute)
    worker._stop = False
    worker._locator = object()
    worker._pending = sv._Job(1, worker._token, None, 10, 10, False)
    t = threading.Thread(target=worker._run, daemon=True)  # entrega el resultado a nadie y termina
    t.start()
    t.join(timeout=10)
    assert not t.is_alive() and not errors and not shiboken6.isValid(worker)
