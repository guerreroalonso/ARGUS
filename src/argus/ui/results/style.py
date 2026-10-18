"""Estilo de las gráficas de resultados (matplotlib).

Paleta categórica de 8 colores en orden fijo (validada para daltonismo: ΔE
adyacente ≥ 9,1 y ≥ 19,6 sin simular; tres colores tienen poco contraste con el
fondo, así que siempre hay leyenda con dos o más series y una vista de tabla).
El color sigue a la entidad: cada tipo de partícula tiene siempre el suyo, y en
las comparaciones cada conjunto de datos toma el color de su orden.

* Mapas: rampa secuencial de un solo tono (azul, claro → oscuro) o «calor»
  (inferno, para superponer en la geometría).
* Diferencias, cocientes e índice gamma: rampa divergente azul ↔ rojo con gris
  en el centro.
* Líneas de 2 px, bandas de incertidumbre al 15 %, rejilla fina y discreta.
"""

from __future__ import annotations

from functools import cache

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
NEUTRAL_MID = "#f0efec"

CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
# Cada tipo de partícula, siempre del mismo color (orden de KPAR: e−, γ, e+, p, α, n); el total, en tinta.
PARTICLE_COLORS = {None: INK, 0: INK, 1: CATEGORICAL[0], 2: CATEGORICAL[1], 3: CATEGORICAL[2], 4: CATEGORICAL[3],
                   5: CATEGORICAL[4], 6: CATEGORICAL[5]}
BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
             "#1c5cab", "#184f95", "#104281", "#0d366b"]  # pasos 100..700
LINE_WIDTH = 1.5  # puntos: ~2 px
BAND_ALPHA = 0.15


def particle_color(kpar: int | None) -> str:
    return PARTICLE_COLORS.get(kpar, INK)


def dataset_color(i: int) -> str:
    """Color del i-ésimo conjunto de datos en una comparación (el primero, en tinta)."""
    return INK if i == 0 else CATEGORICAL[(i - 1) % len(CATEGORICAL)]


def apply_style() -> None:
    import matplotlib as mpl

    mpl.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": "white",
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8,
        "axes.labelcolor": INK_2,
        "axes.titlecolor": INK,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "grid.linestyle": "-",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": INK_2,
        "ytick.labelcolor": INK_2,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "legend.labelcolor": INK_2,
        "lines.linewidth": LINE_WIDTH,
        "lines.solid_capstyle": "round",
        "lines.solid_joinstyle": "round",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.family": "sans-serif",
        "axes.formatter.use_mathtext": True,
        "axes.formatter.limits": (-3, 4),
        "image.origin": "lower",
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })


@cache
def colormap(name: str):
    """Rampas: 'azul' (secuencial), 'azul_lineas' (secuencial sin los tonos más claros, para líneas), 'calor'
    (inferno), 'divergente' (azul ↔ gris ↔ rojo) y 'grises'."""
    import matplotlib as mpl
    from matplotlib.colors import LinearSegmentedColormap

    if name == "azul":
        return LinearSegmentedColormap.from_list("argus_azul", ["#f4f8fd"] + BLUE_RAMP)
    if name == "azul_lineas":  # para líneas finas sobre el fondo claro: sin los pasos más claros
        return LinearSegmentedColormap.from_list("argus_azul_lineas", BLUE_RAMP[3:])
    if name == "calor":
        return mpl.colormaps["inferno_r"]
    if name == "divergente":
        return LinearSegmentedColormap.from_list(
            "argus_div", ["#0d366b", "#2a78d6", "#9ec5f4", NEUTRAL_MID, "#f2aaa9", "#e34948", "#8f1d1c"])
    if name == "grises":
        return mpl.colormaps["Greys"]
    return mpl.colormaps[name]


CMAP_TITLES = {"azul": "Azul (secuencial)", "calor": "Calor", "grises": "Grises"}

__all__ = ["AXIS", "BAND_ALPHA", "BLUE_RAMP", "CATEGORICAL", "CMAP_TITLES", "GRID", "INK", "INK_2", "LINE_WIDTH",
           "MUTED", "NEUTRAL_MID", "PARTICLE_COLORS", "SURFACE", "apply_style", "colormap", "dataset_color",
           "particle_color"]
