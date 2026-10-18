# ADR-018 · Materiales con composición propia, el terminal al ejecutar y la sección «Trabajo»

**Estado:** aceptada (versión 0.14.0)

## Contexto

Rafael pidió (10-10-2026) revisar tres cosas:

1. **Materiales**: cómo sabe ARGUS, a partir del nombre de un material, el nombre del fichero que genera y su número;
   y que se pueda dar la composición del material que se quiere simular, para tener las dos opciones (al crear un
   material a mano).
2. **El terminal**: al ejecutar, que se abra un terminal donde se vea la simulación en tiempo real: el de Linux en
   Linux, el de Windows en Windows y el de macOS en macOS. Él tiene penhmain compilado en Linux y trabaja en Windows
   con WSL: tiene que poder abrir el terminal de Linux; a los usuarios de Windows, quizá, dejarles elegir el de Linux
   o el de Windows.
3. **«Trabajo»** en el diálogo de ejecutar: «Lluvias (NSIMSH)» es el número de partículas que se simulan y debería
   llamarse «Partículas»; el tiempo máximo puede ser de semanas o más (hasta 10⁹ s); y otro contador, «Lluvias», para
   elegir cuántas se siguen (trayectorias), como mucho 1000.

## Decisiones

### 1. Cómo se genera un `.mat` y la composición propia (`penhan/composition.py`)

penhmat (el programa MATERIAL de penhan: PEMATW de `penelope.f` y PHMATW de `penhan.f`) genera un `.mat` de dos
maneras, y ARGUS tiene ahora las dos:

* **Opción 2, la lista de PENELOPE** (lo que había). El nombre del material se guarda como comentario de su línea
  `MFNAME` en el `.in` (penhmain no lee más allá de la columna 27). Al escribirlo, el panel Materiales (y el diálogo
  del material) lo busca en `database/material-list.txt` de la instalación de penhan del usuario (la de «Programas de
  penhan» o la que se encuentre subiendo desde la carpeta del caso): el elegido queda como «Water, liquid (PENELOPE
  278)», y ese número entre paréntesis es el que se da a penhmat (`match_material`; también vale el nombre exacto de
  la lista, sin mirar mayúsculas). El fichero se propone con la primera palabra del nombre en minúsculas (como mucho
  14 letras, con un número si ya está: `water.mat`, `water2.mat`; `suggest_mat_filename`) y se puede cambiar (20
  caracteres, lo que lee penhmain con `(A20)`). El número del material en la geometría (`MATERIAL(N)` en el `.geo`)
  es el orden de las líneas `MFNAME`.
* **Opción 1, composición propia** (nuevo). `Composition` guarda lo que se contesta a penhmat: los elementos con su
  cantidad (átomos por molécula, «fórmula», o fracción en masa), la densidad y la energía media de excitación:
  * la **regla de ARGUS** (por defecto; la de los tejidos del TAC, ADR-011): sólidos y líquidos, Bragg con los
    valores de fase condensada de ICRU 37 escalada al agua de ICRU 90 (78 eV); gases (< 0,02 g/cm³) y elementos, la
    que calcula PENELOPE;
  * **la que calcula PENELOPE** (se contesta «no» a cambiarla);
  * **otro valor** (≥ 1 eV, lo que admite penhmat).

  Lo que escribe el usuario se entiende como fórmula química (`C5H8O2`, `Ca10(PO4)6(OH)2`, `CuSO4·5H2O`; átomos)
  o como pares elemento-cantidad (`H 0.111894 O 0.888106`, con símbolo o número atómico, coma decimal o %); si son
  pares, se adivina si son átomos (enteros) o fracciones (suman 1 o 100) mientras el usuario no lo elija. Se
  comprueba antes lo que haría que penhmat volviera a preguntar (y se quedara esperando) o se parase: Z fuera de
  1-99, cantidades no positivas, elementos repetidos (IRETRN 1101/1103), más de 30 elementos, densidad no positiva,
  I menor de 1 eV; el nombre va en ASCII y con 60 caracteres como mucho.
* **Dónde se guarda**: en el propio `.in`, en comentarios justo detrás de su `MFNAME`, con el fichero en cada línea
  (si alguien cambia el orden a mano, no se confunde con otro material) y partidos para no pasar de la columna 127:

  ```
  MFNAME pmma.mat             [PMMA]
         [ARGUS pmma.mat: composicion por atomos; densidad 1.19 g/cm3; I segun ARGUS]
         [ARGUS pmma.mat: C 5 H 8 O 2]
  ```

  penhmain salta las líneas con la palabra clave en blanco en toda la sección de materiales (penhmain.f 2025,
  `IF(KWORD.EQ.'      ') GO TO 202`, 203, 273 y 274), y el lector de ARGUS (PMRDR0) también: el `.in` sigue
  siendo el mismo para penhmain. Así la composición viaja con el `.in` (copiarlo, una campaña, la carpeta de
  simulación). Si cambia el nombre del fichero en la tabla, su composición pasa al nuevo; al quitar el último
  material, se quita la suya (si ningún otro usa el fichero); al elegir en la tabla un material de la lista
  («… (PENELOPE n)»), deja de tener composición propia: un material, un origen.
* **«Generar .mat y ENDF…»** contesta a penhmat con la composición (opción 1); manda sobre la del TAC y sobre el
  nombre. `materiales.txt` da las respuestas a penhmat de los que tienen composición propia y el número de los de la
  lista, para hacerlo a mano.
* **Un `.mat` que ya está y no es el del `.in`**: se lee su cabecera (densidad, átomos por molécula de cada elemento
  e I) y se compara: si no coincide, se avisa (en el panel, los mensajes y el diálogo) y no se toma por bueno; ARGUS
  no lo sustituye nunca: se cambia el nombre del fichero (el diálogo propone el siguiente libre, `pmma2.mat`) o se
  borra a mano.
* **Interfaz**: el diálogo del material («Añadir un material» y, con el botón derecho en el panel, «Composición del
  material N…») tiene las dos opciones: la lista (dice el número y el nombre del elegido) o la composición propia,
  con una tabla de cada elemento con sus átomos y su fracción en masa, la densidad, las tres opciones de I con su
  valor y lo que pasa con el fichero. La ayuda emergente del nombre en el panel dice de dónde sale la composición de
  cada material.
* **Comprobado con el penhmat real** (Linux, la base de datos de penhan 2025): agua y PMMA por fórmula, aire seco
  por fracciones (gas), aluminio y plomo (un elemento), un tejido blando de 9 elementos, hidroxiapatita y PMMA con
  I de PENELOPE y con I = 74 eV: la cabecera de los 9 `.mat` tiene exactamente los elementos, sus proporciones, la
  densidad y la I que dice el `.in`, y la lista de ficheros ENDF completa.

### 2. El terminal donde se ve la simulación (`penhan/run.py`, `ui/run_dialog.py`)

* Linux: el terminal que haya (`$TERMINAL`, `x-terminal-emulator`, GNOME, Konsole…; sin ninguno, en segundo plano);
  macOS: el Terminal. Como antes, la salida se ve al momento (`GFORTRAN_UNBUFFERED_PRECONNECTED=y`) y queda también
  en `penhmain.out`.
* **Windows: se elige** «Terminal: Linux (WSL) / Windows (cmd)», y se recuerda:
  * un **penhmain de Linux** (el de Rafael, compilado en WSL) va en el **terminal de Linux**: el guion de sh corre en
    la distribución de WSL (`wsl.exe --cd <carpeta> -e sh argus-ejecutar.sh`, en una ventana de consola nueva, la
    que tenga Windows por defecto). La consola de Windows no se puede elegir (no puede ejecutarlo);
  * uno **de Windows**: en la consola de Windows (el `.bat`, como siempre) o, si se prefiere, en el de Linux, donde
    WSL lo ejecuta como programa de Windows («interop»; el guion pone `GFORTRAN_UNBUFFERED_PRECONNECTED` en `WSLENV`
    para que la salida siga viéndose al momento) y la salida queda también en `penhmain.out`. Si WSL no está o no
    puede ejecutar programas de Windows (se pregunta una vez: `/proc/sys/fs/binfmt_misc/WSLInterop`), solo la
    consola.
* Los mensajes dicen dónde se ejecuta: «el terminal de Linux (WSL: Ubuntu 22.04.4 LTS)», «la consola de Windows
  (cmd)». Antes decía «una consola de Windows (con WSL)», que parecía la de Windows aunque fuera Linux.
* Una carpeta de red no sirve con WSL: se dice también cuando se elige el terminal de Linux con un penhmain de
  Windows.

### 3. «Trabajo» (`ui/sim_outputs.py`, `ui/sim_widgets.py`, `ui/run_dialog.py`)

* `NSIMSH` se llama **«Partículas»**: las partículas primarias que se simulan, cada una con su lluvia (con una
  fuente radiactiva, desintegraciones). Donde ARGUS cuenta lo de `NSIMSH` (seguir la simulación, el resumen, las
  simulaciones en paralelo, los avisos) dice también «partículas»; «lluvias» queda para las que se siguen y se ven
  (la cascada y `LTRACK`, que pasa a llamarse «Trayectorias: N lluvias en la simulación»).
* **El tiempo máximo** (`TIME`) se escribe en s, min, h, días o semanas (en el `.in`, en segundos; la unidad se
  recuerda) y como mucho **10⁹ s** (unos 31,7 años): más no se acepta y se dice. Junto al campo, lo que es en
  segundos (o en semanas, días…). Vacío: sin límite (penhmain toma 2e9 s); un `.in` que ya tiene `TIME 2e9` se enseña
  tal cual.
* **«Lluvias»** (en el diálogo de ejecutar, debajo del tiempo): cuántas de las primeras se siguen para verlas en 3D
  con sus trayectorias reales (la cascada, ADR-017): de 0 (ninguna) a **1000** (`MAX_SHOWERS` era 50 000). Sustituye
  a la casilla y el número de la caja «Cascada de partículas», que desaparece.

## Consecuencias

* Las dos maneras de generar un `.mat` están en ARGUS y en `materiales.txt`; un material que ARGUS no sabía generar
  (un nombre que no está en la lista) se puede generar dándole su composición.
* Un `.in` con composiciones propias tiene unas líneas de comentario más; penhmain no las lee. Si se editan a mano
  y no se entienden, ARGUS lo dice y no genera ese `.mat`.
* El terminal de Linux en Windows (y un penhmain de Windows en él) no se ha podido probar en Windows: las órdenes son
  las de WSL que ya usaban penhmat y la cascada; las pruebas las simulan.
* Las cascadas de más de 1000 lluvias hechas antes se siguen viendo; solo no se pueden pedir más.
