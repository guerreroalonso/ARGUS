"""Duodécima revisión (0.17.4): lo que dice ARGUS de los volcados frente a lo que hace el penhmain de verdad.

Necesitan ``ARGUS_PENHMAIN`` (un penhmain 2025 compilado) y ``ARGUS_PENHMAIN_MATS`` (los .mat de caja: water.mat y
hueso.mat); la de combinar, también ``ARGUS_PENHMAIN_SUM`` (penhmain-sum). Cada simulación es de 300 lluvias del caso
caja del corpus de resultados (unos segundos)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from argus.penhan import sections as sc
from argus.penhan.dumpfile import read_dump
from argus.penhan.inputfile import parse_in_file, write_in_text
from test_matrix_review6 import CAJA

REAL = os.environ.get("ARGUS_PENHMAIN")
MATS = os.environ.get("ARGUS_PENHMAIN_MATS")
SUM = os.environ.get("ARGUS_PENHMAIN_SUM")
pytestmark = pytest.mark.skipif(not REAL or not MATS, reason="ARGUS_PENHMAIN y ARGUS_PENHMAIN_MATS: un penhmain de "
                                                             "verdad y los .mat del caso caja")


def real_case(folder: Path, nsimsh: float = 300.0, ipsf: int = 1, seeds: tuple[int, int] = (1, 1)) -> Path:
    folder.mkdir(parents=True)
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, folder / n)
    doc = parse_in_file(folder / "caja.in")
    for md in doc.model.materials:
        shutil.copy(Path(MATS) / md.fname.strip(), folder / md.fname.strip())
    sc.set_value(doc, "NSIMSH", [nsimsh])
    sc.set_value(doc, "LTRACK", None)
    sc.set_seeds(doc, [seeds])
    dets = sc.impact_detectors(doc)
    dets[0].ipsf = ipsf
    sc.set_impact_detectors(doc, dets)
    (folder / "caja.in").write_bytes(write_in_text(doc).encode("latin-1"))
    return folder / "caja.in"


def edit(in_path: Path, fn) -> object:
    doc = parse_in_file(in_path)
    fn(doc)
    in_path.write_bytes(write_in_text(doc).encode("latin-1"))
    return parse_in_file(in_path)


def penhmain(in_path: Path) -> subprocess.CompletedProcess:
    with open(in_path, "rb") as fin:
        return subprocess.run([REAL], cwd=in_path.parent, stdin=fin, capture_output=True, timeout=900, check=False)


def outcome(in_path: Path, doc):
    from argus.penhan import resume as rs

    return rs.resume_outcome(doc, in_path.parent)


def test_with_a_phase_space_detector_penhmain_really_cannot_resume(tmp_path):
    """IPSF 1 y RESUME: ARGUS dice que penhmain se detendría, y se detiene al escribir («EOF marker») sin guardar nada
    de lo simulado (el volcado sigue con las 300 de antes). Con IPSF 0 en ese detector, sí sigue hasta las 600."""
    in_path = real_case(tmp_path / "a")
    assert penhmain(in_path).returncode == 0
    assert read_dump(in_path.parent / "dump.dmp").tallies.rwrite > 0
    doc = edit(in_path, lambda d: (sc.set_text(d, "RESUME", "dump.dmp"), sc.set_value(d, "NSIMSH", [600.0])))
    assert outcome(in_path, doc).action == "stop"
    res = penhmain(in_path)
    out = (res.stdout + res.stderr).decode("latin-1")
    assert res.returncode != 0 and "EOF marker" in out, out[-2000:]
    assert read_dump(in_path.parent / "dump.dmp").showers == 300  # se ha perdido todo lo simulado tras reanudar

    def no_psf(d):
        dets = sc.impact_detectors(d)
        dets[0].ipsf = 0
        sc.set_impact_detectors(d, dets)

    doc = edit(in_path, no_psf)
    assert outcome(in_path, doc).action == "continue"
    assert penhmain(in_path).returncode == 0 and read_dump(in_path.parent / "dump.dmp").showers == 600


def test_a_finished_simulation_is_really_rewritten_with_the_time_at_zero(tmp_path):
    in_path = real_case(tmp_path / "a", ipsf=0)
    assert penhmain(in_path).returncode == 0
    before = read_dump(in_path.parent / "dump.dmp")
    assert before.showers == 300 and before.time_total > 0
    doc = edit(in_path, lambda d: sc.set_text(d, "RESUME", "dump.dmp"))
    assert outcome(in_path, doc).action == "done"
    out = penhmain(in_path).stdout.decode("latin-1")
    assert "already completed" in out
    after = read_dump(in_path.parent / "dump.dmp")
    assert after.showers == 300 and after.time_total == 0.0  # lo que dice ARGUS: «con el tiempo de cálculo a 0»


@pytest.mark.parametrize("how", ["foreign", "cut", "no-end"])
def test_damaged_foreign_and_end_less_dumps_with_the_real_penhmain(tmp_path, how):
    """Otra cosa como volcado: penhmain la sustituye por «Empty.» (ARGUS: error, «se perdería»); cortado: se detiene
    (End of file); completo sin la línea «*** END ***»: lo reanuda (antes ARGUS lo daba por incompleto)."""
    in_path = real_case(tmp_path / "a", ipsf=0)
    assert penhmain(in_path).returncode == 0
    folder = in_path.parent
    text = (folder / "dump.dmp").read_text(encoding="latin-1")
    name = "dump.dmp"
    if how == "foreign":
        name = "notas.txt"
        shutil.copy(folder / "caja.geo", folder / name)
    elif how == "cut":
        (folder / name).write_text("\n".join(text.split("\n")[:60]) + "\n", encoding="latin-1")
    else:
        (folder / name).write_text(text.replace("*** END ***", ""), encoding="latin-1")
    doc = edit(in_path, lambda d: (sc.set_text(d, "RESUME", name), sc.set_value(d, "NSIMSH", [600.0])))
    expected = {"foreign": "destroy", "cut": "stop", "no-end": "continue"}[how]
    assert outcome(in_path, doc).action == expected
    res = penhmain(in_path)
    log = (folder / "penhmain.dat").read_text(encoding="latin-1")
    if how == "foreign":
        assert (folder / name).read_text().strip() == "Empty."  # (una copia de la geometría: se ha perdido)
    elif how == "cut":
        assert res.returncode != 0 and "End of file" in res.stderr.decode("latin-1")
    else:
        assert res.returncode == 0 and "Simulation has been resumed" in log
        assert read_dump(folder / "dump.dmp").showers == 600


def test_the_runs_of_the_cascade_really_write_no_dump(tmp_path):
    from argus.penhan import cascade as cs

    in_path = real_case(tmp_path / "caso", ipsf=0)
    d = cs.cascade_doc(parse_in_file(in_path), 3, (-2, 0))
    run = tmp_path / "run"
    run.mkdir()
    for f in in_path.parent.iterdir():
        if f.suffix in (".geo", ".mat", ".mcs"):
            shutil.copy(f, run / f.name)
    (run / "caja.in").write_bytes(write_in_text(d).encode("latin-1"))
    assert penhmain(run / "caja.in").returncode == 0
    assert (run / "penhmain-res.dat").is_file() and not (run / "dump.dmp").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="guion de sh")
def test_the_launcher_says_penhmain_stopped_with_the_real_penhmain(tmp_path):
    from argus.penhan import run as rn

    in_path = real_case(tmp_path / "a", ipsf=0)
    folder = in_path.parent
    assert penhmain(in_path).returncode == 0
    text = (folder / "dump.dmp").read_text(encoding="latin-1").replace("ARGUS corpus: caja", "Otro título       ")
    (folder / "otro.dmp").write_text(text, encoding="latin-1")
    edit(in_path, lambda d: sc.set_text(d, "RESUME", "otro.dmp"))
    shutil.copy(REAL, folder / "penhmain.x")
    script = rn.write_launcher(folder, "sh", "caja.in", "penhmain.x")
    res = subprocess.run(["sh", str(script)], input="\n", capture_output=True, text=True, timeout=900)
    assert "IRETRN = 3112" in res.stdout and "se ha detenido" in res.stdout, res.stdout[-1500:]
    assert "los resultados estan en esta carpeta" not in res.stdout


@pytest.mark.skipif(not SUM or sys.platform == "win32", reason="ARGUS_PENHMAIN_SUM: un penhmain-sum de verdad")
def test_combine_with_the_real_penhmain_sum(tmp_path):
    """combinar.sh combina dos ejecuciones de verdad y, con el volcado de una a medio escribir, no combina ni dice
    «Combinadas» (penhmain-sum acaba con el código 0 sin haber combinado nada)."""
    from argus.penhan import batch as bt

    base = real_case(tmp_path / "caso", ipsf=0)
    b = bt.make_parallel(parse_in_file(base), base, tmp_path / "par", 2, total_showers=600)
    (tmp_path / "par").mkdir()
    script = tmp_path / "par" / "combinar.sh"
    script.write_text(bt.combine_scripts(b)["combinar.sh"])
    for k, r in enumerate(b.runs, start=1):
        runp = real_case(tmp_path / "par" / r.folder, seeds=(-k, 0), ipsf=0)
        assert penhmain(runp).returncode == 0
    res = subprocess.run(["sh", str(script), SUM], capture_output=True, text=True, timeout=300)
    assert "Combinadas en suma" in res.stdout, res.stdout + res.stderr
    assert read_dump(tmp_path / "par" / "suma" / "all-dump.dat").showers == 600
    shutil.rmtree(tmp_path / "par" / "suma")
    dump2 = tmp_path / "par" / b.runs[1].folder / "dump.dmp"
    dump2.write_text("\n".join(dump2.read_text(encoding="latin-1").split("\n")[:50]) + "\n", encoding="latin-1")
    res = subprocess.run(["sh", str(script), SUM], capture_output=True, text=True, timeout=300)
    assert "Combinadas" not in res.stdout and "Aun no hay un volcado completo" in res.stdout, res.stdout
    assert not (tmp_path / "par" / "suma").exists()
