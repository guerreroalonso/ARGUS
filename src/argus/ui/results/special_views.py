"""Vistas de las trayectorias (LTRACK), del espacio de fases y de los datos de los materiales.

* Trayectorias: todas las partículas del caso a la vez (un fichero .trk por
  tipo), proyectadas en un plano o en perspectiva 3D, con un color por tipo de
  partícula o por energía.
* Espacio de fases: espectro de energía, posiciones sobre el detector y ángulo
  polar de las partículas, por tipo (con su peso); por primaria si se conoce el
  número de lluvias.
* Materiales: la lista de materiales y, si penhmain escribió las tablas (un solo
  material), las curvas de poderes de frenado, alcances y coeficientes, con una
  calculadora de alcance, poder de frenado y atenuación.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core.numfmt import fmt_count
from ...results.data import PARTICLE_NAMES, PhaseSpace, Tracks, fair_shares
from ...results.display import ENERGY_SCALE, DisplayOptions, auto_energy
from ...results.materialdata import (
    ATTENUATION_TABLE,
    STOPPING_TABLES,
    MaterialData,
    MaterialInfo,
    csda_range,
    energy_for_range,
    stopping_power,
    table_value,
)
from ..flow_layout import FlowLayout, group
from .canvas import PlotCanvas
from .style import CATEGORICAL, INK_2, MUTED, colormap, particle_color

MAX_SEGMENTS = 400_000  # tramos dibujados como mucho (se muestran las primeras trayectorias)


def _energy_unit(opts: DisplayOptions | None, emax: float) -> str:
    u = opts.energy_unit if opts is not None else "auto"
    return u if u in ENERGY_SCALE else auto_energy(emax)


def _particle_checks(box: QLayout, kpars: list[int], visible: set[int], on_toggle) -> dict[int, QCheckBox]:
    while box.count():
        it = box.takeAt(0)
        if it.widget() is not None:
            it.widget().deleteLater()
    out = {}
    if len(kpars) < 2:
        return out
    for kp in kpars:
        cb = QCheckBox(PARTICLE_NAMES.get(kp, f"KPAR {kp}"))
        color = particle_color(kp)
        cb.setStyleSheet(f"QCheckBox {{ color: {INK_2}; }} QCheckBox::indicator:checked {{ "
                         f"background: {color}; border: 1px solid {color}; }}")
        cb.setChecked(kp in visible)
        cb.toggled.connect(lambda on, k=kp: on_toggle(k, on))
        box.addWidget(cb)
        out[kp] = cb
    return out


def _clip_tracks(t: Tracks, max_segments: int) -> tuple[Tracks, int]:
    """Las primeras trayectorias con como mucho ``max_segments`` tramos (y cuántas son)."""
    if len(t.points) - t.count <= max_segments:
        return t, t.count
    segs = np.diff(t.starts) - 1
    k = int(np.searchsorted(np.cumsum(segs), max_segments, side="right"))
    k = max(k, 1)
    end = int(t.starts[k])
    return Tracks(t.particle, t.points[:end], t.starts[:k + 1]), k


# ------------------------------------------------------------------------------------------------------------------
class TracksView(QWidget):
    """Trayectorias de LTRACK: proyección en un plano o perspectiva 3D."""

    PLANES = {"xz": ("x", "z"), "yz": ("y", "z"), "xy": ("x", "y")}
    changed = Signal()  # otras partículas visibles (para las trayectorias sobre la geometría)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = FlowLayout()  # sigue en otra línea si no cabe (pantallas al 125 o 150 %)
        row.setContentsMargins(6, 4, 6, 0)
        self.plane = QComboBox()
        for k, t in (("xz", "plano XZ"), ("yz", "plano YZ"), ("xy", "plano XY"), ("3d", "perspectiva 3D")):
            self.plane.addItem(t, k)
        self.plane.currentIndexChanged.connect(self.redraw)
        row.addWidget(group(QLabel("Vista:"), self.plane))
        self.color = QComboBox()
        self.color.addItem("por partícula", "particle")
        self.color.addItem("por energía", "energy")
        self.color.currentIndexChanged.connect(self.redraw)
        row.addWidget(group(QLabel("Color:"), self.color))
        checks = QWidget()
        self.checks_box = FlowLayout(checks, spacing=10)
        row.addWidget(checks)
        row.addStretch(1)
        lay.addLayout(row)
        self.plot = PlotCanvas()
        lay.addWidget(self.plot, 1)
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {INK_2}; padding: 2px 6px;")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.plot.hovered.connect(self._on_hover)
        self.tracks: dict[int | None, Tracks] = {}
        self.visible: set[int | None] = set()
        self.opts: DisplayOptions | None = None
        self.title = ""
        self._checks: dict = {}
        self._axes_names: tuple[str, str] | None = None

    def set_data(self, data: Tracks, title: str, opts: DisplayOptions | None = None, context: dict | None = None
                 ) -> None:
        siblings = list((context or {}).get("siblings") or [])
        if not any(s is data or s.particle == data.particle for s in siblings):
            siblings.append(data)
        new = {t.particle: t for t in sorted(siblings, key=lambda t: t.particle or 0)}
        if set(new) != set(self.tracks):
            self.visible = {k for k, t in new.items() if t.count} or set(new)
        self.tracks = new
        self.opts = opts
        self.title = "Trayectorias (LTRACK)" if len(new) > 1 else title
        self._checks = _particle_checks(self.checks_box, [k for k in new if k is not None],
                                        {k for k in self.visible if k is not None}, self._toggle)
        self.redraw()

    def set_options(self, opts: DisplayOptions) -> None:
        self.opts = opts
        self.redraw()

    def _toggle(self, kp: int, on: bool) -> None:
        (self.visible.add if on else self.visible.discard)(kp)
        self.redraw()
        self.changed.emit()

    def visible_tracks(self) -> list[Tracks]:
        """Las trayectorias de las partículas visibles (todas, sin recortar)."""
        return [t for kp, t in self.tracks.items() if kp in self.visible and t.count]

    def shown_tracks(self) -> list[tuple[Tracks, int, int]]:
        """(trayectorias dibujadas, cuántas, cuántas hay) de cada partícula visible. Los tramos que se dibujan se
        reparten entre las partículas: unas con muchas trayectorias no dejan sin ver a las otras."""
        vis = [t for kp, t in self.tracks.items() if kp in self.visible and t.count]
        shares = fair_shares([len(t.points) - t.count for t in vis], MAX_SEGMENTS)
        out = []
        for t, share in zip(vis, shares):
            clipped, k = _clip_tracks(t, share)
            out.append((clipped, k, t.count))
        return out

    def redraw(self) -> None:
        from matplotlib.collections import LineCollection
        from matplotlib.colors import LogNorm

        pc = self.plot
        pc.clear()
        shown = self.shown_tracks()
        mode = self.plane.currentData()
        by_energy = self.color.currentData() == "energy"
        emax = max((float(np.max(t.points[:, 3])) for t, _k, _n in shown if len(t.points)), default=1.0)
        eu = _energy_unit(self.opts, emax)
        ef = ENERGY_SCALE[eu]
        if mode == "3d":
            ax = pc.figure.add_subplot(projection="3d")
        else:
            ax = pc.figure.add_subplot()
        norm = None
        cmap = None
        if by_energy:
            positives = [t.points[:, 3][t.points[:, 3] > 0] for t, _k, _n in shown]
            allpos = np.concatenate(positives) if positives else np.zeros(0)
            if len(allpos):
                hi = float(allpos.max()) * ef
                lo = max(float(allpos.min()) * ef, hi * 1e-4)
                norm = LogNorm(vmin=lo, vmax=hi)
                cmap = colormap("azul_lineas")
        mappable = None
        lo_pts = []
        hi_pts = []
        for t, _k, _n in shown:
            a, b = t.segments()
            if not len(a):
                continue
            color = particle_color(t.particle)
            lab = PARTICLE_NAMES.get(t.particle, "trayectorias")
            lw = 0.6 if t.particle == 2 else 0.9
            if mode == "3d":
                from mpl_toolkits.mplot3d.art3d import Line3DCollection

                segs = np.stack([a[:, :3], b[:, :3]], axis=1)
                lc = Line3DCollection(segs, linewidths=lw)
            else:
                i, j = ("xyz".index(c) for c in self.PLANES[mode])
                segs = np.stack([a[:, [i, j]], b[:, [i, j]]], axis=1)
                lc = LineCollection(segs, linewidths=lw)
            if norm is not None:
                lc.set_array(np.maximum(a[:, 3] * ef, norm.vmin))
                lc.set_cmap(cmap)
                lc.set_norm(norm)
                mappable = lc
            else:
                lc.set_color(color)
                lc.set_label(lab)
            if mode == "3d":
                ax.add_collection3d(lc)
            else:
                ax.add_collection(lc)
            lo_pts.append(np.minimum(a[:, :3].min(axis=0), b[:, :3].min(axis=0)))
            hi_pts.append(np.maximum(a[:, :3].max(axis=0), b[:, :3].max(axis=0)))
        if lo_pts:
            lo = np.min(lo_pts, axis=0)
            hi = np.max(hi_pts, axis=0)
            pad = 0.03 * np.maximum(hi - lo, 1e-6)
            lo, hi = lo - pad, hi + pad
            if mode == "3d":
                ax.set_xlim(lo[0], hi[0])
                ax.set_ylim(lo[1], hi[1])
                ax.set_zlim(lo[2], hi[2])
                ax.set_box_aspect(tuple(np.maximum(hi - lo, 1e-6)))
                ax.set_xlabel("x (cm)")
                ax.set_ylabel("y (cm)")
                ax.set_zlabel("z (cm)")
                self._axes_names = None
            else:
                i, j = ("xyz".index(c) for c in self.PLANES[mode])
                ax.update_datalim([(lo[i], lo[j]), (hi[i], hi[j])])
                ax.autoscale_view()
                ax.set_aspect("equal", adjustable="datalim")
                u, v = self.PLANES[mode]
                ax.set_xlabel(f"{u} (cm)")
                ax.set_ylabel(f"{v} (cm)")
                self._axes_names = (u, v)
        if mappable is not None:
            cb = pc.figure.colorbar(mappable, ax=ax, shrink=0.85)
            cb.set_label(f"Energía ({eu})")
        elif len(shown) >= 2:
            ax.legend(loc="upper right")
        ax.set_title(self.title, loc="left")
        pc.draw()
        parts = []
        for t, k, n in shown:
            name = PARTICLE_NAMES.get(t.particle, "trayectorias")
            parts.append(f"{name}: {n} trayectorias" + (f" (se dibujan las {k} primeras)" if k < n else ""))
        empty = [PARTICLE_NAMES.get(kp, "?") for kp, t in self.tracks.items() if not t.count]
        if empty:
            parts.append("sin trayectorias: " + ", ".join(empty))
        self.info.setText(" · ".join(parts) or "No hay trayectorias.")

    def _on_hover(self, ax, x: float, y: float) -> None:
        if self._axes_names is None:
            return
        u, v = self._axes_names
        self.plot.readout.setText(f"{u} = {x:.4g} cm   {v} = {y:.4g} cm")

    def table_rows(self) -> tuple[list[str], list[np.ndarray]]:
        cols = ["partícula (KPAR)", "trayectoria", "x (cm)", "y (cm)", "z (cm)", "E (eV)"]
        kp, tr, pts = [], [], []
        for t, _k, _n in self.shown_tracks():
            idx = np.repeat(np.arange(t.count), np.diff(t.starts))
            kp.append(np.full(len(t.points), float(t.particle or 0)))
            tr.append(idx.astype(np.float64) + 1)
            pts.append(t.points)
        if not pts:
            return [], []
        p = np.concatenate(pts)
        return cols, [np.concatenate(kp), np.concatenate(tr), p[:, 0], p[:, 1], p[:, 2], p[:, 3]]


# ------------------------------------------------------------------------------------------------------------------
class PhaseSpaceView(QWidget):
    """Partículas de un espacio de fases (IMPDET con PSF): energía, posición y ángulo."""

    WHAT = {"energy": "Espectro de energía", "pos": "Posiciones", "theta": "Ángulo polar (respecto de +z)"}

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = FlowLayout()  # sigue en otra línea si no cabe (pantallas al 125 o 150 %)
        row.setContentsMargins(6, 4, 6, 0)
        self.what = QComboBox()
        for k, t in self.WHAT.items():
            self.what.addItem(t, k)
        self.what.currentIndexChanged.connect(self._what_changed)
        row.addWidget(group(QLabel("Ver:"), self.what))
        self.plane_label = QLabel("Plano:")
        self.plane = QComboBox()
        for k in ("xy", "xz", "yz"):
            self.plane.addItem(k.upper(), k)
        self.plane.currentIndexChanged.connect(self.redraw)
        row.addWidget(group(self.plane_label, self.plane))
        self.bins = QSpinBox()
        self.bins.setRange(5, 1000)
        self.bins.setValue(100)
        self.bins.valueChanged.connect(self.redraw)
        self.log = QCheckBox("log")
        self.log.setToolTip("Energía: intervalos logarítmicos. Posiciones: escala de colores logarítmica.")
        self.log.toggled.connect(self.redraw)
        row.addWidget(group(QLabel("Intervalos:"), self.bins, self.log))
        checks = QWidget()
        self.checks_box = FlowLayout(checks, spacing=10)
        row.addWidget(checks)
        row.addStretch(1)
        lay.addLayout(row)
        self.plot = PlotCanvas()
        lay.addWidget(self.plot, 1)
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {INK_2}; padding: 2px 6px;")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.info)
        self.psf: PhaseSpace | None = None
        self.showers: float | None = None
        self.opts: DisplayOptions | None = None
        self.title = ""
        self.visible: set[int] = set()
        self._checks: dict = {}
        self._rows: tuple[list[str], list[np.ndarray]] = ([], [])
        self._what_changed()

    def set_data(self, data: PhaseSpace, title: str, opts: DisplayOptions | None = None, context: dict | None = None
                 ) -> None:
        kps = sorted({int(k) for k in np.unique(data.kpar)})
        if self.psf is None or set(kps) != {int(k) for k in np.unique(self.psf.kpar)}:
            self.visible = set(kps)
            self.plane.blockSignals(True)
            self.plane.setCurrentIndex(self.plane.findData(self._auto_plane(data)))
            self.plane.blockSignals(False)
        self.psf = data
        self.showers = (context or {}).get("showers")
        if data.truncated:
            # Solo se han leído las primeras partículas: por primaria es entre las lluvias que cubren (la suma de los
            # incrementos NSHI, como penhmain), no entre todas las de la simulación.
            covered = float(np.sum(data.nshi)) if len(data.nshi) else 0.0
            self.showers = covered if covered > 0 else None
        self.opts = opts
        self.title = title
        self._checks = _particle_checks(self.checks_box, kps, self.visible, self._toggle)
        self._fill_info()
        self.redraw()

    def set_options(self, opts: DisplayOptions) -> None:
        self.opts = opts
        self.redraw()

    @staticmethod
    def _auto_plane(p: PhaseSpace) -> str:
        """El plano de las dos coordenadas que más varían (el detector suele ser plano)."""
        if not p.count:
            return "xy"
        spread = np.ptp(p.pos, axis=0)
        k = int(np.argmin(spread))
        return {0: "yz", 1: "xz", 2: "xy"}[k]

    def _toggle(self, kp: int, on: bool) -> None:
        (self.visible.add if on else self.visible.discard)(kp)
        self.redraw()

    def _what_changed(self) -> None:
        pos = self.what.currentData() == "pos"
        self.plane.setVisible(pos)
        self.plane_label.setVisible(pos)
        self.redraw()

    def _fill_info(self) -> None:
        p = self.psf
        if p is None:
            self.info.setText("")
            return
        rows = []
        per = f" por primaria (de {fmt_count(self.showers)})" if self.showers else ""
        for kp in sorted({int(k) for k in np.unique(p.kpar)}):
            m = p.kpar == kp
            w = p.weight[m]
            e = p.e[m]
            sw = float(w.sum())
            mean = float((w * e).sum() / sw) if sw > 0 else math.nan
            u = auto_energy(mean if math.isfinite(mean) else 1.0)
            f = ENERGY_SCALE[u]
            num = f"{sw / self.showers:.4g}" if self.showers else f"{sw:.6g}"
            rows.append(f"{PARTICLE_NAMES.get(kp, kp)}: {int(m.sum())} partículas, peso {num}{per}, "
                        f"energía media {mean * f:.4g} {u} (de {e.min() * f:.3g} a {e.max() * f:.3g} {u})")
        note = ""
        if p.truncated:
            note = (f"<br><span style='color:{MUTED}'>Se han leído {p.count} de {p.total} partículas (las de las "
                    f"primeras {fmt_count(self.showers)} lluvias).</span>" if self.showers else
                    f"<br><span style='color:{MUTED}'>Se han leído {p.count} de {p.total} partículas.</span>")
        self.info.setText("<br>".join(rows) + note)

    def _norm(self) -> float:
        return float(self.showers) if self.showers else 1.0

    def redraw(self) -> None:
        pc = self.plot
        pc.clear()
        p = self.psf
        self._rows = ([], [])
        if p is None or not p.count:
            pc.draw()
            return
        what = self.what.currentData()
        ax = pc.figure.add_subplot()
        kps = [kp for kp in sorted({int(k) for k in np.unique(p.kpar)}) if kp in self.visible]
        per = " por primaria" if self.showers else ""
        n = self._norm()
        nb = self.bins.value()
        if what == "energy":
            emax = float(p.e.max())
            eu = _energy_unit(self.opts, emax)
            f = ENERGY_SCALE[eu]
            e = p.e * f
            pos = e[e > 0]
            if self.log.isChecked() and len(pos):
                lo, hi = float(pos.min()), float(pos.max())
                edges = np.geomspace(lo, hi * (1 + 1e-9), nb + 1) if hi > lo else np.array([lo * 0.9, lo * 1.1])
            else:
                hi = float(e.max())
                edges = np.linspace(0.0, hi * (1 + 1e-9) if hi > 0 else 1.0, nb + 1)
            cols, data = [f"E ({eu}) inferior", f"E ({eu}) superior"], [edges[:-1], edges[1:]]
            for kp in kps:
                m = p.kpar == kp
                h, _ = np.histogram(e[m], bins=edges, weights=p.weight[m])
                y = h / (n * np.diff(edges))
                ax.stairs(y, edges, color=particle_color(kp), label=PARTICLE_NAMES.get(kp, str(kp)), baseline=None)
                cols.append(PARTICLE_NAMES.get(kp, str(kp)))
                data.append(y)
            ax.set_xlabel(f"Energía ({eu})")
            ax.set_ylabel(f"Partículas{per} / {eu}")
            if self.log.isChecked():
                ax.set_xscale("log")
            self._rows = (cols, data)
        elif what == "theta":
            th = np.degrees(np.arccos(np.clip(p.dir[:, 2], -1.0, 1.0)))
            edges = np.linspace(0.0, 180.0, nb + 1)
            cols, data = ["θ inferior (grados)", "θ superior (grados)"], [edges[:-1], edges[1:]]
            for kp in kps:
                m = p.kpar == kp
                h, _ = np.histogram(th[m], bins=edges, weights=p.weight[m])
                y = h / (n * np.diff(edges))
                ax.stairs(y, edges, color=particle_color(kp), label=PARTICLE_NAMES.get(kp, str(kp)), baseline=None)
                cols.append(PARTICLE_NAMES.get(kp, str(kp)))
                data.append(y)
            ax.set_xlabel("Ángulo polar θ (grados)")
            ax.set_ylabel(f"Partículas{per} / grado")
            ax.set_xlim(0, 180)
            self._rows = (cols, data)
        else:
            from matplotlib.colors import LogNorm

            u, v = self.plane.currentData()
            i, j = "xyz".index(u), "xyz".index(v)
            m = np.isin(p.kpar, kps)
            a, b = p.pos[m, i], p.pos[m, j]
            if len(a):
                ea = np.linspace(a.min(), a.max() + 1e-9, nb + 1) if np.ptp(a) > 0 else np.array([a[0] - .5, a[0] + .5])
                eb = np.linspace(b.min(), b.max() + 1e-9, nb + 1) if np.ptp(b) > 0 else np.array([b[0] - .5, b[0] + .5])
                h, _, _ = np.histogram2d(a, b, bins=(ea, eb), weights=p.weight[m])
                h = h / (n * np.diff(ea)[:, None] * np.diff(eb)[None, :])
                pos = h[h > 0]
                norm = LogNorm(vmin=max(float(pos.min()), float(pos.max()) * 1e-4), vmax=float(pos.max())) \
                    if self.log.isChecked() and len(pos) else None
                cmap = colormap("azul").with_extremes(bad=(0.0, 0.0, 0.0, 0.0))  # sin dato: transparente
                mesh = ax.pcolormesh(ea, eb, np.ma.masked_where(h <= 0, h).T, cmap=cmap, norm=norm)
                ax.set_aspect("equal", adjustable="box")
                ax.grid(False)
                cb = pc.figure.colorbar(mesh, ax=ax, shrink=0.9)
                cb.set_label(f"Partículas{per} / cm²")
                ac, bc = 0.5 * (ea[:-1] + ea[1:]), 0.5 * (eb[:-1] + eb[1:])
                aa, bb = np.meshgrid(ac, bc, indexing="ij")
                self._rows = ([f"{u} (cm)", f"{v} (cm)", f"partículas{per}/cm²"], [aa.ravel(), bb.ravel(), h.ravel()])
            ax.set_xlabel(f"{u} (cm)")
            ax.set_ylabel(f"{v} (cm)")
        if what != "pos" and len(kps) >= 2:
            ax.legend(loc="best")
        ax.set_title(self.title, loc="left")
        pc.draw()

    def table_rows(self) -> tuple[list[str], list[np.ndarray]]:
        return self._rows


# ------------------------------------------------------------------------------------------------------------------
def _display_unit(unit: str, in_cm: bool, density: float | None) -> tuple[float, str]:
    """Factor y unidad con que se muestra una columna de material.dat («mtu» = g/cm²)."""
    rho = density if (in_cm and density) else None
    u = unit.replace("**", "^")
    if u in ("", "W>WCR"):
        return 1.0, ""
    if u == "eV/mtu":
        return (1e-6 * rho, "MeV/cm") if rho else (1e-6, "MeV·cm²/g")
    if u == "eV^2/mtu":
        return (1e-6 * rho, "keV²/cm") if rho else (1e-6, "keV²·cm²/g")
    if u == "mtu":
        return (1.0 / rho, "cm") if rho else (1.0, "g/cm²")
    if u == "1/mtu":
        return (rho, "1/cm") if rho else (1.0, "cm²/g")
    return 1.0, unit


class MaterialsView(QWidget):
    """Materiales de la simulación y sus tablas (material.dat)."""

    CALC = {1: "electrones", 3: "positrones", 4: "protones", 5: "alfas", 2: "fotones"}

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 4, 0, 4)
        ll.addWidget(QLabel("Materiales:"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._material_changed)
        ll.addWidget(self.list, 1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {INK_2};")
        ll.addWidget(self.note)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 4, 6, 4)
        row = QHBoxLayout()
        row.addWidget(QLabel("Tabla:"))
        self.table = QComboBox()
        self.table.currentIndexChanged.connect(self._table_changed)
        row.addWidget(self.table, 1)
        self.in_cm = QCheckBox("Longitudes en cm (con la densidad)")
        self.in_cm.toggled.connect(self.redraw)
        row.addWidget(self.in_cm)
        rl.addLayout(row)
        self.cols_box = QHBoxLayout()
        self.cols_box.setSpacing(10)
        crow = QHBoxLayout()
        crow.addLayout(self.cols_box)
        crow.addStretch(1)
        rl.addLayout(crow)
        self.plot = PlotCanvas()
        rl.addWidget(self.plot, 1)
        calc = QGroupBox("Calculadora")
        cl = QVBoxLayout(calc)
        r1 = QHBoxLayout()
        self.calc_particle = QComboBox()
        self.calc_particle.currentIndexChanged.connect(self._calc)
        r1.addWidget(self.calc_particle)
        r1.addWidget(QLabel("de"))
        self.calc_energy = QDoubleSpinBox()
        self.calc_energy.setDecimals(4)
        self.calc_energy.setRange(1e-4, 1e7)
        self.calc_energy.setValue(1.0)
        self.calc_energy.valueChanged.connect(self._calc)
        r1.addWidget(self.calc_energy)
        self.calc_unit = QComboBox()
        for u in ("keV", "MeV", "GeV"):
            self.calc_unit.addItem(u, u)
        self.calc_unit.setCurrentIndex(1)
        self.calc_unit.currentIndexChanged.connect(self._calc)
        r1.addWidget(self.calc_unit)
        r1.addStretch(1)
        cl.addLayout(r1)
        self.calc_out = QLabel("")
        self.calc_out.setWordWrap(True)
        self.calc_out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(self.calc_out)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Energía con un alcance CSDA de"))
        self.calc_depth = QDoubleSpinBox()
        self.calc_depth.setDecimals(4)
        self.calc_depth.setRange(1e-6, 1e5)
        self.calc_depth.setValue(1.0)
        self.calc_depth.setSuffix(" cm")
        self.calc_depth.valueChanged.connect(self._calc)
        r2.addWidget(self.calc_depth)
        self.calc_inv = QLabel("")
        r2.addWidget(self.calc_inv, 1)
        cl.addLayout(r2)
        rl.addWidget(calc)
        split.addWidget(right)
        split.setSizes([260, 800])
        self.data: MaterialData | None = None
        self.opts: DisplayOptions | None = None
        self.checked: dict[str, set[str]] = {}
        self._rows: tuple[list[str], list[np.ndarray]] = ([], [])

    # -- datos ---------------------------------------------------------------------------------------------
    def set_data(self, data: MaterialData, title: str, opts: DisplayOptions | None = None, context: dict | None = None
                 ) -> None:
        self.data = data
        self.opts = opts
        row = max(self.list.currentRow(), 0)
        self.list.blockSignals(True)
        self.list.clear()
        for m in data.materials:
            dens = f", {m.density:g} g/cm³" if m.density else ""
            it = QListWidgetItem(f"{m.index} · {m.name or m.file}{dens}")
            it.setToolTip(m.file)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if data.materials:
            self.list.setCurrentRow(min(row, len(data.materials) - 1))
        self._material_changed(self.list.currentRow())
        if data.has_tables:
            self.note.setText("")
        else:
            self.note.setText("penhmain solo escribe las tablas (poderes de frenado, alcances, coeficientes de "
                              "atenuación) cuando la simulación tiene un único material.")

    def set_options(self, opts: DisplayOptions) -> None:
        self.opts = opts
        self.redraw()

    def material(self) -> MaterialInfo | None:
        i = self.list.currentRow()
        if self.data is None or not (0 <= i < len(self.data.materials)):
            return None
        return self.data.materials[i]

    def _material_changed(self, _row: int) -> None:
        m = self.material()
        cur = self.table.currentText()
        self.table.blockSignals(True)
        self.table.clear()
        for t in (m.tables if m else []):
            self.table.addItem(t.title + (f" ({t.source})" if t.source == "PENHAN" else ""), t.title)
        k = self.table.findData(cur.split(" (PENHAN)")[0])
        self.table.setCurrentIndex(max(k, 0))
        self.table.blockSignals(False)
        self.table.setEnabled(bool(m and m.tables))
        cur_p = self.calc_particle.currentData()
        self.calc_particle.blockSignals(True)
        self.calc_particle.clear()
        for kp, name in self.CALC.items():
            ok = m is not None and (m.table(ATTENUATION_TABLE) if kp == 2 else m.table(STOPPING_TABLES[kp][0]))
            if ok:
                self.calc_particle.addItem(name.capitalize(), kp)
        self.calc_particle.setCurrentIndex(max(self.calc_particle.findData(cur_p), 0))
        self.calc_particle.blockSignals(False)
        self._table_changed()
        self._calc()

    def _current_table(self):
        m = self.material()
        title = self.table.currentData()
        return next((t for t in m.tables if t.title == title), None) if m and title else None

    def _table_changed(self) -> None:
        t = self._current_table()
        while self.cols_box.count():
            it = self.cols_box.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        if t is None:
            self.redraw()
            return
        names = t.columns[1:]
        chosen = self.checked.get(t.title)
        if chosen is None:
            first_unit = t.units[1] if len(t.units) > 1 else ""
            chosen = {n for n, u in zip(names, t.units[1:]) if u == first_unit and u}
            if not chosen and names:
                chosen = {names[0]}
            self.checked[t.title] = chosen
        for n, u in zip(names, t.units[1:]):
            cb = QCheckBox(n)
            cb.setToolTip(u or "sin unidades")
            cb.setChecked(n in chosen)
            cb.toggled.connect(lambda on, name=n, title=t.title: self._toggle_col(title, name, on))
            self.cols_box.addWidget(cb)
        self.redraw()

    def _toggle_col(self, title: str, name: str, on: bool) -> None:
        s = self.checked.setdefault(title, set())
        (s.add if on else s.discard)(name)
        self.redraw()

    def redraw(self) -> None:
        pc = self.plot
        pc.clear()
        self._rows = ([], [])
        t = self._current_table()
        m = self.material()
        if t is None or m is None:
            pc.draw()
            return
        e = t.column("Energy")
        eu = _energy_unit(self.opts, float(np.max(e)) if e is not None and len(e) else 1.0)
        ef = ENERGY_SCALE[eu]
        chosen = [n for n in t.columns[1:] if n in self.checked.get(t.title, set())]
        groups: dict[str, list[tuple[str, np.ndarray]]] = {}
        for n in chosen:
            k = t.columns.index(n)
            f, unit = _display_unit(t.units[k], self.in_cm.isChecked(), m.density)
            groups.setdefault(unit, []).append((n, t.data[:, k] * f))
        if not groups:
            pc.draw()
            return
        units = list(groups)[:3]  # como mucho tres paneles (uno por unidad; nunca dos escalas en un panel)
        axes = pc.figure.subplots(len(units), 1, sharex=True, squeeze=False)[:, 0]
        cols, data = [f"Energía ({eu})"], [e * ef]
        color_i = 0
        for ax, unit in zip(axes, units):
            positive = True
            for n, y in groups[unit]:
                ax.plot(e * ef, y, color=CATEGORICAL[color_i % len(CATEGORICAL)], label=n)
                color_i += 1
                positive &= bool(np.all(y > 0))
                cols.append(f"{n} ({unit})" if unit else n)
                data.append(y)
            ax.set_xscale("log")
            if positive:
                ax.set_yscale("log")
            ax.set_ylabel(unit or "sin unidades")
            if len(groups[unit]) >= 2:
                ax.legend(loc="best")
        axes[-1].set_xlabel(f"Energía ({eu})")
        axes[0].set_title(f"{t.title} · {m.name or m.file}", loc="left")
        pc.draw()
        self._rows = (cols, data)

    def table_rows(self) -> tuple[list[str], list[np.ndarray]]:
        return self._rows

    # -- calculadora ------------------------------------------------------------------------------------------
    def _calc(self) -> None:
        m = self.material()
        kp = self.calc_particle.currentData()
        if m is None or kp is None:
            self.calc_out.setText("Sin tablas para este material.")
            self.calc_inv.setText("")
            return
        energy = self.calc_energy.value() / ENERGY_SCALE[self.calc_unit.currentData()]  # eV
        rho = m.density
        if kp == 2:
            t = m.table(ATTENUATION_TABLE)
            mu_rho = table_value(t, "Total", energy) if t else None
            if mu_rho is None:
                self.calc_out.setText("Energía fuera de la tabla.")
            else:
                txt = f"μ/ρ = {mu_rho:.4g} cm²/g"
                if rho:
                    mu = mu_rho * rho
                    txt += (f" · μ = {mu:.4g} 1/cm · recorrido libre medio {1 / mu:.4g} cm · capa hemirreductora "
                            f"{math.log(2) / mu:.4g} cm · decirreductora {math.log(10) / mu:.4g} cm")
                self.calc_out.setText(txt)
            self.calc_inv.setText("(no aplica a fotones)")
            self.calc_depth.setEnabled(False)
            return
        self.calc_depth.setEnabled(True)
        t = m.table(STOPPING_TABLES[kp][0])
        r = csda_range(t, energy) if t else None
        s = stopping_power(m, kp, energy)
        parts = []
        if r is not None:
            parts.append(f"alcance CSDA {r / rho:.4g} cm ({r:.4g} g/cm²)" if rho else f"alcance CSDA {r:.4g} g/cm²")
        if s is not None:
            parts.append(f"poder de frenado {s * 1e-6:.4g} MeV·cm²/g" + (f" ({s * 1e-6 * rho:.4g} MeV/cm)" if rho
                                                                          else ""))
        txt = " · ".join(parts)
        self.calc_out.setText(txt[:1].upper() + txt[1:] if parts else "Energía fuera de la tabla.")
        depth = self.calc_depth.value()
        en = energy_for_range(t, depth, rho) if t else None
        if en is None:
            self.calc_inv.setText("fuera de la tabla")
        else:
            u = auto_energy(en)
            self.calc_inv.setText(f"{en * ENERGY_SCALE[u]:.4g} {u}")


# ------------------------------------------------------------------------------------------------------------------
_VIEWS = {Tracks: TracksView, PhaseSpace: PhaseSpaceView, MaterialData: MaterialsView}


def view_for(window, data) -> QWidget | None:
    """La vista (una por ventana y tipo, reutilizada) para trayectorias, espacios de fases, materiales, las
    medidas de una campaña o la cascada."""
    from .campaign_view import CampaignData, CampaignView
    from .shower_view import CascadeData, ShowerView

    if isinstance(data, CampaignData):
        cls = CampaignView
    elif isinstance(data, CascadeData):
        cls = ShowerView
    else:
        cls = next((v for t, v in _VIEWS.items() if isinstance(data, t)), None)
    if cls is None:
        return None
    cache = getattr(window, "_special_views", None)
    if cache is None:
        cache = {}
        window._special_views = cache
    w = cache.get(cls)
    if w is None:
        w = cls(settings=getattr(window, "settings", None)) if cls is ShowerView else cls()
        cache[cls] = w
    return w


__all__ = ["MaterialsView", "PhaseSpaceView", "TracksView", "view_for"]
