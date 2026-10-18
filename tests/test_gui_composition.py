"""Ventana: «Añadir un material» y «Composición del material N» con las dos opciones (lista de PENELOPE o composición
propia), el menú del panel Materiales y deshacer (0.14.0)."""

import pytest

pytest.importorskip("PySide6")

from argus.penhan import composition as cp  # noqa: E402
from argus.penhan.inputfile import parse_in_text  # noqa: E402
from argus.penhan.materials import material_recipe, recipes  # noqa: E402
from test_gui_modules import case_dir, fake_exec, window  # noqa: E402, F401

pytestmark = pytest.mark.gui

MLIST = [(224, "Polymethyl methacrilate (lucite, perspex, plexiglass)"), (278, "Water, liquid")]


def test_add_a_material_with_its_own_composition(qtbot, window, case_dir, monkeypatch):  # noqa: F811
    w = window
    w.open_file(case_dir / "maniqui.in")
    n = len(w.input.doc.find("MFNAME")) + 1

    def fill(dlg):
        dlg.rb_own.setChecked(True)
        dlg.name.setText("PMMA")
        dlg.formula.setText("C5H8O2")
        assert dlg.rb_atoms.isChecked()  # una fórmula: átomos por molécula
        assert not dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()  # falta la densidad
        dlg.density.setText("1,19")
        assert dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()
        assert dlg.fname() == "pmma.mat" and "73,4 eV" in dlg.rb_i_argus.text()
        return True

    fake_exec(w, monkeypatch, fill)
    w.add_material_dialog()
    doc = parse_in_text(w.input.doc.text(), None)
    assert [m.fname for m in doc.model.materials][-1] == "pmma.mat"
    assert material_recipe(doc, n).composition == cp.Composition([(6, 5), (1, 8), (8, 2)], cp.BY_ATOMS, 1.19)
    # Un solo paso de deshacer quita el material y su composición.
    w.undo()
    assert len(w.input.doc.find("MFNAME")) == n - 1 and recipes(w.input.doc) == {}


def test_edit_the_composition_and_go_back_to_the_list(qtbot, window, case_dir, monkeypatch):  # noqa: F811
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.materials._mlist = MLIST
    n = 1

    def own(dlg):
        assert dlg.edit and dlg.windowTitle() == f"Composición del material {n}"
        dlg.rb_own.setChecked(True)
        dlg.formula.setText("H 0.111894 O 0.888106")
        assert dlg.rb_mass.isChecked()  # suman 1: fracciones en masa
        dlg.density.setText("1")
        dlg.rb_i_val.setChecked(True)
        dlg.i_value.setText("78")
        return True

    fake_exec(w, monkeypatch, own)
    w.edit_material_dialog(n)
    comp = material_recipe(w.input.doc, n).composition
    assert comp.by == cp.BY_MASS and comp.i_mode == cp.I_VALUE and comp.i_value == 78.0

    def check_prefilled(dlg):
        assert dlg.rb_own.isChecked() and dlg.formula.text() == "H 0.111894 O 0.888106"
        assert dlg.density.text() == "1" and dlg.rb_i_val.isChecked() and dlg.i_value.text() == "78"
        assert not dlg.material_changed()
        dlg.rb_list.setChecked(True)
        dlg.name.setText("Water, liquid (PENELOPE 278)")
        assert "n.º 278" in dlg.list_state.text()
        return True

    fake_exec(w, monkeypatch, check_prefilled)
    w.edit_material_dialog(n)
    assert material_recipe(w.input.doc, n) is None  # de la lista de PENELOPE: sin composición propia
    assert "PENELOPE 278" in w.input.doc.find("MFNAME")[0].comment


def test_panel_menu_and_choosing_from_the_list_in_the_table(qtbot, window, case_dir,  # noqa: F811
                                                          monkeypatch):
    from argus.penhan.materials import set_recipe
    from argus.ui.materials_panel import COL_NAME, ROLE_NUMBER

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("comp", lambda d: set_recipe(d, 1, cp.Composition([(1, 2), (8, 1)], density=1.0)))
    w.materials.refresh()
    t = w.materials.table
    row = next(r for r in range(t.rowCount()) if t.item(r, 0).data(ROLE_NUMBER) == 1)
    tip = t.item(row, COL_NAME).toolTip()
    assert "Composición propia" in tip and "H2O" in tip
    seen = []
    w.materials.compositionRequested.connect(seen.append)
    opened = fake_exec(w, monkeypatch, lambda dlg: False)  # la ventana abre el diálogo: se cierra sin aceptar
    menu = w.materials.build_menu(t.visualItemRect(t.item(row, COL_NAME)).center())
    action = next(a for a in menu.actions() if a.text() == "Composición del material 1…")
    action.trigger()
    assert seen == [1] and len(opened) == 1 and opened[0].rb_own.isChecked()
    menu.deleteLater()
    # Elegir en la tabla un material de la lista de PENELOPE quita la composición propia.
    t.item(row, COL_NAME).setText("Water, liquid (PENELOPE 278)")
    w.materials.flush_pending()
    assert material_recipe(w.input.doc, 1) is None


def test_existing_mat_with_another_composition_offers_another_name(qtbot, tmp_path):
    from argus.ui.add_material_dialog import MaterialDialog

    (tmp_path / "pmma.mat").write_text(
        " PENELOPE (v. 2018)  Material data file ...............\n Material: PMMA\n"
        " Mass density = 1.18000000E+00 g/cm**3\n Number of elements in the molecule =  3\n"
        "   atomic number =  6,  atoms/molecule = 5.00000000E+00\n"
        "   atomic number =  1,  atoms/molecule = 8.00000000E+00\n"
        "   atomic number =  8,  atoms/molecule = 2.00000000E+00\n"
        " Mean excitation energy = 7.34000000E+01 eV\n", encoding="latin-1")
    dlg = MaterialDialog(None, 2, MLIST, tmp_path, set(), "", "#888888", fname="pmma.mat", description="PMMA",
                         recipe=cp.Composition([(6, 5), (1, 8), (8, 2)], cp.BY_ATOMS, 1.18), edit=True)
    qtbot.addWidget(dlg)
    assert not dlg.btn_newfile.isVisibleTo(dlg)  # el que hay es esta composición
    dlg.density.setText("1.19")
    assert dlg.btn_newfile.isVisibleTo(dlg) and "otra densidad" in dlg.status.text()
    dlg.propose_new_file()
    assert dlg.fname() == "pmma2.mat" and not dlg.btn_newfile.isVisibleTo(dlg)
