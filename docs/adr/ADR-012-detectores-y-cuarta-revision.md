# ADR-012 · Detectores con formas habituales, celdas de detectores y cuarta revisión

**Estado:** aceptada (versión 0.10.0)

## Contexto

El usuario pidió dos cosas para crear detectores:

* **Formas predefinidas**, las habituales en detectores (cuadrado, círculo,
  hexágono, pentágono y triángulo), en 3D con una altura, sin quitar la
  posibilidad de crear cualquier otro cuerpo.
* **Celdas de detectores**: un rectángulo en 3D (una caja) lleno de muchos de
  esos detectores.

Pidió también que los límites fueran los de penhmain y, al terminar, otra
revisión de todo el código en busca de fallos.

## Decisión 1: «Añadir un detector…»

(Menú Editar, botón derecho en el corte, en 3D o en el árbol.)

* **Formas:** cuadrado, rectángulo, círculo, triángulo, pentágono y hexágono,
  con su altura a lo largo de x, y o z y un giro alrededor de ese eje.
  * Los polígonos son prismas rectos de base regular (`prism3` a `prism6` en
    `pengeom/edit.py`), escritos con planos de PENGEOM. Su tamaño se da por el
    lado, por la distancia entre caras opuestas (del vértice a la cara opuesta
    si tiene un número impar de lados) o por el diámetro circunscrito.
  * El cuadrado y el rectángulo son cajas; el círculo es un cilindro.
  * Se colocan como cualquier cuerpo: dentro del cuerpo pulsado, comprobando
    que caben enteros, o pegados a una cara de otro elemento.
* **En el `.in`, en el mismo paso de deshacer.** El cuerpo nuevo va a un
  detector de penhmain:
  * uno nuevo de energía depositada (`ENDETC`/`EDBODY`), con su ventana de
    energía y sus canales;
  * uno nuevo de impacto (`IMPDET`/`IDBODY`);
  * uno de los que ya tiene el `.in`;
  * o ninguno (solo el cuerpo).
* **Límites de penhmain** (`penhmain.f`; los diálogos no dejan pasar de ahí):
  * 5 detectores de energía de hasta 100 cuerpos cada uno;
  * 5 detectores de impacto de hasta 50 cuerpos cada uno;
  * la ventana de energía como la corrige penhmain: EU < 50 eV es
    max(EPMAX, EHMAX); con canales logarítmicos, EL sube a 50 eV (energía) o
    a 1 eV (impacto); EU < EL + 1 es un error (3097/3077);
  * un detector de energía no se puede combinar con el forzado de
    interacciones (FORCER efectivo > 1,01) ni con `IXRSPL` (3095);
  * un cuerpo de vacío (material 0) no puede ser parte de un detector.

  Antes de aceptar, el diálogo aplica el cambio a una copia del `.in` y la
  comprueba como PMRDR. Solo cuenta los errores que no tenía ya el `.in`. Si
  una línea de detectores no se puede leer, no se toca.
* **Energía por cuerpo.** Aunque un cuerpo no esté en ningún detector,
  penhmain da la energía media depositada en él en `penhmain-res.dat` («Average
  deposited energies (bodies)»). La de cada detector es la suma de sus cuerpos:
  se ha comprobado con penhmain real.

## Decisión 2: «Añadir una celda de detectores…»

Una celda es una caja (un módulo de PENGEOM) llena de detectores iguales, en
filas, columnas y capas (`pengeom/detcell.py`).

* **Qué se elige:**
  * la forma y el tamaño de cada detector y su altura;
  * filas, columnas y capas;
  * la disposición:
    * en cuadrícula;
    * o con las filas desplazadas: en panal (hexágonos), al tresbolillo
      (círculos), alternos sin huecos (triángulos) o como ladrillos
      (cuadrados, rectángulos y pentágonos);
  * el hueco entre detectores y entre capas;
  * la pared de la caja alrededor de ellos;
  * el eje;
  * el material de los detectores y el de la caja.

  La caja se ajusta a los detectores con su extensión exacta (la de cada forma
  en cada dirección) más la pared. Una vista previa dibuja una capa de la
  celda vista a lo largo del eje.
* **Detectores de penhmain:** uno con toda la celda, uno por fila, por
  columna, por capa, uno por cada detector o ninguno, dentro de los límites
  anteriores. Cada detector de la celda lleva en su descripción del `.geo` su
  fila, su columna y su capa.
* **Límites de PENGEOM** (`pengeom.f`):
  * NB = 5000 cuerpos y módulos;
  * NS = 10000 superficies;
  * NXG = 250:
    * un módulo tiene como mucho 248 hijas;
    * su lista de superficies (las suyas y las de sus hijas) tiene como mucho
      249.
  * A un elemento del nivel superior GEOMIN le añade, sin comprobarlo, la
    esfera de la envolvente. Por eso ahí el límite es de 248 superficies: con
    una más, las tablas se corrompen sin ningún error.

  Para respetarlos, la celda se organiza sola en una de estas estructuras:
  * **plano**: los detectores son hijas directas de la caja;
  * **capas** o **filas**: módulos intermedios, sin huecos entre ellos;
  * **capas y filas**: filas dentro de cada capa.

  Los cortes entre módulos intermedios van a mitad del hueco entre detectores.
  Las etiquetas nuevas se toman de las libres (más allá de 9999 se reutilizan
  los números libres). Si no caben, el diálogo dice por qué antes de aceptar.

## Decisión 3: una superficie por plano (STEP)

Al comprobar las celdas con el `STEP` real de PENGEOM apareció un fallo que
`LOCATE` no ve:

* **El fallo.** Dos superficies *distintas* que coinciden geométricamente, una
  pared de un módulo y una cara de una de sus hijas. `STEP` puede no ver la
  pared del módulo y dejar la partícula en el material del módulo durante
  centímetros. Las hermanas que se tocan no tienen este problema.
* **Regla:** una sola superficie por plano geométrico, o una separación de al
  menos 1e-5 cm.
  * **Celdas.** Todas las caras planas se escriben como planos
    `(0,0,0,1,0)` con ángulos y desplazamiento. La normal tiene un signo
    canónico, y cada cuerpo nombra el plano con su propio lado. Los planos
    iguales se unen: misma normal y distancia con un cuanto de 1e-8 cm,
    mirando también las celdas vecinas del cuanto. Así la pared de la caja y
    la cara del detector que la toca son la misma superficie.
  * **«Envolver en un módulo».** Deja 1e-5 cm de holgura en las caras planas
    (lados de la caja y altura del cilindro; los radios no). Si con la holgura
    el módulo no cabe en su contenedor, se ajusta justo.
  * **Validación** (`coincident_walls`). Avisa cuando un módulo y una hija
    directa tienen planos distintos en el mismo sitio, y dice cómo
    arreglarlo. El corpus entero da 0 avisos.
* **Comprobado con PENGEOM real** (tablas, `LOCATE` y `STEP`):
  * 150 celdas al azar, la mitad en módulos girados, con 80 rayos cada una;
  * 85 celdas grandes.
  
  Todo coincide. Con penhmain real, la energía de cada detector es la suma de
  la de sus cuerpos.

## Cuarta revisión

Revisión en cinco partes: lo nuevo de los detectores, el núcleo de PENGEOM, el
`.in`, el TAC y la interfaz. Cada fallo se reprodujo con una prueba antes de
corregirlo (`test_review4_*.py`, `test_detectors.py`).

* **Detectores y celdas:**
  * las cajas de los prismas son las exactas en cada dirección (antes se
    tomaba el círculo circunscrito);
  * celdas enormes o con medidas diminutas se rechazan antes de calcular nada;
  * las etiquetas siguen funcionando más allá de 9999;
  * las descripciones caben en las columnas que lee GEOMIN (53 caracteres);
  * los giros enormes de un prisma se reducen a una vuelta.
* **Núcleo de PENGEOM:**
  * en un número, una mantisa sin cifras se lee como 0, como gfortran; un
    signo solo al final de un campo entero es un error;
  * la sonda lee exponentes de tres cifras;
  * más de 248 superficies en un elemento del nivel superior es un error;
  * «Añadir una superficie que ya existe» dentro de un módulo girado la toma
    como se ve, no en los ejes del módulo.
* **`.in`:**
  * fuente radiactiva: límites del `.nuc` de PENNUC (3120) y los ficheros de
    relajación de la base de datos;
  * tabla de `EPROF`, leída como el `READ` de Fortran (3137–3139);
  * ficheros de salida en carpetas que no existen (3114 y avisos);
  * líneas editadas que pasarían de la columna 127 (se acortan los reales);
  * volcados grandes, que se leen sin cargarlos (70 MB en menos de 2 s);
  * `GRIDZ` sin `GRIDR` (3134/3135);
  * una línea editada que no se puede leer conserva su texto;
  * renumerar no toca los factores de repetición (`2*1`);
  * se explica el «;»;
  * las etiquetas con apóstrofo no se escriben;
  * se avisa de `IBRSPL` con un `FORCER` negativo.
  
  El lector coincide con PMRDR en 1362 de 1362 variantes al azar.
* **TAC:**
  * cancelar una importación a medio escribir el `.ct` no deja ficheros; los
    temporales tienen nombre único;
  * el diálogo no deja importar mientras se cancela;
  * solo aparecen los tejidos presentes en el recorte;
  * la etiqueta del `CTBOX` y el número de materiales se comprueban antes de
    importar;
  * curvas CSV con punto decimal y «, » como separador;
  * `RescaleSlope`/`RescaleIntercept` de los grupos funcionales compartidos;
  * aviso de un `.mat` de otra importación (densidad distinta a la de los
    vóxeles).
* **Interfaz:**
  * menús y diálogos ya no se quedan vivos con la geometría de antes;
  * `Ctrl+Y` y `Ctrl+Mayús+Z` rehacen;
  * una geometría rota por un `PARINP` al abrirla se arregla desde el `.in`;
  * el corte no se queda con la imagen de una geometría que ya no vale;
  * el botón de guardar del panel «Editar» sigue a deshacer y rehacer;
  * «Guardar la geometría como…» y «Nueva geometría» no escriben encima de un
    `.in`, y guardar como el mismo fichero no cambia la carpeta de trabajo;
  * **los diálogos del editor prueban el cambio en una copia antes de
    cerrarse**: añadir un cuerpo o un detector, una superficie, duplicar,
    envolver y pegar. Si PENGEOM no lo aceptaría, el diálogo sigue abierto con
    lo escrito y dice por qué;
  * valores enormes de la fuente;
  * las asas de la rejilla de dosis con un PSF;
  * la rueda sobre una tabla desplaza el panel;
  * los cuadros numéricos leen «1e3» y «1.000,5»;
  * al abrir una geometría sin TAC se libera la textura del anterior, y la
    caché de `.ct` no pasa de 1 GiB;
  * el hilo del corte no falla si la vista desaparece a mitad del cálculo.

## Consecuencias

* **Módulos nuevos:** `pengeom/detcell.py` y `ui/detector_dialogs.py`.
* **Pruebas nuevas:** `test_detectors.py` (con `STEP` real mediante
  `geo_oracle.step_disagreements`) y `test_review4_pengeom.py`,
  `test_review4_in.py`, `test_review4_ct.py` y `test_review4_ui.py`.
* **Queda:**
  * penhmain solo admite 5 detectores de cada tipo. Con más cuerpos que
    medir, la energía de cada uno sale igualmente en `penhmain-res.dat`;
  * `coincident_walls` avisa, pero no corrige los `.geo` escritos a mano.
