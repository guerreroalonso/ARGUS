"""Visor de la cascada: las primeras lluvias del caso simuladas con el penhmain del usuario, en 3D sobre la
geometría y animadas en el tiempo (0.13.0, ADR-017).

Las trayectorias son las que escribe penhmain (LTRACK) en las ejecuciones de la cascada (``penhan.cascade``):
``results.shower`` reconstruye a qué lluvia pertenece cada una, su generación y su tiempo de vuelo. Aquí se eligen
las lluvias (las primeras N o una sola), las partículas, el color (partícula, energía, lluvia o generación) y cómo
se ve la geometría (translúcida, opaca u oculta, por material), se anima el tiempo real de vuelo y se exporta la
imagen o la animación (GIF o MP4) para clase. Mientras la cascada se simula, se ve cómo avanza y las lluvias se
añaden a medida que terminan las ejecuciones de penhmain.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEventLoop, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.numfmt import fmt_int, plural
from ...penhan import cascade as cs
from ...render.track_layer import (
    ALL_PARTICLES,
    COLOR_MODES,
    PALETTE,
    TrackLayerData,
    TrackStyle,
    energy_lut,
    format_time,
    layer_from_showers,
)
from ...results.data import PARTICLE_NAMES, Tracks
from ...results.display import ENERGY_SCALE, DisplayOptions, auto_energy
from ...results.shower import ShowerSet, keep_cascade, load_cascade
from ..flow_layout import FlowLayout, group
from ..settings import app_settings
from .style import INK_2, particle_color

CASCADE_KEY = "cascada"  # resultado «Cascada de partículas» del árbol de la ventana de resultados
CASCADE_TITLE = "Cascada de partículas (3D)"
GAS_DENSITY = 0.01  # g/cm³: por debajo, gas (aire…): oculto al empezar, para que no tape las trayectorias
BASE_SECONDS = 10.0  # lo que dura la animación entera a velocidad ×1
LOG_RATIO = 1000.0  # escala logarítmica del tiempo: tres décadas por debajo del final
SPEEDS = [("×0,1", 0.1), ("×0,25", 0.25), ("×0,5", 0.5), ("×1", 1.0), ("×2", 2.0), ("×4", 4.0)]
TICK_MS = 30


# ---------------------------------------------------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class CascadeData:
    """Lo que enseña el visor: la cascada del caso (la más reciente o la que se está simulando) y su geometría."""

    folder: Path  # la carpeta de resultados (la del .in)
    in_path: Path | None
    info: cs.CascadeInfo | None = None
    cascades: list = field(default_factory=list)  # todas las del caso en la carpeta (CascadeInfo), la más reciente 1.ª
    showers: ShowerSet | None = None
    layer: TrackLayerData | None = None
    case: object | None = None  # results.case.CaseGeometry
    primary: int | None = None  # KPAR de la fuente (None: radiactiva o espacio de fases)
    source_label: str = ""
    stale: str | None = None  # por qué ya no es la del caso de ahora (o None)
    done: int = 0  # lluvias de las ejecuciones terminadas (y de las que se sabe la lluvia de cada trayectoria)
    total: int = 0  # lluvias pedidas
    unverified: int = 0  # lluvias simuladas de las que aún no se sabe la lluvia de cada trayectoria (faltan cortes)

    @property
    def key(self) -> tuple:
        return (os.path.normcase(str(self.folder)), os.path.normcase(str(self.in_path)),
                os.path.normcase(str(self.info.path)) if self.info is not None else None)


def source_description(doc) -> tuple[int | None, str]:
    """(KPAR de las primarias o None, «electrones de 6 MeV»…) de la fuente de un .in."""
    src = doc.model.source
    if src.from_psf:
        return None, "partículas del espacio de fases"
    if src.radioactive:
        return None, f"desintegraciones de {src.nuclide}" if src.nuclide else "fuente radiactiva"
    k = int(src.kparp)
    name = PARTICLE_NAMES.get(k, f"KPAR {k}")

    def energies(values: list[float]) -> str:
        u = auto_energy(max(values))
        texts = [f"{v * ENERGY_SCALE[u]:.3g}".replace(".", ",") for v in values]
        return (", ".join(texts[:-1]) + " y " + texts[-1] if len(texts) > 1 else texts[0]) + f" {u}"

    if src.spectrum:
        top = max((e for e, _p in src.spectrum), default=0.0)
        return k, f"{name} (espectro hasta {energies([top])})" if top > 0 else f"{name} (espectro)"
    if src.lines:
        main = sorted(src.lines, key=lambda ep: -ep[1])[:3]
        vals = sorted(e for e, _p in main if e > 0)
        more = " (y otras)" if len(src.lines) > 3 else ""
        return k, f"{name} de {energies(vals)}{more}" if vals else f"{name} (líneas)"
    e = float(src.energy)
    if e > 0:
        return k, f"{name} de {energies([e])}"
    return k, name


def cascade_stale(info: cs.CascadeInfo, in_path: Path | None, exe: Path | None, doc=None) -> str | None:
    """Por qué la cascada ya no corresponde al caso (el .in, la geometría, los materiales o penhmain han cambiado
    después de simularla), o None si es la del caso o no se puede saber."""
    if in_path is None or not in_path.is_file() or os.path.normcase(info.case) != os.path.normcase(in_path.name):
        return None
    if info.repeats_showers:
        return ("Esta cascada se simuló con ARGUS 0.13.0, que con las semillas por defecto repetía en la segunda "
                "ejecución las lluvias de la primera: simúlela de nuevo.")
    if exe is None or not Path(exe).is_file():
        cand = info.run_paths[0] / info.exe if info.runs and info.exe else None
        exe = cand if cand is not None and cand.is_file() else None
    if exe is None:
        return None
    try:
        if doc is None:
            from ...penhan.inputfile import parse_in_file

            doc = parse_in_file(in_path)
        same = cs.cascade_matches(info, in_path, doc, exe)
    except (OSError, ValueError):
        return None
    if not same:
        return ("El caso ha cambiado después de simular esta cascada (el .in, la geometría, los materiales o "
                "penhmain): simúlela de nuevo para ver la de ahora.")
    return None


def load_cascade_data(folder: str | os.PathLike, in_path: str | os.PathLike | None, *,
                      prefer: str | os.PathLike | None = None, case=None, exe: str | os.PathLike | None = None
                      ) -> CascadeData:
    """La cascada del caso de ``folder`` (lee las ejecuciones terminadas; en otro hilo). ``prefer``: la carpeta de
    una cascada concreta (la que se está simulando); ``case``: la geometría ya leída (se vuelve a leer si ha
    cambiado); ``exe``: el penhmain con que se simularía ahora (para saber si la cascada es la del caso)."""
    from ...results.case import is_current, load_case_geometry

    folder = Path(folder)
    in_path = Path(in_path) if in_path else None
    infos = cs.find_cascades(folder, in_path.name if in_path is not None else None)
    info = cs.read_manifest(prefer) if prefer else None
    if info is None and in_path is not None:
        job = cs.active_job(in_path)
        st = job.status if job is not None else None
        if st is not None and st.folder is not None:
            info = cs.read_manifest(st.folder)
    if info is None and infos:
        info = infos[0]
    if in_path is not None and (case is None or not is_current(case)):
        case = load_case_geometry(in_path)
    data = CascadeData(folder, in_path, info, infos, case=case)
    doc = None
    if in_path is not None and in_path.is_file():
        from ...penhan.inputfile import parse_in_file

        try:
            doc = parse_in_file(in_path)
            data.primary, data.source_label = source_description(doc)
        except (OSError, ValueError):
            doc = None
    if info is None:
        return data
    with keep_cascade(info.path):  # (se recorren sus ejecuciones tres veces: que no se expulsen entre medias)
        data.done, data.total, _states = cs.cascade_progress(info)
        data.unverified = cs.unverified_showers(info)
        s = load_cascade(info, data.primary)
    if s.count:
        data.showers = s
        data.layer = layer_from_showers(s).prepare()  # (aquí, en otro hilo: al verla, la ventana no se para)
    data.stale = cascade_stale(info, in_path, Path(exe) if exe else None, doc)
    return data


def gas_materials(case) -> set[int]:
    """Materiales que son un gas (aire…), por la densidad de su .mat (o, sin ella, por su nombre)."""
    from ...penhan.materials import read_mat_header

    out: set[int] = set()
    if case is None:
        return out
    base = Path(case.input_path).parent
    for i, name in enumerate(case.material_files, start=1):
        head = read_mat_header(base / name) if name else None
        dens = head.density if head is not None else None
        text = f"{name} {head.name if head is not None else ''}".lower()
        if (dens is not None and dens < GAS_DENSITY) or (dens is None and any(w in text for w in (
                "air", "aire", "gas", "vacuum", "vacio", "vacío"))):
            out.add(i)
    return out


def default_visibility(case) -> dict[int, int]:
    """Cómo se ve cada material al empezar: translúcido (se ven las trayectorias de dentro) y los gases, ocultos."""
    from ...render.scene3d import HIDDEN, TRANSLUCENT

    geo = case.geo if case is not None else None
    if geo is None or not geo.ok:
        return {}
    gas = gas_materials(case)
    return {m: (HIDDEN if m in gas else TRANSLUCENT) for m in geo.used_materials()}


def shower_spans(layer: TrackLayerData) -> np.ndarray:
    """Cuánto dura (ns) cada lluvia: el tiempo de su último punto."""
    return layer.shower_ends()


def wants_log_time(showers: ShowerSet | None, layer: TrackLayerData) -> bool:
    """¿Escala de tiempo logarítmica al empezar? Sí cuando, en lineal, casi todo pasaría en un instante:

    * si el 99 % de las trayectorias ha terminado antes de la cuarta parte de la animación (unos pocos neutrones lentos
      o fotones que salen lejos alargan el resto: con protones de hasta 138 MeV en agua, el 99 % acaba antes de 3,2 ns
      y lo último, a los 19 ns), o
    * si la mitad termina en el primer 2 % (una fuente de neutrones, que tardan microsegundos en frenarse).

    Con electrones o fotones en un maniquí, lo último dura poco más que el resto: en lineal."""
    if showers is None or not showers.count or layer.t_max <= 0:
        return False
    ends = showers.time[showers.starts[1:] - 1].astype(np.float64)
    if not len(ends):
        return False
    t_end = float(layer.t_max)
    p50, p99 = np.percentile(ends, [50.0, 99.0])
    return bool(p99 < 0.25 * t_end or p50 < 0.02 * t_end)


def auto_alpha(n_showers: int) -> float:
    """Opacidad de las líneas: con muchas lluvias a la vez, más transparentes (se ve dónde se juntan)."""
    if n_showers <= 30:
        return 0.85
    return max(0.25, 0.85 * (30.0 / n_showers) ** 0.35)


def time_of(u: float, t_end: float, log: bool) -> float:
    """Instante (ns) de la posición ``u`` (0…1) de la barra de tiempo; en escala logarítmica, tres décadas."""
    u = min(max(u, 0.0), 1.0)
    if t_end <= 0:
        return 0.0
    if not log:
        return u * t_end
    lo = t_end / LOG_RATIO
    return lo * ((1.0 + LOG_RATIO) ** u - 1.0)


def position_of(t: float, t_end: float, log: bool) -> float:
    """La inversa de :func:`time_of`."""
    if t_end <= 0:
        return 0.0
    t = min(max(t, 0.0), t_end)
    if not log:
        return t / t_end
    lo = t_end / LOG_RATIO
    return math.log1p(t / lo) / math.log1p(LOG_RATIO)


# ---------------------------------------------------------------------------------------------------------------------
# Exportar la animación
# ---------------------------------------------------------------------------------------------------------------------

def _to_pil(img: QImage):
    from PIL import Image

    rgb = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = rgb.width(), rgb.height()
    data = bytes(rgb.constBits())
    return Image.frombuffer("RGB", (w, h), data, "raw", "RGB", rgb.bytesPerLine(), 1).copy()


def write_gif(path: str | os.PathLike, frames: Iterable[QImage], fps: float, n: int | None = None) -> int:
    """GIF animado (Pillow): 256 colores por imagen, sin tramado (las líneas se ven nítidas). ``n``: cuántas
    imágenes son (para repartir la duración; si no se dice, se juntan antes). Devuelve las imágenes escritas."""
    from PIL import Image

    quant = getattr(getattr(Image, "Quantize", None), "FASTOCTREE", 2)
    dither = getattr(getattr(Image, "Dither", None), "NONE", 0)
    count = [0]

    def paletted() -> Iterator:
        for img in frames:
            count[0] += 1
            yield _to_pil(img).quantize(colors=255, method=quant, dither=dither)

    it = paletted()
    first = next(it, None)
    if first is None:
        return 0
    rest = it if n is not None else list(it)
    total = int(n) if n is not None else 1 + len(rest)  # type: ignore[arg-type]
    first.save(str(path), save_all=True, append_images=rest, duration=gif_durations(max(total, 1), fps), loop=0,
               disposal=1, optimize=False)
    return count[0]


def gif_durations(n: int, fps: float) -> list[int]:
    """Lo que dura cada imagen de un GIF (ms): el formato guarda centésimas, así que se reparten para que la suma
    sea la duración pedida (15 imágenes por segundo: 70, 60, 70, 70, 60… y no 66 ms cada una, que el GIF guarda como
    60: un 10 % más deprisa). Como mucho 50 por segundo (20 ms: algunos visores no admiten menos)."""
    total_cs = n * 100.0 / max(float(fps), 1e-6)
    cuts = [int(round(k * total_cs / n)) for k in range(n + 1)]
    return [max(10 * (b - a), 20) for a, b in zip(cuts, cuts[1:])]


def mp4_problem() -> str | None:
    """Por qué no se puede grabar un MP4 aquí (o None)."""
    try:
        from PySide6.QtMultimedia import QMediaFormat
    except ImportError as exc:  # pragma: no cover - depende de la instalación
        return f"falta Qt Multimedia ({exc})"
    fmt = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
    codecs = fmt.supportedVideoCodecs(QMediaFormat.ConversionMode.Encode)
    if not any(c in codecs for c in (QMediaFormat.VideoCodec.H264, QMediaFormat.VideoCodec.MPEG4)):
        return "Qt Multimedia no tiene aquí un codificador de vídeo MP4 (H.264 o MPEG-4)"
    return None


def write_mp4(path: str | os.PathLike, frames: Iterable[QImage], fps: float, timeout: float = 120.0) -> int:
    """Vídeo MP4 con Qt Multimedia (H.264 si lo hay; si no, MPEG-4). Las imágenes deben tener todas el mismo
    tamaño (par). Devuelve las imágenes escritas; ``RuntimeError`` si la grabación falla."""
    from PySide6.QtMultimedia import (
        QMediaCaptureSession,
        QMediaFormat,
        QMediaRecorder,
        QVideoFrame,
        QVideoFrameInput,
    )

    fmt = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
    codecs = fmt.supportedVideoCodecs(QMediaFormat.ConversionMode.Encode)
    codec = QMediaFormat.VideoCodec.H264 if QMediaFormat.VideoCodec.H264 in codecs else QMediaFormat.VideoCodec.MPEG4
    fmt.setVideoCodec(codec)
    inp = QVideoFrameInput()
    session = QMediaCaptureSession()
    session.setVideoFrameInput(inp)
    rec = QMediaRecorder()
    session.setRecorder(rec)
    rec.setMediaFormat(fmt)
    rec.setVideoFrameRate(float(fps))
    rec.setQuality(QMediaRecorder.Quality.VeryHighQuality)
    rec.setOutputLocation(QUrl.fromLocalFile(str(Path(path).resolve())))
    errors: list[str] = []
    rec.errorOccurred.connect(lambda _e, text: errors.append(text))
    ready = [True]
    inp.readyToSendVideoFrame.connect(lambda: ready.__setitem__(0, True))
    rec.record()
    app = QApplication.instance()
    period = 1.0e6 / float(fps)  # µs
    sent = 0
    t0 = time.monotonic()
    for img in frames:
        frame = QVideoFrame(img.convertToFormat(QImage.Format.Format_RGB32))
        frame.setStartTime(int(round(sent * period)))
        frame.setEndTime(int(round((sent + 1) * period)))
        if hasattr(frame, "setStreamFrameRate"):  # Qt 6.8: sin ella, el codificador avisa y toma la de por defecto
            frame.setStreamFrameRate(float(fps))
        while not errors:
            if inp.sendVideoFrame(frame):
                break
            ready[0] = False
            t1 = time.monotonic()
            while not ready[0] and not errors and time.monotonic() - t1 < 10.0:
                app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
                time.sleep(0.002)
            if not ready[0] and not errors:
                errors.append("el codificador de vídeo no acepta más imágenes")
        if errors:
            break
        sent += 1
        app.processEvents()
    rec.stop()
    while rec.recorderState() != QMediaRecorder.RecorderState.StoppedState and time.monotonic() - t0 < timeout:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)
    if errors:
        raise RuntimeError(errors[0])
    if rec.recorderState() != QMediaRecorder.RecorderState.StoppedState:
        raise RuntimeError("la grabación del vídeo no ha terminado")
    return sent


class ExportAnimationDialog(QDialog):
    """Formato, duración, imágenes por segundo y anchura de la animación que se exporta."""

    def __init__(self, parent, max_width: int, seconds: float, mp4_note: str | None, aspect: float = 0.6):
        super().__init__(parent)
        self._aspect = max(float(aspect), 0.05)
        self.setWindowTitle("Exportar la animación")
        form = QFormLayout(self)
        self.format = QComboBox()
        self.format.addItem("Vídeo MP4 (para presentaciones)", "mp4")
        self.format.addItem("GIF animado (para una página o un documento)", "gif")
        if mp4_note:
            self.format.model().item(0).setEnabled(False)
            self.format.setItemData(0, f"No disponible: {mp4_note}", Qt.ItemDataRole.ToolTipRole)
            self.format.setCurrentIndex(1)
        form.addRow("Formato:", self.format)
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(1.0, 120.0)
        self.seconds.setDecimals(1)
        self.seconds.setSuffix(" s")
        self.seconds.setValue(min(max(seconds, 1.0), 120.0))
        self.seconds.setToolTip("Lo que dura el vídeo: toda la animación (de t = 0 al final), a la velocidad que se "
                                "elija aquí")
        form.addRow("Duración:", self.seconds)
        self.fps = QSpinBox()
        self.fps.setRange(5, 60)
        form.addRow("Imágenes por segundo:", self.fps)
        self.px_width = QSpinBox()
        self.px_width.setRange(160, max(int(max_width), 160))
        self.px_width.setSuffix(" px")
        self.px_width.setToolTip("Como mucho, el tamaño de la vista 3D (agrándela para exportar más grande)")
        form.addRow("Anchura:", self.px_width)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {INK_2};")
        form.addRow(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._max_width = int(max_width)
        self.format.currentIndexChanged.connect(self._format_changed)
        for w in (self.seconds, self.fps, self.px_width):
            w.valueChanged.connect(self._update_note)
        self._format_changed()

    def _format_changed(self) -> None:
        gif = self.format.currentData() == "gif"
        self.fps.setMaximum(50 if gif else 60)  # un GIF guarda centésimas: como mucho 50 imágenes por segundo
        self.fps.setValue(15 if gif else 25)
        self.px_width.setValue(min(self._max_width, 800) if gif else self._max_width)
        self._update_note()

    def _update_note(self) -> None:
        n = int(round(self.seconds.value() * self.fps.value()))
        text = f"{plural(n, 'imagen', 'imágenes')}."
        if self.format.currentData() == "gif":
            w = self.px_width.value()
            mb = n * w * max(int(w * self._aspect), 1) / 1e6  # Pillow las tiene todas en memoria (1 byte/píxel)
            text += (f" Un GIF grande ocupa mucho (necesita unos {fmt_int(round(mb))} MB de memoria al guardarlo): "
                     "para una presentación, mejor el vídeo.")
        self.note.setText(text)

    def values(self) -> tuple[str, float, int, int]:
        return self.format.currentData(), float(self.seconds.value()), int(self.fps.value()), int(self.px_width.value())


# ---------------------------------------------------------------------------------------------------------------------
# La vista
# ---------------------------------------------------------------------------------------------------------------------

def _fmt_int(n: int) -> str:
    return fmt_int(n)


def _same_folder(a, b) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


class ShowerView(QWidget):
    """La cascada en 3D sobre la geometría, con su animación (una por ventana de resultados)."""

    changed = Signal()  # otras lluvias o partículas visibles (para dibujarlas sobre los cortes 2D)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings or app_settings()
        self.data: CascadeData | None = None
        self.title = ""
        self.opts: DisplayOptions | None = None
        self.track_style = TrackStyle()
        self.view3d = None
        self._session_key = None
        self._visibility: dict[int, int] = {}
        self._u = 1.0  # posición de la barra de tiempo (0…1)
        self._playing = False
        self._last_tick = 0.0
        self._loader = None
        self._token = 0
        self._loading = False
        self._job_seen: tuple | None = None
        self._follow_all = True  # «las primeras N» con N = todas: al llegar más lluvias, se ven también
        self._particles_off: set[int] = set()  # las que ha desmarcado el usuario (en este caso)
        self._chosen: Path | None = None  # la cascada elegida en la lista (None: la más reciente o la que se simula)
        self._reload_after_export: Path | None | bool = False
        self._render_note = ""  # p. ej. «las trayectorias se dibujan sin GPU»
        self._exporting = False
        self._faint_hidden = False  # lo que tapa la geometría opaca, tenue (si no, no se ve)
        self._gen_key: tuple | None = None
        self._gen_max = 0
        self._mat_actions: dict[int, dict[int, QAction]] = {}
        self._build()
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(TICK_MS)
        self.anim_timer.timeout.connect(self._tick)
        self.job_timer = QTimer(self)
        self.job_timer.setInterval(500)
        self.job_timer.timeout.connect(self._poll_job)

    # -- construcción ------------------------------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        # Página sin trayectorias: qué pasa y cómo simular la cascada.
        self.msg_page = QWidget()
        ml = QVBoxLayout(self.msg_page)
        ml.addStretch(1)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg.setTextFormat(Qt.TextFormat.RichText)
        self.msg.setMinimumWidth(320)
        ml.addWidget(self.msg)
        row = QHBoxLayout()
        row.addStretch(1)
        self.start_label = QLabel("Lluvias:")
        row.addWidget(self.start_label)
        self.start_n = QSpinBox()
        self.start_n.setRange(1, cs.MAX_SHOWERS)
        self.start_n.setSingleStep(100)
        self.start_n.setToolTip("Cuántas lluvias (partículas primarias) se simulan para verlas")
        row.addWidget(self.start_n)
        self.start_btn = QPushButton("Simular la cascada")
        self.start_btn.setToolTip("Simula las primeras lluvias del caso con su penhmain, en la carpeta «cascada» "
                                  "junto al .in (en segundo plano)")
        self.start_btn.clicked.connect(lambda: self.start_cascade(self.start_n.value()))
        row.addWidget(self.start_btn)
        row.addStretch(1)
        ml.addLayout(row)
        ml.addStretch(2)
        self.pages.addWidget(self.msg_page)
        # Página de la cascada.
        self.main_page = QWidget()
        mp = QVBoxLayout(self.main_page)
        mp.setContentsMargins(4, 2, 4, 0)
        mp.setSpacing(2)
        # Las filas de controles siguen en otra línea si no caben (pantallas al 125 o 150 %).
        r1 = FlowLayout()
        self.mode_first = QRadioButton("las primeras")
        self.mode_one = QRadioButton("solo la")
        self.mode_first.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.mode_first)
        grp.addButton(self.mode_one)
        self.first_n = QSpinBox()
        self.first_n.setRange(1, 1)
        digits = self.fontMetrics().horizontalAdvance("88 888") + 34
        self.first_n.setMinimumWidth(digits)
        self.first_n.setToolTip("Cuántas lluvias se ven a la vez (desde la primera)")
        self.first_of = QLabel("")
        self.one_k = QSpinBox()
        self.one_k.setRange(1, 1)
        self.one_k.setMinimumWidth(digits)
        self.one_k.setToolTip("Qué lluvia se ve (de una en una)")
        self.prev_btn = QToolButton()
        self.prev_btn.setText("◀")
        self.prev_btn.setToolTip("La lluvia anterior")
        self.next_btn = QToolButton()
        self.next_btn.setText("▶")
        self.next_btn.setToolTip("La lluvia siguiente")
        r1.addWidget(group(QLabel("Lluvias:"), self.mode_first, self.first_n, self.first_of))
        r1.addSpacing(10)
        r1.addWidget(group(self.mode_one, self.one_k, self.prev_btn, self.next_btn))
        r1.addSpacing(12)
        self.color = QComboBox()
        for k, text in COLOR_MODES.items():
            self.color.addItem(text, k)
        self.color.setToolTip("Por partícula; por energía (en cada punto); por lluvia; por generación (las "
                              "primarias, sus hijas, sus nietas…)")
        r1.addWidget(group(QLabel("Color:"), self.color))
        r1.addSpacing(12)
        self.line_width = QDoubleSpinBox()
        self.line_width.setRange(0.5, 8.0)
        self.line_width.setSingleStep(0.5)
        self.line_width.setDecimals(1)
        self.line_width.setSuffix(" px")
        self.line_width.setValue(self.track_style.line_width)
        r1.addWidget(group(QLabel("Grosor:"), self.line_width))
        r1.addSpacing(12)
        self.geo_btn = QToolButton()
        self.geo_btn.setText("Geometría")
        self.geo_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.geo_btn.setToolTip("Cómo se ve cada material: translúcido (se ven las trayectorias de dentro), opaco u "
                                "oculto")
        self.geo_menu = QMenu(self.geo_btn)
        self.geo_btn.setMenu(self.geo_menu)
        self.fit_btn = QToolButton()
        self.fit_btn.setText("Encuadrar")
        self.fit_btn.setToolTip("Acercar la cámara a las trayectorias que se ven (las lluvias y partículas elegidas)")
        r1.addWidget(group(self.geo_btn, self.fit_btn))
        r1.addStretch(1)
        mp.addLayout(r1)
        r2 = FlowLayout()
        checks = QWidget()
        self.checks_box = FlowLayout(checks, spacing=10)
        r2.addWidget(group(QLabel("Partículas:"), checks))  # la etiqueta, con sus casillas
        r2.addStretch(1)
        self.shower_info = QLabel("")
        self.shower_info.setStyleSheet(f"color: {INK_2};")
        r2.addWidget(self.shower_info)
        mp.addLayout(r2)
        self.view_holder = QVBoxLayout()
        self.view_holder.setContentsMargins(0, 0, 0, 0)
        mp.addLayout(self.view_holder, 1)
        # Animación.
        r3 = FlowLayout()
        self.play_btn = QPushButton("▶ Reproducir")
        self.play_btn.setToolTip("Ver cómo se desarrolla la cascada en el tiempo (tiempo real de vuelo de cada "
                                 "partícula)")
        self.all_btn = QPushButton("Todo")
        self.all_btn.setToolTip("Ver las trayectorias enteras (sin animación)")
        r3.addWidget(group(self.play_btn, self.all_btn))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(160)
        self.slider.setRange(0, 1000)
        self.slider.setValue(1000)
        self.slider.setToolTip("Instante de la animación")
        r3.addWidget(self.slider, 1)
        self.time_label = QLabel("")
        self.time_label.setMinimumWidth(150)
        r3.addWidget(self.time_label)
        self.speed = QComboBox()
        for text, v in SPEEDS:
            self.speed.addItem(text, v)
        self.speed.setCurrentIndex(3)
        self.speed.setToolTip(f"A ×1, la animación entera dura {BASE_SECONDS:.0f} s")
        r3.addWidget(group(QLabel("Velocidad:"), self.speed))
        self.log_time = QCheckBox("Tiempo logarítmico")
        self.log_time.setToolTip("Más despacio al principio: para ver a la vez lo rápido (electrones, fotones) y "
                                 "lo lento (neutrones…)")
        self.stagger = QCheckBox("Escalonadas")
        self.stagger.setToolTip("Cada lluvia sale un poco después de la anterior (si no, todas a la vez)")
        self.trail = QCheckBox("Estela")
        self.trail.setToolTip("Lo recorrido hace poco, más intenso")
        self.heads = QCheckBox("En vuelo")
        self.heads.setChecked(True)
        self.heads.setToolTip("Las partículas en vuelo, como puntos")
        for w in (self.log_time, self.stagger, self.trail, self.heads):
            r3.addWidget(w)
        mp.addLayout(r3)
        self.pages.addWidget(self.main_page)
        # Estado de la cascada y exportar.
        r4 = FlowLayout()
        r4.setContentsMargins(6, 0, 6, 2)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        r4.addWidget(self.status, 1)
        self.which = QComboBox()
        self.which.setToolTip("Otra cascada de este caso (de una versión anterior del .in)")
        self.which.setVisible(False)
        r4.addWidget(self.which)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setVisible(False)
        r4.addWidget(self.progress)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setToolTip("Parar la simulación de la cascada (se puede terminar después)")
        self.cancel_btn.setVisible(False)
        r4.addWidget(self.cancel_btn)
        self.again_btn = QPushButton("Simular…")
        self.again_btn.setToolTip("Simular la cascada (otra vez, con otro número de lluvias o la del caso de ahora)")
        r4.addWidget(self.again_btn)
        self.img_btn = QPushButton("Imagen…")
        self.img_btn.setToolTip("Guardar lo que se ve (PNG)")
        self.anim_btn = QPushButton("Animación…")
        self.anim_btn.setToolTip("Guardar la animación (MP4 o GIF), para clase")
        r4.addWidget(group(self.img_btn, self.anim_btn))
        lay.addLayout(r4)
        # Conexiones.
        self.mode_first.toggled.connect(self._selection_changed)
        self.first_n.valueChanged.connect(self._first_changed)
        self.one_k.valueChanged.connect(self._one_changed)
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.color.currentIndexChanged.connect(self._style_changed)
        self.line_width.valueChanged.connect(self._style_changed)
        self.play_btn.clicked.connect(self.toggle_play)
        self.all_btn.clicked.connect(self.show_everything)
        self.slider.valueChanged.connect(self._slider_moved)
        self.log_time.toggled.connect(self._timing_changed)
        self.stagger.toggled.connect(self._timing_changed)
        self.trail.toggled.connect(self._style_changed)
        self.heads.toggled.connect(self._style_changed)
        self.cancel_btn.clicked.connect(self.cancel_cascade)
        self.again_btn.clicked.connect(self._ask_and_start)
        self.img_btn.clicked.connect(self.export_image)
        self.anim_btn.clicked.connect(self.export_animation)
        self.which.activated.connect(self._which_changed)
        self.fit_btn.clicked.connect(self.fit_visible)
        self._checks: dict[int, QCheckBox] = {}

    def _ensure_view(self):
        if self.view3d is None:
            from ..view3d import View3D

            v = View3D(self.main_page)
            v.show_overlay_info = False
            v.show_all_button = False
            v.overlay_painters.append(self._paint_overlay)
            v.renderInfo.connect(self._on_render_info)
            self.view_holder.addWidget(v)
            self.view3d = v
        return self.view3d

    # -- datos -------------------------------------------------------------------------------------------------
    def set_data(self, data: CascadeData, title: str, opts: DisplayOptions | None = None,
                 context: dict | None = None) -> None:
        same = self.data is not None and data.key == self.data.key
        same_case = self.data is not None and self.data.in_path == data.in_path
        self._token += 1  # lo que se estuviera leyendo era de antes: no sustituye a esto
        self._loading = False
        self.title = title
        if opts is not None:
            self.opts = opts
        if not same_case:
            self._chosen = None
            self._particles_off = set()
        elif self._chosen is not None and self._chosen.is_dir() and (
                data.info is None or not _same_folder(data.info.path, self._chosen)):
            # La ventana trae la más reciente; se sigue con la elegida en la lista (se vuelve a leer).
            self.reload(prefer=self._chosen)
            return
        self.data = data
        if not same_case:
            # Con una cascada a medias, sus lluvias: «Simular la cascada» la termina (con otras, sería otra).
            half = data.info is not None and data.done < data.total
            self.start_n.setValue(int(data.info.showers) if half else self._default_n())
        self._show_data(keep=same)
        self._watch_job()

    def set_options(self, opts: DisplayOptions) -> None:
        self.opts = opts
        if self.view3d is not None:
            self.view3d.overlay.update()

    def _default_n(self) -> int:
        from ..cascade_ui import default_showers

        return default_showers(self.settings)

    def _exe_now(self) -> Path | None:
        from ..cascade_ui import cascade_exe

        d = self.data
        if d is None or d.in_path is None:
            return None
        try:
            return cascade_exe(d.in_path, self.settings)
        except (OSError, ValueError):
            return None

    def reload(self, prefer: Path | None = None) -> None:
        """Vuelve a leer la cascada (en otro hilo): al terminar ejecuciones o al elegir otra cascada."""
        d = self.data
        if d is None:
            return
        if self._loader is None:
            from .window import _Loader

            self._loader = _Loader(self, skip_superseded=True, workers=1)
            self._loader.loaded.connect(self._on_loaded)
        self._token += 1
        folder, in_path, case, exe = d.folder, d.in_path, d.case, self._exe_now()
        self._loading = True
        self._loader.submit(self._token, lambda: load_cascade_data(folder, in_path, prefer=prefer, case=case,
                                                                   exe=exe))

    def _on_render_info(self, text: str) -> None:
        if text.startswith("Las trayectorias"):
            self._render_note = text
            self._update_status()

    def hideEvent(self, e) -> None:
        # Otra vista en la ventana de resultados, o la ventana minimizada: la animación no sigue gastando.
        if self._playing:
            self._stop()
            self._update_style()
        super().hideEvent(e)

    def _reload_or_wait(self, prefer: Path | None) -> None:
        if self._exporting:
            self._reload_after_export = prefer
            return
        self.reload(prefer=prefer)

    def _on_loaded(self, token: int, data, err: str) -> None:
        if token != self._token:
            return
        self._loading = False
        if self._exporting:  # se aplica al terminar de exportar
            self._reload_after_export = data.info.path if data is not None and data.info is not None else None
            return
        if data is None:
            if err != "sustituido":
                self.status.setText(f"No se ha podido leer la cascada: {err}")
            return
        same = self.data is not None and data.key == self.data.key
        self.data = data
        self._show_data(keep=same)

    def _show_data(self, keep: bool) -> None:
        d = self.data
        case = d.case if d is not None else None
        session = getattr(case, "session", None) if case is not None else None
        ok = session is not None and getattr(session, "ok", False)
        if d is None or d.layer is None or not ok:
            self._stop()
            self.pages.setCurrentWidget(self.msg_page)
            self._fill_message()
        else:
            self.pages.setCurrentWidget(self.main_page)
            v = self._ensure_view()
            key = (str(case.input_path), case.stamp)
            new_session = key != self._session_key
            if new_session:
                from ..settings import saved_material_colors

                self._session_key = key
                v.set_session(session)
                v.set_material_colors(saved_material_colors(session, self.settings))
                self._visibility = default_visibility(case)
                v.set_material_visibility(self._visibility)
                self._fill_geometry_menu()
            self._apply_layer(keep=keep and not new_session)
        self._update_status()

    def _apply_layer(self, keep: bool) -> None:
        d = self.data
        L = d.layer
        v = self.view3d
        n = L.n_showers
        prev_total = self.first_n.maximum()
        for w in (self.first_n, self.one_k):
            w.blockSignals(True)
        self.first_n.setRange(1, max(n, 1))
        self.one_k.setRange(1, max(n, 1))
        if not keep:
            self.first_n.setValue(n)
            self.one_k.setValue(1)
            self.mode_first.setChecked(True)
            self._follow_all = True
        elif self._follow_all and self.first_n.value() >= prev_total:
            self.first_n.setValue(n)
        for w in (self.first_n, self.one_k):
            w.blockSignals(False)
        self.first_of.setText(f"de {_fmt_int(n)}")
        self._fill_particle_checks()
        if not keep:
            self.log_time.blockSignals(True)
            self.log_time.setChecked(wants_log_time(d.showers, L))
            self.log_time.blockSignals(False)
            self._u = 1.0
        self._update_style(animate=self.track_style.animate if keep else False)
        v.set_track_layer(L)
        if not keep:
            v.set_view("iso")
            v.fit()
        self.changed.emit()

    def _fill_message(self) -> None:
        d = self.data
        job = self._job()
        st = job.status if job is not None else None
        can_start = d is not None and d.in_path is not None
        self.start_n.setVisible(can_start)
        self.start_label.setVisible(can_start)
        self.start_btn.setVisible(can_start)
        if d is None:
            text = ""
        elif d.in_path is None:
            text = ("<p>No se ha encontrado el .in de la simulación en esta carpeta: la cascada se simula a partir "
                    "de él.</p>")
        elif d.case is not None and (d.case.session is None or not d.case.ok):
            text = (f"<p>No se puede ver la geometría del caso: {d.case.problem or 'tiene errores'}.</p>")
            self.start_btn.setVisible(False)
            self.start_n.setVisible(False)
            self.start_label.setVisible(False)
        elif st is not None and job.running:
            text = ("<p><b>Simulando la cascada…</b></p><p>Las lluvias aparecerán aquí en cuanto termine la primera "
                    "ejecución de penhmain (y, si hacen falta, sus cortes).</p>")
            self.start_btn.setVisible(False)
            self.start_n.setVisible(False)
            self.start_label.setVisible(False)
        elif d.info is not None and d.done == 0:
            text = (f"<p>La cascada «{d.info.path.name}» no tiene aún lluvias terminadas (0 de "
                    f"{_fmt_int(d.total)}).</p>"
                    + (f"<p>penhmain ha simulado {_fmt_int(d.unverified)}, pero aún no se sabe de qué lluvia es cada "
                       "trayectoria: falta repetir esas ejecuciones parando antes.</p>" if d.unverified else "")
                    + f"<p>«Simular la cascada» con {_fmt_int(d.info.showers)} lluvias la termina.</p>")
        else:
            text = ("<p><b>Cascada de partículas</b></p>"
                    "<p>Las trayectorias reales de las primeras lluvias del caso, simuladas con su penhmain "
                    "(LTRACK), en 3D sobre la geometría y animadas con el tiempo de vuelo de cada partícula.</p>"
                    "<p>Se simulan aparte, en la carpeta «cascada» junto al .in (el .in, los .mat y la geometría no "
                    "se tocan) y suelen tardar poco: unos segundos por cada 50 lluvias (con protones, alfas o "
                    "neutrones, unos minutos más por cada ejecución de penhmain, que prepara antes los datos "
                    "nucleares).</p>"
                    "<p>Cada trayectoria va en su lluvia, sin adivinar nada: penhmain no lo escribe, y cuando sus "
                    "ficheros no bastan para saberlo (sobre todo con una fuente radiactiva puntual) ARGUS repite esa "
                    "ejecución con las mismas semillas parando tras las lluvias que hace falta. Entonces tarda más: "
                    "lo que tarda penhmain en arrancar por cada corte.</p>")
        self.msg.setText(text)

    # -- trabajo en segundo plano --------------------------------------------------------------------------------
    def _job(self):
        d = self.data
        if d is None or d.in_path is None:
            return None
        return cs.active_job(d.in_path)

    def _watch_job(self) -> None:
        job = self._job()
        if job is not None and job.running:
            if not self.job_timer.isActive():
                self._job_seen = None
                self.job_timer.start()
            self._poll_job()
        else:
            self.job_timer.stop()

    def _poll_job(self) -> None:
        from ..cascade_ui import save_learned_cap

        job = self._job()
        if job is None:
            self.job_timer.stop()
            return
        st = job.status
        save_learned_cap(job, self.settings)
        seen = (str(st.folder), st.showers_done, st.stage)
        # Se vuelve a leer la que se simula si es la que se ve (no si se ha elegido otra en la lista) y nunca a mitad
        # de una exportación (se lee al terminar).
        mine = st.folder is not None and (self._chosen is None or _same_folder(self._chosen, st.folder))
        if mine and seen != self._job_seen and not self._loading:
            self._job_seen = seen
            self._reload_or_wait(st.folder)
        if not job.running:
            self.job_timer.stop()
            if mine:
                self._reload_or_wait(st.folder)
        self._update_status()
        if self.pages.currentWidget() is self.msg_page:
            self._fill_message()

    def start_cascade(self, n: int | None = None) -> bool:
        """Simula la cascada del caso con ``n`` lluvias (en segundo plano)."""
        from ..cascade_ui import set_default_showers, start_cascade

        d = self.data
        if d is None or d.in_path is None:
            return False
        n = int(n or self._default_n())
        try:
            start_cascade(d.in_path, showers=n, settings=self.settings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "ARGUS", f"No se puede simular la cascada: {str(exc).rstrip('.')}.")
            return False
        set_default_showers(n, self.settings)
        self._chosen = None  # se sigue la que se simula
        self._job_seen = None
        self._watch_job()
        self._update_status()
        if self.pages.currentWidget() is self.msg_page:
            self._fill_message()
        return True

    def _ask_and_start(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        d = self.data
        if d is None or d.in_path is None:
            return
        n0 = d.info.showers if d.info is not None else self._default_n()
        n, ok = QInputDialog.getInt(self, "Simular la cascada",
                                    "Lluvias que se simulan con penhmain (si ya están simuladas con el caso de ahora, "
                                    "no se repiten):", n0, 1, cs.MAX_SHOWERS, 100)
        if ok:
            self.start_cascade(n)

    def cancel_cascade(self) -> None:
        job = self._job()
        if job is not None and job.running:
            job.cancel()
            self.status.setText("Cancelando la cascada…")

    def _which_changed(self, i: int) -> None:
        path = self.which.itemData(i)
        if path:
            self._chosen = Path(path)
            self.reload(prefer=Path(path))

    def _update_status(self) -> None:
        d = self.data
        job = self._job()
        st = job.status if job is not None else None
        running = job is not None and job.running
        self.cancel_btn.setVisible(running)
        self.again_btn.setVisible(d is not None and d.in_path is not None and not running)
        has = d is not None and d.layer is not None
        self.img_btn.setEnabled(has)
        self.anim_btn.setEnabled(has)
        # Otras cascadas del caso.
        self.which.blockSignals(True)
        self.which.clear()
        if d is not None and len(d.cascades) > 1:
            for info in d.cascades:
                when = time.strftime("%d/%m/%Y %H:%M", time.localtime(info.created)) if info.created else ""
                self.which.addItem(f"{info.path.name} ({_fmt_int(info.showers)} lluvias, {when})", str(info.path))
                if d.info is not None and os.path.normcase(str(info.path)) == os.path.normcase(str(d.info.path)):
                    self.which.setCurrentIndex(self.which.count() - 1)
        self.which.setVisible(self.which.count() > 1)
        self.which.blockSignals(False)
        style = ""
        if running and st is not None:
            text = st.text
            if st.showers_total:
                self.progress.setVisible(True)
                self.progress.setRange(0, int(st.showers_total))
                self.progress.setValue(int(st.showers_done))
                self.progress.setFormat(f"{_fmt_int(st.showers_done)} / {_fmt_int(st.showers_total)}")
            else:
                self.progress.setVisible(False)
        else:
            self.progress.setVisible(False)
            if st is not None and st.stage in ("error", "cancelada") and d is not None and (
                    d.info is None or st.folder is None or os.path.normcase(str(st.folder))
                    == os.path.normcase(str(d.info.path))):
                text = st.text
                style = "color: #b8860b;" if st.stage == "cancelada" else "color: #c0392b;"
            elif d is None or d.info is None:
                text = "" if d is None or d.in_path is None else "Sin cascada simulada para este caso."
            else:
                when = time.strftime("%d/%m/%Y %H:%M", time.localtime(d.info.created)) if d.info.created else ""
                runs = len(d.info.runs)
                text = (f"Cascada «{d.info.path.name}»: {_fmt_int(d.done)} de {_fmt_int(d.total)} lluvias simuladas "
                        f"con {d.info.exe or 'penhmain'} ({runs} {'ejecución' if runs == 1 else 'ejecuciones'}"
                        + (f", {when}" if when else "") + ").")
                if d.unverified:
                    text += (f" De {plural(d.unverified, 'lluvia')} aún no se sabe de qué lluvia es cada trayectoria "
                             "(penhmain tiene que repetir esas ejecuciones parando antes): «Simular…» lo termina.")
                    style = "color: #b8860b;"
                elif d.done < d.total:
                    text += " A medias: «Simular…» la termina."
                    style = "color: #b8860b;"
                if d.showers is not None and d.showers.orphans:
                    n = d.showers.orphans
                    text += (f" {plural(n, 'trayectoria')} sin madre reconocida "
                             + ("(cuenta como primaria)." if n == 1 else "(cuentan como primarias)."))
        if d is not None and d.stale and not running:
            text = d.stale + (" " + text if text else "")
            style = "color: #b8860b;"
        if self._render_note:
            text = (text + " " if text else "") + self._render_note.rstrip(".") + "."
        self.status.setText(text)
        self.status.setStyleSheet(style)

    # -- lluvias, partículas y estilo --------------------------------------------------------------------------
    def _fill_particle_checks(self) -> None:
        d = self.data
        L = d.layer if d is not None else None
        kinds = L.kinds if L is not None else []
        if sorted(self._checks) != sorted(kinds):
            while self.checks_box.count():
                it = self.checks_box.takeAt(0)
                if it.widget() is not None:
                    it.widget().deleteLater()
            self._checks = {}
            for kp in kinds:
                cb = QCheckBox(PARTICLE_NAMES.get(kp, f"KPAR {kp}"))
                cb.setIcon(_swatch(particle_color(kp)))  # el color, como icono (no con una hoja de estilo: el texto
                cb.setStyleSheet(f"QCheckBox {{ color: {INK_2}; }}")  # se cortaba al cambiar con los números)
                cb.setChecked(kp not in self._particles_off)
                cb.toggled.connect(self._particles_changed)
                self.checks_box.addWidget(cb)
                self._checks[kp] = cb
        for kp, cb in self._checks.items():  # las de otro caso empiezan todas marcadas
            if cb.isChecked() == (kp in self._particles_off):
                cb.blockSignals(True)
                cb.setChecked(kp not in self._particles_off)
                cb.blockSignals(False)
        self._update_counts()

    def _update_counts(self) -> None:
        d = self.data
        s = d.showers if d is not None else None
        if s is None:
            return
        lo, hi = self._shower_range()
        sel = (s.shower >= lo) & (s.shower < hi)
        for kp, cb in self._checks.items():
            n = int(np.count_nonzero(sel & (s.particle == kp)))
            cb.setText(f"{PARTICLE_NAMES.get(kp, f'KPAR {kp}')} ({_fmt_int(n)})")
            cb.setMinimumWidth(cb.sizeHint().width())
        if hi - lo == 1:
            gens = s.generation[sel]
            ng = int(gens.max()) + 1 if len(gens) else 0
            self.shower_info.setText(f"Lluvia {fmt_int(lo + 1)}: {plural(int(sel.sum()), 'trayectoria')}, "
                                     f"{plural(ng, 'generación', 'generaciones')}")
        else:
            self.shower_info.setText(plural(int(sel.sum()), "trayectoria"))

    def _particles_changed(self) -> None:
        self._particles_off = {k for k, cb in self._checks.items() if not cb.isChecked()}
        self._update_style()
        self.changed.emit()

    def _shower_range(self) -> tuple[int, int]:
        d = self.data
        n = d.layer.n_showers if d is not None and d.layer is not None else 0
        if self.mode_one.isChecked():
            k = min(max(self.one_k.value() - 1, 0), max(n - 1, 0))
            return k, k + 1
        return 0, min(self.first_n.value(), n) if n else 0

    def _first_changed(self) -> None:
        self._follow_all = self.first_n.value() >= self.first_n.maximum()
        if not self.mode_first.isChecked():
            self.mode_first.setChecked(True)  # avisa con toggled
            return
        self._selection_changed()

    def _one_changed(self) -> None:
        if not self.mode_one.isChecked():
            self.mode_one.setChecked(True)  # avisa con toggled
            return
        self._selection_changed()

    def _step(self, k: int) -> None:
        if not self.mode_one.isChecked():
            self.mode_one.setChecked(True)
        self.one_k.setValue(min(max(self.one_k.value() + k, 1), self.one_k.maximum()))

    def _selection_changed(self) -> None:
        self._update_counts()
        self._update_style()
        self.changed.emit()

    def _timing_changed(self) -> None:
        t = self.track_style.time if self.track_style.animate else None
        self._update_style()
        if t is not None:  # el mismo instante con la nueva escala
            self._u = position_of(t, self.track_style.end_time(self.data.layer), self.log_time.isChecked())
            self._update_style()

    def _style_changed(self) -> None:
        self._update_style()

    def _update_style(self, animate: bool | None = None) -> None:
        d = self.data
        L = d.layer if d is not None else None
        if L is None:
            return
        lo, hi = self._shower_range()
        spans = shower_spans(L)
        med = float(np.median(spans[lo:hi])) if hi > lo else 0.0
        st = self.track_style
        st.particles = frozenset(ALL_PARTICLES - self._particles_off) if self._particles_off else ALL_PARTICLES
        st.shower_lo, st.shower_hi = lo, hi
        st.color_mode = self.color.currentData() or "particle"
        st.line_width = float(self.line_width.value())
        st.alpha = auto_alpha(hi - lo)
        st.stagger = 0.5 * med if self.stagger.isChecked() and hi - lo > 1 else 0.0
        st.trail = 0.2 * max(med, 1e-9) if self.trail.isChecked() else 0.0
        st.heads = self.heads.isChecked()
        st.hidden_alpha = 0.15 if self._faint_hidden else 0.0
        if animate is not None:
            st.animate = animate
        t_end = st.end_time(L)
        st.time = time_of(self._u, t_end, self.log_time.isChecked())
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(1000 * self._u)))
        self.slider.blockSignals(False)
        self.time_label.setText(f"t = {format_time(st.time)} de {format_time(t_end)}" if st.animate
                                else f"hasta {format_time(t_end)}")
        self.play_btn.setText("❚❚ Pausa" if self._playing else "▶ Reproducir")
        if self.view3d is not None:
            self.view3d.set_track_style(st)

    def fit_visible(self) -> None:
        """Encuadra las trayectorias que se ven (sin lo poco que se aleja mucho: el 1 % más lejano no cuenta)."""
        d = self.data
        L = d.layer if d is not None else None
        if L is None or self.view3d is None:
            return
        m = self.track_style.visible_mask(L)
        if not m.any():
            return
        idx = np.concatenate([L.seg_a[m], L.seg_b[m]])
        pts = L.vertices[idx, :3].astype(np.float64)
        if len(pts) > 200:
            lo, hi = np.percentile(pts, 0.5, axis=0), np.percentile(pts, 99.5, axis=0)
        else:
            lo, hi = pts.min(axis=0), pts.max(axis=0)
        pad = 0.05 * float(np.max(hi - lo)) + 1e-3
        self.view3d.fit_box(lo - pad + L.origin, hi + pad + L.origin)

    def _visible_generations(self) -> int:
        """La generación más alta de lo que se ve."""
        d = self.data
        L = d.layer
        st = self.track_style
        key = (L.uid, st.shower_lo, st.shower_hi, st.particles)
        if self._gen_key != key:
            v = L.vertices
            m = (v[:, 6] >= st.shower_lo - 0.5) & (v[:, 6] < st.shower_hi - 0.5)
            if st.particles != ALL_PARTICLES:
                m &= np.isin(v[:, 5].astype(np.int64), list(st.particles))
            self._gen_key = key
            self._gen_max = int(v[m, 7].max()) if m.any() else 0
        return self._gen_max

    # -- animación ---------------------------------------------------------------------------------------------
    def toggle_play(self) -> None:
        if self._playing:
            self._stop()
            self._update_style()
            return
        if self.data is None or self.data.layer is None:
            return
        if not self.track_style.animate or self._u >= 0.999:
            self._u = 0.0
        self._playing = True
        self._last_tick = time.monotonic()
        self._update_style(animate=True)
        self.anim_timer.start()

    def _stop(self) -> None:
        self._playing = False
        self.anim_timer.stop()
        self.play_btn.setText("▶ Reproducir")

    def show_everything(self) -> None:
        self._stop()
        self._u = 1.0
        self._update_style(animate=False)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        speed = float(self.speed.currentData() or 1.0)
        self._u = min(self._u + dt * speed / BASE_SECONDS, 1.0)
        if self._u >= 1.0:
            self._stop()
        self._update_style(animate=True)

    def _slider_moved(self, value: int) -> None:
        self._stop()
        self._u = value / 1000.0
        self._update_style(animate=True)

    # -- geometría ---------------------------------------------------------------------------------------------
    def _fill_geometry_menu(self) -> None:
        from ...render.scene3d import HIDDEN, OPAQUE, TRANSLUCENT

        m = self.geo_menu
        m.clear()
        case = self.data.case if self.data is not None else None
        if case is None or case.geo is None:
            return
        for text, how in (("Todo translúcido (los gases, ocultos)", "auto"), ("Todo opaco", OPAQUE),
                          ("Todo oculto (solo las trayectorias)", HIDDEN)):
            a = QAction(text, self)
            a.triggered.connect(lambda _c=False, h=how: self._set_all(h))
            m.addAction(a)
        m.addSeparator()
        names = {OPAQUE: "Opaco", TRANSLUCENT: "Translúcido", HIDDEN: "Oculto"}
        self._mat_actions = {}
        for mat in case.geo.used_materials():
            sub = m.addMenu(case.material_name(mat) or f"Material {mat}")
            grp = QActionGroup(sub)
            acts = {}
            for how in (OPAQUE, TRANSLUCENT, HIDDEN):
                a = QAction(names[how], sub, checkable=True)
                a.setChecked(self._visibility.get(mat, OPAQUE) == how)
                a.triggered.connect(lambda _c=False, mm=mat, h=how: self._set_material(mm, h))
                grp.addAction(a)
                sub.addAction(a)
                acts[how] = a
            self._mat_actions[mat] = acts
        m.addSeparator()
        self.act_faint = QAction("Ver tenue lo que tapa la geometría opaca", self, checkable=True)
        self.act_faint.setChecked(self._faint_hidden)
        self.act_faint.toggled.connect(self._set_faint)
        m.addAction(self.act_faint)

    def _set_all(self, how) -> None:
        case = self.data.case if self.data is not None else None
        if case is None or case.geo is None:
            return
        self._visibility = default_visibility(case) if how == "auto" else {m: how for m in case.geo.used_materials()}
        self._apply_visibility()

    def _set_material(self, mat: int, how: int) -> None:
        self._visibility[mat] = how
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        if self.view3d is not None:
            self.view3d.set_material_visibility(self._visibility)
        for mat, acts in self._mat_actions.items():
            for how, a in acts.items():
                a.setChecked(self._visibility.get(mat) == how)

    def _set_faint(self, on: bool) -> None:
        self._faint_hidden = bool(on)
        self._update_style()

    # -- lo que se dibuja encima de la vista 3D ------------------------------------------------------------------
    def _energy_unit(self, emax: float) -> str:
        u = self.opts.energy_unit if self.opts is not None else "auto"
        return u if u in ENERGY_SCALE else auto_energy(emax)

    def _paint_overlay(self, p: QPainter, w: int, h: int) -> None:
        d = self.data
        L = d.layer if d is not None else None
        if L is None:
            return
        st = self.track_style
        n = L.n_showers
        lo, hi = st.shower_lo, min(st.shower_hi, n)
        title = f"Lluvia {lo + 1} de {_fmt_int(n)}" if hi - lo == 1 else f"Lluvias 1–{_fmt_int(hi)} de {_fmt_int(n)}"
        if lo > 0 and hi - lo > 1:
            title = f"Lluvias {lo + 1}–{hi} de {_fmt_int(n)}"
        if d.source_label:
            title += f" · {d.source_label}"
        lines = [title]
        cpu = getattr(self.view3d, "cpu_showers", None) if self.view3d is not None else None
        if cpu is not None:
            a, b = cpu
            lines.append(f"Sin GPU solo caben las lluvias {a + 1}–{b}" if b - a > 1 else
                         f"Sin GPU solo cabe parte de la lluvia {a + 1}")
        if st.animate:
            lines.append(f"t = {format_time(st.time)}")
        font = QFont(p.font())
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f2 = QFont(font)
        f2.setPointSizeF(font.pointSizeF() * 1.1)
        p.setFont(f2)
        _box(p, 10, 10, lines)
        p.setFont(font)
        self._paint_legend(p, w, h)
        p.restore()

    def _paint_legend(self, p: QPainter, w: int, h: int) -> None:
        d = self.data
        L = d.layer
        st = self.track_style
        fm = p.fontMetrics()
        lh = fm.height() + 2
        x_right = w - 10
        mode = st.color_mode
        rows: list[tuple[QColor | None, str]] = []
        if mode == "particle":
            for kp in L.kinds:
                if kp in st.particles:
                    rows.append((QColor(particle_color(kp)), PARTICLE_NAMES.get(kp, f"KPAR {kp}")))
        elif mode == "generation":
            gmax = self._visible_generations()
            for g in range(min(gmax, 11) + 1):
                label = "primarias" if g == 0 else f"{g}.ª generación" + (" o más" if g == 11 and gmax > 11 else "")
                rows.append((QColor(PALETTE[min(g, len(PALETTE) - 1)]), label))
        elif mode == "shower":
            self._paint_shower_legend(p, w)
            return
        elif mode == "energy":
            self._paint_energy_bar(p, w, h)
            return
        if not rows:
            return
        tw = max(fm.horizontalAdvance(t) for _c, t in rows) + 34
        x0 = x_right - tw
        rect = QRectF(x0, 10, tw, lh * len(rows) + 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 27, 170))
        p.drawRoundedRect(rect, 4, 4)
        y = 15
        for c, text in rows:
            if c is not None:
                p.setPen(QPen(c, 3))
                p.drawLine(QPointF(x0 + 8, y + lh / 2), QPointF(x0 + 24, y + lh / 2))
            p.setPen(QColor("#f2f3f5"))
            p.drawText(QRectF(x0 + 30, y, tw - 34, lh), Qt.AlignmentFlag.AlignVCenter, text)
            y += lh

    def _paint_shower_legend(self, p: QPainter, w: int) -> None:
        fm = p.fontMetrics()
        text = f"Un color por lluvia (se repiten cada {len(PALETTE)})"
        sw = 12
        tw = max(fm.horizontalAdvance(text), len(PALETTE) * (sw + 3)) + 16
        x0 = w - 10 - tw
        rect = QRectF(x0, 10, tw, fm.height() + sw + 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 27, 170))
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(QColor("#f2f3f5"))
        p.drawText(QRectF(x0 + 8, 14, tw - 12, fm.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(Qt.PenStyle.NoPen)
        for i, hx in enumerate(PALETTE):
            p.setBrush(QColor(hx))
            p.drawRoundedRect(QRectF(x0 + 8 + i * (sw + 3), 18 + fm.height(), sw, sw), 2, 2)

    def _paint_energy_bar(self, p: QPainter, w: int, h: int) -> None:
        L = self.data.layer
        lo, hi = L.log_e
        unit = self._energy_unit(10 ** hi)
        f = ENERGY_SCALE[unit]
        fm = p.fontMetrics()
        bar_h = min(160, max(h - 120, 60))
        labels = [(1.0, 10 ** hi), (0.5, 10 ** (0.5 * (lo + hi))), (0.0, 10 ** lo)]
        texts = [f"{e * f:.3g} {unit}".replace(".", ",") for _u, e in labels]
        tw = max(fm.horizontalAdvance(t) for t in texts + ["Energía"]) + 36
        x0 = w - 10 - tw
        rect = QRectF(x0, 10, tw, bar_h + fm.height() + 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 27, 170))
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(QColor("#f2f3f5"))
        p.drawText(QRectF(x0 + 8, 14, tw - 12, fm.height()), Qt.AlignmentFlag.AlignVCenter, "Energía")
        top = 18 + fm.height()
        grad = QLinearGradient(0, top + bar_h, 0, top)
        lut = energy_lut()
        for k in range(0, 256, 32):
            grad.setColorAt(k / 255.0, QColor(int(lut[k, 0]), int(lut[k, 1]), int(lut[k, 2])))
        grad.setColorAt(1.0, QColor(int(lut[255, 0]), int(lut[255, 1]), int(lut[255, 2])))
        p.setBrush(grad)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(x0 + 8, top, 12, bar_h))
        p.setPen(QColor("#f2f3f5"))
        for (u, _e), text in zip(labels, texts):
            y = top + (1.0 - u) * bar_h
            p.drawLine(QPointF(x0 + 20, y), QPointF(x0 + 24, y))
            p.drawText(QRectF(x0 + 27, y - fm.height() / 2, tw - 30, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                       text)

    # -- exportar ----------------------------------------------------------------------------------------------
    def _wait_idle(self, timeout: float = 30.0) -> None:
        v = self.view3d
        t0 = time.monotonic()
        while v is not None and not v.is_idle() and time.monotonic() - t0 < timeout:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 30)
            time.sleep(0.01)

    def export_image(self) -> None:
        if self.view3d is None or self.data is None or self.data.layer is None:
            QMessageBox.information(self, "ARGUS", "No hay trayectorias que guardar.")
            return
        start = str(self.settings.value("resultados/exportar", "") or "")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar la imagen", str(Path(start) / "cascada.png"),
                                              "PNG (*.png)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".png"
            if not self._may_overwrite(path):
                return
        self.settings.setValue("resultados/exportar", str(Path(path).parent))
        self._wait_idle()
        img = self.view3d.grab_image()
        if not img.save(path):
            QMessageBox.warning(self, "ARGUS", f"No se ha podido guardar {path}.")
            return
        self.status.setText(f"Imagen guardada en {path}.")

    def _may_overwrite(self, path: str) -> bool:
        """Al añadir la extensión, el diálogo no ha podido preguntar si se sustituye el fichero que ya existe."""
        if not Path(path).exists():
            return True
        ans = QMessageBox.question(self, "ARGUS", f"Ya existe {Path(path).name}. ¿Sustituirlo?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.No)
        return ans == QMessageBox.StandardButton.Yes

    def animation_frames(self, n: int, width: int, even: bool = False) -> Iterator[QImage]:
        """Las ``n`` imágenes de la animación, de t = 0 al final (con la escala de tiempo elegida)."""
        st = self.track_style
        old = (st.animate, self._u)
        try:
            for i in range(n):
                self._u = i / max(n - 1, 1)
                self._update_style(animate=True)
                img = self.view3d.grab_image()
                img.setDevicePixelRatio(1.0)
                if width and img.width() != width:
                    img = img.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
                if even and (img.width() % 2 or img.height() % 2):
                    img = img.copy(0, 0, img.width() - img.width() % 2, img.height() - img.height() % 2)
                yield img
        finally:
            st.animate, self._u = old
            self._update_style(animate=old[0])

    def export_animation(self) -> None:
        if self.view3d is None or self.data is None or self.data.layer is None:
            QMessageBox.information(self, "ARGUS", "No hay trayectorias que animar.")
            return
        self._stop()
        self._wait_idle()
        probe = self.view3d.grab_image()
        speed = float(self.speed.currentData() or 1.0)
        dlg = ExportAnimationDialog(self, probe.width(), BASE_SECONDS / speed, mp4_problem(),
                                    aspect=probe.height() / max(probe.width(), 1))
        try:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            fmt, seconds, fps, width = dlg.values()
        finally:
            dlg.deleteLater()
        start = str(self.settings.value("resultados/exportar", "") or "")
        ext = ".mp4" if fmt == "mp4" else ".gif"
        flt = "Vídeo MP4 (*.mp4)" if fmt == "mp4" else "GIF animado (*.gif)"
        path, _ = QFileDialog.getSaveFileName(self, "Guardar la animación", str(Path(start) / f"cascada{ext}"), flt)
        if not path:
            return
        if Path(path).suffix.lower() != ext:
            path += ext
            if not self._may_overwrite(path):
                return
        self.settings.setValue("resultados/exportar", str(Path(path).parent))
        n = max(int(round(seconds * fps)), 2)
        prog = QProgressDialog("Preparando las imágenes de la animación…", "Cancelar", 0, n, self)
        prog.setWindowTitle("Exportar la animación")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(300)
        cancelled = [False]

        def frames():
            for i, img in enumerate(self.animation_frames(n, width, even=fmt == "mp4")):
                if prog.wasCanceled():
                    cancelled[0] = True
                    return
                prog.setValue(i)
                yield img

        self._exporting = True
        try:
            if fmt == "mp4":
                count = write_mp4(path, frames(), fps)
            else:
                count = write_gif(path, frames(), fps, n)
        except MemoryError:
            QMessageBox.warning(self, "ARGUS", "No hay memoria para tantas imágenes: pruebe con menos (menos "
                                               "duración o imágenes por segundo) o más pequeñas, o con el vídeo MP4.")
            return
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "ARGUS", f"No se ha podido guardar la animación: {exc}")
            return
        finally:
            self._exporting = False
            prog.close()
            prog.deleteLater()
            if self._reload_after_export is not False:
                pending, self._reload_after_export = self._reload_after_export, False
                self.reload(prefer=pending)
        if cancelled[0]:
            self.status.setText(f"Exportación cancelada ({plural(count, 'imagen', 'imágenes')} en {path}).")
            return
        secs = f"{seconds:g}".replace(".", ",")
        self.status.setText(f"Animación guardada en {path} ({plural(count, 'imagen', 'imágenes')}, {secs} s).")

    # -- para la ventana de resultados -------------------------------------------------------------------------
    def visible_tracks(self) -> list[Tracks]:
        """Las trayectorias que se ven (lluvias y partículas elegidas), para dibujarlas sobre los cortes 2D."""
        d = self.data
        if d is None or d.showers is None:
            return []
        lo, hi = self._shower_range()
        parts = set(ALL_PARTICLES - self._particles_off) if self._particles_off else None
        return d.showers.subset(range(lo, hi), parts).to_tracks()

    def table_rows(self) -> tuple[list[str], list[np.ndarray]]:
        """Una fila por trayectoria de las lluvias que se ven: lluvia, partícula, generación, madre, principio y
        final (posición, energía y tiempo) y puntos."""
        d = self.data
        if d is None or d.showers is None:
            return [], []
        lo, hi = self._shower_range()
        s = d.showers
        idx = np.flatnonzero((s.shower >= lo) & (s.shower < hi))
        if self._particles_off:
            idx = idx[~np.isin(s.particle[idx], list(self._particles_off))]
        a = s.starts[idx]
        b = s.starts[idx + 1] - 1
        cols = ["lluvia", "trayectoria", "KPAR", "generación", "madre", "x0 (cm)", "y0 (cm)", "z0 (cm)", "E0 (eV)",
                "t0 (ns)", "x1 (cm)", "y1 (cm)", "z1 (cm)", "E1 (eV)", "t1 (ns)", "puntos"]
        data = [s.shower[idx] + 1.0, idx + 1.0, s.particle[idx].astype(float), s.generation[idx].astype(float),
                np.where(s.parent[idx] >= 0, s.parent[idx] + 1.0, np.nan),
                s.points[a, 0], s.points[a, 1], s.points[a, 2], s.energy[a].astype(float), s.time[a].astype(float),
                s.points[b, 0], s.points[b, 1], s.points[b, 2], s.energy[b].astype(float), s.time[b].astype(float),
                (b - a + 1).astype(float)]
        return cols, [np.asarray(c, dtype=float) for c in data]

    def shutdown(self) -> None:
        """Antes de cerrar ARGUS: para la animación y libera la GPU."""
        self._stop()
        self.job_timer.stop()
        if self._loader is not None:
            self._loader.shutdown()
        if self.view3d is not None:
            self.view3d.shutdown()


def _swatch(color: str, size: int = 12):
    from PySide6.QtGui import QIcon, QPixmap

    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def _box(p: QPainter, x: float, y: float, lines: list[str]) -> None:
    fm = p.fontMetrics()
    lh = fm.height() + 2
    tw = max(fm.horizontalAdvance(t) for t in lines) + 14
    rect = QRectF(x, y, tw, lh * len(lines) + 8)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(20, 22, 27, 170))
    p.drawRoundedRect(rect, 4, 4)
    p.setPen(QColor("#f2f3f5"))
    for i, t in enumerate(lines):
        p.drawText(QRectF(x + 7, y + 4 + i * lh, tw - 10, lh), Qt.AlignmentFlag.AlignVCenter, t)


__all__ = ["CASCADE_KEY", "CASCADE_TITLE", "CascadeData", "ExportAnimationDialog", "ShowerView", "auto_alpha",
           "cascade_stale", "default_visibility", "gas_materials", "gif_durations", "load_cascade_data", "mp4_problem",
           "position_of", "shower_spans", "source_description", "time_of", "wants_log_time", "write_gif",
           "write_mp4"]

