"""El visor de la cascada, más deprisa y sin cambiar nada de lo que se ve (0.16.0, ADR-020).

* Cada ejecución de la cascada se reconstruye una sola vez al abrirla (antes, dos: ¿está terminada? y sus lluvias) y,
  al volver a abrirla, ninguna.
* ``_hall`` (las lluvias que tienen que ser distintas) con una tabla en vez de mirar cada intervalo: el mismo
  resultado que el de antes (``_hall_loop``), que se sigue usando para comprobarlo.
* ``block_lengths`` con numpy: lo mismo que línea a línea.
* La capa de trayectorias guarda lo que se calcula de ella (límites, datos de cada tramo, qué se ve con cada filtro)
  y a la GPU solo se le mandan los tramos de lo que se ve, en el mismo orden: la misma imagen.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from argus.penhan import cascade as cs
from argus.render.track_layer import ALL_PARTICLES, TrackStyle, heads_at, layer_from_showers
from argus.results import shower as sh
from argus.results.data import Tracks
from argus.results.formats import _block_lengths_fast, block_lengths

sys.path.insert(0, str(Path(__file__).parent))
from test_cascade import NATIVE, _case, _fake  # noqa: E402

CORPUS = Path(__file__).parent / "corpus" / "resultados"
needs_sh = pytest.mark.skipif(os.name == "nt" or shutil.which("awk") is None,
                              reason="el penhmain de mentira es un guion de sh con awk")


# -- _hall ------------------------------------------------------------------------------------------------------------

def _hall_cases(rng, n):
    for it in range(n):
        if it % 2:  # al azar (muchas no caben)
            u = int(rng.integers(1, 12))
            lo = rng.integers(0, 14, u)
            hi = lo + rng.integers(0, 5 if it % 3 else 14, u)
        else:  # siempre caben (una lluvia distinta para cada una) y se estrechan en cadena
            u = int(rng.integers(1, 40 if it % 10 == 0 else 14))
            v = u + int(rng.integers(0, 5))
            vals = rng.permutation(v)[:u]
            lo = np.clip(vals - rng.integers(0, 4, u), 0, v - 1)
            hi = np.clip(vals + rng.integers(0, 4, u), 0, v - 1)
        yield lo.astype(np.int64), hi.astype(np.int64)


def test_hall_with_the_table_is_the_same_as_interval_by_interval():
    rng = np.random.default_rng(20261013)
    changed = failed = 0
    for lo, hi in _hall_cases(rng, 4000):
        l1, h1, l2, h2 = lo.copy(), hi.copy(), lo.copy(), hi.copy()
        r1, r2 = sh._hall_loop(l1, h1), sh._hall(l2, h2)
        assert r1 == r2, (lo.tolist(), hi.tolist())
        if r1:
            assert np.array_equal(l1, l2) and np.array_equal(h1, h2), (lo.tolist(), hi.tolist())
            changed += not (np.array_equal(l1, lo) and np.array_equal(h1, hi))
        else:
            failed += 1
    assert changed > 500 and failed > 50  # (las dos cosas, muchas veces)
    # Un caso a mano: las dos primeras solo caben en {0, 1}: la tercera no puede ser 0 ni 1.
    lo, hi = np.array([0, 0, 0]), np.array([1, 1, 3])
    assert sh._hall(lo, hi) and lo.tolist() == [0, 0, 2] and hi.tolist() == [1, 1, 3]
    assert not sh._hall(np.array([0, 0, 0]), np.array([1, 1, 1]))  # tres en dos lluvias


def test_a_huge_table_uses_the_interval_by_interval_version(monkeypatch):
    calls = []
    monkeypatch.setattr(sh, "HALL_CELLS", 4)
    monkeypatch.setattr(sh, "_hall_loop", lambda lo, hi: calls.append(1) or True)
    assert sh._hall(np.arange(5), np.arange(5) + 1) and calls


# -- block_lengths ----------------------------------------------------------------------------------------------------

def _lines_one_by_one(text):
    lengths, n = [], 0
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            if n:
                lengths.append(n)
                n = 0
            continue
        if not s.startswith("#"):
            n += 1
    if n:
        lengths.append(n)
    return lengths


def test_block_lengths_with_numpy_are_the_same_as_line_by_line():
    rng = np.random.default_rng(7)
    pieces = ["1.0E+00", " ", "  ", "\t", "#", " # c", "\n", "\n", "\r\n", "-2.5", "\n\n", " \n", "\t\n"]
    rare = ["\r", "\v", "\f", "\x1c", "\x1f", "\x85", "\xa0", "é"]
    fast = 0
    for it in range(4000):
        parts = [pieces[i] for i in rng.integers(0, len(pieces), int(rng.integers(0, 50)))]
        if it % 7 == 0:
            parts.insert(int(rng.integers(0, len(parts) + 1)), rare[it % len(rare)])
        text = "".join(parts)
        got = _block_lengths_fast(text)
        if got is not None:
            fast += 1
            assert got == _lines_one_by_one(text), repr(text)
        assert block_lengths(text) == _lines_one_by_one(text), repr(text)
    assert fast > 3000
    assert _block_lengths_fast("1\r2\n") is None and _block_lengths_fast("a\x85b") is None
    assert _block_lengths_fast(" " * 100 + "1\n") is None  # muchos blancos al principio: línea a línea
    for p in sorted(CORPUS.rglob("*.trk")):
        text = p.read_bytes().decode("latin-1")
        assert block_lengths(text) == _lines_one_by_one(text) and _block_lengths_fast(text) is not None


# -- leer la cascada una sola vez -------------------------------------------------------------------------------------

@needs_sh
def test_each_run_is_reconstructed_once_and_not_again(tmp_path, monkeypatch):
    from argus.ui.results.shower_view import load_cascade_data

    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=120, jobs=2, cap=50))
    assert job.wait(60) and job.status.stage == "terminada"
    sh._CACHE.clear()
    calls = []
    real = sh.reconstruct
    monkeypatch.setattr(sh, "reconstruct", lambda *a, **k: calls.append(1) or real(*a, **k))
    data = load_cascade_data(in_path.parent, in_path)
    assert len(calls) == 3  # tres ejecuciones (50, 50 y 20 lluvias): antes, seis
    assert data.done == data.total == 120 and data.showers.n_showers == 120
    again = load_cascade_data(in_path.parent, in_path)
    assert len(calls) == 3 and again.showers.count == data.showers.count  # nada que volver a leer
    # Lo mismo que con el _hall de antes.
    sh._CACHE.clear()
    monkeypatch.setattr(sh, "_hall", sh._hall_loop)
    old = load_cascade_data(in_path.parent, in_path)
    for k in ("points", "energy", "time", "starts", "particle", "shower", "parent", "generation"):
        assert np.array_equal(getattr(old.showers, k), getattr(data.showers, k)), k
    assert np.array_equal(old.layer.vertices, data.layer.vertices)


def test_the_primary_only_counts_when_the_input_cannot_be_read(tmp_path, monkeypatch):
    d = tmp_path / "run"
    d.mkdir()
    for n in ("electron.trk", "photon.trk"):
        shutil.copy(CORPUS / "caja" / n, d / n)
    shutil.copy(CORPUS / "caja" / "caja.in", d / "caja.in")
    sh._CACHE.clear()
    calls = []
    real = sh.reconstruct
    monkeypatch.setattr(sh, "reconstruct", lambda *a, **k: calls.append(a[1] if len(a) > 1 else None) or real(*a, **k))
    a = sh.load_run(d, None, in_name="caja.in")
    b = sh.load_run(d, 1, in_name="caja.in")  # la fuente sale del .in: lo mismo, sin volver a reconstruir
    assert a is b and len(calls) == 1
    (d / "caja.in").unlink()  # sin el .in de la ejecución, la fuente sale de la primaria
    sh.load_run(d, 1, in_name="caja.in")
    sh.load_run(d, 2, in_name="caja.in")  # sin .in que leer, la primaria sí cuenta
    assert calls[1:] == [1, 2]


def test_the_cache_keeps_the_most_recent_within_its_size(tmp_path, monkeypatch):
    sh._CACHE.clear()
    dirs = []
    for i in range(3):
        d = tmp_path / f"run{i}"
        d.mkdir()
        shutil.copy(CORPUS / "caja" / "electron.trk", d / "electron.trk")
        dirs.append(d)
    one = sh._set_bytes(sh.load_run(dirs[0], 1))
    monkeypatch.setattr(sh, "CACHE_BYTES", int(2.5 * one))
    sh.load_run(dirs[1], 1)
    sh.load_run(dirs[0], 1)  # vuelve a ser la más reciente
    sh.load_run(dirs[2], 1)  # no caben tres: se va la más antigua (run1)
    keys = [Path(k).name for k in sh._CACHE]
    assert keys == ["run0", "run2"]
    sh._CACHE.clear()


def test_the_cache_is_safe_with_several_threads(tmp_path, monkeypatch):
    """Revisión de 0.16: el hilo de la cascada que se simula, el del visor y los de la ventana de resultados leen a la
    vez; sin cerrojo, guardar una ejecución (que recorre lo guardado) mientras otro hilo leía otra daba «dictionary
    keys changed during iteration» y paraba la cascada."""
    import threading

    sh._CACHE.clear()
    dirs = []
    for i in range(8):
        d = tmp_path / f"run{i}"
        d.mkdir()
        shutil.copy(CORPUS / "caja" / "electron.trk", d / "electron.trk")
        dirs.append(d)
    monkeypatch.setattr(sh, "CACHE_ITEMS", 3)  # que se expulse sin parar
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    errors = []

    def work(seed):
        rng = np.random.default_rng(seed)
        try:
            for _ in range(60):
                sh.load_run(dirs[int(rng.integers(len(dirs)))], 1)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    try:
        threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        sys.setswitchinterval(old)
        sh._CACHE.clear()
    assert not errors, errors


@needs_sh
def test_a_cascade_bigger_than_the_cache_is_read_once(tmp_path, monkeypatch):
    """Revisión de 0.16: si las ejecuciones no cabían en lo guardado, abrir la cascada las reconstruía tres veces
    (lo terminado, lo que falta por comprobar y las lluvias las recorren seguidas, y se iba siempre la más antigua)."""
    from argus.ui.results.shower_view import load_cascade_data

    in_path, doc = _case(tmp_path)
    exe = _fake(tmp_path)
    job = cs.start_job(cs.CascadeJob(in_path, doc, exe, NATIVE, showers=120, jobs=2, cap=50))
    assert job.wait(60) and job.status.stage == "terminada"
    sh._CACHE.clear()
    load_cascade_data(in_path.parent, in_path)
    one = max(sh._set_bytes(v[2]) for v in sh._CACHE.values())
    sh._CACHE.clear()
    monkeypatch.setattr(sh, "CACHE_BYTES", int(1.5 * one))  # cabe una (y la última)
    calls = []
    real = sh.reconstruct
    monkeypatch.setattr(sh, "reconstruct", lambda *a, **k: calls.append(1) or real(*a, **k))
    load_cascade_data(in_path.parent, in_path)
    assert len(calls) == 3  # una vez cada una (antes, nueve)
    load_cascade_data(in_path.parent, in_path)
    assert len(calls) == 3  # y ninguna al volver a abrirla
    other = tmp_path / "otra"
    other.mkdir()
    shutil.copy(CORPUS / "caja" / "electron.trk", other / "electron.trk")
    sh.load_run(other, 1)  # al guardar otra cosa, se va lo que sobra
    assert len(sh._CACHE) <= 2
    sh._CACHE.clear()


def test_another_run_with_the_same_size_and_date_is_read_again(tmp_path):
    """Revisión de 0.16: una ejecución sustituida por otra del mismo tamaño y con la misma fecha (copiada conservando
    las fechas, de una copia de seguridad) seguía enseñando las lluvias de antes."""
    head = " # X/cm         Y/cm         Z/cm         E/eV\n"

    def row(x, y, z, e):
        return f" {x: .5E} {y: .5E} {z: .5E} {e: .5E}\n"

    d = tmp_path / "run-01"
    d.mkdir()
    a = head + row(0, 0, 0, 1e6) + row(0, 0, 1, 5e5) + row(0, 0, 2, 1e5) + " \n"
    b = head + row(5, 0, 0, 1e6) + row(5, 0, 1, 5e5) + row(5, 0, 2, 1e5) + " \n"
    assert len(a) == len(b)
    p = d / "electron.trk"
    p.write_text(a)
    st = p.stat()
    sh._CACHE.clear()
    first = sh.load_run(d, 1, showers=1)
    p.write_text(b)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    second = sh.load_run(d, 1, showers=1)
    assert second.points[0, 0] == 5.0 and first.points[0, 0] == 0.0
    sh._CACHE.clear()


# -- la capa de trayectorias ------------------------------------------------------------------------------------------

def _layer():
    """Cuatro veces las dos lluvias de test_track_layer (ocho lluvias; electrones y fotones)."""
    from test_track_layer import _showers

    s = _showers()
    return layer_from_showers(sh.merge([s, s, s, s], offsets=[0, 2, 4, 6]))


def _old_visible_mask(st, data):
    v = data.vertices
    kind = v[data.seg_a, 5].astype(np.int64)
    sh_ = v[data.seg_a, 6]
    m = np.isin(kind, list(st.particles)) if st.particles != ALL_PARTICLES else np.ones(len(kind), bool)
    return m & (sh_ >= st.shower_lo - 0.5) & (sh_ < st.shower_hi - 0.5)


def _old_heads(data, style):
    v = data.vertices
    a, b = data.seg_a, data.seg_b
    off = np.maximum(v[a, 6] - style.shower_lo, 0.0) * style.stagger
    ta, tb = v[a, 3] + off, v[b, 3] + off
    on = (ta <= style.time) & (tb > style.time) & _old_visible_mask(style, data)
    idx = np.flatnonzero(on)
    f = ((style.time - ta[idx]) / np.maximum(tb[idx] - ta[idx], 1e-12)).astype(np.float32)[:, None]
    pa, pb = v[a[idx], :3], v[b[idx], :3]
    return (pa + f * (pb - pa)).astype(np.float32), style.colors(data, a[idx]).astype(np.float32)


def _styles(d, rng, n):
    kinds = sorted({int(k) for k in d.vertices[:, 5]})
    for _ in range(n):
        lo = int(rng.integers(0, d.n_showers + 1))
        hi = int(rng.integers(lo, d.n_showers + 2)) if rng.random() < 0.8 else 10 ** 9
        parts = frozenset(k for k in kinds if rng.random() < 0.7) if rng.random() < 0.5 else ALL_PARTICLES
        yield TrackStyle(particles=parts, shower_lo=lo, shower_hi=hi, animate=True,
                         time=float(rng.uniform(0, 1.2 * d.t_max)), stagger=float(rng.choice([0.0, 0.3 * d.t_max])))


def test_the_layer_keeps_what_it_computes_and_gives_the_same():
    d = _layer()
    lo, hi = d.bounds()
    v = d.vertices[:, :3]
    assert np.array_equal(lo, v.min(axis=0).astype(np.float64) + d.origin)
    lo[0] = 1e9  # una copia: lo guardado no cambia
    assert d.bounds()[0][0] != 1e9
    rng = np.random.default_rng(3)
    for st in _styles(d, rng, 300):
        m = st.visible_mask(d)
        assert np.array_equal(m, _old_visible_mask(st, d)) and not m.flags.writeable
        p1, c1 = heads_at(d, st)
        p2, c2 = _old_heads(d, st)
        assert np.array_equal(p1, p2) and np.array_equal(c1, c2)


def test_the_gpu_is_sent_only_what_can_be_seen_in_the_same_order():
    d = _layer()
    rng = np.random.default_rng(5)
    for st in _styles(d, rng, 300):
        ranges = d.draw_ranges(st)
        sent = np.zeros(d.n_segments, dtype=bool)
        last = -1
        for first, count in ranges:
            assert first > last and count > 0  # en orden, sin repetir
            sent[first:first + count] = True
            last = first + count
        vis = _old_visible_mask(st, d)
        assert not np.any(vis & ~sent)  # todo lo que se ve se manda
        kind, shower, _ta, _tb = d.segment_data()
        assert all(r[3] for r in d.kind_runs())  # (aquí las lluvias van en orden: solo se manda lo que se ve)
        assert np.array_equal(sent, vis)
    # Lluvias desordenadas en un tipo de partícula: se manda entero (el shader descarta lo que no se ve).
    tr = [Tracks(1, np.array([[0, 0, 0, 1e6], [0, 0, 1, 5e5]], float), np.array([0, 2])) for _ in range(3)]
    s = sh.reconstruct(tr, 1, n_source=3)
    s.shower[:] = [2, 0, 1]
    d2 = layer_from_showers(s)
    assert d2.kind_runs() == [(0, 3, 1, False)]
    assert d2.draw_ranges(TrackStyle(shower_lo=0, shower_hi=1)) == [(0, 3)]
    assert d2.draw_ranges(TrackStyle(particles=frozenset({2}))) == []
    full = layer_from_showers(reconstruct_three())
    assert full.draw_ranges(TrackStyle()) == [(0, full.n_segments)]


def reconstruct_three():
    tr = [Tracks(1, np.array([[0, 0, 0, 1e6], [0, 0, 1, 5e5], [0, 0, 2, 1e5]], float), np.array([0, 3]))
          for _ in range(3)]
    return sh.reconstruct(tr, 1, n_source=3)


def test_what_goes_to_the_gpu_is_prepared_before_and_released_after():
    d = _layer().prepare()
    assert "_gpu" in d.__dict__ and "_bounds" in d.__dict__ and "_runs" in d.__dict__
    raw, idx = d.gpu_buffers()
    assert len(raw) == d.vertices.size * 4 and np.array_equal(np.frombuffer(idx, np.uint32), d.indices)
    d.release_gpu()
    assert "_gpu" not in d.__dict__ and d.gpu_buffers()[0] == raw


@pytest.mark.gui
def test_without_gpu_what_was_prepared_for_it_is_released(qtbot):
    """Revisión de 0.16: lo preparado para la GPU (32 bytes por punto) solo se soltaba al subirlo; sin GPU se quedaba
    en memoria mientras se veía la cascada."""
    from argus.ui.view3d import View3D

    v = View3D(backend="cpu")
    qtbot.addWidget(v)
    layer = _layer().prepare()
    assert "_gpu" in layer.__dict__
    v.set_track_layer(layer)
    assert "_gpu" not in layer.__dict__


@pytest.mark.gui
def test_on_the_gpu_drawing_only_what_is_seen_gives_the_same_image(qtbot, tmp_path, monkeypatch):
    from argus.ui.gpu_tracer import probe_gl

    ok, why = probe_gl()
    if not ok:
        pytest.skip(f"Sin OpenGL 4.1: {why}")
    from argus.render import track_layer as tl
    from test_track_layer import _view

    v = _view(qtbot, tmp_path, "gpu")
    qtbot.waitUntil(lambda: v.canvas.gpu is not None, timeout=30000)
    d = _layer()
    v.set_track_layer(d)
    v.fit()
    qtbot.waitUntil(v.is_idle, timeout=60000)
    styles = [TrackStyle(), TrackStyle(shower_lo=1, shower_hi=2), TrackStyle(particles=frozenset({2})),
              TrackStyle(shower_lo=0, shower_hi=1, animate=True, time=0.5 * d.t_max, line_width=1.0),
              TrackStyle(color_mode="energy", shower_lo=1, shower_hi=3, trail=0.1, animate=True, time=d.t_max)]
    images = []
    for st in styles:
        v.set_track_style(st)
        images.append(v.grab_image())
    monkeypatch.setattr(tl.TrackLayerData, "draw_ranges", lambda self, style: [(0, self.n_segments)])
    for st, img in zip(styles, images):
        v.set_track_style(st)
        assert v.grab_image() == img
    assert v.canvas.track_error is None
    v.shutdown()
