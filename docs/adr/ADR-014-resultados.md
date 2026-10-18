# ADR-014 · Resultados de penhmain: verlos, analizarlos, simular en paralelo y en campañas

**Estado:** aceptada (versión 0.12.0)

## Contexto

Hasta la 0.11, ARGUS preparaba el .in, el .geo y los materiales, pero no mostraba lo que devuelve penhmain.
Rafael pidió (30-09-2026):

* ver todas las salidas de penhmain, sin pensar solo en el SOBP: las simulaciones pueden ser de cualquier tipo;
* elegir desde ARGUS qué salidas pide el .in;
* las trayectorias de LTRACK en 2D y 3D;
* simulaciones en paralelo como MultiRuns.

Decidió además:

* **ARGUS no lanza penhmain:** prepara las carpetas y los guiones, y sigue los resultados mientras se simula;
* **campañas ahora:** variantes de un caso para comparar sus resultados;
* **análisis:**
  * medidas en curvas y perfiles;
  * comparación con medidas, con el índice gamma;
  * dosis por cuerpo y DVH;
  * espectrometría.

## Decisión

### 1. Leer las salidas (`src/argus/results`)

* **Qué es cada fichero:** se reconoce por su **contenido**, por las líneas «#» que escribe cada subrutina de
  penhmain.f (`catalog.py`). Así se reconocen también los ficheros renombrados («E100_chi40_depth-dose.dat»),
  los de penhmain-sum (escribe a veces «PENMAIN») y los de penmain.
* **El nombre solo decide:**
  * el tipo de partícula de un `.trk`;
  * el número de detector si no está en la cabecera.
* **Salidas que se leen (`readers.py`):**
  * dosis: en profundidad, en los ejes, mapa 3D, cilíndrico y radial;
  * TAC: 3D, profundidad, ejes y DVH;
  * partículas que salen: energía hacia arriba y hacia abajo, ángulos (θ, φ) y ángulo polar con la energía
    media;
  * detectores: de impacto (espectro, fluencia, edad), angulares y de energía depositada;
  * espacio de fases;
  * fuente (psource y usource);
  * EPROF (P3D), en probabilidad o en fluencia según su cabecera;
  * trayectorias;
  * material.dat (tablas, `materialdata.py`) y las equivalencias de energía;
  * textos: registro, salida en pantalla, geometry.rep y PENNUC.
* **Lectura rápida:** `numpy.loadtxt` (un millón de filas de 10 columnas por segundo). Solo si falla se
  corrigen los exponentes de Fortran de tres cifras sin «E» y los «D».
* **Mapas:**
  * cada valor se coloca por sus índices IX, IY, IZ;
  * los bordes salen de la cabecera, porque los centros se escriben con 4 cifras;
  * un mapa con menos filas de las anunciadas se está escribiendo (`IncompleteFile`).
* **Incertidumbres:** siempre se guardan de **1 sigma**; penhmain escribe 3 sigma. Los valores 1e-35 son ceros.
* **Resumen (`summary.py`):** `penhmain-res.dat` completo:
  * lluvias, tiempos y velocidad;
  * fracciones;
  * secundarias;
  * interacciones medias;
  * recorrido, energía y ángulo de salida;
  * energía y carga por cuerpo;
  * detectores;
  * eficiencia del vóxel con más dosis;
  * últimas semillas.

### 2. Combinar ejecuciones (`combine.py`)

* **Cada resultado es una media por lluvia.** De N, m y σ se recupera el segundo momento `q = Nσ² + m²` y se
  combina como una sola simulación con todas las lluvias. Es exactamente lo que hace penhmain-sum con los
  contadores.
* **Medias condicionadas** (energía media por ángulo): se combinan con el peso total de cada intervalo,
  `TW = pdf·N·ΔΩ`.
* **Comprobación:** con tres ejecuciones reales, coincide con penhmain-sum:
  * valores, hasta 2e-6;
  * incertidumbres, hasta la cifra con que se escriben.
* **DVH del TAC:** se recalcula con la dosis combinada.

### 3. La carpeta y su estado (`folder.py`)

* **Salidas:** las de la carpeta.
* **El .in:**
  * el que tiene el título de `penhmain-res.dat`;
  * o el único .in de la carpeta.
* **Ejecuciones:** las subcarpetas con resultados, según el manifiesto de ARGUS o buscándolas. Las carpetas
  de penhmain-sum no cuentan como ejecución.
* **Estado, sin lanzar nada:**
  * `terminada`: se han hecho las lluvias de NSIMSH o se ha agotado TIME, o hay «END»;
  * `simulando`: el resumen se ha escrito en los últimos DUMPP + 2 min;
  * `parada`;
  * `error`: salida en pantalla con IRETRN o un error de Fortran;
  * `empezando`;
  * `vacia`.
* **Progreso:** frente a NSIMSH y TIME, con una estimación del tiempo que falta.

### 4. Unidades y normalización (`units.py`)

* **Unidades:** eV/g o Gy (del Gy al fGy); energías en eV, keV, MeV y GeV.
* **Normalización:**
  * por partícula o por desintegración;
  * para N partículas;
  * para A Bq durante t s;
  * porcentaje del máximo o de un punto;
  * área 1.

### 5. El visor (`src/argus/ui/results`)

* **Una ventana de resultados** (menú Resultados de la ventana principal):
  * árbol de salidas por familias: resumen, dosis, TAC, detectores, partículas que salen, fuente, proceso y
    textos;
  * cada resultado en su vista: curvas, mapas, resumen, texto, trayectorias, espacio de fases y datos de los
    materiales;
  * es siempre la misma: al cerrarla se para el seguimiento y al volver a abrirla sigue funcionando.
* **Gráficas con matplotlib** (no pyqtgraph, que no exporta bien a PDF y SVG con PySide6 6.11):
  * curvas con bandas de ±k·σ, escala logarítmica, una serie por partícula (color de su partícula) o por
    cuerpo (colores categóricos en orden fijo);
  * mapas con isolíneas y el perfil que se pulsa;
  * sin ejes dobles: lo que va en otras unidades, en otro panel.
* **Seguir la simulación:** se relee lo que cambia. Un fichero a medio escribir (`IncompleteFile`, también
  una tabla sin filas todavía) deja lo que se veía y se vuelve a leer en la vuelta siguiente.
* **Lecturas en un hilo aparte:** gana la última petición (un número de turno); una lectura antigua nunca
  sustituye a la nueva, y un error se enseña en lugar de la vista anterior.
* **Unidades:** todas las curvas dibujadas juntas (comparaciones, variantes) llevan las unidades «automáticas»
  de la principal (`resolve_units`). Con la normalización a N partículas o a una actividad, las unidades pierden
  el «por partícula».
* **Exportar:** la figura (PDF, SVG o PNG) y los datos que se ven (CSV); en un mapa esférico, su curva radial.

### 6. Sobre la geometría

* **El mapa en los cortes 2D** de la ventana principal («Ver sobre la geometría»): colores con transparencia
  (nada por debajo de un 2 % del máximo), isolíneas con sus etiquetas sin pisarse y una leyenda.
  * Caja: coordenadas del mundo. Cilindro: alrededor del eje z. Esfera: alrededor del origen.
  * TAC: coordenadas del TAC, llevadas al mundo con su colocación (`CtFrame`).
* **Perfiles** en cualquier recta del corte (menú contextual): van a la ventana de resultados, donde se miden
  y se comparan.
* **Trayectorias de LTRACK:** proyectadas en el corte 2D y en la vista 3D. Los puntos que se dibujan se
  reparten entre los tipos de partícula (`fair_shares`): muchas trayectorias de una partícula no dejan sin
  dibujar las de otra.

### 7. Análisis

* **Medidas** (`metrics.py`), con su incertidumbre por remuestreo:
  * general;
  * pico de Bragg: alcances, caídas distales 80-20 y 90-10, anchura del pico, cociente pico/entrada;
  * dosis en profundidad: máximo, R90, R80 y R50, Rp, PDD10, PDD20 y su cociente;
  * perfil lateral: centro, anchura a media altura, penumbras, planitud y simetría;
  * espectro: energía media, pico, su anchura y área;
  * histograma dosis-volumen: D98, D95, D50, D2, dosis media y máxima.
* **Comparar:**
  * con otras carpetas, otras ejecuciones o datos medidos (texto o CSV; coma decimal; mm → cm);
  * superponer, diferencia, cociente y z, con χ² y diferencia media;
  * índice gamma 1D y 2D: global o local, umbral y referencia, con su histograma.
* **Dosis por cuerpo** (`bodies.py`):
  * masa = volumen × densidad del material (`material.dat` o la cabecera del .mat);
  * volumen por muestreo estratificado con LOCATE; los cuerpos pequeños se vuelven a medir en su propia caja;
  * TAC: la suma de sus vóxeles;
  * un cuerpo que llega al borde de la escena (infinito o muy grande) queda sin masa, con el porqué.
* **Histograma dosis-volumen** (`dvh.py`):
  * por cuerpo, con un mapa de caja (subpuntos en cada vóxel partido entre cuerpos);
  * por tejido del TAC y del TAC entero;
  * ARGUS cuenta todos los vóxeles (el `ct-dvh` de penhmain deja fuera el de más dosis). Con varias
    ejecuciones, se recalcula con la dosis combinada.
* **Espectrometría** (`spectro.py`):
  * resolución del detector como mcdet: gaussiana de FWHM(E) − ΔE, con la ΔE de cada intervalo (también en
    intervalos logarítmicos), y cola exponencial; PIPS, Si(Li), CdTe, NaI o a medida;
  * ventanas de energía con fondo lineal, centroide y anchura, en la unidad con que se ve el espectro;
  * coeficientes de conversión (fluencia → kerma, dosis ambiental…) de una tabla.
* **Materiales:** las tablas de `material.dat` y una calculadora de alcance CSDA, poder de frenado y μ y
  capa hemirreductora de fotones.

### 8. Qué resultados pedir (`penhan/goals.py`)

* **Por objetivos** (Resultados → «Elegir qué resultados pide el .in…»): seguir la simulación, dosis en 3D
  (caja, cilindro o esfera), espectro, fluencia, espacio de fases, energía depositada, ángulos y trayectorias.
  Cada uno dice qué hay ya en el .in; los cambios van en un solo paso de deshacer.
* **Las reglas de penhmain:**
  * un cuerpo, en un solo detector de impacto y en uno de energía;
  * ningún detector en un cuerpo vacío;
  * un solo detector con espacio de fases (IPSF = 1);
  * fluencia y espacio de fases no comparten fichero.
* **Rejillas por defecto:**
  * caja: toda la escena;
  * cilindro y esfera: la mayor que cabe si el eje o el origen están bien dentro de la escena; si no, la que
    la cubre.

### 9. Simulaciones en paralelo y campañas (`penhan/batch.py`)

* **ARGUS no lanza penhmain:** prepara las carpetas y las órdenes, y la ventana de resultados sigue las
  ejecuciones.
* **Cada ejecución, en su carpeta** (`run-01`…; en una campaña, `v01-SPOSIT_-5`… y, con réplicas,
  `v01-…/run-01`):
  * `RSEED -i 0`: pares de semillas precalculados y distintos;
  * las lluvias repartidas;
  * `DUMPTO dump.dmp` y `DUMPP` de al menos 30 s (lo mínimo que acepta penhmain);
  * sin `RESUME`, con una nota;
  * los ficheros que necesita (como la carpeta de simulación);
  * nunca se sobrescribe nada.
* **Órdenes para lanzarlas:**
  * `lanzar.sh`: Linux, macOS o WSL, con `xargs -P`;
  * `lanzar.bat` y `lanzar.ps1`: Windows; los penhmain de Linux, con WSL. `Start-Process` recibe una sola
    cadena ya entrecomillada. El .ps1 va en UTF-8 con BOM y con fin de línea de Windows (PowerShell 5.1);
  * `combinar.sh` y `combinar.ps1`: combinan con penhmain-sum. No hace falta para verlas en ARGUS.
* **Índices** (`argus-paralelo.json`, `argus-campana.json`): la ventana de resultados encuentra las
  ejecuciones aunque no hayan empezado, y da el estado de todas juntas.
* **Campañas:**
  * uno o dos parámetros: energía, posición, apertura, `PARINP`, un material o cualquier palabra clave con
    su índice;
  * valores «50 100», «A:B:C», «A-B/N», con coma decimal;
  * réplicas y, si se quiere, las mismas semillas en todas las variantes.
* **Ver una campaña:**
  * «Comparar las N variantes»: cada resultado de todas a la vez (rampa azul si el parámetro es un número;
    sin los histogramas dosis-volumen que calcula ARGUS, que se ven en cada variante);
  * «Medidas frente al parámetro»: una medida de cada variante con su ajuste (lineal, parábola, potencia;
    mínimos cuadrados ponderados, centrados para que sean estables);
  * cada variante con su propia geometría del caso: su .in puede cambiar la geometría o los materiales, y con
    ellos las masas y los histogramas.

### 10. Revisión

Una revisión a fondo de lo nuevo (preparar y lanzar, análisis y ventana de resultados) encontró estos
fallos, corregidos cada uno con su prueba:

* **Órdenes:** las de WSL no funcionaban; faltaba el BOM del .ps1; la ruta relativa de penhmain-sum; los
  errores de PowerShell.
* **Análisis:**
  * ajuste de parábola inestable lejos del origen;
  * unidades mezcladas entre curvas superpuestas y entre variantes;
  * escenas finas tomadas por infinitas, cuerpos pequeños con masa 0, la masa del TAC descartada;
  * la corrección de mcdet en intervalos logarítmicos;
  * un segundo detector con espacio de fases; el radio de la esfera por defecto;
  * el .ct fuera del sello del caso; la coma decimal; tablas sin filas.
* **Ventana:**
  * inservible tras cerrarla; el cursor de espera; errores que se perdían; datos de la carpeta anterior;
  * el espectro convolucionado de otra curva; el plano de un mapa anterior al exportar una esfera; la unidad
    de las ventanas de energía;
  * gamma sin los perfiles de la ventana principal; el árbol que se cerraba y se leía dos veces;
  * «partícula» en las unidades de N partículas;
  * el presupuesto de trayectorias de una sola partícula;
  * preparar otra vez en otra carpeta;
  * la geometría y el estado de cada variante de una campaña.

### Límites conocidos

* **DVH:** por cuerpo o por tejido, no por estructuras dibujadas (RTSTRUCT).
* **Vista 3D:** la dosis no se dibuja sobre el plano de corte (solo en los cortes 2D).
* **Lanzar:** ARGUS no lanza ni para penhmain.
* **WSL:** las órdenes para el penhmain de Linux en Windows se han comprobado aquí, no en un Windows real.

## Consecuencias

* **Nueva dependencia:** matplotlib, para las gráficas y su exportación; pyqtgraph no exporta bien a PDF y SVG
  con PySide6 6.11.
* **Corpus:** `tests/corpus/resultados`, con salidas reales de penhmain de casos pequeños generados en la nube:
  caja, cilindro, esfera (fuente radiactiva), TAC, tres ejecuciones en paralelo con su penhmain-sum, y una
  campaña de tres variantes con dos réplicas cada una.
* **Comprobado de principio a fin** con penhmain y penhmain-sum reales: preparar, lanzar con `lanzar.sh`,
  seguir y combinar.
