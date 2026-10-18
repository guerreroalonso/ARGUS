"""Novena revisión (0.16.3), en la ventana: la fuente que penhmain no lee, el editor de espectros, los cuerpos de un
detector y los plurales del panel Fuente."""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from argus.penhan import sections as sc  # noqa: E402
from argus.penhan import source_edit as se  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui
SB = QMessageBox.StandardButton
CASE = CORPUS / "usuario/100Mev15-174"
STACK = CORPUS / "penhan/stack"


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.ui.main_window import MainWindow

    msgs = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: msgs.append(("question", a[2])) or SB.No)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2])) or SB.Ok)
    app_settings().clear()
    w = MainWindow()
    w._msgs = msgs
    qtbot.addWidget(w)
    w.resize(1300, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def _copy(src, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy(f, dest / f.name)
    return dest


def test_a_source_penhmain_cannot_read_is_neither_shown_nor_edited(window, tmp_path, monkeypatch):
    w = window
    d = _copy(CASE, tmp_path / "caso")
    text = (d / "maniqui.in").read_text(encoding="latin-1").replace("SKPAR  4 ", "SKPAR  4.", 1)
    (d / "maniqui.in").write_text(text, encoding="latin-1")
    w.open_file(d / "maniqui.in")
    m = w.input.doc.model
    assert not m.ok and not m.source_read
    p = w.source_panel
    assert p.empty.isVisible() and "no puede leer la fuente" in p.empty_label.text()
    assert not p.g_position.isVisible() and not p.g_particle.isVisible()
    assert w.view.source is None  # sin la estrella de la fuente (estaría en 0, 0, 0)
    before = w.input.doc.text()
    opened = []
    monkeypatch.setattr(w, "_exec_dialog", lambda dlg: opened.append(dlg) or False)
    w.edit_spectrum()
    w.fit_source_box()
    w.set_source_bodies([1])
    assert not opened and w.input.doc.text() == before
    assert "no puede leer la fuente" in w.statusBar().currentMessage()


def test_source_panel_texts_in_singular(window, tmp_path):
    w = window
    d = _copy(STACK, tmp_path / "caso")
    w.open_file(d / "stack.in")
    w.input.edit("espectro", lambda doc: sc.set_spectrum(doc, [(9e6, 1.0), (1e7, -1.0)]))
    assert w.source_panel.spectrum_label.text().startswith("1 intervalo entre")
    w.input.edit("extensa", lambda doc: (se.set_box(doc, [0.01, 0.01, 0.01]),
                                         se.set_active_bodies(doc, w.session.geo, [1])))
    assert "en el cuerpo marcado (SBODY)" in w.source_panel.bodies_hint.text()


# ---------------------------------------------------------------------------------------------------------------------
# Editor de espectros
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def spectrum(qtbot):
    from argus.ui.spectrum_dialog import SpectrumDialog

    dlg = SpectrumDialog(None, [(1e6, 0.5), (2e6, 0.5), (3e6, -1.0)], False, particle="Fotones.", kparp=2,
                         settings=app_settings())
    qtbot.addWidget(dlg)
    return dlg


def _texts(dlg):
    return [(dlg.table.item(r, 0).text(), dlg.table.item(r, 1).text()) for r in range(dlg.table.rowCount())]


def test_a_row_with_an_invalid_value_is_kept(spectrum, monkeypatch):
    dlg = spectrum
    dlg.unit.setCurrentText("MeV")
    dlg.table.item(1, 1).setText("x0,5")  # probabilidad no válida en la fila de 2 MeV
    assert dlg.table.item(1, 1).data(Qt.ItemDataRole.UserRole) is None
    dlg.unit.setCurrentText("keV")
    assert dlg.table.rowCount() == 3 and _texts(dlg)[1] == ("2000", "x0,5")
    dlg._sort()
    assert dlg.table.rowCount() == 3 and ("2000", "x0,5") in _texts(dlg)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]) or SB.Ok)
    dlg._normalize()
    assert shown and "no válidos" in shown[0] and dlg.table.rowCount() == 3
    assert not dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()


def test_paste_takes_the_unit_of_the_header(spectrum):
    dlg = spectrum
    dlg.unit.setCurrentText("MeV")
    QGuiApplication.clipboard().setText("E (keV)\tP\n100\t0,1\n200\t-1\n")
    dlg._paste()
    assert dlg.rows() == [(1e5, 0.1), (2e5, -1.0)] and dlg.unit.currentText() == "keV"
    # Sin unidad en la cabecera, la de la tabla.
    dlg.unit.setCurrentText("MeV")
    QGuiApplication.clipboard().setText("100\t0,1\n200\t-1\n")
    dlg._paste()
    assert dlg.rows() == [(1e8, 0.1), (2e8, -1.0)]


# ---------------------------------------------------------------------------------------------------------------------
# Cuerpos de un detector
# ---------------------------------------------------------------------------------------------------------------------

def test_body_checklist_keeps_the_written_references(qtbot, tmp_path):
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry
    from argus.ui.sim_widgets import BodyChecklist

    geo = compile_geometry(load_geometry(STACK / "3discs.geo"), parinp=[0.002, 0.004, 0.008, 0.008])
    assert geo.ok
    w = BodyChecklist()
    qtbot.addWidget(w)
    w.set_geometry(geo)
    w.set_refs([sc.BodyRef(None, 2), sc.BodyRef("   3", None)])
    item = next(w.list.item(i) for i in range(w.list.count()) if w.list.item(i).data(Qt.ItemDataRole.UserRole)
                == ("kb", 1))
    item.setCheckState(Qt.CheckState.Checked)
    refs = w.refs()
    assert [(r.label, r.kb) for r in refs] == [("   1", None), (None, 2), ("   3", None)]
