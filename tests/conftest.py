"""Configuración común de las pruebas de ARGUS."""

from __future__ import annotations

import gc
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CORPUS = ROOT / "tests" / "corpus"

# Los ajustes (colores, recientes, elección de .in...) de las pruebas van a un .ini temporal: las pruebas
# los borran y no deben tocar los del usuario (en Windows, en el registro).
if not os.environ.get("ARGUS_SETTINGS"):
    import tempfile

    os.environ["ARGUS_SETTINGS"] = str(Path(tempfile.mkdtemp(prefix="argus-tests-")) / "argus.ini")
# Y su carpeta de datos (los materiales personalizados, 0.16.0): una temporal, no la del usuario.
if not os.environ.get("ARGUS_DATA_DIR"):
    import tempfile

    os.environ["ARGUS_DATA_DIR"] = tempfile.mkdtemp(prefix="argus-tests-datos-")

# Interfaz sin pantalla, salvo que se pida probar la GPU (ARGUS_TEST_GPU=1 con una pantalla de verdad). La pantalla
# de mentira, de 1920 × 1080 (la de «offscreen» es de 800 × 800 y las ventanas se ajustan a la pantalla).
if not os.environ.get("ARGUS_TEST_GPU"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    import tempfile

    _screen = Path(tempfile.mkdtemp(prefix="argus-tests-")) / "pantalla.json"
    _screen.write_text(json.dumps({"screens": [{"name": "ARGUS", "x": 0, "y": 0, "width": 1920, "height": 1080,
                                                "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}))
    if ":" not in str(_screen):  # en Windows («C:\…»), los «:» separarían los argumentos de la plataforma
        os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_screen}"
try:  # el formato OpenGL de la vista 3D se fija antes de crear la aplicación, como en app.py
    from PySide6.QtGui import QSurfaceFormat

    from argus.ui.gpu_tracer import surface_format

    QSurfaceFormat.setDefaultFormat(surface_format())
except ImportError:  # pragma: no cover
    pass


# Lo que no va en el repositorio público de ARGUS (ADR-022, .gitignore): los ejemplos de PENELOPE, PenGeomJar y
# penhan (no se pueden redistribuir) y los casos del usuario. Sin ellos, las pruebas que los usan se omiten.
PRIVATE_CORPUS = ("pengeomjar", "ornl", "penhan", "usuario")
MISSING_CORPUS = tuple(name for name in PRIVATE_CORPUS if not (CORPUS / name).is_dir())
_CORPUS_REFS = {  # cómo los nombran las pruebas: CORPUS / "penhan" / "stack", CORPUS / "usuario/100Mev15-174"...
    "pengeomjar": r"pengeomjar",
    "ornl": r"\bornl\b",
    "penhan": r"CORPUS\s*/\s*[\"']penhan\b|[\"']penhan/|corpus[/\\]penhan",
    "usuario": r"CORPUS\s*/\s*[\"']usuario\b|[\"']usuario/|corpus[/\\]usuario",
}


def load_manifest() -> list[dict]:
    """Las geometrías del corpus que están (sin el corpus privado, solo las de los resultados)."""
    entries = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))["geometries"]
    return [g for g in entries if (CORPUS / g["path"]).exists()]


def modules_needing(missing: tuple[str, ...], folder: Path) -> set[str]:
    """Los módulos de pruebas de ``folder`` que nombran algo de ``missing`` o importan de uno que lo nombra
    (``from test_gui_modules import window``: su ventana abre un caso del usuario)."""
    import re

    if not missing:
        return set()
    refs = re.compile("|".join(_CORPUS_REFS[name] for name in missing))
    imports = re.compile(r"^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))", re.MULTILINE)
    texts = {p.stem: p.read_text(encoding="utf-8") for p in folder.glob("*.py") if p.stem != "conftest"}
    needing = {name for name, text in texts.items() if refs.search(text)}
    while True:
        more = {name for name, text in texts.items() if name not in needing
                and any((a or b) in needing for a, b in imports.findall(text))}
        if not more:
            return needing
        needing |= more


def pytest_collection_modifyitems(config, items):
    """Sin el corpus privado (un clon del repositorio público), se omiten las pruebas que lo necesitan."""
    needing = modules_needing(MISSING_CORPUS, Path(__file__).parent)
    if not needing:
        return
    skip = pytest.mark.skip(reason="faltan ficheros de tests/corpus que no van en el repositorio público ("
                            + ", ".join(MISSING_CORPUS) + ")")
    for item in items:
        if Path(str(item.fspath)).stem in needing:
            item.add_marker(skip)


MANIFEST = load_manifest()
MANIFEST_IDS = [g["path"] for g in MANIFEST]


@pytest.fixture(scope="session")
def probe_available() -> bool:
    from argus.oracle.probe import find_probe

    return find_probe() is not None


@pytest.fixture
def need_probe(probe_available):
    if not probe_available:
        pytest.skip("Sonda PENGEOM no compilada (python tools/build_probe.py)")


@pytest.fixture
def need_sim_probe():
    from argus.oracle.probe import find_sim_probe

    if find_sim_probe() is None:
        pytest.skip("Sonda de PMRDR no compilada (python tools/build_probe.py --penhmain ...)")


@pytest.fixture(autouse=True)
def _qt_leftovers_in_the_gui_thread(request):
    """Lo que deja una prueba de interfaz se destruye al acabarla, en el hilo de la interfaz.

    pytest-qt cierra los widgets de la prueba con ``deleteLater``, que solo se cumple al volver al bucle de eventos.
    Un diálogo sin padre (de Python) en un ciclo de referencias lo recogería después el recolector de Python en
    cualquier hilo: p. ej. en el que lee un TAC en la prueba siguiente, y el destructor de Qt fuera del hilo de la
    interfaz es un fallo de segmentación al azar. En ARGUS los diálogos y menús tienen padre (la ventana)."""
    yield
    if "qtbot" not in request.fixturenames:
        return
    from PySide6.QtCore import QCoreApplication, QEvent

    if QCoreApplication.instance() is None:
        return
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
