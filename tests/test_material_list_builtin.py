"""El desplegable del material sin la carpeta de penhan (0.17.1).

Rafael (18-10-2026): «el desplegable en la versión de Windows no funciona; es verdad que no le había asignado la
carpeta de penhan». La lista de los 280 materiales salía de ``database/material-list.txt``: la de «Programas de
penhan» o la que se encontrara subiendo desde la carpeta del caso o desde la de ARGUS. Desde el código, la carpeta de
ARGUS está junto a su penhan (``SOBP_Investigacion/ProgramasBase/penhan``) y se encontraba siempre; el ejecutable está
en la carpeta de programas y, sin un caso cerca de penhan, la lista quedaba vacía: el desplegable no enseñaba nada.
Ahora, si no se encuentra la del usuario, se usa la que trae ARGUS (los mismos números y nombres)."""

import shutil

import pytest

from argus.penhan import materials as mt
from argus.penhan import matgen
from argus.penhan.material_names import NAMES, penelope_entries
from conftest import CORPUS


def test_the_builtin_list_has_the_280_materials_of_penelope():
    ml = mt.builtin_material_list()
    assert ml.builtin and len(ml) == 280
    assert [n for n, _ in ml] == list(range(1, 281))
    assert dict(ml)[278] == NAMES[278][0] == "Water, liquid"
    texts = {e.number: e.text for e in penelope_entries(ml)}
    assert texts[278] == "Agua líquida (PENELOPE 278)" and texts[82] == "Plomo (PENELOPE 82)"
    assert mt.match_material("Agua líquida (PENELOPE 278)", ml) == (278, "Water, liquid")


def test_the_users_list_comes_first(tmp_path):
    db = tmp_path / "penhan" / "database"
    db.mkdir(parents=True)
    (db / "material-list.txt").write_text("  1  Hydrogen\n278  Water, liquid\n", encoding="latin-1")
    ml = mt.material_list_or_builtin(db / "material-list.txt")
    assert not ml.builtin and list(ml) == [(1, "Hydrogen"), (278, "Water, liquid")]
    assert mt.material_list_or_builtin(None).builtin
    assert mt.material_list_or_builtin(tmp_path / "no-existe.txt").builtin  # (no se lee: la de ARGUS)
    assert matgen.material_list(db, tmp_path) == ml
    assert matgen.material_list(None, tmp_path / "penhan").builtin is False  # la encuentra subiendo desde el caso


def test_without_penhan_generating_still_knows_the_number(tmp_path, monkeypatch):
    """Sin la lista del usuario, el plan de «Generar .mat y ENDF…» sabe igual qué material es (por la lista de
    ARGUS); generarlo pide penhmat (la carpeta de penhan), como antes."""
    monkeypatch.setattr(matgen, "find_material_list", lambda *a: None)
    ml = matgen.material_list(None, tmp_path)
    assert ml.builtin and len(ml) == 280


@pytest.mark.gui
def test_the_dropdown_works_without_the_penhan_folder(qtbot, tmp_path, monkeypatch):
    """Como en el ejecutable sin «Programas de penhan»: no se encuentra material-list.txt y el desplegable del
    material (el de la tabla del panel Materiales y el del diálogo) ofrece igual los 280."""
    pytest.importorskip("PySide6")
    from argus.ui import materials_panel
    from argus.ui.main_window import MainWindow
    from argus.ui.material_picker import MaterialPicker
    from argus.ui.settings import app_settings

    monkeypatch.setenv("ARGUS_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setattr(materials_panel, "find_material_list", lambda *a: None)
    case = tmp_path / "caso"
    case.mkdir()
    for name in ("caja.in", "caja.geo"):
        shutil.copy2(CORPUS / "resultados" / "caja" / name, case / name)
    app_settings().clear()
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    try:
        w.open_file(case / "caja.in")
        panel = w.materials
        assert getattr(panel._mlist, "builtin", False) and len(panel._mlist) == 280
        texts = [e.text for e in panel.delegate.entries]
        assert "Agua líquida (PENELOPE 278)" in texts and len([e for e in panel.delegate.entries if e.number]) == 280
        picker = MaterialPicker()
        qtbot.addWidget(picker)
        picker.set_entries(panel.delegate.entries)
        picker.show_all()
        assert len(picker.visible_entries()) >= 280
        picker.edit.setText("agua")
        picker._on_edited("agua")
        assert any(e.number == 278 for e in picker.visible_entries())
    finally:
        w.close()
        app_settings().clear()

