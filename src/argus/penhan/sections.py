"""Secciones del .in de penhmain como datos: se leen de las líneas y se escriben en su sitio.

Cada sección del fichero (parámetros de los materiales, geometría, campos,
reducción de varianza, detectores, rejilla de dosis, trabajo...) se lee de
las líneas del documento y se reescribe **en la posición y en el orden en que
las lee PMRDR0** (penhmain.f 2025), de modo que el fichero sigue siendo
válido para penhmain:

* las líneas que no cambian se conservan tal cual (con sus comentarios);
* las nuevas llevan el comentario canónico de ``penhmain-layout.in``;
* los bloques (detectores) se reescriben enteros, con sus líneas en el orden
  del bloque (IMPDET, IDSPC, IDPSF, IDFLNC, IDAGEL, IDAGEF, IDBODY*,
  IDKPAR*...).

Los valores por defecto (lo que usa penhmain si falta la línea) son los del
módulo ``PENHMAIN_input`` de penhmain.f 2025, no siempre los que dice el
encabezado del programa (p. ej. sin MSIMPA las energías de absorción son de
50 eV).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .inputfile import InDocument, InLine
from .keywords import DEFAULTS, SPEC
from .materials import _material_groups

PARTICLE_NAMES = {1: "electrones", 2: "fotones", 3: "positrones", 4: "protones", 5: "alfas", 6: "neutrones"}
MAX_DETECTORS = 5
MAX_BODY_LIST = {"DSMAX": 50, "EABSB": 50, "EABSBH": 50, "EFIELD": 100, "BFIELD": 100, "IFORCE": 100,
                 "IBRSPL": 100, "IXRSPL": 100}
MAX_DETECTOR_BODIES = {"IDBODY": 50, "EDBODY": 100}
MAX_SPECTRUM = 250  # MISPEC y MLINE


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def kw6(keyword: str) -> str:
    return keyword.ljust(6)[:6]


def new_line(keyword: str, values=None, *, text: str = "", label: str | None = None,
             kb: int | None = None) -> InLine:
    """Línea nueva con el comentario canónico de penhmain-layout.in."""
    kw = kw6(keyword)
    spec = SPEC[kw]
    return InLine(kw=kw, values=list(values or []), text=text, label=label, kb=kb, comment=spec.comment,
                  modified=True)


def replace_lines(doc: InDocument, keywords, new: list[InLine], anchor: str | None = None) -> None:
    """Sustituye todas las líneas de ``keywords`` por ``new`` sin mover los comentarios de en medio.

    Las líneas que no cambian se quedan como están; la que cambia ocupa el sitio de la que sustituye y solo se
    añaden o quitan las que sobran o faltan (:meth:`InDocument.replace_block`). Si no había ninguna, las nuevas
    se ponen donde las leería penhmain (tras la última línea de una palabra anterior en el orden de PMRDR0).
    """
    doc.replace_block([kw6(k) for k in keywords], new, anchor=kw6(anchor) if anchor else None)


def value(doc: InDocument, keyword: str) -> list | None:
    """Valores de la (primera) línea de ``keyword``; None si no está."""
    ln = doc.first(kw6(keyword))
    return list(ln.values) if ln is not None else None


def text(doc: InDocument, keyword: str) -> str | None:
    ln = doc.first(kw6(keyword))
    return ln.text.strip(" ") if ln is not None else None  # como penhmain: solo los blancos


def set_value(doc: InDocument, keyword: str, values: list | None) -> None:
    """Pone (o quita, con None) una línea de valores que solo aparece una vez.

    Si la línea ya tiene esos valores no se toca (conserva su texto).
    """
    if values is None:
        doc.remove(kw6(keyword))
        return
    ln = doc.first(kw6(keyword))
    if ln is not None and not ln.error and list(ln.values) == list(values):
        return
    doc.set(kw6(keyword), list(values))


def set_text(doc: InDocument, keyword: str, name: str | None) -> None:
    """Pone (o quita, con None o vacío) una línea de nombre de fichero (columnas 8-27)."""
    if not name or not name.strip():
        doc.remove(kw6(keyword))
        return
    ln = doc.first(kw6(keyword))
    if ln is not None and not ln.error and (ln.text or "").strip() == name.strip():
        return
    doc.set(kw6(keyword), text=name.strip())


def check_label(label: str | None) -> str | None:
    """Etiqueta de cuerpo para el .in: exactamente 4 caracteres (como la guarda PENGEOM).

    PMRDR0 solo toma como etiqueta lo que hay entre apóstrofos si mide 4
    caracteres; con otra longitud intentaría leerlo como un número.
    """
    if label is not None and len(label) != 4:
        raise ValueError(f"La etiqueta '{label}' no tiene 4 caracteres: penhmain no la reconocería.")
    if label is not None and "'" in label:
        raise ValueError(f"La etiqueta «{label}» tiene un apóstrofo: penhmain no la puede leer entre apóstrofos. "
                         "Nombre ese cuerpo por su número (o cámbiele la etiqueta en la geometría).")
    return label


def default(keyword: str) -> tuple:
    """Valores que usa penhmain si falta la línea (módulo PENHMAIN_input)."""
    return tuple(DEFAULTS.get(keyword.strip(), SPEC[kw6(keyword)].defaults))


# ---------------------------------------------------------------------------
# Listas de cuerpos (DSMAX, EABSB, EABSBH, EFIELD, BFIELD, IFORCE, IBRSPL, IXRSPL)
# ---------------------------------------------------------------------------

@dataclass
class BodyValue:
    """Una línea «palabra KB valores»: cuerpo por etiqueta (entre apóstrofos) o por número."""

    label: str | None
    kb: int | None
    values: list = field(default_factory=list)
    line: InLine | None = field(default=None, compare=False, repr=False)  # línea del .in (al leer)

    @property
    def ref(self) -> str:
        return f"'{self.label.strip()}'" if self.label is not None else str(self.kb)


def body_list(doc: InDocument, keyword: str) -> list[BodyValue]:
    return [BodyValue(ln.label, ln.kb, list(ln.values), ln) for ln in doc.find(kw6(keyword))]


def set_body_list(doc: InDocument, keyword: str, entries: list[BodyValue]) -> None:
    """Todas las líneas de una lista de cuerpos, en el sitio de la sección."""
    limit = MAX_BODY_LIST.get(keyword.strip())
    if limit is not None and len(entries) > limit:
        raise ValueError(f"penhmain admite como máximo {limit} líneas {keyword.strip()}.")
    lines = [new_line(keyword, e.values, label=check_label(e.label), kb=e.kb if e.label is None else None)
             for e in entries]
    replace_lines(doc, [keyword], lines)


# ---------------------------------------------------------------------------
# Materiales: parámetros de simulación (MSIMPA, MSIMPH, MSIMPN) de cada material
# ---------------------------------------------------------------------------

MATERIAL_PARAMS = ("MSIMPA", "MSIMPH", "MSIMPN")


def material_params(doc: InDocument, number: int) -> dict[str, list | None]:
    """{'MSIMPA': [...] o None, ...} del material ``number`` (1, 2...)."""
    groups = _material_groups(doc)
    out: dict[str, list | None] = {k: None for k in MATERIAL_PARAMS}
    if not 1 <= number <= len(groups):
        return out
    for i in groups[number - 1][1:]:
        ln = doc.lines[i]
        key = ln.kw.strip()
        if key in out and out[key] is None:
            out[key] = list(ln.values)
    return out


def set_material_param(doc: InDocument, number: int, keyword: str, values: list | None) -> None:
    """Pone, cambia o quita (None) la línea MSIMPA/MSIMPH/MSIMPN del material ``number``."""
    key = keyword.strip()
    if key not in MATERIAL_PARAMS:
        raise ValueError(keyword)
    groups = _material_groups(doc)
    if not 1 <= number <= len(groups):
        raise ValueError(f"No hay material {number} en el .in.")
    group = groups[number - 1]
    existing = [i for i in group[1:] if doc.lines[i].kw.strip() == key]
    if values is None:
        if existing:
            drop = set(existing)
            doc.lines = [ln for i, ln in enumerate(doc.lines) if i not in drop]
            doc.analyze()
        return
    if existing:
        doc.modify(doc.lines[existing[0]], values=list(values))
        return
    order = MATERIAL_PARAMS.index(key)
    pos = group[0] + 1
    for i in group[1:]:
        k = doc.lines[i].kw.strip()
        if k in MATERIAL_PARAMS and MATERIAL_PARAMS.index(k) < order:
            pos = i + 1
    doc.insert_lines(pos, [new_line(key, values)])


def effective_material(doc: InDocument, number: int) -> dict[str, float]:
    """Parámetros que usará penhmain para el material ``number`` (con sus recortes y valores por defecto).

    EABS >= 50 eV; C1, C2 en [0, 0,2]; WCC >= 0; WCR >= 10 eV (o -10 si es
    negativo); EABSH >= 100 keV; C1H, C2H en [0, 0,2]; WCCH >= 0 (PMRDR,
    penhmain.f 2025). Los de hadrones solo se usan con protones, alfas o
    fuentes radiactivas.
    """
    par = material_params(doc, number)
    a = [float(v) for v in (par["MSIMPA"] or default("MSIMPA"))]
    h = [float(v) for v in (par["MSIMPH"] or default("MSIMPH"))]
    n = [float(v) for v in (par["MSIMPN"] or default("MSIMPN"))]

    def clip(v: float) -> float:
        return min(max(v, 0.0), 0.2)

    return {
        "EABS1": max(a[0], 50.0), "EABS2": max(a[1], 50.0), "EABS3": max(a[2], 50.0),
        "C1": clip(a[3]), "C2": clip(a[4]), "WCC": max(a[5], 0.0), "WCR": -10.0 if a[6] < 0 else max(a[6], 10.0),
        "EABSH1": max(h[0], 1.0e5), "EABSH2": max(h[1], 1.0e5), "EABSH3": max(h[2], 1.0e5),
        "C1H": clip(h[3]), "C2H": clip(h[4]), "WCCH": max(h[5], 0.0),
        "FNABS": n[0], "FORCEN": n[1],
    }


def set_all_material_params(doc: InDocument, keyword: str, values: list | None) -> None:
    for n in range(1, len(_material_groups(doc)) + 1):
        set_material_param(doc, n, keyword, values)


# ---------------------------------------------------------------------------
# Geometría: PARINP
# ---------------------------------------------------------------------------

def parinp(doc: InDocument) -> dict[int, float]:
    out: dict[int, float] = {}
    for ln in doc.find("PARINP"):
        if len(ln.values) >= 2:
            out[int(ln.values[0])] = float(ln.values[1])
    return out


def set_parinp(doc: InDocument, values: dict[int, float]) -> None:
    """Líneas PARINP (justo tras GEOMFN, en orden de índice)."""
    if len(values) > 50:
        raise ValueError("penhmain admite como máximo 50 parámetros PARINP.")
    old = {int(ln.values[0]): ln for ln in doc.find("PARINP") if ln.values}
    lines = []
    for ip, v in sorted(values.items()):
        ln = new_line("PARINP", [int(ip), float(v)])
        prev = old.get(int(ip))
        if prev is not None and prev.comment.strip():
            ln.comment = prev.comment  # se conserva la descripción del parámetro
        lines.append(ln)
    idx = [i for i, ln in enumerate(doc.lines) if ln.kw == "PARINP"]
    if not idx:
        g = next((i for i, ln in enumerate(doc.lines) if ln.kw == "GEOMFN"), None)
        if g is None:
            raise ValueError("El .in no tiene línea GEOMFN: PARINP va justo después.")
    replace_lines(doc, ["PARINP"], lines, anchor="PARINP")


# ---------------------------------------------------------------------------
# Detectores
# ---------------------------------------------------------------------------

@dataclass
class BodyRef:
    label: str | None
    kb: int | None
    line: InLine | None = field(default=None, compare=False, repr=False)

    @property
    def ref(self) -> str:
        return f"'{self.label.strip()}'" if self.label is not None else str(self.kb)


@dataclass
class ImpactDetector:
    el: float = 0.0
    eu: float = 0.0
    nbe: int = 100
    ipsf: int = 0
    idcut: int = 1
    spc: str = ""  # IDSPC (vacío: spc-impdet-##.dat)
    psf: str = ""  # IDPSF (vacío: psf-impdet.dat)
    flnc: str = ""  # IDFLNC (vacío: fln-impdet-##.dat)
    age: tuple[float, float, int] | None = None  # IDAGEL
    agef: str = ""  # IDAGEF
    bodies: list[BodyRef] = field(default_factory=list)
    kpar: list[int] = field(default_factory=list)  # IDKPAR (vacío: todas las partículas)
    line: InLine | None = field(default=None, compare=False, repr=False)  # línea IMPDET (al leer)
    lines: dict = field(default_factory=dict, compare=False, repr=False)  # palabra -> línea (al leer)


@dataclass
class AngularDetector:
    theta1: float = 0.0
    theta2: float = 90.0
    phi1: float = 0.0
    phi2: float = 360.0
    energy: tuple[float, float, int] | None = None  # ADENER
    kpar: int | None = None  # ADKPAR (una sola partícula)
    spc: str = ""  # ADSPC
    line: InLine | None = field(default=None, compare=False, repr=False)
    lines: dict = field(default_factory=dict, compare=False, repr=False)


@dataclass
class EnergyDetector:
    el: float = 0.0
    eu: float = 0.0
    nbe: int = 100
    spc: str = ""  # EDSPC
    bodies: list[BodyRef] = field(default_factory=list)
    line: InLine | None = field(default=None, compare=False, repr=False)
    lines: dict = field(default_factory=dict, compare=False, repr=False)


IMPACT_KEYWORDS = ("IMPDET", "IDSPC", "IDPSF", "IDFLNC", "IDAGEL", "IDAGEF", "IDBODY", "IDKPAR")
ANGULAR_KEYWORDS = ("ANGDET", "ADENER", "ADKPAR", "ADSPC")
ENERGY_KEYWORDS = ("ENDETC", "EDSPC", "EDBODY")


def _num(v, kind="real"):
    return int(v) if kind == "int" else float(v)


def impact_detectors(doc: InDocument) -> list[ImpactDetector]:
    out: list[ImpactDetector] = []
    for ln in doc.lines:
        k = ln.kw.strip()
        if k == "IMPDET":
            v = list(ln.values) + [0.0, 0.0, 100, 0, 1][len(ln.values):]
            out.append(ImpactDetector(float(v[0]), float(v[1]), int(v[2]), int(v[3]), int(v[4]), line=ln))
            continue
        elif not out or k not in IMPACT_KEYWORDS:
            continue
        out[-1].lines.setdefault(k, ln)
        if k == "IDSPC":
            out[-1].spc = ln.text.strip(" ")
        elif k == "IDPSF":
            out[-1].psf = ln.text.strip(" ")
        elif k == "IDFLNC":
            out[-1].flnc = ln.text.strip(" ")
        elif k == "IDAGEL" and len(ln.values) >= 3:
            out[-1].age = (float(ln.values[0]), float(ln.values[1]), int(ln.values[2]))
        elif k == "IDAGEF":
            out[-1].agef = ln.text.strip(" ")
        elif k == "IDBODY":
            out[-1].bodies.append(BodyRef(ln.label, ln.kb, ln))
        elif k == "IDKPAR" and ln.values:
            out[-1].kpar.append(int(ln.values[0]))
    return out


def set_impact_detectors(doc: InDocument, dets: list[ImpactDetector]) -> None:
    if len(dets) > MAX_DETECTORS:
        raise ValueError(f"penhmain admite como máximo {MAX_DETECTORS} detectores de impacto.")
    lines = []
    for d in dets:
        if len(d.bodies) > MAX_DETECTOR_BODIES["IDBODY"]:
            raise ValueError("Un detector de impacto admite como máximo 50 cuerpos.")
        lines.append(new_line("IMPDET", [float(d.el), float(d.eu), int(d.nbe), int(d.ipsf), int(d.idcut)]))
        if d.spc:
            lines.append(new_line("IDSPC", text=d.spc))
        if d.psf:
            lines.append(new_line("IDPSF", text=d.psf))
        if d.flnc:
            lines.append(new_line("IDFLNC", text=d.flnc))
        if d.age is not None:
            lines.append(new_line("IDAGEL", [float(d.age[0]), float(d.age[1]), int(d.age[2])]))
        if d.agef:
            lines.append(new_line("IDAGEF", text=d.agef))
        for b in d.bodies:
            lines.append(new_line("IDBODY", label=check_label(b.label), kb=b.kb if b.label is None else None))
        for kp in d.kpar:
            lines.append(new_line("IDKPAR", [int(kp)]))
    replace_lines(doc, IMPACT_KEYWORDS, lines, anchor="IMPDET")


def angular_detectors(doc: InDocument) -> list[AngularDetector]:
    out: list[AngularDetector] = []
    for ln in doc.lines:
        k = ln.kw.strip()
        if k == "ANGDET":
            v = list(ln.values) + [0.0, 90.0, 0.0, 360.0][len(ln.values):]
            out.append(AngularDetector(*(float(x) for x in v[:4]), line=ln))
            continue
        elif not out or k not in ANGULAR_KEYWORDS:
            continue
        out[-1].lines.setdefault(k, ln)
        if k == "ADENER" and len(ln.values) >= 3:
            out[-1].energy = (float(ln.values[0]), float(ln.values[1]), int(ln.values[2]))
        elif k == "ADKPAR" and ln.values:
            out[-1].kpar = int(ln.values[0])
        elif k == "ADSPC":
            out[-1].spc = ln.text.strip(" ")
    return out


def set_angular_detectors(doc: InDocument, dets: list[AngularDetector]) -> None:
    if len(dets) > MAX_DETECTORS:
        raise ValueError(f"penhmain admite como máximo {MAX_DETECTORS} detectores angulares.")
    lines = []
    for d in dets:
        lines.append(new_line("ANGDET", [float(d.theta1), float(d.theta2), float(d.phi1), float(d.phi2)]))
        if d.energy is not None:
            lines.append(new_line("ADENER", [float(d.energy[0]), float(d.energy[1]), int(d.energy[2])]))
        if d.kpar is not None:
            lines.append(new_line("ADKPAR", [int(d.kpar)]))
        if d.spc:
            lines.append(new_line("ADSPC", text=d.spc))
    replace_lines(doc, ANGULAR_KEYWORDS, lines, anchor="ANGDET")


def energy_detectors(doc: InDocument) -> list[EnergyDetector]:
    out: list[EnergyDetector] = []
    for ln in doc.lines:
        k = ln.kw.strip()
        if k == "ENDETC":
            v = list(ln.values) + [0.0, 0.0, 100][len(ln.values):]
            out.append(EnergyDetector(float(v[0]), float(v[1]), int(v[2]), line=ln))
            continue
        elif not out or k not in ENERGY_KEYWORDS:
            continue
        out[-1].lines.setdefault(k, ln)
        if k == "EDSPC":
            out[-1].spc = ln.text.strip(" ")
        elif k == "EDBODY":
            out[-1].bodies.append(BodyRef(ln.label, ln.kb, ln))
    return out


def set_energy_detectors(doc: InDocument, dets: list[EnergyDetector]) -> None:
    if len(dets) > MAX_DETECTORS:
        raise ValueError(f"penhmain admite como máximo {MAX_DETECTORS} detectores de energía depositada.")
    lines = []
    for d in dets:
        if len(d.bodies) > MAX_DETECTOR_BODIES["EDBODY"]:
            raise ValueError("Un detector de energía depositada admite como máximo 100 cuerpos.")
        lines.append(new_line("ENDETC", [float(d.el), float(d.eu), int(d.nbe)]))
        if d.spc:
            lines.append(new_line("EDSPC", text=d.spc))
        for b in d.bodies:
            lines.append(new_line("EDBODY", label=check_label(b.label), kb=b.kb if b.label is None else None))
    replace_lines(doc, ENERGY_KEYWORDS, lines, anchor="ENDETC")


# ---------------------------------------------------------------------------
# Rejilla de dosis
# ---------------------------------------------------------------------------

GRID_KINDS = {"none": "sin rejilla", "box": "caja (x, y, z)", "cylinder": "cilindro de eje z (r, z)",
              "sphere": "esfera (r)"}


@dataclass
class DoseGrid:
    x: tuple[float, float, int] | None = None
    y: tuple[float, float, int] | None = None
    z: tuple[float, float, int] | None = None
    r: tuple[float, int] | None = None

    @property
    def kind(self) -> str:
        """'none', 'box', 'cylinder', 'sphere' o 'invalid' (combinación que penhmain rechaza)."""
        has = (self.x is not None, self.y is not None, self.z is not None, self.r is not None)
        if has == (False, False, False, False):
            return "none"
        if has == (True, True, True, False):
            return "box"
        if has == (False, False, True, True):
            return "cylinder"
        if has == (False, False, False, True):
            return "sphere"
        return "invalid"


def dose_grid(doc: InDocument) -> DoseGrid:
    g = DoseGrid()
    for key in ("x", "y", "z"):
        v = value(doc, "GRID" + key.upper())
        if v is not None and len(v) >= 3 and int(v[2]) > 0:  # NDB = 0 anula la línea
            setattr(g, key, (float(v[0]), float(v[1]), int(v[2])))
    v = value(doc, "GRIDR")
    if v is not None and len(v) >= 2 and int(v[1]) > 0:
        g.r = (float(v[0]), int(v[1]))
    return g


def grid_text(g: DoseGrid) -> str:
    """La rejilla en palabras, con los números como en el resto de la interfaz: «24 × 24 × 75 vóxeles de
    0,5 × 0,5 × 0,2 cm», «r = 5 cm en 50 anillos, z 0…10 cm en 100 capas», «r = 3 cm en 1 capa»."""
    from ..core.numfmt import fmt_int, fmt_num, plural

    def cm(v: float) -> str:
        return fmt_num(float(v), 6)

    if g.kind == "box":
        ts = (g.x, g.y, g.z)
        ns = [int(t[2]) for t in ts]
        vox = " × ".join(fmt_num(abs(t[1] - t[0]) / max(int(t[2]), 1), 4) for t in ts)
        n = " × ".join(fmt_int(v) for v in ns)
        return f"{n} {'vóxel' if ns == [1, 1, 1] else 'vóxeles'} de {vox} cm"
    if g.kind == "cylinder":
        zlo, zhi = sorted(g.z[:2])
        return (f"r = {cm(g.r[0])} cm en {plural(int(g.r[1]), 'anillo')}, z {cm(zlo)}…{cm(zhi)} cm en "
                f"{plural(int(g.z[2]), 'capa')}")
    if g.kind == "sphere":
        return f"r = {cm(g.r[0])} cm en {plural(int(g.r[1]), 'capa')}"
    return "sin rejilla"


def dose_grid_from_model(grids: dict | None) -> DoseGrid:
    """La rejilla a partir de ``doc.model.grids`` (valores de las líneas GRID tal como las leyó el lector)."""
    g = DoseGrid()
    for key in ("x", "y", "z"):
        v = (grids or {}).get("GRID" + key.upper())
        if v is not None and len(v) >= 3 and int(v[2]) > 0:
            setattr(g, key, (float(v[0]), float(v[1]), int(v[2])))
    v = (grids or {}).get("GRIDR")
    if v is not None and len(v) >= 2 and int(v[1]) > 0:
        g.r = (float(v[0]), int(v[1]))
    return g


GRID_MAX_BINS = {"x": 101, "y": 101, "z": 401, "r": 101}  # penhmain.f: NDXM, NDYM, NDZM, NDRM
DOSE_MAP_BYTES_PER_VOXEL = 96  # una línea de 3d-dose-map.dat (10 columnas) con los separadores de cada plano


GRID_MIN_LENGTH = 1e-6  # penhmain.f: XU ≥ XL + 1e-6 (3102, 3104, 3106)


def _nice6(v: float) -> float:
    """Sin restos del redondeo binario (0.30000000000000004 → 0.3), con cifras de sobra para que el vóxel mida lo
    pedido también lejos del origen (1000.015 cm)."""
    return float(f"{v:.10g}")


def round_bound(v: float, span: float) -> float:
    """Un extremo de la rejilla sacado de la geometría (``span``: el tamaño de la caja en ese eje). Si queda a menos de
    5·10⁻⁴·span de un número redondo a tres cifras de ese tamaño, ese número: la superficie de un cilindro o de una
    esfera cae entre dos rayos y sale un poco por dentro (4,9988 cm por 5 cm, en una caja de 10 cm), y una cara en 0
    queda en 0 sin el ruido del trazado. Si no, con 10 cifras (1000,015 cm se queda: lejos del origen no se pierden
    cifras)."""
    v = float(v)
    span = abs(float(span))
    if not math.isfinite(v):
        return v
    if not (span > 0) or not math.isfinite(span):
        return float(f"{v:.10g}") + 0.0
    q = 10.0 ** (math.floor(math.log10(span)) - 2)
    cand = round(v / q) * q
    if abs(cand - v) <= 5e-4 * span:
        return float(f"{cand:.10g}") + 0.0  # + 0.0: sin «-0»
    return float(f"{v:.10g}") + 0.0


def _es(v: float) -> str:
    """Un número para los textos, con coma decimal."""
    return f"{v:.4g}".replace(".", ",")


def bins_for_voxel(lo: float, hi: float, size: float, max_bins: int, odd: bool = True, anchor: str = "centre"
                   ) -> tuple[float, float, int, str | None]:
    """Intervalos de un eje de la rejilla para un tamaño de vóxel: N = L/Δ redondeado y el eje con la longitud
    justa (N·Δ), así el vóxel mide exactamente ``size``. ``anchor``: «centre» deja el centro (los ejes laterales;
    con ``odd``, N impar, como recomienda penhmain, para que el eje central pase por el centro de un vóxel) y
    «start» deja el principio (la profundidad: la rejilla empieza en la superficie). Si harían falta más de
    ``max_bins``, se dejan los extremos con ``max_bins`` intervalos (y se dice)."""
    if not (size > 0) or not math.isfinite(size):
        raise ValueError("El tamaño del vóxel tiene que ser positivo.")
    lo, hi = min(lo, hi), max(lo, hi)  # penhmain da la vuelta a un eje escrito al revés: empieza en el menor
    length = hi - lo
    if not (length > 0) or not math.isfinite(length):
        raise ValueError("El eje de la rejilla no tiene longitud.")
    ratio = length / size
    n = max(1, int(round(ratio))) if math.isfinite(ratio) and ratio <= 10 * max_bins else max_bins + 1
    if odd and n % 2 == 0:
        n = n + 1 if n + 1 <= max_bins else n - 1
    if n > max_bins:
        n = max_bins if not odd or max_bins % 2 else max_bins - 1
        return lo, hi, n, (f"Como mucho {max_bins} intervalos: con la rejilla de ahora el vóxel mide "
                           f"{_es(length / n)} cm (para que sea de {_es(size)} cm, reduzca la rejilla).")
    span = n * size
    if anchor == "start":
        a, b = lo, lo + span
    else:
        c = 0.5 * (lo + hi)
        a, b = c - 0.5 * span, c + 0.5 * span
    return _nice6(a), _nice6(b), n, None


def radial_bins_for(radius: float, size: float, max_bins: int = 101) -> tuple[float, int, str | None]:
    """Como :func:`bins_for_voxel` para el radio (de 0 a R): R = N·Δr."""
    if not (size > 0) or not math.isfinite(size):
        raise ValueError("El grosor de las capas tiene que ser positivo.")
    if not (radius > 0) or not math.isfinite(radius):
        raise ValueError("El radio de la rejilla tiene que ser positivo.")
    ratio = radius / size
    n = max(1, int(round(ratio))) if math.isfinite(ratio) and ratio <= 10 * max_bins else max_bins + 1
    if n > max_bins:
        return radius, max_bins, (f"Como mucho {max_bins} capas: con el radio de ahora miden "
                                  f"{_es(radius / max_bins)} cm.")
    return _nice6(n * size), n, None


def stretch_axis(lo: float, hi: float, n: int, value: float, end: str, max_bins: int, keep_voxel: bool = True
                 ) -> tuple[float, float, int]:
    """Lleva el extremo ``end`` («lo» o «hi») de un eje de la rejilla a ``value`` (al arrastrarlo con el ratón).
    Con ``keep_voxel``, el vóxel no cambia: cambia el número de intervalos y el extremo queda a un número entero de
    vóxeles del otro (entre 1 y ``max_bins``); si no, los intervalos son los mismos y cambia el vóxel."""
    a, b = (lo, hi) if lo <= hi else (hi, lo)
    if not math.isfinite(value):
        return _nice6(a), _nice6(b), n
    d = (b - a) / n if n >= 1 else 0.0
    if not keep_voxel or not (d > 0) or not math.isfinite(d):
        # El extremo no pasa del otro: el eje mide al menos lo que pide penhmain (y algo más, para que el redondeo
        # no lo deje en nada).
        gap = max(10 * GRID_MIN_LENGTH * max(n, 1), 1e-9 * max(abs(a), abs(b), 1.0))
        if end == "lo":
            a = min(value, b - gap)
        else:
            b = max(value, a + gap)
        return _nice6(a), _nice6(b), max(n, 1)
    if end == "lo":
        m = int(min(max(round((b - value) / d), 1), max_bins))
        a = b - m * d
    else:
        m = int(min(max(round((value - a) / d), 1), max_bins))
        b = a + m * d
    return _nice6(a), _nice6(b), m


def stretch_radius(r: float, n: int, value: float, max_bins: int = 101, keep_voxel: bool = True
                   ) -> tuple[float, int]:
    """Como :func:`stretch_axis` para el radio (de 0 a R)."""
    if not math.isfinite(value):
        return _nice6(r), n
    d = r / n if n >= 1 else 0.0
    if not keep_voxel or not (d > 0) or not math.isfinite(d):
        return _nice6(max(value, 10 * GRID_MIN_LENGTH * max(n, 1), 1e-9 * max(r, 1.0))), max(n, 1)
    m = int(min(max(round(value / d), 1), max_bins))
    return _nice6(m * d), m


def dose_grid_cells(g: DoseGrid) -> int:
    """Celdas de la rejilla (vóxeles, anillos por capas o capas esféricas)."""
    if g.kind == "box":
        return g.x[2] * g.y[2] * g.z[2]
    if g.kind == "cylinder":
        return g.r[1] * g.z[2]
    if g.kind == "sphere":
        return g.r[1]
    return 0


def set_dose_grid(doc: InDocument, g: DoseGrid) -> None:
    """Escribe las líneas GRIDX/GRIDY/GRIDZ/GRIDR (quita las que no se usan)."""
    if g.kind == "invalid":
        raise ValueError("Esa combinación de líneas GRID no es válida: caja (X, Y, Z), cilindro (Z y R) o esfera "
                         "(solo R).")
    if g.kind == "none" and doc.first("EPROF ") is not None:
        raise ValueError("EPROF (el proceso externo) se registra en la rejilla de dosis y penhmain no la puede quitar "
                         "sin él (3110): quite antes EPROF (panel «Simulación», «Proceso externo»).")
    for key in ("x", "y", "z"):
        t = getattr(g, key)
        if t is not None and not (abs(t[1] - t[0]) >= GRID_MIN_LENGTH):
            raise ValueError(f"GRID{key.upper()}: los dos extremos tienen que estar separados al menos 1e-6 cm.")
    if g.r is not None and not (g.r[0] >= GRID_MIN_LENGTH):
        raise ValueError("GRIDR: el radio tiene que medir al menos 1e-6 cm.")
    for key in ("x", "y", "z"):
        t = getattr(g, key)
        if t is not None and ((key in "xy" and t[2] > 101) or (key == "z" and t[2] > 401)):
            raise ValueError(f"GRID{key.upper()}: como máximo {101 if key in 'xy' else 401} intervalos.")
    if g.r is not None and g.r[1] > 101:
        raise ValueError("GRIDR: como máximo 101 intervalos radiales.")
    for key in ("x", "y", "z"):
        t = getattr(g, key)
        set_value(doc, "GRID" + key.upper(), None if t is None else [float(t[0]), float(t[1]), int(t[2])])
    set_value(doc, "GRIDR", None if g.r is None else [float(g.r[0]), int(g.r[1])])


# ---------------------------------------------------------------------------
# Proceso externo, partículas emergentes, espacio de fases y trabajo
# ---------------------------------------------------------------------------

def external_process(doc: InDocument) -> tuple[str, int, int] | None:
    f = doc.first("EPROF ")
    m = doc.first("EPROPM")
    if f is None:
        return None
    kp, mat = (list(m.values) + [0, 0])[:2] if m is not None else (0, 0)
    return f.text.strip(" "), int(kp), int(mat)


def set_external_process(doc: InDocument, proc: tuple[str, int, int] | None) -> None:
    if proc is None:
        replace_lines(doc, ["EPROF", "EPROPM"], [], anchor="EPROF")
        return
    fname, kp, mat = proc
    replace_lines(doc, ["EPROF", "EPROPM"],
                  [new_line("EPROF", text=fname.strip()), new_line("EPROPM", [int(kp), int(mat)])], anchor="EPROF")


def psf_files(doc: InDocument) -> list[str]:
    return [ln.text.strip(" ") for ln in doc.find("IPSFN ")]


def set_psf_files(doc: InDocument, names: list[str]) -> None:
    if len(names) > 100:
        raise ValueError("penhmain admite como máximo 100 ficheros de espacio de fases.")
    replace_lines(doc, ["IPSFN"], [new_line("IPSFN", text=n.strip()) for n in names if n.strip()], anchor="IPSFN")


def seeds(doc: InDocument) -> list[tuple[int, int]]:
    return [(int(ln.values[0]), int(ln.values[1])) for ln in doc.find("RSEED ") if len(ln.values) >= 2]


def set_seeds(doc: InDocument, pairs: list[tuple[int, int]]) -> None:
    replace_lines(doc, ["RSEED"], [new_line("RSEED", [int(a), int(b)]) for a, b in pairs], anchor="RSEED")


# ---------------------------------------------------------------------------
# Espectro de la fuente
# ---------------------------------------------------------------------------

def spectrum(doc: InDocument) -> list[tuple[float, float]]:
    """Líneas SPECTR (extremo inferior del intervalo y probabilidad; la última, Emax con P<0)."""
    return [(float(ln.values[0]), float(ln.values[1])) for ln in doc.find("SPECTR") if len(ln.values) >= 2]


def energy_lines(doc: InDocument) -> list[tuple[float, float]]:
    return [(float(ln.values[0]), float(ln.values[1])) for ln in doc.find("SLINES") if len(ln.values) >= 2]


def set_spectrum(doc: InDocument, rows: list[tuple[float, float]]) -> None:
    """Espectro continuo (quita SENERG y SLINES, que no pueden ir con él: IRETRN 3004/3006)."""
    if len(rows) > MAX_SPECTRUM:
        raise ValueError(f"penhmain admite como máximo {MAX_SPECTRUM} líneas SPECTR.")
    doc.remove("SENERG")
    doc.remove("SLINES")
    replace_lines(doc, ["SPECTR"], [new_line("SPECTR", [float(e), float(p)]) for e, p in rows], anchor="SPECTR")


def set_energy_lines(doc: InDocument, rows: list[tuple[float, float]]) -> None:
    """Espectro de líneas (quita SENERG y SPECTR)."""
    if len(rows) > MAX_SPECTRUM:
        raise ValueError(f"penhmain admite como máximo {MAX_SPECTRUM} líneas SLINES.")
    doc.remove("SENERG")
    doc.remove("SPECTR")
    replace_lines(doc, ["SLINES"], [new_line("SLINES", [float(e), float(p)]) for e, p in rows], anchor="SLINES")


def sort2(x: list[float], y: list[float]) -> tuple[list[float], list[float]]:
    """SORT2 de penelope.f: ordena la tabla (x, y) por x, como la ordena penhmain.

    Es una ordenación por selección; si dos abscisas coinciden (una
    discontinuidad), las devuelve a su orden de entrada. Las ordenadas
    menores que 1e-75 pasan a valer 1e-75.
    """
    x = [float(v) for v in x]
    y = [float(v) for v in y]
    n = len(x)
    if n <= 1:
        return x, y
    order = list(range(1, n + 1))
    y = [v if v >= 1.0e-75 else 1.0e-75 for v in y]

    def swap(a: int, b: int, with_order: bool = True) -> None:
        x[a], x[b] = x[b], x[a]
        y[a], y[b] = y[b], y[a]
        if with_order:
            order[a], order[b] = order[b], order[a]

    for i in range(n - 1):
        imin, xmin = i, x[i]
        for j in range(i + 1, n):
            if x[j] < xmin:
                imin, xmin = j, x[j]
        swap(i, imin)
        if i == 0:
            continue
        if order[i] < order[i - 1] and abs(x[i] - x[i - 1]) < 1.0e-15:
            swap(i - 1, i)
    i = n - 1
    if order[i] < order[i - 1] and abs(x[i] - x[i - 1]) < 1.0e-15:
        swap(i - 1, i, with_order=False)
    return x, y


@dataclass
class SpectrumInfo:
    """Lo que hará penhmain con un espectro: intervalos ordenados y probabilidades normalizadas."""

    bins: list[tuple[float, float, float]]  # (E inferior, E superior, probabilidad normalizada); líneas: E, E
    emin: float
    emax: float
    mean: float
    problems: list[str]  # avisos (penhmain lo acepta pero probablemente no es lo que se quiere)
    errors: list[str]  # penhmain se detendría
    lines: bool = False

    def density(self) -> list[float]:
        """Probabilidad por unidad de energía (1/eV) de cada intervalo; 0 en los de anchura cero."""
        return [p / (b - a) if b > a else 0.0 for a, b, p in self.bins]


def _fmt_e(v: float) -> str:
    from ..core.numfmt import fmt_energy

    return fmt_energy(v)


def analyze_spectrum(rows: list[tuple[float, float]], lines: bool = False) -> SpectrumInfo:
    """El espectro tal como lo construye PMRDR (penhmain.f 2025).

    Las probabilidades negativas cuentan como 0 y la tabla se ordena por
    energía con SORT2. Espectro continuo (SPECTR): el intervalo i va de E_i a
    E_(i+1) y tiene probabilidad P_i (la energía se muestrea uniforme dentro
    del intervalo); la probabilidad de la energía más alta no se usa (por
    convenio se escribe negativa). Líneas (SLINES): cada energía con su
    probabilidad. IRND0 normaliza las probabilidades.
    """
    problems: list[str] = []
    errors: list[str] = []
    if len(rows) > MAX_SPECTRUM:
        errors.append(f"Hay {len(rows)} líneas y penhmain admite como máximo {MAX_SPECTRUM}.")
    if len(rows) < 2:
        errors.append("Hacen falta al menos dos líneas" + (" (IRETRN 3041)." if lines else
                      ": un intervalo y la energía máxima, con probabilidad negativa (IRETRN 3040)."))
    raw_p = [float(p) for _, p in rows]
    es, ps = sort2([float(e) for e, _ in rows], [max(p, 0.0) for p in raw_p])
    if lines:
        bins = [(e, e, p) for e, p in zip(es, ps)]
    else:
        bins = [(es[i], es[i + 1], ps[i]) for i in range(len(es) - 1)]
    total = sum(b[2] for b in bins)
    real = [b for b in bins if b[2] > 1.0e-70]  # las P <= 0 valen 1e-75 tras SORT2
    if bins and not real:
        problems.append("Todas las probabilidades son cero o negativas: penhmain no se detiene, reparte las "
                        "partículas por igual entre " + ("las líneas." if lines else "los intervalos."))
    if total > 0:
        bins = [(a, b, p / total) for a, b, p in bins]
    if not lines and len(rows) >= 2:
        order = sorted(range(len(rows)), key=lambda i: rows[i][0])
        last = rows[order[-1]]
        if last[1] >= 0 and sum(1 for r in rows if r[0] == last[0]) == 1:
            problems.append(f"La probabilidad de la energía máxima ({_fmt_e(last[0])}) no se usa: por convenio se "
                            "escribe negativa (p. ej. -1).")
        neg = sorted({e for e, p in rows if p < 0 and e != last[0]})
        for e in neg:
            problems.append(f"La línea de {_fmt_e(e)} tiene probabilidad negativa: su intervalo no se muestreará "
                            "(solo la energía máxima debe llevar P < 0).")
        for a, b, p in bins:
            if b == a:
                msg = f"Dos líneas con la misma energía ({_fmt_e(a)}): el intervalo entre ellas tiene anchura cero"
                if p > 1.0e-12:
                    msg += (f" y probabilidad {100 * p:.3g} %: penhmain emitiría esa fracción de las partículas "
                            f"exactamente a {_fmt_e(a)}.")
                else:
                    msg += " (no afecta: su probabilidad es cero)."
                problems.append(msg)
    mean = sum(0.5 * (a + b) * p for a, b, p in bins) if total > 0 else 0.0
    emin = es[0] if es else 0.0
    emax = es[-1] if es else 0.0
    return SpectrumInfo(bins, emin, emax, mean, problems, errors, lines)


def parse_spectrum_text(text: str, unit: float = 1.0,
                        skipped: list[str] | None = None) -> list[tuple[float, float]]:
    """Filas (energía en eV, probabilidad) de un texto pegado o de un fichero de dos columnas.

    Acepta tabuladores, punto y coma, comas o blancos como separadores,
    exponentes de Fortran (1.0D6) y líneas SPECTR/SLINES copiadas de un .in (esas
    ya están en eV). Las líneas que no empiezan por un número (cabeceras,
    comentarios) se saltan. ``unit``: eV por unidad de la tabla.

    Números a la española o a la inglesa: si en el texto hay comas decimales
    («0,25») y ningún punto decimal, los puntos son separadores de miles
    («1.500» = 1500, «2.000,5» = 2000,5); al revés con los números a la inglesa
    («1,500.5»). Si la última fila no tiene probabilidad (celda vacía: la de la
    energía más alta no se usa), se toma -1. Con tabuladores o punto y coma, la
    coma es siempre decimal (una hoja de cálculo en español).

    ``skipped``: si se da, se le añaden las filas que empiezan por un número
    pero cuya probabilidad no se puede leer (no se usan).
    """
    import re
    from decimal import Decimal

    num = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
    thousands_dot = re.compile(r"^[-+]?\d{1,3}(?:\.\d{3})+$")
    thousands_comma = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})+$")
    from .spectrum_file import comma_separated

    raws = text.lstrip("\ufeff").replace("\r", "\n").split("\n")
    # Una hoja de cálculo separa las columnas con tabuladores (o con «;»): entonces una coma es decimal,
    # también en una fila con una sola celda llena («1,00E+08» y la probabilidad vacía).
    tabular = any(";" in r or "\t" in r for r in raws)
    csv = comma_separated(raws)  # un CSV con comas: «20,1» son dos números, no 20,1
    lines = []
    for raw in raws:
        # Solo blancos: un tabulador al final es una celda vacía (la probabilidad de la última fila).
        line = raw.strip(" \n\ufeff")
        if not line.strip():
            continue
        factor = unit
        # Como penhmain: la palabra clave en las columnas 1 a 6 (con blancos delante es un comentario) y los datos
        # desde la 8.
        head = raw.lstrip("\ufeff")[:6]
        if head in ("SPECTR", "SLINES"):
            line = raw.lstrip("\ufeff")[7:127].split("[", 1)[0]
            factor = 1.0
        if ";" in line or "\t" in line:
            toks = [t.strip() for t in re.split(r"[;\t]", line)]
        elif csv and factor != 1.0 or (not tabular and line.count(",") == 1 and " " not in line.strip()
                                       and re.search(r"[.eEdD]", line)):
            toks = [t.strip() for t in line.split(",")]  # «1e6,0.5»: la coma separa
        else:
            toks = [t for t in re.split(r"\s+", line.replace(", ", " ").strip()) if t]
        lines.append((toks, factor))
    # ¿Números a la española (coma decimal) o a la inglesa (punto decimal)?
    dec_comma = dec_point = 0
    for toks, _ in lines:
        for t in toks[:2]:
            if re.fullmatch(r"[-+]?\d*,\d+(?:[eEdD][-+]?\d+)?", t) and not thousands_comma.match(t):
                dec_comma += 1
            elif re.fullmatch(r"[-+]?\d*\.\d+(?:[eEdD][-+]?\d+)?", t) and not thousands_dot.match(t):
                dec_point += 1
    spanish = dec_comma > 0 and dec_point == 0
    english = dec_point > 0 and dec_comma == 0

    def number(tok: str) -> str | None:
        t = tok.strip().replace("D", "E").replace("d", "e")
        if not t:
            return None
        if "," in t and "." in t:
            t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
        elif "," in t:
            t = t.replace(",", "") if english and thousands_comma.match(t) else t.replace(",", ".")
        elif spanish and thousands_dot.match(t):
            t = t.replace(".", "")
        return t if num.match(t) else None

    data = [(toks, factor) for toks, factor in lines if toks and number(toks[0]) is not None]
    rows: list[tuple[float, float]] = []
    for i, (toks, factor) in enumerate(data):
        a = number(toks[0])
        b = number(toks[1]) if len(toks) > 1 else None
        if b is None:
            if i == len(data) - 1 and rows and all(not t.strip() for t in toks[1:2]):
                b = "-1"  # la última fila sin probabilidad: no se usa
            else:
                if skipped is not None:
                    skipped.append("  ".join(t for t in toks if t))
                continue
        rows.append((float(Decimal(a) * Decimal(repr(float(factor)))), float(b)))
    return rows
