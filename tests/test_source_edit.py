"""Edición de la fuente del .in (F5): operaciones, plantilla y verificación con penhmain."""

import random
import zlib
from pathlib import Path

import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry
from argus.penhan import source_edit as se
from argus.penhan.inputfile import fmt_real, parse_in_file, parse_in_text, write_in_text
from argus.penhan.template import new_input_document, new_input_text
from argus.penhan.validate import validate_case
from argus.session import open_case
from conftest import CORPUS

IN_FILES = sorted(CORPUS.rglob("*.in"))
IN_IDS = [str(p.relative_to(CORPUS)) for p in IN_FILES]
MANIQUI = CORPUS / "usuario/100Mev15-174/maniqui.in"

MINIMAL = """TITLE  Prueba ARGUS
SKPAR  2          [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]
SENERG 1.0E6
SPOSIT 0 0 -10
MFNAME water.mat                      [Material file, up to 20 chars]
GEOMFN a.geo                          [Geometry file, up to 20 chars]
NSIMSH 1.0E6
"""


def reparse(doc):
    """El texto escrito, leído de nuevo (lo que vería penhmain)."""
    return parse_in_text(write_in_text(doc))


def test_fmt_real_compact():
    assert fmt_real(1e8) == "1e8"
    assert fmt_real(1e4) == "1e4"
    assert fmt_real(2000.0) == "2000.0"
    assert fmt_real(-100.0) == "-100.0"
    assert fmt_real(12345.678) == "12345.678"
    assert fmt_real(2.5e-5) == "2.5e-5"
    for v in (1e8, 1e4, 2.5e-5, 91227061.66, 0.1, 1234567.0, -3e-9):
        assert float(fmt_real(v)) == v


def test_emission_kinds():
    doc = parse_in_text(MINIMAL)
    assert se.emission_kind(doc.model.source) == ("pencil", {"theta": 0.0, "phi": 0.0, "alpha": 0.0,
                                                             "default": True})
    se.set_emission(doc, "cone", 30, 45, 10)
    assert reparse(doc).model.source.cone == (30.0, 45.0, 10.0)
    se.set_emission(doc, "isotropic", 30, 45)
    assert reparse(doc).model.source.cone == (30.0, 45.0, 180.0)
    se.set_emission(doc, "rect", rect=[0, 30, 0, 90])
    src = reparse(doc).model.source
    assert src.cone is None and src.rect == (0.0, 30.0, 0.0, 90.0)
    assert se.emission_kind(src)[0] == "rect"
    se.set_emission(doc, "pencil", 90, 180)
    src = reparse(doc).model.source
    assert src.rect is None and src.cone == (90.0, 180.0, 0.0)
    assert doc.ok


def test_direction_keeps_kind_and_cleans_angles():
    doc = parse_in_text(MINIMAL)
    se.set_emission(doc, "cone", 0, 0, 12)
    se.set_direction(doc, 89.99999999999999, 360.0 - 1e-12)
    assert doc.model.source.cone == (90.0, 0.0, 12.0)
    se.set_direction(doc, 180.0, 123.0)  # en el polo φ no importa
    assert doc.model.source.cone == (180.0, 0.0, 12.0)
    se.set_emission(doc, "rect", rect=[10, 30, 350, 360])
    se.set_direction(doc, 90, 0)  # la ventana no puede cruzar φ = 0
    thl, thu, phl, phu = doc.model.source.rect
    assert (thl, thu) == (80.0, 100.0) and 0.0 <= phl < phu <= 360.0 and phu - phl == 10.0
    assert not validate_case(doc).has_errors


def test_aim_at():
    doc = parse_in_text(MINIMAL)
    assert se.aim_at(doc, [0, 0, 50]) == (0.0, 0.0)
    assert se.aim_at(doc, [10, 0, -10]) == (90.0, 0.0)
    assert se.aim_at(doc, [0, -10, -10]) == (90.0, 270.0)
    th, ph = se.aim_at(doc, [10, 10, 0])
    assert th == pytest.approx(54.735610317, abs=1e-6) and ph == 45.0
    assert se.aim_at(doc, [0, 0, -10]) is None  # el punto es la propia fuente


def test_particle_energy_and_nuclide():
    doc = parse_in_text(MINIMAL)
    se.set_particle(doc, 0)
    m = reparse(doc).model
    assert m.source.kparp == 0 and m.source.nuclide == se.DEFAULT_NUCLIDE
    se.set_nuclide(doc, "Co-60.nuc")
    assert reparse(doc).model.source.nuclide == "Co-60.nuc"
    se.set_particle(doc, 4)  # SRNUCL fuerza KPARP = 0: hay que quitarlo
    m = reparse(doc).model
    assert m.source.kparp == 4 and m.source.nuclide is None and doc.first("SRNUCL") is None
    se.set_energy(doc, 150e6)
    assert reparse(doc).model.source.energy == 150e6


def test_energy_replaces_spectrum():
    doc = parse_in_file(MANIQUI)
    assert len(doc.find("SPECTR")) == 13
    se.set_energy(doc, 100e6)
    assert doc.find("SPECTR") == [] and doc.ok
    src = reparse(doc).model.source
    assert src.energy == 100e6 and src.spectrum == []


def test_extended_source_and_bodies():
    geo = compile_geometry(load_geometry(CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"))
    doc = parse_in_file(MANIQUI)
    se.set_active_bodies(doc, geo, [1])  # sin caja: penhmain daría 3049
    errs = validate_case(doc, geo).errors
    assert errs and errs[0].code == 3049
    se.set_box(doc, [20, 20, 10])
    se.set_active_bodies(doc, geo, [1])
    src = reparse(doc).model.source
    assert src.box == (20.0, 20.0, 10.0)
    assert [b.label for b in src.bodies] == [geo.balias(1)]  # por etiqueta
    assert se.active_bodies(doc, geo) == {1}
    # La caja está en z = -100 ± 5 y el cuerpo 1 (el agua) empieza en z = 0: penhmain no terminaría nunca.
    errs = validate_case(doc, geo).errors
    assert errs and "sin fin" in errs[0].message
    inside = reparse(doc)
    se.set_position(inside, [0, 0, 10])
    assert not validate_case(inside, geo).has_errors
    # Una etiqueta que no existe se conserva si se pide, y es un error 3050.
    se.set_active_bodies(doc, geo, [1], extra_refs=[{"label": "ZZZZ"}])
    errs = validate_case(doc, geo).errors
    assert errs and errs[0].code == 3050
    # Volver a puntual quita también SBODY.
    se.set_box(doc, None)
    assert doc.first("SBOX") is None and doc.first("SBODY") is None and doc.ok
    # Añadir y quitar la caja deja el fichero byte a byte como estaba.
    assert write_in_text(doc) == MANIQUI.read_bytes().decode("latin-1")


def test_radioactive_source_checks_sbody():
    """PMRDR también comprueba SBODY en las fuentes radiactivas (salta solo la parte angular)."""
    doc = parse_in_file(CORPUS / "penhan/nuclide/nuclide.in")
    geo = compile_geometry(load_geometry(CORPUS / "penhan/nuclide/disc-origin.geo"))
    se.set_active_bodies(doc, geo, [1])
    assert any(d.code == 3049 for d in validate_case(doc, geo).errors)


def test_diagnostic_lines_follow_edits():
    doc = parse_in_text(MINIMAL.replace("NSIMSH 1.0E6\n", "FOOBAR 1\nNSIMSH 1.0E6\n"))
    assert doc.diagnostics.errors[0].line == 6
    se.set_box(doc, [1, 1, 1])  # inserta una línea antes
    err = doc.diagnostics.errors[0]
    assert err.code == 3035 and err.line == 7 and doc.display_lines()[7].startswith("FOOBAR")


@pytest.mark.parametrize("path", IN_FILES, ids=IN_IDS)
def test_untouched_lines_survive_edits(path: Path):
    raw = path.read_bytes().decode("latin-1")
    doc = parse_in_file(path)
    if doc.model.source.from_psf:
        pytest.skip("fuente de espacio de fases")
    se.set_position(doc, [1.5, -2.0, 3.25])
    out = write_in_text(doc)
    old = [ln for ln in raw.splitlines() if not ln.startswith("SPOSIT")]
    new = [ln for ln in out.splitlines() if not ln.startswith("SPOSIT")]
    assert old == new
    assert parse_in_text(out).model.source.position == (1.5, -2.0, 3.25)


def test_template_is_valid_for_every_geometry():
    for geo_path in sorted(CORPUS.rglob("*.geo")):
        geo = compile_geometry(load_geometry(geo_path))
        if not geo.ok:
            continue
        mats = [f"m{i}.mat" for i in range(1, max(1, geo.nmat) + 1)]
        if geo.nmat > 10:
            continue  # penhmain admite como mucho 10 materiales (MAXMAT)
        doc = new_input_document(geo_path.name[:20], mats)
        assert doc.ok, [str(d) for d in doc.diagnostics]
        d = validate_case(doc, geo)
        assert not d.has_errors, (geo_path, [str(x) for x in d])


def test_template_text_layout():
    text = new_input_text("cubo-esfera.geo", ["water.mat"], position=(0, 0, -100))
    lines = text.split("\r\n")
    assert lines[0].startswith("TITLE  ")
    # Comentarios alineados como en penhmain-layout.in (el corchete final en la columna 69).
    for ln in lines:
        if ln[:6].strip() and "[" in ln and ln.rstrip().endswith("]") and len(ln) <= 69:
            assert len(ln.rstrip()) == 69, ln


def test_open_case_uses_input_parinp():
    case = open_case(CORPUS / "penhan/disc/disc.in")
    assert case.session is not None and case.session.ok
    assert case.session.parinp == [40.0, 40.0]
    assert case.input_doc.model.geomfn == "disc.geo"
    missing = open_case(CORPUS / "usuario/auuux/maniqui.in")
    assert missing.session is None and "cubo-esfera.geo" in missing.problem


# ---------------------------------------------------------------------------
# Verificación con el PMRDR0 real de penhmain
# ---------------------------------------------------------------------------

@pytest.fixture
def need_reader_probe():
    from argus.oracle.probe import find_reader_probe

    if find_reader_probe() is None:
        pytest.skip("Sonda del lector de .in no compilada (python tools/build_probe.py --penhmain ...)")


def random_edits(doc, rng: random.Random, n: int = 6) -> list[str]:
    done = []
    for _ in range(n):
        op = rng.choice(["pos", "box", "point", "cone", "pencil", "iso", "rect", "dir", "energy", "particle",
                         "bodies"])
        if op == "pos":
            se.set_position(doc, [rng.choice([0.0, 1.5, -2.25, 1e-3, 123.456]) for _ in range(3)])
        elif op == "box":
            se.set_box(doc, [rng.choice([0.5, 1.0, 2e-4, 30.0]) for _ in range(3)])
        elif op == "point":
            se.set_box(doc, None)
        elif op == "cone":
            se.set_emission(doc, "cone", rng.uniform(0, 180), rng.uniform(0, 360), rng.uniform(0.5, 90))
        elif op == "pencil":
            se.set_emission(doc, "pencil", rng.uniform(0, 180), rng.uniform(0, 360))
        elif op == "iso":
            se.set_emission(doc, "isotropic")
        elif op == "rect":
            a, b = sorted(rng.uniform(0, 180) for _ in range(2))
            c, d = sorted(rng.uniform(0, 360) for _ in range(2))
            se.set_emission(doc, "rect", rect=[a, b, c, d])
        elif op == "dir":
            se.set_direction(doc, rng.uniform(0, 180), rng.uniform(0, 360))
        elif op == "energy":
            se.set_energy(doc, rng.choice([15e3, 1e6, 100e6, 91.22706166e6]))
        elif op == "particle":
            se.set_particle(doc, rng.choice([0, 1, 2, 3, 4, 5]))
        elif op == "bodies":
            if doc.model.source.extended:
                se.set_active_bodies(doc, None, rng.sample([1, 2, 3], rng.randint(0, 2)))
        done.append(op)
    return done


@pytest.mark.oracle
@pytest.mark.parametrize("path", IN_FILES, ids=IN_IDS)
def test_edited_files_read_by_penhmain(path: Path, need_reader_probe):
    """Lo que escribe ARGUS lo lee penhmain igual que ARGUS (valores, errores y orden)."""
    from argus.oracle.compare_in import check_text
    from argus.oracle.probe import run_reader_probe

    rng = random.Random(zlib.crc32(path.name.encode()) + 7)
    for _ in range(6):
        doc = parse_in_file(path)
        if doc.model.source.from_psf:
            pytest.skip("fuente de espacio de fases")
        ops = random_edits(doc, rng)
        text = write_in_text(doc)
        diffs = check_text(text, lambda t: run_reader_probe(text=t, timeout=3.0))
        assert not diffs, (ops, diffs)


@pytest.mark.oracle
def test_template_read_by_penhmain(need_reader_probe):
    from argus.oracle.compare_in import check_text
    from argus.oracle.probe import run_reader_probe

    text = new_input_text("cubo-esfera.geo", ["water.mat", "air.mat"], position=(0, 0, -100))
    assert not check_text(text, lambda t: run_reader_probe(text=t, timeout=3.0))
