"""Abrir una geometría: qué .in la usan (GEOMFN) y PARINP sin .in."""

import os
import shutil

from argus.core.diagnostics import Severity
from argus.pengeom.parinp import parinp_uses, provisional_parinp
from argus.pengeom.parser import load_geometry
from argus.penhan.inputfile import parse_in_text
from argus.penhan.quickscan import inputs_for_geometry
from argus.penhan.template import new_input_text
from argus.session import open_case, open_geometry
from conftest import CORPUS

DISC = CORPUS / "penhan/disc"


def _disc_case(tmp_path):
    shutil.copy2(DISC / "disc.geo", tmp_path / "disc.geo")
    shutil.copy2(DISC / "disc.in", tmp_path / "disc.in")
    return tmp_path


def _variant(folder, name: str, geomfn: str = "disc.geo", thickness: str = "40.0", mtime: float | None = None):
    """Copia de disc.in con otro GEOMFN y otro PARINP(1)."""
    lines = (DISC / "disc.in").read_text("latin-1").splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith("GEOMFN"):
            lines[i] = f"GEOMFN {geomfn:<20}          [Geometry file, up to 20 chars]\n"
        elif ln.startswith("PARINP 1 "):
            lines[i] = f"PARINP 1  {thickness:<10}                   thickness  [Replacement parameter]\n"
    path = folder / name
    path.write_text("".join(lines), "latin-1")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_inputs_for_geometry_compares_files(tmp_path):
    folder = _disc_case(tmp_path)
    _variant(folder, "b.IN")  # extensión en mayúsculas
    _variant(folder, "c.in", geomfn="./disc.geo")  # mismo fichero con otro nombre
    _variant(folder, "d.in", geomfn="otra.geo")
    _variant(folder, "e.in", geomfn="DISC.GEO")  # solo en sistemas que no distinguen mayúsculas
    found = [s.path.name for s in inputs_for_geometry(folder / "disc.geo")]
    insensitive = (folder / "DISC.GEO").exists()
    assert found == ["b.IN", "c.in", "disc.in"] + (["e.in"] if insensitive else [])
    assert all(s.mtime is not None for s in inputs_for_geometry(folder / "disc.geo"))


def test_parinp_uses_and_provisional_values():
    doc = load_geometry(DISC / "disc.geo")
    uses = parinp_uses(doc)
    assert list(uses) == [1, 2]
    assert uses[1].names == ["Z-SHIFT"] and uses[2].names == ["X-SCALE", "Y-SCALE"]
    assert provisional_parinp(doc) == [0.005, 1.0]
    assert provisional_parinp(load_geometry(CORPUS / "penhan/stack/3discs.geo")) == [1.0, 2.0, 3.0, 1.0]
    assert provisional_parinp(load_geometry(CORPUS / "usuario/100Mev15-174/cubo-esfera.geo")) == []


def test_geometry_without_input_uses_written_values(tmp_path):
    folder = _disc_case(tmp_path)
    s = open_geometry(folder / "disc.geo", search_input=False)
    assert s.ok and s.input_file is None
    assert s.parinp == [0.005, 1.0] and s.parinp_provisional
    warn = [d for d in s.diagnostics if d.severity == Severity.WARNING and "Sin .in" in d.message]
    assert len(warn) == 1 and warn[0].line == 9 and "PARINP(2) = 1" in warn[0].message
    # Con el .in, los valores son los suyos y no hay aviso.
    s = open_geometry(folder / "disc.geo")
    assert s.parinp == [40.0, 40.0] and not s.parinp_provisional
    assert not any("Sin .in" in d.message for d in s.diagnostics)
    # Un PARINP dado explícitamente (aunque falte) no se completa: es lo que haría penhmain.
    s = open_geometry(folder / "disc.geo", parinp=[40.0])
    assert not s.ok and any(d.code == 2008 or "PARINP(2)" in d.message for d in s.diagnostics)


def test_provisional_values_conflicts_and_unusable(tmp_path):
    folder = _disc_case(tmp_path)
    text = (folder / "disc.geo").read_text("latin-1")
    y = "Y-SCALE=(+1.000000000000000E+00,   2)"
    assert y in text
    (folder / "conflict.geo").write_text(text.replace(y, "Y-SCALE=(+2.000000000000000E+00,   2)"), "latin-1")
    s = open_geometry(folder / "conflict.geo", search_input=False)
    assert s.ok and s.parinp == [0.005, 1.0]
    assert any(d.severity == Severity.INFO and "PARINP(2)" in d.message and "1, 2" in d.message
               for d in s.diagnostics)
    zero = text.replace("X-SCALE=(+1.000000000000000E+00,   2)", "X-SCALE=(+0.000000000000000E+00,   2)")
    (folder / "zero.geo").write_text(zero, "latin-1")
    s = open_geometry(folder / "zero.geo", search_input=False)
    assert not s.ok and s.parinp_provisional
    assert any("no se puede construir" in d.message for d in s.diagnostics)


def test_open_case_with_known_geometry(tmp_path):
    folder = _disc_case(tmp_path)
    other = _variant(folder, "fino.in", thickness="2.0")
    case = open_case(other, geometry=folder / "disc.geo")
    assert case.session.ok and case.session.path == folder / "disc.geo"
    assert case.session.parinp == [2.0, 40.0] and case.input_doc.model.parinp == {1: 2.0, 2: 40.0}


def test_new_input_carries_parinp():
    text = new_input_text("disc.geo", ["H2O.mat"], parinp={2: 1.0, 1: 0.005})
    doc = parse_in_text(text, None)
    assert doc.ok and doc.model.parinp == {1: 0.005, 2: 1.0}
    lines = text.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("GEOMFN"))
    assert lines[i + 1].startswith("PARINP 1 ") and lines[i + 2].startswith("PARINP 2 ")
