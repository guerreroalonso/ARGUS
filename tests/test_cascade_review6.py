"""Revisión de la cascada (0.13.1): semillas, prueba de NPSHOW, ficheros fuera de la carpeta, espacio de fases,
fuentes radiactivas, errores al lanzar y ejecuciones de otra sesión."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from argus.penhan import cascade as cs
from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_file, parse_in_text, write_in_text
from argus.results import shower as sh
from test_cascade import FAKE, NATIVE, _case, _fake

sh_only = pytest.mark.skipif(sys.platform == "win32", reason="penhmain de mentira con /bin/sh")


def _write_in(in_path: Path, doc) -> None:
    in_path.write_bytes(write_in_text(doc).encode("latin-1"))


# -- semillas y prueba ----------------------------------------------------------------------------------------------

def test_the_default_seeds_are_pair_1_and_are_not_repeated():
    """penhmain empieza con (1, 1) sin RSEED, y el par 1 de RAND0 es (1, 1): la 2.ª ejecución repetía la 1.ª."""
    assert cs.seed_pair((1, 1)) == 1 and cs.seed_pair((-7, 3)) == 7 and cs.seed_pair((5, 9)) is None
    assert cs.run_seeds((1, 1), 4) == [(1, 1), (-2, 0), (-3, 0), (-4, 0)]
    assert cs.run_seeds((-1, 0), 3) == [(-1, 0), (-2, 0), (-3, 0)]
    old = cs.CascadeInfo(Path("x"), "c.in", "s", 100, 50, 50, "p", [cs.CascadeRun("run-01", 50, (1, 1)),
                                                                    cs.CascadeRun("run-02", 50, (-1, 0))], 0.0,
                         version=1)
    assert old.repeats_showers
    new = cs.CascadeInfo(Path("x"), "c.in", "s", 100, 50, 50, "p", [cs.CascadeRun("run-01", 50, (1, 1)),
                                                                    cs.CascadeRun("run-02", 50, (-2, 0))], 0.0)
    assert not new.repeats_showers


@sh_only
def test_the_probe_learns_the_limit_whatever_the_request(tmp_path, monkeypatch):
    """La prueba va con LTRACK 99: con una cascada de 5 lluvias ya no aprende «5» (y la siguiente de 1000 no son
    200 ejecuciones de 5)."""
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    monkeypatch.setenv("CAP", "20")
    caps = []
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=5, jobs=2, on_cap=caps.append))
    assert job.wait(60) and job.status.stage == "terminada", job.status.text
    assert caps == [20]


def test_the_old_learned_limits_are_not_used(tmp_path):
    """Lo que apuntó ARGUS 0.13.0 podía ser menos de lo que guarda penhmain: se usa otra clave."""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings

    from argus.ui import cascade_ui as cu

    s = QSettings(str(tmp_path / "ajustes.ini"), QSettings.Format.IniFormat)
    exe = tmp_path / "penhmain.x"
    exe.write_text("x")
    s.setValue("cascada/npshow/" + cs.file_sha1(exe), 5)
    assert cu.known_cap(exe, s) is None
    cu.remember_cap(exe, 50, s)
    assert cu.known_cap(exe, s) == 50


# -- ficheros fuera de la carpeta ----------------------------------------------------------------------------------

def test_the_first_run_writes_its_results_in_its_folder(tmp_path):
    in_path, doc = _case(tmp_path)
    d = parse_in_text(write_in_text(doc), doc.path)
    imp = d.find("IMPDET")[0]
    pos = d.lines.index(imp) + 1
    new = [parse_in_text("IDSPC  D:\\spc\\a.dat\nIDPSF  ../../psf-x.dat\n").lines[k] for k in (0, 1)]
    d.insert_lines(pos, new)
    ex = cs.cascade_doc(d, 50, (1, 1), exact=True)
    assert sc.text(ex, "IDPSF").strip() == "psf-x.dat" and sc.text(ex, "IDSPC").strip() == "a.dat"
    # Dos con el mismo nombre en carpetas distintas: no al mismo fichero.
    assert cs._unique_name("a.dat", {"a.dat"}) == "a-2.dat"
    assert len(cs._unique_name("nombre-muy-largo.dat", {"nombre-muy-largo.dat"})) <= 20


@sh_only
def test_inputs_from_a_folder_above_are_linked_with_their_name(tmp_path):
    """«MFNAME ../mats/x.mat»: penhmain lo lee en la carpeta del caso; desde la de una ejecución apuntaría a otro
    sitio. Se enlaza con su nombre (otro si ya hay uno con ese nombre en el .in) y así lo cita el .in de la
    ejecución."""
    in_path, doc = _case(tmp_path)
    mats = tmp_path / "mats"
    mats.mkdir()
    names = [md.fname.strip() for md in doc.model.materials]
    assert len(names) == 2
    (mats / names[1]).write_text("el de la carpeta de arriba\n")
    d = parse_in_text(write_in_text(doc), doc.path)
    d.modify(d.find("MFNAME")[0], text=f"../mats/{names[1]}")  # el segundo sigue siendo el de la carpeta
    _write_in(in_path, d)
    doc = parse_in_file(in_path)
    exe = _fake(tmp_path)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=60, jobs=2, cap=50))
    assert job.wait(60) and job.status.stage == "terminada", job.status.text
    info = cs.find_cascades(in_path.parent)[0]
    stem, ext = names[1].rsplit(".", 1)
    for run in info.run_paths:
        cited = [ln.text.strip() for ln in parse_in_file(run / "caja.in").find("MFNAME")]
        assert cited == [f"{stem}-2.{ext}", names[1]]
        assert os.path.samefile(run / cited[0], mats / names[1])
        assert os.path.samefile(run / cited[1], in_path.parent / names[1])
    # Al ejecutar en la carpeta del .in, en cambio, es justo ese fichero: no es un problema.
    from argus.penhan.run import plan_run

    plan = plan_run(in_path, doc, penhmain=exe)
    up = [it for it in plan.files.items if it.name == f"../mats/{names[1]}"]
    assert up and up[0].status == "up" and not [p for p in plan.problems if "sube" in p]


def test_the_signature_ignores_what_only_records(tmp_path):
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    s0 = cs.case_signature(in_path, doc, exe, 100)

    def changed(edit) -> bool:
        d = parse_in_text(write_in_text(doc), doc.path)
        edit(d)
        return cs.case_signature(in_path, d, exe, 100) != s0

    assert not changed(lambda d: sc.set_value(d, "NBE", [0.0, 1.4e6, 70]))
    assert not changed(lambda d: sc.set_value(d, "NBANGL", [90, 18]))
    assert not changed(lambda d: d.modify(d.first("TITLE"), text="Otro título"))
    ln = doc.find("IMPDET")[0]

    def window(d):
        v = list(d.find("IMPDET")[0].values)
        v[1] = v[1] * 2
        d.modify(d.find("IMPDET")[0], values=v)

    def cut(d):
        v = list(d.find("IMPDET")[0].values)
        v[4] = 1 - int(v[4])
        d.modify(d.find("IMPDET")[0], values=v)

    assert len(ln.values) == 5
    assert not changed(window)
    assert changed(cut)  # con IDCUT 0 el detector absorbe lo que entra
    assert changed(lambda d: sc.set_value(d, "SPOSIT", [0.0, 0.0, -7.0]))


def test_two_new_cascades_at_once_get_their_own_folders(tmp_path):
    a = cs.new_cascade_folder(tmp_path, create=True)
    b = cs.new_cascade_folder(tmp_path, create=True)
    assert (a.name, b.name) == ("cascada", "cascada-2") and a.is_dir() and b.is_dir()


@sh_only
def test_a_missing_file_is_said_before_the_probe(tmp_path):
    in_path, doc = _case(tmp_path)
    missing = doc.model.materials[-1].fname.strip()
    (in_path.parent / missing).unlink()
    exe = tmp_path / "programas" / "penhmain.x"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\ntouch ../../LANZADO\ncat > /dev/null\n")
    exe.chmod(0o755)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=60, jobs=2))
    assert job.wait(30)
    st = job.status
    assert st.stage == "error" and f"no se encuentra {missing} (material)" in st.text, st.text
    assert not (tmp_path / "LANZADO").exists() and not list(in_path.parent.glob("cascada*"))


# -- errores al lanzar y ejecuciones que mueren -----------------------------------------------------------------------

@sh_only
@pytest.mark.parametrize("script, expected", [
    ("cat > /dev/null\necho 'Initialising PENELOPE ...'\nexit 0\n", "ha terminado sin acabar la simulación"),
    ("cat > /dev/null\necho './penhmain.x: error while loading shared libraries: libgfortran.so.5: cannot open' "
     ">&2\nexit 127\n", "libgfortran.so.5"),
    ("cat > /dev/null\nkill -9 $$\n", "código -9"),
])
def test_a_run_that_dies_is_an_error_not_simulating(tmp_path, script, expected):
    in_path, doc = _case(tmp_path)
    exe = tmp_path / "programas" / "penhmain.x"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n" + script)
    exe.chmod(0o755)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=60, jobs=2, cap=50))
    assert job.wait(60)
    st = job.status
    assert st.stage == "error" and expected in st.text, st.text
    info = cs.find_cascades(in_path.parent)[0]
    assert all(s.status == "error" for s in cs.cascade_progress(info)[2])


def test_a_launch_that_fails_is_said(tmp_path):
    """Windows: Popen falla (el antivirus bloquea la copia del .exe, falta una DLL…). Antes moría en su hilo y la
    ejecución se quedaba «simulando»."""
    in_path, doc = _case(tmp_path)
    exe = tmp_path / "programas" / "penhmain.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ no ejecutable")
    exe.chmod(0o644)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=60, jobs=2, cap=50, host="windows"))
    assert job.wait(60)
    st = job.status
    assert st.stage == "error" and "no se ha podido lanzar penhmain" in st.text, st.text


@sh_only
def test_runs_still_going_from_another_session_are_not_launched_again(tmp_path, monkeypatch):
    """ARGUS se cerró de golpe con la cascada en marcha: sus penhmain siguen. Al completarla, no se lanzan otra vez
    (serían dos en la misma carpeta): se espera a que terminen."""
    in_path, doc = _case(tmp_path)
    exe = tmp_path / "programas" / "penhmain.x"
    exe.parent.mkdir()
    log = tmp_path / "lanzamientos.txt"
    exe.write_text(FAKE.replace('[ -n "$SLEEP" ] && sleep "$SLEEP"',
                                f'echo "$(basename $(pwd))" >> {log}; [ -n "$SLEEP" ] && sleep "$SLEEP"'))
    exe.chmod(0o755)
    sig = cs.case_signature(in_path, doc, exe, 100)
    plan = cs.plan_cascade(in_path, doc, exe, showers=100, cap=50, signature=sig,
                           folder=cs.new_cascade_folder(in_path.parent, create=True))
    cs.write_cascade(plan)
    # La «otra sesión»: lanza run-01 y se va.
    run1 = plan.folder / "run-01"
    env = dict(os.environ, SLEEP="4")
    other = subprocess.Popen(["sh", cs.RUN_SCRIPT], cwd=run1, env=env, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import threading

    threading.Thread(target=other.wait, daemon=True).start()  # como lo haría el sistema con un huérfano
    t0 = time.time()
    while cs._read_pid(run1) is None and time.time() - t0 < 10:
        time.sleep(0.05)
    time.sleep(0.3)
    assert cs.pid_alive(run1, NATIVE)
    monkeypatch.setattr(cs, "ORPHAN_POLL", 0.5)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=100, jobs=2, cap=50))
    assert job.wait(60) and job.status.stage == "terminada", job.status.text
    assert log.read_text().split().count("run-01") == 1  # no se lanzó otra vez
    assert log.read_text().split().count("run-02") == 1


@sh_only
def test_cancelling_does_not_wait_in_the_interface(tmp_path, monkeypatch):
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    monkeypatch.setenv("SLEEP", "30")
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=100, jobs=2, cap=50))
    t0 = time.time()
    while job.status.stage != "simulando" and time.time() - t0 < 20:
        time.sleep(0.05)
    time.sleep(0.5)
    t1 = time.perf_counter()
    job.cancel()
    assert time.perf_counter() - t1 < 0.1
    assert job.wait(20) and job.status.stage == "cancelada"
    info = cs.find_cascades(in_path.parent)[0]
    assert [s.status for s in cs.cascade_progress(info)[2]] == ["pendiente", "pendiente"]


# -- espacio de fases --------------------------------------------------------------------------------------------------

PSF_HEAD = " # PSF de prueba\n #/KPAR  E  X  Y  Z  U  V  W  WGHT  ILB(1:4)  NSHI\n"


def _psf(path: Path, n_showers: int) -> list[str]:
    """Lluvias con saltos de NSHI (lluvias sin partículas) y lluvias con dos partículas (NSHI 0 la segunda)."""
    lines = []
    for k in range(1, n_showers + 1):
        nshi = 2 if k % 7 == 0 else 1
        lines.append(f" 2  1.00000E+06  {k * 0.01:.5E}  0.00000E+00  0.00000E+00  0 0 1  1.0  1 0 0 0 {nshi}")
        if k % 5 == 0:
            lines.append(f" 1  3.00000E+05  {k * 0.01:.5E}  1.00000E-01  0.00000E+00  0 0 1  1.0  1 0 0 0 0")
    path.write_text(PSF_HEAD + "\n".join(lines) + "\n")
    return lines


def test_the_phase_space_showers_are_read_like_penhmain(tmp_path):
    p = tmp_path / "a.psf"
    lines = _psf(p, 30)
    found = cs.read_psf_showers([p], 10)
    # N: suma de los NSHI; la 7.ª tiene NSHI 2 (una lluvia sin partículas antes).
    assert [s.n for s in found] == [1, 2, 3, 4, 5, 6, 8, 9, 10]
    assert [len(s.lines) for s in found][4] == 2  # la 5.ª, con dos partículas
    per, parts = cs.split_psf(cs.read_psf_showers([p], 1000), [10, 10, 10])
    assert per == [10, 10, 10]
    assert parts[0][-1].n == 10 and parts[1][0].n == 11 and parts[1][-1].n >= 20
    # Si el fichero no tiene tantas, menos ejecuciones.
    per, parts = cs.split_psf(cs.read_psf_showers([p], 1000), [20, 20, 20])
    assert len(per) == 2 and sum(per) <= 40
    assert cs.psf_record("# comentario") is None and cs.psf_record(lines[0])[0] == 2


def test_each_run_reads_its_own_phase_space_showers(tmp_path):
    """Antes, cada ejecución leía el espacio de fases desde el principio: las mismas primarias en todas."""
    in_path, doc = _case(tmp_path)
    lines = _psf(in_path.parent / "fase.psf", 200)
    d = parse_in_text(write_in_text(doc), doc.path)
    for kw in ("SKPAR", "SENERG", "SPECTR", "SLINES", "SGPOL"):
        d.remove(sc.kw6(kw))
    sc.set_psf_files(d, ["fase.psf"])
    _write_in(in_path, d)
    doc = parse_in_file(in_path)
    assert doc.model.source.from_psf
    exe = _fake(tmp_path)
    plan = cs.plan_cascade(in_path, doc, exe, showers=120, cap=50)
    assert [r.showers for r in plan.runs] == [50, 50, 20]
    assert sc.psf_files(plan.docs[0]) == ["fase.psf"] and plan.chunks[0] is None
    assert all(sc.psf_files(d) == [cs.PSF_CHUNK] for d in plan.docs[1:])
    chunk = plan.chunks[1].decode("latin-1").splitlines()
    body = [ln for ln in chunk if not ln.startswith("#")]
    parts = cs.split_psf(cs.read_psf_showers([in_path.parent / "fase.psf"], 120), [50, 50, 20])[1]
    assert parts[0][-1].n >= 50 and parts[1][0].n == parts[0][-1].n + 1  # sigue donde acaba la 1.ª
    assert body == [ln for g in parts[1] for ln in g.lines] and body[0] in lines  # tal cual
    assert not any(it.kind == "psf" for it in plan.files[1].items)
    cs.write_cascade(plan)
    assert (plan.folder / "run-02" / cs.PSF_CHUNK).read_bytes() == plan.chunks[1]
    # Lo que lee el visor: las lluvias de cada ejecución en su fichero (en la 2.ª, desde la 51).
    src = sh.run_source(plan.folder / "run-02", "caja.in", 50)
    assert src.pieces and src.n_showers == len(cs.read_psf_showers([plan.folder / "run-02" / cs.PSF_CHUNK], 50))
    assert src.particles[0][2] == pytest.approx(cs.psf_record(parts[1][0].lines[0])[2]) and src.particles[0][5] == 0


# -- reconstrucción con fuentes radiactivas ---------------------------------------------------------------------------

def _trk(path: Path, tracks: list[list[tuple]], header: bool = True) -> None:
    lines = [" # X/cm         Y/cm         Z/cm         E/eV"] if header else []
    for i, t in enumerate(tracks):
        if i:
            lines.append(" ")
        lines += [" " + " ".join(f"{v:12.5E}" for v in p) for p in t]
    path.write_text("\n".join(lines) + "\n")


def test_decay_particles_written_together_are_separated(tmp_path):
    """Co-60 puntual: penhmain escribe la β de cada desintegración como trayectoria (su primer punto, dos veces) y
    los dos fotones a continuación de la última trayectoria de fotones, sin línea en blanco (los primeros, en
    fort.72, que aún no había photon.trk)."""
    D = (0.0, 0.0, 2.5)
    b1 = [(*D, 2e5), (*D, 2e5), (1.0, 0.0, 2.5, 1e5), (1.5, 0.0, 2.5, 0.0)]
    b2 = [(*D, 1.5e5), (*D, 1.5e5), (-1.0, 0.0, 2.5, 5e4), (-1.2, 0.0, 2.5, 0.0)]
    compton = [(0.0, 2.0, 2.5, 3e5), (0.0, 2.3, 2.5, 0.0)]  # del primer fotón de la 1.ª
    run = tmp_path / "run"
    run.mkdir()
    _trk(run / "electron.trk", [b1, compton, b2])
    # fort.72: los dos fotones de la 1.ª desintegración (antes de abrirse photon.trk).
    _trk(run / "fort.72", [[(*D, 1.17e6), (0.0, 2.0, 2.5, 8.7e5), (0.0, 9.0, 2.5, 8.7e5),
                            (*D, 1.33e6), (0.0, 0.0, 9.0, 1.33e6)]], header=False)
    # photon.trk: un fotón de frenado de la 2.ª β (trayectoria) y, pegados, los dos de la 2.ª desintegración (el
    # primero con menos energía que el último punto anterior: solo se ve que vuelve a la fuente).
    _trk(run / "photon.trk", [[(-1.0, 0.0, 2.5, 2e4), (-3.0, 0.0, 2.5, 2e4),
                               (*D, 1.17e6), (0.0, -9.0, 2.5, 1.17e6), (*D, 1.33e6), (-9.0, 0.0, 2.5, 1.33e6)]])
    tracks = sh.read_run_tracks(run, fort=True)
    assert [type(t).__name__ for t in tracks] == ["Tracks", "FortTracks", "Tracks"]
    s = sh.reconstruct(tracks, None, pieces=True, source_points=np.array([D]), n_source=2)
    assert s.count == 3 + 5 and s.n_showers == 2
    starts = [tuple(np.round(s.points[a], 5)) for a in s.starts[:-1]]
    assert starts.count(D) == 2 + 4  # dos β y cuatro fotones de la fuente
    photons = np.flatnonzero(s.particle == 2)
    roots = photons[s.parent[photons] < 0]
    assert len(roots) == 4 and np.all(s.generation[roots] == 0)
    # Lo que se sabe: el primer fotón de fort.72 es de la 1.ª (su electrón Compton va antes de la 2.ª β); los de
    # photon.trk, de la 2.ª (después del fotón de frenado de la 2.ª β). El segundo de fort.72 puede ser de las dos:
    # no se adivina (antes se repartía «por las energías»), hace falta el corte tras la 1.ª lluvia.
    assert list(s.shower[roots]) == [0, -1, 1, 1] and s.pending == 1 and s.needed == (1,)
    # El corte: tras la 1.ª lluvia, penhmain había escrito 6 puntos de electrones (β y Compton) y 5 de fotones (los
    # dos de fort.72).
    s = sh.reconstruct(tracks, None, pieces=True, source_points=np.array([D]), n_source=2, cuts={1: {1: 6, 2: 5}})
    roots = np.flatnonzero((s.particle == 2) & (s.parent < 0))
    assert list(s.shower[roots]) == [0, 0, 1, 1] and s.exact and not s.needed
    comp = np.flatnonzero((s.particle == 1) & (s.parent >= 0))
    assert len(comp) == 1 and s.parent[comp[0]] == roots[0] and s.shower[comp[0]] == 0
    brem = photons[s.parent[photons] >= 0]
    assert len(brem) == 1 and s.shower[brem[0]] == 1 and s.particle[s.parent[brem[0]]] == 1
    # Ninguna trayectoria vuelve a la fuente ni gana energía.
    for i in range(s.count):
        pts = s.points[s.starts[i]:s.starts[i + 1]]
        e = s.energy[s.starts[i]:s.starts[i + 1]]
        assert not any(tuple(np.round(p, 5)) == D for p in pts[1:] if not np.all(p == pts[0]))
        assert np.all(np.diff(e) <= 0)


def test_without_pieces_mode_nothing_changes(tmp_path):
    """Con una fuente normal (SKPAR) no se corta nada: un electrón lento repite puntos y no es otra partícula."""
    run = tmp_path / "run"
    run.mkdir()
    e = [(0.0, 0.0, 0.0, 1e6), (0.0, 0.0, 0.0, 1e6), (1e-5, 0.0, 0.0, 9e5), (0.0, 0.0, 0.0, 8e5),
         (2e-5, 0.0, 0.0, 7e5)]
    _trk(run / "electron.trk", [e])
    s = sh.reconstruct(sh.read_run_tracks(run), 1)
    assert s.count == 1 and s.n_showers == 1
    s2 = sh.reconstruct(sh.read_run_tracks(run, fort=True), None, pieces=True)
    assert s2.count == 1  # los electrones no se cortan por volver a un punto suyo


# -- con un penhmain de verdad -----------------------------------------------------------------------------------------

REAL = os.environ.get("ARGUS_PENHMAIN")
DB = os.environ.get("ARGUS_PENHAN_DB")


@pytest.mark.skipif(not REAL or not os.environ.get("ARGUS_PENHMAIN_MATS") or not DB,
                    reason="ARGUS_PENHMAIN, ARGUS_PENHMAIN_MATS y ARGUS_PENHAN_DB")
def test_with_a_real_penhmain_a_radioactive_source(tmp_path):
    """Co-60 puntual en el caso caja: cada desintegración, una lluvia; ninguna trayectoria vuelve a la fuente ni gana
    energía; las semillas de las dos ejecuciones, distintas."""
    mats = Path(os.environ["ARGUS_PENHMAIN_MATS"])
    in_path, doc = _case(tmp_path)
    for md in doc.model.materials:
        shutil.copy(mats / md.fname.strip(), in_path.parent / md.fname.strip())
    db = Path(DB)
    for f in ("nucleide-files/Co-60.nuc", "pdfiles/pdrelax.p11", "pdfiles/pdatconf.p14"):
        shutil.copy(db / f, in_path.parent / Path(f).name)
    d = parse_in_text(write_in_text(doc), doc.path)
    for kw in ("SENERG", "SPECTR", "SLINES", "SGPOL", "SCONE", "SRECTA"):
        d.remove(sc.kw6(kw))
    sc.set_value(d, "SKPAR", [0])
    d.set("SRNUCL", text="Co-60.nuc")
    _write_in(in_path, d)
    doc = parse_in_file(in_path)
    assert doc.ok and doc.model.source.radioactive
    job = cs.start_job(cs.CascadeJob(in_path, doc, REAL, NATIVE, showers=60, jobs=2, cap=50))
    assert job.wait(1200) and job.status.stage == "terminada", job.status.text
    info = cs.find_cascades(in_path.parent)[0]
    s = sh.load_cascade(info)
    # 0.13.2: la lluvia de cada trayectoria, exacta (con los cortes que hagan falta: fuente puntual) y sin carpetas
    # de cortes que se queden.
    assert s.n_showers == 60 and s.exact and s.count and sorted(set(s.shower.tolist())) == list(range(60))
    assert job.status.cuts_done == job.status.cuts_total > 0
    assert not [p for p in info.path.iterdir() if p.name.startswith(cs.CUT_PREFIX)]
    src = np.array(doc.model.source.position)
    for i in range(s.count):
        a, b = s.starts[i], s.starts[i + 1]
        p = s.points[a:b]
        assert not any(np.allclose(q, src) for q in p[1:] if not np.all(q == p[0])), i
        if s.particle[i] != 6:
            assert np.all(np.diff(s.energy[a:b]) <= 1e-5 * s.energy[a:b][:-1] + 1e-3), i
