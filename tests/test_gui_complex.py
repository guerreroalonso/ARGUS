"""Ventana: la pestaña «Geometrías complejas» con las pestañas de PenGeomJar (0.15.0, ADR-019)."""

import shutil

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from argus.pengeom import blocks as bk  # noqa: E402
from argus.pengeom.parser import parse_geo_text  # noqa: E402
from conftest import CORPUS  # noqa: E402
from test_gui_modules import case_dir, fake_exec, window  # noqa: E402, F401

pytestmark = pytest.mark.gui

CHAIR = CORPUS / "pengeomjar" / "chair"
Z = "0" * 64


@pytest.fixture
def chair(tmp_path):
    p = tmp_path / "chair.geo"
    shutil.copy2(CHAIR, p)
    return p


def _enter(w, qtbot):
    w.central.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.complex.active, timeout=3000)
    return w.complex


def _line_of(w, title):
    doc = w.session.doc
    i = next(i for i, el in enumerate(doc.elements) if bk.block_tab(el) and bk.block_title(el) == title)
    return doc.elements[i].first_line, i


def _visible_docks(w):
    return sorted(d.windowTitle() for d in w._docks() if not d.isHidden())


def test_the_tab_hides_the_panels_and_brings_the_views(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    before = _visible_docks(w)
    assert before
    cw = _enter(w, qtbot)
    assert _visible_docks(w) == [] and w.splitter.parent() is cw.view_holder
    assert w.splitter.orientation() == Qt.Orientation.Vertical and w.act_complex.isChecked()
    assert cw.sel == 0 and cw.position.text() == "Block starts: line 5"  # como PenGeomJar: el primer bloque
    w.central.setCurrentIndex(0)
    assert _visible_docks(w) == before and w.splitter.parent() is w.view_tab
    assert w.splitter.orientation() == Qt.Orientation.Horizontal and not cw.active


def test_clicking_a_line_shows_its_block_in_its_tab(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    line, i = _line_of(w, "SURFACE (1)")
    cw._on_line_clicked(line + 2)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    assert cw.sel == i and cw.current_tab() is t
    assert t.label.text() == "1" and t.desc.text() == "leg (cylinder)" and t.quadric.currentText() == "Cylinder"
    assert t.rows["X-SCALE"].value.text() == "+2.000000000000000E+00" and t.rows["X-SCALE"].index.text() == "0"
    assert not t.rows["Z-SCALE"].value.text() and "#dcc8c8" in t.rows["Z-SCALE"].value.styleSheet()  # rosado
    assert t.buttons["modify"].isEnabled() and t.buttons["add"].text() == "Duplicate"
    assert not t.buttons["cancel"].isEnabled()
    users = {w.session.geo.find_body(lab).index for lab in ("1",)}
    assert w.view.highlight == users  # los cuerpos que limita
    line, i = _line_of(w, "BODY (5)")
    cw._on_line_clicked(line)
    b = cw.tab_widgets[bk.TAB_BODY]
    assert cw.current_tab() is b and b.material.text() == "1"
    assert [(lab.strip(), side) for lab, side in b.t_surf.rows()] == [("6", 1), ("7", -1), ("8", 1), ("9", -1),
                                                                       ("10", 1), ("11", -1)]
    assert w.view.selected == w.session.geo.find_body("5").index
    assert not cw.tab_widgets[bk.TAB_SURFACE].label.text()  # las demás pestañas, vacías
    assert not cw.tab_widgets[bk.TAB_SURFACE].buttons["modify"].isEnabled()
    line, i = _line_of(w, "CLONE (7)")
    cw._on_line_clicked(line)
    c = cw.tab_widgets[bk.TAB_CLONE]
    assert c.module.text() == "6" and c.note.text() == "original module"
    assert c.rows["Y-SHIFT"].value.text() == "+38.00000000000000E+00"
    # Un cuerpo pulsado en las vistas elige su bloque.
    w.select_body(w.session.geo.find_body("55").index)
    assert bk.block_title(w.session.doc.elements[cw.sel]) == "MODULE (55)"


def test_modify_is_one_undo_step(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.chk_preview.setChecked(False)
    before = w.geo_ctrl.text
    t = cw.tab_widgets[bk.TAB_SURFACE]
    t.rows["X-SCALE"].value.setText("2,5")
    t._changed()
    assert t.buttons["cancel"].isEnabled()
    t.buttons["modify"].click()
    after = w.geo_ctrl.text
    assert w.geo_ctrl.dirty and "X-SCALE=(+2.500000000000000E+00,   0)" in after
    assert [a for a, b in zip(before.splitlines(), after.splitlines()) if a != b] == [before.splitlines()[6]]
    assert cw.sel == 0 and t.rows["X-SCALE"].value.text() == "+2.500000000000000E+00"
    assert "X-SCALE=(+2.500000000000000E+00" in cw.text.toPlainText() and "reescrito" in cw.status.text()
    w.undo()
    assert w.geo_ctrl.text == before and t.rows["X-SCALE"].value.text() == "+2.000000000000000E+00"
    assert "X-SCALE=(+2.000000000000000E+00" in cw.text.toPlainText().splitlines()[6]


def test_new_and_add_a_body_with_its_tables(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.chk_preview.setChecked(False)
    line, i = _line_of(w, "SURFACE (13)")
    cw._on_line_clicked(line)
    b = cw.tab_widgets[bk.TAB_BODY]
    b.buttons["new"].click()
    assert cw.new_tab == bk.TAB_BODY and b.label.text() == "56"  # la que sigue a la mayor (55)
    assert not cw.tabs.isTabEnabled(0) and cw.tabs.isTabEnabled(2) and b.buttons["add"].text() == "Add"
    assert not b.buttons["modify"].isEnabled() and b.buttons["cancel"].isEnabled()
    b.desc.setText("esfera nueva")
    b.material.setText("2")
    messages = []
    b.problem.connect(messages.append)
    b.t_surf.field.setText("99")  # no existe: no entra (PenGeomJar: «This surface number doesn't exits!»)
    b.t_surf.add()
    assert messages and "99" in messages[0] and b.t_surf.rows() == []
    for lab, side in (("13", -1), ("12", -1), ("7", 1)):
        b.t_surf.field.setText(lab)
        b.t_surf.side.set_value(side)
        b.t_surf.add()
    assert b.t_surf.rows() == [("13", -1), ("12", -1), ("7", 1)]
    b.t_surf.table.selectRow(1)
    b.t_surf.delete()
    assert b.t_surf.rows() == [("13", -1), ("7", 1)]
    b.t_surf.field.setText("12")
    b.t_surf.side.set_value(-1)
    b.t_surf.add()
    n = len(w.session.doc.elements)
    b.buttons["add"].click()
    doc = w.session.doc
    assert len(doc.elements) == n + 1 and cw.new_tab is None
    assert bk.block_title(doc.elements[cw.sel]) == "BODY (56)" and cw.sel == i + 1
    assert w.session.ok and w.session.geo.find_body("56") is not None
    assert "BODY    (  56)   esfera nueva" in w.geo_ctrl.text and cw.tabs.isTabEnabled(0)
    w.undo()
    assert len(w.session.doc.elements) == n


def test_delete_refuses_what_others_use(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    before = w.geo_ctrl.text
    cw.tab_widgets[bk.TAB_SURFACE].buttons["delete"].click()
    assert w.geo_ctrl.text == before and not w.geo_ctrl.dirty and "lo usa BODY (1)" in cw.status.text()


def test_duplicate_gets_a_free_label_and_goes_after(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    free = bk.free_label(w.session.doc, bk.TAB_SURFACE, w.session.geo)
    assert free == "205"  # la que sigue a la mayor (la chair tiene las 201 a 204)
    t.buttons["add"].click()  # «Duplicate» de SURFACE (1)
    doc = w.session.doc
    assert cw.sel == 1 and bk.block_title(doc.elements[1]) == "SURFACE (205)"
    assert "ya existe" in cw.status.text() and t.label.text() == "205"


def test_preview_while_typing_and_cancel(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    assert cw.chk_preview.isChecked()
    line, i = _line_of(w, "BODY (5)")
    cw._on_line_clicked(line)
    committed = w.session
    b = cw.tab_widgets[bk.TAB_BODY]
    b.material.setText("1")
    b.material.textEdited.emit("1")
    b.material.setText("3")
    b.material.textEdited.emit("3")
    qtbot.waitUntil(lambda: cw.preview is not None, timeout=3000)
    assert w.view.session is cw.preview.session and w.view3d.session is cw.preview.session
    assert w.session is committed and not w.geo_ctrl.dirty  # nada aplicado
    assert "MATERIAL(   3)" in cw.text.toPlainText() and "Vista previa" in cw.status.text()
    b.buttons["cancel"].click()
    assert cw.preview is None and w.view.session is committed and b.material.text() == "1"
    assert "MATERIAL(   3)" not in cw.text.toPlainText()
    # Lo que PENGEOM no aceptaría se dice y no se ve.
    b.t_surf.table.selectRow(0)
    b.t_surf.delete()
    b.t_mod.field.setText("6")  # un módulo de después: «no está definido antes»
    b.t_mod.add()
    qtbot.waitUntil(lambda: "no se podría" in cw.status.text(), timeout=3000)
    assert cw.preview is None and w.view.session is committed


def test_manual_edit_applies_or_keeps_editing(qtbot, window, chair, monkeypatch):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.btn_manual.click()
    assert cw.manual and not cw.text.isReadOnly() and cw.btn_manual.text() == "Cancel edit"
    assert cw.btn_end.isEnabled() and not cw.tabs.isVisible()
    text = cw.text.toPlainText().replace("plane Z=40", "plano a 40 cm")
    cw.text.setPlainText(text)
    qtbot.waitUntil(lambda: cw.preview is not None, timeout=3000)
    cw.btn_end.click()
    assert not cw.manual and "plano a 40 cm" in w.geo_ctrl.text and w.geo_ctrl.dirty
    assert w.geo_ctrl.text.count("\r\n") == len(w.geo_ctrl.text.splitlines())  # los finales de línea del fichero
    assert w.act_undo.text().endswith("Manual edit")
    # Un texto que PENGEOM no acepta: se pregunta («Seguir editando»).
    cw.btn_manual.click()
    cw.text.setPlainText(cw.text.toPlainText().replace("SURFACE (   1), SIDE", "SURFACE (  77), SIDE"))
    asked = []
    monkeypatch.setattr(cw, "ask_apply_broken", lambda msg: asked.append(msg) or False)
    cw.btn_end.click()
    assert cw.manual and "77" in asked[0]  # sigue editando
    cw.btn_manual.click()  # «Cancel edit»
    assert not cw.manual and "SURFACE (  77)" not in cw.text.toPlainText()


def test_manual_edit_fixes_a_geometry_opened_with_errors(qtbot, window, tmp_path):  # noqa: F811
    w = window
    bad = tmp_path / "mal.geo"
    bad.write_text("Mal\n" + Z + "\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\nBODY    (   1)\n"
                   "MATERIAL(   1)\nSURFACE (   9), SIDE POINTER=(-1)\n" + Z + "\nEND      " + "0" * 55 + "\n")
    w.open_file(bad, with_input=False)
    assert w.geo_ctrl is None and not w.session.ok
    cw = _enter(w, qtbot)
    assert "PENGEOM no acepta" in cw.status.text() and not cw.tab_widgets[bk.TAB_BODY].buttons["new"].isEnabled()
    assert cw.text._errors  # la línea del error, marcada
    cw.btn_manual.click()
    cw.text.setPlainText(cw.text.toPlainText().replace("SURFACE (   9), SIDE", "SURFACE (   1), SIDE"))
    cw.end_manual()
    assert w.geo_ctrl is not None and w.session.ok and w.geo_ctrl.dirty
    assert cw.tab_widgets[bk.TAB_BODY].buttons["new"].isEnabled()


def test_relabel_navigation_and_moving_blocks(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.last_block()
    assert bk.block_title(w.session.doc.elements[cw.sel]).startswith("MODULE")
    cw.first_block()
    cw.next_block()
    assert cw.sel == 1
    cw.previous_block()
    assert cw.sel == 0
    cw.next_block()
    cw.move_block(-1)
    assert cw.sel == 0 and bk.block_title(w.session.doc.elements[0]) == "SURFACE (2)"
    line, i = _line_of(w, "BODY (1)")
    cw._on_line_clicked(line)
    for _ in range(i):
        cw.move_block(-1)
    assert "no está definida" in cw.status.text()  # PENGEOM: las superficies tienen que estar antes
    w._answers["question"] = QMessageBox.StandardButton.Yes
    cw.relabel()
    doc = w.session.doc
    geo = w.session.geo
    for b in geo.bodies:
        if b.origin == "user" and b.document is doc and b.element is not None:
            assert int(b.element.label) == b.index
    assert "MODULE  (   6)   seat and legs" in w.geo_ctrl.text and "renumeradas" in cw.status.text()


def test_build_a_new_geometry_from_nothing(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    w = window
    cw = _enter(w, qtbot)
    assert cw.left.currentIndex() == 0  # «No geometry loaded»

    def fill(dlg):
        assert dlg.start.currentData() == "empty"
        dlg.path_edit.setText(str(tmp_path / "nueva.geo"))
        dlg.make_in.setChecked(False)
        return True

    fake_exec(w, monkeypatch, fill)
    cw.btn_new_geo.click()
    assert cw.left.currentIndex() == 1 and w.session is not None and w.session.ok
    cw.chk_preview.setChecked(False)
    s = cw.tab_widgets[bk.TAB_SURFACE]
    s.buttons["new"].click()
    s.quadric.setCurrentText("Sphere")
    for name in ("X-SCALE", "Y-SCALE", "Z-SCALE"):
        s.rows[name].value.setText("10")
    s.buttons["add"].click()
    b = cw.tab_widgets[bk.TAB_BODY]
    b.buttons["new"].click()
    b.material.setText("1")
    b.t_surf.field.setText("1")
    b.t_surf.side.set_value(-1)
    b.t_surf.add()
    b.buttons["add"].click()
    assert w.session.ok and w.session.geo.find_body("1") is not None
    text = w.geo_ctrl.text
    assert "SURFACE (   1)" in text and "BODY    (   1)" in text and text.index("SURFACE") < text.index("BODY")
    assert w.view.session is w.session and w.view._status_text == ""


def test_include_and_ct_blocks_in_their_tabs(qtbot, window, tmp_path):  # noqa: F811
    w = window
    for name in ("example2", "example1", "ctbox", "box.ct"):
        shutil.copy2(CORPUS / "pengeomjar" / name, tmp_path / name)
    w.open_file(tmp_path / "example2", with_input=False)
    cw = _enter(w, qtbot)
    inc = cw.tab_widgets[bk.TAB_INCLUDE]
    assert cw.current_tab() is inc and inc.file.text() == "example1" and inc.keyword.starred
    assert inc.keyword.text() == "INCLUDE*:"
    w.open_file(tmp_path / "ctbox", with_input=False)
    line, i = _line_of(w, "CTBOX (CTB)")
    cw._on_line_clicked(line)
    ct = cw.tab_widgets[bk.TAB_CT]
    assert ct.file.text() == "box.ct" and ct.rows["X-SHIFT"].value.text() == "-1.000000000000000E+00"
    before = w.geo_ctrl.text
    ct.buttons["modify"].click()  # sin cambios: el fichero queda igual
    assert w.geo_ctrl.text == before


def test_the_in_follows_blocks_added_before_its_bodies(qtbot, window, tmp_path):  # noqa: F811
    w = window
    for name in ("stack.in", "3discs.geo"):
        shutil.copy2(CORPUS / "penhan" / "stack" / name, tmp_path / name)
    w.open_file(tmp_path / "stack.in")
    cw = _enter(w, qtbot)
    cw.chk_preview.setChecked(False)
    line, i = _line_of(w, "SURFACE (5)")
    cw._on_line_clicked(line)
    b = cw.tab_widgets[bk.TAB_BODY]
    b.buttons["new"].click()
    b.material.setText("0")
    for lab, side in (("1", 1), ("2", -1), ("5", 1)):
        b.t_surf.field.setText(lab)
        b.t_surf.side.set_value(side)
        b.t_surf.add()
    in_before = w.input.doc.text()
    b.buttons["add"].click()
    assert w.session.ok and bk.block_title(w.session.doc.elements[cw.sel]) == "BODY (4)"
    after = w.input.doc.text()
    assert "DSMAX  2 0.0001" in after and "DSMAX  3 0.0001" in after and "DSMAX  4 0.0002" in after
    assert "En el .in" in cw.status.text()
    w.undo()
    assert w.input.doc.text() == in_before and not any(
        bk.block_title(el) == "BODY (4)" for el in w.session.doc.elements if bk.block_tab(el))


def test_surface_trace_on_the_slice(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    w.set_plane("XY")
    line, i = _line_of(w, "SURFACE (1)")
    cw._on_line_clicked(line)
    assert w.view.surface_traces and w.view.surface_traces[0][1] == "SURFACE 1"
    line, i = _line_of(w, "BODY (1)")
    cw._on_line_clicked(line)
    assert not w.view.surface_traces
    w.central.setCurrentIndex(0)
    assert not w.view.surface_traces and not w.view.highlight


def test_text_round_trip_keeps_crlf():
    doc = parse_geo_text("T\r\n" + Z + "\r\nEND      " + "0" * 55 + "\r\n", "x.geo")
    assert doc.newline == "\r\n"


def test_clicking_the_text_during_a_preview_picks_the_right_block(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    t.rows["Z-SHIFT"].value.setText("1")  # una línea más en el bloque de la vista previa
    t.rows["Z-SHIFT"].value.textEdited.emit("1")
    qtbot.waitUntil(lambda: cw.preview is not None, timeout=3000)
    assert cw.preview.lines == (4, 11)
    line, i = _line_of(w, "SURFACE (2)")  # en el texto aplicado, la 11; en el de la vista previa, la 12
    cw._on_line_clicked(line + 1)  # lo escrito sin aplicar se perdería: se pregunta («no»: todo sigue igual)
    assert cw.preview is not None and cw.sel == 0 and t.rows["Z-SHIFT"].value.text() == "1"
    w._answers["question"] = QMessageBox.StandardButton.Yes
    cw._on_line_clicked(line + 1)
    assert cw.preview is None and cw.sel == i


def test_delete_asks_when_the_in_names_the_body(qtbot, window, tmp_path):  # noqa: F811
    w = window
    for name in ("stack.in", "3discs.geo"):
        shutil.copy2(CORPUS / "penhan" / "stack" / name, tmp_path / name)
    w.open_file(tmp_path / "stack.in")
    cw = _enter(w, qtbot)
    line, i = _line_of(w, "MODULE (3)")
    cw._on_line_clicked(line)
    m = cw.tab_widgets[bk.TAB_MODULE]
    before = w.geo_ctrl.text
    m.buttons["delete"].click()  # el .in lo nombra (DSMAX 3, EDBODY 3): se pregunta; «no»
    assert w.geo_ctrl.text == before
    w._answers["question"] = QMessageBox.StandardButton.Yes
    m.buttons["delete"].click()
    assert "MODULE  (   3)" not in w.geo_ctrl.text and "DSMAX  3" not in w.input.doc.text()
    assert "En el .in" in cw.status.text()
    w.undo()
    assert w.geo_ctrl.text == before and "DSMAX  3" in w.input.doc.text()


def test_f6_and_the_view_menu_toggle_the_tab(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    w.act_complex.trigger()
    assert w.central.currentIndex() == 1 and w.complex.active
    w.act_complex.trigger()
    assert w.central.currentIndex() == 0 and not w.complex.active
    assert w.act_complex.shortcut().toString() == "F6"
    assert w.central.tabText(1) == "Geometrías complejas (PenGeomJar)"


END_LINE = "END      " + "0" * 55 + "\n"
INC = ("inc\n" + Z + "\nSURFACE (   1)   r=1\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
       "BODY    (   1)   ball\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n" + END_LINE)


def _stack_in(geo_name, dsmax):
    return ("TITLE  t\nSKPAR  1\nSENERG 1e6\nSPOSIT 0 0 0\nSCONE  0 0 0\nMFNAME Si.mat\n"
            "MSIMPA 5e3 1e3 5e3 0.05 0.05 5e3 1e3\nMFNAME Si.mat\nMSIMPA 5e3 1e3 5e3 0.05 0.05 5e3 1e3\n"
            f"GEOMFN {geo_name}\n" + "".join(f"DSMAX  {k} 0.000{k}\n" for k in dsmax) + "NSIMSH 1e3\n")


def test_include_blocks_keep_the_in_right(qtbot, window, tmp_path):  # noqa: F811
    """Un INCLUDE sin «*» que se mueve o se borra: el .in sigue a sus cuerpos (sus etiquetas internas cambian)."""
    w = window
    for name, x in (("a.geo", 3.0), ("b.geo", 6.0)):
        shifted = INC.replace("INDICES=( 1, 1, 1, 0,-1)\n", f"INDICES=( 1, 1, 1, 0,-1)\nX-SHIFT=({x:+.15E},   0)\n")
        (tmp_path / name).write_text(shifted, encoding="latin-1")
    (tmp_path / "m.geo").write_text("m\n" + Z + "\nINCLUDE\n   FILE=(a.geo       )\n" + Z + "\nINCLUDE\n"
                                    "   FILE=(b.geo       )\n" + Z + "\n" + END_LINE, encoding="latin-1")
    (tmp_path / "t.in").write_text(_stack_in("m.geo", (1, 2)), encoding="latin-1")
    w.open_file(tmp_path / "t.in")
    cw = _enter(w, qtbot)

    def dsmax():
        return [ln for ln in w.input.doc.text().splitlines() if ln.startswith("DSMAX")]

    cw.select_block(1)
    cw.move_block(-1)  # b.geo delante: las bolas cambian de número
    assert dsmax() == ["DSMAX  2 0.0001", "DSMAX  1 0.0002"]
    w.undo()
    assert dsmax() == ["DSMAX  1 0.0001", "DSMAX  2 0.0002"]
    cw.select_block(0)
    inc = cw.tab_widgets[bk.TAB_INCLUDE]
    inc.buttons["delete"].click()  # el .in nombra su bola: se pregunta («no»)
    assert "a.geo" in w.geo_ctrl.text
    w._answers["question"] = QMessageBox.StandardButton.Yes
    inc.buttons["delete"].click()
    assert "a.geo" not in w.geo_ctrl.text and dsmax() == ["DSMAX  1 0.0002"]


def test_manual_edit_without_changes_keeps_every_byte(qtbot, window, tmp_path):  # noqa: F811
    w = window
    p = tmp_path / "m.geo"
    raw = ("Mezcla\r\n" + Z + "\nSURFACE (   1)   r=1\r\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\r\nBODY    (   1)\n"
           "MATERIAL(   1)\r\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\nEND      " + "0" * 55)  # sin fin de línea
    p.write_bytes(raw.encode("latin-1"))
    w.open_file(p, with_input=False)
    cw = _enter(w, qtbot)
    cw.btn_manual.click()
    cw.btn_end.click()
    assert not w.geo_ctrl.dirty and w.geo_ctrl.text == raw  # nada que deshacer
    cw.btn_manual.click()
    lines = cw.text.shown_lines()
    lines[2] = lines[2].replace("r=1", "radio 1")
    cw.text.setPlainText("\n".join(lines))
    cw.btn_end.click()
    assert w.geo_ctrl.text == raw.replace("r=1\r\n", "radio 1\r\n")  # la línea cambiada, con el fin del fichero


def test_undo_reloads_a_block_that_changed(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.chk_preview.setChecked(False)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    t.rows["X-SCALE"].value.setText("2.5")
    t._changed()
    t.buttons["modify"].click()
    t.desc.setText("pata")  # sin aplicar
    t._changed()
    w.undo()  # el bloque cambia: lo escrito no puede quedarse (Modify desharía el «deshacer»)
    assert t.rows["X-SCALE"].value.text() == "+2.000000000000000E+00" and t.desc.text() == "leg (cylinder)"
    assert "se ha vuelto a cargar" in cw.status.text() and not t.buttons["cancel"].isEnabled()


def test_clicks_in_the_text_keep_what_is_being_typed(qtbot, window, chair):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    t.rows["Z-SHIFT"].value.setText("1")
    t.rows["Z-SHIFT"].value.textEdited.emit("1")
    qtbot.waitUntil(lambda: cw.preview is not None, timeout=3000)
    cw._on_line_clicked(6)  # dentro del bloque naranja (sin aplicar)
    assert cw.preview is not None and cw.sel == 0 and t.rows["Z-SHIFT"].value.text() == "1"
    b = cw.tab_widgets[bk.TAB_BODY]
    w._answers["question"] = QMessageBox.StandardButton.Yes  # (New descarta lo escrito en Surface: se pregunta)
    b.buttons["new"].click()
    b.desc.setText("mi cuerpo")
    cw._on_line_clicked(0)  # la cabecera
    assert cw.new_tab == bk.TAB_BODY and b.desc.text() == "mi cuerpo"
