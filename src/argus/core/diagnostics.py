"""Diagnósticos (errores y avisos) con posición en el fichero.

Cada diagnóstico lleva, cuando procede, el código ``IRETRN`` con el que
PENGEOM o penhmain abortarían, para que el usuario pueda relacionar el aviso
de ARGUS con el mensaje del programa Fortran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2  # PENGEOM/penhmain abortarían con este fichero.


@dataclass
class Diagnostic:
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None  # 0-based
    code: int | None = None  # IRETRN equivalente, si existe
    fortran_reason: str | None = None  # texto REASON de PENGEOM/penhmain

    def location(self) -> str:
        where = self.file or ""
        if self.line is not None:
            where = f"{where}:{self.line + 1}" if where else f"línea {self.line + 1}"
        return where

    def __str__(self) -> str:  # pragma: no cover - solo presentación
        tag = {Severity.INFO: "info", Severity.WARNING: "aviso", Severity.ERROR: "ERROR"}[self.severity]
        code = f" [IRETRN {self.code}]" if self.code else ""
        loc = self.location()
        loc = f"{loc}: " if loc else ""
        return f"{loc}{tag}{code}: {self.message}"


@dataclass
class DiagnosticList:
    items: list[Diagnostic] = field(default_factory=list)

    def add(self, severity: Severity, message: str, **kw) -> Diagnostic:
        d = Diagnostic(severity, message, **kw)
        self.items.append(d)
        return d

    def error(self, message: str, **kw) -> Diagnostic:
        return self.add(Severity.ERROR, message, **kw)

    def warning(self, message: str, **kw) -> Diagnostic:
        return self.add(Severity.WARNING, message, **kw)

    def info(self, message: str, **kw) -> Diagnostic:
        return self.add(Severity.INFO, message, **kw)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == Severity.ERROR]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == Severity.ERROR for d in self.items)

    def extend(self, other: DiagnosticList) -> None:
        self.items.extend(other.items)

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)
