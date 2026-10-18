"""Los programas de penhan del usuario (ADR-013): reconocer penhmat y penhmain de Windows, Linux o macOS, cómo se
ejecutan aquí (directamente o con WSL), y generar con penhmat los .mat que faltan y copiar los ENDF que necesitan.

Casi todas las pruebas usan un penhmat de mentira (un guion de Python que contesta como penhmat); la última usa el
penhmat real si está (``ARGUS_PENHMAT`` y ``ARGUS_PENHAN_DB``)."""

import os
import shutil
import struct
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from argus.penhan import matgen as mg
from argus.penhan import programs as pg
from argus.penhan.inputfile import parse_in_text
from argus.penhan.materials import read_mat_header, read_mat_nuclear

POSIX = pytest.mark.skipif(os.name == "nt", reason="el penhmat de mentira es un guion con #! (Linux o macOS)")


# ---------------------------------------------------------------------------------------------------------------------
# Ejecutables de mentira (solo la cabecera)
# ---------------------------------------------------------------------------------------------------------------------

def _pe(machine=0x8664, at=0x80) -> bytes:
    head = bytearray(max(at + 64, 0x200))
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, at)
    head[at:at + 4] = b"PE\0\0"
    struct.pack_into("<H", head, at + 4, machine)
    return bytes(head)


def _elf(machine=0x3E, big=False) -> bytes:
    head = bytearray(64)
    head[:4] = b"\x7fELF"
    head[4] = 2
    head[5] = 2 if big else 1
    struct.pack_into((">" if big else "<") + "H", head, 18, machine)
    return bytes(head)


def _macho(cpu=0x0100000C) -> bytes:
    return b"\xcf\xfa\xed\xfe" + struct.pack("<I", cpu) + bytes(56)


def _fat(*cpus) -> bytes:
    out = b"\xca\xfe\xba\xbe" + struct.pack(">I", len(cpus))
    for c in cpus:
        out += struct.pack(">IIIII", c, 0, 0, 0, 0)
    return out + bytes(32)


def _write(path: Path, data: bytes, executable=True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if executable:
        os.chmod(path, 0o755)
    return path


@pytest.mark.parametrize("data, system, arch", [
    (_pe(), "windows", "x86-64"), (_pe(0xAA64), "windows", "arm64"), (_pe(0x14C, at=0x2000), "windows", "x86"),
    (_elf(), "linux", "x86-64"), (_elf(0xB7), "linux", "arm64"), (_elf(0x15, big=True), "linux", "ppc64"),
    (_macho(), "macos", "arm64"), (_macho(0x01000007), "macos", "x86-64"),
    (_fat(0x01000007, 0x0100000C), "macos", "universal (x86-64, arm64)"),
    (b"#!/bin/sh\necho hola\n", "script", ""),
])
def test_executables_are_recognised_by_their_content(tmp_path, data, system, arch):
    info = pg.exe_info(_write(tmp_path / "prog.x", data))
    assert (info.system, info.arch) == (system, arch)


def test_what_is_not_an_executable(tmp_path):
    assert pg.exe_info(tmp_path / "nada").problem == "no existe"
    assert pg.exe_info(tmp_path).problem == "es una carpeta"
    assert pg.exe_info(_write(tmp_path / "t.txt", b"hola\n")).system is None
    java = b"\xca\xfe\xba\xbe" + struct.pack(">I", 52) + bytes(16)  # un .class de Java empieza igual
    assert pg.exe_info(_write(tmp_path / "A.class", java)).system is None


def test_how_each_executable_runs_on_each_system(tmp_path):
    lin = pg.exe_info(_write(tmp_path / "penhmat.x", _elf()))
    win = pg.exe_info(_write(tmp_path / "penhmat.exe", _pe()))
    mac = pg.exe_info(_write(tmp_path / "penhmat_mac", _macho(0x01000007)))
    ok = lambda: pg.WslStatus(True, "Ubuntu 24.04")  # noqa: E731
    no = lambda: pg.WslStatus(False, "WSL (el Linux de Windows) no está instalado")  # noqa: E731
    assert pg.launch_for(lin, host="linux", arch="x86-64").how == "native"
    assert pg.launch_for(win, host="windows", arch="x86-64").how == "native"
    assert pg.launch_for(lin, host="windows", arch="x86-64", wsl=ok) == pg.Launch(
        "wsl", "ejecutable de Linux (x86-64): se ejecuta con WSL (Ubuntu 24.04)")
    none = pg.launch_for(lin, host="windows", arch="x86-64", wsl=no)
    assert not none.ok and "wsl --install" in none.note and "no está instalado" in none.note
    assert not pg.launch_for(win, host="linux", arch="x86-64").ok
    assert "macOS" in pg.launch_for(lin, host="macos", arch="arm64").note
    assert pg.launch_for(mac, host="macos", arch="arm64").ok  # Rosetta 2
    assert not pg.launch_for(lin, host="linux", arch="arm64").ok  # x86-64 en un Linux ARM
    assert not pg.launch_for(pg.exe_info(_write(tmp_path / "arm.exe", _pe(0xAA64))), host="windows",
                             arch="x86-64").ok
    os.chmod(lin.path, 0o644)
    if os.name != "nt":
        assert "chmod +x" in pg.launch_for(lin, host="linux", arch="x86-64").note


def test_wsl_commands_need_no_quoting(tmp_path):
    db = tmp_path / "penhan con espacios" / "database"
    wsl = pg.Launch("wsl", "")
    inside = pg.command(db / "penhmat.x", db, wsl)
    assert inside[1:] == ["--cd", str(db), "-e", "./penhmat.x"]
    other = pg.command(tmp_path / "otra" / "penhmat.x", db, wsl, to_wsl=lambda p: "/mnt/c/otra/penhmat.x")
    assert other[1:] == ["--cd", str(db), "-e", "/mnt/c/otra/penhmat.x"]
    assert pg.command(db / "penhmat.exe", db, pg.Launch("native", "")) == [str(db / "penhmat.exe")]
    with pytest.raises(ValueError):
        pg.command(db / "x", db, pg.Launch("", "no"))
    assert pg.wsl_style(r"C:\Users\Rafa\SOBP Inv\caso") == "/mnt/c/Users/Rafa/SOBP Inv/caso"


def test_output_of_wsl_and_of_programs_is_decoded():
    assert pg.decode_output("No hay distribuciones".encode("utf-16-le")) == "No hay distribuciones"
    assert pg.decode_output("Densidad: 1,0 g/cm³".encode()) == "Densidad: 1,0 g/cm³"
    assert pg.decode_output("año".encode("cp1252")) == "año"


def test_how_to_launch_penhmain(tmp_path):
    lin = pg.exe_info(_write(tmp_path / "penhmain.x", _elf()))
    win = pg.exe_info(_write(tmp_path / "penhmain.exe", _pe()))
    assert pg.run_hint(win, "caso.in", host="windows") == ("en cmd: «penhmain < caso.in»; en PowerShell: «cmd /c "
                                                           "\"penhmain < caso.in\"»")
    hint = pg.run_hint(lin, "caso.in", r"C:\Users\Rafa\caso_sim", host="windows")
    assert "WSL" in hint and 'cd "/mnt/c/Users/Rafa/caso_sim" && ./penhmain.x < caso.in' in hint
    assert pg.run_hint(lin, "caso.in", host="linux") == "en un terminal: «./penhmain.x < caso.in»"


def _penhan_tree(root: Path) -> Path:
    db = root / "penhan" / "database"
    for sub in pg.DATABASE_PARTS:
        (db / sub).mkdir(parents=True)
    (db / "material-list.txt").write_text("  1  Hydrogen\n278  Water, liquid\n")
    return db


def test_the_penhan_folder_and_its_programs_are_found(tmp_path):
    db = _penhan_tree(tmp_path)
    progs = db.parent / "programs"
    _write(db / "penhmat.x", _elf())
    _write(progs / "penhmain.x", _elf())
    _write(progs / "penhmain-sum.x", _elf())  # no es penhmain
    (progs / "penhmain.f").write_text("      PROGRAM PENHMAIN\n")
    (progs / "penhmat.f").write_text("      INCLUDE 'penelope.f'\n")
    for start in (db.parent, db, db / "pdfiles"):
        assert pg.database_folder(start) == db
    assert pg.find_penhmat(db.parent) == db / "penhmat.x"
    assert pg.find_penhmain(db.parent) == progs / "penhmain.x"
    assert pg.database_problems(db) == []
    shutil.rmtree(db / "pdhan")
    assert "pdhan" in pg.database_problems(db)[0]


# ---------------------------------------------------------------------------------------------------------------------
# penhmat de mentira: contesta como penhmat y escribe un .mat con la cabecera y la lista de ENDF
# ---------------------------------------------------------------------------------------------------------------------

FAKE = textwrap.dedent('''\
    #!{python}
    import os, sys, time
    answers = [a.strip() for a in sys.stdin.read().split("\\n")]
    mode = open("fake-mode.txt").read().strip() if os.path.exists("fake-mode.txt") else "ok"
    with open("fake-answers.txt", "a") as f:
        f.write("|".join(answers) + "\\n")
    print(" Select one option (1 or 2):")
    if mode.startswith("slow"):
        time.sleep(30)
    if mode == "iretrn":
        print("This element has been declared twice.")
        print("IRETRN = 1101")
        sys.exit(0)
    if mode == "crash":
        print("At line 1822 of file penelope.f")
        print("Fortran runtime error: End of file")
        sys.exit(2)
    if mode == "nofile":
        sys.exit(0)
    out = [a for a in answers if a][-1]
    if mode.startswith("race:"):
        open(mode[5:], "w").write("otro")
    with open(out, "w") as f:
        if mode == "garbage":
            f.write("hola\\n")
        else:
            name = answers[1] if answers[0] == "1" else "WATER, LIQUID  (" + answers[1] + ")"
            dens = answers[-5] if answers[0] == "1" else "9.98210000E-01"
            f.write(" PENELOPE (v. 2024)  Material data file ...............\\n")
            f.write(" Material: " + name + "\\n")
            f.write(" Mass density = " + dens + " g/cm**3\\n tablas\\n")
            f.write(" *** ENDF filenames and abundances of isotopes,  NISOT =   2\\n")
            f.write(" pO16_ICRU63.endf            8016 9.97570E-01\\n")
            f.write(" pO17_TENDL.endf             8017 3.80000E-04\\n")
            f.write("   0 = NALPHA\\n")
    print(" Press enter to continue ...")
''')

IN = """TITLE  Prueba de materiales
SKPAR  4              [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]
SENERG 1.0e8          [Initial energy (monoenergetic sources only)]
SPOSIT 0 0 -10        [Coordinates of the source]
SCONE  0 0 0          [Conical beam; angles in deg]
MFNAME agua.mat                       [Water, liquid]
MSIMPA 1.0e3 1.0e3 1.0e3 0.1 0.1 1e3 1e3
MFNAME raro.mat                       [Mi material]
MSIMPA 1.0e3 1.0e3 1.0e3 0.1 0.1 1e3 1e3
MFNAME sub/otro.mat                   [Water, liquid]
MSIMPA 1.0e3 1.0e3 1.0e3 0.1 0.1 1e3 1e3
MFNAME agua.mat                       [Water, liquid]
MSIMPA 1.0e3 1.0e3 1.0e3 0.1 0.1 1e3 1e3
GEOMFN caso.geo                       [Geometry file, up to 20 chars]
NSIMSH 1.0e3          [Desired number of simulated showers]
"""


@pytest.fixture
def fake(tmp_path):
    """(database, penhmat de mentira, carpeta del caso, .in)."""
    db = _penhan_tree(tmp_path)
    for f in ("pO16_ICRU63.endf", "pO17_TENDL.endf"):
        (db / "ENDF_data" / f).write_text(f"datos ENDF {f}\n")
    exe = _write(db / "penhmat.py", FAKE.format(python=sys.executable).encode())
    case = tmp_path / "caso"
    case.mkdir()
    (case / "caso.in").write_text(IN, encoding="latin-1")
    return db, exe, case, parse_in_text(IN)


def _mode(db: Path, mode: str) -> None:
    (db / "fake-mode.txt").write_text(mode)


def test_what_can_be_generated(fake):
    db, _exe, case, doc = fake
    plan = mg.plan_materials(doc, None, case, db)
    jobs = {j.fname: j for j in plan.mats}
    assert list(jobs) == ["agua.mat", "raro.mat", "sub/otro.mat"]  # agua.mat, una vez aunque salga dos veces
    assert jobs["agua.mat"].possible and "278" in jobs["agua.mat"].what
    assert jobs["agua.mat"].answers("x.mat") == "2\n278\n2\nx.mat\n\n"
    assert not jobs["raro.mat"].possible and "no sabe de qué está hecho" in jobs["raro.mat"].reason
    assert jobs["raro.mat"].unknown and not jobs["sub/otro.mat"].unknown  # (0.16.0: «Asignar materiales…»)
    assert not jobs["sub/otro.mat"].possible and "carpeta" in jobs["sub/otro.mat"].reason
    (case / "agua.mat").write_text("ya está\n")
    assert [j.fname for j in mg.plan_materials(doc, None, case, db).mats] == ["raro.mat", "sub/otro.mat"]


def test_tac_tissues_are_generated_with_their_composition_and_voxel_density(fake, monkeypatch):
    from argus.ct.calibration import preset
    from argus.penhan import materials

    db, _exe, case, doc = fake
    t = preset("schneider5").tissues[3]
    t.voxel_density = 1.0213
    real = materials.materials_table

    def with_tissue(d, geo, folder, *rest):
        rows = real(d, geo, folder, *rest)
        rows[1].tissue = t  # raro.mat es un tejido del TAC
        return rows

    monkeypatch.setattr(mg, "materials_table", with_tissue)
    job = next(j for j in mg.plan_materials(doc, None, case, db).mats if j.fname == "raro.mat")
    assert job.possible and t.name in job.what and "1.021" in job.what
    answers = job.answers("x.mat").split("\n")
    assert answers[0] == "1" and "1.0213" in answers and answers[-3:] == ["x.mat", "", ""]


@POSIX
def test_generate_puts_the_mat_and_its_endf_beside_the_in(fake):
    db, exe, case, doc = fake
    plan = mg.plan_materials(doc, None, case, db)
    launch = pg.launch_for(pg.exe_info(exe))
    assert launch.how == "native"
    steps = []
    mg.generate(plan, exe, launch, db, doc=doc, progress=lambda a, b, t: steps.append(t))
    job = plan.mats[0]
    assert job.status == "done" and job.target == case / "agua.mat"
    head = read_mat_header(case / "agua.mat")
    assert head.penelope_id == 278
    assert {e.name: e.status for e in plan.endf} == {"pO16_ICRU63.endf": "done", "pO17_TENDL.endf": "done"}
    assert (case / "pO17_TENDL.endf").read_text() == "datos ENDF pO17_TENDL.endf\n"
    assert "penhmat: agua.mat" in steps
    # En la carpeta database solo queda lo que había (el temporal se borra).
    assert not [p.name for p in db.iterdir() if p.name.startswith(mg.TEMP_PREFIX)]
    answers = (db / "fake-answers.txt").read_text().split("|")
    assert answers[:3] == ["2", "278", "2"] and answers[3].startswith(mg.TEMP_PREFIX)  # sin ruta: vale en WSL
    assert mg.summary(plan)[:2] == ["Generado con penhmat: agua.mat.", "Copiados 2 ficheros ENDF."]


@POSIX
@pytest.mark.parametrize("mode, text", [("iretrn", "declared twice. (IRETRN 1101)"),
                                        ("crash", "End of file"), ("nofile", "no ha escrito el .mat"),
                                        ("garbage", "no es un fichero de material")])
def test_what_goes_wrong_in_penhmat_is_told(fake, mode, text):
    db, exe, case, doc = fake
    _mode(db, mode)
    plan = mg.plan_materials(doc, None, case, db)
    mg.generate(plan, exe, pg.launch_for(pg.exe_info(exe)), db, doc=doc)
    job = plan.mats[0]
    assert job.status == "failed" and text in job.message, job.message
    assert not (case / "agua.mat").exists()
    assert not [p for p in db.iterdir() if p.name.startswith(mg.TEMP_PREFIX)]


@POSIX
def test_nothing_is_ever_replaced(fake):
    db, exe, case, doc = fake
    _mode(db, f"race:{case / 'agua.mat'}")  # aparece otro agua.mat mientras penhmat trabaja
    (case / "pO16_ICRU63.endf").write_text("mi versión\n")
    plan = mg.plan_materials(doc, None, case, db)
    mg.generate(plan, exe, pg.launch_for(pg.exe_info(exe)), db, doc=doc)
    assert plan.mats[0].status == "exists" and (case / "agua.mat").read_text() == "otro"
    assert (case / "pO16_ICRU63.endf").read_text() == "mi versión\n"  # ya estaba: ni se lista


@POSIX
def test_cancel_stops_penhmat(fake):
    db, exe, case, doc = fake
    _mode(db, "slow")
    plan = mg.plan_materials(doc, None, case, db)
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    t0 = time.monotonic()
    mg.generate(plan, exe, pg.launch_for(pg.exe_info(exe)), db, doc=doc, cancel=stop.is_set)
    assert time.monotonic() - t0 < 10
    assert plan.cancelled and plan.mats[0].status == "cancelled" and not (case / "agua.mat").exists()


def test_endf_without_penhmat(fake):
    """Con los .mat ya hechos (o sin penhmat), se copian solo los ENDF que faltan; con electrones, ninguno."""
    db, _exe, case, doc = fake
    (case / "agua.mat").write_text(" PENELOPE (v. 2024)  Material data file ...\n Material: WATER (278)\n"
                                   " Mass density = 1.0 g/cm**3\n *** ENDF filenames and abundances of isotopes,  "
                                   "NISOT =   3\n pO16_ICRU63.endf 8016 0.99\n pO17_TENDL.endf 8017 0.001\n"
                                   " pO18_TENDL.endf 8018 0.002\n   0 = NALPHA\n")
    plan = mg.plan_materials(doc, None, case, db)
    assert [e.name for e in plan.endf_to_copy] == ["pO16_ICRU63.endf", "pO17_TENDL.endf"]
    assert [e.name for e in plan.endf_missing] == ["pO18_TENDL.endf"]
    mg.copy_endf_files(plan)
    assert {e.name: e.status for e in plan.endf} == {"pO16_ICRU63.endf": "done", "pO17_TENDL.endf": "done",
                                                      "pO18_TENDL.endf": "missing"}
    assert any("pO18_TENDL.endf" in line and "no está en la carpeta de los ENDF" in line for line in mg.summary(plan))
    assert mg.plan_endf(parse_in_text(IN.replace("SKPAR  4", "SKPAR  1")), case, db) == []
    assert "Programas de penhan" in mg.plan_endf(doc, tmp_path_like(case), None)[0].message


def tmp_path_like(case: Path) -> Path:
    """Otra carpeta con el mismo .mat pero sin ENDF (para ver el aviso sin base de datos)."""
    other = case.parent / "otra"
    other.mkdir(exist_ok=True)
    shutil.copy2(case / "agua.mat", other / "agua.mat")
    return other


# ---------------------------------------------------------------------------------------------------------------------
# El penhmat de verdad
# ---------------------------------------------------------------------------------------------------------------------

@POSIX
@pytest.mark.oracle
def test_the_real_penhmat_generates_list_materials_and_tac_tissues(tmp_path, monkeypatch):
    exe, db_real = os.environ.get("ARGUS_PENHMAT"), os.environ.get("ARGUS_PENHAN_DB")
    if not exe or not db_real:
        pytest.skip("penhmat no disponible (ARGUS_PENHMAT y ARGUS_PENHAN_DB)")
    from argus.ct.calibration import preset
    from argus.penhan import materials

    db = tmp_path / "database"
    db.mkdir()
    for sub in pg.DATABASE_PARTS:
        (db / sub).symlink_to(Path(db_real) / sub)
    shutil.copy2(Path(db_real) / "material-list.txt", db / "material-list.txt")
    penhmat = db / "penhmat.x"
    shutil.copy2(exe, penhmat)
    case = tmp_path / "caso"
    case.mkdir()
    doc = parse_in_text(IN)
    tissue = preset("schneider5").tissues[3]
    tissue.voxel_density = 1.0213
    real = materials.materials_table

    def with_tissue(d, geo, folder, *rest):
        rows = real(d, geo, folder, *rest)
        rows[1].tissue = tissue
        return rows

    monkeypatch.setattr(mg, "materials_table", with_tissue)
    plan = mg.plan_materials(doc, None, case, db)
    mg.generate(plan, penhmat, pg.launch_for(pg.exe_info(penhmat)), db, doc=doc, timeout=600)
    status = {j.fname: (j.status, j.message) for j in plan.mats}
    assert status["agua.mat"][0] == "done" and status["raro.mat"][0] == "done", status
    water, soft = read_mat_header(case / "agua.mat"), read_mat_header(case / "raro.mat")
    assert water.penelope_id == 278 and abs(soft.density - 1.0213) < 1e-6
    for mat in ("agua.mat", "raro.mat"):
        nuc = read_mat_nuclear(case / mat)
        assert nuc.complete and all((case / f).is_file() for f, _z, _a in nuc.isotopes)
    assert not [p for p in db.iterdir() if p.name.startswith(mg.TEMP_PREFIX)]


# ---------------------------------------------------------------------------------------------------------------------
# WSL de mentira: las mismas órdenes que en Windows, contestadas por un guion
# ---------------------------------------------------------------------------------------------------------------------

FAKE_WSL = textwrap.dedent('''\
    #!{python}
    import os, subprocess, sys
    args = sys.argv[1:]
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wsl-calls.txt"), "a") as f:
        f.write(" ".join(args) + "\\n")
    if args[:2] == ["-e", "wslpath"]:
        print(args[-1])
    elif args[:3] == ["-e", "sh", "-c"]:
        sys.exit(subprocess.call(["sh", "-c", args[3]]))
    elif args[0] == "--cd" and args[2] == "-e":
        os.chdir(args[1])
        os.execv(args[3], args[3:])
    else:
        sys.exit(9)
''')


@pytest.fixture
def fake_wsl(tmp_path, monkeypatch):
    exe = _write(tmp_path / "wsl" / "wsl.exe", FAKE_WSL.format(python=sys.executable).encode())
    monkeypatch.setattr(pg, "wsl_executable", lambda: exe)
    pg._WSL_CACHE.clear()
    yield exe
    pg._WSL_CACHE.clear()


@POSIX
def test_wsl_status_and_paths(fake_wsl, monkeypatch):
    st = pg.wsl_status(refresh=True)
    assert st.ok and st.note  # el nombre de la distribución (o «Linux»)
    assert pg.wsl_path("/c/algo/penhmat.x") == "/c/algo/penhmat.x"
    monkeypatch.setattr(pg, "wsl_executable", lambda: None)
    assert not pg.wsl_status(refresh=True).ok
    assert pg.wsl_path(r"C:\x\penhmat.x") == "/mnt/c/x/penhmat.x"


def test_wsl_without_distributions_is_explained(monkeypatch):
    class R:
        returncode = 1
        stdout = "El Subsistema de Windows para Linux no tiene distribuciones instaladas.".encode("utf-16-le")
        stderr = b""

    monkeypatch.setattr(pg, "wsl_executable", lambda: Path("wsl.exe"))
    monkeypatch.setattr(pg.subprocess, "run", lambda *a, **k: R())
    pg._WSL_CACHE.clear()
    try:
        st = pg.wsl_status(refresh=True)
    finally:
        pg._WSL_CACHE.clear()
    assert not st.ok and "no tiene distribuciones instaladas" in st.note


@POSIX
@pytest.mark.parametrize("inside", [True, False], ids=["en-database", "en-otra-carpeta"])
def test_generate_through_wsl_like_on_windows(fake, fake_wsl, inside):
    """Lo que hace ARGUS en Windows con un penhmat de Linux: wsl.exe --cd <database> -e <penhmat>."""
    db, exe, case, doc = fake
    if not inside:
        other = _write(case.parent / "otra" / "penhmat.py", exe.read_bytes())
        exe = other
    plan = mg.plan_materials(doc, None, case, db)
    mg.generate(plan, exe, pg.Launch("wsl", "prueba"), db, doc=doc)
    assert plan.mats[0].status == "done", plan.mats[0].message
    calls = (fake_wsl.parent / "wsl-calls.txt").read_text().splitlines()
    run = [c for c in calls if c.startswith("--cd")]
    assert run == [f"--cd {db} -e " + ("./penhmat.py" if inside else str(exe))]
    assert any(c.startswith("-e wslpath -a") for c in calls) != inside
