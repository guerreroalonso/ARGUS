# ADR-013 · Los programas de penhan del usuario: generar los .mat con su penhmat

**Estado:** aceptada (versión 0.11.0)

## Contexto

ARGUS no incluye penhmat ni penhmain ni los compila. Cada usuario los compila en
su ordenador (optimizados para él) dentro de su carpeta de penhan: penhmat en
`database`, como pide `database/readme.txt`, y penhmain donde quiera. Hasta la
0.10, ARGUS solo escribía en `materiales.txt` las respuestas que hay que dar a
penhmat, y el usuario tenía que ejecutarlo a mano y copiar el `.mat` y sus
ficheros ENDF a la carpeta del caso.

El usuario pidió:

* que se pueda decir dónde está su carpeta de penhan, o que no hace falta;
* que ARGUS busque los ENDF necesarios y los lleve solo a la carpeta donde se
  simula;
* que funcione con un penhmat de Windows, de Linux o de macOS. El suyo es de
  Linux (`penhmat.x`, ELF), en su carpeta del escritorio de Windows, y también
  sus penhmain (`penhmain.x`).

## Decisión

### 1. Dónde están los programas (Opciones → «Programas de penhan…»)

* **Qué se elige:**
  * la carpeta de penhan;
  * de ella salen la `database` (con `pdfiles`, `pdhan` y `ENDF_data`), penhmat
    y penhmain;
  * y si ARGUS genera los `.mat` con penhmat o **no hace falta** (el usuario
    los genera; ARGUS solo copia los ENDF).
* **Dónde se guarda:** en los ajustes. La base de datos y penhmain son los
  mismos que usa «Preparar la carpeta de simulación».
* **La primera vez** que se genera algo sin haberlo dicho, se abre este
  diálogo, ya relleno con lo que se encuentra junto a los casos.
* **Qué dice el diálogo:**
  * qué es cada ejecutable;
  * cómo se ejecutará en este ordenador;
  * cómo se lanza penhmain.

  «Probar penhmat» genera agua líquida (n.º 278 de la lista de PENELOPE) con
  un nombre temporal, comprueba el `.mat` y lo borra.

### 2. Ejecutables de cualquier sistema (`penhan/programs.py`)

Un ejecutable se reconoce por su **contenido**, no por su extensión:

* **PE:** Windows;
* **ELF:** Linux;
* **Mach-O:** macOS, también los universales;
* **un guion con `#!`.**

Se lee también el procesador (x86-64, arm64…).

Cómo se ejecuta:

* **si es del mismo sistema:** directamente, si el procesador es compatible
  (un x86-64 vale en un Mac con Rosetta y en Windows sobre ARM);
* **en Windows, uno de Linux:** con **WSL**, con
  `wsl.exe --cd <carpeta de Windows> -e ./penhmat.x`, sin comillas anidadas;
  * si penhmat no está en la carpeta `database`, su ruta se traduce con
    `wslpath`;
  * antes se comprueba que WSL tiene una distribución (su nombre se muestra);
  * si no la tiene, se dice cómo instalarla («wsl --install») o que se compile
    penhmat para Windows;
* **en cualquier otro caso** (uno de Windows en Linux, uno de Linux en un
  Mac…): no se ejecuta, y se dice por qué.

La salida de penhmat se lee como UTF-8, y la del propio `wsl.exe` como UTF-16.
Los fallos típicos se explican:

* permiso de ejecución (`chmod +x`);
* bibliotecas de Fortran que faltan en la distribución (instalar gfortran o
  compilar con `-static`);
* un penhmat que pregunta otra cosa (otra versión).

La orden para lanzar penhmain en la carpeta de simulación también depende del
ejecutable:

* **de Windows:** `penhmain < caso.in` en cmd o `cmd /c "…"` en PowerShell;
* **de Linux en Windows:** en un terminal de WSL, `cd "/mnt/c/…" &&
  ./penhmain.x < caso.in`;
* **en Linux o macOS:** `./penhmain.x < caso.in`.

### 3. Generar los .mat (`penhan/matgen.py`)

Se generan los `.mat` cuya composición conoce ARGUS:

* **tejidos de un TAC importado con ARGUS:**
  * composición de la ficha `.ct.json`;
  * **densidad media de sus vóxeles**;
  * I de la regla de ARGUS (ADR-011);
  * opción 1 de penhmat, o la 2 si el tejido es de la lista sin composición
    conocida;
* **materiales de la lista de PENELOPE**, por su nombre en el panel
  Materiales (el comentario de la línea MFNAME): opción 2 con su número.

Los demás se explican y hay que generarlos a mano. Para uno de la lista basta
con escribirle su nombre de la lista en el panel Materiales.

**Cómo se ejecuta penhmat:**

* en su carpeta `database`, porque lee `./pdfiles`, `./pdhan` y `./ENDF_data`;
* con las respuestas por la entrada estándar;
* con salida a un **nombre temporal único y corto sin ruta**
  (`argus-xxxxxxxx.mat`), que se escribe en esa carpeta como cuando se usa a
  mano. Así no hay que traducir rutas para WSL, ni tropezar con los 80
  caracteres de penhmat ni con tildes en la ruta del usuario.

**Después:**

* se comprueba el resultado: que no haya `IRETRN`, que el código de salida sea
  0, que tenga la cabecera de PENELOPE y la lista final de ENDF completa;
* el `.mat` se copia con su nombre a la carpeta del `.in`, **sin sustituir
  nunca un fichero**: si aparece otro con ese nombre, se deja;
* se borra el temporal.

Cancelar detiene penhmat. Tampoco se sustituye un `.mat` de un tejido del TAC
que es de otra importación (con otra densidad): se pide borrarlo o renombrarlo.

Con protones o alfas (o con una fuente radiactiva), los **ENDF** que listan los
`.mat` del `.in` y no están en la carpeta:

* se copian desde `database/ENDF_data`, o desde la carpeta del `.mat`;
* sin sustituir ninguno;
* los que no están en la base de datos se dicen.

Si solo faltan ENDF, no hace falta penhmat.

### 4. En la interfaz

* **Panel Materiales → «Generar .mat y ENDF…»:**
  * sale en negrita cuando falta un `.mat` o un ENDF;
  * genera en la carpeta del `.in`, con una tabla de qué se hará, progreso y
    cancelación;
  * copiar los ENDF allí es una casilla, marcada por defecto: hacen falta si
    penhmain se ejecuta en esa carpeta, como hace el usuario;
  * al terminar se vuelve a escribir `materiales.txt` (si el `.in` no tiene
    cambios sin guardar).
* **«Preparar la carpeta de simulación»:**
  * «Generar los .mat que faltan…» los genera junto al `.in`, sin los ENDF;
  * después se copian a la carpeta de simulación con sus ENDF;
  * el texto dice cómo lanzar el penhmain elegido.

## Consecuencias

* **Módulos nuevos:** `penhan/programs.py`, `penhan/matgen.py` y
  `ui/penhan_dialogs.py`.
* **Pruebas nuevas:**
  * `test_penhmat.py` (cabeceras sintéticas de ejecutables, WSL simulado, un
    penhmat de mentira y el penhmat real);
  * `test_penhmat_ui.py`.
* **No se ha podido probar aquí WSL en Windows.** La orden se ha escrito para
  no depender de comillas. Con un penhmat de Linux en Windows, «Probar
  penhmat» lo comprueba en el ordenador del usuario.
* **Queda:**
  * se usa la distribución de WSL por defecto;
  * penhmat escribe su temporal en `database`: un corte de luz a mitad lo
    dejaría allí, con su nombre `argus-…`.
