"""Resultados de penhmain (ADR-014): reconocer, leer, resumir, combinar y estado de la carpeta.

El corpus (tests/corpus/resultados) son salidas reales de penhmain 2025 de casos pequeños; «paralelo» tiene tres
ejecuciones con semillas RAND0 distintas y su suma con penhmain-sum, que sirve de referencia para combinar.
"""

import os
import shutil
import time

import numpy as np
import pytest

from argus.results import formats as fm
from argus.results.catalog import KINDS, identify, identify_text
from argus.results.combine import Incompatible, combine, combine_summaries, pooled
from argus.results.data import Curves, Grid, PhaseSpace, TextOutput, Tracks
from argus.results.folder import ResultsFolder, log_error
from argus.results.materialdata import MaterialData, csda_range, energy_for_range, read_material_dat
from argus.results.readers import IncompleteFile, read_output
from argus.results.summary import read_summary
from argus.results.units import Normalization, auto_dose_unit, auto_energy_unit
from conftest import CORPUS

RES = CORPUS / "resultados"
CASES = ["caja", "cilindro", "esfera", "tac", "paralelo/run-01", "paralelo/suma"]


def _all_outputs():
    for case in CASES:
        for p in sorted((RES / case).iterdir()):
            ident = identify(p)
            if ident is not None:
                yield case, p, ident


def test_every_output_of_the_corpus_is_recognised_and_read():
    seen = set()
    for _case, p, ident in _all_outputs():
        data = read_output(p, ident)
        seen.add(ident.kind)
        assert data is not None, p
    expected = {"res", "log", "run-log", "geometry-rep", "depth-dose", "x-dose", "y-dose", "z-dose", "3d-dose-map",
                "2d-dose-map", "radial-dose", "gnu-dose", "ct-3d-dose", "ct-ddose", "ct-dvh", "ct-x-dose",
                "ct-y-dose", "ct-z-dose", "gnu-ct-dose", "energy-up", "energy-down", "angle", "polar-angle",
                "spc-impdet", "fln-impdet", "age-impdet", "spc-angdet", "spc-enddet", "psf", "psource", "usource",
                "p3d-depth", "p3d-x", "p3d-y", "p3d-z", "p3d-map", "p3d-map-cyl", "p3d-sphere", "trk", "material",
                "energy-equivalence", "pennuc"}
    assert expected <= seen, expected - seen
    assert set(KINDS) >= seen


def test_inputs_and_geometries_are_not_results():
    for name in ("caja/caja.in", "caja/caja.geo", "caja/mcs-null.mcs", "tac/tac.ct", "LEEME.txt"):
        assert identify(RES / name) is None


def test_renamed_outputs_are_recognised_by_content(tmp_path):
    shutil.copy(RES / "cilindro/depth-dose.dat", tmp_path / "E100_chi40_depth-dose.dat")
    shutil.copy(RES / "caja/spc-impdet-02.dat", tmp_path / "detector-plano.dat")
    shutil.copy(RES / "caja/electron.trk", tmp_path / "trazas.trk")
    f = ResultsFolder(tmp_path)
    kinds = {o.path.name: (o.kind, o.renamed, o.ident.detector, o.ident.particle) for o in f.files}
    assert kinds["E100_chi40_depth-dose.dat"] == ("depth-dose", True, None, None)
    assert kinds["detector-plano.dat"] == ("spc-impdet", True, 2, None)  # el número sale de la cabecera
    assert kinds["trazas.trk"][0] == "trk" and kinds["trazas.trk"][3] is None  # el tipo de partícula no se sabe


def test_penmain_and_penhmain_sum_headers():
    head = " #  Results from PENMAIN.\n #  Dose distribution along the central Z axis.\n"
    assert identify_text(head).kind == "z-dose"
    assert identify(RES / "paralelo/suma/sum.log").variant == "sum"


def test_fortran_numbers():
    assert fm.fix_fortran_numbers(" 1.234567-100 2.0D-05 -3.1+101") == " 1.234567E-100 2.0E-05 -3.1E+101"
    t = fm.load_table_text(" 1.0-100 2.0\n # c\n\n 3.0 4.0D-2\n")
    assert t.shape == (2, 2) and t[0, 0] == pytest.approx(1e-100) and t[1, 1] == pytest.approx(0.04)
    edges, log = fm.centers_to_edges(np.array([1.5, 3.0, 6.0, 12.0]))
    assert log and np.allclose(edges, [1, 2, 4, 8, 16])
    edges, log = fm.centers_to_edges(np.array([0.1, 0.3, 0.5]))
    assert not log and np.allclose(edges, [0, 0.2, 0.4, 0.6])


def test_depth_dose_and_units():
    c = read_output(RES / "cilindro/depth-dose.dat")
    assert isinstance(c, Curves) and c.x_unit == "cm" and c.y_unit == "eV·cm²/g"
    assert len(c.x) == 30 and np.allclose(c.edges[[0, -1]], [0.0, 3.0])
    s = c.series[0]
    raw = fm.load_table(RES / "cilindro/depth-dose.dat")
    assert np.allclose(s.y, raw[:, 1]) and np.allclose(s.err, raw[:, 2] / 3.0)  # 1 sigma


def test_3d_dose_map_is_placed_by_its_indices():
    g = read_output(RES / "caja/3d-dose-map.dat")
    assert isinstance(g, Grid) and g.geometry == "box" and g.shape == (11, 11, 25)
    assert np.allclose(g.edges[0][[0, -1]], [-5, 5]) and np.allclose(g.edges[2][[0, -1]], [0, 5])
    raw = fm.load_table(RES / "caja/3d-dose-map.dat")
    row = raw[137]
    i, j, k = (int(v) - 1 for v in row[7:10])
    assert g.value[i, j, k] == pytest.approx(row[3] if row[3] > 1e-35 else 0.0)
    assert g.err[i, j, k] == pytest.approx(row[4] / 3.0)
    assert "Energía depositada" in g.extra
    # z-dose.dat son los vóxeles centrales de la caja (media ponderada por la masa de 3x3 vóxeles, agua uniforme)
    z = read_output(RES / "caja/z-dose.dat").series[0].y
    assert np.allclose(z, g.value[4:7, 4:7, :].mean(axis=(0, 1)), rtol=2e-5)


def test_cylinder_sphere_and_ct_maps():
    g = read_output(RES / "cilindro/2d-dose-map.dat")
    assert g.geometry == "cylinder" and g.axes == ("r", "z") and g.shape == (10, 30)
    raw = fm.load_table(RES / "cilindro/2d-dose-map.dat")
    assert g.value[0, 1] == pytest.approx(raw[1, 2]) and g.value[1, 0] == pytest.approx(raw[30, 2])
    vol = g.voxel_volumes()
    assert vol.sum() == pytest.approx(np.pi * 25 * 3)
    r = read_output(RES / "esfera/radial-dose.dat")
    assert r.geometry == "sphere" and r.shape == (25,) and r.voxel_volumes().sum() == pytest.approx(4 / 3 * np.pi * 125)
    ct = read_output(RES / "tac/ct-3d-dose.dat")
    assert ct.geometry == "ct" and ct.shape == (8, 6, 12) and ct.meta["ct_local"]
    assert np.allclose(ct.edges[1][[0, -1]], [0, 6])
    dvh = read_output(RES / "tac/ct-dvh.dat")
    assert dvh.get("acumulado").y[0] <= 1.0 and dvh.meta["max_dose"] > 0


def test_detectors_and_emerging_particles():
    spc = read_output(RES / "caja/spc-impdet-02.dat")
    assert spc.log_x and spc.meta["detector"] == 2 and [s.name for s in spc.nonempty()][0] == "total"
    fln = read_output(RES / "caja/fln-impdet-02.dat")
    assert fln.y_unit == "cm/(eV·partícula)"
    ang = read_output(RES / "caja/spc-angdet-02.dat")
    assert [s.particle for s in ang.series] == [2]  # ADKPAR 2: solo fotones
    ed = read_output(RES / "caja/spc-enddet-01.dat")
    area = float(np.sum(ed.series[0].y * np.diff(ed.edges)))
    assert area == pytest.approx(read_summary(RES / "caja/penhmain-res.dat").enddet_area[1].value, rel=1e-4)
    a = read_output(RES / "caja/angle.dat")
    assert a.geometry == "angles" and a.shape == (30, 4) and a.meta["main"] == "fotones"
    pa = read_output(RES / "caja/polar-angle-ph.dat")
    assert pa.get("energía media").weight_of == "fotones" and pa.get("energía media").unit == "eV"


def test_source_spectra():
    lines = read_output(RES / "caja/psource.dat")
    assert lines.discrete and np.allclose(lines.x, [3.2e4, 6.617e5])
    assert lines.get("definido").y.sum() == pytest.approx(1.0, rel=1e-5)
    hist = read_output(RES / "cilindro/psource.dat")
    assert np.allclose(hist.edges, [4.8e7, 4.9e7, 5.0e7]) and hist.get("definido").y[1] == pytest.approx(6e-7)
    u = read_output(RES / "esfera/usource.dat")
    assert u.y_unit == "1/(eV·desintegración)" and u.edges[0] == 0.0


def test_eprof_maps_fluence_and_tracks_and_psf():
    assert read_output(RES / "caja/P3D-map.dat").unit == "1/cm²"
    assert read_output(RES / "cilindro/P3D-map-cyl.dat").geometry == "cylinder"
    tr = read_output(RES / "caja/electron.trk")
    assert isinstance(tr, Tracks) and tr.particle == 1 and tr.count > 1
    assert tr.starts[-1] == len(tr.points)
    a, b = tr.segments()
    assert len(a) == len(tr.points) - tr.count  # ningún tramo une dos trayectorias
    psf = read_output(RES / "caja/psf-impdet.dat")
    assert isinstance(psf, PhaseSpace) and psf.count == 399 and set(np.unique(psf.kpar)) <= {1, 2, 3}
    assert np.allclose(np.linalg.norm(psf.dir, axis=1), 1.0, atol=1e-4)
    assert isinstance(read_output(RES / "caja/penhmain.dat"), TextOutput)


def test_summary():
    s = read_summary(RES / "caja/penhmain-res.dat")
    assert s.complete and s.kparp == 2 and s.showers == 2e4 and s.title == "ARGUS corpus: caja"
    assert s.fractions["up"].value == pytest.approx(0.78035) and s.fractions["up"].err == pytest.approx(9.5e-3 / 3)
    assert s.body_energy[1].value == pytest.approx(9.877665e4) and s.body_energy[1].effic == pytest.approx(3.04e3)
    assert set(s.body_energy) == {1, 2, 3, 4}  # el 4 es la envolvente que añade PENGEOM
    assert s.impact_area[2].value == pytest.approx(0.6515) and s.enddet_energy[1].value == pytest.approx(45298.6)
    assert s.events["abs"]["Photoelectric absorption"].value == pytest.approx(1.0)
    assert s.secondaries[(1, "abs")].value == pytest.approx(1.40635)
    assert s.dose_uncertainty_pct == pytest.approx(8.875862) and s.last_seeds == (923293763, 869407497)
    e = read_summary(RES / "esfera/penhmain-res.dat")
    assert e.per_decay and e.primary_name.startswith("desintegraciones") and not e.fractions


def test_combining_runs_reproduces_penhmain_sum():
    runs = [RES / f"paralelo/run-0{i}" for i in (1, 2, 3)]
    ns = [read_summary(r / "penhmain-res.dat").showers for r in runs]
    checked = 0
    for p in sorted((RES / "paralelo/suma").iterdir()):
        ident = identify(p)
        if ident is None or ident.kind in ("run-log", "gnu-dose", "res", "psource"):
            continue
        ref = read_output(p, ident)
        comb = combine([(n, read_output(r / p.name)) for n, r in zip(ns, runs)])
        if isinstance(ref, Curves):
            pairs = [(s, comb.get(s.name)) for s in ref.series]
        else:
            pairs = [(ref, comb)]
        for a, b in pairs:
            ya, yb = (a.y, b.y) if isinstance(ref, Curves) else (a.value, b.value)
            ok = np.abs(ya) > 1e-30
            if not ok.any():
                continue
            assert np.allclose(yb[ok], ya[ok], rtol=2e-6), p.name
            if a.err is not None:
                big = ok & (a.err > 1e-6 * np.abs(ya))
                assert np.allclose(b.err[big], a.err[big], rtol=0.02), p.name  # 2 cifras en los ficheros
            checked += 1
    assert checked >= 25
    s = combine_summaries([read_summary(r / "penhmain-res.dat") for r in runs])
    ref = read_summary(RES / "paralelo/suma/penhmain-res.dat")
    assert s.showers == ref.showers
    for kb in (1, 2, 3):
        assert s.body_energy[kb].value == pytest.approx(ref.body_energy[kb].value, rel=1e-6)
        assert s.body_energy[kb].err == pytest.approx(ref.body_energy[kb].err, rel=0.05)


def test_pooled_is_a_single_simulation():
    rng = np.random.default_rng(3)
    x = rng.exponential(2.0, size=(3, 5000))  # contribuciones por lluvia de tres ejecuciones
    means = [a.mean() for a in x]
    sig = [np.sqrt((np.mean(a * a) - a.mean() ** 2) / len(a)) for a in x]
    m, e = pooled([np.array(v) for v in means], [np.array(v) for v in sig], [5000] * 3)
    allx = x.ravel()
    assert float(m) == pytest.approx(allx.mean())
    assert float(e) == pytest.approx(np.sqrt((np.mean(allx * allx) - allx.mean() ** 2) / allx.size))


def test_incompatible_runs_are_refused():
    a = read_output(RES / "caja/depth-dose.dat")
    b = read_output(RES / "cilindro/depth-dose.dat")
    with pytest.raises(Incompatible):
        combine([(1.0, a), (1.0, b)])
    with pytest.raises(Incompatible):
        combine([(1.0, read_output(RES / "tac/ct-dvh.dat"))])


def test_incomplete_map_while_penhmain_writes(tmp_path):
    src = (RES / "caja/3d-dose-map.dat").read_text()
    (tmp_path / "3d-dose-map.dat").write_text(src[: len(src) // 2])
    with pytest.raises(IncompleteFile):
        read_output(tmp_path / "3d-dose-map.dat")


def test_material_tables():
    m = read_material_dat(RES / "cilindro/material.dat")
    assert isinstance(m, MaterialData) and m.has_tables
    w = m.materials[0]
    assert w.file == "water.mat" and w.density == pytest.approx(0.99821)
    sp = w.table("Stopping powers for protons")
    assert sp.columns[:4] == ["Energy", "Scol", "SBethe", "range"] and sp.units[3] == "mtu"
    r = csda_range(sp, 50e6, w.density)
    assert r == pytest.approx(2.22, abs=0.01)  # alcance CSDA de 50 MeV en agua
    assert energy_for_range(sp, r, w.density) == pytest.approx(50e6, rel=1e-6)
    el = w.table("Stopping powers for electrons")
    assert el.columns[4] == "Rad. Yield"
    two = read_material_dat(RES / "caja/material.dat")
    assert [x.file for x in two.materials] == ["water.mat", "hueso.mat"] and not two.has_tables


def test_folder_input_runs_and_state(tmp_path):
    f = ResultsFolder(RES / "caja")
    assert f.input.path.name == "caja.in" and f.input.nsimsh == 2e4
    st = f.state()
    assert st.status == "terminada" and st.progress == 1.0
    par = ResultsFolder(RES / "paralelo")
    assert [r.path.name for r in par.runs] == ["run-01", "run-02", "run-03"] and par.is_parallel
    assert ResultsFolder(RES / "paralelo/suma").is_sum
    # Una simulación que sigue: el resumen de ahora mismo con menos lluvias de las pedidas.
    d = tmp_path / "caso"
    d.mkdir()
    shutil.copy(RES / "caja/caja.in", d / "caja.in")
    txt = (RES / "caja/penhmain-res.dat").read_text().replace("2.000000E+04", "5.000000E+03", 1)
    (d / "penhmain-res.dat").write_text(txt)
    (d / "caja.in").write_text((d / "caja.in").read_text().replace("NSIMSH 2e4", "NSIMSH 2e7"))
    st = ResultsFolder(d).state()
    assert st.status == "simulando" and st.progress == pytest.approx(5e3 / 2e7) and st.eta > 0
    old = time.time() - 3600
    os.utime(d / "penhmain-res.dat", (old, old))
    assert ResultsFolder(d).state().status == "parada"
    (d / "run.log").write_text("   Initialising PENGEOM ...\nAt line 4801 of file penelope.f (unit = 3)\n"
                               "Fortran runtime error: End of file\n")
    st = ResultsFolder(d).state()
    assert st.status == "error" and "End of file" in st.error


def test_log_errors():
    assert "cannot be defined" in log_error("SENDET: Detector cannot be defined.\nIRETRN = 3130\n")
    assert log_error("   The simulation is started ...\n") is None


def test_units_and_normalization():
    assert auto_dose_unit(1.2e4) == "pGy" and auto_dose_unit(3e7) == "nGy"
    assert auto_energy_unit(6.6e5) == "keV" and auto_energy_unit(5e7) == "MeV"
    x = np.array([0.0, 1.0, 2.0])
    y = np.array([1.0, 4.0, 2.0])
    assert Normalization("max").factor(x, y) == pytest.approx(25.0)
    assert Normalization("point", point=0.5).factor(x, y) == pytest.approx(40.0)
    assert Normalization("area").factor(x, y, np.array([-0.5, 0.5, 1.5, 2.5])) == pytest.approx(1 / 7)
    assert Normalization("activity", activity=1e3, duration=60).factor(x, y) == pytest.approx(6e4)
    assert Normalization("raw").label(per_decay=True) == "por desintegración"
