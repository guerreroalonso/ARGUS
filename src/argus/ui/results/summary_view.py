"""Vista del resumen de la simulación (``penhmain-res.dat``) y de los ficheros de texto."""

from __future__ import annotations

import html
import math

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser, QVBoxLayout, QWidget

from ...core.numfmt import plural
from ...results.data import PARTICLE_NAMES
from ...results.display import ENERGY_SCALE, auto_energy
from ...results.folder import RunState
from ...results.summary import WHERE_TITLES, Measure, Summary
from .style import INK, INK_2, MUTED

CSS = f"""
body {{ color: {INK}; font-size: 10pt; }}
h2 {{ font-size: 12pt; margin: 10px 0 4px 0; }}
h3 {{ font-size: 10.5pt; margin: 12px 0 4px 0; color: {INK}; }}
table {{ border-collapse: collapse; margin: 2px 0 6px 0; }}
td, th {{ padding: 2px 10px 2px 0; text-align: right; }}
th {{ color: {INK_2}; font-weight: normal; border-bottom: 1px solid #e1e0d9; }}
td.l, th.l {{ text-align: left; }}
.muted {{ color: {MUTED}; }}
.note {{ color: {INK_2}; }}
"""


def _num(v: float, digits: int = 4) -> str:
    if v is None or not math.isfinite(v):
        return "—"
    if v == 0:
        return "0"
    a = abs(v)
    if 1e-3 <= a < 1e5:
        return f"{v:.{digits}g}"
    return f"{v:.{digits - 1}e}".replace("e+0", "e").replace("e-0", "e-").replace("e+", "e")


def measure(m: Measure | None, unit: str = "", energy: bool = False) -> str:
    if m is None:
        return "—"
    v, e, u = m.value, m.err, unit or m.unit
    if energy and (u == "eV" or not u):
        u = auto_energy(v) if v else "eV"
        f = ENERGY_SCALE[u]
        v, e = v * f, e * f
    s = _num(v)
    if e > 0:
        s += f" ± {_num(e, 2)}"
        if v:
            s += f" <span class='muted'>({100 * e / abs(v):.2g} %)</span>"
    return f"{s} {html.escape(u)}".strip()


def _duration(t: float) -> str:
    if t >= 86400:
        return f"{t / 86400:.2f} días"
    if t >= 3600:
        return f"{t / 3600:.2f} h"
    if t >= 60:
        return f"{t / 60:.1f} min"
    return f"{t:.3g} s"


def summary_html(s: Summary, state: RunState | None = None, body_names: dict[int, str] | None = None,
                 body_dose: dict[int, str] | None = None, body_materials: dict[int, str] | None = None,
                 note: str = "", body_mass: dict[int, str] | None = None) -> str:
    """El resumen como HTML (tablas pequeñas y legibles).

    ``body_names`` y ``body_materials``: nombre y material de cada cuerpo (de la geometría del caso);
    ``body_dose``: dosis media de cada cuerpo ya escrita; ``note``: un aviso al final (p. ej., por qué no hay
    nombres).
    """
    body_names = body_names or {}
    body_materials = body_materials or {}
    # Con una fuente radiactiva (KPARP = 0), penhmain cuenta desintegraciones y da todo por desintegración.
    what = "desintegraciones" if s.per_decay else "partículas"
    per = "por desintegración" if s.per_decay else "por primaria"
    out = [f"<style>{CSS}</style>"]
    out.append(f"<h2>{html.escape(s.title or 'Simulación')}</h2>")
    info = []
    if state is not None:
        info.append(f"<b>{html.escape(state.text)}</b>")
    info.append(f"Partículas primarias: {html.escape(s.primary_name)}")
    simulated = (("desintegración simulada" if s.per_decay else "partícula simulada") if s.showers == 1
                 else f"{what} simuladas")
    info.append(f"{_num(s.showers, 6)} {simulated}" + (f" · {html.escape(s.date)}" if s.date else ""))
    info.append(f"Tiempo de cálculo {_duration(s.time_total)}, de simulación (CPU) {_duration(s.time_cpu)}, "
                f"{_num(s.speed)} {what}/s")
    if not s.complete:
        info.append("<span class='note'>El resumen está incompleto (penhmain lo estaba escribiendo).</span>")
    # penhmain-res.dat da las incertidumbres con 3σ; aquí van con 1σ (el selector k·σ es de las gráficas).
    info.append("<span class='muted'>Las incertidumbres «±» son de 1σ (en penhmain-res.dat, 3σ).</span>")
    out.append("<br>".join(info))
    if s.dose_uncertainty_pct:
        pct = s.dose_uncertainty_pct
        need = {p: s.showers * (pct / p) ** 2 for p in (2.0, 1.0, 0.5)}
        txt = ", ".join(f"{_num(n, 3)} para el {p:g} %" for p, n in need.items() if n > s.showers)
        out.append(f"<p>Incertidumbre (1σ) en el vóxel con más dosis: <b>{pct:.3g} %</b>"
                   + (f". Lluvias necesarias: {txt} (la incertidumbre baja como 1/√N)." if txt else ".")
                   + f" Eficiencia: {_num(s.dose_efficiency or 0)} (intrínseca {_num(s.dose_intrinsic or 0)}).</p>")
    if s.fractions:
        out.append("<h3>Partículas primarias</h3><table><tr><th class='l'></th><th>Fracción</th>"
                   "<th>Recorrido medio</th><th>Energía final</th><th>Ángulo de salida</th></tr>")
        for w in ("up", "down", "abs"):
            if w not in s.fractions:
                continue
            out.append(f"<tr><td class='l'>{WHERE_TITLES[w].capitalize()}</td><td>{measure(s.fractions[w])}</td>"
                       f"<td>{measure(s.track_length.get(w))}</td>"
                       f"<td>{measure(s.final_energy.get(w), energy=True) if w != 'abs' else '—'}</td>"
                       f"<td>{measure(s.polar_angle.get(w)) if w != 'abs' else '—'}</td></tr>")
        out.append("</table>")
    if s.secondaries:
        kps = [kp for kp in range(1, 7) if any(s.secondaries.get((kp, w)) and s.secondaries[(kp, w)].value
                                               for w in ("up", "down", "abs"))]
        if kps:
            out.append(f"<h3>Partículas secundarias generadas ({per})</h3><table><tr><th class='l'></th>"
                       + "".join(f"<th>{PARTICLE_NAMES[k]}</th>" for k in kps) + "</tr>")
            for w in ("up", "down", "abs"):
                out.append(f"<tr><td class='l'>{WHERE_TITLES[w].capitalize()}</td>"
                           + "".join(f"<td>{measure(s.secondaries.get((k, w)))}</td>" for k in kps) + "</tr>")
            out.append("</table>")
    if s.events:
        names: list[str] = []
        for d in s.events.values():
            for n in d:
                if n not in names:
                    names.append(n)
        wheres = [w for w in ("up", "down", "abs") if w in s.events]
        out.append(f"<h3>Interacciones {'por desintegración' if s.per_decay else 'por partícula primaria'}</h3>"
                   "<table><tr><th class='l'></th>"
                   + "".join(f"<th>{WHERE_TITLES[w]}</th>" for w in wheres) + "</tr>")
        for n in names:
            out.append(f"<tr><td class='l'>{html.escape(n)}</td>"
                       + "".join(f"<td>{measure(s.events[w].get(n))}</td>" for w in wheres) + "</tr>")
        out.append("</table>")
    if s.body_energy:
        with_dose = bool(body_dose)
        with_mat = bool(body_materials)
        with_mass = bool(body_mass)
        out.append(f"<h3>Energía depositada en cada cuerpo ({per})</h3><table><tr><th class='l'>Cuerpo</th>"
                   + ("<th class='l'>Material</th>" if with_mat else "") + ("<th>Masa</th>" if with_mass else "")
                   + "<th>Energía</th>" + ("<th>Dosis media</th>" if with_dose else "")
                   + "<th>Carga</th><th>Eficiencia</th></tr>")
        for kb, m in s.body_energy.items():
            if m.value == 0 and not (s.body_charge.get(kb) and s.body_charge[kb].value):
                continue
            name = html.escape(body_names.get(kb, f"n.º {kb}"))
            out.append(f"<tr><td class='l'>{name}</td>"
                       + (f"<td class='l'>{html.escape(body_materials.get(kb, ''))}</td>" if with_mat else "")
                       + (f"<td>{body_mass.get(kb, '—')}</td>" if with_mass else "")
                       + f"<td>{measure(m, energy=True)}</td>"
                       + (f"<td>{body_dose.get(kb, '—')}</td>" if with_dose else "")
                       + f"<td>{measure(s.body_charge.get(kb), 'e')}</td><td>{_num(m.effic or 0, 3)}</td></tr>")
        zero = [kb for kb, m in s.body_energy.items() if m.value == 0]
        out.append("</table>")
        if zero:
            out.append(f"<p class='muted'>{plural(len(zero), 'cuerpo')} sin energía depositada.</p>")
    det_rows = []
    for title, d, energy in (("Detector de impacto: energía que entra", s.impact_energy, True),
                             ("Detector de impacto: área del espectro", s.impact_area, False),
                             ("Detector angular: energía que sale", s.angular_energy, True),
                             ("Detector de energía depositada: energía media", s.enddet_energy, True),
                             ("Detector de energía depositada: área del espectro", s.enddet_area, False)):
        for n, m in d.items():
            det_rows.append(f"<tr><td class='l'>{title} n.º {n}</td><td>{measure(m, energy=energy)}</td></tr>")
    if det_rows:
        out.append(f"<h3>Detectores ({per})</h3><table>" + "".join(det_rows) + "</table>")
    if s.expected:
        out.append("<h3>Colisiones esperadas para las primarias absorbidas</h3><table>"
                   + "".join(f"<tr><td class='l'>{html.escape(k)}</td><td>{_num(v)}</td></tr>"
                             for k, v in s.expected.items()) + "</table>")
    if s.last_seeds:
        out.append(f"<p class='muted'>Últimas semillas: {s.last_seeds[0]}, {s.last_seeds[1]}</p>")
    if note:
        out.append(f"<p class='muted'>{html.escape(note)}</p>")
    return "".join(out)


def campaign_html(title: str, parameters: list[dict], rows: list[tuple]) -> str:
    """Resumen de una campaña: cada variante con su estado, sus lluvias y la incertidumbre de la dosis."""
    out = [f"<style>{CSS}</style>", f"<h2>{html.escape(title)}: campaña de {len(rows)} variantes</h2>"]
    if parameters:
        ps = "; ".join(html.escape(str(p.get("name", ""))) + (f" ({html.escape(p['unit'])})" if p.get("unit") else "")
                       for p in parameters)
        out.append(f"<p>Parámetros: {ps}. Compare cada resultado de todas a la vez (en el árbol) o una medida frente "
                   "al parámetro («Medidas frente al parámetro»).</p>")
    out.append("<table><tr><th class='l'>Variante</th><th class='l'>Estado</th><th>Lluvias</th><th>Tiempo</th>"
               "<th>Incertidumbre de la dosis</th></tr>")
    for label, st, s in rows:
        showers = _num(s.showers, 6) if s is not None else "—"
        t = _duration(s.time_total) if s is not None else "—"
        unc = f"{s.dose_uncertainty_pct:.3g} %" if s is not None and s.dose_uncertainty_pct else "—"
        out.append(f"<tr><td class='l'>{html.escape(label)}</td><td class='l'>{html.escape(st.text)}</td>"
                   f"<td>{showers}</td><td>{t}</td><td>{unc}</td></tr>")
    out.append("</table>")
    return "".join(out)


class SummaryView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        lay.addWidget(self.browser)

    def set_summary(self, s: Summary | None, state: RunState | None = None, body_names=None, body_dose=None,
                    body_materials=None, note: str = "", body_mass=None) -> None:
        if s is None:
            self.browser.setHtml(f"<p>{html.escape(state.text) if state else 'Sin resumen.'}</p>")
            return
        bar = self.browser.verticalScrollBar().value()
        self.browser.setHtml(summary_html(s, state, body_names, body_dose, body_materials, note, body_mass))
        self.browser.verticalScrollBar().setValue(bar)


class TextView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.edit.setFont(f)
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.edit)

    def set_text(self, text: str) -> None:
        at_end = self.edit.verticalScrollBar().value() >= self.edit.verticalScrollBar().maximum() - 2
        self.edit.setPlainText(text)
        if at_end:
            self.edit.verticalScrollBar().setValue(self.edit.verticalScrollBar().maximum())


__all__ = ["SummaryView", "TextView", "campaign_html", "measure", "summary_html"]
