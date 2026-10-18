# ADR-016 · Quinta revisión: resultados, análisis, lotes, espectros y compatibilidad

**Estado:** aceptada (versión 0.12.2)

## Contexto

Rafael pidió (05-10-2026) buscar fallos, errores e incompatibilidades en todo lo que se pudiera. Se hizo:

* un entorno idéntico al de su PC (Python 3.12 con su `uv.lock`: numpy 2.5.3, matplotlib 3.11.2, PySide6 6.11.2,
  numba 0.67, pydicom 3.0.2) para pasar todas las pruebas, también con los avisos convertidos en errores;
* siete revisiones en paralelo (lectura y combinación de resultados; análisis; lotes, programas y Windows; ventana de
  resultados; espectros y fuente; compatibilidad y análisis estático; geometría y `.in`), con los oráculos de PENGEOM
  y PMRDR, penhmain, penhmain-sum y mcdet compilados, y las salidas reales de Rafael (las 87 carpetas de `SOBP`).

Cada hallazgo se reprodujo antes de corregirlo y tiene su prueba (`test_review5_*.py`).

## Decisiones

### 1. Casos distintos no se combinan como réplicas

Una carpeta sin índice de ARGUS con subcarpetas de resultados (la carpeta `DJ` de Rafael: seis espectros distintos
con las mismas semillas) se combinaba como si fueran ejecuciones en paralelo del mismo caso: la dosis media de tres
espectros con la incertidumbre dividida entre √3.

Ahora (`results/folder.replica_problem`) solo se combinan si simulan lo mismo: el registro de penhmain
(`penhmain.dat` hasta «Job characteristics», sin la fecha) o, si falta, el `.in` sin las palabras clave de cada
ejecución (`RSEED`, `NSIMSH`, `TIME`, `DUMPP`, `DUMPTO`, `RESUME`), la misma geometría y semillas distintas (como
pide penhmain-sum, pero comprobándolo). Si no, la ventana enseña «Cada caso por separado», cada uno con su geometría,
y dice por qué no se combinan. Con el índice de ARGUS (`argus-paralelo.json`) se confía en él. Además, al combinar se
rechazan resúmenes de títulos, partículas o cuerpos distintos y fuentes distintas.

### 2. Otros resultados

* **Copias renombradas** (`E100_penhmain-res.dat` junto a `penhmain-res.dat`): el resultado es el que tiene el nombre
  de penhmain (o, si ninguno, el más reciente); los demás son copias con su propia clave y su nombre en el árbol.
* **Medias por destino del resumen** (recorrido, energía final, ángulo, interacciones de las primarias que salen o se
  absorben): se combinan con las primarias de ese destino, como penhmain-sum (coincide con él a 1e-7).
* **Estado**: con `RESUME`, el final por `TIME` no se deduce del tiempo del resumen (suma el de las ejecuciones
  anteriores); sin `DUMPP`, penhmain vuelca cada día (DUMPP = 2e9, recortado a 86400 s), no solo al acabar.
* **Ficheros a medio escribir** durante un volcado (penhmain reescribe cada salida y cierra el resumen el último): un
  fichero reciente vacío, con la última línea a medias, con menos puntos que la vez anterior, o de una carpeta cuyo
  resumen está a medias, se espera (se sigue viendo lo anterior) en vez de enseñarse cortado.
* **La geometría cambiada después de simular**: se compara con el `geometry.rep` que escribió penhmain; si no es la
  misma, no se dan nombres, masas, dosis por cuerpo ni histogramas por cuerpo, y se dice por qué.

### 3. Análisis

* **Gamma** exacto en 1D y 2D sobre la distribución interpolada linealmente (en 2D, dos triángulos por celda y la
  distancia a cada uno, como Ju et al. 2008); en 3D, la búsqueda en rejilla se afina desde el mejor punto y desde el
  propio punto. La rejilla sola daba fallos falsos en las caídas de dosis (γ = 1,6 con 0,1 mm de desplazamiento).
* Datos medidos: la unidad de la posición sale del título de su columna (y si no lo dice, se pregunta); solo se
  ofrecen con curvas en función de la posición; números entre comillas y filas sin incertidumbre.
* Espacio de fases leído en parte: «por primaria» entre las lluvias que cubren las partículas leídas (ΣNSHI).
* Líneas discretas de la fuente: medidas propias (probabilidad total, energía media) y fuera del panel «Espectro».
* Histograma dosis-volumen de un cuerpo cubierto solo en parte por la rejilla: se dice qué parte se ve.
* Rejillas de dosis por defecto con la caja de lo que tiene material (afinada con rayos), no la de la esfera de vacío.
* D98, D50… del `ct-dvh.dat` de penhmain con el borde inferior de cada intervalo; anchura a media altura de las
  ventanas interpolada; cociente con numerador 0; canales logarítmicos que no bajan hasta 0; posiciones 0,0.

### 4. Ventana de resultados

Superposiciones de la selección anterior, carpetas comparadas que no se releían al seguir, «Comparar las N
variantes» cuando la primera no tiene el resultado (y qué variante se ve en un mapa), el resumen con las unidades, la
barra de estado y los textos que se quedaban viejos, series que tienen datos al seguir, exportar con otra extensión,
el rótulo del perfil, `LTRACK` (penhmain guarda como mucho 50), etiquetas numéricas en «Elegir qué resultados pide el
.in», y un atributo `metric` que tapaba `QWidget::metric()` (error al cambiar de pantalla o de escala en Windows; una
prueba vigila que ningún atributo tape un método virtual de Qt). Los hilos de lectura ya no retrasan la salida.

### 5. Lotes y Windows

* Campañas sobre `NSIMSH` (las lluvias de cada variante, repartidas) o `DUMPP`; `RSEED`, `RESUME` y `DUMPTO` no son
  parámetros (los pone ARGUS). Palabras clave que no existen, un material que no hay, nombres de `.mat` que penhmain
  no puede abrir y `SCONE` sobre una fuente `SRECTA` se rechazan; antes de preparar, cada variante pasa las
  comprobaciones de penhmain con su geometría.
* `lanzar.sh`/`lanzar.ps1` no vuelven a lanzar una carpeta que ya tiene `penhmain.out` o resultados (empezaría desde
  cero encima), entrecomillan bien un penhmain con espacios o paréntesis, avisan si la carpeta está en la red (cmd y
  WSL no trabajan en `\\servidor\...`) y escriben la salida sin búfer.
* «Preparar la carpeta de simulación» y los lotes no sustituyen las rutas de «Programas de penhan» por una vacía o que
  no existe en ese momento.
* `ARGUS.bat` no cambia de carpeta (`uv run --project`) y el instalador usa `pushd`: funcionan desde la red y con
  rutas relativas. `tools/revisar_in.py` escribe en UTF-8 aunque la salida vaya a un fichero.

### 6. Fuente y espectros

* **Fuente extensa**: si la caja (`SPOSIT`/`SBOX`) no toca los cuerpos activos (`SBODY`), penhmain repite el sorteo de
  la posición sin fin (y escribe un aviso por intento); ahora es un error, y si los toca muy poco, un aviso.
* Al importar un `psource.dat`, la energía exacta del SOBP (un intervalo de anchura cero, que penhmain escribe con
  FACT = 1e15) ya no se pierde, y se usa la serie definida (no la muestreada). También se importan las líneas.
* La última fila `SPECTR` (de otro `.in`, del formato de ARGUS o con «# energía: borde») es la energía máxima tenga el
  valor que tenga, como en penhmain; «Guardar…» la escribe con −1.
* Las líneas `SPECTR` comentadas (blancos en las columnas 1 a 6) no cuentan; un CSV con comas se decide para todo el
  fichero; la unidad y la densidad salen del título de su columna.

### 7. Geometría, `.in`, materiales y compatibilidad

* Con varios errores en un `.geo`, también se da el primero del fichero, el que da PENGEOM (lee línea a línea),
  esté en un elemento anterior o en el mismo (una etiqueta repetida en la cabecera, una superficie que no existe o
  un lado 0 antes de la línea mal escrita). Como gfortran al leer de una variable, los blancos del final de la
  línea no cuentan: un signo que la acaba («INDICES=( 1, 1,-», «MATERIAL(  -») es un error (antes se leía 0: con
  «MATERIAL(  -», ARGUS daba por buena, con vacío, una geometría que PENGEOM rechaza). En 1058 variantes de las
  geometrías del corpus con los cambios típicos hechos a mano, el mismo resultado que PENGEOM (el mismo error, o
  las mismas tablas y LOCATE) salvo una: una línea «1111» repetida dentro de un giro, que PENGEOM lee como un
  índice PARINP que no existe (ARGUS dice que la palabra no existe).
* `.mat` con reacciones nucleares absorbentes (`NISOT` < 0): no necesitan ficheros ENDF.
* `numpy>=2.0` (se usa `np.trapezoid`); el lector rápido del `.ct` deja las repeticiones `r*c` al lento (con NumPy
  anterior a 2.3 se leían mal); `Colormap.with_extremes` en vez de `set_bad` (matplotlib 3.11); la tubería de salida
  de penhmat se cierra.
* Con la plataforma «offscreen» de Qt y un servidor X (las pruebas en un Linux con escritorio), Qt crea contextos
  OpenGL pero la vista 3D de la GPU no dibuja nunca (cuatro pruebas se quedaban esperando): `probe_gl` dice que no y
  se usa la CPU.

## Consecuencias

* Pruebas en `test_review5_results.py`, `test_review5_ui.py` y `test_review5_core.py`; las de antes, ajustadas donde
  el comportamiento era el fallo (`test_results_ui`, `test_source_edit`, `test_spectrum_file`, `test_batch`).
* Toda la suite pasa con Python 3.11 y con el entorno del PC de Rafael (Python 3.12).
* Sin probar en Windows de verdad: los `.bat` y `.ps1` (razonados línea a línea) y WSL.
