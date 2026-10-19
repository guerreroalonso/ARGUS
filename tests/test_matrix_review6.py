"""Revisión de los resultados que se piden y de la matriz de dosis (0.13.1): RESUME con otras rejillas o intervalos,
detectores angulares con la ventana vacía, IDAGEL de más de 1000 intervalos, «Seguir la simulación», la nota que se
quedaba tras deshacer, la rejilla por defecto, «Energía depositada» con forzado y los textos."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from argus.penhan import sections as sc
from argus.penhan.dumpfile import parse_dump_text, read_dump, tally_differences
from argus.penhan.inputfile import parse_in_file, write_in_text

CORPUS = Path(__file__).parent / "corpus"
CAJA = CORPUS / "resultados" / "caja"


# ---------------------------------------------------------------------------------------------------------------------
# Un volcado completo, escrito como lo escribe penhmain 2025 (penhmain.f 5434-5492 y las rutinas …D)
# ---------------------------------------------------------------------------------------------------------------------

def _reals(vals) -> str:
    return "".join(f"  {float(v):24.16E}" for v in vals)


def _ints(vals) -> str:
    return "".join(f"{int(v):12d}" for v in vals)


def _bins(n: int, el: float, eu: float, log: bool) -> str:
    """NE, EL, EU, BSE, RBSE, LLE (con escala logarítmica, EL y EU son logaritmos)."""
    if log:
        el, eu = math.log(max(el, 1.0)), math.log(eu)
    bse = 1.000000001 * (eu - el) / n
    return _ints([n]) + _reals([el, eu, bse, 1.0 / bse]) + _ints([1 if log else 0])


# Lo que guarda penhmain con el .in de corpus/resultados/caja (comprobado con el volcado de un penhmain real).
CAJA_TALLIES = dict(
    nbe=(100, 0.0, 661700.0, False),
    nbangl=(30, 4, False),
    impact=[dict(bins=(70, 1e3, 7e5, False), idcut=1, nage=0),
            dict(bins=(50, 1e3, 7e5, True), idcut=2, nage=50)],
    angular=[dict(bins=(70, 1e3, 7e5, False), window=(90.0, 180.0, 0.0, 360.0), kkad=(1, 1, 1, 1, 1, 1)),
             dict(bins=(70, 1e3, 7e5, False), window=(0.0, 30.0, 0.0, 360.0), kkad=(0, 1, 0, 0, 0, 0))],  # ADKPAR 2
    deposit=[(140, 0.0, 7e5, False)],
    grid=(1, (11, 11, 25), (-5.0, -5.0, 0.0), (5.0, 5.0, 5.0)),
    epro=True,
)


def full_dump(title: str = "ARGUS corpus: caja", nbody: int = 4, repeat: bool = False, rwrite: float = 0.0,
              **over) -> str:
    """Un volcado entero de penhmain (cabecera, resultados y el principio de la dosis); ``repeat``: los ceros
    repetidos como «r*0.0», como los escribe ifort. ``rwrite``: las partículas que lleva escritas el espacio de fases
    del detector de impacto 1 (IPSF 1 en caja.in; con más de 0, penhmain no puede reanudar: duodécima revisión)."""
    t = {**CAJA_TALLIES, **over}

    def zeros(k: int) -> str:
        if repeat and k > 1:
            return f" {k}*0.000000000000000E+000"
        return _reals([0.0] * k)

    # 1e4 lluvias de las 2e4 de caja.in (NSIMSH): una simulación a medias, que penhmain sigue.
    lines = [_reals([1.0e4, 12.5, 12.0]), title.ljust(65)[:65], _ints([123456789, 987654321]),
             _ints([0]) + _reals([0.0]) + "  F", _ints([2]), _ints([2]) + " F T",
             _reals([3.2e4, 6.617e5, 6.617e5]), _reals([0.07, 0.851, 0.0]), _reals([750, 9250, 0.0]),
             _reals([6.617e5, 0, 0, 0, 0, 0, 0]) + _ints([0, 0])]
    lines += [zeros(n) for n in (3, 3, 18, 18, 2, 2, 2, 2, 2, 2, 24, 24, 3, 3)]
    lines.append(_ints([nbody]))
    lines += [zeros(nbody)] * 4
    n, el, eu, log = t["nbe"]
    lines.append(_bins(n, el, eu, log))
    lines += [zeros(12 * n)] * 2
    nth, nph, logt = t["nbangl"]
    lines.append(_ints([nth]) + _reals([0.0, 180.0, 6.0, 1 / 6]) + _ints([nph]) + _reals([90.0, 1 / 90])
                 + _ints([1 if logt else 0]))
    lines += [zeros(6 * nth * nph)] * 2 + [zeros(6 * nth)] * 3
    imp, ang, dep, grid = t["impact"], t["angular"], t["deposit"], t["grid"]
    lines.append(_ints([len(imp), len(ang), len(dep)]) + f" {'T' if grid else 'F'} {'T' if t['epro'] else 'F'} F")
    if imp:
        lines.append(_reals([5.0, rwrite]))  # RLAST, RWRITE
        lines.append(_ints([len(imp)]))
        lines += [_reals([1.0] * len(imp))] * 2
        lines.append(_ints([d["idcut"] for d in imp]))
        for i, d in enumerate(imp, start=1):
            n = d["bins"][0]
            lines += [f"spc-impdet-{i:02d}.dat   ", _bins(*d["bins"]), zeros(n), zeros(n), zeros(6 * n), zeros(6 * n)]
            if d["idcut"] == 2:
                lines += [f"fln-impdet-{i:02d}.dat   ", zeros(n), zeros(n), zeros(6 * n), zeros(6 * n)]
            lines.append(_bins(d["nage"], 0.0, 2e-9, False) if d["nage"] else _ints([0]) + _reals([0.0] * 4)
                         + _ints([0]))
            if d["nage"]:
                lines += [f"age-impdet-{i:02d}.dat   ", zeros(d["nage"]), zeros(d["nage"])]
    if ang:
        lines.append(_ints([len(ang)]))
        lines += [_reals([1.0] * len(ang))] * 2
        for i, d in enumerate(ang, start=1):
            n = d["bins"][0]
            lines += [f"spc-angdet-{i:02d}.dat   ", _reals([math.radians(a) for a in d["window"]]),
                      _ints(d.get("kkad", (1,) * 6)), _bins(*d["bins"]), zeros(6 * n), zeros(6 * n)]
    if dep:
        lines.append(_ints([len(dep)]))
        lines += [_reals([1.0] * len(dep))] * 2
        for i, b in enumerate(dep, start=1):
            lines += [f"spc-enddet-{i:02d}.dat   ", _bins(*b), zeros(b[0])]
    if grid:
        kdose, ndb, dxl, dxu = grid
        lines += [_ints(ndb), _reals(dxl), _reals(dxu), _reals([0.5] * 6), _ints([kdose])]
        cells = ndb[0] * ndb[1] * ndb[2]
        lines += [_reals([1.0] * cells), zeros(cells), zeros(cells), zeros(ndb[2]), zeros(ndb[2])]
    if t["epro"]:  # P3DMD (penhmain.f 10341-10374), en la rejilla de la dosis
        _kd, ndb, dxl, dxu = grid or (1, (1, 1, 1), (-5.0, -5.0, 0.0), (5.0, 5.0, 5.0))
        cells = ndb[0] * ndb[1] * ndb[2]
        lines += [_ints(ndb), _reals(dxl), _reals(dxu), _reals([0.5] * 6), _ints([1, 0]),
                  _reals([1.0] * cells), zeros(cells), zeros(cells), zeros(ndb[2]), zeros(ndb[2])]
    lines += ["", "   *** END ***"]
    return "\n".join(lines) + "\n"


def caja_doc():
    return parse_in_file(CAJA / "caja.in")


@pytest.mark.parametrize("repeat", [False, True])
def test_the_dump_says_which_grid_and_bins_penhmain_will_use(repeat):
    """penhmain toma del volcado, al reanudar, la rejilla de dosis y los intervalos de los resultados (HENANR,
    IMDETR, ANDETR, ENDETR, DOSER): ARGUS los lee (también con los ceros repetidos «r*0.0» de ifort)."""
    info = parse_dump_text(full_dump(repeat=repeat))
    assert info.usable, info.message
    t = info.tallies
    assert t is not None
    assert (t.energy.n, t.energy.hi, t.nth, t.nph) == (100, 661700.0, 30, 4)
    assert [(b.n, b.log, nage) for b, nage in t.impact] == [(70, False, 0), (50, True, 50)]
    assert t.impact[1][0].lo == pytest.approx(1e3) and t.impact[1][0].hi == pytest.approx(7e5)
    assert [w for _b, w in t.angular] == [pytest.approx((90, 180, 0, 360)), pytest.approx((0, 30, 0, 360))]
    assert [b.n for b in t.deposit] == [140] and t.epro
    assert t.grid == sc.DoseGrid((-5.0, 5.0, 11), (-5.0, 5.0, 11), (0.0, 5.0, 25))
    assert tally_differences(info, caja_doc()) == []


def test_what_differs_from_the_in_is_said():
    info = parse_dump_text(full_dump())
    doc = caja_doc()
    sc.set_dose_grid(doc, sc.DoseGrid((-5.0, 5.0, 11), (-5.0, 5.0, 11), (0.0, 5.0, 50)))
    assert tally_differences(info, doc) == [
        "rejilla de dosis: 11 × 11 × 25 vóxeles de 0,9091 × 0,9091 × 0,2 cm en el volcado y 11 × 11 × 50 vóxeles "
        "de 0,9091 × 0,9091 × 0,1 cm en el .in"]
    sc.set_dose_grid(doc, sc.DoseGrid((-4.0, 6.0, 11), (-5.0, 5.0, 11), (0.0, 5.0, 25)))  # la misma, en otro sitio
    assert "x −5…5, y −5…5, z 0…5 cm en el volcado y x −4…6" in tally_differences(info, doc)[0]
    sc.set_dose_grid(doc, sc.DoseGrid((5.0, -5.0, 11), (-5.0, 5.0, 11), (0.0, 5.0, 25)))  # al revés: es la misma
    assert tally_differences(info, doc) == []
    sc.set_external_process(doc, None)  # EPROF necesita la rejilla
    sc.set_dose_grid(doc, sc.DoseGrid())
    out = tally_differences(info, doc)
    assert "el volcado tiene una" in out[0] and "penhmain la seguirá calculando" in out[0]
    assert out[-1] == "proceso externo (EPROF): el volcado lo tiene y el .in no"
    doc = caja_doc()
    dets = sc.impact_detectors(doc)
    dets[1].age = (0.0, 2e-9, 20)
    sc.set_impact_detectors(doc, dets)
    sc.set_value(doc, "NBANGL", [45, 18])
    sc.set_value(doc, "NBE", [0.0, 1e6, 100])
    d = sc.energy_detectors(doc)
    d[0].eu = 0.0  # la energía máxima de la fuente: no se compara (penhmain la calcula)
    sc.set_energy_detectors(doc, d)
    a = sc.angular_detectors(doc)
    a[1].theta2 = 20.0
    sc.set_angular_detectors(doc, a + [sc.AngularDetector()])
    out = tally_differences(info, doc)
    assert out == ["detector de impacto 2: 50 intervalos de edad en el volcado y 20 intervalos en el .in",
                   "detectores angulares: 2 en el volcado y 3 en el .in",
                   "espectro de lo que sale (NBE): de 0 eV a 661,7 keV en el volcado y de 0 eV a 1 MeV en el .in",
                   "ángulos de lo que sale (NBANGL): 30 × 4 intervalos en el volcado y 45 × 18 en el .in"]
    sc.set_angular_detectors(doc, a)
    assert "θ de 0 a 30°, φ de 0 a 360° en el volcado y θ de 0 a 20°" in tally_differences(info, doc)[1]
    # Un volcado sin rejilla ni EPROF frente al .in que los pide.
    info = parse_dump_text(full_dump(grid=None, epro=False))
    out = tally_differences(info, caja_doc())
    assert out[0].startswith("rejilla de dosis: el .in tiene una") and "penhmain no calculará la dosis" in out[0]
    assert out[-1] == "proceso externo (EPROF): el .in lo pide y el volcado no lo tiene"


def test_a_dump_that_cannot_be_read_further_is_damaged():
    """Un valor que no es un número en los resultados: penhmain 2025 los lee sin ERR= y se detiene con un error de
    ejecución (comprobado con el penhmain real: «Bad real number»). Antes se callaba (``tallies`` None, sin avisar:
    duodécima revisión); ahora el volcado está dañado y no se compara nada."""
    text = full_dump().replace("spc-impdet-01.dat", "x x x")
    info = parse_dump_text(text)
    assert info.usable and info.tallies is not None  # el nombre es texto: se lee igual
    broken = full_dump().split("\n")
    broken[30] = "  no es un número"
    info = parse_dump_text("\n".join(broken))
    assert not info.usable and (info.problem, info.problem_in) == ("format", "results")
    assert "«no»" in info.message and "línea 31" in info.message and tally_differences(info, caja_doc()) == []


def _resume_case(tmp_path: Path, grid_z=(0.0, 5.0, 50)) -> tuple[Path, object]:
    d = tmp_path / "caso"
    d.mkdir(parents=True)
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, d / n)
    doc = parse_in_file(d / "caja.in")
    sc.set_text(doc, "RESUME", "dump.dmp")
    sc.set_dose_grid(doc, sc.DoseGrid((-5.0, 5.0, 11), (-5.0, 5.0, 11), grid_z))
    (d / "caja.in").write_bytes(write_in_text(doc).encode("latin-1"))
    (d / "dump.dmp").write_text(full_dump())
    return d / "caja.in", parse_in_file(d / "caja.in")


def test_resume_with_another_grid_is_warned_everywhere(tmp_path):
    """RESUME con GRIDZ cambiada: la comprobación del .in, «Ejecutar» y la tarjeta «Dosis» lo dicen (antes solo
    «penhmain continuará la simulación guardada…», y penhmain escribía la rejilla vieja)."""
    from argus.penhan import run as rn
    from argus.penhan.validate import validate_case

    in_path, doc = _resume_case(tmp_path)
    msgs = [d.message for d in validate_case(doc, None, in_path.parent)]
    hit = [m for m in msgs if "toma del volcado las rejillas" in m]
    assert hit and "11 × 11 × 25 vóxeles" in hit[0] and "empiece sin RESUME" in hit[0], msgs
    plan = rn.plan_run(in_path, doc, penhmain=None)
    assert plan.resume == "dump.dmp" and plan.resume_diffs and plan.resume_diffs[0].startswith("rejilla de dosis")
    assert read_dump(in_path.parent / "dump.dmp").tallies is not None


def test_the_dose_card_and_the_dialog_say_it(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from argus.ui.outputs_dialog import resume_note
    from argus.ui.sim_outputs import DoseSection, JobSection

    in_path, doc = _resume_case(tmp_path)
    s = DoseSection()
    qtbot.addWidget(s)
    s.set_context(doc, None, in_path.parent)
    assert not s.resume_note.isHidden() and "sigue con la rejilla del volcado" in s.resume_note.text()
    note = resume_note(doc, in_path.parent)
    assert "Se reanuda la simulación de dump.dmp" in note and "Ahora no coinciden: rejilla de dosis" in note
    job = JobSection()
    qtbot.addWidget(job)
    job.set_context(doc, None, in_path.parent)
    assert "rejilla de dosis" in job.resume_state.text()
    # Con la rejilla del volcado no hay nota en la tarjeta (sí en el diálogo: lo de aquí no cuenta al reanudar).
    in_path2, doc2 = _resume_case(tmp_path / "b", grid_z=(0.0, 5.0, 25))
    s.set_context(doc2, None, in_path2.parent)
    assert s.resume_note.isHidden()
    assert "Ahora no coinciden" not in resume_note(doc2, in_path2.parent)


REAL = os.environ.get("ARGUS_PENHMAIN")


@pytest.mark.skipif(not REAL or not os.environ.get("ARGUS_PENHMAIN_MATS"),
                    reason="ARGUS_PENHMAIN y ARGUS_PENHMAIN_MATS: un penhmain de verdad y los .mat del caso caja")
def test_with_a_real_penhmain_resume_keeps_the_grid_of_the_dump(tmp_path):
    """Con el penhmain de verdad: el volcado que escribe se lee entero y coincide con el .in; con GRIDZ cambiada y
    RESUME, ARGUS avisa, y penhmain escribe, en efecto, la rejilla del volcado."""
    from argus.penhan.validate import validate_case

    mats = Path(os.environ["ARGUS_PENHMAIN_MATS"])
    d = tmp_path / "caso"
    d.mkdir()
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, d / n)
    doc = parse_in_file(d / "caja.in")
    for md in doc.model.materials:
        shutil.copy(mats / md.fname.strip(), d / md.fname.strip())
    sc.set_value(doc, "NSIMSH", [300.0])
    sc.set_value(doc, "LTRACK", None)
    dets = sc.impact_detectors(doc)
    for det in dets:
        det.ipsf = 0  # con el espacio de fases de un detector (IPSF 1), penhmain no puede reanudar (duodécima revisión)
    sc.set_impact_detectors(doc, dets)
    (d / "caja.in").write_bytes(write_in_text(doc).encode("latin-1"))

    def run() -> None:
        with open(d / "caja.in", "rb") as fin:
            subprocess.run([REAL], cwd=d, stdin=fin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=600, check=False)

    run()
    info = read_dump(d / "dump.dmp")
    assert info.usable and info.tallies is not None, info.message
    assert tally_differences(info, parse_in_file(d / "caja.in")) == []
    sc.set_text(doc, "RESUME", "dump.dmp")
    sc.set_value(doc, "NSIMSH", [600.0])
    sc.set_dose_grid(doc, sc.DoseGrid((-5.0, 5.0, 11), (-5.0, 5.0, 11), (0.0, 5.0, 50)))
    (d / "caja.in").write_bytes(write_in_text(doc).encode("latin-1"))
    doc = parse_in_file(d / "caja.in")
    assert any("toma del volcado las rejillas" in m.message for m in validate_case(doc, None, d))
    run()
    from argus.results.summary import read_summary

    assert "Simulation has been resumed" in (d / "penhmain.dat").read_text(encoding="latin-1")
    # (Que el resumen tenga las 600: hasta 0.17.3, con IPSF 1, penhmain se detenía al escribir y el mapa era el de la
    # primera ejecución, que también tiene 25 capas.)
    assert read_summary(d / "penhmain-res.dat").showers == 600
    head = (d / "3d-dose-map.dat").read_text(encoding="latin-1")[:2000]
    assert "NBZ =  25" in head  # la rejilla del volcado, no los 50 intervalos del .in


# ---------------------------------------------------------------------------------------------------------------------
# «Energía depositada» con forzado (MX4)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line", ["IFORCE 1 1 2 10 0.1 2", "IFORCE 1 1 2 -5 0.1 2", "IXRSPL 1 2"])
def test_an_energy_deposition_detector_is_not_added_with_forcing(line):
    """Con forzado de interacciones efectivo (FORCER > 1 o negativo) o splitting de rayos X, penhmain se detendría
    con el error 3095: «Energía depositada» no se pide y se dice por qué (antes, «detector … con DETE» en verde)."""
    from argus.penhan import goals as gl
    from argus.results.case import load_case_geometry

    case = load_case_geometry(CAJA / "caja.in")
    text = (CAJA / "caja.in").read_text("latin-1")
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith(("ENDETC", "EDBODY")))
    text = text.replace("GEOMFN caja.geo", "GEOMFN caja.geo\n" + line.split()[0].ljust(6) + " "
                        + " ".join(line.split()[1:]))
    from argus.penhan.inputfile import parse_in_text

    doc = parse_in_text(text, str(CAJA / "caja.in"))
    kb = next(k for k, _t in gl.detector_bodies(case.geo))
    with pytest.raises(ValueError, match="3095"):
        gl.apply(doc, "deposito", geo=case.geo, kb=kb)
    assert sc.energy_detectors(doc) == []


# ---------------------------------------------------------------------------------------------------------------------
# Detectores angulares con la ventana vacía (MX7) e IDAGEL de más de 1000 intervalos (MX8)
# ---------------------------------------------------------------------------------------------------------------------

def _caja_with(replace: dict[str, str]):
    from argus.penhan.inputfile import parse_in_text

    text = (CAJA / "caja.in").read_text("latin-1")
    for a, b in replace.items():
        assert a in text, a
        text = text.replace(a, b)
    return parse_in_text(text, str(CAJA / "caja.in"))


@pytest.mark.parametrize("adener, empty", [("ADENER 7e5 1e3 70", True), ("ADENER 1e6 0 70", True),
                                           ("ADENER 1e3 7e5 70", False), ("ADENER 0 0 -70", False)])
def test_an_empty_angular_window_is_warned(tmp_path, adener, empty):
    """penhmain no comprueba la ventana de los detectores angulares: con EU por debajo de EL (también con la EU
    automática, 661,7 keV con esta fuente) el espectro sale vacío sin avisar."""
    from argus.penhan.validate import validate_case

    doc = _caja_with({"ADENER 1e3 7e5 70                     [Energy window and no. of bins]\nADKPAR":
                      adener + "\nADKPAR"})
    msgs = [d.message for d in validate_case(doc, None, CAJA)]
    hit = [m for m in msgs if "Detector angular 2" in m and "está vacía" in m]
    assert bool(hit) == empty, msgs


def test_too_many_age_bins_are_an_error():
    from argus.penhan.validate import validate_case

    doc = _caja_with({"IDAGEL 0 2e-9 50": "IDAGEL 0 2e-9 2000"})
    msgs = [d.message for d in validate_case(doc, None, CAJA) if d.severity == 2]
    assert any("2000 intervalos de edad" in m and "admite 1000" in m for m in msgs), msgs


def test_the_editors_mark_what_penhmain_would_not_use(qtbot):
    """El editor de un detector angular enseña «E máx.» con EU < 50 eV (no «0 eV») y marca en rojo una ventana
    vacía; los intervalos de más (IDAGEL 2000, GRIDX 150) se marcan en vez de enseñar el máximo como si fuera bueno.
    LTRACK 100 enseñaba 50 sin marcarlo; desde 0.17.1 «Trabajo» no tiene LTRACK (las lluvias para verlas en 3D son
    «Lluvias», al ejecutar) y el que trae el .in se queda como está."""
    pytest.importorskip("PySide6")
    from argus.ui.sim_outputs import AngularSection, DoseSection, ImpactSection, JobSection

    doc = _caja_with({"ADENER 1e3 7e5 70                     [Energy window and no. of bins]\nADKPAR":
                      "ADENER 7e5 0 70\nADKPAR", "IDAGEL 0 2e-9 50": "IDAGEL 0 2e-9 2000",
                      "GRIDX  -5 5 11": "GRIDX  -5 5 150", "LTRACK 5": "LTRACK 100"})
    a = AngularSection()
    qtbot.addWidget(a)
    a.set_context(doc, None, CAJA)
    a._on_select(1)
    assert a.eu.text() == "" and a.eu.placeholderText() == "E máx."
    doc2 = _caja_with({"ADENER 1e3 7e5 70                     [Energy window and no. of bins]\nADKPAR":
                       "ADENER 7e5 1e3 70\nADKPAR"})
    a.set_context(doc2, None, CAJA)
    a._on_select(1)
    assert "vacía" in a.el.toolTip() and "background" in a.eu.styleSheet()
    a._on_select(0)
    assert a.el.styleSheet() == "" and "vacía" not in a.el.toolTip()
    imp = ImpactSection()
    qtbot.addWidget(imp)
    imp.set_context(doc, None, CAJA)
    imp._on_select(1)
    assert "background" in imp.age_bins.spin.styleSheet() and "2000" in imp.age_bins.spin.toolTip()
    dose = DoseSection()
    qtbot.addWidget(dose)
    dose.set_context(doc, None, CAJA)
    assert "background" in dose.n["x"].spin.styleSheet() and "101" in dose.n["x"].spin.toolTip()
    job = JobSection()
    qtbot.addWidget(job)
    job.set_context(doc, None, CAJA)
    assert not hasattr(job, "ltrack") and sc.value(doc, "LTRACK") == [100]


# ---------------------------------------------------------------------------------------------------------------------
# «Seguir la simulación» y las trayectorias: lo que hará penhmain (MX12)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("dumpp, text", [(None, ""), ("60", "cada 60 s en dump.dmp"),
                                         ("10", "cada 30 s (penhmain no baja de 30 s) en dump.dmp"),
                                         ("0", "cada 30 s (penhmain no baja de 30 s) en dump.dmp"),
                                         ("-5", "cada 30 s (penhmain no baja de 30 s) en dump.dmp"),
                                         ("1e5", "cada 24 h (penhmain no pasa de un día) en dump.dmp"),
                                         ("1e12", "")])
def test_follow_status_says_what_penhmain_will_do(dumpp, text):
    from argus.penhan import goals as gl

    rep = {"DUMPP  30                                   [Dumping period, in sec]":
           "" if dumpp is None else f"DUMPP  {dumpp}"}
    doc = _caja_with(rep)
    assert gl.status(doc, "seguir") == text
    if not text:  # marcarlo lo pide de verdad (con 1e12, antes no cambiaba nada y la casilla no se marcaba)
        gl.apply(doc, "seguir")
        assert gl.status(doc, "seguir") == "cada 60 s en dump.dmp"


def test_unchecking_follow_undoes_what_checking_did():
    from argus.penhan import goals as gl
    from argus.ui.outputs_dialog import add, remove

    doc = _caja_with({"DUMPTO dump.dmp                  [Generate this dump file, 20 chars]\n": "",
                      "DUMPP  30                                   [Dumping period, in sec]\n": ""})
    before = write_in_text(doc)
    assert gl.status(doc, "seguir") == ""
    add(doc, "seguir")
    assert sc.text(doc, "DUMPTO") == "dump.dmp" and gl.status(doc, "seguir")
    remove(doc, "seguir")
    assert write_in_text(doc) == before
    # Un volcado con otro nombre lo puso el usuario: se queda.
    sc.set_text(doc, "DUMPTO", "mio.dmp")
    add(doc, "seguir")
    remove(doc, "seguir")
    assert sc.text(doc, "DUMPTO") == "mio.dmp" and sc.value(doc, "DUMPP") is None


def test_track_status_says_penhmain_keeps_50():
    """penhmain guarda NPSHOW = MIN(LTRACK, 50) lluvias y una más (revisión de 0.16: mira N.GT.NPSHOW después de
    cada una; LTRACK 3 da 4 lluvias en los .trk y LTRACK 99, 51)."""
    from argus.penhan import goals as gl

    doc = _caja_with({"LTRACK 5": "LTRACK 100"})
    assert gl.status(doc, "trayectorias") == "51 lluvias (LTRACK 100: penhmain guarda como mucho 51)"
    doc = _caja_with({"LTRACK 5": "LTRACK 1"})
    assert gl.status(doc, "trayectorias") == "2 lluvias (LTRACK 1: penhmain escribe también la siguiente)"


def test_typing_zero_in_dumpp_removes_the_line(qtbot):
    """En «Trabajo», 0 quita DUMPP (con «DUMPP 0», penhmain volcaría cada 30 s: lo contrario de lo que se quiere
    decir), como el diálogo; al lado se dice lo que hará penhmain."""
    pytest.importorskip("PySide6")
    from argus.ui.sim_outputs import JobSection

    doc = _caja_with({"DUMPP  30": "DUMPP  10"})
    job = JobSection()
    qtbot.addWidget(job)
    edits = []
    job.apply.connect(lambda text, fn: edits.append(fn))
    job.set_context(doc, None, CAJA)
    assert job.dumpp_text.text() == "penhmain no baja de 30 s"
    job.dumpp.setText("0")
    job.dumpp.setModified(True)
    job.dumpp._commit()
    edits[-1](doc)
    assert sc.value(doc, "DUMPP") is None
    job.set_context(doc, None, CAJA)
    assert job.dumpp_text.text().startswith("sin DUMPP")


# ---------------------------------------------------------------------------------------------------------------------
# «Elegir resultados…» desde «Ejecutar» (MX13), la nota que se quedaba tras deshacer (MX16) y los textos
# ---------------------------------------------------------------------------------------------------------------------

def test_fit_and_show_work_from_the_run_dialog(qtbot, monkeypatch, tmp_path):
    """«Ajustar a la geometría» y «Ver en el corte» en «Ejecutar → Elegir resultados…» llegan a la ventana principal
    (antes el botón estaba activo y no hacía nada)."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QWidget

    from argus.session import open_case
    from argus.ui.input_controller import InputController
    from argus.ui.outputs_dialog import OutputsDialog
    from argus.ui.run_dialog import RunDialog
    from argus.ui.settings import app_settings

    d = tmp_path / "caso"
    d.mkdir()
    for n in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, d / n)
    cf = open_case(d / "caja.in")
    ctrl = InputController(cf.input_doc, d / "caja.in")
    calls = []

    class Main(QWidget):
        def show_dose_grid(self):
            calls.append("ver")

        def fit_dose_grid(self):
            calls.append("ajustar")

    main = Main()
    qtbot.addWidget(main)
    dlg = RunDialog(main, ctrl, cf.session, app_settings())
    qtbot.addWidget(dlg)

    def fake_exec(self):
        self.fitGridRequested.emit()
        self.showGridRequested.emit()
        return 0

    monkeypatch.setattr(OutputsDialog, "exec", fake_exec)
    dlg._choose_outputs()
    assert calls == ["ajustar", "ver"]


def test_the_voxel_note_goes_away_when_the_grid_changes(qtbot):
    """La nota «Como mucho 101 intervalos…» es de la rejilla de entonces: tras deshacer (u otra rejilla) se quita."""
    pytest.importorskip("PySide6")
    from argus.ui.sim_outputs import DoseSection

    doc = caja_doc()
    s = DoseSection()
    qtbot.addWidget(s)
    s.apply.connect(lambda text, fn: (fn(doc), s.set_context(doc, None, CAJA)))
    s.set_context(doc, None, CAJA)
    before = sc.dose_grid(doc)
    s._on_size("y", 0.05)  # 200 intervalos: no caben (101)
    assert s.size_note.text().startswith("Como mucho 101 intervalos") and "0,099" in s.size_note.text()
    sc.set_dose_grid(doc, sc.DoseGrid(before.x, (-4.0, 4.0, 11), before.z))  # «deshacer» o el panel: otra rejilla
    s.set_context(doc, None, CAJA)
    assert s.size_note.text() == ""


def test_numbers_in_the_dose_texts():
    from argus.ui.sim_outputs import DoseSection, _bytes

    assert _bytes(999_600) == "1 MB" and _bytes(4_147_200) == "4,15 MB" and _bytes(512) == "512 B"
    t = DoseSection._voxel_text(sc.DoseGrid((-6.0, 6.0, 24), (-6.0, 6.0, 24), (0.0, 15.0, 75)))
    assert t == ("Vóxel de 0,5 × 0,5 × 0,2 cm: 24 × 24 × 75 = 43\u202f200 vóxeles (3d-dose-map.dat de unos 4,15 MB)."
                 " Número par de intervalos en X y en Y.")
    assert sc.grid_text(sc.DoseGrid(z=(0.0, 10.0, 1), r=(5.0, 1))) == "r = 5 cm en 1 anillo, z 0…10 cm en 1 capa"


# ---------------------------------------------------------------------------------------------------------------------
# La rejilla por defecto empieza en la superficie (MX17)
# ---------------------------------------------------------------------------------------------------------------------

def test_the_matter_box_is_not_one_percent_bigger():
    """«Dosis» y «Ajustar a la geometría» ponen la matriz en la caja de los cuerpos con material: con caras planas,
    justo en ellas (antes medio paso de los rayos más fuera: la primera capa de la caja, de −0,106 a −0,005 cm, toda
    fuera del agua)."""
    from argus.penhan import goals as gl
    from argus.render.raytrace import RayTracer
    from argus.render.scene3d import matter_bounds
    from argus.results.case import load_case_geometry

    case = load_case_geometry(CAJA / "caja.in")
    rt = RayTracer(case.session.geo)
    lo, hi = matter_bounds(rt, *case.session.bounds, rt.default_visibility())
    assert lo[2] == pytest.approx(0.0, abs=1e-9) and hi[2] == pytest.approx(10.0, abs=1e-9)
    g = gl.default_grid("dosis-caja", (lo, hi))
    assert g.z[:2] == (0.0, 10.0) and g.x[:2] == (-10.0, 10.0)
    case = load_case_geometry(CORPUS / "resultados" / "esfera" / "esfera.in")
    rt = RayTracer(case.session.geo)
    lo, hi = matter_bounds(rt, *case.session.bounds, rt.default_visibility())
    assert np_all_close(hi, 5.0, 0.01) and np_all_close(lo, -5.0, 0.01)
    assert gl.default_grid("dosis-esfera", (lo, hi)).r[0] == 5.0


def np_all_close(v, x: float, tol: float) -> bool:
    return all(abs(float(a) - x) <= tol for a in v)


def test_no_menu_repeats_an_accelerator(qtbot):
    """Con una letra repetida, Alt+S, E solo salta entre las entradas que la tienen (no ejecuta): en Simulación
    estaban «&Elegir…» y «&Ejecutar penhmain…», y en Archivo, cuatro con la G."""
    pytest.importorskip("PySide6")
    import re

    from argus.ui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)

    def acc(text: str) -> str | None:
        m = re.search(r"&([^&])", text.replace("&&", ""))
        return m.group(1).lower() if m else None

    tops = [acc(a.text()) for a in w.menuBar().actions()]
    assert len(tops) == len(set(tops))
    for a in w.menuBar().actions():
        keys = [acc(b.text()) for b in a.menu().actions() if not b.isSeparator()]
        keys = [k for k in keys if k]
        assert len(keys) == len(set(keys)), (a.text(), [b.text() for b in a.menu().actions()])
    w.close()


def test_grid_bounds_from_the_geometry_are_round():
    """La superficie de un cilindro o de una esfera cae entre dos rayos (4,9988 por 5); una cara en 0, con el ruido del
    trazado, no deja «-1e-12» en el .in."""
    assert sc.round_bound(4.9988, 10) == 5.0 and sc.round_bound(-9.9976, 20) == -10.0
    assert sc.round_bound(12.34, 30) == 12.34 and sc.round_bound(0.5012, 1) == 0.5012
    assert sc.round_bound(-1e-12, 10) == 0.0 and str(sc.round_bound(-0.0, 10)) == "0.0"
    assert sc.round_bound(1000.015, 10) == 1000.015  # sin perder cifras lejos del origen
