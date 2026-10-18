from pathlib import Path

import numpy as np
import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.model import (
    BodyElement,
    ElementKind,
    IncludeElement,
    ParamLine,
    RefLine,
    SurfaceElement,
)
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from conftest import CORPUS, MANIFEST, MANIFEST_IDS


@pytest.mark.parametrize("item", MANIFEST, ids=MANIFEST_IDS)
def test_unmodified_roundtrip_is_byte_identical(item):
    path = CORPUS / item["path"]
    raw = path.read_bytes().decode("latin-1")
    doc = load_geometry(path)
    assert write_geo_text(doc) == raw


def _same_tables(g1, g2):
    assert g1.ok and g2.ok
    assert np.array_equal(g1.coefficients(), g2.coefficients())
    for a, b in zip(g1.bodies, g2.bodies, strict=True):
        assert (a.surfaces, a.flags, a.daughters, a.limiting, a.material, a.mother, a.is_module) == \
               (b.surfaces, b.flags, b.daughters, b.limiting, b.material, b.mother, b.is_module)


@pytest.mark.parametrize("item", MANIFEST, ids=MANIFEST_IDS)
def test_regenerated_text_compiles_to_same_tables(item):
    path = CORPUS / item["path"]
    doc = load_geometry(path)
    for el in doc.elements:
        el.modified = True
    text = write_geo_text(doc)
    doc2 = parse_geo_text(text, str(path))
    assert doc2.ok, [str(d) for d in doc2.diagnostics]
    for e1, e2 in zip(doc.elements, doc2.elements, strict=True):
        if isinstance(e2, IncludeElement):
            e2.document = e1.document
    g1 = compile_geometry(load_geometry(path), parinp=item.get("parinp"))
    g2 = compile_geometry(doc2, parinp=item.get("parinp"), base_dir=Path(path).parent)
    _same_tables(g1, g2)


def test_new_elements_are_written_in_pengeom_format():
    doc = parse_geo_text("Nueva\n" + "0" * 64 + "\nEND      " + "0" * 55 + "\n")
    s = SurfaceElement(ElementKind.SURFACE, label="   1", description="   Esfera R=3",
                       indices=(1, 1, 1, 0, -1),
                       params=[ParamLine("X-SCALE", 3.0), ParamLine("Y-SCALE", 3.0), ParamLine("Z-SCALE", 3.0),
                               ParamLine("THETA", 90.0)])
    b = BodyElement(ElementKind.BODY, label="   1", description="   Bola", material=2,
                    items=[RefLine("SURFACE", "   1", -1)])
    doc.elements[0:0] = [s, b]
    text = write_geo_text(doc)
    lines = text.splitlines()
    assert "SURFACE (   1)   Esfera R=3" in lines
    assert "INDICES=( 1, 1, 1, 0,-1)" in lines
    assert "X-SCALE=(+3.000000000000000E+00,   0)" in lines
    assert "  THETA=(+9.000000000000000E+01,   0) DEG" in lines
    assert "MATERIAL(   2)" in lines
    assert "SURFACE (   1), SIDE POINTER=(-1)" in lines
    doc2 = parse_geo_text(text)
    assert doc2.ok
    g = compile_geometry(doc2)
    assert g.ok and g.nbodys == 2  # cuerpo + envolvente


def test_modified_element_keeps_comments():
    text = ("T\n" + "0" * 64 + "\nC antes\nSURFACE (   1)   plano\nC dentro\nINDICES=( 0, 0, 0, 1, 0)\n"
            + "0" * 64 + "\nEND      " + "0" * 55 + "\n")
    doc = parse_geo_text(text)
    s = doc.elements[0]
    s.params.append(ParamLine("Z-SHIFT", 2.0))
    s.modified = True
    out = write_geo_text(doc).splitlines()
    assert out[2] == "C antes" and out[3].startswith("SURFACE (   1)") and out[4] == "C dentro"
    assert "Z-SHIFT=(+2.000000000000000E+00,   0)" in out
