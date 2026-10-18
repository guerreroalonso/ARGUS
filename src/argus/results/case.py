"""La geometría del caso de una carpeta de resultados.

penhmain numera los cuerpos como PENGEOM (KB). Para poner nombre a los
cuerpos del resumen, calcular la dosis media de cada cuerpo o los histogramas
dosis-volumen, ARGUS abre el .in de la carpeta y su geometría (GEOMFN, con sus
PARINP), igual que al abrir un caso.

La geometría puede haber cambiado después de simular (se edita y se guarda en
la misma carpeta): se compara con el árbol de cuerpos que penhmain escribió al
empezar (``geometry.rep``) y, si no es el mismo, no se usa para los resultados
por cuerpo (sus números ya no serían los de la simulación).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..pengeom.compiler import CompiledGeometry

# PENGEOM escribe el material con I3 (pengeom.f, «Genealogical tree»): uno que no cabe en tres cifras (el 9999 de los
# vóxeles de un TAC) sale como «***».
_TREE = re.compile(r"\*\*\*\s+(BODY|MODULE)\s*=\s*(\d+)\s*,\s*KMOTH\s*=\s*(-?\d+)\s*,\s*MAT\s*=\s*(-?\d+|\*+)")


def _fits_i3(value: int) -> bool:
    """¿Cabe el número en un campo I3 de Fortran (de −99 a 999)?"""
    return -99 <= value <= 999


@dataclass
class CaseGeometry:
    input_path: Path
    session: object | None = None  # argus.session.GeometrySession
    problem: str | None = None
    material_files: list[str] = field(default_factory=list)  # MFNAME del .in: material 1, 2…
    stamp: tuple = ()  # cómo estaban el .in y la geometría al leerlos (para volver a leerlos si cambian)
    geometry_path: Path | None = None
    ct_path: Path | None = None
    report_path: Path | None = None  # geometry.rep de la simulación
    mismatch: str | None = None  # por qué la geometría no es la que simuló penhmain (o None)

    @property
    def geo(self) -> CompiledGeometry | None:
        s = self.session
        return None if s is None else s.geo

    @property
    def ok(self) -> bool:
        g = self.geo
        return g is not None and g.ok

    def body_label(self, kb: int) -> str:
        g = self.geo
        if g is None or not 1 <= kb <= g.nbodys:
            return ""
        return g.balias(kb).strip()

    def body_name(self, kb: int) -> str:
        """Nombre corto del cuerpo: su etiqueta en el .geo (la que se usa en el .in) o su número."""
        g = self.geo
        lab = self.body_label(kb)
        name = lab or f"n.º {kb}"
        if g is not None and 1 <= kb <= g.nbodys:
            b = g.body(kb)
            if b.origin == "enclosure":
                return f"envolvente (n.º {kb})"
            if b.origin == "ct":
                return f"TAC ({name})" if lab else "TAC"
            if b.is_module:
                return f"{name} (módulo)"
        return name

    def body_material(self, kb: int) -> int | None:
        g = self.geo
        if g is None or not 1 <= kb <= g.nbodys:
            return None
        return int(g.body(kb).material)

    def material_name(self, mat: int | None) -> str:
        if mat is None:
            return ""
        if mat == 0:
            return "vacío"
        if mat >= 9999:
            return "vóxeles del TAC"
        if 1 <= mat <= len(self.material_files):
            return f"{mat} · {Path(self.material_files[mat - 1]).stem}"
        return str(mat)


def case_stamp(input_path: str | os.PathLike, geometry_path: str | os.PathLike | None = None,
               ct_path: str | os.PathLike | None = None, report_path: str | os.PathLike | None = None) -> tuple:
    """Fecha y tamaño del .in, de la geometría, de su .ct y del geometry.rep de la simulación: si cambian, hay que
    volver a leer el caso."""
    out = []
    for q in (input_path, geometry_path, ct_path, report_path):
        if q is None:
            continue
        try:
            st = os.stat(q)
        except OSError:
            out.append(None)
            continue
        out.append((st.st_size, st.st_mtime_ns))
    return tuple(out)


def report_tree(text: str) -> list[tuple[int, bool, int, int | None]]:
    """El árbol de cuerpos de un geometry.rep: (KB, es módulo, KMOTH, MAT) de cada cuerpo y módulo. MAT es None si
    PENGEOM escribió «***» (un material que no cabe en tres cifras, como el 9999 del TAC)."""
    return [(int(n), kind == "MODULE", int(km), None if mat.startswith("*") else int(mat))
            for kind, n, km, mat in _TREE.findall(text)]


def _same_material(current: int, reported: int | None) -> bool:
    """¿Es ``current`` el material que escribió PENGEOM? «***» (None) es cualquiera que no quepa en I3."""
    return not _fits_i3(current) if reported is None else current == reported


def geometry_mismatch(geo: CompiledGeometry | None, report: str | os.PathLike) -> str | None:
    """Por qué ``geo`` no es la geometría que simuló penhmain según su geometry.rep (None si es la misma o si no se
    puede saber)."""
    if geo is None:
        return None
    try:
        rows = report_tree(Path(report).read_bytes().decode("latin-1"))
    except OSError:
        return None
    if not rows:
        return None
    if len(rows) != geo.nbodys:
        return (f"penhmain simuló una geometría con {len(rows)} cuerpos y módulos, y la de ahora tiene {geo.nbodys}: "
                "se ha cambiado después de simular")
    for kb, module, mother, mat in rows:
        if not 1 <= kb <= geo.nbodys:
            return "la geometría se ha cambiado después de simular"
        b = geo.body(kb)
        if bool(b.is_module) != module or int(b.mother) != mother or not _same_material(int(b.material), mat):
            what = "módulo" if module else "cuerpo"
            mat_text = mat if mat is not None else "*** (de más de tres cifras)"
            return (f"el {what} n.º {kb} no es el que simuló penhmain (geometry.rep: madre {mother}, material "
                    f"{mat_text}): la geometría se ha cambiado después de simular")
    return None


def load_case_geometry(input_path: str | os.PathLike) -> CaseGeometry:
    """Abre el .in y su geometría (no lanza excepciones: el problema queda en ``problem``) y la compara con la que
    simuló penhmain (geometry.rep de la carpeta del .in)."""
    from ..session import open_case

    p = Path(input_path)
    rep = p.parent / "geometry.rep"
    rep_path = rep if rep.is_file() else None
    try:
        cf = open_case(p)
    except (OSError, ValueError) as exc:
        return CaseGeometry(p, None, f"No se puede leer {p.name}: {exc}", stamp=case_stamp(p, report_path=rep_path),
                            report_path=rep_path)
    mats = [m.fname for m in cf.input_doc.model.materials]
    problem = cf.problem
    if cf.session is not None and not cf.session.geo.ok:
        problem = "La geometría tiene errores (PENGEOM no la aceptaría)."
    ct_path = getattr(cf.session.geo, "ct_path", None) if cf.session is not None else None
    mismatch = geometry_mismatch(cf.session.geo if cf.session is not None else None, rep_path) if rep_path else None
    return CaseGeometry(p, cf.session, problem, mats, stamp=case_stamp(p, cf.geometry_path, ct_path, rep_path),
                        geometry_path=cf.geometry_path, ct_path=ct_path, report_path=rep_path, mismatch=mismatch)


def is_current(case: CaseGeometry) -> bool:
    """¿Siguen el .in, la geometría y el geometry.rep como cuando se leyeron?"""
    rep = case.input_path.parent / "geometry.rep"
    if (case.report_path is None) != (not rep.is_file()):
        return False  # penhmain ha escrito (o se ha borrado) el geometry.rep
    return case_stamp(case.input_path, case.geometry_path, case.ct_path, case.report_path) == case.stamp


__all__ = ["CaseGeometry", "case_stamp", "geometry_mismatch", "is_current", "load_case_geometry", "report_tree"]
