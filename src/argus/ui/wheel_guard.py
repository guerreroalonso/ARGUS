"""La rueda del ratón no cambia los desplegables ni los números que no se han pulsado.

Por defecto, Qt deja que la rueda cambie un QComboBox o un QSpinBox que esté
bajo el puntero aunque no tenga el foco: al desplazar un panel largo (Fuente,
Simulación, Editar), si el puntero pasa por encima de uno, su valor cambia y se
escribe en el .in o en el .geo sin que el usuario lo note. Con este filtro, sobre
un campo sin foco la rueda desplaza el panel que lo contiene; para cambiar el
valor con la rueda, primero se pulsa el campo.

Lo mismo con las teclas: los atajos de una sola tecla de la ventana (F, G, 1-3,
F2-F4, Supr) no se disparan con el foco en un campo, un desplegable, una lista,
una tabla o un texto (``ShortcutGuard``).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDockWidget,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)


def _scroll_areas(w):
    """Los paneles desplazables (y tablas) que contienen al campo, del más cercano al más lejano."""
    area = w.parentWidget()
    while area is not None:
        if isinstance(area, QAbstractScrollArea):
            yield area
        area = area.parentWidget()


def _scroll_area(w):
    """El panel desplazable (o la tabla) que contiene al campo, si lo hay."""
    return next(_scroll_areas(w), None)


def _scroll(w, dy: int) -> None:
    """Desplaza el primero de los paneles que contienen a ``w`` que todavía se puede mover hacia ``dy`` (una tabla
    sin nada que desplazar, o ya en su tope, deja pasar la rueda al panel que la contiene)."""
    for area in _scroll_areas(w):
        bar = area.verticalScrollBar()
        if bar is None or bar.maximum() <= bar.minimum():
            continue
        if (dy > 0 and bar.value() <= bar.minimum()) or (dy < 0 and bar.value() >= bar.maximum()):
            continue
        lines = QApplication.wheelScrollLines() * bar.singleStep()
        bar.setValue(bar.value() - round(dy / 120 * lines))
        return


class WheelGuard(QObject):
    """Solo en campos dentro de un panel desplazable o de una tabla (Fuente, Simulación, Editar, Materiales):
    en los diálogos y en la barra de herramientas (profundidad del corte) la rueda sigue como siempre."""

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 - nombre de Qt
        t = ev.type()
        if t == QEvent.Type.Polish and isinstance(obj, (QAbstractSpinBox, QComboBox)):
            if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus and _scroll_area(obj) is not None:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # la rueda no da el foco
        elif t == QEvent.Type.Wheel and isinstance(obj, (QAbstractSpinBox, QComboBox)) and not obj.hasFocus():
            if _scroll_area(obj) is None:
                return False
            dy = ev.angleDelta().y()
            if dy:
                _scroll(obj, dy)
            return True
        return False


def install_wheel_guard() -> None:
    """Instala el filtro en la aplicación (una sola vez)."""
    app = QApplication.instance()
    if app is None or getattr(app, "_argus_wheel_guard", None) is not None:
        return
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    app._argus_wheel_guard = guard


# Atajos de una sola tecla de la ventana principal que también usan los campos, listas, tablas y textos.
PANEL_KEYS = frozenset({Qt.Key.Key_F, Qt.Key.Key_G, Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_F2,
                        Qt.Key.Key_F3, Qt.Key.Key_F4, Qt.Key.Key_Delete})
INPUT_WIDGETS = (QLineEdit, QAbstractSpinBox, QComboBox, QAbstractItemView, QTextEdit, QPlainTextEdit)
# Las teclas de función solo son de los widgets que las usan: F2 edita la celda de una lista o una tabla y F4 abre
# un desplegable; F3 no la usa ninguno. Desde un campo (p. ej. la profundidad del corte en la barra), F2-F4
# cambian la vista.
KEY_OWNERS = {Qt.Key.Key_F2: (QAbstractItemView,), Qt.Key.Key_F3: (), Qt.Key.Key_F4: (QComboBox,)}
_COMMAND_MODIFIERS = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                      | Qt.KeyboardModifier.MetaModifier)


class ShortcutGuard(QObject):
    """Con el foco en un campo, un desplegable, una lista, una tabla o un texto de ``window``, las teclas de
    ``keys`` son del widget y no del atajo de la ventana: F2 edita la celda, «3» busca en el desplegable, Supr
    en el texto del .geo no pregunta si se elimina el cuerpo seleccionado. En el corte, la vista 3D y los
    widgets de ``exempt`` (el árbol de la geometría) los atajos siguen valiendo."""

    def __init__(self, window, keys=PANEL_KEYS, exempt=()):
        super().__init__(window)
        self._window = window
        self._keys = frozenset(keys)
        self._exempt = tuple(exempt)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 - nombre de Qt
        if ev.type() != QEvent.Type.ShortcutOverride or ev.key() not in self._keys:
            return False
        if ev.modifiers() & _COMMAND_MODIFIERS:
            return False
        w = QApplication.focusWidget()
        owners = KEY_OWNERS.get(ev.key(), INPUT_WIDGETS)
        if obj is not w or not owners or not isinstance(w, owners) or not self._owns(w):
            return False
        ev.accept()  # aceptar el ShortcutOverride: la tecla llega al widget y el atajo no se dispara
        return True

    def _owns(self, w) -> bool:
        """¿Está ``w`` en la ventana (también en un panel flotante) y fuera de los widgets exentos? Los diálogos
        no se tocan: allí los atajos de la ventana no valen (y pueden tener los suyos)."""
        top = w.window()
        if top is not self._window and not (isinstance(top, QDockWidget) and top.parentWidget() is self._window):
            return False
        p = w
        while p is not None:
            if any(p is x for x in self._exempt):
                return False
            if p is top:
                return True
            p = p.parentWidget()
        return False


def install_shortcut_guard(window, exempt=()) -> ShortcutGuard:
    """Instala en la aplicación el filtro de las teclas sueltas de ``window`` (se quita al cerrarla)."""
    guard = ShortcutGuard(window, exempt=exempt)
    QApplication.instance().installEventFilter(guard)
    return guard
