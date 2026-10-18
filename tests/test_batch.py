"""Simulaciones en paralelo y campañas: preparación (penhan.batch), índice, órdenes y seguimiento en Resultados."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from argus.penhan import batch as bt
from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_file, parse_in_text, write_in_text
from argus.results.folder import ResultsFolder, aggregate_state
from argus.results.store import ResultsStore
from conftest import CORPUS

RES = CORPUS / "resultados"


@pytest.fixture
def case(tmp_path):
    d = tmp_path / "caso"
    d.mkdir()
    for f in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy2(RES / "caja" / f, d / f)
    return d / "caja.in"


def test_parallel_runs_have_independent_seeds_and_split_showers(case, tmp_path):
    doc = parse_in_file(case)
    b = bt.make_parallel(doc, case, tmp_path / "par", 4, total_showers=10001, dumpp=10)
    assert [r.folder for r in b.runs] == ["run-01", "run-02", "run-03", "run-04"]
    for i, r in enumerate(b.runs, start=1):
        d = parse_in_text(r.in_text)
        assert sc.seeds(d) == [(-i, 0)]
        assert sc.value(d, "NSIMSH")[0] == 2501.0  # 10001 / 4 hacia arriba
        assert sc.text(d, "DUMPTO") == "dump.dmp"
        assert sc.value(d, "DUMPP")[0] == 30.0  # penhmain no baja de 30 s
        assert d.model.title == doc.model.title  # penhmain-sum exige el mismo título
    with pytest.raises(ValueError):
        bt.make_parallel(doc, case, tmp_path / "x", 3, first_seed=1000)  # solo hay 1001 pares
    with pytest.raises(ValueError):
        bt.make_parallel(doc, case, tmp_path / "x", 1)


def test_resume_is_removed(case, tmp_path):
    doc = parse_in_file(case)
    sc.set_text(doc, "RESUME", "viejo.dmp")
    b = bt.make_parallel(doc, case, tmp_path / "par", 2)
    assert sc.text(parse_in_text(b.runs[0].in_text), "RESUME") is None
    assert any("RESUME" in n for n in b.notes)
    assert b.showers is None and sc.value(parse_in_text(b.runs[1].in_text), "NSIMSH") == sc.value(doc, "NSIMSH")


def test_write_parallel_never_overwrites(case, tmp_path):
    doc = parse_in_file(case)
    dest = tmp_path / "par"
    b = bt.make_parallel(doc, case, dest, 2, total_showers=100)
    bt.plan_files(b)
    assert any("water.mat" in p for p in b.problems)  # el corpus no trae los .mat: se avisa
    w = bt.write_batch(b, jobs=2)
    assert not w.failed and not w.cancelled
    for r in ("run-01", "run-02"):
        assert (dest / r / "caja.in").is_file() and (dest / r / "caja.geo").is_file()
    man = json.loads((dest / "argus-paralelo.json").read_text("utf-8"))
    assert man["kind"] == "paralelo" and [r["folder"] for r in man["runs"]] == ["run-01", "run-02"]
    sh = (dest / "lanzar.sh").read_text("utf-8")
    assert "xargs -P" in sh and "GFORTRAN_UNBUFFERED_PRECONNECTED=y" in sh and '"caja.in"' in sh
    assert "Start-Process" in (dest / "lanzar.ps1").read_text("utf-8")
    assert b"\r\n" in (dest / "lanzar.bat").read_bytes()
    comb = (dest / "combinar.sh").read_text("utf-8")
    assert "../run-01/dump.dmp" in comb and "mkdir 'suma'" in comb
    # Otra vez en la misma carpeta: no se toca nada.
    (dest / "run-01" / "caja.in").write_text("editado a mano", "latin-1")
    w2 = bt.write_batch(bt.make_parallel(doc, case, dest, 2, total_showers=100), jobs=2)
    assert not w2.created and (dest / "run-01" / "caja.in").read_text("latin-1") == "editado a mano"
    # La ventana de resultados las encuentra por el índice aunque no hayan empezado.
    f = ResultsFolder(dest)
    assert [r.path.name for r in f.runs] == ["run-01", "run-02"] and f.is_parallel
    assert aggregate_state(f).text.startswith("2 ejecuciones: 2 sin empezar")


def test_campaign_variants(case, tmp_path):
    doc = parse_in_file(case)
    p1 = bt.Param("keyword", bt.parse_values("-5 -8"), keyword="SPOSIT", index=2, unit="cm")
    p2 = bt.Param("keyword", [4.0, 8.0], keyword="SCONE", index=2, unit="grados")
    b = bt.make_campaign(doc, case, tmp_path / "camp", [p1, p2], replicas=2, showers=1000)
    assert len(b.variants) == 4 and len(b.runs) == 8
    v = b.variants[3]
    assert v.label == "SPOSIT z = -8 cm, SCONE α = 8 grados" and v.folder.startswith("v4-") is False
    assert v.folder == "v04-SPOSIT_-8-SCONE_8"
    d = parse_in_text(v.runs[1].in_text)
    assert sc.value(d, "SPOSIT")[2] == -8.0 and sc.value(d, "SCONE")[2] == 8.0
    assert sc.value(d, "NSIMSH")[0] == 500.0
    seeds = [sc.seeds(parse_in_text(r.in_text))[0][0] for r in b.runs]
    assert sorted(seeds) == list(range(-8, 0))  # todas distintas
    same = bt.make_campaign(doc, case, tmp_path / "c2", [p1], replicas=2, same_seeds=True)
    assert [sc.seeds(parse_in_text(r.in_text))[0][0] for r in same.runs] == [-1, -2, -1, -2]
    w = bt.write_batch(b, jobs=4)
    assert not w.failed
    man = json.loads((tmp_path / "camp" / "argus-campana.json").read_text("utf-8"))
    assert man["kind"] == "campana" and len(man["runs"]) == 4 and man["runs"][0]["values"] == {"SPOSIT z": -5.0,
                                                                                               "SCONE α": 4.0}
    assert (tmp_path / "camp" / v.folder / "argus-paralelo.json").is_file()
    f = ResultsFolder(tmp_path / "camp")
    assert f.is_campaign and len(f.runs) == 4 and all(len(r.runs) == 2 for r in f.runs)
    assert len(f.leaves()) == 8 and f.variant_values(3) == {"SPOSIT z": -8.0, "SCONE α": 8.0}


def test_other_parameters_and_values():
    assert bt.parse_values("50 100; 150") == [50.0, 100.0, 150.0]
    assert bt.parse_values("0:1:0.25") == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert bt.parse_values("10-20/3") == [10.0, 15.0, 20.0]
    with pytest.raises(ValueError):
        bt.parse_values("5:1:1")
    doc = parse_in_file(RES / "cilindro" / "cilindro.in")
    bt.Param("material", ["hueso.mat"], number=1).apply(doc, "hueso.mat")
    assert parse_in_text(write_in_text(doc)).model.materials[0].fname == "hueso.mat"
    p = bt.Param("parinp", [2.5], number=3)
    p.apply(doc, 2.5)
    assert sc.parinp(doc)[3] == 2.5
    assert p.text(2.5) == "PARINP 3 = 2.5"


def test_campaign_results_from_real_runs(qtbot):
    pytest.importorskip("matplotlib")
    from argus.ui.results.window import CAMPAIGN_KEY, Ref, ResultsWindow
    from argus.ui.settings import app_settings

    f = ResultsFolder(RES / "campana")
    assert f.is_campaign and len(f.runs) == 3 and len(f.leaves()) == 6
    st = aggregate_state(f)
    assert st.status == "terminada" and "3 variantes (6 ejecuciones)" in st.text
    # Cada variante combina sus dos ejecuciones.
    store = ResultsStore()
    c = store.get(f.runs[0], "depth-dose")
    a = store.load(f.runs[0].runs[0].find("depth-dose"))
    b = store.load(f.runs[0].runs[1].find("depth-dose"))
    np.testing.assert_allclose(c.series[0].y, 0.5 * (a.series[0].y + b.series[0].y), rtol=1e-6)
    w = ResultsWindow(settings=app_settings())
    qtbot.addWidget(w)
    ds = w.open_folder(RES / "campana")
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=20000)
    assert "variantes" in w.summary_view.browser.toPlainText()
    w.select(Ref(ds.key, "depth-dose", None, True))
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=20000)
    labels = [o.label for o in w.curves_view.overlays]
    assert labels == ["SPOSIT z = -8 cm", "SPOSIT z = -11 cm"]
    assert all(o.color for o in w.curves_view.overlays) and w.curves_view.primary_color
    w.select(Ref(ds.key, CAMPAIGN_KEY, None, True))
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=20000)
    cv = w.views.currentWidget()
    assert type(cv).__name__ == "CampaignView" and all(cv.results)
    cv.model.setCurrentIndex(cv.model.findData("lineal"))
    assert "Recta" in cv.fit_text.text()
    cols, data = cv.table_rows()
    assert list(data[0]) == [-5.0, -8.0, -11.0]
    w.select(Ref(ds.key, "res", 2, False))
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=20000)
    assert w.current_data.showers == pytest.approx(4000)  # las dos ejecuciones de la variante
    w.close()


@pytest.mark.gui
def test_batch_dialog(qtbot, case, tmp_path):
    from argus.ui.batch_dialog import BatchDialog
    from argus.ui.settings import app_settings

    doc = parse_in_file(case)
    dlg = BatchDialog(None, case, doc, app_settings(), "paralelo")
    qtbot.addWidget(dlg)
    dlg.nruns.setValue(3)
    dlg.dest.setText(str(tmp_path / "p"))
    dlg.exe.setText("")
    dlg._preview()
    assert dlg.go.isEnabled() and dlg.preview.count() == 3
    dlg.prepare()
    qtbot.waitUntil(lambda: dlg.written is not None, timeout=30000)
    assert (tmp_path / "p" / "run-03" / "caja.in").is_file() and not dlg.follow.isHidden()
    with qtbot.waitSignal(dlg.openResults, timeout=2000):
        dlg.follow.click()
    camp = BatchDialog(None, case, doc, app_settings(), "campana")
    qtbot.addWidget(camp)
    # NSIMSH se llama «Partículas», como en «Trabajo» (era «Lluvias por variante»; «Lluvias», desde 0.17.1, solo las
    # que se ven en 3D).
    from PySide6.QtWidgets import QFormLayout

    labs = [f.labelForField(camp.showers) for f in camp.findChildren(QFormLayout)]
    labs = [lab.text() for lab in labs if lab is not None]
    assert labs == ["Partículas por variante:"] and "NSIMSH" in camp.showers.toolTip()
    camp.p1.kind.setCurrentIndex(camp.p1.kind.findData("pos_z"))
    camp.p1.values.setText("-5:-3:1")
    camp.replicas.setValue(2)
    camp.dest.setText(str(tmp_path / "c"))
    camp._preview()
    assert camp.preview.count() == 6
    camp.dest.setText(str(tmp_path / "p"))  # ya existe y no está vacía
    camp._preview()
    assert not camp.go.isEnabled() and "ya existe" in camp.status.text()


def test_values_with_decimal_comma():
    assert bt.parse_values("0,5 1,5") == [0.5, 1.5]
    assert bt.parse_values("0,5;1,5") == [0.5, 1.5]
    assert bt.parse_values("-1,5 2") == [-1.5, 2.0]
    assert bt.parse_values("50,100,150") == [50.0, 100.0, 150.0]  # varias comas: separan valores


def test_launch_scripts_for_windows(case, tmp_path):
    doc = parse_in_file(case)
    b = bt.make_parallel(doc, case, tmp_path / "par", 2, total_showers=100)
    # penhmain de Linux en Windows: WSL, con una sola cadena de argumentos ya entrecomillada.
    b.exe_system, b.exe_name = "linux", "penhmain"
    ps = bt.launch_ps1(b, 2)
    assert ("Start-Process -FilePath 'wsl.exe' -ArgumentList '-e sh -c \"GFORTRAN_UNBUFFERED_PRECONNECTED=y "
            "./''penhmain'' < ''caja.in'' > penhmain.out 2>&1\"' -WorkingDirectory $dir") in ps
    assert "wsl.exe --cd $PSScriptRoot -e sh combinar.sh $Sum" in bt.combine_scripts(b)["combinar.ps1"]
    # penhmain de Windows: cmd.
    b.exe_system, b.exe_name = "windows", "penhmain.exe"
    ps = bt.launch_ps1(b, 2)
    # cmd /c quita la primera y la última comilla: todo va entre un par más (vale con espacios y paréntesis).
    assert "-ArgumentList '/c \"\"penhmain.exe\" < \"caja.in\" > penhmain.out 2>&1\"'" in ps
    assert "$env:GFORTRAN_UNBUFFERED_PRECONNECTED = 'y'" in ps
    b.exe_name = "penhmain (1).exe"
    assert "-ArgumentList '/c \"\"penhmain (1).exe\" < \"caja.in\" > penhmain.out 2>&1\"'" in bt.launch_ps1(b, 2)
    b.exe_name = "penhmain.exe"
    comb = bt.combine_scripts(b)
    assert "Test-Path -LiteralPath $Sum" in comb["combinar.ps1"] and '"$PWD/$SUM"' in comb["combinar.sh"]
    # PowerShell 5.1 lee como ANSI un .ps1 sin BOM: UTF-8 con BOM y fin de línea de Windows.
    data = bt._ps1_bytes("# «ejecución»\nWrite-Host 'á'\n")
    assert data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    bt.write_batch(b, jobs=2)
    for name in ("lanzar.ps1", "combinar.ps1"):
        raw = (tmp_path / "par" / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf") and b"\n" not in raw.replace(b"\r\n", b""), name


def test_batch_dialog_can_prepare_again_in_another_folder(qtbot, case, tmp_path):
    from argus.ui.batch_dialog import BatchDialog
    from argus.ui.settings import app_settings

    dlg = BatchDialog(None, case, parse_in_file(case), app_settings(), "paralelo")
    qtbot.addWidget(dlg)
    dlg.nruns.setValue(2)
    dlg.exe.setText(str(tmp_path / "no-esta" / "penhmain"))
    assert "No se encuentra" in dlg.status.text()  # se avisa al escribirlo
    dlg.exe.setText("")
    dlg.dest.setText(str(tmp_path / "p1"))
    dlg.prepare()
    qtbot.waitUntil(lambda: dlg.written is not None, timeout=30000)
    assert not dlg.go.isEnabled()  # la carpeta ya está preparada
    dlg.dest.setText(str(tmp_path / "p2"))  # otra: se puede volver a preparar
    assert dlg.go.isEnabled() and dlg.preview.count() == 2
