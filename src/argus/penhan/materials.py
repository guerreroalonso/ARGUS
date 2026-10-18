"""Materiales de un caso: qué fichero .mat usa cada material y cuáles faltan.

En penhmain el material número N de la geometría (``MATERIAL(N)`` en el
.geo) se lee del fichero de la N-ésima línea ``MFNAME`` del .in. ARGUS no
genera los .mat (se hacen con ``penhmat``, el programa MATERIAL de penhan,
que llama a PEMATW y PHMATW): deja que el usuario les dé nombre y escribe
``materiales.txt`` con los que hay que generar.

De los .mat que ya existen se comprueba lo que hace fallar a penhmain al
leerlos (subrutina PHMATR de penhan.f): la lista final de ficheros ENDF
(``*** ENDF filenames and abundances of isotopes, NISOT = N``) tiene que estar
completa, cada fichero ENDF tiene que estar en la carpeta de trabajo
(IRETRN 40028) y, entre todos los materiales, no puede haber más de
MNIS = 30 isótopos distintos (IRETRN 40029; con más de 30 en un solo material
se desborda la matriz FNAME de PHMATR). penhmain solo lee esa parte (PHINIT)
si la fuente es de protones o alfas, o radiactiva con partículas pesadas de
más de 100 keV; con electrones, fotones o positrones basta un .mat de PENELOPE.

El nombre descriptivo de cada material se guarda en el propio .in, como
comentario de su línea ``MFNAME`` (``[Agua líquida (PENELOPE 278)]``);
penhmain no lee lo que hay después de la columna 27 en esa línea. Una
composición propia (opción 1 de penhmat, :mod:`argus.penhan.composition`) va
en comentarios justo detrás: ``[ARGUS pmma.mat: composicion por atomos;
densidad 1.19 g/cm3; I segun ARGUS]`` y ``[ARGUS pmma.mat: C 5 H 8 O 2]``.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..core.fortran_io import text_records
from ..core.numfmt import plural
from .composition import Composition, comment_parts, read_mat_composition
from .inputfile import BLANK_KW, InDocument, InLine
from .keywords import SPEC

MFNAME_COMMENT = SPEC["MFNAME"].comment  # «[Material file, up to 20 chars]»
MATERIALS_TXT = "materiales.txt"
TXT_MARK = "ARGUS · materiales"
MAXMAT = 10  # PENELOPE_mod (penhmain 2025)
MNIS = 30  # isótopos con datos ENDF, entre todos los materiales (penhan.f)
_ENDF_HEADER = re.compile(r"ENDF filenames and abundances of isotopes,\s*NISOT\s*=\s*(-?\d+)")
_NALPHA = re.compile(r"^\s*(-?\d+)\s*=\s*NALPHA")
_VALID_NAME = re.compile(r"^[A-Za-z0-9._+\-()\[\]#@~]+$")
_HEADER_NAME = re.compile(r"^\s*Material:\s*(.*?)\s*(?:\((\d+)\))?\s*$")
_HEADER_DENSITY = re.compile(r"Mass density\s*=\s*([-+0-9.EeDd]+)")


# ---------------------------------------------------------------------------
# Nombre en el comentario de MFNAME
# ---------------------------------------------------------------------------

def material_description(line: InLine) -> str:
    """Nombre descriptivo guardado en el comentario de una línea MFNAME ('' si no hay)."""
    c = line.comment.strip()
    m = re.match(r"^\[([^\]]*)\]", c)
    text = m.group(1).strip() if m else c
    if not text or f"[{text}]" == MFNAME_COMMENT:
        return ""
    return text


def description_comment(description: str) -> str:
    d = " ".join(description.replace("[", "(").replace("]", ")").split())
    return f"[{d}]" if d else MFNAME_COMMENT


def mat_filename_problem(name: str) -> str | None:
    """Motivo por el que penhmain no podría usar este nombre de fichero (None si vale)."""
    if not name:
        return "El nombre del fichero está vacío."
    if len(name) > 20:
        return f"«{name}» tiene {len(name)} caracteres; penhmain solo lee 20 (columnas 8-27)."
    if "/" in name or "\\" in name:
        return f"«{name}» está en otra carpeta: ARGUS solo escribe y genera los .mat de la carpeta del .in."
    if not _VALID_NAME.match(name):
        return f"«{name}» tiene espacios u otros caracteres que no conviene usar en un nombre de fichero."
    return None


# ---------------------------------------------------------------------------
# Edición del .in
# ---------------------------------------------------------------------------

def _material_groups(doc: InDocument) -> list[list[int]]:
    """Índices de las líneas de cada material: MFNAME y sus MSIMPA/MSIMPH/MSIMPN."""
    groups: list[list[int]] = []
    open_group = False
    for i, ln in enumerate(doc.lines):
        if ln.kw == "MFNAME":
            groups.append([i])
            open_group = True
        elif ln.kw in ("MSIMPA", "MSIMPH", "MSIMPN") and open_group:
            groups[-1].append(i)
        elif ln.kw.strip():  # otra palabra clave cierra el grupo (los comentarios no)
            open_group = False
    return groups


def set_material(doc: InDocument, number: int, fname: str | None = None, description: str | None = None) -> None:
    """Fichero .mat y nombre descriptivo del material ``number`` (1, 2...).

    Si el .in tiene menos líneas MFNAME, se añaden (con los parámetros de
    simulación del último material, o los de penhmain por defecto) hasta
    llegar a ``number``. Si cambia el fichero, su composición propia (si la
    tiene y ningún otro material usa el fichero de antes) pasa al nuevo.
    """
    if number < 1:
        raise ValueError("El número de material empieza en 1")
    mf = doc.find("MFNAME")
    if number > len(mf):
        _append_materials(doc, number)
        mf = doc.find("MFNAME")
    line = mf[number - 1]
    old = line.text.strip()
    doc.modify(line, text=fname.strip() if fname is not None else None,
               comment=description_comment(description) if description is not None else None)
    new = line.text.strip()
    if fname is not None and new != old and old and not any(ln.text.strip() == old for ln in doc.find("MFNAME")):
        rec = find_recipe(doc, old, exact=True)
        if rec is not None and find_recipe(doc, new, exact=True) is None:
            _retag(doc, rec, new)
        elif rec is not None:  # el fichero nuevo ya tiene la suya: la de antes ya no es de ningún material
            drop = {id(ln) for ln in rec.lines}
            doc.lines = [ln for ln in doc.lines if id(ln) not in drop]
            doc.analyze()


def add_material(doc: InDocument, fname: str, description: str = "") -> int:
    """Declara un material más (línea MFNAME y sus parámetros de simulación): devuelve su número."""
    n = len(doc.find("MFNAME")) + 1
    if n > MAXMAT:
        raise ValueError(f"penhmain admite como máximo {MAXMAT} materiales.")
    set_material(doc, n, fname=fname, description=description)
    return n


def remove_last_material(doc: InDocument) -> int:
    """Quita el último material declarado (su MFNAME y sus MSIMPA/MSIMPH/MSIMPN, y su composición propia si ningún
    otro material usa su fichero): devuelve su número."""
    groups = _material_groups(doc)
    if len(groups) < 2:
        raise ValueError("penhmain necesita al menos un material (una línea MFNAME).")
    drop = set(groups[-1])
    fname = doc.lines[groups[-1][0]].text.strip()
    others = {doc.lines[g[0]].text.strip() for g in groups[:-1]}
    rec = find_recipe(doc, fname, exact=True) if fname and fname not in others else None
    lines = {id(ln) for ln in rec.lines} if rec is not None else set()
    doc.lines = [ln for i, ln in enumerate(doc.lines) if i not in drop and id(ln) not in lines]
    doc.analyze()
    return len(groups)


# ---------------------------------------------------------------------------
# Composición propia (opción 1 de penhmat), en comentarios del .in
# ---------------------------------------------------------------------------

@dataclass
class Recipe:
    """La composición propia de un fichero .mat, tal como está en los comentarios del .in."""

    fname: str
    composition: Composition | None
    error: str | None  # por qué no se entiende (None si se entiende)
    lines: list[InLine] = field(default_factory=list)


def recipes(doc: InDocument | None) -> dict[str, Recipe]:
    """Fichero .mat -> su composición propia (de las líneas «[ARGUS fichero.mat: …]» del .in)."""
    if doc is None:
        return {}
    groups: dict[str, list[InLine]] = {}
    for ln in doc.lines:
        if ln.is_comment:
            parts = comment_parts(ln.text)
            if parts is not None:
                groups.setdefault(parts[0], []).append(ln)
    out = {}
    for fname, lines in groups.items():
        try:
            comp, err = Composition.from_comment_bodies([comment_parts(ln.text)[1] for ln in lines]), None
        except ValueError as exc:
            comp, err = None, str(exc)
        out[fname] = Recipe(fname, comp, err, lines)
    return out


def find_recipe(doc: InDocument | None, fname: str, *, exact: bool = False) -> Recipe | None:
    """La composición propia del fichero ``fname`` (sin ``exact``, también con otras mayúsculas: en Windows es el
    mismo fichero)."""
    found = recipes(doc)
    name = fname.strip()
    if name in found:
        return found[name]
    if not exact:
        low = name.lower()
        for key, rec in found.items():
            if key.lower() == low:
                return rec
    return None


def material_recipe(doc: InDocument | None, number: int) -> Recipe | None:
    """La composición propia del material ``number`` (por el fichero de su línea MFNAME)."""
    if doc is None:
        return None
    mf = doc.find("MFNAME")
    if not 1 <= number <= len(mf) or not mf[number - 1].text.strip():
        return None
    return find_recipe(doc, mf[number - 1].text)


def _recipe_lines(comp: Composition, fname: str) -> list[InLine]:
    return [InLine(kw=BLANK_KW, text=t, modified=True) for t in comp.comment_texts(fname)]


def _retag(doc: InDocument, rec: Recipe, new: str) -> None:
    """Las líneas de una composición, con otro fichero .mat."""
    if rec.composition is not None:
        fresh = _recipe_lines(rec.composition, new)
        first = next((i for i, ln in enumerate(doc.lines) if ln is rec.lines[0]), None)
        drop = {id(ln) for ln in rec.lines}
        if first is None:
            return
        doc.lines = [ln for ln in doc.lines[:first] if id(ln) not in drop] + fresh + [
            ln for ln in doc.lines[first:] if id(ln) not in drop]
    else:  # no se entiende: se cambia solo el nombre, sin tocar lo demás
        for ln in rec.lines:
            ln.text = ln.text.replace(f" {rec.fname}:", f" {new}:", 1)
            ln.modified = True
    doc.analyze()


def set_recipe(doc: InDocument, number: int, comp: Composition | None) -> None:
    """Composición propia del material ``number`` (None: se quita y vuelve a ser de la lista de PENELOPE o de lo que
    diga su nombre). Va en comentarios justo detrás de su línea MFNAME. ``ValueError`` si no vale."""
    mf = doc.find("MFNAME")
    if not 1 <= number <= len(mf):
        raise ValueError(f"No hay material {number} en el .in.")
    line = mf[number - 1]
    fname = line.text.strip()
    if comp is not None:
        comp.check()
        problem = mat_filename_problem(fname)
        if problem:
            raise ValueError(problem)
        new = _recipe_lines(comp, fname)
    else:
        new = []
    old = find_recipe(doc, fname, exact=True)
    drop = {id(ln) for ln in old.lines} if old is not None else set()
    kept = [ln for ln in doc.lines if id(ln) not in drop]
    pos = next(i for i, ln in enumerate(kept) if ln is line) + 1
    doc.lines = kept[:pos] + new + kept[pos:]
    doc.analyze()


def suggest_mat_filename(name: str, taken: set[str] | None = None) -> str:
    """Nombre de fichero .mat a partir del nombre de un material («Water, liquid» -> «water.mat», «Óxido de
    aluminio» -> «oxido.mat», «n-heptano» -> «heptano.mat»): la primera palabra de dos letras o más, sin tildes."""
    import unicodedata

    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).lower()
    words = [w for w in re.findall(r"[a-z0-9]+", plain) if len(w) >= 2 and not w.isdigit()]
    base = (words[0] if words else "material")[:14]
    taken = {t.lower() for t in (taken or set())}
    cand = f"{base}.mat"
    k = 2
    while cand.lower() in taken:
        cand = f"{base}{k}.mat"
        k += 1
    return cand


def _append_materials(doc: InDocument, number: int) -> None:
    mf = doc.find("MFNAME")
    groups = _material_groups(doc)
    if groups:
        last = groups[-1]
        pos = last[-1] + 1
        template = [doc.lines[i] for i in last[1:]]
    else:
        pos = doc._insert_index("MFNAME")
        template = []
    new: list[InLine] = []
    for k in range(len(mf) + 1, number + 1):
        new.append(InLine(kw="MFNAME", text=f"material{k}.mat", comment=MFNAME_COMMENT, modified=True))
        for t in template:
            new.append(InLine(kw=t.kw, values=list(t.values), comment=t.comment or SPEC[t.kw].comment,
                              modified=True))
    doc.insert_lines(pos, new)


# ---------------------------------------------------------------------------
# Ficheros .mat y lista de materiales de PENELOPE
# ---------------------------------------------------------------------------

@dataclass
class MatHeader:
    name: str
    penelope_id: int | None
    density: float | None
    version: str


def read_mat_header(path: str | Path) -> MatHeader | None:
    """Nombre, número y densidad de la cabecera de un fichero de material de PENELOPE."""
    try:
        with open(path, encoding="latin-1", errors="replace") as f:
            first = [f.readline() for _ in range(3)]
    except OSError:
        return None
    if "Material data file" not in first[0]:
        return None
    version = first[0].split("Material")[0].strip()
    m = _HEADER_NAME.match(first[1])
    if m is None:
        return None
    dens = None
    d = _HEADER_DENSITY.search(first[2])
    if d:
        try:
            dens = float(d.group(1).replace("D", "E").replace("d", "e"))
        except ValueError:
            dens = None
    return MatHeader(m.group(1).strip(), int(m.group(2)) if m.group(2) else None, dens, version)


@dataclass
class MatNuclear:
    """Final de un .mat de penhan: isótopos con datos ENDF de reacciones nucleares."""

    nisot: int | None  # None si no hay sección ENDF (fichero incompleto o sin datos de hadrones)
    isotopes: list[tuple[str, int, float]] = field(default_factory=list)  # (fichero ENDF, IZA, abundancia)
    nalpha: int | None = None

    @property
    def uses_endf(self) -> bool:
        """penhan lee los ficheros ENDF (ENDFRH) solo con NISOT > 0; con NISOT < 0 las reacciones nucleares son
        absorbentes y con 0 no hay (penhan.f, PHMRDR)."""
        return self.nisot is not None and self.nisot > 0

    @property
    def complete(self) -> bool:
        if self.nisot is None:
            return False
        if not self.uses_endf:
            return True
        return len(self.isotopes) == self.nisot and self.nalpha is not None


_NUCLEAR_CACHE: dict[tuple[str, int, int], MatNuclear] = {}


def read_mat_nuclear(path: str | Path, tail_bytes: int = 65536) -> MatNuclear | None:
    """Lista final de ficheros ENDF de un .mat de penhan (lee solo el final del fichero)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    if key in _NUCLEAR_CACHE:
        return _NUCLEAR_CACHE[key]
    try:
        with open(p, "rb") as f:
            f.seek(max(0, st.st_size - tail_bytes))
            lines = text_records(f.read().decode("latin-1", errors="replace"))
    except OSError:
        return None
    res = MatNuclear(None)
    start = None
    for i in range(len(lines) - 1, -1, -1):
        m = _ENDF_HEADER.search(lines[i])
        if m:
            res.nisot = int(m.group(1))
            start = i + 1
            break
    if start is not None and res.nisot > 0:  # con NISOT ≤ 0 penhan no lee ninguna lista
        for ln in lines[start:start + res.nisot]:
            parts = ln.split()
            if len(parts) < 3:
                break
            try:
                res.isotopes.append((parts[0], int(parts[1]), float(parts[2].replace("D", "E"))))
            except ValueError:
                break
        rest = lines[start + len(res.isotopes):]
        for ln in rest[:2]:
            m = _NALPHA.match(ln)
            if m:
                res.nalpha = int(m.group(1))
                break
    _NUCLEAR_CACHE[key] = res
    return res


class MaterialList(list):
    """La lista de materiales de PENELOPE: (n.º, nombre en inglés). Con ``pdfiles/pdcompos.pen`` de la misma base
    de datos, también los átomos por molécula y la densidad de cada uno (para reconocer una fórmula química en el
    nombre de un fichero: «H2O.mat», «Al2O3.mat»)."""

    def __init__(self, items=(), formulas: dict[int, dict[int, float]] | None = None,
                 densities: dict[int, float] | None = None, builtin: bool = False):
        super().__init__(items)
        self.formulas: dict[int, dict[int, float]] = formulas or {}
        self.densities: dict[int, float] = densities or {}
        self.builtin = builtin  # la que trae ARGUS (:func:`builtin_material_list`), no la de la base de datos


def builtin_material_list() -> MaterialList:
    """Los 280 materiales de la lista de PENELOPE con sus números y sus nombres en inglés, los que trae ARGUS
    (:data:`argus.penhan.material_names.NAMES`, los de ``material-list.txt`` de penhan 2025), para cuando no se
    encuentra la del usuario: sin la carpeta de penhan en «Programas de penhan» y sin penhan cerca del caso (en el
    ejecutable, la carpeta del programa no está junto a penhan). El desplegable del material los ofrece igual, sin
    fórmulas ni densidades (son de ``pdfiles/pdcompos.pen``, de la base de datos); generar el .mat sigue necesitando la
    carpeta de penhan (penhmat)."""
    from .material_names import NAMES

    return MaterialList(sorted((n, english) for n, (english, _es) in NAMES.items()), builtin=True)


def material_list_or_builtin(path: str | Path | None) -> MaterialList:
    """La lista de ``path`` (``material-list.txt``) o, si no hay o no se lee, la que trae ARGUS."""
    found = load_material_list(path) if path is not None else None
    return found if found else builtin_material_list()


_COMPOS_HEAD = re.compile(r"^\s*(\d+)\s+\S.*\(\s*(\d+)\s*\)\s+(\d+)\*?\s*$")


def read_compositions(path: str | Path) -> tuple[dict[int, dict[int, float]], dict[int, float]]:
    """Átomos por molécula (índice estequiométrico de cada elemento) y densidad de los materiales de PENELOPE, de
    ``pdfiles/pdcompos.pen`` (vacíos si no está o no se entiende)."""
    formulas: dict[int, dict[int, float]] = {}
    densities: dict[int, float] = {}
    try:
        lines = Path(path).read_text(encoding="latin-1", errors="replace").splitlines()
    except OSError:
        return formulas, densities
    i = 0
    while i < len(lines):
        m = _COMPOS_HEAD.match(lines[i])
        if m is None or len({m.group(1), m.group(2), m.group(3)}) != 1:
            i += 1
            continue
        try:
            head = lines[i + 1].split()
            nel, rho = int(head[0]), float(head[3].replace("D", "E"))
            atoms: dict[int, float] = {}
            for ln in lines[i + 2:i + 2 + nel]:
                f = ln.split()
                atoms[int(f[0])] = float(f[2].replace("D", "E"))
        except (IndexError, ValueError):
            i += 1
            continue
        if len(atoms) == nel and nel > 0:
            formulas[int(m.group(1))] = atoms
            densities[int(m.group(1))] = rho
        i += 2 + nel
    return formulas, densities


def load_material_list(path: str | Path) -> MaterialList:
    """Lista de materiales predefinidos de PENELOPE (database/material-list.txt), con las fórmulas y densidades de
    ``database/pdfiles/pdcompos.pen`` si está."""
    out = MaterialList()
    try:
        text = Path(path).read_text(encoding="latin-1")
    except OSError:
        return out
    for ln in text_records(text):
        m = re.match(r"^\s*(\d+)\s+(.+?)\s*$", ln)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    out.formulas, out.densities = read_compositions(Path(path).parent / "pdfiles" / "pdcompos.pen")
    return out


def find_material_list(*start: str | Path | None) -> Path | None:
    """Busca material-list.txt de la instalación de PENELOPE/penhan del usuario.

    Mira en las carpetas dadas y en las superiores (hasta 5 niveles), en
    ``ProgramasBase/penhan/database``, ``penhan/database`` y ``database``, y
    también junto a la carpeta de ARGUS.
    """
    here = Path(__file__).resolve()
    candidates: list[Path] = [Path(s) for s in start if s]
    candidates.append(here.parents[3])  # carpeta de ARGUS (ejecutado desde el código fuente)
    seen = set()
    for base in candidates:
        base = base if base.is_dir() else base.parent
        for d in [base, *list(base.parents)[:5]]:
            if d in seen:
                continue
            seen.add(d)
            for sub in ("ProgramasBase/penhan/database", "penhan/database", "database"):
                p = d / sub / "material-list.txt"
                if p.is_file():
                    return p
    return None


def match_material(name: str, mlist: list[tuple[int, str]]) -> tuple[int, str] | None:
    """Material de la lista de PENELOPE que corresponde a un nombre: «... (PENELOPE 278)», su nombre en la lista o
    su nombre en español (:mod:`argus.penhan.material_names`)."""
    m = re.search(r"\(PENELOPE\s+(\d+)\)", name)
    if m:
        k = int(m.group(1))
        for item in mlist:
            if item[0] == k:
                return item
    key = name.strip().lower()
    for item in mlist:
        if item[1].lower() == key:
            return item
    from .material_names import match_spanish

    return match_spanish(name, mlist)


def list_label(item: tuple[int, str]) -> str:
    return f"{item[1]} (PENELOPE {item[0]})"


# ---------------------------------------------------------------------------
# Tabla de materiales del caso y materiales.txt
# ---------------------------------------------------------------------------

@dataclass
class MatProblem:
    message: str
    code: int | None = None  # IRETRN
    error: bool = True  # False: solo aviso (depende de datos que ARGUS no conoce)

    def __iter__(self):  # permite «for msg, code in row.problems»
        return iter((self.message, self.code))


def hadron_data_needed(doc: InDocument | None, database: str | os.PathLike | None = None,
                       extra_dirs: list | tuple = ()) -> bool | None:
    """¿Leerá penhmain los datos de hadrones y los ENDF de los .mat (PHINIT)?

    True con protones o alfas (SKPAR 4 o 5); False con electrones, fotones o
    positrones. Con una fuente radiactiva, según el .nuc (revisión de 0.16): penhmain
    llama a PHINIT si EHMAX, la energía de sus alfas más energéticas (por 1,01, como
    SOURCE0), pasa de 100 keV; None si no se encuentra el .nuc (en la carpeta del .in,
    en ``extra_dirs`` o en ``database/nucleide-files``) o no se entiende.

    Con espacios de fases (IPSFN) penhmain no lee la fuente: KPARP se queda en 1
    (penhmain.f, «GO TO 1702») y PHINIT no se llama, aunque el fichero tenga
    protones: False.
    """
    if doc is None:
        return True
    if doc.model.source.psf:
        return False
    kp = doc.model.source.kparp
    if kp > 3:
        return True
    if kp == 0:
        return nuclide_heavy(doc, database, extra_dirs)
    return False


def no_endf_reason(doc: InDocument | None) -> str:
    """Por qué penhmain no leerá los ficheros ENDF (con :func:`hadron_data_needed` False), para los textos."""
    src = doc.model.source if doc is not None else None
    if src is not None and src.psf:
        return ("Con una fuente de espacio de fases penhmain no inicializa los datos de hadrones (PENHAN): no lee los "
                "ficheros ENDF")
    if src is not None and src.kparp == 0:
        return ("El nucleido de la fuente no emite alfas de más de 100 keV: penhmain no inicializa los datos de "
                "hadrones ni lee los ficheros ENDF")
    return "Con electrones, fotones o positrones penhmain no lee los ficheros ENDF"


def nuclide_heavy(doc: InDocument, database: str | os.PathLike | None = None,
                  extra_dirs: list | tuple = ()) -> bool | None:
    """¿El nucleido de la fuente radiactiva emite alfas de más de 100 keV (1,01·EHMAX > 100 keV: penhmain llama a
    PHINIT y lee los ENDF)? None si no se encuentra su .nuc (en la carpeta del .in, en ``extra_dirs`` o en
    ``database/nucleide-files``; sin ``database``, la que se encuentre desde la carpeta del .in) o no se entiende."""
    from .simcheck import nuclide_energies

    name = (doc.model.source.nuclide or "").strip()
    if not name:
        return None
    dirs: list[Path] = []
    if doc.path:
        dirs.append(Path(doc.path).parent)
    dirs += [Path(d) for d in extra_dirs if d]
    if not database and doc.path:
        from .simfolder import find_database

        database = find_database(Path(doc.path).parent)  # la de penhan que se encuentra subiendo desde el caso
    if database:
        dirs.append(Path(database) / "nucleide-files")
    for d in dirs:
        p = d / name.replace("\\", "/")
        if p.is_file():
            _ep, eh, why = nuclide_energies(p)
            if why or eh is None:
                return None
            return eh > 1.0e5
    return None


@dataclass
class MaterialRow:
    number: int
    fname: str = ""  # '' si no hay línea MFNAME para este número
    description: str = ""
    line: InLine | None = None
    exists: bool = False
    header: MatHeader | None = None
    bodies: list[int] = field(default_factory=list)  # cuerpos de la geometría con este material
    used: bool = False  # lo usa algún cuerpo de la geometría
    nuclear: MatNuclear | None = None
    missing_endf: list[str] = field(default_factory=list)  # ficheros ENDF que no están en la carpeta
    problems: list[MatProblem] = field(default_factory=list)
    tissue: object | None = None  # tejido de un TAC importado (argus.ct.calibration.Tissue), si lo es
    stale: bool = False  # el .mat existe pero no es el que dice el .in (otra densidad del TAC, otra composición)
    stale_kind: str = ""  # por qué: «receta» (otra composición), «tac» (otra importación), «lista» (otro n.º)
    recipe: Composition | None = None  # composición propia (opción 1 de penhmat), de los comentarios del .in
    recipe_error: str | None = None  # la composición propia del .in no se entiende

    @property
    def declared(self) -> bool:
        return self.line is not None

    @property
    def display_name(self) -> str:
        if self.description:
            return self.description
        if self.header is not None:
            return self.header.name + (f" (PENELOPE {self.header.penelope_id})" if self.header.penelope_id else "")
        return ""

    @property
    def status(self) -> str:
        if not self.declared:
            return "sin línea MFNAME"
        if self.exists:
            return "con problemas" if any(p.error for p in self.problems) else "existe"
        return "falta"


def materials_table(doc: InDocument | None, geo=None, folder: str | Path | None = None,
                    database: str | os.PathLike | None = None) -> list[MaterialRow]:
    """Una fila por material: los de las líneas MFNAME y los que usa la geometría. ``database``: la de penhan (con
    una fuente radiactiva, su .nuc dice si hacen falta los datos de hadrones; si no se da, la que se encuentre desde la
    carpeta del .in)."""
    rows: dict[int, MaterialRow] = {}
    folder = Path(folder) if folder is not None else None
    hadrons = hadron_data_needed(doc, database)
    if doc is not None:
        found = recipes(doc)
        for i, ln in enumerate(doc.find("MFNAME"), start=1):
            r = MaterialRow(i, ln.text.strip(), material_description(ln), ln)
            rec = _match_recipe(found, r.fname) if r.fname else None
            if rec is not None:
                r.recipe, r.recipe_error = rec.composition, rec.error
                if rec.error:
                    r.problems.append(MatProblem(f"su composición en el .in («[ARGUS {rec.fname}: …]») no se entiende: "
                                                 f"{rec.error}.", None, False))
            if folder is not None and r.fname:
                p = folder / r.fname
                r.exists = p.is_file()
                if r.exists:
                    r.header = read_mat_header(p)
                    _check_mat(r, p, folder, hadrons)
                    if r.recipe is not None:
                        _check_recipe(r, p)
                    else:
                        _check_listed(r)
            rows[i] = r
    if geo is not None and getattr(geo, "ok", False):
        for b in geo.bodies:
            if b.origin == "enclosure" or b.material <= 0 or b.material == 9999:
                continue
            r = rows.setdefault(b.material, MaterialRow(b.material))
            r.used = True
            r.bodies.append(b.index)
        ct = getattr(geo, "ct", None)
        if ct is not None:  # materiales de los vóxeles de la caja CT
            for m in ct.materials():
                if m > 0:
                    r = rows.setdefault(int(m), MaterialRow(int(m)))
                    r.used = True
                    if geo.ct_body is not None and geo.ct_body not in r.bodies:
                        r.bodies.append(geo.ct_body)
            from ..ct.importer import sidecar_tissues

            card = _ct_card(geo)  # composición de los tejidos (ficha de la importación)
            entries = [(n, t, False) for n, t in sidecar_tissues(card).items()]
            entries += [(n, t, True) for n, t in sidecar_tissues(card, reused=True).items()]  # materiales del .in
            for n, t, reused in entries:
                r = rows.get(n)
                if r is None or r.tissue is not None or not _is_ct_tissue(r, t, reused):
                    continue
                r.tissue = t
                if r.recipe is None:  # una composición propia manda sobre la de la importación
                    _check_ct_density(r, t)
    return [rows[k] for k in sorted(rows)]


def _is_ct_tissue(r: MaterialRow, t, reused: bool) -> bool:
    """¿Es el material ``r`` del .in el tejido ``t`` de la ficha del TAC (se genera y se comprueba como él)?

    Un material que el .in ya tenía y que el TAC reutiliza («Material del .in», o un .mat con el mismo nombre que
    una línea MFNAME) sigue siendo el del .in: el agua de un maniquí, el aire de la sala... Salvo que el .in diga
    que es ese mismo tejido de un TAC, con su .mat («Tejido blando (TAC)», ct_blando.mat: se ha vuelto a importar
    sobre un .in con los materiales de otra importación). Y un material que el .in dice que es de la lista de
    PENELOPE («Air, dry (PENELOPE 104)») es ese, diga lo que diga la ficha (las anteriores a 0.16.3 no distinguen
    los materiales reutilizados).
    """
    if reused:
        written = description_comment(f"{t.name} (TAC)")[1:-1]  # como lo escribe ARGUS en la línea MFNAME
        return (r.description.strip().casefold() == written.casefold()
                and r.fname.strip().casefold() == t.mat_file.strip().casefold())
    return listed_number(r.description) is None


def unidentified(rows: list[MaterialRow], mlist: list[tuple[int, str]]) -> list[MaterialRow]:
    """Los materiales declarados cuyo .mat falta y de los que ARGUS no sabe la composición (ni propia, ni de un TAC,
    ni de la lista de PENELOPE por su nombre): no los puede generar hasta que se les asigne uno (0.16.0)."""
    out = []
    for r in rows:
        if not r.declared or r.exists or r.recipe is not None or r.recipe_error or r.tissue is not None:
            continue
        if known_number(r.description, mlist) is not None:  # su número está en el nombre (aunque no haya lista)
            continue
        if r.description and match_material(r.description, mlist) is not None:
            continue
        out.append(r)
    return out


_LISTED = re.compile(r"\(PENELOPE\s+(\d+)\)")


def listed_number(description: str) -> int | None:
    """El número de la lista de PENELOPE de un nombre elegido de ella («Agua líquida (PENELOPE 278)» → 278)."""
    m = _LISTED.search(description or "")
    return int(m.group(1)) if m else None


def known_number(description: str, mlist: list[tuple[int, str]] | None) -> int | None:
    """El número de la lista de PENELOPE que dice un nombre (:func:`listed_number`) si ARGUS puede usarlo: está en la
    lista o no hay lista (penhmat no la necesita). Con la lista y un número que no está en ella, None: ARGUS no sabe qué
    material es (hay que asignarle uno)."""
    k = listed_number(description)
    if k is None or not mlist:
        return k
    return k if any(n == k for n, _name in mlist) else None


def _check_listed(r: MaterialRow) -> None:
    """Un .mat que ya estaba y no es el material de la lista de PENELOPE que dice el .in (de otro material con el
    mismo nombre de fichero: «oxido.mat» es el de doce óxidos): penhmain usaría ese."""
    n = listed_number(r.description)
    if n is None or r.header is None or r.header.penelope_id == n:
        return
    what = f"el n.º {r.header.penelope_id} ({r.header.name})" if r.header.penelope_id else \
        f"otro material ({r.header.name}, de composición propia)"
    r.stale, r.stale_kind = True, "lista"
    r.problems.append(MatProblem(f"es {what} y el .in dice el n.º {n} de la lista de PENELOPE: penhmain usaría ese. "
                                 "Para generar el que dice el .in, cambie en la tabla el nombre del fichero (ARGUS no "
                                 "sustituye ficheros) y use «Generar .mat y ENDF…».", None, False))


def _header_text(r: MaterialRow) -> str:
    h = r.header
    if h is None:
        return "otro material"
    return f"el n.º {h.penelope_id} ({h.name})" if h.penelope_id else f"otro material ({h.name})"


def stale_state(r: MaterialRow) -> str:
    """El estado de un .mat que ya estaba y no es el que dice el .in (para materiales.txt)."""
    if r.stale_kind == "lista":
        return f"existe, pero es {_header_text(r)}: hay que generarlo con otro nombre"
    if r.stale_kind == "receta" or (not r.stale_kind and r.recipe is not None):
        return "existe, pero no tiene la composición del .in: hay que generarlo de nuevo"
    return "existe, pero es de otra importación del TAC: hay que generarlo de nuevo"


def stale_reason(r: MaterialRow) -> str:
    """Por qué ARGUS no genera un .mat que ya estaba y no es el que dice el .in («Generar .mat y ENDF…»)."""
    if r.stale_kind == "lista":
        return (f"ya hay un fichero con ese nombre y es {_header_text(r)}, no el material del .in: ARGUS no lo "
                "sustituye; cambie en la tabla el nombre del fichero (o borre el que hay) y vuelva a generarlo")
    if r.stale_kind == "receta" or (not r.stale_kind and r.recipe is not None):
        return ("ya hay un fichero con ese nombre y no tiene la composición del .in: ARGUS no lo sustituye; cambie en "
                "la tabla el nombre del fichero (o borre el que hay) y vuelva a generarlo")
    return ("ya hay un fichero con ese nombre, de otra importación del TAC (otra densidad): ARGUS no lo sustituye; "
            "bórrelo o cámbielo de nombre y vuelva a generarlo")


def _match_recipe(found: dict[str, Recipe], fname: str) -> Recipe | None:
    if fname in found:
        return found[fname]
    low = fname.lower()
    return next((rec for key, rec in found.items() if key.lower() == low), None)


def _check_recipe(r: MaterialRow, path: Path) -> None:
    """Un .mat que ya estaba y no es el de la composición propia del .in (otros elementos, densidad o I): penhmain
    usaría ese, no el que dice el .in."""
    mc = read_mat_composition(path)
    if mc is None or r.recipe is None:
        return
    diffs = r.recipe.differences(mc)
    if diffs:
        r.stale, r.stale_kind = True, "receta"
        r.problems.append(MatProblem(f"no tiene la composición que dice el .in ({', '.join(diffs)}): penhmain usaría "
                                     "ese. Para generarlo de nuevo, cambie en la tabla el nombre del fichero (o borre "
                                     "el que hay: ARGUS no sustituye ficheros) y use «Generar .mat y ENDF…».",
                                     None, False))


def _check_ct_density(r: MaterialRow, t) -> None:
    """Un .mat de un tejido del TAC que ya estaba (de otra importación en la misma carpeta) tiene la densidad de
    aquella: con los nombres de siempre (ct_blando.mat...) se tomaría por bueno y penhmain usaría otra densidad."""
    from ..ct.calibration import penhmat_composition

    if not r.exists or r.header is None or not r.header.density or not penhmat_composition(t):
        return  # con la opción 2 de penhmat la densidad es la de la lista de PENELOPE
    want = float(f"{(t.voxel_density or t.density):.6g}")
    if want > 0 and abs(r.header.density - want) > 1e-4 * want:
        r.stale, r.stale_kind = True, "tac"
        r.problems.append(MatProblem(f"su densidad ({r.header.density:.6g} g/cm³) no es la de este TAC "
                                     f"({want:.6g} g/cm³): parece de otra importación; genérelo de nuevo con "
                                     "penhmat (los pasos están en materiales.txt).", None, False))


def _ct_card(geo) -> dict | None:
    """La ficha ``<fichero>.ct.json`` del TAC de la geometría (None si no la hay)."""
    path = getattr(geo, "ct_path", None)
    if path is None:
        return None
    from ..ct.importer import read_sidecar

    return read_sidecar(path)


def ct_tissues(geo) -> dict:
    """Material -> tejido del TAC importado con ARGUS (de la ficha ``<fichero>.ct.json``), si la hay. No salen los
    materiales que el .in ya tenía y que el TAC reutiliza: siguen siendo los del .in."""
    from ..ct.importer import sidecar_tissues

    return sidecar_tissues(_ct_card(geo))


def _check_mat(r: MaterialRow, path: Path, folder: Path, hadrons: bool | None = True) -> None:
    """Lo que haría fallar a penhmain al leer este .mat (PEINIT y, con hadrones, PHINIT)."""
    if r.header is None:
        r.problems.append(MatProblem("no es un fichero de material de PENELOPE (falta la cabecera «Material data "
                                     "file»)."))
        return
    nuc = read_mat_nuclear(path)
    r.nuclear = nuc
    if hadrons is False:
        return  # electrones, fotones o positrones, o espacio de fases: penhmain no lee los datos de hadrones
    error = hadrons is True
    note = "" if error else " (solo importa si el nucleido emite partículas pesadas de más de 100 keV)"
    if nuc is None or nuc.nisot is None:
        r.problems.append(MatProblem("no tiene datos de hadrones ni la lista final de ficheros ENDF: está "
                                     "incompleto o no se generó con penhmat" + note + ".", None, error))
        return
    if not nuc.uses_endf:
        return  # reacciones nucleares absorbentes (NISOT < 0) o sin ellas (0): no hacen falta ficheros ENDF
    if not nuc.complete:
        r.problems.append(MatProblem(f"la lista de ficheros ENDF está incompleta ({len(nuc.isotopes)} de "
                                     f"{abs(nuc.nisot)} isótopos): el fichero está truncado" + note + ".",
                                     None, error))
    if abs(nuc.nisot) > MNIS:
        r.problems.append(MatProblem(f"tiene {abs(nuc.nisot)} isótopos con datos ENDF y penhan admite {MNIS} "
                                     "(MNIS): penhmain falla al leerlo; hay que quitar de la lista final los de "
                                     "menor abundancia" + note + ".", None, error))
    r.missing_endf = [f for f, _, _ in nuc.isotopes if not (folder / f).is_file()]
    if r.missing_endf:
        n = len(r.missing_endf)
        r.problems.append(MatProblem(f"{'falta' if n == 1 else 'faltan'} en la carpeta {plural(n, 'fichero')} ENDF "
                                     "que cita: "
                                     f"{', '.join(r.missing_endf[:6])}" + (" …" if n > 6 else "")
                                     + " (el botón «Generar .mat y ENDF…» del panel Materiales los copia de la "
                                     "carpeta de los ENDF: Opciones → Programas de penhan)" + note + ".", 40028,
                                     error))


def distinct_isotopes(rows: list[MaterialRow]) -> set[int]:
    """Isótopos distintos (IZA) con datos ENDF entre todos los materiales declarados."""
    out: set[int] = set()
    for r in rows:
        if r.declared and r.nuclear is not None and r.nuclear.uses_endf:
            out.update(iza for _, iza, _ in r.nuclear.isotopes)
    return out


def _bodies_text(geo, kbs: list[int], limit: int = 6) -> str:
    if geo is None or not kbs:
        return "—"
    names = []
    for kb in kbs[:limit]:
        lab = geo.balias(kb).strip()
        names.append(f"«{lab}»" if lab else f"n.º {kb}")
    more = f" y {len(kbs) - limit} más" if len(kbs) > limit else ""
    return ", ".join(names) + more


def _cttrack_paragraph(geo, cttrack_fixed: bool) -> str:
    """Lo que hay que saber de la densidad de los .mat del TAC (penhmain 2025, CTTRACK)."""
    if cttrack_fixed:
        return ("La densidad de cada .mat es la media de sus vóxeles; con CTTRACK corregido en penhmain no cambia los "
                "alcances.")
    from .simcheck import cttrack_state_text

    ok, text = cttrack_state_text(geo)
    head = ("La densidad de cada .mat es la media de sus vóxeles: penhmain (2025) resta la pérdida continua de "
            "energía de las partículas cargadas dentro del CT con la densidad del .mat y no con la de cada vóxel "
            "(CTTRACK; ADR-010 y ADR-011 de ARGUS).")
    return head + text + (" La corrección completa hay que hacerla en penhmain." if ok else " O corrija penhmain.")


def _paragraph(text: str, width: int = 95, indent: str = "") -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent,
                         break_long_words=False, break_on_hyphens=False)


def materials_txt(rows: list[MaterialRow], in_name: str, geo=None, geo_name: str | None = None,
                  now: _dt.datetime | None = None, hadrons: bool | None = True, cttrack_fixed: bool = False,
                  mlist: list[tuple[int, str]] | None = None, no_endf: str | None = None) -> str:
    """Texto de materiales.txt: qué fichero .mat necesita cada material y cuáles faltan.

    ``hadrons``: si penhmain leerá los datos de hadrones (ver :func:`hadron_data_needed`); si no, ``no_endf`` dice
    por qué (:func:`no_endf_reason`).
    ``cttrack_fixed``: el penhmain del usuario tiene corregido CTTRACK (ADR-011).
    ``mlist``: la lista de materiales de PENELOPE (para dar el número de los que tienen su nombre).
    """
    now = now or _dt.datetime.now()
    out = [f"{TXT_MARK} de {in_name}  (ARGUS {__version__}, {now:%Y-%m-%d %H:%M})"]
    if geo_name:
        out.append(f"Geometría: {geo_name}")
    out += [
        "",
        "penhmain lee el material N de la geometría (MATERIAL(N) en el .geo) del fichero de la",
        "N-ésima línea MFNAME del .in, en la carpeta donde se ejecuta.",
        "",
        "Cómo generar un .mat que falta: ejecutar penhmat en penhan/database (lee ./pdfiles,",
        "./pdhan y ./ENDF_data), con la opción 2 y el número del material en la lista de PENELOPE",
        "(el número entre paréntesis; database/material-list.txt) o con la opción 1 dando la",
        "composición, y guardar con el nombre de la tabla. Después copiar a la carpeta de trabajo",
        "el .mat y los ficheros ENDF que aparecen al final del .mat (están en database/ENDF_data).",
        "ARGUS lo hace solo con el penhmat del usuario (Opciones → Programas de penhan; panel Materiales →",
        "«Generar .mat y ENDF…») para los materiales de la lista de PENELOPE, los que tienen una",
        "composición propia (panel Materiales, menú contextual → «Composición…») y los tejidos de un TAC.",
        f"penhan admite como mucho {MNIS} isótopos con datos ENDF entre todos los materiales.",
        "",
    ]
    names = [r.display_name or "(sin nombre)" for r in rows]
    bodies = [_bodies_text(geo, r.bodies) for r in rows]
    # Columnas tan anchas como el texto más largo (los nombres en español pasan a veces de 40 caracteres).
    wf = max([20] + [len(r.fname or "—") for r in rows])
    wn = max([40] + [len(n) for n in names])
    wb = max([28] + [len(b) for b in bodies])
    head = f"{'N.º':>3}  {'Fichero .mat':<{wf}}  {'Material':<{wn}}  {'Cuerpos':<{wb}}  Estado"
    out.append(head)
    out.append("-" * len(head))
    missing, undeclared, problems = [], [], []
    for r, name, where in zip(rows, names, bodies, strict=True):
        if not r.declared:
            state = "FALTA la línea MFNAME en el .in"
            undeclared.append(r)
        elif r.stale:
            state = stale_state(r)
            missing.append(r)
            problems.append(r)
        elif r.exists:
            state = "existe, CON PROBLEMAS (ver abajo)" if any(x.error for x in r.problems) else "existe"
            if r.header is not None:
                state += f" ({r.header.name}"
                state += f", {r.header.density:.4g} g/cm³)" if r.header.density else ")"
            if r.nuclear is not None and r.nuclear.nisot is not None:
                state += f", {abs(r.nuclear.nisot)} isótopos ENDF"
            if r.problems:
                problems.append(r)
        else:
            state = "FALTA: hay que generarlo"
            missing.append(r)
        if r.declared and not r.used and geo is not None:
            state += "; no lo usa ningún cuerpo"
        fname = r.fname or "—"
        out.append(f"{r.number:>3}  {fname:<{wf}}  {name:<{wn}}  {where:<{wb}}  {state}")
    out.append("")
    if missing:
        out.append("Ficheros .mat que hay que generar:")
        for r in missing:
            out.append(f"  - {r.fname}" + (f"  ({r.display_name})" if r.display_name else ""))
        out.append("")
        from ..ct.calibration import penhmat_steps

        own = [r for r in missing if r.recipe is not None]
        if own:
            out.append("Materiales con composición propia: respuestas a penhmat (opción 1), en el orden en que")
            out.append("pregunta (la composición está en el .in, en las líneas «[ARGUS fichero.mat: …]»).")
            for r in own:
                out.append(f"  {r.fname}  ({r.description or r.fname}: {r.recipe.summary()})")
                for step in r.recipe.penhmat_steps(r.fname, r.description or r.fname):
                    out.append(f"      {step}")
            out.append("")
        listed = []
        for r in missing:
            if r.recipe is None and r.tissue is None and r.description:
                item = match_material(r.description, mlist or [])
                if item is None and (k := known_number(r.description, mlist)) is not None:
                    item = (k, "")  # sin la lista: el número está en el nombre
                if item is not None:
                    listed.append((r, item))
        if listed:
            out.append("Materiales de la lista de PENELOPE: penhmat con la opción 2 y el número del material;")
            out.append("después, 2 (no cambiar Fcb y Wcb), el nombre del fichero e Intro.")
            for r, (k, name) in listed:
                out.append(f"  {r.fname}  (n.º {k}" + (f": {name})" if name else ")"))
            out.append("")

        tissues = [r for r in missing if r.tissue is not None and r.recipe is None]
        if tissues:
            from ..ct.calibration import penhmat_composition

            out.append("Materiales del TAC: respuestas a penhmat para cada uno, en el orden en que pregunta")
            out.append("(composición de la importación; véase la ficha .ct.json junto al .ct).")
            out += _paragraph(
                "La energía media de excitación sigue la regla de ARGUS, la misma para cualquier composición "
                "(ADR-011): en sólidos y líquidos, la aditividad de Bragg con los valores atómicos de fase "
                "condensada de ICRU 37 y el agua en 78 eV (ICRU 90); en los gases y los elementos, la que calcula "
                "PENELOPE.")
            out += _paragraph(_cttrack_paragraph(geo, cttrack_fixed))
            ct_rows = [r for r in rows if r.tissue is not None and r.declared]
            if hadrons is not False and ct_rows:
                from ..ct.subdivide import PHINIT_S_PER_ELEMENT

                n_el = sum(len(r.tissue.elements()) or 1 for r in ct_rows)
                minutes = max(1, round(n_el * PHINIT_S_PER_ELEMENT / 60))
                out += _paragraph(
                    "Con protones o alfas, penhmain prepara cada material al empezar cada simulación: unos "
                    f"{round(PHINIT_S_PER_ELEMENT)} s de CPU por elemento; con los {len(ct_rows)} materiales del "
                    f"TAC, unos {minutes} minutos antes de la primera lluvia. Para pruebas rápidas se puede importar "
                    "el TAC sin partir por densidad o con menos tejidos.")
            for r in tissues:
                t = r.tissue
                out.append(f"  {r.fname}  ({t.name}; {t.source})")
                if t.penelope_id is not None and not penhmat_composition(t):
                    note = "      (Con la opción 2 la densidad es la de la lista de PENELOPE"
                    if t.voxel_density:
                        rho = f"{t.voxel_density:.4g}".replace(".", ",")
                        note += f"; la media de sus vóxeles es {rho} g/cm³"
                    out.append(note + ".)")
                for step in penhmat_steps(t, r.fname, density=t.voxel_density):
                    out.append(f"      {step}")
            out.append("")
    if problems:
        out.append("Ficheros .mat con problemas (penhmain se detendría o podría detenerse):")
        for r in problems:
            for prob in r.problems:
                out.append(f"  - {r.fname}: {prob.message}" + (f" [IRETRN {prob.code}]" if prob.code else ""))
        out.append("")
    if hadrons is False:
        if no_endf:
            out += textwrap.wrap(f"{no_endf} de los .mat; basta con los .mat de PENELOPE.", 100)
        else:
            out.append("Con electrones, fotones o positrones (o con una fuente de espacio de fases) penhmain no lee")
            out.append("los datos de hadrones ni los ficheros ENDF de los .mat: basta con los .mat de PENELOPE.")
        out.append("")
    iso = distinct_isotopes(rows)
    if hadrons is not False and len(iso) > MNIS:
        out.append(f"Entre todos los materiales hay {len(iso)} isótopos distintos con datos ENDF y penhan admite "
                   f"{MNIS} (MNIS): penhmain se detendría con IRETRN 40029.")
        out.append("")
    if undeclared:
        nums = ", ".join(str(r.number) for r in undeclared)
        out.append(f"La geometría usa los materiales {nums}, que no tienen línea MFNAME en el .in "
                   "(penhmain se detendría con IRETRN 3054).")
        out.append("")
    if len(rows) and max(r.number for r in rows) > MAXMAT:
        out.append(f"penhmain admite como mucho {MAXMAT} materiales (MAXMAT).")
        out.append("")
    if not (missing or problems or undeclared or len(iso) > MNIS):
        out.append("Están todos los ficheros de material y no se ha encontrado ningún problema.")
    return "\n".join(line.rstrip() for line in out).rstrip() + "\n"


def write_materials_txt(folder: str | Path, text: str) -> Path:
    """Escribe materiales.txt en la carpeta del .in.

    Si ya hay un materiales.txt que no ha escrito ARGUS, no se toca y se
    escribe materiales_ARGUS.txt.
    """
    folder = Path(folder)
    target = folder / MATERIALS_TXT
    if target.is_file():
        try:
            first = target.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:1]
        except OSError:
            first = []
        if not first or not first[0].startswith(TXT_MARK):
            target = folder / "materiales_ARGUS.txt"
    # UTF-8 con BOM para que el Bloc de notas de Windows muestre bien las tildes.
    target.write_text(text, encoding="utf-8-sig", newline="\r\n")
    return target
