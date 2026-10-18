"""Revisión de 0.15.0 (ventana): cada hallazgo de la revisión de «Geometrías complejas», con su prueba (0.15.1).

Manual edit frente a los cambios de otros sitios y «Seguir editando»; lo escrito sin aplicar (clics, navegar, New,
mover); el .in que pierde líneas al reescribir o borrar; PARINP sin .in; los campos, tablas y el texto; el File Viewer;
los atajos; los mensajes y las asas de los módulos en geometrías grandes.
"""

import shutil
import sys
import traceback

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from argus.pengeom import blocks as bk  # noqa: E402
from conftest import CORPUS  # noqa: E402
from test_gui_modules import window  # noqa: E402, F401

pytestmark = pytest.mark.gui

CHAIR = CORPUS / "pengeomjar" / "chair"
STACK = CORPUS / "penhan" / "stack"
CASE = CORPUS / "usuario" / "100Mev15-174"
Z = "0" * 64
ONES = "1" * 64
END = "END      " + "0" * 55 + "\n"
YES = QMessageBox.StandardButton.Yes
NO = QMessageBox.StandardButton.No


@pytest.fixture
def errors(monkeypatch):
    """Las excepciones en los slots (que Qt no deja pasar) cuentan como fallos."""
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: seen.append("".join(traceback.format_exception(t, v, tb))))
    return seen


@pytest.fixture
def chair(tmp_path):
    p = tmp_path / "chair.geo"
    shutil.copy2(CHAIR, p)
    return p


@pytest.fixture
def stack(tmp_path):
    for name in ("stack.in", "3discs.geo"):
        shutil.copy2(STACK / name, tmp_path / name)
    return tmp_path / "stack.in"


def _enter(w, qtbot):
    w.central.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.complex.active, timeout=3000)
    return w.complex


def _index(w, title):
    return next(i for i, el in enumerate(w.session.doc.elements) if bk.block_tab(el) and bk.block_title(el) == title)


def _activate(w):
    """La ventana activa, para que el foco cuente (con una pantalla X sin gestor de ventanas hay que pedirlo)."""
    import warnings

    w.activateWindow()
    if QApplication.activeWindow() is not w and hasattr(QApplication, "setActiveWindow"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            QApplication.setActiveWindow(w)


def _type(field, text):
    field.setText(text)
    field.textEdited.emit(text)


def _body_lines(w):
    return [ln.strip() for ln in w.input.doc.text().splitlines()
            if ln[:6].strip() in ("DSMAX", "EDBODY", "IDBODY", "SBODY", "IFORCE", "EABSB", "ENDETC", "IMPDET")]


def _break_manual(cw):
    """Manual edit con un texto que PENGEOM no acepta (una superficie que no existe)."""
    cw.btn_manual.click()
    assert cw.manual
    cw.text.setPlainText(cw.text.toPlainText().replace("SURFACE (   1), SIDE", "SURFACE (  77), SIDE", 1))


# ---------------------------------------------------------------------------------------------------------------------
# Manual edit
# ---------------------------------------------------------------------------------------------------------------------

def test_manual_edit_blocks_edits_from_elsewhere(qtbot, window, stack, monkeypatch, errors):  # noqa: F811
    """Mientras se escribe a mano no se edita la geometría desde otros sitios (al terminar, lo escrito desharía el
    cambio y el .in se quedaría sin las líneas que se le quitaron)."""
    w = window
    w.open_file(stack)
    cw = _enter(w, qtbot)
    before_in = _body_lines(w)
    cw.btn_manual.click()
    assert cw.manual and not w._can_edit() and not w.act_add_body.isEnabled()
    shown = []
    monkeypatch.setattr(w, "_exec_menu", lambda menu, pos: shown.append(menu))
    w._on_context_menu(w.view.mapToGlobal(w.view.rect().center()), (0.0, 0.0, 0.0), 1)
    assert not shown  # sin menú contextual de edición
    w.delete_body(1)  # (también desde el menú Editar o Supr)
    assert "BODY" in w.geo_ctrl.text and _body_lines(w) == before_in
    assert w.edit_geometry("prueba", lambda d, g: None) is None
    cw.end_manual()
    assert not cw.manual and w._can_edit() and _body_lines(w) == before_in
    assert not errors


@pytest.mark.parametrize("action", ["undo", "redo", "save_all", "new_input", "new_geometry"])
def test_keep_editing_stops_the_action(qtbot, window, chair, monkeypatch, errors, action):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.chk_preview.setChecked(False)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    _type(t.desc, "pata modificada")
    t.buttons["modify"].click()
    if action == "redo":
        w.undo()
    text, dirty = w.geo_ctrl.text, w.geo_ctrl.dirty
    _break_manual(cw)
    asked = []
    monkeypatch.setattr(cw, "ask_apply_broken", lambda msg: asked.append(msg) or False)  # «Seguir editando»
    monkeypatch.setattr(w, "_exec_dialog", lambda dlg: pytest.fail("no se sigue: no hay diálogo"))
    result = getattr(w, action)()
    assert len(asked) == 1 and cw.manual and w.geo_ctrl.text == text and result in (None, False)
    assert w.geo_ctrl.dirty == dirty and "77" in cw.text.toPlainText()
    assert not errors


# ---------------------------------------------------------------------------------------------------------------------
# Lo escrito sin aplicar no se pierde sin preguntar
# ---------------------------------------------------------------------------------------------------------------------

def test_clicks_and_navigation_ask_before_dropping_what_is_typed(qtbot, window, chair, errors):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.chk_preview.setChecked(False)  # (sin vista previa también)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    _type(t.rows["X-SCALE"].value, "2.5")
    first = w.session.doc.elements[0].first_line
    cw._on_line_clicked(first + 1)  # el propio bloque: no se recarga
    assert t.rows["X-SCALE"].value.text() == "2.5" and cw.pending_tab() == bk.TAB_SURFACE
    seat = _index(w, "BODY (5)")
    w._answers["question"] = NO
    cw._on_line_clicked(w.session.doc.elements[seat].first_line)  # otro bloque: se pregunta («no»)
    cw.next_block()  # Alt+N: también
    cw.move_block(+1)  # Alt+↓: también
    assert cw.sel == 0 and t.rows["X-SCALE"].value.text() == "2.5"
    w._answers["question"] = YES
    cw._on_line_clicked(w.session.doc.elements[seat].first_line)
    assert cw.sel == seat and cw.pending_tab() is None
    assert not errors


def test_new_asks_before_dropping_and_a_new_block_survives_clicks(qtbot, window, chair, errors):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    b = cw.tab_widgets[bk.TAB_BODY]
    b.buttons["new"].click()
    assert cw.new_tab == bk.TAB_BODY and b.material.text() == "0"  # (como PenGeomJar)
    _type(b.desc, "mi cuerpo")
    w._answers["question"] = NO
    cw._on_line_clicked(w.session.doc.elements[_index(w, "SURFACE (2)")].first_line)
    assert cw.new_tab == bk.TAB_BODY and b.desc.text() == "mi cuerpo"
    # New con una etiqueta que ya existe: se dice (no se cambia por otra sin más).
    _type(b.label, "5")
    b.t_surf.field.setText("6")
    b.t_surf.add()
    b.buttons["add"].click()
    assert cw.new_tab == bk.TAB_BODY and "Ya hay" in cw.status.text() and "Ya hay" in b.message.text()
    assert not errors


# ---------------------------------------------------------------------------------------------------------------------
# El .in: lo que se le quita, se dice antes
# ---------------------------------------------------------------------------------------------------------------------

CLONES = ("c\n" + Z + "\nSURFACE (   1)   r=1\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
          "SURFACE (   2)   r=2\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+2.000000000000000E+00,   0)\n"
          "Y-SCALE=(+2.000000000000000E+00,   0)\nZ-SCALE=(+2.000000000000000E+00,   0)\n" + Z + "\n"
          "BODY    (   1)   bola\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n"
          "MODULE  (  M1)   uno\nMATERIAL(   2)\nSURFACE (   2), SIDE POINTER=(-1)\nBODY    (   1)\n" + Z + "\n"
          "SURFACE (   3)   r=2 en x=10\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+2.000000000000000E+00,   0)\n"
          "Y-SCALE=(+2.000000000000000E+00,   0)\nZ-SCALE=(+2.000000000000000E+00,   0)\n"
          "X-SHIFT=(+1.000000000000000E+01,   0)\n" + Z + "\n"
          "MODULE  (  M2)   dos\nMATERIAL(   2)\nSURFACE (   3), SIDE POINTER=(-1)\n" + Z + "\n"
          "CLONE   (  C1)   copia de M1\nMODULE  (  M1)\n" + ONES + "\nY-SHIFT=(+1.000000000000000E+01,   0)\n"
          + Z + "\n" + END)
CLONE_IN = ("TITLE  t\nSKPAR  1\nSENERG 1e6\nSPOSIT 0 0 0\nSCONE  0 0 0\nMFNAME Si.mat\n"
            "MSIMPA 5e3 1e3 5e3 0.05 0.05 5e3 1e3\nMFNAME Si.mat\nMSIMPA 5e3 1e3 5e3 0.05 0.05 5e3 1e3\n"
            "GEOMFN c.geo\nENDETC 0 1e6 100\nEDBODY 4\nNSIMSH 1e3\n")


def test_modify_that_removes_bodies_asks_with_the_lines(qtbot, window, tmp_path, errors):  # noqa: F811
    w = window
    (tmp_path / "c.geo").write_text(CLONES, encoding="latin-1")
    (tmp_path / "t.in").write_text(CLONE_IN, encoding="latin-1")
    w.open_file(tmp_path / "t.in")
    # EDBODY 4: la copia de la bola dentro del clon (la 5, el módulo C1, sigue: su etiqueta es la del clon).
    assert w.session.geo.body(4).origin == "clone" and w.session.geo.body(4).clone_of == 1
    cw = _enter(w, qtbot)
    cw.select_block(_index(w, "CLONE (C1)"))
    c = cw.tab_widgets[bk.TAB_CLONE]
    _type(c.module, "M2")  # el clon pasa a copiar M2: la copia de la bola desaparece
    seen = []
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: seen.append(a[2]) or NO)
    try:
        c.buttons["modify"].click()
        assert seen and "EDBODY 4" in seen[0] and "ENDETC" in seen[0] and "2 líneas" in seen[0]
        assert "MODULE  (  M1)" in w.geo_ctrl.text  # «no»: nada cambia
        QMessageBox.question = staticmethod(lambda *a, **k: YES)
        c.buttons["modify"].click()
    finally:
        QMessageBox.question = orig
    assert "MODULE  (  M2)" in w.geo_ctrl.text and "EDBODY" not in w.input.doc.text()
    assert not errors


def test_delete_lists_the_whole_detector(qtbot, window, stack, errors):  # noqa: F811
    w = window
    w.open_file(stack)
    cw = _enter(w, qtbot)
    i = _index(w, "MODULE (3)")
    cw.select_block(i)
    seen = []
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: seen.append(a[2]) or YES)
    try:
        cw.tab_widgets[bk.TAB_MODULE].buttons["delete"].click()
    finally:
        QMessageBox.question = orig
    assert seen and "DSMAX" in seen[0] and "EDBODY" in seen[0]
    assert cw.sel == max(k for k in bk.editable_blocks(w.session.doc) if k < i)  # el de antes, elegido
    assert not errors


def test_move_says_what_changes_in_the_in(qtbot, window, stack, errors):  # noqa: F811
    w = window
    w.open_file(stack)
    cw = _enter(w, qtbot)
    first = bk.editable_blocks(w.session.doc)[0]
    cw.select_block(first)
    cw.move_block(-1)
    assert "ya es el primer bloque" in cw.status.text()
    cw.select_block(_index(w, "MODULE (3)"))
    cw.move_block(+1)
    assert "bajado" in cw.status.text() or "último" in cw.status.text()
    assert not errors


# ---------------------------------------------------------------------------------------------------------------------
# PARINP sin .in
# ---------------------------------------------------------------------------------------------------------------------

def test_parinp_without_an_in_follows_the_file(qtbot, window, chair, errors):  # noqa: F811
    """Sin .in, los PARINP son los números escritos en el .geo: un índice nuevo trae su valor y, al cambiar el
    valor, la geometría lo usa (y el .in nuevo lo escribe)."""
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.chk_preview.setChecked(False)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    _type(t.rows["X-SCALE"].index, "1")
    t.buttons["modify"].click()
    assert w.session.ok and w.session.parinp == [2.0], cw.status.text()
    _type(t.rows["X-SCALE"].value, "3")
    t.buttons["modify"].click()
    assert w.session.parinp == [3.0] and w.session.parinp_provisional
    w.new_input()
    assert "PARINP 1 3" in w.input.doc.text().replace("3.0", "3")
    assert not errors


# ---------------------------------------------------------------------------------------------------------------------
# Campos, tablas, texto y File Viewer
# ---------------------------------------------------------------------------------------------------------------------

def test_locked_tabs_and_fields(qtbot, window, chair, errors):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    inc = cw.tab_widgets[bk.TAB_INCLUDE]
    assert not inc.btn_browse.isEnabled()  # (un SURFACE elegido: la pestaña Include no se edita)
    cw.select_block(_index(w, "BODY (5)"))
    body = cw.tab_widgets[bk.TAB_BODY]
    mod = cw.tab_widgets[bk.TAB_MODULE]
    mod.t_surf.set_rows([("   9", -1)])
    mod.t_surf._toggle_side(0, 1)  # doble clic en el lado de una pestaña que no se edita: nada
    assert mod.t_surf.rows() == [("   9", -1)] and bk.TAB_MODULE not in cw._dirty
    # Delete en una tabla: la fila elegida, y después nada elegido (otro Delete no se lleva otra).
    rows = body.t_surf.rows()
    body.t_surf.table.selectRow(0)
    body.t_surf.delete()
    body.t_surf.delete()
    assert body.t_surf.rows() == rows[1:]
    # Un error lleva el cursor a su campo: el índice, no el valor.
    cw.select_block(0)
    s = cw.tab_widgets[bk.TAB_SURFACE]
    _activate(w)
    _type(s.rows["X-SCALE"].index, "77")
    s.buttons["modify"].click()
    assert "X-SCALE" in cw.status.text() and "77" in cw.status.text()
    qtbot.waitUntil(s.rows["X-SCALE"].index.hasFocus, timeout=2000)
    assert not s.rows["X-SCALE"].value.hasFocus()
    assert not errors


def test_choosing_a_completion_counts_as_typing(qtbot, window, chair, errors):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.select_block(_index(w, "CLONE (7)"))
    c = cw.tab_widgets[bk.TAB_CLONE]
    c.module.setText("55")
    c.module.completer().activated.emit("55")
    assert bk.TAB_CLONE in cw._dirty
    assert not errors


def test_text_view_details(qtbot):
    from argus.ui.complex_geo.text_view import GeoTextView

    v = GeoTextView()
    qtbot.addWidget(v)
    v.resize(500, 400)
    v.show()
    qtbot.waitExposed(v)
    # Un blanco duro (NBSP) no se confunde con un blanco al volver a enseñar el texto.
    v.show_text("leg\xa0(cylinder)\n")
    v.show_text("leg (cylinder)\n")
    assert v.shown_lines()[0] == "leg (cylinder)"
    # El bloque elegido entero a la vista y seleccionado (Ctrl+C lo copia).
    v.show_text("".join(f"linea {i}\n" for i in range(400)))
    v.mark_block(200, 210)
    top = v.verticalScrollBar().value()
    rows = int(v.viewport().height() // v.blockBoundingRect(v.firstVisibleBlock()).height())
    assert top <= 200 and 210 <= top + rows - 1
    assert v.textCursor().selectedText().replace(" ", "\n").startswith("linea 200\nlinea 201")
    # Manual edit: lo que no se toca, byte a byte (finales de línea mezclados, sin fin al final), y deprisa.
    raw = ["a\r\n", "b\n", "c\r\n"] * 3000 + ["fin"]
    v.show_text("".join(raw))
    v.begin_edit(raw)
    v.set_editing(True)
    assert v.file_text() == "".join(raw)
    cur = v.textCursor()
    cur.movePosition(cur.MoveOperation.Start)
    for _ in range(4500):
        cur.movePosition(cur.MoveOperation.NextBlock)
    cur.movePosition(cur.MoveOperation.EndOfBlock)
    cur.insertText("X")
    out = v.file_text()
    assert out == "".join(raw[:4500]) + "aX\r\n" + "".join(raw[4501:])  # la línea cambiada, con su «\r\n»
    cur.movePosition(cur.MoveOperation.End)
    cur.insertText("Y")
    assert v.file_text().endswith("c\r\nfinY")  # la última, sin fin de línea como estaba


def test_file_viewer_is_a_snapshot(qtbot, window, chair, errors):  # noqa: F811
    """Como en PenGeomJar: una copia del texto que se edita al pulsar «Update the File Viewer» (Alt+U), con los
    números de línea, que no cambia mientras se edita."""
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    assert "Update the File Viewer" in "\n".join(cw.file_viewer.shown_lines())
    cw.update_file_viewer()
    snap = cw.file_viewer.shown_lines()
    cw.chk_preview.setChecked(False)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    _type(t.desc, "pata cambiada")
    t.buttons["modify"].click()
    assert cw.file_viewer.shown_lines() == snap  # sin guardar ni pulsar Update: igual
    cw.update_file_viewer()
    assert any("pata cambiada" in ln for ln in cw.file_viewer.shown_lines())
    assert cw.viewer_info.text().startswith("Last update of File Viewer: 20")
    assert not errors


def test_alt_down_in_a_list_opens_the_list(qtbot, window, chair, errors):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    t = cw.tab_widgets[bk.TAB_SURFACE]
    before = w.geo_ctrl.text
    t.quadric.setFocus()
    qtbot.keyClick(t.quadric, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    t.quadric.hidePopup()
    assert w.geo_ctrl.text == before  # no ha movido el bloque
    assert not errors


# ---------------------------------------------------------------------------------------------------------------------
# Lo que se ve y lo que se dice
# ---------------------------------------------------------------------------------------------------------------------

def test_picking_a_body_drops_the_surface_trace(qtbot, window, chair, errors):  # noqa: F811
    w = window
    w.open_file(chair, with_input=False)
    cw = _enter(w, qtbot)
    cw.select_block(0)  # SURFACE (1): su traza y los cuerpos que limita
    assert w.view.surface_traces
    w.select_body(w.session.geo.find_body("5").index)
    assert cw.sel == _index(w, "BODY (5)") and not w.view.surface_traces and not w.view.highlight
    assert not errors


def test_an_undeclared_material_is_said(qtbot, window, stack, errors):  # noqa: F811
    w = window
    w.open_file(stack)
    cw = _enter(w, qtbot)
    cw.select_block(_index(w, "BODY (1)"))
    b = cw.tab_widgets[bk.TAB_BODY]
    _type(b.material, "7")
    assert "no está en el .in" in b.material_name.text()
    cw.chk_preview.setChecked(False)
    b.buttons["modify"].click()
    assert "Atención" in cw.status.text() and "MFNAME" in cw.status.text()
    assert not errors


def test_errors_in_an_included_file_name_it(qtbot, window, tmp_path, errors):  # noqa: F811
    w = window
    inc = ("i\n" + Z + "\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
           "BODY    (   1)\nMATERIAL(   1)\nSURFACE (   9), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    (tmp_path / "inc.geo").write_text(inc, encoding="latin-1")
    (tmp_path / "m.geo").write_text("m\n" + Z + "\nINCLUDE\n   FILE=(inc.geo     )\n" + Z + "\n" + END,
                                    encoding="latin-1")
    w.open_file(tmp_path / "m.geo", with_input=False)
    cw = _enter(w, qtbot)
    assert "inc.geo, línea" in cw.status.text()
    assert "deshaga" not in cw.status.text()  # (no hay nada que deshacer)
    assert not errors


def test_module_handles_wait_while_going_through_blocks(qtbot, window, tmp_path, errors):  # noqa: F811
    """La caja de un módulo (para sus asas en el corte) cuesta en geometrías grandes: en esta pestaña se calcula un
    momento después de elegirlo, solo la del último."""
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
    w = window
    w.open_file(tmp_path / "maniqui.in")
    w.set_view_mode("2d")
    w.convert_to_module(w.session.geo.find_body("1").index)  # el cubo, un módulo que se puede mover y girar
    kb = w.session.geo.find_body("1").index
    cw = _enter(w, qtbot)
    i = next(k for k, el in enumerate(w.session.doc.elements)
             if bk.block_tab(el) == bk.TAB_MODULE and el.label.strip() == "1")
    other = next(k for k, el in enumerate(w.session.doc.elements) if bk.block_tab(el) == bk.TAB_BODY)
    calls = []
    real = w.element_extent
    w.element_extent = lambda k: calls.append(k) or real(k)
    # Al ir de bloque en bloque, solo cuenta el último: la de un bloque que ya no está elegido no se calcula.
    cw.select_block(i)
    assert w.view.selected == kb and w.view.module_handle is None  # todavía no
    cw.select_block(other)
    qtbot.wait(400)
    assert w.view.module_handle is None and kb not in calls
    cw.select_block(i)
    assert w.view.module_handle is None
    qtbot.waitUntil(lambda: w.view.module_handle is not None, timeout=3000)
    assert w.view.module_handle["kb"] == kb and calls == [kb]
    # Ya calculada (la misma geometría), al volver a él sale al momento.
    cw.select_block(other)
    cw.select_block(i)
    assert w.view.module_handle is not None and w.view.module_handle["kb"] == kb
    # Fuera de la pestaña, al momento (como antes).
    w.central.setCurrentIndex(0)
    w.select_body(0)
    w.select_body(kb)
    assert w.view.module_handle is not None and w.view.module_handle["kb"] == kb
    assert not errors
