"""Diálogo «Importar un TAC (DICOM)»: de una serie DICOM a una caja CT de PENGEOM.

Pasos: elegir la carpeta (se buscan las series), elegir la serie (se leen los
cortes), ajustar el recorte, el tamaño de vóxel, la calibración (HU → material
y densidad), dónde va y cómo se coloca, y el nombre del .ct. Las vistas previas
muestran el TAC (ventana de HU) o los tejidos, con el recorte encima.

El diálogo solo prepara y escribe el .ct y su ficha; la ventana principal
añade el CTBOX a la geometría y los materiales al .in (un paso de deshacer).
"""

from __future__ import annotations

import html
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..ct import Cancelled
from ..ct.calibration import MNIS, PRESETS, Calibration, Tissue, preset, read_curve_file
from ..ct.dicom import DicomError, HuVolume, Series, read_series, scan_folder
from ..ct.grid import PLACEMENTS, auto_crop, estimate, make_grid, suggest_voxel
from ..ct.importer import (
    BuiltCt,
    CtImport,
    MaterialPlan,
    commit_written,
    ct_name_problem,
    discard_written,
    isotope_summary,
    plan_materials,
)
from ..pengeom.ct import NDD, human_size
from ..penhan.materials import mat_filename_problem
from ..render.colors import hex_to_rgb, material_color
from .screen_fit import fit_to_screen
from .spinbox import DecimalSpinBox

PRESET_LABELS = {"schneider5": "Schneider 2000 · 5 tejidos (H C N O P Ca) — recomendada",
                 "penelope": "Tejidos ICRP de la lista de PENELOPE (más de 30 isótopos)",
                 "agua": "Agua y aire (densidad variable)"}
VOXEL_CHOICES = ("Como el TAC", "1 mm", "1,5 mm", "2 mm", "2,5 mm", "3 mm", "4 mm", "5 mm")
MAX_VOXELS_WARN = 10_000_000
DEFAULT_DESCRIPTION = "TAC importado con ARGUS"


def free_ct_name(folder: Path | None, base: str = "tac") -> str:
    """«tac.ct», o «tac2.ct», «tac3.ct»... el primero que no exista en ``folder``."""
    if folder is None:
        return f"{base}.ct"
    k = 1
    while True:
        name = f"{base}.ct" if k == 1 else f"{base}{k}.ct"
        if not (Path(folder) / name).exists():
            return name
        k += 1


def hadron_warnings(cal, split: str = "auto") -> list[str]:
    """Avisos de la calibración con protones o alfas (penhmain 2025: pérdida continua con la densidad del .mat).

    ``split``: cómo se van a partir los tejidos por densidad al importar («auto», «max» o «none»).
    """
    from ..ct.calibration import penhmat_composition

    out = []
    fixed = [t.name for t in cal.tissues if t.penelope_id is not None and not penhmat_composition(t)]
    water = [t.name for t in cal.tissues if t.composition is None and t.name.lower().startswith("agua")]
    if water and split == "none":
        out.append("Con protones o alfas, penhmain (2025) calcula la pérdida continua de energía dentro del CT con "
                   "la densidad del .mat y no con la de cada vóxel: con «" + ", ".join(water) + "» de densidad "
                   "variable y sin partir por densidad, los alcances saldrán mal. Parta los tejidos por densidad o "
                   "use Schneider (5 tejidos).")
    elif water:
        out.append("«" + ", ".join(water) + "»: todo el cuerpo tendrá la composición del agua (también el hueso y "
                   "el pulmón); Schneider (5 tejidos) es más realista.")
    if fixed:
        out.append("Los .mat de la lista de PENELOPE (" + ", ".join(fixed) + ") tienen su densidad fija y no se "
                   "pueden partir por densidad; penhmain (2025) usa la del .mat para la pérdida continua de los "
                   "protones dentro del CT.")
    return out


def _finite(text: str) -> float:
    """Número de una celda: «inf», «nan» o «1e400» no valen (ValueError)."""
    v = float(text)
    if not np.isfinite(v):
        raise ValueError(text)
    return v


def _mm(text: str) -> float:
    return float(text.split()[0].replace(",", "."))


def _es(v: float, dec: int = 2) -> str:
    """Número con coma decimal."""
    return f"{v:.{dec}f}".replace(".", ",")


def distinct_hu(hu: np.ndarray) -> np.ndarray:
    """Valores de HU distintos (ordenados) de un trozo del TAC.

    Con HU enteros (int16, lo normal) se marcan en un histograma, por bloques de cortes, en vez de ordenar todo el
    recorte: np.unique tardaba segundos con 50 millones de vóxeles, en el hilo de la interfaz y cada vez que se
    cambiaba el recorte. Con otros tipos, np.unique.
    """
    if hu.dtype != np.int16 or hu.ndim != 3:
        return np.unique(hu)
    seen = np.zeros(1 << 16, dtype=bool)
    step = max(1, 4_000_000 // max(1, hu.shape[1] * hu.shape[2]))
    for k in range(0, hu.shape[0], step):
        codes = (hu[k:k + step].view(np.uint16) ^ np.uint16(0x8000)).ravel()  # int16 → 0…65535 en el mismo orden
        seen |= np.bincount(codes, minlength=1 << 16) > 0
    return (np.flatnonzero(seen) - 32768).astype(np.int16)


# ---------------------------------------------------------------------------
# Tareas en segundo plano
# ---------------------------------------------------------------------------

class _Task(QObject):
    """Ejecuta ``fn(progress, cancel)`` en un hilo y avisa (en el hilo de la interfaz) al terminar.

    ``on_lost(res)``: se llama (en el hilo de la tarea) si el resultado no se puede entregar porque el diálogo
    ya no existe (p. ej. para borrar ficheros temporales).
    """

    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None, on_lost=None):
        super().__init__(parent)
        self._fn = fn
        self._on_lost = on_lost
        self._cancel = threading.Event()
        # «_worker» y no «thread»: QObject ya tiene un método thread() que no se debe tapar.
        self._worker = threading.Thread(target=self._run, name="argus-ct", daemon=True)

    def start(self) -> None:
        self._worker.start()

    def cancel(self) -> None:
        self._cancel.set()

    @staticmethod
    def _emit(signal, *args) -> bool:
        try:
            signal.emit(*args)
            return True
        except RuntimeError:  # el diálogo (y esta tarea) ya se han borrado
            return False

    def _run(self) -> None:
        try:
            res = self._fn(lambda a, b, t: self._emit(self.progress, int(a), int(b), str(t)), self._cancel.is_set)
        except Cancelled:
            self._emit(self.failed, "")
            return
        except (DicomError, ValueError, OSError, MemoryError) as exc:
            self._emit(self.failed, str(exc))
            return
        except Exception as exc:  # pragma: no cover - errores inesperados de los ficheros
            self._emit(self.failed, f"{type(exc).__name__}: {exc}")
            return
        if not self._emit(self.done, res) and self._on_lost is not None:
            self._on_lost(res)


# ---------------------------------------------------------------------------
# Vista previa
# ---------------------------------------------------------------------------

AXES = {"z": ("Axial", 0, 1, 2), "y": ("Coronal", 0, 2, 1), "x": ("Sagital", 1, 2, 0)}


class HuPreview(QWidget):
    """Un corte del TAC (ventana de HU o tejidos) con el recorte encima."""

    def __init__(self, axis: str, parent=None):
        super().__init__(parent)
        self.axis = axis
        self.vol: HuVolume | None = None
        self.index = 0
        self.crop: tuple[np.ndarray, np.ndarray] | None = None
        self.tissue_colors: np.ndarray | None = None  # (n, 3) uint8 por tejido; None: ventana de HU
        self.calibration: Calibration | None = None
        self.hu_window = (-1000.0, 1000.0)  # no «window»: QWidget ya tiene un método window()
        self._img: QImage | None = None
        self.setMinimumSize(QSize(180, 160))

    def sizeHint(self) -> QSize:
        return QSize(260, 220)

    def set_volume(self, vol: HuVolume | None) -> None:
        self.vol = vol
        if vol is not None:
            n = {"z": vol.hu.shape[0], "y": vol.hu.shape[1], "x": vol.hu.shape[2]}[self.axis]
            self.index = n // 2
        self.refresh()

    def count(self) -> int:
        if self.vol is None:
            return 1
        return {"z": self.vol.hu.shape[0], "y": self.vol.hu.shape[1], "x": self.vol.hu.shape[2]}[self.axis]

    def plane(self) -> np.ndarray:
        """Corte (filas de arriba abajo, columnas de izquierda a derecha)."""
        hu = self.vol.hu
        k = max(0, min(self.index, self.count() - 1))
        if self.axis == "z":
            return hu[k]  # filas: y (de delante a atrás), columnas: x
        if self.axis == "y":
            return hu[::-1, k, :]  # filas: z (de la cabeza a los pies), columnas: x
        return hu[::-1, :, k]  # filas: z, columnas: y

    def extent(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Intervalos (mm) de las columnas y de las filas de la imagen."""
        vol = self.vol
        ex = {a: (vol.edges(i)[0], vol.edges(i)[-1]) for i, a in enumerate("xyz")}
        if self.axis == "z":
            return ex["x"], ex["y"]
        if self.axis == "y":
            return ex["x"], ex["z"]
        return ex["y"], ex["z"]

    def refresh(self) -> None:
        if self.vol is None:
            self._img = None
            self.update()
            return
        sl = self.plane().astype(np.float32)
        lo, hi = self.hu_window
        g = np.clip((sl - lo) / (hi - lo), 0.0, 1.0) * 255.0
        rgb = np.repeat(g[..., None], 3, axis=2)
        if self.tissue_colors is not None and self.calibration is not None:
            idx = self.calibration.tissue_index(sl)
            col = self.tissue_colors[np.clip(idx, 0, len(self.tissue_colors) - 1)].astype(np.float32)
            rgb = 0.35 * rgb + 0.65 * col
        arr = np.ascontiguousarray(np.clip(rgb, 0, 255).astype(np.uint8))
        h, w = arr.shape[:2]
        self._img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.update()

    def _target(self) -> QRectF:
        (x0, x1), (y0, y1) = self.extent()
        aspect = (x1 - x0) / max(y1 - y0, 1e-9)
        w, h = self.width() - 4, self.height() - 4
        if w / max(h, 1) > aspect:
            tw, th = h * aspect, h
        else:
            tw, th = w, w / aspect
        return QRectF(2 + (w - tw) / 2, 2 + (h - th) / 2, tw, th)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1e1f22"))
        if self._img is None:
            p.setPen(QColor("#9aa0a6"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sin TAC")
            return
        r = self._target()
        p.drawImage(r, self._img)
        (x0, x1), (y0, y1) = self.extent()
        if self.crop is not None:
            lo, hi = self.crop
            a, b = {"z": (0, 1), "y": (0, 2), "x": (1, 2)}[self.axis]

            def px(v):
                return r.left() + (v - x0) / (x1 - x0) * r.width()

            def py(v):
                f = (v - y0) / (y1 - y0)
                if self.axis == "z":
                    return r.top() + f * r.height()
                return r.bottom() - f * r.height()  # z hacia arriba

            pen = QPen(QColor("#ffb000"))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            xa, xb = px(lo[a]), px(hi[a])
            ya, yb = py(lo[b]), py(hi[b])
            p.drawRect(QRectF(min(xa, xb), min(ya, yb), abs(xb - xa), abs(yb - ya)))
        p.setPen(QColor("#e8eaed"))
        name = AXES[self.axis][0]
        p.drawText(6, 16, f"{name} {self.index + 1}/{self.count()}")


class CurvePlot(QWidget):
    """La curva HU → densidad, con los intervalos de cada tejido coloreados."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calibration: Calibration | None = None
        self.colors: np.ndarray | None = None
        self.setMinimumSize(QSize(260, 150))

    def set_data(self, calibration: Calibration, colors: np.ndarray | None) -> None:
        self.calibration = calibration
        self.colors = colors
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#1e1f22"))
        cal = self.calibration
        if cal is None or len(cal.curve) < 2:
            return
        h0, h1 = -1100.0, max(2000.0, max(h for h, _r in cal.curve) + 100.0)
        r1 = max(2.0, max(r for _h, r in cal.curve) * 1.08)
        left, right, top, bottom = 42, self.width() - 8, 8, self.height() - 22

        def X(h):
            return left + (h - h0) / (h1 - h0) * (right - left)

        def Y(r):
            return bottom - r / r1 * (bottom - top)

        th = [h0] + [t.hu_min for t in cal.tissues[1:]] + [h1]
        for i in range(len(cal.tissues)):
            a, b = max(th[i], h0), min(th[i + 1], h1)
            if b <= a:
                continue
            c = QColor("#555") if self.colors is None or i >= len(self.colors) else QColor(*self.colors[i])
            c.setAlpha(70)
            p.fillRect(QRectF(X(a), top, X(b) - X(a), bottom - top), c)
        p.setPen(QColor("#9aa0a6"))
        p.drawLine(left, bottom, right, bottom)
        p.drawLine(left, top, left, bottom)
        for hv in (-1000, 0, 1000, 2000, 3000):
            if h0 <= hv <= h1:
                p.drawText(QRectF(X(hv) - 25, bottom + 2, 50, 16), Qt.AlignmentFlag.AlignCenter, f"{hv}")
        for rv in (0.0, 1.0, 2.0, 3.0):
            if rv <= r1:
                align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                p.drawText(QRectF(2, Y(rv) - 8, left - 6, 16), align, f"{rv:g}")
        pen = QPen(QColor("#ffb000"))
        pen.setWidth(2)
        p.setPen(pen)
        pts = [(max(h0, min(h1, h)), r) for h, r in cal.curve]
        pts = [(h0, cal.curve[0][1])] + pts + [(h1, cal.curve[-1][1])]
        for (ha, ra), (hb, rb) in zip(pts, pts[1:]):
            p.drawLine(int(X(ha)), int(Y(ra)), int(X(hb)), int(Y(rb)))
        p.setPen(QColor("#e8eaed"))
        p.drawText(left + 6, top + 14, "g/cm³ frente a HU")


# ---------------------------------------------------------------------------
# Diálogo
# ---------------------------------------------------------------------------

@dataclass
class CtImportResult:
    imp: CtImport
    built: BuiltCt
    plan: MaterialPlan
    container: int | None
    ct_path: Path
    geo_path: Path | None  # geometría nueva (si no había ninguna abierta)
    new_input: bool
    description: str


class CtImportDialog(QDialog):
    """``session``: la geometría abierta (o None: se crea una nueva); ``in_doc``: el .in abierto (o None)."""

    def __init__(self, parent=None, session=None, in_doc=None, start_dir: str | Path | None = None,
                 material_colors: dict[int, str] | None = None, containers: list[tuple[int, str]] | None = None,
                 other_isotopes: set | None = None, hadrons: bool | None = True):
        super().__init__(parent)
        self.setWindowTitle("Importar un TAC (DICOM)")
        self.session = session
        self.in_doc = in_doc
        self.material_colors = dict(material_colors or {})
        self.other_isotopes = set(other_isotopes or set())
        self.hadrons = hadrons
        self.series: list[Series] = []
        self.vol: HuVolume | None = None
        self.calibration: Calibration = preset("schneider5")
        self.reuse: list[int | None] = [None] * len(self.calibration.tissues)
        self.result: CtImportResult | None = None
        self._task: _Task | None = None
        self._loading = False
        self._error: str | None = None  # último error (se muestra hasta la próxima acción)
        self._zombies: set = set()  # tareas anuladas que todavía no han terminado (pueden estar escribiendo)
        self._hu_values = None  # (recorte, valores HU distintos que hay en él)
        self._cell_errors: dict[tuple, str] = {}  # celdas de las tablas de calibración que no se entienden
        self._exact_crop: tuple[np.ndarray, np.ndarray] | None = None  # recorte sin redondear (mm)
        self._exact_voxel: np.ndarray | None = None  # vóxel sin redondear (mm)
        self._voxel_note = ""
        start = Path(start_dir) if start_dir else Path.home()
        existing = self._existing_materials()

        lay = QVBoxLayout(self)
        # -- carpeta y series -------------------------------------------------------------------
        top = QHBoxLayout()
        self.folder_edit = QLineEdit(str(start))
        self.folder_edit.setToolTip("Carpeta con los ficheros DICOM del TAC (se buscan también en las subcarpetas)")
        btn = QPushButton("Elegir carpeta…")
        btn.clicked.connect(self._browse_folder)
        scan = QPushButton("Buscar series")
        scan.clicked.connect(self.scan)
        top.addWidget(QLabel("Carpeta:"))
        top.addWidget(self.folder_edit, 1)
        top.addWidget(btn)
        top.addWidget(scan)
        lay.addLayout(top)
        self.series_table = QTableWidget(0, 5)
        self.series_table.setHorizontalHeaderLabels(["Serie", "Cortes", "Imagen", "Vóxel (mm)", "Estado"])
        self.series_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.series_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.series_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.series_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.series_table.verticalHeader().setVisible(False)
        self.series_table.setMaximumHeight(120)
        self.series_table.itemSelectionChanged.connect(self._on_series)
        lay.addWidget(self.series_table)

        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split, 1)
        # -- vistas previas ---------------------------------------------------------------------------
        prev = QWidget()
        pl = QGridLayout(prev)
        pl.setContentsMargins(0, 0, 0, 0)
        self.previews: dict[str, HuPreview] = {}
        self.sliders: dict[str, QSlider] = {}
        for i, axis in enumerate(("z", "y", "x")):
            pv = HuPreview(axis)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.valueChanged.connect(lambda v, a=axis: self._on_slider(a, v))
            self.previews[axis] = pv
            self.sliders[axis] = sl
            pl.addWidget(pv, (i // 2) * 2, i % 2)
            pl.addWidget(sl, (i // 2) * 2 + 1, i % 2)
        self.show_tissues = QCheckBox("Colorear por tejidos")
        self.show_tissues.setChecked(True)
        self.show_tissues.toggled.connect(lambda _c: self._update_previews())
        pl.addWidget(self.show_tissues, 2, 1, Qt.AlignmentFlag.AlignTop)
        split.addWidget(prev)
        # -- opciones --------------------------------------------------------------------------------------
        tabs = QTabWidget()
        split.addWidget(tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)
        tabs.addTab(self._grid_tab(), "Recorte y vóxel")
        tabs.addTab(self._calibration_tab(existing), "Calibración")
        tabs.addTab(self._place_tab(containers), "Colocación y fichero")
        self.tabs = tabs
        # -- estado y botones ------------------------------------------------------------------------------
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_btn = self.buttons.addButton("Importar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._import)
        self.buttons.rejected.connect(self._cancel)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._update_all)
        self._set_tissue_table()
        self._update_all()
        fit_to_screen(self, 1180, 760)

    # -- pestañas ---------------------------------------------------------------------------------------------
    def _spin(self, lo=-1e6, hi=1e6, dec=2, suffix=" mm") -> QDoubleSpinBox:
        s = DecimalSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        s.valueChanged.connect(lambda _v: self._changed())
        return s

    def _grid_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        grid = QGridLayout()
        self.crop_lo = {}
        self.crop_hi = {}
        for i, a in enumerate("xyz"):
            self.crop_lo[a] = self._spin()
            self.crop_hi[a] = self._spin()
            grid.addWidget(QLabel(f"{a}:"), i, 0)
            grid.addWidget(self.crop_lo[a], i, 1)
            grid.addWidget(QLabel("a"), i, 2)
            grid.addWidget(self.crop_hi[a], i, 3)
        row = QHBoxLayout()
        b1 = QPushButton("Ajustar al cuerpo")
        b1.setToolTip("Lo que tiene más de −400 HU (paciente, mesa, maniquí) con 5 mm de margen")
        b1.clicked.connect(self.fit_body)
        b2 = QPushButton("Todo el TAC")
        b2.clicked.connect(self.fit_all)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        grid.addLayout(row, 3, 0, 1, 4)
        box = QGroupBox("Recorte (coordenadas del TAC, mm)")
        box.setLayout(grid)
        form.addRow(box)
        self.voxel_choice = QComboBox()
        self.voxel_choice.addItems(VOXEL_CHOICES)
        self.voxel_choice.addItem("Otro")
        self.voxel_choice.currentIndexChanged.connect(self._on_voxel_choice)
        form.addRow("Tamaño de vóxel:", self.voxel_choice)
        vrow = QHBoxLayout()
        self.voxel = {a: self._spin(0.01, 1000.0, 3) for a in "xyz"}
        for a in "xyz":
            vrow.addWidget(QLabel(f"d{a}"))
            vrow.addWidget(self.voxel[a])
        form.addRow("", vrow)
        self.estimate_label = QLabel("")
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setTextFormat(Qt.TextFormat.RichText)
        form.addRow(self.estimate_label)
        note = QLabel("Cada vóxel toma la media de los HU del TAC que cubre (pesada por volumen); fuera del TAC, "
                      "aire. penhmain admite como mucho 512 cortes (NZ) y reserva unos 40 bytes por vóxel.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        form.addRow(note)
        return w

    def _calibration_tab(self, existing: list[str]) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.preset_combo = QComboBox()
        for k in PRESETS:
            self.preset_combo.addItem(PRESET_LABELS[k], k)
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        load = QPushButton("Cargar curva HU → densidad…")
        load.setToolTip("CSV o texto con dos columnas (HU y densidad en g/cm³), p. ej. la curva del TAC de "
                        "planificación")
        load.clicked.connect(self._load_curve)
        row.addWidget(self.preset_combo, 1)
        row.addWidget(load)
        lay.addLayout(row)
        self.tissue_table = QTableWidget(0, 6)
        self.tissue_table.setHorizontalHeaderLabels(["Tejido", "Desde (HU)", "Material del .in", "Fichero .mat",
                                                     "Densidad (g/cm³)", "Composición (masa)"])
        self.tissue_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.tissue_table.verticalHeader().setVisible(False)
        self.tissue_table.itemChanged.connect(self._on_tissue_edit)
        self.tissue_table.setMinimumHeight(120)
        lay.addWidget(self.tissue_table, 3)
        self.curve_table = QTableWidget(0, 2)
        self.curve_table.setHorizontalHeaderLabels(["HU", "g/cm³"])
        self.curve_table.verticalHeader().setVisible(False)
        self.curve_table.itemChanged.connect(self._on_curve_edit)
        self.curve_table.setMaximumWidth(210)
        self.curve_plot = CurvePlot()
        box = QGroupBox("Curva HU → densidad (tramos rectos entre los puntos; fuera, el valor del extremo)")
        bl = QHBoxLayout(box)
        bl.addWidget(self.curve_table)
        bl.addWidget(self.curve_plot, 1)
        lay.addWidget(box, 4)
        self.isotope_label = QLabel("")
        self.isotope_label.setWordWrap(True)
        self.isotope_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.isotope_label)
        lay.addWidget(self._split_box())
        self._existing = existing
        return w

    def _split_box(self) -> QGroupBox:
        """Partir los tejidos en materiales por densidad (penhmain 2025, CTTRACK: ADR-010 y ADR-011)."""
        from .settings import cttrack_fixed

        box = QGroupBox("Densidad de los materiales (penhmain, CTTRACK)")
        bl = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Partir los tejidos por densidad:"))
        self.split_combo = QComboBox()
        self.split_combo.addItem("Automático (recomendado)", "auto")
        self.split_combo.addItem("Todos los materiales que quepan", "max")
        self.split_combo.addItem("No partir", "none")
        self.split_combo.setToolTip("Cada trozo es un material con la composición del tejido y, como densidad del "
                                    ".mat, la media de sus vóxeles. «Automático» deja de partir cuando un material "
                                    "más apenas reduce el error de alcance estimado.")
        self.split_combo.currentIndexChanged.connect(lambda _i: self._changed())
        row.addWidget(self.split_combo, 1)
        bl.addLayout(row)
        self.cttrack_check = QCheckBox("Mi penhmain tiene corregido CTTRACK (DEE = EORIG − E): no hace falta partir")
        self.cttrack_check.setToolTip("Márquelo solo si ha cambiado en penhmain.f (CTTRACK, líneas 11082–11084) "
                                      "«DEE=MIN(SSOFT*DSEF,EORIG)» y «E=MAX(EORIG-DEE,2.0D-16)» por «DEE=EORIG-E» y "
                                      "lo ha vuelto a compilar. Se recuerda para las próximas veces.")
        self.cttrack_check.setChecked(cttrack_fixed())
        self.cttrack_check.toggled.connect(self._on_cttrack)
        bl.addWidget(self.cttrack_check)
        self.split_note = QLabel("")
        self.split_note.setWordWrap(True)
        bl.addWidget(self.split_note)
        self._update_split_note()
        return box

    def _on_cttrack(self, on: bool) -> None:
        from .settings import set_cttrack_fixed

        set_cttrack_fixed(bool(on))
        self._update_split_note()
        self._changed()

    def _update_split_note(self) -> None:
        fixed = self.cttrack_check.isChecked()
        self.split_combo.setEnabled(not fixed)
        if fixed:
            self.split_note.setText("Con CTTRACK corregido, penhmain usa la densidad de cada vóxel: cada tejido es "
                                    "un solo material.")
            return
        self.split_note.setText(
            "penhmain (2025) resta la pérdida continua de energía de las partículas cargadas dentro del CT con la "
            "densidad del .mat de cada material, no con la de cada vóxel. Para que se parezcan, ARGUS parte cada "
            "tejido en varios materiales por densidad (misma composición; la densidad de cada .mat es la media de "
            "sus vóxeles) con los materiales que quedan libres en el .in (penhmain admite 10). Al importar calcula "
            "el error de alcance que queda. Con protones, cada material más alarga la preparación de penhmain "
            "(unos 33 s por elemento).")

    def split_mode(self) -> str:
        """Cómo partir los tejidos al importar: «auto», «max» o «none» (también con CTTRACK corregido)."""
        if self.cttrack_check.isChecked():
            return "none"
        return str(self.split_combo.currentData() or "auto")

    def _place_tab(self, containers) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.place_combo = QComboBox()
        for k, text in PLACEMENTS.items():
            self.place_combo.addItem(text, k)
        self.place_combo.currentIndexChanged.connect(lambda _i: self._changed())
        form.addRow("Colocación:", self.place_combo)
        prow = QHBoxLayout()
        self.point = {a: self._spin() for a in "xyz"}
        for a in "xyz":
            prow.addWidget(QLabel(a))
            prow.addWidget(self.point[a])
        form.addRow("Punto del TAC (mm):", prow)
        orow = QHBoxLayout()
        self.offset = {a: self._spin(suffix=" cm") for a in "xyz"}
        for a in "xyz":
            orow.addWidget(QLabel(a))
            orow.addWidget(self.offset[a])
        form.addRow("Y después desplazar:", orow)
        self.container_combo = QComboBox()
        self._container_auto = True  # se elige solo (el que contiene el centro del CT) hasta que el usuario elija
        if self.session is not None:
            for kb, text in containers or []:
                self.container_combo.addItem(text, kb)
            form.addRow("Dentro de:", self.container_combo)
            self.container_combo.activated.connect(self._on_container_chosen)
            self.container_combo.currentIndexChanged.connect(lambda _i: self._changed())
        self.name_edit = QLineEdit(free_ct_name(self.target_folder() if self.session is not None else None))
        self.name_edit.setMaxLength(12)
        self.name_edit.setToolTip("Nombre del fichero .ct (12 caracteres como mucho: CTFNAME de PENGEOM). Va en la "
                                  "carpeta donde PENGEOM lo buscará: la del .in si hay uno abierto (la carpeta de "
                                  "trabajo de penhmain) y, si no, la de la geometría.")
        self.name_edit.textChanged.connect(lambda _t: self._changed())
        self._name_auto = True  # se propone un nombre libre en la carpeta hasta que el usuario escriba otro
        self.name_edit.textEdited.connect(self._on_name_edited)
        form.addRow("Fichero .ct:", self.name_edit)
        self.label_edit = QLineEdit("TAC")
        self.label_edit.setMaxLength(4)
        form.addRow("Etiqueta del CTBOX:", self.label_edit)
        self.desc_edit = QLineEdit("")
        self.desc_edit.setPlaceholderText(DEFAULT_DESCRIPTION)
        self.desc_edit.setToolTip("Título del .ct y descripción del CTBOX en el .geo. No ponga datos del paciente.")
        form.addRow("Descripción:", self.desc_edit)
        if self.session is None:
            grow = QHBoxLayout()
            self.geo_edit = QLineEdit("")
            self.geo_edit.setToolTip("No hay ninguna geometría abierta: se crea una nueva con el TAC (ruta completa)")
            self.geo_edit.textChanged.connect(lambda _t: self._changed())
            self.geo_edit.textChanged.connect(lambda _t: self._propose_name())
            b = QPushButton("Examinar…")
            b.clicked.connect(self._browse_geo)
            grow.addWidget(self.geo_edit, 1)
            grow.addWidget(b)
            form.addRow("Geometría nueva:", grow)
            self.new_input_check = QCheckBox("Crear también un .in nuevo que la use")
            self.new_input_check.setChecked(True)
            form.addRow("", self.new_input_check)
        else:
            self.geo_edit = None
            self.new_input_check = None
            folder = self.target_folder()
            why = ("junto a la geometría" if folder == self.session.path.parent else
                   "la carpeta del .in, donde penhmain se ejecuta y PENGEOM lo buscará")
            where = QLabel(f"El .ct y su ficha se guardan en {folder} ({why}).")
            where.setWordWrap(True)
            form.addRow(where)
        return w

    def _on_container_chosen(self, _i) -> None:
        self._container_auto = False

    def _on_name_edited(self, _text: str) -> None:
        self._name_auto = False

    def _propose_name(self) -> None:
        """Nombre del .ct libre en la carpeta de la geometría nueva (si el usuario no ha escrito otro)."""
        if self._name_auto:
            self.name_edit.setText(free_ct_name(self.target_folder()))

    def _auto_container(self) -> None:
        """Contenedor en el que cae el centro de la caja CT (el primero de la lista que lo contiene)."""
        if not self._container_auto or self.session is None or self.vol is None or not self.session.ok:
            return
        g = self.grid()
        if g is None or self.container_combo.count() == 0:
            return
        from ..ct.grid import placement

        try:
            rot, shift = placement(self.vol, g, self.place_combo.currentData(),
                                   np.array([self.point[a].value() for a in "xyz"]))
        except ValueError:
            return
        shift = shift + np.array([self.offset[a].value() for a in "xyz"])
        center = rot @ (g.size_mm / 20.0) + shift
        kb, _ = self.session.locator.locate(center[None, :])
        geo = self.session.geo
        allowed = [self.container_combo.itemData(i) for i in range(self.container_combo.count())]
        cur = int(kb[0])
        seen = set()
        while cur and cur not in seen and cur <= geo.nbodys:
            seen.add(cur)
            if cur in allowed:
                i = allowed.index(cur)
                if i != self.container_combo.currentIndex():
                    self.container_combo.blockSignals(True)
                    self.container_combo.setCurrentIndex(i)
                    self.container_combo.blockSignals(False)
                return
            from ..pengeom.edit import logical_parent

            cur = logical_parent(geo, cur)

    # -- materiales del .in ---------------------------------------------------------------------------------
    def _existing_materials(self) -> list[str]:
        if self.in_doc is None:
            return []
        return [ln.text.strip() for ln in self.in_doc.find("MFNAME")]

    def plan_existing(self) -> list[str]:
        """Materiales que ya hay (los del .in o, sin .in, los que usa la geometría): los nuevos van detrás."""
        existing = self._existing_materials()
        if self.in_doc is None and self.session is not None and self.session.ok:
            # Sin .in: los materiales nuevos van después de los que usa la geometría.
            existing = [f"material{i}.mat" for i in range(1, self.session.geo.nmat + 1)]
        return existing

    def plan(self) -> MaterialPlan:
        """Números de material de los tejidos; los que no tienen vóxeles en el recorte no llevan material (como al
        importar: así se cuentan bien los que caben en el .in)."""
        return plan_materials(self.calibration, self.plan_existing(), self.reuse, present=self.present_tissues())

    def present_tissues(self) -> list[bool] | None:
        """¿Tiene cada tejido algún vóxel del TAC en el recorte? (con los HU sin promediar: aproximado; al
        importar se vuelve a comprobar con los vóxeles de verdad). None si no hay serie."""
        if self.vol is None:
            return None
        lo, hi = self.crop()
        key = (id(self.vol), tuple(np.round(lo, 6)), tuple(np.round(hi, 6)))
        if self._hu_values is None or self._hu_values[0] != key:
            v = self.vol
            sel = [np.flatnonzero((c >= a - 1e-9) & (c <= b + 1e-9)) for c, a, b in ((v.x, lo[0], hi[0]),
                                                                                   (v.y, lo[1], hi[1]),
                                                                                   (v.z, lo[2], hi[2]))]
            if any(len(i) == 0 for i in sel):
                vals = distinct_hu(v.hu)
            else:
                vals = distinct_hu(v.hu[sel[2][0]:sel[2][-1] + 1, sel[1][0]:sel[1][-1] + 1, sel[0][0]:sel[0][-1] + 1])
            self._hu_values = (key, vals)
        idx = self.calibration.tissue_index(self._hu_values[1])
        counts = np.bincount(idx, minlength=len(self.calibration.tissues))
        return [bool(c) for c in counts[:len(self.calibration.tissues)]]

    # -- carpeta y series -----------------------------------------------------------------------------------
    def _browse_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Carpeta con el TAC (DICOM)", self.folder_edit.text())
        if d:
            self.folder_edit.setText(d)
            self.scan()

    def _browse_geo(self) -> None:
        start = self.geo_edit.text() or str(Path(self.folder_edit.text()).parent / "tac.geo")
        path, _ = QFileDialog.getSaveFileName(self, "Geometría nueva", start,
                                              "Geometrías PENGEOM (*.geo);;Todos los ficheros (*)")
        if path:
            if not path.lower().endswith(".geo"):
                path += ".geo"
            self.geo_edit.setText(path)

    def scan(self) -> None:
        folder = self.folder_edit.text().strip()
        self._run(lambda prog, cancel: scan_folder(folder, progress=prog, cancel=cancel), self._on_scanned,
                  "Buscando series DICOM…")

    def _on_scanned(self, series: list[Series]) -> None:
        self.series = series
        t = self.series_table
        t.blockSignals(True)
        # La serie de antes deja de valer; sin selección, marcar la primera fila siempre avisa (_on_series).
        t.clearSelection()
        self._set_volume(None)
        t.setRowCount(len(series))
        for i, s in enumerate(series):
            desc = s.description or s.study or "Serie sin descripción"
            dz = s.slice_spacing
            empty = s.nslices == 0  # p. ej. solo localizadores o capturas: no hay tamaño que mostrar
            vox = f"{s.pixel_spacing[1]:.2f} × {s.pixel_spacing[0]:.2f} × {dz:.2f}".replace(".", ",")
            vals = [desc, str(s.nslices), "—" if empty else f"{s.cols}×{s.rows}", "—" if empty else vox,
                    "✖ " + s.problems[0] if s.problems else ("⚠ " + s.notes[0] if s.notes else "✓")]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 4:
                    it.setToolTip("\n".join(s.problems + s.notes) or "Se puede importar")
                t.setItem(i, j, it)
        t.blockSignals(False)
        if not series:
            self._error = "No se ha encontrado ninguna serie DICOM en la carpeta."
            return
        ok = [i for i, s in enumerate(series) if s.ok]
        if ok:
            t.selectRow(ok[0])
        else:
            self._error = "Ninguna serie se puede importar: " + series[0].problems[0]

    def _on_series(self) -> None:
        rows = self.series_table.selectionModel().selectedRows()
        # Otra serie: el volumen anterior deja de valer (si no, se importaría la serie que ya no está marcada).
        self._set_volume(None)
        if not rows:
            self._update_all()
            return
        s = self.series[rows[0].row()]
        if not s.ok:
            self._error = "Esta serie no se puede importar: " + " ".join(s.problems)
            self._update_all()
            return
        self._run(lambda prog, cancel: read_series(s, prog, cancel), self._on_loaded, "Leyendo los cortes…")

    def _set_volume(self, vol: HuVolume | None) -> None:
        self.vol = vol
        self._exact_crop = None
        self._exact_voxel = None
        for a, pv in self.previews.items():
            pv.set_volume(vol)
            sl = self.sliders[a]
            sl.blockSignals(True)
            sl.setRange(0, pv.count() - 1)
            sl.setValue(pv.index)
            sl.blockSignals(False)

    def _on_loaded(self, vol: HuVolume) -> None:
        self._set_volume(vol)
        # Punto por defecto: el centro del TAC en coordenadas del paciente (las que usa «Un punto del TAC»).
        c = 0.5 * (np.array(vol.bounds()[0]) + np.array(vol.bounds()[1]))
        c = np.asarray(vol.rotation, dtype=float) @ c
        self._loading = True
        for i, a in enumerate("xyz"):
            self.point[a].setValue(float(c[i]))
        self._loading = False
        self.fit_body()

    # -- recorte y vóxel ------------------------------------------------------------------------------------------
    def _set_crop(self, lo, hi) -> None:
        self._loading = True
        for i, a in enumerate("xyz"):
            self.crop_lo[a].setValue(float(lo[i]))
            self.crop_hi[a].setValue(float(hi[i]))
        self._loading = False
        # Los cuadros muestran el recorte redondeado; mientras no se toquen se usa el exacto (así la rejilla
        # coincide con la del TAC y los HU no se mezclan con los vecinos).
        self._exact_crop = (np.array(lo, dtype=float), np.array(hi, dtype=float))
        self._on_voxel_choice()

    def fit_body(self) -> None:
        if self.vol is not None:
            self._set_crop(*auto_crop(self.vol))

    def fit_all(self) -> None:
        if self.vol is not None:
            self._set_crop(*self.vol.bounds())

    def crop(self) -> tuple[np.ndarray, np.ndarray]:
        lo = np.array([_exact(self.crop_lo[a], None if self._exact_crop is None else self._exact_crop[0][i])
                       for i, a in enumerate("xyz")])
        hi = np.array([_exact(self.crop_hi[a], None if self._exact_crop is None else self._exact_crop[1][i])
                       for i, a in enumerate("xyz")])
        return lo, hi

    def voxel_size(self) -> np.ndarray:
        return np.array([_exact(self.voxel[a], None if self._exact_voxel is None else self._exact_voxel[i])
                         for i, a in enumerate("xyz")])

    def _on_voxel_choice(self, *_a) -> None:
        if self.vol is None:
            return
        text = self.voxel_choice.currentText()
        if text == "Otro":
            self._changed()
            return
        lo, hi = self.crop()
        self._voxel_note = ""
        if text == "Como el TAC":
            v = np.array(self.vol.spacing())
            if np.all(hi > lo):
                g = make_grid(lo, hi, v)
                if g.nvoxels > MAX_VOXELS_WARN or g.shape[2] > NDD:
                    v = suggest_voxel(self.vol, lo, hi)
                    why = f"más de {NDD} cortes" if g.shape[2] > NDD else f"{g.nvoxels:,} vóxeles".replace(",", ".")
                    self._voxel_note = (f"Con el vóxel del TAC saldrían {why}: se usa "
                                        + " × ".join(_es(x, 2) for x in v) + " mm.")
        else:
            s = _mm(text)
            nat = np.array(self.vol.spacing())
            v = np.array([s, s, max(s, nat[2]) if s < nat[2] else s])
        self._loading = True
        for i, a in enumerate("xyz"):
            self.voxel[a].setValue(float(v[i]))
        self._loading = False
        self._exact_voxel = np.array(v, dtype=float)
        self._changed()

    def grid(self):
        lo, hi = self.crop()
        if np.any(hi <= lo):
            return None
        try:
            return make_grid(lo, hi, self.voxel_size())
        except ValueError:
            return None

    # -- calibración ------------------------------------------------------------------------------------------------
    def _on_preset(self, *_a) -> None:
        key = self.preset_combo.currentData()
        self.calibration = preset(key)
        self.reuse = [None] * len(self.calibration.tissues)
        self._set_tissue_table()
        self._changed()

    def _load_curve(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Curva HU → densidad", self.folder_edit.text(),
                                              "Texto o CSV (*.csv *.txt *.dat);;Todos los ficheros (*)")
        if not path:
            return
        try:
            curve = read_curve_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "ARGUS", f"No se puede leer la curva:\n{exc}")
            return
        self.set_curve(curve, Path(path).name)

    def set_curve(self, curve: list[tuple[float, float]], source: str = "") -> None:
        self.calibration.curve = list(curve)
        if source:
            self.calibration.name += f" · curva de {source}"
            self.calibration.key = "personal"
        self._set_tissue_table()
        self._changed()

    def _tissue_colors(self) -> np.ndarray:
        nums = self.plan().numbers
        out = np.zeros((len(nums), 3), dtype=np.uint8)
        for i, n in enumerate(nums):
            out[i] = hex_to_rgb(self.material_colors.get(n) or material_color(n))
        return out

    def _auto_label(self, row: int, n: int, existing: list[str]) -> str:
        """Texto de la primera opción del combo «Material del .in» del tejido ``row`` (``n``: su número en el plan)."""
        if row < len(self.reuse) and self.reuse[row] is not None:
            return "automático"
        if n <= 0:  # el plan no le da material: no parece tener vóxeles (al importar se mira con los de verdad)
            return "ninguno (sin vóxeles en el recorte)"
        if n > len(existing):
            return f"nuevo (n.º {n})"
        return f"{n}: {existing[n - 1]} (mismo fichero)"

    def _refresh_reuse_labels(self, plan: MaterialPlan) -> None:
        """La primera opción de cada combo «Material del .in» con el plan de ahora (cambia al leer la serie, al
        recortar o al elegir el material de otro tejido)."""
        existing = self._existing_materials()
        for i, n in enumerate(plan.numbers):
            combo = self.tissue_table.cellWidget(i, 2)
            if isinstance(combo, QComboBox) and combo.count():
                text = self._auto_label(i, n, existing)
                if combo.itemText(0) != text:
                    combo.setItemText(0, text)

    def _set_tissue_table(self) -> None:
        self._cell_errors.clear()  # las tablas se escriben de nuevo con los valores que se usan
        t = self.tissue_table
        cal = self.calibration
        plan = self.plan()
        existing = self._existing_materials()
        t.blockSignals(True)
        try:  # si algo fallara, las señales no se quedan bloqueadas (las ediciones de después se perderían)
            t.setRowCount(len(cal.tissues))
            for i, ts in enumerate(cal.tissues):
                name = QTableWidgetItem(ts.name)
                hu = QTableWidgetItem("−∞" if i == 0 else f"{ts.hu_min:g}")
                if i == 0:
                    hu.setFlags(hu.flags() & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(i, 0, name)
                t.setItem(i, 1, hu)
                combo = QComboBox()
                combo.addItem(self._auto_label(i, plan.numbers[i], existing), None)
                for k, f in enumerate(existing, start=1):
                    combo.addItem(f"{k}: {f}", k)
                if self.reuse[i] is not None:
                    combo.setCurrentIndex(max(0, combo.findData(self.reuse[i])))
                combo.currentIndexChanged.connect(lambda _j, row=i, cb=combo: self._on_reuse(row, cb.currentData()))
                t.setCellWidget(i, 2, combo)
                t.setItem(i, 3, QTableWidgetItem(ts.mat_file))
                t.setItem(i, 4, QTableWidgetItem(f"{ts.density:.6g}"))
                listed = f"lista de PENELOPE n.º {ts.penelope_id}" if ts.penelope_id else ""
                comp = QTableWidgetItem(ts.formula() or listed)
                comp.setFlags(comp.flags() & ~Qt.ItemFlag.ItemIsEditable)
                comp.setToolTip(ts.source)
                t.setItem(i, 5, comp)
            t.resizeColumnsToContents()
        finally:
            t.blockSignals(False)
        c = self.curve_table
        c.blockSignals(True)
        try:
            c.setRowCount(len(cal.curve) + 1)
            for i in range(len(cal.curve) + 1):
                for j in range(2):
                    v = "" if i >= len(cal.curve) else f"{cal.curve[i][j]:g}"
                    c.setItem(i, j, QTableWidgetItem(v))
        finally:
            c.blockSignals(False)

    def _on_reuse(self, row: int, value) -> None:
        self.reuse[row] = value
        self._changed()

    def _on_tissue_edit(self, item: QTableWidgetItem) -> None:
        i, j = item.row(), item.column()
        ts: Tissue = self.calibration.tissues[i]
        text = item.text().strip().replace(",", ".").replace("−", "-")
        key = ("tejido", i, j)
        self._cell_errors.pop(key, None)
        try:
            if j == 0:
                ts.name = item.text().strip()
            elif j == 1 and i > 0:
                ts.hu_min = _finite(text)
            elif j == 3:
                ts.mat_file = item.text().strip()
            elif j == 4:
                ts.density = _finite(text)
        except ValueError:
            # No se usa (sigue el valor anterior): se dice, en vez de importar con otro valor sin avisar.
            what = {1: "el HU mínimo", 4: "la densidad"}.get(j, "el valor")
            self._cell_errors[key] = f"Tejido «{ts.name}»: {what} «{item.text().strip()}» no es un número."
        self._changed()

    def _on_curve_edit(self, _item) -> None:
        pts = []
        for key in [k for k in self._cell_errors if k[0] == "curva"]:
            del self._cell_errors[key]
        for i in range(self.curve_table.rowCount()):
            a, b = self.curve_table.item(i, 0), self.curve_table.item(i, 1)
            ta = a.text().strip() if a is not None else ""
            tb = b.text().strip() if b is not None else ""
            if not ta and not tb:
                continue
            if not ta or not tb:
                self._cell_errors[("curva", i, 0)] = (f"Curva HU → densidad, fila {i + 1}: falta "
                                                      + ("el HU." if not ta else "la densidad."))
                continue
            try:
                pts.append((_finite(ta.replace(",", ".").replace("−", "-")), _finite(tb.replace(",", "."))))
            except ValueError:
                self._cell_errors[("curva", i, 0)] = (f"Curva HU → densidad, fila {i + 1}: «{ta}» y «{tb}» tienen "
                                                      "que ser números.")
                continue
        pts.sort()
        self.calibration.curve = pts
        if self.curve_table.item(self.curve_table.rowCount() - 1, 0) is not None and \
                self.curve_table.item(self.curve_table.rowCount() - 1, 0).text().strip():
            self.curve_table.blockSignals(True)
            self.curve_table.insertRow(self.curve_table.rowCount())
            self.curve_table.blockSignals(False)
        self._changed()

    # -- actualización -------------------------------------------------------------------------------------------------
    def _changed(self) -> None:
        if not self._loading:
            self._timer.start()

    def _on_slider(self, axis: str, value: int) -> None:
        pv = self.previews[axis]
        pv.index = value
        pv.refresh()

    def _update_previews(self) -> None:
        self.curve_plot.set_data(self.calibration, self._tissue_colors())
        colors = self._tissue_colors() if self.show_tissues.isChecked() else None
        g = self.grid()
        for pv in self.previews.values():
            pv.tissue_colors = colors
            pv.calibration = self.calibration
            pv.crop = (g.lo, g.hi) if g is not None else None
            pv.refresh()

    def _update_all(self) -> None:
        self._auto_container()
        self._update_previews()
        g = self.grid()
        warn = []
        if g is None:
            self.estimate_label.setText("—")
        else:
            e = estimate(g)
            nx, ny, nz = g.shape
            size = " × ".join(_es(v / 10.0, 1) for v in g.size_mm)
            txt = (f"<b>{nx} × {ny} × {nz}</b> = {e['voxels']:,} vóxeles".replace(",", ".")
                   + f" · caja de {size} cm · fichero ≈ {human_size(e['file'])} · memoria de penhmain ≈ "
                   f"{human_size(e['memory'])}")
            if not e["nz_ok"]:
                txt += f"<br><span style='color:#d33'>Más de {NDD} cortes: penhmain no lo admite.</span>"
            if e["voxels"] > MAX_VOXELS_WARN:
                warn.append("Muchos vóxeles: el fichero será grande y penhmain lento al leerlo.")
            if self._voxel_note:
                txt += f"<br><span style='color:#b36b00'>{html.escape(self._voxel_note)}</span>"
            self.estimate_label.setText(txt)
        plan = self.plan()
        self._refresh_reuse_labels(plan)
        n, text = isotope_summary(self.calibration, None, self.other_isotopes if self.hadrons is not False else None)
        color = "#d33" if n > MNIS and self.hadrons is not False else "#2a7"
        extra = "" if self.hadrons is not False else " (no importan: la fuente no es de protones ni alfas)"
        self.isotope_label.setText(f"<span style='color:{color}'>{text}</span>{extra}<br>"
                                   + self._new_materials_text(plan))
        warn += self.calibration.warnings()
        if self.hadrons is not False:
            warn += hadron_warnings(self.calibration, self.split_mode())
        probs = self.problems()
        if self._task is not None:
            pass  # la tarea en curso muestra su propio mensaje
        elif self._error:
            self._set_status(self._error, error=True)
        elif probs:
            self._set_status(" ".join(probs), error=True)
        elif warn:
            self._set_status(" ".join(warn), error=False)
        elif self.vol is not None:
            self._set_status("Listo para importar.", error=False)
        if self._zombies and self._task is None:
            self._set_status("Cancelando…", error=False)
        self.import_btn.setEnabled(self.vol is not None and not probs and self._task is None and not self._zombies)

    def _new_materials_text(self, plan: MaterialPlan) -> str:
        if not plan.new:
            return "Los tejidos usan materiales que ya tiene el .in."
        items = ", ".join(f"{n}: {f}" for n, f, _d in plan.new)
        if self.in_doc is not None:
            where = "en el .in"
        elif self.new_input_check is not None and self.new_input_check.isChecked():
            where = "en el .in nuevo"
        else:
            where = "(cree el .in después con Archivo → Nuevo .in)"
        text = (f"Materiales nuevos {where}: {items}. Después, «Generar .mat y ENDF…» (panel Materiales) los genera "
                "con su penhmat (o, a mano, materiales.txt dice cómo).")
        if self.split_mode() != "none":
            text += (" Al importar, los tejidos cuyos vóxeles tienen densidades muy distintas se parten en varios "
                     "materiales (ct_pulmon_1.mat…); el número final se decide con el TAC.")
        return text

    def problems(self) -> list[str]:
        out = []
        if self.vol is None:
            return ["Elija una serie de TAC."]
        g = self.grid()
        if g is None:
            out.append("El recorte no es válido.")
        elif g.shape[2] > NDD:
            out.append(f"El CT tendría {g.shape[2]} cortes y penhmain admite {NDD}: aumente dz o recorte.")
        out += list(dict.fromkeys(self._cell_errors.values()))
        out += self.calibration.problems()
        if self.in_doc is not None and self.session is not None and self.session.ok:
            declared = len(self._existing_materials())
            if self.session.geo.nmat > declared:
                out.append(f"La geometría usa materiales hasta el {self.session.geo.nmat} y el .in solo declara "
                           f"{declared} (líneas MFNAME): declare antes los que faltan (panel «Materiales»); si no, "
                           "los tejidos del TAC tomarían esos números.")
        plan = self.plan()
        out += plan.problems
        for _n, fname, _d in plan.new:
            p = mat_filename_problem(fname)
            if p:
                out.append(p)
        p = ct_name_problem(self.name_edit.text().strip())
        if p:
            out.append(p)
        from ..pengeom.edit import EditError
        from ..pengeom.surfaces import label_text

        try:
            label = label_text(self.label_edit.text().strip() or "TAC")
            if self.session is not None and self.session.ok and self.session.geo.find_body(label) is not None:
                out.append(f"Ya hay un cuerpo con la etiqueta «{label.strip()}»: elija otra para el CTBOX.")
        except EditError as exc:
            out.append(f"Etiqueta del CTBOX: {exc}")
        if self.session is None and self.geo_edit is not None:
            text = self.geo_edit.text().strip()
            if not text:
                out.append("Elija dónde guardar la geometría nueva.")
            elif not Path(text).expanduser().is_absolute():
                out.append("Escriba la ruta completa de la geometría nueva (o use «Examinar…»).")
        return out

    def _set_status(self, text: str, error: bool = False) -> None:
        color = "#d33" if error else "#888"
        self.status.setText(f"<span style='color:{color}'>{html.escape(text)}</span>")

    # -- tareas --------------------------------------------------------------------------------------------------------
    def _run(self, fn, on_done, text: str, on_stale=None) -> None:
        """``on_stale(res)``: qué hacer con el resultado de una tarea que se anuló mientras terminaba."""
        if self._task is not None:
            self._task.cancel()
            self._zombies.add(self._task)
        self._error = None
        task = _Task(fn, self, on_lost=on_stale)
        self._task = task
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self._set_status(text)
        self.import_btn.setEnabled(False)

        def prog(a, b, _t):
            if task is self._task:
                self.progress.setRange(0, max(b, 1))
                self.progress.setValue(a)

        def settled() -> None:
            """Una tarea anulada ha terminado de verdad (ya no escribe nada): se puede volver a importar."""
            self._zombies.discard(task)
            if self._task is None and not self._zombies:
                self._update_all()

        def done(res):
            if task is not self._task:
                if on_stale is not None:
                    on_stale(res)
                settled()
                return
            self._task = None
            self.progress.setVisible(False)
            on_done(res)
            self._update_all()

        def failed(msg):
            if task is not self._task:
                settled()
                return
            self._task = None
            self.progress.setVisible(False)
            self._error = msg or None
            self._update_all()

        task.progress.connect(prog)
        task.done.connect(done)
        task.failed.connect(failed)
        task.start()

    def busy(self) -> bool:
        return self._task is not None

    def reject(self) -> None:
        """Cerrar (Esc, la X o Cancelar) anula también la tarea en curso: no se escribe nada."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        super().reject()

    def _cancel(self) -> None:
        if self._task is not None:  # el botón Cancelar con una tarea en marcha: solo la anula
            self._task.cancel()
            self._zombies.add(self._task)
            self._task = None
            self.progress.setVisible(False)
            self._set_status("Cancelado.")
            self._update_all()
            return
        self.reject()

    # -- importar -----------------------------------------------------------------------------------------------
    def target_folder(self) -> Path | None:
        """Carpeta del .ct: donde PENGEOM lo buscará (con un .in abierto, su carpeta: la de trabajo de penhmain)."""
        if self.session is not None:
            return Path(self.session.base_dir)
        if self.geo_edit is not None and self.geo_edit.text().strip():
            p = Path(self.geo_edit.text().strip()).expanduser()
            return p.parent if p.is_absolute() else None
        return None

    def build_import(self) -> CtImport:
        # Al importar, los números salen de los vóxeles de verdad (``existing``). Los de aquí son una vista previa,
        # sin los 0 de los tejidos que no parecen estar con los HU sin promediar: un 0 no es «vacío».
        numbers = plan_materials(self.calibration, self.plan_existing(), self.reuse).numbers
        point = np.array([self.point[a].value() for a in "xyz"])
        offset = np.array([self.offset[a].value() for a in "xyz"])
        return CtImport(self.vol, self.grid(), self.calibration.copy(), numbers,
                        self.place_combo.currentData(), point, self.name_edit.text().strip(),
                        self.label_edit.text().strip() or "TAC",
                        (self.desc_edit.text().strip() or DEFAULT_DESCRIPTION)[:72], offset,
                        split=self.split_mode(), cttrack_fixed=self.cttrack_check.isChecked(),
                        existing=self.plan_existing(),
                        reuse=list(self.reuse) if self.reuse is not None else None)

    def check_fit(self, imp: CtImport, container):
        """¿Cabe la caja del CT donde se ha pedido? (antes de calcular los vóxeles)."""
        from ..ct.importer import check_ct_box
        from ..pengeom.ct import CtFrame

        rot, shift = imp.frame_placement()
        frame = CtFrame(rot, shift, tuple(float(v) for v in imp.grid.size_mm / 10.0), imp.grid.shape)
        return check_ct_box(self.session.geo, self.session.locator, frame, container)

    def _import(self) -> None:
        probs = self.problems()
        if probs:
            self._set_status(" ".join(probs), error=True)
            return
        folder = self.target_folder()
        if folder is None or not folder.is_dir():
            self._set_status(f"No existe la carpeta {folder}.", error=True)
            return
        imp = self.build_import()
        ct_path = folder / imp.ct_name
        if ct_path.exists():
            ans = QMessageBox.question(self, "ARGUS", f"Ya existe {ct_path.name} en {folder}. Si lo usa otra "
                                       "geometría de esa carpeta, esa geometría verá el TAC nuevo. ¿Reemplazarlo?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        geo_path = None
        if self.session is None:
            geo_path = Path(self.geo_edit.text().strip()).expanduser()
            if geo_path.exists():
                ans = QMessageBox.question(self, "ARGUS", f"Ya existe {geo_path.name}. ¿Reemplazarla?")
                if ans != QMessageBox.StandardButton.Yes:
                    return
        plan = self.plan()
        container = self.container_combo.currentData() if self.session is not None else None
        if self.session is not None and self.session.ok:
            fit = self.check_fit(imp, container)
            if not fit.ok:
                self._set_status(fit.message + " Cambie la colocación, el recorte o el contenedor («Dentro de»).",
                                 error=True)
                self.tabs.setCurrentIndex(2)
                return
        desc = self.desc_edit.text().strip() or DEFAULT_DESCRIPTION
        new_input = bool(self.new_input_check.isChecked()) if self.new_input_check is not None else False
        preview = self.present_tissues()  # los tejidos que se ven con los HU sin promediar

        def work(prog, cancel):
            built = imp.build(prog, cancel)
            if cancel():
                raise Cancelled()
            if built.plan is not None and built.plan.problems:  # ni sin partir caben en el .in: no se escribe nada
                raise ValueError(built.plan.problems[0] + appeared_note(imp, built, preview))
            prog(0, 0, "Escribiendo el .ct…")
            written = imp.write_temp(folder, built, cancel=cancel)  # con nombres temporales: se renombran al terminar
            if cancel():
                discard_written(written)
                raise Cancelled()
            return built, written

        def done(res):
            built, written = res
            try:
                commit_written(written)
            except OSError as exc:
                self._error = f"No se puede escribir {ct_path.name}: {exc}"
                return
            final = built.plan if built.plan is not None else plan  # con los tejidos partidos por densidad
            self.result = CtImportResult(imp, built, final, container, ct_path, geo_path, new_input, desc)
            self.accept()

        def stale(res):  # se canceló mientras se escribía: no queda nada
            discard_written(res[1])

        self._run(work, done, "Calculando los vóxeles y escribiendo el .ct…", on_stale=stale)



def appeared_note(imp: CtImport, built: BuiltCt, preview: list[bool] | None) -> str:
    """Si al importar hay tejidos con material que el diálogo no veía (con los HU sin promediar: los vóxeles de la
    rejilla promedian el borde entre el aire y el agua, por ejemplo), por qué el .in necesita más materiales de los
    que decía el diálogo y qué hacer ("" si no los hay)."""
    if preview is None or built.parent_of is None or built.numbers is None:
        return ""
    tissues = imp.calibration.tissues
    voxels = [0] * len(tissues)
    for j, pi in enumerate(built.parent_of):
        if built.numbers[j] > 0:
            voxels[pi] += built.tissue_voxels[j]
    names = [t.name for i, t in enumerate(tissues) if i < len(preview) and not preview[i] and voxels[i] > 0]
    if not names:
        return ""
    return (" Con este vóxel aparecen al promediar los HU tejidos que el recorte no parecía tener "
            f"({', '.join(names)}): elija para ellos un material del .in o el vóxel del TAC («Como el TAC»).")


def _exact(spin: QDoubleSpinBox, exact: float | None) -> float:
    """El valor exacto si el cuadro sigue mostrando ese valor redondeado; si no, lo que se ha escrito."""
    v = spin.value()
    if exact is None or not np.isfinite(exact):
        return v
    # El cuadro muestra el valor exacto redondeado (a su manera: -15,625 → -15,63): está a media unidad
    # de la última cifra como mucho. Lo que escriba el usuario es otro valor de esa rejilla de cifras.
    step = 0.5 * 10.0 ** (-spin.decimals())
    return float(exact) if abs(v - float(exact)) <= step * (1.0 + 1e-9) else v


__all__ = ["CtImportDialog", "CtImportResult", "HuPreview"]
