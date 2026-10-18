"""Análisis de los resultados: masas y dosis por cuerpo, histogramas dosis-volumen y espectrometría."""

from __future__ import annotations

import math

import numpy as np
import pytest

from argus.results import dvh, spectro
from argus.results.bodies import body_doses, ct_mass, material_densities, sample_volumes
from argus.results.case import is_current, load_case_geometry
from argus.results.data import Curves, Series
from argus.results.display import DisplayOptions, show_curves
from argus.results.materialdata import read_material_dat
from argus.results.metrics import compute, guess_preset
from argus.results.readers import read_output
from argus.results.summary import read_summary
from argus.results.units import Normalization
from conftest import CORPUS

RES = CORPUS / "resultados"


@pytest.fixture(scope="module")
def caja():
    return load_case_geometry(RES / "caja" / "caja.in")


@pytest.fixture(scope="module")
def tac():
    return load_case_geometry(RES / "tac" / "tac.in")


def test_case_geometry_names_and_materials(caja):
    assert caja.ok and is_current(caja)
    assert [caja.body_name(k) for k in (1, 2, 3)] == ["SLAB", "PLAN", "DETE"]
    assert caja.material_name(caja.body_material(3)) == "2 · hueso"
    assert load_case_geometry(RES / "no-existe.in").problem


def test_body_volumes_masses_and_doses(caja):
    v = sample_volumes(caja.session, n=2_000_000)
    exact = {1: math.pi * 100 * 5, 2: math.pi * 100 * 0.2, 3: math.pi * 4 * 2}
    for kb, ve in exact.items():
        assert abs(v.volume[kb] - ve) < 4 * v.error[kb] + 1e-9, kb
    assert not ({1, 2, 3} & v.unbounded)
    dens = material_densities(read_material_dat(RES / "caja" / "material.dat"), caja)
    assert dens == {1: pytest.approx(0.99821), 2: pytest.approx(1.85)}
    s = read_summary(RES / "caja" / "penhmain-res.dat")
    table = {b.kb: b for b in body_doses(caja, v, dens, s)}
    d = table[3]
    assert d.mass == pytest.approx(v.volume[3] * 1.85)
    assert d.dose == pytest.approx(s.body_energy[3].value / d.mass)
    assert d.dose_err / d.dose == pytest.approx(math.hypot(s.body_energy[3].err / s.body_energy[3].value,
                                                           d.mass_err / d.mass), rel=1e-9)


def test_unbounded_bodies_have_no_mass(tmp_path):
    geo = tmp_path / "semi.geo"
    geo.write_text("""Semiespacio de agua
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (  P0)   Plano z=0
INDICES=( 0, 0, 0, 1, 0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (  C1)   Cilindro r=1
INDICES=( 1, 1, 0, 0,-1)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (  P1)   Plano z=-1
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(-1.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (CUBO)   Cilindro finito
MATERIAL(   1)
SURFACE (  C1), SIDE POINTER=(-1)
SURFACE (  P0), SIDE POINTER=(-1)
SURFACE (  P1), SIDE POINTER=(+1)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (SEMI)   Semiespacio z>0
MATERIAL(   1)
SURFACE (  P0), SIDE POINTER=(+1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
""", "latin-1")
    inp = tmp_path / "semi.in"
    inp.write_text("TITLE  semi\nGEOMFN semi.geo\nMFNAME agua.mat\nMSIMPA 1e3 1e3 1e3 0.1 0.1 1e3 1e3\nEND\n",
                   "latin-1")
    case = load_case_geometry(inp)
    assert case.ok, case.problem
    v = sample_volumes(case.session, n=400_000)
    kb_semi = next(k for k in (1, 2) if case.body_name(k) == "SEMI")
    kb_cyl = 3 - kb_semi
    assert kb_semi in v.unbounded and kb_cyl not in v.unbounded
    assert v.volume[kb_cyl] == pytest.approx(math.pi, rel=0.03)
    bd = {b.kb: b for b in body_doses(case, v, {1: 1.0})}
    assert bd[kb_semi].mass is None and "más allá" in bd[kb_semi].note


def test_ct_mass_and_tissue_dvh(tac):
    ct = tac.geo.ct
    dv = float(np.prod(ct.voxel_size))
    assert ct_mass(ct) == pytest.approx(float(ct.density[ct.material > 0].sum()) * dv)
    g = read_output(RES / "tac" / "ct-3d-dose.dat")
    st = dvh.ct_structures(g, ct, None, {1: "agua", 2: "hueso"})
    assert {s.name for s in st} == {"agua", "hueso"}
    assert sum(s.volume for s in st) == pytest.approx(576.0)
    # El TAC entero, como el DVH de penhmain: la fracción del volumen con dosis ≥ el borde inferior de cada
    # intervalo (penhmain no cuenta el vóxel de dosis máxima, que cae fuera de su último intervalo: un vóxel menos).
    whole = dvh.whole_ct(g, ct)
    ref = read_output(RES / "tac" / "ct-dvh.dat")
    w = ref.x[1] - ref.x[0]
    lower = np.round((ref.x - 0.5 * w) / w) * w  # bordes inferiores (el primero, 0 exacto)
    mine = dvh.cumulative(whole.doses, whole.volumes, lower) / 100.0
    np.testing.assert_allclose(mine - 1.0 / 576, ref.get("acumulado").y, atol=0.5 / 576)


def test_box_dvh_by_body_splits_partial_voxels(caja):
    v = np.ones((4, 4, 4))
    from argus.results.data import Grid

    # Rejilla de z = 3 a 7: dos capas en la losa (hasta z = 5), una vacía y otra que corta el detector plano (de
    # z = 6 a 6,2: una quinta parte de cada vóxel).
    edges = (np.linspace(-2, 2, 5), np.linspace(-2, 2, 5), np.array([3.0, 4.0, 5.0, 6.0, 7.0]))
    g = Grid("3d-dose-map", "t", "box", ("x", "y", "z"), edges, v * np.arange(1, 5)[None, None, :],
             np.zeros_like(v), "Dosis", "eV/g")
    st = {s.name: s for s in dvh.box_structures(g, caja.session, caja)}
    assert set(st) == {"SLAB", "PLAN"}
    assert st["SLAB"].volume == pytest.approx(32.0)
    assert st["PLAN"].volume == pytest.approx(16 * 0.2, rel=0.3)
    c = dvh.dvh_curves(list(st.values()), "dvh-bodies", "DVH")
    s = dvh.stats(st["SLAB"])
    assert s["mean"] == pytest.approx(1.5) and s["D50"] == 2.0 and s["max"] == 2.0
    assert dvh.stats(st["PLAN"])["mean"] == pytest.approx(4.0)
    assert c.series[0].y[0] == pytest.approx(100.0)


def test_dvh_metrics_and_units():
    doses = np.linspace(0, 10, 1001)
    st = dvh.Structure("uniforme", doses, np.ones_like(doses))
    c = dvh.dvh_curves([st], "dvh-bodies", "DVH", nbins=1000)
    assert guess_preset(c.kind) == "dvh" and guess_preset("ct-dvh") == "dvh"
    m = {x.key: x.value for x in compute("dvh", c.x, c.series[0].y)}
    assert m["D50"] == pytest.approx(5.0, abs=0.02) and m["D98"] == pytest.approx(0.2, abs=0.02)
    assert m["Dmean"] == pytest.approx(5.0, abs=0.02)
    # Con N partículas cambia la dosis del eje x; el volumen sigue en %.
    opts = DisplayOptions(dose_unit="eV/g", norm=Normalization("count", count=1e9))
    sc = show_curves(c, opts)
    assert sc.series[0].y[0] == pytest.approx(100.0)
    raw = show_curves(c, DisplayOptions(dose_unit="eV/g"))
    assert sc.series[0].x[-1] / raw.series[0].x[-1] == pytest.approx(1e9, rel=1e-6)


def test_detector_resolution_like_mcdet():
    edges = np.arange(0.0, 800e3 + 1, 1e3)
    y = np.zeros(len(edges) - 1)
    y[662] = 1e-3  # probabilidad 1 en [662, 663) keV
    _name, fwhm, e0 = spectro.DETECTORS["nai"]
    c = spectro.convolve(edges, y, None, fwhm, e0)
    w = np.diff(c.edges)
    area = float((c.y * w).sum())
    assert area == pytest.approx(1.0, rel=1e-9)
    mean = float((c.x * c.y * w).sum())
    sd = math.sqrt(float(((c.x - mean) ** 2 * c.y * w).sum()))
    # σ² = (0,4246609·(FWHM − ΔE))² + ΔE²/12 (la gaussiana de mcdet y el reparto uniforme en el intervalo)
    s_expected = math.hypot(0.4246609 * (float(fwhm(np.array([662.5e3]))[0]) - 1e3), 1e3 / math.sqrt(12))
    assert mean == pytest.approx(662.5e3, rel=1e-6)
    assert sd == pytest.approx(s_expected, rel=0.01)
    # PIPS: la cola exponencial hacia energías bajas baja la media en E0.
    edges = np.arange(4e6, 6e6 + 1, 1e3)
    y = np.zeros(len(edges) - 1)
    y[1500] = 1e-3
    _n, fw, e0 = spectro.DETECTORS["pips"]
    c = spectro.convolve(edges, y, None, fw, e0)
    w = np.diff(c.edges)
    assert float((c.y * w).sum()) == pytest.approx(1.0, rel=1e-6)
    assert float((c.x * c.y * w).sum()) == pytest.approx(5.5005e6 - 5.5e3, rel=2e-6)


def test_energy_windows_and_conversion():
    edges = np.linspace(0, 100, 101)
    x = 0.5 * (edges[:-1] + edges[1:])
    bg = 0.002 + 0.0001 * x  # fondo lineal (por unidad de energía)
    peak = 0.5 * np.exp(-0.5 * ((x - 50.0) / 2.0) ** 2) / (2.0 * math.sqrt(2 * math.pi))
    y = bg + peak
    wdw = spectro.window(edges, y, np.full_like(y, 1e-5), 40.0, 60.0)
    assert wdw.gross == pytest.approx(float((y * np.diff(edges))[40:60].sum()))
    assert wdw.net == pytest.approx(0.5, rel=0.01)
    assert wdw.centroid == pytest.approx(50.0, abs=0.05)
    assert wdw.fwhm == pytest.approx(2.3548 * 2.0, abs=1.0)
    assert wdw.net_err > 0
    # ∫ y·c dE con c = E (lineal: exacto en el centro de cada intervalo)
    val, sig, out = spectro.conversion(edges, np.ones(100) * 0.01, None, np.array([0.0, 100.0]),
                                       np.array([0.0, 100.0]), loglog=False)
    assert val == pytest.approx(0.01 * 100 ** 2 / 2) and out == 0.0 and sig == 0.0
    val, _s, out = spectro.conversion(edges, np.ones(100) * 0.01, None, np.array([10.0, 50.0]),
                                      np.array([1.0, 1.0]))
    assert val == pytest.approx(0.4) and out == pytest.approx(0.6)


def test_spectrum_series_and_measured_units():
    c = Curves("spc-impdet", "e", np.array([1.0, 2.0, 3.0]), "E", "eV", "p", "1/(eV·partícula)",
               [Series("total", np.array([1.0, 2.0, 1.0]))])
    assert c.get("total") is not None


def _geo_case(tmp_path, geo_text: str, name: str = "g"):
    (tmp_path / f"{name}.geo").write_text(geo_text, "latin-1")
    (tmp_path / f"{name}.in").write_text(f"TITLE  {name}\nGEOMFN {name}.geo\nMFNAME agua.mat\n"
                                         "MSIMPA 1e3 1e3 1e3 0.1 0.1 1e3 1e3\nEND\n", "latin-1")
    case = load_case_geometry(tmp_path / f"{name}.in")
    assert case.ok, case.problem
    return case


SEP = "0" * 64


def _surf(label, indices, scale=None, shift=None):
    lines = [SEP, f"SURFACE ({label:>4})", f"INDICES=({indices})"]
    for k, v in (scale or {}).items():
        lines.append(f"{k}-SCALE=({v:+.15E},   0)")
    for k, v in (shift or {}).items():
        lines.append(f"{k}-SHIFT=({v:+.15E},   0)")
    return lines


def test_small_bodies_are_measured_in_their_own_box(tmp_path):
    # Un dosímetro de 0,003 cm³ (cilindro r = 0,05 cm, h = 0,4 cm) dentro de un maniquí de 30 cm.
    g = ["Maniqui con un TLD"]
    g += _surf("CM", " 1, 1, 0, 0,-1", {"X": 15.0, "Y": 15.0})
    g += _surf("ZM0", " 0, 0, 0, 1, 0", shift={"Z": -15.0}) + _surf("ZM1", " 0, 0, 0, 1, 0", shift={"Z": 15.0})
    g += _surf("CT", " 1, 1, 0, 0,-1", {"X": 0.05, "Y": 0.05})
    g += _surf("ZT0", " 0, 0, 0, 1, 0", shift={"Z": -0.2}) + _surf("ZT1", " 0, 0, 0, 1, 0", shift={"Z": 0.2})
    g += [SEP, "BODY    ( TLD)", "MATERIAL(   1)", "SURFACE (  CT), SIDE POINTER=(-1)",
          "SURFACE ( ZT0), SIDE POINTER=(+1)", "SURFACE ( ZT1), SIDE POINTER=(-1)"]
    g += [SEP, "BODY    (MANI)", "MATERIAL(   1)", "SURFACE (  CM), SIDE POINTER=(-1)",
          "SURFACE ( ZM0), SIDE POINTER=(+1)", "SURFACE ( ZM1), SIDE POINTER=(-1)", "BODY    ( TLD)"]
    g += [SEP, "END      " + "0" * 55, ""]
    case = _geo_case(tmp_path, "\n".join(g))
    v = sample_volumes(case.session, n=1_000_000)
    kb = next(k for k in (1, 2) if case.body_name(k) == "TLD")
    exact = math.pi * 0.05 ** 2 * 0.4
    assert v.volume[kb] == pytest.approx(exact, rel=0.05)
    assert v.error[kb] < 0.03 * exact


def test_thin_scenes_are_not_unbounded(tmp_path):
    # Un disco de 1 cm de radio y 0,05 cm de espesor, solo en la escena.
    g = ["Disco fino"]
    g += _surf("C1", " 1, 1, 0, 0,-1", {"X": 1.0, "Y": 1.0})
    g += _surf("Z0", " 0, 0, 0, 1, 0") + _surf("Z1", " 0, 0, 0, 1, 0", shift={"Z": 0.05})
    g += [SEP, "BODY    (DISC)", "MATERIAL(   1)", "SURFACE (  C1), SIDE POINTER=(-1)",
          "SURFACE (  Z0), SIDE POINTER=(+1)", "SURFACE (  Z1), SIDE POINTER=(-1)"]
    g += [SEP, "END      " + "0" * 55, ""]
    case = _geo_case(tmp_path, "\n".join(g), "disco")
    v = sample_volumes(case.session, n=1_000_000)
    assert 1 not in v.unbounded
    assert v.volume[1] == pytest.approx(math.pi * 0.05, rel=0.02)


def test_parabola_fit_far_from_the_origin():
    from argus.results import fit as ft

    x = np.arange(1000.0, 1011.0)
    y = 3.0 - 2e-3 * x + 5e-6 * x ** 2
    r = ft.fit(x, y, None, "parabola")
    np.testing.assert_allclose(r.params, [3.0, -2e-3, 5e-6], rtol=1e-6)
    assert np.max(np.abs(r.predict(x) - y) / np.abs(y)) < 1e-12 and r.ndf == 8
    # Tres puntos sin incertidumbres: la parábola pasa por ellos, pero sin incertidumbre de los parámetros.
    r3 = ft.fit(np.array([1.0, 2.0, 3.0]), np.array([1.0, 4.0, 9.0]), None, "parabola")
    np.testing.assert_allclose(r3.predict(np.array([4.0])), [16.0])
    assert r3.ndf == 0 and np.isnan(r3.errors).all()


def test_units_fixed_for_curves_drawn_together():
    from argus.results.display import resolve_units, strip_per_particle

    assert strip_per_particle("1/(keV·partícula)") == "1/keV"
    assert strip_per_particle("eV/g/partícula") == "eV/g"
    assert strip_per_particle("1/(eV·sr·desintegración)") == "1/(eV·sr)"
    x = np.linspace(1e4, 2e6, 200)
    big = Curves("spectrum", "a", x, "E", "eV", "p", "1/(eV·partícula)", [Series("e", np.exp(-x / 5e5), None)],
                 histogram=True)
    small = Curves("spectrum", "b", x / 100, "E", "eV", "p", "1/(eV·partícula)", [Series("e", np.exp(-x / 5e5),
                                                                                         None)], histogram=True)
    opts = resolve_units(big, DisplayOptions())
    assert opts.energy_unit == "MeV"
    assert show_curves(small, DisplayOptions()).x_unit == "keV"  # sola, en su escala
    assert show_curves(small, opts).x_unit == "MeV"  # encima de la otra, en la misma


def test_detector_resolution_on_logarithmic_bins():
    edges = np.geomspace(1e4, 2e6, 301)
    w = np.diff(edges)
    y = np.zeros(300)
    j = 250
    y[j] = 1.0 / w[j]  # todo en un intervalo ancho (los logarítmicos crecen con la energía)
    fwhm = spectro.DETECTORS["nai"][1]
    cv = spectro.convolve(edges, y, None, fwhm)
    p = cv.y * np.diff(cv.edges)
    assert p.sum() == pytest.approx(1.0, abs=1e-9)
    c = 0.5 * (cv.edges[:-1] + cv.edges[1:])
    m = (c * p).sum()
    sd = math.sqrt(((c - m) ** 2 * p).sum())
    # La corrección de mcdet con la anchura de ESE intervalo: σ = 0,4246609·(FWHM − ΔE); más la del intervalo de
    # entrada y la de los canales (uniformes, ΔE²/12 cada una).
    ec = 0.5 * (edges[j] + edges[j + 1])
    sigma = 0.4246609 * (float(fwhm(np.array([ec]))[0]) - w[j])
    assert sd == pytest.approx(math.sqrt(sigma ** 2 + 2 * w[j] ** 2 / 12), rel=0.01)
    with pytest.raises(ValueError, match="canales iguales"):
        spectro.convolve(edges, y, None, fwhm, e0=5500.0)
    tail = spectro.convolve(edges, y, None, fwhm, e0=5500.0, channel=2000.0)
    assert (tail.y * np.diff(tail.edges)).sum() == pytest.approx(1.0, abs=1e-9)


def test_files_without_data_yet_are_incomplete(tmp_path):
    from argus.results.readers import IncompleteFile

    for name in ("depth-dose.dat", "spc-impdet-01.dat", "3d-dose-map.dat", "angle.dat"):
        src = RES / "caja" / name
        head = [ln for ln in src.read_text("latin-1").splitlines() if ln.lstrip().startswith("#") or not ln.strip()]
        (tmp_path / name).write_text("\n".join(head) + "\n", "latin-1")
        with pytest.raises(IncompleteFile):
            read_output(tmp_path / name)
    # Un espacio de fases sin partículas sí es un resultado (vacío).
    src = RES / "caja" / "psf-impdet.dat"
    head = [ln for ln in src.read_text("latin-1").splitlines() if ln.lstrip().startswith("#")]
    (tmp_path / "psf-impdet.dat").write_text("\n".join(head) + "\n", "latin-1")
    assert read_output(tmp_path / "psf-impdet.dat").count == 0


def test_track_drawing_budget_is_shared_between_particles():
    from argus.results.data import Tracks, fair_shares

    assert fair_shares([10, 1000, 5], 100) == [10, 85, 5]  # lo que no usan los pequeños, para el grande
    assert fair_shares([50, 50], 60) == [30, 30] and fair_shares([], 5) == []
    t = Tracks(1, np.zeros((12, 4)), np.array([0, 3, 4, 9, 12]))  # trayectorias de 3, 1, 5 y 3 puntos
    assert t.ranges(8) == [(0, 3)]  # enteras (la de un punto no se dibuja)
    assert t.ranges(100) == [(0, 3), (4, 9), (9, 12)]
    assert t.ranges(2) == [(0, 2)]  # la primera, recortada, si ni ella cabe
