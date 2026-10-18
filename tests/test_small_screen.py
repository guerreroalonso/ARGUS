"""Con la pantalla al 150 % (un portátil de 1920 × 1080 tiene 1280 × 720 puntos), las ventanas y los diálogos caben:
las filas de controles siguen en otra línea y los tamaños iniciales no pasan del sitio libre de la pantalla."""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtCore import QRect  # noqa: E402

from argus.ui import screen_fit  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

RES = CORPUS / "resultados"
SMALL = QRect(0, 0, 1280, 680)  # 1920 × 1080 al 150 %, sin la barra de tareas
# Con margen: la letra de las pruebas (DejaVu Sans) es más ancha que la de Windows (Segoe UI).
MAX_W = SMALL.width() - 100
MAX_H = SMALL.height() - 80


class _Screen:
    def availableGeometry(self):  # noqa: N802
        return QRect(SMALL)


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(screen_fit, "screen_for", lambda w: _Screen())


def test_fit_to_screen(qtbot, small):
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    qtbot.addWidget(w)
    screen_fit.fit_to_screen(w, 1400, 880)
    assert (w.width(), w.height()) == (int(1280 * screen_fit.FRACTION), int(680 * screen_fit.FRACTION))
    screen_fit.fit_to_screen(w, 500, 400)
    assert (w.width(), w.height()) == (500, 400)


def _fits(w, what):
    s = w.minimumSizeHint()
    assert s.width() <= MAX_W and s.height() <= MAX_H, (what, s.toTuple())
    assert w.width() <= SMALL.width() and w.height() <= SMALL.height(), (what, w.size().toTuple())


def test_windows_and_dialogs_fit_a_small_screen(qtbot, small, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from argus.ui.main_window import MainWindow
    from argus.ui.outputs_dialog import OutputsDialog
    from argus.ui.results.shower_view import ShowerView
    from argus.ui.results.window import ROLE, Ref, ResultsWindow
    from argus.ui.run_dialog import RunDialog

    for name in ("warning", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.Ok)
    app_settings().clear()
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy2(RES / "caja" / n, tmp_path / n)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.open_file(tmp_path / "caja.in")
    qtbot.waitUntil(lambda: w.input is not None, timeout=30000)
    _fits(w, "ventana principal")
    run = RunDialog(w, w.input, w.session, app_settings())
    qtbot.addWidget(run)
    _fits(run, "Ejecutar penhmain")
    run.reject()
    out = OutputsDialog(w, w.input, w.session)
    qtbot.addWidget(out)
    out.show()
    _fits(out, "Elegir qué resultados pide el .in")
    out.close()
    # La ventana de resultados, con cada resultado ya visto (las páginas que no se ven también cuentan).
    rw = ResultsWindow(settings=app_settings())
    qtbot.addWidget(rw)
    rw.show()
    for case in ("caja", "cilindro", "tac"):
        rw.open_folder(RES / case)
        qtbot.waitUntil(lambda: rw.loaded_token >= rw._token, timeout=30000)
        stack = [rw.tree.topLevelItem(i) for i in range(rw.tree.topLevelItemCount())]
        while stack:
            it = stack.pop(0)
            r = it.data(0, ROLE)
            if isinstance(r, Ref):
                rw.select(r)
                qtbot.waitUntil(lambda: rw.loaded_token >= rw._token, timeout=30000)
            stack.extend(it.child(i) for i in range(it.childCount()))
    _fits(rw, "ventana de resultados")
    sv = ShowerView(settings=app_settings())
    qtbot.addWidget(sv)
    assert sv.main_page.minimumSizeHint().width() <= MAX_W - 260  # junto al árbol de resultados
