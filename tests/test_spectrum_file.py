"""Espectro de la fuente desde un fichero (penhan.spectrum_file): formato de ARGUS, tablas y salidas de penhmain."""

from __future__ import annotations

import numpy as np
import pytest

from argus.penhan import spectrum_file as sf
from argus.penhan.sections import analyze_spectrum
from conftest import CORPUS

RES = CORPUS / "resultados"


def _rows(text: str, particle: int | None = None, **opts):
    data = sf.parse_table_text(text, "t.txt")
    opt = sf.default_options(data, particle)
    for k, v in opts.items():
        setattr(opt, k, v)
    return data, opt, sf.convert(data, opt)


def test_argus_format_round_trip():
    rows = [(90e6, 0.1), (95e6, 0.3), (100e6, -1.0)]
    text = sf.format_spectrum(rows, False, "MeV", "SOBP de prueba")
    assert "# unidad: MeV" in text and "# tipo: continuo" in text and "\n90\t0.1\n" in text
    data, opt, conv = _rows(text)
    assert data.what == "Espectro de ARGUS" and (opt.unit, opt.kind, opt.position, opt.value) == \
        ("MeV", "continuous", "edge", "probability")
    assert conv.error is None and not conv.lines
    assert conv.rows == [(90e6, pytest.approx(0.25)), (95e6, pytest.approx(0.75)), (100e6, -1.0)]
    lines = [(1.1732e6, 0.9985), (1.3325e6, 0.9998)]
    data, opt, conv = _rows(sf.format_spectrum(lines, True, "keV"))
    assert opt.kind == "lines" and opt.unit == "keV" and conv.lines
    assert [e for e, _ in conv.rows] == pytest.approx([1.1732e6, 1.3325e6])


def test_spreadsheet_tables():
    # Hoja de cálculo en español: «;» y coma decimal; la última fila sin probabilidad es la energía máxima.
    data, opt, conv = _rows("90;0,1\n95;0,3\n100;\n", particle=4)
    assert opt.unit == "MeV" and opt.position == "edge"
    assert conv.rows == [(90e6, pytest.approx(0.25)), (95e6, pytest.approx(0.75)), (100e6, -1.0)]
    # Con títulos: la unidad sale de ellos; «E/keV» es la energía en keV (no una densidad).
    data, opt, conv = _rows("E/keV\tP\n10\t1\n20\t3\n30\t-1\n", particle=2)
    assert opt.unit == "keV" and opt.value == "probability" and conv.rows[-1] == (30e3, -1.0)
    data, opt, _ = _rows("Energía (MeV)  peso\n1 2\n2 2\n")
    assert opt.unit == "MeV"
    # Sin unidad: protones en MeV; fotones por encima de 20 en keV; números grandes, eV.
    assert _rows("100 1\n150 1\n", particle=4)[1].unit == "MeV"
    assert _rows("100 1\n150 1\n", particle=2)[1].unit == "keV"
    assert _rows("1e8 1\n1.5e8 1\n")[1].unit == "eV"
    # La última fila con valor: su intervalo mide lo mismo que el anterior (se dice).
    _, _, conv = _rows("1 1\n2 1\n3 2\n", particle=4)
    assert conv.rows[-1] == (4e6, -1.0) and any("mide lo mismo" in n for n in conv.notes)


def test_density_at_bin_centers():
    # Como lo exportan los programas de espectros de rayos X: centros de 0,5 keV y fluencia por keV.
    centers = 10.25 + 0.5 * np.arange(180)
    dens = np.exp(-((centers - 40.0) / 10.0) ** 2)
    body = "\n".join(f"{c}\t{d:.6e}" for c, d in zip(centers, dens))
    text = "# Energy [keV]\tFluence [photons cm^-2 keV^-1]\n" + body
    data, opt, conv = _rows(text, particle=2)
    assert (opt.unit, opt.position, opt.value) == ("keV", "center", "density")
    assert conv.rows[0][0] == pytest.approx(10.0e3) and conv.rows[-1] == (pytest.approx(100.0e3), -1.0)
    p = np.array([q for _, q in conv.rows[:-1]])
    assert p.sum() == pytest.approx(1.0) and p.argmax() == np.argmax(dens)
    # Los centros irregulares tienen sus bordes a mitad de camino.
    assert list(sf.edges_from_centers(np.array([1.0, 2.0, 4.0, 8.5]))) == [0.5, 1.5, 3.0, 6.25, 10.75]


def test_three_columns_with_both_edges():
    data, opt, conv = _rows("Emin Emax P\n1 2 5\n2 3 10\n4 5 5\n", particle=4)
    assert opt.position == "edges"
    assert conv.rows == [(1e6, pytest.approx(0.25)), (2e6, pytest.approx(0.5)), (3e6, 0.0), (4e6, pytest.approx(0.25)),
                         (5e6, -1.0)]
    assert any("hueco" in n for n in conv.notes)
    # Sin títulos también se reconocen por los números; «E P σ» no.
    assert _rows("1 2 5\n2 3 10\n", particle=4)[1].position == "edges"
    assert _rows("90 0.1 0.01\n95 0.3 0.02\n100 -1 0\n", particle=4)[1].position == "edge"
    _, _, bad = _rows("1 3 5\n2 4 5\n", particle=4, position="edges")
    assert bad.error and "solapan" in bad.error


def test_spectrum_lines_of_another_input():
    text = ("TITLE  Un caso\nSKPAR  4\nSPECTR 1.0E8 0.2686   [la energía máxima repetida]\nSPECTR 9.0E7 0.5\n"
            "SPECTR 1.0E8 -1\nSPOSIT 0 0 0\nSLINES 5 5\n")
    data, opt, conv = _rows(text)
    assert data.what.startswith("Líneas SPECTR") and opt.unit == "eV" and not conv.lines
    assert any("no las admite juntas" in n for n in conv.notes)  # las SLINES de después no cuentan
    # Como penhmain (SORT2 conserva el orden de las energías iguales): 26,9 % exactamente a 100 MeV.
    info = analyze_spectrum(conv.rows, False)
    ref = analyze_spectrum([(1.0e8, 0.2686), (9.0e7, 0.5), (1.0e8, -1.0)], False)
    assert [b[:2] for b in info.bins] == [b[:2] for b in ref.bins]
    assert [b[2] for b in info.bins] == pytest.approx([b[2] for b in ref.bins])


def test_lines():
    data, opt, conv = _rows("# tipo: líneas\n# unidad: keV\n1173.2 0.9985\n1332.5 0.9998\n1332.5 0.0002\n")
    assert conv.lines and len(conv.rows) == 2 and conv.rows[1][1] == pytest.approx(1.0 / 1.9985)
    assert any("repetidas" in n for n in conv.notes)
    e = np.linspace(1.0, 300.0, 300)
    text = "# tipo: líneas\n# unidad: keV\n" + "\n".join(f"{a} {1 + (i % 7)}" for i, a in enumerate(e))
    data, opt, conv = _rows(text)
    assert len(conv.rows) == 250 and sum(p for _, p in conv.rows) == pytest.approx(1.0)
    w = np.array([1 + (i % 7) for i in range(300)], dtype=float)
    mean = float((e * w).sum() / w.sum()) * 1e3
    assert sum(a * p for a, p in conv.rows) == pytest.approx(mean, rel=1e-9)  # juntar líneas conserva la media


def test_too_many_bins_are_grouped_keeping_the_peak():
    e = np.linspace(1.0, 101.0, 1001)
    p = np.exp(-((e[:-1] - 80.0) / 0.5) ** 2) + 1e-4
    text = "# unidad: MeV\n" + "\n".join(f"{a} {b}" for a, b in zip(e[:-1], p)) + f"\n{e[-1]} -1\n"
    data, opt, conv = _rows(text)
    assert len(conv.rows) == 250 and conv.rows[-1] == (pytest.approx(101e6), -1.0)
    lo = np.array([a for a, _ in conv.rows])
    q = np.array([b for _, b in conv.rows[:-1]])
    assert q.sum() == pytest.approx(1.0)
    # La probabilidad de cada zona se conserva y el pico sigue con su detalle (intervalos de 0,1 MeV).
    zone = (lo[:-1] >= 79e6) & (lo[1:] <= 81e6)
    ref = p[(e[:-1] >= 79.0) & (e[1:] <= 81.0 + 1e-9)].sum() / p.sum()
    assert q[zone].sum() == pytest.approx(ref, rel=1e-3)
    widths = np.diff(lo)[zone]
    assert widths.min() == pytest.approx(0.1e6)
    assert any("se han juntado" in n for n in conv.notes)


def test_penhmain_spectra(tmp_path):
    data = sf.read_spectrum_file(RES / "caja" / "spc-impdet-01.dat")
    assert data.from_penhmain and "detector de impacto" in data.what
    names = [n for n, _p, _y in data.series]
    opt = sf.default_options(data, particle=2)
    assert names[opt.series] == "fotones"
    conv = sf.convert(data, opt)
    assert conv.error is None and conv.particle == 2 and not conv.lines
    i = opt.series
    w = np.diff(data.edges)
    expect = np.clip(data.series[i][2], 0, None) * w
    nz = np.nonzero(expect > 0)[0]
    expect = expect[nz[0]:nz[-1] + 1] / expect.sum()
    assert [b for _, b in conv.rows[:-1]] == pytest.approx(list(expect))
    assert conv.rows[0][0] == pytest.approx(data.edges[nz[0]])
    assert conv.rows[-1][0] == pytest.approx(data.edges[nz[-1] + 1])
    # La partícula de la fuente elige la serie; si no tiene nada, la total o la que más tiene.
    assert names[sf.default_options(data, particle=4).series] in ("total", "fotones")
    up = sf.read_spectrum_file(RES / "caja" / "energy-up.dat")
    assert sf.convert(up, sf.default_options(up, 1)).error is None
    # Un resultado que no es un espectro de energía no vale.
    with pytest.raises(ValueError, match="no un espectro"):
        sf.read_spectrum_file(RES / "caja" / "depth-dose.dat")


def test_errors_are_explained(tmp_path):
    with pytest.raises(ValueError, match="no tiene filas"):
        sf.parse_table_text("Energía y probabilidad\nnada\n")
    assert "cero" in _rows("1 0\n2 0\n3 -1\n", particle=4)[2].error
    assert "una sola energía" in _rows("5 1\n", particle=4)[2].error
    assert "repetidas" in _rows("1 1\n1 2\n", particle=4, position="center", value="probability")[2].error
    raw = tmp_path / "hoja.xlsx"
    raw.write_bytes(b"PK\x03\x04\x00\x00binario")
    with pytest.raises(ValueError, match="no es un fichero de texto"):
        sf.read_spectrum_file(raw)
    # UTF-16 de Excel («Texto Unicode»).
    f = tmp_path / "unicode.txt"
    f.write_bytes("E (MeV)\tP\r\n90\t0,1\r\n95\t0,3\r\n100\t\r\n".encode("utf-16"))
    data = sf.read_spectrum_file(f)
    assert sf.convert(data, sf.default_options(data, 4)).rows[-1] == (100e6, -1.0)


def test_the_documented_example_with_its_explanations():
    # El ejemplo de la ayuda, copiado tal cual (con las explicaciones al lado de cada clave).
    text = """# Espectro de la fuente (ARGUS)
# unidad: MeV            eV, keV, MeV o GeV
# tipo: continuo         continuo (SPECTR) o líneas (SLINES)
# energía: borde         borde inferior de cada intervalo, centro o bordes
# valor: probabilidad    probabilidad, densidad (por unidad de energía) o cuentas
90      0.10
95      0.25
100     -1               (continuo: la última fila es la energía máxima)
"""
    data, opt, conv = _rows(text)
    assert (data.unit, data.kind, data.position, data.value) == ("MeV", "continuous", "edge", "probability")
    assert conv.rows == [(90e6, pytest.approx(0.10 / 0.35)), (95e6, pytest.approx(0.25 / 0.35)), (100e6, -1.0)]
    data, _o, _c = _rows("# tipo: líneas\n# energía: centro\n# valor: densidad\n1 1\n")
    assert (data.kind, data.position, data.value) == ("lines", "center", "density")
