"""Módulos en el editor: crearlos, convertir cuerpos en módulos, moverlos y pegarlos cara con cara."""

import numpy as np
import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import (
    EditError,
    Primitive,
    add_primitive,
    check_convert,
    check_move,
    check_placement,
    compile_checked,
    container_chain,
    convert_to_module,
    delete_body,
    face_from_normal,
    find_element,
    glue_center,
    glue_delta,
    logical_parent,
    move_module,
    nearest_face,
)
from argus.pengeom.extent import element_extent
from argus.pengeom.locate import Locator
from argus.pengeom.model import ModuleElement
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from argus.render.raytrace import RayTracer
from conftest import CORPUS

CUBO = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
SCENE = (np.array([-5000.0, -5000.0, -5000.0]), np.array([5000.0, 5000.0, 5000.0]))


def _load(path=CUBO):
    doc = load_geometry(path)
    geo = compile_geometry(doc, base_dir=path.parent)
    assert geo.ok
    return doc, geo


def _roundtrip(doc):
    text = write_geo_text(doc)
    doc2 = parse_geo_text(text, doc.path)
    assert doc2.ok, [str(d) for d in doc2.diagnostics]
    return text, doc2, compile_checked(doc2)


def _kb(geo, label):
    return geo.find_body(label).index


def _extent(geo, kb):
    return element_extent(geo, RayTracer(geo), kb, SCENE)


def _materials(geo, pts):
    kb, mat = Locator(geo).locate(pts)
    return mat


def test_container_chain_and_parents():
    doc, geo = _load()
    cube, sphere = _kb(geo, "1"), _kb(geo, "2")
    assert logical_parent(geo, cube) == sphere
    assert logical_parent(geo, sphere) == geo.nbodys  # la envolvente
    assert container_chain(geo, cube) == [cube, sphere, geo.nbodys]
    assert container_chain(geo, None) == [geo.nbodys]


def test_add_module_inside_a_body_is_declared_as_limiting():
    doc, geo = _load()
    sphere = _kb(geo, "2")
    prim = Primitive("box", center=(1000.0, 0.0, 250.0), sides=(200.0, 200.0, 200.0))
    assert check_placement(geo, Locator(geo), prim, sphere).ok
    lab = add_primitive(doc, geo, prim, 3, container=sphere, module=True)
    _, doc2, geo2 = _roundtrip(doc)
    m = geo2.find_body(lab)
    assert m.is_module and m.material == 3 and m.daughters in ([], [m.index])
    assert "MODULE  (   3)" in write_geo_text(doc)
    s = find_element(doc2, "body", "2")
    assert [r.keyword for r in s.items if not r.is_surface] == ["BODY", "MODULE"]
    mats = _materials(geo2, np.array([[1000.0, 0, 250], [1000.0, 0, 360], [0.0, 0, 250]]))
    assert list(mats) == [3, 0, 1]
    # Una lámina no puede ser un módulo.
    with pytest.raises(EditError, match="módulo"):
        add_primitive(doc2, geo2, Primitive("slab", center=(0, 0, 100.0), height=10.0), 1, container=sphere,
                      module=True)


def test_new_module_glued_to_a_face():
    doc, geo = _load()
    cube, sphere = _kb(geo, "1"), _kb(geo, "2")
    ext = _extent(geo, cube)
    assert np.allclose(ext[0], [-250, -250, 0]) and np.allclose(ext[1], [250, 250, 500])
    prim = Primitive("box", center=(0.0, 0.0, 250.0), sides=(100.0, 300.0, 400.0))
    x = glue_center(prim, ext, "+x")
    assert x == 300.0
    prim.center = (x, 0.0, 250.0)
    assert check_placement(geo, Locator(geo), prim, sphere).ok
    lab = add_primitive(doc, geo, prim, 2, container=sphere, module=True)
    _, doc2, geo2 = _roundtrip(doc)
    new = _kb(geo2, lab)
    e2 = _extent(geo2, new)
    assert e2[0][0] == 250.0 and e2[1][0] == 350.0  # toca la cara x = 250 del cubo, sin hueco
    eps = 1e-9
    mats = _materials(geo2, np.array([[250 - eps, 0, 250], [250 + eps, 0, 250], [349.0, 140, 440]]))
    assert list(mats) == [1, 2, 2]
    # Por debajo (−z): el centro de una esfera de r = 40 queda a z = −40.
    assert glue_center(Primitive("sphere", radius=40.0), ext, "-z") == -40.0


def test_convert_body_to_module_keeps_every_material():
    doc, geo = _load()
    rng = np.random.default_rng(2)
    pts = np.vstack([rng.uniform(-600, 600, (4000, 3)), rng.uniform(-6000, 6000, (2000, 3))])
    before = _materials(geo, pts)
    cube = _kb(geo, "1")
    # El cubo (sin nada dentro) y después la esfera (con el cubo dentro).
    assert check_convert(geo, Locator(geo), cube, SCENE).ok
    notes = convert_to_module(doc, geo, cube)
    assert "módulo" in notes[0]
    _, doc, geo = _roundtrip(doc)
    assert geo.find_body("1").is_module
    assert [r.keyword for r in find_element(doc, "body", "2").items if not r.is_surface] == ["MODULE"]
    assert np.array_equal(_materials(geo, pts), before)
    assert check_convert(geo, Locator(geo), _kb(geo, "2"), SCENE).ok
    convert_to_module(doc, geo, _kb(geo, "2"))
    _, doc, geo = _roundtrip(doc)
    world = geo.find_body("2")
    assert world.is_module and geo.find_body("1").mother == world.index
    assert np.array_equal(_materials(geo, pts), before)
    with pytest.raises(EditError, match="Ya es un módulo"):
        convert_to_module(doc, geo, world.index)


NESTED = """Cuerpos anidados dentro de un módulo
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   esfera r=20
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+2.000000000000000E+01,   0)
Y-SCALE=(+2.000000000000000E+01,   0)
Z-SCALE=(+2.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   2)   esfera r=10
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+1.000000000000000E+01,   0)
Y-SCALE=(+1.000000000000000E+01,   0)
Z-SCALE=(+1.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   3)   esfera r=3
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+3.000000000000000E+00,   0)
Y-SCALE=(+3.000000000000000E+00,   0)
Z-SCALE=(+3.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   4)   esfera r=1
INDICES=( 1, 1, 1, 0,-1)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)   Z
MATERIAL(   3)
SURFACE (   4), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   2)   Y
MATERIAL(   2)
SURFACE (   3), SIDE POINTER=(-1)
BODY    (   1)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   3)   X
MATERIAL(   1)
SURFACE (   2), SIDE POINTER=(-1)
BODY    (   2)
0000000000000000000000000000000000000000000000000000000000000000
MODULE  (   4)   M
MATERIAL(   4)
SURFACE (   1), SIDE POINTER=(-1)
BODY    (   3)
BODY    (   2)
BODY    (   1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def test_convert_moves_the_nested_bodies_to_the_new_module(tmp_path):
    path = tmp_path / "anidados.geo"
    path.write_text(NESTED, encoding="latin-1")
    doc, geo = _load(path)
    rng = np.random.default_rng(5)
    pts = rng.uniform(-22, 22, (6000, 3))
    before = _materials(geo, pts)
    convert_to_module(doc, geo, _kb(geo, "3"))
    _, doc, geo = _roundtrip(doc)
    x = find_element(doc, "body", "3")
    m = find_element(doc, "body", "4")
    assert isinstance(x, ModuleElement)
    assert [(r.keyword, r.label.strip()) for r in x.items if not r.is_surface] == [("BODY", "2"), ("BODY", "1")]
    assert [(r.keyword, r.label.strip()) for r in m.items if not r.is_surface] == [("MODULE", "3")]
    assert np.array_equal(_materials(geo, pts), before)


def test_convert_refuses_a_limiting_body_that_sticks_out(tmp_path):
    head = "SURFACE (   3)   esfera r=3\nINDICES=( 1, 1, 1, 0,-1)\n"
    text = NESTED.replace(head, head + "Z-SHIFT=(+9.000000000000000E+00,   0)\n")
    path = tmp_path / "sobresale.geo"
    path.write_text(text, encoding="latin-1")
    doc, geo = _load(path)
    chk = check_convert(geo, Locator(geo), _kb(geo, "3"), (np.full(3, -25.0), np.full(3, 25.0)))
    assert not chk.ok and "no está entero" in chk.message


def test_move_and_glue_modules():
    doc, geo = _load()
    sphere = _kb(geo, "2")
    add_primitive(doc, geo, Primitive("box", center=(1500.0, 800.0, 100.0), sides=(200.0, 100.0, 300.0)), 3,
                  container=sphere, module=True)
    _, doc, geo = _roundtrip(doc)
    convert_to_module(doc, geo, _kb(geo, "1"))
    _, doc, geo = _roundtrip(doc)
    a, cube = _kb(geo, "3"), _kb(geo, "1")
    ext_a, ext_c = _extent(geo, a), _extent(geo, cube)
    delta = glue_delta(ext_a, ext_c, "-x")  # a la izquierda del cubo, centrado en su cara
    assert list(delta) == [-250 - 1600.0, -800.0, 150.0]
    assert check_move(geo, Locator(geo), a, delta, ext_a).ok
    move_module(doc, geo, a, delta)
    _, doc, geo = _roundtrip(doc)
    lo, hi = _extent(geo, _kb(geo, "3"))
    assert hi[0] == -250.0 and lo[0] == -450.0 and np.allclose(0.5 * (lo + hi)[1:], [0.0, 250.0])
    # Encima de la esfera grande queda en la envolvente (vacío de PENGEOM): se puede.
    top = glue_delta(_extent(geo, _kb(geo, "3")), _extent(geo, sphere), "+z")
    assert check_move(geo, Locator(geo), _kb(geo, "3"), top, _extent(geo, _kb(geo, "3"))).ok
    # Fuera de la envolvente (r > 1e7 cm), no.
    chk = check_move(geo, Locator(geo), _kb(geo, "3"), [0.0, 0.0, 2e7], _extent(geo, _kb(geo, "3")))
    assert not chk.ok and "fuera" in chk.message
    # Contra el cubo (solapándose) tampoco.
    chk = check_move(geo, Locator(geo), _kb(geo, "3"), np.array([100.0, 0, 0]), _extent(geo, _kb(geo, "3")))
    assert not chk.ok and "«1»" in chk.message
    # Un cuerpo no se mueve.
    doc2, geo2 = _load()
    with pytest.raises(EditError, match="Solo se pueden mover módulos"):
        move_module(doc2, geo2, _kb(geo2, "1"), [1.0, 0, 0])


def test_move_inside_a_rotated_module_uses_its_frame(tmp_path):
    from test_edit import ROTATED_MODULE

    path = tmp_path / "girado.geo"
    path.write_text(ROTATED_MODULE, encoding="latin-1")
    doc, geo = _load(path)
    lab = add_primitive(doc, geo, Primitive("box", center=(0.0, 0.0, 0.0), sides=(2.0, 2.0, 2.0)), 3,
                        container=_kb(geo, "2"), module=True)
    _, doc, geo = _roundtrip(doc)
    kb = _kb(geo, lab)
    lo0, hi0 = _extent(geo, kb)
    move_module(doc, geo, kb, [0.0, 0.0, 1.5])
    _, doc, geo = _roundtrip(doc)
    lo1, hi1 = _extent(geo, _kb(geo, lab))
    assert np.allclose(lo1 - lo0, [0, 0, 1.5], atol=1e-9) and np.allclose(hi1 - hi0, [0, 0, 1.5], atol=1e-9)


def test_delete_empty_module_and_faces():
    doc, geo = _load()
    lab = add_primitive(doc, geo, Primitive("sphere", center=(1000.0, 0, 0), radius=50.0), 1,
                        container=_kb(geo, "2"), module=True)
    _, doc, geo = _roundtrip(doc)
    notes = delete_body(doc, geo, _kb(geo, lab))
    text, doc, geo = _roundtrip(doc)
    assert geo.find_body(lab.strip()) is None and "MODULE" not in text
    assert any("Superficie" in n for n in notes)
    assert nearest_face((np.zeros(3), np.array([10.0, 10, 10])), [9.0, 5, 5]) == "+x"
    assert face_from_normal([0.1, -0.9, 0.2]) == "-y"


PARINP_MODULE = """Módulo desplazado con PARINP
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   esfera r=1
INDICES=( 1, 1, 1, 0,-1)
0000000000000000000000000000000000000000000000000000000000000000
MODULE  (   1)   módulo
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
1111111111111111111111111111111111111111111111111111111111111111
X-SHIFT=(+5.000000000000000E+00,   1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def test_move_module_respects_parinp(tmp_path):
    path = tmp_path / "parinp.geo"
    path.write_text(PARINP_MODULE, encoding="latin-1")
    doc = load_geometry(path)
    geo = compile_geometry(doc, parinp=[3.0], base_dir=tmp_path)
    assert geo.ok
    with pytest.raises(EditError, match="PARINP"):
        move_module(doc, geo, 1, [1.0, 0, 0], parinp=[3.0])
    move_module(doc, geo, 1, [0.0, 2.0, 0], parinp=[3.0])  # Y no está fijado por PARINP
    el = find_element(doc, "body", "1")
    assert [(p.name, p.value, p.ipar) for p in el.transform] == [("X-SHIFT", 5.0, 1), ("Y-SHIFT", 2.0, 0)]


@pytest.mark.oracle
def test_modules_are_what_pengeom_reads(tmp_path, need_probe):
    """PENGEOM real lee la geometría con módulos creados, convertidos, pegados y movidos igual que ARGUS."""
    from argus.oracle.probe import run_probe

    doc, geo = _load()
    sphere = _kb(geo, "2")
    ext = _extent(geo, _kb(geo, "1"))
    prim = Primitive("cylinder", radius=60.0, height=120.0, axis="x")
    prim.center = (glue_center(prim, ext, "+x"), 0.0, 250.0)
    add_primitive(doc, geo, prim, 2, container=sphere, module=True)
    _, doc, geo = _roundtrip(doc)
    add_primitive(doc, geo, Primitive("sphere", center=(310.0, 0.0, 250.0), radius=20.0), 3,
                  container=_kb(geo, "3"))  # un cuerpo dentro del módulo nuevo
    _, doc, geo = _roundtrip(doc)
    convert_to_module(doc, geo, _kb(geo, "1"))
    _, doc, geo = _roundtrip(doc)
    move_module(doc, geo, _kb(geo, "3"), [0.0, 30.0, -10.0])
    text, doc, geo = _roundtrip(doc)
    out = tmp_path / "modulos.geo"
    out.write_text(text, encoding="latin-1")
    g = compile_geometry(load_geometry(out), base_dir=tmp_path)
    assert g.ok
    rng = np.random.default_rng(8)
    pts = np.vstack([rng.uniform(-600, 600, (3000, 3)), rng.uniform(150, 450, (3000, 3))])
    dirs = rng.normal(size=pts.shape)
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    res = run_probe(out, points=np.hstack([pts, dirs]))
    assert res.status.ok, res.status.reason
    t = res.tables
    assert np.allclose(t.coefficients, g.coefficients(), rtol=0, atol=0)
    assert [(pb.material, pb.surfaces, pb.flags) for pb in t.bodies] == \
        [(b.material, b.surfaces, b.flags) for b in g.bodies]
    ib, _ = Locator(g).locate(pts, dirs)
    assert np.array_equal(ib, res.locate_body)
