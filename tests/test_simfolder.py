"""Preparar la carpeta de simulación: qué se copia, de dónde, y que nunca se sobrescribe nada."""

import os
import shutil
from pathlib import Path

import pytest

from argus.penhan.inputfile import parse_in_file
from argus.penhan.simfolder import default_destination, execute, plan_folder, summary
from argus.session import open_case
from conftest import CORPUS

CASE = CORPUS / "usuario/100Mev15-174"


def _mat(name: str, isotopes: list[tuple[str, int]]) -> str:
    """Un .mat de mentira: solo la cabecera y la lista final de ficheros ENDF (lo que lee ARGUS)."""
    lines = [" PENELOPE (v. 2018)  Material data file ...............",
             f" Material: {name}", " Mass density = 1.00000000E+00 g/cm**3", " ... tablas ...",
             f" *** ENDF filenames and abundances of isotopes,  NISOT = {len(isotopes):3d}"]
    for f, iza in isotopes:
        lines.append(f" {f:<25} {iza:6d} 1.00000E+00")
    lines.append("   0 = NALPHA")
    return "\n".join(lines) + "\n"


@pytest.fixture
def case(tmp_path):
    src = tmp_path / "caso"
    src.mkdir()
    for f in ("maniqui.in", "cubo-esfera.geo"):
        shutil.copy2(CASE / f, src / f)
    (src / "water.mat").write_text(_mat("Water", [("pO16_ICRU63.endf", 8016), ("pO17_TENDL.endf", 8017)]))
    (src / "pO16_ICRU63.endf").write_text("endf O16 (copia local)\n")
    db = tmp_path / "database"
    (db / "ENDF_data").mkdir(parents=True)
    (db / "material-list.txt").write_text("  1  Hydrogen\n")
    for f in ("pO16_ICRU63.endf", "pO17_TENDL.endf"):
        (db / "ENDF_data" / f).write_text(f"endf {f} (base de datos)\n")
    exe = tmp_path / "bin" / "penhmain.x"
    exe.parent.mkdir()
    exe.write_bytes(b"\x7fELF...")
    os.chmod(exe, 0o755)
    return src / "maniqui.in", db, exe


def _plan(in_path, db, exe, dest):
    cf = open_case(in_path)
    return plan_folder(in_path, cf.input_doc, dest, database=db, penhmain=exe)


def test_everything_penhmain_needs_is_copied(case, tmp_path):
    in_path, db, exe = case
    dest = default_destination(in_path)
    assert dest.name == "maniqui_sim" and dest.parent == in_path.parent
    plan = _plan(in_path, db, exe, dest)
    names = {i.name: i for i in plan.items}
    assert set(names) >= {"maniqui.in", "cubo-esfera.geo", "water.mat", "pO16_ICRU63.endf", "pO17_TENDL.endf",
                          "penhmain.x"}
    # Los ENDF se buscan primero junto al .mat y después en la base de datos.
    assert names["pO16_ICRU63.endf"].source.parent == in_path.parent
    assert names["pO17_TENDL.endf"].source.parent == db / "ENDF_data"
    assert not plan.missing and not plan.conflicts
    copied = execute(plan)
    assert {p.name for p in copied} == set(names)
    assert (dest / "pO16_ICRU63.endf").read_text().endswith("(copia local)\n")
    assert os.access(dest / "penhmain.x", os.X_OK)
    assert "0 ya estaban" in summary(plan) or "copiados" in summary(plan)
    # Preparar otra vez: ya está todo (no se copia nada) y la carpeta propuesta pasa a ser otra.
    again = _plan(in_path, db, exe, dest)
    assert not again.to_copy and len(again.by_status("same")) == len(again.items)
    assert default_destination(in_path).name == "maniqui_sim2"


def test_nothing_is_ever_overwritten(case, tmp_path):
    in_path, db, exe = case
    dest = tmp_path / "destino"
    dest.mkdir()
    (dest / "water.mat").write_text("otro material del usuario\n")
    plan = _plan(in_path, db, exe, dest)
    assert [i.name for i in plan.conflicts] == ["water.mat"]
    execute(plan)
    assert (dest / "water.mat").read_text() == "otro material del usuario\n"
    assert "No se ha tocado water.mat" in summary(plan)


def test_missing_files_and_particles_that_do_not_need_endf(case, tmp_path):
    in_path, db, exe = case
    (in_path.parent / "water.mat").unlink()
    plan = _plan(in_path, db, exe, tmp_path / "d1")
    assert [i.name for i in plan.missing] == ["water.mat"]
    # Con electrones no hacen falta los ENDF.
    text = in_path.read_bytes().decode("latin-1").replace("SKPAR  4", "SKPAR  1")
    in_path.write_bytes(text.encode("latin-1"))
    (in_path.parent / "water.mat").write_text(_mat("Water", [("pO16_ICRU63.endf", 8016)]))
    plan = _plan(in_path, db, exe, tmp_path / "d2")
    assert not any(i.kind == "endf" for i in plan.items)
    assert any("ENDF" in n for n in plan.notes)


def test_ct_and_its_card_go_with_the_geometry(tmp_path):
    from test_ct_import import _geometry_with_ct, _import

    imp, built = _import(tmp_path)
    folder = tmp_path / "sim"
    folder.mkdir()
    imp.write(folder, built)
    _geometry_with_ct(folder, built)
    in_text = (CASE / "maniqui.in").read_bytes().decode("latin-1").replace("cubo-esfera.geo", "g.geo          ")
    (folder / "caso.in").write_bytes(in_text.encode("latin-1"))
    doc = parse_in_file(folder / "caso.in")
    plan = plan_folder(folder / "caso.in", doc, tmp_path / "dest")
    kinds = {i.kind: i for i in plan.items}
    assert kinds["ct"].name == "tac.ct" and kinds["ct"].status == "copy"
    assert kinds["ctjson"].name == "tac.ct.json"


# -- revisión 0.8.1 ---------------------------------------------------------------------------
Z = "0" * 64
END = "END      " + "0" * 55 + "\n"


def _set_in(in_path, old: str, new: str) -> None:
    text = in_path.read_bytes().decode("latin-1")
    assert old in text
    in_path.write_bytes(text.replace(old, new).encode("latin-1"))


def test_include_and_ct_come_from_the_saved_geometry(case, tmp_path):
    """El .ct y los INCLUDE salen del .geo que cita GEOMFN (el que leerá penhmain), también si el CTBOX está en
    un fichero incluido y aunque ARGUS no tenga la geometría abierta o no la pueda compilar."""
    in_path, db, exe = case
    src = in_path.parent
    (src / "main.geo").write_text(f"Principal\n{Z}\nINCLUDE\n   FILE=(sub.geo     )\n{Z}\n{END}")
    (src / "sub.geo").write_text(f"Incluida\n{Z}\nCTBOX   ( CTB)   TAC\nCTFNAME=(tac.ct      )\n{Z}\n{END}")
    (src / "tac.ct").write_text("ct\n")
    _set_in(in_path, "GEOMFN cubo-esfera.geo", "GEOMFN main.geo       ")
    plan = plan_folder(in_path, parse_in_file(in_path), tmp_path / "d", database=db, penhmain=exe)
    kinds = {i.name: i.kind for i in plan.items}
    assert kinds["main.geo"] == "geo" and kinds["sub.geo"] == "include" and kinds["tac.ct"] == "ct"
    assert "tac.ct.json" not in kinds  # la ficha es opcional: si no está, no se menciona
    # Sin el .ct: falta (penhmain se detendría con IRETRN 2106) y se dice.
    (src / "tac.ct").unlink()
    plan = plan_folder(in_path, parse_in_file(in_path), tmp_path / "d", database=db, penhmain=exe)
    assert [i.name for i in plan.missing] == ["tac.ct"]
    assert "FALTA TAC (vóxeles): tac.ct" in summary(plan)


def test_absolute_and_parent_paths(case, tmp_path):
    """Un .mat con ruta absoluta lo lee penhmain de allí (no se copia) pero sus ENDF sí hacen falta en la carpeta;
    uno con '..' apuntaría a otro sitio desde la carpeta nueva: no se copia y se avisa."""
    import tempfile

    in_path, db, exe = case
    other = Path(tempfile.mkdtemp(prefix="", dir="/tmp"))  # MFNAME es A20: hace falta una ruta corta
    try:
        bone_name = str(other / "h.mat")
        assert len(bone_name) <= 20
        (other / "h.mat").write_text(_mat("Hueso", [("pCa40_ICRU63.endf", 20040)]))
        _check_absolute_and_parent(in_path, db, exe, tmp_path, bone_name)
    finally:
        shutil.rmtree(other)


def _check_absolute_and_parent(in_path, db, exe, tmp_path, bone_name):
    (db / "ENDF_data" / "pCa40_ICRU63.endf").write_text("endf Ca40\n")
    (tmp_path / "grasa.mat").write_text(_mat("Grasa", [("pC12_ICRU63.endf", 6012)]))
    (db / "ENDF_data" / "pC12_ICRU63.endf").write_text("endf C12\n")
    extra = (f"MFNAME {bone_name}\nMSIMPA 10e3 2e3 10e3 0.10 0.10 2e3 2e3\n"
             "MFNAME ..\\grasa.mat\nMSIMPA 10e3 2e3 10e3 0.10 0.10 2e3 2e3\n")
    _set_in(in_path, "MSIMPN 1.0 500", "MSIMPN 1.0 500\n" + extra.rstrip("\n"))
    dest = tmp_path / "d"
    plan = _plan(in_path, db, exe, dest)
    items = {i.name: i for i in plan.items}
    bone = items[bone_name]
    assert bone.status == "absolute" and bone.source == Path(bone_name)
    assert items["pCa40_ICRU63.endf"].status == "copy"  # penhmain lo abre por su nombre en la carpeta de trabajo
    fat = items["..\\grasa.mat"]
    assert fat.status == "outside" and fat in plan.problems
    assert items["pC12_ICRU63.endf"].status == "copy"
    execute(plan)
    assert not (tmp_path / "grasa.mat.argus-tmp").exists() and (tmp_path / "grasa.mat").read_text().startswith(" PEN")
    text = summary(plan)
    assert "..\\grasa.mat: la ruta sube de carpeta" in text and "ruta absoluta, penhmain lo leerá de allí" in text


@pytest.mark.parametrize("name,where", [
    ("w.mat", "ok"), ("sub/w.mat", "ok"), ("sub\\w.mat", "ok"), ("C:\\data\\w.mat", "absolute"),
    ("\\\\servidor\\datos\\w.mat", "absolute"), ("C:w.mat", "outside"), ("\\mats\\w.mat", "outside"),
    ("..\\w.mat", "outside"), ("sub/../../w.mat", "outside")])
def test_windows_names_never_write_outside_the_folder(name, where):
    from argus.penhan.simfolder import classify

    assert classify(name) == where


def _tmp_left(folder):
    return [p.name for p in folder.rglob("*") if p.name.endswith(".argus-tmp")]


def test_a_file_that_appears_during_the_copy_is_never_overwritten(case, tmp_path, monkeypatch):
    from argus.core import files

    in_path, db, exe = case
    dest = tmp_path / "d"
    plan = _plan(in_path, db, exe, dest)
    real = files.publish_new

    def racing(tmp, target):
        if target.name == "water.mat":  # el usuario lo crea justo mientras se copia
            target.write_text("mío\n")
        return real(tmp, target)

    monkeypatch.setattr(files, "publish_new", racing)
    copied = execute(plan)
    assert (dest / "water.mat").read_text() == "mío\n"
    assert [i.name for i in plan.conflicts] == ["water.mat"] and dest / "water.mat" not in copied
    assert not _tmp_left(dest)


def test_publishing_without_hard_links(case, tmp_path, monkeypatch):
    """En discos sin enlaces duros (FAT, algunas unidades de red) se renombra, sin sobrescribir."""
    from argus.core import files

    def no_links(*a, **k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(files.os, "link", no_links)
    t1 = tmp_path / "a.tmp"
    t1.write_text("nuevo\n")
    assert files.publish_new(t1, tmp_path / "a") and (tmp_path / "a").read_text() == "nuevo\n"
    t2 = tmp_path / "b.tmp"
    t2.write_text("nuevo\n")
    (tmp_path / "b").write_text("viejo\n")
    assert not files.publish_new(t2, tmp_path / "b") and (tmp_path / "b").read_text() == "viejo\n"


def test_cancel_stops_between_chunks_and_removes_the_partial_copy(case, tmp_path, monkeypatch):
    from argus.core import files

    in_path, db, exe = case
    big = in_path.parent / "psf.dat"
    big.write_bytes(b"1 1.0E6 0 0 0 0 0 1 1 0 0 0 0 1\n" * 4000)
    _set_in(in_path, "RESUME dump1.dmp    ", "RESUME psf.dat      ")  # un fichero grande en la lista
    monkeypatch.setattr(files, "CHUNK", 4096)
    dest = tmp_path / "d"
    plan = _plan(in_path, db, exe, dest)
    seen = []

    def cancel():  # se cancela cuando ya hay 8 kB de psf.dat copiados
        part = [p.stat().st_size for p in dest.glob(".psf.dat.*")]
        seen.extend(part)
        return bool(part) and part[0] >= 8192

    execute(plan, cancel=cancel)
    assert max(seen) >= 8192 and plan.cancelled
    assert not (dest / "psf.dat").exists() and not _tmp_left(dest)
    assert (dest / "maniqui.in").is_file()  # lo copiado antes se queda
    assert "Cancelado" in summary(plan)


def test_copy_errors_are_reported_and_leave_no_temporaries(case, tmp_path):
    in_path, db, exe = case
    dest = tmp_path / "d"
    plan = _plan(in_path, db, exe, dest)
    (in_path.parent / "water.mat").unlink()  # desaparece después de hacer la lista
    copied = execute(plan)
    assert [i.name for i in plan.failed] == ["water.mat"] and plan.failed[0].note
    assert {p.name for p in copied} >= {"maniqui.in", "cubo-esfera.geo"}  # los demás sí
    assert not _tmp_left(dest) and "No se ha podido copiar water.mat" in summary(plan)


def test_unreadable_or_odd_targets_are_conflicts(case, tmp_path, monkeypatch):
    from argus.penhan import simfolder

    in_path, db, exe = case
    dest = tmp_path / "d"
    (dest / "water.mat").mkdir(parents=True)  # una carpeta con el nombre del .mat
    shutil.copy2(in_path, dest / "maniqui.in")
    monkeypatch.setattr(simfolder, "_digest", lambda p: (_ for _ in ()).throw(PermissionError(13, "denegado")))
    plan = _plan(in_path, db, exe, dest)  # no lanza
    st = {i.name: i.status for i in plan.items}
    assert st["water.mat"] == "conflict" and st["maniqui.in"] == "conflict"  # no se puede comprobar: no se toca


def test_mingw_libraries_travel_with_the_exe(case, tmp_path):
    in_path, db, _exe = case
    bin_dir = tmp_path / "mingw"
    bin_dir.mkdir()
    exe = bin_dir / "penhmain.exe"
    exe.write_bytes(b"MZ")
    for f in ("libgfortran-5.dll", "libquadmath-0.dll", "libgcc_s_seh-1.dll", "libwinpthread-1.dll", "otra.dll"):
        (bin_dir / f).write_bytes(b"MZ")
    plan = _plan(in_path, db, exe, tmp_path / "d")
    dlls = sorted(i.name for i in plan.items if i.kind == "dll")
    assert dlls == ["libgcc_s_seh-1.dll", "libgfortran-5.dll", "libquadmath-0.dll", "libwinpthread-1.dll"]
    assert all(not i.required and i.status == "copy" for i in plan.items if i.kind == "dll")


def test_phase_space_sources_need_no_endf(case, tmp_path):
    """Con IPSFN penhmain no llama a PHINIT (KPARP se queda en 1): no lee los ENDF aunque diga SKPAR 4."""
    from argus.penhan.materials import hadron_data_needed
    from argus.penhan.validate import validate_case

    in_path, db, exe = case
    (in_path.parent / "psf.dat").write_text("# KPAR E X Y Z U V W WGHT ILB1-4 NSHI\n"
                                            "4 1.0E8 0 0 -10 0 0 1 1 1 0 0 0 1\n")
    _set_in(in_path, "       >>>>>>>> Material data", "IPSFN  psf.dat\n       >>>>>>>> Material data")
    doc = parse_in_file(in_path)
    assert doc.model.ok and doc.model.source.psf == ["psf.dat"] and hadron_data_needed(doc) is False
    plan = _plan(in_path, db, exe, tmp_path / "d")
    assert not any(i.kind == "endf" for i in plan.items) and any(i.name == "psf.dat" for i in plan.items)
    assert any("espacio de fases" in n for n in plan.notes)
    msgs = [d.message for d in validate_case(doc, None, in_path.parent)]
    assert any("tiene protones o alfas" in m for m in msgs)


def test_publishing_on_windows_uses_rename_that_never_overwrites(tmp_path, monkeypatch):
    """En Windows, os.rename falla si el destino existe (FileExistsError): se emula aquí."""
    from argus.core import files

    real_rename = os.rename

    def windows_rename(src, dst):
        if os.path.lexists(dst):
            raise FileExistsError(183, "Cannot create a file when that file already exists", str(dst))
        real_rename(src, dst)

    monkeypatch.setattr(files.os, "name", "nt")
    monkeypatch.setattr(files.os, "rename", windows_rename)
    a = tmp_path / "a.tmp"
    a.write_text("nuevo\n")
    assert files.publish_new(a, tmp_path / "a") and (tmp_path / "a").read_text() == "nuevo\n" and not a.exists()
    b = tmp_path / "b.tmp"
    b.write_text("nuevo\n")
    (tmp_path / "b").write_text("viejo\n")
    assert not files.publish_new(b, tmp_path / "b") and (tmp_path / "b").read_text() == "viejo\n"
