"""Series de TAC en DICOM: buscarlas en una carpeta y leerlas como un volumen de HU.

Un TAC «clásico» es una serie de ficheros, un corte por fichero, con la
posición (ImagePositionPatient, mm) y la orientación (ImageOrientationPatient)
de cada corte en el sistema del paciente (LPS: x hacia la izquierda del
paciente, y hacia atrás, z hacia la cabeza). También se leen los TAC
«mejorados» (un fichero con todos los cortes como marcos). Los valores del
píxel pasan a unidades Hounsfield con RescaleSlope y RescaleIntercept.

Las posiciones se ordenan a lo largo de la normal de los cortes
(fila × columna), así los ejes de la matriz (x: columnas, y: filas, z: cortes)
forman un triedro directo; si coinciden con los ejes del paciente (salvo
signos y orden) se reordena la matriz para que coincidan exactamente y la
caja CT no necesite giro.

No se lee ningún dato de identificación del paciente.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.numfmt import plural
from . import Cancelled

CT_IMAGE = "1.2.840.10008.5.1.4.1.1.2"
ENHANCED_CT_IMAGE = "1.2.840.10008.5.1.4.1.1.2.1"
LEGACY_ENHANCED_CT_IMAGE = "1.2.840.10008.5.1.4.1.1.2.2"
CT_SOP_CLASSES = {CT_IMAGE, ENHANCED_CT_IMAGE, LEGACY_ENHANCED_CT_IMAGE}
UNCOMPRESSED = {"1.2.840.10008.1.2", "1.2.840.10008.1.2.1", "1.2.840.10008.1.2.2", "1.2.840.10008.1.2.1.99"}
MAX_FILE = 2 * 1024 ** 3  # no se intenta leer como DICOM un fichero mayor sin preámbulo
AXIS_TOL = 1e-5  # componentes fuera de los ejes por debajo de esto (radianes): cortes alineados con los ejes
AIR_FLOOR = -1000.0  # HU por debajo del aire (relleno fuera del campo de visión): se llevan a −1000

Progress = Callable[[int, int, str], None]


class DicomError(Exception):
    """No se puede leer la serie (mensaje para el usuario)."""


def _pydicom():
    try:
        import pydicom
    except ImportError as exc:  # pragma: no cover - depende de la instalación
        raise DicomError("Falta la biblioteca pydicom (se instala con ARGUS: vuelva a ejecutar "
                         "instalar_argus.bat).") from exc
    return pydicom


@dataclass
class SliceRef:
    path: Path
    position: np.ndarray  # ImagePositionPatient (mm)
    instance: int = 0
    slope: float = 1.0
    intercept: float = 0.0
    frame: int = -1  # marco de un TAC mejorado (−1: fichero de un corte)
    forced: bool = False  # fichero sin preámbulo DICOM (se lee con force=True)
    padding: float | None = None  # PixelPaddingValue (valor guardado de los píxeles fuera del campo)


@dataclass
class Series:
    """Una serie de cortes (solo cabeceras; los píxeles se leen con :func:`read_series`)."""

    uid: str
    modality: str = ""
    number: str = ""  # SeriesNumber
    description: str = ""
    study: str = ""
    date: str = ""
    manufacturer: str = ""
    model: str = ""
    kvp: float | None = None
    rows: int = 0
    cols: int = 0
    pixel_spacing: tuple[float, float] = (1.0, 1.0)  # (entre filas, entre columnas) mm
    orientation: np.ndarray = field(default_factory=lambda: np.array([1.0, 0, 0, 0, 1.0, 0]))
    thickness: float | None = None
    patient_position: str = ""
    transfer_syntax: str = ""
    slices: list[SliceRef] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # impiden importarla
    notes: list[str] = field(default_factory=list)
    folder: Path | None = None
    sop_uids: set = field(default_factory=set, repr=False)
    duplicates: int = 0  # ficheros repetidos (mismo SOPInstanceUID), que se dejan fuera
    localizers: int = 0  # imágenes de localización (scout), que se dejan fuera
    unreadable: int = 0  # ficheros de la serie que no se han podido leer

    # -- geometría -----------------------------------------------------------------
    @property
    def row_dir(self) -> np.ndarray:
        """Dirección en que crece el índice de columna (eje x de la matriz)."""
        return _unit(self.orientation[:3])

    @property
    def col_dir(self) -> np.ndarray:
        """Dirección en que crece el índice de fila (eje y de la matriz)."""
        return _unit(self.orientation[3:])

    @property
    def normal(self) -> np.ndarray:
        return _unit(np.cross(self.row_dir, self.col_dir))

    @property
    def z_positions(self) -> np.ndarray:
        return np.array([float(s.position @ self.normal) for s in self.slices])

    @property
    def nslices(self) -> int:
        return len(self.slices)

    @property
    def slice_spacing(self) -> float:
        z = self.z_positions
        if z.size < 2:
            return float(self.thickness) if self.thickness and self.thickness > 0 else 1.0
        return float(np.median(np.diff(z)))

    @property
    def compressed(self) -> bool:
        return bool(self.transfer_syntax) and self.transfer_syntax not in UNCOMPRESSED

    @property
    def ok(self) -> bool:
        return not self.problems and self.nslices > 0

    def label(self) -> str:
        """Descripción de una línea para la lista de series (solo se muestra; no se guarda en ningún fichero)."""
        desc = self.description or "Serie sin descripción"
        if self.number:
            desc += f" (serie {self.number})"
        dz = self.slice_spacing
        size = f"{self.cols}×{self.rows}"
        sp = f"{self.pixel_spacing[1]:.2f}×{self.pixel_spacing[0]:.2f}×{dz:.2f} mm".replace(".", ",")
        date = _date(self.date)
        n = self.nslices
        if n == 0:
            return f"{desc} — {self.modality or '?'}, sin cortes" + (f", {date}" if date else "")
        cortes = "1 corte" if n == 1 else f"{n} cortes"
        return f"{desc} — {self.modality or '?'}, {cortes} de {size}, {sp}" + (f", {date}" if date else "")

    def extent_mm(self) -> tuple[np.ndarray, np.ndarray]:
        """Caja de los centros de los vóxeles en el sistema de la matriz (mm)."""
        x0, y0, _ = self.frame_origin()
        z = self.z_positions
        lo = np.array([x0, y0, z.min() if z.size else 0.0])
        hi = lo + np.array([(self.cols - 1) * self.pixel_spacing[1], (self.rows - 1) * self.pixel_spacing[0],
                            (z.max() - z.min()) if z.size else 0.0])
        return lo, hi

    def frame_origin(self) -> np.ndarray:
        """Coordenadas (x, y, z) del centro del primer vóxel en el sistema de la matriz (mm)."""
        if not self.slices:
            return np.zeros(3)
        p = self.slices[0].position
        return np.array([p @ self.row_dir, p @ self.col_dir, p @ self.normal])


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _date(d: str) -> str:
    d = (d or "").strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[6:8]}/{d[4:6]}/{d[0:4]}"
    return d


def _float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _floats(v, n: int):
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    return out if len(out) == n and all(np.isfinite(out)) else None


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _image_type(ds) -> list[str]:
    v = ds.get("ImageType")
    if v is None:
        return []
    if isinstance(v, str):
        return [p.strip().upper() for p in v.split("\\")]
    try:
        return [str(p).strip().upper() for p in v]
    except TypeError:
        return [str(v).strip().upper()]


def axis_aligned(rot: np.ndarray, tol: float = AXIS_TOL) -> bool:
    """¿Son los ejes de la matriz los del paciente salvo orden y signo? (componentes fuera de ellos < ``tol``)."""
    r = np.asarray(rot, dtype=float)
    perm = np.argmax(np.abs(r), axis=0)
    if sorted(perm.tolist()) != [0, 1, 2]:
        return False
    off = r.copy()
    off[perm, [0, 1, 2]] = 0.0
    return bool(np.max(np.abs(off)) <= tol)


# ---------------------------------------------------------------------------
# Búsqueda de series
# ---------------------------------------------------------------------------

def _candidate_files(folder: Path, recursive: bool) -> list[Path]:
    out: list[Path] = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if not f.startswith(".") and f.upper() != "DICOMDIR":
                    out.append(Path(root) / f)
    else:
        out = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    return out


_NOT_DICOM = {".txt", ".in", ".geo", ".ct", ".mat", ".endf", ".dat", ".png", ".jpg", ".jpeg", ".pdf", ".zip",
              ".json", ".csv", ".xml", ".html", ".py", ".exe", ".x", ".dmp", ".nii", ".gz", ".bmp", ".tif", ".tiff"}


def _looks_raw_dicom(path: Path) -> bool:
    """Fichero sin preámbulo que empieza como un conjunto de datos DICOM (grupo 0002 u 0008, little endian)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return len(head) == 8 and head[1] == 0 and head[0] in (0x02, 0x08) and head[3] == 0


def _read_header(path: Path):
    """Cabecera (sin píxeles) de un fichero DICOM, o None. Marca ``_argus_forced`` si no tiene preámbulo."""
    pydicom = _pydicom()
    from pydicom.errors import InvalidDicomError

    try:
        ds = pydicom.dcmread(str(path), stop_before_pixels=True)
        ds._argus_forced = False
        return ds
    except InvalidDicomError:
        pass
    except Exception:  # fichero ilegible o que no es DICOM
        return None
    # Sin preámbulo «DICM»: solo si empieza como un conjunto de datos y no tiene una extensión conocida.
    if path.suffix.lower() in _NOT_DICOM or not _looks_raw_dicom(path):
        return None
    try:
        if path.stat().st_size > MAX_FILE:
            return None
        ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:
        return None
    if "SeriesInstanceUID" not in ds:
        return None
    ds._argus_forced = True
    return ds


def _shared(ds, seq_name: str, item: str):
    """Elemento de un grupo funcional compartido (TAC mejorado)."""
    try:
        return getattr(getattr(ds.SharedFunctionalGroupsSequence[0], seq_name)[0], item)
    except (AttributeError, IndexError, KeyError):
        return None


def _shared_or_root(ds, seq_name: str, item: str):
    """El del grupo funcional compartido o, si no está, el de la raíz del fichero."""
    got = _shared(ds, seq_name, item)
    return got if got is not None else ds.get(item)


def _per_frame(ds, i: int, seq_name: str, item: str):
    try:
        return getattr(getattr(ds.PerFrameFunctionalGroupsSequence[i], seq_name)[0], item)
    except (AttributeError, IndexError, KeyError):
        return None


def scan_folder(folder: str | os.PathLike, recursive: bool = True, progress: Progress | None = None,
                cancel: Callable[[], bool] | None = None) -> list[Series]:
    """Series DICOM de una carpeta (y sus subcarpetas), las de TAC primero."""
    folder = Path(folder)
    if not folder.is_dir():
        raise DicomError(f"No existe la carpeta {folder}.")
    files = _candidate_files(folder, recursive)
    series: dict[str, Series] = {}
    for k, path in enumerate(files):
        if cancel is not None and cancel():
            raise Cancelled()
        if progress is not None and (k % 16 == 0 or k == len(files) - 1):
            progress(k + 1, len(files), path.name)
        ds = _read_header(path)
        if ds is None or "SeriesInstanceUID" not in ds:
            continue
        if "Rows" not in ds:  # no es una imagen (RTSTRUCT, RTPLAN, informes...): no es una serie para importar
            continue
        s = None
        try:
            uid = str(ds.SeriesInstanceUID)
            s = series.get(uid)
            if s is None:
                s = Series(uid=uid, folder=path.parent)
                s.modality = str(ds.get("Modality", "") or "").strip()
                s.number = str(ds.get("SeriesNumber", "") or "").strip()
                s.description = str(ds.get("SeriesDescription", "") or "").strip()
                s.study = str(ds.get("StudyDescription", "") or "").strip()
                s.date = str(ds.get("SeriesDate", "") or ds.get("StudyDate", "") or "")
                s.manufacturer = str(ds.get("Manufacturer", "") or "").strip()
                s.model = str(ds.get("ManufacturerModelName", "") or "").strip()
                s.kvp = _float(ds.get("KVP"))
                s.patient_position = str(ds.get("PatientPosition", "") or "")
                s.thickness = _float(ds.get("SliceThickness"))
                meta = getattr(ds, "file_meta", None)
                s.transfer_syntax = str(getattr(meta, "TransferSyntaxUID", "") or "") if meta is not None else ""
                series[uid] = s
            _add_slices(s, ds, path)
        except Exception:  # cabecera con valores que no se entienden: se deja fuera ese fichero
            if s is not None:
                s.unreadable += 1
            continue
    out = [_finish(s) for s in series.values()]
    out.sort(key=lambda s: (s.modality != "CT", not s.ok, -s.nslices, s.description))
    return out


def _rescale_type_problem(value) -> str | None:
    rt = str(value or "").strip().upper()
    if rt and rt not in ("HU", "HOUNSFIELD UNITS"):
        return f"Los valores no son unidades Hounsfield (RescaleType {rt}): no es un TAC para importar."
    return None


def _add_slices(s: Series, ds, path: Path) -> None:
    rows = _int(ds.get("Rows", 0) or 0)
    cols = _int(ds.get("Columns", 0) or 0)
    nframes = _int(ds.get("NumberOfFrames", 1) or 1, 1)
    forced = bool(getattr(ds, "_argus_forced", False))
    sop = str(ds.get("SOPInstanceUID", "") or "")
    if sop:
        if sop in s.sop_uids:  # el mismo fichero dos veces (copia, dos exportaciones)
            s.duplicates += 1
            return
        s.sop_uids.add(sop)
    sop_class = str(ds.get("SOPClassUID", "") or "")
    if sop_class and s.modality == "CT" and sop_class not in CT_SOP_CLASSES:
        _problem(s, "No son imágenes de TAC (p. ej. capturas de pantalla o informes de la exploración).")
        return
    padding = _float(ds.get("PixelPaddingValue"))
    functional = "PerFrameFunctionalGroupsSequence" in ds or "SharedFunctionalGroupsSequence" in ds
    if functional:  # TAC mejorado (uno o varios marcos)
        iop = _floats(_shared(ds, "PlaneOrientationSequence", "ImageOrientationPatient"), 6)
        ps = _floats(_shared(ds, "PixelMeasuresSequence", "PixelSpacing"), 2)
        # Algunos equipos (sin seguir la norma) lo dejan en la raíz del fichero, como en un TAC clásico.
        slope = _float(_shared_or_root(ds, "PixelValueTransformationSequence", "RescaleSlope"), None)
        icpt = _float(_shared_or_root(ds, "PixelValueTransformationSequence", "RescaleIntercept"), None)
        rtype = _shared(ds, "PixelValueTransformationSequence", "RescaleType") or ds.get("RescaleType")
        th = _float(_shared(ds, "PixelMeasuresSequence", "SliceThickness"))
        if "LOCALIZER" in _image_type(ds):
            s.localizers += nframes
            return
        for i in range(nframes):
            ipp = _floats(_per_frame(ds, i, "PlanePositionSequence", "ImagePositionPatient"), 3)
            fiop = _floats(_per_frame(ds, i, "PlaneOrientationSequence", "ImageOrientationPatient"), 6) or iop
            fps = _floats(_per_frame(ds, i, "PixelMeasuresSequence", "PixelSpacing"), 2) or ps
            fsl = _float(_per_frame(ds, i, "PixelValueTransformationSequence", "RescaleSlope"), slope)
            fic = _float(_per_frame(ds, i, "PixelValueTransformationSequence", "RescaleIntercept"), icpt)
            frt = _per_frame(ds, i, "PixelValueTransformationSequence", "RescaleType") or rtype
            p = _rescale_type_problem(frt)
            if p:
                _problem(s, p)
                return
            if ipp is None or fiop is None or fps is None:
                _problem(s, "Faltan la posición o la orientación de algunos cortes.")
                continue
            if not _consistent(s, rows, cols, fps, fiop, th):
                continue
            s.slices.append(SliceRef(path, np.array(ipp), i + 1, 1.0 if fsl is None else fsl,
                                     0.0 if fic is None else fic, frame=i, forced=forced, padding=padding))
        return
    if "LOCALIZER" in _image_type(ds):  # imagen de localización (scout): no es un corte del volumen
        s.localizers += 1
        return
    p = _rescale_type_problem(ds.get("RescaleType"))
    if p:
        _problem(s, p)
        return
    ipp = _floats(ds.get("ImagePositionPatient"), 3)
    iop = _floats(ds.get("ImageOrientationPatient"), 6)
    ps = _floats(ds.get("PixelSpacing"), 2)
    if ipp is None or iop is None or ps is None:
        _problem(s, "Faltan la posición, la orientación o el tamaño de píxel de algunos cortes.")
        return
    if nframes > 1:
        _problem(s, "Ficheros con varios marcos sin grupos funcionales: ARGUS no sabe dónde va cada marco.")
        return
    if not _consistent(s, rows, cols, ps, iop, _float(ds.get("SliceThickness"))):
        return
    slope = _float(ds.get("RescaleSlope"), 1.0) or 1.0
    icpt = _float(ds.get("RescaleIntercept"), 0.0) or 0.0
    s.slices.append(SliceRef(path, np.array(ipp), _int(ds.get("InstanceNumber", 0) or 0), slope, icpt,
                             forced=forced, padding=padding))


def _problem(s: Series, text: str) -> None:
    if text not in s.problems:
        s.problems.append(text)


def _consistent(s: Series, rows: int, cols: int, ps, iop, thickness) -> bool:
    """Todos los cortes de la serie tienen que tener el mismo tamaño, píxel y orientación."""
    if not s.slices and s.rows == 0:
        s.rows, s.cols = rows, cols
        s.pixel_spacing = (float(ps[0]), float(ps[1]))
        s.orientation = np.array(iop, dtype=float)
        if thickness is not None:
            s.thickness = thickness
        return True
    if rows != s.rows or cols != s.cols:
        _problem(s, "Los cortes no tienen todos el mismo número de filas y columnas.")
        return False
    if abs(ps[0] - s.pixel_spacing[0]) > 1e-4 * s.pixel_spacing[0] or \
            abs(ps[1] - s.pixel_spacing[1]) > 1e-4 * s.pixel_spacing[1]:
        _problem(s, "Los cortes no tienen todos el mismo tamaño de píxel.")
        return False
    if np.max(np.abs(np.array(iop) - s.orientation)) > 1e-4:
        _problem(s, "Los cortes no tienen todos la misma orientación.")
        return False
    return True


def _finish(s: Series) -> Series:
    if s.modality and s.modality != "CT":
        s.problems.insert(0, f"No es un TAC (modalidad {s.modality}): los valores no son unidades Hounsfield.")
    if s.duplicates:
        s.notes.append(f"{plural(s.duplicates, 'fichero repetido', 'ficheros repetidos')} (el mismo corte dos veces): "
                       "se usa uno.")
    if s.unreadable:
        s.notes.append(f"{plural(s.unreadable, 'fichero')} de la serie no se "
                       f"{'ha' if s.unreadable == 1 else 'han'} podido leer.")
    if not s.slices and s.localizers:
        _problem(s, "Solo tiene imágenes de localización (scout): no es un volumen.")
        return s
    if s.localizers:
        s.notes.append(f"Se {'deja' if s.localizers == 1 else 'dejan'} fuera "
                       f"{plural(s.localizers, 'imagen', 'imágenes')} de localización (scout).")
    if s.rows <= 0 or s.cols <= 0:
        _problem(s, "La serie no tiene imágenes.")
    if not s.slices:
        return s
    n = s.normal
    if np.linalg.norm(np.cross(s.orientation[:3], s.orientation[3:])) < 0.99:
        _problem(s, "La orientación de los cortes no es válida (filas y columnas no perpendiculares).")
        return s
    s.slices.sort(key=lambda sl: (float(sl.position @ n), sl.instance))
    z = s.z_positions
    if z.size > 1:
        dz = np.diff(z)
        if np.any(dz < 1e-3):
            _problem(s, "Hay cortes repetidos en la misma posición (¿dos adquisiciones en la misma serie?).")
            return s
        # Desplazamiento de los cortes dentro de su plano: gantry inclinado.
        p0 = s.slices[0].position
        rd, cd = s.row_dir, s.col_dir
        shift = np.array([[(sl.position - p0) @ rd, (sl.position - p0) @ cd] for sl in s.slices])
        tol = 0.01 * min(s.pixel_spacing)
        if np.max(np.abs(shift)) > tol:
            _problem(s, "Los cortes están desplazados unos respecto a otros (gantry inclinado): ARGUS no "
                        "corrige esa inclinación.")
            return s
        med = float(np.median(dz))
        if np.max(dz) > 1.5 * med:
            k = int(np.argmax(dz))
            s.notes.append(f"Falta algún corte entre z = {z[k]:.1f} y {z[k + 1]:.1f} mm; el hueco se rellena con "
                           "los cortes vecinos.".replace(".", ",", 2))
        elif np.max(np.abs(dz - med)) > 0.01 * med:
            s.notes.append("La distancia entre cortes no es constante; se tiene en cuenta al remuestrear.")
    if s.nslices == 1:
        th = f"{s.slice_spacing:.2f}".replace(".", ",")
        s.notes.append(f"Un solo corte: la caja CT tendrá su grosor ({th} mm).")
    if s.compressed:
        s.notes.append("Las imágenes están comprimidas; si no se pueden leer, exporte la serie sin compresión.")
    rot = np.column_stack([s.row_dir, s.col_dir, s.normal])
    if not axis_aligned(rot):
        s.notes.append("Los cortes son oblicuos: la caja CT se colocará girada como en el TAC.")
    elif abs(abs(s.normal @ np.array([0.0, 0.0, 1.0])) - 1.0) > 1e-3:
        s.notes.append("Los cortes no son axiales (coronales o sagitales): se reordenan para que la caja CT "
                       "quede alineada con los ejes.")
    return s


# ---------------------------------------------------------------------------
# Lectura de los píxeles
# ---------------------------------------------------------------------------

@dataclass
class HuVolume:
    """Volumen en unidades Hounsfield en el sistema de la matriz.

    ``hu[k, j, i]``: vóxel de columna i, fila j y corte k. Su centro está en
    (x[i], y[j], z[k]) (mm) del sistema de la matriz; en el del paciente,
    ``rotation @ (x, y, z)``. Tras :func:`canonical`, ``rotation`` es la
    identidad si los ejes del TAC coinciden con los del paciente.
    """

    hu: np.ndarray  # (NZ, NY, NX), int16 o float32
    x: np.ndarray  # (NX,) centros (mm)
    y: np.ndarray  # (NY,)
    z: np.ndarray  # (NZ,) (puede no ser uniforme)
    rotation: np.ndarray  # (3, 3): columnas = direcciones de los ejes de la matriz en el sistema LPS
    info: dict = field(default_factory=dict)  # datos técnicos de la serie (sin datos del paciente)
    single: tuple[float, float, float] = (1.0, 1.0, 1.0)  # anchura (mm) de un eje con un solo vóxel (x, y, z)

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.hu.shape[2], self.hu.shape[1], self.hu.shape[0]

    def edges(self, axis: int) -> np.ndarray:
        """Bordes de los vóxeles a lo largo de un eje (0: x, 1: y, 2: z)."""
        return voxel_edges((self.x, self.y, self.z)[axis], self.single[axis])

    def spacing(self) -> tuple[float, float, float]:
        def med(c, one):
            return float(np.median(np.diff(c))) if c.size > 1 else float(one)
        return abs(med(self.x, self.single[0])), abs(med(self.y, self.single[1])), abs(med(self.z, self.single[2]))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """Caja que ocupan los vóxeles (bordes, no centros), en mm del sistema de la matriz."""
        lo, hi = [], []
        for a in range(3):
            e = self.edges(a)
            lo.append(e[0])
            hi.append(e[-1])
        return np.array(lo), np.array(hi)


def voxel_edges(c: np.ndarray, single: float = 1.0) -> np.ndarray:
    """Bordes de los vóxeles a partir de sus centros (crecientes): puntos medios y medio paso en los extremos.

    ``single``: anchura del vóxel si solo hay uno (p. ej. el grosor de un corte único).
    """
    c = np.asarray(c, dtype=float)
    if c.size == 1:
        h = 0.5 * (single if single and single > 0 else 1.0)
        return np.array([c[0] - h, c[0] + h])
    mid = 0.5 * (c[1:] + c[:-1])
    return np.concatenate([[c[0] - 0.5 * (c[1] - c[0])], mid, [c[-1] + 0.5 * (c[-1] - c[-2])]])


def _decode(ds, frame: int, name: str) -> np.ndarray:
    try:
        arr = ds.pixel_array
    except Exception as exc:
        ts = str(getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", "") or "")
        extra = " (imágenes comprimidas: exporte la serie sin compresión)" if ts and ts not in UNCOMPRESSED else ""
        raise DicomError(f"No se pueden leer los píxeles de {name}{extra}: {exc}") from None
    if frame >= 0 and arr.ndim == 3:
        arr = arr[frame]
    if arr.ndim != 2:
        raise DicomError(f"{name}: la imagen no es de un solo canal (¿es un TAC?).")
    return arr


def _read_full(pydicom, sl: SliceRef):
    """Conjunto de datos completo de un corte (con los píxeles)."""
    if not sl.forced:
        return pydicom.dcmread(str(sl.path))
    # Sin preámbulo ni metadatos: pydicom necesita saber la sintaxis para decodificar los píxeles.
    from pydicom.dataset import FileMetaDataset
    from pydicom.uid import ExplicitVRBigEndian, ExplicitVRLittleEndian, ImplicitVRLittleEndian

    ds = pydicom.dcmread(str(sl.path), force=True)
    meta = getattr(ds, "file_meta", None)
    if meta is None or "TransferSyntaxUID" not in meta:
        implicit, little = getattr(ds, "original_encoding", (True, True))
        meta = meta if meta is not None else FileMetaDataset()
        meta.TransferSyntaxUID = (ImplicitVRLittleEndian if implicit else
                                  ExplicitVRLittleEndian if little else ExplicitVRBigEndian)
        ds.file_meta = meta
    return ds


def read_series(series: Series, progress: Progress | None = None, cancel: Callable[[], bool] | None = None,
                canonical_axes: bool = True) -> HuVolume:
    """Lee los píxeles de una serie y los pasa a HU.

    Los valores por debajo del aire (−1000 HU) —el relleno de fuera del campo
    de visión (PixelPaddingValue, −2048, −3024...)— se llevan a −1000: si no,
    al promediar vóxeles en el borde del campo saldrían tejidos más ligeros.
    """
    pydicom = _pydicom()
    if series.problems:
        raise DicomError(series.problems[0])
    n = series.nslices
    if n == 0:
        raise DicomError("La serie no tiene cortes.")
    rows, cols = series.rows, series.cols
    slopes = {sl.slope for sl in series.slices}
    icpts = {sl.intercept for sl in series.slices}
    integral = slopes == {1.0} and all(float(i).is_integer() for i in icpts)
    hu = np.empty((n, rows, cols), dtype=np.int16 if integral else np.float32)
    cache: dict[Path, object] = {}
    for k, sl in enumerate(series.slices):
        if cancel is not None and cancel():
            raise Cancelled()
        if progress is not None:
            progress(k + 1, n, sl.path.name)
        try:
            if sl.frame >= 0:
                ds = cache.get(sl.path)
                if ds is None:
                    ds = _read_full(pydicom, sl)
                    cache.clear()
                    cache[sl.path] = ds
            else:
                ds = _read_full(pydicom, sl)
        except Exception as exc:
            raise DicomError(f"No se puede leer {sl.path.name}: {exc}") from None
        arr = _decode(ds, sl.frame, sl.path.name)
        if arr.shape != (rows, cols):
            raise DicomError(f"{sl.path.name}: tamaño de imagen distinto del resto de la serie.")
        vals = arr.astype(np.float64) * sl.slope + sl.intercept
        if sl.padding is not None:
            vals[arr == sl.padding] = AIR_FLOOR
        np.maximum(vals, AIR_FLOOR, out=vals)
        if integral:
            hu[k] = np.clip(np.rint(vals), -32768, 32767).astype(np.int16)
        else:
            hu[k] = vals.astype(np.float32)
    x0, y0, _ = series.frame_origin()
    x = x0 + np.arange(cols) * series.pixel_spacing[1]
    y = y0 + np.arange(rows) * series.pixel_spacing[0]
    z = series.z_positions
    rot = np.column_stack([series.row_dir, series.col_dir, series.normal])
    # Solo datos técnicos: nada que identifique al paciente ni la exploración (ni descripciones ni fechas).
    info = {
        "fabricante": series.manufacturer, "modelo": series.model, "kVp": series.kvp,
        "cortes": n, "filas": rows, "columnas": cols,
        "pixel_mm": [series.pixel_spacing[1], series.pixel_spacing[0]],
        "distancia_entre_cortes_mm": series.slice_spacing, "grosor_mm": series.thickness,
        "posicion_paciente": series.patient_position,
    }
    single = (series.pixel_spacing[1], series.pixel_spacing[0], series.slice_spacing)
    vol = HuVolume(hu, x, y, z, rot, info, single)
    return canonical(vol) if canonical_axes else vol


def canonical(vol: HuVolume, tol: float = AXIS_TOL) -> HuVolume:
    """Si los ejes de la matriz son los del paciente salvo orden y signo, la reordena para que coincidan.

    Así la caja CT queda alineada con los ejes x, y, z sin ningún giro. Solo
    si lo que se sale de los ejes es menor que ``tol`` (1e-5: 2,5 µm a 25 cm);
    un giro mayor, aunque sea de décimas de grado, se conserva.
    """
    r = vol.rotation
    perm = np.argmax(np.abs(r), axis=0)  # eje del paciente de cada eje de la matriz
    if not axis_aligned(r, tol):
        return vol
    signs = np.sign(r[perm, [0, 1, 2]])
    coords = [vol.x, vol.y, vol.z]
    arr = vol.hu  # ejes (z, y, x) de la matriz = (2, 1, 0)
    # Coordenadas en el sistema del paciente de cada eje de la matriz.
    pc = [coords[a] * signs[a] for a in range(3)]
    order = [None, None, None]  # eje de la matriz que va a cada eje del paciente
    for a in range(3):
        order[perm[a]] = a
    # La matriz tiene los ejes (2, 1, 0) en las posiciones numpy (0, 1, 2); el nuevo orden numpy es
    # (z_paciente, y_paciente, x_paciente) -> ejes de la matriz order[2], order[1], order[0].
    np_axis = {0: 2, 1: 1, 2: 0}
    arr = np.transpose(arr, [np_axis[order[2]], np_axis[order[1]], np_axis[order[0]]])
    new = []
    for k, pa in enumerate((2, 1, 0)):  # eje numpy k = eje del paciente pa
        c = pc[order[pa]]
        if c.size > 1 and c[-1] < c[0]:
            arr = np.flip(arr, axis=k)
            c = c[::-1]
        new.append(c)
    single = tuple(float(vol.single[order[pa]]) for pa in (0, 1, 2))
    return HuVolume(np.ascontiguousarray(arr), new[2].copy(), new[1].copy(), new[0].copy(), np.eye(3), vol.info,
                    single)


__all__ = ["AIR_FLOOR", "CT_IMAGE", "DicomError", "HuVolume", "Series", "SliceRef", "axis_aligned", "canonical",
           "read_series", "scan_folder", "voxel_edges"]
