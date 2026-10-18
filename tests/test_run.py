"""Ejecutar penhmain en la carpeta del .in (penhan/run.py, 0.13.0)."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from pathlib import Path

import pytest

from argus.penhan import run as rn
from argus.penhan.inputfile import parse_in_file
from argus.penhan.programs import Launch

CAJA = Path(__file__).parent / "corpus" / "resultados" / "caja"

FAKE_PENHMAIN = """#!/bin/sh
# penhmain de mentira: lee el .in de la entrada estándar y deja unos resultados.
cat > leido.in
echo "   Initialising PENELOPE ..."
echo "   The simulation is started ..."
cp "{res}" penhmain-res.dat
cp "{log}" penhmain.dat
cp "{dd}" depth-dose.dat
echo "   No. of simulated showers = 1.0000000E+02, time = 1.000E+00 s"
echo "   *** END ***"
"""


def _case(tmp_path: Path, name: str = "caso") -> tuple[Path, object]:
    d = tmp_path / name
    d.mkdir()
    shutil.copy(CAJA / "caja.in", d / "caja.in")
    shutil.copy(CAJA / "caja.geo", d / "caja.geo")
    shutil.copy(CAJA / "mcs-null.mcs", d / "mcs-null.mcs")  # la tabla de EPROF
    doc = parse_in_file(d / "caja.in")
    for md in doc.model.materials:
        (d / md.fname.strip()).write_text("material de mentira\n")
    return d / "caja.in", doc


def _fake_penhmain(where: Path, name: str = "penhmain.x") -> Path:
    where.mkdir(parents=True, exist_ok=True)
    p = where / name
    p.write_text(FAKE_PENHMAIN.format(res=CAJA / "penhmain-res.dat", log=CAJA / "penhmain.dat",
                                      dd=CAJA / "depth-dose.dat"))
    p.chmod(0o755)
    return p


NATIVE = Launch("native", "de mentira")


def native(_info):
    return NATIVE


# -- penhmain ------------------------------------------------------------------------------------------------------

def test_the_chosen_penhmain_is_copied_to_the_case_folder(tmp_path):
    in_path, doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas")
    plan = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=native)
    assert plan.exe.status == "copy"
    assert plan.exe.target == in_path.parent / "penhmain.x"
    assert plan.previous == [] and plan.resume is None and plan.running is None
    assert plan.problems == []
    copied = rn.install_exe(plan.exe)
    assert copied == [in_path.parent / "penhmain.x"]
    assert os.access(in_path.parent / "penhmain.x", os.X_OK)
    assert plan.exe.status == "same"
    # Otra vez: ya está igual, no se copia nada.
    again = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=native)
    assert again.exe.status == "same" and rn.install_exe(again.exe) == []


def test_another_penhmain_with_that_name_is_never_replaced_without_asking(tmp_path):
    in_path, doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas")
    other = in_path.parent / "penhmain.x"
    other.write_text("#!/bin/sh\necho otro\n")
    other.chmod(0o755)
    plan = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=native)
    assert plan.exe.status == "conflict"
    assert rn.install_exe(plan.exe) == []  # sin decir nada, no se toca
    assert other.read_text() == "#!/bin/sh\necho otro\n"
    # «Usar el de la carpeta»
    use = rn.use_folder_exe(plan.exe, native)
    assert use.status == "inside" and use.target == other
    # «Sustituirlo»: el de antes se queda como .bak (y si ya había un .bak, como .bak2)
    (in_path.parent / "penhmain.x.bak").write_text("copia del usuario")
    rn.install_exe(plan.exe, replace=True)
    assert other.read_text() == exe.read_text()
    assert (in_path.parent / "penhmain.x.bak").read_text() == "copia del usuario"
    assert (in_path.parent / "penhmain.x.2.bak").read_text() == "#!/bin/sh\necho otro\n"


def test_without_a_chosen_penhmain_the_one_in_the_folder_is_used(tmp_path):
    in_path, doc = _case(tmp_path)
    assert rn.plan_run(in_path, doc, launch_fn=native).exe.status == "none"
    assert "elegir" in rn.plan_run(in_path, doc, launch_fn=native).problems[0]
    elf = in_path.parent / "penhmain.x"  # como el de Rafael: un ejecutable de Linux (aquí no hace falta que funcione)
    elf.write_bytes(b"\x7fELF\x02\x01\x01" + bytes(9) + b"\x02\x00\x3e\x00" + bytes(40))
    elf.chmod(0o755)
    plan = rn.plan_run(in_path, doc, launch_fn=native)
    assert plan.exe.status == "inside" and plan.exe.from_folder
    assert plan.exe.info.system == "linux"
    assert plan.exe.target == in_path.parent / "penhmain.x"
    # penhmain-sum y penhmain-layout no son penhmain
    d2 = tmp_path / "otro"
    d2.mkdir()
    (d2 / "penhmain-sum.x").write_bytes(b"\x7fELF" + bytes(60))
    assert rn.folder_penhmain(d2) == []


def test_a_missing_chosen_penhmain_is_a_problem(tmp_path):
    in_path, doc = _case(tmp_path)
    plan = rn.plan_run(in_path, doc, penhmain=tmp_path / "no-existe.x", launch_fn=native)
    assert plan.exe.status == "missing"
    assert any("no se encuentra penhmain" in p for p in plan.problems)


def test_a_penhmain_that_cannot_run_here_is_a_problem(tmp_path):
    in_path, doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas")
    plan = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=lambda _i: Launch("", "es de otro sistema"))
    assert not plan.exe.runnable
    assert any("es de otro sistema" in p for p in plan.problems)


def test_missing_materials_are_reported_apart(tmp_path):
    in_path, doc = _case(tmp_path)
    mat = doc.model.materials[0].fname.strip()
    (in_path.parent / mat).unlink()
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "p"), launch_fn=native)
    assert [it.name for it in plan.missing_mats] == [mat]
    assert plan.problems == []  # los .mat se pueden generar


# -- resultados anteriores ----------------------------------------------------------------------------------------

def _copy_outputs(folder: Path) -> list[str]:
    names = []
    for p in CAJA.iterdir():
        if p.suffix.lower() in (".in", ".geo", ".mcs"):
            continue
        shutil.copy(p, folder / p.name)
        names.append(p.name)
    return names


def test_previous_results_are_found_and_kept_in_a_subfolder(tmp_path):
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    _copy_outputs(folder)
    (folder / "dump.dmp").write_text("volcado")
    (folder / "notas.txt").write_text("mis notas")  # no es de penhmain: no se mueve
    old = time.time() - 3600
    for p in folder.iterdir():
        os.utime(p, (old, old))
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "p"), launch_fn=native)
    names = {p.name for p in plan.previous}
    assert {"penhmain-res.dat", "penhmain.dat", "depth-dose.dat", "electron.trk", "geometry.rep"} <= names
    assert not names & {"caja.in", "caja.geo", "notas.txt"}
    assert all(not n.endswith(".mat") for n in names)
    assert plan.running is None  # hace una hora: no está simulando
    dest = rn.stash_outputs(folder, plan.previous)
    assert dest.parent == folder and dest.name == "anterior-" + time.strftime("%Y-%m-%d_%H-%M-%S",
                                                                              time.localtime(old))
    assert rn.is_stash(dest)
    assert (dest / "penhmain-res.dat").is_file() and not (folder / "penhmain-res.dat").exists()
    assert (folder / "notas.txt").is_file() and (folder / "caja.in").is_file()
    # La carpeta de los anteriores no es otra ejecución del caso para el visor de resultados.
    from argus.results.folder import ResultsFolder

    rf = ResultsFolder(folder)
    assert rf.runs == [] and rf.files == []
    # Otra vez con los mismos nombres y la misma fecha: otra subcarpeta, nada se sobrescribe.
    _copy_outputs(folder)
    dest2 = rn.stash_outputs(folder, rn.previous_outputs(folder, doc), when=old)
    assert dest2.name == dest.name + "-2" and (dest / "penhmain-res.dat").is_file()
    assert (dest2 / "penhmain-res.dat").is_file()


def test_inputs_are_never_taken_for_previous_results(tmp_path):
    """Un espacio de fases de una simulación anterior que este .in usa como fuente (IPSFN) es una entrada."""
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    shutil.copy(CAJA / "psf-impdet.dat", folder / "fuente-psf.dat")
    shutil.copy(CAJA / "penhmain-res.dat", folder / "penhmain-res.dat")
    text = in_path.read_text(encoding="latin-1")
    text = text.replace("SKPAR ", "IPSFN  fuente-psf.dat\r\nSKPAR ", 1) if "SKPAR " in text else text
    in_path.write_text(text, encoding="latin-1")
    doc2 = parse_in_file(in_path)
    plan = rn.plan_run(in_path, doc2, penhmain=_fake_penhmain(tmp_path / "p"), launch_fn=native)
    names = {p.name for p in plan.previous}
    assert "penhmain-res.dat" in names
    if doc2.model.source.psf:
        assert "fuente-psf.dat" not in names


def test_with_resume_and_its_dump_nothing_is_asked(tmp_path):
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    _copy_outputs(folder)
    from test_simcheck import dump_text

    (folder / "dump.dmp").write_text(dump_text())  # un volcado de penhmain que se puede seguir
    text = in_path.read_text(encoding="latin-1").rstrip("\r\n") + "\r\nRESUME dump.dmp\r\n"
    in_path.write_text(text, encoding="latin-1")
    doc2 = parse_in_file(in_path)
    plan = rn.plan_run(in_path, doc2, penhmain=_fake_penhmain(tmp_path / "p"), launch_fn=native)
    assert plan.resume == "dump.dmp" and plan.previous == []
    (folder / "dump.dmp").unlink()
    plan = rn.plan_run(in_path, doc2, penhmain=_fake_penhmain(tmp_path / "p"), launch_fn=native)
    assert plan.resume is None and plan.resume_missing == "dump.dmp"
    assert plan.previous  # sin volcado empieza de cero: los resultados de antes se sobrescribirían


def test_a_simulation_running_in_the_folder_is_detected(tmp_path):
    in_path, doc = _case(tmp_path)
    folder = in_path.parent
    log = (CAJA / "penhmain.dat").read_text(encoding="latin-1")
    (folder / "penhmain.dat").write_text(log.split("***  END  ***")[0], encoding="latin-1")
    (folder / "penhmain.out").write_text("   Initialising PENELOPE ...\n")
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "p"), launch_fn=native)
    assert plan.running is not None


# -- el guion y el terminal -----------------------------------------------------------------------------------------

def test_launcher_texts():
    sh = rn.launcher_text("sh", "mi caso.in", "penhmain.x")
    assert sh.startswith("#!/bin/sh\n") and "\r" not in sh
    assert "./'penhmain.x' < 'mi caso.in' 2>&1" in sh and "} | tee penhmain.out" in sh
    assert "GFORTRAN_UNBUFFERED_PRECONNECTED=y" in sh and "read dummy" in sh
    assert all(ord(c) < 128 for line in sh.splitlines() if not line.startswith("#") for c in line)
    bat = rn.launcher_text("bat", "caso 100%.in", "penhmain.exe")
    assert '"penhmain.exe" < "caso 100%%.in"' in bat and "pause" in bat and "\r\n" in bat
    assert rn.launcher_kind(None, "windows") == "bat"
    assert rn.launcher_kind(type("I", (), {"system": "linux"})(), "windows") == "sh"
    assert rn.launcher_kind(type("I", (), {"system": "macos"})(), "macos") == "command"


def test_the_launcher_of_argus_is_rewritten_but_not_a_user_file(tmp_path):
    p = rn.write_launcher(tmp_path, "sh", "a.in", "penhmain.x")
    assert p.name == "argus-ejecutar.sh" and os.access(p, os.X_OK) and rn.is_launcher(p)
    p2 = rn.write_launcher(tmp_path, "sh", "b.in", "penhmain.x")
    assert p2 == p and "b.in" in p.read_text()
    user = tmp_path / "argus-ejecutar.bat"
    user.write_bytes(b"@echo off\r\necho mio\r\n")
    p3 = rn.write_launcher(tmp_path, "bat", "a.in", "penhmain.exe")
    assert p3.name == "argus-ejecutar-2.bat" and user.read_bytes() == b"@echo off\r\necho mio\r\n"


def test_terminal_commands_for_each_system(tmp_path):
    script = tmp_path / "argus-ejecutar.sh"
    t = rn.terminal_command(script, Launch("wsl", ""), host="windows")
    assert t.argv[1:] == ["--cd", str(tmp_path), "-e", "sh", "argus-ejecutar.sh"] and t.new_console
    t = rn.terminal_command(tmp_path / "argus-ejecutar.bat", Launch("native", ""), host="windows")
    assert t.argv == ["cmd.exe", "/c", "argus-ejecutar.bat"] and t.new_console and t.cwd == tmp_path
    t = rn.terminal_command(tmp_path / "argus-ejecutar.command", Launch("native", ""), host="macos")
    assert t.argv == ["open", "-a", "Terminal", str(tmp_path / "argus-ejecutar.command")]
    have = {"konsole": "/usr/bin/konsole", "xterm": "/usr/bin/xterm"}
    t = rn.terminal_command(script, NATIVE, host="linux", which=have.get, env={})
    assert t.argv == ["/usr/bin/konsole", "-e", "sh", str(script)] and t.name == "konsole"
    t = rn.terminal_command(script, NATIVE, host="linux", which=have.get, env={"TERMINAL": "xterm"})
    assert t.argv[0] == "/usr/bin/xterm"
    t = rn.terminal_command(script, NATIVE, host="linux", which=lambda _n: None, env={})
    assert t.background and t.argv == ["sh", str(script)]


@pytest.mark.skipif(sys.platform == "win32", reason="penhmain de mentira con /bin/sh")
def test_start_runs_penhmain_in_a_terminal_and_the_results_appear(tmp_path):
    """De principio a fin con un penhmain y un terminal de mentira (un guion que ejecuta lo que le pasan)."""
    in_path, doc = _case(tmp_path)
    exe = _fake_penhmain(tmp_path / "programas")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    term = bindir / "xterm"
    term.write_text('#!/bin/sh\n[ "$1" = "-e" ] && shift\nexec "$@" </dev/null\n')
    term.chmod(0o755)
    plan = rn.plan_run(in_path, doc, penhmain=exe, launch_fn=native)
    rn.install_exe(plan.exe)
    handle = rn.start(plan, host="linux", which=lambda n: str(term) if n == "xterm" else None, env={})
    assert handle.terminal.name == "xterm"
    handle.process.wait(timeout=30)
    folder = in_path.parent
    assert (folder / "penhmain-res.dat").is_file() and (folder / "depth-dose.dat").is_file()
    assert (folder / "leido.in").read_bytes() == in_path.read_bytes()  # penhmain leyó el .in por la entrada
    out = (folder / "penhmain.out").read_text()
    assert "*** END ***" in out
    from argus.results.folder import ResultsFolder

    st = ResultsFolder(folder).state()
    assert st.status in ("terminada", "resultados", "parada")


@pytest.mark.skipif(sys.platform == "win32", reason="penhmain de mentira con /bin/sh")
def test_without_a_terminal_it_runs_in_the_background(tmp_path):
    in_path, doc = _case(tmp_path)
    plan = rn.plan_run(in_path, doc, penhmain=_fake_penhmain(tmp_path / "p"), launch_fn=native)
    rn.install_exe(plan.exe)
    handle = rn.start(plan, host="linux", which=lambda _n: None, env={})
    assert handle.terminal.background
    handle.process.wait(timeout=30)
    assert (in_path.parent / "penhmain-res.dat").is_file()
    assert "*** END ***" in (in_path.parent / "penhmain.out").read_text()


def test_the_screen_output_is_recognised_from_the_start(tmp_path):
    from argus.results.catalog import identify

    p = tmp_path / "penhmain.out"
    p.write_text("   Initialising PENELOPE ...\n")
    assert identify(p).kind == "run-log"


@pytest.mark.skipif(not os.environ.get("ARGUS_PENHMAIN"), reason="ARGUS_PENHMAIN: un penhmain de verdad")
def test_a_real_penhmain_runs_from_its_case_folder(tmp_path):
    """Con el penhmain de verdad (ARGUS_PENHMAIN) y los .mat de ARGUS_PENHMAIN_MATS (carpeta con agua.mat…): una
    simulación corta del caso «caja» en su carpeta, en segundo plano."""
    mats = os.environ.get("ARGUS_PENHMAIN_MATS")
    if not mats:
        pytest.skip("ARGUS_PENHMAIN_MATS: carpeta con los .mat del caso caja")
    in_path, doc = _case(tmp_path)
    for md in doc.model.materials:
        src = Path(mats) / md.fname.strip()
        if not src.is_file():
            pytest.skip(f"falta {src}")
        shutil.copy(src, in_path.parent / md.fname.strip())
    from argus.penhan import sections as sc
    from argus.penhan.inputfile import write_in_file

    sc.set_value(doc, "NSIMSH", [200])
    sc.set_value(doc, "TIME", [60])
    sc.set_value(doc, "DUMPP", None)
    sc.set_text(doc, "DUMPTO", None)
    write_in_file(doc, in_path)
    doc = parse_in_file(in_path)
    plan = rn.plan_run(in_path, doc, penhmain=os.environ["ARGUS_PENHMAIN"])
    assert plan.problems == []
    rn.install_exe(plan.exe)
    handle = rn.start(plan, which=lambda _n: None, env={})
    handle.process.wait(timeout=300)
    assert "*** END ***" in (in_path.parent / "penhmain.out").read_text()
    assert stat.S_IMODE(os.stat(plan.exe.target).st_mode) & 0o100
