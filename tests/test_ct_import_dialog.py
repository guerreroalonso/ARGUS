"""Diálogo «Importar un TAC (DICOM)» sin la ventana principal: series, rejilla, nombres y cancelar."""

import threading

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pydicom")

from dicom_synth import phantom_hu, pixel_centers, write_series  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def two_series(tmp_path_factory):
    d = tmp_path_factory.mktemp("dos")
    write_series(d / "a", phantom_hu, rows=40, cols=36, nslices=12, spacing=(1.0, 1.25), dz=2.0,
                 origin=(-20, -18, 10), description="Serie A")
    write_series(d / "b", phantom_hu, rows=24, cols=20, nslices=6, spacing=(2.0, 2.0), dz=3.0,
                 origin=(-20, -18, 10), description="Serie B")
    return d


def _dialog(qtbot, folder):
    from argus.ui.ct_import_dialog import CtImportDialog

    dlg = CtImportDialog(None, None, None, folder)
    qtbot.addWidget(dlg)
    return dlg


def _load(qtbot, dlg, row=None):
    if row is None:
        dlg.scan()
    else:
        dlg.series_table.selectRow(row)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    return dlg.vol


def _row(dlg, description):
    return next(i for i, s in enumerate(dlg.series) if s.description == description)


def test_switching_series_imports_the_one_that_is_marked(qtbot, two_series):
    dlg = _dialog(qtbot, two_series)
    first = _load(qtbot, dlg)
    other = "Serie B" if dlg.series[dlg.series_table.currentRow()].description == "Serie A" else "Serie A"
    dlg.series_table.selectRow(_row(dlg, other))
    assert dlg.vol is None  # el volumen anterior deja de valer en cuanto se cambia de fila
    second = _load(qtbot, dlg, _row(dlg, other))
    assert second is not first and second.shape != first.shape


def test_ct_name_is_free_in_the_folder_and_the_geometry_path_is_complete(qtbot, two_series, tmp_path):
    folder = tmp_path / "caso"
    folder.mkdir()
    (folder / "tac.ct").write_text("otro caso\n")
    dlg = _dialog(qtbot, two_series)
    _load(qtbot, dlg)
    assert dlg.name_edit.text() == "tac.ct"
    dlg.geo_edit.setText(str(folder / "nueva.geo"))
    assert dlg.name_edit.text() == "tac2.ct"  # no pisa el .ct de otra geometría de la carpeta
    dlg.name_edit.selectAll()
    qtbot.keyClicks(dlg.name_edit, "mio.ct")
    dlg.geo_edit.setText(str(tmp_path / "otra.geo"))
    assert dlg.name_edit.text() == "mio.ct"  # lo que escribe el usuario no se cambia
    assert not any("ruta completa" in p for p in dlg.problems())
    dlg.geo_edit.setText("relativa.geo")  # sería relativa a la carpeta de ARGUS
    assert any("ruta completa" in p for p in dlg.problems()) and dlg.target_folder() is None


def test_tissue_mat_names_are_checked(qtbot, two_series):
    dlg = _dialog(qtbot, two_series)
    _load(qtbot, dlg)
    dlg.tissue_table.item(1, 3).setText("ct_pulmon_del_paciente.mat")  # más de 20 caracteres (A20 de MFNAME)
    assert any("ct_pulmon_del_paciente.mat" in p for p in dlg.problems())
    dlg.tissue_table.item(1, 3).setText("")
    assert dlg.problems()


def test_like_the_scan_reproduces_its_grid_exactly(qtbot, tmp_path):
    from argus.ct.grid import resample

    d = tmp_path / "fino"
    write_series(d, phantom_hu, rows=30, cols=32, nslices=8, spacing=(0.9765625, 0.9765625), dz=2.5,
                 origin=(-15.13671875, -14.6484375, 3.75))
    dlg = _dialog(qtbot, d)
    vol = _load(qtbot, dlg)
    dlg.fit_all()
    dlg.voxel_choice.setCurrentText("1 mm")
    dlg.voxel_choice.setCurrentText("Como el TAC")
    g = dlg.grid()
    lo, hi = vol.bounds()
    assert np.array_equal(g.voxel, np.array(vol.spacing())) and np.array_equal(g.lo, lo)
    assert g.shape == vol.shape
    assert np.max(np.abs(resample(vol, g) - vol.hu.astype(np.float32))) < 1e-3  # sin mezclar vecinos
    # Si se escribe otro valor en un cuadro, manda lo escrito.
    dlg.voxel["x"].setValue(2.0)
    assert dlg.voxel_size()[0] == 2.0


def test_default_point_is_the_center_of_an_oblique_scan(qtbot, tmp_path):
    d = tmp_path / "coronal"
    iop = (1, 0, 0, 0, 0, -1)  # coronal: filas a lo largo de -z del paciente
    write_series(d, phantom_hu, rows=16, cols=20, nslices=10, spacing=(2.0, 1.5), dz=2.0,
                 origin=(-15.0, -10.0, 40.0), iop=iop)
    dlg = _dialog(qtbot, d)
    _load(qtbot, dlg)
    center = pixel_centers((-15.0, -10.0, 40.0), iop, (2.0, 1.5), 16, 20, 10, 2.0).reshape(-1, 3).mean(axis=0)
    got = np.array([dlg.point[a].value() for a in "xyz"])
    assert np.allclose(got, center, atol=0.01)


def test_closing_while_the_ct_is_written_leaves_nothing(qtbot, two_series, tmp_path, monkeypatch):
    from argus.ct.importer import CtImport

    folder = tmp_path / "caso"
    folder.mkdir()
    dlg = _dialog(qtbot, two_series)
    _load(qtbot, dlg)
    dlg.geo_edit.setText(str(folder / "nueva.geo"))
    dlg.voxel_choice.setCurrentText("5 mm")
    written, go_on = threading.Event(), threading.Event()
    real = CtImport.write_temp

    def slow_write(self, *a, **k):
        out = real(self, *a, **k)
        written.set()
        go_on.wait(10)
        return out

    monkeypatch.setattr(CtImport, "write_temp", slow_write)
    dlg._update_all()
    dlg._import()
    assert written.wait(30)
    dlg.reject()  # la X, Esc o Cancelar mientras se escribe
    go_on.set()
    qtbot.waitUntil(lambda: not any(folder.iterdir()) or False, timeout=10000)
    qtbot.wait(200)
    assert list(folder.iterdir()) == [] and dlg.result is None


def test_an_import_whose_materials_do_not_fit_writes_nothing(qtbot, two_series, tmp_path, monkeypatch):
    """Si ni sin partir caben los materiales en el .in (MAXMAT), no se escribe el .ct (revisión final)."""
    from argus.ct.importer import CtImport, MaterialPlan

    folder = tmp_path / "caso"
    folder.mkdir()
    dlg = _dialog(qtbot, two_series)
    _load(qtbot, dlg)
    dlg.geo_edit.setText(str(folder / "nueva.geo"))
    dlg.voxel_choice.setCurrentText("5 mm")
    real = CtImport.build

    def build(self, *a, **k):
        b = real(self, *a, **k)
        b.plan = MaterialPlan(list(b.numbers), [], ["El .in tendría 11 materiales y penhmain admite 10."])
        return b

    monkeypatch.setattr(CtImport, "build", build)
    dlg._update_all()
    dlg._import()
    qtbot.waitUntil(lambda: not dlg.busy(), timeout=30000)
    assert list(folder.iterdir()) == [] and dlg.result is None
    assert "11 materiales" in dlg.status.text()
