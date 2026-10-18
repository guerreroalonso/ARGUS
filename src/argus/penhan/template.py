"""Fichero de entrada nuevo para una geometría (plantilla de penhmain-layout.in)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .inputfile import InDocument, InLine, format_line, parse_in_text
from .keywords import SPEC

# Parámetros de simulación de partida (los del caso de protones del usuario,
# 100Mev15-174/maniqui.in); se pueden cambiar en el .in.
MSIMPA_DEFAULT = [10e3, 2e3, 10e3, 0.10, 0.10, 2e3, 2e3]
MSIMPH_DEFAULT = [1e5, 1e5, 1e5, 0.05, 0.05, 2e3]
MSIMPN_DEFAULT = [1.0, 500.0]


def _line(kw: str, values: Sequence | None = None, text: str = "") -> str:
    spec = SPEC[kw.ljust(6)]
    ln = InLine(kw=spec.kw, values=list(values or []), text=text, comment=spec.comment)
    return format_line(ln)


def new_input_text(geomfn: str, materials: Sequence[str], *, title: str | None = None, kparp: int = 4,
                   energy: float = 100e6, position: Sequence[float] = (0.0, 0.0, 0.0),
                   parinp: Mapping[int, float] | None = None, newline: str = "\r\n") -> str:
    """Texto de un .in mínimo y válido para ``geomfn``.

    ``materials``: nombres de los ficheros .mat, en el orden de los números de
    material de la geometría (MFNAME N corresponde al material N).
    ``parinp``: valores PARINP que necesita la geometría ({índice: valor}).
    """
    title = (title or f"Caso de ARGUS para {geomfn}")[:65]
    out = [
        f"TITLE  {title}",
        "       .",
        "       >>>>>>>> Source definition.",
        _line("SKPAR", [kparp]),
        _line("SENERG", [energy]),
        _line("SPOSIT", list(position)),
        _line("SCONE", [0.0, 0.0, 0.0]),
        "       .",
        "       >>>>>>>> Material data and simulation parameters.",
    ]
    for name in materials or ["material1.mat"]:
        out += [
            _line("MFNAME", text=name),
            _line("MSIMPA", MSIMPA_DEFAULT),
            _line("MSIMPH", MSIMPH_DEFAULT),
            _line("MSIMPN", MSIMPN_DEFAULT),
        ]
    out += [
        "       .",
        "       >>>>>>>> Geometry and local simulation parameters.",
        _line("GEOMFN", text=geomfn),
        *(_line("PARINP", [int(i), float(v)]) for i, v in sorted((parinp or {}).items())),
        "       .",
        "       >>>>>>>> Job properties.",
        _line("NSIMSH", [1.0e6]),
        _line("TIME", [1.0e9]),
    ]
    return newline.join(out) + newline


def new_input_document(geomfn: str, materials: Sequence[str], path: str | None = None, **kw) -> InDocument:
    return parse_in_text(new_input_text(geomfn, materials, **kw), path)
