"""Punto de entrada de la aplicación ARGUS."""

from __future__ import annotations

import argparse
import os
import sys

REPORT_NAME = "argus_comprobacion.txt"  # el de argus.comprobar (aquí sin importarlo: la ayuda no debe cargar Qt)


def _install_translations(app) -> None:
    """Botones y diálogos estándar de Qt en español («Guardar», «Cancelar», el diálogo de abrir...)."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    tr = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if tr.load(QLocale("es_ES"), "qtbase", "_", folder):
        app.installTranslator(tr)
        app._argus_translator = tr  # que no lo recoja el recolector de basura


def frozen_setup() -> None:
    """Ajustes del ejecutable de ARGUS (PyInstaller, 0.17.0); con ARGUS ejecutado desde su código no hace nada.

    * La caché de Numba va a la carpeta de cachés del usuario (``NUMBA_CACHE_DIR``, si no se ha dado otra): la
      carpeta del programa puede no admitir escritura (instalado para todos) y en macOS escribir dentro de
      ARGUS.app estropea su firma.
    * Por lo mismo, Python no escribe ``__pycache__`` (los núcleos de Numba van en el paquete como código fuente).
    """
    if not getattr(sys, "frozen", False):
        return
    from .core.userdata import user_cache_dir

    sys.dont_write_bytecode = True
    os.environ.setdefault("NUMBA_CACHE_DIR", str(user_cache_dir() / "numba"))


def clean_argv(argv: list[str]) -> list[str]:
    """Los argumentos sin el «-psn_0_12345» que el Finder de macOS añadía (versiones antiguas) al abrir la app."""
    return [a for a in argv if not a.startswith("-psn_")]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="argus",
        description="ARGUS: visor y editor de geometrías PENGEOM (.geo) y ficheros de entrada de penhmain (.in)")
    parser.add_argument("fichero", nargs="?", help="fichero .in o geometría .geo que abrir")
    parser.add_argument("--cpu3d", action="store_true",
                        help="vista 3D sin la tarjeta gráfica (trazado de rayos en la CPU, más lento)")
    parser.add_argument("--comprobar", nargs="?", const="", default=None, metavar="INFORME",
                        help="comprueba la instalación (abre ARGUS sin preguntar nada, prueba Numba, la lectura de "
                             f"TACs, las gráficas...) y escribe un informe (por omisión, {REPORT_NAME} en la "
                             "carpeta actual)")
    return parser


def main(argv: list[str] | None = None) -> int:
    frozen_setup()  # antes de importar Numba
    args = build_parser().parse_args(clean_argv(argv if argv is not None else sys.argv[1:]))
    if args.cpu3d:
        os.environ["ARGUS_3D"] = "cpu"
    if sys.platform == "win32":  # que la barra de tareas muestre el icono de ARGUS y no el de Python
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ARGUS.ARGUS")
        except (AttributeError, OSError):  # pragma: no cover - solo Windows
            pass

    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from .ui.file_open import ArgusApplication
    from .ui.gpu_tracer import surface_format
    from .ui.main_window import MainWindow

    # OpenGL 4.1 core para la vista 3D: hay que pedirlo antes de crear la aplicación (macOS).
    QSurfaceFormat.setDefaultFormat(surface_format())
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or ArgusApplication(sys.argv[:1])
    app.setApplicationName("ARGUS")
    app.setOrganizationName("ARGUS")
    app.setStyle("Fusion")
    _install_translations(app)
    from .ui.errors import install_excepthook

    install_excepthook()  # los errores inesperados se anotan y se avisan (en Windows la consola no se ve)
    from .ui.icons import argus_icon

    app.setWindowIcon(argus_icon())
    if args.comprobar is not None:
        from .comprobar import run_checks

        return run_checks(app, args.comprobar or None)
    # Compilar LOCATE y el trazador de rayos (Numba) en segundo plano mientras se abre la ventana.
    import threading

    from .pengeom import locate_fast
    from .render import raytrace

    def warmup() -> None:
        locate_fast.warmup()
        raytrace.warmup()

    threading.Thread(target=warmup, name="argus-warmup", daemon=True).start()
    win = MainWindow()
    win.show()
    requests = getattr(app, "file_open_requests", None)  # macOS: lo que se abre desde el Finder
    if requests is not None:
        requests.set_opener(win.open_file)
    if args.fichero:
        win.open_file(args.fichero)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
