"""El resumen de la simulación: ``penhmain-res.dat``.

Lo escribe PMWRT (penhmain.f) al acabar y en cada volcado (DUMPP): lluvias,
tiempos y velocidad; fracciones de primarias que salen hacia arriba, hacia
abajo o se absorben; probabilidades de generar secundarias; número medio de
interacciones; recorrido medio, energía final y ángulo de salida de las
primarias; energía y carga depositadas en cada cuerpo; resúmenes de los
detectores y la eficiencia de la dosis en el vóxel con más dosis. Las
incertidumbres «+-» que escribe penhmain son de 3 sigma; aquí se guardan de 1.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .formats import NUMBER, parse_float, read_text

PRIMARY_KPAR = {"electrons": 1, "photons": 2, "positrons": 3, "protons": 4, "alphas": 5, "neutrons": 6,
                "set by subroutine source": 0}
WHERE = {"upbound": "up", "downbound": "down", "absorbed": "abs"}
WHERE_TITLES = {"up": "salen hacia arriba", "down": "salen hacia abajo", "abs": "se absorben"}

_LINE = re.compile(
    r"^\s*(?P<label>[^.|]+?)\s*\.{2,}\s*(?P<value>" + NUMBER + r")"
    r"(?:\s*\+-\s*(?P<err>" + NUMBER + r"))?\s*(?P<unit>[A-Za-z/*]+)?"
    r"(?:\s*\(effic\.\s*=\s*(?P<eff>" + NUMBER + r")\))?")


@dataclass
class Measure:
    value: float
    err: float = 0.0  # 1 sigma
    unit: str = ""
    effic: float | None = None  # eficiencia de penhmain: 1/(varianza relativa · tiempo)

    @property
    def rel(self) -> float:
        return self.err / abs(self.value) if self.value else 0.0


@dataclass
class Summary:
    text: str = ""
    date: str = ""
    title: str = ""
    primary: str = ""  # lo que escribe penhmain («photons», «set by subroutine SOURCE»…)
    kparp: int | None = None  # 0: fuente radiactiva; None: partículas de espacios de fases
    from_psf: bool = False
    showers: float = 0.0
    time_total: float = 0.0  # tiempo de cálculo (incluye la inicialización)
    time_cpu: float = 0.0  # tiempo de CPU de la simulación
    speed: float = 0.0  # lluvias/s
    primaries: dict[str, float] = field(default_factory=dict)  # 'up', 'down', 'abs'
    fractions: dict[str, Measure] = field(default_factory=dict)
    secondaries: dict[tuple[int, str], Measure] = field(default_factory=dict)  # (KPAR, 'up'/'down'/'abs')
    events: dict[str, dict[str, Measure]] = field(default_factory=dict)  # dónde -> interacción -> número medio
    expected: dict[str, float] = field(default_factory=dict)
    track_length: dict[str, Measure] = field(default_factory=dict)
    final_energy: dict[str, Measure] = field(default_factory=dict)
    polar_cosine: dict[str, Measure] = field(default_factory=dict)
    polar_angle: dict[str, Measure] = field(default_factory=dict)
    body_energy: dict[int, Measure] = field(default_factory=dict)
    body_charge: dict[int, Measure] = field(default_factory=dict)
    impact_energy: dict[int, Measure] = field(default_factory=dict)
    impact_area: dict[int, Measure] = field(default_factory=dict)
    angular_energy: dict[int, Measure] = field(default_factory=dict)
    enddet_energy: dict[int, Measure] = field(default_factory=dict)
    enddet_area: dict[int, Measure] = field(default_factory=dict)
    dose_efficiency: float | None = None
    dose_intrinsic: float | None = None
    dose_uncertainty_pct: float | None = None  # incertidumbre (1 sigma, %) del vóxel con más dosis
    last_seeds: tuple[int, int] | None = None
    complete: bool = False  # el fichero acaba en la línea de guiones (penhmain terminó de escribirlo)

    @property
    def per_decay(self) -> bool:
        return self.kparp == 0

    @property
    def primary_name(self) -> str:
        from .data import PARTICLE_NAMES

        if self.from_psf:
            return "partículas de espacios de fases"
        if self.kparp == 0:
            return "desintegraciones (fuente radiactiva)"
        return PARTICLE_NAMES.get(self.kparp or -1, self.primary or "?")


_SECTIONS = [
    ("mean number of events per primary track", "events"),
    ("expected numbers of collisions", "expected"),
    ("average track length in material bodies", "track"),
    ("average final energy", "final"),
    ("mean value of the polar cosine", "cosine"),
    ("mean value of the polar angle", "angle"),
    ("average deposited energies (bodies)", "body_e"),
    ("average deposited charges (bodies", "body_q"),
    ("average incoming energies (impact detectors)", "imp_e"),
    ("spectral areas (impact detectors)", "imp_a"),
    ("average emerging energies (angular detectors)", "ang_e"),
    ("average deposited energies (energy detectors)", "end_e"),
    ("spectral areas (energy-deposition detectors)", "end_a"),
    ("secondary-particle generation probabilities", "secondary"),
]


def _where(label: str) -> str | None:
    low = label.lower()
    for k, v in WHERE.items():
        if low.startswith(k):
            return v
    return None


def parse_summary(text: str) -> Summary:
    s = Summary(text=text)
    section = ""
    sub = ""  # dónde (up/down/abs) dentro de «events» y «expected»
    title_next = False
    sec_particles: list[int] = []
    sec_row: str | None = None
    lines = text.splitlines()
    for raw in lines:
        line = raw.strip()
        low = line.lower()
        if not line:
            continue
        if title_next:
            s.title = line
            title_next = False
            continue
        if low.startswith("date and time:"):
            s.date = line.split(":", 1)[1].strip()
            title_next = True
            continue
        if low.startswith("particles read from phase-space files"):
            s.from_psf = True
            continue
        if low.startswith("primary particles:"):
            s.primary = line.split(":", 1)[1].strip()
            s.kparp = PRIMARY_KPAR.get(s.primary.lower())
            continue
        if low.startswith("last random seeds"):
            nums = re.findall(r"-?\d+", line)
            if len(nums) >= 2:
                s.last_seeds = (int(nums[0]), int(nums[1]))
            section = "end"
            continue
        if section == "end" and set(line) == {"-"}:
            s.complete = True
            continue
        new_section = next((key for pat, key in _SECTIONS if low.startswith(pat) or pat in low), None)
        if new_section:
            section, sub = new_section, ""
            if section == "secondary":
                sec_particles = []
            continue
        if low.startswith("** "):
            w = _where(low[3:])
            if w:
                sub = w
            continue
        if section == "secondary" and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            words = [c.lower() for c in cells]
            if all(w in ("electrons", "photons", "positrons", "protons", "alphas", "neutrons") for w in words):
                sec_particles = [PRIMARY_KPAR[w] for w in words]
                continue
            if cells and _where(cells[0]):
                sec_row = _where(cells[0])
                for kp, c in zip(sec_particles, cells[1:]):
                    s.secondaries[(kp, sec_row)] = Measure(parse_float(c))
                continue
            if cells and not cells[0] and sec_row:
                for kp, c in zip(sec_particles, cells[1:]):
                    m = re.search(NUMBER, c.replace("+-", ""))
                    if m and (kp, sec_row) in s.secondaries:
                        s.secondaries[(kp, sec_row)].err = abs(parse_float(m.group(0))) / 3.0
                sec_row = None
            continue
        if section == "expected" and low.startswith("e0, range"):
            nums = re.findall(NUMBER, line.split("=", 1)[1])
            if len(nums) >= 2:
                s.expected["E0"] = parse_float(nums[0])
                s.expected["range"] = parse_float(nums[1])
            continue
        m = _LINE.match(line)
        if not m:
            continue
        label = m.group("label").strip()
        lab = label.lower()
        meas = Measure(parse_float(m.group("value")),
                       abs(parse_float(m.group("err"))) / 3.0 if m.group("err") else 0.0,
                       (m.group("unit") or "").strip(),
                       parse_float(m.group("eff")) if m.group("eff") else None)
        if lab.startswith("simulated primary particles") or lab.startswith("number of simulated showers"):
            s.showers = meas.value
        elif lab.startswith("calculation time"):
            s.time_total = meas.value
        elif lab.startswith("effective (cpu) simulation time"):
            s.time_cpu = meas.value
        elif lab.startswith("simulation speed"):
            s.speed = meas.value
        elif lab.endswith("primary particles") and not section and not m.group("err"):
            w = _where(lab)
            if w:
                s.primaries[w] = meas.value
        elif lab.endswith("fraction") and not section:
            key = {"upbound fraction": "up", "downbound fraction": "down", "absorption fraction": "abs"}.get(lab)
            if key:
                s.fractions[key] = meas
        elif lab.startswith("efficiency (highest dose)"):
            s.dose_efficiency = meas.value
        elif lab.startswith("intrinsic"):
            s.dose_intrinsic = meas.value
        elif lab.startswith("percent uncertainty"):
            s.dose_uncertainty_pct = meas.value
        elif section == "events" and sub:
            s.events.setdefault(sub, {})[label] = meas
        elif section == "expected":
            s.expected[label] = meas.value
        elif section in ("track", "final", "cosine", "angle"):
            w = _where(lab)
            if w:
                target = {"track": s.track_length, "final": s.final_energy, "cosine": s.polar_cosine,
                          "angle": s.polar_angle}[section]
                target[w] = meas
        elif section in ("body_e", "body_q"):
            mb = re.match(r"body\s+(\d+)", lab)
            if mb:
                (s.body_energy if section == "body_e" else s.body_charge)[int(mb.group(1))] = meas
        elif section in ("imp_e", "imp_a", "ang_e", "end_e", "end_a"):
            md = re.match(r"detector\s*#\s*(\d+)", lab)
            if md:
                target = {"imp_e": s.impact_energy, "imp_a": s.impact_area, "ang_e": s.angular_energy,
                          "end_e": s.enddet_energy, "end_a": s.enddet_area}[section]
                target[int(md.group(1))] = meas
    return s


def read_summary(path: str | os.PathLike) -> Summary:
    return parse_summary(read_text(Path(path)))


def summary_complete(path: str | os.PathLike) -> bool:
    """¿Acaba el resumen como lo cierra penhmain (las últimas semillas y una línea de guiones)? Mientras penhmain
    vuelca sus resultados, el resumen es lo último que cierra (PMWRT): a medias, los demás también pueden estarlo."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(size - 2048, 0))
            tail = fh.read().decode("latin-1").lower()
    except OSError:
        return False
    i = tail.rfind("last random seeds")
    return i >= 0 and any(len(ln.strip()) >= 10 and set(ln.strip()) == {"-"} for ln in tail[i:].splitlines()[1:])


__all__ = ["Measure", "PRIMARY_KPAR", "Summary", "WHERE_TITLES", "parse_summary", "read_summary", "summary_complete"]
