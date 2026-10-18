"""Novena revisión (0.16.3), en la ventana: el editor de geometría con un clon de un clon, etiquetas que solo se
distinguen por sus columnas y los avisos de PARINP al duplicar."""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402
from test_review9_pengeom import CLONE_OF_CLONE, COLUMNS, PARINP_GEO  # noqa: E402

pytestmark = pytest.mark.gui
SB = QMessageBox.StandardButton


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


def _stack_in(geo_name: str, edbody3: str) -> str:
    text = (CORPUS / "penhan/stack/stack.in").read_text(encoding="latin-1")
    lines = []
    for ln in text.splitlines():
        if ln.startswith(("PARINP", "DSMAX")):
            continue
        if ln.startswith("GEOMFN"):
            ln = f"GEOMFN {geo_name:<20}" + ln[27:]
        if ln.startswith("EDBODY 3"):
            ln = f"EDBODY {edbody3}".ljust(56) + "[Active body]"
        lines.append(ln)
        if ln.startswith("MSIMPN"):  # tres materiales, como la geometría
            block = lines[-4:]
            lines += block + block
    return "\n".join(lines) + "\n"


def test_materializing_a_clone_of_a_clone_keeps_the_in_line(window, tmp_path):
    w = window
    (tmp_path / "cc.geo").write_text(CLONE_OF_CLONE, encoding="latin-1")
    (tmp_path / "cc.in").write_text(_stack_in("cc.geo", "5"), encoding="latin-1")
    w.open_file(tmp_path / "cc.in")
    assert w.session.ok
    before = [ln.raw.strip() for ln in w.input.doc.lines if ln.kw.startswith("EDBODY")]
    assert "EDBODY 5" in [b[:8] for b in before]
    w.materialize_clone(w.session.geo.find_body("   3").index)
    after = [(ln.raw or "").strip()[:8] or ln.kw for ln in w.input.doc.lines if ln.kw.startswith("EDBODY")]
    assert len(after) == len(before) and "EDBODY 5" in after
    assert not any("quitad" in n for n in w.last_follow_notes)


def test_the_material_of_a_left_aligned_label(window, tmp_path):
    w = window
    (tmp_path / "col.geo").write_text(COLUMNS, encoding="latin-1")
    w.open_file(tmp_path / "col.geo")
    assert w.session.ok
    kb = next(b.index for b in w.session.geo.bodies if b.alias == "1   0")
    w.select_body(kb)
    w.geo_panel.material.setValue(7)
    w.geo_panel._on_material()
    geo = w.session.geo
    assert geo.body(next(b.index for b in geo.bodies if b.alias == "1   0")).material == 7
    assert geo.body(next(b.index for b in geo.bodies if b.alias == "   10")).material == 2


def test_duplicating_reports_the_parinp_values_that_become_numbers(window, tmp_path, monkeypatch):
    w = window
    shutil.copy(CORPUS / "penhan/stack/stack.in", tmp_path / "disco.in")
    text = (tmp_path / "disco.in").read_text(encoding="latin-1").replace("3discs.geo", "disco.geo ")
    (tmp_path / "disco.in").write_text(text, encoding="latin-1")
    (tmp_path / "disco.geo").write_text(PARINP_GEO, encoding="latin-1")
    w.open_file(tmp_path / "disco.in")
    assert w.session.ok

    def accept(dlg):
        dlg.rb_shift.setChecked(True)
        for ed, v in zip((dlg.dx, dlg.dy, dlg.dz), (0.0, 0.0, 5.0)):
            ed.setText(str(v))
        return True

    monkeypatch.setattr(w, "_exec_dialog", accept)
    w.duplicate_dialog(1)
    assert "usaba PARINP(1); en la copia es un número" in w.statusBar().currentMessage()
