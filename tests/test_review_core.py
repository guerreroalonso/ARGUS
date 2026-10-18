"""Fallos encontrados en la revisión de la 0.7 (lectura de ficheros y límites de PENGEOM)."""

import numpy as np
import pytest

from argus.core.fortran_io import FortranReadError, read_record, split_records, text_records
from argus.pengeom.compiler import NXG, compile_geometry
from argus.pengeom.parser import _PARAM_FMT, parse_geo_text
from argus.pengeom.validate import new_overflows, table_overflows, validate
from argus.penhan.quickscan import scan_input_text

Z = "0" * 64 + "\n"
HEAD = "Prueba\n" + Z
END = "END      " + "0" * 55 + "\n"


def _v(x):
    return f"{x:+.15E}"


SPHERE = (HEAD + "C comentario con puntos suspensivos\x85 y un salto de página\x0c aquí\n"
          "SURFACE (   1)   esfera\x85 de 1 cm\nINDICES=( 1, 1, 1, 0,-1)\n" + Z
          + "BODY    (   1)   bola\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z + END)


def test_records_split_only_at_newlines_like_fortran():
    t = "a\r\nb\x85c\x0cd\x1ce f\nlast"
    assert split_records(t) == ["a\r\n", "b\x85c\x0cd\x1ce f\n", "last"]
    assert text_records(t) == ["a", "b\x85c\x0cd\x1ce f", "last"]
    assert split_records("") == [] and text_records("x\n") == ["x"]


def test_ellipsis_and_form_feed_in_comments_do_not_split_lines():
    """Un «…» de Windows (0x85 en cp1252) en un comentario partía la línea y ARGUS rechazaba la geometría."""
    doc = parse_geo_text(SPHERE, "g.geo")
    geo = compile_geometry(doc)
    assert not doc.diagnostics.errors and geo.ok and geo.nbodys == 2


@pytest.mark.oracle
def test_ellipsis_in_comments_pengeom_agrees(tmp_path, need_probe):
    from geo_oracle import assert_pengeom_agrees, random_points

    pts, dirs = random_points(np.full(3, -2.0), np.full(3, 2.0), 500, seed=3)
    assert_pengeom_agrees(SPHERE, tmp_path, pts, dirs, name="elipsis.geo")


def test_quickscan_reads_parinp_after_a_comment_with_an_ellipsis():
    text = ("TITLE  prueba\r\nGEOMFN disc.geo                  [Geometry file]\r\n"
            "       . nota\x85 del autor\r\nPARINP 1 40.0\r\nPARINP 2 40.0\r\n")
    s = scan_input_text(text)
    assert s.geomfn == "disc.geo" and s.parinp == {1: 40.0, 2: 40.0}


@pytest.mark.parametrize("line", [
    "Z-SHIFT=(2.5,    0)",                     # valor escrito a mano, con la coma antes de tiempo
    "X-SHIFT=(2,5                   ,   0)",   # coma decimal
    "  THETA=(9.0E1,0,) DEG",
    "X-SHIFT=(1.5,2    ,   0)",
])
def test_a_comma_inside_a_numeric_field_is_an_error_as_in_geomin(line):
    """GEOMIN lee cada línea con una lectura interna: gfortran no acepta ahí una coma dentro del campo
    (Wrong input format, IRETRN 2075). Antes, «2,5» se leía como 2E-15 sin avisar."""
    with pytest.raises(FortranReadError):
        read_record(line, _PARAM_FMT)
    kw, v, ip, _ = read_record("X-SHIFT=(+2.500000000000000E+00,   0)", _PARAM_FMT)
    assert v == 2.5 and ip == 0


def test_a_decimal_comma_in_a_geo_is_reported():
    text = SPHERE.replace("INDICES=( 1, 1, 1, 0,-1)\n",
                          "INDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(2,5                   ,   0)\n")
    doc = parse_geo_text(text, "g.geo")
    assert doc.diagnostics.errors


@pytest.mark.oracle
def test_a_comma_inside_a_numeric_field_pengeom_rejects_it(tmp_path):
    from argus.oracle.probe import find_probe, run_probe

    if find_probe() is None:
        pytest.skip("Sonda PENGEOM no compilada")
    text = SPHERE.replace("INDICES=( 1, 1, 1, 0,-1)\n", "INDICES=( 1, 1, 1, 0,-1)\nZ-SHIFT=(2.5,    0)\n")
    p = tmp_path / "coma.geo"
    p.write_text(text, encoding="latin-1")
    assert run_probe(p).status.iretrn == 2075
    assert parse_geo_text(text, "coma.geo").diagnostics.errors


def _grid(nx, ny, in_module=False):
    """nx × ny cajas que comparten planos (pocas superficies, muchas hijas)."""
    out, s, xs, ys = [HEAD], 0, [], []
    for i in range(nx + 1):
        s += 1
        xs.append(s)
        out.append(f"SURFACE ({s:4d})\nINDICES=( 0, 0, 0, 0, 0)\n     AX=({_v(1.0)},   0)\n"
                   f"     A0=({_v(-i)},   0)\n" + Z)
    for j in range(ny + 1):
        s += 1
        ys.append(s)
        out.append(f"SURFACE ({s:4d})\nINDICES=( 0, 0, 0, 0, 0)\n     AY=({_v(1.0)},   0)\n"
                   f"     A0=({_v(-j)},   0)\n" + Z)
    s += 1
    zlo = s
    out.append(f"SURFACE ({s:4d})\nINDICES=( 0, 0, 0, 1, 0)\nZ-SHIFT=({_v(0.0)},   0)\n" + Z)
    s += 1
    zhi = s
    out.append(f"SURFACE ({s:4d})\nINDICES=( 0, 0, 0, 1, 0)\nZ-SHIFT=({_v(1.0)},   0)\n" + Z)
    b = 0
    for i in range(nx):
        for j in range(ny):
            b += 1
            out.append(f"BODY    ({b:4d})\nMATERIAL({1 + b % 3:4d})\n"
                       f"SURFACE ({xs[i]:4d}), SIDE POINTER=( 1)\nSURFACE ({xs[i + 1]:4d}), SIDE POINTER=(-1)\n"
                       f"SURFACE ({ys[j]:4d}), SIDE POINTER=( 1)\nSURFACE ({ys[j + 1]:4d}), SIDE POINTER=(-1)\n"
                       f"SURFACE ({zlo:4d}), SIDE POINTER=( 1)\nSURFACE ({zhi:4d}), SIDE POINTER=(-1)\n" + Z)
    if in_module:
        s += 1
        out.append(f"SURFACE ({s:4d})\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=({_v(1e3)},   0)\nY-SCALE=({_v(1e3)},   0)\n"
                   f"Z-SCALE=({_v(1e3)},   0)\n" + Z)
        out.append(f"MODULE  ({b + 1:4d})\nMATERIAL(   0)\nSURFACE ({s:4d}), SIDE POINTER=(-1)\n"
                   + "".join(f"BODY    ({k:4d})\n" for k in range(1, b + 1)) + Z)
    out.append(END)
    return "".join(out)


@pytest.mark.parametrize("nx, ny, module, bad", [(8, 31, False, False), (10, 25, False, True),
                                                 (8, 31, True, False), (3, 83, True, True)])
def test_more_daughters_than_geomin_can_store_is_an_error(nx, ny, module, bad):
    """GEOMIN no comprueba KDGHT(KB, NXG): con 249 hijas o más la tabla se corrompe sin error."""
    geo = compile_geometry(parse_geo_text(_grid(nx, ny, module), "g.geo"))
    assert geo.ok
    errors = [d.message for d in validate(geo).errors]
    assert bool(errors) == bad and bool(table_overflows(geo)) == bad
    if bad:
        assert f"solo admite {NXG - 2}" in errors[0]


@pytest.mark.oracle
def test_daughter_overflow_really_breaks_pengeom(tmp_path):
    from argus.oracle.probe import find_probe, run_probe
    from argus.pengeom.locate import Locator

    if find_probe() is None:
        pytest.skip("Sonda PENGEOM no compilada")
    rng = np.random.default_rng(0)
    for nx, ny, module, bad in ((8, 31, True, False), (3, 83, True, True)):
        text = _grid(nx, ny, module)
        p = tmp_path / f"g{nx}.geo"
        p.write_text(text, encoding="latin-1")
        pts = np.column_stack([rng.uniform(0, nx, 3000), rng.uniform(0, ny, 3000), rng.uniform(0, 1, 3000)])
        res = run_probe(p, points=np.hstack([pts, np.tile([0, 0, 1.0], (3000, 1))]))
        geo = compile_geometry(parse_geo_text(text, str(p)))
        differ = int((Locator(geo).locate(pts)[0] != res.locate_body).sum())
        assert res.status.iretrn == 0 and (differ > 0) == bad


def test_edits_cannot_create_a_daughter_overflow():
    old = compile_geometry(parse_geo_text(_grid(8, 31, True), "g.geo"))
    new = compile_geometry(parse_geo_text(_grid(3, 83, True), "g.geo"))
    assert not new_overflows(old, old)
    msgs = new_overflows(old, new)
    assert msgs and "Envolver" in msgs[0]
    assert not new_overflows(new, new)  # si ya estaba así, se puede seguir editando lo demás
