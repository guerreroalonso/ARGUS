"""Un mapa de penhmain (dosis, su incertidumbre, un proceso externo…) sobre los cortes de la geometría.

* Lavado de color semitransparente, más opaco cuanto más alto el valor, y
  transparente por debajo de un umbral (así se sigue viendo la geometría).
* Isolíneas en tanto por ciento del valor de referencia (por defecto el máximo
  del mapa), calculadas con los valores interpolados entre los centros de los
  vóxeles, en tinta con un halo claro para que se lean sobre cualquier color.

Las rejillas de caja (GRIDX/Y/Z) están en el sistema de la geometría; las de
cilindro y esfera, alrededor del eje z y del origen; la del TAC, en el sistema
del propio TAC (se coloca con su CtFrame). Sin Qt: trabaja sobre la imagen RGBA
del corte.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..results.data import Grid
from ..results.profiles import _interp_regular, layer

INK = (11, 11, 11)
HALO = (252, 252, 251)


@dataclass
class DoseOverlay:
    grid: Grid
    layer: str | None = None  # None: la magnitud principal; si no, una de ``grid.extra``
    lut: np.ndarray | None = None  # (256, 3) uint8: colores de 0 a la referencia
    alpha: float = 0.6  # opacidad del color en el valor de referencia
    floor: float = 0.02  # por debajo de esta fracción de la referencia, transparente
    levels: tuple[float, ...] = (20.0, 50.0, 80.0, 95.0)  # isolíneas (% de la referencia)
    ref: float | None = None  # valor de referencia (None: el máximo del mapa)
    colorwash: bool = True
    isolines: bool = True
    unit_factor: float = 1.0  # de las unidades del mapa a las que se muestran
    unit: str = ""
    quantity: str = ""
    label: str = ""  # rótulo de la escala: «Dosis (µGy por partícula)»
    title: str = ""
    frame: object | None = None  # CtFrame del TAC (solo para los mapas del TAC)
    _values: np.ndarray | None = field(default=None, repr=False)
    _errors: np.ndarray | None = field(default=None, repr=False)

    @property
    def spatial(self) -> bool:
        return self.grid.geometry in ("box", "cylinder", "sphere", "ct")

    def values(self) -> np.ndarray:
        if self._values is None:
            v, e, qty, _unit = layer(self.grid, self.layer)
            self._values = np.asarray(v, dtype=np.float64)
            self._errors = np.asarray(e, dtype=np.float64)
            if not self.quantity:
                self.quantity = qty
        return self._values

    def errors(self) -> np.ndarray:
        self.values()
        return self._errors

    def reference(self) -> float:
        if self.ref is not None and self.ref > 0:
            return float(self.ref)
        v = self.values()
        m = float(np.nanmax(v)) if v.size and np.isfinite(v).any() else 0.0
        return m if m > 0 else 1.0

    def grid_coords(self, pts: np.ndarray) -> np.ndarray:
        """Coordenadas de los puntos (n, 3) de la geometría en el sistema de la rejilla."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
        g = self.grid.geometry
        if g == "box":
            return pts
        if g == "ct":
            if self.frame is None:
                raise ValueError("Para colocar la dosis del TAC hace falta la geometría con su TAC")
            return self.frame.to_local(pts)
        if g == "cylinder":
            return np.stack([np.hypot(pts[:, 0], pts[:, 1]), pts[:, 2]], axis=1)
        if g == "sphere":
            return np.linalg.norm(pts, axis=1)[:, None]
        raise ValueError("Este mapa no está en el espacio de la geometría")

    def sample(self, pts: np.ndarray, nearest: bool = True) -> np.ndarray:
        """Valor en cada punto (NaN fuera de la rejilla), en las unidades del mapa."""
        v, _inside = _interp_regular(self.grid.edges, self.values(), self.grid_coords(pts), nearest)
        return v

    def sample_with_error(self, point) -> tuple[float, float]:
        c = self.grid_coords(np.asarray(point, dtype=np.float64).reshape(1, 3))
        v, inside = _interp_regular(self.grid.edges, self.values(), c, True)
        e, _ = _interp_regular(self.grid.edges, self.errors(), c, True)
        return (float(v[0]), float(e[0])) if inside[0] else (float("nan"), float("nan"))

    def check_ct(self, ct) -> str | None:
        """Por qué no se puede colocar este mapa del TAC en la geometría (None si se puede)."""
        if self.grid.geometry != "ct":
            return None
        if ct is None or self.frame is None:
            return "La geometría abierta no tiene TAC: no se puede colocar la dosis del TAC."
        nx, ny, nz = self.grid.shape
        if tuple(ct.shape) != (nx, ny, nz):
            return (f"El TAC de la geometría ({ct.shape[0]}×{ct.shape[1]}×{ct.shape[2]} vóxeles) no es el de "
                    f"estos resultados ({nx}×{ny}×{nz}).")
        return None


def segment_profile(ov: DoseOverlay, p0, p1, n: int = 600, axis_name: str = "distancia", x0: float = 0.0):
    """Perfil del mapa a lo largo del segmento p0→p1 de la geometría (valores de cada vóxel, sin interpolar).

    El eje x es ``x0`` + la distancia desde p0 (así, en un corte XZ, un perfil horizontal va en x). Los extremos
    fuera de la rejilla se quitan. Devuelve una :class:`Curves` en las unidades de penhmain.
    """
    from ..results.data import Curves, Series

    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    length = float(np.linalg.norm(b - a))
    if length <= 0:
        raise ValueError("El segmento tiene longitud cero")
    t = np.linspace(0.0, length, max(int(n), 2))
    pts = a[None, :] + (b - a)[None, :] * (t / length)[:, None]
    c = ov.grid_coords(pts)
    v, inside = _interp_regular(ov.grid.edges, ov.values(), c, True)
    e, _ = _interp_regular(ov.grid.edges, ov.errors(), c, True)
    ok = np.nonzero(inside & np.isfinite(v))[0]
    if len(ok) < 2:
        raise ValueError("Esa recta no pasa por el mapa")
    sl = slice(ok[0], ok[-1] + 1)
    _v, _e, qty, unit = layer(ov.grid, ov.layer)
    x = x0 + t[sl]
    return Curves("profile", f"{qty} a lo largo de {axis_name}", x, axis_name, "cm", qty, unit,
                  [Series(qty.lower(), v[sl], e[sl])], histogram=False,
                  meta={"p0": tuple(pts[sl][0]), "p1": tuple(pts[sl][-1]), "source": ov.grid.kind})


def default_lut() -> np.ndarray:
    """Rampa por defecto (de claro a oscuro, en un solo tono), sin matplotlib."""
    stops = np.array([[253, 231, 160], [250, 164, 58], [221, 81, 58], [147, 38, 103], [40, 11, 84]], dtype=float)
    t = np.linspace(0, 1, 256)
    x = np.linspace(0, 1, len(stops))
    return np.stack([np.interp(t, x, stops[:, c]) for c in range(3)], axis=1).astype(np.uint8)


def _dilate(m: np.ndarray) -> np.ndarray:
    d = m.copy()
    d[1:, :] |= m[:-1, :]
    d[:-1, :] |= m[1:, :]
    d[:, 1:] |= m[:, :-1]
    d[:, :-1] |= m[:, 1:]
    return d


def isoline_masks(values: np.ndarray, levels: list[float]) -> list[np.ndarray]:
    """Para cada nivel, los píxeles donde el valor cruza el nivel respecto del vecino derecho o inferior (sin
    líneas en el borde de la rejilla: allí no hay isolínea, se acaba el mapa)."""
    fin = np.isfinite(values)
    out = []
    for lv in levels:
        above = fin & (np.nan_to_num(values, nan=-np.inf) >= lv)
        e = np.zeros(values.shape, dtype=bool)
        e[:, :-1] |= (above[:, :-1] != above[:, 1:]) & fin[:, :-1] & fin[:, 1:]
        e[:-1, :] |= (above[:-1, :] != above[1:, :]) & fin[:-1, :] & fin[1:, :]
        out.append(e & above)  # la línea, del lado de dentro
    return out


def apply_overlay(rgba: np.ndarray, pts: np.ndarray, ov: DoseOverlay, mask: np.ndarray | None = None,
                  line_scale: int = 1) -> tuple[np.ndarray, list[tuple[float, float, float]]]:
    """Pinta el mapa sobre la imagen (H, W, 4) del corte cuyos centros de píxel son ``pts`` (H·W, 3).

    ``mask``: píxeles donde puede haber mapa (p. ej. los del cuerpo del TAC). Devuelve la imagen nueva y dónde
    poner la etiqueta de cada isolínea (nivel en %, x, y en píxeles de la imagen).
    """
    h, w = rgba.shape[:2]
    ref = ov.reference()
    out = rgba.astype(np.float32)
    labels: list[tuple[float, float, float]] = []
    lut = ov.lut if ov.lut is not None else default_lut()
    if ov.colorwash:
        v = ov.sample(pts, nearest=True).reshape(h, w)
        if mask is not None:
            v = np.where(mask, v, np.nan)
        t = np.clip(np.nan_to_num(v, nan=0.0) / ref, 0.0, 1.0)
        show = np.isfinite(v) & (v >= ov.floor * ref)
        if show.any():
            col = lut[(t[show] * (len(lut) - 1)).astype(np.int64)].astype(np.float32)
            a = (ov.alpha * (0.35 + 0.65 * t[show]))[:, None]
            out[show, :3] = out[show, :3] * (1.0 - a) + col * a
    if ov.isolines and ov.levels:
        vi = ov.sample(pts, nearest=False).reshape(h, w)
        if mask is not None:
            vi = np.where(mask, vi, np.nan)
        levels = sorted(float(x) for x in ov.levels if 0 < x)
        masks = isoline_masks(vi, [ref * lv / 100.0 for lv in levels])
        lines = np.zeros((h, w), dtype=bool)
        thick = []
        for lv, m in zip(levels, masks):
            m2 = m
            for _ in range(max(line_scale, 1) - 1 + (1 if lv >= 80.0 else 0)):
                m2 = _dilate(m2)
            thick.append(m2)
            lines |= m2
            if m.any():
                ys, xs = np.nonzero(m)
                k = int(np.argmax(xs - 1e-6 * ys))
                labels.append((lv, float(xs[k]), float(ys[k])))
        if lines.any():
            halo = _dilate(lines) & ~lines
            out[halo, :3] = out[halo, :3] * 0.35 + np.array(HALO, dtype=np.float32) * 0.65
            out[lines, :3] = np.array(INK, dtype=np.float32)
    res = np.empty_like(rgba)
    res[..., :3] = np.clip(out[..., :3], 0, 255).astype(np.uint8)
    res[..., 3] = rgba[..., 3]
    return res, labels


__all__ = ["DoseOverlay", "apply_overlay", "default_lut", "isoline_masks", "segment_profile"]
