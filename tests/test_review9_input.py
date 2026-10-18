"""Novena revisión (0.16.3): el .in.

* Nombres de fichero que penhmain no encontraría: un blanco delante en GEOMFN, MFNAME, IPSFN, EPROF o RESUME (los
  lee con (A20) desde la columna 8 y los abre tal cual) y, con un penhmain de Linux, las tildes (el .in va en
  Latin-1). SRNUCL sin nombre (IRETRN 3120).
* Mensajes de parada que no decían dónde estaba el problema: un tabulador en las 6 primeras columnas, una palabra
  fuera de su sitio que hace que penhmain no llegue a MFNAME o GEOMFN, el BOM de UTF-8.
* La fuente que penhmain no llega a leer no se enseña ni se edita (eran valores por defecto).
* Marcar o desmarcar un cuerpo no reescribe las líneas de los demás.
* Plurales y textos.
"""

from __future__ import annotations

import shutil

import pytest

from argus.core.diagnostics import Severity
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry
from argus.penhan import goals as gl
from argus.penhan import source_edit as se
from argus.penhan.inputfile import parse_in_file, parse_in_text
from argus.penhan.validate import validate_case
from conftest import CORPUS

STACK = CORPUS / "penhan" / "stack"
BASE = """TITLE  t
SKPAR  2
SENERG 1e6
SPOSIT 0 0 0
MFNAME water.mat
GEOMFN a.geo
NSIMSH 1e5
TIME   100
"""


@pytest.fixture
def stack(tmp_path):
    for f in STACK.iterdir():
        shutil.copy(f, tmp_path / f.name)
    return tmp_path / "stack.in"


def _edit(in_path, old: str, new: str):
    text = in_path.read_text(encoding="latin-1")
    assert old in text
    in_path.write_bytes(text.replace(old, new).encode("latin-1"))
    return parse_in_file(in_path)


# ---------------------------------------------------------------------------------------------------------------------
# Nombres de fichero
# ---------------------------------------------------------------------------------------------------------------------

def test_a_material_name_with_a_leading_blank_is_an_error(stack):
    doc = _edit(stack, "MFNAME Si.mat  ", "MFNAME  Si.mat ")
    errors = [x.message for x in validate_case(doc, None, stack.parent).errors]
    assert any(m.startswith("El nombre de MFNAME empieza por un espacio (columna 8 en blanco): penhmain buscaría "
                            "« Si.mat»") for m in errors)


def test_a_geometry_name_with_a_leading_blank_is_said_once(stack):
    doc = _edit(stack, "GEOMFN 3discs.geo ", "GEOMFN  3discs.geo")
    errors = [x.message for x in validate_case(doc, None, stack.parent).errors]
    assert any("El nombre de GEOMFN empieza por un espacio" in m for m in errors)
    assert not any("No existe el fichero de geometría" in m for m in errors)  # (no es que falte: está mal escrito)


def test_resume_with_a_leading_blank_is_a_warning(stack):
    doc = _edit(stack, "RESUME dump.dmp ", "RESUME  dump.dmp")
    warnings = [x.message for x in validate_case(doc, None, stack.parent) if x.severity == Severity.WARNING]
    assert any("El nombre de RESUME empieza por un espacio" in m and "empezaría desde cero" in m for m in warnings)


def test_accented_file_names_are_a_warning(stack):
    shutil.copy(stack.parent / "3discs.geo", stack.parent / "discos_ñ.geo")
    doc = _edit(stack, "GEOMFN 3discs.geo ", "GEOMFN discos_ñ.geo")
    warnings = [x.message for x in validate_case(doc, None, stack.parent) if x.severity == Severity.WARNING]
    assert any(m.startswith("«discos_ñ.geo» (GEOMFN) tiene tildes o eñes") and "penhmain de Linux" in m
               for m in warnings)
    assert not any("No existe" in x.message for x in validate_case(doc, None, stack.parent).errors)


@pytest.mark.parametrize("name", ["''", ","])
def test_srnucl_without_a_name(name):
    text = BASE.replace("SKPAR  2\nSENERG 1e6\n", f"SKPAR  0\nSRNUCL {name}\n")
    doc = parse_in_text(text)
    assert doc.model.ok and doc.model.source.radioactive
    errors = validate_case(doc, None, None).errors
    assert [x.code for x in errors if "SRNUCL" in x.message] == [3120]


# ---------------------------------------------------------------------------------------------------------------------
# Mensajes de parada
# ---------------------------------------------------------------------------------------------------------------------

def _stop(text: str):
    doc = parse_in_text(text)
    assert not doc.model.ok
    e = doc.diagnostics.errors[0]
    return e.code, e.message


def test_a_tab_in_the_keyword_columns_is_named():
    code, msg = _stop(BASE.replace("TIME   100", "TIME\t\t100"))
    assert code == 3035 and "llevan un tabulador" in msg and "«TIME⇥⇥»" in msg
    assert "no es una palabra clave" not in msg
    code, msg = _stop(BASE.replace("SKPAR  2", "SKPAR\t2"))
    assert code == 3013 and "llevan un tabulador" in msg and "«SKPAR⇥»" in msg
    code, msg = _stop(BASE.replace("NSIMSH 1e5", "\t.\nNSIMSH 1e5"))
    assert code == 3035 and "llevan un tabulador" in msg


def test_a_keyword_out_of_place_before_mfname_or_geomfn():
    code, msg = _stop(BASE.replace("SKPAR  2\n", "").replace("SPOSIT", "SKPAR  2\nSPOSIT"))
    assert code == 3013 and "Hay una línea MFNAME más abajo (línea 5)" in msg and "«SKPAR» (línea 3)" in msg
    code, msg = _stop(BASE.replace("MFNAME water.mat\n", "MFNAME water.mat\nMSIMPH 1e5 1e5 1e5 0.05 0.05 2e3\n"
                                                         "MSIMPA 1e3 1e3 1e3 0.05 0.05 1e3 1e3\n"))
    assert code == 3016 and "Hay una línea GEOMFN más abajo" in msg and "«MSIMPA»" in msg
    # Sin la línea, no se dice nada de más.
    code, msg = _stop(BASE.replace("MFNAME water.mat\n", ""))
    assert code == 3013 and "más abajo" not in msg


def test_a_utf8_bom_is_named():
    code, msg = _stop("﻿".encode().decode("latin-1") + BASE)
    assert code == 3001 and "BOM de UTF-8" in msg


# ---------------------------------------------------------------------------------------------------------------------
# La fuente que no se lee
# ---------------------------------------------------------------------------------------------------------------------

def test_source_read_says_if_penhmain_read_the_source():
    doc = parse_in_text(BASE.replace("SKPAR  2", "SKPAR  2."))
    assert not doc.model.ok and not doc.model.source_read  # lo que hay en source son valores por defecto
    doc = parse_in_text(BASE.replace("MFNAME water.mat\n", "MFNAME water.mat\nMSIMPH 1e5 1e5 1e5 0.05 0.05 2e3\n"
                                                           "MSIMPA 1e3 1e3 1e3 0.05 0.05 1e3 1e3\n"))
    assert not doc.model.ok and doc.model.source_read and doc.model.source.energy == 1e6
    assert parse_in_text(BASE).model.source_read


# ---------------------------------------------------------------------------------------------------------------------
# Cuerpos activos: lo que ya estaba escrito se queda
# ---------------------------------------------------------------------------------------------------------------------

def test_ticking_a_body_keeps_the_other_lines(stack):
    doc = _edit(stack, "SCONE  0 0 0", "SBOX   1 1 1\nSBODY  2\nSBODY  '   3'  [b]\nSCONE  0 0 0")
    geo = compile_geometry(load_geometry(stack.parent / "3discs.geo"), parinp=doc.model.parinp_list())
    assert geo.ok and se.active_bodies(doc, geo) == {2, 3}
    before = [ln for ln in doc.text().splitlines() if ln.startswith("SBODY")]
    se.set_active_bodies(doc, geo, [1, 2, 3])
    lines = [ln for ln in doc.text().splitlines() if ln.startswith("SBODY")]
    assert lines[1:] == before  # las que estaban, igual (SBODY 2 por número)
    assert lines[0].startswith("SBODY  '   1'")  # el nuevo, delante (como en la lista)
    se.set_active_bodies(doc, geo, [2, 3])
    assert [ln for ln in doc.text().splitlines() if ln.startswith("SBODY")] == before


def test_merge_refs_keeps_the_written_order():
    kept = [(2, "b"), (None, "x"), (3, "c")]
    assert se.merge_refs(kept, [(1, "a")], [1, 2, 3]) == ["a", "b", "x", "c"]
    assert se.merge_refs(kept, [(4, "d")], [1, 2, 3, 4]) == ["b", "x", "c", "d"]
    assert se.merge_refs([(3, "c"), (2, "b")], [(1, "a")], [1, 2, 3]) == ["a", "c", "b"]


# ---------------------------------------------------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------------------------------------------------

def test_goal_messages_name_the_body_without_its_blanks(stack):
    doc = parse_in_file(stack)
    geo = compile_geometry(load_geometry(stack.parent / "3discs.geo"), parinp=doc.model.parinp_list())
    msg = gl.apply(doc, "espectro", geo=geo, kb=3)
    assert "«3»" in msg and "   3" not in msg
