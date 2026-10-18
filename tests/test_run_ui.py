"""Ejecutar penhmain desde la interfaz (0.13.0): el diálogo «Ejecutar», el botón de la ventana principal, la ventana
de resultados que se abre y la cascada que se simula a la vez."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from argus.penhan import cascade as cs  # noqa: E402
from argus.penhan import run as rn  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_cascade import FAKE  # noqa: E402
from test_run import CAJA, _case, _fake_penhmain  # noqa: E402

pytestmark = [pytest.mark.gui,
              pytest.mark.skipif(os.name == "nt", reason="los penhmain de mentira son guiones de sh")]


def _headers(in_path: Path) -> None:
    """Cabeceras de .mat de PENELOPE en los .mat de mentira (si no, ARGUS ve errores en el .in)."""
    from argus.penhan.inputfile import parse_in_file

    for md in parse_in_file(in_path).model.materials:
        name = md.fname.strip()
        (in_path.parent / name).write_text(
            " PENELOPE (v. 2018)  Material data file ...............\n"
            f" Material: {name.split('.')[0].upper()}  (278)\n"
            " Mass density = 1.00000000E+00 g/cm**3\n")


@pytest.fixture
def settings(tmp_path):
    from argus.ui.settings import PENHMAIN_KEY, app_settings

    s = app_settings()
    s.setValue(PENHMAIN_KEY, str(_fake_penhmain(tmp_path / "programas")))
    s.setValue("cascada/al_ejecutar", "false")
    yield s
    s.remove(PENHMAIN_KEY)
    s.remove("cascada")


@pytest.fixture
def started(monkeypatch):
    """``run.start`` sin abrir ningún terminal: apunta lo que se habría ejecutado."""
    out = []

    def fake_start(plan, **_kw):
        assert plan.exe.target is not None and plan.exe.target.is_file()
        out.append(plan)
        return rn.RunHandle(None, plan.folder, plan.in_path.name, rn.Terminal(["true"], plan.folder, "un terminal"))

    monkeypatch.setattr(rn, "start", fake_start)
    return out


def _dialog(qtbot, in_path, settings, **kw):
    from argus.session import open_case
    from argus.ui.input_controller import InputController
    from argus.ui.run_dialog import RunDialog

    cf = open_case(in_path)
    ctrl = InputController(cf.input_doc, in_path)
    dlg = RunDialog(None, ctrl, cf.session, settings, **kw)
    qtbot.addWidget(dlg)
    return dlg, ctrl


def test_the_dialog_copies_penhmain_and_starts_it(qtbot, tmp_path, settings, started):
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    saved = []
    dlg, ctrl = _dialog(qtbot, in_path, settings, save=lambda: saved.append(ctrl.save()) or True)
    assert "Se copia" in dlg.exe_state.text() and not dlg.conflict.isVisibleTo(dlg)
    assert not dlg.prev_box.isVisibleTo(dlg) and not dlg.gen.isVisibleTo(dlg)
    assert "Dosis en 3D" in dlg.results.text() and "11 × 11 × 25" in dlg.results.text()
    assert dlg.go.isEnabled(), dlg.status.text()
    # El trabajo se cambia aquí (con deshacer en el .in) y se guarda al ejecutar.
    dlg.job.nsimsh.valueEdited.emit(1234.0)
    assert ctrl.dirty and "NSIMSH 1234" in ctrl.doc.text().replace("  ", " ").replace("  ", " ")
    # Las lluvias que se siguen (la cascada), en «Trabajo», debajo del tiempo: como mucho 1000.
    labels = [dlg.job.form.itemAt(i, dlg.job.form.ItemRole.LabelRole).widget().text()
              for i in range(3)]
    assert labels == ["Partículas:", "Tiempo máx.:", "Lluvias:"]
    assert dlg.showers.maximum() == 1000
    dlg.showers.setValue(200)
    dlg.go.click()
    assert dlg.handle is not None and dlg.result() == dlg.DialogCode.Accepted
    assert saved and not ctrl.dirty
    assert (in_path.parent / "penhmain.x").is_file() and len(started) == 1
    assert dlg.cascade_n == 200 and int(settings.value("cascada/lluvias")) == 200
    assert str(settings.value("cascada/al_ejecutar")) == "true"


def test_previous_results_are_kept_and_another_penhmain_is_not_replaced_unless_asked(qtbot, tmp_path, settings,
                                                                                     started):
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    folder = in_path.parent
    for n in ("penhmain-res.dat", "penhmain.dat", "depth-dose.dat"):
        shutil.copy(CAJA / n, folder / n)
    other = folder / "penhmain.x"
    other.write_text("#!/bin/sh\necho otro\n")
    other.chmod(0o755)
    dlg, _ctrl = _dialog(qtbot, in_path, settings)
    assert dlg.prev_box.isVisibleTo(dlg) and "3 ficheros" in dlg.prev_state.text()
    assert dlg.conflict.isVisibleTo(dlg) and dlg.use_folder.isChecked()
    dlg.replace.setChecked(True)
    dlg.go.click()
    assert dlg.handle is not None
    assert dlg.stash is not None and dlg.stash.name.startswith("anterior-")
    assert sorted(p.name for p in dlg.stash.iterdir()) == sorted(
        ["penhmain-res.dat", "penhmain.dat", "depth-dose.dat", rn.STASH_NOTE])
    assert not (folder / "penhmain-res.dat").exists()
    assert (folder / "penhmain.x.bak").read_text() == "#!/bin/sh\necho otro\n"
    assert "penhmain de mentira" in (folder / "penhmain.x").read_text()


def test_what_stops_a_run(qtbot, tmp_path, settings, started):
    in_path, doc = _case(tmp_path)
    _headers(in_path)
    mat = in_path.parent / doc.model.materials[1].fname.strip()
    mat.unlink()
    dlg, _ctrl = _dialog(qtbot, in_path, settings)
    assert dlg.gen.isVisibleTo(dlg) and "Falta 1 .mat" in dlg.files_state.text()
    assert not dlg.go.isEnabled() and "faltan .mat" in dlg.status.text()
    assert not dlg.errors.isVisibleTo(dlg)  # el .mat que falta se genera aquí: no es un error del .in
    _headers(in_path)
    dlg.refresh()
    assert dlg.go.isEnabled() and not dlg.gen.isVisibleTo(dlg)
    # Un .in con el que penhmain se pararía.
    in_path.write_text(in_path.read_text().replace("SKPAR  2", "SKPAR  9"))
    dlg2, _ctrl2 = _dialog(qtbot, in_path, settings)
    assert dlg2.errors.isVisibleTo(dlg2) and "3002" in dlg2.errors.text()
    assert not dlg2.go.isEnabled() and "errores" in dlg2.status.text()
    dlg2.force.setChecked(True)
    assert dlg2.go.isEnabled()
    # Sin penhmain.
    dlg2.exe.setText(str(tmp_path / "no-existe.x"))
    dlg2.refresh()
    assert not dlg2.go.isEnabled() and "No se encuentra" in dlg2.exe_state.text()


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from argus.ui.main_window import MainWindow
    from argus.ui.settings import PENHMAIN_KEY, app_settings

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    s = app_settings()
    exe = tmp_path / "programas" / "penhmain.x"  # el de la cascada de mentira (escribe trayectorias)
    exe.parent.mkdir(parents=True)
    exe.write_text(FAKE)
    exe.chmod(0o755)
    s.setValue(PENHMAIN_KEY, str(exe))
    s.setValue("cascada/al_ejecutar", "true")
    s.setValue("cascada/lluvias", 60)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1300, 850)
    w.show()
    qtbot.waitExposed(w)
    yield w
    for job in cs.active_jobs():
        job.cancel()
        job.wait(20)
    rw = getattr(w, "_results_window", None)
    if rw is not None:
        rw.shutdown()
        rw.close()
    s.remove(PENHMAIN_KEY)
    s.remove("cascada")


def test_the_run_button_runs_opens_the_results_and_the_cascade(qtbot, window, tmp_path, started, monkeypatch):
    from argus.ui.results.shower_view import ShowerView
    from argus.ui.run_dialog import RunDialog

    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    w = window
    assert not w.act_run.isEnabled()
    w.open_file(in_path, ask=False)
    qtbot.waitUntil(lambda: w.input is not None, timeout=30000)
    assert w.act_run.isEnabled() and w.act_run.shortcut().toString() == "F9"
    assert w._run_button is not None and w._run_button.isVisible()

    def accept(self):
        assert self.go.isEnabled(), self.status.text()
        self.go.click()
        return self.result()

    monkeypatch.setattr(RunDialog, "exec", accept)
    w.act_run.trigger()
    assert len(started) == 1 and started[0].in_path == in_path
    rw = w._results_window
    assert rw.isVisible() and rw.primary().folder.path == in_path.parent
    qtbot.waitUntil(lambda: isinstance(rw.views.currentWidget(), ShowerView), timeout=30000)
    v = rw.views.currentWidget()
    qtbot.waitUntil(lambda: v.data is not None and v.data.layer is not None and v.data.layer.n_showers == 60
                    and not v.job_timer.isActive(), timeout=90000)
    assert "penhmain se está ejecutando en un terminal" in w.statusBar().currentMessage()
    assert cs.find_cascades(in_path.parent, in_path.name)[0].showers == 60
    # «Ver la cascada» la vuelve a enseñar.
    rw.close()
    w.act_cascade.trigger()
    assert rw.isVisible() and isinstance(rw.views.currentWidget(), ShowerView)


def test_endf_files_that_are_copied_do_not_stop_the_run(qtbot, tmp_path, settings, started):
    """Con protones, los ENDF que citan los .mat y no están junto al .in se copian de la base de datos al ejecutar:
    no son un error que impida ejecutar."""
    from argus.penhan.inputfile import parse_in_file
    from argus.ui.settings import PENHAN_DB_KEY

    in_path, _doc = _case(tmp_path)
    text = in_path.read_text()
    text = text.replace("SKPAR  2", "SKPAR  4").replace("SLINES 6.617E5 0.851", "SENERG 1E8")
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("SLINES")) + "\n"
    in_path.write_text(text)
    for md in parse_in_file(in_path).model.materials:
        (in_path.parent / md.fname.strip()).write_text(
            " PENELOPE (v. 2018)  Material data file ...............\n Material: WATER  (278)\n"
            " Mass density = 1.00000000E+00 g/cm**3\n ... datos ...\n"
            " *** ENDF filenames and abundances of isotopes,  NISOT =   1\n"
            " pO16_ICRU63.endf            8016 1.00000E+00\n 0 = NALPHA\n", encoding="latin-1")
    db = tmp_path / "database"
    (db / "ENDF_data").mkdir(parents=True)
    (db / "ENDF_data" / "pO16_ICRU63.endf").write_text("ENDF de mentira\n")
    settings.setValue(PENHAN_DB_KEY, str(db))
    try:
        dlg, _ctrl = _dialog(qtbot, in_path, settings)
        assert "pO16_ICRU63.endf" in dlg.files_state.text() and "Se copia 1" in dlg.files_state.text()
        assert not dlg.errors.isVisibleTo(dlg) and dlg.go.isEnabled(), dlg.status.text()
        dlg.go.click()
        qtbot.waitUntil(lambda: dlg.handle is not None, timeout=30000)
        assert (in_path.parent / "pO16_ICRU63.endf").read_text() == "ENDF de mentira\n"
    finally:
        settings.remove(PENHAN_DB_KEY)
