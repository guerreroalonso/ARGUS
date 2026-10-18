"""Los materiales de la lista de PENELOPE en español, y buscarlos al escribir (0.16.0, ADR-020).

``database/material-list.txt`` trae los 280 materiales predefinidos de PENELOPE en inglés («Water, liquid», «Bone,
cortical (ICRP)»…): quien escribe «agua» o «hueso» no los encontraba. Aquí está el nombre en español de cada uno (y
algunos nombres de uso corriente: PMMA, metacrilato, teflón, PVC, BGO, NaI…) para enseñarlos y buscarlos en los dos
idiomas. El número de la lista es el que cuenta (el que se le da a penhmat, opción 2); el nombre en español solo se
usa si el inglés de la lista del usuario es el que se espera para ese número (otra versión de la lista con otra
numeración no recibiría un nombre equivocado).

:func:`search` filtra mientras se escribe (sin distinguir mayúsculas ni tildes, cada palabra por separado) y
:func:`suggest` propone el material de la lista que corresponde a un nombre de fichero o a una descripción («agua.mat»
→ n.º 278), para asignar los materiales que ARGUS no ha identificado.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

# n.º → (nombre en inglés de material-list.txt, nombre en español)
NAMES: dict[int, tuple[str, str]] = {
    1: ("Hydrogen", "Hidrógeno"), 2: ("Helium", "Helio"), 3: ("Lithium", "Litio"), 4: ("Beryllium", "Berilio"),
    5: ("Boron", "Boro"), 6: ("Amorphous carbon", "Carbono amorfo"), 7: ("Nitrogen", "Nitrógeno"),
    8: ("Oxygen", "Oxígeno"), 9: ("Fluorine", "Flúor"), 10: ("Neon", "Neón"), 11: ("Sodium", "Sodio"),
    12: ("Magnesium", "Magnesio"), 13: ("Aluminium", "Aluminio"), 14: ("Silicon", "Silicio"),
    15: ("Phosphorus", "Fósforo"), 16: ("Sulfur", "Azufre"), 17: ("Chlorine", "Cloro"), 18: ("Argon", "Argón"),
    19: ("Potassium", "Potasio"), 20: ("Calcium", "Calcio"), 21: ("Scandium", "Escandio"),
    22: ("Titanium", "Titanio"), 23: ("Vanadium", "Vanadio"), 24: ("Chromium", "Cromo"),
    25: ("Manganese", "Manganeso"), 26: ("Iron", "Hierro"), 27: ("Cobalt", "Cobalto"), 28: ("Nickel", "Níquel"),
    29: ("Copper", "Cobre"), 30: ("Zinc", "Zinc"), 31: ("Gallium", "Galio"), 32: ("Germanium", "Germanio"),
    33: ("Arsenic", "Arsénico"), 34: ("Selenium", "Selenio"), 35: ("Bromine", "Bromo"), 36: ("Krypton", "Kriptón"),
    37: ("Rubidium", "Rubidio"), 38: ("Strontium", "Estroncio"), 39: ("Yttrium", "Itrio"),
    40: ("Zirconium", "Circonio"), 41: ("Niobium", "Niobio"), 42: ("Molybdenum", "Molibdeno"),
    43: ("Technetium", "Tecnecio"), 44: ("Ruthenium", "Rutenio"), 45: ("Rhodium", "Rodio"),
    46: ("Palladium", "Paladio"), 47: ("Silver", "Plata"), 48: ("Cadmium", "Cadmio"), 49: ("Indium", "Indio"),
    50: ("Tin", "Estaño"), 51: ("Antimony", "Antimonio"), 52: ("Tellurium", "Telurio"), 53: ("Iodine", "Yodo"),
    54: ("Xenon", "Xenón"), 55: ("Cesium", "Cesio"), 56: ("Barium", "Bario"), 57: ("Lanthanum", "Lantano"),
    58: ("Cerium", "Cerio"), 59: ("Praseodymium", "Praseodimio"), 60: ("Neodymium", "Neodimio"),
    61: ("Promethium", "Prometio"), 62: ("Samarium", "Samario"), 63: ("Europium", "Europio"),
    64: ("Gadolinium", "Gadolinio"), 65: ("Terbium", "Terbio"), 66: ("Dysprosium", "Disprosio"),
    67: ("Holmium", "Holmio"), 68: ("Erbium", "Erbio"), 69: ("Thulium", "Tulio"), 70: ("Ytterbium", "Iterbio"),
    71: ("Lutetium", "Lutecio"), 72: ("Hafnium", "Hafnio"), 73: ("Tantalum", "Tantalio"),
    74: ("Tungsten", "Wolframio (tungsteno)"), 75: ("Rhenium", "Renio"), 76: ("Osmium", "Osmio"),
    77: ("Iridium", "Iridio"), 78: ("Platinum", "Platino"), 79: ("Gold", "Oro"), 80: ("Mercury", "Mercurio"),
    81: ("Thallium", "Talio"), 82: ("Lead", "Plomo"), 83: ("Bismuth", "Bismuto"), 84: ("Polonium", "Polonio"),
    85: ("Astatine", "Astato"), 86: ("Radon", "Radón"), 87: ("Francium", "Francio"), 88: ("Radium", "Radio"),
    89: ("Actinium", "Actinio"), 90: ("Thorium", "Torio"), 91: ("Protactinium", "Protactinio"),
    92: ("Uranium", "Uranio"), 93: ("Neptunium", "Neptunio"), 94: ("Plutonium", "Plutonio"),
    95: ("Americium", "Americio"), 96: ("Curium", "Curio"), 97: ("Berkelium", "Berkelio"),
    98: ("Californium", "Californio"), 99: ("Einsteinium", "Einstenio"),
    100: ("Acetone", "Acetona"),
    101: ("Acetylene", "Acetileno"),
    102: ("Adenine", "Adenina"),
    103: ("Adipose tissue (ICRP)", "Tejido adiposo (ICRP)"),
    104: ("Air, dry (near sea level)", "Aire seco (a nivel del mar)"),
    105: ("Alanine", "Alanina"),
    106: ("Aluminum oxide", "Óxido de aluminio (alúmina)"),
    107: ("Amber", "Ámbar"),
    108: ("Ammonia", "Amoníaco"),
    109: ("Aniline", "Anilina"),
    110: ("Anthracene", "Antraceno"),
    111: ("B-100 bone-equivalent plastic", "Plástico equivalente a hueso B-100"),
    112: ("Bakelite", "Baquelita"),
    113: ("Barium fluoride", "Fluoruro de bario"),
    114: ("Barium sulfate", "Sulfato de bario"),
    115: ("Benzene", "Benceno"),
    116: ("Beryllium oxide", "Óxido de berilio"),
    117: ("Bismuth germanium oxide", "Germanato de bismuto (BGO)"),
    118: ("Blood (ICRP)", "Sangre (ICRP)"),
    119: ("Bone, compact (ICRU)", "Hueso compacto (ICRU)"),
    120: ("Bone, cortical (ICRP)", "Hueso cortical (ICRP)"),
    121: ("Boron carbide", "Carburo de boro"),
    122: ("Boron oxide", "Óxido de boro"),
    123: ("Brain (ICRP)", "Cerebro (ICRP)"),
    124: ("Butane", "Butano"),
    125: ("N-butyl alcohol", "Alcohol n-butílico (n-butanol)"),
    126: ("C-552 air-equivalent plastic", "Plástico equivalente a aire C-552"),
    127: ("Cadmium telluride", "Telururo de cadmio (CdTe)"),
    128: ("Cadmium tungstate", "Wolframato de cadmio"),
    129: ("Calcium carbonate", "Carbonato de calcio"),
    130: ("Calcium fluoride", "Fluoruro de calcio"),
    131: ("Calcium oxide", "Óxido de calcio"),
    132: ("Calcium sulfate", "Sulfato de calcio"),
    133: ("Calcium tungstate", "Wolframato de calcio"),
    134: ("Carbon dioxide", "Dióxido de carbono"),
    135: ("Carbon tetrachloride", "Tetracloruro de carbono"),
    136: ("Cellulose acetate, cellophane", "Acetato de celulosa (celofán)"),
    137: ("Cellulose acetate butyrate", "Acetato butirato de celulosa"),
    138: ("Cellulose nitrate", "Nitrato de celulosa"),
    139: ("Ceric sulfate dosimeter solution", "Disolución dosimétrica de sulfato cérico"),
    140: ("Cesium fluoride", "Fluoruro de cesio"),
    141: ("Cesium iodide", "Yoduro de cesio (CsI)"),
    142: ("Chlorobenzene", "Clorobenceno"),
    143: ("Chloroform", "Cloroformo"),
    144: ("Concrete, portland", "Hormigón (portland)"),
    145: ("Cyclohexane", "Ciclohexano"),
    146: ("1,2-dichlorobenzene", "1,2-diclorobenceno"),
    147: ("Dichlorodiethyl ether", "Éter diclorodietílico"),
    148: ("1,2-dichloroethane", "1,2-dicloroetano"),
    149: ("Diethyl ether", "Éter dietílico"),
    150: ("N,n-dimethyl formamide", "N,N-dimetilformamida"),
    151: ("Dimethyl sulfoxide", "Dimetilsulfóxido"),
    152: ("Ethane", "Etano"),
    153: ("Ethyl alcohol", "Alcohol etílico (etanol)"),
    154: ("Ethyl cellulose", "Etilcelulosa"),
    155: ("Ethylene", "Etileno"),
    156: ("Eye lens (ICRP)", "Cristalino (ICRP)"),
    157: ("Ferric oxide", "Óxido férrico"),
    158: ("Ferroboride", "Boruro de hierro"),
    159: ("Ferrous oxide", "Óxido ferroso"),
    160: ("Ferrous sulfate dosimeter solution", "Disolución dosimétrica de sulfato ferroso (Fricke)"),
    161: ("Freon-12", "Freón-12"),
    162: ("Freon-12b2", "Freón-12B2"),
    163: ("Freon-13", "Freón-13"),
    164: ("Freon-13b1", "Freón-13B1"),
    165: ("Freon-13i1", "Freón-13I1"),
    166: ("Gadolinium oxysulfide", "Oxisulfuro de gadolinio"),
    167: ("Gallium arsenide", "Arseniuro de galio"),
    168: ("Gel in photographic emulsion", "Gel de emulsión fotográfica"),
    169: ("Pyrex glass", "Vidrio Pyrex"),
    170: ("Glass, lead", "Vidrio plomado"),
    171: ("Glass, plate", "Vidrio plano"),
    172: ("Glucose", "Glucosa"),
    173: ("Glutamine", "Glutamina"),
    174: ("Glycerol", "Glicerol (glicerina)"),
    175: ("Graphite", "Grafito"),
    176: ("Guanine", "Guanina"),
    177: ("Gypsum, plaster of Paris", "Yeso"),
    178: ("N-heptane", "n-heptano"),
    179: ("N-hexane", "n-hexano"),
    180: ("Kapton polyimide film", "Película de poliimida (Kapton)"),
    181: ("Lanthanum oxybromide", "Oxibromuro de lantano"),
    182: ("Lanthanum oxysulfide", "Oxisulfuro de lantano"),
    183: ("Lead oxide", "Óxido de plomo"),
    184: ("Lithium amide", "Amida de litio"),
    185: ("Lithium carbonate", "Carbonato de litio"),
    186: ("Lithium fluoride", "Fluoruro de litio (LiF)"),
    187: ("Lithium hydride", "Hidruro de litio"),
    188: ("Lithium iodide", "Yoduro de litio"),
    189: ("Lithium oxide", "Óxido de litio"),
    190: ("Lithium tetraborate", "Tetraborato de litio"),
    191: ("Lung (ICRP)", "Pulmón (ICRP)"),
    192: ("M3 wax", "Cera M3"),
    193: ("Magnesium carbonate", "Carbonato de magnesio"),
    194: ("Magnesium fluoride", "Fluoruro de magnesio"),
    195: ("Magnesium oxide", "Óxido de magnesio"),
    196: ("Magnesium tetraborate", "Tetraborato de magnesio"),
    197: ("Mercuric iodide", "Yoduro mercúrico"),
    198: ("Methane", "Metano"),
    199: ("Methanol", "Metanol"),
    200: ("Mixed wax", "Cera mixta"),
    201: ("Ms20 tissue substitute", "Sustituto de tejido MS20"),
    202: ("Muscle, skeletal (ICRP)", "Músculo esquelético (ICRP)"),
    203: ("Muscle, striated (ICRU)", "Músculo estriado (ICRU)"),
    204: ("Muscle-equivalent liquid, with sucrose", "Líquido equivalente a músculo, con sacarosa"),
    205: ("Muscle-equivalent liquid, without sucrose", "Líquido equivalente a músculo, sin sacarosa"),
    206: ("Naphthalene", "Naftaleno"),
    207: ("Nitrobenzene", "Nitrobenceno"),
    208: ("Nitrous oxide", "Óxido nitroso"),
    209: ("Nylon, du Pont elvamide 8062", "Nailon (Du Pont Elvamide 8062)"),
    210: ("Nylon, type 6 and type 6/6", "Nailon 6 y 6/6"),
    211: ("Nylon, type 6/10", "Nailon 6/10"),
    212: ("Nylon, type 11 (rilsan)", "Nailon 11 (Rilsan)"),
    213: ("Octane, liquid", "Octano líquido"),
    214: ("Paraffin wax", "Parafina"),
    215: ("N-pentane", "n-pentano"),
    216: ("Photographic emulsion", "Emulsión fotográfica"),
    217: ("Plastic scintillator (vinyltoluene based)", "Centelleador plástico (de viniltolueno)"),
    218: ("Plutonium dioxide", "Dióxido de plutonio"),
    219: ("Polyacrylonitrile", "Poliacrilonitrilo"),
    220: ("Polycarbonate (makrolon, lexan)", "Policarbonato (Makrolon, Lexan)"),
    221: ("Polychlorostyrene", "Policloroestireno"),
    222: ("Polyethylene", "Polietileno"),
    223: ("Polyethylene terephthalate (mylar)", "Tereftalato de polietileno (PET, Mylar)"),
    224: ("Polymethyl methacrilate (lucite, perspex, plexiglass)", "PMMA, polimetilmetacrilato (metacrilato, perspex)"),
    225: ("Polyoxymethylene", "Polioximetileno (POM)"),
    226: ("Polypropylene", "Polipropileno"),
    227: ("Polystyrene", "Poliestireno"),
    228: ("Polytetrafluoroethylene (teflon)", "Politetrafluoroetileno (teflón)"),
    229: ("Polytrifluorochloroethylene", "Policlorotrifluoroetileno"),
    230: ("Polyvinyl acetate", "Acetato de polivinilo"),
    231: ("Polyvinyl alcohol", "Alcohol polivinílico"),
    232: ("Polyvinyl butyral", "Polivinilbutiral"),
    233: ("Polyvinyl chloride", "Policloruro de vinilo (PVC)"),
    234: ("Polyvinylidene chloride (saran)", "Policloruro de vinilideno (Saran)"),
    235: ("Polyvinylidene fluoride", "Polifluoruro de vinilideno (PVDF)"),
    236: ("Polyvinyl pyrrolidone", "Polivinilpirrolidona"),
    237: ("Potassium iodide", "Yoduro de potasio"),
    238: ("Potassium oxide", "Óxido de potasio"),
    239: ("Propane", "Propano"),
    240: ("Propane, liquid", "Propano líquido"),
    241: ("N-propyl alcohol", "Alcohol n-propílico (n-propanol)"),
    242: ("Pyridine", "Piridina"),
    243: ("Rubber, butyl", "Caucho butílico"),
    244: ("Rubber, natural", "Caucho natural"),
    245: ("Rubber, neoprene", "Neopreno"),
    246: ("Silicon dioxide", "Dióxido de silicio (sílice)"),
    247: ("Silver bromide", "Bromuro de plata"),
    248: ("Silver chloride", "Cloruro de plata"),
    249: ("Silver halides in photographic emulsion", "Haluros de plata de emulsión fotográfica"),
    250: ("Silver iodide", "Yoduro de plata"),
    251: ("Skin (ICRP)", "Piel (ICRP)"),
    252: ("Sodium carbonate", "Carbonato de sodio"),
    253: ("Sodium iodide", "Yoduro de sodio (NaI)"),
    254: ("Sodium monoxide", "Monóxido de sodio"),
    255: ("Sodium nitrate", "Nitrato de sodio"),
    256: ("Stilbene", "Estilbeno"),
    257: ("Sucrose", "Sacarosa"),
    258: ("Terphenyl", "Terfenilo"),
    259: ("Testes (ICRP)", "Testículos (ICRP)"),
    260: ("Tetrachloroethylene", "Tetracloroetileno"),
    261: ("Thallium chloride", "Cloruro de talio"),
    262: ("Tissue, soft (ICRP)", "Tejido blando (ICRP)"),
    263: ("Tissue, soft (ICRU four-component)", "Tejido blando (ICRU, de cuatro componentes)"),
    264: ("Tissue-equivalent gas (methane based)", "Gas equivalente a tejido (de metano)"),
    265: ("Tissue-equivalent gas (propane based)", "Gas equivalente a tejido (de propano)"),
    266: ("Tissue-equivalent plastic (A-150)", "Plástico equivalente a tejido (A-150)"),
    267: ("Titanium dioxide", "Dióxido de titanio"),
    268: ("Toluene", "Tolueno"),
    269: ("Trichloroethylene", "Tricloroetileno"),
    270: ("Triethyl phosphate", "Fosfato de trietilo"),
    271: ("Tungsten hexafluoride", "Hexafluoruro de wolframio"),
    272: ("Uranium dicarbide", "Dicarburo de uranio"),
    273: ("Uranium monocarbide", "Monocarburo de uranio"),
    274: ("Uranium oxide", "Óxido de uranio"),
    275: ("Urea", "Urea"),
    276: ("Valine", "Valina"),
    277: ("Viton fluoroelastomer", "Fluoroelastómero Viton"),
    278: ("Water, liquid", "Agua líquida"),
    279: ("Water vapour", "Vapor de agua"),
    280: ("Xylene", "Xileno"),
}

# Otros nombres con que se buscan (palabras enteras): siglas, nombres comerciales y de uso corriente.
ALIASES: dict[int, tuple[str, ...]] = {
    6: ("carbono", "carbon"), 74: ("w", "tungsteno"), 82: ("pb",), 29: ("cu",), 13: ("al", "aluminum"), 26: ("fe",),
    16: ("sulphur",), 55: ("caesium",),
    79: ("au",), 47: ("ag",), 78: ("pt",), 30: ("cinc", "zn"), 50: ("sn",), 22: ("ti",), 28: ("ni",),
    103: ("grasa", "adiposo"), 104: ("aire", "air"), 106: ("alumina",), 111: ("b100",), 117: ("bgo",),
    119: ("hueso",), 120: ("hueso",), 126: ("c552",), 127: ("cdte",), 141: ("csi",), 144: ("hormigon", "cemento"),
    153: ("etanol",), 160: ("fricke",), 170: ("vidrio",), 174: ("glicerina",), 175: ("grafito",),
    186: ("lif", "tld"), 191: ("pulmon",), 202: ("musculo",), 203: ("musculo",), 214: ("parafina",),
    217: ("centelleador",), 220: ("pc", "lexan", "makrolon"), 222: ("pe", "hdpe", "ldpe"), 223: ("pet", "mylar"),
    224: ("pmma", "metacrilato", "plexiglas", "plexiglass", "perspex", "lucite", "lucita", "acrilico"),
    225: ("pom", "delrin"), 226: ("pp",), 227: ("ps",), 228: ("ptfe", "teflon"), 233: ("pvc",),
    235: ("pvdf",), 253: ("nai",), 262: ("tejido",), 263: ("tejido",), 266: ("a150",), 278: ("agua", "water"),
}


def _symbol_aliases() -> None:
    """El símbolo de cada elemento (1-99) también lo busca: «Pb», «Si», «W»."""
    from .composition import SYMBOLS

    for z in range(1, 100):
        sym = SYMBOLS[z].lower() if z < len(SYMBOLS) and SYMBOLS[z] else ""
        if sym and sym not in ALIASES.get(z, ()):
            ALIASES[z] = (*ALIASES.get(z, ()), sym)


_symbol_aliases()


def norm(text: str) -> str:
    """Sin tildes ni mayúsculas, para comparar («Pulmón» → «pulmon»)."""
    t = unicodedata.normalize("NFKD", text)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def words(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-zñ]+", norm(text)) if w]


@dataclass
class ListEntry:
    """Un material que se puede elegir: de la lista de PENELOPE (``number``) o personalizado (``key``)."""

    text: str  # lo que queda escrito en el campo (y en el comentario del .in)
    secondary: str = ""  # lo que se enseña en gris a su lado
    number: int | None = None  # n.º de la lista de PENELOPE
    key: str | None = None  # nombre en la biblioteca de materiales personalizados
    search_words: list[str] = field(default_factory=list)
    search_text: str = ""
    symbols: tuple[str, ...] = ()  # los elementos de un personalizado («cu», «zn»): se buscan solo enteros
    formula: str = ""  # su estequiometría reducida (:func:`formula_key`), si es un compuesto de la lista con fórmula
    formula_exact: str = ""  # la misma, sin reducir (como la da pdcompos.pen: C6H6 para el benceno)
    density: float | None = None  # g/cm³ (de pdcompos.pen), para preferir el sólido o el líquido de una fórmula

    @property
    def personal(self) -> bool:
        return self.key is not None


def spanish_name(number: int, english: str) -> str | None:
    """El nombre en español del material n.º ``number`` si la lista del usuario lo llama como se espera."""
    known = NAMES.get(number)
    if known is None or norm(known[0]).strip() != norm(english).strip():
        return None
    return known[1]


def penelope_label(item: tuple[int, str]) -> str:
    """Lo que se escribe para un material de la lista: «Agua líquida (PENELOPE 278)» (o el nombre en inglés si no
    se conoce en español)."""
    number, english = item
    return f"{spanish_name(number, english) or english} (PENELOPE {number})"


def penelope_entries(mlist: list[tuple[int, str]]) -> list[ListEntry]:
    """Los materiales de la lista de PENELOPE para elegirlos. Si la lista es la de la base de datos del usuario
    (:func:`argus.penhan.materials.load_material_list`), trae también la fórmula y la densidad de cada uno
    (``pdfiles/pdcompos.pen``), con las que se reconocen las fórmulas químicas («H2O.mat», «Al2O3»)."""
    formulas = getattr(mlist, "formulas", {}) or {}
    densities = getattr(mlist, "densities", {}) or {}
    out = []
    for number, english in mlist:
        es = spanish_name(number, english)
        text = f"{es or english} (PENELOPE {number})"
        sw = words(english) + (words(es) if es else []) + list(ALIASES.get(number, ())) + [str(number)]
        atoms = formulas.get(number)
        key = (formula_key(atoms) if atoms else None) or ""
        compound = "," in key  # (un elemento solo no es una fórmula que buscar)
        out.append(ListEntry(text, english if es else "", number=number, search_words=sw,
                             search_text=" ".join(sw), formula=key if compound else "",
                             formula_exact=(formula_key(atoms, reduce=False) or "") if compound else "",
                             density=densities.get(number)))
    return out


def personal_entry(name: str, summary: str = "") -> ListEntry:
    """Un material personalizado: se busca por su nombre y por los símbolos de sus elementos, enteros («zn» encuentra
    el latón; «c» o «i» no: antes se buscaba en todo el resumen, y «g/cm³», «I =» o «eV» ponían todos los
    personalizados delante del carbono o del yodo)."""
    sw = words(name)
    return ListEntry(name, summary, key=name, search_words=sw, search_text=" ".join(sw),
                     symbols=_summary_symbols(summary))


def _summary_symbols(summary: str) -> tuple[str, ...]:
    """Los elementos de un resumen de composición («Cu 70,00 % · Zn 30,00 %, 8,53 g/cm³, I = 330,3 eV» → cu, zn;
    «H2O, 1 g/cm³…» → h, o)."""
    chunk = (summary or "").split(" g/cm")[0]
    chunk = re.sub(r",\s*[\d.,]+\s*$", "", chunk)
    valid = _symbols_lower()
    out: list[str] = []
    for m in re.findall(r"[A-Z][a-z]?", chunk):
        if m.lower() in valid and m.lower() not in out:
            out.append(m.lower())
    return tuple(out)


# -- fórmulas químicas -------------------------------------------------------------------------------------------------

def formula_key(atoms, reduce: bool = True) -> str | None:
    """La estequiometría reducida de unos átomos por molécula (``{1: 2, 8: 1}`` → «1:2,8:1»; sin ``reduce``, tal
    cual: C6H6 no es CH); None si no son números enteros de átomos (una mezcla, un tejido)."""
    items = list(atoms.items()) if isinstance(atoms, dict) else list(atoms)
    acc: dict[int, int] = {}
    for z, n in items:
        n = float(n)
        if not math.isfinite(n):
            return None
        k = round(n)
        if k < 1 or abs(n - k) > 1e-6 * max(1.0, n):
            return None
        acc[int(z)] = acc.get(int(z), 0) + k
    if not acc:
        return None
    g = 0
    for k in acc.values():
        g = math.gcd(g, k)
    if not reduce:
        g = 1
    return ",".join(f"{z}:{k // g}" for z, k in sorted(acc.items()))


def _symbols_lower() -> dict[str, int]:
    from .composition import SYMBOLS

    return {SYMBOLS[z].lower(): z for z in range(1, min(100, len(SYMBOLS))) if SYMBOLS[z]}


def _segmentations(text: str, limit: int = 64) -> list[dict[int, int]]:
    """Las maneras de leer en minúsculas una fórmula sin mayúsculas («co2»: Co₂ o CO₂).."""
    syms = _symbols_lower()
    out: list[dict[int, int]] = []

    def walk(i: int, acc: dict[int, int]) -> None:
        if len(out) >= limit:
            return
        if i == len(text):
            if acc:
                out.append(dict(acc))
            return
        for size in (2, 1):
            sym = text[i:i + size]
            z = syms.get(sym) if len(sym) == size and sym.isalpha() else None
            if z is None:
                continue
            m = re.match(r"\d+", text[i + size:])
            n = int(m.group()) if m else 1
            if n == 0:
                continue
            nxt = dict(acc)
            nxt[z] = nxt.get(z, 0) + n
            walk(i + size + (len(m.group()) if m else 0), nxt)

    if 0 < len(text) <= 16 and re.fullmatch(r"[a-z][a-z0-9]*", text):
        walk(0, {})
    return out


def formula_keys(text: str) -> tuple[set[str], bool]:
    """Los compuestos (dos elementos o más) que puede ser ``text`` como fórmula química, como pares «reducida|tal
    cual» (:func:`formula_key`), y si lo es seguro (escrita con mayúsculas y minúsculas: «Al2O3», «SiO2»). En
    minúsculas o en mayúsculas («h2o», «SIO2») se prueban todas las maneras de leerla: solo cuentan las que son un
    material de la lista."""
    t = re.sub(r"\s+", "", text or "")
    if not t or not re.fullmatch(r"[A-Za-z0-9()]+", t) or not re.search(r"[A-Za-z]", t):
        return set(), False
    keys: set[str] = set()
    sure = False
    from .composition import parse_formula

    def add(atoms) -> bool:
        if len({z for z, _n in (atoms.items() if isinstance(atoms, dict) else atoms)}) < 2:
            return False
        red, exact = formula_key(atoms), formula_key(atoms, reduce=False)
        if red and exact:
            keys.add(f"{red}|{exact}")
            return True
        return False

    try:
        atoms = parse_formula(t)
    except ValueError:
        atoms = None
    if atoms and add(atoms):
        sure = t != t.upper() and t != t.lower()  # con minúsculas y mayúsculas: no hay otra manera de leerla
    if t == t.lower() or t == t.upper():
        for seg in _segmentations(t.lower()):
            add(seg)
    return keys, sure


def _formula_score(entry: ListEntry, keys: set[str]) -> float:
    """5,5 si ``keys`` tiene su fórmula tal cual (C6H6: el benceno), 5 si solo la reducida (CH: también el
    poliestireno), 0 si no es ninguna."""
    if not entry.formula:
        return 0.0
    best = 0.0
    for k in keys:
        red, _sep, exact = k.partition("|")
        if exact == entry.formula_exact:
            return 5.5
        if red == entry.formula:
            best = 5.0
    return best


# -- buscar y proponer ------------------------------------------------------------------------------------------------

def _matches(entry: ListEntry, tokens: list[str]) -> bool:
    for t in tokens:
        if t in entry.symbols:
            continue
        if len(t) <= 2:  # «pb», «al», «w»: palabras enteras o principios de palabra
            if not any(w == t or w.startswith(t) for w in entry.search_words):
                return False
        elif t not in entry.search_text:
            return False
    return True


def _rank(entry: ListEntry, tokens: list[str]) -> tuple:
    first = tokens[0] if tokens else ""
    sw = entry.search_words
    exact = any(w == first for w in sw) or first in entry.symbols
    starts = any(w.startswith(first) for w in sw)
    lead = bool(sw) and sw[0].startswith(first)  # la primera palabra del nombre en inglés…
    es_lead = False
    if entry.number is not None and entry.number in NAMES:
        es = words(NAMES[entry.number][1])
        es_lead = bool(es) and es[0].startswith(first)  # …o del nombre en español
    return (not entry.personal, not exact, not (lead or es_lead), not starts, entry.number or 0)


def search(entries: list[ListEntry], query: str) -> list[ListEntry]:
    """Los materiales que corresponden a lo escrito (cada palabra, en cualquier orden y sin tildes; también la
    etiqueta entera, «Agua líquida (PENELOPE 278)» o «penelope 278», y una fórmula química, «H2O», si la lista trae
    las fórmulas), los más parecidos primero; sin nada escrito, todos en su orden (los personalizados delante)."""
    tokens = [t for t in words(query) if t != "penelope"]  # (está en todas las etiquetas de la lista)
    if not tokens:
        return sorted(entries, key=lambda e: (not e.personal, e.number or 0))
    found = [e for e in entries if _matches(e, tokens)]
    keys, _sure = formula_keys(query.strip())
    if keys:
        by_formula = sorted((e for e in entries if _formula_score(e, keys) > 0 and e not in found),
                            key=lambda e: (-_formula_score(e, keys), -(e.density or 0.0), e.number or 0))
        return by_formula + sorted(found, key=lambda e: _rank(e, tokens))
    return sorted(found, key=lambda e: _rank(e, tokens))


_GENERIC = {"mat", "material", "materiales", "de", "del", "la", "el", "los", "las", "y", "con", "sin", "en",
            "penelope", "nuevo", "nueva", "copia", "the", "of", "and"}
# Unidades tras un número en el nombre del fichero («agua_5cm», «plomo_1in»): no son el símbolo de un elemento.
# (Solo desde el principio de un número: si no, con una tira larga de cifras se probaba desde cada una y tardaba
# segundos; décima revisión.)
_UNITS = re.compile(r"(?<![0-9.,])(\d+(?:[.,]\d+)?)\s*(?:cm|mm|um|µm|nm|m|in|inch|mil|g|kg|mev|kev|ev)(?![a-z])",
                    re.IGNORECASE)


def _symbol_alias(entry: ListEntry, token: str) -> bool:
    """¿``token`` es el símbolo del elemento ``entry`` («pb», «w», «cm»)? Cuentan menos que una palabra: «Cm» en
    «agua_cm» o «Tl» en «CsI_Tl» no son lo que da nombre al material."""
    return entry.number is not None and entry.number < 100 and len(token) <= 2 and \
        token in ALIASES.get(entry.number, ())


def _word_score(entry: ListEntry, w: str) -> float:
    aliases = ALIASES.get(entry.number, ()) if entry.number is not None else ()
    if w in aliases:
        return 2.0 if _symbol_alias(entry, w) else 3.5
    if any(x == w for x in entry.search_words):
        return 3.0 if len(w) >= 2 else 1.0  # «c» de «C-552» no es como «agua»
    if len(w) >= 3 and any(x.startswith(w) for x in entry.search_words):
        return 2.0
    if len(w) >= 4 and w in entry.search_text:
        return 1.0
    return 0.0


def _name_words(entry: ListEntry) -> list[list[str]]:
    names = [entry.text.rsplit(" (PENELOPE", 1)[0]]
    if entry.number is not None and entry.number in NAMES:
        names.append(NAMES[entry.number][0])
    out = []
    for name in names:
        ws = [w for w in words(re.sub(r"(\d+)", r" \1 ", name)) if w not in _GENERIC]
        if ws:
            out.append(ws)
    return out


def _coverage(entry: ListEntry, tokens: list[str]) -> float:
    """Qué parte del nombre del material dicen las palabras del fichero: «lead» es más «Plomo» que «Óxido de plomo»,
    y «nylon6» más «Nailon 6 y 6/6» que «Nailon 6/10». Un nombre de uso corriente del material («aluminum»,
    «pmma») cuenta como una palabra de su nombre."""
    aliases = {a for a in ALIASES.get(entry.number, ()) if not _symbol_alias(entry, a)} \
        if entry.number is not None else set()
    best = 0.0
    for ws in _name_words(entry):
        hit = sum(1 for w in ws if any(t == w or (len(t) >= 3 and w.startswith(t)) for t in tokens))
        if hit < len(ws) and any(t in aliases and not any(t == w or w.startswith(t) for w in ws) for t in tokens):
            hit += 1
        best = max(best, hit / len(ws))
    return best


def _parts(texts: list[str]) -> tuple[list[str], list[str]]:
    """Las partes de los nombres («hueso_cort.mat» → «hueso», «cort») y cada nombre entero sin separadores
    («C-552» → «c552»)."""
    parts: list[str] = []
    wholes: list[str] = []
    for text in texts:
        stem = re.sub(r"\.mat$", "", text or "", flags=re.IGNORECASE)
        stem = _UNITS.sub(" ", stem)
        compact = re.sub(r"[^0-9a-zñ]", "", norm(stem))
        if compact and compact not in wholes:
            wholes.append(compact)
        for part in re.split(r"[^0-9A-Za-zÀ-ÿñÑ]+", stem):
            if part and norm(part) not in _GENERIC and part not in parts:
                parts.append(part)
    return parts, wholes


def _scores(texts: list[str], entries: list[ListEntry]) -> list[tuple[float, float, ListEntry]]:
    """(puntos, densidad si es por su fórmula, material) de los que se parecen a ``texts``, el más parecido primero.

    Cada parte del nombre puntúa: una fórmula química que es la del material, 5,5 (tal cual) o 5 (reducida); uno de
    sus nombres de uso corriente, 3,5; el símbolo del elemento, 2; una palabra de su nombre, 3 (1 si es de una
    letra); el principio de una, 2; un trozo, 1. Se promedian las partes; una cifra que está en su nombre suma 1, y
    lo que dice de su nombre (:func:`_coverage`), hasta 0,5. Las unidades tras un número («5cm», «1in») no cuentan,
    y una fórmula que no es de la lista (Ti6Al4V) tampoco: sus letras no son símbolos sueltos."""
    parts, wholes = _parts(texts)
    if not parts:
        return []
    known = {w for e in entries for w in e.search_words}
    plain = {w for e in entries for w in e.search_words if len(w) >= 2 and not _symbol_alias(e, w)}
    plan = []  # por parte: («fórmula», claves), («palabras», palabras, cifras), («cifras», …) o («nada», …)
    for part in parts:
        low = norm(part)
        if low.isdigit():
            plan.append(("cifras", [low]))
            continue
        if low not in known:
            keys, sure = formula_keys(part)
            if keys and any(_formula_score(e, keys) > 0 for e in entries):
                plan.append(("fórmula", keys))
                continue
            mixed = part != part.lower() and part != part.upper()
            letters = re.findall(r"[a-zñ]+", low)
            if keys and (sure or mixed or (any(c.isdigit() for c in low) and not any(w in plain for w in letters))):
                # Una fórmula que no es de la lista (Ti6Al4V, o CO2 sin las fórmulas de la base de datos): sus
                # letras no son símbolos sueltos («CO2» no es el cobalto).
                plan.append(("nada", []))
                continue
        runs = re.findall(r"[a-zñ]+|\d+", low)
        alpha = [r for r in runs if not r.isdigit() and r not in _GENERIC]
        digits = [r for r in runs if r.isdigit()]
        plan.append(("palabras", [low] if low in known else alpha, digits))
    counted = [p for p in plan if p[0] != "cifras"]
    if not counted:
        return []
    out = []
    for e in entries:
        total, bonus, tie, by_formula = 0.0, 0.0, 0.0, False
        tokens: list[str] = []
        aliases = ALIASES.get(e.number, ()) if e.number is not None else ()
        for p in plan:
            if p[0] == "fórmula":
                if (fs := _formula_score(e, p[1])) > 0:
                    total += fs
                    by_formula = True
                    tie = e.density or 0.0
            elif p[0] == "palabras":
                ws, digits = p[1], p[2]
                if ws:
                    total += sum(_word_score(e, w) for w in ws) / len(ws)
                    tokens += ws
                tokens += digits
                bonus += sum(1.0 for d in digits if d in e.search_words)
            elif p[0] == "cifras":
                tokens += p[1]
                bonus += sum(1.0 for d in p[1] if d in e.search_words)
        score = total / len(counted)
        for whole in wholes:  # «c552», «a150», «ms20»: el nombre entero, sin guiones
            if len(whole) >= 3 and whole in aliases:
                score = max(score, 3.5)
                tokens.append(whole)
            elif len(whole) >= 3 and whole in e.search_words:
                score = max(score, 3.0)
        if score <= 0:
            continue
        score += bonus + 0.5 * (1.0 if by_formula else _coverage(e, tokens))
        out.append((score, tie, e))
    out.sort(key=lambda x: (-x[0], -x[1], not x[2].personal, x[2].number or 0))
    return out


def suggest(texts: list[str], entries: list[ListEntry], limit: int = 5) -> list[ListEntry]:
    """Los materiales que probablemente son el de un nombre de fichero o una descripción («agua.mat», «hueso_cort»,
    «Cu_lamina», «H2O.mat», «C-552.mat»), los más probables primero. Son propuestas: el usuario las confirma."""
    return [e for _s, _t, e in _scores(texts, entries)[:limit]]


def best_guess(texts: list[str], entries: list[ListEntry]) -> ListEntry | None:
    """La propuesta que se rellena sola («Asignar materiales…»): solo si es la mejor sin empate (con «hueso.mat», el
    compacto y el cortical están igual de cerca: no se elige ninguno). Con una fórmula que es el mismo compuesto en
    dos estados («H2O»: agua líquida y vapor de agua), el más denso (el líquido o el sólido)."""
    ranked = _scores(texts, entries)
    if not ranked:
        return None
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1e-9:
        a, b = ranked[0], ranked[1]
        same = a[1] > b[1] > 0 and _first_english(a[2]) == _first_english(b[2])
        if not same:
            return None
    return ranked[0][2]


def _first_english(e: ListEntry) -> str:
    english = NAMES[e.number][0] if e.number in NAMES else (e.secondary or e.text)
    ws = words(english)
    return ws[0] if ws else ""


def match_spanish(name: str, mlist: list[tuple[int, str]]) -> tuple[int, str] | None:
    """El material de la lista cuyo nombre en español es exactamente ``name`` (sin tildes ni mayúsculas)."""
    key = norm(name).strip()
    if not key:
        return None
    for item in mlist:
        es = spanish_name(*item)
        if es is not None and norm(es) == key:
            return item
    return None


__all__ = ["ALIASES", "ListEntry", "NAMES", "best_guess", "formula_key", "formula_keys", "match_spanish", "norm",
           "penelope_entries", "penelope_label", "personal_entry", "search", "spanish_name", "suggest", "words"]
