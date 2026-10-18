"""Ventana: crear módulos, pegarlos cara con cara, convertir cuerpos, añadir materiales y el icono."""

import shutil

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMenu  # noqa: E402

from argus.pengeom.extent import element_extent  # noqa: E402
from argus.penhan.inputfile import parse_in_text  # noqa: E402
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


def fake_exec(w, monkeypatch, fn):
    """Los diálogos se contestan sin mostrarse: ``fn(dlg)`` rellena y devuelve True (aceptar) o False."""
    seen = []

    def run(dlg):
        seen.append(dlg)
        return fn(dlg)

    monkeypatch.setattr(w, "_exec_dialog", run)
    return seen


def _extent(w, label):
    s = w.session
    return element_extent(s.geo, w._tracer(), s.geo.find_body(label).index, s.bounds)


def _set(dlg, **fields):
    for name, value in fields.items():
        if name == "kind":
            dlg.kind.setCurrentIndex(dlg.kind.findData(value))
        else:
            getattr(dlg, name).setText(str(value))
    dlg._update_glue_center()
    dlg._check()
    return dlg._ok


def test_add_module_glued_to_the_cube(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index
    sphere = w.session.geo.find_body("2").index

    def fill(dlg):
        assert dlg.is_module() and dlg.glue_target() == cube and dlg.container() == sphere
        dlg.kind.setCurrentIndex(dlg.kind.findData("box"))
        ok = _set(dlg, sx="100", sy="200", sz="300")
        assert dlg.cx.value() == 300.0 and not dlg.cx.isEnabled()  # x = 250 + 50: toca la cara +x
        assert (dlg.cy.value(), dlg.cz.value()) == (0.0, 250.0)  # centrado en la cara
        return ok

    fake_exec(w, monkeypatch, fill)
    w.add_body_dialog(sphere, None, module=True, glue_target=cube, glue_face="+x")
    g = w.session.geo
    new = [b for b in g.bodies if b.is_module and b.origin == "user"]
    assert len(new) == 1 and w.geo_ctrl.dirty
    lo, hi = _extent(w, new[0].label.strip())
    assert lo[0] == 250.0 and hi[0] == 350.0
    # Deshacer lo quita.
    w.undo()
    assert not any(b.is_module and b.origin == "user" for b in w.session.geo.bodies)


def test_container_suggestion_when_it_does_not_fit(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index
    sphere = w.session.geo.find_body("2").index

    def fill(dlg):
        # Una esfera de r = 100 a 600 cm del centro no cabe en el cubo, pero sí en la esfera grande.
        ok = _set(dlg, cx="600", cy="0", cz="250", radius="100")
        assert not ok and f"c:{sphere}" in dlg.status.text() and "Cabría dentro de" in dlg.status.text()
        dlg._on_link(f"c:{sphere}")
        dlg._check()
        assert dlg._ok and dlg.container() == sphere
        return True

    fake_exec(w, monkeypatch, fill)
    w.add_body_dialog(cube, np.array([0.0, 0.0, 250.0]))
    assert w.session.geo.nbodys == 4


def test_convert_and_glue_existing_module(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    g = w.session.geo
    sphere = g.find_body("2").index
    # Un módulo suelto lejos del cubo.
    fake_exec(w, monkeypatch, lambda dlg: _set(dlg, kind="sphere", cx="1500", cy="300", cz="100", radius="50"))
    w.add_body_dialog(sphere, None, module=True)
    mod = next(b for b in w.session.geo.bodies if b.is_module and b.origin == "user")
    label = mod.label.strip()
    # El cubo se convierte en módulo (el menú contextual lo ofrece).
    cube = w.session.geo.find_body("1").index
    menu = QMenu()
    w._add_edit_actions(menu, cube, None)
    texts = [a.text() for a in menu.actions()]
    assert any(t.startswith("Convertir Cuerpo «1» en módulo") for t in texts)
    w.convert_to_module(cube)
    assert w.session.geo.find_body("1").is_module
    # Con el módulo nuevo seleccionado, el menú del cubo ofrece pegarlo.
    w.select_body(w.session.geo.find_body(label).index)
    menu = QMenu()
    w._add_edit_actions(menu, w.session.geo.find_body("1").index, np.array([0.0, 0.0, 250.0]), face="+z")
    assert any("(el seleccionado) a Módulo «1» por su cara +z (encima)" in a.text() for a in menu.actions())

    def glue(dlg):
        assert dlg.target() == w.session.geo.find_body("1").index
        assert dlg.face_box.currentData() == "+z"
        dlg._update()
        assert dlg._ok, dlg.result.text()
        return True

    fake_exec(w, monkeypatch, glue)
    w.glue_module_dialog(w.session.geo.find_body(label).index, w.session.geo.find_body("1").index, "+z")
    lo, hi = _extent(w, label)
    assert lo[2] == 500.0 and np.allclose(0.5 * (lo + hi)[:2], [0.0, 0.0])  # encima del cubo, centrada
    # Un paso de deshacer lo devuelve a su sitio.
    w.undo()
    lo, hi = _extent(w, label)
    assert np.allclose(0.5 * (lo + hi), [1500.0, 300.0, 100.0])


def test_add_and_remove_material(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    n_before = len(w.input.doc.find("MFNAME"))

    def fill(dlg):
        assert dlg.number == n_before + 1
        dlg.name.setText("PMMA")
        assert dlg.fname() == "pmma.mat"
        return True

    fake_exec(w, monkeypatch, fill)
    w.add_material_dialog()
    doc = parse_in_text(w.input.doc.text(), None)
    assert [m.fname for m in doc.model.materials][-1] == "pmma.mat"
    assert w.input.dirty
    # La fila «＋ Añadir un material…» abre el mismo diálogo con un clic.
    from argus.ui.materials_panel import ADD_ROW, ROLE_NUMBER

    rows = [r for r in range(w.materials.table.rowCount())
            if w.materials.table.item(r, 0).data(ROLE_NUMBER) == ADD_ROW]
    assert len(rows) == 1
    opened = fake_exec(w, monkeypatch, lambda dlg: False)
    w.materials._on_click(rows[0], 0)
    assert len(opened) == 1
    # Quitar el último (no lo usa nadie).
    w.remove_material(n_before + 1)
    assert len(w.input.doc.find("MFNAME")) == n_before


def test_material_without_input_creates_one(qtbot, window, case_dir, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w = window
    w.open_file(case_dir / "cubo-esfera.geo", with_input=False)
    assert w.input is None
    w._answers["question"] = QMessageBox.StandardButton.Yes
    fake_exec(w, monkeypatch, lambda dlg: True)
    w.add_material_dialog()
    assert w.input is not None and len(w.input.doc.find("MFNAME")) == w.session.geo.nmat + 1


def test_icon_styles(qtbot, window):
    from argus.ui.icons import ICON_STYLES, argus_pixmap, icon_style

    w = window
    for style in ICON_STYLES:
        pm = argus_pixmap(style, 32)
        assert not pm.isNull() and pm.toImage().pixelColor(16, 16).alpha() > 0
    w.set_app_icon("atom")
    assert icon_style() == "atom" and not w.windowIcon().isNull()
