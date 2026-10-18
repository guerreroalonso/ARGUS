"""Medidas en curvas: máximos, anchuras, alcances, penumbras, planitud, simetría, áreas y medias.

Las medidas se agrupan en conjuntos pensados para cada tipo de curva, pero son
generales:

* ``general``: máximo, mínimo, área y media;
* ``bragg`` (dosis en profundidad de protones, alfas o iones): posición y valor
  del pico, alcances distales R90, R80 y R50, caídas distales 80-20 y 90-10,
  anchura del pico al 80 % y cociente entre el pico y la entrada;
* ``pdd`` (dosis en profundidad de fotones o electrones): profundidad del
  máximo, R90, R80 y R50, alcance práctico Rp (electrones) y PDD a 10 y 20 cm;
* ``perfil`` (perfiles laterales): centro, anchura a media altura (tamaño de
  campo), penumbras 80-20 y 90-10 a cada lado, planitud y simetría en el 80 %
  central;
* ``espectro``: energía media, energía del pico principal y su anchura a media
  altura, picos principales y área;
* ``dvh`` (histogramas dosis-volumen acumulados): D98, D95, D50, D2, dosis media
  y máxima.

La incertidumbre de cada medida se estima repitiéndola con curvas en las que
cada valor se varía al azar según su incertidumbre (independientes).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

PRESETS = {"general": "General", "bragg": "Pico de Bragg", "pdd": "Dosis en profundidad (fotones, electrones)",
           "perfil": "Perfil lateral", "espectro": "Espectro", "lineas": "Líneas discretas",
           "dvh": "Histograma dosis-volumen"}
RESAMPLES = 200


@dataclass
class Metric:
    key: str
    name: str
    value: float | None
    err: float | None = None  # 1 sigma
    unit: str = ""  # «x», «y», «%», «x/y»… se sustituye por las unidades de la curva
    note: str = ""


# ---------------------------------------------------------------------------
# Herramientas
# ---------------------------------------------------------------------------

def _clean(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    return x[ok], y[ok]


def peak(x, y, refine: bool = True) -> tuple[float, float, int]:
    """Posición y valor del máximo (con una parábola por los tres puntos más altos)."""
    x, y = _clean(x, y)
    if len(y) == 0:
        return math.nan, math.nan, -1
    i = int(np.argmax(y))
    if refine and 0 < i < len(y) - 1:
        x0, x1, x2 = x[i - 1:i + 2]
        y0, y1, y2 = y[i - 1:i + 2]
        den = (x0 - x1) * (x0 - x2) * (x1 - x2)
        if den != 0:
            a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / den
            b = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) + x0 * x0 * (y1 - y2)) / den
            if a < 0:
                xv = -b / (2 * a)
                if x0 <= xv <= x2:
                    c = y1 - a * x1 * x1 - b * x1
                    return float(xv), float(a * xv * xv + b * xv + c), i
    return float(x[i]), float(y[i]), i


def crossing(x, y, level: float, side: str = "right", start: int | None = None) -> float | None:
    """Primera posición, desde ``start`` hacia ``side``, en la que la curva baja de ``level`` (interpolada)."""
    x, y = _clean(x, y)
    if len(y) < 2:
        return None
    if start is None:
        start = int(np.argmax(y))
    if side == "right":
        for j in range(start, len(y) - 1):
            if y[j] >= level > y[j + 1]:
                return float(x[j] + (level - y[j]) * (x[j + 1] - x[j]) / (y[j + 1] - y[j]))
        return None
    for j in range(start, 0, -1):
        if y[j] >= level > y[j - 1]:
            return float(x[j] + (level - y[j]) * (x[j - 1] - x[j]) / (y[j - 1] - y[j]))
    return None


def value_at(x, y, x0: float) -> float | None:
    x, y = _clean(x, y)
    if len(x) == 0 or x0 < x[0] or x0 > x[-1]:
        return None
    return float(np.interp(x0, x, y))


def area(x, y, edges=None, lo: float | None = None, hi: float | None = None) -> float:
    """Área bajo la curva entre lo y hi: histograma (con bordes) o regla del trapecio."""
    y = np.asarray(y, dtype=np.float64)
    if edges is not None and len(edges) == len(y) + 1:
        e = np.asarray(edges, dtype=np.float64)
        a = e[:-1] if lo is None else np.maximum(e[:-1], lo)
        b = e[1:] if hi is None else np.minimum(e[1:], hi)
        w = np.clip(b - a, 0.0, None)
        return float(np.nansum(y * w))
    x = np.asarray(x, dtype=np.float64)
    m = np.ones(len(x), dtype=bool)
    if lo is not None:
        m &= x >= lo
    if hi is not None:
        m &= x <= hi
    return float(np.trapezoid(y[m], x[m])) if m.sum() > 1 else 0.0


def mean_position(x, y, edges=None, lines=()) -> float | None:
    """Posición media (con ``lines``: posiciones exactas y su área, p. ej. una energía exacta de un espectro)."""
    y = np.asarray(y, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    w = np.diff(edges) if edges is not None and len(edges) == len(y) + 1 else np.ones_like(y)
    tot = float(np.nansum(y * w)) + sum(a for _p, a in lines)
    mom = float(np.nansum(x * y * w)) + sum(p * a for p, a in lines)
    return mom / tot if tot > 0 else None


def usable(x, y, err=None, edges=None, lines=None) -> tuple:
    """Los puntos con valor de una curva que se ve (x, y, err, bordes, líneas): sin los NaN; los bordes se quedan si
    lo que falta son intervalos de anchura cero (las energías exactas de un espectro, que van en ``lines`` como
    (posición, área))."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    e = None if err is None else np.asarray(err, dtype=np.float64)[ok]
    ed = None
    if edges is not None and len(edges) == len(y) + 1:
        edges = np.asarray(edges, dtype=np.float64)
        drop = np.nonzero(~ok)[0]
        if not len(drop):
            ed = edges
        elif np.all(edges[drop + 1] - edges[drop] <= 0):
            ed = np.delete(edges, drop + 1)
    return x[ok], y[ok], e, ed, list(lines or [])


def local_peaks(x, y, rel: float = 0.05, top: int = 5, err=None) -> list[tuple[float, float]]:
    """Picos que superan ``rel`` del máximo y sobresalen al menos un 25 % de su entorno a los dos lados
    (y tres veces su incertidumbre, si se da) (los ``top`` más altos, separados al menos tres intervalos)."""
    e = None if err is None else np.nan_to_num(np.asarray(err, dtype=np.float64))
    x, y = _clean(x, y)
    n = len(y)
    if n < 3:
        return []
    ymax = float(np.max(y))
    win = max(3, n // 40)
    cand = []
    for i in range(1, n - 1):
        if y[i] >= y[i - 1] and y[i] > y[i + 1] and y[i] >= rel * ymax:
            left = float(np.min(y[max(0, i - win):i])) if i > 0 else y[i]
            right = float(np.min(y[i + 1:i + 1 + win])) if i < n - 1 else y[i]
            prom = y[i] - max(left, right)
            significant = e is None or len(e) != n or prom >= 3.0 * max(e[i], 1e-300)
            if (prom >= 0.25 * y[i] and significant) or y[i] == ymax:
                cand.append(i)
    cand.sort(key=lambda i: -y[i])
    chosen: list[int] = []
    for i in cand:
        if all(abs(i - j) >= 3 for j in chosen):
            chosen.append(i)
        if len(chosen) >= top:
            break
    return [(float(x[i]), float(y[i])) for i in chosen]


# ---------------------------------------------------------------------------
# Conjuntos de medidas
# ---------------------------------------------------------------------------

def _general(x, y, edges, err=None, lines=()) -> list[Metric]:
    xm, ym, _ = peak(x, y, refine=False)
    i = int(np.nanargmin(y)) if len(y) else 0
    return [Metric("xmax", "Posición del máximo", xm, unit="x"), Metric("ymax", "Máximo", ym, unit="y"),
            Metric("ymin", "Mínimo", float(np.nanmin(y)) if len(y) else None, unit="y"),
            Metric("xmin", "Posición del mínimo", float(x[i]) if len(y) else None, unit="x"),
            Metric("area", "Área", area(x, y, edges) + sum(a for _p, a in lines), unit="x·y"),
            Metric("mean", "Posición media", mean_position(x, y, edges, lines), unit="x")]


def _minus(a: float | None, b: float | None) -> float | None:
    """a − b, o None si falta alguna (una posición 0,0 es una posición: no se toma por que falte)."""
    return None if a is None or b is None else float(a - b)


def beam_forward(x, y) -> bool:
    """¿Va el haz hacia x creciente en esta curva de dosis en profundidad? (penhmain la da en función de z, y el haz
    puede ir hacia −z.) Pasado el alcance casi no hay dosis y en la entrada sí: el haz va hacia −x si el primer
    extremo con dosis (sin los ceros del vacío) se queda por debajo del 20 % del máximo y del otro extremo."""
    x, y = _clean(x, y)
    y = y[np.argsort(x, kind="stable")]
    pos = np.nonzero(y > 0)[0]
    if len(pos) < 2:
        return True
    k = max(1, min(3, len(pos) // 10))
    first, last = float(np.mean(y[pos[:k]])), float(np.mean(y[pos[-k:]]))
    return not (first < last and first < 0.2 * float(np.max(y)))


def _distal_crossing(xb, yb, level: float) -> float | None:
    """Donde la curva (en el sentido del haz) baja de ``level`` por última vez: el primer punto que llega a ese nivel
    visto desde el final de la curva hacia dentro (en una meseta con ruido, no un hueco de la meseta)."""
    above = np.nonzero(yb >= level)[0]
    if not len(above) or int(above[-1]) >= len(yb) - 1:
        return None
    j = int(above[-1])
    return float(xb[j] + (level - yb[j]) * (xb[j + 1] - xb[j]) / (yb[j + 1] - yb[j]))


def _proximal_crossing(xb, yb, level: float, ipk: int) -> float | None:
    """Donde la curva (en el sentido del haz) llega a ``level`` por primera vez antes del pico (None si ya empieza
    por encima)."""
    above = np.nonzero(yb[:ipk + 1] >= level)[0]
    if not len(above) or int(above[0]) == 0:
        return None
    j = int(above[0])
    return float(xb[j - 1] + (level - yb[j - 1]) * (xb[j] - xb[j - 1]) / (yb[j] - yb[j - 1]))


def _bragg(x, y, edges, err=None) -> list[Metric]:
    """Pico de Bragg (o SOBP): lo distal se busca desde el final de la curva en el sentido del haz (también hacia −z),
    y la entrada es el primer valor con dosis en ese sentido."""
    xm, ym, i = peak(x, y)
    out = [Metric("xpeak", "Posición del pico", xm, unit="x"), Metric("ypeak", "Valor en el pico", ym, unit="y")]
    xc, yc = _clean(x, y)
    order = np.argsort(xc, kind="stable")
    xc, yc = xc[order], yc[order]
    sign = 1.0 if beam_forward(xc, yc) else -1.0
    xb, yb = (xc, yc) if sign > 0 else (xc[::-1], yc[::-1])
    ok = i >= 0 and len(yb) >= 2 and math.isfinite(ym)
    r = {p: (_distal_crossing(xb, yb, p / 100.0 * ym) if ok else None) for p in (90, 80, 50, 20, 10)}
    for p in (90, 80, 50):
        out.append(Metric(f"R{p}", f"R{p} (distal)", r[p], unit="x"))

    def along(a: float | None, b: float | None) -> float | None:
        """Distancia de b a a en el sentido del haz."""
        d = _minus(a, b)
        return None if d is None else sign * d

    out.append(Metric("fall8020", "Caída distal 80-20", along(r[20], r[80]), unit="x"))
    out.append(Metric("fall9010", "Caída distal 90-10", along(r[10], r[90]), unit="x"))
    ipk = int(np.argmax(yb)) if len(yb) else -1
    p80 = _proximal_crossing(xb, yb, 0.8 * ym, ipk) if ok else None
    out.append(Metric("width80", "Anchura del pico al 80 %", along(r[80], p80), unit="x"))
    pos = np.nonzero(yb > 0)[0]
    entrance = float(np.mean(yb[pos[:2]])) if len(pos) >= 2 else None
    out.append(Metric("peak_entrance", "Cociente pico/entrada", ym / entrance if entrance and ok else None, unit=""))
    return out


def _pdd(x, y, edges, err=None, electrons: bool = True) -> list[Metric]:
    xm, ym, i = peak(x, y)
    out = [Metric("dmax", "Profundidad del máximo", xm, unit="x"), Metric("ymax", "Máximo", ym, unit="y")]
    for p in (90, 80, 50):
        out.append(Metric(f"R{p}", f"R{p}", crossing(x, y, p / 100.0 * ym, "right", i) if i >= 0 else None,
                          unit="x"))
    if electrons:
        out.append(Metric("Rp", "Alcance práctico Rp", practical_range(x, y), unit="x",
                          note="Tangente en la bajada más pronunciada hasta el fondo de frenado"))
    p10 = value_at(x, y, 10.0)  # profundidades de 10 y 20 cm (las posiciones de penhmain están en cm)
    p20 = value_at(x, y, 20.0)
    out.append(Metric("pdd10", "PDD a 10 cm", 100 * p10 / ym if p10 is not None and ym else None, unit="%"))
    out.append(Metric("pdd20", "PDD a 20 cm", 100 * p20 / ym if p20 is not None and ym else None, unit="%"))
    out.append(Metric("pdd2010", "Cociente PDD20/PDD10", p20 / p10 if p10 and p20 is not None else None, unit=""))
    return out


def practical_range(x, y) -> float | None:
    """Rp: la tangente en el punto de mayor pendiente (bajando, tras el máximo) corta el fondo (media del
    último 10 % de la curva)."""
    x, y = _clean(x, y)
    if len(y) < 6:
        return None
    i = int(np.argmax(y))
    if i >= len(y) - 3:
        return None
    dy = np.gradient(y, x)
    j = i + int(np.argmin(dy[i:]))
    slope = dy[j]
    if slope >= 0:
        return None
    tail = y[int(0.9 * len(y)):]
    bg = float(np.mean(tail)) if len(tail) else 0.0
    return float(x[j] + (bg - y[j]) / slope)


def _profile(x, y, edges, err=None) -> list[Metric]:
    x, y = _clean(x, y)
    xm, ym, i = peak(x, y, refine=False)
    if i < 0:
        return []
    left50 = crossing(x, y, 0.5 * ym, "left", i)
    right50 = crossing(x, y, 0.5 * ym, "right", i)
    center = 0.5 * (left50 + right50) if left50 is not None and right50 is not None else xm
    ycen = value_at(x, y, center) or ym
    out = [Metric("center", "Centro (mitad de la anchura al 50 %)", center, unit="x"),
           Metric("ycenter", "Valor en el centro", ycen, unit="y"),
           Metric("fwhm", "Anchura a media altura (tamaño de campo)",
                  (right50 - left50) if left50 is not None and right50 is not None else None, unit="x")]
    c = int(np.argmin(np.abs(x - center)))
    for side, name in (("left", "izquierda"), ("right", "derecha")):
        l80 = crossing(x, y, 0.8 * ycen, side, c)
        l20 = crossing(x, y, 0.2 * ycen, side, c)
        l90 = crossing(x, y, 0.9 * ycen, side, c)
        l10 = crossing(x, y, 0.1 * ycen, side, c)
        d82, d91 = _minus(l20, l80), _minus(l10, l90)
        out.append(Metric(f"pen8020_{side}", f"Penumbra 80-20 {name}", abs(d82) if d82 is not None else None,
                          unit="x"))
        out.append(Metric(f"pen9010_{side}", f"Penumbra 90-10 {name}", abs(d91) if d91 is not None else None,
                          unit="x"))
    if left50 is not None and right50 is not None and right50 > left50:
        half = 0.4 * (right50 - left50)  # 80 % central
        m = (x >= center - half) & (x <= center + half)
        if m.sum() >= 2:
            ymx, ymn = float(np.max(y[m])), float(np.min(y[m]))
            out.append(Metric("flatness", "Planitud (80 % central)", 100 * (ymx - ymn) / (ymx + ymn) if ymx + ymn
                              else None, unit="%", note="(máx − mín)/(máx + mín)"))
            xs = x[m]
            mirror = np.interp(2 * center - xs, x, y)
            out.append(Metric("symmetry", "Simetría (80 % central)",
                              100 * float(np.max(np.abs(y[m] - mirror))) / ycen if ycen else None, unit="%",
                              note="máx |D(x) − D(simétrico)| / D(centro)"))
    return out


def _lines(x, y, edges, err=None) -> list[Metric]:
    """Líneas discretas (SLINES): cada valor es la probabilidad de una energía, no una densidad."""
    y = np.nan_to_num(np.asarray(y, dtype=np.float64))
    x = np.asarray(x, dtype=np.float64)
    tot = float(y.sum())
    out = [Metric("emean", "Energía media", float((x * y).sum() / tot) if tot > 0 else None, unit="x"),
           Metric("epeak", "Línea más probable", float(x[int(np.argmax(y))]) if len(y) else None, unit="x"),
           Metric("area", "Probabilidad total", tot, unit="y"),
           Metric("nlines", "Número de líneas", float(np.count_nonzero(y > 0)), unit="")]
    return out


def _spectrum(x, y, edges, err=None, lines=()) -> list[Metric]:
    """Espectro (densidad por unidad de energía); ``lines``: energías exactas (posición, probabilidad), que cuentan
    en la energía media y en el área."""
    xm, ym, i = peak(x, y, refine=False)
    out = [Metric("emean", "Energía media", mean_position(x, y, edges, lines), unit="x"),
           Metric("epeak", "Energía del pico principal", xm, unit="x")]
    if i >= 0:
        l50 = crossing(x, y, 0.5 * ym, "left", i)
        r50 = crossing(x, y, 0.5 * ym, "right", i)
        out.append(Metric("fwhm", "Anchura a media altura del pico", (r50 - l50) if l50 is not None and r50 is not None
                          else None, unit="x"))
    exact = float(sum(a for _p, a in lines))
    total = area(x, y, edges) + exact
    out.append(Metric("area", "Área (probabilidad total)", total, unit="x·y"))
    if lines:
        out.append(Metric("exact", "Con energía exacta", 100.0 * exact / total if total > 0 else None, unit="%",
                          note="Intervalos de anchura cero del espectro (una energía repetida en SPECTR)"))
    peaks = local_peaks(x, y, err=err)
    for k, (px, _py) in enumerate(peaks[1:4], start=2):
        out.append(Metric(f"peak{k}", f"Pico {k}", px, unit="x"))
    return out


def _dvh(x, y, edges, err=None) -> list[Metric]:
    """Histograma dosis-volumen acumulado (volumen en % o en fracción con dosis ≥ x).

    Con ``edges`` (el ct-dvh.dat de penhmain), cada valor está en el centro de su intervalo pero es el volumen con
    dosis ≥ el borde inferior: se usa ese borde."""
    if edges is not None and len(edges) == len(np.asarray(x)) + 1:
        x = np.asarray(edges, dtype=np.float64)[:-1]
    x, y = _clean(x, y)
    if len(y) < 2:
        return []
    if np.nanmax(y) <= 1.0001:  # penhmain da la fracción del volumen
        y = 100.0 * y
    order = np.argsort(x)
    x, y = x[order], y[order]

    def d_at(pct: float) -> float | None:
        """Dosis a la que el volumen acumulado baja de ``pct`` %."""
        for j in range(len(y) - 1):
            if y[j] >= pct > y[j + 1]:
                return float(x[j] + (pct - y[j]) * (x[j + 1] - x[j]) / (y[j + 1] - y[j]))
        return float(x[-1]) if y[-1] >= pct else None

    # Dosis media: ∫ V(D) dD / V (la curva es la fracción con dosis ≥ D).
    xs = np.concatenate([[0.0], x]) if x[0] > 0 else x
    ys = np.concatenate([[100.0], y]) if x[0] > 0 else y
    mean = float(np.trapezoid(ys, xs) / 100.0)
    pos = np.nonzero(y > 0)[0]
    dmax = float(x[pos[-1]]) if len(pos) else None
    return [Metric("D98", "D98 (casi mínima)", d_at(98.0), unit="x"), Metric("D95", "D95", d_at(95.0), unit="x"),
            Metric("D50", "D50 (mediana)", d_at(50.0), unit="x"), Metric("D2", "D2 (casi máxima)", d_at(2.0), unit="x"),
            Metric("Dmean", "Dosis media", mean, unit="x"), Metric("Dmax", "Dosis máxima", dmax, unit="x")]


_PRESET_FUNCS: dict[str, Callable] = {"general": _general, "bragg": _bragg, "pdd": _pdd, "perfil": _profile,
                                      "espectro": _spectrum, "lineas": _lines, "dvh": _dvh}


def compute(preset: str, x, y, err=None, edges=None, *, resamples: int = RESAMPLES, seed: int = 12345,
            lines=None) -> list[Metric]:
    """Medidas de un conjunto, con su incertidumbre (si se da ``err``, 1 sigma). ``lines``: posiciones exactas con
    su área, [(x, área)] (las energías exactas de un espectro), en las medidas que las cuentan (área y medias)."""
    fn = _PRESET_FUNCS[preset]
    if lines and preset in ("general", "espectro"):
        fixed = [(float(p), float(a)) for p, a in lines]
        fn = (lambda f: lambda x_, y_, ed_, er_: f(x_, y_, ed_, er_, fixed))(fn)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    base = fn(x, y, edges, err)
    if err is None or resamples <= 1 or not np.any(np.asarray(err) > 0):
        return base
    rng = np.random.default_rng(seed)
    e = np.nan_to_num(np.asarray(err, dtype=np.float64))
    samples: dict[str, list[float]] = {m.key: [] for m in base}
    for _ in range(resamples):
        yy = y + e * rng.standard_normal(len(y))
        for m in fn(x, yy, edges, err):
            if m.key in samples and m.value is not None and np.isfinite(m.value):
                samples[m.key].append(m.value)
    for m in base:
        vals = samples[m.key]
        if m.value is not None and len(vals) >= max(10, resamples // 4):
            m.err = float(np.std(vals, ddof=1))
    return base


def guess_preset(kind: str, x_name: str = "", primary: int | None = None, discrete: bool = False) -> str:
    """Conjunto de medidas más útil para una curva."""
    if discrete:
        return "lineas"
    if kind == "ct-dvh" or kind.startswith("dvh"):
        return "dvh"
    if kind in ("depth-dose", "ct-ddose", "p3d-depth") or (kind in ("z-dose", "ct-z-dose", "profile")
                                                          and x_name in ("z", "profundidad")):
        if primary in (4, 5):
            return "bragg"
        return "pdd"
    if kind in ("x-dose", "y-dose", "ct-x-dose", "ct-y-dose", "p3d-x", "p3d-y") or x_name in ("x", "y", "r"):
        return "perfil"
    if x_name in ("E", "E depositada"):
        return "espectro"
    return "general"


def product_unit(x_unit: str, y_unit: str) -> str:
    """Unidad del área bajo una curva (x·y), simplificada cuando y es una densidad por unidad de x:
    keV · 1/(keV·partícula) → 1/partícula; cm · Gy → cm·Gy."""
    xu, yu = x_unit.strip(), y_unit.strip()
    if not xu:
        return yu
    if not yu:
        return xu
    m = re.match(r"^(.*)/\((.+?)·(.+)\)$", yu)
    if m and m.group(2) == xu:
        return f"{m.group(1)}/{m.group(3)}" if m.group(1) != "1" or "·" not in m.group(3) else f"1/({m.group(3)})"
    m = re.match(r"^(.*)/([^/()·]+)$", yu)
    if m and m.group(2) == xu:
        return "" if m.group(1) == "1" else m.group(1)  # MeV · 1/MeV: una probabilidad, sin unidad
    return f"{xu}·{yu}"


def unit_of(metric: Metric, x_unit: str, y_unit: str) -> str:
    return {"x": x_unit, "y": y_unit, "x·y": product_unit(x_unit, y_unit), "%": "%", "": ""}.get(metric.unit,
                                                                                                metric.unit)


__all__ = ["Metric", "PRESETS", "area", "beam_forward", "compute", "crossing", "guess_preset", "local_peaks",
           "mean_position", "peak", "practical_range", "product_unit", "unit_of", "usable", "value_at"]
