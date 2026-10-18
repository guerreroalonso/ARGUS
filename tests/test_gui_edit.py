"""Editor de geometría en la ventana (F6): añadir, cambiar, eliminar, deshacer y guardar."""

import shutil

import numpy as np
import pytest

pytest.importorskip("PySide6")

from argus.pengeom.parser import load_geometry  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"


@pytest.fixture
def case_dir(tmp_path):
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    answers = {"question": QMessageBox.StandardButton.Discard}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers["question"])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._answers = answers
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def accept_dialog(w, monkeypatch, **fields):
    """El diálogo «Añadir un cuerpo» se acepta con sus valores (o los dados) sin mostrarse."""
    seen = {}

    def fake(dlg):
        for name, value in fields.items():
            if name == "kind":
                dlg.kind.setCurrentIndex(dlg.kind.findData(value))
            elif name == "material":
                dlg.material.setEditText(str(value))
            else:
                getattr(dlg, name).setText(str(value))
        dlg._check()
        seen["ok"] = dlg._ok
        seen["message"] = dlg.status.text()
        return dlg._ok

    monkeypatch.setattr(w, "_exec_dialog", fake)
    return seen


def test_add_body_undo_redo_and_save(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    assert w.geo_ctrl is not None and not w.geo_ctrl.dirty
    nb = w.session.geo.nbodys
    seen = accept_dialog(w, monkeypatch, radius="40", material=2, name="Tumor")
    w.add_body_dialog(1, np.array([0.0, 0.0, 250.0]))
    assert seen["ok"], seen["message"]
    g = w.session.geo
    assert g.nbodys == nb + 1 and w.geo_ctrl.dirty
    new = g.find_body("3")
    assert new is not None and new.material == 2
    assert w.view.selected == new.index and w.geo_panel.kb == new.index  # queda seleccionado
    assert "Tumor" in w.source.toPlainText()  # el texto del .geo se actualiza
    assert w.windowTitle().endswith("cubo-esfera.geo*")
    ib, mat = w.session.locator.locate(np.array([[0.0, 0.0, 250.0], [0.0, 0.0, 300.0]]))
    assert mat.tolist() == [2, 1]
    # Deshacer y rehacer (Ctrl+Z / Ctrl+Y).
    w.act_undo.trigger()
    assert w.session.geo.nbodys == nb and not w.geo_ctrl.dirty
    w.act_redo.trigger()
    assert w.session.geo.nbodys == nb + 1
    # Guardar: .geo nuevo y copia .bak del anterior; PENGEOM lo leería igual.
    original = (case_dir / "cubo-esfera.geo").read_bytes()
    assert w.save_geometry()
    assert (case_dir / "cubo-esfera.geo.bak").read_bytes() == original
    saved = load_geometry(case_dir / "cubo-esfera.geo")
    assert any(b.label.strip() == "3" for b in saved.bodies())
    assert not w.geo_ctrl.dirty and not w.windowTitle().endswith("*")


def test_overlapping_body_cannot_be_added(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    nb = w.session.geo.nbodys
    seen = accept_dialog(w, monkeypatch, radius="100")
    w.add_body_dialog(1, np.array([0.0, 0.0, 450.0]))  # asoma por arriba del cubo
    assert not seen["ok"] and "No cabe entero" in seen["message"] and "caería en Cuerpo «2»" in seen["message"]
    assert w.session.geo.nbodys == nb and not w.geo_ctrl.dirty


def test_undo_order_across_in_and_geo(qtbot, window, case_dir, monkeypatch):
    from argus.penhan import source_edit as se

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [1.0, 2.0, -50.0]))
    accept_dialog(w, monkeypatch, kind="box", sx="50", sy="50", sz="50")
    w.add_body_dialog(1, np.array([0.0, 0.0, 250.0]))
    nb = w.session.geo.nbodys
    w.input.edit("Mover la fuente otra vez", lambda d: se.set_position(d, [3.0, 2.0, -50.0]))
    assert "Mover la fuente otra vez" in w.act_undo.text()
    w.undo()
    assert w.input.doc.model.source.position == (1.0, 2.0, -50.0) and w.session.geo.nbodys == nb
    assert "Añadir caja" in w.act_undo.text()
    w.undo()
    assert w.session.geo.nbodys == nb - 1
    w.undo()
    assert w.input.doc.model.source.position != (1.0, 2.0, -50.0)
    assert not w.input.dirty and not w.geo_ctrl.dirty
    w.redo()
    w.redo()
    assert w.input.doc.model.source.position == (1.0, 2.0, -50.0) and w.session.geo.nbodys == nb


def test_panel_changes_material_and_surface(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.select_body(1)
    panel = w.geo_panel
    assert panel.box_body.isVisibleTo(panel) and panel.material.value() == 1
    panel.material.setValue(2)
    panel.material.editingFinished.emit()
    assert w.session.geo.find_body("1").material == 2
    w.select_body(w.session.geo.find_body("2").index)  # la esfera de vacío
    panel.table.selectRow(0)
    ed = panel.param_edits["X-SCALE"]
    assert ed.value() == 5000.0
    ed.setText("6000")
    ed.setModified(True)
    ed.editingFinished.emit()
    assert "X-SCALE=(+6.000000000000000E+03" in w.geo_ctrl.text
    # Un cambio que PENGEOM no aceptaría (superficie sin definir) no cambia nada.
    from argus.pengeom.edit import find_element
    from argus.pengeom.model import RefLine

    def bad(doc, geo):
        el = find_element(doc, "body", "1")
        el.items.append(RefLine("SURFACE", "  99", -1))
        el.modified = True

    before = w.geo_ctrl.text
    assert w.edit_geometry("Mal", bad) is None
    assert w.geo_ctrl.text == before and w.session.geo.ok


def test_delete_body_from_the_context_menu(qtbot, window, case_dir, monkeypatch):
    from PySide6.QtWidgets import QMenu, QMessageBox

    w = window
    w.open_file(case_dir / "maniqui.in")
    accept_dialog(w, monkeypatch)
    w.add_body_dialog(1, np.array([0.0, 0.0, 250.0]))
    new = w.session.geo.find_body("3")
    captured = {}

    def fake_exec(menu: QMenu, *args):
        captured["actions"] = {a.text(): a for a in menu.actions()}

    monkeypatch.setattr(w, "_exec_menu", fake_exec)
    w._on_context_menu(None, np.array([0.0, 0.0, 250.0]), new.index)
    assert any(t.startswith("Añadir un cuerpo aquí") for t in captured["actions"])
    delete = next(a for t, a in captured["actions"].items() if t.startswith("Eliminar"))
    w._answers["question"] = QMessageBox.StandardButton.Yes
    delete.trigger()
    assert w.session.geo.find_body("3") is None
    assert w.session.locator.locate(np.array([[0.0, 0.0, 250.0]]))[1][0] == 1  # el cubo ocupa su sitio
    # Menú del árbol: añadir dentro de la envolvente.
    w._on_tree_menu(w.tree.visualItemRect(w.tree.topLevelItem(0)).center())
    assert any(t.startswith("Añadir un cuerpo") for t in captured["actions"])


def test_geometry_with_errors_is_not_editable(qtbot, window, tmp_path):
    w = window
    bad = tmp_path / "mal.geo"
    bad.write_text("Mal\n" + "0" * 64 + "\nBODY    (   1)\nMATERIAL(   1)\nSURFACE (   9), SIDE POINTER=(-1)\n"
                   + "0" * 64 + "\nEND      " + "0" * 55 + "\n")
    w.open_file(bad)
    assert w.geo_ctrl is None and not w.act_add_body.isEnabled()
