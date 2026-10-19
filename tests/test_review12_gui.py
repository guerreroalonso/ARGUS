"""Duodécima revisión (0.17.4): los volcados en la ventana («Trabajo», «Ejecutar», «Elegir resultados…», la dosis y
la ayuda). Cada prueba falla con 0.17.3 (ADR-026)."""

from __future__ import annotations

import pytest

from argus.penhan import sections as sc
from test_review12_dumps import _cut, case, dump

NO_DUMPTO = (("DUMPTO dump.dmp                  [Generate this dump file, 20 chars]\n", ""),)


def _job(qtbot, doc, folder):
    from argus.ui.sim_outputs import JobSection

    sec = JobSection()
    qtbot.addWidget(sec)
    sec.set_context(doc, None, folder)
    return sec


def _settings():
    from argus.ui.settings import app_settings

    s = app_settings()
    s.setValue("cascada/al_ejecutar", "false")
    return s


def _run_dialog(qtbot, in_path):
    from argus.session import open_case
    from argus.ui.input_controller import InputController
    from argus.ui.run_dialog import RunDialog

    cf = open_case(in_path)
    ctrl = InputController(cf.input_doc, in_path)
    dlg = RunDialog(None, ctrl, cf.session, _settings())
    qtbot.addWidget(dlg)
    return dlg


# ---------------------------------------------------------------------------------------------------------------------
# V4, V5, V30, V31: «Trabajo»
# ---------------------------------------------------------------------------------------------------------------------

def test_the_placeholders_say_what_penhmain_does(qtbot, tmp_path):
    """Sin DUMPTO, penhmain escribe el volcado en dump.dmp (salvo con DUMPP ≥ 1e10), y sin DUMPP vuelca una vez al
    día: los marcadores decían «(sin volcado)» y «no»."""
    in_path, doc = case(tmp_path, NO_DUMPTO, resume=None)
    sec = _job(qtbot, doc, in_path.parent)
    assert sec.dumpto.placeholderText() == "dump.dmp" and sec.dumpp.placeholderText() == "86400"
    in2, doc2 = case(tmp_path / "b", NO_DUMPTO + (("DUMPP  30", "DUMPP  1e12"),), resume=None)
    sec.set_context(doc2, None, in2.parent)
    assert sec.dumpto.placeholderText() == "(sin volcado)"


def test_empty_state_rows_take_no_room(qtbot, tmp_path):
    """Las filas de estado vacías dejaban un hueco en «Trabajo»."""
    in_path, doc = case(tmp_path, NO_DUMPTO, resume=None)
    sec = _job(qtbot, doc, in_path.parent)
    assert not sec.form.isRowVisible(sec.resume_state) and not sec.form.isRowVisible(sec.dumpto_state)
    in2, doc2 = case(tmp_path / "b")
    sec.set_context(doc2, None, in2.parent)
    assert sec.form.isRowVisible(sec.resume_state) and "no existe: se empieza desde cero" in sec.resume_state.text()


@pytest.mark.parametrize("text, expected", [(None, "desde cero (RESUME sin volcado)"),
                                            ("good", "reanuda de dump.dmp"),
                                            ("cut", "RESUME: penhmain se detendría")])
def test_the_header_says_it_resumes_only_when_penhmain_does(qtbot, tmp_path, text, expected):
    good = dump()
    in_path, doc = case(tmp_path, dump_text={"good": good, "cut": _cut(good, 40)}.get(text))
    sec = _job(qtbot, doc, in_path.parent)
    assert expected in sec.summary(), sec.summary()


@pytest.mark.parametrize("how, expected", [
    ("blank", "Quite el blanco"),
    ("ipsf", "se detendría"),
    ("done", "ya está terminada"),
    ("foreign", "escribiría «Empty.» encima"),
])
def test_the_resume_state_says_what_the_messages_say(qtbot, tmp_path, how, expected):
    """«Trabajo» decía «300 partículas simuladas con esta configuración» con un nombre con un blanco delante (penhmain
    empieza desde cero), con el espacio de fases de un detector (penhmain se detiene) o ya terminada."""
    resume = " dump.dmp" if how == "blank" else "dump.dmp"
    text = {"blank": dump(), "ipsf": dump(rwrite=10.0), "done": dump(showers=2.0e4),
            "foreign": " PENELOPE (v. 2018)  Material data file\n Material: WATER\n"}[how]
    in_path, doc = case(tmp_path, resume=resume, dump_text=text)
    if how == "ipsf":
        (in_path.parent / "psf-impdet.dat").write_text(" #  Results from PENHMAIN.\n #\n")
    sec = _job(qtbot, doc, in_path.parent)
    assert expected in sec.resume_state.text(), sec.resume_state.text()
    assert "con esta configuración" not in sec.resume_state.text()


def test_the_dump_fields_have_one_name_for_undo(qtbot, tmp_path):
    """El mismo cambio se llamaba «Volcado final» en «Trabajo» y «Volcado» en «Elegir resultados…»."""
    from argus.ui.outputs_dialog import FollowSection

    in_path, doc = case(tmp_path, resume=None)
    names = []
    sec = _job(qtbot, doc, in_path.parent)
    sec.apply.connect(lambda text, _fn: names.append(text))
    follow = FollowSection()
    qtbot.addWidget(follow)
    follow.set_context(doc, None, in_path.parent)
    follow.apply.connect(lambda text, _fn: names.append(text))
    for field in (sec.dumpto, follow.dump):
        field.setText("otro.dmp")
        field.setModified(True)
        field.editingFinished.emit()
    assert names == ["Fichero del volcado", "Fichero del volcado"]


def test_a_long_name_is_not_cut_without_saying_it(qtbot):
    """Un nombre pegado de más de 20 caracteres se cortaba sin avisar (y llegaba cortado al .in)."""
    from argus.ui.sim_widgets import NameEdit

    e = NameEdit("dump.dmp")
    qtbot.addWidget(e)
    e.setText("un_nombre_de_mas_de_veinte.dmp")
    assert e.text() == "un_nombre_de_mas_de_veinte.dmp"
    from argus.penhan.inputfile import parse_in_text

    doc = parse_in_text("TITLE  t\n")  # (el .in lo rechaza diciendo por qué: InDocument.set)
    with pytest.raises(ValueError, match="20"):
        sc.set_text(doc, "DUMPTO", "un_nombre_de_mas_de_veinte.dmp")


# ---------------------------------------------------------------------------------------------------------------------
# V19: «Seguir la simulación mientras corre»
# ---------------------------------------------------------------------------------------------------------------------

def test_the_follow_period_can_be_emptied_and_zero_stops_following(qtbot, tmp_path):
    """Vaciar «Cada» era ««» no es un número» y 0 dejaba «DUMPTO dump.dmp»: los dos son ahora no seguirla (como
    desmarcar la tarjeta)."""
    from argus.penhan import goals as gl
    from argus.ui.outputs_dialog import FollowSection

    for value in ("", "0"):
        in_path, doc = case(tmp_path / (value or "vacio"), resume=None)
        edits = []
        sec = FollowSection()
        qtbot.addWidget(sec)
        sec.set_context(doc, None, in_path.parent)
        sec.apply.connect(lambda text, fn, e=edits: e.append((text, fn)))
        sec.every.setText(value)
        sec.every.setModified(True)
        sec.every.editingFinished.emit()
        assert edits and edits[-1][0] == "Quitar: Seguir la simulación", (value, edits)
        edits[-1][1](doc)
        assert sc.value(doc, "DUMPP") is None and sc.text(doc, "DUMPTO") is None and gl.follow_period(doc) is None
    sec.every.setText("-5")
    sec.every.setModified(True)
    sec.every.editingFinished.emit()
    assert sec.every.toolTip() == "el valor mínimo es 0"


# ---------------------------------------------------------------------------------------------------------------------
# V3: «Ejecutar»
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("how, expected", [("cut", "está incompleto: penhmain se detendría"),
                                           ("title", "es de otra simulación (otro título): penhmain se detendría"),
                                           ("done", "ya está terminada")])
def test_ejecutar_says_what_penhmain_will_do_with_the_dump(qtbot, tmp_path, how, expected):
    """«Ejecutar» decía «penhmain empieza desde cero» con un volcado cortado y «penhmain sigue la simulación guardada»
    con el de otro título (penhmain se detiene en los dos)."""
    good = dump()
    text = {"cut": _cut(good, 40), "title": dump(title="Otra simulación"), "done": dump(showers=2.0e4)}[how]
    in_path, _doc = case(tmp_path, dump_text=text)
    dlg = _run_dialog(qtbot, in_path)
    shown = dlg.files_state.text()
    assert expected in shown and "empieza desde cero" not in shown and "sigue la simulación guardada" not in shown, \
        shown
    assert not dlg.prev_box.isVisibleTo(dlg)


# ---------------------------------------------------------------------------------------------------------------------
# V16, V37, V38: la nota de la dosis, «Elegir resultados…» y la ayuda del menú
# ---------------------------------------------------------------------------------------------------------------------

def test_the_dose_note_without_a_grid_in_the_in(qtbot, tmp_path):
    """Con el .in sin rejilla decía «no con esta (el volcado tiene una … y el .in no …). Para usar esta…»."""
    from argus.ui.sim_outputs import DoseSection

    edits = tuple((f"{k}  {v}", "") for k, v in (("GRIDX", "-5 5 11"), ("GRIDY", "-5 5 11"), ("GRIDZ", "0 5 25")))
    in_path, doc = case(tmp_path, edits, dump_text=dump())
    s = DoseSection()
    qtbot.addWidget(s)
    s.set_context(doc, None, in_path.parent)
    note = s.resume_note.text()
    assert "seguirá calculando la dosis en la rejilla del volcado" in note and "no con esta" not in note, note
    assert "aunque el .in no tenga ninguna" in note


def test_the_outputs_note_says_follow_still_counts(tmp_path):
    from argus.ui.outputs_dialog import CARDS_BY_KEY, resume_note

    in_path, doc = case(tmp_path, dump_text=dump())
    note = resume_note(doc, in_path.parent)
    assert "«Seguir la simulación» sí cuentan" in note and "sus detectores" in note
    assert "IPSF 1" in CARDS_BY_KEY["seguir"].files


def test_the_help_of_the_outputs_menu_does_not_offer_tracks(qtbot):
    from argus.ui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    assert "trayectorias" not in w.act_outputs.toolTip()
