# ADR-021 · Novena revisión: todo ARGUS (0.16.3)

**Estado:** aceptada (versión 0.16.3)

## Contexto

Rafael pidió (15-10-2026) otra vuelta en busca de fallos, esta vez por todo ARGUS. Se hizo:

* análisis estático de todo el código (ruff, pyright y vulture);
* seis revisiones en paralelo, cada una con su parte: lectura de resultados y su ventana; importador de TAC y sus
  vistas; núcleo de PENGEOM y editor; lectura y comprobaciones del `.in`; ejecutar, guiones, Windows y WSL; ventana
  principal y pestaña «Geometrías complejas». Con los oráculos de PENGEOM y PMRDR, el penhmain de Rafael, penhmat,
  tres series de TAC públicas, sus 127 casos y sus tres casos de prueba;
* una prueba de mono con otra semilla y las vistas 3D.

Salieron 51 hallazgos. Cada uno se reprodujo antes de corregirlo y tiene su prueba (`test_review9_*.py` y
`test_gui_review9_*.py`).

## Decisiones

### 1. Resultados (12)

* **El material del TAC en `geometry.rep`.** PENGEOM escribe MAT con I3 y el 9999 del TAC sale como `***`: el árbol
  de la geometría no se leía y la comparación con la geometría abierta fallaba (sin DVH por tejido). Ahora `***` es
  «un material que no cabe en I3» y solo empareja con materiales fuera de −99…999.
* **«Terminada por TIME».** TIME cuenta desde TSECIN, después de PEINIT/PHINIT, y el primer volcado parcial no llega
  hasta DUMPP segundos de tiempo total (penhmain pone TSECAD a 0 al empezar): con protones, la inicialización (minutos)
  hacía que ARGUS diera la simulación por terminada y estimara «209,5 días». Ahora solo se da por terminada si es
  seguro (el tiempo de CPU ha llegado a TIME, o el resumen es el final), lo que queda se estima con la velocidad de CPU
  y entre volcados, TIME < 1 es 100 s y NSIMSH < 1 es 2·10⁹ (como penhmain). «Ejecutar» ve esa simulación en marcha.
* **Medidas.** Las curvas medidas ya no se multiplican por N (o A·t); χ² y la diferencia media se calculan en los
  puntos medidos (`compare.paired`); un CSV de una hoja de cálculo en español con la coma decimal entre comillas se
  lee bien.
* **Una simulación repetida con menos intervalos** se enseña (antes se quedaba la curva vieja).
* **Espectrometría (PIPS).** La cola exponencial ya no desborda con canales anchos; los errores de cuenta salen en el
  panel.
* **«Pico de Bragg».** El sentido del haz sale del extremo en el que la curva cae a cero; R90/R80/R50 y las caídas,
  desde el extremo distal; con un haz hacia −z da lo mismo que hacia +z.
* **La energía exacta de la fuente** (el intervalo de anchura cero de `psource.dat`) es una línea con su probabilidad,
  que cuenta en la energía media, el área, la convolución y los coeficientes.
* **Exportar CSV:** cada columna con su unidad, la curva con su carpeta o variante, las probabilidades de las líneas
  aparte y UTF-8 con BOM.
* **«% del valor en un punto»** fuera de la curva o con valor 0: no se normaliza y se dice.
* **Fuentes radiactivas:** «desintegraciones» en los textos.

### 2. TAC (6)

* **Vóxeles de vacío por el promedio parcial.** Con materiales que ya estaban en el `.in`, un tejido que solo aparece
  tras remuestrear se volvía vacío (264 vóxeles en una serie real). Ahora solo es vacío lo que se pide; si en el `.in`
  no cabe el tejido nuevo, no se escribe nada y se dice cuál y qué hacer.
* **Un material del `.in` reutilizado por el TAC** (el agua, el aire de la sala) se tomaba por un tejido del TAC
  (densidad y composición de la calibración). La ficha marca esos materiales (`"material_del_in": true`; nota en
  ADR-009, punto 4) y se quedan como los del `.in`; las fichas viejas se leen como antes, salvo un material de la lista
  de PENELOPE. Dos tejidos con el mismo `.mat` nuevo se rechazan (antes salían con una densidad equivocada).
* **El diálogo de importar** ya no falla al cambiar la calibración o la curva con la serie leída, y los textos de los
  materiales siguen al plan.
* **Curvas en cp1252** (Excel en español) se leen; los avisos de la calibración, con coma decimal; «no cabe»: el
  porcentaje del volumen que sobresale, bien calculado; buscar los tejidos del recorte, 10 veces más rápido.

### 3. PENGEOM y editor (5)

* **Convertir en copia independiente un clon que otro CLONE copia** quitaba del `.in` las líneas de cuerpos que
  seguían existiendo (las copias del segundo clon): `renumber_map` aplica ahora el cambio de identidad también dentro de
  las copias de un clon.
* **«Convertir en módulo» y «Envolver»** con un limitante de un fichero INCLUDE*: sus propios limitantes se quedaban
  fuera del módulo y GEOMIN les daba madre sin hacerlos hijas (LOCATE ya no los encontraba; PENGEOM real: materiales
  `[3,2,1]` → `[2,2,1]`). Ahora el cierre de limitantes sigue a los cuerpos del INCLUDE* y, por si algún limitante no
  se pudiera declarar desde el fichero principal, `closure_problem` lo compara con lo que tiene GEOMIN y, si falta
  alguno, no se hace y se dice.
* **Etiquetas con sus cuatro columnas.** PENGEOM distingue «   1» de «1   »; el editor las comparaba sin blancos:
  quitar «1   » sacaba del módulo a «   1». Ahora se comparan como PENGEOM (`label_key`) al buscar, quitar, cambiar
  la etiqueta, duplicar, envolver y en el panel Editar.
* **Duplicar** dice si un valor que usaba PARINP es un número en la copia (el aviso se generaba y se perdía).
* Plural: «conviértalos en copias independientes».

### 4. El `.in` (8)

* **Nombres que penhmain no encontraría.** GEOMFN, MFNAME, IPSFN, EPROF y RESUME se leen con `(A20)` desde la columna 8
  y se abren tal cual: con un blanco delante, penhmain crea un fichero vacío con ese nombre y se para (con RESUME,
  empieza desde cero y sobrescribe los resultados). Ahora es un error en las comprobaciones (un aviso con RESUME) y un
  problema que impide «Ejecutar». Un nombre con tildes o eñes es un aviso: el `.in` va en Latin-1 y un penhmain de
  Linux o de macOS (también con WSL) buscaría otros bytes.
* **SRNUCL sin nombre** (`SRNUCL ''`): IRETRN 3120, como PENNUC0.
* **Mensajes de parada:** un tabulador en las 6 primeras columnas se dice (antes «'TIME' no es una palabra clave»);
  si falta MFNAME o GEOMFN pero están más abajo, se dice qué línea está fuera de su sitio; el BOM de UTF-8 se nombra.
* **La fuente que penhmain no llega a leer** (el `.in` se para antes o en la fuente) no se enseña ni se edita: eran
  valores por defecto y cambiar uno sustituía lo escrito (las 13 líneas SPECTR, SPOSIT…). `model.source_read` dice
  si se ha leído entera.
* **Editor de espectros:** las filas con un valor no válido se quedan al cambiar la unidad u ordenar; normalizar las
  pide corregidas; «Pegar» toma la unidad de la cabecera («E (keV)»).
* **Marcar o desmarcar un cuerpo** (fuente extensa y detectores) no reescribe las líneas de los demás: se quedan como
  estaban escritas (por número o por etiqueta) y en su orden.
* Plurales («1 intervalo», «el cuerpo marcado») y las etiquetas sin sus blancos en los mensajes de los objetivos.

### 5. Ejecutar, guiones y Windows (13)

* Los nombres con un blanco delante y, con un penhmain de Linux o de macOS (también con WSL), las rutas de Windows
  («C:\…», «mats\agua.mat») y las tildes son problemas de «Ejecutar», de la carpeta de simulación, de la cascada y de
  las simulaciones en paralelo (`simfolder.classify(…, linux=True)`).
* **Cascada con WSL:** un PID apuntado que ya es de otro proceso de Linux no se da por la ejecución ni se mata (se
  comprueba que sigue en la carpeta de la ejecución, como en Linux); una ejecución que lanza otra sesión de ARGUS no
  se lanza otra vez (los cortes sí pueden repetirse: dan lo mismo).
* **Guiones retocados a mano** (`argus-ejecutar.sh/.bat`): cualquier cambio respecto al de ARGUS (no solo líneas que
  no empiezan como las suyas) se guarda en «.bak» antes de sustituirlo.
* **«Cancelar» durante la copia** ya no ejecuta penhmain si la copia acababa de terminar.
* **WSL no se pregunta en el hilo de la interfaz** (la primera vez tarda lo que tarde en arrancar): «Ejecutar» enseña
  «comprobando…» y la cascada lo pregunta en su hilo.
* **Simulaciones en paralelo** con un penhmain que es un guion con «#!»: WSL, como lo ejecuta ARGUS; `combinar.bat`
  para la política de guiones de Windows.
* **Resultados en una subcarpeta** («IDPSF salida/psf.dat») también se guardan en «anterior-…».
* **penhmat:** si no puede leer un fichero de la base de datos se dice cuál (antes «ha pedido algo que ARGUS no
  sabía») y la línea del error de Fortran sale aunque no esté al final.

### 6. Ventana principal (7)

* **«Recargar»** un fichero que otro programa ha cambiado vuelve a abrir solo ese, si los cambios sin guardar del otro
  no dependen de él (si el `.in` sigue a cambios de la geometría, se pregunta nombrando el otro fichero). Antes se
  perdían sin decirlo.
* **Nombres con tildes** no valen para GEOMFN: «Nueva geometría…», «Guardar la geometría como…» y «Nuevo .in» lo dicen
  (como ya hacían los INCLUDE, el `.ct` y los `.mat`).
* **«Manual edit»** que pasa por un texto que PENGEOM no acepta: al arreglarlo, el `.in` sigue desde la última
  geometría que PENGEOM aceptaba. Si quita un cuerpo que nombra el `.in`, el `.in` sigue a los demás y las líneas de
  ese se quedan como estaban (escrito a mano no se sabe si se ha borrado o ha cambiado de etiqueta) y se dice.
* **«Nuevo .in»** ya no tira lo escrito sin aplicar en «Geometrías complejas».
* **Al guardar:** el error nombra el fichero, no el temporal de ARGUS, y con un permiso denegado dice que puede tenerlo
  abierto otro programa (penhmain en Windows); si la carpeta ya no está, lo dice.
* Textos: «hay cambios sin aplicar», «definida», concordancias y Relabel cuando no cambia nada.

## Datos de penhan y PENGEOM verificados en esta revisión

* penhmain lee GEOMFN, MFNAME, IPSFN, EPROF y RESUME con `READ(BUFFER,'(A20)')` desde la columna 8 y los abre tal
  cual (con los blancos del principio); SRNUCL se lee en formato libre y PENGEOM quita el blanco del principio de los
  nombres de los INCLUDE y del `.ct`.
* TIME cuenta desde TSECIN, después de PEINIT/PHINIT; «Effective (CPU) simulation time» no incluye la inicialización;
  penhmain pone TSECAD a 0 al empezar y no escribe ningún volcado parcial antes de DUMPP segundos de tiempo total.
* PENGEOM escribe MAT con I3 en `geometry.rep` (el 9999 del TAC sale como `***`) y distingue las etiquetas por sus
  cuatro columnas.

## Lo que queda

* Dos sesiones de ARGUS con la misma cascada: cada ejecución se lanza una vez, pero los cortes los pueden hacer las
  dos (el resultado es el mismo).
* `ARGUS.bat`: arrastrar un fichero cuya ruta tiene «&» o «^» y ningún espacio no llega bien (cmd parte la línea antes
  de llegar al guion; el Explorador solo pone comillas con espacios). Se abre desde ARGUS.
* Un caso dentro del Linux de WSL (`\\wsl.localhost\…`) se toma por una carpeta de red.
* Con un solo volcado, la cuenta atrás de TIME aún supone una inicialización corta; con ruido de un 5 % por
  intervalo, R90 se va más de 3 mm en un 20 % de los casos (era el 94 %).
* El CSV exportado usa coma como separador (Excel en español lo abre con «Datos › Desde texto/CSV»).
* Las fichas de TAC anteriores a 0.16.3 no dicen qué materiales eran del `.in`: uno con un nombre libre se sigue
  tomando por un tejido.

## Verificación

* `tests/test_review9_results.py` y `tests/test_gui_review9_results.py` (21): `***`, la inicialización y TIME, las
  medidas, menos intervalos, PIPS, el pico de Bragg en los dos sentidos, la energía exacta, el CSV, el punto de
  normalización y las desintegraciones.
* `tests/test_review9_ct.py` y `tests/test_gui_review9_ct.py` (22): los tejidos que aparecen al promediar, los
  materiales del `.in`, las fichas viejas, dos tejidos con el mismo `.mat`, el diálogo, las codificaciones y el
  porcentaje que no cabe.
* `tests/test_review9_pengeom.py` y `tests/test_gui_review9_pengeom.py` (15): el clon de un clon, convertir y envolver
  con un INCLUDE* (también frente a PENGEOM real), las etiquetas por columnas (también frente a PENGEOM real) y los
  avisos de PARINP; `editor_fuzz.py` con PENGEOM real (24 cadenas con eliminaciones y 30 sin ellas): 0 problemas.
* `tests/test_review9_input.py` y `tests/test_gui_review9_input.py` (18): nombres con blanco y con tildes, SRNUCL,
  los mensajes de parada, `source_read`, los cuerpos activos, la fuente que no se lee en la ventana, el editor de
  espectros y los cuerpos de un detector.
* `tests/test_review9_run.py` (25): nombres, rutas y tildes con un penhmain de Linux, el PID de WSL con un `wsl.exe`
  de mentira, dos sesiones, los guiones retocados, la subcarpeta, `combinar.bat`, penhmat, «Cancelar» durante la copia
  y WSL fuera del hilo de la interfaz.
* `tests/test_gui_review9_window.py` (12): recargar uno u otro (y preguntar si están enlazados), los nombres con
  tildes, «Manual edit» por un texto roto y quitando un cuerpo, lo escrito en la pestaña compleja, los errores al
  guardar y los textos.

## Consecuencias

* Lo que ARGUS da por bueno para ejecutar es lo que el penhmain que se va a usar encontraría (también el de Linux de
  Rafael con WSL).
* Un `.in` que penhmain no lee entero no se puede estropear desde los paneles.
* El editor y «Manual edit» mantienen el `.in` nombrando los mismos cuerpos en más casos, y dicen cuándo no pueden.
