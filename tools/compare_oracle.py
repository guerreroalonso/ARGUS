"""Compara las tablas, LOCATE y STEP de ARGUS con la sonda PENGEOM.

Uso:
    python tools/compare_oracle.py            # todo el corpus
    python tools/compare_oracle.py mlc chair  # solo los que contengan esos nombres
    python tools/compare_oracle.py --points 20000

Requiere haber compilado la sonda (tools/build_probe.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from argus.oracle.compare import compare_tables  # noqa: E402
from argus.oracle.probe import run_probe  # noqa: E402
from argus.pengeom.compiler import compile_geometry  # noqa: E402
from argus.pengeom.parser import load_geometry  # noqa: E402

CORPUS = ROOT / "tests" / "corpus"


def sample_points(geo, n: int, rng: np.random.Generator) -> np.ndarray:
    """Puntos aleatorios en la caja que contiene la geometría (estimada)."""
    from argus.pengeom.bounds import estimate_bounds

    lo, hi = estimate_bounds(geo)
    pts = rng.uniform(lo, hi, size=(n, 3))
    d = rng.normal(size=(n, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    return np.hstack([pts, d])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--points", type=int, default=0, help="puntos aleatorios para LOCATE")
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args()
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    rng = np.random.default_rng(args.seed)
    n_bad = 0
    for item in manifest["geometries"]:
        path = CORPUS / item["path"]
        if args.names and not any(n in item["path"] for n in args.names):
            continue
        parinp = item.get("parinp")
        t0 = time.perf_counter()
        doc = load_geometry(path)
        geo = compile_geometry(doc, parinp=parinp)
        t1 = time.perf_counter()
        if not geo.ok:
            print(f"[ERROR ARGUS] {item['path']}: {[str(d) for d in geo.diagnostics]}")
            n_bad += 1
            continue
        pts = None
        if args.points:
            pts = sample_points(geo, args.points, rng)
        res = run_probe(path, parinp=parinp, points=pts)
        if not res.status.ok:
            print(f"[ERROR PENGEOM] {item['path']}: {res.status}")
            n_bad += 1
            continue
        cmp = compare_tables(geo, res.tables)
        line = (f"{'OK ' if cmp.ok else 'DIF'} {item['path']:45s} NS={geo.nsurf:5d} NB={geo.nbodys:5d} "
                f"compilar={1000 * (t1 - t0):7.1f} ms")
        if pts is not None:
            from argus.pengeom.locate import Locator

            loc = Locator(geo)
            t2 = time.perf_counter()
            ib, mat = loc.locate(pts[:, :3], pts[:, 3:])
            t3 = time.perf_counter()
            nd = int(np.count_nonzero(ib != res.locate_body))
            nm = int(np.count_nonzero(mat != res.locate_mat))
            line += f" LOCATE: {nd} dif. cuerpo, {nm} dif. material ({1000 * (t3 - t2):.0f} ms)"
            if nd or nm:
                cmp.ok = False
                bad = np.nonzero(ib != res.locate_body)[0][:5]
                for k in bad:
                    cmp.differences.append(f"punto {pts[k].tolist()}: ARGUS={ib[k]} PENGEOM={res.locate_body[k]}")
        print(line)
        if not cmp.ok:
            n_bad += 1
            for d in cmp.differences[:12]:
                print("     ", d)
    print("Diferencias en", n_bad, "geometrías" if n_bad != 1 else "geometría")
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
