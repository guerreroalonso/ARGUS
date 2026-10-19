"""Ficheros de estructura CT (CTBOX) de PENGEOM 2024: lectura, escritura y vóxeles.

Formato (GEOMIN lo lee con lecturas dirigidas por lista)::

    título (una línea, A72)
    XM YM ZM          dimensiones de la caja (cm)
    NX NY NZ          número de vóxeles
    MC DC             material y densidad (g/cm³) de cada vóxel, una lectura
                      por vóxel, en el orden IZ, IY, IX (IX varía más rápido):
                      IA = IX + NX·((IY−1) + NY·(IZ−1))

La caja ocupa [0,XM]×[0,YM]×[0,ZM] en su propio sistema; la transformación
del CTBOX (ω, θ, φ, desplazamiento) la coloca: r_lab = R(ω,θ,φ)·r_CT + D
(subrutina SHPRD). DC es la densidad de masa del vóxel (el ejemplo de penhan
pone 0,99821 en el agua); penhmain escala con ella los recorridos. MC = 0 es
un vóxel vacío.

Un CT de un paciente tiene millones de vóxeles: la lectura rápida usa NumPy
y los CT leídos se guardan en una caché (cada edición de la geometría la
vuelve a compilar y no se debe releer el fichero cada vez).
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core.fortran_io import RECORD_LENGTH, text_records
from ..penhan.listdirected import repeat_type_error, to_int, to_real, tokenize

_BLANKS = re.compile(r"[ \t\r]+")  # para gfortran, en una lectura dirigida por lista el CR es un blanco
_SPECIAL = re.compile(r"[*,/'\"]")

MAX_CT_MATERIAL = 99  # formato I2 del fichero de PENGEOM
NDD = 512  # penhmain (CTDOSE0): NZ no puede pasar de 512 (IRETRN 3148)


class CtFormatError(ValueError):
    pass


@dataclass
class CtData:
    """Contenido de un fichero .ct (las matrices no se deben modificar: se comparten)."""

    title: str
    size: tuple[float, float, float]  # XM, YM, ZM
    shape: tuple[int, int, int]  # NX, NY, NZ
    material: np.ndarray  # (NZ, NY, NX) int32
    density: np.ndarray  # (NZ, NY, NX) float64

    @property
    def voxel_size(self) -> tuple[float, float, float]:
        return (self.size[0] / self.shape[0], self.size[1] / self.shape[1], self.size[2] / self.shape[2])

    @property
    def max_material(self) -> int:
        mats = self.materials()
        return mats[-1] if mats else 0

    @property
    def nvoxels(self) -> int:
        return int(self.shape[0] * self.shape[1] * self.shape[2])

    def materials(self) -> list[int]:
        """Materiales presentes (sin el 0), ordenados (se calcula una vez: las matrices no cambian)."""
        cached = self.__dict__.get("_argus_materials")
        if cached is None:
            present = np.flatnonzero(np.bincount(np.clip(self.material.ravel(), 0, None).astype(np.int64)))
            cached = [int(m) for m in present if m > 0]
            self.__dict__["_argus_materials"] = cached
        return list(cached)

    @property
    def has_void(self) -> bool:
        """¿Hay vóxeles vacíos (material 0)?"""
        cached = self.__dict__.get("_argus_void")
        if cached is None:
            cached = bool(np.any(self.material == 0))
            self.__dict__["_argus_void"] = cached
        return cached

    def material_u8(self) -> np.ndarray:
        """Material de cada vóxel como uint8 contiguo (para los trazadores de rayos); se calcula una vez."""
        cached = self.__dict__.get("_argus_u8")
        if cached is None:
            cached = np.ascontiguousarray(np.clip(self.material, 0, 127).astype(np.uint8))
            cached.flags.writeable = False
            self.__dict__["_argus_u8"] = cached
        return cached

    def freeze(self) -> CtData:
        self.material.flags.writeable = False
        self.density.flags.writeable = False
        return self


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def _take(text: str, values: list[str], count: int, first: bool, kinds: tuple[str, ...] | None = None) -> bool:
    """Añade a ``values`` los valores de un registro para un ``READ(*)`` de ``count`` valores (True si ya están).

    Como gfortran: factores de repetición ``r*c`` (entre variables del mismo tipo: ``kinds``, 'int' o 'real'), la
    barra termina la lectura (lo que falta quedaría sin definir en PENGEOM: aquí es un error) y un valor nulo
    también es un error. ``first``: primer registro de la lectura (una coma al principio es un valor nulo; en los
    siguientes, el fin del registro anterior ya separa).
    """
    if _SPECIAL.search(text) is None:  # lo habitual: números separados por blancos
        toks = [x for x in _BLANKS.split(text) if x]
        values.extend(toks[:count - len(values)])
        return len(values) == count
    toks, stop = tokenize(text.replace("\r", " "), lead_null=first)
    for tok in toks:
        if tok.error:
            raise CtFormatError(tok.error)
        if tok.text is None:
            raise CtFormatError("valor nulo («,,» o «r*»): PENGEOM dejaría el valor sin definir")
        if tok.quoted:
            raise CtFormatError(f"'{tok.text}' está entre apóstrofos y aquí va un número")
        take = min(tok.count, count - len(values))
        if kinds is not None:
            k0 = len(values)
            bad = next((j for j in range(k0 + 1, k0 + take) if kinds[j] != kinds[k0]), None)
            if bad is not None:
                raise CtFormatError(repeat_type_error(tok.count, kinds[k0], kinds[bad], bad + 1))
        values.extend([tok.text] * take)
        if len(values) == count:
            return True
    if stop < len(text):
        raise CtFormatError("«/» antes de leer todos los valores (PENGEOM dejaría los que faltan sin leer, con un "
                            "valor indefinido).")
    return False


def _list_directed(lines: list[str], start: int, count: int, kinds: tuple[str, ...] | None = None
                   ) -> tuple[list[str], int]:
    """Lee ``count`` valores como un READ(*) de Fortran a partir de la línea
    ``start``; devuelve los valores y la línea siguiente (el resto del último
    registro se descarta, como en Fortran).

    Como gfortran: «!» no es un comentario (un «!» donde se espera un número es
    un error de lectura; después de los valores da igual, se descarta) y «/»
    termina la lectura dejando sin leer lo que falte, que PENGEOM usaría con un
    valor indefinido: aquí es un error.
    """
    values: list[str] = []
    i = start
    while True:
        if i >= len(lines):
            raise CtFormatError("Fin de fichero inesperado en el fichero CT.")
        try:
            done = _take(lines[i], values, count, i == start, kinds)
        except CtFormatError as exc:
            raise CtFormatError(f"Línea {i + 1}: {exc}") from None
        i += 1
        if done:
            return values, i


def _read_items(raw: bytes, pos: int, count: int) -> tuple[list[str], int]:
    """Un ``READ(IRJ,*)`` de ``count`` valores desde el byte ``pos`` (principio de un registro).

    Devuelve los valores y dónde empieza el registro siguiente. Lee línea a línea: la cabecera puede estar detrás
    de un título de cualquier longitud.
    """
    values: list[str] = []
    first = True
    n = len(raw)
    while True:
        if pos >= n:
            raise CtFormatError("Fin de fichero inesperado en el fichero CT.")
        k = raw.find(b"\n", pos)
        end = n if k < 0 else k + 1
        text = raw[pos:end].decode("latin-1").rstrip("\n")
        pos = end
        if _take(text, values, count, first):
            return values, pos
        first = False


def _title_end(raw: bytes) -> int:
    """Fin del registro del título (``READ(IRJ,'(A72)')``): el «\\n», o un CR suelto en las 72 primeras columnas."""
    k = raw.find(b"\n")
    end = len(raw) if k < 0 else k + 1
    cr = raw.find(b"\r", 0, min(RECORD_LENGTH, end))
    return cr + 1 if cr >= 0 and cr + 1 != k else end


def _header(raw: bytes) -> tuple[str, tuple[float, float, float], tuple[int, int, int], int]:
    """Título, tamaño de la caja, número de vóxeles y byte donde empiezan los vóxeles."""
    if not raw:
        raise CtFormatError("Fichero CT vacío.")
    pos = _title_end(raw)
    title = raw[:pos].decode("latin-1").rstrip("\n").removesuffix("\r")[:RECORD_LENGTH].rstrip()
    try:
        vals, pos = _read_items(raw, pos, 3)
        xm, ym, zm = (to_real(v) for v in vals)
        vals, pos = _read_items(raw, pos, 3)
        nx, ny, nz = (to_int(v) for v in vals)
    except ValueError as exc:
        raise CtFormatError(f"Cabecera del fichero CT no válida: {exc}") from None
    if nx <= 0 or ny <= 0 or nz <= 0:
        raise CtFormatError("Número de vóxeles no válido en el fichero CT.")
    if not (xm > 0 and ym > 0 and zm > 0) or not all(math.isfinite(v) for v in (xm, ym, zm)):
        raise CtFormatError("Las dimensiones de la caja CT tienen que ser positivas y finitas.")
    return title, (xm, ym, zm), (nx, ny, nz), pos


def _slow_body(lines: list[str], start: int, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Una lectura dirigida por lista por vóxel (como GEOMIN), para ficheros con otra disposición."""
    mat = np.empty(n, dtype=np.int32)
    den = np.empty(n, dtype=np.float64)
    j = start
    k = 0
    try:
        for k in range(n):
            vals, j = _list_directed(lines, j, 2, ("int", "real"))  # READ(IRJ,*) MC(IA),DC(IA)
            if not _INT.fullmatch(vals[0]):  # gfortran: «Bad integer» con 1.0 o 1e0
                raise ValueError(f"el material '{vals[0]}' no es un número entero")
            mat[k] = to_int(vals[0])  # INTEGER*4 (gfortran: «Integer overflow»)
            den[k] = to_real(vals[1])
    except ValueError as exc:  # también CtFormatError («/» antes de tiempo, fin de fichero)
        raise CtFormatError(f"Vóxel {k + 1}: {exc}") from None
    return mat, den


_INT = re.compile(r"[-+]?\d+")
_NOT_INT_FIRST = re.compile(rb"(?m)^[ \t]*[-+]?\d*[.eEdD]")


def _integer_materials(body: bytes) -> bool:
    """¿El primer número de cada línea está escrito como entero? (gfortran no lee «1.0» en un INTEGER).

    Con líneas de ancho fijo (como las escribe ARGUS o PENGEOM) se comprueban
    las columnas del material de todas a la vez; si no, con una expresión
    regular (más lenta).
    """
    b = np.frombuffer(body, dtype=np.uint8)
    nl = np.flatnonzero(b == 10)
    if nl.size:
        widths = np.diff(nl, prepend=-1)
        m = re.match(rb"[ \t]*[-+]?\d+[ \t]", body[: int(nl[0]) + 1])
        if m is not None and np.all(widths == widths[0]):
            width, k = int(widths[0]), m.end() - 1
            rows = b[: nl.size * width].reshape(-1, width)
            allowed = np.frombuffer(b" \t+-0123456789", dtype=np.uint8)
            if np.isin(rows[:, :k], allowed).all() and np.isin(rows[:, k], (9, 32)).all():
                return _NOT_INT_FIRST.search(body[nl.size * width:]) is None  # la última línea sin \n
    return _NOT_INT_FIRST.search(body) is None


def _sample_ok(raw: bytes, count: int = 2000) -> bool:
    """Las primeras y las últimas líneas con datos tienen dos números cada una."""
    head = raw[:count * 40].split(b"\n")[:count]
    tail = raw[-count * 40:].split(b"\n")[-count:]
    for ln in head[:-1] + tail[1:]:
        parts = ln.split()
        if parts and len(parts) != 2:
            return False
    return True


# Una coma que hace un valor nulo («,,» o al principio de una línea): la lectura lenta lo dice.
_NULL_COMMA = re.compile(rb"(?m),[ \t\r]*,|^[ \t\r]*,")


def _numbers(data: bytes) -> np.ndarray | None:
    """Los números de ``data`` (separados por blancos) con NumPy; None si hay algo que no es un número."""
    data = data.replace(b"D", b"E").replace(b"d", b"e")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # «string or file could not be read to its end»: hay algo que no es un número
        try:
            return np.fromstring(data, dtype=np.float64, sep=" ")  # noqa: NPY201 - lectura de texto
        except (ValueError, DeprecationWarning):
            return None


def _voxel_records_end(raw: bytes, n: int, chunk: int = 1 << 24) -> int | None:
    """Byte donde acaba el registro del vóxel ``n`` si cada línea hasta él tiene 0 o 2 valores (un vóxel por línea);
    None si no es así. Lo que haya detrás no lo lee PENGEOM (una línea de más al final, por ejemplo). Por trozos, para
    no gastar memoria con un TAC grande (undécima revisión)."""
    b = np.frombuffer(raw, dtype=np.uint8)
    found = 0
    pos = 0
    total = b.size
    while pos < total:
        stop = min(total, pos + chunk)
        if stop < total:  # el trozo acaba en un fin de línea
            k = raw.rfind(b"\n", pos, stop)
            stop = k + 1 if k >= pos else raw.find(b"\n", stop) + 1 or total
        seg = b[pos:stop]
        blank = (seg == 32) | (seg == 9) | (seg == 13) | (seg == 10)
        starts = np.flatnonzero(~blank & np.concatenate(([True], blank[:-1])))
        nl = np.flatnonzero(seg == 10)
        counts = np.bincount(np.searchsorted(nl, starts), minlength=nl.size + 1)
        full = counts == 2
        if not np.all(full | (counts == 0)):
            bad = int(np.flatnonzero(~(full | (counts == 0)))[0])
            if found + int(full[:bad].sum()) < n:  # una línea con otro número de valores antes del último vóxel
                return None
        cum = found + np.cumsum(full)
        if cum.size and cum[-1] >= n:
            line = int(np.searchsorted(cum, n))  # línea (del trozo) del vóxel n
            return pos + (int(nl[line]) + 1 if line < nl.size else seg.size)
        found = int(cum[-1]) if cum.size else found
        pos = stop
    return None


def _fast_body(raw: bytes, n: int) -> tuple[np.ndarray, np.ndarray] | None:
    """Dos números por línea (lo habitual): lectura con NumPy. None si el fichero no es así.

    Las líneas en blanco dan igual (la lectura dirigida por lista las salta); las comas entre los dos números
    también (sin valores nulos), y lo que hay detrás del último vóxel no se lee. Si una línea tuviera otro número de
    valores, Fortran descartaría o esperaría valores: entonces se usa la lectura lenta, idéntica a GEOMIN.
    """
    # Repeticiones de Fortran («2*0.25»), «/» o «!»: la lectura lenta (con NumPy anterior a 2.3, fromstring no se queja
    # de «r*c» en la última línea y lo leería mal). \v y \f: NumPy y bytes.split los toman por blancos y gfortran no
    # (Bad integer/real): la lectura lenta.
    if b"/" in raw or b"!" in raw or b"*" in raw or b"\x0b" in raw or b"\x0c" in raw or b"'" in raw or b'"' in raw:
        return None
    if b"," in raw:
        if _NULL_COMMA.search(raw):
            return None
        raw = raw.replace(b",", b" ")  # «1,1.0000E+00» (undécima revisión: era la lectura lenta, minutos con un TAC)
    arr = _numbers(raw) if _sample_ok(raw) else None
    if arr is None or arr.size != 2 * n:
        end = _voxel_records_end(raw, n)
        if end is None:
            return None
        raw = raw[:end]
        arr = _numbers(raw)
        if arr is None or arr.size != 2 * n:
            return None
    arr = arr.reshape(n, 2)
    m = arr[:, 0]
    if not np.all(np.isfinite(arr)) or np.any(np.abs(m) > np.iinfo(np.int32).max):
        return None  # (fuera de INTEGER*4: la lectura lenta da el error de gfortran)
    mat = m.astype(np.int32)
    if not np.array_equal(mat, m) or not _integer_materials(raw):
        return None  # la lectura lenta dice en qué vóxel está el material mal escrito
    return mat, np.ascontiguousarray(arr[:, 1])


def read_ct_bytes(raw: bytes) -> CtData:
    """Lee el contenido de un fichero .ct."""
    title, size, shape, pos = _header(raw)
    nx, ny, nz = shape
    n = nx * ny * nz
    # Cada vóxel es una lectura (READ(IRJ,*) MC,DC): al menos un registro de dos caracteres («/» y su fin de línea).
    # Con menos, la cabecera no es la de este fichero; mejor decirlo que reservar memoria para lo que no hay
    # (décima revisión: con «8 6 120000000» en 8 kB, MemoryError de 21,5 GiB).
    if len(raw) - pos < 2 * n - 1:
        raise CtFormatError(f"Fin de fichero inesperado en el fichero CT (la cabecera dice {nx} × {ny} × {nz} vóxeles "
                            "y el fichero no los tiene).")
    fast = _fast_body(raw[pos:], n)
    if fast is None:
        lines = text_records(raw[pos:].decode("latin-1"))
        mat, den = _slow_body(lines, 0, n)
    else:
        mat, den = fast
    return CtData(title=title, size=size, shape=shape, material=mat.reshape(nz, ny, nx),
                  density=den.reshape(nz, ny, nx))


def read_ct_text(text: str) -> CtData:
    return read_ct_bytes(text.encode("latin-1", errors="replace"))


def read_ct_file(path: str | os.PathLike) -> CtData:
    return read_ct_bytes(Path(path).read_bytes())


# -- caché -------------------------------------------------------------------------

_CACHE: OrderedDict[tuple, CtData] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 3
_CACHE_BYTES = 1 << 30  # las anteriores a la última se guardan mientras entre todas no pasen de 1 GiB


def _cache_bytes() -> int:
    return sum(int(c.material.nbytes) + int(c.density.nbytes) for c in _CACHE.values())


def load_ct(path: str | os.PathLike) -> CtData:
    """Lee un .ct (o lo toma de la caché si no ha cambiado en el disco). No modificar lo devuelto."""
    p = Path(path)
    st = p.stat()
    key = (os.path.realpath(p), st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        ct = _CACHE.get(key)
        if ct is not None:
            _CACHE.move_to_end(key)
            return ct
    ct = read_ct_file(p).freeze()
    ct.__dict__["_argus_family"] = _sidecar_families(p)
    with _CACHE_LOCK:
        _CACHE[key] = ct
        while len(_CACHE) > _CACHE_SIZE or (len(_CACHE) > 1 and _cache_bytes() > _CACHE_BYTES):
            _CACHE.popitem(last=False)
    return ct


def _sidecar_families(ct_path: Path) -> dict[int, str]:
    """Material -> tejido de la ficha ``<fichero>.ct.json`` de ARGUS (los trozos de un tejido partido por densidad
    tienen el mismo; en la vista se sombrean como un tejido). {} si no hay ficha o no se entiende."""
    import json

    try:
        data = json.loads(ct_path.with_name(ct_path.name + ".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[int, str] = {}
    entries = data.get("materiales") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return out
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("material"), int) and e["material"] > 0:
            out[e["material"]] = str(e.get("tejido") or e.get("nombre") or e["material"])
    return out


def ct_families(ct) -> dict[int, str]:
    """Material -> tejido del que sale (de la ficha, si la hay; si no, {})."""
    return dict(getattr(ct, "__dict__", {}).get("_argus_family") or {})


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Escritura (formato de la documentación de PENGEOM: I2,1P,E11.4 por vóxel)
# ---------------------------------------------------------------------------

def _digits(v: np.ndarray) -> np.ndarray:
    return (v + 48).astype(np.uint8)


def voxel_lines(material: np.ndarray, density: np.ndarray, newline: bytes = b"\n") -> bytes:
    """Las líneas «MC DC» de todos los vóxeles, con el formato (I2,1P,E11.4) de PENGEOM.

    ``material`` y ``density`` en el orden del fichero (IX varía más rápido).
    """
    m = np.asarray(material).reshape(-1).astype(np.int64)
    d = np.asarray(density, dtype=np.float64).reshape(-1)
    n = m.size
    if d.size != n:
        raise ValueError("material y densidad con distinto número de vóxeles")
    if n and (m.min() < 0 or m.max() > MAX_CT_MATERIAL):
        raise ValueError(f"los materiales de un CT van de 0 a {MAX_CT_MATERIAL}")
    if n and (not np.all(np.isfinite(d)) or d.min() < 0.0):
        raise ValueError("densidades negativas o no numéricas")
    nl = len(newline)
    width = 13 + nl
    out = np.full((n, width), 32, dtype=np.uint8)
    # I2
    tens = m // 10
    out[:, 0] = np.where(tens > 0, _digits(tens), 32)
    out[:, 1] = _digits(m % 10)
    # 1P,E11.4: " d.ddddE±ee"
    zero = d <= 0.0
    safe = np.where(zero, 1.0, d)
    e = np.floor(np.log10(safe)).astype(np.int64)
    mant = np.rint(safe / np.power(10.0, e.astype(np.float64)) * 1.0e4).astype(np.int64)
    low = (mant < 10000) & ~zero  # log10 redondeado por arriba
    mant[low] = np.rint(safe[low] / np.power(10.0, (e[low] - 1).astype(np.float64)) * 1.0e4).astype(np.int64)
    e[low] -= 1
    over = mant >= 100000
    mant[over] = (mant[over] + 5) // 10
    e[over] += 1
    mant[zero] = 0
    e[zero] = 0
    if n and (e.max() > 99 or e.min() < -99):
        raise ValueError("densidad fuera del intervalo que admite el formato E11.4")
    out[:, 3] = _digits(mant // 10000)
    out[:, 4] = ord(".")
    out[:, 5] = _digits((mant // 1000) % 10)
    out[:, 6] = _digits((mant // 100) % 10)
    out[:, 7] = _digits((mant // 10) % 10)
    out[:, 8] = _digits(mant % 10)
    out[:, 9] = ord("E")
    out[:, 10] = np.where(e < 0, ord("-"), ord("+"))
    ae = np.abs(e)
    out[:, 11] = _digits(ae // 10)
    out[:, 12] = _digits(ae % 10)
    out[:, 13:] = np.frombuffer(newline, dtype=np.uint8)
    return out.tobytes()


def _ct_header(title: str, size, shape: tuple[int, int, int], newline: str) -> bytes:
    nz, ny, nx = shape
    xm, ym, zm = (float(v) for v in size)
    t = (title or "CT").replace("\r", " ").replace("\n", " ")[:72]
    head = (f"{t}{newline}"
            f" {xm:.15E} {ym:.15E} {zm:.15E}  ! Side lengths (cm){newline}"
            f" {nx:4d} {ny:4d} {nz:4d}  ! CT dimensions (nos. of voxels){newline}")
    return head.encode("latin-1", errors="replace")


def format_ct(title: str, size, material: np.ndarray, density: np.ndarray, newline: str = "\n") -> bytes:
    """Fichero .ct completo. ``material``/``density``: (NZ, NY, NX)."""
    mat = np.asarray(material)
    if mat.ndim != 3:
        raise ValueError("material tiene que ser (NZ, NY, NX)")
    return _ct_header(title, size, mat.shape, newline) + voxel_lines(mat, density, newline.encode("ascii"))


WRITE_CHUNK = 1 << 18  # vóxeles por bloque al escribir (unos 25 MB de memoria de trabajo)


class WriteCancelled(Exception):
    """Se ha dejado de escribir el .ct a medias (``cancel`` devolvió True)."""


def write_ct_stream(fh, title: str, size, material: np.ndarray, density: np.ndarray, newline: str = "\n",
                    chunk: int = WRITE_CHUNK, cancel=None) -> None:
    """Escribe el .ct en un fichero abierto (binario), por bloques: no hace falta tenerlo entero en memoria.
    ``cancel()``: si devuelve True entre dos bloques, se deja de escribir (:class:`WriteCancelled`)."""
    mat = np.asarray(material)
    den = np.asarray(density)
    if mat.ndim != 3 or den.shape != mat.shape:
        raise ValueError("material y densidad tienen que ser (NZ, NY, NX) y del mismo tamaño")
    fh.write(_ct_header(title, size, mat.shape, newline))
    fm = mat.reshape(-1)
    fd = den.reshape(-1)
    nl = newline.encode("ascii")
    for i in range(0, fm.size, chunk):
        if cancel is not None and cancel():
            raise WriteCancelled()
        fh.write(voxel_lines(fm[i:i + chunk], fd[i:i + chunk], nl))


def write_ct_file(path: str | os.PathLike, title: str, size, material: np.ndarray, density: np.ndarray,
                  newline: str = "\n", atomic: bool = True, cancel=None) -> Path:
    """Escribe el .ct por bloques. Con ``atomic``, en un temporal que luego se renombra (nunca queda un fichero
    a medias); si algo falla (o se cancela), el fichero que se estaba escribiendo se borra."""
    p = Path(path)
    target = p.with_name(p.name + ".tmp") if atomic else p
    try:
        with open(target, "wb") as fh:
            write_ct_stream(fh, title, size, material, density, newline, cancel=cancel)
        if atomic:
            os.replace(target, p)
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return p


# ---------------------------------------------------------------------------
# Vóxeles en el sistema del laboratorio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CtFrame:
    """Colocación de la caja CT: r_lab = R·r_CT + D (SHPRD)."""

    rotation: np.ndarray  # R (3, 3)
    shift: np.ndarray  # D (3,)
    size: tuple[float, float, float]
    shape: tuple[int, int, int]

    @property
    def voxel(self) -> np.ndarray:
        return np.array(self.size) / np.array(self.shape)

    def to_local(self, pts: np.ndarray) -> np.ndarray:
        """Coordenadas en el sistema del CT (como SHPRD con IPM = +1)."""
        return (np.asarray(pts, dtype=float) - self.shift) @ self.rotation

    def to_lab(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(pts, dtype=float) @ self.rotation.T + self.shift

    def corners(self) -> np.ndarray:
        """Las 8 esquinas de la caja en el laboratorio."""
        xm, ym, zm = self.size
        c = np.array([[x, y, z] for x in (0.0, xm) for y in (0.0, ym) for z in (0.0, zm)])
        return self.to_lab(c)


def ct_frame(geo) -> CtFrame | None:
    """Colocación del CT de una geometría compilada (None si no tiene)."""
    ct = getattr(geo, "ct", None)
    if ct is None or getattr(geo, "ct_body", None) is None:
        return None
    from .quadric import rotation_matrix

    om, th, ph, dx, dy, dz = geo.ct_transform
    r = np.array(rotation_matrix(om, th, ph), dtype=float)
    return CtFrame(r, np.array([dx, dy, dz], dtype=float), tuple(ct.size), tuple(ct.shape))


_CT_EPS = 1.0e-12  # el EPS de CTLOCATE


def _shprd(frame: CtFrame, v: np.ndarray, shift: bool) -> np.ndarray:
    """SHPRD con IPM = +1 término a término, como PENGEOM: X = R(1,1)·X1(1) + R(2,1)·X1(2) + R(3,1)·X1(3)… (el
    producto de matrices de NumPy redondea distinto y, en un plano entre vóxeles, cambiaba el vóxel)."""
    r = frame.rotation
    v = np.asarray(v, dtype=float).reshape(-1, 3)
    if shift:
        c0, c1, c2 = v[:, 0] - frame.shift[0], v[:, 1] - frame.shift[1], v[:, 2] - frame.shift[2]
    else:
        c0, c1, c2 = v[:, 0], v[:, 1], v[:, 2]
    out = np.empty_like(v)
    for a in range(3):
        out[:, a] = r[0, a] * c0 + r[1, a] * c1 + r[2, a] * c2
    return out


def voxel_indices(frame: CtFrame, pts: np.ndarray, dirs: np.ndarray | None = None
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Índices (ix, iy, iz), desde 0, del vóxel de cada punto y si está dentro de la caja.

    Como CTLOCATE, con su misma aritmética (undécima revisión: con «r / DX» en vez de «X·RDX», 0,3/0,1 daba
    2,999… y otro vóxel): un punto a menos de TT = max(1e-12, 1e-12·DX) de una cara de la caja se mete dentro, y uno
    a menos de 1e-12 vóxeles de un plano entre vóxeles va al vóxel hacia el que apunta la dirección (sin dirección,
    o paralela al plano, como PENGEOM con U = 0).
    """
    loc = _shprd(frame, pts, True)
    dl = np.zeros_like(loc) if dirs is None else _shprd(frame, np.broadcast_to(dirs, loc.shape), False)
    eps = _CT_EPS
    idx = np.empty(loc.shape, dtype=np.int64)
    inside = np.ones(loc.shape[0], dtype=bool)
    for a in range(3):
        xm = float(frame.size[a])
        na = int(frame.shape[a])
        dx = xm / float(na)  # DX = XM/DBLE(NX)
        rdx = 1.0 / dx  # RDX = 1.0D0/DX
        tt = max(eps, eps * dx)
        x = loc[:, a].copy()
        lo = np.abs(x) < tt
        hi = ~lo & (np.abs(x - xm) < tt)
        x[lo] = 2.0 * tt
        x[hi] = xm - 2.0 * tt
        inside &= ~(x * (x - xm) > 0.0)
        rx = x * rdx
        ix = np.trunc(rx)
        u = dl[:, a]
        on_lo = np.abs(rx - ix) < eps
        on_hi = ~on_lo & (np.abs(rx - (ix + 1.0)) < eps)
        x = np.where(on_lo, np.where(u > eps, (ix + 2.0 * eps) * dx, (ix - 2.0 * eps) * dx), x)
        x = np.where(on_hi, np.where(u < -eps, (ix + 1.0 - 2.0 * eps) * dx, (ix + 1.0 + 2.0 * eps) * dx), x)
        # (PENGEOM: IX = INT(X·RDX) + 1. Junto a la cara alta con vóxeles de 1 cm o más puede salir NX + 1, fuera de
        # la matriz; aquí, el último vóxel.)
        idx[:, a] = np.clip(np.trunc(x * rdx), 0, na - 1).astype(np.int64)
    return idx[:, 0], idx[:, 1], idx[:, 2], inside


def voxel_values(geo, pts: np.ndarray, dirs: np.ndarray | None = None, mask: np.ndarray | None = None
                 ) -> tuple[np.ndarray, np.ndarray]:
    """Material y densidad del vóxel en cada punto (material −1 y densidad 0 fuera del CT).

    ``mask``: puntos que se sabe que están en el cuerpo del CT (p. ej. los que
    LOCATE da en él); por defecto, los que caen dentro de la caja.
    """
    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    mat = np.full(pts.shape[0], -1, dtype=np.int32)
    den = np.zeros(pts.shape[0], dtype=np.float64)
    frame = ct_frame(geo)
    if frame is None or pts.shape[0] == 0:
        return mat, den
    sel = np.ones(pts.shape[0], dtype=bool) if mask is None else np.asarray(mask, dtype=bool).reshape(-1)
    if not sel.any():
        return mat, den
    d = None if dirs is None else np.broadcast_to(np.asarray(dirs, dtype=float), pts.shape)[sel]
    ix, iy, iz, inside = voxel_indices(frame, pts[sel], d)
    if mask is None:
        keep = inside
    else:
        keep = np.ones(ix.shape, dtype=bool)
    where = np.flatnonzero(sel)[keep]
    mat[where] = geo.ct.material[iz[keep], iy[keep], ix[keep]]
    den[where] = geo.ct.density[iz[keep], iy[keep], ix[keep]]
    return mat, den


def voxel_at(geo, point, direction=None, nudge: float = 0.0):
    """(ix, iy, iz, material, densidad) del vóxel de un punto del CT (índices desde 0), o None fuera.

    ``direction``: la del rayo o la normal del corte (decide el vóxel si el
    punto está en un plano entre dos, como CTLOCATE). ``nudge``: fracción del
    vóxel que se avanza en esa dirección antes de buscar (para los puntos de
    la vista 3D, que están en la cara del vóxel visible).
    """
    fr = ct_frame(geo)
    if fr is None:
        return None
    pt = np.asarray(point, dtype=float).reshape(1, 3)
    d = None
    if direction is not None:
        d = np.asarray(direction, dtype=float).reshape(1, 3)
        if nudge:
            pt = pt + d * (nudge * float(min(fr.voxel)))
    ix, iy, iz, inside = voxel_indices(fr, pt, d)
    if not inside[0]:
        return None
    i, j, k = int(ix[0]), int(iy[0]), int(iz[0])
    return i, j, k, int(geo.ct.material[k, j, i]), float(geo.ct.density[k, j, i])


def estimated_file_size(shape, newline: str = "\n") -> int:
    """Bytes del fichero .ct (14 por vóxel con fin de línea LF)."""
    nx, ny, nz = shape
    return 200 + int(nx) * int(ny) * int(nz) * (13 + len(newline))


def penhmain_memory(shape) -> int:
    """Memoria que reserva penhmain para el CT (MC, DC y los tres contadores de dosis, más LDOSECT)."""
    nx, ny, nz = shape
    return int(nx) * int(ny) * int(nz) * (4 + 8 + 3 * 8 + 4)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024.0
    return f"{n:.1f} GB"  # pragma: no cover


__all__ = ["CtData", "CtFormatError", "CtFrame", "NDD", "MAX_CT_MATERIAL", "clear_cache", "ct_frame",
           "estimated_file_size", "format_ct", "human_size", "load_ct", "penhmain_memory", "read_ct_bytes",
           "read_ct_file", "read_ct_text", "voxel_indices", "voxel_lines", "voxel_values", "write_ct_file"]
