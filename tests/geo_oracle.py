"""Ayudas comunes de las pruebas del editor: releer, compilar y comparar con PENGEOM real."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.edit import compile_checked
from argus.pengeom.locate import Locator
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text


def roundtrip(doc, parinp=None):
    """(texto, documento releído, geometría compilada) de un documento editado."""
    text = write_geo_text(doc)
    doc2 = parse_geo_text(text, doc.path)
    assert doc2.ok, [str(d) for d in doc2.diagnostics]
    return text, doc2, compile_checked(doc2, parinp)


def load_text(text: str, tmp_path: Path, name: str = "g.geo", parinp=None):
    path = tmp_path / name
    path.write_text(text, encoding="latin-1")
    doc = load_geometry(path)
    geo = compile_geometry(doc, parinp=parinp, base_dir=tmp_path)
    assert geo.ok, [str(d) for d in geo.diagnostics]
    return doc, geo


def materials(geo, pts, dirs=None):
    return Locator(geo).locate(np.asarray(pts, dtype=float), dirs)


def random_points(lo, hi, n, seed=0):
    rng = np.random.default_rng(seed)
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    pts = lo + rng.random((n, 3)) * (hi - lo)
    dirs = rng.normal(size=pts.shape)
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    return pts, dirs


def assert_pengeom_agrees(text: str, tmp_path: Path, pts, dirs, parinp=None, name: str = "editado.geo"):
    """PENGEOM real lee ``text`` con las mismas tablas que ARGUS y LOCATE da los mismos cuerpos."""
    from argus.oracle.compare import compare_tables
    from argus.oracle.probe import run_probe

    path = tmp_path / name
    path.write_text(text, encoding="latin-1")
    geo = compile_geometry(load_geometry(path), parinp=parinp, base_dir=tmp_path)
    assert geo.ok, [str(d) for d in geo.diagnostics]
    res = run_probe(path, parinp=parinp, points=np.hstack([pts, dirs]))
    assert res.status.ok, res.status.reason
    cmp = compare_tables(geo, res.tables)
    assert cmp.ok, cmp.differences[:5]
    ib, _ = Locator(geo).locate(pts, dirs)
    assert np.array_equal(ib, res.locate_body)
    return geo, res


def step_disagreements(text: str, tmp_path: Path, starts, dirs, smax: float, parinp=None, ds: float = 1e-3,
                       near: float = 2e-3, name: str = "step.geo") -> tuple[int, float]:
    """(rayos, longitud mayor en cm) en los que STEP de PENGEOM real deja la partícula en otro material que LOCATE
    (a más de ``near`` de un cambio de material).

    STEP es lo que usa penhmain para transportar: si dos superficies distintas coinciden, puede cruzar una sin
    enterarse de la otra y seguir en el cuerpo equivocado; LOCATE no lo ve.
    """
    from argus.oracle.probe import run_probe

    path = tmp_path / name
    path.write_text(text, encoding="latin-1")
    geo = compile_geometry(load_geometry(path), parinp=parinp, base_dir=tmp_path)
    assert geo.ok, [str(d) for d in geo.diagnostics]
    starts = np.asarray(starts, dtype=float)
    dirs = np.asarray(dirs, dtype=float)
    res = run_probe(path, parinp=parinp, tables=False, rays=np.hstack([starts, dirs]), max_segments=50000)
    assert res.status.ok, res.status.reason
    loc = Locator(geo)
    s_grid = np.arange(0.0, smax, ds)
    bad, worst = 0, 0.0
    for k, ray in enumerate(res.rays):
        seg_s = np.r_[0.0, ray.s]
        seg_m = np.r_[ray.mat0, ray.mat]
        m_step = seg_m[np.searchsorted(seg_s, s_grid, side="right") - 1]
        ib, m_loc = loc.locate(starts[k][None, :] + s_grid[:, None] * dirs[k][None, :])
        # La sonda deja de anotar cuando la partícula llega a un material 0 (el vacío de fuera, la envolvente): se
        # compara hasta ahí y, si STEP sigue en materia después, es un error.
        out = np.flatnonzero((ib < 1) | (ib > geo.nbodys) | (m_loc == 0))
        s_out = float(s_grid[out[0]]) if len(out) else np.inf
        keep = s_grid < s_out - near
        wrong = (m_step != m_loc) & keep
        changes = np.r_[ray.s, s_grid[1:][np.diff(m_loc) != 0]]
        for s_t in changes[changes < smax + near]:
            wrong &= np.abs(s_grid - s_t) >= near
        late = float(ray.s[-1]) - s_out if len(ray.s) and ray.s[-1] > s_out + near and ray.s[-1] < 1e29 else 0.0
        if wrong.any() or late > 0:
            bad += 1
            worst = max(worst, float(wrong.sum()) * ds, late)
    return bad, worst
