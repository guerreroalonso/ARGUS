"""Fallos de la tercera revisión del editor de geometría (0.8.1): superficies compartidas al mover módulos,
clones que se mueven con su original, cuerpos que rodean a otros, láminas finas, limitantes sin cota
analítica, módulo raíz sin envolvente, desbordamientos, MATERIAL(9999) sin CT y lo que no se puede eliminar."""

import numpy as np
import pytest

from argus.pengeom import placement as pl
from argus.pengeom import surfaces as sf
from argus.pengeom.bounds import estimate_bounds
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.duplicate import check_copies, duplicate
from argus.pengeom.edit import (
    EditError,
    Primitive,
    add_primitive,
    check_convert,
    check_move,
    check_placement,
    compile_checked,
    convert_to_module,
    delete_body,
    reparse,
    top_level,
)
from argus.pengeom.extent import element_extent, members_box
from argus.pengeom.identity import renumber_map
from argus.pengeom.locate import Locator
from argus.pengeom.overlap import guard
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.validate import new_overflows, table_overflows, validate
from argus.pengeom.wrap import check_wrap, fitted_primitive, sample_solids, wrap, wrap_candidates
from argus.pengeom.writer import write_geo_text
from argus.render.raytrace import RayTracer
from conftest import CORPUS
from geo_oracle import assert_pengeom_agrees, random_points
from test_duplicate import CLONED
from test_modules import NESTED
from test_review_editor import END, FOIL, SHARED, ZZ, X, Y, Z, _b, _in, _s, _side

STACK = CORPUS / "penhan/stack"
STACK_PARINP = [0.002, 0.004, 0.008, 0.008]
CUBO = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
PRUEBA = CORPUS / "usuario/PruebasNuevaGeo/prueba.geo"


def _load(text, name="g.geo", parinp=None):
    doc = parse_geo_text(text, name)
    geo = compile_checked(doc, parinp)
    loc = Locator(geo)
    return doc, geo, loc, estimate_bounds(geo, locator=loc)


def _step(doc, parinp=None):
    """(texto, documento releído, geometría) tras un cambio."""
    text = write_geo_text(doc)
    doc2 = reparse(text, doc)
    return text, doc2, compile_checked(doc2, parinp)


def _where(geo, pts, dirs=None):
    ib, mat = Locator(geo).locate(np.asarray(pts, dtype=float), dirs)
    return [geo.balias(int(k)).strip() if k <= geo.nbodys else "fuera" for k in ib], list(mat)


# ---------------------------------------------------------------------------------------------------------------
# 1. Mover un módulo no arrastra las superficies que comparte con cuerpos de fuera
# ---------------------------------------------------------------------------------------------------------------

def _3discs():
    doc = load_geometry(STACK / "3discs.geo")
    return doc, compile_geometry(doc, parinp=STACK_PARINP, base_dir=STACK)


def test_shared_surfaces_are_found():
    doc, geo = _3discs()
    m2, m3 = geo.find_body("2").index, geo.find_body("3").index
    shared = pl.shared_surfaces(geo, m2)
    assert set(shared) == {1, 5}  # el plano z = 0 y el cilindro, que también limitan al módulo «3»
    assert all(m3 in users for users in shared.values())
    assert not pl.shared_surfaces(geo, m3)  # el de arriba no comparte nada con nadie de fuera


def test_moving_a_module_keeps_the_planes_it_shares(tmp_path, need_probe):
    """3discs: subir 10 µm el módulo «2»; el hueco z en [0, 0.001] tiene que ser del módulo «3» (antes, vacío)."""
    doc, geo = _3discs()
    m2 = geo.find_body("2").index
    zs = np.array([0.0005, 0.0015, 0.0035, 0.0045, 0.0055, 0.0085])
    pts = np.column_stack([np.zeros_like(zs), np.zeros_like(zs), zs])
    assert _where(geo, pts)[0] == ["1", "1", "2", "3", "3", "fuera"]
    pl.move_element(doc, geo, m2, pl.translation([0.0, 0.0, 0.001]), STACK_PARINP)
    text, _doc2, geo2 = _step(doc, STACK_PARINP)
    names, mats = _where(geo2, pts)
    assert names == ["3", "1", "2", "2", "3", "fuera"]
    assert mats[:5] == [1] * 5
    dirs = np.tile([0.0, 0.0, 1.0], (len(pts), 1))
    p2, d2 = random_points([-0.01] * 3, [0.01, 0.01, 0.012], 2000, seed=1)
    assert_pengeom_agrees(text, tmp_path, np.vstack([pts, p2]), np.vstack([dirs, d2]), parinp=STACK_PARINP)


def test_separating_the_surfaces_alone_changes_nothing(tmp_path, need_probe):
    """Copias propias con los mismos coeficientes: GEOMIN las funde y LOCATE da lo mismo que antes."""
    doc, geo = _3discs()
    notes = pl.separate_surfaces(doc, geo, geo.find_body("2").index)
    assert notes and "«1»" in notes[0]
    text, _doc2, geo2 = _step(doc, STACK_PARINP)
    assert not pl.shared_surfaces(geo2, geo2.find_body("2").index)
    pts, dirs = random_points([-0.01] * 3, [0.01, 0.01, 0.01], 4000, seed=3)
    assert np.array_equal(Locator(geo2).locate(pts, dirs)[0], Locator(geo).locate(pts, dirs)[0])
    assert_pengeom_agrees(text, tmp_path, pts, dirs, parinp=STACK_PARINP)


def test_moving_a_slab_turned_module_does_not_move_the_cube():
    """cubo-esfera: una lámina del cubo convertida en módulo y subida 50: el cubo no se mueve."""
    doc = load_geometry(CUBO)
    geo = compile_geometry(doc)
    cube = geo.find_body("1").index
    lab = add_primitive(doc, geo, Primitive("slab", center=(0.0, 0.0, 150.0), height=100.0, axis="z"), 1, cube)
    _, doc, geo = _step(doc)
    convert_to_module(doc, geo, geo.find_body(lab).index)
    _, doc, geo = _step(doc)
    kb = geo.find_body(lab).index
    probe = np.array([[0, 0, 25], [0, 0, 150], [0, 0, 400], [0, 0, 525], [100, 100, 525]], dtype=float)
    before = _where(geo, probe)
    loc = Locator(geo)
    motion = pl.translation([0.0, 0.0, 50.0])
    assert pl.check_motion(geo, loc, kb, motion, element_extent(geo, RayTracer(geo), kb, estimate_bounds(geo))).ok
    pl.move_element(doc, geo, kb, motion)
    _, doc, geo2 = _step(doc)
    after = _where(geo2, probe)
    assert after[1][0] == 1 and after[0][0] == "1"  # z = 25: sigue siendo el cubo
    assert after[0][3:] == before[0][3:] and after[1][3:] == before[1][3:]  # por encima del cubo, lo mismo
    assert _where(geo2, [[0, 0, 225]])[0] == [lab.strip()]  # la lámina, 50 cm más arriba


def test_moving_a_wrapped_box_does_not_move_its_neighbours():
    """SHARED: envolver V (comparte sus planos z con B y U) y moverlo 5 cm: B y U no cambian."""
    doc, geo, loc, bounds = _load(SHARED)
    v = geo.find_body("4").index
    cont, _cands = wrap_candidates(geo, v)
    ext = [element_extent(geo, RayTracer(geo), v, bounds)]
    prim = fitted_primitive(ext, "box", points=sample_solids(geo, loc, [v], ext))
    mlab = wrap(doc, geo, [v], prim)
    _, doc, geo = _step(doc)
    kb = geo.find_body(mlab).index
    pts = np.array([[-5.0, 0.0, -8.0], [5.0, 0.0, -8.0], [-5.0, 0.0, 12.0], [5.0, 0.0, 12.0], [5.0, 30.0, 0.0],
                    [5.0, 30.0, 12.0]])
    before = _where(geo, pts)
    pl.move_element(doc, geo, kb, pl.translation([0.0, 0.0, 5.0]))
    _, doc, geo2 = _step(doc)
    after = _where(geo2, pts)
    assert after[0][:4] == before[0][:4] and after[1][:4] == before[1][:4]  # B, U y M no cambian
    assert after[0][5] == "4" and after[1][5] == 5  # V, 5 cm más arriba


def test_dragging_a_module_that_shares_surfaces_is_refused():
    """El arrastre en vivo solo cambia la transformación: si comparte superficies, se dice por qué no."""
    doc, geo = _3discs()
    with pytest.raises(EditError, match="comparte"):
        pl.moved_values(geo, geo.find_body("2").index, pl.translation([0.0, 0.0, 0.001]), STACK_PARINP)
    # Tras darle superficies propias, sí.
    pl.separate_surfaces(doc, geo, geo.find_body("2").index)
    _, doc, geo = _step(doc, STACK_PARINP)
    assert pl.moved_values(geo, geo.find_body("2").index, pl.translation([0.0, 0.0, 0.001]), STACK_PARINP)


def test_placement_panel_does_not_reshape_other_bodies():
    """Panel «Colocación»: cambiar Z-SHIFT de un módulo que comparte planos se rechaza (antes lo aceptaba);
    con ``set_placement`` (superficies propias) sí se puede."""
    doc, geo = _3discs()
    bounds = (np.full(3, -0.02), np.full(3, 0.02))
    work = reparse(write_geo_text(doc), doc)
    wgeo = compile_checked(work, STACK_PARINP)
    pl.set_transform(pl.placeable(wgeo, wgeo.find_body("2").index), {"Z-SHIFT": 0.001})
    new = compile_checked(reparse(write_geo_text(work), work), STACK_PARINP)
    with pytest.raises(EditError, match="otros cuerpos"):
        guard(bounds, ["   2"])(geo, new)
    work = reparse(write_geo_text(doc), doc)
    wgeo = compile_checked(work, STACK_PARINP)
    pl.set_placement(work, wgeo, wgeo.find_body("2").index, {"Z-SHIFT": 0.001})
    new = compile_checked(reparse(write_geo_text(work), work), STACK_PARINP)
    guard(bounds, ["   2"])(geo, new)
    assert _where(new, [[0.0, 0.0, 0.0005]]) == (["3"], [1])


SPH = " 1, 1, 1, 0,-1"
# «2» (dentro del módulo «4») está limitado por «1», que es hija de otro módulo («3»): mover «4» movería a «1».
LIMITED_FROM_OUTSIDE = ("Limitante de otro módulo\n" + Z
                        + _s("1", SPH, []) + _s("2", SPH, [("X-SCALE", 3.0), ("Y-SCALE", 3.0), ("Z-SCALE", 3.0)])
                        + _s("3", SPH, [("X-SCALE", 1.5), ("Y-SCALE", 1.5), ("Z-SCALE", 1.5)])
                        + _s("4", SPH, [("X-SCALE", 10.0), ("Y-SCALE", 10.0), ("Z-SCALE", 10.0)])
                        + _b("1", 1, [_side("1", -1)]) + _b("2", 2, [_side("2", -1), _in("1")])
                        + _b("3", 3, [_side("3", -1), _in("1")], kw="MODULE")
                        + _b("4", 4, [_side("4", -1), _in("2")], kw="MODULE")
                        + END)


def test_a_module_whose_body_is_limited_from_outside_is_not_moved():
    doc = parse_geo_text(LIMITED_FROM_OUTSIDE, "g.geo")
    geo = compile_checked(doc)
    m = geo.find_body("4").index
    assert pl.shared_surfaces(geo, m)
    with pytest.raises(EditError, match="«1»"):
        pl.move_element(doc, geo, m, pl.translation([1.0, 0.0, 0.0]))


# ---------------------------------------------------------------------------------------------------------------
# 2. «Añadir» no puede rodear cuerpos que ya existen; 10. ni quedar fuera de la envolvente que añadirá GEOMIN
# ---------------------------------------------------------------------------------------------------------------

def test_a_new_body_around_existing_bodies_is_refused():
    """prueba.geo sin la esfera de 5 m: una esfera de aire de 15 m alrededor del cubo de 43 cm no cabe."""
    doc = load_geometry(PRUEBA)
    geo = compile_checked(doc)
    delete_body(doc, geo, geo.find_body("2").index)
    _, doc, geo = _step(doc)
    loc = Locator(geo)
    bounds = estimate_bounds(geo, locator=loc)
    chk = check_placement(geo, loc, Primitive("sphere", center=(0.0, 0.0, 21.6), radius=1500.0), geo.nbodys, bounds)
    assert not chk.ok and "«1»" in chk.message and "Envolver" in chk.message
    # Al lado del cubo sí cabe.
    chk = check_placement(geo, loc, Primitive("sphere", center=(200.0, 0.0, 21.6), radius=50.0), geo.nbodys, bounds)
    assert chk.ok, chk.message


def test_add_dialog_says_why_a_body_around_others_does_not_fit(qtbot):
    """El diálogo muestra el porqué (antes, «el 0,0 % de su volumen caería en…» para lo que no sale del muestreo)."""
    from types import SimpleNamespace

    from argus.ui.add_body_dialog import AddBodyDialog

    doc = load_geometry(PRUEBA)
    geo = compile_checked(doc)
    delete_body(doc, geo, geo.find_body("2").index)
    _, doc, geo = _step(doc)
    loc = Locator(geo)
    session = SimpleNamespace(geo=geo, locator=loc, bounds=estimate_bounds(geo, locator=loc))
    dlg = AddBodyDialog(None, session, geo.nbodys, (0.0, 0.0, 21.6))
    qtbot.addWidget(dlg)
    dlg.kind.setCurrentIndex(dlg.kind.findData("sphere"))
    dlg.radius.setText("1500")
    for ed, v in ((dlg.cx, "0"), (dlg.cy, "0"), (dlg.cz, "21.6")):
        ed.setText(v)
    dlg._check()
    text = dlg.status.text()
    assert not dlg._ok and "«1»" in text and "Envolver" in text and "0,0 %" not in text


def test_a_body_beyond_the_automatic_enclosure_does_not_fit():
    """3discs no tiene envolvente: al añadir algo en el nivel superior GEOMIN pone una esfera de 1e7 cm."""
    doc, geo = _3discs()
    loc = Locator(geo)
    assert not geo.enclosure_added
    top = top_level(geo)
    far = check_placement(geo, loc, Primitive("sphere", center=(0.0, 0.0, 1e8), radius=1.0), top)
    assert not far.ok and "envolvente" in far.message
    near = check_placement(geo, loc, Primitive("sphere", center=(0.0, 0.0, 1e3), radius=1.0), top)
    assert near.ok, near.message


# ---------------------------------------------------------------------------------------------------------------
# 3. Mover el original de un clon mueve el clon: hay que comprobar dónde queda
# ---------------------------------------------------------------------------------------------------------------

def _cloned_with_obstacle():
    doc = parse_geo_text(CLONED, "c.geo")
    geo = compile_checked(doc)
    world = geo.find_body("4").index
    add_primitive(doc, geo, Primitive("box", center=(40.0, 0.0, 0.0), sides=(6.0, 6.0, 6.0)), 4, world)
    _, doc, geo = _step(doc)
    return doc, geo


def test_moving_the_original_of_a_clone_checks_the_clone():
    doc, geo = _cloned_with_obstacle()
    loc = Locator(geo)
    kb = geo.find_body("2").index  # el original (caja en x de 5 a 15); su clon está en x de 25 a 35
    ext = element_extent(geo, RayTracer(geo), kb, (np.full(3, -60.0), np.full(3, 60.0)))
    chk = pl.check_motion(geo, loc, kb, pl.translation([8.0, 0.0, 0.0]), ext)
    assert not chk.ok and "clon" in chk.message and "«5»" in chk.message
    assert not check_move(geo, loc, kb, np.array([8.0, 0.0, 0.0]), ext).ok  # «Pegar» usa check_move
    # Hacia el otro lado el clon no choca con nada.
    assert pl.check_motion(geo, loc, kb, pl.translation([-8.0, 0.0, 0.0]), ext).ok


# ---------------------------------------------------------------------------------------------------------------
# 4. Duplicar el módulo raíz de una geometría sin envolvente
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("linked", [False, True])
def test_duplicate_the_root_module_to_the_top_level(tmp_path, need_probe, linked):
    doc = parse_geo_text(NESTED, "anidados.geo")
    geo = compile_checked(doc)
    root = geo.find_body("4").index
    assert not geo.enclosure_added and root == geo.nbodys
    labels = duplicate(doc, geo, root, np.array([50.0, 0.0, 0.0]), 2, linked=linked)  # contenedor por defecto
    text, _doc2, geo2 = _step(doc)
    assert geo2.enclosure_added and len(labels) == 2
    for k, lab in enumerate(labels, start=1):
        assert geo2.body(geo2.find_body(lab).index).mother == geo2.nbodys  # en la envolvente, al lado
        assert _where(geo2, [[50.0 * k, 0.0, 0.0], [50.0 * k + 15.0, 0.0, 0.0]])[1] == [3, 4]
    pts, dirs = random_points([-25, -25, -25], [130, 25, 25], 3000, seed=4)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_duplicate_top_level_explicitly_and_not_into_itself():
    doc = parse_geo_text(NESTED, "anidados.geo")
    geo = compile_checked(doc)
    root = geo.find_body("4").index
    duplicate(doc, geo, root, np.array([50.0, 0.0, 0.0]), 1, container=top_level(geo))
    _step(doc)
    doc = parse_geo_text(NESTED, "anidados.geo")
    geo = compile_checked(doc)
    with pytest.raises(EditError, match="dentro"):
        duplicate(doc, geo, root, np.array([50.0, 0.0, 0.0]), 2, container=root)
    # Y las copias tienen que quedar dentro de la envolvente que GEOMIN añadirá (esfera de 1e7 cm).
    loc = Locator(geo)
    ext = element_extent(geo, RayTracer(geo), root, (np.full(3, -30.0), np.full(3, 30.0)))
    far = check_copies(geo, loc, root, np.array([2e7, 0.0, 0.0]), 1, top_level(geo), ext)
    assert not far.ok and "envolvente" in far.message
    assert check_copies(geo, loc, root, np.array([50.0, 0.0, 0.0]), 2, top_level(geo), ext).ok


def test_duplicate_dialog_offers_the_top_level_for_the_root_module(qtbot):
    from types import SimpleNamespace

    from argus.ui.duplicate_dialog import DuplicateDialog

    doc = parse_geo_text(NESTED, "anidados.geo")
    geo = compile_checked(doc)
    loc = Locator(geo)
    session = SimpleNamespace(geo=geo, locator=loc, bounds=estimate_bounds(geo, locator=loc))
    root = geo.find_body("4").index
    dlg = DuplicateDialog(None, session, root, lambda k: element_extent(geo, RayTracer(geo), k, session.bounds))
    qtbot.addWidget(dlg)
    assert dlg.container() == geo.nbodys + 1
    assert "nivel superior" in dlg.container_box.currentText().lower()
    dlg._check()
    assert dlg._ok, dlg.status.text()


# ---------------------------------------------------------------------------------------------------------------
# 5. La comprobación de solapes de los cambios de forma ve las láminas finas
# ---------------------------------------------------------------------------------------------------------------

def test_guard_sees_a_body_grown_through_a_thin_foil():
    doc, geo, loc, bounds = _load(FOIL)
    water = geo.find_body("2").index
    lab = add_primitive(doc, geo, Primitive("box", center=(0.0, 0.0, 5.0), sides=(2.0, 2.0, 2.0)), 3, water)
    _, doc, geo = _step(doc)
    kb = geo.find_body(lab).index
    zpair = [r.label for r in geo.body(kb).element.items if r.is_surface][2]  # sus planos z = 5 ± 1
    sf.remove_surface(doc, geo, kb, zpair)
    new = compile_checked(reparse(write_geo_text(doc), doc))
    with pytest.raises(EditError, match="«1»"):
        guard(bounds, [lab])(geo, new)


def test_guard_still_accepts_a_local_change():
    doc, geo, loc, bounds = _load(FOIL)
    water = geo.find_body("2").index
    lab = add_primitive(doc, geo, Primitive("box", center=(0.0, 0.0, 5.0), sides=(2.0, 2.0, 2.0)), 3, water)
    _, doc, geo = _step(doc)
    kb = geo.find_body(lab).index
    xpair = [r.label for r in geo.body(kb).element.items if r.is_surface][0]  # planos x: sigue lejos del oro
    sf.remove_surface(doc, geo, kb, xpair)
    guard(bounds, [lab])(geo, compile_checked(reparse(write_geo_text(doc), doc)))


# ---------------------------------------------------------------------------------------------------------------
# 6. Convertir y envolver ven los limitantes sin cota analítica (un cubo girado)
# ---------------------------------------------------------------------------------------------------------------

def _rot_plane(label, idx, scale):
    lines = [f"SURFACE ({label:>4})", f"INDICES=({idx})", f"{scale[0]:>7}=({scale[1]:+.15E},   0)",
             "  OMEGA=(+3.000000000000000E+01,   0) DEG"]
    return "\n".join(lines) + "\n" + Z


# El SHARED de test_review_editor con L cambiado por un cubo de 0,5 cm girado 30° (asoma en B y en U) y la caja
# V aparte (x en [0, 10], y en [20, 40]).
ROTATED_L = ("Cubo girado compartido\n" + Z
             + _s("1", X, [("X-SCALE", 5.0), ("X-SHIFT", -5.0)]) + _s("2", X, [("X-SCALE", 5.0), ("X-SHIFT", 5.0)])
             + _s("3", Y, [("Y-SCALE", 10.0)]) + _s("4", ZZ, [("Z-SCALE", 10.0)])
             + _rot_plane("5", X, ("X-SCALE", 0.25)) + _rot_plane("6", Y, ("Y-SCALE", 0.25))
             + _s("7", ZZ, [("Z-SCALE", 0.25)])
             + _s("8", X, [("X-SCALE", 50.0)]) + _s("9", Y, [("Y-SCALE", 50.0)]) + _s("10", ZZ, [("Z-SCALE", 50.0)])
             + _s("11", Y, [("Y-SCALE", 10.0), ("Y-SHIFT", 30.0)])
             + _b("1", 3, [_side("5", -1), _side("6", -1), _side("7", -1)], desc="L (cubo girado)")
             + _b("2", 1, [_side("1", -1), _side("3", -1), _side("4", -1), _in("1")], desc="B")
             + _b("3", 2, [_side("2", -1), _side("3", -1), _side("4", -1), _in("1")], desc="U")
             + _b("4", 5, [_side("2", -1), _side("11", -1), _side("4", -1)], desc="V")
             + _b("5", 4, [_side("8", -1), _side("9", -1), _side("10", -1), _in("1"), _in("2"), _in("3"), _in("4")],
                  kw="MODULE", desc="M")
             + END)


def test_convert_sees_a_limiting_body_without_analytic_box():
    doc, geo, loc, bounds = _load(ROTATED_L)
    chk = check_convert(geo, loc, geo.find_body("2").index, bounds)
    assert not chk.ok and "«1»" in chk.message


def test_wrap_and_copies_see_a_limiting_body_without_analytic_box():
    doc, geo, loc, bounds = _load(ROTATED_L)
    b = geo.find_body("2").index
    ext = element_extent(geo, RayTracer(geo), b, bounds)
    lo, hi = members_box(geo, b, ext, bounds)
    assert hi[0] > 0.3  # lo que asoma L por el lado de U (x hasta 0,34)
    cont, _c = wrap_candidates(geo, b)
    prim = Primitive("box", center=(-5.0, 0.0, 0.0), sides=(10.0, 20.0, 20.0))  # justo B, sin lo que asoma L
    assert not check_wrap(geo, loc, [b], prim, cont, [ext], bounds).ok
    # Una copia de B 30 cm más allá en y lleva su L: lo que asoma caería dentro de V.
    chk = check_copies(geo, loc, b, (0.0, 30.0, 0.0), 1, geo.find_body("5").index, ext)
    assert not chk.ok and "«4»" in chk.message


def test_a_limiting_body_that_cannot_be_sampled_is_not_passed():
    """Un limitante vacío (sin volumen que muestrear): no se puede comprobar, así que no se da por bueno."""
    body_l = _b("1", 3, [_side("5", -1), _side("6", -1), _side("7", -1)], desc="L (cubo girado)")
    empty = ROTATED_L.replace(body_l, _s("12", X, [("X-SCALE", 60.0)])  # |x| > 60: nada del cubo
                              + _b("1", 3, [_side("5", -1), _side("6", -1), _side("7", -1), _side("12", +1)],
                                   desc="L vacío"))
    assert empty != ROTATED_L
    doc, geo, loc, bounds = _load(empty)
    chk = check_convert(geo, loc, geo.find_body("2").index, bounds)
    assert not chk.ok and "comprobar" in chk.message


# ---------------------------------------------------------------------------------------------------------------
# 7. Los desbordamientos que ya había se reconocen aunque cambie la numeración
# ---------------------------------------------------------------------------------------------------------------

def _grid(k=16):
    lines = ["Rejilla de cajas en un modulo", Z.rstrip()]
    for i in range(k + 1):
        lines += [f"SURFACE ({i + 1:4d})", "INDICES=( 0, 0, 0, 1, 0)", "  THETA=(+9.000000000000000E+01,   0) DEG",
                  f"X-SHIFT=({float(i):+.15E},   0)", Z.rstrip()]
    for j in range(k + 1):
        lines += [f"SURFACE ({100 + j:4d})", "INDICES=( 0, 0, 0, 1, 0)", "  THETA=(+9.000000000000000E+01,   0) DEG",
                  "    PHI=(+9.000000000000000E+01,   0) DEG", f"Y-SHIFT=({float(j):+.15E},   0)", Z.rstrip()]
    lines += ["SURFACE ( 200)", "INDICES=( 0, 0, 1, 0,-1)", Z.rstrip()]
    n = 0
    for i in range(k):
        for j in range(k):
            n += 1
            lines += [f"BODY    ({n:4d})", "MATERIAL(   1)", f"SURFACE ({i + 1:4d}), SIDE POINTER=(+1)",
                      f"SURFACE ({i + 2:4d}), SIDE POINTER=(-1)", f"SURFACE ({100 + j:4d}), SIDE POINTER=(+1)",
                      f"SURFACE ({101 + j:4d}), SIDE POINTER=(-1)", "SURFACE ( 200), SIDE POINTER=(-1)", Z.rstrip()]
    lines += ["SURFACE ( 300)", "INDICES=( 1, 1, 1, 0,-1)", "X-SCALE=(+1.000000000000000E+02,   0)",
              "Y-SCALE=(+1.000000000000000E+02,   0)", "Z-SCALE=(+1.000000000000000E+02,   0)", Z.rstrip()]
    lines += ["MODULE  (9000)", "MATERIAL(   2)", "SURFACE ( 300), SIDE POINTER=(-1)"]
    lines += [f"BODY    ({kb:4d})" for kb in range(1, n + 1)] + [Z.rstrip(), END.rstrip()]
    return "\n".join(lines) + "\n"


def test_deleting_a_daughter_of_an_overflowing_module_is_allowed():
    doc = parse_geo_text(_grid(), "rejilla.geo")
    geo = compile_checked(doc)
    assert table_overflows(geo)
    delete_body(doc, geo, geo.find_body("1").index)  # 256 -> 255 hijas: el módulo cambia de número
    _, doc2, new = _step(doc)
    assert table_overflows(new)
    assert not new_overflows(geo, new)
    # Pero una hija más en un módulo que ya se desborda sigue siendo un problema nuevo.
    assert new_overflows(new, geo)


# ---------------------------------------------------------------------------------------------------------------
# 8. Detalles: cajas del revés al duplicar y por qué no se puede eliminar algo
# ---------------------------------------------------------------------------------------------------------------

# Un cuerpo sin cota en x y z (una lámina en y cortada por un plano oblicuo) fuera de la escena.
UNBOUNDED = """Cuerpo no acotado fuera de la escena
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   2)   planos y
INDICES=( 0, 1, 0, 0,-1)
Y-SCALE=(+3.974421007041800E-02,   0)
X-SHIFT=(-4.837987241760000E-01,   0)
Y-SHIFT=(+1.417788319219000E+00,   0)
Z-SHIFT=(+8.828694399220000E-01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   6)   plano oblicuo
INDICES=( 0, 0, 0, 1, 0)
  THETA=(+2.202773925393400E+01,   0) DEG
    PHI=(+3.355014332686500E+01,   0) DEG
X-SHIFT=(+5.344483336500000E-02,   0)
Y-SHIFT=(+1.451255193517000E+00,   0)
Z-SHIFT=(-2.066516775823000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    ( SSS)   no acotado
MATERIAL(   1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   6), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   3)   planos x
INDICES=( 1, 0, 0, 0,-1)
X-SCALE=(+6.699449202227412E-02,   0)
X-SHIFT=(-5.420587252480000E-01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   4)   planos y
INDICES=( 0, 1, 0, 0,-1)
Y-SCALE=(+4.689614441559188E-02,   0)
Y-SHIFT=(+1.655827503834000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   5)   planos z
INDICES=( 0, 0, 1, 0,-1)
Z-SCALE=(+8.709283962895635E-02,   0)
Z-SHIFT=(-1.308834307430000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   2)   caja
MATERIAL(   4)
SURFACE (   3), SIDE POINTER=(-1)
SURFACE (   4), SIDE POINTER=(-1)
SURFACE (   5), SIDE POINTER=(-1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""


def test_extent_and_copies_of_an_unbounded_body_outside_the_scene():
    doc, geo, loc, bounds = _load(UNBOUNDED)
    size = float(np.max(bounds[1] - bounds[0]))
    scene = (bounds[0] - 0.3 * size, bounds[1] + 0.3 * size)
    kb = geo.find_body("SSS").index
    ext = element_extent(geo, RayTracer(geo), kb, scene)
    assert np.all(ext[1] >= ext[0])
    chk = check_copies(geo, loc, kb, np.array([1.0, 0.0, 0.0]), 1, geo.nbodys, ext, n=500)
    assert isinstance(chk.ok, bool)


def test_delete_problem_explains_why_before_asking():
    from argus.pengeom.edit import delete_problem

    doc = parse_geo_text(NESTED, "anidados.geo")
    geo = compile_checked(doc)
    assert "dentro" in delete_problem(doc, geo, geo.find_body("4").index)  # módulo con hijas
    assert delete_problem(doc, geo, geo.find_body("1").index) is None  # un cuerpo sí
    doc = parse_geo_text(CLONED, "c.geo")
    geo = compile_checked(doc)
    assert delete_problem(doc, geo, geo.find_body("3").index) is None  # el clon sí
    assert "clon" in delete_problem(doc, geo, geo.find_body("2").index)  # el original tiene hijas y un clon
    assert delete_problem(doc, geo, geo.nbodys) is not None  # la envolvente, no
    # Un módulo vacío que copia un clon: se explica (antes, GEOMIN daba un error críptico al compilar).
    delete_body(doc, geo, geo.find_body("1").index)
    _, doc, geo = _step(doc)
    msg = delete_problem(doc, geo, geo.find_body("2").index)
    assert msg is not None and "«3»" in msg
    with pytest.raises(EditError, match="«3»"):
        delete_body(doc, geo, geo.find_body("2").index)


# ---------------------------------------------------------------------------------------------------------------
# 9. MODULE con MATERIAL(9999) sin caja CT
# ---------------------------------------------------------------------------------------------------------------

def test_module_with_material_9999_without_ct_is_an_error():
    text = (STACK / "3discs.geo").read_text(encoding="latin-1").replace(
        "MODULE  (   3)   Solid cylinder 3\nMATERIAL(   1)", "MODULE  (   3)   Solid cylinder 3\nMATERIAL(9999)")
    doc = parse_geo_text(text, "t.geo")
    geo = compile_geometry(doc, parinp=STACK_PARINP)
    assert geo.ok and geo.ct is None
    errors = [d.message for d in validate(geo).errors]
    assert any("9999" in m for m in errors), errors


# ---------------------------------------------------------------------------------------------------------------
# 1 (sigue). El .in sigue a la geometría tras mover con superficies propias (misma numeración de cuerpos)
# ---------------------------------------------------------------------------------------------------------------

def test_separating_surfaces_does_not_renumber_bodies():
    doc, geo = _3discs()
    pl.move_element(doc, geo, geo.find_body("2").index, pl.translation([0.0, 0.0, 0.001]), STACK_PARINP)
    _, _doc2, geo2 = _step(doc, STACK_PARINP)
    assert all(k == v for k, v in renumber_map(geo, geo2).items())


def test_motion_guard_for_the_live_drag():
    """El arrastre en vivo cambia solo la transformación: ``motion_guard`` (para ``commit_live``) lo comprueba."""
    doc, geo = _3discs()
    work = reparse(write_geo_text(doc), doc)
    wgeo = compile_checked(work, STACK_PARINP)
    pl.set_transform(pl.placeable(wgeo, wgeo.find_body("2").index), {"Z-SHIFT": 0.001})
    new = compile_checked(reparse(write_geo_text(work), work), STACK_PARINP)
    with pytest.raises(EditError, match="«3»"):
        pl.motion_guard("   2", (np.full(3, -0.02), np.full(3, 0.02)))(geo, new)
    # Con superficies propias, el mismo movimiento pasa.
    work = reparse(write_geo_text(doc), doc)
    wgeo = compile_checked(work, STACK_PARINP)
    pl.separate_surfaces(work, wgeo, wgeo.find_body("2").index)
    wgeo = compile_checked(reparse(write_geo_text(work), work), STACK_PARINP)
    work = wgeo.document
    pl.set_transform(pl.placeable(wgeo, wgeo.find_body("2").index), {"Z-SHIFT": 0.001})
    new = compile_checked(reparse(write_geo_text(work), work), STACK_PARINP)
    pl.motion_guard("   2", (np.full(3, -0.02), np.full(3, 0.02)))(wgeo, new)


def test_moving_the_original_of_a_clone_into_a_neighbour_is_refused_after_the_edit_too():
    """Aunque el llamante no compruebe antes, ``move_element`` comprueba después que nada más cambia."""
    doc, geo = _cloned_with_obstacle()
    with pytest.raises(EditError, match="«5»"):
        pl.move_element(doc, geo, geo.find_body("2").index, pl.translation([8.0, 0.0, 0.0]),
                        bounds=(np.full(3, -60.0), np.full(3, 60.0)))


def test_live_drag_with_separated_surfaces():
    """Lo que tendría que hacer el arrastre en vivo (``GeoController.live_transform``): separar las superficies en su
    copia de trabajo y aplicar los valores de ``moved_values(..., separate=True)``; ``motion_guard`` lo acepta."""
    doc, geo = _3discs()
    kb = geo.find_body("2").index
    values = pl.moved_values(geo, kb, pl.translation([0.0, 0.0, 0.001]), STACK_PARINP, separate=True)
    work = reparse(write_geo_text(doc), doc)
    wgeo = compile_checked(work, STACK_PARINP)
    assert pl.separate_surfaces(work, wgeo, wgeo.find_body("2").index)
    wgeo = compile_checked(work, STACK_PARINP)
    pl.set_transform(pl.placeable(wgeo, wgeo.find_body("2").index), values)
    new = compile_checked(reparse(write_geo_text(work), work), STACK_PARINP)
    pl.motion_guard("   2", (np.full(3, -0.02), np.full(3, 0.02)))(geo, new)
    assert _where(new, [[0.0, 0.0, 0.0005], [0.0, 0.0, 0.0045]]) == (["3", "2"], [1, 1])
