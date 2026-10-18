"""Novena revisión (0.16.3), el diálogo «Importar un TAC»: tejidos que solo aparecen al promediar, cambiar la
calibración o cargar una curva con una serie cargada, los textos del combo «Material del .in» y lo que tarda mirar qué
tejidos hay en el recorte."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pydicom")

from dicom_synth import write_series  # noqa: E402

pytestmark = pytest.mark.gui

IN_2 = ("TITLE  tac\nSKPAR  4\nSENERG 1e8\nSPOSIT 0 0 -100\nSCONE  0 0 0\n"
        "MFNAME water.mat                      [Water, liquid (PENELOPE 278)]\n"
        "MFNAME air.mat                        [Air, dry (PENELOPE 104)]\n"
        "GEOMFN g.geo                          [Geometry file, up to 20 chars]\nNSIMSH 1e5\n")


def _water(P):
    """Un cilindro de agua (0 HU) en aire: sin pulmón, grasa ni hueso."""
    x, y = P[..., 0], P[..., 1]
    hu = np.full(x.shape, -1000.0)
    hu[x ** 2 + y ** 2 < 15.3 ** 2] = 0.0
    return hu


@pytest.fixture(scope="module")
def water_series(tmp_path_factory):
    d = tmp_path_factory.mktemp("agua") / "dcm"
    write_series(d, _water, rows=40, cols=40, nslices=6, spacing=(1.0, 1.0), dz=2.0, origin=(-19.5, -19.5, 0.0),
                 description="Agua")
    return d


def _loaded(qtbot, folder, in_doc=None):
    from argus.ui.ct_import_dialog import CtImportDialog

    dlg = CtImportDialog(None, None, in_doc, folder)
    qtbot.addWidget(dlg)
    dlg.scan()
    qtbot.waitUntil(lambda: dlg.vol is not None and not dlg.busy(), timeout=30000)
    dlg._update_all()
    return dlg


def _labels(dlg) -> list[str]:
    return [dlg.tissue_table.cellWidget(i, 2).itemText(0) for i in range(dlg.tissue_table.rowCount())]


@pytest.mark.parametrize("split", ["none", "auto"])
def test_voxels_averaged_into_a_tissue_the_dialog_did_not_see_are_not_void(qtbot, water_series, split):
    dlg = _loaded(qtbot, water_series)
    assert dlg.present_tissues() == [True, False, False, True, False]  # con los HU sin promediar
    dlg.split_combo.setCurrentIndex(dlg.split_combo.findData(split))
    dlg.cttrack_check.setChecked(False)
    dlg.voxel_choice.setCurrentText("2 mm")  # vóxel más grande que los píxeles: el borde sale promediado
    built = dlg.build_import().build()
    assert int((built.ct.material == 0).sum()) == 0  # antes, todo el anillo del borde era vacío (MC = 0)
    assert any(f.startswith("ct_pulmon") for _n, f, _d in built.plan.new)


def test_without_room_in_the_in_for_the_tissues_that_appear_nothing_is_written(qtbot, water_series, tmp_path):
    """Con un .in casi lleno, el tejido que solo aparece al promediar ya no cabe: no se escribe nada (antes se
    escribía como vacío) y el mensaje dice cuál es y qué hacer."""
    from argus.penhan.inputfile import parse_in_text

    lines = "".join(f"MFNAME m{i}.mat                         [Material file, up to 20 chars]\n" for i in range(1, 9))
    text = "TITLE  tac\nSKPAR  4\nSENERG 1e8\nSPOSIT 0 0 -100\nSCONE  0 0 0\n" + lines + "GEOMFN g.geo\nNSIMSH 1e5\n"
    dlg = _loaded(qtbot, water_series, parse_in_text(text, str(tmp_path / "c.in")))
    plan = dlg.plan()
    assert plan.numbers == [9, 0, 0, 10, 0] and not plan.problems  # con los HU sin promediar caben justos
    dlg.geo_edit.setText(str(tmp_path / "nueva.geo"))
    dlg.voxel_choice.setCurrentText("2 mm")
    dlg._update_all()
    assert dlg.import_btn.isEnabled()
    dlg._import()
    qtbot.waitUntil(lambda: dlg.result is not None or (not dlg.busy() and bool(dlg._error)), timeout=60000)
    assert dlg.result is None and "11 materiales" in dlg._error and "(Pulmón)" in dlg._error
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("tac")]


def test_the_material_combo_texts_follow_the_plan_once_the_series_is_read(qtbot, water_series):
    dlg = _loaded(qtbot, water_series)
    plan = dlg.plan()
    assert plan.numbers == [1, 0, 0, 2, 0]
    # Los textos de «automático» son los del plan con la serie (antes seguían los de antes de leerla).
    labels = _labels(dlg)
    assert labels[0] == "nuevo (n.º 1)" and labels[3] == "nuevo (n.º 2)"
    assert all(lab.startswith("ninguno") for lab, n in zip(labels, plan.numbers) if n == 0)


def test_changing_the_calibration_or_the_curve_with_a_series_loaded(qtbot, water_series):
    dlg = _loaded(qtbot, water_series)
    assert 0 in dlg.plan().numbers  # hay tejidos sin vóxeles (y sin .in): antes, IndexError al reescribir la tabla
    with qtbot.captureExceptions() as errors:
        dlg.preset_combo.setCurrentIndex(dlg.preset_combo.findData("penelope"))
        dlg.set_curve([(-1000.0, 0.001), (0.0, 1.0), (3000.0, 2.8)], "curva.csv")
    assert not errors
    t = dlg.tissue_table
    assert not t.signalsBlocked() and not dlg.curve_table.signalsBlocked()
    cal = dlg.calibration
    assert [t.item(i, 3).text() for i in range(t.rowCount())] == [x.mat_file for x in cal.tissues]
    assert [t.item(i, 4).text() for i in range(t.rowCount())] == [f"{x.density:.6g}" for x in cal.tissues]
    assert dlg.curve_table.rowCount() == 4 and dlg.curve_table.item(1, 1).text() == "1"
    # Lo que se escribe después en la tabla se usa al importar.
    t.item(4, 1).setText("300")
    t.item(3, 3).setText("blando2.mat")
    imp = dlg.build_import()
    assert imp.calibration.tissues[4].hu_min == 300.0 and imp.calibration.tissues[3].mat_file == "blando2.mat"


def test_the_material_combo_texts_follow_the_plan_with_an_in(qtbot, water_series, tmp_path):
    from argus.penhan.inputfile import parse_in_text

    in_doc = parse_in_text(IN_2, str(tmp_path / "c.in"))
    dlg = _loaded(qtbot, water_series, in_doc)
    assert dlg.plan().numbers == [3, 0, 0, 4, 0]
    labels = _labels(dlg)
    assert labels[0] == "nuevo (n.º 3)" and labels[3] == "nuevo (n.º 4)"
    assert not any(lab.startswith("0:") for lab in labels)  # antes: «0: air.mat (mismo fichero)»
    combo = dlg.tissue_table.cellWidget(0, 2)
    combo.setCurrentIndex(combo.findData(2))  # el aire del TAC, con el air.mat del .in
    dlg._update_all()
    labels = _labels(dlg)
    assert labels[0] == "automático" and labels[3] == "nuevo (n.º 3)"


def test_finding_the_tissues_of_the_crop_does_not_sort_it(qtbot, water_series, monkeypatch):
    from argus.ui import ct_import_dialog as mod

    dlg = _loaded(qtbot, water_series)
    hu = dlg.vol.hu
    assert hu.dtype == np.int16
    real = np.unique
    sizes: list[int] = []

    def spy(a, *args, **kwargs):
        sizes.append(int(np.size(a)))
        return real(a, *args, **kwargs)

    monkeypatch.setattr(np, "unique", spy)
    dlg._hu_values = None
    assert dlg.present_tissues() == [True, False, False, True, False]
    assert not any(s >= 1000 for s in sizes)  # sin ordenar el recorte (con 50 millones de vóxeles, segundos)
    monkeypatch.setattr(np, "unique", real)
    for part in (hu, hu[:, 3:30, 5:20], hu[2:3, :1, :]):
        assert np.array_equal(mod.distinct_hu(part), real(part))
    f = hu.astype(np.float32) + 0.5  # HU no enteros (reescala con decimales): np.unique
    assert np.array_equal(mod.distinct_hu(f), real(f))
