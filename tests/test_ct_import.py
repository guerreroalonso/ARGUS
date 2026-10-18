"""Importar TACs: ficheros .ct, series DICOM, calibración, rejilla y caja CT en la geometría."""

import json

import numpy as np
import pytest

from argus.pengeom import ct as C
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from conftest import CORPUS

pydicom = pytest.importorskip("pydicom")

from argus.ct.calibration import MNIS, SCHNEIDER_CURVE, parse_curve_text, preset  # noqa: E402
from argus.ct.dicom import DicomError, read_series, scan_folder  # noqa: E402
from argus.ct.grid import auto_crop, make_grid, resample, suggest_voxel  # noqa: E402
from argus.ct.importer import (  # noqa: E402
    CtImport,
    add_ctbox,
    check_ct_box,
    ct_containers,
    ct_name_problem,
    plan_materials,
    read_sidecar,
    sidecar_tissues,
)
from argus.pengeom.edit import EditError  # noqa: E402
from dicom_synth import linear_hu, phantom_hu, write_series  # noqa: E402

Z = "0" * 64
EMPTY = "Geometria con TAC\n" + Z + "\nEND      " + "0" * 55 + "\n"


# -- ficheros .ct ------------------------------------------------------------------------------
def test_ct_writer_matches_pengeom_format():
    raw = (CORPUS / "penhan/emfield_ct/box.ct").read_bytes()
    ct = C.read_ct_bytes(raw)
    body = C.voxel_lines(ct.material, ct.density)
    ref = [ln.rstrip(b"\r") for ln in raw.split(b"\n")[3:3 + ct.nvoxels]]
    assert body.split(b"\n")[:-1] == ref  # mismas líneas que el ejemplo de penhan (I2,1P,E11.4)
    rng = np.random.default_rng(0)
    m = rng.integers(0, 99, 5000)
    d = np.concatenate([rng.uniform(0, 3, 4988), [0.0, 1.0, 10.0, 9.99995, 9.99994, 1e-3, 0.00121, 99999.5,
                                                  1e-99, 5e-5, 0.1, 1.0e5]])
    assert C.voxel_lines(m, d) == "".join(f"{a:2d}{b:11.4E}\n" for a, b in zip(m, d)).encode()


def test_ct_fast_and_slow_readers_agree(tmp_path):
    rng = np.random.default_rng(1)
    mat = rng.integers(0, 5, (4, 3, 5)).astype(np.int32)
    den = rng.uniform(0.001, 2.5, (4, 3, 5))
    data = C.format_ct("prueba", (2.5, 1.5, 4.0), mat, den)
    a = C.read_ct_bytes(data)
    assert a.shape == (5, 3, 4) and np.array_equal(a.material, mat)
    assert np.allclose(a.density, den, rtol=1e-4)
    # Otras disposiciones (como las lee GEOMIN, una lectura por vóxel): comentarios, D, comas, saltos.
    lines = data.decode().splitlines()
    odd = lines[:3] + [lines[3] + "   ! primer vóxel", lines[4].replace("E", "D"), lines[5].replace(" 0", ",0", 1)]
    first, second = lines[6].split()
    odd += [first, second] + lines[7:]
    b = C.read_ct_text("\n".join(odd) + "\n")
    assert np.array_equal(b.material, a.material) and np.array_equal(b.density, a.density)


def test_ct_reader_rejects_what_gfortran_rejects():
    """«!» donde se espera un número, «/» antes de tiempo y un material «1.0»: PENGEOM falla (o lee basura)."""
    mat = np.ones((2, 2, 2), dtype=np.int32)
    lines = C.format_ct("t", (1.0, 1.0, 1.0), mat, np.full((2, 2, 2), 1.0)).decode().splitlines()
    good = C.read_ct_text("\n".join(lines) + "\n")
    assert good.material.sum() == 8
    for bad, what in ((lines[:3] + ["! comentario"] + lines[3:], "!"),
                      (lines[:3] + [" 1 /"] + lines[4:], "/"),
                      (lines[:3] + [" 1.0 1.0000E+00"] + lines[4:], "1.0"),
                      (lines[:5] + [" 1E0 1.0000E+00"] + lines[6:], "1E0")):
        with pytest.raises(C.CtFormatError) as err:
            C.read_ct_text("\n".join(bad) + "\n")
        assert "Vóxel" in str(err.value), what
    # Un comentario o una «/» después de los dos valores de la línea dan igual (el resto se descarta).
    ok = lines[:3] + [lines[3] + "  ! agua", lines[4] + " /"] + lines[5:]
    assert np.array_equal(C.read_ct_text("\n".join(ok) + "\n").material, good.material)


def test_integer_material_check_is_fast_for_big_files():
    import time

    rng = np.random.default_rng(3)
    mat = rng.integers(0, 6, (60, 200, 200)).astype(np.int32)
    raw = C.format_ct("grande", (20.0, 20.0, 6.0), mat, np.full(mat.shape, 1.0))
    t0 = time.perf_counter()
    ct = C.read_ct_bytes(raw)
    assert time.perf_counter() - t0 < 5.0 and np.array_equal(ct.material, mat)
    body_start = raw.index(b"\n", raw.index(b"\n", raw.index(b"\n") + 1) + 1) + 1
    assert C._integer_materials(raw[body_start:])
    assert not C._integer_materials(raw[body_start:].replace(b" 3 ", b"3.0 ", 1))


def test_ct_cache_reuses_until_the_file_changes(tmp_path):
    C.clear_cache()
    p = tmp_path / "c.ct"
    mat = np.ones((2, 2, 2), dtype=np.int32)
    C.write_ct_file(p, "t", (1, 1, 1), mat, np.full((2, 2, 2), 1.0))
    a = C.load_ct(p)
    assert C.load_ct(p) is a and not a.material.flags.writeable
    import os
    import time

    C.write_ct_file(p, "t", (1, 1, 1), mat * 2, np.full((2, 2, 2), 1.0))
    os.utime(p, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
    assert C.load_ct(p).max_material == 2


# -- series DICOM ----------------------------------------------------------------------------------
ORIENTATIONS = {
    "axial": (1, 0, 0, 0, 1, 0), "pies_primero": (-1, 0, 0, 0, 1, 0), "prono": (-1, 0, 0, 0, -1, 0),
    "sagital": (0, 1, 0, 0, 0, -1), "coronal": (1, 0, 0, 0, 0, -1),
}


@pytest.mark.parametrize("name", list(ORIENTATIONS) + ["oblicuo", "mejorado", "reescala"])
def test_series_are_read_in_patient_coordinates(tmp_path, name):
    kw = {}
    if name in ORIENTATIONS:
        kw["iop"] = ORIENTATIONS[name]
    elif name == "oblicuo":
        a = 0.3
        kw["iop"] = (np.cos(a), np.sin(a), 0, -np.sin(a), np.cos(a), 0)
    elif name == "mejorado":
        kw["enhanced"] = True
    else:
        kw.update(slope=0.5, intercept=-1000.0)
    write_series(tmp_path, linear_hu, **kw)
    series = scan_folder(tmp_path)
    assert len(series) == 1 and series[0].ok, series[0].problems
    vol = read_series(series[0])
    X, Y, Zc = np.meshgrid(vol.x, vol.y, vol.z, indexing="ij")
    P = np.stack([X, Y, Zc], -1) @ vol.rotation.T
    exact = (10.0 * P[..., 0] + 3.0 * P[..., 1] + 0.5 * P[..., 2]).transpose(2, 1, 0)
    assert np.abs(vol.hu - exact).max() <= 0.5 + 1e-9  # la función de prueba redondea a HU enteros
    assert all(np.all(np.diff(c) > 0) for c in (vol.x, vol.y, vol.z))
    assert np.allclose(vol.rotation, np.eye(3)) == (name != "oblicuo")
    assert "PatientName" not in json.dumps(vol.info, default=str)


def test_series_problems_are_reported(tmp_path):
    write_series(tmp_path / "tilt", phantom_hu, shift_last=(0.0, 1.0, 0.0))
    write_series(tmp_path / "mr", phantom_hu, modality="MR")
    write_series(tmp_path / "gap", phantom_hu, z_offsets=[0, 3, 6, 9, 15, 18, 21, 24])
    found = {s.folder.name: s for s in scan_folder(tmp_path)}
    assert "gantry" in found["tilt"].problems[0]
    assert "No es un TAC" in found["mr"].problems[0]
    assert found["gap"].ok and "Falta" in found["gap"].notes[0]
    with pytest.raises(DicomError):
        read_series(found["tilt"])
    (tmp_path / "otro.txt").write_text("no soy DICOM")
    assert len(scan_folder(tmp_path)) == 3


# -- calibración --------------------------------------------------------------------------------------
def test_calibration_presets():
    s5 = preset("schneider5")
    assert len(s5.isotopes()) == 17 <= MNIS
    assert len(preset("penelope").isotopes()) > MNIS
    assert len(preset("agua").isotopes()) == 10
    hu = np.array([-2000, -951, -950, -121, -120, -23, -22, 0, 119, 120, 5000])
    assert s5.tissue_index(hu).tolist() == [0, 0, 1, 1, 2, 2, 3, 3, 3, 4, 4]
    for h, rho in SCHNEIDER_CURVE:
        assert s5.density(h) == pytest.approx(rho)
    assert s5.density(-3000) == pytest.approx(0.00121) and s5.density(4000) == pytest.approx(2.8)
    for t in s5.tissues:
        assert sum(t.composition.values()) == pytest.approx(1.0, abs=1e-5)
        assert set(t.composition) <= {1, 6, 7, 8, 15, 18, 20}
    bad = s5.copy()
    bad.curve[1] = (-2000.0, 1.0)
    bad.tissues[2].density = 0.0
    assert len(bad.problems()) == 2


@pytest.mark.parametrize("text", ["HU;Densidad\n-1000;0,001\n0;1\n1500;1,8\n",
                                  "\ufeffHU\tRho\r\n-1000\t0,001\r\n0\t1\r\n1500\t1,8\r\n",
                                  "-1000 0.001\n0 1.0\n1500 1.8\n", "# curva\n-1000,0.001\n0,1\n1500,1.8\n"])
def test_curve_files_from_spreadsheets(text):
    assert parse_curve_text(text) == [(-1000.0, 0.001), (0.0, 1.0), (1500.0, 1.8)]


# -- rejilla ---------------------------------------------------------------------------------------
def _volume(tmp_path, **kw):
    write_series(tmp_path, phantom_hu, rows=40, cols=36, nslices=20, spacing=(1.0, 1.25), dz=2.0,
                 origin=(-20, -18, 10), **kw)
    return read_series(scan_folder(tmp_path)[0])


def test_resampling_is_exact_on_the_original_grid_and_a_mean_on_coarser_ones(tmp_path):
    vol = _volume(tmp_path)
    lo, hi = vol.bounds()
    g = make_grid(lo, hi, vol.spacing())
    assert g.shape == vol.shape and np.allclose(g.lo, lo)
    assert np.array_equal(resample(vol, g), vol.hu.astype(np.float32))
    g2 = make_grid(lo, hi, np.array(vol.spacing()) * 2)
    r2 = resample(vol, g2)
    block = vol.hu.astype(float).reshape(10, 2, 20, 2, 18, 2).mean(axis=(1, 3, 5))
    assert np.allclose(r2, block)
    # Lo que queda fuera del TAC es aire.
    g3 = make_grid(lo - 5.0, hi + 5.0, [2.5, 2.0, 4.0])
    r3 = resample(vol, g3)
    assert r3[0, 0, 0] == pytest.approx(-1000.0)
    lo_c, hi_c = auto_crop(vol)
    assert np.all(lo_c >= lo - 1e-9) and np.all(hi_c <= hi + 1e-9)


def test_suggested_voxel_respects_penhmain_limits(tmp_path):
    vol = _volume(tmp_path)
    lo, hi = vol.bounds()
    assert np.allclose(suggest_voxel(vol, lo, hi), vol.spacing())
    v = suggest_voxel(vol, lo, hi, max_voxels=2000)
    assert make_grid(lo, hi, v).nvoxels <= 2000
    big_hi = lo + np.array([100.0, 100.0, 2000.0])
    assert make_grid(lo, big_hi, suggest_voxel(vol, lo, big_hi)).shape[2] <= C.NDD


# -- importar ---------------------------------------------------------------------------------------
def test_material_plan():
    cal = preset("agua")
    plan = plan_materials(cal, ["water.mat", "air.mat"])
    assert plan.numbers == [2, 1] and not plan.new
    plan = plan_materials(preset("schneider5"), ["water.mat"])
    assert plan.numbers == [2, 3, 4, 5, 6] and len(plan.new) == 5 and not plan.problems
    plan = plan_materials(preset("schneider5"), [f"m{i}.mat" for i in range(7)])
    assert plan.problems
    plan = plan_materials(preset("schneider5"), ["water.mat"], reuse=[None, None, None, 1, None])
    assert plan.numbers == [2, 3, 4, 1, 5]


def test_ct_names():
    assert ct_name_problem("tac.ct") is None
    assert ct_name_problem("paciente_1234.ct") and ct_name_problem("mi tac.ct") and ct_name_problem("")


def _import(tmp_path, mode="centro", vol=None):
    vol = vol if vol is not None else _volume(tmp_path / "dcm")
    lo, hi = auto_crop(vol)
    g = make_grid(lo, hi, [2.5, 2.5, 2.5])
    cal = preset("schneider5")
    plan = plan_materials(cal, ["water.mat"])
    imp = CtImport(vol, g, cal, plan.numbers, mode, ct_name="tac.ct", title="TAC de prueba")
    return imp, imp.build()


def test_import_writes_the_ct_and_its_card(tmp_path):
    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    ctp, js = imp.write(folder, built)
    ct = C.read_ct_file(ctp)
    assert ct.shape == built.ct.shape and np.array_equal(ct.material, built.ct.material)
    assert set(ct.materials()) <= {2, 3, 4, 5, 6}
    card = read_sidecar(ctp)
    assert card["fichero_ct"] == "tac.ct" and card["voxeles"] == list(built.ct.shape)
    tissues = sidecar_tissues(card)
    assert tissues[3].name == "Pulmón" and tissues[3].composition
    # La masa de cada tejido cuadra con el fichero.
    vox = float(np.prod(ct.voxel_size))
    for i, n in enumerate(imp.numbers):
        assert card["calibracion"]["tejidos"][i]["masa_g"] == pytest.approx(ct.density[ct.material == n].sum() * vox,
                                                                            rel=1e-4)


def _geometry_with_ct(folder, built, text=EMPTY, container=None):
    doc = parse_geo_text(text, str(folder / "g.geo"))
    geo = compile_geometry(doc, base_dir=folder)
    add_ctbox(doc, geo, "tac.ct", built.rotation, built.shift, container, "TAC", "TAC de prueba")
    (folder / "g.geo").write_text(write_geo_text(doc))
    return compile_geometry(load_geometry(folder / "g.geo"), base_dir=folder)


def test_the_ct_extent_is_its_whole_box_even_with_void_voxels(tmp_path):
    """Pegar algo al CT es pegarlo a las caras de la caja, no al contorno del paciente (vóxeles vacíos alrededor)."""
    from argus.pengeom.extent import element_extent
    from argus.render.raytrace import RayTracer

    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    ct = built.ct
    mat = np.array(ct.material, dtype=np.int32)
    mat[:, :, :2] = 0  # vacío en los bordes (p. ej. fuera del paciente)
    mat[:, :, -2:] = 0
    mat[:, :2, :] = 0
    C.write_ct_file(folder / "tac.ct", ct.title, ct.size, mat, np.where(mat > 0, ct.density, 0.0))
    geo = _geometry_with_ct(folder, built)
    assert geo.ok and geo.ct is not None and geo.ct.has_void
    from argus.pengeom.extent import analytic_box

    lo, hi = element_extent(geo, RayTracer(geo), geo.ct_body, (np.full(3, -1e3), np.full(3, 1e3)))
    a_lo, a_hi = analytic_box(geo, geo.ct_body)
    assert np.allclose(lo, a_lo) and np.allclose(hi, a_hi)


def test_resume_with_a_ct_warns_when_the_ct_is_newer_than_the_dump(tmp_path):
    """Con RESUME, penhmain toma del volcado la caja CT y las densidades de los vóxeles (no las del .ct)."""
    import os

    from argus.core.diagnostics import Severity
    from argus.penhan.inputfile import parse_in_file
    from argus.penhan.validate import validate_case
    from test_simcheck import dump_text

    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    geo = _geometry_with_ct(folder, built)
    text = (CORPUS / "usuario/100Mev15-174/maniqui.in").read_bytes().decode("latin-1")
    (folder / "caso.in").write_bytes(text.replace("cubo-esfera.geo", "g.geo          ").encode("latin-1"))
    doc = parse_in_file(folder / "caso.in")
    (folder / "dump1.dmp").write_text(dump_text(title=doc.model.title))
    old = (folder / "tac.ct").stat().st_mtime - 3600
    os.utime(folder / "tac.ct", (old, old))

    def ct_msgs():
        return [d for d in validate_case(doc, geo, folder) if "RESUME con un TAC" in d.message]

    assert ct_msgs() and ct_msgs()[0].severity == Severity.INFO
    new = (folder / "dump1.dmp").stat().st_mtime + 60
    os.utime(folder / "tac.ct", (new, new))  # el TAC se ha vuelto a importar después del volcado
    m = ct_msgs()[0]
    assert m.severity == Severity.WARNING and "empiece sin RESUME" in m.message


BOX_MODULE = ("Caja de aire\n" + Z + "\n" + "".join(
    f"SURFACE ({k:4d})\nINDICES=( 0, 0, 0, 0, 0)\n     A{ax}=(+1.000000000000000E+00,   0)\n"
    f"     A0=({'+' if s > 0 else '-'}2.000000000000000E+01,   0)\n{Z}\n"
    for k, (ax, s) in enumerate([("X", 1), ("X", -1), ("Y", 1), ("Y", -1), ("Z", 1), ("Z", -1)], start=1))
    + "{kind}  (   1)   caja\nMATERIAL(   1)\n" + "".join(
        f"SURFACE ({k:4d}), SIDE POINTER=({'+1' if k % 2 else '-1'})\n" for k in range(1, 7)) + Z + "\n"
    + "END      " + "0" * 55 + "\n")


@pytest.mark.parametrize("kind", ["MODULE", "BODY  "])
def test_ct_box_inside_a_module_or_a_body(tmp_path, kind):
    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    text = BOX_MODULE.replace("{kind}", kind)
    doc = parse_geo_text(text, str(folder / "g.geo"))
    geo = compile_geometry(doc, base_dir=folder)
    assert geo.ok, [d.message for d in geo.diagnostics.errors]
    kb = geo.find_body("   1").index
    assert kb in ct_containers(geo)
    from argus.pengeom.locate import Locator

    assert check_ct_box(geo, Locator(geo), built.frame, kb).ok
    geo2 = _geometry_with_ct(folder, built, text, kb)
    assert geo2.ok and geo2.ct is not None
    with pytest.raises(EditError):
        add_ctbox(geo2.document, geo2, "otro.ct", built.rotation, built.shift, None)


def test_ct_box_overlapping_something_is_refused(tmp_path):
    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    geo = compile_geometry(parse_geo_text(BOX_MODULE.replace("{kind}", "BODY  "), str(folder / "g.geo")),
                           base_dir=folder)
    from argus.pengeom.locate import Locator

    check = check_ct_box(geo, Locator(geo), built.frame, geo.nbodys)  # en la envolvente: cortaría la caja
    assert not check.ok and "se sale" in check.message


@pytest.mark.oracle
@pytest.mark.parametrize("mode", ["centro", "dicom", "oblicuo"])
def test_imported_ct_agrees_with_pengeom(tmp_path, mode):
    from argus.oracle.probe import find_probe, run_probe
    from argus.pengeom.locate import Locator

    if find_probe() is None:
        pytest.skip("Sonda PENGEOM no compilada")
    vol = None
    if mode == "oblicuo":
        a = 0.4
        write_series(tmp_path / "dcm", phantom_hu, rows=30, cols=28, nslices=12, spacing=(1.2, 1.1), dz=2.5,
                     origin=(-15, -12, 12), iop=(np.cos(a), np.sin(a), 0, -np.sin(a), np.cos(a), 0))
        vol = read_series(scan_folder(tmp_path / "dcm")[0])
    imp, built = _import(tmp_path, "centro" if mode == "oblicuo" else mode, vol)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    geo = _geometry_with_ct(folder, built)
    assert geo.ok
    fr = C.ct_frame(geo)
    assert np.allclose(fr.rotation, built.rotation) and np.allclose(fr.shift, built.shift)
    rng = np.random.default_rng(3)
    pts = fr.to_lab(rng.uniform(-0.1, 1.1, (20000, 3)) * np.array(fr.size))
    d = rng.normal(size=pts.shape)
    d /= np.linalg.norm(d, axis=1)[:, None]
    res = run_probe(folder / "g.geo", ct_points=np.hstack([pts, d]))
    assert res.status.iretrn == 0
    ib, _ = Locator(geo).locate(pts, d)
    inct = ib == geo.ct_body
    mat, den = C.voxel_values(geo, pts, d, mask=inct)
    cv = res.ct_voxels
    assert np.array_equal(ib, cv["ibody"]) and inct.sum() > 5000
    assert np.array_equal(mat[inct], cv["mat"][inct])
    assert np.array_equal(den[inct], cv["dens"][inct])


# -- penhmat de verdad (si está: ARGUS_PENHMAT = el ejecutable, ARGUS_PENHAN_DB = la carpeta database) --------
def _penhmat():
    import os

    exe, db = os.environ.get("ARGUS_PENHMAT"), os.environ.get("ARGUS_PENHAN_DB")
    if not exe or not db:
        pytest.skip("penhmat no disponible (ARGUS_PENHMAT y ARGUS_PENHAN_DB)")
    return exe, db


@pytest.mark.parametrize("key,index", [("schneider5", 1), ("schneider5", 4), ("agua", 0)])
def test_materiales_txt_answers_work_with_the_real_penhmat(tmp_path, key, index):
    """Las respuestas de materiales.txt, tal cual, generan el .mat (con la densidad media de los vóxeles)."""
    import subprocess

    from argus.ct.calibration import penhmat_input

    exe, db = _penhmat()
    t = preset(key).tissues[index]
    for sub in ("pdfiles", "pdhan", "ENDF_data"):
        (tmp_path / sub).symlink_to(f"{db}/{sub}")
    rho = 1.0123 if t.composition else None
    run = subprocess.run([exe], input=penhmat_input(t, "prueba.mat", rho), capture_output=True, text=True,
                         cwd=tmp_path, timeout=600)
    mat = tmp_path / "prueba.mat"
    assert run.returncode == 0 and mat.is_file(), run.stdout[-2000:] + run.stderr[-2000:]
    head = mat.read_text(encoding="latin-1", errors="replace")[:4000]
    if rho is not None:
        assert "1.01230000E+00" in head  # la densidad que se le ha dado
