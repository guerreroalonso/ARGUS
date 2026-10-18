"""Paneles (revisión de la 0.8): lo que se está escribiendo al guardar, cerrar, abrir o deshacer; Inf y NaN
en el .in; el cuadro de profundidad en español; mensajes de la geometría con un .in nuevo; errores del panel
Simulación; acciones que no cambian nada; arrastre de la rejilla; SENERG < 50 eV; espectros enormes;
caracteres fuera de Latin-1."""

import os
import shutil
import warnings

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QLocale, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox  # noqa: E402

from argus.penhan import sections as sc  # noqa: E402
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
    answers = {"question": SB.Discard}

    def question(*a, **k):
        msgs.append(("question", a[2]))
        return answers["question"]

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


def _type(qtbot, ed, text):
    _focus(qtbot, ed)
    ed.selectAll()
    QTest.keyClicks(ed, text)  # sin Enter


def _on_disk(path, kw):
    return [ln for ln in path.read_bytes().decode("latin-1").splitlines() if ln.startswith(kw)][0]


def _nsimsh_field(w):
    from argus.ui.sim_outputs import JobSection

    w.sim_panel.frame(JobSection).set_expanded(True)
    return w.sim_panel.section(JobSection).nsimsh


# -- lo que se está escribiendo -------------------------------------------------------------------------
def test_save_while_typing_in_the_simulation_panel(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    _type(qtbot, _nsimsh_field(w), "5e5")
    w.act_save.trigger()  # el botón de la barra no se lleva el foco
    assert sc.value(w.input.doc, "NSIMSH") == [5e5]
    assert "5e5" in _on_disk(case_dir / "maniqui.in", "NSIMSH") and not w.input.dirty
    qtbot.wait(50)
    assert not w.input.dirty  # la edición pendiente no llega después de guardar


def test_close_or_open_while_typing_asks_first(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    _type(qtbot, _nsimsh_field(w), "5e5")
    w._answers["question"] = SB.Cancel
    assert not w.maybe_save()  # lo que hace cerrar: pregunta y se cancela
    assert any(k == "question" for k, _m in w._msgs)
    assert sc.value(w.input.doc, "NSIMSH") == [5e5]


def test_undo_while_typing_undoes_what_was_typed(qtbot, window, case_dir):
    from argus.penhan import source_edit as se

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [3.0, 0.0, -100.0]))
    _type(qtbot, _nsimsh_field(w), "5e5")
    w.undo()
    assert sc.value(w.input.doc, "NSIMSH") == [1e6]  # se deshace lo escrito, no el paso anterior
    assert w.input.doc.model.source.position == (3.0, 0.0, -100.0)
    assert w.act_redo.isEnabled()


def test_material_name_being_edited_is_saved(qtbot, window, case_dir):
    from argus.ui.materials_panel import COL_NAME

    w = window
    w.open_file(case_dir / "maniqui.in")
    mp = w.materials
    w.dock_materials.raise_() if hasattr(w, "dock_materials") else None
    row = next(r for r in range(mp.table.rowCount())
               if mp.table.item(r, 0) is not None and mp.table.item(r, 0).text() == "1")
    item = mp.table.item(row, COL_NAME)
    mp.table.setCurrentItem(item)
    mp.table.editItem(item)
    qtbot.wait(50)
    ed = QApplication.focusWidget()
    if not isinstance(ed, QLineEdit):
        ed = mp.table.findChild(QLineEdit)
        _focus(qtbot, ed)
    ed.selectAll()
    QTest.keyClicks(ed, "Agua liquida")
    w.act_save.trigger()
    assert "Agua liquida" in _on_disk(case_dir / "maniqui.in", "MFNAME")
    qtbot.wait(50)
    assert not w.input.dirty


# -- valores raros del .in ----------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["Inf", "NaN", "1e999"])
def test_inf_and_nan_in_the_in_open_with_a_warning(qtbot, window, case_dir, value):
    p = case_dir / "maniqui.in"
    text = p.read_bytes().decode("latin-1").replace("SPOSIT 0.0 0.0 -100.0", f"SPOSIT {value} 0.0 -100.0")
    text = text.replace("SCONE  0 0 0", f"SCONE  {value} 0 0")
    p.write_bytes(text.encode("latin-1"))
    w = window
    w.open_file(p)
    assert w.input is not None and "maniqui.in" in w.windowTitle()
    shown = w.source_panel.pos[0].text()
    assert shown in ("Inf", "NaN")
    assert any("no es un número finito" in d.message for d in w.input.doc.diagnostics)
    assert "no válida" in w.source_panel.emission_info.text()
    w.view.grab()
    w.view3d.grab()


def test_senerg_below_50_ev_is_refused_and_warned(qtbot, window, case_dir):
    from argus.penhan import source_edit as se
    from argus.penhan.validate import validate_case

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("mono", lambda d: se.set_energy(d, 1e8))
    sp = w.source_panel
    sp.energy.setText("15")  # sin unidad: 15 eV (quería 15 MeV)
    sp.energy.setModified(True)
    sp.energy._commit()
    assert w.input.doc.first("SENERG").values == [1e8] and "50 eV" in sp.energy.toolTip()
    w.input.edit("a mano", lambda d: d.set("SENERG", [15.0]))
    assert any("1 GeV" in d.message for d in validate_case(w.input.doc, None, case_dir))


# -- cuadro de profundidad ----------------------------------------------------------------------------------
def test_depth_box_reads_a_dot_with_the_spanish_locale(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    spin = w.depth_spin
    spin.setLocale(QLocale(QLocale.Language.Spanish, QLocale.Country.Spain))
    for text, want in (("12.5", 12.5), ("12,5", 12.5), ("-3.25", -3.25), ("0.001", 0.001)):
        _focus(qtbot, spin)
        spin.selectAll()
        QTest.keyClicks(spin, text)
        QTest.keyClick(spin, Qt.Key.Key_Return)
        assert spin.value() == pytest.approx(want)
        assert w.view.plane.depth == pytest.approx(want)


# -- mensajes y errores de los paneles ------------------------------------------------------------------
def test_geometry_messages_are_not_shown_as_problems_of_a_new_in(qtbot, window, tmp_path):
    from argus.ui.sim_physics import MaterialsSection

    lines = (CASE / "cubo-esfera.geo").read_bytes().decode("latin-1").split("\n")
    lines[5] = lines[5].replace("X-SCALE=(", "X-SCALE= ", 1)  # PENGEOM lo acepta; ARGUS avisa (línea 6)
    lines[9] = lines[9].replace("Y-SCALE=(", "Y-SCALE= ", 1)  # (línea 10: un MSIMPA en el .in nuevo)
    (tmp_path / "cubo-esfera.geo").write_bytes("\n".join(lines).encode("latin-1"))
    w = window
    w.open_file(tmp_path / "cubo-esfera.geo")
    assert w.session.ok and w.session.diagnostics
    w.new_input()
    qtbot.wait(50)
    assert w.input.doc.path is None
    geo_msgs = [d.message for d in w.session.diagnostics]
    assert all(m not in w.source_panel.status.text() for m in geo_msgs)
    assert all(m not in w.sim_panel.frame(MaterialsSection).status.toolTip() for m in geo_msgs)


def test_simulation_panel_errors_stay_visible(qtbot, window, case_dir):
    from argus.ui.sim_outputs import ImpactSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    sec = w.sim_panel.section(ImpactSection)

    def boom(doc):
        raise ValueError("penhmain admite como máximo 50 cuerpos (prueba)")

    sec.apply.emit("x", boom)
    qtbot.waitUntil(lambda: "50 cuerpos" in w.sim_panel.status.text(), timeout=3000)
    assert "50 cuerpos" in w.statusBar().currentMessage()


def test_actions_that_change_nothing_do_not_touch_the_in(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    sp = w.source_panel
    before = w.input.doc.text()
    sp.particle.activated.emit(sp.particle.currentIndex())  # la misma partícula otra vez
    sp._on_preset("+Z")  # el haz ya va hacia +Z (SCONE 0 0 0)
    assert w.input.doc.text() == before and not w.input.dirty and not w.act_undo.isEnabled()


def test_dragging_the_dose_grid_writes_clean_numbers(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("caja", lambda d: sc.set_dose_grid(d, sc.DoseGrid((0.1, 10.1, 11), (-5.0, 5.0, 11),
                                                                    (0.0, 9.0, 11))))
    v = w.view
    v.scale = 0.01
    v._grid_origin = sc.dose_grid_from_model(v.dose_grid)
    v._drag_mode = "grid_move"
    v._drag_start = QPointF(100, 100)
    v._drag_modifiers = Qt.KeyboardModifier.NoModifier
    got = []
    v.gridChanged.connect(lambda g, fin: got.append(g))
    v._drag_grid(QPointF(120, 100), True)
    w._on_grid_dragged(got[-1], True)
    line = [ln for ln in w.input.doc.display_lines() if ln.startswith("GRID")]
    assert not any("0000000" in ln or "99999" in ln for ln in line), line


def test_fit_dose_cylinder_inside_an_off_centre_geometry(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("cilindro", lambda d: sc.set_dose_grid(d, sc.DoseGrid(z=(0.0, 9.0, 10), r=(15.0, 10))))
    w.view3d.scene_lo[:] = (-2.0, -8.0, 0.0)
    w.view3d.scene_hi[:] = (10.0, 8.0, 5.0)
    w.fit_dose_grid()
    assert sc.dose_grid(w.input.doc).r[0] == pytest.approx(2.0)  # cabe: 2 cm hasta la cara x = -2


# -- espectros ------------------------------------------------------------------------------------------------
def test_huge_spectra_do_not_freeze_the_editor(qtbot):
    import time

    from argus.ui.spectrum_dialog import SpectrumDialog

    rows = [(1e6 + 100.0 * i, 1.0) for i in range(5000)] + [(1e6 + 100.0 * 5000, -1.0)]
    t0 = time.perf_counter()
    dlg = SpectrumDialog(None, rows)
    qtbot.addWidget(dlg)
    assert time.perf_counter() - t0 < 10.0
    assert "250" in dlg.info.text() and not dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()


def test_skipped_pasted_rows_are_reported(qtbot, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from argus.ui.spectrum_dialog import SpectrumDialog

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]) or SB.Ok)
    dlg = SpectrumDialog(None, [(1e6, 1.0), (2e6, -1.0)])
    qtbot.addWidget(dlg)
    QGuiApplication.clipboard().setText("E\tP\n90\t0,25\n95\t75%\n100\t-1\n")
    dlg._paste()
    assert len(dlg.rows()) == 2 and any("95" in m and "75%" in m for m in shown)


# -- Latin-1 ----------------------------------------------------------------------------------------------------
def test_characters_outside_latin1_are_fixed_or_reported(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("título", lambda d: d.set("TITLE", text="Haz – protones “100 MeV” ≤ 5 cm → agua ✓"))
    w.act_save.trigger()
    title = _on_disk(case_dir / "maniqui.in", "TITLE")
    assert '- protones "100 MeV" <= 5 cm -> agua ?' in title
    assert any(k == "warning" and "✓" in m for k, m in w._msgs)
    assert not list(case_dir.glob("*.argus-tmp")) and not list(case_dir.glob(".*argus-tmp"))


def test_nan_in_the_spectrum_is_not_drawn(qtbot, window, case_dir, qtlog):
    p = case_dir / "maniqui.in"
    p.write_bytes(p.read_bytes().replace(b"SPECTR 91.22706166E+6", b"SPECTR NaN           "))
    w = window
    w.open_file(p)
    w.source_panel.grab()
    w.view.grab()
    assert not [r for r in qtlog.records if "NaN" in r.message]
