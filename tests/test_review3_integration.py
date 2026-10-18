"""Revisión 3, lo que une el trabajo de las partes: el arrastre y el panel «Colocación» de un módulo que comparte
superficies (le dan copias propias), los nombres que no caben en las columnas de penhmain en los paneles, y el
registro de errores inesperados."""

import os
import shutil
import sys

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui
SB = QMessageBox.StandardButton


@pytest.fixture
def stack_dir(tmp_path):
    for f in (CORPUS / "penhan/stack").iterdir():
        shutil.copy2(f, tmp_path / f.name)
        os.chmod(tmp_path / f.name, 0o644)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    msgs = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: msgs.append(("question", a[2])) or SB.No)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2])) or SB.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._msgs = msgs
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def test_dragging_a_module_that_shares_a_plane_keeps_its_neighbour(window, stack_dir, qtbot):
    """3discs: el módulo «2» comparte el plano z = 0 con «3». Arrastrarlo 10 µm hacia arriba ya no deja un hueco
    de vacío: se le dan copias propias de las superficies en el mismo paso de deshacer."""
    w = window
    w.open_file(stack_dir / "stack.in")
    kb = w.session.geo.find_body("2").index
    gap = np.array([[0.0, 0.0, 0.0005]])
    before_text = w.geo_ctrl.text
    w.select_body(kb)
    w._on_module_dragged(kb, ("move", np.array([0.0, 0.0, 0.001])), False)
    qtbot.wait(50)
    w._on_module_dragged(kb, ("move", np.array([0.0, 0.0, 0.001])), True)
    g2 = w.session.geo
    ib, _mat = w.session.locator.locate(gap)
    assert w.geo_ctrl.dirty and ib[0] == g2.find_body("3").index  # el hueco es de «3», como antes
    assert not [m for m in w._msgs if m[0] != "question"]
    w.undo()
    assert w.geo_ctrl.text == before_text  # un solo paso (superficies propias y movimiento)


def test_the_placement_panel_moves_a_module_that_shares_a_plane(window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    kb = w.session.geo.find_body("2").index
    w.select_body(kb)
    w.geo_panel._on_transform("Z-SHIFT", 0.001)
    ib, _mat = w.session.locator.locate(np.array([[0.0, 0.0, 0.0005]]))
    assert ib[0] == w.session.geo.find_body("3").index


def test_a_nuclide_name_longer_than_45_is_refused_in_the_panel(window, tmp_path):
    """SRNUCL se lee con 45 caracteres: el panel lo dice y el .in no cambia (antes, excepción sin aviso)."""
    from argus.penhan import source_edit as se

    w = window
    case = CORPUS / "penhan/nuclide"
    for f in case.iterdir():
        shutil.copy2(f, tmp_path / f.name)
        os.chmod(tmp_path / f.name, 0o644)
    ins = sorted(tmp_path.glob("*.in"))
    w.open_file(ins[0])
    before = w.input.doc.snapshot()
    panel = w.source_panel
    panel._edit("Cambiar el nucleido", lambda d: se.set_nuclide(d, "x" * 50 + ".nuc"))
    assert w.input.doc.snapshot() == before
    assert "45" in panel.status.text() or "caracteres" in panel.status.text()


def test_unexpected_errors_are_logged_and_shown_once(tmp_path, monkeypatch, qtbot):
    from argus.ui import errors

    monkeypatch.setattr(errors, "log_path", lambda: tmp_path / "argus_errores.log")
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]) or SB.Ok)
    errors.reset()
    try:
        raise ValueError("prueba de error")
    except ValueError:
        info = sys.exc_info()
    errors.report(*info)
    errors.report(*info)  # el mismo, enseguida: se anota pero no se vuelve a mostrar
    qtbot.waitUntil(lambda: bool(shown), timeout=2000)  # el aviso sale fuera del evento que ha fallado
    qtbot.wait(50)
    assert len(shown) == 1 and "prueba de error" in shown[0]
    log = (tmp_path / "argus_errores.log").read_text(encoding="utf-8")
    assert log.count("ValueError: prueba de error") == 2 and log.count("Traceback") == 1
    errors.reset()


def test_an_error_repeated_while_painting_is_shown_once_and_the_log_is_capped(tmp_path, monkeypatch, qtbot):
    """Revisión final: un error en cada repintado, con un mensaje distinto cada vez, daba un aviso por repintado
    (61 en 3 s) desde dentro del evento de pintar, y el registro crecía sin límite."""
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QWidget

    from argus.ui import errors

    monkeypatch.setattr(errors, "log_path", lambda: tmp_path / "argus_errores.log")
    monkeypatch.setattr(errors, "LOG_MAX_BYTES", 1500)
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]) or SB.Ok)
    errors.reset()
    count = [0]

    class Broken(QWidget):
        def paintEvent(self, e):  # noqa: N802
            count[0] += 1
            with QPainter(self) as p:
                p.fillRect(self.rect(), self.palette().window())
            try:
                raise ValueError(f"pintando {count[0]}")  # el mensaje cambia cada vez
            except ValueError:
                errors.report(*sys.exc_info())

    from PySide6.QtWidgets import QApplication

    w = Broken()
    qtbot.addWidget(w)
    w.resize(100, 100)
    w.show()
    qtbot.waitExposed(w)
    for _ in range(200):
        if count[0] >= 40:
            break
        w.update()
        QApplication.processEvents()
        qtbot.wait(5)
    qtbot.wait(100)
    assert count[0] >= 40 and len(shown) == 1
    log = tmp_path / "argus_errores.log"
    assert log.stat().st_size <= 1500 and (tmp_path / "argus_errores.log.1").exists()  # (y solo 20 repeticiones)
    assert errors.error_key(ValueError, None) == "ValueError en ?"
    errors.reset()


def test_cells_with_inf_or_nan_are_not_taken_as_numbers(qtbot):
    """Editor de espectros y tablas del diálogo del TAC: «1e400», «inf» o «nan» no son números."""
    from argus.ui.ct_import_dialog import _finite
    from argus.ui.spectrum_dialog import SpectrumDialog

    for bad in ("1e400", "inf", "nan", "-inf"):
        with pytest.raises(ValueError):
            _finite(bad)
    assert _finite("1.5") == 1.5
    dlg = SpectrumDialog(None, [(1e6, 1.0), (2e6, -1.0)])
    qtbot.addWidget(dlg)
    item = dlg.table.item(0, 0)
    item.setText("1e400")
    from PySide6.QtCore import Qt

    assert item.data(Qt.ItemDataRole.UserRole) is None
