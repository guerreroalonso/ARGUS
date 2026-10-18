"""Composición propia de un material (0.14.0): la opción 1 de penhmat además de la lista de PENELOPE.

Lo que escribe el usuario (fórmula o pares elemento-cantidad), lo que se contesta a penhmat, cómo se guarda en el .in
(comentarios «[ARGUS fichero.mat: …]» junto a su MFNAME, que penhmain salta), el aviso si el .mat que hay no tiene esa
composición, «Generar .mat y ENDF…», materiales.txt y el diálogo. La última prueba usa el penhmat real si está
(``ARGUS_PENHMAT`` y ``ARGUS_PENHAN_DB``)."""

import os
import shutil
from pathlib import Path

import pytest

from argus.penhan import composition as cp
from argus.penhan import materials as mt
from argus.penhan import matgen as mg
from argus.penhan import programs as pg
from argus.penhan.inputfile import parse_in_text

IN = """TITLE  Prueba de composiciones
SKPAR  4              [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]
SENERG 1.0e8          [Initial energy (monoenergetic sources only)]
SPOSIT 0 0 -10        [Coordinates of the source]
SCONE  0 0 0          [Conical beam; angles in deg]
MFNAME agua.mat                       [Water, liquid]
MSIMPA 1.0e3 1.0e3 1.0e3 0.1 0.1 1e3 1e3
MFNAME pmma.mat                       [PMMA]
MSIMPA 1.0e3 1.0e3 1.0e3 0.1 0.1 1e3 1e3
MFNAME aire.mat                       [Aire seco]
MSIMPA 1.0e3 1.0e3 1.0e3 0.1 0.1 1e3 1e3
GEOMFN caso.geo                       [Geometry file, up to 20 chars]
NSIMSH 1.0e3          [Desired number of simulated showers]
"""

PMMA = cp.Composition([(6, 5), (1, 8), (8, 2)], cp.BY_ATOMS, 1.19)
AIR = cp.Composition([(6, 0.000124), (7, 0.755268), (8, 0.231781), (18, 0.012827)], cp.BY_MASS, 0.00120479)


# ---------------------------------------------------------------------------------------------------------------------
# Lo que escribe el usuario
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, elements, by", [
    ("C5H8O2", [(6, 5), (1, 8), (8, 2)], cp.BY_ATOMS),
    ("H2O", [(1, 2), (8, 1)], cp.BY_ATOMS),
    ("Ca10(PO4)6(OH)2", [(20, 10), (15, 6), (8, 26), (1, 2)], cp.BY_ATOMS),
    ("CuSO4·5H2O", [(29, 1), (16, 1), (8, 9), (1, 10)], cp.BY_ATOMS),
    ("C2.5H4", [(6, 2.5), (1, 4)], cp.BY_ATOMS),
    ("H 0.111894 O 0.888106", [(1, 0.111894), (8, 0.888106)], None),
    ("H 0,111894 O 0,888106", [(1, 0.111894), (8, 0.888106)], None),  # coma decimal
    ("1 2 8 1", [(1, 2), (8, 1)], None),  # números atómicos
    ("H: 2, O: 1", [(1, 2), (8, 1)], None),
    ("h 2 o 1", [(1, 2), (8, 1)], None),  # en pares, los símbolos sin mayúsculas también
    ("H 11.19 % O 88.81 %", [(1, 11.19), (8, 88.81)], cp.BY_MASS),
    ("Co", [(27, 1)], cp.BY_ATOMS),
    ("CO2", [(6, 1), (8, 2)], cp.BY_ATOMS),
])
def test_what_the_user_writes(text, elements, by):
    got, kind = cp.parse_elements(text)
    assert [(z, pytest.approx(v)) for z, v in got] == elements and kind == by


@pytest.mark.parametrize("text, why", [
    ("", "Escriba"), ("Xx2", "«Xx» no es un elemento"), ("co2", "mayúscula"), ("H2O)", "no se entiende"),
    ("Ca(OH2", "paréntesis"),
])
def test_what_is_not_understood_is_explained(text, why):
    with pytest.raises(ValueError, match=why):
        cp.parse_elements(text)


def test_guessing_atoms_or_mass_fractions():
    assert cp.guess_by([(1, 2), (8, 1)]) == cp.BY_ATOMS
    assert cp.guess_by([(1, 0.111894), (8, 0.888106)]) == cp.BY_MASS
    assert cp.guess_by([(1, 11.19), (8, 88.81)]) == cp.BY_MASS
    assert cp.guess_by([(1, 0.3), (8, 0.3)]) is None


def test_what_penhmat_would_not_accept():
    assert PMMA.problems() == []
    bad = cp.Composition([(1, 2), (1, 1), (0, 1), (8, -1)], cp.BY_ATOMS, 0.0, cp.I_VALUE, 0.5)
    probs = " ".join(bad.problems())
    for word in ("dos veces", "entre 1 y 99", "positivo", "densidad", "1 eV"):
        assert word in probs
    assert "30" in " ".join(cp.Composition([(z, 1) for z in range(1, 32)]).problems())
    with pytest.raises(ValueError):
        bad.check()


def test_amounts_in_atoms_and_in_mass():
    water = cp.Composition([(1, 2), (8, 1)])
    fr = water.mass_fractions()
    assert fr[1] == pytest.approx(0.111894, abs=5e-6) and sum(fr.values()) == pytest.approx(1.0)
    by_mass = cp.Composition([(1, 0.111894), (8, 0.888106)], cp.BY_MASS)
    # Como penhmat: w/A con el mayor en 1.
    assert by_mass.atoms()[1] == pytest.approx(1.0) and by_mass.atoms()[8] == pytest.approx(0.5, rel=1e-4)
    assert water.formula() == "H2O" and PMMA.formula() == "C5H8O2"
    assert "H 11,19 %" in by_mass.formula()
    assert len(AIR.isotopes()) == 2 + 2 + 3 + 3  # C, N, O, Ar (penhmat elige los de isotopic_abundances.tab)


def test_mean_excitation_energy_choices():
    assert cp.Composition([(1, 2), (8, 1)]).excitation() == (78.0, True, "regla de ARGUS para sólidos y líquidos")
    i_air, yes, why = AIR.excitation()
    assert i_air == pytest.approx(85.7, abs=0.05) and not yes and "gas" in why  # PENELOPE = ICRU para el aire
    assert cp.Composition([(13, 1)], density=2.699).excitation()[:2] == (166.0, False)  # el del elemento
    pen = cp.Composition(PMMA.elements, PMMA.by, PMMA.density, cp.I_PENELOPE).excitation()
    assert pen[1] is False and pen[0] == pytest.approx(68.6, abs=0.05)
    assert cp.Composition(PMMA.elements, PMMA.by, PMMA.density, cp.I_VALUE, 74.0).excitation()[:2] == (74.0, True)


def test_answers_to_penhmat():
    assert PMMA.penhmat_input("pmma.mat", "PMMA") == "1\nPMMA\n3\n1\n6 5\n1 8\n8 2\n1\n73.4\n1.19\n2\npmma.mat\n\n"
    assert AIR.penhmat_input("aire.mat", "Aire seco") == (
        "1\nAire seco\n4\n2\n6 0.000124\n7 0.755268\n8 0.231781\n18 0.012827\n2\n0.00120479\n2\naire.mat\n\n")
    lead = cp.Composition([(82, 1)], cp.BY_MASS, 11.35)
    assert lead.penhmat_input("plomo.mat") == "1\nplomo\n1\n82\n2\n11.35\n2\nplomo.mat\n\n"  # un elemento: sin opción
    value = cp.Composition(PMMA.elements, PMMA.by, 1.19, cp.I_VALUE, 74.0)
    assert "\n1\n74\n1.19\n" in value.penhmat_input("p.mat")
    # El nombre, en ASCII y como mucho 60 caracteres (lo que lee penhmat).
    name = PMMA.penhmat_input("p.mat", "Polimetacrilato de metilo — «metacrílico» " * 3).split("\n")[1]
    assert name.startswith("Polimetacrilato de metilo metacrilico Poli") and len(name) <= 60 and name.isascii()
    steps = PMMA.penhmat_steps("pmma.mat", "PMMA")
    assert steps[0].startswith("Opción 1") and any("73.4 eV" in s for s in steps) and steps[-1].startswith("Pulse")


def test_comment_lines_round_trip_and_fit_in_127_columns():
    texts = PMMA.comment_texts("pmma.mat")
    assert texts == ["[ARGUS pmma.mat: composicion por atomos; densidad 1.19 g/cm3; I segun ARGUS]",
                     "[ARGUS pmma.mat: C 5 H 8 O 2]"]
    many = cp.Composition([(z, 0.001 * z) for z in range(1, 31)], cp.BY_MASS, 2.5, cp.I_VALUE, 123.4)
    texts = many.comment_texts("aleacion-larga.mat")
    assert len(texts) > 2 and all(7 + len(t) <= 127 for t in texts)
    bodies = [cp.comment_parts(t)[1] for t in texts]
    assert cp.Composition.from_comment_bodies(bodies) == many
    with pytest.raises(ValueError, match="falta la línea"):
        cp.Composition.from_comment_bodies(["C 5 H 8"])
    with pytest.raises(ValueError, match="no se entiende"):
        cp.Composition.from_comment_bodies(["composicion por atomos; densidad 1 g/cm3; I segun ARGUS", "C cinco"])
    # A mano, con tildes y mayúsculas, también se entiende.
    hand = cp.Composition.from_comment_bodies(["Composición por átomos; densidad 1.19 g/cm3; I según PENELOPE",
                                               "C 5 H 8", "O 2"])
    assert hand == cp.Composition(PMMA.elements, cp.BY_ATOMS, 1.19, cp.I_PENELOPE)


# ---------------------------------------------------------------------------------------------------------------------
# En el .in
# ---------------------------------------------------------------------------------------------------------------------

def test_composition_is_kept_in_the_in_and_penhmain_skips_it():
    doc = parse_in_text(IN)
    mt.set_recipe(doc, 2, PMMA)
    text = doc.text()
    lines = text.splitlines()
    i = lines.index("MFNAME pmma.mat                       [PMMA]")
    assert lines[i + 1] == "       [ARGUS pmma.mat: composicion por atomos; densidad 1.19 g/cm3; I segun ARGUS]"
    assert lines[i + 2] == "       [ARGUS pmma.mat: C 5 H 8 O 2]"
    assert lines[i + 3].startswith("MSIMPA")
    again = parse_in_text(text)
    assert not [d for d in again.diagnostics if d.severity == 2]  # PMRDR0 salta las líneas en blanco
    assert [m.fname for m in again.model.materials] == ["agua.mat", "pmma.mat", "aire.mat"]
    rec = mt.material_recipe(again, 2)
    assert rec.composition == PMMA and rec.error is None
    assert mt.material_recipe(again, 1) is None
    # Otra composición la sustituye; None la quita.
    mt.set_recipe(again, 2, AIR)
    assert mt.material_recipe(again, 2).composition == AIR and len(mt.recipes(again)) == 1
    mt.set_recipe(again, 2, None)
    assert mt.recipes(again) == {} and again.text() == IN


def test_composition_follows_its_file():
    doc = parse_in_text(IN)
    mt.set_recipe(doc, 2, PMMA)
    mt.set_material(doc, 2, fname="lucita.mat")
    assert list(mt.recipes(doc)) == ["lucita.mat"] and mt.material_recipe(doc, 2).composition == PMMA
    # Quitar el último material quita su composición (si ningún otro usa su fichero).
    mt.set_recipe(doc, 3, AIR)
    mt.remove_last_material(doc)
    assert list(mt.recipes(doc)) == ["lucita.mat"]
    # Si alguien mueve las líneas a mano, el nombre del fichero dice de quién son.
    lines = doc.text().splitlines()
    moved = [ln for ln in lines if "[ARGUS" not in ln]
    moved.insert(moved.index("GEOMFN caso.geo                       [Geometry file, up to 20 chars]"),
                 [ln for ln in lines if "[ARGUS" in ln][0])
    moved.insert(1, [ln for ln in lines if "[ARGUS" in ln][1])
    doc2 = parse_in_text("\n".join(moved) + "\n")
    assert mt.material_recipe(doc2, 2).composition == PMMA and mt.material_recipe(doc2, 1) is None


def test_a_composition_that_is_not_understood_is_reported():
    text = IN.replace("MFNAME pmma.mat                       [PMMA]\n",
                      "MFNAME pmma.mat                       [PMMA]\n       [ARGUS pmma.mat: C 5 H ocho]\n")
    doc = parse_in_text(text)
    rows = mt.materials_table(doc)
    assert rows[1].recipe is None and rows[1].recipe_error
    assert any("no se entiende" in p.message for p in rows[1].problems)


def _mat(path: Path, density: float, atoms: dict[int, float], ivalue: float) -> None:
    lines = [" PENELOPE (v. 2018)  Material data file ...............", " Material: PRUEBA",
             f" Mass density = {density:.8E} g/cm**3", f" Number of elements in the molecule = {len(atoms):2d}"]
    lines += [f"   atomic number = {z:2d},  atoms/molecule = {n:.8E}" for z, n in atoms.items()]
    lines += [f" Mean excitation energy = {ivalue:.8E} eV", " ...",
              " *** ENDF filenames and abundances of isotopes,  NISOT =   0"]
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_an_existing_mat_with_another_composition_is_not_taken_as_good(tmp_path):
    doc = parse_in_text(IN)
    mt.set_recipe(doc, 2, PMMA)
    _mat(tmp_path / "pmma.mat", 1.19, {6: 5, 1: 8, 8: 2}, 73.4)
    row = mt.materials_table(doc, None, tmp_path)[1]
    assert row.exists and not row.stale and not row.problems
    _mat(tmp_path / "pmma.mat", 1.18, {6: 5, 1: 8, 8: 2}, 73.4)
    row = mt.materials_table(doc, None, tmp_path)[1]
    assert row.stale and "otra densidad" in row.problems[0].message and not row.problems[0].error
    plan = mg.plan_materials(doc, None, tmp_path, None, mlist=[])
    job = next(j for j in plan.mats if j.fname == "pmma.mat")
    assert not job.possible and "no tiene la composición" in job.reason
    assert (tmp_path / "pmma.mat").read_text(encoding="latin-1").count("1.18") == 1  # no se ha tocado


def test_generate_uses_the_composition_and_materiales_txt_tells_the_answers(tmp_path):
    doc = parse_in_text(IN)
    mt.set_recipe(doc, 2, PMMA)
    mt.set_recipe(doc, 3, AIR)
    plan = mg.plan_materials(doc, None, tmp_path, None, mlist=[(278, "Water, liquid")])
    jobs = {j.fname: j for j in plan.mats}
    assert jobs["pmma.mat"].possible and "composición propia" in jobs["pmma.mat"].what
    assert jobs["pmma.mat"].answers("argus-1.mat") == PMMA.penhmat_input("argus-1.mat", "PMMA")
    assert jobs["aire.mat"].answers("t.mat").split("\n")[1] == "Aire seco"
    assert jobs["agua.mat"].answers("t.mat") == "2\n278\n2\nt.mat\n\n"  # la lista de PENELOPE, como siempre
    text = mt.materials_txt(mt.materials_table(doc, None, tmp_path), "caso.in", mlist=[(278, "Water, liquid")])
    assert "Materiales con composición propia" in text and "1.19" in text and "pmma.mat  (PMMA: C5H8O2" in text
    assert "Materiales de la lista de PENELOPE" in text and "agua.mat  (n.º 278: Water, liquid)" in text


# ---------------------------------------------------------------------------------------------------------------------
# El penhmat de verdad
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="el penhmat de las pruebas es de Linux")
@pytest.mark.oracle
def test_the_real_penhmat_generates_own_compositions(tmp_path):
    exe, db_real = os.environ.get("ARGUS_PENHMAT"), os.environ.get("ARGUS_PENHAN_DB")
    if not exe or not db_real:
        pytest.skip("penhmat no disponible (ARGUS_PENHMAT y ARGUS_PENHAN_DB)")
    db = tmp_path / "database"
    db.mkdir()
    for sub in pg.DATABASE_PARTS:
        (db / sub).symlink_to(Path(db_real) / sub)
    shutil.copy2(Path(db_real) / "material-list.txt", db / "material-list.txt")
    penhmat = db / "penhmat.x"
    shutil.copy2(exe, penhmat)
    case = tmp_path / "caso"
    case.mkdir()
    doc = parse_in_text(IN)
    comps = {2: PMMA, 3: AIR}
    for n, comp in comps.items():
        mt.set_recipe(doc, n, comp)
    plan = mg.plan_materials(doc, None, case, db)
    mg.generate(plan, penhmat, pg.launch_for(pg.exe_info(penhmat)), db, doc=doc, timeout=600)
    status = {j.fname: (j.status, j.message) for j in plan.mats}
    assert status["pmma.mat"][0] == "done" and status["aire.mat"][0] == "done", status
    for n, comp in comps.items():
        fname = doc.find("MFNAME")[n - 1].text.strip()
        assert comp.differences(cp.read_mat_composition(case / fname)) == []
        nuc = mt.read_mat_nuclear(case / fname)
        assert nuc.complete and all((case / f).is_file() for f, _z, _a in nuc.isotopes)
    rows = mt.materials_table(doc, None, case)
    assert not any(r.stale for r in rows) and not [p for r in rows for p in r.problems]
    assert not [p for p in db.iterdir() if p.name.startswith(mg.TEMP_PREFIX)]
