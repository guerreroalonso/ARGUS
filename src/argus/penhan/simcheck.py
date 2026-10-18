"""Comprobaciones de penhmain sobre las secciones de simulación del .in.

Reproduce, con los mismos códigos IRETRN, lo que hace la subrutina PMRDR de
penhmain.f 2025 con los materiales, las listas de cuerpos, los campos, la
reducción de varianza, las distribuciones de partículas emergentes, los
detectores, la rejilla de dosis, el proceso externo y el trabajo; y avisa de
lo que penhmain acepta pero cambia sin decir nada (valores que recorta,
líneas que no usa, ficheros que crea vacíos...).

La fuente y los ficheros de material se comprueban en ``validate``; el
espectro, con ``sections.analyze_spectrum``.
"""

from __future__ import annotations

from pathlib import Path

from ..core.diagnostics import DiagnosticList
from ..core.numfmt import fmt_count, fmt_energy, fmt_num
from . import sections as sc
from .dumpfile import read_dump, source_differences, tally_differences
from .inputfile import InDocument, resolve_body_ref
from .listdirected import ListReadError, read_list, read_pairs

LAYOUT_TITLE = "Title of the job, up to 65 characters."
NBEM, NBTHM, NBPHM = 1000, 900, 180
ICOL_OK = {1: (3, 4, 5), 2: (2, 3, 4), 3: (3, 4, 5, 6), 4: (2, 3, 5), 5: (2, 3, 5)}
ICOL_CODE = {1: 3060, 2: 3061, 3: 3062, 4: 3063, 5: 3063}
ICOL_NAMES = {
    1: {3: "colisiones inelásticas", 4: "bremsstrahlung", 5: "ionización de capas internas"},
    2: {2: "Compton", 3: "absorción fotoeléctrica", 4: "creación de pares"},
    3: {3: "colisiones inelásticas", 4: "bremsstrahlung", 5: "ionización de capas internas",
        6: "aniquilación"},
    4: {2: "colisiones elásticas", 3: "colisiones inelásticas", 5: "ionización de capas internas"},
    5: {2: "colisiones elásticas", 3: "colisiones inelásticas", 5: "ionización de capas internas"},
}
PARTICLE = {1: "electrones", 2: "fotones", 3: "positrones", 4: "protones", 5: "alfas", 6: "neutrones"}


def _count(n: float) -> str:
    return fmt_count(n)


def _fe(v: float) -> str:
    return fmt_energy(v)


# ---------------------------------------------------------------------------
# Energía máxima de las partículas (EPMAX, EHMAX de PMRDR)
# ---------------------------------------------------------------------------

def _source_e0(src) -> float | None:
    """Energía de las partículas primarias (E0 de PMRDR): la mayor del espectro o SENERG (None: nucleido o PSF)."""
    if src.psf or src.radioactive:
        return None
    if src.spectrum:
        return max(e for e, _ in src.spectrum)
    if src.lines:
        return max(e for e, _ in src.lines)
    return src.energy


def max_particle_energy(doc: InDocument, folder: str | Path | None = None) -> float | None:
    """MAX(EPMAX, EHMAX) de PMRDR: el límite de las ventanas de energía de los detectores con EU < 50 eV.

    Espacio de fases: la línea EPMAX (1 GeV si falta o si alguno de sus valores no pasa de 1 eV). Nucleido: lo
    que calculan PENNUC0 y SOURCE0 con el .nuc de la carpeta (None si no se puede leer). Positrones:
    1,21·(E0 + 512 keV), por los fotones de aniquilación. Si no, la energía de la fuente.
    """
    src = doc.model.source
    if src.psf:
        ln = doc.first("EPMAX ")
        ep, eh = (float(ln.values[0]), float(ln.values[1])) if ln is not None and len(ln.values) >= 2 \
            else (1.0e9, 1.0e9)
        if not (ep > 1.0 and eh > 1.0):
            ep = eh = 1.0e9
        return max(ep, eh)
    if src.radioactive:
        if not src.nuclide or folder is None:
            return None
        return nuclide_max_energy(Path(folder) / src.nuclide)
    e0 = _source_e0(src)
    if src.kparp > 3:
        return e0  # EHMAX = E0 y EPMAX = EHMAX
    if src.kparp == 3:
        return max(1.21 * (e0 + 5.12e5), 0.0)  # EHMAX = 0
    return max(e0, 0.0)


# Límites de PENNUC_mod (NDM, NBM, NLM, NTM): con más, PENNUC0 falla (IRETRN 3120).
PMAP_NDIM = 500  # puntos de la tabla de sección eficaz de EPROF (PMAP0)
MAXMAT = 10  # materiales de penhmain
_NUC_LIMITS = (3, 200, 100, 100)


def nuclide_max_energy(path: str | Path) -> float | None:
    """MAX(EPMAX, EHMAX) de una fuente radiactiva, como PENNUC0 y SOURCE0 con el fichero .nuc (None si no se lee
    o si PENNUC0 no lo aceptaría)."""
    return read_nuclide(path)[0]


def read_nuclide(path: str | Path) -> tuple[float | None, str | None]:
    """(MAX(EPMAX, EHMAX), por qué PENNUC0 no lo aceptaría) de una fuente radiactiva con el fichero .nuc
    (:func:`nuclide_energies`)."""
    epmax, ehmax, why = nuclide_energies(path)
    if why or epmax is None or ehmax is None:
        return None, why
    return max(epmax, ehmax), None


def nuclide_energies(path: str | Path) -> tuple[float | None, float | None, str | None]:
    """(EPMAX, EHMAX, por qué PENNUC0 no lo aceptaría) de una fuente radiactiva con el fichero .nuc, como quedan
    tras SOURCE0 (por 1,01). EHMAX es la energía de las alfas más energéticas: con más de 100 keV, penhmain inicializa
    los datos de hadrones (PHINIT) y lee los ficheros ENDF de los .mat.

    PENNUC0 empieza con 250 keV (rayos X y electrones Auger) y toma la energía de las ramas β- (EPMAX) y α
    (EHMAX), 1,21·(E + 512 keV) en las β+ y la de las transiciones de los niveles (un nivel sin transiciones
    cuenta con su energía); SOURCE0 multiplica las dos por 1,01. Registros como los lee READSKP: código de 3
    letras, un blanco y los datos en formato libre («;» vale como «,»; se saltan los «COM»); energías en keV.
    Con más hijas, ramas, niveles o transiciones de las que admite PENNUC_mod (NDM, NBM, NLM, NTM), PENNUC0
    falla (IRETRN 3120).
    """
    try:
        text = Path(path).read_bytes().decode("latin-1")
    except OSError as exc:
        return None, None, f"no se puede leer ({exc.strerror or exc})"
    records = iter(text.split("\n"))

    def record(kinds: list[str]) -> tuple[str, list]:
        for raw in records:
            raw = raw.rstrip("\r")
            code = raw[:3].ljust(3)
            if code == "COM":
                continue
            vals, _ = read_list(raw[4:104].replace(";", ","), kinds, ["" if k == "str" else 0 for k in kinds])
            return code, vals
        raise ListReadError("el fichero se acaba antes de tiempo")

    ndm, nbm, nlm, ntm = _NUC_LIMITS
    try:
        record(["str"])  # nucleido
        record(["int", "int"])  # A, Z
        ndaugh = record(["int"])[1][0]
        if ndaugh > ndm:
            return None, None, f"tiene {ndaugh} hijas y PENNUC admite {ndm} (NDM)"
        epmax, ehmax = 2.5e5, 0.0
        for _ in range(ndaugh):
            record(["str"])  # hija
            _, _, nlevel, nbranch = record(["real", "real", "int", "int"])[1]
            if nbranch > nbm:
                return None, None, f"una hija tiene {nbranch} ramas y PENNUC admite {nbm} (NBM)"
            if nlevel > nlm:
                return None, None, f"una hija tiene {nlevel} niveles y PENNUC admite {nlm} (NLM)"
            record(["real", "real"])  # Q
            for _ in range(nbranch):
                code, vals = record(["real", "real", "int", "real", "real", "int"])
                e = vals[3] * 1.0e3
                if code == "ALP":
                    ehmax = max(ehmax, e)
                elif code == "BEM":
                    epmax = max(epmax, e)
                elif code == "BEP":
                    epmax = max(epmax, 1.21 * (e + 5.12e5))
            for _ in range(nlevel):
                elevel, _, ntrans = record(["real", "real", "int", "real", "real"])[1][:3]
                if ntrans > ntm:
                    return None, None, f"un nivel tiene {ntrans} transiciones y PENNUC admite {ntm} (NTM)"
                if ntrans < 1:
                    epmax = max(epmax, elevel * 1.0e3)
                for _ in range(ntrans):
                    epmax = max(epmax, record(["real", "real", "real", "real", "int"])[1][2] * 1.0e3)
    except (ListReadError, ValueError, TypeError) as exc:
        return None, None, f"no se entiende ({exc})"
    return 1.01 * epmax, 1.01 * ehmax, None


# ---------------------------------------------------------------------------
# Referencias a cuerpos con 0 o con una etiqueta en blanco
# ---------------------------------------------------------------------------

def is_blank_ref(label: str | None, kb: int | None) -> bool:
    """¿La referencia es el número 0 o una etiqueta en blanco? (PMRDR busca entonces un cuerpo sin etiqueta.)"""
    return not label.strip() if label is not None else not kb


def _unlabelled_kind(geo, kb: int) -> str:
    origin = geo.body(kb).origin
    if origin == "enclosure":
        return "la envolvente que añade PENGEOM"
    if origin == "clone":
        return "una copia de un clon"
    return "un cuerpo sin etiqueta"


def blank_ref_note(geo, head: str, kb: int) -> tuple[bool, str]:
    """(¿aviso?, mensaje) para una referencia con 0 o con una etiqueta en blanco que PMRDR aplica al cuerpo ``kb``.

    Es un aviso si hay varios cuerpos sin etiqueta: penhmain toma el último y es fácil que no sea el que se quería.
    """
    n = sum(1 for i in range(1, geo.nbodys + 1) if not geo.balias(i).strip())
    kind = _unlabelled_kind(geo, kb)
    if n <= 1:
        return False, f"{head}: penhmain lo aplica al cuerpo {kb} ({kind}), el único sin etiqueta."
    return True, (f"{head}: con 0 o una etiqueta en blanco penhmain busca un cuerpo sin etiqueta y toma el último, "
                  f"el {kb} ({kind}); hay {n} sin etiqueta (copias de clones, la envolvente, cuerpos de un "
                  "INCLUDE sin «*»), así que puede que no sea el que se quería: escriba el número del cuerpo.")


def blank_ref_why(label: str | None, kb: int | None, *, blank_search: bool = True) -> str | None:
    """Por qué una referencia con 0 o con una etiqueta en blanco no nombra ningún cuerpo (None si no es de esas)."""
    if not is_blank_ref(label, kb):
        return None
    if not blank_search:
        return ("con 0 o una etiqueta en blanco los detectores de impacto no buscan un cuerpo (tampoco uno sin "
                "etiqueta): escriba el número del cuerpo")
    return "con 0 o una etiqueta en blanco penhmain busca un cuerpo sin etiqueta y la geometría no tiene ninguno"


class _Ctx:
    def __init__(self, doc: InDocument, geo, folder: Path | None, d: DiagnosticList, cttrack_fixed: bool = False):
        self.doc = doc
        self.cttrack_fixed = cttrack_fixed  # el usuario ha corregido CTTRACK en su penhmain (ADR-011)
        self.geo = geo if geo is not None and getattr(geo, "ok", False) else None
        self.folder = folder
        self.d = d
        src = doc.model.source
        # Con ficheros de espacio de fases PMRDR no usa SKPAR: KPARP se queda en 1.
        self.kparp = 1 if src.psf else src.kparp
        self.emax = _source_e0(src)  # energía de las partículas primarias (None: radiactiva o espacio de fases)
        self.epmax = max_particle_energy(doc, folder)  # MAX(EPMAX, EHMAX) de PMRDR (None: no se sabe)

    def loc(self, line) -> dict:
        return {"file": self.doc.path, "line": self.doc.row(line)}

    def err(self, msg, line, code=None, reason=None):
        self.d.error(msg, code=code, fortran_reason=reason, **self.loc(line))

    def warn(self, msg, line, code=None):
        self.d.warning(msg, code=code, **self.loc(line))

    def info(self, msg, line):
        self.d.info(msg, **self.loc(line))

    # -- cuerpos ---------------------------------------------------------------------
    def resolve(self, head: str, label: str | None, kb: int | None, line, *, blank_search: bool = True) -> int:
        """Cuerpo al que PMRDR aplica la referencia (0 si ninguno; negativo: un material).

        Con 0 o una etiqueta en blanco, dice a qué cuerpo sin etiqueta la aplica penhmain (``head``: «DSMAX 0»...).
        ``blank_search=False`` en IDBODY, que no busca cuerpos sin etiqueta.
        """
        found = resolve_body_ref(self.geo, label, kb, blank_search=blank_search)
        if found > 0 and blank_search and is_blank_ref(label, kb):
            warn, msg = blank_ref_note(self.geo, head, found)
            (self.warn if warn else self.info)(msg, line)
        return found

    def material(self, kb: int) -> int:
        return self.geo.body(kb).material if 1 <= kb <= self.geo.nbodys else 0

    def name(self, label: str | None, kb: int | None, found: int = 0) -> str:
        """La referencia como está en el .in; con 0 o una etiqueta en blanco, también el cuerpo que es (``found``)."""
        text = (f"'{label.strip()}'" if label.strip() else f"'{label}'") if label is not None else str(kb)
        return text + (f" (→ cuerpo {found})" if found > 0 and is_blank_ref(label, kb) else "")


def check_sections(doc: InDocument, geo=None, folder: str | Path | None = None,
                   d: DiagnosticList | None = None, cttrack_fixed: bool = False) -> DiagnosticList:
    """Comprobaciones de PMRDR posteriores a la fuente (materiales, cuerpos, detectores, dosis, trabajo).

    ``cttrack_fixed``: el penhmain del usuario tiene corregido CTTRACK (no se avisa de la densidad de los .mat
    del TAC).
    """
    d = d if d is not None else DiagnosticList()
    if not doc.model.ok:
        return d
    c = _Ctx(doc, geo, Path(folder) if folder is not None else None, d, cttrack_fixed)
    _title(c)
    _materials(c)
    fields = _local_lists(c)
    _ct(c, fields)
    forced = _variance_reduction(c, fields)
    _emerging(c)
    _impact(c)
    _angular(c)
    _energy(c, forced)
    grid_ok = _grid(c)
    _external(c, grid_ok)
    _job(c)
    _psf_input(c)
    return d


# ---------------------------------------------------------------------------

def _title(c: _Ctx) -> None:
    ln = c.doc.first("TITLE ")
    if ln is not None and ln.raw is not None:
        full = ln.raw.rstrip("\r\n")[7:].rstrip()
        if len(full) > 65:
            c.info(f"El título tiene {len(full)} caracteres y penhmain solo usa los 65 primeros "
                   "(lo que se compara con el de un volcado al reanudar).", ln)


def _materials(c: _Ctx) -> None:
    from .materials import _material_groups

    doc = c.doc
    groups = _material_groups(doc)
    hadrons = c.kparp == 0 or c.kparp > 3
    absorbed = []
    for n, group in enumerate(groups, start=1):
        par = sc.material_params(doc, n)
        lines = {doc.lines[i].kw.strip(): doc.lines[i] for i in group[1:]}
        a = par["MSIMPA"] if par["MSIMPA"] is not None else list(sc.default("MSIMPA"))
        la = lines.get("MSIMPA", doc.lines[group[0]])
        eabs = [max(float(v), 50.0) for v in a[:3]]
        if par["MSIMPA"] is not None:
            low = [i for i in range(3) if float(a[i]) < 50.0]
            if low:
                c.warn(f"Material {n}: energías de absorción por debajo de 50 eV "
                       f"({', '.join(('e−', 'γ', 'e+')[i] for i in low)}): penhmain usa 50 eV.", la)
            for i, key in ((3, "C1"), (4, "C2")):
                if not 0.0 <= float(a[i]) <= 0.2:
                    c.warn(f"Material {n}: {key} = {fmt_num(float(a[i]), 6)} está fuera de [0, 0,2]: penhmain usa "
                           f"{fmt_num(min(max(float(a[i]), 0.0), 0.2), 6)}.", la)
            if float(a[5]) < 0:
                c.warn(f"Material {n}: WCC negativo: penhmain usa 0.", la)
            if 0.0 <= float(a[6]) < 10.0:
                c.warn(f"Material {n}: WCR = {fmt_num(float(a[6]), 6)} eV: penhmain usa 10 eV.", la)
        elif not lines.get("MSIMPA"):
            c.info(f"Material {n}: sin línea MSIMPA, penhmain usa energías de absorción de 50 eV y C1 = C2 = 0,05 "
                   "(simulación muy lenta con partículas rápidas).", doc.lines[group[0]])
        if 1 <= c.kparp <= 3 and c.emax is not None and eabs[c.kparp - 1] >= c.emax:
            absorbed.append((n, eabs[c.kparp - 1], la))
        if hadrons:
            h = par["MSIMPH"] if par["MSIMPH"] is not None else list(sc.default("MSIMPH"))
            lh = lines.get("MSIMPH", la)
            eabsh = [max(float(v), 1.0e5) for v in h[:3]]
            if par["MSIMPH"] is not None:
                low = [i for i in range(3) if float(h[i]) < 1.0e5]
                if low:
                    c.warn(f"Material {n}: energías de absorción de {', '.join(('protones', 'alfas', 'neutrones')[i] for i in low)} "  # noqa: E501
                           "por debajo de 100 keV: penhmain usa 100 keV.", lh)
                for i, key in ((3, "C1H"), (4, "C2H")):
                    if not 0.0 <= float(h[i]) <= 0.2:
                        c.warn(f"Material {n}: {key} = {fmt_num(float(h[i]), 6)} está fuera de [0, 0,2]: penhmain usa "
                               f"{fmt_num(min(max(float(h[i]), 0.0), 0.2), 6)}.", lh)
                if float(h[5]) < 0:
                    c.warn(f"Material {n}: WCCH negativo: penhmain usa 0.", lh)
            if c.kparp in (4, 5) and c.emax is not None and eabsh[c.kparp - 4] >= c.emax:
                absorbed.append((n, eabsh[c.kparp - 4], lh))
            nn = par["MSIMPN"] if par["MSIMPN"] is not None else list(sc.default("MSIMPN"))
            if float(nn[1]) == 0.0:
                c.err(f"Material {n}: FORCEN = 0 (penhmain divide por él).", lines.get("MSIMPN", la))
    if absorbed:
        used = set(c.geo.materials()) if c.geo is not None and hasattr(c.geo, "materials") else None
        who = PARTICLE.get(c.kparp, "")
        for n, e, ln in absorbed:
            if used is not None and n not in used:
                continue
            c.warn(f"Material {n}: la energía de absorción de {who} ({_fe(e)}) no es menor que la energía de la "
                   f"fuente ({_fe(c.emax)}): las partículas primarias se absorben en cuanto entran en él.", ln)


def _local_lists(c: _Ctx) -> dict[int, list]:
    """DSMAX, EABSB, EABSBH y campos. Devuelve {cuerpo: [líneas de campo]}."""
    doc = c.doc
    fields: dict[int, list] = {}
    if c.geo is None:
        return fields
    codes = {"DSMAX": 3055, "EABSB": 3056, "EABSBH": 3057}
    for key, code in codes.items():
        for e in sc.body_list(doc, key):
            kb = c.resolve(f"{key} {c.name(e.label, e.kb)}", e.label, e.kb, e.line)
            what = c.name(e.label, e.kb, kb)
            if kb < 1 or kb > c.geo.nbodys:
                why = blank_ref_why(e.label, e.kb) or "el cuerpo no existe en la geometría"
                c.err(f"{key} {what}: {why}.", e.line, code, "Incorrect body number.")
                continue
            mat = c.material(kb)
            if key == "DSMAX" and mat >= 9999:
                c.info(f"DSMAX {what}: es la caja CT y penhmain no lo aplica (solo a cuerpos con material de 1 a "
                       "9998): en el CT no hay límite de paso.", e.line)
            elif mat == 0:
                c.info(f"{key} {what}: el cuerpo es vacío y penhmain ignora la línea.", e.line)
            elif key == "DSMAX" and float(e.values[0]) < 1.0e-7:
                c.info(f"DSMAX {what}: un valor menor que 1e-7 cm significa «sin límite» (penhmain pone 1e20).",
                       e.line)
            elif key == "EABSB":
                base = sc.material_params(doc, mat)["MSIMPA"] or list(sc.default("MSIMPA"))
                low = [i for i in range(3) if float(e.values[i]) < max(float(base[i]), 50.0)]
                if len(low) == 3:
                    c.info(f"EABSB {what}: ninguna energía supera la del material {mat}; penhmain usa la mayor de "
                           "las dos, así que la línea no cambia nada.", e.line)
            elif key == "EABSBH":
                base = sc.material_params(doc, mat)["MSIMPH"] or list(sc.default("MSIMPH"))
                low = [i for i in range(3) if float(e.values[i]) < max(float(base[i]), 1.0e5)]
                if len(low) == 3:
                    c.info(f"EABSBH {what}: ninguna energía supera la del material {mat}; la línea no cambia nada.",
                           e.line)
    for key in ("EFIELD", "BFIELD"):
        for e in sc.body_list(doc, key):
            kb = c.resolve(f"{key} {c.name(e.label, e.kb)}", e.label, e.kb, e.line)
            what = c.name(e.label, e.kb, kb)
            if kb < 1:
                why = blank_ref_why(e.label, e.kb) or "no hay ningún cuerpo con esa etiqueta"
                c.err(f"{key} {what}: {why} (penhmain no lo comprueba y escribiría el campo fuera de sus tablas).",
                      e.line)
                continue
            if kb > c.geo.nbodys:
                c.warn(f"{key} {what}: el cuerpo no existe; penhmain ignora el campo.", e.line)
                continue
            fields.setdefault(kb, []).append(e.line)
            if c.material(kb) == 0:
                c.err(f"{key} {what}: campo en un cuerpo vacío. penhmain no transporta con campo en el vacío: "
                      "rellénelo con un material de densidad muy baja.", e.line, 3058, "EM field in empty volume.")
            elif not any(float(v) for v in e.values):
                c.info(f"{key} {what}: todas las componentes son cero.", e.line)
    return fields


def _ct(c: _Ctx, fields: dict[int, list]) -> None:
    """Lo que penhmain no admite con una caja CT (vóxeles) en la geometría."""
    geo = c.geo
    if geo is None or getattr(geo, "ct", None) is None or geo.ct_body is None:
        return
    kb = geo.ct_body
    ct = geo.ct
    if ct.has_void and kb in fields:
        c.err("Hay un campo electromagnético en la caja CT y el CT tiene vóxeles vacíos (material 0): penhmain "
              "se detendría al entrar una partícula en uno de ellos. Asigne un material (aire) a esos vóxeles.",
              fields[kb][0], 3145, "CTTRACK: Unexpected void voxel with EM fields.")
    for i, det in enumerate(sc.impact_detectors(c.doc), start=1):
        for b in det.bodies:
            if resolve_body_ref(geo, b.label, b.kb, blank_search=False) == kb:
                c.info(f"Detector de impacto {i} con la caja CT: cuenta las partículas que entran en la caja; dentro "
                       "del CT penhmain no calcula la fluencia (IDCUT = 2).", b.line)
    psf = c.doc.first("IPSFN ")
    if psf is not None:
        c.info("Con un CT, las partículas del espacio de fases tienen que empezar fuera de la caja CT "
               "(si no, penhmain se detiene con IRETRN 3118).", psf)
    eprof = c.doc.first("EPROF ")
    if eprof is not None:
        c.info("EPROF: penhmain no registra el proceso dentro de la caja CT (solo fuera de ella).", eprof)
    ct_line = c.doc.first("GEOMFN")
    if ct_line is not None:  # todas las fuentes acaban en partículas cargadas dentro del CT
        c.info("Dentro del CT, la energía de absorción es la menor de todos los materiales (MSIMPA/MSIMPH no se "
               "aplican; se puede subir con EABSB/EABSBH de la caja CT).", ct_line)
        if not c.cttrack_fixed:
            ok, text = cttrack_text(geo)
            (c.info if ok else c.warn)(text, ct_line)


def cttrack_text(geo) -> tuple[bool, str]:
    """(¿está partido por densidad todo lo que hace falta?, texto) del problema de CTTRACK para el CT de la
    geometría. Con False, el texto es un aviso."""
    head = ("Dentro del CT, penhmain (2025) resta la pérdida continua de energía de las partículas cargadas con la "
            "densidad del .mat de cada material y no con la de cada vóxel (CTTRACK; ADR-010 y ADR-011 de ARGUS).")
    fix = " Para eliminarlo, corrija penhmain y márquelo en Opciones → «Mi penhmain tiene corregido CTTRACK»."
    ok, text = cttrack_state_text(geo)
    return ok, head + text + fix


def cttrack_state_text(geo) -> tuple[bool, str]:
    """(¿está partido todo lo que hace falta?, frase con un espacio delante): cómo están los tejidos del CT de la
    geometría respecto a CTTRACK, según la ficha del TAC (lo usan los mensajes y materiales.txt)."""
    from ..ct.importer import read_sidecar, sidecar_subdivision, split_state, split_summary

    path = getattr(geo, "ct_path", None)
    card = read_sidecar(path) if path is not None else None
    sub = sidecar_subdivision(card)
    st = split_state(sub)
    if st.analysed:
        return st.ok, split_summary(sub)
    if card is None:
        why = ("Este TAC no tiene ficha de ARGUS (.ct.json), así que no se sabe si sus materiales están partidos por "
               "densidad")
    elif st.error:
        why = f"Los tejidos de este TAC no están partidos por densidad (no se pudo: {st.error})"
    elif st.mode == "none":
        why = "Los tejidos de este TAC no están partidos por densidad (se importó con «No partir»)"
    elif st.mode == "penhmain_corregido":
        why = ("Los tejidos de este TAC no están partidos por densidad (se importó con «Mi penhmain tiene corregido "
               "CTTRACK»)")
    else:
        why = "Los tejidos de este TAC no están partidos por densidad (se importó con una versión anterior de ARGUS)"
    return False, (f" {why}. Donde la densidad de un tejido varía mucho, los alcances de los protones pueden "
                   "desviarse varios milímetros (en un tórax, más de 1 cm tras mucho pulmón). Vuelva a importar el "
                   "TAC: ARGUS parte cada tejido por densidad al importarlo.")


def _variance_reduction(c: _Ctx, fields: dict[int, list]) -> bool:
    """IFORCE, IBRSPL, IXRSPL. Devuelve si hay forzado o splitting (incompatible con ENDETC)."""
    doc = c.doc
    geo = c.geo
    forced_bodies: dict[int, set[int]] = {}  # cuerpo -> partículas forzadas
    sure_bodies: dict[int, set[int]] = {}  # las que se fuerzan con un FORCER positivo (con uno negativo, penhmain
    # calcula el factor con la física y puede quedar en 1: sin forzado)
    active = False
    nmatg = geo.nmat if geo is not None else 0
    for e in sc.body_list(doc, "IFORCE"):
        vals = list(e.values) + [1, 3, 1.0, 0.5, 1.5][len(e.values):]
        kp, icol, forcer = int(vals[0]), int(vals[1]), float(vals[2])
        what = c.name(e.label, e.kb)
        if not 1 <= kp <= 5:
            c.err(f"IFORCE {what}: partícula KP = {kp}; solo se pueden forzar interacciones de partículas 1 a 5 "
                  "(no de neutrones).", e.line, 3059, "Incorrect value of KPAR.")
            continue
        if icol not in ICOL_OK[kp]:
            names = ", ".join(f"{k} ({v})" for k, v in ICOL_NAMES[kp].items())
            c.err(f"IFORCE {what}: ICOL = {icol} no se puede forzar para {PARTICLE[kp]}; valores válidos: {names}.",
                  e.line, ICOL_CODE[kp], "Incorrect value of ICOL.")
            if kp in (2, 4, 5):
                continue  # penhmain se detiene aquí; con KP = 1 o 3 sigue leyendo la línea (no hay RETURN)
        if geo is None:
            continue
        kb = c.resolve(f"IFORCE {what}", e.label, e.kb, e.line)
        if kb == 0 or kb > geo.nbodys or kb < -nmatg:
            why = blank_ref_why(e.label, e.kb) or "no existe ese cuerpo" + (f" ni el material {-kb}" if kb < 0 else "")
            c.err(f"IFORCE {what}: {why}.", e.line, 3064, "Incorrect body number.")
            continue
        what = c.name(e.label, e.kb, kb)
        if 0 < forcer <= 1.01:
            c.warn(f"IFORCE {what}: FORCER = {fmt_num(forcer, 6)} no tiene efecto (debe ser mayor que 1).", e.line)
            continue
        if forcer <= 0 and forcer >= -1e-6:
            c.warn(f"IFORCE {what}: FORCER = {fmt_num(forcer, 6)} no tiene efecto.", e.line)
            continue
        ct_kb = getattr(geo, "ct_body", None)
        if kb > 0 and kb == ct_kb and forcer < -1e-6:
            c.err(f"IFORCE {what}: con FORCER negativo penhmain calcula el factor con el material del cuerpo, y la "
                  "caja CT no tiene uno (su MATERIAL es la marca 9999): el resultado no tendría sentido. Dé un FORCER "
                  "positivo.", e.line)
            continue
        if kb > 0 and forcer < -1e-6 and c.material(kb) == 0:
            # PMRDR solo recalcula un FORCER negativo si el material es > 0: queda negativo y no fuerza nada.
            c.warn(f"IFORCE {what}: con FORCER negativo penhmain calcula el factor con el material del cuerpo, y el "
                   "cuerpo es vacío: no lo calcula y el forzado no se activa (la línea no tiene efecto).", e.line)
            continue
        # FORCER > 1,01, o negativo (penhmain lo calcula con la física: casi siempre queda > 1).
        bodies = [kb] if kb > 0 else [b.index for b in geo.bodies if b.material == -kb]
        if kb < 0 and not bodies and getattr(geo, "ct", None) is not None and -kb in geo.ct.materials():
            c.warn(f"IFORCE {what}: el material {-kb} solo está en vóxeles del CT y el forzado por material no se "
                   "aplica dentro del CT: no tiene efecto (use la etiqueta de la caja CT).", e.line)
            continue
        for ib in bodies:
            forced_bodies.setdefault(ib, set()).add(kp)
            if forcer > 0:
                sure_bodies.setdefault(ib, set()).add(kp)
            active = True
            if ib in fields:
                c.err(f"IFORCE {what}: el cuerpo {ib} tiene campo electromagnético; el forzado de interacciones es "
                      "incompatible con los campos.", e.line, 3065 if kb > 0 else 3066,
                      "Variance-red. is incompatible with an EM field.")
                break
    for e in sc.body_list(doc, "IBRSPL"):
        if geo is None:
            break
        kb = c.resolve(f"IBRSPL {c.name(e.label, e.kb)}", e.label, e.kb, e.line)
        what = c.name(e.label, e.kb, kb)
        ibr = int(e.values[0]) if e.values else 1
        if kb == 0 or kb > geo.nbodys or kb < -nmatg:
            why = blank_ref_why(e.label, e.kb) or "no existe ese cuerpo o material"
            c.err(f"IBRSPL {what}: {why}.", e.line, 3067, "Incorrect body number.")
            continue
        if kb > 0:
            if not forced_bodies.get(kb, set()) & {1, 3, 4, 5}:
                c.err(f"IBRSPL {what}: en penhmain el splitting de bremsstrahlung solo actúa donde hay forzado de "
                      "interacciones de partículas cargadas (IFORCE con KP = 1, 3, 4 o 5) en ese cuerpo.", e.line,
                      3068, "Interaction forcing inactive in this body.")
            elif not sure_bodies.get(kb, set()) & {1, 3, 4, 5}:
                c.warn(f"IBRSPL {what}: el forzado de ese cuerpo tiene FORCER negativo y penhmain calcula el factor "
                       "con la física: si queda en 1, no fuerza y se detendría con el error 3068 (el splitting "
                       "necesita forzado). Con un FORCER positivo se sabe de antemano.", e.line)
            elif ibr < 1:
                c.err(f"IBRSPL {what}: el factor de splitting debe ser al menos 1.", e.line, 3069,
                      "Incorrect value of IBRSPL.")
        else:
            mats = [b.index for b in geo.bodies if b.material == -kb]
            if not any(forced_bodies.get(ib, set()) & {1, 3} for ib in mats):
                c.info(f"IBRSPL {what}: ningún cuerpo del material {-kb} tiene forzado de electrones o positrones; "
                       "penhmain ignora la línea.", e.line)
    nxrs = 0
    for e in sc.body_list(doc, "IXRSPL"):
        nxrs += 1
        if geo is None:
            break
        kb = c.resolve(f"IXRSPL {c.name(e.label, e.kb)}", e.label, e.kb, e.line)
        what = c.name(e.label, e.kb, kb)
        ixr = int(e.values[0]) if e.values else 1
        if kb == 0 or kb > geo.nbodys or kb < -nmatg:
            why = blank_ref_why(e.label, e.kb) or "no existe ese cuerpo o material"
            c.err(f"IXRSPL {what}: {why}.", e.line, 3070, "Incorrect body number.")
            continue
        if kb > 0:
            if ixr < 1:
                c.err(f"IXRSPL {what}: el factor de splitting debe ser al menos 1.", e.line, 3071,
                      "Incorrect value of IBRSPL.")
            elif kb in fields:
                c.err(f"IXRSPL {what}: el cuerpo tiene campo electromagnético (incompatible con el splitting).",
                      e.line, 3072, "Variance-red. is incompatible with an EM field.")
        elif ixr > 1:
            for ib in (b.index for b in geo.bodies if b.material == -kb):
                if ib in fields:
                    c.err(f"IXRSPL {what}: el cuerpo {ib} (material {-kb}) tiene campo electromagnético.", e.line,
                          3073, "Variance-red. is incompatible with an EM field.")
                    break
    return active or nxrs > 0


def _emerging(c: _Ctx) -> None:
    doc = c.doc
    ln = doc.first("NBE   ")
    el, eu, nbe = (list(ln.values) if ln is not None else list(sc.default("NBE")))[:3]
    nbe = int(nbe)
    if ln is not None:
        if abs(nbe) > NBEM:
            c.err(f"NBE: {abs(nbe)} intervalos de energía; penhmain admite {NBEM}.", ln, 3121,
                  "HENANG: NBE is too large.")
        elif nbe == 0:
            c.err("NBE: el número de intervalos de energía no puede ser 0.", ln)
        else:
            low = max(float(el), 1.0 if nbe < 0 else 0.0)
            top = _window_top(c, float(eu))
            if top is not None and top[0] <= low:
                c.err(f"NBE: la ventana de energía ({_fe(low)} a {top[1]}) está vacía: el límite superior no es mayor "
                      "que el inferior.", ln)
    ln = doc.first("NBANGL")
    if ln is not None:
        nbth, nbph = int(ln.values[0]), int(ln.values[1])
        if nbth == 0:
            c.err("NBANGL: el número de intervalos de θ no puede ser 0.", ln, 3074, "NBTH equal to 0.")
        elif abs(nbth) > NBTHM:
            c.err(f"NBANGL: {abs(nbth)} intervalos de θ; penhmain admite {NBTHM}.", ln, 3122,
                  "HENANG: NBTH is too large.")
        if nbph < 1:
            c.err("NBANGL: el número de intervalos de φ debe ser al menos 1.", ln, 3075, "Wrong number of PHI bins.")
        elif nbph > NBPHM:
            c.err(f"NBANGL: {nbph} intervalos de φ; penhmain admite {NBPHM}.", ln, 3123, "HENANG: NBPH is too large.")


def _window_top(c: _Ctx, eu: float) -> tuple[float, str] | None:
    """Límite superior de una ventana de energía como PMRDR: EU o, si EU < 50 eV, MAX(EPMAX, EHMAX).

    Devuelve (límite, texto para el mensaje), o None si no se sabe (nucleido sin su .nuc): entonces no se marca.
    """
    if eu >= 50.0:
        return eu, _fe(eu)
    if c.epmax is None:
        return None
    return c.epmax, f"{_fe(c.epmax)}, la energía máxima de las partículas según penhmain"


def _output_problem(c: _Ctx, name: str) -> str | None:
    """Por qué penhmain no podría crear el fichero de salida ``name`` en la carpeta (o None)."""
    if c.folder is None or not name:
        return None
    path = c.folder / name
    if path.is_dir():
        return f"'{name}' es una carpeta"
    if not path.parent.is_dir():
        return f"'{name}' está en una carpeta que no existe"
    return None


def _late_output(c: _Ctx, name: str, line, what: str) -> None:
    """Un fichero que penhmain abre sin comprobarlo al escribir los resultados: si no puede, se detiene con un
    error de ejecución al final de la simulación (y se pierde lo simulado)."""
    why = _output_problem(c, name)
    if why:
        c.warn(f"{what}: {why}; penhmain no podría escribirlo y se detendría con un error al terminar la "
               "simulación.", line)


def _impact(c: _Ctx) -> None:
    dets = sc.impact_detectors(c.doc)
    owner: dict[int, int] = {}
    npsf = 0
    for i, det in enumerate(dets, start=1):
        ln = det.line
        if det.nbe == 0:
            c.err(f"Detector de impacto {i}: el número de intervalos de energía no puede ser 0.", ln, 3076,
                  "Incorrect number of energy bins.")
            continue
        edil = max(det.el, 1.0 if det.nbe < 0 else 0.0)
        top = _window_top(c, det.eu)
        if top is not None and top[0] < edil + 1.0:
            c.err(f"Detector de impacto {i}: la ventana de energía ({_fe(edil)} a {top[1]}) está vacía.", ln, 3077,
                  "Incorrect energy limits.")
        if det.ipsf not in (0, 1):
            c.err(f"Detector de impacto {i}: IPSF = {det.ipsf} (0 sin espacio de fases, 1 con él).", ln, 3078,
                  "Wrong IPSF value.")
        if det.idcut not in (0, 1, 2):
            c.err(f"Detector de impacto {i}: IDCUT = {det.idcut} (0, 1 o 2).", ln, 3079, "Wrong IDCUT value.")
        if det.ipsf == 1:
            npsf += 1
            if npsf > 1:
                c.err("Solo un detector de impacto puede generar un fichero de espacio de fases por simulación.",
                      ln, 3080, "Only one PSF can be generated in a each run.")
            why = _output_problem(c, det.psf or "psf-impdet.dat")
            if why:
                c.err(f"Detector de impacto {i}: el espacio de fases {why}; penhmain no puede abrirlo.",
                      det.lines.get("IDPSF", ln), 3114, "File could not be opened.")
        _late_output(c, det.spc, det.lines.get("IDSPC", ln), f"Detector de impacto {i} (IDSPC)")
        _late_output(c, det.agef, det.lines.get("IDAGEF", ln), f"Detector de impacto {i} (IDAGEF)")
        if abs(det.nbe) > NBEM:
            c.err(f"Detector de impacto {i}: {abs(det.nbe)} intervalos; penhmain admite {NBEM}.", ln, 3126,
                  "SIMDET: NBE is too large.")
        if det.age is not None and det.age[2] != 0:
            agel = max(det.age[0], 1e-20 if det.age[2] < 0 else 0.0)
            if det.age[1] < agel + 1.0e-19:
                c.err(f"Detector de impacto {i}: el intervalo de edades está vacío.", det.lines.get("IDAGEL", ln),
                      3081, "Incorrect age limits.")
            if abs(int(det.age[2])) > NBEM:
                # IMDET0 no lo comprueba (penhmain.f 7736-7765): escribe fuera de su tabla de NBEM intervalos y los
                # resultados del detector salen mal o no salen.
                c.err(f"Detector de impacto {i}: {abs(int(det.age[2]))} intervalos de edad; penhmain admite "
                      f"{NBEM} y, con más, escribe fuera de su tabla (los resultados del detector no servirían).",
                      det.lines.get("IDAGEL", ln))
        if det.flnc:
            c.warn(f"Detector de impacto {i}: penhmain 2025 lee IDFLNC pero no lo usa: el fichero de fluencia "
                   f"(IDCUT = 2) se llama como el de IDPSF o, sin IDPSF, fln-impdet-{i:02d}.dat.",
                   det.lines.get("IDFLNC", ln))
        if det.idcut == 2 and det.ipsf == 1 and det.psf:
            c.err(f"Detector de impacto {i}: con IDCUT = 2 penhmain usa el nombre de IDPSF ({det.psf}) también para "
                  "el fichero de fluencia: el espacio de fases y la fluencia se escribirían en el mismo fichero.",
                  det.lines.get("IDPSF", ln))
        if c.geo is None:
            continue
        if not det.bodies:
            c.err(f"Detector de impacto {i}: no tiene cuerpos (IDBODY).", ln, 3085,
                  "This detector has no active bodies.")
            continue
        for b in det.bodies:
            kb = c.resolve("", b.label, b.kb, b.line, blank_search=False)
            what = c.name(b.label, b.kb)
            if kb < 1 or kb > c.geo.nbodys:
                why = blank_ref_why(b.label, b.kb, blank_search=False)
                c.err(f"Detector de impacto {i}: "
                      + (f"IDBODY {what}: {why}." if why else f"el cuerpo {what} no existe."), b.line, 3082,
                      "Incorrect body label.")
                break
            if kb in owner:
                c.err(f"El cuerpo {what} está en los detectores de impacto {owner[kb]} y {i}: un cuerpo solo puede "
                      "formar parte de uno.", b.line, 3083, "A body cannot be part of two detectors.")
                break
            if c.material(kb) == 0:
                c.err(f"Detector de impacto {i}: el cuerpo {what} es vacío. Los detectores necesitan un material "
                      "(puede ser uno de densidad muy baja).", b.line, 3084,
                      "A void body cannot be part of a detector.")
                break
            owner[kb] = i


def _angular(c: _Ctx) -> None:
    for i, det in enumerate(sc.angular_detectors(c.doc), start=1):
        ln = det.line
        th1, th2, ph1, ph2 = det.theta1, det.theta2, det.phi1, det.phi2
        name = f"Detector angular {i}"
        if th1 > th2:
            c.err(f"{name}: θ1 debe ser menor que θ2.", ln, 3086, "THETA1 must be less than THETA2.")
        elif not 0.0 <= th1 <= 180.0:
            c.err(f"{name}: θ1 debe estar entre 0 y 180°.", ln, 3087, "THETA1 must be in the interval (0,180).")
        elif not 0.0 <= th2 <= 180.0:
            c.err(f"{name}: θ2 debe estar entre 0 y 180°.", ln, 3088, "THETA2 must be in the interval (0,180).")
        elif ph1 > ph2:
            c.err(f"{name}: φ1 debe ser menor que φ2.", ln, 3089, "PHI1 must be less than PHI2.")
        elif ph1 < 0.0 and ph1 < -180.0001:
            c.err(f"{name}: φ1 debe ser mayor que −180°.", ln, 3090, "PHI1 must be larger than -180.")
        elif ph1 < 0.0 and ph2 > 180.0001:
            c.err(f"{name}: con φ1 negativo, φ2 no puede pasar de 180° (los dos en [−180, 180] o en [0, 360]).", ln,
                  3091, "PHI2 must be must than 180.")
        elif ph1 >= 0.0 and ph1 > 360.0001:
            c.err(f"{name}: φ1 debe ser menor que 360°.", ln, 3092, "PHI1 must be less than 360.")
        elif ph1 >= 0.0 and ph2 > 360.0001:
            c.err(f"{name}: φ2 debe ser menor que 360°.", ln, 3093, "PHI2 must be less than 360.")
        _late_output(c, det.spc, det.lines.get("ADSPC", ln), f"{name} (ADSPC)")
        en = det.energy if det.energy is not None else sc.default("ADENER")
        if int(en[2]) == 0:
            c.err(f"{name}: el número de intervalos de energía no puede ser 0.", det.lines.get("ADENER", ln), 3094,
                  "Incorrect number of energy bins.")
        elif abs(int(en[2])) > NBEM:
            c.err(f"{name}: {abs(int(en[2]))} intervalos; penhmain admite {NBEM}.", det.lines.get("ADENER", ln), 3129,
                  "SANDET: NB is too large.")
        else:
            # penhmain no comprueba la ventana de los detectores angulares (penhmain.f 3854-3873): con EU por debajo
            # de EL, el espectro sale vacío sin ningún aviso.
            edal = max(float(en[0]), 50.0 if int(en[2]) < 0 else 0.0)
            top = _window_top(c, float(en[1]))
            if top is not None and top[0] < edal + 1.0:
                c.warn(f"{name}: la ventana de energía ({_fe(edal)} a {top[1]}) está vacía: penhmain no lo comprueba "
                       "y su espectro saldría vacío.", det.lines.get("ADENER", ln))


def _energy(c: _Ctx, forced: bool) -> None:
    dets = sc.energy_detectors(c.doc)
    if dets and forced:
        c.err("Los detectores de energía depositada no se pueden usar con forzado de interacciones ni splitting "
              "(el espectro saldría sesgado).", dets[0].line, 3095,
              "Variance-red. is incompatible with energy-deposition detectors.")
        return
    owner: dict[int, int] = {}
    for i, det in enumerate(dets, start=1):
        ln = det.line
        name = f"Detector de energía {i}"
        if det.nbe == 0:
            c.err(f"{name}: el número de intervalos no puede ser 0.", ln, 3096, "Incorrect number of energy bins.")
            continue
        top = _window_top(c, det.eu)
        edel = max(det.el, 50.0) if det.nbe < 0 else max(det.el, 0.0)
        if top is not None and top[0] < edel + 1.0:
            c.err(f"{name}: la ventana de energía ({_fe(edel)} a {top[1]}) está vacía.", ln, 3097,
                  "Incorrect energy limits.")
        if abs(det.nbe) > NBEM:
            c.err(f"{name}: {abs(det.nbe)} intervalos; penhmain admite {NBEM}.", ln, 3132, "SENDET: NB1 is too large.")
        _late_output(c, det.spc, det.lines.get("EDSPC", ln), f"{name} (EDSPC)")
        if c.geo is None:
            continue
        if not det.bodies:
            c.err(f"{name}: no tiene cuerpos (EDBODY).", ln, 3101, "This detector has no active bodies.")
            continue
        for b in det.bodies:
            kb = c.resolve(f"{name}: EDBODY {c.name(b.label, b.kb)}", b.label, b.kb, b.line)
            what = c.name(b.label, b.kb, kb)
            if kb < 1 or kb > c.geo.nbodys:
                why = blank_ref_why(b.label, b.kb)
                c.err(f"{name}: " + (f"EDBODY {what}: {why}." if why else f"el cuerpo {what} no existe."), b.line,
                      3098, "Incorrect body label.")
                break
            if kb in owner:
                c.err(f"El cuerpo {what} está en los detectores de energía {owner[kb]} y {i}.", b.line, 3099,
                      "A body cannot be part of two detectors.")
                break
            if c.material(kb) == 0:
                c.err(f"{name}: el cuerpo {what} es vacío.", b.line, 3100, "A void body cannot be part of a detector.")
                break
            owner[kb] = i


def _grid(c: _Ctx) -> bool:
    """Rejilla de dosis; devuelve si penhmain la define (LDOSEM)."""
    doc = c.doc
    vals = {k: doc.first(k) for k in ("GRIDX ", "GRIDY ", "GRIDZ ", "GRIDR ")}
    n = {k: (int(ln.values[2]) if k != "GRIDR " else int(ln.values[1])) if ln is not None else 0
         for k, ln in vals.items()}
    nbx, nby, nbz, nbr = n["GRIDX "], n["GRIDY "], n["GRIDZ "], n["GRIDR "]
    ldosem = False
    for k, code, lim in (("GRIDX ", 3102, 101), ("GRIDY ", 3104, 101), ("GRIDZ ", 3106, 401)):
        ln = vals[k]
        if ln is None or n[k] <= 0:
            continue
        lo, hi = float(ln.values[0]), float(ln.values[1])
        if lo > hi:
            c.info(f"{k.strip()}: el límite inferior es mayor que el superior; penhmain los intercambia.", ln)
            lo, hi = hi, lo
        if hi < lo + 1.0e-6:
            c.err(f"{k.strip()}: los dos límites coinciden.", ln, code, f"{k.strip()[-1]}U must be greater than "
                  f"{k.strip()[-1]}L+1.0E-6.")
        if n[k] > lim:
            c.err(f"{k.strip()}: {n[k]} intervalos; penhmain admite {lim}.", ln,
                  {"GRIDX ": 3134, "GRIDY ": 3135, "GRIDZ ": 3136}[k], "SDOSE: too many bins.")
    if nbx > 0:
        ldosem = True
        if nby <= 0:
            c.err("GRIDX sin GRIDY: la caja de dosis necesita GRIDX, GRIDY y GRIDZ.", vals["GRIDX "], 3105,
                  "Incorrect dose box definition.")
        if nbz <= 0:
            c.err("GRIDX sin GRIDZ: la caja de dosis necesita GRIDX, GRIDY y GRIDZ.", vals["GRIDX "], 3107,
                  "Incorrect dose box definition.")
    elif nby > 0:
        c.err("GRIDY sin GRIDX: la caja de dosis necesita GRIDX, GRIDY y GRIDZ.", vals["GRIDY "], 3103,
              "Incorrect dose box definition.")
    if nbz > 0 and nbx <= 0:
        ldosem = True
        if nbr <= 0:
            # Cilindro sin GRIDR: penhmain pasa a DOSE0 los intervalos de GRIDX y GRIDY tal como están.
            if nbx < 0:
                c.err(f"GRIDX: {nbx} intervalos (con GRIDZ y sin GRIDR, penhmain los usa y se detiene).",
                      vals["GRIDX "], 3134, "SDOSE: NBX must be .GT.0. and .LE.NDXM")
            elif nby < 0:
                c.err(f"GRIDY: {nby} intervalos (con GRIDZ y sin GRIDR, penhmain los usa y se detiene).",
                      vals["GRIDY "], 3135, "SDOSE: NBY must be .GT.0. and .LE.NDYM")
            else:
                c.err("GRIDZ sin GRIDR ni GRIDX/GRIDY: para un cilindro hace falta GRIDR (radio); para una caja, "
                      "GRIDX y GRIDY. penhmain no lo comprueba y usaría un radio sin definir.", vals["GRIDZ "])
    if nbr > 0:
        ln = vals["GRIDR "]
        if float(ln.values[0]) < 1.0e-6:
            c.err("GRIDR: el radio debe ser mayor que 1e-6 cm.", ln, 3108, "RU must be greater than 1.0E-6.")
        if nbx > 0 or nby > 0:
            c.err("GRIDR no se puede combinar con GRIDX ni GRIDY (cilindro: GRIDZ + GRIDR; esfera: solo GRIDR).",
                  ln, 3109, "Incorrect dose box definition.")
        if nbr > 101:
            c.err(f"GRIDR: {nbr} intervalos; penhmain admite 101.", ln, 3134, "SDOSE: too many bins.")
        ldosem = True
    return ldosem


def _external(c: _Ctx, grid: bool) -> None:
    ep = sc.external_process(c.doc)
    if ep is None:
        return
    fname, kp, mat = ep
    ln = c.doc.first("EPROF ")
    if not grid:
        c.err("EPROF necesita una rejilla de dosis (GRIDX/GRIDY/GRIDZ o GRIDR): el proceso se registra en ella.",
              ln, 3110, "EPRO requires having defined a dose grid.")
    if not 1 <= kp <= 6:
        c.err(f"EPROPM: partícula {kp} (1 a 6).", c.doc.first("EPROPM") or ln, 3111, "Incorrect EPRO particle.")
    if not 1 <= mat <= MAXMAT:
        c.err(f"EPROPM: material {mat}. penhmain no lo comprueba y, fuera de 1 a {MAXMAT}, escribiría fuera de sus "
              "tablas.", c.doc.first("EPROPM") or ln)
    elif mat > len(c.doc.model.materials):
        c.warn(f"EPROPM: el material {mat} no está en el .in (tiene {len(c.doc.model.materials)}): el proceso no se "
               "registraría en ningún sitio.", c.doc.first("EPROPM") or ln)
    if not grid or not 1 <= kp <= 6:
        return  # penhmain se detiene antes de leer la tabla (PMAP0)
    reason = "PMAP definition file could not be opened."
    if not fname:
        c.err("EPROF: falta el nombre del fichero de sección eficaz; penhmain no puede abrirlo.", ln, 3137, reason)
        return
    if c.folder is None:
        return
    path = c.folder / fname
    if path.is_dir() or not path.parent.is_dir():
        c.err(f"EPROF: '{fname}' " + ("es una carpeta" if path.is_dir() else "está en una carpeta que no existe")
              + "; penhmain no puede abrirlo.", ln, 3137, reason)
    elif not path.is_file():
        c.warn(f"No existe el fichero de sección eficaz '{fname}': penhmain lo crearía vacío y, sin sección eficaz, "
               "registraría la fluencia de las partículas en lugar del proceso.", ln)
    else:
        try:
            npts, csmax = read_pairs(path.read_bytes().decode("latin-1"))
        except OSError:
            return
        if npts > PMAP_NDIM:
            c.err(f"EPROF: la tabla de '{fname}' tiene {npts} puntos y penhmain admite {PMAP_NDIM}.", ln, 3138,
                  "PMAP: too many data points.")
        elif csmax >= 1.0e-75 and npts < 2:
            c.err(f"EPROF: la tabla de '{fname}' tiene {npts} punto{'s' if npts != 1 else ''}: hacen falta al menos "
                  "2 para interpolar.", ln, 3139, "PMAP: too few data points.")


def _job(c: _Ctx) -> None:
    doc = c.doc
    ln = doc.first("DUMPP ")
    if ln is not None and float(ln.values[0]) < 1.0e10:
        v = float(ln.values[0])
        if v < 30.0:
            c.info("DUMPP: el periodo mínimo es 30 s (penhmain usa 30).", ln)
        elif v > 86400.0:
            c.info("DUMPP: el periodo máximo es un día (penhmain usa 86400 s).", ln)
        if doc.first("DUMPTO") is None:
            c.info("DUMPP sin DUMPTO: penhmain escribe el volcado en dump.dmp.", ln)
    ln = doc.first("TIME  ")
    if ln is not None and float(ln.values[0]) < 1.0:
        c.warn(f"TIME = {fmt_num(float(ln.values[0]), 6)}: penhmain lo toma como 100 s (para no limitar el tiempo, "
               "ponga un número grande, p. ej. 1e9).", ln)
    ln = doc.first("NSIMSH")
    if ln is not None and float(ln.values[0]) < 1.0:
        c.warn(f"NSIMSH = {fmt_num(float(ln.values[0]), 6)}: penhmain lo toma como 2e9 partículas.", ln)
    seeds = doc.find("RSEED ")
    if len(seeds) > 1:
        c.info(f"Hay {len(seeds)} líneas RSEED: penhmain usa la última.", seeds[0])
    if seeds:
        ln = seeds[-1]
        s1, s2 = int(ln.values[0]), int(ln.values[1])
        if s1 < 0 and -s1 > 1001:
            c.warn(f"RSEED {s1}: penhmain solo tiene 1001 pares de semillas precalculados; con ese número usa 1, 1.",
                   ln)
        elif s1 == 0 or s2 <= 0:
            c.warn("RSEED: las semillas deben ser enteros positivos (un primero negativo, −N, elige el par "
                   "precalculado N).", ln)
    _resume(c)
    _dumpto(c)


def _resume(c: _Ctx) -> None:
    doc = c.doc
    ln = doc.first("RESUME")
    name = sc.text(doc, "RESUME")
    if ln is None or not name or c.folder is None:
        return
    info = read_dump(c.folder / name)
    if info.state == "missing":
        c.info(f"RESUME: no existe {name}; penhmain lo crea (con el texto «Empty.») y empieza desde cero.", ln)
        return
    if info.state == "empty":
        c.info(f"RESUME: {name} no contiene una simulación guardada ({info.message}); penhmain empieza desde cero.",
               ln)
        return
    if info.state == "corrupt":
        c.err(f"RESUME: {name} no es un volcado de penhmain ({info.message}): penhmain se detendría con un error "
              "de lectura.", ln)
        return
    title = doc.model.title[:65].rstrip()
    if info.title != title:
        c.err(f"RESUME: el volcado {name} es de otra simulación (su título es «{info.title}» y el del .in "
              f"«{title}»): penhmain se detendría.", ln, 3112,
              "The dump file is corrupted (the TITLE does not match).")
        return
    if info.problem == "format":
        c.err(f"RESUME: {name} está dañado. {info.message}", ln, 3113, "The dump file is empty or corrupted.")
        return
    if info.problem == "truncated":
        c.err(f"RESUME: {name} está incompleto. {info.message} penhmain se detendría con un error de lectura "
              "(End of file).", ln)
        return
    dshn = float(sc.value(doc, "NSIMSH")[0]) if doc.first("NSIMSH") is not None else 2.0e9
    if dshn < 1.0:
        dshn = 2.0e9
    msg = (f"RESUME: penhmain continuará la simulación guardada en {name} ({_count(info.showers)} partículas ya "
           "simuladas)")
    if info.showers > dshn - 0.5:
        msg += (f" y, como NSIMSH = {_count(dshn)}, no simulará ninguna más: solo volverá a escribir los mismos "
                "resultados")
    diffs = source_differences(info, doc, c.geo)
    if diffs:
        msg += (". Pero el volcado no se hizo con este .in (" + "; ".join(diffs) + "): penhmain solo compara el "
                "título y mezclaría las dos simulaciones")
        c.warn(msg + ".", ln)
    else:
        c.warn(msg + ".", ln)
    tallies = tally_differences(info, doc)
    if tallies:
        # HENANR, IMDETR, ANDETR, ENDETR y DOSER leen del volcado los intervalos y la rejilla (penhmain.f 4337-4351).
        c.warn(f"RESUME: al reanudar, penhmain toma del volcado las rejillas y los intervalos de los resultados, no "
               f"los del .in, y los de {name} son otros (" + "; ".join(tallies) + "). Los resultados seguirán con los "
               "del volcado; para usar los del .in, empiece sin RESUME.", ln)
    _resume_ct(c, ln, name)
    if title == LAYOUT_TITLE:
        c.info("El título es el de la plantilla: penhmain solo compara el título para saber si un volcado es de "
               "esta simulación, así que conviene uno propio para cada caso.", doc.first("TITLE "))


def _resume_ct(c: _Ctx, ln, name: str) -> None:
    """Con un CT, penhmain toma del volcado la caja (XM..NXYZ) y las densidades de los vóxeles (DC)."""
    geo = c.geo
    if geo is None or geo.ct is None:
        return
    ct_path = geo.ct_path if geo.ct_path is not None else c.folder / (geo.ct_file or "")
    try:
        newer = Path(ct_path).stat().st_mtime > (c.folder / name).stat().st_mtime + 1.0
    except OSError:
        newer = False
    if newer:
        c.warn(f"RESUME con un TAC: penhmain toma del volcado las dimensiones de la caja CT y las densidades de los "
               f"vóxeles, no las de {geo.ct_file}, y {geo.ct_file} es posterior a {name}. Si el TAC ha cambiado "
               "desde entonces (otra importación, otra rejilla u otra calibración), la simulación mezclaría los dos "
               "o fallaría: empiece sin RESUME.", ln)
    else:
        c.info(f"RESUME con un TAC: penhmain usa las densidades de los vóxeles guardadas en {name}, no las de "
               f"{geo.ct_file}.", ln)


def _dumpto(c: _Ctx) -> None:
    doc = c.doc
    ln = doc.first("DUMPTO")
    name = sc.text(doc, "DUMPTO")
    if ln is not None and name:
        why = _output_problem(c, name)
        if why:
            c.warn(f"DUMPTO: {why}; penhmain no podría escribir el volcado y se detendría con un error al hacer el "
                   "primero.", ln)
            return
    if ln is None or not name or c.folder is None or name == sc.text(doc, "RESUME"):
        return
    info = read_dump(c.folder / name)
    if not info.usable:
        return
    diffs = source_differences(info, doc, c.geo)
    title = doc.model.title[:65].rstrip()
    if info.title != title:
        diffs.insert(0, "título")
    if diffs:
        c.info(f"Los resultados que hay en la carpeta ({name}, {_count(info.showers)} partículas) no son de este .in ("
               + "; ".join(diffs) + "); se sustituirán al volver a simular.", ln)


def _psf_input(c: _Ctx) -> None:
    """Ficheros de espacio de fases (IPSFN).

    PMRDR solo abre el primero (con IOSTAT: 3116 si no puede; 3117 si está vacío, y OPEN lo crea vacío si no
    existe). Los demás los abre la simulación al acabar el anterior, sin IOSTAT: uno que no existe se crea vacío
    y se salta; uno en blanco o una carpeta la detienen con un error de ejecución.
    """
    for n, ln in enumerate(c.doc.find("IPSFN "), start=1):
        name = ln.text.strip()
        first = n == 1
        # El primero: IRETRN 3116. Los demás: error de ejecución cuando la simulación llega a ellos.
        cannot = (3116, "File could not be opened.") if first else (None, None)
        later = "" if first else (" (se abre al acabar el fichero anterior: penhmain se detendría entonces con un "
                                  "error de ejecución)")
        if not name:
            c.err("IPSFN con el nombre de fichero en blanco: penhmain no puede abrir el espacio de fases" + later + ".",
                  ln, *cannot)
            continue
        if c.folder is None:
            continue
        path = c.folder / name
        if path.is_dir() or not path.parent.is_dir():
            why = "es una carpeta" if path.is_dir() else "su carpeta no existe"
            c.err(f"No se puede abrir el fichero de espacio de fases '{name}': {why}{later}.", ln, *cannot)
        elif not first and (not path.is_file() or _psf_empty(path)):
            c.err(f"El fichero de espacio de fases '{name}' " + ("no existe" if not path.is_file() else "está vacío")
                  + ": al acabar el anterior, penhmain lo abrirá (creándolo vacío) y lo saltará sin avisar; sus "
                  "partículas no se simularán.", ln)
        elif not path.is_file():
            c.err(f"No existe el fichero de espacio de fases '{name}': penhmain lo crearía vacío y se detendría.",
                  ln, 3117, "The file is empty or corrupted.")
        elif _psf_empty(path):
            c.err(f"El fichero de espacio de fases '{name}' está vacío (quizá lo dejó así una simulación "
                  "anterior que no lo encontró): penhmain se detendría.", ln, 3117, "The file is empty or corrupted.")
        elif _psf_kinds(path) & {4, 5}:
            c.warn(f"El espacio de fases '{name}' tiene protones o alfas, pero con IPSFN penhmain no inicializa "
                   "PENHAN (KPARP se queda en 1 y no llama a PHINIT): no carga sus secciones eficaces ni los "
                   "ENDF, y esas partículas no se simularían bien.", ln)


def _psf_kinds(path, limit: int = 1 << 16) -> set[int]:
    """Tipos de partícula (KPAR, primera columna) de las primeras líneas de un espacio de fases."""
    try:
        with open(path, "rb") as f:
            head = f.read(limit)
    except OSError:
        return set()
    kinds = set()
    for raw in head.split(b"\n")[:-1] if len(head) == limit else head.split(b"\n"):
        t = raw.split(b"#", 1)[0].split()
        if t:
            try:
                kinds.add(int(t[0]))
            except ValueError:
                continue
    return kinds


def _psf_empty(path) -> bool:
    """¿El fichero de espacio de fases no tiene ninguna partícula (vacío o solo comentarios)?"""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 16)
    except OSError:
        return False
    for raw in head.split(b"\n"):
        t = raw.strip()
        if t and not t.startswith(b"#"):
            return False
    return len(head) < (1 << 16)
