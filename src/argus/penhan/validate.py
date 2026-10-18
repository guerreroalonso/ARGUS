"""Coherencia entre el .in y el .geo: comprobaciones de la subrutina PMRDR.

Tras leer el fichero (PMRDR0), penhmain inicializa PENGEOM y comprueba la
fuente, los materiales y las referencias a cuerpos (PMRDR, penhmain.f 2025).
Aquí se reproducen las comprobaciones que no necesitan los datos físicos de
los materiales, con los mismos códigos IRETRN.
"""

from __future__ import annotations

from pathlib import Path

from ..core.diagnostics import DiagnosticList
from ..pengeom.compiler import CompiledGeometry
from .inputfile import InDocument

NB = 5000  # PENGEOM_mod
PARTICLES = {0: "fuente radiactiva", 1: "electrones", 2: "fotones", 3: "positrones", 4: "protones", 5: "alfas"}


def _loc(doc: InDocument, line) -> dict:
    return {"file": doc.path, "line": doc.row(line)}


def source_energy_max(doc: InDocument) -> float | None:
    """Energía máxima de la fuente (eV), como la calcula PMRDR."""
    src = doc.model.source
    if src.spectrum:
        return max(e for e, _ in src.spectrum)
    if src.lines:
        return max(e for e, _ in src.lines)
    return src.energy


def validate_case(doc: InDocument, geo: CompiledGeometry | None = None,
                  folder: str | Path | None = None, cttrack_fixed: bool = False) -> DiagnosticList:
    """Comprueba el .in frente a la geometría y a la carpeta de trabajo.

    ``cttrack_fixed``: el penhmain del usuario tiene corregido CTTRACK (ADR-011).
    """
    d = DiagnosticList()
    if not doc.model.ok:
        return d
    m = doc.model
    src = m.source
    folder = Path(folder) if folder is not None else (Path(doc.path).parent if doc.path else None)
    geomfn_line = doc.first("GEOMFN")
    _geometry_file(doc, folder, d)
    _file_names(doc, d)
    for n, mat in enumerate(m.materials, start=1):
        if not mat.fname.strip():  # PEINIT: OPEN(IRD,FILE=PMFILE(M),IOSTAT=KODE) falla con un nombre en blanco
            d.error(f"Material {n}: la línea MFNAME tiene el nombre de fichero en blanco: PENELOPE (PEINIT) no puede "
                    "abrir el fichero de material.", code=1006,
                    fortran_reason="The material data file could not be opened", **_loc(doc, mat.line))
    if geo is not None and geo.ok:
        if geo.nmat < 1:
            d.error("La geometría no tiene ningún cuerpo con material (NMATG = 0).", code=3037,
                    fortran_reason="NMATG must be greater than 0.", **_loc(doc, geomfn_line))
        if geo.nmat > len(m.materials):
            d.error(f"La geometría usa materiales hasta el {geo.nmat}, pero el .in solo declara "
                    f"{len(m.materials)} (líneas MFNAME).", code=3054,
                    fortran_reason="Too many different materials.", **_loc(doc, geomfn_line))
    # Fuente. Con ficheros de espacio de fases (IPSFN) penhmain no lee el resto de la fuente.
    if src.psf:
        ln = doc.first("WGTWIN")
        if ln is not None and ln.values and len(ln.values) >= 2:
            wmin, wmax = abs(float(ln.values[0] or 0.0)), min(abs(float(ln.values[1] or 0.0)), 1.0e10)
            if wmin > wmax:
                d.error(f"WGTWIN: la ventana de pesos está al revés ({wmin:g} > {wmax:g}).", code=3039,
                        fortran_reason="Inconsistent window end points.", **_loc(doc, ln))
    if not src.psf:
        if not src.radioactive:
            _check_energy_and_angles(doc, d)
        elif not src.nuclide.strip() and doc.first("SRNUCL") is not None:
            # «SRNUCL ,» o «SRNUCL ''»: PENNUC0 no tiene qué abrir (penhmain.f, SOURCE0).
            d.error("SRNUCL no tiene el nombre del fichero del nucleido: penhmain no puede inicializar la fuente "
                    "radiactiva.", code=3120, fortran_reason="SOURCE0: PENNUC initialisation failed.",
                    **_loc(doc, doc.first("SRNUCL")))
        elif src.nuclide and folder is not None:
            _check_nuclide(doc, folder, d)
        # Posición, caja y cuerpos activos (también para fuentes radiactivas).
        if src.bodies:
            if not src.extended:
                d.error("SBODY exige una caja de fuente (SBOX) con lados distintos de cero.", code=3049,
                        fortran_reason="A extended source box has not been defined.",
                        **_loc(doc, src.bodies[0].line))
            elif geo is not None and geo.ok:
                if _source_bodies(doc, geo, d):
                    _source_box_hits(doc, geo, d)
    # Ficheros de material en la carpeta (ARGUS no los genera: van a materiales.txt).
    if folder is not None:
        from .materials import MNIS, distinct_isotopes, hadron_data_needed, materials_table

        rows = materials_table(doc, None, folder)
        for r in rows:
            if not r.declared or not r.fname:  # en blanco: ya se ha dicho (1006)
                continue
            p = folder / r.fname
            if p.is_dir() or not p.parent.is_dir():
                d.error(f"Material {r.number}: '{r.fname}' " + ("es una carpeta" if p.is_dir() else
                                                                 "está en una carpeta que no existe")
                        + ": PENELOPE (PEINIT) no puede abrir el fichero de material.", code=1006,
                        fortran_reason="The material data file could not be opened", **_loc(doc, r.line))
            elif not r.exists:
                d.info(f"Material {r.number}: falta el fichero '{r.fname}' en la carpeta (hay que generarlo con "
                       "penhmat: si ARGUS conoce su composición, lo hace el botón «Generar .mat y ENDF…» del panel "
                       "Materiales; al guardar el .in se anota en materiales.txt).", **_loc(doc, r.line))
            for prob in r.problems:
                add = d.error if prob.error else d.warning
                add(f"Material {r.number} ({r.fname}): {prob.message}", code=prob.code, **_loc(doc, r.line))
        hadrons = hadron_data_needed(doc)
        iso = distinct_isotopes(rows)
        if hadrons is not False and len(iso) > MNIS:
            add = d.error if hadrons else d.warning
            add(f"Entre todos los materiales hay {len(iso)} isótopos distintos con datos ENDF y penhan admite "
                f"{MNIS} (MNIS).", code=40029, fortran_reason="Too many isotopes.", **_loc(doc, doc.first("MFNAME")))
    # Resto de secciones: parámetros, cuerpos, campos, reducción de varianza, detectores, dosis y trabajo.
    from .simcheck import check_sections

    check_sections(doc, geo, folder, d, cttrack_fixed=cttrack_fixed)
    return d


def _source_bodies(doc: InDocument, geo: CompiledGeometry, d: DiagnosticList) -> bool:
    """Cuerpos activos (SBODY) como los resuelve PMRDR: 0 o una etiqueta en blanco, el último cuerpo sin etiqueta.
    Devuelve si todos existen."""
    from .simcheck import blank_ref_note, blank_ref_why, is_blank_ref

    bodies = doc.model.source.bodies
    kmax = 0
    for ref in bodies:
        kb = ref.resolve(geo)
        what = f"'{ref.label}'" if ref.label is not None else str(ref.kb)
        if kb is None or kb < 1 or kb > NB:
            why = blank_ref_why(ref.label, ref.kb) or "no hay ningún cuerpo con esa etiqueta en la geometría"
            d.error(f"SBODY {what}: {why}.", code=3050, fortran_reason="SBODY: Incorrect body label.",
                    **_loc(doc, ref.line))
            return False
        if is_blank_ref(ref.label, ref.kb) and kb <= geo.nbodys:
            warn, msg = blank_ref_note(geo, f"SBODY {what}", kb)
            (d.warning if warn else d.info)(msg, **_loc(doc, ref.line))
        kmax = max(kmax, kb)
    if kmax > geo.nbodys:
        d.error(f"SBODY: el cuerpo {kmax} no existe (la geometría tiene {geo.nbodys}).", code=3051,
                fortran_reason="Some source bodies are undefined.", **_loc(doc, bodies[0].line))
        return False
    return True


SOURCE_SAMPLES = 20000  # puntos de la caja de la fuente para ver qué parte cae en los cuerpos activos
SOURCE_LOG_LINES = 10000  # avisos de penhmain («the sampling of initial positions may be very inefficient»)


def source_box_fraction(doc: InDocument, geo: CompiledGeometry, n: int = SOURCE_SAMPLES) -> float | None:
    """Fracción de la caja de la fuente (SPOSIT ± SBOX/2) que está en los cuerpos activos (SBODY), con puntos al azar
    y LOCATE, como sortea penhmain la posición inicial (SOURC0/SOURCE). None si no se puede saber."""
    import numpy as np

    from ..pengeom.extent import locator_for

    src = doc.model.source
    active = {ref.resolve(geo) for ref in src.bodies}
    active.discard(None)
    if not active:
        return None
    rng = np.random.default_rng(20261005)
    c = np.asarray(src.position, dtype=np.float64)
    size = np.abs(np.asarray(src.box, dtype=np.float64))
    pts = c + (rng.random((n, 3)) - 0.5) * size
    try:
        kb, _mat = locator_for(geo).locate(pts)
    except Exception:  # noqa: BLE001 - sin LOCATE no se comprueba
        return None
    return float(np.isin(np.asarray(kb, dtype=np.int64), sorted(active)).mean())


def _source_box_hits(doc: InDocument, geo: CompiledGeometry, d: DiagnosticList) -> None:
    """penhmain sortea la posición en la caja hasta que cae en un cuerpo activo, sin límite: si la caja no toca esos
    cuerpos no termina nunca; si toca muy poco, a partir del intento 200 (100 con SRNUCL) escribe un aviso por
    intento (en penhmain.dat y en pantalla) y el registro crece sin medida."""
    import math

    from . import sections as sc

    f = source_box_fraction(doc, geo)
    if f is None:
        return
    where = _loc(doc, doc.first("SBOX") or doc.first("SBODY"))
    if f == 0.0:
        d.error("Ningún punto de la caja de la fuente (SPOSIT y SBOX) cae en los cuerpos activos (SBODY): penhmain "
                "repetiría el sorteo de la posición inicial sin fin (y escribiría un aviso en cada intento). Mueva la "
                "caja sobre esos cuerpos o elija otros.", **where)
        return
    v = sc.value(doc, "NSIMSH")
    showers = float(v[0]) if v and float(v[0]) >= 1.0 else 2.0e9
    # SOURC0 (partículas) avisa a partir del intento 200; SOURCE (desintegraciones de SRNUCL), del 100.
    radio = doc.model.source.radioactive
    limit = 100 if radio else 200
    per_shower = (1.0 - f) ** limit / f  # intentos de más (cada uno, una línea de aviso) por lluvia
    if per_shower * showers > SOURCE_LOG_LINES:
        lines = per_shower * showers
        d.warning(f"Solo un {100 * f:.2g} % de la caja de la fuente (SPOSIT y SBOX) está en los cuerpos activos "
                  f"(SBODY): a partir del intento {limit} de cada {'desintegración' if radio else 'partícula'} "
                  "penhmain escribe el aviso «the sampling of "
                  f"initial positions may be very inefficient», unas {lines:.2g} líneas"
                  + (" (penhmain.dat crecería sin medida)" if not math.isfinite(lines) or lines > 1e7 else "")
                  + ". Ajuste la caja a esos cuerpos (panel Fuente: «Ajustar la caja a los cuerpos activos»). Si ya "
                  "está justo alrededor, es por su forma (una capa fina, como la piel, ocupa poco de su caja): la "
                  "simulación es correcta, pero penhmain escribirá esos avisos.", **where)


def _geometry_file(doc: InDocument, folder: Path | None, d: DiagnosticList) -> None:
    """GEOMFN como lo abre PMRDR: ``OPEN(15,FILE=PFILE,IOSTAT=KODE)``, sin STATUS.

    Un nombre en blanco (o la falta de la línea, si el fichero se acaba tras los materiales), una carpeta o una
    subcarpeta que no existe hacen fallar el OPEN (IRETRN 3036). Si solo falta el fichero, OPEN lo crea vacío y
    GEOMIN se detiene al leerlo (error de ejecución, sin IRETRN).
    """
    name = doc.model.geomfn
    line = doc.first("GEOMFN")
    reason = "Geometry file could not be opened."
    if name.strip() and name[:1].isspace():
        return  # empieza por un blanco: lo dice ``_file_names`` (penhmain no lo encontraría)
    if not name.strip():
        what = ("La línea GEOMFN tiene el nombre del fichero de geometría en blanco" if line is not None else
                "Falta la línea GEOMFN (el fichero de geometría) tras los materiales")
        d.error(f"{what}: penhmain no puede abrir la geometría.", code=3036, fortran_reason=reason,
                **_loc(doc, line if line is not None else (doc.lines[-1] if doc.lines else None)))
        return
    if folder is None:
        return
    path = folder / name
    if path.is_dir():
        d.error(f"GEOMFN '{name}' es una carpeta, no el fichero de geometría: penhmain no puede abrirlo.", code=3036,
                fortran_reason=reason, **_loc(doc, line))
    elif not path.parent.is_dir():
        d.error(f"No existe la carpeta del fichero de geometría '{name}': penhmain no puede abrirlo.", code=3036,
                fortran_reason=reason, **_loc(doc, line))
    elif not path.is_file():
        d.error(f"No existe el fichero de geometría '{name}' en la carpeta del .in: penhmain crearía un fichero vacío "
                "con ese nombre y se detendría con un error de lectura (End of file).", **_loc(doc, line))


def _file_names(doc: InDocument, d: DiagnosticList) -> None:
    """Nombres de fichero del .in que penhmain no encontraría aunque el fichero esté (0.16.3):

    * GEOMFN, MFNAME, IPSFN, EPROF y RESUME se leen con (A20) desde la columna 8 y se abren tal cual: un blanco
      delante es parte del nombre (penhmain crea un fichero vacío con ese nombre y se para; con RESUME no encuentra
      el volcado, empieza desde cero y sobrescribe los resultados anteriores);
    * con tildes o eñes: el .in va en Latin-1 y un penhmain de Linux o de macOS (también con WSL) busca esos bytes,
      pero en el disco el nombre va en UTF-8 (uno de Windows sí lo encuentra: es un aviso).
    """
    from .simfolder import lead_problem

    m = doc.model
    names: list[tuple[str, str, object]] = []  # (palabra clave, nombre tal como lo lee penhmain, línea)
    if m.geomfn.strip():
        names.append(("GEOMFN", m.geomfn, doc.first("GEOMFN")))
    names += [("MFNAME", mat.fname, mat.line) for mat in m.materials if mat.fname.strip()]
    if m.source.psf:
        names += [("IPSFN", ln.text, ln) for ln in doc.find("IPSFN ") if ln.text.strip()]
    names += [("EPROF", ln.text, ln) for ln in doc.find("EPROF ") if ln.text.strip()]
    res = doc.first("RESUME")
    if res is not None and res.text.strip():
        names.append(("RESUME", res.text, res))
    for kw, raw, line in names:
        prob = lead_problem(kw, raw)
        if not prob:
            continue
        text = prob[:1].upper() + prob[1:] + "."
        if kw == "RESUME":  # penhmain no se para: empieza desde cero y sobrescribe los resultados anteriores
            d.warning(text[:-1] + " (penhmain no seguiría la simulación guardada: empezaría desde cero y "
                      "sobrescribiría los resultados anteriores).", **_loc(doc, line))
        else:
            d.error(text, **_loc(doc, line))
    nuc = doc.first("SRNUCL")
    if m.source.radioactive and nuc is not None and m.source.nuclide.strip():
        names.append(("SRNUCL", m.source.nuclide, nuc))
    for kw, raw, line in names:
        name = raw.strip()
        if not name.isascii():
            d.warning(f"«{name}» ({kw}) tiene tildes o eñes: el .in se guarda en Latin-1 y un penhmain de Linux o de "
                      "macOS (también con WSL, en Windows) buscaría otro nombre y no encontraría el fichero, cuyo "
                      "nombre va en UTF-8 en el disco (uno de Windows sí lo encuentra). Mejor un nombre sin tildes ni "
                      "eñes.", **_loc(doc, line))


def _check_energy_and_angles(doc: InDocument, d: DiagnosticList) -> None:
    src = doc.model.source
    if src.spectrum and len(src.spectrum) < 2:
        d.error("El espectro necesita al menos dos líneas SPECTR (la última con P<0).", code=3040,
                fortran_reason="The source energy spectrum is not defined.", **_loc(doc, doc.first("SPECTR")))
    if src.lines and len(src.lines) < 2:
        d.error("El espectro de líneas necesita al menos dos líneas SLINES.", code=3041,
                fortran_reason="The source energy spectrum is not defined.", **_loc(doc, doc.first("SLINES")))
    if len(src.spectrum) >= 2 or len(src.lines) >= 2:
        from .sections import analyze_spectrum

        kw = "SPECTR" if src.spectrum else "SLINES"
        info = analyze_spectrum(src.spectrum or src.lines, lines=not src.spectrum)
        lines = doc.find(kw)
        for prob in info.problems:
            target = lines[0]
            if "misma energía" in prob:  # la segunda línea con esa energía
                seen: set[float] = set()
                for ln in lines:
                    e = float(ln.values[0]) if ln.values else None
                    if e in seen:
                        target = ln
                        break
                    seen.add(e)
            d.warning(prob, **_loc(doc, target))
    se = doc.first("SENERG")
    if se is not None and se.values and not src.spectrum and not src.lines:
        v = float(se.values[0])
        if v < 50.0:  # penhmain.f: IF(SE0.LT.50.0D0) SE0=1.0D9, sin decir nada
            d.warning(f"SENERG = {v:g} eV: con menos de 50 eV penhmain simula partículas de 1 GeV. Las energías "
                      "del .in van en eV (15 MeV se escribe 15e6; en el panel Fuente, «15 MeV»).", **_loc(doc, se))
    emax = source_energy_max(doc)
    if emax is not None:
        eline = doc.first("SENERG") or doc.first("SPECTR") or doc.first("SLINES")
        if 0 < src.kparp < 4 and emax < 50.0:
            d.error("La energía de la fuente es menor que 50 eV.", code=3042,
                    fortran_reason="The energy EPMAX is too small.", **_loc(doc, eline))
        if src.kparp > 3 and emax < 1.0e5:
            d.error("Para protones y alfas la energía debe ser al menos 100 keV.", code=3043,
                    fortran_reason="The energy EHMAX is too small.", **_loc(doc, eline))
    kind, ang = src.angular()
    if kind == "cone" and src.cone is not None:
        th, ph, al = src.cone
        ln = doc.first("SCONE")
        if th < -1e-9 or th - 180.0 > 1e-9:
            d.error("SCONE: θ debe estar entre 0 y 180°.", code=3044,
                    fortran_reason="THETA must be between 0 and 180 deg", **_loc(doc, ln))
        elif ph < -1e-9 or ph - 360.0 > 1e-9:
            d.error("SCONE: φ debe estar entre 0 y 360°.", code=3045,
                    fortran_reason="PHI must be between 0 and 360 deg", **_loc(doc, ln))
        elif al < -1e-9 or al - 180.0 > 1e-9:
            d.error("SCONE: α debe estar entre 0 y 180°.", code=3046,
                    fortran_reason="ALPHA must be between 0 and 180 deg", **_loc(doc, ln))
    elif kind == "rect":
        thl, thu, phl, phu = ang
        ln = doc.first("SRECTA")
        if min(thl, thu) < -1e-9 or max(thl, thu) - 180.0 > 1e-9:
            d.error("SRECTA: θ debe estar entre 0 y 180°.", code=3047,
                    fortran_reason="THETA must be between 0 and 180 deg", **_loc(doc, ln))
        elif min(phl, phu) < -1e-9 or max(phl, phu) - 360.0 > 1e-9:
            d.error("SRECTA: φ debe estar entre 0 y 360°.", code=3048,
                    fortran_reason="PHI must be between 0 and 360 deg", **_loc(doc, ln))
    if src.cone is not None and src.rect is not None:
        d.warning("Hay SCONE y SRECTA: penhmain usa SCONE e ignora SRECTA.", **_loc(doc, doc.first("SRECTA")))


def _check_nuclide(doc: InDocument, folder: Path, d: DiagnosticList) -> None:
    """Fuente radiactiva: PENNUC0 lee el .nuc (con sus límites) y los ficheros de relajación atómica de la carpeta
    en la que se ejecuta penhmain; si algo falla, SOURCE0 se detiene (IRETRN 3120)."""
    from .simcheck import read_nuclide

    src = doc.model.source
    where = _loc(doc, doc.first("SRNUCL"))
    reason = "SOURCE0: PENNUC initialisation failed."
    path = folder / src.nuclide
    if not path.is_file():
        d.error(f"No se encuentra el fichero del nucleido '{src.nuclide}' en la carpeta: penhmain no puede "
                "inicializar la fuente radiactiva.", code=3120, fortran_reason=reason, **where)
        return
    _emax, why = read_nuclide(path)
    if why:
        d.error(f"El fichero del nucleido '{src.nuclide}' {why}: penhmain no puede inicializar la fuente "
                "radiactiva.", code=3120, fortran_reason=reason, **where)
    missing = [f for f in ("pdatconf.p14", "pdrelax.p11") if not (folder / f).is_file()]
    if missing:
        d.warning("La fuente radiactiva necesita " + " y ".join(missing) + " en la carpeta en la que se ejecute "
                  "penhmain (sin ellos se detiene al empezar, IRETRN 3120). Están en penhan/database/pdfiles; "
                  "«Preparar la carpeta de simulación» los copia.", **where)
