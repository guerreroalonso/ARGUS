"""Espectrometría con los espectros de penhmain: resolución del detector, ventanas de energía y coeficientes.

* Resolución del detector: como el programa ``mcdet`` de penhan (F. Salvat,
  2025): el espectro de energía depositada (histograma) se convoluciona con una
  gaussiana de anchura FWHM(E) (menos la anchura del intervalo, la corrección de
  mcdet) integrada sobre cada intervalo y, en los detectores PIPS, con una cola
  exponencial de un solo lado hacia energías bajas (E0 = 5,5 keV). Detectores de
  mcdet: PIPS (FWHM = 2 keV), Si(Li), CdTe y NaI(Tl); o una gaussiana a medida,
  FWHM = a + b·√E + c·E (en eV).
* Ventanas: el área bajo el espectro entre dos energías, con el fondo lineal
  estimado a los lados de la ventana, y el área neta del pico, su centroide y
  su anchura.
* Coeficientes de conversión: ∫ y(E)·c(E) dE con una tabla (E, c) del usuario
  (p. ej. fluencia → kerma o dosis), interpolada en log-log.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .data import ZERO_WIDTH_EV

# FWHM(E) de mcdet.f (E y FWHM en eV) y la cola exponencial E0 (eV) de cada detector.
DETECTORS: dict[str, tuple[str, Callable[[np.ndarray], np.ndarray], float]] = {
    "pips": ("PIPS (silicio implantado; partículas pesadas)", lambda e: np.full(np.shape(e), 2.0e3), 5.5e3),
    "sili": ("Si(Li)", lambda e: np.sqrt(7849.255 + 2.237253 * np.asarray(e)), 0.0),
    "cdte": ("CdTe", lambda e: np.sqrt(64.5 * np.asarray(e)), 0.0),
    "nai": ("NaI(Tl) (Zerby)", lambda e: 40.0 * np.sqrt(np.asarray(e)) + 0.028 * np.asarray(e), 0.0),
}

_GL_X, _GL_W = np.polynomial.legendre.leggauss(12)


def custom_fwhm(a: float, b: float, c: float) -> Callable[[np.ndarray], np.ndarray]:
    """FWHM(E) = a + b·√E + c·E (todo en eV)."""
    return lambda e: a + b * np.sqrt(np.asarray(e, dtype=np.float64)) + c * np.asarray(e, dtype=np.float64)


def _cdf(x: np.ndarray) -> np.ndarray:
    return _erf_cdf(x)


def _erf_cdf(x: np.ndarray) -> np.ndarray:
    """Φ(x) con la aproximación racional de Hastings que usa mcdet (PACN), vectorizada."""
    p, a1, a2, a3, a4, a5 = 0.3275911, 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    xx = np.asarray(x, dtype=np.float64) / math.sqrt(2.0)
    yy = np.abs(xx)
    t = 1.0 / (1.0 + p * yy)
    erfun = 1.0 - t * (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5)))) * np.exp(-yy * yy)
    out = np.where(xx < 0, 0.5 - 0.5 * erfun, 0.5 + 0.5 * erfun)
    return np.where(xx > 25, 1.0, np.where(xx < -25, 0.0, out))


@dataclass
class Convolved:
    edges: np.ndarray  # canales (eV)
    y: np.ndarray  # densidad por eV (como la entrada)
    err: np.ndarray | None  # 1 sigma
    fwhm_at: Callable[[float], float]

    @property
    def x(self) -> np.ndarray:
        return 0.5 * (self.edges[:-1] + self.edges[1:])


def convolve(edges: np.ndarray, y: np.ndarray, err: np.ndarray | None, fwhm: Callable[[np.ndarray], np.ndarray],
             e0: float = 0.0, channel: float | None = None, bin_correction: bool = True) -> Convolved:
    """El espectro (histograma: ``y`` = probabilidad por eV en cada intervalo de ``edges``) visto con un detector.

    Como mcdet: σ(E) = 0,4246609·(FWHM(E) − ΔE) (con ``bin_correction``, ΔE la anchura del intervalo; como mínimo
    1 eV), integrada sobre cada intervalo de entrada; canales de salida de anchura ``channel`` (por defecto, la de
    los intervalos) desde max(Emin − 5σ − E0, 0) hasta Emax + 5σ + E0; y, con ``e0`` > 0, la cola exponencial hacia
    energías bajas. Las incertidumbres se propagan como independientes de un intervalo a otro.
    """
    edges = np.asarray(edges, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    w_in = np.diff(edges)
    # Un intervalo de anchura cero (una energía exacta de psource.dat) es una línea: su probabilidad (valor por
    # 1e-15 eV) se reparte como la de una energía sola; no cuenta para la anchura de los canales.
    real = w_in > 0
    if not real.any():
        raise ValueError("El espectro no tiene intervalos con anchura")
    uniform = bool(w_in[real].max() <= 1.01 * w_in[real].min())

    def sigma(e, width):
        f = np.asarray(fwhm(np.maximum(e, 1e-3)), dtype=np.float64)
        if bin_correction:
            f = f - width  # la corrección de mcdet, con la anchura de ese intervalo (NBE < 0: intervalos log.)
        return 0.4246609 * np.maximum(f, 1.0)

    s_lo = float(sigma(np.array([edges[0]]), w_in[0])[0])
    s_hi = float(sigma(np.array([edges[-1]]), w_in[-1])[0])
    emin = max(edges[0] - 5.0 * s_lo - e0, 0.0)
    emax = edges[-1] + 5.0 * s_hi + e0
    if channel and channel > 0 or uniform:
        de = float(channel) if channel and channel > 0 else float(np.median(w_in[real]))
        nch = int(math.ceil((emax - emin) / de))
        if nch > 200_000:
            raise ValueError("Demasiados canales: aumente su anchura")
        out_edges = emin + de * np.arange(nch + 1)
    else:
        if e0 > 0:
            raise ValueError("La cola exponencial necesita canales iguales: escriba la anchura del canal")
        de = None
        out_edges = _extended_edges(np.unique(edges), emin, emax)
        nch = len(out_edges) - 1
    content = np.zeros(nch)
    var = np.zeros(nch)
    w_eff = np.where(real, w_in, ZERO_WIDTH_EV)
    prob = y * w_eff  # probabilidad de cada intervalo
    pvar = (np.asarray(err, dtype=np.float64) * w_eff) ** 2 if err is not None else None
    prob = np.where(np.isfinite(prob), prob, 0.0)
    if pvar is not None:
        pvar = np.where(np.isfinite(pvar), pvar, 0.0)
    # Nodos de Gauss-Legendre dentro de cada intervalo de entrada (mcdet integra con Romberg); en una línea, todos en
    # su energía.
    for j in np.nonzero(prob != 0)[0]:
        a, b = max(edges[j], 1e-3), max(edges[j + 1], 1e-3)
        if b < a:
            continue
        e = 0.5 * (a + b) + 0.5 * (b - a) * _GL_X
        sg = sigma(e, w_in[j])
        lo = int(max(0, np.searchsorted(out_edges, a - 8.0 * sg.max(), side="right") - 1))
        hi = int(min(nch, np.searchsorted(out_edges, b + 8.0 * sg.max(), side="left") + 1))
        if hi <= lo:
            continue
        ch = out_edges[lo:hi + 1]
        cdf = _cdf((ch[:, None] - e[None, :]) / sg[None, :])  # (canales + 1, nodos)
        frac = (np.diff(cdf, axis=0) * (0.5 * _GL_W)[None, :]).sum(axis=1)  # promedio en el intervalo
        content[lo:hi] += prob[j] * frac
        if pvar is not None:
            var[lo:hi] += pvar[j] * frac * frac
    if e0 > 0:
        content, var = _exponential_tail(content, var, de, e0)
    w_out = np.diff(out_edges)
    yy = content / w_out
    ee = np.sqrt(var) / w_out if pvar is not None else None
    return Convolved(out_edges, yy, ee, lambda e: float(fwhm(np.array([e]))[0]))


def _extended_edges(edges: np.ndarray, emin: float, emax: float) -> np.ndarray:
    """Los mismos intervalos que el espectro (no uniformes), prolongados hasta [emin, emax] con el mismo paso
    relativo de cada extremo (o el mismo paso absoluto si el primer borde es 0)."""
    lo_part: list[float] = []
    e0, e1 = float(edges[0]), float(edges[1])
    step = e1 - e0
    ratio = e1 / e0 if e0 > 0 else None
    log = bool(ratio and ratio > 1.0)
    # Hacia 0, los intervalos logarítmicos se harían diminutos (miles por debajo de 1 eV): por debajo de la anchura
    # del primero, un último intervalo hasta emin.
    floor = max(emin, step) if log else emin
    cur = e0
    while cur > emin + 1e-12 and len(lo_part) < 10_000:
        if log:
            nxt = cur / ratio
            if nxt < floor:
                lo_part.append(emin if floor > emin else nxt)
                break
            cur = nxt
        else:
            cur = cur - step
            if cur <= 0 or cur < emin - step:
                break
        lo_part.append(cur)
    hi_part: list[float] = []
    a, b = float(edges[-2]), float(edges[-1])
    step = b - a
    ratio = b / a if a > 0 else None
    cur = b
    while cur < emax and len(hi_part) < 10_000:
        cur = cur * ratio if ratio and ratio > 1.0 else cur + step
        hi_part.append(cur)
    return np.concatenate([np.array(lo_part[::-1]), edges, np.array(hi_part)])


def _exponential_tail(p: np.ndarray, var: np.ndarray, de: float, e0: float) -> tuple[np.ndarray, np.ndarray]:
    """Convolución con una exponencial de un solo lado (hacia energías bajas), como en mcdet (canales iguales).

    Lo que se queda en el canal: 1 − (E0/ΔE)(1 − r); lo que baja k canales: (E0/ΔE)(1 − r)²·r^(k−1), con
    r = exp(−ΔE/E0) (lo mismo que el (E0/ΔE)(exp(ΔE/E0) − 1)(1 − r)·r^k de mcdet, sin la exponencial positiva, que
    se desborda con canales de más de ~700·E0: 3,9 MeV con la E0 de 5,5 keV del PIPS)."""
    x = de / e0
    r = math.exp(-x)
    one_r = -math.expm1(-x)  # 1 − r, también con canales mucho más estrechos que E0
    fct = one_r * one_r / x  # al canal de abajo (k = 1)
    fctd = 1.0 - one_r / x
    n = len(p)
    tail = np.zeros(n)
    tail_v = np.zeros(n)
    acc = 0.0
    acc_v = 0.0
    for i in range(n - 2, -1, -1):  # S_i = Σ_{j>i} p_j r^(j−i−1)
        acc = p[i + 1] + r * acc
        acc_v = var[i + 1] + r * r * acc_v
        tail[i] = acc
        tail_v[i] = acc_v
    return p * fctd + fct * tail, var * fctd * fctd + fct * fct * tail_v


def _overlap(edges: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Fracción de cada intervalo dentro de [lo, hi] (un intervalo de anchura cero, una energía exacta: 1 si está
    dentro, bordes incluidos)."""
    a = np.clip(edges[:-1], lo, hi)
    b = np.clip(edges[1:], lo, hi)
    w = np.diff(edges)
    with np.errstate(divide="ignore", invalid="ignore"):
        part = np.where(w > 0, (b - a) / np.where(w > 0, w, 1.0), 0.0)
    return np.where(w > 0, part, ((edges[:-1] >= lo) & (edges[:-1] <= hi)).astype(np.float64))


@dataclass
class Window:
    lo: float
    hi: float
    gross: float  # área (probabilidad por primaria) en la ventana
    gross_err: float
    background: float = 0.0  # fondo lineal bajo la ventana
    background_err: float = 0.0
    net: float = 0.0
    net_err: float = 0.0
    centroid: float | None = None
    fwhm: float | None = None


def window(edges: np.ndarray, y: np.ndarray, err: np.ndarray | None, lo: float, hi: float,
           side_bins: int = 3, subtract: bool = True) -> Window:
    """Área del espectro entre ``lo`` y ``hi`` (en las unidades de ``edges``) y, con ``subtract``, el área neta sobre
    un fondo lineal: la recta que une el valor medio de ``side_bins`` intervalos a cada lado de la ventana."""
    edges = np.asarray(edges, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if hi <= lo:
        raise ValueError("La ventana debe ir de menos a más energía")
    w = np.diff(edges)
    line = w <= 0  # energías exactas (psource.dat): su probabilidad cuenta entera si están dentro
    f = _overlap(edges, lo, hi)
    e = np.zeros_like(y) if err is None else np.asarray(err, dtype=np.float64)
    weff = np.where(line, ZERO_WIDTH_EV, w)
    gross = float(np.nansum(y * weff * f))
    gross_err = float(np.sqrt(np.nansum((e * weff * f) ** 2)))
    out = Window(lo, hi, gross, gross_err, net=gross, net_err=gross_err)
    inside = np.nonzero(f > 0)[0]
    if not len(inside):
        return out
    i0, i1 = int(inside[0]), int(inside[-1])
    if subtract:
        # El fondo, con los intervalos de verdad a cada lado (una línea no es fondo).
        left = np.array([j for j in range(i0 - 1, -1, -1) if not line[j]][:side_bins][::-1], dtype=int)
        right = np.array([j for j in range(i1 + 1, len(y)) if not line[j]][:side_bins], dtype=int)
        if len(left) and len(right):
            yl, yr = float(y[left].mean()), float(y[right].mean())
            vl = float((e[left] ** 2).sum()) / len(left) ** 2
            vr = float((e[right] ** 2).sum()) / len(right) ** 2
            xl = float(0.5 * (edges[left[0]] + edges[left[-1] + 1]))
            xr = float(0.5 * (edges[right[0]] + edges[right[-1] + 1]))
            # Fondo = ∫ recta entre lo y hi.
            span = xr - xl
            c = 0.5 * (lo + hi)
            t = (c - xl) / span if span > 0 else 0.5
            height = yl * (1 - t) + yr * t
            out.background = height * (hi - lo)
            out.background_err = (hi - lo) * math.sqrt(vl * (1 - t) ** 2 + vr * t * t)
            out.net = gross - out.background
            out.net_err = math.hypot(gross_err, out.background_err)
            bg_line = yl + (yr - yl) * ((0.5 * (edges[:-1] + edges[1:]) - xl) / span if span > 0 else 0.0)
        else:
            bg_line = np.zeros_like(y)
    else:
        bg_line = np.zeros_like(y)
    net_y = np.nan_to_num(np.clip(y - bg_line, 0.0, None) * f)
    tot = float((net_y * weff).sum())
    xc = 0.5 * (edges[:-1] + edges[1:])
    if tot > 0:
        out.centroid = float((net_y * weff * xc).sum() / tot)
        # La anchura, la del espectro continuo (una energía exacta no tiene anchura ni altura de densidad).
        out.fwhm = _window_fwhm(np.where(line, 0.0, net_y), xc, edges, inside)
    return out


def _window_fwhm(y: np.ndarray, xc: np.ndarray, edges: np.ndarray, inside: np.ndarray) -> float | None:
    """Anchura a media altura del pico de una ventana, con los cruces interpolados entre los centros de los
    intervalos (no en números enteros de intervalos); si la ventana no llega a la mitad por un lado, su borde."""
    if not len(inside):
        return None
    k = int(inside[int(np.argmax(y[inside]))])
    half = 0.5 * float(y[k])
    if half <= 0:
        return None
    lo_i, hi_i = int(inside[0]), int(inside[-1])
    j = k
    while j > lo_i and y[j - 1] >= half:
        j -= 1
    if j > lo_i:
        a, b = float(y[j - 1]), float(y[j])
        left = float(xc[j - 1] + (half - a) * (xc[j] - xc[j - 1]) / (b - a)) if b != a else float(xc[j])
    else:
        left = float(edges[lo_i])
    j = k
    while j < hi_i and y[j + 1] >= half:
        j += 1
    if j < hi_i:
        a, b = float(y[j]), float(y[j + 1])
        right = float(xc[j] + (a - half) * (xc[j + 1] - xc[j]) / (a - b)) if b != a else float(xc[j])
    else:
        right = float(edges[hi_i + 1])
    return right - left


def load_coefficients(path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Tabla de coeficientes (dos primeras columnas: energía y coeficiente), con cualquier separador."""
    from .measured import read_table

    t = read_table(path)
    if t.data.shape[1] < 2 or len(t.data) < 2:
        raise ValueError("La tabla necesita dos columnas (energía y coeficiente) y al menos dos filas")
    e, c = t.data[:, 0], t.data[:, 1]
    ok = np.isfinite(e) & np.isfinite(c)
    order = np.argsort(e[ok])
    return e[ok][order], c[ok][order], t.columns


def conversion(edges: np.ndarray, y: np.ndarray, err: np.ndarray | None, ce: np.ndarray, cv: np.ndarray,
               loglog: bool = True) -> tuple[float, float, float]:
    """∫ y(E)·c(E) dE con la tabla (``ce``, ``cv``) interpolada (log-log si todo es positivo) en el centro de cada
    intervalo. Devuelve (valor, sigma, fracción del espectro fuera de la tabla, que no se cuenta)."""
    edges = np.asarray(edges, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    w = np.where(np.diff(edges) > 0, np.diff(edges), ZERO_WIDTH_EV)  # una energía exacta: su probabilidad entera
    xc = 0.5 * (edges[:-1] + edges[1:])
    inside = (xc >= ce[0]) & (xc <= ce[-1])
    c = np.zeros_like(xc)
    if loglog and np.all(ce > 0) and np.all(cv > 0):
        good = inside & (xc > 0)
        c[good] = np.exp(np.interp(np.log(xc[good]), np.log(ce), np.log(cv)))
    else:
        c[inside] = np.interp(xc[inside], ce, cv)
    val = float((y * c * w).sum())
    e = np.zeros_like(y) if err is None else np.asarray(err, dtype=np.float64)
    sig = float(np.sqrt(((e * c * w) ** 2).sum()))
    total = float(np.abs(y * w).sum())
    out = float(np.abs(y * w)[~inside].sum()) / total if total > 0 else 0.0
    return val, sig, out


__all__ = ["DETECTORS", "Convolved", "Window", "conversion", "convolve", "custom_fwhm", "load_coefficients",
           "window"]
