"""Espectro de la fuente desde un fichero: elegir cómo darlo y vista previa de lo que se ha leído.

Al pasar de una energía única a un espectro, ARGUS pregunta si las energías se
escriben a mano o se leen de un fichero (``SpectrumStartDialog``). Lo leído se
enseña antes de usarlo (``SpectrumImportDialog``): qué es el fichero, cómo se
interpretan sus columnas (se puede cambiar) y el espectro que resultaría, con
lo que se ha hecho para llegar a él (intervalos vacíos, agrupados…). Después se
revisa en el editor del espectro y se aplica al .in.
"""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import plural
from ..penhan import spectrum_file as sf
from ..penhan.sections import analyze_spectrum
from .screen_fit import fit_to_screen
from .spectrum_plot import SpectrumPlot, energy_unit

FILE_FILTER = "Espectros (*.txt *.dat *.csv *.tsv *.spec *.in);;Todos los ficheros (*)"
SETTINGS_KEY = "fuente/espectros"
MUTED = "#666666"

FORMAT_HELP = """<p>ARGUS lee estos ficheros de espectro:</p>
<ul>
<li><b>El formato de ARGUS</b> (lo escribe «Guardar…» en el editor del espectro): texto con una cabecera que dice cómo
leer los números.<pre>
# Espectro de la fuente (ARGUS)
# unidad: MeV            eV, keV, MeV o GeV
# tipo: continuo         continuo (SPECTR) o líneas (SLINES)
# energía: borde         borde inferior de cada intervalo, centro o bordes
# valor: probabilidad    probabilidad, densidad (por unidad de energía) o cuentas
90      0.10
95      0.25
100     -1               (continuo: la última fila es la energía máxima)</pre></li>
<li><b>Tablas</b> de una hoja de cálculo o de texto, con o sin títulos: dos columnas (energía y valor) o tres con los
dos bordes de cada intervalo y su valor (Emin, Emax, P). Coma o punto decimal; tabuladores, «;», comas o blancos. La
unidad se toma de los títulos («E (keV)», «Energy [MeV]»); un valor por unidad de energía («1/keV», «keV^-1») se toma
como densidad, en el centro de cada intervalo.</li>
<li><b>Líneas SPECTR o SLINES</b> de otro .in.</li>
<li><b>Un espectro de penhmain</b> (detector de impacto, partículas que salen, fuente…): el de una partícula, para
usar como fuente lo que llega a un detector en otra simulación.</li>
</ul>
<p>Antes de usarlo se ve cómo se ha entendido y se puede cambiar. penhmain admite como mucho 250 filas: si hay más, se
juntan intervalos vecinos de poca probabilidad.</p>"""


class SpectrumStartDialog(QDialog):
    """Al pasar de una energía única a un espectro: ¿escribirlo a mano o leerlo de un fichero?"""

    MANUAL, FILE = "manual", "file"

    def __init__(self, parent=None, energy_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Espectro de la fuente")
        self.choice: str | None = None
        lay = QVBoxLayout(self)
        now = f" ({html.escape(energy_text)})" if energy_text else ""
        intro = QLabel(f"La fuente tiene ahora una sola energía{now}. ¿Cómo quiere dar el espectro?")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.btn_manual = QPushButton("Escribir las energías a mano…")
        self.btn_manual.setToolTip("Una tabla de energías y probabilidades, con la gráfica de lo que muestreará "
                                   "penhmain (también se puede pegar de una hoja de cálculo)")
        self.btn_file = QPushButton("Desde un fichero de espectro…")
        self.btn_file.setToolTip("El formato de ARGUS, una tabla de dos o tres columnas, líneas SPECTR/SLINES de otro "
                                 ".in o un espectro de penhmain")
        for b, ch in ((self.btn_manual, self.MANUAL), (self.btn_file, self.FILE)):
            b.setMinimumHeight(34)
            b.clicked.connect(lambda _c=False, ch=ch: self._pick(ch))
            lay.addWidget(b)
        note = QLabel("Ficheros: el formato de ARGUS, tablas de dos o tres columnas (hoja de cálculo o texto), "
                      "líneas SPECTR/SLINES de otro .in o un espectro que haya dado penhmain.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(note)
        row = QHBoxLayout()
        help_btn = QPushButton("Formato del fichero…")
        help_btn.setFlat(True)
        help_btn.clicked.connect(lambda: show_format_help(self))
        row.addWidget(help_btn)
        row.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)
        for lab in (intro, note):  # el alto de un texto partido en líneas sale de un ancho fijo
            lab.setMinimumWidth(440)
        self.adjustSize()

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.accept()


def show_format_help(parent) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("Ficheros de espectro")
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(FORMAT_HELP)
    box.exec()


class SpectrumImportDialog(QDialog):
    """Lo leído de un fichero de espectro, cómo se entiende (se puede cambiar) y el espectro que da."""

    def __init__(self, parent, data: sf.SpectrumData, particle: int | None = None):
        super().__init__(parent)
        self.data = data
        self.particle = particle
        self.setWindowTitle("Espectro desde un fichero")
        fit_to_screen(self, 820, 560)
        self._converted: sf.Converted | None = None
        lay = QVBoxLayout(self)
        rows = data.nrows
        head = QLabel(f"<b>{html.escape(data.name)}</b> · {html.escape(data.what)} · "
                      f"{rows} fila{'s' if rows != 1 else ''}")
        head.setWordWrap(True)
        lay.addWidget(head)
        opt = sf.default_options(data, particle)
        form = QFormLayout()
        self.series = QComboBox()
        self.unit = QComboBox()
        self.rb_cont = QRadioButton("continuo (SPECTR)")
        self.rb_lines = QRadioButton("líneas (SLINES)")
        self.kind = QButtonGroup(self)
        self.kind.addButton(self.rb_cont, 0)
        self.kind.addButton(self.rb_lines, 1)
        self.position = QComboBox()
        self.value = QComboBox()
        if data.from_penhmain:
            w = np.diff(data.edges)
            for name, _p, y in data.series:
                tot = float(np.nansum(np.clip(y, 0, None) * w))
                self.series.addItem(name + ("" if tot > 0 else " (vacía)"), self.series.count())
            self.series.setCurrentIndex(opt.series)
            self.series.currentIndexChanged.connect(self._refresh)
            form.addRow("Partícula:", self.series)
            unit = html.escape(data.value_unit or "por eV")
            info = QLabel(f"Densidad por energía ({unit}) en cada intervalo de penhmain: la probabilidad de cada "
                          "intervalo es la densidad por su anchura.")
            info.setWordWrap(True)
            info.setStyleSheet(f"color: {MUTED};")
            form.addRow(info)
        else:
            for name in sf.UNITS:
                self.unit.addItem(name, name)
            self.unit.setCurrentIndex(max(self.unit.findData(opt.unit), 0))
            self.unit.currentIndexChanged.connect(self._refresh)
            form.addRow("Energías en:", self.unit)
            kind_row = QWidget()
            kr = QHBoxLayout(kind_row)
            kr.setContentsMargins(0, 0, 0, 0)
            kr.addWidget(self.rb_cont)
            kr.addWidget(self.rb_lines)
            kr.addStretch(1)
            (self.rb_lines if opt.kind == "lines" else self.rb_cont).setChecked(True)
            self.kind.idClicked.connect(lambda _i: self._refresh())
            form.addRow("Tipo:", kind_row)
            for key, name in sf.POSITION_NAMES.items():
                self.position.addItem(name, key)
            if data.ncols < 3:
                self.position.model().item(self.position.findData("edges")).setEnabled(False)
            self.position.setCurrentIndex(max(self.position.findData(opt.position), 0))
            self.position.currentIndexChanged.connect(self._refresh)
            form.addRow("La 1.ª columna es:", self.position)
            for key, name in sf.VALUE_NAMES.items():
                self.value.addItem(name, key)
            self.value.setCurrentIndex(max(self.value.findData(opt.value), 0))
            self.value.currentIndexChanged.connect(self._refresh)
            self.value_label = QLabel("La 2.ª columna es:")
            form.addRow(self.value_label, self.value)
        lay.addLayout(form)
        self.plot = SpectrumPlot()
        lay.addWidget(self.plot, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.info)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Usar este espectro")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        help_btn = self.buttons.addButton("Formato del fichero…", QDialogButtonBox.ButtonRole.HelpRole)
        help_btn.clicked.connect(lambda: show_format_help(self))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._refresh()

    def options(self) -> sf.ImportOptions:
        opt = sf.default_options(self.data, self.particle)
        if self.data.from_penhmain:
            opt.series = int(self.series.currentData() or 0)
            return opt
        opt.unit = self.unit.currentData()
        opt.kind = "lines" if self.rb_lines.isChecked() else "continuous"
        opt.position = self.position.currentData()
        opt.value = self.value.currentData()
        return opt

    def result(self) -> sf.Converted | None:
        return self._converted if self._converted is not None and self._converted.error is None else None

    def _refresh(self, *_args) -> None:
        opt = self.options()
        if not self.data.from_penhmain:
            lines = opt.kind == "lines"
            self.position.setEnabled(not lines)
            self.value_label.setText("La 3.ª columna es:" if opt.position == "edges" and not lines else
                                     "La 2.ª columna es:")
        conv = sf.convert(self.data, opt)
        self._converted = conv
        parts = []
        if conv.error is None and conv.rows:
            info = analyze_spectrum(conv.rows, conv.lines)
            self.plot.set_info(info)
            f, unit = energy_unit(max(abs(info.emax), 1.0))
            nb = len(info.bins)
            what = ("línea" if nb == 1 else "líneas") if conv.lines else ("intervalo" if nb == 1 else "intervalos")
            parts.append(f"<b>{nb} {what}</b> de {info.emin / f:.6g} a {info.emax / f:.6g} {unit} · "
                         f"energía media {info.mean / f:.5g} {unit} · {plural(len(conv.rows), 'fila')} "
                         f"{'SLINES' if conv.lines else 'SPECTR'}")
            for e in info.errors:
                parts.append(f"<span style='color:#b42318'>✖ {html.escape(e)}</span>")
            for pr in info.problems:
                parts.append(f"<span style='color:#9a6700'>⚠ {html.escape(pr)}</span>")
        else:
            self.plot.set_info(None)
            why = html.escape(conv.error or "No sale ningún espectro.")
            parts.append(f"<span style='color:#b42318'>✖ {why}</span>")
        for n in conv.notes:
            parts.append(f"<span style='color:{MUTED}'>{html.escape(n)}</span>")
        if conv.particle is not None and self.particle not in (None, 0) and conv.particle != self.particle:
            have = sf.PARTICLE_NAMES.get(conv.particle, "otra partícula")
            want = sf.PARTICLE_NAMES.get(self.particle, "otra partícula")
            parts.append(f"<span style='color:#9a6700'>⚠ Es el espectro de {have} y la fuente es de {want}: cambie la "
                         "partícula en el panel «Fuente» si hace falta (SKPAR).</span>")
        self.info.setText("<br>".join(parts))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(conv.error is None and bool(conv.rows))


def ask_spectrum_file(parent, settings=None, particle: int | None = None, exec_fn=None) -> sf.Converted | None:
    """Elegir un fichero de espectro, leerlo y enseñar la vista previa: el espectro elegido o None."""
    start = str(settings.value(SETTINGS_KEY, "") or "") if settings is not None else ""
    path, _ = QFileDialog.getOpenFileName(parent, "Espectro de la fuente desde un fichero", start, FILE_FILTER)
    if not path:
        return None
    if settings is not None:
        settings.setValue(SETTINGS_KEY, str(Path(path).parent))
    try:
        data = sf.read_spectrum_file(path)
    except (OSError, ValueError) as exc:
        QMessageBox.warning(parent, "ARGUS", f"No se puede leer el espectro de {Path(path).name}:\n\n{exc}")
        return None
    dlg = SpectrumImportDialog(parent, data, particle)
    ok = exec_fn(dlg) if exec_fn is not None else dlg.exec() == QDialog.DialogCode.Accepted
    return dlg.result() if ok else None


__all__ = ["FORMAT_HELP", "SpectrumImportDialog", "SpectrumStartDialog", "ask_spectrum_file", "show_format_help"]
