"""Qué resultados pedir a penhmain, pensado por objetivos del investigador.

Cada objetivo dice qué ficheros escribe penhmain, si el .in ya lo pide y cómo
pedirlo con valores razonables (que luego se ajustan en el panel «Simulación»).
Los cambios respetan las reglas de penhmain: un cuerpo solo puede estar en un
detector de impacto y en uno de energía depositada, y nunca si es vacío; un
detector de impacto hace a la vez el espectro, la fluencia (IDCUT = 2) y el
espacio de fases (IPSF = 1) de sus cuerpos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.numfmt import fmt_int, fmt_num, plural
from . import sections as sc
from .inputfile import InDocument


@dataclass(frozen=True)
class Goal:
    key: str
    title: str
    files: str  # lo que escribe penhmain
    body: bool = False  # hay que elegir un cuerpo
    group: str = ""  # objetivos que se excluyen (la rejilla de dosis es una sola)


GOALS = [
    Goal("seguir", "Seguir la simulación mientras corre",
         "Todos los resultados y un volcado (DUMPTO) cada DUMPP segundos: ARGUS los va enseñando, y la simulación se "
         "puede reanudar."),
    Goal("dosis-caja", "Dosis en 3D (caja de vóxeles)",
         "3d-dose-map.dat, depth-dose.dat y los perfiles x-, y- y z-dose.dat (GRIDX, GRIDY, GRIDZ).", group="rejilla"),
    Goal("dosis-cilindro", "Dosis con simetría cilíndrica (haz a lo largo de z)",
         "2d-dose-map.dat (r, z), depth-dose.dat y z-dose.dat (GRIDZ y GRIDR).", group="rejilla"),
    Goal("dosis-esfera", "Dosis radial (fuente puntual, simetría esférica)",
         "radial-dose.dat (GRIDR).", group="rejilla"),
    Goal("espectro", "Espectro de las partículas que entran en un cuerpo",
         "spc-impdet-##.dat: energía de lo que entra, por tipo de partícula (IMPDET e IDBODY).", body=True),
    Goal("fluencia", "Fluencia en un cuerpo (espectro de longitud de trayectoria)",
         "fln-impdet-##.dat además del espectro (IMPDET con IDCUT = 2).", body=True),
    Goal("psf", "Espacio de fases de lo que entra en un cuerpo (para reutilizarlo como fuente)",
         "psf-impdet.dat: cada partícula con su energía, posición, dirección y peso (IMPDET con IPSF = 1; uno "
         "solo por simulación).",
         body=True),
    Goal("deposito", "Espectro de energía depositada en un cuerpo (altura de pulsos)",
         "spc-enddet-##.dat: la energía que deja cada lluvia en el cuerpo, como un detector real (ENDETC y EDBODY).",
         body=True),
    Goal("angulos", "Distribución angular (θ, φ) de las partículas que salen",
         "angle.dat además de polar-angle-*.dat (NBANGL con más de un intervalo de φ)."),
    Goal("trayectorias", "Trayectorias para verlas sobre la geometría",
         "Un fichero .trk por tipo de partícula con las lluvias que se piden (LTRACK)."),
]
GOALS_BY_KEY = {g.key: g for g in GOALS}

DEFAULT_DUMPP = 60.0  # s
DEFAULT_DUMP = "dump.dmp"  # el volcado de penhmain sin DUMPTO
DEFAULT_LTRACK = 10
MAX_LTRACK = 50  # NPSHOW: con LTRACK de más, penhmain guarda 50 lluvias (y una más: 51)
DEFAULT_NBANGL = (90, 36)


def _body_ref(geo, kb: int) -> sc.BodyRef:
    """Cómo nombrar el cuerpo ``kb`` en el .in: su etiqueta tal cual (4 caracteres, «   1» también) si penhmain la
    lleva a ese cuerpo; si no (clones, la envolvente, INCLUDE sin «*»…), su número."""
    if geo is None or not 1 <= kb <= geo.nbodys:
        return sc.BodyRef(None, kb)
    from .source_edit import body_reference

    ref = body_reference(geo, kb)
    return sc.BodyRef(ref["label"], ref["kb"])


def _resolve(ref: sc.BodyRef, geo) -> int | None:
    if ref.kb is not None:
        return int(ref.kb)
    if geo is None or ref.label is None:
        return None
    b = geo.find_body(ref.label)
    return geo.bodies.index(b) + 1 if b is not None else None


def _impact_with(doc: InDocument, geo, kb: int) -> tuple[list[sc.ImpactDetector], int | None]:
    dets = sc.impact_detectors(doc)
    for i, d in enumerate(dets):
        if any(_resolve(b, geo) == kb for b in d.bodies):
            return dets, i
    return dets, None


def _energy_with(doc: InDocument, geo, kb: int) -> tuple[list[sc.EnergyDetector], int | None]:
    dets = sc.energy_detectors(doc)
    for i, d in enumerate(dets):
        if any(_resolve(b, geo) == kb for b in d.bodies):
            return dets, i
    return dets, None


def _bodies_text(d, geo) -> str:
    names = []
    for b in d.bodies:
        names.append(b.label.strip() if b.label else f"n.º {b.kb}")
    return ", ".join(names) or "sin cuerpos"


def dumpp(doc: InDocument) -> float | None:
    """El DUMPP que escribe el .in (None: no lo dice o no es un número)."""
    v = sc.value(doc, "DUMPP")
    try:
        return float(v[0]) if v else None
    except (TypeError, ValueError):
        return None


def follow_period(doc: InDocument) -> float | None:
    """Cada cuántos segundos escribe penhmain los resultados y el volcado mientras simula, si el .in lo pide con
    DUMPP: PMRDR sube a 30 s lo que es menos (también 0 o un número negativo) y deja en un día lo que es más, y con
    1e10 o más no lo hace (penhmain.f 4235-4245). None sin DUMPP (penhmain solo lo hace una vez al día) o con
    DUMPP ≥ 1e10."""
    from ..results.folder import dump_period

    d = dumpp(doc)
    return None if d is None else dump_period(d)


def _seconds(t: float) -> str:
    return f"{fmt_num(t, 6)} s" if t < 3600 else f"{fmt_num(t / 3600, 3)} h"


def status(doc: InDocument, key: str, geo=None) -> str:
    """Lo que el .in ya pide para ese objetivo («» si nada), con lo que hará penhmain."""
    if key == "seguir":
        period = follow_period(doc)
        if period is None:
            return ""
        d = dumpp(doc)
        note = " (penhmain no baja de 30 s)" if d < 30.0 else " (penhmain no pasa de un día)" if d > 86400.0 else ""
        return f"cada {_seconds(period)}{note} en {sc.text(doc, 'DUMPTO') or 'dump.dmp'}"
    if key.startswith("dosis-"):
        g = sc.dose_grid(doc)
        want = {"dosis-caja": "box", "dosis-cilindro": "cylinder", "dosis-esfera": "sphere"}[key]
        if g.kind != want:
            return ""
        if want == "box":
            return f"{fmt_int(g.x[2])} × {fmt_int(g.y[2])} × {fmt_int(g.z[2])} vóxeles"
        if want == "cylinder":
            return (f"r hasta {fmt_num(g.r[0], 6)} cm, z de {fmt_num(min(g.z[:2]), 6)} a "
                    f"{fmt_num(max(g.z[:2]), 6)} cm")
        return f"r hasta {fmt_num(g.r[0], 6)} cm"
    if key in ("espectro", "fluencia", "psf"):
        dets = sc.impact_detectors(doc)
        sel = [(i, d) for i, d in enumerate(dets, start=1)
               if key == "espectro" or (key == "fluencia" and d.idcut == 2) or (key == "psf" and d.ipsf == 1)]
        return "; ".join(f"detector {i}: {_bodies_text(d, geo)}" for i, d in sel)
    if key == "deposito":
        return "; ".join(f"detector {i}: {_bodies_text(d, geo)}" for i, d in enumerate(sc.energy_detectors(doc),
                                                                                         start=1))
    if key == "angulos":
        v = sc.value(doc, "NBANGL")
        return f"{int(v[0])} × {int(v[1])} intervalos (θ × φ)" if v and int(v[1]) > 1 else ""
    if key == "trayectorias":
        v = sc.value(doc, "LTRACK")
        if not v or int(v[0]) <= 0:
            return ""
        n = int(v[0])
        # NPSHOW = MIN(LTRACK, 50), y penhmain mira N.GT.NPSHOW después de cada lluvia: escribe una más.
        if n > MAX_LTRACK:
            return (f"{plural(MAX_LTRACK + 1, 'lluvia')} (LTRACK {fmt_int(n)}: penhmain guarda como mucho "
                    f"{MAX_LTRACK + 1})")
        return f"{plural(n + 1, 'lluvia')} (LTRACK {fmt_int(n)}: penhmain escribe también la siguiente)"
    raise KeyError(key)


def default_grid(key: str, bounds) -> sc.DoseGrid:
    """Rejilla que cubre la escena (``bounds`` = (lo, hi) de la geometría) con un número impar de intervalos."""
    lo, hi = (np.asarray(b, dtype=float) for b in bounds)

    def axis(k: int, n: int) -> tuple[float, float, int]:
        return sc.round_bound(lo[k], hi[k] - lo[k]), sc.round_bound(hi[k], hi[k] - lo[k]), n

    if key == "dosis-caja":
        return sc.DoseGrid(axis(0, 51), axis(1, 51), axis(2, 101))
    axes = (0, 1) if key == "dosis-cilindro" else (0, 1, 2)
    inscribed = min(min(-lo[i], hi[i]) for i in axes)
    span = max(hi[i] - lo[i] for i in axes)
    if inscribed > 0.1 * span:  # el eje (o el origen) está bien dentro: el mayor que cabe
        r = (sc.round_bound(inscribed, span), 50)
    else:  # en un borde o fuera: el que cubre la escena (redondeado hacia arriba, para que la cubra)
        rad = math.sqrt(sum(max(lo[i] ** 2, hi[i] ** 2) for i in axes))
        q = 10.0 ** (math.floor(math.log10(rad)) - 3) if rad > 0 else 1.0
        r = (float(f"{math.ceil(rad / q - 1e-9) * q:.10g}"), 50)
    if key == "dosis-cilindro":
        return sc.DoseGrid(z=axis(2, 101), r=r)
    return sc.DoseGrid(r=r)


def variance_reduction(doc: InDocument) -> str | None:
    """Qué reducción de varianza del .in no admite detectores de energía depositada (penhmain, LIFEND: forzado de
    interacciones efectivo o splitting de rayos X), o None. Un FORCER negativo cuenta: penhmain lo calcula con la
    física y casi siempre fuerza."""
    for e in sc.body_list(doc, "IFORCE"):
        vals = list(e.values) + [1, 3, 1.0][len(e.values):]
        try:
            forcer = float(vals[2])
        except (TypeError, ValueError):
            continue
        if forcer > 1.01 or forcer < -1e-6:
            return "forzado de interacciones (IFORCE)"
    if sc.body_list(doc, "IXRSPL"):
        return "splitting de rayos X (IXRSPL)"
    return None


def apply(doc: InDocument, key: str, *, geo=None, bounds=None, kb: int | None = None) -> str:
    """Pide el objetivo en el .in (lo que no estuviera ya). Devuelve qué se ha hecho."""
    if key == "seguir":
        done = []
        if not sc.text(doc, "DUMPTO"):
            sc.set_text(doc, "DUMPTO", DEFAULT_DUMP)
            done.append(f"volcado en {DEFAULT_DUMP}")
        if follow_period(doc) is None:  # sin DUMPP o con DUMPP ≥ 1e10
            sc.set_value(doc, "DUMPP", [DEFAULT_DUMPP])
            done.append(f"resultados cada {_seconds(DEFAULT_DUMPP)}")
        return ", ".join(done) or "ya estaba"
    if key.startswith("dosis-"):
        if bounds is None:
            raise ValueError("Hace falta la geometría para saber dónde poner la rejilla")
        g = default_grid(key, bounds)
        sc.set_dose_grid(doc, g)
        return {"box": "rejilla de caja", "cylinder": "rejilla cilíndrica", "sphere": "rejilla esférica"}[g.kind]
    if key in ("espectro", "fluencia", "psf", "deposito"):
        if kb is None or geo is None:
            raise ValueError("Elija el cuerpo")
        if not 1 <= kb <= geo.nbodys:
            raise ValueError("Ese cuerpo no existe")
        if geo.body(kb).material == 0:
            raise ValueError("Un detector no puede ser un cuerpo vacío (material 0)")
        ref = _body_ref(geo, kb)
        name = f"«{ref.label.strip()}»" if ref.label and ref.label.strip() else f"n.º {kb}"
        if key == "deposito":
            dets, i = _energy_with(doc, geo, kb)
            if i is not None:
                return f"{name} ya está en el detector de energía {i + 1}"
            why = variance_reduction(doc)
            if why:
                raise ValueError(f"El .in tiene {why}: con eso penhmain no admite detectores de energía depositada "
                                 "(se detendría con el error 3095: el espectro saldría sesgado). Quítelo antes en el "
                                 "panel «Simulación» («Reducción de varianza»)")
            dets.append(sc.EnergyDetector(bodies=[ref]))
            sc.set_energy_detectors(doc, dets)
            return f"detector de energía depositada {len(dets)} con {name}"
        dets, i = _impact_with(doc, geo, kb)
        if i is None:
            d = sc.ImpactDetector(bodies=[ref])
            dets.append(d)
            i = len(dets) - 1
            what = f"detector de impacto {i + 1} con {name}"
        else:
            d = dets[i]
            what = f"detector de impacto {i + 1} ({name})"
        if key == "fluencia":
            if d.ipsf == 1 and d.psf:
                raise ValueError(f"El detector {i + 1} escribe el espacio de fases en {d.psf}: con la fluencia, "
                                 "penhmain usaría el mismo fichero para las dos cosas (quite el nombre IDPSF)")
            d.idcut = 2
            what += ": con la fluencia"
        elif key == "psf":
            other = next((k for k, x in enumerate(dets) if x.ipsf == 1 and k != i), None)
            if other is not None:
                raise ValueError(f"El detector de impacto {other + 1} ya escribe el espacio de fases: penhmain solo "
                                 "admite uno (añada este cuerpo a ese detector en el panel «Simulación»)")
            if d.idcut == 2 and d.psf:
                raise ValueError(f"El detector {i + 1} calcula la fluencia y tiene nombre de espacio de fases "
                                 f"({d.psf}): penhmain los mezclaría")
            d.ipsf = 1
            what += ": con el espacio de fases"
        sc.set_impact_detectors(doc, dets)
        return what
    if key == "angulos":
        v = sc.value(doc, "NBANGL")
        nth = int(v[0]) if v and int(v[0]) != 0 else DEFAULT_NBANGL[0]
        sc.set_value(doc, "NBANGL", [nth, DEFAULT_NBANGL[1]])
        return f"{nth} × {DEFAULT_NBANGL[1]} intervalos"
    if key == "trayectorias":
        v = sc.value(doc, "LTRACK")
        if v and int(v[0]) > 0:
            return "ya estaba"
        sc.set_value(doc, "LTRACK", [DEFAULT_LTRACK])
        return f"{DEFAULT_LTRACK} lluvias"
    raise KeyError(key)


def unfollow(doc: InDocument) -> None:
    """Quita «Seguir la simulación»: DUMPP y, si es «DUMPTO dump.dmp» (lo que pone marcarlo), también DUMPTO, que
    sin DUMPP no cambia nada (penhmain escribe igualmente dump.dmp al acabar: su DUMPP por defecto es 2e9). Otro
    volcado lo puso el usuario y se queda."""
    sc.set_value(doc, "DUMPP", None)
    name = sc.text(doc, "DUMPTO")
    if name == DEFAULT_DUMP and sc.text(doc, "RESUME") != DEFAULT_DUMP:
        sc.set_text(doc, "DUMPTO", None)


def detector_bodies(geo) -> list[tuple[int, str]]:
    """Cuerpos que pueden ser detectores (con material y que no son la envolvente), con su nombre."""
    out = []
    if geo is None:
        return out
    for kb in range(1, geo.nbodys + 1):
        b = geo.body(kb)
        if b.material == 0 or b.origin == "enclosure" or b.is_module and not b.material:
            continue
        lab = geo.balias(kb).strip()
        kind = "módulo" if b.is_module else ("TAC" if b.origin == "ct" else "cuerpo")
        out.append((kb, f"{lab or 'n.º ' + str(kb)} ({kind}, material {b.material if b.material < 9999 else 'TAC'})"))
    return out


__all__ = ["DEFAULT_DUMP", "DEFAULT_DUMPP", "DEFAULT_LTRACK", "DEFAULT_NBANGL", "GOALS", "GOALS_BY_KEY", "Goal",
           "MAX_LTRACK", "apply", "default_grid", "detector_bodies", "dumpp", "follow_period", "status", "unfollow"]
