"""Números en los textos de la interfaz, como se escriben en español.

* Enteros: los de cinco cifras o más, con los millares separados por un espacio fino que no se parte al final de una
  línea («43 200», «1 000 000»); los de cuatro, juntos («1000», «2259»), como recomienda la RAE.
* Reales: con coma decimal («0,5 cm»).
"""

from __future__ import annotations

import math

THIN_NBSP = " "  # espacio fino que no se parte


def fmt_int(n: int | float) -> str:
    """«2259», «43 200», «-12 500»."""
    n = int(n)
    s = str(abs(n))
    if len(s) >= 5:
        groups = []
        while s:
            groups.append(s[-3:])
            s = s[:-3]
        s = THIN_NBSP.join(reversed(groups))
    return ("-" if n < 0 else "") + s


def fmt_num(v: float, digits: int = 4) -> str:
    """Un real con ``digits`` cifras significativas y coma decimal («0,5», «1,234e-05»)."""
    if not math.isfinite(v):
        return str(v)
    return f"{v:.{digits}g}".replace(".", ",")


def fmt_count(n: float) -> str:
    """Un número de lluvias o de partículas: entero, con sus millares, hasta mil millones («200 000», «1500»); más,
    en notación científica corta («2e9», «1,5e10»), como se escribe en el .in."""
    v = float(n)
    if not math.isfinite(v):
        return str(v)
    if abs(v) < 1e9:
        return fmt_int(round(v)) if abs(v - round(v)) < 1e-6 * max(abs(v), 1.0) else fmt_num(v, 6)
    m, e = f"{v:.3g}".split("e")
    return f"{m.replace('.', ',')}e{int(e)}"


def fmt_energy(v: float, digits: int = 6) -> str:
    """Una energía en eV con la unidad que le va («1,5 MeV», «661,657 keV», «50 eV»)."""
    a = abs(v)
    for factor, unit in ((1e9, "GeV"), (1e6, "MeV"), (1e3, "keV")):
        if a >= factor:
            return f"{fmt_num(v / factor, digits)} {unit}"
    return f"{fmt_num(v, digits)} eV"


def lower_first(text: str) -> str:
    """Para seguir una frase: «Material» → «material», «Datos ENDF» → «datos ENDF»; una sigla se queda («TAC
    (vóxeles)»)."""
    return text[:1].lower() + text[1:] if len(text) > 1 and text[1:2].islower() else text


def plural(n: int, one: str, many: str | None = None) -> str:
    """«1 lluvia», «2 lluvias», «1 ejecución», «3 ejecuciones» (``many`` si no basta con añadir «s»)."""
    word = one if abs(int(n)) == 1 else (many if many is not None else one + "s")
    return f"{fmt_int(n)} {word}"


__all__ = ["THIN_NBSP", "fmt_count", "fmt_energy", "fmt_int", "fmt_num", "lower_first", "plural"]
