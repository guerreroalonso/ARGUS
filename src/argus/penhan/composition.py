"""Composición propia de un material: la opción 1 de penhmat, además de la lista de PENELOPE (opción 2).

penhmat (el programa MATERIAL de penhan, que llama a PEMATW de ``penelope.f`` y a PHMATW de ``penhan.f``) genera un
.mat de dos maneras:

* **opción 2**: el número de un material de la lista de PENELOPE (``database/material-list.txt``, 280 materiales de
  ``pdcompos.pen``), con su composición, su densidad y su energía media de excitación;
* **opción 1**: la composición por teclado: el nombre (como mucho 60 caracteres, solo informativo), el número de
  elementos (de 1 a 30), si se dan átomos por molécula (fórmula química) o fracciones en masa (con un solo elemento
  no lo pregunta), el número atómico y la cantidad de cada elemento, si se cambia la energía media de excitación
  (penhmat propone la de la regla de Bragg con los valores de los elementos de PENELOPE), la densidad, el plasmón
  (Fcb y Wcb) y el nombre del fichero.

Este módulo es la opción 1: :class:`Composition` guarda lo que hay que contestar y lo comprueba antes (un número
atómico fuera de 1-99, una cantidad que no es positiva o un elemento repetido harían que penhmat volviera a preguntar o
se parase con IRETRN 1101/1103). La energía media de excitación puede ser:

* la **regla de ARGUS** (por defecto; la misma que para los tejidos de un TAC, ADR-011): en sólidos y líquidos, la
  aditividad de Bragg con los valores atómicos de fase condensada de ICRU 37, escalada para que el agua líquida dé
  78 eV (ICRU 90); en gases (menos de 0,02 g/cm³) y elementos, la que calcula PENELOPE;
* **la que calcula PENELOPE** (se contesta «no» a cambiarla);
* **un valor** en eV (p. ej. el de ICRU 37 o 90 de ese material).

Se guarda en el propio .in, en comentarios justo detrás de la línea MFNAME del material (penhmain salta las líneas con
la palabra clave en blanco: penhmain.f, «IF(KWORD.EQ.'      ') GO TO 202» y siguientes), con el nombre del fichero
.mat en cada línea para que no se confunda con otro material si alguien cambia el orden a mano::

    MFNAME pmma.mat             [PMMA]
           [ARGUS pmma.mat: composicion por atomos; densidad 1.19 g/cm3; I segun ARGUS]
           [ARGUS pmma.mat: C 5 H 8 O 2]

Las líneas de elementos se parten para no pasar de la columna 127 (lo que lee penhmain en cada línea). Todo en ASCII.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from ..ct.calibration import ENDF_ISOTOPES, PENHMAT_END, PENHMAT_FCB, SYMBOLS
from ..ct.elements import ATOMIC_WEIGHTS, RULE_CONDENSED, RULE_GAS, mean_excitation_energy, penelope_i

BY_ATOMS = "atomos"  # átomos por molécula (fórmula química): penhmat, opción 1 de la segunda pregunta
BY_MASS = "masa"  # fracción en masa de cada elemento: opción 2
I_ARGUS = "argus"
I_PENELOPE = "penelope"
I_VALUE = "valor"
MAX_ELEMENTS = 30  # PEMATW: «NELEM must be positive and less than 31»
NAME_WIDTH = 60  # PEMATW: «no more than 60 characters»
LINE_WIDTH = 127  # penhmain lee las columnas 1-127 de cada línea del .in
TAG = "ARGUS"
COMMENT_INDENT = 7  # palabra clave en blanco (6) y un espacio

_SYMBOL_Z = {s: z for z, s in enumerate(SYMBOLS) if s}
_SYMBOL_UPPER = {s.upper(): z for s, z in _SYMBOL_Z.items()}
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?"
_LINE = re.compile(r"^\[" + TAG + r" (?P<file>[^\s:\]]+): (?P<body>.*?)\]?\s*$")
_HEADER = re.compile(
    r"^composici[oó]n por (?P<by>[aá]tomos|masa)\s*;\s*densidad\s+(?P<rho>" + _NUMBER + r")\s*g/cm3?\s*;"
    r"\s*I\s+(?P<i>seg[uú]n\s+(?:ARGUS|PENELOPE)|" + _NUMBER + r"\s*eV)\s*$", re.IGNORECASE)


def _num(v: float) -> str:
    """Un real como lo lee Fortran en formato libre, exacto y corto («5», «0.111894», «1.2e-05»)."""
    v = float(v)
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    r = repr(v)
    return r.replace("e-0", "e-").replace("e+0", "e").replace("e+", "e")


def _float(text: str) -> float:
    v = float(text.replace("d", "e").replace("D", "e"))
    if not math.isfinite(v):
        raise ValueError(f"«{text}» no es un número")
    return v


def symbol(z: int) -> str:
    return SYMBOLS[z] if 0 < z < len(SYMBOLS) else f"Z={z}"


def element_z(token: str) -> int | None:
    """Número atómico de un símbolo («H», «Ca»; también «ca» o «CA» si no es ambiguo) o de un número («20»)."""
    t = token.strip()
    if not t:
        return None
    if t.isdigit():
        z = int(t)
        return z if 1 <= z <= 99 else None
    if t in _SYMBOL_Z:
        return _SYMBOL_Z[t]
    return _SYMBOL_UPPER.get(t.upper())


def ascii_name(text: str, width: int = NAME_WIDTH) -> str:
    """El nombre para la consola de penhmat: sin tildes ni caracteres que no sean ASCII, como mucho ``width``."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    plain = " ".join(plain.replace("\t", " ").split())
    return plain[:width].rstrip()


# ---------------------------------------------------------------------------------------------------------------------
# Lo que escribe el usuario: «C5H8O2», «H2O», «Ca10(PO4)6(OH)2», «H 0.111894 O 0.888106», «1 2 8 1»…
# ---------------------------------------------------------------------------------------------------------------------

class _FormulaParser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def error(self, what: str) -> ValueError:
        return ValueError(f"{what} (en «{self.s}», posición {self.i + 1})")

    def number(self) -> float | None:
        m = re.compile(r"\d+(?:\.\d+)?").match(self.s, self.i)
        if m is None:
            return None
        self.i = m.end()
        return float(m.group(0))

    def groups(self, closing: str | None) -> dict[int, float]:
        out: dict[int, float] = {}
        while self.i < len(self.s):
            c = self.s[self.i]
            if closing is not None and c == closing:
                return out
            if c in "([":
                self.i += 1
                inner = self.groups(")" if c == "(" else "]")
                if self.i >= len(self.s):
                    raise self.error("falta cerrar un paréntesis")
                self.i += 1
                k = self.number()
                for z, n in inner.items():
                    out[z] = out.get(z, 0.0) + n * (1.0 if k is None else k)
            elif c.isupper():
                m = re.compile(r"[A-Z][a-z]?").match(self.s, self.i)
                sym = m.group(0)
                if sym not in _SYMBOL_Z:
                    raise self.error(f"«{sym}» no es un elemento")
                self.i += len(sym)
                k = self.number()
                z = _SYMBOL_Z[sym]
                out[z] = out.get(z, 0.0) + (1.0 if k is None else k)
            elif c in "·*." and closing is None:
                break
            elif c.islower():
                raise self.error(f"«{c}»: los símbolos de los elementos empiezan con mayúscula (H, C, Ca, Co…)")
            else:
                raise self.error(f"no se entiende «{c}»")
        if closing is not None:
            raise self.error("falta cerrar un paréntesis")
        return out

    def formula(self) -> dict[int, float]:
        total: dict[int, float] = {}
        first = True
        while True:
            k = None if first else self.number()
            part = self.groups(None)
            if not part:
                raise self.error("falta un elemento")
            for z, n in part.items():
                total[z] = total.get(z, 0.0) + n * (1.0 if k is None else k)
            first = False
            if self.i >= len(self.s):
                return total
            self.i += 1  # «·», «*» o «.» de un hidrato: CuSO4·5H2O


def parse_formula(text: str) -> list[tuple[int, float]]:
    """Átomos por molécula de una fórmula química («C5H8O2», «Ca10(PO4)6(OH)2», «CuSO4·5H2O»)."""
    s = re.sub(r"\s+", "", text)
    if not s:
        raise ValueError("Escriba una fórmula química o los elementos con su cantidad.")
    out = _FormulaParser(s).formula()
    return list(out.items())


def _decimal_commas(text: str) -> str:
    """«0,111894» → «0.111894» (una coma entre dos cifras, sin blancos, es la coma decimal)."""
    return re.sub(r"(?<=\d),(?=\d)", ".", text)


def parse_pairs(text: str) -> list[tuple[int, float]] | None:
    """Pares «elemento cantidad» («H 0.111894 O 0.888106», «H: 2, O: 1», «1 2 8 1», «H 11,19 % O 88,81 %»), o
    None si no lo son."""
    tokens = [t for t in re.split(r"[\s,;:=%]+", _decimal_commas(text).strip()) if t]
    if len(tokens) < 2 or len(tokens) % 2:
        return None
    out = []
    for sym, amount in zip(tokens[0::2], tokens[1::2]):
        z = element_z(sym)
        if z is None or not re.fullmatch(_NUMBER, amount):
            return None
        out.append((z, _float(amount)))
    return out


def parse_elements(text: str) -> tuple[list[tuple[int, float]], str | None]:
    """Los elementos de lo que escribe el usuario y en qué vienen las cantidades, si se sabe.

    Una fórmula química, en átomos por molécula (:data:`BY_ATOMS`); con «%», en masa (:data:`BY_MASS`); los demás
    pares «elemento cantidad», None (las cantidades son lo que elija el usuario: átomos o fracciones en masa).
    ``ValueError`` con el motivo si no se entiende.
    """
    pairs = parse_pairs(text)
    if pairs is not None:
        return pairs, (BY_MASS if "%" in text else None)
    return parse_formula(text), BY_ATOMS


def guess_by(elements: list[tuple[int, float]]) -> str | None:
    """Si unos pares «elemento cantidad» parecen átomos (todos enteros) o fracciones en masa (suman 1 o 100)."""
    if not elements:
        return None
    amounts = [float(v) for _z, v in elements]
    total = sum(amounts)
    if all(v.is_integer() for v in amounts) and abs(total - 100.0) > 1e-9:
        return BY_ATOMS
    if abs(total - 1.0) <= 0.02 or abs(total - 100.0) <= 2.0:
        return BY_MASS
    return None


# ---------------------------------------------------------------------------------------------------------------------
# La composición
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Composition:
    """Lo que se contesta a penhmat con la opción 1 (salvo el nombre y el fichero, que son los del material)."""

    elements: list[tuple[int, float]] = field(default_factory=list)  # (Z, cantidad), en el orden del usuario
    by: str = BY_ATOMS
    density: float = 1.0  # g/cm³
    i_mode: str = I_ARGUS
    i_value: float | None = None  # eV, con I_VALUE

    # -- comprobaciones ---------------------------------------------------------------------------------------------
    def problems(self) -> list[str]:
        """Lo que haría que penhmat no la aceptase (vacío si vale)."""
        out = []
        if not self.elements:
            out.append("no tiene ningún elemento")
        if len(self.elements) > MAX_ELEMENTS:
            out.append(f"tiene {len(self.elements)} elementos y penhmat admite {MAX_ELEMENTS}")
        seen: set[int] = set()
        for z, amount in self.elements:
            if not isinstance(z, int) or not 1 <= z <= 99:
                out.append(f"el número atómico {z} no está entre 1 y 99")
                continue
            if z in seen:
                out.append(f"el {symbol(z)} aparece dos veces")
            seen.add(z)
            if not (isinstance(amount, (int, float)) and math.isfinite(amount) and amount > 0):
                what = "la fracción en masa" if self.by == BY_MASS else "el número de átomos"
                out.append(f"{what} del {symbol(z)} tiene que ser positiv{'a' if self.by == BY_MASS else 'o'}")
        if self.by not in (BY_ATOMS, BY_MASS):
            out.append(f"no se sabe si las cantidades son átomos o fracciones en masa («{self.by}»)")
        if not (isinstance(self.density, (int, float)) and math.isfinite(self.density) and self.density > 0):
            out.append("la densidad tiene que ser positiva")
        if self.i_mode == I_VALUE:
            v = self.i_value
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v >= 1.0):
                out.append("la energía media de excitación tiene que ser de al menos 1 eV (penhmat)")
        elif self.i_mode not in (I_ARGUS, I_PENELOPE):
            out.append(f"no se sabe qué energía media de excitación usar («{self.i_mode}»)")
        return out

    def check(self) -> None:
        probs = self.problems()
        if probs:
            raise ValueError("La composición no vale: " + "; ".join(probs) + ".")

    @property
    def ok(self) -> bool:
        return not self.problems()

    # -- cantidades -------------------------------------------------------------------------------------------------
    def mass_fractions(self) -> dict[int, float]:
        """Fracción en masa de cada elemento (suman 1), con los pesos atómicos de PENELOPE."""
        if self.by == BY_MASS:
            raw = {z: float(w) for z, w in self.elements}
        else:
            raw = {z: float(n) * ATOMIC_WEIGHTS[z] for z, n in self.elements}
        s = sum(raw.values())
        return {z: w / s for z, w in raw.items()} if s > 0 else {}

    def atoms(self) -> dict[int, float]:
        """Átomos por molécula: los del usuario o, con fracciones en masa, w/A con el mayor en 1 (como penhmat)."""
        if self.by == BY_ATOMS:
            return {z: float(n) for z, n in self.elements}
        raw = {z: float(w) / ATOMIC_WEIGHTS[z] for z, w in self.elements}
        top = max(raw.values()) if raw else 0.0
        return {z: v / top for z, v in raw.items()} if top > 0 else {}

    def mass_sum(self) -> float | None:
        """Suma de las fracciones en masa que ha escrito el usuario (None si da átomos)."""
        return sum(float(w) for _z, w in self.elements) if self.by == BY_MASS else None

    def isotopes(self) -> set[tuple[int, int]]:
        """Isótopos con datos ENDF de protones que tendrá el .mat (penhmain admite 30 entre todos los materiales)."""
        return {(z, a) for z, _ in self.elements for a in ENDF_ISOTOPES.get(z, ())}

    # -- textos -----------------------------------------------------------------------------------------------------
    def formula(self) -> str:
        """«C5H8O2» (átomos enteros), «C 5,5 · H 8» (no enteros) o «H 11,19 % · O 88,81 %» (fracciones en masa)."""
        if self.by == BY_ATOMS:
            if all(float(n).is_integer() and n < 1e6 for _z, n in self.elements):
                return "".join(symbol(z) + (str(int(n)) if n != 1 else "") for z, n in self.elements)
            return " · ".join(f"{symbol(z)} {_num(n).replace('.', ',')}" for z, n in self.elements)
        return self.mass_text()

    def mass_text(self, digits: int = 2) -> str:
        fr = self.mass_fractions()
        return " · ".join(f"{symbol(z)} {100 * fr[z]:.{digits}f} %".replace(".", ",") for z, _ in self.elements
                          if z in fr)

    def excitation(self) -> tuple[float, bool, str]:
        """(I en eV, si se contesta «sí» a cambiarla, de dónde sale) de lo que tendrá el .mat.

        Con la regla de ARGUS en un gas o un elemento, y con «la que calcula PENELOPE», se contesta «no»: penhmat la
        calcula (la regla de Bragg con los valores atómicos de PENELOPE)."""
        fr = self.mass_fractions()
        if self.i_mode == I_VALUE and self.i_value is not None:
            return float(self.i_value), True, "valor dado"
        if self.i_mode == I_PENELOPE or not fr:
            return (penelope_i(fr) if fr else 0.0), False, "la que calcula PENELOPE"
        value, rule = mean_excitation_energy(fr, self.density)
        if rule == RULE_CONDENSED:
            return round(value, 1), True, "regla de ARGUS para sólidos y líquidos"
        return value, False, "regla de ARGUS: " + ("la de un gas, la de PENELOPE" if rule == RULE_GAS else
                                                   "la del elemento")

    def summary(self) -> str:
        """«C5H8O2, 1,19 g/cm³, I = 74,3 eV» para las tablas y las ayudas."""
        i_val, _yes, _why = self.excitation()
        rho = _num(self.density).replace(".", ",")
        return f"{self.formula()}, {rho} g/cm³, I = {f'{i_val:.1f}'.replace('.', ',')} eV"

    # -- penhmat ----------------------------------------------------------------------------------------------------
    def penhmat_dialog(self, mat_file: str, name: str = "") -> list[tuple[str, str | None]]:
        """(texto para el usuario, respuesta que se escribe en penhmat o None) de cada pregunta de penhmat."""
        self.check()
        name = ascii_name(name) or ascii_name(mat_file.rsplit(".", 1)[0]) or "material"
        steps: list[tuple[str, str | None]] = [
            ("Opción 1 (composición por teclado).", "1"),
            (f"Nombre del material: {name}", name),
            (f"Número de elementos: {len(self.elements)}", str(len(self.elements)))]
        if len(self.elements) > 1:
            if self.by == BY_ATOMS:
                steps.append(("Opción 1 (fórmula química: átomos por molécula).", "1"))
                steps.append(("Número atómico y átomos por molécula de cada elemento (una línea por elemento):", None))
            else:
                steps.append(("Opción 2 (fracción en masa de cada elemento).", "2"))
                steps.append(("Número atómico y fracción en masa de cada elemento (una línea por elemento):", None))
            for z, amount in self.elements:
                steps.append((f"  {z} {_num(amount)}      ({symbol(z)})", f"{z} {_num(amount)}"))
        else:
            steps.append((f"Número atómico: {self.elements[0][0]} ({symbol(self.elements[0][0])})",
                          str(self.elements[0][0])))
        i_val, change, why = self.excitation()
        if change:
            text = f"{i_val:.1f}" if self.i_mode == I_ARGUS else _num(i_val)
            steps.append((f"¿Cambiar la energía media de excitación? 1 (sí) y {text} eV ({why})", None))
            steps.append(("", "1"))
            steps.append(("", text))
        else:
            steps.append((f"¿Cambiar la energía media de excitación? 2 (no): {why} ({i_val:.1f} eV)", "2"))
        steps.append((f"Densidad: {_num(self.density)} g/cm³", _num(self.density)))
        steps += [(PENHMAT_FCB, "2"), (f"Nombre del fichero de salida: {mat_file}", mat_file), (PENHMAT_END, "")]
        return steps

    def penhmat_steps(self, mat_file: str, name: str = "") -> list[str]:
        """Las respuestas que hay que dar a penhmat, en el orden en que pregunta (para materiales.txt)."""
        return [text for text, _ans in self.penhmat_dialog(mat_file, name) if text]

    def penhmat_input(self, mat_file: str, name: str = "") -> str:
        """Lo que se escribe en penhmat por la entrada estándar (una respuesta por línea)."""
        return "\n".join(ans for _text, ans in self.penhmat_dialog(mat_file, name) if ans is not None) + "\n"

    def i_value_written(self) -> float:
        """La energía media de excitación que tendrá el .mat (la que se da a penhmat o la que calcula)."""
        i_val, change, _why = self.excitation()
        if change and self.i_mode == I_ARGUS:
            return float(f"{i_val:.1f}")
        return i_val

    # -- comentarios del .in ----------------------------------------------------------------------------------------
    def header_text(self) -> str:
        if self.i_mode == I_VALUE:
            i_text = f"{_num(self.i_value)} eV"
        else:
            i_text = "segun PENELOPE" if self.i_mode == I_PENELOPE else "segun ARGUS"
        return f"composicion por {self.by}; densidad {_num(self.density)} g/cm3; I {i_text}"

    def comment_texts(self, fname: str) -> list[str]:
        """Los textos de las líneas de comentario del .in (sin la palabra clave en blanco), en ASCII."""
        self.check()
        head = f"[{TAG} {fname}: "
        out = [head + self.header_text() + "]"]
        room = LINE_WIDTH - COMMENT_INDENT - len(head) - 1
        current = ""
        for z, amount in self.elements:
            tok = f"{symbol(z)} {_num(amount)}"
            if current and len(current) + 1 + len(tok) > room:
                out.append(head + current + "]")
                current = tok
            else:
                current = f"{current} {tok}" if current else tok
        if current:
            out.append(head + current + "]")
        return out

    @classmethod
    def from_comment_bodies(cls, bodies: list[str]) -> Composition:
        """La composición de los textos de sus comentarios (lo que va tras «[ARGUS fichero.mat: »).

        ``ValueError`` con el motivo si no se entienden."""
        header = None
        elements: list[tuple[int, float]] = []
        for body in bodies:
            b = body.strip()
            m = _HEADER.match(b)
            if m is not None:
                if header is not None:
                    raise ValueError("tiene dos líneas «composicion por …»")
                header = m
                continue
            pairs = parse_pairs(b)
            if pairs is None:
                raise ValueError(f"no se entiende «{b}»")
            elements += pairs
        if header is None:
            raise ValueError("falta la línea «composicion por …; densidad …; I …»")
        by = BY_MASS if header.group("by").lower() == "masa" else BY_ATOMS
        i_text = header.group("i").lower()
        if "argus" in i_text:
            i_mode, i_value = I_ARGUS, None
        elif "penelope" in i_text:
            i_mode, i_value = I_PENELOPE, None
        else:
            i_mode, i_value = I_VALUE, _float(i_text.replace("ev", "").strip())
        comp = cls(elements, by, _float(header.group("rho")), i_mode, i_value)
        probs = comp.problems()
        if probs:
            raise ValueError("; ".join(probs))
        return comp

    # -- comparación con un .mat ------------------------------------------------------------------------------------
    def differences(self, mat: MatComposition) -> list[str]:
        """Lo que tiene distinto el .mat (vacío si es esta composición): elementos, densidad, I."""
        out = []
        mine = self.atoms()
        if set(mine) != set(mat.atoms):
            have = ", ".join(symbol(z) for z in sorted(mat.atoms)) or "ninguno"
            out.append(f"otros elementos ({have})")
        else:
            top_a = max(mine.values())
            top_b = max(mat.atoms.values())
            if any(abs(mine[z] / top_a - mat.atoms[z] / top_b) > 2e-6 * max(1.0, mine[z] / top_a) for z in mine):
                out.append("otras proporciones de los elementos")
        if mat.density is None or abs(mat.density - self.density) > 2e-6 * self.density:
            out.append(f"otra densidad ({_num(mat.density).replace('.', ',')} g/cm³)" if mat.density is not None
                       else "sin densidad")
        want = self.i_value_written()
        if mat.ivalue is not None and want > 0 and abs(mat.ivalue - want) > 1e-5 * want:
            out.append(f"otra energía media de excitación ({mat.ivalue:.1f} eV)".replace(".", ","))
        return out


@dataclass
class MatComposition:
    """La cabecera de un .mat de PENELOPE: densidad, átomos por molécula de cada elemento y I."""

    density: float | None
    atoms: dict[int, float]
    ivalue: float | None


_MAT_DENSITY = re.compile(r"Mass density\s*=\s*(" + _NUMBER + ")")
_MAT_NELEM = re.compile(r"Number of elements in the molecule\s*=\s*(\d+)")
_MAT_ELEMENT = re.compile(r"atomic number\s*=\s*(\d+)\s*,\s*atoms/molecule\s*=\s*(" + _NUMBER + ")")
_MAT_I = re.compile(r"Mean excitation energy\s*=\s*(" + _NUMBER + ")")


def read_mat_composition(path) -> MatComposition | None:
    """Composición de la cabecera de un .mat (None si no es un .mat de PENELOPE o no se puede leer)."""
    try:
        with open(path, encoding="latin-1", errors="replace") as f:
            head = [f.readline() for _ in range(40)]
    except OSError:
        return None
    if not head or "Material data file" not in head[0]:
        return None
    density = ivalue = None
    n = None
    atoms: dict[int, float] = {}
    for ln in head[1:]:
        if density is None and (m := _MAT_DENSITY.search(ln)):
            density = _float(m.group(1))
        elif n is None and (m := _MAT_NELEM.search(ln)):
            n = int(m.group(1))
        elif (m := _MAT_ELEMENT.search(ln)):
            atoms[int(m.group(1))] = _float(m.group(2))
        elif (m := _MAT_I.search(ln)):
            ivalue = _float(m.group(1))
            break
    if n is None or len(atoms) != n:
        return None
    return MatComposition(density, atoms, ivalue)


# ---------------------------------------------------------------------------------------------------------------------
# Las líneas del .in
# ---------------------------------------------------------------------------------------------------------------------

def comment_parts(text: str) -> tuple[str, str] | None:
    """(fichero .mat, resto) de una línea de comentario de composición de ARGUS («[ARGUS f.mat: …]»), o None."""
    m = _LINE.match(text.strip())
    if m is None:
        return None
    return m.group("file"), m.group("body")


__all__ = ["BY_ATOMS", "BY_MASS", "Composition", "I_ARGUS", "I_PENELOPE", "I_VALUE", "MAX_ELEMENTS",
           "MatComposition", "NAME_WIDTH", "TAG", "ascii_name", "comment_parts", "element_z", "guess_by",
           "parse_elements", "parse_formula", "parse_pairs", "read_mat_composition", "symbol"]
