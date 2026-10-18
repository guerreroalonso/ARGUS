"""Trayectorias en la GPU sobre la imagen trazada de la vista 3D (cascada de partículas, 0.13.0).

Cada tramo es una línea (GL_LINES, con los índices de sus dos vértices: cada punto se sube una sola vez) con su tiempo,
energía, partícula, lluvia y generación; el shader decide qué se ve (partículas, lluvias, instante de la animación) y su
color, así que cambiar cualquiera de esas cosas no vuelve a subir nada a la GPU. Solo se le mandan los tramos de las
partículas y las lluvias que se ven (``TrackLayerData.draw_ranges``, en el mismo orden: la misma imagen; 0.16.0). La
geometría tapa las líneas con la profundidad que el trazado de rayos deja en la segunda textura de su imagen (lo primero
opaco y lo primero visible de cada píxel). Las partículas en vuelo de la animación se dibujan como puntos (se calculan
en la CPU en cada imagen: son pocas).
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QVector2D, QVector3D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)
from shiboken6 import VoidPtr

from ..render import shaders
from ..render.track_layer import PALETTE, TrackLayerData, TrackStyle, energy_lut, heads_at, hex_rgb, particle_rgb
from .gpu_tracer import _ORPHANS, _ContextWatch, _program, _set_uniforms

GL_LINES = 0x0001
GL_POINTS = 0x0000
GL_FLOAT = 0x1406
GL_UNSIGNED_INT = 0x1405
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_ZERO = 0
GL_ONE = 1
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_PROGRAM_POINT_SIZE = 0x8642
GL_DEPTH_TEST = 0x0B71
DEPTH_UNIT = 10
LUT_UNIT = 11
STRIDE = 8 * 4  # x, y, z, t, logE, kind, shower, gen (float32)
MODES = {"particle": 0, "energy": 1, "shower": 2, "generation": 3}


def _wide_program() -> QOpenGLShaderProgram | None:
    """El programa de las líneas con grosor (con geometry shader), o None si la tarjeta no lo acepta."""
    prog = QOpenGLShaderProgram()
    ok = (prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, shaders.TRACK_VERTEX)
          and prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Geometry, shaders.TRACK_GEOMETRY)
          and prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, shaders.TRACK_FRAGMENT_WIDE)
          and prog.link())
    if not ok:
        prog.removeAllShaders()
        return None
    return prog


class GpuTrackLayer:
    """Programas, búferes y textura de color de las trayectorias (con un contexto OpenGL activo)."""

    def __init__(self, ctx):
        # Como GpuTracer: si el contexto se destruye antes que esto, se libera todo entonces (una QOpenGLTexture
        # que sobrevive a su contexto puede romper el programa al liberarla el recolector de Python).
        self._watch = _ContextWatch(self)
        ctx.aboutToBeDestroyed.connect(self._watch.dying)
        self._alive = True
        self.f = ctx.functions()
        self.thin = _program(shaders.TRACK_VERTEX, shaders.TRACK_FRAGMENT)
        self.wide = _wide_program()
        self.prog = self.thin
        self.head_prog = _program(shaders.HEAD_VERTEX, shaders.HEAD_FRAGMENT)
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vbo.create()
        self.vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        self.ibo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self.ibo.create()
        self.ibo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        self.head_vao = QOpenGLVertexArrayObject()
        self.head_vao.create()
        self.head_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.head_vbo.create()
        self.head_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StreamDraw)
        self.lut = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        self.lut.setFormat(QOpenGLTexture.TextureFormat.RGBA8_UNorm)
        self.lut.setSize(256, 1)
        self.lut.setMipLevels(1)
        self.lut.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8)
        self.lut.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        self.lut.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self.lut.setData(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.UInt8,
                         np.ascontiguousarray(energy_lut()).tobytes())
        self.uid: int | None = None
        self.n_vertices = 0
        self._attrs_ready = False
        self._head_attrs_ready = False

    # -- datos ----------------------------------------------------------------------------------------------------
    def upload(self, data: TrackLayerData) -> None:
        """Sube los puntos y los tramos (los índices de sus dos puntos) si no son los que ya tiene."""
        if self.uid == data.uid:
            return
        raw, idx = data.gpu_buffers()
        self.vao.bind()
        self.vbo.bind()
        self.vbo.allocate(raw, len(raw))
        self.ibo.bind()  # (con el VAO activo: se queda en él)
        self.ibo.allocate(idx, len(idx))
        if not self._attrs_ready:
            p = self.prog
            p.bind()
            p.enableAttributeArray(0)
            p.setAttributeBuffer(0, GL_FLOAT, 0, 3, STRIDE)
            p.enableAttributeArray(1)
            p.setAttributeBuffer(1, GL_FLOAT, 12, 4, STRIDE)
            p.enableAttributeArray(2)
            p.setAttributeBuffer(2, GL_FLOAT, 28, 1, STRIDE)
            p.release()
            self._attrs_ready = True
        self.vbo.release()
        self.vao.release()  # (antes que el de los índices: si no, el VAO lo perdería)
        self.uid = data.uid
        self.n_vertices = 2 * data.n_segments
        data.release_gpu()  # ya está en la GPU (si hace falta otra vez, se prepara de nuevo)

    # -- dibujo ---------------------------------------------------------------------------------------------------
    @staticmethod
    def camera_uniforms(frame: dict, width: int, height: int) -> dict:
        """Los de la proyección (los del trazado de la imagen: misma cámara y mismo origen flotante)."""
        return {k: frame[k] for k in ("uCamPos", "uRight", "uUp", "uFwd", "uTan", "uHalf", "uOrtho")} | {
            "uAspect": float(width) / float(max(height, 1)), "uNear": 1.0e-4}

    def draw(self, data: TrackLayerData, style: TrackStyle, frame: dict, origin: np.ndarray, width: int,
             height: int, depth_texture: int | None, eps: float) -> None:
        """Dibuja las trayectorias sobre el *framebuffer* activo (el de la imagen ya copiada)."""
        self.upload(data)
        f = self.f
        cam = self.camera_uniforms(frame, width, height)
        shift = np.asarray(data.origin, dtype=float) - np.asarray(origin, dtype=float)
        f.glViewport(0, 0, width, height)
        f.glDisable(GL_DEPTH_TEST)
        f.glEnable(GL_BLEND)
        # El color se mezcla con la imagen y su alfa no cambia (queda 1): si bajase, la imagen se vería más clara y
        # con otros colores al copiarla (QWidget.grab, exportar) o al componer la ventana.
        f.glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ZERO, GL_ONE)
        if depth_texture is not None:
            f.glActiveTexture(GL_TEXTURE0 + DEPTH_UNIT)
            f.glBindTexture(GL_TEXTURE_2D, depth_texture)
        self.lut.bind(LUT_UNIT)
        wide = self.wide is not None and style.line_width > 1.05
        p = self.wide if wide else self.thin
        p.bind()
        mask = 0
        for k in style.particles:
            mask |= 1 << int(k)
        u = cam | {
            "uShift": QVector3D(*shift), "uColorMode": MODES.get(style.color_mode, 0),
            "uLogLo": float(data.log_e[0]), "uLogHi": float(data.log_e[1]), "uMask": mask,
            "uShowerLo": float(style.shower_lo), "uShowerHi": float(min(style.shower_hi, 2 ** 30)),
            "uStagger": float(style.stagger), "uAlpha": float(style.alpha), "uHasDepth": int(depth_texture is not None),
            "uEps": float(eps), "uAnimate": int(style.animate), "uTime": float(style.time),
            "uTrail": float(style.trail), "uHiddenAlpha": float(style.hidden_alpha),
            "uWidth": float(style.line_width), "uEdgeMax": (0.5 * style.line_width + 1.0) / max(0.5 * style.line_width,
                                                                                                  0.5),
        }
        if wide:
            p.setUniformValue(p.uniformLocation("uViewport"), QVector2D(float(width), float(height)))
        for k, v in u.items():
            if isinstance(v, QVector3D):
                p.setUniformValue(p.uniformLocation(k), v)
        _set_uniforms(p, {k: v for k, v in u.items() if not isinstance(v, QVector3D)})
        p.setUniformValue1i(p.uniformLocation("uDepth"), DEPTH_UNIT)
        p.setUniformValue1i(p.uniformLocation("uLut"), LUT_UNIT)
        for k in range(8):
            p.setUniformValue(p.uniformLocation(f"uKind[{k}]"), QVector3D(*particle_rgb(k)))
        for i, h in enumerate(PALETTE[:12]):
            p.setUniformValue(p.uniformLocation(f"uPal[{i}]"), QVector3D(*hex_rgb(h)))
        self.vao.bind()
        # Solo los tramos de las partículas y las lluvias que se ven (en el mismo orden: la misma imagen).
        for first, count in data.draw_ranges(style):
            # (el desplazamiento en el búfer de índices, como puntero: PySide6 no acepta un int)
            f.glDrawElements(GL_LINES, 2 * count, GL_UNSIGNED_INT, VoidPtr(2 * first * 4))
        self.vao.release()
        p.release()
        if style.animate and style.heads:
            self._draw_heads(data, style, cam, shift, depth_texture, eps)
        self.lut.release(LUT_UNIT)
        if depth_texture is not None:
            f.glActiveTexture(GL_TEXTURE0 + DEPTH_UNIT)
            f.glBindTexture(GL_TEXTURE_2D, 0)
            f.glActiveTexture(GL_TEXTURE0)
        f.glDisable(GL_BLEND)

    def _draw_heads(self, data: TrackLayerData, style: TrackStyle, cam: dict, shift: np.ndarray,
                    depth_texture: int | None, eps: float) -> None:
        pos, col = heads_at(data, style)
        if not len(pos):
            return
        arr = np.ascontiguousarray(np.column_stack([pos, col]).astype(np.float32))
        raw = arr.tobytes()
        f = self.f
        self.head_vao.bind()
        self.head_vbo.bind()
        self.head_vbo.allocate(raw, len(raw))
        hp = self.head_prog
        hp.bind()
        if not self._head_attrs_ready:
            hp.enableAttributeArray(0)
            hp.setAttributeBuffer(0, GL_FLOAT, 0, 3, 24)
            hp.enableAttributeArray(1)
            hp.setAttributeBuffer(1, GL_FLOAT, 12, 3, 24)
            self._head_attrs_ready = True
        for k in ("uCamPos", "uRight", "uUp", "uFwd"):
            hp.setUniformValue(hp.uniformLocation(k), cam[k])
        hp.setUniformValue(hp.uniformLocation("uShift"), QVector3D(*shift))
        _set_uniforms(hp, {"uTan": cam["uTan"], "uHalf": cam["uHalf"], "uOrtho": cam["uOrtho"],
                           "uAspect": cam["uAspect"], "uNear": cam["uNear"], "uSize": float(style.head_size),
                           "uHasDepth": int(depth_texture is not None), "uEps": float(eps)})
        hp.setUniformValue1i(hp.uniformLocation("uDepth"), DEPTH_UNIT)
        f.glEnable(GL_PROGRAM_POINT_SIZE)
        f.glDrawArrays(GL_POINTS, 0, len(arr))
        f.glDisable(GL_PROGRAM_POINT_SIZE)
        hp.release()
        self.head_vbo.release()
        self.head_vao.release()

    def _context_dying(self, ctx) -> None:
        """El contexto va a destruirse sin haber llamado a ``destroy``: se activa (en una superficie fuera de
        pantalla) y se libera todo; después se deja activo el que lo estaba."""
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext

        if not self._alive:
            self.f = None
            return
        prev = QOpenGLContext.currentContext()
        prev_surface = prev.surface() if prev is not None else None
        if prev is not ctx:
            surface = QOffscreenSurface()
            surface.setFormat(ctx.format())
            surface.create()
            if not ctx.makeCurrent(surface):  # pragma: no cover - depende del controlador
                _ORPHANS.append(self.lut)
                self._alive = False
                return
        try:
            self.destroy()
        finally:
            if prev is not ctx:
                ctx.doneCurrent()
                if prev is not None and prev_surface is not None:
                    prev.makeCurrent(prev_surface)

    def destroy(self) -> None:
        """Libera todo; con el contexto activo. Se puede llamar más de una vez."""
        if not self._alive:
            return
        self._alive = False
        for b in (self.vbo, self.ibo, self.head_vbo):
            b.destroy()
        for v in (self.vao, self.head_vao):
            v.destroy()
        self.lut.destroy()
        self.thin.removeAllShaders()
        if self.wide is not None:
            self.wide.removeAllShaders()
        self.head_prog.removeAllShaders()
        self.uid = None
        self.f = None  # las funciones de OpenGL mueren con el contexto (ver GpuTracer.destroy)


__all__ = ["GpuTrackLayer"]
