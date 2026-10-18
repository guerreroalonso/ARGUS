"""Trazado de rayos en la GPU con OpenGL 4.1 core (Qt).

``GpuTracer`` guarda los programas GLSL y las texturas de una geometría, y
dibuja en el *framebuffer* que esté activo. Necesita un contexto OpenGL
actual; lo usan la vista 3D (``view3d.GLCanvas``) y las pruebas (con una
superficie fuera de pantalla).
"""

from __future__ import annotations

import weakref
from functools import lru_cache

import numpy as np
import shiboken6
from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import (
    QGuiApplication,
    QOffscreenSurface,
    QOpenGLContext,
    QSurfaceFormat,
    QVector2D,
    QVector3D,
    QVector4D,
)
from PySide6.QtOpenGL import (
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLPixelTransferOptions,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)

from ..render import shaders
from ..render.scene3d import BACKGROUND, Camera, key_light

GL_TRIANGLES = 0x0004
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_SCISSOR_TEST = 0x0C11
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_COLOR_BUFFER_BIT = 0x4000
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_LINEAR = 0x2601
GL_NEAREST = 0x2600
GL_RENDERER = 0x1F01
GL_VERSION = 0x1F02
GL_VENDOR = 0x1F00
GL_MAX_3D_TEXTURE_SIZE = 0x8073

UNITS = {"coef": 0, "body": 1, "cons": 2, "eval": 3, "dght": 4, "color": 5, "matcolor": 7}
SAMPLERS = {"coef": "uCoef", "body": "uBody", "cons": "uCons", "eval": "uEval", "dght": "uDght", "color": "uColor",
            "matcolor": "uMatColor"}
DISPLAY_UNIT = 6
CT_UNIT = 8  # textura 3D con el material de cada vóxel del CT
DEPTH_COPY_UNIT = 9  # profundidad de la vista previa, al copiarla a la imagen completa
GL_RGBA32F = 0x8814
GL_COLOR_ATTACHMENT0 = 0x8CE0

# Texturas que no se pudieron borrar al destruirse su contexto: se guardan para que no se destruyan nunca (ver
# GpuTracer._context_dying). Solo pasaría si el controlador no deja activar el contexto en ese momento.
_ORPHANS: list[QOpenGLTexture] = []


class GpuError(RuntimeError):
    """La GPU no sirve para la vista 3D (sin OpenGL 4.1 o el *shader* no compila)."""

# Plataformas de Qt que no dibujan OpenGL en las ventanas (QOpenGLWidget: «not supported on this platform»).
NO_GL_WINDOWS = frozenset({"offscreen", "minimal"})


def surface_format() -> QSurfaceFormat:
    """Formato OpenGL que pide ARGUS: 4.1 core (el máximo de macOS; Intel UHD da 4.6)."""
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(8)
    return fmt


@lru_cache(maxsize=1)
def probe_gl() -> tuple[bool, str]:
    """¿Hay OpenGL 4.1 core? Devuelve (sí/no, descripción de la tarjeta o motivo).

    Se crea un contexto fuera de pantalla y se compila el *shader* de
    trazado; así se detectan también los controladores que no lo aceptan.
    Con las plataformas de Qt sin ventanas OpenGL («offscreen», «minimal») no:
    pueden crear contextos (con un servidor X, por GLX), pero la vista 3D de la
    GPU (``QOpenGLWidget``) no dibujaría nunca.
    """
    plat = QGuiApplication.platformName()
    if plat in NO_GL_WINDOWS:
        return False, f"la plataforma «{plat}» de Qt no dibuja OpenGL en las ventanas"
    ctx = QOpenGLContext()
    ctx.setFormat(surface_format())
    if not ctx.create():
        return False, "no se puede crear un contexto OpenGL"
    v = ctx.format().version()
    if v < (4, 1):
        return False, f"OpenGL {v[0]}.{v[1]} (hace falta 4.1)"
    surf = QOffscreenSurface()
    surf.setFormat(ctx.format())
    surf.create()
    if not ctx.makeCurrent(surf):
        return False, "no se puede activar el contexto OpenGL"
    try:
        f = ctx.functions()
        renderer = f.glGetString(GL_RENDERER) or "?"
        version = f.glGetString(GL_VERSION) or "?"
        try:
            tr = GpuTracer(ctx)
            tr.destroy()
        except GpuError as exc:
            return False, f"{renderer}: {exc}"
        return True, f"{renderer} (OpenGL {version.split()[0]})"
    finally:
        ctx.doneCurrent()


def _program(vertex: str, fragment: str) -> QOpenGLShaderProgram:
    prog = QOpenGLShaderProgram()
    if not prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
        raise GpuError("el vertex shader no compila: " + prog.log())
    if not prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
        raise GpuError("el fragment shader no compila: " + prog.log())
    if not prog.link():
        raise GpuError("no se pueden enlazar los shaders: " + prog.log())
    return prog


_FORMATS = {
    # nombre: (formato interno, formato de los datos, tipo, canales, dtype)
    "f4": (QOpenGLTexture.TextureFormat.RGBA32F, QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.Float32),
    "i4": (QOpenGLTexture.TextureFormat.RGBA32I, QOpenGLTexture.PixelFormat.RGBA_Integer,
           QOpenGLTexture.PixelType.Int32),
    "i1": (QOpenGLTexture.TextureFormat.R32I, QOpenGLTexture.PixelFormat.Red_Integer, QOpenGLTexture.PixelType.Int32),
    "u4": (QOpenGLTexture.TextureFormat.RGBA8_UNorm, QOpenGLTexture.PixelFormat.RGBA,
           QOpenGLTexture.PixelType.UInt8),
}
_KINDS = {"coef": "f4", "body": "i4", "cons": "i1", "eval": "i1", "dght": "i1", "color": "u4", "matcolor": "u4"}


class _ContextWatch(QObject):
    """Recibe ``aboutToBeDestroyed`` del contexto y avisa al GpuTracer (con una referencia débil: no lo
    mantiene vivo).

    Cuando se borra un widget OpenGL, su envoltorio de Python y el del contexto ya no son válidos al emitirse
    la señal; ``sender()`` da uno nuevo del contexto (que aún existe), que se invalida al terminar.
    """

    def __init__(self, tracer: GpuTracer):
        super().__init__()
        self._tracer = weakref.ref(tracer)

    @Slot()
    def dying(self) -> None:
        tracer = self._tracer()
        ctx = self.sender()
        if tracer is None or not isinstance(ctx, QOpenGLContext):
            return
        try:
            tracer._context_dying(ctx)
        finally:
            shiboken6.invalidate(ctx)  # el contexto deja de existir enseguida


class GpuTracer:
    """Programas y texturas del trazado de rayos (con un contexto actual)."""

    def __init__(self, ctx: QOpenGLContext):
        # No se guarda el contexto: su vida la lleva quien lo creó (el widget o OffscreenGpu). Si se destruye
        # antes que este objeto (se borra la ventana sin cerrarla, por ejemplo), _ContextWatch avisa y las
        # texturas se liberan entonces; ver _context_dying.
        self._watch = _ContextWatch(self)
        ctx.aboutToBeDestroyed.connect(self._watch.dying)
        self.f = ctx.functions()
        self.trace = _program(shaders.VERTEX, shaders.TRACE)
        self.show = _program(shaders.VERTEX, shaders.DISPLAY)
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.textures: dict[str, QOpenGLTexture] = {}
        self._shapes: dict[str, tuple] = {}
        self.nbodys = 0
        self.max_steps = 4096
        self.ct_tex: QOpenGLTexture | None = None
        self.ct_factor = (1, 1, 1)
        self._ct_key = None
        self.ct_uniforms: dict = {"uCtBody": 0}
        self.upload_ct_materials(np.zeros((1, 1, 1), dtype=np.uint8))
        self._ct_key = None
        mc = np.zeros((1, 128, 4), dtype=np.uint8)
        self.upload("matcolor", mc)

    # -- datos ---------------------------------------------------------------
    def upload(self, name: str, arr: np.ndarray) -> None:
        """Sube (o actualiza) una textura de datos; ``arr``: (alto, ancho, canales)."""
        fmt, pf, pt = _FORMATS[_KINDS[name]]
        dtype = {"f4": np.float32, "i4": np.int32, "i1": np.int32, "u4": np.uint8}[_KINDS[name]]
        data = np.ascontiguousarray(arr, dtype=dtype)
        shape = data.shape
        tex = self.textures.get(name)
        if tex is None or self._shapes.get(name) != shape:
            if tex is not None:
                tex.destroy()
            tex = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
            tex.setFormat(fmt)
            tex.setSize(shape[1], shape[0])
            tex.setMipLevels(1)
            tex.allocateStorage(pf, pt)
            tex.setMinMagFilters(QOpenGLTexture.Filter.Nearest, QOpenGLTexture.Filter.Nearest)
            tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
            self.textures[name] = tex
            self._shapes[name] = shape
        tex.setData(pf, pt, data.tobytes())

    def _max_3d(self) -> int:
        try:
            v = int(self.f.glGetIntegerv(GL_MAX_3D_TEXTURE_SIZE))
        except Exception:  # pragma: no cover - depende del enlace de PySide
            v = 0
        return v if v >= 64 else 2048  # el mínimo de OpenGL 4.1 es 2048

    def upload_ct_materials(self, mat: np.ndarray) -> tuple[int, int, int]:
        """Textura 3D (R8UI) con el material de cada vóxel; ``mat``: (NZ, NY, NX) uint8.

        Si el CT no cabe en una textura 3D de la tarjeta (más de GL_MAX_3D_TEXTURE_SIZE vóxeles por eje o sin
        memoria), se sube con menos resolución (un vóxel de cada k): la vista 3D lo muestra aproximado (el
        corte 2D, la selección y la información del cursor usan el CT entero). Devuelve (kx, ky, kz).
        """
        full = np.ascontiguousarray(mat, dtype=np.uint8)
        limit = self._max_3d()
        k = [max(1, -(-n // limit)) for n in (full.shape[2], full.shape[1], full.shape[0])]
        for _attempt in range(4):
            data = np.ascontiguousarray(full[::k[2], ::k[1], ::k[0]])
            nz, ny, nx = data.shape
            if self.ct_tex is not None:
                self.ct_tex.destroy()
                self.ct_tex = None
            for _ in range(16):  # errores anteriores (acotado: con el contexto perdido no se vacía nunca)
                if not self.f.glGetError():
                    break
            tex = QOpenGLTexture(QOpenGLTexture.Target.Target3D)
            tex.setFormat(QOpenGLTexture.TextureFormat.R8U)
            tex.setSize(nx, ny, nz)
            tex.setMipLevels(1)
            tex.allocateStorage(QOpenGLTexture.PixelFormat.Red_Integer, QOpenGLTexture.PixelType.UInt8)
            tex.setMinMagFilters(QOpenGLTexture.Filter.Nearest, QOpenGLTexture.Filter.Nearest)
            tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
            opts = QOpenGLPixelTransferOptions()
            opts.setAlignment(1)  # filas de NX bytes, sin relleno
            tex.setData(QOpenGLTexture.PixelFormat.Red_Integer, QOpenGLTexture.PixelType.UInt8, data.tobytes(),
                        opts)
            self.ct_tex = tex
            if tex.isStorageAllocated() and not self.f.glGetError():
                break
            k = [2 * v for v in k]  # sin memoria en la tarjeta: la mitad de resolución
        self.ct_factor = (k[0], k[1], k[2])
        return self.ct_factor

    def set_ct(self, geo, origin) -> None:
        """Vóxeles del CT de ``geo`` (se suben una vez por CT) y su colocación respecto al origen flotante."""
        ct = getattr(geo, "ct", None)
        if ct is None or getattr(geo, "ct_body", None) is None:
            self.ct_uniforms = {"uCtBody": 0}
            if self._ct_key is not None:  # una geometría sin CT: se libera la textura (y el CT anterior)
                self.upload_ct_materials(np.zeros((1, 1, 1), dtype=np.uint8))
                self._ct_key = None
            return
        from ..pengeom.ct import ct_frame

        if ct is not self._ct_key:  # el objeto (no su id, que podría repetirse tras liberarse otro CT)
            self.upload_ct_materials(ct.material_u8())
            self._ct_key = ct
        fr = ct_frame(geo)
        o = fr.shift - np.asarray(origin, dtype=float)
        r = fr.rotation
        kx, ky, kz = getattr(self, "ct_factor", (1, 1, 1))
        nx, ny, nz = (-(-ct.shape[0] // kx), -(-ct.shape[1] // ky), -(-ct.shape[2] // kz))
        vx, vy, vz = (fr.voxel[0] * kx, fr.voxel[1] * ky, fr.voxel[2] * kz)
        self.ct_uniforms = {
            "uCtBody": int(geo.ct_body), "uCtO": QVector3D(*o), "uCtAx": QVector3D(*r[:, 0]),
            "uCtAy": QVector3D(*r[:, 1]), "uCtAz": QVector3D(*r[:, 2]), "uCtVox": QVector3D(vx, vy, vz),
            "uCtN": (int(nx), int(ny), int(nz)),
        }

    def set_tree(self, tables: dict[str, np.ndarray], nbodys: int) -> None:
        for name in ("body", "cons", "eval", "dght"):
            self.upload(name, tables[name])
        self.nbodys = nbodys

    def ready(self) -> bool:
        return all(n in self.textures for n in UNITS)

    # -- dibujo ----------------------------------------------------------------
    @staticmethod
    def frame_uniforms(camera: Camera, width: int, height: int, origin, tol: float, clip=(), selected: int = 0,
                       mode: int = 0, background=BACKGROUND) -> dict:
        """Uniformes de una imagen (width x height píxeles) vista con ``camera``."""
        right, up, fwd = camera.basis()
        key = key_light(camera)
        if camera.ortho:
            # t = 0 en el plano del objetivo (máxima precisión cerca de él); los rayos salen desde delante.
            base = np.asarray(camera.target, dtype=float) - np.asarray(origin, dtype=float)
            t0 = -max(camera.distance, camera.ortho_back)
        else:
            base = camera.position - np.asarray(origin, dtype=float)
            t0 = 0.0
        o = np.asarray(origin, dtype=float)
        planes = [(nx, ny, nz, d - (nx * o[0] + ny * o[1] + nz * o[2])) for nx, ny, nz, d in list(clip)[:3]]
        u = {
            "uFull": QVector2D(width, height), "uCamPos": QVector3D(*base), "uRight": QVector3D(*right),
            "uUp": QVector3D(*up), "uFwd": QVector3D(*fwd), "uKey": QVector3D(*key), "uTan": camera.tan_half(),
            "uOrtho": int(camera.ortho), "uHalf": camera.half_height(), "uTol": float(tol), "uT0": float(t0),
            "uClipN": len(planes), "uSelected": int(selected or 0), "uMode": int(mode),
            "uBg0": QVector3D(*background[0]), "uBg1": QVector3D(*background[1]),
        }
        for i in range(3):
            u[f"uClip{i}"] = QVector4D(*planes[i]) if i < len(planes) else QVector4D(0, 0, 0, 0)
        return u

    def draw(self, uniforms: dict, scale: float, width: int, height: int, scissor=None) -> None:
        """Traza sobre el *framebuffer* activo (``width`` x ``height``; ``scissor``: x, y, w, h)."""
        f = self.f
        prog = self.trace
        prog.bind()
        for name, unit in UNITS.items():
            self.textures[name].bind(unit)
            prog.setUniformValue1i(prog.uniformLocation(SAMPLERS[name]), unit)
        self.ct_tex.bind(CT_UNIT)
        prog.setUniformValue1i(prog.uniformLocation("uCtMat"), CT_UNIT)
        _set_uniforms(prog, self.ct_uniforms)
        _set_uniforms(prog, uniforms)
        _set_uniforms(prog, {"uNBodys": self.nbodys, "uMaxSteps": self.max_steps, "uScale": float(scale)})
        f.glDisable(GL_DEPTH_TEST)
        f.glDisable(GL_BLEND)
        f.glViewport(0, 0, width, height)
        if scissor is not None:
            f.glEnable(GL_SCISSOR_TEST)
            f.glScissor(*scissor)
        self.vao.bind()
        f.glDrawArrays(GL_TRIANGLES, 0, 3)
        self.vao.release()
        if scissor is not None:
            f.glDisable(GL_SCISSOR_TEST)
        prog.release()

    def display(self, texture_id: int, width: int, height: int, edges: bool, linear: bool = True,
                depth_texture: int | None = None) -> None:
        """Copia una imagen trazada (textura) al *framebuffer* activo, con o sin fronteras; con ``depth_texture``,
        también su profundidad (a la segunda textura del destino, si la tiene)."""
        f = self.f
        prog = self.show
        prog.bind()
        if depth_texture is not None:
            f.glActiveTexture(GL_TEXTURE0 + DEPTH_COPY_UNIT)
            f.glBindTexture(GL_TEXTURE_2D, depth_texture)
        f.glActiveTexture(GL_TEXTURE0 + DISPLAY_UNIT)
        f.glBindTexture(GL_TEXTURE_2D, texture_id)
        filt = GL_LINEAR if linear else GL_NEAREST
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filt)
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filt)
        prog.setUniformValue1i(prog.uniformLocation("uImg"), DISPLAY_UNIT)
        prog.setUniformValue1i(prog.uniformLocation("uDepthImg"), DEPTH_COPY_UNIT)
        prog.setUniformValue1i(prog.uniformLocation("uCopyDepth"), int(depth_texture is not None))
        prog.setUniformValue(prog.uniformLocation("uDst"), QVector2D(width, height))
        prog.setUniformValue1i(prog.uniformLocation("uEdges"), int(edges))
        f.glDisable(GL_DEPTH_TEST)
        f.glDisable(GL_BLEND)
        f.glDisable(GL_SCISSOR_TEST)
        f.glViewport(0, 0, width, height)
        self.vao.bind()
        f.glDrawArrays(GL_TRIANGLES, 0, 3)
        self.vao.release()
        f.glBindTexture(GL_TEXTURE_2D, 0)
        if depth_texture is not None:
            f.glActiveTexture(GL_TEXTURE0 + DEPTH_COPY_UNIT)
            f.glBindTexture(GL_TEXTURE_2D, 0)
        f.glActiveTexture(GL_TEXTURE0)
        prog.release()

    def render_image(self, uniforms: dict, width: int, height: int) -> np.ndarray:
        """Traza una imagen completa en un FBO propio y la devuelve (alto, ancho, RGBA) uint8."""
        fbo = QOpenGLFramebufferObject(width, height, QOpenGLFramebufferObjectFormat())
        fbo.bind()
        self.draw(uniforms, 1.0, width, height)
        self.f.glFinish()
        img = fbo.toImage(False)  # sin reordenar filas: fila 0 = abajo
        fbo.release()
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(height, img.bytesPerLine() // 4, 4)[:, :width]
        # QImage (ARGB32, little endian) guarda B, G, R, A; se pasa a RGBA con la fila 0 arriba.
        out = arr[::-1, :, [2, 1, 0, 3]].copy()
        del fbo
        return out

    def _take_textures(self) -> list[QOpenGLTexture]:
        texs = list(self.textures.values())
        if self.ct_tex is not None:
            texs.append(self.ct_tex)
        self.textures.clear()
        self._shapes.clear()
        self.ct_tex = None
        self._ct_key = None
        return texs

    def destroy(self) -> None:
        """Libera texturas, VAO y *shaders*; con el contexto activo. Se puede llamar más de una vez."""
        for tex in self._take_textures():
            tex.destroy()
        # Al cerrar la aplicación Qt puede haber borrado ya el VAO o los programas (hijos del widget) antes de avisar
        # de que el contexto se destruye: entonces no queda nada que liberar.
        for release in (lambda: self.vao.destroy(), lambda: self.trace.removeAllShaders(),
                        lambda: self.show.removeAllShaders()):
            try:
                release()
            except RuntimeError:  # objeto de C++ ya borrado
                pass
        # Las funciones de OpenGL son del contexto (no son un QObject: PySide no sabe cuándo se borran). Si su
        # envoltorio de Python sobreviviera al contexto, otro objeto creado después en la misma dirección de memoria
        # podría recibir ese envoltorio viejo (se vio: una QAction que llegaba como QOpenGLFunctions).
        self.f = None

    def _context_dying(self, ctx: QOpenGLContext) -> None:
        """El contexto va a destruirse y no se ha llamado a ``destroy``: se activa (en una superficie fuera de
        pantalla, como hace Qt con sus VAO) y se liberan los recursos; después se deja activo el que lo estaba.

        Hay que hacerlo: una QOpenGLTexture que sobrevive a su contexto guarda un puntero a él, y su destructor
        lo usa si en ese momento hay otro contexto activo (fallo de segmentación, al azar, cuando el recolector
        de Python libere este objeto)."""
        if not self.textures and self.ct_tex is None:
            self.f = None  # (ver destroy)
            return
        prev = QOpenGLContext.currentContext()
        prev_surface = prev.surface() if prev is not None else None
        surface = None
        if prev is not ctx:
            surface = QOffscreenSurface()
            surface.setFormat(ctx.format())
            surface.create()
            if not ctx.makeCurrent(surface):  # pragma: no cover - depende del controlador
                _ORPHANS.extend(self._take_textures())
                return
        try:
            self.destroy()
        finally:
            if prev is not ctx:
                ctx.doneCurrent()
                if prev is not None and prev_surface is not None:
                    prev.makeCurrent(prev_surface)


def image_fbo(ctx: QOpenGLContext, width: int, height: int, depth: bool = True) -> QOpenGLFramebufferObject:
    """Búfer para una imagen trazada: el color y, con ``depth``, una segunda textura (RGBA32F) con la profundidad
    de lo primero opaco y de lo primero visible de cada píxel (para tapar las trayectorias con la geometría)."""
    fbo = QOpenGLFramebufferObject(width, height, QOpenGLFramebufferObjectFormat())
    if depth:
        fbo.addColorAttachment(width, height, GL_RGBA32F)
        fbo.bind()
        ctx.extraFunctions().glDrawBuffers(2, [GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT0 + 1])
        fbo.release()
    return fbo


def depth_texture(fbo: QOpenGLFramebufferObject | None) -> int | None:
    """La textura de profundidad de un búfer de :func:`image_fbo` (None si no la tiene)."""
    if fbo is None:
        return None
    tex = fbo.textures()
    return int(tex[1]) if len(tex) > 1 else None


class OffscreenGpu:
    """Contexto OpenGL fuera de pantalla con su ``GpuTracer`` (pruebas, exportar imágenes)."""

    def __init__(self):
        self.ctx = QOpenGLContext()
        self.ctx.setFormat(surface_format())
        if not self.ctx.create():
            raise GpuError("no se puede crear un contexto OpenGL")
        self.surface = QOffscreenSurface()
        self.surface.setFormat(self.ctx.format())
        self.surface.create()
        self.current()
        self.tracer = GpuTracer(self.ctx)

    def current(self) -> None:
        """Activa este contexto (otro widget OpenGL puede haber dejado activo el suyo o ninguno)."""
        if not self.ctx.makeCurrent(self.surface):
            raise GpuError("no se puede activar el contexto OpenGL")

    def close(self) -> None:
        self.current()
        self.tracer.destroy()
        self.ctx.doneCurrent()


def _set_uniforms(prog: QOpenGLShaderProgram, values: dict) -> None:
    for name, v in values.items():
        loc = prog.uniformLocation(name)
        if loc < 0:
            continue
        if isinstance(v, tuple) and len(v) == 3 and all(isinstance(x, int) for x in v):  # ivec3
            _uniform3i(prog, loc, v)
        elif isinstance(v, bool | int | np.integer):
            prog.setUniformValue1i(loc, int(v))
        elif isinstance(v, float | np.floating):
            prog.setUniformValue1f(loc, float(v))
        else:
            prog.setUniformValue(loc, v)


def id_image(rgba: np.ndarray) -> np.ndarray:
    """Índices de cuerpo de una imagen trazada con ``mode = 1``."""
    a = rgba.astype(np.int64)
    return a[..., 0] | (a[..., 1] << 8) | (a[..., 2] << 16)


def _uniform3i(prog: QOpenGLShaderProgram, loc: int, v: tuple[int, int, int]) -> None:
    """ivec3 (QOpenGLShaderProgram no tiene setUniformValue para tres enteros)."""
    QOpenGLContext.currentContext().functions().glUniform3i(loc, int(v[0]), int(v[1]), int(v[2]))
