"""Lectura de series DICOM (revisión de la 0.8): ficheros malformados o sin preámbulo, localizadores,
RescaleType, clases SOP, TAC mejorado de un marco, orientación casi axial, duplicados, un solo corte y el
relleno de fuera del campo de visión."""

import shutil

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")

from argus.ct import dicom as D  # noqa: E402
from argus.ct.dicom import AIR_FLOOR, read_series, scan_folder  # noqa: E402
from dicom_synth import linear_hu, phantom_hu, write_series  # noqa: E402


def _series(folder, **kw):
    kw.setdefault("rows", 12)
    kw.setdefault("cols", 10)
    kw.setdefault("nslices", 6)
    return write_series(folder, kw.pop("fn", linear_hu), **kw)


def _edit(path, **attrs):
    ds = pydicom.dcmread(str(path))
    for k, v in attrs.items():
        setattr(ds, k, v)
    ds.save_as(str(path), enforce_file_format=True)


def test_a_file_that_cannot_be_understood_does_not_stop_the_scan(tmp_path, monkeypatch):
    paths = _series(tmp_path / "a")
    real = D._add_slices

    def picky(s, ds, path):
        if path == paths[2]:
            int("1A")  # como un IS mal escrito en la cabecera
        return real(s, ds, path)

    monkeypatch.setattr(D, "_add_slices", picky)
    series = scan_folder(tmp_path)
    assert len(series) == 1 and series[0].ok and series[0].nslices == 5
    assert series[0].unreadable == 1 and any("no se ha podido leer" in n for n in series[0].notes)


def test_files_without_preamble_are_read(tmp_path):
    paths = _series(tmp_path / "a", fn=phantom_hu)
    ref = read_series(scan_folder(tmp_path / "a")[0])
    for p in paths:  # como los guardan algunos equipos y programas antiguos: sin «DICM» ni metadatos
        ds = pydicom.dcmread(str(p))
        del ds.file_meta
        ds.preamble = None
        pydicom.dcmwrite(str(p), ds, implicit_vr=True, little_endian=True, enforce_file_format=False)
        assert p.read_bytes()[128:132] != b"DICM"
    s = scan_folder(tmp_path / "a")[0]
    assert s.ok and all(sl.forced for sl in s.slices)
    assert np.array_equal(read_series(s).hu, ref.hu)


def test_localizers_are_left_out_or_refused(tmp_path):
    paths = _series(tmp_path / "a")
    loc = tmp_path / "a" / "LOC"
    shutil.copy2(paths[0], loc)
    _edit(loc, ImageType=["ORIGINAL", "PRIMARY", "LOCALIZER"], ImageOrientationPatient=[1, 0, 0, 0, 0, -1],
          SOPInstanceUID=pydicom.uid.generate_uid())
    s = scan_folder(tmp_path / "a")[0]
    assert s.ok and s.nslices == 6 and s.localizers == 1 and any("localización" in n for n in s.notes)
    # Una serie con solo localizadores no es un volumen.
    only = tmp_path / "b"
    only.mkdir()
    shutil.copy2(loc, only / "LOC")
    _edit(only / "LOC", SeriesInstanceUID=pydicom.uid.generate_uid())
    s = scan_folder(only)[0]
    assert not s.ok and "localización" in s.problems[0]


def test_values_that_are_not_hounsfield_units_are_refused(tmp_path):
    paths = _series(tmp_path / "a")
    for p in paths:
        _edit(p, RescaleType="US")
    s = scan_folder(tmp_path / "a")[0]
    assert not s.ok and "Hounsfield" in s.problems[0]
    paths = _series(tmp_path / "b")
    for p in paths:  # capturas de pantalla dentro de una «serie» de TAC
        _edit(p, SOPClassUID="1.2.840.10008.5.1.4.1.1.7")
    s = scan_folder(tmp_path / "b")[0]
    assert not s.ok and "No son imágenes de TAC" in s.problems[0]


def test_an_enhanced_ct_with_one_frame_is_accepted(tmp_path):
    _series(tmp_path / "a", nslices=1, enhanced=True, dz=2.5)
    s = scan_folder(tmp_path / "a")[0]
    assert s.ok and s.nslices == 1 and s.slice_spacing == pytest.approx(2.5)
    vol = read_series(s)
    assert vol.hu.shape == (1, 12, 10)


def test_a_nearly_axial_series_is_treated_as_axial(tmp_path):
    eps = 5e-6  # redondeo de la orientación en la cabecera: no es un giro
    _series(tmp_path / "a", iop=(1, eps, 0, -eps, 1, 0))
    vol = read_series(scan_folder(tmp_path / "a")[0])
    assert np.array_equal(vol.rotation, np.eye(3))
    tilt = 5e-4  # esto sí es un giro (0,03°): se conserva
    _series(tmp_path / "b", iop=(1, tilt, 0, -tilt, 1, 0))
    s = scan_folder(tmp_path / "b")[0]
    assert any("oblicuos" in n for n in s.notes) and not np.array_equal(read_series(s).rotation, np.eye(3))


def test_duplicate_files_are_used_once(tmp_path):
    paths = _series(tmp_path / "a")
    shutil.copy2(paths[3], tmp_path / "a" / "copia")
    s = scan_folder(tmp_path / "a")[0]
    assert s.ok and s.nslices == 6 and s.duplicates == 1 and any("repetido" in n for n in s.notes)


def test_a_single_slice_takes_its_thickness(tmp_path):
    _series(tmp_path / "a", nslices=1, dz=3.0)
    s = scan_folder(tmp_path / "a")[0]
    assert s.ok and s.slice_spacing == pytest.approx(3.0) and "1 corte " in s.label()
    assert any("Un solo corte" in n for n in s.notes)


def test_padding_and_values_below_air_become_air(tmp_path):
    paths = _series(tmp_path / "a", fn=phantom_hu, intercept=-1024.0)
    stored_pad = -2000
    for p in paths:
        ds = pydicom.dcmread(str(p))
        arr = ds.pixel_array.copy()
        arr[0, :] = stored_pad  # fuera del campo de visión
        arr[1, :] = -3024 + 1024  # otro relleno típico: −3024 HU
        ds.PixelData = arr.astype(np.int16).tobytes()
        ds.add_new(0x00280120, "SS", stored_pad)  # PixelPaddingValue (con signo, como los píxeles)
        ds.save_as(str(p), enforce_file_format=True)
    vol = read_series(scan_folder(tmp_path / "a")[0], canonical_axes=False)
    assert np.all(vol.hu[:, 0, :] == AIR_FLOOR) and np.all(vol.hu[:, 1, :] == AIR_FLOOR)
    assert vol.hu.min() >= AIR_FLOOR
