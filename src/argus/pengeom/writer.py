"""Escritura de ficheros .geo.

Principios:

* Un documento sin modificar se reescribe **byte a byte** (se reutilizan las
  líneas originales, con sus finales de línea y comentarios).
* En un elemento modificado (``element.modified = True``) solo se reescriben
  las líneas cuyos valores cambian: las demás se copian tal cual; la que
  cambia conserva el texto que va detrás de sus campos (p. ej. el
  «(DEFAULT=1.0)» de layout.geo) y los comentarios «C» se quedan donde
  estaban. La cabecera se conserva entera (aunque pase de la columna 70) si
  no cambian la palabra clave, la etiqueta ni la descripción.
* Los elementos nuevos se escriben en el formato canónico de PENGEOM.
* Los formatos son los de GEOMIN (columnas fijas): palabra clave A8, etiqueta
  en las columnas 10-13, valores ``E22.15`` en las columnas 10-31, índice
  PARINP ``I4`` en las 33-36, unidad angular en las 37-44.
* Una línea original sin fin de línea (la última del fichero, o una que acaba
  en un CR más allá de la columna 72, que PENGEOM ignora) lleva uno si detrás
  se escribe otra: si no, PENGEOM las leería como una sola.
"""

from __future__ import annotations

from ..core.fortran_io import (
    RECORD_LENGTH,
    A,
    E,
    FortranReadError,
    I,
    X,
    format_e22_15,
    read_record,
    record,
    record_end,
)
from .model import (
    COEF_KEYWORDS,
    KEYWORD_OF,
    KW_ONES,
    PARAM_KEYWORDS,
    BodyElement,
    CloneElement,
    CtBoxElement,
    Element,
    EndElement,
    GeoDocument,
    IncludeElement,
    ModuleElement,
    ParamLine,
    RefLine,
    SurfaceElement,
)
from .parser import is_comment

_PARAM_FMT = [A(8), X(1), E(22, 15), X(1), I(4), A(8)]
_INDICES_FMT = [A(8)] + [A(1), I(2)] * 5 + [A(1)]
_PARAM_NAMES = {**PARAM_KEYWORDS, **COEF_KEYWORDS}

SEPARATOR_LINE = "0" * 64
ONES_LINE = "1" * 64
END_LINE = "END      " + "0" * 55


def format_value(value: float) -> str:
    """Valor E22.15 con signo explícito: ``+2.500000000000000E+02``."""
    text = format_e22_15(value)
    if text.startswith(" ") and not text.lstrip().startswith("-"):
        text = "+" + text[1:]
    return text


def format_label(label: str | None) -> str:
    return (label or "").rjust(4)[:4] if (label or "").strip() else (label or "    ").ljust(4)[:4]


def param_line(p: ParamLine) -> str:
    kw = KEYWORD_OF[p.name]
    line = f"{kw}({format_value(p.value)},{p.ipar:4d})"
    if p.is_angle:
        line += " RAD" if p.unit == "RAD" else " DEG"
    return line


def element_head(keyword: str, label: str | None, description: str) -> str:
    head = f"{keyword}({format_label(label)})"
    desc = description.rstrip()
    if desc:
        head += "   " + desc.lstrip() if not description.startswith(" ") else description.rstrip()
    return head


def note_text(note: str) -> str:
    """Texto libre tras la etiqueta (descripción de un elemento o nota de la línea MODULE de un CLONE)."""
    if not note.strip():
        return ""
    return note.rstrip() if note.startswith(" ") else "   " + note.strip()


def ref_line(ref: RefLine) -> str:
    lab = format_label(ref.label)
    if ref.is_surface:
        kw = "SURFACE*" if ref.keyword == "SURFACE*" else "SURFACE "
        side = ref.side if ref.side is not None else 1
        return f"{kw}({lab}), SIDE POINTER=({side:+d})"
    kw = "BODY    " if ref.keyword == "BODY" else "MODULE  "
    return f"{kw}({lab})"


def element_lines(el: Element) -> list[str]:
    """Líneas canónicas de un elemento (sin fin de línea)."""
    out: list[str] = []
    if isinstance(el, SurfaceElement):
        out.append(element_head("SURFACE*" if el.starred else "SURFACE ", el.label, el.description))
        i = el.indices
        out.append("INDICES=(" + ",".join(f"{k:2d}" for k in i) + ")")
        for p in el.params:
            out.append(param_line(p))
        if el.implicit and (el.has_ones_line or el.transform):
            out.append(ONES_LINE)
            for p in el.transform:
                out.append(param_line(p))
        out.append(SEPARATOR_LINE)
    elif isinstance(el, BodyElement):
        out.append(element_head("BODY    ", el.label, el.description))
        out.append(f"MATERIAL({el.material:4d})")
        for r in el.items:
            out.append(ref_line(r))
        out.append(SEPARATOR_LINE)
    elif isinstance(el, ModuleElement):
        out.append(element_head("MODULE  ", el.label, el.description))
        out.append(f"MATERIAL({el.material:4d})")
        for r in el.items:
            out.append(ref_line(r))
        if el.has_ones_line or el.transform:
            out.append(ONES_LINE)
            for p in el.transform:
                out.append(param_line(p))
        out.append(SEPARATOR_LINE)
    elif isinstance(el, CloneElement):
        out.append(element_head("CLONE   ", el.label, el.description))
        out.append(f"MODULE  ({format_label(el.module_label)})" + note_text(el.module_note))
        if el.has_ones_line or el.transform:
            out.append(ONES_LINE)
            for p in el.transform:
                out.append(param_line(p))
        out.append(SEPARATOR_LINE)
    elif isinstance(el, IncludeElement):
        out.append("INCLUDE*" if el.starred else "INCLUDE ")
        out.append(f"   FILE=({el.filename[:12].ljust(12)})")
        out.append(SEPARATOR_LINE)
    elif isinstance(el, CtBoxElement):
        out.append(element_head("CTBOX   ", el.label, el.description))
        out.append(f"CTFNAME=({el.filename[:12].ljust(12)})")
        if el.has_ones_line or el.transform:
            out.append(ONES_LINE)
            for p in el.transform:
                out.append(param_line(p))
        out.append(SEPARATOR_LINE)
    elif isinstance(el, EndElement):
        out.append(END_LINE)
    return out


def _span_end(el: Element) -> int:
    if isinstance(el, EndElement):
        return el.first_line
    if el.terminator_line is not None:
        return el.terminator_line
    return el.last_line


# -- Reescritura de un elemento modificado línea a línea -----------------------------------------------------------

def _content(raw: str) -> str:
    """Texto de un registro sin su fin de línea."""
    return raw.rstrip("\n").removesuffix("\r")


def _param_fields(raw: str) -> tuple[str | None, float, int, str] | None:
    try:
        kw, value, ipar, angle = read_record(raw, _PARAM_FMT)
    except FortranReadError:
        return None
    return _PARAM_NAMES.get(kw), value, ipar, "RAD" if angle == ") RAD   " else "DEG"


def _same_param(fields, p: ParamLine) -> bool:
    return (fields is not None and fields[0] == p.name and fields[1] == p.value and fields[2] == p.ipar
            and (not p.is_angle or fields[3] == p.unit))


def _ref_fields(raw: str) -> tuple[str, str, int | None] | None:
    rec = record(raw)
    kw = rec[:8]
    if kw in ("SURFACE ", "SURFACE*"):
        try:
            side = read_record(rec, [X(30), I(2)])[0]
        except FortranReadError:
            return None
        return kw.strip(), rec[9:13], side
    if kw in ("BODY    ", "MODULE  "):
        return kw.strip(), rec[9:13], None
    return None


def _with_tail(canonical: str, raw: str | None, reads_back=None) -> str:
    """``canonical`` (los campos que lee GEOMIN) seguido del texto que la línea original tenía detrás de ellos.

    ``reads_back(texto)`` comprueba que GEOMIN leería lo mismo; si el texto de detrás lo estropea (p. ej. pega
    letras a la unidad «) RAD»), se separa con blancos y, si aun así no vale, se escribe solo lo canónico.
    """
    if raw is None:
        return canonical
    tail = _content(raw)[len(canonical):]
    if not tail.strip():
        return canonical
    for cand in (canonical + tail, canonical + "   " + tail.lstrip()):
        if reads_back is None or reads_back(cand):
            return cand
    return canonical


class _Plan:
    """Líneas de un elemento modificado: las originales que no cambian, las cambiadas y los comentarios en su sitio."""

    def __init__(self, lines: list[str], first: int, end: int):
        self.lines = lines
        self.first = first
        self.end = end
        self.comments = [i for i in range(first + 1, end + 1) if is_comment(record(lines[i]))]
        skip = set(self.comments)
        self.data = [i for i in range(first + 1, end + 1) if i not in skip]
        self.used: set[int] = set()
        self.done = 0  # comentarios ya escritos
        self.out: list[tuple[bool, str]] = []  # (original, texto): original = registro tal cual

    def _comments_before(self, r: int) -> None:
        while self.done < len(self.comments) and self.comments[self.done] < r:
            self.out.append((True, self.lines[self.comments[self.done]]))
            self.done += 1

    def keep(self, r: int) -> None:
        self._comments_before(r)
        self.used.add(r)
        self.out.append((True, self.lines[r]))

    def put(self, text: str, r: int | None = None) -> None:
        if r is not None:
            self._comments_before(r)
            self.used.add(r)
        self.out.append((False, text))

    def orig(self, r: int | None) -> int | None:
        """``r`` si es una línea de datos original de este elemento aún sin usar."""
        return r if r is not None and r in self.data and r not in self.used else None

    def find(self, pred) -> int | None:
        return next((r for r in self.data if r not in self.used and pred(self.lines[r])), None)

    def finish(self) -> list[tuple[bool, str]]:
        self._comments_before(self.end + 1)
        return self.out


def _param_rewrite(p: ParamLine, raw: str | None) -> str:
    """La línea de un parámetro que cambia, con el texto que la de antes tenía detrás de sus campos.

    Lo de detrás empieza tras el «)» de la columna 37 y tras la unidad (« DEG» o « RAD»), si la tenía: así, al
    reescribir un ángulo que no llevaba unidad, ese texto no se pega a la « DEG» nueva."""
    canon = param_line(p)

    def reads_back(t: str) -> bool:
        return _same_param(_param_fields(t), p)

    if raw is None:
        return canon
    c = _content(raw)
    if len(c) <= 37 or c[36] != ")":
        return _with_tail(canon, raw, reads_back)
    rest = c[37:]
    if rest[:4].upper() in (" DEG", " RAD"):
        rest = rest[4:]
    if not rest.strip():
        return canon
    for cand in (canon + (rest if rest[:1].isspace() else " " + rest), canon + "   " + rest.lstrip()):
        if reads_back(cand):
            return cand
    return canon


def _plan_params(plan: _Plan, params: list[ParamLine]) -> None:
    for p in params:
        r = plan.orig(p.line)
        raw = plan.lines[r] if r is not None else None
        if raw is not None and _same_param(_param_fields(raw), p):
            plan.keep(r)
        else:
            plan.put(_param_rewrite(p, raw), r)


def _plan_refs(plan: _Plan, items: list[RefLine]) -> None:
    for ref in items:
        want = (ref.keyword, ref.label, ref.side if ref.is_surface else None)
        own = plan.orig(ref.line)
        r = own
        if r is None or _ref_fields(plan.lines[r]) != want:
            r = plan.find(lambda raw, w=want: _ref_fields(raw) == w)
        if r is not None:
            plan.keep(r)
            continue
        f = _ref_fields(plan.lines[own]) if own is not None else None
        if f is not None and f[0] == want[0] and f[2] == want[2]:
            # Su línea, con otra etiqueta (se ha cambiado): solo cambian las columnas 10-13.
            raw = _content(plan.lines[own])
            plan.put(raw[:9] + format_label(ref.label) + raw[13:], own)
            continue
        r = plan.find(lambda raw, w=want: (_ref_fields(raw) or ("",))[:2] == w[:2])  # cambia el lado
        text = ref_line(ref)
        plan.put(_with_tail(text, plan.lines[r] if r is not None else None), r)


def _plan_simple(plan: _Plan, r: int | None, text: str, same: bool) -> None:
    r = plan.orig(r)
    if r is not None and same:
        plan.keep(r)
    else:
        plan.put(_with_tail(text, plan.lines[r] if r is not None else None), r)


def _modified_element(el: Element, lines: list[str], end: int) -> list[tuple[bool, str]]:
    """Líneas de un elemento modificado que ya estaba en el fichero (desde su palabra clave hasta los ceros)."""
    plan = _Plan(lines, el.first_line, end)
    gen = element_lines(el)
    head = record(lines[el.first_line])
    if isinstance(el, EndElement):
        return [(True, lines[el.first_line])] if head[:8] == "END     " else [(False, END_LINE)]
    only_label = False
    if isinstance(el, IncludeElement):
        same_head = head[:8] == gen[0][:8]
    else:
        same_rest = (head[:8] == gen[0][:8] and head[8] == "(" and head[13] == ")"
                     and head[14:70].rstrip() == el.description)
        same_head = same_rest and head[9:13] == el.label
        only_label = same_rest and not same_head
    if same_head:
        plan.keep(el.first_line)
    elif only_label:  # solo cambia la etiqueta: las columnas 10-13 (lo de detrás de la columna 70 se queda)
        raw = _content(lines[el.first_line])
        plan.put(raw[:9] + format_label(el.label) + raw[13:], el.first_line)
    else:
        plan.put(gen[0], el.first_line)
    if isinstance(el, SurfaceElement):
        r = plan.find(lambda raw: record(raw)[:8] == "INDICES=")
        try:
            same = r is not None and tuple(read_record(lines[r], _INDICES_FMT)[2::2]) == tuple(el.indices)
        except FortranReadError:
            same = False
        _plan_simple(plan, r, gen[1], same)
        _plan_params(plan, el.params)
    elif isinstance(el, BodyElement | ModuleElement):
        r = el.material_line
        same = False
        if plan.orig(r) is not None:
            try:
                same = read_record(lines[r], [A(8), X(1), I(4)])[1] == el.material
            except FortranReadError:
                same = False
        _plan_simple(plan, r, f"MATERIAL({el.material:4d})", same)
        _plan_refs(plan, el.items)
    elif isinstance(el, CloneElement):
        r = el.module_line
        ro = plan.orig(r)
        text = f"MODULE  ({format_label(el.module_label)})"
        if ro is None or record(lines[ro])[14:70].rstrip() == el.module_note:
            _plan_simple(plan, r, text, ro is not None and record(lines[ro])[9:13] == el.module_label)
        else:  # otra nota detrás de la etiqueta: se escribe la nueva (sin el texto de antes)
            plan.put(text + note_text(el.module_note), ro)
    elif isinstance(el, IncludeElement | CtBoxElement):
        r = el.file_line
        same = plan.orig(r) is not None and record(lines[r])[9:21].strip() == el.filename.strip()
        _plan_simple(plan, r, gen[1], same)
    transform = getattr(el, "transform", [])
    if (isinstance(el, SurfaceElement) and el.implicit and (el.has_ones_line or el.transform)) or (
            isinstance(el, ModuleElement | CloneElement | CtBoxElement) and (el.has_ones_line or el.transform)):
        r = plan.find(lambda raw: record(raw)[:8] == KW_ONES)
        _plan_simple(plan, r, ONES_LINE, True)
        _plan_params(plan, transform)
    r = el.terminator_line
    _plan_simple(plan, r, SEPARATOR_LINE, plan.orig(r) is not None and record(lines[r])[:8] == "00000000")
    return plan.finish()


def write_geo_text(doc: GeoDocument, newline: str | None = None) -> str:
    """Texto del documento, reutilizando las líneas originales que no cambian."""
    nl = newline or doc.newline or "\n"
    lines = doc.lines
    out: list[str] = []

    def emit_original(recs) -> None:
        for raw in recs:
            if out and not record_end(out[-1], RECORD_LENGTH):
                out[-1] += nl  # el registro anterior no tenía fin de línea: no unirlo a este
            out.append(raw)

    def emit_generated(texts: list[str]) -> None:
        emit_original(t + nl for t in texts)

    if doc.header_end is not None:
        emit_original(lines[:doc.header_end + 1])
    else:
        emit_generated([doc.title or "Geometría creada con ARGUS", SEPARATOR_LINE])
    has_end = False
    for el in doc.elements:
        original = el.lead_line is not None and el.first_line is not None and lines
        if isinstance(el, EndElement):
            has_end = True
        if original:
            end = _span_end(el)
            if not el.modified:
                emit_original(lines[el.lead_line:end + 1])
            else:
                emit_original(lines[el.lead_line:el.first_line])  # comentarios previos
                for is_orig, text in _modified_element(el, lines, end):
                    if is_orig:
                        emit_original([text])
                    else:
                        emit_generated([text])
        else:
            emit_generated(element_lines(el))
    if not has_end:
        emit_generated([END_LINE])
    # Lo que haya tras END (PENGEOM no lo lee) se conserva.
    last_end = None
    for el in doc.elements:
        if isinstance(el, EndElement) and el.first_line is not None and not el.modified:
            last_end = el.first_line
    if last_end is not None:
        emit_original(lines[last_end + 1:])
    return "".join(out)


def write_geo_file(doc: GeoDocument, path: str, encoding: str = "latin-1") -> None:
    data = write_geo_text(doc).encode(encoding, errors="replace")
    with open(path, "wb") as f:
        f.write(data)
