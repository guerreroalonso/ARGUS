"""Interfaz del espectro de la fuente: a mano o desde un fichero, vista previa, importar y guardar."""

import shutil

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox  # noqa: E402

from argus.penhan import spectrum_file as sf  # noqa: E402
from argus.penhan.inputfile import parse_in_text  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

DISC = CORPUS / "penhan/disc"  # protones de 200 MeV (SENERG)
RES = CORPUS / "resultados"


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    for f in DISC.iterdir():
        shutil.copy2(f, tmp_path / f.name)
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_file(tmp_path / "disc.in")
    assert w.input is not None and w.input.doc.first("SENERG") is not None
    yield w
    app_settings().clear()


def _doc(w):
    return parse_in_text(w.input.doc.text(), None)


def test_from_a_single_energy_to_a_spectrum_file(qtbot, window, tmp_path, monkeypatch):
    from argus.ui.spectrum_dialog import SpectrumDialog
    from argus.ui.spectrum_import import SpectrumImportDialog, SpectrumStartDialog

    w = window
    spec = tmp_path / "sobp.csv"
    spec.write_text("Energía (MeV);peso\n180;0,2\n190;0,3\n200;0,5\n210;\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(spec), ""))
    seen = {}

    def drive(dlg):
        if isinstance(dlg, SpectrumStartDialog):
            seen["start"] = True
            dlg.choice = dlg.FILE
            return True
        if isinstance(dlg, SpectrumImportDialog):
            seen["preview"] = dlg.info.text()
            assert dlg.unit.currentData() == "MeV" and dlg.rb_cont.isChecked()
            return dlg.result() is not None
        assert isinstance(dlg, SpectrumDialog)
        seen["table"] = dlg.rows()
        return True

    monkeypatch.setattr(w, "_exec_dialog", drive)
    w.edit_spectrum()
    assert seen["start"] and "3 intervalos" in seen["preview"]
    d = _doc(w)
    assert d.ok and d.first("SENERG") is None
    rows = [(float(x.values[0]), float(x.values[1])) for x in d.find("SPECTR")]
    assert [e for e, _ in rows] == [180e6, 190e6, 200e6, 210e6]
    assert [p for _, p in rows[:-1]] == pytest.approx([0.2, 0.3, 0.5]) and rows[-1][1] == -1.0
    assert app_settings().value("fuente/espectros") == str(tmp_path)  # la carpeta se recuerda
    # Deshacer vuelve a la energía única.
    w.undo()
    assert _doc(w).first("SENERG") is not None and not _doc(w).find("SPECTR")
    # Cancelar en la pregunta no toca nada.
    monkeypatch.setattr(w, "_exec_dialog", lambda dlg: False)
    before = w.input.doc.text()
    w.edit_spectrum()
    assert w.input.doc.text() == before


def test_import_preview_options_and_penhmain_series(qtbot):
    from argus.ui.spectrum_import import SpectrumImportDialog

    data = sf.read_spectrum_file(RES / "caja" / "spc-impdet-01.dat")
    dlg = SpectrumImportDialog(None, data, particle=4)  # la fuente es de protones; el espectro, de fotones
    qtbot.addWidget(dlg)
    names = [dlg.series.itemText(i) for i in range(dlg.series.count())]
    assert any(n.startswith("fotones") for n in names) and any("(vacía)" in n for n in names)
    dlg.series.setCurrentIndex(next(i for i, n in enumerate(names) if n.startswith("fotones")))
    res = dlg.result()
    assert res is not None and res.particle == 2 and "es de protones" in dlg.info.text()
    empty = next(i for i, n in enumerate(names) if "(vacía)" in n)
    dlg.series.setCurrentIndex(empty)
    assert dlg.result() is None and not dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()
    # Una tabla: se puede cambiar cómo se entienden las columnas y se ve al momento.
    table = sf.parse_table_text("E (keV) P\n10 1\n20 1\n30 1\n", "t.txt")
    dlg2 = SpectrumImportDialog(None, table, particle=2)
    qtbot.addWidget(dlg2)
    assert dlg2.result().rows[-1] == (pytest.approx(40e3), -1.0)  # bordes inferiores; la última fila tenía valor
    dlg2.position.setCurrentIndex(dlg2.position.findData("center"))
    assert dlg2.result().rows[0][0] == pytest.approx(5e3) and dlg2.result().rows[-1][0] == pytest.approx(35e3)
    dlg2.rb_lines.click()
    assert dlg2.result().lines and not dlg2.position.isEnabled()
    assert not dlg2.position.model().item(dlg2.position.findData("edges")).isEnabled()  # hacen falta 3 columnas


def test_spectrum_editor_saves_and_imports(qtbot, tmp_path, monkeypatch):
    from argus.ui.spectrum_dialog import SpectrumDialog
    from argus.ui.spectrum_import import SpectrumImportDialog

    rows = [(48e6, 0.4), (49e6, 0.6), (50e6, -1.0)]
    dlg = SpectrumDialog(None, rows, False, kparp=4, settings=app_settings())
    qtbot.addWidget(dlg)
    target = tmp_path / "espectro.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    dlg._save()
    text = target.read_text("utf-8")
    assert "# unidad: MeV" in text and "\n48\t0.4\n" in text
    # Otro editor lo importa (con su vista previa) y queda igual.
    other = SpectrumDialog(None, [(1e6, 1.0), (2e6, -1.0)], True, kparp=4, settings=app_settings())
    qtbot.addWidget(other)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(SpectrumImportDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    other._import()
    assert not other.is_lines() and other.unit.currentText() == "MeV"
    got = other.rows()
    assert [e for e, _ in got] == [48e6, 49e6, 50e6] and [p for _, p in got[:-1]] == pytest.approx([0.4, 0.6])
