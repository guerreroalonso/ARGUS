"""Revisa que en el repositorio de ARGUS no haya lo que no debe ir en él (0.17.0, ADR-022).

    python packaging/revisar_repo.py [carpeta]

Lo hace el flujo de GitHub antes de construir nada, y conviene hacerlo antes de subir cambios. Falla (código 1) si
encuentra:

* fuentes de Fortran de PENELOPE, penhan o PENGEOM (``pengeom.f``, ``penhmain.f``, ``penelope.f``...: cualquier
  ``.f``/``.for``/``.F``); las únicas fuentes de Fortran admitidas son las de la sonda de pruebas de ARGUS
  (``src/argus/oracle/fortran/*.f90``, escritas para ARGUS);
* PenGeomJar o sus fuentes (``.jar``, ``.class``, ``.java``);
* ficheros DICOM (``.dcm`` o con la marca «DICM»): un TAC puede llevar datos de pacientes;
* el corpus de pruebas que no va en el repositorio público (``.gitignore``): los ejemplos de PENELOPE,
  PenGeomJar y penhan (``tests/corpus/pengeomjar``, ``ornl`` y ``penhan``) y los casos del usuario
  (``tests/corpus/usuario``);
* ejecutables y compilados (``.exe``, ``.o``, ``.mod``, ``.so``, ``.dll``...), entornos (``.venv``) y las
  carpetas ``build`` y ``dist``;
* ficheros de más de 50 MB (GitHub no admite más de 100 MB);
* un flujo de GitHub (``.github/workflows/ejecutables.yml``) distinto de su copia en ARGUS
  (``packaging/github/ejecutables.yml``).

Con git (``.git`` en la carpeta) se revisan los ficheros de ``git ls-files`` (lo que se subiría); sin git, todos menos
los que ``.gitignore`` de ARGUS deja fuera (``build``, ``dist``, ``.venv``, cachés).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 50 * 2**20
FORTRAN_OK = re.compile(r"^src/argus/oracle/fortran/(argus_\w+|penelope_stub)\.f90$")
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
FORBIDDEN_DIRS = {".venv", "venv", "build", "dist"}
PRIVATE_CORPUS = ("tests/corpus/pengeomjar/", "tests/corpus/ornl/", "tests/corpus/penhan/", "tests/corpus/usuario/")
WORKFLOW = ".github/workflows/ejecutables.yml"
WORKFLOW_COPY = "packaging/github/ejecutables.yml"
BINARY_EXT = {".exe", ".o", ".obj", ".mod", ".so", ".dll", ".dylib", ".a", ".lib", ".pyc", ".pyd", ".dmg", ".7z"}


def repo_files(root: Path) -> list[str]:
    """Rutas relativas (con «/») de lo que va (o iría) en el repositorio."""
    if (root / ".git").exists():
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout
        return sorted(p for p in out.decode("utf-8").split("\0") if p)
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            if not (name.endswith((".pyc", ".pyo")) or name in (".DS_Store", "Thumbs.db")
                    or rel.startswith(PRIVATE_CORPUS)):
                files.append(rel)
    return sorted(files)


def is_dicom(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(132)
    except OSError:
        return False
    return len(head) == 132 and head[128:132] == b"DICM"


def problems(root: Path) -> list[str]:
    found = []
    for rel in repo_files(root):
        path = root / rel
        name = Path(rel).name
        ext = Path(rel).suffix
        parts = rel.split("/")
        if any(p in FORBIDDEN_DIRS for p in parts[:-1]):
            found.append(f"{rel}: carpeta que no va en el repositorio ({parts[0]})")
        elif rel.startswith(PRIVATE_CORPUS):
            found.append(f"{rel}: corpus de pruebas que no va en el repositorio público (ejemplos de PENELOPE, "
                         "PenGeomJar o penhan, o casos del usuario)")
        elif ext.lower() in (".f", ".for", ".f77") or ext == ".F":
            found.append(f"{rel}: fuente de Fortran (PENELOPE, penhan y PENGEOM no van en el repositorio)")
        elif ext.lower() in (".f90", ".f95") and not FORTRAN_OK.match(rel):
            found.append(f"{rel}: fuente de Fortran fuera de la sonda de ARGUS (src/argus/oracle/fortran)")
        elif ext.lower() in (".jar", ".class", ".java"):
            found.append(f"{rel}: PenGeomJar o fuentes de Java (no van en el repositorio)")
        elif ext.lower() == ".dcm" or is_dicom(path):
            found.append(f"{rel}: fichero DICOM (un TAC puede llevar datos de pacientes)")
        elif ext.lower() in BINARY_EXT:
            found.append(f"{rel}: ejecutable o compilado")
        elif name.lower() in ("pengeom.f", "penelope.f", "penhmain.f", "penhmat.f"):
            found.append(f"{rel}: fuente de PENELOPE")
        try:
            if path.stat().st_size > MAX_BYTES:
                found.append(f"{rel}: más de 50 MB")
        except OSError:
            pass
    workflow, copy = root / WORKFLOW, root / WORKFLOW_COPY
    if workflow.is_file() and (not copy.is_file() or workflow.read_bytes() != copy.read_bytes()):
        found.append(f"{WORKFLOW}: no es igual que {WORKFLOW_COPY} (la copia de ARGUS)")
    return found


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve() if argv else ROOT
    files = repo_files(root)
    found = problems(root)
    if found:
        print(f"Hay {len(found)} ficheros que no deben ir en el repositorio:")
        for f in found:
            print(f"  {f}")
        return 1
    print(f"Repositorio revisado: {len(files)} ficheros, ninguno de los que no deben ir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
