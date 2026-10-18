"""Novena revisión (0.16.3): ejecutar penhmain, los guiones, la cascada y Windows.

* penhmain lee GEOMFN, MFNAME, IPSFN, EPROF y RESUME con (A20) desde la columna 8 y abre el nombre tal cual: con un
  blanco delante («MFNAME  agua.mat») no lo encuentra (crea uno vacío y se para; con RESUME empieza desde cero y
  sobrescribe los resultados). «Ejecutar» lo dice y no ejecuta.
* Un penhmain de Linux (también con WSL) no encuentra los nombres con tildes (el .in va en Latin-1 y el nombre del
  fichero en UTF-8) ni entiende las rutas de Windows.
* Cascada con WSL: un PID que ya es de otro proceso de Linux no se da por la ejecución ni se mata; una ejecución que
  lanza otra sesión de ARGUS no se lanza otra vez; sin esperar a WSL en la interfaz.
* Los guiones retocados a mano se guardan en «.bak»; «Cancelar» durante la copia no ejecuta; los resultados de una
  subcarpeta también se guardan en «anterior-…»; combinar.bat; la pista de penhmat con el fichero que no pudo leer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.penhan import batch as bt
from argus.penhan import cascade as cs
from argus.penhan import matgen as mg
from argus.penhan import programs as pg
from argus.penhan import run as rn
from argus.penhan import simfolder as sf
from argus.penhan.inputfile import parse_in_file
from argus.penhan.programs import Launch

sys.path.insert(0, str(Path(__file__).parent))
from test_run import CAJA, _case, _fake_penhmain, native  # noqa: E402

ELF = b"\x7fELF\x02\x01\x01" + bytes(9) + b"\x02\x00\x3e\x00" + bytes(40)  # cabecera de un ejecutable de Linux
PE = b"MZ" + bytes(0x3A) + (0x40).to_bytes(4, "little") + b"PE\0\0" + (0x8664).to_bytes(2, "little") + bytes(16)


@pytest.fixture
def qt_settings():
    pytest.importorskip("PySide6")
    from argus.ui.settings import app_settings

    s = app_settings()
    for k in ("trabajo", "ejecutar"):
        s.remove(k)
    yield s
    for k in ("trabajo", "ejecutar"):
        s.remove(k)


def _edit(in_path: Path, old: str, new: str):
    text = in_path.read_text(encoding="latin-1")
    assert old in text
    in_path.write_bytes(text.replace(old, new).encode("latin-1"))
    return parse_in_file(in_path)


def _exe(folder: Path, head: bytes, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(head)
    p.chmod(0o755)
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Nombres con un blanco delante (la columna 8)
# ---------------------------------------------------------------------------------------------------------------------

def test_a_material_name_with_a_leading_blank_stops_running(tmp_path):
    in_path, _doc = _case(tmp_path)
    doc = _edit(in_path, "MFNAME water.mat  ", "MFNAME  water.mat ")
    assert doc.model.ok and doc.model.materials[0].fname == " water.mat"  # como lo abre penhmain
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "programas"), launch_fn=native)
    it = next(i for i in plan.files.items if i.kind == "mat" and i.name == "water.mat")
    assert it.status == "blank" and it.lead == " " and it.lead_keyword == "MFNAME"
    msg = ("el nombre de MFNAME empieza por un espacio (columna 8 en blanco): penhmain buscaría « water.mat», con el "
           "espacio, y no lo encontraría; quite el espacio")
    assert msg in plan.problems
    # Al preparar una carpeta de simulación, tampoco se copia: se dice.
    sim = sf.plan_folder(in_path, doc, tmp_path / "sim")
    assert [i.name for i in sim.blank] == ["water.mat"] and sim.blank[0] in sim.problems
    assert "El nombre de MFNAME empieza por un espacio" in sf.summary(sim)


def test_a_geometry_name_with_two_leading_blanks(tmp_path):
    in_path, _doc = _case(tmp_path)
    doc = _edit(in_path, "GEOMFN caja.geo  ", "GEOMFN   caja.geo")
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "programas"), launch_fn=native)
    assert any(p.startswith("el nombre de GEOMFN empieza por espacios (columna 8 en blanco): penhmain buscaría "
                            "«  caja.geo», con los espacios") and p.endswith("quite los espacios")
               for p in plan.problems)


def test_resume_with_a_leading_blank_is_not_followed_and_does_not_run(tmp_path):
    in_path, _doc = _case(tmp_path)
    (in_path.parent / "dump.dmp").write_bytes(b"un volcado de la simulacion anterior")
    doc = _edit(in_path, "DUMPTO dump.dmp ", "RESUME  dump.dmp\nDUMPTO dump.dmp ")
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "programas"), launch_fn=native)
    assert plan.resume is None and plan.resume_missing == "dump.dmp"
    assert "espacio" in plan.resume_note
    assert any(p.startswith("el nombre de RESUME empieza por un espacio") for p in plan.problems)
    # El volcado del usuario no es un resultado anterior que se mueva.
    assert all(p.name != "dump.dmp" for p in plan.previous)


def test_a_name_in_column_8_has_no_problem(tmp_path):
    in_path, doc = _case(tmp_path)
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "programas"), launch_fn=native)
    assert not plan.files.blank and not plan.problems


# ---------------------------------------------------------------------------------------------------------------------
# Lo que no encontraría un penhmain de Linux (también con WSL)
# ---------------------------------------------------------------------------------------------------------------------

def test_accented_names_with_a_linux_penhmain(tmp_path):
    in_path, _doc = _case(tmp_path)
    (in_path.parent / "pulmón.mat").write_text("material de mentira\n")
    doc = _edit(in_path, "MFNAME water.mat  ", "MFNAME pulmón.mat ")
    linux = _exe(tmp_path / "programas", ELF, "penhmain.x")
    plan = rn.plan_run(in_path, doc, penhmain=linux, launch_fn=native)
    assert any("no encontraría «pulmón.mat»: el .in se guarda en Latin-1" in p for p in plan.problems)
    # Uno de Windows lee el .in con la página de códigos de Windows: lo encuentra.
    win = _exe(tmp_path / "otros", PE, "penhmain.exe")
    plan = rn.plan_run(in_path, doc, penhmain=win, launch_fn=native)
    assert not any("Latin-1" in p for p in plan.problems)


def test_windows_paths_with_a_linux_penhmain(tmp_path):
    in_path, _doc = _case(tmp_path)
    (in_path.parent / "mats").mkdir()
    shutil.move(str(in_path.parent / "water.mat"), in_path.parent / "mats" / "water.mat")
    doc = _edit(in_path, "MFNAME water.mat  ", "MFNAME mats\\water.mat")
    linux = _exe(tmp_path / "programas", ELF, "penhmain.x")
    plan = rn.plan_run(in_path, doc, penhmain=linux, launch_fn=native)
    assert [i.name for i in plan.files.winpath] == ["mats\\water.mat"]
    assert any("«\\» no separa carpetas; escriba «mats/water.mat»" in p for p in plan.problems)


def test_classify_names_for_a_linux_penhmain():
    assert sf.classify("C:\\m\\agua.mat", linux=True) == "winpath"
    assert sf.classify("mats\\agua.mat", linux=True) == "winpath"
    assert sf.classify("/home/rafa/agua.mat", linux=True) == "absolute"
    assert sf.classify("../agua.mat", linux=True) == "outside"
    assert sf.classify("mats/agua.mat", linux=True) == "ok"
    assert sf.windows_from_wsl("/mnt/c/m/agua.mat") == "C:\\m\\agua.mat"
    assert sf.windows_from_wsl("/mnt/d") == "D:\\"
    assert sf.windows_from_wsl("/home/rafa/agua.mat") is None


# ---------------------------------------------------------------------------------------------------------------------
# La cascada con WSL: el PID apuntado y dos sesiones de ARGUS
# ---------------------------------------------------------------------------------------------------------------------

FAKE_WSL = """#!/bin/sh
# wsl.exe de mentira: «--cd CARPETA -e orden…» en este Linux (como WSL en su distribución).
if [ "$1" = "--cd" ]; then cd "$2" || exit 1; shift 2; fi
[ "$1" = "-e" ] && shift
exec "$@"
"""


@pytest.mark.skipif(os.name == "nt", reason="un wsl.exe de mentira para sh")
def test_a_wsl_pid_of_another_process_is_neither_trusted_nor_killed(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "wsl.exe").write_text(FAKE_WSL)
    (bin_dir / "wsl.exe").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    run_dir = tmp_path / "cascada" / "run-02"
    run_dir.mkdir(parents=True)
    wsl = Launch("wsl", "de mentira")
    other = subprocess.Popen(["sleep", "60"], cwd=tmp_path, start_new_session=True)  # otro proceso, otra carpeta
    ours = None
    try:
        (run_dir / cs.PID_FILE).write_text(str(other.pid))
        assert not cs.pid_alive(run_dir, wsl)
        cs.kill_pid(run_dir, wsl)
        time.sleep(0.3)
        assert other.poll() is None  # sigue vivo: no era el de la ejecución
        ours = subprocess.Popen(["sleep", "60"], cwd=run_dir, start_new_session=True)  # el penhmain de la ejecución
        (run_dir / cs.PID_FILE).write_text(str(ours.pid))
        assert cs.pid_alive(run_dir, wsl)
        cs.kill_pid(run_dir, wsl)
        ours.wait(10)
        assert ours.returncode is not None
    finally:
        for p in (other, ours):
            if p is not None and p.poll() is None:
                p.kill()
                p.wait()


def _cascade_job(tmp_path: Path):
    from test_cascade import FAKE
    from test_cascade import _case as cascade_case

    in_path, doc = cascade_case(tmp_path)
    exe = tmp_path / "programas" / "penhmain.x"
    exe.parent.mkdir()
    exe.write_text(FAKE)
    exe.chmod(0o755)
    job = cs.CascadeJob(in_path, doc, exe, Launch("native", ""), showers=150, cap=50, jobs=1)
    job.plan = cs.plan_cascade(in_path, doc, exe, showers=150, cap=50,
                               folder=cs.new_cascade_folder(in_path.parent, create=True))
    cs.write_cascade(job.plan)
    return job, [job.plan.folder / r.folder for r in job.plan.runs]


def test_a_run_another_session_is_launching_is_not_launched_again(tmp_path):
    job, dirs = _cascade_job(tmp_path)
    queue: list = []
    others: set[int] = set()
    assert not job._taken(0, dirs, queue, others)  # nadie la ha lanzado: se lanza
    (dirs[1] / cs.RC_FILE).write_text("")  # otra sesión acaba de lanzarla (aún sin PID)
    assert job._taken(1, dirs, queue, others) and others == {1}
    # Una que otra sesión dejó a medias hace rato (sin proceso) sí se lanza.
    rc = dirs[2] / cs.RC_FILE
    rc.write_text("")
    old = time.time() - 10 * cs.PID_WAIT
    os.utime(rc, (old, old))
    assert not job._taken(2, dirs, queue, others)


def test_the_cascade_asks_wsl_in_its_own_thread(tmp_path, monkeypatch):
    job, _dirs = _cascade_job(tmp_path)
    job.launch = pg.WSL_CHECKING
    job.plan = None
    seen = []

    def fake_launch(info, host=pg.HOST, **_kw):
        import threading

        seen.append(threading.current_thread().name)
        return Launch("", "WSL no funciona: sin distribución de Linux instalada")

    monkeypatch.setattr(cs, "launch_for", fake_launch)
    job.start()
    job.wait(30)
    assert seen == ["argus-cascada"]
    assert job.status.stage == "error" and "WSL no funciona" in job.status.text


def test_start_cascade_does_not_wait_for_wsl(tmp_path, monkeypatch, qt_settings):
    from argus.ui import cascade_ui

    in_path, _doc = _case(tmp_path)
    exe = _exe(tmp_path / "programas", ELF, "penhmain.x")
    started = []
    monkeypatch.setattr(cascade_ui, "needs_wsl", lambda info: True)
    monkeypatch.setattr(cascade_ui, "pg_wsl_cached", lambda: None)
    monkeypatch.setattr(cascade_ui, "launch_for", lambda info: pytest.fail("se ha preguntado a WSL en la interfaz"))
    monkeypatch.setattr(cascade_ui.cs, "start_job", lambda job: started.append(job) or job)
    job = cascade_ui.start_cascade(in_path, showers=10, settings=qt_settings, exe=exe)
    assert started == [job] and job.launch.pending


# ---------------------------------------------------------------------------------------------------------------------
# Guiones retocados a mano
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,edit", [
    ("sh", lambda t: t.replace("  argus_rc=$?", "  argus_rc=$?\n  ulimit -s unlimited")),
    ("sh", lambda t: t.replace("GFORTRAN_UNBUFFERED_PRECONNECTED=y ./", "GFORTRAN_UNBUFFERED_PRECONNECTED=y nice ./")),
    ("sh", lambda t: t.replace("chmod +x", "#chmod +x")),
    ("sh", lambda t: t.replace("echo\n", "echo 'Simulando: no cierre esta ventana'\n", 1)),
    ("bat", lambda t: t.replace("set GFORTRAN", "set OMP_NUM_THREADS=1\r\nset GFORTRAN")),
    ("bat", lambda t: t.replace('"penhmain.exe" <', '"penhmain-2.exe" <')),
])
def test_a_hand_edited_launcher_is_kept_in_a_bak(tmp_path, kind, edit):
    first = rn.write_launcher(tmp_path, kind, "caso.in", "penhmain.exe" if kind == "bat" else "penhmain.x")
    edited = edit(first.read_bytes().decode("utf-8"))
    first.write_bytes(edited.encode("utf-8"))
    again = rn.write_launcher(tmp_path, kind, "otro.in", "penhmain.exe" if kind == "bat" else "penhmain.x")
    assert again == first
    bak = first.with_name(first.name + ".bak")
    assert bak.read_bytes() == edited.encode("utf-8")
    assert "otro.in" in again.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["sh", "bat"])
def test_argus_own_launcher_for_another_case_is_replaced_without_a_bak(tmp_path, kind):
    exe = "penhmain.exe" if kind == "bat" else "penhmain.x"
    rn.write_launcher(tmp_path, kind, "uno con 'comilla' & 100%.in", exe)
    target = rn.write_launcher(tmp_path, kind, "otro.in", exe)
    assert not list(tmp_path.glob("*.bak"))
    assert "otro.in" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------------
# Resultados en una subcarpeta, scripts en paralelo y penhmat
# ---------------------------------------------------------------------------------------------------------------------

def test_previous_results_in_a_subfolder_are_stashed_too(tmp_path):
    in_path, _doc = _case(tmp_path)
    doc = _edit(in_path, "IMPDET 1e3 7e5 70 1 1          [E-window, no. of bins, IPSF, IDCUT]\n",
                "IMPDET 1e3 7e5 70 1 1          [E-window, no. of bins, IPSF, IDCUT]\nIDPSF  salida/psf.dat\n")
    folder = in_path.parent
    (folder / "salida").mkdir()
    shutil.copy(CAJA / "psf-impdet.dat", folder / "salida" / "psf.dat")
    shutil.copy(CAJA / "penhmain-res.dat", folder / "penhmain-res.dat")
    prev = rn.previous_outputs(folder, doc)
    assert sorted(rn.relative_name(folder, p) for p in prev) == ["penhmain-res.dat", "salida/psf.dat"]
    dest = rn.stash_outputs(folder, prev)
    assert (dest / "salida" / "psf.dat").is_file() and (dest / "penhmain-res.dat").is_file()
    assert (folder / "salida").is_dir() and not (folder / "salida" / "psf.dat").exists()
    assert "salida/psf.dat" in (dest / rn.STASH_NOTE).read_text(encoding="utf-8")


def _parallel(tmp_path: Path):
    in_path, doc = _case(tmp_path)
    return bt.make_parallel(doc, in_path, tmp_path / "par", 2, total_showers=100)


def test_parallel_scripts_with_a_script_penhmain_use_wsl(tmp_path):
    b = _parallel(tmp_path)
    b.exe_system, b.exe_name = "script", "penhmain.sh"  # un guion con «#!»: ARGUS lo ejecuta con WSL en Windows
    assert b.with_wsl
    assert "Start-Process -FilePath 'wsl.exe'" in bt.launch_ps1(b, 2)
    assert "wsl.exe --cd $PSScriptRoot -e sh combinar.sh $Sum" in bt.combine_scripts(b)["combinar.ps1"]


def test_combinar_bat_runs_the_powershell_script(tmp_path):
    b = _parallel(tmp_path)
    for system, usage in (("windows", "C:\\ruta\\penhmain-sum.exe"), ("linux", "/ruta/de/linux/penhmain-sum")):
        b.exe_system = system
        bat = bt.combine_scripts(b)["combinar.bat"]
        assert 'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0combinar.ps1" %*' in bat
        assert f"combinar.bat {usage}" in bat and bat.isascii()
    bt.write_batch(b, jobs=2)
    raw = (tmp_path / "par" / "combinar.bat").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_penhmat_hint_names_the_database_file_it_could_not_read():
    out = ("  Inner-shell lowest energy = 9.09E+01 eV\n"
           "At line 516 of file penelope.f (unit = 3, file = './pdfiles/pdesi11.p14')\n"
           "Fortran runtime error: End of file\n" + "\n".join(f"  salida {i}" for i in range(12)))
    res = SimpleNamespace(output=out, error="", cancelled=False, timed_out=False, returncode=2)
    msg = mg.penhmat_problem(res, Path("no-existe.mat"))
    assert "no ha podido leer ./pdfiles/pdesi11.p14: está vacío o incompleto" in msg
    assert "ARGUS no sabía" not in msg
    assert "Fortran runtime error: End of file" in msg  # la línea del error, aunque no esté al final
    # Leyendo las respuestas de ARGUS (la entrada estándar): penhmat pidió algo más.
    res.output = ("Enter ...\nAt line 99 of file penhmat.f (unit = 5, file = 'stdin')\n"
                  "Fortran runtime error: End of file\n")
    assert "ARGUS no sabía" in mg.penhmat_problem(res, Path("no-existe.mat"))


# ---------------------------------------------------------------------------------------------------------------------
# El diálogo «Ejecutar»
# ---------------------------------------------------------------------------------------------------------------------

def _mat_headers(in_path: Path, doc) -> None:
    for md in doc.model.materials:  # cabeceras de .mat (si no, ARGUS ve errores)
        (in_path.parent / md.fname.strip()).write_text(
            " PENELOPE (v. 2018)  Material data file ...\n Material: X  (278)\n"
            " Mass density = 1.00000000E+00 g/cm**3\n")


@pytest.mark.gui
@pytest.mark.skipif(os.name == "nt", reason="los penhmain de mentira son guiones de sh")
def test_cancel_while_copying_never_runs_penhmain(qtbot, tmp_path, qt_settings, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from argus.session import open_case
    from argus.ui.input_controller import InputController
    from argus.ui.run_dialog import RunDialog
    from argus.ui.settings import PENHMAIN_KEY

    in_path, doc = _case(tmp_path)
    _mat_headers(in_path, doc)
    qt_settings.setValue(PENHMAIN_KEY, str(_fake_penhmain(tmp_path / "programas")))
    qt_settings.setValue("cascada/al_ejecutar", "false")
    started = []
    monkeypatch.setattr(rn, "start", lambda plan, **kw: started.append(plan) or rn.RunHandle(
        None, plan.folder, plan.in_path.name, rn.Terminal(["true"], plan.folder, "x")))
    extra = tmp_path / "otra"
    extra.mkdir()
    mat = doc.model.materials[0].fname.strip()
    shutil.move(str(in_path.parent / mat), extra / mat)
    real_plan = rn.plan_run
    monkeypatch.setattr(rn, "plan_run", lambda *a, **k: real_plan(*a, **{**k, "extra_dirs": [extra]}))
    try:
        cf = open_case(in_path)
        dlg = RunDialog(None, InputController(cf.input_doc, in_path), cf.session, qt_settings)
        qtbot.addWidget(dlg)
        dlg.showers.setValue(0)
        dlg.force.setChecked(True)
        assert [it.name for it in dlg.plan.to_copy] == [mat]
        dlg.run()  # copia en otro hilo
        t0 = time.time()
        while dlg._copier is not None and not (in_path.parent / mat).exists() and time.time() - t0 < 10:
            time.sleep(0.01)
        time.sleep(0.2)  # la copia ha terminado, pero su señal aún no ha llegado a la ventana
        dlg.reject()  # «Cancelar»
        for _ in range(3):
            QApplication.processEvents()
            time.sleep(0.05)
        assert not started and dlg.handle is None
    finally:
        qt_settings.remove(PENHMAIN_KEY)
        qt_settings.remove("cascada")


@pytest.mark.gui
@pytest.mark.skipif(os.name == "nt", reason="se simula Windows")
def test_the_run_dialog_does_not_wait_for_wsl(qtbot, tmp_path, qt_settings, monkeypatch):
    from argus.session import open_case
    from argus.ui.input_controller import InputController
    from argus.ui.run_dialog import RunDialog

    in_path, doc = _case(tmp_path)
    _mat_headers(in_path, doc)
    _exe(in_path.parent, ELF, "penhmain.x")  # el penhmain de Linux, en la carpeta del caso
    monkeypatch.setattr(pg, "HOST", "windows")
    pg._WSL_CACHE.clear()
    calls = []

    def slow_wsl(refresh=False, timeout=90.0):
        import threading

        calls.append(threading.current_thread().name)
        time.sleep(1.5)
        st = pg.WslStatus(True, "Ubuntu 22.04.4 LTS", True)
        pg._WSL_CACHE["status"] = st
        return st

    monkeypatch.setattr(pg, "wsl_status", slow_wsl)
    try:
        cf = open_case(in_path)
        t0 = time.monotonic()
        dlg = RunDialog(None, InputController(cf.input_doc, in_path), cf.session, qt_settings)
        qtbot.addWidget(dlg)
        assert time.monotonic() - t0 < 1.2  # sin esperar a WSL
        assert dlg.plan.exe.launch is not None and dlg.plan.exe.launch.pending
        assert "comprobando si funciona wsl" in dlg.exe_state.text().lower()
        qtbot.waitUntil(lambda: dlg._wsl_result is not None, timeout=10000)
        assert calls and all(name == "argus-wsl" for name in calls)
        assert dlg.plan.exe.launch is not None and dlg.plan.exe.launch.how == "wsl"
    finally:
        pg._WSL_CACHE.clear()
