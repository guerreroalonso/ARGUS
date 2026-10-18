"""Revisión 3 (ficheros): el .geo y el .in que lo sigue se guardan juntos (y enteros o ninguno), cambios hechos por
otros programas, enlaces, nombres de más de 20 caracteres, colores tras «Guardar como…», el .in con errores también
sigue a la geometría, arrastrar ficheros a la ventana y la carpeta de trabajo de penhmain (INCLUDE y .ct)."""

import os
import re
import shutil

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from argus.core import files  # noqa: E402
from argus.pengeom import edit as ge  # noqa: E402
from argus.pengeom.compiler import compile_geometry  # noqa: E402
from argus.pengeom.parser import load_geometry  # noqa: E402
from argus.penhan import source_edit as se  # noqa: E402
from argus.penhan.inputfile import parse_in_file  # noqa: E402
from argus.ui.settings import app_settings  # noqa: E402
from conftest import CORPUS  # noqa: E402

pytestmark = pytest.mark.gui

SB = QMessageBox.StandardButton
STACK = CORPUS / "penhan/stack"
CASE = CORPUS / "usuario/100Mev15-174"
REFS = ("DSMAX", "IDBODY", "EDBODY")
REAL_QUESTION = QMessageBox.question  # la de verdad (el fixture la sustituye)
Z = "0" * 64
END = "END      " + "0" * 55 + "\n"


def _copy(src, dst, names=None):
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and (names is None or f.name in names):
            shutil.copy2(f, dst / f.name)
            os.chmod(dst / f.name, 0o644)
    return dst


@pytest.fixture
def stack_dir(tmp_path):
    return _copy(STACK, tmp_path / "stack")


@pytest.fixture
def case_dir(tmp_path):
    return _copy(CASE, tmp_path / "caso", {"maniqui.in", "cubo-esfera.geo"})


@pytest.fixture
def window(qtbot, monkeypatch):
    from argus.pengeom import locate_fast
    from argus.ui.main_window import MainWindow

    msgs = []
    answers = {"question": SB.Discard, "questions": []}

    def question(*a, **k):
        msgs.append(("question", a[2]))
        return answers["questions"].pop(0) if answers["questions"] else answers["question"]

    monkeypatch.setattr(QMessageBox, "question", question)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, k=kind, **kw: msgs.append((k, a[2])) or SB.Ok)
    locate_fast.warmup()
    app_settings().clear()
    w = MainWindow()
    w._answers = answers
    w._msgs = msgs
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    yield w
    app_settings().clear()


def _asked(w, kind="question"):
    return [m for k, m in w._msgs if k == kind]


def _disk_refs(folder, in_name="stack.in"):
    """(palabra clave, etiqueta del cuerpo) de cada referencia a cuerpos del .in guardado, con la geometría guardada."""
    doc = parse_in_file(folder / in_name)
    geo = compile_geometry(load_geometry(folder / doc.model.geomfn), parinp=doc.model.parinp_list(),
                           base_dir=folder)
    return [(ln.kw.strip(), geo.balias(ln.kb).strip() if 0 < ln.kb <= geo.nbodys else None)
            for ln in doc.lines if ln.kw.strip() in REFS]


def _memory_refs(w):
    geo = w.session.geo
    return [(ln.kw.strip(), geo.balias(ln.kb).strip() if ln.kb is not None and 0 < ln.kb <= geo.nbodys else None)
            for ln in w.input.doc.lines if ln.kw.strip() in REFS]


def _add_sphere(w):
    """Una esfera pequeña dentro del disco «2»: PENGEOM renumera los cuerpos y el .in lo sigue."""
    m2 = w.session.geo.find_body("2").index
    w.edit_geometry("Añadir esfera", lambda doc, g: ge.add_primitive(
        doc, g, ge.Primitive("sphere", center=(0.0, 0.0, 0.003), radius=0.0008), 1, m2))


def _bytes(folder, *names):
    return [(folder / n).read_bytes() for n in names]


# -- 1. el .geo y el .in que lo sigue: una sola pregunta, los dos o ninguno ------------------------------------------
@pytest.mark.parametrize("answers", [[SB.Save, SB.Discard], [SB.Discard, SB.Save]])
def test_closing_asks_once_for_the_geometry_and_the_in_that_follows_it(qtbot, window, stack_dir, answers):
    """Antes: una pregunta por fichero, y «guardar uno y descartar el otro» dejaba en el disco un .in que nombraba
    otros cuerpos (DSMAX 1 y EDBODY 1 en la esfera nueva, IDBODY 2 en el disco 1)."""
    w = window
    w.open_file(stack_dir / "stack.in")
    before = _disk_refs(stack_dir)
    _add_sphere(w)
    assert "EDBODY 4" in w.input.doc.text()
    old = _bytes(stack_dir, "3discs.geo", "stack.in")
    w._answers["questions"] = list(answers)
    assert w.maybe_save()
    q = _asked(w)
    assert len(q) == 1 and "los dos" in q[0]
    saved = answers[0] == SB.Save
    new = _bytes(stack_dir, "3discs.geo", "stack.in")
    assert [a != b for a, b in zip(old, new)] == [saved, saved]
    assert _disk_refs(stack_dir) == before


def test_cancelling_the_pair_question_writes_nothing(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    _add_sphere(w)
    old = _bytes(stack_dir, "3discs.geo", "stack.in")
    w._answers["questions"] = [SB.Cancel]
    assert not w.maybe_save()
    assert _bytes(stack_dir, "3discs.geo", "stack.in") == old and w.geo_ctrl.dirty and w.input.dirty


def test_independent_changes_are_still_asked_separately(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [1.0, 0.0, -100.0]))
    w.edit_geometry("Cambiar el nombre",
                    lambda doc, g: ge.set_description(ge.find_element(doc, "body", "   1"), "Cubo"))
    in0 = (case_dir / "maniqui.in").read_bytes()
    w._answers["questions"] = [SB.Save, SB.Discard]
    assert w.maybe_save()
    assert len(_asked(w)) == 2
    assert b"Cubo" in (case_dir / "cubo-esfera.geo").read_bytes() and (case_dir / "maniqui.in").read_bytes() == in0


def test_the_pair_question_has_its_own_spanish_buttons(qtbot, window, stack_dir, monkeypatch):
    """Con el QMessageBox de verdad: «Guardar los dos», «No guardar ninguno» y «Cancelar»."""
    w = window
    w.open_file(stack_dir / "stack.in")
    _add_sphere(w)
    monkeypatch.setattr(QMessageBox, "question", REAL_QUESTION)
    boxes = []  # los textos de los botones de cada pregunta
    tries = {"n": 0}

    def answer():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            return
        texts = [b.text() for b in box.buttons()]
        tries["n"] += 1
        if "Guardar los dos" in texts or tries["n"] > 20:  # sin esos textos (el fallo) también se contesta
            boxes.append(texts)
            tries["n"] = 0
            box.button(SB.Discard).click()

    timer = QTimer()
    timer.timeout.connect(answer)
    timer.start(10)
    try:
        assert w.maybe_save()
    finally:
        timer.stop()
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: SB.Discard)  # al cerrar la ventana
    assert len(boxes) == 1 and {"Guardar los dos", "No guardar ninguno"} <= set(boxes[0])


def test_save_input_as_also_saves_the_geometry_it_follows(qtbot, window, stack_dir, monkeypatch):
    """Y, revisión final: el stack.in original (que se queda en el disco) nombraría otros cuerpos de la geometría
    guardada: se pregunta antes (antes se guardaba sin decir nada)."""
    w = window
    w.open_file(stack_dir / "stack.in")
    before = _disk_refs(stack_dir)
    _add_sphere(w)
    target = stack_dir / "copia.in"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    old = _bytes(stack_dir, "3discs.geo", "stack.in")
    w._answers["questions"] = [SB.Save, SB.Cancel]  # los dos, pero no con el original roto
    assert not w.save_input_as()
    q = _asked(w)[-1]
    assert "stack.in" in q and "DSMAX" in q and "otros cuerpos" in q
    assert _bytes(stack_dir, "3discs.geo", "stack.in") == old and not target.exists()
    w._answers["questions"] = [SB.Save, SB.Save]  # «Guardar de todos modos»
    assert w.save_input_as()
    assert _disk_refs(stack_dir, "copia.in") == before
    assert not w.geo_ctrl.dirty and not w.input.dirty and w.input.path == target


def test_saving_only_the_geometry_asks_to_save_the_in_too(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    before = _disk_refs(stack_dir)
    _add_sphere(w)
    old = _bytes(stack_dir, "3discs.geo", "stack.in")
    w._answers["questions"] = [SB.Cancel]
    assert not w.save_geometry()
    assert _bytes(stack_dir, "3discs.geo", "stack.in") == old
    w._answers["questions"] = [SB.Save]
    assert w.save_geometry()
    assert _disk_refs(stack_dir) == before and not w.input.dirty and not w.geo_ctrl.dirty


def test_new_input_saves_the_pair_or_keeps_the_old_in_open(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    before = _disk_refs(stack_dir)
    _add_sphere(w)
    w._answers["questions"] = [SB.Discard]  # no guardar: el .in abierto se queda
    w.new_input()
    assert w.input.path == stack_dir / "stack.in" and w.input.dirty
    w._answers["questions"] = [SB.Save]
    w.new_input()
    assert w.input.path is None and not w.geo_ctrl.dirty
    assert _disk_refs(stack_dir) == before


def test_undo_after_saving_the_pair_asks_again_for_both(qtbot, window, stack_dir):
    w = window
    w.open_file(stack_dir / "stack.in")
    before = _disk_refs(stack_dir)
    _add_sphere(w)
    w._answers["questions"] = [SB.Save]
    assert w.save_all()
    w.undo()  # la esfera y el .in que la seguía, a la vez
    assert w.geo_ctrl.dirty and w.input.dirty
    w._answers["questions"] = [SB.Save, SB.Discard]
    assert w.maybe_save()
    assert len(_asked(w)) == 1 and _disk_refs(stack_dir) == before


def test_save_as_that_geomfn_cannot_follow_waits_for_the_pair(qtbot, window, stack_dir, monkeypatch):
    """Con cambios del .in que siguen a la geometría, «Guardar la geometría como…» con un nombre que GEOMFN no
    puede seguir (más de 20 caracteres, otra carpeta) dejaría el .in nombrando otros cuerpos de 3discs.geo."""
    w = window
    w.open_file(stack_dir / "stack.in")
    before = _disk_refs(stack_dir)
    _add_sphere(w)
    other = stack_dir / "otra"
    other.mkdir()
    for target in (stack_dir / "tres-discos-version2.geo", other / "3discs.geo"):
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, t=target, **k: (str(t), ""))
        assert not w.save_geometry_as() and not target.exists()
        assert "Guarde antes los dos" in _asked(w, "warning")[-1]
    # Con un nombre que GEOMFN sigue, sí; al cerrar, solo se pregunta por el .in (los dos casan de las dos maneras).
    new = stack_dir / "tres.geo"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new), ""))
    assert w.save_geometry_as() and w.input.doc.model.geomfn == "tres.geo"
    w._msgs.clear()
    w._answers["questions"] = [SB.Discard]
    assert w.maybe_save()
    assert _asked(w) == ["¿Guardar los cambios en stack.in?"]
    assert _disk_refs(stack_dir) == before  # stack.in, sin guardar, sigue con 3discs.geo (sin cambios)


# -- 2. «Guardar»: los dos ficheros enteros o ninguno ----------------------------------------------------------------
def _read_only_like_windows(monkeypatch):
    """Como root, os.access dice que se puede escribir un fichero de solo lectura: se simula lo que diría Windows."""
    real = os.access

    def access(path, mode, *a, **k):
        if mode & os.W_OK and os.path.isfile(path) and not os.stat(path).st_mode & 0o222:
            return False
        return real(path, mode, *a, **k)

    monkeypatch.setattr(os, "access", access)


def test_save_all_writes_nothing_if_the_in_is_read_only(qtbot, window, stack_dir, monkeypatch):
    w = window
    w.open_file(stack_dir / "stack.in")
    _add_sphere(w)
    old = _bytes(stack_dir, "3discs.geo", "stack.in")
    os.chmod(stack_dir / "stack.in", 0o444)
    _read_only_like_windows(monkeypatch)
    assert not w.save_all()
    assert _bytes(stack_dir, "3discs.geo", "stack.in") == old
    assert w.geo_ctrl.dirty and w.input.dirty
    msg = _asked(w, "critical")
    assert msg and "stack.in" in msg[-1] and "solo lectura" in msg[-1]


def test_a_failed_second_replace_puts_the_first_file_back(qtbot, window, stack_dir, monkeypatch):
    """En Windows, un .in abierto por «penhmain < stack.in» no se puede sustituir: la geometría ya escrita vuelve
    a su versión anterior y se dice qué ha pasado."""
    w = window
    w.open_file(stack_dir / "stack.in")
    _add_sphere(w)
    old = _bytes(stack_dir, "3discs.geo", "stack.in")
    real_replace = os.replace

    def locked(src, dst, *a, **k):
        if os.path.basename(dst) == "stack.in":
            raise PermissionError(13, "El archivo está siendo utilizado por otro proceso", str(dst))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", locked)
    assert not w.save_all()
    assert _bytes(stack_dir, "3discs.geo", "stack.in") == old
    msg = _asked(w, "critical")[-1]
    assert "stack.in" in msg and "3discs.geo" in msg and "versión anterior" in msg
    assert w.geo_ctrl.dirty and w.input.dirty
    assert not [p for p in stack_dir.iterdir() if p.name.endswith(files.TMP_SUFFIX)]


def test_write_files_is_all_or_nothing(tmp_path, monkeypatch):
    a, b = tmp_path / "a.geo", tmp_path / "b.in"
    a.write_bytes(b"A0")
    b.write_bytes(b"B0")
    files.write_files([(a, b"A1"), (b, b"B1")])
    assert (a.read_bytes(), b.read_bytes()) == (b"A1", b"B1")
    assert (tmp_path / "a.geo.bak").read_bytes() == b"A0" and (tmp_path / "b.in.bak").read_bytes() == b"B0"
    real_replace = os.replace

    def fail_b(src, dst, *x, **k):
        if os.path.basename(dst) == "b.in":
            raise PermissionError(13, "bloqueado", str(dst))
        return real_replace(src, dst, *x, **k)

    monkeypatch.setattr(os, "replace", fail_b)
    with pytest.raises(files.SaveError) as info:
        files.write_files([(a, b"A2"), (b, b"B2")])
    assert (a.read_bytes(), b.read_bytes()) == (b"A1", b"B1")
    assert "a.geo" in str(info.value) and "b.in" in str(info.value)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.geo", "a.geo.bak", "b.in", "b.in.bak"]
    # Las copias .bak vuelven a ser las de antes (la versión anterior a la última guardada), no una copia de lo
    # que sigue en el disco.
    assert (tmp_path / "a.geo.bak").read_bytes() == b"A0" and (tmp_path / "b.in.bak").read_bytes() == b"B0"


def test_write_files_puts_back_a_hard_linked_file_left_half_written(tmp_path, monkeypatch):
    a, b = tmp_path / "a.geo", tmp_path / "b.in"
    a.write_bytes(b"A0")
    b.write_bytes(b"B0")
    os.link(b, tmp_path / "b2.in")  # con dos nombres: se escribe en su sitio
    real = files._write_in_place

    def half(path, data, job=None):
        if job is None:  # al volver atrás
            return real(path, data)
        with open(path, "r+b") as f:  # el disco se llena a mitad
            job.touched = True
            f.write(data[:1])
            f.truncate()
        raise OSError(28, "No queda espacio en el disco", str(path))

    monkeypatch.setattr(files, "_write_in_place", half)
    with pytest.raises(files.SaveError) as info:
        files.write_files([(a, b"A1"), (b, b"B1")])
    assert [p.read_bytes() for p in (a, b, tmp_path / "b2.in")] == [b"A0", b"B0", b"B0"]
    assert "a.geo ya se había guardado" in info.value.strerror and "No ha cambiado ningún fichero" in \
        info.value.strerror


# -- 3. nombres de más de 20 caracteres (GEOMFN es A20) ---------------------------------------------------------------
def test_save_as_with_a_name_longer_than_20_keeps_geomfn(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    new = case_dir / "cubo-esfera-version2.geo"  # 24 caracteres
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new), ""))
    assert w.save_geometry_as()
    assert new.is_file()
    assert w.input.doc.model.geomfn == "cubo-esfera.geo" and not w.input.dirty
    assert any("20" in m and "cubo-esfera.geo" in m for m in _asked(w, "warning"))


def test_new_input_refuses_a_geometry_name_longer_than_20(qtbot, window, tmp_path):
    w = window
    long = tmp_path / "cubo-esfera-version2.geo"
    shutil.copy2(CASE / "cubo-esfera.geo", long)
    w.open_file(long, with_input=False)
    w.new_input()
    assert w.input is None
    assert any("20" in m for k, m in w._msgs if k in ("warning", "information"))


def test_new_geometry_dialog_needs_a_short_name_to_create_the_in(qtbot, tmp_path):
    from argus.ui.new_geometry_dialog import NewGeometryDialog

    dlg = NewGeometryDialog(None, tmp_path / "geometria.geo")
    qtbot.addWidget(dlg)
    dlg.path_edit.setText(str(tmp_path / "geometria-muy-larga-1.geo"))
    dlg.make_in.setChecked(True)
    assert not dlg._check() and "20" in dlg.status.text()
    dlg.make_in.setChecked(False)
    assert dlg._check()


# -- 4. ficheros cambiados por otro programa ------------------------------------------------------------------------
def test_saving_over_an_in_changed_by_another_program_asks(qtbot, window, case_dir):
    w = window
    p = case_dir / "maniqui.in"
    w.open_file(p)
    p.write_bytes(p.read_bytes().replace(b"NSIMSH 1.E6", b"NSIMSH 5.E7"))  # el Bloc de notas
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [1.0, 0.0, -100.0]))
    w._answers["questions"] = [SB.Cancel]
    assert not w.save_all()
    q = _asked(w)
    assert len(q) == 1 and "otro programa" in q[0] and "maniqui.in" in q[0]
    assert b"NSIMSH 5.E7" in p.read_bytes() and w.input.dirty
    w._answers["questions"] = [SB.Save]  # «Sobrescribir»: la versión del otro programa queda en el .bak
    assert w.save_all()
    assert b"NSIMSH 1.E6" in p.read_bytes() and b"NSIMSH 5.E7" in (case_dir / "maniqui.in.bak").read_bytes()
    w._msgs.clear()
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [2.0, 0.0, -100.0]))
    assert w.save_all() and not _asked(w)  # lo que ha escrito ARGUS no cuenta como cambio de otro programa


def test_reload_takes_the_version_on_disk(qtbot, window, case_dir):
    w = window
    p = case_dir / "maniqui.in"
    w.open_file(p)
    p.write_bytes(p.read_bytes().replace(b"NSIMSH 1.E6", b"NSIMSH 5.E7"))
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [1.0, 0.0, -100.0]))
    w._answers["questions"] = [SB.Reset]  # «Recargar»
    assert not w.save_all()
    assert b"NSIMSH 5.E7" in p.read_bytes() and not (case_dir / "maniqui.in.bak").exists()
    assert w.input.doc.first("NSIMSH").values[0] == 5e7 and not w.input.dirty


def test_the_controller_does_not_overwrite_a_file_changed_by_another_program(qapp, tmp_path):
    p = tmp_path / "maniqui.in"
    shutil.copy2(CASE / "maniqui.in", p)
    c = _edited_in_controller(p)
    p.write_bytes(p.read_bytes().replace(b"NSIMSH 1.E6", b"NSIMSH 1.E8"))
    with pytest.raises(files.FileChangedError):
        c.save()
    assert b"NSIMSH 1.E8" in p.read_bytes() and c.dirty
    c.save(force=True)
    assert b"NSIMSH 1.E6" in p.read_bytes() and b"NSIMSH 1.E8" in (tmp_path / "maniqui.in.bak").read_bytes()
    c.edit("Mover la fuente", lambda doc: se.set_position(doc, [4.0, 5.0, 6.0]))
    c.save()  # lo que ha escrito ARGUS no cuenta como cambio de otro programa
    assert b"SPOSIT 4.0 5.0 6.0" in p.read_bytes()


def test_coming_back_to_the_window_tells_about_changes_made_by_another_program(qtbot, window, case_dir):
    from PySide6.QtCore import QEvent

    w = window
    p = case_dir / "maniqui.in"
    w.open_file(p)
    w.changeEvent(QEvent(QEvent.Type.ActivationChange))
    w.statusBar().clearMessage()
    w._notice_external_changes()
    assert "otro programa" not in w.statusBar().currentMessage()
    p.write_bytes(p.read_bytes().replace(b"NSIMSH 1.E6", b"NSIMSH 5.E7"))
    w._notice_external_changes()
    assert "maniqui.in" in w.statusBar().currentMessage() and "otro programa" in w.statusBar().currentMessage()
    w.statusBar().clearMessage()
    w._notice_external_changes()  # el mismo cambio se dice una vez
    assert w.statusBar().currentMessage() == ""


def test_the_geometry_changed_by_another_program_is_not_overwritten_silently(qtbot, window, case_dir):
    w = window
    g = case_dir / "cubo-esfera.geo"
    w.open_file(case_dir / "maniqui.in")
    g.write_bytes(g.read_bytes().replace(b"\n", b"\n", 1) + b"\n")  # otro editor añade una línea al final
    other = g.read_bytes()
    w.edit_geometry("Cambiar el nombre",
                    lambda doc, gg: ge.set_description(ge.find_element(doc, "body", "   1"), "Cubo"))
    w._answers["questions"] = [SB.Cancel]
    assert not w.save_geometry()
    assert g.read_bytes() == other and any("cubo-esfera.geo" in m for m in _asked(w))


# -- 5. «Guardar la geometría como…» en una carpeta con otro INCLUDE o .ct del mismo nombre ---------------------------
def test_save_as_refuses_a_folder_with_another_include_of_the_same_name(qtbot, window, tmp_path, monkeypatch):
    w = window
    src = _copy(CORPUS / "pengeomjar", tmp_path / "src", {"example1", "example2"})
    w.open_file(src / "example2", with_input=False)
    assert w.session.ok
    b = tmp_path / "b"
    b.mkdir()
    text = (src / "example1").read_text(encoding="latin-1")
    (b / "example1").write_text(text.replace("MATERIAL(   1)", "MATERIAL(   2)"), encoding="latin-1")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(b / "ex2.geo"), ""))
    assert not w.save_geometry_as()
    assert sorted(p.name for p in b.iterdir()) == ["example1"]
    assert any("example1" in m for k, m in w._msgs if k in ("warning", "critical"))
    assert w.session.path == src / "example2"
    # Con el mismo contenido no hay problema.
    shutil.copy2(src / "example1", b / "example1")
    assert w.save_geometry_as() and (b / "ex2.geo").is_file()


def test_save_as_refuses_a_folder_with_another_ct_of_the_same_name(qtbot, window, tmp_path, monkeypatch):
    w = window
    src = _copy(CORPUS / "penhan/emfield_ct", tmp_path / "src", {"ctbox.geo", "box.ct"})
    w.open_file(src / "ctbox.geo", with_input=False)
    assert w.session.ok and w.session.geo.ct is not None
    b = tmp_path / "b"
    b.mkdir()
    (b / "box.ct").write_bytes((src / "box.ct").read_bytes().replace(b"1", b"2", 1))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(b / "g.geo"), ""))
    assert not w.save_geometry_as()
    assert sorted(p.name for p in b.iterdir()) == ["box.ct"]


# -- 6. enlaces simbólicos y duros ------------------------------------------------------------------------------------
def _edited_in_controller(path):
    from argus.ui.input_controller import InputController

    c = InputController(parse_in_file(path), path)
    c.edit("Mover la fuente", lambda doc: se.set_position(doc, [1.0, 2.0, 3.0]))
    return c


def test_saving_through_a_symbolic_link_keeps_the_link(qapp, tmp_path):
    shared = tmp_path / "compartido.in"
    shutil.copy2(CASE / "maniqui.in", shared)
    original = shared.read_bytes()
    d = tmp_path / "caso"
    d.mkdir()
    link = d / "maniqui.in"
    os.symlink(shared, link)
    _edited_in_controller(link).save()
    assert link.is_symlink() and b"SPOSIT 1.0 2.0 3.0" in shared.read_bytes()
    assert (d / "maniqui.in.bak").read_bytes() == original


def test_saving_a_file_with_hard_links_updates_every_name(qapp, tmp_path):
    f = tmp_path / "maniqui.in"
    shutil.copy2(CASE / "maniqui.in", f)
    other = tmp_path / "otro_nombre.in"
    os.link(f, other)
    _edited_in_controller(f).save()
    assert b"SPOSIT 1.0 2.0 3.0" in other.read_bytes() and os.stat(f).st_nlink == 2
    assert (tmp_path / "maniqui.in.bak").read_bytes() == (CASE / "maniqui.in").read_bytes()


# -- 7. los ajustes de la geometría siguen a «Guardar la geometría como…» --------------------------------------------
def test_colours_and_the_chosen_in_follow_save_as(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "maniqui.in")
    w.materials.colors = {1: "#ff0000"}
    w._on_colors_changed({1: "#ff0000"})
    new = case_dir / "copia.geo"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new), ""))
    assert w.save_geometry_as()
    assert w.settings.value(w._choice_key(new)) == "maniqui.in"  # el .in que ahora la usa (GEOMFN)
    w._answers["question"] = SB.Discard
    w.open_file(new, with_input=False)
    assert w.material_colors == {1: "#ff0000"}


# -- 8. el .in con errores de lectura también sigue a la geometría ----------------------------------------------------
def _typo(path, old, new):
    t = path.read_bytes().decode("latin-1")
    assert old in t
    path.write_bytes(t.replace(old, new, 1).encode("latin-1"))


def test_an_in_with_a_reader_error_still_follows_the_geometry(qtbot, window, stack_dir, monkeypatch):
    import argus.ui.main_window as mw

    calls = []
    real_follow = mw.follow_geometry

    def follow(*a, **k):
        calls.append(k)
        return real_follow(*a, **k)

    monkeypatch.setattr(mw, "follow_geometry", follow)
    w = window
    p = stack_dir / "stack.in"
    _typo(p, "DUMPP  60  ", "DUMPP  6x  ")
    w.open_file(p)
    assert not w.input.doc.model.ok
    before = _memory_refs(w)
    old = w.session.geo
    _add_sphere(w)
    assert _memory_refs(w) == before and "EDBODY 4" in w.input.doc.text()
    # Las líneas con 0 o con una etiqueta en blanco siguen a su cuerpo con las dos geometrías (penhan.follow).
    assert calls and calls[-1]["old_geo"] is old and calls[-1]["new_geo"] is w.session.geo
    # Eliminar un cuerpo que nombra el .in: se avisa de las líneas que se quitarán.
    w._answers["questions"] = [SB.No]
    w.delete_body(w.session.geo.find_body("1").index)
    assert "El .in lo nombra" in _asked(w)[-1]


def test_unreadable_body_lines_are_warned_before_renumbering(qtbot, window, stack_dir):
    w = window
    p = stack_dir / "stack.in"
    _typo(p, "DSMAX  2 0.0001", "DSMAX  2x 0.0001")
    w.open_file(p)
    text = w.geo_ctrl.text
    w._answers["questions"] = [SB.No]
    _add_sphere(w)
    q = _asked(w)
    assert len(q) == 1 and "DSMAX" in q[0]
    assert w.geo_ctrl.text == text and not w.geo_ctrl.dirty and not w.input.dirty
    w._answers["questions"] = [SB.Yes]
    _add_sphere(w)
    assert w.geo_ctrl.dirty and "EDBODY 4" in w.input.doc.text()


# -- 9. arrastrar ficheros a la ventana ---------------------------------------------------------------------------
def _drop(w, paths):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    enter = QDragEnterEvent(w.rect().center(), Qt.DropAction.CopyAction, md, Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w, enter)
    drop = QDropEvent(QPointF(w.rect().center()), Qt.DropAction.CopyAction, md, Qt.MouseButton.NoButton,
                      Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w, drop)
    return enter.isAccepted()


def test_only_in_and_geo_files_can_be_dropped(qtbot, window, case_dir):
    w = window
    w.open_file(case_dir / "maniqui.in")
    (case_dir / "notas.txt").write_text("hola\n")
    recent = w.settings.value("recent")
    assert not _drop(w, [case_dir / "notas.txt"])
    assert w.input.path.name == "maniqui.in" and w.session.path.name == "cubo-esfera.geo"
    assert w.settings.value("recent") == recent and "notas.txt" in w.statusBar().currentMessage()
    shutil.copy2(case_dir / "cubo-esfera.geo", case_dir / "OTRA.GEO")
    assert _drop(w, [case_dir / "notas.txt", case_dir / "OTRA.GEO"])
    assert w.session.path.name == "OTRA.GEO"
    # Una geometría con otra extensión (o sin ella) se reconoce por su contenido (revisión final).
    shutil.copy2(case_dir / "cubo-esfera.geo", case_dir / "geometria")
    assert _drop(w, [case_dir / "geometria"])
    assert w.session.path.name == "geometria"


# -- 10. INCLUDE y .ct en la carpeta de trabajo de penhmain (la del .in) ---------------------------------------------
def _subfolder_case(root, part_in):
    """case.in con «GEOMFN sub/3discs.geo»; la geometría incluye part.geo, que está en ``part_in``."""
    d = root / "caso"
    (d / "sub").mkdir(parents=True)
    t = (STACK / "stack.in").read_text(encoding="latin-1")
    t = re.sub(r"^GEOMFN 3discs\.geo    ", "GEOMFN sub/3discs.geo", t, flags=re.M)
    (d / "case.in").write_text(t, encoding="latin-1")
    (d / "sub/3discs.geo").write_text(f"envoltura\n{Z}\nINCLUDE*\n   FILE=(part.geo    )\n{Z}\n{END}")
    shutil.copy2(STACK / "3discs.geo", d / part_in / "part.geo")
    return d


def test_includes_are_read_from_the_in_folder_like_penhmain(tmp_path):
    from argus.session import open_case

    d = _subfolder_case(tmp_path / "a", ".")  # penhmain la lee (IRETRN 0)
    s = open_case(d / "case.in").session
    assert s.ok, [x.message for x in s.diagnostics]
    assert any("INCLUDE" in x.message and "carpeta" in x.message for x in s.diagnostics)
    d = _subfolder_case(tmp_path / "b", "sub")  # penhmain se para (fin de fichero en pengeom.f)
    s = open_case(d / "case.in").session
    assert not s.ok


def test_the_ct_file_is_read_from_the_in_folder(qtbot, window, tmp_path):
    from argus.session import open_case

    src = CORPUS / "penhan/emfield_ct"
    d = tmp_path / "caso"
    (d / "g").mkdir(parents=True)
    t = (src / "ctbox.in").read_text(encoding="latin-1")
    (d / "case.in").write_text(re.sub(r"^GEOMFN ctbox\.geo  ", "GEOMFN g/ctbox.geo", t, flags=re.M),
                               encoding="latin-1")
    shutil.copy2(src / "ctbox.geo", d / "g/ctbox.geo")
    shutil.copy2(src / "box.ct", d / "g/box.ct")
    s = open_case(d / "case.in").session
    assert not s.ok and any(x.code == 2106 for x in s.diagnostics)  # como penhmain (IRETRN 2106)
    shutil.copy2(src / "box.ct", d / "box.ct")
    w = window
    w.open_file(d / "case.in")
    assert w.session.ok and w.session.geo.ct_path == d / "box.ct"
    from argus.pengeom.model import CtBoxElement

    w.edit_geometry("Cambiar el nombre", lambda doc, g: ge.set_description(
        next(e for e in doc.elements if isinstance(e, CtBoxElement)), "Maniquí"))
    assert w.geo_ctrl.dirty and "Maniquí" in w.geo_ctrl.text
    assert w.session.ok and w.session.geo.ct_path == d / "box.ct"  # se sigue compilando con el .ct del .in


def test_the_simulation_folder_takes_includes_from_the_in_folder(tmp_path):
    from argus.penhan.simfolder import plan_folder

    d = _subfolder_case(tmp_path, "sub")  # solo junto a la geometría: penhmain no lo encontraría
    plan = plan_folder(d / "case.in", parse_in_file(d / "case.in"), tmp_path / "destino")
    items = {i.name: i for i in plan.items}
    assert items["part.geo"].status == "missing" and "carpeta del .in" in items["part.geo"].note
    d = _subfolder_case(tmp_path / "otra", ".")
    plan = plan_folder(d / "case.in", parse_in_file(d / "case.in"), tmp_path / "destino2")
    assert {i.name: i for i in plan.items}["part.geo"].source == d / "part.geo"


def test_save_input_as_warns_only_if_geomfn_would_name_another_file(qtbot, window, tmp_path, monkeypatch):
    w = window
    d = _subfolder_case(tmp_path, ".")
    w.open_file(d / "case.in")
    w.input.edit("Mover la fuente", lambda doc: se.set_position(doc, [0.0, 0.0, -1.0]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(d / "copia.in"), ""))
    assert w.save_input_as() and not _asked(w, "warning")  # «sub/3discs.geo» sigue siendo la misma geometría
    other = tmp_path / "otra"
    other.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(other / "copia.in"), ""))
    w._answers["question"] = SB.Cancel
    assert not w.save_input_as() and not (other / "copia.in").exists()
    assert "GEOMFN" in _asked(w, "warning")[-1]


def test_save_as_copies_the_includes_penhmain_reads(qtbot, window, tmp_path, monkeypatch):
    w = window
    d = _subfolder_case(tmp_path, ".")
    w.open_file(d / "case.in")
    assert w.session.ok
    other = tmp_path / "otra"
    other.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(other / "g.geo"), ""))
    assert w.save_geometry_as()
    assert (other / "part.geo").read_bytes() == (d / "part.geo").read_bytes()


# -- revisión final: .in que nombrarían otros cuerpos, recargar lo que ha cambiado y carpeta de trabajo -------------
def test_ctrl_s_after_undoing_the_geomfn_that_followed_save_as_asks(qtbot, window, stack_dir, monkeypatch):
    """«Guardar la geometría como» tres.geo (GEOMFN la sigue), deshacer el cambio de GEOMFN y Ctrl+S: stack.in se
    guardaría con la numeración de tres.geo nombrando 3discs.geo (otros cuerpos). Antes se guardaba sin más."""
    w = window
    w.open_file(stack_dir / "stack.in")
    before = _disk_refs(stack_dir)
    _add_sphere(w)
    new = stack_dir / "tres.geo"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new), ""))
    assert w.save_geometry_as() and w.input.doc.model.geomfn == "tres.geo"
    w.undo()  # el paso de GEOMFN
    assert w.input.doc.model.geomfn == "3discs.geo"
    w._msgs.clear()
    w._answers["questions"] = [SB.Cancel]
    assert not w.save_all()
    assert len(_asked(w)) == 1 and "3discs.geo" in _asked(w)[0] and "otros cuerpos" in _asked(w)[0]
    assert _disk_refs(stack_dir) == before
    w._answers["questions"] = [SB.Save]  # «Guardar de todos modos»: es su decisión
    assert w.save_all() and not w.input.dirty


def test_saving_a_renumbered_geometry_warns_about_other_ins_that_use_it(qtbot, window, stack_dir):
    shutil.copy2(stack_dir / "stack.in", stack_dir / "otro.in")
    w = window
    w._answers["question"] = SB.Cancel
    w.open_file(stack_dir / "stack.in")
    _add_sphere(w)  # stack.in la sigue; otro.in, no
    old = _bytes(stack_dir, "3discs.geo", "stack.in", "otro.in")
    w._answers["questions"] = [SB.Cancel]
    assert not w.save_all()
    q = _asked(w)[-1]
    assert "otro.in" in q and "stack.in" not in q.split("\n\n")[1]
    assert _bytes(stack_dir, "3discs.geo", "stack.in", "otro.in") == old
    # Sin renumerar (solo un nombre), no se pregunta.
    w.undo()
    w._msgs.clear()
    w.edit_geometry("Nombre", lambda doc, g: ge.set_description(ge.find_element(doc, "body", "1"), "Disco"))
    assert w.save_all() and not _asked(w)


def test_reload_reopens_only_the_geometry_the_in_does_not_use(qtbot, window, stack_dir, monkeypatch):
    """Tras guardar la geometría con un nombre de más de 20 caracteres (GEOMFN no lo sigue), «Recargar» abría el
    .in y con él 3discs.geo, no el fichero que había cambiado."""
    w = window
    w.open_file(stack_dir / "stack.in")
    new = stack_dir / "tres-discos-version2.geo"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new), ""))
    assert w.save_geometry_as()
    assert w.session.path == new and w.input.doc.model.geomfn == "3discs.geo"
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [1.0, 2.0, 3.0]))
    w.edit_geometry("Nombre", lambda doc, g: ge.set_description(ge.find_element(doc, "body", "1"), "Disco"))
    new.write_bytes(new.read_bytes() + b"\n")  # otro editor
    ctrl = w.input
    w._answers["questions"] = [SB.Reset]  # «Recargar»
    assert not w.save_geometry()
    assert w.session.path == new and not w.geo_ctrl.dirty and "Disco" not in w.geo_ctrl.text
    assert w.input is ctrl and w.input.dirty  # el .in (que no usa esta geometría) se queda como estaba


def test_reload_keeps_a_new_in_that_was_never_saved(qtbot, window, case_dir, monkeypatch):
    w = window
    w.open_file(case_dir / "cubo-esfera.geo", with_input=False)
    w.new_input()
    ctrl = w.input
    w.input.edit("Mover la fuente", lambda d: se.set_position(d, [1.0, 2.0, 3.0]))
    w.edit_geometry("Nombre", lambda doc, g: ge.set_description(ge.find_element(doc, "body", "   1"), "Cubo"))
    g = case_dir / "cubo-esfera.geo"
    g.write_bytes(g.read_bytes() + b"\n")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(case_dir / "nuevo.in"), ""))
    w._answers["questions"] = [SB.Reset]
    assert not w.save_all()
    assert w.input is ctrl and w.input.path is None and w.input.dirty
    assert not w.geo_ctrl.dirty and w.session.ok and not (case_dir / "nuevo.in").exists()
    assert w.save_all() and (case_dir / "nuevo.in").is_file()


def test_saving_the_in_in_another_folder_moves_penhmains_folder(qtbot, window, tmp_path, monkeypatch):
    """«GEOMFN sub/3discs.geo» (part.geo en la carpeta del .in); un .in nuevo guardado en sub/ hace que penhmain se
    ejecute en sub/: ARGUS lee de allí el INCLUDE (copiado, como con «Guardar la geometría como…»)."""
    w = window
    d = _subfolder_case(tmp_path, ".")
    w.open_file(d / "case.in")
    assert w.session.ok and w.session.work_dir == d
    w._answers["question"] = SB.Discard
    w.new_input()
    target = d / "sub" / "nuevo.in"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert w.save_input() and target.is_file()
    assert w.geo_ctrl.work_dir == d / "sub" and w.session.ok
    assert (d / "sub" / "part.geo").read_bytes() == (d / "part.geo").read_bytes()
    from argus.session import open_case

    assert open_case(target).session.ok  # como lo abriría penhmain
