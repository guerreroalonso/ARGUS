"""Dibuja los iconos de los ejecutables de ARGUS (el trébol, el estilo por omisión) en ``build/iconos`` (0.17.0).

* ``argus.ico``: Windows (16 a 256 px, cada tamaño dibujado a su medida y no reducido);
* ``argus.icns``: macOS (16 a 1024 px);
* ``argus.png``: Linux (256 px), el del menú de aplicaciones.

Lo llama ``construir.py``; a mano: ``python packaging/iconos.py [carpeta]``. Necesita PySide6 y Pillow (que viene con
Matplotlib); dibuja sin pantalla (plataforma «offscreen» de Qt).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def draw_icons(out: Path) -> dict[str, Path]:
    sys.path.insert(0, str(ROOT / "src"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtGui import QGuiApplication

    from argus.ui.icons import DEFAULT_STYLE, argus_pixmap

    _app = QGuiApplication.instance() or QGuiApplication(["iconos"])  # noqa: F841 - QPixmap necesita la aplicación
    out.mkdir(parents=True, exist_ok=True)
    images: dict[int, Image.Image] = {}
    for size in sorted(set(ICO_SIZES) | set(ICNS_SIZES)):
        png = out / f"argus-{size}.png"
        if not argus_pixmap(DEFAULT_STYLE, size).save(str(png), "PNG"):
            raise RuntimeError(f"No se pudo guardar {png}")
        with Image.open(png) as im:
            images[size] = im.convert("RGBA")
    paths = {"png": out / "argus.png", "ico": out / "argus.ico", "icns": out / "argus.icns"}
    images[256].save(paths["png"])
    images[256].save(paths["ico"], format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                     append_images=[images[s] for s in ICO_SIZES if s != 256])
    images[1024].save(paths["icns"], format="ICNS", append_images=[images[s] for s in ICNS_SIZES if s != 1024])
    return paths


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else ROOT / "build" / "iconos"
    for kind, path in draw_icons(out).items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
