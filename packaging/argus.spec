# -*- mode: python ; coding: utf-8 -*-
"""Receta de PyInstaller para el ejecutable de ARGUS (Windows, macOS y Linux; 0.17.0).

Se usa desde ``packaging/construir.py``, que antes dibuja los iconos en ``build/iconos``. A mano, con el Python del
entorno de ARGUS (``uv sync --frozen``) y PyInstaller instalado:

    python -m PyInstaller packaging/argus.spec --noconfirm --distpath dist --workpath build/pyinstaller

Sale una carpeta (no un único fichero: arranca mucho antes y los antivirus la miran con mejores ojos):
``dist/ARGUS`` con ``ARGUS.exe`` (Windows) o ``ARGUS`` (Linux), y en macOS además ``dist/ARGUS.app``.

ARGUS no incluye ni compila los programas de penhan (penhmain, penhmat, PENGEOM): los pide al usuario (ADR-013).
Tampoco van aquí la sonda de pruebas (``argus.oracle``) ni sus fuentes de Fortran.
"""

import re
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH lo define PyInstaller
SRC = ROOT / "src"
ICONS = ROOT / "build" / "iconos"
NAME = "ARGUS"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
sys.path.insert(0, str(SRC))

# Los módulos con núcleos de Numba (``cache=True``) van como código fuente (.py) y no dentro del archivo PYZ: así
# Numba sabe de qué fichero sale cada núcleo, y su caché (en la carpeta de cachés del usuario, véase
# ``argus.app.frozen_setup``) no depende de la carpeta desde la que se abre ARGUS.
NUMBA_MODULES = ["argus.pengeom.locate_fast", "argus.render.raytrace"]

hiddenimports = collect_submodules("argus", filter=lambda name: not name.startswith("argus.oracle"))
# Matplotlib carga al guardar el módulo de cada formato (PDF, SVG, EPS: los de «Guardar la gráfica» y los de su
# barra de herramientas); PyInstaller no los ve. La comprobación del ejecutable lo vigila (ADR-022).
hiddenimports += ["matplotlib.backends.backend_pdf", "matplotlib.backends.backend_svg",
                  "matplotlib.backends.backend_ps"]
excludes = [
    "argus.oracle",
    "tkinter", "_tkinter", "pytest", "IPython", "scipy", "PyQt5", "PyQt6", "PySide2",
    # Partes de Qt que ARGUS no usa (ocupan mucho):
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebView",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.QtCharts", "PySide6.QtGraphs",
    "PySide6.QtDataVisualization", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
]

a = Analysis(  # noqa: F821 - las clases de la receta las define PyInstaller
    [str(ROOT / "packaging" / "lanzador.py")],
    pathex=[str(SRC)],
    hiddenimports=hiddenimports,
    excludes=excludes,
    module_collection_mode={name: "py" for name in NUMBA_MODULES},
    noarchive=False,
    optimize=0,
)

# Lo que sobra: complementos de Qt que ARGUS no usa (el teclado virtual, ver PDF como imagen, el tema de GTK de
# Linux: ARGUS usa el estilo Fusion), las bibliotecas que solo ellos necesitaban, las traducciones de Qt a otros
# idiomas y, en Windows, el OpenGL por software (ARGUS ya traza en la CPU si no hay OpenGL 4.1).
DROP_PLUGINS = re.compile(r"(platforminputcontexts/(lib)?qtvirtualkeyboardplugin|imageformats/(lib)?qpdf|"
                          r"platformthemes/libqgtk3)[^/]*$", re.IGNORECASE)
MAYBE_ORPHANS = re.compile(r"^(lib)?(Qt6?(VirtualKeyboard\w*|Pdf\w*|Quick\w*|Qml\w*)|gtk-3|gdk-3|gdk_pixbuf-2\.0|"
                           r"atk-1\.0|atk-bridge-2\.0|atspi|cairo|cairo-gobject|epoxy|pango-1\.0|pangocairo-1\.0|"
                           r"pangoft2-1\.0|pixman-1|fribidi|thai|datrie|harfbuzz|graphite2)\b", re.IGNORECASE)


def _base(name: str) -> str:
    return re.split(r"[\\/]", name)[-1]


def _trim(binaries, datas):
    from PyInstaller.depend.bindepend import get_imports

    def dest(entry):
        return entry[0].replace("\\", "/")

    dropped = [e for e in binaries if DROP_PLUGINS.search(dest(e))
               or (sys.platform == "win32" and _base(e[0]).lower() == "opengl32sw.dll")]
    binaries = [e for e in binaries if e not in dropped]
    while True:  # una biblioteca sobra si ya nadie la importa (Qt Quick, por ejemplo, la sigue usando el vídeo)
        needed = set()
        for _dest, src, _kind in binaries:
            try:
                needed |= {_base(n).lower() for n, _path in get_imports(src)}
            except Exception:  # noqa: BLE001 - un fichero que no se puede analizar no quita nada
                pass
        orphans = [e for e in binaries if ".framework/" not in dest(e) and MAYBE_ORPHANS.search(_base(e[0]))
                   and _base(e[0]).lower() not in needed]
        if not orphans:
            break
        dropped += orphans
        binaries = [e for e in binaries if e not in orphans]
    kept_datas = [e for e in datas if not (dest(e).startswith("PySide6/") and "/translations/" in dest(e)
                                           and dest(e).endswith(".qm") and not dest(e).endswith("_es.qm"))]
    print(f"ARGUS: fuera {len(dropped)} bibliotecas y complementos de Qt que no hacen falta "
          f"({', '.join(sorted(_base(e[0]) for e in dropped))}) y {len(datas) - len(kept_datas)} traducciones")
    return binaries, kept_datas


a.binaries, a.datas = _trim(a.binaries, a.datas)
pyz = PYZ(a.pure)  # noqa: F821


def windows_version_info():
    """Datos del ejecutable que enseña Windows (Propiedades → Detalles)."""
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    nums = tuple(([int(n) for n in re.findall(r"\d+", VERSION)] + [0, 0, 0, 0])[:4])
    strings = [
        StringStruct("CompanyName", "Rafael Guerrero Alonso"),
        StringStruct("FileDescription", "ARGUS: geometrías PENGEOM y ficheros de penhan"),
        StringStruct("FileVersion", VERSION),
        StringStruct("InternalName", NAME),
        StringStruct("LegalCopyright", "Rafael Guerrero Alonso"),
        StringStruct("OriginalFilename", f"{NAME}.exe"),
        StringStruct("ProductName", NAME),
        StringStruct("ProductVersion", VERSION),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("0C0A04B0", strings)]),  # español (España), Unicode
              VarFileInfo([VarStruct("Translation", [0x0C0A, 1200])])],
    )


is_win, is_mac = sys.platform == "win32", sys.platform == "darwin"
icon = ICONS / ("argus.ico" if is_win else "argus.icns" if is_mac else "argus.png")
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # Windows: sin ventana de consola (los errores van al registro de errores de ARGUS)
    disable_windowed_traceback=False,
    upx=False,  # UPX hace que los antivirus desconfíen y estropea DLL de Qt
    strip=False,
    icon=str(icon) if icon.is_file() else None,
    version=windows_version_info() if is_win else None,
    codesign_identity=None,  # macOS: firma «ad hoc» (sin certificado de Apple)
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, strip=False, name=NAME)  # noqa: F821

if is_mac:
    folders = "ARGUS abre y guarda geometrías, ficheros .in y resultados de penhan en {}."
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{NAME}.app",
        icon=str(icon) if icon.is_file() else None,
        bundle_identifier="io.github.guerreroalonso.argus",
        version=VERSION,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleDevelopmentRegion": "es",
            "LSMinimumSystemVersion": "13.0",  # el mínimo de Qt 6.11 (PySide6)
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "Rafael Guerrero Alonso",
            # «Abrir con» ARGUS en el Finder (sin quitarle los .in ni los .geo a la aplicación que los abra ya)
            "CFBundleDocumentTypes": [
                {"CFBundleTypeName": "Geometría de PENGEOM", "CFBundleTypeExtensions": ["geo"],
                 "CFBundleTypeRole": "Editor", "LSHandlerRank": "Alternate"},
                {"CFBundleTypeName": "Fichero de entrada de penhmain", "CFBundleTypeExtensions": ["in"],
                 "CFBundleTypeRole": "Editor", "LSHandlerRank": "Alternate"},
            ],
            "NSDesktopFolderUsageDescription": folders.format("el Escritorio"),
            "NSDocumentsFolderUsageDescription": folders.format("Documentos"),
            "NSDownloadsFolderUsageDescription": folders.format("Descargas"),
            "NSRemovableVolumesUsageDescription": folders.format("discos externos"),
            "NSNetworkVolumesUsageDescription": folders.format("carpetas de red"),
        },
    )
