"""Undécima revisión (0.17.3): PENGEOM (cajas exactas de los cuerpos, «Envolver», «Duplicar», la fuente extensa, la
caja de la escena y los rayos que vienen de fuera). La de la copa de PenGeomJar, en ``test_review11_corpus.py``.

Cada prueba falla con el código de 0.17.2."""

from __future__ import annotations

import numpy as np
import pytest

from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.session import open_geometry
from test_track_layer import need_gpu  # noqa: F401

Z64 = "0" * 64
END = "END      " + "0" * 55 + "\n"


def _write(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="latin-1")
    s = open_geometry(p)
    assert s.ok, s.problem
    return s


def _lamina(angle: float, thick: float) -> str:
    """Un disco de radio 2 y grosor ``thick`` girado ``angle``° (THETA), dentro de una caja de agua de 10 cm."""
    h = thick / 2
    rot = f"  THETA=({angle:+.15E},   0) DEG\n"
    return ("Lamina fina inclinada dentro de una caja de agua\n" + Z64 + "\n"
            "SURFACE (   1)   cilindro r=2\nINDICES=( 1, 1, 0, 0,-1)\nX-SCALE=(+2.000000000000000E+00,   0)\n"
            "Y-SCALE=(+2.000000000000000E+00,   0)\n" + rot + Z64 + "\n"
            f"SURFACE (   2)   grosor\nINDICES=( 0, 0, 1, 0,-1)\nZ-SCALE=({h:+.15E},   0)\n" + rot + Z64 + "\n"
            "BODY    (   1)   lamina\nMATERIAL(   2)\nSURFACE (   1), SIDE POINTER=(-1)\n"
            "SURFACE (   2), SIDE POINTER=(-1)\n" + Z64 + "\n"
            "SURFACE (   3)   |x|<5\nINDICES=( 1, 0, 0, 0,-1)\nX-SCALE=(+5.000000000000000E+00,   0)\n" + Z64 + "\n"
            "SURFACE (   4)   |y|<5\nINDICES=( 0, 1, 0, 0,-1)\nY-SCALE=(+5.000000000000000E+00,   0)\n" + Z64 + "\n"
            "SURFACE (   5)   |z|<5\nINDICES=( 0, 0, 1, 0,-1)\nZ-SCALE=(+5.000000000000000E+00,   0)\n" + Z64 + "\n"
            "MODULE  (   2)   caja de agua\nMATERIAL(   1)\nSURFACE (   3), SIDE POINTER=(-1)\n"
            "SURFACE (   4), SIDE POINTER=(-1)\nSURFACE (   5), SIDE POINTER=(-1)\nBODY    (   1)\n" + Z64 + "\n"
            + END)


def _rotated(theta: float, omega: float = 0.0) -> str:
    out = f"  THETA=({theta:+.15E},   0) DEG\n" if theta else ""
    return out + (f"  OMEGA=({omega:+.15E},   0) DEG\n" if omega else "")


def _cube(theta: float, omega: float) -> str:
    """Un cubo de 2 cm girado, solo."""
    rot = _rotated(theta, omega)
    surf = "".join(f"SURFACE (   {k})   |{a}|<1\nINDICES=({i})\n" + rot + Z64 + "\n"
                   for k, (a, i) in enumerate((("x", " 1, 0, 0, 0,-1"), ("y", " 0, 1, 0, 0,-1"),
                                               ("z", " 0, 0, 1, 0,-1")), start=1))
    return ("Cubo girado\n" + Z64 + "\n" + surf + "BODY    (   1)   cubo\nMATERIAL(   1)\n"
            + "".join(f"SURFACE (   {k}), SIDE POINTER=(-1)\n" for k in (1, 2, 3)) + Z64 + "\n" + END)


# -- la caja exacta de una región de cuádricas -------------------------------------------------------------------------

def _plane(n, d):
    return np.zeros((3, 3)), np.asarray(n, float), -float(d)


def _sphere(c, r):
    c = np.asarray(c, float)
    return np.eye(3), -2.0 * c, float(c @ c - r * r)


def _rot_x(deg):
    t = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])


def _turn(con, rot):
    a, b, c = con
    return rot @ a @ rot.T, rot @ b, c


def test_the_exact_box_of_known_regions():
    from argus.pengeom.support import region_box

    lo, hi = region_box([_sphere([1, 2, 3], 2)])
    assert np.allclose(lo, [-1, 0, 1], atol=1e-9) and np.allclose(hi, [3, 4, 5])
    cube = [_turn(_plane(n, 1), _rot_x(45)) for n in np.vstack([np.eye(3), -np.eye(3)])]
    lo, hi = region_box(cube)
    assert np.allclose(hi, [1, np.sqrt(2), np.sqrt(2)]) and np.allclose(lo, -hi)
    cyl_z = (np.diag([1.0, 1.0, 0.0]), np.zeros(3), -1.0)
    lo, hi = region_box([cyl_z])
    assert np.allclose(hi[:2], 1) and hi[2] == np.inf and lo[2] == -np.inf
    lo, hi = region_box([_turn(cyl_z, _rot_x(30))])  # (girado alrededor de x: sin cota en y y z)
    assert np.allclose(lo[0], -1, atol=1e-6) and np.allclose(hi[0], 1, atol=1e-6)
    assert np.all(np.isinf(lo[1:])) and np.all(np.isinf(hi[1:]))
    lo, hi = region_box([_turn(cyl_z, _rot_x(30) @ _rot_x(30).T @ np.array([[0.8, 0, 0.6], [0, 1, 0],
                                                                           [-0.6, 0, 0.8]]) @ _rot_x(30))])
    assert np.all(np.isinf(lo)) and np.all(np.isinf(hi))  # (oblicuo en todos los ejes y sin tapas)
    lo, hi = region_box([_sphere([0, 0, 0], 1), _sphere([5, 0, 0], 1)])
    assert np.all(hi < lo)  # vacía
    lo, hi = region_box([_sphere([0, 0, 0], 2), (np.diag([1.0, 1.0, 0.0]), np.array([-1.0, 0, 0]), -0.75)])
    assert np.allclose(lo, [-0.5, -1, -2]) and np.allclose(hi, [1.5, 1, 2])  # esfera ∩ cilindro
    three = [(np.diag(v), np.zeros(3), -1.0) for v in ([0, 1.0, 1.0], [1.0, 0, 1.0], [1.0, 1.0, 0])]
    lo, hi = region_box(three)  # (tres cilindros: el sólido de Steinmetz)
    assert np.allclose(hi, 1) and np.allclose(lo, -1)


def test_the_exact_box_never_leaves_out_points_of_random_regions():
    """Regiones al azar (esferas, elipsoides, cilindros, conos y exteriores de esferas, girados y desplazados, con
    planos): ningún punto de la región queda fuera de su caja."""
    from argus.pengeom.support import region_box

    rng = np.random.default_rng(2026)

    def rot():
        q = rng.normal(size=4)
        a, b, c, d = q / np.linalg.norm(q)
        return np.array([[a * a + b * b - c * c - d * d, 2 * (b * c - a * d), 2 * (b * d + a * c)],
                         [2 * (b * c + a * d), a * a - b * b + c * c - d * d, 2 * (c * d - a * b)],
                         [2 * (b * d - a * c), 2 * (c * d + a * b), a * a - b * b - c * c + d * d]])

    def shape(kind):
        r, s = rot(), rng.normal(scale=0.5, size=3)
        if kind == "plane":
            n = rng.normal(size=3)
            return _plane(n / np.linalg.norm(n), rng.uniform(0.2, 1.0))
        a = {"ellipsoid": np.diag(1 / rng.uniform(0.3, 1.5, 3) ** 2),
             "cylinder": np.diag(np.r_[1 / rng.uniform(0.3, 1.2, 2) ** 2, 0.0]),
             "cone": np.diag([1.0, 1.0, -rng.uniform(0.3, 1.0) ** 2])}[kind] if kind != "outside" else np.eye(3)
        c = -1.0 if kind in ("ellipsoid", "cylinder") else (0.0 if kind == "cone" else -rng.uniform(0.2, 0.8) ** 2)
        a2 = r @ a @ r.T
        out = (a2, -2 * a2 @ s, float(s @ a2 @ s + c))
        return (-out[0], -out[1], -out[2]) if kind == "outside" else out

    checked = 0
    for _ in range(25):
        cons = [shape("ellipsoid")] + [shape(rng.choice(["plane", "ellipsoid", "cylinder", "cone", "outside"]))
                                       for _ in range(rng.integers(1, 4))]
        lo, hi = region_box(cons)
        assert np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) or np.all(hi < lo)
        p = rng.uniform(-3, 3, (200000, 3))
        ok = np.ones(len(p), dtype=bool)
        for a, b, c in cons:
            ok &= np.einsum("ij,jk,ik->i", p, a, p) + p @ b + c <= 0
        if ok.sum() < 10:
            continue
        checked += 1
        q = p[ok]
        assert np.all(q.min(axis=0) >= lo - 1e-9) and np.all(q.max(axis=0) <= hi + 1e-9)
    assert checked >= 10


# -- «Envolver», «Pegar», «Duplicar»: la caja de un elemento -----------------------------------------------------------

@pytest.mark.parametrize("angle", [80.0, 30.0])
def test_a_thin_inclined_lamina_has_its_whole_box_and_wrapping_does_not_cut_it(tmp_path, angle):
    """Una lámina de 50 µm girada: la caja de los rayos se quedaba 0,37 cm corta (la rejilla no la veía entera),
    «Envolver en un módulo» decía que la contenía entera y le quitaba el 11 % (a agua, también en PENGEOM)."""
    from argus.pengeom.extent import element_extent, members_box
    from argus.pengeom.locate import Locator
    from argus.pengeom.wrap import check_wrap, fitted_primitive, sample_solids, wrap, wrap_candidates
    from argus.pengeom.writer import write_geo_text
    from argus.render.raytrace import RayTracer

    thick = 0.005
    s = _write(tmp_path, "lamina.geo", _lamina(angle, thick))
    geo = s.geo
    ext = element_extent(geo, RayTracer(geo), 1, s.bounds)
    th = np.radians(angle)
    n = np.array([np.sin(th), 0.0, np.cos(th)])
    half = 2.0 * np.sqrt(1 - n ** 2) + thick / 2 * np.abs(n)
    assert np.allclose(ext[0], -half, atol=1e-9) and np.allclose(ext[1], half, atol=1e-9)
    cont, _ = wrap_candidates(geo, 1)
    exts = [members_box(geo, 1, ext, s.bounds)]
    pts = sample_solids(geo, s.locator, [1], exts, n=6000, bounds=s.bounds)
    prim = fitted_primitive(exts, "box", 0.0, "z", pts)
    assert check_wrap(geo, s.locator, [1], prim, cont, [ext], s.bounds).ok
    wrap(s.doc, geo, [1], prim, None, "", s.parinp)
    out = tmp_path / "envuelta.geo"
    out.write_text(write_geo_text(s.doc), encoding="latin-1")
    geo2 = compile_geometry(load_geometry(out), base_dir=tmp_path)
    p = np.random.default_rng(1).uniform(-half - 0.01, half + 0.01, (400000, 3))
    m1, m2 = Locator(geo).locate(p)[1], Locator(geo2).locate(p)[1]
    assert (m1 == 2).sum() > 500 and not np.any((m1 == 2) & (m2 != 2))


def test_a_rotated_cube_alone_has_its_whole_box(tmp_path):
    """Un cubo de 2 cm girado, solo: la caja de la escena y la del cubo dejaban fuera sus esquinas (el 2,3 % del cubo
    pasaba a vacío al envolverlo)."""
    from argus.pengeom.extent import _matrix, element_extent
    from argus.render.raytrace import RayTracer

    s = _write(tmp_path, "cubo.geo", _cube(35.0, 20.0))
    p = np.random.default_rng(0).uniform(-2, 2, (400000, 3))
    mine = p[s.locator.locate(p)[0] == 1]
    ext = element_extent(s.geo, RayTracer(s.geo), 1, s.bounds)
    assert np.all(mine.min(axis=0) >= ext[0]) and np.all(mine.max(axis=0) <= ext[1])
    assert np.all(mine.min(axis=0) >= s.bounds[0]) and np.all(mine.max(axis=0) <= s.bounds[1])
    # Justa: la de sus vértices (cada uno, el corte de tres caras: x'² = 1 son dos planos, x' = ±1).
    normals = []
    for ks, _side in s.geo.body(1).own_surfaces():
        if s.geo.surface(ks).origin == "enclosure":
            continue
        a, _b, _c = _matrix(s.geo.surface(ks).q)
        lam, vec = np.linalg.eigh(a)
        normals.append(vec[:, int(np.argmax(np.abs(lam)))] * np.sqrt(np.abs(lam).max()))
    m = np.array(normals)
    verts = np.array([np.linalg.solve(m, [a, b, c]) for a in (1, -1) for b in (1, -1) for c in (1, -1)])
    assert np.allclose(ext[0], verts.min(axis=0), atol=1e-9) and np.allclose(ext[1], verts.max(axis=0), atol=1e-9)


FOIL = ("Lamina de 0,9 micras casi horizontal\n" + Z64 + "\n"
        "SURFACE (   1)   disco\nINDICES=( 1, 1, 0, 0,-1)\nX-SCALE=(+1.866229631417323E+00,   0)\n"
        "Y-SCALE=(+1.082320828598078E+00,   0)\n  OMEGA=(-2.769623275491000E+01,   0) DEG\n"
        "  THETA=(+3.553092054732015E+02,   0) DEG\n    PHI=(+3.546518357942120E+02,   0) DEG\n"
        "X-SHIFT=(+1.949560265215352E-01,   0)\nY-SHIFT=(+5.231633296098829E-01,   0)\n"
        "Z-SHIFT=(+7.629071511883703E-01,   0)\n" + Z64 + "\n"
        "SURFACE (   2)   lamina\nINDICES=( 0, 0, 1, 0,-1)\nZ-SCALE=(+4.403544397365005E-05,   0)\n"
        "  OMEGA=(-2.769623275491000E+01,   0) DEG\n  THETA=(+3.553092054732015E+02,   0) DEG\n"
        "    PHI=(+3.546518357942120E+02,   0) DEG\nX-SHIFT=(+1.949560265215352E-01,   0)\n"
        "Y-SHIFT=(+5.231633296098829E-01,   0)\nZ-SHIFT=(+7.629071511883703E-01,   0)\n" + Z64 + "\n"
        "BODY    (   1)   foil\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\nSURFACE (   2), SIDE POINTER=(-1)\n"
        + Z64 + "\n" + END)


def test_the_source_box_of_a_thin_foil_holds_all_of_it(tmp_path):
    """«Fuente extensa en» una lámina de 0,9 µm casi horizontal, sola: la caja SBOX dejaba fuera el 64 % (no
    emitiría) y decía «sin cota»; la de la escena era de 0,2 × 0,13 × 0,06 cm."""
    from argus.pengeom.extent import source_extent
    from argus.render.raytrace import RayTracer

    s = _write(tmp_path, "foil.geo", FOIL)
    lo, hi, state = source_extent(s.geo, 1, s.bounds)
    assert state == "ok"
    rt = RayTracer(s.geo)
    xs, ys = np.meshgrid(np.linspace(-2.5, 2.5, 201), np.linspace(-1.5, 2.5, 161))
    o = np.c_[xs.ravel(), ys.ravel(), np.full(xs.size, 5.0)]
    d = np.tile([0.0, 0.0, -1.0], (len(o), 1))
    t, kb, _ = rt.render(o, d, rt.default_visibility())
    hits = o[kb == 1] + t[kb == 1, None] * d[kb == 1]
    assert len(hits) > 1000
    assert np.all(hits >= lo - 1e-6) and np.all(hits <= hi + 1e-6)
    assert np.all(hits.min(axis=0) >= s.bounds[0] - 1e-6) and np.all(hits.max(axis=0) <= s.bounds[1] + 1e-6)


def _cylinder(theta: float) -> str:
    rot = f"  THETA=({theta:+.15E},   0) DEG\n" if theta else ""
    return ("Cilindro r=1, h=4\n" + Z64 + "\n"
            "SURFACE (   1)   cilindro\nINDICES=( 1, 1, 0, 0,-1)\n" + rot + Z64 + "\n"
            "SURFACE (   2)   tapas\nINDICES=( 0, 0, 1, 0,-1)\nZ-SCALE=(+2.000000000000000E+00,   0)\n" + rot
            + Z64 + "\nBODY    (   1)   cilindro\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n"
            "SURFACE (   2), SIDE POINTER=(-1)\n" + Z64 + "\n" + END)


def test_a_rotated_body_can_be_duplicated(tmp_path):
    """Un cilindro con tapas girado 30°: «no es un volumen cerrado (sus superficies no lo acotan)» (la caja de cada
    superficie, con un cilindro girado, es infinita), aunque la comprobación de las copias decía que sí cabían."""
    from argus.pengeom.duplicate import duplicate
    from argus.pengeom.edit import logical_parent, top_level

    s = _write(tmp_path, "c.geo", _cylinder(30.0))
    cont = logical_parent(s.geo, 1) or top_level(s.geo)
    labels = duplicate(s.doc, s.geo, 1, np.array([5.0, 0.0, 0.0]), 1, cont)
    assert labels


OPEN_MODULE = ("Modulo abierto (solo un plano)\n" + Z64 + "\n"
               "SURFACE (   1)   esfera\nINDICES=( 1, 1, 1, 0,-1)\n" + Z64 + "\n"
               "SURFACE (   2)   z < 3\nINDICES=( 0, 0, 0, 0, 0)\n     AZ=(+1.000000000000000E+00,   0)\n"
               "     A0=(-3.000000000000000E+00,   0)\n" + Z64 + "\n"
               "BODY    (   1)   bola\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z64 + "\n"
               "MODULE  (   2)   abierto\nMATERIAL(   2)\nSURFACE (   2), SIDE POINTER=(-1)\nBODY    (   1)\n" + Z64
               + "\n" + "SURFACE (   3)   caja\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+5.000000000000000E+01,   0)\n"
               "Y-SCALE=(+5.000000000000000E+01,   0)\nZ-SCALE=(+5.000000000000000E+01,   0)\n" + Z64 + "\n"
               "MODULE  (   3)   mundo\nMATERIAL(   0)\nSURFACE (   3), SIDE POINTER=(-1)\nMODULE  (   2)\n" + Z64
               + "\n" + END)


def test_an_open_module_cannot_be_duplicated(tmp_path):
    """Un módulo que sus superficies no cierran se dejaba duplicar: las copias podían quedar encima de otras cosas
    fuera de lo que se comprobaba."""
    from argus.pengeom.duplicate import duplicate
    from argus.pengeom.edit import EditError

    s = _write(tmp_path, "abierto.geo", OPEN_MODULE)
    kb = s.geo.find_body("   2").index
    with pytest.raises(EditError, match="no es un módulo cerrado"):
        duplicate(s.doc, s.geo, kb, np.array([0.0, 20.0, 0.0]), 1, s.geo.find_body("   3").index)


# -- rayos que vienen de fuera ----------------------------------------------------------------------------------------

ROOT_STICKS_OUT = ("Modulo raiz (sin envolvente) con una hija que asoma\n" + Z64 + "\n"
                   "SURFACE (   1)   Esfera R=1.5\nINDICES=( 1, 1, 1, 0,-1)\nX-SCALE=(+1.500000000000000E+00,   0)\n"
                   "Y-SCALE=(+1.500000000000000E+00,   0)\nZ-SCALE=(+1.500000000000000E+00,   0)\n" + Z64 + "\n"
                   "SURFACE (   2)   |x|<1\nINDICES=( 1, 0, 0, 0,-1)\n" + Z64 + "\n"
                   "SURFACE (   3)   |y|<1\nINDICES=( 0, 1, 0, 0,-1)\n" + Z64 + "\n"
                   "SURFACE (   4)   |z|<1\nINDICES=( 0, 0, 1, 0,-1)\n" + Z64 + "\n"
                   "BODY    (   1)   esfera\nMATERIAL(   1)\nSURFACE (   1), SIDE POINTER=(-1)\n" + Z64 + "\n"
                   "MODULE  (   2)   caja raiz\nMATERIAL(   2)\nSURFACE (   2), SIDE POINTER=(-1)\n"
                   "SURFACE (   3), SIDE POINTER=(-1)\nSURFACE (   4), SIDE POINTER=(-1)\nBODY    (   1)\n" + Z64 + "\n"
                   + END)


def test_rays_from_outside_do_not_enter_what_sticks_out_of_the_root_module():
    """Con un módulo raíz (GEOMIN no añade la envolvente) y una hija que asoma de él, STEP de PENGEOM no entra nunca
    desde fuera en lo que asoma (exige estar dentro de la raíz); el trazador de ARGUS sí (relocalizaba con LOCATE,
    que no mira las superficies de la raíz): la vista 3D lo dibujaba. Desde dentro de lo que asoma, LOCATE sigue
    diciendo que es la hija (como PENGEOM)."""
    from argus.render.raytrace import RayTracer

    geo = compile_geometry(parse_geo_text(ROOT_STICKS_OUT, "raiz.geo"))
    assert geo.ok and not geo.enclosure_added
    rt = RayTracer(geo)
    o, d = np.array([5.0, 0.0, 1.3]), np.array([-1.0, 0.0, 0.0])
    assert rt.step_stops(o, d) == []
    assert rt.first_visible(o, d)[1] == 0
    stops = rt.step_stops(np.array([5.0, 0.0, 0.5]), d)  # (por dentro de la raíz, sí: por su cara, x = 1)
    assert stops[0].body == 1 and stops[0].t == pytest.approx(4.0)
    assert rt.trace(np.array([0.5, 0.0, 1.3]), d)[0].body == 1  # (desde dentro de lo que asoma: la hija)


@pytest.mark.gui
def test_the_gpu_does_not_enter_what_sticks_out_of_the_root_module(qapp, need_gpu):  # noqa: F811
    from argus.render.raytrace import RayTracer, default_material_rgba
    from argus.render.scene3d import Camera, body_rgba, gpu_coefficients, gpu_tree_tables, grid
    from argus.ui.gpu_tracer import GpuTracer, OffscreenGpu

    geo = compile_geometry(parse_geo_text(ROOT_STICKS_OUT, "raiz.geo"))
    rt = RayTracer(geo)
    gpu = OffscreenGpu()
    try:
        gpu.current()
        g = gpu.tracer
        cam = Camera(target=np.zeros(3), distance=12.0, yaw=0, pitch=0)
        origin = np.asarray(cam.target, float)
        g.set_tree(gpu_tree_tables(rt.t, rt.nbodys), rt.nbodys)
        g.upload("coef", gpu_coefficients(rt.t, origin))
        g.upload("color", grid(body_rgba(geo), 4, np.uint8))
        mc = np.zeros((1, 128, 4), np.uint8)
        mc[0, :100] = np.rint(default_material_rgba() * 255).astype(np.uint8)
        g.upload("matcolor", mc)
        w, h = 200, 200
        u = GpuTracer.frame_uniforms(cam, w, h, origin, 1e-5 * cam.distance, mode=1)
        img = g.render_image(u, w, h).astype(int)
    finally:
        gpu.close()
    pick = img[..., 0] + 256 * img[..., 1] + 65536 * img[..., 2]
    px, _z = cam.project(np.array([[0.0, 0.0, 1.3], [0.0, 0.0, 0.5]]), w, h)
    out_px, in_px = np.round(px).astype(int)
    assert pick[out_px[1], out_px[0]] == 0  # (lo que asoma: nada)
    assert pick[in_px[1], in_px[0]] in (1, 2)
    o, d = cam.rays(np.array([float(out_px[0])]), np.array([float(out_px[1])]), w, h)
    assert rt.first_visible(np.asarray(o).reshape(-1, 3)[0], np.asarray(d).reshape(-1, 3)[0])[1] == 0
