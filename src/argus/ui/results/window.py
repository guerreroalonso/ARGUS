"""Ventana «Resultados»: las salidas de penhmain de una o varias carpetas, con sus gráficas y análisis.

* A la izquierda, un árbol con cada carpeta y sus resultados por familias (si
  la carpeta tiene ejecuciones en paralelo, también los combinados de todas y
  cada ejecución por separado).
* En el centro, la vista del resultado elegido: curvas, mapas, el resumen o un
  texto.
* A la derecha, las unidades, las medidas de la curva y la comparación con
  otras carpetas o con datos medidos.
* Abajo, el estado de la simulación. Cada 2 s se mira si penhmain ha vuelto a
  escribir algo y, si ha cambiado lo que se ve, se vuelve a dibujar.

Los ficheros se leen en otro hilo (un mapa 3D grande tarda unos segundos) y se
guardan en caché mientras no cambian.
"""

from __future__ import annotations

import csv
import html
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
)

from ...results.case import CaseGeometry, is_current, load_case_geometry
from ...results.catalog import FAMILIES, KINDS
from ...results.combine import Incompatible
from ...results.compare import gamma_index
from ...results.data import Curves, Grid, PhaseSpace, TextOutput, Tracks
from ...results.display import grid_scale, show_curves
from ...results.folder import ResultsFolder, aggregate_state, kind_of
from ...results.materialdata import MaterialData
from ...results.measured import measured_curve, position_scale, read_table
from ...results.readers import IncompleteFile
from ...results.store import ResultsStore
from ...results.summary import Summary
from ..screen_fit import fit_to_screen
from ..settings import app_settings
from .curves_view import CurvesView, Overlay
from .map_view import REL, MapView
from .panels import ComparePanel, MetricsPanel, UnitsPanel
from .shower_view import CASCADE_KEY, CascadeData
from .spectro_panel import SpectroPanel
from .summary_view import SummaryView, TextView

POLL_MS = 2000
ROLE = Qt.ItemDataRole.UserRole

# Resultados que calcula ARGUS (clave, nombre, familia, resultado de penhmain del que salen).
DERIVED = [("dvh-bodies", "Histogramas dosis-volumen por cuerpo", "dosis", "3d-dose-map"),
           ("dvh-tissues", "Histogramas dosis-volumen por tejido", "tac", "ct-3d-dose")]
DERIVED_KEYS = {d[0] for d in DERIVED}
CAMPAIGN_KEY = "campaign-metrics"
GRID_KINDS = {"3d-dose-map", "2d-dose-map", "radial-dose", "ct-3d-dose", "angle", "p3d-map", "p3d-map-cyl",
              "p3d-sphere"}
DERIVED_TITLES = {d[0]: d[1] for d in DERIVED}
DERIVED_TIPS = {
    "dvh-bodies": "Calculado por ARGUS con el mapa 3D de dosis y la geometría: cada vóxel se reparte entre los "
                  "cuerpos que corta.",
    "dvh-tissues": "Calculado por ARGUS con la dosis de los vóxeles del TAC y sus materiales (los tejidos partidos "
                   "por densidad, juntos).",
}


@dataclass
class Dataset:
    key: str
    folder: ResultsFolder
    label: str
    primary: bool = False


@dataclass(frozen=True)
class Ref:
    """Qué se ha elegido en el árbol: un resultado de una carpeta, de sus ejecuciones combinadas o de una de ellas."""

    dataset: str
    output: str  # clave del resultado (kind#detector@partícula) o 'res'
    run: int | None = None  # índice de la ejecución
    combined: bool = False


class _Loader(QObject):
    """Lee en otros hilos. Son hilos «daemon» propios (no los de ThreadPoolExecutor, que Python espera al salir): al
    cerrar ARGUS no se espera a que acabe de leerse un mapa grande."""

    loaded = Signal(int, object, str)

    def __init__(self, parent=None, skip_superseded: bool = False, workers: int = 2):
        super().__init__(parent)
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._alive = True
        self._lock = threading.Lock()
        self.skip_superseded = skip_superseded  # si ya se ha pedido otra cosa, esta no se lee
        self.latest = 0
        self._threads = [threading.Thread(target=self._work, name="argus-resultados", daemon=True)
                         for _ in range(max(1, workers))]
        for t in self._threads:
            t.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        # Si se destruye (con su ventana), sus hilos acaban en vez de quedarse esperando.
        q, n = self._queue, len(self._threads)
        self.destroyed.connect(lambda *_a: [q.put(None) for _ in range(n)])

    def submit(self, token: int, fn) -> None:
        self.latest = max(self.latest, token)
        self._queue.put((token, fn))

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None or not self._alive:
                return
            token, fn = item
            if self.skip_superseded and token < self.latest:
                data, err = None, "sustituido"
            else:
                try:
                    data, err = fn(), ""
                except IncompleteFile as exc:
                    data, err = None, f"incompleto: {exc}"
                except (OSError, ValueError, Incompatible) as exc:
                    data, err = None, str(exc)
                except Exception as exc:  # noqa: BLE001 - cualquier fallo tiene que llegar a la ventana
                    data, err = None, f"{type(exc).__name__}: {exc}"
            with self._lock:
                if not self._alive:
                    return
                try:
                    self.loaded.emit(token, data, err)
                except RuntimeError:  # la ventana ya no existe
                    pass

    def shutdown(self) -> None:
        with self._lock:
            if not self._alive:
                return
            self._alive = False
        for _ in self._threads:
            self._queue.put(None)


class ResultsWindow(QMainWindow):
    closed = Signal()
    showOnGeometry = Signal(object)  # DoseOverlay (o None) para superponer en los cortes de la ventana principal
    showTracksOnGeometry = Signal(object)  # lista de Tracks (o None) para dibujar sobre la geometría

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("ARGUS · Resultados")
        self.settings = settings or app_settings()
        self.store = ResultsStore()
        self.datasets: list[Dataset] = []
        self.measured: list[Overlay] = []
        self.current: Ref | None = None
        self.current_data = None
        self._token = 0
        self.loaded_token = 0  # el último resultado que ha llegado (para esperar en las pruebas)
        self.last_error = ""
        self._next_key = 0
        self._compare_checked: set[str] = set()
        self._signatures: dict[str, tuple] = {}
        self.loader = _Loader(self, skip_superseded=True)
        self.loader.loaded.connect(self._on_loaded)
        self._busy = False
        self._token_ref: dict[int, Ref] = {}
        self._retry_ref: Ref | None = None  # lo último que se leyó a medio escribir (se vuelve a leer al seguir)
        self.shown_ref: Ref | None = None  # a qué selección corresponde lo que se ve
        # La geometría de cada caso (nombres y materiales de los cuerpos…), también en otro hilo.
        self.cases: dict[str, CaseGeometry | None] = {}
        self.volumes: dict[str, tuple[tuple, object]] = {}  # (sello del caso, Volumes) de cada carpeta
        self._case_tokens: dict[int, tuple[str, Path, str]] = {}
        self._case_token = 0
        self.case_loader = _Loader(self)
        self.case_loader.loaded.connect(self._on_case_loaded)
        self._build()
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        fit_to_screen(self, 1400, 860)

    # -- construcción ---------------------------------------------------------------------
    def _build(self) -> None:
        tb = QToolBar("Resultados")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act_open = QAction("Abrir carpeta…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_folder_dialog)
        self.act_add = QAction("Añadir carpeta para comparar…", self)
        self.act_add.triggered.connect(self.add_folder_dialog)
        self.act_refresh = QAction("Actualizar", self)
        self.act_refresh.setShortcut(QKeySequence("F5"))
        self.act_refresh.triggered.connect(lambda: self.poll(force=True))
        self.act_follow = QAction("Seguir la simulación", self, checkable=True)
        self.act_follow.setChecked(True)
        self.act_follow.setToolTip("Volver a leer los resultados cuando penhmain los escribe (cada DUMPP segundos)")
        self.act_follow.toggled.connect(lambda on: self.timer.start() if on else self.timer.stop())
        self.act_fig = QAction("Exportar figura…", self)
        self.act_fig.triggered.connect(self.export_figure)
        self.act_csv = QAction("Exportar datos (CSV)…", self)
        self.act_csv.triggered.connect(self.export_csv)
        self.act_geo = QAction("Ver sobre la geometría", self, checkable=True)
        self.act_geo.setToolTip("Superponer este mapa (con isolíneas) en los cortes 2D de la ventana principal, o "
                                "dibujar las trayectorias sobre la geometría en 2D y en 3D")
        self.act_geo.toggled.connect(self._toggle_geometry)
        for a in (self.act_open, self.act_add, None, self.act_refresh, self.act_follow, None, self.act_fig,
                  self.act_csv, None, self.act_geo):
            if a is None:
                tb.addSeparator()
            else:
                tb.addAction(a)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(240)
        self.tree.currentItemChanged.connect(self._on_tree)
        split.addWidget(self.tree)
        self.views = QStackedWidget()
        self.empty = QLabel("Abra la carpeta donde ha simulado con penhmain (Resultados → Abrir carpeta…).")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.curves_view = CurvesView()
        self.map_view = MapView()
        self.summary_view = SummaryView()
        self.text_view = TextView()
        for w in (self.empty, self.curves_view, self.map_view, self.summary_view, self.text_view):
            self.views.addWidget(w)
        split.addWidget(self.views)
        self.side = QTabWidget()
        self.units = UnitsPanel()
        self.metrics = MetricsPanel()
        self.compare = ComparePanel()
        self.spectro = SpectroPanel(settings=self.settings)
        self.side.addTab(self.metrics, "Medidas")
        self.side.addTab(self.compare, "Comparar")
        self.side.addTab(self.spectro, "Espectro")
        self.side.addTab(self.units, "Unidades")
        self.side.setMinimumWidth(340)
        self.side.setUsesScrollButtons(False)
        split.addWidget(self.side)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([270, 800, 360])
        self.setCentralWidget(split)
        self.units.changed.connect(self._on_units)
        self.curves_view.activeCurveChanged.connect(self._on_active_curve)
        self.map_view.profileChanged.connect(self._on_profile)
        self.map_view.layer.currentIndexChanged.connect(self._refresh_geometry_overlay)
        self.map_view.iso.toggled.connect(self._refresh_geometry_overlay)
        self.map_view.iso_levels.editingFinished.connect(self._refresh_geometry_overlay)
        self.compare.changed.connect(self._on_compare_changed)
        self.compare.addFolderRequested.connect(self.add_folder_dialog)
        self.compare.addMeasuredRequested.connect(self.add_measured_dialog)
        self.compare.gammaRequested.connect(self.compute_gamma)
        self.spectro.changed.connect(self._update_overlays)
        sb = self.statusBar()
        self.state_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        sb.addWidget(self.state_label, 1)
        sb.addPermanentWidget(self.progress)

    # -- carpetas ---------------------------------------------------------------------------------
    def _new_key(self) -> str:
        self._next_key += 1
        return f"d{self._next_key}"

    def open_folder(self, path: str | Path, input_path: str | Path | None = None) -> Dataset:
        """Abre una carpeta como principal (la que se analiza), en lugar de la que lo era. Las carpetas añadidas
        para comparar se quedan."""
        folder = ResultsFolder(path, input_path)
        keep = [d for d in self.datasets if not d.primary and d.folder.path != folder.path]
        for d in self.datasets:
            if d not in keep:
                self._forget(d.key)
        ds = Dataset(self._new_key(), folder, self._label(folder, keep), primary=True)
        self.datasets = [ds] + keep
        self._signatures[ds.key] = folder.signature()
        self._token += 1  # lo que se estuviera leyendo de la carpeta anterior ya no se enseña
        self.loaded_token = self._token  # y no queda nada pendiente (hasta que se elija algo de la nueva)
        self._end_busy()
        self.current = None
        self.current_data = None
        self.shown_ref = None
        if self.act_geo.isChecked():
            self.act_geo.setChecked(False)
        self._rebuild_tree(select=ds)
        self._refresh_compare()
        self._update_state()  # la barra de estado, de la carpeta nueva (aunque no se esté siguiendo)
        return ds

    def _forget(self, key: str) -> None:
        for d in (self.cases, self.volumes):  # también las de cada variante de una campaña («clave#vN»)
            for k in [k for k in d if k == key or k.startswith(key + "#")]:
                d.pop(k, None)
        self._signatures.pop(key, None)
        self._compare_checked.discard(key)

    def add_folder(self, path: str | Path) -> Dataset:
        folder = ResultsFolder(path)
        ds = Dataset(self._new_key(), folder, self._label(folder))
        self.datasets.append(ds)
        self._signatures[ds.key] = folder.signature()
        self._compare_checked.add(ds.key)
        self._rebuild_tree()
        self._refresh_compare()
        return ds

    def _label(self, folder: ResultsFolder, others: list[Dataset] | None = None) -> str:
        names = [d.label for d in (self.datasets if others is None else others)]
        base = folder.path.name or str(folder.path)
        if base in names:
            base = f"{folder.path.parent.name}/{base}"
        return base

    def open_folder_dialog(self) -> None:
        start = str(self.settings.value("resultados/carpeta", "") or "")
        d = QFileDialog.getExistingDirectory(self, "Carpeta con los resultados de penhmain", start)
        if d:
            self.settings.setValue("resultados/carpeta", d)
            self.open_folder(d)

    def add_folder_dialog(self) -> None:
        start = str(self.settings.value("resultados/carpeta", "") or "")
        d = QFileDialog.getExistingDirectory(self, "Otra carpeta con resultados para comparar", start)
        if d:
            self.add_folder(d)

    def add_measured_dialog(self) -> None:
        start = str(self.settings.value("resultados/medidas", "") or "")
        path, _ = QFileDialog.getOpenFileName(self, "Datos medidos (texto o CSV)", start,
                                              "Texto o CSV (*.txt *.csv *.dat *.tsv);;Todos (*)")
        if path:
            self.settings.setValue("resultados/medidas", str(Path(path).parent))
            self.add_measured(path)

    def add_measured(self, path: str | Path, x_scale: float | None = None) -> Overlay | None:
        try:
            table = read_table(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "ARGUS", f"No se pueden leer los datos de {Path(path).name}: {exc}")
            return None
        if x_scale is None:
            # La unidad de la posición, por el título de su columna (solo esa: «gamma» o «incert. (mm)» de otra
            # columna no cuentan); si no lo dice, se pregunta.
            x_scale = position_scale(table.columns[0]) if table.columns else None
            if x_scale is None:
                units = {"cm": 1.0, "mm": 0.1, "m": 100.0}
                unit, ok = QInputDialog.getItem(
                    self, "ARGUS", f"¿En qué unidad está la posición (primera columna, «{table.columns[0]}») de "
                    f"{Path(path).name}?", list(units), 0, False)
                if not ok:
                    return None
                x_scale = units[unit]
        err_col = 2 if table.data.shape[1] >= 3 else None
        c = measured_curve(path, 0, 1, err_col, x_scale=x_scale, table=table)
        ov = Overlay(f"medidas: {Path(path).name}", c, measured=True)
        self.measured.append(ov)
        self._compare_checked.add(f"m{len(self.measured) - 1}")
        self._refresh_compare()
        self._update_overlays()
        return ov

    # -- árbol ------------------------------------------------------------------------------------------
    def _expansion(self) -> dict[tuple, bool]:
        """Qué nodos del árbol están abiertos (por el camino de textos desde la raíz)."""
        out: dict[tuple, bool] = {}
        stack = [(self.tree.topLevelItem(i), ()) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it, path = stack.pop()
            key = path + (it.text(0).lstrip("★ "),)
            if it.childCount():
                out[key] = it.isExpanded()
            stack.extend((it.child(i), key) for i in range(it.childCount()))
        return out

    def _restore_expansion(self, state: dict[tuple, bool]) -> None:
        stack = [(self.tree.topLevelItem(i), ()) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it, path = stack.pop()
            key = path + (it.text(0).lstrip("★ "),)
            if key in state:
                it.setExpanded(state[key])
            stack.extend((it.child(i), key) for i in range(it.childCount()))

    def _rebuild_tree(self, select: Dataset | None = None) -> None:
        cur = self.current
        expanded = self._expansion() if select is None else {}
        self.tree.blockSignals(True)
        self.tree.clear()
        target = None
        for ds in self.datasets:
            top = QTreeWidgetItem([("★ " if ds.primary else "") + ds.label])
            top.setToolTip(0, str(ds.folder.path))
            top.setData(0, ROLE, None)
            self.tree.addTopLevelItem(top)
            f = ds.folder
            if f.is_campaign:
                comp = QTreeWidgetItem([f"Comparar las {len(f.runs)} variantes"])
                comp.setToolTip(0, "Cada resultado de todas las variantes a la vez, y una medida frente al parámetro")
                top.addChild(comp)
                # Sin los histogramas dosis-volumen que calcula ARGUS: se ven en cada variante (cada una con su
                # geometría), no todos a la vez; tampoco el del TAC si alguna variante tiene varias ejecuciones.
                replicas = any(r.combines for r in f.runs)
                keys = [k for k in self._union_outputs(f, copies=False) if not (replicas and k[0] == "ct-dvh")]
                t = self._add_outputs(comp, ds, keys, None, combined=True, derived=False)
                it = QTreeWidgetItem(["Medidas frente al parámetro"])
                it.setData(0, ROLE, Ref(ds.key, CAMPAIGN_KEY, None, True))
                it.setToolTip(0, "Una medida de cada variante (alcance, energía media, anchura…) frente al parámetro, "
                                 "con un ajuste")
                comp.insertChild(1, it)
                target = target or t
                var_node = QTreeWidgetItem(["Cada variante"])
                top.addChild(var_node)
                for i, r in enumerate(f.runs):
                    vit = QTreeWidgetItem([r.label or r.path.name])
                    vit.setToolTip(0, str(r.path))
                    var_node.addChild(vit)
                    outs = self._union_outputs(r, copies=False) if r.combines else \
                        [(o.key, o.label, o.family) for o in r.visible()]
                    self._add_outputs(vit, ds, outs, i)
                comp.setExpanded(True)
            elif f.combines:
                comb = QTreeWidgetItem([f"Todas las ejecuciones combinadas ({len(f.runs)})"])
                top.addChild(comb)
                keys = self._union_outputs(f, copies=False)
                t = self._add_outputs(comb, ds, keys, None, combined=True)
                target = target or t
                runs = QTreeWidgetItem(["Cada ejecución"])
                top.addChild(runs)
                for i, r in enumerate(f.runs):
                    it = QTreeWidgetItem([r.path.name])
                    runs.addChild(it)
                    self._add_outputs(it, ds, [(o.key, o.label, o.family) for o in r.visible()], i)
                comb.setExpanded(True)
            elif f.is_group:
                # Casos distintos (o copias con las mismas semillas): cada uno por separado, sin combinarlos.
                cases = QTreeWidgetItem([f"Cada caso por separado ({len(f.runs)})"])
                cases.setToolTip(0, f"No se combinan: {f.not_replicas}.")
                top.addChild(cases)
                for i, r in enumerate(f.runs):
                    it = QTreeWidgetItem([r.path.name])
                    it.setToolTip(0, str(r.path))
                    cases.addChild(it)
                    t = self._add_outputs(it, ds, [(o.key, o.label, o.family) for o in r.visible()], i)
                    target = target or t
                cases.setExpanded(True)
            else:
                t = self._add_outputs(top, ds, [(o.key, o.label, o.family) for o in f.visible()], None)
                target = target or t
                self._add_cascade_item(top, ds)
            top.setExpanded(ds.primary)
        self._restore_expansion(expanded)
        item = self._find_item(cur) if cur is not None else None
        if item is None and select is not None:
            sf = select.folder
            first = Ref(select.key, "res", 0) if sf.is_group else Ref(select.key, "res", combined=sf.combines)
            item = self._find_item(first) or target
        if item is not None:
            self.tree.setCurrentItem(item)  # sin avisar: si es otra selección, se lee aquí abajo
        self.tree.blockSignals(False)
        ref = item.data(0, ROLE) if item is not None else None
        if isinstance(ref, Ref) and ref != cur:
            self.select(ref)
        elif item is None and cur is None:
            self._show_empty()

    def _add_cascade_item(self, top: QTreeWidgetItem, ds: Dataset) -> None:
        """«Cascada de partículas» (detrás del resumen) si se puede simular (hay .in) o ya hay una."""
        from ...penhan.cascade import find_cascades
        from .shower_view import CASCADE_KEY, CASCADE_TITLE

        in_path = self._input_of(ds)
        if in_path is None and not find_cascades(ds.folder.path):
            return
        it = QTreeWidgetItem([CASCADE_TITLE])
        it.setData(0, ROLE, Ref(ds.key, CASCADE_KEY))
        it.setToolTip(0, "Las trayectorias reales de las primeras lluvias del caso, simuladas con su penhmain, en 3D "
                         "sobre la geometría y animadas en el tiempo")
        first = top.child(0) if top.childCount() else None
        ref0 = first.data(0, ROLE) if first is not None else None
        top.insertChild(1 if isinstance(ref0, Ref) and ref0.output == "res" else 0, it)

    def show_cascade(self, in_path: str | Path) -> None:
        """Abre la carpeta del .in (si no es ya la principal) y enseña su cascada."""
        from .shower_view import CASCADE_KEY

        in_path = Path(in_path)
        ds = self.primary()
        if ds is None or os.path.normcase(os.path.abspath(ds.folder.path)) != os.path.normcase(
                os.path.abspath(in_path.parent)):
            ds = self.open_folder(in_path.parent, in_path)
        item = self._find_item(Ref(ds.key, CASCADE_KEY))
        if item is not None:
            self.tree.setCurrentItem(item)

    def _union_outputs(self, f: ResultsFolder, copies: bool = True) -> list[tuple[str, str, str]]:
        """Los resultados de todas las ejecuciones de ``f``; sin ``copies``, sin las copias renombradas de cada
        una (lo que se combina o se compara entre todas)."""
        seen: dict[str, tuple[str, str, str]] = {}
        for r in f.leaves():
            for o in r.visible():
                if o.copy and not copies:
                    continue
                label = o.label if copies or not o.twin else o._label()
                seen.setdefault(o.key, (o.key, label, o.family))
        return list(seen.values())

    def _add_outputs(self, parent: QTreeWidgetItem, ds: Dataset, outputs: list[tuple[str, str, str]],
                     run: int | None, combined: bool = False, derived: bool = True) -> QTreeWidgetItem | None:
        """Los resultados de una carpeta en el árbol, por familias; con ``derived``, también los que calcula ARGUS
        (histogramas dosis-volumen) con los de penhmain."""
        by_fam: dict[str, list[tuple[str, str]]] = {}
        keys = {k for k, _l, _f in outputs}
        extra = [d[:3] for d in DERIVED if d[3] in keys] if derived else []
        for key, label, fam in outputs + extra:
            by_fam.setdefault(fam, []).append((key, label))
        first = None
        for fam, title in FAMILIES:
            items = by_fam.get(fam)
            if not items:
                continue
            if fam == "resumen":
                for key, label in items:
                    it = QTreeWidgetItem([label])
                    it.setData(0, ROLE, Ref(ds.key, key, run, combined))
                    parent.addChild(it)
                    first = first or it
                continue
            node = QTreeWidgetItem([title])
            parent.addChild(node)
            for key, label in sorted(items, key=lambda t: t[1]):
                it = QTreeWidgetItem([label])
                it.setData(0, ROLE, Ref(ds.key, key, run, combined))
                kind = KINDS.get(kind_of(key))
                if kind is not None and kind.description:
                    it.setToolTip(0, kind.description)
                elif key in DERIVED_TIPS:
                    it.setToolTip(0, DERIVED_TIPS[key])
                node.addChild(it)
            node.setExpanded(fam in ("dosis", "tac", "detectores"))
        return first

    def _find_item(self, ref: Ref | None) -> QTreeWidgetItem | None:
        if ref is None:
            return None
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, ROLE) == ref:
                return it
            stack.extend(it.child(i) for i in range(it.childCount()))
        return None

    def dataset(self, key: str) -> Dataset | None:
        return next((d for d in self.datasets if d.key == key), None)

    def primary(self) -> Dataset | None:
        return next((d for d in self.datasets if d.primary), self.datasets[0] if self.datasets else None)

    # -- elegir y cargar -----------------------------------------------------------------------------------
    def _on_tree(self, item: QTreeWidgetItem | None, _prev=None) -> None:
        ref = item.data(0, ROLE) if item is not None else None
        if not isinstance(ref, Ref):
            return
        self.select(ref)

    def select(self, ref: Ref) -> None:
        changed = ref != self.current
        self.current = ref
        self._load(ref, keep_view=not changed)

    def _source(self, ref: Ref):
        ds = self.dataset(ref.dataset)
        if ds is None:
            raise ValueError("La carpeta ya no está abierta")
        f = ds.folder
        if ref.output == CASCADE_KEY:
            return self._cascade_source(ds)
        if f.is_campaign:
            if ref.output == CAMPAIGN_KEY:
                data = self._campaign_data(ds)
                return lambda: data
            if ref.output in DERIVED_KEYS:
                return self._derived_source(ds, ref)
            i = self._campaign_main(f, ref.output) if ref.combined or ref.run is None else min(ref.run,
                                                                                              len(f.runs) - 1)
            variant = f.runs[i]
            if ref.output == "ct-dvh" and variant.combines:
                return self._derived_source(ds, ref)  # el de sus ejecuciones juntas se recalcula con su dosis
            return lambda: self.store.get(variant, ref.output)
        if ref.output in DERIVED_KEYS or (ref.combined and ref.output == "ct-dvh"):
            return self._derived_source(ds, ref)
        if ref.combined:
            return lambda: self.store.combined(f, ref.output)
        folder = f.runs[ref.run] if ref.run is not None and ref.run < len(f.runs) else f
        out = folder.find(ref.output)
        if out is None:
            raise ValueError("Ese resultado ya no está en la carpeta")
        writing = folder.writing
        res = folder.find("res")
        summary = res.path if res is not None and res is not out else None  # para saber si está entero
        return lambda: self.store.load(out, writing=writing, summary=summary)

    def _cascade_source(self, ds: Dataset):
        """La cascada del caso de la carpeta (con la geometría ya leída, si lo está)."""
        from ..cascade_ui import cascade_exe
        from .shower_view import load_cascade_data

        in_path = self._input_of(ds)
        case = self.cases.get(self._case_key(ds)[0])
        try:
            exe = cascade_exe(in_path, self.settings) if in_path is not None else None
        except (OSError, ValueError):
            exe = None
        folder = ds.folder.path
        return lambda: load_cascade_data(folder, in_path, case=case, exe=exe)

    def _campaign_data(self, ds: Dataset):
        """Lo que necesita la vista de «Medidas frente al parámetro» de una campaña."""
        from .campaign_view import CampaignData

        f = ds.folder
        curve_fams = {"dosis", "tac", "detectores", "emergentes", "fuente", "proceso"}
        replicas = any(r.combines for r in f.runs)  # su ct-dvh no se puede combinar
        outs = [(k, lab) for k, lab, fam in self._union_outputs(f, copies=False)
                if fam in curve_fams and KINDS.get(kind_of(k)) is not None
                and kind_of(k) not in GRID_KINDS and not (replicas and k == "ct-dvh")]
        store = self.store

        def load(key: str, i: int):
            return store.get(f.runs[i], key)

        summary = self._summary_of(ds, Ref(ds.key, "res", 0))
        return CampaignData([r.label or r.path.name for r in f.runs], [f.variant_values(i) for i in range(len(f.runs))],
                            f.parameters, outs, load, summary.kparp if summary else None)

    def _derived_source(self, ds: Dataset, ref: Ref):
        """Resultados que ARGUS calcula con los de penhmain y la geometría del caso: los histogramas dosis-volumen
        por cuerpo o por tejido, y el del TAC entero con las ejecuciones combinadas."""
        from ...pengeom.ct import ct_families
        from ...results import dvh

        grid_key = "3d-dose-map" if ref.output == "dvh-bodies" else "ct-3d-dose"
        grid_fn = self._source(Ref(ref.dataset, grid_key, ref.run, ref.combined))
        in_path = self._input_of(ds, ref)
        case_key = self._case_key(ds, ref)[0]
        cached = self.cases.get(case_key)
        vols = self.volumes.get(case_key)
        title = DERIVED_TITLES.get(ref.output, "Histograma dosis-volumen del TAC")

        def job():
            grid = grid_fn()
            if not isinstance(grid, Grid):
                raise ValueError("No se ha podido leer el mapa de dosis")
            case = cached if cached is not None and is_current(cached) else (
                load_case_geometry(in_path) if in_path is not None else None)
            if ref.output == "ct-dvh" and (case is None or case.geo is None or case.geo.ct is None):
                return dvh.dvh_curves([dvh.whole_ct(grid)], "dvh-ct", title)
            if case is None or case.geo is None or not case.ok:
                why = case.problem if case is not None and case.problem else "no se ha encontrado el .in en la carpeta"
                raise ValueError(f"Hace falta la geometría del caso: {why}")
            if case.mismatch:
                raise ValueError(f"No se puede calcular con la geometría de ahora: {case.mismatch}.")
            if ref.output == "dvh-bodies":
                from ...results.bodies import sample_volumes

                st = dvh.box_structures(grid, case.session, case)
                # Un cuerpo que la rejilla cubre solo en parte: su histograma es el de esa parte (se dice).
                v = vols[1] if vols is not None and vols[0] == case.stamp else sample_volumes(case.session)
                partial = dvh.mark_partial(st, v)
                out = dvh.dvh_curves(st, "dvh-bodies", title)
                if partial:
                    out.meta["note"] = ("La rejilla de dosis (GRIDX/Y/Z) solo cubre una parte de algunos cuerpos: su "
                                        "histograma es el de la parte cubierta.")
                return out
            ct = case.geo.ct
            if ct is None:
                raise ValueError("La geometría del caso no tiene TAC")
            if ref.output == "ct-dvh":
                return dvh.dvh_curves([dvh.whole_ct(grid, ct)], "dvh-ct", title)
            names = {m: case.material_name(m) for m in ct.materials()}
            st = dvh.ct_structures(grid, ct, ct_families(ct), names)
            return dvh.dvh_curves(st, "dvh-tissues", title)

        return job

    def _load(self, ref: Ref, keep_view: bool = False) -> None:
        self._token += 1
        token = self._token
        self._token_ref = {token: ref}
        try:
            fn = self._source(ref)
        except ValueError as exc:
            self._end_busy()
            self._show_problem(str(exc))
            self.loaded_token, self.last_error = token, str(exc)
            self._retry_ref = None
            return
        self._keep_view = keep_view
        if not keep_view and not self._busy:
            QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            self._busy = True
        self.loader.submit(token, fn)

    def _end_busy(self) -> None:
        if self._busy:
            QApplication.restoreOverrideCursor()
            self._busy = False

    def _on_loaded(self, token: int, data, err: str) -> None:
        if token != self._token:
            return  # una lectura sustituida por otra más reciente
        self._end_busy()
        ref = self._token_ref.get(token)
        self.loaded_token, self.last_error = token, err
        # A medio escribir: se vuelve a leer en la próxima vuelta aunque la carpeta no cambie (si penhmain ya ha
        # terminado, no cambiará más).
        self._retry_ref = ref if data is None and err.startswith("incompleto") else None
        if data is None:
            if err.startswith("incompleto") and self.current_data is not None and ref == self.shown_ref:
                return  # penhmain lo está reescribiendo: se queda lo anterior y se vuelve a leer en la próxima vuelta
            if err.startswith("incompleto"):
                self._show_problem("penhmain está escribiendo este resultado: se leerá en cuanto termine.")
            else:
                self._show_problem(f"No se ha podido leer: {err}")
            return
        self.current_data = data
        self.shown_ref = ref
        self._show(data, keep=getattr(self, "_keep_view", False))
        self._update_state()

    def _show_problem(self, text: str) -> None:
        """Lo que se ve ya no es lo elegido: en su lugar, por qué no se puede ver."""
        self.current_data = None
        self.shown_ref = None
        self.empty.setText(text)
        self.views.setCurrentWidget(self.empty)
        self.side.setVisible(True)
        self.metrics.clear()
        self.spectro.clear()

    def _title(self, ref: Ref) -> str:
        ds = self.dataset(ref.dataset)
        if ds is None:
            return ""
        f = ds.folder
        if ref.output == CAMPAIGN_KEY:
            return f"Medidas frente al parámetro · {ds.label}"
        if ref.output == CASCADE_KEY:
            return f"Cascada de partículas · {ds.label}"
        if f.is_campaign:
            label = DERIVED_TITLES.get(ref.output) or next(
                (lab for k, lab, _f in self._union_outputs(f) if k == ref.output), ref.output)
            if ref.combined or ref.run is None:
                kind = KINDS.get(kind_of(ref.output))
                if ref.output == "res" or (kind is not None and kind.shows == "curves") or not f.runs:
                    return f"{label} · {ds.label}"  # todas las variantes a la vez
                r = f.runs[self._campaign_main(f, ref.output)]
                return f"{label} · {r.label or r.path.name} (las demás variantes, en «Cada variante»)"
            r = f.runs[min(ref.run, len(f.runs) - 1)]
            return f"{label} · {r.label or r.path.name}"
        if ref.combined:
            label = DERIVED_TITLES.get(ref.output) or next(
                (lab for k, lab, _f in self._union_outputs(f) if k == ref.output), ref.output)
            return f"{label} · {ds.label} (combinadas)"
        folder = f.runs[ref.run] if ref.run is not None else f
        o = folder.find(ref.output)
        label = o.label if o else DERIVED_TITLES.get(ref.output, ref.output)
        return f"{label} · {ds.label}" + (f"/{folder.path.name}" if ref.run is not None else "")

    def _show(self, data, keep: bool = False) -> None:
        ref = self.current
        title = self._title(ref) if ref else ""
        if not keep:
            self.compare.g_result.setText("")  # el gamma era de lo que se veía antes
        if not isinstance(data, Curves):
            self.spectro.clear()  # el panel «Espectro» es de curvas
        opts = self.units.options()
        ds = self.dataset(ref.dataset) if ref else None
        summary = self._summary_of(ds, ref) if ds else None
        self.units.per_decay = bool(summary and summary.per_decay)
        opts.per_decay = self.units.per_decay
        self.metrics.primary = summary.kparp if summary else None
        cascade = isinstance(data, CascadeData)
        self.side.setVisible(not cascade)  # la cascada tiene sus propios controles: más sitio para la vista 3D
        spatial = (isinstance(data, Grid) and data.geometry in ("box", "ct", "cylinder", "sphere")) \
            or isinstance(data, Tracks) or cascade
        self.act_geo.setEnabled(spatial or self.act_geo.isChecked())  # encendida, siempre se puede apagar
        if isinstance(data, Summary):
            self.views.setCurrentWidget(self.summary_view)
            self._show_summary(data, ds, ref)
            self.metrics.clear()
        elif isinstance(data, Curves):
            self.views.setCurrentWidget(self.curves_view)
            self.curves_view.opts = opts
            self.curves_view.primary_label = ds.label if ds else ""
            self.curves_view.primary_color = None
            if ds is not None and ds.folder.is_campaign and ref.combined and ds.folder.runs:
                i0 = self._campaign_main(ds.folder, ref.output)
                v0 = ds.folder.runs[i0]
                self.curves_view.primary_label = v0.label or v0.path.name
                self.curves_view.primary_color = self._campaign_colors(ds.folder)[i0]
            # Sin la convolucionada: la del panel «Espectro» es aún la de la curva anterior; la nueva se añade al
            # elegir su serie (_on_active_curve).
            self.curves_view.overlays = self._overlays_for(ref, with_resolution=False)
            self.curves_view.set_curves(data, title, keep_series=keep)
        elif isinstance(data, Grid):
            self.views.setCurrentWidget(self.map_view)
            self.map_view.opts = opts
            self.map_view.set_grid(data, title)
            if self.act_geo.isChecked():
                self._refresh_geometry_overlay()
        elif isinstance(data, TextOutput):
            self.views.setCurrentWidget(self.text_view)
            self.text_view.set_text(data.text)
            self.metrics.clear()
        elif isinstance(data, (Tracks, PhaseSpace, MaterialData, CascadeData)) or type(data).__name__ == "CampaignData":
            self._show_special(data, title)
        else:
            self._show_empty()
        if self.act_geo.isChecked() and not isinstance(data, (Grid, Tracks, CascadeData)):
            self._refresh_geometry_overlay()  # quita de la ventana principal lo del resultado anterior
        self._refresh_compare()

    def show_profile(self, c: Curves) -> None:
        """Un perfil sacado en la ventana principal (sobre los cortes): en la vista de curvas, con sus medidas."""
        opts = self.units.options()
        opts.per_decay = self.units.per_decay
        self.views.setCurrentWidget(self.curves_view)
        self.curves_view.opts = opts
        self.curves_view.primary_label = ""
        self.curves_view.overlays = []
        self.curves_view.set_curves(c, c.title)

    def _campaign_summary(self, ds: Dataset) -> None:
        from .summary_view import campaign_html

        f = ds.folder
        rows = []
        for r in f.runs:
            st = aggregate_state(r)
            try:
                s = self.store.get(r, "res")
            except (OSError, ValueError, Incompatible):
                s = None
            rows.append((r.label or r.path.name, st, s))
        self.summary_view.browser.setHtml(campaign_html(ds.label, f.parameters, rows))

    def _show_summary(self, data: Summary, ds: Dataset | None, ref: Ref | None) -> None:
        if ds is not None and ds.folder.is_campaign and ref is not None and ref.combined:
            self._campaign_summary(ds)
            return
        state = None
        if ds is not None and ref is not None:
            f = ds.folder
            if ref.run is not None and ref.run < len(f.runs):
                state = aggregate_state(f.runs[ref.run])  # una ejecución, o una variante con varias
            elif ref.combined:
                state = aggregate_state(f)
            else:
                state = f.state()
        case = self.case_of(ds, ref) if ds is not None else None
        names: dict[int, str] = {}
        mats: dict[int, str] = {}
        note = ""
        dose: dict[int, str] = {}
        mass: dict[int, str] = {}
        if case is not None and case.mismatch:
            note = f"Sin los nombres, la masa ni la dosis de los cuerpos: {case.mismatch}."
        elif case is not None and case.geo is not None:
            for kb in data.body_energy:
                names[kb] = case.body_name(kb)
                mats[kb] = case.material_name(case.body_material(kb))
            table = self.body_dose_table(ds, data, ref) if ds is not None else None
            if table is not None:
                opts = self.units.options()
                opts.per_decay = data.per_decay
                if opts.norm.relative:
                    from ...results.units import Normalization

                    opts.norm = Normalization()
                for bd in table:
                    if bd.mass is not None:
                        mass[bd.kb] = f"{bd.mass:.4g} g"
                    if bd.dose is not None:
                        f, unit = grid_scale("eV/g", abs(bd.dose), opts)
                        rel = f" <span class='muted'>({100 * bd.dose_err / abs(bd.dose):.2g} %)</span>" \
                            if bd.dose and bd.dose_err else ""
                        dose[bd.kb] = f"{bd.dose * f:.4g} ± {(bd.dose_err or 0) * f:.2g} {unit}{rel}"
                    elif bd.note:
                        dose[bd.kb] = f"<span class='muted'>{html.escape(bd.note)}</span>"
                note = ("Masa: volumen de cada cuerpo medido con LOCATE (puntos al azar por celdas) por la densidad de "
                        "su material; en el TAC, la de sus vóxeles. Dosis media = energía / masa, "
                        + opts.norm.label(data.per_decay) + ".")
        elif case is not None and case.problem:
            note = f"Sin los nombres de los cuerpos: {case.problem}"
        elif ds is not None and self._input_of(ds, ref) is None:
            note = "Sin los nombres de los cuerpos: no se ha encontrado el .in de la simulación en la carpeta."
        self.summary_view.set_summary(data, state, names, dose or None, mats, note, mass or None)

    # -- geometría del caso ----------------------------------------------------------------------------------
    def _case_key(self, ds: Dataset, ref: Ref | None = None) -> tuple[str, ResultsFolder]:
        """Clave de la geometría del caso y carpeta de la que sale su .in. En una campaña, la de cada variante
        (su .in puede cambiar la geometría o los materiales); en lo que compara todas, la de la primera."""
        f = ds.folder
        if (f.is_campaign or f.is_group) and f.runs:
            i = ref.run if ref is not None and ref.run is not None else 0
            i = min(max(int(i), 0), len(f.runs) - 1)
            return f"{ds.key}#v{i}", f.runs[i]
        return ds.key, f

    def _input_of(self, ds: Dataset, ref: Ref | None = None) -> Path | None:
        _key, f = self._case_key(ds, ref)
        info = f.input if f.input is not None else next((r.input for r in f.leaves() if r.input is not None), None)
        return info.path if info is not None else None

    def case_of(self, ds: Dataset, ref: Ref | None = None) -> CaseGeometry | None:
        """La geometría del caso de una carpeta (o de una variante de una campaña): None mientras se lee; se pide
        la primera vez."""
        key, _f = self._case_key(ds, ref)
        path = self._input_of(ds, ref)
        if key in self.cases:
            case = self.cases[key]
            if case is None or (case.input_path == path and is_current(case)):
                return case
        if path is None:
            return None
        self.cases[key] = None
        self._case_token += 1
        self._case_tokens[self._case_token] = (ds.key, key, path, "case")
        self.case_loader.submit(self._case_token, lambda: load_case_geometry(path))
        return None

    def _on_case_loaded(self, token: int, data, err: str) -> None:
        from ...results.bodies import sample_volumes

        ds_key, key, path, what = self._case_tokens.pop(token, (None, None, None, None))
        if key is None or self.dataset(ds_key) is None:
            return
        if what == "case":
            case = data if isinstance(data, CaseGeometry) else CaseGeometry(path, None, err or "error")
            self.cases[key] = case
            self.volumes.pop(key, None)
            if case.ok and not case.mismatch and case.session is not None and case.session.locator is not None:
                # Después, el volumen de cada cuerpo (para su masa y su dosis media).
                self._case_token += 1
                self._case_tokens[self._case_token] = (ds_key, key, path, "volumes")
                session = case.session
                self.case_loader.submit(self._case_token, lambda: sample_volumes(session))
        elif what == "volumes":
            case = self.cases.get(key)
            if case is not None and data is not None:
                self.volumes[key] = (case.stamp, data)
        cur = self.current
        ds = self.dataset(ds_key)
        if cur is not None and cur.dataset == ds_key and self._case_key(ds, cur)[0] == key \
                and isinstance(self.current_data, Summary) and self.views.currentWidget() is self.summary_view:
            self._show_summary(self.current_data, ds, cur)

    def _material_data(self, ds: Dataset, ref: Ref | None = None):
        """Los datos de los materiales (material.dat) de la carpeta, o de la variante, (o de su primera
        ejecución)."""
        from ...results.materialdata import MaterialData

        _key, f = self._case_key(ds, ref)
        for folder in [f] + f.leaves()[:1]:
            o = folder.find("material")
            if o is not None:
                try:
                    md = self.store.load(o)
                except (OSError, ValueError):
                    continue
                if isinstance(md, MaterialData):
                    return md
        return None

    def body_dose_table(self, ds: Dataset, summary: Summary, ref: Ref | None = None):
        """Masa y dosis media de cada cuerpo (None mientras no se sepa la geometría y el volumen de los cuerpos)."""
        from ...results.bodies import body_doses, material_densities

        key, _f = self._case_key(ds, ref)
        case = self.cases.get(key)
        vol = self.volumes.get(key)
        if case is None or case.geo is None or case.mismatch or vol is None or vol[0] != case.stamp:
            return None
        dens = material_densities(self._material_data(ds, ref), case)
        return body_doses(case, vol[1], dens, summary)

    def _show_special(self, data, title: str) -> None:
        """Trayectorias, espacio de fases y datos de materiales (vistas propias)."""
        from .special_views import view_for

        w = view_for(self, data)
        if w is None:
            self._show_empty()
            return
        if self.views.indexOf(w) < 0:
            self.views.addWidget(w)
        self.views.setCurrentWidget(w)
        self.metrics.clear()
        w.set_data(data, title, self.units.options(), self._special_context(data))
        if hasattr(w, "changed") and not getattr(w, "_argus_connected", False):
            w.changed.connect(self._refresh_geometry_overlay)
            w._argus_connected = True
        if isinstance(data, (Tracks, CascadeData)) and self.act_geo.isChecked():
            self._refresh_geometry_overlay()

    def _special_context(self, data) -> dict:
        """Lo que necesitan las vistas especiales además del fichero: las trayectorias de las demás partículas y
        el número de lluvias (para dar el espacio de fases por partícula primaria)."""
        ref = self.current
        ds = self.dataset(ref.dataset) if ref is not None else None
        if ds is None:
            return {}
        f = ds.folder
        if ref.run is not None and ref.run < len(f.runs):
            folder = f.runs[ref.run]
        elif ref.combined and f.runs:
            folder = f.runs[0]  # lo que no se combina (trayectorias, espacio de fases…) es el de la primera
        else:
            folder = f
        folder = folder.leaves()[0]
        ctx: dict = {}
        if isinstance(data, Tracks):
            sib = []
            for o in folder.files:
                if o.kind == "trk":
                    try:
                        t = self.store.load(o)
                    except (OSError, ValueError):
                        continue
                    if isinstance(t, Tracks):
                        sib.append(t)
            # Con una fuente radiactiva, cada partícula en su trayectoria (penhmain pega las demás de cada
            # desintegración a la anterior de su tipo).
            inp = folder.input
            if inp is not None and inp.kparp == 0:
                from ...results.shower import separate_tracks

                try:
                    sep = separate_tracks(folder.path, inp.path.name)
                except (OSError, ValueError):
                    sep = None
                if sep:
                    sib = sep
            ctx["siblings"] = sib
        elif isinstance(data, PhaseSpace):
            try:
                s = self.store.summary(folder)
            except (OSError, ValueError):
                s = None
            ctx["showers"] = s.showers if s is not None and s.showers else None
        return ctx

    EMPTY_TEXT = "Abra la carpeta donde ha simulado con penhmain (Resultados → Abrir carpeta…)."

    def _show_empty(self) -> None:
        self.empty.setText(self.EMPTY_TEXT)
        self.views.setCurrentWidget(self.empty)
        self.side.setVisible(True)
        self.metrics.clear()

    def _summary_of(self, ds: Dataset, ref: Ref | None) -> Summary | None:
        f = ds.folder
        try:
            if f.is_campaign:  # nunca se combinan variantes distintas
                i = ref.run if ref is not None and ref.run is not None else 0
                return self.store.get(f.runs[min(i, len(f.runs) - 1)], "res") if f.runs else None
            if f.combines:
                return self.store.combined(f, "res")
            folder = f.runs[ref.run] if ref is not None and ref.run is not None and ref.run < len(f.runs) else f
            return self.store.summary(folder)
        except (OSError, ValueError, Incompatible):
            return None

    @staticmethod
    def _has_output(folder: ResultsFolder, key: str) -> bool:
        return any(leaf.find(key) is not None for leaf in folder.leaves())

    # -- comparaciones ----------------------------------------------------------------------------
    def _compare_candidates(self) -> list[tuple[str, str, bool]]:
        ref = self.current
        out = []
        if ref is not None:
            for d in self.datasets:
                if d.key == ref.dataset:
                    continue
                if d.folder.is_group:  # casos distintos: cada uno por separado
                    for i, r in enumerate(d.folder.runs):
                        if self._has_output(r, ref.output):
                            key = f"{d.key}:{i}"
                            out.append((key, f"{d.label}/{r.path.name}", key in self._compare_checked))
                    continue
                if self._has_output(d.folder, ref.output):
                    out.append((d.key, d.label, d.key in self._compare_checked))
            ds = self.dataset(ref.dataset)
            campaign_all = ds is not None and ds.folder.is_campaign and ref.combined
            if ds is not None and ds.folder.runs and (ref.combined or ref.run is not None) and not campaign_all:
                for i, r in enumerate(ds.folder.runs):
                    if ref.run == i or not self._has_output(r, ref.output):
                        continue
                    key = f"{ds.key}:{i}"
                    out.append((key, f"{ds.label}/{r.label or r.path.name}", key in self._compare_checked))
        # Las medidas son posiciones (cm): solo con curvas en función de la posición, o con el perfil de un mapa.
        cur = self.current_data
        if (isinstance(cur, Curves) and cur.x_unit == "cm") or isinstance(cur, Grid):
            for i, m in enumerate(self.measured):
                out.append((f"m{i}", m.label, f"m{i}" in self._compare_checked))
        return out

    def _refresh_compare(self) -> None:
        self.compare.set_candidates(self._compare_candidates())

    def _on_compare_changed(self) -> None:
        checked = set(self.compare.checked())
        visible = {k for k, _t, _c in self._compare_candidates()}
        self._compare_checked = (self._compare_checked - visible) | checked
        self._update_overlays()

    def _load_other(self, key: str, output: str):
        if key.startswith("m"):
            return self.measured[int(key[1:])].curves
        if ":" in key:
            dkey, i = key.split(":")
            ds = self.dataset(dkey)
            return self.store.get(ds.folder.runs[int(i)], output) if ds else None
        ds = self.dataset(key)
        if ds is None:
            return None
        if ds.folder.is_campaign:
            return self.store.get(ds.folder.runs[self._campaign_main(ds.folder, output)], output) \
                if ds.folder.runs else None
        if ds.folder.combines:
            return self.store.combined(ds.folder, output)
        o = ds.folder.find(output)
        res = ds.folder.find("res")
        return self.store.load(o, writing=ds.folder.writing,
                               summary=res.path if res is not None and res is not o else None) if o else None

    def _campaign_colors(self, f: ResultsFolder) -> list[str]:
        """Color de cada variante: con un parámetro numérico, de claro a oscuro según su valor (rampa azul); si no,
        los colores categóricos en orden."""
        from .style import BLUE_RAMP, CATEGORICAL

        n = len(f.runs)
        params = f.parameters
        if len(params) == 1 and params[0].get("kind") != "material" and n > 1:
            vals = []
            for i in range(n):
                try:
                    vals.append(float(f.variant_values(i).get(params[0].get("name"), i)))
                except (TypeError, ValueError):
                    vals.append(float(i))
            lo, hi = min(vals), max(vals)
            ramp = BLUE_RAMP[3:]
            return [ramp[int(round((v - lo) / (hi - lo) * (len(ramp) - 1)))] if hi > lo else ramp[-1] for v in vals]
        return [CATEGORICAL[i % len(CATEGORICAL)] for i in range(n)]

    def _campaign_main(self, f: ResultsFolder, output: str) -> int:
        """La variante que se dibuja como principal al comparar todas: la primera que tiene ese resultado."""
        return next((i for i, r in enumerate(f.runs) if self._has_output(r, output)), 0)

    def _campaign_overlays(self, ds: Dataset, output: str) -> list[Overlay]:
        f = ds.folder
        colors = self._campaign_colors(f)
        main = self._campaign_main(f, output)
        out = []
        for i, r in enumerate(f.runs):
            if i == main:
                continue
            try:
                data = self.store.get(r, output)
            except (OSError, ValueError, Incompatible):
                continue
            if isinstance(data, Curves):
                out.append(Overlay(r.label or r.path.name, data, color=colors[i]))
        return out

    def _overlays_for(self, ref: Ref | None, with_resolution: bool = True) -> list[Overlay]:
        """Las curvas que se dibujan encima de la de ``ref``. Sin ``with_resolution``, sin la convolucionada del
        panel «Espectro» (antes de mostrar una curva nueva: el panel aún tiene la anterior)."""
        if ref is None:
            return []
        out = []
        ds = self.dataset(ref.dataset)
        if ds is not None and ds.folder.is_campaign and ref.combined:
            out.extend(self._campaign_overlays(ds, ref.output))
        # Lo marcado entre lo que se puede comparar con esta selección (la lista del panel aún puede ser la de la
        # anterior: se refresca después de dibujar).
        cands = self._compare_candidates()
        labels = {k: t for k, t, _c in cands}
        for key in [k for k, _t, checked in cands if checked]:
            try:
                data = self._load_other(key, ref.output)
            except (OSError, ValueError, Incompatible):
                continue
            if isinstance(data, Curves):
                out.append(Overlay(labels.get(key, key), data, measured=key.startswith("m")))
        res = self._resolution_overlay() if with_resolution else None
        if res is not None:
            out.append(res)
        return out

    def _resolution_overlay(self) -> Overlay | None:
        """El espectro que se ve, con la resolución del detector elegida en «Espectro»."""
        sp = self.spectro
        if sp.fwhm_function() is None or sp._curves is None or sp._curves is not self.curves_view.curves:
            return None
        c = sp.resolution_curves()
        return Overlay(f"con {sp.detector_name()}", c, resolution=True) if c is not None else None

    def _resolution_stale(self) -> bool:
        """¿La curva convolucionada que se dibuja (o su falta) no es la que toca con el panel «Espectro»?"""
        drawn = [o for o in self.curves_view.overlays if o.resolution]
        want = self._resolution_overlay()
        if want is None:
            return bool(drawn)
        return not drawn or drawn[0].label != want.label

    def _update_overlays(self) -> None:
        if self.views.currentWidget() is self.curves_view:
            self.curves_view.set_overlays(self._overlays_for(self.current))
        self._update_compare_stats()

    def _update_compare_stats(self) -> None:
        """χ² y diferencia media entre la curva que se analiza y cada una con la que se compara. Con datos medidos, en
        las posiciones de las medidas (cada medida es un punto; la simulación se interpola en ellas)."""
        from ...results.compare import chi2_test, paired

        cv = self.curves_view
        a = cv.active_series() if self.views.currentWidget() is cv else None
        if a is None or not cv.shown_overlays:
            self.compare.stats.setText("")
            return
        rows = []
        for o, so in cv.shown_overlays:
            b = next((x for x in so.series if x.name == a.name), so.series[0] if so.series else None)
            if b is None or o.resolution:
                continue
            _x, ya, ea, yb, eb = paired(a.x, a.y, a.err, b.x, b.y, b.err, at_b=o.measured)
            ok = np.isfinite(yb) & np.isfinite(ya)
            if ok.sum() < 3:
                continue
            both = ea is not None or eb is not None
            txt = f"<b>{o.label}</b>: "
            with np.errstate(divide="ignore", invalid="ignore"):
                rel = np.where(np.abs(ya[ok]) > 0, (yb[ok] - ya[ok]) / np.abs(ya[ok]), np.nan)
            if np.isfinite(rel).any():
                txt += f"diferencia media {100 * np.nanmean(rel):+.2g} % (máx. {100 * np.nanmax(np.abs(rel)):.2g} %)"
            if both:
                ch = chi2_test(ya[ok], None if ea is None else ea[ok], yb[ok], None if eb is None else eb[ok])
                if ch.ndf > 0:
                    pts = "punto" if ch.ndf == 1 else "puntos"
                    txt += f"; χ²/n = {ch.reduced:.3g} ({ch.ndf} {pts}, p = {ch.p_value:.2g})"
            rows.append(txt)
        self.compare.stats.setText("<br>".join(rows))

    def compute_gamma(self, crit: dict) -> None:
        """Índice gamma entre lo que se ve y lo primero marcado en «Comparar».

        Curva con curva (dosis en profundidad, perfiles, medidas), el perfil sacado de un mapa con una curva, o el
        plano que se ve de un mapa con el mismo plano de otro mapa del mismo tipo.
        """
        from .gamma_dialog import GammaDialog

        others = self.compare.checked()
        if not others or self.current is None or self.current_data is None:
            self.compare.g_result.setText("Marque en la lista con qué comparar.")
            return
        data = self.current_data
        labels = {k: t for k, t, _c in self._compare_candidates()}
        other_label = labels.get(others[0], others[0])
        ds = self.dataset(self.current.dataset)
        main_label = ds.label if ds else "principal"
        try:
            other = self._load_other(others[0], self.current.output)
            if isinstance(data, Grid) and isinstance(other, Grid) and self.views.currentWidget() is self.map_view:
                res, axes, names, ref = self._gamma_maps(data, other, crit)
                GammaDialog.show_2d(self, res, axes, names, ref)
                note = ""
            elif isinstance(other, Curves) or (isinstance(other, Grid) and self.views.currentWidget() is
                                                self.curves_view):
                if not isinstance(other, Curves):
                    raise ValueError("Para comparar un perfil, marque una curva (otra carpeta con ese perfil o "
                                     "medidas)")
                (rx, ry), (ex, ey), name, note = self._gamma_curves(data, other, measured=others[0].startswith("m"))
                ref_label = main_label
                if crit["reference"] == "other":
                    rx, ry, ex, ey = ex, ey, rx, ry
                    ref_label = other_label
                order = np.argsort(ex)
                res = gamma_index([rx], ry, [ex[order]], ey[order], dose=crit["dose"], dta=crit["dta"],
                                  threshold=crit["threshold"], local=crit["local"])
                GammaDialog.show_1d(self, res, rx, ry, name=name, ref_label=f"Referencia: {ref_label}")
            else:
                raise ValueError("Se comparan curvas con curvas (o con el perfil de un mapa) y mapas con mapas del "
                                 "mismo tipo.")
        except (OSError, ValueError, Incompatible) as exc:
            self.compare.g_result.setText(str(exc))
            return
        self.compare.g_result.setText(res.summary() + note)

    def _gamma_curves(self, data, other: Curves, measured: bool):
        """Las dos curvas del gamma en las unidades que se ven: ((x, y) principal, (x, y) otra, eje, nota)."""
        opts = self.units.options()
        opts.per_decay = self.units.per_decay
        if self.views.currentWidget() is self.curves_view and self.curves_view.shown is not None:
            a = self.curves_view.active_series()
            if a is None:
                raise ValueError("No hay ninguna curva visible")
            sc = self.curves_view.shown
            ax_, ay, a_name, x_unit, y_unit, name = a.x, a.y, a.name, sc.x_unit, sc.y_unit, sc.x_label
        elif isinstance(data, Grid) and self.map_view.profile is not None:
            p = self.map_view.profile
            ax_, ay, a_name, x_unit, y_unit = p.x, p.series[0].y, p.series[0].name, p.x_unit, p.y_unit
            name = f"{p.x_name} ({p.x_unit})"
        else:
            raise ValueError("Pulse en el mapa para sacar el perfil que quiere comparar")
        if x_unit != "cm":
            raise ValueError("El índice gamma compara distribuciones en función de la posición (cm)")
        if self.views.currentWidget() is self.curves_view:
            opts = self.curves_view.shown_opts  # las mismas unidades que la curva principal
        so = show_curves(other, opts, None)
        b = next((s for s in so.series if s.name == a_name), None) or next(iter(so.series), None)
        if b is None:
            raise ValueError("La otra curva está vacía")
        ax_, ay = np.asarray(ax_, dtype=np.float64), np.asarray(ay, dtype=np.float64)
        bx, by = np.asarray(b.x, dtype=np.float64), np.asarray(b.y, dtype=np.float64)
        oka, okb = np.isfinite(ay), np.isfinite(by)
        ax_, ay, bx, by = ax_[oka], ay[oka], bx[okb], by[okb]
        if len(ay) < 2 or len(by) < 2:
            raise ValueError("Hacen falta al menos dos puntos en cada curva")
        note = ""
        already = opts.norm.mode in ("max", "point")
        if not already and (measured or so.y_unit != y_unit):
            ay = 100.0 * ay / max(float(np.max(np.abs(ay))), 1e-300)
            by = 100.0 * by / max(float(np.max(np.abs(by))), 1e-300)
            note = " (cada curva, normalizada a su máximo)"
        return (ax_, ay), (bx, by), name, note

    def _gamma_maps(self, a: Grid, b: Grid, crit: dict):
        """γ entre dos mapas del mismo tipo: el plano que se ve (caja o TAC), el mapa R-Z (cilindro) o la dosis
        radial (esfera). Devuelve (resultado, ejes, nombres de los ejes, valores de la referencia)."""
        from ...results.profiles import layer

        if a.geometry != b.geometry or a.geometry == "angles":
            raise ValueError("Los dos mapas deben ser del mismo tipo (caja, cilindro, esfera o TAC)")
        mv = self.map_view
        name = mv.layer.currentData()
        if name == REL:
            name = None
        va = layer(a, name)[0]
        vb = layer(b, name if name in b.extra else None)[0]
        ca = [a.centers(i) for i in range(len(a.edges))]
        cb = [b.centers(i) for i in range(len(b.edges))]
        names = [f"{n} (cm)" for n in a.axes]
        if a.geometry in ("box", "ct"):
            k = "xyz".index(mv.axis.currentData())
            idx = min(mv.slider.value(), len(ca[k]) - 1)
            pos = ca[k][idx]
            kb = int(np.argmin(np.abs(cb[k] - pos)))
            if abs(cb[k][kb] - pos) > 0.5 * float(np.max(b.steps(k))) + 1e-9:
                raise ValueError(f"El otro mapa no llega a {'xyz'[k]} = {pos:.4g} cm")
            sla: list = [slice(None)] * 3
            slb: list = [slice(None)] * 3
            sla[k], slb[k] = idx, kb
            ra, rb = va[tuple(sla)], vb[tuple(slb)]
            ax_a = [c for i, c in enumerate(ca) if i != k]
            ax_b = [c for i, c in enumerate(cb) if i != k]
            names = [n for i, n in enumerate(names) if i != k]
        else:
            ra, rb, ax_a, ax_b = va, vb, ca, cb
        norm = self.units.options().norm
        if norm.mode in ("max", "point"):  # cada mapa respecto de su propio máximo
            ra = 100.0 * ra / max(float(np.nanmax(ra)), 1e-300)
            rb = 100.0 * rb / max(float(np.nanmax(rb)), 1e-300)
        if crit["reference"] == "other":
            ra, rb, ax_a, ax_b = rb, ra, ax_b, ax_a
        res = gamma_index(ax_a, ra, ax_b, rb, dose=crit["dose"], dta=crit["dta"], threshold=crit["threshold"],
                          local=crit["local"])
        return res, ax_a, names, ra

    # -- medidas ---------------------------------------------------------------------------------------------
    def _on_active_curve(self, s) -> None:
        sp = self.spectro
        if s is None or self.curves_view.shown is None:
            self.metrics.clear()
            sp.clear()
            if self._resolution_stale():
                QTimer.singleShot(0, self._update_overlays)  # la convolucionada que quedaba ya no es de nada
            return
        sc = self.curves_view.shown
        c = sc.curves
        # Las energías exactas de un espectro (intervalos de anchura cero) con su área, en las unidades de la curva.
        self.metrics.set_curve(s.x, s.y, s.err, s.edges, sc.x_unit, sc.y_unit, c.kind, c.x_name,
                               self.curves_view.title, discrete=c.discrete,
                               lines=[(ln.x, ln.p * s.scale) for ln in s.lines])
        changed = False
        if sp.applies_to(c):
            changed = sp._curves is not c or sp._series is not s.source
            sp.set_spectrum(c, s.source, self.curves_view.opts, self.curves_view.title, x_unit=sc.x_unit)
        else:
            sp.clear()
        if (changed and sp.fwhm_function() is not None) or self._resolution_stale():
            QTimer.singleShot(0, self._update_overlays)  # la convolucionada de la curva nueva (o ya ninguna)
        self._update_compare_stats()

    def _on_profile(self, c: Curves | None) -> None:
        if c is None:
            if self.views.currentWidget() is self.map_view:
                self.metrics.clear()
            return
        s = c.series[0]
        self.metrics.set_curve(c.x, s.y, s.err, c.edges, c.x_unit, c.y_unit, "profile", c.x_name, c.title)

    def _on_units(self, opts) -> None:
        opts.per_decay = self.units.per_decay
        self._refresh_geometry_overlay()
        w = self.views.currentWidget()
        if w is self.curves_view:
            self.curves_view.set_options(opts)
        elif w is self.map_view:
            self.map_view.set_options(opts)
        elif w is self.summary_view and isinstance(self.current_data, Summary) and self.current is not None:
            ds = self.dataset(self.current.dataset)
            self._show_summary(self.current_data, ds, self.current)  # la dosis de cada cuerpo, en las unidades
        elif hasattr(w, "set_options"):
            w.set_options(opts)

    # -- seguir la simulación ---------------------------------------------------------------------------------
    def poll(self, force: bool = False) -> None:
        changed_current = False
        changed_other = False
        rebuild = False
        compared = {k.split(":")[0] for k in self._compare_checked}
        for ds in self.datasets:
            before = self._signatures.get(ds.key)
            ds.folder.scan()
            sig = ds.folder.signature()
            if sig != before or force:
                self._signatures[ds.key] = sig
                rebuild = True
                if self.current is not None and self.current.dataset == ds.key:
                    changed_current = True
                elif ds.key in compared:
                    changed_other = True  # una carpeta con la que se compara
        if rebuild:
            self._rebuild_tree()
        retry = self._retry_ref is not None and self._retry_ref == self.current and self.loaded_token >= self._token
        if (changed_current or retry) and self.current is not None and self.current.output != CASCADE_KEY:
            # (la vista de la cascada sigue sola a la cascada; lo que escribe penhmain en la carpeta no la cambia)
            self._load(self.current, keep_view=True)  # al llegar, se dibuja con lo que se compara ya releído
        elif changed_other and self.current is not None:
            self._update_overlays()
        self._update_state()

    def _update_state(self) -> None:
        ds = self.primary()
        if ds is None:
            self.state_label.setText("")
            self.progress.setVisible(False)
            return
        f = ds.folder
        state = aggregate_state(f)
        text = f"{ds.label}: {state.text}"
        self.state_label.setText(text)
        if state.progress is not None and state.status in ("simulando", "parada", "terminada"):
            self.progress.setVisible(True)
            self.progress.setValue(int(1000 * min(max(state.progress, 0.0), 1.0)))
            self.progress.setFormat(f"{100 * state.progress:.1f} %")
        else:
            self.progress.setVisible(False)

    # -- exportar ----------------------------------------------------------------------------------------------
    def _current_canvas(self):
        w = self.views.currentWidget()
        return getattr(w, "plot", None)

    def export_figure(self) -> None:
        w = self.views.currentWidget()
        if hasattr(w, "export_image"):  # la cascada: lo que se ve en 3D
            w.export_image()
            return
        pc = self._current_canvas()
        if pc is None:
            QMessageBox.information(self, "ARGUS", "Esta vista no tiene figura.")
            return
        start = str(self.settings.value("resultados/exportar", "") or "")
        name = (self._title(self.current) if self.current else "figura").split(" · ")[0]
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name).strip() or "figura"
        path, flt = QFileDialog.getSaveFileName(self, "Exportar figura", str(Path(start) / f"{safe}.pdf"),
                                                "PDF (*.pdf);;SVG (*.svg);;PNG (*.png)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".svg" if "svg" in flt.lower() else ".png" if "png" in flt.lower() else ".pdf"
        self.settings.setValue("resultados/exportar", str(Path(path).parent))
        if Path(path).suffix.lower() not in (".pdf", ".svg", ".png"):
            QMessageBox.warning(self, "ARGUS", "La figura se guarda en PDF, SVG o PNG: ponga una de esas extensiones "
                                               f"(«{Path(path).name}»).")
            return
        try:
            pc.export(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "ARGUS", f"No se ha podido guardar: {exc}")
            return
        self.statusBar().showMessage(f"Figura guardada en {path}", 8000)

    def export_csv(self) -> None:
        w = self.views.currentWidget()
        if w is self.curves_view:
            cols, data = self.curves_view.table_rows()
        elif w is self.map_view:
            cols, data = self.map_view.plane_rows()
        elif hasattr(w, "table_rows"):
            cols, data = w.table_rows()
        else:
            cols, data = [], []
        if not cols:
            QMessageBox.information(self, "ARGUS", "No hay datos que exportar en esta vista.")
            return
        start = str(self.settings.value("resultados/exportar", "") or "")
        path, _ = QFileDialog.getSaveFileName(self, "Exportar datos", str(Path(start) / "datos.csv"),
                                              "CSV (*.csv)")
        if not path:
            return
        self.settings.setValue("resultados/exportar", str(Path(path).parent))
        try:
            write_csv(path, cols, data)
        except OSError as exc:
            QMessageBox.warning(self, "ARGUS", f"No se ha podido guardar: {exc}")
            return
        self.statusBar().showMessage(f"Datos guardados en {path}", 8000)

    def _toggle_geometry(self, on: bool) -> None:
        if on:
            self._refresh_geometry_overlay()
        else:
            self.showOnGeometry.emit(None)
            self.showTracksOnGeometry.emit(None)

    def geometry_overlay(self):
        """El mapa que se ve, preparado para dibujarlo sobre los cortes de la ventana principal (DoseOverlay)."""
        from ...render.dose_overlay import DoseOverlay
        from ...results.display import grid_label, grid_scale
        from ...results.profiles import layer
        from .map_view import _levels
        from .style import colormap

        data = self.current_data
        if not isinstance(data, Grid) or data.geometry not in ("box", "ct", "cylinder", "sphere"):
            return None
        mv = self.map_view
        name = mv.layer.currentData()
        name = name if name in data.extra else None
        v, _e, qty, unit = layer(data, name)
        vmax = float(np.nanmax(np.abs(v))) if v.size and np.isfinite(v).any() else 0.0
        opts = self.units.options()
        opts.per_decay = self.units.per_decay
        f, shown = grid_scale(unit, vmax, opts)
        lut = (np.asarray(colormap("calor")(np.linspace(0.0, 1.0, 256)))[:, :3] * 255.0).astype(np.uint8)
        levels = tuple(_levels(mv.iso_levels.text())) or (20.0, 50.0, 80.0, 95.0)
        return DoseOverlay(data, layer=name, lut=lut, levels=levels, isolines=mv.iso.isChecked(), unit_factor=f,
                           unit=shown, quantity=qty, label=grid_label(qty, shown, opts),
                           title=self._title(self.current) if self.current else data.title)

    def _refresh_geometry_overlay(self, *_args) -> None:
        """Vuelve a mandar el mapa (o las trayectorias) a la ventana principal, si se está viendo allí."""
        if not self.act_geo.isChecked():
            return
        # Lo que se veía antes en la ventana principal se quita (el mapa de dosis al pasar a la cascada, las
        # trayectorias al pasar a un mapa o al resumen…).
        if isinstance(self.current_data, CascadeData):
            from ...results.data import TRACKS_ON_GEOMETRY

            w = self.views.currentWidget()
            tracks = w.visible_tracks(TRACKS_ON_GEOMETRY) if hasattr(w, "visible_tracks") else []
            self.showOnGeometry.emit(None)
            self.showTracksOnGeometry.emit(tracks or None)
            return
        if isinstance(self.current_data, Tracks):
            w = self.views.currentWidget()
            tracks = w.visible_tracks() if hasattr(w, "visible_tracks") else [self.current_data]
            self.showOnGeometry.emit(None)
            self.showTracksOnGeometry.emit(tracks)
            return
        self.showTracksOnGeometry.emit(None)
        self.showOnGeometry.emit(self.geometry_overlay())  # None si lo que se ve no es un mapa

    def closeEvent(self, event) -> None:
        # La ventana se vuelve a usar (la ventana principal solo tiene una): se para el seguimiento, pero los hilos
        # de lectura siguen hasta que se cierra la aplicación.
        self.timer.stop()
        self._end_busy()
        if self.act_geo.isChecked():
            self.act_geo.setChecked(False)  # quita el mapa y las trayectorias de la ventana principal
        self.closed.emit()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.act_follow.isChecked() and not self.timer.isActive():
            self.timer.start()

    def shutdown(self) -> None:
        """Al cerrar ARGUS: las vistas con GPU la liberan (la ventana solo se oculta al cerrarla)."""
        for w in list(getattr(self, "_special_views", {}).values()):
            if hasattr(w, "shutdown"):
                w.shutdown()


def write_csv(path: str | Path, cols: list[str], data: list[np.ndarray]) -> None:
    """Columnas de números a CSV (con punto decimal y coma de separador; las columnas cortas se completan). En UTF-8
    con BOM, para que Excel (en Windows) lea bien los acentos y los símbolos de los títulos (µGy, cm², σ…)."""
    n = max(len(c) for c in data)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i in range(n):
            w.writerow([f"{col[i]:.8g}" if i < len(col) and np.isfinite(col[i]) else "" for col in data])


__all__ = ["Dataset", "Ref", "ResultsWindow", "write_csv"]
