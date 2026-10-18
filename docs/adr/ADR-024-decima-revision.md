# ADR-024 · Décima revisión: búsqueda aleatoria de fallos (0.17.2)

**Estado:** aceptada (versión 0.17.2)

## Contexto

Rafael pidió (18-10-2026) una tanda de búsqueda aleatoria de fallos antes de subir los ejecutables nuevos. Esta vez no
se leyó el código por partes, sino que se le dieron entradas al azar y se compararon las respuestas. Cuatro frentes:

* **oráculos**, con PENGEOM y PMRDR reales (las sondas):
  * 1407 `.geo` mutados;
  * 1297 `.in` generados de 64 casos (cascada, paralelo, campañas y objetivos);
  * `fuzz_in_reader` (1214 casos);
  * 70 cadenas del editor al azar, con eliminaciones;
* **ficheros dañados y entradas raras**, en el núcleo y abiertos en la ventana:
  * unos 16 000 resultados de penhmain mutados;
  * volcados, espectros, bibliotecas de materiales, series DICOM, `.in` y `.geo`, carpetas de lluvias y tablas
    medidas;
* **una prueba de mono** de la ventana:
  * la de antes con dos semillas (127 y 64 casos);
  * una extendida con lo nuevo (vista 3D, el desplegable del material, «Trabajo», «Elegir resultados…» y «Ejecutar»
    con «Lluvias», sin la carpeta de penhan): 112 casos con cuatro semillas;
* **la navegación 3D nueva** (0.17.1), con 40 000 cámaras y puntos al azar.

Salieron 21 fallos. Cada uno se reprodujo antes de corregirlo y tiene su prueba, que falla con el código de 0.17.1.

## Decisiones

### 1. PENGEOM frente a GEOMIN (4)

* **Sin la línea END.** Si el fichero se acaba sin END, ARGUS ponía «Fin de fichero sin línea END» en la última línea.
  Así callaba el error que GEOMIN da antes en el último elemento (2004, 2021, 2024, 2036 o 2039, en su línea). Ahora
  dice también ese error, con su código, y el de END cuenta como el último (es lo último que lee GEOMIN).
* **Un parámetro que no se reconoce y pide un PARINP que el `.in` no define.** GEOMIN compara ICHPAR con NPINP antes
  de mirar la palabra clave. Por eso, en lugar de «palabra desconocida», da otro código:

  | Dónde | Código |
  |---|---|
  | superficie reducida | 2008 |
  | superficie implícita | 2013 |
  | transformación de una implícita | 2015 |
  | transformación de un módulo | 2049 |
  | transformación de un CLONE | 2058 |
  | transformación del TAC | 2111 |

  ARGUS sigue diciendo qué está mal escrito y añade, en la misma línea, el error que daría PENGEOM. Una línea `1111…`
  dentro de una superficie reducida se lee como un parámetro con ICHPAR 1111; ahora se explica.
* **Una referencia a un cuerpo que no existe, en el primer elemento.** GEOMIN solo busca las etiquetas si ya hay otros
  elementos (`IF(NBODYS.GT.1)`). En el primero no lo comprueba y sigue con datos que no existen. ARGUS lo sigue
  rechazando, pero ya no con un código de PENGEOM: dice que PENGEOM no lo comprueba.
* **Eliminar lo que deja un solo módulo arriba.** Sin otro elemento en el nivel superior, PENGEOM no añade la
  envolvente: ese módulo es toda la geometría. Una hija que asoma de él ocupa también lo de fuera: puntos lejanos
  pasaban de vacío a material, en ARGUS y en PENGEOM real. Ahora «Eliminar» lo comprueba con 20 000 puntos alrededor
  del módulo. Si alguna hija asoma, no elimina nada y dice cómo arreglarlo: corregir esa hija o envolver lo que queda.

### 2. Ficheros dañados y entradas raras (12)

* **Fórmulas con letras o cifras que no son ASCII** («H2Ó», «ÑaCl», «Ｈ２Ｏ», «Ⅻ», «²»). Daban AttributeError al
  escribir en «Composición propia». Ahora dicen qué carácter no se entiende y dónde. Con más de 50 paréntesis anidados:
  «demasiados paréntesis anidados» (con más de mil era un RecursionError).
* **Cabeceras de mapas sin sentido** (NBR = 0 o −3, XL = −5E+999, RU = 5E+999, NBX = 1E+999). Antes era un ValueError o
  un OverflowError al dibujar. Ahora el fichero no se lee y se dice: «la cabecera tiene límites o números de intervalos
  que no tienen sentido».
* **Series DICOM con PixelSpacing 0** (o 1e-300). Daban OverflowError en «Importar TAC». Ahora esos cortes no se
  aceptan («El tamaño de píxel (PixelSpacing) de algunos cortes no es válido»). El límite es 1e-4 mm, así que un
  micro-TAC de 0,001 mm sigue valiendo.
* **Un NUL en el nombre de RESUME o DUMPTO.** El `.in` no se abría (ValueError «embedded null byte»). Ahora se abre;
  el volcado no se lee.
* **Una biblioteca de materiales con un carácter suelto** (un surrogado) en un nombre. No se podía guardar, y lo de
  memoria quedaba distinto de lo del disco. Ahora se guarda con escapes ASCII.
* **Un `.ct` cuya cabecera pide más vóxeles de los que caben en el fichero** (120 millones en 8 kB). Daba un
  MemoryError (21,5 GiB). Ahora dice «Fin de fichero inesperado» antes de reservar memoria.
* **Índices de campaña o de paralelo que son JSON pero no un objeto** (`[1]`, `"paralelo"`). Daban AttributeError al
  abrir la carpeta. Ahora se tratan como un índice que no se puede leer.
* **Un GRIDR enorme** (1e200). Daba OverflowError al pintar el corte, en cada repintado. Ahora las coordenadas de
  dibujo se acotan, y una malla de más de 1e9 cm no se dibuja (ni en 2D ni en 3D).
* **Buscar un material con números absurdos** («(H2O)» y 400 nueves) daba OverflowError. Además, un nombre con miles
  de cifras tardaba segundos, porque la expresión regular era cuadrática. Ahora es lineal.
* **Espectros con valores que se desbordan** (1e308). Las probabilidades salían 0 o NaN sin aviso, y las líneas se
  juntaban en inf. Con una densidad de 1e303/MeV salía «todas las probabilidades son cero». Ahora se divide por el
  máximo antes de multiplicar.
* **Espectros con energías negativas o desordenadas.** El intervalo al revés pasaba sin error, y en las salidas de
  penhmain no se comprobaba que los bordes crezcan. Ahora se dice.
* **NBX = 1.0E+999 en una cabecera.** Daba OverflowError; ahora es un error de formato («no es un número de
  intervalos»), que los diálogos ya enseñan.

### 3. La ventana (4)

* **«Ejecutar» con la geometría sin guardar.** Con «Ejecutar» abierto, «Elegir resultados…» → «Deshacer» (que es el de
  la ventana) puede deshacer un cambio de la geometría. «Ejecutar» solo guardaba el `.in` si tenía cambios, así que
  penhmain simulaba la geometría del disco, que ya no era la de la ventana. Ahora guarda siempre todo lo que tenga
  cambios y, si no puede, no ejecuta.
* **Los diálogos del editor** («Envolver en un módulo», «Añadir un cuerpo» o un módulo, «Añadir un detector», «Añadir
  una celda de detectores», «Duplicar», «Añadir una superficie» y «Pegar un módulo»). Cada uno comprueba lo escrito con
  un temporizador (150 ms). Al cancelarlo, la comprobación seguía
  pendiente y miraba los cuerpos de antes en la geometría de ahora: un IndexError con «Envolver…» en la prueba de mono.
  Ahora, al cerrarse, el diálogo para su temporizador.
* **Semillas en «Trabajo».** Una semilla de más de 4 bytes (3141592653) se escribía en el `.in`: penhmain no la leería
  y el panel «Simulación» entero quedaba desactivado. Ahora el campo admite lo que admite RANECU (de 1 a 2147483562 y
  de 1 a 2147483398; con el par precalculado, de 1 a 1001) y lo dice en su ayuda.
* **Campos vacíos en «Trabajo».** La ayuda de «Partículas», «Tiempo máx.» y «Cada» dice «Vacío: …», pero al vaciarlos
  salía «no es un número» y volvía el valor de antes. Ahora se quita la línea.

### 4. La navegación 3D (1)

* **El punto hacia el que se acerca y alrededor del que se gira** podía ser una trayectoria que no se ve: de las de
  LTRACK, las que no caben en la imagen (`MAX_TRACK_POINTS`) y, en la animación, lo que aún no ha salido. La rueda iba
  hacia algo invisible. Ahora solo cuenta lo dibujado, con el mismo reparto que `_draw_tracks`.

## Datos de penhan y PENGEOM verificados en esta revisión

* RANECU (`RAND`, en rita.f): la primera semilla va de 1 a 2147483562 y la segunda de 1 a 2147483398. `RAND0` tiene
  1001 pares precalculados.
* GEOMIN compara ICHPAR con NPINP antes de la palabra clave: en las superficies reducidas e implícitas, en sus
  transformaciones, en las de los módulos, en CLONE y en el TAC (pengeom.f 461-476, 612-626, 694-708, 1390-1406,
  1645-1658 y 2258-2270).
* GEOMIN busca las etiquetas de cuerpos y módulos solo si ya hay otros elementos (`IF(NBODYS.GT.1)`, pengeom.f 925, 973
  y 1222).
* Con un solo elemento en el nivel superior, GEOMIN no añade la envolvente, y LOCATE no mira las superficies de la raíz
  antes que las hijas.

## Lo que queda

* Lo que quedaba en ADR-021, igual.
* Una serie de TAC con píxeles de menos de 1e-4 mm (0,1 µm) no se acepta.
* Falta que Rafael pruebe en su Windows lo de 0.17.1: el desplegable sin «Programas de penhan» y moverse en 3D (con
  GPU).

## Verificación

* `tests/test_review10.py` (38 pruebas):
  * fórmulas con caracteres que no son ASCII y paréntesis de más;
  * la búsqueda de materiales con números absurdos y nombres largos (también lo que tarda);
  * el NUL en el nombre del volcado;
  * la biblioteca con un surrogado;
  * las cabeceras de mapas, de NBX y del `.ct`;
  * los índices de campaña y de paralelo;
  * los espectros que se desbordan o van al revés;
  * DICOM con PixelSpacing 0 y un micro-TAC que sigue valiendo;
  * sin END, el error del último elemento;
  * los códigos de NPINP, y uno con el PARINP definido que conserva el suyo;
  * la referencia en el primer elemento;
  * los primeros errores frente a la sonda de PENGEOM;
  * eliminar dejando una hija que asoma, y uno en el que no asoma.
* `tests/test_gui_review10.py` (10 pruebas):
  * las semillas y los campos vacíos de «Trabajo»;
  * los diálogos del editor y la cadena de la prueba de mono («Duplicar», «Envolver…», Esc y deshacer);
  * «Ejecutar» guarda la geometría deshecha con el diálogo abierto;
  * la composición con mayúsculas que no son ASCII;
  * el GRIDR enorme;
  * el NUL del volcado en la ventana.
* `tests/test_view3d_navigation.py`: solo se coge lo que se dibuja (LTRACK con el límite y la animación).
* Cada prueba nueva falla con el código de 0.17.1, salvo los tres controles.
* Al repetir la búsqueda con las correcciones, no sale ningún fallo: los guiones de cada hallazgo, las cadenas del
  editor, el mono y las 40 000 cámaras.
* La batería completa de pruebas y ruff; con la GPU (Mesa), las de la vista 3D, las trayectorias y el visor de la
  cascada.

## Consecuencias

* Ningún fichero dañado de los probados tumba la ventana ni deja un aviso de «error inesperado»: o se lee, o se dice
  qué tiene.
* Los errores de PENGEOM que enseña ARGUS son los de GEOMIN también en estos casos límite. Lo que GEOMIN no comprueba
  se sigue rechazando, pero sin atribuírselo a PENGEOM.
* Lo que se ejecuta es siempre lo que se ve en la ventana.
* 0.17.2 incluye lo de 0.17.1, que no llegó a publicarse.
