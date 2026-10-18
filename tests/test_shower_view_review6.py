"""Revisión del visor de la cascada (0.13.1): partículas por caso, números de lluvia estables, la animación de las
lluvias que se ven, sin GPU (las que caben, grosor, estela, orden), exportar y lo que se lee mientras tanto."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from argus.penhan import cascade as cs  # noqa: E402
from argus.penhan.inputfile import parse_in_file  # noqa: E402
from argus.penhan.programs import Launch  # noqa: E402
from argus.results import shower as sh  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_shower_view import _case, _fake  # noqa: E402

pytestmark = [pytest.mark.gui, pytest.mark.skipif(os.name == "nt" or shutil.which("awk") is None,
                                                  reason="el penhmain de mentira es un guion de sh con awk")]
NATIVE = Launch("native", "de mentira")


def _cascade(tmp_path: Path, name: str = "a", showers: int = 100) -> Path:
    in_path = _case(tmp_path / name)
    exe = _fake(tmp_path / name)
    job = cs.start_job(cs.CascadeJob(in_path, parse_in_file(in_path), exe, NATIVE, showers=showers, jobs=2, cap=50))
    assert job.wait(60) and job.status.stage == "terminada", job.status.text
    return in_path


@pytest.fixture
def view(qtbot):
    from argus.ui.results.shower_view import ShowerView
    from argus.ui.settings import app_settings

    v = ShowerView(settings=app_settings())
    qtbot.addWidget(v)
    v.resize(1000, 700)
    v.show()
    qtbot.waitExposed(v)
    yield v
    v.shutdown()


def _show(v, in_path: Path):
    from argus.ui.results.shower_view import load_cascade_data

    data = load_cascade_data(in_path.parent, in_path)
    assert data.layer is not None
    v.set_data(data, "Cascada")
    return data


def test_unchecked_particles_do_not_carry_to_another_case(tmp_path, view):
    a = _cascade(tmp_path, "a")
    b = _cascade(tmp_path, "b")
    _show(view, a)
    view._checks[2].setChecked(False)
    assert 2 not in view.track_style.particles
    _show(view, b)  # otro caso: se ven todas otra vez
    assert view._checks[2].isChecked() and 2 in view.track_style.particles
    view._checks[2].setChecked(False)
    _show(view, b)  # el mismo caso (se vuelve a leer): se queda como estaba
    assert not view._checks[2].isChecked()


def test_shower_numbers_do_not_change_while_runs_finish(tmp_path):
    """Mientras se simula, una ejecución que acaba antes que las de delante no se queda con sus números."""
    in_path = _cascade(tmp_path, "a")
    info = cs.find_cascades(in_path.parent)[0]
    out = info.run_paths[0] / cs.OUTPUT
    text = out.read_text()
    out.write_text(text.replace("*** END ***", ""))  # la primera, «todavía simulando»
    s = sh.load_cascade(info)
    assert s.n_showers == 100 and int(s.shower.min()) == 50  # las de run-02 siguen siendo la 51…100
    out.write_text(text)
    s2 = sh.load_cascade(info)
    assert s2.n_showers == 100 and int(s2.shower.min()) == 0


def test_controls_choose_their_mode_and_keep_the_instant(tmp_path, view):
    from argus.ui.results.shower_view import position_of

    _show(view, _cascade(tmp_path, "a"))
    view.one_k.setValue(7)  # escribir en «solo la» elige ese modo
    assert view.mode_one.isChecked() and (view.track_style.shower_lo, view.track_style.shower_hi) == (6, 7)
    # La animación de una lluvia dura lo que dura esa lluvia.
    from argus.ui.results.shower_view import shower_spans

    end = view.track_style.end_time(view.data.layer)
    assert end == pytest.approx(shower_spans(view.data.layer)[6], rel=1e-6)
    # En pausa, cambiar a tiempo logarítmico no cambia el instante.
    view.mode_first.setChecked(True)
    view.toggle_play()
    view._u = 0.5
    view._update_style()
    view.toggle_play()  # pausa
    t = view.track_style.time
    view.log_time.setChecked(not view.log_time.isChecked())
    assert view.track_style.time == pytest.approx(t, rel=1e-6)
    assert view._u == pytest.approx(position_of(t, view.track_style.end_time(view.data.layer),
                                                view.log_time.isChecked()))


def test_hiding_stops_the_animation(tmp_path, view):
    _show(view, _cascade(tmp_path, "a"))
    view.toggle_play()
    assert view._playing
    view.hide()
    assert not view._playing and not view.anim_timer.isActive()


def test_a_late_read_does_not_replace_what_is_shown(tmp_path, view):
    a = _cascade(tmp_path, "a")
    b = _cascade(tmp_path, "b")
    data_a = _show(view, a)
    old = view._token
    _show(view, b)
    view._on_loaded(old, data_a, "")  # la lectura de antes llega tarde
    assert view.data.in_path == b


def test_nothing_is_reloaded_while_exporting(tmp_path, view):
    _show(view, _cascade(tmp_path, "a"))
    token = view._token
    view._exporting = True
    view._reload_or_wait(None)
    assert view._token == token and view._reload_after_export is None
    view._exporting = False


def test_the_chosen_cascade_is_kept(tmp_path, view):
    """Con dos cascadas del caso, la elegida en la lista no se cambia por la más reciente al volver a leer."""
    a = _cascade(tmp_path, "a", showers=100)
    exe = _fake(tmp_path / "a")
    doc = parse_in_file(a)
    job = cs.start_job(cs.CascadeJob(a, doc, exe, NATIVE, showers=60, jobs=2, cap=50))
    assert job.wait(60)
    _show(view, a)
    assert view.data.info.path.name == "cascada-2"  # la más reciente
    first = view.data.cascades[-1].path
    assert first.name == "cascada"
    view._chosen = first
    _show(view, a)  # la ventana trae la más reciente: se vuelve a leer la elegida
    t0 = time.time()
    while view._loading and time.time() - t0 < 20:
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        time.sleep(0.02)
    assert view.data.info.path == first


def test_gif_lasts_what_was_asked():
    from argus.ui.results.shower_view import gif_durations

    for fps in (6, 12, 15, 24, 25, 30, 50):
        d = gif_durations(int(10 * fps), fps)
        assert sum(d) == 10000 and all(x % 10 == 0 and x >= 20 for x in d), fps


def test_exporting_with_the_extension_added_asks_before_overwriting(tmp_path, view, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    _show(view, _cascade(tmp_path, "a"))
    target = tmp_path / "cascada.png"
    target.write_bytes(b"no se toca")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "cascada"),
                                                                                       "PNG (*.png)")))
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No))
    view.export_image()
    assert asked and "cascada.png" in asked[0] and target.read_bytes() == b"no se toca"


def test_without_gpu_only_whole_showers_that_fit_are_drawn(tmp_path, view, monkeypatch):
    """Sin GPU, el límite de tramos se aplica a lluvias enteras y sin el tiempo: en la animación no desaparece ninguna
    y el rótulo lo dice."""
    from argus.ui.view3d import GLCanvas

    _show(view, _cascade(tmp_path, "a"))
    v3 = view.view3d
    if isinstance(v3.canvas, GLCanvas):
        pytest.skip("con GPU")
    per = int(view.data.layer.n_segments / view.data.layer.n_showers)
    monkeypatch.setattr(type(v3), "MAX_CPU_SEGMENTS", 10 * per + per // 2)
    view.line_width.setValue(3.0)
    view.trail.setChecked(True)
    img = v3.grab_image()
    assert not img.isNull() and v3.cpu_showers == (0, 10)
    view.toggle_play()
    view._u = 0.5
    view._update_style()
    v3.grab_image()
    assert v3.cpu_showers == (0, 10)  # las mismas, también a mitad de la animación
    from PySide6.QtGui import QImage, QPainter

    im = QImage(800, 500, QImage.Format.Format_ARGB32)
    p = QPainter(im)
    lines = []
    import argus.ui.results.shower_view as svmod

    monkeypatch.setattr(svmod, "_box", lambda _p, _x, _y, ls: lines.extend(ls))
    view._paint_overlay(p, 800, 500)
    p.end()
    assert any("Sin GPU solo caben las lluvias 1–10" in t for t in lines)


def test_switching_result_removes_the_previous_overlay_from_the_main_window(qtbot, tmp_path):
    """«Ver sobre la geometría»: al pasar de la cascada al resumen (o a un mapa), las trayectorias se quitan de la
    ventana principal (antes se quedaban dibujadas sobre el mapa de dosis)."""
    from argus.results.data import TextOutput
    from argus.ui.results.window import ResultsWindow
    from argus.ui.settings import app_settings

    w = ResultsWindow(settings=app_settings())
    qtbot.addWidget(w)
    tracks, maps = [], []
    w.showTracksOnGeometry.connect(tracks.append)
    w.showOnGeometry.connect(maps.append)
    w.act_geo.setEnabled(True)
    w.act_geo.setChecked(True)
    tracks.clear()
    maps.clear()
    w.current = None
    w.current_data = TextOutput("dat", "penhmain.dat", "texto")
    w._show(w.current_data)
    assert tracks and tracks[-1] is None and maps and maps[-1] is None
    w.shutdown()
