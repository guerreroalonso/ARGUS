"""El .in sigue a la geometría: referencias a cuerpos tras editar el .geo.

penhmain identifica los cuerpos del .in (SBODY, DSMAX, EABSB, EABSBH,
EFIELD, BFIELD, IFORCE, IBRSPL, IXRSPL, IDBODY y EDBODY) por su etiqueta
entre apóstrofos o por su número en PENGEOM; con el número 0 o una etiqueta
en blanco, PMRDR busca un cuerpo sin etiqueta (copias de un CLONE, la
envolvente que añade PENGEOM...) y toma el último, salvo en IDBODY, que no
lo busca (``inputfile.resolve_body_ref``). Al editar la geometría:

* los números se cambian por los nuevos (PENGEOM renumera al insertar);
* las etiquetas cambiadas se cambian también en el .in;
* las líneas de cuerpos que ya no existen se quitan (si no, penhmain se
  detendría o, con números, aplicaría el valor a otro cuerpo);
* con las geometrías de antes y de ahora, las líneas con 0 o con una
  etiqueta en blanco siguen al cuerpo que nombraban: se quitan si ya no
  existe y, si ahora el último cuerpo sin etiqueta es otro, pasan a su
  número (una etiqueta en blanco no lo identificaría).

Los números negativos (todos los cuerpos de un material) no cambian.
"""

from __future__ import annotations

import re

from .inputfile import InDocument, InLine, resolve_body_ref

_NUMBER = re.compile(r"(\s*)([+-]?\d+)(?![\d.eEdD*])")  # «2*1»: el 2 es un factor de repetición, no el cuerpo
_LABEL = re.compile(r"(\s*)'([^']*)'")


def retarget(ln: InLine, *, kb: int | None = None, label: str | None = None) -> None:
    """Cambia el cuerpo al que se refiere una línea tocando solo esa palabra (el resto queda igual).

    No vuelve a analizar el documento: hay que llamar a ``doc.analyze()`` después
    (que sustituye las líneas modificadas por otras nuevas).
    """
    raw = ln.raw
    quoted = ln.label is not None
    m = (_LABEL if quoted else _NUMBER).match(raw[7:]) if raw is not None and not ln.modified and len(raw) > 7 \
        else None
    if m is not None:
        new = f"'{label}'" if label is not None else str(int(kb))
        start = 7 + m.start(2) - (1 if quoted else 0)
        end = 7 + m.end(2) + (1 if quoted else 0)
        ln.raw = raw[:start] + new + raw[end:]
    else:
        ln.modified = True  # línea nueva o ya cambiada: se reescribe entera
        ln.error = None
    ln.label, ln.kb = label, (None if label is not None else int(kb))


def body_lines(doc: InDocument) -> list[InLine]:
    return [ln for ln in doc.lines if ln.spec is not None and ln.spec.mode == "body" and not ln.error]


def is_unlabelled_ref(ln: InLine) -> bool:
    """¿La línea nombra el cuerpo con 0 o con una etiqueta en blanco? (PMRDR busca un cuerpo sin etiqueta.)"""
    return not ln.label.strip() if ln.label is not None else not ln.kb


def target(geo, ln: InLine) -> int:
    """Cuerpo al que penhmain aplica la línea (0 si ninguno; negativo: los de un material), como PMRDR."""
    return resolve_body_ref(geo, ln.label, ln.kb, blank_search=ln.kw != "IDBODY")


def references_to(doc: InDocument, geo, kb: int) -> list[InLine]:
    """Líneas del .in que nombran el cuerpo ``kb`` (por número, por etiqueta, o con 0 o una etiqueta en blanco)."""
    if kb < 1:
        return []
    return [ln for ln in body_lines(doc) if target(geo, ln) == kb]


def _body_word(geo, kb: int) -> str:
    if kb < 0:
        return f"los cuerpos del material {-kb}"
    if not 0 < kb <= geo.nbodys:
        return "ningún cuerpo"
    label = geo.balias(kb).strip()
    if label:
        return f"«{label}»"
    b = geo.body(kb)  # sin etiqueta para penhmain: se dice de dónde sale
    clone = getattr(b, "clone_element", None)
    inc = getattr(b, "include", None)
    if b.origin == "clone" and clone is not None and clone.label:
        return f"el cuerpo {kb} (una copia del clon «{clone.label.strip()}»)"
    if inc is not None:
        own = (b.element.label or "").strip() if b.element is not None else ""
        return f"el cuerpo {kb} («{own}» de {inc.filename})" if own else f"el cuerpo {kb} (de {inc.filename})"
    if b.origin == "enclosure":
        return f"el cuerpo {kb} (la envolvente)"
    return f"el cuerpo {kb} (sin etiqueta)"


def stale_references(doc: InDocument, old_geo, new_geo) -> list[str]:
    """Líneas de cuerpos de ``doc`` que con la geometría ``new_geo`` nombrarían otro cuerpo que con ``old_geo``, o
    ninguno (p. ej. otro .in de la carpeta al guardar la geometría renumerada): «DSMAX 3 (era «esfe», sería
    «cubo»)».

    Un cuerpo es el mismo si tiene la misma identidad (``pengeom.identity``: su etiqueta o, si no la tiene —las
    copias de un CLONE, los cuerpos de un INCLUDE sin «*»—, de dónde sale). Las que ya no nombraban ningún cuerpo no
    cuentan.
    """
    from ..pengeom.identity import body_keys

    old_keys, new_keys = body_keys(old_geo), body_keys(new_geo)
    out: list[str] = []
    for ln in body_lines(doc):
        a, b = target(old_geo, ln), target(new_geo, ln)
        if a < 0 or b < 0:
            same = a == b
        else:
            if not 0 < a <= old_geo.nbodys:
                continue
            same = 0 < b <= new_geo.nbodys and old_keys[a - 1] == new_keys[b - 1]
        if not same:
            out.append(f"{ln.kw.strip()} {ln.body_ref} (era {_body_word(old_geo, a)}, sería {_body_word(new_geo, b)})")
    return out


def labels_still_resolve(doc: InDocument, mapping: dict[int, int | None], old_geo, new_geo) -> bool:
    """¿Cada línea que nombra un cuerpo por su etiqueta sigue nombrando el mismo cuerpo con la geometría nueva? (Un
    INCLUDE* que pasa a INCLUDE quita la etiqueta a sus cuerpos para penhmain, sin cambiar su número.)"""
    for ln in body_lines(doc):
        if ln.label is None or not ln.label.strip():
            continue
        before = target(old_geo, ln)
        if before > 0 and target(new_geo, ln) != mapping.get(before):
            return False
    return True


def follow_geometry(doc: InDocument, mapping: dict[int, int | None], renames: dict[str, str] | None = None,
                    deleted_labels: set[str] | None = None, *, old_geo=None, new_geo=None,
                    dropped: list[InLine] | None = None) -> list[str]:
    """Actualiza las referencias a cuerpos del .in; devuelve lo que ha cambiado (para el usuario).

    ``mapping``: número de antes → número de ahora (None: ya no existe).
    ``renames``: etiqueta de antes (4 caracteres) → etiqueta de ahora.
    ``deleted_labels``: etiquetas (4 caracteres) de los cuerpos que ya no existen.
    ``old_geo``, ``new_geo``: las geometrías de antes y de ahora; sin ellas, las líneas con 0 o con una
    etiqueta en blanco se dejan como están (no se sabe a qué cuerpo sin etiqueta iban). Con ellas, una línea que
    nombra un cuerpo por su etiqueta y con la geometría nueva ya no lo encontraría (p. ej. un INCLUDE* que pasa a
    INCLUDE: sus cuerpos pierden la etiqueta para penhmain) pasa a nombrarlo por su número.
    ``dropped``: si se da, se le añaden las líneas que se quitan (también las de los detectores que se quedan sin
    cuerpos), para decirlas antes de hacer el cambio.

    Una etiqueta en blanco en ``renames`` o ``deleted_labels`` (la de una copia de un clon) no cuenta: no es
    una etiqueta de usuario y no identifica ningún cuerpo.
    """
    renames = {k: v for k, v in (renames or {}).items() if k.strip() and v.strip()}
    deleted_labels = {x for x in (deleted_labels or ()) if x.strip()}
    notes: list[str] = []
    removed: list[InLine] = []
    renumbered = relabelled = unlabelled = 0
    for ln in body_lines(doc):
        if ln.label is not None and ln.label.strip() and old_geo is not None and new_geo is not None:
            before = target(old_geo, ln)
            now = mapping.get(before) if before > 0 else None
            if before > 0 and now is None and before in mapping:
                removed.append(ln)  # el cuerpo ya no existe
                continue
            if now is not None:
                lab = renames.get(ln.label, ln.label)
                if resolve_body_ref(new_geo, lab, None) == now:
                    if lab != ln.label:
                        retarget(ln, label=lab)
                        relabelled += 1
                elif new_geo.balias(now).strip():
                    retarget(ln, label=new_geo.balias(now))  # su etiqueta de ahora
                    relabelled += 1
                else:
                    retarget(ln, kb=now)  # sin etiqueta para penhmain: por su número
                    renumbered += 1
                continue
        if is_unlabelled_ref(ln):
            if ln.kw == "IDBODY" or old_geo is None or new_geo is None:
                continue  # IDBODY no busca cuerpos sin etiqueta (penhmain: 3082, antes y después)
            before = target(old_geo, ln)
            if before < 1 or before not in mapping:
                continue  # ya no nombraba ningún cuerpo
            now = mapping[before]
            if now is None:
                removed.append(ln)
            elif target(new_geo, ln) != now:
                retarget(ln, kb=now)
                unlabelled += 1
            continue
        if ln.label is not None:
            if ln.label in renames:
                retarget(ln, label=renames[ln.label])
                relabelled += 1
            elif ln.label in deleted_labels:
                removed.append(ln)
            continue
        kb = ln.kb
        if kb is None or kb <= 0 or kb not in mapping:
            continue  # material (negativo) o un número que ya no existía
        new = mapping[kb]
        if new is None:
            removed.append(ln)
        elif new != kb:
            retarget(ln, kb=new)
            renumbered += 1
    empty_detectors = _emptied_detectors(doc, removed) if removed else []
    source_unbound = bool(removed) and any(ln.kw == "SBODY " for ln in removed) and \
        all(any(ln is r for r in removed) for ln in doc.lines if ln.kw == "SBODY ")
    if removed:
        drop = removed + [ln for _, block in empty_detectors for ln in block if not any(ln is r for r in removed)]
        if dropped is not None:
            dropped.extend(drop)
        doc.lines = [ln for ln in doc.lines if not any(ln is r for r in drop)]
    if removed or renumbered or relabelled or unlabelled:
        doc.analyze()
    if removed:
        what = ", ".join(f"{ln.kw.strip()} {ln.body_ref}" for ln in removed[:6]) + (" …" if len(removed) > 6 else "")
        head = "quitada la línea" if len(removed) == 1 else f"quitadas las {len(removed)} líneas"
        notes.insert(0, f"{head} de cuerpos que ya no existen ({what})")
    for name, _block in empty_detectors:  # penhmain se detendría: «This detector has no active bodies» (3101)
        notes.append(f"quitado el {name}, que se quedaba sin cuerpos")
    if source_unbound:
        notes.append("la fuente ya no está limitada a ningún cuerpo (SBODY): emitirá en toda su caja (SBOX)")
    if unlabelled:
        notes.insert(0, "1 referencia con 0 o etiqueta en blanco (el último cuerpo sin etiqueta) pasa a su número"
                     if unlabelled == 1 else f"{unlabelled} referencias con 0 o etiqueta en blanco (el último cuerpo "
                     "sin etiqueta) pasan a su número")
    if relabelled:
        notes.insert(0, "1 referencia por etiqueta cambiada" if relabelled == 1
                     else f"{relabelled} referencias por etiqueta cambiadas")
    if renumbered:
        notes.insert(0, "1 referencia por número actualizada" if renumbered == 1
                     else f"{renumbered} referencias por número actualizadas")
    return notes


def _emptied_detectors(doc: InDocument, removed: list[InLine]) -> list[tuple[str, list[InLine]]]:
    """Detectores de impacto o de energía cuyos cuerpos (IDBODY/EDBODY) se quitan todos: (nombre, sus líneas)."""
    from .sections import ENERGY_KEYWORDS, IMPACT_KEYWORDS

    out = []
    for head, keys, body_kw, name in (("IMPDET", IMPACT_KEYWORDS, "IDBODY", "detector de impacto"),
                                      ("ENDETC", ENERGY_KEYWORDS, "EDBODY", "detector de energía depositada")):
        n = 0
        block: list[InLine] | None = None
        blocks = []
        for ln in doc.lines:
            k = ln.kw.strip()
            if k == head:
                n += 1
                block = [ln]
                blocks.append((n, block))
            elif block is not None and k in keys:
                block.append(ln)
            elif k and k not in keys:
                block = None
        for i, blk in blocks:
            bodies = [ln for ln in blk if ln.kw.strip() == body_kw]
            if bodies and all(any(b is r for r in removed) for b in bodies):
                out.append((f"{name} {i}", blk))
    return out
