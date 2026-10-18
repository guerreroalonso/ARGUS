"""Duplicar: copias independientes y clones (CLONE), y convertir un clon en copia."""

import numpy as np
import pytest

from argus.pengeom import placement as pl
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.duplicate import check_copies, clone_transform, duplicate, materialize_clone
from argus.pengeom.edit import EditError, Primitive, add_primitive, convert_to_module, find_element
from argus.pengeom.extent import element_extent
from argus.pengeom.identity import renumber_map
from argus.pengeom.locate import Locator
from argus.pengeom.parser import load_geometry
from argus.render.raytrace import RayTracer
from conftest import CORPUS
from geo_oracle import assert_pengeom_agrees, load_text, materials, random_points, roundtrip

SEP = "0" * 64
END = "END      " + "0" * 55


def _surf(label, idx, **params):
    lines = [f"SURFACE ({label:>4})", "INDICES=(" + ",".join(f"{i:2d}" for i in idx) + ")"]
    names = {"xs": "X-SCALE", "ys": "Y-SCALE", "zs": "Z-SCALE", "om": "  OMEGA", "th": "  THETA", "ph": "    PHI",
             "dx": "X-SHIFT", "dy": "Y-SHIFT", "dz": "Z-SHIFT"}
    for k, v in params.items():
        unit = " DEG" if k in ("om", "th", "ph") else ""
        lines.append(f"{names[k]}=({v:+.15E},   0){unit}")
    return "\n".join(lines + [SEP]) + "\n"


CLONED = ("Prueba de clones\n" + SEP + "\n"
          + _surf("1", (1, 0, 0, 0, -1), xs=5.0) + _surf("2", (0, 1, 0, 0, -1), ys=5.0)
          + _surf("3", (0, 0, 1, 0, -1), zs=5.0) + _surf("4", (1, 1, 1, 0, -1), xs=2.0, ys=2.0, zs=2.0)
          + _surf("5", (1, 0, 0, 0, -1), xs=50.0) + _surf("6", (0, 1, 0, 0, -1), ys=50.0)
          + _surf("7", (0, 0, 1, 0, -1), zs=50.0)
          + "BODY    (   1)   esfera\nMATERIAL(   2)\nSURFACE (   4), SIDE POINTER=(-1)\n" + SEP + "\n"
          + "MODULE  (   2)   caja\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n"
            "SURFACE (   2), SIDE POINTER=(-1)\nSURFACE (   3), SIDE POINTER=(-1)\nBODY    (   1)\n"
          + "1" * 64 + "\nX-SHIFT=(+1.000000000000000E+01,   0)\n" + SEP + "\n"
          + "CLONE   (   3)   copia\nMODULE  (   2)\n" + "1" * 64
          + "\nX-SHIFT=(+2.000000000000000E+01,   0)\n" + SEP + "\n"
          + "BODY    (   4)   mundo\nMATERIAL(   3)\nSURFACE (   5), SIDE POINTER=(-1)\n"
            "SURFACE (   6), SIDE POINTER=(-1)\nSURFACE (   7), SIDE POINTER=(-1)\nMODULE  (   2)\nMODULE  (   3)\n"
          + SEP + "\n" + END + "\n")

BOX = (np.full(3, -60.0), np.full(3, 60.0))


def _extent(geo, kb, box=BOX):
    return element_extent(geo, RayTracer(geo), kb, box)


def test_copies_of_a_module_are_independent_and_glued(tmp_path, need_probe):
    doc, geo = load_text(CLONED, tmp_path)
    m = geo.find_body("2").index
    lo, hi = _extent(geo, m)
    step = np.array([hi[0] - lo[0], 0.0, 0.0])  # pegadas cara con cara, hacia +x... chocan con el clon
    chk = check_copies(geo, Locator(geo), m, step, 2, geo.find_body("4").index, (lo, hi))
    assert not chk.ok and "clon" in chk.message
    step = np.array([0.0, 12.0, 0.0])
    chk = check_copies(geo, Locator(geo), m, step, 3, geo.find_body("4").index, (lo, hi))
    assert chk.ok, chk.message
    labels = duplicate(doc, geo, m, step, 3)
    text, doc2, geo2 = roundtrip(doc)
    assert len(labels) == 3 and all(geo2.find_body(lab).is_module for lab in labels)
    # Cada copia tiene su esfera (material 2) en el sitio trasladado, y material 1 alrededor.
    for k in range(1, 4):
        pts = np.array([[10.0, 12.0 * k, 0], [13.0, 12.0 * k, 0], [10.0, 12.0 * k + 5.5, 0]])
        assert list(materials(geo2, pts)[1]) == [2, 1, 3]
    # El original no cambia; y cada copia se edita por separado.
    assert list(materials(geo2, np.array([[10.0, 0, 0]]))[1]) == [2]
    pts, dirs = random_points(*BOX, 5000)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
    mapping = renumber_map(geo, geo2)
    assert all(mapping[kb] is not None for kb in mapping)


def test_copy_a_body_with_what_is_inside(tmp_path, need_probe):
    path = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
    doc = load_geometry(path)
    geo = compile_geometry(doc)
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 250.0), radius=50.0), 4, geo.find_body("1").index)
    _, doc, geo = roundtrip(doc)
    cube = geo.find_body("1").index
    box = (np.full(3, -6000.0), np.full(3, 6000.0))
    ext = _extent(geo, cube, box)
    step = np.array([0.0, 0.0, -600.0])
    assert check_copies(geo, Locator(geo), cube, step, 1, geo.find_body("2").index, ext).ok
    (lab,) = duplicate(doc, geo, cube, step, 1)
    text, doc, geo = roundtrip(doc)
    copy = find_element(doc, "body", lab)
    inner = [r.label for r in copy.items if not r.is_surface]
    assert len(inner) == 1 and inner[0].strip() not in ("1", "3")
    pts = np.array([[0.0, 0, -350], [0.0, 0, -450], [0.0, 0, 250], [0.0, 0, -50]])
    assert list(materials(geo, pts)[1]) == [4, 1, 4, 0]
    # Declarado en la esfera (su contenedor): la esfera excluye la copia.
    assert any(r.label == lab for r in find_element(doc, "body", "2").items if not r.is_surface)
    pts, dirs = random_points(*box, 4000, seed=3)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_linked_clones(tmp_path, need_probe):
    doc, geo = load_text(CLONED, tmp_path)
    m = geo.find_body("2").index
    labels = duplicate(doc, geo, m, [0.0, -12.0, 0.0], 2, linked=True)
    text, doc2, geo2 = roundtrip(doc)
    assert [type(find_element(doc2, "body", lab)) for lab in labels] == [type(None)] * 2  # no son BODY/MODULE
    clones = [e for e in doc2.elements if type(e).__name__ == "CloneElement"]
    assert len(clones) == 3 and all(c.module_label.strip() == "2" for c in clones)
    for k in (1, 2):
        pts = np.array([[10.0, -12.0 * k, 0], [13.0, -12.0 * k, 0]])
        assert list(materials(geo2, pts)[1]) == [2, 1]
    pts, dirs = random_points(*BOX, 5000, seed=2)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
    # Si cambia el original, cambian todos sus clones (PENGEOM los copia al leer).
    s = find_element(doc2, "surface", "4")
    s.params[0].value = 1.0  # esfera de radio 1 en x
    s.modified = True
    _, _, geo3 = roundtrip(doc2)
    assert list(materials(geo3, np.array([[11.5, -12.0, 0], [11.5, -24.0, 0]]))[1]) == [1, 1]


def test_clone_in_a_rotated_container_lands_where_asked(tmp_path, need_probe):
    """Clonar un módulo que está dentro de otro módulo girado: el clon cae donde se pide."""
    text = ("Clon dentro de un módulo girado\n" + SEP + "\n"
            + _surf("1", (1, 0, 0, 0, -1), xs=2.0) + _surf("2", (0, 1, 0, 0, -1), ys=1.0)
            + _surf("3", (0, 0, 1, 0, -1), zs=1.0) + _surf("4", (1, 1, 1, 0, -1), xs=30.0, ys=30.0, zs=30.0)
            + "MODULE  (   1)   bloque\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n"
              "SURFACE (   2), SIDE POINTER=(-1)\nSURFACE (   3), SIDE POINTER=(-1)\n" + "1" * 64
            + "\nX-SHIFT=(+5.000000000000000E+00,   0)\n" + SEP + "\n"
            + "MODULE  (   2)   esfera girada\nMATERIAL(   2)\nSURFACE (   4), SIDE POINTER=(-1)\nMODULE  (   1)\n"
            + "1" * 64 + "\n  OMEGA=(+1.500000000000000E+01,   0) DEG\n  THETA=(+4.000000000000000E+01,   0) DEG\n"
              "    PHI=(+7.000000000000000E+01,   0) DEG\nX-SHIFT=(+3.000000000000000E+00,   0)\n" + SEP + "\n"
            + END + "\n")
    doc, geo = load_text(text, tmp_path)
    kb = geo.find_body("1").index
    step = np.array([0.0, 0.0, 6.0])  # global
    (lab,) = duplicate(doc, geo, kb, step, 1, linked=True)
    out, doc2, geo2 = roundtrip(doc)
    # El volumen del clon es el del original desplazado 6 cm en z (global).
    pts, dirs = random_points(np.full(3, -12.0), np.full(3, 12.0), 20000, seed=5)
    ib, _ = materials(geo, pts)
    orig = pts[ib == kb]
    k2 = geo2.find_body(lab).index
    ib2, _ = materials(geo2, orig + step)
    assert np.all(ib2 == k2)
    assert_pengeom_agrees(out, tmp_path, pts, dirs)
    # Lo mismo con una copia independiente.
    doc, geo = load_text(text, tmp_path, "b.geo")
    (lab,) = duplicate(doc, geo, geo.find_body("1").index, step, 1)
    out, doc2, geo2 = roundtrip(doc)
    ib2, _ = materials(geo2, orig + step)
    assert np.all(ib2 == geo2.find_body(lab).index)
    assert_pengeom_agrees(out, tmp_path, pts, dirs, name="copia.geo")
    t = clone_transform(doc, geo, geo.find_body("1").index, pl.translation(step), geo.find_body("2").index)
    assert np.allclose(t[0], np.eye(3))


def test_materialize_a_clone_keeps_everything(tmp_path, need_probe):
    doc, geo = load_text(CLONED, tmp_path)
    pts, dirs = random_points(*BOX, 8000, seed=9)
    before = materials(geo, pts, dirs)[1]
    k = geo.find_body("3").index
    notes, key_map = materialize_clone(doc, geo, k)
    text, doc2, geo2 = roundtrip(doc)
    assert not any(type(e).__name__ == "CloneElement" for e in doc2.elements)
    assert geo2.find_body("3").is_module and geo2.find_body("3").origin == "user"
    assert np.array_equal(materials(geo2, pts, dirs)[1], before)
    mapping = renumber_map(geo, geo2, key_map=key_map)
    assert all(v is not None for v in mapping.values())
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_rotated_clone_materializes_in_place(tmp_path, need_probe):
    doc, geo = load_text(CLONED, tmp_path)
    k = geo.find_body("3").index
    ext = _extent(geo, k)
    pl.move_element(doc, geo, k, pl.rotation_about([0, 0, 1], 90.0, pl.element_center(ext)))
    _, doc, geo = roundtrip(doc)
    pts, dirs = random_points(*BOX, 8000, seed=10)
    before = materials(geo, pts, dirs)[1]
    materialize_clone(doc, geo, geo.find_body("3").index)
    text, doc2, geo2 = roundtrip(doc)
    assert np.array_equal(materials(geo2, pts, dirs)[1], before)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_duplicate_refuses_what_it_cannot_copy(tmp_path):
    doc, geo = load_text(CLONED, tmp_path)
    with pytest.raises(EditError, match="desplazad"):
        duplicate(doc, geo, geo.find_body("2").index, [0.0, 0, 0], 1)
    with pytest.raises(EditError, match="clon"):
        duplicate(doc, geo, geo.find_body("4").index, [200.0, 0, 0], 1)  # el mundo contiene un clon
    with pytest.raises(EditError, match="copias"):
        duplicate(doc, geo, geo.find_body("2").index, [0.0, 20, 0], 0)
    # Una lámina (planos ∩ el cuerpo que la contiene) es un volumen cerrado: su copia es otra lámina.
    path = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
    d = load_geometry(path)
    g = compile_geometry(d)
    add_primitive(d, g, Primitive("slab", center=(0.0, 0, 100.0), height=10.0), 3, g.find_body("1").index)
    _, d, g = roundtrip(d)
    (lab,) = duplicate(d, g, g.find_body("3").index, [0.0, 0, 50.0], 1)
    _, d, g = roundtrip(d)
    pts = np.array([[0.0, 0, 150.0], [0.0, 0, 160.0], [0.0, 0, 100.0]])
    assert list(materials(g, pts)[0]) == [g.find_body(lab).index, g.find_body("1").index, g.find_body("3").index]
    # Un cuerpo abierto (sus superficies no lo acotan) no.
    open_text = ("abierto\n" + SEP + "\n" + _surf("1", (0, 0, 0, 1, 0)) + _surf("2", (1, 1, 1, 0, -1), xs=9.0,
                 ys=9.0, zs=9.0)
                 + "BODY    (   1)   semiespacio\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + SEP + "\n"
                 + END + "\n")
    d, g = load_text(open_text, tmp_path, "abierto.geo")
    with pytest.raises(EditError, match="cerrado"):
        duplicate(d, g, g.find_body("1").index, [0.0, 0, 50.0], 1)


def test_convert_then_duplicate_then_clone_chain(tmp_path, need_probe):
    """Cadena de ediciones típica: convertir en módulo, copiar, clonar la copia, mover y girar el clon."""
    path = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
    doc = load_geometry(path)
    geo = compile_geometry(doc)
    convert_to_module(doc, geo, geo.find_body("1").index)
    _, doc, geo = roundtrip(doc)
    (c1,) = duplicate(doc, geo, geo.find_body("1").index, [600.0, 0, 0], 1)
    _, doc, geo = roundtrip(doc)
    (k1,) = duplicate(doc, geo, geo.find_body(c1).index, [0.0, 600, 0], 1, linked=True)
    _, doc, geo = roundtrip(doc)
    kb = geo.find_body(k1).index
    box = (np.full(3, -6000.0), np.full(3, 6000.0))
    ext = _extent(geo, kb, box)
    motion = pl.rotation_about([1, 0, 0], 30.0, pl.element_center(ext))
    assert pl.check_motion(geo, Locator(geo), kb, motion, ext).ok
    pl.move_element(doc, geo, kb, motion)
    text, doc, geo = roundtrip(doc)
    pts, dirs = random_points(np.full(3, -1500.0), np.full(3, 1500.0), 6000, seed=12)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
