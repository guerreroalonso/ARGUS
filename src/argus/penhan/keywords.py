"""Palabras clave del fichero de entrada de penhmain 2025 (.in).

Tabla construida a partir de la subrutina PMRDR0 de penhmain.f, que es la que
lee el fichero: tipo de cada valor (entero, real, nombre de fichero...), si
la línea se puede repetir y la sección a la que pertenece. Los comentarios
canónicos son los de ``penhmain-layout.in`` (salvo ``EMPARC``, que en el
código es ``PFIELD``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordSpec:
    keyword: str  # 6 caracteres, p. ej. 'SENERG'
    mode: str  # 'title', 'list', 'a20' (nombre en columnas 8-27), 'token', 'body'
    kinds: tuple[str, ...] = ()  # tipos de los valores ('int', 'real', 'str')
    names: tuple[str, ...] = ()  # nombres de los valores
    repeat: bool = False
    section: str = ""
    comment: str = ""  # comentario canónico (penhmain-layout.in)
    help: str = ""  # explicación en español
    defaults: tuple = ()  # valores iniciales de PENHMAIN_input (si una barra corta la lectura)

    @property
    def kw(self) -> str:
        return self.keyword.ljust(6)


def _k(keyword, mode, kinds=(), names=(), repeat=False, section="", comment="", help=""):
    d = DEFAULTS.get(keyword, ())
    if not d:
        d = tuple(0 if k == "int" else (0.0 if k == "real" else "") for k in kinds)
    return KeywordSpec(keyword.ljust(6), mode, tuple(kinds), tuple(names), repeat, section, comment, help, d)


# Valores iniciales del módulo PENHMAIN_input (penhmain.f 2025).
DEFAULTS: dict[str, tuple] = {
    "SKPAR": (1,), "IPSPLI": (1,), "WGTWIN": (0.5, 1.5), "EPMAX": (1.0e9, 1.0e9),
    "MSIMPA": (50.0, 50.0, 50.0, 0.05, 0.05, 500.0, 500.0),
    "MSIMPH": (1.0e5, 1.0e5, 1.0e5, 0.05, 0.05, 2.0e3), "MSIMPN": (1.0, 500.0),
    "DSMAX": (1.0e9,), "EABSB": (50.0, 50.0, 50.0), "EABSBH": (1.0e5, 1.0e5, 1.0e5),
    "PFIELD": (0.05, 0.05), "IFORCE": (1, 3, 1.0, 0.5, 1.5), "IBRSPL": (1,), "IXRSPL": (1,),
    "NBE": (0.0, 1.0e9, 100), "NBANGL": (90, 1), "IMPDET": (0.0, 0.0, 100, 0, 1),
    "IDAGEL": (0.0, 1.0e-6, 0), "ANGDET": (0.0, 90.0, 0.0, 360.0), "ADENER": (0.0, 0.0, 100),
    "ENDETC": (0.0, 0.0, 100), "DUMPP": (2.0e9,), "NSIMSH": (2.0e9,), "TIME": (2.0e9,),
    "RSEED": (1, 1),
}


SPECS: list[KeywordSpec] = [
    _k("TITLE", "title", section="título", help="Título del trabajo (hasta 65 caracteres)."),
    # Fuente
    _k("SKPAR", "list", ["int"], ["KPARP"], section="fuente",
       comment="[Primary particles: 1=e-, 2=ph, 3=e+, 4=h, 5=a]",
       help="Tipo de partícula primaria: 0 fuente radiactiva, 1 electrón, 2 fotón, 3 positrón, 4 protón, 5 alfa."),
    _k("SRNUCL", "token", ["str"], ["FNAME"], section="fuente", comment="[NUCLEIDE data file]",
       help="Fichero .nuc del nucleido (solo con SKPAR 0)."),
    _k("SENERG", "list", ["real"], ["SE0"], section="fuente",
       comment="[Initial energy (monoenergetic sources only)]",
       help="Energía inicial (eV) de una fuente monoenergética."),
    _k("SPECTR", "list", ["real", "real"], ["Ei", "Pi"], repeat=True, section="fuente",
       comment="[E bin: lower end and probability]",
       help="Espectro continuo: extremo inferior del intervalo (eV) y probabilidad. Termina con Emax y P<0."),
    _k("SLINES", "list", ["real", "real"], ["Ei", "Pi"], repeat=True, section="fuente",
       comment="[Line energy and probability]", help="Espectro de líneas: energía (eV) y probabilidad."),
    _k("SGPOL", "list", ["real", "real", "real"], ["SP1", "SP2", "SP3"], section="fuente",
       comment="[Stokes parameters for polarised photons]", help="Parámetros de Stokes de fotones polarizados."),
    _k("SPOSIT", "list", ["real", "real", "real"], ["SX0", "SY0", "SZ0"], section="fuente",
       comment="[Coordinates of the source]", help="Posición de la fuente (cm); centro de la caja si hay SBOX."),
    _k("SBOX", "list", ["real", "real", "real"], ["SSX", "SSY", "SSZ"], section="fuente",
       comment="[Source box dimensions]", help="Lados de la caja de la fuente extensa (cm), centrada en SPOSIT."),
    _k("SBODY", "body", [], [], repeat=True, section="fuente",
       comment="[Active source body; one line for each body]",
       help="Cuerpo activo de la fuente extensa (exige SBOX): solo se aceptan puntos dentro de estos cuerpos."),
    _k("SCONE", "list", ["real", "real", "real"], ["THETA", "PHI", "ALPHA"], section="fuente",
       comment="[Conical beam; angles in deg]",
       help="Haz cónico: dirección del eje (θ, φ) y semiapertura α en grados (α=0 lápiz, α=180 isótropo)."),
    _k("SRECTA", "list", ["real", "real", "real", "real"], ["THETAL", "THETAU", "PHIL", "PHIU"], section="fuente",
       comment="[Rectangular beam; angles in deg]",
       help="Haz rectangular: intervalos de θ y φ en grados."),
    # Ficheros de espacio de fases
    _k("IPSFN", "a20", ["str"], ["FNAME"], repeat=True, section="psf",
       comment="[Input psf name, up to 20 characters]", help="Fichero de espacio de fases de entrada."),
    _k("IPSPLI", "list", ["int"], ["NSPLIT"], section="psf", comment="[Splitting number]"),
    _k("WGTWIN", "list", ["real", "real"], ["WGMIN", "WGMAX"], section="psf",
       comment="[Weight window, RR & spl of psf particles]"),
    _k("EPMAX", "list", ["real", "real"], ["EPMAX", "EHMAX"], section="psf",
       comment="[Highest energies of particles in the psf's]"),
    # Materiales
    _k("MFNAME", "a20", ["str"], ["FNAME"], section="materiales", comment="[Material file, up to 20 chars]",
       help="Fichero .mat del material número N (N = orden de aparición)."),
    _k("MSIMPA", "list", ["real"] * 7, ["EABS1", "EABS2", "EABS3", "C1", "C2", "WCC", "WCR"], section="materiales",
       comment="[EABS(1:3),C1,C2,WCC,WCR]"),
    _k("MSIMPH", "list", ["real"] * 6, ["EABSH1", "EABSH2", "EABSH3", "C1H", "C2H", "WCCH"], section="materiales",
       comment="[EABSH(1:3),C1H,C2H,WCCH]"),
    _k("MSIMPN", "list", ["real", "real"], ["FNABS", "FORCEN"], section="materiales", comment="[FNABS,FORCEN]"),
    # Geometría
    _k("GEOMFN", "a20", ["str"], ["FNAME"], section="geometría", comment="[Geometry file, up to 20 chars]",
       help="Fichero de geometría PENGEOM."),
    _k("PARINP", "list", ["int", "real"], ["IP", "PARINP"], repeat=True, section="geometría",
       comment="[Replacement parameter]", help="Valor que sustituye a los parámetros del .geo con índice IP."),
    _k("DSMAX", "body", ["real"], ["DSMAX"], repeat=True, section="geometría",
       comment="[KB, maximum step length in body KB]"),
    _k("EABSB", "body", ["real"] * 3, ["EABSB1", "EABSB2", "EABSB3"], repeat=True, section="geometría",
       comment="[KB, local absorption energies, EABSB]"),
    _k("EABSBH", "body", ["real"] * 3, ["EABSBH1", "EABSBH2", "EABSBH3"], repeat=True, section="geometría",
       comment="[KB, local absorption energies, EABSBH]"),
    # Campos
    _k("PFIELD", "list", ["real", "real"], ["ULDE", "ULDV"], section="campos",
       comment="[Precision delta parameters]"),
    _k("EFIELD", "body", ["real"] * 3, ["EX", "EY", "EZ"], repeat=True, section="campos",
       comment="[KB, E field components, in V/cm]"),
    _k("BFIELD", "body", ["real"] * 3, ["BX", "BY", "BZ"], repeat=True, section="campos",
       comment="[KB, B field components, in gauss]"),
    # Reducción de varianza
    _k("IFORCE", "body", ["int", "int", "real", "real", "real"], ["KP", "ICOL", "FORCER", "WLOW", "WHIG"],
       repeat=True, section="reducción de varianza", comment="[KB,KP,ICOL,FORCER,WLOW,WHIG]"),
    _k("IBRSPL", "body", ["int"], ["IBRSPL"], repeat=True, section="reducción de varianza",
       comment="[KB,splitting factor]"),
    _k("IXRSPL", "body", ["int"], ["IXRSPL"], repeat=True, section="reducción de varianza",
       comment="[KB,splitting factor]"),
    # Distribuciones de partículas emergentes
    _k("NBE", "list", ["real", "real", "int"], ["EL", "EU", "NBE"], section="emergentes",
       comment="[Energy window and no. of bins]"),
    _k("NBANGL", "list", ["int", "int"], ["NBTH", "NBPH"], section="emergentes",
       comment="[No. of bins for the angles THETA and PHI]"),
    # Detectores de impacto
    _k("IMPDET", "list", ["real", "real", "int", "int", "int"], ["EL", "EU", "NBE", "IPSF", "IDCUT"],
       section="detectores de impacto", comment="[E-window, no. of bins, IPSF, IDCUT]"),
    _k("IDSPC", "a20", ["str"], ["FNAME"], section="detectores de impacto",
       comment="[Spectrum file name, 20 chars]"),
    _k("IDPSF", "a20", ["str"], ["FNAME"], section="detectores de impacto",
       comment="[Phase-space file name, 20 chars]"),
    _k("IDFLNC", "a20", ["str"], ["FNAME"], section="detectores de impacto",
       comment="[Fluence spectrum file name, 20 chars]"),
    _k("IDAGEL", "list", ["real", "real", "int"], ["AGEL", "AGEU", "NAGE"], section="detectores de impacto",
       comment="[Age interval and no. of bins]"),
    _k("IDAGEF", "a20", ["str"], ["FNAME"], section="detectores de impacto",
       comment="[Age-distribution file name, 20 chars]"),
    _k("IDBODY", "body", [], [], repeat=True, section="detectores de impacto", comment="[Active body]"),
    _k("IDKPAR", "list", ["int"], ["KP"], repeat=True, section="detectores de impacto",
       comment="[Type of detected particles]"),
    # Detectores angulares
    _k("ANGDET", "list", ["real"] * 4, ["THETA1", "THETA2", "PHI1", "PHI2"], section="detectores angulares",
       comment="[Angular window, angles in deg]"),
    _k("ADENER", "list", ["real", "real", "int"], ["EL", "EU", "NBE"], section="detectores angulares",
       comment="[Energy window and no. of bins]"),
    _k("ADKPAR", "list", ["int"], ["KP"], section="detectores angulares", comment="[Type of detected particles]"),
    _k("ADSPC", "a20", ["str"], ["FNAME"], section="detectores angulares", comment="[Spectrum file name, 20 chars]"),
    # Detectores de energía depositada
    _k("ENDETC", "list", ["real", "real", "int"], ["EL", "EU", "NBE"], section="detectores de energía",
       comment="[Energy window and no. of bins]"),
    _k("EDSPC", "a20", ["str"], ["FNAME"], section="detectores de energía", comment="[Spectrum file name, 20 chars]"),
    _k("EDBODY", "body", [], [], repeat=True, section="detectores de energía", comment="[Active body]"),
    # Dosis
    _k("GRIDX", "list", ["real", "real", "int"], ["XL", "XU", "NDBX"], section="dosis",
       comment="[X coords of the box vertices, no. of bins]"),
    _k("GRIDY", "list", ["real", "real", "int"], ["YL", "YU", "NDBY"], section="dosis",
       comment="[Y coords of the box vertices, no. of bins]"),
    _k("GRIDZ", "list", ["real", "real", "int"], ["ZL", "ZU", "NDBZ"], section="dosis",
       comment="[Z coords of the box vertices, no. of bins]"),
    _k("GRIDR", "list", ["real", "int"], ["RU", "NDBR"], section="dosis",
       comment="[Radius of the dose volume, no. of bins]"),
    # Proceso externo
    _k("EPROF", "a20", ["str"], ["FNAME"], section="proceso externo", comment="[Cross section table, up to 20 chars]"),
    _k("EPROPM", "list", ["int", "int"], ["KP", "MAT"], section="proceso externo",
       comment="[Activating particle, material]"),
    # Trabajo
    _k("RESUME", "a20", ["str"], ["FNAME"], section="trabajo", comment="[Resume from this dump file, 20 chars]"),
    _k("DUMPTO", "a20", ["str"], ["FNAME"], section="trabajo", comment="[Generate this dump file, 20 chars]"),
    _k("DUMPP", "list", ["real"], ["DUMPP"], section="trabajo", comment="[Dumping period, in sec]"),
    _k("LTRACK", "list", ["int"], ["NSHOW"], section="trabajo", comment="[Generate shower files for visualisation]"),
    _k("NSIMSH", "list", ["real"], ["DSHN"], section="trabajo", comment="[Desired number of simulated showers]"),
    _k("TIME", "list", ["real"], ["TIMEA"], section="trabajo", comment="[Allotted simulation time, in sec]"),
    _k("RSEED", "list", ["int", "int"], ["ISEED1", "ISEED2"], repeat=True, section="trabajo",
       comment="[Seeds of the random-number generator]"),
]

SPEC: dict[str, KeywordSpec] = {s.kw: s for s in SPECS}
ORDER: dict[str, int] = {s.kw: i for i, s in enumerate(SPECS)}

# Palabras que el usuario suele copiar de penhmain-layout.in y que el código no reconoce.
KNOWN_TYPOS = {
    "EMPARC": "PFIELD",
}
