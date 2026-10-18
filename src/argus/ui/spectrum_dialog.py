"""Editor del espectro de energía de la fuente (líneas SPECTR o SLINES del .in).

La tabla se puede escribir, pegar (de una hoja de cálculo o de otro .in),
importar de un fichero de espectro (``spectrum_import``: el formato de ARGUS,
tablas o un espectro de penhmain) o guardar en el formato de ARGUS. A la derecha se ve el espectro tal
como lo muestreará penhmain (ordenado por SORT2, con las probabilidades
negativas a cero y normalizado), y en cada fila qué papel tiene: un
intervalo continuo, la energía máxima (cuya probabilidad no se usa) o una
energía exacta.
"""

from __future__ import annotations

import html
import math
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import plural
from ..penhan.inputfile import fmt_real
from ..penhan.sections import MAX_SPECTRUM, analyze_spectrum, parse_spectrum_text, sort2
from ..penhan.spectrum_file import decode_text, format_spectrum  # noqa: F401 - decode_text se usaba desde aquí
from .screen_fit import fit_to_screen
from .source_panel import parse_energy, parse_number
from .spectrum_plot import SpectrumPlot, energy_unit

UNITS = (("MeV", 1e6), ("keV", 1e3), ("eV", 1.0), ("GeV", 1e9))
ROLE_COLOR = {"max": "#8a5a00", "zero": "#b42318", "neg": "#b42318", "ok": "#555555"}


def sort_order(energies: list[float]) -> list[int]:
    """Posición de cada fila tras la ordenación SORT2 de penhmain."""
    _, idx = sort2(energies, [float(i + 1) for i in range(len(energies))])
    order = [int(round(v)) - 1 for v in idx]  # order[j] = fila que queda en la posición j
    pos = [0] * len(order)
    for j, i in enumerate(order):
        pos[i] = j
    return pos


def row_roles(rows: list[tuple[float, float]], lines: bool) -> list[tuple[str, str]]:
    """(tipo, texto) del papel de cada fila: 'ok', 'max', 'zero' o 'neg'."""
    if not rows:
        return []
    info = analyze_spectrum(rows, lines)
    pos = sort_order([e for e, _ in rows])
    f, unit = energy_unit(max(abs(info.emax), 1.0))
    out = []
    n = len(rows)
    for i, (e, p) in enumerate(rows):
        j = pos[i]
        if lines:
            pr = info.bins[j][2] if j < len(info.bins) else 0.0
            out.append(("neg" if p < 0 else "ok", f"{100 * pr:.4g} % a {e / f:.6g} {unit}" if p >= 0
                        else "P < 0: no se emite"))
            continue
        if j == n - 1:
            out.append(("max", "energía máxima (su P no se usa)" if p >= 0 else "energía máxima"))
            continue
        a, b, pr = info.bins[j]
        if p < 0:
            out.append(("neg", f"P < 0: el intervalo hasta {b / f:.6g} {unit} no se muestrea"))
        elif b == a:
            out.append(("zero", f"anchura cero: {100 * pr:.3g} % exactamente a {a / f:.6g} {unit}"))
        else:
            out.append(("ok", f"{100 * pr:.4g} % uniforme hasta {b / f:.6g} {unit}"))
    return out


class SpectrumDialog(QDialog):
    def __init__(self, parent, rows: list[tuple[float, float]], lines: bool = False, particle: str = "",
                 kparp: int | None = None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Espectro de la fuente")
        self.kparp = kparp  # partícula de la fuente (para elegir la serie de un espectro de penhmain)
        self.settings = settings
        fit_to_screen(self, 980, 600)
        self._updating = False
        self._error: str | None = None
        lay = QVBoxLayout(self)
        # Tipo de espectro.
        self.rb_cont = QRadioButton("Espectro continuo (SPECTR): en cada intervalo, energía uniforme entre su "
                                    "E y la E de la fila siguiente")
        self.rb_lines = QRadioButton("Líneas (SLINES): energías exactas, cada una con su probabilidad")
        self.kind = QButtonGroup(self)
        self.kind.addButton(self.rb_cont, 0)
        self.kind.addButton(self.rb_lines, 1)
        (self.rb_lines if lines else self.rb_cont).setChecked(True)
        self.kind.idClicked.connect(lambda _i: self._refresh())
        lay.addWidget(self.rb_cont)
        lay.addWidget(self.rb_lines)
        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split, 1)
        # Tabla.
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("Energías en:"))
        self.unit = QComboBox()
        for name, _ in UNITS:
            self.unit.addItem(name)
        rmax = max((abs(e) for e, _ in rows), default=1e6)
        self.unit.setCurrentText(energy_unit(rmax)[1] if rmax >= 1e3 else "eV")
        # (con todas las filas: también las que tienen un valor no válido, tal como están escritas)
        self.unit.currentIndexChanged.connect(lambda _i: self._fill_cells(self._cells()))
        top.addWidget(self.unit)
        top.addStretch(1)
        ll.addLayout(top)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Energía", "Probabilidad", "Cómo lo usa penhmain"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 110)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item)
        ll.addWidget(self.table, 1)
        btns = QHBoxLayout()
        for text, slot, tip in (
                ("Añadir fila", self._add, "Añade una fila al final"),
                ("Quitar", self._remove, "Quita las filas seleccionadas"),
                ("Ordenar", self._sort, "Ordena por energía (penhmain también lo hace al leer)"),
                ("Normalizar", self._normalize, "Escala las probabilidades para que sumen 1 (penhmain normaliza "
                                                "de todos modos)"),
                ("Pegar", self._paste, "Sustituye la tabla por dos columnas copiadas (hoja de cálculo, texto o "
                                       "líneas SPECTR de otro .in)"),
                ("Importar…", self._import, "Lee un fichero de espectro (formato de ARGUS, tabla de dos o tres "
                                            "columnas, líneas SPECTR/SLINES o un espectro de penhmain), con vista "
                                            "previa"),
                ("Guardar…", self._save, "Guarda el espectro en un fichero (formato de ARGUS) para usarlo en "
                                         "otros casos")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        ll.addLayout(btns)
        split.addWidget(left)
        # Gráfica e información.
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        mode = QHBoxLayout()
        mode.addWidget(QLabel("Ver:"))
        self.view_mode = QComboBox()
        self.view_mode.addItem("probabilidad de cada intervalo", "prob")
        self.view_mode.addItem("densidad (por unidad de energía)", "density")
        self.view_mode.currentIndexChanged.connect(lambda _i: self.plot.set_mode(self.view_mode.currentData()))
        mode.addWidget(self.view_mode)
        mode.addStretch(1)
        rl.addLayout(mode)
        self.plot = SpectrumPlot()
        rl.addWidget(self.plot, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        rl.addWidget(self.info)
        self.btn_to_lines = QPushButton("Convertir en líneas (SLINES)…")
        self.btn_to_lines.setToolTip("Cada fila con P ≥ 0 pasa a ser una energía exacta con su probabilidad")
        self.btn_to_lines.clicked.connect(self._to_lines)
        rl.addWidget(self.btn_to_lines, 0, Qt.AlignmentFlag.AlignLeft)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        note = QLabel(f"{html.escape(particle)} Hasta {MAX_SPECTRUM} filas. Las energías se escriben en eV en el "
                      ".in; en la tabla se admiten unidades («91,2 MeV», «15 keV»).")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        lay.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar al .in")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._refresh)
        self._fill(list(rows))

    # -- datos --------------------------------------------------------------------------
    def factor(self) -> float:
        return dict(UNITS)[self.unit.currentText()]

    def is_lines(self) -> bool:
        return self.rb_lines.isChecked()

    def rows(self) -> list[tuple[float, float]]:
        out = []
        for r in range(self.table.rowCount()):
            e = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            p = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
            if e is not None and p is not None:
                out.append((float(e), float(p)))
        return out

    def _cells(self) -> list[tuple[float | None, float | None, str, str]]:
        """Todas las filas de la tabla, también las que tienen un valor no válido (que ``rows`` deja fuera): (E, P, lo
        escrito en E, lo escrito en P); un valor no válido es None."""
        out = []
        for r in range(self.table.rowCount()):
            ie, ip = self.table.item(r, 0), self.table.item(r, 1)
            out.append((ie.data(Qt.ItemDataRole.UserRole), ip.data(Qt.ItemDataRole.UserRole), ie.text(), ip.text()))
        return out

    def _fill_cells(self, cells: list[tuple[float | None, float | None, str, str]]) -> None:
        """Como ``_fill``, sin perder las filas con un valor no válido (se quedan como están escritas, en rojo:
        revisión de 0.16.2, al cambiar la unidad, ordenar o normalizar desaparecían)."""
        self._updating = True
        try:
            self.table.setRowCount(len(cells))
            for r, (e, p, te, tp) in enumerate(cells):
                self._set_row(r, e, p)
                for c, v, t in ((0, e, te), (1, p, tp)):
                    if v is None and t.strip():
                        self.table.item(r, c).setText(t)
                        self.table.item(r, c).setBackground(QColor("#f8d7da"))
        finally:
            self._updating = False
        self._refresh()

    def _invalid_rows(self) -> int:
        return sum(1 for e, p, _te, _tp in self._cells() if e is None or p is None)

    def _fmt_e(self, e: float) -> str:
        q = (Decimal(repr(float(e))) / Decimal(repr(self.factor()))).normalize()
        return format(q, "f") if abs(q.adjusted()) < 12 else fmt_real(float(q))

    def _fill(self, rows: list[tuple[float, float]]) -> None:
        self._updating = True
        try:
            self.table.setRowCount(len(rows))
            for r, (e, p) in enumerate(rows):
                self._set_row(r, e, p)
        finally:
            self._updating = False
        self._refresh()

    def _set_row(self, r: int, e: float | None, p: float | None) -> None:
        it_e = QTableWidgetItem("" if e is None else self._fmt_e(e))
        it_e.setData(Qt.ItemDataRole.UserRole, e)
        it_e.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        it_p = QTableWidgetItem("" if p is None else fmt_real(p))
        it_p.setData(Qt.ItemDataRole.UserRole, p)
        it_p.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        role = QTableWidgetItem("")
        role.setFlags(role.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 0, it_e)
        self.table.setItem(r, 1, it_p)
        self.table.setItem(r, 2, role)

    def _on_item(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() > 1:
            return
        text = item.text().strip()
        self._updating = True
        try:
            if item.column() == 0:
                try:
                    has_unit = any(c.isalpha() and c not in "eEdD" for c in text)
                    v = parse_energy(text) if has_unit else float(
                        Decimal(text.replace(",", ".").replace("d", "e").replace("D", "e"))
                        * Decimal(repr(self.factor())))
                    if not math.isfinite(v):  # «1e400», «inf», «nan»: no es una energía
                        raise ValueError(text)
                    item.setData(Qt.ItemDataRole.UserRole, v)
                    item.setText(self._fmt_e(v))
                    item.setBackground(QBrush())
                except (ValueError, InvalidOperation):
                    item.setData(Qt.ItemDataRole.UserRole, None)
                    item.setBackground(QColor("#f8d7da"))
            else:
                try:
                    v = parse_number(text)
                    item.setData(Qt.ItemDataRole.UserRole, v)
                    item.setText(fmt_real(v))
                    item.setBackground(QBrush())
                except ValueError:
                    item.setData(Qt.ItemDataRole.UserRole, None)
                    item.setBackground(QColor("#f8d7da"))
        finally:
            self._updating = False
        self._timer.start()

    # -- análisis ------------------------------------------------------------------------
    def _refresh(self) -> None:
        rows = self.rows()
        lines = self.is_lines()
        bad = sum(1 for r in range(self.table.rowCount())
                  if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) is None
                  or self.table.item(r, 1).data(Qt.ItemDataRole.UserRole) is None)
        if len(rows) > MAX_SPECTRUM:
            self._too_many(len(rows))  # el análisis (SORT2) tarda n²: con miles de filas congelaría la ventana
            return
        info = analyze_spectrum(rows, lines) if rows else None
        self.plot.set_info(info)
        # Papel de cada fila.
        roles = row_roles(rows, lines) if rows else []
        self._updating = True
        try:
            k = 0
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 2)
                ok = (self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) is not None
                      and self.table.item(r, 1).data(Qt.ItemDataRole.UserRole) is not None)
                if not ok:
                    it.setText("valor no válido")
                    it.setForeground(QColor(ROLE_COLOR["neg"]))
                    continue
                kind, text = roles[k]
                k += 1
                it.setText(text)
                it.setForeground(QColor(ROLE_COLOR[kind]))
        finally:
            self._updating = False
        # Resumen y problemas.
        parts = []
        self._error = None
        if bad:
            self._error = f"Hay {plural(bad, 'fila')} con valores no válidos."
        if info is not None:
            f, unit = energy_unit(max(abs(info.emax), 1.0))
            n = len(info.bins)
            what = ("línea" if n == 1 else "líneas") if lines else ("intervalo" if n == 1 else "intervalos")
            parts.append(f"<b>{n} {what}</b> entre {info.emin / f:.6g} y {info.emax / f:.6g} {unit} · energía "
                         f"media {info.mean / f:.5g} {unit}")
            if info.errors and self._error is None:
                self._error = info.errors[0]
            for e in info.errors:
                parts.append(f"<span style='color:#b42318'>✖ {html.escape(e)}</span>")
            for pr in info.problems:
                parts.append(f"<span style='color:#9a6700'>⚠ {html.escape(pr)}</span>")
            self.btn_to_lines.setVisible(not lines and any(b[0] == b[1] for b in info.bins))
        else:
            self._error = self._error or "El espectro está vacío."
            self.btn_to_lines.setVisible(False)
        if self._error and (info is None or self._error not in (info.errors or [])):
            parts.append(f"<span style='color:#b42318'>✖ {html.escape(self._error)}</span>")
        self.info.setText("<br>".join(parts))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self._error is None)

    def _too_many(self, n: int) -> None:
        self.plot.set_info(None)
        self._updating = True
        try:
            for r in range(self.table.rowCount()):
                self.table.item(r, 2).setText("")
        finally:
            self._updating = False
        self._error = (f"Hay {n} filas y penhmain admite como máximo {MAX_SPECTRUM} (MISPEC/MLINE): agrupe "
                       "intervalos o quite filas.")
        self.btn_to_lines.setVisible(False)
        self.info.setText(f"<span style='color:#b42318'>✖ {html.escape(self._error)}</span>")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def _report_skipped(self, title: str, skipped: list[str]) -> None:
        if skipped:
            shown = "\n".join(f"  {t}" for t in skipped[:8]) + ("\n  …" if len(skipped) > 8 else "")
            QMessageBox.information(self, title, f"Se {'ha' if len(skipped) == 1 else 'han'} saltado "
                                    f"{plural(len(skipped), 'fila')} cuya probabilidad no se puede leer:\n{shown}")

    # -- acciones ------------------------------------------------------------------------
    def _add(self) -> None:
        rows = self.rows()
        r = self.table.rowCount()
        self._updating = True
        try:
            self.table.insertRow(r)
            e = rows[-1][0] if rows else None
            self._set_row(r, e, 0.0 if rows else None)
        finally:
            self._updating = False
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))
        self._refresh()

    def _remove(self) -> None:
        sel = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in sel:
            self.table.removeRow(r)
        self._refresh()

    def _sort(self) -> None:
        # Las filas con una energía no válida, al final (como estaban).
        self._fill_cells(sorted(self._cells(), key=lambda c: (c[0] is None, c[0] if c[0] is not None else 0.0)))

    def _normalize(self) -> None:
        bad = self._invalid_rows()
        if bad:  # la suma no tendría en cuenta esas filas
            QMessageBox.information(self, "Normalizar", f"{'Hay una fila' if bad == 1 else f'Hay {bad} filas'} con "
                                    "valores no válidos (en rojo): corríjalas o quítelas antes de normalizar.")
            return
        rows = self.rows()
        if not rows:
            return
        if self.is_lines():
            tot = sum(p for _, p in rows if p > 0)
            if tot > 0:
                self._fill([(e, p / tot if p > 0 else p) for e, p in rows])
            return
        emax = max(e for e, _ in rows)
        last = max(i for i, (e, _) in enumerate(rows) if e == emax)
        tot = sum(p for i, (_, p) in enumerate(rows) if p > 0 and i != last)
        if tot > 0:
            self._fill([(e, (p / tot if p > 0 else p) if i != last else -1.0) for i, (e, p) in enumerate(rows)])

    def _paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        skipped: list[str] = []
        # Si la cabecera dice en qué unidad van las energías («E (keV)»), esa (como al importar un fichero: revisión
        # de 0.16.2, se tomaban en la unidad de la tabla).
        try:
            from ..penhan.spectrum_file import parse_table_text

            said = parse_table_text(text).unit
        except Exception:  # noqa: BLE001 - sin cabecera que se entienda, la unidad de la tabla
            said = None
        units = dict(UNITS)
        old_unit = self.unit.currentText()
        if said in units and said != old_unit:
            self.unit.blockSignals(True)
            self.unit.setCurrentText(said)
            self.unit.blockSignals(False)
        rows = parse_spectrum_text(text, self.factor(), skipped)
        if not rows and self.unit.currentText() != old_unit:
            self.unit.blockSignals(True)
            self.unit.setCurrentText(old_unit)
            self.unit.blockSignals(False)
        if not rows:
            QMessageBox.information(self, "Pegar", "El portapapeles no tiene filas de dos números (energía y "
                                    "probabilidad).")
            return
        self._fill(rows)
        self._report_skipped("Pegar", skipped)

    def _import(self) -> None:
        from .spectrum_import import ask_spectrum_file

        conv = ask_spectrum_file(self, self.settings, self.kparp)
        if conv is None:
            return
        (self.rb_lines if conv.lines else self.rb_cont).setChecked(True)
        emax = max((abs(e) for e, _ in conv.rows), default=1e6)
        self.unit.blockSignals(True)
        self.unit.setCurrentText(energy_unit(emax)[1] if emax >= 1e3 else "eV")
        self.unit.blockSignals(False)
        self._fill(conv.rows)

    def _save(self) -> None:
        from pathlib import Path

        from ..core.files import write_bytes_atomic
        from .spectrum_import import SETTINGS_KEY

        self._refresh()
        rows = self.rows()
        if not rows or self._error:
            QMessageBox.information(self, "Guardar", self._error or "El espectro está vacío.")
            return
        start = str(self.settings.value(SETTINGS_KEY, "") or "") if self.settings is not None else ""
        path, _ = QFileDialog.getSaveFileName(self, "Guardar el espectro", str(Path(start) / "espectro.txt"),
                                              "Texto (*.txt);;Todos los ficheros (*)")
        if not path:
            return
        if self.settings is not None:
            self.settings.setValue(SETTINGS_KEY, str(Path(path).parent))
        try:
            write_bytes_atomic(path, format_spectrum(rows, self.is_lines(), self.unit.currentText()).encode("utf-8"))
        except OSError as exc:
            QMessageBox.warning(self, "Guardar", f"No se ha podido guardar: {exc}")

    def _to_lines(self) -> None:
        rows = [(e, p) for e, p in self.rows() if p >= 0]
        self.rb_lines.setChecked(True)
        self._fill(rows)

    def accept(self) -> None:
        self._refresh()
        if self._error is None:
            super().accept()
