"""Novena revisión (0.16.3): la ventana principal.

* «Recargar» un fichero que otro programa ha cambiado no tira los cambios sin guardar del otro (si no dependen de él).
* Una geometría con tildes en el nombre no se puede citar en un .in (un penhmain de Linux, también con WSL, no la
  encontraría): se dice al crearla, al guardarla como… y al crear el .in.
* «Manual edit» que pasa por un texto que PENGEOM no acepta, o que quita un cuerpo que nombra el .in: el .in sigue a
  los demás cuerpos.
* Lo escrito sin aplicar en «Geometrías complejas» no se pierde al crear un .in.
* Mensajes al guardar (el temporal de ARGUS, la carpeta que ya no está) y textos.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from argus.pengeom import blocks as bk  # noqa: E402
from argus.pengeom import edit as ge  # noqa: E402
from argus.penhan import source_edit as se  # noqa: E402
from conftest import CORPUS  # noqa: E402
from test_gui_modules import case_dir, window  # noqa: E402,F401

pytestmark = pytest.mark.gui
SB = QMessageBox.StandardButton
STACK = CORPUS / "penhan" / "stack"
CHAIR = CORPUS / "pengeomjar" / "chair"
Z = "0" * 64


@pytest.fixture
def stack(tmp_path):
    for name in ("stack.in", "3discs.geo"):
        shutil.copy2(STACK / name, tmp_path / name)
    return tmp_path / "stack.in"


def _enter(w, qtbot):
    w.central.setCurrentIndex(1)
    qtbot.waitUntil(lambda: w.complex.active, timeout=3000)
    return w.complex


def _index_of(w, title):
    return next(i for i, el in enumerate(w.session.doc.elements) if bk.block_tab(el) and bk.block_title(el) == title)


def _body_lines(w):
    return [ln.strip()[:16] for ln in w.input.doc.text().splitlines() if ln[:6].strip() in ("DSMAX", "EDBODY",
                                                                                            "IDBODY")]


# ---------------------------------------------------------------------------------------------------------------------
# Recargar
# ---------------------------------------------------------------------------------------------------------------------

def test_reloading_the_in_keeps_unsaved_geometry_work(qtbot, window, case_dir, monkeypatch):  # noqa: F811
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.edit_geometry("Material 1", lambda doc, g: ge.set_material(ge.find_element(doc, "body", "1"), 2))
    w.input.edit("Colocar la fuente", lambda d: se.set_position(d, [1.0, 2.0, 3.0]))
    geo_text = w.geo_ctrl.text
    time.sleep(0.01)
    with open(case_dir / "maniqui.in", "a", encoding="latin-1") as f:
        f.write("       . cambiado por otro programa\n")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or SB.Reset)  # «Recargar»
    assert not w.save_all()
    assert len(asked) == 1 and "«maniqui.in»" in asked[0]
    assert w.geo_ctrl.dirty and w.geo_ctrl.text == geo_text  # la geometría, con sus cambios
    assert "cambiado por otro programa" in w.input.doc.text() and not w.input.dirty


def test_reloading_the_geometry_keeps_unsaved_in_work(qtbot, window, case_dir, monkeypatch):  # noqa: F811
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("Colocar la fuente", lambda d: se.set_position(d, [1.0, 2.0, 3.0]))
    w.edit_geometry("Material 1", lambda doc, g: ge.set_material(ge.find_element(doc, "body", "1"), 2))
    in_text = w.input.doc.text()
    time.sleep(0.01)
    with open(case_dir / "cubo-esfera.geo", "a", encoding="latin-1") as f:
        f.write("\n")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: SB.Reset)
    assert not w.save_all()
    assert w.input.dirty and w.input.doc.text() == in_text and not w.geo_ctrl.dirty


def test_reloading_with_linked_changes_asks_before_dropping_the_other(qtbot, window, stack, monkeypatch):  # noqa: F811
    """Si el .in sigue a cambios de la geometría (cuerpos renumerados), volver a abrir solo uno los dejaría
    desparejados: se pregunta, nombrando el otro fichero."""
    w = window
    w.open_file(stack)
    cw = _enter(w, qtbot)
    _manual(cw, lambda t: _before_first_body(t, NEW_BODY))  # renumera los cuerpos: el .in los sigue
    assert w.input.dirty and w._linked_changes()
    time.sleep(0.01)
    with open(stack, "a", encoding="latin-1") as f:
        f.write("       . cambiado por otro programa\n")
    asked = []

    def answer(*a, **k):
        asked.append(a[2])
        return SB.Reset if len(asked) == 1 else SB.No

    monkeypatch.setattr(QMessageBox, "question", answer)
    assert not w.save_all()
    if len(asked) == 1:  # (se guardan juntos: la pregunta de los dos ya lo dice)
        pytest.skip("guardar los dos a la vez no pregunta por fichero")
    assert "«3discs.geo»" in asked[-1] and "Se perderán los cambios sin guardar" in asked[-1]
    assert w.geo_ctrl.dirty and w.input.dirty  # «No»: nada cambia


# ---------------------------------------------------------------------------------------------------------------------
# Nombres con tildes
# ---------------------------------------------------------------------------------------------------------------------

def test_new_geometry_with_an_accented_name_and_its_in_is_refused(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    w = window
    seen = []

    def fill(dlg):
        dlg.path_edit.setText(str(tmp_path / "maniquí.geo"))
        dlg.start.setCurrentIndex(dlg.start.findData("body"))
        ok = dlg._check()
        seen.append((ok, dlg.status.text()))
        return ok

    monkeypatch.setattr(w, "_exec_dialog", fill)
    w.new_geometry()
    assert seen and not seen[0][0] and "no son ASCII" in seen[0][1]
    assert not (tmp_path / "maniquí.geo").exists()


def test_new_in_for_an_accented_geometry_is_refused(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    w = window
    shutil.copy(CHAIR, tmp_path / "silla_ñ.geo")
    w.open_file(tmp_path / "silla_ñ.geo", with_input=False)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]) or SB.Ok)
    w.new_input()
    assert w.input is None and warned and "no son ASCII" in warned[0]


# ---------------------------------------------------------------------------------------------------------------------
# «Manual edit» y el .in
# ---------------------------------------------------------------------------------------------------------------------

NEW_BODY = (f"{Z}\nBODY    (  10)   new body\nMATERIAL(   1)\nSURFACE (   5), SIDE POINTER=(+1)\n"
            "SURFACE (   1), SIDE POINTER=(-1)\n")


def _manual(cw, transform):
    cw.btn_manual.click()
    assert cw.manual
    cw.text.setPlainText(transform(cw.text.toPlainText()))
    cw.end_manual()


def _before_first_body(text, block):
    i = text.index(f"{Z}\nBODY    (   1)")
    return text[:i] + block + text[i:]


def test_manual_edit_through_a_broken_text_keeps_the_in_in_step(qtbot, window, stack, monkeypatch):  # noqa: F811
    w = window
    w.open_file(stack)
    cw = _enter(w, qtbot)
    one_step = None
    _manual(cw, lambda t: _before_first_body(t, NEW_BODY))
    one_step = _body_lines(w)  # lo que sale en un paso
    w.undo()
    before = _body_lines(w)
    assert one_step != before
    monkeypatch.setattr(cw, "ask_apply_broken", lambda msg: True)  # «Aplicar así»
    bad = NEW_BODY.replace("SURFACE (   1), SIDE POINTER=(-1)", "SURFACE (  77), SIDE POINTER=(-1)")
    _manual(cw, lambda t: _before_first_body(t, bad))
    assert not w.session.ok and _body_lines(w) == before
    _manual(cw, lambda t: t.replace("SURFACE (  77), SIDE POINTER=(-1)", "SURFACE (   1), SIDE POINTER=(-1)"))
    assert w.session.ok and _body_lines(w) == one_step  # lo mismo que en un paso
    w.undo()  # (el .in vuelve con la geometría)
    assert _body_lines(w) == before


def test_manual_delete_of_a_named_body_still_follows_the_others(qtbot, window, stack):  # noqa: F811
    w = window
    w.open_file(stack)
    cw = _enter(w, qtbot)
    before = _body_lines(w)

    def delete_body_1(t):
        i = t.index(f"{Z}\nBODY    (   1)")
        j = t.index(f"{Z}\nMODULE  (   2)")
        t = t[:i] + t[j:]
        return t.replace("SURFACE (   5), SIDE POINTER=(-1)\nBODY    (   1)\n", "SURFACE (   5), SIDE POINTER=(-1)\n",
                         1)

    _manual(cw, delete_body_1)
    assert w.session.ok
    assert {b.index: b.alias for b in w.session.geo.bodies} == {1: "   20", 2: "   30"}
    # Los módulos «2» y «3» son ahora los cuerpos 1 y 2: sus líneas los siguen; las del cuerpo «1», que ya no está,
    # se quedan como estaban (escrito a mano no se sabe si se ha borrado o ha cambiado de etiqueta) y se dice.
    assert before == ["DSMAX  1 0.0001 ", "DSMAX  2 0.0001 ", "DSMAX  3 0.0002 ", "IDBODY 2        ",
                      "EDBODY 1        ", "EDBODY 2        ", "EDBODY 3        "]
    assert _body_lines(w) == ["DSMAX  1 0.0001 ", "DSMAX  1 0.0001 ", "DSMAX  2 0.0002 ", "IDBODY 1        ",
                              "EDBODY 1        ", "EDBODY 1        ", "EDBODY 2        "]
    assert any("revíselas" in n for n in w.last_follow_notes)


# ---------------------------------------------------------------------------------------------------------------------
# «Geometrías complejas»: lo escrito sin aplicar
# ---------------------------------------------------------------------------------------------------------------------

def test_new_input_keeps_typed_values_in_the_complex_tab(qtbot, window, tmp_path):  # noqa: F811
    w = window
    shutil.copy(CHAIR, tmp_path / "chair.geo")
    w.open_file(tmp_path / "chair.geo", with_input=False)
    cw = _enter(w, qtbot)
    body = next(bk.block_title(el) for el in w.session.doc.elements if bk.block_tab(el)
                and bk.block_title(el).startswith("BODY"))
    cw.select_block(_index_of(w, body))
    tab = cw.current_tab()
    tab.desc.setText("escrito sin aplicar")
    tab.desc.textEdited.emit("escrito sin aplicar")
    assert cw.pending_tab() is not None
    w._new_input_at([0.0, 0.0, 0.0])
    assert w.input is not None
    assert cw.pending_tab() is not None and tab.desc.text() == "escrito sin aplicar"


# ---------------------------------------------------------------------------------------------------------------------
# Mensajes al guardar
# ---------------------------------------------------------------------------------------------------------------------

def test_a_save_error_names_the_file_not_the_temporary(qtbot, window, case_dir, monkeypatch):  # noqa: F811
    import argus.core.files as files

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("Colocar la fuente", lambda d: se.set_position(d, [1.0, 2.0, 3.0]))
    real_replace = os.replace

    def bad_replace(src, dst):
        if str(dst).endswith("maniqui.in"):
            raise PermissionError(13, "Acceso denegado", str(src), None, str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", bad_replace)
    crit = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: crit.append(a[2]) or SB.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: SB.Save)
    assert not w.save_all()
    assert crit and "argus-tmp" not in crit[0] and "maniqui.in" in crit[0] and "otro programa" in crit[0]


def test_a_save_error_when_the_folder_is_gone(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    src = tmp_path / "1_electrones_agua"
    src.mkdir()
    for n in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CORPUS / "usuario/100Mev15-174" / n, src / n)
    w = window
    w.open_file(src / "maniqui.in")
    w.input.edit("Colocar la fuente", lambda d: se.set_position(d, [1.0, 2.0, 3.0]))
    src.rename(tmp_path / "renombrada")
    crit = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: crit.append(a[2]) or SB.Ok)
    assert not w.save_all()
    assert crit and "ya no existe la carpeta" in crit[0] and "maniqui.in" in crit[0]


# ---------------------------------------------------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------------------------------------------------

def test_relabel_says_when_nothing_changes(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    w = window
    shutil.copy(CHAIR, tmp_path / "chair.geo")
    w.open_file(tmp_path / "chair.geo", with_input=False)
    cw = _enter(w, qtbot)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: SB.Yes)
    cw.relabel()
    cw.relabel()  # la segunda vez ya están
    assert "no ha cambiado nada" in cw.status.text()


def test_discard_question_wording(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    w = window
    shutil.copy(CHAIR, tmp_path / "chair.geo")
    w.open_file(tmp_path / "chair.geo", with_input=False)
    cw = _enter(w, qtbot)
    body = next(bk.block_title(el) for el in w.session.doc.elements if bk.block_tab(el)
                and bk.block_title(el).startswith("BODY"))
    cw.select_block(_index_of(w, body))
    tab = cw.current_tab()
    tab.desc.setText("x")
    tab.desc.textEdited.emit("x")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or SB.No)
    assert not cw.confirm_discard("cerrar")
    assert asked and "hay cambios sin aplicar (se aplican con Modify)" in asked[0]
