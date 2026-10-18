# ADR-008 · Revisión de fallos de la versión 0.7

**Estado:** aceptada (versión 0.7.1)

## Contexto

Tras el editor completo (ADR-007) se revisó todo el programa buscando fallos:
análisis estático, revisión del código por partes con pruebas que reproducen
cada sospecha (frente a PENGEOM y penhmain reales cuando la pregunta era qué
hacen ellos), una prueba «de mono» de la ventana con los 126 `.in` del usuario
(clics de los menús contextuales, arrastres, deshacer/rehacer, guardar y volver
a abrir) y las pruebas de la interfaz con la configuración regional española y
la pantalla al 150 %.

## Decisiones

1. **Numba en Windows.** Si no hay TBB ni el OpenMP de Microsoft, Numba usa la
   capa «workqueue», que cierra el proceso si dos hilos lanzan a la vez núcleos
   `parallel=True` (el corte 2D tiene su hilo; la vista 3D y la compilación
   inicial, los suyos). Todos esos núcleos pasan por un cerrojo
   (`core/parallel.py`); cada uno ya usa todos los núcleos de la CPU.
2. **Nada a medias.** Antes de guardar, deshacer, rehacer, abrir, cerrar o crear
   se anula el arrastre en curso (fuente, rejilla o módulo; también al cambiar a
   la vista 3D sola o al editar por otro lado) y se confirma lo que se está
   escribiendo en un campo (los botones y los menús no se llevan el foco). Esc
   antes de que se aplique el primer paso de un arrastre ya no lo deja
   enganchado, y el arrastre no pasa al fichero siguiente.
3. **La rueda del ratón** no cambia desplegables ni números que no se han
   pulsado (`ui/wheel_guard.py`): desplaza el panel.
4. **Guardar.** El `.bak` no copia el atributo de solo lectura (bloqueaba los
   guardados siguientes); si la geometría no se puede guardar, tampoco se guarda
   el `.in` (podría nombrar los cuerpos con la numeración nueva). «Nueva
   geometría» pregunta una sola vez por los cambios; tras «Nuevo .in», los pasos
   de la geometría que renumeraron el `.in` cerrado ya no se deshacen.
5. **Lectura como Fortran.** Los registros se separan solo en `\n`
   (`str.splitlines` también partía en el «…» de Windows, 0x85, y en el salto de
   página). En los campos numéricos del `.geo`, una coma es un error (lectura
   interna de gfortran; antes «2,5» se leía 2E-15). En el `.in`: enteros fuera
   de `INTEGER*4`, factor de repetición cero y números entre apóstrofos son
   errores; `Inf` y `NaN` se aceptan (como gfortran). Los nombres con espacios
   son válidos (penhmain lee 20 columnas) y los que penhmain lee dirigidos por
   lista (`SRNUCL`) se escriben entre apóstrofos si hace falta.
6. **Límites de GEOMIN que no avisan.** Con 249 hijas o más en un módulo (o en el
   nivel superior), o 250 cuerpos limitantes en un cuerpo, la tabla (`KDGHT`,
   `KBODY`, `NXG = 250`) se desborda sin error y LOCATE falla: es un error en
   los mensajes y las ediciones que lo provocarían se rechazan.
7. **Ediciones locales.** Al comprobar un cambio de forma, además de los solapes
   nuevos se mira que no cambien otros cuerpos (una superficie compartida con un
   módulo girado se mueve con él). Duplicar y envolver tienen en cuenta lo que
   asoman los cuerpos limitantes. Las comprobaciones de «¿cabe?» añaden rayos por
   los tres ejes: el muestreo no ve una lámina de 1 µm, un rayo sí. Un detector
   que se queda sin cuerpos al eliminar uno se quita entero (penhmain no admite
   detectores sin cuerpos, IRETRN 3101).
8. **Otros.** WGTWIN al revés (3039) y un fichero de espacio de fases vacío
   (3117) se avisan; los volcados dañados no rompen la lectura; la sección de la
   rejilla de dosis se actualiza al soltar el arrastre; los espectros pegados o
   importados de una hoja de cálculo en español (BOM, UTF-16, separador de
   miles, última probabilidad en blanco) se leen bien; el zoom del corte tiene
   límites; las asas de la rejilla se cogen con la fuente oculta; la caja de la
   escena 3D incluye los materiales ocultos; el tamaño del framebuffer se
   redondea como Qt (pantalla al 150 %); tras cambiar a una geometría pesada, las
   primeras órdenes a la GPU son pequeñas (vigilante TDR de Windows); las pruebas
   no tocan los ajustes del usuario.

## Consecuencias

* Cada fallo tiene su prueba (`tests/test_review_*.py`, `tests/test_gui_review.py`,
  `tests/test_gui_robustness.py`), con PENGEOM o penhmain reales cuando la
  pregunta es qué hacen ellos.
* Queda como está (a propósito): ARGUS es más estricto que gfortran con números
  raros en el `.geo` (`E5`, `.`, `++1`...), que GEOMIN lee como 0.
