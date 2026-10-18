"""El .in sigue a la geometría: números y etiquetas de los cuerpos tras editar el .geo."""

import shutil

import numpy as np
import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import Primitive, add_primitive, compile_checked, delete_body
from argus.pengeom.identity import body_keys, is_identity, renumber_map
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from argus.penhan.follow import follow_geometry, references_to
from argus.penhan.inputfile import parse_in_file, parse_in_text
from conftest import CORPUS

STACK = CORPUS / "penhan/stack"
PARINP = [0.002, 0.004, 0.008, 0.008]


def _load(folder):
    doc = load_geometry(folder / "3discs.geo")
    geo = compile_geometry(doc, parinp=PARINP, base_dir=folder)
    assert geo.ok
    return doc, geo


def _recompile(doc):
    doc2 = parse_geo_text(write_geo_text(doc), doc.path)
    return doc2, compile_checked(doc2, PARINP)


def _refs(indoc):
    return [(ln.kw.strip(), ln.label, ln.kb) for ln in indoc.lines
            if ln.spec is not None and ln.spec.mode == "body"]


def test_keys_are_labels_clones_and_enclosure():
    from test_duplicate import CLONED

    geo = compile_geometry(parse_geo_text(CLONED, "c.geo"))
    assert geo.ok
    keys = body_keys(geo)
    assert keys[0] == ("alias", "   10")
    assert ("enclosure",) in keys
    clones = [k for k in keys if k[0] == "clone"]
    assert clones and all(k[1] == ("alias", "   30") for k in clones)  # la identidad del clon
    assert len(set(keys)) == len(keys)


def test_bodies_of_an_include_keep_their_identity_when_it_moves():
    """Un INCLUDE sin «*»: sus etiquetas llevan un sufijo que depende de los INCLUDE de antes; la identidad no."""
    from argus.pengeom.identity import renumber_map

    z = "0" * 64
    end = "END      " + "0" * 55 + "\n"

    def inc(x):
        return parse_geo_text(f"inc\n{z}\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n"
                              f"X-SHIFT=({x:+.15E},   0)\n{z}\nBODY    (   1)\nMATERIAL(   1)\n"
                              f"SURFACE (   1), SIDE POINTER=(-1)\n{z}\n{end}", f"{x}.geo", included=True)

    def main(order):
        text = "m\n" + z + "\n" + "".join(f"INCLUDE\n   FILE=({n:<12})\n{z}\n" for n in order) + end
        doc = parse_geo_text(text, "m.geo")
        for el in doc.elements:
            if el.kind.value == "INCLUDE":
                el.document = inc(3.0 if el.filename == "a.geo" else 6.0)
        return compile_geometry(doc)

    ab, ba = main(["a.geo", "b.geo"]), main(["b.geo", "a.geo"])
    assert ab.ok and ba.ok and ab.balias(1) == ba.balias(1)  # el sufijo es el mismo: «   11»
    assert renumber_map(ab, ba) == {1: 2, 2: 1, 3: 3}  # pero cada bola es la de su fichero (3: la envolvente)
    assert renumber_map(ab, main(["b.geo"]))[1] is None


def test_adding_a_body_renumbers_the_in(tmp_path):
    for f in STACK.iterdir():
        shutil.copy2(f, tmp_path / f.name)
    doc, geo = _load(tmp_path)
    indoc = parse_in_file(tmp_path / "stack.in")
    before = {ln.kw.strip() + str(i): geo.balias(ln.kb) for i, ln in enumerate(indoc.lines)
              if ln.spec is not None and ln.spec.mode == "body"}
    # Una esfera dentro del módulo 2 (en su parte libre, z entre 0.002 y 0.004): va antes del módulo 2.
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 0.003), radius=0.0009), 1,
                  container=geo.find_body("2").index, parinp=PARINP)
    doc2, geo2 = _recompile(doc)
    mapping = renumber_map(geo, geo2)
    assert mapping == {1: 1, 2: 3, 3: 4}
    assert not is_identity(mapping)
    notes = follow_geometry(indoc, mapping)
    assert notes and "actualizadas" in notes[0]
    after = {ln.kw.strip() + str(i): geo2.balias(ln.kb) for i, ln in enumerate(indoc.lines)
             if ln.spec is not None and ln.spec.mode == "body"}
    assert after == before  # cada línea nombra el mismo cuerpo (por su etiqueta) que antes
    text = indoc.text()
    assert "DSMAX  3 0.0001" in text and "DSMAX  4 0.0002" in text and "EDBODY 4   " in text
    # penhmain lee el .in nuevo sin errores.
    again = parse_in_text(text, str(tmp_path / "stack.in"))
    assert again.model.ok and not [d for d in again.diagnostics if d.severity == 2]


def test_deleted_bodies_lose_their_lines(tmp_path):
    for f in STACK.iterdir():
        shutil.copy2(f, tmp_path / f.name)
    doc, geo = _load(tmp_path)
    indoc = parse_in_file(tmp_path / "stack.in")
    kb = geo.find_body("1").index
    assert [ln.kw.strip() for ln in references_to(indoc, geo, kb)] == ["DSMAX", "EDBODY"]
    delete_body(doc, geo, kb)
    doc2, geo2 = _recompile(doc)
    mapping = renumber_map(geo, geo2)
    assert mapping[1] is None and mapping[2] == 1 and mapping[3] == 2
    notes = follow_geometry(indoc, mapping, deleted_labels={"   1"})
    refs = _refs(indoc)
    assert ("DSMAX", None, 1) in refs and ("DSMAX", None, 2) in refs and len([r for r in refs if r[0] == "DSMAX"]) == 2
    assert any("quitada" in n for n in notes)


def test_label_references_follow_renames():
    text = ("TITLE  prueba\nSBODY  '   2'                                              [Active body]\n"
            "DSMAX  '   1' 0.1                   [KB, maximum step length in body KB]\n")
    indoc = parse_in_text(text)
    notes = follow_geometry(indoc, {1: 1, 2: 2}, renames={"   2": "DISC"})
    assert [(r[0], r[1]) for r in _refs(indoc)] == [("SBODY", "DISC"), ("DSMAX", "   1")]
    assert "etiqueta" in notes[0]
    # Material (número negativo) y 0: no se tocan.
    indoc = parse_in_text("TITLE  x\nDSMAX  -1 0.1                   [KB, maximum step length in body KB]\n")
    assert follow_geometry(indoc, {1: 5}) == []


def test_lines_already_edited_in_the_session_follow_too():
    """Las líneas cambiadas antes desde el panel (reescritas enteras) también se renumeran."""
    indoc = parse_in_file(STACK / "stack.in")
    for ln in indoc.find("DSMAX"):
        indoc.modify(ln, values=[0.5])  # ahora son líneas «modificadas» (se sustituyen al analizar)
    follow_geometry(indoc, {1: 1, 2: 3, 3: 4})
    assert [(ln.kb, ln.values) for ln in indoc.find("DSMAX")] == [(1, [0.5]), (3, [0.5]), (4, [0.5])]
    assert [ln.kb for ln in indoc.find("EDBODY")] == [1, 3, 4]


@pytest.mark.parametrize("seed", range(3))
def test_random_edits_keep_every_reference(tmp_path, seed):
    """Tras varias ediciones seguidas, cada referencia del .in sigue apuntando al mismo cuerpo."""
    for f in STACK.iterdir():
        shutil.copy2(f, tmp_path / f.name)
    doc, geo = _load(tmp_path)
    indoc = parse_in_file(tmp_path / "stack.in")
    target = [geo.balias(ln.kb) for ln in indoc.lines if ln.spec is not None and ln.spec.mode == "body"]
    rng = np.random.default_rng(seed)
    for _ in range(4):
        cont = int(rng.choice([geo.find_body("2").index, geo.find_body("3").index, geo.nbodys]))
        z = {geo.find_body("2").index: 0.003, geo.find_body("3").index: 0.006}.get(cont, 0.02 + rng.random())
        prim = Primitive("sphere", center=(0.0, 0.0, z), radius=0.0004 * rng.random() + 0.0001)
        from argus.pengeom.edit import check_placement
        from argus.pengeom.locate import Locator

        if not check_placement(geo, Locator(geo), prim, cont).ok:
            continue
        add_primitive(doc, geo, prim, 1, container=cont, parinp=PARINP)
        doc, geo2 = _recompile(doc)
        follow_geometry(indoc, renumber_map(geo, geo2))
        geo = geo2
    now = [geo.balias(ln.kb) for ln in indoc.lines if ln.spec is not None and ln.spec.mode == "body"]
    assert now == target


def _penhmain_bodies(output: str, geo) -> dict:
    """{(qué, n.º): etiqueta} de lo que penhmain dice que usa: DSMAX por cuerpo y cuerpos de los detectores."""
    import re

    out = {}
    lines = output.splitlines()
    table = False
    for ln in lines:
        if "DSMAX(IB)" in ln:
            table = True
            continue
        if table:
            m = re.match(r"\s+(\d+)\s+([0-9.E+-]+)\s+[0-9.E+-]+", ln)
            if m:
                out[("DSMAX", geo.balias(int(m.group(1))))] = float(m.group(2))
            elif ln.strip().startswith("---"):
                table = False
    det = None
    for ln in lines:
        m = re.search(r">>>>>>\s+(.*detector) # (\d+)", ln)
        if m:
            det = (m.group(1), int(m.group(2)))
        m = re.search(r"Active body =\s+(\d+)", ln)
        if m and det is not None:
            out[det] = geo.balias(int(m.group(1)))
    return out


@pytest.mark.oracle
def test_penhmain_reads_the_followed_in_on_the_same_bodies(tmp_path):
    """El PMRDR real, con el .in renumerado y la geometría nueva, aplica todo a los mismos cuerpos."""
    from argus.oracle.probe import find_sim_probe, run_sim_probe

    if find_sim_probe() is None:
        pytest.skip("Sonda de las comprobaciones de penhmain no compilada (python tools/build_probe.py)")
    for f in STACK.iterdir():
        shutil.copy2(f, tmp_path / f.name)
    doc, geo = _load(tmp_path)
    indoc = parse_in_file(tmp_path / "stack.in")
    res = run_sim_probe(indoc.text(), {"3discs.geo": tmp_path / "3discs.geo"})
    assert res.iretrn == 0, res.reason
    before = _penhmain_bodies(res.output, geo)
    assert len(before) == 7  # 3 DSMAX, 1 detector de impacto y 3 de energía
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 0.003), radius=0.0009), 1,
                  container=geo.find_body("2").index, parinp=PARINP)
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 0.001), radius=0.0005), 1,
                  container=geo.find_body("1").index, parinp=PARINP)
    text = write_geo_text(doc)
    doc2, geo2 = _recompile(doc)
    follow_geometry(indoc, renumber_map(geo, geo2))
    res2 = run_sim_probe(indoc.text(), {"3discs.geo": text})
    assert res2.iretrn == 0, res2.reason
    assert _penhmain_bodies(res2.output, geo2) == before
