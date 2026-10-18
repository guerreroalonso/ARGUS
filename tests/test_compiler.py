import math

import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.quadric import is_plane, reduced_to_implicit, rotshf
from conftest import CORPUS, MANIFEST, MANIFEST_IDS

SEP = "0" * 64
END = "END      " + "0" * 55


def geo(*lines: str) -> str:
    return "\n".join(["Prueba", SEP, *lines]) + "\n"


def surf(label, indices, *params):
    return [f"SURFACE ({label:>4})", "INDICES=(" + ",".join(f"{i:2d}" for i in indices) + ")", *params, SEP]


def par(name, value, ipar=0, unit=""):
    kw = {"X-SCALE": "X-SCALE=", "Y-SCALE": "Y-SCALE=", "Z-SCALE": "Z-SCALE=", "Z-SHIFT": "Z-SHIFT=",
          "X-SHIFT": "X-SHIFT=", "THETA": "  THETA=", "PHI": "    PHI=", "OMEGA": "  OMEGA="}[name]
    return f"{kw}({value:+.15E},{ipar:4d}){(' ' + unit) if unit else ''}"


# ---------------------------------------------------------------------------
# Cuádricas
# ---------------------------------------------------------------------------

def test_rotshf_identity_and_shift():
    sphere = reduced_to_implicit((1, 1, 1, 0, -1), 2.0, 2.0, 2.0)
    assert rotshf(0, 0, 0, 0, 0, 0, sphere) == sphere
    moved = rotshf(0, 0, 0, 1.0, 2.0, 3.0, sphere)
    # (x-1)²/4 + (y-2)²/4 + (z-3)²/4 - 1
    assert moved[0] == 0.25 and moved[6] == -0.5 and moved[7] == -1.0 and moved[8] == -1.5
    assert moved[9] == pytest.approx(0.25 * 14 - 1)


def test_rotshf_rotates_plane_normal():
    plane = reduced_to_implicit((0, 0, 0, 1, 0))  # z = 0
    q = rotshf(0.0, math.pi / 2, 0.0, 0, 0, 0, plane)  # θ=90°: el eje z pasa a x
    assert is_plane(q)
    assert q[6] == pytest.approx(1.0) and abs(q[8]) < 1e-15


# ---------------------------------------------------------------------------
# Tablas conocidas (verificadas con PENGEOM)
# ---------------------------------------------------------------------------

def test_cubo_esfera_tables():
    g = compile_geometry(load_geometry(CORPUS / "usuario/100Mev15-174/cubo-esfera.geo"))
    assert g.ok and g.nsurf == 6 and g.nbodys == 3 and g.enclosure_added
    b1, b2, b3 = g.bodies
    assert (b1.material, b1.mother, b1.surfaces, b1.flags) == (1, 3, [1, 2, 3, 4, 6], [1, 1, 1, 2, 1])
    assert (b2.material, b2.surfaces, b2.flags, b2.limiting) == (0, [5, 1, 2, 3, 4, 6], [1, 3, 3, 3, 3, 1], [1])
    assert b3.is_module and b3.daughters == [1, 2, 3] and b3.flags == [1, 4, 4, 4, 4, 4]
    assert g.surface(6).q[9] == -1.0e14
    assert g.balias(1) == "   1" and g.balias(3) == ""
    assert g.materials() == [1]


# ---------------------------------------------------------------------------
# Errores con los códigos IRETRN de GEOMIN
# ---------------------------------------------------------------------------

def _err(text, **kw):
    g = compile_geometry(parse_geo_text(text), **kw)
    assert not g.ok
    return g.diagnostics.errors[0].code


BALL = ["BODY    (   1)", "MATERIAL(   1)", "SURFACE (   1), SIDE POINTER=(-1)", SEP]


def test_undefined_surface():
    assert _err(geo(*BALL, END)) == 2021


def test_duplicate_surface_label():
    s = surf("1", (1, 1, 1, 0, -1))
    assert _err(geo(*s, *s, *BALL, END)) == 2004


def test_parinp_missing():
    s = surf("1", (1, 1, 1, 0, -1), par("X-SCALE", 1.0, ipar=1))
    assert _err(geo(*s, *BALL, END)) == 2008
    g = compile_geometry(parse_geo_text(geo(*s, *BALL, END)), parinp=[3.0])
    assert g.ok and g.surface(1).q[0] == pytest.approx(1 / 9) and g.parinp_used == [1]


def test_two_mothers():
    s = surf("1", (1, 1, 1, 0, -1), par("X-SCALE", 1.0), par("Y-SCALE", 1.0), par("Z-SCALE", 1.0))
    s2 = surf("2", (1, 1, 1, 0, -1), par("X-SCALE", 5.0), par("Y-SCALE", 5.0), par("Z-SCALE", 5.0))
    m1 = ["MODULE  (  10)", "MATERIAL(   0)", "SURFACE (   2), SIDE POINTER=(-1)", "BODY    (   1)", SEP]
    m2 = ["MODULE  (  11)", "MATERIAL(   0)", "SURFACE (   2), SIDE POINTER=(-1)", "BODY    (   1)", SEP]
    assert _err(geo(*s, *s2, *BALL, *m1, *m2, END)) == 2042


def test_limiting_body_must_exist():
    s = surf("1", (1, 1, 1, 0, -1))
    b = ["BODY    (   2)", "MATERIAL(   1)", "SURFACE (   1), SIDE POINTER=(-1)", "BODY    (   9)", SEP]
    assert _err(geo(*s, *BALL, *b, END)) == 2025


def test_side_pointer_value():
    s = surf("1", (1, 1, 1, 0, -1))
    b = ["BODY    (   1)", "MATERIAL(   1)", "SURFACE (   1), SIDE POINTER=( 0)", SEP]
    assert _err(geo(*s, *b, END)) == 2024


def test_module_transform_moves_daughters():
    s = surf("1", (1, 1, 1, 0, -1))
    s2 = surf("2", (1, 1, 1, 0, -1), par("X-SCALE", 5.0), par("Y-SCALE", 5.0), par("Z-SCALE", 5.0))
    m = ["MODULE  (  10)", "MATERIAL(   0)", "SURFACE (   2), SIDE POINTER=(-1)", "BODY    (   1)",
         "1" * 64, par("Z-SHIFT", 10.0), SEP]
    g = compile_geometry(parse_geo_text(geo(*s, *s2, *BALL, *m, END)))
    assert g.ok
    # La esfera 1 queda centrada en z=10: coeficiente AZ = -20, A0 = 100-1.
    assert g.surface(1).q[8] == -20.0 and g.surface(1).q[9] == 99.0
    # Hay un único módulo sin madre: es la raíz y no se añade envolvente.
    assert not g.enclosure_added and g.nbodys == 2


# ---------------------------------------------------------------------------
# Verificación con PENGEOM (sonda)
# ---------------------------------------------------------------------------

@pytest.mark.oracle
@pytest.mark.parametrize("item", MANIFEST, ids=MANIFEST_IDS)
def test_tables_match_pengeom(item, need_probe):
    from argus.oracle.compare import compare_tables
    from argus.oracle.probe import run_probe

    path = CORPUS / item["path"]
    g = compile_geometry(load_geometry(path), parinp=item.get("parinp"))
    assert g.ok, [str(d) for d in g.diagnostics]
    res = run_probe(path, parinp=item.get("parinp"))
    assert res.status.ok, res.status
    cmp = compare_tables(g, res.tables, exact=True)
    assert cmp.ok, "\n".join(cmp.differences)
    assert g.nmat == res.status.nmat


@pytest.mark.oracle
def test_error_codes_match_pengeom(tmp_path, need_probe):
    from argus.oracle.probe import run_probe

    s = surf("1", (1, 1, 1, 0, -1))
    cases = [
        geo(*BALL, END),
        geo(*s, *s, *BALL, END),
        geo(*s, "BODY    (   1)", "MATERIAL(   1)", "SURFACE (   1), SIDE POINTER=( 0)", SEP, END),
        geo(*surf("1", (1, 1, 1, 0, -1), par("X-SCALE", 1.0, ipar=2)), *BALL, END),
    ]
    for i, text in enumerate(cases):
        f = tmp_path / f"caso{i}.geo"
        f.write_text(text)
        g = compile_geometry(parse_geo_text(text))
        res = run_probe(f, tables=False)
        assert not g.ok and g.diagnostics.errors[0].code == res.status.iretrn
