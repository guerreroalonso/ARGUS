"""Edición de geometrías (F6): añadir, cambiar y quitar cuerpos con las reglas de PENGEOM."""

import numpy as np
import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import (
    EditError,
    Primitive,
    add_primitive,
    check_placement,
    compile_checked,
    delete_body,
    editable_body,
    find_element,
    new_label,
    reduced_values,
    set_description,
    set_material,
    set_module_transform,
    set_surface_params,
)
from argus.pengeom.locate import Locator
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from conftest import CORPUS

CUBO = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"


def _load(path=CUBO):
    doc = load_geometry(path)
    geo = compile_geometry(doc, base_dir=path.parent)
    assert geo.ok
    return doc, geo


def _roundtrip(doc):
    """Texto escrito, vuelto a leer y compilado (lo que vería PENGEOM)."""
    from argus.pengeom.model import IncludeElement

    text = write_geo_text(doc)
    doc2 = parse_geo_text(text, doc.path)
    assert doc2.ok, [str(d) for d in doc2.diagnostics]
    inc = [e for e in doc.elements if isinstance(e, IncludeElement)]
    for e2, e1 in zip([e for e in doc2.elements if isinstance(e, IncludeElement)], inc):
        e2.document = e1.document  # los ficheros incluidos no cambian
    geo2 = compile_checked(doc2)
    return text, doc2, geo2


def _kb(geo, label):
    return geo.find_body(label).index


def test_add_sphere_inside_the_cube():
    doc, geo = _load()
    raw = CUBO.read_bytes().decode("latin-1")
    prim = Primitive("sphere", center=(0.0, 0.0, 250.0), radius=50.0)
    assert check_placement(geo, Locator(geo), prim, 1).ok
    lab = add_primitive(doc, geo, prim, material=2, container=1, name="Tumor")
    assert lab.strip() == "3"
    text, doc2, g2 = _roundtrip(doc)
    loc = Locator(g2)
    kb_new, kb_cube = _kb(g2, lab), _kb(g2, "1")
    ib, mat = loc.locate(np.array([[0, 0, 250], [0, 0, 290], [0, 0, 310], [200, 0, 100], [0, 0, 1000]]))
    assert ib.tolist()[:4] == [kb_new, kb_new, kb_cube, kb_cube]
    assert mat.tolist()[:4] == [2, 2, 1, 1]
    assert g2.body(kb_cube).limiting == [kb_new]
    # Lo que no se ha tocado sigue igual, línea a línea.
    new_lines = text.splitlines()
    assert new_lines[:3] == raw.splitlines()[:3]
    assert "BODY    (   3)   Tumor" in new_lines
    assert "SURFACE (   6)   esfera r=50" in new_lines
    i_body3 = new_lines.index("BODY    (   3)   Tumor")
    i_body1 = next(i for i, ln in enumerate(new_lines) if ln.startswith("BODY    (   1)"))
    assert i_body3 < i_body1  # el cuerpo limitante se define antes


def test_overlap_is_rejected_before_adding():
    doc, geo = _load()
    prim = Primitive("sphere", center=(0.0, 0.0, 480.0), radius=50.0)  # asoma por la cara z = 500
    chk = check_placement(geo, Locator(geo), prim, 1)
    assert not chk.ok and 0.0 < chk.outside_fraction < 0.5 and 2 in chk.offenders
    assert "Cuerpo «2»" in chk.message


def test_add_in_void_body_and_in_the_enclosure():
    doc, geo = _load()
    loc = Locator(geo)
    # Dentro de la esfera de vacío (cuerpo 2), fuera del cubo.
    p1 = Primitive("box", center=(0.0, 0.0, 1500.0), sides=(100.0, 200.0, 300.0))
    assert check_placement(geo, loc, p1, 2).ok
    l1 = add_primitive(doc, geo, p1, 1, container=2)
    # En la envolvente automática (fuera de la esfera de radio 5000).
    p2 = Primitive("cylinder", center=(0.0, 0.0, 7000.0), radius=100.0, height=400.0, axis="x")
    assert check_placement(geo, loc, p2, geo.nbodys).ok
    _, doc, geo_mid = _roundtrip(doc)
    l2 = add_primitive(doc, geo_mid, p2, 3, container=geo_mid.nbodys)
    _, _, g2 = _roundtrip(doc)
    ib, mat = Locator(g2).locate(np.array([[0, 0, 1500], [0, 90, 1500], [150, 0, 7000], [250, 0, 7000],
                                           [0, 0, 7150]]))
    assert ib.tolist()[:4] == [_kb(g2, l1), _kb(g2, l1), _kb(g2, l2), _kb(g2, "2") * 0 + ib[3]]
    assert mat.tolist() == [1, 1, 3, 0, 0]
    assert g2.body(_kb(g2, l2)).mother == g2.nbodys  # hija de la envolvente


def test_add_inside_a_module_daughter_is_declared_in_the_module():
    path = CORPUS / "pengeomjar/saturne"
    doc, geo = _load(path)
    loc = Locator(geo)
    # Un cuerpo con material que sea hija de un módulo (no de la envolvente).
    rng = np.random.default_rng(0)
    from argus.pengeom.bounds import estimate_bounds

    lo, hi = estimate_bounds(geo, locator=loc)
    pts = rng.uniform(lo, hi, size=(20000, 3))
    ib, mat = loc.locate(pts)
    for p, kb, m in zip(pts, ib, mat):
        if kb > geo.nbodys:
            continue
        b = geo.body(int(kb))
        if m > 0 and not b.is_module and b.mother and geo.body(b.mother).origin == "user":
            prim = Primitive("sphere", center=tuple(p), radius=0.05)
            if check_placement(geo, loc, prim, int(kb)).ok:
                break
    else:
        pytest.fail("no se encontró un sitio para la esfera")
    mother = geo.body(b.mother)
    lab = add_primitive(doc, geo, prim, 1, container=int(kb))
    _, _, g2 = _roundtrip(doc)
    new = g2.find_body(lab)
    assert new.mother == g2.find_body(mother.label).index
    assert Locator(g2).locate(np.array([p]))[0][0] == new.index
    assert g2.find_body(b.label).limiting[-1] == new.index


def test_add_inside_a_module():
    path = CORPUS / "pengeomjar/example2"
    doc, geo = _load(path)
    loc = Locator(geo)
    from argus.pengeom.bounds import estimate_bounds

    lo, hi = estimate_bounds(geo, locator=loc)
    rng = np.random.default_rng(1)
    pts = rng.uniform(lo, hi, size=(20000, 3))
    ib, _ = loc.locate(pts)
    found = None
    for p, kb in zip(pts, ib):
        if kb > geo.nbodys:
            continue
        b = geo.body(int(kb))
        if b.is_module and b.origin == "user":
            prim = Primitive("sphere", center=tuple(p), radius=0.02)
            if check_placement(geo, loc, prim, int(kb)).ok:
                found = (p, int(kb), prim)
                break
    if found is None:
        pytest.skip("la geometría no tiene módulos con sitio libre")
    p, kb, prim = found
    lab = add_primitive(doc, geo, prim, 2, container=kb)
    _, _, g2 = _roundtrip(doc)
    new = g2.find_body(lab)
    assert new.mother == g2.find_body(geo.body(kb).label).index
    assert Locator(g2).locate(np.array([p]))[0][0] == new.index


def test_slab_takes_the_part_of_the_body_between_the_planes():
    doc, geo = _load()
    prim = Primitive("slab", center=(0.0, 0.0, 100.0), height=20.0, axis="z")
    assert check_placement(geo, Locator(geo), prim, 1, bounds=(np.array([-300.0] * 3), np.array([300.0] * 3))).ok
    lab = add_primitive(doc, geo, prim, 2, container=1)
    _, doc2, g2 = _roundtrip(doc)
    ib, _ = Locator(g2).locate(np.array([[0, 0, 100], [240, 240, 95], [300, 0, 100], [0, 0, 120]]))
    new, cube, void = _kb(g2, lab), _kb(g2, "1"), _kb(g2, "2")
    assert ib.tolist() == [new, new, void, cube]
    with pytest.raises(EditError):
        add_primitive(doc2, g2, prim, 2, container=g2.nbodys)  # una lámina en la envolvente sería infinita


def test_delete_restores_the_geometry():
    doc, geo = _load()
    before = write_geo_text(doc)
    lab = add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 250.0), radius=50.0), 2, container=1)
    _, doc2, g2 = _roundtrip(doc)
    notes = delete_body(doc2, g2, _kb(g2, lab))
    assert any("ocupa ahora" in n for n in notes) and any("borrada" in n for n in notes)
    g3 = compile_checked(doc2)
    assert np.array_equal(g3.coefficients(), geo.coefficients())
    assert [b.limiting for b in g3.bodies] == [b.limiting for b in geo.bodies]
    # El texto regenerado describe lo mismo que el original.
    assert write_geo_text(doc2).count("BODY    (") == before.count("BODY    (")


def test_delete_nested_body_keeps_inner_body_excluded():
    doc, geo = _load()
    a = add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 250.0), radius=100.0), 2, container=1)
    _, doc, g = _roundtrip(doc)
    b = add_primitive(doc, g, Primitive("sphere", center=(0.0, 0.0, 250.0), radius=30.0), 3,
                      container=_kb(g, a))
    _, doc, g = _roundtrip(doc)
    delete_body(doc, g, _kb(g, a))
    _, _, g = _roundtrip(doc)
    ib, mat = Locator(g).locate(np.array([[0, 0, 250], [0, 0, 300], [0, 0, 400]]))
    assert ib.tolist()[0] == _kb(g, b) and mat.tolist() == [3, 1, 1]  # el cubo ocupa lo que era A
    assert _kb(g, b) in g.find_body("1").limiting


def test_simple_changes_keep_the_rest_of_the_file():
    doc, geo = _load()
    raw = CUBO.read_bytes().decode("latin-1").splitlines()
    set_material(find_element(doc, "body", "1"), 3)
    set_description(find_element(doc, "body", "2"), "Aire alrededor")
    sph = find_element(doc, "surface", "5")
    assert reduced_values(sph)["X-SCALE"] == 5000.0
    set_surface_params(sph, {"X-SCALE": 6000.0, "Y-SCALE": 6000.0, "Z-SCALE": 6000.0})
    text, _, g2 = _roundtrip(doc)
    out = text.splitlines()
    assert "MATERIAL(   3)" in out and "BODY    (   2)   Aire alrededor" in out
    assert "X-SCALE=(+6.000000000000000E+03,   0)" in out
    assert out[:20] == raw[:20]  # cabecera y superficies 1-4: sin tocar
    ib, mat = Locator(g2).locate(np.array([[0, 0, 5500], [0, 0, 250]]))
    assert mat.tolist() == [0, 3] and ib[0] == _kb(g2, "2")
    with pytest.raises(EditError):
        set_surface_params(sph, {"X-SCALE": 0.0})
    with pytest.raises(EditError):
        set_material(sph, 1)


def test_parinp_values_are_protected():
    path = CORPUS / "penhan/disc/disc.geo"
    doc = load_geometry(path)
    geo = compile_geometry(doc, parinp=[40.0, 40.0], base_dir=path.parent)
    assert geo.ok
    el = next(s for s in doc.surfaces() if any(p.ipar > 0 for p in s.params))
    name = next(p.name for p in el.params if p.ipar > 0)
    with pytest.raises(EditError, match="PARINP"):
        set_surface_params(el, {name: 1.0})


def test_module_transform_and_labels():
    path = CORPUS / "pengeomjar/example2"
    doc, geo = _load(path)
    mod = next(m for m in doc.modules())
    set_module_transform(mod, {"Z-SHIFT": 1.5})
    assert any(p.name == "Z-SHIFT" and p.value == 1.5 for p in mod.transform) and mod.has_ones_line
    _roundtrip(doc)
    lab = new_label(doc, "surface", geo)
    assert lab.strip().isdigit() and find_element(doc, "surface", lab) is None
    with pytest.raises(EditError):
        editable_body(geo, geo.nbodys + 5)


def test_editing_elements_from_includes_and_clones_is_refused():
    enclosure_doc, enclosure_geo = _load()
    with pytest.raises(EditError, match="envolvente"):
        editable_body(enclosure_geo, enclosure_geo.nbodys)
    path = CORPUS / "pengeomjar/example2"
    doc, geo = _load(path)
    for b in geo.bodies:
        if b.origin == "clone":
            with pytest.raises(EditError, match="clon"):
                editable_body(geo, b.index)
            break


@pytest.mark.oracle
def test_edited_geometry_is_what_pengeom_reads(tmp_path, need_probe):
    """PENGEOM lee el .geo editado y construye las mismas tablas que ARGUS; LOCATE da lo mismo."""
    from argus.oracle.probe import run_probe

    doc, geo = _load()
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 250.0), radius=50.0), 2, container=1)
    _, doc, geo = _roundtrip(doc)
    add_primitive(doc, geo, Primitive("cylinder", center=(0.0, 0.0, 1500.0), radius=80.0, height=300.0,
                                      axis="y"), 1, container=_kb(geo, "2"))
    _, doc, geo = _roundtrip(doc)
    add_primitive(doc, geo, Primitive("slab", center=(0.0, 0.0, 400.0), height=40.0, axis="z"), 2,
                  container=_kb(geo, "1"))
    set_material(find_element(doc, "body", "2"), 0)
    out = tmp_path / "editada.geo"
    out.write_bytes(write_geo_text(doc).encode("latin-1"))
    g = compile_geometry(load_geometry(out), base_dir=tmp_path)
    assert g.ok
    rng = np.random.default_rng(4)
    pts = np.hstack([rng.uniform(-600, 600, (3000, 3)), rng.normal(size=(3000, 3))])
    pts[:, 3:] /= np.linalg.norm(pts[:, 3:], axis=1)[:, None]
    res = run_probe(out, points=pts)
    assert res.status.ok, res.status.reason
    t = res.tables
    assert np.allclose(t.coefficients, g.coefficients(), rtol=0, atol=0)
    assert [(pb.material, pb.surfaces, pb.flags) for pb in t.bodies] == \
        [(b.material, b.surfaces, b.flags) for b in g.bodies]
    ib, _ = Locator(g).locate(pts[:, :3], pts[:, 3:])
    assert np.array_equal(ib, res.locate_body)


ROTATED_MODULE = """Módulo girado y desplazado
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   esfera del módulo
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+1.000000000000000E+01,   0)
Y-SCALE=(+1.000000000000000E+01,   0)
Z-SCALE=(+1.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   2)   bola
INDICES=( 1, 1, 1, 0,-1)
X-SHIFT=(+5.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)   bola
MATERIAL(   2)
SURFACE (   2), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
MODULE  (   2)   módulo
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
BODY    (   1)
1111111111111111111111111111111111111111111111111111111111111111
  OMEGA=(+2.000000000000000E+01,   0) DEG
  THETA=(+3.000000000000000E+01,   0) DEG
    PHI=(+4.000000000000000E+01,   0) DEG
X-SHIFT=(+1.000000000000000E+02,   0)
Y-SHIFT=(-5.000000000000000E+01,   0)
Z-SHIFT=(+2.500000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


@pytest.mark.parametrize("container", ["module", "ball"])
def test_bodies_in_a_rotated_module_are_placed_in_global_coordinates(container):
    from argus.pengeom.edit import euler_angles, local_frame
    from argus.pengeom.quadric import rotation_matrix

    doc = parse_geo_text(ROTATED_MODULE)
    geo = compile_checked(doc)
    loc = Locator(geo)
    m = geo.find_body("2")
    rot, shift = local_frame(geo, m.index)
    assert np.allclose(shift, [100, -50, 25])
    om, th, ph = euler_angles(rot.T)
    assert np.allclose(rotation_matrix(*np.radians([om, th, ph])), rot.T)
    ball_center = rot @ np.array([5.0, 0.0, 0.0]) + shift
    if container == "module":
        cont, c = m.index, shift + np.array([-4.0, 0.0, 1.0])
        prims = [Primitive("box", center=tuple(c), sides=(3.0, 2.0, 4.0)),
                 Primitive("cylinder", center=tuple(c + [0, 0, -5.0]), radius=1.0, height=4.0, axis="x")]
    else:
        cont, c = geo.find_body("1").index, ball_center
        prims = [Primitive("box", center=tuple(c), sides=(0.6, 0.4, 0.8)),
                 Primitive("cylinder", center=tuple(c + [0, 0.3, 0]), radius=0.1, height=0.8, axis="x")]
    labels = []
    for prim in prims:
        assert check_placement(geo, loc, prim, cont).ok, prim
        labels.append(add_primitive(doc, geo, prim, 3, container=cont))
        doc = parse_geo_text(write_geo_text(doc))
        geo = compile_checked(doc)
        loc = Locator(geo)
        cont = geo.find_body("2" if container == "module" else "1").index
    box, cyl = (geo.find_body(lab).index for lab in labels)
    b = prims[0]
    half = 0.5 * np.array(b.sides)
    inside = np.array(b.center) + half * np.array([[0.99, 0.99, 0.99], [-0.99, 0.99, -0.99], [0.0, -0.99, 0.5]])
    outside = np.array(b.center) + half * np.array([[1.02, 0, 0], [0, -1.02, 0], [0, 0, 1.02]])
    assert (loc.locate(inside)[0] == box).all()
    assert not (loc.locate(outside)[0] == box).any()
    cy = prims[1]
    k = np.array(cy.center)
    assert (loc.locate(k + np.array([[0.49 * cy.height, 0, 0], [-0.49 * cy.height, 0.9 * cy.radius, 0]]))[0]
            == cyl).all()
    assert not (loc.locate(k + np.array([[0.51 * cy.height, 0, 0], [0, 1.05 * cy.radius, 0]]))[0] == cyl).any()
