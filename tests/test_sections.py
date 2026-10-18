"""Secciones del .in (materiales, cuerpos, campos, detectores, rejilla, trabajo, espectro).

Las pruebas de oráculo aplican ediciones aleatorias con ``penhan.sections`` a
los .in del corpus y pasan el texto resultante por el PMRDR0 real de
penhmain: tiene que leerlo sin errores y leer exactamente lo que ARGUS cree
que ha escrito.
"""

import random
import zlib

import pytest

from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_file, parse_in_text
from conftest import CORPUS

IN_FILES = sorted(CORPUS.rglob("*.in"))
IN_IDS = [str(p.relative_to(CORPUS)) for p in IN_FILES]
USER_IN = CORPUS / "usuario/100Mev15-174/maniqui.in"


def reparse(doc):
    """El documento tal como se guardaría y se volvería a abrir."""
    return parse_in_text(doc.text(), doc.path)


# ---------------------------------------------------------------------------
# Lectura y escritura
# ---------------------------------------------------------------------------

def test_material_params_roundtrip():
    doc = parse_in_file(USER_IN)
    n = len(doc.find("MFNAME"))
    assert n >= 1
    p = sc.material_params(doc, 1)
    assert p["MSIMPA"] is not None
    sc.set_material_param(doc, 1, "MSIMPA", [1e5, 1e4, 1e5, 0.1, 0.1, 1e3, 1e3])
    assert doc.ok
    d2 = reparse(doc)
    assert sc.material_params(d2, 1)["MSIMPA"] == [1e5, 1e4, 1e5, 0.1, 0.1, 1e3, 1e3]
    # Quitar MSIMPN de todos y volver a ponerlo: queda tras MSIMPH, dentro del grupo de cada material.
    sc.set_all_material_params(doc, "MSIMPN", None)
    assert all(sc.material_params(doc, k)["MSIMPN"] is None for k in range(1, n + 1))
    sc.set_all_material_params(doc, "MSIMPN", [1.0, 400.0])
    assert doc.ok
    d2 = reparse(doc)
    for k in range(1, n + 1):
        assert sc.material_params(d2, k)["MSIMPN"] == [1.0, 400.0]


def test_unchanged_lines_keep_their_text():
    doc = parse_in_file(USER_IN)
    before = doc.text()
    sc.set_dose_grid(doc, sc.dose_grid(doc))
    sc.set_impact_detectors(doc, sc.impact_detectors(doc))
    sc.set_seeds(doc, sc.seeds(doc))
    assert doc.text() == before


def test_body_lists_and_limits():
    doc = parse_in_file(USER_IN)
    sc.set_body_list(doc, "DSMAX", [sc.BodyValue("   1", None, [0.5]), sc.BodyValue(None, 2, [1.0])])
    assert doc.ok
    got = sc.body_list(reparse(doc), "DSMAX")
    assert [(e.label, e.kb, e.values) for e in got] == [("   1", None, [0.5]), (None, 2, [1.0])]
    with pytest.raises(ValueError):  # PMRDR0 solo reconoce etiquetas de 4 caracteres
        sc.set_body_list(doc, "DSMAX", [sc.BodyValue("1", None, [0.5])])
    with pytest.raises(ValueError):
        sc.set_body_list(doc, "DSMAX", [sc.BodyValue(None, 1, [1.0])] * 51)
    sc.set_body_list(doc, "DSMAX", [])
    assert not doc.find("DSMAX")


def test_detectors_roundtrip():
    doc = parse_in_file(USER_IN)
    imp = sc.ImpactDetector(1e6, 1e8, -50, 1, 2, spc="spc1.dat", psf="salida.psf",
                            age=(0.0, 1e-8, 20), bodies=[sc.BodyRef("   1", None), sc.BodyRef(None, 2)], kpar=[2, 4])
    ang = sc.AngularDetector(0.0, 30.0, -180.0, 180.0, energy=(0.0, 0.0, 100), kpar=2, spc="ang.dat")
    ene = sc.EnergyDetector(0.0, 0.0, 200, bodies=[sc.BodyRef(None, 1)])
    sc.set_impact_detectors(doc, [imp])
    sc.set_angular_detectors(doc, [ang])
    sc.set_energy_detectors(doc, [ene])
    assert doc.ok, doc.diagnostics.errors
    d2 = reparse(doc)
    assert sc.impact_detectors(d2) == [imp]
    assert sc.angular_detectors(d2) == [ang]
    assert sc.energy_detectors(d2) == [ene]
    sc.set_impact_detectors(doc, [])
    assert not any(doc.find(k) for k in sc.IMPACT_KEYWORDS)
    with pytest.raises(ValueError):
        sc.set_energy_detectors(doc, [ene] * 6)


def test_dose_grid_kinds():
    doc = parse_in_file(USER_IN)
    g = sc.dose_grid(doc)
    assert g.kind == "cylinder"
    sc.set_dose_grid(doc, sc.DoseGrid(x=(-1.0, 1.0, 11), y=(-1.0, 1.0, 11), z=(0.0, 10.0, 100)))
    assert sc.dose_grid(reparse(doc)).kind == "box"
    sc.set_dose_grid(doc, sc.DoseGrid(r=(5.0, 50)))
    d2 = reparse(doc)
    assert sc.dose_grid(d2).kind == "sphere" and not d2.find("GRIDX") and not d2.find("GRIDZ")
    with pytest.raises(ValueError):
        sc.set_dose_grid(doc, sc.DoseGrid(z=(0.0, 1.0, 10)))  # GRIDZ solo: penhmain no tiene radio
    with pytest.raises(ValueError):
        sc.set_dose_grid(doc, sc.DoseGrid(z=(0.0, 1.0, 500), r=(1.0, 10)))
    sc.set_dose_grid(doc, sc.DoseGrid())
    assert sc.dose_grid(doc).kind == "none"


def test_spectrum_replaces_energy():
    doc = parse_in_text("TITLE  t\nSKPAR  4\nSENERG 1e8\nSPOSIT 0 0 0\nMFNAME water.mat\nGEOMFN a.geo\n")
    sc.set_spectrum(doc, [(9e7, 0.3), (1e8, 0.7), (1.1e8, -1)])
    assert doc.ok and not doc.find("SENERG")
    assert sc.spectrum(reparse(doc)) == [(9e7, 0.3), (1e8, 0.7), (1.1e8, -1.0)]
    sc.set_energy_lines(doc, [(1e8, 1.0), (2e8, 1.0)])
    assert not doc.find("SPECTR") and len(doc.find("SLINES")) == 2


def test_analyze_spectrum_like_penhmain():
    # Espectro normal: intervalos [E_i, E_i+1] con P_i; la P de Emax no cuenta.
    info = sc.analyze_spectrum([(2e7, 1.0), (1e7, 1.0), (3e7, -1.0)])  # desordenado: PMRDR lo ordena
    assert [(a, b) for a, b, _ in info.bins] == [(1e7, 2e7), (2e7, 3e7)]
    assert info.errors == [] and info.problems == []
    assert abs(info.mean - 2e7) < 1e-6
    # El caso de EspectroPrueba2: dos líneas en 49 MeV -> un intervalo de anchura cero con P = 0.9.
    rows = [(10e6, 0.4), (20e6, 0.5), (30e6, 0.9), (49e6, 0.9), (49e6, -1)]
    info = sc.analyze_spectrum(rows)
    zero = [b for b in info.bins if b[0] == b[1]]
    assert len(zero) == 1 and abs(zero[0][2] - 0.9 / 2.7) < 1e-12
    assert any("anchura cero" in p for p in info.problems)
    # Una sola línea: penhmain se detiene (IRETRN 3040).
    assert sc.analyze_spectrum([(1e6, -1)]).errors


def test_sort2_keeps_input_order_of_equal_energies():
    # SORT2 (penelope.f) es una selección que devuelve a su orden original las parejas de abscisas iguales.
    rows = [(49e6, -1.0), (10e6, 1.0), (49e6, 0.5)]
    e, p = sc.sort2([r[0] for r in rows], [max(r[1], 0.0) for r in rows])
    assert e == [10e6, 49e6, 49e6] and p[1] <= 1e-75 and p[2] == 0.5


def test_psf_seeds_external():
    doc = parse_in_file(USER_IN)
    sc.set_seeds(doc, [(-3, 1)])
    sc.set_external_process(doc, ("xs.dat", 4, 1))
    sc.set_value(doc, "LTRACK", [3])
    sc.set_text(doc, "DUMPTO", "final.dmp")
    assert doc.ok, doc.diagnostics.errors
    d2 = reparse(doc)
    assert sc.seeds(d2) == [(-3, 1)] and sc.external_process(d2) == ("xs.dat", 4, 1)
    assert sc.value(d2, "LTRACK") == [3] and sc.text(d2, "DUMPTO") == "final.dmp"
    sc.set_external_process(doc, None)
    sc.set_text(doc, "DUMPTO", "")
    assert not doc.find("EPROF") and not doc.find("EPROPM") and not doc.find("DUMPTO")


# ---------------------------------------------------------------------------
# Oráculo: ediciones aleatorias leídas por el PMRDR0 real
# ---------------------------------------------------------------------------

def _ref(rng):
    if rng.random() < 0.4:
        return rng.choice(["   1", "   2", "  AB", "x1  "]), None
    return None, rng.randint(1, 20)


def _energy(rng):
    return rng.choice([0.0, 50.0, 1e3, 1.5e6, 2.5e7, 1e8, 2.0e8])


def random_edit(doc, rng):
    """Una edición aleatoria de una sección cualquiera; devuelve su nombre."""
    kind = rng.choice(["mat", "mat_all", "list", "parinp", "field", "vr", "nbe", "impact", "angular", "energy",
                       "grid", "eprof", "job", "seeds", "spectrum", "lines", "psf"])
    nmat = len(doc.find("MFNAME"))
    if kind == "mat" and nmat:
        key = rng.choice(sc.MATERIAL_PARAMS)
        n = rng.randint(1, nmat)
        if rng.random() < 0.25:
            vals = None
        elif key == "MSIMPA":
            vals = [_energy(rng), _energy(rng), _energy(rng), rng.choice([0.0, 0.05, 0.1, 0.3]),
                    rng.choice([0.0, 0.05, 0.2]), rng.choice([-1.0, 0.0, 1e3]), rng.choice([-5.0, 5.0, 1e3])]
        elif key == "MSIMPH":
            vals = [rng.choice([1e4, 1e5, 1e6]) for _ in range(3)] + [0.05, 0.05, 2e3]
        else:
            vals = [rng.choice([0.5, 1.0]), rng.choice([1.0, 500.0])]
        sc.set_material_param(doc, n, key, vals)
    elif kind == "mat_all" and nmat:
        sc.set_all_material_params(doc, "MSIMPA", [1e5, 1e4, 1e5, 0.1, 0.1, 2e3, 2e3])
    elif kind == "list":
        key = rng.choice(["DSMAX", "EABSB", "EABSBH"])
        nval = 1 if key == "DSMAX" else 3
        entries = []
        for _ in range(rng.randint(0, 3)):
            lab, kb = _ref(rng)
            entries.append(sc.BodyValue(lab, kb, [rng.choice([0.0, 0.1, 2.5, 1e3]) for _ in range(nval)]))
        sc.set_body_list(doc, key, entries)
    elif kind == "parinp":
        if doc.find("GEOMFN"):
            sc.set_parinp(doc, {rng.randint(1, 5): rng.choice([0.5, 1.0, -2.0]) for _ in range(rng.randint(0, 3))})
    elif kind == "field":
        if rng.random() < 0.5:
            sc.set_value(doc, "PFIELD", [rng.choice([0.01, 0.05]), rng.choice([0.02, 0.05])])
        for key in ("EFIELD", "BFIELD"):
            entries = [sc.BodyValue(*_ref(rng), [rng.choice([0.0, 1e3, -2e2]) for _ in range(3)])
                       for _ in range(rng.randint(0, 2))]
            sc.set_body_list(doc, key, entries)
    elif kind == "vr":
        entries = []
        for _ in range(rng.randint(0, 2)):
            lab, kb = _ref(rng)
            if lab is None and rng.random() < 0.3:
                kb = -rng.randint(1, 3)  # todos los cuerpos de un material
            entries.append(sc.BodyValue(lab, kb, [rng.randint(1, 5), rng.randint(2, 5), rng.choice([2.0, -10.0]),
                                                  0.1, 2.0]))
        sc.set_body_list(doc, "IFORCE", entries)
        for key in ("IBRSPL", "IXRSPL"):
            sc.set_body_list(doc, key, [sc.BodyValue(*_ref(rng), [rng.randint(1, 20)])
                                        for _ in range(rng.randint(0, 2))])
    elif kind == "nbe":
        sc.set_value(doc, "NBE", [0.0, rng.choice([0.0, 1e8]), rng.choice([-50, 100])])
        sc.set_value(doc, "NBANGL", [rng.choice([45, -30]), rng.choice([1, 18])])
    elif kind == "impact":
        dets = []
        for _ in range(rng.randint(0, 3)):
            d = sc.ImpactDetector(rng.choice([0.0, 1e3]), rng.choice([0.0, 1e8]), rng.choice([100, -40]),
                                  rng.randint(0, 1), rng.randint(0, 2))
            if rng.random() < 0.4:
                d.spc = f"spc{rng.randint(1, 9)}.dat"
            if rng.random() < 0.3:
                d.psf = "fase.psf"
            if rng.random() < 0.2:
                d.flnc = "flu.dat"
            if rng.random() < 0.3:
                d.age = (0.0, 1e-7, rng.choice([10, -10]))
                d.agef = "edad.dat" if rng.random() < 0.5 else ""
            d.bodies = [sc.BodyRef(*_ref(rng)) for _ in range(rng.randint(1, 3))]
            d.kpar = sorted(rng.sample(range(1, 7), rng.randint(0, 2)))
            dets.append(d)
        sc.set_impact_detectors(doc, dets)
    elif kind == "angular":
        dets = []
        for _ in range(rng.randint(0, 2)):
            d = sc.AngularDetector(0.0, rng.choice([10.0, 90.0]), rng.choice([0.0, -90.0]), rng.choice([90.0, 360.0]))
            if rng.random() < 0.5:
                d.energy = (0.0, rng.choice([0.0, 1e7]), rng.choice([100, -20]))
            if rng.random() < 0.5:
                d.kpar = rng.randint(1, 6)
            if rng.random() < 0.3:
                d.spc = "ang.dat"
            dets.append(d)
        sc.set_angular_detectors(doc, dets)
    elif kind == "energy":
        dets = []
        for _ in range(rng.randint(0, 2)):
            d = sc.EnergyDetector(0.0, rng.choice([0.0, 1e8]), rng.choice([100, -20]))
            if rng.random() < 0.4:
                d.spc = "dep.dat"
            d.bodies = [sc.BodyRef(*_ref(rng)) for _ in range(rng.randint(1, 3))]
            dets.append(d)
        sc.set_energy_detectors(doc, dets)
    elif kind == "grid":
        g = rng.choice([sc.DoseGrid(), sc.DoseGrid(x=(-1.0, 1.0, 11), y=(-2.0, 2.0, 21), z=(0.0, 5.0, 50)),
                        sc.DoseGrid(z=(0.0, 30.0, 300), r=(15.0, 40)), sc.DoseGrid(r=(2.0, 20))])
        sc.set_dose_grid(doc, g)
    elif kind == "eprof":
        sc.set_external_process(doc, rng.choice([None, ("xs.tab", rng.randint(1, 6), rng.randint(1, 3))]))
    elif kind == "job":
        sc.set_text(doc, "RESUME", rng.choice(["", "dump1.dmp"]))
        sc.set_text(doc, "DUMPTO", rng.choice(["", "dump2.dmp"]))
        sc.set_value(doc, "DUMPP", rng.choice([None, [60.0], [1e4]]))
        sc.set_value(doc, "LTRACK", rng.choice([None, [0], [5]]))
        sc.set_value(doc, "NSIMSH", rng.choice([None, [1e6], [2.5e8]]))
        sc.set_value(doc, "TIME", rng.choice([None, [3600.0], [1e9]]))
    elif kind == "seeds":
        sc.set_seeds(doc, [(rng.randint(-5, 10 ** 6), rng.randint(1, 10 ** 6)) for _ in range(rng.randint(0, 2))])
    elif kind == "spectrum" and not doc.model.source.psf and doc.model.source.kparp != 0:
        n = rng.randint(2, 6)
        es = sorted(rng.sample(range(10, 200), n))
        sc.set_spectrum(doc, [(e * 1e6, rng.random()) for e in es[:-1]] + [(es[-1] * 1e6, -1.0)])
    elif kind == "lines" and not doc.model.source.psf and doc.model.source.kparp != 0:
        sc.set_energy_lines(doc, [(rng.randint(1, 100) * 1e6, rng.random()) for _ in range(rng.randint(2, 4))])
    elif kind == "psf" and doc.model.source.psf:
        sc.set_psf_files(doc, [f"psf{i}.dat" for i in range(rng.randint(1, 3))])
    return kind


@pytest.fixture
def need_reader_probe():
    from argus.oracle.probe import find_reader_probe

    if find_reader_probe() is None:
        pytest.skip("Sonda del lector de .in no compilada (python tools/build_probe.py --penhmain ...)")


@pytest.mark.oracle
@pytest.mark.parametrize("path", IN_FILES, ids=IN_IDS)
def test_random_section_edits_read_by_penhmain(path, need_reader_probe):
    from argus.oracle.compare_in import compare_reader, compare_sections
    from argus.oracle.probe import run_reader_probe

    base = parse_in_file(path)
    if not base.ok:
        pytest.skip("El .in original no lo lee penhmain")
    rng = random.Random(zlib.crc32(path.name.encode()) + 7)
    for trial in range(10):
        doc = parse_in_file(path)
        done = [random_edit(doc, rng) for _ in range(rng.randint(1, 4))]
        text = doc.text()
        doc = parse_in_text(text, str(path))
        res = run_reader_probe(text=text, timeout=3.0)
        diffs = compare_reader(doc, res)
        assert res.iretrn == 0 and doc.ok, f"{trial} {done}: IRETRN {res.iretrn} {res.reason} {diffs}"
        diffs += compare_sections(doc, res)
        assert not diffs, f"{trial} {done}: {diffs}"
