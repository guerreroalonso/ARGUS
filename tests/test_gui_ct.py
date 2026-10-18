"""Importar un TAC y preparar la carpeta de simulación desde la ventana."""

import os
import shutil

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pydicom")

from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402
from dicom_synth import phantom_hu, write_series  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"


@pytest.fixture
def case_dir(tmp_path):
    d = tmp_path / "caso"
    d.mkdir()
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, d / name)
    return d


@pytest.fixture(scope="module")
def dicom_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("dicom")
    write_series(d, phantom_hu, rows=40, cols=36, nslices=20, spacing=(1.0, 1.25), dz=2.0, origin=(-20, -18, 10))
    return d


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def _drive(qtbot, monkeypatch, dicom_dir, setup=None):
    """El diálogo de importar se maneja como lo haría el usuario (sin mostrarlo de forma modal)."""
    from argus.ui.ct_import_dialog import CtImportDialog
    from argus.ui.main_window import MainWindow

    seen = {}
    original = MainWindow._exec_dialog

    def fake(self, dlg):
        if not isinstance(dlg, CtImportDialog):
            return original(self, dlg)
        seen["dlg"] = dlg
        dlg.folder_edit.setText(str(dicom_dir))
        dlg.scan()
        qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
        dlg.voxel_choice.setCurrentText("2,5 mm")
        if setup is not None:
            setup(dlg)
        dlg._update_all()
        dlg._import()
        qtbot.waitUntil(lambda: dlg.result is not None or (not dlg.busy() and "color:#d33" in dlg.status.text()),
                        timeout=60000)
        return dlg.result is not None

    monkeypatch.setattr(MainWindow, "_exec_dialog", fake)
    return seen


def test_import_a_ct_into_the_open_case(qtbot, window, case_dir, dicom_dir, monkeypatch):
    from argus.ui.view3d import HIDDEN

    w = window
    w.open_file(case_dir / "maniqui.in")
    qtbot.waitUntil(lambda: w.session is not None and w.input is not None)

    def setup(dlg):
        dlg.offset["z"].setValue(25.0)  # dentro del cubo de agua (de z = 0 a 500 cm)
        dlg.name_edit.setText("fantoma.ct")

    seen = _drive(qtbot, monkeypatch, dicom_dir, setup)
    w.import_ct()
    geo = w.session.geo
    assert geo.ct is not None and geo.ct_file == "fantoma.ct"
    assert "Cubo" in seen["dlg"].container_combo.currentText()
    from argus.pengeom.model import BodyElement

    cube = next(e for e in w.session.doc.elements if isinstance(e, BodyElement) and e.label.strip() == "1")
    assert any(r.label.strip() == "TAC" and r.keyword == "MODULE" for r in cube.items)
    mats = [ln.text.strip() for ln in w.input.doc.find("MFNAME")]
    assert mats == ["water.mat", "ct_aire.mat", "ct_pulmon.mat", "ct_adiposo.mat", "ct_blando.mat", "ct_hueso.mat"]
    assert w.materials.visibility.get(2) == HIDDEN and w.view3d.material_visibility.get(2) == HIDDEN
    assert w.act_fit_ct.isEnabled() and not w.act_import_ct.isEnabled()
    # Información del vóxel bajo el cursor.
    from argus.pengeom.ct import ct_frame

    fr = ct_frame(geo)
    p = fr.to_lab(np.array(fr.size) / 2)
    w._on_cursor({"point": p, "body": geo.ct_body, "mat": 9999})
    assert "vóxel" in w.body_label.text() and "g/cm³" in w.body_label.text()
    # Un solo paso de deshacer para el .geo y el .in.
    w.undo()
    assert w.session.geo.ct is None and [ln.text.strip() for ln in w.input.doc.find("MFNAME")] == ["water.mat"]
    w.redo()
    assert w.session.geo.ct is not None and len(w.input.doc.find("MFNAME")) == 6
    assert w.save_all()
    txt = (case_dir / "materiales.txt").read_text(encoding="utf-8-sig")
    assert "Materiales del TAC" in txt and "ct_pulmon.mat" in txt and "Opción 1" in txt
    assert (case_dir / "fantoma.ct").is_file() and (case_dir / "fantoma.ct.json").is_file()
    # «Guardar la geometría como…» en otra carpeta: el .ct va con ella (PENGEOM lo busca allí).
    from PySide6.QtWidgets import QFileDialog

    other = case_dir.parent / "otra"
    other.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(other / "copia.geo"), ""))
    assert w.save_geometry_as()
    assert (other / "fantoma.ct").read_bytes() == (case_dir / "fantoma.ct").read_bytes()
    assert (other / "fantoma.ct.json").is_file()
    w.select_body(1)
    w.edit_geometry("Cambiar el nombre", lambda d, g: None)  # la geometría se sigue compilando en la carpeta nueva
    assert w.session.geo.ok and w.session.geo.ct is not None
    # Si la copia falla (disco lleno), no queda un .ct a medias y se avisa con su propio mensaje.
    from PySide6.QtWidgets import QMessageBox

    from argus.core import files

    third = case_dir.parent / "tercera"
    third.mkdir()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2]) or QMessageBox.StandardButton.Ok)

    def disk_full(tmp, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(files, "publish_new", disk_full)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(third / "copia.geo"), ""))
    assert w.save_geometry_as()
    assert sorted(p.name for p in third.iterdir()) == ["copia.geo"]  # ni .ct a medias ni temporales
    assert any("No se han podido copiar" in m and "fantoma.ct" in m for m in warnings)


def test_a_ct_that_does_not_fit_is_not_imported(qtbot, window, case_dir, dicom_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    qtbot.waitUntil(lambda: w.session is not None)

    def setup(dlg):
        dlg._container_auto = False
        dlg.container_combo.setCurrentIndex(0)  # nivel superior: se solaparía con el cubo y la esfera

    seen = _drive(qtbot, monkeypatch, dicom_dir, setup)
    w.import_ct()
    assert w.session.geo.ct is None
    assert "se sale" in seen["dlg"].status.text()
    assert not (case_dir / "tac.ct").exists()  # no se ha escrito nada


def test_import_without_a_geometry_creates_one(qtbot, window, tmp_path, dicom_dir, monkeypatch):
    w = window
    geo_path = tmp_path / "nuevo" / "tac.geo"
    geo_path.parent.mkdir()

    def setup(dlg):
        dlg.geo_edit.setText(str(geo_path))

    _drive(qtbot, monkeypatch, dicom_dir, setup)
    w.import_ct()
    assert w.session is not None and w.session.path == geo_path and w.session.geo.ct is not None
    assert w.input is not None and w.input.path is None  # .in nuevo, sin guardar
    mf = w.input.doc.find("MFNAME")
    assert [ln.text.strip() for ln in mf] == ["ct_aire.mat", "ct_pulmon.mat", "ct_adiposo.mat", "ct_blando.mat",
                                             "ct_hueso.mat"]
    assert "Pulmón (TAC)" in mf[1].comment


def _fake_mat(path, isotopes):
    lines = [" PENELOPE (v. 2018)  Material data file", " *** ENDF filenames and abundances of isotopes,  NISOT = "
             f"{len(isotopes):3d}"] + [f" {f:<25} {iza:6d} 1.00000E+00" for f, iza in isotopes] + ["   0 = NALPHA"]
    path.write_text("\n".join(lines) + "\n")


def test_prepare_the_simulation_folder(qtbot, window, case_dir, tmp_path, monkeypatch):
    from argus.ui.sim_folder_dialog import SimFolderDialog

    w = window
    _fake_mat(case_dir / "water.mat", [("pO16_ICRU63.endf", 8016)])
    db = tmp_path / "database"
    (db / "ENDF_data").mkdir(parents=True)
    (db / "material-list.txt").write_text("  1  Hydrogen\n")
    (db / "ENDF_data" / "pO16_ICRU63.endf").write_text("endf\n")
    exe = tmp_path / "penhmain.x"
    exe.write_bytes(b"\x7fELF")
    os.chmod(exe, 0o755)
    app_settings().setValue("sim/database", str(db))
    app_settings().setValue("sim/penhmain", str(exe))
    w.open_file(case_dir / "maniqui.in")
    qtbot.waitUntil(lambda: w.input is not None)
    seen = {}

    def fake_exec(dlg):
        seen["dlg"] = dlg
        assert dlg.database.text() == str(db) and dlg.exe.text() == str(exe)
        dlg.prepare()
        qtbot.waitUntil(lambda: not dlg.busy(), timeout=30000)
        return 1

    monkeypatch.setattr(SimFolderDialog, "exec", fake_exec)
    w.prepare_simulation_folder()
    dest = case_dir / "maniqui_sim"
    assert sorted(p.name for p in dest.iterdir()) == ["cubo-esfera.geo", "maniqui.in", "pO16_ICRU63.endf",
                                                     "penhmain.x", "water.mat"]
    assert not seen["dlg"].open_btn.isHidden() and "Carpeta de simulación preparada" in w.statusBar().currentMessage()
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(seen["dlg"])  # el diálogo no se queda en memoria
    # Otra vez: la carpeta propuesta es nueva y la anterior no se toca.
    (dest / "water.mat").write_text("cambiado por el usuario\n")
    w.prepare_simulation_folder()
    assert (dest / "water.mat").read_text() == "cambiado por el usuario\n"
    assert (case_dir / "maniqui_sim2" / "water.mat").is_file()


def test_importing_a_ct_asks_before_dropping_an_edited_in(qtbot, window, case_dir, monkeypatch):
    """Sin geometría (falta o tiene errores), importar crea otra y cierra el .in: antes se pregunta."""
    from PySide6.QtWidgets import QMessageBox

    from argus.penhan import sections as sc
    from argus.ui.main_window import MainWindow

    w = window
    (case_dir / "cubo-esfera.geo").unlink()
    w.open_file(case_dir / "maniqui.in")
    qtbot.waitUntil(lambda: w.input is not None)
    w.input.edit("NSIMSH", lambda d: sc.set_value(d, "NSIMSH", [5e5]))
    asked, shown = [], []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(MainWindow, "_exec_dialog", lambda self, dlg: shown.append(dlg) or False)
    w.import_ct()
    assert asked and not shown  # se ha preguntado y, al cancelar, no se importa
    assert w.input is not None and sc.value(w.input.doc, "NSIMSH") == [5e5] and w.input.dirty


# -- revisión final ------------------------------------------------------------------------------------------------

def test_the_ct_goes_to_the_folder_of_the_in_when_geomfn_has_a_folder(qtbot, window, tmp_path, dicom_dir,
                                                                       monkeypatch):
    """«GEOMFN sub/cubo-esfera.geo»: penhmain se ejecuta en la carpeta del .in y PENGEOM busca allí el .ct. Antes se
    escribía junto a la geometría (sub/) y la geometría no lo encontraba."""
    import re

    d = tmp_path / "caso"
    (d / "sub").mkdir(parents=True)
    t = (CASE / "maniqui.in").read_text(encoding="latin-1")
    t = re.sub(r"^GEOMFN cubo-esfera\.geo    ", "GEOMFN sub/cubo-esfera.geo", t, flags=re.M)
    (d / "caso.in").write_text(t, encoding="latin-1")
    shutil.copy2(CASE / "cubo-esfera.geo", d / "sub" / "cubo-esfera.geo")
    w = window
    w.open_file(d / "caso.in")
    assert w.session.ok and w.session.work_dir == d

    def setup(dlg):
        dlg.offset["z"].setValue(25.0)
        assert dlg.target_folder() == d and dlg.name_edit.text() == "tac.ct"

    _drive(qtbot, monkeypatch, dicom_dir, setup)
    w.import_ct()
    assert w.session.geo.ct is not None and w.session.geo.ct_path == d / "tac.ct"
    assert (d / "tac.ct.json").is_file() and not (d / "sub" / "tac.ct").exists()


def _hetero_hu(P):
    """Tejido blando con dos pulmones heterogéneos (−900 a −200 HU) y una franja de hueso (200 a 1400 HU)."""
    x, y = P[..., 0], P[..., 1]
    hu = np.full(x.shape, 40.0)
    lung = (np.abs(x - 2) < 12) & (np.abs(y) < 14)
    hu[lung] = np.clip(-900.0 + 700.0 * (x[lung] + 10) / 24.0, -900, -200)
    bone = (y > 16) & (y < 22)
    hu[bone] = 200.0 + 1200.0 * np.clip((x[bone] + 18) / 36.0, 0, 1)
    hu[(x < -15) & (y < -20)] = -1000.0
    return hu


def test_the_pieces_of_a_split_tissue_share_a_colour_everywhere(qtbot, window, case_dir, tmp_path, monkeypatch):
    """Los trozos de un tejido partido por densidad llevan el color de su primer material en las vistas y en la
    tabla de materiales, también tras «Colores por defecto» y al volver a abrir el caso."""
    from argus.render.colors import material_color

    dicom = tmp_path / "dicom_h"
    write_series(dicom, _hetero_hu, rows=40, cols=36, nslices=20, spacing=(1.0, 1.25), dz=2.0,
                 origin=(-20, -18, 10))
    w = window
    w.open_file(case_dir / "maniqui.in")

    def setup(dlg):
        dlg.offset["z"].setValue(25.0)

    seen = _drive(qtbot, monkeypatch, dicom, setup)
    w.import_ct()
    r = seen["dlg"].result
    lung = [n for m, n in zip(r.built.materials, r.built.numbers) if n and m.parent == "Pulmón"]
    assert len(lung) >= 2

    def table_colors():
        t = w.materials.table
        out = {}
        for row in range(t.rowCount()):
            it = t.item(row, 0)
            if it is not None and it.text().strip().isdigit():
                out[int(it.text())] = it.icon().pixmap(14, 14).toImage().pixelColor(7, 7).name()
        return out

    def check():
        views = {w.material_colors.get(n) or material_color(n) for n in lung}
        assert len(views) == 1
        assert {table_colors().get(n) for n in lung} == views

    check()
    w.materials._reset_colors()
    check()
    assert not w.materials.btn_colors.isEnabled()
    assert w.save_all()
    w.open_file(case_dir / "maniqui.in")
    check()
