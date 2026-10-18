"""Panel «Espectro» del visor de resultados: resolución del detector, ventanas de energía y coeficientes.

Trabaja con la serie que se analiza de la curva que se ve (la primera visible)
cuando su eje x es la energía: espectros de los detectores, de las partículas
que salen, de la fuente…
"""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...results import spectro as sp
from ...results.data import Curves, Series
from ...results.display import ENERGY_SCALE, DisplayOptions, auto_energy
from .style import INK_2, MUTED


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {MUTED};")
    return lab


def parse_windows(text: str) -> list[tuple[float, float]]:
    """«650-680; 30 40» → [(650, 680), (30, 40)] (en la unidad que se ve)."""
    out = []
    for part in text.replace(",", ".").split(";"):
        nums = part.replace("–", " ").replace("-", " ").split()
        if len(nums) != 2:
            continue
        try:
            a, b = float(nums[0]), float(nums[1])
        except ValueError:
            continue
        if b > a:
            out.append((a, b))
    return out


class SpectroPanel(QWidget):
    changed = Signal()  # cambia la resolución del detector (hay que volver a dibujar la curva convolucionada)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.source = QLabel("")
        self.source.setWordWrap(True)
        self.source.setStyleSheet(f"color: {INK_2};")
        lay.addWidget(self.source)
        g = QGroupBox("Resolución del detector")
        f = QFormLayout(g)
        self.det = QComboBox()
        self.det.addItem("ninguna (el espectro de penhmain)", "")
        for k, (name, _fn, _e0) in sp.DETECTORS.items():
            self.det.addItem(name, k)
        self.det.addItem("gaussiana a medida", "custom")
        self.det.currentIndexChanged.connect(self._det_changed)
        f.addRow(self.det)
        self.coef_row = QWidget()
        cr = QHBoxLayout(self.coef_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.ca, self.cb, self.cc = (QDoubleSpinBox() for _ in range(3))
        for w, lab, val in ((self.ca, "a", 1000.0), (self.cb, "b", 0.0), (self.cc, "c", 0.01)):
            w.setDecimals(6)
            w.setRange(0.0, 1e7)
            w.setValue(val)
            w.valueChanged.connect(self._emit)
            cr.addWidget(QLabel(lab))
            cr.addWidget(w)
        f.addRow(self.coef_row)
        self.coef_hint = _hint("FWHM = a + b·√E + c·E, con E y FWHM en eV.")
        f.addRow(self.coef_hint)
        self.tail = QDoubleSpinBox()
        self.tail.setRange(0.0, 1e6)
        self.tail.setDecimals(1)
        self.tail.setSuffix(" eV")
        self.tail.setToolTip("Cola exponencial hacia energías bajas (E0); 0: sin cola")
        self.tail.valueChanged.connect(self._emit)
        f.addRow("Cola (E0):", self.tail)
        self.channel = QDoubleSpinBox()
        self.channel.setRange(0.0, 1e7)
        self.channel.setDecimals(1)
        self.channel.setSuffix(" eV")
        self.channel.setSpecialValueText("la de los intervalos")
        self.channel.setToolTip("Anchura de los canales del espectro convolucionado")
        self.channel.valueChanged.connect(self._emit)
        f.addRow("Canal:", self.channel)
        self.det_info = QLabel("")
        self.det_info.setWordWrap(True)
        f.addRow(self.det_info)
        f.addRow(_hint("Como el programa mcdet de penhan: gaussiana de anchura FWHM(E) − ΔE integrada en cada "
                       "intervalo del espectro de energía depositada."))
        lay.addWidget(g)
        g2 = QGroupBox("Ventanas de energía")
        f2 = QVBoxLayout(g2)
        self.windows = QLineEdit("")
        self.windows.setPlaceholderText("p. ej. 640-680; 30-40")
        self.windows.editingFinished.connect(self.recompute)
        f2.addWidget(self.windows)
        self.bg = QCheckBox("Restar el fondo lineal (3 intervalos a cada lado)")
        self.bg.setChecked(True)
        self.bg.toggled.connect(self.recompute)
        f2.addWidget(self.bg)
        self.win_out = QLabel("")
        self.win_out.setWordWrap(True)
        self.win_out.setTextFormat(Qt.TextFormat.RichText)
        self.win_out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f2.addWidget(self.win_out)
        lay.addWidget(g2)
        g3 = QGroupBox("Coeficientes de conversión")
        f3 = QFormLayout(g3)
        row = QHBoxLayout()
        self.coef_btn = QPushButton("Cargar tabla…")
        self.coef_btn.setToolTip("Texto o CSV: energía y coeficiente (p. ej. fluencia → kerma o dosis ambiental)")
        self.coef_btn.clicked.connect(self._load_coefficients)
        row.addWidget(self.coef_btn)
        self.coef_unit = QComboBox()
        for u in ("eV", "keV", "MeV"):
            self.coef_unit.addItem(f"energía en {u}", u)
        self.coef_unit.setCurrentIndex(2)
        self.coef_unit.currentIndexChanged.connect(self.recompute)
        row.addWidget(self.coef_unit)
        f3.addRow(row)
        self.coef_name = QLabel("")
        f3.addRow(self.coef_name)
        self.coef_out = QLabel("")
        self.coef_out.setWordWrap(True)
        self.coef_out.setTextFormat(Qt.TextFormat.RichText)
        self.coef_out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f3.addRow(self.coef_out)
        lay.addWidget(g3)
        lay.addStretch(1)
        self._curves: Curves | None = None
        self._series: Series | None = None
        self._x_unit: str | None = None
        self._opts = DisplayOptions()
        self._coef: tuple[np.ndarray, np.ndarray] | None = None
        self.convolved: sp.Convolved | None = None
        self._det_changed()
        self.clear()

    # -- estado ---------------------------------------------------------------------------------------------
    def clear(self) -> None:
        self._curves = None
        self._series = None
        self._x_unit = None
        self.convolved = None
        self.source.setText("Elija un espectro (una curva con la energía en el eje x).")
        self.win_out.setText("")
        self.coef_out.setText("")
        self.det_info.setText("")

    def applies_to(self, c: Curves | None) -> bool:
        # Las líneas discretas (SLINES) no son una densidad: ni ventanas ni resolución sobre ellas.
        return c is not None and c.x_unit == "eV" and len(c.x) > 2 and not c.discrete

    def set_spectrum(self, c: Curves, s: Series, opts: DisplayOptions, title: str = "",
                     x_unit: str | None = None) -> None:
        """La serie ``s`` (en las unidades de penhmain) de la curva ``c`` es la que se analiza; ``x_unit``, la
        unidad de energía con que se ve (las ventanas se escriben y se dan en ella)."""
        self._curves, self._series, self._opts = c, s, opts
        self._x_unit = x_unit if x_unit in ENERGY_SCALE else None
        self.source.setText(f"{html.escape(title or c.title)} — {html.escape(s.name)}")
        self.recompute()

    def energy_unit(self) -> str:
        """La unidad de energía de las ventanas y los resultados: la de la curva que se ve."""
        if self._x_unit:
            return self._x_unit
        if self._opts.energy_unit in ENERGY_SCALE:
            return self._opts.energy_unit
        edges = self._edges()
        return auto_energy(float(edges[-1]) if edges is not None and len(edges) else 1.0)

    def _det_changed(self) -> None:
        k = self.det.currentData()
        custom = k == "custom"
        self.coef_row.setVisible(custom)
        self.coef_hint.setVisible(custom)
        if k in sp.DETECTORS:
            self.tail.blockSignals(True)
            self.tail.setValue(sp.DETECTORS[k][2])
            self.tail.blockSignals(False)
        self.tail.setEnabled(bool(k))
        self.channel.setEnabled(bool(k))
        self._emit()

    def _emit(self, *_args) -> None:
        self.recompute()
        self.changed.emit()

    def fwhm_function(self):
        k = self.det.currentData()
        if not k:
            return None
        if k == "custom":
            return sp.custom_fwhm(self.ca.value(), self.cb.value(), self.cc.value())
        return sp.DETECTORS[k][1]

    def detector_name(self) -> str:
        return self.det.currentText() if self.det.currentData() else ""

    # -- cálculos -------------------------------------------------------------------------------------------
    def _edges(self) -> np.ndarray | None:
        c = self._curves
        if c is None:
            return None
        if c.edges is not None and len(c.edges) == len(c.x) + 1:
            return np.asarray(c.edges, dtype=np.float64)
        from ...results.formats import centers_to_edges

        e, _log = centers_to_edges(np.asarray(c.x, dtype=np.float64), c.log_x)
        return e

    def convolve(self) -> sp.Convolved | None:
        """El espectro que se analiza, con la resolución elegida (None sin resolución o si no se puede)."""
        fn = self.fwhm_function()
        c, s = self._curves, self._series
        if fn is None or c is None or s is None:
            return None
        edges = self._edges()
        try:
            return sp.convolve(edges, s.y, s.err, fn, e0=self.tail.value(), channel=self.channel.value() or None)
        except (ValueError, ArithmeticError) as exc:  # lo que no se puede calcular se dice aquí, no en un diálogo
            self.det_info.setText(str(exc) if isinstance(exc, ValueError) else
                                  f"No se puede calcular con estos canales y esta resolución ({exc}).")
            return None

    def resolution_curves(self) -> Curves | None:
        """El espectro convolucionado como otra curva (para dibujarla encima), en las unidades de penhmain."""
        cv = self.convolved
        c, s = self._curves, self._series
        if cv is None or c is None or s is None:
            return None
        return Curves(c.kind, f"{c.title} (con {self.detector_name()})", cv.x, c.x_name, c.x_unit, c.y_name, c.y_unit,
                      [Series(s.name, cv.y, cv.err, s.particle)], edges=cv.edges, histogram=True, log_x=False)

    def _norm_factor(self) -> tuple[float, str]:
        n = self._opts.norm
        if n.mode in ("count", "activity"):
            return n.factor(np.zeros(0), np.zeros(0)), n.label(self._opts.per_decay)
        return 1.0, "por desintegración" if self._opts.per_decay else "por partícula primaria"

    def recompute(self, *_args) -> None:
        c, s = self._curves, self._series
        if c is None or s is None:
            return
        self.convolved = self.convolve()
        edges = self._edges()
        emax = float(edges[-1]) if edges is not None and len(edges) else 1.0
        eu = self.energy_unit()
        ef = ENERGY_SCALE[eu]
        if self.convolved is not None:
            fw = self.convolved.fwhm_at
            pk = float(c.x[int(np.argmax(s.y))]) if len(s.y) else emax
            self.det_info.setText(f"FWHM en el máximo del espectro ({pk * ef:.4g} {eu}): {fw(pk) * ef:.4g} {eu}")
        elif self.det.currentData():
            pass
        else:
            self.det_info.setText("")
        # Ventanas (sobre el espectro convolucionado si se ha elegido detector).
        f, per = self._norm_factor()
        if self.convolved is not None:
            we, wy, werr = self.convolved.edges, self.convolved.y, self.convolved.err
            where = f"con {self.detector_name()}"
        else:
            we, wy, werr = edges, s.y, s.err
            where = "sin resolución"
        rows = []
        what = "cuentas" if f != 1.0 else "probabilidad"
        for lo, hi in parse_windows(self.windows.text()):
            try:
                w = sp.window(we, wy, werr, lo / ef, hi / ef, subtract=self.bg.isChecked())
            except ValueError:
                continue
            net = f"{w.net * f:.4g} ± {w.net_err * f:.2g}"
            extra = []
            if self.bg.isChecked():
                extra.append(f"bruto {w.gross * f:.4g}, fondo {w.background * f:.3g}")
            if w.centroid is not None:
                extra.append(f"centroide {w.centroid * ef:.5g} {eu}")
            if w.fwhm is not None:
                extra.append(f"FWHM {w.fwhm * ef:.3g} {eu}")
            rows.append(f"<b>{lo:g}–{hi:g} {eu}</b>: {net}<br><span style='color:{MUTED}'>{'; '.join(extra)}</span>")
        if rows:
            note = (" Las incertidumbres de un espectro convolucionado son aproximadas (sus canales están "
                    "correlacionados).") if self.convolved is not None else ""
            self.win_out.setText(f"Área neta ({what} {per}, {where}):<br>" + "<br>".join(rows)
                                 + (f"<br><span style='color:{MUTED}'>{note}</span>" if note else ""))
        else:
            self.win_out.setText(f"<span style='color:{MUTED}'>Escriba las ventanas en {eu}.</span>")
        self._conversion(edges, s, f, per)

    def _conversion(self, edges, s: Series, f: float, per: str) -> None:
        if self._coef is None:
            self.coef_out.setText("")
            return
        ce, cv = self._coef
        scale = 1.0 / ENERGY_SCALE[self.coef_unit.currentData()]  # energía de la tabla → eV
        val, sig, out = sp.conversion(edges, s.y, s.err, ce * scale, cv)
        c = self._curves
        unit = c.y_unit.replace("/(eV·", "/(").replace("1/(partícula)", "1/partícula") if c else ""
        txt = f"∫ y(E)·c(E) dE = <b>{val * f:.5g} ± {sig * f:.2g}</b> × [c]"
        if unit:
            txt += f" <span style='color:{MUTED}'>(y en {html.escape(unit)} tras integrar en E; {per})</span>"
        if out > 0.001:
            txt += (f"<br><span style='color:{MUTED}'>El {100 * out:.3g} % del espectro está fuera de la tabla y "
                    "no se cuenta.</span>")
        self.coef_out.setText(txt)

    def _load_coefficients(self) -> None:
        start = str(self.settings.value("resultados/coeficientes", "") or "") if self.settings is not None else ""
        path, _ = QFileDialog.getOpenFileName(self, "Tabla de coeficientes de conversión", start,
                                              "Texto o CSV (*.txt *.csv *.dat *.tsv);;Todos (*)")
        if path:
            self.load_coefficients(path)

    def load_coefficients(self, path: str | Path) -> None:
        try:
            ce, cv, cols = sp.load_coefficients(path)
        except (OSError, ValueError) as exc:
            self.coef_name.setText(f"No se puede leer: {exc}")
            self._coef = None
            self.recompute()
            return
        if self.settings is not None:
            self.settings.setValue("resultados/coeficientes", str(Path(path).parent))
        self._coef = (ce, cv)
        label = " / ".join(cols[:2]) if cols else ""
        self.coef_name.setText(f"{Path(path).name}: {len(ce)} filas" + (f" ({html.escape(label)})" if label else ""))
        self.recompute()


__all__ = ["SpectroPanel", "parse_windows"]
