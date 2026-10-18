"""Tejidos partidos por densidad al importar un TAC (penhmain 2025, CTTRACK: ADR-011) y regla general de la
energía media de excitación: importación, ficha, materiales del .in, materiales.txt, avisos y vista."""

import json

import numpy as np
import pytest

from argus.ct.calibration import Calibration, penhmat_steps, preset
from argus.ct.dicom import HuVolume
from argus.ct.grid import make_grid
from argus.ct.importer import (
    MAXMAT,
    CtImport,
    plan_materials,
    read_sidecar,
    sidecar_families,
    sidecar_subdivision,
    sidecar_tissues,
    split_summary,
    sub_material_name,
)


def _thorax(bone: bool = True) -> HuVolume:
    """Un «tórax» de 20 × 20 × 4 cm con vóxeles de 5 mm: tejido blando, dos pulmones heterogéneos (−900 a −200
    HU) y una lámina de hueso con densidades muy distintas; aire alrededor."""
    nz, ny, nx = 8, 40, 40
    hu = np.full((nz, ny, nx), -1000.0, dtype=np.float32)
    hu[:, 4:36, 4:36] = 40.0
    grad = np.linspace(-900.0, -200.0, 20, dtype=np.float32)
    hu[:, 10:30, 8:20] = grad[:, None]
    hu[:, 10:30, 22:34] = grad[::-1][:, None]
    if bone:
        hu[:, 32:35, 4:36] = np.linspace(200.0, 1400.0, 32, dtype=np.float32)[None, None, :]
    x = np.arange(nx) * 5.0
    y = np.arange(ny) * 5.0
    z = np.arange(nz) * 5.0
    return HuVolume(hu=hu, x=x, y=y, z=z, rotation=np.eye(3), info={"fabricante": "sintético"})


def _import(split="auto", existing=("water.mat",), bone=True, reuse=None, fixed=False, cal=None):
    vol = _thorax(bone)
    lo, hi = vol.bounds()
    g = make_grid(lo, hi, [5.0, 5.0, 5.0])
    cal = cal or preset("schneider5")
    plan = plan_materials(cal, list(existing), reuse)
    imp = CtImport(vol, g, cal, plan.numbers, ct_name="tac.ct", title="TAC de prueba", split=split,
                   cttrack_fixed=fixed, existing=list(existing), reuse=reuse)
    return imp, imp.build()


def test_the_lung_is_split_and_each_mat_has_the_mean_of_its_voxels():
    imp, b = _import()
    pieces = b.subdivision["trozos"]
    assert pieces["Pulmón"] >= 2
    assert not b.plan.problems and max(b.numbers) <= MAXMAT
    for i, m in enumerate(b.materials):
        n = b.numbers[i]
        if n:
            sel = b.ct.material == n
            assert m.voxel_density == pytest.approx(float(b.ct.density[sel].mean()), rel=1e-9)
    lung = [m for m in b.materials if m.parent == "Pulmón"]
    assert len(lung) == pieces["Pulmón"]
    assert [m.name for m in lung] == [f"Pulmón {i}/{len(lung)}" for i in range(1, len(lung) + 1)]
    # Misma composición que el tejido; densidades crecientes y sin huecos entre los trozos.
    assert all(m.composition == imp.calibration.tissues[1].composition for m in lung)
    cuts = [m.density_cut for m in lung]
    assert cuts[0][0] is None and cuts[-1][1] is None
    assert all(a[1] == b_[0] for a, b_ in zip(cuts, cuts[1:]))
    assert [m.voxel_density for m in lung] == sorted(m.voxel_density for m in lung)
    # Los nombres de los .mat caben en A20 y no se repiten.
    names = [f for _n, f, _d in b.plan.new]
    assert all(len(f) <= 20 for f in names) and len({f.lower() for f in names}) == len(names)
    # El error estimado baja.
    err = b.subdivision["error_alcance_mm"]
    assert err["elegido"]["p95"] < err["sin_partir"]["p95"]


def test_the_card_has_the_materials_and_the_calibration_stays_valid(tmp_path):
    imp, b = _import()
    imp.write(tmp_path, b)
    card = read_sidecar(tmp_path / "tac.ct")
    assert card["version_ficha"] == 2 and sidecar_subdivision(card)["modo"] == "auto"
    tissues = sidecar_tissues(card)
    assert set(tissues) == {n for n in b.numbers if n}
    fam = sidecar_families(card)
    lung = [n for n, t in tissues.items() if t.parent == "Pulmón"]
    assert len(lung) >= 2 and {fam[n] for n in lung} == {"Pulmón"}
    # «calibracion» sigue siendo la calibración de los tejidos (sin repetir HU): se puede volver a usar.
    cal = Calibration.from_dict(card["calibracion"])
    assert not cal.problems() and [t.name for t in cal.tissues] == [t.name for t in imp.calibration.tissues]
    # La masa de cada material cuadra con el fichero y lleva la I que se da a penhmat.
    for e in card["materiales"]:
        assert e["regla_i"] in ("gas", "condensado")
    blando = next(e for e in card["materiales"] if e["nombre"] == "Tejido blando")
    assert blando["i_penhmat"] == pytest.approx(76.7, abs=0.05)
    # Las fichas anteriores (sin «materiales») se siguen leyendo con los tejidos.
    old = json.loads(json.dumps(card))
    del old["materiales"]
    assert set(sidecar_tissues(old)) == {t["material"] for t in old["calibracion"]["tejidos"] if t["material"]}


def test_without_split_or_with_cttrack_fixed_nothing_is_split():
    # Sin partir, un material por tejido; el tejido sin vóxeles (grasa) no lleva MFNAME.
    for kw, mode in (({"split": "none"}, "none"), ({"split": "auto", "fixed": True}, "penhmain_corregido")):
        imp, b = _import(**kw)
        assert b.subdivision == {"modo": mode} and len(b.materials) == len(imp.calibration.tissues)
        assert [m.name for m in b.materials] == [t.name for t in imp.calibration.tissues]
        fat = next(i for i in range(len(b.materials)) if b.tissue_voxels[i] == 0)
        assert b.numbers[fat] == 0 and sorted(n for n in b.numbers if n) == list(range(2, len(b.numbers) + 1))
        assert split_summary(b.subdivision) == ""
    # Quien importa sin decir qué tiene el .in: los números que dio, sin plan.
    vol = _thorax()
    lo, hi = vol.bounds()
    cal = preset("schneider5")
    nums = plan_materials(cal, ["water.mat"]).numbers
    b = CtImport(vol, make_grid(lo, hi, [5.0, 5.0, 5.0]), cal, nums, ct_name="tac.ct", split="none").build()
    assert b.numbers == list(nums) and b.plan is None


def test_the_budget_is_what_the_in_leaves_free():
    # Con 8 materiales en el .in, no caben ni los 5 tejidos: no se parte y se avisa de MAXMAT.
    existing = [f"m{i}.mat" for i in range(1, 9)]
    _imp, b = _import(existing=existing)
    assert all(k <= 1 for k in b.subdivision["trozos"].values())  # 0: sin vóxeles (no hay grasa)
    assert b.plan.problems and "10" in b.plan.problems[0]
    # Con 4, quedan 6: los 5 tejidos y un trozo más como mucho.
    existing = [f"m{i}.mat" for i in range(1, 5)]
    _imp, b = _import(existing=existing)
    assert sum(b.subdivision["trozos"].values()) <= 6 and not b.plan.problems
    assert max(b.numbers) <= MAXMAT


def test_a_reused_material_is_not_split_and_an_absent_tissue_gets_no_mfname():
    # El pulmón usa el material 1 del .in: no se parte. Sin hueso en el TAC, el hueso no lleva material.
    reuse = [None, 1, None, None, None]
    imp, b = _import(reuse=reuse, bone=False)
    assert b.subdivision["trozos"]["Pulmón"] == 1
    lung = b.materials.index(next(m for m in b.materials if m.name == "Pulmón"))
    assert b.numbers[lung] == 1
    bone = next(i for i, m in enumerate(b.materials) if m.name.startswith("Hueso"))
    assert b.numbers[bone] == 0 and all("hueso" not in f for _n, f, _d in b.plan.new)


def test_sub_material_names_fit_in_20_characters():
    taken = {"ct_pulmon_1.mat"}
    assert sub_material_name("ct_pulmon.mat", 1, taken) == "ct_pulmona_1.mat"
    long = sub_material_name("tejido_con_nombre_muy_largo.mat", 12, set())
    assert len(long) <= 20 and long.endswith("_12.mat")


def test_penhmat_steps_of_a_piece_use_its_density_and_the_general_i():
    _imp, b = _import()
    lung = [m for m in b.materials if m.parent == "Pulmón"]
    steps = penhmat_steps(lung[0], density=lung[0].voxel_density)
    assert any(s.startswith(f"Densidad: {lung[0].voxel_density:.6g}") for s in steps)
    assert any("1 (sí) y 77.2 eV" in s for s in steps)  # Schneider pulmón, regla de fase condensada
    air = next(m for m in b.materials if m.name == "Aire")
    assert any("2 (no)" in s and "85.7 eV" in s and "gas" in s for s in penhmat_steps(air))


def test_the_2d_shading_of_a_split_tissue_is_continuous(tmp_path):
    from argus.pengeom.ct import load_ct
    from argus.render.slice2d import density_ranges

    imp, b = _import()
    imp.write(tmp_path, b)
    ct = load_ct(tmp_path / "tac.ct")
    lung = [n for n, m in zip(b.numbers, b.materials) if m.parent == "Pulmón"]
    ranges = density_ranges(ct)
    assert len({ranges[n] for n in lung}) == 1  # un solo intervalo: el del pulmón entero
    lo, hi = ranges[lung[0]]
    sel = np.isin(ct.material, lung)
    assert lo == pytest.approx(float(ct.density[sel].min())) and hi == pytest.approx(float(ct.density[sel].max()))


def test_the_messages_depend_on_the_split_and_the_setting(tmp_path):
    from argus.penhan.simcheck import cttrack_text

    imp, b = _import()
    imp.write(tmp_path, b)

    class Geo:
        ct_path = tmp_path / "tac.ct"

    split, text = cttrack_text(Geo())
    assert split and "partidos por densidad" in text and "Pulmón en" in text
    imp, b = _import(split="none")
    (tmp_path / "b").mkdir()
    imp.write(tmp_path / "b", b)
    Geo.ct_path = tmp_path / "b" / "tac.ct"
    split, text = cttrack_text(Geo())
    assert not split and "no están partidos" in text and "pulmón" in text


# -- interfaz -----------------------------------------------------------------------------------------------------

def test_the_import_dialog_offers_the_split_and_remembers_the_setting(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from argus.ui import settings as st
    from argus.ui.ct_import_dialog import CtImportDialog

    ini = tmp_path / "argus.ini"
    monkeypatch.setenv("ARGUS_SETTINGS", str(ini))
    dlg = CtImportDialog(None, None, None, str(tmp_path))
    qtbot.addWidget(dlg)
    assert dlg.split_mode() == "auto" and dlg.split_combo.isEnabled()
    dlg.cttrack_check.setChecked(True)
    assert st.cttrack_fixed() and not dlg.split_combo.isEnabled() and dlg.split_mode() == "none"
    dlg.cttrack_check.setChecked(False)
    assert not st.cttrack_fixed() and dlg.split_mode() == "auto"
    dlg.split_combo.setCurrentIndex(dlg.split_combo.findData("max"))
    assert dlg.split_mode() == "max"


def test_the_option_in_the_window_changes_the_messages(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from argus.ui import settings as st
    from argus.ui.main_window import MainWindow

    monkeypatch.setenv("ARGUS_SETTINGS", str(tmp_path / "argus.ini"))
    w = MainWindow()
    qtbot.addWidget(w)
    assert not w.act_cttrack.isChecked()
    w.act_cttrack.setChecked(True)
    assert st.cttrack_fixed()
    w.act_cttrack.setChecked(False)
    assert not st.cttrack_fixed()


# -- por qué no se parte un tejido (revisión final) ----------------------------------------------------------------

def _uniform() -> HuVolume:
    """Tejido blando de un solo valor dentro de aire: no hay nada que partir."""
    hu = np.full((6, 30, 30), -1000.0, dtype=np.float32)
    hu[:, 5:25, 5:25] = 40.0
    ax = np.arange(30) * 5.0
    return HuVolume(hu=hu, x=ax, y=ax.copy(), z=np.arange(6) * 5.0, rotation=np.eye(3), info={})


def test_why_a_tissue_is_not_split_goes_to_the_card_and_the_messages(tmp_path):
    from argus.ct.importer import split_state
    from argus.penhan.materials import _cttrack_paragraph
    from argus.penhan.simcheck import cttrack_text

    class Geo:
        ct_path = tmp_path / "tac.ct"

    # Reimportar sobre un .in que ya tiene el .mat del pulmón (de un TAC anterior): no se puede partir, y se dice
    # por qué y qué hacer. Es un aviso aunque se haya partido el hueso.
    imp, b = _import(existing=("water.mat", "ct_pulmon.mat"))
    st = split_state(b.subdivision)
    assert st.blocked == {"Pulmón": "material del .in"} and not st.ok and st.analysed
    imp.write(tmp_path, b)
    ok, text = cttrack_text(Geo())
    assert not ok and "Pulmón usa un material que ya tiene el .in" in text and "MFNAME" in text
    assert "Pulmón usa un material" in _cttrack_paragraph(Geo(), False)
    # Sin materiales libres: el .in tiene 6 y el TAC necesita 4 (caben justos, sin partir nada).
    _imp, b = _import(existing=[f"m{i}.mat" for i in range(1, 7)])
    st = split_state(b.subdivision)
    assert not st.pieces and st.blocked.get("Pulmón") == "presupuesto" and not b.plan.problems
    assert "no quedan materiales libres" in split_summary(b.subdivision)
    # Un TAC sin nada que partir: no hace falta, y no es un aviso.
    vol = _uniform()
    lo, hi = vol.bounds()
    cal = preset("schneider5")
    plan = plan_materials(cal, ["water.mat"])
    imp = CtImport(vol, make_grid(lo, hi, [5.0, 5.0, 5.0]), cal, plan.numbers, ct_name="tac.ct", split="auto",
                   existing=["water.mat"])
    b = imp.build()
    st = split_state(b.subdivision)
    assert st.ok and not st.pieces and not st.blocked and set(st.unneeded) == {"Aire", "Tejido blando"}
    assert "No ha hecho falta partir" in split_summary(b.subdivision)
    (tmp_path / "u").mkdir()
    imp.write(tmp_path / "u", b)
    Geo.ct_path = tmp_path / "u" / "tac.ct"
    ok, text = cttrack_text(Geo())
    assert ok and "No ha hecho falta" in text


def test_older_or_missing_cards_are_a_warning(tmp_path):
    from argus.penhan.simcheck import cttrack_text

    class Geo:
        ct_path = tmp_path / "tac.ct"

    ok, text = cttrack_text(Geo())  # sin ficha
    assert not ok and "no tiene ficha" in text
    imp, b = _import()
    imp.write(tmp_path, b)
    card = read_sidecar(tmp_path / "tac.ct")
    del card["subdivision"]  # una ficha de ARGUS 0.8
    (tmp_path / "tac.ct.json").write_text(json.dumps(card), encoding="utf-8")
    ok, text = cttrack_text(Geo())
    assert not ok and "versión anterior" in text


def test_a_void_tissue_is_written_to_the_card(tmp_path):
    # El aire como vacío (material 0): su masa es 0 y no tiene densidad media, pero la ficha se escribe.
    imp, b = _import(reuse=[0, None, None, None, None])
    assert b.numbers[0] == 0 and b.subdivision["trozos"]["Aire"] == 0
    imp.write(tmp_path, b)
    card = read_sidecar(tmp_path / "tac.ct")
    aire = next(e for e in card["materiales"] if e["nombre"] == "Aire")
    assert aire["material"] == 0 and "densidad_media" not in aire
    assert 0 not in sidecar_tissues(card)


@pytest.mark.parametrize("text", ["[]", "3", '"x"', "null", '{"materiales": [1, "a", null]}', '{"calibracion": []}',
                                  '{"calibracion": {"tejidos": {"a": 1}}}', '{"subdivision": []}'])
def test_cards_that_are_not_what_argus_writes_are_ignored(tmp_path, text):
    from argus.ct.importer import split_state

    (tmp_path / "t.ct.json").write_text(text, encoding="utf-8")
    card = read_sidecar(tmp_path / "t.ct")
    assert sidecar_tissues(card) == {} and sidecar_subdivision(card) is None and sidecar_families(card) == {}
    assert not split_state(sidecar_subdivision(card)).ok


@pytest.mark.parametrize("sub", [{"modo": "auto", "trozos": {"a": "x", "b": None}, "limites": [],
                                  "error_alcance_mm": {"sin_partir": 1}},
                                 {"modo": 3}, {"modo": "auto", "trozos": [], "preparacion_extra_s": "x"},
                                 {"modo": "auto", "trozos": {"a": 2}, "error_alcance_mm": {"sin_partir": {"p50": "x"},
                                                                                           "elegido": {}}}])
def test_odd_subdivisions_do_not_break_the_messages(sub):
    from argus.ct.importer import split_state

    split_state(sub)
    assert isinstance(split_summary(sub), str)


def test_gas_or_condensed_is_decided_by_the_tissue_not_its_voxels():
    """Un trozo del aire con vóxeles de 0,03 g/cm³ (junto a la piel) sigue siendo gas; uno del pulmón de 0,015,
    condensado: la I es la misma para todos los trozos de un tejido."""
    from argus.ct.calibration import excitation_energy

    cal = preset("schneider5")
    air, lung = cal.tissues[0], cal.tissues[1]
    assert excitation_energy(air, 0.03) == excitation_energy(air) and excitation_energy(air)[1] == "gas"
    assert excitation_energy(lung, 0.015) == excitation_energy(lung) and excitation_energy(lung)[1] == "condensado"
