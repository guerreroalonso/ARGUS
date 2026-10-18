"""Revisión de 0.15.0 (núcleo): cada hallazgo de la revisión de «Geometrías complejas», con su prueba (0.15.1).

La identidad de los cuerpos de los INCLUDE (dos del mismo fichero, Duplicate, INCLUDE ↔ INCLUDE*), las líneas del .in
que nombran cuerpos por etiqueta, Relabel con un INCLUDE*, mover bloques que cambia la geometría, índices PARINP y
nombres de fichero tal como estaban, números mal escritos, el texto que se conserva al reescribir y los mensajes.
"""

from __future__ import annotations

import pytest

from argus.pengeom import blocks as bk
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import EditError, compile_checked, reparse
from argus.pengeom.identity import body_keys, geometry_change, renumber_map
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from argus.penhan.follow import follow_geometry, labels_still_resolve, stale_references
from argus.penhan.inputfile import parse_in_text
from conftest import CORPUS
from geo_oracle import assert_pengeom_agrees, random_points

Z = "0" * 64
ONES = "1" * 64
END = "END      " + "0" * 55 + "\n"
BALL = ("inc\n" + Z + "\nSURFACE (   1)   r=1\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
        "BODY    (  B1)   bola\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n" + END)
PLANE = "SURFACE (   9)   plano\nINDICES=( 0, 0, 0, 1, 0)\nZ-SHIFT=(+5.000000000000000E+00,   0)\n" + Z + "\n"


def _include(name: str, starred: bool = False) -> str:
    return ("INCLUDE*" if starred else "INCLUDE") + f"\n   FILE=({name:<12})\n" + Z + "\n"


def _main(tmp_path, *blocks: str, inc: str = BALL):
    (tmp_path / "a.geo").write_text(inc, encoding="latin-1")
    text = "m\n" + Z + "\n" + "".join(blocks) + END
    (tmp_path / "m.geo").write_text(text, encoding="latin-1")
    doc = load_geometry(tmp_path / "m.geo")
    return doc, compile_checked(doc, None, tmp_path)


def _apply(doc, fn, base_dir):
    work = reparse(write_geo_text(doc), doc)
    geo = compile_checked(work, None, base_dir)
    res = fn(work, geo)
    after = write_geo_text(work)
    new = reparse(after, doc, base_dir)
    return after, new, compile_checked(new, None, base_dir), res


def _describe(doc, i, text):
    def fn(d, g):
        f = bk.form_of(d.elements[i], d)
        f.description = text
        return bk.modify_block(d, i, f, g)
    return fn


# ---------------------------------------------------------------------------------------------------------------------
# INCLUDE: la identidad de sus cuerpos (el .in no se puede quedar con los de otro)
# ---------------------------------------------------------------------------------------------------------------------

def test_two_includes_of_the_same_file_are_two_bodies(tmp_path):
    doc, geo = _main(tmp_path, PLANE, _include("a.geo"), _include("a.geo"))
    keys = body_keys(geo)
    assert keys[0] != keys[1]
    # Un cambio que no tiene nada que ver con los INCLUDE: cada documento incluido sigue siendo suyo.
    _, new, geo2, _ = _apply(doc, _describe(doc, 0, "otro plano"), tmp_path)
    incs = [el for el in new.elements if el.kind.value == "INCLUDE"]
    assert incs[0].document is not incs[1].document
    assert renumber_map(geo, geo2) == {1: 1, 2: 2, 3: 3}
    # Y el .in que nombra las dos bolas por número sigue igual.
    indoc = parse_in_text("TITLE  x\nDSMAX  1 0.1\nDSMAX  2 0.2\n")
    assert follow_geometry(indoc, renumber_map(geo, geo2), old_geo=geo, new_geo=geo2) == []
    # Quitar el segundo: la primera bola sigue siendo la 1.
    _, _, geo3, _ = _apply(new, lambda d, g: bk.delete_block(d, 2), tmp_path)
    assert renumber_map(geo2, geo3) == {1: 1, 2: None, 3: 2}


def test_duplicate_of_an_include_block(tmp_path):
    doc, geo = _main(tmp_path, PLANE, _include("a.geo"))
    i = next(k for k, el in enumerate(doc.elements) if el.kind.value == "INCLUDE")
    _, new, geo2, _ = _apply(doc, lambda d, g: bk.add_block(d, i, bk.form_of(d.elements[i], d), g), tmp_path)
    assert renumber_map(geo, geo2) == {1: 1, 2: 3}  # la bola de antes es la 1; la nueva, la 2; la envolvente, la 3
    indoc = parse_in_text("TITLE  x\nDSMAX  1 0.1\n")
    notes = follow_geometry(indoc, renumber_map(geo, geo2), old_geo=geo, new_geo=geo2)
    assert notes == [] and "DSMAX  1 0.1" in indoc.text()


def test_include_to_include_star_keeps_the_bodies_and_their_lines(tmp_path):
    doc, geo = _main(tmp_path, _include("a.geo", starred=True), PLANE)

    def toggle(d, g):
        f = bk.form_of(d.elements[0], d)
        f.starred = not f.starred
        return bk.modify_block(d, 0, f, g)

    _, new, geo2, _ = _apply(doc, toggle, tmp_path)
    mapping = renumber_map(geo, geo2)
    assert mapping == {1: 1, 2: 2}  # los mismos números: los mismos cuerpos
    assert geo.balias(1) == "  B1" and geo2.balias(1) == ""  # sin «*», la bola pierde la etiqueta para penhmain
    # El .in la nombraba por su etiqueta: ya no la encontraría, así que pasa a nombrarla por su número.
    indoc = parse_in_text("TITLE  x\nDSMAX  '  B1' 0.1\nEABSB  1 1e3 1e3 1e3 1e9\n")
    assert not labels_still_resolve(indoc, mapping, geo, geo2)
    notes = follow_geometry(indoc, mapping, old_geo=geo, new_geo=geo2)
    assert "DSMAX  1 0.1" in indoc.text() and notes and "número" in notes[0]
    # Y al revés (de INCLUDE a INCLUDE*), nada que cambiar.
    _, back, geo3, _ = _apply(new, toggle, tmp_path)
    assert renumber_map(geo2, geo3) == {1: 1, 2: 2}


def test_stale_references_of_other_inputs_follow_identity():
    """Otro .in de la carpeta: con un CLONE nuevo delante, la copia «8» es otra aunque tenga el mismo número (las
    copias de un clon no tienen etiqueta): se avisa."""
    clone = ("m\n" + Z + "\nSURFACE (   1)   r=1\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
             "BODY    (   1)\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n"
             "MODULE  (  M1)\nMATERIAL(   0)\nSURFACE (   1), SIDE POINTER=(-1)\nBODY    (   1)\n" + Z + "\n")
    copy = lambda lab, x: (f"CLONE   ({lab:>4})\nMODULE  (  M1)\n" + ONES  # noqa: E731
                           + f"\nX-SHIFT=({x:+.15E},   0)\n" + Z + "\n")
    old = compile_geometry(parse_geo_text(clone + copy("C1", 5.0) + END, "m.geo"))
    new = compile_geometry(parse_geo_text(clone + copy("C0", 9.0) + copy("C1", 5.0) + END, "m.geo"))
    assert old.ok and new.ok
    kb = next(b.index for b in old.bodies if b.origin == "clone" and b.element is not b.clone_element)
    stale = stale_references(parse_in_text(f"TITLE  x\nDSMAX  {kb} 0.1\n"), old, new)
    assert stale and "copia del clon «C1»" in stale[0] and "«C0»" in stale[0]


# ---------------------------------------------------------------------------------------------------------------------
# Relabel y mover bloques: solo cambian etiquetas y orden, nunca la geometría
# ---------------------------------------------------------------------------------------------------------------------

def test_relabel_refuses_an_include_star_that_names_main_labels(tmp_path):
    inc = ("i\n" + Z + "\nBODY    (  B2)\nMATERIAL(   2)\nSURFACE (  S1), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    surf = "SURFACE (  S1)\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
    doc, geo = _main(tmp_path, surf, _include("a.geo", starred=True), inc=inc)
    with pytest.raises(bk.BlockError, match="INCLUDE\\*\\) nombra la superficie «S1»"):
        _apply(doc, lambda d, g: bk.relabel(d, g), tmp_path)
    # Etiquetas numéricas en el INCLUDE* que chocarían con los números nuevos: se dice claro.
    inc2 = ("i\n" + Z + "\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n" + Z + "\n"
            "BODY    (   1)\nMATERIAL(   2)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    surf2 = "SURFACE (  AA)\nINDICES=( 1, 1, 1, 0,-1)\nX-SHIFT=(+5.000000000000000E+00,   0)\n" + Z + "\n"
    doc2, geo2 = _main(tmp_path, surf2, _include("a.geo", starred=True), inc=inc2)
    with pytest.raises(bk.BlockError, match="ya la tiene un bloque de a.geo"):
        _apply(doc2, lambda d, g: bk.relabel(d, g), tmp_path)


def test_relabel_keeps_the_geometry_in_the_corpus():
    for rel in ("pengeomjar/chair", "pengeomjar/example2", "pengeomjar/saturne", "pengeomjar/well"):
        doc = load_geometry(CORPUS / rel)
        geo = compile_geometry(doc, base_dir=(CORPUS / rel).parent)
        renames: dict[str, str] = {}

        def fn(d, g, renames=renames):
            res = bk.relabel(d, g)
            renames.update(res.renames)
            return res

        _, _, geo2, _ = _apply(doc, fn, (CORPUS / rel).parent)
        assert geometry_change(geo, geo2, renumber_map(geo, geo2, renames)) is None, rel


R20 = ("INDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+2.000000000000000E+01,   0)\nY-SCALE=(+2.000000000000000E+01,   0)\n"
       "Z-SCALE=(+2.000000000000000E+01,   0)\n")
TWO_MODULES = ("m\n" + Z + "\nSURFACE (   1)   plano z=0\nINDICES=( 0, 0, 0, 1, 0)\n" + Z
               + "\nSURFACE (   2)   esfera\n" + R20 + Z + "\nSURFACE (   3)   otra esfera\n" + R20 + Z
               + "\nMODULE  (  M1)   gira 90 grados\nMATERIAL(   1)\nSURFACE (   2), SIDE POINTER=(-1)\n"
               "SURFACE (   1), SIDE POINTER=(-1)\n" + ONES + "\n  THETA=(+9.000000000000000E+01,   0) DEG\n" + Z
               + "\nMODULE  (  M2)   sube 5\nMATERIAL(   2)\nSURFACE (   3), SIDE POINTER=(-1)\n"
               "SURFACE (   1), SIDE POINTER=( 1)\n" + ONES + "\nZ-SHIFT=(+5.000000000000000E+00,   0)\n" + Z + "\n"
               + END)


def test_moving_a_block_that_changes_the_geometry_is_detected(tmp_path, need_probe):
    """Dos módulos con transformación que comparten una superficie: PENGEOM la transforma con los dos, en el orden en
    que se definen. Cambiar ese orden la deja en otro sitio, y PENGEOM acepta los dos ficheros."""
    doc = parse_geo_text(TWO_MODULES, "m.geo")
    geo = compile_checked(doc)
    m2 = next(i for i, el in enumerate(doc.elements) if bk.block_title(el) == "MODULE (M2)")
    after, _, geo2, _ = _apply(doc, lambda d, g: bk.move_block(d, m2, -1), None)
    change = geometry_change(geo, geo2, renumber_map(geo, geo2))
    assert change is not None and "superficies" in change
    pts, dirs = random_points((-25, -25, -25), (25, 25, 25), 2000, 5)
    assert_pengeom_agrees(after, tmp_path, pts, dirs)  # ARGUS ve lo mismo que PENGEOM: la geometría cambia
    # Cambiar de sitio dos superficies no cambia nada (solo la numeración).
    _, _, geo3, _ = _apply(doc, lambda d, g: bk.move_block(d, 1, +1), None)
    assert geometry_change(geo, geo3, renumber_map(geo, geo3)) is None


# ---------------------------------------------------------------------------------------------------------------------
# Lo que se escribe en las pestañas
# ---------------------------------------------------------------------------------------------------------------------

def test_a_negative_parinp_index_is_kept_as_written(tmp_path, need_probe):
    """geometry.rep escribe los índices ya usados como negativos (GEOMIN los toma como «ninguno")."""
    text = ("t\n" + Z + "\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+2.000000000000000E+00,  -1)\n"
            + Z + "\nBODY    (   1)\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "rep.geo")
    after, _, geo, _ = _apply(doc, _describe(doc, 0, "esfera"), None)
    assert "X-SCALE=(+2.000000000000000E+00,  -1)" in after and "esfera" in after
    pts, dirs = random_points((-3, -3, -3), (3, 3, 3), 2000, 3)
    assert_pengeom_agrees(after, tmp_path, pts, dirs)
    f = bk.form_of(doc.elements[0], doc)  # escrito a mano: entre 0 y 50
    f.params["X-SCALE"].index = "-2"
    with pytest.raises(bk.BlockError) as exc:
        bk.element_of(f, doc, doc.elements[0])
    assert exc.value.field == "X-SCALE.index"


def test_untouched_filenames_are_kept(tmp_path):
    (tmp_path / "mi bola.geo").write_text(BALL, encoding="latin-1")
    doc, geo = _main(tmp_path, PLANE, _include("mi bola.geo"))
    after, *_ = _apply(doc, lambda d, g: bk.modify_block(d, 1, bk.form_of(d.elements[1], d), g), tmp_path)
    assert after == write_geo_text(doc)  # Modify sin cambios: igual (no se vuelve a comprobar el nombre)
    f = bk.form_of(doc.elements[1], doc)
    f.filename = "ot bola.geo"
    with pytest.raises(bk.BlockError, match="blancos"):
        bk.element_of(f, doc, doc.elements[1])


@pytest.mark.parametrize("text", ["-", "+", ".", "-.", "--1", "+-3", "E5", "d2", "10-2", "2+3", "1,2,3", "1.5-1"])
def test_typos_are_not_numbers(text):
    with pytest.raises(bk.BlockError, match="no es un número"):
        bk.parse_number(text, "Z-SHIFT")


@pytest.mark.parametrize("text, value", [("2,5", 2.5), ("1.0D0", 1.0), ("+2.500000000000000E+00", 2.5),
                                         ("1e3", 1000.0), ("-.5", -0.5), ("5.", 5.0), ("1.000,5", 1000.5),
                                         ("1.0-100", 1e-100), ("+2.5+123", 2.5e123)])
def test_numbers_as_people_write_them(text, value):
    assert bk.parse_number(text) == pytest.approx(value, rel=1e-15)


def test_values_without_a_decimal_point_keep_their_fortran_meaning():
    """GEOMIN lee «250» en un campo E22.15 como 2.5E-13: si solo cambia la unidad, se escribe ese valor."""
    text = ("t\n" + Z + "\nSURFACE (   1)\nINDICES=( 0, 0, 0, 1, 0)\n  THETA=(250                   ,   0) DEG\n"
            + Z + "\nBODY    (   1)\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "f.geo")
    assert doc.elements[0].params[0].value == pytest.approx(2.5e-13)

    def to_rad(d, g):
        f = bk.form_of(d.elements[0], d)
        f.params["THETA"].unit = "RAD"
        return bk.modify_block(d, 0, f, g)

    after, *_ = _apply(doc, to_rad, None)
    assert "  THETA=(+2.500000000000000E-13,   0) RAD" in after


def test_rewritten_lines_keep_what_follows_their_fields():
    text = ("t\n" + Z + "\nSURFACE (   1)   plano" + " " * 40 + "<-- columna 66: comentario largo\n"
            "INDICES=( 0, 0, 0, 1, 0)\n  THETA=(+0.000000000000000E+00,   0)   (DEFAULT=0.0)\n"
            "Z-SHIFT=(+1.000000000000000E+00,   0)          (DEFAULT=0.0)\n" + Z + "\n"
            "BODY    (   1)\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + "\n" + END)
    doc = parse_geo_text(text, "t.geo")

    def change(d, g):
        f = bk.form_of(d.elements[0], d)
        f.label = "AGUA"
        f.params["THETA"].value = "30"  # tenía la unidad sin escribir
        f.params["Z-SHIFT"].value = "0.30000000000000004"  # 17 cifras: E22.15 lo redondea
        return bk.modify_block(d, 0, f, g)

    after, *_ = _apply(doc, change, None)
    lines = after.splitlines()
    assert lines[2].startswith("SURFACE (AGUA)   plano") and lines[2].endswith("<-- columna 66: comentario largo")
    assert lines[4] == "  THETA=(+3.000000000000000E+01,   0) DEG   (DEFAULT=0.0)"
    assert lines[5] == "Z-SHIFT=(+3.000000000000000E-01,   0)          (DEFAULT=0.0)"
    assert "SURFACE (AGUA), SIDE POINTER=(-1)" in after


def test_add_tells_what_pengeom_would_not_take():
    doc = load_geometry(CORPUS / "pengeomjar" / "chair")
    # New con una etiqueta que ya existe: se dice (Duplicate le da una libre).
    f = bk.form_of(doc.elements[0], doc)
    with pytest.raises(bk.BlockError, match="Ya hay otra superficie"):
        bk.add_block(reparse(write_geo_text(doc), doc), 0, f, None, strict_label=True)
    res = bk.add_block(reparse(write_geo_text(doc), doc), 0, f, None)
    assert "ya existe" in res.notes[0]
    # Un cuerpo con las mismas tablas que otro ocupa su sitio: se avisa.
    b = next(i for i, el in enumerate(doc.elements) if bk.block_title(el) == "BODY (5)")
    _, _, _, res = _apply(doc, lambda d, g: bk.add_block(d, b, bk.form_of(d.elements[b], d), g), None)
    assert any("mismo sitio" in n and "BODY (5)" in n for n in res.notes)
    # Una sola caja CT.
    ct = load_geometry(CORPUS / "penhan" / "emfield_ct" / "ctbox.geo")
    with pytest.raises(bk.BlockError, match="una sola caja CT"):
        bk.add_block(ct, 0, bk.form_of(next(e for e in ct.elements if bk.block_tab(e) == bk.TAB_CT), ct))


def test_new_body_proposes_material_zero():
    assert bk.empty_form(bk.TAB_BODY).material == "0" and bk.empty_form(bk.TAB_MODULE).material == "0"


def test_the_block_includes_its_comments():
    text = "t\n" + Z + "\nC un comentario\nSURFACE (   1)\nINDICES=( 0, 0, 0, 1, 0)\n" + Z + "\n" + END
    doc = parse_geo_text(text, "c.geo")
    assert bk.block_lines(doc.elements[0]) == (2, 5)  # del comentario a los ceros (se borra y se mueve con él)


def test_two_mothers_message_names_the_block():
    doc = load_geometry(CORPUS / "pengeomjar" / "chair")
    m = next(i for i, el in enumerate(doc.elements) if bk.block_title(el) == "MODULE (55)")
    with pytest.raises(EditError, match=r"ya está dentro de MODULE \(55\)"):
        _apply(doc, lambda d, g: bk.add_block(d, m, bk.form_of(d.elements[m], d), g), None)


def test_relabel_and_move_on_random_corpus_files_keep_pengeom_tables(tmp_path, need_probe):
    """Las operaciones nuevas, frente a PENGEOM real: Relabel y mover el primer bloque en tres geometrías."""
    for rel in ("pengeomjar/chair", "pengeomjar/saturne"):
        doc = load_geometry(CORPUS / rel)
        geo = compile_checked(doc)
        after, *_ = _apply(doc, lambda d, g: bk.relabel(d, g), None)
        lo, hi = (-60, -60, -10), (60, 60, 100)
        pts, dirs = random_points(lo, hi, 3000, 11)
        assert_pengeom_agrees(after, tmp_path, pts, dirs, name=rel.split("/")[-1] + "-r.geo")
        moved, _, geo2, _ = _apply(doc, lambda d, g: bk.move_block(d, 0, +1), None)
        assert geometry_change(geo, geo2, renumber_map(geo, geo2)) is None
        assert_pengeom_agrees(moved, tmp_path, pts, dirs, name=rel.split("/")[-1] + "-m.geo")
