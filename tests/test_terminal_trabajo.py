"""El terminal al ejecutar y la sección «Trabajo» (0.14.0).

* En Windows se elige el terminal: el de Linux (WSL) o la consola de Windows. Un penhmain de Linux solo en el de
  Linux; uno de Windows en los dos (en el de Linux, WSL lo ejecuta como programa de Windows, con la salida también en
  penhmain.out). En Linux y macOS, el que hay.
* «Trabajo»: «Partículas (NSIMSH)», el tiempo máximo con unidades (hasta 10⁹ s) y, al ejecutar, «Lluvias»: las que
  se siguen para verlas en 3D (la cascada, como mucho 1000).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.penhan import cascade as cs
from argus.penhan import programs as pg
from argus.penhan import run as rn
from argus.penhan.programs import ExeInfo, Launch, WslStatus

sys.path.insert(0, str(Path(__file__).parent))
from test_run import _case, _fake_penhmain  # noqa: E402

UBUNTU = WslStatus(True, "Ubuntu 22.04.4 LTS", True)
NO_INTEROP = WslStatus(True, "Ubuntu 22.04.4 LTS", False)
NO_WSL = WslStatus(False, "WSL (el Linux de Windows) no está instalado")
LINUX_EXE = ExeInfo(Path("penhmain.x"), "linux", "x86-64")
WIN_EXE = ExeInfo(Path("penhmain.exe"), "windows", "x86-64")


def _keys(choices):
    return {c.key: c.ok for c in choices}


# ---------------------------------------------------------------------------------------------------------------------
# Qué terminales se pueden elegir
# ---------------------------------------------------------------------------------------------------------------------

def test_a_linux_penhmain_on_windows_goes_to_the_linux_terminal():
    ch = rn.shell_choices(LINUX_EXE, host="windows", wsl=lambda: UBUNTU)
    assert _keys(ch) == {rn.SHELL_LINUX: True, rn.SHELL_WINDOWS: False}
    linux = ch[0]
    assert linux.label == "Linux (WSL: Ubuntu 22.04.4 LTS)" and "penhmain.out" in linux.note
    assert "solo se ejecuta con WSL" in ch[1].note
    assert rn.default_shell(ch) == rn.SHELL_LINUX
    assert rn.default_shell(ch, preferred=rn.SHELL_WINDOWS) == rn.SHELL_LINUX  # no se puede: el de Linux
    # Sin WSL no hay ninguno (y ya lo dice penhmain: no se puede ejecutar).
    none = rn.shell_choices(LINUX_EXE, host="windows", wsl=lambda: NO_WSL)
    assert _keys(none) == {rn.SHELL_LINUX: False, rn.SHELL_WINDOWS: False} and rn.default_shell(none) is None
    assert "no está instalado" in none[0].note


def test_a_windows_penhmain_can_go_to_either():
    ch = rn.shell_choices(WIN_EXE, host="windows", wsl=lambda: UBUNTU)
    assert _keys(ch) == {rn.SHELL_LINUX: True, rn.SHELL_WINDOWS: True}
    assert rn.default_shell(ch) == rn.SHELL_WINDOWS  # el de siempre
    assert rn.default_shell(ch, preferred=rn.SHELL_LINUX) == rn.SHELL_LINUX  # lo que eligió el usuario
    assert "WSL ejecuta el penhmain de Windows" in ch[0].note
    off = rn.shell_choices(WIN_EXE, host="windows", wsl=lambda: NO_INTEROP)
    assert _keys(off) == {rn.SHELL_LINUX: False, rn.SHELL_WINDOWS: True} and "interop" in off[0].note
    assert _keys(rn.shell_choices(WIN_EXE, host="windows", wsl=lambda: NO_WSL)) == {rn.SHELL_LINUX: False,
                                                                                     rn.SHELL_WINDOWS: True}


def test_linux_and_macos_have_one_terminal():
    assert rn.shell_choices(LINUX_EXE, host="linux", wsl=lambda: UBUNTU) == []
    assert rn.shell_choices(LINUX_EXE, host="macos", wsl=lambda: UBUNTU) == []
    t = rn.describe_terminal(Launch("native", ""), host="macos")
    assert t.name == "el Terminal de macOS"
    t = rn.describe_terminal(Launch("native", ""), host="linux", which={"konsole": "/usr/bin/konsole"}.get, env={})
    assert t.name == "konsole" and not t.background


def test_wsl_status_says_if_it_runs_windows_programs(monkeypatch):
    class R:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out.encode(), b""

    monkeypatch.setattr(pg, "wsl_executable", lambda: Path("wsl.exe"))
    for out, interop in (("ARGUS-WSL\nUbuntu 22.04.4 LTS\nARGUS-INTEROP\n", True),
                         ("ARGUS-WSL\nUbuntu 22.04.4 LTS\n", False), ("ARGUS-WSL\nARGUS-INTEROP\n", True)):
        monkeypatch.setattr(pg.subprocess, "run", lambda *a, _o=out, **k: R(_o))
        pg._WSL_CACHE.clear()
        try:
            st = pg.wsl_status(refresh=True)
        finally:
            pg._WSL_CACHE.clear()
        assert st.ok and st.interop is interop and st.note in ("Ubuntu 22.04.4 LTS", "Linux")


# ---------------------------------------------------------------------------------------------------------------------
# El guion y la orden
# ---------------------------------------------------------------------------------------------------------------------

def test_terminal_command_for_each_choice(tmp_path):
    script = tmp_path / "argus-ejecutar.sh"
    t = rn.terminal_command(script, Launch("native", ""), host="windows", shell=rn.SHELL_LINUX, wsl=lambda: UBUNTU)
    assert t.argv[1:] == ["--cd", str(tmp_path), "-e", "sh", "argus-ejecutar.sh"] and t.new_console
    assert t.name == "el terminal de Linux (WSL: Ubuntu 22.04.4 LTS)"
    t = rn.terminal_command(script, Launch("wsl", ""), host="windows", wsl=lambda: None)
    assert t.name == "el terminal de Linux (WSL)" and t.argv[-2:] == ["sh", "argus-ejecutar.sh"]
    t = rn.terminal_command(tmp_path / "argus-ejecutar.bat", Launch("native", ""), host="windows",
                            shell=rn.SHELL_WINDOWS)
    assert t.argv == ["cmd.exe", "/c", "argus-ejecutar.bat"] and t.name == "la consola de Windows (cmd)"


def test_a_windows_penhmain_in_the_linux_terminal_gets_its_output_at_once():
    assert rn.launcher_kind(WIN_EXE, "windows", rn.SHELL_LINUX) == "sh"
    assert rn.launcher_kind(WIN_EXE, "windows", rn.SHELL_WINDOWS) == "bat"
    assert rn.launcher_kind(WIN_EXE, "windows") == "bat"
    assert rn.launcher_kind(LINUX_EXE, "windows") == "sh"
    sh = rn.launcher_text("sh", "caso.in", "penhmain.exe", windows_exe=True)
    assert 'WSLENV="GFORTRAN_UNBUFFERED_PRECONNECTED${WSLENV:+:$WSLENV}"; export WSLENV' in sh
    assert "WSLENV" not in rn.launcher_text("sh", "caso.in", "penhmain.x")
    assert rn._launcher_like(sh.encode(), "sh")  # un guion de ARGUS: se puede volver a escribir
    # En la consola de Windows, también sin búfer (y el .bat sigue siendo «de ARGUS»).
    bat = rn.launcher_text("bat", "caso.in", "penhmain.exe")
    lines = bat.split("\r\n")
    i = lines.index("set GFORTRAN_UNBUFFERED_PRECONNECTED=y")
    assert lines[i + 1] == '"penhmain.exe" < "caso.in"' and lines[i + 2] == "set ARGUS_RC=%errorlevel%"
    assert rn._launcher_like(bat.encode(), "bat")


@pytest.mark.skipif(os.name == "nt", reason="sh")
def test_the_script_with_wslenv_runs_and_keeps_the_output(tmp_path):
    in_path, doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas", "penhmain.exe")
    plan = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=lambda _i: Launch("native", ""))
    rn.install_exe(plan.exe)
    script = rn.write_launcher(plan.folder, "sh", in_path.name, "penhmain.exe", windows_exe=True)
    r = subprocess.run(["sh", str(script)], input=b"\n", capture_output=True, timeout=60, cwd=plan.folder)
    assert r.returncode == 0, r.stderr
    out = (plan.folder / rn.RUN_OUTPUT).read_text()
    assert "*** END ***" in out and (plan.folder / "penhmain-res.dat").is_file()


def test_start_with_the_chosen_terminal(tmp_path):
    in_path, doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas", "penhmain.exe")
    plan = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=lambda _i: Launch("native", ""))
    rn.install_exe(plan.exe)
    plan.exe.info = WIN_EXE  # como si fuera un penhmain.exe de verdad
    opened = []
    plan.shell = rn.SHELL_LINUX
    h = rn.start(plan, host="windows", popen=lambda t: opened.append(t))
    assert h.terminal.argv[-2:] == ["sh", "argus-ejecutar.sh"] and "WSLENV" in (plan.folder /
                                                                                   "argus-ejecutar.sh").read_text()
    plan.shell = rn.SHELL_WINDOWS
    h = rn.start(plan, host="windows", popen=lambda t: opened.append(t))
    assert h.terminal.argv == ["cmd.exe", "/c", "argus-ejecutar.bat"]
    plan.exe.info = LINUX_EXE
    with pytest.raises(ValueError, match="terminal de Linux"):
        rn.start(plan, host="windows", popen=lambda t: opened.append(t))


def test_a_network_folder_and_the_linux_terminal(tmp_path, monkeypatch):
    in_path, doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas", "penhmain.exe")
    plan = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=lambda _i: Launch("native", ""))
    monkeypatch.setattr(rn, "HOST", "windows")
    plan.folder = Path("\\\\servidor\\casos\\caso")
    plan.shell = rn.SHELL_WINDOWS
    assert any("consola de Windows no puede" in p for p in plan.problems)
    plan.shell = rn.SHELL_LINUX
    assert any("WSL no puede" in p for p in plan.problems)


def test_the_cascade_asks_for_at_most_1000_showers():
    assert cs.MAX_SHOWERS == 1000


# ---------------------------------------------------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def qt_settings():
    pytest.importorskip("PySide6")
    from argus.ui.settings import app_settings

    s = app_settings()
    s.remove("trabajo")
    s.remove("ejecutar")
    yield s
    s.remove("trabajo")
    s.remove("ejecutar")


@pytest.mark.gui
def test_time_with_units_up_to_1e9_seconds(qtbot, qt_settings):
    from argus.ui.sim_widgets import DurationEdit

    w = DurationEdit(settings=qt_settings)
    qtbot.addWidget(w)
    got = []
    w.secondsEdited.connect(got.append)
    w.set_seconds(2e9)  # el de penhmain (de un .in que ya lo tiene): se enseña tal cual
    assert w.edit.text() == "2e9"
    w.unit.setCurrentIndex(w.unit.findText("semanas"))
    assert w.edit.text() == "3306.88"
    w.edit.setText("2")
    w.edit.setModified(True)
    w.edit.editingFinished.emit()
    assert got == [1209600.0] and w.seconds() == 1209600.0
    # Más de 10⁹ s no: se queda como estaba y lo dice.
    w.edit.setText("2000")
    w.edit.setModified(True)
    w.edit.editingFinished.emit()
    assert got == [1209600.0] and w.edit.text() == "2" and "10⁹" in w.edit.toolTip()
    # La unidad se recuerda.
    assert qt_settings.value("trabajo/unidad_tiempo") == "semanas"
    w2 = DurationEdit(settings=qt_settings)
    qtbot.addWidget(w2)
    assert w2.unit.currentText() == "semanas"


@pytest.mark.gui
def test_job_section_says_particles(qtbot, qt_settings):
    from argus.penhan.inputfile import parse_in_text
    from argus.ui.sim_outputs import JobSection

    text = Path(__file__).parent.joinpath("corpus", "resultados", "caja", "caja.in").read_text(encoding="latin-1")
    doc = parse_in_text(text)
    sec = JobSection()
    qtbot.addWidget(sec)
    edits = []
    sec.apply.connect(lambda t, fn: edits.append(t) or fn(doc))
    sec.set_context(doc, None, None)
    labels = [sec.form.itemAt(i, sec.form.ItemRole.LabelRole).widget().text() for i in range(2)]
    assert labels == ["Partículas:", "Tiempo máx.:"]
    assert "partículas" in sec.summary()
    sec.time.unit.setCurrentIndex(sec.time.unit.findText("h"))
    sec.time.edit.setText("36")
    sec.time.edit.setModified(True)
    sec.time.edit.editingFinished.emit()
    from argus.penhan import sections as sc

    assert sc.value(doc, "TIME") == [129600.0] and edits[-1] == "Tiempo de simulación"
    sec.refresh()
    from argus.core.numfmt import fmt_count

    assert sec.time_text.text() == f"= {fmt_count(129600)} s"


@pytest.mark.gui
@pytest.mark.skipif(os.name == "nt", reason="los penhmain de mentira son guiones de sh")
def test_run_dialog_on_windows_with_a_linux_penhmain(qtbot, tmp_path, qt_settings, monkeypatch):
    from argus.session import open_case
    from argus.ui.input_controller import InputController
    from argus.ui.run_dialog import RunDialog

    in_path, _doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas")
    monkeypatch.setattr(pg, "HOST", "windows")
    pg._WSL_CACHE["status"] = UBUNTU
    real = rn.plan_run
    monkeypatch.setattr(rn, "plan_run", lambda *a, **k: real(
        *a, **{**k, "launch_fn": lambda info: pg.launch_for(info, host="windows", wsl=lambda: UBUNTU)}))
    started = []
    monkeypatch.setattr(rn, "start", lambda plan, **kw: started.append(plan) or rn.RunHandle(
        None, plan.folder, plan.in_path.name, rn.Terminal(["true"], plan.folder, "x")))
    try:
        cf = open_case(in_path)
        ctrl = InputController(cf.input_doc, in_path)
        from argus.ui.settings import PENHMAIN_KEY

        qt_settings.setValue(PENHMAIN_KEY, str(exe))
        dlg = RunDialog(None, ctrl, cf.session, qt_settings)
        dlg.exe.setText(str(exe))
        dlg.refresh()
        qtbot.addWidget(dlg)
        assert dlg.rb_linux.isEnabled() and dlg.rb_linux.isChecked() and not dlg.rb_windows.isEnabled()
        assert "Ubuntu 22.04.4 LTS" in dlg.rb_linux.text()
        assert "solo se ejecuta con WSL" in dlg.term_state.text()
        assert dlg._shell() == rn.SHELL_LINUX
        dlg.showers.setValue(0)
        dlg.force.setChecked(True)
        dlg.run_anyway.setChecked(True)
        dlg._launch()
        assert started and started[0].shell == rn.SHELL_LINUX
        assert dlg.cascade_n is None and str(qt_settings.value("cascada/al_ejecutar")) == "false"
    finally:
        from argus.ui.settings import PENHMAIN_KEY

        pg._WSL_CACHE.clear()
        qt_settings.remove(PENHMAIN_KEY)
        qt_settings.remove("cascada")
