"""Quinta revisión (0.12.2): leer y combinar resultados de penhmain.

* Casos distintos (o copias con las mismas semillas) en subcarpetas no se combinan como si fueran ejecuciones en
  paralelo del mismo caso (la carpeta DJ de Rafael: seis espectros distintos con las mismas semillas).
* Una copia renombrada de un resultado (``E100_penhmain-res.dat``) no tapa el de penhmain.
* Las medias por destino del resumen se combinan con las primarias de ese destino (como penhmain-sum).
* Con RESUME, el final por TIME no se deduce del tiempo del resumen; sin DUMPP, penhmain vuelca cada día.
* Ficheros a medio escribir durante un volcado: se esperan (``IncompleteFile``) en vez de enseñarlos a medias.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from argus.results.combine import Incompatible, combine_summaries
from argus.results.folder import ResultsFolder, aggregate_state, dump_period, kind_of
from argus.results.readers import FormatError, IncompleteFile, read_output
from argus.results.store import ResultsStore
from argus.results.summary import read_summary, summary_complete
from conftest import CORPUS

RES = CORPUS / "resultados"


def _old(path: Path, age: float = 3600.0) -> None:
    t = time.time() - age
    os.utime(path, (t, t))


def _age_all(folder: Path, age: float = 3600.0) -> None:
    for p in folder.rglob("*"):
        if p.is_file():
            _old(p, age)


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    _age_all(dst)
    return dst


# -- casos distintos no son réplicas ------------------------------------------------------------------------------

def test_parallel_runs_of_the_same_case_are_combined(tmp_path):
    par = _copy(RES / "paralelo", tmp_path / "paralelo")
    shutil.rmtree(par / "suma")
    f = ResultsFolder(par)
    assert f.combines and not f.is_group and f.not_replicas is None and f.is_parallel
    s = ResultsStore().get(f, "res")
    assert s.showers == pytest.approx(18000)


def test_different_cases_are_not_combined(tmp_path):
    parent = tmp_path / "DJ"
    parent.mkdir()
    _copy(RES / "caja", parent / "caja")
    _copy(RES / "cilindro", parent / "cilindro")
    f = ResultsFolder(parent)
    assert f.is_group and not f.combines and not f.is_parallel
    assert "no simulan el mismo caso" in f.not_replicas
    with pytest.raises(ValueError):
        ResultsStore().get(f, "res")  # la carpeta de arriba no tiene resultados propios ni combinados
    text = aggregate_state(f).text
    assert text.startswith("2 casos") and "No se combinan" in text


def test_same_case_with_different_spectra_is_not_combined(tmp_path):
    # Como DJ: el mismo .in salvo el espectro (y las mismas semillas).
    parent = tmp_path / "DJ"
    parent.mkdir()
    a = _copy(RES / "paralelo" / "run-01", parent / "a")
    b = _copy(RES / "paralelo" / "run-02", parent / "b")
    for d in (a, b):
        (d / "penhmain.dat").unlink()  # sin el registro: se mira el .in
    txt = (b / "run-02.in").read_text(encoding="latin-1")
    assert "SLINES 6.617E5 0.851" in txt
    (b / "run-02.in").write_text(txt.replace("SLINES 6.617E5 0.851", "SLINES 6.617E5 0.951"), encoding="latin-1")
    _age_all(parent)
    f = ResultsFolder(parent)
    assert f.is_group and "no simulan el mismo caso" in f.not_replicas


def test_copies_with_the_same_seeds_are_not_independent(tmp_path):
    parent = tmp_path / "copias"
    parent.mkdir()
    _copy(RES / "caja", parent / "caja")
    _copy(RES / "caja", parent / "caja - copia")
    f = ResultsFolder(parent)
    assert f.is_group and "mismas semillas" in f.not_replicas


def test_combine_refuses_different_cases():
    a = read_summary(RES / "caja" / "penhmain-res.dat")
    b = read_summary(RES / "cilindro" / "penhmain-res.dat")
    with pytest.raises(Incompatible):
        combine_summaries([a, b])


# -- copias renombradas ------------------------------------------------------------------------------------------

def test_renamed_copy_does_not_hide_the_current_result(tmp_path):
    d = _copy(RES / "caja", tmp_path / "caja")
    old = (d / "depth-dose.dat").read_text(encoding="latin-1")
    rows = []
    for ln in old.splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            v = ln.split()
            v[1] = f"{2 * float(v[1]):.6E}"
            ln = "  ".join(v)
        rows.append(ln)
    (d / "depth-dose-6000.dat").write_text("\n".join(rows) + "\n", encoding="latin-1")
    shutil.copy(d / "penhmain-res.dat", d / "E100_penhmain-res.dat")
    _age_all(d)
    f = ResultsFolder(d)
    keys = [o.key for o in f.files]
    assert len(keys) == len(set(keys))  # cada fichero, su clave
    assert f.find("res").path.name == "penhmain-res.dat"
    assert f.find("depth-dose").path.name == "depth-dose.dat"
    copy = next(o for o in f.files if o.path.name == "depth-dose-6000.dat")
    assert copy.copy and copy.key == "depth-dose~depth-dose-6000.dat" and kind_of(copy.key) == "depth-dose"
    assert "depth-dose-6000.dat" in copy.label and "depth-dose.dat" in f.find("depth-dose").label
    st = ResultsStore()
    main = st.load(f.find("depth-dose"))
    other = st.load(f.find(copy.key))
    assert other.series[0].y.max() == pytest.approx(2 * main.series[0].y.max(), rel=1e-5)


def test_without_the_default_name_the_most_recent_is_the_result(tmp_path):
    d = _copy(RES / "caja", tmp_path / "caja")
    (d / "depth-dose.dat").rename(d / "a_depth-dose.dat")
    shutil.copy(d / "a_depth-dose.dat", d / "b_depth-dose.dat")
    _old(d / "a_depth-dose.dat", 7200)
    _old(d / "b_depth-dose.dat", 3600)
    f = ResultsFolder(d)
    assert f.find("depth-dose").path.name == "b_depth-dose.dat"


# -- medias por destino ----------------------------------------------------------------------------------------------

def test_per_destination_means_match_penhmain_sum():
    f = ResultsFolder(RES / "paralelo")
    runs = [r for r in f.runs if r.path.name.startswith("run-")]
    st = ResultsStore()
    combined = combine_summaries([st.summary(r) for r in runs])
    ref = read_summary(RES / "paralelo" / "suma" / "penhmain-res.dat")
    for name in ("track_length", "final_energy", "polar_cosine", "polar_angle"):
        for w, m in getattr(ref, name).items():
            got = getattr(combined, name)[w]
            assert got.value == pytest.approx(m.value, rel=1e-5), (name, w)
            assert got.err == pytest.approx(m.err, rel=0.05), (name, w)
    for w, d in ref.events.items():
        for k, m in d.items():
            assert combined.events[w][k].value == pytest.approx(m.value, rel=1e-5, abs=1e-12), (w, k)


def test_a_destination_without_primaries_counts_as_zero():
    s1 = read_summary(RES / "caja" / "penhmain-res.dat")
    s2 = read_summary(RES / "caja" / "penhmain-res.dat")
    s2.primaries = dict(s2.primaries, down=0.0)
    from argus.results.summary import Measure

    s2.final_energy = dict(s2.final_energy, down=Measure(0.0, 0.0, "eV"))
    out = combine_summaries([s1, s2])
    assert out.final_energy["down"].value == pytest.approx(s1.final_energy["down"].value)


# -- estado: RESUME, TIME y DUMPP --------------------------------------------------------------------------------------

def _edit_in(path: Path, drop: tuple[str, ...], add: list[str]) -> None:
    lines = [ln for ln in path.read_text(encoding="latin-1").splitlines(keepends=True)
             if not ln.startswith(drop)]
    end = next((i for i, ln in enumerate(lines) if ln.startswith("END")), len(lines))
    lines[end:end] = [a + "\n" for a in add]
    path.write_text("".join(lines), encoding="latin-1")


def test_time_limit_with_resume_is_not_the_end(tmp_path):
    d = _copy(RES / "caja", tmp_path / "caja")
    (d / "penhmain.dat").unlink()  # sin el «END» del registro
    (d / "run.log").unlink()
    s = read_summary(d / "penhmain-res.dat")
    _edit_in(d / "caja.in", ("NSIMSH", "TIME", "RESUME"),
             ["NSIMSH 1e9", f"TIME   {max(s.time_total / 2, 1.0):g}"])
    _age_all(d, 30)
    assert ResultsFolder(d).state().status == "terminada"  # sin RESUME: el tiempo de TIME se ha acabado
    _edit_in(d / "caja.in", ("RESUME",), ["RESUME dump.dmp"])
    _age_all(d, 30)
    st = ResultsFolder(d).state()
    assert st.status == "simulando", st.text  # con RESUME, el tiempo del resumen suma el de las anteriores


def test_dump_period_like_penhmain(tmp_path):
    assert dump_period(None) == 86400.0  # DUMPP = 2e9 si no se dice: vuelca cada día
    assert dump_period(10) == 30.0 and dump_period(600) == 600.0 and dump_period(1e10) is None
    d = _copy(RES / "caja", tmp_path / "caja")
    (d / "penhmain.dat").unlink()
    (d / "run.log").unlink()
    _edit_in(d / "caja.in", ("NSIMSH", "DUMPP"), ["NSIMSH 1e9"])
    _age_all(d, 3600)
    assert ResultsFolder(d).state().status == "simulando"  # hace una hora: aún no tocaba volcar
    _edit_in(d / "caja.in", ("DUMPP",), ["DUMPP  60"])
    _age_all(d, 3600)
    assert ResultsFolder(d).state().status == "parada"


# -- ficheros a medio escribir ---------------------------------------------------------------------------------------

def test_half_written_files_are_waited_for(tmp_path):
    d = _copy(RES / "caja", tmp_path / "caja")
    dd = d / "depth-dose.dat"
    full = dd.read_bytes()
    dd.write_bytes(full[: len(full) - 7])  # la última línea a medias
    with pytest.raises(IncompleteFile):
        read_output(dd)
    _old(dd)
    c = read_output(dd)  # hace una hora: es lo que hay
    assert len(c.x) >= 1
    # Vacío (penhmain lo acaba de abrir para escribirlo): ya no se reconoce por su contenido, pero sigue en la
    # carpeta con lo que era y se espera.
    f = ResultsFolder(d)
    dd.write_bytes(b"")
    f.scan()
    o = f.find("depth-dose")
    assert o is not None and o.path == dd
    with pytest.raises(IncompleteFile):
        ResultsStore().load(o)
    # El resumen a medias, reciente: se está escribiendo; viejo, se lee lo que tenga.
    res = d / "penhmain-res.dat"
    text = res.read_bytes()
    res.write_bytes(text[: len(text) // 2].rsplit(b"\n", 1)[0] + b"\n")
    assert not summary_complete(res)
    with pytest.raises(IncompleteFile):
        read_output(res)
    _old(res)
    try:
        read_output(res)
    except FormatError as exc:
        assert not isinstance(exc, IncompleteFile)


def test_fewer_points_than_before_is_still_being_written(tmp_path):
    d = _copy(RES / "caja", tmp_path / "caja")
    dd = d / "depth-dose.dat"
    st = ResultsStore()
    first = st.load(dd)
    lines = dd.read_text(encoding="latin-1").splitlines(keepends=True)
    keep = [ln for ln in lines if ln.lstrip().startswith("#")] + \
        [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")][:5]
    dd.write_text("".join(keep), encoding="latin-1")
    with pytest.raises(IncompleteFile):
        st.load(dd)
    _old(dd)
    assert len(st.load(dd).x) == 5 < len(first.x)


def test_while_penhmain_dumps_the_folder_waits(tmp_path):
    par = _copy(RES / "paralelo", tmp_path / "paralelo")
    shutil.rmtree(par / "suma")
    res = par / "run-02" / "penhmain-res.dat"
    text = res.read_bytes()
    res.write_bytes(text[: len(text) // 2].rsplit(b"\n", 1)[0] + b"\n")  # volcando: el resumen a medias
    os.utime(par / "run-02" / "depth-dose.dat")
    f = ResultsFolder(par)
    assert f.runs[1].writing and not f.runs[0].writing
    st = ResultsStore()
    with pytest.raises(IncompleteFile):
        st.combined(f, "depth-dose")  # no se combinan solo las otras dos
    with pytest.raises(IncompleteFile):
        st.get(f.runs[1], "depth-dose")
    _age_all(par)
    f = ResultsFolder(par)
    assert not f.runs[1].writing


def test_energy_equivalence_without_rows(tmp_path):
    p = tmp_path / "energy-equivalence-proton.dat"
    src = (RES / "cilindro" / "energy-equivalence-proton.dat").read_text(encoding="latin-1")
    p.write_text("".join(ln + "\n" for ln in src.splitlines() if ln.lstrip().startswith("#")), encoding="latin-1")
    _old(p)
    with pytest.raises(FormatError):
        read_output(p)


def test_psf_drops_a_half_written_particle(tmp_path):
    p = tmp_path / "psf-impdet-01.dat"
    src = (RES / "caja" / "psf-impdet.dat").read_bytes()
    p.write_bytes(src + b"  1  1.0E+05  0.1")  # una partícula a medias al final
    data = read_output(p)
    full = read_output(RES / "caja" / "psf-impdet.dat")
    assert len(data.e) == len(full.e)
    assert np.array_equal(data.e, full.e)


# -- la geometría cambiada después de simular ------------------------------------------------------------------------

def test_geometry_edited_after_simulating_is_detected(tmp_path):
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.edit import Primitive, add_primitive
    from argus.pengeom.parser import load_geometry
    from argus.pengeom.writer import write_geo_text
    from argus.results.case import is_current, load_case_geometry

    d = _copy(RES / "caja", tmp_path / "caja")
    before = load_case_geometry(d / "caja.in")
    assert before.ok and before.mismatch is None
    # Se añade un cuerpo dentro de la losa y se guarda la geometría en la misma carpeta: los números de los cuerpos
    # ya no son los de la simulación.
    doc = load_geometry(d / "caja.geo")
    geo = compile_geometry(doc, base_dir=d)
    add_primitive(doc, geo, Primitive("sphere", center=(0.0, 0.0, 2.5), radius=0.5), 2, geo.find_body("SLAB").index)
    (d / "caja.geo").write_text(write_geo_text(doc), encoding="latin-1")
    assert not is_current(before)
    after = load_case_geometry(d / "caja.in")
    assert after.ok and after.mismatch and "después de simular" in after.mismatch
    # Si penhmain vuelve a simular (escribe su geometry.rep), es la misma otra vez.
    (d / "geometry.rep").unlink()
    assert not is_current(after)
    assert load_case_geometry(d / "caja.in").mismatch is None


def test_results_window_does_not_use_a_changed_geometry(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from argus.ui.results.window import Ref, ResultsWindow
    from argus.ui.settings import app_settings

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    d = _copy(RES / "caja", tmp_path / "caja")
    geo = (d / "caja.geo").read_text(encoding="latin-1")
    assert "MATERIAL(   2)" in geo
    (d / "caja.geo").write_text(geo.replace("MATERIAL(   2)", "MATERIAL(   1)"), encoding="latin-1")
    app_settings().clear()
    w = ResultsWindow(settings=app_settings())
    qtbot.addWidget(w)
    ds = w.open_folder(d)
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=30000)
    qtbot.waitUntil(lambda: w.cases.get(ds.key) is not None, timeout=30000)
    case = w.cases[ds.key]
    assert case.mismatch
    w.select(Ref(ds.key, "res"))
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=30000)
    html = w.summary_view.browser.toHtml()
    assert "después de simular" in html
    assert w.body_dose_table(ds, w.current_data, w.current) is None
    w.select(Ref(ds.key, "dvh-bodies"))
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=30000)
    assert "después de simular" in w.last_error
    w.close()
