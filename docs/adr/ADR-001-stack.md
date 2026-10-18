# ADR-001: pila tecnológica

Fecha: 2026-09-23. Estado: aceptada.

## Contexto

ARGUS debe leer, visualizar (2D y 3D), editar y escribir ficheros de
PENGEOM y penhan con fidelidad exacta al código Fortran, funcionar en un
portátil con Intel UHD Graphics y también en Mac y en equipos con GPU NVIDIA,
e instalarse sin complicaciones en Windows y macOS.

## Decisión

* **Python 3.12** (compatible con 3.11), entornos gestionados con **uv**.
* **PySide6 (Qt Widgets)** para la interfaz: paneles acoplables, barras de
  herramientas y árbol de capas al estilo de QCAD, igual en Windows, macOS y
  Linux, licencia LGPL.
* **NumPy** para el núcleo numérico (cuádricas, LOCATE vectorizado, cortes).
* **Render 3D propio con OpenGL 4.1 core** mediante las clases `QOpenGL*` de
  PySide6 (sin dependencias nativas extra como moderngl), trazando las
  cuádricas en el *fragment shader* (ver ADR-002).
* **gfortran** solo para la sonda de verificación (pruebas), nunca en tiempo
  de ejecución.
* Empaquetado con **PyInstaller** (+ Inno Setup en Windows, `.app` en macOS).

## Alternativas descartadas

* Java/JavaFX (tecnología de PenGeomJar): el objetivo es sustituir su visor
  por uno más fluido; mantener Java no aporta y dificulta el uso de NumPy.
* VTK/PyVista: exigiría mallar las cuádricas (pierde exactitud en los bordes y
  cuesta memoria en geometrías como `mlc` o `spheres`) y pesa mucho en el
  instalador.
* moderngl: sin ruedas para ARM en algunas plataformas; `QOpenGL*` ya viene
  con PySide6.

## Consecuencias

* Toda la lógica de PENGEOM se reimplementa en Python y se verifica bit a bit
  contra el Fortran (ADR-003).
* El visor 2D exacto no depende de la GPU y sirve de referencia para el 3D.
