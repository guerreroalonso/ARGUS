"""Arranque de ARGUS dentro del ejecutable: PyInstaller necesita un guion (no un módulo con importaciones relativas
como ``argus/__main__.py``)."""

from argus.app import main

raise SystemExit(main())
