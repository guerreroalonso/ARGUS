"""Escena 3D sin Qt: cámara orbital, cortes y datos de la geometría para el trazado de rayos.

La GPU trabaja en ``float32``. Para no perder precisión lejos del origen de
coordenadas (el ejemplo ``tball`` de PENGEOM pone una bola de 1 cm a
5·10⁶ cm), las cuádricas se trasladan en ``float64`` al punto que mira la
cámara (**origen flotante**) antes de subirlas, y cada superficie se escala
por su coeficiente mayor (no cambia ni la superficie ni el signo de F).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .colors import ENCLOSURE_COLOR, build_lut, hex_to_rgb

TEX_WIDTH = 4096  # anchura de las texturas de datos (índice i -> (i % 4096, i // 4096))

# Estado de visibilidad de cada material en 3D (canal alfa de la textura de colores).
HIDDEN, OPAQUE, TRANSLUCENT = 0, 255, 90
VISIBILITY_NAMES = {OPAQUE: "opaco", TRANSLUCENT: "translúcido", HIDDEN: "oculto"}

BACKGROUND = ((0.12, 0.13, 0.15), (0.26, 0.28, 0.32))  # fondo: abajo, arriba


# ---------------------------------------------------------------------------
# Cámara
# ---------------------------------------------------------------------------

@dataclass
class Camera:
    """Cámara que orbita alrededor de ``target`` (Z hacia arriba).

    ``yaw``: azimut (grados, desde +X hacia +Y) de la posición de la cámara;
    ``pitch``: elevación (grados). Con yaw = -90 y pitch = 0 se mira hacia +Y
    con x a la derecha y z hacia arriba, como el corte XZ.

    En proyección ortográfica ``distance`` solo fija el encuadre; los rayos
    salen desde ``max(distance, ortho_back)`` detrás del objetivo para que
    nada de la escena quede detrás de ellos.
    """

    target: np.ndarray = field(default_factory=lambda: np.zeros(3))
    distance: float = 100.0
    yaw: float = -60.0
    pitch: float = 25.0
    fov: float = 30.0  # campo vertical (grados)
    ortho: bool = False
    ortho_back: float = 0.0

    def copy(self) -> Camera:
        return Camera(np.array(self.target, dtype=float), self.distance, self.yaw, self.pitch, self.fov, self.ortho,
                      self.ortho_back)

    def state(self) -> tuple:
        """Estado comparable (para saber si hay que volver a dibujar)."""
        return (tuple(float(v) for v in self.target), self.distance, self.yaw, self.pitch, self.fov, self.ortho,
                self.ortho_back)

    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(derecha, arriba, adelante), unitarios."""
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        p = max(-89.999, min(89.999, self.pitch))
        cp, sp = math.cos(math.radians(p)), math.sin(math.radians(p))
        fwd = -np.array([cp * cy, cp * sy, sp])
        right = np.cross(fwd, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        return right, up, fwd

    @property
    def position(self) -> np.ndarray:
        return np.asarray(self.target, dtype=float) - self.basis()[2] * self.distance

    def ray_origin(self) -> np.ndarray:
        """Origen común de los rayos (perspectiva) o centro del plano de salida (ortográfica)."""
        if self.ortho:
            back = max(self.distance, self.ortho_back)
            return np.asarray(self.target, dtype=float) - self.basis()[2] * back
        return self.position

    def tan_half(self) -> float:
        return math.tan(math.radians(0.5 * self.fov))

    def half_height(self) -> float:
        """Semialtura del encuadre en el plano del objetivo (proyección ortográfica)."""
        return self.distance * self.tan_half()

    def rays(self, px: np.ndarray, py: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        """Rayos (origen, dirección unitaria) por los píxeles (px, py) (origen arriba a la izquierda)."""
        right, up, fwd = self.basis()
        nx = 2.0 * (np.asarray(px, dtype=float) + 0.5) / width - 1.0
        ny = 1.0 - 2.0 * (np.asarray(py, dtype=float) + 0.5) / height
        aspect = width / height
        th = self.tan_half()
        base = self.ray_origin()
        if self.ortho:
            hh = self.half_height()
            o = base[None, :] + (nx * hh * aspect)[..., None] * right + (ny * hh)[..., None] * up
            d = np.broadcast_to(fwd, o.shape).copy()
        else:
            d = fwd + (nx * th * aspect)[..., None] * right + (ny * th)[..., None] * up
            d = d / np.linalg.norm(d, axis=-1)[..., None]
            o = np.broadcast_to(base, d.shape).copy()
        return o, d

    def ray(self, px: float, py: float, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        o, d = self.rays(np.array([px]), np.array([py]), width, height)
        return o[0], d[0]

    def rays_to(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Rayos desde la cámara hasta unos puntos: (orígenes, direcciones, distancias)."""
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        if self.ortho:
            _, _, fwd = self.basis()
            back = max(self.distance, self.ortho_back)
            rel = pts - np.asarray(self.target, dtype=float)
            depth = rel @ fwd + back
            o = pts - depth[:, None] * fwd
            d = np.broadcast_to(fwd, pts.shape).copy()
            return o, d, depth
        o = np.broadcast_to(self.position, pts.shape).copy()
        v = pts - o
        dist = np.linalg.norm(v, axis=1)
        d = v / np.where(dist > 0, dist, 1.0)[:, None]
        return o, d, dist

    def project(self, pts: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        """Píxeles (x, y) de puntos 3D y su profundidad a lo largo de la vista (<= 0: detrás)."""
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        right, up, fwd = self.basis()
        rel = pts - self.position[None, :]
        z = rel @ fwd
        x = rel @ right
        y = rel @ up
        aspect = width / height
        if self.ortho:
            hh = self.half_height()
            nx = x / (hh * aspect)
            ny = y / hh
            z = np.ones_like(z)  # en ortográfica todo punto se proyecta (no hay «detrás de la cámara»)
        else:
            th = self.tan_half()
            zz = np.where(np.abs(z) < 1e-300, 1e-300, z)
            nx = x / (zz * th * aspect)
            ny = y / (zz * th)
        px = (nx + 1.0) * 0.5 * width
        py = (1.0 - ny) * 0.5 * height
        return np.stack([px, py], axis=-1), z

    def fit(self, lo: np.ndarray, hi: np.ndarray, aspect: float = 1.0) -> None:
        """Encuadra la caja [lo, hi] manteniendo la orientación."""
        lo = np.asarray(lo, dtype=float)
        hi = np.asarray(hi, dtype=float)
        self.target = 0.5 * (lo + hi)
        radius = 0.5 * float(np.linalg.norm(hi - lo)) or 1.0
        th = self.tan_half()
        fit_tan = th * min(1.0, aspect)
        self.distance = radius / math.sin(math.atan(fit_tan)) * 1.05

    def orbit(self, dyaw: float, dpitch: float) -> None:
        self.yaw = (self.yaw + dyaw + 180.0) % 360.0 - 180.0
        self.pitch = max(-89.9, min(89.9, self.pitch + dpitch))

    def pan(self, dx_px: float, dy_px: float, height: int) -> None:
        """Desplaza el objetivo como si se arrastrara la imagen ``dx, dy`` píxeles."""
        right, up, _ = self.basis()
        per_px = 2.0 * self.half_height() / max(height, 1)
        self.target = np.asarray(self.target, dtype=float) - right * dx_px * per_px + up * dy_px * per_px

    def zoom(self, factor: float) -> None:
        self.distance = max(1e-9, self.distance * factor)


def ortho_back(camera: Camera, lo, hi) -> float:
    """Distancia detrás del objetivo desde la que salen los rayos en proyección ortográfica.

    Lo justo para que la caja [lo, hi] (lo que se ve) quede delante: cuanto
    más cerca del objetivo empiezan los rayos, más precisión en ``float32``.
    """
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    _, _, fwd = camera.basis()
    front = float(np.max(-(corners - np.asarray(camera.target, dtype=float)) @ fwd))
    return max(front, 0.0) + 0.02 * float(np.linalg.norm(hi - lo)) + 1e-9


def key_light(camera: Camera) -> np.ndarray:
    """Dirección hacia la luz principal: arriba a la izquierda y detrás del observador."""
    right, up, fwd = camera.basis()
    v = -fwd + 0.75 * up - 0.45 * right
    return v / np.linalg.norm(v)


VIEWS = {  # nombre: (yaw, pitch) de la posición de la cámara
    "+X": (0.0, 0.0), "-X": (180.0, 0.0),
    "+Y": (90.0, 0.0), "-Y": (-90.0, 0.0),
    "+Z": (-90.0, 89.9), "-Z": (-90.0, -89.9),
    "iso": (-60.0, 25.0),
}
VIEW_TITLES = {
    "+X": "desde +X", "-X": "desde −X", "+Y": "desde +Y", "-Y": "desde −Y (como el corte XZ)",
    "+Z": "desde +Z (desde arriba)", "-Z": "desde −Z (desde abajo)", "iso": "isométrica",
}


# ---------------------------------------------------------------------------
# Planos de corte
# ---------------------------------------------------------------------------

CLIP_MODES = {
    "none": "Sin corte",
    "x": "Ocultar x > x₀",
    "y": "Ocultar y > y₀",
    "z": "Ocultar z > z₀",
    "corner": "Ocultar una esquina",
    "slice": "Por el corte 2D",
}


def clip_planes(mode: str, point, invert: bool = False, slice_normal=None, slice_origin=None) -> list[tuple]:
    """Planos (nx, ny, nz, d) de un corte: se oculta la intersección de los semiespacios n·r > d.

    ``point``: punto por el que pasan los planos (x₀, y₀, z₀). ``invert``
    oculta el otro lado. Con ``slice`` se usa el plano del corte 2D
    (``slice_normal``, ``slice_origin``) y se oculta lo que queda delante de él.
    """
    p = np.asarray(point, dtype=float)
    s = -1.0 if invert else 1.0
    planes = []
    if mode in ("x", "y", "z"):
        k = "xyz".index(mode)
        n = np.zeros(3)
        n[k] = s
        planes.append(n)
    elif mode == "corner":
        for k in range(3):
            n = np.zeros(3)
            n[k] = s
            planes.append(n)
    elif mode == "slice" and slice_normal is not None:
        n = np.asarray(slice_normal, dtype=float)
        n = n / np.linalg.norm(n)
        planes.append(s * n)
        p = np.asarray(slice_origin if slice_origin is not None else point, dtype=float)
    return [(float(n[0]), float(n[1]), float(n[2]), float(n @ p)) for n in planes]


def describe_clip(planes: list[tuple]) -> str:
    """Texto breve de lo que oculta un corte."""
    if not planes:
        return ""
    parts = []
    for nx, ny, nz, d in planes:
        n = np.array([nx, ny, nz])
        k = int(np.argmax(np.abs(n)))
        if abs(abs(n[k]) - 1.0) < 1e-12 and np.count_nonzero(np.abs(n) > 1e-12) == 1:
            op = ">" if n[k] > 0 else "<"
            parts.append(f"{'xyz'[k]} {op} {d * np.sign(n[k]):.6g}")
        else:
            parts.append(f"{nx:.3g}x {ny:+.3g}y {nz:+.3g}z > {d:.6g}")
    return "oculto: " + " y ".join(parts) + " cm"


# ---------------------------------------------------------------------------
# Datos de la geometría para la GPU
# ---------------------------------------------------------------------------

def translated_coefficients(coef: np.ndarray, origin) -> np.ndarray:
    """Coeficientes de F(r' + o) como función de r' (en float64).

    ``origin``: un punto (3,) para todas las superficies o uno por superficie (n, 3).
    """
    c = np.asarray(coef, dtype=float)
    o = np.asarray(origin, dtype=float)
    if o.ndim == 1:
        o = np.broadcast_to(o, (c.shape[0], 3))
    ox, oy, oz = o[:, 0], o[:, 1], o[:, 2]
    axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = (c[:, i] for i in range(10))
    out = c.copy()
    out[:, 6] = ax + 2.0 * axx * ox + axy * oy + axz * oz
    out[:, 7] = ay + axy * ox + 2.0 * ayy * oy + ayz * oz
    out[:, 8] = az + axz * ox + ayz * oy + 2.0 * azz * oz
    out[:, 9] = (ox * (axx * ox + axy * oy + axz * oz + ax) + oy * (ayy * oy + ayz * oz + ay)
                 + oz * (azz * oz + az) + a0)
    return out


def grid(values: np.ndarray, channels: int, dtype) -> np.ndarray:
    """Coloca ``values`` (N, channels) en una textura (alto, TEX_WIDTH, channels)."""
    values = np.asarray(values, dtype=dtype).reshape(-1, channels)
    n = max(1, values.shape[0])
    rows = (n + TEX_WIDTH - 1) // TEX_WIDTH
    out = np.zeros((rows, TEX_WIDTH, channels), dtype=dtype)
    out.reshape(-1, channels)[:values.shape[0]] = values
    return out


def gpu_tree_tables(t: dict, nbodys: int) -> dict[str, np.ndarray]:
    """Árbol de cuerpos para el *shader* (a partir de ``locate_fast.build_tables``).

    body: 2 texeles RGBA32I por cuerpo, (cons_ptr, cons_end, eval_ptr,
    eval_end) y (dght_ptr, dght_end, ndght, material); el cuerpo NBODYS+1
    (fuera de la envolvente) usa las superficies de la raíz. cons: ks·4 + kf;
    eval: ks; dght: kb·2 + (1 si LOCATE puede entrar en esa hija).
    """
    nb = nbodys
    body = np.zeros((nb + 2, 8), dtype=np.int64)
    for kb in range(1, nb + 1):
        body[kb] = (t["cons_ptr"][kb], t["cons_ptr"][kb + 1], t["eval_ptr"][kb], t["eval_ptr"][kb + 1],
                    t["dght_ptr"][kb], t["dght_ptr"][kb + 1], t["ndght"][kb], t["mater"][kb])
    body[nb + 1, 2:4] = body[nb, 2:4]
    cons = t["cons_ks"] * 4 + t["cons_kf"]
    dght = t["dght"] * 2 + t["dok"].astype(np.int64)
    return {
        "body": grid(body.reshape(-1, 4), 4, np.int32),
        "cons": grid(cons, 1, np.int32),
        "eval": grid(t["eval_ks"], 1, np.int32),
        "dght": grid(dght, 1, np.int32),
    }


def surface_centers(coef: np.ndarray, kplane: np.ndarray) -> np.ndarray:
    """Punto de referencia de cada superficie (float64) para evaluarla bien en ``float32``.

    Centro de la cuádrica en las direcciones en que no es degenerada (0 en
    las demás) y, en los planos, el pie de la perpendicular desde el origen.
    Evaluar F en r - c con los coeficientes centrados evita restar números
    grandes: una lámina delgada definida como par de planos (z² = a²) lejos
    del origen flotante sería ruido en ``float32`` con los coeficientes sin
    centrar.
    """
    coef = np.asarray(coef, dtype=float)
    out = np.zeros((coef.shape[0], 3))
    for k, q in enumerate(coef):
        axx, axy, axz, ayy, ayz, azz, ax, ay, az, a0 = q
        lin = np.array([ax, ay, az])
        m = np.array([[axx, 0.5 * axy, 0.5 * axz], [0.5 * axy, ayy, 0.5 * ayz], [0.5 * axz, 0.5 * ayz, azz]])
        lmax = float(np.max(np.abs(m)))
        if kplane[k] or lmax == 0.0:
            nn = float(lin @ lin)
            out[k] = -a0 * lin / nn if nn > 0 else 0.0
            continue
        lam, vec = np.linalg.eigh(m)
        rhs = vec.T @ (-0.5 * lin)
        comp = np.zeros(3)
        ok = np.abs(lam) > 1e-9 * float(np.max(np.abs(lam)))
        comp[ok] = rhs[ok] / lam[ok]
        out[k] = vec @ comp
    return out


def gpu_coefficients(t: dict, origin, centers: np.ndarray | None = None) -> np.ndarray:
    """Superficies centradas y normalizadas: 4 texeles RGBA32F por superficie.

    (axx, axy, axz, ayy), (ayz, azz, ax, ay), (az, a0, plano, 0) de F(c + q)
    como función de q, y (c - origen flotante, 0); la superficie 0 no existe.
    En el *shader*, q = p - (c - origen) con p relativo al origen flotante.
    """
    raw = t["coef"][1:]
    if centers is None:
        centers = surface_centers(raw, t["kplane"][1:])
    coef = translated_coefficients(raw, centers)
    mx = np.max(np.abs(coef), axis=1) if coef.size else np.zeros(0)
    mx[mx == 0] = 1.0
    coef = coef / mx[:, None]
    ns = coef.shape[0]
    tex = np.zeros((ns + 1, 16), dtype=np.float64)
    tex[1:, 0:10] = coef
    tex[1:, 10] = t["kplane"][1:] != 0
    tex[1:, 12:15] = centers - np.asarray(origin, dtype=float)[None, :]
    return grid(tex.reshape(-1, 4), 4, np.float32)


def body_rgba(geo, mode: str = "material", material_colors: dict[int, str] | None = None,
              visibility: dict[int, int] | None = None, hidden_bodies: set[int] | None = None,
              tint: dict[int, tuple[int, int, int]] | None = None, tint_amount: float = 0.3) -> np.ndarray:
    """Color RGBA8 de cada cuerpo (NBODYS+2, 4); el alfa es la visibilidad en 3D.

    ``visibility``: estado por número de material (OPAQUE, TRANSLUCENT o
    HIDDEN; por defecto opaco). Los cuerpos sin material, los módulos vacíos
    y la envolvente no se ven. ``tint``: color que se mezcla con algunos
    cuerpos (p. ej. los cuerpos activos de la fuente).
    """
    lut = build_lut(geo, mode, material_colors)
    n = geo.nbodys
    rgba = np.zeros((n + 2, 4), dtype=np.uint8)
    rgba[:, :3] = lut
    vis = visibility or {}
    hidden = hidden_bodies or set()
    for b in geo.bodies:
        a = HIDDEN
        if b.material > 0 and b.origin != "enclosure" and b.index not in hidden:
            a = vis.get(b.material, OPAQUE)
        rgba[b.index, 3] = a
        if tint and b.index in tint:
            c = np.array(tint[b.index], dtype=float)
            rgba[b.index, :3] = np.clip((1 - tint_amount) * rgba[b.index, :3] + tint_amount * c, 0, 255)
    rgba[n + 1] = (*hex_to_rgb(ENCLOSURE_COLOR), 0)
    return rgba


def visibility_array(geo, visibility: dict[int, int] | None = None, hidden_bodies: set[int] | None = None
                     ) -> np.ndarray:
    """Visibilidad por cuerpo para el trazador de la CPU (1 = se ve)."""
    vis = np.zeros(geo.nbodys + 2, dtype=np.int64)
    v = visibility or {}
    hidden = hidden_bodies or set()
    for b in geo.bodies:
        if b.material > 0 and b.origin != "enclosure" and b.index not in hidden:
            vis[b.index] = 1 if v.get(b.material, OPAQUE) != HIDDEN else 0
    return vis


def visible_bounds(tracer, lo, hi, vis: np.ndarray | None = None, n: int = 48
                   ) -> tuple[np.ndarray, np.ndarray] | None:
    """Caja de lo que se ve (cuerpos con material), mirando la caja [lo, hi] desde los seis ejes.

    Las esferas de vacío que rodean a muchas geometrías no cuentan: la vista
    3D se encuadra en lo que tiene material.

    En cada eje, los rayos paralelos a él dan la superficie exacta (una cara plana, justo donde está); los demás
    solo saben que hay material en su línea, a medio paso de rayo de donde de verdad acaba. Por eso la caja va de
    la superficie que tocan los rayos de ese eje y solo se alarga medio paso por un lado si los otros rayos ven
    material más allá (algo fino que los de ese eje no han tocado): así la rejilla de dosis por defecto empieza en
    la superficie y no un 1 % fuera.
    """
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    c = 0.5 * (lo + hi)
    span = np.maximum(hi - lo, 1e-9)
    back = float(np.linalg.norm(span)) + 1.0
    u = (np.arange(n) + 0.5) / n
    ga, gb = np.meshgrid(u, u)
    ga, gb = ga.ravel(), gb.ravel()
    exact_lo = np.full(3, np.inf)  # por los rayos paralelos a cada eje
    exact_hi = np.full(3, -np.inf)
    other_lo = np.full(3, np.inf)  # por los demás (con el error de medio paso)
    other_hi = np.full(3, -np.inf)
    for k in range(3):
        i, j = [a for a in range(3) if a != k]
        for s in (1.0, -1.0):
            o = np.zeros((ga.size, 3))
            o[:, i] = lo[i] + ga * span[i]
            o[:, j] = lo[j] + gb * span[j]
            o[:, k] = c[k] - s * back
            d = np.zeros_like(o)
            d[:, k] = s
            t, kb, _ = tracer.render(o, d, visible=vis)
            hit = kb > 0
            if not hit.any():
                continue
            p = o[hit] + t[hit, None] * d[hit]
            exact_lo[k] = min(exact_lo[k], float(p[:, k].min()))
            exact_hi[k] = max(exact_hi[k], float(p[:, k].max()))
            for a in (i, j):
                other_lo[a] = min(other_lo[a], float(p[:, a].min()))
                other_hi[a] = max(other_hi[a], float(p[:, a].max()))
    if not np.all(np.isfinite(exact_lo) | np.isfinite(other_lo)):
        return None
    pad = 0.5 * span / n  # medio paso de los rayos
    # Un poco más allá (una esfera entre dos rayos) no es «algo fino»: se queda con lo que han visto unos u otros.
    vlo = np.where(other_lo < exact_lo - 0.5 * pad, other_lo - pad, np.minimum(exact_lo, other_lo))
    vhi = np.where(other_hi > exact_hi + 0.5 * pad, other_hi + pad, np.maximum(exact_hi, other_hi))
    return vlo, vhi


def matter_bounds(tracer, lo, hi, vis: np.ndarray | None = None, passes: int = 4
                  ) -> tuple[np.ndarray, np.ndarray] | None:
    """:func:`visible_bounds` afinada: se repite en la caja que da la vuelta anterior (con los rayos más juntos)
    mientras encoge. En una escena con una esfera de vacío grande (5000 cm alrededor de un maniquí de 50 cm), la
    primera vuelta tiene rayos cada 200 cm y su margen es de 100 cm."""
    box = visible_bounds(tracer, lo, hi, vis)
    if box is not None:  # dentro de la caja que se da (los rayos pueden tocar superficies de fuera)
        box = (np.maximum(box[0], np.asarray(lo, dtype=float)), np.minimum(box[1], np.asarray(hi, dtype=float)))
    for _ in range(passes - 1):
        if box is None:
            break
        nb = visible_bounds(tracer, box[0], box[1], vis)
        if nb is None:
            break
        shrunk = np.any(nb[1] - nb[0] < 0.99 * (box[1] - box[0]))
        box = (np.maximum(nb[0], box[0]), np.minimum(nb[1], box[1]))
        if not shrunk:
            break
    return box
