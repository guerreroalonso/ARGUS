"""Importar un TAC: rejilla, calibración, fichero .ct, su ficha y los cambios en el .geo y el .in.

El .ct va en la carpeta del .geo (PENGEOM lo busca allí) con un nombre de
12 caracteres como mucho. Junto a él se escribe ``<nombre>.ct.json``, la
**ficha** de la importación: de qué serie viene (sin datos del paciente), el
recorte, el tamaño de vóxel, la colocación y la calibración con la
composición de cada material. ARGUS la usa para ``materiales.txt`` (cómo
generar cada .mat con penhmat) y para volver a importar con los mismos ajustes.

En la geometría se añade un elemento CTBOX con su transformación final (el
CT no se puede mover después). Puede ir en el nivel superior, dentro de un
módulo o dentro de un cuerpo (como módulo limitante), siempre que ni ese
contenedor ni los que lo contienen tengan giro o desplazamiento propio (con
los PARINP del .in) ni se clonen (un clon copiaría la caja con MATERIAL(9999)).

Los ficheros se escriben primero con nombres temporales y después se
renombran (:meth:`CtImport.write_temp` y :func:`commit_written`): si se
cancela o algo falla, no queda nada a medias.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .. import __version__
from ..pengeom.compiler import body_title
from ..pengeom.ct import MAX_CT_MATERIAL, NDD, CtData, CtFrame, WriteCancelled, write_ct_file
from ..pengeom.edit import (
    EditError,
    PlacementCheck,
    _insert_before,
    _lab,
    _mother_module_element,
    editable_body,
    euler_angles,
    is_top_level,
    local_frame,
    new_label,
    set_description,
    thin_message,
    top_level,
)
from ..pengeom.model import CloneElement, CtBoxElement, ElementKind, ParamLine, RefLine
from . import Cancelled
from .calibration import MNIS, Calibration, Tissue, excitation_energy, penhmat_composition
from .dicom import HuVolume
from .grid import GridSpec, placement, resample

CT_NAME_MAX = 12  # CTFNAME=(...) de PENGEOM
_CT_NAME = re.compile(r"^[A-Za-z0-9._+\-]+$")
SIDECAR_SUFFIX = ".json"


def ct_name_problem(name: str) -> str | None:
    """Por qué no vale un nombre de fichero .ct (None si vale)."""
    if not name:
        return "El nombre del fichero .ct está vacío."
    if len(name) > CT_NAME_MAX:
        return f"«{name}» tiene {len(name)} caracteres; PENGEOM solo lee {CT_NAME_MAX} (CTFNAME)."
    if not _CT_NAME.match(name):
        return f"«{name}» tiene espacios, acentos u otros caracteres que no conviene usar."
    return None


def sidecar_path(ct_path: str | Path) -> Path:
    p = Path(ct_path)
    return p.with_name(p.name + SIDECAR_SUFFIX)


def read_sidecar(ct_path: str | Path) -> dict | None:
    """Ficha de un .ct importado por ARGUS (None si no hay o no se puede leer)."""
    p = sidecar_path(ct_path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


IN_MATERIAL = "material_del_in"  # ficha: el tejido usa un material que el .in ya tenía (sigue siendo el del .in)


def sidecar_tissues(data: dict | None, *, reused: bool = False) -> dict[int, Tissue]:
    """Material del .in -> tejido (o material de un tejido partido por densidad), según la ficha.

    Las fichas de ARGUS 0.9 tienen la lista «materiales» (un material por trozo de cada tejido); las anteriores,
    solo los tejidos de la calibración.

    Un tejido que usa un material que el .in ya tenía (el combo «Material del .in», o un .mat con el mismo nombre
    que una línea MFNAME: p. ej. el water.mat de un maniquí o el air.mat de la sala) lleva ``material_del_in`` en la
    ficha (desde 0.16.3): ese material sigue siendo el del .in, no se genera ni se comprueba como el tejido, y aquí
    no sale. Con ``reused=True`` salen solo esos. Las fichas anteriores no lo distinguen: todos son tejidos.
    """
    out: dict[int, Tissue] = {}
    if not isinstance(data, dict):
        return out
    entries = data.get("materiales")
    if not isinstance(entries, list):
        cal = data.get("calibracion")
        entries = cal.get("tejidos") if isinstance(cal, dict) else None
        if not isinstance(entries, list):
            return out
    for t in entries:
        if not isinstance(t, dict) or bool(t.get(IN_MATERIAL)) != reused:
            continue
        n = t.get("material")
        if isinstance(n, int) and n > 0:
            try:
                out[n] = Tissue.from_dict(t)
            except (KeyError, TypeError, ValueError):
                continue
    return out


def sidecar_subdivision(data: dict | None) -> dict | None:
    """Cómo se partieron los tejidos por densidad al importar (None en fichas anteriores a ARGUS 0.9)."""
    sub = data.get("subdivision") if isinstance(data, dict) else None
    return sub if isinstance(sub, dict) else None


def sidecar_families(data: dict | None) -> dict[int, str]:
    """Material del .in -> tejido del que sale (para los tejidos partidos por densidad; el resto, su nombre)."""
    return {n: (t.parent or t.name) for n, t in sidecar_tissues(data).items()}


# ---------------------------------------------------------------------------
# Tejidos partidos por densidad (penhmain 2025, CTTRACK: ADR-010 y ADR-011)
# ---------------------------------------------------------------------------

SPLIT_MODES = ("auto", "none", "max")  # partir: lo recomendado, nada, todos los materiales que quepan
MAXMAT = 10  # materiales de PENELOPE (penelope.f)


def sub_material_name(mat_file: str, i: int, taken: set[str]) -> str:
    """Nombre del .mat del trozo ``i`` (1, 2...) de un tejido: ``ct_pulmon_2.mat``; 20 caracteres como mucho
    (penhmain los lee con A20) y distinto de los de ``taken`` (en minúsculas; se añade)."""
    stem = mat_file[:-4] if mat_file.lower().endswith(".mat") else mat_file
    suffix = f"_{i}.mat"
    base = stem[: 20 - len(suffix)]
    name = base + suffix
    extra = 0
    while name.lower() in taken:
        extra += 1
        tag = chr(ord("a") + (extra - 1) % 26) * (1 + (extra - 1) // 26)
        name = base[: 20 - len(suffix) - len(tag)] + tag + suffix
    taken.add(name.lower())
    return name


def split_tissues(tissues: list[Tissue], cuts: dict[int, tuple[float, ...]], taken: set[str]
                  ) -> tuple[list[Tissue], list[int], list[int]]:
    """Materiales de los tejidos con sus cortes de densidad: (materiales, tejido de cada uno, primer material de
    cada tejido). Los trozos de un tejido tienen su composición (la de la lista de PENELOPE si es de ella) y el
    nombre «Pulmón 2/3»."""
    out: list[Tissue] = []
    parent_of: list[int] = []
    first: list[int] = []
    for i, t in enumerate(tissues):
        first.append(len(out))
        c = tuple(cuts.get(i) or ())
        if not c:
            out.append(t)
            parent_of.append(i)
            continue
        k = len(c) + 1
        edges: list[float | None] = [None, *[float(v) for v in c], None]
        comp = penhmat_composition(t)
        for j in range(k):
            lo, hi = edges[j], edges[j + 1]
            rng = (f"{'' if lo is None else format(lo, '.4g')}–{'' if hi is None else format(hi, '.4g')} g/cm³"
                   .replace(".", ","))
            out.append(replace(t, name=f"{t.name} {j + 1}/{k}", mat_file=sub_material_name(t.mat_file, j + 1, taken),
                               composition=comp, source=f"{t.source}; densidades {rng}".lstrip("; "),
                               voxel_density=None, parent=t.name, density_cut=(lo, hi)))
            parent_of.append(i)
    return out, parent_of, first


def _mm(v) -> str:
    return f"{float(v):.1f}".replace(".", ",")


# Por qué un tejido tiene los trozos que tiene (ficha, «limites»; ver :data:`argus.ct.subdivide.LIMIT_VOID`...).
WHY_UNNEEDED = ("homogéneo", "pocos valores", "trozo pequeño")  # no hace falta partirlo (más)
WHY_IN = "material del .in"  # usa un material que ya tiene el .in
WHY_SHARED = "comparte .mat"  # otro tejido usa el mismo .mat
WHY_UNKNOWN = "sin composición"  # penhmat necesita la composición para crear los trozos
WHY_BUDGET = "presupuesto"  # no quedan materiales libres en el .in (MAXMAT)
WHY_GAIN = "poca mejora"  # «Automático»: un trozo más apenas reduce el error
WHY_BLOCKED = (WHY_IN, WHY_SHARED, WHY_UNKNOWN, "bloqueado")
_WHY_TEXT = {
    WHY_IN: "usa un material que ya tiene el .in (quite esa línea MFNAME del .in o cambie el nombre del .mat del "
            "tejido y vuelva a importar)",
    WHY_SHARED: "comparte su .mat con otro tejido (dé a cada tejido su propio .mat y vuelva a importar)",
    WHY_UNKNOWN: "no tiene composición conocida (penhmat la necesita para crear sus trozos; désela en la "
                 "calibración y vuelva a importar)",
    "bloqueado": "no se puede partir",
    WHY_BUDGET: f"no quedan materiales libres en el .in (PENELOPE admite {MAXMAT}; quite los que no use y vuelva a "
                "importar)",
}


@dataclass(frozen=True)
class SplitState:
    """Cómo quedaron los tejidos de un TAC respecto al problema de CTTRACK, según su ficha."""

    mode: str | None  # «modo» de la ficha; None: TAC importado antes de ARGUS 0.9 o sin ficha
    pieces: dict[str, int]  # tejidos partidos: trozos
    unneeded: tuple[str, ...]  # tejidos que no hace falta partir
    blocked: dict[str, str]  # tejidos que haría falta partir y no se han partido: por qué (WHY_*)
    error: str | None  # el análisis falló
    sub: dict | None

    @property
    def analysed(self) -> bool:
        return self.mode in ("auto", "max") and not self.error and self.sub is not None and "trozos" in self.sub

    @property
    def ok(self) -> bool:
        """¿Está partido todo lo que hace falta partir? (entonces el aviso es informativo)."""
        return self.analysed and not self.blocked

    def blocked_text(self) -> str:
        return "; ".join(f"{name} {_WHY_TEXT.get(why, why)}" for name, why in self.blocked.items())


def split_state(sub: dict | None) -> SplitState:
    """:class:`SplitState` de la «subdivision» de una ficha (:func:`sidecar_subdivision`)."""
    if not isinstance(sub, dict):
        return SplitState(None, {}, (), {}, None, None)
    mode = sub.get("modo") if isinstance(sub.get("modo"), str) else None
    err = sub.get("error")
    pieces_all = sub.get("trozos") if isinstance(sub.get("trozos"), dict) else {}
    limits = sub.get("limites") if isinstance(sub.get("limites"), dict) else {}
    pieces: dict[str, int] = {}
    unneeded: list[str] = []
    blocked: dict[str, str] = {}
    for name, k in pieces_all.items():
        try:
            k = int(k)
        except (TypeError, ValueError):
            continue
        why = str(limits.get(name, ""))
        if k >= 2:
            pieces[str(name)] = k
        elif k == 1:
            if why in WHY_BLOCKED or why == WHY_BUDGET:
                blocked[str(name)] = why
            elif why in WHY_UNNEEDED or why == WHY_GAIN:
                unneeded.append(str(name))
    return SplitState(mode, pieces, tuple(unneeded), blocked, str(err) if err else None, sub)


def _error_text(err, split: bool) -> str:
    """« Error de alcance estimado…» de la ficha, o "" si no está o no se entiende."""
    try:
        e0, e1 = err["sin_partir"], err["elegido"]
        text = (f" Error de alcance estimado sin corregir penhmain: mediana {_mm(e1['p50'])} mm y p95 "
                f"{_mm(e1['p95'])} mm")
        return text + (f" (sin partir: {_mm(e0['p50'])} y {_mm(e0['p95'])} mm)." if split else ".")
    except (KeyError, TypeError, ValueError):
        return ""


def split_summary(sub: dict | None) -> str:
    """Frase (con un espacio delante) sobre cómo se partieron los tejidos por densidad al importar, o ""."""
    st = split_state(sub)
    if st.mode in (None, "none", "penhmain_corregido"):
        return ""
    if st.error:
        return f" No se han podido partir los tejidos por densidad: {st.error}"
    text = ""
    if st.pieces:
        parts = [f"{name} en {k}" for name, k in st.pieces.items()]
        text += (" Tejidos partidos por densidad (CTTRACK): " + ", ".join(parts)
                 + f" ({sub.get('materiales_tac')} materiales del TAC).")
    elif not st.blocked:
        text += " No ha hecho falta partir ningún tejido por densidad."
    if st.blocked:
        text += " Sin partir: " + st.blocked_text() + "."
    text += _error_text(sub.get("error_alcance_mm"), bool(st.pieces))
    try:
        extra = float(sub.get("preparacion_extra_s") or 0.0)
    except (TypeError, ValueError):
        extra = 0.0
    if extra >= 30:
        text += f" Con protones, penhmain tardará unos {max(1, round(extra / 60))} min más en prepararse."
    return text


def _error_dict(err) -> dict:
    return {"p50": round(float(err.p50_mm), 2), "p95": round(float(err.p95_mm), 2), "rms": round(float(err.rms_mm), 2),
            "puntos": int(err.n_stop)}


# ---------------------------------------------------------------------------
# Construcción del CT
# ---------------------------------------------------------------------------

@dataclass
class BuiltCt:
    ct: CtData
    rotation: np.ndarray
    shift: np.ndarray  # cm
    tissue_voxels: list[int]  # de cada material de ``materials`` (los tejidos, si no se han partido)
    tissue_mass: list[float]  # g
    hu_range: tuple[float, float]
    materials: list[Tissue] | None = None  # los tejidos, o sus trozos si se han partido por densidad
    numbers: list[int] | None = None  # material del .in de cada uno
    plan: MaterialPlan | None = None  # materiales nuevos del .in (None: los del plan de quien importa)
    subdivision: dict | None = None  # cómo se partieron los tejidos y el error de alcance estimado
    parent_of: list[int] | None = None  # tejido de cada material

    @property
    def frame(self) -> CtFrame:
        return CtFrame(self.rotation, self.shift, self.ct.size, self.ct.shape)

    @property
    def angles(self) -> tuple[float, float, float]:
        """(OMEGA, THETA, PHI) en grados."""
        if np.allclose(self.rotation, np.eye(3), atol=1e-12):
            return (0.0, 0.0, 0.0)
        return euler_angles(self.rotation)

    def mean_density(self, i: int) -> float | None:
        """Densidad media (g/cm³) de los vóxeles del tejido ``i`` (None si no tiene ninguno)."""
        n = self.tissue_voxels[i]
        if n <= 0 or not self.tissue_mass[i] > 0:  # sin vóxeles, o vacío (material 0: densidad 0)
            return None
        nx, ny, nz = self.ct.shape
        vol_cm3 = float(np.prod(np.array(self.ct.size) / np.array([nx, ny, nz])))
        return self.tissue_mass[i] / (n * vol_cm3)


@dataclass
class WrittenCt:
    """El .ct y su ficha escritos con nombres temporales, a la espera de :func:`commit_written`."""

    ct_path: Path
    sidecar_path: Path
    tmp_ct: Path
    tmp_sidecar: Path


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def commit_written(w: WrittenCt) -> tuple[Path, Path]:
    """Da a los temporales sus nombres definitivos (sustituye a los que hubiera: se ha preguntado antes)."""
    try:
        os.replace(w.tmp_ct, w.ct_path)
        os.replace(w.tmp_sidecar, w.sidecar_path)
    finally:
        discard_written(w)
    return w.ct_path, w.sidecar_path


def discard_written(w: WrittenCt) -> None:
    """Borra los temporales que queden (al cancelar o si algo ha fallado)."""
    _unlink(w.tmp_ct)
    _unlink(w.tmp_sidecar)


@dataclass
class CtImport:
    """Todo lo que se elige al importar."""

    vol: HuVolume
    grid: GridSpec
    calibration: Calibration
    # Material del .in de cada tejido. Sin ``existing``, son los del CT y 0 es vacío. Con ``existing`` los números
    # salen de los vóxeles al importar y son solo una vista previa (el vacío se pide con ``reuse``).
    numbers: list[int]
    placement_mode: str = "centro"
    point_mm: np.ndarray | None = None
    ct_name: str = "tac.ct"
    label: str = "TAC"
    title: str = ""
    offset_cm: np.ndarray | None = None  # desplazamiento adicional (cm) después de la colocación
    extra: dict = field(default_factory=dict)
    # Partir los tejidos por densidad (CTTRACK): «auto» (lo recomendado), «max» (todos los materiales que
    # quepan) o «none». Con ``cttrack_fixed`` (penhmain corregido) no se parte nada.
    split: str = "none"
    cttrack_fixed: bool = False
    existing: list[str] | None = None  # ficheros .mat del .in (material 1, 2...): para numerar y para MAXMAT
    reuse: list[int | None] | None = None  # material del .in que usa cada tejido (None: nuevo; 0: vacío)
    maxmat: int = MAXMAT

    def frame_placement(self) -> tuple[np.ndarray, np.ndarray]:
        """Giro y desplazamiento (cm) del CTBOX, con el desplazamiento adicional."""
        rot, shift = placement(self.vol, self.grid, self.placement_mode, self.point_mm)
        if self.offset_cm is not None:
            shift = shift + np.asarray(self.offset_cm, dtype=float)
        return rot, shift

    def problems(self) -> list[str]:
        out = list(self.calibration.problems())
        if len(self.numbers) != len(self.calibration.tissues):
            out.append("Falta el material de algún tejido.")
        elif any(n < 0 or n > MAX_CT_MATERIAL for n in self.numbers):
            out.append(f"Los materiales de un CT van de 0 a {MAX_CT_MATERIAL}.")
        out += shared_mat_problems(self.calibration, self.existing, self.reuse)
        p = ct_name_problem(self.ct_name)
        if p:
            out.append(p)
        if self.grid.shape[2] > NDD:
            out.append(f"El CT tendría {self.grid.shape[2]} cortes; penhmain admite como mucho {NDD} "
                       "(aumente el tamaño de vóxel en z o recorte).")
        return out

    def build(self, progress: Callable[[int, int, str], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> BuiltCt:
        probs = self.problems()
        if probs:
            raise ValueError(probs[0])
        if self.split not in SPLIT_MODES:
            raise ValueError(f"Modo de partir por densidad desconocido: {self.split!r}.")
        hu = resample(self.vol, self.grid, progress, cancel)
        cal = self.calibration
        nt = len(cal.tissues)
        idx = cal.tissue_index(hu)
        den = cal.density(hu)
        vol_cm3 = float(np.prod(self.grid.voxel / 10.0))
        rot, shift = self.frame_placement()
        hu_range = (float(hu.min()), float(hu.max()))
        counts_t = np.bincount(idx.ravel(), minlength=nt)
        reuse = list(self.reuse) if self.reuse is not None else [None] * nt
        reuse += [None] * (nt - len(reuse))
        # Vacío (material 0) es solo lo que se pide: ``reuse`` 0 o, si quien importa da los números del CT (sin
        # ``existing``), un 0 en ``numbers``. Con ``existing`` un 0 en ``numbers`` solo dice que el tejido no parecía
        # estar (el diálogo lo mira con los HU sin promediar), y los vóxeles promediados de los bordes pueden caer
        # en él: entonces tiene que tener su material (antes salían vacíos sin avisar).
        void = [reuse[i] == 0 or (self.existing is None and i < len(self.numbers) and self.numbers[i] == 0)
                for i in range(nt)]
        reuse = [0 if void[i] else reuse[i] for i in range(nt)]
        mode = "penhmain_corregido" if self.cttrack_fixed else self.split
        sub: dict = {"modo": mode}
        analysis, chosen = None, 0
        existing = None if self.existing is None and mode in ("none", "penhmain_corregido") else list(
            self.existing or [])
        if mode in ("auto", "max"):
            if cancel is not None and cancel():
                raise Cancelled()
            if progress is not None:
                progress(0, 0, "Partiendo los tejidos por densidad (CTTRACK)…")
            try:
                analysis, locked_why = self._analyse(idx, den, void, reuse, counts_t, existing or [])
                chosen = len(analysis.candidates) - 1 if mode == "max" else analysis.recommended
            except Exception as exc:  # el análisis nunca impide importar: se importa sin partir y se dice por qué
                sub["error"] = str(exc) or type(exc).__name__
                analysis = None
        # Del reparto elegido hacia «sin partir», el primero cuyos materiales caben en el .in (MAXMAT).
        for k in (range(chosen, -1, -1) if analysis is not None else [None]):
            alloc = analysis.candidates[k] if analysis is not None and k is not None else None
            cuts = {i: tuple(float(v) for v in alloc.cuts[i]) for i in range(nt) if alloc.pieces[i] >= 2} \
                if alloc is not None else {}
            mats, parent_of, eidx, counts, plan, numbers = self._materials(idx, den, cuts, void, reuse, existing)
            if plan is None or not plan.problems or not k:
                break
        if analysis is not None:
            last = alloc is analysis.candidates[-1] or k != chosen  # el reparto lo limitan los materiales libres
            sub.update(self._summary(analysis, alloc, locked_why, last))
        nums = np.array(numbers, dtype=np.int32)
        mat = nums[eidx]
        den = np.where(mat > 0, den, 0.0)
        ct = CtData(title=(self.title or "CT importado con ARGUS")[:72],
                    size=tuple(float(v) for v in self.grid.size_mm / 10.0), shape=self.grid.shape,
                    material=mat, density=den)
        mass = np.bincount(eidx.ravel(), weights=den.ravel(), minlength=len(mats)) * vol_cm3
        built = BuiltCt(ct, rot, shift, [int(c) for c in counts], [float(m) for m in mass], hu_range,
                        materials=mats, numbers=numbers, plan=plan, subdivision=sub, parent_of=parent_of)
        # La densidad de cada .mat es la media de sus vóxeles.
        built.materials = [replace(m, voxel_density=built.mean_density(i)) if built.mean_density(i) else m
                           for i, m in enumerate(mats)]
        return built

    def _materials(self, idx: np.ndarray, den: np.ndarray, cuts: dict[int, tuple[float, ...]], void: list[bool],
                   reuse: list[int | None], existing: list[str] | None):
        """(materiales, tejido de cada uno, material de cada vóxel (índice), vóxeles, plan, números del .in).

        Sin ``existing`` (quien importa no dice qué tiene el .in) y sin partir, los números son los que se dieron
        (``numbers``) y no hay plan.
        """
        cal = self.calibration
        taken = {e.lower() for e in (existing or [])} | {t.mat_file.lower() for t in cal.tissues}
        mats, parent_of, first = split_tissues(list(cal.tissues), cuts, taken)
        if cuts:
            from .subdivide import assign

            ti = np.where(np.asarray(void, dtype=bool)[idx], -1, idx).astype(np.int32)
            piece = assign(ti, den, {i: np.asarray(c, dtype=float) for i, c in cuts.items()})
            eidx = np.asarray(first, dtype=np.int32)[idx] + np.maximum(piece, 0).astype(np.int32)
        else:
            eidx = idx
        counts = np.bincount(eidx.ravel(), minlength=len(mats))
        if existing is None:
            return mats, parent_of, eidx, counts, None, [int(n) for n in self.numbers]
        plan = plan_materials(Calibration(cal.name, cal.curve, mats, cal.key), existing,
                              [reuse[pi] for pi in parent_of], self.maxmat, present=[bool(c) for c in counts])
        return mats, parent_of, eidx, counts, plan, [int(n) for n in plan.numbers]

    def _analyse(self, idx: np.ndarray, den: np.ndarray, void: list[bool], reuse: list[int | None],
                 counts_t: np.ndarray, existing: list[str]):
        """Análisis de :mod:`argus.ct.subdivide` con los materiales que quedan libres en el .in."""
        from .subdivide import TissueInfo, analyse, density_ranges

        cal = self.calibration
        names = [e.lower() for e in existing]
        present = [bool(counts_t[i]) and not void[i] for i in range(len(cal.tissues))]
        locked_why: list[str | None] = []
        sharing: dict[str, int] = {}  # .mat nuevo -> tejidos presentes que lo usan
        for i, t in enumerate(cal.tissues):
            if present[i]:
                sharing[t.mat_file.lower()] = sharing.get(t.mat_file.lower(), 0) + 1
        ranges = density_ranges(cal.curve, [t.hu_min for t in cal.tissues])
        infos: list[TissueInfo] = []
        allowance = 0  # trozos que no necesitan material nuevo (el CT los cuenta todos)
        for i, t in enumerate(cal.tissues):
            key = t.mat_file.lower()
            reused = reuse[i] not in (None, 0) or key in names
            shared = sharing.get(key, 0) > 1
            comp = penhmat_composition(t)
            if present[i] and reused:
                allowance += 1
            locked_why.append(WHY_IN if reused else WHY_SHARED if shared else WHY_UNKNOWN if not comp else None)
            ie = excitation_energy(t)  # la misma I que se dará a penhmat
            infos.append(TissueInfo(index=i, composition=comp, density_range=ranges[i] if i < len(ranges) else None,
                                    locked=bool(reused or shared or not comp), void=bool(void[i]), name=t.name,
                                    i_ev=ie[0] if ie else None))
        for key, k in sharing.items():  # k tejidos con el mismo .mat nuevo: un solo material
            if k > 1 and key not in names:
                allowance += k - 1
        budget = self.maxmat - len(existing) + allowance
        ti = np.where(np.asarray(void, dtype=bool)[idx], -1, idx).astype(np.int32)
        return analyse(ti, den, tuple(float(v) / 10.0 for v in self.grid.voxel), infos, budget), locked_why

    def _summary(self, analysis, alloc, locked_why: list[str | None], budget_bound: bool) -> dict:
        """«subdivision» de la ficha. «limites»: por qué cada tejido tiene los trozos que tiene (``WHY_*``, o
        «vacío» / «ausente»)."""
        from .subdivide import F_SOFT, LIMIT_LOCKED

        cal = self.calibration
        why = []
        for i in range(len(cal.tissues)):
            lim = str(analysis.limits[i])
            if lim == LIMIT_LOCKED:
                lim = locked_why[i] or lim
            elif alloc.pieces[i] < analysis.kcap[i]:  # admitiría más trozos
                lim = WHY_BUDGET if budget_bound else WHY_GAIN
            why.append(lim)
        return {"materiales_tac": int(alloc.n_materials),
                "trozos": {t.name: int(alloc.pieces[i]) for i, t in enumerate(cal.tissues)},
                "limites": {t.name: why[i] for i, t in enumerate(cal.tissues)},
                "error_alcance_mm": {"sin_partir": _error_dict(analysis.unsplit.error),
                                     "elegido": _error_dict(alloc.error)},
                "preparacion_extra_s": round(float(alloc.setup_s), 1),
                "presupuesto": int(analysis.max_ct_materials),
                "modelo": {"fraccion_blanda": F_SOFT, "direcciones": list(analysis.directions),
                           "descripcion": "error de alcance (mm de agua) en los puntos del paciente donde puede "
                                          "pararse una partícula, sin corregir penhmain (CTTRACK)"}}

    def sidecar(self, built: BuiltCt) -> dict:
        """La ficha: datos técnicos (nada del paciente ni de la exploración), rejilla, colocación y calibración."""
        cal = self.calibration.to_dict()
        mats = built.materials if built.materials is not None else list(self.calibration.tissues)
        nums = built.numbers if built.numbers is not None else [int(n) for n in self.numbers]
        parent_of = built.parent_of if built.parent_of is not None else list(range(len(mats)))
        vox_cm3 = float(np.prod(np.array(built.ct.size) / np.array(built.ct.shape)))
        # Materiales que el .in ya tenía (el tejido los reutiliza): siguen siendo los del .in (agua de un maniquí,
        # aire de la sala...); la ficha lo dice para que no se generen ni se comprueben como el tejido. Sin plan
        # (quien importa dio los números sin decir qué tiene el .in) no se sabe: todos son del TAC.
        new = {int(n) for n, _f, _d in built.plan.new} if built.plan is not None else None

        def from_in(n: int) -> bool:
            return new is not None and n > 0 and n not in new

        for i, tj in enumerate(cal["tejidos"]):  # los tejidos de la calibración: totales de sus trozos
            members = [j for j, pi in enumerate(parent_of) if pi == i]
            nvox = sum(built.tissue_voxels[j] for j in members)
            mass = sum(built.tissue_mass[j] for j in members)
            tj["material"] = int(nums[members[0]]) if members else 0
            if from_in(tj["material"]):
                tj[IN_MATERIAL] = True
            tj["voxeles"] = int(nvox)
            tj["masa_g"] = round(mass, 6)
            if nvox > 0:
                tj["densidad_media"] = float(f"{mass / (nvox * vox_cm3):.6g}")
        materials = []
        for i, m in enumerate(mats):
            d = m.to_dict()
            d["material"] = int(nums[i])
            d["voxeles"] = built.tissue_voxels[i]
            d["masa_g"] = round(built.tissue_mass[i], 6)
            rho = built.mean_density(i)
            if rho is not None:
                d["densidad_media"] = float(f"{rho:.6g}")
            if from_in(d["material"]):
                d[IN_MATERIAL] = True  # ARGUS no le da nada a penhmat: es el material del .in
            else:
                ie = excitation_energy(m, rho)
                if ie is not None:
                    d["i_penhmat"], d["regla_i"] = round(float(ie[0]), 2), ie[1]
            materials.append(d)
        g = self.grid
        return {
            "argus": __version__,
            "version_ficha": 2,
            "creado": _dt.datetime.now().isoformat(timespec="seconds"),
            "fichero_ct": self.ct_name,
            "serie": self.vol.info,
            "recorte_mm": {"x": [float(g.lo[0]), float(g.hi[0])], "y": [float(g.lo[1]), float(g.hi[1])],
                           "z": [float(g.lo[2]), float(g.hi[2])]},
            "voxel_mm": [float(v) for v in g.voxel],
            "voxeles": list(g.shape),
            "colocacion": {"modo": self.placement_mode,
                           "punto_mm": None if self.point_mm is None else [float(v) for v in self.point_mm],
                           "desplazamiento_adicional_cm": None if self.offset_cm is None
                           else [float(v) for v in self.offset_cm],
                           "desplazamiento_cm": [float(v) for v in built.shift],
                           "giro_grados": [float(v) for v in built.angles]},
            "hu": [built.hu_range[0], built.hu_range[1]],
            "calibracion": cal,
            "materiales": materials,
            "subdivision": built.subdivision or {"modo": "none"},
        }

    def write_temp(self, folder: str | Path, built: BuiltCt, newline: str = "\n", cancel=None) -> WrittenCt:
        """Escribe el .ct y su ficha en ``folder`` (la carpeta del .geo) con nombres temporales, distintos en cada
        importación (una cancelada que aún está escribiendo no pisa la siguiente). ``cancel()``: deja de escribir
        (:class:`Cancelled`) y borra lo escrito."""
        folder = Path(folder)
        ct_path = folder / self.ct_name
        js = sidecar_path(ct_path)
        tag = f".argus-tmp-{uuid.uuid4().hex[:10]}"
        w = WrittenCt(ct_path, js, ct_path.with_name(ct_path.name + tag), js.with_name(js.name + tag))
        try:
            write_ct_file(w.tmp_ct, built.ct.title, built.ct.size, built.ct.material, built.ct.density, newline,
                          atomic=False, cancel=cancel)
            w.tmp_sidecar.write_text(json.dumps(self.sidecar(built), ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        except WriteCancelled:
            discard_written(w)
            raise Cancelled() from None
        except BaseException:
            discard_written(w)
            raise
        return w

    def write(self, folder: str | Path, built: BuiltCt, newline: str = "\n") -> tuple[Path, Path]:
        """Escribe el .ct y su ficha en ``folder`` (la carpeta del .geo)."""
        return commit_written(self.write_temp(folder, built, newline))


# ---------------------------------------------------------------------------
# Geometría
# ---------------------------------------------------------------------------

def _clean(v: float) -> float:
    r = round(float(v), 12)
    return 0.0 if r == 0 else r


def ct_transform_params(rotation: np.ndarray, shift: np.ndarray) -> list[ParamLine]:
    """Líneas de la transformación del CTBOX (ángulos en grados solo si hay giro)."""
    out: list[ParamLine] = []
    if not np.allclose(rotation, np.eye(3), atol=1e-12):
        om, th, ph = euler_angles(rotation)
        for name, v in (("OMEGA", om), ("THETA", th), ("PHI", ph)):
            out.append(ParamLine(name, _clean(v), 0, "DEG"))
    for name, v in zip(("X-SHIFT", "Y-SHIFT", "Z-SHIFT"), shift):
        out.append(ParamLine(name, _clean(v), 0, ""))
    return out


def _cloned_labels(doc) -> set[str]:
    return {_lab(e.module_label) for e in doc.elements if isinstance(e, CloneElement)}


def _inside_cloned(geo, kb: int, cloned: set[str]) -> bool:
    """¿Se clona ``kb`` o un módulo que lo contiene? (el clon copiaría la caja CT)."""
    k = kb
    while k and k <= geo.nbodys:
        b = geo.body(k)
        if b.is_module and b.element is not None and _lab(getattr(b.element, "label", "")) in cloned:
            return True
        k = b.mother
    return False


def ct_containers(geo, parinp=None) -> list[int]:
    """Dónde puede ir la caja CT: el nivel superior y los cuerpos o módulos editables que no están girados ni
    desplazados (tampoco los que los contienen, con los PARINP del .in) ni se clonan."""
    out: list[int] = [top_level(geo)]
    cloned = _cloned_labels(geo.document)
    for b in geo.bodies:
        if b.origin != "user" or (geo.enclosure_added and b.index == geo.nbodys):
            continue
        try:
            editable_body(geo, b.index)
        except EditError:
            continue
        rot, shift = local_frame(geo, b.index, parinp)
        if not (np.allclose(rot, np.eye(3), atol=1e-12) and np.allclose(shift, 0.0, atol=1e-12)):
            continue
        if _inside_cloned(geo, b.index, cloned):
            continue
        out.append(b.index)
    return out


def add_ctbox(doc, geo, filename: str, rotation: np.ndarray, shift: np.ndarray, container: int | None = None,
              label: str | None = None, description: str = "", parinp=None) -> str:
    """Añade el CTBOX (con su transformación final) dentro de ``container``; devuelve su etiqueta.

    ``container``: el nivel superior (None, la envolvente o NBODYS+1 si no
    la hay), un módulo (la caja es una hija suya) o un cuerpo (la caja es un
    módulo limitante suyo y una hija del módulo madre del cuerpo).
    """
    if geo.document is not doc:
        raise ValueError("La geometría compilada no es la de este documento.")
    if geo.ct is not None or any(isinstance(e, CtBoxElement) for e in doc.elements):
        raise EditError("La geometría ya tiene un CT: PENGEOM admite uno solo.")
    p = ct_name_problem(filename)
    if p:
        raise EditError(p)
    if container is None:
        container = top_level(geo)
    enclosure = is_top_level(geo, container)
    anchor = None
    cont_el = None
    if not enclosure:
        cont_el = editable_body(geo, container)
        rot, sh = local_frame(geo, container, parinp)
        if not (np.allclose(rot, np.eye(3), atol=1e-12) and np.allclose(sh, 0.0, atol=1e-12)):
            raise EditError("El CT no puede ir dentro de un módulo girado o desplazado (PENGEOM no mueve la caja "
                            "CT con él).")
        if _inside_cloned(geo, container, _cloned_labels(doc)):
            raise EditError("El CT no puede ir dentro de un módulo que se clona (el clon copiaría la caja CT y "
                            "PENGEOM solo admite una).")
        anchor = cont_el
    lab = label.strip() if label and label.strip() else ""
    if lab:
        from ..pengeom.surfaces import label_text

        lab = label_text(lab).strip()  # sin apóstrofos, comas ni paréntesis (EditError si los lleva)
    taken = {(getattr(e, "label", None) or "").strip() for e in doc.elements}
    if not lab or lab in taken or lab in {b.alias[:4].strip() for b in geo.bodies}:
        lab = new_label(doc, "body", geo)
    el = CtBoxElement(ElementKind.CTBOX, label=lab, filename=filename, has_ones_line=True,
                      transform=ct_transform_params(rotation, shift), modified=True)
    set_description(el, description or "TAC importado con ARGUS")
    _insert_before(doc, anchor, [el])
    if cont_el is not None:
        cont_el.items.append(RefLine("MODULE", lab))
        cont_el.modified = True
        if cont_el.kind == ElementKind.BODY:
            mother = _mother_module_element(doc, cont_el.label)
            if mother is not None:
                mother.items.append(RefLine("MODULE", lab))
                mother.modified = True
    return lab


def _pct(f: float) -> str:
    """«12,5 %» (y «menos del 0,1 %» para lo que no llega, también 0: lo que solo asoma en las caras)."""
    if f < 0.0005:
        return "menos del 0,1 %"
    return f"{100 * f:.1f} %".replace(".", ",")


def _where_name(geo, b: int) -> str:
    if b > geo.nbodys:
        return "fuera de la geometría"
    return body_title(geo, b)


def check_ct_box(geo, locator, frame: CtFrame, container: int | None, n: int = 30000,
                 seed: int = 7) -> PlacementCheck:
    """¿Cabe la caja CT en el contenedor sin cortar nada?

    Puntos del volumen (y de sus caras), rayos por los tres ejes (láminas
    finas) y, para los cuerpos pequeños que caen entre los puntos y los rayos,
    puntos dentro de la caja de cada cuerpo que toca la del CT.
    """
    target = top_level(geo) if container is None else container
    rng = np.random.default_rng(seed)
    u = rng.random((n, 3))
    m = n // 5
    k = rng.integers(0, 3, m)
    u[np.arange(m), k] = np.where(u[np.arange(m), k] < 0.5, 1e-9, 1 - 1e-9)  # puntos en las caras
    pts = frame.to_lab(u * np.array(frame.size))
    kb, _ = locator.locate(pts)
    inside = kb == target
    if not inside.all():
        # Los puntos de las caras solo dicen si algo asoma; la parte del volumen que se sale se mide con los demás,
        # repartidos por todo el volumen (con los de las caras, 0,2 mm fuera en dos caras salía como un 6,7 %).
        vol_in = inside[m:]
        frac = 1.0 - float(vol_in.mean())
        offenders: dict[int, float] = {int(b): 0.0 for b in kb[~inside]}
        for b in kb[m:][~vol_in]:
            offenders[int(b)] += 1.0 / vol_in.size
        worst = sorted(offenders.items(), key=lambda kv: -kv[1])[:3]
        if len(worst) == 1:
            names = _where_name(geo, worst[0][0])
        else:
            names = ", ".join(f"{_where_name(geo, b)} ({_pct(f)})" for b, f in worst)
        share = _pct(frac) if frac < 0.0005 else f"el {_pct(frac)}"  # «menos del 0,1 %», sin «el»
        return PlacementCheck(False, f"La caja del CT se sale del contenedor: {share} de su volumen está en {names}.",
                              frac, offenders)
    from ..pengeom.raycheck import intruders

    corners = frame.corners()
    lo, hi = corners.min(axis=0), corners.max(axis=0)
    size = np.array(frame.size)
    tol = 1e-9 * float(np.max(np.abs(np.r_[lo, hi]))) + 1e-12

    def inside_box(p):
        loc = frame.to_local(p)
        return np.all((loc >= tol) & (loc <= size - tol), axis=1)

    found = intruders(geo, lo, hi, inside_box, {target})
    thin = thin_message(geo, found)
    if thin:
        return thin
    small = _small_intruder(geo, locator, lo, hi, inside_box, target, rng)
    if small is not None:
        return PlacementCheck(False, f"La caja del CT se sale del contenedor: dentro de ella está "
                                     f"{body_title(geo, small)}.", 0.0, {small: 0.0})
    return PlacementCheck(True, "La caja del CT cabe en el contenedor.")


def _small_intruder(geo, locator, lo, hi, inside_box, target: int, rng, per_body: int = 600) -> int | None:
    """Un cuerpo pequeño dentro de la caja del CT que no han visto ni los puntos ni los rayos (o None).

    Para cada cuerpo cuya caja (la de sus superficies) toca la del CT, se
    buscan puntos suyos dentro de la parte común de las dos cajas.
    """
    from ..pengeom.extent import analytic_box

    allowed = {target}
    k = target
    while 1 <= k <= geo.nbodys:  # el contenedor y los que lo contienen rodean la caja: no cuentan
        allowed.add(k)
        k = geo.body(k).mother
    for b in geo.bodies:
        if b.index in allowed or b.origin == "enclosure" or b.index == getattr(geo, "ct_body", None):
            continue
        try:
            blo, bhi = analytic_box(geo, b.index)
        except Exception:
            continue
        if not (np.all(np.isfinite(blo)) and np.all(np.isfinite(bhi))):
            continue  # cuerpos no acotados por sí mismos: ya los ven los rayos
        clo, chi = np.maximum(lo, blo), np.minimum(hi, bhi)
        if np.any(chi <= clo):
            continue
        pts = clo + rng.random((per_body, 3)) * (chi - clo)
        pts = np.vstack([pts, 0.5 * (clo + chi)])
        pts = pts[inside_box(pts)]
        if not len(pts):
            continue
        kb, _ = locator.locate(pts)
        hit = kb[(kb != target) & (kb <= geo.nbodys)]
        if hit.size:
            return int(hit[0])
    return None


# ---------------------------------------------------------------------------
# Materiales del .in
# ---------------------------------------------------------------------------

@dataclass
class MaterialPlan:
    """Qué material del .in usa cada tejido y qué líneas MFNAME hay que añadir."""

    numbers: list[int]
    new: list[tuple[int, str, str]]  # (número, fichero .mat, descripción)
    problems: list[str] = field(default_factory=list)


def plan_materials(calibration: Calibration, existing: list[str], reuse: list[int | None] | None = None,
                   maxmat: int = MAXMAT, present: list[bool] | None = None) -> MaterialPlan:
    """Números de material de los tejidos.

    ``existing``: ficheros .mat del .in (por orden: material 1, 2...).
    ``reuse``: para cada tejido, el material del .in que se quiere usar
    (None: uno nuevo, o el que ya tenga el mismo fichero .mat).
    ``present``: si se da, los tejidos sin vóxeles en el TAC no llevan material (número 0, sin MFNAME).
    """
    names = [e.lower() for e in existing]
    numbers: list[int] = []
    new: list[tuple[int, str, str]] = []
    nxt = len(existing) + 1
    added: dict[str, int] = {}
    for i, t in enumerate(calibration.tissues):
        want = reuse[i] if reuse is not None and i < len(reuse) else None
        if want is not None:
            numbers.append(int(want))
            continue
        if present is not None and i < len(present) and not present[i]:
            numbers.append(0)
            continue
        key = t.mat_file.lower()
        if key in names:
            numbers.append(names.index(key) + 1)
        elif key in added:
            numbers.append(added[key])
        else:
            added[key] = nxt
            new.append((nxt, t.mat_file, f"{t.name} (TAC)"))
            numbers.append(nxt)
            nxt += 1
    probs = shared_mat_problems(calibration, existing, reuse)
    if nxt - 1 > maxmat:
        probs.append(f"El .in tendría {nxt - 1} materiales y penhmain admite {maxmat}: use menos tejidos o "
                     "aproveche materiales que ya tiene el .in.")
    return MaterialPlan(numbers, new, probs)


def _names_es(names: list[str]) -> str:
    """«A» y «B»; «A», «B» y «C»."""
    quoted = [f"«{n}»" for n in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " y " + quoted[-1]


def shared_mat_problems(calibration: Calibration, existing: list[str] | None = None,
                        reuse: list[int | None] | None = None) -> list[str]:
    """Tejidos que tendrían el mismo .mat nuevo: no se admite, porque un .mat tiene una sola composición y una sola
    densidad (se generaba con la del último tejido y los vóxeles del otro se quedaban con ella sin avisar).

    No cuentan los tejidos que van al vacío (``reuse`` 0) ni los que usan un material del .in (``reuse``, o un .mat
    que ya está en ``existing``): esos siguen siendo los materiales del .in.
    """
    names = {e.lower() for e in (existing or [])}
    groups: dict[str, list[Tissue]] = {}
    for i, t in enumerate(calibration.tissues):
        want = reuse[i] if reuse is not None and i < len(reuse) else None
        key = t.mat_file.lower()
        if want is None and key not in names:
            groups.setdefault(key, []).append(t)
    return [f"Los tejidos {_names_es([t.name for t in group])} tendrían el mismo .mat nuevo ({group[0].mat_file}), "
            "pero un .mat tiene una sola composición y una sola densidad: dé a cada tejido su propio fichero .mat."
            for group in groups.values() if len(group) > 1]


def isotope_summary(calibration: Calibration, used: list[int] | None = None, others: set | None = None
                    ) -> tuple[int, str]:
    """(isótopos ENDF distintos, texto) de los tejidos más ``others`` (los del resto de materiales)."""
    iso = calibration.isotopes(used) | set(others or set())
    n = len(iso)
    txt = f"Isótopos con datos ENDF (protones): {n} de {MNIS}"
    if n > MNIS:
        txt += " — con protones o alfas penhmain se pararía (IRETRN 40029)"
    return n, txt


def tissue_index_of(numbers: list[int]) -> dict[int, int]:
    return {n: i for i, n in enumerate(numbers)}


__all__ = ["BuiltCt", "CT_NAME_MAX", "CtImport", "IN_MATERIAL", "MAXMAT", "MaterialPlan", "SPLIT_MODES", "add_ctbox",
           "check_ct_box", "ct_containers", "ct_name_problem", "ct_transform_params", "isotope_summary",
           "plan_materials", "read_sidecar", "shared_mat_problems", "sidecar_families", "sidecar_path",
           "sidecar_subdivision", "sidecar_tissues", "SplitState", "split_state", "split_summary", "split_tissues",
           "sub_material_name"]
