"""Revisión de 0.16 (0.16.2), en la interfaz: la carpeta de los ENDF en «Programas de penhan», las tandas de «Lluvias»
con el penhmain que guarda otras, los ENDF que faltan al ejecutar, el motivo con un espacio de fases y los plurales."""

import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from argus.penhan.inputfile import parse_in_text  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture
def settings():
    from argus.ui.settings import app_settings

    s = app_settings()
    s.clear()
    yield s
    s.clear()


def _database(root: Path) -> Path:
    db = root / "penhan" / "database"
    for sub in ("pdfiles", "pdhan", "ENDF_data"):
        (db / sub).mkdir(parents=True)
    (db / "ENDF_data" / "pO16_ICRU63.endf").write_text("ENDF\n")
    return db


def _setup_dialog(qtbot, settings):
    from argus.ui.penhan_dialogs import PenhanSetupDialog

    dlg = PenhanSetupDialog(None, settings)
    qtbot.addWidget(dlg)
    return dlg


def test_an_empty_endf_field_says_the_database_ones_are_used(qtbot, tmp_path, settings):
    """Revisión de 0.16: con la base de datos elegida y «Ficheros ENDF» vacío, decía que los ENDF que falten no se
    pueden copiar; se copian de database/ENDF_data."""
    from argus.penhan import matgen as mg

    db = _database(tmp_path)
    dlg = _setup_dialog(qtbot, settings)
    dlg.database.setText(str(db))
    dlg.endf.setText("")
    dlg._update()
    setup = dlg.setup()
    assert setup.endf is None and mg.endf_sources(setup.database, setup.endf) == [db / "ENDF_data"]
    assert "no se pueden copiar" not in dlg.endf_state.text() and "database/ENDF_data" in dlg.endf_state.text()


def test_the_endf_folder_follows_the_database(qtbot, tmp_path, settings):
    """Revisión de 0.16: la carpeta de los ENDF se quedaba en la base de datos vieja (guardada como elegida desde
    «Generar .mat y ENDF…», o al elegir una base de datos aún sin ENDF_data) y los ENDF se seguían copiando de ella.
    Ahora la ENDF_data de la base de datos se guarda como «la de la base de datos» y sigue a la que se elija."""
    from argus.penhan.programs import PenhanSetup
    from argus.ui.settings import penhan_setup, save_penhan_setup

    old, new = _database(tmp_path / "vieja"), _database(tmp_path / "nueva")
    save_penhan_setup(PenhanSetup(old, endf=old / "ENDF_data"), settings)  # lo que guardaba «Carpeta de los ENDF…»
    assert penhan_setup(settings).endf is None
    dlg = _setup_dialog(qtbot, settings)
    dlg.database.setText(str(new))
    dlg.accept()
    setup = penhan_setup(settings)
    assert setup.database == new and setup.endf is None and setup.endf_folder() == new / "ENDF_data"
    # Una base de datos aún sin ENDF_data: el campo se vacía (no se queda en la vieja) y la sigue cuando la tenga.
    bare = tmp_path / "otra" / "penhan" / "database"
    for sub in ("pdfiles", "pdhan"):
        (bare / sub).mkdir(parents=True)
    dlg = _setup_dialog(qtbot, settings)
    dlg.database.setText(str(bare))
    assert dlg.endf.text() == ""
    dlg.accept()
    (bare / "ENDF_data").mkdir()
    assert penhan_setup(settings).endf is None and penhan_setup(settings).endf_folder() == bare / "ENDF_data"
    # Una carpeta propia de los ENDF (fuera de la base de datos) no se toca al cambiar de base de datos.
    mine = tmp_path / "mis_endf"
    mine.mkdir()
    save_penhan_setup(PenhanSetup(old, endf=mine), settings)
    dlg = _setup_dialog(qtbot, settings)
    dlg.database.setText(str(new))
    dlg.accept()
    assert penhan_setup(settings).endf == mine


def test_changing_the_penhan_folder_changes_what_was_inside_it(qtbot, tmp_path, settings):
    """Revisión de 0.16 (de antes, pero afecta a la carpeta de los ENDF): con una carpeta de penhan ya guardada,
    cambiar «Carpeta de penhan» no cambiaba la base de datos ni lo demás, que seguían en la vieja."""
    from argus.penhan.programs import PenhanSetup
    from argus.ui.settings import penhan_setup, save_penhan_setup

    old, new = _database(tmp_path / "vieja"), _database(tmp_path / "nueva")
    save_penhan_setup(PenhanSetup(old), settings)
    dlg = _setup_dialog(qtbot, settings)
    dlg.root.setText(str(new.parent))
    assert dlg.database.text() == str(new) and dlg.endf.text() == str(new / "ENDF_data")
    dlg.accept()
    setup = penhan_setup(settings)
    assert setup.database == new and setup.endf is None


def test_the_psf_reason_in_the_generate_dialog(qtbot, tmp_path, settings):
    """Revisión de 0.16: con un espacio de fases, «Generar .mat y ENDF…» decía que no se leen los ENDF porque se
    simulan electrones, fotones o positrones; es porque penhmain no inicializa los datos de hadrones."""
    from argus.ui.penhan_dialogs import CompleteMaterialsDialog

    case = tmp_path / "caso"
    case.mkdir()
    doc = parse_in_text("TITLE  psf de protones\nSKPAR  4\nIPSFN  protones.psf\nEPMAX  2e8 2e8\n"
                        "MFNAME agua.mat                                 [Agua líquida (PENELOPE 278)]\n"
                        "MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN caso.geo\n", str(case / "caso.in"))
    dlg = CompleteMaterialsDialog(None, doc, None, case, settings)
    qtbot.addWidget(dlg)
    assert dlg.plan.hadrons is False
    assert "espacio de fases" in dlg.status.text() and "electrones, fotones o positrones" not in dlg.status.text()


@pytest.mark.skipif(os.name == "nt", reason="el penhmain de mentira es un guion de sh")
def test_showers_are_counted_with_what_this_penhmain_keeps(qtbot, tmp_path, settings):
    """Revisión de 0.16: con un penhmain que guarda 99 lluvias por ejecución (lo aprendido con la prueba de LTRACK
    99), la cascada hace 11 tandas para 1000 lluvias, pero «Ejecutar» decía «20 tandas de 50»."""
    from argus.penhan import cascade as cs
    from argus.ui.cascade_ui import remember_cap
    from argus.ui.settings import PENHMAIN_KEY
    from test_run import _case, _fake_penhmain
    from test_run_ui import _dialog as run_dialog
    from test_run_ui import _headers

    exe = _fake_penhmain(tmp_path / "programas")
    settings.setValue(PENHMAIN_KEY, str(exe))
    settings.setValue("cascada/al_ejecutar", "false")
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    dlg, _ctrl = run_dialog(qtbot, in_path, settings)
    dlg.showers.setValue(1000)
    assert dlg.showers_text.text().endswith("20 tandas de 50")  # sin saberlo, las de penhmain.f
    remember_cap(exe, 99, settings)
    dlg.refresh()
    assert len(cs.runs_for(1000, 99)) == 11
    assert dlg.showers_text.text().endswith("11 tandas de 99")
    assert "tandas de 99" in dlg.showers.toolTip() and "como mucho 100" in dlg.showers_note.text()


def test_run_shows_how_to_find_the_missing_endf(qtbot, tmp_path, settings):
    """Revisión de 0.16: con protones, los .mat en la carpeta y los ENDF en ningún sitio que ARGUS conozca,
    «Ejecutar» no se podía usar y no decía cómo arreglarlo. Ahora lo dice y tiene el botón del diálogo de los ENDF."""
    from test_review016 import _proton_case
    from test_run_ui import _dialog as run_dialog

    in_path, _doc = _proton_case(tmp_path / "caso")
    exe = in_path.parent / "penhmain.x"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    settings.setValue("cascada/al_ejecutar", "false")
    dlg, _ctrl = run_dialog(qtbot, in_path, settings)
    assert dlg.gen.isVisibleTo(dlg) and "ENDF" in dlg.gen.text()
    assert not dlg.go.isEnabled()
    assert "Programas de penhan" in " ".join(dlg.problems())


def test_one_of_each_in_the_simulation_folder_dialog(qtbot, tmp_path, settings):
    """Revisión de 0.16 (de antes): «Faltan 1: agua.mat», «1 no se copiarán»…"""
    from argus.ui.sim_folder_dialog import SimFolderDialog

    case = tmp_path / "caso"
    case.mkdir()
    text = ("TITLE  x\nSKPAR  1\nSENERG 1e6\nSPOSIT 0 0 -10\nSCONE  0 0 0\nMFNAME agua.mat\n"
            "MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN caso.geo\nNSIMSH 10\n")
    (case / "caso.in").write_text(text)
    shutil.copy(Path(__file__).parent / "corpus" / "resultados" / "caja" / "caja.geo", case / "caso.geo")
    dest = tmp_path / "sim"
    dest.mkdir()
    (dest / "caso.geo").write_text("otra geometría\n")
    doc = parse_in_text(text, str(case / "caso.in"))
    dlg = SimFolderDialog(None, case / "caso.in", doc, settings)
    qtbot.addWidget(dlg)
    dlg.dest.setText(str(dest))
    dlg.refresh()
    t = dlg.status.text()
    assert "Falta 1: agua.mat" in t and "1 fichero no se copiará" in t
    assert "añadirlo después" in t  # (segunda pasada: decía «añadirlos»)


@pytest.mark.skipif(os.name == "nt", reason="el penhmain de mentira es un guion de sh")
def test_the_showers_with_the_penhmain_that_will_replace_the_folder_one(qtbot, tmp_path, settings):
    """Segunda pasada: con otro penhmain en la carpeta y «Sustituirlo por el elegido», las tandas de «Lluvias» se
    contaban con el de la carpeta (el que se iba a sustituir) y no cambiaban al elegir."""
    from argus.ui.cascade_ui import remember_cap
    from argus.ui.settings import PENHMAIN_KEY
    from test_run import _case, _fake_penhmain
    from test_run_ui import _dialog as run_dialog
    from test_run_ui import _headers

    chosen = _fake_penhmain(tmp_path / "programas")  # el de Opciones: guarda 99 por ejecución
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    folder_exe = in_path.parent / "penhmain.x"  # otro, ya en la carpeta del caso: guarda 50
    folder_exe.write_text(chosen.read_text() + "# otra compilación\n")
    folder_exe.chmod(0o755)
    remember_cap(chosen, 99, settings)
    remember_cap(folder_exe, 50, settings)
    settings.setValue(PENHMAIN_KEY, str(chosen))
    settings.setValue("cascada/al_ejecutar", "false")
    dlg, _ctrl = run_dialog(qtbot, in_path, settings)
    dlg.showers.setValue(1000)
    assert dlg.plan.exe.status == "conflict" and dlg.use_folder.isChecked()
    assert dlg.showers_text.text().endswith("20 tandas de 50")
    dlg.replace.setChecked(True)
    assert dlg.showers_text.text().endswith("11 tandas de 99")
    dlg.refresh()
    assert dlg.showers_text.text().endswith("11 tandas de 99")
    dlg.use_folder.setChecked(True)
    assert dlg.showers_text.text().endswith("20 tandas de 50")
