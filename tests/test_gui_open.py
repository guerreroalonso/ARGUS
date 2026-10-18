"""Abrir una geometría en la ventana: con su .in, eligiendo entre varios .in o sola."""

import os
import shutil
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")

from argus.pengeom import edit as ge  # noqa: E402
from argus.penhan.inputfile import parse_in_text  # noqa: E402
from argus.penhan.quickscan import inputs_for_geometry  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

DISC = CORPUS / "penhan/disc"


def _write_in(folder, name: str, thickness: str, energy: str, mtime: float):
    lines = (DISC / "disc.in").read_text("latin-1").splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith("PARINP 1 "):
            lines[i] = f"PARINP 1  {thickness:<10}                   thickness  [Replacement parameter]\n"
        elif ln.startswith("SENERG"):
            lines[i] = f"SENERG {energy:<16}[Initial energy (monoenergetic sources only)]\n"
    path = folder / name
    path.write_text("".join(lines), "latin-1")
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def disc_dir(tmp_path):
    """disc.geo con dos .in que la usan: disc.in (el más antiguo) y fino.in (el más reciente)."""
    shutil.copy2(DISC / "disc.geo", tmp_path / "disc.geo")
    now = time.time()
    _write_in(tmp_path, "disc.in", "40.0", "200.e6", now - 3600)
    _write_in(tmp_path, "fino.in", "2.0", "150e6", now - 60)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def answer_chooser(w, monkeypatch, action: str, pick: str | None = None):
    """El diálogo de elegir .in se contesta sin mostrarse: 'open' (con ``pick`` o lo propuesto), 'geometry' o
    'cancel'. Devuelve lo que el diálogo proponía cada vez."""
    seen = []

    def fake(dlg):
        proposed = dlg.selected_input()
        seen.append({"proposed": proposed.name if proposed else None,
                     "geometry_default": dlg.btn_geometry.isDefault(),
                     "rows": [dlg.table.topLevelItem(i).text(0) for i in range(dlg.table.topLevelItemCount())],
                     "sources": [dlg.table.topLevelItem(i).text(2) for i in range(dlg.table.topLevelItemCount())],
                     "parinp": [dlg.table.topLevelItem(i).text(3) for i in range(dlg.table.topLevelItemCount())]})
        if action == "cancel":
            dlg.btn_cancel.click()
            return False
        if action == "geometry":
            dlg.btn_geometry.click()
            return True
        if pick is not None:
            for i in range(dlg.table.topLevelItemCount()):
                if dlg.table.topLevelItem(i).text(0) == pick:
                    dlg.table.setCurrentItem(dlg.table.topLevelItem(i))
        dlg.btn_open.click()
        return True

    monkeypatch.setattr(w, "_exec_dialog", fake)
    return seen


def test_several_inputs_ask_which_one(qtbot, window, disc_dir, monkeypatch):
    w = window
    seen = answer_chooser(w, monkeypatch, "open", pick="disc.in")
    w.open_file(disc_dir / "disc.geo")
    assert len(seen) == 1
    first = seen[0]
    assert first["rows"] == ["disc.in", "fino.in"] and first["proposed"] == "fino.in"  # el más reciente
    assert first["sources"] == ["protones, 200 MeV", "protones, 150 MeV"]
    assert first["parinp"] == ["1 = 40, 2 = 40", "1 = 2, 2 = 40"]
    assert w.input is not None and w.input.name == "disc.in"
    assert w.session.ok and w.session.parinp == [40.0, 40.0] and w.session.path == disc_dir / "disc.geo"
    # La vez siguiente se propone lo elegido.
    seen = answer_chooser(w, monkeypatch, "open")
    w.open_file(disc_dir / "disc.geo")
    assert seen[0]["proposed"] == "disc.in" and w.input.name == "disc.in"
    # Recargar reabre el .in (sin preguntar).
    seen = answer_chooser(w, monkeypatch, "cancel")
    w.reload()
    assert not seen and w.input is not None and w.input.name == "disc.in"


def test_chosen_input_gives_its_parinp(qtbot, window, disc_dir, monkeypatch):
    w = window
    answer_chooser(w, monkeypatch, "open", pick="fino.in")
    w.open_file(disc_dir / "disc.geo")
    assert w.input.name == "fino.in" and w.session.parinp == [2.0, 40.0]
    lo, hi = w.session.bounds
    assert hi[2] - lo[2] < 10.0  # el disco de 2 cm, no el de 40


def test_geometry_only_and_cancel(qtbot, window, disc_dir, monkeypatch):
    w = window
    seen = answer_chooser(w, monkeypatch, "geometry")
    w.open_file(disc_dir / "disc.geo")
    assert w.input is None and w.session.ok
    assert w.session.parinp_provisional and w.session.parinp == [0.005, 1.0]
    assert any("Sin .in" in d.message for d in w.session.diagnostics)
    assert "disc.in" not in w.windowTitle()
    # Recargar no vuelve a preguntar ni abre un .in.
    seen = answer_chooser(w, monkeypatch, "open")
    w.reload()
    assert not seen and w.input is None
    # La próxima vez se propone abrirla sola.
    seen = answer_chooser(w, monkeypatch, "cancel")
    w.open_file(disc_dir / "disc.geo")
    assert seen[0]["geometry_default"]
    assert w.input is None and w.session.parinp_provisional  # cancelar deja todo como estaba
    # Con un .in abierto, cancelar tampoco cambia nada.
    w.open_file(disc_dir / "fino.in")
    seen = answer_chooser(w, monkeypatch, "cancel")
    w.open_file(disc_dir / "disc.geo")
    assert w.input is not None and w.input.name == "fino.in"


def test_open_geometry_only_action_and_single_input(qtbot, window, disc_dir, monkeypatch):
    w = window
    (disc_dir / "fino.in").unlink()
    seen = answer_chooser(w, monkeypatch, "cancel")
    w.open_file(disc_dir / "disc.geo")  # un solo .in: se abre con él sin preguntar
    assert not seen and w.input is not None and w.input.name == "disc.in"
    assert "disc.in" in w.statusBar().currentMessage()
    w.open_file(disc_dir / "disc.geo", with_input=False)
    assert not seen and w.input is None and w.session.parinp_provisional


def test_new_input_for_geometry_only_writes_parinp_and_keeps_edits(qtbot, window, disc_dir, monkeypatch):
    w = window
    w.open_file(disc_dir / "disc.geo", with_input=False)
    # Un cambio en la geometría antes de crear el .in: no se pierde ni se da por guardado.
    kb = next(b.index for b in w.session.geo.bodies if b.material > 0)  # el disco es un módulo
    label = w.session.geo.balias(kb)
    w.edit_geometry("Cambiar el nombre",
                    lambda doc, g: ge.set_description(ge.find_element(doc, "body", label), "Agua"))
    assert w.geo_ctrl.dirty
    ctrl_before = w.geo_ctrl
    w.new_input()
    assert w.input is not None and w.input.path is None
    assert w.geo_ctrl is ctrl_before and w.geo_ctrl.dirty
    doc = parse_in_text(w.input.doc.text(), None)
    assert doc.ok and doc.model.parinp == {1: 0.005, 2: 1.0} and doc.model.geomfn == "disc.geo"
    assert not w.session.parinp_provisional
    assert not any("Sin .in" in d.message for d in w.session.diagnostics)
    # El deshacer de la geometría sigue funcionando después de crear el .in.
    w.undo()
    assert not w.geo_ctrl.dirty


def test_inputs_listed_in_folder(disc_dir):
    assert [s.path.name for s in inputs_for_geometry(disc_dir / "disc.geo")] == ["disc.in", "fino.in"]
    assert np.isfinite([s.mtime for s in inputs_for_geometry(disc_dir / "disc.geo")]).all()


def test_edit_panel_shows_effective_parinp_values(qtbot, window, disc_dir, monkeypatch):
    w = window
    w.open_file(disc_dir / "fino.in")  # PARINP 1 = 2 (espesor), 2 = 40 (radio)
    kb = next(b.index for b in w.session.geo.bodies if b.material > 0)
    w.select_body(kb)
    panel = w.geo_panel
    rows = [panel.table.item(r, 0).text() for r in range(panel.table.rowCount())]
    panel.table.selectRow(next(r for r, t in enumerate(rows) if t == "2"))  # el plano z = espesor
    ed = panel.param_edits["Z-SHIFT"]
    assert ed.value() == 2.0 and not ed.isEnabled() and "PARINP(1)" in ed.toolTip()
    panel.table.selectRow(next(r for r, t in enumerate(rows) if t == "3"))  # el cilindro
    assert panel.param_edits["X-SCALE"].value() == 40.0
    # Sin .in: los números escritos en el .geo.
    w.open_file(disc_dir / "disc.geo", with_input=False)
    w.select_body(kb)
    panel.table.selectRow(next(r for r, t in enumerate(rows) if t == "2"))
    assert panel.param_edits["Z-SHIFT"].value() == 0.005
