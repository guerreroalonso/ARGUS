"""Sesión de trabajo: una geometría abierta con todo lo derivado de ella."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .core.diagnostics import Diagnostic, Severity
from .core.files import FileStamp, file_stamp
from .pengeom.bounds import estimate_bounds
from .pengeom.compiler import CompiledGeometry, compile_geometry
from .pengeom.locate import Locator
from .pengeom.model import CtBoxElement, GeoDocument, IncludeElement
from .pengeom.parinp import parinp_uses, provisional_parinp
from .pengeom.parser import load_geometry
from .pengeom.validate import validate
from .penhan.inputfile import InDocument, parse_in_text
from .penhan.quickscan import InputSummary, inputs_for_geometry, scan_input

EMPTY_HALF = 10.0  # cm: mitad del encuadre de una geometría sin cuerpos


@dataclass
class GeometrySession:
    path: Path
    doc: GeoDocument
    geo: CompiledGeometry
    locator: Locator | None
    bounds: tuple[np.ndarray, np.ndarray]
    parinp: list[float] = field(default_factory=list)
    input_file: InputSummary | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    # True si no hay .in y los valores PARINP son los números escritos en el propio .geo.
    parinp_provisional: bool = False
    # Carpeta de trabajo de penhmain (la del .in) si no es la de la geometría («GEOMFN sub/caso.geo»): PENGEOM
    # busca en ella los INCLUDE y el .ct. None: la carpeta de la geometría.
    work_dir: Path | None = None
    disk_stamp: FileStamp | None = None  # cómo estaba el .geo al leerlo (para ver si otro programa lo cambia)

    @property
    def ok(self) -> bool:
        return self.geo.ok and self.locator is not None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def base_dir(self) -> Path:
        """Carpeta en la que PENGEOM busca los INCLUDE y el .ct: la de trabajo de penhmain."""
        return self.work_dir if self.work_dir is not None else self.path.parent


@dataclass
class CaseFiles:
    """Un .in leído y la geometría a la que apunta (GEOMFN), si se ha podido abrir."""

    input_doc: InDocument
    input_path: Path
    session: GeometrySession | None
    geometry_path: Path | None
    problem: str | None = None  # por qué no hay geometría
    input_stamp: FileStamp | None = None  # cómo estaba el .in al leerlo


def open_case(in_path: str | os.PathLike, geometry: str | os.PathLike | None = None) -> CaseFiles:
    """Lee un .in y abre su geometría con los PARINP del propio .in (como penhmain).

    penhmain abre ``GEOMFN`` en su carpeta de trabajo, que es la del .in, y
    PENGEOM, también allí los INCLUDE y el .ct de la geometría (aunque GEOMFN
    tenga una carpeta). ``geometry``: el fichero de geometría, si ya se sabe
    cuál es (se ha abierto el .geo y se ha elegido este .in).
    """
    p = Path(in_path)
    data = p.read_bytes()
    stamp = file_stamp(p, data)
    doc = parse_in_text(data.decode("latin-1"), str(p))
    quick = scan_input(p)
    name = doc.model.geomfn or quick.geomfn
    parinp = doc.model.parinp_list() if doc.model.geomfn else quick.parinp_list()
    if geometry is not None:
        geo_path = Path(geometry)
    else:
        if not name:
            return CaseFiles(doc, p, None, None, "El .in no indica el fichero de geometría (línea GEOMFN).",
                             input_stamp=stamp)
        geo_path = p.parent / name
        if not geo_path.is_file():
            return CaseFiles(doc, p, None, geo_path,
                             f"No se encuentra la geometría '{name}' en la carpeta del .in ({p.parent}).",
                             input_stamp=stamp)
    session = open_geometry(geo_path, parinp=parinp, input_file=p, work_dir=p.parent)
    return CaseFiles(doc, p, session, geo_path, input_stamp=stamp)


def _same_dir(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _load(p: Path, work_dir: Path | None) -> GeoDocument:
    """``load_geometry`` con los INCLUDE leídos de la carpeta de trabajo (la del .in), como PENGEOM."""
    if work_dir is None:
        return load_geometry(p)
    doc = load_geometry(p, loader=lambda name: (work_dir / name).read_bytes().decode("latin-1"))
    for el in doc.elements:  # los nombres de los ficheros leídos, para los mensajes y el panel de texto
        if isinstance(el, IncludeElement) and el.document is not None:
            old, new = str(p.parent / el.filename), str(work_dir / el.filename)
            el.document.path = new
            for d in doc.diagnostics:
                if d.file == old:
                    d.file = new
    return doc


def open_geometry(path: str | os.PathLike, parinp: list[float] | None = None,
                  input_file: str | os.PathLike | None = None, search_input: bool = True,
                  work_dir: str | os.PathLike | None = None) -> GeometrySession:
    """Lee, compila y prepara una geometría para verla.

    Los valores PARINP salen de ``parinp``, del .in ``input_file`` o, con
    ``search_input``, del primer .in de la carpeta (por orden alfabético) cuyo
    ``GEOMFN`` sea esta geometría (la ventana pregunta cuál si hay varios). Sin
    .in, se usan provisionalmente los números escritos en el propio .geo.
    ``work_dir``: la carpeta de trabajo de penhmain (la del .in), donde se
    buscan los INCLUDE y el .ct; por defecto, la de la geometría.
    """
    p = Path(path)
    wd = Path(work_dir) if work_dir is not None and not _same_dir(work_dir, p.parent) else None
    doc = _load(p, wd)
    stamp = file_stamp(p)
    summary: InputSummary | None = None
    if input_file is not None:
        summary = scan_input(input_file)
    elif parinp is None and search_input:
        found = inputs_for_geometry(p)
        if found:
            summary = found[0]
    if parinp is None and summary is not None:
        parinp = summary.parinp_list()
    provisional = False
    if parinp is None:
        # Sin .in: los números escritos en el propio .geo (también si aún no usa PARINP: al editarla se vuelven a
        # sacar del documento, véase ``geo_controller.parinp_for``).
        parinp = provisional_parinp(doc)
        provisional = True
    session = session_from_document(p, doc, parinp, summary, provisional=provisional, work_dir=wd)
    session.disk_stamp = stamp
    return session


def parse_with_includes(text: str, path: str | os.PathLike, work_dir: str | os.PathLike | None) -> GeoDocument:
    """El texto de una geometría (con sus cambios sin guardar) con los INCLUDE leídos de la carpeta de trabajo
    ``work_dir`` o, sin ella, de la de la geometría; como al abrirla (:func:`_load`)."""
    from .pengeom.parser import load_includes, parse_geo_text

    p = Path(path)
    return load_includes(parse_geo_text(text, str(p)), Path(work_dir) if work_dir is not None else p.parent)


def work_dir_notes(path: Path, doc: GeoDocument, work_dir: Path | None) -> list[Diagnostic]:
    """Aviso si la geometría no está en la carpeta de trabajo de penhmain y tiene INCLUDE o un .ct."""
    if work_dir is None:
        return []
    docs = [doc] + [el.document for el in doc.elements if isinstance(el, IncludeElement) and el.document]
    names = [el.filename for d in docs for el in d.elements if isinstance(el, IncludeElement | CtBoxElement)
             and el.filename]
    if not names:
        return []
    try:
        where = os.path.relpath(path, work_dir).replace(os.sep, "/")
    except ValueError:  # otra unidad (Windows)
        where = str(path)
    return [Diagnostic(Severity.WARNING,
                       f"GEOMFN nombra la geometría en otra carpeta («{where}»): penhmain y PENGEOM buscan sus "
                       "INCLUDE y su .ct (" + ", ".join(dict.fromkeys(names)) + ") en la carpeta de trabajo, la del "
                       f".in ({work_dir}), y no junto a la geometría. ARGUS también los busca allí.", file=doc.path)]


def session_from_document(path: str | os.PathLike, doc: GeoDocument, parinp: list[float] | None = None,
                          input_file: InputSummary | None = None,
                          geo: CompiledGeometry | None = None, provisional: bool = False,
                          bounds=None, quick: bool = False, work_dir: str | os.PathLike | None = None
                          ) -> GeometrySession:
    """Compila un documento ya leído (p. ej. tras editarlo) y prepara la sesión.

    ``provisional``: los ``parinp`` son los números escritos en el .geo (no hay .in).
    ``bounds``: la caja de la escena, si ya se sabe; ``quick``: sin las
    comprobaciones de la geometría (al arrastrar un módulo). ``work_dir``: la
    carpeta de trabajo de penhmain, si no es la de la geometría (véase
    ``GeometrySession.work_dir``).
    """
    p = Path(path)
    wd = Path(work_dir) if work_dir is not None and not _same_dir(work_dir, p.parent) else None
    summary = input_file
    if geo is None or geo.document is not doc:
        geo = compile_geometry(doc, parinp=parinp or [], base_dir=wd or p.parent)
    locator = Locator(geo) if geo.ok else None
    if bounds is not None:
        bounds = (np.asarray(bounds[0], dtype=float), np.asarray(bounds[1], dtype=float))
    elif locator is not None and all(b.origin == "enclosure" for b in geo.bodies):
        bounds = (np.full(3, -EMPTY_HALF), np.full(3, EMPTY_HALF))  # geometría vacía: una escena de 20 cm
    elif locator is not None:
        bounds = estimate_bounds(geo, locator=locator)
    else:
        bounds = (np.array([-1.0, -1.0, -1.0]), np.array([1.0, 1.0, 1.0]))
    diags: list[Diagnostic] = list(doc.diagnostics)
    diags.extend(work_dir_notes(p, doc, wd))
    if provisional:
        diags.extend(provisional_parinp_notes(doc, geo.ok))
    for d in geo.diagnostics:
        if d not in diags:
            diags.append(d)
    if not quick:
        diags.extend(validate(geo))
    return GeometrySession(p, doc, geo, locator, bounds, list(parinp or []), summary, diags,
                           parinp_provisional=provisional, work_dir=wd)


def provisional_parinp_notes(doc: GeoDocument, ok: bool = True) -> list[Diagnostic]:
    """Avisos de una geometría con PARINP abierta sin .in (valores escritos en el .geo)."""
    uses = parinp_uses(doc)
    if not uses:
        return []
    first = min(uses.values(), key=lambda u: (u.file != doc.path, u.line if u.line is not None else 1 << 30))
    values = ", ".join(f"PARINP({i}) = {u.value:g}" for i, u in uses.items())
    out = [Diagnostic(Severity.WARNING,
                      "Sin .in: la geometría toma medidas de PARINP y se muestra con los números escritos en el "
                      f"propio .geo ({values}). penhmain usará los de las líneas PARINP del .in.",
                      file=first.file, line=first.line)]
    for i, u in uses.items():
        if u.others:
            vals = ", ".join(f"{v:g}" for v in [u.value, *u.others])
            out.append(Diagnostic(Severity.INFO,
                                  f"PARINP({i}) está escrito con números distintos en el .geo ({vals}); "
                                  f"sin .in se usa el primero, {u.value:g}.", file=u.file, line=u.line))
    if not ok:
        out.append(Diagnostic(Severity.INFO,
                              "Con los números escritos en el .geo la geometría no se puede construir; "
                              "ábrala con su .in (el que da los valores PARINP).", file=first.file, line=first.line))
    return out
