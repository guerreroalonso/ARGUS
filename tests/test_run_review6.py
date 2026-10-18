"""Revisión de «Ejecutar penhmain» (0.13.1): los fallos que se encontraron, cada uno con su prueba.

Consola de Windows sin la salida de penhmain, RESUME con un volcado vacío, cancelar o fallar la copia, el volcado por
defecto, la nota de «anterior-…», el penhmain de la cascada, el .bat con tildes y «&», penhmain «ya simulando» con
DUMPP largo, el penhmain que se usará en un conflicto, copias del usuario, «.bak2», DLL, guiones retocados, el .in
cambiado en el disco, Intro en el diálogo y los textos.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

from argus.penhan import run as rn
from argus.penhan.inputfile import parse_in_file
from argus.results.folder import ResultsFolder, log_error

sys.path.insert(0, str(Path(__file__).parent))
from test_run import CAJA, _case, _fake_penhmain, native  # noqa: E402

shell = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="penhmain de mentira en sh")


# -- núcleo (sin interfaz) ------------------------------------------------------------------------------------------

def test_the_new_windows_console_gets_its_own_standard_handles(tmp_path, monkeypatch):
    """Con CREATE_NEW_CONSOLE no se pasan stdin/stdout/stderr: con uno solo, CPython pone STARTF_USESTDHANDLES con
    los otros dos de ARGUS y la consola nueva se quedaba sin la salida de penhmain (y «pause» no esperaba)."""
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen.update(kw)

    monkeypatch.setattr(rn.os, "name", "nt")
    monkeypatch.setattr(rn.subprocess, "Popen", FakePopen)
    rn.open_terminal(rn.Terminal(["cmd.exe", "/c", "argus-ejecutar.bat"], tmp_path, "una consola de Windows",
                                 new_console=True))
    monkeypatch.undo()
    assert not [k for k in ("stdin", "stdout", "stderr") if seen.get(k) is not None]
    assert seen["creationflags"] == rn.CREATE_NEW_CONSOLE and seen["cwd"] == str(tmp_path)


def test_an_empty_or_broken_resume_dump_is_not_resumed(tmp_path):
    """penhmain deja «Empty.» en el volcado de RESUME si no había nada que seguir: la vez siguiente empieza desde
    cero y sobrescribe los resultados, que hay que guardar en «anterior-…»."""
    in_path, _doc = _case(tmp_path)
    folder = in_path.parent
    text = in_path.read_text(encoding="latin-1").replace("DUMPTO dump.dmp", "RESUME dump1.dmp\nDUMPTO dump2.dmp")
    in_path.write_text(text, encoding="latin-1")
    for n in ("penhmain-res.dat", "penhmain.dat", "depth-dose.dat"):
        shutil.copy(CAJA / n, folder / n)
    (folder / "dump1.dmp").write_text("   Empty.\n")
    plan = rn.plan_run(in_path, parse_in_file(in_path), launch_fn=native)
    assert plan.resume is None and plan.resume_missing == "dump1.dmp" and plan.resume_note == "está vacío"
    assert {"penhmain-res.dat", "depth-dose.dat"} <= {p.name for p in plan.previous}
    (folder / "dump1.dmp").unlink()
    plan = rn.plan_run(in_path, parse_in_file(in_path), launch_fn=native)
    assert plan.resume_note == "no existe" and plan.previous


def test_the_default_dump_is_kept_with_the_previous_results(tmp_path):
    """Sin DUMPTO, penhmain vuelca en «dump.dmp» (DUMPP vale por defecto 2e9 < 1e10): es de la simulación anterior y
    se guarda con sus resultados (si no, la nueva lo sobrescribe)."""
    in_path, _doc = _case(tmp_path)
    folder = in_path.parent
    text = "\n".join(ln for ln in in_path.read_text(encoding="latin-1").splitlines()
                     if not ln.startswith(("DUMPTO", "DUMPP"))) + "\n"
    in_path.write_text(text, encoding="latin-1")
    shutil.copy(CAJA / "penhmain-res.dat", folder / "penhmain-res.dat")
    (folder / "dump.dmp").write_text("volcado\n")
    names = [p.name for p in rn.previous_outputs(folder, parse_in_file(in_path))]
    assert "dump.dmp" in names
    # Con DUMPP ≥ 1e10, penhmain no vuelca: un «dump.dmp» ahí no es suyo.
    in_path.write_text(text.replace("NSIMSH", "DUMPP  1e12\nNSIMSH"), encoding="latin-1")
    assert "dump.dmp" not in [p.name for p in rn.previous_outputs(folder, parse_in_file(in_path))]


def test_user_copies_are_not_moved_but_files_named_in_the_in_are(tmp_path):
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    shutil.copy(CAJA / "depth-dose.dat", folder / "depth-dose.dat")
    shutil.copy(CAJA / "depth-dose.dat", folder / "E100_depth-dose.dat")  # guardada a mano por el usuario
    shutil.copy(CAJA / "run.log", folder / "salida_E100.txt")
    shutil.copy(CAJA / "spc-impdet-01.dat", folder / "espectro1.dat")  # el nombre de IDSPC en el .in
    text = in_path.read_text(encoding="latin-1")
    text = re.sub(r"(?m)^(IMPDET .*)$", r"\1\nIDSPC  espectro1.dat", text, count=1)
    in_path.write_text(text, encoding="latin-1")
    names = [p.name for p in rn.previous_outputs(folder, parse_in_file(in_path))]
    assert "depth-dose.dat" in names and "espectro1.dat" in names
    assert "E100_depth-dose.dat" not in names and "salida_E100.txt" not in names


def test_a_stash_that_stops_halfway_is_still_marked_as_a_stash(tmp_path, monkeypatch):
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    for n in ("penhmain-res.dat", "penhmain.dat", "photon.trk", "depth-dose.dat"):
        shutil.copy(CAJA / n, folder / n)
    prev = rn.previous_outputs(folder, doc)
    prev.append(folder / "ya-no-esta.dat")  # borrado mientras tanto: no cuenta
    real = os.rename

    def rename(a, b):  # un fichero abierto en otro programa (WinError 32)
        if Path(a).name == "photon.trk":
            raise PermissionError(13, "El proceso no tiene acceso al archivo")
        return real(a, b)

    monkeypatch.setattr(rn.os, "rename", rename)
    with pytest.raises(OSError, match="photon.trk"):
        rn.stash_outputs(folder, prev)
    monkeypatch.undo()
    dest = next(d for d in folder.iterdir() if d.is_dir() and d.name.startswith(rn.STASH_PREFIX))
    assert rn.is_stash(dest)
    assert ResultsFolder(folder, in_path).runs == []  # la ventana de resultados no la toma por otra ejecución


def test_a_backup_of_penhmain_is_never_taken_for_the_penhmain_of_the_folder(tmp_path):
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    elf = b"\x7fELF\x02\x01\x01" + bytes(9) + b"\x02\x00\x3e\x00" + bytes(40)
    chosen = tmp_path / "programas" / "penhmain.x"
    chosen.parent.mkdir()
    chosen.write_bytes(elf + b"nuevo")
    chosen.chmod(0o755)
    (folder / "penhmain.x.bak").write_bytes(elf + b"copia del usuario")
    (folder / "penhmain.x.bak2").write_bytes(elf + b"copia de ARGUS 0.13.0")
    here = folder / "penhmain.x"
    here.write_bytes(elf + b"el de la carpeta")
    here.chmod(0o755)
    plan = rn.plan_run(in_path, doc, penhmain=chosen, launch_fn=native)
    assert plan.exe.status == "conflict"
    rn.install_exe(plan.exe, replace=True)
    assert (folder / "penhmain.x.2.bak").read_bytes().endswith(b"el de la carpeta")
    choice = rn.choose_exe(folder, None, launch_fn=native)
    assert choice.target.name == "penhmain.x"
    assert [p.name for p in rn.folder_penhmain(folder)] == ["penhmain.x"]


def test_replacing_a_penhmain_exe_also_brings_its_mingw_dlls(tmp_path):
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    pe = b"MZ" + bytes(0x3A) + (0x40).to_bytes(4, "little") + b"PE\0\0" + (0x8664).to_bytes(2, "little") + bytes(64)
    prog = tmp_path / "msys64" / "penhmain.exe"
    prog.parent.mkdir()
    prog.write_bytes(pe + b"nuevo, compilado sin -static")
    (prog.parent / "libgfortran-5.dll").write_bytes(b"MZ dll")
    (folder / "penhmain.exe").write_bytes(pe + b"el de antes")
    plan = rn.plan_run(in_path, doc, penhmain=prog, launch_fn=native)
    assert plan.exe.status == "conflict" and [p.name for p in plan.exe.libs] == ["libgfortran-5.dll"]
    rn.install_exe(plan.exe, replace=True)
    assert (folder / "libgfortran-5.dll").is_file()


@pytest.mark.parametrize("in_name", ["protón.in", "simulación SOBP.in", "caña.in"])
def test_bat_names_with_accents_reach_cmd_intact(tmp_path, in_name):
    """cmd.exe lee cada línea del .bat con la página de códigos de la consola: el .bat va en UTF-8 y cambia a la
    65001 en la segunda línea (la primera, solo ASCII)."""
    raw = rn.write_launcher(tmp_path, "bat", in_name, "penhmain.exe").read_bytes()
    lines = raw.split(b"\r\n")
    assert lines[0] == b"@echo off" and lines[1] == b"chcp 65001 >nul"
    assert f'"penhmain.exe" < "{in_name}"'.encode() in lines
    assert not raw.startswith(b"\xef\xbb\xbf")  # sin BOM: rompería «@echo off»


def test_bat_special_characters_and_exit_code():
    """«&», «|», «^», «%» en el nombre del .in no se toman por órdenes de cmd (title, echo); el guion dice si penhmain
    ha acabado con un error, sin bloques if con paréntesis."""
    bat = rn.launcher_text("bat", "E1&E2 (100%).in", "penhmain.exe")
    title = next(ln for ln in bat.split("\r\n") if ln.startswith("title "))
    assert title == "title ARGUS - E1^&E2 (100%%).in"
    assert '"penhmain.exe" < "E1&E2 (100%%).in"' in bat
    assert "set ARGUS_RC=%errorlevel%" in bat and 'if not "%ARGUS_RC%"=="0" goto argus_error' in bat
    assert rn.RUN_OUTPUT not in bat  # con un penhmain de Windows, la salida solo se ve en la consola


@shell
def test_the_sh_launcher_reports_the_exit_code_in_penhmain_out(tmp_path):
    """Si penhmain no arranca (falta libgfortran en WSL…), el guion lo dice y ARGUS lo ve en penhmain.out."""
    import subprocess

    exe = tmp_path / "penhmain.x"
    exe.write_text("#!/bin/sh\necho './penhmain.x: error while loading shared libraries: libgfortran.so.5: cannot "
                   "open shared object file: No such file or directory' >&2\nexit 127\n")
    exe.chmod(0o755)
    (tmp_path / "caso.in").write_text("TITLE prueba\n")
    script = rn.write_launcher(tmp_path, "sh", "caso.in", "penhmain.x")
    r = subprocess.run(["sh", str(script)], input=b"\n", capture_output=True, timeout=30)
    out = (tmp_path / rn.RUN_OUTPUT).read_text()
    assert f"{rn.ERROR_MARK} 127" in out
    assert "con un error (codigo 127)" in r.stdout.decode()
    assert "libgfortran.so.5" in log_error(out)


def test_a_launcher_edited_by_the_user_is_kept(tmp_path):
    p = rn.write_launcher(tmp_path, "sh", "caso.in", "penhmain.x")
    mine = p.read_text().replace("chmod +x", "ulimit -s unlimited\nchmod +x", 1)
    p.write_text(mine)
    p2 = rn.write_launcher(tmp_path, "sh", "otro.in", "penhmain.x")
    assert p2 == p and "otro.in" in p2.read_text()
    assert "ulimit" in (tmp_path / (p.name + ".bak")).read_text()
    # Uno de ARGUS sin retocar (el de otro .in) se sustituye sin copia.
    rn.write_launcher(tmp_path, "sh", "caso.in", "penhmain.x")
    assert not (tmp_path / (p.name + ".2.bak")).exists()


def _running(tmp_path: Path, dumpp: int, age: float) -> tuple[Path, Path]:
    """El caso «caja» a mitad de una simulación larga: sin terminar, DUMPP ``dumpp`` s y lo último escrito hace
    ``age`` s."""
    in_path, _doc = _case(tmp_path)
    folder = in_path.parent
    text = in_path.read_text(encoding="latin-1")
    text = re.sub(r"(?m)^NSIMSH .*$", "NSIMSH 2e9", text)
    text = re.sub(r"(?m)^DUMPP .*$", f"DUMPP  {dumpp}", text)
    in_path.write_text(text, encoding="latin-1")
    shutil.copy(CAJA / "penhmain-res.dat", folder / "penhmain-res.dat")
    log = (CAJA / "penhmain.dat").read_text(encoding="latin-1").split("***  END  ***")[0]
    (folder / "penhmain.dat").write_text(log, encoding="latin-1")
    t = time.time() - age
    for p in folder.iterdir():
        os.utime(p, (t, t))
    return in_path, folder


@pytest.mark.parametrize("dumpp,age,running", [(300, 200.0, True), (600, 400.0, True), (30, 60.0, True),
                                               (300, 1000.0, False)])
def test_a_running_penhmain_is_detected_with_its_dump_period(tmp_path, dumpp, age, running):
    in_path, folder = _running(tmp_path, dumpp, age)
    assert (ResultsFolder(folder, in_path).state().status == "simulando") == running
    assert (rn.running_state(folder, in_path=in_path) is not None) == running


def test_network_folders_with_wsl(monkeypatch):
    """Una letra de unidad de red sirve en la consola de Windows pero no con WSL (solo monta los discos)."""
    assert rn.windows_path_problem("\\\\servidor\\casos\\SOBP") == "carpeta de red"
    assert rn.windows_path_problem("C:\\Users\\Rafa\\Desktop\\SOBP_Investigacion") is None


def test_log_error_recognises_programs_that_do_not_start():
    assert "libgfortran.so.5" in log_error("x: error while loading shared libraries: libgfortran.so.5: cannot open")
    assert "permiso" in log_error(f"sh: ./penhmain.x: Permission denied\n{rn.ERROR_MARK} 126\n")
    assert "código de salida 2" in log_error(f"algo\n{rn.ERROR_MARK} 2\n")
    assert "otro procesador" in log_error("Illegal instruction (core dumped)\n")
    assert log_error("   *** END ***\n") is None


def test_copy_needed_raises_when_cancelled_or_failed(tmp_path, monkeypatch):
    from argus.core.files import CopyCancelled
    from argus.penhan import simfolder

    in_path, doc = _case(tmp_path)
    plan = rn.plan_run(in_path, doc, launch_fn=native)
    item = simfolder.Item("endf", "pO16_ICRU63.endf", tmp_path / "pO16_ICRU63.endf", required=True, status="copy")
    item.source.write_text("ENDF\n")
    plan.files.items.append(item)

    def fail(*_a, **_k):
        raise OSError(errno.ENOSPC, "No queda espacio en el dispositivo")

    monkeypatch.setattr(simfolder, "_copy_new", fail)
    with pytest.raises(OSError, match="pO16_ICRU63.endf"):
        rn.copy_needed(plan)
    item.status = "copy"
    with pytest.raises(CopyCancelled):
        rn.copy_needed(plan, cancel=lambda: True)


# -- el diálogo -------------------------------------------------------------------------------------------------------

pytest.importorskip("PySide6")
import test_run_ui as _ui  # noqa: E402
from test_cascade import FAKE  # noqa: E402
from test_run_ui import _dialog, _headers  # noqa: E402

settings = _ui.settings  # las mismas preparaciones (fixtures) que las pruebas del diálogo
started = _ui.started
window = _ui.window


def _proton_case(tmp_path, settings):
    """El caso «caja» con protones: su .mat cita un ENDF que está en la base de datos (se copia al ejecutar)."""
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
    return in_path


@shell
def test_cancel_while_copying_does_not_run_penhmain(qtbot, tmp_path, settings, started, monkeypatch):
    from argus.core.files import CopyCancelled
    from argus.penhan import simfolder

    in_path = _proton_case(tmp_path, settings)
    real = simfolder._copy_new

    def slow_copy(src, target, *, executable=False, cancel=None):
        for _ in range(100):
            if cancel is not None and cancel():
                raise CopyCancelled
            time.sleep(0.02)
        return real(src, target, executable=executable, cancel=cancel)

    monkeypatch.setattr(simfolder, "_copy_new", slow_copy)
    for n in ("penhmain-res.dat", "penhmain.dat"):
        shutil.copy(CAJA / n, in_path.parent / n)
    dlg, _ctrl = _dialog(qtbot, in_path, settings)
    dlg.show()
    assert dlg.go.isEnabled(), dlg.status.text()
    dlg.go.click()
    assert dlg._copier is not None
    dlg.reject()  # «Cancelar», Esc o la X: se cancela la copia y, al acabar, se cierra
    qtbot.waitUntil(lambda: dlg._copier is None, timeout=15000)
    assert started == [] and dlg.stash is None and dlg.handle is None and not dlg.isVisible()


@shell
def test_a_copy_that_fails_is_reported_and_penhmain_is_not_run(qtbot, tmp_path, settings, started, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.penhan import simfolder

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]) or QMessageBox.StandardButton.Ok)
    in_path = _proton_case(tmp_path, settings)

    def failing_copy(src, target, *, executable=False, cancel=None):
        raise OSError(errno.ENOSPC, "No queda espacio en el dispositivo")

    monkeypatch.setattr(simfolder, "_copy_new", failing_copy)
    dlg, _ctrl = _dialog(qtbot, in_path, settings)
    dlg.go.click()
    qtbot.waitUntil(lambda: dlg._copier is None, timeout=15000)
    assert started == [] and "pO16_ICRU63.endf" in dlg.status.text() and warnings


@shell
@pytest.mark.parametrize("how", ["carpeta", "elegido"])
def test_the_cascade_uses_the_penhmain_of_the_run(qtbot, window, tmp_path, started, monkeypatch, how):
    """La cascada se simula con el mismo penhmain que la simulación: el de la carpeta (lo que viene marcado en un
    conflicto) o el que se elige en el propio diálogo."""
    from argus.penhan import cascade as cs
    from argus.ui.run_dialog import RunDialog

    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    other = None
    if how == "carpeta":
        folder_exe = in_path.parent / "penhmain.x"
        folder_exe.write_text(FAKE + "# otra compilación\n")
        folder_exe.chmod(0o755)
    else:
        other = tmp_path / "otros" / "penhmain2.x"
        other.parent.mkdir()
        other.write_text(FAKE + "# otra versión\n")
        other.chmod(0o755)
    w = window
    w.open_file(in_path, ask=False)
    qtbot.waitUntil(lambda: w.input is not None, timeout=30000)

    def accept(self):
        if other is not None:
            self.exe.setText(str(other))
            self.refresh()
        else:
            assert self.use_folder.isChecked() and self.conflict.isVisibleTo(self)
        self.go.click()
        return self.result()

    monkeypatch.setattr(RunDialog, "exec", accept)
    w.act_run.trigger()
    assert len(started) == 1
    job = cs.active_job(in_path)
    assert job is not None and os.path.samefile(job.exe, started[0].exe.target)
    cs.cancel_all()


@shell
def test_conflict_problems_are_those_of_the_exe_that_will_run(qtbot, tmp_path, settings, started):
    from argus.ui.settings import PENHMAIN_KEY

    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    folder = in_path.parent
    bad = folder / "penhmain.x"  # compilado para otro procesador (ARM64)
    bad.write_bytes(b"\x7fELF\x02\x01\x01" + bytes(9) + b"\x02\x00\xb7\x00" + bytes(40))
    bad.chmod(0o755)
    dlg, _ctrl = _dialog(qtbot, in_path, settings)
    assert dlg.conflict.isVisibleTo(dlg) and dlg.use_folder.isChecked()
    assert not dlg.go.isEnabled() and "arm64" in dlg.status.text()
    dlg.replace.setChecked(True)  # el elegido sí se puede ejecutar
    assert dlg.go.isEnabled(), dlg.status.text()
    # Al revés: el elegido no se puede ejecutar y el de la carpeta sí.
    chosen = _fake_penhmain(tmp_path / "otros")
    chosen.chmod(0o644)
    settings.setValue(PENHMAIN_KEY, str(chosen))
    bad.write_text("#!/bin/sh\necho el de la carpeta\n")
    bad.chmod(0o755)
    dlg2, _ctrl2 = _dialog(qtbot, in_path, settings)
    assert dlg2.use_folder.isChecked() and dlg2.go.isEnabled(), dlg2.status.text()


@shell
def test_the_dialog_texts_about_resume_and_previous_results(qtbot, tmp_path, settings, started):
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    folder = in_path.parent
    text = in_path.read_text(encoding="latin-1").replace("DUMPTO dump.dmp", "RESUME dump1.dmp\nDUMPTO dump2.dmp")
    in_path.write_text(text, encoding="latin-1")
    for n in ("penhmain-res.dat", "penhmain.dat", "depth-dose.dat", "x-dose.dat", "y-dose.dat", "z-dose.dat",
              "gnu-dose.dat"):
        shutil.copy(CAJA / n, folder / n)
    (folder / "dump1.dmp").write_text("   Empty.\n")
    dlg, _ctrl = _dialog(qtbot, in_path, settings)
    assert "dump1.dmp está vacío: penhmain empieza desde cero" in dlg.files_state.text()
    assert dlg.prev_box.isVisibleTo(dlg)
    prev = dlg.prev_state.text()
    assert re.search(r"\d+ ficheros del .*: .* y \d+ más\.$", prev) and "…" not in prev
    assert "ya están en la carpeta" in dlg.files_state.text()


@shell
def test_a_changed_in_on_disk_is_not_run(qtbot, tmp_path, settings, started):
    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    dlg, ctrl = _dialog(qtbot, in_path, settings)
    assert dlg.go.isEnabled()
    time.sleep(0.05)
    in_path.write_text(in_path.read_text().replace("SKPAR  2", "SKPAR  9"))
    dlg.refresh()
    assert ctrl.changed_on_disk() and not dlg.go.isEnabled() and "Recargar" in dlg.status.text()
    dlg.run()
    assert started == []


@shell
def test_enter_runs_and_the_summary_lists_angular_detectors(qtbot, tmp_path, settings, started):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    in_path, _doc = _case(tmp_path)
    _headers(in_path)
    dlg, _ctrl = _dialog(qtbot, in_path, settings)
    dlg.show()
    qtbot.waitExposed(dlg)
    assert dlg.go.isDefault()
    assert "detector angular" in dlg.results.text() or "detectores angulares" in dlg.results.text()
    dlg.exe.setFocus()
    QTest.keyClick(dlg.exe, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: len(started) == 1, timeout=5000)
