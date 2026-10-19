"""Fallos de la cuarta revisión del .in (0.10): la fuente radiactiva (PENNUC), la tabla de EPROF, los ficheros de
salida, líneas que pasan de la columna 127, volcados grandes, rejillas de dosis, líneas cambiadas que no se pueden
leer, factores de repetición al renumerar, el «;», las etiquetas con apóstrofo y el forzado con FORCER negativo.

Las pruebas de oráculo comparan con el PMRDR real (sonda ``argus_simprobe``)."""

from __future__ import annotations

import time

import pytest

from argus.core.diagnostics import Severity
from argus.penhan import sections as sc
from argus.penhan.dumpfile import read_dump
from argus.penhan.follow import retarget
from argus.penhan.inputfile import LINE_WIDTH, parse_in_text
from argus.penhan.listdirected import read_pairs
from test_review3_checks import (
    NUC,
    NUCLIDE_CASE,
    PDFILES,
    VOID_GEO,
    argus,
    assert_agree,
    codes,
    nuclide_in,
    text_of,
    void_in,
)


def _pdfiles():
    if not PDFILES:
        pytest.skip("Faltan pdrelax.p11 y pdatconf.p14 (base de datos de PENELOPE) para PENNUC.")
    return {"pdrelax.p11": PDFILES[0] / "pdrelax.p11", "pdatconf.p14": PDFILES[0] / "pdatconf.p14"}


# ---------------------------------------------------------------------------------------------------------------------
# Fuente radiactiva: PENNUC0 lee el .nuc (con sus límites) y los ficheros de relajación de la carpeta
# ---------------------------------------------------------------------------------------------------------------------

NUC4 = NUC.replace("ND  1\n", "ND  4\n")  # 4 hijas: PENNUC admite 3 (NDM)


def test_a_radioactive_source_needs_its_files(tmp_path):
    base = {"disc-origin.geo": NUCLIDE_CASE / "disc-origin.geo"}
    _doc, _geo, d = argus(tmp_path / "a", nuclide_in(""), {"xx.nuc": NUC, **base})
    warn = text_of(d, Severity.WARNING)
    assert "pdatconf.p14" in warn and "pdrelax.p11" in warn and 3120 not in codes(d)
    _doc, _geo, d = argus(tmp_path / "b", nuclide_in(""), {"xx.nuc": NUC4, **base})
    assert 3120 in codes(d) and "NDM" in text_of(d, Severity.ERROR)


@pytest.mark.oracle
@pytest.mark.parametrize("nuc, with_pdfiles", [(NUC, True), (NUC, False), (NUC4, True)],
                         ids=["bien", "sin-pdfiles", "cuatro-hijas"])
def test_radioactive_sources_match_penhmain(tmp_path, need_sim_probe, nuc, with_pdfiles):
    files = {"xx.nuc": nuc, "disc-origin.geo": NUCLIDE_CASE / "disc-origin.geo", **(_pdfiles() if with_pdfiles else {})}
    if with_pdfiles:
        assert_agree(tmp_path, nuclide_in(""), files)
    else:  # sin los pdfiles ARGUS avisa (la carpeta de simulación los copia) y penhmain se detiene
        from test_review3_checks import penhmain, write

        write(tmp_path / "p", files)
        assert penhmain(tmp_path / "p", nuclide_in(""))[0] == 3120
        _doc, _geo, d = argus(tmp_path / "a", nuclide_in(""), files)
        assert "pdatconf.p14" in text_of(d, Severity.WARNING)


# ---------------------------------------------------------------------------------------------------------------------
# EPROF: la tabla de sección eficaz (PMAP0)
# ---------------------------------------------------------------------------------------------------------------------

EPROF_IN = void_in("GRIDZ  -1 1 10\nGRIDR  2 10\nEPROF  {name}\nEPROPM 2 1\n")


def test_eprof_pairs_are_counted_like_fortran():
    assert read_pairs("1 2\n3 4\n") == (2, 4.0)
    assert read_pairs("1\n2\n3 4 5\n") == (2, 4.0)  # un par en dos registros; lo que sobra se pierde
    assert read_pairs("a b\n1 2\n") == (1, 2.0)  # ERR=1: se salta
    assert read_pairs("1 2\n3") == (1, 2.0)  # END=2 a medias
    assert read_pairs("".join(f"{i} {2 * i}\n" for i in range(600)))[0] == 600


BIG_TABLE = "".join(f"{1e3 * (i + 1):e} {1e-24 * (i + 1):e}\n" for i in range(600))


@pytest.mark.oracle
@pytest.mark.parametrize("table, code", [(BIG_TABLE, 3138), ("1.0e6 1.0e-24\n", 3139), ("1e5 1e-24\n2e6 3e-24\n", 0),
                                         ("1e5 0\n", 0)],
                         ids=["600-puntos", "un-punto", "bien", "sin-seccion"])
def test_the_eprof_table_matches_penhmain(tmp_path, need_sim_probe, table, code):
    got = assert_agree(tmp_path, EPROF_IN.format(name="xs.dat"), {"void.geo": VOID_GEO, "xs.dat": table})
    assert got == code


@pytest.mark.oracle
def test_eprof_files_that_cannot_be_opened(tmp_path, need_sim_probe):
    assert assert_agree(tmp_path / "a", EPROF_IN.format(name="sub"), {"void.geo": VOID_GEO}, dirs=("sub",)) == 3137
    assert assert_agree(tmp_path / "b", EPROF_IN.format(name="nada/xs.dat"), {"void.geo": VOID_GEO}) == 3137


def test_eprof_material_out_of_the_tables(tmp_path):
    text = void_in("GRIDZ  -1 1 10\nGRIDR  2 10\nEPROF  xs.dat\nEPROPM 2 12\n")
    _doc, _geo, d = argus(tmp_path, text, {"void.geo": VOID_GEO, "xs.dat": "1e5 1e-24\n2e6 3e-24\n"})
    assert "fuera de sus tablas" in text_of(d, Severity.ERROR)


# ---------------------------------------------------------------------------------------------------------------------
# Ficheros de salida
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.oracle
def test_a_phase_space_file_that_cannot_be_created(tmp_path, need_sim_probe):
    text = void_in("IMPDET 0 0 100 1 1\nIDPSF  nada/out.psf\nIDBODY 1 [b]\n")
    assert assert_agree(tmp_path, text, {"void.geo": VOID_GEO}) == 3114


def test_outputs_in_missing_folders_are_warned(tmp_path):
    text = void_in("IMPDET 0 0 100 0 1\nIDSPC  nada/spc.dat\nIDBODY 1 [b]\nDUMPTO otra/dump.dmp\n")
    _doc, _geo, d = argus(tmp_path, text, {"void.geo": VOID_GEO})
    warn = text_of(d, Severity.WARNING)
    # DUMPTO, un error desde la duodécima revisión: penhmain simularía y se detendría al hacer el primer volcado, sin
    # escribir ningún resultado (con IDSPC, el volcado y el resumen se escriben antes: se puede reanudar).
    assert "IDSPC" in warn and "DUMPTO" in text_of(d, Severity.ERROR)


# ---------------------------------------------------------------------------------------------------------------------
# Escritura: columnas, líneas que no se pueden leer, repeticiones, «;» y apóstrofos
# ---------------------------------------------------------------------------------------------------------------------

BASE = void_in("")


def test_edited_values_always_fit_in_the_columns_penhmain_reads():
    doc = parse_in_text(BASE)
    vals = [1234.567890123456, 0.3, 12345.67890123456, 0.1234567890123456, 0.2, 98765.43210987654,
            1.2345678901234567e-05]
    sc.set_material_param(doc, 1, "MSIMPA", vals)
    line = next(t for t in doc.text().splitlines() if t.startswith("MSIMPA"))
    assert len(line.split("[")[0].rstrip()) <= LINE_WIDTH
    again = sc.material_params(parse_in_text(doc.text()), 1)["MSIMPA"]
    assert again == sc.material_params(doc, 1)["MSIMPA"]
    assert all(abs(a - b) <= 1e-11 * abs(b) for a, b in zip(again, vals))


def test_an_edited_line_that_cannot_be_read_keeps_its_text():
    doc = parse_in_text(BASE)
    sc.set_seeds(doc, [(12345678901, 5)])
    shown = [t for t in doc.display_lines() if t.startswith("RSEED")]
    assert shown and "12345678901" in shown[0]
    assert "12345678901" in doc.text() and any("RSEED" in d.message for d in doc.diagnostics.errors)


def test_retarget_does_not_touch_a_repeat_count():
    doc = parse_in_text(void_in("IBRSPL 2*1           [KB,splitting factor]\n"))
    ln = doc.first("IBRSPL")
    retarget(ln, kb=3)
    doc.analyze()
    ln = doc.first("IBRSPL")
    assert ln.kb == 3 and ln.values == [1] and not ln.error


def test_semicolons_are_explained():
    doc = parse_in_text("TITLE  x\nSPOSIT 0 ; 0 ; -10\n")
    msg = doc.diagnostics.errors[0].message
    assert "«;»" in msg and "detendría" not in msg


def test_labels_with_an_apostrophe_cannot_be_written():
    doc = parse_in_text(BASE)
    with pytest.raises(ValueError, match="apóstrofo"):
        sc.set_energy_detectors(doc, [sc.EnergyDetector(0.0, 0.0, 100, bodies=[sc.BodyRef("O'BR", None)])])


# ---------------------------------------------------------------------------------------------------------------------
# Volcados grandes y forzado con FORCER negativo
# ---------------------------------------------------------------------------------------------------------------------

def test_a_big_dump_is_read_without_loading_it(tmp_path):
    from test_simcheck import dump_text  # cabecera de un volcado de penhmain

    head = dump_text().replace("   *** END ***", "")
    path = tmp_path / "dump.dmp"
    body = ("  1.0000000000000000E+00" * 20 + "\n") * 150_000  # ~70 MB
    path.write_text(head + body + "   *** END ***\n", encoding="latin-1")
    t = time.perf_counter()
    info = read_dump(path)
    assert time.perf_counter() - t < 2.0
    assert info.usable, info.message


def test_splitting_with_a_negative_forcer_is_warned(tmp_path):
    text = void_in("IFORCE 1 1 4 -0.5 0.1 2\nIBRSPL 1 2\n")
    _doc, _geo, d = argus(tmp_path, text, {"void.geo": VOID_GEO})
    assert "FORCER negativo" in text_of(d, Severity.WARNING) and 3068 not in codes(d)
