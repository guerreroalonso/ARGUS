"""El único módulo del nivel superior (sin envolvente) se mueve y se gira desde la ventana: fuera de él no hay nada."""

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from test_placement import ONLY_MODULE  # noqa: E402

pytestmark = pytest.mark.gui


def test_rotate_the_whole_geometry_from_the_window(qtbot, monkeypatch, tmp_path):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    locate_fast.warmup()
    path = tmp_path / "cilindro.geo"
    path.write_text(ONLY_MODULE, encoding="latin-1")
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_file(str(path))
    assert w.session.ok and w.session.geo.nbodys == 1
    w.rotate_element(1, [0.0, 1.0, 0.0], 30.0)
    assert not shown, shown
    el = w.session.geo.body(1).element
    assert any(p.name == "THETA" and abs(p.value - 30.0) < 1e-9 for p in el.transform)
    w.undo()
    assert not w.session.geo.body(1).element.transform


def test_the_disc_example_of_penhan(qtbot, monkeypatch, tmp_path):
    # El caso de Rafael: examples/disc de penhan (un solo módulo, medidas con PARINP).
    import shutil

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow
    from conftest import CORPUS

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    locate_fast.warmup()
    for f in (CORPUS / "penhan/disc").iterdir():
        shutil.copy2(f, tmp_path / f.name)
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_file(tmp_path / "disc.in")
    assert w.session.ok and w.session.geo.nbodys == 1 and not w.session.geo.enclosure_added
    w.rotate_element(1, [1.0, 0.0, 0.0], 90.0)
    assert not shown, shown
    s = w.session
    assert s.ok and s.parinp  # sigue con sus PARINP
    # Girado 90° alrededor de x por su centro (0, 0, 20): el eje del cilindro (40 de alto, radio 40) va a lo largo
    # de y, entre -20 y 20. z = 59 queda dentro (antes acababa en z = 40); y = 25, fuera.
    ib, _ = s.locator.locate(np.array([[0.0, 19.0, 20.0], [0.0, 0.0, 59.0], [0.0, 25.0, 20.0]]))
    assert list(ib) == [1, 1, 2]
