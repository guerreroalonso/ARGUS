"""Ventana: panel «Simulación» (todo el .in en formularios) y editor de espectros."""

import shutil

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from argus.penhan import sections as sc  # noqa: E402
from argus.penhan.inputfile import parse_in_text  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

CASE = CORPUS / "usuario/100Mev15-174"
DISC = CORPUS / "penhan/disc"


@pytest.fixture
def case_dir(tmp_path):
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def doc_of(w):
    return parse_in_text(w.input.doc.text(), None)


def expand(w, cls):
    w.sim_panel.frame(cls).set_expanded(True)
    return w.sim_panel.section(cls)


# ---------------------------------------------------------------------------
# Editor de espectros
# ---------------------------------------------------------------------------

def test_spectrum_dialog_roles_and_lines(qtbot):
    from argus.ui.spectrum_dialog import SpectrumDialog, row_roles

    rows = sc.spectrum(parse_in_text((CASE / "maniqui.in").read_text(encoding="latin-1")))
    roles = row_roles(rows, lines=False)
    assert roles[-2][0] == "zero" and "26.9 %" in roles[-2][1]  # la línea de 100 MeV con P = 0,2686
    assert roles[-1][0] == "max"
    dlg = SpectrumDialog(None, rows, False, "Protones.")
    qtbot.addWidget(dlg)
    assert dlg.rows() == rows and not dlg.is_lines()
    assert dlg.btn_to_lines.isVisibleTo(dlg)
    dlg._to_lines()
    assert dlg.is_lines() and len(dlg.rows()) == len(rows) - 1 and all(p >= 0 for _, p in dlg.rows())
    dlg._normalize()
    assert abs(sum(p for _, p in dlg.rows()) - 1.0) < 1e-12
    # Una celda con unidades: «91,5 MeV».
    it = dlg.table.item(0, 0)
    it.setText("91,5 MeV")
    assert dlg.rows()[0][0] == 91.5e6


def test_spectrum_paste(qtbot):
    from PySide6.QtGui import QGuiApplication

    from argus.ui.spectrum_dialog import SpectrumDialog

    dlg = SpectrumDialog(None, [(1e6, 1.0), (2e6, -1.0)])
    qtbot.addWidget(dlg)
    dlg.unit.setCurrentText("MeV")
    QGuiApplication.clipboard().setText("E\tP\n10\t0,5\n20\t0,5\n30\t-1\n")
    dlg._paste()
    assert dlg.rows() == [(10e6, 0.5), (20e6, 0.5), (30e6, -1.0)]
    assert dlg.buttons.button(dlg.buttons.StandardButton.Ok).isEnabled()


def test_edit_spectrum_from_window(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")

    def run(dlg):
        dlg._to_lines()
        return True

    monkeypatch.setattr(w, "_exec_dialog", run)
    w.edit_spectrum()
    d = doc_of(w)
    assert not d.find("SPECTR") and len(d.find("SLINES")) == 12 and d.ok
    w.undo()
    assert len(doc_of(w).find("SPECTR")) == 13
    # Con energía única, primero se pregunta cómo darlo; a mano, el editor parte de un intervalo que acaba en ella.
    from argus.ui.spectrum_import import SpectrumStartDialog

    w.input.edit("mono", lambda doc: sc.set_value(doc, "SENERG", [1e8]) or doc.remove("SPECTR"))
    seen = []

    def manual(dlg):
        if isinstance(dlg, SpectrumStartDialog):
            seen.append("pregunta")
            dlg.choice = dlg.MANUAL
            return True
        seen.append(dlg.rows())
        return False

    monkeypatch.setattr(w, "_exec_dialog", manual)
    w.edit_spectrum()
    assert seen[0] == "pregunta" and seen[1][-1] == (1e8, -1.0)


# ---------------------------------------------------------------------------
# Panel «Simulación»
# ---------------------------------------------------------------------------

def test_materials_section_edits_msimpa(qtbot, window, case_dir):
    from argus.ui.sim_physics import MaterialsSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    sec = expand(w, MaterialsSection)
    assert sec.edits["EABS1"].value() == 1e4
    sec._on_value("MSIMPA", "C1", 0.5)
    qtbot.waitUntil(lambda: sc.material_params(w.input.doc, 1)["MSIMPA"][3] == 0.5)
    # penhmain recorta C1 a 0,2: el formulario lo dice.
    assert "C1 = 0.2" in sec.groups["MSIMPA"][1].text()
    sec._remove_line("MSIMPN")
    qtbot.waitUntil(lambda: sc.material_params(w.input.doc, 1)["MSIMPN"] is None)
    assert doc_of(w).ok
    w.undo()
    assert sc.material_params(w.input.doc, 1)["MSIMPN"] == [1.0, 500.0]


def test_impact_detector_and_highlight(qtbot, window, case_dir):
    from argus.ui.sim_outputs import ImpactSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    sec = expand(w, ImpactSection)
    assert not sec.dets
    sec._add()
    qtbot.waitUntil(lambda: len(sc.impact_detectors(w.input.doc)) == 1)
    # Marcar el cuerpo 1 en la lista.
    items = [sec.bodies.list.item(i) for i in range(sec.bodies.list.count())]
    target = next(it for it in items if it.data(Qt.ItemDataRole.UserRole)[1] == 1)
    target.setCheckState(Qt.CheckState.Checked)
    qtbot.waitUntil(lambda: sc.impact_detectors(w.input.doc)[0].bodies == [sc.BodyRef("   1", None)])
    assert w.view.highlight == {1} and w.view3d.highlight == {1}
    # Al plegar la sección se deja de resaltar.
    w.sim_panel.frame(ImpactSection).set_expanded(False)
    assert w.view.highlight == set()
    d = doc_of(w)
    assert d.ok and d.find("IMPDET") and d.find("IDBODY")


def test_variance_section_force_choices(qtbot, window, case_dir):
    from argus.ui.sim_physics import VarianceSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    sec = expand(w, VarianceSection)
    sec.iforce.add_row()
    qtbot.waitUntil(lambda: len(w.input.doc.find("IFORCE")) == 1)
    e = sc.body_list(w.input.doc, "IFORCE")[0]
    assert e.label == "   1" and e.values[:2] == [1, 4]
    combo = sec.iforce.table.cellWidget(0, 1)
    i = next(k for k in range(combo.count()) if list(combo.itemData(k)) == [4, 3])
    combo.setCurrentIndex(i)  # protones, colisiones inelásticas
    qtbot.waitUntil(lambda: sc.body_list(w.input.doc, "IFORCE")[0].values[:2] == [4, 3])
    assert doc_of(w).ok


def test_dose_section_kind_and_fit(qtbot, window, case_dir):
    from argus.ui.sim_outputs import DoseSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    sec = expand(w, DoseSection)
    assert sec.summary().startswith("cilindro")
    sec.kind.setCurrentIndex(sec.kind.findData("box"))
    sec._on_kind(0)
    qtbot.waitUntil(lambda: sc.dose_grid(w.input.doc).kind == "box")
    assert not w.input.doc.find("GRIDR")
    w.fit_dose_grid()
    g = sc.dose_grid(w.input.doc)
    lo, hi = w._matter_box()  # la caja de lo que tiene material, afinada con rayos (el cubo de agua, ±250 cm)
    assert g.x[:2] == (float(f"{lo[0]:.6g}"), float(f"{hi[0]:.6g}")) and g.x[2] == 11
    assert 250.0 <= hi[0] < 260.0 and -260.0 < lo[0] <= -250.0
    w.show_dose_grid()
    assert w.view.show_dose_grid


def test_job_section_seeds_and_resume_state(qtbot, window, case_dir):
    from argus.ui.sim_outputs import JobSection

    w = window
    (case_dir / "dump1.dmp").write_text("   Empty.\n")
    w.open_file(case_dir / "maniqui.in")
    sec = expand(w, JobSection)
    assert "no tiene una simulación guardada" in sec.resume_state.text()
    sec.seed_kind.setCurrentIndex(1)
    sec._on_seed_kind(1)
    qtbot.waitUntil(lambda: sc.seeds(w.input.doc)[-1][0] < 0)
    sec.s1.set_value(7.0)
    sec._on_seeds()
    qtbot.waitUntil(lambda: sc.seeds(w.input.doc)[-1] == (-7, 1))


def test_parinp_change_recompiles_geometry(qtbot, window, tmp_path):
    from argus.ui.sim_physics import GeometrySection

    for f in DISC.iterdir():
        shutil.copy2(f, tmp_path / f.name)
    w = window
    w.open_file(tmp_path / "disc.in")
    assert w.session.parinp[:2] == [40.0, 40.0]
    hi_before = float(w.session.bounds[1].max())
    sec = expand(w, GeometrySection)
    sec._on_parinp(2, 20.0)
    qtbot.waitUntil(lambda: w.session.parinp[:2] == [40.0, 20.0])
    assert w.session.ok and float(w.session.bounds[1].max()) != hi_before
    w.undo()
    assert w.session.parinp[:2] == [40.0, 40.0]


def test_section_status_shows_errors(qtbot, window, case_dir):
    from argus.ui.sim_outputs import ImpactSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("det", lambda d: sc.set_impact_detectors(d, [sc.ImpactDetector(0, 0, 100, 0, 1,
                                                                                 bodies=[sc.BodyRef(None, 2)])]))
    frame = w.sim_panel.frame(ImpactSection)
    assert "✖" in frame.status.text() and "vacío" in frame.status.toolTip()


def test_drag_dose_grid_in_slice(qtbot, window, case_dir):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    w = window
    w.open_file(case_dir / "maniqui.in")
    w.set_view_mode("2d")
    v = w.view
    v.set_plane("XZ")
    v.set_depth(0.0)
    v.center = (0.0, 4.5)
    v.scale = 0.05  # cm por píxel: la rejilla (9 cm) ocupa 180 píxeles
    v._request(quick=False)
    qtbot.waitUntil(lambda: v._image is not None)
    v.grab()  # dibuja: registra los tiradores de la rejilla
    assert {"grid_r", "grid_zlo", "grid_zhi"} <= set(v._handles)
    h = v._handles["grid_zhi"]
    start = QPoint(int(round(h.x())), int(round(h.y())))
    QTest.mousePress(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(v, start + QPoint(0, -40))
    QTest.mouseRelease(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(0, -40))
    g = sc.dose_grid(w.input.doc)
    assert g.kind == "cylinder" and g.z[1] > 9.0 and g.z[0] == 0.0 and g.r == (15.0, 40)
    w.undo()
    assert sc.dose_grid(w.input.doc).z == (0.0, 9.0, 200)


def test_psf_source_editing(qtbot, window, case_dir, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    w = window
    (case_dir / "fase.psf").write_text("")
    w.open_file(case_dir / "maniqui.in")
    sp = w.source_panel
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("fase.psf", True))
    sp._on_psf_on()
    d = doc_of(w)
    assert d.model.source.psf == ["fase.psf"] and d.ok
    assert sc.value(w.input.doc, "EPMAX") == [1e8, 1e8]
    assert sp.g_psf.isVisibleTo(sp) and not sp.g_particle.isVisibleTo(sp)
    sp.psf_split.set_value(None)
    sp.psf_split.setText("4")
    sp.psf_split.setModified(True)
    sp.psf_split._commit()
    assert sc.value(w.input.doc, "IPSPLI") == [4]
    sp._on_psf_off()
    d = doc_of(w)
    assert not d.model.source.psf and not d.find("IPSPLI") and not d.find("EPMAX") and d.ok


def test_detector_from_view_context_menu(qtbot, window, case_dir, monkeypatch):
    from PySide6.QtWidgets import QMenu

    from argus.ui.sim_outputs import ImpactSection

    w = window
    w.open_file(case_dir / "maniqui.in")
    menu = QMenu()
    w._add_detector_actions(menu, 1)
    sub = next(a.menu() for a in menu.actions() if a.menu() is not None)
    texts = [a.text() for a in sub.actions()]
    assert "Nuevo detector de impacto con este cuerpo" in texts
    w.toggle_detector_body("impact", 0, 1)
    dets = sc.impact_detectors(w.input.doc)
    assert len(dets) == 1 and dets[0].bodies == [sc.BodyRef("   1", None)]
    assert w.sim_panel.frame(ImpactSection).is_expanded() and w.view.highlight == {1}
    w.toggle_detector_body("impact", 1, 1)  # otra vez: se quita
    assert sc.impact_detectors(w.input.doc)[0].bodies == []
    # El cuerpo 2 es vacío: el submenú lo explica y no deja añadirlo.
    menu = QMenu()
    w._add_detector_actions(menu, 2)
    sub = next(a.menu() for a in menu.actions() if a.menu() is not None)
    assert not sub.actions()[0].isEnabled()
