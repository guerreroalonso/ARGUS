"""Los programas de penhan del usuario: la carpeta ``database``, penhmat y penhmain (ADR-013).

Cada usuario compila penhmat y penhmain en su ordenador, optimizados para él, y los deja en su carpeta de penhan:
penhmat en ``database`` (lo pide ``database/readme.txt``: lee ``./pdfiles``, ``./pdhan`` y ``./ENDF_data``) y
penhmain donde quiera. ARGUS no los incluye ni los compila: el usuario le dice dónde están (Opciones → «Programas
de penhan…») o que no hace falta.

Un ejecutable puede ser de Windows (PE, «.exe»), de Linux (ELF) o de macOS (Mach-O), y de distintos procesadores.
ARGUS lo reconoce por su contenido y no por la extensión (el penhmat de Linux suele llamarse «penhmat.x»), y lo
ejecuta así en este ordenador:

* si es de su sistema, directamente;
* en Windows, uno de Linux con WSL (``wsl.exe``), si está instalado con una distribución, que tiene que tener las
  bibliotecas con las que se compiló (p. ej. libgfortran);
* si no, no se ejecuta, y se dice por qué y qué hacer.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

HOST = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
SYSTEM_NAMES = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # sin ventana de consola en Windows

_PE_MACHINES = {0x8664: "x86-64", 0x014C: "x86", 0xAA64: "arm64", 0x01C4: "arm"}
_ELF_MACHINES = {0x3E: "x86-64", 0x03: "x86", 0xB7: "arm64", 0x28: "arm", 0xF3: "riscv", 0x15: "ppc64"}
_MACHO_CPUS = {0x01000007: "x86-64", 0x0100000C: "arm64", 7: "x86", 12: "arm"}
# Lo que no es el programa aunque empiece por su nombre (fuentes, datos, scripts de gnuplot...).
_NOT_PROGRAMS = {".f", ".f90", ".for", ".in", ".txt", ".dat", ".gnu", ".mat", ".geo", ".bat", ".o", ".obj", ".mod",
                 ".pdf", ".py", ".sh", ".dll", ".so", ".dylib", ".bak", ".zip", ".gz", ".tgz", ".tar", ".7z", ".dmp",
                 ".rep", ".trk", ".log"}


def host_arch() -> str:
    """Procesador de este ordenador con los nombres de :class:`ExeInfo` («x86-64», «arm64»...)."""
    m = platform.machine().lower()
    return {"amd64": "x86-64", "x86_64": "x86-64", "x64": "x86-64", "arm64": "arm64", "aarch64": "arm64",
            "i386": "x86", "i686": "x86", "x86": "x86"}.get(m, m)


# ---------------------------------------------------------------------------------------------------------------------
# Qué es un ejecutable
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExeInfo:
    path: Path
    system: str | None  # 'windows', 'linux', 'macos', 'script' (#!) o None (no es un ejecutable que se reconozca)
    arch: str = ""  # 'x86-64', 'arm64', 'x86'... (vacío si no se sabe); «universal (...)» en macOS
    problem: str = ""  # por qué no se reconoce (no existe, es una carpeta, no se puede leer...)

    @property
    def description(self) -> str:
        if self.system is None:
            return self.problem or "no es un ejecutable"
        if self.system == "script":
            return "guion de órdenes (#!)"
        return f"ejecutable de {SYSTEM_NAMES[self.system]}" + (f" ({self.arch})" if self.arch else "")


def exe_info(path: str | os.PathLike) -> ExeInfo:
    """Sistema y procesador de un ejecutable, por su contenido."""
    p = Path(path)
    if not p.exists():
        return ExeInfo(p, None, problem="no existe")
    if p.is_dir():
        return ExeInfo(p, None, problem="es una carpeta")
    try:
        with open(p, "rb") as f:
            head = f.read(4096)
            if head[:2] == b"MZ" and len(head) >= 0x40:
                off = struct.unpack_from("<I", head, 0x3C)[0]
                if off + 6 > len(head):
                    f.seek(off)
                    head = head[:0x40] + b"\0" * max(0, off - 0x40) + f.read(6)
    except OSError as exc:
        return ExeInfo(p, None, problem=f"no se puede leer ({exc.strerror or exc})")
    return _classify(p, head)


def _classify(p: Path, head: bytes) -> ExeInfo:
    if head[:2] == b"MZ" and len(head) >= 0x40:
        off = struct.unpack_from("<I", head, 0x3C)[0]
        if head[off:off + 4] == b"PE\0\0" and len(head) >= off + 6:
            machine = struct.unpack_from("<H", head, off + 4)[0]
            return ExeInfo(p, "windows", _PE_MACHINES.get(machine, f"máquina 0x{machine:04x}"))
        return ExeInfo(p, "windows")  # un MZ sin cabecera PE (MS-DOS): no se sabe el procesador
    if head[:4] == b"\x7fELF" and len(head) >= 20:
        endian = "<" if head[5] == 1 else ">"
        machine = struct.unpack_from(endian + "H", head, 18)[0]
        return ExeInfo(p, "linux", _ELF_MACHINES.get(machine, f"máquina {machine}"))
    magic = head[:4]
    if magic in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe") and len(head) >= 8:
        endian = ">" if magic[0] == 0xFE else "<"
        cpu = struct.unpack_from(endian + "I", head, 4)[0]
        return ExeInfo(p, "macos", _MACHO_CPUS.get(cpu, f"cpu 0x{cpu:x}"))
    if magic == b"\xca\xfe\xba\xbe" and len(head) >= 8:
        n = struct.unpack_from(">I", head, 4)[0]
        if 0 < n <= 16:  # binario universal (un número mayor sería un .class de Java)
            cpus = []
            for i in range(n):
                at = 8 + 20 * i
                if at + 4 <= len(head):
                    cpus.append(_MACHO_CPUS.get(struct.unpack_from(">I", head, at)[0], "?"))
            return ExeInfo(p, "macos", "universal (" + ", ".join(cpus) + ")")
    if head[:2] == b"#!":
        return ExeInfo(p, "script")
    return ExeInfo(p, None, problem="no es un ejecutable de Windows, Linux ni macOS")


def _runs_on(arch: str, host: str, h_arch: str) -> bool:
    """¿Puede un ejecutable de ``arch`` funcionar en un ordenador ``host`` con procesador ``h_arch``?"""
    if arch.startswith("universal"):
        return host == "macos"  # binario universal de macOS (x86-64 y/o arm64; Rosetta ejecuta el primero)
    if not arch or not h_arch or arch == h_arch:
        return True
    if arch == "x86" and h_arch == "x86-64":
        return True  # 32 bits en 64 (en Linux hacen falta sus bibliotecas de 32 bits, pero suele estar)
    if arch == "x86-64" and h_arch == "arm64" and host in ("macos", "windows"):
        return True  # Rosetta 2 / emulación de Windows en ARM
    return False


# ---------------------------------------------------------------------------------------------------------------------
# Cómo se ejecuta aquí
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WslStatus:
    ok: bool
    note: str  # la distribución («Ubuntu 22.04.4 LTS») o por qué no se puede usar
    interop: bool = True  # WSL puede ejecutar programas de Windows (un penhmain.exe en el terminal de Linux)


_WSL_CACHE: dict[str, WslStatus] = {}
_WSL_LOCK = threading.Lock()


def wsl_executable() -> Path | None:
    """``wsl.exe`` de Windows (``Sysnative`` desde un Python de 32 bits), o None si no está."""
    if os.name != "nt":
        return None
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    for sub in ("Sysnative", "System32"):
        p = Path(root) / sub / "wsl.exe"
        if p.is_file():
            return p
    w = shutil.which("wsl")
    return Path(w) if w else None


def decode_output(data: bytes) -> str:
    """Salida de un programa: la de Linux es UTF-8; los mensajes del propio ``wsl.exe``, UTF-16."""
    if not data:
        return ""
    if data.count(b"\0") > len(data) // 4:
        try:
            return data.decode("utf-16-le", errors="replace").replace("\0", "")
        except UnicodeDecodeError:  # pragma: no cover - errors='replace'
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def wsl_status(refresh: bool = False, timeout: float = 90.0) -> WslStatus:
    """¿Se puede usar WSL en este Windows? (se pregunta una vez por sesión; tarda unos segundos si WSL no está
    ya en marcha)."""
    with _WSL_LOCK:
        if not refresh and "status" in _WSL_CACHE:
            return _WSL_CACHE["status"]
        exe = wsl_executable()
        if exe is None:
            st = WslStatus(False, "WSL (el Linux de Windows) no está instalado")
        else:
            # Sin comillas (wsl.exe las pasaría mal). WSLInterop(-late): WSL ejecuta programas de Windows.
            script = ("echo ARGUS-WSL; . /etc/os-release 2>/dev/null; echo ${PRETTY_NAME:-Linux}; "
                      "if [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] || "
                      "[ -e /proc/sys/fs/binfmt_misc/WSLInterop-late ]; then echo ARGUS-INTEROP; fi")
            try:
                r = subprocess.run([str(exe), "-e", "sh", "-c", script], capture_output=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
                out = decode_output(r.stdout) + decode_output(r.stderr)
            except subprocess.TimeoutExpired:
                out, r = "WSL no ha contestado a tiempo", None
            except OSError as exc:
                out, r = f"no se ha podido ejecutar wsl.exe ({exc.strerror or exc})", None
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            if r is not None and r.returncode == 0 and "ARGUS-WSL" in lines:
                i = lines.index("ARGUS-WSL")
                name = lines[i + 1] if i + 1 < len(lines) and lines[i + 1] != "ARGUS-INTEROP" else "Linux"
                st = WslStatus(True, name, "ARGUS-INTEROP" in lines)
            else:
                why = " ".join(lines)[:300] or "sin distribución de Linux instalada"
                st = WslStatus(False, f"WSL no funciona: {why}")
        _WSL_CACHE["status"] = st
        return st


@dataclass(frozen=True)
class Launch:
    how: str  # 'native', 'wsl' o '' (no se puede ejecutar en este ordenador)
    note: str  # cómo se ejecutará, o por qué no se puede y qué hacer
    pending: bool = False  # aún no se sabe: se le está preguntando a WSL en otro hilo (``WSL_CHECKING``)

    @property
    def ok(self) -> bool:
        return bool(self.how)


# Mientras se le pregunta a WSL en otro hilo (la primera vez tarda lo que tarde en arrancar): la interfaz no espera.
WSL_CHECKING = Launch("", "comprobando si funciona WSL (el Linux de Windows)", pending=True)


def needs_wsl(info: ExeInfo | None, host: str = HOST) -> bool:
    """¿Hace falta WSL para ejecutar ``info`` en este ordenador? (en Windows, uno de Linux o un guion con «#!»)."""
    return host == "windows" and info is not None and info.system in ("linux", "script")


def launch_for(info: ExeInfo, *, host: str = HOST, arch: str | None = None,
               wsl: Callable[[], WslStatus] | None = None) -> Launch:
    """Cómo se ejecutaría ``info`` en este ordenador (``host``, ``arch``: para las pruebas)."""
    h_arch = host_arch() if arch is None else arch
    if info.system is None:
        return Launch("", info.problem or "no es un ejecutable")
    if info.system == host or (info.system == "script" and host != "windows"):
        if info.system != "script" and not _runs_on(info.arch, host, h_arch):
            return Launch("", f"es un {info.description} y este ordenador tiene un procesador {h_arch}: "
                              "compílelo en este ordenador")
        if host != "windows" and not os.access(info.path, os.X_OK):
            return Launch("", f"no tiene permiso de ejecución: en un terminal, chmod +x \"{info.path}\"")
        return Launch("native", f"{info.description}: se ejecuta directamente")
    if host == "windows" and info.system in ("linux", "script"):
        if info.system == "linux" and not _runs_on(info.arch, "linux", h_arch):
            return Launch("", f"es un {info.description} y este ordenador tiene un procesador {h_arch}")
        st = (wsl or wsl_status)()
        if st.ok:
            return Launch("wsl", f"{info.description}: se ejecuta con WSL ({st.note})")
        return Launch("", f"es un {info.description}, que en Windows se ejecuta con WSL: {st.note}. Instale WSL "
                          "(«wsl --install» en PowerShell) con la distribución donde lo compiló, o compile el "
                          "programa para Windows (gfortran de MSYS2)")
    return Launch("", f"es un {info.description} y este ordenador es {SYSTEM_NAMES.get(host, host)}: compílelo en "
                      "este ordenador (gfortran)")


def wsl_path(path: str | os.PathLike, timeout: float = 60.0) -> str:
    """Ruta de Windows como la ve WSL (``wslpath -a``: da igual dónde monte las unidades); si WSL no contesta, la
    de por defecto (``/mnt/c/...``)."""
    exe = wsl_executable()
    if exe is not None:
        try:
            r = subprocess.run([str(exe), "-e", "wslpath", "-a", str(path)], capture_output=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            out = decode_output(r.stdout).strip()
            if r.returncode == 0 and out.startswith("/"):
                return out.splitlines()[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return wsl_style(path)


def command(exe: str | os.PathLike, cwd: str | os.PathLike, launch: Launch,
            to_wsl: Callable[[str | os.PathLike], str] | None = None) -> list[str]:
    """La orden para ejecutar ``exe`` en la carpeta ``cwd`` (``to_wsl``: traducción de rutas, para las pruebas)."""
    if launch.how == "native":
        return [str(exe)]
    if launch.how == "wsl":
        # wsl.exe --cd admite la carpeta de Windows tal cual; el programa, si está en ella, por su nombre.
        exe, cwd = Path(exe), Path(cwd)
        inside = os.path.normcase(os.path.abspath(exe.parent)) == os.path.normcase(os.path.abspath(cwd))
        prog = "./" + exe.name if inside else (to_wsl or wsl_path)(exe)
        return [str(wsl_executable() or "wsl.exe"), "--cd", str(cwd), "-e", prog]
    raise ValueError(launch.note or "no se puede ejecutar")


@dataclass
class RunResult:
    returncode: int | None
    output: str
    timed_out: bool = False
    cancelled: bool = False
    error: str = ""  # no se ha podido lanzar

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not (self.timed_out or self.cancelled or self.error)


def run_program(exe: str | os.PathLike, cwd: str | os.PathLike, stdin: str, launch: Launch, *,
                timeout: float = 900.0, cancel: Callable[[], bool] | None = None,
                argv: list[str] | None = None) -> RunResult:
    """Ejecuta ``exe`` en ``cwd`` con ``stdin`` como entrada (texto ASCII, una respuesta por línea).

    ``cancel()`` se consulta cada décima de segundo; si devuelve True (o se pasa de ``timeout`` segundos) se
    termina el programa. ``argv``: la orden (por defecto, :func:`command`).
    """
    argv = argv or command(exe, cwd, launch)
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    except OSError as exc:
        return RunResult(None, "", error=f"no se ha podido ejecutar ({exc.strerror or exc})")
    chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()), name="argus-run", daemon=True)
    reader.start()
    try:
        proc.stdin.write(stdin.encode("ascii", errors="replace"))
        proc.stdin.close()
    except OSError:  # el programa ha terminado antes de leerlo todo
        pass
    t0 = time.monotonic()
    timed_out = cancelled = False
    while True:
        try:
            rc = proc.wait(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel():
                cancelled = True
            elif time.monotonic() - t0 > timeout:
                timed_out = True
            if cancelled or timed_out:
                proc.kill()
                rc = proc.wait()
                break
    reader.join(timeout=10)
    try:
        proc.stdout.close()  # la tubería de la salida (si no, queda abierta hasta que la recoge el recolector)
    except OSError:
        pass
    return RunResult(rc, decode_output(b"".join(chunks)), timed_out, cancelled)


def last_lines(text: str, n: int = 8) -> str:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------------------------------------------------------------
# La carpeta de penhan del usuario
# ---------------------------------------------------------------------------------------------------------------------

DATABASE_PARTS = ("pdfiles", "pdhan", "ENDF_data")


@dataclass
class PenhanSetup:
    """Dónde están los programas de penhan del usuario (Opciones → «Programas de penhan…»)."""

    database: Path | None = None  # carpeta database (pdfiles, pdhan, ENDF_data, material-list.txt)
    penhmat: Path | None = None
    penhmain: Path | None = None
    penhmat_off: bool = False  # «No hace falta»: los .mat los genera el usuario
    endf: Path | None = None  # carpeta de los ficheros ENDF que se copian (0.16.0; si no, database/ENDF_data)

    def endf_folder(self) -> Path | None:
        """De dónde se copian los ficheros ENDF a la carpeta donde se simula: la carpeta elegida o, si no se ha
        elegido, la de la base de datos (``database/ENDF_data``)."""
        if self.endf is not None:
            return self.endf if self.endf.is_dir() else None
        if self.database is not None and (self.database / "ENDF_data").is_dir():
            return self.database / "ENDF_data"
        return None

    @property
    def configured(self) -> bool:
        """¿Ha dicho el usuario algo sobre penhmat (dónde está o que no hace falta)?"""
        return self.penhmat_off or self.penhmat is not None

    def penhmat_problem(self) -> str:
        """Por qué no se puede generar con penhmat ('' si se puede intentar; si se ejecuta aquí lo dice
        :func:`launch_for`)."""
        if self.penhmat_off:
            return "ha elegido generar los .mat usted mismo (Opciones → Programas de penhan…)"
        if self.penhmat is None:
            return "no se ha dicho dónde está penhmat (Opciones → Programas de penhan…)"
        if not self.penhmat.is_file():
            return f"no se encuentra penhmat en {self.penhmat}"
        probs = database_problems(self.database)
        return probs[0] if probs else ""


def database_folder(path: str | os.PathLike | None) -> Path | None:
    """La carpeta ``database`` de penhan a partir de la de penhan, de la propia ``database`` o de una de sus
    subcarpetas (``pdfiles``...)."""
    if not path:
        return None
    p = Path(path)
    for cand in (p, p / "database", p.parent):
        if (cand / "pdfiles").is_dir() and ((cand / "pdhan").is_dir() or (cand / "ENDF_data").is_dir()):
            return cand
    return None


def database_problems(db: str | os.PathLike | None) -> list[str]:
    if not db:
        return ["no se ha elegido la carpeta de penhan"]
    d = Path(db)
    missing = [s for s in DATABASE_PARTS if not (d / s).is_dir()]
    if missing:
        return [f"a la carpeta {d} le falta {', '.join(missing)}: no es la carpeta database de penhan"]
    return []


def find_program(folders: list, stem: str, exclude: tuple[str, ...] = ()) -> list[Path]:
    """Ficheros «<stem>…» de estas carpetas que son programas (por su contenido), primero los que se pueden
    ejecutar en este ordenador y, entre ellos, los más recientes."""
    found: dict[Path, tuple] = {}
    for d in folders:
        if not d:
            continue
        try:
            entries = list(Path(d).iterdir())
        except OSError:
            continue
        for f in entries:
            low = f.name.lower()
            if not low.startswith(stem) or any(low.startswith(x) for x in exclude):
                continue
            if f.suffix.lower() in _NOT_PROGRAMS or re.search(r"\.bak\d*$", low) or not f.is_file():
                continue  # tampoco las copias de seguridad («penhmain.x.bak2» de ARGUS 0.13.0)
            info = exe_info(f)
            if info.system is None or info.system == "script":
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                mtime = 0.0
            found[f] = (0 if launch_for(info, wsl=lambda: WslStatus(True, "")).ok else 1, -mtime)
    return sorted(found, key=lambda f: found[f])


def find_penhmat(root: str | os.PathLike | None) -> Path | None:
    """penhmat en la carpeta de penhan: en ``database`` (donde tiene que estar) o en ``programs``."""
    db = database_folder(root)
    base = db.parent if db is not None else (Path(root) if root else None)
    folders = [db, base / "programs" if base else None, base]
    hits = find_program(folders, "penhmat")
    return hits[0] if hits else None


def find_penhmain(root: str | os.PathLike | None) -> Path | None:
    """penhmain en la carpeta de penhan (``programs``, la propia carpeta o ``database``); no penhmain-sum."""
    db = database_folder(root)
    base = db.parent if db is not None else (Path(root) if root else None)
    folders = [base / "programs" if base else None, base, db]
    hits = find_program(folders, "penhmain", exclude=("penhmain-sum", "penhmain_sum", "penhmain-layout"))
    return hits[0] if hits else None


# ---------------------------------------------------------------------------------------------------------------------
# Cómo se lanza penhmain en la carpeta de simulación
# ---------------------------------------------------------------------------------------------------------------------

def wsl_style(path: str | os.PathLike) -> str:
    """Ruta de Windows como la ve WSL por defecto (``C:\\x\\y`` → ``/mnt/c/x/y``)."""
    w = PureWindowsPath(str(path))
    if w.drive and len(w.drive) == 2 and w.drive[1] == ":":
        return "/mnt/" + w.drive[0].lower() + "/" + "/".join(w.parts[1:])
    return str(path).replace("\\", "/")


def run_hint(info: ExeInfo | None, in_name: str, folder: str | os.PathLike | None = None, host: str = HOST) -> str:
    """Cómo se lanza penhmain con ``in_name`` en su carpeta (texto para el usuario)."""
    name = info.path.name if info is not None else "penhmain"

    def q(text: str) -> str:  # entre comillas si lleva espacios o paréntesis («penhmain (1).exe»)
        return f'"{text}"' if re.search(r"[\s()&^%!]", text) else text

    if info is None or info.system in (None, "windows"):
        exe = Path(name).stem if name.lower().endswith(".exe") and not re.search(r"[\s()]", name) else name
        line = f"{q(exe)} < {q(in_name)}"
        if host != "windows":
            return f"«{line}» (con el penhmain de Windows)"
        if '"' in line:  # con comillas, PowerShell las pasaría mal a cmd: solo en cmd
            return f"en cmd (no en PowerShell): «{line}»"
        return f"en cmd: «{line}»; en PowerShell: «cmd /c \"{line}\"»"
    run = f"./{q(name)} < {q(in_name)}"
    if host == "windows" and info.system in ("linux", "script"):
        where = f"cd \"{wsl_style(folder)}\" && " if folder else ""
        return f"en un terminal de WSL (Linux): «{where}{run}»"
    return f"en un terminal: «{run}»"


__all__ = ["DATABASE_PARTS", "ExeInfo", "HOST", "Launch", "PenhanSetup", "RunResult", "SYSTEM_NAMES", "WSL_CHECKING",
           "WslStatus", "command", "database_folder", "database_problems", "decode_output", "exe_info",
           "find_penhmain", "find_penhmat", "find_program", "host_arch", "last_lines", "launch_for", "needs_wsl",
           "run_hint", "run_program", "wsl_executable", "wsl_path", "wsl_status", "wsl_style"]
