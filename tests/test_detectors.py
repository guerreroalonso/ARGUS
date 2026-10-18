"""Detectores con formas habituales (prismas regulares: triángulo, cuadrado, pentágono y hexágono, con su altura) y
celdas de detectores (una caja llena de detectores iguales), con sus detectores de penhmain dentro de sus límites.

Las marcadas ``oracle`` comparan con PENGEOM y el PMRDR de penhmain reales (sondas en build/probe).
"""

from __future__ import annotations

import math
import re
import shutil

import numpy as np
import pytest

from argus.pengeom import detcell as dc
from argus.pengeom import edit as ge
from argus.pengeom.locate import Locator
from argus.pengeom.newgeo import new_geometry
from argus.pengeom.parser import parse_geo_text
from argus.pengeom.writer import write_geo_text
from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_text
from conftest import CORPUS
from geo_oracle import assert_pengeom_agrees, random_points, step_disagreements

CASE = CORPUS / "usuario/100Mev15-174"


def _world(size: float = 80.0):
    doc = new_geometry("detectores", ge.Primitive("box", center=(0.0, 0.0, 0.0), sides=(size, size, size)), 1)
    return doc, ge.compile_checked(doc)


def _reread(doc):
    text = write_geo_text(doc)
    doc2 = parse_geo_text(text, "x.geo")
    return text, doc2, ge.compile_checked(doc2)


# ---------------------------------------------------------------------------------------------------------------------
# Prismas regulares
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["prism3", "prism4", "prism5", "prism6"])
@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("angle", [0.0, 17.5])
def test_a_prism_is_exactly_its_shape(kind, axis, angle):
    doc, geo = _world(40.0)
    prim = ge.Primitive(kind, center=(1.5, -2.0, 3.25), radius=4.0, height=6.0, axis=axis, angle=angle)
    lab = ge.add_primitive(doc, geo, prim, 2, container=1)
    _text, _doc2, geo2 = _reread(doc)
    kb = geo2.find_body(lab).index
    pts, dirs = random_points((-8, -9, -6), (11, 7, 12), 3000, seed=len(kind) + int(angle))
    ib, _ = Locator(geo2).locate(pts, dirs)
    assert np.array_equal(ib == kb, prim.contains(pts))
    rng = np.random.default_rng(1)
    s = prim.sample(2000, rng)
    lo, hi = prim.box()
    assert prim.contains(s, 1e-9).all() and np.all(s >= lo - 1e-9) and np.all(s <= hi + 1e-9)
    # La caja es la del polígono: tocan sus caras en los tres ejes.
    for k in range(3):
        assert s[:, k].min() < lo[k] + 0.05 * (hi[k] - lo[k]) and s[:, k].max() > hi[k] - 0.05 * (hi[k] - lo[k])


def test_polygon_measures():
    for n in (3, 4, 5, 6):
        for m in ge.PRISM_MEASURES:
            r = ge.prism_radius(n, m, 2.5)
            assert ge.prism_measure(n, m, r) == pytest.approx(2.5)
    assert ge.prism_radius(4, "side", 1.0) == pytest.approx(math.sqrt(0.5))
    assert ge.prism_radius(6, "flats", 2.0) == pytest.approx(2.0 / math.sqrt(3.0))  # entre caras = 2·apotema
    assert ge.prism_radius(3, "flats", 1.5) == pytest.approx(1.0)  # altura del triángulo = R + r = 1,5 R
    p = ge.Primitive("prism4", radius=ge.prism_radius(4, "side", 2.0), height=1.0)
    assert p.side == pytest.approx(2.0) and p.half_extent(0) == pytest.approx(1.0)  # con giro 0, lados paralelos
    assert "Prisma cuadrado lado=2" in p.describe()


def test_prisms_inside_a_turned_module_and_as_modules():
    doc, geo = _world(60.0)
    mod = ge.add_primitive(doc, geo, ge.Primitive("box", center=(5.0, 0.0, 0.0), sides=(20.0, 20.0, 20.0)), 1,
                           container=1, module=True)
    _t, doc, geo = _reread(doc)
    el = ge.find_element(doc, "body", mod)
    ge.set_module_transform(el, {"OMEGA": 30.0, "THETA": 20.0, "PHI": 10.0})
    _t, doc, geo = _reread(doc)
    kb_mod = geo.find_body(mod).index
    prim = ge.Primitive("prism6", center=(5.0, 1.0, -1.0), radius=3.0, height=4.0, axis="y", angle=10.0)
    hexa = ge.add_primitive(doc, geo, prim, 3, container=kb_mod, parinp=[], module=True)
    _t, doc, geo = _reread(doc)
    inner = ge.Primitive("prism3", center=(5.0, 1.0, -1.0), radius=1.0, height=2.0, axis="x", angle=45.0)
    tri = ge.add_primitive(doc, geo, inner, 4, container=geo.find_body(hexa).index)
    text, _doc, geo = _reread(doc)
    pts, dirs = random_points((-6, -6, -6), (16, 8, 6), 4000, seed=3)
    ib, _ = Locator(geo).locate(pts, dirs)
    k_hex, k_tri = geo.find_body(hexa).index, geo.find_body(tri).index
    assert np.array_equal(ib == k_tri, inner.contains(pts))
    assert np.array_equal((ib == k_hex) | (ib == k_tri), prim.contains(pts))


@pytest.mark.oracle
def test_prisms_agree_with_pengeom(tmp_path, need_probe):
    doc, geo = _world(40.0)
    for n, (kind, axis, angle) in enumerate([("prism3", "z", 0.0), ("prism4", "x", 30.0), ("prism5", "y", 12.0),
                                             ("prism6", "z", 90.0)]):
        c = (-12.0 + 8.0 * n, 3.0, -4.0)
        ge.add_primitive(doc, geo, ge.Primitive(kind, center=c, radius=3.0, height=5.0, axis=axis, angle=angle),
                         2 + n, container=geo.find_body("1").index)
        _t, doc, geo = _reread(doc)
    text = write_geo_text(doc)
    pts, dirs = random_points((-18, -5, -10), (20, 10, 6), 5000, seed=9)
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


# ---------------------------------------------------------------------------------------------------------------------
# Celdas
# ---------------------------------------------------------------------------------------------------------------------

CELLS = [
    dict(shape="prism6", size=1.0, rows=8, cols=8, layout="offset"),
    dict(shape="prism6", size=1.0, rows=5, cols=6, layout="offset", gap=0.1, margin=0.3),
    dict(shape="prism6", size=1.0, rows=4, cols=4, layout="grid", gap=0.2),
    dict(shape="prism4", size=math.sqrt(0.5), rows=16, cols=16, layout="grid"),
    dict(shape="prism4", size=math.sqrt(0.5), rows=6, cols=7, layout="offset", gap=0.05, layers=3, layer_gap=0.2),
    dict(shape="cylinder", size=0.5, rows=10, cols=10, layout="offset", gap=0.02),
    dict(shape="cylinder", size=0.5, rows=6, cols=6, layout="grid", gap=0.1, axis="x"),
    dict(shape="prism3", size=1.0, rows=6, cols=10, layout="offset"),
    dict(shape="prism3", size=1.0, rows=4, cols=8, layout="offset", gap=0.15, axis="y"),
    dict(shape="prism5", size=0.8, rows=5, cols=5, layout="grid", gap=0.1, layers=3),
    dict(shape="box", sides=(1.0, 2.0), rows=3, cols=4, layout="offset", gap=0.1, margin=0.2),
    dict(shape="prism4", size=0.3, rows=20, cols=20, layers=2),
]


def _cell(kw, center=(1.0, -2.0, 3.0), world=80.0):
    kw = dict(kw)
    spec = dc.CellSpec(height=kw.pop("height", 2.0), center=center, **kw)
    plan = dc.plan_cell(spec)
    assert plan.ok, plan.problems
    doc, geo = _world(world)
    res = dc.add_cell(doc, geo, plan, 2, 3, container=1, name="C")
    text, doc2, geo2 = _reread(doc)
    return plan, res, text, geo2


@pytest.mark.parametrize("kw", CELLS, ids=[f"{c['shape']}-{c.get('layout', 'grid')}-{i}" for i, c in enumerate(CELLS)])
def test_every_detector_of_a_cell_is_exactly_its_shape(kw):
    plan, res, _text, geo = _cell(kw)
    loc = Locator(geo)
    rng = np.random.default_rng(len(plan.elements))
    assert len(res.elements) == len(plan.elements) == plan.spec.count
    for e, lab in list(zip(plan.elements, res.elements))[:: max(1, len(plan.elements) // 20)]:
        kb = geo.find_body(lab).index
        assert f"fila {e.row}, columna {e.col}" in geo.body(kb).element.description
        lo, hi = e.prim.box()
        pts = lo + (rng.random((500, 3)) * 1.2 - 0.1) * (hi - lo)
        ib, _ = loc.locate(pts)
        edge = e.prim.contains(pts, 1e-9) & ~e.prim.contains(pts, -1e-9)  # en una cara compartida vale cualquiera
        assert np.array_equal((ib == kb)[~edge], e.prim.contains(pts)[~edge])
    lo, hi = plan.housing.box()
    pts = lo + rng.random((4000, 3)) * (hi - lo)
    _ib, mat = loc.locate(pts)
    in_det = np.zeros(len(pts), bool)
    for e in plan.elements:
        in_det |= e.prim.contains(pts, 1e-9)
    assert set(np.unique(mat[~in_det]).tolist()) <= {3}  # la caja (y sus módulos intermedios)


@pytest.mark.oracle
@pytest.mark.parametrize("n", [0, 3, 5, 7, 9, 11])
def test_cells_agree_with_pengeom(n, tmp_path, need_probe):
    plan, _res, text, _geo = _cell(CELLS[n])
    lo, hi = plan.housing.box()
    rng = np.random.default_rng(n)
    pts = lo + rng.random((3000, 3)) * (hi - lo)
    dirs = rng.normal(size=pts.shape)
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    assert_pengeom_agrees(text, tmp_path, pts, dirs)


def _rays(plan, n: int, seed: int):
    """Rayos que cruzan la caja de la celda: a lo largo de u, de v y del eje, y en direcciones al azar."""
    a, u, v = plan.spec.axes()
    lo, hi = plan.housing.box()
    c, size = 0.5 * (lo + hi), hi - lo
    rng = np.random.default_rng(seed)
    starts, dirs = [], []
    for d, others in ((u, (v, a)), (v, (a, u)), (a, (u, v))):
        for _ in range(n // 4):
            off = sum((rng.random() - 0.5) * 0.98 * float(size @ o) * o for o in others)
            starts.append(c + off - (0.5 * float(size @ d) + 1.0) * d)
            dirs.append(d)
    reach = float(np.linalg.norm(size))
    for _ in range(n - len(starts)):
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        starts.append(c - (0.5 * reach + 1.0) * d + rng.normal(scale=0.2 * reach, size=3))
        dirs.append(d)
    return np.array(starts), np.array(dirs), reach + 2.0 + 0.6 * reach


STEP_CELLS = [
    dict(shape="prism6", size=0.5, rows=4, cols=4, layout="grid"),  # sin pared ni huecos (lo de siempre)
    dict(shape="prism6", size=0.5, rows=6, cols=6, layout="offset"),
    dict(shape="prism3", size=0.8, rows=5, cols=8, layout="offset"),
    dict(shape="prism5", size=0.5, rows=4, cols=5, layout="grid", layers=3),  # capas que se tocan
    dict(shape="prism4", size=0.3, rows=16, cols=16, layers=2),  # capas y filas
    dict(shape="box", sides=(0.6, 0.3), rows=16, cols=16, layout="offset", gap=0.02),  # filas de ladrillos
    dict(shape="cylinder", size=0.4, rows=5, cols=5, layout="offset", layers=2, margin=0.1),
]


@pytest.mark.oracle
@pytest.mark.parametrize("kw", STEP_CELLS, ids=[f"{c['shape']}-{c.get('layout', 'grid')}-{i}"
                                                for i, c in enumerate(STEP_CELLS)])
def test_pengeom_transport_never_misses_a_wall_of_the_cell(kw, tmp_path, need_probe):
    """STEP (lo que usa penhmain para transportar) cruza cada pared: las caras que coinciden (detectores que se
    tocan, la pared de la caja sin grosor, capas sin hueco, los módulos de capas y filas) son la misma superficie."""
    plan, _res, text, _geo = _cell(kw)
    starts, dirs, smax = _rays(plan, 160, len(plan.elements))
    bad, worst = step_disagreements(text, tmp_path, starts, dirs, smax)
    assert bad == 0, f"{bad} rayos con otro material en STEP (hasta {worst:.3f} cm)"


@pytest.mark.oracle
def test_pengeom_transport_through_a_cell_in_a_turned_module(tmp_path, need_probe):
    doc, geo = _world(60.0)
    mod = ge.add_primitive(doc, geo, ge.Primitive("box", center=(2.0, 1.0, -1.0), sides=(30.0, 30.0, 30.0)), 1,
                           container=1, module=True)
    _t, doc, geo = _reread(doc)
    ge.set_module_transform(ge.find_element(doc, "body", mod), {"OMEGA": 25.0, "THETA": 40.0, "PHI": -15.0})
    _t, doc, geo = _reread(doc)
    plan = dc.plan_cell(dc.CellSpec(shape="prism6", size=0.6, height=0.8, rows=5, cols=5, layers=2, layout="offset",
                                    center=(2.0, 1.0, -1.0)))
    dc.add_cell(doc, geo, plan, 2, 3, container=geo.find_body(mod).index, parinp=[])
    text, _doc, _geo = _reread(doc)
    starts, dirs, smax = _rays(plan, 160, 5)
    bad, worst = step_disagreements(text, tmp_path, starts, dirs, smax)
    assert bad == 0, f"{bad} rayos con otro material en STEP (hasta {worst:.3f} cm)"


def _seg_dist(x, a, b) -> float:
    ab = b - a
    t = float(np.clip(np.dot(x - a, ab) / np.dot(ab, ab), 0.0, 1.0))
    return float(np.linalg.norm(x - (a + t * ab)))


def _distance(p, q) -> float:
    """Distancia en el plano entre dos detectores de una capa con eje z (círculos, o polígonos que no se cortan)."""
    if p.kind == "cylinder":
        return float(np.hypot(p.center[0] - q.center[0], p.center[1] - q.center[1])) - p.radius - q.radius

    def outline(e):
        if e.kind == "box":
            hu, hv = 0.5 * e.sides[0], 0.5 * e.sides[1]
            return np.array(e.center[:2]) + np.array([[-hu, -hv], [hu, -hv], [hu, hv], [-hu, hv]])
        return np.array(e.center[:2]) + e.vertices_2d()

    pp, qq = outline(p), outline(q)
    return min(_seg_dist(x, b[k], b[(k + 1) % len(b)]) for a, b in ((pp, qq), (qq, pp)) for x in a
               for k in range(len(b)))


@pytest.mark.parametrize("kw,gap", [
    (dict(shape="prism6", size=1.0, layout="offset"), 0.1),
    (dict(shape="prism6", size=1.0, layout="grid"), 0.1),
    (dict(shape="cylinder", size=0.5, layout="offset"), 0.05),
    (dict(shape="cylinder", size=0.5, layout="grid"), 0.05),
    (dict(shape="prism3", size=1.0, layout="offset"), 0.2),
    (dict(shape="prism3", size=1.0, layout="grid"), 0.2),
    (dict(shape="prism5", size=0.8, layout="grid"), 0.1),
    (dict(shape="prism5", size=0.8, layout="offset"), 0.1),
    (dict(shape="prism4", size=0.7, layout="offset"), 0.15),
    (dict(shape="box", sides=(1.0, 0.4), layout="grid"), 0.03),
], ids=lambda v: v["shape"] + "-" + v["layout"] if isinstance(v, dict) else str(v))
def test_the_cell_layouts_have_the_gap_asked_for(kw, gap):
    """Ningún detector se mete en otro y cada uno tiene algún vecino justo al hueco pedido (entre caras, vértices o
    círculos): no se deja más hueco del que se pide (un triángulo o un pentágono no llega lo mismo hacia su
    vértice que hacia la cara de enfrente)."""
    plan = dc.plan_cell(dc.CellSpec(rows=4, cols=5, gap=gap, **kw))
    els = [e.prim for e in plan.elements]
    rng = np.random.default_rng(0)
    for i, p in enumerate(els):
        pts = p.sample(400, rng)
        near = []
        for j, q in enumerate(els):
            if i != j:
                assert not q.contains(pts, -1e-9).any(), (i, j)
                near.append(_distance(p, q))
        assert min(near) == pytest.approx(gap, abs=1e-9), (i, min(near))
    # La caja, justa alrededor de los detectores (con la pared pedida, 0).
    lo, hi = plan.housing.box()
    boxes = np.array([np.r_[e.box()] for e in els])
    assert np.allclose(boxes[:, :3].min(axis=0), lo) and np.allclose(boxes[:, 3:].max(axis=0), hi)


def test_the_structure_respects_the_limits_of_pengeom():
    # 256 cuadrados en cuadrícula: más de 248 hijas en un módulo; se agrupan por filas.
    plan = dc.plan_cell(dc.CellSpec(shape="prism4", size=0.5, rows=16, cols=16))
    assert plan.ok and plan.structure == "filas" and plan.modules == 17
    # 256 hexágonos en panal: las filas encajan unas en otras y no se pueden separar en módulos.
    plan = dc.plan_cell(dc.CellSpec(shape="prism6", size=1.0, rows=16, cols=16, layout="offset"))
    assert not plan.ok and "248 hijas" in plan.problems[0] and "encajan" in plan.problems[0]
    # 240 cilindros al tresbolillo: 240 superficies propias; con la caja y las bases, 244 (caben).
    plan = dc.plan_cell(dc.CellSpec(shape="cylinder", size=0.5, rows=15, cols=16, layout="offset"))
    assert plan.ok and plan.structure == "plano"
    _p, _r, _t, geo = _cell(dict(shape="cylinder", size=0.5, rows=15, cols=16, layout="offset"))
    assert geo.ok
    # 248 cilindros: 253 superficies en el módulo (más de 249).
    plan = dc.plan_cell(dc.CellSpec(shape="cylinder", size=0.5, rows=8, cols=31, layout="offset"))
    assert not plan.ok and "superficies" in plan.problems[0]


def test_bad_specs_are_refused():
    for kw in (dict(size=0.0), dict(height=-1.0), dict(rows=0), dict(gap=-0.1), dict(shape="estrella"),
               dict(layout="espiral"), dict(size=float("nan"))):
        assert not dc.plan_cell(dc.CellSpec(**kw)).ok, kw
    plan = dc.plan_cell(dc.CellSpec())
    doc, geo = _world()
    with pytest.raises(ge.EditError):
        dc.add_cell(doc, geo, plan, 0, 1, container=1)  # detectores de vacío


# ---------------------------------------------------------------------------------------------------------------------
# Detectores de penhmain (5 de cada tipo; 100 cuerpos los de energía, 50 los de impacto)
# ---------------------------------------------------------------------------------------------------------------------

def test_detector_groups_stay_within_the_limits_of_penhmain():
    plan = dc.plan_cell(dc.CellSpec(shape="prism4", size=0.5, rows=12, cols=10, layers=2))  # 240 detectores
    assert dc.detector_problem(plan, "cell", "energy", 0) is not None  # 240 > 100 cuerpos
    assert dc.detector_problem(plan, "layers", "energy", 0) is not None  # 120 > 100
    rows_problem = dc.detector_problem(plan, "rows", "energy", 0)  # 24 detectores > 5
    assert rows_problem is not None and "24" in rows_problem
    assert dc.suggest_grouping(plan, "energy", 0) == "none"
    plan = dc.plan_cell(dc.CellSpec(shape="prism4", size=0.5, rows=8, cols=10, layers=3))  # 240, capas de 80
    assert dc.detector_problem(plan, "layers", "energy", 0) is None
    assert "ya tiene 3" in dc.detector_problem(plan, "layers", "energy", 3)
    assert dc.detector_problem(plan, "layers", "impact", 0) is not None  # 80 > 50 cuerpos
    assert dc.suggest_grouping(plan, "energy", 0) == "layers"
    plan = dc.plan_cell(dc.CellSpec(shape="prism6", rows=2, cols=2))
    assert dc.suggest_grouping(plan, "impact", 0) == "cell"
    assert dc.detector_problem(plan, "each", "impact", 1) is None and dc.detector_problem(plan, "each", "impact", 2)


def test_apply_choice_adds_the_bodies_by_label_or_refuses_without_changes():
    from argus.ui.detector_dialogs import DetectorChoice, apply_choice

    text = (CASE / "maniqui.in").read_text(encoding="latin-1")
    doc = parse_in_text(text)
    before = doc.text()
    n = apply_choice(doc, DetectorChoice("energy", 0, "rows", 1e5, 2e8, -50), [["   5", "   6"], ["   7"]])
    dets = sc.energy_detectors(doc)
    assert n == 2 and [[b.label for b in d.bodies] for d in dets] == [["   5", "   6"], ["   7"]]
    assert dets[0].nbe == -50 and "EDBODY '   5'" in doc.text()
    assert apply_choice(doc, DetectorChoice("energy", 2), [["   8"]]) == 0  # al detector 2
    assert [b.label for b in sc.energy_detectors(doc)[1].bodies] == ["   7", "   8"]
    many = [[f"{i:4d}"] for i in range(10, 14)]
    snap = doc.text()
    with pytest.raises(ValueError):
        apply_choice(doc, DetectorChoice("energy"), many)  # 2 + 4 > 5
    assert doc.text() == snap
    with pytest.raises(ValueError):
        apply_choice(doc, DetectorChoice("impact"), [[f"{i:4d}" for i in range(51)]])  # 51 > 50
    assert doc.text() == snap and before != snap


# ---------------------------------------------------------------------------------------------------------------------
# Ventana
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def case_dir(tmp_path):
    for name in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def window(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow
    from argus.ui.settings import app_settings

    msgs = []
    for kind in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2]))
                            or QMessageBox.StandardButton.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._msgs = msgs
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def _drive(monkeypatch, fill):
    """El diálogo se rellena como lo haría el usuario (sin mostrarlo de forma modal) y se acepta si puede."""
    from argus.ui.main_window import MainWindow

    seen = {}

    def fake(self, dlg):
        seen["dlg"] = dlg
        fill(dlg)
        dlg._timer.stop()
        (dlg._update if hasattr(dlg, "_update") else dlg._check)()
        seen["status"] = dlg.status.text()
        if dlg._ok:
            dlg.accept()
        return dlg._ok

    monkeypatch.setattr(MainWindow, "_exec_dialog", fake)
    return seen


def test_add_a_hexagonal_detector_with_its_energy_detector(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index
    before_in, before_geo = w.input.doc.text(), w.geo_ctrl.text

    def fill(dlg):
        assert dlg.windowTitle() == "Añadir un detector" and [dlg.kind.itemText(i) for i in range(dlg.kind.count())] \
            == list(dc.CELL_SHAPES.values())
        dlg.kind.setCurrentIndex(dlg.kind.findData("prism6"))
        dlg.pmeasure.setCurrentIndex(dlg.pmeasure.findData("flats"))
        dlg.psize.setText("10")  # 10 cm entre caras
        dlg.height.setText("2")
        for ed, v in zip((dlg.cx, dlg.cy, dlg.cz), ("0", "0", "50")):
            ed.setText(v)
        dlg.material.setCurrentIndex(dlg.material.findData(1))
        dlg.options.bins.set_value(-200)

    seen = _drive(monkeypatch, fill)
    w.add_detector_dialog(cube, None)
    assert "✔" in seen["status"]
    geo = w.session.geo
    hexa = [kb for kb in range(1, geo.nbodys + 1) if "Prisma hexagonal" in (geo.body(kb).element.description
                                                                             if geo.body(kb).element else "")]
    assert len(hexa) == 1
    lab = geo.balias(hexa[0])
    dets = sc.energy_detectors(w.input.doc)
    assert len(dets) == 1 and [b.label for b in dets[0].bodies] == [lab] and dets[0].nbe == -200
    # Con giro 0, una cara abajo: 10 cm entre caras en y (v) y de vértice a vértice (11,55 cm) en x (u).
    prim = ge.Primitive("prism6", center=(0.0, 0.0, 50.0), radius=10.0 / math.sqrt(3.0), height=2.0)
    pts = np.array([[0.0, 4.99, 50.0], [5.7, 0.0, 50.0], [0.0, -5.01, 50.0], [5.8, 0.0, 50.0], [0.0, 0.0, 51.1]])
    ib, _ = w.session.locator.locate(pts)
    assert list(ib == hexa[0]) == list(prim.contains(pts)) == [True, True, False, False, False]
    w.undo()  # un solo paso: el cuerpo y el detector
    assert w.input.doc.text() == before_in and w.geo_ctrl.text == before_geo


def test_add_a_cell_of_detectors_grouped_by_rows(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index
    before_in, before_geo = w.input.doc.text(), w.geo_ctrl.text

    def fill(dlg):
        dlg.shape.setCurrentIndex(dlg.shape.findData("prism4"))
        dlg.size.setText("2")
        dlg.height.setText("1")
        dlg.rows.setValue(4)
        dlg.cols.setValue(6)
        dlg.gap.setText("0.2")
        dlg.margin.setText("0.5")
        for ed, v in zip((dlg.cx, dlg.cy, dlg.cz), ("0", "0", "100")):
            ed.setText(v)
        dlg.mat_det.setCurrentIndex(dlg.mat_det.findData(1))
        dlg._update()
        assert dlg.options.target.currentData() == "cell"  # 24 cuerpos: caben en un detector de energía
        dlg.options.target.setCurrentIndex(dlg.options.target.findData("rows"))

    seen = _drive(monkeypatch, fill)
    w.add_cell_dialog(cube, None)
    assert "✔" in seen["status"], seen["status"]
    geo = w.session.geo
    dets = sc.energy_detectors(w.input.doc)
    assert len(dets) == 4 and all(len(d.bodies) == 6 for d in dets)
    for i, d in enumerate(dets, start=1):
        for b in d.bodies:
            assert f"fila {i}," in geo.body(geo.find_body(b.label).index).element.description
    w.undo()
    assert w.input.doc.text() == before_in and w.geo_ctrl.text == before_geo


def test_the_cell_dialog_refuses_what_penhmain_would_not_accept(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index

    def fill(dlg):
        dlg.shape.setCurrentIndex(dlg.shape.findData("cylinder"))
        dlg.radius.setText("1")
        dlg.rows.setValue(3)
        dlg.cols.setValue(4)
        for ed, v in zip((dlg.cx, dlg.cy, dlg.cz), ("0", "0", "100")):
            ed.setText(v)
        dlg.mat_det.setCurrentIndex(dlg.mat_det.findData(1))
        dlg.options.target.setCurrentIndex(dlg.options.target.findData("each"))  # 12 detectores > 5

    seen = _drive(monkeypatch, fill)
    before = w.geo_ctrl.text
    w.add_cell_dialog(cube, None)
    assert "✖" in seen["status"] and "12 detectores" in seen["status"] and w.geo_ctrl.text == before


def test_without_an_in_the_detector_is_only_the_body(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "cubo-esfera.geo", with_input=False)
    cube = w.session.geo.find_body("1").index

    def fill(dlg):
        assert not dlg.options.kind.isEnabled() and dlg.options.kind_key() == "none"
        dlg.kind.setCurrentIndex(dlg.kind.findData("prism3"))
        dlg.psize.setText("5")
        dlg.height.setText("1")
        for ed, v in zip((dlg.cx, dlg.cy, dlg.cz), ("0", "0", "50")):
            ed.setText(v)

    seen = _drive(monkeypatch, fill)
    w.add_detector_dialog(cube, None)
    assert "✔" in seen["status"] and w.geo_ctrl.dirty and w.input is None


# ---------------------------------------------------------------------------------------------------------------------
# penhmain real: los detectores de la celda
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.oracle
def test_penhmain_reads_the_detectors_of_a_cell(tmp_path, qtbot, window, case_dir, monkeypatch):
    from argus.oracle.probe import find_sim_probe, run_sim_probe

    if find_sim_probe() is None:
        pytest.skip("Sonda de las comprobaciones de penhmain no compilada (python tools/build_probe.py)")
    w = window
    w.open_file(case_dir / "maniqui.in")
    cube = w.session.geo.find_body("1").index

    def fill(dlg):
        dlg.shape.setCurrentIndex(dlg.shape.findData("prism6"))
        dlg.size.setText("2")
        dlg.height.setText("1")
        dlg.rows.setValue(3)
        dlg.cols.setValue(4)
        dlg.layout_box.setCurrentIndex(1)
        for ed, v in zip((dlg.cx, dlg.cy, dlg.cz), ("0", "0", "100")):
            ed.setText(v)
        dlg.mat_det.setCurrentIndex(dlg.mat_det.findData(1))
        dlg._update()
        dlg.options.target.setCurrentIndex(dlg.options.target.findData("rows"))

    _drive(monkeypatch, fill)
    w.add_cell_dialog(cube, None)
    geo = w.session.geo
    res = run_sim_probe(w.input.doc.text(), {"cubo-esfera.geo": w.geo_ctrl.text})
    assert res.iretrn == 0, res.reason
    found: dict[int, list[str]] = {}
    det = None
    for ln in res.output.splitlines():
        m = re.search(r"Energy-deposition detector #\s*(\d+)", ln)
        if m:
            det = int(m.group(1))
        m = re.search(r"Active body =\s+(\d+)", ln)
        if m and det is not None:
            found.setdefault(det, []).append(geo.balias(int(m.group(1))))
    want = {i: [b.label for b in d.bodies] for i, d in enumerate(sc.energy_detectors(w.input.doc), start=1)}
    assert found == want and len(want) == 3


# ---------------------------------------------------------------------------------------------------------------------
# Revisión: límites, materiales, etiquetas y casos extremos
# ---------------------------------------------------------------------------------------------------------------------

def _planes(geo):
    """(n, d) de cada superficie plana compilada (A·x + B·y + C·z + D = 0, n unitario con su sentido fijo)."""
    out = []
    for s in geo.surfaces:
        q = np.asarray(s.q, dtype=float)  # AXX, AXY, AXZ, AYY, AYZ, AZZ, AX, AY, AZ, A0
        if np.any(np.abs(q[:6]) > 0):
            continue
        n = q[6:9]
        norm = float(np.linalg.norm(n))
        n, d = n / norm, -q[9] / norm
        k = int(np.argmax(np.abs(n) > 1e-6))
        if n[k] < 0:
            n, d = -n, -d
        out.append((n, d))
    return out


@pytest.mark.parametrize("kw", CELLS[:8], ids=[f"{c['shape']}-{i}" for i, c in enumerate(CELLS[:8])])
def test_no_two_surfaces_of_a_cell_are_the_same_plane(kw):
    """Cada plano del espacio se escribe una vez (también con la normal al revés): dos superficies distintas en el
    mismo sitio confunden a STEP."""
    _plan, _res, _text, geo = _cell(kw)
    from argus.pengeom.validate import coincident_walls

    assert not coincident_walls(geo)
    planes = _planes(geo)
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            (n1, d1), (n2, d2) = planes[i], planes[j]
            assert not (np.allclose(n1, n2, atol=1e-9) and abs(d1 - d2) < 1e-7), (i, j, n1, d1)


def test_the_cell_module_leaves_room_for_the_sphere_of_the_enclosure(tmp_path):
    """GEOMIN añade la esfera de la envolvente a cada elemento sin madre sin comprobar NXG: la caja de la celda,
    como mucho 248 superficies (con 249, PENGEOM construye mal sus tablas sin dar error)."""
    kw = dict(shape="cylinder", size=0.5, height=1.0, layout="grid")
    assert dc.plan_cell(dc.CellSpec(rows=2, cols=121, **kw)).structure == "plano"  # 242 + 6 = 248
    plan = dc.plan_cell(dc.CellSpec(rows=3, cols=81, **kw))  # 243 + 6 = 249
    assert plan.ok and plan.structure == "filas"  # en módulos de fila, que sí tienen madre
    from argus.oracle.probe import find_probe

    if find_probe() is not None:
        plan, _res, text, _geo = _cell(dict(rows=3, cols=81, **kw), world=400.0)
        lo, hi = plan.housing.box()
        pts, dirs = random_points(lo - 0.5, hi + 0.5, 3000, seed=2)
        assert_pengeom_agrees(text, tmp_path, pts, dirs)


def test_tiny_sizes_and_gaps_are_refused():
    for kw in (dict(size=5e-9), dict(height=1e-8), dict(gap=1e-9), dict(margin=3e-7), dict(layer_gap=1e-7, layers=2),
               dict(shape="box", sides=(1.0, 1e-8))):
        plan = dc.plan_cell(dc.CellSpec(**kw))
        assert not plan.ok, kw
    assert dc.plan_cell(dc.CellSpec(size=1e-3, height=1e-3, gap=1e-6, margin=0.0)).ok


def test_a_huge_turn_still_makes_a_regular_polygon():
    p = ge.Primitive("prism3", radius=2.0, height=1.0, angle=1e19)
    vert = p.vertices_2d()
    assert np.allclose(np.linalg.norm(vert, axis=1), 2.0)
    sides = [np.linalg.norm(vert[i] - vert[(i + 1) % 3]) for i in range(3)]
    assert np.allclose(sides, 2.0 * math.sqrt(3.0))
    assert not p.contains(np.array([[15.0, 0.0, 0.0], [0.0, 15.0, 0.0], [-15.0, -15.0, 0.0]])).any()


def test_long_names_keep_the_row_and_column_of_every_detector():
    kw = dict(shape="prism4", size=0.5, rows=3, cols=12, layers=2)
    plan = dc.plan_cell(dc.CellSpec(**kw))
    doc, geo = _world()
    res = dc.add_cell(doc, geo, plan, 2, 3, container=1, name="Prototipo B del detector pixelado de 2026, v2")
    _t, _d, geo2 = _reread(doc)
    descs = [geo2.body(geo2.find_body(lab).index).element.description.strip() for lab in res.elements]
    assert len(set(descs)) == len(descs)
    for e, d in zip(plan.elements, descs):
        assert d.endswith(f"fila {e.row}, columna {e.col}, capa {e.layer}") and len(d) <= ge.MAX_DESCRIPTION


def test_labels_are_found_below_the_largest_one():
    from argus.pengeom.model import BodyElement

    doc, geo = _world()
    body = next(el for el in doc.elements if isinstance(el, BodyElement))
    body.label = "9999"
    body.modified = True
    _t, doc, geo = _reread(doc)
    assert ge.new_label(doc, "body", geo).strip() == "1"
    plan = dc.plan_cell(dc.CellSpec(rows=3, cols=4))
    assert dc.total_problem(geo, plan) is None
    res = dc.add_cell(doc, geo, plan, 2, 3, container=geo.find_body("9999").index)
    assert sorted(int(x) for x in res.elements) == list(range(2, 14))
    # Una etiqueta con dígitos que no son del 0 al 9 (latin-1: «²») no es un número.
    pool = ge.LabelPool(doc, "body", taken={"²", "１２"})
    assert pool.next().strip() == "14"  # 1-13 y 9999, ocupadas


def test_materials_that_pengeom_does_not_take_are_refused_in_the_dialogs(qtbot):
    from PySide6.QtWidgets import QComboBox

    from argus.ui.add_body_dialog import combo_material, new_material_number

    combo = QComboBox()
    combo.setEditable(True)
    for text, ok in (("5", True), ("0", True), ("9998", True), ("9999", False), ("12345", False)):
        combo.setEditText(text)
        if ok:
            assert combo_material(combo) == int(text)
        else:
            with pytest.raises(ge.EditError):
                combo_material(combo)
    assert new_material_number({1, 2, 9999}) == 3
    assert new_material_number({1, 9998}) == 2
    assert new_material_number(set(range(1, 9999))) is None


def test_detector_lines_that_penhmain_cannot_read_are_not_rewritten():
    from argus.ui.detector_dialogs import DetectorChoice, apply_choice

    base = (CASE / "maniqui.in").read_text(encoding="latin-1")
    anchor = "       >>>>>>>> Absorbed dose distribution."
    doc = parse_in_text(base.replace(anchor, "IMPDET 1e5 2e8 50\nIDBODY 1\n" + anchor))
    assert any(ln.error for ln in doc.lines if ln.kw.strip() == "IMPDET")
    snap = doc.text()
    with pytest.raises(ValueError, match="IMPDET"):
        apply_choice(doc, DetectorChoice("impact"), [["   1"]])
    assert doc.text() == snap
    assert apply_choice(doc, DetectorChoice("energy"), [["   1"]]) == 1  # los de energía no tienen nada que ver


@pytest.mark.oracle
def test_the_detector_options_say_what_penhmain_would_say(qtbot, tmp_path):
    """La ventana de energía (con EU = 0, la energía máxima de la fuente; con intervalos logarítmicos, EL de al
    menos 50 eV o 1 eV), los intervalos y el forzado de interacciones: el diálogo lo rechaza si y solo si PMRDR
    real da error."""
    from argus.oracle.probe import find_sim_probe, run_sim_probe
    from argus.ui.detector_dialogs import DetectorChoice, DetectorOptions, apply_choice

    if find_sim_probe() is None:
        pytest.skip("Sonda de las comprobaciones de penhmain no compilada (python tools/build_probe.py)")
    geo_text = (CASE / "cubo-esfera.geo").read_text(encoding="latin-1")
    base = (CASE / "maniqui.in").read_text(encoding="latin-1")
    cases = [("energy", 0.0, 0.0, 100), ("energy", 2e8, 0.0, 100), ("energy", 0.0, 50.5, -100),
             ("energy", 10.0, 1e6, -100), ("impact", 2e8, 0.0, 100),
             ("impact", 0.0, 1.5, -10), ("impact", 0.5, 1e6, -10), ("impact", 99.0, 100.0, 5)]
    for kind, el, eu, nbe in cases:
        doc = parse_in_text(base, str(tmp_path / "maniqui.in"))
        opts = DetectorOptions(doc, cell=False)
        opts.kind.setCurrentIndex(opts.kind.findData(kind))
        opts.el.set_value(el)
        opts.eu.set_value(eu)
        opts.bins.set_value(nbe)
        verdict = opts.problem()
        trial = parse_in_text(base)
        apply_choice(trial, DetectorChoice(kind, 0, "cell", el, eu, nbe), [["   1"]])
        res = run_sim_probe(trial.text(), {"cubo-esfera.geo": geo_text})
        assert (verdict is None) == (res.iretrn == 0), (kind, el, eu, nbe, verdict, res.iretrn, res.reason)
    # Forzado de interacciones: los detectores de energía depositada no se admiten (3095); los de impacto, sí.
    pixe = CORPUS / "penhan/pixe/pixe.in"
    doc = parse_in_text(pixe.read_text(encoding="latin-1"), str(pixe))
    opts = DetectorOptions(doc, cell=False)
    opts.kind.setCurrentIndex(opts.kind.findData("energy"))
    assert "forzado" in (opts.problem() or "")
    opts.kind.setCurrentIndex(opts.kind.findData("impact"))
    assert opts.problem() is None or "forzado" not in opts.problem()
