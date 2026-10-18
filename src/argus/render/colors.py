"""Paletas de colores para materiales y cuerpos."""

from __future__ import annotations

import colorsys

import numpy as np

# Colores de materiales (1, 2, 3...): paleta de alto contraste y agradable.
MATERIAL_COLORS = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1",
    "#ff9da7", "#9c755f", "#86bcb6", "#d37295", "#8cd17d", "#b6992d", "#499894",
    "#f1ce63", "#a0cbe8", "#ffbe7d", "#d4a6c8", "#fabfd2", "#79706e",
]
VOID_COLOR = "#f5f7fa"  # cuerpos de vacío (MAT=0) definidos por el usuario
ENCLOSURE_COLOR = "#cfd4dc"  # envolvente automática de PENGEOM
OUTSIDE_COLOR = "#2b2d31"  # fuera de la envolvente (no hay geometría)
CT_COLOR = "#c9b458"
EDGE_COLOR = "#1d1f24"
SELECT_COLOR = "#ff7f0e"


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def material_color(mat: int) -> str:
    if mat <= 0:
        return VOID_COLOR
    if mat == 9999:
        return CT_COLOR
    return MATERIAL_COLORS[(mat - 1) % len(MATERIAL_COLORS)]


def body_color(kb: int) -> str:
    h = (kb * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.45, 0.93)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def build_lut(geo, mode: str = "material", material_colors: dict[int, str] | None = None) -> np.ndarray:
    """Tabla (NBODYS+2, 3) uint8: color de cada índice de cuerpo.

    Índice 0 sin uso; NBODYS+1 = fuera de la envolvente.
    """
    n = geo.nbodys
    lut = np.zeros((n + 2, 3), dtype=np.uint8)
    lut[0] = hex_to_rgb(OUTSIDE_COLOR)
    lut[n + 1] = hex_to_rgb(OUTSIDE_COLOR)
    custom = material_colors or {}
    for b in geo.bodies:
        if b.origin == "enclosure":
            c = ENCLOSURE_COLOR
        elif mode == "body":
            c = body_color(b.index) if b.material != 0 else VOID_COLOR
        else:
            c = custom.get(b.material) or material_color(b.material)
        lut[b.index] = hex_to_rgb(c)
    return lut


def material_lut(material_colors: dict[int, str] | None = None, n: int = 100) -> np.ndarray:
    """Color (n, 3) uint8 de cada número de material (0 = vacío): para los vóxeles de un CT."""
    custom = material_colors or {}
    out = np.zeros((n, 3), dtype=np.uint8)
    for m in range(n):
        out[m] = hex_to_rgb(custom.get(m) or material_color(m))
    return out
