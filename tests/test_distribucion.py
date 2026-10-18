"""Los ejecutables de ARGUS (0.17.0, ADR-022): lo que va en el código y lo que lo empaqueta.

* ``argus.app``: los ajustes del ejecutable (la caché de Numba fuera del programa), ``--comprobar`` y los
  argumentos que añade el Finder de macOS; los ficheros que macOS pide abrir (``QFileOpenEvent``).
* ``argus.comprobar``: la comprobación de una instalación (un informe sin preguntar nada a nadie).
* ``packaging/``: la receta de PyInstaller, el instalador de Windows, el flujo de GitHub, la revisión del
  repositorio (nada de PENELOPE, PenGeomJar ni DICOM) y la construcción de los paquetes.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

from conftest import ROOT

PACKAGING = ROOT / "packaging"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def construir():
    return _load("argus_construir", PACKAGING / "construir.py")


@pytest.fixture(scope="module")
def revisar():
    return _load("argus_revisar_repo", PACKAGING / "revisar_repo.py")


# ---------------------------------------------------------------------------------------------------------------
# argus.app y argus.core.userdata
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("plat, env, tail", [
    ("win32", {"LOCALAPPDATA": "/x/Local"}, "/x/Local/ARGUS"),
    ("darwin", {}, "Library/Caches/ARGUS"),
    ("linux", {"XDG_CACHE_HOME": "/x/cache"}, "/x/cache/ARGUS"),
    ("linux", {"XDG_CACHE_HOME": "relativa"}, ".cache/ARGUS"),  # la norma XDG: una relativa no vale
    ("linux", {}, ".cache/ARGUS"),
])
def test_the_cache_folder_of_each_system(monkeypatch, plat, env, tail):
    from argus.core import userdata

    for var in ("LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(userdata.sys, "platform", plat)
    got = userdata.user_cache_dir().as_posix()
    assert got.endswith(tail)
    assert userdata.user_cache_dir() != userdata.user_data_dir()  # lo que se puede borrar va aparte


def test_frozen_setup_only_in_the_executable(monkeypatch, tmp_path):
    from argus import app

    monkeypatch.delenv("NUMBA_CACHE_DIR", raising=False)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    app.frozen_setup()
    assert "NUMBA_CACHE_DIR" not in os.environ and not sys.dont_write_bytecode

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    app.frozen_setup()
    assert sys.dont_write_bytecode
    assert Path(os.environ["NUMBA_CACHE_DIR"]) == tmp_path / "ARGUS" / "numba"

    monkeypatch.setenv("NUMBA_CACHE_DIR", "/otra")  # la que se haya dado se respeta
    app.frozen_setup()
    assert os.environ["NUMBA_CACHE_DIR"] == "/otra"


def test_arguments_of_the_finder_are_dropped():
    from argus.app import build_parser, clean_argv

    assert clean_argv(["-psn_0_1234567", "caso.in"]) == ["caso.in"]
    args = build_parser().parse_args(clean_argv(["-psn_0_99"]))
    assert args.fichero is None and args.comprobar is None


def test_the_check_option():
    from argus.app import REPORT_NAME, build_parser
    from argus.comprobar import REPORT_NAME as NAME

    p = build_parser()
    assert p.parse_args(["--comprobar"]).comprobar == ""  # sin fichero: el de por omisión
    assert p.parse_args(["--comprobar", "informe.txt"]).comprobar == "informe.txt"
    assert p.parse_args([]).comprobar is None
    assert REPORT_NAME == NAME
    assert "--comprobar" in p.format_help()


@pytest.mark.gui
def test_main_with_check_runs_the_checks_and_not_the_window(monkeypatch, qapp, tmp_path):
    from argus import app, comprobar
    from argus.ui import main_window

    calls = []
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # main() pone el suyo: se deja como estaba
    monkeypatch.setattr(app, "_install_translations", lambda a: None)  # sin cambiar el idioma a las demás pruebas
    monkeypatch.setattr(comprobar, "run_checks", lambda a, path=None: calls.append((a, path)) or 7)
    monkeypatch.setattr(main_window, "MainWindow", lambda *a, **k: pytest.fail("no debe abrir la ventana"))
    before = (qapp.style().name(), qapp.applicationName(), qapp.organizationName(), qapp.windowIcon())
    try:
        assert app.main(["--comprobar", str(tmp_path / "i.txt")]) == 7
        assert calls == [(qapp, str(tmp_path / "i.txt"))]
        assert app.main(["--comprobar"]) == 7 and calls[-1][1] is None
    finally:  # main() da a la aplicación su estilo, su nombre y su icono: se dejan como estaban
        qapp.setStyle(before[0])
        qapp.setApplicationName(before[1])
        qapp.setOrganizationName(before[2])
        qapp.setWindowIcon(before[3])


@pytest.mark.gui
def test_files_that_macos_asks_to_open(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFileOpenEvent
    from PySide6.QtWidgets import QApplication

    from argus.ui.file_open import ArgusApplication, FileOpenRequests

    req = FileOpenRequests()
    first = str(tmp_path / "antes de la ventana.in")
    assert req.handle(QFileOpenEvent(first))  # al abrir ARGUS con un fichero: aún no hay ventana, espera
    assert req.pending == [first]
    opened = []
    req.set_opener(opened.append)
    qtbot.waitUntil(lambda: opened == [first], timeout=2000)
    second = str(tmp_path / "caso con espacios.geo")
    assert req.handle(QFileOpenEvent(second))
    assert opened == [first]  # no dentro del evento...
    qtbot.waitUntil(lambda: opened == [first, second], timeout=2000)  # ...sino en la vuelta siguiente
    assert req.requested == [first, second] and req.pending == []
    assert not req.handle(QEvent(QEvent.Type.ApplicationActivate))  # lo demás sigue su camino
    assert issubclass(ArgusApplication, QApplication)


ARGUS_APPLICATION = r"""
import sys
from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtGui import QFileOpenEvent
from argus.ui.file_open import ArgusApplication

app = ArgusApplication(["argus"])          # Qt manda eventos a la aplicación mientras se construye
QObject(app)                                # y al darle un hijo (ChildAdded)
assert QCoreApplication.sendEvent(app, QFileOpenEvent("/x/caso.in"))
assert app.file_open_requests.pending == ["/x/caso.in"]
opened = []
app.file_open_requests.set_opener(opened.append)
app.processEvents()
assert opened == ["/x/caso.in"], opened
print("ok")
"""


def test_argus_application_takes_the_files_that_macos_asks_to_open(tmp_path):
    """Una QApplication de verdad (en otro proceso: en este ya hay una): la que crea ``argus.app.main``."""
    pytest.importorskip("PySide6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run([sys.executable, "-c", ARGUS_APPLICATION], capture_output=True, text=True, env=env,
                          timeout=120, cwd=tmp_path)
    assert proc.returncode == 0 and proc.stdout.strip().endswith("ok"), proc.stdout + proc.stderr


# ---------------------------------------------------------------------------------------------------------------
# argus.comprobar
# ---------------------------------------------------------------------------------------------------------------
def test_report_result_and_text():
    from argus.comprobar import FAIL, INFO, OK, Report, Result

    r = Report(header=["Sistema: prueba"])
    r.results += [Result("Una", OK, "bien", 1.25), Result("Otra", INFO, "depende del ordenador")]
    assert r.ok and r.status_of("Otra") == INFO and r.status_of("Ninguna") is None
    text = r.text()
    assert "[ OK  ] Una (1,2 s)" in text or "[ OK  ] Una (1,3 s)" in text
    assert "[INFO ] Otra" in text and "Resultado: todo correcto." in text
    r.dialogs.append("Error: algo")
    assert not r.ok and "[AVISO] Apareció un aviso: Error: algo" in r.text()
    r.dialogs.clear()
    r.results.append(Result("Mala", FAIL, "ValueError: x"))
    assert not r.ok and "hay problemas" in r.text()
    r.results.pop()
    r.aborted = "La comprobación no terminó en 5 s."
    assert not r.ok and "[FALLO] La comprobación no terminó" in r.text()


def test_the_sample_geometry_is_a_sphere_in_a_box():
    from argus.comprobar import sample_geometry_text
    from argus.pengeom.compiler import compile_geometry
    from argus.pengeom.parser import parse_geo_text

    geo = compile_geometry(parse_geo_text(sample_geometry_text()))
    assert geo.ok and geo.nbodys == 3
    assert sorted(b.material for b in geo.bodies) == [0, 1, 2]


def test_check_dicom_writes_and_reads_a_ct(tmp_path):
    pytest.importorskip("pydicom")
    from argus.comprobar import OK, check_dicom

    status, detail = check_dicom(tmp_path)
    assert status == OK, detail
    assert not any(p.suffix == ".dcm" for p in tmp_path.rglob("*"))  # sin extensión, como muchos equipos


@pytest.fixture
def spanish_qt(qapp):
    """Las traducciones de Qt que pone ``argus.app.main`` (y se quitan después: las demás pruebas van sin ellas)."""
    from argus.app import _install_translations

    _install_translations(qapp)
    yield qapp
    tr = getattr(qapp, "_argus_translator", None)
    if tr is not None:
        qapp.removeTranslator(tr)
        del qapp._argus_translator


@pytest.mark.gui
def test_the_whole_check_from_the_source(spanish_qt, tmp_path):
    from argus.comprobar import run_checks

    report = tmp_path / "informe.txt"
    before = os.environ.get("ARGUS_SETTINGS")
    assert run_checks(spanish_qt, report, hard_exit=False) == 0, report.read_text(encoding="utf-8")
    text = report.read_text(encoding="utf-8")
    for name in ("Traducciones de Qt", "Numba", "Ventana de ARGUS", "Lectura de un TAC", "Gráficas",
                 "Animaciones GIF", "Vídeos", "OpenGL"):
        assert name in text
    assert "Resultado: todo correcto." in text
    assert re.search(r"^\[\s*OK\s*\] Ventana de ARGUS", text, re.MULTILINE)
    assert os.environ.get("ARGUS_SETTINGS") == before  # los ajustes de prueba se quitan al terminar


@pytest.mark.gui
def test_a_dialog_during_the_check_is_closed_and_reported(monkeypatch, qapp, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from argus import comprobar

    def plots_with_a_warning():
        QMessageBox.warning(None, "Gráficas", "No se puede dibujar")  # se queda esperando... a nadie
        return comprobar.OK, "dibujada"

    for name in ("check_numba", "check_window", "check_video", "check_opengl", "check_gif"):
        monkeypatch.setattr(comprobar, name, lambda *a, **k: (comprobar.OK, "rápida"))
    monkeypatch.setattr(comprobar, "check_plots", plots_with_a_warning)
    report = tmp_path / "informe.txt"
    assert comprobar.run_checks(qapp, report, hard_exit=False) == 1
    text = report.read_text(encoding="utf-8")
    assert "[AVISO] Apareció un aviso: Gráficas: No se puede dibujar" in text


@pytest.mark.gui
def test_a_check_that_does_not_end_writes_what_there_is(monkeypatch, qapp, tmp_path):
    import time

    from PySide6.QtWidgets import QApplication

    from argus import comprobar

    def slow():
        end = time.perf_counter() + 1.5
        while time.perf_counter() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        return comprobar.OK, "lenta"

    for name in ("check_numba", "check_window", "check_video", "check_opengl", "check_gif", "check_plots"):
        monkeypatch.setattr(comprobar, name, lambda *a, **k: (comprobar.OK, "rápida"))
    monkeypatch.setattr(comprobar, "check_translations", lambda app: slow())
    report = tmp_path / "informe.txt"
    assert comprobar.run_checks(qapp, report, timeout_s=0.5, hard_exit=False) == 1
    assert "La comprobación no terminó en 0 s." in report.read_text(encoding="utf-8")


@pytest.mark.gui
def test_a_check_that_fails_says_what_and_where(monkeypatch, qapp, tmp_path):
    import tempfile

    from argus import comprobar

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # el informe de reserva va a la carpeta temporal

    def broken():
        raise ValueError("sin datos")

    for name in ("check_translations", "check_numba", "check_window", "check_video", "check_opengl", "check_gif",
                 "check_dicom"):
        monkeypatch.setattr(comprobar, name, lambda *a, **k: (comprobar.OK, "rápida"))
    monkeypatch.setattr(comprobar, "check_plots", broken)
    report = tmp_path / "sub" / "informe.txt"  # una carpeta que no existe: el informe va a la temporal
    assert comprobar.run_checks(qapp, report, hard_exit=False) == 1
    alt = tmp_path / comprobar.REPORT_NAME
    text = alt.read_text(encoding="utf-8")
    assert "[FALLO] Gráficas (Matplotlib)" in text
    assert "ValueError: sin datos (en test_distribucion.py:" in text


# ---------------------------------------------------------------------------------------------------------------
# packaging/: la receta de PyInstaller y lo que la rodea
# ---------------------------------------------------------------------------------------------------------------
def _spec_value(name: str):
    tree = ast.parse((PACKAGING / "argus.spec").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} no está en argus.spec")


def test_every_numba_kernel_with_a_cache_goes_as_source():
    """Un módulo nuevo con ``@njit(cache=True)`` tiene que ir en NUMBA_MODULES (si no, su caché depende de la carpeta
    desde la que se abre ARGUS y se compila cada vez)."""
    listed = set(_spec_value("NUMBA_MODULES"))
    src = ROOT / "src"
    cached = {".".join(p.relative_to(src).with_suffix("").parts) for p in src.rglob("*.py")
              if "cache=True" in p.read_text(encoding="utf-8") and "__pycache__" not in p.parts}
    assert cached == listed


def test_the_spec_compiles_and_names_what_exists():
    text = (PACKAGING / "argus.spec").read_text(encoding="utf-8")
    compile(text, "argus.spec", "exec")
    assert (PACKAGING / "lanzador.py").is_file()
    assert "argus.oracle" in text  # la sonda de pruebas no va en el ejecutable
    assert "matplotlib.backends.backend_pdf" in text and "matplotlib.backends.backend_svg" in text


def test_the_windows_installer_script():
    raw = (PACKAGING / "windows" / "argus.iss").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 con BOM: Inno Setup lee así las tildes
    text = raw.decode("utf-8-sig")
    assert "AppId={{DFB19C1E-AF4A-464E-A8C7-591729650167}" in text  # el mismo siempre: una versión sustituye a otra
    assert re.search(r"#ifdef Clave\s+Password=\{#Clave\}\s+Encryption=yes\s+#endif", text)
    assert "PrivilegesRequired=lowest" in text and "Spanish.isl" in text
    assert 'Name: "{app}\\_internal"' in text  # lo de la versión anterior se quita


def test_the_readme_names_every_package(construir):
    readme = (PACKAGING / "LEEME.txt").read_text(encoding="utf-8")
    for tail in ("Windows-instalador.exe", "macOS-AppleSilicon.dmg", "macOS-Intel.dmg", "Linux-x86_64.tar.gz",
                 "--comprobar", "Abrir igualmente", "Ejecutar de todas formas"):
        assert tail in readme
    src = (PACKAGING / "construir.py").read_text(encoding="utf-8")
    assert '-Windows-instalador"' in src and 'macOS-AppleSilicon' in src and 'macOS-Intel' in src


def test_package_password_rules(monkeypatch, construir):
    monkeypatch.delenv("ARGUS_CLAVE", raising=False)
    assert construir.package_password() is None
    monkeypatch.setenv("ARGUS_CLAVE", "Sobp-2026!")
    assert construir.package_password() == "Sobp-2026!"
    for bad in ("corta", "con espacios dentro", "contraseña-tilde", 'comillas"dobles'):
        monkeypatch.setenv("ARGUS_CLAVE", bad)
        with pytest.raises(SystemExit):
            construir.package_password()


def test_system_label(monkeypatch, construir):
    monkeypatch.setattr(construir.sys, "platform", "darwin")
    monkeypatch.setattr(construir.platform, "machine", lambda: "arm64")
    assert construir.system_label() == "macOS-AppleSilicon"
    monkeypatch.setattr(construir.platform, "machine", lambda: "x86_64")
    assert construir.system_label() == "macOS-Intel"
    monkeypatch.setattr(construir.sys, "platform", "linux")
    assert construir.system_label() == "Linux-x86_64"
    monkeypatch.setattr(construir.sys, "platform", "win32")
    assert construir.system_label() == "Windows"


def test_linux_package_entries(construir, tmp_path):
    script = tmp_path / "pkg" / "instalar.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o600)
    with tarfile.open(tmp_path / "x.tar.gz", "w:gz") as tar:
        tar.add(script, arcname="ARGUS-1-Linux/instalar.sh", filter=construir._tar_entry)
    with tarfile.open(tmp_path / "x.tar.gz") as tar:
        info = tar.getmember("ARGUS-1-Linux/instalar.sh")
    assert info.mode == 0o755 and info.uid == 0 and info.uname == ""


def test_git_keeps_every_file_byte_for_byte():
    """Sin «* -text», git (con autocrlf en Windows) cambiaría los CRLF del corpus o de los .bat al subirlos."""
    lines = [ln.strip() for ln in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()]
    assert "* -text" in lines
    assert b"\r\n" in (ROOT / "ARGUS.bat").read_bytes()  # los .bat siguen con CRLF
    import conftest

    example = ROOT / "tests" / "corpus" / conftest.PRIVATE_CORPUS[0] / "arrow"  # un ejemplo de PenGeomJar
    if example.is_file():  # (no está en un clon del repositorio público)
        assert b"\r\n" in example.read_bytes()


def test_the_workflow_builds_the_four_packages_from_the_lock():
    text = (PACKAGING / "github" / "ejecutables.yml").read_text(encoding="utf-8")
    for needle in ("packaging/revisar_repo.py", "packaging/construir.py", "uv sync --frozen", "windows-2022",
                   "macos-15-intel", "ubuntu-22.04", "secrets.ARGUS_CLAVE", "--draft", "--draft=false"):
        assert needle in text
    assert "UV_VERSION" in text and "pyinstaller==" in text
    assert (ROOT / "uv.lock").is_file()


def test_the_version_is_the_same_everywhere():
    """La de ``pyproject.toml`` (la de los paquetes y la etiqueta de la publicación) es la que dice ARGUS (Ayuda →
    Acerca de ARGUS, ``--comprobar``) y la de ``uv.lock``: al subirla (0.17.1), en los tres sitios."""
    import tomllib

    import argus

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    mine = [p for p in lock["package"] if p["name"] == "argus"]
    assert argus.__version__ == version and len(mine) == 1 and mine[0]["version"] == version


def test_the_workflow_installs_what_qt_multimedia_needs_on_linux():
    """Qt Multimedia (los vídeos de la cascada) necesita libpulse.so.0 del sistema: sin ella fallaban la comprobación
    del ejecutable de Linux y la de las pruebas en la primera ejecución en GitHub (18-10-2026). PyInstaller la mete en
    el paquete si está en el ordenador donde se construye."""
    text = (PACKAGING / "github" / "ejecutables.yml").read_text(encoding="utf-8")
    tests = text[text.index("\n  pruebas:"):text.index("\n  construir:")]
    build = text[text.index("\n  construir:"):text.index("\n  publicar:")]
    for job in (tests, build):
        assert "libpulse0" in job and "libxrandr2" in job
    draft = text[text.index("\n  borrador:"):text.index("\n  pruebas:")]
    assert 'gh release edit "$TAG" --target "$GITHUB_SHA"' in draft  # un borrador que se repite va al commit nuevo

def _workflow_step_script(text: str, step_name: str) -> str:
    """El «run: |» de un paso del flujo (sin un lector de YAML: el bloque que sigue, más sangrado que «run:»)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {step_name}")
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    block = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return textwrap.dedent("\n".join(block)) + "\n"


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="hace falta bash (como en GitHub)")
@pytest.mark.parametrize("clave, sin_clave, ok", [
    ("", "", False),  # sin el secreto: se para (los paquetes irían sin contraseña en un repositorio público)
    ("", "si", True),  # salvo que se pida a propósito (variable ARGUS_SIN_CLAVE)
    ("Sobp-2026!", "", True),
    ("corta", "", False),  # contraseña no válida: se para antes de construir los cuatro
    ("con espacio 2026", "si", False),
])
def test_the_workflow_stops_before_building_without_a_valid_password(clave, sin_clave, ok):
    text = (PACKAGING / "github" / "ejecutables.yml").read_text(encoding="utf-8")
    review = text[text.index("\n  revisar:"):text.index("\n  borrador:")]
    assert "secrets.ARGUS_CLAVE" in review and "vars.ARGUS_SIN_CLAVE" in review  # en el primer trabajo
    script = _workflow_step_script(text, "La contraseña de los paquetes (secreto ARGUS_CLAVE)")
    script = script.replace("python3 ", f'"{sys.executable}" ')
    env = {**os.environ, "ARGUS_CLAVE": clave, "SIN_CLAVE": sin_clave}  # GitHub da "" si no existen
    done = subprocess.run(["bash", "-e", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    assert (done.returncode == 0) == ok, done.stdout + done.stderr
    if not clave and not ok:
        assert "::error::Falta el secreto ARGUS_CLAVE" in done.stdout
    if clave:
        assert clave not in done.stdout + done.stderr  # la contraseña no sale en el registro


# ---------------------------------------------------------------------------------------------------------------
# packaging/revisar_repo.py
# ---------------------------------------------------------------------------------------------------------------
def test_the_repository_check_finds_what_must_not_go(revisar, tmp_path):
    (tmp_path / "src" / "argus" / "oracle" / "fortran").mkdir(parents=True)
    (tmp_path / "src" / "argus" / "oracle" / "fortran" / "argus_probe.f90").write_text("! sonda\n")
    (tmp_path / "pengeom.f").write_text("C fuente\n")
    (tmp_path / "otro.f90").write_text("! fuera de la sonda\n")
    (tmp_path / "PenGeomJar.jar").write_bytes(b"PK")
    (tmp_path / "tac").mkdir()
    (tmp_path / "tac" / "IM0001").write_bytes(b"\0" * 128 + b"DICM" + b"\0" * 10)
    (tmp_path / "lib.dll").write_bytes(b"MZ")
    (tmp_path / "README.md").write_text("ARGUS\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "argus_probe.o").write_bytes(b"\0")  # build/ no se mira (no va: .gitignore)
    found = "\n".join(revisar.problems(tmp_path))
    assert "pengeom.f: fuente de Fortran" in found
    assert "otro.f90: fuente de Fortran fuera de la sonda" in found
    assert "PenGeomJar.jar: PenGeomJar" in found
    assert "tac/IM0001: fichero DICOM" in found
    assert "lib.dll: ejecutable o compilado" in found
    assert "argus_probe.f90" not in found and "README.md" not in found and "build/" not in found


def test_the_workflow_in_the_repository_is_the_copy_of_argus(revisar, tmp_path):
    """GitHub solo ejecuta .github/workflows; la copia de ARGUS está en packaging/github y deben ser iguales."""
    (tmp_path / "packaging" / "github").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "packaging" / "github" / "ejecutables.yml").write_text("name: Ejecutables\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "ejecutables.yml").write_text("name: Ejecutables\n", encoding="utf-8")
    assert revisar.problems(tmp_path) == []
    (tmp_path / ".github" / "workflows" / "ejecutables.yml").write_text("name: Otro\n", encoding="utf-8")
    assert any("no es igual que packaging/github/ejecutables.yml" in p for p in revisar.problems(tmp_path))


def test_the_private_corpus_stays_out_of_the_public_repository(revisar):
    """Los ejemplos de PENELOPE, PenGeomJar y penhan y los casos del usuario: fuera por .gitignore, la revisión los
    señala si se subieran, y las pruebas saben cuáles son."""
    import conftest

    ignored = {ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    names = conftest.PRIVATE_CORPUS
    assert {f"/tests/corpus/{n}/" for n in names} <= ignored
    assert revisar.PRIVATE_CORPUS == tuple(f"tests/corpus/{n}/" for n in names)


def test_the_repository_check_with_git(revisar, tmp_path):
    if shutil.which("git") is None:
        pytest.skip("sin git")
    private = revisar.PRIVATE_CORPUS[1]  # «tests/corpus/<maniquíes del ORNL>/»
    (tmp_path / private).mkdir(parents=True)
    (tmp_path / private / "adult.geo").write_text("ORNL\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("ARGUS\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "."], check=True)
    found = revisar.problems(tmp_path)
    assert found == [f"{private}adult.geo: corpus de pruebas que no va en el repositorio público (ejemplos "
                     "de PENELOPE, PenGeomJar o penhan, o casos del usuario)"]


def test_without_the_private_corpus_the_manifest_has_what_there_is(monkeypatch, tmp_path):
    import json

    import conftest

    (tmp_path / "resultados").mkdir()
    (tmp_path / "resultados" / "caja.geo").write_text("x\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"geometries": [
        {"path": f"{conftest.PRIVATE_CORPUS[0]}/chair"}, {"path": "resultados/caja.geo"}]}), encoding="utf-8")
    monkeypatch.setattr(conftest, "CORPUS", tmp_path)
    assert conftest.load_manifest() == [{"path": "resultados/caja.geo"}]


def test_argus_itself_passes_the_repository_check(revisar):
    assert revisar.problems(ROOT) == []


# ---------------------------------------------------------------------------------------------------------------
# packaging/iconos.py
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.gui
def test_the_icons_of_the_executables(qapp, tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    iconos = _load("argus_iconos", PACKAGING / "iconos.py")
    paths = iconos.draw_icons(tmp_path)
    with Image.open(paths["ico"]) as ico:
        assert sorted(ico.ico.sizes()) == [(s, s) for s in iconos.ICO_SIZES]
    with Image.open(paths["png"]) as png:
        assert png.size == (256, 256) and png.mode == "RGBA"
        assert png.getpixel((128, 128))[3] == 255 and png.getpixel((0, 0))[3] == 0  # dibujado, fondo transparente
    with Image.open(paths["icns"]) as icns:
        assert icns.format == "ICNS"
    assert PIL.__version__
