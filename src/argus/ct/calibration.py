"""Calibración de un TAC: HU → densidad y HU → material.

penhmain no entiende de unidades Hounsfield: cada vóxel del .ct lleva un
número de material (un .mat del .in) y una densidad de masa. La traducción
tiene dos partes:

* una **curva HU → densidad** (tramos rectos entre puntos), que depende del
  TAC (en el hospital se mide con un maniquí de calibración);
* unos **rangos de HU → material** (tejidos), cada uno con su composición.

Por defecto se usa la curva de Schneider, Bortfeld y Schlegel (2000), tal
como la distribuye GATE (Schneider2000DensitiesTable.txt), y cinco tejidos
con sus composiciones (Schneider2000MaterialsTable.txt) reducidas a H, C, N,
O, P y Ca (más N, O y Ar en el aire): con protones, penhmain admite como
mucho MNIS = 30 isótopos con datos ENDF entre todos los materiales, y los
tejidos ICRP completos (con Na, Mg, S, Cl, K, Fe, Zn) pasan de 30. Cada
tejido tiene un **HU representativo**: su composición es la del intervalo de
Schneider que lo contiene y su densidad nominal (la que se da a penhmat) la
de la curva en ese HU.

Los límites son los de los intervalos de Schneider: aire < −950 ≤ pulmón <
−120 ≤ adiposo < −22 ≤ tejido blando < 120 ≤ hueso. Los intervalos de
Schneider entre −22 y 18 HU (AT_AG_SI4 y SI5) tienen ya casi la composición
del tejido blando; con el límite en −22 el agua (0 HU) y los tejidos blandos
no se toman por grasa.
"""

from __future__ import annotations

import codecs
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------

SYMBOLS = ("", "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
           "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
           "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I",
           "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
           "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr",
           "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es")

# Isótopos con datos ENDF de protones que elige penhmat (columna 5 = 1 en
# database/ENDF_data/isotopic_abundances.tab de penhan 2025). El hidrógeno no tiene.
ENDF_ISOTOPES: dict[int, tuple[int, ...]] = {
    5: (10, 11), 6: (12, 13), 7: (14, 15), 8: (16, 17, 18), 9: (19,), 10: (20, 21, 22), 11: (23,),
    12: (24, 25, 26), 13: (27,), 14: (28, 29, 30), 15: (31,), 16: (32, 33, 34, 36), 17: (35, 37),
    18: (36, 38, 40), 19: (39, 40, 41), 20: (40, 42, 43, 44, 46, 48), 21: (45,), 22: (46, 47, 48, 49, 50),
    23: (50, 51), 24: (50, 52, 53, 54), 25: (55,), 26: (54, 56, 57, 58), 27: (59,), 28: (58, 60, 61, 62, 64),
    29: (63, 65), 30: (64, 66, 67, 68, 70), 31: (69, 71), 32: (70, 72, 73, 74, 76), 33: (75,),
    34: (74, 76, 77, 78, 80, 82), 35: (79, 81), 36: (78, 80, 82, 83, 84, 86), 37: (85, 87), 38: (84, 86, 87, 88),
    39: (89,), 40: (90, 91, 92, 94, 96), 41: (93,), 42: (92, 94, 95, 96, 97, 98, 100), 43: (98,),
    44: (96, 98, 99, 100, 101, 102, 104), 45: (103,), 46: (102, 104, 105, 106, 108, 110), 47: (107, 109),
    48: (106, 108, 110, 111, 112, 113, 114, 116), 49: (113, 115), 50: (112, 114, 115, 116, 117, 118, 119, 120, 122,
                                                                      124),
    51: (121, 123), 52: (120, 122, 123, 124, 125, 126, 128, 130), 53: (127,),
    54: (124, 126, 128, 129, 130, 131, 132, 134, 136), 55: (133,), 56: (130, 132, 134, 135, 136, 137, 138),
    57: (138, 139), 58: (136, 138, 140, 142), 59: (141,), 60: (142, 143, 144, 145, 146, 148, 150), 61: (145,),
    62: (144, 147, 148, 149, 150, 152, 154), 63: (151, 153), 64: (152, 154, 155, 156, 157, 158, 160), 65: (159,),
    66: (156, 158, 160, 161, 162, 163, 164), 67: (165,), 68: (162, 164, 166, 167, 168, 170), 69: (169,),
    70: (168, 170, 171, 172, 173, 174, 176), 71: (175, 176), 72: (174, 176, 177, 178, 179, 180), 73: (180, 181),
    74: (180, 182, 183, 184, 186), 75: (185, 187), 76: (184, 186, 187, 188, 189, 190, 192), 77: (191, 193),
    78: (190, 192, 194, 195, 196, 198), 79: (197,), 80: (196, 198, 199, 200, 201, 202, 204), 81: (203, 205),
    82: (204, 206, 207, 208), 83: (209,), 84: (209,), 85: (210,), 86: (222,), 87: (223,), 88: (226,), 89: (227,),
    90: (232,), 91: (231,), 92: (234, 235, 238), 93: (237,), 94: (244,), 95: (243,), 96: (247,), 97: (247,),
    98: (251,), 99: (252,),
}
MNIS = 30  # penhan.f: isótopos con datos ENDF entre todos los materiales

# Curva de Schneider et al. (2000) tal como la distribuye GATE (Schneider2000DensitiesTable.txt).
SCHNEIDER_CURVE: tuple[tuple[float, float], ...] = (
    (-1000.0, 0.00121), (-98.0, 0.93), (-97.0, 0.930486), (14.0, 1.03), (23.0, 1.031), (100.0, 1.1199),
    (101.0, 1.0762), (1600.0, 1.9642), (3000.0, 2.8),
)


def _norm(fr: dict[int, float]) -> dict[int, float]:
    s = sum(fr.values())
    return {z: round(v / s, 6) for z, v in fr.items() if v > 0}


# Composiciones (% en masa) de Schneider2000MaterialsTable.txt, sin los elementos traza.
SCHNEIDER_AIR = _norm({7: 75.5, 8: 23.2, 18: 1.3})
SCHNEIDER_LUNG = _norm({1: 10.3, 6: 10.5, 7: 3.1, 8: 74.9, 15: 0.2})  # sin Na 0,2 S 0,3 Cl 0,3 K 0,2
SCHNEIDER_ADIPOSE = _norm({1: 11.6, 6: 68.1, 7: 0.2, 8: 19.8})  # AT_AG_SI1; sin Na 0,1 S 0,1 Cl 0,1
SCHNEIDER_SOFT = _norm({1: 10.3, 6: 13.4, 7: 3.0, 8: 72.3, 15: 0.2})  # sin Na 0,2 S 0,2 Cl 0,2 K 0,2
SCHNEIDER_BONE = _norm({1: 5.6, 6: 26.5, 7: 3.6, 8: 40.5, 15: 7.3, 20: 15.9})  # Marrow_Bone08; sin Na, Mg, S

# Composiciones de la lista de PENELOPE (pdcompos.pen de penhan 2025), para contar isótopos.
PENELOPE_COMPOSITIONS: dict[int, dict[int, float]] = {
    103: {1: 0.119477, 6: 0.63724, 7: 0.00797, 8: 0.232333, 11: 0.0005, 12: 2e-05, 15: 0.00016, 16: 0.00073,
          17: 0.00119, 19: 0.00032, 20: 2e-05, 26: 2e-05, 30: 2e-05},
    104: {6: 0.000124, 7: 0.755267, 8: 0.231781, 18: 0.012827},
    119: {1: 0.063984, 6: 0.278, 7: 0.027, 8: 0.410016, 12: 0.002, 15: 0.07, 16: 0.002, 20: 0.147},
    120: {1: 0.047234, 6: 0.14433, 7: 0.04199, 8: 0.446096, 12: 0.0022, 15: 0.10497, 16: 0.00315, 20: 0.20993,
          30: 0.0001},
    123: {1: 0.110667, 6: 0.12542, 7: 0.01328, 8: 0.737723, 11: 0.00184, 12: 0.00015, 15: 0.00354, 16: 0.00177,
          17: 0.00236, 19: 0.0031, 20: 9e-05, 26: 5e-05, 30: 1e-05},
    191: {1: 0.101278, 6: 0.10231, 7: 0.02865, 8: 0.757072, 11: 0.00184, 12: 0.00073, 15: 0.0008, 16: 0.00225,
          17: 0.00266, 19: 0.00194, 20: 9e-05, 26: 0.00037, 30: 1e-05},
    202: {1: 0.100637, 6: 0.10783, 7: 0.02768, 8: 0.754773, 11: 0.00075, 12: 0.00019, 15: 0.0018, 16: 0.00241,
          17: 0.00079, 19: 0.00302, 20: 3e-05, 26: 4e-05, 30: 5e-05},
    262: {1: 0.104472, 6: 0.23219, 7: 0.02488, 8: 0.630238, 11: 0.00113, 12: 0.00013, 15: 0.00133, 16: 0.00199,
          17: 0.00134, 19: 0.00199, 20: 0.00023, 26: 5e-05, 30: 3e-05},
    278: {1: 0.111894, 8: 0.888106},
}
PENELOPE_DENSITY = {103: 0.92, 104: 1.20479e-3, 119: 1.85, 120: 1.85, 123: 1.03, 191: 0.30, 202: 1.04,
                    262: 1.00, 278: 1.0}
PENELOPE_IVALUE = {103: 63.2, 104: 85.7, 119: 91.9, 120: 106.4, 123: 73.3, 191: 75.3, 202: 75.3, 262: 72.3}


# ---------------------------------------------------------------------------
# Tejidos y calibración
# ---------------------------------------------------------------------------

@dataclass
class Tissue:
    """Un rango de HU que se convierte en un material."""

    name: str
    hu_min: float  # desde este HU (incluido) hasta el siguiente tejido; el primero, desde −∞
    mat_file: str  # fichero .mat que se propone
    density: float  # densidad nominal (g/cm³) para penhmat
    composition: dict[int, float] | None = None  # Z -> fracción en masa (penhmat opción 1)
    penelope_id: int | None = None  # número en la lista de PENELOPE (penhmat opción 2)
    source: str = ""  # de dónde sale
    reference_i: float | None = None  # energía media de excitación de ICRU de un tejido parecido (eV)
    voxel_density: float | None = None  # densidad media de sus vóxeles en un TAC importado (g/cm³; de la ficha)
    # Material de un tejido partido por densidad (CTTRACK, ADR-011): el tejido del que sale y sus densidades
    # (desde, hasta; None en los extremos abiertos).
    parent: str | None = None
    density_cut: tuple[float | None, float | None] | None = None

    def elements(self) -> list[int]:
        if self.composition:
            return sorted(z for z, f in self.composition.items() if f > 0)
        if self.penelope_id in PENELOPE_COMPOSITIONS:
            return sorted(PENELOPE_COMPOSITIONS[self.penelope_id])
        return []

    def isotopes(self) -> set[tuple[int, int]]:
        return {(z, a) for z in self.elements() for a in ENDF_ISOTOPES.get(z, ())}

    def formula(self) -> str:
        """«H 10,40 % · C 10,61 % · …» (fracciones en masa)."""
        comp = self.composition or PENELOPE_COMPOSITIONS.get(self.penelope_id or -1)
        if not comp:
            return ""
        parts = [f"{SYMBOLS[z]} {100 * f:.2f} %".replace(".", ",") for z, f in sorted(comp.items())]
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return {"nombre": self.name, "hu_desde": _json_hu(self.hu_min), "fichero_mat": self.mat_file,
                "densidad_nominal": self.density,
                "composicion": {str(z): f for z, f in sorted((self.composition or {}).items())} or None,
                "penelope": self.penelope_id, "origen": self.source, "i_referencia": self.reference_i,
                **({"densidad_media": self.voxel_density} if self.voxel_density is not None else {}),
                **({"tejido": self.parent} if self.parent is not None else {}),
                **({"densidades": list(self.density_cut)} if self.density_cut is not None else {})}

    @classmethod
    def from_dict(cls, d: dict) -> Tissue:
        comp = d.get("composicion")
        cut = d.get("densidades")
        density_cut = None
        if isinstance(cut, (list, tuple)) and len(cut) == 2:
            density_cut = (_positive(cut[0]), _positive(cut[1]))
        return cls(name=str(d["nombre"]), hu_min=_hu_json(d.get("hu_desde")), mat_file=str(d.get("fichero_mat", "")),
                   density=float(d.get("densidad_nominal", 1.0)),
                   composition={int(z): float(f) for z, f in comp.items()} if comp else None,
                   penelope_id=d.get("penelope"), source=str(d.get("origen", "")), reference_i=d.get("i_referencia"),
                   voxel_density=_positive(d.get("densidad_media")),
                   parent=str(d["tejido"]) if d.get("tejido") else None, density_cut=density_cut)


def _positive(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _json_hu(v: float):
    return None if not math.isfinite(v) else v


def _hu_json(v) -> float:
    return float("-inf") if v is None else float(v)


@dataclass
class Calibration:
    name: str
    curve: list[tuple[float, float]]
    tissues: list[Tissue] = field(default_factory=list)
    key: str = "personal"

    # -- conversión -------------------------------------------------------------------
    def density(self, hu) -> np.ndarray:
        """Densidad (g/cm³) de cada HU: tramos rectos; fuera de la curva, el valor del extremo."""
        h = np.array([p[0] for p in self.curve], dtype=float)
        r = np.array([p[1] for p in self.curve], dtype=float)
        return np.interp(np.asarray(hu, dtype=float), h, r).astype(np.float64)

    def thresholds(self) -> np.ndarray:
        return np.array([t.hu_min for t in self.tissues[1:]], dtype=float)

    def tissue_index(self, hu) -> np.ndarray:
        """Índice (0, 1, ...) del tejido de cada HU."""
        return np.searchsorted(self.thresholds(), np.asarray(hu, dtype=float), side="right").astype(np.int32)

    # -- comprobaciones -------------------------------------------------------------------
    def problems(self) -> list[str]:
        out = []
        if len(self.curve) < 2:
            out.append("La curva HU → densidad necesita al menos dos puntos.")
        hs = [p[0] for p in self.curve]
        if any(b <= a for a, b in zip(hs, hs[1:])):
            out.append("Los HU de la curva tienen que ir en orden creciente y sin repetirse.")
        if any(not (p[1] > 0) for p in self.curve):
            out.append("Las densidades de la curva tienen que ser positivas.")
        if not self.tissues:
            out.append("Hace falta al menos un tejido.")
        th = [t.hu_min for t in self.tissues[1:]]
        if any(b <= a for a, b in zip(th, th[1:])):
            out.append("Los tejidos tienen que ir en orden de HU creciente.")
        for t in self.tissues:
            if not t.name.strip():
                out.append("Hay un tejido sin nombre.")
            if not (t.density > 0):
                out.append(f"«{t.name}»: la densidad nominal tiene que ser positiva.")
            if t.composition is not None:
                if not t.composition:
                    out.append(f"«{t.name}»: la composición está vacía.")
                elif any(z < 1 or z > 99 for z in t.composition):
                    out.append(f"«{t.name}»: número atómico fuera de 1-99.")
                elif len(t.composition) > 30:
                    out.append(f"«{t.name}»: penhmat admite como mucho 30 elementos.")
                elif any(not (f > 0) for f in t.composition.values()):
                    out.append(f"«{t.name}»: las fracciones en masa tienen que ser positivas.")
            elif t.penelope_id is not None and not (1 <= t.penelope_id <= 300):
                out.append(f"«{t.name}»: los números de la lista de PENELOPE van de 1 a 300.")
        return out

    def warnings(self) -> list[str]:
        """Avisos que no impiden importar (se muestran en el diálogo)."""
        out = []
        if self.curve:
            hs = [p[0] for p in self.curve]
            def es(v: float, fmt: str = "g") -> str:
                return format(v, fmt).replace(".", ",")

            if min(hs) > -950:
                out.append(f"La curva empieza en {es(min(hs))} HU: por debajo, todos los vóxeles (el aire) tendrán "
                           f"la densidad de ese punto ({es(self.curve[0][1], '.4g')} g/cm³).")
            if max(hs) < 1000:
                out.append(f"La curva acaba en {es(max(hs))} HU: por encima (hueso), todos los vóxeles tendrán la "
                           f"densidad de ese punto ({es(self.curve[-1][1], '.4g')} g/cm³).")
            if any(p[1] > 5.0 for p in self.curve):
                out.append("Hay densidades de más de 5 g/cm³ en la curva (¿metales?).")
        for t in self.tissues:
            if t.composition:
                s = sum(t.composition.values())
                if abs(s - 1.0) > 0.01:
                    total = f"{s:.3f}".replace(".", ",")
                    out.append(f"«{t.name}»: las fracciones en masa suman {total}; penhmat las normaliza.")
        return out

    def isotopes(self, used: list[int] | None = None) -> set[tuple[int, int]]:
        """Isótopos ENDF de los tejidos (``used``: índices de los tejidos que aparecen)."""
        out: set[tuple[int, int]] = set()
        for i, t in enumerate(self.tissues):
            if used is None or i in used:
                out |= t.isotopes()
        return out

    # -- JSON -----------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"nombre": self.name, "clave": self.key, "curva": [[h, r] for h, r in self.curve],
                "tejidos": [t.to_dict() for t in self.tissues]}

    @classmethod
    def from_dict(cls, d: dict) -> Calibration:
        return cls(name=str(d.get("nombre", "Calibración")), curve=[(float(h), float(r)) for h, r in d["curva"]],
                   tissues=[Tissue.from_dict(t) for t in d.get("tejidos", [])], key=str(d.get("clave", "personal")))

    def copy(self) -> Calibration:
        return Calibration.from_dict(json.loads(json.dumps(self.to_dict())))


# ---------------------------------------------------------------------------
# Preajustes
# ---------------------------------------------------------------------------

def _rep(curve, hu: float) -> float:
    h = np.array([p[0] for p in curve])
    r = np.array([p[1] for p in curve])
    v = float(np.interp(hu, h, r))
    return float(f"{v:.4g}")


def schneider_tissues() -> Calibration:
    """Cinco tejidos de Schneider (2000) simplificados (17 isótopos ENDF): la calibración por defecto."""
    c = list(SCHNEIDER_CURVE)
    src = "Schneider et al. 2000 ({}), sin elementos traza"
    tissues = [
        Tissue("Aire", float("-inf"), "ct_aire.mat", _rep(c, -1000), SCHNEIDER_AIR, None,
               "Schneider et al. 2000 (Air)", PENELOPE_IVALUE[104]),
        Tissue("Pulmón", -950.0, "ct_pulmon.mat", _rep(c, -750), SCHNEIDER_LUNG, None,
               src.format("Lung; HU representativo −750"), PENELOPE_IVALUE[191]),
        Tissue("Adiposo", -120.0, "ct_adiposo.mat", _rep(c, -100), SCHNEIDER_ADIPOSE, None,
               src.format("AT_AG_SI1; HU representativo −100"), PENELOPE_IVALUE[103]),
        Tissue("Tejido blando", -22.0, "ct_blando.mat", _rep(c, 40), SCHNEIDER_SOFT, None,
               src.format("SoftTissue; HU representativo 40"), PENELOPE_IVALUE[262]),
        Tissue("Hueso", 120.0, "ct_hueso.mat", _rep(c, 800), SCHNEIDER_BONE, None,
               src.format("Marrow_Bone08; HU representativo 800"), PENELOPE_IVALUE[119]),
    ]
    return Calibration("Schneider 2000 · 5 tejidos (H C N O P Ca)", c, tissues, key="schneider5")


def penelope_tissues() -> Calibration:
    """Los mismos rangos con tejidos ICRP de la lista de PENELOPE (penhmat opción 2; más de 30 isótopos)."""
    c = list(SCHNEIDER_CURVE)

    def t(name, hu, fname, pid):
        return Tissue(name, hu, fname, PENELOPE_DENSITY[pid], None, pid, f"Lista de PENELOPE ({pid})",
                      PENELOPE_IVALUE.get(pid))

    tissues = [t("Aire", float("-inf"), "air.mat", 104), t("Pulmón", -950.0, "lung.mat", 191),
               t("Adiposo", -120.0, "adipose.mat", 103), t("Tejido blando", -22.0, "softtissue.mat", 262),
               t("Hueso", 120.0, "bone_cortical.mat", 120)]
    return Calibration("Tejidos ICRP de la lista de PENELOPE", c, tissues, key="penelope")


def water_air() -> Calibration:
    """Aire por debajo de −950 HU y agua con densidad variable en el resto."""
    c = list(SCHNEIDER_CURVE)
    tissues = [Tissue("Aire", float("-inf"), "air.mat", PENELOPE_DENSITY[104], None, 104, "Lista de PENELOPE (104)"),
               Tissue("Agua", -950.0, "water.mat", 1.0, None, 278, "Lista de PENELOPE (278)")]
    return Calibration("Agua y aire (densidad variable)", c, tissues, key="agua")


PRESETS = {"schneider5": schneider_tissues, "penelope": penelope_tissues, "agua": water_air}


def preset(key: str) -> Calibration:
    return PRESETS[key]()


# ---------------------------------------------------------------------------
# Curva desde un fichero (CSV de una hoja de cálculo, como la del planificador)
# ---------------------------------------------------------------------------

# Signos menos tipográficos (copiados de un PDF o de Word) y espacios que separan los miles.
_CLEAN = str.maketrans({"−": "-", "–": "-", "‒": "-", "‐": "-", " ": "", " ": "",
                        " ": "", " ": ""})
_PLAIN = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
# Separadores de miles: el primer grupo no empieza por 0 («0.001» nunca es mil).
_GROUP_DOT = re.compile(r"[-+]?[1-9]\d{0,2}(?:\.\d{3})+(?:,\d*)?(?:[eE][-+]?\d+)?")  # 1.000,5 (miles con punto)
_GROUP_COMMA = re.compile(r"[-+]?[1-9]\d{0,2}(?:,\d{3})+(?:\.\d*)?(?:[eE][-+]?\d+)?")  # 1,000.5 (miles con coma)
_LOOKS_NUMERIC = re.compile(r"[-+]?[.,]?\d")


def _cell_number(cell: str, comma_decimal: bool) -> float | None:
    """Número de una celda con coma o punto decimal (y separador de miles); None si no es un número."""
    c = cell.strip().strip("\"'").strip().translate(_CLEAN)
    if not c:
        return None
    if comma_decimal:
        if _GROUP_DOT.fullmatch(c):
            c = c.replace(".", "")
        if "." in c:
            return None
        c = c.replace(",", ".")
    else:
        if _GROUP_COMMA.fullmatch(c):
            c = c.replace(",", "")
        if "," in c:
            return None
    return float(c) if _PLAIN.fullmatch(c) else None


def _split_cells(line: str) -> tuple[list[str], bool]:
    """Celdas de una línea y si en ellas puede haber coma decimal (no, si la coma separa columnas).

    Las celdas entre comillas se respetan (un CSV con «"1,500"» tiene el
    separador de miles dentro de la celda).
    """
    import csv

    if ";" in line or "\t" in line:
        delim = ";" if ";" in line else "\t"
        return next(csv.reader([line], delimiter=delim)), True
    if re.search(r",\s", line):  # «-1000, 0.00121»: una coma seguida de un blanco nunca es decimal
        return next(csv.reader([line], delimiter=",", skipinitialspace=True)), False
    if '"' not in line and re.search(r"\s", line.strip()):
        return line.split(), True
    if '"' in line and "," not in line:
        return next(csv.reader([line], delimiter=" ", skipinitialspace=True)), True
    return next(csv.reader([line], delimiter=",", skipinitialspace=True)), False


def _parse_rows(rows: list[tuple[int, list[str]]], comma_decimal: bool) -> list[tuple[float, float]] | str:
    """Puntos con una convención decimal, o el motivo por el que una fila no se entiende."""
    pts = []
    for lineno, cells in rows:
        vals = [_cell_number(c, comma_decimal) for c in cells]
        idx = [i for i, v in enumerate(vals) if v is not None]
        if len(idx) < 2:
            return f"línea {lineno}: se esperaban dos números (HU y densidad) y se lee «{';'.join(cells).strip()}»"
        a, b = idx[0], idx[1]
        if any(not cells[i].strip() or vals[i] is None for i in range(a + 1, b)):
            return f"línea {lineno}: hay una celda vacía o con texto entre el HU y la densidad"
        pts.append((vals[a], vals[b]))
    return pts


def _plausible(pts: list[tuple[float, float]]) -> bool:
    return all(-2000.0 <= h <= 60000.0 and 0.0 < r < 30.0 for h, r in pts)


def parse_curve_text(text: str) -> list[tuple[float, float]]:
    """Puntos (HU, densidad) de un texto: dos números por línea (coma o punto decimal, «;», tabuladores).

    Las líneas sin números (cabeceras, comentarios tras «#») se saltan; una
    etiqueta antes de los números también. Una fila con números que no se
    entiende es un error (nunca se salta ni se lee a medias): un solo número,
    una celda vacía entre el HU y la densidad, un punto o una coma que no son
    ni decimales ni de miles. Se aceptan el separador de miles («1.000,5» o
    «1,000.5»), comillas y el signo menos tipográfico (−). Si una fila se puede
    leer de dos maneras con sentido (p. ej. «1.964»), es un error.
    """
    text = text.lstrip("﻿")
    rows: list[tuple[int, list[str]]] = []
    comma_ok = True
    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        ln = raw.split("#")[0].strip()
        if not ln:
            continue
        cells, can_comma = _split_cells(ln)
        if not any(_LOOKS_NUMERIC.match(c.strip().strip("\"'").strip().translate(_CLEAN)) for c in cells):
            continue  # cabecera o texto
        comma_ok = comma_ok and can_comma
        rows.append((n, cells))
    options = [False, True] if comma_ok else [False]
    parsed = {c: _parse_rows(rows, c) for c in options}
    good = {c: p for c, p in parsed.items() if not isinstance(p, str)}
    plausible = {c: p for c, p in good.items() if _plausible(p)}
    choice = plausible
    if not choice:
        if good:  # se lee, pero con valores sin sentido (p. ej. «1.964» tomado como 1964 g/cm³)
            pts = next(iter(good.values()))
            h, r = next(q for q in pts if not _plausible([q]))
            raise ValueError(f"La curva no tiene sentido: sale el punto {h:g} HU → {r:g} g/cm³ (los HU van de "
                             "−2000 a 60000 y las densidades, de más de 0 a 30 g/cm³). ¿Se mezclan la coma y el "
                             "punto decimal, o separadores de miles?")
        raise ValueError("No se entiende la curva: " + next(p for p in parsed.values() if isinstance(p, str)) + ".")
    if len(choice) > 1 and choice[True] != choice[False]:
        amb = next(f"{a[0]:g} / {a[1]:g} o {b[0]:g} / {b[1]:g}" for a, b in zip(choice[False], choice[True])
                   if a != b)
        raise ValueError("La curva se puede leer con punto o con coma decimal y sale distinta (p. ej. "
                         f"{amb}): use la misma marca decimal en todo el fichero.")
    pts = next(iter(choice.values()))
    if len(pts) < 2:
        raise ValueError("No se han encontrado al menos dos filas con HU y densidad.")
    pts.sort(key=lambda p: p[0])
    return pts


def decode_curve_bytes(raw: bytes) -> str:
    """Texto de un fichero de curva hecho en Windows.

    Primero el BOM (UTF-8, o UTF-16: el «Texto Unicode» de Excel); sin BOM, UTF-16 solo si hay bytes nulos (un texto
    en UTF-8 o cp1252 no los tiene), luego UTF-8 y, si no lo es, cp1252 (el «CSV» de Excel en español). Probar
    «utf-16» a ciegas no vale: descodifica sin error cualquier fichero de longitud par (y la curva salía ilegible).
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw:  # UTF-16 sin BOM: los bytes nulos son la mitad alta de los caracteres ASCII
        little = raw[1::2].count(0) >= raw[0::2].count(0)
        return raw.decode("utf-16-le" if little else "utf-16-be", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def read_curve_file(path: str | Path) -> list[tuple[float, float]]:
    return parse_curve_text(decode_curve_bytes(Path(path).read_bytes()))


# ---------------------------------------------------------------------------
# Instrucciones para penhmat
# ---------------------------------------------------------------------------

PENHMAT_FCB = "¿Cambiar Fcb y Wcb (plasmón)? 2 (no)"
PENHMAT_END = "Pulse Intro (penhmat termina)."


def penhmat_composition(t: Tissue) -> dict[int, float] | None:
    """Composición que se escribe en penhmat (opción 1): la del tejido o, si es de la lista de PENELOPE y se
    conoce, la de la lista (así la densidad puede ser la de sus vóxeles). None: opción 2 (densidad de la lista)."""
    if t.composition:
        return dict(t.composition)
    comp = PENELOPE_COMPOSITIONS.get(t.penelope_id) if t.penelope_id is not None else None
    return dict(comp) if comp else None


def excitation_energy(t: Tissue, density: float | None = None) -> tuple[float, str] | None:
    """(I en eV, regla) que ARGUS da a penhmat para el tejido (None con la opción 2: la de la lista).

    Regla general (:func:`argus.ct.elements.mean_excitation_energy`), la misma para cualquier composición:
    un elemento, su valor; un gas (menos de 0,02 g/cm³), el que calcula PENELOPE; un sólido o un líquido, la
    aditividad de Bragg con los valores atómicos de fase condensada de ICRU 37, con el agua en 78 eV (ICRU 90).

    Si es gas o no lo decide la densidad nominal del tejido (la misma para todos sus trozos: los vóxeles de aire
    junto a la piel no lo convierten en un sólido); ``density`` (la de sus vóxeles) solo se usa si el tejido no
    tiene densidad nominal.
    """
    from .elements import mean_excitation_energy

    comp = penhmat_composition(t)
    if not comp:
        return None
    rho = next((float(v) for v in (t.density, density, 1.0)
                if isinstance(v, (int, float)) and math.isfinite(v) and v > 0), 1.0)
    return mean_excitation_energy(comp, rho)


def _i_text(value: float) -> str:
    return f"{value:.1f}"


def _penhmat_dialog(t: Tissue, mat_file: str | None, density: float | None) -> list[tuple[str, str | None]]:
    """(texto para el usuario, respuesta que se escribe en penhmat o None) de cada paso del diálogo."""
    from .elements import RULE_CONDENSED, RULE_GAS

    fname = mat_file or t.mat_file
    end = [(PENHMAT_FCB, "2"), (f"Nombre del fichero de salida: {fname}", fname), (PENHMAT_END, "")]
    comp_map = penhmat_composition(t)
    if not comp_map:
        return [("Opción 2 (leer la composición de pdcompos.pen).", "2"),
                (f"Número del material: {t.penelope_id}", str(t.penelope_id))] + end
    import unicodedata

    comp = sorted(comp_map.items())
    ascii_name = unicodedata.normalize("NFKD", t.name).encode("ascii", "ignore").decode()  # consola de penhmat
    name = f"{ascii_name} (TAC)"
    steps: list[tuple[str, str | None]] = [
        ("Opción 1 (composición por teclado).", "1"),
        (f"Nombre del material: {name}", name),
        (f"Número de elementos: {len(comp)}", str(len(comp)))]
    if len(comp) > 1:
        steps.append(("Opción 2 (fracción en masa de cada elemento).", "2"))
        steps.append(("Número atómico y fracción en masa de cada elemento (una línea por elemento):", None))
        for z, f in comp:
            steps.append((f"  {z} {f:.6f}      ({SYMBOLS[z]})", f"{z} {f:.6f}"))
    else:
        steps.append((f"Número atómico: {comp[0][0]}", str(comp[0][0])))
    rho = t.density if density is None else density
    i_val, rule = excitation_energy(t, rho) or (0.0, "")
    if rule == RULE_CONDENSED:
        steps.append((f"¿Cambiar la energía media de excitación? 1 (sí) y {_i_text(i_val)} eV (regla de ARGUS "
                      "para sólidos y líquidos: aditividad de Bragg con los valores atómicos de fase condensada de "
                      "ICRU 37, con el agua en 78 eV como ICRU 90)", None))
        steps.append(("", "1"))
        steps.append(("", _i_text(i_val)))
    else:
        why = "la de un gas" if rule == RULE_GAS else "la del elemento"
        steps.append((f"¿Cambiar la energía media de excitación? 2 (no): la que calcula PENELOPE "
                      f"({_i_text(i_val)} eV) es {why}", "2"))
    steps.append((f"Densidad: {rho:.6g} g/cm³", f"{rho:.6g}"))
    return steps + end


def penhmat_steps(t: Tissue, mat_file: str | None = None, density: float | None = None) -> list[str]:
    """Respuestas que hay que dar a penhmat para generar el .mat de un tejido, en el orden en que pregunta.

    Es el diálogo de PEMATW (penelope.f) y del programa penhmat.f de penhan:
    opción 1 (composición por teclado) u opción 2 (número de la lista de
    PENELOPE), la pregunta del plasmón (Fcb y Wcb), el fichero de salida y el
    «Press enter» final. ``density``: la densidad que se da a penhmat con la
    opción 1 (por defecto, la nominal del tejido).
    """
    return [text for text, _ans in _penhmat_dialog(t, mat_file, density) if text]


def penhmat_input(t: Tissue, mat_file: str | None = None, density: float | None = None) -> str:
    """Lo que se escribe en penhmat (una respuesta por línea), para dárselo por la entrada estándar.

    La energía media de excitación es la de :func:`excitation_energy` (se
    contesta «1» y el valor, o «2» si es la que calcula PENELOPE), como
    propone :func:`penhmat_steps`.
    """
    return "\n".join(ans for _text, ans in _penhmat_dialog(t, mat_file, density) if ans is not None) + "\n"


__all__ = ["Calibration", "ENDF_ISOTOPES", "MNIS", "PRESETS", "SCHNEIDER_CURVE", "SYMBOLS", "Tissue",
           "excitation_energy", "parse_curve_text", "penelope_tissues", "penhmat_composition", "penhmat_input",
           "penhmat_steps", "preset", "read_curve_file", "schneider_tissues", "water_air"]
