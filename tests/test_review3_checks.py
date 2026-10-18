"""Revisión 3 de las comprobaciones de PMRDR.

* Referencias a cuerpos resueltas como PMRDR: el número 0 y una etiqueta en
  blanco nombran el último cuerpo sin etiqueta (copias de un CLONE, la
  envolvente que añade PENGEOM...), salvo en IDBODY (IRETRN 3082).
* IFORCE con FORCER negativo en un cuerpo vacío no fuerza nada.
* La energía máxima de las ventanas de los detectores es la de PMRDR
  (positrones, espacio de fases, nucleidos).
* Nombres de fichero en blanco o imposibles de abrir (GEOMFN, IPSFN, MFNAME).
* El .in sigue a la geometría con la misma resolución (``penhan.follow``).

Las pruebas de oráculo comparan con el PMRDR real (sonda ``argus_simprobe``).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from argus.core.diagnostics import Severity
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.identity import renumber_map
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.penhan.follow import follow_geometry, references_to
from argus.penhan.inputfile import parse_in_file, parse_in_text
from argus.penhan.validate import validate_case
from conftest import CORPUS, ROOT


def max_particle_energy(doc, folder):
    from argus.penhan.simcheck import max_particle_energy as f

    return f(doc, folder)


def nuclide_max_energy(path):
    from argus.penhan.simcheck import nuclide_max_energy as f

    return f(path)



SEP = "0" * 64
END = "END      " + "0" * 55
SPHERES = CORPUS / "pengeomjar/spheres"  # 1999 cuerpos; las copias de los clones no tienen etiqueta
NUCLIDE_CASE = CORPUS / "penhan/nuclide"
PDFILES = [p for p in (ROOT.parent / "ProgramasBase/penhan/database/pdfiles",
                       Path("/home/claude/ref/SOBP_Investigacion/ProgramasBase/penhan/database/pdfiles"))
           if (p / "pdrelax.p11").is_file() and (p / "pdatconf.p14").is_file()]


def _surf(label, idx, **params):
    lines = [f"SURFACE ({label:>4})", "INDICES=(" + ",".join(f"{i:2d}" for i in idx) + ")"]
    names = {"xs": "X-SCALE", "ys": "Y-SCALE", "zs": "Z-SCALE", "dx": "X-SHIFT"}
    for k, v in params.items():
        lines.append(f"{names[k]}=({v:+.15E},   0)")
    return "\n".join(lines + [SEP]) + "\n"


def _body(label, mat, *items, kind="BODY   "):
    return f"{kind} ({label:>4})\nMATERIAL({mat:>4})\n" + "".join(items) + SEP + "\n"


def _side(ks, side=-1):
    return f"SURFACE ({ks:>4}), SIDE POINTER=({side:+d})\n"


SPHERE = (1, 1, 1, 0, -1)
# Dos esferas separadas: PENGEOM añade la envolvente (cuerpo 3, vacío y sin etiqueta).
ENC_GEO = ("Dos esferas: PENGEOM añade la envolvente\n" + SEP + "\n"
           + _surf("1", SPHERE, dx=-3.0) + _surf("2", SPHERE, dx=3.0)
           + _body("1", 1, _side(1)) + _body("2", 1, _side(2)) + END + "\n")
# Las mismas dentro de un módulo: ya no hay envolvente.
WRAPPED_GEO = ("Dos esferas en un módulo\n" + SEP + "\n"
               + _surf("1", SPHERE, dx=-3.0) + _surf("2", SPHERE, dx=3.0) + _surf("3", SPHERE, xs=10.0, ys=10.0,
                                                                                    zs=10.0)
               + _body("1", 1, _side(1)) + _body("2", 1, _side(2))
               + _body("3", 0, _side(3), "BODY    (   1)\nBODY    (   2)\n", kind="MODULE ") + END + "\n")
# Esfera con material dentro de una corteza vacía.
VOID_GEO = ("Esfera y corteza vacía\n" + SEP + "\n"
            + _surf("1", SPHERE) + _surf("2", SPHERE, xs=2.0, ys=2.0, zs=2.0)
            + _body("1", 1, _side(1)) + _body("2", 0, _side(2), "BODY    (   1)\n") + END + "\n")


def clones_geo(clones=(3,)) -> str:
    """Módulo 2 con una esfera (cuerpo 1) y sus clones (etiquetas 3 y 5), todo en el módulo 4 (sin envolvente).

    La copia de la esfera en cada clon no tiene etiqueta: con los dos clones son los cuerpos 3 y 5.
    """
    shift = "1" * 64 + "\nX-SHIFT=({:+.15E},   0)\n"
    t = ("Clones\n" + SEP + "\n" + _surf("1", (1, 0, 0, 0, -1), xs=5.0) + _surf("2", (0, 1, 0, 0, -1), ys=5.0)
         + _surf("3", (0, 0, 1, 0, -1), zs=5.0) + _surf("4", SPHERE, xs=2.0, ys=2.0, zs=2.0)
         + _surf("5", (1, 0, 0, 0, -1), xs=50.0) + _surf("6", (0, 1, 0, 0, -1), ys=50.0)
         + _surf("7", (0, 0, 1, 0, -1), zs=50.0)
         + _body("1", 2, _side(4))
         + "MODULE  (   2)\nMATERIAL(   1)\n" + _side(1) + _side(2) + _side(3) + "BODY    (   1)\n"
         + shift.format(10.0) + SEP + "\n")
    mods = "MODULE  (   2)\n"
    for lab in clones:
        t += f"CLONE   ({lab:>4})\nMODULE  (   2)\n" + shift.format(20.0 if lab == 3 else -20.0) + SEP + "\n"
        mods += f"MODULE  ({lab:>4})\n"
    return t + "MODULE  (   4)\nMATERIAL(   3)\n" + _side(5) + _side(6) + _side(7) + mods + SEP + "\n" + END + "\n"


MAT = "MFNAME water.mat                      [Material file, up to 20 chars]\nMSIMPA 1e3 1e3 1e3 0.05 0.05 1e3 1e3\n"


def enc_in(extra: str = "", sbody: str = "") -> str:
    box = "SBOX   1 1 1\n" + sbody if sbody else ""
    return ("TITLE  cero y etiquetas en blanco\nSKPAR  2\nSENERG 1.0E6\nSPOSIT -3 0 0\n" + box + MAT
            + "GEOMFN enc.geo                        [Geometry file, up to 20 chars]\n" + extra + "NSIMSH 1.0E6\n")


def spheres_in(extra: str) -> str:
    return ("TITLE  copias de clones\nSKPAR  2\nSENERG 1.0E6\nSPOSIT 0 0 0\n"
            "MFNAME m1.mat                         [Material file, up to 20 chars]\n"
            "MFNAME m2.mat                         [Material file, up to 20 chars]\n"
            "GEOMFN spheres.geo                    [Geometry file, up to 20 chars]\n" + extra + "NSIMSH 1.0E6\n")


def clones_in(extra: str) -> str:
    return ("TITLE  seguir\nSKPAR  2\nSENERG 1.0E6\nSPOSIT 0 0 0\n"
            "MFNAME m1.mat                         [Material file, up to 20 chars]\n"
            "MFNAME m2.mat                         [Material file, up to 20 chars]\n"
            "MFNAME m3.mat                         [Material file, up to 20 chars]\n"
            "GEOMFN c.geo                          [Geometry file, up to 20 chars]\n" + extra + "NSIMSH 1.0E6\n")


def void_in(extra: str = "", source: str = "SKPAR  1\nSENERG 1.0E6\nSPOSIT 0 0 0\n") -> str:
    return ("TITLE  cuerpo vacío\n" + source + MAT
            + "GEOMFN void.geo                       [Geometry file, up to 20 chars]\n" + extra + "NSIMSH 1.0E6\n")


PSF_LINE = "IPSFN  psf.dat                        [Input psf name, up to 20 characters]\n"
PSF_TEXT = " 2  1.0E6  0.0 0.0 -5.0  0.0 0.0 1.0  1.0  1 0 0 0  1\n"
NUC = ("COM  Nucleido de prueba (formato de PENNUC: código de 3 letras, un blanco y los datos)\n"
       "NUC Xx-100\n"
       "AZ  100, 50\n"
       "ND  1\n"
       "COM  Un comentario entre los datos\n"
       "DAU Yy-100\n"
       "DDE 1.0, 0.0, 1, 2\n"
       "Q   1500.0, 0.0\n"
       "BEM 0.6, 0.0, 1, 800.0, 0.0, 0\n"
       "ALP 0.4, 0.0, 0, 5000.0, 0.0, 0\n"
       "LED 300.0, 0.0, 1, 1.0E-12, 0.0\n"
       "GA  1.0, 0.0, 300.0, 0.0, 0\n")
# Positrones (β+ de 2 MeV) y un nivel sin transiciones (cuenta su energía).
NUC_BEP = ("NUC Zz-60\nAZ  60; 30\nND  1\nDAU Ww-60\nDDE 1.0; 0.0; 2; 1\nQ   3000.0; 0.0\n"
           "BEP 1.0; 0.0; 2; 2000.0; 0.0; 0\n"
           "LED 2600.0; 0.0; 0; 1.0E-12; 0.0\n"
           "LED 100.0; 0.0; 1; 1.0E-12; 0.0\nGA  1.0; 0.0; 100.0; 0.0; 0\n")


def nuclide_in(det: str, name: str = "xx.nuc") -> str:
    return ("TITLE  nucleido\nSKPAR  0\nSRNUCL " + name + "\nSPOSIT 0 0 0\n"
            "MFNAME H2O.mat                        [Material file, up to 20 chars]\n"
            "GEOMFN disc-origin.geo                [Geometry file, up to 20 chars]\n" + det + "NSIMSH 1e6\n")


def write(folder: Path, files: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        if isinstance(content, Path):
            shutil.copy2(content, folder / name)
        else:
            (folder / name).write_bytes(content.encode("latin-1"))


def argus(folder: Path, in_text: str, files: dict | None = None):
    """(doc, geo, diagnósticos) de ARGUS para el caso escrito en ``folder`` (lector + PMRDR)."""
    write(folder, {**(files or {}), "case.in": in_text})
    doc = parse_in_file(folder / "case.in")
    geo = None
    name = doc.model.geomfn
    if name and (folder / name).is_file():
        geo = compile_geometry(load_geometry(folder / name), parinp=doc.model.parinp_list() or None, base_dir=folder)
    return doc, geo, list(doc.diagnostics) + list(validate_case(doc, geo, folder))


def codes(diags) -> set:
    return {d.code for d in diags if d.severity == Severity.ERROR}


def coded(diags) -> list:
    return [(d.code, d.message) for d in diags if d.severity == Severity.ERROR and d.code is not None]


def text_of(diags, severity=None) -> str:
    return " | ".join(d.message for d in diags if severity is None or d.severity == severity)


@pytest.fixture
def need_sim_probe():
    from argus.oracle.probe import find_sim_probe

    if find_sim_probe() is None:
        pytest.skip("Sonda de PMRDR no compilada (python tools/build_probe.py --penhmain ...)")


def penhmain(folder: Path, in_text: str) -> tuple[int, str, str]:
    """(IRETRN, REASON, penhmain.dat) del PMRDR real ejecutado en ``folder`` (puede tener subcarpetas)."""
    from argus.oracle.probe import find_sim_probe

    proc = subprocess.run([str(find_sim_probe())], input=in_text.encode("latin-1"), cwd=folder, capture_output=True,
                          timeout=120)
    vals = {}
    for ln in proc.stdout.decode("latin-1", errors="replace").splitlines():
        key, _, rest = ln.partition(" ")
        vals.setdefault(key, rest.strip())
    out = folder / "penhmain.dat"
    output = out.read_text(encoding="latin-1", errors="replace") if out.is_file() else ""
    return (int(vals["IRETRN"]) if "IRETRN" in vals else -1), vals.get("REASON", "CRASH"), output


def assert_agree(tmp: Path, in_text: str, files: dict | None = None, dirs: tuple = ()) -> int:
    """ARGUS da el IRETRN de penhmain (o ningún error con código si penhmain no se detiene)."""
    a, p = tmp / "argus", tmp / "pen"
    for d in dirs:
        (a / d).mkdir(parents=True, exist_ok=True)
        (p / d).mkdir(parents=True, exist_ok=True)
    _, _, diags = argus(a, in_text, files)
    write(p, files or {})
    iretrn, reason, _ = penhmain(p, in_text)
    assert iretrn >= 0, f"penhmain se detiene con un error de ejecución: {reason}"
    if iretrn:
        assert iretrn in codes(diags), f"penhmain {iretrn} ({reason}); ARGUS {coded(diags)}"
    else:
        assert not coded(diags), f"penhmain no se detiene; ARGUS {coded(diags)}"
    return iretrn


# ---------------------------------------------------------------------------
# 1. Referencias a cuerpos: 0 y etiquetas en blanco
# ---------------------------------------------------------------------------

def test_idbody_does_not_search_blank_labels(tmp_path):
    """Con copias de clones sin etiqueta: IDBODY '    ' es 3082 (no busca), EDBODY '    ' es el cuerpo 1997."""
    files = {"spheres.geo": SPHERES}
    _, geo, d = argus(tmp_path / "a", spheres_in("IMPDET 0 1e6 100 0 1\nIDBODY '    '        [Active body]\n"), files)
    assert geo.nbodys == 1999 and not geo.balias(20).strip() and geo.body(1997).material == 0
    assert 3082 in codes(d) and 3084 not in codes(d)
    assert "sin etiqueta" in text_of(d, Severity.ERROR)
    _, _, d = argus(tmp_path / "b", spheres_in("IMPDET 0 1e6 100 0 1\nIDBODY 0        [Active body]\n"), files)
    assert 3082 in codes(d) and 3084 not in codes(d)
    _, _, d = argus(tmp_path / "c", spheres_in("ENDETC 0 1e6 100\nEDBODY '    '        [Active body]\n"), files)
    assert 3100 in codes(d) and "1997" in text_of(d)


def test_blank_label_names_the_last_unlabelled_body(tmp_path):
    """DSMAX '    ' se aplica al último cuerpo sin etiqueta (1997, vacío): penhmain ignora la línea sin decir nada."""
    _, _, d = argus(tmp_path, spheres_in("DSMAX  '    ' 0.01               [KB, maximum step length in body KB]\n"),
                    {"spheres.geo": SPHERES})
    assert not codes(d)
    notes = [x for x in d if "1997" in x.message]
    assert notes and any(x.severity == Severity.WARNING for x in notes), text_of(d)
    assert "sin etiqueta" in text_of(notes)


@pytest.mark.parametrize("extra, sbody", [
    ("DSMAX  0 0.1        [x]\n", ""),
    ("DSMAX  '    ' 0.1        [x]\n", ""),
    ("EABSB  0 1e4 1e4 1e4        [x]\n", ""),
    ("IFORCE 0 2 3 10 0.5 1.5   [x]\n", ""),
    ("IFORCE 0 1 4 10 0.5 1.5   [x]\nIBRSPL 0 2   [x]\n", ""),
    ("IXRSPL 0 2   [x]\n", ""),
    ("", "SBODY  0     [b]\n"),
    ("", "SBODY  '    '     [b]\n"),
])
def test_zero_names_the_enclosure(tmp_path, extra, sbody):
    """Con la envolvente que añade PENGEOM, 0 y '    ' la nombran (cuerpo 3): penhmain lo acepta."""
    _, geo, d = argus(tmp_path, enc_in(extra, sbody), {"enc.geo": ENC_GEO})
    assert geo.enclosure_added and geo.nbodys == 3
    assert not coded(d), coded(d)
    assert "envolvente" in text_of(d), text_of(d)


def test_zero_in_detectors(tmp_path):
    """EDBODY 0 es la envolvente (vacía: 3100, no 3098); IDBODY 0 no busca (3082)."""
    _, _, d = argus(tmp_path / "e", enc_in("ENDETC 0 1e6 100\nEDBODY 0      [b]\n"), {"enc.geo": ENC_GEO})
    assert 3100 in codes(d) and 3098 not in codes(d)
    _, _, d = argus(tmp_path / "i", enc_in("IMPDET 0 1e6 100 0 1\nIDBODY 0      [b]\n"), {"enc.geo": ENC_GEO})
    assert 3082 in codes(d) and 3084 not in codes(d)


def test_zero_without_unlabelled_bodies_is_an_error(tmp_path):
    """Sin cuerpos sin etiqueta (un módulo lo envuelve todo), 0 no nombra nada: 3055, 3064, 3098 (y se explica)."""
    extra = "DSMAX  0 0.1        [x]\nIFORCE '    ' 2 3 10 0.5 1.5   [x]\nENDETC 0 1e6 100\nEDBODY 0      [b]\n"
    _, geo, d = argus(tmp_path, enc_in(extra, "SBODY  0     [b]\n"), {"enc.geo": WRAPPED_GEO})
    assert geo.ok and not any(not geo.balias(k).strip() for k in range(1, geo.nbodys + 1))
    assert {3050, 3055, 3064, 3098} <= codes(d)
    assert "sin etiqueta" in text_of(d, Severity.ERROR)


REF_CASES = [
    ("enc.geo", ENC_GEO, enc_in("DSMAX  0 0.1        [x]\n")),
    ("enc.geo", ENC_GEO, enc_in("DSMAX  '    ' 0.1        [x]\nEABSBH 0 1e6 1e6 1e6   [x]\n")),
    ("enc.geo", ENC_GEO, enc_in("IFORCE 0 2 3 10 0.5 1.5   [x]\nIBRSPL 0 2   [x]\n")),
    ("enc.geo", ENC_GEO, enc_in("IXRSPL '    ' 2   [x]\n")),
    ("enc.geo", ENC_GEO, enc_in("", "SBODY  0     [b]\n")),
    ("enc.geo", ENC_GEO, enc_in("ENDETC 0 1e6 100\nEDBODY 0      [b]\n")),
    ("enc.geo", ENC_GEO, enc_in("ENDETC 0 1e6 100\nEDBODY '    '      [b]\n")),
    ("enc.geo", ENC_GEO, enc_in("IMPDET 0 1e6 100 0 1\nIDBODY 0      [b]\n")),
    ("enc.geo", ENC_GEO, enc_in("IMPDET 0 1e6 100 0 1\nIDBODY '    '      [b]\n")),
    ("void.geo", VOID_GEO, void_in("DSMAX  0 0.1        [x]\n")),
    ("void.geo", VOID_GEO, void_in("IFORCE 0 2 3 10 0.5 1.5   [x]\n")),
    ("void.geo", VOID_GEO, void_in("ENDETC 0 1e6 100\nEDBODY '    '      [b]\n")),
    ("enc.geo", WRAPPED_GEO, enc_in("DSMAX  0 0.1        [x]\n")),
    ("enc.geo", WRAPPED_GEO, enc_in("EABSB  '    ' 1e4 1e4 1e4        [x]\n")),
    ("enc.geo", WRAPPED_GEO, enc_in("IFORCE '    ' 2 3 10 0.5 1.5   [x]\n")),
    ("enc.geo", WRAPPED_GEO, enc_in("IXRSPL 0 2   [x]\n")),
    ("enc.geo", WRAPPED_GEO, enc_in("", "SBODY  0     [b]\n")),
    ("enc.geo", WRAPPED_GEO, enc_in("ENDETC 0 1e6 100\nEDBODY 0      [b]\n")),
    ("c.geo", clones_geo((3, 5)), clones_in("ENDETC 0 1e6 100\nEDBODY '    '      [b]\n")),
    ("c.geo", clones_geo((3, 5)), clones_in("DSMAX  0 0.1        [x]\nIFORCE 0 1 4 10 0.5 1.5   [x]\n"
                                            "IBRSPL '    ' 3   [x]\n")),
    ("spheres.geo", SPHERES, spheres_in("IMPDET 0 1e6 100 0 1\nIDBODY '    '        [Active body]\n")),
    ("spheres.geo", SPHERES, spheres_in("ENDETC 0 1e6 100\nEDBODY '    '        [Active body]\n")),
    ("spheres.geo", SPHERES, spheres_in("DSMAX  '    ' 0.01               [x]\nIFORCE 0 2 3 10 0.5 1.5   [x]\n")),
]


@pytest.mark.oracle
@pytest.mark.parametrize("n", range(len(REF_CASES)))
def test_body_references_match_penhmain(tmp_path, need_sim_probe, n):
    name, geo, text = REF_CASES[n]
    assert_agree(tmp_path, text, {name: geo})


# ---------------------------------------------------------------------------
# 2. IFORCE con FORCER negativo en un cuerpo vacío
# ---------------------------------------------------------------------------

def test_negative_forcer_in_void_body_does_not_force(tmp_path):
    files = {"void.geo": VOID_GEO}
    force = "IFORCE 2 1 4 -5 0.9 1.0    [x]\n"
    _, geo, d = argus(tmp_path / "a", void_in(force + "ENDETC 0 1e6 100\nEDBODY 1  [b]\n"), files)
    assert geo.body(2).material == 0
    assert 3095 not in codes(d), coded(d)
    assert "vacío" in text_of(d, Severity.WARNING)
    _, _, d = argus(tmp_path / "b", void_in(force + "IBRSPL 2 5   [x]\n"), files)
    assert 3068 in codes(d)
    # Con FORCER positivo penhmain sí marca el forzado (aunque en el vacío no hay interacciones).
    _, _, d = argus(tmp_path / "c", void_in("IFORCE 2 1 4 5 0.9 1.0    [x]\nIBRSPL 2 5   [x]\n"), files)
    assert not coded(d)
    # En un cuerpo con material, el FORCER negativo se recalcula (y casi siempre fuerza).
    _, _, d = argus(tmp_path / "d", void_in("IFORCE 1 1 4 -5 0.9 1.0    [x]\nENDETC 0 1e6 100\nEDBODY 1  [b]\n"),
                    files)
    assert 3095 in codes(d)


@pytest.mark.oracle
@pytest.mark.parametrize("extra", [
    "IFORCE 2 1 4 -5 0.9 1.0    [x]\nENDETC 0 1e6 100\nEDBODY 1  [b]\n",
    "IFORCE 2 1 4 -5 0.9 1.0    [x]\nIBRSPL 2 5   [x]\n",
    "IFORCE 2 1 4 5 0.9 1.0    [x]\nIBRSPL 2 5   [x]\n",
    "IFORCE 2 1 4 5 0.9 1.0    [x]\nENDETC 0 1e6 100\nEDBODY 1  [b]\n",
    "IFORCE 1 1 4 -5 0.9 1.0    [x]\nIBRSPL 1 5   [x]\n",
], ids=["void-neg-endetc", "void-neg-ibrspl", "void-pos-ibrspl", "void-pos-endetc", "mat-neg-ibrspl"])
def test_void_forcing_matches_penhmain(tmp_path, need_sim_probe, extra):
    assert_agree(tmp_path, void_in(extra), {"void.geo": VOID_GEO})


# ---------------------------------------------------------------------------
# 3. Energía máxima de las ventanas de los detectores
# ---------------------------------------------------------------------------

POSITRON = "SKPAR  3\nSENERG 1.0E6\nSPOSIT 0 0 0\n"


def test_positron_source_max_energy(tmp_path):
    """Con positrones, PMRDR usa 1,21·(E0 + 512 keV) (fotones de aniquilación): 1,82952 MeV para E0 = 1 MeV."""
    files = {"void.geo": VOID_GEO}
    doc, _, d = argus(tmp_path / "a", void_in("IMPDET 1.5e6 0 100 0 1\nIDBODY 1 [b]\n", POSITRON), files)
    assert max_particle_energy(doc, tmp_path / "a") == pytest.approx(1.21 * (1.0e6 + 5.12e5), rel=1e-15)
    assert 3077 not in codes(d), coded(d)
    _, _, d = argus(tmp_path / "b", void_in("ENDETC 1.5e6 0 100\nEDBODY 1 [b]\n", POSITRON), files)
    assert 3097 not in codes(d), coded(d)
    _, _, d = argus(tmp_path / "c", void_in("IMPDET 1.9e6 0 100 0 1\nIDBODY 1 [b]\n", POSITRON), files)
    assert 3077 in codes(d)
    _, _, d = argus(tmp_path / "d", void_in("ENDETC 1.9e6 0 100\nEDBODY 1 [b]\n", POSITRON), files)
    assert 3097 in codes(d)


def test_psf_max_energy_is_the_epmax_line(tmp_path):
    files = {"void.geo": VOID_GEO, "psf.dat": PSF_TEXT}
    src = PSF_LINE + "EPMAX  1e6 1e6\n"
    doc, _, d = argus(tmp_path / "a", void_in("IMPDET 2e6 0 100 0 1\nIDBODY 1 [b]\n", src), files)
    assert max_particle_energy(doc, tmp_path / "a") == 1.0e6
    assert 3077 in codes(d)
    _, _, d = argus(tmp_path / "b", void_in("ENDETC 2e6 0 100\nEDBODY 1 [b]\n", src), files)
    assert 3097 in codes(d)
    # EPMAX con un valor no mayor que 1 eV (o sin la línea): penhmain usa 1 GeV.
    for n, s in enumerate((PSF_LINE + "EPMAX  0.5 1e6\n", PSF_LINE)):
        doc, _, d = argus(tmp_path / f"c{n}", void_in("IMPDET 2e6 0 100 0 1\nIDBODY 1 [b]\n", s), files)
        assert max_particle_energy(doc, tmp_path / f"c{n}") == 1.0e9 and not coded(d), coded(d)
    # Protones: EHMAX = E0, y la energía máxima de la ventana es la de la fuente.
    doc, _, d = argus(tmp_path / "p", void_in("IMPDET 1.5e8 0 100 0 1\nIDBODY 1 [b]\n",
                                              "SKPAR  4\nSENERG 1.0E8\nSPOSIT 0 0 0\n"), files)
    assert max_particle_energy(doc, tmp_path / "p") == 1.0e8 and 3077 in codes(d)


def test_nbe_window_uses_the_max_energy(tmp_path):
    """NBE con EU < 50 eV: el límite superior es MAX(EPMAX, EHMAX), como en los detectores (error sin código)."""
    files = {"void.geo": VOID_GEO, "psf.dat": PSF_TEXT}
    _, _, d = argus(tmp_path / "a", void_in("NBE    2e6 0 100\n", PSF_LINE + "EPMAX  1e6 1e6\n"), files)
    assert "NBE" in text_of(d, Severity.ERROR) and not coded(d)
    _, _, d = argus(tmp_path / "b", void_in("NBE    1.5e6 0 100\n", POSITRON), files)
    assert "NBE" not in text_of(d, Severity.ERROR)
    _, _, d = argus(tmp_path / "c", void_in("NBE    1.9e6 0 100\n", POSITRON), files)
    assert "NBE" in text_of(d, Severity.ERROR)


def test_nuclide_max_energy(tmp_path):
    (tmp_path / "xx.nuc").write_text(NUC)
    (tmp_path / "zz.nuc").write_text(NUC_BEP.replace("\n", "\r\n"))
    # β- de 800 keV, α de 5 MeV y un γ de 300 keV: 1,01 · 5 MeV (SOURCE0).
    assert nuclide_max_energy(tmp_path / "xx.nuc") == 1.01 * 5.0e6
    # β+ de 2 MeV (1,21·(E + 512 keV)) frente a un nivel de 2,6 MeV sin transiciones.
    assert nuclide_max_energy(tmp_path / "zz.nuc") == 1.01 * max(1.21 * (2.0e6 + 5.12e5), 2.6e6)
    assert nuclide_max_energy(tmp_path / "no.nuc") is None
    (tmp_path / "bad.nuc").write_text(NUC.replace("BEM 0.6", "BEM x.6"))
    assert nuclide_max_energy(tmp_path / "bad.nuc") is None
    (tmp_path / "short.nuc").write_text("".join(NUC.splitlines(keepends=True)[:9]))
    assert nuclide_max_energy(tmp_path / "short.nuc") is None


def test_nuclide_detector_windows(tmp_path):
    files = {"xx.nuc": NUC, "disc-origin.geo": NUCLIDE_CASE / "disc-origin.geo"}
    doc, _, d = argus(tmp_path / "a", nuclide_in("IMPDET 5.0e6 0 100 0 1\nIDBODY 1 [b]\n"), files)
    assert max_particle_energy(doc, tmp_path / "a") == 1.01 * 5.0e6
    assert 3077 not in codes(d)
    _, _, d = argus(tmp_path / "b", nuclide_in("IMPDET 5.06e6 0 100 0 1\nIDBODY 1 [b]\n"), files)
    assert 3077 in codes(d)
    _, _, d = argus(tmp_path / "c", nuclide_in("ENDETC 5.06e6 0 100\nEDBODY 1 [b]\n"), files)
    assert 3097 in codes(d)
    # Sin el .nuc no se sabe la energía máxima: no se marca la ventana (sí que falta el fichero).
    doc, _, d = argus(tmp_path / "d", nuclide_in("IMPDET 1e12 0 100 0 1\nIDBODY 1 [b]\n", "otro.nuc"), files)
    assert max_particle_energy(doc, tmp_path / "d") is None
    assert 3077 not in codes(d) and 3120 in codes(d)


ENERGY_CASES = [
    ("pos-imp-1.5", void_in("IMPDET 1.5e6 0 100 0 1\nIDBODY 1 [b]\n", POSITRON), {}),
    ("pos-end-1.5", void_in("ENDETC 1.5e6 0 100\nEDBODY 1 [b]\n", POSITRON), {}),
    ("pos-imp-1.9", void_in("IMPDET 1.9e6 0 100 0 1\nIDBODY 1 [b]\n", POSITRON), {}),
    ("pos-end-log", void_in("ENDETC 1.82e6 0 -100\nEDBODY 1 [b]\n", POSITRON), {}),
    ("psf-imp", void_in("IMPDET 2e6 0 100 0 1\nIDBODY 1 [b]\n", PSF_LINE + "EPMAX  1e6 1e6\n"), {"psf.dat": PSF_TEXT}),
    ("psf-end", void_in("ENDETC 2e6 0 100\nEDBODY 1 [b]\n", PSF_LINE + "EPMAX  1e6 3e6\n"), {"psf.dat": PSF_TEXT}),
    ("psf-small", void_in("IMPDET 2e6 0 100 0 1\nIDBODY 1 [b]\n", PSF_LINE + "EPMAX  0.5 1e6\n"),
     {"psf.dat": PSF_TEXT}),
]


@pytest.mark.oracle
@pytest.mark.parametrize("n", range(len(ENERGY_CASES)), ids=[c[0] for c in ENERGY_CASES])
def test_detector_windows_match_penhmain(tmp_path, need_sim_probe, n):
    _, text, extra = ENERGY_CASES[n]
    assert_agree(tmp_path, text, {"void.geo": VOID_GEO, **extra})


@pytest.mark.oracle
@pytest.mark.parametrize("nuc, el", [(NUC, 5.0e6), (NUC, 5.06e6), (NUC_BEP, 3.0e6), (NUC_BEP, 3.1e6)],
                         ids=["alfa-dentro", "alfa-fuera", "beta+-dentro", "beta+-fuera"])
def test_nuclide_windows_match_penhmain(tmp_path, need_sim_probe, nuc, el):
    if not PDFILES:
        pytest.skip("Faltan pdrelax.p11 y pdatconf.p14 (base de datos de PENELOPE) para PENNUC.")
    files = {"xx.nuc": nuc, "disc-origin.geo": NUCLIDE_CASE / "disc-origin.geo",
             "pdrelax.p11": PDFILES[0] / "pdrelax.p11", "pdatconf.p14": PDFILES[0] / "pdatconf.p14"}
    code = assert_agree(tmp_path, nuclide_in(f"IMPDET {el:g} 0 100 0 1\nIDBODY 1 [b]\n"), files)
    assert code == (3077 if el > nuclide_max_energy(tmp_path / "argus" / "xx.nuc") else 0)


# ---------------------------------------------------------------------------
# 4. Nombres de fichero en blanco o que no se pueden abrir
# ---------------------------------------------------------------------------

def _geomfn(line: str | None) -> str:
    text = enc_in()
    old = "GEOMFN enc.geo                        [Geometry file, up to 20 chars]\n"
    if line is None:  # el fichero termina tras los materiales
        return text.split(old)[0]
    return text.replace(old, line + "\n")


def test_missing_or_blank_geomfn_is_3036(tmp_path):
    for n, line in enumerate((None, "GEOMFN                               [Geometry file, up to 20 chars]",
                              "GEOMFN")):
        doc, _, d = argus(tmp_path / str(n), _geomfn(line), {"enc.geo": ENC_GEO})
        assert doc.model.ok and not doc.model.geomfn
        assert 3036 in codes(d), (line, text_of(d))


def test_geomfn_folder_problems_are_3036(tmp_path):
    (tmp_path / "a" / "sub").mkdir(parents=True)
    _, _, d = argus(tmp_path / "a", _geomfn("GEOMFN sub                           [Geometry file]"))
    assert 3036 in codes(d) and "carpeta" in text_of(d, Severity.ERROR)
    _, _, d = argus(tmp_path / "b", _geomfn("GEOMFN sub/c.geo                     [Geometry file]"))
    assert 3036 in codes(d) and "vacío" not in text_of(d, Severity.ERROR)
    # En una carpeta que existe, OPEN crea el fichero vacío y GEOMIN falla al leerlo (sin IRETRN).
    _, _, d = argus(tmp_path / "c", _geomfn("GEOMFN nope.geo                      [Geometry file]"))
    assert 3036 not in codes(d) and "vacío" in text_of(d, Severity.ERROR)


def test_blank_ipsfn_is_3116(tmp_path):
    files = {"void.geo": VOID_GEO, "psf.dat": PSF_TEXT}
    for n, line in enumerate(("IPSFN                                [Input psf name, up to 20 characters]",
                              "IPSFN")):
        _, _, d = argus(tmp_path / str(n), void_in("", line + "\nEPMAX  1e8 1e8\n"), files)
        assert 3116 in codes(d), text_of(d)
    # Un segundo fichero en blanco: penhmain lo abre al acabar el primero (sin IOSTAT): error sin código.
    _, _, d = argus(tmp_path / "x", void_in("", PSF_LINE + "IPSFN\nEPMAX  1e8 1e8\n"), files)
    assert not coded(d) and "blanco" in text_of(d, Severity.ERROR)


def test_first_psf_folder_problems(tmp_path):
    """El primer IPSFN se abre con IOSTAT: una carpeta o una subcarpeta que no existe dan 3116."""
    (tmp_path / "a" / "fases").mkdir(parents=True)
    files = {"void.geo": VOID_GEO}
    _, _, d = argus(tmp_path / "a", void_in("", "IPSFN  fases\nEPMAX  1e8 1e8\n"), files)
    assert 3116 in codes(d)
    _, _, d = argus(tmp_path / "b", void_in("", "IPSFN  no/psf.dat\nEPMAX  1e8 1e8\n"), files)
    assert 3116 in codes(d)
    _, _, d = argus(tmp_path / "c", void_in("", "IPSFN  psf.dat\nEPMAX  1e8 1e8\n"), files)
    assert 3117 in codes(d)
    # Los siguientes se abren al acabar el anterior: uno que falta se crea vacío y se salta (sin IRETRN).
    _, _, d = argus(tmp_path / "d", void_in("", PSF_LINE + "IPSFN  otro.dat\nEPMAX  1e8 1e8\n"),
                    {**files, "psf.dat": PSF_TEXT})
    assert not coded(d) and "otro.dat" in text_of(d, Severity.ERROR)


def test_blank_mfname_cannot_be_opened(tmp_path):
    text = enc_in().replace("MFNAME water.mat                      [Material file, up to 20 chars]",
                            "MFNAME                               [Material file, up to 20 chars]")
    _, _, d = argus(tmp_path, text, {"enc.geo": ENC_GEO})
    assert 1006 in codes(d), text_of(d)
    # Sin carpeta también se ve (no depende de los ficheros).
    doc = parse_in_text(text)
    assert 1006 in codes(validate_case(doc, None, None))


NAME_CASES = [
    ("no-geomfn", _geomfn(None), (), {}),
    ("blank-geomfn", _geomfn("GEOMFN                               [Geometry file, up to 20 chars]"), (), {}),
    ("geomfn-dir", _geomfn("GEOMFN sub                           [Geometry file]"), ("sub",), {}),
    ("geomfn-subfolder", _geomfn("GEOMFN sub/c.geo                     [Geometry file]"), (), {}),
    ("blank-ipsfn", void_in("", "IPSFN                                [Input psf name]\nEPMAX  1e8 1e8\n"), (), {}),
    ("ipsfn-dir", void_in("", "IPSFN  fases\nEPMAX  1e8 1e8\n"), ("fases",), {}),
    ("ipsfn-subfolder", void_in("", "IPSFN  no/psf.dat\nEPMAX  1e8 1e8\n"), (), {}),
    ("second-psf-missing", void_in("", PSF_LINE + "IPSFN  otro.dat\nEPMAX  1e8 1e8\n"), (), {"psf.dat": PSF_TEXT}),
]


@pytest.mark.oracle
@pytest.mark.parametrize("n", range(len(NAME_CASES)), ids=[c[0] for c in NAME_CASES])
def test_file_names_match_penhmain(tmp_path, need_sim_probe, n):
    _, text, dirs, extra = NAME_CASES[n]
    assert_agree(tmp_path, text, {"enc.geo": ENC_GEO, "void.geo": VOID_GEO, **extra}, dirs=dirs)


# ---------------------------------------------------------------------------
# 5. El .in sigue a la geometría con la misma resolución
# ---------------------------------------------------------------------------

def _refs(doc):
    return [(ln.kw.strip(), ln.label, ln.kb) for ln in doc.lines if ln.spec is not None and ln.spec.mode == "body"]


def test_references_to_resolves_like_penhmain():
    geo = compile_geometry(load_geometry(SPHERES), base_dir=SPHERES.parent)
    doc = parse_in_text(spheres_in("DSMAX  '    ' 0.01               [x]\nIMPDET 0 1e6 100 0 1\n"
                                   "IDBODY '    '        [Active body]\nIDBODY 20        [Active body]\n"))
    assert [ln.kw.strip() for ln in references_to(doc, geo, 1997)] == ["DSMAX"]  # IDBODY '    ' no nombra nada
    assert [ln.kw.strip() for ln in references_to(doc, geo, 20)] == ["IDBODY"]  # solo la línea con su número
    enc = compile_geometry(parse_geo_text(ENC_GEO, "enc.geo"))
    doc = parse_in_text(enc_in("DSMAX  0 0.1        [x]\nIMPDET 0 1e6 100 0 1\nIDBODY 0      [b]\n",
                               "SBODY  '    '     [b]\n"))
    assert [ln.kw.strip() for ln in references_to(doc, enc, 3)] == ["SBODY", "DSMAX"]


def test_source_edit_never_writes_a_blank_label():
    """source_edit: una copia de clon (sin etiqueta) se escribe por su número; SBODY 0 se resuelve como PMRDR."""
    from argus.penhan import source_edit as se

    geo = compile_geometry(parse_geo_text(clones_geo((3, 5)), "c.geo"))
    assert se.body_reference(geo, 3) == {"label": None, "kb": 3} and se.body_reference(geo, 5) == {"label": None,
                                                                                                    "kb": 5}
    assert se.body_reference(geo, 1) == {"label": "   1", "kb": None}
    doc = parse_in_text(clones_in("").replace("SPOSIT 0 0 0\n", "SPOSIT 0 0 0\nSBOX   1 1 1\nSBODY  0   [b]\n"))
    assert se.active_bodies(doc, geo) == {5}  # el último cuerpo sin etiqueta
    se.set_active_bodies(doc, geo, [3, 1])
    assert "'    '" not in doc.text() and [(r.label, r.kb) for r in doc.model.source.bodies] == [(None, 3),
                                                                                             ("   1", None)]


def test_follow_retargets_zero_and_blank_labels():
    """Un clon nuevo cambia cuál es el último cuerpo sin etiqueta: 0 y '    ' pasan al número del de antes."""
    old = compile_geometry(parse_geo_text(clones_geo((3,)), "c.geo"))
    new = compile_geometry(parse_geo_text(clones_geo((3, 5)), "c.geo"))
    assert old.ok and new.ok and not old.balias(3).strip() and not new.balias(5).strip()
    mapping = renumber_map(old, new)
    assert mapping[3] == 3 and mapping[5] == 7
    doc = parse_in_text(clones_in("DSMAX  0 0.05     [x]\nDSMAX  5 0.02     [x]\nIMPDET 0 1e6 100 0 1\n"
                                  "IDBODY '    '  [b]\nENDETC 0 1e6 100\nEDBODY '    '      [Active body]\n"))
    assert doc.model.ok
    notes = follow_geometry(doc, mapping, old_geo=old, new_geo=new)
    assert _refs(doc) == [("DSMAX", None, 3), ("DSMAX", None, 7), ("IDBODY", "    ", None), ("EDBODY", None, 3)]
    assert notes and any("sin etiqueta" in n for n in notes), notes
    text = doc.text()
    assert "EDBODY 3      [Active body]" in text and "DSMAX  3 0.05     [x]" in text
    assert text.count("'    '") == 1  # solo queda la de IDBODY (penhmain no la resuelve: 3082 antes y después)
    assert parse_in_text(text).model.ok
    # Si el último cuerpo sin etiqueta sigue siendo el mismo, la línea no cambia.
    doc = parse_in_text(clones_in("DSMAX  0 0.05     [x]\n"))
    assert follow_geometry(doc, {k: k for k in range(1, old.nbodys + 1)}, old_geo=old, new_geo=old) == []
    assert _refs(doc) == [("DSMAX", None, 0)]


def test_follow_removes_zero_when_its_body_disappears():
    """Al meter las esferas en un módulo desaparece la envolvente: las líneas con 0 que la nombraban se quitan."""
    old = compile_geometry(parse_geo_text(ENC_GEO, "enc.geo"))
    new = compile_geometry(parse_geo_text(WRAPPED_GEO, "enc.geo"))
    assert old.ok and new.ok and old.enclosure_added and not new.enclosure_added
    mapping = renumber_map(old, new)
    assert mapping == {1: 1, 2: 2, 3: None}
    doc = parse_in_text(enc_in("DSMAX  0 0.1        [x]\nDSMAX  1 0.1        [x]\n", "SBODY  0     [b]\n"))
    notes = follow_geometry(doc, mapping, old_geo=old, new_geo=new)
    assert _refs(doc) == [("DSMAX", None, 1)]
    assert any("quitada" in n for n in notes) and any("SBODY" in n for n in notes), notes


def test_follow_ignores_blank_deleted_labels():
    """Al borrar el clon 3, la ventana pasa como etiquetas borradas '   3' y la de su copia ('    ').

    '    ' no es una etiqueta de usuario: las líneas con 0 o '    ' siguen al cuerpo que nombraban (la copia del
    clon 5, que sigue ahí); la del clon borrado se quita.
    """
    old = compile_geometry(parse_geo_text(clones_geo((3, 5)), "c.geo"))
    new = compile_geometry(parse_geo_text(clones_geo((5,)), "c.geo"))
    mapping = renumber_map(old, new)
    assert mapping[3] is None and mapping[4] is None and mapping[5] == 3
    deleted = {old.balias(kb) for kb, v in mapping.items() if v is None and old.balias(kb)}  # como la ventana
    assert deleted == {"    ", "   3"}
    body = "DSMAX  '    ' 0.05     [x]\nDSMAX  0 0.05     [x]\nDSMAX  '   3' 0.05     [x]\nDSMAX  '   1' 0.05     [x]\n"
    doc = parse_in_text(clones_in(body))
    notes = follow_geometry(doc, mapping, deleted_labels=deleted, old_geo=old, new_geo=new)
    assert _refs(doc) == [("DSMAX", "    ", None), ("DSMAX", None, 0), ("DSMAX", "   1", None)], notes
    # Sin las geometrías no se sabe a qué cuerpo iban: esas líneas se dejan como están.
    doc = parse_in_text(clones_in(body))
    follow_geometry(doc, mapping, deleted_labels=deleted)
    assert _refs(doc) == [("DSMAX", "    ", None), ("DSMAX", None, 0), ("DSMAX", "   1", None)]


@pytest.mark.oracle
def test_penhmain_applies_the_followed_line_to_the_same_body(tmp_path, need_sim_probe):
    """PMRDR real: EDBODY '    ' era el cuerpo 3; tras añadir un clon, el .in seguido sigue en él (no en el 5)."""
    old_text, new_text = clones_geo((3,)), clones_geo((3, 5))
    old = compile_geometry(parse_geo_text(old_text, "c.geo"))
    new = compile_geometry(parse_geo_text(new_text, "c.geo"))
    src = clones_in("ENDETC 0 1e6 100\nEDBODY '    '      [Active body]\n")

    def active(folder, text, geo_text):
        write(folder, {"c.geo": geo_text})
        iretrn, reason, out = penhmain(folder, text)
        assert iretrn == 0, reason
        return [int(x) for x in re.findall(r"Active body =\s*(\d+)", out)]

    assert active(tmp_path / "old", src, old_text) == [3]
    assert active(tmp_path / "raw", src, new_text) == [5]  # sin seguir, penhmain cambiaría de cuerpo
    doc = parse_in_text(src)
    follow_geometry(doc, renumber_map(old, new), old_geo=old, new_geo=new)
    assert active(tmp_path / "new", doc.text(), new_text) == [3]
