"""Fallos de la novena revisión del importador de TAC (0.16.3): vóxeles vacíos por el volumen parcial, materiales del
.in que reutiliza el TAC (no son tejidos), dos tejidos con el mismo .mat nuevo, curvas de Excel en cp1252, el mensaje
de «¿cabe la caja?» y el aviso de las fracciones en masa."""

from __future__ import annotations

import codecs
import json

import numpy as np
import pytest

from argus.ct.calibration import preset, read_curve_file
from argus.ct.dicom import HuVolume
from argus.ct.grid import make_grid
from argus.ct.importer import CtImport, add_ctbox, plan_materials, read_sidecar, sidecar_tissues
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text

Z = "0" * 64


# ---------------------------------------------------------------------------------------------------------------------
# 1. Un tejido que solo aparece al promediar (volumen parcial) no es vacío
# ---------------------------------------------------------------------------------------------------------------------

def _water_cylinder() -> HuVolume:
    """Un cilindro de agua (0 HU) en aire, con píxeles de 1 mm y cortes de 2 mm: sin pulmón, grasa ni hueso."""
    nz, ny, nx = 6, 40, 40
    x = np.arange(nx) * 1.0 - 19.5
    y = np.arange(ny) * 1.0 - 19.5
    hu = np.full((nz, ny, nx), -1000.0, dtype=np.float32)
    hu[:, (x[None, :] ** 2 + y[:, None] ** 2) < 15.3 ** 2] = 0.0
    return HuVolume(hu=hu, x=x, y=y, z=np.arange(nz) * 2.0, rotation=np.eye(3), info={})


def _present_raw(cal, vol) -> list[bool]:
    """Los tejidos que hay en el TAC según sus HU sin promediar (lo que mira el diálogo)."""
    idx = cal.tissue_index(np.unique(vol.hu))
    return [bool(c) for c in np.bincount(idx, minlength=len(cal.tissues))]


@pytest.mark.parametrize("split", ["none", "auto"])
def test_tissues_that_only_appear_after_averaging_get_a_material(tmp_path, split):
    vol = _water_cylinder()
    cal = preset("schneider5")
    present = _present_raw(cal, vol)
    assert present == [True, False, False, True, False]
    preview = plan_materials(cal, [], present=present)  # los números que veía el diálogo (0: «no está»)
    lo, hi = vol.bounds()
    imp = CtImport(vol, make_grid(lo, hi, [2.0, 2.0, 2.0]), cal, preview.numbers, ct_name="tac.ct", existing=[],
                   split=split)
    b = imp.build()
    # Con vóxeles de 2 mm el borde del cilindro sale a medio camino entre el aire y el agua (pulmón, quizá grasa):
    # esos vóxeles tienen material y densidad, no son vacío (MC = 0) sin avisar.
    assert int((b.ct.material == 0).sum()) == 0
    lung = [i for i, m in enumerate(b.materials) if (m.parent or m.name) == "Pulmón"]
    assert lung and all(b.numbers[i] > 0 for i in lung) and sum(b.tissue_voxels[i] for i in lung) > 0
    assert any(f.startswith("ct_pulmon") for _n, f, _d in b.plan.new)
    assert not b.plan.problems
    imp.write(tmp_path, b)
    card = read_sidecar(tmp_path / "tac.ct")
    pulmon = next(t for t in card["calibracion"]["tejidos"] if t["nombre"] == "Pulmón")
    assert pulmon["material"] > 0 and pulmon["masa_g"] > 0


def test_only_the_tissues_asked_for_are_void():
    vol = _water_cylinder()
    cal = preset("schneider5")
    lo, hi = vol.bounds()
    reuse = [0, None, None, None, None]  # el aire, al vacío
    plan = plan_materials(cal, [], reuse, present=_present_raw(cal, vol))
    b = CtImport(vol, make_grid(lo, hi, [2.0, 2.0, 2.0]), cal, plan.numbers, ct_name="tac.ct", existing=[],
                 reuse=reuse).build()
    from argus.ct.grid import resample

    idx = cal.tissue_index(resample(vol, make_grid(lo, hi, [2.0, 2.0, 2.0])))
    assert np.array_equal(b.ct.material == 0, idx == 0)  # vacíos los del aire, y solo esos
    # Quien da los números del CT sin decir qué tiene el .in: un 0 sigue siendo vacío.
    nums = [0, 2, 3, 4, 5]
    b = CtImport(vol, make_grid(lo, hi, [2.0, 2.0, 2.0]), cal, nums, ct_name="tac.ct").build()
    assert np.array_equal(b.ct.material == 0, idx == 0)


# ---------------------------------------------------------------------------------------------------------------------
# 2. Un material que el .in ya tenía sigue siendo el del .in
# ---------------------------------------------------------------------------------------------------------------------

GEO = f"""Sala de aire con un cubo de agua
{Z}
SURFACE (   1)  x=+-40
INDICES=( 1, 0, 0, 0,-1)
X-SCALE=( 4.000000000000000E+01,   0)
{Z}
SURFACE (   2)  y=+-40
INDICES=( 0, 1, 0, 0,-1)
Y-SCALE=( 4.000000000000000E+01,   0)
{Z}
SURFACE (   3)  z=+-40
INDICES=( 0, 0, 1, 0,-1)
Z-SCALE=( 4.000000000000000E+01,   0)
{Z}
SURFACE (   4)  z=30
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=( 3.000000000000000E+01,   0)
{Z}
BODY    (   1)  Agua
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
SURFACE (   4), SIDE POINTER=( 1)
{Z}
BODY    (   2)  Aire de la sala
MATERIAL(   2)
SURFACE (   1), SIDE POINTER=(-1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
SURFACE (   4), SIDE POINTER=(-1)
{Z}
END      0000000000000000000000000000000000000000000000000000000
"""

LISTED = {"water.mat": "Water, liquid (PENELOPE 278)", "air.mat": "Air, dry (PENELOPE 104)"}
MLIST = [(104, "AIR, DRY (NEAR SEA LEVEL)"), (278, "WATER, LIQUID")]


def _thorax() -> HuVolume:
    """Aire alrededor, tejido blando, un pulmón y una lámina de hueso (vóxeles de 2 mm)."""
    nz, ny, nx = 6, 20, 20
    hu = np.full((nz, ny, nx), -1000.0, dtype=np.float32)
    hu[:, 3:17, 3:17] = 30.0
    hu[:, 6:12, 5:9] = np.linspace(-850.0, -300.0, 4, dtype=np.float32)[None, None, :]
    hu[:, 14:16, 3:17] = 900.0
    ax = np.arange(nx) * 2.0
    return HuVolume(hu=hu, x=ax, y=np.arange(ny) * 2.0, z=np.arange(nz) * 2.0, rotation=np.eye(3), info={})


def _mat_header(path, name: str, density: float) -> None:
    """Las tres primeras líneas de un .mat de PENELOPE (lo que lee ARGUS para el nombre y la densidad)."""
    path.write_text(" PENELOPE (v. 2018)  Material data file ...............\n"
                    f" Material: {name}\n Mass density = {density:.8E} g/cm**3\n", encoding="latin-1")


def _case(tmp_path, cal, existing, reuse=None, descriptions=None):
    """Un caso con la sala de aire, el cubo de agua y el TAC en la sala; el .in con ``existing`` (y sus nombres) y
    los materiales nuevos del TAC detrás, como los añade ARGUS. Devuelve (ficha, .in, geometría, carpeta)."""
    from argus.penhan.inputfile import parse_in_text

    folder = tmp_path / "caso"
    folder.mkdir()
    vol = _thorax()
    lo, hi = vol.bounds()
    plan = plan_materials(cal, existing, reuse)
    imp = CtImport(vol, make_grid(lo, hi, [2.0, 2.0, 2.0]), cal, plan.numbers, ct_name="tac.ct", title="TAC",
                   existing=list(existing), reuse=reuse, split="none")
    built = imp.build()
    imp.write(folder, built)
    (folder / "caso.geo").write_text(GEO, encoding="latin-1")
    doc = load_geometry(folder / "caso.geo")
    add_ctbox(doc, compile_geometry(doc, base_dir=folder), "tac.ct", built.rotation, built.shift, 2, "TAC", "TAC")
    (folder / "caso.geo").write_text(write_geo_text(doc), encoding="latin-1")
    geo = compile_geometry(load_geometry(folder / "caso.geo"), base_dir=folder)
    assert geo.ok and geo.ct is not None
    descriptions = {**LISTED, **(descriptions or {})}
    lines = [(f, descriptions.get(f, "Material file, up to 20 chars")) for f in existing]
    lines += [(f, d) for _n, f, d in built.plan.new]
    text = "TITLE  Sala con TAC\nSKPAR  2\nSENERG 1e6\nSPOSIT 0 0 -50\nSCONE  0 0 0\n"
    text += "".join(f"MFNAME {f:<31}[{d}]\nMSIMPA 1e5 1e4 1e5 0.05 0.05 5e3 5e3\n" for f, d in lines)
    text += "GEOMFN caso.geo\nNSIMSH 1e5\n"
    (folder / "caso.in").write_text(text, encoding="latin-1")
    return read_sidecar(folder / "tac.ct"), parse_in_text(text, str(folder / "caso.in")), geo, folder, built


def _rows(in_doc, geo, folder):
    from argus.penhan.materials import materials_table

    return {r.number: r for r in materials_table(in_doc, geo, folder)}


def test_the_water_and_air_of_the_in_stay_the_in_materials(tmp_path):
    """«Agua y aire» con un .in que ya tiene water.mat (el cubo) y air.mat (la sala): el TAC los usa, pero siguen
    siendo los del .in: water.mat se genera como el agua de la lista y el air.mat de PENELOPE no es «de otra
    importación»."""
    from argus.penhan.materials import materials_txt
    from argus.penhan.matgen import plan_materials as matgen_plan

    card, in_doc, geo, folder, built = _case(tmp_path, preset("agua"), ["water.mat", "air.mat"])
    assert not built.plan.new and sorted(int(m) for m in geo.ct.materials()) == [1, 2]
    _mat_header(folder / "air.mat", "AIR, DRY (NEAR SEA LEVEL) (104)", 1.20479e-3)
    rows = _rows(in_doc, geo, folder)
    assert rows[1].tissue is None and rows[2].tissue is None
    assert not rows[2].stale and not any("otra importación" in p.message for p in rows[2].problems)
    jobs = {j.fname: j for j in matgen_plan(in_doc, geo, folder, mlist=MLIST).mats}
    assert set(jobs) == {"water.mat"} and "n.º 278" in jobs["water.mat"].what and "TAC" not in jobs["water.mat"].what
    txt = materials_txt(list(rows.values()), "caso.in", geo, "caso.geo", hadrons=False, mlist=MLIST)
    assert "Agua (TAC)" not in txt and "otra importación" not in txt and "Materiales del TAC" not in txt
    assert "water.mat  (n.º 278: WATER, LIQUID)" in txt
    # La ficha lo dice: esos materiales son los del .in.
    assert all(e.get("material_del_in") is True for e in card["materiales"])
    assert all("i_penhmat" not in e for e in card["materiales"])
    assert sidecar_tissues(card) == {}
    assert set(sidecar_tissues(card, reused=True)) == {1, 2}


def test_a_tissue_that_uses_the_room_air_does_not_take_it_over(tmp_path):
    """El aire del TAC con «Material del .in» = el air.mat de la sala: los demás tejidos son materiales nuevos del
    TAC (se generan como tejidos) y el aire de la sala sigue siendo el de la lista de PENELOPE."""
    from argus.penhan.matgen import plan_materials as matgen_plan

    reuse = [2, None, None, None, None]
    card, in_doc, geo, folder, built = _case(tmp_path, preset("schneider5"), ["water.mat", "air.mat"], reuse)
    _mat_header(folder / "air.mat", "AIR, DRY (NEAR SEA LEVEL) (104)", 1.20479e-3)
    new = {n for n, _f, _d in built.plan.new}
    rows = _rows(in_doc, geo, folder)
    assert rows[2].tissue is None and not rows[2].stale
    assert new and all(rows[n].tissue is not None for n in new)
    jobs = {j.fname: j for j in matgen_plan(in_doc, geo, folder, mlist=MLIST).mats}
    assert "air.mat" not in jobs
    assert all("tejido del TAC" in jobs[f].what for _n, f, _d in built.plan.new)
    aire = next(e for e in card["materiales"] if e["nombre"] == "Aire")
    assert aire["material"] == 2 and aire["material_del_in"] is True
    assert next(t for t in card["calibracion"]["tejidos"] if t["nombre"] == "Aire").get("material_del_in") is True
    assert all("material_del_in" not in e for e in card["materiales"] if e["material"] in new)


def test_old_cards_are_still_read_and_a_listed_material_of_the_in_wins(tmp_path):
    """Una ficha de antes de 0.16.3 no marca los materiales reutilizados: se siguen leyendo todos como tejidos,
    pero un material que el .in dice que es de la lista de PENELOPE es ese (no se genera ni se comprueba como el
    tejido); los materiales nuevos del TAC («… (TAC)») siguen siendo sus tejidos."""
    reuse = [2, None, None, None, None]
    card, in_doc, geo, folder, built = _case(tmp_path, preset("schneider5"), ["water.mat", "air.mat"], reuse)
    for e in card["materiales"] + card["calibracion"]["tejidos"]:
        e.pop("material_del_in", None)
    (folder / "tac.ct.json").write_text(json.dumps(card), encoding="utf-8")
    old = read_sidecar(folder / "tac.ct")
    assert 2 in sidecar_tissues(old)  # sin la marca, como antes
    _mat_header(folder / "air.mat", "AIR, DRY (NEAR SEA LEVEL) (104)", 1.20479e-3)
    rows = _rows(in_doc, geo, folder)
    assert rows[2].tissue is None and not rows[2].stale
    assert all(rows[n].tissue is not None for n, _f, _d in built.plan.new)


def test_reimporting_over_the_materials_of_another_import_keeps_them_as_tissues(tmp_path):
    """Volver a importar sobre un .in que ya tiene los .mat de otra importación («Tejido blando (TAC)»,
    ct_blando.mat): son ese tejido del TAC, se generan como él y se avisa si el .mat tiene otra densidad."""
    cal = preset("schneider5")
    old = ["water.mat"] + [t.mat_file for t in cal.tissues]
    descriptions = {t.mat_file: f"{t.name} (TAC)" for t in cal.tissues}
    card, in_doc, geo, folder, built = _case(tmp_path, cal, old, descriptions=descriptions)
    assert not built.plan.new
    blando = next(e for e in card["materiales"] if e["nombre"] == "Tejido blando")
    assert blando["material_del_in"] is True
    _mat_header(folder / "ct_blando.mat", "Tejido blando (TAC)", 0.999)
    rows = _rows(in_doc, geo, folder)
    n = blando["material"]
    assert rows[n].tissue is not None and rows[n].tissue.name == "Tejido blando"
    assert rows[n].stale and any("otra importación" in p.message for p in rows[n].problems)
    assert rows[1].tissue is None  # el agua del .in no la usa el TAC


def test_two_tissues_cannot_share_a_new_mat_file():
    cal = preset("schneider5")
    cal.tissues[2].mat_file = "CT_Blando.mat"  # el adiposo con el .mat del tejido blando (otras mayúsculas)
    plan = plan_materials(cal, ["water.mat"])
    assert any("«Adiposo» y «Tejido blando»" in p and "mismo .mat" in p for p in plan.problems)
    vol = _thorax()
    lo, hi = vol.bounds()
    g = make_grid(lo, hi, [2.0, 2.0, 2.0])
    with pytest.raises(ValueError, match="mismo .mat"):
        CtImport(vol, g, cal, plan.numbers, existing=["water.mat"], split="auto").build()
    with pytest.raises(ValueError, match="mismo .mat"):  # quien importa sin decir qué tiene el .in, tampoco
        CtImport(vol, g, cal, plan.numbers, split="none").build()
    # Compartir un material que ya tiene el .in (por el nombre o con «Material del .in») o ir al vacío sí vale.
    assert not plan_materials(cal, ["water.mat", "ct_blando.mat"]).problems
    assert not plan_materials(cal, ["water.mat"], reuse=[None, None, 1, None, None]).problems
    assert not plan_materials(cal, ["water.mat"], reuse=[None, None, 0, None, None]).problems


# ---------------------------------------------------------------------------------------------------------------------
# 4. Curvas guardadas por Excel en español (cp1252) y otras codificaciones
# ---------------------------------------------------------------------------------------------------------------------

ROWS = "-1000;0,00121\r\n-98;0,93\r\n14;1,03\r\n100;1,1199\r\n1600;1,9642\r\n3000;2,8\r\n"
CURVE = [(-1000.0, 0.00121), (-98.0, 0.93), (14.0, 1.03), (100.0, 1.1199), (1600.0, 1.9642), (3000.0, 2.8)]


@pytest.mark.parametrize("header", ["HU;Densidad (g/cm³)", "HU;Densidad (g/cm³) ", "Número CT;Densidad (g/cm3)",
                                    "Nº CT;Densidad (g/cm³)"])
def test_curves_saved_by_spanish_excel_are_read(tmp_path, header):
    text = header + "\r\n" + ROWS
    p = tmp_path / "curva.csv"
    p.write_bytes(text.encode("cp1252"))  # con longitud par se tomaba por UTF-16 y salía ilegible
    assert read_curve_file(p) == CURVE


def test_curve_encodings(tmp_path):
    text = "HU;Densidad (g/cm³)\r\n" + ROWS
    assert len(text.encode("cp1252")) % 2 == 0
    p = tmp_path / "curva.txt"
    for data in (text.encode("utf-8"), codecs.BOM_UTF8 + text.encode("utf-8"),
                 text.replace(";", "\t").encode("utf-16"),  # «Texto Unicode» de Excel: UTF-16 con BOM
                 text.encode("utf-16-le"), text.encode("utf-16-be")):  # UTF-16 sin BOM
        p.write_bytes(data)
        assert read_curve_file(p) == CURVE


# ---------------------------------------------------------------------------------------------------------------------
# 5. «La caja del CT se sale del contenedor»: cuánto de verdad
# ---------------------------------------------------------------------------------------------------------------------

CUBE = f"""Cubo
{Z}
SURFACE (   1)  x=+-20
INDICES=( 1, 0, 0, 0,-1)
X-SCALE=( 2.000000000000000E+01,   0)
{Z}
SURFACE (   2)  y=+-20
INDICES=( 0, 1, 0, 0,-1)
Y-SCALE=( 2.000000000000000E+01,   0)
{Z}
SURFACE (   3)  z=+-20
INDICES=( 0, 0, 1, 0,-1)
Z-SCALE=( 2.000000000000000E+01,   0)
{Z}
BODY    (   1)  Cubo de agua
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
{Z}
END      0000000000000000000000000000000000000000000000000000000
"""


def test_the_fit_message_says_how_much_of_the_volume_sticks_out(tmp_path):
    from argus.ct.importer import check_ct_box
    from argus.pengeom.ct import CtFrame
    from argus.pengeom.locate import Locator

    geo = compile_geometry(parse_geo_text(CUBE, str(tmp_path / "g.geo")), base_dir=tmp_path)
    loc = Locator(geo)

    def check(over):
        fr = CtFrame(np.eye(3), np.array([-20.0 - over, -15.0, -15.0]), (40.0 + 2 * over, 30.0, 30.0), (10, 10, 10))
        return check_ct_box(geo, loc, fr, 1)

    chk = check(0.5)  # 1 cm de 41: un 2,4 %
    assert not chk.ok and chk.outside_fraction == pytest.approx(1.0 / 41.0, abs=0.006)
    assert "el 2," in chk.message
    chk = check(0.02)  # 0,4 mm de 40 cm: un 0,1 % (antes, un 6,7 %: los puntos de las caras)
    assert not chk.ok and chk.outside_fraction < 0.003 and "6,7" not in chk.message
    chk = check(0.004)  # casi nada, pero se sale
    assert not chk.ok and "se sale" in chk.message
    assert "menos del 0,1 % de su volumen" in chk.message and "el menos" not in chk.message


# ---------------------------------------------------------------------------------------------------------------------
# 6. El aviso de las fracciones en masa, con coma decimal
# ---------------------------------------------------------------------------------------------------------------------

def test_the_mass_fraction_warning_uses_a_decimal_comma():
    cal = preset("schneider5")
    cal.tissues[3].composition = {1: 0.10, 8: 0.85}
    w = " ".join(cal.warnings())
    assert "suman 0,950" in w and "0.950" not in w
