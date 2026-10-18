"""Comprobaciones de penhmain sobre las secciones del .in (PMRDR) y volcados (RESUME/DUMPTO).

Las pruebas de oráculo generan variantes de los .in del corpus (con valores
válidos e inválidos en todas las secciones), las pasan por el PMRDR real de
penhmain (sonda ``argus_simprobe``: penhmain.f sin simular, con la física
sustituida) y exigen que ARGUS dé el mismo error (IRETRN) o ninguno, y que
calcule igual los parámetros efectivos de los materiales.
"""

import random
import shutil
import zlib
from pathlib import Path

import pytest

from argus.core.diagnostics import Severity
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry
from argus.penhan import sections as sc
from argus.penhan.dumpfile import parse_dump_text, read_dump, source_differences
from argus.penhan.inputfile import parse_in_file, parse_in_text
from argus.penhan.validate import validate_case
from conftest import CORPUS

CASE = CORPUS / "usuario/100Mev15-174"
CASES = [p for p in sorted(CORPUS.rglob("*.in")) if any(q.suffix == ".geo" for q in p.parent.iterdir())
         and "nuclide" not in str(p)]
CASE_IDS = [str(p.relative_to(CORPUS)) for p in CASES]


def load_case(in_path: Path, tmp: Path):
    """Copia la carpeta del caso a ``tmp`` y devuelve (doc, geo)."""
    for f in in_path.parent.iterdir():
        if f.is_file():
            shutil.copy2(f, tmp / f.name)
    doc = parse_in_file(tmp / in_path.name)
    geo_path = tmp / doc.model.geomfn
    geo = compile_geometry(load_geometry(geo_path), parinp=doc.model.parinp_list() or None, base_dir=tmp)
    return doc, geo


def codes(d, severity=Severity.ERROR):
    return {x.code for x in d if x.severity == severity}


def messages(d):
    return " | ".join(x.message for x in d)


@pytest.fixture
def case(tmp_path):
    return load_case(CASE / "maniqui.in", tmp_path) + (tmp_path,)


# ---------------------------------------------------------------------------
# Casos concretos (sin sonda)
# ---------------------------------------------------------------------------

def test_user_case_has_no_errors(case):
    doc, geo, folder = case
    d = validate_case(doc, geo, folder)
    assert not d.has_errors, messages(d.errors)
    # El espectro repite 100 MeV: penhmain emite ~27 % exactamente a 100 MeV.
    assert any("misma energía (100 MeV)" in x.message and x.severity == Severity.WARNING for x in d)


def test_body_list_codes(case):
    doc, geo, folder = case
    sc.set_body_list(doc, "DSMAX", [sc.BodyValue(None, 9, [0.1])])
    assert 3055 in codes(validate_case(doc, geo, folder))
    sc.set_body_list(doc, "DSMAX", [sc.BodyValue("   1", None, [0.1])])
    sc.set_body_list(doc, "EABSB", [sc.BodyValue("ZZZZ", None, [1e3, 1e3, 1e3])])
    d = validate_case(doc, geo, folder)
    assert 3055 not in codes(d) and 3056 in codes(d)


def test_field_in_void_and_forcing_incompatibilities(case):
    doc, geo, folder = case
    sc.set_body_list(doc, "EFIELD", [sc.BodyValue(None, 2, [1e3, 0.0, 0.0])])  # el cuerpo 2 es vacío
    assert 3058 in codes(validate_case(doc, geo, folder))
    sc.set_body_list(doc, "EFIELD", [sc.BodyValue(None, 1, [1e3, 0.0, 0.0])])
    sc.set_body_list(doc, "IFORCE", [sc.BodyValue(None, 1, [4, 3, 10.0, 0.5, 2.0])])
    assert 3065 in codes(validate_case(doc, geo, folder))
    sc.set_body_list(doc, "EFIELD", [])
    sc.set_body_list(doc, "IFORCE", [sc.BodyValue(None, 1, [2, 1, 10.0, 0.5, 2.0])])  # Rayleigh: no se fuerza
    assert 3061 in codes(validate_case(doc, geo, folder))


def test_splitting_needs_forcing(case):
    doc, geo, folder = case
    sc.set_body_list(doc, "IBRSPL", [sc.BodyValue(None, 1, [5])])
    assert 3068 in codes(validate_case(doc, geo, folder))
    sc.set_body_list(doc, "IFORCE", [sc.BodyValue(None, 1, [1, 4, 5.0, 0.5, 2.0])])
    assert 3068 not in codes(validate_case(doc, geo, folder))


def test_energy_detector_incompatible_with_forcing(case):
    doc, geo, folder = case
    sc.set_energy_detectors(doc, [sc.EnergyDetector(0.0, 0.0, 100, bodies=[sc.BodyRef(None, 1)])])
    assert not validate_case(doc, geo, folder).has_errors
    sc.set_body_list(doc, "IXRSPL", [sc.BodyValue(None, 1, [1])])  # cualquier IXRSPL cuenta
    assert 3095 in codes(validate_case(doc, geo, folder))


def test_impact_detector_codes(case):
    doc, geo, folder = case
    det = sc.ImpactDetector(0.0, 0.0, 100, 0, 1, bodies=[sc.BodyRef(None, 1)])
    sc.set_impact_detectors(doc, [det])
    assert not validate_case(doc, geo, folder).has_errors
    for change, code in ((dict(nbe=0), 3076), (dict(el=1e9, eu=1e3), 3077), (dict(ipsf=2), 3078),
                         (dict(idcut=3), 3079), (dict(bodies=[sc.BodyRef(None, 2)]), 3084),
                         (dict(bodies=[]), 3085), (dict(bodies=[sc.BodyRef(None, 7)]), 3082)):
        bad = sc.ImpactDetector(**{**det.__dict__, **change})
        bad.line, bad.lines = None, {}
        sc.set_impact_detectors(doc, [bad])
        assert code in codes(validate_case(doc, geo, folder)), change
    sc.set_impact_detectors(doc, [det, sc.ImpactDetector(0.0, 0.0, 100, 0, 1, bodies=[sc.BodyRef(None, 1)])])
    assert 3083 in codes(validate_case(doc, geo, folder))
    # IDFLNC no lo usa penhmain 2025; con IDCUT = 2 la fluencia toma el nombre de IDPSF.
    sc.set_impact_detectors(doc, [sc.ImpactDetector(0.0, 0.0, 100, 1, 2, psf="a.psf", flnc="f.dat",
                                                    bodies=[sc.BodyRef(None, 1)])])
    d = validate_case(doc, geo, folder)
    assert any("IDFLNC" in x.message for x in d) and any("mismo fichero" in x.message for x in d.errors)


def test_dose_grid_codes(case):
    doc, geo, folder = case
    sc.set_value(doc, "GRIDR", None)
    assert any("GRIDZ sin GRIDR" in x.message for x in validate_case(doc, geo, folder).errors)
    sc.set_value(doc, "GRIDX", [-1.0, 1.0, 10])
    d = validate_case(doc, geo, folder)
    assert 3105 in codes(d)
    sc.set_value(doc, "GRIDY", [-1.0, 1.0, 10])
    sc.set_value(doc, "GRIDR", [5.0, 10])
    assert 3109 in codes(validate_case(doc, geo, folder))


def test_job_values_penhmain_changes(case):
    doc, geo, folder = case
    sc.set_value(doc, "TIME", [0.0])
    sc.set_value(doc, "NSIMSH", [0.0])
    sc.set_seeds(doc, [(-5000, 1)])
    d = validate_case(doc, geo, folder)
    text = messages(x for x in d if x.severity == Severity.WARNING)
    assert "100 s" in text and "2e9" in text and "1001" in text


def test_effective_material_clamps():
    doc = parse_in_file(CASE / "maniqui.in")
    sc.set_material_param(doc, 1, "MSIMPA", [10.0, 1e3, 1e4, 0.5, -0.1, -5.0, 3.0])
    eff = sc.effective_material(doc, 1)
    assert (eff["EABS1"], eff["C1"], eff["C2"], eff["WCC"], eff["WCR"]) == (50.0, 0.2, 0.0, 0.0, 10.0)
    sc.set_material_param(doc, 1, "MSIMPA", [1e3, 1e3, 1e3, 0.1, 0.1, 1e3, -1.0])
    assert sc.effective_material(doc, 1)["WCR"] == -10.0


# ---------------------------------------------------------------------------
# Volcados (RESUME / DUMPTO)
# ---------------------------------------------------------------------------

def dump_text(title="Title of the job, up to 65 characters.", showers=1.0e6, nseb=2, energies=(1e7, 2e7, 3e7),
              probs=(1.0, 1.0, 1e-75), counts=(5e5, 5e5, 0.0), nbody=3, complete=True):
    """Cabecera de un volcado con el formato de las escrituras dirigidas por lista de gfortran."""

    def row(vals):
        return "".join(f"  {v:24.16E}" for v in vals)

    lines = [f"  {showers:24.16f}  {1234.5:24.16f}  {1200.25:24.16f}", title.ljust(65)[:65], "   1234567   7654321",
             "           0   0.0000000000000000      F", "           4", f"  {nseb:10d} T F",
             row(energies), row(probs), row(counts)]
    if complete:
        lines += [row([0.0] * 7) + "           0           0"]
        lines += [row([0.0] * n) for n in (3, 3, 18, 18, 2, 2, 2, 2, 2, 2, 24, 24, 3, 3)]
        lines += [f"  {nbody:10d}", "   (resto del volcado)", "", "   *** END ***"]
    return "\n".join(lines) + "\n"


def test_parse_dump_header():
    info = parse_dump_text(dump_text())
    assert info.state == "ok" and info.showers == 1e6 and info.nseb == 2 and info.lspec and not info.lline
    assert info.energies == [1e7, 2e7, 3e7] and info.counts[:2] == [5e5, 5e5] and info.nbody == 3
    assert parse_dump_text("   Empty.\n").state == "empty"
    assert parse_dump_text("Empty file. Initialisation of MultiRuns.\n").state == "empty"
    assert parse_dump_text("").state == "empty"
    assert parse_dump_text(dump_text(complete=False)).problem == "truncated"
    assert parse_dump_text(dump_text().replace("   *** END ***", "")).problem == "truncated"
    assert parse_dump_text(dump_text().replace("   1234567", "   abc")).problem == "format"
    assert parse_dump_text("  1.0  2.0  3.0\n").state == "corrupt"


def test_dump_source_differences():
    doc = parse_in_text("TITLE  t\nSKPAR  4\nSPECTR 1e7 1\nSPECTR 2e7 1\nSPECTR 3e7 -1\nSPOSIT 0 0 0\n"
                        "MFNAME water.mat\nGEOMFN a.geo\n")
    assert source_differences(parse_dump_text(dump_text()), doc) == []
    other = parse_dump_text(dump_text(energies=(1e7, 2.5e7, 3e7)))
    assert source_differences(other, doc) and "espectro" in source_differences(other, doc)[0]
    sc.set_spectrum(doc, [(1e7, 1.0), (3e7, -1.0)])
    assert "el .in, 1 intervalo" in source_differences(parse_dump_text(dump_text()), doc)[0]


def test_resume_states(case):
    doc, geo, folder = case
    title = doc.model.title
    assert sc.text(doc, "RESUME") == "dump1.dmp"

    def resume_messages():
        d = validate_case(doc, geo, folder)
        return [x for x in d if x.message.startswith("RESUME")]

    (folder / "dump1.dmp").unlink(missing_ok=True)
    assert "no existe" in resume_messages()[0].message
    (folder / "dump1.dmp").write_text("   Empty.\n")
    m = resume_messages()[0]
    assert m.severity == Severity.INFO and "desde cero" in m.message
    (folder / "dump1.dmp").write_text(dump_text(title="Otra simulación"))
    assert resume_messages()[0].code == 3112
    (folder / "dump1.dmp").write_text(dump_text(title=title, complete=False))
    m = resume_messages()[0]
    assert m.severity == Severity.ERROR and "incompleto" in m.message
    (folder / "dump1.dmp").write_text(dump_text(title=title).replace("   1234567", "   abc"))
    assert resume_messages()[0].code == 3113
    # Volcado válido de esta simulación pero con otro espectro: penhmain no lo comprueba.
    (folder / "dump1.dmp").write_text(dump_text(title=title))
    m = resume_messages()[0]
    assert m.severity == Severity.WARNING and "no simulará ninguna más" in m.message and "espectro" in m.message
    assert read_dump(folder / "dump1.dmp").usable


# ---------------------------------------------------------------------------
# Oráculo: variantes aleatorias leídas por el PMRDR real
# ---------------------------------------------------------------------------

def _ref(geo, rng, allow_material=False):
    labels = [geo.balias(k) for k in range(1, geo.nbodys + 1) if geo.balias(k)]
    x = rng.random()
    if x < 0.3 and labels:
        return rng.choice(labels), None
    if x < 0.75:
        return None, rng.randint(1, geo.nbodys)
    if x < 0.82:
        return None, geo.nbodys + rng.randint(1, 3)
    if x < 0.88:
        return "ZZZZ", None
    if allow_material:
        return None, -rng.randint(1, geo.nmat + 1)
    return None, rng.randint(1, geo.nbodys)


def random_check_edit(doc, geo, rng, files: dict):
    kind = rng.choice(["mat", "lists", "fields", "vr", "nbe", "impact", "angular", "energy", "grid", "eprof",
                       "job", "resume"])
    nmat = len(doc.find("MFNAME"))
    if kind == "mat" and nmat:
        n = rng.randint(1, nmat)
        sc.set_material_param(doc, n, "MSIMPA", [rng.choice([10.0, 1e3, 1e5]), rng.choice([10.0, 1e3]),
                                                 rng.choice([1e3, 1e5]), rng.choice([-0.1, 0.05, 0.5]),
                                                 rng.choice([0.0, 0.1, 0.3]), rng.choice([-5.0, 0.0, 1e3]),
                                                 rng.choice([-1.0, 5.0, 1e3])])
        sc.set_material_param(doc, n, "MSIMPH", [rng.choice([1e3, 1e5, 1e6]), 1e5, rng.choice([50.0, 1e6]),
                                                 rng.choice([0.05, 0.4]), rng.choice([-0.2, 0.05]),
                                                 rng.choice([-1.0, 2e3])])
        sc.set_material_param(doc, n, "MSIMPN", [1.0, rng.choice([1.0, 500.0])])
    elif kind == "lists":
        for key, nv in (("DSMAX", 1), ("EABSB", 3), ("EABSBH", 3)):
            if rng.random() < 0.6:
                sc.set_body_list(doc, key, [sc.BodyValue(*_ref(geo, rng), [rng.choice([0.0, 0.1, 1e4, 1e6])
                                                                           for _ in range(nv)])
                                            for _ in range(rng.randint(0, 2))])
    elif kind == "fields":
        key = rng.choice(["EFIELD", "BFIELD"])
        lab, kb = _ref(geo, rng)
        if lab == "ZZZZ" or (kb is not None and kb > geo.nbodys):
            lab, kb = None, 1  # penhmain escribiría fuera de sus tablas
        sc.set_body_list(doc, key, [sc.BodyValue(lab, kb, [rng.choice([0.0, 1e3]), 0.0, 1e2])])
    elif kind == "vr":
        entries = []
        for _ in range(rng.randint(0, 2)):
            entries.append(sc.BodyValue(*_ref(geo, rng, True), [rng.randint(0, 6), rng.randint(1, 7),
                                                              rng.choice([0.5, 1.005, 2.0, 50.0]), 0.1, 10.0]))
        sc.set_body_list(doc, "IFORCE", entries)
        sc.set_body_list(doc, "IBRSPL", [sc.BodyValue(*_ref(geo, rng, True), [rng.choice([0, 1, 5])])
                                         for _ in range(rng.randint(0, 1))])
        sc.set_body_list(doc, "IXRSPL", [sc.BodyValue(*_ref(geo, rng, True), [rng.choice([0, 1, 5])])
                                         for _ in range(rng.randint(0, 1))])
    elif kind == "nbe":
        sc.set_value(doc, "NBE", [rng.choice([0.0, 1e3]), rng.choice([0.0, 1e8]), rng.choice([-50, 100, 2000])])
        sc.set_value(doc, "NBANGL", [rng.choice([0, 90, -45, 1000]), rng.choice([0, 1, 18, 200])])
    elif kind == "impact":
        dets = []
        for _ in range(rng.randint(1, 3)):
            d = sc.ImpactDetector(rng.choice([0.0, 1e9]), rng.choice([0.0, 1e3, 1e8]), rng.choice([0, 100, -40, 2000]),
                                  rng.choice([0, 0, 1, 2]), rng.choice([0, 1, 2, 3]))
            if rng.random() < 0.3:
                d.psf = "fase.psf"
            if rng.random() < 0.2:
                d.age = (rng.choice([0.0, 1e-6]), rng.choice([1e-7, 1e-5]), rng.choice([0, 10, -10]))
            d.bodies = [sc.BodyRef(*_ref(geo, rng)) for _ in range(rng.randint(0, 2))]
            dets.append(d)
        sc.set_impact_detectors(doc, dets)
    elif kind == "angular":
        dets = []
        for _ in range(rng.randint(1, 2)):
            d = sc.AngularDetector(rng.choice([0.0, 30.0, 200.0]), rng.choice([10.0, 90.0, 181.0]),
                                   rng.choice([-190.0, -90.0, 0.0, 370.0]), rng.choice([90.0, 200.0, 360.0, 400.0]))
            if rng.random() < 0.5:
                d.energy = (0.0, 0.0, rng.choice([0, 100, 5000]))
            dets.append(d)
        sc.set_angular_detectors(doc, dets)
    elif kind == "energy":
        dets = [sc.EnergyDetector(rng.choice([0.0, 1e9]), rng.choice([0.0, 1e8]), rng.choice([0, 100, -20]),
                                  bodies=[sc.BodyRef(*_ref(geo, rng)) for _ in range(rng.randint(0, 2))])
                for _ in range(rng.randint(1, 2))]
        sc.set_energy_detectors(doc, dets)
    elif kind == "grid":
        for key in ("GRIDX", "GRIDY", "GRIDZ"):
            lim = 401 if key == "GRIDZ" else 101
            sc.set_value(doc, key, rng.choice([None, None, [0.0, 10.0, rng.choice([0, 11, lim + 5])],
                                               [5.0, 5.0, 10], [10.0, 0.0, 10]]))
        sc.set_value(doc, "GRIDR", rng.choice([None, [15.0, 40], [0.0, 10], [5.0, 150]]))
    elif kind == "eprof":
        if rng.random() < 0.5:
            files["xs.dat"] = "1.0E3 1.0E-20\n1.0E9 2.0E-20\n"
        sc.set_external_process(doc, ("xs.dat", rng.randint(0, 7), 1))
    elif kind == "job":
        sc.set_value(doc, "TIME", rng.choice([None, [0.0], [1e3]]))
        sc.set_value(doc, "NSIMSH", rng.choice([None, [0.0], [1e3]]))
        sc.set_value(doc, "DUMPP", rng.choice([None, [10.0], [1e5]]))
        sc.set_seeds(doc, [rng.choice([(-5, 1), (-2000, 1), (0, 5), (12345, 678)])])
    elif kind == "resume":
        sc.set_text(doc, "RESUME", "resume.dmp")
        title = doc.model.title
        files["resume.dmp"] = rng.choice(["   Empty.\n", dump_text(title="Otra"), dump_text(title=title,
                                                                                         complete=False)])
    return kind


@pytest.fixture
def need_sim_probe():
    from argus.oracle.probe import find_sim_probe

    if find_sim_probe() is None:
        pytest.skip("Sonda de PMRDR no compilada (python tools/build_probe.py --penhmain ...)")


@pytest.mark.oracle
@pytest.mark.parametrize("path", CASES, ids=CASE_IDS)
def test_checks_match_penhmain(path, tmp_path, need_sim_probe):
    from argus.oracle.compare_in import compare_checks
    from argus.oracle.probe import run_sim_probe

    rng = random.Random(zlib.crc32(path.name.encode()) + 11)
    base_doc, geo = load_case(path, tmp_path)
    base_files = {f.name: f for f in tmp_path.iterdir() if f.is_file() and f.suffix.lower() != ".in"}
    for trial in range(25):
        doc = parse_in_text(base_doc.text(), str(tmp_path / path.name))
        extra: dict[str, str] = {}
        done = [random_check_edit(doc, geo, rng, extra) for _ in range(rng.randint(1, 3))]
        work = tmp_path / f"t{trial}"
        work.mkdir()
        for name, content in extra.items():
            (work / name).write_text(content, encoding="latin-1")
        for name, src in base_files.items():
            if not (work / name).exists():
                shutil.copy2(src, work / name)
        text = doc.text()
        doc = parse_in_text(text, str(work / path.name))
        res = run_sim_probe(text, {**{n: str(work / n) for n in extra}, **{n: str(p) for n, p in base_files.items()}},
                            timeout=20.0)
        diffs = compare_checks(doc, geo, work, res)
        assert not diffs, f"{trial} {done}: {diffs}"
