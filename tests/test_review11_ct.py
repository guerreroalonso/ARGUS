"""Undécima revisión (0.17.3): los vóxeles del TAC.

La vista 3D con el corte en un plano de la geometría, la lectura del .ct como gfortran, el vóxel de CTLOCATE en un
plano entre vóxeles y el diálogo «Importar TAC» (memoria y micro-TAC). Cada prueba falla con el código de 0.17.2."""

from __future__ import annotations

import numpy as np
import pytest

from argus.pengeom import ct as C
from argus.pengeom.compiler import compile_geometry
from argus.pengeom.parser import load_geometry, parse_geo_text
from argus.pengeom.writer import write_geo_text
from test_track_layer import need_gpu  # noqa: F401

Z64 = "0" * 64
END = "END      " + "0" * 55 + "\n"


def _box_surfaces(lo, hi, start=1):
    out = []
    for k, (ax, val) in enumerate([("X", lo[0]), ("X", hi[0]), ("Y", lo[1]), ("Y", hi[1]), ("Z", lo[2]),
                                   ("Z", hi[2])], start=start):
        out.append(f"SURFACE ({k:4d})\nINDICES=( 0, 0, 0, 0, 0)\n     A{ax}=(+1.000000000000000E+00,   0)\n"
                   f"     A0=({-val:+.15E},   0)\n{Z64}\n")
    return "".join(out)


def _sides(start=1):
    return "".join(f"SURFACE ({k:4d}), SIDE POINTER=({'+1' if (k - start) % 2 == 0 else '-1'})\n"
                   for k in range(start, start + 6))


def _ct_geometry(tmp_path, mat, size, shift, in_module: bool, density=None):
    """Un CT (``mat``: (NZ, NY, NX)) colocado en ``shift``, dentro de un módulo de agua o solo."""
    from argus.ct.importer import add_ctbox

    size = np.asarray(size, dtype=float)
    shift = np.asarray(shift, dtype=float)
    den = np.where(mat > 1, 1.0, 0.0012) if density is None else density
    C.write_ct_file(tmp_path / "tac.ct", "t", tuple(size), mat, den)
    if in_module:
        text = ("Caja\n" + Z64 + "\n" + _box_surfaces(shift - 2.0, shift + size + 2.0)
                + "MODULE  (   1)   caja\nMATERIAL(   1)\n" + _sides() + Z64 + "\n" + END)
    else:
        text = "Geometria con TAC\n" + Z64 + "\n" + END
    doc = parse_geo_text(text, str(tmp_path / "g.geo"))
    geo = compile_geometry(doc, base_dir=tmp_path)
    container = geo.find_body("   1").index if in_module else None
    add_ctbox(doc, geo, "tac.ct", np.eye(3), shift, container, "TAC", "")
    (tmp_path / "g.geo").write_text(write_geo_text(doc))
    geo = compile_geometry(load_geometry(tmp_path / "g.geo"), base_dir=tmp_path)
    assert geo.ok, [str(d) for d in geo.diagnostics]
    return geo


def _keys_and_colors(rt, cam, clip, mat_rgba, w=160, h=120):
    from argus.render.scene3d import body_rgba, key_light

    py, px = np.mgrid[0:h, 0:w]
    o, d = cam.rays(px.ravel(), py.ravel(), w, h)
    res = rt.render_color(o, d, body_rgba(rt.geo).astype(float) / 255.0, 0, list(clip), key_light(cam),
                          mat_rgba=mat_rgba)
    return res[:, 5].reshape(h, w), res[:, :3]


def _edges(key):
    return int((key[:, :-1] != key[:, 1:]).sum() + (key[:-1, :] != key[1:, :]).sum())


# -- la vista 3D con el corte en un plano de la geometría ---------------------------------------------------------

def test_a_clip_on_the_face_of_the_ct_box_is_not_speckled(tmp_path):
    """El corte justo en la cara de una hija del módulo (aquí la del CTBOX; también la de cualquier cuerpo, en planos
    redondos que da el deslizador): cada píxel decidía por redondeo entre «cara del corte» y «superficie», con miles
    de fronteras falsas (6243 frente a 1427 con el corte 1e-7 cm dentro)."""
    from argus.render.raytrace import RayTracer, default_material_rgba
    from argus.render.scene3d import Camera, clip_planes

    rng = np.random.default_rng(0)
    mat = rng.integers(2, 4, (8, 12, 10)).astype(np.int32)
    geo = _ct_geometry(tmp_path, mat, (5.0, 6.0, 4.0), (-2.5, -3.0, -2.0), in_module=True)
    rt = RayTracer(geo)
    cam = Camera(target=np.zeros(3), distance=20.0, yaw=20, pitch=15)
    mr = default_material_rgba()
    on, _ = _keys_and_colors(rt, cam, clip_planes("x", np.array([2.5, 0.0, 0.0])), mr)
    inside, _ = _keys_and_colors(rt, cam, clip_planes("x", np.array([2.5 - 1e-7, 0.0, 0.0])), mr)
    assert _edges(on) <= 1.05 * _edges(inside) + 10


def test_a_clip_on_a_plane_between_voxels_hides_the_voxel_in_front(tmp_path):
    """Corte en un plano entre vóxeles, visto desde el lado que queda, con el aire oculto: unos píxeles enseñaban el
    vóxel de delante del plano (oculto por el corte) y otros no."""
    from argus.render.raytrace import RayTracer, default_material_rgba
    from argus.render.scene3d import Camera, clip_planes

    n, vox = 40, 0.25
    c = (np.arange(n) + 0.5) * vox - n * vox / 2
    zc, yc, xc = np.meshgrid(c, c, c, indexing="ij")
    mat = np.where(xc ** 2 + yc ** 2 < (3.0 + 0.8 * (zc > 0)) ** 2, 2, 1).astype(np.int32)
    geo = _ct_geometry(tmp_path, mat, (n * vox,) * 3, -np.full(3, n * vox / 2), in_module=False)
    rt = RayTracer(geo)
    mr = default_material_rgba()
    mr[1, 3] = 0.0  # el aire (material 1) oculto
    cam = Camera(target=np.zeros(3), distance=30.0, yaw=30, pitch=-60)
    _, at = _keys_and_colors(rt, cam, clip_planes("z", np.array([0.0, 0.0, 0.0])), mr)
    _, below = _keys_and_colors(rt, cam, clip_planes("z", np.array([0.0, 0.0, -1e-7])), mr)
    assert int((np.abs(at - below).max(axis=1) > 0.02).sum()) == 0


@pytest.mark.gui
def test_the_gpu_does_the_same_with_the_clip_on_the_ct_box(qapp, tmp_path, need_gpu):  # noqa: F811
    from argus.render.raytrace import RayTracer, default_material_rgba
    from argus.render.scene3d import Camera, body_rgba, clip_planes, gpu_coefficients, gpu_tree_tables, grid
    from argus.ui.gpu_tracer import GpuTracer, OffscreenGpu

    rng = np.random.default_rng(0)
    mat = rng.integers(2, 4, (8, 12, 10)).astype(np.int32)
    geo = _ct_geometry(tmp_path, mat, (5.0, 6.0, 4.0), (-2.5, -3.0, -2.0), in_module=True)
    rt = RayTracer(geo)
    gpu = OffscreenGpu()
    try:
        gpu.current()
        g = gpu.tracer
        cam = Camera(target=np.zeros(3), distance=20.0, yaw=20, pitch=15)
        origin = np.asarray(cam.target, float)
        g.set_tree(gpu_tree_tables(rt.t, rt.nbodys), rt.nbodys)
        g.upload("coef", gpu_coefficients(rt.t, origin))
        g.upload("color", grid(body_rgba(geo), 4, np.uint8))
        mc = np.zeros((1, 128, 4), np.uint8)
        mc[0, :100] = np.rint(default_material_rgba() * 255).astype(np.uint8)
        g.upload("matcolor", mc)
        g.set_ct(geo, origin)
        edges = {}
        for x in (2.5, 2.49):
            u = GpuTracer.frame_uniforms(cam, 160, 120, origin, 1e-5 * cam.distance,
                                         clip=clip_planes("x", np.array([x, 0.0, 0.0])), mode=0)
            edges[x] = _edges(g.render_image(u, 160, 120)[..., 3].astype(int))
    finally:
        gpu.close()
    assert edges[2.5] <= 1.05 * edges[2.49] + 10



# -- leer el .ct como gfortran ------------------------------------------------------------------------------------

def _ct_text(lines: list[str], shape=(2, 1, 1), size=(1.0, 1.0, 1.0)) -> bytes:
    head = "t\n" + " ".join(f"{v:.4E}" for v in size) + "\n" + " ".join(str(n) for n in shape) + "\n"
    return (head + "".join(lines)).encode("latin-1")


@pytest.mark.parametrize("body", [" 1 1.0000E+00\n\x0c 1 1.0000E+00\n", " 1 1.0000E+00\n 1 1.0000E+00\x0b\n"])
def test_a_ct_with_form_feeds_or_vertical_tabs_is_not_read(body):
    """NumPy y bytes.split toman \\f y \\v por blancos; gfortran no («Bad integer/real»): PENGEOM no leería el .ct."""
    with pytest.raises(C.CtFormatError):
        C.read_ct_bytes(_ct_text([body]))


def test_a_material_out_of_integer4_is_a_format_error(tmp_path):
    """«Python integer 2147483648 out of bounds for int32», sin fichero ni línea, al abrir la geometría."""
    with pytest.raises(C.CtFormatError, match="4 bytes"):
        C.read_ct_bytes(_ct_text([" 1 1.0\n", " 2147483648 1.0\n"]))
    (tmp_path / "tac.ct").write_bytes(_ct_text([" 1 1.0\n", " 2147483648 1.0\n"]))
    text = ("Geometria con TAC\n" + Z64 + "\n" + "CTBOX   (   1)   TAC\nCTFNAME=(tac.ct      )\n" + Z64 + "\n" + END)
    (tmp_path / "g.geo").write_text(text)
    geo = compile_geometry(load_geometry(tmp_path / "g.geo"), base_dir=tmp_path)
    assert not geo.ok
    assert any("tac.ct" in d.message and "4 bytes" in d.message for d in geo.diagnostics.errors)


def _big_ct(n=(60, 60, 60)):
    rng = np.random.default_rng(1)
    mat = rng.integers(1, 5, n[::-1]).astype(np.int32)
    den = np.round(rng.uniform(0.001, 2, n[::-1]), 4)
    raw = C.format_ct("t", (6.0, 6.0, 6.0), mat, den)
    k = 0
    for _ in range(3):
        k = raw.index(b"\n", k) + 1
    return raw[:k], raw[k:], mat, den


@pytest.mark.parametrize("variant", ["coma", "linea_final", "tabuladores"])
def test_valid_ct_layouts_are_read_quickly(variant, monkeypatch):
    """Una coma entre MC y DC («1,1.0000E+00», válido para gfortran) o una línea de más al final (que PENGEOM no lee)
    iban por la lectura lenta, un READ por vóxel en Python: 10-16 s por millón de vóxeles, en el hilo de la ventana."""
    head, body, mat, den = _big_ct()
    if variant == "coma":
        body = b"".join(ln[:2].strip() + b"," + ln[2:].strip() + b"\n" for ln in body.split(b"\n")[:-1])
    elif variant == "linea_final":
        body = body + b"fin del fichero\n"
    else:
        body = body.replace(b" ", b"\t")

    def slow(*_a, **_k):
        raise AssertionError("lectura lenta")

    monkeypatch.setattr(C, "_slow_body", slow)
    ct = C.read_ct_bytes(head + body)
    assert np.array_equal(ct.material, mat) and np.allclose(ct.density, den)


@pytest.mark.parametrize("body, why", [
    (" 1,,1.0\n 1 1.0\n", "nulo"),
    (",1 1.0\n 1 1.0\n", "nulo"),
    (" 1 1.0 7\n 1\n 1.0\n", "otro número de valores"),
])
def test_the_fast_reader_still_leaves_odd_files_to_the_fortran_one(body, why):
    """Lo que no es un vóxel por línea (valores nulos, líneas con más o menos valores) sigue leyéndose como gfortran."""
    raw = _ct_text([body])
    if why == "nulo":
        with pytest.raises(C.CtFormatError, match="nulo"):
            C.read_ct_bytes(raw)
    else:  # «1 1.0 7»: el 7 se descarta; el segundo vóxel ocupa dos líneas
        ct = C.read_ct_bytes(raw)
        assert ct.material.ravel().tolist() == [1, 1]


@pytest.mark.parametrize("name", ["\ttac.ct", "tac.ct\t", "\xa0tac.ct", "tac.ct\x0b"])
def test_a_ct_name_with_a_tab_or_a_hard_space_is_not_found(tmp_path, name):
    """GEOMIN solo quita los espacios del principio (y OPEN los del final): con un tabulador o un espacio duro el
    fichero no existe (IRETRN 2106). ARGUS lo quitaba con str.strip y enseñaba el CT."""
    mat = np.ones((2, 2, 2), dtype=np.int32)
    C.write_ct_file(tmp_path / "tac.ct", "t", (1.0, 1.0, 1.0), mat, np.ones(mat.shape))
    field = f"{name:<12}"[:12]
    text = ("Geometria con TAC\n" + Z64 + "\n" + f"CTBOX   (   1)   TAC\nCTFNAME=({field})\n" + Z64 + "\n" + END)
    (tmp_path / "g.geo").write_bytes(text.encode("latin-1"))
    geo = compile_geometry(load_geometry(tmp_path / "g.geo"), base_dir=tmp_path)
    assert not geo.ok
    err = next(d for d in geo.diagnostics.errors if d.code == 2106)
    assert "no se ven" in err.message and "\\" in err.message  # (el carácter, escrito: «\t», «\xa0»…)


def test_a_ct_name_with_spaces_around_is_still_found(tmp_path):
    mat = np.ones((2, 2, 2), dtype=np.int32)
    C.write_ct_file(tmp_path / "tac.ct", "t", (1.0, 1.0, 1.0), mat, np.ones(mat.shape))
    text = ("Geometria con TAC\n" + Z64 + "\n" + "CTBOX   (   1)   TAC\nCTFNAME=(  tac.ct    )\n" + Z64 + "\n" + END)
    (tmp_path / "g.geo").write_text(text)
    assert compile_geometry(load_geometry(tmp_path / "g.geo"), base_dir=tmp_path).ok


# -- el vóxel de CTLOCATE -----------------------------------------------------------------------------------------

def test_a_point_on_a_plane_between_voxels_with_a_parallel_direction(tmp_path):
    """0,3 cm con vóxeles de 0,1 cm: PENGEOM hace X·RDX = 3 (y, con U = 0, el vóxel de debajo del plano); ARGUS hacía
    0,3/0,1 = 2,999… y daba el de al lado. Se veía en los cortes 2D (justo en las fronteras) y en la barra de estado."""
    mat = (np.arange(10)[None, None, :] % 2 + 1).repeat(2, axis=1).repeat(2, axis=0).astype(np.int32)
    geo = _ct_geometry(tmp_path, mat, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), in_module=False)
    fr = C.ct_frame(geo)
    pts = np.array([[0.3, 0.25, 0.25], [0.3, 0.75, 0.6], [0.7, 0.5, 0.5]])
    dirs = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    ix, _iy, _iz, inside = C.voxel_indices(fr, pts, dirs)
    assert inside.all()
    assert ix.tolist() == [2, 2, 6]  # (desde 0: el de debajo de cada plano, como CTLOCATE con U = 0)


def test_ctlocate_on_voxel_planes_against_pengeom(tmp_path, need_probe):
    """Puntos en los planos entre vóxeles de un CT girado y desplazado, con direcciones paralelas y oblicuas."""
    from argus.ct.importer import add_ctbox
    from argus.oracle.probe import run_probe
    from argus.pengeom.quadric import rotation_matrix

    rng = np.random.default_rng(7)
    shape = (7, 9, 11)  # (NZ, NY, NX)
    mat = rng.integers(1, 6, shape).astype(np.int32)
    den = rng.uniform(0.5, 2.0, shape).round(4)
    size = np.array([1.1, 0.9, 0.7])
    C.write_ct_file(tmp_path / "tac.ct", "t", tuple(size), mat, den)
    doc = parse_geo_text("Geometria con TAC\n" + Z64 + "\n" + END, str(tmp_path / "g.geo"))
    geo = compile_geometry(doc, base_dir=tmp_path)
    rot = np.array(rotation_matrix(0.3, 0.7, -0.2))
    add_ctbox(doc, geo, "tac.ct", rot, np.array([0.2, -0.1, 0.3]), None, "TAC", "")
    (tmp_path / "g.geo").write_text(write_geo_text(doc))
    geo = compile_geometry(load_geometry(tmp_path / "g.geo"), base_dir=tmp_path)
    fr = C.ct_frame(geo)
    n = 4000
    loc = rng.uniform(0.0, 1.0, (n, 3)) * size
    nx = np.array(shape[::-1])
    axis = rng.integers(0, 3, n)
    k = rng.integers(1, nx[axis])
    loc[np.arange(n), axis] = k * size[axis] / nx[axis]  # en un plano entre vóxeles
    dl = rng.normal(size=(n, 3))
    par = rng.random(n) < 0.5
    dl[par, axis[par]] = 0.0  # paralela al plano
    dl /= np.linalg.norm(dl, axis=1)[:, None]
    pts, dirs = fr.to_lab(loc), dl @ fr.rotation.T
    res = run_probe(tmp_path / "g.geo", ct_points=np.hstack([pts, dirs]))
    assert res.status.ok
    ok = res.ct_voxels["ibody"] == geo.ct_body
    ix, iy, iz, _ = C.voxel_indices(fr, pts, dirs)
    got = np.stack([ix, iy, iz], axis=1)[ok] + 1
    want = np.stack([res.ct_voxels["ix"], res.ct_voxels["iy"], res.ct_voxels["iz"]], axis=1)[ok]
    assert ok.sum() > 0.9 * n
    assert np.array_equal(got, want)
