"""Construye el ejecutable de ARGUS de este sistema y su paquete para distribuirlo (0.17.0, ADR-022).

Con el Python del entorno de ARGUS (``uv sync --frozen --no-dev``) y PyInstaller instalado en él:

    python packaging/construir.py

1. Dibuja los iconos (``packaging/iconos.py``).
2. PyInstaller (``packaging/argus.spec``): ``dist/ARGUS`` (y ``dist/ARGUS.app`` en macOS).
3. Comprueba el ejecutable: lo abre con ``--comprobar`` (``argus.comprobar``) y exige que todo salga bien.
4. Hace el paquete, en ``dist/paquetes``:

   * Windows: un instalador de Inno Setup (``packaging/windows/argus.iss``);
   * macOS: una imagen de disco (.dmg) con ARGUS.app y un acceso a Aplicaciones;
   * Linux: un .tar.gz con la carpeta de ARGUS, ``instalar.sh`` (menú de aplicaciones) y el icono.

   Con cada paquete va ``LEEME.txt`` (cómo instalarlo y abrirlo la primera vez).

Paquetes con contraseña: si la variable de entorno ``ARGUS_CLAVE`` tiene una, el instalador de Windows la pide y
lleva los ficheros cifrados, el .dmg va cifrado (AES-256: el Finder la pide al abrirlo) y en Linux sale un .7z cifrado
(también los nombres de dentro) con el .tar.gz dentro.

Opciones: ``--sin-comprobar``; ``--solo-paquete`` (usa el ``dist`` que ya hay); ``--sin-numba`` (el paquete no lleva
Numba y la comprobación no lo exige: el de los Mac con Intel, para los que Numba ya no publica versiones);
``--iscc RUTA`` (el ISCC.exe de Inno Setup, si no está en su sitio de siempre).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PACKAGES = DIST / "paquetes"
README = ROOT / "packaging" / "LEEME.txt"
NAME = "ARGUS"
PASSWORD_ENV = "ARGUS_CLAVE"
PASSWORD_RE = re.compile(r"[A-Za-z0-9._,;:!?@#%+=*/-]{8,64}")


class BuildError(SystemExit):
    def __init__(self, message: str):
        super().__init__(f"construir.py: {message}")


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def system_label() -> str:
    """«Windows», «macOS-AppleSilicon», «macOS-Intel» o «Linux-x86_64» (lo que va en el nombre del paquete)."""
    if sys.platform == "win32":
        return "Windows"
    if sys.platform == "darwin":
        return "macOS-AppleSilicon" if platform.machine() == "arm64" else "macOS-Intel"
    return f"Linux-{platform.machine() or 'x86_64'}"


def package_password() -> str | None:
    """La contraseña de ``ARGUS_CLAVE`` (o None). Solo caracteres que no den problemas en ninguno de los tres."""
    pw = os.environ.get(PASSWORD_ENV, "")
    if not pw:
        return None
    if not PASSWORD_RE.fullmatch(pw):
        raise BuildError(f"{PASSWORD_ENV}: entre 8 y 64 caracteres, sin espacios ni tildes: letras, números y "
                         "._,;:!?@#%+=*/-")
    return pw


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    shown = [str(c) for c in cmd]
    print("$", " ".join(re.sub(r"^-p.+", "-p***", s) for s in shown), flush=True)  # sin la contraseña de 7z
    return subprocess.run(shown, check=True, **kwargs)


def app_executable() -> Path:
    if sys.platform == "win32":
        return DIST / NAME / f"{NAME}.exe"
    if sys.platform == "darwin":
        return DIST / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return DIST / NAME / NAME


def draw_icons() -> None:
    sys.path.insert(0, str(ROOT / "packaging"))
    from iconos import draw_icons as draw

    for kind, path in draw(BUILD / "iconos").items():
        print(f"icono {kind}: {path}")


def build_app() -> None:
    run([sys.executable, "-m", "PyInstaller", ROOT / "packaging" / "argus.spec", "--noconfirm", "--clean",
         "--distpath", DIST, "--workpath", BUILD / "pyinstaller"])
    if not app_executable().is_file():
        raise BuildError(f"PyInstaller no dejó {app_executable()}")


def check_app(label: str, numba: bool, environ: dict[str, str]) -> Path:
    """Abre el ejecutable con ``--comprobar``; falla si la comprobación no sale bien (o falta Numba y debe estar).

    ``environ``: el entorno con el que se lanzó la construcción (dibujar los iconos pone la plataforma «offscreen»
    de Qt, y la comprobación debe usar la de verdad: la ventana, OpenGL...)."""
    report = BUILD / f"comprobacion-{label}.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    env = {k: v for k, v in environ.items() if k != PASSWORD_ENV}
    proc = subprocess.run([str(app_executable()), "--comprobar", str(report)], env=env, timeout=1800)
    text = report.read_text(encoding="utf-8") if report.is_file() else "(el ejecutable no escribió el informe)\n"
    print(text, flush=True)
    if proc.returncode != 0:
        raise BuildError(f"la comprobación del ejecutable ha fallado (código {proc.returncode}); véase {report}")
    if numba and not re.search(r"^\[\s*OK\s*\] Numba", text, re.MULTILINE):
        raise BuildError("el ejecutable no lleva Numba (o no compila sus núcleos)")
    return report


def find_iscc(given: str | None) -> Path:
    candidates = [given, os.environ.get("ISCC"), shutil.which("iscc"), shutil.which("ISCC.exe")]
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            base_path = Path(base) / ("Programs" if var == "LOCALAPPDATA" else "")
            candidates += [str(base_path / f"Inno Setup {n}" / "ISCC.exe") for n in (7, 6)]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise BuildError("no se encuentra ISCC.exe (Inno Setup 6 o 7: https://jrsoftware.org/isinfo.php); "
                     "dígase dónde está con --iscc")


def package_windows(v: str, password: str | None, iscc: Path) -> Path:
    name = f"ARGUS-{v}-Windows-instalador"
    args = [iscc, "/Q", f"/DAppVersion={v}", f"/DSourceDir={DIST / NAME}", f"/DOutputDir={PACKAGES}",
            f"/DOutputName={name}", f"/DIconFile={BUILD / 'iconos' / 'argus.ico'}", f"/DReadme={README}"]
    key_file = BUILD / "clave.iss"
    if password:  # en un fichero aparte, no en la línea de órdenes; se borra al acabar
        key_file.write_text(f'#define Clave "{password}"\n', encoding="ascii")
        args.append(f"/DClaveFichero={key_file}")
    try:
        run([*args, ROOT / "packaging" / "windows" / "argus.iss"])
    finally:
        key_file.unlink(missing_ok=True)
    return PACKAGES / f"{name}.exe"


def package_macos(v: str, password: str | None, label: str) -> Path:
    app = DIST / f"{NAME}.app"
    # La firma «ad hoc» de PyInstaller: solo se informa (una firma rota ya habría impedido abrir ARGUS en la
    # comprobación; aquí, un aviso estricto de codesign no debe parar el paquete).
    sig = subprocess.run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
                         capture_output=True, text=True)
    print(f"codesign: {'firma correcta' if sig.returncode == 0 else 'aviso'}\n{sig.stderr.strip()}", flush=True)
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    run(["ditto", app, stage / f"{NAME}.app"])  # ditto conserva enlaces, atributos y la firma
    (stage / "Aplicaciones").symlink_to("/Applications")
    shutil.copy2(README, stage / "LEEME.txt")
    out = PACKAGES / f"ARGUS-{v}-{label}.dmg"
    out.unlink(missing_ok=True)
    cmd = ["hdiutil", "create", "-volname", f"ARGUS {v}", "-srcfolder", stage, "-format", "UDZO",
           "-imagekey", "zlib-level=9", "-ov"]
    secret = (password.encode("ascii") + b"\0") if password else None
    if password:
        cmd += ["-encryption", "AES-256", "-stdinpass"]
    for attempt in range(1, 4):  # hdiutil falla a veces con «Resource busy» en los Mac de GitHub
        try:
            run([*cmd, out], input=secret)
            break
        except subprocess.CalledProcessError:
            if attempt == 3:
                raise
            print(f"hdiutil ha fallado; otra vez ({attempt + 1} de 3)", flush=True)
            time.sleep(10 * attempt)
    mount = BUILD / "dmg-montada"
    mount.mkdir(exist_ok=True)
    run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mount,
         *(["-stdinpass"] if password else []), out], input=secret)
    try:
        if not (mount / f"{NAME}.app" / "Contents" / "MacOS" / NAME).is_file():
            raise BuildError(f"{out.name} no tiene ARGUS.app dentro")
    finally:
        run(["hdiutil", "detach", mount])
    return out


def _tar_entry(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if info.name.endswith("/instalar.sh"):
        info.mode = 0o755
    return info


def package_linux(v: str, password: str | None, label: str) -> Path:
    top = f"ARGUS-{v}-{label}"
    out = PACKAGES / f"{top}.tar.gz"
    with tarfile.open(out, "w:gz", compresslevel=9) as tar:
        tar.add(DIST / NAME, arcname=f"{top}/ARGUS", filter=_tar_entry)
        tar.add(ROOT / "packaging" / "linux" / "instalar.sh", arcname=f"{top}/instalar.sh", filter=_tar_entry)
        tar.add(README, arcname=f"{top}/LEEME.txt", filter=_tar_entry)
        tar.add(BUILD / "iconos" / "argus.png", arcname=f"{top}/argus.png", filter=_tar_entry)
    if not password:
        return out
    seven = shutil.which("7z") or shutil.which("7zz")
    if not seven:
        raise BuildError("hace falta 7-Zip (7z) para el paquete de Linux con contraseña")
    protected = PACKAGES / f"{top}.7z"
    protected.unlink(missing_ok=True)
    # -mhe=on: cifra también los nombres; -mx=0: el .tar.gz ya va comprimido
    run([seven, "a", "-t7z", "-mx=0", "-mhe=on", f"-p{password}", protected, out])
    out.unlink()
    return protected


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Construye el ejecutable de ARGUS de este sistema y su paquete.")
    ap.add_argument("--sin-comprobar", action="store_true", help="no abrir el ejecutable para comprobarlo")
    ap.add_argument("--solo-paquete", action="store_true", help="no volver a hacer el ejecutable (usa dist/)")
    ap.add_argument("--sin-numba", action="store_true", help="el paquete no lleva Numba (Mac con Intel)")
    ap.add_argument("--iscc", help="ruta de ISCC.exe (Windows)")
    args = ap.parse_args(argv)
    environ = dict(os.environ)
    v, label, password = version(), system_label(), package_password()
    print(f"ARGUS {v} para {label}" + (" (paquete con contraseña)" if password else ""), flush=True)
    if not args.solo_paquete:
        draw_icons()
        build_app()
    if not args.sin_comprobar:
        check_app(label, numba=not args.sin_numba, environ=environ)
    PACKAGES.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        out = package_windows(v, password, find_iscc(args.iscc))
    elif sys.platform == "darwin":
        out = package_macos(v, password, label)
    else:
        out = package_linux(v, password, label)
    size = out.stat().st_size / 2**20
    print(f"\nPaquete: {out} ({size:.0f} MB)\nSHA-256: {sha256(out)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
