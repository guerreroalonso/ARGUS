"""Resultados que pedir a penhmain por objetivos (penhan.goals y el diálogo del menú Resultados)."""

from __future__ import annotations

import shutil

import pytest

from argus.penhan import goals as gl
from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_text
from argus.penhan.simcheck import check_sections
from argus.results.case import load_case_geometry
from conftest import CORPUS

RES = CORPUS / "resultados"


def _doc(name: str):
    p = RES / name / f"{name}.in"
    return parse_in_text(p.read_text("latin-1"), str(p))


def test_status_of_a_case_with_every_output():
    doc = _doc("caja")
    geo = load_case_geometry(RES / "caja" / "caja.in").geo
    st = {g.key: gl.status(doc, g.key, geo) for g in gl.GOALS}
    assert st["dosis-caja"] == "11 × 11 × 25 vóxeles" and st["dosis-cilindro"] == "" and st["dosis-esfera"] == ""
    assert "detector 1" in st["espectro"] and "detector 2" in st["espectro"]
    assert "detector 2" in st["fluencia"] and "detector 1" not in st["fluencia"]
    assert "detector 1" in st["psf"]
    assert "DETE" in st["deposito"]
    assert st["angulos"] == "30 × 4 intervalos (θ × φ)"
    assert st["trayectorias"] == "6 lluvias (LTRACK 5: penhmain escribe también la siguiente)"


def test_apply_goals_to_a_simple_case(tmp_path):
    for f in ("cilindro.in", "cilindro.geo"):
        shutil.copy2(RES / "cilindro" / f, tmp_path / f)
    case = load_case_geometry(tmp_path / "cilindro.in")
    geo = case.geo
    p = tmp_path / "cilindro.in"
    doc = parse_in_text(p.read_text("latin-1"), str(p))
    assert gl.status(doc, "seguir") == ""
    assert "dump.dmp" not in gl.apply(doc, "seguir") and gl.status(doc, "seguir") == "cada 60 s en dump.dmp"
    kb = next(k for k, _t in gl.detector_bodies(geo))
    assert gl.apply(doc, "espectro", geo=geo, kb=kb).startswith("detector de impacto 1")
    # La fluencia y el espacio de fases del mismo cuerpo van en el mismo detector (un cuerpo, un detector).
    gl.apply(doc, "fluencia", geo=geo, kb=kb)
    gl.apply(doc, "psf", geo=geo, kb=kb)
    dets = sc.impact_detectors(doc)
    assert len(dets) == 1 and dets[0].idcut == 2 and dets[0].ipsf == 1
    assert gl.apply(doc, "deposito", geo=geo, kb=kb).startswith("detector de energía depositada 1")
    assert "ya está" in gl.apply(doc, "deposito", geo=geo, kb=kb)
    gl.apply(doc, "angulos")
    assert sc.value(doc, "NBANGL") == [30, gl.DEFAULT_NBANGL[1]]
    assert gl.apply(doc, "trayectorias") == "ya estaba"  # LTRACK 3
    # La rejilla: sustituye la cilíndrica por la de caja, que cubre la escena.
    gl.apply(doc, "dosis-caja", bounds=case.session.bounds)
    g = sc.dose_grid(doc)
    assert g.kind == "box" and g.x[2] % 2 == 1 and g.z[0] <= 0.0 and g.z[1] >= 3.0
    with pytest.raises(ValueError):
        gl.apply(doc, "espectro", geo=geo, kb=None)
    # El .in resultante no tiene errores de penhmain en los detectores ni en la rejilla.
    doc2 = parse_in_text(sc_text(doc), str(p))
    errs = [x for x in check_sections(doc2, geo, tmp_path).items if x.severity.name == "ERROR"
            and any(w in x.message for w in ("detector", "Detector", "GRID", "rejilla", "DUMP", "NBANGL"))]
    assert not errs, [e.message for e in errs]


def sc_text(doc) -> str:
    from argus.penhan.inputfile import write_in_text

    return write_in_text(doc)


def test_void_bodies_cannot_be_detectors(tmp_path):
    case = load_case_geometry(RES / "caja" / "caja.in")
    doc = _doc("caja")
    bodies = [kb for kb, _t in gl.detector_bodies(case.geo)]
    assert bodies == [1, 2, 3]
    geo = case.geo
    geo.body(2).material = 0  # como si fuera vacío
    try:
        with pytest.raises(ValueError, match="vacío"):
            gl.apply(doc, "espectro", geo=geo, kb=2)
    finally:
        geo.body(2).material = 1


@pytest.fixture
def cilindro_window(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow
    from argus.ui.settings import app_settings

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    locate_fast.warmup()
    app_settings().clear()
    for f in ("cilindro.in", "cilindro.geo"):
        shutil.copy2(RES / "cilindro" / f, tmp_path / f)
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_file(str(tmp_path / "cilindro.in"))
    yield w
    w.close()


@pytest.mark.gui
def test_outputs_dialog(qtbot, cilindro_window):
    """Cada resultado con su casilla y su editor: marcar lo pide, desmarcar lo quita, y todo se deshace."""
    from argus.ui.outputs_dialog import OutputsDialog
    from argus.ui.sim_outputs import DoseSection

    w = cilindro_window
    assert w.act_outputs.isEnabled()
    dlg = OutputsDialog(w, w.input, w.session)
    qtbot.addWidget(dlg)
    dlg.show()
    cards = dlg.cards
    assert cards["dosis"].check.isChecked() and "cilindro, 300 anillos" in cards["dosis"].state.text()
    assert cards["dosis"].editor.isVisibleTo(dlg) and isinstance(cards["dosis"].editor, DoseSection)
    assert "trayectorias" not in cards  # (0.17.1: las lluvias para verlas en 3D son «Lluvias», al ejecutar)
    assert not cards["seguir"].check.isChecked() and not cards["seguir"].editor.isVisibleTo(dlg)
    assert cards["salen"].check.isChecked() and not cards["salen"].check.isEnabled()  # siempre
    assert cards["deposito"].body_row.isVisibleTo(dlg)
    doc = w.input.doc
    # Marcar: lo pide con valores razonables y enseña su editor.
    cards["seguir"].check.setChecked(True)
    dlg.flush_pending()
    assert sc.value(doc, "DUMPP") == [60.0] and cards["seguir"].editor.isVisibleTo(dlg)
    cards["deposito"].check.setChecked(True)
    dlg.flush_pending()
    assert len(sc.energy_detectors(doc)) == 1 and cards["deposito"].check.isChecked()
    assert not cards["deposito"].body_row.isVisibleTo(dlg) and cards["deposito"].editor.isVisibleTo(dlg)
    # Los editores cambian el .in (los intervalos del detector, por ejemplo).
    ed = cards["deposito"].editor
    ed.bins.valueEdited.emit(70)
    dlg.flush_pending()
    assert sc.energy_detectors(doc)[0].nbe == 70
    # Desmarcar lo quita; la dosis no, mientras EPROF la use (penhmain se pararía: 3110).
    cards["dosis"].check.setChecked(False)
    dlg.flush_pending()
    assert sc.dose_grid(doc).kind == "cylinder" and cards["dosis"].check.isChecked()
    assert "EPROF" in cards["dosis"].message.text() and cards["dosis"].message.isVisibleTo(dlg)
    w.input.edit("Sin EPROF", lambda d: sc.set_external_process(d, None))
    cards["dosis"].check.setChecked(False)
    dlg.flush_pending()
    assert sc.dose_grid(doc).kind == "none" and not cards["dosis"].editor.isVisibleTo(dlg)
    cards["seguir"].check.setChecked(False)
    dlg.flush_pending()
    assert sc.value(doc, "DUMPP") is None and not cards["seguir"].editor.isVisibleTo(dlg)
    # Cada cambio, un paso de deshacer (también desde el botón del diálogo).
    dlg.btn_undo.click()
    assert sc.value(doc, "DUMPP") == [60.0]
    w.input.stack.undo()  # desde la ventana: el diálogo lo ve al volver al bucle de eventos
    assert sc.dose_grid(doc).kind == "cylinder"
    qtbot.waitUntil(lambda: cards["dosis"].check.isChecked(), timeout=5000)
    # Pedir la caja otra vez desde cero: con la geometría.
    cards["dosis"].check.setChecked(False)
    dlg.flush_pending()
    cards["dosis"].check.setChecked(True)
    dlg.flush_pending()
    assert sc.dose_grid(doc).kind == "box"
    assert sc.value(doc, "LTRACK") == [3]  # el diálogo no lo toca (ni lo enseña)
    dlg.close()


@pytest.mark.gui
def test_the_outputs_dialog_is_not_modal_and_follows_the_window(qtbot, cilindro_window):
    w = cilindro_window
    w.choose_outputs()
    dlg = w._outputs_dialog
    assert dlg.isVisible() and not dlg.isModal()
    w.choose_outputs()
    assert w._outputs_dialog is dlg  # la misma
    # Lo que se cambia en la ventana (el panel, deshacer) se ve en el diálogo.
    w.input.edit("prueba", lambda d: sc.set_value(d, "DUMPP", [120.0]))
    qtbot.waitUntil(lambda: "cada 120 s en dump.dmp" in dlg.cards["seguir"].state.text(), timeout=5000)
    dlg.close()


def test_voxel_size_sets_the_number_of_bins():
    assert sc.bins_for_voxel(-5, 5, 0.2, 101) == (-5.1, 5.1, 51, None)  # impar y con el mismo centro
    assert sc.bins_for_voxel(-5, 5, 0.2, 101, odd=False) == (-5.0, 5.0, 50, None)
    assert sc.bins_for_voxel(0, 30, 0.1, 401, odd=False, anchor="start") == (0.0, 30.0, 300, None)
    a, b, n, note = sc.bins_for_voxel(0, 30, 0.07, 401, odd=False, anchor="start")
    assert (a, b, n) == (0, 30, 401) and "Como mucho 401" in note
    # Un eje al revés: penhmain le da la vuelta (empieza en el menor), y la profundidad se ancla ahí.
    assert sc.bins_for_voxel(10, 0, 3.0, 401, odd=False, anchor="start")[:3] == (0.0, 9.0, 3)
    assert sc.radial_bins_for(4, 0.1) == (4.0, 40, None)
    assert sc.radial_bins_for(40, 0.1)[1] == 101
    with pytest.raises(ValueError):
        sc.bins_for_voxel(0, 1, 0.0, 101)
    g = sc.DoseGrid((-1, 1, 3), (-1, 1, 5), (0, 1, 7))
    assert sc.dose_grid_cells(g) == 105 and sc.dose_grid_cells(sc.DoseGrid(z=(0, 1, 4), r=(1, 5))) == 20


@pytest.mark.gui
def test_the_dose_editor_takes_the_voxel_size(qtbot):
    from argus.ui.sim_outputs import DoseSection

    doc = _doc("caja")
    sec = DoseSection()
    qtbot.addWidget(sec)
    edits = []
    sec.apply.connect(lambda text, fn: (edits.append(text), fn(doc), sec.set_context(doc, None, None)))
    sec.set_context(doc, None, None)
    assert sec.voxel_size["x"].value() == pytest.approx(10 / 11) and sec.voxel_size["z"].value() == pytest.approx(0.2)
    assert "11 × 11 × 25" in sec.voxel.text() and "kB" in sec.voxel.text()
    sec.voxel_size["x"].valueEdited.emit(0.5)
    g = sc.dose_grid(doc)
    assert g.x == (-5.25, 5.25, 21) and edits == ["Tamaño del vóxel"]
    sec.voxel_size["z"].valueEdited.emit(0.1)
    assert sc.dose_grid(doc).z == (0.0, 5.0, 50)
    sec.voxel_size["y"].valueEdited.emit(0.01)
    assert sc.dose_grid(doc).y[2] == 101 and "Como mucho 101" in sec.size_note.text()


def test_only_one_detector_writes_the_phase_space():
    case = load_case_geometry(RES / "caja" / "caja.in")
    geo = case.geo
    doc = _doc("caja")  # el detector 1 (PLAN) ya escribe el espacio de fases
    assert "espacio de fases" in gl.apply(doc, "psf", geo=geo, kb=2)
    for kb in (1, 3):  # otro cuerpo, en otro detector o en uno nuevo: penhmain solo admite uno
        with pytest.raises(ValueError, match="ya escribe el espacio de fases"):
            gl.apply(_doc("caja"), "psf", geo=geo, kb=kb)


def test_default_radial_grids_cover_the_scene():
    # Con el origen (o el eje z) bien dentro de la escena, la mayor esfera o cilindro que cabe en ella.
    assert gl.default_grid("dosis-esfera", ((-10, -10, -10), (10, 10, 10))).r == (10.0, 50)
    assert gl.default_grid("dosis-cilindro", ((-5, -5, 0), (5, 5, 30))).r == (5.0, 50)
    # En un borde de la escena, la que la cubre entera.
    assert gl.default_grid("dosis-esfera", ((0, 0, 0), (10, 10, 10))).r == (17.33, 50)  # √300 = 17,3205…: la cubre
    g = gl.default_grid("dosis-cilindro", ((0, -5, 0), (10, 5, 30)))
    assert g.r == (11.19, 50) and g.z == (0.0, 30.0, 101)
