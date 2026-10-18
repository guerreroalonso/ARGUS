"""Fallos encontrados en la revisión de la 0.7 (editor de geometría): detectores que se quedan sin cuerpos,
cuerpos limitantes que asoman al duplicar o envolver, superficies compartidas que cambian otro cuerpo y
láminas finas que el muestreo no ve."""

import numpy as np
import pytest

from argus.pengeom import surfaces as sf
from argus.pengeom.bounds import estimate_bounds
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.duplicate import check_copies
from argus.pengeom.edit import EditError, Primitive, check_placement, compile_checked, delete_body, reparse
from argus.pengeom.extent import element_extent
from argus.pengeom.identity import renumber_map
from argus.pengeom.locate import Locator
from argus.pengeom.overlap import guard
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.wrap import check_wrap, fitted_primitive, sample_solids
from argus.pengeom.writer import write_geo_text
from argus.penhan.follow import follow_geometry
from argus.penhan.inputfile import parse_in_file
from argus.render.raytrace import RayTracer
from conftest import CORPUS

Z = "0" * 64 + "\n"


def _s(label, idx, params, desc=""):
    lines = [f"SURFACE ({label:>4})   {desc}".rstrip(), f"INDICES=({idx})"]
    lines += [f"{k:>7}=({v:+.15E},   0)" for k, v in params]
    return "\n".join(lines) + "\n" + Z


def _b(label, mat, items, kw="BODY", desc=""):
    return f"{kw:<8}({label:>4})   {desc}".rstrip() + f"\nMATERIAL({mat:4d})\n" + "".join(items) + Z


def _side(label, side):
    return f"SURFACE ({label:>4}), SIDE POINTER=({side:+d})\n"


def _in(label, kw="BODY"):
    return f"{kw:<8}({label:>4})\n"


END = "END      " + "0" * 55 + "\n"
X = " 1, 0, 0, 0,-1"
Y = " 0, 1, 0, 0,-1"
ZZ = " 0, 0, 1, 0,-1"
SPH = " 1, 1, 1, 0,-1"

# Módulo M con las cajas B (x en [-10, 0]) y U (x en [0, 10]); la esfera L (r = 3) las recorta a las dos
# (asoma por los dos lados), y una caja V aparte (x en [0, 10], y en [20, 40]).
SHARED = ("Esfera limitante compartida por dos cajas\n" + Z
          + _s("1", X, [("X-SCALE", 5.0), ("X-SHIFT", -5.0)]) + _s("2", X, [("X-SCALE", 5.0), ("X-SHIFT", 5.0)])
          + _s("3", Y, [("Y-SCALE", 10.0)]) + _s("4", ZZ, [("Z-SCALE", 10.0)])
          + _s("5", SPH, [("X-SCALE", 3.0), ("Y-SCALE", 3.0), ("Z-SCALE", 3.0)])
          + _s("6", Y, [("Y-SCALE", 10.0), ("Y-SHIFT", 30.0)])
          + _s("7", X, [("X-SCALE", 50.0)]) + _s("8", Y, [("Y-SCALE", 50.0)]) + _s("9", ZZ, [("Z-SCALE", 50.0)])
          + _b("1", 3, [_side("5", -1)], desc="L")
          + _b("2", 1, [_side("1", -1), _side("3", -1), _side("4", -1), _in("1")], desc="B")
          + _b("3", 2, [_side("2", -1), _side("3", -1), _side("4", -1), _in("1")], desc="U")
          + _b("4", 5, [_side("2", -1), _side("6", -1), _side("4", -1)], desc="V")
          + _b("5", 4, [_side("7", -1), _side("8", -1), _side("9", -1), _in("1"), _in("2"), _in("3"), _in("4")],
               kw="MODULE", desc="M")
          + END)

FOIL = ("Caja de agua de 30 cm con una lámina de oro de 1 um\n" + Z
        + _s("1", X, [("X-SCALE", 15.0)]) + _s("2", Y, [("Y-SCALE", 15.0)]) + _s("3", ZZ, [("Z-SCALE", 15.0)])
        + _s("4", ZZ, [("Z-SCALE", 5e-5)])
        + _b("1", 2, [_side("1", -1), _side("2", -1), _side("4", -1)], desc="lamina")
        + _b("2", 1, [_side("1", -1), _side("2", -1), _side("3", -1), _in("1")], desc="agua")
        + END)


def _load(text):
    doc = parse_geo_text(text, "g.geo")
    geo = compile_geometry(doc)
    assert geo.ok, [str(d) for d in geo.diagnostics]
    loc = Locator(geo)
    return doc, geo, loc, estimate_bounds(geo, locator=loc)


def test_deleting_the_only_body_of_a_detector_removes_the_detector():
    """Si no, el .in queda con un detector sin cuerpos y penhmain se detiene (3101)."""
    stack = CORPUS / "penhan/stack"
    parinp = [0.002, 0.004, 0.008, 0.008]
    doc = load_geometry(stack / "3discs.geo")
    geo = compile_geometry(doc, parinp=parinp, base_dir=stack)
    indoc = parse_in_file(stack / "stack.in")
    n_det = sum(1 for ln in indoc.lines if ln.kw == "ENDETC")
    delete_body(doc, geo, geo.find_body("1").index)
    geo2 = compile_checked(reparse(write_geo_text(doc), doc), parinp, stack)
    m = renumber_map(geo, geo2)
    notes = follow_geometry(indoc, m, {}, {geo.balias(k) for k, v in m.items() if v is None})
    assert any("quitado el detector de energía depositada 1" in n for n in notes)
    assert sum(1 for ln in indoc.lines if ln.kw == "ENDETC") == n_det - 1
    from argus.penhan import sections as sc

    assert all(d.bodies for d in sc.energy_detectors(indoc))


def test_duplicating_a_body_whose_limiting_body_protrudes():
    doc, geo, loc, bounds = _load(SHARED)
    b = geo.find_body("2").index
    ext = element_extent(geo, RayTracer(geo), b, bounds)
    chk = check_copies(geo, loc, b, (0.0, 30.0, 0.0), 1, geo.find_body("5").index, ext)
    assert not chk.ok and "«4»" in chk.message  # la copia de L asomaría dentro de V


def test_wrapping_a_body_whose_limiting_body_protrudes():
    doc, geo, loc, bounds = _load(SHARED)
    b = geo.find_body("2").index
    ext = [element_extent(geo, RayTracer(geo), b, bounds)]
    pts = sample_solids(geo, loc, [b], ext, n=4000)
    prim = fitted_primitive(ext, "box", points=pts)
    assert not check_wrap(geo, loc, [b], prim, geo.find_body("5").index, ext, bounds).ok


SHARED_SURFACE = """Losa X (|z|<1) en el nivel superior y un módulo M girado en x=100 con una bola B
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)   planos z = +-1 (losa X)
INDICES=( 0, 0, 1, 0,-1)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   2)   planos x = +-50
INDICES=( 1, 0, 0, 0,-1)
X-SCALE=(+5.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   3)   planos y = +-50
INDICES=( 0, 1, 0, 0,-1)
Y-SCALE=(+5.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)   X
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   4)   bola r=5
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+5.000000000000000E+00,   0)
Y-SCALE=(+5.000000000000000E+00,   0)
Z-SCALE=(+5.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   5)   esfera del módulo r=10
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=(+1.000000000000000E+01,   0)
Y-SCALE=(+1.000000000000000E+01,   0)
Z-SCALE=(+1.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   2)   B
MATERIAL(   2)
SURFACE (   4), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
MODULE  (   3)   M: girado 90 grados alrededor de y, en x=100
MATERIAL(   3)
SURFACE (   5), SIDE POINTER=(-1)
BODY    (   2)
1111111111111111111111111111111111111111111111111111111111111111
  THETA=(+9.000000000000000E+01,   0) DEG
X-SHIFT=(+1.000000000000000E+02,   0)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def test_an_edit_that_changes_other_bodies_is_refused():
    """Añadir a B (dentro de un módulo girado) una superficie que usa X: PENGEOM la gira con el módulo y X
    cambia. La comprobación de solapes no lo ve (no hay solapes nuevos): hace falta la de localidad."""
    doc, geo, loc, bounds = _load(SHARED_SURFACE)
    b = geo.find_body("2").index
    work = reparse(write_geo_text(doc), doc)
    sf.add_surface(work, compile_checked(work), b, "1", -1)
    new = compile_checked(reparse(write_geo_text(work), work))
    p = np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.5]])
    assert list(Locator(new).locate(p)[0]) != list(loc.locate(p)[0])  # X ha cambiado
    with pytest.raises(EditError, match="otros cuerpos"):
        guard(bounds, [geo.balias(b)])(geo, new)
    # Un cambio local sí se acepta: B con su propia superficie nueva.
    work = reparse(write_geo_text(doc), doc)
    wgeo = compile_checked(work)
    new_s = sf.new_plane(work, wgeo, b, np.array([100.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    sf.add_surface(work, wgeo, b, new_s, -1)
    guard(bounds, [geo.balias(b)])(geo, compile_checked(reparse(write_geo_text(work), work)))


def test_a_body_across_a_thin_foil_is_refused():
    """20 000 puntos al azar casi nunca caen en una lámina de 1 um; un rayo que la cruza, siempre."""
    doc, geo, loc, bounds = _load(FOIL)
    water = geo.find_body("agua").index if geo.find_body("agua") else geo.find_body("2").index
    chk = check_placement(geo, loc, Primitive("sphere", center=(1.0, -2.0, -3.5), radius=4.0), water, bounds)
    assert not chk.ok and "fino" in chk.message
    ok = check_placement(geo, loc, Primitive("sphere", center=(1.0, -2.0, -5.0), radius=4.0), water, bounds)
    assert ok.ok


@pytest.mark.oracle
def test_thin_foil_overlap_is_real_for_pengeom(tmp_path):
    """Lo que se rechaza: PENGEOM, con esa esfera añadida, atraviesa la lámina sin verla."""
    from argus.oracle.probe import find_probe, run_probe
    from argus.pengeom.edit import add_primitive

    if find_probe() is None:
        pytest.skip("Sonda PENGEOM no compilada")
    doc, geo, loc, bounds = _load(FOIL)
    add_primitive(doc, geo, Primitive("sphere", center=(1.0, -2.0, -3.5), radius=4.0), 3, geo.find_body("2").index)
    p = tmp_path / "lamina.geo"
    p.write_text(write_geo_text(doc), encoding="latin-1")
    r = run_probe(p, tables=False, rays=np.array([[1.0, -2.0, -1.0, 0.0, 0.0, 1.0]]))
    mats = [int(m) for m in r.rays[0].segments["mat"]]
    assert 2 not in mats  # el oro de la lámina no aparece: la esfera la tapa
