"""Duodécima revisión (0.17.4): los volcados de penhmain (RESUME, DUMPTO, DUMPP).

Lo que hace penhmain con el volcado lo decide ahora un solo sitio (``penhan.resume``) y lo dicen igual los mensajes
del .in, «Ejecutar», «Trabajo» y las vistas. Cada prueba falla con 0.17.3 (ADR-026). Los casos son copias del caso
``caja`` del corpus de resultados con volcados escritos como los escribe penhmain 2025 (``test_matrix_review6``)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.core.diagnostics import Severity
from argus.penhan import run as rn
from argus.penhan import sections as sc
from argus.penhan.dumpfile import parse_dump_text, read_dump, tally_differences
from argus.penhan.inputfile import parse_in_file, parse_in_text, write_in_text
from argus.penhan.validate import validate_case
from test_matrix_review6 import CAJA, _ints, _reals, full_dump

TITLE = "ARGUS corpus: caja"


def dump(*, showers: float = 1.0e4, psf: tuple[int, bool] | None = None, kparp: int | None = None,
         single: float | None = None, **kw) -> str:
    """Un volcado de caja.in (``full_dump``) con otras lluvias, otro espacio de fases (NPSN, LPSF), otra partícula o
    una energía única (``single``, sin espectro)."""
    lines = full_dump(**kw).split("\n")
    lines[0] = _reals([showers, 12.5, 12.0])
    if psf is not None:
        lines[3] = _ints([psf[0]]) + _reals([0.0]) + (" T" if psf[1] else " F")
    if kparp is not None:
        lines[4] = _ints([kparp])
    if single is not None:
        lines[5:9] = [_ints([0]) + " F F", _reals([single]), _reals([1.0]), _reals([showers])]
        lines[9] = _reals([single, 0, 0, 0, 0, 0, 0]) + _ints([0, 0])
    return "\n".join(lines)


def case(tmp_path: Path, edits: tuple[tuple[str, str], ...] = (), resume: str | None = "dump.dmp",
         dump_text: str | None = None, name: str = "dump.dmp") -> tuple[Path, object]:
    """caja.in en una carpeta nueva, con ``edits`` (pares de texto) y RESUME (y su volcado, si se da)."""
    d = tmp_path / "caso"
    d.mkdir(parents=True, exist_ok=True)
    for n in ("caja.geo", "mcs-null.mcs"):
        shutil.copy(CAJA / n, d / n)
    text = (CAJA / "caja.in").read_text(encoding="latin-1")
    for a, b in edits:
        assert a in text, a
        text = text.replace(a, b)
    if resume is not None:
        text = text.replace("DUMPTO dump.dmp", f"RESUME {resume}\nDUMPTO dump.dmp")
    (d / "caja.in").write_text(text, encoding="latin-1")
    if dump_text is not None:
        (d / name).write_text(dump_text)
    return d / "caja.in", parse_in_file(d / "caja.in")


def messages(doc, folder, start: str = "RESUME") -> list:
    return [m for m in validate_case(doc, None, folder) if m.message.startswith(start)]


def outcome(in_path: Path, doc):
    from argus.penhan import resume as rs  # (aquí: con 0.17.3, que no lo tiene, fallan solo las que lo usan)

    out = rs.resume_outcome(doc, in_path.parent)
    assert out is not None
    return out


# ---------------------------------------------------------------------------------------------------------------------
# V2: un fichero que no es un volcado (penhmain escribe «Empty.» encima)
# ---------------------------------------------------------------------------------------------------------------------

MAT_HEAD = " PENELOPE (v. 2018)  Material data file ...............\n Material: WATER, LIQUID (278)\n Mass density =" \
           "  1.00000000E+00 g/cm**3\n Number of elements in the molecule =  2\n"


@pytest.mark.parametrize("what", ["mat", "in", "psf"])
def test_resume_of_a_file_that_is_not_a_dump_is_an_error(tmp_path, what):
    """penhmain no lo puede leer como volcado, escribe «Empty.» encima y empieza desde cero: se perdería un .mat, el
    propio .in o un espacio de fases. Antes: «no contiene una simulación guardada» (información) y «está vacío»."""
    name = {"mat": "agua.mat", "in": "caja.in", "psf": "fase.psf"}[what]
    text = {"mat": MAT_HEAD, "psf": " #  Results from PENHMAIN. Phase-space file of detector no.  1\n #\n"
            " 2  6.61700E+05  0.00000E+00  0.00000E+00  1.00000E+00  0.00000E+00  0.00000E+00  1.00000E+00  1.00000E+00"
            "  0 0 0 1 1\n"}.get(what)
    in_path, doc = case(tmp_path, resume=name, dump_text=text, name=name)
    info = read_dump(in_path.parent / name)
    assert info.state == "foreign" and "empieza por" in info.message
    out = outcome(in_path, doc)
    assert out.action == "destroy"
    m = messages(doc, in_path.parent)
    assert m and m[0].severity == Severity.ERROR and "escribiría «Empty.» encima" in m[0].message
    plan = rn.plan_run(in_path, doc, penhmain=None)
    assert any("escribiría «Empty.» encima" in p for p in plan.problems) and plan.previous == []
    assert "Empty." in plan.resume_error


def test_a_binary_file_is_not_read_line_by_line(tmp_path):
    (tmp_path / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0" + bytes(range(256)) * 100)
    info = read_dump(tmp_path / "foto.png")
    assert info.state == "foreign" and "binario" in info.message


@pytest.mark.parametrize("text", ["", "   Empty.\n", "Empty file. Initialisation of MultiRuns.\n", "\n\n",
                                  "   300.00000000000000        4.6\n"])
def test_what_penhmain_leaves_is_still_an_empty_dump(tmp_path, text):
    """(Control: como en 0.17.3.) Lo que deja penhmain, o una primera línea a medias: no hay nada que perder."""
    in_path, doc = case(tmp_path, dump_text=text)
    assert read_dump(in_path.parent / "dump.dmp").state == "empty"
    m = messages(doc, in_path.parent)
    assert m and m[0].severity == Severity.INFO and "desde cero" in m[0].message


@pytest.mark.parametrize("target, why", [("caja.geo", "lee penhmain"), ("mcs-null.mcs", "lee penhmain"),
                                         ("penhmain-res.dat", "el resumen"), ("depth-dose.dat", "resultados"),
                                         ("geometry.rep", "informe")])
def test_dumpto_onto_an_input_or_a_result_is_an_error(tmp_path, target, why):
    """DUMPTO tanque.geo: penhmain sustituía la geometría por el volcado sin que ARGUS dijera nada; DUMPTO
    penhmain-res.dat: el resumen se escribe encima del volcado."""
    in_path, doc = case(tmp_path, (("DUMPTO dump.dmp", f"DUMPTO {target}"),), resume=None)
    errs = [m for m in validate_case(doc, None, in_path.parent) if m.severity == Severity.ERROR and "DUMPTO" in
            m.message]
    assert errs and why in errs[0].message, [m.message for m in validate_case(doc, None, in_path.parent)]
    plan = rn.plan_run(in_path, doc, penhmain=None)
    assert any("DUMPTO" in p for p in plan.hard) == (why == "lee penhmain")


def test_dumpto_onto_another_file_that_is_not_a_dump_is_said(qtbot, tmp_path):
    in_path, doc = case(tmp_path, (("DUMPTO dump.dmp", "DUMPTO notas.txt"),), resume=None)
    (in_path.parent / "notas.txt").write_text("unas notas del usuario\nque no son un volcado\n")
    from argus.ui.sim_outputs import JobSection

    sec = JobSection()
    qtbot.addWidget(sec)
    sec.set_context(doc, None, in_path.parent)
    assert "no es un volcado de penhmain" in sec.dumpto_state.text()


def test_dumpto_in_a_missing_folder_is_an_error(tmp_path):
    """penhmain simula entera la simulación y se detiene al escribir el primer volcado, sin ningún resultado."""
    in_path, doc = case(tmp_path, (("DUMPTO dump.dmp", "DUMPTO sub/dump.dmp"),), resume=None)
    errs = [m for m in validate_case(doc, None, in_path.parent) if "DUMPTO" in m.message]
    assert errs and errs[0].severity == Severity.ERROR and "sin escribir ningún resultado" in errs[0].message


# ---------------------------------------------------------------------------------------------------------------------
# V3, V20, V12: volcados con los que penhmain se detiene
# ---------------------------------------------------------------------------------------------------------------------

def _cut(text: str, keep: int) -> str:
    return "\n".join(text.split("\n")[:keep]) + "\n"


@pytest.mark.parametrize("kind", ["cortado", "formato", "una-linea", "carpeta", "titulo", "subcarpeta"])
def test_a_dump_that_stops_penhmain_is_said_the_same_in_ejecutar(tmp_path, kind):
    """«Ejecutar» decía «penhmain empieza desde cero» (y ofrecía mover los resultados) con un volcado dañado, cortado,
    que es una carpeta o de otro título; penhmain se detiene (comprobado con el penhmain real: IRETRN 3112 o 3113,
    End of file, Cannot open file)."""
    good = dump()
    text = {"cortado": _cut(good, 40), "formato": good.replace("   123456789", "   abc", 1),
            "una-linea": _cut(good, 1), "titulo": dump(title="Otra simulación")}.get(kind)
    name = "sub/dump.dmp" if kind == "subcarpeta" else "dump.dmp"
    in_path, doc = case(tmp_path, resume=name, dump_text=text if kind not in ("carpeta", "subcarpeta") else None)
    if kind == "carpeta":
        (in_path.parent / "dump.dmp").mkdir()
    for n in ("penhmain-res.dat", "depth-dose.dat"):
        shutil.copy(CAJA / n, in_path.parent / n)
    out = outcome(in_path, doc)
    assert out.action == "stop", (out.action, out.why)
    m = messages(doc, in_path.parent)
    assert m and m[0].severity == Severity.ERROR
    text_m = m[0].message
    assert "Errno" not in text_m and "/" + str(in_path.parent) not in text_m and "invalid literal" not in text_m
    if kind == "titulo":
        assert m[0].code == 3112
    if kind == "formato":
        assert m[0].code == 3113 and "«abc»" in text_m
    if kind == "carpeta":
        assert "es una carpeta" in text_m and "no es un volcado" not in text_m
    if kind == "una-linea":
        assert "se acaba tras la primera línea" in text_m and "End of file" in text_m
    plan = rn.plan_run(in_path, doc, penhmain=None)
    assert plan.resume is None and plan.resume_missing is None and "se detendría" in plan.resume_error
    assert plan.previous == []  # penhmain no simula: no hay nada que guardar


def test_resume_in_a_missing_folder_is_in_the_simulation_folder_plan(tmp_path):
    from argus.penhan.simfolder import plan_folder

    in_path, doc = case(tmp_path, resume="falta/dump.dmp")
    plan = plan_folder(in_path, doc, tmp_path / "sim")
    it = next(i for i in plan.items if i.kind == "dump")
    assert it.status == "missing" and "se detendría" in it.note
    assert any("falta/dump.dmp" in n for n in plan.notes)
    in_path2, doc2 = case(tmp_path / "otra", resume="dump.dmp")
    it2 = next(i for i in plan_folder(in_path2, doc2, tmp_path / "sim2").items if i.kind == "dump")
    assert it2.note == "no está: penhmain empezará desde cero"


def test_python_text_does_not_reach_the_messages():
    info = parse_dump_text(dump().replace("   123456789", "   abc", 1))
    assert info.problem == "format" and "«abc»" in info.message and "línea 3" in info.message
    assert "invalid literal" not in info.message and "could not convert" not in info.message
    info = parse_dump_text(dump().replace("           2 F T", "           2 F T /", 1))
    assert "barra" not in info.message or "una barra «/»" in info.message


# ---------------------------------------------------------------------------------------------------------------------
# V10: sin «*** END ***» y contadores dañados
# ---------------------------------------------------------------------------------------------------------------------

def test_a_complete_dump_without_its_last_line_can_be_resumed():
    """PMRDR no lee «*** END ***»: penhmain reanuda un volcado completo sin ella (comprobado: 3000 lluvias). Antes era
    un error («está incompleto»)."""
    whole = dump().replace("   *** END ***", "")
    info = parse_dump_text(whole)
    assert info.usable and info.no_end
    cut = _cut(whole, len(whole.split("\n")) - 5)
    assert parse_dump_text(cut).problem == "truncated"


@pytest.mark.parametrize("where", ["henand", "dosis"])
def test_a_damaged_counter_stops_penhmain(where):
    """HENANR, IMDETR… y DOSER leen sin ERR=: un valor que no es un número detiene penhmain («Bad integer», «Bad
    real number»). Antes, «continuará la simulación guardada»."""
    lines = dump().split("\n")
    if where == "henand":
        i = 29  # NE, EL, EU… de HENAND (tras NBODY y los cuatro de TDEBO…)
        assert lines[i].startswith("         100")
        lines[i] = lines[i].replace("         100", "         1x0", 1)
    else:
        i = len(lines) - 4  # un contador de la dosis (o del proceso externo), cerca del final
        lines[i] = lines[i].replace("0.0000000000000000E+00", "1.0x00", 1)
    info = parse_dump_text("\n".join(lines))
    assert not info.usable and (info.problem, info.problem_in) == ("format", "results"), info.message


# ---------------------------------------------------------------------------------------------------------------------
# V1: un detector de impacto que escribe espacio de fases (IPSF 1)
# ---------------------------------------------------------------------------------------------------------------------

def test_resume_with_a_phase_space_detector_cannot_go_on(tmp_path):
    """penhmain relee el espacio de fases con RDPSF y, con gfortran, se detiene al escribir los primeros resultados
    tras reanudar («Sequential READ or WRITE not allowed after EOF marker»): se pierde todo lo simulado. Antes:
    «penhmain continuará la simulación guardada»."""
    in_path, doc = case(tmp_path, dump_text=dump(rwrite=402.0))
    (in_path.parent / "psf-impdet.dat").write_text(" #  Results from PENHMAIN.\n #\n")
    m = messages(doc, in_path.parent)
    assert m[0].severity == Severity.ERROR and "Sequential READ or WRITE" in m[0].message, m[0].message
    plan = rn.plan_run(in_path, doc, penhmain=None)
    assert "se detendría" in plan.resume_error and plan.resume is None
    out = outcome(in_path, doc)
    assert out.action == "stop" and "IPSF 1" in out.why and "IPSF 0" in out.detail
    # Con IPSF 0 en ese detector, sí sigue (el espacio de fases se queda como estaba).
    in2, doc2 = case(tmp_path / "b", (("IMPDET 1e3 7e5 70 1 1", "IMPDET 1e3 7e5 70 0 1"),),
                     dump_text=dump(rwrite=402.0))
    assert outcome(in2, doc2).action == "continue"
    # Sin partículas escritas aún (RWRITE 0), penhmain no relee nada.
    in3, doc3 = case(tmp_path / "c", dump_text=dump(rwrite=0.0))
    assert outcome(in3, doc3).action == "continue"


# ---------------------------------------------------------------------------------------------------------------------
# V7: espacios de fases de entrada (NPSN, LPSF del volcado)
# ---------------------------------------------------------------------------------------------------------------------

PSF_IN = (("SKPAR  2              [Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]\n", ""),
          ("SLINES 6.617E5 0.851                    [Line energy and probability]\n", ""),
          ("SLINES 3.2E4 0.07                       [Line energy and probability]\n", ""),
          ("[Conical beam; angles in deg]\n", "[Conical beam; angles in deg]\nIPSFN  fase1.psf\nIPSFN  fase2.psf\n"))


def test_phase_space_sources_and_the_dump(tmp_path):
    """Con IPSFN, penhmain toma del volcado por qué fichero va (NPSN) y si la fuente era un espacio de fases (LPSF):
    ya leídos todos, «The simulation was already completed»; volcado sin IPSFN y .in con ellos, Segmentation fault;
    volcado con IPSFN y .in sin ellos, también la da por terminada (ARGUS decía «continuará» en los tres)."""
    in_path, doc = case(tmp_path / "a", PSF_IN, dump_text=dump(psf=(3, True)))
    m = messages(doc, in_path.parent)
    assert "ya está terminada" in m[0].message and "continuará" not in m[0].message, m[0].message
    out = outcome(in_path, doc)
    assert out.action == "done" and "espacios de fases" in out.why
    in2, doc2 = case(tmp_path / "b", PSF_IN, dump_text=dump(psf=(0, False)))
    out2 = outcome(in2, doc2)
    assert out2.action == "stop" and "Segmentation fault" in out2.detail
    in3, doc3 = case(tmp_path / "c", dump_text=dump(psf=(1, True)))
    out3 = outcome(in3, doc3)
    assert out3.action == "done" and "el .in no tiene ninguno" in out3.why
    # A medias (NPSN 2 de 2): sigue, y la energía (EINITIAL de un volcado de espacio de fases) no se compara.
    in4, doc4 = case(tmp_path / "d", PSF_IN, dump_text=dump(psf=(2, True)))
    out4 = outcome(in4, doc4)
    assert out4.action == "continue" and not [k for k, _ in out4.diffs if k == "energy"]


# ---------------------------------------------------------------------------------------------------------------------
# V8: lo demás que penhmain toma del volcado (IDCUT, edades, ADKPAR, nombres de los ficheros)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("edit, expected", [
    (("IMPDET 1e3 7e5 -50 0 2", "IMPDET 1e3 7e5 -50 0 1"), "IDCUT 2 en el volcado y 1 en el .in"),
    (("IMPDET 1e3 7e5 70 1 1", "IMPDET 1e3 7e5 70 0 2"), "no la escribiría"),
    (("IDAGEL 0 2e-9 50", "IDAGEL 0 5e-9 50"), "edades de 0 a 2e-09 s"),
    (("IDAGEL 0 2e-9 50", "IDAGEL 1e-12 2e-9 -50"), "escala logarítmica"),
    (("ADKPAR 2", "ADKPAR 1"), "cuenta fotones en el volcado y electrones en el .in"),
    (("IDBODY 'PLAN'", "IDSPC  impacto.dat\nIDBODY 'PLAN'"), "«spc-impdet-01.dat» y no «impacto.dat»"),
    (("ADKPAR 2", "ADKPAR 2\nADSPC  angular.dat"), "«spc-angdet-02.dat» y no «angular.dat»"),
])
def test_what_penhmain_takes_from_the_dump_is_compared(tmp_path, edit, expected):
    """penhmain sigue con el IDCUT, la ventana y la escala de las edades, las partículas del detector angular y los
    nombres de los ficheros del volcado (comprobado con el penhmain real); antes no se comparaban."""
    in_path, doc = case(tmp_path, (edit,), dump_text=dump(rwrite=0.0))
    m = [x.message for x in messages(doc, in_path.parent) if "toma del volcado" in x.message]
    assert m and expected in m[0], m
    out = outcome(in_path, doc)
    assert out.action == "continue"
    assert any(expected in t for t in out.tallies), out.tallies


def test_the_same_in_has_no_differences(tmp_path):
    in_path, doc = case(tmp_path, dump_text=dump())
    out = outcome(in_path, doc)
    assert out.action == "continue" and out.tallies == [] and out.diffs == []


# ---------------------------------------------------------------------------------------------------------------------
# V13, V24: ya terminada; y lo que hará penhmain con cada diferencia de la fuente
# ---------------------------------------------------------------------------------------------------------------------

def test_a_finished_simulation_is_rewritten_with_the_time_at_zero(tmp_path):
    """Con las lluvias de NSIMSH, penhmain no simula más y reescribe los resultados y el volcado con el tiempo de
    cálculo a 0 (TIMET no se suma: penhmain.f 1272). Antes: «solo volverá a escribir los mismos resultados» (y, con
    otra fuente, además, «mezclaría las dos simulaciones»)."""
    in_path, doc = case(tmp_path, (("SKPAR  2", "SKPAR  1"),), dump_text=dump(showers=2.0e4))
    m = messages(doc, in_path.parent)
    assert "tiempo de cálculo a 0" in m[0].message and "mezclaría" not in m[0].message, m[0].message
    assert outcome(in_path, doc).action == "done"
    plan = rn.plan_run(in_path, doc, penhmain=None)
    assert plan.resume == "dump.dmp" and plan.resume_done


@pytest.mark.parametrize("edits, text", [
    ((("SKPAR  2", "SKPAR  1"),), "seguirá simulando fotones, la del volcado"),
    ((("SLINES 3.2E4 0.07", "SLINES 3.2E4 0.07\nSLINES 1.2E5 0.02"),), "sortearía un espectro que no es ninguno"),
])
def test_each_source_difference_says_what_penhmain_will_do(tmp_path, edits, text):
    """penhmain toma del volcado la partícula y el número de intervalos y del .in las energías (comprobado: sigue con
    fotones; con otro número de intervalos sortea un tercer espectro). Antes, para todo, «mezclaría las dos»."""
    in_path, doc = case(tmp_path, edits, dump_text=dump())
    m = messages(doc, in_path.parent)
    assert "continuará" in m[0].message and text in m[0].message, m[0].message


def test_another_single_energy_mixes_the_two(tmp_path):
    edits = (("SLINES 6.617E5 0.851                    [Line energy and probability]\n", ""),
             ("SLINES 3.2E4 0.07                       [Line energy and probability]", "SENERG 1e6"))
    from argus.penhan.dumpfile import source_diffs

    in_path, doc = case(tmp_path, edits, dump_text=dump(single=6.617e5))
    diffs = source_diffs(read_dump(in_path.parent / "dump.dmp"), doc)
    assert [k for k, _ in diffs] == ["energy"]
    m = messages(doc, in_path.parent)
    assert "saldrán con la del .in: mezclaría las dos" in m[0].message


# ---------------------------------------------------------------------------------------------------------------------
# V14, V15, V21, V23, V27, V34
# ---------------------------------------------------------------------------------------------------------------------

def test_one_particle_and_the_decays_of_a_radioactive_source(tmp_path):
    in_path, doc = case(tmp_path, dump_text=dump(showers=1.0))
    m = messages(doc, in_path.parent)
    assert "(1 partícula ya simulada)" in m[0].message


def test_dumpto_with_a_dump_of_this_simulation_and_no_resume_is_said(tmp_path):
    """Sin RESUME, penhmain empieza desde cero y lo sustituye en el primer volcado: antes, en verde «con esta
    configuración» y sin ningún mensaje."""
    in_path, doc = case(tmp_path, resume=None, dump_text=dump())
    infos = [m for m in validate_case(doc, None, in_path.parent) if "sin RESUME" in m.message]
    assert infos and "la sustituirá en el primer volcado" in infos[0].message
    in2, doc2 = case(tmp_path / "b", resume=None, dump_text=dump(title="Otro caso"))
    other = [m.message for m in validate_case(doc2, None, in2.parent) if m.message.startswith("El volcado")]
    from argus.core.numfmt import fmt_count

    assert other and other[0].startswith(f"El volcado dump.dmp ({fmt_count(1e4)} partículas) no es de este .in "
                                         "(otro título)")


def test_resume_with_ltrack_is_warned(tmp_path):
    """Al reanudar (N ≠ 0), penhmain no escribe trayectorias: LTRACK no hace nada (penhmain.f 1275-1281)."""
    in_path, doc = case(tmp_path, dump_text=dump())
    assert any("no escribe trayectorias" in m.message for m in validate_case(doc, None, in_path.parent))
    assert outcome(in_path, doc).ltrack


def test_a_seed_pair_with_a_zero_second_seed_is_not_warned():
    """«RSEED −2 0» (lo que escribe ARGUS en paralelo y en la cascada): con un primero negativo la segunda no
    cuenta."""
    base = (CAJA / "caja.in").read_text(encoding="latin-1")
    doc = parse_in_text(base.replace("RSEED  1 1", "RSEED  -2 0"))
    assert not [m for m in validate_case(doc, None, None) if m.message.startswith("RSEED")]
    doc = parse_in_text(base.replace("RSEED  1 1", "RSEED  5 0"))
    assert [m for m in validate_case(doc, None, None) if m.message.startswith("RSEED")]


def test_the_minimum_period_has_its_unit():
    doc = parse_in_text((CAJA / "caja.in").read_text(encoding="latin-1").replace("DUMPP  30", "DUMPP  5"))
    assert any("(penhmain usa 30 s)" in m.message for m in validate_case(doc, None, None))


# ---------------------------------------------------------------------------------------------------------------------
# V5b, V17, V18: nombres con un blanco delante y dónde se escriben las líneas nuevas
# ---------------------------------------------------------------------------------------------------------------------

def test_resume_with_a_leading_blank_says_the_same_everywhere(tmp_path):
    """« dump.dmp»: penhmain no encuentra el volcado y empieza desde cero (comprobado). La comprobación del .in lo
    decía, y a la vez «continuará la simulación guardada»."""
    in_path, doc = case(tmp_path, resume=" dump.dmp", dump_text=dump())
    msgs = [m.message for m in validate_case(doc, None, in_path.parent)]
    assert not any("continuará" in m for m in msgs) and any("empieza por un espacio" in m for m in msgs), msgs
    out = outcome(in_path, doc)
    assert out.blank and out.action == "fresh"
    # Escribir el nombre sin el blanco arregla la línea (antes, se comparaban sin blancos y no cambiaba nada).
    sc.set_text(doc, "RESUME", "dump.dmp")
    assert doc.first("RESUME").text == "dump.dmp" and outcome(in_path, doc).action == "continue"


def test_an_output_name_with_a_leading_blank_says_what_penhmain_writes():
    doc = parse_in_text("TITLE  t\nDUMPTO  dump.dmp\nRESUME  dump.dmp\n")
    assert "escribirá « dump.dmp»" in doc.first("DUMPTO").warning
    assert "no encontrará el fichero" in doc.first("RESUME").warning


def test_new_lines_go_after_the_header_of_their_section():
    """RESUME, DUMPTO y DUMPP nuevos quedaban encima de «>>>>>>>> Job properties.», en la sección anterior."""
    text = (CAJA / "caja.in").read_text(encoding="latin-1").replace("DUMPTO dump.dmp", "XXXXX").replace(
        "DUMPP  30", "YYYYY")
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith(("XXXXX", "YYYYY"))) + "\n"
    doc = parse_in_text(text)
    sc.set_text(doc, "RESUME", "dump.dmp")
    lines = write_in_text(doc).splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("RESUME"))
    assert "Job properties" in lines[i - 1] or "Job properties" in lines[i - 2], lines[i - 3:i + 2]
    from argus.penhan.template import new_input_text

    doc = parse_in_text(new_input_text("caso.geo", ["agua.mat"]))
    sc.set_value(doc, "DUMPP", [60.0])
    lines = write_in_text(doc).splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("DUMPP"))
    assert "Job properties" in lines[i - 1], lines[i - 3:i + 2]


# ---------------------------------------------------------------------------------------------------------------------
# V6: el guion de «Ejecutar» con un IRETRN; V9: combinar.sh si penhmain-sum falla
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="guion de sh")
def test_the_launcher_says_when_penhmain_stopped_with_iretrn(tmp_path):
    """penhmain acaba con el código 0 tras un IRETRN: el guion decía «ha terminado; los resultados están en esta
    carpeta»."""
    exe = tmp_path / "penhmain.x"
    exe.write_text("#!/bin/sh\ncat > /dev/null\necho ' The dump file is corrupted (the TITLE does not match).'\n"
                   "echo 'IRETRN = 3112'\nexit 0\n")
    exe.chmod(0o755)
    (tmp_path / "caso.in").write_text("TITLE  t\n")
    script = rn.write_launcher(tmp_path, "sh", "caso.in", "penhmain.x")
    res = subprocess.run(["sh", str(script)], input="\n", capture_output=True, text=True, timeout=60)
    assert "se ha detenido" in res.stdout and "los resultados estan" not in res.stdout, res.stdout
    exe.write_text("#!/bin/sh\ncat > /dev/null\necho '   *** END ***'\nexit 0\n")
    res = subprocess.run(["sh", str(script)], input="\n", capture_output=True, text=True, timeout=60)
    assert "los resultados estan en esta carpeta" in res.stdout and "detenido" not in res.stdout
    bat = rn.launcher_text("bat", "caso.in", "penhmain.exe")
    assert 'findstr /c:"IRETRN =" penhmain.dat' in bat and ":argus_stopped" in bat


def test_an_old_launcher_of_argus_is_replaced_without_a_backup(tmp_path):
    """El guion de 0.17.3 (sin la comprobación de IRETRN) es de ARGUS sin retocar: se sustituye sin dejar un .bak."""
    old = rn.launcher_text("sh", "caso.in", "penhmain.x", iretrn=False)
    (tmp_path / "argus-ejecutar.sh").write_text(old)
    path = rn.write_launcher(tmp_path, "sh", "caso.in", "penhmain.x")
    assert path.read_text() == rn.launcher_text("sh", "caso.in", "penhmain.x")
    assert not list(tmp_path.glob("*.bak*"))


def _parallel(tmp_path: Path):
    from argus.penhan import batch as bt

    doc = parse_in_file(CAJA / "caja.in")
    b = bt.make_parallel(doc, CAJA / "caja.in", tmp_path / "par", 2, total_showers=100)
    return bt.combine_scripts(b)["combinar.sh"]


@pytest.mark.skipif(sys.platform == "win32", reason="guion de sh")
@pytest.mark.parametrize("how", ["ok", "inconsistent", "missing"])
def test_combine_says_when_penhmain_sum_failed(tmp_path, how):
    """penhmain-sum acaba con el código 0 aunque no combine nada («Inconsistent dump file format») y crea vacío el
    volcado que falta: combinar.sh decía «Combinadas en suma» y luego se negaba a repetir («Ya existe suma»)."""
    folder = tmp_path / "par"
    folder.mkdir()
    (folder / "combinar.sh").write_text(_parallel(tmp_path / "x"))
    for r in ("run-01", "run-02"):
        (folder / r).mkdir()
        if not (how == "missing" and r == "run-02"):
            (folder / r / "dump.dmp").write_text(dump())
    fake = tmp_path / "penhmain-sum"
    if how == "ok":
        fake.write_text("#!/bin/sh\ncat > /dev/null\necho res > penhmain-res.dat\necho\necho '  ***  END  ***'\n")
    else:
        fake.write_text("#!/bin/sh\ncat > /dev/null\necho '  ***  Inconsistent dump file format.'\n")
    fake.chmod(0o755)
    res = subprocess.run(["sh", str(folder / "combinar.sh"), str(fake)], capture_output=True, text=True, timeout=60)
    assert res.returncode == (0 if how == "ok" else 1)
    if how == "ok":
        assert "Combinadas en suma" in res.stdout and (folder / "suma" / "penhmain-res.dat").is_file()
    elif how == "inconsistent":
        assert "penhmain-sum ha fallado" in res.stdout and "Combinadas" not in res.stdout, res.stdout
        assert not (folder / "suma").exists() and list(folder.glob("suma-fallida-*"))  # se puede volver a combinar
    else:
        assert "Aun no hay un volcado completo en run-02/dump.dmp" in res.stdout and not (folder / "suma").exists()
        assert not (folder / "run-02" / "dump.dmp").exists()  # penhmain-sum no lo ha creado vacío


# ---------------------------------------------------------------------------------------------------------------------
# V11, V22, V29: el estado de la carpeta con RESUME
# ---------------------------------------------------------------------------------------------------------------------

def _res(path: Path, date: str, showers: float) -> None:
    text = (CAJA / "penhmain-res.dat").read_text(encoding="latin-1")
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if "Date and time:" in ln:
            lines[i] = "   Date and time: " + date
        if ln.strip().startswith("Simulated primary particles") or ln.strip().startswith("Number of simulated sh"):
            head = ln.split("...")[0]
            lines[i] = head + "..... " + f"{showers:13.6E}"
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")


def _log(path: Path, date: str) -> None:
    text = (CAJA / "penhmain.dat").read_text(encoding="latin-1")
    lines = text.splitlines()[:20]
    lines = ["   Date and time: " + date if "Date and time:" in ln else ln for ln in lines]
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_a_resumed_run_before_its_first_dump_is_starting(tmp_path):
    """Con RESUME siempre hay resultados de antes: mientras penhmain reanuda y no vuelca, la carpeta salía «parada»
    («¿Se ha detenido penhmain?») y «Ejecutar» no veía que ya estaba simulando."""
    from argus.results.folder import ResultsFolder

    in_path, _doc = case(tmp_path, (("NSIMSH 2e4", "NSIMSH 1e9"),), dump_text=dump())
    folder = in_path.parent
    _res(folder / "penhmain-res.dat", "19th Oct 2026. 10:00:00", 1.0e4)
    old = time.time() - 7200
    os.utime(folder / "penhmain-res.dat", (old, old))
    _log(folder / "penhmain.dat", "19th Oct 2026. 12:00:00")  # la ejecución de ahora, que aún no ha volcado
    st = ResultsFolder(folder, in_path).state()
    assert st.status == "empezando" and "reanudando" in st.text, st.text
    assert rn.running_state(folder, in_path=in_path) is not None
    # Recién abierto, el registro aún está vacío o solo tiene la cabecera, sin la fecha (gfortran no lo escribe al disco
    # hasta llenar su búfer): también es la de ahora (comprobado con el penhmain real).
    head = "\n\n   " + "*" * 62 + "\n   **   Program PENHMAIN.  Input data and run-time messages.   **\n   " + "*" * 62
    for text in ("", head + "\n"):
        (folder / "penhmain.dat").write_text(text)
        assert ResultsFolder(folder, in_path).state().status == "empezando", repr(text)


def test_a_run_that_is_starting_is_not_an_empty_folder(tmp_path):
    """Mientras penhmain se inicializa, su registro está vacío (gfortran no lo ha escrito al disco): la carpeta salía
    «No hay resultados de penhmain en esta carpeta»."""
    from argus.results.folder import ResultsFolder

    in_path, _doc = case(tmp_path, resume=None)
    (in_path.parent / "penhmain.dat").write_text("")
    assert ResultsFolder(in_path.parent, in_path).state().status == "empezando"


def test_with_resume_the_time_left_is_bounded_by_time(tmp_path):
    """TIME cuenta desde que empieza esta ejecución: con TIME 50 decía «faltan unos 4,2 días»."""
    from argus.results.folder import ResultsFolder

    in_path, _doc = case(tmp_path, (("NSIMSH 2e4", "NSIMSH 1e9"), ("TIME   2e9", "TIME   50")), dump_text=dump())
    folder = in_path.parent
    date = "19th Oct 2026. 12:00:00"
    _log(folder / "penhmain.dat", date)
    t0 = time.time() - 20
    os.utime(folder / "penhmain.dat", (t0, t0))
    _res(folder / "penhmain-res.dat", date, 2.0e4)
    st = ResultsFolder(folder, in_path).state()
    assert st.status == "simulando" and st.eta is not None and st.eta <= 31, (st.text, st.eta)
    # Mientras simula, gfortran no ha escrito aún el registro al disco (vacío o solo la cabecera, con la fecha de
    # cuando lo abrió).
    for text in ("", "\n\n   " + "*" * 62 + "\n"):
        (folder / "penhmain.dat").write_text(text)
        os.utime(folder / "penhmain.dat", (t0, t0))
        st = ResultsFolder(folder, in_path).state()
        assert st.status == "simulando" and st.eta is not None and st.eta <= 31, (st.text, st.eta)


@pytest.mark.parametrize("period, text", [(90.0, "90 s"), (150.0, "150 s"), (7200.0, "2 h"), (86400.0, "1 día")])
def test_the_dump_period_is_written_exactly(period, text):
    from argus.results.folder import _fmt_period

    assert _fmt_period(period) == text


# ---------------------------------------------------------------------------------------------------------------------
# V25: las fracciones al combinar ejecuciones, como penhmain-sum
# ---------------------------------------------------------------------------------------------------------------------

def test_combined_fractions_have_the_uncertainty_of_penhmain_sum():
    """penhmain escribe la incertidumbre de la fracción que sale como ERR1 + ERR2 (primarias y secundarias): ARGUS la
    combinaba como la de una sola magnitud (un 8 % menos que penhmain-sum)."""
    import math

    from argus.results.combine import combine_summaries
    from argus.results.summary import Measure, Summary

    def run(n, prim, prim2, sec, sec2):
        e1 = math.sqrt(abs(prim2 - prim * prim / n)) / n
        e2 = math.sqrt(abs(sec2 - sec * sec / n)) / n
        s = Summary(title="t", kparp=2, showers=n)
        s.primaries = {"up": prim, "down": 0.0, "abs": n - prim}
        s.fractions = {"up": Measure((prim + sec) / n, e1 + e2), "down": Measure(0.0, 0.0),
                       "abs": Measure(1.0, 0.0)}
        s.secondaries = {(2, "up"): Measure(sec / n, e2), (2, "down"): Measure(0.0, 0.0)}
        return s

    runs = [(1000, 0.0, 0.0, 5.0, 7.0), (1000, 1.0, 1.0, 3.0, 3.0), (1000, 2.0, 2.0, 4.0, 6.0)]
    out = combine_summaries([run(*r) for r in runs])
    n = sum(r[0] for r in runs)
    prim, prim2, sec, sec2 = (sum(r[k] for r in runs) for k in (1, 2, 3, 4))
    want = (math.sqrt(prim2 - prim * prim / n) + math.sqrt(sec2 - sec * sec / n)) / n
    assert out.fractions["up"].value == pytest.approx((prim + sec) / n)
    assert out.fractions["up"].err == pytest.approx(want, rel=1e-9)


# ---------------------------------------------------------------------------------------------------------------------
# V39: la cascada no escribe volcados
# ---------------------------------------------------------------------------------------------------------------------

def test_the_runs_of_the_cascade_write_no_dump_and_keep_their_signature():
    """Sin DUMPP, penhmain vuelca igual en dump.dmp (una vez al día y al acabar): cada ejecución de la cascada dejaba
    su volcado (con un TAC, cientos de MB). Con DUMPP 1e12 no hay volcado; la firma de las cascadas no cambia."""
    from argus.penhan import cascade as cs

    doc = parse_in_file(CAJA / "caja.in")
    for exact in (True, False):
        d = cs.cascade_doc(doc, 10, (1, 1), exact=exact)
        assert sc.value(d, "DUMPP") == [cs.NO_DUMP] and sc.text(d, "DUMPTO") is None
        assert not any(line.startswith("DUMPP") for line in cs._physics_lines(d))
    d = cs.cascade_doc(doc, 10, (1, 1), exact=True)
    d.remove("DUMPP ")
    assert cs._physics_lines(d) == cs._physics_lines(cs.cascade_doc(doc, 10, (1, 1), exact=True))


# ---------------------------------------------------------------------------------------------------------------------
# V35, V36: textos
# ---------------------------------------------------------------------------------------------------------------------

def test_texts_about_dumps_in_campaigns_and_runs():
    from argus.penhan import batch as bt

    doc = parse_in_file(CAJA / "caja.in")
    with pytest.raises(ValueError, match="lo decide ARGUS en cada ejecución .*su propio volcado"):
        bt.Param("keyword", [1.0, 2.0], keyword="RESUME").apply(doc, 1.0)

def test_runs_that_differ_say_the_dumps_do_not_count(tmp_path):
    from argus.results.folder import ResultsFolder, replica_problem

    runs = []
    for k, z in enumerate(("-5", "-4"), start=1):
        in_path, _doc = case(tmp_path / f"run-0{k}", (("SPOSIT 0 0 -5", f"SPOSIT 0 0 {z}"),), resume=None)
        runs.append(ResultsFolder(in_path.parent, in_path))
    assert "las partículas, el tiempo y los volcados" in (replica_problem(runs) or "")


def test_dump_tallies_record_what_penhmain_keeps():
    info = parse_dump_text(dump(rwrite=12.0))
    t = info.tallies
    assert t.rwrite == 12.0 and [m.idcut for m in t.impact_more] == [1, 2]
    assert t.impact_more[1].age is not None and t.impact_more[1].age.hi == pytest.approx(2e-9)
    assert t.angular_kpar == [(1, 1, 1, 1, 1, 1), (0, 1, 0, 0, 0, 0)]
    assert t.angular_names == ["spc-angdet-01.dat", "spc-angdet-02.dat"] and t.ndb == (11, 11, 25)
    assert info.npsn == 0 and info.lpsf is False and tally_differences(info, parse_in_file(CAJA / "caja.in")) == []
