"""Novena revisión (0.16.3): resultados de penhmain.

* geometry.rep: PENGEOM escribe el material con I3; el 9999 de un TAC sale «***» y la geometría del caso no se daba
  por cambiada.
* Estado de la simulación: el «Calculation time» incluye la inicialización (PEINIT/PHINIT); TIME cuenta desde que
  acaba (TSECIN): no se da por terminada por TIME con el tiempo total, y lo que falta no sale de n/tiempo total.
* Datos medidos: son absolutos, no se multiplican por N (ni por A·t).
* Volver a simular con menos intervalos en la misma carpeta: la curva nueva se ve.
* Panel «Espectro»: canales mucho más anchos que la cola E0 del PIPS (sin OverflowError).
* «Pico de Bragg»: lo distal desde el final de la curva (meseta con ruido) y en el sentido del haz (también hacia −z).
* psource.dat con un intervalo de anchura cero (la energía máxima repetida del SOBP): una línea con su probabilidad.
* CSV de medidas con la coma decimal entre comillas; «% del valor en un punto» que no se puede aplicar; χ² con las
  medidas; fuentes radiactivas: desintegraciones.
"""

from __future__ import annotations

import math
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import CORPUS

RES = CORPUS / "resultados"


def _old(path: Path, age: float = 3600.0) -> None:
    t = time.time() - age
    os.utime(path, (t, t))


def _age_all(folder: Path, age: float = 3600.0) -> None:
    for p in folder.rglob("*"):
        if p.is_file():
            _old(p, age)


# -- 1. geometry.rep con el material del TAC («MAT =***») -----------------------------------------------------------

# Un TAC dentro de un cubo y una esfera (como el caso del revisor), y el árbol que escribió el PENGEOM de penhan para
# esa geometría (geometry.rep real: el módulo del TAC lleva «MAT =***»).
CT_IN_CUBE_GEO = """\
Cubo con un TAC dentro
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   1)  Planos en X
INDICES=( 1, 0, 0, 0,-1)
X-SCALE=( 2.500000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   2)  Planos en Y
INDICES=( 0, 1, 0, 0,-1)
Y-SCALE=( 2.500000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   3)  Plano Z max
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=( 5.000000000000000E+01,   0)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   4)  Plano Z min
INDICES=( 0, 0, 0, 1, 0)
Z-SHIFT=( 0.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
CTBOX   ( TAC)   TAC
CTFNAME=(tac.ct      )
1111111111111111111111111111111111111111111111111111111111111111
X-SHIFT=(-4.000000000000000E+00,   0)
Y-SHIFT=(-3.000000000000000E+00,   0)
Z-SHIFT=( 1.000000000000000E+00,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   1)  Cubo
MATERIAL(   1)
SURFACE (   1), SIDE POINTER=(-1)
SURFACE (   2), SIDE POINTER=(-1)
SURFACE (   3), SIDE POINTER=(-1)
SURFACE (   4), SIDE POINTER=( 1)
MODULE  ( TAC)
0000000000000000000000000000000000000000000000000000000000000000
SURFACE (   5)  Esfera
INDICES=( 1, 1, 1, 0,-1)
X-SCALE=( 5.000000000000000E+02,   0)
Y-SCALE=( 5.000000000000000E+02,   0)
Z-SCALE=( 5.000000000000000E+02,   0)
0000000000000000000000000000000000000000000000000000000000000000
BODY    (   2)  Esfera (con hueco)
MATERIAL(   0)
SURFACE (   5), SIDE POINTER=(-1)
BODY    (   1)
0000000000000000000000000000000000000000000000000000000000000000
END      0000000000000000000000000000000000000000000000000000000
"""

REAL_TREE = """\
************  Genealogical tree.


*** MODULE =    1,  KMOTH =    4,  MAT =***
KDGHT =    1
KSURF =    5    6    7    8    9   10   12
KFLAG =    2    2    2    2    2    2    1

*** BODY   =    2,  KMOTH =    4,  MAT =  1
KBODY =    1
KSURF =    1    2    3    4    5    6    7    8    9   10   12
KFLAG =    1    1    1    2    3    3    3    3    3    3    1

*** BODY   =    3,  KMOTH =    4,  MAT =  0
KBODY =    2
KSURF =   11    1    2    3    4    5    6    7    8    9   10   12
KFLAG =    1    3    3    3    3    3    3    3    3    3    3    1

*** MODULE =    4,  KMOTH =    0,  MAT =  0
KDGHT =    1    2    3    4
KSURF =   12    5    6    7    8    9   10    1    2    3    4   11
KFLAG =    1    4    4    4    4    4    4    4    4    4    4    4
"""


def _ct_case(tmp_path: Path, tree: str = REAL_TREE) -> Path:
    d = tmp_path / "tac-en-cubo"
    shutil.copytree(RES / "tac", d)
    (d / "tac.geo").write_text(CT_IN_CUBE_GEO, encoding="latin-1")
    (d / "geometry.rep").write_text(CT_IN_CUBE_GEO + "\n\n" + tree, encoding="latin-1")
    return d


def test_geometry_rep_material_of_the_ct_does_not_fit_in_i3():
    from argus.results.case import report_tree

    rows = report_tree(REAL_TREE)
    assert rows == [(1, True, 4, None), (2, False, 4, 1), (3, False, 4, 0), (4, True, 0, 0)]


def test_a_ct_case_is_the_geometry_that_penhmain_simulated(tmp_path):
    from argus.results.case import load_case_geometry

    d = _ct_case(tmp_path)
    case = load_case_geometry(d / "tac.in")
    assert case.ok, case.problem
    assert case.body_material(1) == 9999  # el módulo del TAC
    assert case.mismatch is None, case.mismatch
    # Un material que sí cabe en tres cifras donde ahora está el TAC (o al revés): la geometría ha cambiado.
    d2 = _ct_case(tmp_path / "otro", REAL_TREE.replace("MAT =***", "MAT =  1"))
    case2 = load_case_geometry(d2 / "tac.in")
    assert case2.mismatch is not None and "n.º 1" in case2.mismatch
    d3 = _ct_case(tmp_path / "otro2", REAL_TREE.replace("BODY   =    2,  KMOTH =    4,  MAT =  1",
                                                         "BODY   =    2,  KMOTH =    4,  MAT =***"))
    case3 = load_case_geometry(d3 / "tac.in")
    assert case3.mismatch is not None and "n.º 2" in case3.mismatch and "***" in case3.mismatch


# -- 2. estado de la simulación: la inicialización no cuenta para TIME --------------------------------------------

def _set_summary(res: Path, showers: float, total: float, cpu: float) -> None:
    """El resumen de un volcado con esas lluvias, ese «Calculation time» y ese tiempo de CPU de la simulación."""
    import re

    text = res.read_text(encoding="latin-1")
    text = re.sub(r"(Simulated primary particles \.+\s+)\S+", lambda m: m.group(1) + f"{showers:.6E}", text)
    text = re.sub(r"(Calculation time \.+\s+)\S+", lambda m: m.group(1) + f"{total:.6E}", text)
    text = re.sub(r"(Effective \(CPU\) simulation time \.+\s+)\S+", lambda m: m.group(1) + f"{cpu:.6E}", text)
    text = re.sub(r"(Simulation speed \.+\s+)\S+", lambda m: m.group(1) + f"{showers / cpu:.6E}", text)
    res.write_text(text, encoding="latin-1")


def _protons_running(tmp_path: Path, time_limit: str = "120") -> Path:
    """Como la ejecución real de protones del caso 2 de Rafael: NSIMSH 50000, TIME 120, DUMPP 30, y una
    inicialización (PEINIT/PHINIT con datos nucleares) de 6 min; sin el «END» del registro de penhmain."""
    import re

    d = tmp_path / "protones"
    shutil.copytree(RES / "caja", d)
    (d / "penhmain.dat").unlink()
    (d / "run.log").unlink()
    text = (d / "caja.in").read_text(encoding="latin-1")
    text = re.sub(r"(?m)^NSIMSH .*$", "NSIMSH 50000", text)
    text = re.sub(r"(?m)^TIME .*$", f"TIME   {time_limit}", text)
    text = re.sub(r"(?m)^DUMPP .*$", "DUMPP  30", text)
    (d / "caja.in").write_text(text, encoding="latin-1")
    return d


def test_initialisation_time_does_not_end_the_simulation_by_time(tmp_path):
    from argus.penhan.run import running_state
    from argus.results.folder import ResultsFolder

    d = _protons_running(tmp_path)
    # Primer volcado, nada más acabar la inicialización: 1 lluvia, 362 s de cálculo, unos milisegundos de CPU.
    _set_summary(d / "penhmain-res.dat", 1.0, 362.1, 0.002)
    st = ResultsFolder(d).state()
    assert st.status == "simulando", st.text
    assert "TIME" not in st.text and st.progress < 0.1
    assert st.eta is not None and st.eta < 600, st.text  # antes, «faltan unos 209,5 días» (TIME de 1e9)
    assert running_state(d) is not None  # ARGUS avisa de que penhmain sigue simulando en la carpeta
    # Segundo volcado, 30 s después: la velocidad de verdad entre los dos (lo que faltaba eran 64 s).
    _set_summary(d / "penhmain-res.dat", 16376.0, 392.6, 14.0)
    st = ResultsFolder(d).state()
    assert st.status == "simulando" and 50.0 < st.eta < 80.0, st.text
    # Con TIME de 1e9, igual (antes, el ritmo era el de las lluvias por el tiempo total).
    d2 = _protons_running(tmp_path / "b", "1e9")
    _set_summary(d2 / "penhmain-res.dat", 1.0, 362.1, 0.002)
    st = ResultsFolder(d2).state()
    assert st.status == "simulando" and st.eta < 600, st.text


def test_the_end_by_time_is_still_detected(tmp_path):
    from argus.results.folder import ResultsFolder

    d = _protons_running(tmp_path)
    # El tiempo de CPU de la simulación ya llega a TIME: es el resumen del final.
    _set_summary(d / "penhmain-res.dat", 40000.0, 480.0, 121.0)
    _age_all(d, 600)
    st = ResultsFolder(d).state()
    assert st.status == "terminada" and "se acabó el tiempo de TIME" in st.text, st.text
    # Un tiempo total que no llega a DUMPP: no puede ser un volcado parcial (TIME de 10 s).
    d2 = _protons_running(tmp_path / "b", "10")
    _set_summary(d2 / "penhmain-res.dat", 3000.0, 14.0, 9.0)
    st = ResultsFolder(d2).state()
    assert st.status == "terminada" and "TIME" in st.text, st.text
    # Pasado el tiempo total de TIME, viejo y sin el registro: puede que haya acabado o que se haya parado.
    d3 = _protons_running(tmp_path / "c")
    _set_summary(d3 / "penhmain-res.dat", 30000.0, 450.0, 60.0)
    _age_all(d3, 3600)
    st = ResultsFolder(d3).state()
    assert st.status == "parada" and "TIME" in st.text, st.text


def test_time_below_one_second_is_100_s_like_penhmain(tmp_path):
    from argus.results.folder import ResultsFolder

    d = _protons_running(tmp_path, "0.5")
    _set_summary(d / "penhmain-res.dat", 2000.0, 40.0, 30.0)  # con TIME de 0,5 s de verdad, ya habría acabado
    st = ResultsFolder(d).state()
    assert st.status == "simulando", st.text


# -- 12. fuentes radiactivas: desintegraciones, no partículas --------------------------------------------------------

def test_radioactive_source_counts_decays(tmp_path):
    from argus.results.folder import ResultsFolder, aggregate_state
    from argus.results.summary import read_summary
    from argus.ui.results.summary_view import summary_html

    d = tmp_path / "esfera"
    shutil.copytree(RES / "esfera", d)  # SKPAR 0 con SRNUCL Co-60.nuc
    st = ResultsFolder(d).state()
    assert "3000 desintegraciones de 3000" in st.text and "partícula" not in st.text, st.text
    assert st.status == "terminada" and st.decays
    html = summary_html(read_summary(d / "penhmain-res.dat"), st)
    assert "desintegraciones simuladas" in html and "desintegraciones/s" in html
    assert "partículas simuladas" not in html and "por primaria" not in html
    # Varias ejecuciones (en paralelo) de la misma fuente: también en el estado de todas juntas.
    par = tmp_path / "par"
    for k in (1, 2):
        shutil.copytree(RES / "esfera", par / f"run-{k:02d}")
    seeds = iter(("RSEED  1 2", "RSEED  3 4"))
    for k in (1, 2):
        p = par / f"run-{k:02d}" / "esfera.in"
        lines = [ln for ln in p.read_text(encoding="latin-1").splitlines() if not ln.startswith("RSEED")]
        lines.insert(-1, next(seeds))
        p.write_text("\n".join(lines) + "\n", encoding="latin-1")
    agg = aggregate_state(ResultsFolder(par))
    assert "6000 desintegraciones" in agg.text, agg.text
    # Una fuente de partículas sigue contando partículas.
    st2 = ResultsFolder(RES / "caja").state()
    assert "20\u202f000 partículas" in st2.text and not st2.decays, st2.text


# -- 4. volver a simular con menos intervalos en la misma carpeta ------------------------------------------------------

def _fewer_rows(path: Path, n: int) -> None:
    """La curva de una simulación nueva con ``n`` intervalos (penhmain reescribe el fichero entero)."""
    lines = path.read_text(encoding="latin-1").splitlines()
    head = [ln for ln in lines if ln.strip().startswith("#")]
    rows = [f"{0.25 + 0.5 * i:14.6E}{1000.0 * (i + 1):14.6E}{30.0:10.2E}" for i in range(n)]
    path.write_text("\n".join(head + rows) + "\n", encoding="latin-1")


def test_a_new_run_with_fewer_bins_is_shown(tmp_path):
    from argus.results.folder import ResultsFolder
    from argus.results.readers import IncompleteFile
    from argus.results.store import ResultsStore

    d = tmp_path / "caja"
    shutil.copytree(RES / "caja", d)
    _age_all(d)
    st = ResultsStore()
    f = ResultsFolder(d)
    assert len(st.get(f, "depth-dose").x) == 25
    # penhmain vuelca la simulación nueva: la curva (10 intervalos) y, lo último, el resumen.
    _fewer_rows(d / "depth-dose.dat", 10)
    res = d / "penhmain-res.dat"
    res.write_bytes(res.read_bytes())
    f.scan()
    assert len(st.get(f, "depth-dose").x) == 10
    # Mientras penhmain vuelve a escribir la curva (el resumen aún es del volcado anterior), se espera.
    t = time.time()
    os.utime(res, (t - 60, t - 60))
    _fewer_rows(d / "depth-dose.dat", 5)
    f.scan()
    with pytest.raises(IncompleteFile):
        st.get(f, "depth-dose")


def test_fewer_points_that_do_not_change_are_not_being_written(tmp_path, monkeypatch):
    from argus.results import store as store_mod
    from argus.results.readers import IncompleteFile
    from argus.results.store import ResultsStore

    d = tmp_path / "caja"
    shutil.copytree(RES / "caja", d)
    _age_all(d)
    st = ResultsStore()
    dd = d / "depth-dose.dat"
    assert len(st.load(dd).x) == 25
    _fewer_rows(dd, 10)  # sin resumen que lo diga (otro programa, o el resumen no se ha vuelto a escribir)
    with pytest.raises(IncompleteFile):
        st.load(dd)
    monkeypatch.setattr(store_mod, "STABLE_S", 0.0, raising=False)
    assert len(st.load(dd).x) == 10  # el mismo fichero, sin cambiar: ya está escrito


# -- 3. datos medidos: absolutos, no por partícula --------------------------------------------------------------------

def _measured_gy():
    from argus.results.data import Curves, Series

    return Curves("measured", "medidas", np.array([0.5, 1.5, 2.5]), "z (cm)", "cm", "Dosis (Gy)", "",
                  [Series("medida", np.array([2.0, 1.9, 1.8]), np.array([0.02, 0.02, 0.02]))], histogram=False)


def test_measured_data_are_not_multiplied_by_the_number_of_particles():
    from argus.results.display import DisplayOptions, show_curves
    from argus.results.units import Normalization

    m = _measured_gy()
    for norm in (Normalization("count", count=1e9), Normalization("activity", activity=1e6, duration=60.0)):
        s = show_curves(m, DisplayOptions(dose_unit="Gy", norm=norm)).series[0]
        assert s.y.tolist() == [2.0, 1.9, 1.8] and s.err.tolist() == [0.02, 0.02, 0.02]
    # Las normalizaciones relativas sí (para comparar formas).
    s = show_curves(m, DisplayOptions(norm=Normalization("max"))).series[0]
    assert s.y[0] == pytest.approx(100.0) and s.y[2] == pytest.approx(90.0)


# -- 5. panel «Espectro»: canales mucho más anchos que la cola del PIPS ------------------------------------------------

def test_pips_tail_with_wide_channels_does_not_overflow():
    from argus.results import spectro as sp

    edges = np.arange(0.0, 6.0e7 + 1.0, 4.0e6)  # intervalos de 4 MeV (727 veces la E0 de 5,5 keV)
    y = np.ones(len(edges) - 1) / (edges[-1] - edges[0])
    out = sp.convolve(edges, y, 0.01 * y, sp.DETECTORS["pips"][1], e0=sp.DETECTORS["pips"][2])
    # (lo que la cola saca por debajo del primer canal, E0/ΔE de su contenido, se pierde, como en mcdet)
    assert np.isfinite(out.y).all() and float(np.sum(out.y * np.diff(out.edges))) == pytest.approx(1.0, rel=1e-3)
    out = sp.convolve(edges, y, None, sp.DETECTORS["pips"][1], e0=5.5e3, channel=5e6)
    assert np.isfinite(out.y).all()


def test_exponential_tail_is_the_one_of_mcdet():
    from argus.results.spectro import _exponential_tail

    rng = np.random.default_rng(3)
    p = rng.random(40)
    var = 0.01 * rng.random(40)
    for de, e0 in ((1.0e3, 5.5e3), (5.5e3, 5.5e3), (2.0e4, 5.5e3), (10.0, 5.5e3)):
        # La fórmula de mcdet tal cual (sin desbordar con estos canales).
        r = math.exp(-de / e0)
        fct = (e0 / de) * (math.exp(de / e0) - 1.0) * (1.0 - r)
        fctd = 1.0 - (e0 / de) * (1.0 - r)
        tail = np.array([sum(p[j] * r ** (j - i) for j in range(i + 1, 40)) for i in range(40)])
        tail_v = np.array([sum(var[j] * r ** (2 * (j - i)) for j in range(i + 1, 40)) for i in range(40)])
        c, v = _exponential_tail(p, var, de, e0)
        np.testing.assert_allclose(c, p * fctd + fct * tail, rtol=1e-9, atol=1e-14)
        np.testing.assert_allclose(v, var * fctd ** 2 + fct ** 2 * tail_v, rtol=1e-9, atol=1e-16)
    c, v = _exponential_tail(p, var, 4.0e6, 5.5e3)  # mcdet se desbordaría
    x = 4.0e6 / 5.5e3  # sale del primer canal (hacia abajo) la fracción 1/x de su contenido; lo demás se conserva
    assert np.isfinite(c).all() and np.isfinite(v).all()
    assert float(c.sum()) == pytest.approx(float(p.sum()) - p[0] / x, rel=1e-9)


# -- 6. «Pico de Bragg»: lo distal, desde el final y en el sentido del haz ---------------------------------------------

def test_bragg_distal_ranges_on_a_noisy_sobp_plateau():
    from argus.results import metrics as mt

    z = np.arange(0.05, 10, 0.1)
    true = np.where(z < 2, 0.6 + 0.2 * z, 1.0)
    true = np.where(z < 7, true, np.exp(-((z - 7) / 0.4) ** 2))
    truth = {p: mt.crossing(z, true, p / 100.0, "right", int(np.argmax(z >= 6.9))) for p in (90, 80, 50)}
    rng = np.random.default_rng(7)
    bad = {0.02: 0, 0.03: 0}
    for noise in bad:
        for _ in range(100):
            y = true * (1 + noise * rng.standard_normal(len(z)))  # antes, R90 mal en el 6 % (2 %) y el 78 % (3 %)
            ms = {m.key: m.value for m in mt.compute("bragg", z, y, resamples=1)}
            for p in (90, 80, 50):
                if ms[f"R{p}"] is None or abs(ms[f"R{p}"] - truth[p]) > 0.3:
                    bad[noise] += 1
    assert bad[0.02] == 0 and bad[0.03] <= 3, bad


def test_bragg_peak_of_a_beam_towards_minus_z():
    from argus.results import metrics as mt

    depth = np.arange(0.05, 15, 0.1)
    y = np.where(depth < 10, 0.3 + 0.7 * np.exp(-0.5 * ((depth - 10) / 1.5) ** 2),
                 np.exp(-0.5 * ((depth - 10) / 0.4) ** 2))
    plus = {m.key: m.value for m in mt.compute("bragg", depth, y)}
    z = 15.0 - depth[::-1]  # el mismo pico con la fuente arriba: z creciente, entrada a la derecha
    minus = {m.key: m.value for m in mt.compute("bragg", z, y[::-1])}
    for p in (90, 80, 50):
        assert minus[f"R{p}"] == pytest.approx(15.0 - plus[f"R{p}"], abs=1e-6)
    for k in ("fall8020", "fall9010", "width80", "peak_entrance"):
        assert minus[k] == pytest.approx(plus[k], rel=1e-6), k  # antes, pico/entrada = 1,6e32
    assert 3.0 < plus["peak_entrance"] < 3.6


# -- 7. psource.dat con un intervalo de anchura cero (la energía máxima repetida del SOBP) -----------------------------

# El psource.dat que escribió penhmain con los SPECTR del caso 2 de Rafael (100 MeV repetido: 26,9 % exactamente a
# 100 MeV; FACT = 1e15 en el último intervalo).
PSOURCE_SOBP = """\
 #  Results from PENHMAIN.
 #  Source energy spectrum (histogram).
 #  1st column: E (eV). 2nd column: spectrum (1/eV).
 #  3rd and 4th columns: simul. pdf limits (3SD, 1/eV).
  9.122706E+07  0.000000E+00  0.000000E+00  0.000000E+00
  9.122706E+07  2.377441E-08  1.562446E-08  3.453778E-08
  9.205105E+07  2.377441E-08  1.562446E-08  3.453778E-08
  9.205105E+07  4.992320E-08  3.647357E-08  6.291315E-08
  9.286941E+07  4.992320E-08  3.647357E-08  6.291315E-08
  9.286941E+07  5.340417E-08  3.959141E-08  6.703106E-08
  9.368224E+07  5.340417E-08  3.959141E-08  6.703106E-08
  9.368224E+07  5.753326E-08  4.531142E-08  7.440820E-08
  9.448969E+07  5.753326E-08  4.531142E-08  7.440820E-08
  9.448969E+07  6.254241E-08  5.040409E-08  8.090740E-08
  9.529185E+07  6.254241E-08  5.040409E-08  8.090740E-08
  9.529185E+07  6.879819E-08  5.633466E-08  8.837509E-08
  9.608885E+07  6.879819E-08  5.633466E-08  8.837509E-08
  9.608885E+07  7.692189E-08  4.955727E-08  8.008223E-08
  9.688079E+07  7.692189E-08  4.955727E-08  8.008223E-08
  9.688079E+07  8.807295E-08  6.085411E-08  9.416769E-08
  9.766777E+07  8.807295E-08  6.085411E-08  9.416769E-08
  9.766777E+07  1.047472E-07  8.406618E-08  1.222073E-07
  9.844991E+07  1.047472E-07  8.406618E-08  1.222073E-07
  9.844991E+07  1.337672E-07  1.150651E-07  1.585042E-07
  9.922728E+07  1.337672E-07  1.150651E-07  1.585042E-07
  9.922728E+07  2.070101E-07  1.921456E-07  2.452725E-07
  1.000000E+08  2.070101E-07  1.921456E-07  2.452725E-07
  1.000000E+08  2.685868E+14  2.440642E+14  2.926025E+14
  1.000000E+08  2.685868E+14  2.440642E+14  2.926025E+14
  1.000000E+08  0.000000E+00  0.000000E+00  0.000000E+00
"""
# Lo que simuló penhmain (las P de SPECTR, normalizadas): energía media y fracción exactamente a 100 MeV.
SPECTR_E = np.array([91.22706166, 92.05105460, 92.86940675, 93.68224353, 94.48968567, 95.29184953, 96.08884722,
                     96.88078691, 97.66777297, 98.44990616, 99.22728381, 100.0, 100.0]) * 1e6
SPECTR_P = np.array([0.01958994804, 0.04085475497, 0.0434088735, 0.04645477557, 0.0501692578, 0.05483200094,
                     0.06091749431, 0.06931218497, 0.0819262544, 0.1039876678, 0.1599600235, 0.2685867642])


def _psource(tmp_path: Path) -> Path:
    p = tmp_path / "psource.dat"
    p.write_text(PSOURCE_SOBP, encoding="latin-1")
    _old(p)
    return p


def _true_mean_mev() -> float:
    pn = SPECTR_P / SPECTR_P.sum()
    mid = np.where(np.diff(SPECTR_E) > 0, 0.5 * (SPECTR_E[:-1] + SPECTR_E[1:]), SPECTR_E[:-1])
    return float((pn * mid).sum() / 1e6)


def test_exact_energy_of_the_source_is_a_line_with_its_probability(tmp_path):
    from argus.results import metrics as mt
    from argus.results.display import DisplayOptions, show_curves
    from argus.results.readers import read_output
    from argus.results.units import Normalization

    c = read_output(_psource(tmp_path))
    assert c.edges[-1] == c.edges[-2]  # se lee como lo escribe penhmain (es lo que se importa como espectro)
    sc = show_curves(c, DisplayOptions(), {"definido"})
    s = sc.series[0]
    assert sc.x_unit == "MeV" and "1/MeV" in sc.y_label  # antes, «1/eV» con el eje en MeV
    assert float(np.nanmax(s.y)) == pytest.approx(0.2070101)  # sin el pico de 2,7e14
    assert len(s.lines) == 1 and s.lines[0].x == pytest.approx(100.0) and s.lines[0].p == pytest.approx(0.2685868)
    xs, ys, es, ed, lines = mt.usable(s.x, s.y, s.err, s.edges, [(ln.x, ln.p * s.scale) for ln in s.lines])
    ms = {m.key: m.value for m in mt.compute("espectro", xs, ys, es, ed, lines=lines)}
    assert ms["emean"] == pytest.approx(_true_mean_mev(), abs=1e-3)  # antes, 96,89 MeV (sin el 27 % a 100 MeV)
    assert ms["area"] == pytest.approx(1.0, abs=1e-5) and ms["exact"] == pytest.approx(26.86, abs=0.01)
    # Normalizado: «% del máximo», el máximo del espectro continuo; «área 1», con la línea dentro del área.
    s = show_curves(c, DisplayOptions(norm=Normalization("max")), {"definido"}).series[0]
    assert float(np.nanmax(s.y)) == pytest.approx(100.0)
    s = show_curves(c, DisplayOptions(norm=Normalization("area")), {"definido"}).series[0]
    assert s.scale == pytest.approx(1.0, abs=1e-5) and float(np.nanmax(s.y)) == pytest.approx(0.2070101, rel=1e-4)


def test_exact_energy_in_the_spectroscopy_panel(tmp_path):
    from argus.results import spectro as sp
    from argus.results.readers import read_output

    c = read_output(_psource(tmp_path))
    s = c.get("definido")
    cv = sp.convolve(c.edges, s.y, None, sp.DETECTORS["nai"][1])
    assert np.isfinite(cv.y).all() and len(cv.y) < 1000  # antes, 10 000 canales de anchura cero (NaN)
    assert float(np.sum(cv.y * np.diff(cv.edges))) == pytest.approx(1.0, abs=1e-5)
    w = sp.window(c.edges, s.y, None, 99.0e6, 100.5e6, subtract=False)
    cont = (99.22728e6 - 99.0e6) * 1.337672e-7 + (100.0e6 - 99.22728e6) * 2.070101e-7
    assert w.gross == pytest.approx(cont + 0.2685868, rel=1e-4)  # con la energía exacta
    w2 = sp.window(c.edges, s.y, None, 95.0e6, 99.0e6)  # el fondo no se hace con la línea
    assert abs(w2.background) < 1.0
    val, _sig, out = sp.conversion(c.edges, s.y, None, np.array([1e6, 2e8]), np.array([1.0, 1.0]))
    assert val == pytest.approx(1.0, abs=1e-5) and out == 0.0


# -- 9. CSV de una hoja de cálculo en español --------------------------------------------------------------------------

def test_spanish_csv_with_quoted_decimal_commas():
    from argus.results.measured import read_table_text

    t = read_table_text('"z (mm)","Dosis (%)"\n"0,5","50,1"\n"1,5","60,2"\n"2,5","70,3"\n')
    assert t.columns == ["z (mm)", "Dosis (%)"] and t.data.tolist() == [[0.5, 50.1], [1.5, 60.2], [2.5, 70.3]]
    t = read_table_text('"z","D"\n"0,5","50"\n"1","60,2"\n"2","70"\n')  # comas solo en algunos números
    assert t.data.tolist() == [[0.5, 50.0], [1.0, 60.2], [2.0, 70.0]]
    t = read_table_text('"z","D"\n"0.5","50.1"\n')
    assert t.data.tolist() == [[0.5, 50.1]]


# -- 10. «% del valor en un punto» que no se puede aplicar -------------------------------------------------------------

def test_point_normalisation_outside_the_curve_or_at_zero_says_so():
    from argus.results.display import DisplayOptions, show_curves
    from argus.results.readers import read_output
    from argus.results.units import Normalization

    c = read_output(RES / "caja" / "spc-enddet-01.dat")
    raw = show_curves(c, DisplayOptions())
    xs = raw.series[0].x
    zero_at = float(xs[np.nonzero(c.series[0].y == 0)[0][0]])
    for pt, why in ((1e9, "fuera de la curva"), (-5.0, "fuera de la curva"), (zero_at, "vale 0")):
        sc = show_curves(c, DisplayOptions(norm=Normalization("point", point=pt)))
        assert "%" not in sc.y_label and sc.y_label == raw.y_label, sc.y_label
        assert why in sc.note
        np.testing.assert_allclose(sc.series[0].y, raw.series[0].y)
    # Dentro de la curva, donde no vale 0: en %.
    i = int(np.argmax(raw.series[0].y))
    sc = show_curves(c, DisplayOptions(norm=Normalization("point", point=float(xs[i]))))
    assert "%" in sc.y_label and not sc.note and sc.series[0].y[i] == pytest.approx(100.0)
