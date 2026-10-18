"""Núcleo de la pestaña «Geometrías complejas» (formato de PenGeomJar): bloques, pestañas y operaciones (0.15.0)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from argus.pengeom import blocks as bk
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import EditError, compile_checked, reparse
from argus.pengeom.model import CloneElement, EndElement, IncludeElement, ModuleElement, SurfaceElement
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from conftest import CORPUS, MANIFEST_IDS
from geo_oracle import assert_pengeom_agrees, materials, random_points

CHAIR = CORPUS / "pengeomjar" / "chair"
EXAMPLE2 = CORPUS / "pengeomjar" / "example2"
Z = "0" * 64
END = "END      " + "0" * 55 + "\n"


def _doc(path: Path):
    doc = load_geometry(path)
    assert doc.ok
    return doc


def _apply(doc, fn, base_dir=None):
    """Como GeoController.edit: una copia, el cambio, escribir, releer y compilar (EditError si PENGEOM no lo
    aceptaría). Devuelve (texto, documento nuevo, geometría nueva, resultado)."""
    text = write_geo_text(doc)
    work = reparse(text, doc)
    geo = compile_checked(work, None, base_dir)
    res = fn(work, geo)
    after = write_geo_text(work)
    new = reparse(after, doc, base_dir)
    return after, new, compile_checked(new, None, base_dir), res


def _index(doc, title):
    return next(i for i, el in enumerate(doc.elements)
                if not isinstance(el, EndElement) and bk.block_title(el) == title)


# ---------------------------------------------------------------------------------------------------------------------
# Bloques y líneas
# ---------------------------------------------------------------------------------------------------------------------

def test_block_of_each_line_like_pengeomjar():
    doc = _doc(CHAIR)
    assert bk.block_at_line(doc, 0) is None  # cabecera
    assert bk.block_at_line(doc, 3) is None  # la línea de ceros de la cabecera
    i = bk.block_at_line(doc, 4)  # «SURFACE (   1)»
    assert bk.block_title(doc.elements[i]) == "SURFACE (1)" and bk.block_lines(doc.elements[i]) == (4, 10)
    assert bk.block_at_line(doc, 10) == i  # su línea de ceros
    assert bk.block_title(doc.elements[bk.block_at_line(doc, 11)]) == "SURFACE (2)"
    end = next(el for el in doc.elements if isinstance(el, EndElement))
    assert bk.block_at_line(doc, end.first_line) is None
    assert bk.block_at_line(doc, len(doc.lines) + 5) is None
    tabs = {bk.block_tab(el) for el in doc.elements}
    assert {bk.TAB_SURFACE, bk.TAB_IMPLICIT, bk.TAB_BODY, bk.TAB_MODULE, bk.TAB_CLONE} <= tabs
    # Los comentarios que preceden a un bloque son suyos (como al borrarlo en PenGeomJar).
    lines = "Com\n" + Z + "\nC un comentario\n# otro\nSURFACE (   1)\nINDICES=( 0, 0, 0, 1, 0)\n" + Z + "\n" + END
    d = parse_geo_text(lines, "c.geo")
    assert bk.block_at_line(d, 2) == 0 and bk.block_at_line(d, 3) == 0


def test_forms_show_the_values_as_written():
    doc = _doc(CHAIR)
    f = bk.form_of(doc.elements[0], doc)
    assert isinstance(f, bk.SurfaceForm) and not f.implicit and f.label == "1" and f.description == "leg (cylinder)"
    assert f.indices == (1, 1, 0, 0, -1) and bk.quadric_name(f.indices) == "Cylinder"
    assert f.params["X-SCALE"].value == "+2.000000000000000E+00" and f.params["X-SCALE"].index == "0"
    assert f.params["Y-SHIFT"].value == "+6.000000000000000E+00" and "Z-SCALE" not in f.params
    star = doc.elements[_index(doc, "SURFACE* (5)")]
    assert bk.form_of(star, doc).starred
    plane = bk.form_of(doc.elements[_index(doc, "SURFACE (8)")], doc)
    assert plane.implicit and plane.params["AX"].value == "+1.000000000000000E+00" and plane.params["A0"].value
    body = bk.form_of(doc.elements[_index(doc, "BODY (1)")], doc)
    assert body.material == "2" and body.surfaces == [("   1", -1), ("   5", 1), ("   6", -1)] and not body.bodies
    mod = bk.form_of(doc.elements[_index(doc, "MODULE (55)")], doc)
    assert mod.module and [b.strip() for b in mod.bodies] == ["1", "2", "3", "4", "5"] and len(mod.surfaces) == 6
    back = bk.form_of(doc.elements[_index(doc, "MODULE (6)")], doc)
    assert back.transform["THETA"].value == "+9.000000000000000E+00" and back.transform["THETA"].unit == "DEG"
    clone = bk.form_of(doc.elements[_index(doc, "CLONE (7)")], doc)
    assert clone.module == "   6" and clone.module_note == "original module"
    assert clone.transform["Y-SHIFT"].value == "+38.00000000000000E+00"


def test_quadric_presets_are_pengeomjar_list():
    names = [n for n, _ in bk.QUADRICS]
    assert names[:3] == ["Plane (z=0)", "Sphere", "Cylinder"] and len(names) == 10
    assert bk.quadric_name((1, 1, 1, 0, -1)) == "Sphere" and bk.quadric_name((1, 0, 1, 0, -1)) == bk.USER_INDICES
    assert bk.empty_form(bk.TAB_SURFACE).indices == (0, 0, 0, 1, 0)


# ---------------------------------------------------------------------------------------------------------------------
# Modify sin cambiar nada: el fichero queda igual byte a byte (todo el corpus)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", MANIFEST_IDS)
def test_modify_without_changes_keeps_every_byte(rel):
    path = CORPUS / rel
    doc = load_geometry(path)
    if not doc.ok:
        pytest.skip("geometría con errores")
    text = write_geo_text(doc)
    work = reparse(text, doc)
    for i, el in enumerate(work.elements):  # Modify de cada bloque con lo que enseña su pestaña
        if not isinstance(el, EndElement):
            bk.modify_block(work, i, bk.form_of(el, work))
    out = write_geo_text(work)
    if out != text:  # ¿qué bloque?
        for i, el in enumerate(doc.elements):
            if isinstance(el, EndElement):
                continue
            one = reparse(text, doc)
            bk.modify_block(one, i, bk.form_of(one.elements[i], one))
            assert write_geo_text(one) == text, (rel, i, bk.block_title(el))
    assert out == text


def test_duplicated_parameters_stay_until_changed():
    text = ("T\n" + Z + "\nSURFACE (   1)   esfera\nINDICES=( 1, 1, 1, 0,-1)\n"
            "X-SCALE=(+2.000000000000000E+00,   0)          (DEFAULT=1.0)\n"
            "X-SCALE=(+3.000000000000000E+00,   0)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "d.geo")
    f = bk.form_of(doc.elements[0], doc)
    assert f.params["X-SCALE"].value == "+3.000000000000000E+00"  # el último manda (PENGEOM)
    work = reparse(text, doc)
    bk.modify_block(work, 0, f)
    assert write_geo_text(work) == text
    f.params["X-SCALE"].value = "4"
    work = reparse(text, doc)
    bk.modify_block(work, 0, f)
    out = write_geo_text(work)
    assert "X-SCALE=(+2.0" not in out and "X-SCALE=(+4.000000000000000E+00,   0)" in out


# ---------------------------------------------------------------------------------------------------------------------
# Modify con cambios
# ---------------------------------------------------------------------------------------------------------------------

def test_modify_rewrites_only_the_changed_lines(tmp_path, need_probe):
    doc = _doc(CHAIR)
    before = write_geo_text(doc)

    def fn(d, g):
        f = bk.form_of(d.elements[0], d)
        f.params["X-SCALE"].value = "2,5"  # coma decimal
        f.params["Z-SHIFT"] = bk.Param("Z-SHIFT", "1e-1", "")
        f.description = "pata (cilindro)"
        return bk.modify_block(d, 0, f, g)

    after, new, geo, res = _apply(doc, fn)
    a, b = before.splitlines(), after.splitlines()
    assert len(b) == len(a) + 1
    assert b[4] == "SURFACE (   1)   pata (cilindro)" and b[6] == "X-SCALE=(+2.500000000000000E+00,   0)"
    assert b[10] == "Z-SHIFT=(+1.000000000000000E-01,   0)" and b[11] == Z
    assert b[12:] == a[11:] and res.index == 0
    pts, dirs = random_points([-10, -10, -5], [60, 60, 100], 4000)
    assert_pengeom_agrees(after, tmp_path, pts, dirs)


def test_modify_label_renames_its_references():
    doc = _doc(CHAIR)

    def fn(d, g):
        f = bk.form_of(d.elements[_index(d, "SURFACE* (5)")], d)
        f.label = "F0"
        return bk.modify_block(d, _index(d, "SURFACE* (5)"), f, g)

    after, new, geo, res = _apply(doc, fn)
    assert "SURFACE*(  F0)   plane Z=0" in after and "SURFACE (  F0), SIDE POINTER=( 1)" in after
    assert "SURFACE (   5)" not in after and res.notes and not res.renames

    def fn2(d, g):
        i = _index(d, "MODULE (6)")
        f = bk.form_of(d.elements[i], d)
        f.label = "BACK"
        return bk.modify_block(d, i, f, g)

    after2, new2, geo2, res2 = _apply(new, fn2)
    assert res2.renames == {"   6": "BACK"}
    clone = next(el for el in new2.elements if isinstance(el, CloneElement))
    assert clone.module_label == "BACK" and "original module" in after2.splitlines()[clone.module_line]


def test_modify_refuses_a_label_that_already_exists():
    doc = _doc(CHAIR)
    with pytest.raises(bk.BlockError, match="Ya hay otra superficie con la etiqueta «2»"):
        def fn(d, g):
            f = bk.form_of(d.elements[0], d)
            f.label = "2"
            return bk.modify_block(d, 0, f, g)
        _apply(doc, fn)


def test_modify_body_tables_keep_order_and_sides():
    doc = _doc(CHAIR)
    i = _index(doc, "BODY (5)")

    def fn(d, g):
        f = bk.form_of(d.elements[i], d)
        f.surfaces = [(s, side) for s, side in f.surfaces if s.strip() != "6"] + [("6", 1)]  # Delete y Add
        f.material = "3"
        return bk.modify_block(d, i, f, g)

    after, new, geo, _ = _apply(doc, fn)
    body = new.elements[i]
    assert [(r.label.strip(), r.side) for r in body.items] == [("7", -1), ("8", 1), ("9", -1), ("10", 1), ("11", -1),
                                                                ("6", 1)]
    assert body.material == 3 and "SURFACE (   6), SIDE POINTER=( 1)" in after  # su línea, tal como estaba

    def flip(d, g):  # el lado de una superficie que comparte con su módulo: GEOMIN lo rechaza (IRETRN 2074)
        f = bk.form_of(d.elements[i], d)
        f.surfaces = [(s, -side if s.strip() == "8" else side) for s, side in f.surfaces]
        return bk.modify_block(d, i, f, g)

    with pytest.raises(EditError, match="punteros de lado opuestos"):
        _apply(doc, flip)


def test_new_items_go_after_their_kind():
    text = ("T\n" + Z + "\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\nSURFACE (   2)\n"
            "INDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+2.000000000000000E+00,   0)\nY-SCALE=(+2.000000000000000E+00,   0)\n"
            "Z-SCALE=(+2.000000000000000E+00,   0)\n" + Z + "\nBODY    (   1)\nMATERIAL(   1)\n"
            "SURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\nMODULE  (   2)\nMATERIAL(   2)\n"
            "BODY    (   1)\nSURFACE (   2), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "m.geo")
    f = bk.form_of(doc.elements[3], doc)
    assert f.bodies == ["   1"] and f.surfaces == [("   2", -1)]  # tal como están en el fichero
    f.surfaces.append(("1", 1))
    work = reparse(text, doc)
    bk.modify_block(work, 3, f)
    assert write_geo_text(work).splitlines()[-5:] == [
        "BODY    (   1)", "SURFACE (   2), SIDE POINTER=(-1)", "SURFACE (   1), SIDE POINTER=(+1)", Z, END.strip()]


# ---------------------------------------------------------------------------------------------------------------------
# Lo que está mal escrito
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change, field, message", [
    (lambda f: f.params.__setitem__("X-SCALE", bk.Param("X-SCALE", "abc")), "X-SCALE", "no es un número"),
    (lambda f: f.params.__setitem__("X-SCALE", bk.Param("X-SCALE", "-1")), "X-SCALE", "mayor que 1.0E-15"),
    (lambda f: f.params.__setitem__("Y-SCALE", bk.Param("Y-SCALE", "", "3")), "Y-SCALE", "escriba también un valor"),
    (lambda f: f.params.__setitem__("OMEGA", bk.Param("OMEGA", "1", "51")), "OMEGA.index",
         r"entre 0 \(ninguno\) y 50"),
    (lambda f: setattr(f, "indices", (0, 0, 0, 0, 0)), "indices", r"Surface \(implicit\)"),
    (lambda f: setattr(f, "label", "ABCDE"), "label", "como máximo 4"),
    (lambda f: setattr(f, "label", "A(B"), "label", "paréntesis"),
    (lambda f: setattr(f, "description", "x" * 60), "description", "caben 55"),
])
def test_form_errors_name_the_field(change, field, message):
    doc = _doc(CHAIR)
    f = bk.form_of(doc.elements[0], doc)
    change(f)
    with pytest.raises(bk.BlockError, match=message) as exc:
        bk.element_of(f, doc, doc.elements[0])
    assert exc.value.field in (field, None) or field == "X-SCALE"


def test_numbers_like_the_user_writes_them():
    assert bk.parse_number("2,5") == 2.5 and bk.parse_number(" +2.500000000000000E+00 ") == 2.5
    assert bk.parse_number("1e3") == 1000.0 and bk.parse_number("1.0D0") == 1.0 and bk.parse_number("−3") == -3.0
    assert bk.parse_number("1.000,5") == 1000.5 and bk.parse_number("1,000.5") == 1000.5
    for bad in ("", "abc", "inf", "nan", "1e400"):
        with pytest.raises(bk.BlockError):
            bk.parse_number(bad)
    assert bk.parse_index("", "X") == 0 and bk.parse_index(" 7", "X") == 7
    with pytest.raises(bk.BlockError):
        bk.parse_index("-1", "X")


def test_body_material_and_tables_are_checked():
    doc = _doc(CHAIR)
    f = bk.form_of(doc.elements[_index(doc, "BODY (1)")], doc)
    for material, msg in (("", "Falta el número de material"), ("x", "no es un número entero"),
                          ("9999", "caja CT"), ("-2", "entre 0")):
        g = bk.BodyForm(**{**f.__dict__, "material": material})
        with pytest.raises(bk.BlockError, match=msg):
            bk.element_of(g, doc)
    g = bk.BodyForm(**{**f.__dict__, "surfaces": f.surfaces + [("1", 1)]})
    with pytest.raises(bk.BlockError, match="dos veces"):
        bk.element_of(g, doc)
    g = bk.BodyForm(**{**f.__dict__, "bodies": ["2"], "modules": ["2"]})
    with pytest.raises(bk.BlockError, match="dos veces"):
        bk.element_of(g, doc)


# ---------------------------------------------------------------------------------------------------------------------
# Add / Duplicate, Delete, Move y Relabel
# ---------------------------------------------------------------------------------------------------------------------

def test_add_after_the_selected_block_with_a_free_label():
    doc = _doc(CHAIR)

    def fn(d, g):
        f = bk.form_of(d.elements[0], d)  # Duplicate de «SURFACE (1)»: la etiqueta 1 ya existe
        return bk.add_block(d, 0, f, g)

    after, new, geo, res = _apply(doc, fn)
    assert res.index == 1 and "ya existe" in res.notes[0]
    el = new.elements[1]
    assert isinstance(el, SurfaceElement) and el.label.strip() == bk.free_label(doc, bk.TAB_SURFACE)
    lines = after.splitlines()
    assert lines[11].startswith(f"SURFACE ({el.label})   leg (cylinder)")


def test_add_moves_below_what_it_names_and_before_end():
    text = ("T\n" + Z + "\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\nSURFACE (   2)\n"
            "INDICES=( 0, 0, 0, 1, 0)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "a.geo")
    form = bk.BodyForm("B1", "esfera", "1", surfaces=[("1", -1), ("2", 1)])

    def fn(d, g):
        return bk.add_block(d, 0, form, g)  # elegido el bloque 0, pero usa la superficie 2

    after, new, geo, res = _apply(doc, fn)
    assert res.index == 2 and "PENGEOM exige" in res.notes[0]
    assert bk.block_title(new.elements[2]) == "BODY (B1)" and isinstance(new.elements[3], EndElement)
    # Sin bloque elegido: al final, antes de END.
    after2, new2, geo2, res2 = _apply(new, lambda d, g: bk.add_block(d, None, bk.SurfaceForm("9"), g))
    assert res2.index == 3 and isinstance(new2.elements[4], EndElement)


def test_add_to_an_empty_geometry_builds_it_block_by_block(tmp_path, need_probe):
    doc = parse_geo_text("Nueva\n" + Z + "\n" + END, str(tmp_path / "n.geo"))
    steps = [
        bk.SurfaceForm("1", "esfera", indices=(1, 1, 1, 0, -1),
                       params={"X-SCALE": bk.Param("X-SCALE", "5"), "Y-SCALE": bk.Param("Y-SCALE", "5"),
                               "Z-SCALE": bk.Param("Z-SCALE", "5")}),
        bk.SurfaceForm("2", "plano", implicit=True, indices=(0, 0, 0, 0, 0),
                       params={"AZ": bk.Param("AZ", "1"), "A0": bk.Param("A0", "-1")},
                       transform={"X-SHIFT": bk.Param("X-SHIFT", "1")}),
        bk.BodyForm("1", "media esfera", "1", surfaces=[("1", -1), ("2", -1)]),
        bk.BodyForm("2", "la otra", "2", surfaces=[("1", -1), ("2", 1)]),
        bk.BodyForm("3", "todo", "0", module=True, surfaces=[("1", -1)], bodies=["1", "2"],
                    transform={"OMEGA": bk.Param("OMEGA", "90", unit="DEG"), "Z-SHIFT": bk.Param("Z-SHIFT", "2")}),
        bk.CloneForm("4", "copia", "3", "el original", transform={"X-SHIFT": bk.Param("X-SHIFT", "20")}),
    ]
    cur = doc
    sel = None
    for form in steps:
        text, cur, geo, res = _apply(cur, lambda d, g, form=form, sel=sel: bk.add_block(d, sel, form, g))
        sel = res.index
    assert geo.ok and geo.nbodys >= 6
    lines = text.splitlines()
    assert "SURFACE (   2)   plano" in lines and "1" * 64 in lines and "  OMEGA=(+9.000000000000000E+01,   0) DEG" \
        in lines and "MODULE  (   3)   el original" in lines
    pts, dirs = random_points([-30, -10, -10], [30, 10, 10], 3000)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_delete_refuses_what_others_use_and_takes_the_comments(tmp_path):
    doc = _doc(CHAIR)
    with pytest.raises(bk.BlockError, match="lo usa BODY"):
        _apply(doc, lambda d, g: bk.delete_block(d, 0))
    text = ("T\n" + Z + "\nC la esfera\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\nC sin usar\n"
            "SURFACE (   2)\nINDICES=( 0, 0, 0, 1, 0)\n" + Z + "\nBODY    (   1)\nMATERIAL(   1)\n"
            "SURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    d = parse_geo_text(text, "x.geo")
    after, new, geo, _ = _apply(d, lambda w, g: bk.delete_block(w, 1))
    assert "sin usar" not in after and "C la esfera" in after and len(new.elements) == 3
    with pytest.raises(bk.BlockError, match="END"):
        bk.delete_block(d, len(d.elements) - 1)


def test_move_blocks_and_definition_order():
    doc = _doc(CHAIR)
    after, new, geo, res = _apply(doc, lambda d, g: bk.move_block(d, 1, -1))
    assert res.index == 0 and bk.block_title(new.elements[0]) == "SURFACE (2)"
    assert after.splitlines()[4] == "SURFACE (   2)   leg (cylinder)"
    i = _index(doc, "BODY (1)")  # subirlo por encima de sus superficies: PENGEOM no lo aceptaría
    with pytest.raises(EditError, match="no está definida"):
        _apply(doc, lambda d, g: _move_many(d, i, i))
    last = max(bk.editable_blocks(doc))
    after3, new3, geo3, res3 = _apply(doc, lambda d, g: bk.move_block(d, last, 1))  # no pasa de END
    assert res3.index == last and after3 == write_geo_text(doc)


def _move_many(d, i, n):
    res = None
    for k in range(n):
        res = bk.move_block(d, i - k, -1)
    return res


def test_relabel_uses_pengeom_numbers(tmp_path, need_probe):
    doc = _doc(CHAIR)
    geo0 = compile_geometry(doc)
    after, new, geo, res = _apply(doc, lambda d, g: bk.relabel(d, g))
    for el in new.elements:
        if isinstance(el, SurfaceElement):
            s = next(s for s in geo.surfaces if s.element is el)
            assert int(el.label) == s.index
    for b in geo.bodies:
        if b.origin in ("user",) and b.document is new and b.element is not None:
            assert int(b.element.label) == b.index
    assert res.renames["  55"] == "   6" and "MODULE  (   6)" in after
    pts, dirs = random_points([-5.0, -5.0, -5.0], [95.0, 55.0, 95.0], 5000)
    assert np.array_equal(materials(geo0, pts, dirs)[1], materials(geo, pts, dirs)[1])
    assert_pengeom_agrees(after, tmp_path, pts, dirs)


def test_include_added_from_the_work_folder(tmp_path):
    shutil.copy2(CORPUS / "pengeomjar" / "example1", tmp_path / "example1")
    doc = parse_geo_text("Con include\n" + Z + "\n" + END, str(tmp_path / "inc.geo"))
    after, new, geo, res = _apply(doc, lambda d, g: bk.add_block(d, None, bk.IncludeForm("example1", True), g),
                                  base_dir=tmp_path)
    inc = new.elements[0]
    assert isinstance(inc, IncludeElement) and inc.document is not None and geo.ok and geo.nbodys > 1
    assert after.splitlines()[2:4] == ["INCLUDE*", "   FILE=(example1    )"]
    with pytest.raises(EditError):
        _apply(doc, lambda d, g: bk.add_block(d, None, bk.IncludeForm("noexiste", False), g), base_dir=tmp_path)
    with pytest.raises(bk.BlockError, match="CHARACTER\\*12"):
        bk.element_of(bk.IncludeForm("nombre_muy_largo.geo"))


def test_clone_note_is_read_and_written():
    doc = _doc(CHAIR)
    clone = next(el for el in doc.elements if isinstance(el, CloneElement))
    assert clone.module_note == "   original module"
    i = doc.elements.index(clone)

    def fn(d, g):
        f = bk.form_of(d.elements[i], d)
        f.module_note = "el respaldo"
        return bk.modify_block(d, i, f, g)

    after, new, geo, _ = _apply(doc, fn)
    assert "MODULE  (   6)   el respaldo" in after and "original module" not in after


def test_labels_for_completion_include_shared_files():
    doc = load_geometry(EXAMPLE2)
    surf = dict(bk.labels_of(doc, "surface"))
    assert "BS3" in surf and surf["BS3"] == "Plane Y=-5.0"
    mods = dict(bk.labels_of(doc, "module"))
    assert {"BM1", "BM2", "FULL"} <= set(mods)
    assert isinstance(next(el for el in doc.elements if isinstance(el, ModuleElement)), ModuleElement)


# ---------------------------------------------------------------------------------------------------------------------
# Cadenas de cambios al azar con las pestañas, comprobadas con PENGEOM real
# ---------------------------------------------------------------------------------------------------------------------

FUZZ_FILES = ("chair", "well", "quadricinbox", "spheres", "glass", "tcone")


def _random_step(rng, doc, geo):
    """Un cambio al azar como los de las pestañas (puede no valer: PENGEOM lo rechazaría)."""
    blocks = bk.editable_blocks(doc)
    i = rng.choice(blocks) if blocks else None
    el = doc.elements[i] if i is not None else None
    op = rng.choice(("param", "param", "add_surface", "add_body", "delete", "move", "label", "material",
                     "relabel")) if blocks else "add_surface"
    if op == "param" and isinstance(el, SurfaceElement) and el.params:
        f = bk.form_of(el, doc)
        name = rng.choice(sorted(f.params))
        p = f.params[name]
        if p.index not in ("", "0"):
            return None
        v = bk.parse_number(p.value)
        v = v * rng.uniform(0.8, 1.25) if name.endswith("SCALE") or name.startswith("A") else v + rng.uniform(-2, 2)
        f.params[name] = bk.Param(name, repr(v), "", p.unit)
        return lambda d, g: bk.modify_block(d, i, f, g)
    if op == "add_surface":
        axis = rng.choice("XYZ")
        if rng.random() < 0.5:
            f = bk.SurfaceForm("", "plano", indices=(0, 0, 0, 1, 0),
                               params={"THETA": bk.Param("THETA", "90" if axis != "Z" else "0"),
                                       "PHI": bk.Param("PHI", "90" if axis == "Y" else "0"),
                                       f"{axis}-SHIFT": bk.Param(f"{axis}-SHIFT", f"{rng.uniform(-20, 20):.4f}")})
        else:
            f = bk.SurfaceForm("", "plano implícito", implicit=True, indices=(0, 0, 0, 0, 0),
                               params={f"A{axis}": bk.Param(f"A{axis}", "1"),
                                       "A0": bk.Param("A0", f"{rng.uniform(-20, 20):.4f}")})
        f.label = bk.free_label(doc, bk.TAB_SURFACE, geo)
        return lambda d, g: bk.add_block(d, i, f, g)
    if op == "add_body":
        surfs = [e for e in doc.elements if isinstance(e, SurfaceElement)]
        if len(surfs) < 2:
            return None
        chosen = rng.sample(surfs, 2)
        f = bk.BodyForm(bk.free_label(doc, bk.TAB_BODY, geo), "nuevo", str(rng.randint(0, 3)),
                        surfaces=[(s.label.strip(), rng.choice((-1, 1))) for s in chosen])
        return lambda d, g: bk.add_block(d, None, f, g)
    if op == "delete":
        return lambda d, g: bk.delete_block(d, i)
    if op == "move":
        delta = rng.choice((-1, 1))
        return lambda d, g: bk.move_block(d, i, delta)
    if op == "label" and el is not None and not isinstance(el, IncludeElement):
        f = bk.form_of(el, doc)
        tab = bk.TAB_SURFACE if isinstance(el, SurfaceElement) else bk.TAB_BODY
        f.label = bk.free_label(doc, tab, geo)
        return lambda d, g: bk.modify_block(d, i, f, g)
    if op == "material" and isinstance(el, bk.BODY_LIKE[:2]):
        f = bk.form_of(el, doc)
        f.material = str(rng.randint(0, 4))
        return lambda d, g: bk.modify_block(d, i, f, g)
    if op == "relabel":
        return lambda d, g: bk.relabel(d, g)
    return None


@pytest.mark.parametrize("seed", range(len(FUZZ_FILES)))
def test_random_block_edits_agree_with_pengeom(seed, tmp_path, need_probe):
    import random

    from argus.pengeom.bounds import estimate_bounds
    from argus.pengeom.locate import Locator

    rng = random.Random(1000 + seed)
    name = FUZZ_FILES[seed]
    doc = _doc(CORPUS / "pengeomjar" / name)
    done = 0
    for _ in range(40):
        geo = compile_checked(doc)
        fn = _random_step(rng, doc, geo)
        if fn is None:
            continue
        try:
            text, doc, geo, _ = _apply(doc, fn)
        except EditError:
            continue  # lo que PENGEOM no aceptaría no se hace
        assert write_geo_text(reparse(text, doc)) == text  # estable al releer
        done += 1
    assert done >= 10
    lo, hi = estimate_bounds(geo, locator=Locator(geo))
    pts, dirs = random_points(lo, hi, 4000, seed)
    assert_pengeom_agrees(write_geo_text(doc), tmp_path, pts, dirs, name=f"{name}.geo")


def test_labels_are_compared_on_their_four_columns():
    """PENGEOM compara las cuatro columnas: «   1» y «1   » son dos superficies distintas."""
    text = ("T\n" + Z + "\nSURFACE (   1)   r=1\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\nSURFACE (1   )   r=3\n"
            "INDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+3.000000000000000E+00,   0)\nY-SCALE=(+3.000000000000000E+00,   0)\n"
            "Z-SCALE=(+3.000000000000000E+00,   0)\n" + Z + "\nBODY    (   1)\nMATERIAL(   1)\n"
            "SURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\nBODY    (   2)\nMATERIAL(   2)\n"
            "SURFACE (1   ), SIDE POINTER=(-1)\nBODY    (   1)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "l.geo")
    assert [bk.block_title(u) for u in bk.users_of(doc, doc.elements[0])] == ["BODY (1)"]

    def fn(d, g):
        f = bk.form_of(d.elements[0], d)
        f.label = "9"
        return bk.modify_block(d, 0, f, g)

    after, new, geo, res = _apply(doc, fn)
    assert "SURFACE (   9), SIDE POINTER=(-1)" in after and "SURFACE (1   ), SIDE POINTER=(-1)" in after
    # Relabel: cada una con su número.
    after2, new2, geo2, _ = _apply(doc, lambda d, g: bk.relabel(d, g))
    body2 = after2.split("BODY    (   2)")[1]
    assert "SURFACE (   2)   r=3" in after2 and body2.splitlines()[2] == "SURFACE (   2), SIDE POINTER=(-1)"


def test_a_typed_label_finds_the_one_in_the_file():
    text = ("T\n" + Z + "\nSURFACE (S1  )   esfera\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
            "BODY    (B1  )\nMATERIAL(   1)\nSURFACE (S1  ), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "a.geo")
    assert bk.resolve_label(doc, "SURFACE", "S1") == "S1  " and bk.resolve_label(doc, "SURFACE", "S2") == "  S2"
    after, new, geo, _ = _apply(doc, lambda d, g: bk.add_block(d, None, bk.BodyForm(
        "B2", "fuera", "2", surfaces=[("S1", 1)]), g))
    assert "SURFACE (S1  ), SIDE POINTER=(+1)" in after and geo.ok
    after2, new2, geo2, res = _apply(doc, lambda d, g: bk.add_block(d, 1, bk.form_of(d.elements[1], d), g))
    assert geo2.ok and "ya existe" in res.notes[0]  # Duplicate de «B1  »: su superficie sigue siendo «S1  »
