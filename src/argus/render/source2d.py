"""Geometría de la fuente para dibujarla sobre un corte (sin Qt).

Semántica de penhmain (PMRDR/SOURCE):

* dirección (θ, φ) → (sinθ·cosφ, sinθ·sinφ, cosθ);
* SCONE θ φ α: direcciones uniformes dentro del cono de semiapertura α
  alrededor del eje (θ, φ); α = 0 es un haz lápiz y α = 180 es isótropo;
* SRECTA θL θU φL φU: cosθ uniforme entre cosθL y cosθU y φ uniforme
  entre φL y φU;
* sin SCONE ni SRECTA: haz lápiz hacia +Z;
* SKPAR 0 (fuente radiactiva): isótropa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..penhan.inputfile import SourceDef
from .slice2d import SlicePlane


def direction(theta_deg: float, phi_deg: float) -> np.ndarray:
    if not (math.isfinite(theta_deg) and math.isfinite(phi_deg)):  # Inf o NaN en el .in
        return np.full(3, np.nan)
    th = math.radians(theta_deg)
    ph = math.radians(phi_deg)
    return np.array([math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)])


def angles_of(d: np.ndarray) -> tuple[float, float]:
    """(θ, φ) en grados de un vector (φ en [0, 360))."""
    d = np.asarray(d, dtype=float)
    d = d / np.linalg.norm(d)
    th = math.degrees(math.acos(max(-1.0, min(1.0, float(d[2])))))
    ph = math.degrees(math.atan2(float(d[1]), float(d[0]))) % 360.0
    if th < 1e-9 or th > 180.0 - 1e-9:
        ph = 0.0
    return th, ph


def _basis(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(d, a)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(d, e1)
    return e1, e2


@dataclass
class EmissionShape:
    kind: str  # 'pencil', 'cone', 'rect', 'isotropic' o 'invalid' (ángulos con Inf o NaN: no se dibuja)
    axis: np.ndarray  # dirección central (unitaria)
    samples: np.ndarray  # (N,3) direcciones del borde (o de la ventana) para dibujar
    solid_angle_fraction: float  # fracción de 4π


def emission(src: SourceDef) -> EmissionShape:
    kind, par = src.angular()
    if kind == "isotropic":
        return EmissionShape("isotropic", np.array([0.0, 0.0, 1.0]), np.zeros((0, 3)), 1.0)
    if not all(math.isfinite(float(v)) for v in par):
        return EmissionShape("invalid", np.full(3, np.nan), np.zeros((0, 3)), 0.0)
    if kind == "cone":
        th, ph, al = par
        axis = direction(th, ph)
        if al <= 1e-9:
            return EmissionShape("pencil", axis, axis[None, :], 0.0)
        if al >= 180.0 - 1e-9:
            return EmissionShape("isotropic", axis, np.zeros((0, 3)), 1.0)
        e1, e2 = _basis(axis)
        a = math.radians(al)
        psi = np.linspace(0.0, 2.0 * math.pi, 73)[:-1]
        samples = (math.cos(a) * axis[None, :] + math.sin(a) * (np.cos(psi)[:, None] * e1[None, :]
                                                                 + np.sin(psi)[:, None] * e2[None, :]))
        return EmissionShape("cone", axis, samples, 0.5 * (1.0 - math.cos(a)))
    thl, thu, phl, phu = par
    th = np.radians(np.linspace(thl, thu, 13))
    ph = np.radians(np.linspace(phl, phu, 25))
    border = []
    for t in (th[0], th[-1]):
        for p in ph:
            border.append((t, p))
    for p in (ph[0], ph[-1]):
        for t in th:
            border.append((t, p))
    arr = np.array(border)
    samples = np.stack([np.sin(arr[:, 0]) * np.cos(arr[:, 1]), np.sin(arr[:, 0]) * np.sin(arr[:, 1]),
                        np.cos(arr[:, 0])], axis=1)
    tc = math.radians(0.5 * (thl + thu))
    pc = math.radians(0.5 * (phl + phu))
    axis = np.array([math.sin(tc) * math.cos(pc), math.sin(tc) * math.sin(pc), math.cos(tc)])
    frac = abs(math.cos(math.radians(thl)) - math.cos(math.radians(thu))) * abs(phu - phl) / 360.0 / 2.0
    if thl == thu and phl == phu:
        return EmissionShape("pencil", axis, axis[None, :], 0.0)
    if frac >= 1.0 - 1e-9:
        return EmissionShape("isotropic", axis, np.zeros((0, 3)), 1.0)
    return EmissionShape("rect", axis, samples, frac)


def project(plane: SlicePlane, d: np.ndarray) -> np.ndarray:
    """Componentes (u, v, n) de uno o varios vectores en la base del plano."""
    d = np.atleast_2d(d)
    return np.stack([d @ plane.u, d @ plane.v, d @ plane.normal], axis=1)


def box_section(plane: SlicePlane, center, size) -> tuple[float, float, float, float] | None:
    """Rectángulo (a0, b0, a1, b1) del corte de la caja SBOX con un plano estándar."""
    k = plane.depth_axis
    if k is None:
        return None
    c = np.asarray(center, dtype=float)
    s = np.abs(np.asarray(size, dtype=float))
    if abs(plane.depth - c[k]) > 0.5 * s[k] + 1e-12:
        return None
    iu = int(np.argmax(np.abs(plane.u)))
    iv = int(np.argmax(np.abs(plane.v)))
    a0, b0 = plane.coords(c)
    return (a0 - 0.5 * s[iu], b0 - 0.5 * s[iv], a0 + 0.5 * s[iu], b0 + 0.5 * s[iv])


def box_projection(plane: SlicePlane, center, size) -> tuple[float, float, float, float] | None:
    """Proyección de la caja sobre el plano (para dibujarla discontinua fuera del corte)."""
    k = plane.depth_axis
    if k is None:
        return None
    c = np.asarray(center, dtype=float)
    s = np.abs(np.asarray(size, dtype=float))
    iu = int(np.argmax(np.abs(plane.u)))
    iv = int(np.argmax(np.abs(plane.v)))
    a0, b0 = plane.coords(c)
    return (a0 - 0.5 * s[iu], b0 - 0.5 * s[iv], a0 + 0.5 * s[iu], b0 + 0.5 * s[iv])


def source_mask(points: np.ndarray, body: np.ndarray, src: SourceDef, active: set[int]) -> np.ndarray:
    """Píxeles donde penhmain puede generar partículas (caja SBOX ∩ cuerpos SBODY)."""
    c = np.asarray(src.position, dtype=float)
    s = np.abs(np.asarray(src.box, dtype=float))
    inside = np.all(np.abs(points - c[None, :]) <= 0.5 * s[None, :] + 1e-12, axis=1)
    inside = inside.reshape(body.shape)
    if active:
        inside &= np.isin(body, list(active))
    return inside


@dataclass
class DoseRegion:
    kind: str  # 'box', 'cylinder' (eje Z) o 'sphere' (centrada en el origen)
    lo: np.ndarray | None = None  # caja
    hi: np.ndarray | None = None
    radius: float = 0.0
    zlo: float = 0.0
    zhi: float = 0.0


def dose_region(grids: dict | None) -> DoseRegion | None:
    """Volumen de la rejilla de dosis como lo define penhmain (PMRDR).

    GRIDX+GRIDY+GRIDZ: caja; GRIDZ+GRIDR: cilindro de eje Z; solo GRIDR:
    esfera centrada en el origen. Una línea con 0 intervalos no cuenta.
    """
    if not grids:
        return None

    def get(key):
        g = grids.get(key)
        if g is None or int(g[-1]) <= 0:
            return None
        return g

    gx, gy, gz, gr = get("GRIDX"), get("GRIDY"), get("GRIDZ"), get("GRIDR")
    if gx and gy and gz:
        lo = np.array([min(gx[0], gx[1]), min(gy[0], gy[1]), min(gz[0], gz[1])], dtype=float)
        hi = np.array([max(gx[0], gx[1]), max(gy[0], gy[1]), max(gz[0], gz[1])], dtype=float)
        return DoseRegion("box", lo, hi)
    if gx or gy:
        return None
    if gr:
        if gz:
            return DoseRegion("cylinder", radius=float(gr[0]), zlo=float(min(gz[0], gz[1])),
                              zhi=float(max(gz[0], gz[1])))
        return DoseRegion("sphere", radius=float(gr[0]))
    return None


def box_corners(center, size) -> np.ndarray:
    c = np.asarray(center, dtype=float)
    h = 0.5 * np.abs(np.asarray(size, dtype=float))
    return np.array([c + h * np.array([sx, sy, sz]) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])


def source_region(res, src: SourceDef, active: set[int]):
    """Máscara de emisión solo en los píxeles que cubre la caja SBOX.

    Devuelve ``(filas, columnas, máscara)`` (dos ``slice`` y una matriz
    booleana) o ``None`` si el corte no atraviesa la caja. Vale para
    cualquier orientación del plano. Reproduce el muestreo de penhmain
    (SOURCE): punto uniforme en la caja y, si hay cuerpos SBODY, se acepta
    solo si LOCATE lo sitúa en uno de ellos.
    """
    view = res.view
    plane = view.plane
    c = np.asarray(src.position, dtype=float)
    s = np.abs(np.asarray(src.box, dtype=float))
    if not (np.all(np.isfinite(c)) and np.all(np.isfinite(s))):
        return None  # Inf o NaN en el .in (se avisa en los mensajes)
    w, h = res.width, res.height
    with np.errstate(over="ignore", invalid="ignore"):  # cajas enormes: sus esquinas pueden no caber en un float
        corners = box_corners(c, s)
        dist = (corners - plane.origin[None, :]) @ plane.normal
        if dist.min() > 1e-12 or dist.max() < -1e-12:
            return None
        rel = corners - plane.origin[None, :]
        px = 0.5 * w + (rel @ plane.u - view.center[0]) / view.scale
        py = 0.5 * h - (rel @ plane.v - view.center[1]) / view.scale
    # Solo acotan los píxeles que se miran; lo que está fuera de la imagen (o no es un número) se recorta a ella y
    # la prueba de cada píxel de abajo decide.
    px = np.array([-2.0, w + 2.0]) if np.isnan(px).any() else np.clip(px, -2.0, w + 2.0)
    py = np.array([-2.0, h + 2.0]) if np.isnan(py).any() else np.clip(py, -2.0, h + 2.0)
    i0 = max(0, int(np.floor(px.min())) - 1)
    i1 = min(w, int(np.ceil(px.max())) + 1)
    j0 = max(0, int(np.floor(py.min())) - 1)
    j1 = min(h, int(np.ceil(py.max())) + 1)
    if i0 >= i1 or j0 >= j1:
        return None
    aa = view.center[0] + (np.arange(i0, i1) + 0.5 - 0.5 * w) * view.scale
    bb = view.center[1] + (0.5 * h - (np.arange(j0, j1) + 0.5)) * view.scale
    ga, gb = np.meshgrid(aa, bb)
    pts = plane.origin + ga[..., None] * plane.u + gb[..., None] * plane.v
    inside = np.all(np.abs(pts - c) <= 0.5 * s, axis=-1)
    if active:
        inside &= np.isin(res.body[j0:j1, i0:i1], list(active))
    return slice(j0, j1), slice(i0, i1), inside
