"""Fallos de la cuarta revisión del núcleo de PENGEOM (0.10): la esfera de la envolvente que desborda la lista de
superficies de un elemento del nivel superior, paredes de un módulo en el mismo sitio que la cara de una hija (STEP
las confunde), superficies ya escritas que se añaden dentro de un módulo movido, campos numéricos como gfortran y
etiquetas."""

from __future__ import annotations

import numpy as np
import pytest

from argus.core.fortran_io import FortranReadError, output_float, parse_int, parse_real
from argus.pengeom import edit as ge
from argus.pengeom import surfaces as sf
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.locate import Locator
from argus.pengeom.newgeo import new_geometry
from argus.pengeom.parser import parse_geo_text
from argus.pengeom.validate import coincident_walls, new_overflows, table_overflows, validate
from argus.pengeom.wrap import fitted_primitive, wrap
from argus.pengeom.writer import write_geo_text
from geo_oracle import assert_pengeom_agrees, random_points, roundtrip, step_disagreements

SEP = "0" * 64
END = "END      " + "0" * 55


def _world(size: float = 40.0):
    doc = new_geometry("prueba", ge.Primitive("box", center=(0.0, 0.0, 0.0), sides=(size, size, size)), 1)
    return doc, ge.compile_checked(doc)


# ---------------------------------------------------------------------------------------------------------------------
# La esfera de la envolvente: 249 superficies en un elemento del nivel superior son 250
# ---------------------------------------------------------------------------------------------------------------------

def _many_planes(ns: int) -> str:
    out = ["muchos planos", SEP]
    for i in range(ns):
        out += [f"SURFACE ({i + 1:4d})   plano z=-{i + 1}", "INDICES=( 0, 0, 0, 1, 0)",
                f"Z-SHIFT=({-float(i + 1):22.15E},   0)", SEP]
    out += ["SURFACE (SPH )   esfera", "INDICES=( 1, 1, 1, 0,-1)", "X-SCALE=( 5.000000000000000E+00,   0)",
            "Y-SCALE=( 5.000000000000000E+00,   0)", "Z-SCALE=( 5.000000000000000E+00,   0)", SEP,
            "SURFACE (SPB )   esfera B", "INDICES=( 1, 1, 1, 0,-1)", "X-SHIFT=( 2.000000000000000E+01,   0)", SEP,
            "BODY    (   A)   muchos", "MATERIAL(   1)"]
    out += [f"SURFACE ({i + 1:4d}), SIDE POINTER=( 1)" for i in range(ns)]
    out += ["SURFACE (SPH ), SIDE POINTER=(-1)", SEP,
            "MODULE  (   M)   modulo", "MATERIAL(   0)", "SURFACE (SPH ), SIDE POINTER=(-1)", "BODY    (   A)", SEP,
            "BODY    (   B)   esfera", "MATERIAL(   2)", "SURFACE (SPB ), SIDE POINTER=(-1)", SEP, END, ""]
    return "\n".join(out)


def test_the_enclosure_sphere_overflowing_a_top_level_module_is_an_error(tmp_path):
    """GEOMIN añade la esfera de la envolvente a cada elemento sin madre sin comprobar NXG: con 249 superficies
    (las del módulo y las propias de su hija) la lista pasa a 250, se desborda y LOCATE falla sin ningún error."""
    ok = compile_geometry(parse_geo_text(_many_planes(247), "a.geo"))
    bad = compile_geometry(parse_geo_text(_many_planes(248), "b.geo"))
    assert ok.ok and bad.ok
    assert not table_overflows(ok)
    msgs = [d.message for d in validate(bad).errors]
    assert any("esfera de la envolvente" in m and "KSURF" in m for m in msgs), msgs
    assert new_overflows(ok, bad) and not new_overflows(bad, bad)


# ---------------------------------------------------------------------------------------------------------------------
# Paredes de módulo sobre caras de hijas
# ---------------------------------------------------------------------------------------------------------------------

def _flush_daughter():
    """Una caja dentro de un módulo, pegada a su pared +x (x = 5) con otra superficie."""
    doc, geo = _world()
    mod = ge.add_primitive(doc, geo, ge.Primitive("box", center=(0, 0, 0), sides=(10, 6, 6)), 2, container=1,
                           module=True)
    _t, doc, geo = roundtrip(doc)
    ge.add_primitive(doc, geo, ge.Primitive("box", center=(3.0, 0, 0), sides=(4, 4, 4)), 3,
                     container=geo.find_body(mod).index)
    return roundtrip(doc)


def _rays(n=300, seed=0, lo=(-8, -2, -2), hi=(8, 2, 2)):
    rng = np.random.default_rng(seed)
    lo, hi = np.array(lo, float), np.array(hi, float)
    starts, dirs = [], []
    for _ in range(n):
        d = rng.normal(size=3)
        d[0] = abs(d[0]) * 3
        d /= np.linalg.norm(d)
        p = lo + rng.random(3) * (hi - lo)
        p[0] = -9.0
        starts.append(p)
        dirs.append(d)
    return np.array(starts), np.array(dirs), 25.0


def test_a_module_wall_on_the_face_of_a_daughter_is_warned():
    _text, _doc, geo = _flush_daughter()
    warned = coincident_walls(geo)
    assert len(warned) == 1 and "mismo sitio" in warned[0][0]
    assert any("mismo sitio" in d.message for d in validate(geo) if d.severity.name == "WARNING")


@pytest.mark.oracle
def test_that_is_what_step_gets_wrong(tmp_path, need_probe):
    """Por qué se avisa: con la hija pegada a la pared, STEP de PENGEOM deja partículas con el material del módulo
    fuera de él (varios cm); separada 1e-5 cm, no."""
    text, _doc, _geo = _flush_daughter()
    bad, worst = step_disagreements(text, tmp_path, *_rays())
    assert bad > 0 and worst > 1.0
    doc, geo = _world()
    mod = ge.add_primitive(doc, geo, ge.Primitive("box", center=(0, 0, 0), sides=(10, 6, 6)), 2, container=1,
                           module=True)
    _t, doc, geo = roundtrip(doc)
    ge.add_primitive(doc, geo, ge.Primitive("box", center=(3.0 - 1e-5, 0, 0), sides=(4, 4, 4)), 3,
                     container=geo.find_body(mod).index)
    text, _doc, geo = roundtrip(doc)
    assert not coincident_walls(geo)
    assert step_disagreements(text, tmp_path, *_rays()) == (0, 0.0)


def _two_boxes():
    doc, geo = _world()
    ge.add_primitive(doc, geo, ge.Primitive("box", center=(-2.5, 0, 0), sides=(5, 4, 4)), 2, container=1)
    _t, doc, geo = roundtrip(doc)
    ge.add_primitive(doc, geo, ge.Primitive("box", center=(2.5, 0.5, 0), sides=(5, 3, 3)), 3,
                     container=geo.find_body("1").index)
    return roundtrip(doc)


def test_wrapping_without_margin_leaves_a_minimum_clearance(tmp_path):
    _t, doc, geo = _two_boxes()
    kbs = [b.index for b in geo.bodies if b.material in (2, 3)]
    exts = [(np.array([-5, -2, -2.0]), np.array([0, 2, 2.0])), (np.array([0, -1, -1.5]), np.array([5, 2, 1.5]))]
    prim = fitted_primitive(exts, "box", 0.0)
    lo, hi = prim.box()
    assert np.allclose(lo, [-5, -2, -2], atol=2e-5) and np.all(lo < [-5, -2, -2])
    wrap(doc, geo, kbs, prim, material=4)
    text, _doc, geo = roundtrip(doc)
    assert not coincident_walls(geo)
    from argus.oracle.probe import find_probe

    if find_probe() is not None:
        assert step_disagreements(text, tmp_path, *_rays(lo=(-8, -2.5, -2.5), hi=(8, 2.5, 2.5))) == (0, 0.0)


def test_the_corpus_has_no_coincident_walls():
    from argus.pengeom.parser import load_geometry
    from conftest import CORPUS

    for p in sorted(CORPUS.rglob("*.geo")):
        geo = compile_geometry(load_geometry(p), base_dir=p.parent)
        if geo.ok:
            assert not coincident_walls(geo), p


# ---------------------------------------------------------------------------------------------------------------------
# Añadir una superficie ya escrita a un cuerpo de un módulo movido
# ---------------------------------------------------------------------------------------------------------------------

UNUSED_PLANE = "\n".join([
    "caja en un módulo desplazado y un plano z=0 sin usar", SEP,
    "SURFACE (   1)   esfera del módulo", "INDICES=( 1, 1, 1, 0,-1)", "X-SCALE=( 2.000000000000000E+01,   0)",
    "Y-SCALE=( 2.000000000000000E+01,   0)", "Z-SCALE=( 2.000000000000000E+01,   0)", SEP,
    "SURFACE (   2)", "INDICES=( 1, 0, 0, 0,-1)", "X-SCALE=( 5.000000000000000E+00,   0)", SEP,
    "SURFACE (   3)", "INDICES=( 0, 1, 0, 0,-1)", "Y-SCALE=( 5.000000000000000E+00,   0)", SEP,
    "SURFACE (   4)", "INDICES=( 0, 0, 1, 0,-1)", "Z-SCALE=( 5.000000000000000E+00,   0)", SEP,
    "SURFACE (   9)   plano z=0 (sin usar)", "INDICES=( 0, 0, 0, 1, 0)", SEP,
    "BODY    (   1)   caja", "MATERIAL(   2)", "SURFACE (   2), SIDE POINTER=(-1)",
    "SURFACE (   3), SIDE POINTER=(-1)", "SURFACE (   4), SIDE POINTER=(-1)", SEP,
    "MODULE  (   2)   módulo desplazado 3 cm hacia arriba", "MATERIAL(   1)", "SURFACE (   1), SIDE POINTER=(-1)",
    "BODY    (   1)", "1" * 64, "  OMEGA=( 3.000000000000000E+01,   0) DEG",
    "Z-SHIFT=( 3.000000000000000E+00,   0)", SEP, END, ""])


def test_an_unused_surface_is_added_where_it_is_seen(tmp_path, need_probe):
    """GEOMIN mueve con el módulo las superficies de lo que tiene dentro: el plano z = 0 (sin usar, donde está
    escrito) se reescribe en el sistema del módulo para que corte la caja en z = 0, como se veía."""
    doc = parse_geo_text(UNUSED_PLANE, str(tmp_path / "g.geo"))
    geo = compile_geometry(doc)
    kb = geo.find_body("1").index
    sf.add_surface(doc, geo, kb, "9", -1, as_seen=True)
    text, _doc, new = roundtrip(doc)
    pts = np.array([[0.3, 0.2, z] for z in (-1.5, -0.5, 0.5, 1.5, 2.5, 3.5, 7.0, 9.0)])
    _ib, mat = Locator(new).locate(pts)
    assert mat.tolist() == [2, 2, 1, 1, 1, 1, 1, 1]  # la caja (z de -2 a 8) cortada en z = 0
    pts, dirs = random_points((-8, -8, -4), (8, 8, 10), 3000)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
    # Sin as_seen (un plano nuevo, ya escrito en el sistema del cuerpo), la superficie no se toca.
    doc = parse_geo_text(UNUSED_PLANE, str(tmp_path / "g.geo"))
    geo = compile_geometry(doc)
    sf.add_surface(doc, geo, geo.find_body("1").index, "9", -1)
    assert "SURFACE (   9)   plano z=0 (sin usar)\nINDICES=( 0, 0, 0, 1, 0)\n" + SEP in write_geo_text(doc)


# ---------------------------------------------------------------------------------------------------------------------
# Campos numéricos y etiquetas
# ---------------------------------------------------------------------------------------------------------------------

def test_numeric_fields_are_read_like_gfortran():
    for field in (" +", "   -", "-"):
        with pytest.raises(FortranReadError):
            parse_int(field)
    assert parse_int("+  ") == 0 and parse_int("  -3") == -3 and parse_int("    ") == 0
    for field in (".", "-.", "+.", "E5", ".E5", "--1", "+-1"):
        assert parse_real(field) == 0.0, field
    for field in ("E", "1E", "1..0", "1.0E2.0"):
        with pytest.raises(FortranReadError):
            parse_real(field)
    assert output_float("-2.0000000000000000+160") == -2e160 and output_float("1.0-300") == 1e-300


def test_labels_with_other_digits_and_the_last_label():
    text = "\n".join(["etiquetas", SEP, "SURFACE (   1)", "INDICES=( 1, 1, 1, 0,-1)", SEP,
                      "BODY    (   ²)   esfera", "MATERIAL(   1)", "SURFACE (   1), SIDE POINTER=(-1)", SEP,
                      "BODY    (9999)   fuera", "MATERIAL(   2)", "BODY    (   ²)", SEP, END, ""])
    doc = parse_geo_text(text, "e.geo")
    geo = compile_geometry(doc)
    assert geo.ok
    assert ge.new_label(doc, "body", geo).strip() == "1"  # «²» no es un número y 9999 ya no deja sitio arriba
    assert ge.new_label(doc, "surface", geo).strip() == "2"
