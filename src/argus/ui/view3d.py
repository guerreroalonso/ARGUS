"""Vista 3D (F3): trazado de rayos de la geometría PENGEOM en la GPU (o en la CPU).

La imagen se calcula por trazado de rayos sobre las cuádricas, sin
triangular nada, con la misma lógica que PENGEOM (``render/raytrace.py`` y
``render/shaders.py``). Mientras se mueve la cámara se dibuja una vista
previa de baja resolución; al parar se completa la imagen por bloques
(*tiles*) para que ninguna orden a la GPU dure demasiado (en Windows, una
orden de más de ~2 s reinicia el controlador).

Encima se dibujan con QPainter la fuente del .in (posición, haz, cono,
caja), la rejilla de dosis, el plano del corte 2D y los ejes; lo que queda
detrás de la geometría se dibuja discontinuo (oclusión con el trazador de
la CPU).
"""

from __future__ import annotations

import math
import time

import numpy as np
import shiboken6
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QGridLayout,
    QMenu,
    QSizePolicy,
    QSlider,
    QToolBar,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import plural
from ..penhan.sections import grid_text
from ..render.colors import SELECT_COLOR, hex_to_rgb, material_color
from ..render.raytrace import NMAT_CT, RayTracer
from ..render.scene3d import (
    BACKGROUND,
    CLIP_MODES,
    HIDDEN,
    OPAQUE,
    TRANSLUCENT,
    VIEW_TITLES,
    VIEWS,
    Camera,
    body_rgba,
    clip_planes,
    describe_clip,
    gpu_coefficients,
    gpu_tree_tables,
    grid,
    key_light,
    ortho_back,
    surface_centers,
    visibility_array,
    visible_bounds,
)
from ..render.slice2d import HIGHLIGHT_COLOR, nice_length

CONTROLS_HELP = ("Vista 3D:\n"
                 "· arrastrar: girar alrededor del centro\n"
                 "· Mayús + arrastrar o botón derecho: desplazar\n"
                 "· rueda: acercar o alejar (hacia el cursor)\n"
                 "· doble clic: girar alrededor de ese punto\n"
                 "· clic: seleccionar el cuerpo; clic derecho: menú (fuente, visibilidad, corte 2D)\n"
                 "· flechas: girar; + y −: acercar y alejar; Inicio: encuadrar\n"
                 "· rejilla de dosis: arrastrar el rombo la mueve; un cuadrado, estira esa cara (el vóxel no cambia; "
                 "con Ctrl, cambia el vóxel y no su número); Esc anula el arrastre")
BROKEN_GEOMETRY_TEXT = ("La geometría tiene errores: véalos en el panel de mensajes (en «Geometrías complejas», "
                        "debajo del texto).")
SOURCE_RGB = (255, 170, 30)
DOSE_RGB = (90, 200, 255)
PLANE_RGB = (235, 235, 240)
GL_COLOR_BUFFER_BIT = 0x4000
GL_FRAMEBUFFER = 0x8D40


def _bg_rgb() -> tuple[float, float, float]:
    return tuple(0.5 * (a + b) for a, b in zip(*BACKGROUND))


# ---------------------------------------------------------------------------
# Lienzo en la GPU
# ---------------------------------------------------------------------------

def _qround(x: float) -> int:
    """Como qRound de Qt (las mitades hacia arriba): así calcula Qt el tamaño del framebuffer. El round de
    Python redondea las mitades al par y, con la pantalla al 150 %, se quedaba un píxel corto (una raya)."""
    return int(math.floor(x + 0.5))


class GLCanvas(QOpenGLWidget):
    """Lienzo OpenGL: vista previa rápida y luego la imagen completa por bloques."""

    def __init__(self, view: View3D, parent: QWidget | None = None):
        super().__init__(parent or view)
        self.view = view
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gpu = None
        self.error: str | None = None
        self._fbo = None
        self._pfbo = None
        self._frame: dict | None = None
        self._tiles: list[tuple[int, int, int, int]] = []
        self._tile = 128
        self._div = 4
        self._need_frame = True
        self._upload = {"tree": True, "coef": True, "color": True}
        self._origin: np.ndarray | None = None
        self._size = (1, 1)
        self._complete = False
        self._frame_start = 0.0
        self._gpu_time = 0.0
        self._tracks = None  # GpuTrackLayer (trayectorias de la cascada), al hacer falta
        self.track_error: str | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._work)

    # -- interfaz con la vista -------------------------------------------------------
    def invalidate(self, what: str = "camera") -> None:
        if what == "tree":
            self._upload = {"tree": True, "coef": True, "color": True}
            # Otra geometría: la vista previa y los bloques se ajustaron a la anterior. Si era sencilla y esta
            # es pesada, la primera orden a la GPU podía pasar de los 2 s del vigilante de Windows (TDR) y
            # reiniciar el controlador: se empieza con poco trabajo por orden y se vuelve a ajustar.
            self._div = max(self._div, 8)
            self._tile = min(self._tile, 64)
        elif what in self._upload:
            self._upload[what] = True
        self._need_frame = True
        self._timer.stop()
        self.update()

    @property
    def complete(self) -> bool:
        return self._complete and not self._need_frame

    def backend_name(self) -> str:
        return "GPU"

    # -- OpenGL ------------------------------------------------------------------------
    def initializeGL(self) -> None:
        from .gpu_tracer import GpuError, GpuTracer

        ctx = self.context()
        ctx.aboutToBeDestroyed.connect(self._cleanup)
        try:
            self.gpu = GpuTracer(ctx)
        except GpuError as exc:  # pragma: no cover - depende del controlador
            self.error = str(exc)
            QTimer.singleShot(0, lambda: self.view.gpu_failed(self.error))
            return
        self._upload = {"tree": True, "coef": True, "color": True}
        self._need_frame = True

    def _cleanup(self) -> None:
        # Si el widget se está borrando, su envoltorio de Python ya no vale: el GpuTracer se libera solo (ver
        # gpu_tracer._ContextWatch).
        if shiboken6.isValid(self):
            self.release_gl()

    def release_gl(self) -> None:
        """Libera texturas, programas y FBO con su contexto activo.

        Hay que hacerlo antes de destruir el widget: si lo hiciera el recolector
        de Python más tarde, podría estar activo otro contexto (o ninguno).
        """
        self._timer.stop()
        if self.gpu is None and self._fbo is None and self._pfbo is None and self._tracks is None:
            return
        if self.context() is not None and self.context().isValid():
            self.makeCurrent()
            if self.gpu is not None:
                self.gpu.destroy()
            if self._tracks is not None:
                self._tracks.destroy()
            self.gpu = None
            self._tracks = None
            self._fbo = None
            self._pfbo = None
            self.doneCurrent()
        else:  # pragma: no cover - el contexto ya no existe
            self.gpu = None
            self._tracks = None
            self._fbo = None
            self._pfbo = None

    def resizeGL(self, w: int, h: int) -> None:
        self._need_frame = True

    def _pixels(self) -> tuple[int, int]:
        dpr = self.devicePixelRatioF()
        return max(1, _qround(self.width() * dpr)), max(1, _qround(self.height() * dpr))

    def _upload_data(self) -> None:
        v = self.view
        gpu = self.gpu
        tr = v.tracer
        if self._upload["tree"]:
            gpu.set_tree(gpu_tree_tables(tr.t, tr.nbodys), tr.nbodys)
            self._upload["tree"] = False
        target = np.asarray(v.camera.target, dtype=float)
        if self._upload["coef"] or self._origin is None or not np.array_equal(target, self._origin):
            self._origin = target.copy()
            gpu.upload("coef", gpu_coefficients(tr.t, self._origin, v.centers))
            self._upload["coef"] = False
        if self._upload["color"]:
            gpu.upload("color", grid(v.body_colors(), 4, np.uint8))
            gpu.upload("matcolor", v.material_rgba().reshape(1, -1, 4))
            self._upload["color"] = False
        gpu.set_ct(tr.geo, self._origin)

    def paintGL(self) -> None:
        from .gpu_tracer import depth_texture, image_fbo

        f = self.context().functions()
        W, H = self._pixels()
        v = self.view
        if self.gpu is None or v.tracer is None:
            f.glClearColor(*_bg_rgb(), 1.0)
            f.glClear(GL_COLOR_BUFFER_BIT)
            return
        if self._need_frame:
            self._need_frame = False
            self._upload_data()
            if self._fbo is None or self._size != (W, H):
                self._fbo = image_fbo(self.context(), W, H)
                self._size = (W, H)
            self._frame = self.gpu.frame_uniforms(v.camera, W, H, self._origin, v.tolerance(), v.clip(),
                                                  v.selected or 0)
            self._frame_start = time.perf_counter()
            # Vista previa (un píxel de cada div x div), ampliada sobre la imagen completa.
            div = self._div
            pw, ph = -(-W // div), -(-H // div)
            if self._pfbo is None or (self._pfbo.width(), self._pfbo.height()) != (pw, ph):
                self._pfbo = image_fbo(self.context(), pw, ph)
            self._pfbo.bind()
            t0 = time.perf_counter()
            self.gpu.draw(self._frame, float(div), pw, ph)
            f.glFinish()
            dt = time.perf_counter() - t0
            if dt > 0.045 and self._div < 16:
                self._div += 2
            elif dt < 0.010 and self._div > 2:
                self._div -= 1
            self._fbo.bind()
            self.gpu.display(self._pfbo.texture(), W, H, edges=False, linear=True,
                             depth_texture=depth_texture(self._pfbo))
            self._fbo.release()
            self._gpu_time = dt
            self._tiles = _tiles(W, H, self._tile)
            self._complete = False
            self._timer.start(160 if v.interacting else 0)
        f.glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        if self._fbo is not None:
            self.gpu.display(self._fbo.texture(), W, H, edges=v.draw_edges and self._complete, linear=False)
            if v.track_layer is not None and self._frame is not None and self.track_error is None:
                self._draw_tracks(W, H, depth_texture(self._fbo))

    def _draw_tracks(self, W: int, H: int, depth: int | None) -> None:
        """Las trayectorias de la cascada, encima de la imagen y tapadas por la geometría opaca."""
        from .gpu_tracer import GpuError
        from .gpu_tracks import GpuTrackLayer

        v = self.view
        try:
            if self._tracks is None:
                self._tracks = GpuTrackLayer(self.context())
            scale = float(np.linalg.norm(v.scene_hi - v.scene_lo)) or 1.0
            self._tracks.draw(v.track_layer, v.track_style, self._frame, self._origin, W, H, depth,
                              eps=2.0 * v.tolerance() + 1.0e-6 * scale)
        except GpuError as exc:  # pragma: no cover - depende del controlador
            self.track_error = msg = str(exc)
            if v.track_layer is not None:
                v.track_layer.release_gpu()  # se dibujan con QPainter: lo preparado para la GPU sobra
            QTimer.singleShot(0, lambda: v.renderInfo.emit(f"Las trayectorias se dibujan sin GPU (la tarjeta no "
                                                            f"acepta su programa: {msg})"))
            QTimer.singleShot(0, v.canvas.update)

    def update_tracks(self) -> None:
        """Solo cambian las trayectorias (animación, filtros, colores): se vuelve a dibujar sin trazar."""
        self.update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.invalidate()

    def _work(self) -> None:
        if not self._tiles or self.gpu is None or self._fbo is None:
            return
        if not self.isVisible():  # vista 3D oculta (modo 2D): se termina al volver a mostrarla
            return
        if self.view.interacting:
            self._timer.start(120)
            return
        self.makeCurrent()
        f = self.context().functions()
        W, H = self._size
        self._fbo.bind()
        start = time.perf_counter()
        while self._tiles:
            x, y, w, h = self._tiles.pop(0)
            t0 = time.perf_counter()
            self.gpu.draw(self._frame, 1.0, W, H, scissor=(x, y, w, h))
            f.glFinish()
            dt = time.perf_counter() - t0
            self._gpu_time += dt
            if dt > 0.12 and self._tile > 32:  # bloque demasiado lento: se parten los que quedan
                self._tile //= 2
                self._tiles = [t for big in self._tiles for t in _split(big, self._tile)]
            elif dt < 0.004 and self._tile < 512:
                self._tile *= 2
            if time.perf_counter() - start > 0.03:
                break
        self._fbo.release()
        self.doneCurrent()
        if self._tiles:
            self._timer.start(0)
        else:
            self._complete = True
            self.view.frame_done(self._gpu_time, time.perf_counter() - self._frame_start)
        self.update()

    # -- eventos -------------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        self.view.on_press(e)

    def mouseMoveEvent(self, e) -> None:
        self.view.on_move(e)

    def mouseReleaseEvent(self, e) -> None:
        self.view.on_release(e)

    def mouseDoubleClickEvent(self, e) -> None:
        self.view.on_double_click(e)

    def wheelEvent(self, e) -> None:
        self.view.on_wheel(e)

    def keyPressEvent(self, e) -> None:
        if not self.view.on_key(e):
            super().keyPressEvent(e)

    def leaveEvent(self, e) -> None:
        self.view.on_leave()


def _tiles(W: int, H: int, size: int) -> list[tuple[int, int, int, int]]:
    """Bloques de la imagen, del centro hacia fuera."""
    out = []
    for y in range(0, H, size):
        for x in range(0, W, size):
            out.append((x, y, min(size, W - x), min(size, H - y)))
    cx, cy = 0.5 * W, 0.5 * H
    out.sort(key=lambda t: (t[0] + 0.5 * t[2] - cx) ** 2 + (t[1] + 0.5 * t[3] - cy) ** 2)
    return out


def _split(tile: tuple[int, int, int, int], size: int) -> list[tuple[int, int, int, int]]:
    x0, y0, w, h = tile
    return [(x, y, min(size, x0 + w - x), min(size, y0 + h - y))
            for y in range(y0, y0 + h, size) for x in range(x0, x0 + w, size)]


# ---------------------------------------------------------------------------
# Lienzo en la CPU (sin OpenGL 4.1)
# ---------------------------------------------------------------------------

class CpuCanvas(QWidget):
    """Lienzo sin GPU: el mismo trazado con Numba, por franjas para no bloquear la interfaz."""

    def __init__(self, view: View3D, parent: QWidget | None = None):
        super().__init__(parent or view)
        self.view = view
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._img: np.ndarray | None = None  # (H, W, 3) uint8
        self._key: np.ndarray | None = None  # (H, W) huella para las fronteras
        self._shown: QImage | None = None
        self._frame = None
        self._row = 0
        self._band = 16
        self._div = 4
        self._need_frame = True
        self._complete = False
        self._frame_start = 0.0
        self._cpu_time = 0.0
        self._colors = None
        self._mat_colors = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._work)
        self._start = QTimer(self)
        self._start.setSingleShot(True)
        self._start.timeout.connect(self._start_frame)

    def invalidate(self, what: str = "camera") -> None:
        if what in ("tree", "color"):
            self._colors = None
        self._need_frame = True
        self._timer.stop()
        self._start.start(0)

    @property
    def complete(self) -> bool:
        return self._complete and not self._need_frame

    def backend_name(self) -> str:
        return "CPU"

    def _pixels(self) -> tuple[int, int]:
        dpr = self.devicePixelRatioF()
        return max(1, _qround(self.width() * dpr)), max(1, _qround(self.height() * dpr))

    def _trace(self, px, py, W, H):
        v = self.view
        cam = self._frame
        o, d = cam.rays(px, py, W, H)
        res = v.tracer.render_color(o.reshape(-1, 3), d.reshape(-1, 3), self._colors, v.selected or 0, v.clip(),
                                    key_light(cam), mat_rgba=self._mat_colors)
        ny = 1.0 - 2.0 * (np.asarray(py, dtype=float).ravel() + 0.5) / H
        b0, b1 = np.array(BACKGROUND[0]), np.array(BACKGROUND[1])
        bg = b0[None, :] + (b1 - b0)[None, :] * (0.5 * (ny + 1.0))[:, None]
        rgb = res[:, :3] + (1.0 - res[:, 3:4]) * bg
        return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), res[:, 5].astype(np.int64)

    def _start_frame(self) -> None:
        v = self.view
        if v.tracer is None:  # sin geometría (no se ha podido abrir): el fondo, no la imagen de la anterior
            self._img = None
            self._shown = None
            self._need_frame = False
            self.update()
            return
        if not self._need_frame:
            self.update()
            return
        self._need_frame = False
        W, H = self._pixels()
        self._frame = v.camera.copy()
        if self._colors is None:
            self._colors = v.body_colors().astype(float) / 255.0
            self._mat_colors = v.material_rgba()[:NMAT_CT].astype(float) / 255.0
        self._frame_start = time.perf_counter()
        div = self._div
        pw, ph = -(-W // div), -(-H // div)
        # Vista previa: un rayo por bloque de div x div píxeles (en su centro).
        py, px = np.mgrid[0:ph, 0:pw]
        t0 = time.perf_counter()
        rgb, _ = self._trace((px.ravel() + 0.5) * div - 0.5, (py.ravel() + 0.5) * div - 0.5, W, H)
        dt = time.perf_counter() - t0
        if dt > 0.06 and self._div < 16:
            self._div += 2
        elif dt < 0.015 and self._div > 2:
            self._div -= 1
        small = rgb.reshape(ph, pw, 3)
        self._img = np.ascontiguousarray(np.repeat(np.repeat(small, div, axis=0), div, axis=1)[:H, :W])
        self._key = np.zeros((H, W), dtype=np.int64)
        self._cpu_time = dt
        self._row = 0
        self._complete = False
        self._to_qimage()
        self.update()
        self._timer.start(160 if v.interacting else 0)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.invalidate()

    def _work(self) -> None:
        v = self.view
        if self._img is None or self._complete:
            return
        if not self.isVisible():
            return
        if v.interacting:
            self._timer.start(120)
            return
        H, W = self._img.shape[:2]
        start = time.perf_counter()
        while self._row < H and time.perf_counter() - start < 0.04:
            r0, r1 = self._row, min(H, self._row + self._band)
            py, px = np.mgrid[r0:r1, 0:W]
            t0 = time.perf_counter()
            rgb, key = self._trace(px.ravel(), py.ravel(), W, H)
            dt = time.perf_counter() - t0
            self._cpu_time += dt
            self._img[r0:r1] = rgb.reshape(r1 - r0, W, 3)
            self._key[r0:r1] = key.reshape(r1 - r0, W)
            self._row = r1
            if dt > 0.05 and self._band > 2:
                self._band //= 2
            elif dt < 0.01 and self._band < 256:
                self._band *= 2
        if self._row >= H:
            self._complete = True
            v.frame_done(self._cpu_time, time.perf_counter() - self._frame_start)
        else:
            self._timer.start(0)
        self._to_qimage()
        self.update()

    def _to_qimage(self) -> None:
        img = self._img
        if img is None:
            self._shown = None
            return
        out = img
        if self._complete and self.view.draw_edges:
            k = self._key
            edge = np.zeros(k.shape, dtype=np.int64)
            edge[:, 1:] += k[:, 1:] != k[:, :-1]
            edge[:, :-1] += k[:, :-1] != k[:, 1:]
            edge[1:, :] += k[1:, :] != k[:-1, :]
            edge[:-1, :] += k[:-1, :] != k[1:, :]
            f = 1.0 - 0.14 * np.minimum(edge, 3)
            out = (img * f[..., None]).astype(np.uint8)
        H, W = out.shape[:2]
        data = np.ascontiguousarray(out)
        q = QImage(data.data, W, H, 3 * W, QImage.Format.Format_RGB888)
        q = q.copy()
        q.setDevicePixelRatio(self.devicePixelRatioF())
        self._shown = q

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        if self._shown is None:
            r, g, b = (int(255 * c) for c in _bg_rgb())
            p.fillRect(self.rect(), QColor(r, g, b))
        else:
            p.drawImage(QRectF(0, 0, self.width(), self.height()), self._shown)
        p.end()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.invalidate()

    def mousePressEvent(self, e) -> None:
        self.view.on_press(e)

    def mouseMoveEvent(self, e) -> None:
        self.view.on_move(e)

    def mouseReleaseEvent(self, e) -> None:
        self.view.on_release(e)

    def mouseDoubleClickEvent(self, e) -> None:
        self.view.on_double_click(e)

    def wheelEvent(self, e) -> None:
        self.view.on_wheel(e)

    def keyPressEvent(self, e) -> None:
        if not self.view.on_key(e):
            super().keyPressEvent(e)

    def leaveEvent(self, e) -> None:
        self.view.on_leave()


# ---------------------------------------------------------------------------
# Superposiciones (fuente, rejilla de dosis, plano del corte 2D, ejes)
# ---------------------------------------------------------------------------

class Overlay3D(QWidget):
    def __init__(self, view: View3D, parent: QWidget | None = None):
        super().__init__(parent or view)
        self.view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self.view.paint_overlays(p, self.width(), self.height())
        finally:
            p.end()


# ---------------------------------------------------------------------------
# Vista 3D
# ---------------------------------------------------------------------------

class View3D(QWidget):
    """Vista 3D con su barra de herramientas, el lienzo y las superposiciones."""

    bodyPicked = Signal(int)
    contextRequested = Signal(object, object)  # (QPoint global, dict con point, body, t, origin, direction)
    cursorMoved = Signal(object)  # dict: point, body, mat (o None)
    renderInfo = Signal(str)
    visibilityChanged = Signal()
    slicePlaneRequested = Signal(object)  # punto 3D por el que llevar el corte 2D
    gridChanged = Signal(object, bool)  # (rejilla de dosis nueva, arrastre terminado), como en el corte 2D
    dragCancelled = Signal()  # Esc durante el arrastre de la rejilla (como en el corte 2D)
    grid_editable = True  # la rejilla de dosis se puede estirar y mover con sus asas (si hay .in)
    GRID_HANDLE_PX = 9.0
    MAX_LATTICE_LINES = 40  # líneas de los vóxeles por eje en cada cara (si hay más, una de cada tantas)

    def __init__(self, parent=None, backend: str = "auto"):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.session = None
        self.tracer: RayTracer | None = None
        self.centers: np.ndarray | None = None
        self.camera = Camera()
        self.color_mode = "material"
        self.material_colors: dict[int, str] = {}
        self.material_visibility: dict[int, int] = {}
        self.hidden_bodies: set[int] = set()
        self.selected: int | None = None
        self.highlight: set[int] = set()  # cuerpos resaltados (detector del panel Simulación)
        self.draw_edges = True
        self.clip_mode = "none"
        self.clip_point = np.zeros(3)
        self.clip_invert = False
        self.slice_plane = None  # SlicePlane del corte 2D
        self.show_slice_plane = True
        self.source = None
        self.source_active: set[int] = set()
        self.dose_grid: dict | None = None
        self.show_source = True
        self.show_dose_grid = True
        self.tracks = None  # trayectorias de LTRACK (lista de Tracks) sobre la geometría
        self.track_layer = None  # trayectorias de una cascada (render.track_layer.TrackLayerData), en la GPU
        from ..render.track_layer import TrackStyle

        self.track_style = TrackStyle()
        self.cpu_showers: tuple[int, int] | None = None  # sin GPU, las lluvias que se dibujan (si no caben todas)
        self._cpu_depth: tuple | None = None  # (estado, mapa de profundidad) para las trayectorias sin GPU
        # Para otras ventanas (el visor de la cascada): dibujar algo más encima (leyenda, tiempo…), sin los rótulos
        # de la vista ni el botón «Mostrar todo».
        self.overlay_painters: list = []  # funciones (QPainter, ancho, alto)
        self.show_overlay_info = True
        self.show_all_button = True
        self.scene_lo = np.array([-1.0, -1.0, -1.0])
        self.scene_hi = np.array([1.0, 1.0, 1.0])
        self.view_name = "iso"
        self.interacting = False
        self.backend_note = ""
        self._press = None
        self._press_cam: Camera | None = None
        self._press_pivot = None  # el punto cogido al empezar a girar o desplazar (o None: el objetivo)
        self._moved = False
        self._nav_tracks: tuple | None = None  # (clave, puntos de las trayectorias que se ven) para _nav_point
        self._hover_pos = None
        self._last_info = ""
        self._occl_cache: dict = {}
        self._grid_drag: dict | None = None  # arrastre de una asa de la rejilla de dosis
        self._cursor_on_handle = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.toolbar = self._build_toolbar()
        lay.addWidget(self.toolbar)
        self.area = QWidget(self)
        self.area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid = QGridLayout(self.area)
        self._grid.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.area, 1)
        self.canvas = None
        self.overlay = None
        self._make_canvas(backend)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(40)
        self._hover_timer.timeout.connect(self._hover_pick)

    # -- construcción ----------------------------------------------------------------
    def _make_canvas(self, backend: str) -> None:
        use_gpu = False
        note = ""
        if backend in ("auto", "gpu"):
            from .gpu_tracer import probe_gl

            ok, info = probe_gl()
            use_gpu = ok
            note = info if ok else f"sin GPU: {info}"
        else:
            note = "trazado en la CPU (elegido)"
        if self.canvas is not None:
            if isinstance(self.canvas, GLCanvas):
                self.canvas.release_gl()
            self._grid.removeWidget(self.canvas)
            self.canvas.hide()
            self.canvas.deleteLater()
        self.canvas = GLCanvas(self, self.area) if use_gpu else CpuCanvas(self, self.area)
        self.backend_note = note
        self._grid.addWidget(self.canvas, 0, 0)
        if self.overlay is None:
            self.overlay = Overlay3D(self, self.area)
        self._grid.addWidget(self.overlay, 0, 0)
        self.overlay.raise_()
        self.canvas.invalidate("tree")

    def gpu_failed(self, reason: str) -> None:  # pragma: no cover - depende del controlador
        self._make_canvas("cpu")
        self.backend_note = f"sin GPU: {reason}"
        self.renderInfo.emit(self.backend_note)

    def use_backend(self, backend: str) -> None:
        self._make_canvas(backend)
        self.canvas.show()

    @property
    def backend(self) -> str:
        return self.canvas.backend_name()

    def _build_toolbar(self) -> QToolBar:
        tb = QToolBar("Vista 3D", self)
        tb.setIconSize(tb.iconSize() * 0.8)
        tb.setStyleSheet("QToolBar { border: 0; padding: 1px; } QToolButton { padding: 1px 4px; }")
        self.view_actions = {}
        group = QActionGroup(self)
        self.view_button = QToolButton(self)
        self.view_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_button.setToolTip("Orientación de la cámara")
        menu = QMenu(self.view_button)
        for name in ("iso", "+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            a = QAction(VIEW_TITLES[name].capitalize(), self, checkable=True)
            a.triggered.connect(lambda checked=False, n=name: self.set_view(n))
            group.addAction(a)
            menu.addAction(a)
            self.view_actions[name] = a
        self.view_actions["iso"].setChecked(True)
        menu.addSeparator()
        self.act_ortho = QAction("Proyección ortográfica (sin perspectiva)", self, checkable=True)
        self.act_ortho.toggled.connect(self.set_ortho)
        menu.addAction(self.act_ortho)
        self.act_fit3d = QAction("Encuadrar", self)
        self.act_fit3d.setToolTip("Encuadrar lo que tiene material (Inicio)")
        self.act_fit3d.triggered.connect(self.fit)
        menu.addAction(self.act_fit3d)
        self.view_button.setMenu(menu)
        tb.addWidget(self.view_button)
        self._update_view_button()
        # Corte: modo, posición e inversión.
        self.clip_button = QToolButton(self)
        self.clip_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.clip_button.setToolTip("Ocultar una parte de la geometría para ver el interior")
        cmenu = QMenu(self.clip_button)
        cgroup = QActionGroup(self)
        self.clip_actions = {}
        for key, text in CLIP_MODES.items():
            a = QAction(text, self, checkable=True)
            a.triggered.connect(lambda checked=False, k=key: self.set_clip(k))
            cgroup.addAction(a)
            cmenu.addAction(a)
            self.clip_actions[key] = a
        cmenu.addSeparator()
        self.act_clip_invert = QAction("Ocultar el otro lado", self, checkable=True)
        self.act_clip_invert.toggled.connect(self._on_clip_invert)
        cmenu.addAction(self.act_clip_invert)
        self.clip_button.setMenu(cmenu)
        tb.addWidget(self.clip_button)
        self.clip_slider = QSlider(Qt.Orientation.Horizontal)
        self.clip_slider.setRange(0, 1000)
        self.clip_slider.setValue(500)
        self.clip_slider.setMinimumWidth(80)
        self.clip_slider.setMaximumWidth(160)
        self.clip_slider.setToolTip("Posición del plano de corte")
        self.clip_slider.valueChanged.connect(self._on_clip_slider)
        self.clip_slider_action = tb.addWidget(self.clip_slider)
        self.act_show_all = QAction("Mostrar todo", self)
        self.act_show_all.setToolTip("Volver a mostrar los cuerpos ocultos y los materiales translúcidos u ocultos")
        self.act_show_all.triggered.connect(self.show_all)
        tb.addAction(self.act_show_all)
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.act_help = QAction("?", self)
        self.act_help.setToolTip(CONTROLS_HELP)
        self.act_help.triggered.connect(lambda: QToolTip.showText(QCursor.pos(), CONTROLS_HELP, self))
        tb.addAction(self.act_help)
        self._sync_clip_widgets()
        return tb

    # -- configuración ------------------------------------------------------------------
    def set_session(self, session, keep_camera: bool = False, keep_bounds: bool = False,
                    body_map: dict[int, int | None] | None = None) -> None:
        """Muestra otra geometría; con ``keep_camera`` (tras editarla) se conservan cámara, corte y materiales.

        ``keep_bounds``: la caja de la escena no se recalcula (mientras se arrastra un módulo).
        ``body_map``: número nuevo de cada cuerpo de antes (None si ya no está), tras editar la geometría: los
        cuerpos ocultos siguen ocultos con su número nuevo. Sin él (otra geometría), se muestran todos.
        """
        hidden = self.hidden_bodies
        self.session = session
        self.selected = None
        self.highlight = set()
        self.hidden_bodies = set()
        if body_map is not None and session is not None and session.ok:
            n = session.geo.nbodys
            self.hidden_bodies = {k for k in (body_map.get(kb) for kb in hidden) if k and 1 <= k <= n}
        if not keep_camera:
            self.material_visibility = {}
        self._occl_cache.clear()
        if session is None or not session.ok:
            self.tracer = None
            self.canvas.invalidate("tree")
            self.overlay.update()
            return
        self.tracer = RayTracer(session.geo)
        self.centers = surface_centers(self.tracer.t["coef"][1:], self.tracer.t["kplane"][1:])
        if not keep_bounds:
            lo, hi = session.bounds
            # Con todo lo que tiene material, aunque haya materiales o cuerpos ocultos: la caja no se recalcula
            # al volver a mostrarlos, y una caja pequeña hacía que en ortográfica los rayos empezasen dentro de
            # lo que se vuelve a ver (cortado) y que «Encuadrar» lo dejase fuera.
            vb = visible_bounds(self.tracer, lo, hi, self.tracer.default_visibility())
            self.scene_lo, self.scene_hi = (np.asarray(lo, float), np.asarray(hi, float)) if vb is None else vb
        if keep_camera:
            self._sync_clip_widgets()
            self.canvas.invalidate("tree")
            self._camera_changed()
            return
        span = self.scene_hi - self.scene_lo
        self.clip_point = np.array([_snap(0.5 * (self.scene_lo[k] + self.scene_hi[k]), span[k]) for k in range(3)])
        self._sync_clip_widgets()
        self.canvas.invalidate("tree")
        self.fit()

    def set_material_colors(self, colors: dict[int, str]) -> None:
        self.material_colors = dict(colors)
        self._colors_changed()

    def set_color_mode(self, mode: str) -> None:
        self.color_mode = mode
        self._colors_changed()

    def set_material_visibility(self, visibility: dict[int, int]) -> None:
        self.material_visibility = {int(k): int(v) for k, v in visibility.items() if int(v) != OPAQUE}
        self._colors_changed()
        self.visibilityChanged.emit()

    def set_body_hidden(self, kb: int, hidden: bool) -> None:
        if hidden:
            self.hidden_bodies.add(kb)
        else:
            self.hidden_bodies.discard(kb)
        self._colors_changed()
        self.visibilityChanged.emit()

    def show_all(self) -> None:
        self.hidden_bodies = set()
        self.material_visibility = {}
        self._colors_changed()
        self.visibilityChanged.emit()

    def set_selected(self, kb: int | None) -> None:
        self.selected = kb or None
        self.canvas.invalidate("camera")

    def set_highlight(self, kbs) -> None:
        kbs = set(kbs or ())
        if kbs != self.highlight:
            self.highlight = kbs
            if self.session is not None and self.session.ok:
                self._colors_changed()

    def set_draw_edges(self, on: bool) -> None:
        self.draw_edges = on
        self.canvas.invalidate("camera")

    def set_source(self, source, active_bodies: set[int] | None = None, grids: dict | None = None,
                   editable: bool = True) -> None:
        tint_before = self._tint()
        self.source = source
        self.source_active = set(active_bodies or ())
        self.dose_grid = grids
        self.grid_editable = source is not None
        if self._tint() != tint_before:
            self._colors_changed()
        self.overlay.update()

    def set_show_source(self, on: bool) -> None:
        self.show_source = on
        self._colors_changed()

    def set_show_dose_grid(self, on: bool) -> None:
        self.show_dose_grid = on
        self.overlay.update()

    def set_tracks(self, tracks) -> None:
        """Trayectorias de LTRACK (lista de Tracks) sobre la geometría (lo que tapa la geometría, más tenue)."""
        self.tracks = tracks or None
        self._nav_tracks = None
        self.overlay.update()

    def set_track_layer(self, data) -> None:
        """Trayectorias de una cascada (``TrackLayerData``) sobre la geometría (None las quita). En la GPU se
        dibujan como líneas que la geometría opaca tapa; sin GPU, con QPainter (como mucho MAX_CPU_SEGMENTS)."""
        self.track_layer = data
        self._nav_tracks = None
        if data is not None and (not isinstance(self.canvas, GLCanvas) or getattr(self.canvas, "track_error", None)):
            data.release_gpu()  # sin GPU no se suben: lo preparado para ella sobra (32 bytes por punto)
        back = ortho_back(self.camera, *self._content_bounds())
        if self.camera.ortho and back != self.camera.ortho_back:  # que la cámara empiece detrás de todo
            self.camera.ortho_back = back
            self.canvas.invalidate("camera")
        self._refresh_tracks()

    def set_track_style(self, style) -> None:
        """Qué partículas y lluvias se ven, el color y el instante de la animación (no vuelve a trazar)."""
        self.track_style = style
        self._refresh_tracks()

    def _refresh_tracks(self) -> None:
        if isinstance(self.canvas, GLCanvas):
            self.canvas.update_tracks()
        self.overlay.update()

    def fit_box(self, lo, hi) -> None:
        """Encuadra la caja (lo, hi) (sin cambiar la orientación)."""
        w, h = max(1, self.area.width()), max(1, self.area.height())
        self.camera.fit(np.asarray(lo, dtype=float), np.asarray(hi, dtype=float), w / h)
        self.fitted = self.isVisible()
        self._camera_changed()

    def grab_image(self) -> QImage:
        """Lo que se ve (imagen trazada, trayectorias y superposiciones), en píxeles de la pantalla."""
        if isinstance(self.canvas, GLCanvas):
            img = self.canvas.grabFramebuffer()
        else:
            img = self.canvas.grab().toImage()
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        dpr = self.canvas.devicePixelRatioF()
        img.setDevicePixelRatio(dpr)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.paint_overlays(p, self.overlay.width(), self.overlay.height())
        p.end()
        return img

    def set_slice_plane(self, plane) -> None:
        self.slice_plane = plane
        if self.clip_mode == "slice":
            self.canvas.invalidate("camera")
        self.overlay.update()

    def _colors_changed(self) -> None:
        self._occl_cache.clear()
        self._sync_show_all()
        self.canvas.invalidate("color")
        self.overlay.update()

    def _tint(self) -> dict[int, tuple[int, int, int]]:
        src = self.source
        out: dict[int, tuple[int, int, int]] = {}
        if self.show_source and src is not None and not src.from_psf and src.extended and self.source_active:
            out = {kb: SOURCE_RGB for kb in self.source_active}
        for kb in self.highlight:
            out[kb] = HIGHLIGHT_COLOR
        return out

    def body_colors(self) -> np.ndarray:
        return body_rgba(self.session.geo, self.color_mode, self.material_colors, self.material_visibility,
                         self.hidden_bodies, self._tint(), 0.3)

    def _vis_array(self) -> np.ndarray:
        return visibility_array(self.session.geo, self.material_visibility, self.hidden_bodies)

    def material_rgba(self) -> np.ndarray:
        """Color y visibilidad (alfa) de cada material en los vóxeles del CT: (128, 4) uint8."""
        out = np.zeros((128, 4), dtype=np.uint8)
        geo = self.session.geo if self.session is not None and self.session.ok else None
        ct_col = None
        if self.color_mode != "material" and geo is not None and geo.ct_body is not None:
            ct_col = body_rgba(geo, self.color_mode, self.material_colors)[geo.ct_body, :3]
        tint = self._tint().get(geo.ct_body) if geo is not None and geo.ct_body is not None else None
        for m in range(1, NMAT_CT):
            if ct_col is not None:
                c = np.asarray(ct_col, dtype=float)
            else:
                c = np.asarray(hex_to_rgb(self.material_colors.get(m) or material_color(m)), dtype=float)
            if tint is not None:  # el CT es cuerpo activo de la fuente o de un detector resaltado
                c = 0.7 * c + 0.3 * np.asarray(tint, dtype=float)
            out[m, :3] = np.clip(np.rint(c), 0, 255)
            out[m, 3] = self.material_visibility.get(m, OPAQUE)
        return out

    def _mat_vis(self, opaque_only: bool = False) -> np.ndarray:
        """Materiales de los vóxeles que se ven (1) en 3D; con ``opaque_only``, solo los opacos."""
        a = self.material_rgba()[:NMAT_CT, 3].astype(np.int64)
        return ((a == OPAQUE) if opaque_only else (a != HIDDEN)).astype(np.int64)

    def tolerance(self) -> float:
        return 1.0e-5 * max(self.camera.distance, 1e-12)

    def clip(self) -> list[tuple]:
        if self.clip_mode == "none":
            return []
        if self.clip_mode == "slice":
            pl = self.slice_plane
            if pl is None:
                return []
            return clip_planes("slice", self.clip_point, self.clip_invert, pl.normal, pl.origin)
        return clip_planes(self.clip_mode, self.clip_point, self.clip_invert)

    # -- cámara --------------------------------------------------------------------------
    def _camera_changed(self, interactive: bool = False) -> None:
        self.interacting = interactive
        self.camera.ortho_back = ortho_back(self.camera, *self._content_bounds())
        self.canvas.invalidate("camera")
        self.overlay.update()

    def _content_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """Lo que tiene material y, si las hay, las trayectorias de la cascada (sin lo que se aleja mucho: con un
        fotón que sale lejos, la geometría quedaría diminuta)."""
        lo, hi = self.scene_lo.copy(), self.scene_hi.copy()
        data = self.track_layer
        if data is not None and data.n_segments:
            tlo, thi = data.bounds()
            span = float(np.linalg.norm(hi - lo)) or 1.0
            centre = 0.5 * (lo + hi)
            tlo = np.maximum(tlo, centre - 1.5 * span)
            thi = np.minimum(thi, centre + 1.5 * span)
            ok = np.all(np.isfinite(tlo)) and np.all(np.isfinite(thi)) and np.all(tlo <= thi)
            if ok:
                lo, hi = np.minimum(lo, tlo), np.maximum(hi, thi)
        return lo, hi

    fitted = False  # encuadrada con la vista visible (oculta, su tamaño no es el de verdad)

    def fit(self) -> None:
        """Encuadra lo que tiene material, las trayectorias de la cascada y, si se muestra, la fuente del .in (con
        su caja)."""
        w, h = max(1, self.area.width()), max(1, self.area.height())
        lo, hi = self._content_bounds()
        src = self.source
        if self.show_source and src is not None and not src.from_psf and self.tracer is not None:
            pts = [np.asarray(src.position, dtype=float)]
            if src.extended:
                half = 0.5 * np.abs(np.asarray(src.box, dtype=float))
                pts += [pts[0] - half, pts[0] + half]
            span = float(np.linalg.norm(hi - lo)) or 1.0
            for q in pts:
                # Una fuente muy lejana no se incluye: la geometría quedaría diminuta.
                if np.all(np.isfinite(q)) and np.linalg.norm(q - 0.5 * (lo + hi)) < 3.0 * span:
                    lo, hi = np.minimum(lo, q), np.maximum(hi, q)
        self.camera.fit(lo, hi, w / h)
        self.fitted = self.isVisible()
        self._camera_changed()

    def set_view(self, name: str) -> None:
        yaw, pitch = VIEWS[name]
        self.camera.yaw, self.camera.pitch = yaw, pitch
        self.view_name = name
        if name in self.view_actions:
            self.view_actions[name].setChecked(True)
        self._update_view_button()
        self._camera_changed()

    def _update_view_button(self) -> None:
        text = VIEW_TITLES[self.view_name].split(" (")[0] if self.view_name else "libre"
        self.view_button.setText("Vista: " + text)

    def set_ortho(self, on: bool) -> None:
        self.camera.ortho = bool(on)
        if self.act_ortho.isChecked() != bool(on):
            self.act_ortho.setChecked(bool(on))
        self._camera_changed()

    def look_at(self, point) -> None:
        self.camera.target = np.asarray(point, dtype=float)
        self._camera_changed()

    def focus_source(self) -> None:
        """Encuadra la geometría junto con la fuente."""
        self.fit()

    # -- corte ----------------------------------------------------------------------------
    def set_clip(self, mode: str, point=None, invert: bool | None = None) -> None:
        self.clip_mode = mode
        if point is not None:
            self.clip_point = np.asarray(point, dtype=float)
        if invert is not None:
            self.clip_invert = bool(invert)
        self._sync_clip_widgets()
        self._occl_cache.clear()
        self.canvas.invalidate("camera")
        self.overlay.update()

    def _sync_clip_widgets(self) -> None:
        self.clip_actions[self.clip_mode].setChecked(True)
        label = {"none": "Corte: no", "x": "Corte: x", "y": "Corte: y", "z": "Corte: z", "corner": "Corte: esquina",
                 "slice": "Corte: como el 2D"}[self.clip_mode]
        self.clip_button.setText(label)
        axis = {"x": 0, "y": 1, "z": 2}.get(self.clip_mode)
        self.clip_slider_action.setVisible(axis is not None or self.clip_mode == "corner")
        self.act_clip_invert.setEnabled(self.clip_mode != "none")
        if axis is not None or self.clip_mode == "corner":
            k = axis if axis is not None else 0
            lo, hi = float(self.scene_lo[k]), float(self.scene_hi[k])
            pos = 0.5 if hi <= lo else (float(self.clip_point[k]) - lo) / (hi - lo)
            self.clip_slider.blockSignals(True)
            self.clip_slider.setValue(int(round(1000 * min(1.0, max(0.0, pos)))))
            self.clip_slider.blockSignals(False)
        self._sync_show_all()

    def _sync_show_all(self) -> None:
        self.act_show_all.setVisible(self.show_all_button and bool(self.hidden_bodies or self.material_visibility))

    def _on_clip_slider(self, value: int) -> None:
        f = value / 1000.0
        p = np.array(self.clip_point, dtype=float)
        axis = {"x": 0, "y": 1, "z": 2}.get(self.clip_mode)
        axes = [axis] if axis is not None else [0, 1, 2]
        for k in axes:
            lo, hi = float(self.scene_lo[k]), float(self.scene_hi[k])
            p[k] = _snap(lo + f * (hi - lo), hi - lo)
        self.clip_point = p
        self._occl_cache.clear()
        self.canvas.invalidate("camera")
        self.overlay.update()

    def _on_clip_invert(self, on: bool) -> None:
        self.clip_invert = on
        self._occl_cache.clear()
        self.canvas.invalidate("camera")
        self.overlay.update()

    # -- selección ------------------------------------------------------------------------
    def pick(self, x: float, y: float) -> dict | None:
        """Primer punto visible bajo el píxel (x, y) (coordenadas lógicas del lienzo)."""
        if self.tracer is None:
            return None
        w, h = max(1, self.canvas.width()), max(1, self.canvas.height())
        o, d = self.camera.ray(x, y, w, h)
        t, kb, ks = self.tracer.first_visible(o, d, self._vis_array(), self.clip(), mat_vis=self._mat_vis())
        if kb <= 0:
            return None
        p = o + t * d
        return {"point": p, "body": kb, "surface": ks, "t": t, "origin": o, "direction": d,
                "mat": self.session.geo.body(kb).material}

    def _opaque_visibility(self) -> np.ndarray:
        """Visibilidad por cuerpo contando solo lo opaco (lo translúcido, como oculto)."""
        return visibility_array(self.session.geo, {**self.material_visibility,
                                                   **{m: HIDDEN for m, v in self.material_visibility.items()
                                                      if v == TRANSLUCENT}}, self.hidden_bodies)

    NAV_TRACK_RADIUS = 10.0  # píxeles: una trayectoria dibujada tan cerca del cursor es lo que se mira

    def _track_points(self) -> np.ndarray | None:
        """Los puntos de las trayectorias que se dibujan, en cm: los de la cascada con los filtros del visor (y, en la
        animación, los ya recorridos) y los de LTRACK que caben en la imagen (``MAX_TRACK_POINTS``). Se guardan
        mientras no cambien (la rueda los pide a cada paso)."""
        data, st = self.track_layer, self.track_style
        key = (id(data), getattr(data, "uid", None), st.particles, st.shower_lo, st.shower_hi, id(self.tracks),
               (st.time, st.stagger) if st.animate else None, self.MAX_TRACK_POINTS)
        if self._nav_tracks is not None and self._nav_tracks[0] == key:
            return self._nav_tracks[1]
        parts = []
        if data is not None and data.n_segments:
            mask = st.visible_mask(data)
            used = np.zeros(len(data.vertices), dtype=bool)
            used[data.seg_a[mask]] = True
            used[data.seg_b[mask]] = True
            if st.animate:  # lo que aún no ha salido no se ve (como en el shader)
                idx = np.flatnonzero(used)
                late = data.vertices[idx, 3] + st.start_offsets(data, idx) > st.time
                used[idx[late]] = False
            if used.any():
                parts.append(data.vertices[used, :3].astype(np.float64) + data.origin)
        tracks = [t for t in self.tracks or () if np.ndim(getattr(t, "points", ())) == 2 and len(t.points)]
        if tracks:
            from ..results.data import fair_shares

            for t, budget in zip(tracks, fair_shares([len(t.points) for t in tracks], self.MAX_TRACK_POINTS)):
                for a, b in t.ranges(budget):  # (las que se dibujan: _draw_tracks)
                    parts.append(np.asarray(t.points[a:b, :3], dtype=float))
        out = np.concatenate(parts) if parts else None
        self._nav_tracks = (key, out, (data, self.tracks))  # (con ellos: sus id no pasan a otros mientras tanto)
        return out

    def _nav_point(self, x: float, y: float, translucent: bool = True) -> np.ndarray | None:
        """Lo que se mira bajo el cursor, para acercarse a ello, girar a su alrededor y desplazarlo a su escala
        (0.17.1): la superficie opaca (a través de lo translúcido) o un punto de una trayectoria dibujada a menos de
        ``NAV_TRACK_RADIUS`` píxeles, lo más cercano a la cámara. Sin nada opaco, con ``translucent`` vale una
        superficie translúcida (para girar y desplazar; para acercarse no, que no se podría pasar de ella). None si no
        hay nada: entonces, el plano del objetivo, como antes."""
        cands = []
        w, h = max(1, self.canvas.width()), max(1, self.canvas.height())
        if self.tracer is not None and self.session is not None and self.session.ok:
            o, d = self.camera.ray(x, y, w, h)
            t, kb, _ = self.tracer.first_visible(o, d, self._opaque_visibility(), self.clip(),
                                                 mat_vis=self._mat_vis(opaque_only=True))
            if kb <= 0 and translucent:
                t, kb, _ = self.tracer.first_visible(o, d, self._vis_array(), self.clip(), mat_vis=self._mat_vis())
            if kb > 0:
                cands.append(o + t * d)
        pts = self._track_points()
        if pts is not None and len(pts):
            px, z = self.camera.project(pts, w, h)
            d2 = (px[:, 0] - x) ** 2 + (px[:, 1] - y) ** 2
            ok = (z > 0) & (d2 <= self.NAV_TRACK_RADIUS ** 2)
            if ok.any():
                idx = np.flatnonzero(ok)
                cands.append(pts[idx[np.argmin(d2[idx])]])
        cands = [c for c in cands if self.camera.ortho or self.camera.depth_of(c) > 0.0]
        if not cands:
            return None
        return min(cands, key=self.camera.depth_of)

    def _hover_pick(self) -> None:
        if self._hover_pos is None or self.tracer is None:
            return
        info = self.pick(*self._hover_pos)
        self.cursorMoved.emit(info)

    # -- ratón y teclado ----------------------------------------------------------------------
    def on_press(self, e) -> None:
        self.canvas.setFocus()
        if e.button() == Qt.MouseButton.LeftButton:
            name = self.grid_handle_at(e.position().x(), e.position().y())
            if name is not None:
                # La rejilla de partida se toma al empezar a moverse: un valor escrito sin Intro en un panel se
                # aplica justo después de este clic (al perder el foco) y el arrastre no debe pisarlo.
                self._grid_drag = {"handle": name, "g0": None, "p0": (e.position().x(), e.position().y()),
                                   "last": None, "moved": False}
                self._press = None
                return
        self._press = (e.position(), e.button(), e.modifiers())
        self._press_cam = self.camera.copy()
        self._press_pivot = None
        self._moved = False

    def on_move(self, e) -> None:
        pos = e.position()
        if self._grid_drag is not None:
            if not e.buttons() & Qt.MouseButton.LeftButton:
                self._end_lost_grid_drag()  # la suelta no llegó (se soltó fuera de la ventana…)
            else:
                drag = self._grid_drag
                if not drag["moved"] and math.hypot(pos.x() - drag["p0"][0], pos.y() - drag["p0"][1]) < 3:
                    return  # un clic, no un arrastre
                drag["moved"] = True
                self._drag_grid(pos.x(), pos.y(), e.modifiers(), finished=False)
                return
        if self._press is None:
            self._hover_pos = (pos.x(), pos.y())
            self._hover_timer.start()
            on_handle = self.grid_handle_at(pos.x(), pos.y()) is not None
            if on_handle != self._cursor_on_handle:
                self._cursor_on_handle = on_handle
                if on_handle:
                    self.canvas.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    self.canvas.unsetCursor()
            return
        p0, button, mods = self._press
        dx, dy = pos.x() - p0.x(), pos.y() - p0.y()
        if not self._moved and math.hypot(dx, dy) < 3:
            return
        if not self._moved:
            # Lo que se ha cogido (con la cámara de cuando se pulsó): se gira a su alrededor y se desplaza a su escala.
            self._press_pivot = self._nav_point(p0.x(), p0.y())
        self._moved = True
        cam = self._press_cam.copy()
        pivot = self._press_pivot
        pan = (button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton)
               or (button == Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.ShiftModifier))
        if pan:
            depth = cam.depth_of(pivot) if pivot is not None else None
            cam.pan(dx, dy, max(1, self.canvas.height()), depth=depth)
        elif pivot is not None:
            cam.orbit_about(pivot, -0.4 * dx, 0.4 * dy)
        else:
            cam.orbit(-0.4 * dx, 0.4 * dy)
            if self.view_name:
                self.view_name = ""
                for a in self.view_actions.values():
                    a.setChecked(False)
                self._update_view_button()
        self.camera = cam
        self._camera_changed(interactive=True)

    def on_release(self, e) -> None:
        if self._grid_drag is not None:
            if self._grid_drag["moved"]:
                self._drag_grid(e.position().x(), e.position().y(), e.modifiers(), finished=True)
            self._grid_drag = None  # un clic sin moverse no cambia nada (ni deja un paso en el historial)
            self.overlay.update()
            return
        if self._press is None:
            return
        _, button, _ = self._press
        self._press = None
        if self._moved:
            self._camera_changed(interactive=False)
            return
        pos = e.position()
        if button == Qt.MouseButton.LeftButton:
            info = self.pick(pos.x(), pos.y())
            self.bodyPicked.emit(info["body"] if info else 0)
        elif button == Qt.MouseButton.RightButton:
            info = self.pick(pos.x(), pos.y())
            gp = self.canvas.mapToGlobal(pos.toPoint())
            self.contextRequested.emit(gp, info)

    def on_double_click(self, e) -> None:
        info = self.pick(e.position().x(), e.position().y())
        if info is not None:
            self.look_at(info["point"])

    def on_wheel(self, e) -> None:
        steps = e.angleDelta().y() / 120.0
        if steps == 0:
            return
        factor = 0.85 ** steps
        pos = e.position()
        if not self.camera.ortho:
            # Hacia lo que hay bajo el cursor, a su profundidad: se queda en su píxel, nunca se pasa de largo y
            # después girar y desplazar van a su escala (0.17.1; antes, hacia el plano del objetivo: algo pequeño
            # delante o detrás de él no se acercaba o se perdía al girar).
            target = self._nav_point(pos.x(), pos.y(), translucent=False)
            if target is not None:
                self.camera.dolly_toward(target, factor)
                self._camera_changed(interactive=True)
                self._end_interaction_later()
                return
        # Acercar hacia el punto del cursor (en el plano del objetivo).
        w, h = max(1, self.canvas.width()), max(1, self.canvas.height())
        right, up, _ = self.camera.basis()
        nx = 2.0 * pos.x() / w - 1.0
        ny = 1.0 - 2.0 * pos.y() / h
        hh = self.camera.half_height()
        offset = right * nx * hh * (w / h) + up * ny * hh
        self.camera.target = np.asarray(self.camera.target, dtype=float) + (1.0 - factor) * offset
        self.camera.zoom(factor)
        self._camera_changed(interactive=True)
        self._end_interaction_later()

    def _end_interaction_later(self) -> None:
        if not hasattr(self, "_wheel_timer"):
            self._wheel_timer = QTimer(self)
            self._wheel_timer.setSingleShot(True)
            self._wheel_timer.setInterval(150)
            self._wheel_timer.timeout.connect(lambda: self._camera_changed(interactive=False))
        self._wheel_timer.start()

    def _end_lost_grid_drag(self) -> None:
        drag = self._grid_drag
        self._grid_drag = None
        if drag is not None and drag["moved"] and drag["last"] is not None:
            self.gridChanged.emit(drag["last"], True)
        self.overlay.update()

    def on_key(self, e) -> bool:
        k = e.key()
        if k == Qt.Key.Key_Escape and self._grid_drag is not None:
            self._grid_drag = None
            self.overlay.update()
            self.dragCancelled.emit()  # lo aplicado en vivo se anula
            return True
        step = 15.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 5.0
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) and self.view_name:
            self.view_name = ""
            self._update_view_button()
        if k == Qt.Key.Key_Left:
            self.camera.orbit(step, 0)
        elif k == Qt.Key.Key_Right:
            self.camera.orbit(-step, 0)
        elif k == Qt.Key.Key_Up:
            self.camera.orbit(0, step)
        elif k == Qt.Key.Key_Down:
            self.camera.orbit(0, -step)
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.camera.zoom(0.8)
        elif k == Qt.Key.Key_Minus:
            self.camera.zoom(1.25)
        elif k == Qt.Key.Key_Home:
            self.fit()
            return True
        else:
            return False
        self._camera_changed()
        return True

    def on_leave(self) -> None:
        self._hover_pos = None
        self.cursorMoved.emit(None)

    # -- información ---------------------------------------------------------------------------
    def frame_done(self, busy: float, wall: float) -> None:
        self._last_info = f"3D ({self.backend}): {1000 * busy:.0f} ms"
        self.renderInfo.emit(self._last_info)

    def is_idle(self) -> bool:
        return self.canvas.complete

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.overlay.update()

    def shutdown(self) -> None:
        self._hover_timer.stop()
        if isinstance(self.canvas, GLCanvas):
            self.canvas.release_gl()

    def closeEvent(self, e) -> None:
        self.shutdown()
        super().closeEvent(e)

    # -- superposiciones -------------------------------------------------------------------------
    def _occluded(self, pts: np.ndarray) -> np.ndarray:
        """True donde el punto queda detrás de la geometría visible (o dentro de ella)."""
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        if self.tracer is None or len(pts) == 0:
            return np.zeros(len(pts), dtype=bool)
        o, d, dist = self.camera.rays_to(pts)
        # Solo tapan los cuerpos opacos: a través de los translúcidos la fuente se ve.
        t, kb, _ = self.tracer.render(o, d, self._opaque_visibility(), self.clip(),
                                      mat_vis=self._mat_vis(opaque_only=True))
        scale = float(np.linalg.norm(self.scene_hi - self.scene_lo)) or 1.0
        return (kb > 0) & (t < dist - 1e-6 * scale)

    def paint_overlays(self, p: QPainter, w: int, h: int) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.95))
        p.setFont(font)
        if self.tracer is None:
            text = ("Abra una geometría (.geo) o un fichero de entrada (.in) para verla en 3D."
                    if self.session is None else BROKEN_GEOMETRY_TEXT)
            p.setPen(QColor("#d8dce3"))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
            return
        gl = isinstance(self.canvas, GLCanvas)
        if self.track_layer is not None and (not gl or getattr(self.canvas, "track_error", None)):
            # Sin GPU, o si la GPU no acepta el programa de las trayectorias: con QPainter.
            self._draw_track_layer_cpu(p, w, h)
        else:
            self.cpu_showers = None
        batch = _Batch(self, w, h)
        if self.show_slice_plane and self.slice_plane is not None:
            self._draw_slice_plane(batch)
        if self.show_dose_grid and self.dose_grid:
            self._draw_dose_grid(batch)
        if self.show_source and self.source is not None and not self.source.from_psf:
            self._draw_source(batch)
        if self.tracks:
            self._draw_tracks(batch)
        batch.flush(p)
        if self.show_dose_grid and self.dose_grid:
            self._draw_grid_handles(p, w, h)
        self._draw_axes(p, w, h)
        for paint in self.overlay_painters:
            paint(p, w, h)
        if not self.show_overlay_info:
            return
        # Rótulos.
        proj = "ortográfica" if self.camera.ortho else "perspectiva"
        title = f"3D · {proj}" + (f" · {VIEW_TITLES[self.view_name]}" if self.view_name else "")
        _text_box(p, QPointF(10, 10), title)
        y = 36
        clip = self.clip()
        if clip:
            _text_box(p, QPointF(10, y), "Corte: " + describe_clip(clip))
            y += 26
        hidden = [f"material {m} {'oculto' if v == HIDDEN else 'translúcido'}"
                  for m, v in sorted(self.material_visibility.items()) if v != OPAQUE]
        if self.hidden_bodies:
            hidden.append(plural(len(self.hidden_bodies), "cuerpo oculto", "cuerpos ocultos"))
        if hidden:
            _text_box(p, QPointF(10, y), "; ".join(hidden))
        if self.backend == "CPU" and self.backend_note:
            fm = p.fontMetrics()
            _text_box(p, QPointF(w - fm.horizontalAdvance(self.backend_note) - 22, 10), self.backend_note)

    MAX_CPU_SEGMENTS = 120_000  # tramos de una cascada dibujados sin GPU (los de las primeras lluvias)

    def _cpu_depth_map(self, w: int, h: int) -> tuple[np.ndarray, int]:
        """Profundidad de lo primero opaco en una rejilla de píxeles (cada ``step``), para tapar las trayectorias
        sin GPU; se calcula de nuevo solo si cambian la cámara, el tamaño, el corte o lo que se ve."""
        step = 3
        state = (self.camera.state(), w, h, tuple(map(tuple, self.clip())),
                 tuple(sorted(self.material_visibility.items())), tuple(sorted(self.hidden_bodies)))
        if self._cpu_depth is not None and self._cpu_depth[0] == state:
            return self._cpu_depth[1], step
        ny, nx = -(-h // step), -(-w // step)
        py, px = np.mgrid[0:ny, 0:nx]
        o, d = self.camera.rays((px.ravel() + 0.5) * step - 0.5, (py.ravel() + 0.5) * step - 0.5, w, h)
        opaque = visibility_array(self.session.geo, {**self.material_visibility,
                                                    **{m: HIDDEN for m, v in self.material_visibility.items()
                                                       if v == TRANSLUCENT}}, self.hidden_bodies)
        tt, kb, _ = self.tracer.render(o.reshape(-1, 3), d.reshape(-1, 3), opaque, self.clip(),
                                       mat_vis=self._mat_vis(opaque_only=True))
        depth = np.where(kb > 0, tt, np.inf).reshape(ny, nx)
        self._cpu_depth = (state, depth)
        return depth, step

    def _draw_track_layer_cpu(self, p: QPainter, w: int, h: int) -> None:
        """Las trayectorias de la cascada sin GPU: líneas con QPainter, lo tapado por la geometría opaca tenue o
        nada (como en la GPU), con su grosor y su estela. Si no caben todas (``MAX_CPU_SEGMENTS``), las primeras
        lluvias enteras que caben, siempre las mismas (también en la animación): ``cpu_showers`` dice cuáles."""
        from PySide6.QtCore import QLineF

        from ..render.track_layer import DRAW_PRIORITY, heads_at

        self.cpu_showers = None
        data, st = self.track_layer, self.track_style
        if data is None or not data.n_segments or self.tracer is None:
            return
        v = data.vertices
        a_all, b_all = data.seg_a, data.seg_b
        mask = st.visible_mask(data)
        vis = np.flatnonzero(mask)
        if len(vis) > self.MAX_CPU_SEGMENTS:
            sh = v[a_all[vis], 6]
            order = np.argsort(sh, kind="stable")
            over = float(sh[order[self.MAX_CPU_SEGMENTS]])  # la lluvia en la que se pasa: fuera, entera
            keep = vis[sh < over - 0.5]
            if not len(keep):  # una sola lluvia con más tramos que el límite: sus primeros
                keep = vis[order[:self.MAX_CPU_SEGMENTS]]
            mask = np.zeros_like(mask)
            mask[keep] = True
            self.cpu_showers = (int(st.shower_lo), max(int(round(over)), int(st.shower_lo) + 1))
        draw = mask.copy()
        if st.animate:
            draw &= v[a_all, 3] + st.start_offsets(data, a_all) <= st.time
        idx = np.flatnonzero(draw)
        if len(idx):
            a, b = a_all[idx], b_all[idx]
            pa = v[a, :3].astype(np.float64) + data.origin
            pb = v[b, :3].astype(np.float64) + data.origin
            ta = tb = None
            if st.animate:
                off = st.start_offsets(data, a)
                ta, tb = v[a, 3] + off, v[b, 3] + off
                frac = np.clip((st.time - ta) / np.maximum(tb - ta, 1e-12), 0.0, 1.0)
                tb = np.minimum(tb, st.time)
                pb = pa + frac[:, None] * (pb - pa)
            cam = self.camera
            # Los tramos largos en pantalla, en trozos (la oclusión se mira en sus extremos).
            q, _z = cam.project(np.vstack([pa, pb]), w, h)
            n0 = len(a)
            px_len = np.linalg.norm(q[n0:] - q[:n0], axis=1)
            parts = np.clip(np.ceil(np.nan_to_num(px_len, nan=1.0) / 16.0), 1, 64).astype(np.int64)
            if parts.max() > 1:
                rep = np.repeat(np.arange(n0), parts)
                k = np.concatenate([np.arange(c) for c in parts])
                f0 = (k / parts[rep])[:, None]
                f1 = ((k + 1) / parts[rep])[:, None]
                pa, pb = pa[rep] + f0 * (pb[rep] - pa[rep]), pa[rep] + f1 * (pb[rep] - pa[rep])
                if tb is not None:
                    tb = ta[rep] + f1[:, 0] * (tb[rep] - ta[rep])
                a = a[rep]
            pts = np.vstack([pa, pb])
            px, z = cam.project(pts, w, h)
            _, _, dist = cam.rays_to(pts)
            depth, step = self._cpu_depth_map(w, h)
            iy = np.clip((px[:, 1] / step).astype(np.int64), 0, depth.shape[0] - 1)
            ix = np.clip((px[:, 0] / step).astype(np.int64), 0, depth.shape[1] - 1)
            scale = float(np.linalg.norm(self.scene_hi - self.scene_lo)) or 1.0
            hidden = dist > depth[iy, ix] + 2.0 * self.tolerance() + 1e-6 * scale + 1e-5 * np.abs(dist)
            n = len(a)
            near = 1e-6 * max(cam.distance, 1e-12)
            front = (z[:n] > near) & (z[n:] > near)
            hid = hidden[:n] | hidden[n:]
            cols = st.colors(data, a)
            # Estela: lo recorrido hace más de «trail» ns, menos intenso (como en la GPU), en cuatro niveles.
            level = np.zeros(n, dtype=np.int64)
            if st.animate and st.trail > 0 and tb is not None:
                level = np.clip(np.floor(np.clip((st.time - tb) / st.trail, 0.0, 1.0) * 3.999), 0, 3).astype(np.int64)
            # Orden de dibujo: el de la GPU (fotones y neutrones debajo, cargadas encima), por color dentro.
            prio = DRAW_PRIORITY[np.clip(v[a, 5].astype(np.int64), 0, len(DRAW_PRIORITY) - 1)]
            ckey = np.round(cols * 15.0).astype(np.int64) @ np.array([256, 16, 1])
            key = (prio * 4096 + ckey) * 4 + level
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            width = max(float(st.line_width), 0.5)
            for kv in np.unique(key):
                sel = key == kv
                rgb = np.clip(np.round(cols[np.flatnonzero(sel)[0]] * 255), 0, 255).astype(int)
                fade = 1.0 - 0.65 * (int(kv) % 4) / 3.0
                for hidden_part, alpha in ((False, st.alpha * fade), (True, st.alpha * st.hidden_alpha * fade)):
                    if alpha <= 0.0:
                        continue
                    s_ = np.flatnonzero(sel & front & (hid == hidden_part))
                    if not len(s_):
                        continue
                    pen = QPen(QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(255 * min(alpha, 1.0))), width)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLines([QLineF(px[i, 0], px[i, 1], px[n + i, 0], px[n + i, 1]) for i in s_])
            p.restore()
        if st.animate and st.heads:
            hp, hc = heads_at(data, st, mask=mask)  # solo las de las lluvias que se dibujan
            if len(hp):
                q, zz = self.camera.project(hp.astype(np.float64) + data.origin, w, h)
                p.save()
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setPen(Qt.PenStyle.NoPen)
                r = 0.5 * st.head_size
                for (x, y), c, zi in zip(q, hc, zz):
                    if zi > 0:
                        p.setBrush(QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), 240))
                        p.drawEllipse(QPointF(x, y), r, r)
                p.restore()

    MAX_TRACK_POINTS = 40_000  # vértices de trayectorias por imagen (cada uno necesita un rayo para la oclusión)

    def _draw_tracks(self, batch: _Batch) -> None:
        """Trayectorias con el color de su partícula; los vértices que se dibujan se reparten entre los tipos de
        partícula (las primeras trayectorias enteras de cada uno)."""
        from ..results.data import fair_shares
        from .results.style import PARTICLE_COLORS

        budgets = fair_shares([len(t.points) for t in self.tracks], self.MAX_TRACK_POINTS)
        for t, budget in zip(self.tracks, budgets):
            h = PARTICLE_COLORS.get(t.particle, "#0b0b0b").lstrip("#")
            rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            for a, b in t.ranges(budget):
                batch.polyline(t.points[a:b, :3], rgb, 1.2 if t.particle != 2 else 0.9)

    def _box_edges(self, batch: _Batch, lo, hi, rgb, width: float = 1.6) -> None:
        lo = np.asarray(lo, dtype=float)
        hi = np.asarray(hi, dtype=float)
        c = [np.array([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
        for a, b in edges:
            batch.line(np.array([c[a], c[b]]), rgb, width)

    def _draw_slice_plane(self, batch: _Batch) -> None:
        pl = self.slice_plane
        k = pl.depth_axis
        if k is None:
            return
        lo, hi = self.scene_lo, self.scene_hi
        d = float(pl.depth)
        if not lo[k] - 1e-9 <= d <= hi[k] + 1e-9:
            return
        i, j = [a for a in range(3) if a != k]
        corners = []
        for a, b in ((lo[i], lo[j]), (hi[i], lo[j]), (hi[i], hi[j]), (lo[i], hi[j])):
            q = np.zeros(3)
            q[i], q[j], q[k] = a, b, d
            corners.append(q)
        batch.line(np.array(corners), PLANE_RGB, 1.2, closed=True, hidden_alpha=70)
        batch.label(corners[3], f"corte {pl.name}  {'xyz'[k]} = {d:.6g}", dx=4, dy=-24)

    # -- la rejilla de dosis: vóxeles y asas ------------------------------------------------------------------
    @property
    def dragging(self) -> bool:
        return self._grid_drag is not None

    def abort_drag(self) -> None:
        self._grid_drag = None
        self.overlay.update()

    def grid_handles(self) -> dict[str, np.ndarray]:
        """Asas de la rejilla de dosis (cm): el centro de cada cara de la caja («lo0», «hi0»… por eje) y su centro
        («move»); el radio y los extremos del cilindro; el radio de la esfera."""
        from ..penhan.sections import dose_grid_from_model

        if not (self.dose_grid and self.show_dose_grid and self.grid_editable):
            return {}
        g = dose_grid_from_model(self.dose_grid)
        values = [v for t in (g.x, g.y, g.z, g.r) if t is not None for v in t]
        if not all(math.isfinite(float(v)) for v in values):
            return {}  # Inf o NaN en el .in (se avisa en los mensajes)
        right = np.asarray(self.camera.basis()[0], dtype=float)
        if g.kind == "box":
            ts = (g.x, g.y, g.z)
            lo = np.array([min(t[0], t[1]) for t in ts], dtype=float)
            hi = np.array([max(t[0], t[1]) for t in ts], dtype=float)
            c = 0.5 * (lo + hi)
            out = {"move": c}
            for k in range(3):
                for end, v in (("lo", lo[k]), ("hi", hi[k])):
                    q = c.copy()
                    q[k] = v
                    out[f"{end}{k}"] = q
            return out
        if g.kind in ("cylinder", "sphere"):
            r = float(g.r[0])
            if g.kind == "cylinder":
                zlo, zhi = sorted(g.z[:2])
                d = np.array([right[0], right[1], 0.0])
                n = float(np.linalg.norm(d))
                d = d / n if n > 1e-9 else np.array([1.0, 0.0, 0.0])
                return {"r": np.array([r * d[0], r * d[1], 0.5 * (zlo + zhi)]), "zlo": np.array([0.0, 0.0, zlo]),
                        "zhi": np.array([0.0, 0.0, zhi])}
            return {"r": r * right}
        return {}

    def usable_grid_handles(self) -> dict[str, np.ndarray]:
        """Las asas que se pueden coger: sin las de las caras que se ven de frente (su eje apunta a la cámara: en
        pantalla caen sobre el rombo de mover y un arrastre las estiraría sin control)."""
        hs = self.grid_handles()
        faces = [n for n in hs if n[:2] in ("lo", "hi") and n[2:].isdigit()]
        if not faces:
            return hs
        w, h = max(1, self.canvas.width()), max(1, self.canvas.height())
        c = hs.get("move")
        if c is None:
            return hs
        L = 2.0 * self.camera.half_height() / h * 20.0
        px, _z = self.camera.project(np.array([c] + [c + L * np.eye(3)[k] for k in range(3)]), w, h)
        per_axis = [float(np.hypot(*(px[k + 1] - px[0]))) for k in range(3)]
        top = max(per_axis) or 1.0
        out = dict(hs)
        for n in faces:
            if per_axis[int(n[2])] < 0.15 * top:
                out.pop(n)
        return out

    def grid_handle_at(self, x: float, y: float) -> str | None:
        hs = self.usable_grid_handles()
        if not hs or self.tracer is None:
            return None
        names = list(hs)
        pts = np.array([hs[n] for n in names])
        w, h = max(1, self.canvas.width()), max(1, self.canvas.height())
        px, z = self.camera.project(pts, w, h)
        best, key = None, (self.GRID_HANDLE_PX + 1e-9, 1)
        for name, q, zz in zip(names, px, z):
            dd = math.hypot(q[0] - x, q[1] - y)
            k = (dd, 0 if name == "move" else 1)  # a igual distancia, el rombo de mover
            if zz > 0 and dd <= self.GRID_HANDLE_PX and k < key:
                best, key = name, k
        return best

    def _axis_cm(self, point: np.ndarray, axis: np.ndarray, dx: float, dy: float) -> float:
        """Cuánto (cm) se ha movido el ratón a lo largo de ``axis`` en ``point`` (la proyección del arrastre)."""
        w, h = max(1, self.canvas.width()), max(1, self.canvas.height())
        L = 20.0 * 2.0 * self.camera.half_height() / h  # unos 20 píxeles: la dirección del eje en ese punto
        px, _ = self.camera.project(np.array([point, point + L * axis]), w, h)
        sdir = (px[1] - px[0]) / L  # píxeles por cm a lo largo del eje
        n2 = float(sdir @ sdir)
        return float((dx * sdir[0] + dy * sdir[1]) / n2) if n2 > 1e-4 else 0.0  # un eje visto de frente: nada

    def _drag_grid(self, x: float, y: float, mods, finished: bool) -> None:
        """Estira la rejilla por una cara (sin cambiar el vóxel; con Ctrl, sin cambiar el número de vóxeles) o la
        mueve en el plano de la pantalla."""
        from ..penhan.sections import GRID_MAX_BINS, DoseGrid, stretch_axis, stretch_radius

        drag = self._grid_drag
        if drag["g0"] is None:
            from ..penhan.sections import dose_grid_from_model

            if not self.dose_grid:
                return
            drag["g0"] = dose_grid_from_model(self.dose_grid)
        g0 = drag["g0"]
        name = drag["handle"]
        dx, dy = x - drag["p0"][0], y - drag["p0"][1]
        keep = not (mods & Qt.KeyboardModifier.ControlModifier)
        # Paso «redondo» de unos 4 píxeles en pantalla (como en el corte, depende de lo cerca que se mire).
        per_px = 2.0 * self.camera.half_height() / max(1, self.canvas.height())
        step = nice_length(4.0 * per_px)
        hs = self.grid_handles()
        start = hs.get(name)
        new = None
        info = ""
        if g0.kind == "box":
            ts = [g0.x, g0.y, g0.z]
            lo = [min(t[0], t[1]) for t in ts]
            hi = [max(t[0], t[1]) for t in ts]
            ns = [t[2] for t in ts]
            if name == "move":
                right, up, _ = self.camera.basis()
                delta = (np.asarray(right) * dx - np.asarray(up) * dy) * per_px
                for k in range(3):
                    sd = round(round(float(delta[k]) / step) * step, 12)
                    lo[k] = float(f"{lo[k] + sd:.12g}") + 0.0
                    hi[k] = float(f"{hi[k] + sd:.12g}") + 0.0
            else:
                end, k = name[:2], int(name[2])
                e = np.zeros(3)
                e[k] = 1.0
                base = lo[k] if end == "lo" else hi[k]
                p0 = np.array([0.5 * (lo[q] + hi[q]) for q in range(3)])
                p0[k] = base
                val = base + self._axis_cm(p0, e, dx, dy)
                if not keep:
                    val = round(round(val / step) * step, 12)
                lo[k], hi[k], ns[k] = stretch_axis(lo[k], hi[k], ns[k], val, end, GRID_MAX_BINS["xyz"[k]], keep)
            new = DoseGrid(*[(lo[q], hi[q], ns[q]) for q in range(3)])
            info = grid_text(new)
        elif g0.kind in ("cylinder", "sphere") and start is not None:
            r, nr = g0.r
            zlo, zhi, nz = 0.0, 0.0, 1  # solo los usa el cilindro
            if g0.kind == "cylinder":
                zlo, zhi = sorted(g0.z[:2])
                nz = g0.z[2]
            if name == "r":
                direction = start / (np.linalg.norm(start[:2] if g0.kind == "cylinder" else start) or 1.0)
                if g0.kind == "cylinder":
                    direction = np.array([direction[0], direction[1], 0.0])
                val = r + self._axis_cm(start, direction, dx, dy)
                if not keep:
                    val = max(step, round(round(val / step) * step, 12))
                r, nr = stretch_radius(r, nr, max(val, 1e-6), GRID_MAX_BINS["r"], keep)
            elif g0.kind == "cylinder" and name in ("zlo", "zhi"):
                base = zlo if name == "zlo" else zhi
                val = base + self._axis_cm(start, np.array([0.0, 0.0, 1.0]), dx, dy)
                if not keep:
                    val = round(round(val / step) * step, 12)
                zlo, zhi, nz = stretch_axis(zlo, zhi, nz, val, name[1:], GRID_MAX_BINS["z"], keep)
            if g0.kind == "cylinder":
                new = DoseGrid(z=(zlo, zhi, nz), r=(r, nr))
            else:
                new = DoseGrid(r=(r, nr))
            info = grid_text(new)
        if new is None:
            return
        drag["last"] = new
        drag["info"] = info
        self.gridChanged.emit(new, finished)
        self.overlay.update()

    def _draw_lattice(self, batch: _Batch, lo, hi, bins) -> list[int]:
        """Los vóxeles en las tres caras del fondo de la caja (las que dan la espalda a la cámara): como papel
        cuadriculado detrás de lo que hay dentro (una línea de cada tantas si son muchos)."""
        lo = np.asarray(lo, dtype=float)
        hi = np.asarray(hi, dtype=float)
        cam_pos = np.asarray(self.camera.position, dtype=float)
        c = 0.5 * (lo + hi)
        rgb = tuple(int(0.75 * v) for v in DOSE_RGB)
        every_axis = [max(1, int(math.ceil((int(n) - 1) / self.MAX_LATTICE_LINES))) if int(n) >= 2 else 1
                      for n in bins]
        for k in range(3):
            # La cara del fondo en el eje k: la que está más lejos de la cámara.
            far = hi[k] if cam_pos[k] < c[k] else lo[k]
            others = [q for q in range(3) if q != k]
            for a in others:
                b = others[0] if a == others[1] else others[1]
                n = int(bins[a])
                if n < 2:
                    continue
                every = max(1, int(math.ceil((n - 1) / self.MAX_LATTICE_LINES)))
                for i in range(every, n, every):
                    t = lo[a] + (hi[a] - lo[a]) * i / n
                    p0 = np.zeros(3)
                    p1 = np.zeros(3)
                    p0[k] = p1[k] = far
                    p0[a] = p1[a] = t
                    p0[b], p1[b] = lo[b], hi[b]
                    batch.line(np.array([p0, p1]), rgb, 0.8, hidden_alpha=40, subdivide=3)
        return every_axis

    def _draw_grid_handles(self, p: QPainter, w: int, h: int) -> None:
        hs = self.usable_grid_handles()
        if not hs:
            return
        names = list(hs)
        px, z = self.camera.project(np.array([hs[n] for n in names]), w, h)
        active = self._grid_drag["handle"] if self._grid_drag is not None else None
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for name, q, zz in zip(names, px, z):
            if zz <= 0:
                continue
            pt = QPointF(float(q[0]), float(q[1]))
            p.setPen(QPen(QColor(20, 20, 20, 220), 1.0))
            p.setBrush(QColor(*DOSE_RGB, 240))
            if name == "move":
                p.drawPolygon(QPolygonF([QPointF(pt.x(), pt.y() - 6), QPointF(pt.x() + 6, pt.y()),
                                         QPointF(pt.x(), pt.y() + 6), QPointF(pt.x() - 6, pt.y())]))
            else:
                p.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))
            if name == active:
                p.setPen(QPen(QColor(255, 255, 255, 230), 1.5, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(pt, 11, 11)
                info = self._grid_drag.get("info")
                if info:
                    _text_box(p, QPointF(pt.x() + 14, pt.y() + 10), info)
        p.restore()

    def _draw_dose_grid(self, batch: _Batch) -> None:
        from ..penhan.sections import dose_grid_from_model
        from ..render.source2d import dose_region
        from .slice_view import finite_region

        reg = dose_region(self.dose_grid)
        if reg is None or not finite_region(reg):
            return  # Inf o NaN en el .in (se avisa en los mensajes): no hay nada que dibujar
        g = dose_grid_from_model(self.dose_grid)
        if reg.kind == "box":
            label = "rejilla de dosis"
            if g.kind == "box":
                every = self._draw_lattice(batch, reg.lo, reg.hi, (g.x[2], g.y[2], g.z[2]))
                label += ": " + grid_text(g) + (" (una línea de cada " + "/".join(str(e) for e in every) +
                                                " vóxeles)" if max(every) > 1 else "")
            self._box_edges(batch, reg.lo, reg.hi, DOSE_RGB, 1.3)
            batch.label(reg.hi, label, dx=4, dy=-22, rgb=DOSE_RGB)
            return
        psi = np.linspace(0, 2 * math.pi, 49)[:-1]
        r = reg.radius
        if reg.kind == "cylinder":
            for z in (reg.zlo, reg.zhi):
                ring = np.stack([r * np.cos(psi), r * np.sin(psi), np.full_like(psi, z)], axis=1)
                batch.line(ring, DOSE_RGB, 1.3, closed=True)
            for a in (0.0, 0.5 * math.pi, math.pi, 1.5 * math.pi):
                batch.line(np.array([[r * math.cos(a), r * math.sin(a), reg.zlo],
                                     [r * math.cos(a), r * math.sin(a), reg.zhi]]), DOSE_RGB, 1.3)
            batch.label(np.array([r, 0.0, reg.zhi]), "rejilla de dosis: " + grid_text(g), dx=4, dy=-22,
                        rgb=DOSE_RGB)
        else:
            z0 = np.zeros_like(psi)
            for ring in (np.stack([r * np.cos(psi), r * np.sin(psi), z0], axis=1),
                         np.stack([r * np.cos(psi), z0, r * np.sin(psi)], axis=1),
                         np.stack([z0, r * np.cos(psi), r * np.sin(psi)], axis=1)):
                batch.line(ring, DOSE_RGB, 1.3, closed=True)
            batch.label(np.array([0.0, 0.0, r]), "rejilla de dosis: " + grid_text(g), dx=4, dy=-22, rgb=DOSE_RGB)

    def _draw_source(self, batch: _Batch) -> None:
        from ..render.source2d import box_corners, emission

        src = self.source
        pos = np.asarray(src.position, dtype=float)
        if not (np.all(np.isfinite(pos)) and np.all(np.isfinite(np.asarray(src.box, dtype=float)))):
            return  # Inf o NaN en el .in (se avisa en los mensajes): no hay nada que dibujar
        L = 0.3 * float(np.linalg.norm(self.scene_hi - self.scene_lo)) or 1.0
        if src.extended:
            c = box_corners(pos, src.box)
            self._box_edges(batch, c.min(axis=0), c.max(axis=0), SOURCE_RGB, 1.8)
        em = emission(src)
        if em.kind == "isotropic":
            for dvec in _sphere_directions():
                batch.arrow(pos, pos + 0.35 * L * dvec, SOURCE_RGB, 1.4)
        elif em.kind != "invalid":  # con Inf o NaN en los ángulos solo se marca la posición
            axis = em.axis
            far = self._exit_distance(pos, axis)  # eje del haz hasta salir de la escena
            if far > L:
                batch.line(np.array([pos + L * axis, pos + far * axis]), SOURCE_RGB, 1.0, hidden_alpha=70,
                           dashed=True)
            if em.kind in ("cone", "rect") and len(em.samples):
                samples = em.samples
                step = max(1, len(samples) // 12)
                for dvec in samples[::step]:
                    batch.line(np.array([pos, pos + L * dvec]), SOURCE_RGB, 1.1)
                batch.line(pos + L * samples, SOURCE_RGB, 1.3, closed=em.kind == "cone")
            batch.arrow(pos, pos + L * axis, SOURCE_RGB, 3.0)
        batch.star(pos)

    def _exit_distance(self, pos: np.ndarray, d: np.ndarray) -> float:
        """Distancia a la que la recta sale de la caja de la escena (0 si no la cruza)."""
        span = self.scene_hi - self.scene_lo
        lo = self.scene_lo - 0.05 * span
        hi = self.scene_hi + 0.05 * span
        t0, t1 = 0.0, math.inf
        for k in range(3):
            if abs(d[k]) < 1e-300:
                if not lo[k] <= pos[k] <= hi[k]:
                    return 0.0
                continue
            a, b = (lo[k] - pos[k]) / d[k], (hi[k] - pos[k]) / d[k]
            t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        return t1 if t1 > t0 else 0.0

    def _draw_axes(self, p: QPainter, w: int, h: int) -> None:
        right, up, fwd = self.camera.basis()
        ox, oy = 44.0, h - 44.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 27, 150))
        p.drawEllipse(QPointF(ox, oy), 38, 38)
        axes = [(np.array([1.0, 0, 0]), "x", QColor("#e15759")), (np.array([0, 1.0, 0]), "y", QColor("#59a14f")),
                (np.array([0, 0, 1.0]), "z", QColor("#76b7ff"))]
        axes.sort(key=lambda a: -float(a[0] @ fwd))  # primero los que se alejan
        for vec, name, col in axes:
            ex, ey = float(vec @ right), -float(vec @ up)
            tip = QPointF(ox + 28 * ex, oy + 28 * ey)
            p.setPen(QPen(col, 2.4))
            p.drawLine(QPointF(ox, oy), tip)
            p.setPen(QColor("#f2f3f5"))
            p.drawText(QPointF(tip.x() + 4 * ex - 3, tip.y() + 4 * ey + 4), name)
        # Escala en el plano del objetivo (en perspectiva, lo más cercano se ve mayor).
        per_px = 2.0 * self.camera.half_height() / max(h, 1)
        length = nice_length(110 * per_px)
        px = length / per_px
        x1, y1 = w - 20 - px, h - 22
        p.setPen(QPen(QColor("#111318"), 5))
        p.drawLine(QPointF(x1, y1), QPointF(x1 + px, y1))
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x1, y1), QPointF(x1 + px, y1))
        _text_box(p, QPointF(x1 + px / 2 - 30, y1 - 26), f"{length:g} cm", center_width=60)


class _Batch:
    """Superposiciones 3D de una imagen: se acumulan y la oclusión se calcula de una vez."""

    def __init__(self, view: View3D, w: int, h: int):
        self.view = view
        self.w, self.h = w, h
        self.items: list[tuple] = []
        self.points: list[np.ndarray] = []
        self.count = 0

    def _add_points(self, pts: np.ndarray) -> slice:
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        s = slice(self.count, self.count + len(pts))
        self.points.append(pts)
        self.count += len(pts)
        return s

    def line(self, pts, rgb, width: float = 1.6, closed: bool = False, hidden_alpha: int = 110,
             dashed: bool = False, subdivide: int = 8) -> None:
        pts = np.asarray(pts, dtype=float)
        if closed:
            pts = np.vstack([pts, pts[:1]])
        fine = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            n = max(1, int(subdivide))
            fine.extend(a + (b - a) * k / n for k in range(1, n + 1))
        s = self._add_points(np.array(fine))
        self.items.append(("line", s, rgb, width, hidden_alpha, dashed))

    def polyline(self, pts, rgb, width: float = 1.2) -> None:
        """Una línea quebrada sin subdividir (trayectorias: ya tienen sus puntos)."""
        s = self._add_points(np.asarray(pts, dtype=float))
        self.items.append(("poly", s, rgb, width))

    def arrow(self, a, b, rgb, width: float) -> None:
        self.line(np.array([a, b]), rgb, width)
        s = self._add_points(np.array([a, b]))
        self.items.append(("head", s, rgb, width))

    def star(self, pos) -> None:
        self.items.append(("star", self._add_points(pos)))

    def label(self, pos, text: str, dx: float = 0, dy: float = 0, rgb=None) -> None:
        self.items.append(("label", self._add_points(pos), text, dx, dy))

    def flush(self, p: QPainter) -> None:
        if not self.items:
            return
        pts = np.vstack(self.points)
        occl = self.view._occluded(pts)
        cam = self.view.camera
        px, z = cam.project(pts, self.w, self.h)
        front = z > 1e-6 * max(cam.distance, 1e-12)
        for it in self.items:
            kind, s = it[0], it[1]
            ip, oc, fr = px[s], occl[s], front[s]
            if kind == "line":
                _, _, rgb, width, hidden_alpha, dashed = it
                pen_vis = QPen(QColor(*rgb, 240), width, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
                pen_hid = QPen(QColor(*rgb, hidden_alpha), max(1.0, width * 0.75), Qt.PenStyle.DotLine)
                for i in range(len(ip) - 1):
                    if fr[i] and fr[i + 1]:
                        p.setPen(pen_hid if (oc[i] or oc[i + 1]) else pen_vis)
                        p.drawLine(QPointF(*ip[i]), QPointF(*ip[i + 1]))
            elif kind == "poly":
                _, _, rgb, width = it
                vis = QPen(QColor(*rgb, 225), width)
                hid = QPen(QColor(*rgb, 70), max(0.8, width * 0.7))
                run: list[QPointF] = []
                run_hidden = None
                for i in range(len(ip)):
                    if not fr[i]:
                        if len(run) > 1:
                            p.setPen(hid if run_hidden else vis)
                            p.drawPolyline(QPolygonF(run))
                        run, run_hidden = [], None
                        continue
                    hidden = bool(oc[i])
                    if run_hidden is not None and hidden != run_hidden:
                        run.append(QPointF(*ip[i]))
                        p.setPen(hid if run_hidden else vis)
                        p.drawPolyline(QPolygonF(run))
                        run = [QPointF(*ip[i])]
                    else:
                        run.append(QPointF(*ip[i]))
                    run_hidden = hidden
                if len(run) > 1:
                    p.setPen(hid if run_hidden else vis)
                    p.drawPolyline(QPolygonF(run))
            elif kind == "head":
                _, _, rgb, width = it
                if not (fr[0] and fr[1]):
                    continue
                pa, pb = QPointF(*ip[0]), QPointF(*ip[1])
                dx, dy = pb.x() - pa.x(), pb.y() - pa.y()
                n = math.hypot(dx, dy)
                if n < 4:
                    continue
                ux, uy = dx / n, dy / n
                sz = 7.0 + 1.5 * width
                left = QPointF(pb.x() - sz * ux + 0.5 * sz * uy, pb.y() - sz * uy - 0.5 * sz * ux)
                right = QPointF(pb.x() - sz * ux - 0.5 * sz * uy, pb.y() - sz * uy + 0.5 * sz * ux)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(*rgb, 110 if oc[1] else 240))
                p.drawPolygon(QPolygonF([pb, left, right]))
            elif kind == "star":
                if not fr[0]:
                    continue
                c = QPointF(*ip[0])
                alpha = 130 if oc[0] else 255
                p.setPen(QPen(QColor(20, 20, 20, alpha), 1.6))
                p.setBrush(QColor(255, 200, 40, alpha))
                star = []
                for k in range(10):
                    rr = 9.0 if k % 2 == 0 else 4.0
                    ang = math.pi / 2 + k * math.pi / 5
                    star.append(QPointF(c.x() + rr * math.cos(ang), c.y() - rr * math.sin(ang)))
                p.drawPolygon(QPolygonF(star))
                if oc[0]:
                    _text_box(p, QPointF(c.x() + 12, c.y() + 8), "fuente (tapada por la geometría)")
            elif kind == "label":
                _, _, text, dx, dy = it
                if fr[0]:
                    _text_box(p, QPointF(ip[0][0] + dx, ip[0][1] + dy), text)


def _snap(value: float, span: float) -> float:
    """Redondea a un paso «redondo» de ~1/200 del intervalo (posición del corte)."""
    step = nice_length(max(abs(span), 1e-12) / 200.0)
    return round(round(value / step) * step, 12)


def _sphere_directions() -> np.ndarray:
    """14 direcciones repartidas por la esfera (ejes y diagonales), para la emisión isótropa."""
    d = [np.array(v, dtype=float) for v in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                d.append(np.array([sx, sy, sz], dtype=float) / math.sqrt(3.0))
    return np.array(d)


def _text_box(p: QPainter, pos: QPointF, text: str, center_width: float | None = None) -> None:
    fm = p.fontMetrics()
    tw = fm.horizontalAdvance(text) + 12
    th = fm.height() + 6
    x = pos.x() if center_width is None else pos.x() + center_width / 2 - tw / 2
    rect = QRectF(x, pos.y(), tw, th)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(20, 22, 27, 170))
    p.drawRoundedRect(rect, 4, 4)
    p.setPen(QColor("#f2f3f5"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


__all__ = ["View3D", "GLCanvas", "CpuCanvas", "HIDDEN", "OPAQUE", "TRANSLUCENT", "SELECT_COLOR"]
