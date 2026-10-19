"""Preparar la carpeta de simulación: todo lo que necesita penhmain para un .in, en una carpeta.

penhmain lee todos sus ficheros de la carpeta donde se ejecuta (``penhmain <
caso.in``): la geometría (GEOMFN) y sus INCLUDE, el .ct de un CTBOX, los .mat
(MFNAME), los ficheros ENDF que lista cada .mat al final (si se simulan
protones o alfas), el .nuc y los ficheros de relajación atómica de una fuente
radiactiva (SRNUCL), los espacios de fases de entrada (IPSFN), la tabla de
EPROF y el volcado de RESUME. Un OPEN sin STATUS de penhmain **crea vacío** el
fichero que falta, así que conviene tenerlos todos antes de empezar.

Los INCLUDE y el .ct se leen del .geo guardado que cita GEOMFN (el que leerá
penhmain), no de la geometría que se esté viendo, y solo se buscan en la carpeta
del .in: PENGEOM los abre en la carpeta de trabajo aunque GEOMFN tenga una
carpeta («GEOMFN sub/caso.geo»). Los demás se buscan primero en la carpeta del
.in (y la de cada .mat, para sus ENDF), luego en las carpetas que indique el
usuario y en la base de datos de penhan (``database/ENDF_data``,
``nucleide-files``, ``pdfiles``).

Nunca se sobrescribe ni se borra nada en la carpeta de destino: cada fichero se
copia a un temporal y se le da el nombre final sin reemplazar (enlace duro o
``rename`` de Windows, que fallan si ya existe); si ya hay un fichero con ese
nombre y distinto contenido, se deja y se avisa. Un nombre con ruta absoluta lo
lee penhmain de allí (no se copia, pero sí los ENDF de un .mat así); uno que
sube de carpeta (``..``) apuntaría a otro sitio desde la carpeta nueva: no se
copia y se avisa (ejecutando en la carpeta del .in, en cambio, es el mismo
fichero y penhmain lo lee de allí).

penhmain lee GEOMFN, MFNAME, IPSFN, EPROF y RESUME con ``(A20)`` desde la
columna 8 y abre el nombre tal cual, con los blancos del principio: «MFNAME
agua.mat» con dos espacios abre « agua.mat» (no lo encuentra, lo crea vacío y
se para). Esos nombres no se buscan sin el blanco: son un problema que hay que
corregir en el .in (0.16.3).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..core.files import CopyCancelled as _Cancelled
from ..core.files import copy_new as _copy_new
from ..core.numfmt import lower_first, plural
from .inputfile import InDocument
from .materials import hadron_data_needed, no_endf_reason, read_mat_nuclear

KINDS = {
    "in": "Fichero de entrada", "geo": "Geometría", "include": "Geometría incluida", "ct": "TAC (vóxeles)",
    "ctjson": "Ficha del TAC", "mat": "Material", "endf": "Datos ENDF", "nuc": "Nucleido", "pdfile": "Relajación",
    "psf": "Espacio de fases", "eprof": "Tabla de EPROF", "dump": "Volcado (RESUME)", "txt": "materiales.txt",
    "exe": "Ejecutable de penhmain", "dll": "Biblioteca del ejecutable",
}
STATUS_TEXT = {
    "copy": "se copiará", "copied": "copiado", "same": "ya está (igual)",
    "conflict": "ya hay OTRO con ese nombre: no se toca", "missing": "NO SE ENCUENTRA",
    "absolute": "ruta absoluta: penhmain lo leerá de allí (no se copia)",
    "up": "en una carpeta de arriba (..): penhmain lo leerá de allí (no se copia)",
    "outside": "la ruta sube de carpeta (..): no se copia y penhmain no lo encontraría",
    "failed": "no se ha podido copiar",
    "blank": "el nombre empieza por un espacio en el .in (columna 8 en blanco): penhmain no lo encontraría",
    "winpath": "ruta de Windows: un penhmain de Linux no la entiende y no lo encontraría",
}
# Los nombres que penhmain lee con «READ(BUFFER,'(A20)')» desde la columna 8 y abre tal cual, con los blancos del
# principio (penhmain.f): por el tipo de fichero, su palabra clave. SRNUCL se lee con formato libre (sin esos blancos)
# y PENGEOM quita el blanco del principio de los nombres de los INCLUDE y del .ct (pengeom.f).
A20_KEYWORDS = {"geo": "GEOMFN", "mat": "MFNAME", "psf": "IPSFN", "eprof": "EPROF", "dump": "RESUME"}
# Bibliotecas de MinGW/MSYS2 que necesita un penhmain.exe compilado sin -static.
DLL_PATTERNS = ("libgfortran*.dll", "libquadmath*.dll", "libgcc_s*.dll", "libwinpthread*.dll", "libstdc++*.dll")
CHUNK = 1 << 20


@dataclass
class Item:
    kind: str
    name: str  # como lo cita el .in o la geometría (relativo a la carpeta de trabajo de penhmain)
    source: Path | None
    required: bool = True
    note: str = ""
    status: str = ""
    size: int = 0
    # 'ok' (relativo), 'absolute', 'outside' (sube de carpeta, o unidad/raíz sin ruta completa) y, con un penhmain de
    # Linux (``plan_folder(linux=True)``), 'winpath' (un nombre con «\» o «C:», que no es una ruta para Linux) o
    # 'linux' (una ruta absoluta de Linux que desde Windows no se puede ver: dentro de WSL)
    where: str = "ok"
    lead: str = ""  # los blancos con que empieza el nombre en el .in (penhmain los conserva: no lo encontraría)
    lead_keyword: str = ""  # la palabra clave de la línea con esos blancos

    @property
    def kind_text(self) -> str:
        return KINDS.get(self.kind, self.kind)

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    @property
    def lead_problem(self) -> str:
        """«el nombre de RESUME empieza por un espacio (columna 8 en blanco): penhmain buscaría « dump.dmp», con el
        espacio, y no lo encontraría; quite el espacio» ('' si el nombre empieza en la columna 8)."""
        return lead_problem(self.lead_keyword or A20_KEYWORDS.get(self.kind, ""), self.lead + self.name)


@dataclass
class SimPlan:
    dest: Path
    items: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False

    def by_status(self, status: str) -> list[Item]:
        return [i for i in self.items if i.status == status]

    @property
    def to_copy(self) -> list[Item]:
        return self.by_status("copy")

    @property
    def missing(self) -> list[Item]:
        return [i for i in self.items if i.status == "missing" and i.required]

    @property
    def outside(self) -> list[Item]:
        return self.by_status("outside")

    @property
    def failed(self) -> list[Item]:
        return self.by_status("failed")

    @property
    def conflicts(self) -> list[Item]:
        return self.by_status("conflict")

    @property
    def blank(self) -> list[Item]:
        """Nombres que empiezan por un blanco en el .in (columna 8): penhmain no los encontraría."""
        return self.by_status("blank")

    @property
    def winpath(self) -> list[Item]:
        """Con un penhmain de Linux: nombres con «\\» o una unidad («C:»), que para Linux no son una ruta."""
        return self.by_status("winpath")

    @property
    def problems(self) -> list[Item]:
        """Lo que impedirá simular: ficheros que faltan, rutas que suben de carpeta, nombres que penhmain no
        encontraría (un blanco delante, una ruta de Windows para un penhmain de Linux) y copias fallidas."""
        return self.missing + [i for i in self.outside if i.required] + self.blank + self.winpath + self.failed

    @property
    def copy_bytes(self) -> int:
        return sum(i.size for i in self.to_copy)


def default_destination(in_path: str | os.PathLike) -> Path:
    """Carpeta nueva junto al .in: «caso_sim» (o «caso_sim2»... si ya existe y no está vacía)."""
    p = Path(in_path)
    base = p.parent / f"{p.stem}_sim"
    cand = base
    k = 2
    while cand.exists() and (not cand.is_dir() or any(cand.iterdir())):
        cand = p.parent / f"{p.stem}_sim{k}"
        k += 1
    return cand


def find_database(*start: str | os.PathLike | None) -> Path | None:
    """Carpeta ``database`` de penhan (la de material-list.txt)."""
    from .materials import find_material_list

    ml = find_material_list(*start)
    return ml.parent if ml is not None else None


def _posix(name: str) -> str:
    return name.replace("\\", "/")


def classify(name: str, linux: bool = False) -> str:
    """'ok' si es un nombre relativo dentro de la carpeta de trabajo; 'absolute' con una ruta completa
    (``C:\\...``, ``\\\\servidor\\...`` o ``/...`` fuera de Windows); 'outside' si sube de carpeta (``..``)
    o depende de la unidad o de la raíz actuales (``C:x.mat``, ``\\x.mat``).

    ``linux``: como lo entiende un penhmain de Linux o de macOS (también con WSL en Windows), para el que «/» separa
    las carpetas y «\\» es un carácter más: ``/...`` es una ruta completa y un nombre con «\\» o con una unidad
    (``C:\\m\\agua.mat``, ``mats\\agua.mat``) es 'winpath': penhmain buscaría en su carpeta un fichero que se llama
    así, con las barras."""
    if linux:
        if "\\" in name or PureWindowsPath(name).drive:
            return "winpath"
        if name.startswith("/"):
            return "absolute"
        return "outside" if ".." in PurePosixPath(name).parts else "ok"
    w = PureWindowsPath(name)
    if w.is_absolute() or (os.name != "nt" and name.startswith("/")):
        return "absolute"
    if w.drive or w.root or ".." in Path(_posix(name)).parts:
        return "outside"
    return "ok"


def _only_up(name: str) -> bool:
    """¿Un nombre relativo que sube de carpeta («../mats/agua.mat»), sin unidad ni raíz?"""
    w = PureWindowsPath(name)
    return not (w.drive or w.root or name.startswith("/")) and ".." in Path(_posix(name)).parts


def _find(name: str, dirs: list[Path | None], nocase: bool = False) -> Path | None:
    """El fichero ``name`` en la primera de ``dirs`` que lo tenga. ``nocase``: si no está tal cual, también con
    otras mayúsculas (los ENDF copiados de Windows o de un .zip en Linux: «PO16_ICRU63.ENDF»); se copia con el
    nombre que se busca, el que abrirá penhmain."""
    for d in dirs:
        if d is None:
            continue
        p = Path(d) / _posix(name)
        if p.is_file():
            return p
    if nocase and "/" not in _posix(name):
        key = name.lower()
        for d in dirs:
            if d is None:
                continue
            try:
                hit = next((Path(d) / f for f in sorted(os.listdir(d)) if f.lower() == key), None)
            except OSError:
                continue
            if hit is not None and hit.is_file():
                return hit
    return None


def find_endf(name: str, dirs: list[Path | None]) -> Path | None:
    """Un fichero ENDF en ``dirs``, también con otras mayúsculas (:func:`_find`)."""
    return _find(name, dirs, nocase=True)


def _same(a: Path, b: Path) -> bool:
    """¿Mismo contenido? Si no se puede comprobar (fichero ilegible, carpeta...), se da por distinto."""
    try:
        if not b.is_file() or a.stat().st_size != b.stat().st_size:
            return False
        if os.path.samefile(a, b):
            return True
        return _digest(a) == _digest(b)
    except OSError:
        return False


def _digest(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _geometry_files(geo_path: Path, search: list[Path | None]) -> tuple[list[str], list[str], list[str]]:
    """Nombres de los INCLUDE y de los .ct (CTFNAME) que cita un .geo y los ficheros que incluye.

    Devuelve (includes, cts, avisos). PENGEOM abre unos y otros por su nombre en
    la carpeta de trabajo; un CTBOX también puede estar en un fichero incluido.
    """
    from ..pengeom.model import CtBoxElement, IncludeElement
    from ..pengeom.parser import parse_geo_file

    includes: list[str] = []
    cts: list[str] = []
    notes: list[str] = []
    try:
        main = parse_geo_file(geo_path)
    except OSError as exc:
        notes.append(f"No se ha podido leer la geometría {geo_path.name} ({exc.strerror or exc}): no se sabe qué "
                     "ficheros INCLUDE o .ct necesita.")
        return includes, cts, notes
    docs = [main]
    for el in main.elements:
        if isinstance(el, IncludeElement) and el.filename:
            includes.append(el.filename)
            sub = _find(el.filename, search) if classify(el.filename) == "ok" else None
            if sub is not None:
                try:
                    docs.append(parse_geo_file(sub))
                except OSError:
                    pass
    for d in docs:
        cts += [el.filename for el in d.elements if isinstance(el, CtBoxElement) and el.filename]
    if not main.ok:
        notes.append(f"La geometría {geo_path.name} tiene errores de lectura: puede que falte algún fichero "
                     "INCLUDE o .ct en la lista.")
    return includes, cts, notes


def _exe_libraries(exe: Path) -> list[Path]:
    """DLL de MinGW que hay junto a un penhmain.exe (sin ellas, un .exe no estático no arranca en otra carpeta)."""
    if exe.suffix.lower() != ".exe":
        return []
    try:
        entries = sorted(exe.parent.iterdir())
    except OSError:
        return []
    return [p for p in entries if p.is_file() and any(fnmatch.fnmatch(p.name.lower(), pat) for pat in DLL_PATTERNS)]


def _hadrons_016(doc: InDocument) -> bool | None:
    """:func:`hadron_data_needed` como era hasta 0.16.1 (una fuente radiactiva: None, sin mirar el .nuc)."""
    src = doc.model.source
    if src.psf:
        return False
    return True if src.kparp > 3 else None if src.kparp == 0 else False


def lead_problem(keyword: str, raw: str) -> str:
    """Si el nombre ``raw`` que penhmain lee con (A20) desde la columna 8 empieza por un blanco: «el nombre de MFNAME
    empieza por un espacio (columna 8 en blanco): penhmain buscaría « agua.mat», con el espacio, y no lo encontraría;
    quite el espacio» (penhmain abre el nombre tal cual). '' si empieza en la columna 8."""
    raw = (raw or "").rstrip()
    name = raw.lstrip()
    lead = raw[:len(raw) - len(name)]
    if not lead or not name:
        return ""
    noun = "espacio" if set(lead) == {" "} else "tabulador" if set(lead) == {"\t"} else "blanco"
    if len(lead) == 1:
        what, the = f"un {noun}", f"el {noun}"
    else:
        what = noun + ("es" if noun == "tabulador" else "s")
        the = "los " + what
    return (f"el nombre de {keyword} empieza por {what} (columna 8 en blanco): penhmain buscaría «{lead}{name}», con "
            f"{the}, y no lo encontraría; quite {the}")


def in_name_problem(name: str, width: int = 20) -> str | None:
    """Por qué un fichero con este nombre no se puede citar bien en el .in (GEOMFN, MFNAME…: penhmain lee ``width``
    caracteres), o None: demasiado largo, o con tildes, eñes u otros caracteres que no son ASCII (el .in va en
    Latin-1 y un penhmain de Linux o de macOS, también con WSL, buscaría otro nombre: en el disco va en UTF-8)."""
    if len(name) > width:
        return f"penhmain solo lee {width} caracteres y «{name}» tiene {len(name)}"
    if not name.isascii() or any(not 32 <= ord(c) < 127 for c in name):
        return (f"«{name}» tiene tildes, eñes u otros caracteres que no son ASCII: un penhmain de Linux (WSL) o de "
                "macOS no encontraría el fichero (el .in va en Latin-1 y el nombre del fichero, en UTF-8)")
    return None


def windows_from_wsl(name: str) -> str | None:
    """La ruta de Windows de una ruta de Linux de WSL en una unidad del ordenador («/mnt/c/m/agua.mat» →
    «C:\\m\\agua.mat»), o None si está dentro del Linux de WSL («/home/…»: desde Windows no se puede comprobar)."""
    m = re.match(r"/mnt/([A-Za-z])(?:/(.*))?$", name)
    if m is None:
        return None
    return f"{m.group(1).upper()}:\\" + (m.group(2) or "").replace("/", "\\")


def plan_folder(in_path: str | os.PathLike, doc: InDocument, dest: str | os.PathLike, *,
                database: str | os.PathLike | None = None, extra_dirs: list | None = None,
                penhmain: str | os.PathLike | None = None, sources: dict[str, Path] | None = None,
                endf_dir: str | os.PathLike | None = None, legacy: bool = False, linux: bool = False) -> SimPlan:
    """Qué hay que copiar a ``dest`` para simular ``in_path`` (sin copiar nada todavía).

    La geometría es la que cita GEOMFN tal como está guardada (la que leerá
    penhmain): de ella salen los INCLUDE y el .ct. ``sources``: de dónde sale el
    fichero de algunos nombres (en minúsculas), en vez de buscarlo (la cascada da
    otro nombre a los que suben de carpeta). ``endf_dir``: la carpeta de los ENDF
    del usuario (si no, los de ``database/ENDF_data``). ``legacy``: los ENDF como
    hasta 0.16.1 (con una fuente radiactiva, los que haya, sin mirar el .nuc, y
    solo con sus mayúsculas): para reconocer las cascadas de entonces por su firma.
    ``linux``: los nombres como los entiende el penhmain de Linux (o de macOS) que
    se va a ejecutar, también con WSL (:func:`classify`).
    """
    in_path = Path(in_path)
    src = in_path.parent
    dest = Path(dest)
    db = Path(database) if database else find_database(src)
    extra = [Path(d) for d in (extra_dirs or []) if d]
    plan = SimPlan(dest)
    items: dict[str, Item] = {}

    def add(kind: str, name: str, dirs: list, required: bool = True, note: str = "") -> Item | None:
        raw = (name or "").rstrip()
        name = raw.strip()
        if not name:
            return None
        # Lo que penhmain lee con (A20) lo abre tal cual: un blanco delante es parte del nombre (no lo encontraría).
        lead = raw[:len(raw) - len(raw.lstrip())] if kind in A20_KEYWORDS else ""
        key = _posix(name).lower()
        if key in items:
            it = items[key]
            if lead and not it.lead:  # el mismo fichero, citado otra vez con un blanco delante
                it.lead, it.lead_keyword = lead, A20_KEYWORDS[kind]
            return it
        where = classify(name, linux)
        if sources and key in sources:
            source = sources[key] if Path(sources[key]).is_file() else None
            where = "ok"
        elif where == "ok":
            source = _find(name, dirs, nocase=kind == "endf" and not legacy)
        elif where == "absolute":
            path: str | None = name
            if linux and os.name == "nt":  # una ruta de Linux vista desde Windows (penhmain con WSL)
                path = windows_from_wsl(name)
                if path is None:
                    where = "linux"
            source = Path(path) if path is not None and Path(path).is_file() else None
        elif where == "winpath":
            source = None
        else:  # donde lo encontraría penhmain ejecutado en la carpeta del .in
            p = src / _posix(name)
            source = p if p.is_file() else None
        it = Item(kind, name, source, required, note, where=where)
        if lead:
            it.lead, it.lead_keyword = lead, A20_KEYWORDS[kind]
        items[key] = it
        return it

    add("in", in_path.name, [src])
    m = doc.model
    geo = add("geo", m.geomfn, [src])
    if geo is not None and geo.source is not None:
        # PENGEOM abre los INCLUDE y el .ct en la carpeta de trabajo de penhmain (la del .in), aunque la
        # geometría esté en otra («GEOMFN sub/caso.geo»).
        search = [src]
        includes, cts, notes = _geometry_files(geo.source, search)
        plan.notes += notes
        beside = geo.source.parent
        for kind, name in [("include", n) for n in includes] + [("ct", n) for n in cts]:
            it = add(kind, name, search)
            if kind == "ct":
                add("ctjson", name + ".json", search, required=False,
                    note="no la usa penhmain: composición de los tejidos y datos de la importación")
            if it is not None and it.source is None and it.where == "ok" and _find(name, [beside]) is not None:
                it.note = (f"está junto a la geometría ({beside.name}/), pero penhmain lo busca en la carpeta del .in: "
                           "cópielo allí")
    hadrons = _hadrons_016(doc) if legacy else hadron_data_needed(doc, db, [src] + extra)
    endf_dirs = [src] + extra + ([Path(endf_dir)] if endf_dir else []) + ([db / "ENDF_data"] if db else [])
    for md in m.materials:
        it = add("mat", md.fname, [src] + extra)
        if it is None or it.source is None or hadrons is False:
            continue
        nuc = read_mat_nuclear(it.source)
        if nuc is None:
            continue
        note = "" if hadrons else "solo si el nucleido emite partículas pesadas de más de 100 keV"
        for fname, _iza, _ab in nuc.isotopes:  # penhmain los abre por su nombre en la carpeta de trabajo
            add("endf", fname, [it.source.parent] + endf_dirs, required=bool(hadrons), note=note)
    if hadrons is False:
        plan.notes.append(no_endf_reason(doc) + " (no se copian).")
    src_def = m.source
    if src_def.kparp == 0 and src_def.nuclide and not src_def.psf:
        add("nuc", src_def.nuclide, [src] + extra + ([db / "nucleide-files"] if db else []))
        for f in ("pdrelax.p11", "pdatconf.p14"):
            add("pdfile", f, [src] + extra + ([db / "pdfiles"] if db else []))
    for f in src_def.psf:
        add("psf", f, [src])
    for ln in doc.find("EPROF "):
        add("eprof", ln.text, [src])
    res = doc.first("RESUME")
    if res is not None and res.text.strip():
        add("dump", res.text, [src], required=False)
    add("txt", "materiales.txt", [src], required=False, note="no la usa penhmain: cómo generar los .mat")
    if penhmain:
        exe = Path(penhmain)
        items["\0exe"] = Item("exe", exe.name, exe if exe.is_file() else None, True)
        for lib in _exe_libraries(exe):
            items.setdefault(lib.name.lower(), Item("dll", lib.name, lib, False,
                                                    "estaba junto al ejecutable (MinGW): puede necesitarla"))
    in_place = os.path.normcase(os.path.abspath(dest)) == os.path.normcase(os.path.abspath(src))
    for it in items.values():
        if it.lead:
            it.status = "blank"  # penhmain buscaría el nombre con el blanco delante: no se copia, se dice
            continue
        if it.where == "winpath":
            it.status = "winpath"
            continue
        if it.where == "linux":
            it.status = "absolute"
            it.note = "ruta de Linux, dentro de WSL: ARGUS no puede comprobar que esté" + (
                f"; {it.note}" if it.note else "")
            continue
        if it.where == "outside":
            # Ejecutando en la carpeta del .in, «../mats/agua.mat» es justo ese fichero: penhmain lo lee de allí.
            it.status = "up" if in_place and it.source is not None and _only_up(it.name) else "outside"
            continue
        if it.source is None:
            it.status = "missing"
            if not it.required and it.kind in ("txt", "ctjson"):
                it.status = ""  # opcional y no está: no se menciona
            elif it.kind == "dump":
                # Sin volcado, penhmain lo crea y empieza desde cero; pero si tampoco está su carpeta, no puede ni
                # abrirlo y se detiene (duodécima revisión: el que faltaba no salía en la lista).
                sub = posixpath.dirname(_posix(it.name))
                if not sub or (dest / sub).is_dir() or (in_place and (src / sub).is_dir()):
                    it.note = "no está: penhmain empezará desde cero"
                else:
                    it.note = (f"no está, ni su carpeta ({sub}): penhmain no podría abrirlo y se detendría al empezar "
                               "(cree la carpeta o quite RESUME)")
                    plan.notes.append(f"RESUME: {it.name} {it.note}.")
            continue
        if it.where == "absolute":
            it.status = "absolute"
            continue
        try:
            it.size = it.source.stat().st_size
        except OSError:
            it.status = "missing"
            continue
        target = dest / _posix(it.name)
        if os.path.lexists(target):
            it.status = "same" if _same(it.source, target) else "conflict"
        else:
            it.status = "copy"
    plan.items = [i for i in items.values() if i.status]
    return plan


def _error_text(exc: OSError) -> str:
    return exc.strerror or str(exc) or type(exc).__name__


def execute(plan: SimPlan, progress: Callable[[int, int, str], None] | None = None,
            cancel: Callable[[], bool] | None = None) -> list[Path]:
    """Copia lo que falta (nunca sobrescribe). Devuelve los ficheros copiados.

    ``cancel()`` se consulta entre ficheros y cada MiB: si devuelve True se para,
    se borra la copia a medias y ``plan.cancelled`` queda a True. Un fichero que
    no se puede copiar queda como 'failed' (con el motivo en ``note``) y se sigue
    con los demás.
    """
    plan.dest.mkdir(parents=True, exist_ok=True)
    plan.cancelled = False
    out: list[Path] = []
    todo = plan.to_copy
    done = 0
    for it in todo:
        if cancel is not None and cancel():
            plan.cancelled = True
            break
        if progress is not None:
            progress(done, len(todo), it.name)
        target = plan.dest / _posix(it.name)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fresh = _copy_new(it.source, target, executable=it.kind == "exe", cancel=cancel)
        except _Cancelled:
            plan.cancelled = True
            break
        except OSError as exc:
            it.status = "failed"
            it.note = _error_text(exc)
            done += 1
            continue
        done += 1
        if fresh:
            it.status = "copied"
            out.append(target)
        else:  # apareció mientras tanto: no se toca
            it.status = "same" if _same(it.source, target) else "conflict"
    if progress is not None:
        progress(done, len(todo), "")
    return out


def summary(plan: SimPlan) -> str:
    """Resumen para el usuario (texto plano)."""
    lines = [f"Carpeta: {plan.dest}",
             f"{plural(len(plan.by_status('copied')), 'fichero copiado', 'ficheros copiados')}, "
             f"{len(plan.by_status('same'))} ya {'estaba' if len(plan.by_status('same')) == 1 else 'estaban'}."]
    if plan.cancelled:
        lines.append(f"Cancelado: {plural(len(plan.to_copy), 'fichero')} sin copiar.")
    for it in plan.failed:
        lines.append(f"No se ha podido copiar {it.name}: {it.note}")
    for it in plan.missing:
        lines.append(f"FALTA {lower_first(it.kind_text)}: {it.name}" + (f" ({it.note})" if it.note else ""))
    for it in plan.outside:
        lines.append(f"{it.name}: la ruta sube de carpeta o depende de la unidad actual; penhmain no lo "
                     "encontraría desde la carpeta de simulación (póngale en el .in un nombre sin carpeta).")
    for it in plan.blank:
        lines.append(it.lead_problem[:1].upper() + it.lead_problem[1:] + ".")
    for it in plan.winpath:
        lines.append(f"{it.name}: ruta de Windows; un penhmain de Linux no lo encontraría (cópielo junto al .in y "
                     "cítelo solo por su nombre).")
    for it in plan.conflicts:
        lines.append(f"No se ha tocado {it.name}: en la carpeta ya hay otro fichero con ese nombre.")
    for it in plan.by_status("absolute"):
        lines.append(f"{it.name}: ruta absoluta, penhmain lo leerá de allí (no se copia).")
    lines += plan.notes
    return "\n".join(lines)


__all__ = ["A20_KEYWORDS", "Item", "KINDS", "SimPlan", "classify", "default_destination", "execute", "find_database",
           "in_name_problem", "lead_problem", "plan_folder", "summary", "windows_from_wsl"]
