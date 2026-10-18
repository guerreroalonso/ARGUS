# ADR-023 · El desplegable sin la carpeta de penhan, moverse en 3D sin perder lo pequeño y «Lluvias», un solo número

**Estado:** aceptada (versión 0.17.1)

## Contexto

Rafael, tras probar el ejecutable de Windows de 0.17.0 (18-10-2026):

1. «el desplegable en la versión de Windows no funciona; es verdad que no le había asignado la carpeta de penhan».
2. «Cuando zoomeas la velocidad de movimiento tiene que ser proporcional al zoom (…) en la vista tridimensional,
   porque si no para cosas muy chicas pierdo muy rápido el objeto».
3. «Modificar lo de las lluvias, hay que ponerlo más intuitivo (…): que básicamente solo tenga que poner el número de
   lluvias máxima, que como máximo puede ser 1000». El resto, «perfecto».

## Decisiones

### 1. Sin la lista de penhan, la que trae ARGUS

La lista de los 280 materiales del desplegable salía de `database/material-list.txt`: la de «Programas de penhan» o
la que se encontrara subiendo desde la carpeta del caso o desde la de ARGUS. Desde el código, la carpeta de ARGUS
está junto a su penhan (`SOBP_Investigacion/ProgramasBase/penhan`) y se encontraba siempre. El ejecutable está en la
carpeta de programas: sin «Programas de penhan» y con un caso lejos de penhan, la lista quedaba vacía y el
desplegable no enseñaba nada.

Ahora, si no se encuentra la del usuario, se usa la de ARGUS (`material_names.NAMES`: los mismos números y nombres;
comparada con la `material-list.txt` de Rafael, idéntica en las 280). La del usuario sigue yendo primero. Que es la
lista de ARGUS lo dicen el diálogo del material (junto al elegido) y «Asignar materiales…» (en gris), con lo que hará
falta para generarlo. Generar el `.mat` sigue pidiendo su penhmat (la carpeta de penhan), como antes.

### 2. Moverse en la vista 3D a la escala de lo que se mira

La cámara giraba alrededor de su objetivo y la rueda la acercaba al plano del objetivo, moviendo el objetivo hacia
el cursor dentro de ese plano. Algo pequeño delante de ese plano se pasaba de largo; detrás, no se acercaba más
que su distancia al plano. Y al menor giro se salía de la imagen: se giraba alrededor de un punto lejos de él. En
0.17.0, 30 pasos de la rueda sobre una esfera de 0,5 cm lejos del centro la dejaban a −41,8 cm, detrás de la cámara.

Ahora, lo que se mira es lo que hay bajo el cursor. Es la superficie opaca (a través de lo translúcido) o el punto
de una trayectoria dibujada a menos de 10 píxeles; de los dos, el más cercano a la cámara. Con eso:

* **La rueda** (perspectiva) acerca la cámara hacia ese punto, por la recta que los une. El punto se queda en su
  píxel, se acerca un 15 % por paso y nunca se pasa. El objetivo queda a su profundidad, así que después girar y
  desplazar van a su escala. Si no hay nada bajo el cursor, la rueda hace lo de antes. En ortográfica, también lo
  de antes: allí la escala no depende de la profundidad.
* **Girar** es alrededor del punto cogido al pulsar (aquí también vale lo translúcido), que se queda en su píxel.
* **Desplazar:** lo cogido sigue al ratón píxel a píxel. Antes iba a la escala del objetivo: algo 20 veces más cerca
  que él se movía 20 veces más deprisa que el ratón.
* Las flechas, `+`/`−` y el doble clic, como antes. Las flechas y `+`/`−` van alrededor del objetivo, que después de
  la rueda está a la profundidad de lo que se mira.
* **Las trayectorias en la GPU** no se dibujaban a menos de 1 µm de la cámara. Ahora el plano cercano es 1 µm o, más
  cerca de algo pequeño, una diezmilésima de la distancia (sin GPU ya era así).

Vale igual en la ventana principal y en el visor de la cascada (los dos usan la misma vista 3D). Los puntos de las
trayectorias se guardan mientras no cambien la capa, sus filtros o las trayectorias de LTRACK: la rueda los pide a
cada paso.

### 3. «Lluvias», un solo número

En 0.17.0 había dos números para casi lo mismo, y el resto eran explicaciones:

* **«Trabajo»** (en el panel Simulación y en «Ejecutar») tenía **«Trayectorias»** (`LTRACK`, las primeras lluvias de
  la propia simulación en sus `.trk`; penhmain escribe una más, como mucho 51). «Elegir resultados…» tenía su
  tarjeta, y el resumen de «Ejecutar» lo contaba.
* **«Lluvias»**, al ejecutar (la cascada: hasta 1000, simuladas aparte), llevaba al lado «20 tandas de 50», una ayuda
  emergente con las tandas y un párrafo sobre la carpeta «cascada», las semillas y LTRACK + 1.

Se ha elegido dejar un solo número (opción A):

* **«Lluvias»: de 1 a 1000** (0, «ninguna»: sin ellas). Al lado solo «para verlas en 3D (como mucho 1000)» o «no se
  simula ninguna para verlas en 3D». La ayuda emergente dice qué son y que con protones, alfas o neutrones tardan más.
  Cómo las simula ARGUS (aparte, en tandas de lo que guarda ese penhmain, en la carpeta «cascada») no se cuenta y no
  cambia: la cascada sigue usando lo que guarda cada penhmain y el penhmain que queda en la carpeta.
* **`LTRACK` sale de la interfaz:** de «Trabajo», de «Elegir resultados…» y del resumen de «Ejecutar». Un `.in` que lo
  trae lo conserva: ARGUS no lo quita, sus avisos siguen yendo a «Trabajo» y sus `.trk` se ven en la ventana de
  resultados («Trayectorias»), como antes.
* **El visor de la cascada no cambia** («y ya en el display exactamente igual»).
* En la campaña, «Lluvias por variante» (era `NSIMSH`) pasa a «Partículas por variante», como en «Trabajo»: «Lluvias»
  queda solo para las que se ven en 3D.

Descartadas:

* (B) dejar `LTRACK` y explicarlo mejor. Es lo que hizo 0.16.0, y no resultaba intuitivo.
* (C) que «Lluvias» escriba `LTRACK` en el `.in`. penhmain guarda como mucho 51 lluvias por ejecución; las 1000 salen
  de la cascada, que son ejecuciones aparte.

### 4. Guardar con la lista del material desplegada

Con la lista siempre presente (la de ARGUS), una prueba de antes falló y sacó un fallo que ya había desde 0.16.0
cuando se encontraba la lista. Al escribir el nombre de un material en la tabla del panel se despliega lo que
coincide; con la lista abierta, el campo no se confirma al perder el foco (es lo que pasa al abrirse la lista). Guardar
le quita el foco para confirmarlo, así que el nombre recién escrito no se guardaba. Ahora, antes de guardar, deshacer,
abrir o cerrar, la ventana cierra la lista que esté abierta y lo escrito se confirma
(`MainWindow._commit_pending`).

## Verificación

* `tests/test_material_list_builtin.py` (4 pruebas):
  * la lista de ARGUS;
  * la del usuario primero;
  * «Generar .mat y ENDF…» sin la del usuario;
  * la ventana sin la carpeta de penhan: los 280 en el desplegable, y «agua» los acota.
* `tests/test_view3d_navigation.py` (10 pruebas):
  * la cámara: girar alrededor de un punto, desplazar a su profundidad y acercarse sin pasarse;
  * el plano cercano de las líneas;
  * en la ventana, sin GPU:
    * acercarse 30 pasos a una esfera de 0,5 cm lejos del centro, que sigue bajo el cursor 130 veces más cerca;
    * girar y desplazar desde ella;
    * sin nada bajo el cursor, lo de antes;
    * una trayectoria de 1 µm;
    * los filtros del visor de la cascada;
  * con la GPU (Mesa): una trayectoria de 2 µm vista a medio µm se dibuja bajo el cursor; con el plano cercano de
    antes, no.
* «Lluvias» y `LTRACK`, en las pruebas de antes que las usaban, reescritas:
  * `test_gui_material_library`: un solo número y sin tandas ni carpetas, y el `LTRACK` del `.in` se queda;
  * `test_gui_review016`: la cascada con lo que guarda el penhmain que se usa o que sustituye al de la carpeta;
  * `test_goals`: el diálogo de resultados sin la tarjeta;
  * `test_matrix_review6` y `test_review5_ui`: «Trabajo» sin el campo;
  * `test_batch`: «Partículas por variante».
* `test_distribucion`: la versión, la misma en `pyproject.toml`, en `src/argus/__init__.py` y en `uv.lock`.
* `test_gui_review08`: guardar con la lista del material desplegada (falla con el código de antes).
* La batería completa de pruebas y ruff; con la GPU (Mesa), las de la vista 3D, las trayectorias y el visor de la
  cascada.

## Consecuencias

* El ejecutable de 0.17.0 no lo tiene. 0.17.1 no llegó a publicarse: va en 0.17.2 (ADR-024), con la décima
  revisión; el flujo «Ejecutables» crea la publicación nueva y su etiqueta.
* Desde el código (`ARGUS.bat`) se puede probar ya.
* Falta que Rafael lo pruebe en su Windows: el desplegable sin «Programas de penhan» y moverse en 3D (con GPU).
