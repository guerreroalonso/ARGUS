"""Qué es cada fichero de resultados de penhmain.

Los ficheros se reconocen por su **contenido** (las líneas de comentario que
escribe penhmain), no por su nombre: así se reconocen también los que el usuario
ha renombrado para guardarlos («E100_depth-dose.dat»). El nombre solo decide lo
que el contenido no dice: el tipo de partícula de un .trk y el número de
detector si falta en la cabecera.

Cada tipo (:class:`Kind`) dice de qué familia es, qué contiene, qué palabras
clave del .in lo producen y cómo se muestra.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .data import PARTICLE_FILE_WORDS, PARTICLE_KEYS
from .formats import read_head

FAMILIES: list[tuple[str, str]] = [
    ("resumen", "Resumen"),
    ("dosis", "Dosis"),
    ("tac", "Dosis en el TAC"),
    ("detectores", "Detectores"),
    ("emergentes", "Partículas que salen"),
    ("fuente", "Fuente"),
    ("proceso", "Proceso externo (EPROF)"),
    ("trayectorias", "Trayectorias"),
    ("espacio", "Espacio de fases"),
    ("materiales", "Materiales"),
    ("registro", "Registro"),
]
FAMILY_TITLES = dict(FAMILIES)


@dataclass(frozen=True)
class Kind:
    id: str
    family: str
    title: str
    default_name: str  # nombre que le da penhmain («##» = número de detector)
    shows: str  # 'summary', 'curves', 'grid', 'tracks', 'psf', 'text', 'materials', 'meta'
    keywords: tuple[str, ...] = ()  # palabras clave del .in que lo producen
    description: str = ""
    hidden: bool = False  # datos auxiliares que no se muestran solos (gnu-dose.dat)


def _k(id, family, title, name, shows, keywords=(), description="", hidden=False) -> Kind:
    return Kind(id, family, title, name, shows, tuple(keywords), description, hidden)


KINDS: dict[str, Kind] = {k.id: k for k in [
    _k("res", "resumen", "Resumen de la simulación", "penhmain-res.dat", "summary", (),
       "Lluvias simuladas, tiempo y velocidad; fracciones transmitida, retrodispersada y absorbida; partículas "
       "secundarias; energía y carga depositadas en cada cuerpo; resumen de los detectores y eficiencia."),
    # Dosis (GRIDX/GRIDY/GRIDZ/GRIDR).
    _k("depth-dose", "dosis", "Dosis en profundidad", "depth-dose.dat", "curves", ("GRIDZ",),
       "Energía depositada por unidad de espesor másico, integrada en X e Y dentro de la rejilla de dosis "
       "(eV·cm²/g por partícula). Con un cilindro de dosis (GRIDR), penhmain la integra hasta 101 veces el paso "
       "radial (RU/NBR), más allá de RU si NBR < 101."),
    _k("x-dose", "dosis", "Dosis a lo largo del eje X", "x-dose.dat", "curves", ("GRIDX", "GRIDY", "GRIDZ"),
       "Dosis en los vóxeles del eje X central de la caja (media de los vóxeles centrales)."),
    _k("y-dose", "dosis", "Dosis a lo largo del eje Y", "y-dose.dat", "curves", ("GRIDX", "GRIDY", "GRIDZ"),
       "Dosis en los vóxeles del eje Y central de la caja."),
    _k("z-dose", "dosis", "Dosis a lo largo del eje Z", "z-dose.dat", "curves", ("GRIDZ",),
       "Dosis en el eje Z central (en un mapa cilíndrico, en el primer anillo)."),
    _k("3d-dose-map", "dosis", "Mapa 3D de dosis", "3d-dose-map.dat", "grid", ("GRIDX", "GRIDY", "GRIDZ"),
       "Dosis y energía depositada en cada vóxel de la caja de dosis."),
    _k("2d-dose-map", "dosis", "Mapa de dosis con simetría cilíndrica", "2d-dose-map.dat", "grid", ("GRIDZ", "GRIDR"),
       "Dosis y energía depositada en cada anillo (R, Z) del cilindro de dosis alrededor del eje Z."),
    _k("radial-dose", "dosis", "Dosis radial (simetría esférica)", "radial-dose.dat", "grid", ("GRIDR",),
       "Dosis en cascarones esféricos alrededor del origen y energía por unidad de radio."),
    _k("gnu-dose", "dosis", "Rejilla de dosis (para gnuplot)", "gnu-dose.dat", "meta", ("GRIDX", "GRIDY", "GRIDZ"),
       hidden=True),
    # TAC.
    _k("ct-3d-dose", "tac", "Dosis en los vóxeles del TAC", "ct-3d-dose.dat", "grid", ("GEOMFN",),
       "Dosis media en cada vóxel del TAC (en el sistema del propio TAC)."),
    _k("ct-ddose", "tac", "Dosis en profundidad en el TAC", "ct-ddose.dat", "curves", ("GEOMFN",),
       "Dosis en profundidad a lo largo del eje Z del TAC (eV·cm²/g); su incertidumbre está subestimada."),
    _k("ct-dvh", "tac", "Histograma dosis-volumen del TAC", "ct-dvh.dat", "curves", ("GEOMFN",),
       "Histogramas dosis-volumen diferencial y acumulado de todo el TAC."),
    _k("ct-x-dose", "tac", "Dosis en el eje X del TAC", "ct-x-dose.dat", "curves", ("GEOMFN",)),
    _k("ct-y-dose", "tac", "Dosis en el eje Y del TAC", "ct-y-dose.dat", "curves", ("GEOMFN",)),
    _k("ct-z-dose", "tac", "Dosis en el eje Z del TAC", "ct-z-dose.dat", "curves", ("GEOMFN",)),
    _k("gnu-ct-dose", "tac", "Tamaño del TAC (para gnuplot)", "gnu-ct-dose.dat", "meta", ("GEOMFN",), hidden=True),
    # Detectores.
    _k("spc-impdet", "detectores", "Espectro del detector de impacto", "spc-impdet-##.dat", "curves",
       ("IMPDET", "IDSPC"), "Espectro en energía de las partículas que entran en el detector, total y por tipo "
       "de partícula (1/(eV·partícula))."),
    _k("fln-impdet", "detectores", "Fluencia en el detector de impacto", "fln-impdet-##.dat", "curves",
       ("IMPDET", "IDFLNC"), "Distribución en energía de la fluencia integrada en el volumen del detector "
       "(cm/(eV·partícula)); solo con IDCUT 2."),
    _k("age-impdet", "detectores", "Edad de las partículas del detector de impacto", "age-impdet-##.dat", "curves",
       ("IDAGEL", "IDAGEF"), "Distribución del tiempo transcurrido desde el inicio de la lluvia (1/s)."),
    _k("spc-angdet", "detectores", "Espectro del detector angular", "spc-angdet-##.dat", "curves",
       ("ANGDET", "ADSPC"), "Espectro de las partículas que salen dentro de la ventana angular (1/(eV·sr·partícula))."),
    _k("spc-enddet", "detectores", "Espectro de energía depositada", "spc-enddet-##.dat", "curves",
       ("ENDETC", "EDSPC"), "Distribución de la energía depositada por lluvia en el detector (1/(eV·partícula))."),
    _k("psf", "espacio", "Espacio de fases", "psf-impdet.dat", "psf", ("IMPDET", "IDPSF"),
       "Estado de cada partícula que entra en el detector: tipo, energía, posición, dirección y peso."),
    # Partículas que salen.
    _k("energy-up", "emergentes", "Energía de las partículas que salen hacia arriba", "energy-up.dat", "curves",
       ("NBE",), "Distribución en energía de las partículas que salen con W > 0, por tipo (1/(eV·partícula))."),
    _k("energy-down", "emergentes", "Energía de las partículas que salen hacia abajo", "energy-down.dat", "curves",
       ("NBE",), "Distribución en energía de las partículas que salen con W < 0, por tipo (1/(eV·partícula))."),
    _k("angle", "emergentes", "Ángulos (θ, φ) de las partículas que salen", "angle.dat", "curves", ("NBANGL",),
       "Distribución angular por tipo de partícula (1/(sr·partícula)); solo si hay más de un intervalo de φ."),
    _k("polar-angle", "emergentes", "Ángulo polar de las partículas que salen", "polar-angle-*.dat", "curves",
       ("NBANGL",), "Distribución en el ángulo polar (1/sr) y energía media en cada intervalo."),
    # Fuente.
    _k("psource", "fuente", "Espectro de la fuente", "psource.dat", "curves", ("SPECTR", "SLINES"),
       "Espectro definido en el .in y límites del muestreado (3 sigma)."),
    _k("usource", "fuente", "Partículas emitidas por la fuente radiactiva", "usource.dat", "curves", ("SRNUCL",),
       "Espectros de las partículas que emite el nucleido, por desintegración."),
    # EPROF.
    _k("p3d-depth", "proceso", "Proceso externo en profundidad", "P3D-depth.dat", "curves", ("EPROF",)),
    _k("p3d-map", "proceso", "Mapa 3D del proceso externo", "P3D-map.dat", "grid", ("EPROF", "GRIDX")),
    _k("p3d-x", "proceso", "Proceso externo en el eje X", "P3D-x.dat", "curves", ("EPROF",)),
    _k("p3d-y", "proceso", "Proceso externo en el eje Y", "P3D-y.dat", "curves", ("EPROF",)),
    _k("p3d-z", "proceso", "Proceso externo en el eje Z", "P3D-z.dat", "curves", ("EPROF",)),
    _k("p3d-map-cyl", "proceso", "Mapa cilíndrico del proceso externo", "P3D-map-cyl.dat", "grid", ("EPROF", "GRIDR")),
    _k("p3d-sphere", "proceso", "Proceso externo radial", "P3D-sphere.dat", "grid", ("EPROF", "GRIDR")),
    _k("pmap-input", "proceso", "Tabla de EPROF interpolada", "PMAP-input.dat", "text", ("EPROF",)),
    # Trayectorias.
    _k("trk", "trayectorias", "Trayectorias", "*.trk", "tracks", ("LTRACK",),
       "Puntos (x, y, z, E) de las trayectorias de las primeras lluvias, un fichero por tipo de partícula."),
    # Materiales.
    _k("material", "materiales", "Datos de los materiales", "material.dat", "materials", ("MFNAME",),
       "Poderes de frenado, alcances, recorridos libres medios y coeficientes de atenuación que calcula PENELOPE "
       "(con un solo material)."),
    _k("energy-equivalence", "materiales", "Equivalencias de energía de productos ligeros",
       "energy-equivalence-*.dat", "curves", (),
       "Energía de un protón (o alfa) con el mismo alcance que un producto ligero."),
    # Registro.
    _k("log", "registro", "Registro de penhmain", "penhmain.dat", "text", (),
       "Lo que ha leído penhmain y los mensajes durante la simulación; si se ha parado, dice por qué."),
    _k("run-log", "registro", "Salida en pantalla", "run.log", "text", (),
       "Lo que penhmain escribe en pantalla: partículas simuladas en cada volcado y «*** END ***» al acabar."),
    _k("geometry-rep", "registro", "Informe de la geometría", "geometry.rep", "text", ("GEOMFN",)),
    _k("pennuc", "registro", "Datos del nucleido (PENNUC)", "pennuc.dat", "text", ("SRNUCL",)),
]}

# Extensiones que nunca son resultados (se saltan sin leerlas).
NOT_RESULTS = {".mat", ".endf", ".geo", ".in", ".ct", ".nuc", ".p08", ".p11", ".p14", ".p18", ".p23", ".pen", ".gnu",
               ".pdf", ".png", ".jpg", ".svg", ".bak", ".exe", ".x", ".o", ".obj", ".mod", ".f", ".f90", ".for",
               ".py", ".sh", ".bat", ".ps1", ".json", ".zip", ".gz", ".tar", ".7z", ".dmp", ".mcs", ".tab", ".xlsx",
               ".csv", ".dll", ".so", ".dylib", ".dcm", ".ini", ".tex", ".docx"}
CANDIDATE_EXT = {".dat", ".trk", ".txt", ".log", ".rep", ".out", ""}
MAX_TEXT_SIZE = 50 * 1024 * 1024  # un .log o .txt enorme no es de penhmain


@dataclass(frozen=True)
class Identified:
    kind: str
    detector: int | None = None
    particle: int | None = None
    variant: str = ""  # 'fluence'/'probability' (EPROF), 'lines'/'histogram' (psource)…

    @property
    def info(self) -> Kind:
        return KINDS[self.kind]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _detector_number(head: str, name: str) -> int | None:
    m = re.search(r"detector\s*(?:#|no\.)\s*(\d+)", head, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"-(\d{1,2})\.dat$", name)
    return int(m.group(1)) if m else None


def _particle_from_name(name: str) -> int | None:
    low = name.lower()
    for word, kp in PARTICLE_FILE_WORDS.items():
        if word in low:
            return kp
    return None


def identify_text(head: str, name: str = "") -> Identified | None:
    """Tipo de un fichero a partir de su principio (``head``) y de su nombre."""
    h = _norm(head[:6000])
    low = name.lower()
    if "program penhmain. results." in h:
        return Identified("res")
    if "program penhmain. input data and run-time messages" in h:
        return Identified("log")
    if "results from ctdose." in h:
        if "3d dose distribution" in h:
            return Identified("ct-3d-dose")
        if "depth-dose distribution" in h:
            return Identified("ct-ddose")
        if "dose-volume histograms" in h:
            return Identified("ct-dvh")
        for ax in "xyz":
            if f"central {ax} axis" in h:
                return Identified(f"ct-{ax}-dose")
        return None
    penhmain = re.search(r"results from pen(?:h)?main", h) is not None  # penhmain-sum escribe a veces «PENMAIN»
    if "results from sp3d." in h or (penhmain and "map cylinder" in h and "dose-map cylinder" not in h):
        variant = "fluence" if "fluence" in h and "probability density" not in h else "probability"
        if "map cylinder" in h:
            return Identified("p3d-map-cyl", variant=variant)
        if "map sphere" in h:
            return Identified("p3d-sphere", variant=variant)
        if "map box" in h:
            return Identified("p3d-map", variant=variant)
        if "depth distribution" in h:
            return Identified("p3d-depth", variant=variant)
        for ax in "xyz":
            if f"central {ax} axis" in h:
                return Identified(f"p3d-{ax}", variant=variant)
        return None
    if penhmain:
        if "phase-space file of detector" in h:
            return Identified("psf", detector=_detector_number(head, low))
        if "output from impact detector" in h:
            det = _detector_number(head, low)
            if "fluences integrated over the volume" in h:
                return Identified("fln-impdet", detector=det)
            if "age distribution" in h:
                return Identified("age-impdet", detector=det)
            return Identified("spc-impdet", detector=det)
        if "output from angular detector" in h:
            return Identified("spc-angdet", detector=_detector_number(head, low))
        if "output from energy-deposition detector" in h:
            return Identified("spc-enddet", detector=_detector_number(head, low))
        if "depth-dose distribution" in h:
            return Identified("depth-dose")
        if "3d dose distribution" in h:
            return Identified("3d-dose-map")
        if "dose-map cylinder" in h:
            return Identified("2d-dose-map")
        if "dose-map sphere" in h:
            return Identified("radial-dose")
        for ax in "xyz":
            if f"dose distribution along the central {ax} axis" in h:
                return Identified(f"{ax}-dose")
        if "energy distributions of upbound particles" in h:
            return Identified("energy-up")
        if "energy distributions of downbound particles" in h:
            return Identified("energy-down")
        if "angular distributions of emerging particles" in h:
            return Identified("angle")
        m = re.search(r"angular distribution of emerging (electrons|photons|positrons|protons|alpha particles|"
                      r"neutrons)", h)
        if m:
            kp = {"electrons": 1, "photons": 2, "positrons": 3, "protons": 4, "alpha particles": 5,
                  "neutrons": 6}[m.group(1)]
            return Identified("polar-angle", particle=kp)
        if "kparp=0, subroutine source" in h:
            return Identified("usource")
        if "source energy spectrum" in h:
            return Identified("psource", variant="lines" if "discrete lines" in h else "histogram")
        return None
    if re.match(r"\s*#\s*x/cm\s+y/cm\s+z/cm\s+e/ev", head, re.I):
        return Identified("trk", particle=_particle_from_name(low))
    if "energy of a proton having the same range" in h or "energy of an alpha having the same range" in h:
        return Identified("energy-equivalence", particle=4 if "of a proton" in h else 5)
    if re.search(r"penelope\s+\(version", h) and ("material" in h):
        return Identified("material")
    if re.match(r"\s*xl=", h) and "dz=" in h:
        return Identified("gnu-dose")
    if re.match(r"\s*xm=", h) and "dz=" in h:
        return Identified("gnu-ct-dose")
    if "verification of the pmap interpolation" in h:
        return Identified("pmap-input")
    if "radioisotope number" in h and "parent nuclide" in h:
        return Identified("pennuc")
    if "reading the dump file" in h and ("number of independent runs" in h or "number of simulated showers" in h):
        return Identified("run-log", variant="sum")
    if ("no. of simulated showers" in h or "the simulation is started" in h or "initialising pengeom" in h
            or "initialising penelope" in h) and len(head) < 200000:
        return Identified("run-log")
    if low.endswith(".rep") and "0000000000" in head and "surface" in h:
        return Identified("geometry-rep")
    return None


def identify(path: str | os.PathLike) -> Identified | None:
    """Tipo de un fichero de resultados (None si no lo es)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in NOT_RESULTS or (ext not in CANDIDATE_EXT and not ext.startswith(".dat")):
        return None
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if size == 0 or (ext in (".log", ".txt", ".out", "") and size > MAX_TEXT_SIZE):
        return None
    head = read_head(p)
    if "\x00" in head[:2048]:
        return None  # binario
    return identify_text(head, p.name)


def particle_from_polar_name(name: str) -> int | None:
    m = re.search(r"polar-angle-([a-z]+)\.dat$", name.lower())
    return PARTICLE_KEYS.get(m.group(1)) if m else None


__all__ = ["CANDIDATE_EXT", "FAMILIES", "FAMILY_TITLES", "Identified", "KINDS", "Kind", "NOT_RESULTS", "identify",
           "identify_text", "particle_from_polar_name"]
