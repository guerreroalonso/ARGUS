"""Undécima revisión (0.17.3): lo que necesita el corpus que no va en el repositorio público (aparte, para que sin él
solo se omita esto: ``tests/conftest.py`` omite el módulo entero).

Cada prueba falla con el código de 0.17.2."""

from __future__ import annotations

import numpy as np

from argus.session import open_geometry
from conftest import CORPUS


def test_the_scene_box_of_the_glass_has_its_foot():
    """La copa de PenGeomJar: la caja de la escena se quedaba en z = -0,39 (sin el tallo ni el pie, hasta -6,8): el
    deslizador de profundidad no llegaba al pie y «Nuevo .in» ponía la fuente dentro del tallo."""
    s = open_geometry(CORPUS / "pengeomjar" / "glass")
    assert s.ok
    lo, hi = s.bounds
    p = np.random.default_rng(3).uniform([-4, -4, -8], [4, 4, 10], (600000, 3))
    ib, mat = s.locator.locate(p)
    solid = p[mat > 0]
    assert solid[:, 2].min() < -6.5  # (el pie)
    assert np.all(solid.min(axis=0) >= lo) and np.all(solid.max(axis=0) <= hi)
