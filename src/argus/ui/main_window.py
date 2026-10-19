"""Ventana principal de ARGUS."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDockWidget,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSlider,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.files import SaveError, backup_file, same_content, write_files, write_latin1
from ..core.numfmt import plural
from ..ct.importer import split_summary
from ..pengeom import edit as ge
from ..pengeom import placement as pl
from ..pengeom import surfaces as sf
from ..pengeom.duplicate import materialize_clone
from ..pengeom.edit import FACE_NAMES
from ..pengeom.extent import element_box, element_extent, locate_bound, locator_for, source_extent
from ..pengeom.identity import is_identity, renumber_map
from ..pengeom.newgeo import new_geometry
from ..pengeom.parinp import parinp_uses
from ..pengeom.parser import load_geometry
from ..pengeom.writer import write_geo_text
from ..penhan import sections as sc
from ..penhan import source_edit as se
from ..penhan.follow import follow_geometry, references_to
from ..penhan.materials import (
    MAXMAT,
    add_material,
    ct_tissues,
    distinct_isotopes,
    hadron_data_needed,
    material_description,
    material_recipe,
    materials_table,
    materials_txt,
    no_endf_reason,
    remove_last_material,
    set_material,
    set_recipe,
    write_materials_txt,
)
from ..penhan.quickscan import inputs_for_geometry, looks_like_input
from ..penhan.simfolder import in_name_problem
from ..penhan.template import new_input_document
from ..penhan.validate import validate_case
from ..render.colors import material_color
from ..render.raytrace import RayTracer
from ..render.slice2d import nice_length
from ..session import open_case, open_geometry, provisional_parinp_notes, session_from_document
from .add_body_dialog import AddBodyDialog
from .add_material_dialog import AddMaterialDialog
from .choose_input_dialog import GEOMETRY_ONLY, ChooseInputDialog
from .complex_geo.workspace import ComplexGeometryWorkspace
from .duplicate_dialog import DuplicateDialog
from .geo_controller import GeoController
from .geo_panel import GeometryEditPanel
from .glue_dialog import GlueDialog, glue_candidates
from .icons import ICON_STYLES, argus_icon, icon_style, set_icon_style
from .input_controller import InputController, input_geomfn
from .materials_panel import MaterialsPanel
from .new_geometry_dialog import NewGeometryDialog
from .panels import (
    GeometryTree,
    InputTextView,
    MessagesPanel,
    PropertiesPanel,
    SourceText,
    body_title,
)
from .screen_fit import fit_to_screen
from .settings import app_settings, cttrack_fixed, remember_sim_folder, results_folder_for, set_cttrack_fixed
from .sim_panel import SimulationPanel
from .slice_view import SliceViewWidget
from .source_panel import SourcePanel
from .spectrum_dialog import SpectrumDialog
from .spinbox import DecimalSpinBox
from .surface_dialog import SurfaceDialog
from .view3d import HIDDEN, OPAQUE, TRANSLUCENT, View3D
from .wheel_guard import install_shortcut_guard, install_wheel_guard
from .wrap_dialog import WrapDialog

OPEN_FILTER = ("Ficheros de ARGUS (*.in *.IN *.geo *.GEO);;Ficheros de entrada de penhmain (*.in *.IN);;"
               "Geometrías PENGEOM (*.geo *.GEO);;Todos los ficheros (*)")
IN_FILTER = "Ficheros de entrada de penhmain (*.in);;Todos los ficheros (*)"
GEO_FILTER = "Geometrías PENGEOM (*.geo *.GEO);;Todos los ficheros (*)"
GEOMFN_MAX = 20  # penhmain lee GEOMFN con A20
SB = QMessageBox.StandardButton
# La pregunta de guardar el .geo y el .in que lo sigue (van juntos).
PAIR_BUTTONS = {SB.Save: "Guardar los dos", SB.Discard: "No guardar ninguno"}


def _looks_like_geometry(path) -> bool:
    """¿Parece una geometría de PENGEOM? Una línea de 64 ceros (la que separa los elementos) en su principio."""
    try:
        with open(path, "rb") as f:
            head = f.read(16384)
    except OSError:
        return False
    return any(ln.rstrip(b" \t\r").startswith(b"0" * 64) for ln in head.split(b"\n")[:200])


def _same_path(a, b) -> bool:
    if a is None or b is None:
        return False
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (TypeError, ValueError):
        return False


class _Declined(ge.EditError):
    """El usuario no ha querido hacer el cambio de la geometría (no se avisa de nada más)."""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        install_wheel_guard()  # la rueda no cambia valores de campos que no se han pulsado
        self.setWindowTitle("ARGUS")
        self.setWindowIcon(argus_icon())
        fit_to_screen(self, 1400, 880)
        self.session = None  # GeometrySession
        self.input: InputController | None = None
        self.geo_ctrl: GeoController | None = None
        # Deshacer común al .in y al .geo: el último cambio, sea del fichero que sea. Cada entrada es
        # (pila, índice, grupo): los pasos de un mismo grupo (un cambio del .geo y el del .in que lo sigue)
        # se deshacen y rehacen juntos.
        self._timeline: list[tuple[object, int, int]] = []
        self._redo_list: list[tuple[object, int, int]] = []
        self._in_undo = False
        self._group = 0
        self._group_seq = 0
        self.last_follow_notes: list[str] = []
        self.material_colors: dict[int, str] = {}
        self.material_names: dict[int, str] = {}
        self.case_problem: str | None = None
        self.settings = app_settings()
        # Cajas exactas de los elementos (para pegar), de la geometría actual.
        self._extent_geo = None
        self._extent_cache: dict[int, tuple] = {}
        self._analytic_cache: dict[int, tuple] = {}
        self._parts_cache: dict[tuple, list] = {}
        self._source_cache: dict[int, tuple] = {}
        self._bound_cache: dict[int, tuple] = {}
        self._own_tracer = None
        self._mdrag: dict | None = None  # arrastre de un módulo en el corte

        self.view = SliceViewWidget(self)
        self.view3d = View3D(self, backend=os.environ.get("ARGUS_3D", "auto"))
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.view)
        self.splitter.addWidget(self.view3d)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.view_tab = QWidget()
        vt = QVBoxLayout(self.view_tab)
        vt.setContentsMargins(0, 0, 0, 0)
        vt.addWidget(self.splitter)
        self.central = QTabWidget(self)
        self.central.setDocumentMode(True)
        self.central.addTab(self.view_tab, "Geometría")
        self.setCentralWidget(self.central)
        self._preview_session = None  # vista previa de «Geometrías complejas» en las vistas (o None)
        self._dock_state = None
        self.view_mode = "2d"

        self.tree = GeometryTree()
        self.props = PropertiesPanel()
        self.source = SourceText()
        self.input_text = InputTextView()
        self.messages = MessagesPanel()
        self.materials = MaterialsPanel()
        self.source_panel = SourcePanel()
        self.sim_panel = SimulationPanel()
        self.geo_panel = GeometryEditPanel()
        area = Qt.DockWidgetArea
        self.dock_tree = self._dock("Geometría", self.tree, area.LeftDockWidgetArea)
        self.dock_mats = self._dock("Materiales", self.materials, area.LeftDockWidgetArea)
        self.dock_srcpanel = self._dock("Fuente", self.source_panel, area.RightDockWidgetArea)
        self.dock_sim = self._dock("Simulación", self.sim_panel, area.RightDockWidgetArea)
        self.dock_props = self._dock("Propiedades", self.props, area.RightDockWidgetArea)
        self.dock_edit = self._dock("Editar", self.geo_panel, area.RightDockWidgetArea)
        self.dock_source = self._dock("Texto .geo", self.source, area.RightDockWidgetArea)
        self.dock_input = self._dock("Texto .in", self.input_text, area.RightDockWidgetArea)
        self.dock_msgs = self._dock("Mensajes", self.messages, area.BottomDockWidgetArea)
        self.splitDockWidget(self.dock_tree, self.dock_mats, Qt.Orientation.Vertical)
        self.splitDockWidget(self.dock_srcpanel, self.dock_input, Qt.Orientation.Vertical)
        self.tabifyDockWidget(self.dock_srcpanel, self.dock_sim)
        self.tabifyDockWidget(self.dock_sim, self.dock_props)
        self.tabifyDockWidget(self.dock_props, self.dock_edit)
        self.tabifyDockWidget(self.dock_input, self.dock_source)
        self.dock_srcpanel.raise_()
        self.dock_input.raise_()
        self.resizeDocks([self.dock_tree, self.dock_srcpanel], [320, 460], Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_tree, self.dock_mats], [380, 300], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_srcpanel, self.dock_input], [560, 240], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_msgs], [110], Qt.Orientation.Vertical)

        # «Geometrías complejas»: el .geo con las pestañas de PenGeomJar, el texto y la geometría en vivo (ADR-019).
        self.complex = ComplexGeometryWorkspace(self)
        self.central.addTab(self.complex, "Geometrías complejas (PenGeomJar)")
        self.central.setTabToolTip(0, "La geometría en 2D y 3D con los paneles de ARGUS")
        self.central.setTabToolTip(1, "Construir la geometría bloque a bloque con las pestañas de PenGeomJar "
                                      "(Surface, Body, Module, Clone…), viendo el texto y la geometría a la vez")
        self.central.currentChanged.connect(self._on_central_tab)

        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_status()

        self.view.cursorMoved.connect(self._on_cursor)
        self.view.bodyPicked.connect(self._on_view_pick)
        self.view.viewChanged.connect(self._sync_depth_widgets)
        self.view.renderTime.connect(self._on_render_time_2d)
        self.view.sourceMoved.connect(self._on_source_dragged)
        self.view.directionChanged.connect(self._on_direction_dragged)
        self.view.boxResized.connect(self._on_box_dragged)
        self.view.dragCancelled.connect(self._on_drag_cancelled)
        self.view.gridChanged.connect(self._on_grid_dragged)
        self.view.moduleDragged.connect(self._on_module_dragged)
        self.view.contextRequested.connect(self._on_context_menu)
        self.view.viewChanged.connect(self._sync_slice_plane_3d)
        self.view3d.bodyPicked.connect(self._on_view_pick)
        self.view3d.cursorMoved.connect(self._on_cursor)
        self.view3d.renderInfo.connect(self._on_render_info_3d)
        self.view3d.contextRequested.connect(self._on_context_menu_3d)
        self.view3d.gridChanged.connect(self._on_grid_dragged)
        self.view3d.dragCancelled.connect(self._on_drag_cancelled)
        self.view3d.visibilityChanged.connect(lambda: self.materials.set_visibility(self.view3d.material_visibility))
        self.materials.visibilityChanged.connect(self.view3d.set_material_visibility)
        self.tree.bodySelected.connect(self.select_body)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)
        self.geo_panel.editRequested.connect(self.edit_geometry)
        self.geo_panel.addRequested.connect(lambda kb: self.add_body_dialog(kb or None))
        self.geo_panel.deleteRequested.connect(self.delete_body)
        self.geo_panel.glueRequested.connect(lambda kb: self.glue_module_dialog(kb))
        self.geo_panel.convertRequested.connect(self.convert_to_module)
        self.geo_panel.duplicateRequested.connect(self.duplicate_dialog)
        self.geo_panel.wrapRequested.connect(self.wrap_dialog)
        self.geo_panel.rotateRequested.connect(self.rotate_element)
        self.geo_panel.surfaceAddRequested.connect(self.add_surface_dialog)
        self.geo_panel.materializeRequested.connect(self.materialize_clone)
        self.geo_panel.selectRequested.connect(self.select_body)
        self.geo_panel.saveRequested.connect(self.save_geometry)
        self.messages.lineRequested.connect(self._goto_line)
        self.source_panel.newInputRequested.connect(self.new_input)
        self.source_panel.spectrumRequested.connect(self.edit_spectrum)
        # La fuente extensa y sus cuerpos activos: la caja exacta de cada cuerpo la da la ventana.
        self.source_panel.uncovered_fn = self.uncovered_bodies
        self.source_panel.fit_fn = self.fit_source_box
        self.source_panel.bodies_fn = self.set_source_bodies
        self.sim_panel.newInputRequested.connect(self.new_input)
        self.sim_panel.highlightChanged.connect(self.set_highlight)
        self.sim_panel.bodySelected.connect(lambda kb: self.select_body(kb) if kb else None)
        self.sim_panel.doseGridShow.connect(self.show_dose_grid)
        self.sim_panel.doseGridFit.connect(self.fit_dose_grid)
        self.sim_panel.editFailed.connect(lambda msg: self.statusBar().showMessage(f"No se ha cambiado: {msg}", 10000))
        self.materials.newInputRequested.connect(self.new_input)
        self.materials.writeTxtRequested.connect(self.write_materials_now)
        self.materials.colorsChanged.connect(self._on_colors_changed)
        self.materials.addMaterialRequested.connect(self.add_material_dialog)
        self.materials.removeMaterialRequested.connect(self.remove_material)
        self.materials.completeRequested.connect(self.complete_materials)
        self.materials.compositionRequested.connect(self.edit_material_dialog)
        self.materials.assignRequested.connect(self.assign_materials)
        self.materials.libraryRequested.connect(self.material_library)
        self.materials.database_fn = self._penhan_database
        # Durante los arrastres, el texto y los mensajes se actualizan con un pequeño retraso.
        self._slow_timer = QTimer(self)
        self._slow_timer.setSingleShot(True)
        self._slow_timer.setInterval(120)
        self._slow_timer.timeout.connect(self._refresh_slow)
        self.setAcceptDrops(True)
        geom = self.settings.value("window/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        self._warm_up_wsl()
        self._render_2d = self._render_3d = ""
        self.set_view_mode(str(self.settings.value("view/mode", "split")))
        self._update_actions()

    def _warm_up_wsl(self) -> None:
        """En Windows, con un penhmain o un penhmat de Linux elegidos, se pregunta ya (en otro hilo) si WSL funciona:
        la primera vez tarda unos segundos (hasta que arranca WSL) y, si no, «Ejecutar» o «Generar .mat» dejarían la
        ventana parada mientras."""
        import threading

        from ..penhan.programs import HOST, exe_info, wsl_status
        from .settings import penhan_setup

        if HOST != "windows":
            return
        try:
            setup = penhan_setup(self.settings)
            linux = any(p is not None and Path(p).is_file() and exe_info(p).system in ("linux", "script")
                        for p in (setup.penhmain, setup.penhmat))
        except OSError:
            return
        if linux:
            threading.Thread(target=wsl_status, name="argus-wsl", daemon=True).start()

    # -- construcción ------------------------------------------------------------
    def _dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName("dock_" + title)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                      | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                      | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _build_actions(self) -> None:
        st = self.style()
        icon = st.standardIcon
        SP = QStyle.StandardPixmap
        self.act_open = QAction(icon(SP.SP_DialogOpenButton), "&Abrir…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.setToolTip("Abrir un fichero de entrada (.in) o una geometría (.geo)")
        self.act_open.triggered.connect(self.open_dialog)
        self.act_open_geo = QAction("Abrir solo la geo&metría…", self)
        self.act_open_geo.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.act_open_geo.setToolTip("Abrir un .geo sin ningún .in, aunque en su carpeta haya alguno que lo use")
        self.act_open_geo.triggered.connect(self.open_geometry_dialog)
        self.act_new_geo = QAction("Nue&va geometría…", self)
        self.act_new_geo.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self.act_new_geo.setToolTip("Una geometría desde cero: vacía o con un primer cuerpo o módulo")
        self.act_new_geo.triggered.connect(lambda: self.new_geometry())
        self.act_import_ct = QAction("Importar un &TAC (DICOM)…", self)
        self.act_import_ct.setToolTip("Convierte una serie DICOM de TAC en una caja de vóxeles (CTBOX) de PENGEOM, "
                                      "con su fichero .ct y los materiales del .in")
        self.act_import_ct.triggered.connect(self.import_ct)
        self.act_prepare_sim = QAction("&Preparar la carpeta de simulación…", self)
        self.act_prepare_sim.setToolTip("Copia en una carpeta todo lo que necesita penhmain para este .in: la "
                                        "geometría, el .ct, los .mat, los ENDF y el ejecutable")
        self.act_prepare_sim.triggered.connect(self.prepare_simulation_folder)
        self.act_new_in = QAction(icon(SP.SP_FileIcon), "&Nuevo .in para esta geometría", self)
        self.act_new_in.setShortcut(QKeySequence.StandardKey.New)
        self.act_new_in.triggered.connect(self.new_input)
        self.act_save = QAction(icon(SP.SP_DialogSaveButton), "&Guardar .in", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.triggered.connect(self.save_all)
        self.act_save.setText("&Guardar")
        self.act_save.setToolTip("Guardar el .in y la geometría modificados (copias .bak de las versiones anteriores)")
        self.act_save_geo = QAction("Guar&dar la geometría (.geo)", self)
        self.act_save_geo.triggered.connect(self.save_geometry)
        self.act_save_geo_as = QAction("Guardar la geometría c&omo…", self)
        self.act_save_geo_as.triggered.connect(self.save_geometry_as)
        self.act_add_body = QAction("Añadir un cuerpo…", self)
        self.act_add_body.setShortcut(QKeySequence("Ctrl+B"))
        self.act_add_body.setToolTip("Esfera, cilindro, caja o lámina dentro del cuerpo seleccionado")
        self.act_add_body.triggered.connect(lambda: self.add_body_dialog(self.view.selected))
        self.act_add_detector = QAction("Añadir un detector…", self)
        self.act_add_detector.setToolTip("Un cuerpo con forma de cuadrado, rectángulo, círculo, triángulo, pentágono o "
                                         "hexágono (con su altura) y su detector de penhmain")
        self.act_add_detector.triggered.connect(lambda: self.add_detector_dialog(self.view.selected))
        self.act_add_cell = QAction("Añadir una celda de detectores…", self)
        self.act_add_cell.setToolTip("Una caja llena de detectores iguales en filas, columnas y capas")
        self.act_add_cell.triggered.connect(lambda: self.add_cell_dialog(self.view.selected))
        self.act_delete_body = QAction("Eliminar el cuerpo o módulo seleccionado…", self)
        self.act_delete_body.setShortcut(QKeySequence.StandardKey.Delete)
        self.act_delete_body.triggered.connect(lambda: self.view.selected and self.delete_body(self.view.selected))
        self.act_duplicate = QAction("Duplicar…", self)
        self.act_duplicate.setShortcut(QKeySequence("Ctrl+D"))
        self.act_duplicate.setToolTip("Copias o clones del cuerpo o módulo seleccionado")
        self.act_duplicate.triggered.connect(lambda: self.view.selected and self.duplicate_dialog(self.view.selected))
        self.act_wrap = QAction("Envolver en un módulo…", self)
        self.act_wrap.setToolTip("Un módulo nuevo alrededor del elemento seleccionado (y de otros a su lado)")
        self.act_wrap.triggered.connect(lambda: self.view.selected and self.wrap_dialog(self.view.selected))
        self.act_overlaps = QAction("Buscar solapes en la geometría", self)
        self.act_overlaps.setToolTip("Cuerpos que comparten volumen sin ser uno limitante del otro, e hijas que "
                                     "asoman fuera de su módulo (PENGEOM no lo comprueba)")
        self.act_overlaps.triggered.connect(self.check_overlaps_now)
        self.act_save_as = QAction("Guardar .in &como…", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.triggered.connect(self.save_input_as)
        self.act_reload = QAction(icon(SP.SP_BrowserReload), "&Recargar", self)
        self.act_reload.setShortcut(QKeySequence("F5"))
        self.act_reload.triggered.connect(self.reload)
        self.act_quit = QAction("&Salir", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)
        self.act_undo = QAction(icon(SP.SP_ArrowBack), "&Deshacer", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.triggered.connect(self.undo)
        self.act_redo = QAction(icon(SP.SP_ArrowForward), "&Rehacer", self)
        # Ctrl+Y y Ctrl+Mayús+Z en todos los sistemas, sin repetir: en Windows la tecla estándar ya es Ctrl+Y y
        # un atajo repetido en la misma acción es «ambiguo» para Qt (no hace nada).
        redo_keys: list[QKeySequence] = []
        for k in (*QKeySequence.keyBindings(QKeySequence.StandardKey.Redo), QKeySequence("Ctrl+Y"),
                  QKeySequence("Ctrl+Shift+Z")):
            if k.toString() and all(k.toString() != x.toString() for x in redo_keys):
                redo_keys.append(k)
        self.act_redo.setShortcuts(redo_keys)
        self.act_redo.triggered.connect(self.redo)
        self.act_fit = QAction("Ajustar vista", self)
        self.act_fit.setShortcut(QKeySequence("F"))
        self.act_fit.setToolTip("Encuadrar la geometría en las vistas (F)")
        self.act_fit.triggered.connect(self.fit_views)
        self.act_fit_ct = QAction("Encuadrar el TAC", self)
        self.act_fit_ct.setToolTip("Lleva el corte al centro del TAC (caja CT) y lo encuadra en 2D y 3D")
        self.act_fit_ct.triggered.connect(self.frame_ct)
        self.mode_group = QActionGroup(self)
        self.mode_actions = {}
        for mode, text, key, tip in (("2d", "2D", "F2", "Solo el corte 2D"),
                                     ("3d", "3D", "F3", "Solo la vista 3D"),
                                     ("split", "2D + 3D", "F4", "El corte 2D y la vista 3D, lado a lado")):
            a = QAction(text, self, checkable=True)
            a.setShortcut(QKeySequence(key))
            a.setToolTip(f"{tip} ({key})")
            a.triggered.connect(lambda checked=False, m=mode: self.set_view_mode(m))
            self.mode_group.addAction(a)
            self.mode_actions[mode] = a
        self.act_slice_in_3d = QAction("Plano del corte 2D en la vista 3D", self, checkable=True)
        self.act_slice_in_3d.setChecked(True)
        self.act_slice_in_3d.toggled.connect(self._toggle_slice_in_3d)
        self.act_complex = QAction("Geometrías complejas (formato PenGeomJar)", self, checkable=True)
        self.act_complex.setShortcut(QKeySequence("F6"))
        self.act_complex.setToolTip("La pestaña para construir la geometría bloque a bloque con las pestañas de "
                                    "PenGeomJar, con el texto y la geometría a la vista")
        self.act_complex.triggered.connect(lambda on: self.central.setCurrentIndex(1 if on else 0))
        self.act_cpu3d = QAction("Vista 3D sin GPU (trazado en la CPU)", self, checkable=True)
        self.act_cpu3d.setToolTip("Por si la tarjeta gráfica da problemas: más lento, mismo resultado")
        self.act_cpu3d.toggled.connect(lambda on: self.view3d.use_backend("cpu" if on else "auto"))
        self.act_focus_source = QAction("Ir a la fuente", self)
        self.act_focus_source.setShortcut(QKeySequence("G"))
        self.act_focus_source.setToolTip("Poner el corte a la altura de la fuente y encuadrarla")
        self.act_focus_source.triggered.connect(self._focus_source)
        self.plane_group = QActionGroup(self)
        self.plane_actions = {}
        for name, key in (("XY", "1"), ("XZ", "2"), ("YZ", "3")):
            a = QAction(f"Plano {name}", self, checkable=True)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda checked, n=name: self.set_plane(n))
            self.plane_group.addAction(a)
            self.plane_actions[name] = a
        self.plane_actions["XZ"].setChecked(True)
        self.color_group = QActionGroup(self)
        self.act_color_mat = QAction("Color por material", self, checkable=True)
        self.act_color_body = QAction("Color por cuerpo", self, checkable=True)
        self.act_color_mat.setChecked(True)
        for a, mode in ((self.act_color_mat, "material"), (self.act_color_body, "body")):
            self.color_group.addAction(a)
            a.triggered.connect(lambda checked, m=mode: self.set_color_mode(m))
        self.act_edges = QAction("Fronteras", self, checkable=True)
        self.act_edges.setChecked(True)
        self.act_edges.toggled.connect(self.view.set_draw_edges)
        self.act_edges.toggled.connect(self.view3d.set_draw_edges)
        self.act_show_source = QAction("Fuente", self, checkable=True)
        self.act_show_source.setChecked(True)
        self.act_show_source.setToolTip("Mostrar la fuente del .in en el corte y en 3D")
        self.act_show_source.toggled.connect(self.view.set_show_source)
        self.act_show_source.toggled.connect(self.view3d.set_show_source)
        self.act_show_grid = QAction("Rejilla de dosis", self, checkable=True)
        self.act_show_grid.setChecked(True)
        self.act_show_grid.toggled.connect(self.view.set_show_dose_grid)
        self.act_show_grid.toggled.connect(self.view3d.set_show_dose_grid)
        self.act_about = QAction("Acerca de ARGUS", self)
        self.act_about.triggered.connect(self.about)
        self.act_results_case = QAction("&Ver los resultados de este caso", self)
        self.act_results_case.setShortcut(QKeySequence("Ctrl+R"))
        self.act_results_case.setToolTip("Los resultados de penhmain de este .in (en su carpeta o en la última carpeta "
                                         "de simulación preparada para él), mientras simula y al terminar")
        self.act_results_case.triggered.connect(self.show_case_results)
        self.act_results_open = QAction("&Abrir los resultados de una carpeta…", self)
        self.act_results_open.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.act_results_open.setToolTip("Cualquier carpeta donde se haya simulado con penhmain (o con varias "
                                         "ejecuciones en subcarpetas)")
        self.act_results_open.triggered.connect(self.open_results_folder)
        self.act_outputs = QAction("&Elegir qué resultados pide el .in…", self)
        self.act_outputs.setToolTip("Dosis en 3D, espectros y fluencia en un cuerpo, espacio de fases, energía "
                                    "depositada, seguir la simulación…: lo que calcula penhmain")
        self.act_outputs.triggered.connect(self.choose_outputs)
        self.act_parallel = QAction("Simulaciones en para&lelo…", self)
        self.act_parallel.setToolTip("El mismo .in en varias carpetas con semillas independientes (como MultiRuns): "
                                     "ARGUS prepara las carpetas y las órdenes para lanzarlas, y sigue los resultados")
        self.act_parallel.triggered.connect(lambda: self.prepare_batch("paralelo"))
        self.act_campaign = QAction("&Campaña de simulaciones…", self)
        self.act_campaign.setToolTip("Variantes del caso cambiando uno o dos parámetros (energía, posición, un "
                                     "material, un PARINP…), para compararlas y ver una medida frente al parámetro")
        self.act_campaign.triggered.connect(lambda: self.prepare_batch("campana"))
        self.act_run = QAction(icon(SP.SP_MediaPlay), "E&jecutar penhmain…", self)
        self.act_run.setShortcut(QKeySequence("F9"))
        self.act_run.setIconText("Ejecutar")
        self.act_run.setToolTip("Ejecuta penhmain con este .in en su carpeta, en un terminal (F9): copia allí su "
                                "penhmain y lo que falte, abre los resultados y simula la cascada de partículas")
        self.act_run.triggered.connect(self.run_simulation)
        self.act_cascade = QAction("Ver la casca&da de partículas (3D)", self)
        self.act_cascade.setToolTip("Las trayectorias reales de las primeras lluvias del caso, simuladas con su "
                                    "penhmain, en 3D sobre la geometría y animadas")
        self.act_cascade.triggered.connect(self.show_cascade)
        self.act_complete_mats = QAction("&Generar los .mat y ENDF que faltan…", self)
        self.act_complete_mats.setToolTip("Con su penhmat, en la carpeta del .in")
        self.act_complete_mats.triggered.connect(self.complete_materials)
        # Las teclas sueltas (F, G, 1-3, F2-F4, Supr) no se disparan desde un campo, una lista, una tabla o un
        # texto de los paneles; en el corte, la vista 3D y el árbol de la geometría, sí.
        install_shortcut_guard(self, exempt=(self.tree,))

    def _build_toolbar(self) -> None:
        tb = QToolBar("Principal", self)
        tb.setObjectName("toolbar_main")
        tb.setMovable(True)
        self.addToolBar(tb)
        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addAction(self.act_reload)
        tb.addSeparator()
        # «Ejecutar», con su texto y al principio (es la acción principal: no puede quedar escondida al final de la
        # barra en una pantalla estrecha).
        tb.addAction(self.act_run)
        self._run_button = tb.widgetForAction(self.act_run)
        if isinstance(self._run_button, QToolButton):
            self._run_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self._run_button.setStyleSheet("QToolButton { font-weight: 600; }")
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        for a in self.mode_actions.values():
            tb.addAction(a)
        tb.addSeparator()
        for a in self.plane_actions.values():
            tb.addAction(a)
        tb.addAction(self.act_fit)
        tb.addAction(self.act_focus_source)
        tb.addSeparator()
        self.depth_label = QLabel("  z = ")
        tb.addWidget(self.depth_label)
        self.depth_spin = DecimalSpinBox()
        self.depth_spin.setDecimals(6)
        self.depth_spin.setRange(-1e9, 1e9)
        self.depth_spin.setKeyboardTracking(False)
        self.depth_spin.setSuffix(" cm")
        self.depth_spin.setMinimumWidth(130)
        self.depth_spin.valueChanged.connect(self._on_depth_spin)
        tb.addWidget(self.depth_spin)
        self.depth_slider = QSlider(Qt.Orientation.Horizontal)
        self.depth_slider.setRange(0, 1000)
        self.depth_slider.setMinimumWidth(160)
        self.depth_slider.valueChanged.connect(self._on_depth_slider)
        tb.addWidget(self.depth_slider)
        tb.addSeparator()
        tb.addAction(self.act_color_mat)
        tb.addAction(self.act_color_body)
        tb.addAction(self.act_edges)
        tb.addAction(self.act_show_source)
        self._syncing = False

    def _build_menus(self) -> None:
        m = self.menuBar().addMenu("&Archivo")
        m.addAction(self.act_open)
        m.addAction(self.act_open_geo)
        m.addAction(self.act_new_geo)
        m.addAction(self.act_import_ct)
        self.recent_menu = m.addMenu("Rec&ientes")
        self._update_recent_menu()
        m.addSeparator()
        m.addAction(self.act_new_in)
        m.addAction(self.act_save)
        m.addAction(self.act_save_as)
        m.addAction(self.act_save_geo)
        m.addAction(self.act_save_geo_as)
        m.addSeparator()
        m.addAction(self.act_prepare_sim)
        m.addSeparator()
        m.addAction(self.act_reload)
        m.addSeparator()
        m.addAction(self.act_quit)
        e = self.menuBar().addMenu("&Editar")
        e.addAction(self.act_undo)
        e.addAction(self.act_redo)
        e.addSeparator()
        e.addAction(self.act_add_body)
        e.addAction(self.act_add_detector)
        e.addAction(self.act_add_cell)
        e.addAction(self.act_duplicate)
        e.addAction(self.act_wrap)
        e.addAction(self.act_delete_body)
        e.addSeparator()
        e.addAction(self.act_overlaps)
        v = self.menuBar().addMenu("&Ver")
        for a in self.mode_actions.values():
            v.addAction(a)
        v.addSeparator()
        for a in self.plane_actions.values():
            v.addAction(a)
        v.addAction(self.act_fit)
        v.addAction(self.act_fit_ct)
        v.addAction(self.act_focus_source)
        v.addSeparator()
        v.addAction(self.act_color_mat)
        v.addAction(self.act_color_body)
        v.addAction(self.act_edges)
        v.addAction(self.act_show_source)
        v.addAction(self.act_show_grid)
        v.addAction(self.act_slice_in_3d)
        v.addAction(self.act_cpu3d)
        v.addSeparator()
        v.addAction(self.act_complex)
        v.addSeparator()
        for d in (self.dock_tree, self.dock_mats, self.dock_srcpanel, self.dock_props, self.dock_edit,
                  self.dock_source, self.dock_input, self.dock_msgs):
            v.addAction(d.toggleViewAction())
        sim = self.menuBar().addMenu("&Simulación")
        sim.addAction(self.act_complete_mats)
        sim.addAction(self.act_outputs)
        sim.addSeparator()
        sim.addAction(self.act_run)
        sim.addAction(self.act_cascade)
        sim.addAction(self.act_results_case)
        sim.addSeparator()
        sim.addAction(self.act_prepare_sim)
        sim.addAction(self.act_parallel)
        sim.addAction(self.act_campaign)
        sim.setToolTipsVisible(True)
        self.sim_menu = sim
        r = self.menuBar().addMenu("&Resultados")
        r.addAction(self.act_outputs)
        r.addSeparator()
        r.addAction(self.act_results_case)
        r.addAction(self.act_results_open)
        r.addSeparator()
        r.addAction(self.act_parallel)
        r.addAction(self.act_campaign)
        r.setToolTipsVisible(True)
        self.results_menu = r
        o = self.menuBar().addMenu("&Opciones")
        self.act_cttrack = QAction("Mi penhmain tiene corregido CTTRACK (DEE = EORIG − E)", self)
        self.act_cttrack.setCheckable(True)
        self.act_cttrack.setChecked(cttrack_fixed(self.settings))
        self.act_cttrack.setToolTip("Márquelo solo si ha cambiado en penhmain.f (CTTRACK, líneas 11082–11084) "
                                    "«DEE=MIN(SSOFT*DSEF,EORIG)» y «E=MAX(EORIG-DEE,2.0D-16)» por «DEE=EORIG-E» y lo "
                                    "ha vuelto a compilar: entonces no hace falta partir los tejidos de un TAC por "
                                    "densidad y ARGUS deja de avisar.")
        self.act_cttrack.toggled.connect(self._on_cttrack)
        o.addAction(self.act_cttrack)
        self.act_penhan = QAction("Programas de penhan (penhmat y penhmain)…", self)
        self.act_penhan.setToolTip("Dónde están su carpeta de penhan, penhmat y penhmain (de Windows, Linux o macOS): "
                                   "con penhmat ARGUS genera los .mat que faltan y copia los ficheros ENDF")
        self.act_penhan.triggered.connect(self.penhan_programs)
        o.addAction(self.act_penhan)
        self.act_library = QAction("Materiales personalizados…", self)
        self.act_library.setToolTip("Sus materiales de composición propia (aleaciones, plásticos…), guardados para "
                                    "elegirlos en cualquier caso: editarlos, copiarlos, exportarlos e importarlos")
        self.act_library.triggered.connect(self.material_library)
        o.addAction(self.act_library)
        o.setToolTipsVisible(True)
        self.sim_menu.addSeparator()
        self.sim_menu.addAction(self.act_penhan)
        h = self.menuBar().addMenu("A&yuda")
        h.addAction(self.act_about)
        im = h.addMenu("Icono de ARGUS")
        self.icon_group = QActionGroup(self)
        for key, text in ICON_STYLES.items():
            a = im.addAction(text)
            a.setCheckable(True)
            a.setChecked(key == icon_style())
            a.setIcon(argus_icon(key))
            a.triggered.connect(lambda checked=False, k=key: self.set_app_icon(k))
            self.icon_group.addAction(a)

    def _build_status(self) -> None:
        sb = self.statusBar()
        self.coord_label = QLabel("")
        self.body_label = QLabel("")
        self.render_label = QLabel("")
        sb.addWidget(self.coord_label, 2)
        sb.addWidget(self.body_label, 2)
        sb.addPermanentWidget(self.render_label)

    def _update_actions(self) -> None:
        has_geo = self.session is not None and self.session.ok
        has_in = self.input is not None
        has_edit = self.geo_ctrl is not None
        can_edit = self._can_edit()
        self.act_new_in.setEnabled(has_geo)
        self.act_save.setEnabled(has_in or (has_edit and self.geo_ctrl.dirty))
        self.act_save_as.setEnabled(has_in)
        self.act_save_geo.setEnabled(has_edit and self.geo_ctrl.dirty)
        self.act_save_geo_as.setEnabled(has_edit)
        self.act_add_body.setEnabled(can_edit)
        self.act_add_detector.setEnabled(can_edit)
        self.act_add_cell.setEnabled(can_edit)
        self.act_delete_body.setEnabled(can_edit and bool(self.view.selected))
        self.act_duplicate.setEnabled(can_edit and bool(self.view.selected))
        self.act_wrap.setEnabled(can_edit and bool(self.view.selected))
        self.act_overlaps.setEnabled(has_geo)
        self.act_prepare_sim.setEnabled(has_in)
        self.act_results_case.setEnabled(has_in and self.input.path is not None)
        self.act_outputs.setEnabled(has_in)
        self.act_run.setEnabled(has_in)
        self.act_cascade.setEnabled(has_in and self.input.path is not None)
        self.act_complete_mats.setEnabled(has_in)
        self.act_parallel.setEnabled(has_in)
        self.act_campaign.setEnabled(has_in)
        self.act_fit_ct.setEnabled(has_geo and self.session.geo.ct is not None)
        self.act_import_ct.setEnabled(self.geo_ctrl is None or (has_geo and self.session.geo.ct is None))
        self.act_reload.setEnabled(self.session is not None or has_in)
        if self._in_undo:  # a mitad de deshacer/rehacer: se actualiza al terminar
            return
        u = self._undo_text()
        r = self._redo_text()
        self.act_undo.setEnabled(u is not None)
        self.act_redo.setEnabled(r is not None)
        self.act_undo.setText("&Deshacer " + u if u is not None else "&Deshacer")
        self.act_redo.setText("&Rehacer " + r if r is not None else "&Rehacer")
        src_ok = has_in and not self.input.doc.model.source.from_psf
        self.act_focus_source.setEnabled(has_geo and src_ok)

    # -- ficheros ----------------------------------------------------------------
    def open_dialog(self) -> None:
        start = self.settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Abrir", start, OPEN_FILTER)
        if path:
            self.open_file(path)

    def open_geometry_dialog(self) -> None:
        """Abrir un .geo sin .in (aunque en su carpeta haya alguno que lo use)."""
        start = self.settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Abrir solo la geometría", start, GEO_FILTER)
        if path:
            self.open_file(path, with_input=False)

    def open_file(self, path: str | os.PathLike, with_input: bool = True, ask: bool = True) -> None:
        """Abre un .in (con su geometría) o una geometría.

        Con una geometría se buscan en su carpeta los .in cuyo GEOMFN es ella
        (penhmain la abriría con ellos): si hay uno, se abre con él; si hay
        varios, se pregunta cuál (o solo la geometría). ``with_input=False``:
        solo la geometría.
        """
        if ask and not self.maybe_save():
            return
        path = str(path)
        is_input = Path(path).suffix.lower() == ".in"
        input_path: Path | None = None
        note = ""
        if not is_input and with_input:
            found = inputs_for_geometry(path)
            if len(found) == 1:
                input_path = found[0].path
                note = (f"Abierta con {input_path.name}, el .in de la carpeta que usa esta geometría "
                        "(para verla sin él: Archivo → Abrir solo la geometría…).")
            elif len(found) > 1:
                choice = self._choose_input(Path(path), found)
                if choice is None:
                    return
                if choice != GEOMETRY_ONLY:
                    input_path = Path(choice)
                    note = f"Abierta con {input_path.name}."
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if is_input or input_path is not None:
                case = open_case(path) if is_input else open_case(input_path, geometry=path)
                session, doc, problem = case.session, case.input_doc, case.problem
                ctrl = InputController(doc, str(case.input_path), stamp=case.input_stamp)
            else:
                session = open_geometry(path, search_input=False)
                problem = None
                ctrl = None
        except Exception as exc:  # pragma: no cover - errores de E/S
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "ARGUS", f"No se puede abrir {path}:\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        self.settings.setValue("last_dir", str(Path(path).parent))
        self._add_recent(path)
        self.set_case(session, ctrl, problem)
        if note:
            self.statusBar().showMessage(note, 10000)

    def _choice_key(self, geo_path: Path) -> str:
        return "inputs/" + os.path.normcase(os.path.abspath(str(geo_path))).replace("\\", "/")

    def _choose_input(self, geo_path: Path, found: list) -> Path | str | None:
        """Pregunta con qué .in abrir la geometría: su ruta, GEOMETRY_ONLY o None (cancelado).

        Se propone lo que se eligió la vez anterior para esta geometría.
        """
        key = self._choice_key(geo_path)
        raw = self.settings.value(key)  # nombre del .in elegido, o «*» si se abrió sola
        last = GEOMETRY_ONLY if raw == "*" else (raw if isinstance(raw, str) and raw else None)
        try:
            used = list(parinp_uses(load_geometry(geo_path)))
        except Exception:  # noqa: BLE001 - la geometría se leerá (y se dirá qué le pasa) al abrirla
            used = []
        dlg = ChooseInputDialog(self, geo_path, found, last=last, parinp_used=used)
        if not self._exec_dialog(dlg):
            return None
        choice = dlg.choice()
        if choice is not None:
            self.settings.setValue(key, choice.name if isinstance(choice, Path) else "*")
        return choice

    def reload(self) -> None:
        if self.input is not None and self.input.path is not None:
            self.open_file(self.input.path)
        elif self.session is not None:
            self.open_file(self.session.path, with_input=False)

    def set_case(self, session, ctrl: InputController | None, problem: str | None = None,
                 keep_geometry: bool = False) -> None:
        """Muestra una geometría y (opcionalmente) su fichero de entrada.

        ``keep_geometry``: la geometría es la que ya está abierta (solo cambia el
        .in); se conservan sus cambios sin guardar y su deshacer.
        """
        self._mdrag = None  # un arrastre del fichero anterior no sigue en el nuevo
        self.view.abort_drag()
        last = self._last_dialog
        try:
            if last is not None and not last.isVisible():
                self._release_dialog()  # ya cerrado: tiene la geometría y el .in de antes
        except RuntimeError:  # ya destruido
            self._last_dialog = None
        old_stack = self.input.stack if self.input is not None else None
        same_input = ctrl is not None and ctrl is self.input  # se vuelve a abrir solo la geometría
        if self.input is not None and not same_input:
            self.input.changed.disconnect(self._on_input_changed)
            self.input.dirtyChanged.disconnect(self._update_title)
            self.input.stack.indexChanged.disconnect(self._on_stack_index)
            self.input.deleteLater()
        self.input = ctrl
        self.case_problem = problem
        if ctrl is not None and not same_input:
            ctrl.setParent(self)
            ctrl.changed.connect(self._on_input_changed)
            ctrl.dirtyChanged.connect(self._update_title)
            ctrl.stack.indexChanged.connect(self._on_stack_index)
        self.session = session
        ok = session is not None and session.ok
        keep_geometry = keep_geometry and self.geo_ctrl is not None and self.geo_ctrl.session is session
        if not keep_geometry:
            self._set_geo_controller(GeoController(session, self) if ok else None)
        kept = [ctrl.stack] if ctrl is not None else []
        if keep_geometry:
            kept.append(self.geo_ctrl.stack)
        timeline = self._timeline
        if same_input and not keep_geometry:
            # Se conserva el .in y la geometría se vuelve a abrir: sus pasos que siguieron a pasos de la geometría
            # (que ya no están) no se pueden deshacer, ni los anteriores.
            groups = {g for st, _i, g in timeline if st is ctrl.stack and g}
            cut = max((i for i, e in enumerate(timeline) if e[2] in groups), default=-1)
            timeline = timeline[cut + 1:]
        if keep_geometry and old_stack is not None and (ctrl is None or ctrl.stack is not old_stack):
            # Los pasos de la geometría agrupados con cambios del .in que se cierra (renumeraciones)
            # ya no se pueden deshacer: ese .in no seguiría a la geometría y quedaría nombrando otros
            # cuerpos. Tampoco los anteriores (se deshace en orden).
            groups = {g for st, _i, g in timeline if st is old_stack and g}
            cut = max((i for i, e in enumerate(timeline) if e[2] in groups), default=-1)
            timeline = timeline[cut + 1:]
        self._timeline = [e for e in timeline if any(e[0] is k for k in kept)]
        self._redo_list = []
        self.material_colors = self._load_colors(session)
        self.view.material_colors = dict(self.material_colors)
        self._push_source_to_view()
        self.view3d.material_colors = dict(self.material_colors)
        self.view3d.set_session(session)
        # El corte se encuadra en lo que tiene material (no en la esfera de vacío que suele rodearlo).
        self.view.fit_box = (self.view3d.scene_lo, self.view3d.scene_hi) if ok else None
        self.view.set_session(session)
        self._sync_slice_plane_3d()
        if session is None:
            self.view.set_status_text(problem or "Sin geometría.")
        self._update_material_names()
        self.tree.set_geometry(session.geo if ok else None, self.view.color_mode, self.material_colors)
        self.materials.set_context(ctrl, session, self.material_colors, defaults=self._default_colors(session))
        if self.materials.visibility:  # p. ej. el aire de un CT, oculto en 3D
            self.view3d.set_material_visibility(self.materials.visibility)
        self.props.show_body(session, None)
        self.source.show_document(session.doc if session is not None else None)
        self.source_panel.set_context(ctrl, session)
        self.sim_panel.set_context(ctrl, session)
        self._outputs_context(ctrl, session)
        self.input_text.show_input(ctrl)
        if ctrl is not None:
            self.dock_srcpanel.raise_()
            self.dock_input.raise_()
            if ok:
                self.view.focus_source()
                self._sync_slice_plane_3d()
        self._refresh_messages()
        self._update_title()
        self._update_actions()
        self._sync_depth_widgets()
        self._preview_session = None
        if keep_geometry:
            # La misma geometría (solo cambia el .in: «Nuevo .in», recargar el .in…): lo escrito sin aplicar en las
            # pestañas de «Geometrías complejas» se queda (revisión de 0.16.2: se perdía sin preguntar).
            self.complex.refresh()
        else:
            self.complex.reset()

    def _source_read(self) -> bool:
        """¿Hay un .in y penhmain lee su fuente? (si se para antes o en la fuente, lo que hay son valores por defecto:
        ni se dibujan ni se editan; 0.16.3)."""
        ctrl = self.input
        return ctrl is not None and (ctrl.doc.model.ok or ctrl.doc.model.source_read)

    def _source_unread_message(self) -> bool:
        """Si la fuente no se puede leer, lo dice y devuelve True (quien llama no cambia nada)."""
        if self.input is None or self._source_read():
            return False
        self.statusBar().showMessage("No se ha cambiado la fuente: penhmain no puede leer la fuente del .in (vea el "
                                     "panel de mensajes); corríjala en el texto.", 10000)
        return True

    def _push_source_to_view(self) -> None:
        ctrl = self.input
        if ctrl is None or not self._source_read():
            self.view.set_source(None)
            self.view3d.set_source(None)
            return
        geo = self.session.geo if self.session is not None and self.session.ok else None
        m = ctrl.doc.model
        active = se.active_bodies(ctrl.doc, geo)
        grids, grid_kw = m.grids, {}
        dumped = self._resume_dose_grid()
        if dumped is not None:
            # penhmain seguirá con la del volcado (RESUME): esa se dibuja, sin asas (no se cambia desde el .in).
            grids = dumped
            grid_kw = {"grid_editable": False, "grid_label": "rejilla del volcado (RESUME), no la del .in"}
        self.view.set_source(m.source, active, grids, editable=not m.source.from_psf, **grid_kw)
        self.view3d.set_source(m.source, active, grids, editable=not m.source.from_psf, **grid_kw)

    def _resume_dose_grid(self) -> dict | None:
        """Con RESUME de un volcado que penhmain usará, la rejilla de dosis del volcado (como en ``doc.model.grids``)
        si no es la del .in: penhmain toma la del volcado (undécima revisión: las vistas dibujaban y dejaban estirar
        la del .in). None si no hay RESUME, el volcado no se usará o la rejilla es la misma."""
        from ..penhan import resume as rs
        from ..penhan.dumpfile import tally_diffs

        ctrl = self.input
        if ctrl is None or ctrl.folder is None:
            return None
        out = rs.resume_outcome(ctrl.doc, ctrl.folder)  # lo mismo que los mensajes (duodécima revisión)
        if out is None or not out.follows or out.info.tallies is None:
            return None
        info = out.info
        if not tally_diffs(info, ctrl.doc, ("grid",)):
            return None
        g = info.tallies.grid
        out: dict = {}
        if g is not None:
            for k in ("x", "y", "z"):
                v = getattr(g, k)
                if v is not None:
                    out["GRID" + k.upper()] = [float(v[0]), float(v[1]), int(v[2])]
            if g.r is not None:
                out["GRIDR"] = [float(g.r[0]), int(g.r[1])]
        return out

    def _on_input_changed(self) -> None:
        self._sync_parinp()  # si han cambiado los PARINP, se recalcula la geometría
        self._push_source_to_view()
        self.source_panel.refresh()
        dragging = self.view.dragging or self.view3d.dragging
        if not dragging:
            self.materials.refresh()
            self._update_material_names()
            self.sim_panel.refresh()
        self._update_title()
        self._update_actions()
        if dragging:
            self._slow_timer.start()  # arrastrando: texto y mensajes, con retraso
        else:
            self._refresh_slow()

    def _on_stack_index(self, *args) -> None:
        stack = self.sender()
        if not self._in_undo and stack is not None:
            self._timeline.append((stack, stack.index(), self._group))
            self._redo_list.clear()
        self._update_actions()

    # -- deshacer común (.in y .geo) -------------------------------------------------------
    def _stacks(self) -> list:
        out = []
        if self.input is not None:
            out.append(self.input.stack)
        if self.geo_ctrl is not None:
            out.append(self.geo_ctrl.stack)
        return out

    def _undo_target(self):
        stacks = self._stacks()
        while self._timeline:
            st, idx, _g = self._timeline[-1]
            if st in stacks and st.index() == idx and st.canUndo():
                return st
            self._timeline.pop()
        return None

    def _undo_text(self) -> str | None:
        """Texto del paso que se desharía (el primero del grupo: el cambio que pidió el usuario)."""
        if self._undo_target() is None:
            return None
        st, idx, g = self._timeline[-1]
        if g:
            for e in self._timeline:
                if e[2] == g:
                    st, idx = e[0], e[1]
                    break
        return st.text(idx - 1) if idx > 0 else st.undoText()

    def _redo_text(self) -> str | None:
        if not self._redo_list:
            return None
        st = self._redo_list[-1][0]
        return st.redoText() if st in self._stacks() and st.canRedo() else None

    def undo(self) -> None:
        if not self._settle():
            return
        st = self._undo_target()
        if st is None:
            return
        text = self._undo_text()
        self._in_undo = True
        try:
            while st is not None:
                _, idx, g = self._timeline.pop()
                st.undo()
                self._redo_list.append((st, idx, g))
                st = self._undo_target() if g else None
                if st is not None and self._timeline[-1][2] != g:
                    st = None
        finally:
            self._in_undo = False
        self._update_actions()
        self._show_step("Deshecho", text)

    def redo(self) -> None:
        if not self._settle():
            return
        if not self._redo_list:
            return
        text = self._redo_text()
        self._in_undo = True
        try:
            while self._redo_list:
                st, idx, g = self._redo_list.pop()
                if st not in self._stacks() or not st.canRedo():
                    self._redo_list.clear()
                    break
                st.redo()
                self._timeline.append((st, st.index(), g))
                if not g or not self._redo_list or self._redo_list[-1][2] != g:
                    break
        finally:
            self._in_undo = False
        self._update_actions()
        self._show_step("Rehecho", text)

    def _show_step(self, what: str, text: str | None) -> None:
        """La barra de estado dice qué se ha deshecho o rehecho (no se queda el mensaje del cambio anterior)."""
        self.statusBar().showMessage(f"{what}: «{text}»." if text else f"{what}.", 8000)

    def _refresh_slow(self) -> None:
        self.input_text.show_input(self.input)
        self._refresh_messages()

    def _refresh_messages(self) -> None:
        diags = []
        info = []
        s = self.session
        if s is not None:
            diags.extend(s.diagnostics)
            if s.ok:
                g = s.geo
                info.append(f"{s.name}: {g.nsurf} superficies, {g.nbodys} cuerpos y módulos, "
                            f"materiales {', '.join(map(str, g.materials())) or '—'}.")
        if self.case_problem:
            info.append(self.case_problem)
        ctrl = self.input
        if ctrl is not None:
            doc = ctrl.doc
            diags.extend(doc.diagnostics)
            folder = ctrl.folder or (s.path.parent if s is not None else None)
            geo = s.geo if s is not None and s.ok else None
            if folder is not None or geo is not None:
                diags.extend(validate_case(doc, geo, folder, cttrack_fixed=cttrack_fixed(self.settings)))
            msg = f"Fichero de entrada: {ctrl.name}"
            if doc.model.geomfn:
                msg += f" (GEOMFN {doc.model.geomfn}"
                if doc.model.parinp:
                    msg += ", PARINP " + ", ".join(f"{k}={v:g}" for k, v in sorted(doc.model.parinp.items()))
                msg += ")"
            info.append(msg + ".")
        elif s is not None and s.ok:
            info.append("Geometría sin .in: para definir la fuente, Archivo → Nuevo .in para esta geometría.")
        self.messages.set_messages(diags, info)
        self.case_errors = [d for d in diags if d.severity == 2]  # (lo nuevo lo dice también «Geometrías complejas»)
        self.source_panel.set_diagnostics(diags)
        self.sim_panel.set_diagnostics(diags)
        if any(d.severity == 2 for d in diags):
            self.dock_msgs.raise_()

    def _update_title(self, *args) -> None:
        parts = []
        if self.input is not None:
            parts.append(self.input.name + ("*" if self.input.dirty else ""))
        if self.session is not None:
            parts.append(self.session.name + ("*" if self.geo_ctrl is not None and self.geo_ctrl.dirty else ""))
        self.setWindowTitle("ARGUS — " + " · ".join(parts) if parts else "ARGUS")

    # -- guardar: el .geo y el .in que lo sigue van juntos (ADR-008 §4) -------------------------
    def maybe_save(self) -> bool:
        """Pregunta si hay cambios sin guardar en el .in o en el .geo; False si el usuario cancela.

        Si el .in tiene cambios que siguen a los de la geometría (cuerpos renumerados, materiales de un TAC),
        una sola pregunta: los dos se guardan o se descartan juntos (si no, el .in guardado nombraría otros
        cuerpos). Con cambios independientes, una pregunta para cada fichero.
        """
        if not self._settle():  # (se sigue escribiendo a mano: PENGEOM no aceptaría el texto)
            return False
        if not self.complex.confirm_discard("cerrar este caso"):
            return False
        if self._linked_changes():
            r = self._ask(self._pair_text() + "\n\n¿Guardar los dos?", SB.Save | SB.Discard | SB.Cancel, SB.Save,
                          PAIR_BUTTONS)
            if r == SB.Save:  # (uno sin cambios ya está guardado así)
                return self._save_files(geo=self.geo_ctrl.dirty, inp=self.input.dirty)
            return r == SB.Discard
        return self._maybe_save_geometry() and self._maybe_save_input()

    def _ask(self, text: str, buttons, default, labels: dict | None = None):
        """``QMessageBox.question`` con textos propios en algunos botones (``labels``: botón → texto).

        Los textos se ponen en cuanto se muestra la pregunta: así sigue siendo la pregunta estándar de Qt (con
        sus botones traducidos y la misma respuesta), la que sustituyen las pruebas.
        """
        if labels:
            def relabel() -> None:
                box = QApplication.activeModalWidget()
                if isinstance(box, QMessageBox) and box.text() == text:
                    for button, label in labels.items():
                        b = box.button(button)
                        if b is not None:
                            b.setText(label)

            QTimer.singleShot(0, relabel)
        return QMessageBox.question(self, "ARGUS", text, buttons, default)

    def _pair_text(self) -> str:
        return (f"El .in «{self.input.name}» se ha actualizado para seguir los cambios de la geometría "
                f"«{self.geo_ctrl.name}» (números y etiquetas de los cuerpos, materiales): si se guardara solo uno de "
                "los dos, el .in guardado nombraría otros cuerpos.")

    def _confirm_pair(self, question: str) -> bool:
        """«Guardar los dos» o «Cancelar»."""
        r = self._ask(self._pair_text() + "\n\n" + question, SB.Save | SB.Cancel, SB.Save,
                      {SB.Save: PAIR_BUTTONS[SB.Save]})
        return r == SB.Save

    def _linked_changes(self) -> bool:
        """¿Guardar (o descartar) solo la geometría o solo el .in dejaría en el disco un .in que nombra otros
        cuerpos? Entonces se guardan juntos."""
        gc, ic = self.geo_ctrl, self.input
        if gc is None or ic is None or not (gc.dirty or ic.dirty) or self._pair_mismatch(True, True):
            return False  # (si ni guardando los dos quedarían bien, véase _maybe_save_input)
        return self._pair_mismatch(True, False) or self._pair_mismatch(False, True)

    def _pair_mismatch(self, save_geo: bool, save_in: bool, in_path: Path | None = None) -> bool:
        """¿Tras guardar la geometría (``save_geo``) y/o el .in (``save_in``, en ``in_path``), el .in guardado
        nombraría otros cuerpos de la geometría guardada que nombra su GEOMFN?

        Los pasos de la geometría y los del .in que los siguen llevan el mismo número de grupo (``link``, véase
        ``_link_pair``): un .in y su geometría guardados están de acuerdo si tienen los mismos grupos de este .in.
        """
        gc, ic = self.geo_ctrl, self.input
        if gc is None or ic is None:
            return False
        groups = ic.links(applied=False) | ic.saved_links
        if not groups:
            return False
        if save_in:
            target = Path(in_path) if in_path is not None else ic.path
            if target is None:
                return False
            links, geomfn, folder = ic.links(), input_geomfn(ic.doc), target.parent
        else:
            if ic.path is None or ic.is_new:
                return False  # no hay .in guardado
            links, geomfn, folder = ic.saved_links, ic.saved_geomfn, ic.path.parent
        if not geomfn:
            return False
        geo_file = folder / geomfn
        if save_geo and _same_path(geo_file, gc.path):
            geo_links = gc.links()
        else:
            geo_links = gc.disk_links_of(geo_file)
            if geo_links is None:
                return False  # el .in nombra otro fichero de geometría
        return (set(links) & groups) != (set(geo_links) & groups)

    def _link_pair(self) -> None:
        """El último paso del .in sigue al último de la geometría (grupo ``_group``): se guardarán juntos."""
        if self._group and self.input is not None and self.geo_ctrl is not None:
            self.input.link_last(self._group)
            self.geo_ctrl.link_last(self._group)

    def _maybe_save_geometry(self) -> bool:
        if self.geo_ctrl is None or not self.geo_ctrl.dirty:
            return True
        r = QMessageBox.question(self, "ARGUS", f"¿Guardar los cambios en la geometría {self.geo_ctrl.name}?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                 | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save and not self.save_geometry():
            return False
        return True

    def _maybe_save_input(self) -> bool:
        if self.input is None or not self.input.dirty:
            return True
        text = f"¿Guardar los cambios en {self.input.name}?"
        if self._pair_mismatch(False, True):  # p. ej. se ha deshecho su GEOMFN tras «Guardar la geometría como…»
            text += ("\n\nOjo: sus números de cuerpos siguen los cambios de la geometría abierta, que no está guardada "
                     "en el fichero que nombra su GEOMFN: guardado así, nombraría otros cuerpos.")
        r = QMessageBox.question(self, "ARGUS", text, SB.Save | SB.Discard | SB.Cancel, SB.Save)
        if r == SB.Cancel:
            return False
        if r == SB.Save:
            return self._save_input(mismatch_ok=True)  # (el aviso ya iba en la pregunta)
        return True

    def save_input(self) -> bool:
        return self._save_input(mismatch_ok=False)

    def _save_input(self, mismatch_ok: bool) -> bool:
        if not self._settle():
            return False
        if self.input is None:
            return False
        if self.input.path is None:
            path = self._ask_input_path()
            return path is not None and self._save_input_to(path)
        return self._save_input_to(self.input.path, mismatch_ok)

    def save_input_as(self) -> bool:
        if not self._settle():
            return False
        if self.input is None:
            return False
        path = self._ask_input_path()
        return path is not None and self._save_input_to(path)

    def _ask_input_path(self) -> Path | None:
        """Dónde guardar el .in (diálogo); avisa si no es la carpeta de la geometría. None si se cancela."""
        if self.input.path is not None:
            start = str(self.input.path)
        elif self.session is not None:
            start = str(self.session.path.with_suffix(".in"))
        else:
            start = str(Path(self.settings.value("last_dir", str(Path.home()))) / "entrada.in")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar fichero de entrada", start, IN_FILTER)
        if not path:
            return None
        if not path.lower().endswith(".in"):
            path += ".in"
        # penhmain abre GEOMFN en la carpeta del .in: desde la carpeta nueva, ¿es la misma geometría?
        s = self.session
        geomfn = input_geomfn(self.input.doc)
        now = (self.input.folder or s.path.parent) / geomfn if s is not None and geomfn else None
        if now is not None and not _same_path(Path(path).parent / geomfn, now):
            r = QMessageBox.warning(
                self, "ARGUS",
                f"penhmain busca la geometría (GEOMFN {geomfn}) en la carpeta del .in, y este .in se va a guardar en "
                f"otra carpeta: allí no encontrará la de ahora ({now}).\n\n¿Guardar de todos modos?",
                SB.Save | SB.Cancel)
            if r != SB.Save:
                return None
        return Path(path)

    def _save_input_to(self, path: Path, mismatch_ok: bool = False) -> bool:
        """Guarda el .in en ``path``; si sigue a cambios de la geometría sin guardar, también la geometría."""
        both = self._pair_mismatch(False, True, path) and not self._pair_mismatch(True, True, path)
        if both and not self._confirm_pair("¿Guardar también la geometría?"):
            return False
        if not both and not mismatch_ok and not self._confirm_mismatch(False, True, path):
            return False
        return self._save_files(geo=both, inp=True, in_path=path)

    def _confirm_mismatch(self, save_geo: bool, save_in: bool, in_path: Path | None = None) -> bool:
        """Si, guardando así, el .in guardado nombraría otros cuerpos de la geometría guardada que nombra su GEOMFN
        (y guardar los dos no lo arregla: p. ej. tras deshacer el cambio de GEOMFN de «Guardar la geometría
        como…»), se pregunta si se guarda de todos modos. False: no guardar."""
        if not self._pair_mismatch(save_geo, save_in, in_path):
            return True
        ic = self.input
        name = Path(in_path).name if in_path is not None else ic.name
        geomfn = input_geomfn(ic.doc) if save_in else ic.saved_geomfn
        r = self._ask(f"Guardado así, el .in «{name}» nombraría otros cuerpos de «{geomfn}», la geometría de su "
                      f"GEOMFN: sus números de cuerpos siguen los cambios de la geometría abierta, y «{geomfn}» no "
                      "está guardada con ellos.\n\n¿Guardar de todos modos?",
                      SB.Save | SB.Cancel, SB.Cancel, {SB.Save: "Guardar de todos modos"})
        return r == SB.Save

    def _confirm_other_inputs(self, geo_target: Path, in_target: Path | None) -> bool:
        """Otros .in de la carpeta que usan la geometría que se va a guardar y, con la versión nueva, nombrarían
        otros cuerpos (p. ej. el .in original tras «Guardar .in como…» una copia): se pregunta. False: no guardar."""
        broken = self._other_inputs_broken(geo_target, in_target)
        if not broken:
            return True
        lines = []
        for path, stale in broken[:5]:
            lines.append(f"• {path.name}: " + "; ".join(stale[:3]) + (" …" if len(stale) > 3 else ""))
        if len(broken) > 5:
            lines.append(f"(y {len(broken) - 5} más)")
        r = self._ask(f"Otros .in de la carpeta usan «{geo_target.name}» y, con la versión que se va a guardar, "
                      "nombrarían otros cuerpos (sus números cambian):\n\n" + "\n".join(lines)
                      + "\n\nSi se guarda, penhmain aplicaría esas líneas a otros cuerpos: habría que corregirlas "
                      "(o nombrar los cuerpos por su etiqueta).\n\n¿Guardar de todos modos?",
                      SB.Save | SB.Cancel, SB.Cancel, {SB.Save: "Guardar de todos modos"})
        return r == SB.Save

    def _other_inputs_broken(self, geo_target: Path, in_target: Path | None) -> list[tuple[Path, list[str]]]:
        """(.in, líneas) de los .in de la carpeta de ``geo_target`` que la usan (GEOMFN) y cuyas líneas de cuerpos
        nombrarían otro cuerpo con la geometría abierta que con la del disco. No cuentan el que se guarda ahora ni
        el abierto si sigue a la geometría (de ese se encarga :meth:`_pair_mismatch`)."""
        from ..pengeom.compiler import compile_geometry
        from ..penhan.follow import stale_references
        from ..penhan.inputfile import parse_in_file

        s, ic = self.session, self.input
        if s is None or not s.ok or not geo_target.is_file():
            return []
        exclude = [in_target] if in_target is not None else []
        if ic is not None and ic.path is not None and in_target is None and (ic.links(applied=False) | ic.saved_links):
            exclude.append(ic.path)  # el .in guardado del abierto, que lo sigue (con «Guardar como», sí cuenta)
        try:
            others = [f.path for f in inputs_for_geometry(geo_target)
                      if not any(_same_path(f.path, e) for e in exclude)]
        except OSError:
            return []
        if not others:
            return []
        try:
            disk = load_geometry(geo_target)
        except Exception:  # noqa: BLE001 - la versión del disco no se puede leer: no hay con qué comparar
            return []
        compiled: dict[tuple, object] = {}

        def old_geometry(parinp: list[float]):
            """La versión del disco con los PARINP de ese .in (la numeración no depende de ellos, pero sin ellos
            PENGEOM no la acepta)."""
            key = tuple(parinp)
            if key not in compiled:
                try:
                    geo = compile_geometry(disk, parinp=list(parinp), base_dir=geo_target.parent)
                except Exception:  # noqa: BLE001
                    geo = None
                compiled[key] = geo if geo is not None and geo.ok else None
            return compiled[key]

        out = []
        for path in others:
            try:
                doc = parse_in_file(path)
            except (OSError, ValueError):
                continue
            old = old_geometry(doc.model.parinp_list())
            if old is None:
                continue
            stale = stale_references(doc, old, s.geo)
            if stale:
                out.append((path, stale))
        return out

    def _save_files(self, geo: bool, inp: bool, *, geo_path: Path | None = None, in_path: Path | None = None) -> bool:
        """Escribe la geometría y/o el .in, los dos enteros o ninguno (``core.files.write_files``).

        Antes, si otro programa ha cambiado uno de ellos en el disco desde que se leyó o se guardó, se pregunta
        si se recarga, se sobrescribe o no se guarda.
        """
        jobs = []
        if geo and self.geo_ctrl is not None:
            jobs.append((self.geo_ctrl, Path(geo_path) if geo_path is not None else self.geo_ctrl.path))
        if inp and self.input is not None:
            target = Path(in_path) if in_path is not None else self.input.path
            if target is None:
                target = self._ask_input_path()
                if target is None:
                    return False
            jobs.append((self.input, target))
        if not jobs:
            return False
        for ctrl, target in jobs:
            if _same_path(target, ctrl.path) and ctrl.changed_on_disk():
                answer = self._ask_external(ctrl.name)
                if answer == "reload":
                    self._reload_from_disk(ctrl)
                if answer != "write":
                    return False
        geo_target = next((t for c, t in jobs if c is self.geo_ctrl), None)
        in_target = next((t for c, t in jobs if c is self.input), None)
        if geo_target is not None and not self._confirm_other_inputs(geo_target, in_target):
            return False
        try:
            data = [ctrl.encoded() for ctrl, _target in jobs]
        except ValueError as exc:  # p. ej. un nombre que no cabe en su campo
            QMessageBox.critical(self, "ARGUS", f"No se ha guardado nada:\n{exc}")
            return False
        existed = [target.is_file() for _ctrl, target in jobs]
        try:
            write_files([(target, d) for (_ctrl, target), (d, _bad) in zip(jobs, data, strict=True)])
        except OSError as exc:
            QMessageBox.critical(self, "ARGUS", self._save_error_text(exc, [target for _ctrl, target in jobs]))
            return False
        for (ctrl, target), (d, bad) in zip(jobs, data, strict=True):
            ctrl.mark_saved(target, d, bad)
        self._after_save(jobs, existed)
        return True

    @staticmethod
    def _save_error_text(exc: OSError, targets: list[Path]) -> str:
        if isinstance(exc, SaveError):
            return exc.strerror
        from ..core.files import TMP_SUFFIX

        def target_of(filename) -> Path | None:
            """El fichero que se guardaba, también si el error es del temporal de ARGUS («.caso.in.1a2b3c4d»
            + TMP_SUFFIX): el usuario no sabe de él (revisión de 0.16.2)."""
            n = Path(filename).name if filename else ""
            return next((t for t in targets if n == t.name or (n.startswith(f".{t.name}.") and n.endswith(TMP_SUFFIX))),
                        None)

        gone = next((t.parent for t in targets if not t.parent.is_dir()), None)
        if gone is not None:  # la carpeta del caso ya no está (cambiada de nombre, movida, un disco quitado)
            text = (f"No se puede guardar {' ni '.join(t.name for t in targets)}: ya no existe la carpeta {gone} "
                    "(¿se ha cambiado de nombre, se ha movido o se ha quitado el disco?). Guárdelo con otro nombre o "
                    "en otra carpeta (Archivo → Guardar como…).")
        else:
            target = target_of(exc.filename) or target_of(getattr(exc, "filename2", None))
            name = target.name if target is not None else " y ".join(t.name for t in targets)
            text = f"No se puede guardar {name} ({exc.strerror or exc})."
            if isinstance(exc, PermissionError) and target is not None:
                text += (" Puede que otro programa lo tenga abierto (en Windows, penhmain mientras simula, o un editor "
                         "de texto): ciérrelo y vuelva a guardar.")
        if len(targets) > 1:
            text += (f"\n\nNo se ha guardado nada: {' y '.join(t.name for t in targets)} se guardan juntos y siguen "
                     "como estaban.")
        return text

    def _after_save(self, jobs: list, existed: list[bool]) -> None:
        """Tras guardar: mensaje, recientes, materiales.txt, caracteres que no caben en Latin-1 y paneles."""
        parts = []
        for (ctrl, target), was in zip(jobs, existed, strict=True):
            what = "la geometría" if ctrl is self.geo_ctrl else "el .in"
            parts.append(f"{what} {target}" + (f" (copia anterior en {target.name}.bak)" if was else ""))
        msg = ("Se han guardado " if len(parts) > 1 else "Se ha guardado ") + " y ".join(parts)
        if any(ctrl is self.input for ctrl, _target in jobs):
            self._follow_work_dir()
            self._add_recent(str(self.input.path))
            txt = self._write_materials_txt()
            if txt is not None:
                msg += f"; lista de materiales en {txt.name}"
        self.statusBar().showMessage(msg, 8000)
        for ctrl, target in jobs:
            self._warn_replaced(target, getattr(ctrl, "replaced_chars", ""))
        if any(ctrl is self.geo_ctrl for ctrl, _target in jobs):
            self.geo_panel.refresh()
        self._refresh_messages()
        self.materials.refresh()
        self._update_title()
        self._update_actions()

    def _follow_work_dir(self) -> None:
        """El .in que usa la geometría se ha guardado en otra carpeta: penhmain se ejecutará allí y PENGEOM buscará
        allí los INCLUDE y el .ct. Se copian si faltan (como con «Guardar la geometría como…») y ARGUS los lee de
        allí."""
        ic, gc = self.input, self.geo_ctrl
        if ic is None or gc is None or ic.folder is None or not self._input_uses_geometry():
            return
        old, new = gc.work_dir, ic.folder
        if _same_path(old, new):
            return
        self._copy_geometry_files(old, new)
        try:
            gc.move_work_dir(new)
        except Exception as exc:  # noqa: BLE001 - un INCLUDE que no se puede leer: la geometría lo dirá
            self.statusBar().showMessage(f"No se ha podido leer la geometría desde {new}: {exc}", 10000)

    def _ask_external(self, name: str) -> str:
        """``name`` ha cambiado en el disco (otro programa): 'reload', 'write' o 'cancel'."""
        r = self._ask(f"«{name}» ha cambiado en el disco desde que ARGUS lo leyó o lo guardó: lo ha modificado otro "
                      "programa.\n\n"
                      f"Recargar: abrir la versión del disco de «{name}» (se pierden sus cambios sin guardar en "
                      "ARGUS).\n"
                      f"Sobrescribir: guardar la versión de ARGUS (la del otro programa queda en {name}.bak).\n"
                      "Cancelar: no guardar nada.",
                      SB.Reset | SB.Save | SB.Cancel, SB.Cancel, {SB.Reset: "Recargar", SB.Save: "Sobrescribir"})
        return {SB.Reset: "reload", SB.Save: "write"}.get(r, "cancel")

    def _reload_from_disk(self, changed=None) -> None:
        """Abre otra vez, como está en el disco, lo que otro programa ha cambiado (``changed``: su controlador), sin
        preguntar por los cambios que se pierden.

        Solo se vuelve a abrir el que ha cambiado si los cambios sin guardar del otro no dependen de él (0.16.3): el .in
        si los de la geometría no cambian los números ni las etiquetas de sus cuerpos, la geometría si el .in no sigue
        cambios suyos que se descartan. Si no, el .in y la geometría que usa se vuelven a abrir juntos (los cambios de
        uno pueden seguir a los del otro) y, si el otro tiene cambios sin guardar, se pregunta antes. Si el .in no usa
        la geometría (p. ej. tras guardarla con un nombre que no cabe en GEOMFN), solo se vuelve a abrir el que ha
        cambiado. Un .in nuevo (nunca guardado) se conserva al volver a abrir la geometría; si sigue cambios de ella
        que se descartan, se pregunta.
        """
        ic, gc = self.input, self.geo_ctrl
        saved_in = ic is not None and ic.path is not None and not ic.is_new
        if saved_in and (self._input_uses_geometry() or self.session is None):
            # Solo el que ha cambiado en el disco, si los cambios sin guardar del otro no dependen de él (revisión de
            # 0.16.2: se volvían a abrir los dos y se perdían, sin decirlo, los cambios del otro).
            other = gc if changed is ic else ic if changed is gc and changed is not None else None
            if other is not None and other.dirty and not other.changed_on_disk():
                if changed is ic and self._geometry_keeps_numbers():
                    self._reload_input_only()
                    return
                if changed is gc and not self._pair_mismatch(False, True):
                    self._reload_geometry_only(ic)
                    return
                r = self._ask(f"Para abrir la versión del disco de «{changed.name}» hay que volver a abrir también "
                              f"«{other.name}»: el .in sigue a la geometría (números y etiquetas de los cuerpos) y "
                              f"sus cambios sin guardar no valen para la otra versión. Se perderán los cambios sin "
                              f"guardar de «{other.name}».\n\n¿Recargar los dos?", SB.Yes | SB.No, SB.No)
                if r != SB.Yes:
                    return
            self.open_file(ic.path, ask=False)
            return
        if saved_in and changed is ic:
            self._reload_input_only()
            return
        if self.session is None:
            return
        keep = ic
        if keep is not None and keep.is_new and keep.links() and self._input_uses_geometry():
            r = self._ask(f"El .in nuevo «{keep.name}» (sin guardar) sigue cambios de la geometría que se descartan "
                          "al volver a abrirla: nombraría otros cuerpos.\n\n¿Conservarlo de todos modos?",
                          SB.Yes | SB.No, SB.No)
            if r != SB.Yes:
                keep = None
        self._reload_geometry_only(keep)

    def _geometry_keeps_numbers(self) -> bool:
        """¿Con los cambios sin guardar de la geometría, los cuerpos siguen con los números y las etiquetas de la
        geometría del disco (las que nombra el .in del disco)? Entonces se puede volver a abrir solo el .in."""
        s, gc = self.session, self.geo_ctrl
        if s is None or not s.ok or gc is None or self._pair_mismatch(True, False):
            return False
        from ..pengeom.compiler import compile_geometry

        try:
            disk = compile_geometry(load_geometry(gc.path), parinp=list(s.parinp), base_dir=s.work_dir)
        except Exception:  # noqa: BLE001 - si no se puede leer, no se sabe: se vuelven a abrir los dos
            return False
        return disk.ok and is_identity(renumber_map(disk, s.geo))

    def _reload_input_only(self) -> None:
        """El .in, como está en el disco; la geometría se queda como está (con sus cambios sin guardar: o no la usa, o
        esos cambios no cambian los números ni las etiquetas de sus cuerpos)."""
        from ..core.files import file_stamp
        from ..penhan.inputfile import parse_in_text

        path = self.input.path
        try:
            data = path.read_bytes()
            doc = parse_in_text(data.decode("latin-1"), str(path))
        except OSError as exc:
            QMessageBox.critical(self, "ARGUS", f"No se puede abrir {path}:\n{exc}")
            return
        self.set_case(self.session, InputController(doc, str(path), stamp=file_stamp(path, data)), self.case_problem,
                      keep_geometry=True)

    def _reload_geometry_only(self, keep) -> None:
        """La geometría, como está en el disco (con la misma carpeta de trabajo y los mismos PARINP); ``keep``: el
        .in que se conserva (o None)."""
        s = self.session
        try:
            session = open_geometry(s.path, parinp=None if s.parinp_provisional else list(s.parinp),
                                    search_input=False, work_dir=s.work_dir)
        except Exception as exc:  # noqa: BLE001 - errores de E/S
            QMessageBox.critical(self, "ARGUS", f"No se puede abrir {s.path}:\n{exc}")
            return
        self.set_case(session, keep)

    def _warn_replaced(self, path: Path, chars: str) -> None:
        """PENGEOM y penhmain leen Latin-1: un carácter que no cabe se ha escrito como «?»."""
        if chars:
            QMessageBox.warning(self, "ARGUS", f"{Path(path).name} tiene caracteres que no se pueden escribir en los "
                                f"ficheros de PENGEOM y penhmain (Latin-1): {' '.join(chars)}. Se han guardado como "
                                "«?»; cámbielos (en títulos, descripciones o comentarios) si importan.")

    def _write_materials_txt(self) -> Path | None:
        """Escribe materiales.txt junto al .in (qué ficheros .mat hacen falta y cuáles faltan)."""
        ctrl = self.input
        if ctrl is None or ctrl.path is None:
            return None
        s = self.session
        geo = s.geo if s is not None and s.ok else None
        db = self._penhan_database()
        rows = materials_table(ctrl.doc, geo, ctrl.folder, db)
        text = materials_txt(rows, ctrl.name, geo, s.name if s is not None else ctrl.doc.model.geomfn,
                             hadrons=hadron_data_needed(ctrl.doc, db),
                             cttrack_fixed=cttrack_fixed(self.settings),
                             mlist=self.materials._mlist, no_endf=no_endf_reason(ctrl.doc))
        try:
            return write_materials_txt(ctrl.folder, text)
        except OSError as exc:
            self.statusBar().showMessage(f"No se pudo escribir materiales.txt: {exc}", 8000)
            return None

    def write_materials_now(self) -> None:
        if self.input is None:
            return
        if self.input.path is None:
            QMessageBox.information(self, "ARGUS", "Guarde primero el .in: materiales.txt se escribe en su carpeta.")
            return
        txt = self._write_materials_txt()
        if txt is not None:
            self.statusBar().showMessage(f"Escrito {txt}", 6000)

    def new_input(self) -> None:
        if not self._settle():
            return
        s = self.session
        if s is None or not s.ok:
            return
        why = in_name_problem(s.path.name, GEOMFN_MAX)
        if why:
            QMessageBox.warning(self, "ARGUS", f"No se puede citar la geometría en un .in (GEOMFN): {why}. Guárdela "
                                f"con un nombre de {GEOMFN_MAX} caracteres o menos y sin tildes ni eñes (Archivo → "
                                "Guardar la geometría como…) y cree después el .in.")
            return
        if self._pair_mismatch(True, False) and not self._pair_mismatch(True, True):
            # El .in que se cierra sigue a cambios de la geometría sin guardar: si no se guardan los dos, al guardar
            # después la geometría, el .in guardado nombraría otros cuerpos.
            if not self._confirm_pair("Para crear otro .in hay que cerrar este. ¿Guardar los dos?") \
                    or not self._save_files(geo=True, inp=True):
                return
        elif not self._maybe_save_input():  # la geometría se queda como está (con sus cambios)
            return
        geo = s.geo
        nmat = max(1, geo.nmat)
        tissues = ct_tissues(geo)  # materiales de un TAC importado: sus ficheros .mat propuestos
        materials = [tissues[i].mat_file if i in tissues else f"material{i}.mat" for i in range(1, nmat + 1)]
        lo, hi = s.bounds
        span = float(max(hi - lo)) or 1.0
        q = nice_length(span / 100.0)
        cx, cy = (round(0.5 * float(lo[i] + hi[i]) / q) * q for i in (0, 1))
        z0 = (round((float(lo[2]) - 0.1 * span) / q)) * q
        pos = [se.clean(cx), se.clean(cy), se.clean(z0)]
        # Los PARINP que usa la geometría, con los valores con que se está viendo (sin ellos, penhmain no la lee).
        parinp = {i: s.parinp[i - 1] for i in geo.parinp_used if i <= len(s.parinp)}
        doc = new_input_document(s.path.name, materials, position=pos, parinp=parinp)
        for n, t in tissues.items():
            if n <= nmat:
                set_material(doc, n, description=f"{t.name} (TAC)")
        ctrl = InputController(doc, None, new=True)
        doc.path = None
        if s.parinp_provisional:  # los valores escritos en el .geo pasan a ser los PARINP del .in nuevo
            notes = provisional_parinp_notes(s.doc, s.geo.ok)
            s.diagnostics = [d for d in s.diagnostics if d not in notes]
            s.parinp_provisional = False
        self.set_case(s, ctrl, keep_geometry=True)
        self.statusBar().showMessage("Nuevo .in: fuente de protones de 100 MeV a lo largo de +Z, delante de la "
                                     "geometría. Cámbiela en el panel Fuente o arrastrándola.", 8000)

    def _add_recent(self, path: str) -> None:
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [p for p in rec if not _same_path(p, path)]
        rec.insert(0, path)
        self.settings.setValue("recent", rec[:12])
        self._update_recent_menu()

    def _update_recent_menu(self) -> None:
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for p in rec:
            a = self.recent_menu.addAction(p)
            a.triggered.connect(lambda checked=False, path=p: self.open_file(path))
        self.recent_menu.setEnabled(bool(rec))

    @staticmethod
    def _dropped_file(mime) -> tuple[str | None, str | None]:
        """(el primer fichero .in o de geometría que se arrastra, el nombre del primero de otro tipo). Una geometría
        puede tener otra extensión (o ninguna): se reconoce por su contenido (:func:`_looks_like_geometry`)."""
        other = None
        for url in mime.urls() if mime.hasUrls() else []:
            path = url.toLocalFile()
            if path and os.path.isfile(path) and (Path(path).suffix.lower() in (".in", ".geo")
                                                  or _looks_like_geometry(path)):
                return path, other
            other = other or (Path(path).name if path else url.toString())
        return None, other

    def _refuse_drop(self, e, other: str | None) -> None:
        e.ignore()
        if other:
            self.statusBar().showMessage(f"ARGUS solo abre ficheros .in y geometrías de PENGEOM: «{other}» no lo "
                                         "es.", 8000)

    def dragEnterEvent(self, e) -> None:
        path, other = self._dropped_file(e.mimeData())
        if path is None:
            self._refuse_drop(e, other)
        else:
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        path, other = self._dropped_file(e.mimeData())
        if path is None:
            self._refuse_drop(e, other)
            return
        e.acceptProposedAction()
        self.open_file(path)

    # -- edición de la fuente desde la vista ------------------------------------------
    def _drag_edit(self, text: str, fn, finished: bool) -> None:
        if self.input is None:
            return
        try:
            self.input.live(fn)
        except ValueError as exc:
            # Lo que penhmain no aceptaría (set_dose_grid lo comprueba antes de escribir): esta posición no se
            # aplica, el .in se queda con la última que valía y el arrastre sigue (o se cierra con ella).
            self.statusBar().showMessage(str(exc), 8000)
        if finished:
            self.input.commit_live(text)
            # Al soltar, el corte aún se da por «arrastrando» y _on_input_changed no ha refrescado estos
            # paneles: la sección de la rejilla de dosis se quedaba con los límites de antes (y el siguiente
            # cambio en ella los volvía a escribir, deshaciendo el arrastre sin avisar).
            self.materials.refresh()
            self._update_material_names()
            self.sim_panel.refresh()
            self._refresh_slow()

    def _on_source_dragged(self, pos, finished: bool) -> None:
        pos = [float(v) for v in pos]
        self._drag_edit("Mover la fuente", lambda d: se.set_position(d, pos), finished)

    def _on_direction_dragged(self, theta: float, phi: float, finished: bool) -> None:
        self._drag_edit("Cambiar la dirección del haz", lambda d: se.set_direction(d, theta, phi), finished)

    def _on_box_dragged(self, size, finished: bool) -> None:
        size = [float(v) for v in size]
        self._drag_edit("Cambiar la caja de la fuente", lambda d: se.set_box(d, size), finished)

    def _on_grid_dragged(self, grid, finished: bool) -> None:
        self._drag_edit("Mover la rejilla de dosis", lambda d: sc.set_dose_grid(d, grid), finished)

    def _on_drag_cancelled(self) -> None:
        # Siempre se olvida el arrastre del módulo: si Esc llega antes de que se aplique el primer
        # movimiento (aún pendiente en el temporizador), no hay nada «en vivo» que anular, pero ese
        # movimiento no debe aplicarse después.
        self._mdrag = None
        if self.input is not None:
            self.input.cancel_live()
        if self.geo_ctrl is not None and self.geo_ctrl.in_live:
            self.geo_ctrl.cancel_live()

    def _abort_drags(self) -> None:
        """Anula el arrastre en curso (fuente, rejilla o módulo) antes de deshacer, guardar, abrir,
        editar por otro lado o cambiar de vista: si no, quedaría a medias (y podría guardarse)."""
        busy = self._mdrag is not None or (self.geo_ctrl is not None and self.geo_ctrl.in_live) \
            or (self.input is not None and self.input.in_live)
        if busy:
            self.view.abort_drag()
            self.view3d.abort_drag()
            self._on_drag_cancelled()

    def _commit_pending(self) -> None:
        """Confirma lo que se está escribiendo en un campo de los paneles (sin Enter todavía).

        Los botones de la barra y los menús no se llevan el foco: sin esto, Guardar
        guardaría el valor anterior, al cerrar se perdería lo escrito sin preguntar,
        y al abrir otro fichero se escribiría en el nuevo al salir del campo.
        """
        w = QApplication.focusWidget()
        if not isinstance(w, (QLineEdit, QAbstractSpinBox)):
            return
        p = w
        while p is not None and p is not self:
            p = p.parentWidget()
        if p is self:
            # Con su lista desplegada (el nombre del material enseña lo que coincide con lo escrito), el campo no se
            # confirma al perder el foco: es lo que pasa al abrirse la lista. Se cierra antes, y lo escrito se guarda
            # (0.17.1: con la lista de ARGUS siempre hay lista, y Guardar perdía el nombre recién escrito).
            popup = QApplication.activePopupWidget()
            if popup is not None:
                popup.hide()
            w.clearFocus()  # como al salir del campo: se confirma (o se descarta si no es válido)

    def _settle(self) -> bool:
        """Antes de guardar, deshacer, rehacer, abrir, cerrar o crear: nada a medias. False si no se puede seguir: se
        estaba escribiendo a mano en «Geometrías complejas», PENGEOM no aceptaría el texto y se ha elegido «Seguir
        editando» (quien llama no hace nada)."""
        self.complex.settle()
        if self.complex.manual:
            return False
        self._abort_drags()
        self._commit_pending()
        # Lo que se acaba de confirmar en los paneles (o en sus tablas y listas) se aplica en la vuelta
        # siguiente del bucle de eventos: se aplica ya, antes de la acción y en su orden.
        for panel in (self.source_panel, self.sim_panel, self.materials):
            panel.flush_pending()
        dlg = getattr(self, "_outputs_dialog", None)
        if dlg is not None:
            dlg.flush_pending()
        return True

    def _on_context_menu(self, global_pos, point, kb: int) -> None:
        if self.complex.manual:  # escribiendo a mano: lo que se ve es el texto sin aplicar, no se edita desde aquí
            self.statusBar().showMessage("Termine antes «Manual edit» (End manual edit o Cancel edit).", 6000)
            return
        if self._preview_session is not None:  # lo que se veía era una vista previa: se quita y se mira otra vez
            self.complex.drop_preview()
            lp = self.view.mapFromGlobal(global_pos)
            kb = self.view.body_at(lp.x(), lp.y())[0]
        menu = QMenu(self)
        geo = self.session.geo if self.session is not None and self.session.ok else None
        p = [se.clean(float(v), 6) for v in point]
        where = f"({p[0]:g}, {p[1]:g}, {p[2]:g}) cm"
        ctrl = self.input
        src = ctrl.doc.model.source if ctrl is not None else None
        if ctrl is not None and self._source_read() and not src.from_psf:
            a = menu.addAction(f"Colocar la fuente aquí {where}")
            a.triggered.connect(lambda: ctrl.edit("Colocar la fuente", lambda d: se.set_position(d, p)))
            if not src.radioactive:
                a = menu.addAction("Apuntar el haz hacia aquí")
                a.setEnabled(any(abs(x - y) > 1e-12 for x, y in zip(p, src.position)))
                a.triggered.connect(lambda: ctrl.edit("Apuntar el haz", lambda d: se.aim_at(d, p)))
            if kb and geo is not None and kb <= geo.nbodys and geo.body(kb).origin != "enclosure":
                menu.addSeparator()
                self._add_source_body_actions(menu, kb)
        elif ctrl is None and geo is not None:
            a = menu.addAction("Crear un .in con la fuente aquí")
            a.triggered.connect(lambda: self._new_input_at(p))
        if kb and geo is not None and kb <= geo.nbodys:
            menu.addSeparator()
            a = menu.addAction(f"Seleccionar {body_title(geo, kb)}")
            a.triggered.connect(lambda: self.select_body(kb))
            self._add_detector_actions(menu, kb)
        if geo is not None:
            self._add_edit_actions(menu, kb, np.asarray(p, dtype=float))
        if self.view.results_overlay is not None:
            menu.addSeparator()
            pl = self.view.plane
            for axis, label in ((pl.u, pl.u_label), (pl.v, pl.v_label)):
                a = menu.addAction(f"Perfil del mapa de resultados a lo largo de {label} por aquí")
                a.triggered.connect(lambda _c=False, d=axis, n=label: self.results_profile(point, d, n))
        if not menu.isEmpty():
            self._exec_menu(menu, global_pos)
        else:
            menu.deleteLater()

    def _exec_menu(self, menu: QMenu, global_pos) -> None:
        try:
            menu.exec(global_pos)
        finally:
            # Sin esto, cada menú se quedaría como hijo oculto de la ventana, con sus funciones y la geometría
            # que nombran (también después de abrir otros ficheros).
            menu.deleteLater()

    # -- detectores desde las vistas --------------------------------------------------------
    _DETECTORS = (("impact", "de impacto"), ("energy", "de energía depositada"))

    def _detector_api(self, kind: str):
        from .sim_outputs import EnergySection, ImpactSection

        if kind == "impact":
            return sc.impact_detectors, sc.set_impact_detectors, lambda: sc.ImpactDetector(0.0, 0.0, 100, 0, 1), \
                ImpactSection
        return sc.energy_detectors, sc.set_energy_detectors, lambda: sc.EnergyDetector(0.0, 0.0, 100), EnergySection

    def _add_detector_actions(self, menu: QMenu, kb: int) -> None:
        """Submenú para poner o quitar el cuerpo en los detectores de impacto y de energía."""
        from .sim_widgets import resolve_ref

        ctrl = self.input
        s = self.session
        if ctrl is None or s is None or not s.ok or not ctrl.doc.model.ok or not 1 <= kb <= s.geo.nbodys:
            return
        geo = s.geo
        if geo.body(kb).origin == "enclosure":
            return
        sub = menu.addMenu(f"Detectores con {body_title(geo, kb)}")
        if geo.body(kb).material == 0:
            a = sub.addAction("Un cuerpo vacío no puede formar parte de un detector")
            a.setEnabled(False)
            return
        for kind, name in self._DETECTORS:
            read, _, _, _ = self._detector_api(kind)
            dets = read(ctrl.doc)
            for i, d in enumerate(dets, start=1):
                a = sub.addAction(f"Detector {name} {i}")
                a.setCheckable(True)
                a.setChecked(any(resolve_ref(geo, r.label, r.kb, kind != "impact") == kb for r in d.bodies))
                a.triggered.connect(lambda checked=False, kind=kind, i=i: self.toggle_detector_body(kind, i, kb))
            if len(dets) < sc.MAX_DETECTORS:
                a = sub.addAction(f"Nuevo detector {name} con este cuerpo")
                a.triggered.connect(lambda checked=False, kind=kind: self.toggle_detector_body(kind, 0, kb))

    def toggle_detector_body(self, kind: str, index: int, kb: int) -> None:
        """Pone o quita el cuerpo ``kb`` en el detector ``index`` (1, 2...; 0 = uno nuevo)."""
        from .sim_widgets import body_ref_for, resolve_ref

        ctrl = self.input
        geo = self.session.geo
        read, write, maker, section = self._detector_api(kind)
        dets = read(ctrl.doc)
        if index == 0:
            dets.append(maker())
            index = len(dets)
        det = dets[index - 1]
        inside = [r for r in det.bodies if resolve_ref(geo, r.label, r.kb, kind != "impact") == kb]
        if inside:
            det.bodies = [r for r in det.bodies if r not in inside]
        else:
            det.bodies.append(sc.BodyRef(*body_ref_for(geo, kb)))
        try:
            ctrl.edit("Cuerpos del detector", lambda d: write(d, dets))
        except ValueError as exc:
            QMessageBox.warning(self, "Detectores", str(exc))
            return
        sec = self.sim_panel.section(section)
        sec.current = index - 1
        self.sim_panel.frame(section).set_expanded(True)
        self.sim_panel.refresh()
        self.dock_sim.raise_()

    # -- la fuente extensa en unos cuerpos ---------------------------------------------------------------------
    def _boxable(self, kbs) -> list[int]:
        """Los cuerpos que una caja puede cubrir: no la envolvente que añade PENGEOM (todo lo de alrededor; un
        `SBODY 0` la nombra)."""
        geo = self.session.geo
        return [kb for kb in kbs if 1 <= kb <= geo.nbodys and geo.body(kb).origin != "enclosure"]

    def source_bound(self, kb: int):
        """Cota [lo, hi] de lo que LOCATE puede situar en ``kb`` (``extent.locate_bound``: al momento, siempre mayor
        que el cuerpo; puede tener infinitos, y lo > hi si sus superficies no dejan nada)."""
        self._extent_caches()
        box = self._bound_cache.get(kb)
        if box is None:
            box = self._bound_cache[kb] = locate_bound(self.session.geo, kb)
        return box

    def source_extent(self, kb: int):
        """(lo, hi, estado) de los puntos que LOCATE sitúa en ``kb``, lo que penhmain acepta como cuerpo activo
        (``extent.source_extent``; se guarda con la geometría). Estado «ok», «vacío» (lo, hi = None) o «sin cota»."""
        s = self.session
        self._extent_caches()
        ext = self._source_cache.get(kb)
        if ext is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ext = source_extent(s.geo, kb, s.bounds)
            finally:
                QApplication.restoreOverrideCursor()
            self._source_cache[kb] = ext
        return ext

    def bodies_extent(self, kbs) -> tuple[np.ndarray, np.ndarray] | None:
        """La caja que contiene todo lo que LOCATE sitúa en esos cuerpos (la unión de la de cada uno; sin la
        envolvente ni los cuerpos vacíos). None si no hay nada."""
        boxes = [e for e in (self.source_extent(kb) for kb in self._boxable(kbs)) if e[0] is not None]
        if not boxes:
            return None
        return np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)

    def uncovered_bodies(self, kbs) -> list[int]:
        """Los cuerpos de ``kbs`` que la caja de la fuente extensa no cubre enteros (de ellos solo emite lo que queda
        dentro). Primero con su cota, al momento (si la caja la cubre, cubre el cuerpo); si no, con la caja de lo que
        LOCATE sitúa en el cuerpo (se guarda). Un cuerpo vacío no cuenta."""
        ctrl = self.input
        s = self.session
        if ctrl is None or s is None or not s.ok or not ctrl.doc.model.source.extended:
            return []
        src = ctrl.doc.model.source
        out = []
        for kb in self._boxable(kbs):
            lo, hi = self.source_bound(kb)
            if np.any(hi < lo) or (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and
                                   se.box_contains(src, lo, hi)):
                continue
            e_lo, e_hi, _state = self.source_extent(kb)
            if e_lo is not None and not se.box_contains(src, e_lo, e_hi):
                out.append(kb)
        return out

    def source_box_covers(self, kbs) -> bool:
        """¿La caja de la fuente extensa cubre enteros esos cuerpos?"""
        return not self.uncovered_bodies(kbs)

    def organ_parts(self, group) -> list[list[int]]:
        """Las partes separadas de un órgano partido en varios cuerpos (los dos pulmones de sus siete trozos):
        ``source_edit.connected_parts`` con la caja de lo que LOCATE sitúa en cada cuerpo. Las cotas (al momento, y
        siempre mayores que el cuerpo) separan ya lo que está lejos; solo las partes de más de un cuerpo necesitan las
        cajas de verdad (se guardan, como las partes). Los cuerpos vacíos no están en ninguna parte (de uno solo se
        mira antes si LOCATE lo encuentra con unos puntos al azar en su cota: al momento)."""
        self._extent_caches()
        key = tuple(sorted(int(k) for k in group))
        parts = self._parts_cache.get(key)
        if parts is None:
            inf = (np.full(3, -np.inf), np.full(3, np.inf))
            rough = {}
            for kb in key:
                lo, hi = self.source_bound(kb)
                if np.any(hi < lo):
                    continue  # sus superficies no dejan nada: vacío
                ok = np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))
                rough[kb] = (lo, hi) if ok else inf  # sin cota: puede tocar a cualquiera
            parts = []
            for p in se.connected_parts(list(rough), rough):
                if len(p) == 1:
                    parts.extend([p] if self._body_found(p[0], *rough[p[0]]) else [])
                    continue
                boxes = {kb: e[:2] for kb in p if (e := self.source_extent(kb))[0] is not None}
                parts.extend(se.connected_parts(list(boxes), boxes))
            parts = self._parts_cache[key] = sorted(parts)
        return [list(p) for p in parts]

    def _body_found(self, kb: int, lo, hi) -> bool:
        """¿LOCATE sitúa algún punto en ``kb``? Con su caja si ya se tiene; si no, con unos puntos al azar en su
        cota [lo, hi] (al momento) y, si ninguno cae en él, con la caja de verdad (``source_extent``, a fondo)."""
        ext = self._source_cache.get(kb)
        if ext is not None:
            return ext[0] is not None
        if np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.all(hi > lo):
            pts = np.random.default_rng(kb).uniform(lo, hi, (500, 3))
            if np.any(locator_for(self.session.geo).locate(pts)[0] == kb):
                return True
        return self.source_extent(kb)[0] is not None

    def _organ_choices(self, kb: int) -> list[tuple[str, list[int], str]]:
        """(texto, cuerpos, ayuda) del órgano de ``kb`` si está partido en varios cuerpos: su parte, si tiene más de un
        cuerpo y no es todo el órgano (un pulmón), y todo el órgano (los dos)."""
        geo = self.session.geo
        group = se.body_group(geo, kb)
        if len(group) < 2:
            return []
        desc = se.body_description(geo, kb)
        parts = self.organ_parts(group)
        part = next((p for p in parts if kb in p), [kb])
        out = []
        if len(parts) > 1 and len(part) > 1:
            out.append((f"Este «{desc}» ({len(part)} cuerpos unidos)", part,
                        f"La parte del órgano donde está este cuerpo: los cuerpos «{desc}» que se tocan con él (una "
                        f"de las {len(parts)})"))
        if len(parts) > 1:
            out.append((f"Los {len(parts)} «{desc}» ({len(group)} cuerpos)", group,
                        f"Las {len(parts)} partes separadas: todos los cuerpos con la descripción «{desc}» y el mismo "
                        "material"))
        else:
            out.append((f"Todo «{desc}» ({len(group)} cuerpos)", group,
                        f"El órgano partido en {len(group)} cuerpos que se tocan: todos los cuerpos con la descripción "
                        f"«{desc}» y el mismo material"))
        return out

    def _lazy_menu(self, menu: QMenu, fill) -> None:
        """Rellena el submenú al abrirlo: lo que cuesta calcular (las partes de un órgano) no retrasa el menú."""
        menu.setToolTipsVisible(True)

        def once():
            if not menu.property("argus_filled"):
                menu.setProperty("argus_filled", True)
                fill(menu)

        menu.aboutToShow.connect(once)

    def _body_label(self, kb: int) -> str:
        """«Cuerpo «126» (BODY LUNGS)» para los menús: el nombre del cuerpo con su descripción (un «&» se ve como
        tal, no como la tecla del menú)."""
        geo = self.session.geo
        name = body_title(geo, kb)
        desc = se.body_description(geo, kb)
        if desc:
            name = name[:-1] + f", {desc})" if name.endswith(")") else f"{name} ({desc})"
        return name.replace("&", "&&")

    def _add_source_body_actions(self, menu, kb: int) -> None:
        """En el menú de un cuerpo: «Fuente extensa en» él, en su parte del órgano (un pulmón) o en todo el órgano
        (los dos) y, si la fuente ya es extensa, si el cuerpo es uno de los activos y añadir su parte o su órgano."""
        ctrl = self.input
        geo = self.session.geo
        src = ctrl.doc.model.source
        name = self._body_label(kb)
        multi = len(se.body_group(geo, kb)) > 1

        def choices():
            for text, kbs, tip in self._organ_choices(kb):
                yield text.replace("&", "&&"), kbs, tip, len(kbs) > se.MAX_SOURCE_BODIES

        too_many = (f"penhmain admite como mucho {se.MAX_SOURCE_BODIES} cuerpos activos (MKBS): elija una parte o "
                    "marque los cuerpos en el panel Fuente")

        def fill_extended(sub):
            a = sub.addAction(name)
            a.setToolTip("Solo este cuerpo: la caja SBOX justo alrededor de él y él como cuerpo activo (SBODY). Las "
                         "partículas salen de todo el cuerpo y solo de él, no del resto de la caja")
            a.triggered.connect(lambda: self.extended_source_in([kb]))
            for text, kbs, tip, over in choices():
                a = sub.addAction(text)
                a.setToolTip(too_many if over else tip + ". Las partículas salen solo de esos cuerpos, no del resto "
                             "de la caja")
                a.setEnabled(not over)
                a.triggered.connect(lambda _c=False, k=kbs: self.extended_source_in(k))

        self._lazy_menu(menu.addMenu("Fuente extensa en"), fill_extended)
        if not src.extended:
            return
        active = se.active_bodies(ctrl.doc, geo)
        a = menu.addAction(f"Cuerpo activo de la fuente: {name}")
        a.setCheckable(True)
        a.setChecked(kb in active)
        a.setToolTip("Si la caja de la fuente no lo cubre entero, se ajusta a los cuerpos activos para que lo cubra")
        if kb in active:
            a.triggered.connect(lambda: self.set_source_bodies(sorted(active - {kb})))
        else:
            a.triggered.connect(lambda: self.set_source_bodies(sorted(active | {kb})))
        if multi and not set(se.body_group(geo, kb)) <= active:

            def fill_add(sub):
                kept = len(se.unresolved_refs(ctrl.doc, geo))  # las líneas SBODY que no son de la geometría se quedan
                for text, kbs, tip, _over in choices():
                    a = sub.addAction(text)
                    over = len(active | set(kbs)) + kept > se.MAX_SOURCE_BODIES
                    a.setToolTip(too_many if over else tip)
                    a.setEnabled(not set(kbs) <= active and not over)  # (ya lo son, o serían demasiados)
                    a.triggered.connect(lambda _c=False, k=kbs: self.set_source_bodies(sorted(active | set(k))))

            self._lazy_menu(menu.addMenu("Añadir a los cuerpos activos"), fill_add)
        if active and not self.source_box_covers(active):
            a = menu.addAction("Ajustar la caja de la fuente a los cuerpos activos")
            a.setToolTip("Hay cuerpos activos que la caja no cubre enteros: solo emiten la parte que queda dentro")
            a.triggered.connect(self.fit_source_box)

    def _source_notes(self, kbs) -> str:
        """Lo que conviene saber de esos cuerpos al poner la caja: los vacíos y los que no tienen cota."""
        geo = self.session.geo
        states = {kb: self.source_extent(kb)[2] for kb in self._boxable(kbs)}
        notes = []
        empty = [kb for kb, st in states.items() if st == "vacío"]
        open_ = [kb for kb, st in states.items() if st == "sin cota"]
        if empty:
            names = ", ".join(body_title(geo, kb) for kb in empty[:3]) + (" …" if len(empty) > 3 else "")
            if len(empty) > 1:
                notes.append(f"{names} están vacíos (LOCATE no los encuentra en ningún punto: no emiten nada)")
            else:
                notes.append(f"{names} está vacío (LOCATE no lo encuentra en ningún punto: no emite nada)")
        if open_:
            names = ", ".join(body_title(geo, kb) for kb in open_[:3]) + (" …" if len(open_) > 3 else "")
            verb = "no tienen" if len(open_) > 1 else "no tiene"
            notes.append(f"{names} {verb} límite en algún eje: la caja cubre solo la parte cercana a la geometría")
        return "; ".join(notes)

    def extended_source_in(self, kbs) -> None:
        """Fuente extensa en esos cuerpos: la caja justo alrededor de todo lo que LOCATE sitúa en ellos y ellos, los
        cuerpos activos."""
        s = self.session
        if self.input is None or s is None or not s.ok or self._source_unread_message():
            return
        geo = s.geo
        kbs = sorted(set(int(k) for k in kbs))
        if len(kbs) > se.MAX_SOURCE_BODIES:
            self.statusBar().showMessage(f"No se ha cambiado la fuente: penhmain admite como mucho "
                                         f"{se.MAX_SOURCE_BODIES} cuerpos activos (MKBS) y son {len(kbs)}.", 10000)
            return
        ext = self.bodies_extent(kbs)
        notes = self._source_notes(kbs)
        if ext is None:
            QMessageBox.information(self, "Fuente extensa", "No se ha cambiado la fuente: "
                                    + (notes or "esos cuerpos no se encuentran en la geometría") + ".")
            return
        lo, hi = ext

        def fn(d):
            se.set_box_around(d, lo, hi)
            se.set_active_bodies(d, geo, kbs)

        self.input.edit("Fuente extensa en " + (body_title(geo, kbs[0]) if len(kbs) == 1 else
                                                f"{len(kbs)} cuerpos"), fn)
        box = " × ".join(f"{v:g}".replace(".", ",") for v in self.input.doc.model.source.box)
        who = "todo ese cuerpo y solo de él" if len(kbs) == 1 else f"todos esos {len(kbs)} cuerpos y solo de ellos"
        self.statusBar().showMessage(f"Fuente extensa: las partículas salen de {who} (cuerpos activos, SBODY); la caja "
                                     f"SBOX, de {box} cm, es donde penhmain sortea los puntos."
                                     + (f" Atención: {notes}." if notes else ""), 15000 if notes else 10000)

    def set_source_bodies(self, kbs, extra_refs=None) -> None:
        """Cuerpos activos de la fuente extensa. Si se añade alguno que la caja no cubre entero, la caja se ajusta a
        todos los activos (si no, de ese cuerpo solo emitiría lo que quedase dentro de la caja). ``extra_refs``: las
        referencias SBODY que no son cuerpos de la lista (las da el panel); sin ellas, se conservan las del .in que no
        llevan a ningún cuerpo de esta geometría."""
        ctrl = self.input
        s = self.session
        if self._source_unread_message():
            self.source_panel.refresh()
            return
        if ctrl is None or s is None or not s.ok:
            # Sin geometría no se sabe qué cubre la caja: solo las líneas SBODY.
            if ctrl is not None:
                ctrl.edit("Cuerpos activos de la fuente",
                          lambda d: se.set_active_bodies(d, None, kbs, extra_refs or ()))
            return
        geo = s.geo
        kbs = [int(k) for k in kbs]
        extra = list(extra_refs) if extra_refs is not None else se.unresolved_refs(ctrl.doc, geo)
        if len(set(kbs)) + len(extra) > se.MAX_SOURCE_BODIES:
            self.statusBar().showMessage(f"No se han cambiado los cuerpos activos: penhmain admite como mucho "
                                         f"{se.MAX_SOURCE_BODIES} (MKBS).", 10000)
            self.source_panel.refresh()
            return
        added = set(kbs) - se.active_bodies(ctrl.doc, geo)
        refit = bool(self.uncovered_bodies(sorted(added)))
        ext = self.bodies_extent(kbs) if refit else None
        refit = ext is not None

        def fn(d):
            if refit:
                se.set_box_around(d, *ext)
            se.set_active_bodies(d, geo, kbs, extra)

        ctrl.edit("Cuerpos activos de la fuente", fn)
        if refit:
            self.statusBar().showMessage("La caja de la fuente se ha ajustado para cubrir enteros los cuerpos "
                                         "activos (Deshacer la deja como estaba).", 8000)

    def fit_source_box(self) -> None:
        """La caja de la fuente extensa justo alrededor de sus cuerpos activos."""
        ctrl = self.input
        geo = self.session.geo if self.session is not None and self.session.ok else None
        if ctrl is None or geo is None or not ctrl.doc.model.source.extended or self._source_unread_message():
            return
        ext = self.bodies_extent(sorted(se.active_bodies(ctrl.doc, geo)))
        if ext is None:
            return
        ctrl.edit("Ajustar la caja de la fuente", lambda d: se.set_box_around(d, *ext))

    def _new_input_at(self, point) -> None:
        self.new_input()
        if self.input is not None:
            self.input.edit("Colocar la fuente", lambda d: se.set_position(d, point))

    def _focus_source(self) -> None:
        self.view.focus_source()
        self.view3d.focus_source()
        self._sync_depth_widgets()
        self._sync_slice_plane_3d()

    # -- edición de la geometría (F6) ---------------------------------------------------
    def _set_geo_controller(self, ctrl: GeoController | None) -> None:
        old = self.geo_ctrl
        if old is not None:
            old.changed.disconnect(self._on_geometry_changed)
            old.dirtyChanged.disconnect(self._update_title)
            old.stack.indexChanged.disconnect(self._on_stack_index)
            old.deleteLater()
        self.geo_ctrl = ctrl
        if ctrl is not None:
            ctrl.changed.connect(self._on_geometry_changed)
            ctrl.dirtyChanged.connect(self._update_title)
            ctrl.dirtyChanged.connect(lambda _d: self._update_actions())
            ctrl.stack.indexChanged.connect(self._on_stack_index)
        self.geo_panel.set_context(ctrl)

    def _can_edit(self) -> bool:
        """¿Se puede editar la geometría? Tiene que leerse sin errores: con un PARINP del .in que GEOMIN no acepta
        se ve rota (con su aviso) y no se edita hasta corregirlo o deshacerlo. Tampoco mientras se escribe a mano en
        «Geometrías complejas» (al terminar, lo escrito sustituye a la geometría: desharía el cambio)."""
        return self.geo_ctrl is not None and self.session is not None and self.session.ok and not self.complex.manual

    def _refuse_not_editable(self, kb: int, what: str) -> bool:
        """True, y dice por qué, si ``kb`` no es un cuerpo o módulo del fichero que se pueda ``what`` (duplicar...)."""
        try:
            ge.editable_body(self.session.geo, kb)
        except ge.EditError as exc:
            QMessageBox.information(self, "ARGUS", f"No se puede {what} {body_title(self.session.geo, kb)}: {exc}")
            return True
        return False

    def edit_geometry(self, text: str, fn, renames: dict[str, str] | None = None, key_map=None, then=None,
                      errors=None) -> object:
        """Aplica un cambio a la geometría (un paso de deshacer); avisa si PENGEOM no lo aceptaría.

        Si el cambio renumera los cuerpos (o cambia etiquetas: ``renames``, o
        identidades: ``key_map``), el .in abierto se actualiza en el mismo paso
        de deshacer (véase ``penhan.follow``); lo que ha cambiado en él queda en
        ``last_follow_notes``. ``fn.renames`` y ``fn.check`` (comprobación de
        solapes) también valen. ``then(resultado)``: lo que se hace después en
        el .in en el mismo paso de deshacer (p. ej. los detectores de un cuerpo
        nuevo; con ``_link_pair``). ``errors(exc)``: quien dice por qué no se ha
        hecho (si no, una ventana).
        """
        self.last_follow_notes = []
        if self.geo_ctrl is None:
            return None
        if self.complex.manual:  # al terminar «Manual edit», lo escrito sustituiría (desharía) este cambio
            msg = "Termine antes «Manual edit» (End manual edit o Cancel edit)."
            if errors is not None:
                errors(ge.EditError(msg))
            else:
                QMessageBox.information(self, "ARGUS", msg)
            return None
        self._abort_drags()  # p. ej. Supr o Ctrl+D con el botón del ratón aún pulsado en un arrastre
        renames = renames if renames is not None else getattr(fn, "renames", None)
        old = self.geo_ctrl.session.geo
        self._group_seq += 1
        self._group = self._group_seq
        self._edit_identity = (renames, key_map)  # para seguir la selección y lo oculto en 3D (_body_map)
        try:
            result = self.geo_ctrl.edit(text, fn, check=self._follow_guard(fn, renames, key_map))
            new = self.geo_ctrl.session.geo
            if new is not old and old is not None and old.ok and new.ok:
                self._follow_geometry(old, new, renames, key_map() if callable(key_map) else key_map)
            if then is not None and result is not None:
                then(result)
            return result
        except _Declined as exc:
            if errors is not None:  # (quien lo pidió dice que no se ha hecho)
                errors(ge.EditError(str(exc)))
            self.geo_panel.refresh()
            return None
        except ge.EditError as exc:
            if errors is not None:
                errors(exc)
            else:
                QMessageBox.warning(self, "ARGUS", f"No se ha hecho el cambio «{text}»:\n\n{exc}")
            self.geo_panel.refresh()
            return None
        finally:
            self._group = 0
            self._edit_identity = None

    def _input_uses_geometry(self) -> bool:
        """¿El .in abierto usa esta geometría (GEOMFN)? También con errores de lectura en el .in: GEOMFN se lee
        línea a línea, como al abrirlo (``input_geomfn``)."""
        ctrl, s = self.input, self.session
        if ctrl is None or s is None:
            return False
        name = input_geomfn(ctrl.doc)
        return bool(name) and _same_path(s.path, (ctrl.folder or s.path.parent) / name)

    def _follow_guard(self, fn, renames=None, key_map=None):
        """``check`` para ``GeoController.edit``: el de ``fn`` (p. ej. los solapes) y, si el cambio renumera los
        cuerpos y alguna línea de cuerpos del .in no se puede leer (no se podrá actualizar), la pregunta de si se
        hace de todos modos, antes de hacerlo."""
        previous = getattr(fn, "check", None)
        ctrl = self.input
        bad = [ln for ln in ctrl.doc.lines if ln.spec is not None and ln.spec.mode == "body" and ln.error] \
            if ctrl is not None and self._input_uses_geometry() else []

        def check(before, after) -> None:
            if previous is not None:
                previous(before, after)
            if not bad:
                return
            mapping = renumber_map(before, after, renames, key_map() if callable(key_map) else key_map)
            if is_identity(mapping) and not {k: v for k, v in (renames or {}).items() if k != v}:
                return
            rows = [f"{ln.kw.strip()} en la línea {ctrl.doc.row(ln) + 1}" for ln in bad[:4]]
            r = QMessageBox.question(
                self, "ARGUS", "Este cambio renumera los cuerpos, pero el .in tiene líneas de cuerpos que no se pueden "
                f"leer ({', '.join(rows)}{' …' if len(bad) > 4 else ''}): no se actualizarán y, cuando se corrijan, "
                "nombrarán otros cuerpos.\n\n¿Hacer el cambio de todos modos?",
                SB.Yes | SB.No, SB.No)
            if r != SB.Yes:
                raise _Declined("Cambio cancelado.")

        return check

    def _follow_geometry(self, old, new, renames: dict[str, str] | None = None, key_map=None,
                         keep_gone: bool = False) -> None:
        """Números y etiquetas de cuerpos del .in tras un cambio de la geometría (mismo paso de deshacer).

        Se sigue línea a línea, también si el .in tiene errores de lectura; el paso del .in queda enlazado con el
        de la geometría (se guardan juntos). ``keep_gone``: las líneas de los cuerpos que ya no están se quedan
        como están (escrito a mano, no se sabe si se han borrado o han cambiado de etiqueta).
        """
        from ..penhan.follow import body_lines, is_unlabelled_ref, labels_still_resolve

        self.last_follow_notes = []
        if not self._input_uses_geometry():
            return
        mapping = renumber_map(old, new, renames, key_map)
        if keep_gone:
            mapping = {k: v for k, v in mapping.items() if v is not None}  # (sin número: la línea no se toca)
        deleted = {old.balias(kb) for kb, v in mapping.items() if v is None and old.balias(kb)}
        renames = {k: v for k, v in (renames or {}).items() if k != v}
        if is_identity(mapping) and not renames and not deleted \
                and not any(is_unlabelled_ref(ln) for ln in body_lines(self.input.doc)) \
                and labels_still_resolve(self.input.doc, mapping, old, new):
            return  # (con 0 o una etiqueta en blanco, el último cuerpo sin etiqueta puede ser otro)
        notes: list[str] = []
        if self.input.edit("El .in sigue a la geometría",
                           lambda d: notes.extend(follow_geometry(d, mapping, renames, deleted, old_geo=old,
                                                                  new_geo=new))):
            self._link_pair()
        self.last_follow_notes = notes

    def lost_in_lines(self, old, new, renames: dict[str, str] | None = None, key_map=None) -> list[str]:
        """Las líneas del .in abierto que se quitarían si la geometría pasara de ``old`` a ``new`` (las de los cuerpos
        que desaparecen y las de los detectores que se quedan sin cuerpos), sin cambiar nada: para preguntar antes.
        Lo mismo que hará ``_follow_geometry``, en una copia del .in."""
        import copy

        if self.input is None or old is None or new is None or not old.ok or not new.ok \
                or not self._input_uses_geometry():
            return []
        mapping = renumber_map(old, new, renames, key_map)
        if all(v is not None for v in mapping.values()):
            return []
        deleted = {old.balias(kb) for kb, v in mapping.items() if v is None and old.balias(kb)}
        work = copy.deepcopy(self.input.doc)
        shown = {id(ln): t for ln, t in zip(work.lines, work.display_lines(), strict=True)}
        dropped: list = []
        follow_geometry(work, mapping, {k: v for k, v in (renames or {}).items() if k != v}, deleted,
                        old_geo=old, new_geo=new, dropped=dropped)
        return [shown.get(id(ln), ln.kw).rstrip() for ln in dropped]

    def _report(self, message: str, timeout: int = 10000) -> None:
        """Mensaje en la barra de estado, con lo que haya cambiado en el .in por el último cambio del .geo."""
        if self.last_follow_notes:
            message = message.rstrip() + " En el .in: " + "; ".join(self.last_follow_notes) + "."
        self.statusBar().showMessage(message, timeout)

    _edit_identity = None  # (renames, key_map) de la edición en curso (edit_geometry)
    case_errors: list = []  # los errores del caso (geometría y .in) que enseña el panel de mensajes

    def _body_map(self, old, new) -> dict[int, int | None] | None:
        """Número en ``new`` de cada cuerpo de ``old`` (None si ya no está), por su identidad y no por su etiqueta:
        las copias de un CLONE, la envolvente y los cuerpos de un INCLUDE sin «*» no la tienen. None si alguna de
        las dos geometrías tiene errores."""
        if old is None or new is None or not old.ok or not new.ok:
            return None
        renames, key_map = self._edit_identity or (None, None)
        return renumber_map(old.geo, new.geo, renames, key_map() if callable(key_map) else key_map)

    def _on_geometry_changed(self, session) -> None:
        """La geometría ha cambiado (edición, deshacer o rehacer): se actualiza todo sin mover las vistas."""
        if self.geo_ctrl is not None and self.geo_ctrl.in_live:
            # Arrastrando un módulo: solo las vistas (el resto, al soltar). Los cuerpos no cambian de número.
            sel = self.view.selected
            self.session = session
            same = {kb: kb for kb in self.view3d.hidden_bodies}
            self.view3d.set_session(session, keep_camera=True, keep_bounds=True, body_map=same)
            self.view.set_session(session, keep_view=True)
            self.view.set_selected(sel)
            self.view3d.set_selected(sel)
            return
        old = self.session
        old_sel = self.view.selected
        mapping = self._body_map(old, session)
        self.session = session
        ok = session.ok
        self._push_source_to_view()
        self.view3d.set_session(session, keep_camera=True, body_map=mapping)
        self.view.fit_box = (self.view3d.scene_lo, self.view3d.scene_hi) if ok else None
        self.view.set_session(session, keep_view=True)
        self._sync_slice_plane_3d()
        self._update_material_names()
        self.tree.set_geometry(session.geo if ok else None, self.view.color_mode, self.material_colors)
        self.materials.set_context(self.input, session, self.material_colors, keep_visibility=True,
                                   defaults=self._default_colors(session))
        self.view3d.set_material_visibility(self.materials.visibility)
        self.source.show_document(session.doc)
        self._preview_session = None
        self.complex.refresh()
        self.source_panel.set_context(self.input, session)
        self.sim_panel.set_context(self.input, session)
        self._outputs_context(self.input, session)
        self._refresh_messages()
        self._update_title()
        self._update_actions()
        kb = None
        label = self.geo_ctrl.last_result if isinstance(self.geo_ctrl.last_result, str) else None
        self.geo_ctrl.last_result = None
        if label and ok:  # lo que acaba de crear (o renombrar) la edición
            b = session.geo.find_body(label)
            kb = b.index if b is not None else None
        elif old_sel and mapping is not None:  # lo que estaba seleccionado, con su número nuevo
            kb = mapping.get(old_sel)
        self.select_body(kb or 0)

    def add_body_dialog(self, container: int | None, center=None, module: bool = False,
                        glue_target: int | None = None, glue_face: str | None = None) -> None:
        """Diálogo para añadir un cuerpo o un módulo dentro de ``container`` (o de la envolvente).

        Con ``glue_target``, el nuevo va pegado a esa cara (``glue_face``) del
        elemento, en el mismo contenedor que él.
        """
        if not self._can_edit():
            return
        s = self.session
        geo = s.geo
        cont = ge.container_of(geo, container)
        if glue_target is not None and not glue_face:
            glue_face = self._face_near(glue_target, center)
        if center is None:
            if glue_target is not None:
                lo, hi = self.element_extent(glue_target)
                center = 0.5 * (np.asarray(lo) + np.asarray(hi))
            elif container and container <= geo.nbodys and geo.body(container).origin != "enclosure":
                center = self._body_point(container)
            else:
                lo, hi = s.bounds
                center = 0.5 * (np.asarray(lo) + np.asarray(hi))
        if not ge.container_chain(geo, ge.logical_parent(geo, glue_target) if glue_target else cont):
            QMessageBox.information(self, "ARGUS", "No se puede añadir aquí: está dentro de un clon, de un fichero "
                                    "INCLUDE o de la caja CT (edítelos en su módulo o fichero de origen).")
            return
        ref = glue_target if glue_target is not None else cont
        mat = geo.body(ref).material if ref <= geo.nbodys and geo.body(ref).material > 0 else None
        dlg = AddBodyDialog(self, s, cont, center, self.material_names, material=mat, module=module,
                            glue_target=glue_target, glue_face=glue_face, extent_fn=self.element_extent)
        if not self._exec_dialog(dlg):
            return
        try:
            prim, fn = dlg.primitive(), dlg.edit_fn()
        except (ge.EditError, ValueError) as exc:
            QMessageBox.information(self, "ARGUS", f"No se ha añadido nada:\n\n{exc}")
            return
        module = dlg.is_module()
        what = "un módulo" if module else ge.PRIMITIVES[prim.kind].lower()
        label = self.edit_geometry(f"Añadir {what}", fn)
        if label:
            kind = "el módulo" if module else "el cuerpo"
            self._report(f"Añadido {kind} «{str(label).strip()}» ({prim.describe()}). "
                         "Guarde la geometría para escribir el .geo.")

    # -- detectores -------------------------------------------------------------------------
    def _detector_input_doc(self):
        """El .in al que se añaden los detectores (el abierto, si usa esta geometría)."""
        return self.input.doc if self.input is not None and self._input_uses_geometry() else None

    def _add_detectors(self, choice, groups: list[list[str]], what: str) -> str:
        """Pone los cuerpos nuevos en sus detectores de penhmain (en el paso de deshacer de la geometría)."""
        from .detector_dialogs import apply_choice

        if choice.kind == "none" or self.input is None or not groups:
            return ""
        created = []
        try:
            if self.input.edit(what, lambda d: created.append(apply_choice(d, choice, groups))):
                self._link_pair()
        except ValueError as exc:
            QMessageBox.warning(self, "ARGUS", f"Se ha añadido a la geometría, pero no al detector:\n\n{exc}")
            return ""
        from .sim_outputs import EnergySection, ImpactSection

        section = EnergySection if choice.kind == "energy" else ImpactSection
        self.sim_panel.refresh()
        self.sim_panel.frame(section).set_expanded(True)
        n = created[0] if created else 0
        kind = "de energía depositada" if choice.kind == "energy" else "de impacto"
        if n:
            return f" En el .in, {n} detector{'es' if n > 1 else ''} {kind} nuevo{'s' if n > 1 else ''}."
        return f" En el .in, en el detector {kind} {choice.target}."

    def _dialog_context(self, container: int | None, center):
        """(sesión, contenedor, centro, material) para los diálogos de añadir, o None si aquí no se puede."""
        if not self._can_edit():
            return None
        s = self.session
        geo = s.geo
        cont = ge.container_of(geo, container)
        if center is None:
            if container and container <= geo.nbodys and geo.body(container).origin != "enclosure":
                center = self._body_point(container)
            else:
                lo, hi = s.bounds
                center = 0.5 * (np.asarray(lo) + np.asarray(hi))
        if not ge.container_chain(geo, cont):
            QMessageBox.information(self, "ARGUS", "No se puede añadir aquí: está dentro de un clon, de un fichero "
                                    "INCLUDE o de la caja CT (edítelos en su módulo o fichero de origen).")
            return None
        mat = geo.body(cont).material if cont <= geo.nbodys and geo.body(cont).material > 0 else None
        return s, cont, center, mat

    def add_detector_dialog(self, container: int | None, center=None) -> None:
        """«Añadir un detector»: un cuerpo de una forma habitual y, en el .in, su detector de penhmain."""
        from .detector_dialogs import DetectorDialog

        ctx = self._dialog_context(container, center)
        if ctx is None:
            return
        s, cont, center, mat = ctx
        dlg = DetectorDialog(self, s, cont, center, self.material_names, material=mat,
                             input_doc=self._detector_input_doc(), extent_fn=self.element_extent)
        if not self._exec_dialog(dlg):
            return
        try:
            prim, fn = dlg.primitive(), dlg.edit_fn()
            choice = dlg.detector_choice()
        except (ge.EditError, ValueError) as exc:
            QMessageBox.information(self, "ARGUS", f"No se ha añadido nada:\n\n{exc}")
            return
        note: list[str] = []
        label = self.edit_geometry("Añadir un detector", fn,
                                   then=lambda lab: note.append(self._add_detectors(choice, [[str(lab)]],
                                                                                    "Detector nuevo")))
        if label:
            self._report(f"Añadido el detector «{str(label).strip()}» ({prim.describe()})."
                         + "".join(note) + " Guarde la geometría para escribir el .geo.")

    def add_cell_dialog(self, container: int | None, center=None) -> None:
        """«Añadir una celda de detectores»: una caja llena de detectores iguales (y sus detectores de penhmain)."""
        from ..pengeom import detcell as dc
        from .detector_dialogs import CellDialog

        ctx = self._dialog_context(container, center)
        if ctx is None:
            return
        s, cont, center, mat = ctx
        dlg = CellDialog(self, s, cont, center, self.material_names, material=mat,
                         input_doc=self._detector_input_doc())
        if not self._exec_dialog(dlg):
            return
        try:
            plan = dlg.plan()
            mat_det, mat_box, name = dlg.element_material(), dlg.housing_material(), dlg.cell_name()
            choice = dlg.detector_choice()
        except (ge.EditError, ValueError) as exc:
            QMessageBox.information(self, "ARGUS", f"No se ha añadido nada:\n\n{exc}")
            return
        cont = dlg.container()
        note: list[str] = []

        def then(res) -> None:
            groups = [[res.elements[n] for n in g] for g in dc.detector_groups(plan, choice.grouping)]
            note.append(self._add_detectors(choice, groups if choice.kind != "none" else [], "Detectores de la celda"))

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = self.edit_geometry("Añadir una celda de detectores",
                                     lambda doc, g: dc.add_cell(doc, g, plan, mat_det, mat_box, cont, name,
                                                                parinp=s.parinp), then=then)
        finally:
            QApplication.restoreOverrideCursor()
        if res:
            self._report(f"Añadida la celda «{res.cell.strip()}» con {len(res.elements)} detectores (etiquetas "
                         f"{res.elements[0].strip()} a {res.elements[-1].strip()}; en el .geo, su fila y su columna)."
                         + "".join(note) + " penhmain da la energía depositada en cada cuerpo en penhmain-res.dat. "
                         "Guarde la geometría para escribir el .geo.")

    # -- módulos: pegar y convertir ---------------------------------------------------------
    def _tracer(self):
        s = self.session
        t = self.view3d.tracer
        if t is not None and t.geo is s.geo:
            return t
        if self._own_tracer is None or self._own_tracer.geo is not s.geo:
            self._own_tracer = RayTracer(s.geo)
        return self._own_tracer

    def _extent_caches(self) -> None:
        """Las cajas guardadas son de una geometría: con otra, se empieza de nuevo."""
        s = self.session
        if self._extent_geo is not s.geo:
            self._extent_geo = s.geo
            self._extent_cache = {}
            self._analytic_cache = {}
            self._parts_cache = {}
            self._source_cache = {}
            self._bound_cache = {}

    def analytic_extent(self, kb: int):
        """Cota analítica [lo, hi] de un cuerpo o módulo (sin rayos, al momento; puede ser mayor que el cuerpo o
        infinita). El cuerpo está siempre dentro."""
        self._extent_caches()
        box = self._analytic_cache.get(kb)
        if box is None:
            box = self._analytic_cache[kb] = element_box(self.session.geo, kb)
        return box

    def element_extent(self, kb: int):
        """Caja exacta [lo, hi] de un cuerpo o módulo (con todo lo que tiene dentro)."""
        s = self.session
        self._extent_caches()
        ext = self._extent_cache.get(kb)
        if ext is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ext = element_extent(s.geo, self._tracer(), kb, s.bounds)
            finally:
                QApplication.restoreOverrideCursor()
            self._extent_cache[kb] = ext
        return ext

    def _face_near(self, kb: int, point) -> str:
        if point is None:
            return "+x"
        return ge.nearest_face(self.element_extent(kb), point)

    def _can_glue(self, moving: int, target: int) -> bool:
        geo = self.session.geo
        if not moving or moving > geo.nbodys or not self._placeable(moving):
            return False
        return target in glue_candidates(geo, moving)

    def _placeable(self, kb: int) -> bool:
        """¿Es un módulo o un clon que se puede mover y girar como un todo?"""
        s = self.session
        if s is None or not s.ok or not kb or kb > s.geo.nbodys:
            return False
        try:
            pl.placeable(s.geo, kb)
        except ge.EditError:
            return False
        return not pl.starred_surfaces(s.geo, kb)

    def _is_clone(self, kb: int) -> bool:
        s = self.session
        if s is None or not s.ok or not kb or kb > s.geo.nbodys:
            return False
        b = s.geo.body(kb)
        return b.origin == "clone" and b.element is b.clone_element and b.document is s.geo.document

    def glue_module_dialog(self, moving: int, target: int | None = None, face: str | None = None) -> None:
        """Mueve el módulo ``moving`` hasta que toca una cara de otro elemento."""
        if not self._can_edit() or not 1 <= moving <= self.session.geo.nbodys:
            return
        s = self.session
        if not glue_candidates(s.geo, moving):
            QMessageBox.information(self, "ARGUS", "No hay ningún elemento al que pegarlo.")
            return
        dlg = GlueDialog(self, s, moving, self.element_extent, target=target, face=face)
        if not self._exec_dialog(dlg):
            return
        delta, tgt = dlg.delta(), dlg.target()
        name, tname = body_title(s.geo, moving), body_title(s.geo, tgt)
        done = self.edit_geometry(f"Pegar {name} a {tname}", dlg.edit_fn())
        if done:
            d = ", ".join(f"{v:g}" for v in delta)
            self._report(f"{name} pegado a {tname} (desplazado {d} cm).")

    def convert_to_module(self, kb: int) -> None:
        """Convierte un cuerpo en módulo (mismo volumen y material; lo que tiene dentro pasa a ser hijas)."""
        if not self._can_edit() or not 1 <= kb <= self.session.geo.nbodys:
            return
        s = self.session
        chk = ge.check_convert(s.geo, s.locator, kb, s.bounds)
        name = body_title(s.geo, kb)
        if not chk.ok:
            QMessageBox.information(self, "ARGUS", f"No se puede convertir {name} en módulo:\n\n{chk.message}")
            return
        label = s.geo.balias(kb)
        notes = self.edit_geometry(f"Convertir {name} en módulo",
                                   lambda doc, g: ge.convert_to_module(doc, g, g.find_body(label).index))
        if notes:
            self._report(" ".join(notes) + " Ahora se puede mover y pegar a otros.")

    # -- punto 4: mover y girar, duplicar, envolver, superficies y etiquetas ----------------------
    def _update_module_handle(self, kb: int | None) -> None:
        """Asas de mover y girar en el corte para el módulo o clon seleccionado.

        La caja exacta del módulo se calcula con rayos y en una geometría grande tarda (segundos en la `mlc`): en
        «Geometrías complejas», donde se va de bloque en bloque, se calcula un momento después de elegirlo, y solo
        la del último elegido."""
        if self.geo_ctrl is not None and self.geo_ctrl.in_live:
            return
        self._handle_pending = None
        if self.geo_ctrl is None or not kb or not self._placeable(kb) or self.complex.manual:
            self.view.set_module_handle(None)
            return
        s = self.session
        cached = getattr(self, "_extent_cache", {}).get(kb) if getattr(self, "_extent_geo", None) is s.geo else None
        if cached is None and self.complex.active:
            self.view.set_module_handle(None)
            self._handle_pending = (kb, s.geo)
            timer = getattr(self, "_handle_timer", None)
            if timer is None:
                timer = self._handle_timer = QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(250)
                timer.timeout.connect(self._deferred_module_handle)
            timer.start()
            return
        ext = self.element_extent(kb)
        self.view.set_module_handle(kb, pl.element_center(ext), ext)

    _handle_pending = None

    def _deferred_module_handle(self) -> None:
        pending, self._handle_pending = self._handle_pending, None
        s = self.session
        if pending is None or s is None or s.geo is not pending[1] or self.view.selected != pending[0] \
                or self._preview_session is not None or not self._can_edit():
            return
        ext = self.element_extent(pending[0])
        self.view.set_module_handle(pending[0], pl.element_center(ext), ext)

    @staticmethod
    def _motion(action) -> tuple:
        if action[0] == "move":
            return pl.translation(action[1])
        return pl.rotation_about(action[1], action[2], action[3])

    def _on_module_dragged(self, kb: int, action, finished: bool) -> None:
        """Arrastre de las asas de un módulo en el corte: en vivo y, al soltar, un paso de deshacer.

        Los movimientos del ratón se agrupan: si llegan más rápido de lo que se
        tarda en recalcular la geometría, solo se aplica el último.
        """
        gc = self.geo_ctrl
        if gc is None:
            return
        if self._mdrag is None:
            s = self.session
            label = s.geo.balias(kb) if s is not None and s.ok and kb <= s.geo.nbodys else ""
            if not label:
                self.view.abort_drag()
                return
            self._mdrag = {"label": label, "kb": kb, "extent": self.element_extent(kb), "start": s,
                           "pending": None}
        md = self._mdrag
        md["pending"] = action
        if not finished:
            if not md.get("scheduled"):
                md["scheduled"] = True
                QTimer.singleShot(0, self._apply_module_drag)
            return
        if not self._apply_module_drag():
            return
        self._mdrag = None
        start = md["start"]
        what = "mover" if action[0] == "move" else "girar"
        name = body_title(start.geo, md["kb"])
        chk = pl.check_motion(start.geo, start.locator, md["kb"], self._motion(action), md["extent"])
        if not chk.ok:
            gc.cancel_live()
            QMessageBox.information(self, "ARGUS", f"No se ha podido {what} {name}:\n\n{chk.message}")
            return
        try:
            done = gc.commit_live(f"{what.capitalize()} {name}", check=pl.motion_guard(md["label"], start.bounds))
        except ge.EditError as exc:  # movería también otros cuerpos: se deshace el arrastre
            QMessageBox.information(self, "ARGUS", f"No se ha podido {what} {name}:\n\n{exc}")
            return
        if done:
            self._report(f"{name}: {'movido' if what == 'mover' else 'girado'}. Deshacer con Ctrl+Z.")

    def _apply_module_drag(self) -> bool:
        """Aplica en vivo el último movimiento pedido del arrastre en curso; False si no se puede."""
        md = self._mdrag
        gc = self.geo_ctrl
        if md is None or gc is None:
            return False
        md["scheduled"] = False
        action = md["pending"]
        if action is None:
            return True
        md["pending"] = None
        start = md["start"]
        try:
            # separate: el arrastre en vivo da antes al módulo copias propias de las superficies que comparta
            values = pl.moved_values(start.geo, md["kb"], self._motion(action), start.parinp, separate=True)
            gc.live_transform(md["label"], values)
        except ge.EditError as exc:
            self._mdrag = None
            self.view.abort_drag()
            gc.cancel_live()
            self.statusBar().showMessage(f"No se puede mover: {exc}", 10000)
            return False
        return True

    def rotate_element(self, kb: int, axis, angle: float) -> None:
        """Gira un módulo o clon ``angle`` grados alrededor de ``axis`` (global) por su centro."""
        if self.geo_ctrl is None or not self._placeable(kb):
            return
        s = self.session
        ext = self.element_extent(kb)
        center = pl.element_center(ext)
        name = body_title(s.geo, kb)
        if not (math.isfinite(float(angle)) and np.all(np.isfinite(center))):
            self.statusBar().showMessage(f"No se ha girado {name}: el ángulo o el centro no son números finitos.",
                                         10000)
            return
        motion = pl.rotation_about(axis, angle, center)
        chk = pl.check_motion(s.geo, s.locator, kb, motion, ext)
        if not chk.ok:
            QMessageBox.information(self, "ARGUS", f"No se ha girado {name}:\n\n{chk.message}")
            return
        label = s.geo.balias(kb)
        done = self.edit_geometry(f"Girar {name} {angle:g}°",
                                  lambda doc, g: pl.move_element(doc, g, g.find_body(label).index, motion,
                                                                 s.parinp, bounds=s.bounds) or True)
        if done:
            self._report(f"{name} girado {angle:g}° alrededor de su centro.")

    def duplicate_dialog(self, kb: int) -> None:
        """Copias independientes o clones de un cuerpo o módulo."""
        if not self._can_edit() or not kb:
            return
        s = self.session
        if kb > s.geo.nbodys or s.geo.body(kb).origin == "enclosure":
            return
        if not self._is_clone(kb) and self._refuse_not_editable(kb, "duplicar"):
            return  # p. ej. Ctrl+D con un cuerpo de un INCLUDE seleccionado
        dlg = DuplicateDialog(self, s, kb, self.element_extent)
        if not self._exec_dialog(dlg):
            return
        linked = dlg.linked()
        name = body_title(s.geo, kb)
        labels = self.edit_geometry(f"{'Clonar' if linked else 'Duplicar'} {name}", dlg.edit_fn())
        if labels:
            what = ("clon" if linked else "copia") + ("es" if linked and len(labels) > 1 else
                                                      ("s" if len(labels) > 1 else ""))
            self._report(f"{len(labels)} {what} de {name}: " + ", ".join(f"«{x.strip()}»" for x in labels) + "."
                         + "".join(" " + n for n in getattr(labels, "notes", [])))
            b = self.session.geo.find_body(labels[0])
            if b is not None:
                self.select_body(b.index)

    def wrap_dialog(self, kb: int) -> None:
        """Un módulo nuevo alrededor de este elemento y de los que se elijan a su lado."""
        if not self._can_edit() or not kb:
            return
        s = self.session
        if kb > s.geo.nbodys or s.geo.body(kb).origin == "enclosure":
            return
        own_ct = kb == getattr(s.geo, "ct_body", None) and s.geo.body(kb).document is s.geo.document
        if not own_ct and not self._is_clone(kb) and self._refuse_not_editable(kb, "envolver"):
            return
        dlg = WrapDialog(self, s, kb, self.element_extent, self.material_names)
        if not self._exec_dialog(dlg):
            return
        labels = [s.geo.balias(k) for k in dlg.chosen()]
        try:
            fn = dlg.edit_fn()
        except (ge.EditError, ValueError) as exc:
            QMessageBox.information(self, "ARGUS", f"No se ha envuelto nada:\n\n{exc}")
            return
        lab = self.edit_geometry("Envolver en un módulo", fn)
        if lab:
            self._report(f"Módulo «{str(lab).strip()}» alrededor de " + ", ".join(f"«{x.strip()}»" for x in labels)
                         + ": ya se pueden mover, girar o duplicar juntos.")

    def add_surface_dialog(self, kb: int) -> None:
        """Limitar un cuerpo con otra superficie (una que existe, un plano nuevo o una cuádrica)."""
        if not self._can_edit() or not kb:
            return
        s = self.session
        try:
            ge.editable_body(s.geo, kb)
        except ge.EditError as exc:
            QMessageBox.information(self, "ARGUS", str(exc))
            return
        dlg = SurfaceDialog(self, s, kb, point=self._body_point(kb))
        if not self._exec_dialog(dlg):
            return
        from ..pengeom.overlap import guard

        fn = dlg.edit_fn()
        label = s.geo.balias(kb)
        fn.check = guard(s.bounds, [label])
        notes = self.edit_geometry(f"Añadir una superficie a {body_title(s.geo, kb)}", fn)
        if isinstance(notes, list) and notes:
            self._report(" ".join(notes))

    def materialize_clone(self, kb: int) -> None:
        """Convierte un clon en una copia independiente (mismo sitio y misma etiqueta)."""
        if self.geo_ctrl is None or not self._is_clone(kb):
            return
        s = self.session
        label = s.geo.balias(kb)
        name = body_title(s.geo, kb)
        holder: dict = {}

        def fn(doc, g):
            notes, holder["key_map"] = materialize_clone(doc, g, g.find_body(label).index, s.parinp)
            holder["notes"] = notes
            return label

        done = self.edit_geometry(f"Convertir {name} en copia independiente", fn,
                                  key_map=lambda: holder.get("key_map"))
        if done:
            self._report(f"{name} es ahora una copia independiente del módulo: se puede editar por separado. "
                         + " ".join(holder.get("notes", [])))

    def rename_dialog(self, kb: int) -> None:
        """Cambia la etiqueta de un cuerpo, módulo o clon (y la del .in, si lo nombra por etiqueta)."""
        from PySide6.QtWidgets import QInputDialog

        if not self._can_edit() or not 1 <= kb <= self.session.geo.nbodys:
            return
        s = self.session
        try:
            el = sf.renamable_body(s.geo, kb)
        except ge.EditError as exc:
            QMessageBox.information(self, "ARGUS", str(exc))
            return
        name = body_title(s.geo, kb)
        text, ok = QInputDialog.getText(self, "Cambiar la etiqueta",
                                        f"Etiqueta nueva de {name} (hasta 4 caracteres):", text=el.label.strip())
        if not ok or text.strip() == el.label.strip():
            return
        try:
            new = sf.label_text(text)
        except ge.EditError as exc:
            QMessageBox.information(self, "ARGUS", str(exc))
            return
        old = el.label
        done = self.edit_geometry(f"Etiqueta «{old.strip()}» → «{new.strip()}»",
                                  lambda doc, g: sf.rename_body(doc, g, g.find_body(old).index, new)[1],
                                  renames={old: new})
        if done:
            self._report(f"{name} se llama ahora «{new.strip()}».")

    def check_overlaps_now(self) -> None:
        """Busca solapes y salientes en toda la geometría (con puntos de muestra)."""
        from ..pengeom.extent import analytic_box
        from ..pengeom.overlap import check_overlaps, sample_box

        s = self.session
        if s is None or not s.ok:
            return
        geo = s.geo
        lo, hi = (np.asarray(v, dtype=float) for v in s.bounds)
        rng = np.random.default_rng(0)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            chunks = [sample_box(lo, hi, 40000, rng)]
            for b in geo.bodies:
                if b.origin == "enclosure":
                    continue
                a_lo, a_hi = analytic_box(geo, b.index)
                if np.all(np.isfinite(a_lo)) and np.all(np.isfinite(a_hi)) and np.all(a_hi > a_lo):
                    chunks.append(sample_box(a_lo, a_hi, 60, rng))
            pts = np.vstack(chunks)[:120000]
            rep = check_overlaps(geo, pts, 1e-9 * float(np.max(hi - lo)))
        finally:
            QApplication.restoreOverrideCursor()
        if rep.ok:
            QMessageBox.information(self, "ARGUS", f"No se ha visto ningún solape en {len(pts)} puntos de muestra.")
            return
        lines = []
        for (a, b), f in sorted(rep.overlaps.items(), key=lambda kv: -kv[1])[:12]:
            lines.append(f"• {rep.names.get(a, a)} y {rep.names.get(b, b)} se solapan ({100 * f:.3g} % de los puntos)")
        for (a, b), f in sorted(rep.escapes.items(), key=lambda kv: -kv[1])[:12]:
            lines.append(f"• {rep.names.get(a, a)} asoma fuera de {rep.names.get(b, b)} ({100 * f:.3g} %)")
        QMessageBox.warning(self, "ARGUS", "PENGEOM no lo comprueba, pero en estos sitios una partícula puede cruzar "
                            "una frontera sin verla:\n\n" + "\n".join(lines))

    def new_geometry(self, start_with: str = "body") -> None:
        """Una geometría desde cero (vacía o con un primer cuerpo o módulo)."""
        if not self._settle():
            return
        if not self.maybe_save():
            return
        start = Path(str(self.settings.value("last_dir", str(Path.home()))))
        dlg = NewGeometryDialog(self, start / "geometria.geo", start_with)
        if not self._exec_dialog(dlg):
            return
        path = dlg.path()
        doc = new_geometry(dlg.title(), dlg.primitive(), dlg.material_number(), dlg.is_module(), dlg.body_name(),
                           path=str(path))
        try:
            backup_file(path)
            write_latin1(path, write_geo_text(doc))
        except OSError as exc:
            QMessageBox.critical(self, "ARGUS", f"No se puede escribir {path}:\n{exc}")
            return
        # Ya se ha preguntado qué hacer con los cambios sin guardar (y el usuario ha decidido).
        self.open_file(path, with_input=False, ask=False)
        if self.session is None or not _same_path(self.session.path, path):
            return
        if dlg.create_input():
            self.new_input()
        self.statusBar().showMessage(f"Geometría nueva {path.name}. Añada cuerpos y módulos con clic derecho en el "
                                     "corte o en la vista 3D.", 10000)

    # -- TAC (DICOM) -----------------------------------------------------------------------------
    def import_ct(self) -> None:
        """Importa una serie DICOM de TAC como caja de vóxeles (CTBOX) de la geometría abierta (o de una nueva)."""
        from ..ct.importer import add_ctbox, check_ct_box, ct_containers
        from .ct_import_dialog import CtImportDialog

        if not self._settle():
            return
        s = self.session if self.session is not None and self.session.ok else None
        if self.session is not None and not self.session.ok:
            QMessageBox.warning(self, "ARGUS", "La geometría abierta tiene errores: corríjalos antes de importar.")
            return
        if s is not None and s.geo.ct is not None:
            QMessageBox.information(self, "ARGUS", "La geometría ya tiene un CT; PENGEOM admite uno solo (para "
                                    "cambiarlo, elimínelo antes: botón derecho sobre él → Eliminar).")
            return
        if s is None and self.input is not None and not self.maybe_save():
            return  # sin geometría se abrirá una nueva (y el .in abierto se cierra): antes, sus cambios
        containers = []
        if s is not None:
            for kb in ct_containers(s.geo, s.parinp):
                if ge.is_top_level(s.geo, kb):
                    text = "nivel superior (fuera de todo lo demás)" if kb == s.geo.nbodys else \
                        "nivel superior (al lado de lo que hay; PENGEOM añadirá la envolvente)"
                else:
                    text = ge.element_name(s.geo, kb)
                containers.append((kb, text))
        # Los materiales nuevos van al .in abierto solo si usa esta geometría (o si no hay geometría).
        in_ctrl = self.input if self.input is not None and (s is None or self._input_uses_geometry()) else None
        if s is None:
            in_ctrl = None  # sin geometría abierta, la importación crea una geometría (y un .in) nuevos
        rows = materials_table(in_ctrl.doc if in_ctrl else None, s.geo if s else None,
                               in_ctrl.folder if in_ctrl else (s.path.parent if s else None), self._penhan_database())
        others = {(iza // 1000, iza % 1000) for iza in distinct_isotopes(rows)}
        start = self.settings.value("ct/last_dir", "") or self.settings.value("last_dir", str(Path.home()))
        dlg = CtImportDialog(self, s, in_ctrl.doc if in_ctrl is not None else None, start,
                             self.material_colors, containers, others,
                             hadron_data_needed(in_ctrl.doc, self._penhan_database()) if in_ctrl is not None
                             else True)
        try:
            if not self._exec_dialog(dlg) or dlg.result is None:
                return
            r = dlg.result
            self.settings.setValue("ct/last_dir", dlg.folder_edit.text())
        finally:
            dlg.deleteLater()  # el diálogo guarda el TAC entero (cientos de MB): no se deja vivo
            self.act_cttrack.setChecked(cttrack_fixed(self.settings))  # se puede cambiar en el diálogo
        name = r.imp.ct_name
        shape = "×".join(str(v) for v in r.built.ct.shape)
        if s is None:
            self._new_geometry_with_ct(r)
            return
        check = check_ct_box(s.geo, s.locator, r.built.frame, r.container)
        if not check.ok:
            QMessageBox.warning(self, "ARGUS", f"El .ct se ha escrito ({r.ct_path}), pero la caja del CT no cabe "
                                f"donde se ha pedido:\n\n{check.message}\n\nElija otra colocación o contenedor y "
                                "vuelva a importar (se reemplazará el .ct).")
            return

        def add(doc, geo):
            return add_ctbox(doc, geo, name, r.built.rotation, r.built.shift, r.container, r.imp.label,
                             r.description, parinp=s.parinp)

        self._group_seq += 1
        self._group = self._group_seq
        try:
            old = self.geo_ctrl.session.geo
            self.geo_ctrl.edit(f"Importar el TAC {name}", add, check=self._follow_guard(add))
            new = self.geo_ctrl.session.geo
            if new is not old and old.ok and new.ok:
                self._follow_geometry(old, new)
            if in_ctrl is not None and r.plan.new:
                def materials(d):
                    for _n, f, desc in r.plan.new:
                        add_material(d, f, desc)

                if in_ctrl.edit("Materiales del TAC", materials):
                    self._link_pair()  # la geometría usa los materiales nuevos: se guardan juntos
        except _Declined:
            return
        except (ge.EditError, ValueError) as exc:
            QMessageBox.warning(self, "ARGUS", f"No se ha podido añadir el TAC:\n\n{exc}")
            return
        finally:
            self._group = 0
        self._hide_ct_air()
        self._family_colors(r)
        self.frame_ct()
        self.statusBar().showMessage(f"TAC importado: {name} ({shape} vóxeles). Genere los .mat de los tejidos con "
                                     "«Generar .mat y ENDF…» (panel Materiales) o con penhmat a mano (al guardar el "
                                     ".in, materiales.txt dice cómo)."
                                     + split_summary(r.built.subdivision), 20000)

    def _family_colors(self, r=None) -> None:
        """Tras importar un TAC: los trozos de un tejido partido por densidad, del color de su primer material (se
        ven como un tejido), también en la tabla de materiales."""
        defaults = self._default_colors(self.session)
        self.materials.default_colors = dict(defaults)
        colors = dict(self.material_colors)
        colors.update(defaults)
        self.materials.colors = dict(colors)
        self.materials.refresh()
        if colors != self.material_colors:
            self._on_colors_changed(colors)

    @staticmethod
    def _default_colors(session) -> dict[int, str]:
        """Colores por defecto que no son los de la paleta (``settings.default_material_colors``)."""
        from .settings import default_material_colors

        return default_material_colors(session)

    def prepare_simulation_folder(self) -> None:
        """Copia en una carpeta todo lo que necesita penhmain para el .in abierto (nunca sobrescribe)."""
        from .sim_folder_dialog import SimFolderDialog

        if not self._settle():
            return
        ctrl = self.input
        if ctrl is None:
            return
        dirty = ctrl.dirty or ctrl.path is None or (self.geo_ctrl is not None and self.geo_ctrl.dirty)
        if dirty:
            ans = QMessageBox.question(
                self, "ARGUS", "Hay cambios sin guardar y penhmain usará los ficheros guardados. ¿Guardarlos antes "
                "de preparar la carpeta?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if ans != QMessageBox.StandardButton.Save or not self.save_all():
                return
        if ctrl.path is None:
            return
        s = self.session
        dlg = SimFolderDialog(self, Path(ctrl.path), ctrl.doc, self.settings,
                              geo=s.geo if s is not None and s.ok else None)
        try:
            dlg.exec()
            plan, copied = dlg.plan, dlg.copied
        finally:
            dlg.deleteLater()
        self.materials.refresh()  # «Generar los .mat que faltan…» puede haber añadido ficheros junto al .in
        self._refresh_messages()
        if copied is None or plan is None:
            return
        if not plan.cancelled:
            remember_sim_folder(self.settings, Path(ctrl.path), Path(plan.dest))
        n_copied = plural(len(copied), "fichero copiado", "ficheros copiados")
        if plan.cancelled:
            self.statusBar().showMessage(f"Preparación cancelada: {n_copied} en {plan.dest}; la copia a medias se ha "
                                         "borrado.", 12000)
        elif plan.problems:
            self.statusBar().showMessage(f"Carpeta de simulación preparada con problemas: {plan.dest} ({n_copied}, "
                                         f"{len(plan.problems)} por resolver).", 12000)
        else:
            self.statusBar().showMessage(f"Carpeta de simulación preparada: {plan.dest} ({n_copied}).", 12000)

    # -- resultados --------------------------------------------------------------------------------------------
    def results_window(self):
        """La ventana de resultados (una sola, que se crea la primera vez)."""
        from .results.window import ResultsWindow

        w = getattr(self, "_results_window", None)
        if w is None:
            w = ResultsWindow(None, settings=self.settings)
            w.setWindowIcon(self.windowIcon())
            w.showOnGeometry.connect(self.show_results_on_geometry)
            w.showTracksOnGeometry.connect(self.show_tracks_on_geometry)
            self._results_window = w
        return w

    def show_case_results(self) -> None:
        """Resultados → «Ver los resultados de este caso»: la carpeta del .in o la última carpeta de simulación
        preparada para él (la que tenga resultados más recientes)."""
        ctrl = self.input
        if ctrl is None or ctrl.path is None:
            return
        folder = results_folder_for(self.settings, Path(ctrl.path))
        w = self.results_window()
        in_path = Path(ctrl.path)
        cand = folder / in_path.name
        w.open_folder(folder, cand if cand.is_file() else None)
        w.show()
        w.raise_()
        w.activateWindow()

    def prepare_batch(self, mode: str) -> None:
        """Resultados → «Simulaciones en paralelo…» o «Campaña de simulaciones…» (se prepara, no se lanza)."""
        from .batch_dialog import BatchDialog

        if not self._settle():
            return
        ctrl = self.input
        if ctrl is None:
            return
        dirty = ctrl.dirty or ctrl.path is None or (self.geo_ctrl is not None and self.geo_ctrl.dirty)
        if dirty:
            ans = QMessageBox.question(
                self, "ARGUS", "Hay cambios sin guardar y las simulaciones usarán los ficheros guardados. ¿Guardarlos "
                "antes?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if ans != QMessageBox.StandardButton.Save or not self.save_all():
                return
        if ctrl.path is None:
            return
        dlg = BatchDialog(self, Path(ctrl.path), ctrl.doc, self.settings, mode)
        dlg.openResults.connect(self._follow_batch)
        self._exec_dialog(dlg)
        if dlg.written is not None and not dlg.written.cancelled and dlg.plan is not None:
            # «Ver los resultados de este caso» también mirará allí.
            remember_sim_folder(self.settings, Path(ctrl.path), Path(dlg.plan.dest))

    def _follow_batch(self, dest) -> None:
        w = self.results_window()
        w.open_folder(dest)
        w.show()
        w.raise_()
        w.activateWindow()

    def run_simulation(self) -> None:
        """Simulación → «Ejecutar penhmain» (F9): en la carpeta del .in, en un terminal; después se abre la ventana de
        resultados (que sigue la simulación) y, si se pide, se simula la cascada de partículas."""
        from .run_dialog import RunDialog

        if not self._settle():
            return
        ctrl = self.input
        if ctrl is None:
            QMessageBox.information(self, "ARGUS", "Abra un .in (o cree uno para la geometría) para ejecutarlo.")
            return
        dirty = ctrl.dirty or ctrl.path is None or (self.geo_ctrl is not None and self.geo_ctrl.dirty)
        if dirty:
            ans = QMessageBox.question(
                self, "ARGUS", "penhmain lee los ficheros guardados: hay que guardar los cambios para ejecutarlo. "
                "¿Guardarlos ahora?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if ans != QMessageBox.StandardButton.Save or not self.save_all():
                return
        if ctrl.path is None:
            return
        s = self.session
        dlg = RunDialog(self, ctrl, s, self.settings, save=self.save_all,
                        outputs_bounds=self._matter_box() if s is not None and s.ok else None)
        self._exec_dialog(dlg)
        self.materials.refresh()
        self._refresh_messages()
        if dlg.handle is None:
            return
        in_path = Path(ctrl.path)
        remember_sim_folder(self.settings, in_path, in_path.parent)
        term = dlg.handle.terminal
        msg = (f"penhmain se está ejecutando en {term.name}." if not term.background else
               "No se ha encontrado un terminal: penhmain se ejecuta en segundo plano (su salida va a penhmain.out).")
        if dlg.stash is not None:
            msg += f" Los resultados anteriores están en {dlg.stash.name}."
        w = self.results_window()
        w.open_folder(in_path.parent, in_path)
        if dlg.cascade_n:
            from ..core.numfmt import plural
            from .cascade_ui import start_cascade

            try:
                # Con el mismo penhmain que la simulación (la primera tanda es la simulación misma).
                start_cascade(in_path, showers=dlg.cascade_n, settings=self.settings, exe=dlg.cascade_exe)
                w.show_cascade(in_path)
                msg += f" Simulando la cascada ({plural(dlg.cascade_n, 'lluvia')})."
            except (OSError, ValueError) as exc:
                msg += f" La cascada no se puede simular: {str(exc).rstrip('.')}."
        w.show()
        w.raise_()
        w.activateWindow()
        self.statusBar().showMessage(msg, 20000)

    def show_cascade(self) -> None:
        """Simulación → «Ver la cascada de partículas»: la ventana de resultados de la carpeta del .in, en la
        cascada (si no hay, desde allí se simula)."""
        ctrl = self.input
        if ctrl is None or ctrl.path is None:
            return
        w = self.results_window()
        w.show_cascade(Path(ctrl.path))
        w.show()
        w.raise_()
        w.activateWindow()

    def choose_outputs(self) -> None:
        """Simulación → «Elegir qué resultados pide el .in…»: cada resultado con su editor (la matriz de dosis, los
        detectores…), sin cerrar la ventana: la matriz se ve y se mueve en el corte y en 3D mientras."""
        from .outputs_dialog import OutputsDialog

        if not self._settle():
            return
        if self.input is None:
            return
        s = self.session if self.session is not None and self.session.ok else None
        bounds = self._matter_box() if s is not None else None
        dlg = getattr(self, "_outputs_dialog", None)
        if dlg is None:
            dlg = OutputsDialog(self, self.input, s, bounds=bounds)
            dlg.showGridRequested.connect(self.show_dose_grid)
            dlg.fitGridRequested.connect(self.fit_dose_grid)
            self._outputs_dialog = dlg
        else:
            dlg.set_context(self.input, s, bounds)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _outputs_context(self, ctrl, session) -> None:
        """El diálogo de los resultados (si está abierto) sigue al .in y a la geometría de la ventana."""
        dlg = getattr(self, "_outputs_dialog", None)
        if dlg is None:
            return
        if ctrl is None:
            dlg.close()
            return
        if dlg.isVisible() or dlg.ctrl is not ctrl:
            s = session if session is not None and session.ok else None
            dlg.set_context(ctrl, s, self._matter_box() if s is not None else None)

    def open_results_folder(self) -> None:
        w = self.results_window()
        w.show()
        w.raise_()
        w.open_folder_dialog()

    def results_profile(self, point, direction, name: str) -> None:
        """Perfil del mapa que se ve sobre el corte, por ``point`` a lo largo de ``direction`` (u o v del corte), en
        toda la anchura de la vista; se muestra y se mide en la ventana de resultados."""
        from ..render.dose_overlay import segment_profile

        ov = self.view.results_overlay
        if ov is None:
            return
        d = np.asarray(direction, dtype=float)
        d = d / np.linalg.norm(d)
        p = np.asarray(point, dtype=float)
        w, h = self.view.width(), self.view.height()
        half = 0.5 * max(w, h) * self.view.scale * 1.5
        k = int(np.argmax(np.abs(d)))  # la coordenada del eje en ese punto (x, y o z) como origen del eje del perfil
        start = p - half * d
        try:
            c = segment_profile(ov, start, p + half * d, n=int(min(max(2 * max(w, h), 400), 4000)),
                                axis_name=name, x0=float(start[k]) * float(np.sign(d[k]) or 1.0))
        except ValueError as exc:
            self.statusBar().showMessage(f"Sin perfil: {exc}", 8000)
            return
        others = [f"{'xyz'[i]} = {p[i]:.4g}" for i in range(3) if i != k]
        c.title = f"{c.y_name} a lo largo de {name} ({', '.join(others)} cm)"
        rw = self.results_window()
        rw.show_profile(c)
        rw.show()
        rw.raise_()

    def show_tracks_on_geometry(self, tracks) -> None:
        """Trayectorias de LTRACK (lista de Tracks) sobre los cortes 2D y en la vista 3D, o None para quitarlas."""
        from ..core.numfmt import fmt_int

        self.view.set_tracks(tracks)
        self.view3d.set_tracks(tracks)
        if tracks:
            n = sum(t.count for t in tracks)
            note = getattr(tracks, "note", "")
            points = sum(len(t.points) for t in tracks)
            if not note and points > min(self.view.MAX_TRACK_POINTS, self.view3d.MAX_TRACK_POINTS):
                note = "no caben todas: de cada tipo de partícula, las primeras"
            self.statusBar().showMessage(f"Trayectorias sobre la geometría: {fmt_int(n)}"
                                         + (f" ({note})" if note else ""), 15000 if note else 8000)

    def show_results_on_geometry(self, overlay) -> None:
        """Un mapa de la ventana de resultados (DoseOverlay) sobre los cortes, o None para quitarlo."""
        if overlay is None:
            self.view.set_results_overlay(None)
            return
        s = self.session
        if overlay.grid.geometry == "ct":
            from ..pengeom.ct import ct_frame

            geo = s.geo if s is not None and s.ok else None
            overlay.frame = ct_frame(geo) if geo is not None else None
            problem = overlay.check_ct(geo.ct if geo is not None else None)
            if problem:
                self.view.set_results_overlay(None)
                self.statusBar().showMessage(problem, 15000)
                return
        self.view.set_results_overlay(overlay)
        if s is None or not s.ok:
            self.statusBar().showMessage("Abra la geometría de la simulación para ver el mapa sobre sus cortes.",
                                         10000)
        else:
            self.statusBar().showMessage(f"Sobre los cortes: {overlay.title}", 8000)

    def frame_ct(self) -> None:
        """Lleva el corte 2D al centro del CT y encuadra el CT en 2D y en 3D."""
        s = self.session
        if s is None or not s.ok or s.geo.ct is None:
            return
        from ..pengeom.ct import ct_frame

        c = ct_frame(s.geo).corners()
        lo, hi = c.min(axis=0), c.max(axis=0)
        k = self.view.plane.depth_axis
        if k is not None:
            self.view.set_depth(float(0.5 * (lo[k] + hi[k])))
            self._sync_depth_widgets()
        keep = self.view.fit_box
        self.view.fit_box = (lo, hi)
        self.view.fit()
        self.view.fit_box = keep
        w, h = max(1, self.view3d.area.width()), max(1, self.view3d.area.height())
        self.view3d.camera.fit(lo, hi, w / h)
        self.view3d.fitted = self.view3d.isVisible()
        self.view3d._camera_changed()

    def penhan_programs(self) -> None:
        """Opciones → «Programas de penhan…»: la carpeta de penhan, penhmat y penhmain del usuario (ADR-013)."""
        from .penhan_dialogs import PenhanSetupDialog

        start = self.input.folder if self.input is not None else (self.session.path.parent if self.session else None)
        if self._exec_dialog(PenhanSetupDialog(self, self.settings, start=start)):
            self.materials.refresh()
            self.statusBar().showMessage("Guardado dónde están los programas de penhan.", 6000)

    def complete_materials(self) -> None:
        """Panel Materiales → «Generar .mat y ENDF…»: los .mat que faltan, con el penhmat del usuario, y los ENDF
        que necesitan, en la carpeta del .in (nunca se sustituye nada)."""
        from .penhan_dialogs import CompleteMaterialsDialog, PenhanSetupDialog
        from .settings import penhan_setup

        if not self._settle():
            return
        ctrl = self.input
        if ctrl is None:
            return
        if ctrl.path is None or ctrl.folder is None:
            QMessageBox.information(self, "ARGUS", "Guarde primero el .in: los .mat y los ENDF van a su carpeta.")
            return
        if not penhan_setup(self.settings).configured:
            self._exec_dialog(PenhanSetupDialog(self, self.settings, start=ctrl.folder,
                                                reason="Para generar los .mat, diga dónde está su penhmat (o que no "
                                                       "hace falta)."))
        s = self.session
        geo = s.geo if s is not None and s.ok else None
        dlg = CompleteMaterialsDialog(self, ctrl.doc, geo, ctrl.folder, self.settings, in_name=ctrl.name,
                                      assign=self.assign_materials)
        self._exec_dialog(dlg)
        plan = dlg.finished_plan
        if plan is not None and any(j.status == "done" for j in plan.mats) and not ctrl.dirty:
            self._write_materials_txt()  # ya no faltan: materiales.txt lo dice (con cambios sin guardar, al guardar)
        self.materials.refresh()
        self._refresh_messages()
        if plan is not None:
            from ..penhan.matgen import summary

            self.statusBar().showMessage(" ".join(summary(plan)) or "No había nada que generar ni copiar.", 12000)

    def _on_cttrack(self, on: bool) -> None:
        """Opciones → «Mi penhmain tiene corregido CTTRACK»: se recuerda y cambian los avisos."""
        set_cttrack_fixed(bool(on), self.settings)
        self._refresh_messages()
        self.statusBar().showMessage(
            "Se supone que su penhmain tiene corregido CTTRACK: los TAC se importan sin partir los tejidos por "
            "densidad." if on else "Se supone el penhmain de serie: los TAC se importan partiendo los tejidos por "
            "densidad (CTTRACK).", 8000)

    def _hide_ct_air(self) -> None:
        """Oculta en 3D el aire del CT (si no, la caja se ve llena de aire)."""
        from .materials_panel import ct_air_hidden

        hide = ct_air_hidden(self.session)
        if hide:
            vis = {**self.materials.visibility, **hide}
            self.materials.set_visibility(vis)
            self.view3d.set_material_visibility(vis)

    def _new_geometry_with_ct(self, r) -> None:
        """Sin geometría abierta: una geometría nueva con solo la caja del CT."""
        from ..ct.importer import add_ctbox
        from ..pengeom.edit import compile_checked

        path = r.geo_path
        doc = new_geometry(r.description or "TAC importado con ARGUS", None, path=str(path))
        try:
            geo = compile_checked(doc, None, path.parent)
            add_ctbox(doc, geo, r.imp.ct_name, r.built.rotation, r.built.shift, None, r.imp.label, r.description)
            backup_file(path)
            write_latin1(path, write_geo_text(doc))
        except (ge.EditError, OSError) as exc:
            QMessageBox.critical(self, "ARGUS", f"No se puede crear la geometría {path}:\n{exc}")
            return
        self.open_file(path, with_input=False, ask=False)
        if self.session is None or not _same_path(self.session.path, path):
            return
        self._hide_ct_air()
        self._family_colors(r)
        self.frame_ct()
        if r.new_input:
            self.new_input()
        self.statusBar().showMessage(f"Geometría nueva {path.name} con el TAC {r.imp.ct_name}."
                                     + split_summary(r.built.subdivision), 20000)

    _last_dialog = None  # el último diálogo mostrado: se libera al mostrar el siguiente o al abrir otro caso

    def _exec_dialog(self, dlg) -> bool:
        self._release_dialog()
        self._last_dialog = dlg
        return dlg.exec() == AddBodyDialog.DialogCode.Accepted

    def _release_dialog(self) -> None:
        """Libera el diálogo anterior (quien lo mostró ya ha terminado con él): si no, cada diálogo se quedaría como
        hijo oculto de la ventana, con la geometría y el .in de entonces.

        Uno que sigue abierto porque el nuevo se abre desde él («Asignar materiales…» → «Composición propia…») se
        libera después, ya cerrado: con deleteLater se borraba en cuanto se cerraba el de dentro, y se cerraba con él
        (revisión de 0.16)."""
        held = list(getattr(self, "_held_dialogs", ()))
        old, self._last_dialog = self._last_dialog, None
        if old is not None:
            held.append(old)
        keep = []
        for d in held:
            try:
                if d.isVisible():
                    keep.append(d)
                else:
                    d.deleteLater()
            except RuntimeError:  # ya destruido
                pass
        self._held_dialogs = keep

    # -- panel «Simulación» ---------------------------------------------------------------
    def set_highlight(self, kbs) -> None:
        """Resalta en el corte y en 3D los cuerpos de un detector (o nada)."""
        self.view.set_highlight(kbs)
        self.view3d.set_highlight(kbs)

    def show_dose_grid(self) -> None:
        """Muestra la rejilla de dosis y pone el corte en su centro."""
        from ..render.source2d import dose_region

        ctrl = self.input
        if ctrl is None or self.session is None or not self.session.ok:
            return
        reg = dose_region(ctrl.doc.model.grids)
        if reg is None:
            return
        act = getattr(self, "act_show_grid", None)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self.view.set_show_dose_grid(True)
        self.view3d.set_show_dose_grid(True)
        k = self.view.plane.depth_axis
        if k is not None:
            if reg.kind == "box":
                c = 0.5 * (reg.lo[k] + reg.hi[k])
            elif reg.kind == "cylinder":
                c = 0.5 * (reg.zlo + reg.zhi) if k == 2 else 0.0
            else:
                c = 0.0
            if math.isfinite(float(c)):  # con Inf o NaN en el .in, el corte no se mueve
                self.view.set_depth(float(c))
            self._sync_depth_widgets()
        if self.view_mode == "3d":
            self.set_view_mode("2d")
        self.dock_sim.raise_()

    def _matter_box(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Caja de lo que tiene material (sin el vacío de alrededor), afinada con rayos."""
        from ..render.scene3d import matter_bounds

        lo, hi = np.asarray(self.view3d.scene_lo, float), np.asarray(self.view3d.scene_hi, float)
        if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
            return None
        tracer = self.view3d.tracer
        if tracer is None:
            return lo, hi
        box = matter_bounds(tracer, lo, hi, tracer.default_visibility())
        return (lo, hi) if box is None else box

    def fit_dose_grid(self) -> None:
        """Ajusta la rejilla de dosis a la caja de los cuerpos con material (conservando los intervalos)."""
        ctrl = self.input
        if ctrl is None or self.session is None or not self.session.ok:
            return
        box = self._matter_box()
        if box is None:
            return
        lo, hi = box
        g = sc.dose_grid(ctrl.doc)

        def r(v, k: int | None = None):
            """Con la escala del eje ``k`` (o, para un radio, de la mayor de la caja)."""
            span = hi[k] - lo[k] if k is not None else float(np.max(np.asarray(hi) - np.asarray(lo)))
            return sc.round_bound(v, span)

        if g.kind == "box":
            new = sc.DoseGrid((r(lo[0], 0), r(hi[0], 0), g.x[2]), (r(lo[1], 1), r(hi[1], 1), g.y[2]),
                              (r(lo[2], 2), r(hi[2], 2), g.z[2]))
        elif g.kind in ("cylinder", "sphere"):
            # El cilindro tiene el eje en x = y = 0 y la esfera, el centro en el origen: el mayor que cabe en la
            # caja. Si el eje (o el origen) no pasa por ella, no cabe ninguno: se toma el que la cubre.
            axes = (0, 1) if g.kind == "cylinder" else (0, 1, 2)
            inside = all(lo[i] < 0.0 < hi[i] for i in axes)
            if inside:
                rad = float(min(min(-lo[i], hi[i]) for i in axes))
            else:
                rad = float(math.sqrt(sum(max(lo[i] ** 2, hi[i] ** 2) for i in axes)))
                where = "el eje del cilindro (x = y = 0)" if g.kind == "cylinder" else "el origen"
                self.statusBar().showMessage(f"La geometría no rodea {where}: la rejilla la cubre entera.", 10000)
            if g.kind == "cylinder":
                new = sc.DoseGrid(z=(r(lo[2], 2), r(hi[2], 2), g.z[2]), r=(r(rad), g.r[1]))
            else:
                new = sc.DoseGrid(r=(r(rad), g.r[1]))
        else:
            return
        ctrl.edit("Ajustar la rejilla de dosis", lambda d: sc.set_dose_grid(d, new))

    def _sync_parinp(self) -> bool:
        """Si han cambiado los PARINP del .in, recalcula la geometría con ellos. True si lo ha hecho."""
        ctrl, s, gc = self.input, self.session, self.geo_ctrl
        if ctrl is None or s is None or not ctrl.doc.model.ok or (gc is not None and gc.session is not s):
            return False
        if not ctrl.doc.model.geomfn or not _same_path(s.path, (ctrl.folder or s.path.parent) / ctrl.doc.model.geomfn):
            return False
        new = ctrl.doc.model.parinp_list()
        if list(new) == list(s.parinp) or (not new and s.parinp_provisional):
            return False
        if gc is None:
            # La geometría no se pudo usar al abrirla (p. ej. por los PARINP del .in): se prueba con los nuevos.
            if s.doc is None or not s.doc.ok:
                return False
            fresh = session_from_document(s.path, s.doc, list(new), s.input_file, provisional=False,
                                          work_dir=s.work_dir)
            self.set_case(fresh, ctrl, self.case_problem)
            return True
        gc.set_parinp(new)
        return True

    def edit_spectrum(self) -> None:
        """Editor del espectro de la fuente (SPECTR/SLINES); con energía única, parte de ella."""
        if not self._settle():  # lo que se estuviera escribiendo (la energía) antes de abrir el diálogo
            return
        ctrl = self.input
        if ctrl is None or self._source_unread_message():  # (no se sustituyen unas líneas SPECTR que no se leen)
            return
        src = ctrl.doc.model.source
        if src.radioactive or src.from_psf:
            return
        if src.spectrum:
            rows, lines = list(src.spectrum), False
        elif src.lines:
            rows, lines = list(src.lines), True
        else:
            # De una energía única a un espectro: ¿a mano o desde un fichero?
            from .source_panel import format_energy
            from .spectrum_import import SpectrumStartDialog, ask_spectrum_file

            has_e = ctrl.doc.first("SENERG") is not None and src.energy > 0
            e = src.energy if has_e else 1.0e6
            start = SpectrumStartDialog(self, format_energy(e) if has_e else "")
            if not self._exec_dialog(start) or start.choice is None:
                return
            if start.choice == SpectrumStartDialog.FILE:
                conv = ask_spectrum_file(self, self.settings, src.kparp, exec_fn=self._exec_dialog)
                if conv is None:
                    return
                rows, lines = conv.rows, conv.lines
            else:
                rows, lines = [(0.9 * e, 1.0), (e, -1.0)], False
        names = {1: "Electrones.", 2: "Fotones.", 3: "Positrones.", 4: "Protones.", 5: "Partículas alfa."}
        dlg = SpectrumDialog(self, rows, lines, particle=names.get(src.kparp, ""), kparp=src.kparp,
                             settings=self.settings)
        if not self._exec_dialog(dlg):
            return
        new_rows, new_lines = dlg.rows(), dlg.is_lines()
        fn = sc.set_energy_lines if new_lines else sc.set_spectrum
        ctrl.edit("Editar el espectro", lambda d: fn(d, new_rows))

    def _body_point(self, kb: int):
        """Un punto dentro del cuerpo (para centrar el diálogo): del corte actual o de su caja."""
        s = self.session
        lo, hi = s.bounds
        rng = np.random.default_rng(0)
        pts = rng.uniform(lo, hi, size=(20000, 3))
        ib, _ = s.locator.locate(pts)
        inside = pts[ib == kb]
        if len(inside) == 0:
            return 0.5 * (np.asarray(lo) + np.asarray(hi))
        c = inside.mean(axis=0)
        # El punto medio puede caer fuera (cuerpos en forma de C): el más cercano que sí está dentro.
        return inside[np.argmin(np.linalg.norm(inside - c, axis=1))]

    def delete_body(self, kb: int) -> None:
        if not self._can_edit() or not kb or kb > self.session.geo.nbodys:
            return
        geo = self.session.geo
        name = body_title(geo, kb)
        # Lo que no se puede eliminar se dice antes de preguntar (no después, con un error de GEOMIN).
        problem = ge.delete_problem(self.geo_ctrl.doc, geo, kb)
        if problem:
            QMessageBox.information(self, "ARGUS", f"No se puede eliminar {name}:\n\n{problem}")
            return
        if kb == getattr(geo, "ct_body", None):
            self._delete_ct()
            return
        clone = self._is_clone(kb)
        try:
            el = geo.body(kb).element if clone else ge.editable_body(geo, kb)
        except ge.EditError as exc:
            QMessageBox.information(self, "ARGUS", f"No se puede eliminar: {exc}")
            return
        users = ge.body_users(self.geo_ctrl.doc, el.label)
        msg = f"¿Eliminar {name}?"
        if users:
            msg += ("\n\nSu volumen pasará a ser de: " + ", ".join(u.label.strip() for u in users)
                    + " (el cuerpo o módulo que lo contenía).")
        if self.input is not None and self._input_uses_geometry():
            refs = references_to(self.input.doc, geo, kb)
            if refs:
                kws = sorted({ln.kw.strip() for ln in refs})
                msg += (f"\n\nEl .in lo nombra en {len(refs)} línea{'s' if len(refs) > 1 else ''} "
                        f"({', '.join(kws)}): se quitarán.")
                if "SBODY" in kws:
                    msg += " Ojo: es un cuerpo activo de la fuente (SBODY)."
                if "EDBODY" in kws or "IDBODY" in kws:
                    msg += (" Un detector que se quede sin cuerpos se quita entero (penhmain no admite "
                            "detectores sin cuerpos).")
        r = QMessageBox.question(self, "ARGUS", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        label = el.label
        if clone:
            notes = self.edit_geometry(f"Eliminar el clon {label.strip()}",
                                       lambda doc, g: pl.delete_clone(doc, g, g.find_body(label).index))
            if notes:
                self._report(" ".join(notes))
            return
        what = "el módulo" if geo.body(kb).is_module else "el cuerpo"
        notes = self.edit_geometry(f"Eliminar {what} {label.strip()}",
                                   lambda doc, g: ge.delete_body(doc, g, g.find_body(label).index))
        if notes:
            self._report(" ".join(notes))

    def _delete_ct(self) -> None:
        """Quita la caja CT de la geometría (el .ct no se borra); así se puede importar otro TAC."""
        geo = self.session.geo
        kb = geo.ct_body
        msg = (f"¿Eliminar {body_title(geo, kb)} (la caja de vóxeles del TAC)?\n\nEl fichero "
               f"{geo.ct_file or '.ct'} no se borra y los materiales de los tejidos siguen en el .in.")
        if self.input is not None and self._input_uses_geometry():
            refs = references_to(self.input.doc, geo, kb)
            if refs:
                kws = sorted({ln.kw.strip() for ln in refs})
                msg += (f"\n\nEl .in lo nombra en {len(refs)} línea{'s' if len(refs) > 1 else ''} "
                        f"({', '.join(kws)}): se quitarán.")
        r = QMessageBox.question(self, "ARGUS", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        notes = self.edit_geometry("Eliminar el TAC", lambda doc, g: ge.delete_ct(doc, g))
        if notes:
            self._report(" ".join(notes))

    def save_geometry(self) -> bool:
        if not self._settle():
            return False
        if self.geo_ctrl is None:
            return False
        both = self._pair_mismatch(True, False) and not self._pair_mismatch(True, True)
        if both and not self._confirm_pair("¿Guardar también el .in?"):
            return False
        if not both and not self._confirm_mismatch(True, False):
            return False
        return self._save_files(geo=True, inp=both)

    def save_geometry_as(self) -> bool:
        """Guarda la geometría con otro nombre; el .in abierto la sigue (GEOMFN) si penhmain la puede encontrar.

        Tras guardarla, PENGEOM buscará sus INCLUDE y su .ct en la carpeta nueva: se copian allí si faltan y, si
        allí ya hay otros distintos con el mismo nombre, no se guarda (PENGEOM leería esos).
        """
        if not self._settle():
            return False
        if self.geo_ctrl is None:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Guardar la geometría", str(self.geo_ctrl.path),
                                              "Geometrías PENGEOM (*.geo);;Todos los ficheros (*)")
        if not path:
            return False
        if not Path(path).suffix:
            path += ".geo"
        path = Path(path)
        gc = self.geo_ctrl
        if path.suffix.lower() == ".in" or looks_like_input(path) or (
                self.input is not None and self.input.path is not None and _same_path(path, self.input.path)):
            QMessageBox.warning(self, "ARGUS", f"No se ha guardado: «{path.name}» es un fichero de entrada de "
                                "penhmain (.in). La geometría se guarda en un .geo.")
            return False
        if _same_path(path, gc.path):
            return self.save_geometry()  # el mismo fichero: guardar sin más (su carpeta de trabajo no cambia)
        used = self._input_uses_geometry()
        old_path, old_dir, new_dir = gc.path, gc.work_dir, path.parent
        clashes = [n for n, needed in self._geometry_files() if needed and not _same_path(old_dir, new_dir)
                   and os.path.lexists(new_dir / n) and not same_content(old_dir / n, new_dir / n)]
        if clashes:
            one = len(clashes) == 1
            QMessageBox.warning(self, "ARGUS", f"No se ha guardado: en {new_dir} ya hay otro "
                                + " y otro ".join(f"«{n}»" for n in clashes)
                                + (", distinto del que usa" if one else ", distintos de los que usa")
                                + f" la geometría (en {old_dir}), y PENGEOM leería "
                                + ("el de esa carpeta" if one else "los de esa carpeta")
                                + ". Guárdela en otra carpeta, o cambie antes el nombre de "
                                + ("ese fichero." if one else "esos ficheros."))
            return False
        name_why = in_name_problem(path.name, GEOMFN_MAX)
        follows = used and _same_path(self.input.folder or new_dir, new_dir) and name_why is None
        in_target = self.input.path if used and self.input.path is not None else old_path.with_suffix(".in")
        if used and not follows and self._pair_mismatch(False, True, in_target):
            why = name_why or "la geometría nueva iría a otra carpeta"
            QMessageBox.warning(self, "ARGUS", f"No se ha guardado: el .in «{self.input.name}» tiene cambios sin "
                                "guardar que siguen a los de la geometría y seguiría usando "
                                f"«{input_geomfn(self.input.doc)}» ({why}): guardado después, nombraría otros cuerpos. "
                                f"Guarde antes los dos (Archivo → Guardar), o elija un nombre de {GEOMFN_MAX} "
                                "caracteres o menos, sin tildes ni eñes, en la carpeta del .in.")
            return False
        if not self._save_files(geo=True, inp=False, geo_path=path):
            return False
        gc.set_work_dir(None)  # la carpeta de la geometría nueva (la del .in, si este la sigue)
        self._copy_geometry_files(old_dir, new_dir)
        self._copy_geometry_settings(old_path, path)
        self._add_recent(str(path))
        if used:
            self._follow_geometry_file(path)
        elif name_why:
            QMessageBox.warning(self, "ARGUS", f"Ese nombre no vale para citar la geometría en un .in (GEOMFN): "
                                f"{name_why}. Use otro si esta geometría va a usarse en un .in.")
        self._refresh_messages()
        self._update_title()
        self._update_actions()
        return True

    def _geometry_files(self) -> list[tuple[str, bool]]:
        """Los ficheros que usa la geometría, por su nombre en la carpeta de trabajo: los INCLUDE y el .ct (True:
        los lee PENGEOM) y la ficha del TAC (False: no la lee)."""
        from ..ct.importer import sidecar_path
        from ..pengeom.model import CtBoxElement, IncludeElement

        s = self.session
        if s is None:
            return []
        docs = [s.doc] + [e.document for e in s.doc.elements if isinstance(e, IncludeElement) and e.document]
        out = [(e.filename, True) for e in s.doc.elements if isinstance(e, IncludeElement) and e.filename]
        for d in docs:
            for e in d.elements:
                if isinstance(e, CtBoxElement) and e.filename:
                    out += [(e.filename, True), (sidecar_path(e.filename).name, False)]
        return list(dict.fromkeys(out))

    def _copy_geometry_settings(self, old: Path, new: Path) -> None:
        """Los ajustes de la geometría pasan a su nombre nuevo: los colores de los materiales y, en la misma
        carpeta, el .in con que se abre."""
        colors = self.settings.value(self._colors_key(old), "")
        if colors:
            self.settings.setValue(self._colors_key(new), colors)
        choice = self.settings.value(self._choice_key(old))
        if choice and _same_path(old.parent, new.parent):
            self.settings.setValue(self._choice_key(new), choice)

    def _copy_geometry_files(self, old: Path, new: Path) -> None:
        """La geometría se ha guardado en otra carpeta: allí tienen que estar también su .ct y sus INCLUDE.

        PENGEOM los busca en la carpeta de trabajo; ``old`` es la de antes (de
        donde los ha leído ARGUS). Se copian si faltan (a un temporal que luego
        toma el nombre, sin sobrescribir nunca: un fallo no deja un .ct a
        medias); si ya hay otro fichero con el mismo nombre, no se toca y se
        avisa.
        """
        from ..core.files import copy_new

        if _same_path(old, new) or self.session is None:
            return
        names = [n for n, _needed in self._geometry_files()]
        copied, clashes, errors = [], [], []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for name in names:
                src, dst = old / name, new / name
                if not src.is_file():
                    continue
                try:
                    if copy_new(src, dst):
                        copied.append(name)
                    elif not same_content(src, dst):
                        clashes.append(name)
                except OSError as exc:
                    errors.append(f"{name} ({exc.strerror or exc})")
        finally:
            QApplication.restoreOverrideCursor()
        if errors:
            QMessageBox.warning(self, "ARGUS", "No se han podido copiar a la carpeta nueva los ficheros que usa la "
                                "geometría (PENGEOM no los encontrará allí): " + ", ".join(errors) + ".")
        if clashes:
            QMessageBox.warning(self, "ARGUS", "En la carpeta nueva ya hay otros ficheros con el nombre de los que usa "
                                "la geometría (no se han tocado): " + ", ".join(clashes) + ".")
        if copied:
            self.statusBar().showMessage("Copiados a la carpeta nueva los ficheros que usa la geometría: "
                                         + ", ".join(copied) + ".", 10000)

    def _follow_geometry_file(self, path: Path) -> None:
        """El .in abierto usaba la geometría que se acaba de guardar con otro nombre: su GEOMFN la sigue, si
        penhmain la puede encontrar (en la carpeta del .in y con un nombre de 20 caracteres como mucho)."""
        ctrl = self.input
        folder = ctrl.folder or path.parent
        old = input_geomfn(ctrl.doc)
        if not _same_path(folder, path.parent):
            QMessageBox.warning(self, "ARGUS", f"La geometría se ha guardado en otra carpeta que la del .in "
                                f"({folder}): penhmain no la encontrará. El .in sigue usando {old}.")
            return
        why = in_name_problem(path.name, GEOMFN_MAX)
        if why:
            QMessageBox.warning(self, "ARGUS", f"La geometría se ha guardado como «{path.name}», pero el .in no la "
                                f"puede citar (GEOMFN): {why}. El .in sigue usando «{old}». Para usarla en él, "
                                f"guárdela con un nombre de {GEOMFN_MAX} caracteres o menos y sin tildes ni eñes.")
            return
        if old != path.name:
            ctrl.edit(f"GEOMFN: {path.name}", lambda d: d.set("GEOMFN", text=path.name))
            self.statusBar().showMessage(f"El .in usa ahora la geometría {path.name} (GEOMFN). Guarde el .in.", 10000)
        if ctrl.path is not None:  # al abrir la geometría, se propone este .in
            self.settings.setValue(self._choice_key(path), ctrl.name)

    def save_all(self) -> bool:
        """Guarda lo que haya cambiado, la geometría y el .in: los dos enteros o ninguno (si la geometría no se
        puede guardar, el .in tampoco: podría nombrar los cuerpos con la numeración nueva)."""
        if not self._settle():
            return False
        gc, ic = self.geo_ctrl, self.input
        geo = gc is not None and gc.dirty
        inp = ic is not None and (ic.dirty or ic.path is None)
        if geo and not inp and self._pair_mismatch(True, False) and not self._pair_mismatch(True, True):
            inp = True  # el .in guardado nombraría otros cuerpos de la geometría nueva
        if not geo and not inp:
            return True
        if not self._confirm_mismatch(geo, inp):
            return False
        return self._save_files(geo=geo, inp=inp)

    def _on_tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        kb = int(item.data(0, Qt.ItemDataRole.UserRole + 1) or 0) if item is not None else 0
        menu = QMenu(self)
        if kb:
            menu.addAction(f"Seleccionar {body_title(self.session.geo, kb)}", lambda: self.select_body(kb))
        self._add_edit_actions(menu, kb, None)
        if not menu.isEmpty():
            self._exec_menu(menu, self.tree.viewport().mapToGlobal(pos))
        else:
            menu.deleteLater()

    def _add_edit_actions(self, menu: QMenu, kb: int, point, face: str | None = None) -> None:
        """Acciones de edición de la geometría en un menú contextual.

        ``kb``: cuerpo pulsado; ``point``: punto dentro de él (o None, desde
        el árbol); ``face``: cara pulsada en 3D («+x»...), para pegar.
        """
        if not self._can_edit():
            return
        geo = self.session.geo
        chain = ge.container_chain(geo, ge.container_of(geo, kb))
        if not menu.isEmpty():
            menu.addSeparator()
        if chain:
            where = "aquí" if point is not None else "dentro"
            cont = chain[0]
            a = menu.addAction(f"Añadir un cuerpo {where}…")
            a.triggered.connect(lambda: self.add_body_dialog(cont, point))
            a = menu.addAction(f"Añadir un módulo {where}…")
            a.setToolTip("Un módulo agrupa: puede tener cuerpos dentro y se mueve con todo lo que tiene")
            a.triggered.connect(lambda: self.add_body_dialog(cont, point, module=True))
            a = menu.addAction(f"Añadir un detector {where}…")
            a.setToolTip("Cuadrado, rectángulo, círculo, triángulo, pentágono o hexágono, y su detector de penhmain")
            a.triggered.connect(lambda: self.add_detector_dialog(cont, point))
            a = menu.addAction(f"Añadir una celda de detectores {where}…")
            a.setToolTip("Una caja llena de detectores iguales en filas, columnas y capas")
            a.triggered.connect(lambda: self.add_cell_dialog(cont, point))
        if not kb or kb > geo.nbodys or geo.body(kb).origin == "enclosure":
            return
        name = body_title(geo, kb)
        face_txt = f" por su cara {FACE_NAMES[face]}" if face else ""
        parent = ge.logical_parent(geo, kb)
        if parent and ge.container_chain(geo, parent):
            a = menu.addAction(f"Añadir un módulo pegado a {name}{face_txt}…")
            a.triggered.connect(lambda: self.add_body_dialog(parent, point, module=True, glue_target=kb,
                                                             glue_face=face))
        sel = self.view.selected
        if sel and sel != kb and self._can_glue(sel, kb):
            a = menu.addAction(f"Pegar {body_title(geo, sel)} (el seleccionado) a {name}{face_txt}…")
            a.triggered.connect(lambda: self.glue_module_dialog(sel, kb, face))
        if kb == getattr(geo, "ct_body", None):  # la caja del TAC: se puede envolver o quitar, no editar
            if geo.body(kb).document is geo.document:
                menu.addSeparator()
                a = menu.addAction(f"Envolver {name} en un módulo…")
                a.setToolTip("Un módulo nuevo alrededor (el CT no se mueve)")
                a.triggered.connect(lambda: self.wrap_dialog(kb))
                a = menu.addAction(f"Eliminar {name}…")
                a.setToolTip("Quita la caja de vóxeles (el .ct no se borra); después se puede importar otro TAC")
                a.triggered.connect(lambda: self.delete_body(kb))
            return
        clone = self._is_clone(kb)
        if not clone and not ge.is_editable(geo, kb):
            return
        b = geo.body(kb)
        menu.addSeparator()
        a = menu.addAction(f"Duplicar {name}…")
        a.setToolTip("Copias independientes o clones (CLONE), desplazadas o pegadas por una cara")
        a.triggered.connect(lambda: self.duplicate_dialog(kb))
        a = menu.addAction(f"Envolver {name} en un módulo…")
        a.setToolTip("Un módulo nuevo alrededor (y de otros a su lado), para moverlos o girarlos juntos")
        a.triggered.connect(lambda: self.wrap_dialog(kb))
        if self._placeable(kb):
            self._add_rotate_menu(menu, kb, name)
            if glue_candidates(geo, kb):
                a = menu.addAction(f"Pegar {name} a otro cuerpo o módulo…")
                a.triggered.connect(lambda: self.glue_module_dialog(kb))
        if clone:
            orig = geo.find_body(b.clone_element.module_label)
            if orig is not None:
                a = menu.addAction(f"Seleccionar el original ({body_title(geo, orig.index)})")
                a.triggered.connect(lambda: self.select_body(orig.index))
            a = menu.addAction(f"Convertir {name} en copia independiente")
            a.triggered.connect(lambda: self.materialize_clone(kb))
        elif not b.is_module:
            a = menu.addAction(f"Convertir {name} en módulo")
            a.setToolTip("Mismo volumen y material; lo que tiene dentro pasa a ser hijas. Un módulo se puede "
                         "mover, girar y pegar a otros.")
            a.triggered.connect(lambda: self.convert_to_module(kb))
        if not clone:
            a = menu.addAction(f"Añadir una superficie a {name}…")
            a.setToolTip("Recortarlo con un plano o con otra superficie")
            a.triggered.connect(lambda: self.add_surface_dialog(kb))
        a = menu.addAction(f"Cambiar la etiqueta de {name}…")
        a.triggered.connect(lambda: self.rename_dialog(kb))
        el = b.element
        if clone or not b.is_module or not any(not r.is_surface for r in el.items):
            a = menu.addAction(f"Eliminar {name}…")
            a.triggered.connect(lambda: self.delete_body(kb))

    def _add_rotate_menu(self, menu: QMenu, kb: int, name: str) -> None:
        sub = menu.addMenu(f"Girar {name}")
        for axis in "xyz":
            vec = [1.0 if a == axis else 0.0 for a in "xyz"]
            for ang in (90.0, -90.0):
                a = sub.addAction(f"{ang:+g}° alrededor de {axis} (por su centro)")
                a.triggered.connect(lambda _c=False, v=vec, g=ang: self.rotate_element(kb, v, g))
        sub.addSeparator()
        a = sub.addAction("Otro ángulo… (panel Editar)")
        a.triggered.connect(lambda: (self.select_body(kb), self.dock_edit.raise_()))

    # -- materiales ------------------------------------------------------------------------
    def add_material_dialog(self) -> None:
        """Declara un material más en el .in (línea MFNAME), sin tener que crear antes un cuerpo."""
        s = self.session
        if self.input is None:
            if s is None or not s.ok:
                return
            r = QMessageBox.question(
                self, "ARGUS", "Los materiales se declaran en el .in (una línea MFNAME por material) y esta "
                "geometría no tiene ningún .in abierto.\n\n¿Crear un .in nuevo para ella?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
            if r != QMessageBox.StandardButton.Yes:
                return
            self.new_input()
            if self.input is None:
                return
        doc = self.input.doc
        mf = doc.find("MFNAME")
        n = len(mf) + 1
        if n > MAXMAT:
            QMessageBox.information(self, "ARGUS", f"penhmain admite como máximo {MAXMAT} materiales y el .in ya "
                                    f"declara {len(mf)}.")
            return
        geo = s.geo if s is not None and s.ok else None
        used_by = ""
        if geo is not None:
            kbs = [b.index for b in geo.bodies if b.material == n and b.origin != "enclosure"]
            used_by = ", ".join(body_title(geo, kb) for kb in kbs[:6]) + (" y más" if len(kbs) > 6 else "")
        folder = self.input.folder or (s.path.parent if s is not None else None)
        taken = {ln.text.strip() for ln in mf if ln.text}
        dlg = AddMaterialDialog(self, n, self.materials._mlist, folder, taken, used_by,
                                self.material_colors.get(n) or material_color(n))
        if not self._exec_dialog(dlg):
            return
        fname, desc, color, comp = dlg.fname(), dlg.description(), dlg.color(), dlg.composition()

        def add(d):
            set_material(d, n, fname=fname, description=desc)
            if comp is not None:
                set_recipe(d, n, comp)

        try:
            self.input.edit(f"Añadir el material {n}", add)
        except ValueError as exc:
            QMessageBox.warning(self, "ARGUS", f"No se ha añadido el material: {exc}")
            return
        self._set_material_color(n, color)
        self.materials.reload_library()
        self.materials.refresh()
        saved = f" Guardado también en sus materiales personalizados como «{dlg.saved_to_library}»." \
            if dlg.saved_to_library else ""
        self.statusBar().showMessage(f"Material {n} añadido ({fname}). Ya se puede usar en los cuerpos (panel "
                                     "Editar o «Añadir un cuerpo»); guarde el .in para escribirlo"
                                     + (" y genere el .mat con «Generar .mat y ENDF…»." if comp is not None else ".")
                                     + saved, 10000)

    def _set_material_color(self, n: int, color: str) -> None:
        if color != (self.material_colors.get(n) or material_color(n)):
            colors = dict(self.material_colors)
            colors[n] = color
            self.materials.colors = dict(colors)
            self._on_colors_changed(colors)

    def _penhan_database(self):
        from .settings import penhan_setup

        return penhan_setup(self.settings).database

    def edit_material_dialog(self, n: int) -> bool:
        """Panel Materiales → «Composición del material N…»: de la lista de PENELOPE o propia (fórmula o elementos,
        densidad y energía media de excitación), su fichero .mat y su color. True si ha cambiado algo."""
        if self.input is None:
            return False
        if not self._settle():
            return False
        choice = self.ask_material(n)
        if choice is None:
            return False
        old_color = self.material_colors.get(n) or material_color(n)

        def change(d):
            set_material(d, n, fname=choice.fname, description=choice.description)
            set_recipe(d, n, choice.composition)

        try:
            changed = self.input.edit(f"Material {n}: {choice.description or choice.fname}", change)
        except ValueError as exc:
            QMessageBox.warning(self, "ARGUS", f"No se ha cambiado el material {n}: {exc}")
            return False
        self._set_material_color(n, choice.color)
        self.materials.reload_library()
        self.materials.refresh()
        self._refresh_messages()
        if choice.saved_to_library:
            self.statusBar().showMessage(f"Guardado también en sus materiales personalizados como "
                                         f"«{choice.saved_to_library}».", 8000)
        return bool(changed) or choice.color != old_color

    def ask_material(self, n: int, current=None, pending=()):
        """El diálogo «Composición del material N», sin cambiar nada: lo elegido (:class:`MaterialChoice`) o None
        si se cancela. ``current``: lo elegido antes y aún no aplicado (para volver a abrirlo con ello); ``pending``:
        los ficheros .mat elegidos para otros materiales y aún no aplicados (no se pueden usar para este)."""
        if self.input is None:
            return None
        doc = self.input.doc
        mf = doc.find("MFNAME")
        if not 1 <= n <= len(mf):
            return None
        line = mf[n - 1]
        rec = material_recipe(doc, n)
        if current is None and rec is not None and rec.composition is None:
            QMessageBox.warning(self, "ARGUS", f"La composición del material {n} en el .in no se entiende "
                                f"({rec.error}). Se puede escribir de nuevo aquí: al guardar, sustituye a la de antes.")
        s = self.session
        geo = s.geo if s is not None and s.ok else None
        used_by = ""
        if geo is not None:
            kbs = [b.index for b in geo.bodies if b.material == n and b.origin != "enclosure"]
            used_by = ", ".join(body_title(geo, kb) for kb in kbs[:6]) + (" y más" if len(kbs) > 6 else "")
        folder = self.input.folder or (s.path.parent if s is not None else None)
        taken = {ln.text.strip() for i, ln in enumerate(mf) if ln.text and i != n - 1} | set(pending)
        if current is not None:
            fname, desc, recipe, color = current.fname, current.description, current.composition, current.color
        else:
            fname, desc = line.text.strip(), material_description(line)
            recipe, color = (rec.composition if rec is not None else None), (self.material_colors.get(n) or
                                                                              material_color(n))
        dlg = AddMaterialDialog(self, n, self.materials._mlist, folder, taken, used_by, color, fname=fname,
                                description=desc, recipe=recipe, edit=True)
        if not self._exec_dialog(dlg):
            return None
        return dlg.choice()

    def material_library(self) -> None:
        """Opciones → «Materiales personalizados…» (también en el panel Materiales)."""
        from .material_library_dialog import LibraryDialog

        self._exec_dialog(LibraryDialog(self))
        self.materials.reload_library()
        self.materials.refresh()

    def assign_materials(self) -> bool:
        """Panel Materiales → «Asignar materiales…»: a los materiales que ARGUS no sabe de qué están hechos se les
        elige uno de la lista de PENELOPE o de los personalizados, o se escribe su composición («Composición
        propia…»). Todo se aplica al «Asignar», en un solo paso de deshacer («Cancelar» no cambia nada). True si se
        ha asignado alguno."""
        from ..penhan.materials import materials_table, unidentified
        from .assign_materials_dialog import AssignMaterialsDialog

        if self.input is None or not self._settle():
            return False
        s = self.session
        geo = s.geo if s is not None and s.ok else None
        folder = self.input.folder or (s.path.parent if s is not None else None)
        rows = unidentified(materials_table(self.input.doc, geo, folder, self._penhan_database()),
                            self.materials._mlist)
        if not rows:
            QMessageBox.information(self, "ARGUS", "ARGUS sabe de qué están hechos todos los materiales cuyo .mat "
                                    "falta: no hay nada que asignar.")
            return False

        def bodies(r) -> str:
            if geo is None or not r.bodies:
                return "ningún cuerpo lo usa" if geo is not None else ""
            names = [body_title(geo, kb) for kb in r.bodies[:3]]
            return "en " + ", ".join(names) + (f" y {len(r.bodies) - 3} más" if len(r.bodies) > 3 else "")

        dlg = AssignMaterialsDialog(self, rows, self.materials._mlist, self.materials.library,
                                    bodies=bodies, compose=self.ask_material)
        accepted = self._exec_dialog(dlg)
        self.materials.reload_library()  # «Guardarlo también en mis materiales personalizados» (aunque se cancele)
        chosen = dlg.assignments() if accepted else []
        if not chosen:
            self.materials.refresh()
            return False
        failed: list[str] = []

        def apply(d):
            failed.clear()
            for a in chosen:
                before = d.snapshot()
                try:
                    set_material(d, a.number, fname=a.fname, description=a.description)
                    set_recipe(d, a.number, a.composition)
                except ValueError as exc:  # este no (los demás sí): se dice cuál y por qué
                    d.restore(before)
                    failed.append(f"material {a.number}: {exc}")

        try:
            self.input.edit(f"Asignar {plural(len(chosen), 'material', 'materiales')}", apply)
        except ValueError as exc:
            QMessageBox.warning(self, "ARGUS", f"No se han asignado los materiales: {exc}")
            return False
        done = [a for a in chosen if not any(f.startswith(f"material {a.number}:") for f in failed)]
        for a in done:
            if a.color:
                self._set_material_color(a.number, a.color)
        self.materials.refresh()
        self._refresh_messages()
        if failed:
            QMessageBox.warning(self, "ARGUS", "No se ha podido asignar:\n" + "\n".join(failed))
        if done:
            one = len(done) == 1
            self.statusBar().showMessage(f"{'Asignado' if one else 'Asignados'} "
                                         f"{plural(len(done), 'material', 'materiales')}: ya se "
                                         f"{'puede' if one else 'pueden'} generar {'su' if one else 'sus'} .mat con "
                                         "«Generar .mat y ENDF…» (o al ejecutar).", 10000)
        return bool(done)

    def remove_material(self, n: int) -> None:
        if self.input is None:
            return
        s = self.session
        if s is not None and s.ok and n in s.geo.used_materials():
            who = ("vóxeles del CT" if n not in s.geo.materials() else "cuerpos de la geometría")
            QMessageBox.information(self, "ARGUS", f"El material {n} lo usan {who}: no se puede quitar.")
            return
        if n != len(self.input.doc.find("MFNAME")):
            QMessageBox.information(self, "ARGUS", "Solo se puede quitar el último material: los demás cambiarían "
                                    "de número (penhmain los numera por el orden de las líneas MFNAME).")
            return
        self.input.edit(f"Quitar el material {n}", remove_last_material)

    # -- icono ------------------------------------------------------------------------------------
    def set_app_icon(self, style: str) -> None:
        set_icon_style(style)
        icon = argus_icon(style)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
        self.setWindowIcon(icon)

    # -- vista 3D ------------------------------------------------------------------
    # -- «Geometrías complejas» (ADR-019) ----------------------------------------------------------------------------
    def _docks(self) -> list[QDockWidget]:
        return self.findChildren(QDockWidget)

    def _on_central_tab(self, index: int) -> None:
        """Entrar en «Geometrías complejas» oculta los paneles (y se recuerdan) y trae las vistas a su columna;
        al volver, todo queda como estaba."""
        if index == 1 and not self.complex.active:
            self._abort_drags()
            self._dock_state = self.saveState()
            for d in self._docks():
                d.hide()
            self.complex.view_layout.addWidget(self.splitter)
            self.splitter.setOrientation(Qt.Orientation.Vertical)
            self.complex.activate()
        elif index == 0 and self.complex.active:
            if self.complex.manual:
                self.complex.end_manual(ask_to_continue=True)
                if self.complex.manual:  # «Seguir editando»: se queda en la pestaña
                    with QSignalBlocker(self.central):
                        self.central.setCurrentIndex(1)
                    self.act_complex.setChecked(True)
                    return
            self.complex.deactivate()
            self.view_tab.layout().addWidget(self.splitter)
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            if self._dock_state is not None:
                self.restoreState(self._dock_state)
        if self.view_mode == "split":
            self.splitter.setSizes([10000, 10000])
        self.act_complex.setChecked(index == 1)
        QTimer.singleShot(0, self._refit_after_mode)

    def show_preview(self, session) -> None:
        """Las vistas enseñan una geometría sin aplicar (vista previa de «Geometrías complejas») o, con None, otra vez
        la de verdad. La cámara, el corte, la caja de la escena y lo oculto se conservan."""
        prev = self._preview_session or self.session
        if session is None:
            if self._preview_session is None:
                return
            self._preview_session = None
            target = self.session
        else:
            self._preview_session = session
            target = session
        mapping = None
        if prev is not None and target is not None and prev.ok and target.ok and prev.geo is not target.geo:
            mapping = renumber_map(prev.geo, target.geo)
        self.view3d.set_session(target, keep_camera=True, keep_bounds=True, body_map=mapping)
        self.view.set_session(target, keep_view=True)
        self.view3d.set_material_visibility(self.materials.visibility)

    def apply_geometry_text(self, text: str, label: str) -> bool:
        """«Manual edit»: el texto entero pasa a ser la geometría (un paso de deshacer), la acepte PENGEOM o no. El .in
        sigue a los cuerpos si las dos geometrías, la de antes y la nueva, se pueden construir."""
        if self.session is None:
            return False
        self._abort_drags()
        if self.geo_ctrl is None:  # una geometría que se abrió con errores: se arregla a mano
            self._set_geo_controller(GeoController(self.session, self))
        old = self.geo_ctrl.session.geo
        self.last_follow_notes = []
        self._group_seq += 1
        self._group = self._group_seq
        try:
            changed = self.geo_ctrl.replace_text(text, label)
            new = self.geo_ctrl.session.geo
            if changed and new.ok and self._input_uses_geometry():
                since = ""
                if old is None or not old.ok:
                    # Se arregla una geometría que PENGEOM no aceptaba: el .in sigue nombrando los cuerpos como en la
                    # última que aceptaba (revisión de 0.16.2: no se seguía y quedaban nombrando otros cuerpos).
                    old = self.geo_ctrl.last_good_geometry()
                    since = " (desde la última geometría que PENGEOM aceptaba)"
                if old is not None and old.ok:
                    # Escrito a mano no se sabe si un cuerpo que ya no está se ha borrado o ha cambiado de etiqueta:
                    # el .in sigue a los demás y las líneas que nombran alguno de esos se quedan como están (antes no
                    # se seguía a ninguno), para que se revisen.
                    mapping = renumber_map(old, new)
                    gone = [kb for kb, v in mapping.items() if v is None and references_to(self.input.doc, old, kb)]
                    self._follow_geometry(old, new, keep_gone=True)
                    if since and self.last_follow_notes:
                        self.last_follow_notes = [self.last_follow_notes[0] + since] + self.last_follow_notes[1:]
                    if gone:
                        names = ", ".join(body_title(old, kb) for kb in gone[:4]) + (" …" if len(gone) > 4 else "")
                        self.last_follow_notes.append(
                            f"no se han cambiado las líneas que nombran {names}, que ya no "
                            f"{'está' if len(gone) == 1 else 'están'} (o {'tiene' if len(gone) == 1 else 'tienen'} "
                            "otra etiqueta): revíselas")
        finally:
            self._group = 0
        self._update_actions()
        return changed

    def _on_view_pick(self, kb: int) -> None:
        """Clic en un cuerpo del corte o de la vista 3D. Si lo que se veía era una vista previa, se quita y se elige
        el mismo cuerpo en la geometría de verdad (el que aún no existe, nada). Escribiendo a mano, la vista previa se
        queda (lo que se ve es el texto escrito) y no se elige nada."""
        if self.complex.manual:
            return
        if self._preview_session is not None:
            mapping = self.complex.preview_body_map()
            kb = (mapping or {}).get(kb) or 0
            self.complex.drop_preview()
        self.select_body(kb)

    def set_view_mode(self, mode: str) -> None:
        """«2d», «3d» o «split» (el corte y la vista 3D lado a lado)."""
        self._abort_drags()  # un arrastre en el corte no puede seguir si el corte se oculta
        if mode not in ("2d", "3d", "split"):
            mode = "split"
        self.view_mode = mode
        self.view.setVisible(mode in ("2d", "split"))
        self.view3d.setVisible(mode in ("3d", "split"))
        self.mode_actions[mode].setChecked(True)
        if mode == "split":
            self.splitter.setSizes([10000, 10000])
        QTimer.singleShot(0, self._refit_after_mode)

    def _refit_after_mode(self) -> None:
        # F2, F3 y F4 no pierden el zoom ni la cámara: solo se encuadra la vista que aún no se había encuadrado
        # a la vista (p. ej. la 3D, si el fichero se abrió con ella oculta).
        if self.session is not None and self.session.ok:
            if self.view.isVisible() and not self.view.fitted:
                self.view.fit()
            if self.view3d.isVisible() and not self.view3d.fitted:
                self.view3d.fit()

    def fit_views(self) -> None:
        if self.view.isVisible():
            self.view.fit()
        if self.view3d.isVisible():
            self.view3d.fit()

    def _sync_slice_plane_3d(self) -> None:
        self.view3d.set_slice_plane(self.view.plane if self.act_slice_in_3d.isChecked() else None)

    def _toggle_slice_in_3d(self, on: bool) -> None:
        self.view3d.show_slice_plane = on
        self._sync_slice_plane_3d()

    def _on_render_time_2d(self, t: float) -> None:
        self._render_2d = f"corte: {1000 * t:.0f} ms"
        self._show_render_info()

    def _on_render_info_3d(self, text: str) -> None:
        self._render_3d = text
        self._show_render_info()

    def _show_render_info(self) -> None:
        parts = [x for x in (self._render_2d if self.view.isVisible() else "",
                             self._render_3d if self.view3d.isVisible() else "") if x]
        self.render_label.setText("   ·   ".join(parts))

    def _on_context_menu_3d(self, global_pos, info) -> None:
        """Menú contextual de la vista 3D: fuente, visibilidad, corte 2D y cámara."""
        if self.complex.manual:  # escribiendo a mano: solo la cámara (lo que se ve es el texto sin aplicar)
            menu = QMenu(self)
            menu.addAction("Encuadrar", self.view3d.fit)
            self._exec_menu(menu, global_pos)
            return
        if self._preview_session is not None:  # lo que se veía era una vista previa: se quita y se mira otra vez
            self.complex.drop_preview()
            lp = self.view3d.canvas.mapFromGlobal(global_pos)
            info = self.view3d.pick(lp.x(), lp.y())
        menu = QMenu(self)
        v3 = self.view3d
        geo = self.session.geo if self.session is not None and self.session.ok else None
        if info is None or geo is None:
            menu.addAction("Encuadrar", v3.fit)
            if v3.hidden_bodies or v3.material_visibility:
                menu.addAction("Mostrar todos los cuerpos", v3.show_all)
            self._exec_menu(menu, global_pos)
            return
        kb = int(info["body"])
        p = [se.clean(float(x), 6) for x in info["point"]]
        where = f"({p[0]:g}, {p[1]:g}, {p[2]:g}) cm"
        name = body_title(geo, kb)
        mat = geo.body(kb).material
        if kb == getattr(geo, "ct_body", None):  # el material del vóxel pulsado, no la marca 9999 de la caja
            from ..pengeom.ct import voxel_at

            v = voxel_at(geo, info["point"], info.get("direction"), nudge=1e-6)
            mat = v[3] if v is not None else 0
        ctrl = self.input
        src = ctrl.doc.model.source if ctrl is not None else None
        if ctrl is not None and self._source_read() and not src.from_psf:
            if not src.radioactive:
                a = menu.addAction(f"Apuntar el haz hacia aquí {where}")
                a.setEnabled(any(abs(x - y) > 1e-12 for x, y in zip(p, src.position)))
                a.triggered.connect(lambda: ctrl.edit("Apuntar el haz", lambda d: se.aim_at(d, p)))
            a = menu.addAction(f"Colocar la fuente aquí, en la superficie {where}")
            a.triggered.connect(lambda: ctrl.edit("Colocar la fuente", lambda d: se.set_position(d, p)))
            seg = v3.tracer.segment_through(info["origin"], info["direction"], kb, float(info["t"]))
            if seg is not None and np.isfinite(seg[1]):
                mid = np.asarray(info["origin"]) + 0.5 * (seg[0] + seg[1]) * np.asarray(info["direction"])
                m = [se.clean(float(x), 6) for x in mid]
                a = menu.addAction(f"Colocar la fuente dentro de {name}, a mitad de su espesor aquí "
                                   f"({m[0]:g}, {m[1]:g}, {m[2]:g})")
                a.triggered.connect(lambda: ctrl.edit("Colocar la fuente", lambda d: se.set_position(d, m)))
            if geo.body(kb).origin != "enclosure":
                self._add_source_body_actions(menu, kb)
            menu.addSeparator()
        elif ctrl is None:
            a = menu.addAction("Crear un .in con la fuente aquí")
            a.triggered.connect(lambda: self._new_input_at(p))
            menu.addSeparator()
        k = self.view.plane.depth_axis
        if k is not None:
            a = menu.addAction(f"Llevar el corte 2D a {'xyz'[k]} = {p[k]:g} cm")
            a.triggered.connect(lambda: self._slice_through(p))
        menu.addAction("Centrar la vista 3D aquí", lambda: v3.look_at(info["point"]))
        menu.addSeparator()
        menu.addAction(f"Ocultar {name} en 3D", lambda: v3.set_body_hidden(kb, True))
        if mat > 0:
            sub = menu.addMenu(f"Material {mat} en 3D")
            state = v3.material_visibility.get(mat, OPAQUE)
            for value, text in ((OPAQUE, "Opaco"), (TRANSLUCENT, "Translúcido"), (HIDDEN, "Oculto")):
                a = sub.addAction(text)
                a.setCheckable(True)
                a.setChecked(state == value)
                a.triggered.connect(lambda checked=False, val=value: self._set_material_3d(mat, val))
        if v3.hidden_bodies or v3.material_visibility:
            menu.addAction("Mostrar todos los cuerpos", v3.show_all)
        menu.addSeparator()
        a = menu.addAction(f"Seleccionar {name}")
        a.triggered.connect(lambda: self.select_body(kb))
        self._add_detector_actions(menu, kb)
        # Para añadir un cuerpo se toma el punto medio del tramo del rayo dentro del cuerpo pulsado.
        seg = v3.tracer.segment_through(info["origin"], info["direction"], kb, float(info["t"]))
        inner = info["point"]
        if seg is not None and np.isfinite(seg[1]):
            inner = np.asarray(info["origin"]) + 0.5 * (seg[0] + seg[1]) * np.asarray(info["direction"])
        face = None
        if int(info.get("surface", 0)) > 0:  # cara pulsada (no la de un plano de corte)
            face = ge.face_from_normal(v3.tracer.normal(int(info["surface"]), np.asarray(info["point"]),
                                                        np.asarray(info["direction"])))
        self._add_edit_actions(menu, kb, np.asarray(inner, dtype=float), face=face)
        self._exec_menu(menu, global_pos)

    def _set_material_3d(self, mat: int, value: int) -> None:
        vis = dict(self.view3d.material_visibility)
        if value == OPAQUE:
            vis.pop(mat, None)
        else:
            vis[mat] = value
        self.view3d.set_material_visibility(vis)

    def _slice_through(self, point) -> None:
        k = self.view.plane.depth_axis
        if k is None:
            return
        if not self.view.isVisible():
            self.set_view_mode("split")
        self.view.set_depth(float(point[k]))
        self._sync_depth_widgets()
        self._sync_slice_plane_3d()

    # -- vista ---------------------------------------------------------------------
    def set_plane(self, name: str) -> None:
        self.view.set_plane(name)
        self._sync_depth_widgets()
        self._sync_slice_plane_3d()

    def set_color_mode(self, mode: str) -> None:
        self.view.set_color_mode(mode)
        self.view3d.set_color_mode(mode)
        if self.session is not None and self.session.ok:
            self.tree.set_geometry(self.session.geo, mode, self.material_colors)

    # -- colores de los materiales (se recuerdan por geometría) -------------------------
    def _colors_key(self, session) -> str | None:
        """Clave de los colores de una geometría (su sesión o su ruta)."""
        from .settings import colors_key

        return colors_key(session)

    def _load_colors(self, session) -> dict[int, str]:
        """Los colores por defecto con los que ha elegido el usuario encima (``settings.saved_material_colors``)."""
        from .settings import saved_material_colors

        return saved_material_colors(session, self.settings)

    def _on_colors_changed(self, colors: dict) -> None:
        self.material_colors = dict(colors)
        key = self._colors_key(self.session)
        if key is not None:
            if colors and colors != self._default_colors(self.session):
                self.settings.setValue(key, json.dumps({str(k): v for k, v in colors.items()}))
            else:
                self.settings.remove(key)
        self.view.set_material_colors(self.material_colors)
        self.view3d.set_material_colors(self.material_colors)
        if self.session is not None and self.session.ok:
            self.tree.set_geometry(self.session.geo, self.view.color_mode, self.material_colors)

    def _sync_depth_widgets(self) -> None:
        self._syncing = True
        try:
            k = self.view.plane.depth_axis
            self.depth_label.setText(f"  {'xyz'[k] if k is not None else 'd'} = ")
            lo, hi = self.view.depth_range()
            d = self.view.plane.depth
            span = hi - lo
            self.depth_spin.setSingleStep(max(span / 100, 1e-9) if math.isfinite(span) else 1.0)
            if math.isfinite(d):
                self.depth_spin.setValue(d)
            f = (d - lo) / span if span > 0 else math.nan
            if math.isfinite(f):  # un corte fuera del intervalo, en su extremo
                self.depth_slider.setValue(round(1000 * min(max(f, 0.0), 1.0)))
            for n, a in self.plane_actions.items():
                a.setChecked(n == self.view.plane.name)
        finally:
            self._syncing = False

    def _on_depth_spin(self, value: float) -> None:
        if self._syncing:
            return
        self.view.set_depth(value)
        self._sync_depth_widgets()

    def _on_depth_slider(self, pos: int) -> None:
        if self._syncing:
            return
        lo, hi = self.view.depth_range()
        self.view.set_depth(lo + (hi - lo) * pos / 1000.0)
        self._syncing = True
        self.depth_spin.setValue(self.view.plane.depth)
        self._syncing = False

    # -- selección -----------------------------------------------------------------
    def select_body(self, kb: int) -> None:
        if self.session is None:
            return
        kb = kb or None
        if not self.session.ok:
            kb = None  # geometría con errores (p. ej. por un PARINP): nada seleccionado ni en los paneles
        elif kb is not None and not 1 <= kb <= self.session.geo.nbodys:
            # p. ej. una línea del .in escrita para otra versión de la geometría (DSMAX 9 con 3 cuerpos)
            self.statusBar().showMessage(f"La geometría no tiene el cuerpo n.º {kb}.", 8000)
            kb = None
        self.view.set_selected(kb)
        self.view3d.set_selected(kb)
        self._update_module_handle(kb)
        self.geo_panel.show_body(kb)
        can_edit = self._can_edit() and bool(kb)
        self.act_delete_body.setEnabled(can_edit)
        self.act_duplicate.setEnabled(can_edit)
        self.act_wrap.setEnabled(can_edit)
        self.tree.select_body(kb)
        self.props.show_body(self.session, kb)
        if kb:
            b = self.session.geo.body(kb)
            el = b.element
            if el is not None and b.document is self.session.doc:
                end = el.terminator_line if el.terminator_line is not None else el.last_line
                self.source.highlight(el.first_line, end)
            else:
                self.source.highlight(None, None)
        else:
            self.source.highlight(None, None)
        if self.complex.active:
            self.complex.follow_body(kb)

    def _goto_line(self, file, line: int) -> None:
        if self.input is not None and file is not None and (_same_path(file, self.input.doc.path)
                                                             or file == self.input.doc.path):
            self.dock_input.raise_()
            self.input_text.highlight(line, line)
            return
        if self.input is not None and file is None and self.input.doc.path is None:
            self.dock_input.raise_()
            self.input_text.highlight(line, line)
            return
        self.dock_source.raise_()
        self.source.highlight(line, line, file)

    def _update_material_names(self) -> None:
        """«agua.mat · Water, liquid (PENELOPE 278)» para cada número de material."""
        names: dict[int, str] = {}
        if self.input is not None:
            s = self.session
            geo = s.geo if s is not None and s.ok else None
            folder = self.input.folder or (s.path.parent if s is not None else None)
            for r in materials_table(self.input.doc, geo, folder):
                if r.declared:
                    names[r.number] = r.fname + (f" · {r.display_name}" if r.display_name else "")
        self.material_names = names
        self.tree.set_material_names(names)
        self.props.material_names = names

    def _on_cursor(self, info) -> None:
        if not info:
            self.coord_label.setText("")
            self.body_label.setText("")
            return
        p = info["point"]
        self.coord_label.setText(f"x = {p[0]:.6g}   y = {p[1]:.6g}   z = {p[2]:.6g} cm" + self._overlay_text(p))
        kb, mat = info["body"], info["mat"]
        geo = (self._preview_session or self.session).geo  # lo que se ve (también una vista previa)
        if kb > geo.nbodys:
            self.body_label.setText("Fuera de la envolvente")
        else:
            txt = body_title(geo, kb)
            if getattr(geo, "ct", None) is not None and kb == geo.ct_body:
                self.body_label.setText(f"{txt}   ·   {self._voxel_text(geo, p, info.get('direction'))}")
                return
            if not mat:
                what = "0 (vacío)"
            else:
                what = f"{mat}" + (f" ({self.material_names[mat]})" if mat in self.material_names else "")
            self.body_label.setText(f"{txt}   ·   material {what}")

    def _overlay_text(self, point) -> str:
        """«   ·   Dosis 1,23 µGy ± 2 %» del mapa de resultados que se ve sobre el corte, en ese punto."""
        ov = getattr(self.view, "results_overlay", None)
        if ov is None:
            return ""
        try:
            v, e = ov.sample_with_error(point)
        except ValueError:
            return ""
        if not np.isfinite(v):
            return ""
        rel = f" ± {100 * e / abs(v):.2g} %" if v and np.isfinite(e) and e > 0 else ""
        return f"   ·   {ov.quantity or 'valor'} {v * ov.unit_factor:.4g} {ov.unit}{rel}"

    def _voxel_text(self, geo, point, direction=None) -> str:
        """«vóxel (12, 40, 7) · material 3 (Pulmón) · 0,26 g/cm³» del punto del CT.

        ``direction``: la del rayo (vista 3D: el punto está en la cara del vóxel visible) o la normal del corte.
        """
        from ..pengeom.ct import voxel_at

        v = voxel_at(geo, point, direction, nudge=1e-6 if direction is not None else 0.0)
        if v is None:
            return "fuera de la caja del CT"
        i, j, k, m, rho = v
        name = f" ({self.material_names[m]})" if m in self.material_names else ""
        what = "0 (vacío)" if m == 0 else f"{m}{name}"
        dens = f"{rho:.4g}".replace(".", ",")
        return f"vóxel ({i + 1}, {j + 1}, {k + 1})   ·   material {what}   ·   {dens} g/cm³"

    def about(self) -> None:
        QMessageBox.about(self, "Acerca de ARGUS",
                          f"<h3>ARGUS {__version__}</h3>"
                          "<p>Advanced Radiation Geometry &amp; User-interface System.</p>"
                          "<p>Visor y constructor de geometrías PENGEOM y ficheros de entrada de penhmain "
                          "(PENELOPE). La lectura y la localización reproducen PENGEOM 2024 y el lector de "
                          "penhmain 2025, y se verifican contra ellos.</p>")

    def closeEvent(self, e) -> None:
        if not self.maybe_save():
            e.ignore()
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("view/mode", self.view_mode)
        rw = getattr(self, "_results_window", None)
        if rw is not None:
            rw.close()
            rw.shutdown()
        from ..penhan.cascade import cancel_all

        cancel_all()  # las cascadas a medias se terminan la próxima vez («Simular…» en su vista)
        self.view.shutdown()
        self.view3d.shutdown()
        super().closeEvent(e)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and self.isActiveWindow():
            self._notice_external_changes()

    def _notice_external_changes(self) -> None:
        """Al volver a la ventana: si otro programa ha cambiado el .in o el .geo abiertos, se dice (una vez por
        cambio). Al guardar, se preguntará antes de sobrescribirlo (``_save_files``)."""
        for ctrl in (self.input, self.geo_ctrl):
            if ctrl is None or ctrl.path is None:
                continue
            try:
                st = os.stat(ctrl.path)
            except OSError:
                continue
            seen = (st.st_mtime_ns, st.st_size)
            if getattr(ctrl, "_noticed", None) == seen or not ctrl.changed_on_disk():
                continue
            ctrl._noticed = seen
            self.statusBar().showMessage(f"«{ctrl.name}» ha cambiado en el disco (lo ha modificado otro programa). "
                                         "Recargar (F5) abre esa versión; al guardar, ARGUS preguntará antes de "
                                         "sobrescribirla.", 15000)

    _shown = False  # la ventana ya se ha mostrado una vez

    def showEvent(self, e) -> None:
        super().showEvent(e)
        # Solo la primera vez (entonces las vistas ya tienen su tamaño): al restaurar la ventana minimizada no se
        # reencuadra, se perderían el zoom y la cámara.
        if not self._shown and not e.spontaneous():
            self._shown = True
            QTimer.singleShot(0, self.fit_views)
