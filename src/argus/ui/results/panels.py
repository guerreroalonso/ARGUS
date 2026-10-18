"""Paneles del visor de resultados: unidades y normalización, medidas y comparación."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...results import metrics as mt
from ...results.display import DOSE_SCALES, DisplayOptions
from ...results.units import Normalization
from .style import INK_2, MUTED


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {MUTED};")
    return lab


class UnitsPanel(QWidget):
    """Unidades, normalización e incertidumbres de lo que se muestra."""

    changed = Signal(object)  # DisplayOptions

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFormLayout(self)
        f.setContentsMargins(6, 6, 6, 6)
        self.dose = QComboBox()
        self.dose.addItem("automática", "auto")
        for name, _ in DOSE_SCALES:
            self.dose.addItem(name, name)
        f.addRow("Dosis:", self.dose)
        self.energy = QComboBox()
        for name in ("auto", "eV", "keV", "MeV", "GeV"):
            self.energy.addItem("automática" if name == "auto" else name, name)
        f.addRow("Energía:", self.energy)
        self.norm = QComboBox()
        for key, text in (("raw", "por partícula primaria"), ("count", "para N partículas"),
                          ("activity", "para una actividad y un tiempo"), ("max", "% del máximo"),
                          ("point", "% del valor en un punto"), ("area", "área 1")):
            self.norm.addItem(text, key)
        f.addRow("Normalizar:", self.norm)
        self.count = QDoubleSpinBox()
        self.count.setDecimals(3)
        self.count.setRange(1e-30, 1e30)
        self.count.setValue(1e9)
        self.count.setStepType(QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
        self.count_row = self._row(f, "N:", self.count)
        self.activity = QDoubleSpinBox()
        self.activity.setRange(0.0, 1e30)
        self.activity.setDecimals(3)
        self.activity.setValue(1e6)
        self.activity_row = self._row(f, "Actividad (Bq):", self.activity)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.0, 1e12)
        self.duration.setDecimals(3)
        self.duration.setValue(1.0)
        self.duration_row = self._row(f, "Tiempo (s):", self.duration)
        self.point = QDoubleSpinBox()
        self.point.setRange(-1e9, 1e9)
        self.point.setDecimals(4)
        self.point_row = self._row(f, "En x =", self.point)
        self.sigma = QComboBox()
        for k, text in ((0.0, "sin bandas"), (1.0, "±1σ (68 %)"), (2.0, "±2σ (95 %)"), (3.0, "±3σ (penhmain)")):
            self.sigma.addItem(text, k)
        self.sigma.setCurrentIndex(1)
        f.addRow("Incertidumbre:", self.sigma)
        f.addRow(_hint("penhmain escribe las incertidumbres a 3σ; aquí se muestran a la que elija. Con una fuente "
                       "radiactiva todo va por desintegración."))
        for w in (self.dose, self.energy, self.norm, self.sigma):
            w.currentIndexChanged.connect(self._emit)
        for w in (self.count, self.activity, self.duration, self.point):
            w.valueChanged.connect(self._emit)
        self.per_decay = False
        self._update_rows()

    @staticmethod
    def _row(form: QFormLayout, label: str, w: QWidget) -> tuple[QLabel, QWidget]:
        lab = QLabel(label)
        form.addRow(lab, w)
        return lab, w

    def _update_rows(self) -> None:
        mode = self.norm.currentData()
        for row, on in ((self.count_row, mode == "count"), (self.activity_row, mode == "activity"),
                        (self.duration_row, mode == "activity"), (self.point_row, mode == "point")):
            for w in row:
                w.setVisible(on)

    def options(self) -> DisplayOptions:
        return DisplayOptions(
            dose_unit=self.dose.currentData(), energy_unit=self.energy.currentData(),
            norm=Normalization(self.norm.currentData(), count=self.count.value(), activity=self.activity.value(),
                               duration=self.duration.value(), point=self.point.value()),
            sigma=float(self.sigma.currentData()), per_decay=self.per_decay)

    def _emit(self, *_a) -> None:
        self._update_rows()
        self.changed.emit(self.options())


class MetricsPanel(QWidget):
    """Medidas de la curva que se analiza (la principal o el perfil sacado de un mapa)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("Medidas de:"))
        self.preset = QComboBox()
        self.preset.addItem("automático", "auto")
        for k, t in mt.PRESETS.items():
            self.preset.addItem(t, k)
        self.preset.currentIndexChanged.connect(self.recompute)
        row.addWidget(self.preset, 1)
        lay.addLayout(row)
        self.source = QLabel("")
        self.source.setWordWrap(True)
        self.source.setStyleSheet(f"color: {INK_2};")
        lay.addWidget(self.source)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Medida", "Valor (±1σ)"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        lay.addWidget(self.table, 1)
        b = QPushButton("Copiar la tabla")
        b.clicked.connect(self.copy)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignRight)
        lay.addWidget(_hint("La incertidumbre de cada medida se estima repitiéndola con la curva variada al azar "
                            "según su incertidumbre."))
        self._curve = None  # (x, y, err, edges, x_unit, y_unit, kind, x_name, title, discreta, líneas)
        self.primary: int | None = None
        self.results: list[mt.Metric] = []

    def set_curve(self, x, y, err, edges, x_unit: str, y_unit: str, kind: str, x_name: str, title: str,
                  discrete: bool = False, lines=None) -> None:
        """``lines``: energías exactas de un espectro, [(x, área)] en las mismas unidades que y·Δx."""
        self._curve = (np.asarray(x), np.asarray(y), None if err is None else np.asarray(err), edges, x_unit, y_unit,
                       kind, x_name, title, discrete, list(lines or []))
        self.recompute()

    def clear(self) -> None:
        self._curve = None
        self.results = []
        self.table.setRowCount(0)
        self.source.setText("Elija una curva o saque un perfil de un mapa (pulse en el mapa).")

    def recompute(self) -> None:
        if self._curve is None:
            self.clear()
            return
        x, y, err, edges, xu, yu, kind, x_name, title, discrete, lines = self._curve
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < (1 if discrete else 3):
            self.clear()
            return
        preset = self.preset.currentData()
        if preset == "auto" or (discrete and preset == "espectro"):
            preset = mt.guess_preset(kind, x_name, self.primary, discrete)
        # Sin los NaN (las energías exactas de un espectro, que van aparte en ``lines``, con sus bordes quitados).
        xs, ys, e, ed, lines = mt.usable(x, y, err, edges, lines)
        self.results = mt.compute(preset, xs, ys, e, ed, lines=lines)
        self.source.setText(f"{title} — {mt.PRESETS[preset]}")
        self.table.setRowCount(len(self.results))
        for i, m in enumerate(self.results):
            unit = mt.unit_of(m, xu, yu)
            val = "—" if m.value is None or not math.isfinite(m.value) else f"{m.value:.5g}"
            if m.err and m.value is not None and math.isfinite(m.err) and m.err > 1e-9 * max(abs(m.value), 1e-300):
                val += f" ± {m.err:.2g}"
            if unit and val != "—":
                val += f" {unit}"
            it0 = QTableWidgetItem(m.name)
            if m.note:
                it0.setToolTip(m.note)
            it1 = QTableWidgetItem(val)
            it1.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 0, it0)
            self.table.setItem(i, 1, it1)
        self.table.resizeRowsToContents()

    def copy(self) -> None:
        rows = [f"{self.table.item(i, 0).text()}\t{self.table.item(i, 1).text()}" for i in range(self.table.rowCount())]
        QGuiApplication.clipboard().setText("\n".join(rows))


class ComparePanel(QWidget):
    """Otras carpetas y datos medidos con los que comparar lo que se ve."""

    changed = Signal()
    addFolderRequested = Signal()
    addMeasuredRequested = Signal()
    gammaRequested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(QLabel("Comparar con:"))
        self.list = QListWidget()
        self.list.itemChanged.connect(lambda _it: self.changed.emit())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        b1 = QPushButton("Añadir carpeta…")
        b1.setToolTip("Otra carpeta con resultados de penhmain (otra simulación, otra versión…)")
        b1.clicked.connect(self.addFolderRequested)
        b2 = QPushButton("Añadir medidas…")
        b2.setToolTip("Un fichero de texto o CSV con columnas: posición, valor y, si la tiene, su incertidumbre")
        b2.clicked.connect(self.addMeasuredRequested)
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        self.stats = QLabel("")
        self.stats.setWordWrap(True)
        self.stats.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.stats)
        g = QGroupBox("Índice gamma")
        gf = QFormLayout(g)
        self.g_dose = QDoubleSpinBox()
        self.g_dose.setRange(0.1, 50.0)
        self.g_dose.setValue(3.0)
        self.g_dose.setSuffix(" %")
        gf.addRow("Diferencia de dosis:", self.g_dose)
        self.g_dta = QDoubleSpinBox()
        self.g_dta.setRange(0.01, 50.0)
        self.g_dta.setValue(3.0)
        self.g_dta.setSuffix(" mm")
        gf.addRow("Distancia (DTA):", self.g_dta)
        self.g_thr = QDoubleSpinBox()
        self.g_thr.setRange(0.0, 99.0)
        self.g_thr.setValue(10.0)
        self.g_thr.setSuffix(" %")
        gf.addRow("Umbral:", self.g_thr)
        self.g_local = QCheckBox("Local (ΔD relativa a cada punto)")
        gf.addRow(self.g_local)
        self.g_ref = QComboBox()
        self.g_ref.addItem("la principal es la referencia", "main")
        self.g_ref.addItem("la otra es la referencia (p. ej. medidas)", "other")
        gf.addRow(self.g_ref)
        bg = QPushButton("Calcular gamma")
        bg.clicked.connect(lambda: self.gammaRequested.emit(self.gamma_criteria()))
        gf.addRow(bg)
        self.g_result = QLabel("")
        self.g_result.setWordWrap(True)
        gf.addRow(self.g_result)
        lay.addWidget(g)
        lay.addWidget(_hint("Para la gamma, normalice antes las dos (p. ej. «% del máximo» en Unidades) si no "
                            "están en las mismas unidades."))

    def gamma_criteria(self) -> dict:
        return {"dose": self.g_dose.value() / 100.0, "dta": self.g_dta.value() / 10.0,
                "threshold": self.g_thr.value() / 100.0, "local": self.g_local.isChecked(),
                "reference": self.g_ref.currentData()}

    def set_candidates(self, items: list[tuple[str, str, bool]]) -> None:
        """(clave, texto, marcado) de cada conjunto de datos con el que se puede comparar."""
        self.list.blockSignals(True)
        self.list.clear()
        for key, text, checked in items:
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)

    def checked(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


__all__ = ["ComparePanel", "MetricsPanel", "UnitsPanel"]
