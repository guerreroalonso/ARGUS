"""Series DICOM sintéticas de TAC para las pruebas (se escriben con pydicom).

``write_series`` escribe un corte por fichero (o un TAC mejorado) a partir de
una función de HU en el sistema del paciente (mm), con la orientación, el
paso y la reescala que se pidan; así se sabe qué valor tiene que leer ARGUS en
cada punto.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _uid():
    from pydicom.uid import generate_uid

    return generate_uid()


def _base(sop_class: str, study_uid: str, series_uid: str, description: str, modality: str):
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    sop = _uid()
    meta.MediaStorageSOPInstanceUID = sop
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sop
    ds.Modality = modality
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SeriesDescription = description
    ds.StudyDescription = "Estudio de prueba"
    ds.StudyDate = "20260101"
    ds.Manufacturer = "ARGUS"
    ds.ManufacturerModelName = "Sintético"
    ds.KVP = 120
    ds.PatientPosition = "HFS"
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    return ds


def pixel_centers(origin, iop, spacing, rows, cols, nslices, dz, z_offsets=None):
    """Centros (nslices, rows, cols, 3) en mm del sistema del paciente."""
    row = np.asarray(iop[:3], dtype=float)
    col = np.asarray(iop[3:], dtype=float)
    nrm = np.cross(row, col)
    zs = np.arange(nslices) * dz if z_offsets is None else np.asarray(z_offsets, dtype=float)
    i = np.arange(cols)[None, None, :, None]
    j = np.arange(rows)[None, :, None, None]
    k = zs[:, None, None, None]
    return (np.asarray(origin, dtype=float) + i * spacing[1] * row + j * spacing[0] * col + k * nrm)


def write_series(folder, hu_fn, *, rows=12, cols=10, nslices=8, spacing=(2.0, 1.5), dz=3.0,
                 origin=(-7.0, -9.0, 20.0), iop=(1, 0, 0, 0, 1, 0), slope=1.0, intercept=-1024.0,
                 description="TAC de prueba", modality="CT", z_offsets=None, shift_last=None,
                 enhanced=False, shuffle=True, series_uid=None, study_uid=None, seed=0) -> list[Path]:
    """Escribe una serie; ``hu_fn(P)`` da los HU en puntos (..., 3) del paciente (mm)."""
    from pydicom.dataset import Dataset
    from pydicom.sequence import Sequence

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    series_uid = series_uid or _uid()
    study_uid = study_uid or _uid()
    centers = pixel_centers(origin, iop, spacing, rows, cols, nslices, dz, z_offsets)
    hu = np.asarray(hu_fn(centers), dtype=float)
    stored = np.rint((hu - intercept) / slope).astype(np.int16)
    row = np.asarray(iop[:3], dtype=float)
    col = np.asarray(iop[3:], dtype=float)
    nrm = np.cross(row, col)
    zs = np.arange(nslices) * dz if z_offsets is None else np.asarray(z_offsets, dtype=float)
    paths = []
    if enhanced:
        ds = _base("1.2.840.10008.5.1.4.1.1.2.1", study_uid, series_uid, description, modality)
        ds.Rows, ds.Columns = rows, cols
        ds.NumberOfFrames = nslices
        shared = Dataset()
        po = Dataset()
        po.ImageOrientationPatient = [float(v) for v in iop]
        shared.PlaneOrientationSequence = Sequence([po])
        pm = Dataset()
        pm.PixelSpacing = [float(spacing[0]), float(spacing[1])]
        pm.SliceThickness = float(dz)
        shared.PixelMeasuresSequence = Sequence([pm])
        pv = Dataset()
        pv.RescaleSlope = float(slope)
        pv.RescaleIntercept = float(intercept)
        shared.PixelValueTransformationSequence = Sequence([pv])
        ds.SharedFunctionalGroupsSequence = Sequence([shared])
        frames = []
        order = np.random.default_rng(seed).permutation(nslices) if shuffle else np.arange(nslices)
        for k in order:
            fr = Dataset()
            pp = Dataset()
            pp.ImagePositionPatient = [float(v) for v in np.asarray(origin, dtype=float) + zs[k] * nrm]
            fr.PlanePositionSequence = Sequence([pp])
            frames.append(fr)
        ds.PerFrameFunctionalGroupsSequence = Sequence(frames)
        ds.PixelData = np.ascontiguousarray(stored[order]).tobytes()
        p = folder / "enhanced.dcm"
        ds.save_as(str(p), enforce_file_format=True)
        return [p]
    order = np.random.default_rng(seed).permutation(nslices) if shuffle else np.arange(nslices)
    for n, k in enumerate(order):
        ds = _base("1.2.840.10008.5.1.4.1.1.2", study_uid, series_uid, description, modality)
        ds.Rows, ds.Columns = rows, cols
        pos = np.asarray(origin, dtype=float) + zs[k] * nrm
        if shift_last is not None and k == nslices - 1:
            pos = pos + np.asarray(shift_last, dtype=float)
        ds.ImagePositionPatient = [float(v) for v in pos]
        ds.ImageOrientationPatient = [float(v) for v in iop]
        ds.PixelSpacing = [float(spacing[0]), float(spacing[1])]
        ds.SliceThickness = float(dz)
        ds.InstanceNumber = int(k + 1)
        ds.RescaleSlope = float(slope)
        ds.RescaleIntercept = float(intercept)
        ds.PixelData = np.ascontiguousarray(stored[k]).tobytes()
        p = folder / f"IM{n:04d}"  # sin extensión, como exportan muchos equipos
        ds.save_as(str(p), enforce_file_format=True)
        paths.append(p)
    return paths


def phantom_hu(P):
    """Maniquí analítico (HU) en el sistema del paciente: agua con pulmón, grasa y hueso."""
    x, y, z = P[..., 0], P[..., 1], P[..., 2]
    hu = np.full(x.shape, 0.0)
    hu[(x - 2) ** 2 + (y + 1) ** 2 < 16] = -750.0  # pulmón
    hu[x > 6] = -100.0  # grasa
    hu[(np.abs(y - 3) < 2) & (z > 26)] = 1200.0  # hueso
    hu[(x < -5) & (y < -6)] = -1000.0  # aire
    return hu


def linear_hu(P):
    """HU que dependen de las tres coordenadas: sirve para comprobar posiciones y orientación."""
    return np.rint(10.0 * P[..., 0] + 3.0 * P[..., 1] + 0.5 * P[..., 2])
