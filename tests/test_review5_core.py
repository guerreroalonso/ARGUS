"""Quinta revisión (0.12.2): análisis, espectros de la fuente, lotes, comprobaciones del .in y de la geometría."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from argus.penhan import batch as bt
from argus.penhan import spectrum_file as sf
from argus.penhan.inputfile import parse_in_file, parse_in_text, write_in_text
from argus.results import metrics as mt
from argus.results.compare import gamma_index, ratio
from argus.results.measured import position_scale, read_table_text
from conftest import CORPUS

RES = CORPUS / "resultados"


# -- gamma ----------------------------------------------------------------------------------------------------------

def _bragg_like(z):
    return 100.0 / (1.0 + np.exp((z - 7.8) / 0.03)) * (1 + 2 * np.exp(-((z - 7.75) / 0.1) ** 2))


def test_gamma_1d_is_exact_in_a_steep_falloff():
    z = np.arange(0.0, 9.0, 0.0225)
    d = _bragg_like(z)
    for shift in (0.01, 0.015):  # 0,1 y 0,15 mm: γ exacto = desplazamiento / DTA
        res = gamma_index([z], d, [z + shift], d, dose=0.03, dta=0.3, threshold=0.1)
        assert res.passing == 100.0
        assert res.maximum <= shift / 0.3 + 1e-6


def test_gamma_2d_is_exact_in_a_steep_falloff():
    r = np.arange(0.1875, 15.0, 0.375)
    z = np.arange(0.0, 3.5, 0.035)
    R, Z = np.meshgrid(r, z, indexing="ij")
    d = np.exp(-(R / 4.0) ** 2) * 100.0 / (1.0 + np.exp((Z - 2.15) / 0.01))
    res = gamma_index([r, z], d, [r, z + 0.005], d, dose=0.03, dta=0.3, threshold=0.1)
    assert res.passing == 100.0 and res.maximum <= 0.005 / 0.3 + 1e-6
    # Una diferencia de dosis de verdad sigue fallando.
    res = gamma_index([r, z], d, [r, z], 1.05 * d, dose=0.03, dta=0.3, threshold=0.1)
    assert res.passing < 100.0


def test_ratio_with_zero_numerator_keeps_its_uncertainty():
    r, e = ratio(np.array([0.0, 1.0]), np.array([0.1, 0.1]), np.array([1.0, 2.0]), np.array([0.1, 0.1]))
    assert r[0] == 0.0 and e[0] == pytest.approx(0.1)
    assert e[1] == pytest.approx(0.5 * math.hypot(0.1, 0.05))


# -- medidas -----------------------------------------------------------------------------------------------------------

def test_metrics_with_a_position_at_zero():
    x = np.linspace(-2.0, 2.0, 401)
    y = 100.0 / (1.0 + np.exp((x - 0.0) / 0.1))  # caída distal centrada en 0
    y = np.where(x < -1.5, 0.0, y)
    res = {m.key: m.value for m in mt.compute("bragg", x, y)}
    assert res["fall8020"] is not None and res["fall8020"] > 0


def test_penhmain_cumulative_dvh_uses_the_lower_edges():
    from argus.results import dvh
    from argus.results.case import load_case_geometry
    from argus.results.readers import read_output

    case = load_case_geometry(RES / "tac" / "tac.in")
    g = read_output(RES / "tac" / "ct-3d-dose.dat")
    ex = dvh.stats(dvh.whole_ct(g, case.geo.ct))
    ref = read_output(RES / "tac" / "ct-dvh.dat")
    w = ref.x[1] - ref.x[0]
    m = {k.key: k.value for k in mt.compute("dvh", ref.x, ref.get("acumulado").y, None, ref.edges)}
    for k in ("D98", "D95", "D50"):
        assert abs(m[k] - ex[k]) < 0.25 * w, k


def test_window_fwhm_is_interpolated():
    from argus.results.spectro import window

    edges = np.arange(600e3, 720e3 + 1, 5e3)
    c = 0.5 * (edges[:-1] + edges[1:])
    sigma = 3e3
    y = np.exp(-0.5 * ((c - 662.3e3) / sigma) ** 2)
    w = window(edges, y, None, 640e3, 690e3, subtract=False)
    assert 6.5e3 < w.fwhm < 8.0e3  # el de la gaussiana es 7,06 keV; no un número entero de intervalos (5 o 10)


def test_log_channels_do_not_go_down_to_zero():
    from argus.results.spectro import convolve

    edges = np.geomspace(1e3, 1e6, 301)
    y = np.ones(300) / np.diff(edges) / 300
    out = convolve(edges, y, None, lambda e: 0.07 * np.sqrt(e * 662e3))
    assert len(out.edges) < 1000 and np.sum(out.edges[1:] < 1.0) <= 1


def test_measured_units_and_cells():
    assert position_scale("z (mm)") == 0.1 and position_scale("Profundidad (cm)") == 1.0
    assert position_scale("gamma") is None and position_scale("comment") is None and position_scale("x [m]") == 100
    t = read_table_text('z;D;err\n"1,0";"2,0";"0,1"\n"2,0";"3,0"\n3,0;4,0;NaN\n')
    assert t.data.shape == (3, 3) and t.data[0, 2] == pytest.approx(0.1) and np.isnan(t.data[1, 2])


# -- espectro de la fuente ---------------------------------------------------------------------------------------

def test_psource_keeps_the_exact_energy_of_the_sobp(tmp_path):
    # Como el psource.dat de los SOBP de Rafael: el último intervalo tiene anchura cero (26,9 % a 100 MeV).
    from argus.penhan.sections import analyze_spectrum

    rows = [(9.0e7, 0.5), (1.0e8, 0.2686), (1.0e8, -1.0)]
    info = analyze_spectrum(rows, False)
    p_total = sum(b[2] for b in info.bins)
    lines = [" #  Results from PENHMAIN. ", " #  Source energy spectrum (histogram).",
             " #  1st column: E (eV). 2nd column: spectrum (1/eV).",
             " #  3rd and 4th columns: simul. pdf limits (3SD, 1/eV)."]
    vals = [(9.0e7, 0.0, 0.0, 0.0)]
    for a, b, p in info.bins:
        fact = 1.0 / (b - a) if b - a > 1e-15 else 1e15
        vals += [(a, p / p_total * fact, 0.0, 0.0), (b, p / p_total * fact, 0.0, 0.0)]
    vals.append((1.0e8, 0.0, 0.0, 0.0))
    f = tmp_path / "psource.dat"
    f.write_text("\n".join(lines + ["".join(f"{v:14.6E}" for v in r) for r in vals]) + "\n", encoding="latin-1")
    data = sf.read_spectrum_file(f)
    opt = sf.default_options(data, 4)
    assert data.series[opt.series][0] == "definido"
    conv = sf.convert(data, opt)
    assert conv.error is None
    got = analyze_spectrum(conv.rows, False)
    assert [b[:2] for b in got.bins] == pytest.approx([b[:2] for b in info.bins])
    assert [b[2] for b in got.bins] == pytest.approx([b[2] / p_total for b in info.bins], rel=1e-5)


def test_last_spectr_row_is_the_maximum_like_penhmain():
    # Líneas de un .in con P ≥ 0 en la última: penhmain la toma como la energía máxima.
    data = sf.parse_table_text("SPECTR 1e8 0.3\nSPECTR 1.5e8 0.5\nSPECTR 2e8 0.2\n")
    conv = sf.convert(data, sf.default_options(data, 4))
    assert conv.rows == [(1e8, pytest.approx(0.375)), (1.5e8, pytest.approx(0.625)), (2e8, -1.0)]
    # Guardar y volver a importar da lo mismo que penhmain (también con una energía exacta en el máximo).
    for rows in ([(90e6, 0.1), (95e6, 0.3), (100e6, 0.2)], [(99e6, 0.1), (100e6, 0.27), (100e6, 0.5)]):
        text = sf.format_spectrum(rows, False, "MeV")
        data = sf.parse_table_text(text)
        conv = sf.convert(data, sf.default_options(data, 4))
        assert conv.error is None and conv.rows[-1][1] == -1.0
        from argus.penhan.sections import analyze_spectrum

        a = analyze_spectrum(rows, False)
        b = analyze_spectrum(conv.rows, False)
        assert [x[:2] for x in a.bins] == [x[:2] for x in b.bins]
        assert [x[2] for x in a.bins] == pytest.approx([x[2] for x in b.bins])


def test_commented_spectr_lines_do_not_count():
    from argus.penhan.sections import parse_spectrum_text

    text = "SPECTR 1.5e8 0.5\n       SPECTR 1.9e8 0.7\nSPECTR 1.8e8 0.5\nSPECTR 2.0e8 -1\n"
    conv = sf.convert(sf.parse_table_text(text), sf.ImportOptions(unit="eV", position="edge"))
    assert [e for e, _ in conv.rows] == [1.5e8, 1.8e8, 2.0e8]
    assert [e for e, _ in parse_spectrum_text(text)] == [1.5e8, 1.8e8, 2.0e8]


def test_csv_with_commas_and_integer_rows():
    from argus.penhan.sections import parse_spectrum_text

    text = "E (MeV),P\n10,0.2\n20,1\n30,0.5\n45,-1\n"
    data = sf.parse_table_text(text)
    conv = sf.convert(data, sf.default_options(data, 4))
    assert [e for e, _ in conv.rows] == [10e6, 20e6, 30e6, 45e6]
    assert parse_spectrum_text(text.split("\n", 1)[1], 1e6) == [(10e6, 0.2), (20e6, 1.0), (30e6, 0.5), (45e6, -1.0)]
    # Coma decimal con espacios: no es un CSV.
    assert parse_spectrum_text("1,5 2,3\n2,5 1,0\n") == [(1.5, 2.3), (2.5, 1.0)]


def test_titles_decide_unit_and_density():
    data = sf.parse_table_text("# Espectro con energía media 0.045 MeV\n# E (keV)  P\n10 1\n20 6\n30 4\n40 -1\n")
    assert data.unit == "keV" and data.value is None
    for title, unit in (("E_kin/MeV  P", "MeV"), ("Energie/keV  P", "keV")):
        data = sf.parse_table_text(f"{title}\n10 0.1\n20 0.3\n40 0.6\n")
        assert data.unit == unit and data.value is None, title
    data = sf.parse_table_text("E (keV)\tcounts/keV\n10\t1\n20\t3\n")
    assert data.unit == "keV" and data.value == "density"


def test_psource_of_a_lines_source_can_be_imported(tmp_path):
    f = tmp_path / "psource.dat"
    f.write_text(" #  Results from PENHMAIN. \n #  Source energy spectrum (discrete lines).\n"
                 " #  1st column: E (eV). 2nd column: probability.\n"
                 " #  3rd and 4th columns: simul. prob. limits (3SD).\n"
                 "  1.173200E+06  4.997500E-01  4.9E-01  5.1E-01\n  1.332500E+06  5.002500E-01  4.9E-01  5.1E-01\n",
                 encoding="latin-1")
    data = sf.read_spectrum_file(f)
    conv = sf.convert(data, sf.default_options(data, 2))
    assert conv.lines and conv.error is None and [e for e, _ in conv.rows] == [1.1732e6, 1.3325e6]


def test_edges_tolerance_is_the_same_to_detect_and_apply():
    data = sf.parse_table_text("1.0 2.0000001 5\n2.0 3.0 10\n")
    opt = sf.default_options(data, 4)
    assert opt.position == "edges" and sf.convert(data, opt).error is None


def test_nice_ticks_always_ends():
    from argus.ui.spectrum_plot import nice_ticks

    t0 = time.monotonic()
    for lo, hi in ((1.0, math.nextafter(1.0, 2.0)), (100.0, 100.00000000000003), (90.0, 100.0)):
        assert nice_ticks(lo, hi, 8)
    assert time.monotonic() - t0 < 1.0


# -- fuente extensa ----------------------------------------------------------------------------------------------------

def test_source_box_away_from_its_active_bodies_is_an_error(tmp_path):
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry
    from argus.penhan import source_edit as se
    from argus.penhan.validate import source_box_fraction, validate_case

    geo = compile_geometry(load_geometry(RES / "caja" / "caja.geo"))
    doc = parse_in_file(RES / "caja" / "caja.in")
    kb = geo.bodies.index(geo.find_body("SLAB")) + 1
    se.set_box(doc, [2, 2, 2])
    se.set_active_bodies(doc, geo, [kb])
    se.set_position(doc, [0, 0, 2.5])  # dentro de la losa
    assert source_box_fraction(doc, geo) == pytest.approx(1.0)
    assert not validate_case(doc, geo).has_errors
    se.set_position(doc, [50, 0, 2.5])  # lejos: penhmain no terminaría
    errs = validate_case(doc, geo).errors
    assert errs and "sin fin" in errs[0].message
    se.set_position(doc, [10.99, 0, 2.5])  # casi fuera: muy pocos puntos dentro
    se.set_box(doc, [2, 20, 2])
    diags = validate_case(doc, geo)
    assert not diags.has_errors and any("intento 200" in d.message for d in diags)
    # 0.16.1: dice cómo ajustarla y que, si ya está ajustada, es la forma de los cuerpos (una capa fina).
    assert any("«Ajustar la caja a los cuerpos activos»" in d.message and "capa fina" in d.message for d in diags)


# -- lotes -------------------------------------------------------------------------------------------------------------

@pytest.fixture
def case():
    return RES / "caja" / "caja.in"


def test_campaign_over_nsimsh_and_dumpp(case, tmp_path):
    doc = parse_in_file(case)
    b = bt.make_campaign(doc, case, tmp_path / "c", [bt.Param("keyword", [1e4, 1e5], keyword="NSIMSH")], replicas=2,
                         showers=2e4)
    got = [[parse_in_text(r.in_text).model for r in v.runs] for v in b.variants]
    from argus.penhan import sections as sc

    vals = [[sc.value(parse_in_text(r.in_text), "NSIMSH")[0] for r in v.runs] for v in b.variants]
    assert vals == [[5e3, 5e3], [5e4, 5e4]] and got
    b = bt.make_campaign(doc, case, tmp_path / "d", [bt.Param("keyword", [120, 600], keyword="DUMPP")])
    assert [sc.value(parse_in_text(v.runs[0].in_text), "DUMPP")[0] for v in b.variants] == [120, 600]
    with pytest.raises(ValueError, match="lo pone ARGUS"):
        bt.make_campaign(doc, case, tmp_path / "e", [bt.Param("keyword", [1, 2], keyword="RSEED")])


def test_campaign_parameters_that_would_not_do_what_they_say(case, tmp_path):
    doc = parse_in_file(case)
    with pytest.raises(ValueError, match="no es una palabra clave"):
        bt.Param("keyword", [1.0], keyword="SBO").apply(parse_in_file(case), 1.0)
    with pytest.raises(ValueError, match="material"):
        bt.make_campaign(doc, case, tmp_path / "m", [bt.Param("material", ["agua.mat"], number=7)])
    with pytest.raises(ValueError, match="caracteres"):
        bt.make_campaign(doc, case, tmp_path / "m2", [bt.Param("material", ["pulmón muy largo.mat"], number=1)])
    rect = parse_in_text(write_in_text(doc).replace("SCONE ", "SRECTA 0 10 0 360\nXSCONE").replace("XSCONE", "      "))
    if rect.first("SRECTA") is not None and rect.first("SCONE") is None:
        with pytest.raises(ValueError, match="SRECTA"):
            bt.Param("keyword", [5.0], keyword="SCONE", index=2).apply(rect, 5.0)


def test_campaign_variants_are_checked(case, tmp_path):
    doc = parse_in_file(case)
    b = bt.make_campaign(doc, case, tmp_path / "c", [bt.Param("keyword", [0.0, 200.0], keyword="SCONE", index=2)])
    errs = bt.check_variants(b)
    assert len(errs) == 1 and "200" in errs[0] and "180" in errs[0]


def test_launch_sh_does_not_relaunch_and_quotes_the_program(case, tmp_path):
    if shutil.which("sh") is None:
        pytest.skip("sin sh")
    doc = parse_in_file(case)
    b = bt.make_parallel(doc, case, tmp_path / "p", 2, total_showers=100)
    b.exe_system, b.exe_name = "linux", "penhmain - copia"
    bt.write_batch(b, jobs=2)
    for r in b.runs:
        exe = tmp_path / "p" / r.folder / "penhmain - copia"
        exe.write_text("#!/bin/sh\ncat > /dev/null\necho una vez >> veces.txt\necho fin > penhmain-res.dat\n")
        exe.chmod(0o755)
    out = subprocess.run(["sh", str(tmp_path / "p" / "lanzar.sh"), "2"], capture_output=True, text=True,
                         timeout=60).stdout
    assert out.count("Terminada") == 2
    out = subprocess.run(["sh", str(tmp_path / "p" / "lanzar.sh"), "2"], capture_output=True, text=True,
                         timeout=60).stdout
    assert out.count("Ya lanzada") == 2
    for r in b.runs:
        assert (tmp_path / "p" / r.folder / "veces.txt").read_text().count("una vez") == 1


def test_run_hint_quotes_names_with_spaces():
    from argus.penhan import programs as pg

    info = pg.ExeInfo(Path("penhmain (1).exe"), "windows", "x86-64")
    hint = pg.run_hint(info, "caso uno.in", host="windows")
    assert '"penhmain (1).exe" < "caso uno.in"' in hint and "no en PowerShell" in hint
    assert pg.run_hint(pg.ExeInfo(Path("penhmain.exe"), "windows"), "caso.in", host="windows") == \
        "en cmd: «penhmain < caso.in»; en PowerShell: «cmd /c \"penhmain < caso.in\"»"


# -- materiales y geometría -----------------------------------------------------------------------------------------

def test_absorptive_nuclear_reactions_need_no_endf(tmp_path):
    from argus.penhan.materials import read_mat_nuclear

    mat = tmp_path / "abs.mat"
    mat.write_text(" PENELOPE (v. 2018)  Material data file ...............\n"
                   " ...\n"
                   " *** ENDF filenames and abundances of isotopes, NISOT = -2\n", encoding="latin-1")
    nuc = read_mat_nuclear(mat)
    assert nuc.nisot == -2 and not nuc.uses_endf and nuc.complete and nuc.isotopes == []


def test_first_error_of_the_file_is_the_one_pengeom_gives(tmp_path):
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry

    base = (CORPUS / "usuario" / "100Mev15-174" / "cubo-esfera.geo").read_text(encoding="latin-1")
    text = base.replace("SURFACE (   2), SIDE POINTER=(-1)", "SURFACE (   2), SIDE POINTER=( 0)", 1).replace(
        "MATERIAL(   0)", "MATERIAL(   0)\nFOOBAR  (   1)", 1)
    p = tmp_path / "dos-errores.geo"
    p.write_text(text, encoding="latin-1")
    doc = load_geometry(p)
    geo = compile_geometry(doc, base_dir=tmp_path)
    errs = sorted((d.line, d.code) for d in list(doc.diagnostics.errors) + list(geo.diagnostics.errors)
                  if d.code is not None and d.line is not None)
    assert errs[0][1] == 2024  # lado 0, antes de la línea mala (como PENGEOM)



@pytest.mark.parametrize("field, inside, at_end", [
    # Comprobado con gfortran: READ(texto,'(I3)') con el campo dentro del registro («…X») y al final.
    ("1 2", 12, 12), ("- 1", -1, -1), ("-1 ", -1, -1), (" - ", 0, None), ("+  ", 0, None), ("-  ", 0, None),
    ("1- ", None, None), (" 1 ", 1, 1), ("12 ", 12, 12), ("   ", 0, 0),
])
def test_internal_read_ignores_the_trailing_blanks_like_gfortran(field, inside, at_end):
    """gfortran no cuenta los blancos del final de una variable al leerla: un signo que acaba el registro es un error
    («INDICES=( 1, 1,-»: PENGEOM da 2075), aunque seguido de blancos dentro del registro valga 0."""
    from argus.core.fortran_io import FortranReadError, I, read_record

    for line, want in ((field + "X", inside), (field, at_end)):
        if want is None:
            with pytest.raises(FortranReadError):
                read_record(line, [I(3)])
        else:
            assert read_record(line, [I(3)]) == [want]


BASE_GEO = CORPUS / "usuario" / "100Mev15-174" / "cubo-esfera.geo"
# Dos errores en el mismo elemento: el de una línea anterior (el que da PENGEOM, que lee línea a línea) y uno de
# formato después. Los códigos son los que da PENGEOM real con cada variante (prueba «oracle» de abajo).
SAME_ELEMENT = [
    ("SURFACE (   5)  Esfera de 5000mm (5m) de radio\r\nINDICES=( 1, 1, 1, 0,-1)",
     "SURFACE (   1)  Esfera de 5000mm (5m) de radio\r\nINDICES=( 1, 1, X, 0,-1)", 2004),  # etiqueta repetida
    ("SURFACE (   2), SIDE POINTER=(-1)", "SURFACE (   2),\r\n SIDE POINTER=(-1)", 2024),  # lado en blanco (0)
    ("BODY    (   1)\r\n0000", "BODY    ( \r\n  1)\r\n0000", 2025),  # cuerpo limitante sin etiqueta
    ("INDICES=( 1, 1, 1, 0,-1)", "INDICES=( 1, 1,-", 2075),  # un signo al final del registro
    ("BODY    (   2)  Esfera Gigante (con hueco)\r\nMATERIAL(   0)",
     "BODY    (   1)  Esfera Gigante (con hueco)\r\nMATERIA (   0)", 2018),  # etiqueta repetida
]


def _two_errors(tmp_path, old, new):
    text = BASE_GEO.read_bytes().decode("latin-1")
    assert text.count(old) == 1
    p = tmp_path / "dos-errores.geo"
    p.write_bytes(text.replace(old, new).encode("latin-1"))
    return p


@pytest.mark.parametrize("old, new, code", SAME_ELEMENT)
def test_first_error_inside_the_same_element_is_the_one_pengeom_gives(tmp_path, old, new, code):
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry

    p = _two_errors(tmp_path, old, new)
    doc = load_geometry(p)
    geo = compile_geometry(doc, base_dir=tmp_path)
    errs = sorted((d.line, d.code) for d in list(doc.diagnostics.errors) + list(geo.diagnostics.errors)
                  if d.code is not None and d.line is not None)
    assert not geo.ok and errs[0][1] == code


@pytest.mark.oracle
@pytest.mark.parametrize("old, new, code", SAME_ELEMENT)
def test_first_error_inside_the_same_element_pengeom_agrees(tmp_path, need_probe, old, new, code):
    from argus.oracle.probe import run_probe

    assert run_probe(_two_errors(tmp_path, old, new)).status.iretrn == code


MATERIAL_SIGN = ("MATERIAL(   1)", "MATERIAL(  -")


def test_a_sign_ending_the_material_line_is_an_error_as_in_pengeom(tmp_path):
    """Antes «MATERIAL(  -» se leía como material 0 (vacío) y la geometría se daba por buena; PENGEOM da 2075."""
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry

    p = _two_errors(tmp_path, *MATERIAL_SIGN)
    doc = load_geometry(p)
    geo = compile_geometry(doc, base_dir=tmp_path)
    assert not geo.ok and [d.code for d in doc.diagnostics.errors] == [2075]


@pytest.mark.oracle
def test_a_sign_ending_the_material_line_pengeom_rejects_it(tmp_path, need_probe):
    from argus.oracle.probe import run_probe

    assert run_probe(_two_errors(tmp_path, *MATERIAL_SIGN)).status.iretrn == 2075

def test_ct_fast_reader_leaves_repeat_counts_to_the_slow_one():
    from argus.pengeom.ct import _fast_body

    assert _fast_body(b"1 0.3\n7 2*0.25\n", 2) is None


def test_revisar_in_writes_utf8_when_redirected(tmp_path):
    import sys

    from conftest import ROOT

    d = tmp_path / "caso"
    shutil.copytree(RES / "tac", d)
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    out = tmp_path / "informe.txt"
    with open(out, "wb") as fh:
        rc = subprocess.run([sys.executable, str(ROOT / "tools" / "revisar_in.py"), str(d), "--avisos"], stdout=fh,
                            stderr=subprocess.PIPE, env=env, timeout=300).returncode
    assert rc in (0, 1) and out.read_bytes().decode("utf-8")
