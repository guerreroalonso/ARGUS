"""Cuadro numérico con decimales que acepta el punto y la coma (en español, «12.5» no es 125)."""

from __future__ import annotations

import math
import re

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox

# Un número completo y el principio de uno (lo que se lleva escrito), ya con el punto como separador decimal.
_FULL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_PARTIAL = re.compile(r"[+-]?(?:\d*\.?\d*|(?:\d+\.?\d*|\.\d+)[eE][+-]?)")


def plain_number(text: str) -> str | None:
    """``text`` con el punto como separador decimal y sin separadores de miles; None si no puede ser un número.

    Un solo separador (punto o coma) es siempre el decimal: «12.5» y «12,5» son 12,5. Con los dos, el último es el
    decimal y los otros separan miles de tres en tres: «1.000,5» y «1,000.5» son 1000,5 (y «1.00,5» no vale).
    """
    mant, e, exp = text.partition("e") if "e" in text else text.partition("E")
    if "." in mant and "," in mant:
        k = max(mant.rfind("."), mant.rfind(","))
        grp = "," if mant[k] == "." else "."
        head, frac = mant[:k], mant[k + 1:]
        if mant[k] in head:
            return None
        sign = head[:1] if head[:1] in ("+", "-") else ""
        groups = head[len(sign):].split(grp)
        if not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])):
            return None
        mant = sign + "".join(groups) + "." + frac
    else:
        mant = mant.replace(",", ".")
    return mant + e + exp


class DecimalSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox que lee «12.5» y «12,5» igual, con cualquier idioma del sistema.

    Con el idioma español, Qt toma el punto como separador de miles: «12.5» se
    leía como 125 y «0.001» como 1. Aquí un punto o una coma sueltos son siempre
    el separador decimal; con los dos («1.000,5»), el último es el decimal (véase
    ``plain_number``). También vale la notación científica («1e3», «2,5E-2»). Lo
    que no se puede escribir con los decimales del cuadro no se acepta (la tecla
    no entra) en vez de redondearse sin avisar. El valor se sigue mostrando como
    en el sistema.
    """

    def _number_text(self, text: str) -> str:
        t = text
        if self.prefix() and t.startswith(self.prefix()):
            t = t[len(self.prefix()):]
        if self.suffix() and t.endswith(self.suffix()):
            t = t[:-len(self.suffix())]
        return t.strip().replace("−", "-").replace(" ", "")

    def validate(self, text: str, pos: int):
        t = plain_number(self._number_text(text))
        if t is None:
            return QValidator.State.Invalid, text, pos
        if not _FULL.fullmatch(t):
            state = QValidator.State.Intermediate if _PARTIAL.fullmatch(t) else QValidator.State.Invalid
            return state, text, pos
        v = float(t)
        if not math.isfinite(v):  # cientos de cifras: no cabe en un real
            return QValidator.State.Invalid, text, pos
        d = self.decimals()
        mant = t.split("e")[0].split("E")[0]
        if "e" in t.lower():
            if abs(v - round(v, d)) > 1e-12 * max(1.0, abs(v)):
                return QValidator.State.Invalid, text, pos  # p. ej. 1e-7 con 6 decimales: sería 0
        elif "." in mant and len(mant.split(".", 1)[1]) > d:
            return QValidator.State.Invalid, text, pos
        if v < self.minimum() or v > self.maximum():
            return QValidator.State.Intermediate, text, pos
        return QValidator.State.Acceptable, text, pos

    def valueFromText(self, text: str) -> float:  # noqa: N802 - nombre de Qt
        t = plain_number(self._number_text(text))
        try:
            return float(t) if t is not None else self.value()
        except ValueError:
            return self.value()


__all__ = ["DecimalSpinBox", "plain_number"]
