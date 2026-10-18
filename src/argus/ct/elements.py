"""Datos de los elementos y energía media de excitación (I) de un material.

Tablas de PENELOPE (``penelope.f`` de penhan 2025, BLOCK DATA PENDAT) para
Z = 1–99: los pesos atómicos (``DATA ATW``) y las energías medias de
excitación de los elementos (``DATA EPX``).

Regla general de ARGUS para la I que se da a penhmat
(:func:`mean_excitation_energy`), para cualquier composición:

* **Un solo elemento** (regla «elemento»): el valor EPX del elemento, el que
  calcula penhmat por defecto.
* **Gas** (regla «gas»; densidad por debajo de 0,02 g/cm³: los gases a 1 atm
  tienen como mucho unos 0,01 g/cm³ y el pulmón, al menos unos 0,05): el valor
  por defecto de PENELOPE (subrutina PEMATW, ``penelope.f`` líneas 1913–1993),
  la regla de Bragg con los valores atómicos EPX, que son los de fase gaseosa.
  Para el aire da 85,7 eV, el valor de ICRU.
* **Materiales condensados** (regla «condensado»; líquidos y sólidos, todos
  los tejidos del TAC): regla de Bragg con los valores atómicos para fases
  condensadas de Berger y Seltzer (NBSIR 82-2550-A, tabla 5.1; el esquema de
  ICRU 37 y 49): H 19,2, C 81, N 82, O 106, F 112 y Cl 180 eV; para los demás
  elementos, 1,13 × el valor del elemento (EPX; el Br con 1,13 × 357 eV, su
  valor en fase condensada, porque el 343 de PENELOPE es el del gas). El
  resultado se multiplica por el factor que da al agua líquida 78,0 eV
  (ICRU 90): 78 / 75,32 ≈ 1,0356, calculado con la composición del agua.

Por qué: con los valores atómicos de PENELOPE (de fase gaseosa) la I de los
tejidos condensados sale alrededor de un 10 % baja (69,0 eV para el agua
frente a 78 eV) y el alcance de los protones, un 1,3–1,6 % corto; la I de
ICRU de un tejido parecido (lo que proponía ``materiales.txt``) lo deja un
0,3–0,9 % corto. La regla de fase condensada escalada al agua de ICRU 90 vale
para cualquier composición y reproduce los valores de ICRU de los tejidos.

Todas las funciones reciben la composición como fracciones en masa
``{Z: w}`` (no hace falta que sumen 1; las claves pueden ser textos, como en
la ficha JSON).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

# ---------------------------------------------------------------------------
# Tablas de PENELOPE (el índice es Z; el 0 no se usa)
# ---------------------------------------------------------------------------

# penelope.f (penhan 2025), BLOCK DATA PENDAT, líneas 7386–7402: DATA ATW (g/mol).
ATOMIC_WEIGHTS: tuple[float, ...] = (
    0.0,
    1.0079, 4.0026, 6.941, 9.0122, 10.811, 12.011, 14.007, 15.999, 18.998, 20.179,
    22.99, 24.305, 26.982, 28.086, 30.974, 32.066, 35.453, 39.948, 39.098, 40.078,
    44.956, 47.88, 50.942, 51.996, 54.938, 55.847, 58.933, 58.69, 63.546, 65.39,
    69.723, 72.61, 74.922, 78.96, 79.904, 83.8, 85.468, 87.62, 88.906, 91.224,
    92.906, 95.94, 97.907, 101.07, 102.91, 106.42, 107.87, 112.41, 114.82, 118.71,
    121.75, 127.6, 126.9, 131.29, 132.91, 137.33, 138.91, 140.12, 140.91, 144.24,
    144.91, 150.36, 151.96, 157.25, 158.93, 162.5, 164.93, 167.26, 168.93, 173.04,
    174.97, 178.49, 180.95, 183.85, 186.21, 190.2, 192.22, 195.08, 196.97, 200.59,
    204.38, 207.2, 208.98, 208.98, 209.99, 222.02, 223.02, 226.03, 227.03, 232.04,
    231.04, 238.03, 237.05, 239.05, 243.06, 247.07, 247.07, 251.08, 252.08,
)

# penelope.f (penhan 2025), BLOCK DATA PENDAT, líneas 7404–7420 (DATA EPX en 7406–7420):
# energías medias de excitación de los elementos (eV).
EPX: tuple[float, ...] = (
    0.0,
    19.2, 41.8, 40.0, 63.7, 76.0, 81.0, 82.0, 95.0, 115.0, 137.0,
    149.0, 156.0, 166.0, 173.0, 173.0, 180.0, 174.0, 188.0, 190.0, 191.0,
    216.0, 233.0, 245.0, 257.0, 272.0, 286.0, 297.0, 311.0, 322.0, 330.0,
    334.0, 350.0, 347.0, 348.0, 343.0, 352.0, 363.0, 366.0, 379.0, 393.0,
    417.0, 424.0, 428.0, 441.0, 449.0, 470.0, 470.0, 469.0, 488.0, 488.0,
    487.0, 485.0, 491.0, 482.0, 488.0, 491.0, 501.0, 523.0, 535.0, 546.0,
    560.0, 574.0, 580.0, 591.0, 614.0, 628.0, 650.0, 658.0, 674.0, 684.0,
    694.0, 705.0, 718.0, 727.0, 736.0, 746.0, 757.0, 790.0, 790.0, 800.0,
    810.0, 823.0, 823.0, 830.0, 825.0, 794.0, 827.0, 826.0, 841.0, 847.0,
    878.0, 890.0, 902.0, 921.0, 934.0, 939.0, 952.0, 966.0, 980.0,
)

# ---------------------------------------------------------------------------
# Constantes de la regla
# ---------------------------------------------------------------------------

WATER: dict[int, float] = {1: 0.111894, 8: 0.888106}  # agua líquida (pdcompos.pen, material 278)
I_WATER = 78.0  # eV, agua líquida (ICRU 90)
GAS_DENSITY_LIMIT = 0.02  # g/cm³: por debajo, gas

# Berger y Seltzer, NBSIR 82-2550-A, tabla 5.1 (líquidos y sólidos); los demás elementos, 1,13 × I del
# elemento en fase condensada. ESTAR (subrutina FORMEL) deja el He en 41,8 eV y redondea el Be y el B a
# 72,0 y 85,9 eV; aquí se aplica 1,13 × EPX a todos (nada de eso cambia los tejidos).
CONDENSED_ATOMIC_I: dict[int, float] = {1: 19.2, 6: 81.0, 7: 82.0, 8: 106.0, 9: 112.0, 17: 180.0}
CONDENSED_FACTOR = 1.13
CONDENSED_ELEMENT_I: dict[int, float] = {35: 357.0}  # Br líquido; EPX tiene el del gas (343 eV)

ELECTRON_MASS_MEV = 0.51099895
PROTON_MASS_MEV = 938.27208816

RULE_ELEMENT = "elemento"
RULE_GAS = "gas"
RULE_CONDENSED = "condensado"


# ---------------------------------------------------------------------------
# Funciones
# ---------------------------------------------------------------------------

def _items(composition: Mapping) -> list[tuple[int, float]]:
    """(Z, fracción) de los elementos con fracción positiva; ValueError si la composición no vale."""
    if not composition:
        raise ValueError("La composición está vacía.")
    out: dict[int, float] = {}
    for key, value in composition.items():
        try:
            z = int(key)
            w = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Elemento o fracción no válidos: {key!r}: {value!r}.") from None
        if not 1 <= z <= 99:
            raise ValueError(f"Número atómico fuera de 1-99: {z}.")
        if not math.isfinite(w) or w < 0.0:
            raise ValueError(f"Fracción en masa no válida para Z = {z}: {value!r}.")
        if z in out:
            raise ValueError(f"El elemento Z = {z} aparece dos veces.")
        out[z] = w
    items = [(z, w) for z, w in out.items() if w > 0.0]
    if not items:
        raise ValueError("Todas las fracciones en masa son cero.")
    return items


def _bragg(items: list[tuple[int, float]], atomic_i) -> float:
    """Regla de Bragg: ln I = Σ n·Z·ln I_Z / Σ n·Z con n = w/A (átomos por molécula, como PEMATW)."""
    num = 0.0
    den = 0.0
    for z, w in items:
        nz = w / ATOMIC_WEIGHTS[z] * z
        num += nz * math.log(atomic_i(z))
        den += nz
    return math.exp(num / den)


def _epx(z: int) -> float:
    return EPX[z]


def condensed_atomic_i(z: int) -> float:
    """Valor atómico de Z en un compuesto condensado (eV): Berger y Seltzer, tabla 5.1."""
    if z in CONDENSED_ATOMIC_I:
        return CONDENSED_ATOMIC_I[z]
    return CONDENSED_FACTOR * CONDENSED_ELEMENT_I.get(z, EPX[z])


def penelope_i(composition: Mapping) -> float:
    """I (eV) que calcula penhmat por defecto (PEMATW), a partir de fracciones en masa.

    Las fracciones se pasan a átomos por molécula, n_i = w_i/A_i (con los
    pesos atómicos de PENELOPE), y ln I = Σ n_i·Z_i·ln EPX(Z_i) / Σ n_i·Z_i.
    Con un solo elemento, su EPX.
    """
    return _bragg(_items(composition), _epx)


def _condensed_additivity(items: list[tuple[int, float]]) -> float:
    return _bragg(items, condensed_atomic_i)


# Factor que da 78,0 eV al agua líquida (ICRU 90): unos 1,0356.
CONDENSED_SCALE = I_WATER / _condensed_additivity(_items(WATER))


def condensed_i(composition: Mapping) -> float:
    """I (eV) de un material condensado: Bragg con los valores de fase condensada, escalada al agua.

    Valores atómicos de Berger y Seltzer (NBSIR 82-2550-A, tabla 5.1; ICRU 37
    y 49), multiplicados por :data:`CONDENSED_SCALE` para que el agua líquida
    dé 78,0 eV (ICRU 90).
    """
    return CONDENSED_SCALE * _condensed_additivity(_items(composition))


def mean_excitation_energy(composition: Mapping, density: float) -> tuple[float, str]:
    """I (eV) que ARGUS propone para un material y la regla usada («elemento», «gas» o «condensado»).

    ``density``: densidad de masa del material (g/cm³). Un solo elemento: su
    EPX; por debajo de :data:`GAS_DENSITY_LIMIT`, el valor por defecto de
    PENELOPE (gas); si no, :func:`condensed_i`. Véase el comentario del módulo.
    """
    items = _items(composition)
    if len(items) == 1:
        return EPX[items[0][0]], RULE_ELEMENT
    rho = float(density)
    if not (math.isfinite(rho) and rho > 0.0):
        raise ValueError(f"La densidad tiene que ser positiva: {density!r}.")
    if rho < GAS_DENSITY_LIMIT:
        return _bragg(items, _epx), RULE_GAS
    return CONDENSED_SCALE * _condensed_additivity(items), RULE_CONDENSED


def z_over_a(composition: Mapping) -> float:
    """<Z/A> (mol/g): Σ w_i·Z_i/A_i / Σ w_i, con los pesos atómicos de PENELOPE."""
    items = _items(composition)
    return sum(w * z / ATOMIC_WEIGHTS[z] for z, w in items) / sum(w for _z, w in items)


WATER_Z_OVER_A = z_over_a(WATER)  # 0,5551 con los pesos atómicos de PENELOPE (ICRU: 0,55509)


def _stopping_number(i_ev: float, e_mev: float) -> float:
    """L = ½·ln(2·mc²·β²γ²·W_max / I²) − β² de la fórmula de Bethe para un protón (sin correcciones)."""
    i = float(i_ev)
    e = float(e_mev)
    if not (math.isfinite(i) and i > 0.0):
        raise ValueError(f"La energía media de excitación tiene que ser positiva: {i_ev!r}.")
    if not (math.isfinite(e) and e > 0.0):
        raise ValueError(f"La energía del protón tiene que ser positiva: {e_mev!r}.")
    gamma = 1.0 + e / PROTON_MASS_MEV
    beta2 = 1.0 - 1.0 / (gamma * gamma)
    bg2 = beta2 * gamma * gamma
    r = ELECTRON_MASS_MEV / PROTON_MASS_MEV
    wmax = 2.0 * ELECTRON_MASS_MEV * bg2 / (1.0 + 2.0 * gamma * r + r * r)  # MeV
    i_mev = i * 1e-6
    number = 0.5 * math.log(2.0 * ELECTRON_MASS_MEV * bg2 * wmax / (i_mev * i_mev)) - beta2
    if number <= 0.0:
        raise ValueError(f"La fórmula de Bethe no vale para {e:g} MeV con I = {i:g} eV.")
    return number


def relative_stopping_power(composition: Mapping, i_ev: float, e_mev: float = 70.0) -> float:
    """Poder de frenado másico de un protón relativo al del agua (fórmula de Bethe).

    Sin correcciones de capas ni de densidad; el agua con I = 78 eV y su <Z/A>
    calculado con su composición. A 70 MeV y con la I por defecto de
    PENELOPE, reproduce los .mat de penhmat de los tejidos del TAC (aire 0,888,
    pulmón 1,009, adiposo 1,034, blando 1,009, hueso 0,931) con menos del 1 %.
    """
    return (z_over_a(composition) * _stopping_number(i_ev, e_mev)
            / (WATER_Z_OVER_A * _stopping_number(I_WATER, e_mev)))


__all__ = ["ATOMIC_WEIGHTS", "CONDENSED_SCALE", "EPX", "GAS_DENSITY_LIMIT", "I_WATER", "RULE_CONDENSED",
           "RULE_ELEMENT", "RULE_GAS", "WATER", "condensed_atomic_i", "condensed_i", "mean_excitation_energy",
           "penelope_i", "relative_stopping_power", "z_over_a"]
