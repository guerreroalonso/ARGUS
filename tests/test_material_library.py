"""Materiales (0.16.0, ADR-020): la lista de PENELOPE en español y su búsqueda, las propuestas por el nombre del
fichero, los materiales personalizados del usuario, los materiales sin identificar y la carpeta de los ENDF."""

import json
import os
import sys

import pytest

from argus.core.userdata import user_data_dir
from argus.penhan import composition as cp
from argus.penhan import material_names as mn
from argus.penhan import matgen as mg
from argus.penhan.inputfile import parse_in_text
from argus.penhan.library import Library, LibraryMaterial, read_file, write_file
from argus.penhan.materials import (
    load_material_list,
    match_material,
    materials_table,
    materials_txt,
    set_recipe,
    suggest_mat_filename,
    unidentified,
)
from argus.penhan.programs import PenhanSetup
from conftest import ROOT

# La lista de PENELOPE de verdad: la de la carpeta de Rafael (junto a ARGUS) o la copia de referencia de la nube.
_LISTS = [ROOT.parent / "ProgramasBase" / "penhan" / "database" / "material-list.txt",
          ROOT.parent / "ref" / "SOBP_Investigacion" / "ProgramasBase" / "penhan" / "database" / "material-list.txt"]
REF_LIST = next((p for p in _LISTS if p.is_file()), _LISTS[0])
# Un trozo de la lista de PENELOPE (como database/material-list.txt), para no depender de la del usuario.
MLIST = [(6, "Amorphous carbon"), (13, "Aluminium"), (29, "Copper"), (30, "Zinc"), (82, "Lead"),
         (104, "Air, dry (near sea level)"), (119, "Bone, compact (ICRU)"), (120, "Bone, cortical (ICRP)"),
         (126, "C-552 air-equivalent plastic"), (183, "Lead oxide"), (191, "Lung (ICRP)"), (222, "Polyethylene"),
         (223, "Polyethylene terephthalate (mylar)"), (224, "Polymethyl methacrilate (lucite, perspex, plexiglass)"),
         (262, "Tissue, soft (ICRP)"), (278, "Water, liquid"), (279, "Water vapour")]
BRASS = cp.Composition([(29, 70.0), (30, 30.0)], cp.BY_MASS, 8.53)


def _texts(entries):
    return [e.text for e in entries]


# -- la lista en español y la búsqueda ---------------------------------------------------------------------------------

def test_the_whole_penelope_list_has_spanish_names():
    if not REF_LIST.is_file():
        pytest.skip("sin la lista de PENELOPE de referencia")
    ml = load_material_list(REF_LIST)
    assert len(ml) == 280
    assert all(mn.spanish_name(n, e) for n, e in ml)
    assert mn.penelope_label((278, "Water, liquid")) == "Agua líquida (PENELOPE 278)"


def test_a_list_with_other_numbering_keeps_its_english_names():
    """El nombre en español solo si el inglés es el esperado para ese número (otra versión de la lista no recibe un
    nombre equivocado)."""
    assert mn.spanish_name(278, "Water, liquid") == "Agua líquida"
    assert mn.spanish_name(278, "Something else") is None
    assert mn.penelope_label((278, "Something else")) == "Something else (PENELOPE 278)"
    entry = mn.penelope_entries([(278, "Something else")])[0]
    assert entry.text == "Something else (PENELOPE 278)" and entry.secondary == ""


def test_search_in_spanish_or_english_by_words_and_without_accents():
    ents = mn.penelope_entries(MLIST)
    assert _texts(mn.search(ents, "agua")) == ["Agua líquida (PENELOPE 278)", "Vapor de agua (PENELOPE 279)"]
    assert _texts(mn.search(ents, "water")) == ["Agua líquida (PENELOPE 278)", "Vapor de agua (PENELOPE 279)"]
    assert _texts(mn.search(ents, "hueso cort")) == ["Hueso cortical (ICRP) (PENELOPE 120)"]
    assert _texts(mn.search(ents, "cort hueso")) == ["Hueso cortical (ICRP) (PENELOPE 120)"]
    assert _texts(mn.search(ents, "PULMON")) == ["Pulmón (ICRP) (PENELOPE 191)"]
    assert _texts(mn.search(ents, "pmma"))[0].startswith("PMMA") and _texts(mn.search(ents, "plexi"))
    assert _texts(mn.search(ents, "Pb")) == ["Plomo (PENELOPE 82)"]  # símbolo: palabra entera
    assert _texts(mn.search(ents, "278")) == ["Agua líquida (PENELOPE 278)"]
    assert mn.search(ents, "acero") == []  # no está en la lista: composición propia
    assert len(mn.search(ents, "")) == len(MLIST)


def test_personal_materials_come_first():
    ents = [mn.personal_entry("Latón 70/30", "Cu 70 %, Zn 30 %")] + mn.penelope_entries(MLIST)
    assert _texts(mn.search(ents, ""))[0] == "Latón 70/30"
    assert _texts(mn.search(ents, "laton")) == ["Latón 70/30"]
    assert _texts(mn.search(ents, "zn")) == ["Latón 70/30", "Zinc (PENELOPE 30)"]


@pytest.mark.parametrize("fname,expected", [
    ("agua.mat", 278), ("water.mat", 278), ("aire.mat", 104), ("pmma.mat", 224), ("PE.mat", 222),
    ("hueso_cort.mat", 120), ("Cu_lamina.mat", 29), ("plomo.mat", 82), ("lead.mat", 82), ("pulmon2.mat", 191),
    ("tejido_blando.mat", 262), ("c.mat", 6),
])
def test_suggestions_from_the_file_name(fname, expected):
    best = mn.suggest([fname], mn.penelope_entries(MLIST), limit=1)
    assert best and best[0].number == expected


def test_no_suggestion_without_a_meaningful_name():
    ents = mn.penelope_entries(MLIST)
    assert mn.suggest(["material3.mat"], ents) == [] and mn.suggest(["", "mat2.mat"], ents) == []
    personal = [mn.personal_entry("Latón 70/30", "")] + ents
    assert mn.suggest(["laton_ventana.mat"], personal, limit=1)[0].personal


def test_spanish_names_are_recognised_as_list_materials():
    assert match_material("Agua líquida", MLIST) == (278, "Water, liquid")
    assert match_material("agua liquida", MLIST) == (278, "Water, liquid")
    assert match_material("Agua líquida (PENELOPE 278)", MLIST) == (278, "Water, liquid")
    assert match_material("Water, liquid", MLIST) == (278, "Water, liquid")
    assert match_material("Agua", MLIST) is None  # solo el nombre exacto (las propuestas se confirman)


@pytest.mark.parametrize("name,expected", [
    ("Water, liquid", "water.mat"), ("Agua líquida", "agua.mat"), ("Óxido de aluminio", "oxido.mat"),
    ("n-heptano", "heptano.mat"), ("1,2-diclorobenceno", "diclorobenceno.mat"), ("Latón 70/30", "laton.mat"),
    ("", "material.mat"),
])
def test_mat_file_names_without_accents_or_initials(name, expected):
    assert suggest_mat_filename(name) == expected


# -- la carpeta de datos del usuario y los materiales personalizados ---------------------------------------------------

def test_user_data_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_DIR", str(tmp_path / "datos"))
    assert user_data_dir() == tmp_path / "datos"
    monkeypatch.delenv("ARGUS_DATA_DIR")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert user_data_dir() == tmp_path / "Roaming" / "ARGUS"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert str(user_data_dir()).endswith(os.path.join("Library", "Application Support", "ARGUS"))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert user_data_dir() == tmp_path / "xdg" / "ARGUS"


def test_the_library_keeps_saves_copies_and_removes(tmp_path):
    path = tmp_path / "datos" / "materiales_personalizados.json"
    lib = Library(path)
    assert lib.items == [] and not path.exists()
    lib.put(LibraryMaterial("Latón 70/30", BRASS, "#c8a040"))
    with pytest.raises(ValueError):
        lib.put(LibraryMaterial("latón 70/30", BRASS))  # el nombre, sin distinguir mayúsculas
    copy = lib.duplicate("Latón 70/30")
    assert copy.name == "Latón 70/30 (copia)" and copy.composition == BRASS and copy.color == "#c8a040"
    assert lib.duplicate("Latón 70/30").name == "Latón 70/30 (copia) (2)"
    variant = cp.Composition([(29, 65.0), (30, 35.0)], cp.BY_MASS, 8.47)
    lib.put(LibraryMaterial("Latón 65/35", variant), replace="Latón 70/30 (copia)")  # editar = sustituir
    again = Library(path)
    assert again.names() == ["Latón 70/30", "Latón 65/35", "Latón 70/30 (copia) (2)"]
    assert again.find("LATÓN 65/35").composition == variant and again.items[0].color == "#c8a040"
    assert again.remove("Latón 70/30 (copia) (2)") and Library(path).names() == ["Latón 70/30", "Latón 65/35"]
    data = json.loads(path.read_text(encoding="utf-8"))  # JSON en UTF-8, legible
    assert data["kind"] == "argus-materiales-personalizados" and data["materials"][0]["name"] == "Latón 70/30"


def test_an_unreadable_library_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "materiales_personalizados.json"
    path.write_text("{esto no es JSON", encoding="utf-8")
    lib = Library(path)
    assert lib.unreadable and lib.items == []
    lib.put(LibraryMaterial("Latón 70/30", BRASS))
    aside = [p for p in tmp_path.iterdir() if p.name.startswith("materiales_personalizados.json.dañado-")]
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == "{esto no es JSON"
    assert Library(path).names() == ["Latón 70/30"]


def test_entries_that_do_not_make_sense_are_reported(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"kind": "argus-materiales-personalizados", "version": 1, "materials": [
        {"name": "Bueno", "elements": [[1, 2], [8, 1]], "by": "atomos", "density": 1.0, "i_mode": "argus"},
        {"name": "Sin densidad", "elements": [[1, 2]], "by": "atomos", "density": None},
        {"name": "Elemento 120", "elements": [[120, 1]], "by": "atomos", "density": 1.0, "i_mode": "argus"},
        {"elements": []},
    ]}), encoding="utf-8")
    items, problems = read_file(path)
    assert [m.name for m in items] == ["Bueno", "Elemento 120"] and len(problems) == 2
    assert items[0].problem is None and items[1].problem
    (tmp_path / "otro.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        read_file(tmp_path / "otro.json")  # no es de materiales de ARGUS


def test_export_and_import_merge_without_losing_anything(tmp_path):
    a = Library(tmp_path / "a.json")
    a.put(LibraryMaterial("Latón 70/30", BRASS))
    a.put(LibraryMaterial("Agua pesada", cp.Composition([(1, 2), (8, 1)], cp.BY_ATOMS, 1.107)))
    write_file(tmp_path / "exportado.json", a.items)
    b = Library(tmp_path / "b.json")
    b.put(LibraryMaterial("Latón 70/30", cp.Composition([(29, 60.0), (30, 40.0)], cp.BY_MASS, 8.4)))  # otro
    items, _ = read_file(tmp_path / "exportado.json")
    assert b.merge(items) == (1, 0, 1)
    assert b.names() == ["Latón 70/30", "Latón 70/30 (importado)", "Agua pesada"]
    assert b.find("Latón 70/30").composition.density == 8.4  # el suyo no se toca
    assert b.merge(items) == (0, 2, 0)  # importar otra vez: nada nuevo


# -- materiales sin identificar y la carpeta de los ENDF ---------------------------------------------------------------

IN = """TITLE  prueba
SKPAR  4
SENERG 1e8
SPOSIT 0 0 -10
SCONE  0 0 0
MFNAME agua.mat
MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4
MFNAME raro.mat
MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4
MFNAME hueso.mat                                 [Hueso cortical (ICRP) (PENELOPE 120)]
MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4
GEOMFN caso.geo
NSIMSH 1e3
"""

WATER_MAT = (" PENELOPE (v. 2024)  Material data file ...\n Material: WATER (278)\n Mass density = 1.0 g/cm**3\n"
             " *** ENDF filenames and abundances of isotopes,  NISOT =   2\n pO16_ICRU63.endf 8016 0.99\n"
             " pO17_TENDL.endf 8017 0.001\n   0 = NALPHA\n")


def test_unidentified_materials_are_the_ones_argus_cannot_generate(tmp_path):
    doc = parse_in_text(IN, str(tmp_path / "caso.in"))
    rows = materials_table(doc, None, tmp_path)
    assert [r.fname for r in unidentified(rows, MLIST)] == ["agua.mat", "raro.mat"]
    (tmp_path / "agua.mat").write_text(WATER_MAT, encoding="latin-1")  # ya está: no hace falta generarlo
    set_recipe(doc, 2, BRASS)  # con su composición propia
    assert unidentified(materials_table(doc, None, tmp_path), MLIST) == []


def test_endf_files_come_from_the_users_endf_folder(tmp_path):
    case, db, mine = tmp_path / "caso", tmp_path / "penhan" / "database", tmp_path / "mis_endf"
    for d in (case, db / "ENDF_data", mine):
        d.mkdir(parents=True)
    (case / "agua.mat").write_text(WATER_MAT, encoding="latin-1")
    (mine / "pO16_ICRU63.endf").write_text("mío\n")
    (db / "ENDF_data" / "pO16_ICRU63.endf").write_text("de la base\n")
    (db / "ENDF_data" / "pO17_TENDL.endf").write_text("de la base\n")
    doc = parse_in_text(IN.replace("MFNAME raro.mat", "MFNAME agua.mat"), str(case / "caso.in"))
    jobs = {e.name: e for e in mg.plan_endf(doc, case, db, mine)}
    assert jobs["pO16_ICRU63.endf"].source == mine / "pO16_ICRU63.endf"  # la carpeta elegida, antes
    assert jobs["pO17_TENDL.endf"].source == db / "ENDF_data" / "pO17_TENDL.endf"  # y si no, la de la base
    lost = mg.plan_endf(doc, case, None, tmp_path / "vacia")
    assert all(e.source is None and "carpeta de los ENDF" in e.message for e in lost)
    assert "Programas de penhan" in mg.plan_endf(doc, case, None, None)[0].message
    from argus.penhan.simfolder import plan_folder

    plan = plan_folder(case / "caso.in", doc, tmp_path / "sim", database=db, endf_dir=mine)
    src = {it.name: it.source for it in plan.items if it.kind == "endf"}
    assert src["pO16_ICRU63.endf"] == mine / "pO16_ICRU63.endf"


def test_the_endf_folder_setting(tmp_path):
    db = tmp_path / "database"
    (db / "ENDF_data").mkdir(parents=True)
    assert PenhanSetup(db).endf_folder() == db / "ENDF_data"
    assert PenhanSetup(db, endf=tmp_path).endf_folder() == tmp_path
    assert PenhanSetup(db, endf=tmp_path / "no_existe").endf_folder() is None
    assert PenhanSetup(None).endf_folder() is None


def test_the_endf_folder_is_remembered(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings

    from argus.ui.settings import penhan_setup, save_penhan_setup

    s = QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat)
    save_penhan_setup(PenhanSetup(tmp_path / "database", endf=tmp_path / "endf"), s)
    assert penhan_setup(s).endf == tmp_path / "endf"
    save_penhan_setup(PenhanSetup(tmp_path / "database"), s)
    assert penhan_setup(s).endf is None


# -- revisión de 0.16 (0.16.2) -----------------------------------------------------------------------------------------

def mat_header(name: str, number: int | None = None, density: float = 1.0, atoms: dict[int, float] | None = None,
               ivalue: float = 78.0) -> str:
    """La cabecera de un .mat como la escribe penhmat: nombre (y número de la lista), densidad, composición e I."""
    atoms = atoms or {1: 2.0, 8: 1.0}
    out = [" PENELOPE (v. 2018)  Material data file ...............",
           f" Material: {name}" + (f"  ({number})" if number else ""),
           f" Mass density = {density:.8E} g/cm**3",
           f" Number of elements in the molecule = {len(atoms):2d}"]
    out += [f"   atomic number = {z:2d},  atoms/molecule = {n:.8E}" for z, n in atoms.items()]
    out.append(f" Mean excitation energy = {ivalue:.8E} eV")
    return "\n".join(out) + "\n"


def one_material_in(line: str) -> str:
    return ("TITLE  x\nSKPAR  1\nSENERG 1e6\n" + line + "\nMSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN c.geo\n"
            "NSIMSH 10\n")


def test_a_mat_of_another_list_material_is_not_taken_for_the_one_in_the_in(tmp_path):
    """Revisión de 0.16: en la carpeta ya está hueso.mat del n.º 119 (de otro .in de la misma carpeta, o de un material
    que se quitó) y el .in dice «Hueso cortical (ICRP) (PENELOPE 120)» con ese fichero: penhmain usaría el 119. La
    tabla lo marca y materiales.txt y «Generar .mat y ENDF…» lo dicen, sin darlo por bueno ni sustituirlo."""
    (tmp_path / "hueso.mat").write_text(mat_header("BONE, COMPACT (ICRU)", 119, 1.85), encoding="latin-1")
    doc = parse_in_text(one_material_in("MFNAME hueso.mat            [Hueso cortical (ICRP) (PENELOPE 120)]"),
                        str(tmp_path / "c.in"))
    (row,) = materials_table(doc, None, tmp_path)
    assert row.exists and row.stale and row.stale_kind == "lista"
    assert any("n.º 119" in p.message and "n.º 120" in p.message for p in row.problems)
    assert "es el n.º 119 (BONE, COMPACT (ICRU)): hay que generarlo con otro nombre" in materials_txt([row], "c.in")
    (job,) = mg.plan_materials(doc, None, tmp_path, None, MLIST).mats
    assert job.answers is None and "es el n.º 119 (BONE, COMPACT (ICRU)), no el material del .in" in job.reason
    # Con el que dice el .in no hay nada que decir ni que generar.
    (tmp_path / "hueso.mat").write_text(mat_header("BONE, CORTICAL (ICRP)", 120, 1.92), encoding="latin-1")
    (row,) = materials_table(doc, None, tmp_path)
    assert not row.stale and not row.problems
    assert mg.plan_materials(doc, None, tmp_path, None, MLIST).mats == []


def test_the_number_in_the_name_is_enough_without_the_penelope_list(tmp_path):
    """Revisión de 0.16: sin database/material-list.txt (penhan sin configurar), «Agua líquida (PENELOPE 278)» se
    reconoce por su número: no sale entre los que ARGUS no sabe de qué están hechos y se genera con la opción 2 de
    penhmat y el 278, como dice materiales.txt."""
    doc = parse_in_text(one_material_in("MFNAME agua.mat             [Agua líquida (PENELOPE 278)]"),
                        str(tmp_path / "c.in"))
    rows = materials_table(doc, None, tmp_path)
    assert unidentified(rows, []) == []
    (job,) = mg.plan_materials(doc, None, tmp_path, None, []).mats
    assert job.answers is not None and job.answers("agua.mat").startswith("2\n278\n")
    assert job.what == "n.º 278 de la lista de PENELOPE" and not job.reason
    assert "agua.mat  (n.º 278)" in materials_txt(rows, "c.in", mlist=[])


def test_long_names_keep_the_columns_of_materiales_txt(tmp_path):
    """Revisión de 0.16: los nombres en español de más de 40 caracteres descuadraban la tabla de materiales.txt."""
    doc = parse_in_text(
        "TITLE  x\nSKPAR  1\nSENERG 1e6\n"
        "MFNAME fricke.mat           [Disolución dosimétrica de sulfato ferroso (Fricke) (PENELOPE 160)]\n"
        "MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nMFNAME agua.mat             [Agua líquida (PENELOPE 278)]\n"
        "MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN c.geo\nNSIMSH 10\n", str(tmp_path / "c.in"))
    lines = materials_txt(materials_table(doc, None, tmp_path), "c.in").splitlines()
    head = next(i for i, ln in enumerate(lines) if ln.startswith("N.º"))
    table = lines[head:head + 4]
    assert len({ln.index("Estado") if "Estado" in ln else ln.index("FALTA") for ln in table if ln[:1] != "-"}) == 1


STEEL = cp.Composition([(26, 70.0), (24, 18.0), (28, 12.0)], cp.BY_MASS, 7.9)


def test_the_library_keeps_what_this_version_does_not_understand(tmp_path):
    """Revisión de 0.16: al guardar cualquier cambio se perdían, sin copia, los materiales que no se podían leer (una
    densidad con coma escrita a mano, un modo de una versión más nueva), los campos desconocidos, la nota al editar y
    el número de versión del fichero (con la carpeta de datos sincronizada entre ordenadores, una versión vieja borraba
    lo de una nueva). Ahora se vuelven a escribir tal cual."""
    from argus.penhan.library import KIND

    p = tmp_path / "lib.json"
    comma = {"name": "Acero de Rafael", "elements": [[26, 70], [24, 18], [28, 12]], "by": "masa", "density": "7,9",
             "i_mode": "argus"}
    future = {"name": "Por volumen", "elements": [[1, 2], [8, 1]], "by": "volumen", "density": 1.0,
              "i_mode": "icru90"}
    brass = {"name": "Latón 70/30", "elements": [[29, 70], [30, 30]], "by": "masa", "density": 8.53, "i_mode": "argus",
             "note": "Medido en el laboratorio", "fuente": "certificado del fabricante"}
    p.write_text(json.dumps({"kind": KIND, "version": 2, "materials": [brass, comma, future]}, ensure_ascii=False),
                 encoding="utf-8")
    lib = Library(p)
    assert lib.names() == ["Latón 70/30"] and len(lib.problems) == 2
    m = lib.find("Latón 70/30")
    lib.put(LibraryMaterial(m.name, m.composition, "#c8a040"), replace=m.name)  # como «Editar…» (sin la nota)
    lib.put(LibraryMaterial("Nuevo", STEEL))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == 2
    saved = {d["name"]: d for d in data["materials"]}
    assert saved["Acero de Rafael"] == comma and saved["Por volumen"] == future
    assert saved["Latón 70/30"]["note"] == "Medido en el laboratorio" and saved["Latón 70/30"]["color"] == "#c8a040"
    assert saved["Latón 70/30"]["fuente"] == "certificado del fabricante" and "Nuevo" in saved


def test_two_argus_open_at_once_do_not_lose_materials(tmp_path):
    """Revisión de 0.16: cada ARGUS guardaba la lista que leyó al abrir, sin releer el fichero: lo que había guardado
    el otro se perdía."""
    p = tmp_path / "lib.json"
    a, b = Library(p), Library(p)
    a.put(LibraryMaterial("Acero 316L", STEEL))
    b.put(LibraryMaterial("Latón 70/30", BRASS))
    assert sorted(Library(p).names()) == ["Acero 316L", "Latón 70/30"]
    a.remove("Latón 70/30")  # también lo que guardó el otro
    assert Library(p).names() == ["Acero 316L"]


def test_library_names_are_unique_and_clean(tmp_path):
    """Revisión de 0.16: con «Latón» y «latón» en el fichero (escrito a mano), borrar la segunda fila borraba la
    primera; importar no limpiaba los nombres (saltos de línea, 100 caracteres); y un personalizado podía llamarse como
    uno de la lista de PENELOPE («… (PENELOPE 278)»), con lo que se confundían."""
    from argus.penhan.library import KIND, clean_name

    p = tmp_path / "lib.json"
    p.write_text(json.dumps({"kind": KIND, "version": 1, "materials": [
        {"name": "Latón", "elements": [[29, 70], [30, 30]], "by": "masa", "density": 8.53, "i_mode": "argus"},
        {"name": "latón", "elements": [[29, 60], [30, 40]], "by": "masa", "density": 8.4, "i_mode": "argus"},
        {"name": "Agua líquida (PENELOPE 278)", "elements": [[1, 2], [8, 1]], "by": "atomos", "density": 1.05,
         "i_mode": "argus"}]}, ensure_ascii=False), encoding="utf-8")
    lib = Library(p)
    assert lib.names() == ["Latón", "latón (2)", "Agua líquida"]
    lib.remove("latón (2)")
    assert [(m.name, m.composition.elements[0][1]) for m in Library(p).items] == [("Latón", 70.0),
                                                                                  ("Agua líquida", 2.0)]
    ext = tmp_path / "exportado.json"
    ext.write_text(json.dumps({"kind": KIND, "version": 1, "materials": [
        {"name": "Acero   inox\n316L", "elements": [[26, 70], [24, 18], [28, 12]], "by": "masa", "density": 7.9},
        {"name": "X" * 100, "elements": [[1, 2], [8, 1]], "by": "atomos", "density": 1.0},
        {"name": "Plomo (PENELOPE 82)", "elements": [[82, 1]], "by": "atomos", "density": 11.35}]}),
        encoding="utf-8")
    items, _problems = read_file(ext)
    assert lib.merge(items) == (3, 0, 0)
    names = Library(p).names()
    assert {"Acero inox 316L", "X" * 60, "Plomo"} <= set(names)
    with pytest.raises(ValueError):
        lib.put(LibraryMaterial("X" * 100, BRASS))  # el mismo nombre, recortado a 60
    assert clean_name("  Hueso cortical (ICRP) (PENELOPE 120) ") == "Hueso cortical (ICRP)"
    assert lib.find("Agua líquida (PENELOPE 278)").name == "Agua líquida"


def _database_list(tmp_path):
    """Una base de datos de penhan pequeña: material-list.txt y pdfiles/pdcompos.pen (átomos por molécula y densidad,
    como los de PENELOPE) de unos cuantos materiales."""
    compos = {1: ("HYDROGEN", 8.3748e-05, [(1, 1)]), 6: ("AMORPHOUS CARBON", 2.0, [(6, 1)]),
              8: ("OXYGEN", 1.33151e-03, [(8, 1)]), 13: ("ALUMINIUM", 2.6989, [(13, 1)]),
              27: ("COBALT", 8.9, [(27, 1)]), 81: ("THALLIUM", 11.72, [(81, 1)]), 82: ("LEAD", 11.35, [(82, 1)]),
              106: ("ALUMINUM OXIDE", 3.97, [(8, 3), (13, 2)]), 134: ("CARBON DIOXIDE", 1.84212e-03, [(6, 1), (8, 2)]),
              141: ("CESIUM IODIDE", 4.51, [(53, 1), (55, 1)]), 183: ("LEAD OXIDE", 9.53, [(8, 1), (82, 1)]),
              222: ("POLYETHYLENE", 0.94, [(1, 2), (6, 1)]), 239: ("PROPANE", 1.87939e-03, [(1, 8), (6, 3)]),
              240: ("PROPANE, LIQUID", 0.43, [(1, 8), (6, 3)]), 278: ("WATER, LIQUID", 0.99821, [(1, 2), (8, 1)]),
              279: ("WATER VAPOUR", 7.56182e-04, [(1, 2), (8, 1)])}
    extra = [(1, "Hydrogen"), (8, "Oxygen"), (27, "Cobalt"), (81, "Thallium"), (106, "Aluminum oxide"),
             (134, "Carbon dioxide"), (141, "Cesium iodide"), (239, "Propane"), (240, "Propane, liquid")]
    items = sorted(set(MLIST) | set(extra))
    db = tmp_path / "database"
    (db / "pdfiles").mkdir(parents=True)
    (db / "material-list.txt").write_text("".join(f"{n:4d}  {name}\n" for n, name in items), encoding="latin-1")
    lines = ["*: composiciones de prueba", "", "            *****************************************", ""]
    for n, (name, rho, atoms) in sorted(compos.items()):
        lines.append(f"{n:3d}  {name}  ({n})".ljust(66) + f"{n:3d}")
        lines.append(f"       {len(atoms)}  5.00000E-01  7.50000E+01  {rho:.5E}")
        lines += [f"      {z:2d}  1.00000E+00  {k:.5E}" for z, k in atoms]
    (db / "pdfiles" / "pdcompos.pen").write_text("\n".join(lines) + "\n", encoding="latin-1")
    return load_material_list(db / "material-list.txt")


@pytest.mark.parametrize("fname,expected", [
    ("H2O.mat", 278), ("h2o.mat", 278), ("CO2.mat", 134), ("co2.mat", 134), ("Al2O3.mat", 106), ("SIO2.mat", None),
    ("CsI_Tl.mat", 141), ("Ti6Al4V.mat", None), ("C3H8.mat", 240), ("PbO.mat", 183),
    ("water_1cm.mat", 278), ("agua5cm.mat", 278), ("plomo_1in.mat", 82), ("agua_in.mat", 278),
    ("C-552.mat", 126), ("c552.mat", 126), ("c.mat", 6), ("carbon.mat", 6), ("aluminum.mat", 13), ("lead.mat", 82),
    ("hueso.mat", None), ("hueso_cort.mat", 120), ("tejido_blando.mat", 262), ("PE.mat", 222),
])
def test_proposals_from_formulas_codes_and_units(tmp_path, fname, expected):
    """Revisión de 0.16 («Asignar materiales…» las rellena): «H2O.mat» proponía el hidrógeno, «CO2.mat» el cobalto,
    «water_1cm.mat» el curio, «C552» el carbono, «aluminum» la alúmina y «carbon» el dióxido de carbono. Las fórmulas
    se reconocen con las de la base de datos (pdcompos.pen; con dos estados, el más denso), las unidades no cuentan,
    los símbolos pesan menos que las palabras y, si hay empate, no se propone nada."""
    entries = mn.penelope_entries(_database_list(tmp_path))
    best = mn.best_guess([fname], entries)
    assert (best.number if best else None) == expected


def test_without_the_formulas_a_formula_is_not_taken_for_its_symbols():
    """Sin pdcompos.pen junto a la lista, una fórmula no se puede reconocer, pero tampoco se toma por sus símbolos
    («CO2» no es el cobalto, «H2O» no es el hidrógeno)."""
    entries = mn.penelope_entries(MLIST + [(1, "Hydrogen"), (8, "Oxygen"), (27, "Cobalt")])
    for fname in ("CO2.mat", "co2.mat", "H2O.mat", "Ti6Al4V.mat"):
        assert mn.best_guess([fname], entries) is None, fname
    assert mn.best_guess(["agua_2cm.mat"], entries).number == 278


def test_search_finds_the_whole_label_and_skips_personal_summaries(tmp_path):
    """Revisión de 0.16: pegar la etiqueta entera («Agua líquida (PENELOPE 278)») o «penelope 278» no encontraba
    nada, y los personalizados se buscaban en su resumen («g/cm³», «I =», «eV»): con «C» o «I» salían todos delante del
    carbono o del yodo. Ahora se buscan por su nombre y por sus elementos, enteros («zn» sí encuentra el latón); y con
    las fórmulas de la base de datos, «H2O» encuentra el agua."""
    ents = [mn.personal_entry("Latón 70/30", BRASS.summary())] + mn.penelope_entries(_database_list(tmp_path))
    assert _texts(mn.search(ents, "Agua líquida (PENELOPE 278)")) == ["Agua líquida (PENELOPE 278)"]
    assert _texts(mn.search(ents, "Water, liquid (PENELOPE 278)")) == ["Agua líquida (PENELOPE 278)"]
    assert _texts(mn.search(ents, "penelope 278")) == ["Agua líquida (PENELOPE 278)"]
    assert "Latón 70/30" not in _texts(mn.search(ents, "C")) + _texts(mn.search(ents, "I"))
    assert _texts(mn.search(ents, "zn"))[:2] == ["Latón 70/30", "Zinc (PENELOPE 30)"]
    assert _texts(mn.search(ents, "H2O"))[:2] == ["Agua líquida (PENELOPE 278)", "Vapor de agua (PENELOPE 279)"]
    assert len(mn.search(ents, "PE")) < 10  # («penelope» no es una palabra de cada material)
