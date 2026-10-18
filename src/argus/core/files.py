"""Copias de seguridad al guardar, copias que nunca sobrescriben y escrituras enteras o nada."""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import shutil
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1 << 20
TMP_SUFFIX = ".argus-tmp"


class CopyCancelled(Exception):
    """Se ha cancelado una copia (la copia a medias ya se ha borrado)."""


def backup_file(target: str | os.PathLike) -> Path | None:
    """Copia ``target`` a ``target.bak`` antes de escribirlo (si existe).

    No copia los permisos: un .in de solo lectura (copiado de un CD, de un .zip o
    de la carpeta de un compañero) no debe dejar un .bak de solo lectura que
    impida guardar las veces siguientes. Un .bak anterior de solo lectura se
    sobrescribe igualmente.
    """
    target = Path(target)
    if not target.is_file():
        return None
    bak = target.with_name(target.name + ".bak")
    if bak.exists():
        try:
            os.chmod(bak, stat.S_IREAD | stat.S_IWRITE)
        except OSError:
            pass
    shutil.copyfile(target, bak)
    try:
        st = target.stat()
        os.utime(bak, (st.st_atime, st.st_mtime))  # la fecha de la versión anterior
    except OSError:
        pass
    return bak


def new_temp(target: Path):
    """Fichero temporal nuevo (nunca uno que ya exista) junto a ``target``: (ruta, fichero abierto)."""
    for _ in range(100):
        tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}{TMP_SUFFIX}")
        try:
            return tmp, open(tmp, "xb")  # lo cierra quien lo pide
        except FileExistsError:
            continue
    raise FileExistsError(f"No se puede crear un fichero temporal junto a {target}")


def publish_new(tmp: str | os.PathLike, target: str | os.PathLike) -> bool:
    """Da a ``tmp`` el nombre ``target`` sin sobrescribir nunca: False si ``target`` ya existe.

    En Windows, ``os.rename`` falla si el destino existe. En el resto, un enlace
    duro (``os.link``) también es atómico y falla si ya existe (``tmp`` queda y
    hay que borrarlo); sin enlaces duros (FAT, algunas unidades de red) se
    comprueba justo antes de renombrar.
    """
    if os.name == "nt":
        try:
            os.rename(tmp, target)
        except FileExistsError:
            return False
        return True
    try:
        os.link(tmp, target)
    except FileExistsError:
        return False
    except OSError:
        if os.path.lexists(target):
            return False
        os.rename(tmp, target)
    return True


def copy_new(src: str | os.PathLike, target: str | os.PathLike, *, executable: bool = False,
             cancel: Callable[[], bool] | None = None) -> bool:
    """Copia ``src`` como ``target`` si no existe; nunca sobrescribe. False si ``target`` ya existía.

    Se copia a un temporal que se borra siempre al terminar (también si falla o
    se cancela): en la carpeta solo aparece el fichero completo. ``cancel()`` se
    consulta cada MiB; si devuelve True, se lanza :class:`CopyCancelled`.
    Conserva la fecha del original; ``executable`` copia además su modo.
    """
    target = Path(target)
    if os.path.lexists(target):
        return False
    tmp, out = new_temp(target)
    try:
        with out, open(src, "rb") as inp:
            while True:
                if cancel is not None and cancel():
                    raise CopyCancelled
                chunk = inp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        st = os.stat(src)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        if executable:  # que se pueda ejecutar (el modo del original, pero escribible)
            os.chmod(tmp, stat.S_IMODE(st.st_mode) | stat.S_IWUSR | stat.S_IRUSR)
        return publish_new(tmp, target)
    finally:
        try:
            os.unlink(tmp)  # tras un enlace duro, o si no se ha publicado
        except OSError:
            pass


def write_new(target: str | os.PathLike, data: bytes, *, executable: bool = False) -> bool:
    """Escribe ``target`` si no existe; nunca sobrescribe. False si ya existía.

    Como :func:`copy_new`: primero a un temporal, que se publica con el nombre final sin reemplazar nada.
    """
    target = Path(target)
    if os.path.lexists(target):
        return False
    tmp, out = new_temp(target)
    try:
        with out:
            out.write(data)
        if executable:
            os.chmod(tmp, 0o755)
        return publish_new(tmp, target)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def same_content(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    """¿Mismo contenido? Se compara por bloques; si no se puede leer alguno, se da por distinto."""
    try:
        if not os.path.isfile(b) or os.path.getsize(a) != os.path.getsize(b):
            return False
        if os.path.samefile(a, b):
            return True
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(CHUNK), fb.read(CHUNK)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


# Caracteres que un editor o un procesador de textos mete sin querer y que no caben en Latin-1 (la
# codificación de los ficheros de PENGEOM y penhmain): se escriben con su equivalente.
_LATIN1_FIX = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2026": "...", "\u2022": "*", "\u2264": "<=", "\u2265": ">=", "\u2192": "->", "\u03bc": "\u00b5",
    "\u2009": " ", "\u200a": " ", "\u202f": " ", "\u2002": " ", "\u2003": " ", "\u200b": "", "\ufeff": "",
})


def latin1_text(text: str) -> tuple[str, str]:
    """(texto para escribir en Latin-1, caracteres que aun así no caben y se escribirán como «?»)."""
    fixed = text.translate(_LATIN1_FIX)
    return fixed, "".join(sorted({c for c in fixed if ord(c) > 255}))


@dataclass(frozen=True)
class FileStamp:
    """Cómo estaba un fichero en el disco al leerlo o al escribirlo: fecha (ns), tamaño y SHA-1 del contenido."""

    mtime_ns: int
    size: int
    digest: str


def _digest_file(path: str | os.PathLike) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def file_stamp(path: str | os.PathLike, data: bytes | None = None) -> FileStamp | None:
    """Huella de ``path`` (None si no se puede leer); ``data``: su contenido, si se acaba de leer o de escribir."""
    try:
        st = os.stat(path)
        digest = hashlib.sha1(data).hexdigest() if data is not None else _digest_file(path)
    except OSError:
        return None
    return FileStamp(st.st_mtime_ns, st.st_size, digest)


def file_changed(path: str | os.PathLike, stamp: FileStamp | None) -> bool:
    """¿Otro programa ha cambiado ``path`` desde ``stamp``?

    Con la misma fecha y el mismo tamaño no se lee; si no, se compara el contenido (guardar sin cambios en otro
    editor solo cambia la fecha). Un fichero que ya no está no cuenta: al escribirlo no se pierde nada.
    """
    try:
        st = os.stat(path)
    except OSError:
        return False
    if stamp is None:
        return True
    if st.st_mtime_ns == stamp.mtime_ns and st.st_size == stamp.size:
        return False
    if st.st_size != stamp.size:
        return True
    try:
        return _digest_file(path) != stamp.digest
    except OSError:
        return True


class SaveError(OSError):
    """Ha fallado uno de los ficheros que se guardaban juntos; ``strerror`` dice cómo ha quedado cada uno."""


class FileChangedError(OSError):
    """Otro programa ha cambiado el fichero desde que se leyó o se guardó: no se ha escrito nada."""

    def __init__(self, path: str | os.PathLike):
        super().__init__(errno.ESTALE, "Otro programa lo ha cambiado desde que se leyó o se guardó", str(path))


def real_target(path: str | os.PathLike) -> Path:
    """El fichero que se escribe de verdad: el destino de un enlace simbólico (el enlace se conserva)."""
    path = Path(path)
    return Path(os.path.realpath(path)) if os.path.islink(path) else path


def check_writable(path: str | os.PathLike) -> None:
    """Lanza OSError con el motivo si ``path`` no se va a poder escribir (antes de tocar nada)."""
    real = real_target(path)
    if real.is_dir():
        raise IsADirectoryError(errno.EISDIR, "Es una carpeta", str(path))
    if not real.parent.is_dir():
        raise FileNotFoundError(errno.ENOENT, "No existe la carpeta", str(real.parent))
    if real.exists() and not os.access(real, os.W_OK):
        raise PermissionError(errno.EACCES, "El fichero es de solo lectura", str(path))
    if not os.access(real.parent, os.W_OK):
        raise PermissionError(errno.EACCES, "No se puede escribir en la carpeta", str(real.parent))


@dataclass
class _Job:
    path: Path  # como lo ve el usuario (puede ser un enlace simbólico)
    real: Path  # el fichero que se escribe
    data: bytes
    in_place: bool = False  # con varios enlaces duros se escribe en su sitio (todos sus nombres ven el cambio)
    old: bytes | None = None  # el contenido anterior, para volver atrás (None: no existía)
    tmp: Path | None = None
    touched: bool = False  # se ha empezado a escribir en su sitio (puede haber quedado a medias)
    done: bool = False
    restored: bool = False  # se escribió y ha vuelto a su versión anterior
    bak: Path | None = None  # la copia .bak que se ha hecho al guardar...
    bak_old: bytes | None = None  # ... y lo que tenía antes (None: no había), para dejarla como estaba
    bak_times: tuple[float, float] | None = None


def _write_new(path: Path, data: bytes, mode_from: Path | None) -> Path:
    """``data`` en un temporal nuevo junto a ``path`` (con el modo de ``mode_from``); devuelve su ruta."""
    tmp, out = new_temp(path)
    try:
        with out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if mode_from is not None:
            try:
                shutil.copymode(mode_from, tmp)
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return tmp


def _write_in_place(path: Path, data: bytes, job: _Job | None = None) -> None:
    with open(path, "r+b") as f:
        if job is not None:
            job.touched = True
        f.write(data)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())


def _restore(job: _Job) -> None:
    """Deja el fichero de ``job`` como estaba antes de guardar."""
    if job.old is None:
        os.unlink(job.real)
    elif job.in_place:
        _write_in_place(job.real, job.old)
    else:
        tmp = _write_new(job.real, job.old, job.real)
        try:
            os.replace(tmp, job.real)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    job.restored = True


def _make_backup(job: _Job) -> None:
    """La copia .bak de ``job``, recordando la que había (si el guardado falla, vuelve a estar)."""
    bak = job.path.with_name(job.path.name + ".bak")
    if bak.is_file():
        try:
            job.bak_old = bak.read_bytes()
            st = bak.stat()
            job.bak_times = (st.st_atime, st.st_mtime)
        except OSError:
            job.bak_old = None
    job.bak = backup_file(job.path)


def _restore_backups(jobs: list[_Job]) -> None:
    """Tras un guardado que no se ha hecho, las copias .bak vuelven a ser las de antes (la versión anterior a la
    última que se guardó). No se toca la de un fichero que no ha podido volver atrás: ahí está su versión anterior.
    """
    for job in jobs:
        if job.bak is None or job.bak_old is None or ((job.done or job.touched) and not job.restored):
            continue
        try:
            tmp = _write_new(job.bak, job.bak_old, job.bak)
            try:
                os.replace(tmp, job.bak)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if job.bak_times is not None:
                os.utime(job.bak, job.bak_times)
        except OSError:
            pass


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def _undo_saved(jobs: list[_Job], failed: _Job, exc: OSError, backup: bool) -> OSError:
    """Vuelve atrás lo que ya se había escrito y devuelve el error, que dice cómo ha quedado cada fichero."""
    written = [j for j in jobs if j.done]
    if failed.touched:
        written.append(failed)  # puede haber quedado a medias
    if not written:
        return exc  # no ha cambiado nada: el error tal cual
    back, stuck = [], []
    for j in reversed(written):
        try:
            _restore(j)
            back.append(j.path.name)
        except OSError as e:
            stuck.append((j.path.name, e.strerror or str(e)))
    reason = exc.strerror or str(exc)
    parts = [f"No se ha podido guardar {failed.path.name} ({reason})."]
    others = [n for n in reversed(back) if n != failed.path.name]
    if others:
        s = "n" if len(others) > 1 else ""
        parts.append(f"{_names(others)} ya se había{s} guardado y ha{s} vuelto a su versión anterior.")
    for name, why in stuck:
        where = f"la anterior está en {name}.bak" if backup else "la anterior se ha perdido"
        parts.append(f"{name} no ha podido volver a su versión anterior ({why}): {where}.")
    if not stuck:
        parts.append("No ha cambiado ningún fichero.")
    return SaveError(exc.errno or errno.EIO, " ".join(parts), str(failed.path))


def write_files(items: Iterable[tuple[str | os.PathLike, bytes]], *, backup: bool = True) -> None:
    """Escribe varios ficheros juntos (``items``: destino y contenido), todos o ninguno.

    Antes de tocar nada se comprueba que se pueden escribir todos (solo lectura, carpetas) y se escribe cada uno
    en un temporal junto a su destino. Después se hacen las copias ``.bak`` (con ``backup``) y cada temporal toma
    el nombre de su destino. Si falla uno (en Windows, p. ej., un .in abierto por «penhmain < caso.in»), los que
    ya se habían escrito vuelven a su versión anterior y se lanza :class:`SaveError`, que dice qué ha pasado.
    Un fallo con un solo fichero lanza el error tal cual (el fichero no ha cambiado).

    Un enlace simbólico se conserva (se escribe el fichero al que apunta) y un fichero con varios enlaces duros
    se escribe en su sitio, tras la copia ``.bak``, para que lo vean todos sus nombres.
    """
    jobs = []
    for path, data in items:
        path = Path(path)
        check_writable(path)
        real = real_target(path)
        try:
            links = os.stat(real).st_nlink
        except FileNotFoundError:
            links = 0
        jobs.append(_Job(path, real, bytes(data), in_place=links > 1))
    try:
        for job in jobs:
            if job.real.exists():
                job.old = job.real.read_bytes()
            if not job.in_place:
                job.tmp = _write_new(job.real, job.data, job.real if job.old is not None else None)
        if backup:
            try:
                for job in jobs:
                    _make_backup(job)
            except OSError:
                _restore_backups(jobs)
                raise
        for job in jobs:
            try:
                if job.in_place:
                    _write_in_place(job.real, job.data, job)
                else:
                    os.replace(job.tmp, job.real)
            except OSError as exc:
                err = _undo_saved(jobs, job, exc, backup)
                _restore_backups(jobs)
                if err is exc:
                    raise
                raise err from exc
            job.done = True
    finally:
        for job in jobs:
            if job.tmp is not None:
                try:
                    os.unlink(job.tmp)  # si ya ha tomado el nombre del destino, no existe
                except OSError:
                    pass


def write_bytes_atomic(target: str | os.PathLike, data: bytes) -> None:
    """Escribe ``target`` entero o nada: a un temporal que luego toma el nombre (un fallo a medias, p. ej.
    con el disco lleno, no deja el fichero cortado). Un fichero de solo lectura no se sustituye; un enlace
    simbólico o duro se conserva (véase :func:`write_files`)."""
    write_files([(target, data)], backup=False)


def write_latin1(target: str | os.PathLike, text: str) -> str:
    """Escribe un fichero de texto de PENGEOM/penhmain (Latin-1, sin cortarlo si falla).

    Devuelve los caracteres que no caben en Latin-1 y se han escrito como «?»
    (vacío si no hay ninguno), para avisar.
    """
    fixed, bad = latin1_text(text)
    write_bytes_atomic(target, fixed.encode("latin-1", errors="replace"))
    return bad
