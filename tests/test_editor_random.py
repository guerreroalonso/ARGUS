"""Cadenas de ediciones al azar comprobadas con PENGEOM real (tablas y LOCATE idénticos)."""

import pytest

from editor_fuzz import run


@pytest.mark.oracle
def test_random_edit_chains_are_what_pengeom_reads(need_probe):
    stats = run(10, steps=6, seed=2024, verbose=False)
    assert stats["chains"] == 10 and stats["edits"] >= 40
    assert len(stats["ops"]) >= 6  # se han probado casi todas las operaciones


@pytest.mark.oracle
def test_random_edit_chains_with_deletions_keep_the_rest(need_probe):
    """Con eliminaciones (cuerpos, módulos vacíos, clones, limitantes, la caja CT), superficies quitadas o dadas la
    vuelta y geometrías vaciadas hasta el final: lo que no se edita no cambia (LOCATE antes y después), lo eliminado
    pasa a lo que lo alojaba y PENGEOM lee el resultado igual que ARGUS."""
    stats = run(6, steps=5, seed=31, verbose=False, deletions=True, drain_every=3,
                names=["clones", "anidados", "shared", "ctbox", "axes", "cubo-esfera"])
    assert stats["chains"] == 6 and not stats["problems"]
    assert stats["ops"].get("eliminar", 0) >= 5
