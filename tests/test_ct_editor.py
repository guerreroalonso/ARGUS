"""El TAC con el editor (revisión de la 0.8): módulos con el CT que no se pueden mover, girar ni copiar;
PARINP que desplazan el contenedor; el nivel superior sin envolvente; el material 9999; quitar materiales
del CT; eliminar el CT; y las comprobaciones de penhmain con un CT."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from argus.ct.importer import add_ctbox, ct_containers
from argus.pengeom import ct as C
from argus.pengeom import edit as ge
from argus.pengeom import placement as pl
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.duplicate import duplicate
from argus.pengeom.edit import EditError
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.validate import ct_placement_errors
from argus.pengeom.writer import write_geo_text

Z = "0" * 64
END = "END      " + "0" * 55 + "\n"


def _plane(k, ax, a0):
    s = "+" if a0 >= 0 else "-"
    return (f"SURFACE ({k:4d})\nINDICES=( 0, 0, 0, 0, 0)\n     A{ax}=(+1.000000000000000E+00,   0)\n"
            f"     A0=({s}{abs(a0):.15E},   0)\n{Z}\n")


def _box_surfs(start, lo, hi):
    out, k = "", start
    for i, ax in enumerate("XYZ"):
        out += _plane(k, ax, -lo[i])
        out += _plane(k + 1, ax, -hi[i])
        k += 2
    return out


def _box_refs(start):
    return "".join(f"SURFACE ({k:4d}), SIDE POINTER=({'+1' if (k - start) % 2 == 0 else '-1'})\n"
                   for k in range(start, start + 6))


def _element(kw, label, mat, refs, desc=""):
    return f"{kw:<8}({label:>4})   {desc}\nMATERIAL({mat:4d})\n" + refs + Z + "\n"


def _write_ct(folder):
    mat = np.ones((4, 4, 4), dtype=np.int32)
    mat[:, :, 2:] = 2
    C.write_ct_file(folder / "tac.ct", "CT de prueba", (8.0, 8.0, 8.0), mat, np.where(mat == 1, 1.0, 1.85))


def _case(tmp_path, kind="module", transform="", parinp=None, other=True):
    """Geometría con un CT de 8 cm en el cuerpo o módulo «1» (caja de ±20 cm) o en el nivel superior."""
    folder = tmp_path / kind
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    _write_ct(folder)
    text = "Geometria con TAC\n" + Z + "\n"
    cont = None
    if kind in ("module", "body"):
        text += _box_surfs(1, (-20, -20, -20), (20, 20, 20))
        el = _element("MODULE" if kind == "module" else "BODY", "1", 1, _box_refs(1), "caja")
        if transform:
            el = el[: -len(Z) - 1] + "1" * 64 + "\n" + transform + Z + "\n"
        text += el
        cont = "1"
    if other:
        text += _box_surfs(11, (30, -5, -5), (40, 5, 5)) + _element("MODULE", "2", 1, _box_refs(11), "otra")
    text += END
    p = folder / "g.geo"
    doc = parse_geo_text(text, str(p))
    geo = compile_geometry(doc, parinp=parinp, base_dir=folder)
    assert geo.ok, [d.message for d in geo.diagnostics.errors]
    kb = geo.find_body(cont).index if cont else None
    add_ctbox(doc, geo, "tac.ct", np.eye(3), np.array([-4.0, -4.0, -4.0]), kb, "TAC", "TAC de prueba",
              parinp=parinp)
    p.write_text(write_geo_text(doc))
    geo = compile_geometry(load_geometry(p), parinp=parinp, base_dir=folder)
    assert geo.ok, [d.message for d in geo.diagnostics.errors]
    return p, geo


def test_modules_that_hold_the_ct_cannot_move_rotate_or_be_copied(tmp_path):
    _p, geo = _case(tmp_path)
    kb = geo.find_body("1").index
    assert ge.contains_ct(geo, kb) and ge.contains_ct(geo, geo.ct_body)
    with pytest.raises(EditError, match="caja CT"):
        pl.placeable(geo, kb)
    for linked in (True, False):
        with pytest.raises(EditError):
            duplicate(geo.document, geo, kb, [0.0, 50.0, 0.0], 1, linked=linked)
    other = geo.find_body("2").index
    pl.placeable(geo, other)  # los demás, sí


def test_a_container_shifted_by_parinp_is_not_offered_and_is_an_error(tmp_path):
    shift = "X-SHIFT=(+0.000000000000000E+00,   1)\n"
    _p, geo = _case(tmp_path, transform=shift, parinp=[0.0])  # con PARINP(1) = 0 no se mueve
    assert not ct_placement_errors(geo)
    moved = compile_geometry(load_geometry(_p), parinp=[10.0], base_dir=_p.parent)
    errs = ct_placement_errors(moved)
    assert errs and "IRETRN 2113" in errs[0]  # PENGEOM movería las caras de la caja pero no los vóxeles
    kb = moved.find_body("1").index
    assert kb not in ct_containers(moved, [10.0])


def test_a_second_material_9999_is_reported(tmp_path):
    p, geo = _case(tmp_path)
    text = p.read_text().replace("MATERIAL(   1)\n", "MATERIAL(9999)\n", 1)  # el módulo «1» con la marca del CT
    geo2 = compile_geometry(parse_geo_text(text, str(p)), base_dir=p.parent)
    assert any("MATERIAL(9999)" in m for m in ct_placement_errors(geo2))
    with pytest.raises(EditError, match="9999"):
        ge.check_material(9999)
    with pytest.raises(EditError):
        ge.check_material(10000)
    ge.check_material(9998)


def test_the_ct_at_the_top_level_of_a_geometry_without_enclosure(tmp_path):
    p, geo = _case(tmp_path, kind="top", other=True)
    assert geo.ct is not None
    top = ge.top_level(geo)
    assert ge.is_top_level(geo, top)
    # Solo con el CT: la geometría no tiene envolvente y el nivel superior es «fuera de todo».
    p2, geo2 = _case(tmp_path, kind="top", other=False)
    assert ge.top_level(geo2) == geo2.nbodys + (0 if geo2.enclosure_added else 1)
    assert ge.contains_ct(geo2, geo2.ct_body)


def test_materials_used_only_by_the_ct_count_as_used(tmp_path):
    _p, geo = _case(tmp_path)
    used = geo.used_materials()
    assert {1, 2} <= set(used) and 9999 not in used


def test_the_ct_can_be_deleted_to_replace_it(tmp_path):
    p, geo = _case(tmp_path)
    doc = geo.document
    notes = ge.delete_ct(doc, geo)
    assert isinstance(notes, list)
    p.write_text(write_geo_text(doc))
    geo2 = compile_geometry(load_geometry(p), base_dir=p.parent)
    assert geo2.ok and geo2.ct is None and "CTBOX" not in p.read_text()
    assert (p.parent / "tac.ct").is_file()  # el .ct no se borra
    with pytest.raises(EditError):
        ge.delete_ct(geo2.document, geo2)


# -- comprobaciones de penhmain con un CT ---------------------------------------------------------------------
def _in_text() -> str:
    from conftest import CORPUS

    text = (CORPUS / "usuario/100Mev15-174/maniqui.in").read_bytes().decode("latin-1")
    return text.replace("cubo-esfera.geo", "g.geo          ")


def test_penhmain_rules_with_a_ct(tmp_path):
    from argus.penhan.inputfile import parse_in_text
    from argus.penhan.validate import validate_case

    p, geo = _case(tmp_path)
    folder = p.parent
    lab = geo.balias(geo.ct_body)
    text = _in_text().replace("       >>>>>>>> Emerging particles.",
                        f"IFORCE '{lab}' 4 2 -10 0.1 1.0\n       >>>>>>>> Emerging particles.")
    doc = parse_in_text(text, str(folder / "caso.in"))
    msgs = [d.message for d in validate_case(doc, geo, folder)]
    assert any("FORCER" in m and ("CT" in m or "TAC" in m) for m in msgs), msgs
    # CTTRACK: la densidad media no basta en el pulmón (ADR-010).
    assert any("CTTRACK" in m and "pulmón" in m for m in msgs), msgs
    assert Path(folder / "tac.ct").is_file()
