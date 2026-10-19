"""Undécima revisión (0.17.3): la cascada (lo que no necesita la ventana).

Cada prueba falla con el código de 0.17.2."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from argus.penhan import cascade as cs
from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_file, parse_in_text, write_in_text
from argus.results import shower as sh
from argus.results.data import Tracks
from test_cascade import NATIVE, _case, _fake

sh_only = pytest.mark.skipif(sys.platform == "win32", reason="penhmain de mentira con /bin/sh")

HEAD = " # X/cm         Y/cm         Z/cm         E/eV"


def _psf_case(tmp_path: Path, records: list[tuple], extra: tuple[str, ...] = ()) -> tuple[Path, object]:
    """El caso de la caja con un espacio de fases: ``records`` = [(KPAR, E, x, y, z, WGHT, NSHI)]."""
    in_path, doc = _case(tmp_path)
    lines = [" # PSF de prueba", " #/KPAR  E  X  Y  Z  U  V  W  WGHT  ILB(1:4)  NSHI"]
    for kp, e, x, y, z, w, nshi in records:
        lines.append(f" {kp}  {e:.5E}  {x:.5E}  {y:.5E}  {z:.5E}  0 0 1  {w:.5E}  1 0 0 0 {nshi}")
    (in_path.parent / "fase.psf").write_text("\n".join(lines) + "\n")
    d = parse_in_text(write_in_text(doc), doc.path)
    for kw in ("SKPAR", "SENERG", "SPECTR", "SLINES", "SGPOL"):
        d.remove(sc.kw6(kw))
    sc.set_psf_files(d, ["fase.psf"])
    text = write_in_text(d)
    if extra:
        text = text.replace("IPSFN ", "\n".join(extra) + "\nIPSFN ", 1)
    in_path.write_bytes(text.encode("latin-1"))
    doc = parse_in_file(in_path)
    assert doc.model.source.from_psf
    return in_path, doc


def _jumps(n: int) -> list[tuple]:
    """Fotones de 1 MeV en x = k·0,01 cm; una de cada 7 lluvias, con NSHI 2 (la anterior no llegó al fichero)."""
    return [(2, 1.0e6, k * 0.01, 0.0, 0.0, 1.0, 2 if k % 7 == 0 else 1) for k in range(1, n + 1)]


def _simulate_psf_run(run_dir: Path, case: str, showers: int) -> None:
    """Lo que escribiría penhmain en una ejecución con un espacio de fases (sin ruleta ni copias): una trayectoria
    por cada partícula del fichero que lee (de su punto, con su energía), en orden."""
    src = sh.run_source(run_dir, case, showers)
    by_kind: dict[int, list[str]] = {}
    for kp, e, x, y, z, _g in src.particles:
        lines = by_kind.setdefault(int(kp), [HEAD])
        if len(lines) > 1:
            lines.append(" ")
        lines.append(f"  {x:.5E}  {y:.5E}  {z:.5E}  {e:.5E}")
        lines.append(f"  {x:.5E}  {y:.5E}  {z + 1.0:.5E}  {0.5 * e:.5E}")
    names = {1: "electron.trk", 2: "photon.trk", 3: "positron.trk"}
    for kp, lines in by_kind.items():
        (run_dir / names[kp]).write_text("\n".join(lines) + "\n")
    (run_dir / cs.OUTPUT).write_text("   No. of simulated showers = 1.0E+02\n   *** END ***\n")


# -- espacio de fases: las lluvias que se piden son las que se ven -----------------------------------------------------

def test_a_phase_space_cascade_has_the_showers_asked_and_none_is_empty(tmp_path):
    """Con NSHI > 1 (lo normal en el espacio de fases de un detector de impacto), cada ejecución tenía sus lluvias en
    el N de penhmain y el visor las numeraba por grupos del fichero: 120 pedidas eran 103 (y el estado decía 120),
    con lluvias vacías al final de cada ejecución. Ahora las lluvias son grupos: las 120 y ninguna vacía."""
    in_path, doc = _psf_case(tmp_path, _jumps(200))
    plan = cs.plan_cascade(in_path, doc, _fake(tmp_path), showers=120, cap=50)
    cs.write_cascade(plan)
    info = cs.read_manifest(plan.folder)
    for r, d in zip(info.runs, info.run_paths):
        _simulate_psf_run(d, info.case, r.showers)
    s = sh.load_cascade(info)
    assert s.exact
    assert s.n_showers == 120
    assert sorted(set(int(k) for k in s.shower)) == list(range(120))  # ninguna vacía
    done, total, _ = cs.cascade_progress(info)
    assert done == total == 120


def test_each_phase_space_run_reads_a_chunk_that_ends_with_its_last_shower(tmp_path):
    """La primera ejecución leía el fichero del caso entero: con la ruleta rusa (WGTWIN; en penhmain, WGMIN = 0,5
    aunque el .in no lo diga), al matar la partícula de su última lluvia penhmain seguía leyendo en la misma lluvia y
    acababa más allá de NSIMSH (comprobado con penhmain: N = 57 con NSIMSH = 50); esa trayectoria iba a la última
    lluvia de la ejecución dándose por exacta, y la partícula se simulaba otra vez en la segunda. Ahora cada ejecución
    (también la primera) y cada corte leen un trozo que acaba en su última lluvia: penhmain no puede pasarse."""
    recs = [(2, 1.0e6, k * 0.01, 0.0, 0.0, 0.2, 1) for k in range(1, 121)]
    in_path, doc = _psf_case(tmp_path, recs)
    plan = cs.plan_cascade(in_path, doc, _fake(tmp_path), showers=120, cap=50)
    assert all(c is not None for c in plan.chunks)
    assert all(sc.psf_files(d) == [cs.PSF_CHUNK] for d in plan.docs)
    body = [ln for ln in plan.chunks[0].decode("latin-1").splitlines() if not ln.startswith("#")]
    assert len(body) == 50 and body[-1].split()[2] == f"{0.5:.5E}"
    cs.write_cascade(plan)
    run1 = plan.folder / "run-01"
    chunk = cs.cut_psf(run1, in_path.name, plan.runs[0].showers, 7).decode("latin-1")
    assert [ln for ln in chunk.splitlines() if not ln.startswith("#")] == body[:7]
    cut = plan.folder / "corte"
    cut.mkdir()
    cs.write_cut_folder(run1, cut, plan.files[0].items, in_path.name, "penhmain.x", 7.0, chunk.encode("latin-1"))
    assert sc.psf_files(parse_in_file(cut / in_path.name)) == [cs.PSF_CHUNK]
    assert (cut / cs.PSF_CHUNK).read_text(encoding="latin-1") == chunk


def test_the_chunk_header_has_no_question_marks(tmp_path):
    """«lluvias 51?100»: el guion largo no cabe en latin-1."""
    in_path, doc = _psf_case(tmp_path, _jumps(120))
    plan = cs.plan_cascade(in_path, doc, _fake(tmp_path), showers=100, cap=50)
    head = plan.chunks[1].decode("latin-1").splitlines()[0]
    assert head.startswith("#") and "?" not in head


def test_phase_space_particles_that_start_alike_are_not_all_put_in_the_first_shower():
    """Con un espacio de fases de partículas que salen todas del mismo punto con la misma energía (escrito a mano
    desde una fuente puntual, o los fotones de un haz fino que llegan sin chocar), cada primaria se quedaba con la
    primera partícula del fichero: todas sus trayectorias, en la lluvia 1, dándose por exactas. Si puede haber
    ruleta rusa o copias, no se sabe cuál es (lo dicen los cortes); si no, cada una es la siguiente."""
    n = 6
    src = [(2, 6.617e5, 0.0, 0.0, 6.0, k) for k in range(n)]
    pts = np.array([[0.0, 0.0, 6.0, 6.617e5], [0.0, 0.0, 9.0, 6.617e5]])
    tracks = [Tracks(2, np.vstack([pts] * n), np.arange(0, 2 * n + 1, 2))]
    s = sh.reconstruct(tracks, pieces=True, source=src, n_source=n)
    assert not (s.exact and np.all(s.shower == 0))
    assert not s.exact and s.needed  # (que lo digan los cortes)
    s = sh.reconstruct(tracks, pieces=True, source=src, n_source=n, one_each=True)
    assert s.exact and list(s.shower) == list(range(n))


def test_the_weight_window_says_whether_each_particle_gives_one_track(tmp_path):
    """penhmain aplica WGTWIN 0,5 1,5 aunque el .in no lo tenga (PMRDR0) y divide en IPSPLI copias."""
    _in, doc = _psf_case(tmp_path, _jumps(3))
    assert cs.psf_window(doc) == (0.5, 1.5, 1)
    assert cs.psf_one_each([1.0, 0.7, 1.5], cs.psf_window(doc))
    assert not cs.psf_one_each([1.0, 0.2], cs.psf_window(doc))  # ruleta
    assert cs.psf_one_each([4.0], (0.5, 1.5, 1))  # (sin IPSPLI no hay copias)
    assert not cs.psf_one_each([4.0], (0.5, 1.5, 10))
    d = parse_in_text(write_in_text(doc).replace("IPSFN ", "WGTWIN 2 5\nIPSPLI -3\nIPSFN ", 1), doc.path)
    assert cs.psf_window(d) == (2.0, 5.0, 3)


def test_penhmain_reads_136_characters_of_each_phase_space_line():
    long = " 2  6.6170000000E+05  8.5189800000E-04 -1.3756400000E-04  6.0000000000E+00  7.7445200000E-05 " \
           "-1.2505800000E-05  1.0000000000E+00  1.0000000000E+00 1 0 0 0 1"
    assert len(long) > 136 and cs.psf_record(long) is None  # (penhmain no la lee: «The file is empty or corrupted»)
    assert cs.psf_record(" 2 1.0E6 0 0 6 0 0 1 1.0 1 0 0 0 1") == (2, 1.0e6, 0.0, 0.0, 6.0, 1, 1.0)
    assert cs.psf_record(" 2 1.0E6 0 0 6 0 0 1 1.0 1 0 0 0.5 1") is None  # (ILB entero: si no, READ falla)


# -- la firma: el contenido, no la fecha -------------------------------------------------------------------------------

def test_a_case_copied_without_its_dates_keeps_its_cascade(tmp_path):
    """La firma usaba la fecha de los .mat, la geometría…: al copiar la carpeta sin las fechas (cp -r, git, un pendrive
    FAT32) la cascada era «de otro caso» y «Simular…» la repetía entera (con las mismas trayectorias)."""
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    plan = cs.plan_cascade(in_path, doc, exe, showers=10, cap=50)
    cs.write_cascade(plan)
    info = cs.read_manifest(plan.folder)
    assert cs.cascade_matches(info, in_path, doc, exe)
    for p in in_path.parent.iterdir():
        if p.is_file():
            os.utime(p, (1.0e9, 1.0e9))
    getattr(cs, "_CONTENT", {}).clear()
    assert cs.cascade_matches(info, in_path, doc, exe)
    mat = in_path.parent / doc.model.materials[0].fname.strip()
    mat.write_text("material de mentira, otro\n")
    assert not cs.cascade_matches(info, in_path, doc, exe)


def test_the_content_of_a_huge_file_is_sampled(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_SAMPLED", 1000)
    p = tmp_path / "grande.psf"
    p.write_bytes(bytes(range(256)) * 40)
    a = cs.content_sha1(p)
    p.write_bytes(bytes(range(256)) * 40)  # (mismo contenido, otra fecha)
    assert cs.content_sha1(p) == a
    data = bytearray(p.read_bytes())
    data[-1] ^= 1
    p.write_bytes(bytes(data))
    assert cs.content_sha1(p) != a


# -- ejecuciones sin trayectorias y cortes huérfanos -------------------------------------------------------------------

@sh_only
def test_a_finished_run_without_any_track_is_not_a_finished_shower(tmp_path):
    """Una cascada terminada cuyas ejecuciones no tienen ningún .trk (borrados, o un penhmain que no escribe las de
    LTRACK) se daba por «5 de 5 lluvias simuladas» y «Simular…» respondía «ya simulada». Ahora no cuentan, el visor lo
    dice y «Simular…» las repite."""
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=5, cap=50, jobs=1))
    assert job.wait(60) and job.status.stage == "terminada"
    info = cs.find_cascades(in_path.parent)[0]
    for p in info.run_paths[0].glob("*.trk"):
        p.unlink()
    done, total, _ = cs.cascade_progress(info)
    assert (done, total) == (0, 5)
    assert cs.untracked_runs(info) == ["run-01"]
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=5, cap=50, jobs=1))
    assert job.wait(60) and job.status.stage == "terminada", job.status.text
    assert "ya simulada" not in job.status.text
    assert cs.cascade_progress(info)[:2] == (5, 5) and not cs.untracked_runs(info)


@sh_only
def test_a_cut_folder_left_by_a_closed_session_is_removed_also_when_the_cascade_is_finished(tmp_path):
    """Si ARGUS se cerraba de golpe durante un corte, su carpeta («argus-corte-…», con los .trk y el volcado del corte)
    se quedaba para siempre: una cascada terminada ya no las miraba."""
    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=5, cap=50, jobs=1))
    assert job.wait(60) and job.status.stage == "terminada"
    info = cs.find_cascades(in_path.parent)[0]
    left = info.path / f"{cs.CUT_PREFIX}run-01-3-abcd"
    left.mkdir()
    (left / "electron.trk").write_text(HEAD + "\n")
    (left / cs.PID_FILE).write_text("999999999")  # (un proceso que ya no existe)
    (left / cs.RC_FILE).write_text("cancelada")
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=5, cap=50, jobs=1))
    assert job.wait(60) and "ya simulada" in job.status.text
    assert not left.exists()


# -- puntos dañados ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [np.nan, 1.0e30, np.inf])
def test_one_damaged_point_does_not_spoil_the_whole_cascade(bad):
    """Un punto NaN o de 1e30 cm en un .trk: el origen de los vértices (float32, relativos a él) era NaN o 5e29 y la
    cascada entera desaparecía o quedaba aplastada en el visor; el tiempo máximo, 1,7e29 ns."""
    from argus.render.track_layer import layer_from_showers

    el = np.array([[0.0, 0.0, 0.0, 1e6], [0.0, 0.0, 1.0, 8e5], [0.0, 0.0, 2.0, 6e5], [0.0, 0.0, 3.0, 4e5]])
    el2 = el + [1.0, 0.0, 0.0, 0.0]
    el2[2, 2] = bad
    s = sh.reconstruct([Tracks(1, np.vstack([el, el2]), np.array([0, 4, 8]))], primary=1, n_source=2)
    L = layer_from_showers(s)
    assert np.isfinite(L.origin).all() and np.all(np.abs(L.origin) < 10)
    assert np.isfinite(s.time).all() and s.t_max < 1.0  # (ns)
    assert s.bad_points == 1
    v = L.vertices[:, :3].astype(np.float64) + L.origin
    assert np.allclose(v[:4, 2], [0, 1, 2, 3])  # la primera, intacta
    assert len(L.seg_a) == 3 + 1  # sus tres tramos y el de la segunda que no toca el punto dañado
    assert np.isfinite(L.t_max) and L.t_max < 1.0
    lo, hi = s.bounds()
    assert np.isfinite(lo).all() and np.isfinite(hi).all() and hi[2] <= 3.0


def test_run_counts_of_an_index_of_before(tmp_path):
    """Los índices de 0.17.2 no dicen las lluvias que se ven (con un espacio de fases, su «showers» era el N de
    penhmain): se cuentan los grupos que lee cada ejecución."""
    in_path, doc = _psf_case(tmp_path, _jumps(60))
    plan = cs.plan_cascade(in_path, doc, _fake(tmp_path), showers=60, cap=50)
    cs.write_cascade(plan)
    m = plan.folder / cs.MANIFEST
    j = json.loads(m.read_text(encoding="utf-8"))
    for r in j["runs"]:
        r.pop("groups")
    m.write_text(json.dumps(j), encoding="utf-8")
    info = cs.read_manifest(plan.folder)
    assert all(r.groups is None for r in info.runs)
    assert cs.run_counts(info) == [r.groups for r in plan.runs]


# -- el espacio de fases como lo lee RDPSF ---------------------------------------------------------------------------

_LONG = ("  2  6.6170000000E+05  8.5189800000E-04 -1.3756400000E-04  6.0000000000E+00  7.7445200000E-05"
         " -1.2505800000E-05  1.0000000000E+00  1.0000000000E+00 1 0 0 0 1\n")
_SHORT = "  2  6.6170E+05  0.0  0.0  6.0  0.0  0.0  1.0  1.0 1 0 0 0 1\n"


def _raw_psf_case(tmp_path: Path, text: str) -> Path:
    """El caso de la caja con un espacio de fases escrito tal cual."""
    in_path, _doc = _psf_case(tmp_path, [])
    (in_path.parent / "fase.psf").write_text(text, encoding="latin-1")
    return in_path


def test_a_phase_space_file_with_no_line_penhmain_can_read_is_3117(tmp_path):
    """Líneas de 157 caracteres (con más cifras de las que escribe penhmain): RDPSF lee los 136 primeros de cada una,
    no le salen sus 14 valores y las salta todas: «The file is empty or corrupted» (3117). ARGUS no decía nada; la
    cascada acababa con ese error en cada ejecución."""
    from argus.core.diagnostics import Severity
    from argus.penhan.validate import validate_case

    assert len(_LONG.rstrip()) > 136
    p = _raw_psf_case(tmp_path, "# cabecera\n" + _LONG * 3)
    errs = validate_case(parse_in_file(p), None, p.parent).errors
    assert 3117 in [e.code for e in errs]
    assert any("136" in e.message for e in errs if e.code == 3117)
    # Con alguna que sí se lee no es un error: se avisa de las que pasan de 136 caracteres.
    (p.parent / "fase.psf").write_text("# cabecera\n" + _LONG + _SHORT)
    d = validate_case(parse_in_file(p), None, p.parent)
    assert 3117 not in [e.code for e in d.errors]
    assert any("1 línea" in w.message and "136" in w.message for w in d if w.severity == Severity.WARNING)
    (p.parent / "fase.psf").write_text("# cabecera\n" + _SHORT * 2)
    d = validate_case(parse_in_file(p), None, p.parent)
    assert not any("136" in w.message for w in d)


@pytest.mark.oracle
@pytest.mark.parametrize("psf, code", [(_LONG * 3, 3117), (_LONG + _SHORT, None)])
def test_long_phase_space_lines_agree_with_penhmain(tmp_path, psf, code):
    from argus.oracle.compare_in import compare_checks
    from argus.oracle.probe import find_sim_probe, run_sim_probe
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry

    if find_sim_probe() is None:
        pytest.skip("Sonda de las comprobaciones de penhmain no compilada")
    p = _raw_psf_case(tmp_path, "# cabecera\n" + psf)
    doc = parse_in_file(p)
    work = p.parent
    geo = compile_geometry(load_geometry(work / doc.model.geomfn), base_dir=work)
    res = run_sim_probe(p.read_bytes().decode("latin-1"), {f.name: str(f) for f in work.iterdir() if f.is_file()},
                        timeout=30.0)
    assert not compare_checks(doc, geo, work, res)
    assert res.iretrn == 3117 if code else res.iretrn != 3117
