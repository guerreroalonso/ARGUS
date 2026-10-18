"""Diálogo «Preparar la carpeta de simulación»: rutas, ajustes guardados, cancelar y textos."""

import shutil
import time

import pytest

pytest.importorskip("PySide6")

from argus.penhan import simfolder  # noqa: E402
from argus.penhan.inputfile import parse_in_file  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"


@pytest.fixture
def dlg_case(qtbot, tmp_path):
    from argus.ui.sim_folder_dialog import SimFolderDialog

    src = tmp_path / "caso"
    src.mkdir()
    for f in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / f, src / f)
    (src / "water.mat").write_text(" PENELOPE (v. 2018)  Material data file\n")
    app_settings().clear()

    def make(in_name="maniqui.in"):
        in_path = src / in_name
        if not in_path.exists():
            shutil.copy2(src / "maniqui.in", in_path)
        dlg = SimFolderDialog(None, in_path, parse_in_file(in_path), app_settings())
        qtbot.addWidget(dlg)
        return dlg

    yield src, make
    app_settings().clear()


def test_relative_paths_are_relative_to_the_in_folder(dlg_case):
    src, make = dlg_case
    dlg = make()
    dlg.dest.setText("sim_rel")
    dlg.refresh()
    assert dlg.plan.dest == src / "sim_rel"


def test_a_stale_database_setting_does_not_hide_the_real_one(dlg_case, tmp_path):
    src, make = dlg_case
    db = tmp_path / "database"
    db.mkdir()
    (db / "material-list.txt").write_text("  1  Hydrogen\n")
    shutil.move(str(src), str(db / "caso"))  # el .in dentro de la base de datos: se encuentra solo
    app_settings().setValue("sim/database", str(tmp_path / "ya-no-existe"))
    from argus.ui.sim_folder_dialog import SimFolderDialog

    in_path = db / "caso" / "maniqui.in"
    dlg = SimFolderDialog(None, in_path, parse_in_file(in_path), app_settings())
    try:
        assert dlg.database.text() == str(db)
    finally:
        dlg.deleteLater()


def test_prepare_uses_what_is_typed_right_now(dlg_case, qtbot):
    src, make = dlg_case
    dlg = make()
    dlg.refresh()
    dlg.dest.setText(str(src / "otra"))  # el temporizador (250 ms) aún no ha saltado
    dlg.prepare()
    qtbot.waitUntil(lambda: not dlg.busy(), timeout=20000)
    assert (src / "otra" / "maniqui.in").is_file()
    assert not (src / "maniqui_sim").exists()
    # Se puede vaciar el ejecutable guardado.
    assert app_settings().value("sim/penhmain", "") == ""


def test_cancel_while_copying_stops_cleans_up_and_then_closes(dlg_case, qtbot, monkeypatch):
    src, make = dlg_case

    def slow_copy(source, target, *, executable=False, cancel=None):
        while not cancel():  # una copia larga que solo termina si se cancela
            time.sleep(0.01)
        raise simfolder._Cancelled

    monkeypatch.setattr(simfolder, "_copy_new", slow_copy)
    dlg = make()
    dlg.show()
    dlg.prepare()
    qtbot.waitUntil(dlg.busy)
    dlg.reject()  # Cancelar, Esc o cerrar la ventana
    assert dlg.isVisible() and "Cancelando" in dlg.status.text()
    qtbot.waitUntil(lambda: not dlg.busy(), timeout=10000)
    assert not dlg.isVisible() and dlg.plan.cancelled and dlg.copied == []


def test_texts_are_escaped(dlg_case):
    from PySide6.QtCore import Qt

    src, make = dlg_case
    dlg = make("a&b.in")
    labels = [w.text() for w in dlg.findChildren(type(dlg.status))]
    assert any("a&amp;b.in" in t for t in labels)
    assert dlg.status.textFormat() == Qt.TextFormat.PlainText  # un error con '<' se ve entero
