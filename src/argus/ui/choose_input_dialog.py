"""Diálogo «¿con qué .in abro esta geometría?», cuando varios .in de la carpeta la usan."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..penhan.inputfile import parse_in_file
from ..penhan.quickscan import InputSummary
from ..penhan.validate import PARTICLES
from .screen_fit import fit_to_screen
from .source_panel import format_energy

# Lo que devuelve choice() si se elige abrir la geometría sin .in.
GEOMETRY_ONLY = ""


def describe_source(source) -> str:
    """Resumen de la fuente de un .in: partícula y energía (o espectro, nucleido, espacio de fases)."""
    if source.from_psf:
        return "espacio de fases: " + ", ".join(source.psf)
    if source.radioactive:
        return "fuente radiactiva" + (f" ({source.nuclide})" if source.nuclide else "")
    name = PARTICLES.get(source.kparp, f"KPARP {source.kparp}")
    if source.spectrum:
        energies = [e for e, _ in source.spectrum]
        return f"{name}, espectro de {format_energy(min(energies))} a {format_energy(max(energies))}"
    if source.lines:
        n = len(source.lines)
        return f"{name}, {n} línea{'s' if n != 1 else ''} de energía"
    return f"{name}, {format_energy(source.energy)}"


def describe_input(summary: InputSummary) -> dict[str, str]:
    """Columnas del diálogo para un .in: título, fuente, PARINP y fecha."""
    out = {"title": "", "source": "", "parinp": "—", "date": ""}
    if summary.parinp:
        out["parinp"] = ", ".join(f"{i} = {v:g}" for i, v in sorted(summary.parinp.items()))
    if summary.mtime is not None:
        out["date"] = datetime.fromtimestamp(summary.mtime).strftime("%d-%m-%Y %H:%M")
    try:
        model = parse_in_file(summary.path).model
    except Exception:  # noqa: BLE001 - un .in ilegible se puede elegir igual; ARGUS dirá qué le pasa
        out["source"] = "(no se ha podido leer)"
        return out
    out["title"] = model.title.strip()
    out["source"] = describe_source(model.source)
    if model.parinp:
        out["parinp"] = ", ".join(f"{i} = {v:g}" for i, v in sorted(model.parinp.items()))
    return out


class ChooseInputDialog(QDialog):
    """Lista los .in que usan la geometría; se elige uno o «Solo la geometría».

    ``last``: nombre del .in elegido la vez anterior (``GEOMETRY_ONLY`` si se
    abrió sin .in); si no, se propone el modificado más recientemente.
    ``parinp_used``: índices PARINP que usa la geometría (se explica que cada
    .in puede darle medidas distintas).
    """

    COLUMNS = ("Fichero .in", "Título", "Fuente", "PARINP", "Modificado")

    def __init__(self, parent, geo_path: str | Path, candidates: list[InputSummary], last: str | None = None,
                 parinp_used: list[int] | None = None):
        super().__init__(parent)
        geo_path = Path(geo_path)
        self.setWindowTitle(f"Abrir {geo_path.name}")
        self.candidates = list(candidates)
        self._choice: Path | str | None = None
        lay = QVBoxLayout(self)
        intro = QLabel(f"Hay {len(candidates)} ficheros .in en la carpeta que usan la geometría "
                       f"<b>{html.escape(geo_path.name)}</b> (línea GEOMFN). ¿Con cuál la abro?")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.table = QTreeWidget(self)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setHeaderLabels(list(self.COLUMNS))
        for s in self.candidates:
            info = describe_input(s)
            item = QTreeWidgetItem([s.path.name, info["title"], info["source"], info["parinp"], info["date"]])
            item.setData(0, Qt.ItemDataRole.UserRole, str(s.path))
            item.setToolTip(0, str(s.path))
            item.setToolTip(1, info["title"])
            self.table.addTopLevelItem(item)
        header = self.table.header()
        header.setStretchLastSection(False)
        for col in range(len(self.COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda *_: self._open_selected())
        lay.addWidget(self.table)

        if parinp_used:
            idx = ", ".join(str(i) for i in parinp_used)
            note = QLabel(f"La geometría toma medidas de PARINP ({idx}): cada .in puede darle valores distintos. "
                          "Sin .in se verá con los números escritos en el propio .geo.")
            note.setWordWrap(True)
            note.setStyleSheet("color: #666")
            lay.addWidget(note)

        buttons = QDialogButtonBox(self)
        self.btn_open = buttons.addButton("Abrir con este .in", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_geometry = buttons.addButton("Solo la geometría", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_cancel = buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_geometry.clicked.connect(self._geometry_only)
        self.btn_cancel.clicked.connect(self.reject)
        lay.addWidget(buttons)

        self._preselect(last)
        # La lista, de la altura de sus filas (hasta un máximo); el diálogo se puede agrandar.
        n = self.table.topLevelItemCount()
        row = max(self.table.sizeHintForRow(0) if n else 0, self.table.fontMetrics().height() + 4)
        want = min(header.sizeHint().height() + row * max(n, 2) + 2 * self.table.frameWidth() + 8, 380)
        hint = self.sizeHint()
        fit_to_screen(self, max(820, hint.width()), hint.height() - self.table.sizeHint().height() + want)

    def _preselect(self, last: str | None) -> None:
        row = None
        if last:
            row = next((i for i, s in enumerate(self.candidates) if s.path.name == last), None)
        if row is None and self.candidates:
            row = max(range(len(self.candidates)), key=lambda i: self.candidates[i].mtime or 0.0)
        if row is not None:
            self.table.setCurrentItem(self.table.topLevelItem(row))
        geometry_default = last == GEOMETRY_ONLY
        self.btn_geometry.setDefault(geometry_default)
        self.btn_open.setDefault(not geometry_default)
        (self.btn_geometry if geometry_default else self.table).setFocus()

    def selected_input(self) -> Path | None:
        item = self.table.currentItem()
        return Path(item.data(0, Qt.ItemDataRole.UserRole)) if item is not None else None

    def _open_selected(self) -> None:
        path = self.selected_input()
        if path is None:
            return
        self._choice = path
        self.accept()

    def _geometry_only(self) -> None:
        self._choice = GEOMETRY_ONLY
        self.accept()

    def choice(self) -> Path | str | None:
        """El .in elegido, ``GEOMETRY_ONLY`` (solo la geometría) o None (cancelado)."""
        return self._choice if self.result() == QDialog.DialogCode.Accepted else None
