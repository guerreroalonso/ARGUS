"""Fallos de la tercera revisión (lectura y escritura de ficheros): retornos de carro sueltos, factores de repetición,
PARINP, nombres A20, SRNUCL, valores nulos, coma decimal, ficheros .ct, clones, superficies nulas, escritura del
.geo y del .in sin mover comentarios y valores no finitos.

Cada prueba reproduce un fallo encontrado en la revisión; las marcadas ``oracle`` lo comparan con PENGEOM o
penhmain reales (sondas en build/probe).
"""

from __future__ import annotations

import time

import pytest

from argus.core.fortran_io import (
    NonFiniteValueError,
    format_e22_15,
    record_end,
    split_records,
    text_records,
)
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import parse_geo_text
from argus.pengeom.writer import write_geo_text
from argus.penhan.inputfile import InLine, format_line, parse_in_text, write_in_text
from argus.penhan.listdirected import ListReadError, read_list

Z = "0" * 64
END = "END      " + "0" * 55

GEO = f"""Test geometry
{Z}
SURFACE (   1)   Plane Z=0
INDICES=( 0, 0, 0, 1, 0)
{Z}
SURFACE (   2)   Plane Z=5
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(+5.000000000000000E+00,   0)
{Z}
SURFACE (   3)   Sphere R=10
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+1.000000000000000E+01,   0)
Y-SCALE=(+1.000000000000000E+01,   0)
Z-SCALE=(+1.000000000000000E+01,   0)
{Z}
BODY    (   1)   Slab inside sphere
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(+1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
{Z}
MODULE  (   2)   Sphere
MATERIAL(   2)
SURFACE (   3), SIDE POINTER=(-1)
BODY    (   1)
{Z}
{END}
"""

MINIMAL = (
    "TITLE  Prueba ARGUS\n"
    "SKPAR  2          [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]\n"
    "SENERG 1.0E6\n"
    "SPOSIT 0 0 -10\n"
    "MFNAME water.mat                      [Material file, up to 20 chars]\n"
    "GEOMFN a.geo                          [Geometry file, up to 20 chars]\n"
    "NSIMSH 1.0E6\n"
)


def _codes(diags):
    return [d.code for d in diags.errors]


@pytest.fixture
def need_reader_probe():
    from argus.oracle.probe import find_reader_probe

    if find_reader_probe() is None:
        pytest.skip("Sonda del lector de .in no compilada (python tools/build_probe.py)")


def _pen_in(text):
    from argus.oracle.probe import run_reader_probe

    return run_reader_probe(text=text, timeout=5.0)


def _diffs_in(text):
    from argus.oracle.compare_in import compare_reader

    return compare_reader(parse_in_text(text), _pen_in(text))


def _pen_geo(text, tmp_path, name="t.geo"):
    from argus.oracle.probe import run_probe

    p = tmp_path / name
    p.write_bytes(text.encode("latin-1"))
    return run_probe(p, tables=True)


# ---------------------------------------------------------------------------------------------------------------------
# 1. Retornos de carro (CR) sueltos: gfortran termina el registro en un CR dentro de las columnas que lee
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, width, first, expected", [
    ("a\rb\n", 72, None, ["a\r", "b\n"]),
    ("a\r\nb", 72, None, ["a\r\n", "b"]),
    ("a\r\r\nb\n", 72, None, ["a\r", "\r\n", "b\n"]),               # CR CR LF: un registro en blanco en medio
    ("a\rb\rc", 72, None, ["a\r", "b\r", "c"]),                      # fichero de Mac antiguo (solo CR)
    ("x" * 71 + "\ry\n", 72, None, ["x" * 71 + "\r", "y\n"]),        # CR en la columna 72: termina el registro
    ("x" * 72 + "\ry\n", 72, None, ["x" * 72 + "\ry\n"]),            # columna 73: gfortran lo ignora
    ("T" * 80 + "\rX\nS" + "s" * 80 + "\rY\n", 127, 72, ["T" * 80 + "\rX\n", "S" + "s" * 80 + "\r", "Y\n"]),
    ("x\n\ry", 72, None, ["x\n", "\r", "y"]),
])
def test_lone_cr_ends_a_record_within_the_columns_read(text, width, first, expected):
    recs = split_records(text, width, first_width=first)
    assert recs == expected and "".join(recs) == text
    assert split_records("a\rb\n") == ["a\rb\n"]  # sin anchura (lectura dirigida por lista): solo «\n»


def test_record_end_and_text_records():
    assert text_records("a\rb\r\nc", 72) == ["a", "b", "c"]
    assert record_end("abc\r", 72) == "\r" and record_end("abc\r\n", 72) == "\r\n" and record_end("abc", 72) == ""
    assert record_end("x" * 80 + "\r", 72) == ""  # el CR de la columna 81 no termina nada (fin de fichero)


@pytest.mark.parametrize("variant, ok", [
    ("crlf", True),
    ("cr_only", True),        # PENGEOM lee un fichero con solo CR
    ("cr_cr_lf", False),      # «\r\r\n» (Python en modo texto en Windows): un registro en blanco -> 2002
    ("cr_in_description", False),
])
def test_geo_with_lone_cr_is_read_like_pengeom(variant, ok):
    text = {"crlf": GEO.replace("\n", "\r\n"),
            "cr_only": GEO.replace("\n", "\r"),
            "cr_cr_lf": GEO.replace("\n", "\r\r\n"),
            "cr_in_description": GEO.replace("Plane Z=5", "Plane\rZ=5")}[variant]
    doc = parse_geo_text(text, "t.geo")
    assert write_geo_text(doc) == text  # sin cambios: byte a byte
    geo = compile_geometry(doc)
    assert geo.ok == ok, [str(d) for d in doc.diagnostics]
    if variant == "cr_cr_lf":
        assert 2002 in _codes(doc.diagnostics)
    if variant == "cr_in_description":
        assert 2006 in _codes(doc.diagnostics)
    lone = [d for d in doc.diagnostics if d.severity.name == "INFO" and "CR" in d.message]
    assert bool(lone) == (variant != "crlf")


@pytest.mark.parametrize("col, ok", [(60, True), (72, True), (73, False)])
def test_geo_cr_column_rule(col, ok):
    old = "SURFACE (   2)   Plane Z=5\nINDICES=( 0, 0, 0, 1, 0)\n"
    new = "SURFACE (   2)   Plane Z=5".ljust(col - 1) + "\r" + "INDICES=( 0, 0, 0, 1, 0)\n"
    doc = parse_geo_text(GEO.replace(old, new), "t.geo")
    assert compile_geometry(doc).ok == ok
    if not ok:
        assert 2006 in _codes(doc.diagnostics)


@pytest.mark.oracle
@pytest.mark.parametrize("variant", ["cr_only", "cr_cr_lf", "cr_in_description", "col60", "col72", "col73"])
def test_geo_lone_cr_agrees_with_pengeom(variant, tmp_path, need_probe):
    from argus.oracle.compare import compare_tables

    old = "SURFACE (   2)   Plane Z=5\nINDICES=( 0, 0, 0, 1, 0)\n"
    text = {"cr_only": GEO.replace("\n", "\r"), "cr_cr_lf": GEO.replace("\n", "\r\r\n"),
            "cr_in_description": GEO.replace("Plane Z=5", "Plane\rZ=5")}.get(variant)
    if text is None:
        col = int(variant[3:])
        text = GEO.replace(old, "SURFACE (   2)   Plane Z=5".ljust(col - 1) + "\rINDICES=( 0, 0, 0, 1, 0)\n")
    res = _pen_geo(text, tmp_path)
    doc = parse_geo_text(text, str(tmp_path / "t.geo"))
    geo = compile_geometry(doc)
    assert geo.ok == res.status.ok
    if not res.status.ok:
        assert res.status.iretrn in _codes(doc.diagnostics)
    else:
        assert compare_tables(geo, res.tables).ok


IN_CR_CASES = {
    "lone_cr_between_lines": MINIMAL.replace("SENERG 1.0E6\nSPOSIT 0 0 -10\n", "SENERG 1.0E6\rSPOSIT 0 0 -10\n"),
    "lone_cr_before_comment": MINIMAL.replace("SENERG 1.0E6\n", "SENERG 1.0E6\r [comment]\n"),
    "cr_only": MINIMAL.replace("\n", "\r"),
    "cr_cr_lf": MINIMAL.replace("\n", "\r\r\n"),
    "cr_at_col_7": MINIMAL.replace("SENERG 1.0E6\n", "SENERG\rSPOSIT 5 5 5\n"),
    "cr_at_col_127": MINIMAL.replace("SENERG 1.0E6\n", "SENERG 1.0E6" + " " * 114 + "\rSPOSIT 5 5 5\n"),
    "cr_at_col_128": MINIMAL.replace("SENERG 1.0E6\n", "SENERG 1.0E6" + " " * 115 + "\rSPOSIT 5 5 5\n"),
    "title_cr_col_50": MINIMAL.replace("TITLE  Prueba ARGUS\n", "TITLE  Prueba ARGUS" + " " * 30 + "\rSKPAR  1\n"),
    "title_cr_col_80": MINIMAL.replace("TITLE  Prueba ARGUS\n", "TITLE  Prueba ARGUS" + " " * 60 + "\rSKPAR  1\n"),
}


def test_in_lone_cr_ends_a_record_like_penhmain():
    doc = parse_in_text(IN_CR_CASES["lone_cr_between_lines"])
    assert doc.ok and doc.model.source.energy == 1.0e6 and doc.model.source.position == (0.0, 0.0, -10.0)
    assert [d for d in doc.diagnostics if d.severity.name == "INFO" and "CR" in d.message]
    doc = parse_in_text(IN_CR_CASES["cr_only"])
    assert doc.ok and doc.model.source.position == (0.0, 0.0, -10.0)
    assert _codes(parse_in_text(IN_CR_CASES["lone_cr_before_comment"]).diagnostics) == [3013]
    assert _codes(parse_in_text(IN_CR_CASES["cr_at_col_127"]).diagnostics) == [3013]
    assert parse_in_text(IN_CR_CASES["cr_at_col_128"]).ok
    assert _codes(parse_in_text(IN_CR_CASES["title_cr_col_50"]).diagnostics) == [3013]
    assert parse_in_text(IN_CR_CASES["title_cr_col_80"]).ok
    doc = parse_in_text(IN_CR_CASES["cr_at_col_7"])  # SENERG sin valores: error de lectura
    assert doc.diagnostics.errors and "faltan valores" in doc.diagnostics.errors[0].message
    for text in IN_CR_CASES.values():
        assert write_in_text(parse_in_text(text)) == text


@pytest.mark.oracle
@pytest.mark.parametrize("name", list(IN_CR_CASES))
def test_in_lone_cr_agrees_with_penhmain(name, need_reader_probe):
    assert not _diffs_in(IN_CR_CASES[name])


def test_editing_a_file_with_lone_cr_keeps_every_record():
    """Tras editar, el fichero guardado perdía la línea SPOSIT que iba detrás de un CR suelto."""
    text = MINIMAL.replace("\n", "\r\n").replace("SENERG 1.0E6\r\nSPOSIT", "SENERG 1.0E6\rSPOSIT")
    doc = parse_in_text(text)
    doc.set("SENERG", [2.0e6])
    out = write_in_text(doc)
    again = parse_in_text(out)
    assert again.ok and again.model.source.energy == 2.0e6 and again.model.source.position == (0.0, 0.0, -10.0)
    # Fichero solo con CR: una línea nueva no se une a la anterior.
    doc = parse_in_text(MINIMAL.replace("\n", "\r"))
    doc.set("RSEED", [5, 6])
    again = parse_in_text(write_in_text(doc))
    assert again.ok and [ln.values for ln in again.find("RSEED")] == [[5, 6]]
    assert [ln.values for ln in again.find("NSIMSH")] == [[1.0e6]]


def test_a_record_without_terminator_is_not_merged_with_a_new_line():
    """Un CR más allá de la columna 127 al final del fichero no termina el registro: hay que añadir un fin de línea."""
    text = MINIMAL.replace("NSIMSH 1.0E6\n", "NSIMSH 1.0E6" + " " * 120 + "\r")
    doc = parse_in_text(text)
    assert doc.ok
    doc.set("RSEED", [5, 6])
    again = parse_in_text(write_in_text(doc))
    assert again.ok and [ln.values for ln in again.find("RSEED")] == [[5, 6]]
    # Lo mismo en un .geo: el END sin fin de línea y un elemento modificado detrás.
    gtext = GEO.replace(END + "\n", END + " " * 20 + "\r")
    gdoc = parse_geo_text(gtext, "t.geo")
    assert gdoc.ok and write_geo_text(gdoc) == gtext


@pytest.mark.oracle
def test_edited_cr_only_in_file_agrees_with_penhmain(need_reader_probe):
    doc = parse_in_text(MINIMAL.replace("\n", "\r"))
    doc.set("SPOSIT", [1.0, 2.0, 3.0])
    doc.set("RSEED", [5, 6])
    out = write_in_text(doc)
    assert not _diffs_in(out)
    assert parse_in_text(out).model.source.position == (1.0, 2.0, 3.0)


def test_source_views_show_one_line_per_record(qtbot):
    """La línea de los mensajes es la del registro de Fortran: el texto que se muestra tiene una línea por registro."""
    from argus.penhan.inputfile import InDocument
    from argus.ui.panels import InputTextView, SourceText

    text = GEO.replace("Test geometry", "Test\rgeometry").replace("Sphere R=10", "Sphere R=10".ljust(70) + "\rx")
    doc = parse_geo_text(text, "t.geo")
    view = SourceText()
    qtbot.addWidget(view)
    view.show_document(doc)
    blocks = view.toPlainText().split("\n")
    assert len(blocks) == len(doc.lines) + 1  # + la línea vacía tras el último fin de línea
    assert blocks[1] == "geometry" and "␍" in blocks[10]  # el CR que PENGEOM ignora se ve como «␍»
    assert view.document().blockCount() == len(doc.lines) + 1

    class Ctrl:
        pass

    ctrl = Ctrl()
    ctrl.doc = parse_in_text(IN_CR_CASES["lone_cr_between_lines"])
    assert isinstance(ctrl.doc, InDocument)
    iv = InputTextView()
    qtbot.addWidget(iv)
    iv.show_input(ctrl)
    assert iv.document().blockCount() == len(ctrl.doc.lines)
    assert iv.document().findBlockByNumber(3).text() == "SPOSIT 0 0 -10"


# ---------------------------------------------------------------------------------------------------------------------
# 2. Factores de repetición: gfortran los lee sin expandirlos
# ---------------------------------------------------------------------------------------------------------------------

def test_repeat_counts_are_not_expanded():
    t0 = time.perf_counter()
    assert read_list("5000000*1.5", ["real"] * 3)[0] == [1.5, 1.5, 1.5]
    assert read_list("200000000*1.5 [comentario]", ["real"] * 3)[0] == [1.5, 1.5, 1.5]
    doc = parse_in_text(MINIMAL.replace("SPOSIT 0 0 -10", "SPOSIT 200000000*1.5"))
    assert doc.ok and doc.model.source.position == (1.5, 1.5, 1.5)
    assert time.perf_counter() - t0 < 1.0
    with pytest.raises(ListReadError, match="Repeat count overflow"):
        read_list("200000001*1.5", ["real"] * 3)
    with pytest.raises(ListReadError, match="Zero repeat count"):
        read_list("0*1 2", ["int", "int"])
    # Lo que va detrás de los valores que se leen no se interpreta (gfortran no llega a leerlo).
    assert read_list("1 2 3 0*5", ["int"] * 3)[0] == [1, 2, 3]
    assert read_list("1 2*7 99999999999*1", ["int"] * 3)[0] == [1, 7, 7]
    assert read_list("2* 5", ["int"] * 3, [9, 9, 9])[0] == [9, 9, 5]


@pytest.mark.oracle
@pytest.mark.parametrize("value", ["200000000*1.5", "200000001*1.5", "2147483648*1.5", "3*1.5 0*2"])
def test_repeat_count_limits_agree_with_penhmain(value, need_reader_probe):
    assert not _diffs_in(MINIMAL.replace("SPOSIT 0 0 -10", "SPOSIT " + value))


# ---------------------------------------------------------------------------------------------------------------------
# 13. Valores no finitos en el .geo
# ---------------------------------------------------------------------------------------------------------------------

def test_format_e22_15_refuses_non_finite_values_with_a_clear_error():
    for v in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(NonFiniteValueError) as exc:
            format_e22_15(v)
        assert isinstance(exc.value, ValueError) and "finito" in str(exc.value)


@pytest.mark.parametrize("field", ["Z-SHIFT=(", "X-SCALE=(", "    AXX=(", "X-SHIFT=("])
@pytest.mark.parametrize("value", ["1.0E+999", "Inf", "NaN", "-Infinity"])
def test_non_finite_values_in_a_geo_are_rejected_consistently(field, value):
    ok = "+1.000000000000000E+00"
    text = f"""inf
{Z}
SURFACE (   1)   plano
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=({ok},   0)
{Z}
SURFACE (   2)   esfera
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=({ok},   0)
Y-SCALE=(+5.000000000000000E+00,   0)
Z-SCALE=(+5.000000000000000E+00,   0)
{Z}
SURFACE (   3)   implicita
INDICES=( 0, 0, 0, 0, 0)
    AXX=({ok},   0)
     A0=(-1.000000000000000E+00,   0)
{Z}
MODULE  (   1)   modulo
MATERIAL(   1)
SURFACE (   2), SIDE POINTER=(-1)
1111111111111111111111111111111111111111111111111111111111111111
X-SHIFT=({ok},   0)
{Z}
{END}
"""
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith(field))
    lines[i] = f"{field}{value:>22},   0)"
    doc = parse_geo_text("\n".join(lines), "i.geo")
    errs = doc.diagnostics.errors
    assert len(errs) == 1 and errs[0].line == i and "finito" in errs[0].message
    assert parse_geo_text(text, "i.geo").ok


# ---------------------------------------------------------------------------------------------------------------------
# 14. Nombres A20 de más de 20 caracteres: format_line no los recorta en silencio
# ---------------------------------------------------------------------------------------------------------------------

def test_format_line_refuses_a20_names_longer_than_20_characters():
    with pytest.raises(ValueError, match="20 caracteres"):
        format_line(InLine(kw="GEOMFN", text="un_nombre_de_geometria_largo.geo", modified=True))
    assert format_line(InLine(kw="GEOMFN", text="12345678901234567890", modified=True)).startswith(
        "GEOMFN 12345678901234567890")
    doc = parse_in_text(MINIMAL)
    before = write_in_text(doc)
    with pytest.raises(ValueError, match="20 caracteres"):
        doc.set("GEOMFN", text="un_nombre_de_geometria_largo.geo")
    assert write_in_text(doc) == before and doc.ok  # el documento no ha cambiado


# ---------------------------------------------------------------------------------------------------------------------
# 12. Editar una línea de un bloque del .in no mueve los comentarios de en medio
# ---------------------------------------------------------------------------------------------------------------------

BLOCKS = """TITLE  prueba de comentarios
       >>>>>>>> Source definition.
SKPAR  4          [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]
SPECTR 1.0e6 0.5
       comentario entre lineas SPECTR (pico 1)
SPECTR 2.0e6 0.5
SPECTR 3.0e6 -1
SPOSIT 0 0 -10                        [Coordinates of the source]
SBOX   1 1 1
SBODY  1
       segundo cuerpo activo:
SBODY  2
SCONE  0 0 0                  [Conical beam; angles in deg]
       .
MFNAME water.mat                      [Material file, up to 20 chars]
MSIMPA 1e3 1e3 1e3 0.1 0.1 1e3 1e3
       comentario del material 1
MFNAME bone.mat
MSIMPA 1e3 1e3 1e3 0.1 0.1 1e3 1e3
       .
GEOMFN g.geo
PARINP 1 2.0            [radio]
       comentario entre PARINP
PARINP 2 3.0            [altura]
DSMAX  1 0.1
       comentario entre DSMAX
DSMAX  2 0.2
       >>>>>>>> Impact detectors.
IMPDET 0 0 100 0 1
IDBODY 1
       .
       >>>>>>>> segundo detector
IMPDET 0 0 100 0 1
IDBODY 2
       .
       >>>>>>>> Job properties.
NSIMSH 1e6
"""


def _changed(before: str, after: str) -> list[tuple[str, str, str]]:
    import difflib

    a, b = before.splitlines(), after.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append((tag, "|".join(a[i1:i2]), "|".join(b[j1:j2])))
    return out


def _spectrum(d):
    from argus.penhan import sections as sc

    sc.set_spectrum(d, [(1e6, 0.5), (2e6, 0.25), (3e6, -1)])


def _sbody(d):
    from argus.penhan import source_edit as se

    se.set_active_bodies(d, None, [2])


def _parinp(d):
    from argus.penhan import sections as sc

    sc.set_parinp(d, {1: 2.0, 2: 4.0})


def _dsmax(d):
    from argus.penhan import sections as sc

    sc.set_body_list(d, "DSMAX", [sc.BodyValue(None, 1, [0.1]), sc.BodyValue(None, 2, [0.3])])


def _impdet(d):
    from argus.penhan import sections as sc

    ds = sc.impact_detectors(d)
    ds[1].nbe = 50
    sc.set_impact_detectors(d, ds)


def _add_spectrum_line(d):
    from argus.penhan import sections as sc

    sc.set_spectrum(d, [(1e6, 0.5), (1.5e6, 0.1), (2e6, 0.5), (3e6, -1)])


@pytest.mark.parametrize("edit, expected", [
    (_spectrum, [("replace", "SPECTR 2.0e6 0.5", "SPECTR 2e6 0.25")]),
    (_sbody, [("delete", "SBODY  1", "")]),
    (_parinp, [("replace", "PARINP 2 3.0            [altura]", "PARINP 2 4.0 ")]),
    (_dsmax, [("replace", "DSMAX  2 0.2", "DSMAX  2 0.3")]),
    (_impdet, [("replace", "IMPDET 0 0 100 0 1", "IMPDET 0.0 0.0 50 0 1")]),
    (_add_spectrum_line, [("insert", "", "SPECTR 1.5e6 0.1 ")]),
])
def test_editing_one_line_of_a_block_keeps_the_comments_in_place(edit, expected):
    doc = parse_in_text(BLOCKS)
    assert doc.ok, [d.message for d in doc.diagnostics.errors]
    edit(doc)
    out = write_in_text(doc)
    changes = _changed(BLOCKS, out)
    assert [(t, a) for t, a, _ in changes] == [(t, a) for t, a, _ in expected], changes
    for (_, _, got), (_, a, want) in zip(changes, expected):
        assert got.startswith(want), (got, want)
        if "[" in a:
            assert got.endswith(a[a.index("["):]), got  # conserva el comentario de la línea que sustituye
    again = parse_in_text(out)
    assert again.ok
    if edit is _add_spectrum_line:  # la línea nueva va tras la anterior del bloque, antes del comentario
        lines = out.splitlines()
        i = lines.index(next(x for x in lines if x.startswith("SPECTR 1.5e6")))
        assert lines[i - 1] == "SPECTR 1.0e6 0.5" and lines[i + 1].startswith("       comentario entre lineas SPECTR")


# ---------------------------------------------------------------------------------------------------------------------
# 11. El escritor del .geo solo reescribe las líneas cuyos valores cambian
# ---------------------------------------------------------------------------------------------------------------------

LAYOUT = f"""Esfera con comentarios al estilo de layout.geo
{Z}
SURFACE (   1)   Esfera de radio 5 cm (tumor); el texto sigue mas alla de la columna 70 hasta aqui
INDICES=( 1, 1, 1, 0,-1)          esfera
C  el radio sale del contorneo del TAC del 12/03
X-SCALE=(+5.000000000000000E+00,   0)              (DEFAULT=1.0)
Y-SCALE=(+5.000000000000000E+00,   0)              (DEFAULT=1.0)
C  el eje z es el del haz
Z-SCALE=(+5.000000000000000E+00,   0)              (DEFAULT=1.0)
Z-SHIFT=(+1.000000000000000E+01,   0)              centro en z = 10 cm
{Z}
SURFACE (   2)   Esfera grande
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+5.000000000000000E+01,   0)
Y-SCALE=(+5.000000000000000E+01,   0)
Z-SCALE=(+5.000000000000000E+01,   0)
{Z}
BODY    (   1)   Tumor
MATERIAL(   1)                              agua
SURFACE (   1), SIDE POINTER=(-1)    dentro de la esfera
{Z}
MODULE  (   2)   Mundo
MATERIAL(   2)
SURFACE (   2), SIDE POINTER=(-1)    dentro
BODY    (   1)                       el tumor
1111111111111111111111111111111111111111111111111111111111111111
  THETA=(+0.000000000000000E+00,   0) DEG      giro
Z-SHIFT=(+0.000000000000000E+00,   0)          sin desplazar
{Z}
CLONE   (   3)   Copia del mundo
MODULE  (   2)                       el original
1111111111111111111111111111111111111111111111111111111111111111
X-SHIFT=(+2.000000000000000E+02,   0)          a la derecha
{Z}
{END}
"""


def _el(doc, kind, label):
    return next(e for e in doc.elements if e.kind.value == kind and e.label == label)


def _edit_zshift(doc):
    s = _el(doc, "SURFACE", "   1")
    next(p for p in s.params if p.name == "Z-SHIFT").value = 12.0
    s.modified = True


def _edit_clone(doc):
    c = _el(doc, "CLONE", "   3")
    c.transform[0].value = 300.0
    c.modified = True


def _edit_side(doc):
    from argus.pengeom.model import RefLine

    b = _el(doc, "BODY", "   1")
    b.items = [RefLine("SURFACE", "   1", -1)]  # el editor crea las referencias de nuevo
    b.material = 3
    b.modified = True


def _edit_module(doc):
    from argus.pengeom.model import ParamLine

    m = _el(doc, "MODULE", "   2")
    m.transform = [m.transform[0], ParamLine("X-SHIFT", 5.0), m.transform[1]]
    m.transform[0].value = 90.0
    m.modified = True


def _drop_param(doc):
    s = _el(doc, "SURFACE", "   1")
    s.params = [p for p in s.params if p.name != "Y-SCALE"]
    s.params[0].value = 6.0
    s.modified = True


def _describe(doc):
    b = _el(doc, "BODY", "   1")
    b.description = "   Tumor grande"
    b.modified = True


@pytest.mark.parametrize("edit, expected", [
    (_edit_zshift, [("Z-SHIFT=(+1.000000000000000E+01,   0)              centro en z = 10 cm",
                     "Z-SHIFT=(+1.200000000000000E+01,   0)              centro en z = 10 cm")]),
    (_edit_clone, [("X-SHIFT=(+2.000000000000000E+02,   0)          a la derecha",
                    "X-SHIFT=(+3.000000000000000E+02,   0)          a la derecha")]),
    (_edit_side, [("MATERIAL(   1)                              agua",
                   "MATERIAL(   3)                              agua")]),
    (_edit_module, [("  THETA=(+0.000000000000000E+00,   0) DEG      giro",
                     "  THETA=(+9.000000000000000E+01,   0) DEG      giro|X-SHIFT=(+5.000000000000000E+00,   0)")]),
    (_drop_param, [("X-SCALE=(+5.000000000000000E+00,   0)              (DEFAULT=1.0)|"
                    "Y-SCALE=(+5.000000000000000E+00,   0)              (DEFAULT=1.0)",
                    "X-SCALE=(+6.000000000000000E+00,   0)              (DEFAULT=1.0)")]),
    (_describe, [("BODY    (   1)   Tumor", "BODY    (   1)   Tumor grande")]),
])
def test_geo_writer_rewrites_only_the_lines_that_change(edit, expected):
    doc = parse_geo_text(LAYOUT, "l.geo")
    assert doc.ok and compile_geometry(doc).ok
    edit(doc)
    out = write_geo_text(doc)
    changes = _changed(LAYOUT, out)
    assert [(a, b) for _, a, b in changes] == expected, changes
    assert parse_geo_text(out, "l.geo").ok


@pytest.mark.oracle
@pytest.mark.parametrize("edit", [_edit_zshift, _edit_clone, _edit_side, _edit_module, _drop_param, _describe])
def test_geo_writer_result_is_read_identically_by_pengeom(edit, tmp_path, need_probe):
    from argus.oracle.compare import compare_tables

    doc = parse_geo_text(LAYOUT, "l.geo")
    edit(doc)
    out = write_geo_text(doc)
    res = _pen_geo(out, tmp_path)
    assert res.status.ok, res.status.reason
    doc2 = parse_geo_text(out, str(tmp_path / "t.geo"))
    geo = compile_geometry(doc2)
    assert geo.ok and compare_tables(geo, res.tables).ok
    # Las mismas tablas que el documento editado sin pasar por el texto.
    ref = compile_geometry(doc)
    assert [b.surfaces for b in ref.bodies] == [b.surfaces for b in geo.bodies]
    assert (ref.coefficients() == geo.coefficients()).all()


# ---------------------------------------------------------------------------------------------------------------------
# 3. PARINP: límite de líneas (3015), índices fuera de la tabla de penhmain y valores enormes
# ---------------------------------------------------------------------------------------------------------------------

def _with_after(text, keyword, new_lines):
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith(keyword))
    lines[i + 1:i + 1] = new_lines
    return "\n".join(lines)


def test_parinp_limit_counts_lines_not_indices():
    lines = ["PARINP 1 1.0"] + [f"PARINP {i} 1.0" for i in range(1, 51)]
    doc = parse_in_text(_with_after(MINIMAL, "GEOMFN", lines))
    assert _codes(doc.diagnostics) == [3015]
    assert parse_in_text(_with_after(MINIMAL, "GEOMFN", lines[1:])).ok


@pytest.mark.parametrize("ip", [51, 60000, 30000000, 2147483647])
def test_parinp_index_outside_the_table_is_an_error_and_is_stored_sparsely(ip):
    from argus.penhan.quickscan import scan_input_text

    t0 = time.perf_counter()
    text = _with_after(MINIMAL, "GEOMFN", ["PARINP 1 4.0", f"PARINP {ip} 1.0"])
    doc = parse_in_text(text)
    errs = doc.diagnostics.errors
    assert len(errs) == 1 and errs[0].code is None and "50" in errs[0].message and errs[0].line == 7
    assert doc.model.parinp == {1: 4.0, ip: 1.0}
    assert doc.model.parinp_list() == [4.0]  # penhmain no puede usar los de fuera de la tabla
    s = scan_input_text(text)
    assert s.parinp_list() == [4.0]
    assert time.perf_counter() - t0 < 1.0


def test_parinp_index_is_checked_before_the_value_is_read():
    doc = parse_in_text(_with_after(MINIMAL, "GEOMFN", ["PARINP 0 abc"]))
    assert _codes(doc.diagnostics) == [3014]


def test_the_summary_panel_shows_a_bounded_parinp_list(qtbot):
    from types import SimpleNamespace

    from argus.ui.panels import PropertiesPanel

    geo = compile_geometry(parse_geo_text(GEO, "t.geo"))
    session = SimpleNamespace(geo=geo, name="t.geo", doc=SimpleNamespace(title="t"), parinp=[1.5] * 50,
                              parinp_provisional=False, input_file=None, bounds=((0, 0, 0), (1, 1, 1)), ok=True)
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    html = panel._summary(session)
    assert len(html) < 3000 and "50 valores" in html


@pytest.mark.oracle
@pytest.mark.parametrize("lines", [["PARINP 1 1.0"] + [f"PARINP {i} 1.0" for i in range(1, 51)], ["PARINP 0 abc"]])
def test_parinp_checks_agree_with_penhmain(lines, need_reader_probe):
    assert not _diffs_in(_with_after(MINIMAL, "GEOMFN", lines))


# ---------------------------------------------------------------------------------------------------------------------
# 4. Nombres A20: solo se quitan los blancos finales y solo se leen las columnas 8-27
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line, name", [
    ("GEOMFN a.geo\t                        [Geometry file, up to 20 chars]", "a.geo\t"),
    ("GEOMFN a.geo\xa0                       [Geometry file, up to 20 chars]", "a.geo\xa0"),
    ("GEOMFN a.geo\t", "a.geo\t"),
])
def test_a20_names_keep_trailing_tabs_like_penhmain(line, name):
    doc = parse_in_text(MINIMAL.replace("GEOMFN a.geo                          [Geometry file, up to 20 chars]", line))
    assert doc.model.geomfn == name
    ln = doc.first("GEOMFN")
    assert ln.error and "columnas 8-27" in ln.error
    assert doc.diagnostics.errors


@pytest.mark.parametrize("after", ["\t[Geometry file]", "(Geometry file)", "!comentario", "[Geometry file]"])
def test_a20_only_reads_columns_8_to_27(after):
    doc = parse_in_text(MINIMAL.replace("GEOMFN a.geo                          [Geometry file, up to 20 chars]",
                                        "GEOMFN 12345678901234567890" + after))
    assert doc.ok, [d.message for d in doc.diagnostics.errors]
    assert doc.model.geomfn == "12345678901234567890"


def test_a20_name_longer_than_20_is_a_warning_not_an_error():
    doc = parse_in_text(MINIMAL.replace("GEOMFN a.geo                          [Geometry file, up to 20 chars]",
                                        "GEOMFN geometria_muy_larga_2.geo"))
    assert doc.ok and doc.model.geomfn == "geometria_muy_larga_"
    assert any("columna 27" in (d.message or "") for d in doc.diagnostics if d.severity.name == "WARNING")


def test_quickscan_trims_only_blanks(tmp_path):
    from argus.penhan.quickscan import inputs_for_geometry, scan_input_text

    (tmp_path / "a.geo").write_text(GEO)
    for line, found in [("GEOMFN a.geo                 [Geometry file]", True),
                        ("GEOMFN a.geo\t               [Geometry file]", False),
                        ("GEOMFN  a.geo                [Geometry file]", False)]:
        text = MINIMAL.replace("GEOMFN a.geo                          [Geometry file, up to 20 chars]", line)
        (tmp_path / "c.in").write_text(text, encoding="latin-1")
        assert bool(inputs_for_geometry(tmp_path / "a.geo")) == found, line
        assert scan_input_text(text).geomfn == line[7:27].rstrip(" ")


@pytest.mark.oracle
def test_probe_values_keep_trailing_tabs(need_reader_probe):
    text = MINIMAL.replace("GEOMFN a.geo                          [Geometry file, up to 20 chars]", "GEOMFN a.geo\t")
    assert _pen_in(text).values["GEOMFN"] == ["a.geo\t"]
    assert not _diffs_in(text.replace("\t", "\t" + " " * 15 + "(x)"))


# ---------------------------------------------------------------------------------------------------------------------
# 5. SRNUCL (CHARACTER*45) leído como gfortran
# ---------------------------------------------------------------------------------------------------------------------

NUC = ("TITLE  nuc\nSKPAR  0\nSRNUCL {v}\nSPOSIT 0 0 0\n"
       "MFNAME water.mat                      [Material file, up to 20 chars]\n"
       "GEOMFN a.geo                          [Geometry file, up to 20 chars]\n")


@pytest.mark.parametrize("value, name, error, warning", [
    ("'Co-60.nuc", "Co-60.nuc", False, False),               # sin la comilla final: hasta el final (sin blancos)
    ("2*'Co 60.nuc'", "Co 60.nuc", False, False),            # factor de repetición y comillas
    ("A" * 50, "A" * 45, False, True),                       # penhmain lee 45 caracteres
    ("'" + "B" * 50 + "'", "B" * 45, False, True),
    ("'x'y", None, True, False),                             # gfortran: «Invalid string input»
    ("'Co-60.nuc'x.nuc", None, True, False),
    ("'ab'  cd", "ab", False, False),
    ("ab'cd'", "ab'cd'", False, False),
])
def test_srnucl_is_read_like_gfortran(value, name, error, warning):
    doc = parse_in_text(NUC.format(v=value))
    ln = doc.first("SRNUCL")
    assert bool(ln.error) == error, ln.error
    if not error:
        assert doc.model.source.nuclide == name
        assert bool(ln.warning) == warning and (not warning or "45" in ln.warning)


def test_srnucl_longer_than_45_is_refused_when_writing():
    doc = parse_in_text(NUC.format(v="Co-60.nuc"))
    with pytest.raises(ValueError, match="45"):
        doc.set("SRNUCL", text="nucleido_con_un_nombre_muy_largo_de_mas_de_45_caracteres.nuc")
    assert doc.model.source.nuclide == "Co-60.nuc"


@pytest.mark.oracle
@pytest.mark.parametrize("value", ["'Co-60.nuc", "2*'Co 60.nuc'", "A" * 50, "'x'y", "'ab'  cd", "ab'cd'"])
def test_srnucl_agrees_with_penhmain(value, need_reader_probe):
    text = NUC.format(v=value)
    assert not _diffs_in(text)
    res = _pen_in(text)
    if not res.crashed:
        assert res.values["SRNUCL"] == [parse_in_text(text).model.source.nuclide]


# ---------------------------------------------------------------------------------------------------------------------
# 6. Valores nulos: la variable conserva su valor (penhmain lee en variables que persisten)
# ---------------------------------------------------------------------------------------------------------------------

def test_null_seed_keeps_the_previous_rseed_value():
    from argus.penhan import sections as sc

    doc = parse_in_text(MINIMAL + "RSEED  5 6\nRSEED  , 7\n")
    assert doc.ok and sc.seeds(doc) == [(5, 6), (5, 7)]
    assert any("anterior" in d.message for d in doc.diagnostics if d.severity.name == "WARNING")
    assert sc.seeds(parse_in_text(MINIMAL + "RSEED  , 7\n")) == [(1, 7)]  # sin línea anterior: el valor inicial


def test_null_idkpar_keeps_the_previous_particle():
    from argus.penhan import sections as sc

    text = MINIMAL.replace("NSIMSH 1.0E6\n", "IMPDET 0 0 100 0 1\nIDBODY 1\nIDKPAR 2\nIDKPAR ,\nNSIMSH 1.0E6\n")
    doc = parse_in_text(text)
    assert doc.ok, [d.message for d in doc.diagnostics.errors]
    assert sc.impact_detectors(doc)[0].kpar == [2, 2]
    first = parse_in_text(text.replace("IDKPAR 2\n", ""))  # nulo sin valor anterior: indefinido
    assert first.diagnostics.errors and first.diagnostics.errors[0].code is None


def test_the_null_value_warning_survives_a_new_analysis():
    """Revisión final: el aviso de un valor nulo se da en cada análisis (antes solo en el primero) y con el valor
    que tenga ahora la línea anterior."""
    from argus.penhan import sections as sc

    doc = parse_in_text(MINIMAL + "RSEED  5 6\nRSEED  , 7\n")

    def notes():
        return [d.message for d in doc.diagnostics if d.severity.name == "WARNING" and "valor nulo" in d.message]

    assert len(notes()) == 1 and "5, 7" in notes()[0]
    doc.analyze()
    doc.modify(doc.find("RSEED ")[0], [8, 6])  # la línea anterior cambia: el nulo conserva el valor nuevo
    assert sc.seeds(doc) == [(8, 6), (8, 7)] and len(notes()) == 1 and "8, 7" in notes()[0]
    assert "RSEED  , 7" in doc.text()  # la línea con el nulo no se toca


def test_null_parinp_index_is_an_error():
    doc = parse_in_text(_with_after(MINIMAL, "GEOMFN", ["PARINP 3 1.0", "PARINP ,2.0"]))
    errs = doc.diagnostics.errors
    assert len(errs) == 1 and errs[0].code is None and errs[0].line == 7
    assert doc.model.parinp == {3: 1.0}


@pytest.mark.oracle
@pytest.mark.parametrize("extra", ["RSEED  5 6\nRSEED  , 7\n", "IMPDET 0 0 100 0 1\nIDBODY 1\nIDKPAR 2\nIDKPAR ,\n"])
def test_null_values_agree_with_penhmain(extra, need_reader_probe):
    from argus.oracle.compare_in import compare_sections

    text = MINIMAL.replace("NSIMSH 1.0E6\n", "") + extra
    doc = parse_in_text(text)
    res = _pen_in(text)
    assert not _diffs_in(text)
    assert not compare_sections(doc, res)


@pytest.mark.parametrize("buf, kinds, ok", [
    ("2*1", ["int", "real"], False),  # gfortran: «Read type INTEGER where REAL was expected for item 2»
    ("2*1.5", ["real", "int"], False),
    ("2*1", ["str", "int"], False),
    ("2* 5", ["int", "real"], True),  # nulos: sin tipo
    ("3*1 4", ["int", "int", "int"], True),
    ("1*1 3", ["int", "real"], True),
    ("2*ab", ["str", "str"], True),
])
def test_a_repeat_count_cannot_span_two_types(buf, kinds, ok):
    """Revisión final: como gfortran (comprobado con un programa de prueba), un factor de repetición solo vale para
    variables del mismo tipo."""
    if ok:
        read_list(buf, kinds, [7, 7.0, 0][:len(kinds)])
    else:
        with pytest.raises(ListReadError, match="Read type"):
            read_list(buf, kinds, [7, 7.0, 0][:len(kinds)])


def test_a_repeat_count_in_a_voxel_of_the_ct_file_is_an_error():
    from argus.pengeom.ct import CtFormatError, read_ct_text

    with pytest.raises(CtFormatError, match="INTEGER where REAL"):
        read_ct_text("t\n1 1 1\n1 1 1\n2*1\n")  # READ(IRJ,*) MC(IA),DC(IA)
    ct = read_ct_text("t\n3*2.0\n1 1 1\n1 2*0.5\n")  # XM, YM, ZM: los tres reales
    assert ct.size == (2.0, 2.0, 2.0) and float(ct.density.ravel()[0]) == 0.5


@pytest.mark.oracle
@pytest.mark.parametrize("line, ok", [
    ("IMPDET 0 2*1E5 0 1", False),  # EU (real) y NBE (entero)
    ("IMPDET 2*0 100 0 1", True),  # EL y EU, reales
    ("IMPDET 0 0 100 2*0", True),  # IPSF e IDCUT, enteros
])
def test_repeat_counts_across_types_agree_with_penhmain(line, ok, need_reader_probe):
    text = MINIMAL.replace("NSIMSH 1.0E6\n", f"{line}\nIDBODY 1\nNSIMSH 1.0E6\n")
    assert parse_in_text(text).ok == ok
    assert not _diffs_in(text)


# ---------------------------------------------------------------------------------------------------------------------
# 7. Coma decimal: «SPOSIT 0,5 0 -10» se lee (0, 5, 0)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line, warn", [
    ("SPOSIT 0,5 0 -10", True),
    ("SPOSIT 0 0 -10,5", True),
    ("SPOSIT 1,5 2,5 3,5", True),
    ("SENERG 1,5E6", True),
    ("SPOSIT 0,0,1", False),
    ("SPOSIT 0, 0, -10", False),
    ("SPOSIT 0,0,-10 [a,b 1,5]", False),
    ("SPOSIT 0 0 -10    [Coordinates of the source]", False),
])
def test_decimal_comma_warning(line, warn):
    key = line[:6]
    text = "\n".join(line if ln.startswith(key) else ln for ln in MINIMAL.split("\n"))
    doc = parse_in_text(text)
    msgs = [d.message for d in doc.diagnostics if d.severity.name == "WARNING" and "coma decimal" in d.message]
    assert bool(msgs) == warn, msgs
    if line == "SPOSIT 0,5 0 -10":
        assert doc.model.source.position == (0.0, 5.0, 0.0)
        assert "0 y 5" in msgs[0] and "punto" in msgs[0]


# ---------------------------------------------------------------------------------------------------------------------
# 10. Superficie implícita con los 10 coeficientes nulos; sondas compiladas como indica penhmain.f (-Os)
# ---------------------------------------------------------------------------------------------------------------------

ZERO_SURFACE = f"SURFACE (  99)   implicita sin coeficientes\nINDICES=( 0, 0, 0, 0, 0)\n{Z}\n"
BALL = (f"Ball\n{Z}\nSURFACE (   1)   plano z=0\nINDICES=( 0, 0, 0, 1, 0)\n{Z}\n"
        "SURFACE (   2)   esfera r=5\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+5.000000000000000E+00,   0)\n"
        f"Y-SCALE=(+5.000000000000000E+00,   0)\nZ-SCALE=(+5.000000000000000E+00,   0)\n{Z}\n" + ZERO_SURFACE +
        "BODY    (   1)   bola\nMATERIAL(   1)\nSURFACE (   2), SIDE POINTER=(-1)\nSURFACE (  99), SIDE POINTER=(+1)\n"
        f"{Z}\n{END}\n")


def _zero_warnings(geo):
    return [d for d in geo.diagnostics if d.severity.name == "WARNING" and "coeficientes" in d.message]


def test_an_implicit_surface_with_all_coefficients_zero_is_warned():
    geo = compile_geometry(parse_geo_text(BALL, "b.geo"))
    assert geo.ok and len(_zero_warnings(geo)) == 1 and "99" in _zero_warnings(geo)[0].message
    with_parinp = BALL.replace(ZERO_SURFACE, "SURFACE (  99)   con PARINP\nINDICES=( 0, 0, 0, 0, 0)\n"
                               "    AZZ=(+1.000000000000000E+00,   1)\n     A0=(-1.000000000000000E+00,   2)\n"
                               f"{Z}\n")
    doc = parse_geo_text(with_parinp, "b.geo")
    assert _zero_warnings(compile_geometry(doc, parinp=[0.0, 0.0]))
    assert not _zero_warnings(compile_geometry(doc, parinp=[1.0, -1.0]))
    assert not _zero_warnings(compile_geometry(parse_geo_text(GEO, "t.geo")))


def test_probes_are_built_with_the_flags_penhmain_documents(tmp_path, monkeypatch):
    """penhmain.f indica «gfortran -Wall -Os»; con -O2, GEOMIN trata distinto una superficie nula."""
    from pathlib import Path

    from argus.oracle import probe

    programs = Path(__file__).resolve().parents[2] / "ref" / "SOBP_Investigacion" / "ProgramasBase" / "penhan" / \
        "programs"
    if not (programs / "penhmain.f").is_file() or probe.shutil.which("gfortran") is None:
        pytest.skip("Sin penhmain.f o sin gfortran")
    cmds = []
    monkeypatch.setattr(probe, "_run_compiler", lambda cmd, cwd=None: cmds.append(cmd))
    probe.build_probe(programs / "pengeom.f", tmp_path)
    probe.build_reader_probe(programs / "penhmain.f", tmp_path)
    probe.build_sim_probe(programs / "penhmain.f", tmp_path)
    compiles = [c for c in cmds if "-c" in c]
    assert len(compiles) == 3 + 4 + 3
    assert all("-Os" in c and not {"-O0", "-O1", "-O2", "-O3"} & set(c) for c in compiles)
    assert probe.probe_flags(tmp_path / probe.PROBE_NAME) == ("-Os",)


@pytest.mark.oracle
def test_zero_surface_agrees_with_pengeom_built_with_os(tmp_path, need_probe):
    from argus.oracle.compare import compare_tables
    from argus.oracle.probe import PROBE_FLAGS, find_probe, probe_flags

    if probe_flags(find_probe()) != PROBE_FLAGS:
        pytest.skip("La sonda PENGEOM no está compilada con -Os (python tools/build_probe.py)")
    res = _pen_geo(BALL, tmp_path)
    geo = compile_geometry(parse_geo_text(BALL, str(tmp_path / "t.geo")))
    assert res.status.ok and geo.ok and compare_tables(geo, res.tables).ok


# ---------------------------------------------------------------------------------------------------------------------
# 9. CLONE de un módulo sin hijas: la copia se renombra y no puede quedar registrada con la etiqueta en blanco
# ---------------------------------------------------------------------------------------------------------------------

def _sphere(label, r, x=0.0):
    s = f"SURFACE ({label:>4})   esfera\nINDICES=( 1, 1, 1, 0,-1)\n"
    for k in "XYZ":
        s += f"{k}-SCALE=({r:+22.15E},   0)\n"
    if x:
        s += f"X-SHIFT=({x:+22.15E},   0)\n"
    return s + Z + "\n"


EMPTY_MODULE_CLONE = (f"Etiquetas en blanco\n{Z}\n" + _sphere("1", 1.0)
                      + f"MODULE  (   1)   modulo sin hijas\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n{Z}\n"
                      "CLONE   (  C1)   copia\nMODULE  (   1)\n" + "1" * 64 + f"\nX-SHIFT=({5.0:+22.15E},   0)\n{Z}\n")
BLANK_BODY = (EMPTY_MODULE_CLONE + _sphere("2", 1.0, x=-5.0)
              + f"BODY    (    )   etiqueta en blanco\nMATERIAL(   2)\nSURFACE (   2), SIDE POINTER=(-1)\n{Z}\n{END}\n")
BLANK_MODULE_REF = (EMPTY_MODULE_CLONE + _sphere("9", 20.0)
                    + "MODULE  (   9)   mundo\nMATERIAL(   0)\nSURFACE (   9), SIDE POINTER=(-1)\n"
                      f"MODULE  (    )\nMODULE  (   1)\n{Z}\n{END}\n")


def test_clone_of_a_module_without_daughters_leaves_no_blank_label():
    geo = compile_geometry(parse_geo_text(BLANK_BODY, "c.geo"))
    assert geo.ok, [str(d) for d in geo.diagnostics]
    geo = compile_geometry(parse_geo_text(BLANK_MODULE_REF, "c.geo"))
    assert not geo.ok and _codes(geo.diagnostics) == [2044]


@pytest.mark.oracle
@pytest.mark.parametrize("text", [BLANK_BODY, BLANK_MODULE_REF], ids=["blank_body", "blank_module_ref"])
def test_clone_blank_labels_agree_with_pengeom(text, tmp_path, need_probe):
    from argus.oracle.compare import compare_tables

    res = _pen_geo(text, tmp_path)
    geo = compile_geometry(parse_geo_text(text, str(tmp_path / "t.geo")))
    assert geo.ok == res.status.ok
    if res.status.ok:
        assert compare_tables(geo, res.tables).ok
    else:
        assert _codes(geo.diagnostics) == [res.status.iretrn]


# ---------------------------------------------------------------------------------------------------------------------
# 8. Ficheros .ct: formas dirigidas por lista que GEOMIN lee
# ---------------------------------------------------------------------------------------------------------------------

def _voxels(n, seed=8):
    import numpy as np

    rng = np.random.default_rng(seed)
    return [f"{int(rng.integers(1, 4))} {rng.uniform(0.5, 2.0):.4E}\n" for _ in range(n)]


CT_CASES = {
    "repeat_header": "titulo\n 3*2.0\n 3*2\n" + "".join(_voxels(8)),
    "repeat_voxel": "titulo\n 2.0 2.0 2.0\n 2 2 2\n" + "".join("1*" + x for x in _voxels(8)),
    "exponent_without_letter": ("titulo\n 2.0 2.0 2.0\n 2 2 2\n"
                                + "".join(x.split()[0] + " 1.5+0\n" for x in _voxels(8))),
    "long_title": "t" * 5000 + "\n 2.0 2.0 2.0\n 2 2 2\n" + "".join(_voxels(8)),
    "cr_in_title": "titulo\r 2.0 2.0 2.0\n 2 2 2\n" + "".join(_voxels(8)),
    "values_split_over_lines": "titulo\n 2.0\n 2.0, 2.0\n 2 2\n 2\n" + "".join(_voxels(8)),
}


@pytest.mark.parametrize("name", list(CT_CASES))
def test_ct_list_directed_forms_are_read(name):
    from argus.pengeom.ct import read_ct_text

    ct = read_ct_text(CT_CASES[name])
    assert ct.size == (2.0, 2.0, 2.0) and ct.shape == (2, 2, 2)
    ref = [x.split() for x in _voxels(8)]
    assert list(ct.material.ravel()) == [int(m) for m, _ in ref]
    if name == "exponent_without_letter":
        assert (ct.density == 1.5).all()
    else:
        assert list(ct.density.ravel()) == [float(d) for _, d in ref]
    assert ct.title == ("t" * 72 if name == "long_title" else "titulo")


@pytest.mark.parametrize("text, message", [
    ("titulo\n 0*2.0 2.0 2.0\n 2 2 2\n", "Zero repeat count"),
    ("titulo\n 2.0,,2.0\n 2 2 2\n", "nulo"),
    ("titulo\n 2.0 2.0 2.0\n 2 2 2\n1 1.0\n1 1.0\n2*\n", "nulo"),
    ("titulo\n 2.0 2.0 Inf\n 2 2 2\n", "finit"),
])
def test_ct_values_gfortran_would_not_define_are_errors(text, message):
    from argus.pengeom.ct import CtFormatError, read_ct_text

    with pytest.raises(CtFormatError, match=message):
        read_ct_text(text + "".join(_voxels(8)))


def test_ct_fast_path_is_still_used_for_the_usual_layout(monkeypatch):
    import numpy as np

    from argus.pengeom import ct as ctmod

    def boom(*a, **k):
        raise AssertionError("lectura lenta")

    monkeypatch.setattr(ctmod, "_slow_body", boom)
    n = 64
    mat = np.random.default_rng(1).integers(0, 5, size=(n, n, n))
    den = np.random.default_rng(2).uniform(0.1, 2.0, size=(n, n, n))
    raw = ctmod.format_ct("x" * 5000, (1.0, 1.0, 1.0), mat, den)
    t0 = time.perf_counter()
    ct = ctmod.read_ct_bytes(raw)
    assert time.perf_counter() - t0 < 2.0
    assert np.array_equal(ct.material, mat) and ct.title == "x" * 72


@pytest.mark.oracle
@pytest.mark.parametrize("name", ["repeat_header", "repeat_voxel", "exponent_without_letter", "long_title",
                                  "cr_in_title", "values_split_over_lines"])
def test_ct_forms_agree_with_pengeom(name, tmp_path, need_probe):
    import numpy as np

    from argus.oracle.probe import run_probe
    from argus.pengeom.ct import clear_cache, voxel_values

    clear_cache()
    (tmp_path / "t.geo").write_text(f"CT\n{Z}\nCTBOX   ( CTB)   CT\nCTFNAME=(t.ct        )\n{Z}\n{END}\n")
    (tmp_path / "t.ct").write_bytes(CT_CASES[name].encode("latin-1"))
    geo = compile_geometry(parse_geo_text((tmp_path / "t.geo").read_text(), str(tmp_path / "t.geo")),
                           base_dir=tmp_path)
    assert geo.ok, [str(d) for d in geo.diagnostics]
    rng = np.random.default_rng(1)
    pts = rng.uniform(0.01, 1.99, size=(200, 3))
    dirs = np.tile([0.0, 0.0, 1.0], (200, 1))
    res = run_probe(tmp_path / "t.geo", tables=False, ct_points=np.hstack([pts, dirs]))
    assert res.status.ok
    m, d = voxel_values(geo, pts, dirs)
    assert np.array_equal(m, res.ct_voxels["mat"]) and np.array_equal(d, res.ct_voxels["dens"])
