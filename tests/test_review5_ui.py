"""Quinta revisión (0.12.2): ventana de resultados, diálogos y widgets."""

from __future__ import annotations

import ast
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS, ROOT  # noqa: E402

pytestmark = pytest.mark.gui

RES = CORPUS / "resultados"


@pytest.fixture
def win(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.ui.results.window import ResultsWindow

    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(str(a[2]) if len(a) > 2 else "")
                        or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    app_settings().clear()
    w = ResultsWindow(settings=app_settings())
    w.warnings = warnings
    qtbot.addWidget(w)
    w.resize(1400, 860)
    w.show()
    yield w
    w.close()


def _wait(qtbot, w):
    qtbot.waitUntil(lambda: w.loaded_token >= w._token, timeout=30000)


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


def _texts(w):
    out = []
    stack = [w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount())]
    while stack:
        it = stack.pop(0)
        out.append(it.text(0))
        stack.extend(it.child(i) for i in range(it.childCount()))
    return out


def _select(qtbot, w, pred):
    for label, r in _refs(w):
        if pred(label, r):
            w.tree.setCurrentItem(w._find_item(r))
            _wait(qtbot, w)
            return r
    raise AssertionError("no está en el árbol")


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    t = time.time() - 3600
    for p in dst.rglob("*"):
        if p.is_file():
            os.utime(p, (t, t))
    return dst


# -- casos distintos en una carpeta ---------------------------------------------------------------------------------

def test_a_folder_with_different_cases_shows_each_one(qtbot, win, tmp_path):
    parent = tmp_path / "DJ"
    parent.mkdir()
    _copy(RES / "caja", parent / "caja")
    _copy(RES / "cilindro", parent / "cilindro")
    ds = win.open_folder(parent)
    _wait(qtbot, win)
    texts = _texts(win)
    assert any(t.startswith("Cada caso por separado (2)") for t in texts)
    assert not any("combinadas" in t for t in texts)
    assert win.current is not None and win.current.run == 0 and not win.current.combined
    assert "No se combinan" in win.state_label.text()
    # Cada caso con su geometría (sus cuerpos tienen sus nombres).
    _select(qtbot, win, lambda _l, r: r.run == 1 and r.output == "res")
    qtbot.waitUntil(lambda: win.cases.get(f"{ds.key}#v1") is not None, timeout=30000)
    assert win.cases[f"{ds.key}#v1"].input_path.parent.name == "cilindro"


# -- comparar ---------------------------------------------------------------------------------------------------------

def test_no_overlay_left_from_the_previous_selection(qtbot, win):
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    other = win.add_folder(RES / "cilindro")
    _wait(qtbot, win)
    main = win.primary()
    _select(qtbot, win, lambda _l, r: r.dataset == main.key and r.output == "depth-dose")
    assert [o.label for o in win.curves_view.overlays] == ["cilindro"]
    _select(qtbot, win, lambda _l, r: r.dataset == other.key and r.output == "depth-dose")
    assert not [o for o in win.curves_view.overlays if not o.resolution]  # ni ella misma encima


def test_compared_folder_is_followed(qtbot, win, tmp_path):
    a = _copy(RES / "caja", tmp_path / "A")
    b = _copy(RES / "caja", tmp_path / "B")
    win.open_folder(a)
    _wait(qtbot, win)
    win.add_folder(b)
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.dataset == win.primary().key and r.output == "depth-dose")
    before = win.curves_view.overlays[0].curves.series[0].y.max()
    p = b / "depth-dose.dat"
    rows = []
    for ln in p.read_text("latin-1").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            v = ln.split()
            v[1] = f"{2 * float(v[1]):.6E}"
            ln = "  ".join(v)
        rows.append(ln)
    p.write_text("\n".join(rows) + "\n", "latin-1")
    t = time.time() - 3600  # escrito y ya terminado
    os.utime(p, (t, t + 1))
    win.poll()
    qtbot.waitUntil(lambda: win.curves_view.overlays and
                    win.curves_view.overlays[0].curves.series[0].y.max() == pytest.approx(2 * before, rel=1e-5),
                    timeout=10000)


def test_measured_positions_unit_and_where_they_are_offered(qtbot, win, tmp_path):
    from argus.results.readers import read_output

    c = read_output(RES / "caja" / "depth-dose.dat")
    y = c.series[0].y
    # «gamma» y «(mm)» de otra columna no son la unidad de la posición.
    meas = tmp_path / "medidas.csv"
    meas.write_text("Profundidad (cm);Dosis gamma (%);incert. (mm)\n" + "\n".join(
        f"{x:.4f};{100 * v / y.max():.4f};0,1".replace(".", ",") for x, v in zip(c.x, y)) + "\n", "utf-8")
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    ov = win.add_measured(meas)
    np.testing.assert_allclose(ov.curves.x, c.x, atol=1e-4)
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose")
    assert any(k.startswith("m") for k, _t, _c in win._compare_candidates())
    assert any(o.measured for o in win.curves_view.overlays)
    # Un espectro no está en función de la posición: las medidas no se ofrecen ni se dibujan encima.
    _select(qtbot, win, lambda _l, r: r.output.startswith("spc-impdet"))
    assert not any(k.startswith("m") for k, _t, _c in win._compare_candidates())
    assert not any(o.measured for o in win.curves_view.overlays)


def test_measured_without_unit_asks(qtbot, win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    meas = tmp_path / "m.txt"
    meas.write_text("z D\n1 10\n2 20\n3 30\n", "utf-8")
    asked = []
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: asked.append(a[3]) or ("mm", True))
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    ov = win.add_measured(meas)
    assert asked and list(ov.curves.x) == pytest.approx([0.1, 0.2, 0.3])


def test_campaign_comparison_uses_the_first_variant_that_has_it(qtbot, win, tmp_path):
    d = _copy(RES / "campana", tmp_path / "campana")
    for run in (d / "v01-SPOSIT_-5").glob("run-*"):
        (run / "depth-dose.dat").unlink()
    win.open_folder(d)
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose" and r.combined)
    assert win.last_error == ""
    assert win.curves_view.curves is not None and win.curves_view.primary_label.startswith("SPOSIT z = -8")
    assert len([o for o in win.curves_view.overlays if not o.resolution]) == 1


# -- textos y estado que se quedaban viejos ------------------------------------------------------------------------

def test_summary_follows_the_units(qtbot, win):
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    ds = win.primary()
    qtbot.waitUntil(lambda: ds.key in win.volumes, timeout=30000)
    win._show_summary(win.current_data, ds, win.current)
    before = win.summary_view.browser.toPlainText()
    i = win.units.dose.findData("eV/g")
    assert i >= 0
    win.units.dose.setCurrentIndex(i)
    qtbot.waitUntil(lambda: win.summary_view.browser.toPlainText() != before, timeout=5000)
    assert "eV/g" in win.summary_view.browser.toPlainText()


def test_status_bar_belongs_to_the_open_folder(qtbot, win, tmp_path):
    win.act_follow.setChecked(False)
    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    assert "Terminada" in win.state_label.text()
    empty = tmp_path / "vacia"
    empty.mkdir()
    win.open_folder(empty)
    _wait(qtbot, win)
    assert "Terminada" not in win.state_label.text()


def test_gamma_text_and_spectrum_panel_are_cleared(qtbot, win):
    win.open_folder(RES / "paralelo" / "run-01")
    _wait(qtbot, win)
    win.add_folder(RES / "paralelo" / "run-02")
    _wait(qtbot, win)
    main = win.primary()
    _select(qtbot, win, lambda _l, r: r.dataset == main.key and r.output == "depth-dose")
    win.compute_gamma({"dose": 0.05, "dta": 0.3, "threshold": 0.1, "local": False, "reference": "main"})
    assert win.compare.g_result.text()
    _select(qtbot, win, lambda _l, r: r.dataset == main.key and r.output.startswith("spc-impdet"))
    assert win.compare.g_result.text() == ""
    assert win.spectro._curves is not None
    _select(qtbot, win, lambda _l, r: r.dataset == main.key and r.output == "3d-dose-map")
    assert win.spectro._curves is None


def test_export_figure_with_another_extension(qtbot, win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    win.open_folder(RES / "caja")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output == "depth-dose")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "fig.txt"), "PDF (*.pdf)"))
    win.export_figure()  # sin excepción: se avisa
    assert win.warnings and "PDF, SVG o PNG" in win.warnings[-1]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "fig"), "SVG (*.svg)"))
    win.export_figure()
    assert (tmp_path / "fig.svg").is_file()


def test_map_profile_label_says_per_particle(qtbot, win):
    win.open_folder(RES / "cilindro")
    _wait(qtbot, win)
    _select(qtbot, win, lambda _l, r: r.output == "2d-dose-map")
    mv = win.map_view
    g = mv.grid
    mv.pick = (float(g.centers(0)[1]), float(g.centers(1)[3]))
    mv.redraw()
    labels = [ax.get_ylabel() for ax in mv.plot.figure.axes]
    assert any("por partícula" in t for t in labels if t.startswith("Dosis"))


def test_series_that_get_data_while_following_are_shown(qtbot):
    from argus.results.data import Curves, Series
    from argus.ui.results.curves_view import CurvesView

    v = CurvesView()
    qtbot.addWidget(v)
    x = np.arange(5.0)

    def curves(ph):
        return Curves("energy-up", "t", x, "E", "eV", "p", "1/eV", [Series("electrones", np.ones(5), None, particle=1),
                                                                     Series("fotones", ph, None, particle=2)])

    v.set_curves(curves(np.zeros(5)))
    assert "fotones" not in v.visible
    v.set_curves(curves(np.ones(5)), keep_series=True)
    assert "fotones" in v.visible
    # Una que se quita a mano no vuelve sola.
    v._checks["fotones"].setChecked(False)
    v.set_curves(curves(2 * np.ones(5)), keep_series=True)
    assert "fotones" not in v.visible


def test_truncated_phase_space_is_per_primary_of_what_was_read(qtbot):
    from argus.results.data import PhaseSpace
    from argus.ui.results.special_views import PhaseSpaceView

    n = 6000
    psf = PhaseSpace(1, np.full(n, 2, dtype=np.int8), np.full(n, 1e5), np.zeros((n, 3)), np.tile([0, 0, 1.0], (n, 1)),
                     np.ones(n), np.zeros((n, 4), dtype=np.int64), np.ones(n, dtype=np.int64), total=30000)
    v = PhaseSpaceView()
    qtbot.addWidget(v)
    v.set_data(psf, "psf", None, {"showers": 30000.0})
    assert v.showers == pytest.approx(6000.0)


def test_discrete_lines_are_not_a_density(qtbot):
    from argus.results import metrics as mt
    from argus.results.data import Curves, Series
    from argus.ui.results.spectro_panel import SpectroPanel

    c = Curves("psource", "fuente", np.array([3.4e5, 3.5e5, 3.6e5]), "E", "eV", "Probabilidad", "",
               [Series("definido", np.array([0.3, 0.2, 0.5]), None)], histogram=False, discrete=True)
    p = SpectroPanel(settings=app_settings())
    qtbot.addWidget(p)
    assert not p.applies_to(c)
    res = {m.key: m.value for m in mt.compute(mt.guess_preset("psource", "E", None, True), c.x, c.series[0].y)}
    assert res["area"] == pytest.approx(1.0) and res["emean"] == pytest.approx(3.52e5)


def test_the_job_section_has_no_ltrack_field(qtbot):
    """El campo de LTRACK paraba en 50 (lo que guarda penhmain). Desde 0.17.1 no está: eran dos números para lo mismo
    («Trayectorias» y «Lluvias»); las lluvias para verlas en 3D se eligen al ejecutar, de 1 a 1000. Los avisos de una
    línea LTRACK del .in siguen yendo a «Trabajo»."""
    from argus.ui.sim_outputs import JobSection

    p = JobSection()
    qtbot.addWidget(p)
    assert not hasattr(p, "ltrack") and "LTRACK" in p.keywords
    f = p.form
    labels = [f.itemAt(i, f.ItemRole.LabelRole).widget().text() for i in range(f.rowCount())
              if f.itemAt(i, f.ItemRole.LabelRole) is not None]
    assert labels[:2] == ["Partículas:", "Tiempo máx.:"] and "Trayectorias:" not in labels


# -- Qt ----------------------------------------------------------------------------------------------------------------

# Métodos virtuales de Qt (los que Qt llama desde C++ y PySide busca en el objeto de Python).
QOBJECT_VIRTUALS = {"event", "eventFilter", "timerEvent", "childEvent", "customEvent", "connectNotify",
                    "disconnectNotify"}
WIDGET_VIRTUALS = {"metric", "devType", "paintEngine", "initPainter", "redirected", "sharedPainter", "setVisible",
                   "sizeHint", "minimumSizeHint", "heightForWidth", "hasHeightForWidth", "nativeEvent",
                   "inputMethodQuery", "focusNextPrevChild"}
DIALOG_VIRTUALS = {"accept", "reject", "done", "open", "exec"}


def test_no_widget_attribute_hides_a_virtual_method_of_qt():
    """Un atributo que se llame como un método virtual de Qt (``self.metric = QComboBox()``) hace que Qt llame a
    ese objeto en lugar del método (en ARGUS, al cambiar de pantalla o de escala: «QComboBox object is not
    callable»)."""
    import importlib

    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QDialog, QWidget

    bad = []
    for f in sorted((ROOT / "src" / "argus" / "ui").rglob("*.py")):
        rel = f.relative_to(ROOT / "src").with_suffix("")
        mod = importlib.import_module(".".join(rel.parts))
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            obj = getattr(mod, cls.name, None)
            if not isinstance(obj, type) or not issubclass(obj, QObject):
                continue
            names = set(QOBJECT_VIRTUALS)
            if issubclass(obj, QWidget):
                names |= WIDGET_VIRTUALS
            if issubclass(obj, QDialog):
                names |= DIALOG_VIRTUALS
            for node in ast.walk(cls):
                targets = node.targets if isinstance(node, ast.Assign) else \
                    [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
                for t in targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self" \
                            and (t.attr in names or t.attr.endswith("Event")):
                        bad.append(f"{f.name}:{node.lineno} {cls.name}.{t.attr}")
    assert not bad, bad


def test_campaign_view_survives_a_screen_change(qtbot, win):
    from PySide6.QtCore import QCoreApplication, QEvent

    errors = []
    import sys

    old = sys.excepthook
    sys.excepthook = lambda *a: errors.append(a)
    try:
        win.open_folder(RES / "campana")
        _wait(qtbot, win)
        _select(qtbot, win, lambda _l, r: r.output == "campaign-metrics")
        w = win.views.currentWidget()
        QCoreApplication.sendEvent(w, QEvent(QEvent.Type.DevicePixelRatioChange))
        w.grab()
    finally:
        sys.excepthook = old
    assert not errors


def test_results_loader_threads_do_not_hold_the_exit(qtbot, win):
    assert all(t.daemon for t in win.loader._threads + win.case_loader._threads)


def test_no_gpu_view_on_a_platform_without_opengl_windows(qtbot):
    """Con la plataforma «offscreen» y un servidor X (pruebas en Linux con DISPLAY), Qt crea contextos OpenGL pero
    QOpenGLWidget no dibuja: la vista 3D de la GPU no terminaba nunca. Ahí probe_gl dice que no (y se usa la CPU)."""
    from PySide6.QtGui import QGuiApplication

    from argus.ui.gpu_tracer import NO_GL_WINDOWS, probe_gl

    if QGuiApplication.platformName() not in NO_GL_WINDOWS:
        pytest.skip("solo con la plataforma offscreen (las pruebas sin ARGUS_TEST_GPU)")
    ok, why = probe_gl.__wrapped__()
    assert not ok and "offscreen" in why
