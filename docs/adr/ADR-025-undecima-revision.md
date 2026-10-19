# ADR-025 · Undécima revisión: PENGEOM, la cascada y los vóxeles (0.17.3)

**Estado:** aceptada (versión 0.17.3)

## Contexto

Rafael pidió (19-10-2026) otra iteración de búsqueda de fallos, centrada en PENGEOM, la cascada («shower») y los
vóxeles. Como en la décima, se dieron entradas al azar y se compararon las respuestas con PENGEOM, PMRDR y penhmain
reales. Cuatro frentes:

* **PENGEOM** (oráculos y editor):
  * 1900 geometrías al azar, 27,7 millones de puntos de LOCATE y 717 000 rayos de STEP frente a la sonda;
  * 107 escenas con sus cajas exactas;
  * 251 cadenas del editor y 185 de la pestaña «Geometrías complejas» (2550 operaciones);
* **la cascada** (ejecuciones, cortes, reconstrucción y visor):
  * 23 cascadas de 1 a 1000 lluvias, con 24 065 trayectorias comparadas con la verdad (un penhmain instrumentado,
    solo en la nube): haces, fuentes radiactivas y espacios de fases;
  * 22 tipos de `.trk` dañados, interrupciones y copias del caso;
  * el visor, 630 pasos con GPU y sin ella;
* **los vóxeles del TAC**:
  * 1366 series importadas, con el vóxel, el material y la densidad de 4,1 millones de puntos comparados con
    CTLOCATE;
  * 1700 `.ct` mutados, 1860 imágenes de la GPU frente a la CPU y 3150 particiones de tejidos;
  * una prueba de mono del diálogo (20 000 acciones);
* **los vóxeles de la matriz de dosis**:
  * 1020 pasos del editor (951 comprobados con PMRDR) y 896 `.in` con GRID escritos a mano;
  * 488 arrastres en 2D y 413 en 3D;
  * 51 rejillas simuladas con penhmain real y 20 carpetas de resultados.

Salieron 33 fallos de ARGUS y uno de PENGEOM. Cada uno de ARGUS se reprodujo antes de corregirlo y tiene su prueba, que
falla con el código de 0.17.2.

## Decisiones

### 1. PENGEOM: la caja exacta (5)

* **La caja de un cuerpo o de un módulo** (`extent.element_extent`). La usan «Envolver en un módulo», «Duplicar»,
  «Pegar», mover y la fuente extensa. Salía de rayos en una rejilla de 40 × 40 y, en los ejes sin cota, de la caja de
  la escena, y lo fino e inclinado se le escapaba:
  * una lámina de 50 µm girada 80° quedaba 0,37 cm corta. «Envolver» proponía un módulo menor y su comprobación, que
    muestreaba en la misma caja, lo daba por bueno: lo que asomaba (el 11 % de la lámina) pasaba a agua, también en
    PENGEOM real;
  * un cubo de 2 cm girado, solo, perdía sus esquinas (el 2,3 %).

  Ahora la caja sale de las ecuaciones (`pengeom/support.py`). El máximo de una coordenada en la región de unas
  cuádricas está en un punto de Karush-Kuhn-Tucker: en una superficie, en la curva de dos o donde se cortan tres. Con
  planos se calcula con fórmulas; con cuádricas curvas, resolviendo todos los cortes de tres cuádricas por homotopía
  (en la curva de dos, los puntos buscados están en otra cuádrica). La caja de un cuerpo es la de sus superficies
  cortada con la de sus madres: lo que LOCATE puede situar en él. Los rayos solo quedan para los ejes sin cota.
* **«Fuente extensa en» una lámina de 0,9 µm casi horizontal.** La caja SBOX dejaba fuera el 64 % (penhmain no
  emitiría de ahí) y la nota decía «no tiene límite en algún eje». Ahora es la caja exacta.
* **La caja de la escena** (`bounds.estimate_bounds`, con LOCATE en una rejilla) dejaba fuera lo fino y las esquinas
  giradas. En la copa de PenGeomJar faltaban el tallo y el pie (z = −0,39 en vez de −6,8): el deslizador del corte no
  llegaba al pie y «Nuevo .in» ponía la fuente dentro del tallo. Ahora se une con las cajas exactas de los elementos
  del nivel superior.
* **«Duplicar»** rechazaba los cuerpos girados («no es un volumen cerrado»: la caja de cada superficie de un cilindro
  girado es infinita). En cambio, dejaba duplicar un módulo que sus superficies no cierran, y las copias podían quedar
  encima de otras cosas, fuera de lo que se comprobaba. Ahora usa la caja exacta, y un módulo abierto se rechaza («no
  es un módulo cerrado»).
* **La vista 3D con un módulo raíz** (sin envolvente) y una hija que asoma de él. El trazador (CPU y GPU) entraba desde
  fuera en lo que asoma y lo dibujaba. STEP no entra: desde fuera, solo si el punto está dentro de las superficies de
  la raíz («The particle enters from outside the enclosure»). Ahora el trazador hace lo mismo. Desde dentro de lo que
  asoma, LOCATE sigue diciendo que es la hija, como en PENGEOM.

### 2. La cascada (11)

* **Con un espacio de fases, las lluvias son los grupos del fichero.** penhmain cuenta en N la suma de los NSHI de
  cada grupo; en el espacio de fases de un detector de impacto, lo normal es NSHI > 1. Cada ejecución pedía sus
  lluvias en ese N, mientras el visor numeraba grupos:
  * 120 pedidas eran 103 (y el estado decía 120), con lluvias vacías al final de cada ejecución;
  * con el espacio de fases del caso 3 de Rafael, el visor decía «de 955», con 741 vacías.

  Ahora cada lluvia es un grupo, lo que numera el visor. Cada ejecución pide los grupos que penhmain sigue en sus
  `.trk`: los siguientes mientras N, que sube con sus NSHI, no pase de 50, y como mucho 50; NSIMSH es ese N. Hay más
  ejecuciones que antes.
* **Cada ejecución lee su trozo, también la primera.** La primera leía el fichero del caso entero. Con la ruleta rusa
  (WGTWIN; penhmain pone 0,5 y 1,5 aunque el `.in` no lo diga), al matar la partícula de su última lluvia penhmain
  sigue leyendo en la misma lluvia y se pasa de NSIMSH (comprobado: N = 57 con NSIMSH = 50). Esas trayectorias iban a
  la última lluvia como exactas, y la partícula se simulaba otra vez en la segunda ejecución. Ahora cada ejecución y
  cada corte leen un trozo (`argus-psf.dat`) que acaba en su última lluvia: al acabarse, penhmain para (JOBEND = 4) y
  no puede pasarse.
* **Partículas que salen igual.** Con un espacio de fases de partículas que salen del mismo punto con la misma energía
  (escrito a mano desde una fuente puntual, o los fotones de un haz fino que llegan sin chocar), cada primaria se
  quedaba con la primera partícula del fichero. Todas sus trayectorias iban a la lluvia 1, como exactas. Ahora, si
  puede haber ruleta rusa o copias (IPSPLI), no se da por sabida (lo dicen los cortes); si no, cada una es la
  siguiente del fichero.
* **Las líneas del espacio de fases, como las lee RDPSF:** 136 caracteres (`CHARACTER*136`), y la que así no tiene
  sus 14 valores, la salta sin avisar. La cascada las leía enteras, y la comprobación del `.in` no lo sabía: con líneas
  más largas (más cifras de las que escribe penhmain), penhmain no lee ninguna partícula y se para (3117), y ARGUS no
  decía nada. Ahora la cascada las lee como penhmain, y la comprobación da el 3117 o, si solo algunas pasan de 136
  caracteres, avisa.
* **Sin GPU, las trayectorias se dibujaban en cada franja** del trazado progresivo (cada ~40 ms). Con 1000 lluvias
  eran entre 28 y 115 s con la ventana a tirones, e «Imagen…» guardaba a medias a los 30 s. Pasaba lo mismo en la
  ventana principal con «Ver sobre la geometría» (la oclusión, 53 veces). Ahora se dibujan una vez por cámara, tamaño,
  estilo y lo que se ve, y se reutilizan.
* **«Ver sobre la geometría» con muchas lluvias.** El límite de puntos se repartía por tipo de partícula: con 1000
  lluvias, fotones de casi todas y electrones de las 46 primeras, y la barra no lo decía. Ahora van las primeras
  lluvias enteras que caben (40 000 puntos) y la barra dice cuáles.
* **Un punto NaN o de 1e30 cm en un `.trk`.** El origen de la capa era NaN o 5e29, así que la cascada entera
  desaparecía o quedaba aplastada, y el tiempo máximo era de 1,7e29 ns. Ahora esos puntos (no finitos o a más de
  1e10 cm) no cuentan ni se dibujan sus tramos, y el visor lo dice.
* **Cortes huérfanos.** Si ARGUS se cerraba de golpe durante un corte, su carpeta (`argus-corte-…`, con sus `.trk` y su
  volcado) se quedaba para siempre: una cascada terminada ya no las miraba. Ahora se borran también con la cascada
  terminada, salvo las de un corte que sigue vivo.
* **La firma del caso usaba la fecha y el tamaño** de los ficheros. Al copiar la carpeta sin las fechas (`cp -r`, git,
  un pendrive FAT32), la cascada era «de otro caso» y «Simular…» la repetía entera. Ahora es el contenido (sha1; en
  ficheros de más de 512 MB, muestras), guardado por tamaño y fecha para no releerlo. Las firmas de antes se siguen
  reconociendo.
* **Textos y cascadas sin trayectorias.**
  * Textos: «1 de 1 lluvias simuladas», «cascada (1 lluvias…)», «17523» sin separar los millares y «lluvias 51?100» en
    la cabecera del trozo (el guion largo no cabe en latin-1).
  * Una cascada terminada sin ningún `.trk` (borrados, o un penhmain que no escribe las de LTRACK) se daba por «5 de 5
    lluvias simuladas», y «Simular la cascada» respondía «ya simulada». Ahora esas ejecuciones no cuentan, el visor lo
    dice («ningún .trk») y «Simular…» las repite.

### 3. Los vóxeles del TAC (9)

* **El corte de la vista 3D en el plano de una hija** (la cara del CTBOX, o los planos redondos que da el deslizador).
  Cada píxel decidía por redondeo entre «cara del corte» y «superficie»: 6243 fronteras falsas frente a 1427. Ahora
  hay una tolerancia relativa de 1e-9, en la CPU y en la GPU.
* **Un corte en un plano entre vóxeles**, visto desde el lado que queda, con el aire oculto. Unos píxeles enseñaban el
  vóxel de delante (que el corte quita) y otros no, y la CPU y la GPU no coincidían. Se corrige con la misma
  tolerancia.
* **«Importar TAC» admitía un recorte de ±1 km.** Con un vóxel de 0,01 mm salían 300 millones de vóxeles (unos 46
  bytes cada uno al importar) y ARGUS se quedaba sin memoria. Ahora el recorte llega como mucho al TAC más su tamaño a
  cada lado, y si lo pedido no cabe en la memoria libre, se dice antes de empezar.
* **Micro-TAC.** Con cortes a 0,5 µm, todos eran «cortes repetidos» (el umbral era 1 µm). Además, el vóxel del diálogo
  no bajaba de 0,01 mm y tenía tres decimales: «Como el TAC» ponía 0,01 sin avisar. Ahora son repetidos los cortes a
  menos de 1 µm o de la centésima del píxel, y el vóxel baja hasta la décima del de la serie, con sus cifras.
* **`CTFNAME=(` y `FILE=(` con un tabulador, un espacio duro o `\v`.** GEOMIN solo quita los espacios (y OPEN, los del
  final), así que ese fichero no existe (2106). ARGUS los quitaba y enseñaba el CT. Ahora hace como GEOMIN, y el mensaje
  enseña lo que no se ve («\t», «\xa0»).
* **Un `.ct` con `\f` o `\v`.** La lectura rápida (NumPy) los tomaba por blancos; gfortran no («Bad integer/real»), así
  que PENGEOM no lo leería. Ahora va a la lectura lenta, que lo dice.
* **Un material fuera de INTEGER*4 en el `.ct`.** Al abrir la geometría salía «Python integer 2147483648 out of bounds
  for int32», sin fichero ni línea. Ahora es un error de formato, con su vóxel.
* **Un `.ct` válido con una coma entre MC y DC, o con una línea de más al final**, iba por la lectura lenta: 10–16 s
  por millón de vóxeles, en el hilo de la ventana. Ahora lo lee la rápida. Las comas que hacen valores nulos siguen
  yendo a la lenta.
* **El vóxel de un punto en un plano entre vóxeles con la dirección paralela** (U = 0). ARGUS calculaba 0,3/0,1 =
  2,999…, mientras PENGEOM calcula 0,3·10 = 3. Se veía en los cortes 2D (en las fronteras) y en la barra de estado.
  Ahora se usa la aritmética de CTLOCATE (SHPRD término a término, X·RDX y las tolerancias de las caras): 0
  diferencias en 409 640 puntos.

### 4. Los vóxeles de la matriz de dosis (8)

* **Un vóxel diminuto con «impar»** (lo de siempre). Un vóxel más de 1010 veces menor que el eje dejaba la caja en
  101·Δ alrededor del centro, sin avisar: en una caja de 10 cm, Δ = 0,0098 cm la dejaba en 0,99 cm. Ahora se hace como
  con cualquier otro «demasiados intervalos»: los extremos se quedan, con 101 intervalos, y se dice.
* **Estirar con el ratón una rejilla de vóxeles de menos de 1e-6 cm hasta un vóxel.** El eje medía menos de 1e-6 cm
  (penhmain: 3102, 3104 o 3106), así que `set_dose_grid` lo rechazaba a mitad del arrastre y salía el aviso de «error
  inesperado» en cada movimiento. Ahora el arrastre deja los vóxeles que hacen falta y, si aun así no vale, lo dice en
  la barra de estado.
* **RESUME de un volcado con otra rejilla.** penhmain sigue con la rejilla del volcado, pero los cortes y la vista 3D
  dibujaban (y dejaban estirar) la del `.in`. Ahora dibujan la del volcado, sin asas, y lo dicen.
* **Curvas de un solo intervalo** («GRIDZ 2 4 1»). Con un solo centro (3 cm), la curva se dibujaba de 1,5 a 4,5 cm, con
  una anchura inventada. Ahora los bordes salen de la rejilla de la misma carpeta (`gnu-dose.dat` o la cabecera de
  `2d-dose-map.dat`); sin ella, se dibuja un punto.
* **Mover la rejilla en 3D, en perspectiva,** iba a la escala del objetivo de la cámara: con el objetivo 30 cm detrás de
  la rejilla, 2,4 veces más deprisa que el ratón. Ahora va a la de la rejilla.
* **«Elegir resultados…»** descartaba sin decir nada un cambio del editor de la dosis que no se puede escribir. Ahora
  lo dice en su tarjeta, como el panel «Simulación».
* **El campo del vóxel.** Con un eje que ya tiene todos los intervalos que caben, escribir un vóxel menor no cambia la
  rejilla, y el campo se quedaba con lo escrito. Ahora dice el vóxel que hay.
* **«cilindro, 3200 anillos»** con 40 anillos × 80 capas: son celdas («cilindro, 40 × 80 = 3200 celdas»).

## Un fallo de PENGEOM (no es de ARGUS)

CTLOCATE (pengeom.f 3847-3960), con vóxeles de 1 cm o más. Un punto a ~1e-12 de una cara «alta» de la caja CT (x = XM)
puede salir con IX = NX + 1: lee MC y DC del vóxel siguiente en memoria, el primero de la fila de al lado o, en la
última fila, uno de fuera de la matriz.

La tolerancia de las caras, TT = MAX(EPS, EPS·DX), está en cm; la de los planos entre vóxeles, EPS, en vóxeles. Con
DX ≥ 1 son la misma, y el redondeo de X·RDX decide. Si el punto no cuenta como «en la cara» pero sí como «en el plano
NX», y U > 0, se lleva a (NX + 2·EPS)·DX, fuera de la caja. Se ha reproducido con la sonda: IX = 11 con NX = 10.

Un arreglo posible: `IX = MIN(IX, NX)` (y lo mismo en y y z) antes de calcular IA. ARGUS usa el último vóxel. Es para
los autores de penhan: no se toca pengeom.f.

## Datos de penhan y PENGEOM verificados en esta revisión

* Con un espacio de fases, penhmain cuenta en N la suma de los NSHI de cada grupo.
* Con la ruleta rusa, cuando SHOWER mata una partícula, sigue leyendo en la misma lluvia (`GO TO 101`) y puede pasarse
  de NSIMSH. Al acabarse el fichero, JOBEND = 4: para sin error.
* WGTWIN vale 0,5 y 1,5 aunque el `.in` no lo tenga (PMRDR0), e IPSPLI vale 1.
* RDPSF lee 136 caracteres de cada línea (`CHARACTER*136`). La que así no tiene sus 14 valores, la salta sin avisar;
  si no queda ninguna, PMRDR da 3117.
* STEP, desde fuera, solo entra por las superficies de la raíz; LOCATE no las mira.
* CTLOCATE: ver el apartado anterior.

## Lo que queda

* Lo que quedaba en ADR-024, igual.
* La caja exacta de un módulo muy complejo tarda la primera vez unos 2–3 s en `spheres` o en la `mlc` (antes, con
  rayos, 0,5 s). Después queda guardada.
* En una geometría con solapes, que PENGEOM no permite, la caja de un cuerpo es la de lo que LOCATE puede situar en él.
  Es mayor que lo que llega a situar: no deja nada fuera.
* El fallo de CTLOCATE es de PENGEOM.

## Verificación

* 66 pruebas nuevas en seis ficheros:

  | Fichero | Pruebas |
  |---|---|
  | `tests/test_review11_pengeom.py` | 10 |
  | `tests/test_review11_corpus.py` (la copa de PenGeomJar) | 1 |
  | `tests/test_review11_cascade.py` | 17 |
  | `tests/test_review11_ct.py` | 19 |
  | `tests/test_review11_dose.py` | 8 |
  | `tests/test_gui_review11.py` | 11 |

  Cubren:
  * las cajas exactas de regiones conocidas y de regiones al azar (frente a Monte Carlo);
  * la lámina girada, el cubo, la lámina de la fuente, la copa, duplicar un cuerpo girado y un módulo abierto;
  * los rayos que no entran en lo que asoma de la raíz, en la CPU y en la GPU;
  * en la cascada, los grupos del espacio de fases, el trozo de cada ejecución, la ventana de pesos, los 136
    caracteres (también en la comprobación del `.in`, frente a PMRDR), las partículas que salen igual, la firma por el
    contenido, los puntos dañados, los cortes huérfanos y las ejecuciones sin `.trk`;
  * en el TAC, el corte en una cara y en un plano entre vóxeles (CPU y GPU), los nombres con blancos raros, `\f` y
    `\v`, INTEGER*4, la lectura rápida y el vóxel de CTLOCATE frente a la sonda;
  * en la matriz, el vóxel diminuto, el arrastre, las curvas de un intervalo y las celdas;
  * en la ventana, el dibujo sin GPU, «Ver sobre la geometría», los textos, el arrastre que no se acepta, RESUME, mover
    en 3D, la tarjeta de «Elegir resultados…», el campo del vóxel, el recorte y la memoria del TAC y el micro-TAC.
* Cada prueba nueva falla con el código de 0.17.2, salvo siete controles: lo que debe seguir igual.
* Pruebas reescritas:
  * dos de `test_cascade_review6.py`, por los grupos del espacio de fases;
  * `test_review016.py`, por las tres firmas;
  * `test_extended_source.py`, porque la caja de un cuerpo es la de lo que LOCATE puede situar en él (más muestras);
  * `test_goals.py`, por las celdas.
* Al repetir la búsqueda con las correcciones, no sale ningún fallo:
  * los guiones de cada hallazgo;
  * la cascada frente a la verdad en nueve casos: ocho de espacio de fases (con y sin ruleta, con copias, dos
    ficheros, el caso 3 de Rafael y partículas del mismo punto) y uno de fuente radiactiva. Son 2523 trayectorias,
    todas en su lluvia y con su generación (111 cortes);
  * un décimo caso, con líneas de 157 caracteres: penhmain no lee el fichero (3117), y ahora ARGUS lo dice antes de
    simular.
* La batería completa de pruebas y ruff; con la GPU (Mesa), las de la vista 3D, las trayectorias y el visor de la
  cascada.

## Consecuencias

* Las cajas de los cuerpos y de los módulos ya no dependen de que un rayo o un punto al azar dé con lo fino: salen de
  las superficies. Las usan envolver, duplicar, pegar, la fuente extensa y la escena.
* Con un espacio de fases, la cascada tiene las lluvias que se piden, ninguna vacía y cada trayectoria en la suya. Las
  cascadas de un espacio de fases hechas con 0.17.2 o antes pedían otras lluvias: se dicen de antes y se vuelven a
  simular.
* Lo que ve ARGUS de un TAC (el vóxel, el `.ct`, los nombres) es lo que ve PENGEOM, también en los casos límite.
* La matriz de dosis que se dibuja es la que usará penhmain.
