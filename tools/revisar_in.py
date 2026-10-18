"""Revisa todos los .in de una carpeta (y sus subcarpetas) con las comprobaciones de ARGUS.

Uso:
    python tools/revisar_in.py ..\\SOBP [--avisos] [--info]

Para cada .in abre su geometría (GEOMFN, con sus PARINP) y muestra lo que
penhmain rechazaría (errores, con su IRETRN) y, con --avisos, lo que acepta
pero probablemente no es lo que se quiere (p. ej. una energía repetida en el
espectro). No modifica nada.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from argus.core.diagnostics import Severity  # noqa: E402
from argus.penhan.validate import validate_case  # noqa: E402
from argus.session import open_case  # noqa: E402


def main() -> int:
    # Con la salida en un fichero («> informe.txt»), Windows escribiría en cp1252 y fallaría con «→» o «−».
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta", help="carpeta con ficheros .in")
    ap.add_argument("--avisos", action="store_true", help="mostrar también los avisos")
    ap.add_argument("--info", action="store_true", help="mostrar también las informaciones")
    args = ap.parse_args()
    level = Severity.INFO if args.info else (Severity.WARNING if args.avisos else Severity.ERROR)
    files = sorted(p for p in Path(args.carpeta).rglob("*") if p.suffix.lower() == ".in" and p.is_file())
    totals: Counter = Counter()
    for f in files:
        case = open_case(f)
        doc = case.input_doc
        geo = case.session.geo if case.session is not None and case.session.ok else None
        diags = list(doc.diagnostics) + list(validate_case(doc, geo, f.parent))
        if case.session is not None:
            diags += [d for d in case.session.diagnostics if d.severity == Severity.ERROR]
        shown = [d for d in diags if d.severity >= level]
        for d in diags:
            totals[d.severity] += 1
        if not shown:
            continue
        print(f"\n{f}")
        if case.problem:
            print(f"  ! {case.problem}")
        for d in shown:
            mark = {Severity.ERROR: "ERROR", Severity.WARNING: "aviso", Severity.INFO: "info"}[d.severity]
            line = f" (línea {d.line + 1})" if d.line is not None else ""
            code = f" [IRETRN {d.code}]" if d.code else ""
            print(f"  {mark}{code}{line}: {d.message}")
    print(f"\n{len(files)} ficheros .in: {totals[Severity.ERROR]} errores, {totals[Severity.WARNING]} avisos, "
          f"{totals[Severity.INFO]} informaciones.")
    return 1 if totals[Severity.ERROR] else 0


if __name__ == "__main__":
    raise SystemExit(main())
