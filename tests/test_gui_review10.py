"""Décima revisión (0.17.2): búsqueda aleatoria de fallos, en la ventana.

Lo que salió con la prueba de mono (otra semilla y acciones nuevas), los ficheros dañados abiertos en la ventana y la
navegación 3D; cada prueba falla con el código de antes."""

from __future__ import annotations

import os
import shutil
import sys

import pytest

pytest.importorskip("PySide6")

from argus.penhan import sections as sc  # noqa: E402
from argus.penhan.inputfile import parse_in_text  # noqa: E402
from conftest import CORPUS  # noqa: E402
from test_gui_modules import case_dir, fake_exec, window  # noqa: E402, F401

pytestmark = pytest.mark.gui

CAJA = CORPUS / "resultados" / "caja"


@pytest.fixture
def hooked(monkeypatch):
    """Lo que llega al aviso de «error inesperado» (sys.excepthook)."""
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: seen.append(f"{t.__name__}: {v}"))
    return seen


def _caja():
    text = (CAJA / "caja.in").read_text("latin-1")
    return parse_in_text(text, str(CAJA / "caja.in"))


def _job(qtbot, doc):
    from argus.ui.sim_outputs import JobSection

    job = JobSection()
    qtbot.addWidget(job)
    job.apply.connect(lambda text, fn: (fn(doc), job.set_context(doc, None, CAJA)))
    job.set_context(doc, None, CAJA)
    return job


def _type(edit, text: str) -> None:
    edit.setText(text)
    edit.setModified(True)
    edit._commit()


# -- «Trabajo» -------------------------------------------------------------------------------------------------------

def test_seeds_that_penhmain_could_not_read_are_not_written(qtbot):
    """Una semilla de más de 4 bytes (3141592653) se escribía en el .in: penhmain no lo leería y el panel Simulación
    entero quedaba desactivado. Ahora el campo no la acepta (como el tiempo de más) y dice hasta dónde."""
    SEED1_MAX, SEED2_MAX = 2147483562, 2147483398  # (los de RAND, en rita.f)

    doc = _caja()
    job = _job(qtbot, doc)
    for bad in ("3141592653", "2147483648", "1e20", "0", "-5"):
        _type(job.s1, bad)
        assert sc.seeds(doc)[-1] == (1, 1), bad
    from argus.ui import sim_outputs

    assert (sim_outputs.SEED1_MAX, sim_outputs.SEED2_MAX) == (SEED1_MAX, SEED2_MAX)
    _type(job.s1, str(SEED1_MAX))
    _type(job.s2, str(SEED2_MAX))
    assert sc.seeds(doc)[-1] == (SEED1_MAX, SEED2_MAX)
    _type(job.s2, str(SEED2_MAX + 1))
    assert sc.seeds(doc)[-1] == (SEED1_MAX, SEED2_MAX)
    # El par precalculado: de 1 a 1001.
    job.seed_kind.setCurrentIndex(job.seed_kind.findData("pair"))
    job._on_seed_kind(0)
    _type(job.s1, "1002")
    assert sc.seeds(doc)[-1][0] >= -1001
    _type(job.s1, "1001")
    assert sc.seeds(doc)[-1] == (-1001, 1) and "1001" in job.s1.toolTip()


@pytest.mark.parametrize("field, kw", [("nsimsh", "NSIMSH"), ("time", "TIME"), ("dumpp", "DUMPP")])
def test_job_fields_can_be_emptied_as_their_help_says(qtbot, field, kw):
    """«Vacío: 2e9», «Vacío: sin límite», «0 o vacío: sin DUMPP»: al vaciarlos, decía «no es un número» y volvía el
    valor de antes. Ahora se quita la línea."""
    doc = _caja()
    job = _job(qtbot, doc)
    assert sc.value(doc, kw) is not None
    edit = getattr(job, field)
    _type(edit.edit if field == "time" else edit, "  ")
    assert sc.value(doc, kw) is None
    assert (edit.edit if field == "time" else edit).styleSheet() == ""  # (sin marcarlo como error)


# -- los diálogos del editor -----------------------------------------------------------------------------------------

def test_editor_dialogs_stop_their_check_when_closed():
    """Cada diálogo comprueba lo escrito con un temporizador; al cancelarlo, la comprobación seguía pendiente y, si
    saltaba después, miraba los cuerpos de entonces en la geometría de ahora (IndexError, con «Envolver»)."""
    from PySide6.QtWidgets import QDialog

    from argus.ui.add_body_dialog import AddBodyDialog
    from argus.ui.detector_dialogs import CellDialog, DetectorDialog
    from argus.ui.duplicate_dialog import DuplicateDialog
    from argus.ui.glue_dialog import GlueDialog
    from argus.ui.surface_dialog import SurfaceDialog
    from argus.ui.wrap_dialog import WrapDialog

    for cls in (AddBodyDialog, DetectorDialog, CellDialog, DuplicateDialog, GlueDialog, SurfaceDialog, WrapDialog):
        assert cls.done is not QDialog.done, cls.__name__


def test_wrapping_cancelled_right_away_and_undo(qtbot, window, case_dir, monkeypatch, hooked):  # noqa: F811
    """La prueba de mono (semilla 2026): duplicar, «Envolver…» la última copia, Esc y deshacer enseguida."""
    from argus.ui.wrap_dialog import WrapDialog

    w = window
    w.open_file(case_dir / "maniqui.in")
    geo = w.session.geo
    kb = next(b.index for b in geo.bodies if b.origin != "enclosure" and not b.is_module)

    def answer(dlg):
        if isinstance(dlg, WrapDialog):
            dlg.reject()
            assert not dlg._timer.isActive()
            return False
        if hasattr(dlg, "count"):
            dlg.count.setValue(3)
        dlg.accept()
        return dlg.result() == 1

    fake_exec(w, monkeypatch, answer)
    w.duplicate_dialog(kb)
    last = max(b.index for b in w.session.geo.bodies if b.origin != "enclosure")
    assert last > geo.nbodys - 1
    w.wrap_dialog(last)
    w.undo()
    qtbot.wait(400)
    assert not hooked


# -- «Ejecutar» ------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="el penhmain de mentira es un guion de sh")
def test_run_saves_the_geometry_changed_with_the_dialog_open(qtbot, tmp_path, monkeypatch):
    """«Elegir resultados…» → «Deshacer» (el de la ventana) puede deshacer un cambio de la geometría con «Ejecutar»
    abierto; «Ejecutar» solo guardaba el .in, y penhmain simulaba la geometría del disco, que ya no era la de la
    ventana."""
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import edit as ge
    from argus.pengeom.writer import write_geo_text
    from argus.penhan import run as rn
    from argus.ui.main_window import MainWindow
    from argus.ui.run_dialog import RunDialog
    from argus.ui.settings import PENHMAIN_KEY, app_settings
    from test_run import _case, _fake_penhmain
    from test_run_ui import _headers

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    started = []
    monkeypatch.setattr(rn, "start", lambda plan, **_kw: started.append(plan) or rn.RunHandle(
        None, plan.folder, plan.in_path.name, rn.Terminal(["true"], plan.folder, "un terminal")))
    s = app_settings()
    s.setValue(PENHMAIN_KEY, str(_fake_penhmain(tmp_path / "programas")))
    s.setValue("cascada/al_ejecutar", "false")
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1300, 850)
    w.show()
    qtbot.waitExposed(w)
    try:
        w.open_file(in_path, ask=False)
        qtbot.waitUntil(lambda: w.session is not None and w.session.ok, timeout=30000)
        kb = next(b.index for b in w.session.geo.bodies if b.origin == "user" and not b.is_module)
        w.edit_geometry("Descripción", lambda doc, g: ge.set_description(ge.editable_body(g, kb), "otra"))
        assert w.save_all() and not w.geo_ctrl.dirty

        def run(self):
            w.undo()  # como «Deshacer» en «Elegir resultados…»
            assert w.geo_ctrl.dirty and not self.ctrl.dirty
            self.go.click()
            return self.result()

        monkeypatch.setattr(RunDialog, "exec", run)
        w.act_run.trigger()
        assert len(started) == 1
        assert not w.geo_ctrl.dirty
        assert w.geo_ctrl.path.read_text("latin-1") == write_geo_text(w.geo_ctrl.doc)
    finally:
        w.close()
        s.remove(PENHMAIN_KEY)
        s.remove("cascada")


# -- ficheros dañados en la ventana ----------------------------------------------------------------------------------

def test_typing_a_non_ascii_capital_in_a_composition(qtbot, hooked):
    from argus.ui.material_picker import CompositionEditor

    ed = CompositionEditor()
    qtbot.addWidget(ed)
    for text in ("H2Ó", "Ｈ２Ｏ", "(" * 1200 + "H2O" + ")" * 1200, "H2O"):
        ed.formula.setText(text)
        qtbot.wait(10)
    assert not hooked


def test_a_huge_dose_grid_is_not_drawn(qtbot, window, tmp_path, hooked):  # noqa: F811
    """«GRIDR 1e200 25»: el corte lanzaba OverflowError al pintar, en cada repintado."""
    for f in ("esfera.in", "esfera.geo"):
        shutil.copy(CORPUS / "resultados" / "esfera" / f, tmp_path / f)
        os.chmod(tmp_path / f, 0o644)
    p = tmp_path / "esfera.in"
    text = p.read_text("latin-1")
    assert "GRIDR  5 25" in text
    p.write_text(text.replace("GRIDR  5 25", "GRIDR  1e200 25", 1), "latin-1")
    w = window
    w.open_file(p)
    w.view.grab()
    qtbot.wait(50)
    assert not hooked


def test_a_dump_name_with_a_nul_char_opens(qtbot, window, tmp_path, hooked):  # noqa: F811
    src = CORPUS / "penhan" / "disc"
    for f in src.iterdir():
        shutil.copy(f, tmp_path / f.name)
        os.chmod(tmp_path / f.name, 0o644)
    p = tmp_path / "disc.in"
    p.write_text(p.read_text("latin-1").replace("DUMPTO dump.dmp ", "DUMPTO du\x00p.dmp ", 1), "latin-1")
    w = window
    w.open_file(p)
    assert w.input is not None and "disc.in" in w.windowTitle()
    assert not hooked
