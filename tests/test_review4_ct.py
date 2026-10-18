"""Fallos de la cuarta revisión del importador de TAC (0.10): buscar series otra vez, cancelar mientras se escribe
el .ct e importar de nuevo, números de material que ya usa la geometría, tejidos sin vóxeles en la cuenta de
materiales, curvas con «, », valores de las tablas que no se entienden, la etiqueta del CTBOX, TAC mejorados con el
rescale en la raíz y .mat de otra importación."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
pydicom = pytest.importorskip("pydicom")

from argus.ct.calibration import parse_curve_text, preset  # noqa: E402
from argus.ct.dicom import read_series, scan_folder  # noqa: E402
from argus.ct.grid import auto_crop, make_grid  # noqa: E402
from argus.ct.importer import CtImport, add_ctbox, plan_materials  # noqa: E402
from argus.pengeom import ct as C  # noqa: E402
from argus.pengeom.compiler import compile_geometry  # noqa: E402
from argus.pengeom.edit import EditError  # noqa: E402
from argus.pengeom.parser import load_geometry, parse_geo_text  # noqa: E402
from argus.pengeom.writer import write_geo_text  # noqa: E402
from dicom_synth import linear_hu, phantom_hu, write_series  # noqa: E402

Z = "0" * 64
EMPTY = "Geometria con TAC\n" + Z + "\nEND      " + "0" * 55 + "\n"


def _dialog(qtbot, folder, session=None, in_doc=None):
    from argus.ui.ct_import_dialog import CtImportDialog

    dlg = CtImportDialog(None, session, in_doc, folder)
    qtbot.addWidget(dlg)
    return dlg


def _scan(qtbot, dlg):
    dlg.scan()
    qtbot.waitUntil(lambda: not dlg.busy(), timeout=30000)
    qtbot.wait(50)


@pytest.fixture(scope="module")
def folders(tmp_path_factory):
    d = tmp_path_factory.mktemp("series")
    write_series(d / "a", phantom_hu, rows=40, cols=36, nslices=12, spacing=(1.0, 1.25), dz=2.0,
                 origin=(-20, -18, 10), description="Serie A")
    write_series(d / "b", phantom_hu, rows=24, cols=20, nslices=6, spacing=(2.0, 2.0), dz=3.0,
                 origin=(-20, -18, 10), description="Serie B")
    write_series(d / "rm", phantom_hu, rows=12, cols=10, nslices=4, description="Serie RM", modality="MR")
    (d / "vacia").mkdir()
    return d


# ---------------------------------------------------------------------------------------------------------------------
# Buscar series otra vez
# ---------------------------------------------------------------------------------------------------------------------

def test_scanning_another_folder_drops_the_series_that_was_loaded(qtbot, folders):
    dlg = _dialog(qtbot, folders / "a")
    _scan(qtbot, dlg)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    first = dlg.vol
    dlg.folder_edit.setText(str(folders / "b"))
    _scan(qtbot, dlg)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    assert dlg.vol is not first and dlg.vol.shape == (20, 24, 6)
    for bad, why in (("rm", "Ninguna serie"), ("vacia", "No se ha encontrado")):
        dlg.folder_edit.setText(str(folders / bad))
        _scan(qtbot, dlg)
        assert dlg.vol is None and not dlg.import_btn.isEnabled() and why in dlg.status.text()


# ---------------------------------------------------------------------------------------------------------------------
# Cancelar mientras se escribe el .ct e importar otra vez
# ---------------------------------------------------------------------------------------------------------------------

def test_cancelling_while_writing_then_importing_again_keeps_the_second_ct(qtbot, folders, tmp_path, monkeypatch):
    case = tmp_path / "caso"
    case.mkdir()
    real = C.write_ct_stream
    started = threading.Event()
    calls = {"n": 0}

    class Slow:
        def __init__(self, fh):
            self.fh = fh

        def write(self, b):
            time.sleep(0.01)
            return self.fh.write(b)

    def stream(fh, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # la primera importación escribe despacio (un TAC grande en un disco lento)
            started.set()
            return real(Slow(fh), *a, **{**k, "chunk": 64})
        return real(fh, *a, **k)

    monkeypatch.setattr(C, "write_ct_stream", stream)
    dlg = _dialog(qtbot, folders / "a")
    _scan(qtbot, dlg)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    dlg.geo_edit.setText(str(case / "nueva.geo"))
    dlg.split_combo.setCurrentIndex(dlg.split_combo.findData("none"))
    dlg.voxel_choice.setCurrentText("Como el TAC")
    dlg._update_all()
    dlg._import()
    assert started.wait(60)
    dlg._cancel()
    assert not dlg.import_btn.isEnabled()  # hasta que la cancelada deje de escribir
    qtbot.waitUntil(lambda: not dlg._zombies, timeout=30000)
    dlg.voxel_choice.setCurrentText("5 mm")
    dlg._update_all()
    assert dlg.import_btn.isEnabled()
    dlg._import()
    qtbot.waitUntil(lambda: dlg.result is not None or (not dlg.busy() and dlg._error), timeout=60000)
    r = dlg.result
    assert r is not None, dlg._error
    ct = C.read_ct_file(case / "tac.ct")
    assert ct.shape == r.built.ct.shape and np.array_equal(ct.material, r.built.ct.material)
    assert not [p for p in case.iterdir() if "argus-tmp" in p.name]


# ---------------------------------------------------------------------------------------------------------------------
# Materiales
# ---------------------------------------------------------------------------------------------------------------------

SPHERE_3 = ("Esfera de material 3\n" + Z + "\nSURFACE (   1)   esfera\nINDICES=( 1, 1, 1, 0,-1)\n"
            "X-SCALE=( 5.000000000000000E+01,   0)\nY-SCALE=( 5.000000000000000E+01,   0)\n"
            "Z-SCALE=( 5.000000000000000E+01,   0)\n" + Z + "\nBODY    (   1)   esfera\nMATERIAL(   3)\n"
            "SURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\nEND      " + "0" * 55 + "\n")
IN_2 = ("TITLE  tac\nSKPAR  4\nSENERG 1e8\nSPOSIT 0 0 -100\nSCONE  0 0 0\n"
        "MFNAME water.mat                      [Material file, up to 20 chars]\n"
        "MFNAME air.mat                        [Material file, up to 20 chars]\n"
        "GEOMFN g.geo                          [Geometry file, up to 20 chars]\nNSIMSH 1e5\n")


def _session(folder, geo_text=SPHERE_3, in_text=IN_2):
    from argus.session import open_case

    (folder / "g.geo").write_text(geo_text, encoding="latin-1")
    (folder / "c.in").write_text(in_text, encoding="latin-1")
    case = open_case(folder / "c.in")
    return case.session, case.input_doc


def test_ct_materials_never_take_numbers_the_geometry_uses(qtbot, folders, tmp_path):
    session, doc = _session(tmp_path)
    assert session.geo.nmat == 3
    dlg = _dialog(qtbot, folders / "a", session, doc)
    _scan(qtbot, dlg)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    dlg._update_all()
    assert any("solo declara 2" in p for p in dlg.problems()) and not dlg.import_btn.isEnabled()


def test_tissues_without_voxels_do_not_count_against_penhmain_materials(qtbot, folders):
    dlg = _dialog(qtbot, folders / "a")
    _scan(qtbot, dlg)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    present = dlg.present_tissues()
    assert present is not None and len(present) == len(dlg.calibration.tissues)
    plan = dlg.plan()
    assert all(n == 0 for n, p in zip(plan.numbers, present) if not p)
    assert len(plan.new) == sum(present)


def test_curves_with_comma_and_space():
    assert parse_curve_text("-1000, 0.00121\n0, 1.0\n1000, 1.6\n") == [(-1000.0, 0.00121), (0.0, 1.0),
                                                                        (1000.0, 1.6)]
    assert parse_curve_text("HU, densidad\naire, -1000, 0.00121\nagua, 0, 1\n") == [(-1000.0, 0.00121), (0.0, 1.0)]
    assert parse_curve_text("-1000 0,00121\n0 1,0\n") == [(-1000.0, 0.00121), (0.0, 1.0)]  # coma decimal


def test_table_cells_that_are_not_numbers_stop_the_import(qtbot, folders):
    from PySide6.QtWidgets import QTableWidgetItem

    dlg = _dialog(qtbot, folders / "a")
    _scan(qtbot, dlg)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    dlg.tissue_table.setItem(len(dlg.calibration.tissues) - 1, 1, QTableWidgetItem("15O"))
    dlg.curve_table.setItem(1, 1, QTableWidgetItem("1,96,42"))
    dlg._update_all()
    probs = " ".join(dlg.problems())
    assert "15O" in probs and "1,96,42" in probs and not dlg.import_btn.isEnabled()
    dlg.tissue_table.setItem(len(dlg.calibration.tissues) - 1, 1, QTableWidgetItem("150"))
    dlg._update_all()
    assert "15O" not in " ".join(dlg.problems())


def test_the_ctbox_label_is_checked(qtbot, folders, tmp_path):
    dlg = _dialog(qtbot, folders / "a")
    _scan(qtbot, dlg)
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    dlg.label_edit.setText("a'b")
    dlg._update_all()
    assert any("CTBOX" in p for p in dlg.problems())
    doc = parse_geo_text(EMPTY, str(tmp_path / "g.geo"))
    geo = compile_geometry(doc, base_dir=tmp_path)
    with pytest.raises(EditError):
        add_ctbox(doc, geo, "tac.ct", np.eye(3), np.zeros(3), None, "a,b", "TAC")


# ---------------------------------------------------------------------------------------------------------------------
# DICOM y .mat
# ---------------------------------------------------------------------------------------------------------------------

def test_enhanced_ct_with_the_rescale_at_the_root(tmp_path):
    write_series(tmp_path, linear_hu, enhanced=True, intercept=-1024.0)
    p = next(tmp_path.iterdir())
    ds = pydicom.dcmread(str(p))
    pv = ds.SharedFunctionalGroupsSequence[0].PixelValueTransformationSequence[0]
    del ds.SharedFunctionalGroupsSequence[0].PixelValueTransformationSequence
    ds.RescaleIntercept, ds.RescaleSlope = pv.RescaleIntercept, pv.RescaleSlope
    ds.save_as(str(p), enforce_file_format=True)
    vol = read_series(scan_folder(tmp_path)[0])
    x, y, zc = np.meshgrid(vol.x, vol.y, vol.z, indexing="ij")
    exact = linear_hu(np.stack([x, y, zc], -1) @ vol.rotation.T).transpose(2, 1, 0)
    assert float(np.abs(vol.hu - exact).max()) < 1.0


def test_a_mat_left_by_another_import_is_flagged(tmp_path):
    from argus.penhan.inputfile import parse_in_text
    from argus.penhan.materials import materials_table, materials_txt

    write_series(tmp_path / "dcm", phantom_hu, rows=40, cols=36, nslices=20, spacing=(1.0, 1.25), dz=2.0,
                 origin=(-20, -18, 10))
    vol = read_series(scan_folder(tmp_path / "dcm")[0])
    lo, hi = auto_crop(vol)
    cal = preset("schneider5")
    plan = plan_materials(cal, ["water.mat"])
    imp = CtImport(vol, make_grid(lo, hi, [2.5, 2.5, 2.5]), cal, plan.numbers, "centro", ct_name="tac.ct",
                   title="TAC", existing=["water.mat"])
    built = imp.build()
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    doc = parse_geo_text(EMPTY, str(folder / "g.geo"))
    geo = compile_geometry(doc, base_dir=folder)
    add_ctbox(doc, geo, "tac.ct", built.rotation, built.shift, None, "TAC", "TAC")
    (folder / "g.geo").write_text(write_geo_text(doc))
    geo = compile_geometry(load_geometry(folder / "g.geo"), base_dir=folder)
    lines = "".join(f"MFNAME {f:<20}          [Material file, up to 20 chars]\n"
                    for f in ["water.mat"] + [f for _n, f, _d in built.plan.new])
    in_doc = parse_in_text("TITLE  tac\nSKPAR  4\nSENERG 1e8\n" + lines + "GEOMFN g.geo\n", str(folder / "c.in"))
    rows = materials_table(in_doc, geo, folder)
    ct_rows = [r for r in rows if r.tissue is not None and penhmat_row(r)]
    assert ct_rows
    r = ct_rows[0]
    (folder / r.fname).write_text(" PENELOPE (v. 2018)  Material data file ...............\n"
                                  " Material: OTRO (TAC)\n Mass density = 9.99000000E-01 g/cm**3\n")
    rows = materials_table(in_doc, geo, folder)
    stale = next(x for x in rows if x.number == r.number)
    assert stale.stale and any("otra importación" in p.message for p in stale.problems)
    assert "hay que generarlo de nuevo" in materials_txt(rows, "c.in", geo)


def penhmat_row(r) -> bool:
    from argus.ct.calibration import penhmat_composition

    return bool(penhmat_composition(r.tissue))
