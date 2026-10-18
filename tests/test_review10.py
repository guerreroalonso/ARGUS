"""Décima revisión (0.17.2): búsqueda aleatoria de fallos, el núcleo (sin ventana).

Rafael (18-10-2026): «una tanda de búsqueda aleatoria de fallos». Fuzzers frente a PENGEOM y PMRDR reales, ficheros
dañados al azar y entradas raras, y una prueba de mono de la ventana. Aquí, lo que salió fuera de la ventana; cada
prueba falla con el código de antes."""

from __future__ import annotations

import json
import math
import shutil
import time
import warnings
from pathlib import Path

import pytest

from argus.penhan import composition as cp
from argus.penhan import material_names as mn
from argus.penhan import spectrum_file as sf
from argus.penhan.materials import builtin_material_list
from conftest import CORPUS

RES = CORPUS / "resultados"
Z = "0" * 64
END = "END      " + "0" * 55


# -- composiciones y nombres de materiales -----------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["H2Ó", "ÑaCl", "Ｈ２Ｏ", "Ⅻ", "Ó", "² 11,19 % O 88,81 %", "H²O"])
def test_formulas_with_letters_or_digits_that_are_not_ascii(text):
    """Una mayúscula o una cifra que no es ASCII («Ó», la «Ｈ» de ancho completo, «²»): AttributeError o el mensaje de
    Python en inglés; el editor de composición lo llevaba al aviso de error inesperado en cada pulsación."""
    with pytest.raises(ValueError) as exc:
        cp.parse_elements(text)
    assert "invalid literal" not in str(exc.value) and "«" in str(exc.value)


def test_formulas_still_parse_and_deep_parentheses_are_an_error():
    assert cp.parse_elements("H2O") is not None and cp.parse_elements("Ca10(PO4)6(OH)2") is not None
    with pytest.raises(ValueError, match="paréntesis"):
        cp.parse_elements("(" * 1200 + "H2O" + ")" * 1200)  # (antes, RecursionError)


def test_material_search_with_absurd_numbers_and_long_names():
    entries = mn.penelope_entries(builtin_material_list())
    mn.search(entries, "(H2O)" + "9" * 400)  # (antes, OverflowError al calcular la fórmula: round(inf))
    mn.search(entries, "(" * 1200 + "H2O" + ")" * 1200)
    t0 = time.perf_counter()
    mn.best_guess(["O" + "9" * 8000], entries)  # (antes, 11 s: la expresión de las unidades, cuadrática)
    assert time.perf_counter() - t0 < 1.0
    assert mn.best_guess(["water_1cm.mat"], entries).number == 278  # las unidades se siguen quitando


# -- volcados, biblioteca, cabeceras, .ct e índices ------------------------------------------------------------------

def test_a_dump_name_with_a_nul_char_does_not_stop_opening_the_in():
    from argus.penhan.dumpfile import read_dump
    from argus.penhan.inputfile import parse_in_text
    from argus.penhan.validate import validate_case

    assert read_dump(Path("du\x00mp.dmp")).state == "missing"  # (antes, ValueError «embedded null byte»)
    src = CORPUS / "penhan" / "disc"
    text = (src / "disc.in").read_text(encoding="latin-1").replace("DUMPTO dump.dmp ", "DUMPTO du\x00p.dmp ", 1)
    assert "\x00" in text
    validate_case(parse_in_text(text, str(src / "disc.in")), None, src)


def test_a_library_with_a_lone_surrogate_can_still_be_saved(tmp_path):
    """Una nota con «\\udfff» (JSON válido, escrito por otro programa): no se podía guardar nada y lo de memoria dejaba
    de ser lo del disco."""
    from argus.penhan.library import FILE_NAME, Library, LibraryMaterial, read_file, write_file

    p = tmp_path / FILE_NAME
    water = cp.Composition([(1, 2.0), (8, 1.0)], cp.BY_ATOMS, 1.0)
    write_file(p, [LibraryMaterial("Agua", water), LibraryMaterial("Hueso", water, note="nota")])
    p.write_text(p.read_text(encoding="utf-8").replace('"nota"', '"\\udfff"'), encoding="utf-8")
    lib = Library(p)
    lib.put(LibraryMaterial("Nuevo", water))
    lib.remove("Agua")
    assert lib.names() == [m.name for m in read_file(p)[0]] == ["Hueso", "Nuevo"]
    write_file(tmp_path / "export.json", lib.items)
    json.loads((tmp_path / "export.json").read_text(encoding="utf-8"))


def test_header_numbers_must_be_finite():
    from argus.results.formats import FormatError, header_int

    with pytest.raises(FormatError):
        header_int(["# NBX = 1.0E+999"], "NBX =")  # (antes, OverflowError)
    assert header_int(["# NBX =  11"], "NBX =") == 11


@pytest.mark.parametrize("case, name, old, new", [
    ("esfera", "radial-dose.dat", "NBR =  25", "NBR =   0"),
    ("esfera", "radial-dose.dat", "NBR =  25", "NBR =  -3"),
    ("cilindro", "2d-dose-map.dat", "NBR =  10", "NBR =   0"),
    ("caja", "3d-dose-map.dat", "XL = -5.000000E+00", "XL = -5.000000E+999"),
    ("esfera", "radial-dose.dat", "RU =  5.000000E+00", "RU =  5.000000E+999"),
    ("caja", "3d-dose-map.dat", "NBX =  11", "NBX = 1.0E+999"),
])
def test_maps_with_headers_that_make_no_sense_are_not_read(tmp_path, case, name, old, new):
    """Se leían (una rejilla vacía, bordes NaN) y la vista de mapas fallaba al dibujarlos."""
    from argus.results.formats import FormatError
    from argus.results.readers import read_output

    p = tmp_path / name
    text = (RES / case / name).read_text("latin-1")
    assert old in text
    p.write_text(text.replace(old, new, 1), "latin-1")
    with pytest.raises(FormatError):
        read_output(p)
    read_output(RES / case / name)  # (el original se sigue leyendo)


def test_a_ct_header_with_more_voxels_than_the_file_has():
    """Antes, MemoryError («Unable to allocate 21.5 GiB»), que la ventana enseñaba como si fuera la memoria."""
    from argus.pengeom import ct as C

    raw = (RES / "tac" / "tac.ct").read_bytes()
    assert b"   8   6  12  " in raw
    t0 = time.perf_counter()
    with pytest.raises(C.CtFormatError, match="Fin de fichero"):
        C.read_ct_bytes(raw.replace(b"   8   6  12  ", b"   8   6  120000000  ", 1))
    assert time.perf_counter() - t0 < 2.0
    assert C.read_ct_bytes(raw).material.size == 8 * 6 * 12


@pytest.mark.parametrize("bad", ["[1]\n", '"paralelo"\n', "5\n"])
def test_index_files_that_are_json_but_not_objects(tmp_path, bad):
    from argus.results.folder import ResultsFolder

    d = tmp_path / "campana"
    shutil.copytree(RES / "campana", d)
    (d / "argus-campana.json").chmod(0o644)
    (d / "argus-campana.json").write_text(bad)
    ResultsFolder(d)  # (antes, AttributeError: 'list' object has no attribute 'get')
    shutil.rmtree(d)
    shutil.copytree(RES / "campana", d)
    p = d / "v01-SPOSIT_-5" / "argus-paralelo.json"
    p.chmod(0o644)
    p.write_text(bad)
    ResultsFolder(d)


# -- espectros ---------------------------------------------------------------------------------------------------------

def test_spectra_whose_values_overflow():
    warnings.simplefilter("ignore")
    d = sf.parse_table_text("# E (MeV)  P\n1  1e308\n2  1e308\n3  -1\n", "t.txt")
    for rows in (250, 2):
        c = sf.convert(d, sf.ImportOptions(unit="MeV", kind="continuous", position="edge", value="probability",
                                           max_rows=rows))
        assert c.error is None and all(math.isfinite(p) for _e, p in c.rows)
        assert sum(p for _e, p in c.rows[:-1]) == pytest.approx(1.0)  # (antes, 0 y 0, o NaN)
    d = sf.parse_table_text("# E (MeV)  dN/dE (1/MeV)\n1  1e303\n2  1e303\n3  1e303\n", "t.txt")
    c = sf.convert(d, sf.default_options(d))
    assert c.error is None and len(c.rows) == 4  # (antes, «Todas las probabilidades son cero»)
    d = sf.parse_table_text("1e308 2\n1.5e308 2\n1.7e308 2\n", "t.txt")
    c = sf.convert(d, sf.ImportOptions(unit="eV", kind="lines", max_rows=1))
    assert c.error is None and all(math.isfinite(e) for e, _p in c.rows)  # (antes, una línea en E = inf)


def test_spectra_with_negative_or_unsorted_energies(tmp_path):
    d = sf.parse_table_text("# E (MeV)  P\n-3  0.2\n1  0.3\n10  0.5\n", "t.txt")
    c = sf.convert(d, sf.ImportOptions(unit="MeV", kind="continuous", position="center", value="probability"))
    assert c.error and "negativa" in c.error  # (antes, filas SPECTR con un intervalo al revés)
    lines = (RES / "caja" / "spc-enddet-01.dat").read_text("latin-1").splitlines()
    k = next(i for i, ln in enumerate(lines) if ln.strip() and not ln.lstrip().startswith("#")) + 10
    toks = lines[k].split()
    lines[k] = "  " + "  ".join(["-" + toks[0]] + toks[1:])
    p = tmp_path / "spc-enddet-01.dat"
    p.write_text("\n".join(lines) + "\n", "latin-1")
    with pytest.raises(ValueError, match="orden"):
        sf.read_spectrum_file(p)


# -- TAC --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ps", [[0.0, 0.0], [1e-300, 1e-300], [1e-20, 1e-20]])
def test_a_dicom_series_with_a_pixel_of_size_zero(tmp_path, ps):
    """Se aceptaba y el recorte daba OverflowError (en el diálogo, al acabar de leerla)."""
    pydicom = pytest.importorskip("pydicom")
    from argus.ct.dicom import scan_folder
    from dicom_synth import phantom_hu, write_series

    for p in write_series(tmp_path, phantom_hu, rows=12, cols=10, nslices=4):
        ds = pydicom.dcmread(p)
        ds.PixelSpacing = ps
        ds.save_as(p)
    s = scan_folder(tmp_path)[0]
    assert any("PixelSpacing" in t for t in s.problems)


def test_a_micro_ct_pixel_is_still_valid(tmp_path):
    pydicom = pytest.importorskip("pydicom")
    from argus.ct.dicom import scan_folder
    from dicom_synth import phantom_hu, write_series

    for p in write_series(tmp_path, phantom_hu, rows=12, cols=10, nslices=4):
        ds = pydicom.dcmread(p)
        ds.PixelSpacing = [0.001, 0.001]  # 1 µm
        ds.save_as(p)
    assert not scan_folder(tmp_path)[0].problems


# -- PENGEOM: el primer error, como GEOMIN ----------------------------------------------------------------------------

PLANE = f"SURFACE (   1)   Plane Z=0\nINDICES=( 0, 0, 0, 1, 0)\n{Z}\n"
PLANE2 = f"SURFACE (   2)   Plane Z=1\nINDICES=( 0, 0, 0, 1, 0)\nZ-SHIFT=(+1.000000000000000E+00,   0)\n{Z}\n"


def _body(lab="   1", refs=("SURFACE (   1), SIDE POINTER=(+1)", "SURFACE (   2), SIDE POINTER=(-1)")):
    return f"BODY    ({lab})   cuerpo\nMATERIAL(   1)\n" + "".join(r + "\n" for r in refs) + f"{Z}\n"


def _errors(text: str, tmp_path: Path):
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import parse_geo_text

    p = tmp_path / "g.geo"
    p.write_text(text, encoding="latin-1")
    doc = parse_geo_text(text, str(p))
    geo = compile_geometry(doc, base_dir=tmp_path)
    errs = [d for d in list(doc.diagnostics) + list(geo.diagnostics) if d.severity.name == "ERROR"]
    return geo, errs


@pytest.mark.parametrize("ref, code", [("SURFACE (   3), SIDE POINTER=(-1)", 2021),
                                       ("SURFACE (   2), SIDE POINTER=( 0)", 2024)])
def test_without_end_the_error_of_the_last_element_comes_first(tmp_path, ref, code):
    """Frente a GEOMIN (la sonda): sin END, ARGUS solo decía «Fin de fichero sin línea END» y no el error del último
    elemento, que GEOMIN da antes de llegar al final (en la línea 13)."""
    text = f"Prueba\n{Z}\n{PLANE}{PLANE2}" + _body(refs=("SURFACE (   1), SIDE POINTER=(+1)", ref))
    geo, errs = _errors(text, tmp_path)
    assert not geo.ok
    first = min((d for d in errs if d.line is not None), key=lambda d: (len(text) if d.at_end else d.line))
    assert first.code == code and first.line == 12
    assert any(d.at_end and "END" in d.message for d in errs)  # (también se dice que falta END)


@pytest.mark.parametrize("lines, at, parser_code, code", [
    ("SURFACE (   1)   Plane Z=0\nINDICES=( 0, 0, 0, 1, 0)\nZ-SHIFX=(+1.000000000000000E+00,   1)\n", 4, 2012, 2008),
    ("SURFACE (   1)   Plane Z=0\nINDICES=( 0, 0, 0, 1, 0)\n" + "1" * 64 + "\n"
     "Z-SHIFT=(+1.000000000000000E+00,   0)\n", 4, 2012, 2008),
    ("SURFACE (   1)   Plane Z=0\nINDICES=( 0, 0, 0, 0, 0)\n     AZ=(+1.000000000000000E+00,   0)\n"
     "    AXW=(+1.000000000000000E+00,   2)\n", 5, 2014, 2013),
    ("SURFACE (   1)   Plane Z=0\nINDICES=( 0, 0, 0, 0, 0)\n     AZ=(+1.000000000000000E+00,   0)\n" + "1" * 64 +
     "\nX-SHIFX=(+1.000000000000000E+00,   1)\n", 6, 2016, 2015),
])
def test_an_unknown_parameter_that_asks_for_a_parinp_gets_the_code_of_geomin(tmp_path, lines, at, parser_code, code):
    """GEOMIN compara ICHPAR (columnas 33-36) con NPINP antes de mirar la palabra: con un PARINP que el .in no da, su
    error es «NPINP is too small» (2008, 2013, 2015, 2049…). Lo de ARGUS (qué está mal escrito) se queda."""
    text = f"Prueba\n{Z}\n{lines}{Z}\n{PLANE2}" + _body() + END + "\n"
    _geo, errs = _errors(text, tmp_path)
    assert any(d.code == parser_code and d.line == at for d in errs)
    npinp = [d for d in errs if d.code == code]
    assert npinp and npinp[0].line == at and "PARINP" in npinp[0].message


def test_with_the_parinp_defined_the_unknown_parameter_keeps_its_code(tmp_path):
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import parse_geo_text

    text = (f"Prueba\n{Z}\nSURFACE (   1)   Plane Z=0\nINDICES=( 0, 0, 0, 1, 0)\n"
            f"Z-SHIFX=(+1.000000000000000E+00,   1)\n{Z}\n{PLANE2}" + _body() + END + "\n")
    doc = parse_geo_text(text, str(tmp_path / "g.geo"))
    geo = compile_geometry(doc, parinp=[1.0], base_dir=tmp_path)
    assert not any(d.code == 2008 for d in geo.diagnostics)


def test_a_reference_in_the_first_element_is_not_blamed_on_pengeom(tmp_path):
    """GEOMIN no busca las etiquetas en el primer elemento (IF(NBODYS.GT.1)) y sigue con datos que no existen: ARGUS lo
    rechaza igual, pero sin decir que PENGEOM se pararía con 2025."""
    text = f"Prueba\n{Z}\n{PLANE}{PLANE2}" + _body(refs=("SURFACE (   1), SIDE POINTER=(+1)", "BODY    (   9)")) \
        + END + "\n"
    geo, errs = _errors(text, tmp_path)
    assert not geo.ok
    e = [d for d in errs if "'9'" in d.message]
    assert e and e[0].code is None and "primer" in e[0].message
    # En otro elemento, sí es el error de PENGEOM.
    text2 = f"Prueba\n{Z}\n{PLANE}{PLANE2}" + _body() + _body(lab="   2", refs=(
        "SURFACE (   1), SIDE POINTER=(+1)", "BODY    (   9)")) + END + "\n"
    _geo2, errs2 = _errors(text2, tmp_path)
    assert any(d.code == 2025 for d in errs2)


@pytest.mark.oracle
def test_the_first_errors_against_geomin(tmp_path, need_probe):
    """Los mismos ficheros con el GEOMIN de verdad: el código de PENGEOM es el que da ARGUS."""
    from argus.oracle.probe import run_probe

    cases = [
        (f"Prueba\n{Z}\n{PLANE}{PLANE2}" + _body(refs=("SURFACE (   1), SIDE POINTER=(+1)",
                                                       "SURFACE (   3), SIDE POINTER=(-1)")), 2021),
        (f"Prueba\n{Z}\nSURFACE (   1)   Plane Z=0\nINDICES=( 0, 0, 0, 1, 0)\n{'1' * 64}\n"
         f"Z-SHIFT=(+1.000000000000000E+00,   0)\n{Z}\n{PLANE2}" + _body() + END + "\n", 2008),
    ]
    for text, code in cases:
        p = tmp_path / "g.geo"
        p.write_text(text, encoding="latin-1")
        res = run_probe(p, tables=False)
        assert res.status.iretrn == code
        _geo, errs = _errors(text, tmp_path)
        assert any(d.code == code for d in errs)


# -- el editor: eliminar sin cambiar lo de fuera ---------------------------------------------------------------------

PROTRUDE = f"""Cuerpo que asoma de su modulo
{Z}
SURFACE (  P0)   Plane Z=0
INDICES=( 0, 0, 0, 1, 0)
{Z}
SURFACE (  CR)   Cylinder R=1
INDICES=( 1, 1, 0, 0,-1)
{Z}
BODY    (   C)   r<1, z<0 (sin tapa inferior)
MATERIAL(   1)
SURFACE (  P0), SIDE POINTER=(-1)
SURFACE (  CR), SIDE POINTER=(-1)
{Z}
SURFACE (  P4)   Plane Z=-4
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(-4.000000000000000E+00,   0)
{Z}
SURFACE (  PT)   Plane Z=1e-5
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(+1.000000000000000E-05,   0)
{Z}
MODULE  (   M)   r<1, -4<z<1e-5
MATERIAL(   2)
SURFACE (  CR), SIDE POINTER=(-1)
SURFACE (  P4), SIDE POINTER=(+1)
SURFACE (  PT), SIDE POINTER=(-1)
BODY    (   C)
{Z}
SURFACE (  SS)   Sphere R=0.5 at x=5
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+5.000000000000000E-01,   0)
Y-SCALE=(+5.000000000000000E-01,   0)
Z-SCALE=(+5.000000000000000E-01,   0)
X-SHIFT=(+5.000000000000000E+00,   0)
{Z}
BODY    (   S)   esfera aparte
MATERIAL(   1)
SURFACE (  SS), SIDE POINTER=(-1)
{Z}
{END}
"""


def test_deleting_what_would_leave_a_module_alone_with_a_child_that_sticks_out(tmp_path):
    """editor_fuzz (semilla 7777, cadena 27): con dos elementos arriba, GEOMIN añade la envolvente; al eliminar la
    esfera, el módulo queda solo, sin envolvente, y lo que asomaba de su hija (sin tapa) pasaba a ser material hasta
    z = −∞ (en ARGUS y en PENGEOM), lejos de lo eliminado y sin ningún aviso."""
    from argus.pengeom import edit as ge
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import parse_geo_text

    doc = parse_geo_text(PROTRUDE, str(tmp_path / "g.geo"))
    geo = compile_geometry(doc, base_dir=tmp_path)
    assert geo.ok and geo.enclosure_added
    kb = geo.find_body("   S").index
    problem = ge.delete_problem(doc, geo, kb)
    assert problem and "quedaría solo arriba" in problem and "«C»" in problem
    with pytest.raises(ge.EditError):
        ge.delete_body(doc, geo, kb)
    # Con la hija dentro de su módulo, se elimina como siempre.
    fixed = PROTRUDE.replace("SURFACE (  P0), SIDE POINTER=(-1)\nSURFACE (  CR), SIDE POINTER=(-1)\n",
                             "SURFACE (  P0), SIDE POINTER=(-1)\nSURFACE (  CR), SIDE POINTER=(-1)\n", 1)
    fixed = fixed.replace("BODY    (   C)   r<1, z<0 (sin tapa inferior)\nMATERIAL(   1)\n",
                          "BODY    (   C)   r<1, -2<z<0\nMATERIAL(   1)\nSURFACE (  P2), SIDE POINTER=(+1)\n", 1)
    fixed = fixed.replace(f"SURFACE (  CR)   Cylinder R=1\nINDICES=( 1, 1, 0, 0,-1)\n{Z}\n",
                          f"SURFACE (  CR)   Cylinder R=1\nINDICES=( 1, 1, 0, 0,-1)\n{Z}\nSURFACE (  P2)   Plane "
                          f"Z=-2\nINDICES=( 0, 0, 0, 1, 0)\nZ-SHIFT=(-2.000000000000000E+00,   0)\n{Z}\n", 1)
    doc2 = parse_geo_text(fixed, str(tmp_path / "h.geo"))
    geo2 = compile_geometry(doc2, base_dir=tmp_path)
    assert geo2.ok
    assert ge.delete_problem(doc2, geo2, geo2.find_body("   S").index) is None
