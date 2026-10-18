import random
import zlib
from pathlib import Path

import pytest

from argus.penhan.inputfile import fmt_real, parse_in_file, parse_in_text, write_in_text
from argus.penhan.listdirected import ListReadError, read_list
from conftest import CORPUS

IN_FILES = sorted(CORPUS.rglob("*.in"))
IN_IDS = [str(p.relative_to(CORPUS)) for p in IN_FILES]

MINIMAL = """TITLE  Prueba ARGUS
SKPAR  2          [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]
SENERG 1.0E6
SPOSIT 0 0 -10
MFNAME water.mat                      [Material file, up to 20 chars]
GEOMFN a.geo                          [Geometry file, up to 20 chars]
NSIMSH 1.0E6
"""


def test_list_directed():
    assert read_list("1 2.5 3e2  [comentario]", ["int", "real", "real"])[0] == [1, 2.5, 300.0]
    assert read_list("1,2.5,,", ["int", "real", "real"], [0, 0.0, 9.0])[0] == [1, 2.5, 9.0]
    assert read_list("3*0.5", ["real"] * 3)[0] == [0.5, 0.5, 0.5]
    assert read_list("7 /", ["int", "int"], [1, 1])[0] == [7, 1]
    with pytest.raises(ListReadError):
        read_list("1.0", ["int"])
    with pytest.raises(ListReadError):
        read_list("1 2", ["real"] * 3)


def test_fmt_real_roundtrip():
    for v in (0.0, 1.0, 100e6, 1.5e-7, -0.25, 12345.678, 3.070000076293945e1):
        assert float(fmt_real(v)) == v


@pytest.mark.parametrize("path", IN_FILES, ids=IN_IDS)
def test_corpus_in_roundtrip(path: Path):
    raw = path.read_bytes().decode("latin-1")
    doc = parse_in_file(path)
    assert doc.ok, [str(d) for d in doc.diagnostics]
    assert write_in_text(doc) == raw


def test_user_input_model():
    doc = parse_in_file(CORPUS / "usuario/100Mev15-174/maniqui.in")
    m = doc.model
    assert m.ok
    src = m.source
    assert src.kparp == 4 and len(src.spectrum) == 13 and src.spectrum[-1] == (100e6, -1.0)
    assert src.position == (0.0, 0.0, -100.0) and src.cone == (0.0, 0.0, 0.0)
    assert src.angular() == ("cone", (0.0, 0.0, 0.0))
    assert [mat.fname for mat in m.materials] == ["water.mat"]
    assert m.geomfn == "cubo-esfera.geo"
    assert m.grids["GRIDZ"] == [0.0, 9.0, 200]


def test_order_error_3035_with_hint():
    text = MINIMAL.replace("NSIMSH 1.0E6\n", "EMPARC 0.02 0.02\nNSIMSH 1.0E6\n")
    doc = parse_in_text(text)
    err = doc.diagnostics.errors[0]
    assert err.code == 3035 and err.line == 6 and "PFIELD" in err.message


def test_integer_with_decimals_is_runtime_error():
    doc = parse_in_text(MINIMAL.replace("SKPAR  2", "SKPAR  2.0"))
    err = doc.diagnostics.errors[0]
    assert err.code is None and err.line == 1


def test_label_without_comment_hangs_penhmain():
    text = MINIMAL.replace("SPOSIT 0 0 -10\n", "SPOSIT 0 0 -10\nSBOX   1 1 1\nSBODY  '   1'\n")
    doc = parse_in_text(text)
    err = doc.diagnostics.errors[0]
    assert err.fortran_reason == "HANG" and err.line == 5


def test_edit_source_and_write():
    doc = parse_in_text(MINIMAL)
    doc.set("SPOSIT", [1.0, 2.0, -50.0])
    doc.set("SCONE", [0.0, 0.0, 5.0])
    doc.set("SBOX", [2.0, 2.0, 0.5])
    doc.set_repeated("SBODY", [{"label": "   1"}])
    out = write_in_text(doc)
    lines = out.splitlines()
    assert lines[0] == "TITLE  Prueba ARGUS"
    kws = [ln[:6] for ln in lines]
    assert kws.index("SPOSIT") < kws.index("SBOX  ") < kws.index("SBODY ") < kws.index("SCONE ") < kws.index("MFNAME")
    assert any(ln.startswith("SBODY  '   1'") and "[" in ln for ln in lines)  # comentario tras la etiqueta
    doc2 = parse_in_text(out)
    assert doc2.ok
    src = doc2.model.source
    assert src.position == (1.0, 2.0, -50.0) and src.cone == (0.0, 0.0, 5.0) and src.box == (2.0, 2.0, 0.5)
    assert src.bodies[0].label == "   1"
    # Las líneas no tocadas se conservan tal cual.
    assert "SKPAR  2          [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]" in lines


def test_remove_line():
    doc = parse_in_text(MINIMAL + "")
    doc.set("SCONE", [10.0, 0.0, 0.0])
    assert doc.remove("SCONE") == 1
    assert doc.model.source.cone is None


# ---------------------------------------------------------------------------
# Verificación con el PMRDR0 real de penhmain
# ---------------------------------------------------------------------------

@pytest.fixture
def need_reader_probe():
    from argus.oracle.probe import find_reader_probe

    if find_reader_probe() is None:
        pytest.skip("Sonda del lector de .in no compilada (python tools/build_probe.py --penhmain ...)")


@pytest.mark.oracle
@pytest.mark.parametrize("path", IN_FILES, ids=IN_IDS)
def test_reader_matches_penhmain(path, need_reader_probe):
    from argus.oracle.compare_in import check_text, mutations
    from argus.oracle.probe import run_reader_probe

    text = path.read_bytes().decode("latin-1")
    rng = random.Random(zlib.crc32(path.name.encode()))
    for kind, variant in [("original", text)] + mutations(text, rng, 12):
        diffs = check_text(variant, lambda t: run_reader_probe(text=t, timeout=3.0))
        assert not diffs, f"{kind}: {diffs}"
