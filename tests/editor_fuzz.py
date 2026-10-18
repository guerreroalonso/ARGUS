"""Cadenas de ediciones al azar (punto 4) para comprobarlas con PENGEOM real.

Se usa en ``test_editor_random.py`` (pocas cadenas) y a mano con muchas:
``python tests/editor_fuzz.py 200`` (sin eliminar) o ``python tests/editor_fuzz.py 100 --borrar`` (también
elimina cuerpos, módulos, clones, limitantes y la caja CT, quita y da la vuelta a superficies y, de vez en
cuando, vacía la geometría hasta el último cuerpo).

Con ``deletions`` (``--borrar``), tras cada cambio se comprueba además con LOCATE, antes y después, que los
puntos que no están en lo editado conservan su cuerpo (con la numeración nueva) y su material, y que el volumen
de lo eliminado pasa a lo que lo alojaba. Al final de cada cadena, PENGEOM real lee la geometría con las mismas
tablas y los mismos LOCATE que ARGUS.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from argus.pengeom import placement as pl  # noqa: E402
from argus.pengeom import surfaces as sf  # noqa: E402
from argus.pengeom.bounds import estimate_bounds  # noqa: E402
from argus.pengeom.compiler import compile_geometry  # noqa: E402
from argus.pengeom.duplicate import check_copies, duplicate, materialize_clone  # noqa: E402
from argus.pengeom.edit import (  # noqa: E402
    EditError,
    Primitive,
    add_primitive,
    check_convert,
    check_placement,
    container_chain,
    convert_to_module,
    delete_body,
    delete_ct,
    delete_problem,
    hosts,
    is_editable,
    logical_parent,
    top_level,
)
from argus.pengeom.extent import element_box, element_extent, solid_bodies  # noqa: E402
from argus.pengeom.identity import renumber_map  # noqa: E402
from argus.pengeom.locate import Locator  # noqa: E402
from argus.pengeom.overlap import guard  # noqa: E402
from argus.pengeom.parser import parse_geo_text  # noqa: E402
from argus.pengeom.validate import new_ct_errors, new_overflows  # noqa: E402
from argus.pengeom.wrap import check_wrap, fitted_primitive, sample_solids, wrap, wrap_candidates  # noqa: E402
from argus.pengeom.writer import write_geo_text  # noqa: E402
from argus.render.raytrace import RayTracer  # noqa: E402

CORPUS = HERE / "corpus"


def bases():
    from test_duplicate import CLONED
    from test_edit import ROTATED_MODULE
    from test_modules import NESTED

    out = [("cubo-esfera", (CORPUS / "usuario/100Mev15-174/cubo-esfera.geo").read_text(encoding="latin-1"), None),
           ("clones", CLONED, None), ("girado", ROTATED_MODULE, None), ("anidados", NESTED, None),
           ("3discs", (CORPUS / "penhan/stack/3discs.geo").read_text(encoding="latin-1"),
            [0.002, 0.004, 0.008, 0.008])]
    return out


def more_bases():
    """Más geometrías para las cadenas con eliminaciones: limitantes compartidos, PenGeomJar, la del usuario y un CT."""
    from test_review_editor import SHARED

    out = [("shared", SHARED, None)]
    for rel in ("pengeomjar/arrow", "pengeomjar/axes", "pengeomjar/chair", "pengeomjar/glass", "pengeomjar/example1",
                "pengeomjar/quadricinbox", "pengeomjar/tshell", "usuario/PruebasNuevaGeo/prueba.geo",
                "pengeomjar/onion", "pengeomjar/saturne", "pengeomjar/ctbox"):
        p = CORPUS / rel
        f = sorted(p.glob("*.geo"))[0] if p.is_dir() else p
        out.append((rel.split("/")[-1].replace(".geo", ""), f.read_text(encoding="latin-1"), None))
    return out


CT_FILES = {"ctbox": [CORPUS / "pengeomjar/box.ct"]}  # ficheros que necesita cada geometría


class Reject(Exception):
    """La ventana no haría ese cambio (o lo rechazaría al comprobarlo): se deshace."""


class Chain:
    def __init__(self, text: str, parinp, rng: np.random.Generator, deletions: bool = False, base_dir=None):
        self.parinp = parinp
        self.rng = rng
        self.deletions = deletions
        self.base_dir = base_dir
        self.doc = parse_geo_text(text, "cadena.geo")
        self.geo = compile_geometry(self.doc, parinp=parinp, base_dir=base_dir)
        assert self.geo.ok, [str(d) for d in self.geo.diagnostics]
        self.log: list[str] = []
        self.texts = [text]
        self._refresh()

    def _refresh(self):
        self.loc = Locator(self.geo)
        lo, hi = estimate_bounds(self.geo, locator=self.loc)
        size = float(np.max(hi - lo)) or 1.0
        self.bounds = (lo - 0.3 * size, hi + 0.3 * size)
        self.tracer = RayTracer(self.geo)

    def revert(self):
        """Deshace lo que haya hecho en el documento una operación rechazada."""
        self.doc = parse_geo_text(self.texts[-1], "cadena.geo")
        self.geo = compile_geometry(self.doc, parinp=self.parinp, base_dir=self.base_dir)
        self._refresh()

    def commit(self, what: str, affected=None, renames=None, key_map=None, check=None, may_fail: bool = False):
        """Relee y compila el documento editado; con ``deletions``, comprueba lo que cambia con LOCATE.

        ``affected(old, new, mapping) -> (A_old, A_new)``: cuerpos (0 = fuera o envolvente) entre los que pueden
        pasar los puntos de lo editado; los demás puntos conservan su cuerpo y su material.
        """
        old = self.geo
        text = write_geo_text(self.doc)
        if self.deletions and text == self.texts[-1]:
            raise Reject("sin cambios")
        doc = parse_geo_text(text, "cadena.geo")
        geo = compile_geometry(doc, parinp=self.parinp, base_dir=self.base_dir)
        if not geo.ok:
            if may_fail and self._pengeom_refuses(text):  # la ventana también lo compila y dice por qué no
                raise Reject("GEOMIN no lo aceptaría")
            raise AssertionError(f"{what}: GEOMIN no lo acepta: {[str(d) for d in geo.diagnostics]}\n{text}")
        if self.deletions:
            if new_overflows(old, geo) or new_ct_errors(old, geo):
                raise Reject("desbordamiento o CT")
            if check is not None:
                try:
                    check(old, geo)
                except EditError as exc:
                    raise Reject(str(exc)) from None
            self._same_elsewhere(what, old, geo, affected, renames, key_map)
        self.doc, self.geo = doc, geo
        self.texts.append(text)
        self.log.append(what)
        self._refresh()

    def _pengeom_refuses(self, text: str) -> bool:
        """¿Rechaza PENGEOM real también la geometría? (Si ARGUS la rechaza y PENGEOM no, es un fallo de ARGUS.)"""
        import tempfile

        from argus.oracle.probe import find_probe, run_probe

        if find_probe() is None:
            return True
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "cadena.geo"
            path.write_text(text, encoding="latin-1")
            for f in CT_FILES.get("ctbox", []):
                shutil.copy2(f, Path(t) / f.name)
            res = run_probe(path, parinp=self.parinp, tables=False, points=np.array([[0.0, 0.0, 0.0, 0.0, 0.0, 1.0]]))
        return not res.status.ok

    # -- antes y después ---------------------------------------------------------------
    def _points(self, old, new):
        lo, hi = self.bounds
        pts = [self.rng.uniform(lo, hi, (2500, 3))]
        for g in (old, new):
            for b in g.bodies:
                if b.origin == "enclosure":
                    continue
                e_lo, e_hi = element_box(g, b.index)
                e_lo = np.where(np.isfinite(e_lo), np.maximum(e_lo, lo), lo)
                e_hi = np.where(np.isfinite(e_hi), np.minimum(e_hi, hi), hi)
                if np.all(e_hi > e_lo):
                    pts.append(self.rng.uniform(e_lo, e_hi, (30, 3)))
        pts = np.vstack(pts)
        dirs = self.rng.normal(size=pts.shape)
        return pts, dirs / np.linalg.norm(dirs, axis=1)[:, None]

    def _same_elsewhere(self, what, old, new, affected, renames, key_map):
        mapping = renumber_map(old, new, renames, key_map)
        a_old, a_new = affected(old, new, mapping) if affected is not None else (set(), set())
        pts, dirs = self._points(old, new)
        ib0, m0 = Locator(old).locate(pts, dirs)
        ib1, m1 = Locator(new).locate(pts, dirs)

        def c_old(k):
            return 0 if k > old.nbodys or old.body(k).origin == "enclosure" else k

        def c_new(k):
            return 0 if k > new.nbodys or new.body(k).origin == "enclosure" else k

        o = np.array([c_old(int(k)) for k in ib0])
        n = np.array([c_new(int(k)) for k in ib1])
        exp = np.array([0 if k == 0 else (mapping.get(k) or -1) for k in o])
        same = (exp == n) & (m0 == m1)
        moved = np.isin(o, list(a_old)) & np.isin(n, list(a_new))
        bad = ~same & ~moved
        if bad.any():
            i = int(np.nonzero(bad)[0][0])
            raise AssertionError(f"{what}: {int(bad.sum())}/{len(pts)} puntos cambian de cuerpo o de material fuera de "
                                 f"lo editado, p. ej. {pts[i].tolist()}: antes {ib0[i]} (mat {m0[i]}), ahora {ib1[i]} "
                                 f"(mat {m1[i]}); se esperaba {exp[i]} (A_antes={sorted(a_old)}, "
                                 f"A_después={sorted(a_new)})\n{write_geo_text(self.doc)}")

    @staticmethod
    def _around(g, kbs) -> set[int]:
        """Lo que aloja a ``kbs``: los cuerpos que los excluyen (también a través de otro limitante, como un
        limitante que asoma de su cuerpo) y sus madres (0 = fuera o envolvente)."""
        from argus.pengeom.overlap import _closure

        closure = _closure(g)
        out: set[int] = set()
        for k in kbs:
            users = [b.index for b in g.bodies if b.index != k and k in closure[b.index]]
            for h in hosts(g, k) + users + [g.body(k).mother]:
                out.add(0 if not h or h > g.nbodys or g.body(h).origin == "enclosure" else h)
        return out

    @staticmethod
    def _to_new(mapping, kbs) -> set[int]:
        return {0 if k == 0 else mapping.get(k) for k in kbs} - {None}

    def ext(self, kb):
        return element_extent(self.geo, self.tracer, kb, self.bounds)

    def pick(self, pred):
        cands = [b.index for b in self.geo.bodies if pred(b)]
        return int(self.rng.choice(cands)) if cands else None

    # -- operaciones -----------------------------------------------------------------
    def op_add(self):
        g = self.geo
        cont = self.pick(lambda b: b.origin != "clone" and (is_editable(g, b.index) or b.origin == "enclosure"))
        if cont is None or not container_chain(g, cont):
            return False
        lo, hi = self.bounds
        pts = self.rng.uniform(lo, hi, (4000, 3))
        ib, _ = self.loc.locate(pts)
        inside = pts[ib == cont]
        if not len(inside):
            return False
        c = inside[self.rng.integers(len(inside))]
        size = float(np.max(hi - lo)) * self.rng.uniform(0.002, 0.05)
        kinds = ["sphere", "box", "cylinder"] + (["slab"] if self.deletions and not g.body(cont).is_module else [])
        kind = str(self.rng.choice(kinds))
        prim = Primitive(kind, center=tuple(c), radius=size / 2, height=size, sides=(size, size * 0.7, size * 1.3),
                         axis=str(self.rng.choice(list("xyz"))))
        if not check_placement(g, self.loc, prim, cont, self.bounds, n=3000).ok:
            return False
        module = bool(self.rng.random() < 0.4) and kind != "slab"
        lab = add_primitive(self.doc, g, prim, int(self.rng.integers(1, 5)), cont, parinp=self.parinp, module=module)
        a_old = {cont} | self._copies(g, {cont})
        a_old = {0 if k == g.nbodys and g.enclosure_added else k for k in a_old}

        def affected(old, new, mapping):
            b = new.find_body(lab)
            return a_old, (({b.index} | self._copies(new, {b.index})) if b is not None else set())
        self.commit(f"añadir {kind}{' módulo' if module else ''} en {cont}", affected)
        return True

    def op_convert(self):
        g = self.geo
        kb = self.pick(lambda b: not b.is_module and is_editable(g, b.index))
        if kb is None or not check_convert(g, self.loc, kb, self.bounds).ok:
            return False
        convert_to_module(self.doc, g, kb)
        self.commit(f"convertir {kb}")
        return True

    def _placeable(self):
        g = self.geo

        def ok(b):
            try:
                pl.placeable(g, b.index)
                return not pl.starred_surfaces(g, b.index)
            except EditError:
                return False
        return self.pick(ok)

    def op_move(self, rotate: bool):
        g = self.geo
        kb = self._placeable()
        if kb is None:
            return False
        ext = self.ext(kb)
        size = float(np.max(ext[1] - ext[0]))
        if rotate:
            axis = self.rng.normal(size=3)
            ang = float(self.rng.choice([90.0, -90.0, 45.0, float(self.rng.uniform(-180, 180))]))
            motion = pl.rotation_about(axis, ang, pl.element_center(ext))
        else:
            motion = pl.translation(self.rng.normal(size=3) * size * 0.3)
        if not pl.check_motion(g, self.loc, kb, motion, ext, n=4000).ok:
            return False
        try:
            pl.move_element(self.doc, g, kb, motion, self.parinp, bounds=self.bounds)
        except EditError:
            return False
        moving = pl.descendants(g, kb)
        moving |= self._copies(g, moving)
        a_old = moving | self._around(g, moving)

        def affected(old, new, mapping):
            return a_old, self._to_new(mapping, a_old)
        self.commit(f"{'girar' if rotate else 'mover'} {kb}", affected)
        return True

    def op_duplicate(self):
        g = self.geo
        kb = self.pick(lambda b: b.origin != "enclosure" and b.index <= g.nbodys)
        if kb is None:
            return False
        linked = bool(self.rng.random() < 0.5)
        try:
            ext = self.ext(kb)
        except Exception:  # noqa: BLE001
            return False
        cont = logical_parent(g, kb) or top_level(g)
        size = ext[1] - ext[0]
        axis = int(self.rng.integers(3))
        step = np.zeros(3)
        step[axis] = float(size[axis]) * float(self.rng.choice([1.0, -1.0])) * self.rng.uniform(1.0, 1.5)
        count = int(self.rng.integers(1, 3))
        if not check_copies(g, self.loc, kb, step, count, cont, ext, n=4000, bounds=self.bounds).ok:
            return False
        try:
            labels = duplicate(self.doc, g, kb, step, count, cont, linked=linked, parinp=self.parinp)
        except EditError:
            return False
        a_old = {cont} | self._copies(g, {cont})
        a_old = {0 if k > g.nbodys or (k == g.nbodys and g.enclosure_added) else k for k in a_old}

        def affected(old, new, mapping):
            out: set[int] = set()
            for lab in labels:
                b = new.find_body(lab)
                if b is not None:
                    out |= solid_bodies(new, b.index)
            return a_old, out | self._copies(new, out)
        self.commit(f"{'clonar' if linked else 'copiar'} {kb} ×{count}", affected, may_fail=True)
        return True

    def op_wrap(self):
        g = self.geo
        kb = self.pick(lambda b: b.origin != "enclosure")
        if kb is None:
            return False
        cont, cands = wrap_candidates(g, kb)
        if kb not in cands:
            return False
        others = [k for k in cands if k != kb]
        chosen = [kb] + ([int(self.rng.choice(others))] if others and self.rng.random() < 0.5 else [])
        exts = [self.ext(k) for k in chosen]
        kind = str(self.rng.choice(["box", "cylinder", "sphere"]))
        pts = sample_solids(g, self.loc, chosen, exts, n=4000, bounds=self.bounds)
        prim = fitted_primitive(exts, kind, margin=0.0, axis=str(self.rng.choice(list("xyz"))), points=pts)
        if not check_wrap(g, self.loc, chosen, prim, cont, exts, self.bounds, n=4000).ok:
            return False
        try:
            mlab = wrap(self.doc, g, chosen, prim, parinp=self.parinp)
        except EditError:
            return False
        a_old = {0 if cont > g.nbodys or (cont == g.nbodys and g.enclosure_added) else cont}
        a_old |= self._copies(g, {cont} - {0})

        def affected(old, new, mapping):
            b = new.find_body(mlab)
            return a_old, (({b.index} | self._copies(new, {b.index})) if b is not None else set())
        self.commit(f"envolver {chosen}", affected)
        return True

    def op_materialize(self):
        g = self.geo
        kb = self.pick(lambda b: b.origin == "clone" and b.element is b.clone_element)
        if kb is None:
            return False
        try:
            _notes, key_map = materialize_clone(self.doc, g, kb, self.parinp)
        except EditError:
            return False
        self.commit(f"materializar {kb}", key_map=key_map)
        return True

    def op_rename(self):
        g = self.geo
        kb = self.pick(lambda b: b.origin != "enclosure")
        if kb is None:
            return False
        new = "".join(self.rng.choice(list("ABCDEFGHJKLMNPQRSTUVWXYZ"), 3))
        try:
            old, new4 = sf.rename_body(self.doc, g, kb, new)
        except EditError:
            return False
        self.commit(f"renombrar {kb} a {new}", renames={old: new4})
        return True

    def op_implicit(self):
        surfs = [s for s in self.doc.surfaces() if not s.implicit
                 and not any(p.ipar and p.name.endswith("SCALE") for p in s.params)]
        if not surfs:
            return False
        sf.to_implicit(surfs[int(self.rng.integers(len(surfs)))], self.parinp)
        self.commit("implícita")
        return True

    # -- eliminar (``deletions``) ------------------------------------------------------------
    @staticmethod
    def _copies(g, kbs) -> set[int]:
        """Copias (de clones, a cualquier profundidad) de los cuerpos ``kbs``: cambian con ellos."""
        return pl._copies_of(g, set(k for k in kbs if k))

    def _delete_affected(self, g, dead):
        around = self._around(g, dead) - set(dead)

        def affected(old, new, mapping):
            return set(dead), self._to_new(mapping, around) | {0}
        return affected

    def op_delete(self):
        """Un cuerpo o un módulo vacío (también limitantes y elementos de módulos clonados), como la tecla Supr."""
        g = self.geo
        kb = self.pick(lambda b: b.index != getattr(g, "ct_body", None) and b.origin == "user"
                       and delete_problem(self.doc, g, b.index) is None)
        if kb is None:
            return False
        label = g.balias(kb)
        delete_body(self.doc, g, kb)
        dead = {kb} | self._copies(g, {kb})
        self.commit(f"eliminar {kb} «{label.strip()}»", self._delete_affected(g, dead))
        return True

    def op_delete_clone(self):
        g = self.geo
        kb = self.pick(lambda b: b.origin == "clone" and b.element is b.clone_element and b.document is g.document
                       and delete_problem(self.doc, g, b.index) is None)
        if kb is None:
            return False
        try:
            pl.delete_clone(self.doc, g, kb)
        except EditError:
            return False
        dead = solid_bodies(g, kb) | self._copies(g, solid_bodies(g, kb))
        self.commit(f"eliminar clon {kb}", self._delete_affected(g, dead))
        return True

    def op_delete_ct(self):
        g = self.geo
        kb = getattr(g, "ct_body", None)
        if not kb or delete_problem(self.doc, g, kb) is not None:
            return False
        delete_ct(self.doc, g)
        self.commit("eliminar CT", self._delete_affected(g, {kb}))
        return True

    def op_surface(self, kind: str):
        """Quitar una superficie, darle la vuelta o añadir un plano nuevo (con la comprobación de la ventana)."""
        g = self.geo
        kb = self.pick(lambda b: is_editable(g, b.index))
        if kb is None:
            return False
        el = g.body(kb).element
        label = g.balias(kb)
        srefs = [r for r in el.items if r.is_surface]
        try:
            if kind == "quitar":
                if len(srefs) < 2:
                    return False
                sf.remove_surface(self.doc, g, kb, srefs[int(self.rng.integers(len(srefs)))].label)
            elif kind == "lado":
                if not srefs:
                    return False
                r = srefs[int(self.rng.integers(len(srefs)))]
                sf.set_side(self.doc, g, kb, r.label, -(r.side or 1))
            else:
                lo, hi = self.bounds
                pts = self.rng.uniform(lo, hi, (3000, 3))
                ib, _ = self.loc.locate(pts)
                inside = pts[ib == kb]
                if not len(inside):
                    return False
                p = inside[self.rng.integers(len(inside))]
                new = sf.new_plane(self.doc, g, kb, p, self.rng.normal(size=3), parinp=self.parinp)
                sf.add_surface(self.doc, g, kb, new, int(self.rng.choice([-1, 1])))
        except EditError:
            return False
        edited = solid_bodies(g, kb)
        edited |= self._copies(g, edited)
        a_old = edited | self._around(g, edited) | {0}

        def affected(old, new, mapping):
            return a_old, self._to_new(mapping, a_old)
        self.commit(f"superficie ({kind}) de {kb}", affected, check=guard(self.bounds, [label]), may_fail=True)
        return True

    def step(self):
        ops = [self.op_add, self.op_convert, lambda: self.op_move(False), lambda: self.op_move(True),
               self.op_duplicate, self.op_wrap, self.op_materialize, self.op_rename, self.op_implicit]
        weights = [3, 1, 2, 3, 3, 2, 1, 1, 1]
        if self.deletions:
            ops += [self.op_delete, self.op_delete_clone, self.op_delete_ct, lambda: self.op_surface("quitar"),
                    lambda: self.op_surface("lado"), lambda: self.op_surface("plano")]
            weights += [4, 1.5, 1, 1, 0.5, 0.7]
        w = np.array(weights, dtype=float)
        for _ in range(12):
            op = ops[int(self.rng.choice(len(ops), p=w / w.sum()))]
            try:
                if op():
                    return True
            except Reject:
                pass
            if write_geo_text(self.doc) != self.texts[-1]:  # una operación que no ha seguido: se deshace
                self.revert()
        return False

    def drain(self):
        """Elimina lo que se pueda (clones y cuerpos) hasta que no quede nada que eliminar."""
        for _ in range(400):
            done = False
            for op in (self.op_delete_clone, self.op_delete, self.op_delete_ct):
                try:
                    if op():
                        done = True
                        break
                except Reject:
                    pass
                self.revert()
            if not done:
                return


def check_chain(chain: Chain, tmp: Path, n_points: int = 3000) -> None:
    """PENGEOM real lee la geometría de la cadena con las mismas tablas y los mismos LOCATE."""
    from geo_oracle import assert_pengeom_agrees, random_points

    lo, hi = chain.bounds
    pts, dirs = random_points(lo, hi, n_points, seed=int(chain.rng.integers(1 << 30)))
    # Además, puntos dentro de cada cuerpo pequeño (los aleatorios casi no los tocan).
    extra = []
    for b in chain.geo.bodies:
        if b.origin == "enclosure":
            continue
        try:
            e_lo, e_hi = chain.ext(b.index)
        except Exception:  # noqa: BLE001
            continue
        extra.append(chain.rng.uniform(e_lo, e_hi, (40, 3)))
    if extra:
        more = np.vstack(extra)
        d2 = chain.rng.normal(size=more.shape)
        d2 /= np.linalg.norm(d2, axis=1)[:, None]
        pts, dirs = np.vstack([pts, more]), np.vstack([dirs, d2])
    assert_pengeom_agrees(write_geo_text(chain.doc), tmp, pts, dirs, parinp=chain.parinp, name="cadena.geo")


def run(n_chains: int, steps: int = 6, seed: int = 0, verbose: bool = True, deletions: bool = False,
        drain_every: int = 0, keep_going: bool = False, names: list[str] | None = None) -> dict:
    """``n_chains`` cadenas de ``steps`` ediciones; con ``deletions``, también eliminaciones y la comprobación de
    antes y después, y cada ``drain_every`` cadenas se vacía la geometría. Con ``keep_going`` los fallos se
    cuentan (``stats["problems"]``) en vez de parar; ``names``: solo esas geometrías de partida."""
    import tempfile
    import traceback

    stats = {"chains": 0, "edits": 0, "ops": {}, "problems": []}
    rng = np.random.default_rng(seed)
    base = bases() + (more_bases() if deletions or names else [])
    if names:
        base = [b for b in base if b[0] in names]
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for name in CT_FILES:
            for f in CT_FILES[name]:
                shutil.copy2(f, tmp / f.name)
        for i in range(n_chains):
            name, text, parinp = base[i % len(base)]
            chain = Chain(text, parinp, np.random.default_rng(int(rng.integers(1 << 31))), deletions=deletions,
                          base_dir=tmp)
            try:
                for _ in range(steps):
                    chain.step()
                if drain_every and i % drain_every == drain_every - 1:
                    chain.drain()
                check_chain(chain, tmp)
            except AssertionError as exc:
                if not keep_going:
                    raise
                stats["problems"].append(f"{name}: {str(exc).splitlines()[0][:400]}")
                if verbose:
                    print(f"PROBLEMA {i:4d} {name}: {traceback.format_exc(limit=3)[-1500:]}")
            stats["chains"] += 1
            stats["edits"] += len(chain.log)
            for entry in chain.log:
                key = entry.split()[0]
                stats["ops"][key] = stats["ops"].get(key, 0) + 1
            if verbose:
                print(f"{i:4d} {name:12s} {len(chain.log)} ediciones: {'; '.join(chain.log)}", flush=True)
    return stats


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 20
    borrar = "--borrar" in sys.argv
    s = run(n, deletions=borrar, drain_every=4 if borrar else 0, keep_going=True,
            seed=int(args[1]) if len(args) > 1 else 0)
    print({k: (len(v) if k == "problems" else v) for k, v in s.items()})
    for prob in s["problems"]:
        print(" -", prob)
