"""Compila las sondas de verificación de ARGUS (solo para las pruebas).

Uso:
    python tools/build_probe.py [ruta/a/pengeom.f] [--penhmain ruta/a/penhmain.f] [--gfortran gfortran]

* Sonda PENGEOM: enlaza pengeom.f (GEOMIN, LOCATE, STEP).
* Sonda del lector de .in: enlaza el módulo PENHMAIN_input y las subrutinas
  PMRDR0 y FWORD extraídas sin cambios de penhmain.f.
* Sonda de las comprobaciones de penhmain (PMRDR): penhmain.f entero sin el
  programa principal, con PEINIT/PHINIT sustituidas (no lee materiales). Necesita
  junto a penhmain.f las bibliotecas que incluye (penelope.f, penhan.f...).

Por defecto se usan los ficheros de ../ProgramasBase/penhan/programs/ (la
copia de PENELOPE del usuario, que no se modifica ni se copia al
repositorio). Los ejecutables quedan en build/probe/. ARGUS no necesita las
sondas para funcionar.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from argus.oracle.probe import ProbeError, build_probe, build_reader_probe, build_sim_probe  # noqa: E402

PROGRAMS = ROOT.parent / "ProgramasBase" / "penhan" / "programs"
DEFAULT_SOURCE = PROGRAMS / "pengeom.f"
DEFAULT_PENHMAIN = PROGRAMS / "penhmain.f"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE), help="pengeom.f")
    ap.add_argument("--penhmain", default=str(DEFAULT_PENHMAIN), help="penhmain.f (lector de .in)")
    ap.add_argument("--gfortran", default="gfortran")
    ap.add_argument("--build-dir", default=None)
    args = ap.parse_args()
    status = 0
    try:
        exe = build_probe(args.source, args.build_dir, gfortran=args.gfortran)
        print(f"Sonda PENGEOM compilada: {exe}")
    except ProbeError as exc:
        print(f"ERROR (sonda PENGEOM): {exc}", file=sys.stderr)
        status = 1
    if Path(args.penhmain).is_file():
        try:
            exe = build_reader_probe(args.penhmain, args.build_dir, gfortran=args.gfortran)
            print(f"Sonda del lector de .in compilada: {exe}")
        except ProbeError as exc:
            print(f"ERROR (sonda del lector de .in): {exc}", file=sys.stderr)
            status = 1
        try:
            exe = build_sim_probe(args.penhmain, args.build_dir, gfortran=args.gfortran)
            print(f"Sonda de las comprobaciones de penhmain compilada: {exe}")
        except ProbeError as exc:
            print(f"Aviso (sonda de PMRDR, opcional): {exc}")
    else:
        print(f"Aviso: no se encuentra {args.penhmain}; no se compila la sonda del lector de .in.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
