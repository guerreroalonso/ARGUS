"""Comparación del lector de .in de ARGUS con el PMRDR0 real de penhmain."""

from __future__ import annotations

import math
import random
import re

from ..core.fortran_io import output_float
from ..penhan.inputfile import NO_STOP_REASON, InDocument, parse_in_text
from .probe import ReaderResult


def _f(s: str) -> list[float]:
    """Los reales de una línea de la sonda (un campo desbordado, «****», vale NaN y puede ir pegado al anterior)."""
    return [math.nan if x.startswith("*") else output_float(x) for x in re.findall(r"\*+|[^\s*]+", s)]


def compare_reader(doc: InDocument, res: ReaderResult) -> list[str]:
    """Diferencias entre lo que lee ARGUS y lo que lee penhmain (vacía si coinciden)."""
    diffs: list[str] = []
    # Los errores que no detienen PMRDR0 (un nombre de fichero que no se podrá abrir, PARINP fuera de la tabla,
    # un valor indefinido) no se comparan: penhmain sigue leyendo.
    errs = [e for e in doc.diagnostics.errors if e.fortran_reason != NO_STOP_REASON]
    a_err = errs[0] if errs else None
    if res.hang:
        if a_err is None or a_err.fortran_reason != "HANG":
            diffs.append(f"penhmain se queda colgado; ARGUS: {a_err.message if a_err else 'sin error'}")
        return diffs
    if res.crashed:
        if a_err is None or a_err.code is not None or a_err.fortran_reason == "HANG":
            diffs.append(f"penhmain se detiene con un error de ejecución; ARGUS: "
                         f"{a_err.code if a_err else 'sin error'}")
        return diffs
    if res.iretrn != 0:
        if a_err is None or a_err.code != res.iretrn:
            diffs.append(f"IRETRN penhmain={res.iretrn} ({res.reason}); ARGUS="
                         f"{a_err.code if a_err else 0} ({a_err.message if a_err else ''})")
        elif res.iretrn == 3035 and doc.stop_line is not None:
            # Comprobar que la línea es la misma que penhmain vuelca en fort.26.
            buf_line = [ln for ln in res.fort26.splitlines() if ln.startswith("BUFFER:")]
            if buf_line:
                pen_buf = buf_line[-1][len("BUFFER: "):].rstrip()
                raw = (doc.stop_line.raw or "").rstrip("\r\n")
                ours = raw[7:127].rstrip()
                if pen_buf != ours:
                    diffs.append(f"Línea de parada distinta: penhmain '{pen_buf}' ARGUS '{ours}'")
        return diffs
    if a_err is not None:
        diffs.append(f"penhmain lee el fichero sin error; ARGUS: {a_err.code} {a_err.message}")
        return diffs
    m = doc.model
    v = res.values
    src = m.source
    if int(v["KPARP"][0]) != src.kparp:
        diffs.append(f"KPARP {v['KPARP'][0]} != {src.kparp}")
    if _f(v["SE0"][0])[0] != src.energy:
        diffs.append(f"SE0 {v['SE0'][0]} != {src.energy}")
    if int(v["NISPEC"][0]) != len(src.spectrum):
        diffs.append("NISPEC distinto")
    else:
        for s, (e, p) in zip(v.get("SPECTR", []), src.spectrum, strict=False):
            if _f(s) != [e, p]:
                diffs.append(f"SPECTR {s} != {(e, p)}")
    if int(v["NLINE"][0]) != len(src.lines):
        diffs.append("NLINE distinto")
    if _f(v["SPOSIT"][0]) != list(src.position):
        diffs.append(f"SPOSIT {v['SPOSIT'][0]} != {src.position}")
    if _f(v["SBOX"][0]) != list(src.box):
        diffs.append(f"SBOX {v['SBOX'][0]} != {src.box}")
    if int(v["NKBS"][0]) != len(src.bodies):
        diffs.append("NKBS distinto")
    else:
        for s, b in zip(v.get("SBODY", []), src.bodies, strict=False):
            kb = int(s.split()[0])
            lab = s[s.index("[") + 1:s.rindex("]")]
            if b.label is not None:
                if kb != 0 or lab != b.label:
                    diffs.append(f"SBODY {s} != '{b.label}'")
            elif kb != b.kb:
                diffs.append(f"SBODY {s} != {b.kb}")
    icone, *cone = v["SCONE"][0].split()
    if (int(icone) == 1) != (src.cone is not None) or (src.cone and [output_float(x) for x in cone] != list(src.cone)):
        diffs.append(f"SCONE {v['SCONE'][0]} != {src.cone}")
    irect, *rect = v["SRECTA"][0].split()
    if (int(irect) == 1) != (src.rect is not None) or (src.rect and [output_float(x) for x in rect] != list(src.rect)):
        diffs.append(f"SRECTA {v['SRECTA'][0]} != {src.rect}")
    if int(v["NMATER"][0]) != len(m.materials):
        diffs.append(f"NMATER {v['NMATER'][0]} != {len(m.materials)}")
    else:
        for s, mat in zip(v.get("MFNAME", []), m.materials, strict=False):
            if s != mat.fname.rstrip(" "):  # penhmain solo quita los blancos finales (TRIM)
                diffs.append(f"MFNAME '{s}' != '{mat.fname}'")
    if v["GEOMFN"][0] != m.geomfn.rstrip(" "):
        diffs.append(f"GEOMFN '{v['GEOMFN'][0]}' != '{m.geomfn}'")
    if v.get("SRNUCL", [""])[0] != (src.nuclide or "").rstrip(" "):
        diffs.append(f"SRNUCL '{v['SRNUCL'][0]}' != '{src.nuclide}'")
    psf = [s for s in v.get("IPSFN", [])]
    if psf != [f.rstrip(" ") for f in src.psf]:
        diffs.append(f"IPSFN {psf} != {src.psf}")
    pen_par = {}
    for s in v.get("PARINP", []):
        ip, val = s.split()
        pen_par[int(ip)] = output_float(val)
    if pen_par != m.parinp:
        diffs.append(f"PARINP {pen_par} != {m.parinp}")
    return diffs


# ---------------------------------------------------------------------------
# Mutaciones para probar el lector
# ---------------------------------------------------------------------------

def mutations(text: str, rng: random.Random, n: int = 20) -> list[tuple[str, str]]:
    """Variantes de un .in válido con errores típicos de usuario."""
    lines = text.splitlines(keepends=True)
    nl = "\r\n" if "\r\n" in text else "\n"
    kw_idx = [i for i, ln in enumerate(lines) if ln[:6].strip() and i > 0]
    out: list[tuple[str, str]] = []
    for _ in range(n):
        kind = rng.choice(["swap", "unknown", "decimal_int", "drop_value", "delete", "duplicate",
                           "ruler", "comment_c", "hash", "label3", "label4", "notitle", "slash",
                           "repeat_count", "repeat_any", "lowercase", "emparc"])
        new = list(lines)
        if not kw_idx:
            break
        i = rng.choice(kw_idx)
        ln = new[i].rstrip("\r\n")
        if kind == "swap" and i + 1 < len(new):
            new[i], new[i + 1] = new[i + 1], new[i]
        elif kind == "unknown":
            new.insert(i, "FOOBAR 1 2 3" + nl)
        elif kind == "decimal_int":
            if ln[:6] in ("SKPAR ", "NBANGL", "LTRACK"):
                new[i] = ln[:7] + ln[7:].replace(ln[7:].split()[0], ln[7:].split()[0] + ".0", 1) + nl
            else:
                continue
        elif kind == "drop_value":
            toks = ln[7:].split("[")[0].split()
            if len(toks) >= 2:
                new[i] = ln[:7] + " ".join(toks[:-1]) + nl
            else:
                continue
        elif kind == "delete":
            del new[i]
        elif kind == "duplicate":
            new.insert(i, new[i] if new[i].endswith(("\n", "\r")) else new[i] + nl)
        elif kind == "ruler":
            new.insert(i, "....+....1....+....2....+....3....+....4....+....5" + nl)
        elif kind == "comment_c":
            new.insert(i, "C comentario estilo PENGEOM" + nl)
        elif kind == "hash":
            new.insert(i, "# comentario" + nl)
        elif kind == "label3":
            new.insert(i, "SBODY  'AB1'" + nl)
        elif kind == "label4":
            new.insert(i, "SBODY  '   1'" + nl)
        elif kind == "notitle":
            new = new[1:]
        elif kind == "slash":
            toks = ln[7:].split("[")[0].split()
            if len(toks) >= 2:
                new[i] = ln[:7] + toks[0] + " /" + nl
            else:
                continue
        elif kind == "repeat_count":
            toks = ln[7:].split("[")[0].split()
            if len(toks) >= 2 and toks[0] == toks[1]:
                new[i] = ln[:7] + f"2*{toks[0]} " + " ".join(toks[2:]) + nl
            else:
                continue
        elif kind == "repeat_any":  # «2*v» para los dos primeros valores, aunque sean de tipos distintos
            toks = ln[7:].split("[")[0].split()
            if len(toks) >= 2:
                new[i] = ln[:7] + f"2*{toks[0]} " + " ".join(toks[2:]) + nl
            else:
                continue
        elif kind == "lowercase":
            new[i] = ln[:6].lower() + ln[6:] + nl
        elif kind == "emparc":
            new.insert(i, "EMPARC 0.02 0.02" + nl)
        out.append((kind, "".join(new)))
    return out


def check_text(text: str, run) -> list[str]:
    """Compara ARGUS y penhmain para un texto; ``run`` ejecuta la sonda."""
    doc = parse_in_text(text)
    res = run(text)
    return compare_reader(doc, res)


# ---------------------------------------------------------------------------
# Todas las secciones (formularios del .in)
# ---------------------------------------------------------------------------

def _body(s: str) -> tuple[int, str]:
    """«KB [KBW]» de la sonda -> (número, etiqueta)."""
    head, rest = s.split("[", 1)
    return int(head.split()[-1]), rest.split("]", 1)[0]


def _ref_matches(kb: int, kbw: str, label: str | None, number: int | None) -> bool:
    if label is not None:
        return kb == 0 and kbw.rstrip() == label.rstrip()
    return kb == number


def compare_sections(doc: InDocument, res) -> list[str]:
    """Diferencias entre las secciones que lee ARGUS (``penhan.sections``) y lo que lee PMRDR0."""
    from ..penhan import sections as sc

    v = res.values
    diffs: list[str] = []

    def close(a: list[float], b: list[float]) -> bool:
        return len(a) == len(b) and all(x == y for x, y in zip(a, b))

    # Materiales (solo los valores de las líneas presentes; los ausentes, por defecto del módulo).
    for n, (sa, sh, sn) in enumerate(zip(v.get("MSIMPA", []), v.get("MSIMPH", []), v.get("MSIMPN", [])), start=1):
        par = sc.material_params(doc, n)
        for key, s in (("MSIMPA", sa), ("MSIMPH", sh), ("MSIMPN", sn)):
            want = par[key] if par[key] is not None else list(sc.default(key))
            if not close(_f(s), [float(x) for x in want]):
                diffs.append(f"material {n} {key} {s} != {want}")
    # Listas de cuerpos.
    for key, count in (("DSMAX", "NKBG1"), ("EABSB", "NKBG2"), ("EABSBH", "NKBG3"), ("EFIELD", "NLE"),
                       ("BFIELD", "NLB"), ("IBRSPL", "NBRS"), ("IXRSPL", "NXRS"), ("IFORCE", "NIFOR")):
        ours = sc.body_list(doc, key)
        if int(v[count][0]) != len(ours):
            diffs.append(f"{key}: {v[count][0]} líneas en penhmain, {len(ours)} en ARGUS")
            continue
        for s, e in zip(v.get(key, []), ours):
            kb, kbw = _body(s)
            if not _ref_matches(kb, kbw, e.label, e.kb):
                diffs.append(f"{key} cuerpo {s} != {e.ref}")
            nums = _f(s.split("]", 1)[1])
            if not close(nums, [float(x) for x in e.values]):
                diffs.append(f"{key} valores {s} != {e.values}")
    if v.get("PFIELD"):
        want = sc.value(doc, "PFIELD") or list(sc.default("PFIELD"))
        if not close(_f(v["PFIELD"][0]), [float(x) for x in want]):
            diffs.append(f"PFIELD {v['PFIELD'][0]} != {want}")
    for key in ("NBE", "NBANGL"):
        want = sc.value(doc, key) or list(sc.default(key))
        if not close(_f(v[key][0]), [float(x) for x in want]):
            diffs.append(f"{key} {v[key][0]} != {want}")
    # Detectores de impacto.
    ours_i = sc.impact_detectors(doc)
    if int(v["NIDET"][0]) != len(ours_i):
        diffs.append(f"NIDET {v['NIDET'][0]} != {len(ours_i)}")
    else:
        bodies = [s for s in v.get("IDBODY", [])]
        kpars = {int(s.split()[0]): [int(x) for x in s.split()[1:]] for s in v.get("IDKPAR", [])}
        for i, d in enumerate(ours_i, start=1):
            s = v["IMPDET"][i - 1]
            if not close(_f(s), [d.el, d.eu, d.nbe, d.ipsf, d.idcut]):
                diffs.append(f"IMPDET {i}: {s} != {d}")
            for key, attr in (("IDSPC", "spc"), ("IDPSF", "psf"), ("IDFLNC", "flnc"), ("IDAGEF", "agef")):
                if v[key][i - 1].strip(" ") != getattr(d, attr):
                    diffs.append(f"{key} {i}: '{v[key][i - 1]}' != '{getattr(d, attr)}'")
            if d.age is not None and not close(_f(v["IDAGEL"][i - 1]), [d.age[0], d.age[1], d.age[2]]):
                diffs.append(f"IDAGEL {i}: {v['IDAGEL'][i - 1]} != {d.age}")
            mine = [b for b in bodies if int(b.split()[0]) == i]
            if len(mine) != len(d.bodies):
                diffs.append(f"IDBODY {i}: {len(mine)} != {len(d.bodies)}")
            else:
                for s2, b in zip(mine, d.bodies):
                    kb, kbw = _body(s2.split(None, 1)[1])
                    if not _ref_matches(kb, kbw, b.label, b.kb):
                        diffs.append(f"IDBODY {i}: {s2} != {b.ref}")
            flags = kpars.get(i, [0] * 6)
            want = [1 if k in d.kpar else 0 for k in range(1, 7)]
            if flags != want:
                diffs.append(f"IDKPAR {i}: {flags} != {want}")
    # Detectores angulares.
    ours_a = sc.angular_detectors(doc)
    if int(v["NADET"][0]) != len(ours_a):
        diffs.append(f"NADET {v['NADET'][0]} != {len(ours_a)}")
    else:
        for i, d in enumerate(ours_a, start=1):
            if not close(_f(v["ANGDET"][i - 1]), [d.theta1, d.theta2, d.phi1, d.phi2]):
                diffs.append(f"ANGDET {i}: {v['ANGDET'][i - 1]} != {d}")
            en = d.energy if d.energy is not None else sc.default("ADENER")
            if not close(_f(v["ADENER"][i - 1]), [float(x) for x in en]):
                diffs.append(f"ADENER {i}: {v['ADENER'][i - 1]} != {en}")
            flags = [int(x) for x in v["ADKPAR"][i - 1].split()]
            want = [1 if k == d.kpar else 0 for k in range(1, 7)]
            if flags != want:
                diffs.append(f"ADKPAR {i}: {flags} != {want}")
            if v["ADSPC"][i - 1].strip(" ") != d.spc:
                diffs.append(f"ADSPC {i}: '{v['ADSPC'][i - 1]}' != '{d.spc}'")
    # Detectores de energía depositada.
    ours_e = sc.energy_detectors(doc)
    if int(v["NEDET"][0]) != len(ours_e):
        diffs.append(f"NEDET {v['NEDET'][0]} != {len(ours_e)}")
    else:
        bodies = v.get("EDBODY", [])
        for i, d in enumerate(ours_e, start=1):
            if not close(_f(v["ENDETC"][i - 1]), [d.el, d.eu, d.nbe]):
                diffs.append(f"ENDETC {i}: {v['ENDETC'][i - 1]} != {d}")
            if v["EDSPC"][i - 1].strip(" ") != d.spc:
                diffs.append(f"EDSPC {i}: '{v['EDSPC'][i - 1]}' != '{d.spc}'")
            mine = [b for b in bodies if int(b.split()[0]) == i]
            if len(mine) != len(d.bodies):
                diffs.append(f"EDBODY {i}: {len(mine)} != {len(d.bodies)}")
    # Rejilla de dosis.
    g = sc.dose_grid(doc)
    for key, t in (("GRIDX", g.x), ("GRIDY", g.y), ("GRIDZ", g.z)):
        nums = _f(v[key][0])
        if t is None:
            if int(nums[2]) > 0:
                diffs.append(f"{key}: penhmain {v[key][0]}, ARGUS sin rejilla")
        elif not close(nums, [t[0], t[1], t[2]]):
            diffs.append(f"{key} {v[key][0]} != {t}")
    nums = _f(v["GRIDR"][0])
    if (g.r is None and int(nums[1]) > 0) or (g.r is not None and not close(nums, [g.r[0], g.r[1]])):
        diffs.append(f"GRIDR {v['GRIDR'][0]} != {g.r}")
    # Proceso externo y trabajo.
    ep = sc.external_process(doc)
    if (ep is None) != (int(v["NEPROM"][0]) == 0):
        diffs.append(f"NEPROM {v['NEPROM'][0]} != {ep}")
    elif ep is not None and (v["EPROF"][0].strip(" ") != ep[0] or _f(v["EPROPM"][0]) != [ep[1], ep[2]]):
        diffs.append(f"EPROF {v['EPROF'][0]} {v['EPROPM'][0]} != {ep}")
    for key in ("RESUME", "DUMPTO"):
        if v[key][0].strip(" ") != (sc.text(doc, key) or ""):
            diffs.append(f"{key} '{v[key][0]}' != '{sc.text(doc, key)}'")
    for key in ("DUMPP", "LTRACK", "NSIMSH", "TIME"):
        want = sc.value(doc, key) or list(sc.default(key))
        if not close(_f(v[key][0]), [float(x) for x in want]):
            diffs.append(f"{key} {v[key][0]} != {want}")
    seeds = sc.seeds(doc)
    want = list(seeds[-1]) if seeds else [1, 1]
    if [int(x) for x in v["RSEED"][0].split()] != want:
        diffs.append(f"RSEED {v['RSEED'][0]} != {want}")
    return diffs


# ---------------------------------------------------------------------------
# Comprobaciones de PMRDR (sonda argus_simprobe)
# ---------------------------------------------------------------------------

_MAT_PATTERNS = (
    (r"Electron absorption energy =\s*(\S+)", ("EABS1",)),
    (r"Photon absorption energy =\s*(\S+)", ("EABS2",)),
    (r"Positron absorption energy =\s*(\S+)", ("EABS3",)),
    (r"Proton absorption energy =\s*(\S+)", ("EABSH1",)),
    (r"Alpha absorption energy =\s*(\S+)", ("EABSH2",)),
    (r"Neutron absorption energy =\s*(\S+)", ("EABSH3",)),
    (r"FNABS =\s*(\S+),\s*FORCEN =\s*(\S+)", ("FNABS", "FORCEN")),
)


def parse_material_output(output: str) -> dict[int, dict[str, float]]:
    """Parámetros efectivos de cada material tal como los escribe PMRDR en penhmain.dat."""
    import re

    mats: dict[int, dict[str, float]] = {}
    cur: dict[str, float] | None = None
    hadron = False
    for ln in output.splitlines():
        m = re.search(r"\*\*\*\*\s+(\d+)(?:st|nd|rd|th) material", ln)
        if m:
            cur = mats.setdefault(int(m.group(1)), {})
            hadron = False
            continue
        if cur is None:
            continue
        if "Simulation parameters for protons and alphas" in ln:
            hadron = True
            continue
        m = re.search(r"C1 =\s*(\S+),\s*C2 =\s*(\S+)", ln)
        if m:
            k1, k2 = ("C1H", "C2H") if hadron else ("C1", "C2")
            cur[k1], cur[k2] = output_float(m.group(1)), output_float(m.group(2))
            continue
        m = re.search(r"Wcc =\s*(\S+) eV,\s*Wcr =\s*(\S+)", ln)
        if m:
            cur["WCC"], cur["WCR"] = output_float(m.group(1)), output_float(m.group(2))
            continue
        m = re.search(r"Wcc =\s*(\S+) eV\s*$", ln)
        if m and hadron:
            cur["WCCH"] = output_float(m.group(1))
            continue
        for pat, keys in _MAT_PATTERNS:
            m = re.search(pat, ln)
            if m:
                for k, g in zip(keys, m.groups()):
                    cur[k] = output_float(g.rstrip(","))
                break
        if ">>>>>>" in ln and "Material data" not in ln:
            cur = None
    return mats


def compare_checks(doc: InDocument, geo, folder, res) -> list[str]:
    """Diferencias entre la validación de ARGUS y el PMRDR real (código IRETRN y parámetros efectivos)."""
    from ..penhan import sections as sc
    from ..penhan.validate import validate_case

    errors = list(doc.diagnostics.errors) + validate_case(doc, geo, folder).errors  # lector + PMRDR
    codes = {x.code for x in errors if x.code is not None}
    diffs: list[str] = []
    if res.hang or res.crashed:
        if not errors:
            diffs.append(f"penhmain se detiene con un error de ejecución ({res.stderr[:160]!r}); ARGUS no ve errores")
        return diffs
    if res.iretrn != 0:
        if res.iretrn not in codes:
            diffs.append(f"penhmain IRETRN {res.iretrn} ({res.reason}); ARGUS {sorted(codes)}: "
                         f"{[x.message for x in errors][:3]}")
        return diffs
    coded = [(x.code, x.message) for x in errors if x.code is not None]
    if coded:
        diffs.append(f"penhmain no se detiene; ARGUS: {coded[:3]}")
    got = parse_material_output(res.output)
    for n, vals in got.items():
        eff = sc.effective_material(doc, n)
        for k, v in vals.items():
            want = eff[k]
            if abs(v - want) > 5e-6 * max(abs(v), abs(want), 1e-30):
                diffs.append(f"material {n} {k}: penhmain {v:g}, ARGUS {want:g}")
    return diffs
