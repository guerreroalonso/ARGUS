"""La cascada: ejecuciones cortas de penhmain para ver las trayectorias de las primeras lluvias (0.13.0)."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from argus.penhan import cascade as cs
from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_file, parse_in_text, write_in_text
from argus.penhan.programs import Launch
from argus.results import shower as sh

CAJA = Path(__file__).parent / "corpus" / "resultados" / "caja"
NATIVE = Launch("native", "de mentira")

# penhmain de mentira: lee LTRACK y NSIMSH del .in y escribe, por lluvia, una primaria (electrón) con dos tramos y
# un fotón que sale de su vértice central; NPSHOW como el de penhmain (como mucho CAP, 50 si no se dice).
FAKE = r"""#!/bin/sh
IN=$(cat)
LT=$(printf '%s\n' "$IN" | awk '$1=="LTRACK"{print $2}')
NS=$(printf '%s\n' "$IN" | awk '$1=="NSIMSH"{printf "%d", $2}')
SEED=$(printf '%s\n' "$IN" | awk '$1=="RSEED"{print $2}')
CAP=${CAP:-50}
[ -n "$SLEEP" ] && sleep "$SLEEP"
NP=$LT; [ "$NP" -gt "$CAP" ] && NP=$CAP
echo "   Initialising PENELOPE ..."
{
echo " **   Program PENHMAIN.  Input data and run-time messages.   **"
if [ "$NP" -ge 100 ]; then echo "   Files with NPSHOW = ** showers generated for visualisation"
else echo "   Files with NPSHOW = $NP showers generated for visualisation"; fi
} > penhmain.dat
N=$NS; [ "$N" -gt "$NP" ] && N=$NP
awk -v n="$N" -v seed="$SEED" 'BEGIN{
  print " # X/cm         Y/cm         Z/cm         E/eV" > "electron.trk";
  print " # X/cm         Y/cm         Z/cm         E/eV" > "photon.trk";
  for (s = 1; s <= n; s++) {
    x = s * 0.01 + seed * 1e-5;
    if (s > 1) { print " " > "electron.trk"; print " " > "photon.trk"; }
    printf "  %.5E  %.5E  %.5E  %.5E\n", 0, 0, -1, 1e6 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", 0, 0, -1, 1e6 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", x, 0, 0, 6e5 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", x, 0, 1, 0 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", x, 0, 0, 3e5 > "photon.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", x + 3, 0, 0, 3e5 > "photon.trk";
  }
}'
echo "   No. of simulated showers = $NS, time = 1.0E+00 s"
echo "   *** END ***"
"""


def _case(tmp_path: Path) -> tuple[Path, object]:
    d = tmp_path / "caso"
    d.mkdir()
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, d / n)
    doc = parse_in_file(d / "caja.in")
    for md in doc.model.materials:
        (d / md.fname.strip()).write_text("material de mentira\n")
    return d / "caja.in", doc


def _fake(tmp_path: Path) -> Path:
    p = tmp_path / "programas" / "penhmain.x"
    p.parent.mkdir(exist_ok=True)
    p.write_text(FAKE)
    p.chmod(0o755)
    return p


# -- el .in de cada ejecución --------------------------------------------------------------------------------------

def test_the_cascade_input_keeps_the_transport_and_drops_the_scoring(tmp_path):
    in_path, doc = _case(tmp_path)
    # La primera ejecución: todo igual salvo el trabajo (la rejilla, EPROF y las edades gastan números aleatorios).
    ex = cs.cascade_doc(doc, 50, (1, 1), exact=True)
    for kw in ("GRIDX", "EPROF", "IDAGEL", "ENDETC", "ANGDET", "NBANGL"):
        assert [ln.values for ln in ex.find(sc.kw6(kw))] == [ln.values for ln in doc.find(sc.kw6(kw))], kw
    assert sc.impact_detectors(ex)[0].ipsf == 1
    # Sin volcado: DUMPP 1e12 (sin DUMPP, penhmain vuelca igual en dump.dmp; duodécima revisión).
    assert not ex.find("DUMPTO") and sc.value(ex, "DUMPP") == [cs.NO_DUMP] and sc.value(ex, "LTRACK") == [50]
    d = cascade = cs.cascade_doc(doc, 50, (-3, 0))
    text = write_in_text(d)
    for kw in ("GRIDX", "GRIDY", "GRIDZ", "ENDETC", "ANGDET", "EPROF", "DUMPTO", "NBANGL", "NBE   "):
        assert not any(ln.kw.strip() == kw.strip() for ln in d.lines), kw
    assert sc.value(d, "DUMPP") == [cs.NO_DUMP]
    assert sc.value(d, "LTRACK") == [50] and sc.value(d, "NSIMSH") == [50.0]
    assert sc.seeds(d) == [(-3, 0)]
    assert sc.value(d, "TIME") is None
    # Los detectores de impacto se quedan (con IDCUT 0 absorben), pero sin espacio de fases.
    imp = sc.impact_detectors(d)
    assert len(imp) == len(sc.impact_detectors(doc)) and all(x.ipsf == 0 for x in imp) and all(not x.psf
                                                                                                    for x in imp)
    # Igual que el caso en todo lo demás: materiales, geometría, fuente, parámetros de simulación.
    for kw in ("SKPAR", "SLINES", "SPOSIT", "SCONE", "MFNAME", "MSIMPA", "GEOMFN", "DSMAX"):
        a = [(ln.values, ln.text) for ln in doc.find(sc.kw6(kw))]
        b = [(ln.values, ln.text) for ln in cascade.find(sc.kw6(kw))]
        assert a == b, kw
    # penhmain lo leería (el lector de ARGUS es el PMRDR0 de penhmain).
    again = parse_in_text(text)
    assert again.ok and not [x for x in again.diagnostics if x.severity == "error"]
    probe = cs.cascade_doc(doc, 1000, (1, 1), probe=True)
    assert sc.value(probe, "SKPAR") == [1] and sc.value(probe, "SENERG") == [1.0e6]
    # La prueba, con LTRACK 99 (lo más que penhmain escribe con dos cifras), sean las lluvias que sean.
    assert not probe.find("SLINES") and sc.value(probe, "NSIMSH") == [1.0] and sc.value(probe, "LTRACK") == [99]


def test_runs_and_seeds():
    assert cs.runs_for(1000, 50) == [50] * 20
    assert cs.runs_for(120, 50) == [50, 50, 20]
    assert cs.runs_for(1000, 5000) == [1000]
    assert cs.run_seeds((12345, 54321), 3) == [(12345, 54321), (-1, 0), (-2, 0)]
    assert cs.run_seeds((-2, 1), 3) == [(-2, 1), (-1, 0), (-3, 0)]  # el par del caso no se repite
    # Sin RSEED (o «RSEED 1 1»), el caso empieza con el par 1 de penhmain, que es (1, 1): no se repite.
    assert cs.run_seeds((1, 1), 3) == [(1, 1), (-2, 0), (-3, 0)]
    with pytest.raises(ValueError):
        cs.run_seeds((1, 1), 1003)
    assert cs.read_npshow("   Files with NPSHOW = 50 showers generated for visualisation") == 50
    assert cs.read_npshow("   Files with NPSHOW = ** showers generated for visualisation") is None
    assert cs.parallel_jobs(20, True, cpus=8, memory=int(4e9)) == 3
    assert cs.parallel_jobs(20, False, cpus=8, memory=int(16e9)) == 6
    assert cs.parallel_jobs(2, False, cpus=64, memory=None) == 2


def test_the_signature_changes_with_what_changes_the_tracks(tmp_path):
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    s0 = cs.case_signature(in_path, doc, exe, 1000)
    assert cs.case_signature(in_path, doc, exe, 1000) == s0
    d2 = parse_in_text(write_in_text(doc))
    sc.set_value(d2, "NSIMSH", [5e6])  # solo del trabajo: no cambia la cascada
    sc.set_value(d2, "DUMPP", [120.0])
    assert cs.case_signature(in_path, d2, exe, 1000) == s0
    sc.set_value(d2, "SPOSIT", [0.0, 0.0, -7.0])
    assert cs.case_signature(in_path, d2, exe, 1000) != s0
    assert cs.case_signature(in_path, doc, exe, 500) != s0
    mat = in_path.parent / doc.model.materials[0].fname.strip()
    mat.write_text("otro material\n")
    assert cs.case_signature(in_path, doc, exe, 1000) != s0


# -- de principio a fin con un penhmain de mentira ----------------------------------------------------------------

pytestmark_sh = pytest.mark.skipif(sys.platform == "win32", reason="penhmain de mentira con /bin/sh")


@pytestmark_sh
def test_a_cascade_with_the_limit_of_penhmain(tmp_path, monkeypatch):
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    caps = []
    job = cs.CascadeJob(in_path, doc, exe, NATIVE, showers=120, jobs=2, on_cap=caps.append)
    cs.start_job(job)
    assert job.wait(60)
    st = job.status
    assert st.stage == "terminada", st.text
    assert caps == [50] and st.cap == 50 and st.showers_done == 120
    info = cs.find_cascades(in_path.parent, "caja.in")[0]
    assert info.path == in_path.parent / "cascada" and [r.showers for r in info.runs] == [50, 50, 20]
    assert info.runs[0].seeds == cs.case_seeds(doc)  # la primera: las primeras lluvias de la simulación de verdad
    for r, d in zip(info.runs, info.run_paths):
        assert (d / "caja.in").is_file() and (d / "electron.trk").is_file()
        assert sc.value(parse_in_file(d / "caja.in"), "LTRACK") == [r.showers]
        # Los ficheros del caso, enlazados (el mismo fichero: no ocupan más) y penhmain con ellos.
        geo = d / "caja.geo"
        assert geo.is_file() and os.path.samefile(geo, in_path.parent / "caja.geo")
        assert (d / "penhmain.x").is_file()
    assert not (in_path.parent / "cascada" / "sonda").exists()  # la prueba se hizo en una carpeta temporal
    s = sh.load_cascade(info, primary=1)
    assert s.n_showers == 120 and s.count == 240
    assert sorted(set(s.shower.tolist())) == list(range(120))
    photons = np.flatnonzero(s.particle == 2)
    assert np.all(s.parent[photons] >= 0) and np.all(s.generation[photons] == 1)
    assert np.all(s.shower[photons] == s.shower[s.parent[photons]])
    # Tiempo de vuelo: el electrón de 1 MeV va de la fuente al vértice (v = 0,94 c) y el fotón sale entonces.
    v = sh.speed(1, np.array([1e6]))[0]
    dist = np.linalg.norm(s.points[s.starts[photons]] - s.points[s.starts[s.parent[photons]]], axis=1)
    assert np.allclose(s.time[s.starts[photons]], dist / v, rtol=1e-4)
    # Otra vez lo mismo: no se vuelve a simular.
    job2 = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=120, jobs=2, cap=50))
    assert job2.wait(30) and job2.status.stage == "terminada" and "ya simulada" in job2.status.text
    # Con otras lluvias: otra carpeta (la anterior no se toca).
    job3 = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=30, jobs=2, cap=50))
    assert job3.wait(30) and job3.status.stage == "terminada"
    assert job3.status.folder == in_path.parent / "cascada-2"
    assert (in_path.parent / "cascada" / "run-03" / "electron.trk").is_file()


@pytestmark_sh
def test_a_penhmain_that_keeps_99_or_more_does_runs_of_99(tmp_path, monkeypatch):
    """penhmain escribe NPSHOW con dos cifras: la prueba (LTRACK 99) dice 99 si guarda 99 o más (con 100 o más
    pondría «**» y no se sabría cuántas guarda)."""
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    monkeypatch.setenv("CAP", "100000")
    caps = []
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=200, jobs=2, on_cap=caps.append))
    assert job.wait(60) and job.status.stage == "terminada", job.status.text
    assert caps == [99]
    info = cs.find_cascades(in_path.parent)[0]
    assert [r.showers for r in info.runs] == [99, 99, 2]
    assert sh.load_cascade(info).n_showers == 200


@pytestmark_sh
def test_cancelling_stops_the_runs_and_they_can_be_finished_later(tmp_path, monkeypatch):
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    monkeypatch.setenv("SLEEP", "30")
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=100, jobs=2, cap=50))
    t0 = time.time()
    while job.status.stage != "simulando" and time.time() - t0 < 20:
        time.sleep(0.1)
    time.sleep(0.5)
    job.cancel()
    assert job.wait(20)
    assert job.status.stage == "cancelada" and job.status.showers_done == 0
    assert time.time() - t0 < 25
    monkeypatch.setenv("SLEEP", "")
    job2 = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=100, jobs=2, cap=50))
    assert job2.wait(60) and job2.status.stage == "terminada"
    assert job2.status.folder == in_path.parent / "cascada"  # se terminó en la misma carpeta


@pytestmark_sh
def test_a_failing_penhmain_is_reported(tmp_path):
    in_path, doc = _case(tmp_path)
    bad = tmp_path / "programas" / "penhmain.x"
    bad.parent.mkdir()
    bad.write_text("#!/bin/sh\ncat > /dev/null\necho 'Unexpected file termination.'\necho 'IRETRN = 3035'\n")
    bad.chmod(0o755)
    job = cs.start_job(cs.CascadeJob(in_path, doc, bad, NATIVE, showers=60, jobs=2, cap=50))
    assert job.wait(30)
    st = job.status
    assert st.stage == "error" and "3035" in st.error


# -- reconstrucción con trayectorias de penhmain de verdad ----------------------------------------------------------

def _trk(path: Path, tracks: list[list[tuple[float, float, float, float]]]) -> None:
    lines = [" # X/cm         Y/cm         Z/cm         E/eV"]
    for i, t in enumerate(tracks):
        if i:
            lines.append(" ")
        lines += [" " + " ".join(f"{v:12.5E}" for v in p) for p in t]
    path.write_text("\n".join(lines) + "\n")


def test_reconstruction_rules(tmp_path):
    """Fuente puntual dentro de la materia (el primer punto se repite), una secundaria que sale del final de su madre
    (fotón de aniquilación), dos hermanas del mismo vértice y una nieta. penhmain escribe una lluvia entera antes de
    la siguiente: el delta de la primera va antes que la primaria de la segunda."""
    d = tmp_path / "run"
    d.mkdir()
    src = (0.0, 0.0, 0.0)
    e1 = [(*src, 1e6), (*src, 1e6), (0.0, 0.0, 1.0, 8e5), (0.0, 0.0, 2.0, 0.0)]
    e2 = [(*src, 1e6), (*src, 1e6), (0.0, 1.0, 0.0, 0.0)]
    delta = [(0.0, 0.0, 1.0, 1e5), (0.5, 0.0, 1.0, 0.0)]  # de e1, en z = 1
    g1 = [(0.0, 0.0, 1.0, 5e4), (3.0, 0.0, 1.0, 5e4)]  # hermana del delta (mismo vértice)
    g2 = [(0.5, 0.0, 1.0, 1e4), (0.5, 0.0, 9.0, 1e4)]  # nieta: sale del final del delta
    _trk(d / "electron.trk", [e1, delta, e2])
    _trk(d / "photon.trk", [g1, g2])
    for n in (None, 2):
        s = sh.reconstruct(sh.read_run_tracks(d), primary=1, n_source=n)
        assert s.n_showers == 2 and s.ambiguous == 0 and s.exact and not s.needed
        kinds = list(s.particle)
        assert kinds == [1, 1, 1, 2, 2]
        assert list(s.parent) == [-1, 0, -1, 0, 1]
        assert list(s.generation) == [0, 1, 0, 1, 2]
        assert list(s.shower) == [0, 0, 1, 0, 0]
    v = sh.speed(1, np.array([1e6]))[0]
    t_delta = 1.0 / v
    assert s.time[s.starts[1]] == pytest.approx(t_delta, rel=1e-5)
    assert s.time[s.starts[3]] == pytest.approx(t_delta, rel=1e-5)
    v_delta = sh.speed(1, np.array([1e5]))[0]
    assert s.time[s.starts[4]] == pytest.approx(t_delta + 0.5 / v_delta, rel=1e-5)
    assert s.time[s.starts[4] + 1] == pytest.approx(t_delta + 0.5 / v_delta + 8.0 / sh.C_CM_NS, rel=1e-5)
    sub = s.subset(showers=range(0, 1), particles={1})
    assert sub.count == 2 and list(sub.parent) == [-1, 0]
    tr = s.to_tracks()
    assert [t.particle for t in tr] == [1, 2] and tr[0].count == 3


def test_what_does_not_fit_is_not_guessed(tmp_path):
    """Si los .trk no cuadran con una lluvia detrás de otra (aquí, el delta de la primera lluvia escrito después de la
    segunda primaria), no se adivina: ninguna lluvia y hacen falta todos los cortes."""
    d = tmp_path / "run"
    d.mkdir()
    src = (0.0, 0.0, 0.0)
    e1 = [(*src, 1e6), (*src, 1e6), (0.0, 0.0, 1.0, 8e5), (0.0, 0.0, 2.0, 0.0)]
    e2 = [(*src, 1e6), (*src, 1e6), (0.0, 1.0, 0.0, 0.0)]
    delta = [(0.0, 0.0, 1.0, 1e5), (0.5, 0.0, 1.0, 0.0)]
    _trk(d / "electron.trk", [e1, e2, delta])
    s = sh.reconstruct(sh.read_run_tracks(d), primary=1, n_source=2)
    assert s.conflict and s.pending == 3 and s.needed == (1,) and list(s.shower) == [-1, -1, -1]
    # Con el corte (tras la lluvia 1, penhmain había escrito 4 puntos de electrones): cada una, la de su sitio.
    s = sh.reconstruct(sh.read_run_tracks(d), primary=1, n_source=2, cuts={1: {1: 4}})
    assert s.exact and list(s.shower) == [0, 1, 1]


def test_speeds():
    assert sh.speed(2, np.array([1.0]))[0] == pytest.approx(sh.C_CM_NS)
    beta_p = sh.speed(4, np.array([100e6]))[0] / sh.C_CM_NS
    assert beta_p == pytest.approx(0.428, abs=0.002)  # protón de 100 MeV
    assert sh.speed(6, np.array([0.0253]))[0] == pytest.approx(2.2e-4, rel=0.01)  # neutrón térmico: 2200 m/s
    assert sh.speed(1, np.array([0.0]))[0] == 0.0


REAL = os.environ.get("ARGUS_PENHMAIN")


@pytest.mark.skipif(not REAL or not os.environ.get("ARGUS_PENHMAIN_MATS"),
                    reason="ARGUS_PENHMAIN y ARGUS_PENHMAIN_MATS: un penhmain de verdad y los .mat del caso caja")
def test_with_a_real_penhmain_the_first_run_is_the_start_of_the_simulation(tmp_path):
    """La primera ejecución de la cascada son exactamente las primeras lluvias de la simulación del caso (con todo lo
    que registra): lo que se quita del .in no cambia las trayectorias ni los números aleatorios."""
    mats = Path(os.environ["ARGUS_PENHMAIN_MATS"])
    in_path, doc = _case(tmp_path)
    for md in doc.model.materials:
        shutil.copy(mats / md.fname.strip(), in_path.parent / md.fname.strip())
    job = cs.start_job(cs.CascadeJob(in_path, doc, REAL, NATIVE, showers=60, jobs=2))
    assert job.wait(300) and job.status.stage == "terminada", job.status.text
    info = cs.find_cascades(in_path.parent)[0]
    assert [r.showers for r in info.runs] == [50, 10]
    # La simulación de verdad (con su rejilla, detectores, EPROF…) y LTRACK 50 en otra carpeta.
    ref = tmp_path / "ref"
    shutil.copytree(in_path.parent, ref, ignore=shutil.ignore_patterns("cascada*"))
    d = parse_in_file(ref / "caja.in")
    sc.set_value(d, "LTRACK", [50])
    sc.set_value(d, "NSIMSH", [50.0])
    (ref / "caja.in").write_bytes(write_in_text(d).encode("latin-1"))
    import subprocess

    with open(ref / "caja.in", "rb") as fin:
        subprocess.run([REAL], cwd=ref, stdin=fin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=300, check=False)
    for name in ("electron.trk", "photon.trk"):
        a = (info.run_paths[0] / name).read_bytes()
        b = (ref / name).read_bytes()
        assert a == b, name
    s = sh.load_cascade(info, primary=2)
    assert s.n_showers == 60 and s.orphans == 0
