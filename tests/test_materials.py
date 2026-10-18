"""Materiales con nombre y materiales.txt (F7)."""

import datetime as dt

import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry
from argus.penhan.inputfile import parse_in_file, parse_in_text, write_in_text
from argus.penhan.materials import (
    MFNAME_COMMENT,
    TXT_MARK,
    find_material_list,
    load_material_list,
    mat_filename_problem,
    match_material,
    material_description,
    materials_table,
    materials_txt,
    read_mat_header,
    set_material,
    write_materials_txt,
)
from argus.penhan.validate import validate_case
from conftest import CORPUS

CASE = CORPUS / "usuario/100Mev15-174"
MAT_HEADER = """ PENELOPE (v. 2018)  Material data file ...............
 Material: BRAIN (ICRP)  (123)
 Mass density = 1.03000000E+00 g/cm**3
 Number of elements in the molecule = 13
"""


def test_description_in_comment():
    doc = parse_in_file(CASE / "maniqui.in")
    line = doc.first("MFNAME")
    assert material_description(line) == ""  # comentario canónico (con «&*» detrás)
    set_material(doc, 1, description="Agua líquida (PENELOPE 278)")
    again = parse_in_text(write_in_text(doc))
    assert material_description(again.first("MFNAME")) == "Agua líquida (PENELOPE 278)"
    assert again.model.materials[0].fname == "water.mat"  # el fichero no cambia
    set_material(doc, 1, description="")
    assert doc.first("MFNAME").comment.strip() == MFNAME_COMMENT


def test_rename_file_keeps_rest_of_file():
    raw = (CASE / "maniqui.in").read_bytes().decode("latin-1")
    doc = parse_in_file(CASE / "maniqui.in")
    set_material(doc, 1, fname="agua.mat")
    out = write_in_text(doc)
    changed = [(a, b) for a, b in zip(raw.splitlines(), out.splitlines()) if a != b]
    assert len(changed) == 1 and changed[0][1].startswith("MFNAME agua.mat ")
    assert parse_in_text(out).model.materials[0].fname == "agua.mat"


def test_add_materials_copies_simulation_parameters():
    doc = parse_in_file(CASE / "maniqui.in")
    set_material(doc, 3, "aire.mat", "Air, dry (PENELOPE 104)")
    m = parse_in_text(write_in_text(doc)).model
    assert [x.fname for x in m.materials] == ["water.mat", "material2.mat", "aire.mat"]
    assert m.materials[2].msimpa == m.materials[0].msimpa and m.materials[2].msimph == m.materials[0].msimph
    assert doc.ok
    kws = [ln.kw for ln in doc.lines if ln.kw.strip()]
    assert kws.index("GEOMFN") > max(i for i, k in enumerate(kws) if k == "MFNAME")


def test_filename_rules():
    assert mat_filename_problem("water.mat") is None
    assert mat_filename_problem("") is not None
    assert "20" in mat_filename_problem("a" * 21 + ".mat")
    assert mat_filename_problem("my water.mat") is not None


def test_mat_header(tmp_path):
    p = tmp_path / "Brain.mat"
    p.write_text(MAT_HEADER, encoding="latin-1")
    h = read_mat_header(p)
    assert h.name == "BRAIN (ICRP)" and h.penelope_id == 123 and h.density == 1.03
    (tmp_path / "otro.mat").write_text("hola\n")
    assert read_mat_header(tmp_path / "otro.mat") is None


def test_material_list(tmp_path):
    db = tmp_path / "ProgramasBase/penhan/database"
    db.mkdir(parents=True)
    (db / "material-list.txt").write_text("   1  Hydrogen\n 278  Water, liquid\n 104  Air, dry (near sea level)\n")
    case = tmp_path / "SOBP/caso"
    case.mkdir(parents=True)
    path = find_material_list(case)
    assert path == db / "material-list.txt"
    ml = load_material_list(path)
    assert (278, "Water, liquid") in ml
    assert match_material("water, LIQUID", ml) == (278, "Water, liquid")
    assert match_material("Agua (PENELOPE 278)", ml) == (278, "Water, liquid")


def test_materials_table_and_txt(tmp_path):
    geo = compile_geometry(load_geometry(CASE / "cubo-esfera.geo"))
    doc = parse_in_file(CASE / "maniqui.in")
    set_material(doc, 2, "aire.mat", "Air, dry (PENELOPE 104)")
    (tmp_path / "aire.mat").write_text(MAT_HEADER.replace("BRAIN (ICRP)  (123)", "AIR, DRY  (104)"))
    rows = materials_table(doc, geo, tmp_path)
    assert [(r.number, r.fname, r.exists, r.used) for r in rows] == [(1, "water.mat", False, True),
                                                                     (2, "aire.mat", True, False)]
    assert rows[0].bodies == [1] and rows[1].header.name == "AIR, DRY"
    text = materials_txt(rows, "maniqui.in", geo, "cubo-esfera.geo", now=dt.datetime(2026, 9, 23, 12, 0))
    assert text.startswith(TXT_MARK + " de maniqui.in")
    assert "water.mat" in text and "FALTA: hay que generarlo" in text
    assert "Ficheros .mat que hay que generar:\n  - water.mat" in text
    assert "no lo usa ningún cuerpo" in text
    assert "penhmat" in text
    # aire.mat solo tiene la cabecera: le falta la lista final de ficheros ENDF.
    assert rows[1].problems and "lista final de ficheros ENDF" in rows[1].problems[0].message


def test_hadron_data_only_checked_for_protons_and_alphas(tmp_path):
    from argus.penhan import source_edit as se
    from argus.penhan.materials import hadron_data_needed

    doc = parse_in_file(CASE / "maniqui.in")  # protones
    (tmp_path / "water.mat").write_text(MAT_HEADER)  # .mat de PENELOPE sin datos de hadrones
    (tmp_path / "cubo-esfera.geo").write_bytes((CASE / "cubo-esfera.geo").read_bytes())
    assert hadron_data_needed(doc) is True
    assert materials_table(doc, None, tmp_path)[0].problems[0].error
    se.set_particle(doc, 1)  # electrones: basta el .mat de PENELOPE
    assert hadron_data_needed(doc) is False
    row = materials_table(doc, None, tmp_path)[0]
    assert row.problems == [] and row.status == "existe"
    assert not validate_case(doc, None, tmp_path).has_errors
    se.set_particle(doc, 0)  # radiactiva: depende del nucleido -> aviso
    assert hadron_data_needed(doc) is None
    row = materials_table(doc, None, tmp_path)[0]
    assert row.problems and not row.problems[0].error and row.status == "existe"


def fake_mat(path, name: str, pid: int, isotopes: list[tuple[str, int]], complete: bool = True):
    lines = [MAT_HEADER.replace("BRAIN (ICRP)  (123)", f"{name}  ({pid})").rstrip("\n"), " ... datos ...",
             f" *** ENDF filenames and abundances of isotopes,  NISOT = {len(isotopes):3d}"]
    for fname, iza in (isotopes if complete else isotopes[:-1]):
        lines.append(f" {fname:<25} {iza:6d} 1.00000E+00")
    if complete:
        lines.append(" 0 = NALPHA")
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_mat_nuclear_checks(tmp_path):
    from argus.penhan.materials import MNIS, distinct_isotopes, read_mat_nuclear

    doc = parse_in_file(CASE / "maniqui.in")
    set_material(doc, 2, "hueso.mat")
    set_material(doc, 3, "cerebro.mat")
    water = [("pO16_ICRU63.endf", 8016), ("pO17_TENDL.endf", 8017), ("pO18_TENDL.endf", 8018)]
    fake_mat(tmp_path / "water.mat", "WATER, LIQUID", 278, water)
    for f, _ in water:
        (tmp_path / f).write_text("endf\n")
    many = [(f"pX{k}_TENDL.endf", 50000 + k) for k in range(36)]
    fake_mat(tmp_path / "cerebro.mat", "BRAIN (ICRP)", 123, many)
    fake_mat(tmp_path / "hueso.mat", "BONE", 119, [("pCa40_ICRU63.endf", 20040), ("pP31_ICRU63.endf", 15031)],
             complete=False)
    nuc = read_mat_nuclear(tmp_path / "water.mat")
    assert nuc.nisot == 3 and nuc.complete and nuc.nalpha == 0 and nuc.isotopes[0] == ("pO16_ICRU63.endf", 8016, 1.0)
    rows = materials_table(doc, None, tmp_path)
    water_row, bone, brain = rows
    assert water_row.problems == [] and water_row.status == "existe"
    assert any("incompleta" in m for m, _ in bone.problems)
    assert any("penhan admite 30" in m for m, _ in brain.problems)
    assert any(code == 40028 for _, code in brain.problems) and len(brain.missing_endf) == 36
    assert len(distinct_isotopes(rows)) > MNIS
    d = validate_case(doc, None, tmp_path)
    codes = [x.code for x in d.errors]
    assert 40028 in codes and 40029 in codes
    text = materials_txt(rows, "maniqui.in")
    assert "CON PROBLEMAS" in text and "IRETRN 40029" in text


def test_undeclared_material_is_reported():
    geo = compile_geometry(load_geometry(CASE / "prueba.geo"))
    assert geo.materials() == [1, 2]
    doc = parse_in_file(CASE / "maniqui.in")  # una sola línea MFNAME
    rows = materials_table(doc, geo, None)
    assert [(r.number, r.declared, r.used) for r in rows] == [(1, True, True), (2, False, True)]
    text = materials_txt(rows, "maniqui.in", geo)
    assert "IRETRN 3054" in text
    set_material(doc, 2, "hueso.mat", "Bone, cortical (PENELOPE 120)")
    assert all(r.declared for r in materials_table(doc, geo, None))
    assert not validate_case(doc, geo).has_errors


def test_ct_materials_count_as_used():
    geo = compile_geometry(load_geometry(CORPUS / "penhan/emfield_ct/ctbox.geo"),
                           base_dir=CORPUS / "penhan/emfield_ct")
    doc = parse_in_file(CORPUS / "penhan/emfield_ct/ctbox.in")
    rows = materials_table(doc, geo, None)
    assert rows and all(r.used for r in rows if r.number <= geo.nmat)


def test_write_policy(tmp_path):
    p = write_materials_txt(tmp_path, TXT_MARK + " de a.in\n")
    assert p.name == "materiales.txt"
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 con BOM
    p2 = write_materials_txt(tmp_path, TXT_MARK + " de b.in\n")
    assert p2 == p  # lo escribió ARGUS: se reescribe
    p.write_text("mis notas\n")
    p3 = write_materials_txt(tmp_path, TXT_MARK + " de c.in\n")
    assert p3.name == "materiales_ARGUS.txt" and p.read_text() == "mis notas\n"


@pytest.mark.parametrize("n", [1, 2, 3])
def test_set_material_passes_validation(n):
    geo = compile_geometry(load_geometry(CORPUS / "penhan/stack/3discs.geo"), parinp=[0.002, 0.004, 0.008, 0.008])
    doc = parse_in_file(CORPUS / "penhan/stack/stack.in")
    set_material(doc, n, f"m{n}.mat", f"Material {n}")
    assert doc.ok and not validate_case(doc, geo).has_errors
