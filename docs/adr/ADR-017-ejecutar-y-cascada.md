# ADR-017 · Ejecutar penhmain desde ARGUS, la cascada de partículas y la matriz de dosis

**Estado:** aceptada (versión 0.13.0; revisada en la 0.13.1, apartado 6)

## Contexto

Rafael pidió (06-10-2026) que ARGUS haga todo el proceso de crear una simulación:

* definir y cambiar desde el entorno la matriz de dosis, sus vóxeles y los intervalos (bins) de cada resultado;
* elegir un `penhmain.x` y que se copie a la carpeta de la simulación;
* un botón «Ejecutar» que abra un terminal con `penhmain < caso.in` y la ventana de resultados, que los vaya
  enseñando (los que pide el `.in`: el usuario elige cuáles pide);
* un visor de la cascada («shower») con las primeras 1000 lluvias **simuladas con su penhmain**, para ver las
  trayectorias reales en clase y comprobar que la simulación hace lo que debe.

Sus respuestas: simular en la misma carpeta del `.in`; la matriz, los vóxeles y los intervalos en el mismo diálogo en
que se eligen los resultados, con la matriz visible y movible en 2D y 3D y definible por el tamaño del vóxel; la
cascada al pulsar «Ejecutar» (en segundo plano, con una casilla para no hacerla); el visor en 3D sobre la geometría,
con animación en el tiempo, elección de lluvias y exportación para clase.

## Decisiones

### 1. Ejecutar en la carpeta del `.in` (`penhan/run.py`, `ui/run_dialog.py`)

* «Simulación → Ejecutar penhmain» (F9, botón «Ejecutar» al principio de la barra) abre un diálogo que dice antes
  qué va a hacer: qué penhmain (el elegido en «Programas de penhan» o el que ya está en la carpeta), qué ficheros
  faltan o se copian (los ENDF desde la base de datos de penhan, el `.nuc`…), qué resultados pide el `.in`, el
  trabajo (lluvias, tiempo, semillas, volcados: la misma sección que en el panel «Simulación», con deshacer), qué
  pasa con los resultados de una simulación anterior y si se simula también la cascada.
* El penhmain elegido se copia junto al `.in`; si allí hay otro distinto con el mismo nombre, se pregunta: usar el de
  la carpeta o sustituirlo (el de antes se guarda como «.bak»). Nunca se borra nada.
* Los resultados anteriores se guardan en una subcarpeta «anterior-AAAA-MM-DD_HH-MM-SS» (con una nota de ARGUS, que
  la ventana de resultados no toma por otra ejecución) o se dejan sobrescribir. Con `RESUME` y su volcado, penhmain
  sigue la simulación y no se pregunta.
* Con errores que pararían a penhmain (los de `validate_case`, salvo los `.mat` que faltan, que se generan desde el
  diálogo con penhmat) no se ejecuta, salvo que se marque «Ejecutar aunque ARGUS vea errores»; tampoco, sin
  confirmarlo, si penhmain parece estar simulando ya en la carpeta.
* Un guion (`argus-ejecutar.sh`, `.bat` o `.command`, marcado «Generado por ARGUS») ejecuta penhmain; se abre en un
  terminal (consola de Windows, con WSL si penhmain es de Linux; el terminal que haya en Linux; Terminal en macOS).
  Sin terminal, en segundo plano. Con el `.sh` y el `.command` (Linux, macOS, WSL) la salida en pantalla queda también
  en `penhmain.out`, con el código de salida; con el `.bat` de un `penhmain.exe`, solo en la consola.
* Después se abre la ventana de resultados en la carpeta, que la sigue (cada 2 s), y se recuerda la carpeta para «Ver
  los resultados de este caso».

### 2. La cascada de partículas (`penhan/cascade.py`, `results/shower.py`)

**Simulada con el penhmain del usuario.** penhmain guarda como mucho `NPSHOW = MIN(LTRACK, 50)` lluvias por
ejecución (está fijo en penhmain.f y no se toca). Para 1000 lluvias, ARGUS hace 20 ejecuciones de 50 en la carpeta
`cascada` junto al `.in` (`cascada-2`… para otra versión del caso): la primera con las semillas del caso (sus
trayectorias son, byte a byte, las de las primeras 50 lluvias de la simulación de verdad: comprobado con el penhmain
de Rafael) y las demás con los pares precalculados de RAND0 (`RSEED -k 0`, sin el par del caso). Si el penhmain del
usuario guarda más (compilado con otro NPSHOW), menos ejecuciones (una sola si guarda todas las pedidas): una
ejecución de prueba de una lluvia, con `LTRACK 99`, lo averigua la primera vez y se recuerda (por el sha1 del
ejecutable).

**Lo que se quita del `.in`.** La primera ejecución solo pierde lo del trabajo (`LTRACK`, `NSIMSH`, `TIME`,
`DUMPTO`, `DUMPP`, `RESUME`): la rejilla de dosis, `EPROF` y las edades de los detectores consumen números
aleatorios y quitarlos cambiaría las trayectorias (comprobado). Las demás no registran nada (más rápidas).

**En paralelo y sin estorbar.** Tantas ejecuciones a la vez como procesadores menos dos (y la memoria: unos 0,9 GB
por ejecución con protones o alfas). Se cancelan al cerrar ARGUS; la cascada a medias se reconoce por su firma (el
`.in`, la geometría, los `.mat`, penhmain y las lluvias) y se termina después en la misma carpeta, completando lo que
faltase (el índice `argus-cascada.json` se escribe antes que nada).

**La reconstrucción.** Los `.trk` no dicen a qué lluvia pertenece cada trayectoria ni qué partícula la creó. Toda
secundaria empieza exactamente en un vértice de su madre (el texto de PTRACK es el mismo): la madre es la trayectoria
con ese punto como vértice interior; las que no tienen madre son las primarias, en el orden del fichero. Los puntos
que repiten el anterior (penhmain escribe dos veces el de salida de una fuente puntual dentro de la materia) no son
vértices. El tiempo de vuelo de cada tramo sale de la velocidad de su energía (c para los fotones) y empieza en el de
la madre en el vértice donde nace.

### 3. El visor (`ui/results/shower_view.py`, `render/track_layer.py`, `ui/gpu_tracks.py`)

* «Cascada de partículas (3D)» en el árbol de la ventana de resultados (detrás del resumen): la geometría del caso
  translúcida (los gases, ocultos) y las trayectorias encima, en la GPU (líneas con grosor, tapadas por lo opaco con la
  profundidad que deja el trazado de rayos) o, sin GPU, con QPainter (lluvias enteras hasta los primeros 120 000
  tramos, y el rótulo dice cuáles).
* Lluvias: las primeras N o una sola (anterior/siguiente); partículas, con su número; color por partícula, energía,
  lluvia o generación, con su leyenda; grosor; cada material translúcido, opaco u oculto; encuadrar lo que se ve.
* Animación con el tiempo real de vuelo: reproducir, pausa, la barra de tiempo, velocidad, tiempo logarítmico (para
  ver a la vez lo rápido y los neutrones lentos), lluvias escalonadas, estela y las partículas en vuelo como puntos.
  El tiempo logarítmico se elige solo al abrir la cascada cuando, en lineal, casi todo pasaría en un instante: si el
  99 % de las trayectorias termina antes de la cuarta parte de la animación (con los protones del SOBP, antes de
  3,2 ns de 19 ns: unos pocos neutrones alargan el resto) o la mitad en el primer 2 % (fuentes de neutrones). Con
  electrones o fotones, en lineal.
* Exportar la imagen (PNG) y la animación en MP4 (Qt Multimedia: H.264 si lo hay, si no MPEG-4) o GIF (Pillow).
  La leyenda, la lluvia y el instante van en la imagen.
* Mientras se simula, las lluvias aparecen a medida que terminan las ejecuciones; también «Ver sobre la geometría»
  (en los cortes 2D) y «Exportar datos» (una fila por trayectoria).
* Al dibujar las líneas, el alfa de la imagen no cambia (si bajaba, al copiarla o exportarla se veían otros colores).

### 4. Resultados que pedir y la matriz de dosis (`ui/outputs_dialog.py`, `ui/sim_outputs.py`, vistas)

* «Elegir qué resultados pide el .in» ya no es modal: cada resultado (dosis, lo que entra en un cuerpo, energía
  depositada, detectores angulares, lo que sale, seguir la simulación, trayectorias) con su casilla y, marcado, su
  editor (el mismo del panel «Simulación»: la matriz de vóxeles, las ventanas de energía y los intervalos, los
  cuerpos). Marcar lo pide con valores razonables; desmarcar quita sus líneas; cada cambio, un paso de deshacer.
* La matriz por el tamaño del vóxel: Δx, Δy, Δz (Δr): el número de intervalos es L/Δ redondeado (impar en X e Y si
  se quiere, como recomienda penhmain) y el eje se ajusta para que el vóxel mida eso justo (los laterales con el mismo
  centro; la profundidad desde el mismo principio). Con el límite de penhmain (101, 101, 401) se dice el vóxel que
  queda. Se ve cuántos vóxeles son y lo que ocupará `3d-dose-map.dat`.
* Los vóxeles se ven en el corte (cuando miden al menos 4 píxeles) y en 3D (en las caras del fondo de la matriz),
  con su número y su tamaño en el rótulo (en 3D, también cada cuántas líneas se dibuja una). La matriz se estira por cada cara y se mueve con el ratón en 2D y en 3D: al estirarla,
  el vóxel no cambia (cambia el número de vóxeles); con Ctrl, cambia el vóxel.

### 5. Pantallas pequeñas (`ui/flow_layout.py`, `ui/screen_fit.py`)

Con la pantalla al 150 %, un portátil de 1920 × 1080 tiene 1280 × 720 puntos. La ventana de resultados no bajaba de
1524 puntos de ancho (la fila de opciones del mapa sumaba 936 y la cascada 959), la principal de 1327 (los botones de
«Materiales») y varias ventanas y diálogos se abrían más altos que la pantalla (1400 × 880, 820 × 820), con los botones
fuera.

* Las filas de controles de las vistas de resultados (mapas, curvas, trayectorias, espacio de fases, la cascada) y los
  botones de «Materiales» van en un `FlowLayout`: se usa como `QHBoxLayout` (estiramientos, espacios, controles que se
  expanden) pero lo que no cabe sigue en la línea de abajo; una etiqueta y su campo van juntos (`group`). En una
  pantalla grande se ven igual que antes, en una línea.
* El tamaño inicial de las ventanas y los diálogos (`fit_to_screen`) no pasa del 92 % del sitio libre de su pantalla
  (sin la barra de tareas); la ventana principal recuerda después el tamaño que le dé el usuario.
* Con la letra de las pruebas (más ancha que la de Windows), la ventana de resultados baja a unos 1045 puntos (700 en
  la cascada) y la principal a 956. `test_small_screen.py` lo comprueba con una pantalla de 1280 × 680 simulada; las
  pruebas sin pantalla usan una de 1920 × 1080.

### 6. Revisión 0.13.1

Una revisión de todo lo de la 0.13.0 con el penhmain de Rafael encontró fallos en cada parte; todos están arreglados,
cada uno con su prueba (`test_*_review6.py`, `test_matrix_review6.py`, `test_texts_review6.py`).

**La cascada.**
* Semillas: el par precalculado 1 de RAND0 son las semillas por defecto (1, 1): con un caso sin `RSEED`, la segunda
  ejecución repetía las lluvias de la primera. Las demás ejecuciones usan pares distintos del del caso. El índice pasa
  a la versión 2; una cascada de la 0.13.0 con lluvias repetidas se marca para simularla otra vez.
* La sonda de NPSHOW va con `LTRACK 99` (penhmain lo escribe con I2: «**» desde 100) y los valores aprendidos con la
  0.13.0 no se usan.
* La primera ejecución es la simulación misma también con los nombres de salida del `.in` (`IDSPC`, `IDPSF`…: se
  escriben en la carpeta de la ejecución) y con ficheros en carpetas de arriba (`../mats/agua.mat`).
* Una ejecución que no arranca o muere se dice (código de salida, la salida de penhmain); las que siguen en marcha de
  otra sesión de ARGUS se esperan; cancelar no bloquea la ventana; dos `.in` a la vez no comparten carpeta; lo que
  falta se dice antes de la ejecución de prueba.
* Espacio de fases (`IPSFN`): cada ejecución lee su trozo (`argus-psf.dat`, las lluvias de esa ejecución con su
  número), no el fichero entero cada vez.
* Fuentes radiactivas (`SRNUCL`) y partículas divididas del espacio de fases: penhmain las saca de su pila sin
  `PTRACK(…, 2)`, así que en el `.trk` van pegadas a la trayectoria anterior. ARGUS las separa (donde sube la energía,
  donde un fotón vuelve a un punto, en los puntos de la fuente y, en el vacío, en la recta desde la fuente) y las
  reparte por lluvias con el orden de las desintegraciones; también lee los `fort.7x` que penhmain escribe sin abrir.
  Comprobado con un penhmain instrumentado (solo para la prueba, fuera de ARGUS): todas las trayectorias y
  generaciones; las lluvias, todas en la materia y con un espacio de fases, y con la fuente en el vacío un 86-90 %,
  con las demás marcadas como dudosas.
* Limitación que quedaba (resuelta en 0.13.2, apartado 7): la vista «Trayectorias» de la simulación (no la cascada)
  enseñaba los `.trk` tal como son, sin separar esos trozos; y las lluvias dudosas.

**El visor.** Las partículas desmarcadas no pasan a otro caso; los números de lluvia no cambian mientras se simula;
sin GPU, el límite de tramos va por lluvias enteras y el rótulo dice cuáles caben; si la GPU no acepta el programa de
las trayectorias, se dibujan sin ella y se dice; la animación de una lluvia dura lo que ella; el GIF dura lo pedido; no
se vuelve a leer mientras se exporta; se pregunta antes de sobrescribir al añadir la extensión; textos y plurales.

**Ejecutar.** La consola nueva de Windows recibe la salida de penhmain y espera al final; un `RESUME` con el volcado
vacío («Empty.») no se toma por una simulación que seguir; cancelar o fallar la copia no ejecuta; el `dump.dmp` por
defecto se guarda con los resultados anteriores; la cascada usa el penhmain que se ha ejecutado; el `.bat` va en UTF-8
con `chcp 65001` y escapa `&`, `^`, `%`; «ya está simulando» mira el periodo de `DUMPP`; con conflicto se comprueba
el penhmain que se va a usar antes de mover nada; un `.in` cambiado en el disco no se ejecuta sin recargarlo.

**La matriz y los resultados que se piden.**
* «Deshacer» del diálogo es el de la ventana (con la geometría que sigue al `.in`); arrastrar no deja un eje sin
  longitud ni pisa un valor tecleado sin Intro; Esc y los movimientos sin botón en 3D; en las vistas de los ejes, las
  caras que se ven de frente no tapan el rombo de mover; la rejilla por defecto y «Ajustar a la geometría» empiezan en
  la superficie (antes, medio paso de rayo, un 1 %, por fuera).
* `RESUME`: al reanudar, penhmain toma del volcado la rejilla de dosis y los intervalos de los resultados (HENANR,
  IMDETR, ANDETR, ENDETR, DOSER) y no los del `.in`. ARGUS lee también esa parte del volcado y avisa cuando no
  coinciden (la comprobación del `.in`, «Ejecutar», la tarjeta «Dosis» y el diálogo de resultados).
* Avisos que faltaban: un detector angular con la ventana de energía vacía (penhmain no la comprueba y el espectro
  sale vacío), `IDAGEL` con más de 1000 intervalos (penhmain escribe fuera de su tabla), «Energía depositada» con
  forzado (error 3095). Los editores marcan en rojo una ventana vacía y los intervalos de más.
* «Seguir la simulación» dice lo que hará penhmain (no baja de 30 s ni pasa de un día; con 0 o negativo, cada 30 s;
  con 1e10 o más, nada) y desmarcarlo deshace lo que hizo marcarlo; las trayectorias dicen que penhmain guarda 50.

**Textos.** Coma decimal en los mensajes, millares como la RAE, «2e9» en vez de «2e+09», plurales de verdad en vez de
«fichero(s)», siglas que no pasan a minúsculas («datos ENDF», «TAC») y teclas de los menús sin repetir.

Sin probar aquí (no hay Windows ni una GPU Intel): la consola de Windows y WSL de verdad, y el fallo del programa de
las trayectorias en una GPU que no lo acepta (simulado en las pruebas).

### 7. La lluvia de cada trayectoria, exacta (0.13.2)

Rafael (08-10-2026): con la cascada lo que se hace es arrancar su penhmain sobre su caso y seguir las primeras
lluvias; lo importante es que el seguimiento sea el real y lo más exacto posible, con su penhmain, para cualquier
simulación y geometría. «Dudosas», no.

**Por qué había dudosas.** Las trayectorias siempre fueron las de penhmain; lo que no está en los `.trk` es de qué
lluvia es cada una. Casi siempre se sabe con certeza (las secundarias salen de un vértice de su madre; la partícula de
la fuente que empieza cada lluvia se reconoce), pero con una fuente radiactiva puntual las demás partículas de cada
desintegración salen de la pila sin trayectoria propia y todas del mismo punto: de un fotón que no deja descendientes
nada dice de qué desintegración es. ARGUS lo repartía «por las energías» y lo marcaba como dudoso.

**Decisión: no adivinar nunca.** `results/shower.py` acota, para cada trayectoria, de qué lluvias puede ser solo con
lo que es seguro (orden de cada fichero: una lluvia entera antes de la siguiente; la primera partícula de cada lluvia
es lo primero que escribe penhmain de ella; madres; lluvias distintas no comparten número). Si queda más de una,
`shower = -1` y `needed` dice qué cortes hacen falta. Si algo no cuadra, solo el orden y los cortes (y todos).

**Los cortes** (`penhan/cascade.py`). La ejecución se repite con el mismo penhmain, el mismo `.in` y las mismas
semillas, parando tras `b` lluvias (`NSIMSH = b`; con un espacio de fases, el N de penhmain al acabar su lluvia `b`).
penhmain es determinista: los `.trk` del corte son el principio, byte a byte, de los de la ejecución entera (se
comprueba en cada corte; si no, se dice). Su tamaño dice dónde acaba la lluvia `b` en el fichero de cada tipo de
partícula, y ahí se separa también lo que estuviera pegado. Cada corte va en una carpeta temporal dentro de la de la
cascada (enlaces duros a los ficheros de la ejecución; se borra al terminar, y las que se queden de una sesión cerrada
de golpe, al seguir); lo que dice, en `argus-cortes.json` de la ejecución (vale mientras sus `.trk` no cambien). Se
hacen solo los que hacen falta, en paralelo con las ejecuciones, primero los de las primeras. Una ejecución cuenta
como terminada cuando se sabe la lluvia de todas sus trayectorias: el visor no enseña nada dudoso.

**Alternativas descartadas.** `RESUME` encadenado (penhmain desactiva LTRACK al reanudar: `IF(N.NE.0)
LTRACK=.FALSE.`). Volcados periódicos (`DUMPP` no baja de 30 s). Un espacio de fases de lo que entra en la
geometría (no sirve con la fuente dentro de la materia, limita los cuerpos y los detectores). Una fuente extensa
diminuta en vez de la puntual (no serían las lluvias del caso). Una lluvia por ejecución (lo mismo que los cortes,
sin las primeras lluvias de la simulación de verdad).

**Coste.** Un corte cuesta lo que tarda penhmain en arrancar (unos 3,5 s con dos materiales y Co-60; con datos
nucleares, minutos). Haces, fuentes extensas en la materia y espacios de fases no necesitan ninguno. Una fuente
radiactiva puntual, casi uno por lluvia (43–49 por ejecución de 50).

**Otras correcciones de la reconstrucción.** Las copias de `IPSPLI` de un espacio de fases no van pegadas
(`VSPLIT`: `ILB(1)+1`, penhmain las trata como secundarias y empiezan su trayectoria): sin reglas de separar para el
espacio de fases. Un fotón que crea una partícula pierde energía en ese punto (si no, es otro fotón de la fuente
pegado que empieza ahí). Un vértice en el punto de un registro del espacio de fases es un vértice (la vista dejaba
huérfano el electrón Compton de un fotón que interaccionaba en menos de 1e-5 cm). Con una fuente extensa fuera de la
materia, un fotón que entra en línea recta desde donde empieza una primaria es otro de la desintegración. La vista
«Trayectorias» de una simulación con fuente radiactiva separa también lo pegado (y lee los `fort.7x`).

**Comprobado** con un penhmain instrumentado (fuera de ARGUS; escribe la lluvia y la generación de cada trayectoria y
cada partícula de la pila en la suya): Co-60 puntual en la materia y en el vacío, Co-60 extensa en la materia y en el
vacío, I-125 puntual, espacio de fases con y sin `IPSPLI` (también forzando todos los cortes), haces de electrones,
de fotones (extensa) y protones de 200 MeV: 11 574 trayectorias, todas en su lluvia, con su generación y sin tramos
que no recorrió nadie. Y con el penhmain de Rafael, que los cortes son el principio exacto de la ejecución entera.

## Consecuencias

* ARGUS lanza penhmain (y la cascada) pero no lo modifica ni lo compila: si el usuario cambia NPSHOW y lo compila,
  la cascada lo aprovecha.
* Las carpetas `cascada*` y `anterior-*` son de ARGUS y se quedan en la carpeta del caso: el usuario decide si las
  borra.
* Pruebas: `test_run.py`, `test_run_ui.py`, `test_cascade.py`, `test_track_layer.py`, `test_shower_view.py`,
  `test_dose_grid_ui.py`, `test_goals.py`, `test_flow_layout.py` y `test_small_screen.py`, y las de la revisión
  0.13.1 (`test_run_review6.py`, `test_cascade_review6.py`, `test_shower_view_review6.py`,
  `test_outputs_review6.py`, `test_matrix_review6.py`, `test_texts_review6.py`) y de la lluvia exacta
  (`test_cascade_cuts.py`), con penhmain de mentira y, con `ARGUS_PENHMAIN`, con el de verdad.
