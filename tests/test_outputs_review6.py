"""Revisión del diálogo «Elegir qué resultados pide el .in» (0.13.1): su «Deshacer» es el de la ventana (el paso de la
geometría y el del .in que la sigue, juntos, y se puede rehacer) e Intro en un campo no pulsa «Deshacer»."""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("PySide6")

from argus.penhan import sections as sc  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

RES = CORPUS / "resultados"


@pytest.fixture
def caja(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    for name in ("question", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    for f in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy2(RES / "caja" / f, tmp_path / f)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    w.open_file(str(tmp_path / "caja.in"))
    qtbot.waitUntil(lambda: w.session is not None and w.session.ok, timeout=30000)
    yield w
    w.close()


def _refs(doc):
    return ([b.ref for d in sc.impact_detectors(doc) for b in d.bodies],
            [b.ref for d in sc.energy_detectors(doc) for b in d.bodies])


def test_the_dialog_undo_is_the_window_undo(qtbot, caja, monkeypatch):
    """Cambiar una etiqueta de la geometría cambia también el .in (en el mismo paso): «Deshacer» del diálogo lo
    deshace todo junto, como Ctrl+Z (antes deshacía solo el .in: la geometría con DET2 y el .in con DETE)."""
    from PySide6.QtWidgets import QInputDialog

    w = caja
    w.choose_outputs()
    dlg = w._outputs_dialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("DET2", True)))
    kb = w.session.geo.find_body("DETE").index
    w.rename_dialog(kb)
    assert _refs(w.input.doc) == (["'PLAN'", "'DET2'"], ["'DET2'"])
    qtbot.waitUntil(lambda: dlg.btn_undo.isEnabled(), timeout=5000)
    dlg.btn_undo.click()
    assert _refs(w.input.doc) == (["'PLAN'", "'DETE'"], ["'DETE'"])
    assert w.session.geo.find_body("DETE") is not None
    assert w.act_redo.isEnabled()
    w.redo()
    assert _refs(w.input.doc) == (["'PLAN'", "'DET2'"], ["'DET2'"])
    dlg.close()


def test_enter_in_a_field_does_not_press_undo(qtbot, caja):
    """Los botones del diálogo no son «por defecto»: Intro en un campo de la matriz aplica el valor (antes pulsaba
    «Deshacer» y lo deshacía al momento)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton

    w = caja
    w.choose_outputs()
    dlg = w._outputs_dialog
    qtbot.waitExposed(dlg)
    assert not any(b.isDefault() or b.autoDefault() for b in dlg.findChildren(QPushButton))
    field = dlg.cards["dosis"].editor.hi["x"]
    assert field.isVisibleTo(dlg)
    old = sc.dose_grid(w.input.doc).x
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, str(old[1] + 3.0))
    QTest.keyClick(field, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: sc.dose_grid(w.input.doc).x[1] == pytest.approx(old[1] + 3.0), timeout=5000)
    qtbot.wait(100)
    assert sc.dose_grid(w.input.doc).x[1] == pytest.approx(old[1] + 3.0)
    dlg.close()


def test_the_dialog_says_why_everything_is_off_with_read_errors(qtbot, caja, tmp_path):
    w = caja
    bad = tmp_path / "roto.in"
    text = (tmp_path / "caja.in").read_text(encoding="latin-1")
    bad.write_text(text.replace("SKPAR  2", "SKPAR  dos"), encoding="latin-1")
    w.open_file(str(bad))
    qtbot.waitUntil(lambda: w.input is not None and w.input.path is not None and w.input.path.name == "roto.in",
                    timeout=30000)
    assert not w.input.doc.model.ok
    w.choose_outputs()
    dlg = w._outputs_dialog
    assert dlg.error_note.isVisibleTo(dlg) and "errores de lectura" in dlg.error_note.text()
    assert not dlg.cards["dosis"].check.isEnabled()
    dlg.close()
