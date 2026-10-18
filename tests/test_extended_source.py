"""Fuente extensa en un cuerpo (0.16.1): la caja SBOX justo alrededor del cuerpo, de su parte del órgano o de todo él.

Rafael (14-10-2026): en el maniquí adulto del ORNL (`adult.geo`), la fuente extensa en el pulmón no salía de todo el
pulmón. «Fuente extensa limitada a…» ponía una caja cúbica de una décima de la geometría (20 cm) alrededor del punto
pulsado: del pulmón (siete cuerpos «BODY LUNGS», 27 × 15 × 24 cm) solo salía lo que quedaba dentro; y marcar los demás
trozos como cuerpos activos no cambiaba la caja (el corte lo enseñaba bien: solo esa parte rayada). Después: que emita
solo en lo elegido (no en toda la caja), para cualquier geometría, y poder coger uno o los dos (un pulmón o los dos).
"""

from __future__ import annotations

import os
import shutil
import sys

import numpy as np
import pytest

from argus.penhan import source_edit as se
from argus.penhan.inputfile import parse_in_text
from conftest import CORPUS

ADULT = CORPUS / "ornl" / "adult.geo"
LUNGS = [124, 125, 126, 127, 128, 129, 130]


@pytest.fixture(scope="module")
def adult():
    from argus.session import open_geometry

    s = open_geometry(ADULT)
    assert s.ok
    return s


def test_the_box_around_some_bodies_covers_them_with_round_numbers():
    lo, hi = [-13.47, -7.46, 46.0], [-5.4, 1.5, 54.0]
    c, s = se.fitted_box(lo, hi)
    assert all(c[i] - s[i] / 2 < lo[i] and c[i] + s[i] / 2 > hi[i] for i in range(3))
    assert all(round(v, 6) == v for v in c + s)  # números redondos, sin ruido
    # Un margen pequeño en cada eje: entre una y dos milésimas del tamaño por cada lado.
    assert all(0.001 < s[i] - (hi[i] - lo[i]) < 0.0045 for i in range(3)), s
    # Un punto o una caja diminuta: también una caja (no de lado 0).
    c, s = se.fitted_box([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    assert min(s) > 0 and c == [1.0, 1.0, 1.0]


BOXED = """TITLE  Prueba ARGUS
SKPAR  2
SENERG 1.0E6
SPOSIT 0 0 0
SBOX   10 10 10
SBODY  1
MFNAME water.mat
GEOMFN a.geo
NSIMSH 1.0E6
"""


def test_box_contains_and_set_box_around():
    doc = parse_in_text(BOXED)
    src = doc.model.source
    assert se.box_contains(src, [-5, -5, -5], [5, 5, 5])
    assert not se.box_contains(src, [-5, -5, -5], [5, 5, 5.1])
    se.set_box_around(doc, [0, 0, 0], [2, 4, 6])
    src = doc.model.source
    assert src.extended and se.box_contains(src, [0, 0, 0], [2, 4, 6])
    assert src.position == (1.0, 2.0, 3.0) and doc.first("SBODY") is not None  # los cuerpos activos no se tocan
    point = parse_in_text(BOXED.replace("SBOX   10 10 10\nSBODY  1\n", "")).model.source
    assert not se.box_contains(point, [0, 0, 0], [0, 0, 0])  # una fuente puntual no tiene caja


def test_connected_parts():
    def box(lo, hi):
        return np.asarray(lo, float), np.asarray(hi, float)

    boxes = {
        1: box([0, 0, 0], [1, 1, 1]),
        2: box([1, 0, 0], [2, 1, 1]),  # toca al 1 en x = 1
        3: box([2.0000005, 0, 0], [3, 1, 1]),  # a 5e-7 del 2: lo toca (tolerancia de 1e-6 del tamaño)
        4: box([5, 0, 0], [6, 1, 1]),  # lejos
        5: box([5.5, 0.5, 0.5], [7, 2, 2]),  # se solapa con el 4
    }
    assert se.connected_parts([5, 4, 3, 2, 1], boxes) == [[1, 2, 3], [4, 5]]
    assert se.connected_parts([1, 4], boxes) == [[1], [4]]
    assert se.connected_parts([3, 1], boxes) == [[1], [3]]  # sin el 2 entre medias, separados
    assert se.connected_parts([], boxes) == []
    inf = box([-np.inf] * 3, [np.inf] * 3)
    assert se.connected_parts([1, 4, 9], {**boxes, 9: inf}) == [[1, 4, 9]]  # sin cota, puede tocar a cualquiera


def test_the_organ_of_a_body_is_the_bodies_with_its_description_and_material(adult):
    geo = adult.geo
    assert se.body_description(geo, 126) == "BODY LUNGS"
    assert se.body_group(geo, 126) == LUNGS
    assert se.body_group(geo, 131) == [131]  # el hígado es un cuerpo
    assert se.body_description(geo, geo.nbodys + 5) == ""


def test_the_fitted_box_holds_every_point_of_the_body(adult):
    """Puntos al azar alrededor de cada trozo del pulmón: todos los que LOCATE sitúa en él caen dentro de la caja
    (la caja exacta de cada cuerpo con el trazador, la que usa la ventana)."""
    from argus.pengeom.extent import element_extent
    from argus.pengeom.locate import Locator
    from argus.render.raytrace import RayTracer

    geo = adult.geo
    tracer = RayTracer(geo)
    loc = Locator(geo)
    rng = np.random.default_rng(11)
    for kb in LUNGS:
        lo, hi = element_extent(geo, tracer, kb, adult.bounds)
        c, s = (np.asarray(v) for v in se.fitted_box(lo, hi))
        pad = 0.25 * (hi - lo) + 0.5
        pts = rng.uniform(lo - pad, hi + pad, size=(40000, 3))
        inside = pts[loc.locate(pts)[0] == kb]
        assert len(inside) > 1000
        assert np.all(np.abs(inside - c) <= 0.5 * s), kb


@pytest.mark.skipif(not os.environ.get("ARGUS_SLOW_TESTS"), reason="prueba lenta (ARGUS_SLOW_TESTS=1)")
@pytest.mark.parametrize("name, n_groups", [("adult.geo", 68), ("newborn.geo", None)])
def test_every_organ_fits_in_its_box(name, n_groups):
    """Todos los órganos de los maniquíes adulto y recién nacido (en el adulto, 68 y las 32 partes de los que tienen
    dos, tres o cuatro): todo punto que LOCATE sitúa en sus cuerpos cae en la caja que pone «Fuente extensa en ▸» (con
    el penhmain de Rafael, además, las partículas salen solo de esos cuerpos y en proporción a su volumen: ADR-020,
    apartado 8)."""
    from argus.pengeom.extent import locator_for, source_extent
    from argus.session import open_geometry

    s = open_geometry(CORPUS / "ornl" / name)
    geo = s.geo
    loc = locator_for(geo)
    found = {b.index: source_extent(geo, b.index, s.bounds) for b in geo.bodies if b.origin != "enclosure"}
    ext = {kb: e[:2] for kb, e in found.items() if e[0] is not None}
    groups = {tuple(k for k in se.body_group(geo, kb) if k in ext) for kb in ext}
    cases = []
    for g in groups:
        cases.append(list(g))
        parts = se.connected_parts(list(g), ext)
        if 1 < len(parts) <= 4:
            cases.extend(parts)
    if n_groups is not None:
        assert len(groups) == n_groups and len(cases) == 100
    rng = np.random.default_rng(5)
    for kbs in cases:
        lo = np.min([ext[k][0] for k in kbs], axis=0)
        hi = np.max([ext[k][1] for k in kbs], axis=0)
        c, side = (np.asarray(v) for v in se.fitted_box(lo, hi))
        pad = 0.5 * (hi - lo) + 0.5
        pts = rng.uniform(lo - pad, hi + pad, size=(200_000, 3))
        inside = pts[np.isin(loc.locate(pts)[0], kbs)]
        assert len(inside) > 0 and np.all(np.abs(inside - c) <= 0.5 * side), (name, kbs)


# -- en la ventana ----------------------------------------------------------------------------------------------------

pytest.importorskip("PySide6")
from test_gui_source import window  # noqa: E402, F401


@pytest.fixture
def adult_window(window, tmp_path):  # noqa: F811
    shutil.copy2(ADULT, tmp_path / "adult.geo")
    w = window
    w.open_file(tmp_path / "adult.geo", with_input=False, ask=False)
    w.new_input()
    return w


def _menu(w, monkeypatch, kb, point):
    shown = {}

    def fake_exec(menu, *_a):
        shown.update({a.text(): a for a in menu.actions()})

    monkeypatch.setattr(w, "_exec_menu", fake_exec)
    w._on_context_menu(None, np.asarray(point, float), kb)
    return shown


def _sub(actions, title):
    """Lo que enseña un submenú al abrirlo (se rellena entonces)."""
    menu = actions[title].menu()
    menu.aboutToShow.emit()
    return {a.text(): a for a in menu.actions()}


LUNG_XNEG, LUNG_XPOS = [124, 126, 128, 130], [125, 127, 129]  # los trozos de cada pulmón (x < 0 y x > 0)
P126, P127 = [-8.5, 4.5, 55.0], [8.5, 4.5, 55.0]


@pytest.mark.gui
def test_the_extended_source_in_a_lung_covers_the_whole_lung(qtbot, adult_window, monkeypatch):
    w = adult_window
    geo = w.session.geo
    sub = _sub(_menu(w, monkeypatch, 126, P126), "Fuente extensa en")
    assert list(sub) == ["Cuerpo «126» (BODY LUNGS)", "Este «BODY LUNGS» (4 cuerpos unidos)",
                         "Los 2 «BODY LUNGS» (7 cuerpos)"]
    sub["Cuerpo «126» (BODY LUNGS)"].trigger()
    src = w.input.doc.model.source
    assert se.active_bodies(w.input.doc, geo) == {126} and w.source_box_covers([126])
    assert src.box[0] < 11 and src.box[2] > 23  # alrededor del trozo (antes, un cubo de 20 cm en el punto)
    assert "solo de él" in w.statusBar().currentMessage()
    w.undo()
    sub["Los 2 «BODY LUNGS» (7 cuerpos)"].trigger()
    src = w.input.doc.model.source
    assert se.active_bodies(w.input.doc, geo) == set(LUNGS) and w.source_box_covers(LUNGS)
    assert src.position == (0.0, 0.0, 55.5) and src.box == (27.02, 15.02, 24.02)
    # Ya extensa: el cuerpo es activo (se puede quitar), no hay nada que añadir ni que ajustar.
    actions = _menu(w, monkeypatch, 126, P126)
    active = actions["Cuerpo activo de la fuente: Cuerpo «126» (BODY LUNGS)"]
    assert active.isChecked() and "Añadir a los cuerpos activos" not in actions
    assert "Ajustar la caja de la fuente a los cuerpos activos" not in actions
    active.trigger()
    assert 126 not in se.active_bodies(w.input.doc, geo) and w.input.doc.model.source.box == (27.02, 15.02, 24.02)


@pytest.mark.gui
def test_one_lung_or_both(qtbot, adult_window, monkeypatch):
    """Rafael (14-10-2026): poder coger uno o los dos. Las partes del órgano son los cuerpos que se tocan."""
    w = adult_window
    geo = w.session.geo
    assert w.organ_parts(LUNGS) == [LUNG_XNEG, LUNG_XPOS]
    _sub(_menu(w, monkeypatch, 126, P126), "Fuente extensa en")["Este «BODY LUNGS» (4 cuerpos unidos)"].trigger()
    src = w.input.doc.model.source
    assert se.active_bodies(w.input.doc, geo) == set(LUNG_XNEG) and w.source_box_covers(LUNG_XNEG)
    assert src.position[0] + src.box[0] / 2 < 0  # la caja no llega al otro pulmón
    assert "esos 4 cuerpos y solo de ellos" in w.statusBar().currentMessage()
    # El otro, desde uno de sus cuerpos: también él (la caja se ajusta a los dos) o solo él.
    actions = _menu(w, monkeypatch, 127, P127)
    assert not actions["Cuerpo activo de la fuente: Cuerpo «127» (BODY LUNGS)"].isChecked()
    assert list(_sub(actions, "Fuente extensa en")) == ["Cuerpo «127» (BODY LUNGS)",
                                                        "Este «BODY LUNGS» (3 cuerpos unidos)",
                                                        "Los 2 «BODY LUNGS» (7 cuerpos)"]
    add = _sub(actions, "Añadir a los cuerpos activos")
    assert list(add) == ["Este «BODY LUNGS» (3 cuerpos unidos)", "Los 2 «BODY LUNGS» (7 cuerpos)"]
    add["Este «BODY LUNGS» (3 cuerpos unidos)"].trigger()
    assert se.active_bodies(w.input.doc, geo) == set(LUNGS) and w.source_box_covers(LUNGS)
    assert w.input.doc.model.source.box == (27.02, 15.02, 24.02)
    w.undo()
    actions = _menu(w, monkeypatch, 127, P127)
    _sub(actions, "Fuente extensa en")["Este «BODY LUNGS» (3 cuerpos unidos)"].trigger()
    src = w.input.doc.model.source
    assert se.active_bodies(w.input.doc, geo) == set(LUNG_XPOS) and src.position[0] - src.box[0] / 2 > 0
    # Desde un cuerpo del pulmón que ya es activo, añadir su pulmón no se ofrece (ya lo es).
    add = _sub(_menu(w, monkeypatch, 127, P127), "Añadir a los cuerpos activos")
    assert not add["Este «BODY LUNGS» (3 cuerpos unidos)"].isEnabled()
    assert add["Los 2 «BODY LUNGS» (7 cuerpos)"].isEnabled()


def _organ(geo, desc):
    return [b.index for b in geo.bodies if se.body_description(geo, b.index) == desc]


@pytest.mark.gui
def test_the_parts_of_other_organs(qtbot, adult_window, monkeypatch):
    """No solo los pulmones: los dos riñones (un cuerpo cada uno), los brazos (cuatro trozos cada uno, sin cota
    analítica: hacen falta las cajas exactas), las doce costillas, el tiroides (cinco trozos que se tocan)."""
    w = adult_window
    geo = w.session.geo
    kidneys = _organ(geo, "BODY KIDNEYS")
    assert len(kidneys) == 2 and w.organ_parts(kidneys) == [[k] for k in kidneys]
    arms = _organ(geo, "BODY ARMBONES")
    assert [len(p) for p in w.organ_parts(arms)] == [4, 4]
    ribs = _organ(geo, "BODY RIGCAGE")
    assert w.organ_parts(ribs) == [[k] for k in ribs] and len(ribs) == 12
    thyroid = _organ(geo, "BODY THYROID")
    assert w.organ_parts(thyroid) == [thyroid] and len(thyroid) == 5
    # En el menú: un riñón (el cuerpo) o los dos; un brazo o los dos; el tiroides entero.
    sub = _sub(_menu(w, monkeypatch, kidneys[0], [0, 0, 30]), "Fuente extensa en")
    assert list(sub)[1:] == ["Los 2 «BODY KIDNEYS» (2 cuerpos)"]
    sub = _sub(_menu(w, monkeypatch, arms[0], [18.5, 0, 50]), "Fuente extensa en")
    assert list(sub)[1:] == ["Este «BODY ARMBONES» (4 cuerpos unidos)", "Los 2 «BODY ARMBONES» (8 cuerpos)"]
    sub["Este «BODY ARMBONES» (4 cuerpos unidos)"].trigger()
    assert w.source_box_covers(arms[:4]) and w.input.doc.model.source.position[0] > 15
    sub = _sub(_menu(w, monkeypatch, thyroid[0], [1.5, 1, 74.5]), "Fuente extensa en")
    assert list(sub)[1:] == ["Todo «BODY THYROID» (5 cuerpos)"]


@pytest.mark.gui
def test_adding_a_body_the_box_does_not_cover_fits_the_box(qtbot, adult_window, monkeypatch):
    w = adult_window
    geo = w.session.geo
    _sub(_menu(w, monkeypatch, 126, P126), "Fuente extensa en")["Cuerpo «126» (BODY LUNGS)"].trigger()
    before = w.input.doc.model.source.box
    # Un trozo del otro pulmón, desde el menú: la caja se ajusta a los dos (y Deshacer la deja como estaba, en un paso).
    actions = _menu(w, monkeypatch, 127, P127)
    actions["Cuerpo activo de la fuente: Cuerpo «127» (BODY LUNGS)"].trigger()
    assert se.active_bodies(w.input.doc, geo) == {126, 127} and w.source_box_covers([126, 127])
    assert "cubrir enteros" in w.statusBar().currentMessage()
    w.undo()
    assert w.input.doc.model.source.box == before and se.active_bodies(w.input.doc, geo) == {126}
    # «Añadir a los cuerpos activos ▸ Los 2 «BODY LUNGS»»: todos, con la caja de todos.
    _sub(_menu(w, monkeypatch, 126, P126), "Añadir a los cuerpos activos")["Los 2 «BODY LUNGS» (7 cuerpos)"].trigger()
    assert se.active_bodies(w.input.doc, geo) == set(LUNGS) and w.source_box_covers(LUNGS)


@pytest.mark.gui
def test_the_panel_says_when_the_box_cuts_a_body_and_fits_it(qtbot, adult_window, monkeypatch):
    from PySide6.QtCore import Qt

    from argus.ui.source_panel import ROLE_REF

    w = adult_window
    geo = w.session.geo
    panel = w.source_panel
    # Como el .in de antes: la caja cúbica de 20 cm en un punto y los siete trozos activos.

    def old(d):
        se.set_position(d, [-8.5, 4.5, 55.0])
        se.set_box(d, [20, 20, 20])
        se.set_active_bodies(d, geo, LUNGS)

    w.input.edit("como antes", old)
    assert panel.bodies_hint.text().startswith("⚠ La caja no cubre enteros") and panel.btn_fit_box.isVisibleTo(panel)
    actions = _menu(w, monkeypatch, 126, [-8.5, 4.5, 55.0])
    assert "Ajustar la caja de la fuente a los cuerpos activos" in actions
    panel.fit_box()
    assert w.source_box_covers(LUNGS) and not panel.bodies_hint.text().startswith("⚠")
    assert w.input.doc.model.source.box == (27.02, 15.02, 24.02)
    # En la lista del panel: el órgano se ve (y se filtra) y marcar un cuerpo que la caja no cubre la ajusta.
    texts = [panel.body_list.item(i).text() for i in range(panel.body_list.count())]
    assert any("BODY LUNGS" in t for t in texts) and any("BODY LIVER" in t for t in texts)
    item = next(panel.body_list.item(i) for i in range(panel.body_list.count())
                if panel.body_list.item(i).data(ROLE_REF) == ("kb", 131))
    item.setCheckState(Qt.CheckState.Checked)
    panel.flush_pending()
    assert 131 in se.active_bodies(w.input.doc, geo) and w.source_box_covers(LUNGS + [131])


@pytest.mark.gui
def test_a_box_over_the_analytic_bound_needs_no_rays(qtbot, adult_window, monkeypatch):
    """Una caja holgada (la de todo el tórax) cubre los cuerpos sin calcular su caja exacta con rayos: el panel no
    tarda nada en avisar (o no) en un maniquí de 169 cuerpos."""
    import argus.ui.main_window as mw

    w = adult_window
    geo = w.session.geo
    monkeypatch.setattr(mw, "source_extent", lambda *a, **k: pytest.fail("LOCATE sin necesidad"))

    def chest(d):
        se.set_position(d, [0, 0, 45])
        se.set_box(d, [60, 40, 60])
        se.set_active_bodies(d, geo, LUNGS)

    w.input.edit("tórax", chest)
    assert w.source_box_covers(LUNGS) and not w.source_panel.bodies_hint.text().startswith("⚠")
    assert "Se generan partículas solo" in w.source_panel.bodies_hint.text()


@pytest.mark.gui
def test_the_enclosure_does_not_count_for_the_box(qtbot, window, tmp_path):  # noqa: F811
    """`SBODY 0` nombra la envolvente que añade PENGEOM (todo lo de alrededor): ninguna caja la cubre, así que ni se
    avisa de ella ni cuenta al ajustar la caja."""
    shutil.copy2(CORPUS / "resultados" / "cilindro" / "cilindro.geo", tmp_path / "cilindro.geo")
    w = window
    w.open_file(tmp_path / "cilindro.geo", with_input=False, ask=False)
    w.new_input()
    geo = w.session.geo
    enc = geo.nbodys
    assert geo.body(enc).origin == "enclosure" and se.body_group(geo, 1) == [1]
    w.extended_source_in([1])
    before = w.input.doc.model.source.box
    w.input.edit("con la envolvente", lambda d: se.set_active_bodies(d, geo, [1, enc]))
    assert se.active_bodies(w.input.doc, geo) == {1, enc}
    assert w.uncovered_bodies([1, enc]) == [] and w.source_box_covers([1, enc])
    assert not w.source_panel.bodies_hint.text().startswith("⚠")
    w.fit_source_box()
    assert w.input.doc.model.source.box == before


@pytest.mark.gui
@pytest.mark.skipif(os.name == "nt", reason="(igual en Windows; aquí basta una vez)")
def test_the_3d_menu_offers_the_extended_source_too(qtbot, adult_window, monkeypatch):
    w = adult_window
    v3 = w.view3d
    shown = {}
    monkeypatch.setattr(w, "_exec_menu", lambda menu, *_a: shown.update({a.text(): a for a in menu.actions()}))
    origin = np.array([-8.5, -60.0, 55.0])
    direction = np.array([0.0, 1.0, 0.0])
    seg = v3.tracer.segment_through(origin, direction, 126, 50.0) if v3.tracer is not None else None
    t = float(seg[0]) if seg is not None else 52.0
    info = {"body": 126, "point": origin + t * direction, "origin": origin, "direction": direction, "t": t}
    w._on_context_menu_3d(None, info)
    _sub(shown, "Fuente extensa en")["Los 2 «BODY LUNGS» (7 cuerpos)"].trigger()
    assert se.active_bodies(w.input.doc, w.session.geo) == set(LUNGS)


# -- revisión de 0.16 (0.16.2): la caja con LOCATE, cuerpos vacíos, MKBS, referencias, radiactivas -------------------

def _locate_cover(geo, kbs, c, s, around, n=1_000_000, seed=3):
    """Puntos que LOCATE sitúa en ``kbs`` en una caja ``around`` y cuántos quedan fuera de la caja (c, s)."""
    from argus.pengeom.extent import locator_for

    pts = np.random.default_rng(seed).uniform(around[0], around[1], (n, 3))
    mine = pts[np.isin(locator_for(geo).locate(pts)[0], kbs)]
    return len(mine), int(np.count_nonzero(np.any(np.abs(mine - c) > 0.5 * s, axis=1)))


@pytest.mark.parametrize("name, kb", [("10y.geo", 70), ("newborn.geo", 75), ("newborn.geo", 77), ("5y.geo", 88)])
def test_the_source_box_is_what_locate_puts_in_the_body(name, kb):
    """Revisión de 0.16: la caja se calculaba con rayos (la lógica de STEP) y penhmain sortea con LOCATE. En los
    maniquíes infantiles, donde los cuerpos se solapan, se quedaba corta (el 5,7 % del bazo del de 10 años, el 4,7 %
    de la vesícula del recién nacido) o salía de kilómetros (cuerpos pequeños con una cota analítica enorme)."""
    from argus.pengeom.extent import source_extent
    from argus.session import open_geometry

    s = open_geometry(CORPUS / "ornl" / name)
    lo, hi, state = source_extent(s.geo, kb, s.bounds)
    assert state == "ok" and np.max(hi - lo) < 10.0
    c, side = (np.asarray(v) for v in se.fitted_box(lo, hi))
    span = hi - lo
    found, out = _locate_cover(s.geo, [kb], c, side, (lo - span - 0.5, hi + span + 0.5))
    assert found > 500 and out == 0


def test_empty_bodies_have_no_box():
    """El tiroides del maniquí de 1 año: dos de sus cinco trozos están vacíos (un elipsoide que acaba por debajo del
    plano que los limita). Antes daban la caja de toda la escena: 29 × 24 × 80 cm para un tiroides de 2 cm."""
    from argus.pengeom.extent import source_extent
    from argus.session import open_geometry

    s = open_geometry(CORPUS / "ornl" / "1y.geo")
    res = {kb: source_extent(s.geo, kb, s.bounds) for kb in range(1, 6)}
    assert [res[kb][2] for kb in range(1, 6)] == ["vacío", "vacío", "ok", "ok", "ok"]
    lo = np.min([res[kb][0] for kb in (3, 4, 5)], axis=0)
    hi = np.max([res[kb][1] for kb in (3, 4, 5)], axis=0)
    assert np.max(hi - lo) < 3.0


def test_a_plane_is_unbounded_and_says_so():
    from argus.pengeom.extent import source_extent
    from argus.session import open_geometry

    s = open_geometry(CORPUS / "pengeomjar" / "plane")
    assert source_extent(s.geo, 1, s.bounds)[2] == "sin cota"


def test_radioactive_sources_warn_from_the_100th_attempt():
    """Revisión de 0.16: penhmain avisa «the sampling of initial positions may be very inefficient» desde el intento
    200 de cada partícula (SOURC0) pero desde el 100 de cada desintegración (SOURCE, con SRNUCL)."""
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import load_geometry
    from argus.penhan.validate import source_box_fraction, validate_case

    geo = compile_geometry(load_geometry(CORPUS / "resultados" / "esfera" / "esfera.geo"))
    text = BOXED.replace("SKPAR  2", "SKPAR  0\nSRNUCL Co-60.nuc").replace("SENERG 1.0E6\n", "")
    text = text.replace("SBOX   10 10 10", "SBOX   22 22 22").replace("NSIMSH 1.0E6", "NSIMSH 1.0E5")
    doc = parse_in_text(text)
    f = source_box_fraction(doc, geo)
    assert 0.045 < f < 0.053  # una esfera de 5 cm en una caja de 22 cm
    warn = [d.message for d in validate_case(doc, geo) if "inefficient" in d.message]
    assert warn and "intento 100 de cada desintegración" in warn[0]
    # La misma caja con electrones: unas 85 líneas, no se avisa.
    doc = parse_in_text(text.replace("SKPAR  0\nSRNUCL Co-60.nuc", "SKPAR  1\nSENERG 1.0E6"))
    assert not [d for d in validate_case(doc, geo) if "inefficient" in d.message]


def _granos(path, rows=5, per_row=41):
    """205 esferas «GRANO» (material 1) en filas, en módulos: un «órgano» de más cuerpos de los que admite penhmain."""
    sep = "0" * 64
    out = [f"{rows * per_row} granos", sep]
    k = 0
    for r in range(rows):
        y = -8.0 + 4.0 * r
        for i in range(per_row):
            k += 1
            out += [f"SURFACE ({k:4d})   esfera", "INDICES=( 1, 1, 1, 0,-1)",
                    "X-SCALE=( 4.000000000000000E-01,   0)", "Y-SCALE=( 4.000000000000000E-01,   0)",
                    "Z-SCALE=( 4.000000000000000E-01,   0)", f"X-SHIFT=({-20.0 + i:+.15E},   0)",
                    f"Y-SHIFT=({y:+.15E},   0)", sep, f"BODY    ({k:4d})   GRANO", "MATERIAL(   1)",
                    f"SURFACE ({k:4d}), SIDE POINTER=(-1)", sep]
        out += [f"SURFACE (FX{r:02d})   x", "INDICES=( 1, 0, 0, 0,-1)", "X-SCALE=( 2.100000000000000E+01,   0)", sep,
                f"SURFACE (FY{r:02d})   y", "INDICES=( 0, 1, 0, 0,-1)", "Y-SCALE=( 1.000000000000000E+00,   0)",
                f"Y-SHIFT=({y:+.15E},   0)", sep,
                f"SURFACE (FZ{r:02d})   z", "INDICES=( 0, 0, 1, 0,-1)", "Z-SCALE=( 1.000000000000000E+00,   0)", sep,
                f"MODULE  (FI{r:02d})   FILA", "MATERIAL(   2)", f"SURFACE (FX{r:02d}), SIDE POINTER=(-1)",
                f"SURFACE (FY{r:02d}), SIDE POINTER=(-1)", f"SURFACE (FZ{r:02d}), SIDE POINTER=(-1)"]
        out += [f"BODY    ({j:4d})" for j in range(k - per_row + 1, k + 1)] + [sep]
    out += ["SURFACE (  CX)   x", "INDICES=( 1, 0, 0, 0,-1)", "X-SCALE=( 2.200000000000000E+01,   0)", sep,
            "SURFACE (  CY)   y", "INDICES=( 0, 1, 0, 0,-1)", "Y-SCALE=( 1.200000000000000E+01,   0)", sep,
            "SURFACE (  CZ)   z", "INDICES=( 0, 0, 1, 0,-1)", "Z-SCALE=( 2.000000000000000E+00,   0)", sep,
            "MODULE  (CAJA)   CAJA", "MATERIAL(   2)", "SURFACE (  CX), SIDE POINTER=(-1)",
            "SURFACE (  CY), SIDE POINTER=(-1)", "SURFACE (  CZ), SIDE POINTER=(-1)"]
    out += [f"MODULE  (FI{r:02d})" for r in range(rows)] + [sep, "END      " + "0" * 55, ""]
    path.write_text("\n".join(out), encoding="latin-1")


@pytest.mark.gui
def test_no_more_than_200_active_bodies(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    """penhmain admite 200 líneas SBODY (MKBS; más es el error 3009): ni el menú ni el panel ponen más."""
    _granos(tmp_path / "granos.geo")
    w = window
    w.open_file(tmp_path / "granos.geo", with_input=False, ask=False)
    w.new_input()
    sub = _sub(_menu(w, monkeypatch, 1, [-20.0, -8.0, 0.0]), "Fuente extensa en")
    whole = sub[list(sub)[-1]]
    assert "205" in whole.text() and not whole.isEnabled()
    w.extended_source_in(range(1, 206))
    assert not w.input.doc.find("SBODY") and "200" in w.statusBar().currentMessage()
    w.extended_source_in(range(1, 201))  # 200 sí
    assert len(w.input.doc.find("SBODY")) == 200 and w.input.doc.model.ok
    w.set_source_bodies(range(1, 206))
    assert len(w.input.doc.find("SBODY")) == 200


@pytest.mark.gui
def test_an_organ_with_empty_pieces(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    shutil.copy2(CORPUS / "ornl" / "1y.geo", tmp_path / "1y.geo")
    w = window
    w.open_file(tmp_path / "1y.geo", with_input=False, ask=False)
    w.new_input()
    w.extended_source_in([1, 2, 3, 4, 5])
    src = w.input.doc.model.source
    assert max(src.box) < 3.0 and "vacíos" in w.statusBar().currentMessage()
    assert w.organ_parts([1, 2, 3, 4, 5]) == [[3, 4, 5]] or all(k not in (1, 2) for p in w.organ_parts([1, 2, 3, 4, 5])
                                                                 for k in p)
    # Solo los vacíos: no se cambia la fuente (y se dice por qué).
    texts = []
    monkeypatch.setattr("argus.ui.main_window.QMessageBox.information", lambda *a: texts.append(a[2]))
    before = w.input.doc.text()
    w.extended_source_in([1, 2])
    assert w.input.doc.text() == before and texts and "vacíos" in texts[0]


@pytest.mark.gui
def test_the_menu_keeps_the_references_it_does_not_touch(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    """Revisión de 0.16: «Cuerpo activo de la fuente: …» del botón derecho borraba las líneas SBODY que no llevan a
    ningún cuerpo de la geometría (una etiqueta de otra versión); el panel sí las conserva."""
    shutil.copy2(CORPUS / "resultados" / "cilindro" / "cilindro.geo", tmp_path / "cilindro.geo")
    (tmp_path / "caso.in").write_text(BOXED.replace("SBODY  1\n", "SBODY  1\nSBODY  'ZZZZ'  [Active body]\n")
                                      .replace("GEOMFN a.geo", "GEOMFN cilindro.geo"), encoding="latin-1")
    w = window
    w.open_file(tmp_path / "caso.in", ask=False)
    actions = _menu(w, monkeypatch, 1, [0.0, 0.0, 1.0])
    key = next(k for k in actions if k.startswith("Cuerpo activo de la fuente"))
    actions[key].trigger()  # quita el cilindro
    refs = [ln.body_ref for ln in w.input.doc.find("SBODY")]
    assert len(refs) == 1 and "ZZZZ" in refs[0]


@pytest.mark.gui
def test_unchecking_a_body_without_geometry(qtbot, window, tmp_path):  # noqa: F811
    """Revisión de 0.16: con un .in cuya geometría no existe, desmarcar una referencia del panel daba un error."""
    from PySide6.QtCore import Qt

    errors = []
    old = sys.excepthook
    sys.excepthook = lambda *a: errors.append(a)
    try:
        (tmp_path / "caso.in").write_text(BOXED.replace("SBODY  1\n", "SBODY  'ABCD'  [Active body]\nSBODY  2\n")
                                          .replace("GEOMFN a.geo", "GEOMFN noexiste.geo"), encoding="latin-1")
        w = window
        w.open_file(tmp_path / "caso.in", ask=False)
        panel = w.source_panel
        panel.body_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        panel.flush_pending()
        qtbot.wait(20)
    finally:
        sys.excepthook = old
    assert not errors and len(w.input.doc.find("SBODY")) == 1


@pytest.mark.gui
def test_menu_texts(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    """Un «&» de la descripción se ve (no es la tecla del menú) y sin dos paréntesis seguidos."""
    w = window
    shutil.copy2(CORPUS / "resultados" / "cilindro" / "cilindro.geo", tmp_path / "c.geo")
    text = (tmp_path / "c.geo").read_text(encoding="latin-1").replace("Cilindro de agua", "Agua & R&D")
    (tmp_path / "c.geo").write_text(text, encoding="latin-1")
    w.open_file(tmp_path / "c.geo", with_input=False, ask=False)
    w.new_input()
    label = w._body_label(1)
    assert "&&" in label and label.count("(") == 1
    monkeypatch.setattr("argus.ui.main_window.body_title", lambda geo, kb: "Cuerpo «AB2» (n.º 2)")
    assert w._body_label(1) == "Cuerpo «AB2» (n.º 2, Agua && R&&D)"


# -- segunda pasada de la revisión de 0.16 ----------------------------------------------------------------------------

@pytest.mark.gui
def test_the_menu_of_many_separate_bodies_does_not_measure_each_one(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    """Segunda pasada: con 205 cuerpos separados de la misma descripción, «Fuente extensa en ▸» buscaba la caja de
    cada uno para saber si estaba vacío (68 s; en 0.16.1, 0,08 s). Un cuerpo solo se mira con unos puntos al azar en
    su cota; la caja de verdad, solo si ninguno cae en él (y las de las partes de más de un cuerpo)."""
    _granos(tmp_path / "granos.geo")
    w = window
    w.open_file(tmp_path / "granos.geo", with_input=False, ask=False)
    w.new_input()
    measured = []
    real = w.source_extent
    monkeypatch.setattr(w, "source_extent", lambda kb: (measured.append(kb), real(kb))[1])
    sub = _sub(_menu(w, monkeypatch, 1, [-20.0, -8.0, 0.0]), "Fuente extensa en")
    assert any("205" in t for t in sub) and measured == []
    from argus.penhan import source_edit as se

    assert len(w.organ_parts(se.body_group(w.session.geo, 1))) == 205


@pytest.mark.gui
def test_adding_to_the_active_bodies_counts_the_references_it_keeps(qtbot, window, tmp_path, monkeypatch):  # noqa: F811
    """Segunda pasada: «Añadir a los cuerpos activos ▸» no contaba las líneas SBODY que no son de la geometría (se
    quedan): ofrecía añadir un órgano y después no lo hacía (pasaba de MKBS)."""
    from argus.penhan import source_edit as se

    _granos(tmp_path / "granos.geo", rows=1, per_row=3)
    (tmp_path / "caso.in").write_text(BOXED.replace("SBODY  1\n", "SBODY  1\nSBODY  'ZZZZ'  [Active body]\n")
                                      .replace("GEOMFN a.geo", "GEOMFN granos.geo"), encoding="latin-1")
    w = window
    w.open_file(tmp_path / "caso.in", ask=False)
    monkeypatch.setattr(se, "MAX_SOURCE_BODIES", 3)
    sub = _sub(_menu(w, monkeypatch, 2, [-19.0, -8.0, 0.0]), "Añadir a los cuerpos activos")
    whole = next(a for t, a in sub.items() if "3 cuerpos" in t)
    assert not whole.isEnabled()  # 3 cuerpos y 'ZZZZ' son 4
    monkeypatch.setattr(se, "MAX_SOURCE_BODIES", 4)
    sub = _sub(_menu(w, monkeypatch, 2, [-19.0, -8.0, 0.0]), "Añadir a los cuerpos activos")
    assert next(a for t, a in sub.items() if "3 cuerpos" in t).isEnabled()
