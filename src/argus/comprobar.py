"""Comprobación de una instalación de ARGUS: ``argus --comprobar [INFORME]`` (0.17.0).

Sin preguntar nada: compila los núcleos de Numba, abre la ventana con una geometría de prueba, lee un TAC
sintético, dibuja una gráfica, mira si se pueden grabar vídeos y si hay OpenGL, y escribe un informe de texto. La
construcción de los ejecutables lo hace en cada sistema antes de empaquetarlos (``packaging/construir.py``), y a quien
le falle ARGUS se le puede pedir que lo ejecute y envíe el informe: lleva las versiones, el sistema, las carpetas de
ARGUS y el resultado de cada prueba (ningún fichero suyo).

Cada prueba termina en «OK», «FALLO» o «INFO» (lo que depende del ordenador y no de la instalación: la tarjeta
gráfica, los codificadores de vídeo). El resultado es correcto si no hay ningún FALLO ni apareció ningún aviso.
Los avisos (diálogos) que salgan se anotan y se cierran solos, y si todo tarda más de :data:`TIMEOUT_S` se escribe
el informe con lo que haya y se sale: así la comprobación no se queda esperando a nadie.
"""

from __future__ import annotations

import io
import os
import platform
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

REPORT_NAME = "argus_comprobacion.txt"
TIMEOUT_S = 900.0  # la primera compilación de Numba en un ordenador lento (o emulado) tarda minutos
OK, FAIL, INFO = "OK", "FALLO", "INFO"


@dataclass
class Result:
    name: str
    status: str  # OK, FALLO o INFO
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Report:
    header: list[str] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)  # avisos que aparecieron (y se cerraron solos)
    aborted: str = ""

    @property
    def ok(self) -> bool:
        return not self.aborted and not self.dialogs and all(r.status != FAIL for r in self.results)

    def status_of(self, name: str) -> str | None:
        return next((r.status for r in self.results if r.name == name), None)

    def text(self) -> str:
        lines = [f"ARGUS {__version__}: comprobación de la instalación", *self.header, ""]
        for r in self.results:
            took = f" ({_secs(r.seconds)})" if r.seconds >= 0.05 else ""
            lines.append(f"[{r.status:^5}] {r.name}{took}")
            lines += [f"        {ln}" for ln in r.detail.splitlines() if ln.strip()]
        for d in self.dialogs:
            lines.append(f"[AVISO] Apareció un aviso: {d}")
        if self.aborted:
            lines.append(f"[FALLO] {self.aborted}")
        lines += ["", "Resultado: " + ("todo correcto." if self.ok else "hay problemas (véanse los FALLO y AVISO).")]
        return "\n".join(lines) + "\n"


def _secs(s: float) -> str:
    return f"{s:.1f} s".replace(".", ",")


def _error_text(exc: BaseException) -> str:
    """«ValueError: mensaje» y dónde saltó (el último marco), sin la traza entera."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f" (en {Path(frames[-1].filename).name}:{frames[-1].lineno})" if frames else ""
    return f"{type(exc).__name__}: {exc}{where}"


# ---------------------------------------------------------------------------------------------------------------
# Las pruebas. Cada una devuelve (estado, detalle) o lanza una excepción (FALLO).
# ---------------------------------------------------------------------------------------------------------------
def sample_geometry_text() -> str:
    """Una caja de 4 cm con una esfera de 1 cm de radio dentro (escrita por ARGUS, como la escribiría al guardar)."""
    from .pengeom.edit import Primitive, add_primitive, compile_checked
    from .pengeom.newgeo import new_geometry
    from .pengeom.writer import write_geo_text

    doc = new_geometry("Comprobacion de ARGUS", Primitive("box", sides=(4.0, 4.0, 4.0)), material=1, name="caja")
    add_primitive(doc, compile_checked(doc), Primitive("sphere", radius=1.0), 2, container=1, name="esfera")
    return write_geo_text(doc)


def check_translations(app) -> tuple[str, str]:
    from PySide6.QtCore import QCoreApplication, QLibraryInfo

    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    cancel = QCoreApplication.translate("QPlatformTheme", "Cancel")
    if getattr(app, "_argus_translator", None) is None or cancel == "Cancel":
        return FAIL, f"No se cargan las traducciones de Qt (carpeta: {folder})."
    return OK, f"«Cancel» se lee «{cancel}»."


def check_numba() -> tuple[str, str]:
    import numpy as np

    from .pengeom import locate_fast
    from .pengeom.compiler import compile_geometry
    from .pengeom.parser import parse_geo_text
    from .render import raytrace

    if not locate_fast.HAVE_NUMBA:
        return INFO, ("Numba no está en esta instalación: ARGUS usa NumPy (el corte 2D y la vista 3D sin tarjeta "
                      "gráfica van más despacio).")
    import numba

    t0 = time.perf_counter()
    locate_fast.warmup()
    t1 = time.perf_counter()
    raytrace.warmup()
    t2 = time.perf_counter()
    geo = compile_geometry(parse_geo_text(sample_geometry_text()))
    pts = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [3.0, 0.0, 0.0]])
    dirs = np.tile([0.0, 0.0, 1.0], (3, 1))
    got = [int(b) for b in locate_fast.locate_numba(locate_fast.build_tables(geo), geo.nbodys, pts, dirs)]
    by_material = {b.material: b.index for b in geo.bodies}
    want = [by_material.get(2), by_material.get(1), geo.nbodys]  # la esfera, la caja y la envolvente
    if got != want:
        return FAIL, f"LOCATE compilado da los cuerpos {got} donde tocan {want}."
    cache = numba.config.CACHE_DIR or "junto al código de ARGUS"
    return OK, (f"Numba {numba.__version__}: LOCATE {_secs(t1 - t0)}, trazador de rayos {_secs(t2 - t1)} "
                f"(la primera vez se compilan; después salen de la caché).\n"
                f"Hilos: «{numba.threading_layer()}», {numba.get_num_threads()} a la vez. Caché: {cache}")


def check_window(app, folder: Path, pump: Callable[[float], None]) -> tuple[str, str]:
    from .ui.main_window import MainWindow

    path = folder / "comprobacion.geo"
    path.write_bytes(sample_geometry_text().encode("latin-1", errors="replace"))
    win = MainWindow()
    try:
        win.show()
        pump(0.3)
        win.open_file(str(path), with_input=False, ask=False)
        pump(1.5)
        geo = win.geo_ctrl.geo
        if win.geo_ctrl.doc is None or geo is None or not geo.ok:
            return FAIL, "La ventana no abrió la geometría de prueba."
        canvas = getattr(win.view3d, "canvas", None)
        backend = canvas.backend_name() if canvas is not None else "?"
        return OK, f"Geometría de prueba abierta: {geo.nbodys} cuerpos (con la envolvente). Vista 3D: {backend}."
    finally:
        win.close()
        pump(0.3)
        win.deleteLater()
        pump(0.1)


def check_dicom(folder: Path) -> tuple[str, str]:
    import numpy as np
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    from .ct.dicom import read_series, scan_folder

    out = folder / "tac"
    out.mkdir(exist_ok=True)
    study, series = generate_uid(), generate_uid()
    want = np.arange(2 * 6 * 5, dtype=np.int16).reshape(2, 6, 5) * 10 - 200  # HU
    for k in range(2):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = Dataset()
        ds.file_meta = meta
        ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
        ds.Modality, ds.StudyInstanceUID, ds.SeriesInstanceUID = "CT", study, series
        ds.SeriesDescription = "Comprobacion de ARGUS"
        ds.Rows, ds.Columns = want.shape[1], want.shape[2]
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
        ds.ImagePositionPatient = [-2.0, -2.5, 3.0 * k]
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.PixelSpacing, ds.SliceThickness, ds.InstanceNumber = [1.0, 1.0], 3.0, k + 1
        ds.RescaleSlope, ds.RescaleIntercept = 1.0, -1024.0
        ds.PixelData = (want[k] + 1024).astype(np.int16).tobytes()
        ds.save_as(str(out / f"IM{k:04d}"), enforce_file_format=True)
    found = scan_folder(out)
    if len(found) != 1:
        return FAIL, f"Se esperaba una serie de TAC y se encontraron {len(found)}."
    values = np.asarray(read_series(found[0]).hu)  # (cortes, filas, columnas)
    if values.shape != want.shape or not np.array_equal(values.astype(np.int64), want.astype(np.int64)):
        return FAIL, f"Los HU leídos no son los escritos (forma {values.shape}, se esperaba {want.shape})."
    return OK, f"pydicom {pydicom.__version__}: dos cortes escritos y leídos como un TAC de {want.size} vóxeles."


def check_plots() -> tuple[str, str]:
    import matplotlib
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(3, 2))
    FigureCanvasQTAgg(fig)
    ax = fig.add_subplot()
    ax.plot([0.0, 1.0, 2.0], [0.0, 1.0, 0.5], label="dosis")
    ax.set_xlabel("z (cm)")
    ax.legend(framealpha=1.0)  # opaca: el formato EPS no admite transparencias (avisaría)
    sizes = []
    for fmt in ("png", "pdf", "svg", "eps", "jpg", "tif"):  # los de ARGUS y los de la barra de la gráfica
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt)
        sizes.append(f"{fmt.upper()} {buf.tell() // 1024 + 1} kB")
    return OK, f"Matplotlib {matplotlib.__version__}: una gráfica exportada a " + ", ".join(sizes) + "."


def check_gif() -> tuple[str, str]:
    import PIL
    from PIL import Image

    from .ui.results.shower_view import gif_durations

    frames = [Image.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 128, 255), (255, 200, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=gif_durations(3, 15.0),
                   loop=0)
    buf.seek(0)
    with Image.open(buf) as im:
        n = getattr(im, "n_frames", 1)
    if n != 3:
        return FAIL, f"El GIF de prueba tiene {n} imágenes en vez de 3."
    return OK, f"Pillow {PIL.__version__}: un GIF animado de 3 imágenes escrito y leído."


def check_video() -> tuple[str, str]:
    try:
        from PySide6.QtMultimedia import QMediaFormat
    except ImportError as exc:  # falta en el paquete: es un fallo de la instalación
        return FAIL, f"Falta Qt Multimedia ({exc}): no se podrán grabar vídeos de las cascadas."
    from .ui.results.shower_view import mp4_problem

    problem = mp4_problem()
    if problem:
        return INFO, f"{problem}. Los vídeos de las cascadas se pueden guardar como GIF."
    fmt = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
    codecs = ", ".join(c.name for c in fmt.supportedVideoCodecs(QMediaFormat.ConversionMode.Encode))
    return OK, f"Se pueden grabar vídeos MP4 (codificadores: {codecs})."


def check_opengl() -> tuple[str, str]:
    from .ui.gpu_tracer import probe_gl

    ok, desc = probe_gl()
    if ok:
        return INFO, f"OpenGL 4.1: sí ({desc}). La vista 3D usa la tarjeta gráfica."
    return INFO, f"OpenGL 4.1: no ({desc}). La vista 3D traza los rayos en la CPU (más despacio)."


# ---------------------------------------------------------------------------------------------------------------
def _header(app) -> list[str]:
    import PySide6
    from PySide6.QtCore import qVersion

    from .core.userdata import user_cache_dir, user_data_dir
    from .ui.errors import log_path

    frozen = getattr(sys, "frozen", False)
    return [
        f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Sistema: {platform.platform()} ({platform.machine() or '?'})",
        f"Python {platform.python_version()}; ARGUS {'ejecutable (PyInstaller)' if frozen else 'desde su código'}: "
        f"{sys.executable}",
        f"Qt {qVersion()} (PySide6 {PySide6.__version__}), plataforma «{app.platformName()}»",
        f"Datos del usuario: {user_data_dir()}",
        f"Cachés: {user_cache_dir()}",
        f"Registro de errores: {log_path()}",
    ]


def _dialog_text(w) -> str:
    title = w.windowTitle() or type(w).__name__
    parts = [title]
    for attr in ("text", "informativeText"):
        fn = getattr(w, attr, None)
        if callable(fn):
            try:
                t = fn()
            except (RuntimeError, TypeError):
                t = ""
            if t:
                parts.append(" ".join(str(t).split()))
    return ": ".join(parts)[:500]


class _DialogCloser:
    """Cierra los diálogos que aparezcan (anotando lo que decían): nadie va a contestarlos."""

    def __init__(self, report: Report):
        from PySide6.QtCore import QTimer

        self.report = report
        self.timer = QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def tick(self) -> None:
        from PySide6.QtWidgets import QApplication, QDialog

        candidates = [QApplication.activeModalWidget()]
        candidates += [w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]
        seen: set[int] = set()
        for w in candidates:
            if w is None or id(w) in seen or not w.isVisible():
                continue
            seen.add(id(w))
            self.report.dialogs.append(_dialog_text(w))
            if isinstance(w, QDialog):
                w.reject()
            else:
                w.close()

    def stop(self) -> None:
        self.timer.stop()


def default_report_path() -> Path:
    """Donde se escribe el informe si no se dice: la carpeta actual si se puede escribir; si no, la temporal."""
    cwd = Path.cwd()
    return (cwd if os.access(cwd, os.W_OK) else Path(tempfile.gettempdir())) / REPORT_NAME


def write_report(report: Report, path: Path) -> Path:
    try:
        path.write_text(report.text(), encoding="utf-8")
        return path
    except OSError:
        alt = Path(tempfile.gettempdir()) / REPORT_NAME
        alt.write_text(report.text(), encoding="utf-8")
        return alt


def run_checks(app, report_path: str | os.PathLike | None = None, timeout_s: float = TIMEOUT_S,
               hard_exit: bool = True) -> int:
    """Hace las pruebas, escribe el informe y devuelve 0 (todo correcto) o 1.

    ``hard_exit``: si se pasa de ``timeout_s``, escribe lo que haya y sale del proceso (código 3); las pruebas de
    ARGUS lo llaman sin eso.
    """
    from PySide6.QtWidgets import QApplication

    path = Path(report_path) if report_path else default_report_path()
    report = Report()
    report.header = _header(app)
    done = threading.Event()

    def give_up() -> None:
        if done.is_set():
            return
        report.aborted = f"La comprobación no terminó en {int(timeout_s)} s."
        written = write_report(report, path)
        print(f"ARGUS: comprobación sin terminar; informe en {written}", file=sys.__stdout__ or sys.stdout)
        if hard_exit:
            os._exit(3)

    watchdog = threading.Timer(timeout_s, give_up)
    watchdog.daemon = True
    watchdog.start()

    def pump(seconds: float) -> None:
        end = time.perf_counter() + seconds
        while True:
            QApplication.processEvents()
            if time.perf_counter() >= end:
                break
            time.sleep(0.01)

    closer = _DialogCloser(report)

    def run(name: str, fn: Callable[[], tuple[str, str]]) -> None:
        t0 = time.perf_counter()
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 - cada prueba se anota y se sigue con la siguiente
            status, detail = FAIL, _error_text(exc)
        report.results.append(Result(name, status, detail, time.perf_counter() - t0))
        pump(0.05)

    old_settings = os.environ.get("ARGUS_SETTINGS")
    try:
        with tempfile.TemporaryDirectory(prefix="argus-comprobacion-") as tmp:
            folder = Path(tmp)
            # Unos ajustes aparte: la geometría de prueba no queda en los ficheros recientes del usuario, ni el
            # tamaño de esta ventana en el de la suya.
            os.environ["ARGUS_SETTINGS"] = str(folder / "ajustes.ini")
            run("Traducciones de Qt al español", lambda: check_translations(app))
            run("Numba (LOCATE y trazador de rayos compilados)", check_numba)
            run("Ventana de ARGUS con una geometría", lambda: check_window(app, folder, pump))
            run("Lectura de un TAC (DICOM)", lambda: check_dicom(folder))
            run("Gráficas (Matplotlib)", check_plots)
            run("Animaciones GIF (Pillow)", check_gif)
            run("Vídeos (Qt Multimedia)", check_video)
            run("Tarjeta gráfica (OpenGL)", check_opengl)
    finally:
        done.set()
        watchdog.cancel()
        closer.stop()
        if old_settings is None:
            os.environ.pop("ARGUS_SETTINGS", None)
        else:
            os.environ["ARGUS_SETTINGS"] = old_settings
    written = write_report(report, path)
    print(f"ARGUS: comprobación {'correcta' if report.ok else 'con problemas'}; informe en {written}",
          file=sys.__stdout__ or sys.stdout)
    return 0 if report.ok else 1


__all__ = ["INFO", "FAIL", "OK", "REPORT_NAME", "Report", "Result", "default_report_path", "run_checks",
           "sample_geometry_text"]
