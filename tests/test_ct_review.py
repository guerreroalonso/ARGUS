"""Importador de TAC (revisión de la 0.8): curvas de hojas de cálculo, comprobaciones de la calibración,
densidad media de cada tejido, ficha sin datos del paciente, remuestreo fuera del TAC, escritura sin
restos y cuerpos pequeños dentro de la caja CT."""

import json

import numpy as np
import pytest

pytest.importorskip("pydicom")

from argus.ct.calibration import parse_curve_text, penhmat_steps, preset  # noqa: E402
from argus.ct.grid import make_grid, resample  # noqa: E402
from argus.ct.importer import (  # noqa: E402
    check_ct_box,
    discard_written,
    read_sidecar,
    sidecar_tissues,
)
from argus.pengeom.compiler import compile_geometry  # noqa: E402
from argus.pengeom.parser import parse_geo_text  # noqa: E402
from test_ct_import import BOX_MODULE, _geometry_with_ct, _import, _volume  # noqa: E402


# -- curvas HU → densidad ----------------------------------------------------------------------------------
@pytest.mark.parametrize("text,pts", [
    ("HU;g/cm3\n−1000;0,00121\n0;1\n1.500;1,85\n", [(-1000.0, 0.00121), (0.0, 1.0), (1500.0, 1.85)]),
    ('"HU","rho"\n"-1000","0.00121"\n"0","1.0"\n"1,500","1.85"\n', [(-1000.0, 0.00121), (0.0, 1.0),
                                                                        (1500.0, 1.85)]),
    ("-1000\t0,001\n0\t1\n3000\t2,8\n", [(-1000.0, 0.001), (0.0, 1.0), (3000.0, 2.8)]),
])
def test_curve_text_from_spreadsheets(text, pts):
    assert parse_curve_text(text) == pts


@pytest.mark.parametrize("text", [
    "-1000;;0,001\n0;1\n1500;1,8\n",  # celda vacía entre el HU y la densidad
    "-1000\n0 1\n1500 1.8\n",  # una fila con un solo número
    "-1000 0.001\n0 1.964\n1500 1,8\n",  # «1.964» y «1,8»: marcas decimales mezcladas
    "-1000 0.001\n",  # un solo punto
])
def test_curve_rows_that_cannot_be_read_are_errors(text):
    with pytest.raises(ValueError):
        parse_curve_text(text)


def test_calibration_problems_and_warnings():
    cal = preset("schneider5")
    assert not cal.problems()
    cal.tissues[2].composition = {1: 0.1, 8: -0.9}
    cal.tissues[3].composition = None
    cal.tissues[3].penelope_id = 301
    probs = cal.problems()
    assert any("positivas" in p for p in probs) and any("1 a 300" in p for p in probs)
    cal = preset("schneider5")
    cal.curve = [(-500.0, 0.5), (500.0, 1.3)]
    w = cal.warnings()
    assert any("-500 HU" in x and "0,5 g/cm³" in x for x in w) and any("500 HU" in x for x in w)


# -- densidad media, ficha y materiales.txt --------------------------------------------------------------
def test_the_mat_density_is_the_mean_of_its_voxels(tmp_path):
    """penhmain resta la pérdida continua con la densidad del .mat (CTTRACK): la del .mat es la media."""
    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    card = read_sidecar(folder / "tac.ct")
    tissues = sidecar_tissues(card)
    ct = built.ct
    for i, t in enumerate(imp.calibration.tissues):
        n = imp.numbers[i]
        sel = ct.material == n
        if not sel.any():
            continue
        mean = float(ct.density[sel].mean())
        assert card["calibracion"]["tejidos"][i]["densidad_media"] == pytest.approx(mean, rel=1e-5)
        assert tissues[n].voxel_density == pytest.approx(mean, rel=1e-5)
        steps = penhmat_steps(tissues[n], density=tissues[n].voxel_density)
        if t.composition:
            assert f"Densidad: {tissues[n].voxel_density:.6g} g/cm³" in steps


def test_the_card_has_no_patient_or_study_data(tmp_path):
    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    card = json.loads((folder / "tac.ct.json").read_text(encoding="utf-8"))
    assert set(card["serie"]) <= {"fabricante", "modelo", "kVp", "cortes", "filas", "columnas", "pixel_mm",
                                  "distancia_entre_cortes_mm", "grosor_mm", "posicion_paciente"}
    text = json.dumps(card, ensure_ascii=False)
    assert "Estudio de prueba" not in text and "20260101" not in text  # descripción y fecha del estudio


# -- remuestreo ----------------------------------------------------------------------------------------------
def test_outside_the_field_is_air_and_past_the_last_slice_is_the_patient(tmp_path):
    from argus.ct.grid import GridSpec

    vol = _volume(tmp_path)
    lo, hi = vol.bounds()
    v = np.array(vol.spacing())
    # Una columna de vóxeles fuera del campo en x: aire.
    g = make_grid(lo - np.array([v[0], 0, 0]), hi, v)
    assert np.allclose(resample(vol, g)[:, :, 0], -1000.0)
    # Una capa que el TAC cubre solo a medias en z (la rejilla centrada sobresale): el paciente sigue, no es
    # medio aire.
    nx, ny, _ = vol.shape
    g = GridSpec(np.array([lo[0], lo[1], hi[2] - 0.5 * v[2]]), v, (nx, ny, 1))
    assert np.allclose(resample(vol, g)[0], vol.hu[-1].astype(np.float32))


# -- escritura ---------------------------------------------------------------------------------------------
def test_a_failed_write_leaves_nothing(tmp_path, monkeypatch):
    from argus.ct import importer

    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(importer, "write_ct_file", full)
    with pytest.raises(OSError):
        imp.write(folder, built)
    assert list(folder.iterdir()) == []
    monkeypatch.undo()
    w = imp.write_temp(folder, built)  # cancelar después de escribir: se borran los temporales
    discard_written(w)
    assert list(folder.iterdir()) == []


def test_the_ct_is_written_in_blocks_with_the_same_bytes(tmp_path):
    from argus.pengeom import ct as C

    rng = np.random.default_rng(7)
    mat = rng.integers(0, 6, (9, 70, 90)).astype(np.int32)
    den = rng.uniform(0.0, 2.5, mat.shape)
    old = C.WRITE_CHUNK
    try:
        C.WRITE_CHUNK = 1000  # varios bloques
        C.write_ct_file(tmp_path / "a.ct", "t", (9.0, 7.0, 0.9), mat, den)
    finally:
        C.WRITE_CHUNK = old
    assert (tmp_path / "a.ct").read_bytes() == C.format_ct("t", (9.0, 7.0, 0.9), mat, den)


# -- cuerpos pequeños ------------------------------------------------------------------------------------------
def _sphere(c) -> str:
    """Una esfera de 1 mm de radio centrada en ``c`` (cm), como cuerpo «9» (bolita)."""
    shift = "".join(f"{a}-SHIFT=({v:+.15E},   0)\n" for a, v in zip("XYZ", c))
    return ("SURFACE (  99)   esfera\nINDICES=( 1, 1, 1, 0,-1)\n"
            "X-SCALE=(+1.000000000000000E-01,   0)\nY-SCALE=(+1.000000000000000E-01,   0)\n"
            "Z-SCALE=(+1.000000000000000E-01,   0)\n" + shift + "0" * 64 + "\n"
            "BODY    (   9)   bolita\nMATERIAL(   1)\nSURFACE (  99), SIDE POINTER=(-1)\n" + "0" * 64 + "\n")


def test_a_tiny_body_inside_the_ct_box_is_found(tmp_path):
    from argus.pengeom.locate import Locator

    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    c = built.frame.to_lab((np.array(built.ct.size) * np.array([0.37, 0.61, 0.43]))[None, :])[0]
    text = BOX_MODULE.replace("{kind}", "MODULE")
    text = text.replace("MODULE  (   1)   caja", _sphere(c) + "MODULE  (   1)   caja").replace(
        "SURFACE (   6), SIDE POINTER=(-1)\n", "SURFACE (   6), SIDE POINTER=(-1)\nBODY    (   9)\n", 1)
    geo = compile_geometry(parse_geo_text(text, str(folder / "g.geo")), base_dir=folder)
    assert geo.ok, [d.message for d in geo.diagnostics][:3]
    kb = geo.find_body("1").index
    check = check_ct_box(geo, Locator(geo), built.frame, kb)
    assert not check.ok and "«9»" in check.message
    assert _geometry_with_ct  # (el mismo módulo de pruebas que las demás)


def test_warnings_for_protons_with_the_ct():
    """penhmain (2025) usa la densidad del .mat para la pérdida continua dentro del CT: se avisa con las
    calibraciones de densidad fija o variable dentro de un mismo .mat."""
    pytest.importorskip("PySide6")
    from argus.ui.ct_import_dialog import hadron_warnings

    assert not hadron_warnings(preset("schneider5"))
    # Sin partir por densidad, el agua de densidad variable da alcances mal; partiéndola, solo se avisa de que
    # todo es agua.
    assert any("Agua" in w and "alcances" in w for w in hadron_warnings(preset("agua"), "none"))
    assert not any("alcances" in w for w in hadron_warnings(preset("agua"), "auto"))
    # Los tejidos de la lista de PENELOPE con composición conocida se generan con la opción 1 (densidad de sus
    # vóxeles): no tienen la densidad fija de la lista.
    assert not any("densidad fija" in w for w in hadron_warnings(preset("penelope")))
    cal = preset("penelope")
    cal.tissues[1].penelope_id = 250  # uno que ARGUS no sabe componer: opción 2, densidad de la lista
    assert any("densidad fija" in w for w in hadron_warnings(cal))


def test_materiales_txt_mentions_the_setup_time_with_protons(tmp_path):
    from argus.penhan.inputfile import parse_in_file
    from argus.penhan.materials import materials_table, materials_txt

    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    geo = _geometry_with_ct(folder, built)
    from conftest import CORPUS

    text = (CORPUS / "usuario/100Mev15-174/maniqui.in").read_bytes().decode("latin-1")
    text = text.replace("cubo-esfera.geo", "g.geo          ").replace(
        "MFNAME water.mat", "\n".join(f"MFNAME {t.mat_file}" for t in imp.calibration.tissues) + "\nMFNAME water.mat")
    (folder / "caso.in").write_bytes(text.encode("latin-1"))
    doc = parse_in_file(folder / "caso.in")
    rows = materials_table(doc, geo, folder)
    with_p = materials_txt(rows, "caso.in", geo, "g.geo", hadrons=True)
    # 33 s por elemento de cada material del TAC: 3 + 5 + 4 + 5 + 6 = 23 elementos, unos 13 minutos.
    assert "13 minutos antes de la primera lluvia" in with_p and "¿Cambiar Fcb y Wcb" in with_p
    assert "CTTRACK" in with_p and "pulmón" in with_p  # sin partir por densidad, se avisa (ADR-010, ADR-011)
    assert "minutos antes" not in materials_txt(rows, "caso.in", geo, "g.geo", hadrons=False)
    fixed = materials_txt(rows, "caso.in", geo, "g.geo", hadrons=True, cttrack_fixed=True)
    assert "con CTTRACK corregido" in fixed and "pulmón" not in fixed
