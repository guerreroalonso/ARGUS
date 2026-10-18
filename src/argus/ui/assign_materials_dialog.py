"""«Asignar materiales…» (0.16.0, ADR-020): a los materiales de un .in que ARGUS no ha identificado (el usuario trae
su geometría y su .in, y del comentario de la línea MFNAME no se sabe qué material es) se les elige uno de la lista
de PENELOPE o de los materiales personalizados, para que ARGUS pueda generar su .mat.

Cada fila tiene el campo que busca al escribir (el de «Añadir un material») y, si el nombre del fichero o la
descripción lo sugieren («agua.mat», «hueso_cort.mat»), viene ya propuesto: se ve, se cambia o se borra. Con
«Composición propia…» se escribe la de uno (el diálogo de siempre, que no cambia nada todavía). Todo lo asignado se
aplica al «Asignar», en un solo paso de deshacer; «Cancelar» no cambia nada (revisión de 0.16).
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.numfmt import plural
from ..penhan import composition as cp
from ..penhan.library import Library
from ..penhan.material_names import ListEntry, best_guess, penelope_entries
from ..penhan.materials import MaterialRow, known_number, mat_filename_problem, match_material
from .add_material_dialog import MaterialChoice, library_entries
from .material_picker import MaterialPicker
from .screen_fit import fit_to_screen

OK_COLOR, GREY, WARN_COLOR, BAD_COLOR = "#1a7f37", "#666666", "#8a5a00", "#b42318"


@dataclass
class Assignment:
    number: int
    description: str  # lo que se escribe en el comentario de su línea MFNAME
    composition: cp.Composition | None  # la composición propia (un material personalizado) o None (lista)
    color: str | None = None
    fname: str | None = None  # otro fichero .mat (elegido en «Composición propia…»); None: el que tiene


class AssignMaterialsDialog(QDialog):
    """Un campo por material sin identificar; «Asignar» devuelve lo elegido (:meth:`assignments`)."""

    def __init__(self, parent, rows: list[MaterialRow], mlist: list[tuple[int, str]],
                 library: Library | None = None, *, bodies: Callable[[MaterialRow], str] | None = None,
                 compose: Callable[[int, MaterialChoice | None, set[str]], MaterialChoice | None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Asignar materiales")
        self.rows = list(rows)
        self._rows = {r.number: r for r in self.rows}
        self.mlist = mlist
        self.library = library if library is not None else Library()
        self.compose = compose
        self.entries: list[ListEntry] = library_entries(self.library) + penelope_entries(mlist)
        self.pickers: dict[int, MaterialPicker] = {}
        self.hints: dict[int, QLabel] = {}
        # Lo escrito con «Composición propia…» (o elegido en ese diálogo): se aplica al «Asignar», con lo demás.
        self.composed: dict[int, MaterialChoice] = {}
        self._proposed: dict[int, str] = {}  # lo propuesto por el nombre del fichero o la descripción
        lay = QVBoxLayout(self)
        n = len(self.rows)
        intro = QLabel(f"ARGUS no sabe de qué {'está hecho' if n == 1 else 'están hechos'} "
                       f"{plural(n, 'material', 'materiales')} de este .in, así que no puede generar su .mat con "
                       "penhmat. Elija cada uno en la lista de PENELOPE o en sus materiales personalizados (★): el "
                       "campo busca al escribir, en español o en inglés, y ▾ los enseña todos. Si el nombre del "
                       "fichero lo sugiere, ya viene propuesto: compruébelo.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        if not self.mlist:
            warn = QLabel(f"<span style='color:{WARN_COLOR}'>No se encuentra la lista de materiales de PENELOPE "
                          "(database/material-list.txt; Opciones → Programas de penhan…): solo se pueden elegir sus "
                          "materiales personalizados.</span>")
            warn.setWordWrap(True)
            lay.addWidget(warn)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setColumnStretch(1, 1)
        grid.addWidget(self._head("Material del .in"), 0, 0)
        grid.addWidget(self._head("Es…"), 0, 1)
        for i, r in enumerate(self.rows, start=1):
            who = bodies(r) if bodies is not None else ""
            left = QLabel(f"<b>n.º {r.number}</b> · <tt>{html.escape(r.fname)}</tt>"
                          + (f"<br><span style='color:{GREY}'>{html.escape(r.description)}</span>"
                             if r.description else "")
                          + (f"<br><span style='color:{GREY}'>{html.escape(who)}</span>" if who else ""))
            left.setTextFormat(Qt.TextFormat.RichText)
            left.setWordWrap(True)
            left.setMinimumWidth(220)
            grid.addWidget(left, 2 * i - 1, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
            picker = MaterialPicker()
            picker.set_entries(self.entries)
            picker.setPlaceholderText("Escriba para buscar o pulse ▾")
            grid.addWidget(picker, 2 * i - 1, 1)
            if compose is not None:
                b = QPushButton("Composición propia…")
                b.setAutoDefault(False)
                b.setToolTip("Escribir su composición (fórmula o elementos, densidad, energía media de excitación)")
                b.clicked.connect(lambda _c=False, r=r: self._compose(r))
                grid.addWidget(b, 2 * i - 1, 2)
            hint = QLabel()
            hint.setTextFormat(Qt.TextFormat.RichText)
            hint.setWordWrap(True)
            grid.addWidget(hint, 2 * i, 1, 1, 2)
            self.pickers[r.number] = picker
            self.hints[r.number] = hint
            # Solo si es la mejor sin empate («hueso.mat»: el compacto y el cortical están igual de cerca).
            proposal = best_guess([r.fname, r.description], self.entries)
            if proposal is not None:
                picker.setText(proposal.text)
                self._proposed[r.number] = proposal.text
            picker.textChanged.connect(lambda _t, n=r.number: self._update(n))
            self._update(r.number)
        grid.setRowStretch(2 * len(self.rows) + 1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        lay.addWidget(scroll, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Asignar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._update_status()
        fit_to_screen(self, 860, min(140 + 80 * len(self.rows), 640))

    @staticmethod
    def _head(text: str) -> QLabel:
        lab = QLabel(f"<b>{html.escape(text)}</b>")
        lab.setTextFormat(Qt.TextFormat.RichText)
        return lab

    def entry_for(self, number: int) -> ListEntry | None:
        """El material elegido para el n.º ``number`` (None si lo escrito no es ninguno de las listas)."""
        picker = self.pickers.get(number)
        if picker is None or number in self.composed:
            return None
        text = picker.text().strip()
        return next((e for e in self.entries if e.text == text), None) if text else None

    def _file_problem(self, number: int) -> str | None:
        """Por qué no se le puede dar una composición propia con su fichero de ahora (penhmat la escribe en un .mat
        de la carpeta del .in, con un nombre que penhmain pueda leer)."""
        r = self._rows.get(number)
        return mat_filename_problem(r.fname) if r is not None else None

    def _update(self, number: int) -> None:
        hint = self.hints[number]
        c = self.composed.get(number)
        if c is not None:
            k = known_number(c.description, self.mlist)
            item = match_material(c.description, self.mlist) if k is None and c.composition is None else None
            if c.composition is not None:
                what = f"Con su composición propia: {c.composition.summary()}"
            else:
                what = f"n.º {k if k is not None else item[0]} de la lista de PENELOPE"
            r = self._rows.get(number)
            if r is not None and c.fname != r.fname:
                what += f"; fichero {c.fname}"
            hint.setText(f"<span style='color:{OK_COLOR}'>✔ {html.escape(what)} (se aplica al «Asignar»).</span>")
        else:
            e = self.entry_for(number)
            text = self.pickers[number].text().strip()
            proposed = self._proposed.get(number)
            if e is None:
                hint.setText(f"<span style='color:{WARN_COLOR}'>No es ninguno de la lista: elíjalo al escribir o con "
                             "▾ (o déjelo vacío para no asignarle nada).</span>" if text else
                             f"<span style='color:{GREY}'>Sin asignar.</span>")
            elif e.personal and (problem := self._file_problem(number)) is not None:
                hint.setText(f"<span style='color:{BAD_COLOR}'>✖ {html.escape(problem)} Una composición propia "
                             "necesita un .mat en la carpeta del .in: cambie el nombre del fichero con «Composición "
                             "propia…» (o en la tabla de materiales). Así no se asigna.</span>")
            elif e.personal:
                m = self.library.find(e.key or e.text)
                why = " (propuesto por su nombre: compruébelo)" if text == proposed else ""
                hint.setText(f"<span style='color:{OK_COLOR}'>✔ Material personalizado: "
                             f"{html.escape(m.summary() if m else e.secondary)}{html.escape(why)}.</span>")
            else:
                why = " (propuesto por su nombre: compruébelo)" if text == proposed else ""
                hint.setText(f"<span style='color:{OK_COLOR}'>✔ n.º {e.number} de la lista de PENELOPE"
                             f"{html.escape(why)}.</span>")
        if hasattr(self, "status"):
            self._update_status()

    def _update_status(self) -> None:
        n = len(self.assignments())
        rest = len(self.rows) - n
        self.status.setText(f"Se asignará{'n' if n != 1 else ''} {n} de {len(self.rows)}."
                            + (f" {plural(rest, 'material', 'materiales')} se queda{'n' if rest != 1 else ''} sin "
                               "asignar." if rest else ""))

    def _identifies(self, c: MaterialChoice) -> bool:
        """¿Con lo elegido sabe ARGUS de qué está hecho? (composición propia o uno de la lista de PENELOPE)."""
        return c.composition is not None or known_number(c.description, self.mlist) is not None or \
            match_material(c.description, self.mlist) is not None

    def _compose(self, r: MaterialRow) -> None:
        """«Composición propia…»: el diálogo del material (no cambia nada todavía; lo elegido se aplica al
        «Asignar»)."""
        if self.compose is None:
            return
        # Los ficheros que tendrán las demás filas con lo elegido en su «Composición propia…» (aún no están en el
        # .in): dos composiciones con el mismo .mat nuevo se pisarían al asignar (revisión de 0.16).
        pending = {c.fname for k, c in self.composed.items() if k != r.number and c.fname}
        c = self.compose(r.number, self.composed.get(r.number), pending)
        if c is None:
            return
        if not self._identifies(c):
            self.composed.pop(r.number, None)
            picker = self.pickers[r.number]
            picker.setEnabled(True)
            picker.setText("")  # lo de antes no se asigna: la fila queda sin asignar, como dice
            self._update(r.number)
            self.hints[r.number].setText(f"<span style='color:{WARN_COLOR}'>Con eso ARGUS sigue sin saber de qué "
                                         "está hecho (ni composición propia ni uno de la lista de PENELOPE): no se "
                                         "asigna.</span>")
            return
        self.composed[r.number] = c
        picker = self.pickers[r.number]
        picker.setText(c.description)
        picker.setEnabled(False)
        self._update(r.number)

    def assignments(self) -> list[Assignment]:
        out = []
        for r in self.rows:
            c = self.composed.get(r.number)
            if c is not None:
                out.append(Assignment(r.number, c.description, c.composition, c.color, c.fname))
                continue
            e = self.entry_for(r.number)
            if e is None:
                continue
            if e.personal:
                m = self.library.find(e.key or e.text)
                if m is None or self._file_problem(r.number) is not None:
                    continue
                out.append(Assignment(r.number, m.name, m.composition, m.color))
            else:
                out.append(Assignment(r.number, e.text, None))
        return out


__all__ = ["AssignMaterialsDialog", "Assignment"]
