"""Vista 2D de cortes: muestra lo que LOCATE ve en cada píxel.

El cálculo se hace en un hilo de trabajo (``SliceRenderer``). Mientras se
arrastra o se hace zoom se muestra la última imagen transformada y se pide una
versión de baja resolución; al parar, se calcula la resolución completa.

Sobre el corte se dibuja la fuente del .in (posición, caja, cuerpos activos y
flechas de emisión) con asas que se pueden arrastrar:

* estrella: posición de la fuente (SPOSIT);
* punta de la flecha: dirección del haz (SCONE/SRECTA);
* cuadrado en la esquina de la caja: tamaño de la fuente extensa (SBOX).

Con un módulo (o un clon) seleccionado se dibujan también sus asas: la cruz
del centro lo mueve en el plano del corte y el anillo lo gira alrededor del
eje perpendicular al corte que pasa por su centro.

La vista no modifica el .in ni el .geo: emite señales y la ventana principal
aplica el cambio (así se puede deshacer).
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QLineF, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..render.colors import OUTSIDE_COLOR, build_lut, material_lut
from ..render.slice2d import SlicePlane, SliceResult, SliceView, colorize, compute_slice, nice_length, standard_plane

MAX_FULL_PIXELS = 1_600_000  # límite para el render completo en CPU
QUICK_STEP = 4  # submuestreo durante la interacción
SOURCE_COLOR = (255, 127, 14)
MODULE_COLOR = (170, 120, 255)
HANDLE_RADIUS = 11.0
MIN_VOXEL_PX = 4.0  # los vóxeles de la rejilla de dosis se dibujan si miden al menos esto en pantalla
BIG_PX = 1.0e9  # lo que está más lejos (en píxeles) se dibuja aquí: Qt no dibuja infinitos


def _half_chord(r: float, d: float) -> float:
    """√(r² − d²) sin desbordarse con radios enormes («GRIDR 1e200»: r ** 2 lanzaba OverflowError al pintar; décima
    revisión)."""
    a = abs(d)
    return math.sqrt(max(r - a, 0.0) * (r + a)) if r > a else 0.0


def _px(v: float) -> float:
    return min(max(v, -BIG_PX), BIG_PX) if math.isfinite(v) else (BIG_PX if v > 0 else -BIG_PX)


def _pt(q: QPointF) -> QPointF:
    return QPointF(_px(q.x()), _px(q.y()))
BROKEN_GEOMETRY_TEXT = ("La geometría tiene errores: véalos en el panel de mensajes (en «Geometrías complejas», "
                        "debajo del texto).")


@dataclass
class _Job:
    job_id: int
    token: int
    view: SliceView
    width: int
    height: int
    quick: bool


class SliceRenderer(QObject):
    """Hilo que calcula cortes; solo procesa el trabajo más reciente."""

    finished = Signal(object, object)  # (_Job, SliceResult | Exception)

    def __init__(self):
        super().__init__()
        self._cond = threading.Condition()
        self._pending: _Job | None = None
        self._pending_quick: _Job | None = None
        self._locator = None
        self._token = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="argus-slice", daemon=True)
        self._thread.start()

    def set_locator(self, locator) -> int:
        with self._cond:
            self._locator = locator
            self._token += 1
            self._pending = None
            self._pending_quick = None
            return self._token

    def submit(self, job: _Job) -> None:
        with self._cond:
            if job.quick:
                self._pending_quick = job
            else:
                self._pending = job
            self._cond.notify()

    def shutdown(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stop and self._pending is None and self._pending_quick is None:
                    self._cond.wait()
                if self._stop:
                    return
                if self._pending_quick is not None:
                    job, self._pending_quick = self._pending_quick, None
                else:
                    job, self._pending = self._pending, None
                locator = self._locator
                token = self._token
            if locator is None or job.token != token:
                continue
            try:
                res = compute_slice(locator, job.view, job.width, job.height)
            except Exception as exc:  # pragma: no cover - se informa en la interfaz
                res = exc
            with self._cond:
                if self._stop:
                    return
            try:
                self.finished.emit(job, res)
            except RuntimeError:  # el objeto ya no existe (se cerró la vista a mitad del cálculo)
                return


def _snap(value: float, step: float) -> float:
    """Redondea a un múltiplo de ``step`` sin ruido decimal."""
    if step <= 0:
        return value
    return float(f"{round(value / step) * step:.12g}")


def _grid_step(scale: float) -> float:
    """Paso de redondeo de las coordenadas al arrastrar (≈ 1 píxel, en 1·10^n)."""
    return 10.0 ** math.floor(math.log10(max(scale, 1e-12)))


MAX_GRID_CM = 1.0e9  # cm: mucho más que la envolvente de PENGEOM (1e7 cm), fuera de la cual no hay nada


def finite_region(reg) -> bool:
    """¿Tiene la rejilla de dosis (``render.source2d.DoseRegion``) solo números finitos y de un tamaño con sentido? Con
    Inf o NaN en el .in, o con valores de más de ``MAX_GRID_CM`` («GRIDR 1e200»: décima revisión), no se dibuja ni se
    lleva el corte a ella."""
    vals = [reg.radius, reg.zlo, reg.zhi]
    for arr in (reg.lo, reg.hi):
        if arr is not None:
            vals += [float(v) for v in arr]
    return all(math.isfinite(float(v)) and abs(float(v)) <= MAX_GRID_CM for v in vals)


MIN_SCALE = 1.0e-9  # cm por píxel: 10 fm
MAX_SCALE = 1.0e6  # cm por píxel: la envolvente de PENGEOM (1e7 cm) cabe de sobra


class SliceViewWidget(QWidget):
    """Widget del corte 2D."""

    cursorMoved = Signal(object)  # dict: point, body, mat (o None fuera de la vista)
    bodyPicked = Signal(int)  # índice de cuerpo (0 = nada)
    viewChanged = Signal()
    renderTime = Signal(float)
    sourceMoved = Signal(object, bool)  # (nueva posición 3D, arrastre terminado)
    directionChanged = Signal(float, float, bool)  # (θ, φ en grados, arrastre terminado)
    boxResized = Signal(object, bool)  # (lados SBOX, arrastre terminado)
    contextRequested = Signal(object, object, int)  # (QPoint global, punto 3D, cuerpo o 0)
    dragCancelled = Signal()  # Esc durante un arrastre de la fuente
    gridChanged = Signal(object, bool)  # (rejilla de dosis nueva, arrastre terminado)
    # (cuerpo, ("move", Δ) o ("rotate", eje, grados, centro), arrastre terminado)
    moduleDragged = Signal(int, object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.session = None
        self.fit_box = None  # caja que se encuadra (lo que tiene material); None = toda la geometría
        self.plane: SlicePlane = standard_plane("XZ")
        self.center = (0.0, 0.0)
        self.scale = 0.01  # unidades por píxel lógico
        self.color_mode = "material"
        self.draw_edges = True
        self.selected: int | None = None
        self.highlight: set[int] = set()  # cuerpos resaltados (detector del panel Simulación)
        self.material_colors: dict[int, str] = {}
        self._lut = None
        self._mat_lut = None
        self._base: np.ndarray | None = None  # imagen coloreada sin la fuente
        self._image: QImage | None = None
        self._image_view: SliceView | None = None
        self._image_size = (0, 0)
        self._result: SliceResult | None = None
        self._result_is_full = False
        self._job_id = 0
        self._shown_id = -1
        self._token = 0
        self._submit_times: dict[int, float] = {}
        self._drag_start = None
        self._drag_center = None
        self._last_drag_pos: QPointF | None = None  # última posición del arrastre de un asa
        self._moved = False
        self._renderer = SliceRenderer()
        self._renderer.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self._full_timer = QTimer(self)
        self._full_timer.setSingleShot(True)
        self._full_timer.setInterval(140)
        self._full_timer.timeout.connect(lambda: self._request(quick=False))
        self._status_text = "Abra una geometría (.geo) o un fichero de entrada (.in) para verlo."
        # Fuente del .in (objetivo principal de ARGUS).
        self.source = None  # SourceDef
        self.source_active: set[int] = set()
        self.dose_grid: dict | None = None
        self.show_source = True
        self.show_dose_grid = True
        self.source_editable = True
        self._grid_origin = None  # rejilla de dosis al empezar a arrastrarla
        self._grab_offset = (0.0, 0.0)  # del clic al centro del asa (cm en el plano)
        self._drag_mode: str | None = None  # 'source', 'direction', 'box' o None (encuadre)
        self._drag_info: str | None = None
        self._drag_origin = np.zeros(3)
        self._drag_modifiers = Qt.KeyboardModifier.NoModifier
        self._handles: dict[str, QPointF] = {}
        self._hover_handle: str | None = None
        # Asas del módulo seleccionado: {"kb", "center" (3D), "size" (radio en cm del anillo)}.
        self.module_handle: dict | None = None
        self._module_drag: dict | None = None
        # Un mapa de la ventana de resultados sobre el corte (dosis con isolíneas…).
        self.results_overlay = None  # argus.render.dose_overlay.DoseOverlay
        self.tracks = None  # trayectorias de LTRACK (lista de Tracks) proyectadas en el corte
        # Superficies que se dibujan sobre el corte («Geometrías complejas»): (10 coeficientes, rótulo, pendiente).
        self.surface_traces: list[tuple[np.ndarray, str, bool]] = []
        self._overlay_labels: list[tuple[float, float, float]] = []
        self._overlay_problem: str | None = None

    # -- configuración -------------------------------------------------------
    def shutdown(self) -> None:
        self._renderer.shutdown()

    def set_session(self, session, keep_view: bool = False) -> None:
        """Muestra otra geometría; con ``keep_view`` (tras editarla) no cambia ni el corte ni el encuadre."""
        self.session = session
        self.selected = None
        self.highlight = set()
        if self._module_drag is None:
            self.module_handle = None
        if not keep_view:
            self._image = None
            self._base = None
        self._result = None
        if session is None or not session.ok:
            self._token = self._renderer.set_locator(None)
            # Nada del corte anterior: se vería una geometría que ya no es la que hay.
            self._image = None
            self._base = None
            self._image_view = None
            self._status_text = BROKEN_GEOMETRY_TEXT if session is not None else "Sin geometría."
            self.update()
            return
        self._status_text = ""
        self._token = self._renderer.set_locator(session.locator)
        self._rebuild_lut()
        if keep_view:
            self._request(quick=False)
            self.viewChanged.emit()
            return
        k = self.plane.depth_axis
        self.plane = self.plane.with_depth(self.default_depth(k) if k is not None else 0.0)
        self.fit()

    def set_status_text(self, text: str) -> None:
        self._status_text = text
        self.update()

    def default_depth(self, k: int) -> float:
        """Centro de la geometría en el eje ``k``, redondeado (sin ruido numérico)."""
        lo, hi = self.session.bounds
        c = 0.5 * float(lo[k] + hi[k])
        span = float(hi[k] - lo[k]) or 1.0
        q = 10.0 ** math.floor(math.log10(span / 1000.0))
        return round(c / q) * q

    def _rebuild_lut(self) -> None:
        if self.session is not None and self.session.ok:
            self._lut = build_lut(self.session.geo, self.color_mode, self.material_colors)
            self._mat_lut = material_lut(self.material_colors) if self.color_mode == "material" else None

    def set_material_colors(self, colors: dict[int, str]) -> None:
        self.material_colors = dict(colors)
        self._rebuild_lut()
        self._recolor()

    grid_editable = True  # la rejilla de dosis se puede arrastrar (si hay .in)

    def set_source(self, source, active_bodies: set[int] | None = None, grids: dict | None = None,
                   editable: bool = True) -> None:
        """Fuente (SourceDef) y rejilla de dosis del .in que se dibujan sobre el corte."""
        self.source = source
        self.source_active = set(active_bodies or ())
        self.dose_grid = grids
        self.source_editable = editable
        self._compose()

    def set_show_source(self, on: bool) -> None:
        self.show_source = on
        self._compose()

    def set_tracks(self, tracks) -> None:
        """Trayectorias (lista de argus.results.data.Tracks) que se dibujan proyectadas en el corte, o None."""
        self.tracks = tracks or None
        self.update()

    def set_results_overlay(self, overlay) -> None:
        """Un mapa de resultados (DoseOverlay) sobre el corte, o None para quitarlo."""
        self.results_overlay = overlay
        self._overlay_problem = None
        self._compose()

    def set_show_dose_grid(self, on: bool) -> None:
        self.show_dose_grid = on
        self.update()

    def focus_source(self) -> None:
        """Pone el corte a la altura de la fuente (si la hay) y encuadra."""
        src = self.source
        if src is None or src.from_psf or self.session is None or not self.session.ok:
            return
        k = self.plane.depth_axis
        if k is not None and math.isfinite(float(src.position[k])):  # Inf o NaN: el corte no se mueve
            self.plane = self.plane.with_depth(float(src.position[k]))
        self.fit()

    def set_color_mode(self, mode: str) -> None:
        self.color_mode = mode
        self._rebuild_lut()
        self._recolor()

    def set_draw_edges(self, on: bool) -> None:
        self.draw_edges = on
        self._recolor()

    def set_surface_traces(self, traces) -> None:
        """Superficies que se trazan sobre el corte: ``[(coeficientes, rótulo, sin_aplicar)]`` (o ninguna)."""
        self.surface_traces = [(np.asarray(q, dtype=float), str(text), bool(pending)) for q, text, pending in
                               (traces or [])]
        self.update()

    def _draw_surface_traces(self, p: QPainter) -> None:
        from ..render.quadric_trace import trace_segments

        w, h = self.width(), self.height()
        view = self.view()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cache = getattr(self, "_trace_cache", {})
        frame = (self.plane.origin.tobytes(), self.plane.u.tobytes(), self.plane.v.tobytes(), tuple(view.center),
                 view.scale, w, h)
        fresh = {}
        for q, text, pending in self.surface_traces:
            key = (q.tobytes(),) + frame
            segs = cache.get(key)
            if segs is None:
                segs = trace_segments(q, self.plane, view, w, h)
            fresh[key] = segs
            if not len(segs):
                continue
            lines = [QLineF(*map(float, sg)) for sg in segs]
            p.setPen(QPen(QColor(20, 22, 27, 200), 4.0))
            p.drawLines(lines)
            color = QColor("#ffb347") if pending else QColor("#4fd1ff")
            pen = QPen(color, 2.0)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLines(lines)
            if text:
                k = int(np.argmin(segs[:, 1] + 0.001 * segs[:, 0]))  # el punto más alto de la curva
                x, y = float(segs[k, 0]), float(segs[k, 1])
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(text) + 12
                x = min(max(4.0, x - tw / 2), w - tw - 4.0)
                y = min(max(28.0, y - fm.height() - 12), h - fm.height() - 10)
                self._text_box(p, QPointF(x, y), text)
        self._trace_cache = fresh  # lo de este encuadre (al moverlo o hacer zoom se vuelve a calcular)

    def set_highlight(self, kbs) -> None:
        kbs = set(kbs or ())
        if kbs != self.highlight:
            self.highlight = kbs
            self._recolor()

    def set_selected(self, kb: int | None) -> None:
        self.selected = kb if kb else None
        self._recolor()

    def set_module_handle(self, kb: int | None, center=None, extent=None) -> None:
        """Asas para mover y girar el módulo o clon ``kb`` (None: sin asas)."""
        if not kb or center is None:
            self.module_handle = None
        else:
            lo, hi = (np.asarray(v, dtype=float) for v in extent)
            self.module_handle = {"kb": int(kb), "center": np.asarray(center, dtype=float),
                                  "size": 0.5 * float(np.linalg.norm(hi - lo))}
        self.update()

    def _source_coord(self, k: int) -> float | None:
        """Coordenada ``k`` de la fuente que se muestra (None sin fuente o si en el .in es Inf o NaN)."""
        src = self.source
        if src is None or src.from_psf or not self.show_source:
            return None
        s = float(src.position[k])
        return s if math.isfinite(s) else None

    def set_plane(self, name: str) -> None:
        depth = 0.0
        if self.session is not None and self.session.ok:
            k = standard_plane(name).depth_axis
            old_k = self.plane.depth_axis
            s = self._source_coord(k) if k is not None else None
            if old_k == k and math.isfinite(float(self.plane.depth)):
                depth = float(self.plane.depth)
            elif s is not None:
                depth = s  # que la fuente siga en el corte
            else:
                depth = self.default_depth(k)
        self.plane = standard_plane(name, depth)
        self.fit()

    def set_depth(self, value: float) -> None:
        if not math.isfinite(float(value)):  # p. ej. una rejilla de dosis con Inf en el .in: el corte no se mueve
            return
        self.plane = self.plane.with_depth(value)
        self._request(quick=True)
        self.viewChanged.emit()

    def depth_range(self) -> tuple[float, float]:
        if self.session is None or not self.session.ok:
            return -1.0, 1.0
        lo, hi = self.session.bounds
        k = self.plane.depth_axis
        if k is None:
            return -1.0, 1.0
        lo_k, hi_k = float(lo[k]), float(hi[k])
        s = self._source_coord(k)
        if s is not None:
            lo_k, hi_k = min(lo_k, s), max(hi_k, s)
        return lo_k, hi_k

    fitted = False  # encuadrada con la vista visible (oculta, su tamaño no es el de verdad)

    def fit(self) -> None:
        if self.session is None or not self.session.ok:
            self.fitted = False
            self.update()
            return
        lo, hi = self.fit_box if self.fit_box is not None else self.session.bounds
        pts = [np.array([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        src = self.source
        if src is not None and not src.from_psf and self.show_source:
            from ..render.source2d import box_corners

            pts.append(np.asarray(src.position, dtype=float))
            if src.extended:
                pts.extend(box_corners(src.position, src.box))
        pts = [p for p in pts if np.all(np.isfinite(p))]  # una fuente en Inf o NaN no cuenta
        ab = np.array([self.plane.coords(p) for p in pts])
        amin, bmin = ab.min(axis=0)
        amax, bmax = ab.max(axis=0)
        self.center = (0.5 * (amin + amax), 0.5 * (bmin + bmax))
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        span_a = max(amax - amin, 1e-12)
        span_b = max(bmax - bmin, 1e-12)
        self.scale = 1.08 * max(span_a / w, span_b / h)
        self.fitted = self.isVisible()
        self._request(quick=True)
        self.viewChanged.emit()

    def view(self) -> SliceView:
        return SliceView(self.plane, self.center, self.scale)

    # -- render ---------------------------------------------------------------
    def _request(self, quick: bool) -> None:
        if self.session is None or not self.session.ok:
            return
        w, h = self.width(), self.height()
        if w < 2 or h < 2:
            return
        self._job_id += 1
        if quick:
            step = QUICK_STEP
            wi, hi_ = max(1, math.ceil(w / step)), max(1, math.ceil(h / step))
            view = SliceView(self.plane, self.center, self.scale * w / wi)
            self._renderer.submit(_Job(self._job_id, self._token, view, wi, hi_, True))
            self._full_timer.start()
        else:
            dpr = self.devicePixelRatioF()
            wi, hi_ = int(w * dpr), int(h * dpr)
            if wi * hi_ > MAX_FULL_PIXELS:
                f = math.sqrt(MAX_FULL_PIXELS / (wi * hi_))
                wi, hi_ = max(1, int(wi * f)), max(1, int(hi_ * f))
            view = SliceView(self.plane, self.center, self.scale * w / wi)
            self._renderer.submit(_Job(self._job_id, self._token, view, wi, hi_, False))
        self._submit_times[self._job_id] = time.perf_counter()

    def _on_finished(self, job: _Job, res) -> None:
        t0 = self._submit_times.pop(job.job_id, None)
        for old in [k for k in self._submit_times if k < job.job_id]:
            del self._submit_times[old]  # trabajos sustituidos por otros más recientes
        if job.token != self._token or job.job_id < self._shown_id:
            return
        if isinstance(res, Exception):
            self._status_text = f"Error al calcular el corte: {res}"
            self.update()
            return
        self._shown_id = job.job_id
        self._result = res
        self._result_is_full = not job.quick
        self._recolor()
        if t0 is not None and not job.quick:
            self.renderTime.emit(time.perf_counter() - t0)

    @property
    def dragging(self) -> bool:
        """True mientras se arrastra un asa (de la fuente, de la rejilla o de un módulo)."""
        return self._drag_mode is not None

    def abort_drag(self) -> None:
        """Termina el arrastre en curso sin emitir nada más (la ventana ya lo ha anulado)."""
        self._drag_mode = None
        self._drag_start = None
        self._drag_info = None
        self._module_drag = None
        self.update()

    def is_idle(self) -> bool:
        """True si la imagen mostrada es la definitiva (para pruebas y capturas)."""
        return self._result_is_full and self._shown_id == self._job_id and not self._full_timer.isActive()

    def _recolor(self) -> None:
        """Colorea el último corte (materiales, fronteras, selección)."""
        if self._result is None or self._lut is None:
            self._base = None
            self.update()
            return
        self._base = colorize(self._result, self._lut, self.draw_edges, self.selected, highlight=self.highlight,
                              mat_lut=self._mat_lut)
        self._compose()

    def _compose(self) -> None:
        """Añade a la imagen la región donde la fuente extensa genera partículas."""
        if self._base is None or self._result is None:
            self.update()
            return
        rgba = self._base
        src = self.source
        if self.show_source and src is not None and not src.from_psf and src.extended:
            from ..render.source2d import source_region

            reg = source_region(self._result, src, self.source_active)
            if reg is not None:
                rows, cols, mask = reg
                if mask.any():
                    rgba = rgba.copy()
                    sub = rgba[rows, cols]
                    col = np.array(SOURCE_COLOR, dtype=np.float32)
                    blend = sub[mask, :3].astype(np.float32) * 0.45 + col * 0.55
                    # Rayado diagonal para distinguirla de un material naranja.
                    yy, xx = np.nonzero(mask)
                    stripe = ((xx + cols.start + yy + rows.start) // 3) % 2 == 0
                    blend[stripe] *= 0.8
                    sub[mask, :3] = blend.astype(np.uint8)
                    rgba[rows, cols] = sub
        self._overlay_labels = []
        ov = self.results_overlay
        if ov is not None:
            rgba = self._apply_results_overlay(rgba, ov)
        h, w = rgba.shape[:2]
        img = QImage(np.ascontiguousarray(rgba).data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        self._image = img
        self._image_view = self._result.view
        self._image_size = (w, h)
        self.update()

    def _apply_results_overlay(self, rgba: np.ndarray, ov) -> np.ndarray:
        from ..render.dose_overlay import apply_overlay
        from ..render.slice2d import slice_points

        res = self._result
        mask = None
        if ov.grid.geometry == "ct":
            if res.ctmat is None:
                return rgba  # el corte no pasa por el TAC
            mask = res.ctmat >= 0
        try:
            pts = slice_points(res.view, res.width, res.height)
            scale = max(1, int(round(self.devicePixelRatioF()))) if self._result_is_full else 1
            out, labels = apply_overlay(rgba, pts, ov, mask, line_scale=scale)
        except ValueError as exc:
            self._overlay_problem = str(exc)
            return rgba
        self._overlay_problem = None
        self._overlay_labels = labels
        return out

    # -- dibujo ---------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(OUTSIDE_COLOR))
        if self._image is not None and self._image_view is not None:
            iv = self._image_view
            wi, hi_ = self._image_size
            k = iv.scale / self.scale
            w, h = self.width(), self.height()
            # Centro de la imagen (en coordenadas del plano) -> píxel actual.
            x0 = 0.5 * w + (iv.center[0] - self.center[0]) / self.scale - 0.5 * wi * k
            y0 = 0.5 * h - (iv.center[1] - self.center[1]) / self.scale - 0.5 * hi_ * k
            same_plane = (np.allclose(iv.plane.origin, self.plane.origin) and np.allclose(iv.plane.u, self.plane.u)
                          and np.allclose(iv.plane.v, self.plane.v))
            if not same_plane:
                p.setOpacity(0.35)
            p.drawImage(QRectF(x0, y0, wi * k, hi_ * k), self._image, QRectF(0, 0, wi, hi_))
            p.setOpacity(1.0)
            if same_plane and self._overlay_labels:
                self._draw_isoline_labels(p, x0, y0, k)
        else:
            p.setPen(QColor("#c8ccd4"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._status_text)
        self._handles = {}
        if self.tracks:
            self._draw_tracks(p)
        if self.session is not None and self.session.ok:
            if self.dose_grid and self.show_dose_grid:
                self._draw_dose_grid(p)
            if self.show_source and self.source is not None:
                self._draw_source(p)
            if self.module_handle is not None:
                self._draw_module_handles(p)
            if self.surface_traces:
                self._draw_surface_traces(p)
            self._draw_overlays(p)
            if self.results_overlay is not None:
                self._draw_results_legend(p)
        p.end()

    MAX_TRACK_POINTS = 80_000

    def _draw_tracks(self, p: QPainter) -> None:
        """Trayectorias proyectadas en el plano del corte (con el color de cada tipo de partícula). Los puntos que
        se dibujan se reparten entre los tipos de partícula: las primeras trayectorias enteras de cada uno."""
        from ..results.data import fair_shares
        from .results.style import PARTICLE_COLORS

        w, h = self.width(), self.height()
        pl = self.plane
        cx, cy = self.center
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        budgets = fair_shares([len(t.points) for t in self.tracks], self.MAX_TRACK_POINTS)
        groups = []
        for t, budget in zip(self.tracks, budgets):
            ranges = t.ranges(budget)
            if not ranges:
                continue
            pts = t.points[:ranges[-1][1], :3] - pl.origin  # solo los puntos que se dibujan
            xs = 0.5 * w + (pts @ pl.u - cx) / self.scale
            ys = 0.5 * h - (pts @ pl.v - cy) / self.scale
            polys = [QPolygonF([QPointF(float(xs[j]), float(ys[j])) for j in range(a, b)]) for a, b in ranges]
            col = QColor(PARTICLE_COLORS.get(t.particle, "#0b0b0b"))
            col.setAlpha(215)
            groups.append((polys, 1.0 if t.particle != 2 else 0.8, col))
        # Primero un borde claro bajo todas (se ven sobre el color de cualquier material, también uno parecido al de
        # la partícula), y después cada una con el color de su partícula.
        for polys, width, _col in groups:
            halo = QPen(QColor(252, 252, 251, 150), width + 2.0)
            halo.setCapStyle(Qt.PenCapStyle.RoundCap)
            halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(halo)
            for poly in polys:
                p.drawPolyline(poly)
        for polys, width, col in groups:
            p.setPen(QPen(col, width))
            for poly in polys:
                p.drawPolyline(poly)

    def _draw_isoline_labels(self, p: QPainter, x0: float, y0: float, k: float) -> None:
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.85))
        p.setFont(f)
        fm = p.fontMetrics()
        placed: list[QRectF] = []
        for lv, x, y in sorted(self._overlay_labels, key=lambda t: -t[0]):
            text = f"{lv:g} %"
            px, py = x0 + (x + 0.5) * k + 4, y0 + (y + 0.5) * k - fm.height() / 2
            rect = QRectF(px, py, fm.horizontalAdvance(text) + 8, fm.height() + 2)
            for _ in range(6):  # si choca con otra etiqueta, un poco más abajo
                if not any(rect.intersects(r) for r in placed):
                    break
                rect.translate(0, fm.height() + 3)
            else:
                continue
            placed.append(QRectF(rect))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(252, 252, 251, 215))
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(QColor(11, 11, 11))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_results_legend(self, p: QPainter) -> None:
        """Barra de colores del mapa de resultados (arriba a la derecha) con sus isolíneas."""
        from PySide6.QtGui import QLinearGradient

        from ..render.dose_overlay import default_lut

        ov = self.results_overlay
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.85))
        p.setFont(f)
        fm = p.fontMetrics()
        ref = ov.reference() * ov.unit_factor
        qty = ov.quantity or "Dosis"
        title = ov.label or (f"{qty} ({ov.unit})" if ov.unit else qty)
        lines = [title, f"100 % = {ref:.4g} {ov.unit}".rstrip()]
        if self._overlay_problem:
            lines = [title, self._overlay_problem]
        bar_h = 110
        tw = max(fm.horizontalAdvance(t) for t in lines) + 16
        w_box = max(tw, 150)
        x = self.width() - w_box - 10
        y = 40.0
        h_box = 12 + len(lines) * (fm.height() + 2) + (bar_h + 10 if not self._overlay_problem else 0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 27, 170))
        p.drawRoundedRect(QRectF(x, y, w_box, h_box), 6, 6)
        p.setPen(QColor("#f2f3f5"))
        ty = y + 6
        for t in lines:
            p.drawText(QRectF(x + 8, ty, w_box - 16, fm.height() + 2), Qt.AlignmentFlag.AlignLeft, t)
            ty += fm.height() + 2
        if self._overlay_problem:
            return
        lut = ov.lut if ov.lut is not None else default_lut()
        bx, by = x + 10, ty + 4
        grad = QLinearGradient(QPointF(bx, by + bar_h), QPointF(bx, by))
        for i in range(0, 256, 32):
            c = lut[int(i * (len(lut) - 1) / 255)]
            grad.setColorAt(i / 255.0, QColor(int(c[0]), int(c[1]), int(c[2])))
        c = lut[-1]
        grad.setColorAt(1.0, QColor(int(c[0]), int(c[1]), int(c[2])))
        p.setBrush(grad)
        p.setPen(QPen(QColor("#f2f3f5"), 0.8))
        p.drawRect(QRectF(bx, by, 12, bar_h))
        # Marcas: 100 %, 0 y las isolíneas (sin rótulo las que no caben).
        marks = [100.0, 0.0] + sorted((float(v) for v in ov.levels if 0 < v < 100), reverse=True)
        used: list[float] = []
        for m in marks:
            yy = by + bar_h * (1.0 - m / 100.0)
            p.setPen(QPen(QColor("#f2f3f5"), 1.0))
            p.drawLine(QPointF(bx + 12, yy), QPointF(bx + 17, yy))
            if any(abs(yy - u) < fm.height() * 0.9 for u in used):
                continue
            used.append(yy)
            iso = m in ov.levels and ov.isolines
            p.drawText(QPointF(bx + 20, yy + fm.ascent() / 2 - 1), f"{m:g} %" + ("  —" if iso else ""))

    def _to_px(self, a: float, b: float) -> QPointF:
        x, y = self.view().plane_to_pixel(a, b, self.width(), self.height())
        return QPointF(x, y)

    def _voxel_pen(self) -> QPen:
        return QPen(QColor(90, 200, 255, 85), 1.0)

    def _draw_voxels_box(self, p: QPainter, g, iu: int, iv: int, rect: QRectF) -> None:
        """Las líneas de los vóxeles de la caja en el corte (solo las de un eje cuyos vóxeles miden al menos
        MIN_VOXEL_PX píxeles: si no, el corte se llenaría de líneas)."""
        axes = [g.x, g.y, g.z]
        p.save()
        p.setPen(self._voxel_pen())
        p.setClipRect(rect.adjusted(-1, -1, 1, 1))
        for axis, horizontal in ((iu, False), (iv, True)):
            lo, hi, n = axes[axis]
            if n < 2 or abs(hi - lo) / n / self.scale < MIN_VOXEL_PX:
                continue
            for k in range(1, n):
                c = lo + (hi - lo) * k / n
                if horizontal:
                    y = _px(self._to_px(0.0, c).y())
                    p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                else:
                    x = _px(self._to_px(c, 0.0).x())
                    p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        p.restore()

    def _draw_voxels_round(self, p: QPainter, g, d: float, k: int, center: QPointF, rect: QRectF | None) -> None:
        """Capas del cilindro (anillos y planos z) o de la esfera en el corte."""
        r_out, nr = g.r
        if nr < 1:
            return
        dr = r_out / nr
        p.save()
        p.setPen(self._voxel_pen())
        p.setBrush(Qt.BrushStyle.NoBrush)
        cyl = g.kind == "cylinder"
        if cyl and k != 2:
            # Corte que contiene el eje z: las capas en z (horizontales) y los anillos (verticales, a ±√(r² − d²)).
            if rect is not None:
                p.setClipRect(rect.adjusted(-1, -1, 1, 1))
                zlo, zhi, nz = g.z
                if nz > 1 and abs(zhi - zlo) / nz / self.scale >= MIN_VOXEL_PX:
                    for q in range(1, nz):
                        y = _px(self._to_px(0.0, zlo + (zhi - zlo) * q / nz).y())
                        p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                if dr / self.scale >= MIN_VOXEL_PX:
                    for q in range(1, nr):
                        rq = q * dr
                        if rq > abs(d):
                            half = _px(_half_chord(rq, d) / self.scale)
                            for sx in (-1.0, 1.0):
                                x = center.x() + sx * half
                                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        elif dr / self.scale >= MIN_VOXEL_PX:
            # Corte perpendicular al eje (cilindro) o cualquier corte (esfera): circunferencias.
            for q in range(1, nr):
                rq = q * dr
                if rq > (0.0 if cyl else abs(d)):
                    rp = _px(_half_chord(rq, 0.0 if cyl else d) / self.scale)
                    p.drawEllipse(center, rp, rp)
        p.restore()

    @staticmethod
    def _voxel_text(g) -> str:
        def f(v: float) -> str:
            return f"{v:.3g}".replace(".", ",")

        if g.kind == "box":
            return "vóxel " + " × ".join(f(abs(t[1] - t[0]) / t[2]) for t in (g.x, g.y, g.z)) + " cm"
        if g.kind == "cylinder":
            return f"anillos de {f(g.r[0] / g.r[1])} cm, capas de {f(abs(g.z[1] - g.z[0]) / g.z[2])} cm"
        if g.kind == "sphere":
            return f"capas de {f(g.r[0] / g.r[1])} cm"
        return ""

    def _draw_dose_grid(self, p: QPainter) -> None:
        from ..penhan.sections import dose_grid_from_model
        from ..render.source2d import dose_region

        reg = dose_region(self.dose_grid)
        g = dose_grid_from_model(self.dose_grid)
        plane = self.plane
        k = plane.depth_axis
        if reg is None or k is None or not finite_region(reg):
            return  # Inf o NaN en el .in (se avisa en los mensajes): no hay nada que dibujar
        d = plane.depth
        iu = int(np.argmax(np.abs(plane.u)))
        iv = int(np.argmax(np.abs(plane.v)))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(90, 200, 255, 230), 1.4, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        label_at = None
        handles: dict[str, QPointF] = {}
        if reg.kind == "box":
            if not reg.lo[k] <= d <= reg.hi[k]:
                return
            q0 = _pt(self._to_px(reg.lo[iu], reg.lo[iv]))
            q1 = _pt(self._to_px(reg.hi[iu], reg.hi[iv]))
            rect = QRectF(q0, q1).normalized()
            if g.kind == "box":
                self._draw_voxels_box(p, g, iu, iv, rect)
            p.drawRect(rect)
            label_at = rect.topLeft()
            handles = {"grid_lo": q0, "grid_hi": q1, "grid_move": rect.center()}
        elif reg.kind == "cylinder":
            r = reg.radius
            if k == 2:
                if reg.zlo <= d <= reg.zhi:
                    c = self._to_px(0.0, 0.0)
                    if g.kind == "cylinder":
                        self._draw_voxels_round(p, g, d, k, c, None)
                    rp = _px(r / self.scale)
                    p.drawEllipse(c, rp, rp)
                    label_at = _pt(QPointF(c.x() - rp, c.y() - rp))
                    handles = {"grid_r": _pt(QPointF(c.x() + rp, c.y()))}
            elif abs(d) < r:
                half = _half_chord(r, d)
                lo = np.zeros(3)
                hi = np.zeros(3)
                lo[:2], hi[:2] = -half, half
                lo[2], hi[2] = reg.zlo, reg.zhi
                q0 = _pt(self._to_px(lo[iu], lo[iv]))
                q1 = _pt(self._to_px(hi[iu], hi[iv]))
                rect = QRectF(q0, q1).normalized()
                if g.kind == "cylinder":
                    self._draw_voxels_round(p, g, d, k, self._to_px(0.0, 0.0), rect)
                p.drawRect(rect)
                label_at = rect.topLeft()
                handles = {"grid_r": QPointF(rect.right(), rect.center().y()),
                           "grid_zlo": QPointF(rect.center().x(), rect.bottom()),
                           "grid_zhi": QPointF(rect.center().x(), rect.top())}
        elif reg.kind == "sphere":
            if abs(d) < reg.radius:
                r = _px(_half_chord(reg.radius, d) / self.scale)
                c = self._to_px(0.0, 0.0)
                if g.kind == "sphere":
                    self._draw_voxels_round(p, g, d, k, c, None)
                p.drawEllipse(c, r, r)
                label_at = QPointF(c.x() - r, c.y() - r)
                handles = {"grid_r": QPointF(c.x() + r, c.y())}
        if self.grid_editable:
            p.setPen(QPen(QColor(20, 20, 20, 220), 1.0))
            p.setBrush(QColor(90, 200, 255, 240))
            for name, h in handles.items():
                self._handles[name] = h
                if name == "grid_move":
                    p.drawPolygon(QPolygonF([QPointF(h.x(), h.y() - 5), QPointF(h.x() + 5, h.y()),
                                             QPointF(h.x(), h.y() + 5), QPointF(h.x() - 5, h.y())]))
                else:
                    p.drawRect(QRectF(h.x() - 4, h.y() - 4, 8, 8))
            # El asa bajo el ratón (o la que se arrastra) y lo que dice el arrastre: aquí y no con la fuente, que
            # puede estar oculta o ser un PSF.
            hv = self._drag_mode or self._hover_handle
            if hv in handles:
                p.setPen(QPen(QColor(255, 255, 255, 230), 1.5, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(handles[hv], HANDLE_RADIUS + 1, HANDLE_RADIUS + 1)
            if self._drag_info and self._drag_mode in handles:
                h = handles[self._drag_mode]
                self._text_box(p, QPointF(h.x() + 14, h.y() + 12), self._drag_info)
            p.setPen(QPen(QColor(90, 200, 255, 230), 1.4, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
        if label_at is not None:
            f = QFont(self.font())
            f.setPointSizeF(max(7.5, f.pointSizeF() * 0.85))
            p.setFont(f)
            p.setPen(QColor(90, 200, 255, 255))
            vox = self._voxel_text(g)
            p.drawText(QPointF(label_at.x() + 3, label_at.y() - 4), "rejilla de dosis" + (f" · {vox}" if vox else ""))

    def _draw_source(self, p: QPainter) -> None:
        from ..render.source2d import box_projection, box_section, emission, project

        src = self.source
        if src.from_psf:
            return
        plane = self.plane
        pos = np.asarray(src.position, dtype=float)
        if not (np.all(np.isfinite(pos)) and np.all(np.isfinite(np.asarray(src.box, dtype=float)))):
            return  # Inf o NaN en el .in (se avisa en los mensajes): no hay nada que dibujar
        a, b = plane.coords(pos)
        dist = float((pos - plane.origin) @ plane.normal)
        near = abs(dist) <= max(2.0 * self.scale, 1e-9)
        sec = box_section(plane, pos, src.box) if src.extended else None
        if src.extended:
            near = near or sec is not None
        alpha = 255 if near else 120
        orange = QColor(*SOURCE_COLOR, alpha)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Caja de la fuente extensa: sección (continua) o proyección (discontinua).
        if src.extended:
            rect = sec or box_projection(plane, pos, src.box)
            if rect is not None:
                q0 = self._to_px(rect[0], rect[1])
                q1 = self._to_px(rect[2], rect[3])
                pen = QPen(QColor(*SOURCE_COLOR, 255 if sec else 170), 2.0 if sec else 1.2,
                           Qt.PenStyle.SolidLine if sec else Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(q0, q1).normalized())
                corner = QPointF(max(q0.x(), q1.x()), min(q0.y(), q1.y()))
                self._handles["box"] = corner
                p.setPen(QPen(QColor(20, 20, 20, 230), 1.2))
                p.setBrush(QColor(*SOURCE_COLOR, 255 if sec else 170))
                p.drawRect(QRectF(corner.x() - 4.5, corner.y() - 4.5, 9, 9))
        c = self._to_px(a, b)
        self._handles["source"] = c
        # Dirección de emisión.
        em = emission(src)
        L = 70.0
        if em.kind == "isotropic":
            for k in range(12):
                ang = 2 * math.pi * k / 12
                tip = QPointF(c.x() + 0.55 * L * math.cos(ang), c.y() - 0.55 * L * math.sin(ang))
                self._arrow_line(p, c, tip, orange)
        elif em.kind != "invalid":  # con Inf o NaN en los ángulos solo se marca la posición
            ax = project(plane, em.axis)[0]
            r = math.hypot(ax[0], ax[1])
            if em.kind in ("cone", "rect") and len(em.samples):
                self._draw_fan(p, c, em, ax, r, near, L)
            if r < 1e-6:
                self._draw_perpendicular(p, c, ax[2] > 0, orange)
            else:
                ux, uy = ax[0] / r, -ax[1] / r
                tip = QPointF(c.x() + L * ux * max(r, 0.35), c.y() + L * uy * max(r, 0.35))
                # Trayectoria del eje del haz hasta el borde de la vista.
                far = QPointF(c.x() + 4000 * ux, c.y() + 4000 * uy)
                p.setPen(QPen(QColor(*SOURCE_COLOR, 150 if near else 80), 1.2, Qt.PenStyle.DashLine))
                p.drawLine(c, far)
                self._arrow_line(p, c, tip, orange, width=3.0)
                self._handles["direction"] = tip
                if r < 0.35:
                    # Casi perpendicular al corte: indicar también hacia qué lado sale.
                    self._draw_perpendicular(p, c, ax[2] > 0, orange, badge=True)
        # Marcador de la posición.
        p.setPen(QPen(QColor(20, 20, 20, alpha), 1.6))
        p.setBrush(QColor(255, 200, 40, alpha) if near else QColor(255, 200, 40, 90))
        star = []
        for k in range(10):
            rr = 9.0 if k % 2 == 0 else 4.0
            ang = math.pi / 2 + k * math.pi / 5
            star.append(QPointF(c.x() + rr * math.cos(ang), c.y() - rr * math.sin(ang)))
        p.drawPolygon(QPolygonF(star))
        mine = ("source", "direction", "box")  # las de la rejilla y el módulo se dibujan con ellos
        hv = self._drag_mode or self._hover_handle
        if hv in mine and hv in self._handles and self.source_editable:
            h = self._handles[hv]
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.5, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(h, HANDLE_RADIUS + 1, HANDLE_RADIUS + 1)
        if not near and self._drag_mode is None:
            self._text_box(p, QPointF(c.x() + 12, c.y() + 10), f"fuente a {abs(dist):.4g} cm del corte")
        if self._drag_info and self._drag_mode in mine:
            h = self._handles.get(self._drag_mode, c)
            self._text_box(p, QPointF(h.x() + 14, h.y() + 12), self._drag_info)

    def _draw_fan(self, p: QPainter, c: QPointF, em, ax, r: float, near: bool, L: float) -> None:
        """Proyección sobre el corte del cono (o ventana SRECTA) de emisión."""
        from ..render.source2d import project

        pr = project(self.plane, em.samples)
        ang_axis = math.atan2(ax[1], ax[0]) if r > 1e-9 else 0.0
        ang = np.arctan2(pr[:, 1], pr[:, 0]) - ang_axis
        ang = (ang + np.pi) % (2 * np.pi) - np.pi
        inplane = np.hypot(pr[:, 0], pr[:, 1])
        covers_normal = False
        if em.kind == "cone":
            al = self.source.cone[2]
            cosang = abs(float(em.axis @ self.plane.normal))
            covers_normal = math.degrees(math.acos(min(1.0, cosang))) <= al
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(*SOURCE_COLOR, 60 if near else 30))
        if covers_normal or r < 1e-9:
            p.drawEllipse(c, 0.8 * L, 0.8 * L)
            return
        order = np.argsort(ang)
        amin, amax = float(ang.min()), float(ang.max())
        poly = [c]
        for t in np.linspace(amin, amax, 24):
            rr = 0.8 * L * max(0.25, float(np.interp(t, ang[order], inplane[order])))
            poly.append(QPointF(c.x() + rr * math.cos(ang_axis + t), c.y() - rr * math.sin(ang_axis + t)))
        p.drawPolygon(QPolygonF(poly))
        # Bordes del abanico, prolongados hasta el borde de la vista (huella del haz en el corte).
        for t in (amin, amax):
            ex, ey = math.cos(ang_axis + t), -math.sin(ang_axis + t)
            p.setPen(QPen(QColor(*SOURCE_COLOR, 200 if near else 110), 1.2))
            p.drawLine(c, QPointF(c.x() + 0.8 * L * ex, c.y() + 0.8 * L * ey))
            p.setPen(QPen(QColor(*SOURCE_COLOR, 110 if near else 50), 1.0, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(c.x() + 0.8 * L * ex, c.y() + 0.8 * L * ey), QPointF(c.x() + 4000 * ex,
                                                                                   c.y() + 4000 * ey))

    def _draw_perpendicular(self, p: QPainter, c: QPointF, outward: bool, color: QColor,
                            badge: bool = False) -> None:
        """Símbolo ⊙ (el haz sale del corte hacia el observador) o ⊗ (entra), con el eje."""
        k = self.plane.depth_axis
        axis = ""
        if k is not None:
            sign = float(self.plane.normal[k]) * (1.0 if outward else -1.0)
            axis = ("+" if sign > 0 else "−") + "xyz"[k]
        q = QPointF(c.x() + 30, c.y() - 30)
        rad = 10.0
        p.setPen(QPen(color, 1.8))
        p.setBrush(QColor(20, 22, 27, 170))
        p.drawEllipse(q, rad + 3, rad + 3)
        if outward:
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(q, 3.5, 3.5)
        else:
            p.drawLine(QPointF(q.x() - 6, q.y() - 6), QPointF(q.x() + 6, q.y() + 6))
            p.drawLine(QPointF(q.x() - 6, q.y() + 6), QPointF(q.x() + 6, q.y() - 6))
        if not badge:
            p.setPen(QPen(color, 1.0, Qt.PenStyle.DotLine))
            p.drawLine(c, QPointF(q.x() - rad * 0.75, q.y() + rad * 0.75))
        if axis:
            self._text_box(p, QPointF(q.x() + rad + 6, q.y() - 10), f"haz hacia {axis}")

    def _arrow_line(self, p: QPainter, a: QPointF, b: QPointF, color: QColor, width: float = 2.0) -> None:
        p.setPen(QPen(color, width))
        p.drawLine(a, b)
        d = b - a
        n = math.hypot(d.x(), d.y())
        if n < 1e-6:
            return
        ux, uy = d.x() / n, d.y() / n
        left = QPointF(b.x() - 11 * ux + 5.5 * uy, b.y() - 11 * uy - 5.5 * ux)
        right = QPointF(b.x() - 11 * ux - 5.5 * uy, b.y() - 11 * uy + 5.5 * ux)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([b, left, right]))

    def _module_geometry(self) -> tuple[QPointF, float, float] | None:
        """(centro en píxeles, radio del anillo en píxeles, ángulo del asa de giro) del módulo seleccionado."""
        mh = self.module_handle
        if mh is None:
            return None
        center = mh["center"]
        drag = self._module_drag
        if drag is not None and drag["mode"] == "mod_move":
            center = drag["center"] + drag.get("delta", np.zeros(3))
        a, b = self.plane.coords(center)
        c = self._to_px(a, b)
        r = min(max(mh["size"] / self.scale * 0.75, 38.0), 0.4 * min(self.width(), self.height()))
        ang = math.radians(45.0)
        if drag is not None and drag["mode"] == "mod_rot":
            ang += math.radians(drag.get("angle", 0.0))
        return c, r, ang

    def _draw_module_handles(self, p: QPainter) -> None:
        g = self._module_geometry()
        if g is None:
            return
        c, r, ang = g
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(*MODULE_COLOR, 230)
        p.setPen(QPen(col, 1.6, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, r, r)
        knob = QPointF(c.x() + r * math.cos(ang), c.y() - r * math.sin(ang))
        self._handles["mod_move"] = c
        self._handles["mod_rot"] = knob
        # Cruz del centro (mover) y bola del anillo (girar).
        p.setPen(QPen(QColor(20, 20, 20, 230), 1.2))
        p.setBrush(col)
        p.drawRect(QRectF(c.x() - 5, c.y() - 5, 10, 10))
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.4))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p.drawLine(QPointF(c.x() + 7 * dx, c.y() + 7 * dy), QPointF(c.x() + 12 * dx, c.y() + 12 * dy))
        p.setPen(QPen(QColor(20, 20, 20, 230), 1.2))
        p.setBrush(col)
        p.drawEllipse(knob, 6.5, 6.5)
        hv = self._drag_mode or self._hover_handle
        if hv in ("mod_move", "mod_rot"):
            h = self._handles[hv]
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.5, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(h, HANDLE_RADIUS + 1, HANDLE_RADIUS + 1)
        if self._drag_info and self._drag_mode in ("mod_move", "mod_rot"):
            h = self._handles[self._drag_mode]
            self._text_box(p, QPointF(h.x() + 14, h.y() + 12), self._drag_info)

    def _handle_at(self, pos: QPointF) -> str | None:
        names = []
        if self.module_handle is not None:
            names += ["mod_rot", "mod_move"]
        src = []
        if self.show_source and self.source is not None and self.source_editable:
            src = ["direction", "box", "source"]
        # Las asas de la rejilla de dosis se ven (y se cogen) aunque la fuente esté oculta o sea un PSF.
        grid = []
        if self.dose_grid and self.show_dose_grid and self.grid_editable:
            grid = ["grid_lo", "grid_hi", "grid_r", "grid_zlo", "grid_zhi", "grid_move"]
        # A igual distancia gana la fuente si está en el corte; si no (un haz centrado visto en el corte XY, con la
        # fuente detrás), la rejilla: si no, su rombo de mover quedaría debajo de la estrella de la fuente.
        names += (src + grid) if self._source_in_plane() else (grid + src)
        best, best_d = None, HANDLE_RADIUS + 1e-9
        for name in names:
            h = self._handles.get(name)
            if h is None:
                continue
            dist = math.hypot(h.x() - pos.x(), h.y() - pos.y())
            if dist < best_d - 0.5:  # el más cercano; a igual distancia, el de más prioridad
                best, best_d = name, dist
        return best

    def _source_in_plane(self) -> bool:
        src = self.source
        k = self.plane.depth_axis
        if src is None or k is None:
            return True
        try:
            z = float(np.asarray(src.position, dtype=float)[k])
        except (TypeError, ValueError, IndexError):
            return True
        return abs(z - self.plane.depth) <= max(1e-6, 2.0 * self.scale)  # a dos píxeles del plano

    def _draw_overlays(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.95))
        p.setFont(font)
        # Etiqueta del plano.
        axis = "xyz"[self.plane.depth_axis] if self.plane.depth_axis is not None else "d"
        label = f"Corte {self.plane.name}   {axis} = {self.plane.depth:.6g} cm"
        self._text_box(p, QPointF(10, 10), label)
        # Barra de escala.
        target = 120 * self.scale
        length = nice_length(target)
        px = length / self.scale
        x1, y1 = w - 20 - px, h - 22
        pen = QPen(QColor("#111318"), 5)
        p.setPen(pen)
        p.drawLine(QPointF(x1, y1), QPointF(x1 + px, y1))
        pen = QPen(QColor("#ffffff"), 2)
        p.setPen(pen)
        p.drawLine(QPointF(x1, y1), QPointF(x1 + px, y1))
        self._text_box(p, QPointF(x1 + px / 2 - 30, y1 - 26), f"{length:g} cm", center_width=60)
        # Ejes (sobre un recuadro oscuro para que se lean en cualquier fondo).
        ox, oy = 26.0, h - 26.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 27, 150))
        p.drawRoundedRect(QRectF(ox - 14, oy - 58, 76, 70), 6, 6)
        self._arrow(p, QPointF(ox, oy), QPointF(ox + 40, oy), self.plane.u_label, QColor("#e15759"))
        self._arrow(p, QPointF(ox, oy), QPointF(ox, oy - 40), self.plane.v_label, QColor("#59a14f"))

    def _text_box(self, p: QPainter, pos: QPointF, text: str, center_width: float | None = None) -> None:
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

    def _arrow(self, p: QPainter, a: QPointF, b: QPointF, text: str, color: QColor) -> None:
        pen = QPen(color, 2.2)
        p.setPen(pen)
        p.drawLine(a, b)
        d = b - a
        n = math.hypot(d.x(), d.y())
        ux, uy = d.x() / n, d.y() / n
        left = QPointF(b.x() - 8 * ux + 4 * uy, b.y() - 8 * uy - 4 * ux)
        right = QPointF(b.x() - 8 * ux - 4 * uy, b.y() - 8 * uy + 4 * ux)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([b, left, right]))
        p.setPen(QColor("#f2f3f5"))
        p.drawText(QPointF(b.x() + 5 * ux - 4 + 6 * abs(uy), b.y() + 5 * uy + 4 - 4 * abs(ux)), text)

    # -- interacción ----------------------------------------------------------
    def point_at(self, px: float, py: float) -> np.ndarray:
        a, b = self.view().pixel_to_plane(px, py, self.width(), self.height())
        return self.plane.point(a, b)

    def body_at(self, px: float, py: float) -> tuple[int, int]:
        if self.session is None or not self.session.ok:
            return 0, 0
        pt = self.point_at(px, py)
        ib, mat = self.session.locator.locate(pt[None, :], self.plane.normal)
        return int(ib[0]), int(mat[0])

    _PAN_BUTTONS = Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton

    def _end_lost_drag(self) -> None:
        """El botón se ha soltado sin que la vista lo sepa (un menú o un diálogo se quedó con la suelta): el
        arrastre termina donde se vio por última vez, como si se hubiese soltado allí (y se puede deshacer)."""
        if self._drag_mode is not None and self._moved and self._last_drag_pos is not None:
            self._drag_source(self._last_drag_pos, True)
        self._module_drag = None
        self._drag_mode = None
        self._drag_start = None
        self._drag_info = None
        self.update()

    def mousePressEvent(self, e) -> None:
        if self._drag_mode is not None:
            if e.button() != Qt.MouseButton.LeftButton and e.buttons() & Qt.MouseButton.LeftButton:
                return  # otro botón a mitad del arrastre: no cuenta
            self._end_lost_drag()  # un clic nuevo: la suelta del arrastre anterior no llegó
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self._handle_at(e.position())
            if handle is not None:
                self._drag_mode = handle
                self._drag_start = e.position()
                self._last_drag_pos = QPointF(e.position())
                if handle.startswith("mod_"):
                    mh = self.module_handle
                    self._module_drag = {"mode": handle, "kb": mh["kb"], "center": mh["center"].copy()}
                elif handle.startswith("grid"):
                    # La rejilla de partida se toma al moverse: un valor escrito sin Intro en un panel se aplica
                    # justo después de este clic (al perder el foco) y el arrastre no debe pisarlo. Lo que separa el
                    # clic del centro del asa se mantiene: el asa no salta al cursor.
                    self._grid_origin = None
                    h = self._handles.get(handle)
                    w_, h_ = self.width(), self.height()
                    if h is not None:
                        ha = self.view().pixel_to_plane(h.x(), h.y(), w_, h_)
                        pa = self.view().pixel_to_plane(e.position().x(), e.position().y(), w_, h_)
                        self._grab_offset = (ha[0] - pa[0], ha[1] - pa[1])
                    else:
                        self._grab_offset = (0.0, 0.0)
                else:
                    self._drag_origin = np.asarray(self.source.position, dtype=float)
                self._drag_info = None
                self._moved = False
                return
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag_mode = None
            self._drag_start = e.position()
            self._drag_center = self.center
            self._moved = False

    def _in_plane_step(self) -> float:
        step = _grid_step(self.scale)
        if self._drag_modifiers & Qt.KeyboardModifier.ShiftModifier:
            step *= 10.0
        elif self._drag_modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            step /= 10.0
        return step

    def _angle_step(self) -> float:
        if self._drag_modifiers & Qt.KeyboardModifier.ShiftModifier:
            return 15.0
        if self._drag_modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            return 0.1
        return 1.0

    def _drag_grid(self, pos: QPointF, finished: bool) -> None:
        """Arrastre de la rejilla de dosis: esquinas y centro de la caja, radio y extremos del cilindro..."""
        from ..core.numfmt import fmt_num
        from ..penhan.sections import GRID_MAX_BINS, DoseGrid, grid_text, stretch_axis, stretch_radius

        if self._grid_origin is None and self.dose_grid:
            from ..penhan.sections import dose_grid_from_model

            self._grid_origin = dose_grid_from_model(self.dose_grid)
        g0 = self._grid_origin
        plane = self.plane
        k = plane.depth_axis
        if g0 is None or k is None:
            return
        a, b = self.view().pixel_to_plane(pos.x(), pos.y(), self.width(), self.height())
        a, b = a + self._grab_offset[0], b + self._grab_offset[1]
        step = self._in_plane_step()
        iu = int(np.argmax(np.abs(plane.u)))
        iv = int(np.argmax(np.abs(plane.v)))
        d = plane.depth
        mode = self._drag_mode
        du = _snap((pos.x() - self._drag_start.x()) * self.scale, step)
        dv = _snap(-(pos.y() - self._drag_start.y()) * self.scale, step)
        # Al estirarla, el vóxel no cambia (cambia el número de vóxeles); con Ctrl, cambia el vóxel y no su número.
        keep = not (self._drag_modifiers & Qt.KeyboardModifier.ControlModifier)
        new = None
        if g0.kind == "box":
            ts = [g0.x, g0.y, g0.z]
            lo = [min(t[0], t[1]) for t in ts]
            hi = [max(t[0], t[1]) for t in ts]
            ns = [t[2] for t in ts]
            if mode == "grid_move":
                # Sin restos de coma flotante en el .in (0.1 + 0.2 = 0.30000000000000004): 12 cifras.
                lo[iu] = float(f"{lo[iu] + du:.12g}") + 0.0
                hi[iu] = float(f"{hi[iu] + du:.12g}") + 0.0
                lo[iv] = float(f"{lo[iv] + dv:.12g}") + 0.0
                hi[iv] = float(f"{hi[iv] + dv:.12g}") + 0.0
            elif mode in ("grid_lo", "grid_hi"):
                end = mode[-2:]
                for axis, val in ((iu, a), (iv, b)):
                    lo[axis], hi[axis], ns[axis] = stretch_axis(lo[axis], hi[axis], ns[axis],
                                                                val if keep else _snap(val, step), end,
                                                                GRID_MAX_BINS["xyz"[axis]], keep)
            new = DoseGrid(*[(lo[i], hi[i], ns[i]) for i in range(3)])
            c = [fmt_num(v, 6) for v in (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])]
            self._drag_info = (f"caja x {c[0]}…{c[1]}, y {c[2]}…{c[3]}, z {c[4]}…{c[5]} cm: {grid_text(new)}")
        elif g0.kind == "cylinder":
            zlo, zhi = sorted(g0.z[:2])
            nz = g0.z[2]
            r, nr = g0.r
            if mode == "grid_r":
                val = math.hypot(a, b) if k == 2 else math.sqrt(a * a + d * d)
                r, nr = stretch_radius(r, nr, val if keep else max(step, _snap(val, step)), GRID_MAX_BINS["r"], keep)
            elif mode in ("grid_zlo", "grid_zhi") and k != 2:
                zlo, zhi, nz = stretch_axis(zlo, zhi, nz, b if keep else _snap(b, step), mode[-2:],
                                            GRID_MAX_BINS["z"], keep)
            new = DoseGrid(z=(zlo, zhi, nz), r=(r, nr))
            self._drag_info = f"cilindro {grid_text(new)}"
        elif g0.kind == "sphere" and mode == "grid_r":
            val = math.sqrt(a * a + b * b + d * d)
            r, nr = stretch_radius(g0.r[0], g0.r[1], val if keep else max(step, _snap(val, step)),
                                   GRID_MAX_BINS["r"], keep)
            new = DoseGrid(r=(r, nr))
            self._drag_info = f"esfera {grid_text(new)}"
        if new is not None:
            self.gridChanged.emit(new, finished)

    def _drag_module(self, pos: QPointF, finished: bool) -> None:
        """Arrastre de las asas del módulo: mover en el plano del corte o girar alrededor de su normal."""
        drag = self._module_drag
        if drag is None:
            return
        plane = self.plane
        if drag["mode"] == "mod_move":
            step = self._in_plane_step()
            du = _snap((pos.x() - self._drag_start.x()) * self.scale, step)
            dv = _snap(-(pos.y() - self._drag_start.y()) * self.scale, step)
            delta = du * plane.u + dv * plane.v
            delta = np.array([float(f"{v:.12g}") + 0.0 for v in delta])
            drag["delta"] = delta
            self._drag_info = "desplazamiento ({:.6g}, {:.6g}, {:.6g}) cm".format(*delta)
            self.moduleDragged.emit(drag["kb"], ("move", delta), finished)
        else:
            c, _r, _ang = self._module_geometry()
            a0 = math.degrees(math.atan2(-(self._drag_start.y() - c.y()), self._drag_start.x() - c.x()))
            a1 = math.degrees(math.atan2(-(pos.y() - c.y()), pos.x() - c.x()))
            ang = (a1 - a0 + 180.0) % 360.0 - 180.0
            ang = _snap(ang, self._angle_step())
            drag["angle"] = ang
            axis = plane.normal
            k = int(np.argmax(np.abs(axis)))
            name = ("+" if axis[k] > 0 else "−") + "xyz"[k] if np.abs(axis).max() > 1 - 1e-12 else "la normal del corte"
            self._drag_info = f"giro de {ang:g}° alrededor de {name}"
            self.moduleDragged.emit(drag["kb"], ("rotate", axis.copy(), ang, drag["center"].copy()), finished)
        if finished:
            self._module_drag = None
        self.update()

    def _drag_source(self, pos: QPointF, finished: bool) -> None:
        from ..render.source2d import angles_of, direction

        if self._drag_mode is not None and self._drag_mode.startswith("mod_"):
            self._drag_module(pos, finished)
            return
        if self._drag_mode is not None and self._drag_mode.startswith("grid"):
            self._drag_grid(pos, finished)
            return
        src = self.source
        plane = self.plane
        a, b = self.view().pixel_to_plane(pos.x(), pos.y(), self.width(), self.height())
        if self._drag_mode == "source":
            # Desplazamiento desde el inicio del arrastre, aplicado a la posición original: una
            # coordenada que no se mueve (0 píxeles) se conserva exacta; las demás se redondean.
            step = self._in_plane_step()
            old = self._drag_origin
            a0, b0 = plane.coords(old)
            dpx = pos.x() - self._drag_start.x()
            dpy = pos.y() - self._drag_start.y()
            a = a0 if abs(dpx) < 0.5 else _snap(a0 + dpx * self.scale, step)
            b = b0 if abs(dpy) < 0.5 else _snap(b0 - dpy * self.scale, step)
            k = plane.depth_axis
            if k is not None:
                new = old.copy()
                iu = int(np.argmax(np.abs(plane.u)))
                iv = int(np.argmax(np.abs(plane.v)))
                new[iu] = a  # planos XY/XZ/YZ: u y v son ejes positivos
                new[iv] = b
            else:
                depth = float((old - plane.origin) @ plane.normal)
                new = plane.point(a, b) + depth * plane.normal
            self._drag_info = "x = {:.6g}   y = {:.6g}   z = {:.6g} cm".format(*new)
            self.sourceMoved.emit(new, finished)
        elif self._drag_mode == "direction":
            c = self._handles.get("source")
            if c is None:
                return
            dx, dy = pos.x() - c.x(), -(pos.y() - c.y())
            if math.hypot(dx, dy) < 3:
                return
            kind, par = src.angular()
            d0 = direction(par[0], par[1]) if kind == "cone" else direction(0.5 * (par[0] + par[1]),
                                                                              0.5 * (par[2] + par[3]))
            comp_n = float(d0 @ plane.normal)
            r = math.sqrt(max(0.0, 1.0 - comp_n * comp_n))
            if r < 1e-3:
                r, comp_n = 1.0, 0.0
            n = math.hypot(dx, dy)
            d = r * (dx / n * plane.u + dy / n * plane.v) + comp_n * plane.normal
            th, ph = angles_of(d)
            step = self._angle_step()
            th, ph = _snap(th, step), _snap(ph, step) % 360.0
            if th <= 0.0 or th >= 180.0:
                ph = 0.0
            self._drag_info = f"θ = {th:g}°   φ = {ph:g}°"
            self.directionChanged.emit(th, ph, finished)
        elif self._drag_mode == "box":
            k = plane.depth_axis
            if k is None:
                return
            step = self._in_plane_step()
            ca, cb = plane.coords(np.asarray(src.position, dtype=float))
            size = [abs(float(v)) for v in src.box]
            iu = int(np.argmax(np.abs(plane.u)))
            iv = int(np.argmax(np.abs(plane.v)))
            size[iu] = max(step, _snap(2.0 * abs(a - ca), step))
            size[iv] = max(step, _snap(2.0 * abs(b - cb), step))
            self._drag_info = "caja {:.6g} × {:.6g} × {:.6g} cm".format(*size)
            self.boxResized.emit(size, finished)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._drag_start is not None:
            held = Qt.MouseButton.LeftButton if self._drag_mode is not None else self._PAN_BUTTONS
            if not e.buttons() & held:
                self._end_lost_drag()  # sin el botón pulsado ya no se arrastra
        if self._drag_start is not None and self._drag_mode is not None:
            self._moved = True
            self._drag_modifiers = e.modifiers()
            self._last_drag_pos = QPointF(pos)
            self._drag_source(pos, False)
            return
        if self._drag_start is None:
            hv = self._handle_at(pos)
            if hv != self._hover_handle:
                self._hover_handle = hv
                cursor = {"mod_move": Qt.CursorShape.SizeAllCursor, "mod_rot": Qt.CursorShape.PointingHandCursor,
                          "source": Qt.CursorShape.SizeAllCursor, "direction": Qt.CursorShape.CrossCursor,
                          "box": Qt.CursorShape.SizeBDiagCursor, "grid_move": Qt.CursorShape.SizeAllCursor,
                          "grid_lo": Qt.CursorShape.SizeBDiagCursor, "grid_hi": Qt.CursorShape.SizeBDiagCursor,
                          "grid_r": Qt.CursorShape.SizeHorCursor, "grid_zlo": Qt.CursorShape.SizeVerCursor,
                          "grid_zhi": Qt.CursorShape.SizeVerCursor}.get(hv, Qt.CursorShape.ArrowCursor)
                self.setCursor(cursor)
                self.update()
        if self._drag_start is not None:
            d = pos - self._drag_start
            if abs(d.x()) + abs(d.y()) > 3:
                self._moved = True
            if self._moved:
                self.center = (self._drag_center[0] - d.x() * self.scale, self._drag_center[1] + d.y() * self.scale)
                self._request(quick=True)
                self.update()
                self.viewChanged.emit()
        if self.session is not None and self.session.ok:
            kb, mat = self.body_at(pos.x(), pos.y())
            self.cursorMoved.emit({"point": self.point_at(pos.x(), pos.y()), "body": kb, "mat": mat,
                                   "direction": np.asarray(self.plane.normal, dtype=float)})

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_mode is not None:
            if self._moved:
                self._drag_modifiers = e.modifiers()
                self._drag_source(e.position(), True)
            self._module_drag = None
            self._drag_mode = None
            self._drag_start = None
            self._drag_info = None
            self.update()
            return
        if self._drag_start is not None and not self._moved and e.button() == Qt.MouseButton.LeftButton:
            kb, _ = self.body_at(e.position().x(), e.position().y())
            if self.session is not None and kb > self.session.geo.nbodys:
                kb = 0
            self.bodyPicked.emit(kb)
        self._drag_start = None

    def contextMenuEvent(self, e) -> None:
        if self.session is None or not self.session.ok:
            return
        if self._drag_start is not None:
            # Arrastrando (o con el botón izquierdo o central pulsado) no sale el menú: se quedaría con la suelta
            # del botón y el arrastre seguiría sin él.
            e.accept()
            return
        pos = QPointF(e.pos())
        kb, _ = self.body_at(pos.x(), pos.y())
        if kb > self.session.geo.nbodys:
            kb = 0
        self.contextRequested.emit(e.globalPos(), self.point_at(pos.x(), pos.y()), kb)

    def leaveEvent(self, e) -> None:
        self.cursorMoved.emit(None)

    def wheelEvent(self, e) -> None:
        steps = e.angleDelta().y() / 120.0
        if steps == 0:
            return
        factor = 0.8 ** steps
        self.zoom(factor, e.position())

    def zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        w, h = self.width(), self.height()
        if anchor is None:
            anchor = QPointF(w / 2, h / 2)
        a, b = self.view().pixel_to_plane(anchor.x(), anchor.y(), w, h)
        # Límites (cm por píxel): más allá, la escala acaba en 0 o infinito y el corte ya no se puede recuperar.
        self.scale = min(max(self.scale * factor, MIN_SCALE), MAX_SCALE)
        # Mantener fijo el punto bajo el cursor.
        self.center = (a - (anchor.x() - 0.5 * w) * self.scale, b - (0.5 * h - anchor.y()) * self.scale)
        self._request(quick=True)
        self.update()
        self.viewChanged.emit()

    def keyPressEvent(self, e) -> None:
        key = e.key()
        if key == Qt.Key.Key_F:
            self.fit()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom(0.8)
        elif key == Qt.Key.Key_Minus:
            self.zoom(1.25)
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            lo, hi = self.depth_range()
            step = (hi - lo) / 50 or 1.0
            self.set_depth(self.plane.depth + (step if key == Qt.Key.Key_PageUp else -step))
        elif key == Qt.Key.Key_Escape and self._drag_mode is not None:
            self._drag_mode = None
            self._drag_start = None
            self._drag_info = None
            self._module_drag = None
            self.dragCancelled.emit()
            self.update()
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._request(quick=True)
