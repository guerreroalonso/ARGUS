"""Novena revisión (0.16.3): PENGEOM y el editor de geometría.

* Convertir en copia independiente un clon que otro CLONE copia: las copias de dentro del segundo clon siguen siendo
  los mismos cuerpos (el .in no pierde sus líneas).
* «Convertir en módulo» y «Envolver» con un limitante de un fichero INCLUDE*: también sus limitantes van al módulo
  (si no, GEOMIN les daba madre sin hacerlos hijas y LOCATE ya no los encontraba).
* Las etiquetas, con sus cuatro columnas, como PENGEOM («   1» y «1   » son etiquetas distintas).
* Duplicar avisa de los valores que usaban PARINP y en la copia son números.
* «copias independientes» en plural.
"""

from __future__ import annotations

import numpy as np

from argus.pengeom import edit as ge
from argus.pengeom import surfaces as sf
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.duplicate import duplicate, materialize_clone
from argus.pengeom.edit import check_convert, compile_checked, convert_to_module, reparse
from argus.pengeom.extent import element_extent, members_box
from argus.pengeom.identity import renumber_map
from argus.pengeom.locate import Locator
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.wrap import check_wrap, fitted_primitive, sample_solids, wrap
from argus.pengeom.writer import write_geo_text

Z = "0" * 64
O = "1" * 64
END = "END      " + "0" * 55

CLONE_OF_CLONE = f"""clone of a clone
{Z}
SURFACE (   1)   ball
INDICES=( 1, 1, 1, 0,-1)
X-SHIFT=(+5.000000000000000E-01,   0)
{Z}
SURFACE (   2)   box x
INDICES=( 1, 0, 0, 0,-1)
X-SCALE=(+2.000000000000000E+00,   0)
{Z}
SURFACE (   3)   box y
INDICES=( 0, 1, 0, 0,-1)
Y-SCALE=(+2.000000000000000E+00,   0)
{Z}
SURFACE (   4)   box z
INDICES=( 0, 0, 1, 0,-1)
Z-SCALE=(+2.000000000000000E+00,   0)
{Z}
SURFACE (   5)   world
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+5.000000000000000E+01,   0)
Y-SCALE=(+5.000000000000000E+01,   0)
Z-SCALE=(+5.000000000000000E+01,   0)
{Z}
BODY    (   1)   ball
MATERIAL(   2)
SURFACE (   1), SIDE POINTER=(-1)
{Z}
MODULE  (   2)   box module
MATERIAL(   1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
SURFACE (   4), SIDE POINTER=(-1)
BODY    (   1)
{O}
  OMEGA=(+1.500000000000000E+01,   0) DEG
  THETA=(+2.500000000000000E+01,   0) DEG
X-SHIFT=(+1.000000000000000E+00,   0)
{Z}
CLONE   (   3)   first clone
MODULE  (   2)
{O}
    PHI=(+3.000000000000000E+01,   0) DEG
Y-SHIFT=(+1.000000000000000E+01,   0)
{Z}
CLONE   (   4)   clone of the clone
MODULE  (   3)
{O}
  THETA=(+4.000000000000000E+01,   0) DEG
Z-SHIFT=(+1.200000000000000E+01,   0)
{Z}
MODULE  (   5)   world (rotated)
MATERIAL(   3)
SURFACE (   5), SIDE POINTER=(-1)
MODULE  (   2)
MODULE  (   3)
MODULE  (   4)
{O}
  OMEGA=(+1.000000000000000E+01,   0) DEG
    PHI=(-2.000000000000000E+01,   0) DEG
X-SHIFT=(+2.000000000000000E+00,   0)
{Z}
{END}
"""

MAIN_WITH_INCLUDE = f"""main with INCLUDE*
{Z}
INCLUDE*
   FILE=(inc.geo     )
{Z}
SURFACE (  10)   big sphere
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+1.000000000000000E+01,   0)
Y-SCALE=(+1.000000000000000E+01,   0)
Z-SCALE=(+1.000000000000000E+01,   0)
{Z}
BODY    (  10)   X
MATERIAL(   1)
SURFACE (  10), SIDE POINTER=(-1)
BODY    (   1)
{Z}
{END}
"""
INCLUDED = f"""included detector
{Z}
SURFACE (   1)   sphere r=3
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+3.000000000000000E+00,   0)
Y-SCALE=(+3.000000000000000E+00,   0)
Z-SCALE=(+3.000000000000000E+00,   0)
{Z}
SURFACE (   2)   sphere r=1
INDICES=( 1, 1, 1, 0,-1)
{Z}
BODY    (   2)   Z
MATERIAL(   3)
SURFACE (   2), SIDE POINTER=(-1)
{Z}
BODY    (   1)   Y
MATERIAL(   2)
SURFACE (   1), SIDE POINTER=(-1)
BODY    (   2)
{Z}
{END}
"""

COLUMNS = f"""two labels that differ only in their columns
{Z}
SURFACE (   1)   sphere r=1 at x=-3
INDICES=( 1, 1, 1, 0,-1)
X-SHIFT=(-3.000000000000000E+00,   0)
{Z}
SURFACE (1   )   sphere r=1 at x=+3
INDICES=( 1, 1, 1, 0,-1)
X-SHIFT=(+3.000000000000000E+00,   0)
{Z}
SURFACE (   9)   module sphere r=8
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+8.000000000000000E+00,   0)
Y-SCALE=(+8.000000000000000E+00,   0)
Z-SCALE=(+8.000000000000000E+00,   0)
{Z}
BODY    (   1)   right-aligned label
MATERIAL(   2)
SURFACE (   1), SIDE POINTER=(-1)
{Z}
BODY    (1   )   left-aligned label
MATERIAL(   3)
SURFACE (1   ), SIDE POINTER=(-1)
{Z}
MODULE  (   9)   shifted module
MATERIAL(   1)
SURFACE (   9), SIDE POINTER=(-1)
BODY    (   1)
BODY    (1   )
{O}
Z-SHIFT=(+2.000000000000000E+01,   0)
{Z}
{END}
"""

PTS = np.array([[-3.0, 0, 20.0], [3.0, 0, 20.0], [-3.0, 0, 0.0]])


def _pengeom_agrees(text: str, folder, pts, extra: dict[str, str] | None = None):
    """LOCATE de PENGEOM real y de ARGUS en ``pts`` (materiales)."""
    from argus.oracle.probe import run_probe

    folder.mkdir(parents=True, exist_ok=True)
    for name, t in (extra or {}).items():
        (folder / name).write_text(t, encoding="latin-1")
    (folder / "g.geo").write_text(text, encoding="latin-1")
    res = run_probe(folder / "g.geo", points=np.hstack([pts, np.tile([0, 0, 1.0], (len(pts), 1))]))
    assert res.status.ok, res.status.reason
    geo = compile_geometry(load_geometry(folder / "g.geo"), base_dir=folder)
    assert geo.ok
    assert Locator(geo).locate(pts)[1].tolist() == res.locate_mat.tolist()
    return res.locate_mat.tolist()


# ---------------------------------------------------------------------------------------------------------------------
# Clon de un clon
# ---------------------------------------------------------------------------------------------------------------------

def test_materializing_a_clone_that_another_clone_copies_keeps_its_copies():
    doc = parse_geo_text(CLONE_OF_CLONE)
    geo = compile_checked(doc)
    notes, key_map = materialize_clone(doc, geo, geo.find_body("   3").index)
    geo2 = compile_checked(parse_geo_text(write_geo_text(doc)))
    mapping = renumber_map(geo, geo2, key_map=key_map)
    assert None not in mapping.values()  # ningún cuerpo desaparece (antes, las copias del clon 4)
    for kb, new in mapping.items():  # y cada uno es el mismo: mismo material
        assert geo.body(kb).material == geo2.body(new).material


# ---------------------------------------------------------------------------------------------------------------------
# Convertir en módulo y envolver con un INCLUDE*
# ---------------------------------------------------------------------------------------------------------------------

def _include_case(tmp_path):
    d = tmp_path / "caso"
    d.mkdir(parents=True)
    (d / "inc.geo").write_text(INCLUDED, encoding="latin-1")
    (d / "main.geo").write_text(MAIN_WITH_INCLUDE, encoding="latin-1")
    doc = load_geometry(d / "main.geo")
    geo = compile_geometry(doc, base_dir=d)
    assert geo.ok
    return d, doc, geo


INC_PTS = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 2.0], [0.0, 0.0, 5.0], [0.3, 0.2, -0.4]])


def _materials_after(doc, d):
    text = write_geo_text(doc)
    new_doc = reparse(text, doc, d)
    new_geo = compile_checked(new_doc, None, d)
    return text, Locator(new_geo).locate(INC_PTS)[1].tolist()


def test_convert_to_module_takes_the_limiting_bodies_of_an_include_star_body(tmp_path):
    d, doc, geo = _include_case(tmp_path)
    before = Locator(geo).locate(INC_PTS)[1].tolist()
    assert before == [3, 2, 1, 3]
    kx = geo.find_body("  10").index
    chk = check_convert(geo, Locator(geo), kx, ((-12, -12, -12), (12, 12, 12)))
    assert chk.ok
    convert_to_module(doc, geo, kx)
    text, after = _materials_after(doc, d)
    assert after == before
    assert "BODY    (   2)" in text[text.index("MODULE  (  10)"):]  # el limitante del cuerpo del INCLUDE*


def test_wrap_takes_the_limiting_bodies_of_an_include_star_body(tmp_path):
    d, doc, geo = _include_case(tmp_path)
    kx = geo.find_body("  10").index
    loc = Locator(geo)
    bounds = ((-12, -12, -12), (12, 12, 12))
    ext = element_extent(geo, None, kx, bounds)
    exts = [members_box(geo, kx, ext, bounds)]
    p = sample_solids(geo, loc, [kx], exts, n=4000, bounds=bounds)
    prim = fitted_primitive(exts, "sphere", 0.5, "z", p)
    assert check_wrap(geo, loc, [kx], prim, geo.nbodys, exts, bounds, n=4000).ok
    wrap(doc, geo, [kx], prim)
    _text, after = _materials_after(doc, d)
    assert after == [3, 2, 1, 3]


def test_convert_and_wrap_with_include_star_agree_with_pengeom(tmp_path, need_probe):
    for op in ("convert", "wrap"):
        d, doc, geo = _include_case(tmp_path / op)
        kx = geo.find_body("  10").index
        if op == "convert":
            convert_to_module(doc, geo, kx)
        else:
            bounds = ((-12, -12, -12), (12, 12, 12))
            loc = Locator(geo)
            ext = element_extent(geo, None, kx, bounds)
            exts = [members_box(geo, kx, ext, bounds)]
            prim = fitted_primitive(exts, "sphere", 0.5, "z",
                                    sample_solids(geo, loc, [kx], exts, n=4000, bounds=bounds))
            wrap(doc, geo, [kx], prim)
        assert _pengeom_agrees(write_geo_text(doc), tmp_path / op / "probe", INC_PTS,
                               {"inc.geo": INCLUDED}) == [3, 2, 1, 3]


def test_closure_problem_says_what_the_module_could_not_declare(tmp_path):
    """La comprobación de seguridad: lo que GEOMIN tiene como limitantes (también los de los limitantes) tiene que
    estar entre las hijas que se declaran; si falta alguno, se dice por qué y no se cambia nada."""
    from argus.pengeom.model import RefLine

    _d, _doc, geo = _include_case(tmp_path)
    kx = geo.find_body("  10").index
    assert ge.closure_problem(geo, kx) is None  # con el INCLUDE*, el principal puede declarar Y y su limitante Z
    msg = ge.closure_problem(geo, kx, [RefLine("BODY", "   1")])  # sin Z (como antes de 0.16.3)
    assert msg is not None and "LOCATE no lo encontraría" in msg and "Cuerpo «2»" in msg


# ---------------------------------------------------------------------------------------------------------------------
# Etiquetas con sus cuatro columnas
# ---------------------------------------------------------------------------------------------------------------------

def test_label_key_keeps_the_four_columns():
    assert ge.label_key("1") == "   1" and ge.label_key("   1") == "   1" and ge.label_key("1   ") == "1   "
    assert not ge.same_label("1   ", "   1") and ge.same_label("1", "   1")


def test_deleting_a_left_aligned_label_does_not_touch_the_right_aligned_one():
    doc = parse_geo_text(COLUMNS)
    geo = compile_checked(doc)
    kb = next(b.index for b in geo.bodies if b.alias == "1   0")
    ge.delete_body(doc, geo, kb)
    text = write_geo_text(doc)
    module = text[text.index("MODULE  (   9)"):]
    assert "BODY    (   1)" in module and "BODY    (1   )" not in module
    assert "SURFACE (   1)" in text and "SURFACE (1   )" not in text
    geo2 = compile_checked(parse_geo_text(text))
    assert Locator(geo2).locate(PTS)[1].tolist() == [2, 1, 0]  # «   1» sigue en el módulo (z = 20)


def test_find_element_and_surfaces_with_column_labels():
    doc = parse_geo_text(COLUMNS)
    geo = compile_checked(doc)
    assert ge.find_element(doc, "body", "1   ").material == 3
    assert ge.find_element(doc, "body", "   1").material == 2
    assert [u.label for u in ge.surface_users(doc, "1   ")] == ["1   "]
    # Cambiar la etiqueta de la superficie «1   » no cambia las referencias a «   1».
    sf.rename_surface(doc, geo, "1   ", "7")
    text = write_geo_text(doc)
    body1 = text[text.index("BODY    (   1)"):text.index("BODY    (1   )")]
    assert "SURFACE (   1), SIDE POINTER=(-1)" in body1
    assert "SURFACE (   7), SIDE POINTER=(-1)" in text[text.index("BODY    (1   )"):]
    # Y cambiar la del cuerpo «1   » no cambia las del módulo que nombran a «   1».
    doc = parse_geo_text(COLUMNS)
    geo = compile_checked(doc)
    sf.rename_body(doc, geo, next(b.index for b in geo.bodies if b.alias == "1   0"), "8")
    module = write_geo_text(doc)
    module = module[module.index("MODULE  (   9)"):]
    assert "BODY    (   1)" in module and "BODY    (   8)" in module


def test_column_labels_agree_with_pengeom_after_deleting(tmp_path, need_probe):
    doc = parse_geo_text(COLUMNS)
    geo = compile_checked(doc)
    ge.delete_body(doc, geo, next(b.index for b in geo.bodies if b.alias == "1   0"))
    assert _pengeom_agrees(write_geo_text(doc), tmp_path, PTS) == [2, 1, 0]


def test_duplicating_with_column_labels(tmp_path):
    doc = parse_geo_text(COLUMNS)
    geo = compile_checked(doc)
    labels = duplicate(doc, geo, geo.find_body("   9").index, [0.0, 30.0, 0.0])
    geo2 = compile_checked(parse_geo_text(write_geo_text(doc)))
    pts = PTS + np.array([0.0, 30.0, 0.0])
    assert Locator(geo2).locate(pts)[1].tolist() == [2, 3, 0]  # la copia tiene los dos cuerpos
    assert len(labels) == 1


# ---------------------------------------------------------------------------------------------------------------------
# Duplicar y PARINP; plural
# ---------------------------------------------------------------------------------------------------------------------

PARINP_GEO = f"""disc with a PARINP shift
{Z}
SURFACE (   1)   plane z=0
INDICES=( 0, 0, 0, 1, 0)
{Z}
SURFACE (   2)   plane z=PARINP(1)
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(+2.000000000000000E+00,   1)
{Z}
SURFACE (   3)   cylinder
INDICES=( 1, 1, 0, 0,-1)
{Z}
BODY    (   1)   disc
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(+1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
{Z}
{END}
"""


def test_duplicate_says_when_a_parinp_value_becomes_a_number():
    doc = parse_geo_text(PARINP_GEO)
    geo = compile_geometry(doc, parinp=[2.0])
    assert geo.ok
    labels = duplicate(doc, geo, 1, [0.0, 0.0, 5.0], parinp=[2.0])
    assert labels.notes and any("usaba PARINP(1); en la copia es un número" in n for n in labels.notes)


def test_two_clones_of_a_module_plural():
    doc = parse_geo_text(CLONE_OF_CLONE.replace(f"MODULE  (   3)\n{O}", f"MODULE  (   2)\n{O}"))
    geo = compile_checked(doc)
    msg = ge.delete_problem(doc, geo, geo.find_body("   2").index)
    assert msg is not None and "conviértalos en copias independientes" in msg
