"""Interfaz de los programas de penhan (ADR-013): Opciones → «Programas de penhan…», panel Materiales → «Generar .mat
y ENDF…» y «Preparar la carpeta de simulación», con un penhmat de mentira (un guion de Python)."""

import os
import shutil
import sys
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from argus.penhan import programs as pg  # noqa: E402
from argus.ui.settings import app_settings, penhan_setup, save_penhan_setup  # noqa: E402
from conftest import CORPUS  # noqa: E402
from test_penhmat import FAKE, _elf, _penhan_tree, _write  # noqa: E402

pytestmark = [pytest.mark.gui, pytest.mark.skipif(os.name == "nt", reason="penhmat de mentira con #!")]
SB = QMessageBox.StandardButton
DISC = CORPUS / "penhan/disc"


def _wait(cond, timeout=30.0):
    t0 = time.monotonic()
    while not cond():
        QApplication.processEvents()
        time.sleep(0.02)
        if time.monotonic() - t0 > timeout:
            raise TimeoutError
    QApplication.processEvents()


@pytest.fixture
def penhan(tmp_path):
    """Una carpeta de penhan con su database, un penhmat de mentira y los ENDF del agua."""
    db = _penhan_tree(tmp_path)
    for f in ("pO16_ICRU63.endf", "pO17_TENDL.endf"):
        (db / "ENDF_data" / f).write_text(f"datos ENDF {f}\n")
    exe = _write(db / "penhmat.py", FAKE.format(python=sys.executable).encode())
    return db, exe


@pytest.fixture
def settings():
    s = app_settings()
    s.clear()
    yield s
    s.clear()


@pytest.fixture
def disc(tmp_path):
    d = tmp_path / "caso"
    d.mkdir()
    for f in ("disc.in", "disc.geo"):
        shutil.copy2(DISC / f, d / f)
        os.chmod(d / f, 0o644)
    p = d / "disc.in"
    text = p.read_text(encoding="latin-1").replace(
        "MFNAME H2O.mat                        [Material file, up to 20 chars]",
        "MFNAME H2O.mat                        [Water, liquid]")
    p.write_text(text, encoding="latin-1")
    return p


def test_the_setup_dialog_finds_the_programs_and_saves_them(qtbot, tmp_path, settings):
    from argus.ui.penhan_dialogs import PenhanSetupDialog

    db = _penhan_tree(tmp_path)
    _write(db / "penhmat.x", _elf())
    _write(db.parent / "programs" / "penhmain.x", _elf())
    dlg = PenhanSetupDialog(None, settings)
    qtbot.addWidget(dlg)
    dlg.root.setText(str(db.parent))
    dlg._update()
    assert dlg.database.text() == str(db) and dlg.penhmat.text() == str(db / "penhmat.x")
    assert dlg.penhmain.text() == str(db.parent / "programs" / "penhmain.x")
    assert "pdfiles, pdhan y ENDF_data" in dlg.db_state.text()
    assert "ejecutable de Linux (x86-64)" in dlg.penhmat_state.text()
    assert "./penhmain.x &lt; caso.in" in dlg.penhmain_state.text() or "./penhmain.x < caso.in" in \
        dlg.penhmain_state.text()
    dlg.rb_off.setChecked(True)
    assert not dlg.btn_test.isEnabled()
    dlg.accept()
    setup = penhan_setup(settings)
    assert (setup.database, setup.penhmat, setup.penhmain, setup.penhmat_off) == (
        db, db / "penhmat.x", db.parent / "programs" / "penhmain.x", True)
    assert setup.configured and "usted mismo" in setup.penhmat_problem()


def test_probar_penhmat(qtbot, penhan, settings):
    from argus.ui.penhan_dialogs import PenhanSetupDialog

    db, exe = penhan
    save_penhan_setup(pg.PenhanSetup(db, exe, None), settings)
    dlg = PenhanSetupDialog(None, settings)
    qtbot.addWidget(dlg)
    assert "guion de órdenes" in dlg.penhmat_state.text()
    dlg.test_penhmat()
    _wait(lambda: not dlg._tasks)
    assert "penhmat funciona" in dlg.test_state.text() and "2 ficheros ENDF" in dlg.test_state.text()
    assert not [p for p in db.iterdir() if p.name.startswith("argus-")]


def test_generate_in_the_dialog(qtbot, penhan, settings, disc):
    from argus.penhan.inputfile import parse_in_file
    from argus.ui.penhan_dialogs import CompleteMaterialsDialog

    db, exe = penhan
    save_penhan_setup(pg.PenhanSetup(db, exe, None), settings)
    dlg = CompleteMaterialsDialog(None, parse_in_file(disc), None, disc.parent, settings, in_name=disc.name)
    qtbot.addWidget(dlg)
    rows = [[dlg.table.item(i, k).text() for k in range(3)] for i in range(dlg.table.rowCount())]
    assert rows == [["H2O.mat", "n.º 278 de la lista de PENELOPE (Water, liquid)", "se generará con penhmat"]]
    assert dlg.go.isEnabled() and "Se generará 1 .mat y se copiarán los ficheros ENDF" in dlg.status.text()
    dlg.run()
    _wait(lambda: not dlg.busy())
    names = sorted(p.name for p in disc.parent.iterdir())
    assert names == ["H2O.mat", "disc.geo", "disc.in", "pO16_ICRU63.endf", "pO17_TENDL.endf"]
    assert "Generado con penhmat: H2O.mat" in dlg.status.text() and not dlg.go.isEnabled()


def test_without_penhmat_only_the_endf_are_copied(qtbot, penhan, settings, disc):
    from argus.penhan.inputfile import parse_in_file
    from argus.ui.penhan_dialogs import CompleteMaterialsDialog

    db, exe = penhan
    save_penhan_setup(pg.PenhanSetup(db, exe, None, penhmat_off=True), settings)
    (disc.parent / "H2O.mat").write_text(" PENELOPE (v. 2024)  Material data file ...\n Material: WATER (278)\n"
                                         " Mass density = 1.0 g/cm**3\n *** ENDF filenames and abundances of "
                                         "isotopes,  NISOT =   1\n pO16_ICRU63.endf 8016 0.99\n   0 = NALPHA\n")
    dlg = CompleteMaterialsDialog(None, parse_in_file(disc), None, disc.parent, settings)
    qtbot.addWidget(dlg)
    assert dlg.table.item(0, 0).text() == "pO16_ICRU63.endf" and "se copiará" in dlg.table.item(0, 2).text()
    dlg.run()
    _wait(lambda: not dlg.busy())
    assert (disc.parent / "pO16_ICRU63.endf").is_file()


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    msgs = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: SB.No)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2])) or SB.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._msgs = msgs
    qtbot.addWidget(w)
    w.resize(1300, 850)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def test_the_materials_panel_generates_what_is_missing(qtbot, window, penhan, disc, monkeypatch):
    from argus.ui.penhan_dialogs import CompleteMaterialsDialog, PenhanSetupDialog

    db, exe = penhan
    w = window
    w.open_file(disc)
    assert w.materials.btn_gen.isVisible() and w.materials.btn_gen.isEnabled()
    shown = []

    def run(dlg):
        shown.append(type(dlg).__name__)
        if isinstance(dlg, PenhanSetupDialog):  # la primera vez pregunta dónde está penhmat
            dlg.root.setText(str(db.parent))
            dlg.penhmat.setText(str(exe))
            dlg.accept()
            return True
        assert isinstance(dlg, CompleteMaterialsDialog)
        dlg.run()
        _wait(lambda: not dlg.busy())
        return True

    monkeypatch.setattr(w, "_exec_dialog", run)
    w.complete_materials()
    assert shown == ["PenhanSetupDialog", "CompleteMaterialsDialog"]
    assert (disc.parent / "H2O.mat").is_file() and (disc.parent / "pO16_ICRU63.endf").is_file()
    assert not w.materials.btn_gen.isEnabled()  # ya no falta nada
    assert "Generado con penhmat: H2O.mat" in w.statusBar().currentMessage()
    txt = (disc.parent / "materiales.txt").read_text(encoding="utf-8-sig")
    assert "FALTA" not in txt
    shown.clear()
    w.complete_materials()  # la segunda vez ya no pregunta
    assert shown == ["CompleteMaterialsDialog"]


def test_the_simulation_folder_offers_to_generate_and_says_how_to_run(qtbot, penhan, settings, disc, monkeypatch):
    from argus.penhan.inputfile import parse_in_file
    from argus.ui.sim_folder_dialog import SimFolderDialog

    db, exe = penhan
    penhmain = _write(db.parent / "programs" / "penhmain.x", _elf())
    save_penhan_setup(pg.PenhanSetup(db, exe, penhmain), settings)
    dlg = SimFolderDialog(None, disc, parse_in_file(disc), settings)
    qtbot.addWidget(dlg)
    assert dlg.gen.isVisible() or not dlg.gen.isHidden()
    assert "./penhmain.x < disc.in" in dlg.hint.text()
    ran = []

    def fake_exec(self):
        ran.append(True)
        self.run()
        _wait(lambda: not self.busy())
        return 1

    monkeypatch.setattr("argus.ui.penhan_dialogs.CompleteMaterialsDialog.exec", fake_exec)
    dlg.generate_materials()
    assert ran and (disc.parent / "H2O.mat").is_file()
    assert not (disc.parent / "pO16_ICRU63.endf").exists()  # los ENDF van a la carpeta de simulación
    assert dlg.gen.isHidden() and {"H2O.mat", "pO16_ICRU63.endf"} <= {i.name for i in dlg.plan.to_copy}
