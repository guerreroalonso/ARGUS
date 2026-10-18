"""Visor de la cascada (0.13.0): datos, escala de tiempo, exportar y la vista en la ventana de resultados."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from argus.penhan import cascade as cs
from argus.penhan.inputfile import parse_in_file
from argus.ui.results.shower_view import (
    CASCADE_KEY,
    auto_alpha,
    default_visibility,
    gas_materials,
    load_cascade_data,
    mp4_problem,
    position_of,
    shower_spans,
    source_description,
    time_of,
    wants_log_time,
    write_gif,
    write_mp4,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_cascade import FAKE  # noqa: E402

CAJA = Path(__file__).parent / "corpus" / "resultados" / "caja"
pytestmark_sh = pytest.mark.skipif(os.name == "nt" or shutil.which("awk") is None,
                                   reason="el penhmain de mentira es un guion de sh con awk")


def _case(tmp_path: Path, mats: dict[str, float | None] | None = None) -> Path:
    d = tmp_path / "caso"
    d.mkdir(parents=True)
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, d / n)
    doc = parse_in_file(d / "caja.in")
    for md in doc.model.materials:
        name = md.fname.strip()
        dens = (mats or {}).get(name)
        text = "material de mentira\n" if dens is None else (
            " PENELOPE (v. 2018)  Material data file ...............\n"
            f" Material: {name.split('.')[0].upper()}  (104)\n"
            f" Mass density = {dens:.8E} g/cm**3\n")
        (d / name).write_text(text)
    return d / "caja.in"


def _fake(tmp_path: Path) -> Path:
    p = tmp_path / "programas" / "penhmain.x"
    p.parent.mkdir(exist_ok=True)
    p.write_text(FAKE)
    p.chmod(0o755)
    return p


def test_time_scale_and_alpha():
    for log in (False, True):
        assert time_of(0.0, 10.0, log) == 0.0
        assert time_of(1.0, 10.0, log) == pytest.approx(10.0)
        for u in (0.1, 0.37, 0.8):
            assert position_of(time_of(u, 10.0, log), 10.0, log) == pytest.approx(u)
    # En logarítmica, la mitad de la barra son las tres primeras décadas: ~3 % del final.
    assert time_of(0.5, 1.0, True) == pytest.approx(0.0306, rel=0.05)
    assert time_of(0.5, 0.0, True) == 0.0
    assert auto_alpha(1) == auto_alpha(30) == 0.85
    assert 0.25 <= auto_alpha(1000) < auto_alpha(100) < 0.85


def test_log_time_by_default_only_when_almost_everything_happens_at_the_start():
    from types import SimpleNamespace as NS

    def case(ends):
        ends = np.asarray(ends, dtype=np.float32)
        # Cada trayectoria, dos puntos: sale en 0 y termina en su instante.
        time = np.column_stack([np.zeros_like(ends), ends]).ravel()
        return NS(count=len(ends), time=time, starts=np.arange(0, 2 * len(ends) + 1, 2)), NS(t_max=float(ends.max()))

    # Electrones o fotones en un maniquí: lo último dura poco más que el resto.
    assert not wants_log_time(*case(np.linspace(0.1, 3.2, 200)))
    # Protones: el 99 % acaba antes de 3,2 ns; dos neutrones, a los 12 y 19 ns.
    assert wants_log_time(*case(np.r_[np.linspace(0.1, 3.2, 300), 12.0, 19.0]))
    # Una fuente de neutrones: la mitad acaba enseguida y lo último tarda microsegundos.
    assert wants_log_time(*case(np.r_[np.full(50, 2.0), np.geomspace(10.0, 5e4, 50)]))
    assert not wants_log_time(None, NS(t_max=1.0))
    assert not wants_log_time(*case(np.zeros(5)))


def test_source_materials_and_visibility(tmp_path):
    in_path = _case(tmp_path, {"water.mat": 1.0, "hueso.mat": 1.2e-3})
    k, label = source_description(parse_in_file(in_path))
    assert k == 2 and label == "fotones de 32 y 662 keV"
    from argus.render.scene3d import HIDDEN, TRANSLUCENT
    from argus.results.case import load_case_geometry

    case = load_case_geometry(in_path)
    assert case.ok
    assert gas_materials(case) == {2}  # «hueso» de mentira con la densidad del aire
    assert default_visibility(case) == {1: TRANSLUCENT, 2: HIDDEN}
    # Sin cabecera de .mat: por el nombre.
    in2 = _case(tmp_path / "b")
    case2 = load_case_geometry(in2)
    assert gas_materials(case2) == set()
    (in2.parent / "hueso.mat").write_text("x\n")
    case2.material_files[1] = "aire.mat"
    assert gas_materials(case2) == {2}


def _qimages(n: int, w: int = 64, h: int = 48):
    from PySide6.QtGui import QColor, QImage

    out = []
    for i in range(n):
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor(20 * i, 80, 200))
        out.append(img)
    return out


def test_gif(qapp, tmp_path):
    from PIL import Image

    p = tmp_path / "a.gif"
    assert write_gif(p, iter(_qimages(4)), 10) == 4
    im = Image.open(p)
    assert im.n_frames == 4 and im.size == (64, 48)
    im.seek(3)
    r, g, b = im.convert("RGB").getpixel((5, 5))
    assert abs(r - 60) <= 8 and abs(g - 80) <= 8 and abs(b - 200) <= 8


@pytest.mark.skipif(mp4_problem() is not None, reason=f"sin vídeo MP4: {mp4_problem()}")
def test_mp4(qapp, tmp_path):
    p = tmp_path / "a.mp4"
    assert write_mp4(p, iter(_qimages(30, 96, 64)), 25) == 30
    assert p.is_file() and p.stat().st_size > 1000


# -- con la ventana de resultados -----------------------------------------------------------------------------------

@pytest.fixture
def results(qtbot, tmp_path):
    from argus.ui.results.window import ResultsWindow
    from argus.ui.settings import PENHMAIN_KEY, app_settings

    s = app_settings()
    s.setValue(PENHMAIN_KEY, str(_fake(tmp_path)))
    s.remove("cascada")
    w = ResultsWindow(settings=s)
    qtbot.addWidget(w)
    yield w
    for job in cs.active_jobs():
        job.cancel()
        job.wait(20)
    w.shutdown()
    s.remove(PENHMAIN_KEY)


def _cascade_item(w):
    from argus.ui.results.window import ROLE

    stack = [w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount())]
    while stack:
        it = stack.pop()
        ref = it.data(0, ROLE)
        if ref is not None and getattr(ref, "output", None) == CASCADE_KEY:
            return it
        stack.extend(it.child(k) for k in range(it.childCount()))
    return None


@pytest.mark.gui
@pytestmark_sh
def test_the_cascade_view_simulates_follows_and_exports(qtbot, results, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QFileDialog

    from argus.ui.results.shower_view import ExportAnimationDialog, ShowerView

    w = results
    in_path = _case(tmp_path)
    w.open_folder(in_path.parent, in_path)
    w.show()
    qtbot.waitExposed(w)
    item = _cascade_item(w)
    assert item is not None and item.text(0).startswith("Cascada")
    assert item.parent().indexOfChild(item) == 0  # sin resultados: el primero
    w.tree.setCurrentItem(item)
    qtbot.waitUntil(lambda: isinstance(w.views.currentWidget(), ShowerView), timeout=30000)
    v = w.views.currentWidget()
    assert not w.side.isVisible()
    assert v.pages.currentWidget() is v.msg_page and v.start_btn.isVisible()
    assert v.data.layer is None and v.data.source_label == "fotones de 32 y 662 keV"
    # Simular 120 lluvias: penhmain (de mentira) guarda 50 por ejecución.
    v.start_n.setValue(120)
    v.start_btn.click()
    qtbot.waitUntil(lambda: v.data is not None and v.data.layer is not None and v.data.layer.n_showers == 120
                    and v.data.done == 120 and not v.job_timer.isActive(), timeout=90000)
    assert v.pages.currentWidget() is v.main_page and v.view3d is not None
    assert int(w.settings.value("cascada/lluvias")) == 120
    assert v.first_n.maximum() == 120 and v.first_n.value() == 120
    assert sorted(v._checks) == [1, 2] and "(120)" in v._checks[1].text()
    assert "120 de 120" in v.status.text()
    info = cs.find_cascades(in_path.parent, "caja.in")[0]
    assert [r.showers for r in info.runs] == [50, 50, 20]
    # Una sola lluvia y sus colores.
    v.next_btn.click()
    assert v.mode_one.isChecked() and v.track_style.shower_lo == 1 and v.track_style.shower_hi == 2
    assert "Lluvia 2" in v.shower_info.text()
    for i in range(v.color.count()):
        v.color.setCurrentIndex(i)
        v.view3d.grab_image()
    v.color.setCurrentIndex(0)
    v.first_n.setValue(10)
    assert v.mode_first.isChecked() and (v.track_style.shower_lo, v.track_style.shower_hi) == (0, 10)
    v._checks[2].setChecked(False)
    assert 2 not in v.track_style.particles and 1 in v.track_style.particles
    tracks = v.visible_tracks()
    assert [t.particle for t in tracks] == [1] and tracks[0].count == 10
    cols, data = v.table_rows()
    assert cols[0] == "lluvia" and len(data[0]) == 10 and set(data[2].tolist()) == {1.0}
    v._checks[2].setChecked(True)
    # Animación.
    v.toggle_play()
    assert v._playing and v.track_style.animate
    qtbot.wait(150)
    assert 0.0 < v._u < 1.0 and v.track_style.time > 0.0
    v.toggle_play()
    assert not v._playing
    v.slider.setValue(500)
    assert v.track_style.animate and v.track_style.time == pytest.approx(v.track_style.end_time(v.data.layer) * (
        0.5 if not v.log_time.isChecked() else 0.0306), rel=0.05)
    v.stagger.setChecked(True)
    assert v.track_style.stagger > 0
    v.show_everything()
    assert not v.track_style.animate
    # Las trayectorias sobre los cortes 2D de la ventana principal.
    sent = []
    w.showTracksOnGeometry.connect(sent.append)
    w.act_geo.setChecked(True)
    assert sent and sent[-1] and sum(t.count for t in sent[-1]) == 20
    v.first_n.setValue(5)
    assert sum(t.count for t in sent[-1]) == 10
    w.act_geo.setChecked(False)
    # Seguir la carpeta no vuelve a leer la cascada.
    token = w._token
    w.poll(force=True)
    assert w._token == token and _cascade_item(w).isSelected()
    # Exportar.
    png = tmp_path / "out" / "c.png"
    png.parent.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(png), ""))
    w.export_figure()
    assert png.is_file() and png.stat().st_size > 1000
    gif = tmp_path / "out" / "c.gif"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(gif), ""))
    monkeypatch.setattr(ExportAnimationDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(ExportAnimationDialog, "values", lambda self: ("gif", 1.0, 6, 200))
    v.export_animation()
    from PIL import Image

    im = Image.open(gif)
    assert im.n_frames == 6 and im.size[0] == 200
    assert not v.track_style.animate  # como estaba
    # Si el caso cambia, la cascada ya no es la suya.
    text = in_path.read_text().replace("SPOSIT 0 0 -5", "SPOSIT 0 0 -4")
    in_path.write_text(text)
    v.reload()
    qtbot.waitUntil(lambda: not v._loading, timeout=30000)
    assert v.data.stale and "ha cambiado" in v.status.text()
    # Con el caso nuevo: otra carpeta, y se puede elegir la anterior.
    assert v.start_cascade(30)
    qtbot.waitUntil(lambda: v.data.layer is not None and v.data.layer.n_showers == 30
                    and not v.job_timer.isActive(), timeout=90000)
    assert v.data.info.path.name == "cascada-2" and not v.data.stale
    assert v.which.isVisible() and v.which.count() == 2
    v.which.setCurrentIndex(1)
    v.which.activated.emit(1)
    qtbot.waitUntil(lambda: v.data.info.path.name == "cascada" and not v._loading, timeout=30000)
    assert v.data.layer.n_showers == 120 and v.data.stale


@pytest.mark.gui
@pytestmark_sh
def test_cancelling_from_the_view(qtbot, results, tmp_path, monkeypatch):
    from argus.ui.results.shower_view import ShowerView

    monkeypatch.setenv("SLEEP", "30")
    w = results
    in_path = _case(tmp_path)
    from argus.ui.cascade_ui import CAP_KEY

    w.settings.setValue(CAP_KEY + cs.file_sha1(w.settings.value("sim/penhmain")), 50)
    w.open_folder(in_path.parent, in_path)
    w.show()
    qtbot.waitExposed(w)
    w.show_cascade(in_path)
    qtbot.waitUntil(lambda: isinstance(w.views.currentWidget(), ShowerView), timeout=30000)
    v = w.views.currentWidget()
    assert v.start_cascade(100)
    qtbot.waitUntil(lambda: v.cancel_btn.isVisible() and v.progress.isVisible(), timeout=30000)
    assert "Simulando" in v.msg.text()
    v.cancel_btn.click()
    qtbot.waitUntil(lambda: not v.job_timer.isActive() and not v._loading, timeout=30000)
    assert "cancelada" in v.status.text().lower()
    data = load_cascade_data(in_path.parent, in_path)
    assert data.info is not None and data.done == 0 and data.layer is None
    assert v.pages.currentWidget() is v.msg_page and "no tiene aún lluvias" in v.msg.text()
    # Se termina después en la misma carpeta (completando lo que faltase).
    monkeypatch.setenv("SLEEP", "")
    assert v.start_cascade(100)
    qtbot.waitUntil(lambda: v.data.layer is not None and v.data.done == 100 and not v.job_timer.isActive(),
                    timeout=90000)
    assert v.data.info.path.name == "cascada" and len(cs.find_cascades(in_path.parent)) == 1


def test_spans_of_a_layer():
    from argus.render.track_layer import layer_from_showers
    from argus.results.data import Tracks
    from argus.results.shower import reconstruct

    a = np.array([[0, 0, 0, 1e6], [0, 0, 1, 9e5], [0, 0, 2, 8e5]], float)
    b = np.array([[5, 0, 0, 1e6], [5, 0, 3, 9e5]], float)
    s = reconstruct([Tracks(1, np.vstack([a, b]), np.array([0, 3, 5]))])
    L = layer_from_showers(s)
    spans = shower_spans(L)
    assert len(spans) == 2 and spans[1] > spans[0] > 0
