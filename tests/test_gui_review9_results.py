"""Novena revisión (0.16.3), ventana de resultados: seguir una simulación nueva con menos intervalos, medidas
absolutas con «para N partículas», χ² con las medidas, la línea de una energía exacta y el CSV."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

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


def _age_all(folder: Path, age: float = 3600.0) -> None:
    t = time.time() - age
    for p in folder.rglob("*"):
        if p.is_file():
            os.utime(p, (t, t))


def _caja(tmp_path: Path) -> Path:
    d = tmp_path / "caja"
    shutil.copytree(RES / "caja", d)
    _age_all(d)
    return d


# -- 4. seguir la carpeta: la curva nueva con menos intervalos se ve ------------------------------------------------

def test_following_a_rerun_with_fewer_bins_shows_the_new_curve(qtbot, win, tmp_path, monkeypatch):
    from argus.results import store as store_mod
    from argus.ui.results.window import Ref
    from test_review9_results import _fewer_rows

    d = _caja(tmp_path)
    ds = win.open_folder(d)
    _wait(qtbot, win)
    win.select(Ref(ds.key, "depth-dose"))
    _wait(qtbot, win)
    assert len(win.curves_view.curves.x) == 25
    # Sin un resumen nuevo que diga que está entera, al principio se espera (podría estar a medias)…
    _fewer_rows(d / "depth-dose.dat", 10)
    win.poll(force=True)
    _wait(qtbot, win)
    assert len(win.curves_view.curves.x) == 25 and win.last_error.startswith("incompleto")
    # …pero si no cambia, en la vuelta siguiente se lee, aunque la carpeta no haya cambiado (penhmain ya terminó).
    monkeypatch.setattr(store_mod, "STABLE_S", 0.0, raising=False)
    win.poll()
    _wait(qtbot, win)
    assert len(win.curves_view.curves.x) == 10, win.last_error
    # Con el resumen de la simulación nueva escrito después, a la primera.
    _fewer_rows(d / "depth-dose.dat", 6)
    res = d / "penhmain-res.dat"
    res.write_bytes(res.read_bytes())
    monkeypatch.setattr(store_mod, "STABLE_S", 1e9, raising=False)
    win.poll(force=True)
    _wait(qtbot, win)
    assert len(win.curves_view.curves.x) == 6, win.last_error


# -- 3 y 11. medidas en Gy «para N partículas»; χ² en las posiciones de las medidas ------------------------------------

def test_measured_dose_is_absolute_and_compared_at_its_points(qtbot, win, tmp_path):
    from argus.ui.results.window import Ref

    d = _caja(tmp_path)
    ds = win.open_folder(d)
    _wait(qtbot, win)
    win.select(Ref(ds.key, "z-dose"))
    _wait(qtbot, win)
    meas = tmp_path / "medidas_gy.csv"
    meas.write_text("z (cm);Dosis (Gy);sigma\n0,5;2,0;0,02\n1,5;1,9;0,02\n2,5;1,8;0,02\n", encoding="utf-8")
    assert win.add_measured(meas) is not None
    cv = win.curves_view
    stats = win.compare.stats.text()
    assert "(3 puntos" in stats, stats  # antes, «11 puntos» (los de la simulación entre las medidas)
    cv.mode.setCurrentIndex(cv.mode.findData("diff"))
    pts = [ln for ln in cv.axes_cmp.lines if len(ln.get_xdata()) == 3]
    assert pts and np.allclose(pts[0].get_xdata(), [0.5, 1.5, 2.5])
    u = win.units
    u.dose.setCurrentIndex(u.dose.findData("Gy"))
    u.norm.setCurrentIndex(u.norm.findData("count"))
    u.count.setValue(1e9)
    so = next(so for o, so in cv.shown_overlays if o.measured)
    assert so.series[0].y.tolist() == [2.0, 1.9, 1.8]  # antes, 2e9 Gy
    assert "(3 puntos" in win.compare.stats.text()


# -- 7. la energía exacta de psource.dat en la ventana -----------------------------------------------------------------

def test_exact_energy_of_the_source_in_the_window(qtbot, win, tmp_path):
    from argus.ui.results.window import Ref
    from test_review9_results import PSOURCE_SOBP, _true_mean_mev

    d = tmp_path / "fuente"
    d.mkdir()
    (d / "psource.dat").write_text(PSOURCE_SOBP, encoding="latin-1")
    _age_all(d)
    ds = win.open_folder(d)
    _wait(qtbot, win)
    win.select(Ref(ds.key, "psource"))
    _wait(qtbot, win)
    cv = win.curves_view
    assert cv.axes_main.get_ylim()[1] < 1.0  # 1/MeV: sin el pico de 2,7e14 que aplastaba el resto
    texts = [t.get_text() for t in cv.axes_main.texts]
    assert any("26.86 % de las partículas" in t for t in texts), texts
    emean = next(m for m in win.metrics.results if m.key == "emean")
    assert emean.value == pytest.approx(_true_mean_mev(), abs=1e-3)
    sp = win.spectro
    sp.bg.setChecked(False)
    sp.windows.setText("99-100,5")
    sp.recompute()
    assert "0.459 " in sp.win_out.text(), sp.win_out.text()  # 0,190 del continuo y 0,269 a 100 MeV exactos
    cols, data = cv.table_rows()
    k = next(i for i, c in enumerate(cols) if "energía exacta" in c)
    assert data[k][-1] == pytest.approx(0.2685868) and np.isnan(data[k][0])


# -- 8. CSV: con las unidades, de qué variante es cada curva y para Excel ----------------------------------------------

def test_csv_export_has_units_variants_and_a_bom(qtbot, win, tmp_path):
    import codecs

    from argus.ui.results.window import Ref, write_csv

    d = _caja(tmp_path)
    ds = win.open_folder(d)
    _wait(qtbot, win)
    win.select(Ref(ds.key, "depth-dose"))
    _wait(qtbot, win)
    cols, data = win.curves_view.table_rows()
    unit = win.curves_view.shown.y_label.split(" (", 1)[1][:-1]
    assert unit and cols[1].endswith(f"({unit})") and cols[2].startswith("sigma") and cols[2].endswith(f"({unit})")
    path = tmp_path / "pdd.csv"
    write_csv(path, cols, data)
    raw = path.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)  # Excel lee bien los acentos y «cm²»
    assert raw.decode("utf-8-sig").splitlines()[0].startswith(cols[0])
    # Las N variantes de una campaña: la principal también dice cuál es.
    ds = win.open_folder(RES / "campana")
    _wait(qtbot, win)
    win.select(Ref(ds.key, "depth-dose", None, True))
    _wait(qtbot, win)
    cv = win.curves_view
    assert cv.shown_overlays and cv.primary_label
    cols, data = cv.table_rows()
    assert cols[1].startswith(f"{cv.primary_label} · "), cols
    assert any(c.startswith(f"{cv.shown_overlays[0][0].label} · ") for c in cols[2:])
