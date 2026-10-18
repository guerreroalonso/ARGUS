"""Filas de controles que siguen en otra línea cuando no caben (pantallas al 125 o 150 %)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget  # noqa: E402

from argus.ui.flow_layout import FlowLayout, group  # noqa: E402


def _host(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    flow = FlowLayout()
    lay.addLayout(flow)
    body = QWidget()  # lo de debajo (la gráfica)
    lay.addWidget(body, 1)
    return host, flow, body


def _resize(qtbot, host, w, h=300):
    host.resize(w, h)
    if not host.isVisible():
        host.show()
        qtbot.waitExposed(host)
    qtbot.wait(10)
    host.layout().activate()


def _lines(widgets):
    return sorted({w.geometry().y() for w in widgets if w.isVisible()})


@pytest.mark.gui
def test_a_row_wraps_when_it_does_not_fit(qtbot):
    host, flow, body = _host(qtbot)
    buttons = [QPushButton(f"Botón número {i}") for i in range(8)]
    for b in buttons:
        flow.addWidget(b)
    one_line = flow.sizeHint().width()
    _resize(qtbot, host, one_line + 40)
    assert len(_lines(buttons)) == 1
    _resize(qtbot, host, one_line // 3)
    assert len(_lines(buttons)) >= 3
    # Nada se pisa, todo cabe y lo de debajo empieza después de la última línea.
    for a in buttons:
        assert a.geometry().right() < host.width()
        for b in buttons:
            assert a is b or not a.geometry().intersects(b.geometry())
    assert body.geometry().y() > max(b.geometry().bottom() for b in buttons)
    # La ventana puede ser tan estrecha como el botón más ancho (no como la fila entera).
    assert host.minimumSizeHint().width() <= max(b.sizeHint().width() for b in buttons)
    assert flow.heightForWidth(one_line // 3) > flow.heightForWidth(one_line + 40)


@pytest.mark.gui
def test_stretch_expanding_hidden_and_spacing_like_qhboxlayout(qtbot):
    host, flow, body = _host(qtbot)
    label = QLabel("Velocidad:")
    slider = QSlider(Qt.Orientation.Horizontal)
    check = QCheckBox("Estela")
    flow.addWidget(label)
    flow.addWidget(slider, 1)
    flow.addWidget(check)
    _resize(qtbot, host, 600)
    # El deslizador se queda con lo que sobra: la casilla, al final de la línea.
    assert check.geometry().right() == host.width() - 1
    assert slider.width() > slider.sizeHint().width()
    # Un control oculto no ocupa sitio.
    label.hide()
    host.layout().activate()
    assert slider.geometry().x() == 0
    label.show()
    # Un estiramiento empuja lo que va detrás a la derecha de su línea.
    host2, flow2, _ = _host(qtbot)
    a, info = QLabel("Partículas:"), QLabel("12 trayectorias")
    flow2.addWidget(a)
    flow2.addStretch(1)
    flow2.addWidget(info)
    _resize(qtbot, host2, 500)
    assert a.geometry().x() == 0 and info.geometry().right() == host2.width() - 1
    # Un espacio fijo al principio de una línea no se pone.
    host3, flow3, _ = _host(qtbot)
    b1, b2 = QPushButton("Primero"), QPushButton("Segundo")
    flow3.addWidget(b1)
    flow3.addSpacing(40)
    flow3.addWidget(b2)
    _resize(qtbot, host3, 400)
    assert b2.geometry().x() == b1.geometry().right() + 1 + 40
    _resize(qtbot, host3, max(b1.sizeHint().width(), b2.sizeHint().width()) + 10)
    assert b2.geometry().x() == 0 and b2.geometry().y() > b1.geometry().y()


@pytest.mark.gui
def test_groups_insert_and_deleted_widgets(qtbot):
    host, flow, body = _host(qtbot)
    first = QPushButton("Reproducir")
    lab, combo_like = QLabel("Color:"), QPushButton("por partícula")
    flow.addWidget(first)
    flow.addWidget(group(lab, combo_like))
    _resize(qtbot, host, first.sizeHint().width() + 20)
    # La etiqueta y su campo, siempre en la misma línea.
    gl = lab.mapTo(host, lab.rect().topLeft()).y()
    gc = combo_like.mapTo(host, combo_like.rect().topLeft()).y()
    assert abs(gl - gc) < combo_like.height() and gl > first.geometry().y()
    # Insertar delante y borrar un control (Qt quita su elemento).
    extra = QCheckBox("Escalonadas")
    flow.insertWidget(0, extra)
    assert flow.itemAt(0).widget() is extra and flow.count() == 3
    extra.deleteLater()
    qtbot.waitUntil(lambda: flow.count() == 2)
    _resize(qtbot, host, 800)
    assert len(_lines([first])) == 1
    # Un grupo con todo oculto no ocupa sitio (ni el espacio entre controles).
    assert flow.sizeHint().width() > first.sizeHint().width()
    lab.hide()
    combo_like.hide()
    assert flow.sizeHint().width() == first.sizeHint().width()
    taken = flow.takeAt(0)
    assert taken.widget() is first and flow.count() == 1 and flow.takeAt(5) is None


@pytest.mark.gui
def test_what_is_not_placed_does_not_cover_the_row(qtbot):
    """Un grupo con todo oculto o un contenedor de casillas vacío (creados después que los controles) no se quedan
    encima de ellos con su tamaño por defecto (640 × 480): el clic tiene que llegar al control."""
    from PySide6.QtWidgets import QLineEdit

    host, flow, body = _host(qtbot)
    first = QPushButton("Ver: Espectro de energía")
    flow.addWidget(first)
    empty = QWidget()
    FlowLayout(empty)  # el contenedor de las casillas, sin ninguna (una sola partícula)
    flow.addWidget(empty)
    hidden_label = QLabel("Plano:")
    hidden_group = group(hidden_label)
    hidden_label.hide()
    flow.addWidget(hidden_group)
    _resize(qtbot, host, 600)
    for w in (empty, hidden_group):
        assert not w.isHidden() and w.geometry().isEmpty()
    c = first.geometry().center()
    assert host.childAt(c) is first
    # Un grupo no crece aunque tenga un campo de texto (que se expande): la casilla y su campo, juntos.
    host2, flow2, _ = _host(qtbot)
    iso, levels = QCheckBox("Isolíneas (%):"), QLineEdit("20 50 80 95")
    levels.setMaximumWidth(120)
    g = group(iso, levels)
    flow2.addWidget(g)
    flow2.addWidget(QCheckBox("Reflejar r"))
    _resize(qtbot, host2, 900)
    assert g.width() == g.sizeHint().width()


@pytest.mark.gui
def test_the_materials_panel_asks_for_the_height_its_buttons_need(qtbot):
    """En un panel estrecho (la pantalla al 150 %), los botones de «Materiales» pasan a otra línea y el panel pide la
    altura que necesitan: un QDockWidget no usa heightForWidth y antes «Colores por defecto» quedaba fuera."""
    from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow

    from argus.ui.materials_panel import MaterialsPanel

    win = QMainWindow()
    qtbot.addWidget(win)
    panel = MaterialsPanel()
    dock = QDockWidget("Materiales", win)
    dock.setWidget(panel)
    win.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    win.setCentralWidget(QWidget())
    win.resize(1000, 300)
    win.show()
    qtbot.waitExposed(win)
    buttons = (panel.btn_add, panel.btn_gen, panel.btn_txt, panel.btn_new, panel.btn_colors)
    for b in buttons:
        b.setVisible(True)  # como con un .in abierto
    win.resizeDocks([dock], [190], Qt.Orientation.Horizontal)
    for _ in range(5):
        QApplication.processEvents()
        qtbot.wait(20)
    need = panel.layout().totalMinimumHeightForWidth(panel.width())
    assert panel.minimumHeight() >= need > 0
    win.resize(1000, 900)
    qtbot.wait(50)
    for b in buttons:
        assert b.isVisible() and b.geometry().bottom() <= panel.height(), b.text()
    assert len({b.geometry().y() for b in buttons}) >= 3  # en varias líneas
