"""La lluvia de cada trayectoria, exacta: lo que se sabe con certeza y, si no basta, los «cortes» (la misma
ejecución, parando tras b lluvias) (0.13.2)."""

from __future__ import annotations

import json
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
from argus.results.data import Tracks

CAJA = Path(__file__).parent / "corpus" / "resultados" / "caja"
NATIVE = Launch("native", "de mentira")
pytestmark_sh = pytest.mark.skipif(sys.platform == "win32", reason="penhmain de mentira con /bin/sh")

# penhmain de mentira con una fuente radiactiva puntual en (0, 0, -5) dentro de la materia: por desintegración, la β
# (la primera partícula: trayectoria propia, su primer punto dos veces) y un fotón que sale de la pila sin trayectoria
# propia: como no hay photon.trk, sus puntos van a fort.72, uno detrás de otro. Nada dice de qué desintegración es cada
# fotón: hacen falta los cortes. Con BAD, lo que escribe depende de NSIMSH (no es el principio de la ejecución entera);
# con CUT_SLEEP, los cortes (NSIMSH < LTRACK) tardan.
FAKE_NUC = r"""#!/bin/sh
IN=$(cat)
LT=$(printf '%s\n' "$IN" | awk '$1=="LTRACK"{print $2}')
NS=$(printf '%s\n' "$IN" | awk '$1=="NSIMSH"{printf "%d", $2}')
SEED=$(printf '%s\n' "$IN" | awk '$1=="RSEED"{print $2}')
CAP=${CAP:-50}
[ -n "$CUT_SLEEP" ] && [ "$NS" -lt "$LT" ] && sleep "$CUT_SLEEP"
NP=$LT; [ "$NP" -gt "$CAP" ] && NP=$CAP
echo "   Files with NPSHOW = $NP showers generated for visualisation" > penhmain.dat
N=$NS; [ "$N" -gt "$NP" ] && N=$NP
B=0; [ -n "$BAD" ] && B=$NS
awk -v n="$N" -v seed="$SEED" -v bad="$B" 'BEGIN{
  print " # X/cm         Y/cm         Z/cm         E/eV" > "electron.trk";
  for (s = 1; s <= n; s++) {
    x = s * 0.01 + seed * 1e-5 + bad * 1e-3;
    if (s > 1) print " " > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", 0, 0, -5, 3e5 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", 0, 0, -5, 3e5 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", x, 0, -4.5, 1e5 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", x, 0, -4.4, 0 > "electron.trk";
    printf "  %.5E  %.5E  %.5E  %.5E\n", 0, 0, -5, 1.17e6 > "fort.72";
    printf "  %.5E  %.5E  %.5E  %.5E\n", 2 * x, 1, 5, 1.17e6 > "fort.72";
  }
}'
echo "   *** END ***"
"""


def _trk(path: Path, tracks: list[list[tuple]], header: bool = True) -> None:
    lines = [" # X/cm         Y/cm         Z/cm         E/eV"] if header else []
    for i, t in enumerate(tracks):
        if i:
            lines.append(" ")
        lines += [" " + " ".join(f"{v:12.5E}" for v in p) for p in t]
    path.write_text("\n".join(lines) + "\n")


def _tracks(kp: int, tracks: list[list[tuple]], fort: bool = False) -> Tracks:
    pts = np.array([p for t in tracks for p in t], dtype=np.float64)
    st = np.cumsum([0] + [len(t) for t in tracks]).astype(np.int64)
    return (sh.FortTracks if fort else Tracks)(kp, pts, st)


def _nuclide_case(tmp_path: Path) -> tuple[Path, object]:
    d = tmp_path / "caso"
    d.mkdir()
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, d / n)
    doc = parse_in_file(d / "caja.in")
    for md in doc.model.materials:
        (d / md.fname.strip()).write_text("material de mentira\n")
    for n in ("Co-60.nuc", "pdrelax.p11", "pdatconf.p14"):
        (d / n).write_text("de mentira\n")
    t = parse_in_text(write_in_text(doc), doc.path)
    for kw in ("SENERG", "SPECTR", "SLINES", "SGPOL", "SCONE", "SRECTA"):
        t.remove(sc.kw6(kw))
    sc.set_value(t, "SKPAR", [0])
    t.set("SRNUCL", text="Co-60.nuc")
    in_path = d / "caja.in"
    in_path.write_text(write_in_text(t), encoding="latin-1")
    doc = parse_in_file(in_path)
    assert doc.ok and doc.model.source.radioactive and not doc.model.source.extended
    return in_path, doc


def _fake(tmp_path: Path) -> Path:
    p = tmp_path / "programas" / "penhmain.x"
    p.parent.mkdir(exist_ok=True)
    p.write_text(FAKE_NUC)
    p.chmod(0o755)
    return p


# -- lo que es seguro ------------------------------------------------------------------------------------------------

def test_the_cuts_say_where_each_shower_ends():
    """Tras el corte b (las b primeras lluvias), lo que va antes de su sitio es de las lluvias 0…b-1."""
    kp = np.array([2, 2, 2, 2, 1, 1])
    pos = np.array([0, 3, 5, 9, 0, 4])
    lo, hi, have = sh._cut_bounds(4, {1: {2: 3, 1: 4}, 3: {2: 9, 1: 4}}, kp, pos)
    assert have == {1, 3}
    assert list(lo) == [0, 1, 1, 3, 0, 3]  # las lluvias 1 y 2 no escribieron electrones: lo del punto 4, de la 3
    assert list(hi) == [0, 2, 2, 3, 0, 3]
    lo, hi, have = sh._cut_bounds(4, {}, kp, pos)
    assert not have and list(lo) == [0] * 6 and list(hi) == [3] * 6


def test_a_piece_of_the_next_shower_is_cut_where_its_shower_starts():
    """Un fotón de la desintegración siguiente pegado sin que nada lo delate (la misma energía que el último punto, sin
    volver a la fuente): lo separa el corte, que dice dónde empieza la lluvia siguiente en photon.trk."""
    d0 = (0.0, 0.0, 0.0)
    b1 = [(*d0, 2e5), (*d0, 2e5), (0.1, 0.0, 0.0, 0.0)]
    b2 = [(*d0, 2e5), (*d0, 2e5), (-0.1, 0.0, 0.0, 0.0)]
    brem = [(0.1, 0.0, 0.0, 5e4), (3.0, 0.0, 0.0, 5e4), (3.0, 4.0, 0.0, 5e4), (5.0, 4.0, 0.0, 5e4)]
    tracks = [_tracks(1, [b1, b2]), _tracks(2, [brem])]
    s = sh.reconstruct(tracks, None, pieces=True, source_points=np.array([d0]), n_source=2)
    assert s.count == 3 and s.exact  # sin el corte, el fotón de frenado (de la 1.ª) lleva pegados sus 4 puntos
    s = sh.reconstruct(tracks, None, pieces=True, source_points=np.array([d0]), n_source=2, cuts={1: {1: 3, 2: 2}})
    assert s.count == 4 and s.exact
    ph = np.flatnonzero(s.particle == 2)
    assert list(s.shower[ph]) == [0, 1] and s.parent[ph[1]] == -1 and s.generation[ph[1]] == 0


def test_a_point_source_in_vacuum():
    """Fuera de la materia, solo la primera partícula de cada desintegración sale del punto de la fuente: son las
    lluvias, en orden. Los fotones de la desintegración entran en la geometría donde sea: sin cortes, no se sabe de
    cuál son (salvo por lo que va antes y después en su fichero); con ellos, sí."""
    s0 = (0.0, 0.0, -3.0)
    b = [[(*s0, 3e5), (0.1 * k, 0.0, 0.0, 3e5), (0.1 * k, 0.0, 0.2, 0.0)] for k in (1, 2, 3)]
    g = [(0.5, 0.0, 0.0, 1.17e6), (1.0, 0.0, 3.0, 1.17e6)]  # entra a z = 0: viene de la fuente, en línea recta
    g2 = [(-0.5, 0.0, 0.0, 1.33e6), (-1.0, 0.0, 3.0, 1.33e6)]
    tracks = [_tracks(1, b), _tracks(2, [g + g2], fort=True)]
    s = sh.reconstruct(tracks, None, pieces=True, source_points=np.array([s0]), n_source=3)
    beta = np.flatnonzero(s.particle == 1)
    ph = np.flatnonzero(s.particle == 2)
    assert list(s.shower[beta]) == [0, 1, 2] and len(ph) == 2
    assert list(s.shower[ph]) == [-1, -1] and s.needed == (1, 2) and s.pending == 2
    s = sh.reconstruct(tracks, None, pieces=True, source_points=np.array([s0]), n_source=3,
                       cuts={1: {1: 3, 2: 2}, 2: {1: 6, 2: 2}})
    assert s.exact and list(s.shower[np.flatnonzero(s.particle == 2)]) == [0, 2]


def test_an_extended_source_in_matter_needs_no_cuts():
    """Fuente extensa: cada desintegración sale de un punto distinto, y lo que sale de él es suyo."""
    dk = [(0.1, 0.2, 0.3), (-0.4, 0.5, 0.6), (0.7, -0.8, 0.9)]
    b = [[(*d, 3e5), (*d, 3e5), (d[0] + 0.01, d[1], d[2], 0.0)] for d in dk]
    # Los fotones de las tres, pegados en fort.72 (de la 2.ª, ninguno).
    g = [(*dk[0], 1.17e6), (5.0, 0.0, 0.0, 1.17e6), (*dk[2], 1.17e6), (0.0, 5.0, 0.0, 1.17e6)]
    tracks = [_tracks(1, b), _tracks(2, [g], fort=True)]
    s = sh.reconstruct(tracks, None, pieces=True, extended=True, n_source=3)
    assert s.exact and not s.needed
    ph = np.flatnonzero(s.particle == 2)
    assert list(s.shower[ph]) == [0, 2]


def test_two_photons_of_a_decay_outside_matter_are_separated():
    """Fuente extensa fuera de la materia: el segundo fotón de una desintegración, pegado al primero, empieza donde
    entra en la geometría, en línea recta desde donde se desintegró el núcleo (el primer punto de la β)."""
    d = (1.0, 2.0, -3.0)
    beta = [(*d, 3e5), (1.5, 2.0, 0.0, 3e5), (1.6, 2.0, 0.1, 0.0)]
    p1 = np.array([0.4, -1.0, 0.0])
    u = (p1 - np.array(d)) / np.linalg.norm(p1 - np.array(d))
    p2 = p1 + 4.0 * u
    g1 = [(2.0, 2.0, 0.0, 1.33e6), (4.0, 2.0, 5.0, 1.33e6)]
    g2 = [(*p1, 1.17e6), (*p2, 1.17e6)]  # menos energía: no se ve por la energía
    tracks = [_tracks(1, [beta]), _tracks(2, [g1 + g2], fort=True)]
    s = sh.reconstruct(tracks, None, pieces=True, extended=True, n_source=1)
    assert s.count == 3 and s.exact
    assert [int(n) for n in np.diff(s.starts)] == [3, 2, 2]


def test_phase_space_copies_are_tracks_of_their_own():
    """Las copias de IPSPLI son secundarias para penhmain (ILB(1) + 1): empiezan su trayectoria en el punto de la
    partícula del espacio de fases. ARGUS las cuenta como primarias de su lluvia; no se corta nada."""
    p = (0.5, 0.5, 6.0)
    orig = [(*p, 6.617e5), (*p, 6.617e5), (1.0, 1.0, 8.0, 5e5), (2.0, 2.0, 9.0, 5e5)]
    copy = [(*p, 6.617e5), (0.0, 0.0, 9.0, 6.617e5)]
    q = (-0.5, 0.5, 6.0)
    other = [(*q, 6.617e5), (*q, 6.617e5), (-1.0, 2.0, 9.0, 6.617e5)]
    src = [(2, 6.617e5, *p, 0), (2, 6.617e5, *q, 1)]
    s = sh.reconstruct([_tracks(2, [orig, copy, other])], None, pieces=True, source=src, n_source=2)
    assert s.count == 3 and s.exact and list(s.shower) == [0, 0, 1]
    assert list(s.generation) == [0, 0, 0] and list(s.parent) == [-1, -1, -1]


def test_mains_in_two_files_are_put_in_order():
    """Desintegraciones que empiezan con una β (electron.trk) o con un fotón (photon.trk): lo de cada una (aquí, el
    electrón Compton del fotón, entre las β) dice el orden de todas. Las lluvias son distintas: no comparten número."""
    d0 = (0.0, 0.0, 0.0)
    b0 = [(*d0, 3e5), (*d0, 3e5), (0.1, 0.0, 0.0, 0.0)]
    b2 = [(*d0, 2e5), (*d0, 2e5), (-0.1, 0.0, 0.0, 0.0)]
    g1 = [(*d0, 1.17e6), (*d0, 1.17e6), (0.0, 2.0, 0.0, 8e5), (0.0, 9.0, 0.0, 8e5)]  # empieza la 2.ª
    compton = [(0.0, 2.0, 0.0, 3.7e5), (0.0, 2.1, 0.0, 0.0)]
    tracks = [_tracks(1, [b0, compton, b2]), _tracks(2, [g1])]
    s = sh.reconstruct(tracks, None, pieces=True, source_points=np.array([d0]), n_source=3)
    assert s.exact
    assert list(s.shower) == [0, 1, 2, 1]  # β, Compton, β | fotón


# -- los cortes en disco -------------------------------------------------------------------------------------------

def test_cut_records_are_kept_while_the_tracks_do_not_change(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    _trk(run / "electron.trk", [[(0.0, 0.0, 0.0, 1e5), (1.0, 0.0, 0.0, 0.0)]])
    cs.record_cut(run, 3, {1: 2, 2: 0})
    cs.record_cut(run, 4, None, "penhmain no ha terminado")
    assert sh.read_cuts(run) == {3: {1: 2, 2: 0}}
    assert cs.failed_cuts(run) == {4: "penhmain no ha terminado"}
    data = json.loads((run / sh.CUTS_FILE).read_text(encoding="utf-8"))
    assert data["kind"] == "cortes" and data["files"] == {"electron.trk": (run / "electron.trk").stat().st_size}
    cs.record_cut(run, 4, {1: 2})  # el que no sirvió, ya sí
    assert sh.read_cuts(run) == {3: {1: 2, 2: 0}, 4: {1: 2}} and cs.failed_cuts(run) == {}
    # Si las trayectorias cambian (se ha vuelto a simular), los cortes de antes no valen.
    _trk(run / "electron.trk", [[(0.0, 0.0, 0.0, 1e5), (2.0, 0.0, 0.0, 0.0)], [(5.0, 0.0, 0.0, 1e5)]])
    assert sh.read_cuts(run) == {} and cs.failed_cuts(run) == {}
    cs.record_cut(run, 1, {1: 2})
    assert sh.read_cuts(run) == {1: {1: 2}}


def test_a_cut_must_be_the_start_of_the_whole_run(tmp_path):
    run, cut = tmp_path / "run", tmp_path / "corte"
    run.mkdir()
    cut.mkdir()
    full = [[(0.0, 0.0, 0.0, 1e5), (1.0, 0.0, 0.0, 0.0)], [(0.0, 0.0, 0.0, 1e5), (2.0, 0.0, 0.0, 0.0)]]
    _trk(run / "electron.trk", full)
    _trk(run / "fort.72", [[(0.0, 0.0, 0.0, 1e6), (0.0, 5.0, 0.0, 1e6), (0.0, 0.0, 0.0, 1e6)]], header=False)
    text = (run / "electron.trk").read_text()
    (cut / "electron.trk").write_text(text[:text.index(" \n") if " \n" in text else len(text)])
    ftext = (run / "fort.72").read_text()
    (cut / "fort.72").write_text("".join(ftext.splitlines(keepends=True)[:2]))
    pts, err = cs.check_cut(run, cut)
    assert err == "" and pts == {1: 2, 2: 2}
    (cut / "electron.trk").write_text(text.replace("1.00000E+00", "1.00001E+00"))
    pts, err = cs.check_cut(run, cut)
    assert pts is None and "no es el principio" in err
    # Si ya escribía en photon.trk, fort.72 tiene que estar entero.
    (cut / "electron.trk").write_text(text[:40])
    _trk(cut / "photon.trk", [[(9.0, 0.0, 0.0, 1e4)]])
    _trk(run / "photon.trk", [[(9.0, 0.0, 0.0, 1e4)], [(8.0, 0.0, 0.0, 1e4)]])
    pts, err = cs.check_cut(run, cut)
    assert pts is None and "fort.72" in err


def test_the_cut_input_only_changes_nsimsh(tmp_path):
    in_path, doc = _nuclide_case(tmp_path)
    plan = cs.plan_cascade(in_path, doc, _fake(tmp_path), showers=10, cap=5)
    cs.write_cascade(plan)
    run = plan.folder / plan.runs[1].folder
    cut = tmp_path / "corte"
    cut.mkdir()
    cs.write_cut_folder(run, cut, plan.files[1].items, in_path.name, "penhmain.x", 3.0)
    a = parse_in_file(run / in_path.name)
    b = parse_in_file(cut / in_path.name)
    assert sc.value(b, "NSIMSH") == [3.0] and sc.value(a, "NSIMSH") == [5.0]
    assert sc.seeds(a) == sc.seeds(b) and sc.value(a, "LTRACK") == sc.value(b, "LTRACK")
    rest_a = [ln for ln in write_in_text(a).splitlines() if not ln.startswith("NSIMSH")]
    rest_b = [ln for ln in write_in_text(b).splitlines() if not ln.startswith("NSIMSH")]
    assert rest_a == rest_b
    for name in ("caja.geo", "Co-60.nuc", "penhmain.x", cs.RUN_SCRIPT):
        assert (cut / name).is_file()
    assert os.path.samefile(cut / "caja.geo", run / "caja.geo")  # enlaces, no copias
    assert not (cut / "electron.trk").exists()


def test_the_cut_of_a_phase_space_stops_at_the_end_of_its_shower(tmp_path):
    """Con un espacio de fases, penhmain cuenta las lluvias sumando los NSHI: para parar tras la lluvia b, NSIMSH es
    su N."""
    d = tmp_path / "run"
    d.mkdir()
    rec = "{k} 6.61700E+05  0.0 0.0 6.0  0.0 0.0 1.0  1.0  1 0 0 0  {n}\n"
    (d / "fase.psf").write_text("".join(rec.format(k=2, n=n) for n in (3, 0, 2, 5)))
    doc = parse_in_file(CAJA / "caja.in")
    t = parse_in_text(write_in_text(doc), doc.path)
    for kw in ("SKPAR", "SENERG", "SPECTR", "SLINES", "SGPOL", "SCONE", "SRECTA", "SPOSIT", "SBOX"):
        t.remove(sc.kw6(kw))
    sc.set_psf_files(t, ["fase.psf"])
    (d / "caso.in").write_text(write_in_text(t), encoding="latin-1")
    assert parse_in_file(d / "caso.in").model.source.from_psf
    assert cs.cut_nsimsh(d, "caso.in", 10, 1) == 3.0
    assert cs.cut_nsimsh(d, "caso.in", 10, 2) == 5.0
    assert cs.cut_nsimsh(d, "caso.in", 10, 3) == 10.0
    assert cs.cut_nsimsh(d, "caso.in", 10, 4) is None


# -- la cascada con cortes -------------------------------------------------------------------------------------------

@pytestmark_sh
def test_a_radioactive_point_source_is_cut_until_every_shower_is_known(tmp_path):
    in_path, doc = _nuclide_case(tmp_path)
    exe = _fake(tmp_path)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=10, jobs=3, cap=5))
    assert job.wait(120)
    st = job.status
    assert st.stage == "terminada", st.text
    assert st.showers_done == 10 and st.cuts_total == 8 and st.cuts_done == 8
    assert "8 cortes" in st.text
    info = cs.find_cascades(in_path.parent)[0]
    assert not [p for p in info.path.iterdir() if p.name.startswith(cs.CUT_PREFIX)]  # las carpetas, borradas
    for d in info.run_paths:
        assert sorted(sh.read_cuts(d)) == [1, 2, 3, 4]
    s = sh.load_cascade(info)
    assert s.exact and s.n_showers == 10 and s.count == 20
    beta = np.flatnonzero(s.particle == 1)
    ph = np.flatnonzero(s.particle == 2)
    assert list(s.shower[beta]) == list(range(10)) and list(s.shower[ph]) == list(range(10))
    assert cs.cascade_progress(info)[0] == 10 and cs.unverified_showers(info) == 0
    # Otra vez: ya está todo (ni ejecuciones ni cortes).
    job2 = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=10, jobs=3, cap=5))
    assert job2.wait(60) and "ya simulada" in job2.status.text and job2.status.cuts_total == 0


@pytestmark_sh
def test_cuts_left_halfway_are_finished_later(tmp_path, monkeypatch):
    in_path, doc = _nuclide_case(tmp_path)
    exe = _fake(tmp_path)
    monkeypatch.setenv("CUT_SLEEP", "30")
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=10, jobs=2, cap=5))
    t0 = time.time()
    while job.status.cuts_total == 0 and time.time() - t0 < 30:
        time.sleep(0.1)
    time.sleep(0.5)
    job.cancel()
    assert job.wait(30) and job.status.stage == "cancelada"
    info = cs.find_cascades(in_path.parent)[0]
    # penhmain ha terminado las dos ejecuciones, pero aún no se sabe de qué lluvia es cada fotón: no cuentan.
    assert all(cs.run_state(d).status == "terminada" for d in info.run_paths)
    assert cs.cascade_progress(info)[0] == 0 and cs.unverified_showers(info) == 10
    assert sh.load_cascade(info).count == 0 and sh.load_cascade(info, only_finished=False).pending == 10
    assert not [p for p in info.path.iterdir() if p.name.startswith(cs.CUT_PREFIX)]
    # Una carpeta de un corte que se quedó (ARGUS se cerró de golpe): se borra al seguir.
    stale = info.path / f"{cs.CUT_PREFIX}run-01-3-xyz"
    stale.mkdir()
    (stale / "electron.trk").write_text("de mentira\n")
    monkeypatch.setenv("CUT_SLEEP", "")
    job2 = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=10, jobs=2, cap=5))
    assert job2.wait(120) and job2.status.stage == "terminada", job2.status.text
    assert job2.status.folder == info.path and job2.status.cuts_done == 8
    assert not stale.exists()
    assert sh.load_cascade(info).exact and sh.load_cascade(info).n_showers == 10


@pytestmark_sh
def test_a_penhmain_that_does_not_repeat_itself_is_said(tmp_path, monkeypatch):
    in_path, doc = _nuclide_case(tmp_path)
    exe = _fake(tmp_path)
    monkeypatch.setenv("BAD", "1")
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=10, jobs=2, cap=5))
    assert job.wait(120)
    st = job.status
    assert st.stage == "error" and "no se ha podido saber de qué lluvia es cada trayectoria" in st.text
    assert "mismas semillas" in st.text
    info = cs.find_cascades(in_path.parent)[0]
    assert sh.load_cascade(info).count == 0  # nada dudoso a la vista
    assert cs.failed_cuts(info.run_paths[0])


# -- las trayectorias de la simulación (no la cascada) ------------------------------------------------------------

def test_the_simulation_tracks_of_a_radioactive_source_are_separated(tmp_path):
    """La vista «Trayectorias» de una simulación con una fuente radiactiva: cada partícula en la suya (sin el tramo que
    uniría el final de un fotón con el principio del siguiente) y también las de fort.72."""
    in_path, _doc = _nuclide_case(tmp_path)
    d = in_path.parent
    s0 = (0.0, 0.0, -5.0)
    _trk(d / "electron.trk", [[(*s0, 3e5), (*s0, 3e5), (0.1, 0.0, -4.5, 0.0)],
                              [(*s0, 2e5), (*s0, 2e5), (-0.1, 0.0, -4.5, 0.0)]])
    _trk(d / "fort.72", [[(*s0, 1.17e6), (0.0, 1.0, 5.0, 1.17e6), (*s0, 1.17e6), (0.0, -1.0, 5.0, 1.17e6)]],
         header=False)
    sep = sh.separate_tracks(d, in_path.name)
    assert sep is not None and [t.particle for t in sep] == [1, 2]
    assert sep[0].count == 2 and sep[1].count == 2 and list(np.diff(sep[1].starts)) == [2, 2]
    assert sh.separate_tracks(d, in_path.name) is sep  # de la caché, mientras no cambien
    # Con una fuente de una partícula por lluvia no hay nada pegado: los .trk, tal cual.
    shutil.copy(CAJA / "caja.in", d / "haz.in")
    assert sh.separate_tracks(d, "haz.in") is None


def test_the_tracks_view_keeps_the_separated_tracks(qtbot):
    from argus.ui.results.special_views import TracksView

    v = TracksView()
    qtbot.addWidget(v)
    raw = _tracks(2, [[(0.0, 0.0, 0.0, 1e6), (1.0, 0.0, 0.0, 1e6), (0.0, 0.0, 0.0, 1e6), (0.0, 1.0, 0.0, 1e6)]])
    sep = _tracks(2, [[(0.0, 0.0, 0.0, 1e6), (1.0, 0.0, 0.0, 1e6)], [(0.0, 0.0, 0.0, 1e6), (0.0, 1.0, 0.0, 1e6)]])
    v.set_data(raw, "Trayectorias de fotones", context={"siblings": [sep]})
    assert v.tracks[2] is sep and v.tracks[2].count == 2


def test_the_results_window_shows_each_decay_particle_in_its_track(qtbot, monkeypatch, tmp_path):
    """La ventana de resultados de una simulación con fuente radiactiva: «Trayectorias» con cada partícula en la suya
    (también las de fort.72, que no tienen .trk)."""
    from PySide6.QtWidgets import QMessageBox

    from argus.ui.results.window import ResultsWindow
    from argus.ui.settings import app_settings
    from test_results_ui import _refs, _wait

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    in_path, _doc = _nuclide_case(tmp_path)
    d = in_path.parent
    s0 = (0.0, 0.0, -5.0)
    _trk(d / "electron.trk", [[(*s0, 3e5), (*s0, 3e5), (0.1, 0.0, -4.5, 0.0)],
                              [(*s0, 2e5), (*s0, 2e5), (-0.1, 0.0, -4.5, 0.0)]])
    _trk(d / "fort.72", [[(*s0, 1.17e6), (0.0, 1.0, 5.0, 1.17e6), (*s0, 1.17e6), (0.0, -1.0, 5.0, 1.17e6)]],
         header=False)
    app_settings().clear()
    w = ResultsWindow(settings=app_settings())
    qtbot.addWidget(w)
    w.open_folder(d)
    _wait(qtbot, w)
    for _label, r in _refs(w):
        if r.output.startswith("trk"):
            w.select(r)
            _wait(qtbot, w)
            break
    else:
        raise AssertionError("sin trayectorias en el árbol")
    tv = w.views.currentWidget()
    assert set(tv.tracks) == {1, 2}
    assert tv.tracks[1].count == 2 and tv.tracks[2].count == 2
