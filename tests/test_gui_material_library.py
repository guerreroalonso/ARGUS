"""Ventana: los materiales (0.16.0, ADR-020). El fallo al pasar de «lista de PENELOPE» a «composición propia» y
volver, el campo del material que despliega la lista y la acota al escribir, los materiales personalizados (guardar,
elegir, editar, duplicar, borrar, exportar e importar), «Asignar materiales…» para los que ARGUS no identifica, la
carpeta de los ENDF y los textos de «Trayectorias» y «Lluvias» al ejecutar."""

import os
import shutil
import sys
import traceback

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox  # noqa: E402

from argus.penhan import composition as cp  # noqa: E402
from argus.penhan.inputfile import parse_in_text  # noqa: E402
from argus.penhan.library import Library, LibraryMaterial  # noqa: E402
from argus.penhan.materials import material_recipe  # noqa: E402
from test_gui_modules import case_dir, fake_exec, window  # noqa: E402, F401
from test_material_library import BRASS, IN, MLIST, WATER_MAT  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture
def errors(monkeypatch):
    """Las excepciones en los slots (que Qt no deja pasar) cuentan como fallos."""
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: seen.append("".join(traceback.format_exception(t, v, tb))))
    return seen


@pytest.fixture
def library(monkeypatch, tmp_path):
    """La biblioteca del usuario, en una carpeta de datos propia de esta prueba (con el latón)."""
    monkeypatch.setenv("ARGUS_DATA_DIR", str(tmp_path / "datos"))
    lib = Library()
    lib.put(LibraryMaterial("Latón 70/30", BRASS, "#c8a040"))
    return lib


def _dialog(qtbot, tmp_path, **kw):
    from argus.ui.add_material_dialog import MaterialDialog

    dlg = MaterialDialog(None, 2, MLIST, tmp_path, {"agua.mat"}, "", "#888888", **kw)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    return dlg


# -- el diálogo del material -------------------------------------------------------------------------------------------

def test_switching_between_list_and_own_composition_does_not_break(qtbot, tmp_path, errors, library):
    """0.14–0.15: el completador de la lista era hijo del campo y Qt lo borraba al quitárselo (composición propia):
    al volver a la lista, «Internal C++ object already deleted» (el error que vio Rafael)."""
    dlg = _dialog(qtbot, tmp_path)
    for _ in range(3):
        dlg.rb_own.click()
        dlg.rb_list.click()
    dlg.name.setText("")
    QTest.keyClicks(dlg.name.edit, "agua")
    assert [e.text for e in dlg.name.visible_entries()] == ["Agua líquida (PENELOPE 278)",
                                                           "Vapor de agua (PENELOPE 279)"]
    assert dlg.list_state.isVisibleTo(dlg) and not dlg.own_box.isVisibleTo(dlg)
    assert not errors


def test_the_material_field_lists_everything_and_narrows_while_typing(qtbot, tmp_path, errors, library):
    dlg = _dialog(qtbot, tmp_path)
    dlg.name.show_all()  # ▾ (o ↓): todos, los personalizados delante
    shown = dlg.name.visible_entries()
    assert len(shown) == len(MLIST) + 1 and shown[0].text == "Latón 70/30" and shown[0].personal
    dlg.name.setText("")
    QTest.keyClicks(dlg.name.edit, "hueso cort")
    assert [e.text for e in dlg.name.visible_entries()] == ["Hueso cortical (ICRP) (PENELOPE 120)"]
    popup = dlg.name.completer.popup()
    dlg.name.completer.activated[QModelIndex].emit(popup.model().index(0, 0))  # elegido en la lista (su fila)
    assert dlg.description() == "Hueso cortical (ICRP) (PENELOPE 120)" and dlg.mode() == "lista"
    assert "n.º 120" in dlg.list_state.text() and dlg.fname() == "hueso.mat"
    assert dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()
    # Con ↓ en el campo vacío también se despliega.
    dlg.name.setText("")
    dlg.name.completer.popup().hide()
    QTest.keyClick(dlg.name.edit, Qt.Key.Key_Down)
    assert len(dlg.name.visible_entries()) == len(MLIST) + 1
    assert not errors


def test_a_personal_material_brings_its_composition_and_color(qtbot, tmp_path, errors, library):
    dlg = _dialog(qtbot, tmp_path)
    entry = next(e for e in dlg.name.entries if e.personal)
    dlg.name.pick(entry)
    assert dlg.mode() == "propia" and dlg.composition() == BRASS and dlg.color() == "#c8a040"
    assert dlg.formula.text() == "Cu 70 Zn 30" and dlg.rb_mass.isChecked() and dlg.fname() == "laton.mat"
    # Elegir después uno de la lista vuelve a la lista.
    dlg.name.pick(next(e for e in dlg.name.entries if e.number == 278))
    assert dlg.mode() == "lista" and dlg.composition() is None and dlg.fname() == "agua2.mat"
    assert not errors


def test_saving_a_new_composition_in_the_personal_materials(qtbot, tmp_path, errors, library):
    steel = cp.Composition([(26, 70.0), (24, 18.0), (28, 12.0)], cp.BY_MASS, 7.9)
    dlg = _dialog(qtbot, tmp_path)
    dlg.rb_own.setChecked(True)
    dlg.save_lib.setChecked(True)
    dlg.formula.setText("Fe 70 Cr 18 Ni 12")
    dlg.density.setText("7,9")
    assert not dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()  # para guardarlo hace falta el nombre
    assert "nombre del material" in dlg.status.text()
    dlg.name.setText("Acero 18/12")
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted and dlg.saved_to_library == "Acero 18/12"
    assert Library().find("Acero 18/12").composition == steel
    # Otro con el mismo nombre y otra composición: se pregunta (sustituir, guardarlo con otro nombre o cancelar).
    for answer, expected in (("other", "Acero 18/12 (2)"), (None, None), ("replace", "Acero 18/12")):
        dlg = _dialog(qtbot, tmp_path)
        dlg.rb_own.setChecked(True)
        dlg.save_lib.setChecked(True)
        dlg.name.setText("Acero 18/12")
        dlg.formula.setText("Fe 68 Cr 18 Ni 12 Mo 2")
        dlg.density.setText("8")
        dlg.ask_library_conflict = lambda have, other, a=answer: a
        dlg.accept()
        if expected is None:
            assert dlg.result() != QDialog.DialogCode.Accepted and dlg.isVisible()  # sigue abierto
        else:
            assert dlg.result() == QDialog.DialogCode.Accepted and dlg.saved_to_library == expected
            assert Library().find(expected).composition.density == 8.0
    assert Library().names() == ["Latón 70/30", "Acero 18/12", "Acero 18/12 (2)"]
    assert not errors


def test_the_personal_materials_dialog(qtbot, tmp_path, errors, library, monkeypatch):
    from argus.ui.material_library_dialog import LibraryDialog, LibraryMaterialDialog

    plan = []  # lo que «escribe» el usuario en el siguiente diálogo del material

    def fake_exec(self):
        name, formula, density = plan.pop(0)
        if name is not None:
            self.name.setText(name)
        self.editor.formula.setText(formula)
        self.editor.density.setText(density)
        self.accept()
        return QDialog.DialogCode.Accepted if self.saved is not None else QDialog.DialogCode.Rejected

    monkeypatch.setattr(LibraryMaterialDialog, "exec", fake_exec)
    dlg = LibraryDialog(None, Library())
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 1 and not dlg.btn_edit.isEnabled()
    plan.append(("PMMA de la casa", "C5H8O2", "1,18"))
    dlg.new()
    assert dlg.library.names() == ["Latón 70/30", "PMMA de la casa"]
    dlg.table.selectRow(0)
    plan.append((None, "Cu 65 Zn 35", "8.47"))  # duplicar y cambiar la proporción (el nombre propuesto se queda)
    dlg.duplicate_selected()
    copy = dlg.library.find("Latón 70/30 (copia)")
    assert copy is not None and copy.composition.elements == [(29, 65.0), (30, 35.0)]
    assert dlg.library.find("Latón 70/30").composition == BRASS  # el original no cambia
    dlg.table.selectRow(2)
    plan.append(("Latón 65/35", "Cu 65 Zn 35", "8.47"))  # editar: cambiarle el nombre
    dlg.edit_selected()
    assert dlg.library.names() == ["Latón 70/30", "PMMA de la casa", "Latón 65/35"]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    dlg.table.selectRow(1)
    dlg.delete_selected()
    assert Library().names() == ["Latón 70/30", "Latón 65/35"]  # (guardado en el fichero)
    # Exportar e importar (en otra biblioteca: la de un compañero).
    out = tmp_path / "mis_materiales.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    dlg.export()
    other = Library(tmp_path / "otro" / "m.json")
    other.put(LibraryMaterial("Latón 70/30", cp.Composition([(29, 60.0), (30, 40.0)], cp.BY_MASS, 8.4)))
    dlg2 = LibraryDialog(None, other)
    qtbot.addWidget(dlg2)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(out), ""))
    dlg2.import_()
    assert other.names() == ["Latón 70/30", "Latón 70/30 (importado)", "Latón 65/35"]
    assert dlg2.table.rowCount() == 3
    assert not errors


def test_a_badly_written_personal_material_can_be_opened_and_fixed(qtbot, tmp_path, monkeypatch, errors):
    """Un fichero de materiales personalizados escrito a mano con la energía media de excitación «valor» y sin él:
    se lista con su problema, no se ofrece al elegir y se puede abrir para arreglarlo (antes, al abrirlo, un error)."""
    import json

    from argus.penhan.library import KIND
    from argus.ui.material_library_dialog import LibraryDialog, LibraryMaterialDialog

    monkeypatch.setenv("ARGUS_DATA_DIR", str(tmp_path / "datos"))
    (tmp_path / "datos").mkdir()
    bad = {"name": "Mal escrito", "elements": [[1, 2.0], [8, 1.0]], "by": cp.BY_ATOMS, "density": 1.0,
           "i_mode": cp.I_VALUE, "i_value": None}
    from argus.penhan.library import library_file

    library_file().write_text(json.dumps({"kind": KIND, "version": 1, "materials": [bad]}), encoding="utf-8")
    lib = Library()
    assert lib.items[0].problem and "excitación" in lib.items[0].problem
    from argus.ui.add_material_dialog import library_entries

    assert library_entries(lib) == []  # no se ofrece para elegirlo
    seen = []

    def fake_exec(self):
        seen.append(self.editor.i_value.text())
        self.editor.i_value.setText("75")
        self.editor.refresh()
        self.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(LibraryMaterialDialog, "exec", fake_exec)
    dlg = LibraryDialog(None, lib)
    qtbot.addWidget(dlg)
    assert "✖" in dlg.table.item(0, 2).text() or "✖" in dlg.table.item(0, 1).text()
    dlg.table.selectRow(0)
    dlg.edit_selected()
    assert seen == [""] and Library().items[0].problem is None and Library().items[0].composition.i_value == 75.0
    assert not errors


# -- la ventana: asignar los materiales que ARGUS no identifica --------------------------------------------------------

def test_assigning_unidentified_materials(qtbot, window, case_dir, monkeypatch, errors, library):  # noqa: F811
    from argus.penhan.inputfile import parse_in_file

    w = window
    w.open_file(case_dir / "maniqui.in")  # «MFNAME water.mat» sin nombre: ARGUS no sabe qué es
    w.materials._mlist = MLIST
    w.materials.refresh()
    assert [r.fname for r in w.materials.unknown] == ["water.mat"]
    assert "Asignar materiales" in w.materials.info.text()

    def check_proposal(dlg):
        assert dlg.pickers[1].text() == "Agua líquida (PENELOPE 278)"  # propuesto por el nombre del fichero
        assert "propuesto" in dlg.hints[1].text()
        return True

    fake_exec(w, monkeypatch, check_proposal)
    w.materials._on_link("asignar")  # el enlace del panel
    assert w.input.doc.find("MFNAME")[0].comment.strip().startswith("[Agua líquida (PENELOPE 278)]")
    assert w.materials.unknown == []
    w.undo()  # un solo paso de deshacer
    assert w.materials.unknown and material_recipe(w.input.doc, 1) is None

    def personal(dlg):
        dlg.pickers[1].setText("Latón 70/30")
        return True

    fake_exec(w, monkeypatch, personal)
    assert w.assign_materials()
    assert material_recipe(w.input.doc, 1).composition == BRASS
    assert w.material_colors.get(1) == "#c8a040"
    w.save_input()
    saved = parse_in_file(case_dir / "maniqui.in")
    assert material_recipe(saved, 1).composition == BRASS
    assert not errors


def test_choosing_a_personal_material_in_the_table(qtbot, window, case_dir, errors, library):  # noqa: F811
    from argus.ui.materials_panel import COL_NAME, ROLE_NUMBER

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.materials._mlist = MLIST
    w.materials.reload_library()
    t = w.materials.table
    row = next(r for r in range(t.rowCount()) if t.item(r, 0).data(ROLE_NUMBER) == 1)
    t.item(row, COL_NAME).setText("Latón 70/30")  # lo que deja el editor del nombre al elegirlo
    w.materials.flush_pending()
    assert material_recipe(w.input.doc, 1).composition == BRASS
    t = w.materials.table
    row = next(r for r in range(t.rowCount()) if t.item(r, 0).data(ROLE_NUMBER) == 1)
    t.item(row, COL_NAME).setText("Agua líquida (PENELOPE 278)")  # y uno de la lista le quita la composición
    w.materials.flush_pending()
    assert material_recipe(w.input.doc, 1) is None
    assert not errors


# -- «Generar .mat y ENDF…»: asignar y la carpeta de los ENDF ----------------------------------------------------------

def test_the_generate_dialog_offers_to_assign_and_to_choose_the_endf_folder(qtbot, tmp_path, monkeypatch, errors,
                                                                          library):
    from argus.penhan import programs as pg
    from argus.ui.penhan_dialogs import CompleteMaterialsDialog
    from argus.ui.settings import PENHAN_DB_KEY, app_settings, penhan_setup

    db = tmp_path / "penhan" / "database"  # sin ENDF en database/ENDF_data
    for sub in pg.DATABASE_PARTS:
        (db / sub).mkdir(parents=True)
    (db / "material-list.txt").write_text("".join(f"{n:3d}  {name}\n" for n, name in MLIST))
    case = tmp_path / "caso"
    case.mkdir()
    (case / "hueso.mat").write_text(WATER_MAT, encoding="latin-1")  # con su lista de ENDF (que faltan)
    doc = parse_in_text(IN, str(case / "caso.in"))
    s = app_settings()
    s.clear()
    s.setValue(PENHAN_DB_KEY, str(db))
    calls = []

    def assign():
        from argus.penhan.materials import set_material

        calls.append(1)
        set_material(doc, 1, description="Agua líquida (PENELOPE 278)")
        return True

    dlg = CompleteMaterialsDialog(None, doc, None, case, s, assign=assign)
    qtbot.addWidget(dlg)
    jobs = {j.fname: j for j in dlg.plan.mats}
    assert jobs["agua.mat"].unknown and jobs["raro.mat"].unknown and dlg.btn_assign.isVisibleTo(dlg)
    dlg.btn_assign.click()
    jobs = {j.fname: j for j in dlg.plan.mats}
    assert calls and jobs["agua.mat"].possible and not jobs["agua.mat"].unknown
    # Los ENDF que lista hueso.mat no se encuentran (no se sabe dónde están): se elige su carpeta.
    assert dlg.btn_endf.isVisibleTo(dlg) and dlg.plan.endf_missing
    endf = tmp_path / "mis_endf"
    endf.mkdir()
    for name in ("pO16_ICRU63.endf", "pO17_TENDL.endf"):
        (endf / name).write_text("ENDF\n")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(endf))
    dlg.btn_endf.click()
    assert penhan_setup(s).endf == endf
    assert not dlg.plan.endf_missing and {e.source.parent for e in dlg.plan.endf_to_copy} == {endf}
    assert not dlg.btn_endf.isVisibleTo(dlg)
    s.clear()
    assert not errors


def test_the_endf_folder_in_penhan_programs(qtbot, tmp_path, errors):
    from argus.ui.penhan_dialogs import PenhanSetupDialog
    from argus.ui.settings import app_settings

    db = tmp_path / "penhan" / "database"
    for sub in ("pdfiles", "pdhan", "ENDF_data"):
        (db / sub).mkdir(parents=True)
    (db / "ENDF_data" / "pO16_ICRU63.endf").write_text("ENDF\n")
    s = app_settings()
    s.clear()
    dlg = PenhanSetupDialog(None, s)
    qtbot.addWidget(dlg)
    dlg.database.setText(str(db))
    assert dlg.endf.text() == str(db / "ENDF_data")  # la de la base de datos, propuesta
    dlg._update()
    assert "1 fichero ENDF" in dlg.endf_state.text()
    assert dlg.setup().endf is None  # la de siempre: sigue a la base de datos
    other = tmp_path / "otros_endf"
    other.mkdir()
    dlg.endf.setText(str(other))
    dlg._update()
    assert dlg.setup().endf == other and "no parece tener" in dlg.endf_state.text()
    s.clear()
    assert not errors


# -- «Ejecutar»: trayectorias y lluvias --------------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="el penhmain de mentira es un guion de sh")
def test_trajectories_and_showers_say_what_they_are(qtbot, tmp_path):
    from argus.ui.settings import PENHMAIN_KEY, app_settings
    from test_run import _case, _fake_penhmain
    from test_run_ui import _dialog as run_dialog
    from test_run_ui import _headers

    s = app_settings()
    s.setValue(PENHMAIN_KEY, str(_fake_penhmain(tmp_path / "programas")))
    s.setValue("cascada/al_ejecutar", "false")
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    dlg, _ctrl = run_dialog(qtbot, in_path, s)
    dlg.showers.setValue(1000)
    assert dlg.showers_text.text().endswith("20 tandas de 50")
    dlg.showers.setValue(30)
    assert dlg.showers_text.text().endswith("una tanda")
    dlg.showers.setValue(0)
    assert "sin cascada" in dlg.showers_text.text()
    labels = [dlg.job.form.itemAt(i, dlg.job.form.ItemRole.LabelRole).widget().text() for i in range(3)]
    assert labels == ["Partículas:", "Tiempo máx.:", "Lluvias:"]
    assert "tandas de 50" in dlg.showers.toolTip() and "límite fijo de penhmain" in dlg.job.ltrack.toolTip()
    assert "LTRACK + 1" in dlg.job.ltrack.toolTip() and "como mucho 51" in dlg.showers_note.text()
    s.remove(PENHMAIN_KEY)
    s.remove("cascada")


def test_old_material_names_still_work(tmp_path):
    """Los .in de antes (con «Water, liquid (PENELOPE 278)» o el nombre de la lista en inglés) se siguen
    reconociendo; los nuevos llevan el nombre en español."""
    from argus.penhan.materials import match_material

    assert match_material("Water, liquid (PENELOPE 278)", MLIST)[0] == 278
    assert match_material("Water, liquid", MLIST)[0] == 278
    shutil.rmtree(tmp_path, ignore_errors=True)


# -- revisión de 0.16 (0.16.2) -----------------------------------------------------------------------------------------

def _type_then(qtbot, picker, text: str, *keys) -> None:
    """Escribe en el campo del material y, con la lista desplegada (que tiene el teclado), pulsa ``keys``."""
    picker.edit.setFocus()
    QTest.keyClicks(picker.edit, text)
    popup = picker.completer.popup()
    qtbot.waitUntil(popup.isVisible, timeout=3000)
    for key in keys:
        QTest.keyClick(popup, key)
    qtbot.wait(30)


def _picker(qtbot):
    from argus.penhan.material_names import penelope_entries
    from argus.ui.material_picker import MaterialPicker

    p = MaterialPicker()
    qtbot.addWidget(p)
    p.show()
    qtbot.waitExposed(p)
    p.set_entries(penelope_entries(MLIST))
    return p


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Tab])
def test_enter_or_tab_with_the_list_open_keep_what_was_typed(qtbot, errors, key):
    """Revisión de 0.16: con la lista desplegada al escribir, Entrar o Tab elegían la primera fila sin haberla
    señalado («agua» → «Agua líquida (PENELOPE 278)»). Lo escrito sin elegir se queda; con ↓ y Entrar se elige."""
    p = _picker(qtbot)
    got = []
    p.picked.connect(got.append)
    _type_then(qtbot, p, "agua", key)
    assert p.text() == "agua" and not got
    q = _picker(qtbot)
    q.picked.connect(got.append)
    _type_then(qtbot, q, "agua", Qt.Key.Key_Down, Qt.Key.Key_Return)
    assert q.text() == "Agua líquida (PENELOPE 278)" and [e.number for e in got] == [278]
    assert not errors


def test_enter_in_the_own_composition_name_keeps_the_composition(qtbot, tmp_path, errors, library):
    """Revisión de 0.16: en «Composición propia», el nombre «Agua» y Entrar pasaban el diálogo a la lista de PENELOPE,
    y un segundo Entrar añadía el n.º 278 en vez de la composición escrita."""
    dlg = _dialog(qtbot, tmp_path)
    dlg.activateWindow()
    dlg.rb_own.setChecked(True)
    dlg.formula.setText("H2O")
    dlg.density.setText("1.05")
    _type_then(qtbot, dlg.name, "Agua", Qt.Key.Key_Return)
    assert dlg.description() == "Agua" and dlg.mode() == "propia"
    QTest.keyClick(dlg.name.edit, Qt.Key.Key_Return)
    qtbot.wait(30)
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.choice().composition == cp.Composition([(1, 2), (8, 1)], cp.BY_ATOMS, 1.05)
    assert not errors


def test_the_proposed_mat_file_is_never_one_of_another_material(qtbot, tmp_path, errors, library):
    """Revisión de 0.16: en la carpeta ya está hueso.mat del n.º 119 (de otro .in, o de un material que se quitó). Al
    añadir el «Hueso cortical (ICRP)» (n.º 120) se proponía hueso.mat y el diálogo decía «✔ está en la carpeta»:
    penhmain habría usado el 119. Ahora se propone uno libre, el que ya es ese material se aprovecha y, si se escribe a
    mano el de otro, avisa (con el botón para usar otro nombre)."""
    import html as _html

    from test_material_library import mat_header

    (tmp_path / "hueso.mat").write_text(mat_header("BONE, COMPACT (ICRU)", 119, 1.85), encoding="latin-1")
    dlg = _dialog(qtbot, tmp_path)
    dlg.name.pick(next(e for e in dlg.name.entries if e.number == 120))
    assert dlg.fname() == "hueso2.mat" and "✔" not in dlg.status.text()
    dlg.name.pick(next(e for e in dlg.name.entries if e.number == 119))
    assert dlg.fname() == "hueso.mat" and "✔ hueso.mat está en la carpeta" in dlg.status.text()
    dlg.name.pick(next(e for e in dlg.name.entries if e.number == 120))
    dlg.file.selectAll()
    QTest.keyClicks(dlg.file, "hueso.mat")  # escrito a mano
    status = _html.unescape(dlg.status.text())
    assert "es el n.º 119 de la lista de PENELOPE (BONE, COMPACT (ICRU)): penhmain usaría ese" in status
    assert dlg.btn_newfile.isVisibleTo(dlg)
    dlg.btn_newfile.click()
    assert dlg.fname() == "hueso2.mat"
    assert not errors


def test_an_own_composition_does_not_take_the_mat_of_another_one(qtbot, tmp_path, errors, library):
    """Lo mismo con composición propia: mezcla.mat ya está y es agua de 1 g/cm³; «Mezcla» de 1,05 g/cm³ propone
    mezcla2.mat, y con la misma composición se aprovecha mezcla.mat."""
    from test_material_library import mat_header

    water = cp.Composition([(1, 2), (8, 1)], cp.BY_ATOMS, 1.0)
    (tmp_path / "mezcla.mat").write_text(mat_header("MEZCLA", None, 1.0, ivalue=water.i_value_written()),
                                         encoding="latin-1")
    dlg = _dialog(qtbot, tmp_path)
    dlg.rb_own.setChecked(True)
    dlg.name.setText("Mezcla")
    dlg.formula.setText("H2O")
    dlg.density.setText("1.05")
    assert dlg.fname() == "mezcla2.mat"
    dlg.density.setText("1")
    assert dlg.fname() == "mezcla.mat" and "✔ mezcla.mat está en la carpeta" in dlg.status.text()
    assert not errors


def test_a_personal_material_is_never_taken_for_the_list_one(qtbot, tmp_path, errors, library):
    """Revisión de 0.16: se elegía por el texto y los personalizados van delante, así que uno guardado con el nombre
    de uno de la lista («Agua líquida (PENELOPE 278)», al pasar desde él a composición propia) ocupaba su lugar al
    pulsar la fila de PENELOPE. Ahora el nombre deja de llevar «(PENELOPE n)» al pasar a composición propia (y lo
    recupera al volver), la biblioteca no lo admite y la lista elige por la fila pulsada."""
    dlg = _dialog(qtbot, tmp_path)
    dlg.name.pick(next(e for e in dlg.name.entries if e.number == 278))
    dlg.rb_own.click()
    assert dlg.description() == "Agua líquida"
    dlg.rb_list.click()
    assert dlg.description() == "Agua líquida (PENELOPE 278)" and dlg.list_item()[0] == 278
    dlg.rb_own.click()
    dlg.formula.setText("H2O")
    dlg.density.setText("1.05")
    dlg.save_lib.setChecked(True)
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted and dlg.saved_to_library == "Agua líquida"
    other = _dialog(qtbot, tmp_path)
    other.name.setText("")
    _type_then(qtbot, other.name, "agua")
    shown = other.name.visible_entries()
    row = next(i for i, e in enumerate(shown) if e.number == 278)
    popup = other.name.completer.popup()
    idx = popup.model().index(row, 0)
    popup.scrollTo(idx)
    QTest.mouseClick(popup.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     popup.visualRect(idx).center())
    qtbot.wait(50)
    assert other.mode() == "lista" and other.composition() is None and other.list_item()[0] == 278
    assert not errors


def test_assign_proposes_the_list_material_even_with_an_old_personal_one_named_like_it(qtbot, tmp_path, errors,
                                                                                      library):
    """Uno guardado así por 0.16.0 («Agua líquida (PENELOPE 278)» en la biblioteca) se lee como «Agua líquida», y
    «Asignar materiales…» asigna el n.º 278 que propone para water.mat, no la composición personalizada."""
    import json

    from argus.penhan.library import KIND, library_file
    from argus.penhan.materials import materials_table, unidentified
    from argus.ui.assign_materials_dialog import AssignMaterialsDialog

    data = json.loads(library_file().read_text(encoding="utf-8"))
    data["materials"].append({"name": "Agua líquida (PENELOPE 278)", "elements": [[1, 2], [8, 1]], "by": "atomos",
                              "density": 1.05, "i_mode": "argus"})
    assert data["kind"] == KIND
    library_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    doc = parse_in_text("TITLE  x\nSKPAR  1\nSENERG 1e6\nMFNAME water.mat\nMSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\n"
                        "GEOMFN c.geo\nNSIMSH 10\n", str(tmp_path / "c.in"))
    lib = Library()
    assert "Agua líquida" in lib.names()
    dlg = AssignMaterialsDialog(None, unidentified(materials_table(doc, None, tmp_path), MLIST), MLIST, lib)
    qtbot.addWidget(dlg)
    assert dlg.pickers[1].text() == "Agua líquida (PENELOPE 278)"
    (a,) = dlg.assignments()
    assert a.composition is None and a.description == "Agua líquida (PENELOPE 278)"
    assert not errors


@pytest.mark.parametrize("bad", [
    {"name": "Z 120 (errata)", "elements": [[120, 1]], "by": "atomos", "density": 1.0, "i_mode": "argus"},
    {"name": "Z negativo", "elements": [[-1, 1]], "by": "atomos", "density": 1.0, "i_mode": "argus"},
    {"name": "Densidad NaN", "elements": [[1, 2], [8, 1]], "by": "atomos", "density": float("nan"), "i_mode": "argus"},
    {"name": "Densidad 0", "elements": [[1, 2], [8, 1]], "by": "atomos", "density": 0, "i_mode": "argus"},
    {"name": "Cantidad negativa", "elements": [[1, -2], [8, 1]], "by": "atomos", "density": 1.0, "i_mode": "argus"},
])
def test_the_personal_materials_dialog_opens_with_a_badly_written_material(qtbot, monkeypatch, tmp_path, errors,
                                                                           bad):
    """Revisión de 0.16 (ADR-020: «un material mal escrito a mano en el fichero se lista con su problema y se puede
    abrir y arreglar»): con un número atómico fuera de 1-99, una densidad NaN o 0 o una cantidad negativa, el diálogo
    no llegaba a abrirse. Ahora se lista con su problema, se abre y, arreglado, se guarda."""
    import json

    from argus.penhan.library import KIND, library_file
    from argus.ui.material_library_dialog import LibraryDialog, LibraryMaterialDialog

    monkeypatch.setenv("ARGUS_DATA_DIR", str(tmp_path / "datos"))
    library_file().parent.mkdir(parents=True, exist_ok=True)
    library_file().write_text(json.dumps({"kind": KIND, "version": 1, "materials": [
        {"name": "Bueno", "elements": [[1, 2], [8, 1]], "by": "atomos", "density": 1.0, "i_mode": "argus"}, bad]}),
        encoding="utf-8")
    lib = Library()
    assert len(lib.items) == 2 and lib.items[1].problem
    dlg = LibraryDialog(None, lib)
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 2 and dlg.table.item(1, 1).text().startswith("✖")
    m = lib.items[1]
    edit = LibraryMaterialDialog(dlg, lib, title="Editar", name=m.name, composition=m.composition, color=m.color,
                                 replace=m.name)
    qtbot.addWidget(edit)
    assert not edit.buttons.button(edit.buttons.StandardButton.Ok).isEnabled()  # tal como está, no vale
    edit.editor.formula.setText("H2O")
    edit.editor.density.setText("1")
    edit.accept()
    assert edit.saved is not None and Library().find(m.name).problem is None
    assert not errors


def test_own_composition_in_assign_is_applied_with_the_rest_and_cancel_changes_nothing(  # noqa: F811
        qtbot, window, case_dir, monkeypatch, errors, library):  # noqa: F811
    """Revisión de 0.16: «Composición propia…» dentro de «Asignar materiales…» escribía ya en el .in (un paso de
    deshacer aparte, y «Cancelar» no lo quitaba); además, al cerrar ese diálogo se cerraba también «Asignar
    materiales…» (lo borraba la ventana al mostrar el siguiente). Ahora el de fuera sigue abierto y todo se aplica al
    «Asignar», en un solo paso."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from argus.penhan.materials import set_material

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.materials._mlist = MLIST
    w.input.edit("otro", lambda d: set_material(d, 2, fname="plomo.mat", description=""))
    w.materials.refresh()
    assert [r.fname for r in w.materials.unknown] == ["water.mat", "plomo.mat"]

    def state():
        mf = w.input.doc.find("MFNAME")
        return [(ln.text.strip(), ln.comment.strip(), material_recipe(w.input.doc, i + 1)) for i, ln in enumerate(mf)]

    before, depth = state(), w.input.stack.index()
    seen = []

    def drive(finish: str):
        def step():
            top = QApplication.activeModalWidget()
            name = type(top).__name__ if top is not None else None
            if name == "AssignMaterialsDialog" and not seen:
                seen.append(name)
                QTimer.singleShot(0, lambda: top._compose(top.rows[1]))
            elif name == "MaterialDialog":
                seen.append(name)
                top.rb_own.setChecked(True)
                top.formula.setText("Pb")
                top.density.setText("11.35")
                top.accept()
            elif name == "AssignMaterialsDialog":
                seen.append((name, sorted(top.composed)))
                (top.accept if finish == "asignar" else top.reject)()
                return
            QTimer.singleShot(50, step)

        QTimer.singleShot(50, step)

    drive("cancelar")
    assert not w.assign_materials()
    assert seen == ["AssignMaterialsDialog", "MaterialDialog", ("AssignMaterialsDialog", [2])]
    assert state() == before and w.input.stack.index() == depth  # «Cancelar»: nada
    seen.clear()
    drive("asignar")
    assert w.assign_materials()
    after = state()
    assert after[1][2] is not None and after[1][2].composition.elements == [(82, 1.0)]
    assert after[0][1].startswith("[Agua líquida (PENELOPE 278)]")  # propuesto para water.mat, con lo demás
    assert w.input.stack.index() == depth + 1
    w.undo()
    assert state() == before
    assert not errors


def test_one_invalid_file_name_does_not_stop_the_other_assignments(  # noqa: F811
        qtbot, window, case_dir, monkeypatch, errors, library):  # noqa: F811
    """Revisión de 0.16: con un .mat en una subcarpeta («mats/agua.mat») que recibe un personalizado, set_recipe
    fallaba y no se asignaba ninguno (ni Plomo en la otra fila), con un mensaje que hablaba de espacios. Ahora la fila
    dice por qué no se puede y las demás se asignan."""
    from argus.penhan.materials import set_material

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.materials._mlist = MLIST
    w.input.edit("x", lambda d: (set_material(d, 1, fname="mats/agua.mat"),
                                 set_material(d, 2, fname="plomo.mat", description="")))
    w.materials.reload_library()
    w.materials.refresh()
    assert [r.fname for r in w.materials.unknown] == ["mats/agua.mat", "plomo.mat"]
    hints = []

    def pick(dlg):
        dlg.pickers[1].setText("Latón 70/30")
        hints.append(dlg.hints[1].text())
        assert dlg.pickers[2].text() == "Plomo (PENELOPE 82)"
        return True

    fake_exec(w, monkeypatch, pick)
    assert w.assign_materials()
    assert "está en otra carpeta" in hints[0] and "Así no se asigna" in hints[0]
    mf = w.input.doc.find("MFNAME")
    assert mf[1].comment.strip().startswith("[Plomo (PENELOPE 82)]") and material_recipe(w.input.doc, 1) is None
    assert not errors


def test_the_help_knows_a_list_material_by_its_number_without_the_list():
    """Revisión de 0.16: sin la lista de PENELOPE, la ayuda del panel decía «ARGUS no sabe su composición» de
    «Agua líquida (PENELOPE 278)»."""
    from argus.penhan.materials import materials_table
    from argus.ui.add_material_dialog import describe_origin

    doc = parse_in_text("TITLE  x\nSKPAR  1\nSENERG 1e6\nMFNAME agua.mat             [Agua líquida (PENELOPE 278)]\n"
                        "MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN c.geo\nNSIMSH 10\n", "/no/existe/c.in")
    (row,) = materials_table(doc, None, None)
    assert describe_origin(row, []) == "Lista de PENELOPE: n.º 278 (penhmat, opción 2)."


# -- segunda pasada de la revisión de 0.16 ----------------------------------------------------------------------------

def _answer_in_order(handlers):
    """Contesta los diálogos modales en orden: ``handlers`` es una lista de (nombre del tipo, función); cada función
    recibe el diálogo de arriba cuando es de ese tipo y no es el del paso anterior."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    todo = list(handlers)
    prev = [None]

    def step():
        top = QApplication.activeModalWidget()
        if top is not None and todo and type(top).__name__ == todo[0][0] and top is not prev[0]:
            prev[0] = top
            todo.pop(0)[1](top)
        if todo:
            QTimer.singleShot(40, step)

    QTimer.singleShot(40, step)


def test_two_own_compositions_cannot_take_the_same_new_file(  # noqa: F811
        qtbot, window, case_dir, monkeypatch, errors, library):  # noqa: F811
    """Segunda pasada: en «Asignar materiales…», dos «Composición propia…» con el mismo .mat nuevo se aceptaban (el
    segundo diálogo solo miraba los ficheros del .in) y al asignar uno pisaba al otro: dos MFNAME con nuevo.mat y una
    composición perdida. Ahora el segundo dice que ese fichero ya es de otro material."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialogButtonBox

    from argus.penhan.materials import set_material

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.materials._mlist = MLIST
    w.input.edit("otro", lambda d: set_material(d, 2, fname="plomo.mat", description=""))
    w.materials.refresh()
    seen = []

    def compose(k):
        return lambda top: QTimer.singleShot(0, lambda: top._compose(top.rows[k]))

    def own(formula, rho, fname, check=False):
        def fill(top):
            top.rb_own.setChecked(True)
            top.formula.setText(formula)
            top.density.setText(rho)
            top.file.setText(fname)
            if check:
                seen.append((top.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(), top.status.text()))
                top.file.setText(fname.replace(".mat", "2.mat"))
            top.accept()
        return fill

    def finish(top):
        seen.append(sorted((n, c.fname) for n, c in top.composed.items()))
        top.accept()

    _answer_in_order([("AssignMaterialsDialog", compose(0)), ("MaterialDialog", own("H2O", "1", "nuevo.mat")),
                      ("AssignMaterialsDialog", compose(1)),
                      ("MaterialDialog", own("Pb", "11.35", "nuevo.mat", check=True)),
                      ("AssignMaterialsDialog", finish)])
    assert w.assign_materials()
    assert seen[0][0] is False and "nuevo.mat ya es el fichero de otro material" in seen[0][1]
    assert seen[1] == [(1, "nuevo.mat"), (2, "nuevo2.mat")]
    mf = w.input.doc.find("MFNAME")
    assert [ln.text.strip() for ln in mf] == ["nuevo.mat", "nuevo2.mat"]
    assert material_recipe(w.input.doc, 1).composition.elements == [(1, 2.0), (8, 1.0)]
    assert material_recipe(w.input.doc, 2).composition.elements == [(82, 1.0)]
    assert not errors


def test_a_composition_that_says_nothing_unassigns_the_row(qtbot, tmp_path, errors, library):
    """Segunda pasada: si «Composición propia…» acaba con algo que ARGUS no identifica, la fila decía «no se asigna»
    pero se asignaba lo de antes (el aluminio elegido en esa misma ventana la vez anterior)."""
    from argus.penhan.materials import materials_table
    from argus.ui.add_material_dialog import MaterialChoice
    from argus.ui.assign_materials_dialog import AssignMaterialsDialog

    text = IN.replace("[Material file, up to 20 chars]", "").replace("water.mat", "rara.mat")
    doc = parse_in_text(text, str(tmp_path / "caso.in"))
    rows = [r for r in materials_table(doc, None, tmp_path) if r.declared]
    answers = iter([MaterialChoice("rara.mat", "Aluminio (PENELOPE 13)", "#888888", None),
                    MaterialChoice("rara.mat", "cosa rara", "#888888", None)])
    dlg = AssignMaterialsDialog(None, rows, MLIST, library, compose=lambda n, cur, pending: next(answers))
    qtbot.addWidget(dlg)
    n = rows[0].number

    def mine():
        return [a.description for a in dlg.assignments() if a.number == n]

    dlg._compose(rows[0])
    assert mine() == ["Aluminio (PENELOPE 13)"]
    dlg._compose(rows[0])
    assert mine() == [] and dlg.pickers[n].text() == ""
    assert "no se asigna" in dlg.hints[n].text()
    assert not errors


def test_a_label_of_ones_own_keeps_its_number_after_trying_own_composition(qtbot, tmp_path, errors, library):
    """Segunda pasada: «Agua del maniquí (PENELOPE 278)» → «Composición propia» → la lista dejaba «Agua del maniquí»,
    sin número: el material se quedaba sin identificar."""
    dlg = _dialog(qtbot, tmp_path, description="Agua del maniquí (PENELOPE 278)", fname="agua.mat", edit=True)
    dlg.rb_own.setChecked(True)
    assert dlg.description() == "Agua del maniquí"
    dlg.rb_list.setChecked(True)
    assert dlg.description() == "Agua del maniquí (PENELOPE 278)" and dlg.list_item()[0] == 278
    assert not errors


def test_an_unknown_name_does_not_take_the_mat_of_another_material(qtbot, tmp_path, errors, library):
    """Segunda pasada: con un nombre que ARGUS no identifica («agua» e Intro, que deja lo escrito) se proponía el
    agua.mat que ya estaba, que era el plomo (n.º 82), con «✔ está en la carpeta»: el material nuevo habría sido plomo.
    Ahora se propone otro y, si se escribe ese, se dice qué es."""
    import html as _html

    from test_material_library import mat_header

    (tmp_path / "agua.mat").write_text(mat_header("LEAD", 82, 11.35, {82: 1.0}), encoding="latin-1")
    dlg = _dialog(qtbot, tmp_path)
    dlg.taken = set()
    dlg.name.setText("agua")
    assert dlg.list_item() is None and dlg.fname() == "agua2.mat"
    dlg.file.setText("agua.mat")
    status = _html.unescape(dlg.status.text())
    assert "es el n.º 82 de la lista de PENELOPE: LEAD" in status and "ARGUS aún no sabe si es este material" in status
    assert "✔" not in status and dlg.btn_newfile.isVisibleTo(dlg)
    assert not errors


def test_the_assign_texts_in_plural_and_singular(qtbot, window, case_dir, monkeypatch, errors, library):  # noqa: F811
    """Segunda pasada: «2 materials se quedan sin asignar» y «Asignados 1 material: ya se pueden generar sus .mat»."""
    from argus.penhan.materials import set_material

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.materials._mlist = MLIST
    w.input.edit("otro", lambda d: set_material(d, 2, fname="plomo.mat", description=""))
    w.materials.refresh()
    texts = []

    def pick(dlg):
        for p in dlg.pickers.values():
            p.setText("")
        texts.append(dlg.status.text())
        dlg.pickers[2].setText("Plomo (PENELOPE 82)")
        return True

    fake_exec(w, monkeypatch, pick)
    assert w.assign_materials()
    assert texts == ["Se asignarán 0 de 2. 2 materiales se quedan sin asignar."]
    assert w.statusBar().currentMessage().startswith("Asignado 1 material: ya se puede generar su .mat")
    assert not errors
