"""Textos de la revisión 0.13.1: números como en el resto de la interfaz (coma decimal, millares, «2e9»), plurales
de verdad en vez de «fichero(s)», siglas que no se pasan a minúsculas y lo que falta de simular con TIME."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from argus.core.numfmt import THIN_NBSP, fmt_count, fmt_energy, lower_first
from argus.penhan import sections as sc
from argus.penhan.inputfile import parse_in_text
from argus.results.folder import ResultsFolder
from argus.results.summary import read_summary

RES = Path(__file__).parent / "corpus" / "resultados"


def test_counts_and_energies():
    assert fmt_count(20000) == f"20{THIN_NBSP}000" and fmt_count(1500) == "1500" and fmt_count(2e9) == "2e9"
    assert fmt_count(1.5e10) == "1,5e10" and fmt_count(0) == "0" and fmt_count(2.5) == "2,5"
    assert fmt_energy(661657.0) == "661,657 keV" and fmt_energy(50.0) == "50 eV" and fmt_energy(1.4e8) == "140 MeV"
    assert lower_first("Datos ENDF") == "datos ENDF" and lower_first("TAC (vóxeles)") == "TAC (vóxeles)"
    assert lower_first("Material") == "material"


def test_check_messages_use_the_decimal_comma():
    """«C1 = 0.25 está fuera de [0, 0,2]: penhmain usa 0.2» mezclaba punto y coma en la misma frase."""
    from argus.penhan.validate import validate_case

    text = (RES / "caja" / "caja.in").read_text("latin-1").replace(
        "MSIMPA 1e4 1e3 1e4 0.05 0.05 1e4 1e3", "MSIMPA 1e4 1e3 1e4 0.25 0.05 1e4 1e3", 1)
    doc = parse_in_text(text, str(RES / "caja" / "caja.in"))
    msgs = [d.message for d in validate_case(doc, None, RES / "caja")]
    assert any("C1 = 0,25 está fuera de [0, 0,2]: penhmain usa 0,2." in m for m in msgs), msgs


def test_real_plurals():
    from argus.penhan import simfolder
    from argus.penhan.spectrum_file import convert, default_options, parse_table_text

    plan = simfolder.SimPlan(dest=Path("/tmp/x"))
    assert "0 ficheros copiados, 0 ya estaban." in simfolder.summary(plan)
    item = simfolder.Item("ct", "tac.ct", None, required=True, status="copied")
    plan.items.append(item)
    plan.items.append(simfolder.Item("geo", "a.geo", None, required=True, status="same"))
    assert "1 fichero copiado, 1 ya estaba." in simfolder.summary(plan)
    data = parse_table_text("1e6 1\n2e6 -1\n3e6 1\n")
    conv = convert(data, default_options(data))
    assert not any("(s)" in n or "(es)" in n for n in conv.notes), conv.notes


def test_what_is_left_counts_the_time_limit(tmp_path):
    """Con TIME, lo que falta es lo que queda de ese tiempo si acaba antes que NSIMSH (antes, «faltan unos 3 días»
    con TIME de una hora)."""
    d = tmp_path / "caso"
    shutil.copytree(RES / "caja", d)
    (d / "penhmain.dat").unlink()
    (d / "run.log").unlink()
    s = read_summary(d / "penhmain-res.dat")
    text = (d / "caja.in").read_text("latin-1")
    lines = [ln for ln in text.splitlines() if not ln.startswith(("NSIMSH", "TIME", "DUMPP"))]
    lines.append("NSIMSH 1e9")
    lines.append(f"TIME   {s.time_total + 120:.6g}")
    (d / "caja.in").write_text("\n".join(lines) + "\n", encoding="latin-1")
    now = time.time()
    for f in d.iterdir():
        if f.is_file():
            f.touch()  # recién escrito: sigue simulando
    st = ResultsFolder(d).state(now)
    assert st.status == "simulando" and st.eta == pytest.approx(120, abs=1.0), st.text
    assert "faltan unos 2 min" in st.text and "de 1e9" in st.text and "e+" not in st.text


def test_dose_status_texts():
    from argus.penhan import goals as gl

    doc = parse_in_text("TITLE  t\nGRIDZ  0 10.5 100\nGRIDR  2.5 25\n", "t.in")
    assert gl.status(doc, "dosis-cilindro") == "r hasta 2,5 cm, z de 0 a 10,5 cm"
    assert sc.dose_grid(doc).kind == "cylinder"
