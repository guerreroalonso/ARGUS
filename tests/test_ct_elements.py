"""Datos de los elementos y energía media de excitación (argus.ct.elements)."""

from __future__ import annotations

import math
import os
import re
import subprocess
from pathlib import Path

import pytest

from argus.ct import elements as el

# Composiciones (fracción en masa) copiadas aquí para que la prueba no cambie si cambia la calibración.
AIR = {7: 0.755, 8: 0.232, 18: 0.013}  # Schneider (aire)
LUNG = {1: 0.10404, 6: 0.106061, 7: 0.031313, 8: 0.756566, 15: 0.00202}  # Schneider sin trazas
ADIPOSE = {1: 0.116349, 6: 0.683049, 7: 0.002006, 8: 0.198596}  # AT_AG_SI1 sin trazas
SOFT = {1: 0.103831, 6: 0.135081, 7: 0.030242, 8: 0.728831, 15: 0.002016}  # SoftTissue sin trazas
BONE = {1: 0.056338, 6: 0.2666, 7: 0.036217, 8: 0.407445, 15: 0.073441, 20: 0.15996}  # Marrow_Bone08 sin trazas
WATER = {1: 0.111894, 8: 0.888106}
# pdcompos.pen (PENELOPE): 103 tejido adiposo, 262 tejido blando y 120 hueso cortical (ICRP)
ICRP_ADIPOSE = {1: 0.119477, 6: 0.63724, 7: 0.00797, 8: 0.232333, 11: 0.0005, 12: 2e-05, 15: 0.00016, 16: 0.00073,
                17: 0.00119, 19: 0.00032, 20: 2e-05, 26: 2e-05, 30: 2e-05}
ICRP_SOFT = {1: 0.104472, 6: 0.23219, 7: 0.02488, 8: 0.630238, 11: 0.00113, 12: 0.00013, 15: 0.00133, 16: 0.00199,
             17: 0.00134, 19: 0.00199, 20: 0.00023, 26: 5e-05, 30: 3e-05}
ICRP_CORTICAL = {1: 0.047234, 6: 0.14433, 7: 0.04199, 8: 0.446096, 12: 0.0022, 15: 0.10497, 16: 0.00315,
                 20: 0.20993, 30: 0.0001}

# La I por defecto de penhmat real (la cabecera de los .mat que generó para estos tejidos; el agua, su salida).
PENHMAT_DEFAULT = [("aire", AIR, 85.6830252), ("pulmón", LUNG, 69.1281239), ("adiposo", ADIPOSE, 61.8388470),
                   ("blando", SOFT, 68.8853026), ("hueso", BONE, 88.6845877), ("agua", WATER, 68.9989)]


# -- tablas ------------------------------------------------------------------------------------------------

def test_tables_have_z_1_to_99():
    assert len(el.ATOMIC_WEIGHTS) == 100 and len(el.EPX) == 100
    assert el.ATOMIC_WEIGHTS[1] == 1.0079 and el.ATOMIC_WEIGHTS[8] == 15.999 and el.ATOMIC_WEIGHTS[99] == 252.08
    assert el.EPX[1] == 19.2 and el.EPX[8] == 95.0 and el.EPX[35] == 343.0 and el.EPX[99] == 980.0
    assert all(a > 0 for a in el.ATOMIC_WEIGHTS[1:]) and all(i > 0 for i in el.EPX[1:])


def _penelope_f() -> Path:
    p = os.environ.get("ARGUS_PENELOPE_F")
    if not p or not Path(p).is_file():
        pytest.skip("penelope.f no disponible (ARGUS_PENELOPE_F)")
    return Path(p)


def _fortran_data(src: str, name: str) -> list[float]:
    lines = src.split("\n")
    start = next(i for i, ln in enumerate(lines) if re.match(r"\s+DATA\s+" + name + r"\s*/", ln))
    body = []
    for i in range(start, len(lines)):
        txt = lines[i][:72] if i == start else lines[i][6:72]
        if i == start:
            txt = txt[txt.index("/") + 1:]
        if "/" in txt:
            body.append(txt[:txt.index("/")])
            break
        body.append(txt)
    return [float(t.replace("D", "E")) for t in re.findall(r"[0-9.]+D[+-]?[0-9]+", " ".join(body))]


def test_tables_are_the_ones_of_penelope_f():
    src = _penelope_f().read_text(encoding="latin-1")
    assert tuple(_fortran_data(src, "ATW")) == el.ATOMIC_WEIGHTS[1:]
    assert tuple(_fortran_data(src, "EPX")) == el.EPX[1:]


# -- I por defecto de PENELOPE -------------------------------------------------------------------------------

@pytest.mark.parametrize("name,comp,expected", PENHMAT_DEFAULT, ids=[p[0] for p in PENHMAT_DEFAULT])
def test_penelope_default_is_the_one_of_penhmat(name, comp, expected):
    assert abs(el.penelope_i(comp) - expected) < 0.01


def test_penelope_default_ignores_normalisation_and_accepts_text_keys():
    scaled = {str(z): 3.7 * w for z, w in SOFT.items()}
    assert el.penelope_i(scaled) == pytest.approx(el.penelope_i(SOFT), rel=1e-12)
    assert el.penelope_i({8: 1.0}) == pytest.approx(95.0, rel=1e-12)
    assert el.penelope_i({"6": 2.0}) == pytest.approx(81.0, rel=1e-12)
    assert el.penelope_i({6: 0.5, 8: 0.0}) == pytest.approx(81.0, rel=1e-12)  # una fracción cero no cuenta


def _penhmat_i(exe: str, comp: dict[int, float], cwd: Path) -> float:
    answers = ["1", "Prueba", str(len(comp)), "2"] + [f"{z} {w:.6f}" for z, w in comp.items()] + ["2"]
    run = subprocess.run([exe], input="\n".join(answers) + "\n", capture_output=True, text=True, cwd=cwd, timeout=120)
    m = re.search(r"calculated mean excitation energy I is\s+([0-9.E+-]+)", run.stdout)
    assert m, run.stdout[-2000:]
    return float(m.group(1))


def test_penelope_default_against_the_real_penhmat(tmp_path):
    exe = os.environ.get("ARGUS_PENHMAT")
    if not exe:
        pytest.skip("penhmat no disponible (ARGUS_PENHMAT)")
    for _name, comp, _expected in PENHMAT_DEFAULT + [("icrp", ICRP_SOFT, 0.0)]:
        assert abs(_penhmat_i(exe, comp, tmp_path) - el.penelope_i(comp)) < 0.01


# -- regla de fase condensada --------------------------------------------------------------------------------

def test_condensed_atomic_values_are_berger_seltzer_table_5_1():
    for z, i in ((1, 19.2), (6, 81.0), (7, 82.0), (8, 106.0), (9, 112.0), (17, 180.0)):
        assert el.condensed_atomic_i(z) == i
    assert el.condensed_atomic_i(20) == pytest.approx(1.13 * 191.0)
    assert el.condensed_atomic_i(15) == pytest.approx(1.13 * 173.0)
    assert el.condensed_atomic_i(35) == pytest.approx(1.13 * 357.0)  # Br condensado, no el 343 (gas) de EPX
    assert el.condensed_atomic_i(18) == pytest.approx(1.13 * 188.0)


def test_condensed_scale_makes_water_78_ev():
    wh, wo = 0.111894 / 1.0079 * 1, 0.888106 / 15.999 * 8
    plain = math.exp((wh * math.log(19.2) + wo * math.log(106.0)) / (wh + wo))
    assert el.CONDENSED_SCALE == pytest.approx(78.0 / plain, rel=1e-12)
    assert el.CONDENSED_SCALE == pytest.approx(1.0356, abs=1e-4)
    assert el.condensed_i(WATER) == pytest.approx(78.0, abs=1e-9)


@pytest.mark.parametrize("comp,expected", [(LUNG, 77.2), (ADIPOSE, 65.3), (SOFT, 76.7), (BONE, 98.4),
                                           (ICRP_ADIPOSE, 65.5), (ICRP_SOFT, 75.1), (ICRP_CORTICAL, 110.3)],
                         ids=["pulmón", "adiposo", "blando", "hueso", "icrp-adiposo", "icrp-blando", "icrp-cortical"])
def test_condensed_rule_values(comp, expected):
    assert abs(el.condensed_i(comp) - expected) <= 0.1


def test_penelope_default_is_about_10_percent_low_for_tissues():
    for comp in (LUNG, SOFT, BONE, WATER):
        assert 0.85 < el.penelope_i(comp) / el.condensed_i(comp) < 0.93


# -- regla general -------------------------------------------------------------------------------------------

def test_general_rule():
    i, rule = el.mean_excitation_energy(AIR, 0.00121)
    assert rule == el.RULE_GAS == "gas" and abs(i - 85.7) < 0.05
    i, rule = el.mean_excitation_energy(LUNG, 0.26)
    assert rule == el.RULE_CONDENSED == "condensado" and i == pytest.approx(el.condensed_i(LUNG))
    assert el.mean_excitation_energy(WATER, 1.0) == (pytest.approx(78.0), "condensado")
    assert el.mean_excitation_energy({6: 1.0}, 2.0) == (81.0, "elemento")  # grafito: EPX
    assert el.mean_excitation_energy({1: 1.0}, 8.4e-5) == (19.2, "elemento")
    assert el.mean_excitation_energy({18: 1.0, 8: 0.0}, 0.00166) == (188.0, "elemento")
    assert el.mean_excitation_energy(AIR, 0.0199)[1] == "gas"
    assert el.mean_excitation_energy(AIR, 0.02)[1] == "condensado"  # límite: 0,02 g/cm³ ya no es gas
    assert el.mean_excitation_energy(LUNG, 0.05)[1] == "condensado"  # el pulmón menos denso


@pytest.mark.parametrize("comp", [{}, {0: 1.0}, {100: 1.0}, {8: -0.1, 1: 1.0}, {8: float("nan")}, {8: 0.0},
                                  {8: 0.5, "8": 0.5}, {"x": 1.0}])
def test_invalid_compositions(comp):
    with pytest.raises(ValueError):
        el.penelope_i(comp)
    with pytest.raises(ValueError):
        el.condensed_i(comp)


@pytest.mark.parametrize("rho", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_density(rho):
    with pytest.raises(ValueError):
        el.mean_excitation_energy(SOFT, rho)


# -- <Z/A> y poder de frenado ----------------------------------------------------------------------------------

def test_z_over_a():
    assert el.z_over_a(WATER) == pytest.approx(0.555087, abs=2e-5)  # ICRU: 0,555087; con los ATW de PENELOPE, 0,55510
    assert el.WATER_Z_OVER_A == pytest.approx(el.z_over_a(WATER))
    assert el.z_over_a({z: 2 * w for z, w in BONE.items()}) == pytest.approx(el.z_over_a(BONE), rel=1e-12)
    assert el.z_over_a({1: 1.0}) == pytest.approx(1 / 1.0079)


# S/S_agua a 70 MeV de los .mat que generó penhmat (con su I por defecto), frente al agua con I = 78 eV.
MAT_STOPPING = [("aire", AIR, 0.888), ("pulmón", LUNG, 1.009), ("adiposo", ADIPOSE, 1.034), ("blando", SOFT, 1.009),
                ("hueso", BONE, 0.931)]


@pytest.mark.parametrize("name,comp,expected", MAT_STOPPING, ids=[p[0] for p in MAT_STOPPING])
def test_relative_stopping_power_matches_the_penelope_mat_files(name, comp, expected):
    s = el.relative_stopping_power(comp, el.penelope_i(comp), 70.0)
    assert abs(s / expected - 1.0) < 0.01  # se pide un 3 %; sale menos del 0,1 %


def test_relative_stopping_power_properties():
    assert el.relative_stopping_power(WATER, 78.0) == pytest.approx(1.0, abs=1e-12)
    assert el.relative_stopping_power(WATER, 78.0, 200.0) == pytest.approx(1.0, abs=1e-12)
    # más I, menos frenado; la dependencia se suaviza con la energía
    low, high = el.relative_stopping_power(SOFT, 70.0), el.relative_stopping_power(SOFT, 80.0)
    assert low > high
    assert (el.relative_stopping_power(SOFT, 70.0, 200.0) - el.relative_stopping_power(SOFT, 80.0, 200.0)) < low - high
    with pytest.raises(ValueError):
        el.relative_stopping_power(SOFT, 0.0)
    with pytest.raises(ValueError):
        el.relative_stopping_power(SOFT, 70.0, -1.0)
    with pytest.raises(ValueError):
        el.relative_stopping_power({82: 1.0}, 823.0, 0.01)  # Bethe no vale a 10 keV
