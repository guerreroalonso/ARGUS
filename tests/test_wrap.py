"""«Envolver» cuerpos existentes en un módulo nuevo, y geometrías nuevas desde cero."""

import numpy as np
import pytest

from argus.pengeom import placement as pl
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import EditError, Primitive, add_primitive, compile_checked, find_element
from argus.pengeom.extent import element_extent
from argus.pengeom.locate import Locator
from argus.pengeom.newgeo import empty_text, new_geometry
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.wrap import check_wrap, fitted_primitive, sample_solids, wrap, wrap_candidates
from argus.render.raytrace import RayTracer
from conftest import CORPUS
from geo_oracle import assert_pengeom_agrees, load_text, materials, random_points, roundtrip

CUBO = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
SCENE = (np.full(3, -6000.0), np.full(3, 6000.0))
STACK = CORPUS / "penhan/stack/3discs.geo"
PARINP = [0.002, 0.004, 0.008, 0.008]


def _extent(geo, kb, box=SCENE):
    return element_extent(geo, RayTracer(geo), kb, box)


def _three_bodies():
    """El cubo del usuario y dos cajas más dentro de la esfera grande."""
    doc = load_geometry(CUBO)
    geo = compile_geometry(doc)
    for c, m in (((700.0, 0, 250), 2), ((0.0, 800, 250), 3)):
        add_primitive(doc, geo, Primitive("box", center=c, sides=(200.0, 200.0, 200.0)), m, geo.find_body("2").index)
        _, doc, geo = roundtrip(doc)
    return doc, geo


def test_wrap_two_bodies_keeps_every_material(tmp_path, need_probe):
    doc, geo = _three_bodies()
    cube, a = geo.find_body("1").index, geo.find_body("3").index
    cont, cands = wrap_candidates(geo, cube)
    assert cont == geo.find_body("2").index and set(cands) == {cube, a, geo.find_body("4").index}
    exts = [_extent(geo, k) for k in (cube, a)]
    prim = fitted_primitive(exts, "box")
    # Justa (y 1e-5 cm más: las paredes del módulo nunca en las caras de sus hijas).
    assert np.allclose(prim.box()[0], [-250, -250, 0], atol=2e-5) and np.allclose(prim.box()[1], [800, 250, 500])
    # La caja justa llega hasta x = 800 y no toca la otra caja (y de 700 a 900): cabe.
    chk = check_wrap(geo, Locator(geo), [cube, a], prim, cont, exts, SCENE)
    assert chk.ok, chk.message
    pts, dirs = random_points(np.full(3, -1200.0), np.full(3, 1200.0), 8000, seed=2)
    before = materials(geo, pts, dirs)[1]
    lab = wrap(doc, geo, [cube, a], prim, name="grupo")
    text, doc2, geo2 = roundtrip(doc)
    m = geo2.find_body(lab)
    assert m.is_module and m.material == 0 and set(m.daughters) >= {geo2.find_body("1").index,
                                                                      geo2.find_body("3").index}
    sphere = find_element(doc2, "body", "2")
    assert [(r.keyword, r.label) for r in sphere.items if not r.is_surface] == [("MODULE", lab), ("BODY", "   4")]
    assert np.array_equal(materials(geo2, pts, dirs)[1], before)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
    # Y ya se pueden girar juntos.
    kb = geo2.find_body(lab).index
    ext = _extent(geo2, kb)
    motion = pl.rotation_about([0, 0, 1], 180.0, pl.element_center(ext))
    pl.move_element(doc2, geo2, kb, motion)
    text, doc3, geo3 = roundtrip(doc2)
    c = pl.element_center(ext)
    p_cube = np.array([[0.0, 0, 250]])
    assert materials(geo3, 2 * c[None, :] - p_cube + [0, 0, 2 * p_cube[0, 2] - 2 * c[2]])[1][0] == 1
    assert_pengeom_agrees(text, tmp_path, pts, dirs, name="girado.geo")


def test_wrap_refuses_overlaps_and_mixed_containers():
    doc, geo = _three_bodies()
    cube, a, b = (geo.find_body(x).index for x in ("1", "3", "4"))
    exts = [_extent(geo, k) for k in (cube, a)]
    big = fitted_primitive(exts, "sphere", margin=150.0)  # la esfera que contiene a las dos toca la tercera caja
    chk = check_wrap(geo, Locator(geo), [cube, a], big, geo.find_body("2").index, exts, SCENE)
    assert not chk.ok and "«4»" in chk.message
    small = Primitive("box", center=(0.0, 0, 250), sides=(400.0, 400.0, 400.0))
    chk = check_wrap(geo, Locator(geo), [cube], small, geo.find_body("2").index, exts[:1], SCENE)
    assert not chk.ok and "sobresale" in chk.message
    # Elementos de contenedores distintos.
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0, 250), radius=10.0), 5, cube)
    _, doc, geo = roundtrip(doc)
    with pytest.raises(EditError, match="mismo contenedor"):
        wrap(doc, geo, [geo.find_body("5").index, geo.find_body("3").index], small)


def test_wrap_inside_a_module_with_limiting_bodies(tmp_path, need_probe):
    """Envolver el disco de abajo (cuerpo 1, limitante del módulo 2) dentro de su módulo."""
    doc = load_geometry(STACK)
    geo = compile_geometry(doc, parinp=PARINP)
    assert geo.ok
    d1 = geo.find_body("1").index
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 0.001), radius=0.0005), 1, d1, parinp=PARINP)
    _, doc, geo = roundtrip(doc, PARINP)
    d1 = geo.find_body("1").index
    box = (np.array([-0.01, -0.01, -0.002]), np.array([0.01, 0.01, 0.01]))
    ext = element_extent(geo, RayTracer(geo), d1, box)
    pts_in = sample_solids(geo, Locator(geo), [d1], [ext])
    assert fitted_primitive([ext], "cylinder", axis="z").radius > 0.0113  # sin puntos: el circunscrito
    prim = fitted_primitive([ext], "cylinder", axis="z", points=pts_in)
    assert prim.radius == 0.008 and prim.height == pytest.approx(0.002 + 2e-5)  # con puntos: el disco justo
    # El disco toca las paredes de su módulo: con la holgura, el cilindro se saldría; justo, cabe.
    assert not check_wrap(geo, Locator(geo), [d1], prim, geo.find_body("2").index, [ext], box).ok
    prim = fitted_primitive([ext], "cylinder", axis="z", points=pts_in, clearance=0.0)
    assert prim.radius == 0.008 and prim.height == 0.002
    chk = check_wrap(geo, Locator(geo), [d1], prim, geo.find_body("2").index, [ext], box)
    assert chk.ok, chk.message
    pts, dirs = random_points(*box, 8000, seed=6)
    before = materials(geo, pts, dirs)[1]
    lab = wrap(doc, geo, [d1], prim, parinp=PARINP)
    text, doc2, geo2 = roundtrip(doc, PARINP)
    m2 = find_element(doc2, "body", "2")
    assert [(r.keyword, r.label) for r in m2.items if not r.is_surface] == [("MODULE", lab)]
    assert geo2.find_body(lab).material == 1
    assert np.array_equal(materials(geo2, pts, dirs)[1], before)
    assert_pengeom_agrees(text, tmp_path, pts, dirs, parinp=PARINP)


def test_wrap_at_the_top_level(tmp_path, need_probe):
    doc = load_geometry(CUBO)
    geo = compile_geometry(doc)
    sphere = geo.find_body("2").index
    ext = _extent(geo, sphere)
    prim = fitted_primitive([ext], "box", margin=100.0)
    chk = check_wrap(geo, Locator(geo), [sphere], prim, geo.nbodys, [ext], SCENE)
    assert chk.ok, chk.message
    lab = wrap(doc, geo, [sphere], prim)
    text, doc2, geo2 = roundtrip(doc)
    root = geo2.find_body(lab)
    # Es el único elemento sin madre y es un módulo: PENGEOM no añade la envolvente (es la raíz).
    assert root.is_module and root.mother == 0 and root.index == geo2.nbodys and not geo2.enclosure_added
    pts, dirs = random_points(*SCENE, 6000, seed=8)
    assert np.array_equal(materials(geo2, pts, dirs)[1], materials(geo, pts, dirs)[1])
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_new_geometry_from_scratch(tmp_path, need_probe):
    doc = parse_geo_text(empty_text("Mi montaje"), None)
    geo = compile_checked(doc)
    assert geo.nbodys == 1 and geo.body(1).origin == "enclosure" and doc.title == "Mi montaje"
    doc = new_geometry("Maniquí de agua", Primitive("box", center=(0.0, 0, 15.0), sides=(30.0, 30.0, 30.0)), 1,
                       name="agua")
    text = doc.lines and "".join(doc.lines)
    geo = compile_checked(doc)
    assert geo.materials() == [1] and "agua" in text
    assert list(materials(geo, np.array([[0.0, 0, 1.0], [0.0, 0, 31.0]]))[1]) == [1, 0]
    doc2 = new_geometry("Con un módulo", Primitive("cylinder", radius=5.0, height=20.0), 2, module=True)
    assert compile_checked(doc2).body(1).is_module
    pts, dirs = random_points(np.full(3, -40.0), np.full(3, 40.0), 3000)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
    d, g = load_text(empty_text("vacía"), tmp_path, "vacia.geo")
    assert g.ok and g.nbodys == 1
