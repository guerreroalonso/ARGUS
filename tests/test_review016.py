"""Revisión de 0.16 (0.16.2): la cascada con una alfa lenta, la fuente radiactiva con alfas y los ENDF, la firma de la
cascada, los ENDF con otras mayúsculas y lo que dice «Ejecutar» de los que faltan. Las de la interfaz están en
``test_gui_review016.py``; las de los materiales, en ``test_material_library.py`` y ``test_gui_material_library.py``."""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from argus.penhan.inputfile import parse_in_file, parse_in_text

CAJA = Path(__file__).parent / "corpus" / "resultados" / "caja"
# Un .mat de penhan con su lista de ENDF (NISOT > 0): con protones o alfas, penhmain los lee de la carpeta.
WATER_MAT = (" PENELOPE (v. 2018)  Material data file ...............\n Material: WATER, LIQUID  (278)\n"
             " Mass density = 1.00000000E+00 g/cm**3\n"
             " *** ENDF filenames and abundances of isotopes,  NISOT =   2\n pO16_ICRU63.endf 8016 0.99\n"
             " pO17_TENDL.endf 8017 0.001\n   0 = NALPHA\n")


# -- la cascada: una alfa lenta que no se mueve en sus primeros pasos ------------------------------------------------

def test_a_slow_alpha_is_not_the_mother_of_its_sisters():
    """Revisión de 0.16: en el punto donde acaba un protón salen otro protón, un neutrón y una alfa de 320 keV cuyos
    puntos 2.º a 5.º repiten el primero (las seis cifras de penhmain). Esos puntos contaban como vértices: la alfa era
    madre posible de sus hermanas, el protón quedaba sin madre (dos posibles) y, con un primario de más, la cascada de
    protones del caso 2 necesitaba los 49 cortes (unas 4 h por tanda). Ahora la madre es el protón que acaba ahí."""
    from argus.results.shower import _parents

    p = [1.25, -2.5, 7.75]
    tracks = [
        [[0.0, 0.0, -10.0], [0.5, -1.0, 0.0], p],  # el protón de la fuente acaba en p
        [p, [2.0, -3.0, 9.0], [3.0, -4.0, 11.0]],  # un protón que sale de p
        [p, [6.0, 6.0, 6.0]],  # un neutrón
        [p, p, p, p, p, [1.25, -2.5, 7.75001]],  # la alfa lenta
    ]
    pts = np.array([q for t in tracks for q in t], dtype=np.float64)
    starts = np.cumsum([0] + [len(t) for t in tracks]).astype(np.int64)
    energy = np.concatenate([np.linspace(1e8, 2e7, len(t)) for t in tracks])
    particle = np.array([4, 4, 6, 5], dtype=np.int8)
    parent, multi = _parents(pts, starts, np.zeros(4, dtype=bool), energy, particle)
    assert parent.tolist() == [-1, 0, 0, 0] and not multi
    # Si la alfa es la única que pasa por ese punto, sigue siendo madre de lo que sale de él.
    tracks2 = [[[0.0, 0.0, -10.0], [9.0, 9.0, 9.0]], [p, p, p, [2.0, 2.0, 2.0]], [p, [3.0, 3.0, 3.0]]]
    pts2 = np.array([q for t in tracks2 for q in t], dtype=np.float64)
    starts2 = np.cumsum([0] + [len(t) for t in tracks2]).astype(np.int64)
    energy2 = np.concatenate([np.linspace(1e6, 1e5, len(t)) for t in tracks2])
    parent2, _multi2 = _parents(pts2, starts2, np.array([False, True, False]), energy2,
                                np.array([4, 5, 1], dtype=np.int8))
    assert parent2.tolist()[2] == 1


# -- fuente radiactiva: ¿lee penhmain los ENDF? -----------------------------------------------------------------------

def _nuc(alpha_kev: float | None) -> str:
    """Un .nuc de PENNUC con una rama β- y, si se dice, una α de esa energía (keV)."""
    branches = ["BEM 0.6, 0.0, 1, 800.0, 0.0, 0"]
    if alpha_kev is not None:
        branches.append(f"ALP 0.4, 0.0, 0, {alpha_kev}, 0.0, 0")
    return ("COM  Nucleido de prueba\nNUC Xx-100\nAZ  100, 50\nND  1\nDAU Yy-100\n"
            f"DDE 1.0, 0.0, 1, {len(branches)}\nQ   1500.0, 0.0\n" + "\n".join(branches) + "\n"
            "LED 300.0, 0.0, 1, 1.0E-12, 0.0\nGA  1.0, 0.0, 300.0, 0.0, 0\n")


def _radioactive_case(folder: Path, alpha_kev: float | None, nuc_here: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if nuc_here:
        (folder / "xx.nuc").write_text(_nuc(alpha_kev), encoding="latin-1")
    for f in ("pdatconf.p14", "pdrelax.p11"):
        (folder / f).write_text("x\n")
    (folder / "agua.mat").write_text(WATER_MAT, encoding="latin-1")
    shutil.copy(CAJA / "caja.geo", folder / "caja.geo")
    text = ("TITLE  nucleido con alfas\nSKPAR  0\nSRNUCL xx.nuc\nSPOSIT 0 0 0\n"
            "MFNAME agua.mat                       [Material file, up to 20 chars]\n"
            "MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN caja.geo\nNSIMSH 1e3\n")
    (folder / "caso.in").write_text(text, encoding="latin-1")
    return folder / "caso.in"


@pytest.mark.parametrize("alpha_kev,needed", [(5900.0, True), (None, False), (99.0, False), (99.1, True)])
def test_a_radioactive_source_reads_the_endf_only_with_alphas(tmp_path, alpha_kev, needed):
    """Revisión de 0.16: con SRNUCL, ARGUS daba los ENDF por opcionales sin mirar el .nuc. penhmain llama a PHINIT
    (y lee los ENDF) si EHMAX, la energía de las alfas más energéticas por 1,01 (SOURCE0), pasa de 100 keV: con el
    At-211, «Ejecutar» dejaba simular y penhmain se paraba (IRETRN 40028). Sin alfas, no hacen falta ni se copian."""
    from argus.penhan import run as rn
    from argus.penhan.materials import hadron_data_needed

    in_path = _radioactive_case(tmp_path / "caso", alpha_kev)
    doc = parse_in_file(in_path)
    assert hadron_data_needed(doc) is needed
    plan = rn.plan_run(in_path, doc, penhmain=None, database=None, endf_dir=None)
    endf = [it for it in plan.files.items if it.kind == "endf"]
    if needed:
        assert endf and all(it.required for it in endf)
        assert any("ENDF" in p and "Programas de penhan" in p for p in plan.problems)
    else:
        assert not endf and not any("ENDF" in p for p in plan.problems)
        assert any("no emite alfas de más de 100 keV" in n for n in plan.files.notes)


def test_the_nuclide_of_the_database_also_says_it(tmp_path):
    """El .nuc puede estar aún en database/nucleide-files (se copia al preparar la carpeta); sin él, no se sabe."""
    from argus.penhan.materials import hadron_data_needed

    in_path = _radioactive_case(tmp_path / "caso", 5900.0, nuc_here=False)
    db = tmp_path / "database"
    (db / "nucleide-files").mkdir(parents=True)
    doc = parse_in_file(in_path)
    assert hadron_data_needed(doc) is None
    (db / "nucleide-files" / "xx.nuc").write_text(_nuc(5900.0), encoding="latin-1")
    assert hadron_data_needed(doc, db) is True


# -- la firma de la cascada no depende de dónde están los ENDF --------------------------------------------------------

def _proton_case(folder: Path) -> tuple[Path, object]:
    folder.mkdir(parents=True)
    for f in ("caja.in", "caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / f, folder / f)
    text = (folder / "caja.in").read_text(encoding="latin-1")
    lines = text.splitlines(True)
    lines = ["SKPAR  4        [Primary particles: 1=electron, 2=photon, 3=positron, 4=proton]\n"
             if ln.startswith("SKPAR") else ln for ln in lines]
    (folder / "caja.in").write_text("".join(lines), encoding="latin-1")
    doc = parse_in_file(folder / "caja.in")
    for md in doc.model.materials:
        (folder / md.fname.strip()).write_text(WATER_MAT, encoding="latin-1")
    return folder / "caja.in", doc


def test_the_cascade_signature_does_not_depend_on_where_the_endf_are(tmp_path):
    """Revisión de 0.16: la firma de la cascada buscaba los ENDF solo junto al .in. Simulada con los de la carpeta de
    los ENDF del usuario, al copiarlos después junto al .in («Ejecutar», «Generar .mat y ENDF…») la firma cambiaba y
    la cascada no se reutilizaba (con protones, horas). Las de 0.16.0–0.16.1 se siguen reconociendo."""
    from argus.core.files import copy_new
    from argus.penhan import cascade as cs

    in_path, doc = _proton_case(tmp_path / "caso")
    mine = tmp_path / "mis_endf"
    mine.mkdir()
    for f in ("pO16_ICRU63.endf", "pO17_TENDL.endf"):
        (mine / f).write_text(f"ENDF {f}\n")
    exe = tmp_path / "penhmain.x"
    exe.write_text("#!/bin/sh\n")
    before = cs.case_signature(in_path, doc, exe, 100)
    old_before = cs.case_signature(in_path, doc, exe, 100, legacy=True)
    for f in mine.iterdir():
        copy_new(f, in_path.parent / f.name)
    after, old_after = cs.signatures(in_path, doc, exe, 100)
    assert before == after
    assert old_before != old_after  # (la de antes cambiaba)
    assert old_after in cs.signatures(in_path, doc, exe, 100)  # una cascada de 0.16.1 con los ENDF ahí: es esta
    mat = in_path.parent / doc.model.materials[0].fname.strip()
    mat.write_text(WATER_MAT.replace("1.00000000E+00", "1.05000000E+00"), encoding="latin-1")
    os.utime(mat, (1, 1))
    assert cs.case_signature(in_path, doc, exe, 100) != after  # otro .mat: otra cascada


# -- ENDF con otras mayúsculas ----------------------------------------------------------------------------------------

def test_endf_files_with_other_capitals_are_found(tmp_path):
    """Revisión de 0.16: en Linux, «PO16_ICRU63.ENDF» (copiado de Windows o de un .zip) no se encontraba aunque el
    diálogo lo contaba como ENDF. Se encuentra y se copia con el nombre que dice el .mat (el que abre penhmain)."""
    from argus.penhan import matgen as mg
    from argus.penhan.simfolder import execute, plan_folder

    case, mine = tmp_path / "caso", tmp_path / "mis_endf"
    case.mkdir()
    mine.mkdir()
    (case / "agua.mat").write_text(WATER_MAT, encoding="latin-1")
    (mine / "PO16_ICRU63.ENDF").write_text("ENDF 16\n")
    (mine / "po17_tendl.endf").write_text("ENDF 17\n")
    text = "TITLE  x\nSKPAR  4\nSENERG 1e8\nMFNAME agua.mat\nMSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN c.geo\n"
    doc = parse_in_text(text, str(case / "caso.in"))
    jobs = {j.name: j for j in mg.plan_endf(doc, case, None, mine)}
    assert jobs["pO16_ICRU63.endf"].source == mine / "PO16_ICRU63.ENDF"
    assert jobs["pO17_TENDL.endf"].source == mine / "po17_tendl.endf"
    dest = tmp_path / "sim"
    plan = plan_folder(case / "caso.in", doc, dest, endf_dir=mine)
    endf = {it.name: it for it in plan.items if it.kind == "endf"}
    assert endf["pO16_ICRU63.endf"].source == mine / "PO16_ICRU63.ENDF"
    execute(plan)
    assert (dest / "pO16_ICRU63.endf").read_text() == "ENDF 16\n"


def test_missing_files_are_said_in_lower_case_and_the_endf_with_where_to_choose_them(tmp_path):
    """Revisión de 0.16: «falta pO16_ICRU63.endf (Datos ENDF)», con la mayúscula a media frase y sin decir dónde se
    elige la carpeta de los ENDF."""
    from argus.penhan import run as rn

    in_path = _radioactive_case(tmp_path / "caso", 5900.0)
    (in_path.parent / "xx.nuc").unlink()
    doc = parse_in_file(in_path)
    problems = rn.plan_run(in_path, doc, penhmain=None, database=None, endf_dir=None).problems
    assert "falta xx.nuc (nucleido)" in problems
    in_path, doc = _proton_case(tmp_path / "protones")
    problems = rn.plan_run(in_path, doc, penhmain=None, database=None, endf_dir=None).problems
    endf = [p for p in problems if "ENDF" in p]
    assert len(endf) == 1 and endf[0].startswith("faltan 2 ficheros ENDF (pO16_ICRU63.endf, pO17_TENDL.endf)")
    assert "Opciones → Programas de penhan" in endf[0] and "Carpeta de los ENDF" in endf[0]


# -- segunda pasada (0.16.2): la caja de la fuente extensa ------------------------------------------------------------

def _geo(path: Path, text: str):
    from argus.session import open_geometry

    path.write_text(text, encoding="latin-1")
    s = open_geometry(path)
    assert s.ok
    return s


_SEP = "0" * 64 + "\n"
FAR_GEO = ("Esfera grande y detector lejano\n\n" + _SEP
           + "SURFACE (  S1)   Esfera R=15\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+1.500000000000000E+01,   0)\n"
           "Y-SCALE=(+1.500000000000000E+01,   0)\nZ-SCALE=(+1.500000000000000E+01,   0)\n" + _SEP
           + "SURFACE (  S2)   Esfera R=0.3 en x=80\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+3.000000000000000E-01,   0)\n"
           "Y-SCALE=(+3.000000000000000E-01,   0)\nZ-SCALE=(+3.000000000000000E-01,   0)\n"
           "X-SHIFT=(+8.000000000000000E+01,   0)\n" + _SEP
           + "BODY    (AGUA)   Maniqui\nMATERIAL(   1)\nSURFACE (  S1), SIDE POINTER=(-1)\n" + _SEP
           + "BODY    (DETE)   Detector\nMATERIAL(   1)\nSURFACE (  S2), SIDE POINTER=(-1)\n" + _SEP
           + "END      0000000000000000000000000000000000000000000000000000000\n")
ROD_GEO = ("Esfera con una varilla fina que sale de ella\n\n" + _SEP
           + "SURFACE (  C1)   Cilindro en x, r=0.05, eje en y=0.5\nINDICES=( 0, 1, 1, 0,-1)\n"
           "Y-SCALE=(+5.000000000000000E-02,   0)\nZ-SCALE=(+5.000000000000000E-02,   0)\n"
           "Y-SHIFT=(+5.000000000000000E-01,   0)\n" + _SEP
           + "SURFACE (  X0)   Plano x=0\nINDICES=( 0, 0, 0, 0, 0)\n     AX=(+1.000000000000000E+00,   0)\n" + _SEP
           + "SURFACE (  X6)   Plano x=60\nINDICES=( 0, 0, 0, 0, 0)\n     AX=(+1.000000000000000E+00,   0)\n"
           "     A0=(-6.000000000000000E+01,   0)\n" + _SEP
           + "SURFACE (  S1)   Esfera R=15\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+1.500000000000000E+01,   0)\n"
           "Y-SCALE=(+1.500000000000000E+01,   0)\nZ-SCALE=(+1.500000000000000E+01,   0)\n" + _SEP
           + "BODY    (VARI)   Varilla\nMATERIAL(   2)\nSURFACE (  C1), SIDE POINTER=(-1)\n"
           "SURFACE (  X0), SIDE POINTER=(+1)\nSURFACE (  X6), SIDE POINTER=(-1)\n" + _SEP
           + "BODY    (AGUA)   Maniqui\nMATERIAL(   1)\nSURFACE (  S1), SIDE POINTER=(-1)\nBODY    (VARI)\n" + _SEP
           + "END      0000000000000000000000000000000000000000000000000000000\n")
ROOT_GEO = ("Modulo raiz con una esfera que asoma\n\n" + _SEP
            + "SURFACE (  S1)   Esfera R=12\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+1.200000000000000E+01,   0)\n"
            "Y-SCALE=(+1.200000000000000E+01,   0)\nZ-SCALE=(+1.200000000000000E+01,   0)\n" + _SEP
            + "BODY    (ESFE)   Esfera\nMATERIAL(   1)\nSURFACE (  S1), SIDE POINTER=(-1)\n" + _SEP
            + "SURFACE (  PX)   x\nINDICES=( 1, 0, 0, 0,-1)\nX-SCALE=(+1.000000000000000E+01,   0)\n" + _SEP
            + "SURFACE (  PY)   y\nINDICES=( 0, 1, 0, 0,-1)\nY-SCALE=(+1.000000000000000E+01,   0)\n" + _SEP
            + "SURFACE (  PZ)   z\nINDICES=( 0, 0, 1, 0,-1)\nZ-SCALE=(+1.000000000000000E+01,   0)\n" + _SEP
            + "MODULE  (CAJA)   Caja\nMATERIAL(   2)\nSURFACE (  PX), SIDE POINTER=(-1)\n"
            "SURFACE (  PY), SIDE POINTER=(-1)\nSURFACE (  PZ), SIDE POINTER=(-1)\nBODY    (ESFE)\n" + _SEP
            + "END      0000000000000000000000000000000000000000000000000000000\n")


def _all_inside(s, kb: int, lo, hi, n: int = 400_000, seed: int = 7) -> bool:
    """¿Todo lo que LOCATE sitúa en ``kb`` (puntos al azar alrededor de la caja) está en [lo, hi]?"""
    rng = np.random.default_rng(seed)
    span = np.maximum(hi - lo, 1e-9)
    pts = rng.uniform(lo - 0.3 * span, hi + 0.3 * span, (n, 3))
    mine = pts[s.locator.locate(pts)[0] == kb]
    return len(mine) > 0 and bool(np.all((mine >= lo - 1e-12) & (mine <= hi + 1e-12)))


def test_a_body_away_from_the_scene_is_not_empty(tmp_path):
    """Segunda pasada de la revisión de 0.16: la caja se buscaba en la cota del cuerpo cortada con la escena (la
    rejilla de 40³ de las vistas): un detector pequeño lejos de lo demás salía «vacío» y «Fuente extensa en ▸» no
    hacía nada. Donde la cota es finita, se busca en toda ella."""
    from argus.pengeom.extent import source_extent

    s = _geo(tmp_path / "lejos.geo", FAR_GEO)
    lo, hi, state = source_extent(s.geo, 2, s.bounds)
    assert state == "ok" and np.allclose(lo, [79.7, -0.3, -0.3]) and np.allclose(hi, [80.3, 0.3, 0.3])


def test_a_thin_rod_that_leaves_the_scene_is_found_whole(tmp_path):
    """Segunda pasada: la búsqueda solo se ampliaba si lo encontrado llegaba a 1e-6 de su borde, algo que el muestreo
    no alcanza: una varilla de x = 0 a 60 que sale de una esfera de 15 cm se quedaba en x ≤ 19,86 («sin cota») y solo
    emitía un tercio de ella."""
    from argus.pengeom.extent import source_extent

    s = _geo(tmp_path / "varilla.geo", ROD_GEO)
    lo, hi, state = source_extent(s.geo, 1, s.bounds)
    assert state == "ok" and np.allclose(lo, [0.0, 0.45, -0.05]) and np.allclose(hi, [60.0, 0.55, 0.05])


def test_a_daughter_sticking_out_of_the_root_module(tmp_path):
    """Segunda pasada: sin la envolvente de PENGEOM, LOCATE no mira las superficies del módulo raíz antes de buscar en
    sus hijas: una esfera de r = 12 en una caja de ±10 (una geometría mal hecha) también está fuera de la caja, y la
    caja de la fuente no puede cortarla ahí."""
    from argus.pengeom.extent import locate_bound, source_extent

    s = _geo(tmp_path / "raiz.geo", ROOT_GEO)
    assert s.locator.locate(np.array([[11.0, 0.0, 0.0]]))[0][0] == 1
    lo, hi, state = source_extent(s.geo, 1, s.bounds)
    assert state == "ok" and np.allclose(lo, -12.0) and np.allclose(hi, 12.0)
    assert np.allclose(locate_bound(s.geo, 1)[1], 12.0)  # (también la cota, que sirve para saber qué cubre la caja)


def test_the_pengeomjar_glass_and_quadric():
    """Segunda pasada: la base y el pie de la copa de PenGeomJar salían «vacíos» (LOCATE encuentra unos mil puntos en
    cada uno); la cuádrica de dos hojas no tiene cota (la caja es la parte cercana a la geometría)."""
    from argus.pengeom.extent import source_extent
    from argus.session import open_geometry

    s = open_geometry(Path(__file__).parent / "corpus" / "pengeomjar" / "glass")
    for kb in (4, 5):
        lo, hi, state = source_extent(s.geo, kb, s.bounds)
        assert state == "ok" and _all_inside(s, kb, lo, hi, 200_000)
    s = open_geometry(Path(__file__).parent / "corpus" / "pengeomjar" / "quadric")
    assert source_extent(s.geo, 1, s.bounds)[2] == "sin cota"


def test_a_thin_extreme_of_an_arm_bone_is_inside():
    """Segunda pasada: en los maniquíes de 1, 5, 10 y 15 años algunos huesos de los brazos tenían un extremo estrecho
    fuera de la caja (en el de 10 años, el cuerpo 28 llega a y = -2,263 y la caja se quedaba en -2,199): por fuera de
    cada cara se busca también en franjas finas de toda su sección."""
    from argus.pengeom.extent import source_extent
    from argus.session import open_geometry

    s = open_geometry(Path(__file__).parent / "corpus" / "ornl" / "10y.geo")
    lo, hi, state = source_extent(s.geo, 28, s.bounds)
    assert state == "ok" and lo[1] <= -2.263 and _all_inside(s, 28, lo, hi)


# -- segunda pasada: la firma de antes con una fuente radiactiva sin alfas --------------------------------------------

def test_the_old_signature_of_a_radioactive_source_without_alphas(tmp_path):
    """Segunda pasada: con un nucleido sin alfas, ARGUS 0.16.2 sabe que penhmain no lee los ENDF y ya no los cuenta;
    la firma «de antes» tampoco los contaba y no era la de 0.16.0–0.16.1 (que, sin mirar el .nuc, contaba los que
    hubiera junto al .in): esas cascadas se simulaban otra vez."""
    from argus.penhan import cascade as cs

    in_path = _radioactive_case(tmp_path / "caso", None)
    for f in ("pO16_ICRU63.endf", "pO17_TENDL.endf"):
        (in_path.parent / f).write_text(f"ENDF {f}\n")
    doc = parse_in_file(in_path)
    assert not any(p.startswith(b"endf:") for p in cs._file_parts(in_path, doc))
    old = cs._file_parts(in_path, doc, endf=True)
    assert sum(p.startswith(b"endf:") for p in old) == 2
    new, legacy = cs.signatures(in_path, doc, None, 100)
    assert new != legacy


# -- segunda pasada: lo guardado de una cascada que se está leyendo ---------------------------------------------------

def test_a_cascade_read_twice_at_once_stays_kept_until_both_finish(tmp_path, monkeypatch):
    """Segunda pasada: mientras se lee una cascada lo suyo no se expulsa, pero si dos la leían a la vez (el visor y la
    ventana de resultados), la primera en acabar la soltaba para las dos. Al acabar, lo que sobra se va al guardar
    otra cosa (volver a abrirla no lee nada)."""
    from argus.results import shower as sh

    monkeypatch.setattr(sh, "_CACHE", {})
    monkeypatch.setattr(sh, "_PINNED", {})
    monkeypatch.setattr(sh, "CACHE_BYTES", 1)
    monkeypatch.setattr(sh, "_set_bytes", lambda _s: 10)
    folder = tmp_path / "cascada"
    runs = [str(folder / f"run-{i:02d}") for i in range(1, 4)]
    with sh.keep_cascade(folder):
        with sh.keep_cascade(folder):  # (el visor y la ventana de resultados a la vez)
            for r in runs:
                sh._remember(r, ((), None, object()))
        sh._remember(str(tmp_path / "otra"), ((), None, object()))
        assert len(sh._CACHE) == 4  # la otra lectura sigue: lo suyo no se va
    assert not sh._PINNED and len(sh._CACHE) == 4
    sh._remember(str(tmp_path / "otra2"), ((), None, object()))
    assert list(sh._CACHE) == [str(tmp_path / "otra2")]  # al guardar otra cosa, lo que sobra se va


# -- segunda pasada: materiales personalizados -------------------------------------------------------------------------

def _library_json(path: Path, data: dict) -> None:
    import json

    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_custom_material_with_an_infinite_z_does_not_stop_argus(tmp_path):
    """Segunda pasada: un Z escrito a mano como 1e999 (infinito) daba OverflowError al leer la biblioteca y ARGUS no
    arrancaba. Se lista como ilegible y se vuelve a escribir tal cual."""
    import json

    from argus.penhan.composition import Composition
    from argus.penhan.library import KIND, Library, LibraryMaterial

    p = tmp_path / "lib.json"
    p.write_text('{"kind": "' + KIND + '", "version": 1, "materials": [{"name": "Raro", "elements": [[1e999, 1]], '
                 '"by": "atomos", "density": 1.0}]}', encoding="utf-8")
    lib = Library(p)
    assert lib.items == [] and lib.unreadable is None and len(lib.problems) == 1
    lib.put(LibraryMaterial("Agua", Composition([(1, 2.0), (8, 1.0)], density=1.0)))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert [m["name"] for m in data["materials"]] == ["Agua", "Raro"]


def test_what_a_newer_argus_wrote_is_kept(tmp_path):
    """Segunda pasada: lo de fuera de la lista de materiales («groups» de una versión nueva) se perdía al guardar, y un
    color guardado de otra forma pasaba a ser su texto de Python."""
    import json

    from argus.penhan.composition import Composition
    from argus.penhan.library import KIND, Library, LibraryMaterial

    p = tmp_path / "lib.json"
    color = {"rgb": "#112233", "alpha": 0.5}
    lib = Library(p)
    lib.put(LibraryMaterial("Uno", Composition([(1, 2.0), (8, 1.0)], density=1.0)))
    data = json.loads(p.read_text(encoding="utf-8"))
    data["version"], data["groups"] = 2, [{"name": "Aleaciones", "members": ["Uno"]}]
    data["materials"][0]["color"] = color
    _library_json(p, data)
    lib = Library(p)
    assert lib.find("Uno").color is None
    lib.put(LibraryMaterial("Dos", Composition([(82, 1.0)], density=11.35)))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["kind"] == KIND and data["version"] == 2 and data["groups"][0]["name"] == "Aleaciones"
    assert data["materials"][0]["color"] == color


def test_a_change_of_the_same_size_by_another_argus_is_seen(tmp_path):
    """Segunda pasada: los cambios de otro ARGUS se notaban por la fecha y el tamaño; con fechas poco finas (FAT,
    algunas unidades de red), un cambio del mismo tamaño no se notaba y se perdía. Cada escritura es un fichero nuevo:
    el nodo lo dice."""
    from argus.penhan.composition import Composition
    from argus.penhan.library import Library, LibraryMaterial

    p = tmp_path / "lib.json"
    water = Composition([(1, 2.0), (8, 1.0)], density=1.0)
    a = Library(p)
    a.put(LibraryMaterial("Uno", water, "#111111"))
    st = os.stat(p)
    b = Library(p)
    b.put(LibraryMaterial("Uno", water, "#999999"), replace="Uno")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # la misma fecha (y el mismo tamaño)
    assert os.stat(p).st_size == st.st_size
    a.put(LibraryMaterial("Dos", Composition([(82, 1.0)], density=11.35)))
    assert Library(p).find("Uno").color == "#999999"


@pytest.mark.skipif(os.name == "nt", reason="permisos de POSIX")
def test_an_export_can_be_read_by_others(tmp_path):
    """Segunda pasada: «Exportar…» escribía el fichero con permisos 0600 (solo su dueño lo podía leer): para pasarlo
    a un compañero. Ahora, los de un fichero nuevo (la máscara del usuario) o los del que se sustituye."""
    import stat

    from argus.penhan.composition import Composition
    from argus.penhan.library import LibraryMaterial, write_file

    old = os.umask(0o022)
    try:
        out = tmp_path / "exportado.json"
        write_file(out, [LibraryMaterial("Uno", Composition([(1, 2.0), (8, 1.0)], density=1.0))])
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o644
        os.chmod(out, 0o640)
        write_file(out, [])
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o640
    finally:
        os.umask(old)


# -- segunda pasada: materiales del .in -------------------------------------------------------------------------------

def test_a_list_number_that_is_not_in_the_list_can_be_assigned(tmp_path):
    """Segunda pasada: «Agua líquida ICRU37 (PENELOPE 281)» con una lista de 280: «Generar .mat y ENDF…» mandaba a
    «Asignar materiales…», que decía que no había nada que asignar (y la ayuda del panel, «n.º 281»)."""
    from argus.penhan.materials import known_number, materials_table, unidentified
    from argus.ui.add_material_dialog import describe_origin

    mlist = [(278, "Water, liquid"), (280, "Xylene")]
    text = ("TITLE  x\nSKPAR  1\nSENERG 1e6\n"
            "MFNAME agua37.mat                     [Agua líquida ICRU37 (PENELOPE 281)]\n"
            "MSIMPA 1E5 1E4 1E5 0.05 0.05 1E5 1E4\nGEOMFN c.geo\n")
    doc = parse_in_text(text, str(tmp_path / "caso.in"))
    rows = materials_table(doc, None, tmp_path)
    assert [r.fname for r in unidentified(rows, mlist)] == ["agua37.mat"]
    assert unidentified(rows, []) == []  # sin lista, el número del nombre vale (penhmat no la necesita)
    assert known_number(rows[0].description, mlist) is None and known_number(rows[0].description, []) == 281
    assert "no está en su lista de PENELOPE" in describe_origin(rows[0], mlist)


def test_a_radioactive_source_without_alphas_in_the_materials_txt(tmp_path):
    """Segunda pasada: con un nucleido sin alfas (su .nuc en database/nucleide-files), materiales.txt decía «Con
    electrones, fotones o positrones…» y, a la vez, que al .mat le faltan los datos de hadrones."""
    from argus.penhan.materials import hadron_data_needed, materials_table, materials_txt, no_endf_reason

    in_path = _radioactive_case(tmp_path / "caso", None, nuc_here=False)
    db = tmp_path / "database"
    (db / "nucleide-files").mkdir(parents=True)
    (db / "nucleide-files" / "xx.nuc").write_text(_nuc(None), encoding="latin-1")
    (in_path.parent / "agua.mat").write_text(" PENELOPE (v. 2018)  Material data file\n Material: WATER, LIQUID  "
                                             "(278)\n Mass density = 1.00000000E+00 g/cm**3\n", encoding="latin-1")
    doc = parse_in_file(in_path)
    rows = materials_table(doc, None, in_path.parent, db)
    assert not rows[0].problems
    hadrons = hadron_data_needed(doc, db)
    assert hadrons is False
    text = materials_txt(rows, "caso.in", hadrons=hadrons, no_endf=no_endf_reason(doc))
    assert "no emite alfas de más de 100 keV" in text and "Con electrones, fotones o positrones" not in text


def test_air_mat_proposes_dry_air():
    """Segunda pasada: «air.mat» ya no proponía nada (empate entre el aire seco y el plástico C-552 «equivalente a
    aire»); «aire.mat» sí. Ahora «air» es otro nombre del aire seco, como «water» del agua."""
    from argus.penhan.material_names import best_guess, penelope_entries

    entries = penelope_entries([(104, "Air, dry (near sea level)"), (126, "C-552 air-equivalent plastic"),
                                (278, "Water, liquid")])
    assert best_guess(["air.mat", ""], entries).number == 104
    assert best_guess(["aire.mat", ""], entries).number == 104


def test_one_missing_endf_is_said_in_singular(tmp_path):
    """Segunda pasada: «falta 1 fichero ENDF (…): diga dónde están»."""
    from argus.penhan import run as rn

    in_path, doc = _proton_case(tmp_path / "protones")
    (in_path.parent / "pO17_TENDL.endf").write_text("ENDF\n")
    problems = rn.plan_run(in_path, doc, penhmain=None, database=None, endf_dir=None).problems
    endf = [p for p in problems if "ENDF" in p]
    assert endf[0].startswith("falta 1 fichero ENDF (pO16_ICRU63.endf): diga dónde está en Opciones")
