"""Fallos encontrados en la revisión de la 0.7 (el .in): nombres con espacios, WGTWIN, espacio de fases vacío,
enteros fuera de rango, volcados dañados, ediciones sin cambios y nombres de nucleido."""

import shutil

import pytest

from argus.penhan import sections as sc
from argus.penhan import source_edit as se
from argus.penhan.dumpfile import parse_dump_text
from argus.penhan.inputfile import parse_in_file, parse_in_text
from argus.penhan.listdirected import ListReadError, read_list
from argus.penhan.validate import validate_case
from conftest import CORPUS

CASE = CORPUS / "usuario/100Mev15-174"


def _case_text():
    return (CASE / "maniqui.in").read_bytes().decode("latin-1")


def _replace(text, prefix, new):
    lines = text.split("\r\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith(prefix))
    lines[i] = new
    return "\r\n".join(lines)


def _work(tmp_path, text, extra=None):
    for f in CASE.iterdir():
        if f.is_file() and f.suffix.lower() != ".in":
            shutil.copy2(f, tmp_path / f.name)
    for name, content in (extra or {}).items():
        (tmp_path / name).write_bytes(content if isinstance(content, bytes) else content.encode("latin-1"))
    p = tmp_path / "maniqui.in"
    p.write_bytes(text.encode("latin-1"))
    return p


def _psf_case(wgtwin):
    lines = _case_text().split("\r\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("       >>>>>>>> Material"))
    return "\r\n".join([lines[0], "IPSFN  psf.dat                    [Input psf name, up to 20 characters]",
                        f"WGTWIN {wgtwin}                          [Weight window, RR & spl of psf particles]"]
                       + lines[i:])


def _sim_diffs(path):
    from argus.oracle.compare_in import compare_checks
    from argus.oracle.probe import find_sim_probe, run_sim_probe
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry

    if find_sim_probe() is None:
        pytest.skip("Sonda de las comprobaciones de penhmain no compilada")
    doc = parse_in_file(path)
    work = path.parent
    geo = compile_geometry(load_geometry(work / doc.model.geomfn), parinp=doc.model.parinp_list() or None,
                           base_dir=work)
    res = run_sim_probe(path.read_bytes().decode("latin-1"), {f.name: str(f) for f in work.iterdir() if f.is_file()},
                        timeout=30.0)
    return res, compare_checks(doc, geo, work, res)


# -- nombres de fichero ----------------------------------------------------------------------------
def test_file_names_with_spaces_are_valid_for_penhmain(tmp_path):
    text = _replace(_case_text(), "GEOMFN", "GEOMFN cubo esfera.geo               [Geometry file, up to 20 chars]")
    p = _work(tmp_path, text)
    shutil.copy2(CASE / "cubo-esfera.geo", tmp_path / "cubo esfera.geo")
    doc = parse_in_file(p)
    assert not doc.diagnostics.errors and doc.model.geomfn == "cubo esfera.geo"
    assert any("espacios" in (ln.warning or "") for ln in doc.lines)
    # Un comentario que empieza antes de la columna 28 sí es un error.
    bad = parse_in_text(_replace(_case_text(), "GEOMFN", "GEOMFN cubo.geo [Geometry file]"))
    assert any("columna 28" in (ln.error or "") for ln in bad.lines)


def test_a_20_character_name_followed_by_the_comment_is_valid():
    text = _replace(_case_text(), "MFNAME", "MFNAME 12345678901234567890[Material file, up to 20 chars]")
    doc = parse_in_text(text)
    ln = doc.first("MFNAME")
    assert ln.error is None and ln.text == "12345678901234567890"


# -- fuente de espacio de fases ----------------------------------------------------------------------
def test_inverted_weight_window_is_3039(tmp_path):
    p = _work(tmp_path, _psf_case("2.0 1.0"), {"psf.dat": " 1 1 0 0 0 0 0 1 1e6 1\n"})
    doc = parse_in_file(p)
    codes = [e.code for e in validate_case(doc, None, tmp_path).errors]
    assert 3039 in codes


def test_empty_phase_space_file_is_3117(tmp_path):
    p = _work(tmp_path, _psf_case("0.5 1.5"), {"psf.dat": b""})
    codes = [e.code for e in validate_case(parse_in_file(p), None, tmp_path).errors]
    assert 3117 in codes
    (tmp_path / "psf.dat").write_text("# solo la cabecera\n# sin partículas\n")
    assert 3117 in [e.code for e in validate_case(parse_in_file(p), None, tmp_path).errors]


@pytest.mark.oracle
@pytest.mark.parametrize("wgtwin, psf", [("2.0 1.0", "# vacío\n"), ("0.5 1.5", "")])
def test_psf_checks_agree_with_penhmain(tmp_path, wgtwin, psf):
    p = _work(tmp_path, _psf_case(wgtwin), {"psf.dat": psf})
    res, diffs = _sim_diffs(p)
    assert res.iretrn in (3039, 3117) and not diffs


# -- lectura dirigida por lista ------------------------------------------------------------------------
@pytest.mark.parametrize("buf, kinds", [
    ("3000000000 12345", ["int", "int"]),       # RSEED fuera de INTEGER*4: «Integer overflow»
    ("0*1 2", ["int", "int"]),                  # «Zero repeat count»
    ("'1'", ["int"]),                           # entero entre apóstrofos: «Bad integer»
    ("'1.5'", ["real"]),
])
def test_values_penhmain_cannot_read_are_errors(buf, kinds):
    with pytest.raises(ListReadError):
        read_list(buf, kinds)


def test_limits_and_special_values_like_gfortran():
    assert read_list("2147483647 -2147483648", ["int", "int"])[0] == [2147483647, -2147483648]
    vals, _ = read_list("Inf -infinity NaN", ["real", "real", "real"])
    assert vals[0] == float("inf") and vals[1] == float("-inf") and vals[2] != vals[2]


@pytest.mark.oracle
def test_seed_overflow_agrees_with_penhmain(tmp_path):
    text = _replace(_case_text(), "RSEED", "RSEED  3000000000 12345        [Seeds of the random-number generator]")
    p = _work(tmp_path, text)
    assert parse_in_file(p).diagnostics.errors
    res, diffs = _sim_diffs(p)
    assert not diffs


# -- volcados dañados -----------------------------------------------------------------------------------
def test_damaged_dump_headers_do_not_raise():
    good = ("   1000000.0000000000        21379.535999999993        21115.395942687988     \n"
            "Title of the job, up to 65 characters.                           \n"
            "   480293953  1188892783\n           0   0.0000000000000       F\n")
    for count in ("99999999999999999999", "1000000000000", "-5"):
        info = parse_dump_text(good + f"           4\n {count} T F\n")
        assert info.problem == "format" or info.state != "ok" or info.problem == "truncated"


# -- ediciones sin cambios ---------------------------------------------------------------------------------
@pytest.mark.parametrize("folder, name", [("penhan/epma", "epma.in"), ("penhan/stack", "stack.in")])
def test_rewriting_the_same_detectors_changes_nothing(folder, name):
    raw = (CORPUS / folder / name).read_bytes().decode("latin-1")
    doc = parse_in_text(raw)
    sc.set_energy_detectors(doc, sc.energy_detectors(doc))
    sc.set_angular_detectors(doc, sc.angular_detectors(doc))
    sc.set_impact_detectors(doc, sc.impact_detectors(doc))
    assert doc.text() == raw


# -- nombres que penhmain lee dirigidos por lista ---------------------------------------------------------
@pytest.mark.parametrize("name", ["Co 60.nuc", "nuc/Co-60.nuc", "Co-60,v2.nuc", "2*x.nuc", "Co-60.nuc"])
def test_nuclide_names_are_written_so_penhmain_reads_them_whole(name):
    doc = parse_in_text((CORPUS / "penhan/nuclide/nuclide.in").read_bytes().decode("latin-1"))
    se.set_nuclide(doc, name)
    assert parse_in_text(doc.text()).model.source.nuclide == name


# -- espectros pegados o importados desde una hoja de cálculo en español --------------------------------
@pytest.mark.parametrize("text, unit, rows", [
    ("﻿10;0,2\r\n20;0,3\r\n30;0,5\r\n40;-1\r\n", 1e6,                      # CSV UTF-8 de Excel, con BOM
     [(10e6, 0.2), (20e6, 0.3), (30e6, 0.5), (40e6, -1.0)]),
    ("500\t0,25\r\n999,5\t0,25\r\n1.500\t0,25\r\n2.000,5\t0,25\r\n3.000\t-1\r\n", 1e3,  # separador de miles
     [(5e5, 0.25), (999.5e3, 0.25), (1.5e6, 0.25), (2000.5e3, 0.25), (3e6, -1.0)]),
    ("10\t0,5\r\n20\t0,5\r\n30\t\r\n", 1e6,                                        # última P en blanco
     [(10e6, 0.5), (20e6, 0.5), (30e6, -1.0)]),
    ("1,500.5 0.25\n2,000 0.75\n3000 -1\n", 1.0,                                   # a la inglesa
     [(1500.5, 0.25), (2000.0, 0.75), (3000.0, -1.0)]),
    ("E\tP\n10\t0,5\n20\t0,5\n30\t-1\n", 1e6, [(10e6, 0.5), (20e6, 0.5), (30e6, -1.0)]),
    ("SPECTR 1.0E6 0.5 [x]\nSPECTR 2.0E6 -1 [x]\n", 1e3, [(1e6, 0.5), (2e6, -1.0)]),
    ("1e6,0.5\n2e6,-1\n", 1.0, [(1e6, 0.5), (2e6, -1.0)]),
    # Revisión 0.8: la última P vacía con la energía en notación científica o con miles (Excel en español).
    ("9,00E+07\t0,5\r\n9,50E+07\t0,5\r\n1,00E+08\t\r\n", 1.0, [(9e7, 0.5), (9.5e7, 0.5), (1e8, -1.0)]),
    ("90\t0,5\r\n1,0E+02\t\r\n", 1e6, [(90e6, 0.5), (100e6, -1.0)]),
    ("500\t0,5\r\n1.000,5\t\r\n", 1e3, [(5e5, 0.5), (1000.5e3, -1.0)]),
])
def test_spectrum_text_from_spreadsheets(text, unit, rows):
    from argus.penhan.sections import parse_spectrum_text

    got = parse_spectrum_text(text, unit)
    assert len(got) == len(rows)
    for (e, p), (e2, p2) in zip(got, rows):
        assert e == pytest.approx(e2) and p == pytest.approx(p2)


def test_unreadable_pasted_rows_are_reported():
    from argus.penhan.sections import parse_spectrum_text

    skipped = []
    rows = parse_spectrum_text("E\tP\n90\t0,25\n95\t75%\n100\t-1\n", 1e6, skipped)
    assert [e for e, _ in rows] == [90e6, 100e6] and skipped == ["95  75%"]


def test_spectrum_files_in_windows_encodings():
    pytest.importorskip("PySide6")
    import codecs

    from argus.ui.spectrum_dialog import decode_text

    text = "10\t0,2\r\n20\t-1\r\n"
    for raw in (codecs.BOM_UTF8 + text.encode("utf-8"), text.encode("utf-16"), text.encode("cp1252"),
                "Energía\tP\r\n".encode("cp1252") + text.encode("cp1252")):
        assert decode_text(raw).lstrip("﻿").endswith(text)
