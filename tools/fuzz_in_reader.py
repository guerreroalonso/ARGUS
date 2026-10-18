"""Compara el lector de .in de ARGUS con el PMRDR0 real de penhmain sobre
variantes con errores típicos de los ficheros del corpus.

Uso: python tools/fuzz_in_reader.py [--n 40] [--seed 1]
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from argus.oracle.compare_in import check_text, mutations  # noqa: E402
from argus.oracle.probe import run_reader_probe  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    files = sorted((ROOT / "tests" / "corpus").rglob("*.in"))
    total = bad = 0
    for f in files:
        text = f.read_bytes().decode("latin-1")
        for kind, variant in [("original", text)] + mutations(text, rng, args.n):
            total += 1
            diffs = check_text(variant, lambda t: run_reader_probe(text=t))
            if diffs:
                bad += 1
                print(f"[{f.name} / {kind}]")
                for d in diffs[:4]:
                    print("    ", d)
    print(f"{total - bad}/{total} variantes coinciden con penhmain")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
