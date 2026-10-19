"""Undécima revisión (0.17.3): los vóxeles de la matriz de dosis (lo que no necesita la ventana).

Cada prueba falla con el código de 0.17.2."""

from __future__ import annotations

import numpy as np
import pytest

from argus.penhan import sections as sc


@pytest.mark.parametrize("size", [0.0098, 0.005, 0.001])
def test_a_tiny_voxel_keeps_the_box_and_says_so(size):
    """Con «impar» (lo de siempre), un vóxel más de 1010 veces menor que el eje dejaba la caja en 101·Δ alrededor del
    centro sin avisar (en una caja de 10 cm, Δ = 0,0098 cm la dejaba en 0,99 cm). Como con cualquier otro «demasiados
    intervalos»: los extremos se quedan, 101 intervalos, y se dice."""
    lo, hi, n, note = sc.bins_for_voxel(10.0, 20.0, size, 101, odd=True)
    assert (lo, hi, n) == (10.0, 20.0, 101)
    assert note and "Como mucho 101 intervalos" in note


def test_the_usual_voxels_are_as_before():
    assert sc.bins_for_voxel(10.0, 20.0, 0.5, 101, odd=True)[:3] == (9.75, 20.25, 21)
    lo, hi, n, note = sc.bins_for_voxel(10.0, 20.0, 0.05, 101, odd=True)  # (200 intervalos: ya se decía)
    assert (lo, hi, n) == (10.0, 20.0, 101) and note


def test_stretching_a_grid_of_tiny_voxels_keeps_the_length_penhmain_needs():
    """Vóxeles de 4,95e-7 cm (1 µm en 101) estirados hasta uno: el eje medía menos de 1e-6 cm y set_dose_grid lo
    rechazaba a mitad del arrastre (el aviso de «error inesperado» en cada movimiento)."""
    a, b, m = sc.stretch_axis(10.0, 10.00005, 101, 10.0, "hi", 101)
    assert b - a >= sc.GRID_MIN_LENGTH and m >= 1
    a, b, m = sc.stretch_axis(10.0, 10.00005, 101, 10.00005, "lo", 101)
    assert b - a >= sc.GRID_MIN_LENGTH
    r, n = sc.stretch_radius(5e-5, 101, 0.0)
    assert r >= sc.GRID_MIN_LENGTH and n >= 1
    doc = _caja_doc()
    sc.set_dose_grid(doc, sc.DoseGrid((10.0, 10.0 + (b - a), m), (-5.0, 5.0, 11), (0.0, 5.0, 25)))  # (no lanza)
    sc.set_dose_grid(doc, sc.DoseGrid(None, None, (0.0, 5.0, 25), (r, n)))


def _caja_doc():
    from argus.penhan.inputfile import parse_in_file
    from conftest import CORPUS

    return parse_in_file(CORPUS / "resultados" / "caja" / "caja.in")


_HEAD = (" #  Results from PENHMAIN. Depth-dose distribution.\n #  (integrated over X and Y within the volume of the "
         "dose box).\n #  1st column: z coordinate (cm).\n #  2nd column: depth-dose (eV/(g/cm^2)).\n #  3rd column: "
         "statistical uncertainty (3 sigma).\n #\n")


def test_a_dose_curve_with_one_interval_has_its_real_edges(tmp_path):
    """«GRIDZ 2 4 1»: con un solo centro (3 cm), la curva se dibujaba de 1,5 a 4,5 cm (la anchura, inventada); los
    bordes son los de la rejilla de la misma carpeta (gnu-dose.dat)."""
    from argus.results.readers import read_output

    (tmp_path / "gnu-dose.dat").write_text("XL= 1.000000E+01\nYL=-5.000000E+00\nZL= 2.000000E+00\n"
                                           "DX= 1.000000E+01\nDY= 9.090909E-01\nDZ= 2.000000E+00\n"
                                           "Gy= 6.241509074E15    # eV/g\n")
    (tmp_path / "depth-dose.dat").write_text(_HEAD + "  3.000000E+00  1.512074E+04  3.08E+03\n")
    c = read_output(tmp_path / "depth-dose.dat")
    assert np.allclose(c.edges, [2.0, 4.0])
    x_head = (" #  Results from PENHMAIN.\n #  Dose distribution along the central X axis.\n #  1st column: x (cm).\n"
              " #  2nd column: dose (eV/g).\n #  3rd column: statistical uncertainty (3 sigma).\n")
    (tmp_path / "x-dose.dat").write_text(x_head + "  1.500000E+01  1.0E+04  1.0E+03\n")
    c = read_output(tmp_path / "x-dose.dat")
    assert c.histogram and np.allclose(c.edges, [10.0, 20.0])


def test_without_the_grid_files_a_single_interval_is_not_given_invented_edges(tmp_path):
    from argus.results.readers import read_output

    (tmp_path / "depth-dose.dat").write_text(_HEAD + "  3.000000E+00  1.512074E+04  3.08E+03\n")
    c = read_output(tmp_path / "depth-dose.dat")
    assert not c.histogram  # (un punto: la anchura no se sabe)


def test_the_dose_card_counts_the_cells_of_a_cylinder():
    """«cilindro, 3200 anillos» con 40 anillos × 80 capas: son celdas (el panel ya decía «3200 celdas»)."""
    pytest.importorskip("PySide6")
    from argus.ui.outputs_dialog import present

    doc = _caja_doc()
    sc.set_dose_grid(doc, sc.DoseGrid(None, None, (0.0, 8.0, 80), (4.0, 40)))
    text = present(doc, "dosis")
    assert "3200 celdas" in text.replace(".", "") and "anillos" not in text
