from collections import Counter

import pytest

from argus.pengeom.model import BodyElement, SurfaceElement
from argus.pengeom.parser import load_geometry, parse_geo_text
from conftest import CORPUS, MANIFEST, MANIFEST_IDS

SEP = "0" * 64
END = "END      " + "0" * 55


def geo(*lines: str) -> str:
    return "\n".join(["Prueba", SEP, *lines]) + "\n"


SPHERE = ["SURFACE (   1)   esfera", "INDICES=( 1, 1, 1, 0,-1)",
          "X-SCALE=(+2.000000000000000E+00,   0)", "Y-SCALE=(+2.000000000000000E+00,   0)",
          "Z-SCALE=(+2.000000000000000E+00,   0)", SEP]
BODY = ["BODY    (   1)   bola", "MATERIAL(   1)", "SURFACE (   1), SIDE POINTER=(-1)", SEP]


@pytest.mark.parametrize("item", MANIFEST, ids=MANIFEST_IDS)
def test_corpus_parses_without_diagnostics(item):
    doc = load_geometry(CORPUS / item["path"])
    assert not doc.diagnostics.has_errors, [str(d) for d in doc.diagnostics]


@pytest.mark.parametrize("name,counts", [
    ("pengeomjar/mlc", {"SurfaceElement": 1983, "ModuleElement": 524}),
    ("pengeomjar/chair", {"SurfaceElement": 21, "BodyElement": 6, "ModuleElement": 3, "CloneElement": 1}),
    ("pengeomjar/example2", {"IncludeElement": 1, "SurfaceElement": 3, "ModuleElement": 2, "CloneElement": 1}),
    ("pengeomjar/ctbox", {"SurfaceElement": 4, "ModuleElement": 2, "CtBoxElement": 1}),
])
def test_corpus_element_counts(name, counts):
    doc = load_geometry(CORPUS / name)
    c = Counter(type(e).__name__ for e in doc.elements)
    for k, v in counts.items():
        assert c[k] == v


def test_include_is_loaded():
    doc = load_geometry(CORPUS / "pengeomjar/example2")
    inc = doc.elements[0]
    assert inc.starred and inc.filename == "example1" and inc.document is not None
    assert len(inc.document.surfaces()) > 0


def test_valid_minimal_geometry():
    doc = parse_geo_text(geo(*SPHERE, *BODY, END))
    assert doc.ok
    s = doc.elements[0]
    assert isinstance(s, SurfaceElement) and s.indices == (1, 1, 1, 0, -1)
    assert s.param("X-SCALE", 1.0) == 2.0
    b = doc.elements[1]
    assert isinstance(b, BodyElement) and b.material == 1 and b.items[0].side == -1


def codes(doc):
    return [d.code for d in doc.diagnostics.errors]


def test_bad_label_format():
    doc = parse_geo_text(geo("SURFACE (  1)   mal", "INDICES=( 1, 1, 1, 0,-1)", SEP, END))
    assert 2003 in codes(doc)


def test_bad_indices():
    doc = parse_geo_text(geo("SURFACE (   1)", "INDICES=( 2, 1, 1, 0,-1)", SEP, END))
    assert 2007 in codes(doc)
    doc = parse_geo_text(geo("SURFACE (   1)", "INDICES=( 1; 1, 1, 0,-1)", SEP, END))
    assert 2006 in codes(doc)


def test_unknown_keyword():
    doc = parse_geo_text(geo("SPHERE  (   1)", SEP, END))
    assert 2002 in codes(doc)


def test_misaligned_parameter_keyword():
    doc = parse_geo_text(geo("SURFACE (   1)", "INDICES=( 1, 1, 1, 0,-1)",
                             "THETA=  (+1.000000000000000E+00,   0)", SEP, END))
    assert 2012 in codes(doc)
    msg = doc.diagnostics.errors[0].message
    assert "alineada a la derecha" in msg


def test_scale_too_small_unless_parinp():
    doc = parse_geo_text(geo("SURFACE (   1)", "INDICES=( 1, 1, 1, 0,-1)",
                             "X-SCALE=(+0.000000000000000E+00,   0)", SEP, END))
    assert 2009 in codes(doc)
    # Con IPAR > 0 el valor del fichero no se usa: no es error de lectura.
    doc = parse_geo_text(geo("SURFACE (   1)", "INDICES=( 1, 1, 1, 0,-1)",
                             "X-SCALE=(+0.000000000000000E+00,   1)", SEP, END))
    assert not doc.diagnostics.has_errors


def test_value_without_decimal_point_warns():
    doc = parse_geo_text(geo("SURFACE (   1)", "INDICES=( 1, 1, 1, 0,-1)",
                             "X-SCALE=(250                   ,   0)", SEP, *BODY, END))
    warns = [d for d in doc.diagnostics if d.severity == 1]
    assert any("decimales" in d.message for d in warns)
    assert doc.elements[0].params[0].value == pytest.approx(2.5e-13)


def test_missing_end():
    doc = parse_geo_text(geo(*SPHERE, *BODY))
    assert doc.diagnostics.has_errors


def test_comments_are_skipped():
    doc = parse_geo_text(geo("C comentario", "# otro", *SPHERE[:2], "C dentro", *SPHERE[2:], *BODY, END))
    assert doc.ok and len(doc.surfaces()) == 1
    assert len(doc.surfaces()[0].params) == 3


def test_crlf_and_side_pointer_columns():
    text = geo(*SPHERE, *BODY, END).replace("\n", "\r\n")
    doc = parse_geo_text(text)
    assert doc.ok and doc.newline == "\r\n"
    assert doc.bodies()[0].items[0].side == -1
