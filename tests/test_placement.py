"""Girar y mover módulos y clones como un todo (punto 4 del editor)."""

import math

import numpy as np
import pytest

from argus.pengeom import placement as pl
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import EditError, Primitive, add_primitive, find_element
from argus.pengeom.extent import element_extent
from argus.pengeom.locate import Locator
from argus.pengeom.parser import load_geometry
from argus.render.raytrace import RayTracer
from conftest import CORPUS
from geo_oracle import assert_pengeom_agrees, load_text, materials, random_points, roundtrip

CUBO = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
SCENE = (np.full(3, -6000.0), np.full(3, 6000.0))


def _extent(geo, kb, box=SCENE):
    return element_extent(geo, RayTracer(geo), kb, box)


def _with_module(center=(1500.0, 800.0, 100.0), sides=(200.0, 100.0, 300.0)):
    doc = load_geometry(CUBO)
    geo = compile_geometry(doc)
    lab = add_primitive(doc, geo, Primitive("box", center=center, sides=sides), 3, geo.find_body("2").index,
                        module=True)
    _, doc, geo = roundtrip(doc)
    # Un cuerpo dentro del módulo, descentrado (para ver que gira con él).
    add_primitive(doc, geo, Primitive("sphere", center=(center[0] + 50, center[1], center[2] + 100), radius=20.0), 4,
                  geo.find_body(lab).index)
    _, doc, geo = roundtrip(doc)
    return doc, geo, lab


def test_rotation_helpers():
    r = pl.rotation([0, 0, 1], 90.0)
    assert np.array_equal(r, [[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    g = pl.rotation_about([0, 0, 1], 90.0, [1.0, 0, 0])
    assert np.allclose(g[0] @ np.array([2.0, 0, 0]) + g[1], [1.0, 1.0, 0])
    a = pl.rotation_about([1, 2, 3], 33.0, [4.0, 5, 6])
    b = pl.inverse(a)
    c = pl.compose(a, b)
    assert np.allclose(c[0], np.eye(3)) and np.allclose(c[1], 0)
    vals = pl.transform_values((pl.rotation([0, 0, 1], 30.0), np.array([1.0, 2, 3])))
    assert math.isclose((vals["OMEGA"] + vals["PHI"]) % 360, 30.0) and vals["THETA"] == 0.0


def test_rotate_a_module_about_its_center(tmp_path, need_probe):
    doc, geo, lab = _with_module()
    kb = geo.find_body(lab).index
    ext = _extent(geo, kb)
    assert np.allclose(ext[0], [1400, 750, -50]) and np.allclose(ext[1], [1600, 850, 250])
    c = pl.element_center(ext)
    motion = pl.rotation_about([0, 0, 1], 90.0, c)
    assert pl.check_motion(geo, Locator(geo), kb, motion, ext).ok
    changed = pl.move_element(doc, geo, kb, motion)
    assert "PHI" in changed or "OMEGA" in changed
    text, doc, geo = roundtrip(doc)
    ext2 = _extent(geo, geo.find_body(lab).index)
    assert np.allclose(ext2[0], [1450, 700, -50], atol=1e-9) and np.allclose(ext2[1], [1550, 900, 250], atol=1e-9)
    # La bola de dentro (a +50 en x del centro) queda a +50 en y.
    assert list(materials(geo, np.array([[1500.0, 850, 200], [1550.0, 800, 200]]))[1]) == [4, 3]
    pts, dirs = random_points([1300.0, 600, -100], [1700.0, 1000, 300], 5000)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_rotations_compose_and_come_back(tmp_path):
    doc, geo, lab = _with_module()
    kb = geo.find_body(lab).index
    pts, dirs = random_points([1300.0, 600, -100], [1700.0, 1000, 300], 4000, seed=1)
    before = materials(geo, pts, dirs)[1]
    c = pl.element_center(_extent(geo, kb))
    for axis, ang in (([1, 0, 0], 37.0), ([0, 1, 0], -12.5), ([1, 1, 0], 71.0)):
        pl.move_element(doc, geo, geo.find_body(lab).index, pl.rotation_about(axis, ang, c))
        _, doc, geo = roundtrip(doc)
    for axis, ang in reversed((([1, 0, 0], 37.0), ([0, 1, 0], -12.5), ([1, 1, 0], 71.0))):
        pl.move_element(doc, geo, geo.find_body(lab).index, pl.rotation_about(axis, -ang, c))
        _, doc, geo = roundtrip(doc)
    after = materials(geo, pts, dirs)[1]
    assert (after != before).mean() < 1e-3  # solo puntos a ~1e-10 de las superficies
    el = find_element(doc, "body", lab)
    assert all(abs(p.value) < 1e-6 for p in el.transform if p.name in ("OMEGA", "THETA", "PHI")) or \
        not el.transform


def test_motion_check_sees_overlaps():
    doc, geo, lab = _with_module(center=(350.0, 0.0, 250.0), sides=(200.0, 100.0, 300.0))
    kb = geo.find_body(lab).index
    ext = _extent(geo, kb)
    # Girado 90° alrededor de y, su lado de 300 queda en x y se mete en el cubo (x <= 250).
    chk = pl.check_motion(geo, Locator(geo), kb, pl.rotation_about([0, 1, 0], 90.0, pl.element_center(ext)), ext)
    assert not chk.ok and "«1»" in chk.message
    chk = pl.check_motion(geo, Locator(geo), kb, pl.rotation_about([1, 0, 0], 90.0, pl.element_center(ext)), ext)
    assert chk.ok


def test_bodies_cannot_be_rotated_but_clones_can(tmp_path, need_probe):
    from test_duplicate import BOX, CLONED

    doc = load_geometry(CUBO)
    geo = compile_geometry(doc)
    with pytest.raises(EditError, match="módulos y clones"):
        pl.move_element(doc, geo, geo.find_body("1").index, pl.translation([1.0, 0, 0]))
    doc, geo = load_text(CLONED, tmp_path)
    k = geo.find_body("3").index
    ext = element_extent(geo, RayTracer(geo), k, BOX)
    motion = pl.compose(pl.translation([0.0, 10.0, 0]), pl.rotation_about([0, 0, 1], 45.0, pl.element_center(ext)))
    assert pl.check_motion(geo, Locator(geo), k, motion, ext).ok
    pl.move_element(doc, geo, k, motion)
    text, doc, geo = roundtrip(doc)
    # El centro de la esfera del clon estaba en (30, 0, 0) y es también el centro de su caja: ahora (30, 10, 0).
    assert list(materials(geo, np.array([[30.0, 10.0, 0], [30.0, 0, 0]]))[1]) == [2, 3]
    pts, dirs = random_points(*BOX, 6000, seed=4)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
    notes = pl.delete_clone(doc, geo, geo.find_body("3").index)
    text, doc, geo = roundtrip(doc)
    assert "CLONE" not in text and notes
    assert list(materials(geo, np.array([[30.0, 10.0, 0]]))[1]) == [3]


def test_parinp_values_are_not_touched_by_translations(tmp_path):
    from test_modules import PARINP_MODULE

    doc, geo = load_text(PARINP_MODULE, tmp_path, parinp=[3.0])
    kb = geo.find_body("1").index
    pl.move_element(doc, geo, kb, pl.translation([0.0, 2.0, 0.0]), parinp=[3.0])
    el = find_element(doc, "body", "1")
    assert [(p.name, p.value, p.ipar) for p in el.transform] == [("X-SHIFT", 5.0, 1), ("Y-SHIFT", 2.0, 0)]
    with pytest.raises(EditError, match="PARINP"):
        pl.move_element(doc, geo, kb, pl.translation([1.0, 0.0, 0.0]), parinp=[3.0])
    with pytest.raises(EditError, match="PARINP"):
        pl.move_element(doc, geo, kb, pl.rotation_about([0, 0, 1], 10.0, [3.0, 0, 0]), parinp=[3.0])


ONLY_MODULE = """XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
Un cilindro de agua que es toda la geometría (un solo módulo, sin envolvente)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   Plano z = 0
INDICES=( 0, 0, 0, 1, 0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   2)   Plano z = 30
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=(+3.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   3)   Cilindro r = 20
INDICES=( 1, 1, 0, 0,-1)
X-SCALE=(+2.000000000000000E+01,   0)
Y-SCALE=(+2.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
MODULE  (   1)   Cilindro
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(+1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def test_the_only_top_level_module_can_move_anywhere(tmp_path, need_probe):
    # Con un solo elemento arriba, GEOMIN no añade la envolvente: ese módulo es toda la geometría y fuera de él no
    # hay nada con lo que solaparse (antes ARGUS no dejaba ni girarlo: «caería fuera de la geometría»).
    doc, geo = load_text(ONLY_MODULE, tmp_path, "cilindro.geo")
    assert geo.nbodys == 1 and not geo.enclosure_added
    ext = _extent(geo, 1)
    c = pl.element_center(ext)
    loc = Locator(geo)
    turn = pl.rotation_about([0, 1, 0], 30.0, c)
    assert pl.check_motion(geo, loc, 1, turn, ext).ok
    assert pl.check_motion(geo, loc, 1, pl.translation([50.0, 0, -10]), ext).ok
    pl.move_element(doc, geo, 1, turn)
    text, doc, geo = roundtrip(doc)
    axis = turn[0] @ np.array([0.0, 0, 1])
    side = turn[0] @ np.array([1.0, 0, 0])
    inside = np.array([c + 14.0 * axis, c - 14.0 * axis, c + 19.0 * side])
    outside = np.array([c + 16.0 * axis + 19.0 * side, c + 21.0 * side])
    assert list(materials(geo, inside)[1]) == [1, 1, 1] and list(materials(geo, outside)[1]) == [0, 0]
    pts, dirs = random_points([-50.0, -50, -20], [50.0, 50, 50], 5000, seed=3)
    assert_pengeom_agrees(text, tmp_path, pts, dirs, name="cilindro-girado.geo")
