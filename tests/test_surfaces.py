"""Etiquetas, lados, superficies nuevas y forma implícita (punto 4 del editor)."""

import math

import numpy as np
import pytest

from argus.pengeom import surfaces as sf
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import EditError, Primitive, add_primitive, find_element
from argus.pengeom.locate import Locator
from argus.pengeom.overlap import check_overlaps, guard, new_problems
from argus.pengeom.parser import load_geometry
from argus.pengeom.quadric import REDUCED_NAMES, reduced_to_implicit, rotshf
from conftest import CORPUS, MANIFEST
from geo_oracle import assert_pengeom_agrees, load_text, materials, random_points, roundtrip

CUBO = CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"
SCENE = (np.full(3, -600.0), np.full(3, 600.0))


def _load(path=CUBO):
    doc = load_geometry(path)
    geo = compile_geometry(doc, base_dir=path.parent)
    assert geo.ok
    return doc, geo


def test_label_rules():
    assert sf.label_text("7") == "   7"
    assert sf.label_text(" AB ") == "  AB"
    for bad in ("", "   ", "ABCDE", "A'B", "(1)", "a,b", "Año"):
        with pytest.raises(EditError):
            sf.label_text(bad)


def test_rename_body_updates_every_reference():
    doc, geo = _load()
    old, new = sf.rename_body(doc, geo, geo.find_body("1").index, "CUBO")
    assert (old, new) == ("   1", "CUBO")
    text, doc2, geo2 = roundtrip(doc)
    assert "BODY    (CUBO)" in text and geo2.find_body("CUBO") is not None and geo2.find_body("1") is None
    assert [r.label for r in find_element(doc2, "body", "2").items if not r.is_surface] == ["CUBO"]
    # Las mismas tablas (salvo la etiqueta) y los mismos materiales.
    pts, dirs = random_points(*SCENE, 4000)
    assert np.array_equal(materials(geo, pts, dirs)[1], materials(geo2, pts, dirs)[1])
    with pytest.raises(EditError, match="Ya hay"):
        sf.rename_body(doc2, geo2, geo2.find_body("CUBO").index, "2")
    with pytest.raises(EditError, match="envolvente"):
        sf.rename_body(doc2, geo2, geo2.nbodys, "X")


def test_rename_surface_and_clone_target(tmp_path):
    from test_duplicate import CLONED

    doc, geo = load_text(CLONED, tmp_path)
    sf.rename_surface(doc, geo, "4", "ESF")
    _, doc, geo = roundtrip(doc)
    assert find_element(doc, "surface", "ESF") is not None
    assert any(r.label == " ESF" for r in find_element(doc, "body", "1").items if r.is_surface)
    old, new = sf.rename_body(doc, geo, geo.find_body("2").index, "MOD")
    text, doc, geo = roundtrip(doc)
    assert "MODULE  ( MOD)" in text and geo.find_body("MOD").is_module
    assert all(c.module_label == " MOD" for c in doc.elements if type(c).__name__ == "CloneElement")
    # El clon también se puede renombrar.
    clone = geo.find_body("3").index
    sf.rename_body(doc, geo, clone, "CL")
    text, doc, geo = roundtrip(doc)
    assert "CLONE   (  CL)" in text and geo.find_body("CL").origin == "clone"


def test_side_flip_and_overlap_guard(tmp_path):
    doc, geo = _load()
    sphere = geo.find_body("2").index
    add_primitive(doc, geo, Primitive("box", center=(1000.0, 0, 0), sides=(100.0, 100.0, 100.0)), 1, sphere)
    _, doc, geo = roundtrip(doc)
    add_primitive(doc, geo, Primitive("box", center=(1200.0, 0, 0), sides=(100.0, 100.0, 100.0)), 2,
                  geo.find_body("2").index)
    _, doc, geo = roundtrip(doc)
    a = geo.find_body("3").index
    lab_x = next(r.label for r in find_element(doc, "body", "3").items if r.is_surface)
    old = geo
    sf.set_side(doc, geo, a, lab_x, +1)  # la caja pasa a ser «fuera de los planos x»: invade a la otra
    _, doc, geo = roundtrip(doc)
    rep = new_problems(old, geo, [SCENE, (np.array([900.0, -60, -60]), np.array([1300.0, 60, 60]))])
    assert not rep.ok and "se solapan" in rep.message()
    with pytest.raises(EditError, match="solapan"):
        guard(SCENE, ["3"])(old, geo)
    # Volver a dejarlo como estaba no crea nada nuevo.
    sf.set_side(doc, geo, geo.find_body("3").index, lab_x, -1)
    _, doc2, geo2 = roundtrip(doc)
    guard(SCENE, ["3"])(old, geo2)
    with pytest.raises(EditError):
        sf.set_side(doc, geo, a, lab_x, 0)


def test_cut_a_body_with_a_new_plane(tmp_path, need_probe):
    doc, geo = _load()
    cube = geo.find_body("1").index
    lab = sf.new_plane(doc, geo, cube, (100.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    _, doc, geo = roundtrip(doc)
    sf.add_surface(doc, geo, geo.find_body("1").index, lab, -1)
    text, doc, geo = roundtrip(doc)
    pts = np.array([[150.0, 0, 250], [50.0, 0, 250], [-200.0, 100, 10]])
    assert list(materials(geo, pts)[1]) == [0, 1, 1]
    rng_pts, dirs = random_points(*SCENE, 4000)
    assert_pengeom_agrees(text, tmp_path, rng_pts, dirs)
    # Quitarla deja el cubo como estaba (y la superficie, que ya no usa nadie, se borra).
    notes = sf.remove_surface(doc, geo, geo.find_body("1").index, lab)
    _, doc, geo = roundtrip(doc)
    assert any("borrada" in n for n in notes) and find_element(doc, "surface", lab) is None
    assert list(materials(geo, pts)[1]) == [1, 1, 1]
    with pytest.raises(EditError, match="única"):
        d, g = load_text("t\n" + "0" * 64 + "\nSURFACE (   1)\nINDICES=( 1, 1, 1, 0,-1)\n" + "0" * 64 +
                         "\nBODY    (   1)\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + "0" * 64 +
                         "\nEND      " + "0" * 55 + "\n", tmp_path, "una.geo")
        sf.remove_surface(d, g, 1, "1")


def test_new_plane_in_a_rotated_module_is_written_in_its_frame(tmp_path):
    from argus.pengeom.extent import analytic_box
    from test_edit import ROTATED_MODULE

    doc, geo = load_text(ROTATED_MODULE, tmp_path)
    kb = geo.find_body("1").index
    lo, hi = analytic_box(geo, kb)
    c = 0.5 * (lo + hi)  # centro de la bola (radio 1), girada con el módulo
    assert materials(geo, c[None, :])[0][0] == kb
    lab = sf.new_plane(doc, geo, kb, c + [0.0, 0.0, 0.2], (0.0, 0.0, 1.0))
    _, doc, geo = roundtrip(doc)
    sf.add_surface(doc, geo, kb, lab, -1)
    _, doc, geo = roundtrip(doc)
    # El plano está en z = z_c + 0.2 (global) aunque el módulo esté girado.
    ib, _ = materials(geo, np.array([c + [0.0, 0, 0.1], c + [0.0, 0, 0.3], c + [0.5, 0, 0.1]]))
    assert ib[0] == kb and ib[1] != kb and ib[2] == kb


def test_reduced_to_implicit_keeps_the_geometry(tmp_path, need_probe):
    """Pasar todas las superficies de varias geometrías a forma implícita no cambia nada para PENGEOM."""
    names = ["pengeomjar/quadric", "pengeomjar/chair", "pengeomjar/tcone", "usuario/100Mev15-174/cubo-esfera.geo"]
    for name in names:
        path = CORPUS / name
        entry = next(g for g in MANIFEST if g["path"] == name)
        doc = load_geometry(path)
        geo = compile_geometry(doc, parinp=entry.get("parinp"), base_dir=path.parent)
        for s in doc.surfaces():
            sf.to_implicit(s, entry.get("parinp"))
        text, doc2, geo2 = roundtrip(doc, entry.get("parinp"))
        assert all(s.implicit for s in doc2.surfaces())
        a, b = geo.coefficients(), geo2.coefficients()
        assert np.allclose(a, b, rtol=1e-14, atol=1e-300)
        from argus.pengeom.bounds import estimate_bounds

        lo, hi = estimate_bounds(geo)
        pts, dirs = random_points(lo, hi, 3000, seed=4)
        assert np.array_equal(materials(geo, pts, dirs)[0], materials(geo2, pts, dirs)[0])
        assert_pengeom_agrees(text, tmp_path, pts, dirs, parinp=entry.get("parinp"), name="implicita.geo")


def test_edit_implicit_coefficients(tmp_path, need_probe):
    doc, geo = _load()
    s = find_element(doc, "surface", "5")  # la esfera de 5000
    sf.to_implicit(s)
    coefs, tr = sf.implicit_values(s)
    assert coefs["AXX"] == pytest.approx(1 / 5000.0 ** 2) and coefs["A0"] == -1.0
    # Elipsoide: el doble de ancho en x.
    sf.set_implicit(s, {"AXX": 1 / 10000.0 ** 2})
    text, doc, geo = roundtrip(doc)
    assert sf.classify(geo.surface(geo.body(geo.find_body("2").index).surfaces[0]).q) in ("elipsoide", "esfera")
    assert list(materials(geo, np.array([[8000.0, 0, 0], [0.0, 8000, 0]]))[0]) == [2, geo.nbodys]
    # Con giro y desplazamiento tras la línea de unos.
    sf.set_implicit(find_element(doc, "surface", "5"), {}, {"PHI": 90.0, "Z-SHIFT": 10.0})
    text, doc, geo = roundtrip(doc)
    assert "1111111111" in text and "PHI" in text
    assert list(materials(geo, np.array([[0.0, 8000, 10], [8000.0, 0, 10]]))[0]) == [2, geo.nbodys]
    pts, dirs = random_points(np.full(3, -11000.0), np.full(3, 11000.0), 3000)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)
    with pytest.raises(EditError, match="cero"):
        sf.set_implicit(find_element(doc, "surface", "5"), dict.fromkeys(("AXX", "AYY", "AZZ", "A0"), 0.0))
    with pytest.raises(EditError, match="reducida"):
        sf.set_implicit(find_element(doc, "surface", "1"), {"AXX": 1.0})


def test_new_implicit_surface_limits_a_body(tmp_path, need_probe):
    doc, geo = _load()
    cube = geo.find_body("1").index
    # Cono de eje z con vértice en (0, 0, 600): x² + y² − (z − 600)² = 0; el cubo queda por dentro (F < 0).
    lab = sf.new_implicit(doc, geo, cube, {"AXX": 1.0, "AYY": 1.0, "AZZ": -1.0}, {"Z-SHIFT": 600.0})
    _, doc, geo = roundtrip(doc)
    assert "cono" in find_element(doc, "surface", lab).description
    sf.add_surface(doc, geo, geo.find_body("1").index, lab, -1)
    text, doc, geo = roundtrip(doc)
    pts = np.array([[240.0, 0, 450], [100.0, 0, 450], [200.0, 200, 100]])
    assert list(materials(geo, pts)[1]) == [0, 1, 1]
    rp, dirs = random_points(*SCENE, 3000, seed=7)
    assert_pengeom_agrees(text, tmp_path, rp, dirs)


def test_classify_every_reduced_form():
    expected = {"plano": "plano", "esfera": "esfera", "esfera imaginaria": "vacía (sin puntos reales)",
                "cilindro": "cilindro", "par de planos": "par de planos paralelos",
                "hiperboloide de una hoja": "hiperboloide de una hoja",
                "hiperboloide de dos hojas": "hiperboloide de dos hojas", "cono": "cono",
                "paraboloide": "paraboloide de revolución", "cilindro hiperbólico": "cilindro hiperbólico",
                "cilindro parabólico": "cilindro parabólico", "paraboloide hiperbólico": "paraboloide hiperbólico"}
    for idx, name in REDUCED_NAMES.items():
        q = rotshf(0.3, 0.7, 1.1, 150.0, -2.0, 0.5, reduced_to_implicit(idx, 2.0, 2.0, 2.0))
        assert sf.classify(q) == expected[name], (idx, name)
    assert sf.classify(reduced_to_implicit((1, 1, 1, 0, -1), 1.0, 2.0, 3.0)) == "elipsoide"
    assert sf.classify(reduced_to_implicit((1, 1, 0, 0, -1), 1.0, 2.0, 1.0)) == "cilindro elíptico"


def test_transform_surface_moves_reduced_and_implicit_forms():
    doc, geo = _load()
    s = find_element(doc, "surface", "5")
    rot = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    sf.transform_surface(s, rot, np.array([10.0, 0, 0]))
    vals = {p.name: p.value for p in s.params}
    assert vals["X-SHIFT"] == 10.0 and math.isclose(vals.get("OMEGA", 0.0) + vals.get("PHI", 0.0), 90.0, abs_tol=1e-9)
    s2 = find_element(doc, "surface", "1")
    sf.to_implicit(s2)
    sf.transform_surface(s2, np.eye(3), np.array([0.0, 0, 5.0]))
    assert [(p.name, p.value) for p in s2.transform] == [("Z-SHIFT", 5.0)]


def test_overlap_report_on_the_user_geometries_is_clean():
    for name in ("usuario/100Mev15-174/cubo-esfera.geo", "penhan/stack/3discs.geo"):
        entry = next(g for g in MANIFEST if g["path"] == name)
        path = CORPUS / name
        geo = compile_geometry(load_geometry(path), parinp=entry.get("parinp"), base_dir=path.parent)
        from argus.pengeom.bounds import estimate_bounds

        lo, hi = estimate_bounds(geo)
        pts, _ = random_points(lo, hi, 20000)
        assert check_overlaps(geo, pts, 1e-9 * float(np.max(hi - lo))).ok
    # well (PenGeomJar) trae un solape de verdad: la tapa (5) y la pared exterior (6) comparten un anillo.
    path = CORPUS / "pengeomjar/well"
    geo = compile_geometry(load_geometry(path), base_dir=path.parent)
    rep = check_overlaps(geo, random_points(np.full(3, -4.0), np.full(3, 5.2), 40000, seed=3)[0], 1e-9)
    assert not rep.ok and "«5»" in rep.message() and "«6»" in rep.message()


def test_locator_sees_the_same_regions_as_the_overlap_envelopes():
    """Sin solapes, el cuerpo de LOCATE es el de la envoltura que contiene cada punto (más profunda)."""
    doc, geo = _load()
    pts, _ = random_points(*SCENE, 5000, seed=11)
    ib, _ = Locator(geo).locate(pts)
    from argus.pengeom.overlap import _envelope, surface_signs

    signs = surface_signs(geo, pts, 1e-9)
    cube = geo.find_body("1").index
    inside = _envelope(geo, cube, signs)
    assert np.all(ib[inside] == cube)
