"""Ventana de resultados: abrir carpetas, ver cada resultado, seguir la simulación, comparar, gamma y exportar."""

from __future__ import annotations

import shutil

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from argus.results.data import Curves, Grid, PhaseSpace, Tracks  # noqa: E402
from argus.results.summary import Summary  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

RES = CORPUS / "resultados"


@pytest.fixture
def win(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.ui.results.window import ResultsWindow

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    app_settings().clear()
    w = ResultsWindow(settings=app_settings())
    qtbot.addWidget(w)
    w.resize(1400, 860)
    w.show()
    yield w
    w.close()


def _wait(qtbot, w, token=None):
    token = w._token if token is None else token
    qtbot.waitUntil(lambda: w.loaded_token >= token, timeout=30000)


def _refs(w):
    from argus.ui.results.window import ROLE, Ref

    out = []
    stack = [w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount())]
    while stack:
        it = stack.pop(0)
        r = it.data(0, ROLE)
        if isinstance(r, Ref):
            out.append((it.text(0), r))
        stack.extend(it.child(i) for i in range(it.childCount()))
    return out


def _select(qtbot, w, pred):
    for label, r in _refs(w):
        if pred(label, r):
            w.select(r)
            _wait(qtbot, w)
            return r
    raise AssertionError("no está en el árbol")


def test_every_output_opens_without_errors(qtbot, win):
    for case in ("caja", "cilindro", "esfera", "tac"):
        win.open_folder(RES / case)
        _wait(qtbot, win)
        seen = 0
        for _label, r in _refs(win):
            if r.dataset != win.primary().key:
                continue
            win.select(r)
            _wait(qtbot, win)
            assert win.last_error == "", (case, r, win.last_error)
            assert win.views.currentWidget() is not win.empty, (case, r)
            seen += 1
        assert seen >= 15, case


def test_views_by_type_and_exports(qtbot, win, tmp_path):
    from argus.ui.results.window import write_csv

    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    assert isinstance(win.current_data, Summary)  # al abrir, el resumen
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose")
    assert isinstance(win.current_data, Curves)
    assert win.views.currentWidget() is win.curves_view
    assert win.metrics.results, "medidas de la dosis en profundidad"
    cols, data = win.curves_view.table_rows()
    assert cols[0].startswith("z") and len(data[0]) == 25
    write_csv(tmp_path / "pdd.csv", cols, data)
    text = (tmp_path / "pdd.csv").read_text("utf-8").splitlines()
    assert len(text) == 26 and "," in text[1]
    for ext in ("png", "pdf", "svg"):
        win._current_canvas().export(tmp_path / f"pdd.{ext}")
        assert (tmp_path / f"pdd.{ext}").stat().st_size > 1000
    _select(qtbot, win, lambda _l, r: r.output == "3d-dose-map")
    assert isinstance(win.current_data, Grid)
    assert win.views.currentWidget() is win.map_view
    assert win.act_geo.isEnabled()
    cols, data = win.map_view.plane_rows()
    assert len(cols) == 4 and len(data[0]) == 11 * 11
    # Pulsar en el mapa saca un perfil que se mide.
    mv = win.map_view
    mv._on_click(mv.ax_map, 0.1, 0.2, 1)
    assert mv.profile is not None and win.metrics.results


def test_summary_names_bodies_from_the_geometry(qtbot, win):
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    ds = win.primary()
    qtbot.waitUntil(lambda: win.cases.get(ds.key) is not None, timeout=20000)
    html = win.summary_view.browser.toHtml()
    assert "SLAB" in html and "DETE" in html
    assert "hueso" in html  # material del detector


def test_tracks_psf_and_materials(qtbot, win):
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output.startswith("trk"))
    assert isinstance(win.current_data, Tracks)
    tv = win.views.currentWidget()
    assert set(tv.tracks) == {1, 2}  # electrones y fotones juntos
    cols, data = tv.table_rows()
    assert cols[2] == "x (cm)" and len(data[0]) > 10
    for i in range(tv.plane.count()):
        tv.plane.setCurrentIndex(i)
    tv.color.setCurrentIndex(1)
    assert tv.info.text()
    _select(qtbot, win, lambda _l, r: r.output.startswith("psf"))
    assert isinstance(win.current_data, PhaseSpace)
    pv = win.views.currentWidget()
    assert pv.showers == pytest.approx(2e4)
    for i in range(pv.what.count()):
        pv.what.setCurrentIndex(i)
        cols, data = pv.table_rows()
        assert cols and len(data[0]) > 0
    win.open_folder(RES / "cilindro")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output == "material")
    mv = win.views.currentWidget()
    i = mv.calc_particle.findData(4)
    mv.calc_particle.setCurrentIndex(i)
    mv.calc_unit.setCurrentIndex(mv.calc_unit.findData("MeV"))
    mv.calc_energy.setValue(50.0)
    assert "2.22" in mv.calc_out.text() or "2.23" in mv.calc_out.text()  # alcance CSDA de 50 MeV en agua
    mv.calc_particle.setCurrentIndex(mv.calc_particle.findData(2))
    assert "hemirreductora" in mv.calc_out.text()


def test_parallel_runs_are_combined(qtbot, win):
    from argus.results.readers import read_output

    win.open_folder(RES / "paralelo")
    _wait(qtbot, win)
    refs = _refs(win)
    assert any(r.combined for _l, r in refs) and any(r.run == 2 for _l, r in refs)
    assert not any("suma" in lab for lab, _r in refs)  # la carpeta de penhmain-sum no es una ejecución
    _select(qtbot, win, lambda _l, r: r.combined and r.output == "depth-dose")
    mine = win.current_data
    ref = read_output(RES / "paralelo" / "suma" / "depth-dose.dat")
    np.testing.assert_allclose(mine.series[0].y, ref.series[0].y, rtol=2e-6)
    assert "3 ejecuciones" in win.state_label.text()
    # La comparación ofrece las otras ejecuciones.
    _select(qtbot, win, lambda _l, r: r.run == 0 and r.output == "depth-dose")
    keys = [k for k, _t, _c in win._compare_candidates()]
    assert any(k.endswith(":1") for k in keys) and any(k.endswith(":2") for k in keys)


def test_compare_folders_and_gamma(qtbot, win):
    from argus.ui.results.gamma_dialog import GammaDialog

    win.open_folder(RES / "paralelo" / "run-01")
    _wait(qtbot, win)
    ds2 = win.add_folder(RES / "paralelo" / "run-02")
    _select(qtbot, win, lambda _l, r: r.dataset != ds2.key and r.output == "depth-dose")
    assert win.compare.checked() == [ds2.key]
    assert len(win.curves_view.shown_overlays) == 1
    for i in range(win.curves_view.mode.count()):  # diferencia, cociente, z
        win.curves_view.mode.setCurrentIndex(i)
    crit = {"dose": 0.05, "dta": 0.3, "threshold": 0.1, "local": False, "reference": "main"}
    win.compute_gamma(crit)
    dlg = GammaDialog.instance(win)
    assert dlg.result is not None and dlg.result.evaluated > 5
    assert "%" in win.compare.g_result.text()
    # Mapas: el plano que se ve contra el mismo plano del otro.
    _select(qtbot, win, lambda _l, r: r.dataset != ds2.key and r.output == "3d-dose-map")
    win.compute_gamma(crit)
    assert dlg.result.gamma.ndim == 2
    dlg.close()


def test_measured_data_and_gamma_against_them(qtbot, win, tmp_path):
    from argus.results.readers import read_output

    c = read_output(RES / "caja" / "depth-dose.dat")
    y = c.series[0].y
    lines = ["z (mm);dosis relativa"] + [f"{10 * x:.3f};{100 * v / y.max():.4f}".replace(".", ",")
                                         for x, v in zip(c.x, y)]
    meas = tmp_path / "medidas.csv"
    meas.write_text("\n".join(lines) + "\n", "utf-8")
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    ov = win.add_measured(meas)
    assert ov is not None
    np.testing.assert_allclose(ov.curves.x, c.x, atol=1e-6)  # mm → cm
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose")
    assert any(o.measured for o in win.curves_view.overlays)
    win.compute_gamma({"dose": 0.01, "dta": 0.1, "threshold": 0.1, "local": False, "reference": "main"})
    txt = win.compare.g_result.text()
    assert "normalizada a su máximo" in txt and "100.0 %" in txt


def test_follows_a_running_simulation(qtbot, win, tmp_path):
    src = RES / "caja"
    d = tmp_path / "sim"
    d.mkdir()
    for name in ("caja.in", "caja.geo", "penhmain.dat"):
        shutil.copy2(src / name, d / name)
    win.open_folder(d)
    _wait(qtbot, win)
    assert not any(r.output == "depth-dose" for _l, r in _refs(win))
    shutil.copy2(src / "depth-dose.dat", d / "depth-dose.dat")
    shutil.copy2(src / "penhmain-res.dat", d / "penhmain-res.dat")
    win.poll()
    assert any(r.output == "depth-dose" for _l, r in _refs(win))
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose")
    before = win.current_data.series[0].y.copy()
    # penhmain vuelve a escribir el fichero (DUMPP): se relee sin tocar la vista.
    text = (d / "depth-dose.dat").read_text("latin-1")
    rows = text.splitlines()
    k = next(i for i, ln in enumerate(rows) if ln.strip() and not ln.lstrip().startswith("#"))
    parts = rows[k].split()
    parts[1] = f"{2 * float(parts[1]):.6E}"
    rows[k] = "  ".join(parts)
    (d / "depth-dose.dat").write_text("\n".join(rows) + "\n", "latin-1")
    token = win._token
    win.poll()
    _wait(qtbot, win, token + 1)
    after = win.current_data.series[0].y
    assert after[0] == pytest.approx(2 * before[0], rel=1e-5)


def test_main_window_menu_opens_the_case_results(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow
    from argus.ui.settings import remember_sim_folder, results_folder_for

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    case = tmp_path / "caso"
    case.mkdir()
    for name in ("caja.in", "caja.geo"):
        shutil.copy2(RES / "caja" / name, case / name)
    sim = tmp_path / "sim"
    shutil.copytree(RES / "caja", sim)
    # Sin carpetas de simulación, la del .in; con una que tiene resultados, esa.
    assert results_folder_for(app_settings(), case / "caja.in") == case
    remember_sim_folder(app_settings(), case / "caja.in", sim)
    assert results_folder_for(app_settings(), case / "caja.in") == sim
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_file(str(case / "caja.in"))
    assert w.act_results_case.isEnabled()
    w.act_results_case.trigger()
    rw = w.results_window()
    assert rw.isVisible()
    assert rw.primary().folder.path == sim
    rw.close()
    w.close()


def test_derived_dvh_and_body_doses(qtbot, win):
    win.open_folder(RES / "tac")
    _wait(qtbot, win)
    ds = win.primary()
    # La masa y la dosis de cada cuerpo aparecen en el resumen cuando se sabe el volumen de los cuerpos.
    qtbot.waitUntil(lambda: ds.key in win.volumes, timeout=30000)
    html = win.summary_view.browser.toHtml()
    assert "Dosis media" in html and "606.6 g" in html
    r = _select(qtbot, win, lambda _l, r: r.output == "dvh-tissues")
    assert win.last_error == "" and r.output == "dvh-tissues"
    names = [s.name for s in win.current_data.series]
    assert names == ["1 · water", "2 · hueso"]
    assert win.metrics.preset.currentData() == "auto" and win.metrics.results[0].key == "D98"
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output == "dvh-bodies")
    # La rejilla (10 × 10 × 5 cm) cubre un 32 % de la losa (un cilindro de 10 cm de radio y 5 de alto): se dice.
    assert [s.name for s in win.current_data.series] == ["SLAB (32 % dentro de la rejilla)"]
    assert "solo cubre una parte" in win.curves_view.note.text()
    # Con las ejecuciones combinadas, el DVH del TAC se recalcula con la dosis combinada (aquí, sin TAC: caja).
    win.open_folder(RES / "paralelo")
    _wait(qtbot, win)
    assert any(r.output == "dvh-bodies" and r.combined for _l, r in _refs(win))


def test_spectrum_panel_resolution_and_windows(qtbot, win):
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output.startswith("spc-enddet"))
    sp = win.spectro
    assert sp._curves is win.curves_view.curves
    sp.det.setCurrentIndex(sp.det.findData("nai"))
    qtbot.waitUntil(lambda: any(o.label.startswith("con ") for o in win.curves_view.overlays), timeout=5000)
    ov = next(o for o in win.curves_view.overlays if o.label.startswith("con "))
    w = np.diff(ov.curves.edges)
    s = win.curves_view.curves.series[0]
    area_in = float((s.y * np.diff(win.curves_view.curves.edges)).sum())
    assert float((ov.curves.series[0].y * w).sum()) == pytest.approx(area_in, rel=1e-6)
    sp.windows.setText("20-40")
    sp.recompute()
    assert "20–40 keV" in sp.win_out.text()
    # Con N partículas, cuentas.
    win.units.norm.setCurrentIndex(win.units.norm.findData("count"))
    assert "cuentas" in sp.win_out.text()
    sp.det.setCurrentIndex(0)
    assert not any(o.label.startswith("con ") for o in win.curves_view.overlays)


def test_compare_statistics(qtbot, win):
    win.open_folder(RES / "paralelo" / "run-01")
    _wait(qtbot, win)
    win.add_folder(RES / "paralelo" / "run-02")
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose" and r.dataset == win.primary().key)
    txt = win.compare.stats.text()
    assert "run-02" in txt and "χ²/n" in txt


def test_window_is_reused_after_closing(qtbot, win):
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    win.close()  # la ventana principal solo tiene una: se vuelve a enseñar la misma
    win.show()
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose")
    assert isinstance(win.current_data, Curves) and win.last_error == ""
    win.open_folder(RES / "cilindro")
    _wait(qtbot, win)
    assert win.primary().folder.path == RES / "cilindro" and isinstance(win.current_data, Summary)


def test_detector_resolution_follows_the_curve(qtbot, win):
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output == "spc-enddet#1")
    sp = win.spectro
    sp.det.setCurrentIndex(sp.det.findData("nai"))

    def resolution():
        return [o for o in win.curves_view.overlays if o.resolution]

    qtbot.waitUntil(lambda: len(resolution()) == 1, timeout=5000)
    first = resolution()[0].curves
    # Las ventanas de energía van en la unidad con que se ve la curva.
    assert sp.energy_unit() == win.curves_view.shown.x_unit
    # Otro espectro: la convolucionada es la suya (nunca la del anterior).
    _select(qtbot, win, lambda _l, r: r.output == "spc-impdet#1")
    qtbot.waitUntil(lambda: len(resolution()) == 1 and resolution()[0].curves is not first, timeout=5000)
    assert sp._curves is win.curves_view.curves
    # Una curva que no es un espectro: ninguna.
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose")
    qtbot.wait(100)
    assert not resolution()


def test_sphere_map_exports_its_radial_curve(qtbot, win):
    win.open_folder(RES / "esfera")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output == "radial-dose")
    assert isinstance(win.current_data, Grid) and win.current_data.geometry == "sphere"
    cols, data = win.map_view.plane_rows()
    assert cols[0].startswith("r") and len(cols) == 3 and len(data[0]) == len(win.current_data.edges[0]) - 1


def test_campaign_variants_have_their_own_case(qtbot, win, tmp_path):
    from argus.ui.results.window import Ref

    camp = tmp_path / "campana"
    shutil.copytree(RES / "campana", camp)
    for run in camp.glob("v*/run-*"):  # con mapa de dosis: ARGUS ofrece el histograma dosis-volumen por cuerpos
        shutil.copy2(RES / "caja" / "3d-dose-map.dat", run / "3d-dose-map.dat")
    win.open_folder(camp)
    _wait(qtbot, win)
    ds = win.primary()
    f = ds.folder
    key0, folder0 = win._case_key(ds, Ref(ds.key, "res", None, True))
    key2, folder2 = win._case_key(ds, Ref(ds.key, "res", 2))
    assert key0 != key2 and folder0 is f.runs[0] and folder2 is f.runs[2]
    assert win._input_of(ds, Ref(ds.key, "res", 2)).is_relative_to(f.runs[2].path)
    refs = [r for _l, r in _refs(win)]
    assert any(r.output == "dvh-bodies" and r.run == 1 for r in refs)  # en cada variante
    assert not any(r.output == "dvh-bodies" and r.combined for r in refs)  # no en «Comparar las variantes»
    # El resumen de una variante con varias ejecuciones: el estado de todas ellas.
    _select(qtbot, win, lambda _l, r: r.output == "res" and r.run == 1)
    assert "2 ejecuciones" in win.summary_view.browser.toPlainText()
