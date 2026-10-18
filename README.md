# ARGUS

**Advanced Radiation Geometry & User-interface System**: visor y constructor de
geometrías PENGEOM (`.geo`) y de ficheros de entrada de penhan (`.in`) para
PENELOPE. Como Argos Panoptes, el de los cien ojos, su objetivo es que se vea
todo lo que hay en una simulación antes de lanzarla.

ARGUS construye, lee, visualiza y modifica los ficheros, prepara las carpetas
donde se simula (también muchas a la vez, en paralelo o en campañas), **ejecuta
el penhmain del usuario** en la carpeta del `.in` (en un terminal, con el botón
«Ejecutar») y muestra y analiza lo que devuelve, mientras simula y después. Con
el mismo penhmain simula aparte la **cascada de partículas**: las trayectorias
reales de las primeras lluvias, en 3D y animadas. ARGUS no incluye, modifica ni
compila penhmain ni penhmat: usa los del usuario.

## Estado (versión 0.17.2: fases F0 a F8, los ejecutables de Windows, macOS y Linux (sin instalar Python), el `.in` completo, el editor completo, la pestaña «Geometrías complejas» con el formato de PenGeomJar, detectores con formas habituales y celdas de detectores, diez revisiones de fallos, el importador de TAC con los tejidos partidos por densidad, la carpeta de simulación, los `.mat` generados con el penhmat del usuario (de la lista de PENELOPE, que se busca al escribir, de los materiales personalizados de cada usuario o con una composición propia), los resultados de penhmain: verlos y analizarlos, simulaciones en paralelo y campañas, ejecutar penhmain en el terminal de cada sistema (en Windows, el de Linux o la consola) y la cascada de partículas en 3D, con la lluvia de cada trayectoria exacta)

| Pieza | Estado |
|---|---|
| Lector de `.geo` fiel a GEOMIN (PENGEOM 2024), con los códigos de error `IRETRN` | hecho |
| Escritor de `.geo`: sin cambios reescribe byte a byte; con cambios regenera en formato PENGEOM | hecho |
| Compilador que reproduce las tablas internas de GEOMIN (superficies, árbol de módulos, clones, `INCLUDE`, `CTBOX`, envolvente, superficies duplicadas) | hecho, idéntico bit a bit a PENGEOM en las 43 geometrías del corpus |
| `LOCATE` vectorizado (qué cuerpo hay en cada punto) | hecho, idéntico a PENGEOM (puntos aleatorios y sobre superficies) |
| Visor 2D de cortes con árbol de cuerpos y propiedades | hecho |
| Lectura y escritura del `.in` (orden de palabras clave, formato libre, etiquetas de cuerpo) | hecho, verificado contra el lector real de penhmain 2025 |
| Fuente en el corte: puntual o extensa (caja y cuerpos activos), flechas de emisión (lápiz, cono, isótropa, ventana θ-φ) y rejilla de dosis | hecho |
| Edición de la fuente: arrastrando en el corte o en el panel «Fuente», con deshacer y guardado (copia `.bak`) | hecho |
| Nuevo `.in` para una geometría | hecho |
| Visor 3D: trazado de rayos sobre las cuádricas en la GPU (OpenGL 4.1, Intel UHD) o en la CPU, con la fuente, cortes, transparencias y selección | hecho, verificado contra `STEP` de PENGEOM |
| Editor de geometría: material, nombre, superficies y módulos; añadir esferas, cilindros, cajas y láminas; eliminar cuerpos; deshacer y guardar (`.geo.bak`) | hecho, verificado con PENGEOM real |
| Módulos: crearlos, pegarlos cara con cara, moverlos y convertir cuerpos en módulos | hecho, verificado con PENGEOM real |
| Editor completo: geometría nueva desde cero; girar y mover módulos y clones con el ratón (corte 2D) o por su centro; duplicar (copias independientes o clones `CLONE`); «envolver» cuerpos en un módulo nuevo; etiquetas; lados de las superficies; añadir o quitar superficies (planos y cuádricas implícitas); coeficientes de la forma implícita | hecho, verificado con PENGEOM real (tablas y `LOCATE` idénticos en más de 2200 ediciones al azar) |
| El `.in` sigue a la geometría: al renumerar PENGEOM los cuerpos (o al cambiar etiquetas o eliminar cuerpos), las referencias del `.in` se actualizan en el mismo paso de deshacer | hecho, verificado con el PMRDR real |
| Búsqueda de solapes (cuerpos que comparten volumen sin ser limitante uno del otro, hijas que asoman fuera de su módulo), que PENGEOM no comprueba | hecho |
| Materiales con nombre (fichero `.mat` de cada `MFNAME` y descripción), colores de la vista y `materiales.txt` | hecho |
| Todo el `.in` en formularios (panel «Simulación»): parámetros de los materiales, `PARINP`, parámetros por cuerpo, campos, reducción de varianza, partículas emergentes, detectores, rejilla de dosis, proceso externo y trabajo | hecho, verificado contra el lector real de penhmain 2025 |
| Editor de espectros (`SPECTR`/`SLINES`) con la gráfica de lo que muestreará penhmain; espectros desde un fichero (formato de ARGUS, tablas, líneas de otro `.in` o un espectro de penhmain) con vista previa y agrupados a 250 filas | hecho, verificado con los contadores de volcados reales |
| Comprobaciones de penhmain (PMRDR) de todas las secciones, con sus `IRETRN`, y de los volcados `RESUME`/`DUMPTO` | hecho, verificado contra el PMRDR real (sonda `argus_simprobe`) |
| Revisión de fallos de la 0.7 (ADR-008): arrastres interrumpidos, valores tecleados sin confirmar, la rueda del ratón sobre campos, Numba en Windows sin TBB ni OpenMP, más de 248 hijas en un módulo, comas en campos numéricos, «…» en comentarios, cambios que afectaban a otros cuerpos, láminas finas, hojas de cálculo en español | hecho, cada fallo con su prueba |
| Importar un TAC (DICOM) como caja de vóxeles (`CTBOX`): series axiales, coronales, sagitales, oblicuas y TAC «mejorados»; recorte, tamaño de vóxel (promedio por volumen), calibración HU → material y densidad (Schneider 2000, 5 tejidos con 17 isótopos ENDF por defecto; editable; curva propia en CSV), colocación y `.ct` en el formato de PENGEOM, con los materiales en el `.in` y los pasos de `penhmat` en `materiales.txt` (ADR-009) | hecho, verificado con PENGEOM real (`CTLOCATE`: mismo vóxel, material y densidad) |
| Vóxeles del CT en los cortes 2D (color del material, más claro cuanto más denso) y en 3D (GPU y CPU, con materiales ocultos o translúcidos y cortes) | hecho, la GPU verificada contra la CPU |
| Preparar la carpeta de simulación: copia el `.in`, la geometría y sus `INCLUDE`, el `.ct`, los `.mat`, los ENDF que listan, los ficheros de una fuente radiactiva o de espacio de fases, `EPROF`, `RESUME` y el ejecutable de penhmain; nunca sobrescribe | hecho |
| Revisión de fallos de la 0.8 (ADR-010): el TAC con el editor (módulos que lo contienen, `PARINP`, material 9999, eliminarlo), series DICOM reales (localizadores, duplicados, relleno fuera del campo, un corte), «Como el TAC» exacto, curvas de hojas de cálculo, pasos de penhmat completos con la densidad media de los vóxeles, escrituras que nunca quedan a medias, la carpeta de simulación (nunca sobrescribe, ni con carreras; cancelar; rutas absolutas; DLL; espacios de fases), vista ortográfica y cortes sobre planos de vóxeles, valores a medio escribir en los paneles, `Inf`/`NaN`, números con punto en español | hecho, cada fallo con su prueba; penhmat y penhmain reales |
| Tercera revisión (ADR-011): lectura como gfortran (retornos de carro sueltos, repeticiones, también entre tipos distintos, nombres A20, SRNUCL, `PARINP`, valores nulos, coma decimal), `INCLUDE` y `.ct` en la carpeta del `.in` (también el `.ct` importado y al guardar el `.in` en otra carpeta), referencias a cuerpos como PMRDR (0 y etiquetas en blanco), guardar el `.geo` y el `.in` juntos o nada, aviso de los `.in` que nombrarían otros cuerpos (también otros `.in` de la carpeta), cambios hechos por otro programa y «Recargar» de lo que ha cambiado, mover módulos que comparten superficies, añadir cuerpos que rodean a otros o fuera de la envolvente, clones, láminas finas, cuerpos ocultos y selección tras editar, atajos, arrastres interrumpidos, errores inesperados en un registro | hecho, cada fallo con su prueba; sondas compiladas con `-Os` como penhmain |
| TAC y CTTRACK (ADR-011): cada tejido se parte al importar en varios materiales por densidad (misma composición, densidad media de sus vóxeles) con los materiales libres del `.in`, y se estima el error de alcance que queda; Opciones → «Mi penhmain tiene corregido CTTRACK» lo desactiva. Energía media de excitación con una regla general (Bragg con valores de fase condensada de ICRU 37 y el agua en 78 eV) | hecho; en tres TAC de tórax reales, p95 del error de 13–26 mm a 2–4 mm |
| Detectores (ADR-012): cuadrado, rectángulo, círculo, triángulo, pentágono y hexágono con su altura, y en el `.in`, en el mismo paso de deshacer, su detector de penhmain (energía depositada o impacto, nuevo o uno que ya existe), con los límites de penhmain (5 detectores de cada tipo, 100 o 50 cuerpos) | hecho, verificado con PENGEOM y penhmain reales |
| Celdas de detectores (ADR-012): una caja llena de detectores iguales en filas, columnas y capas (en cuadrícula, en panal, al tresbolillo...), con hueco, pared y materiales; uno o varios detectores de penhmain (toda la celda, por fila, columna o capa, o uno por detector); se organiza sola en módulos para respetar los límites de PENGEOM y escribe una sola superficie por plano | hecho, verificado con `STEP` y `LOCATE` de PENGEOM real en 235 celdas y con penhmain real |
| Cuarta revisión (ADR-012): superficies distintas en el mismo plano entre un módulo y una hija (`STEP` puede no ver la pared; se avisa, y «Envolver» deja 1e-5 cm), más de 248 superficies en un elemento del nivel superior, fuente radiactiva y `EPROF` como PMRDR, ficheros de salida, columna 127, volcados grandes, cancelar el TAC a medias, diálogos del editor que prueban el cambio antes de cerrarse, menús y diálogos que se quedaban en memoria, `Ctrl+Y`, números como «1e3» y «1.000,5» | hecho, cada fallo con su prueba |
| Programas de penhan del usuario (ADR-013): Opciones → «Programas de penhan…» (carpeta de penhan, penhmat y penhmain, o «no hace falta»); ejecutables de Windows, Linux o macOS reconocidos por su contenido, y los de Linux en Windows con WSL; «Generar .mat y ENDF…» genera con su penhmat los `.mat` que faltan (materiales de la lista de PENELOPE, con composición propia y tejidos del TAC) y copia los ENDF que necesitan a la carpeta donde se simula, sin sustituir nunca un fichero | hecho, verificado con el penhmat real (Linux); WSL sin probar en Windows |
| Resultados de penhmain (ADR-014): una ventana con todas las salidas, reconocidas por su contenido (curvas, mapas, resumen, trayectorias, espacio de fases, datos de los materiales); se actualiza sola mientras penhmain simula; unidades y normalización (Gy, por partícula o por desintegración, para N partículas o una actividad); exportar figuras (PDF, SVG, PNG) y datos (CSV) | hecho, verificado con penhmain y penhmain-sum reales |
| Análisis (ADR-014): medidas en curvas y perfiles con su incertidumbre (pico de Bragg, dosis en profundidad, perfiles, espectros, DVH), comparación con otras simulaciones y con medidas (diferencia, cociente, χ², índice gamma 1D y 2D), dosis media por cuerpo, histogramas dosis-volumen por cuerpo y por tejido del TAC, espectrometría como mcdet (resolución del detector, ventanas de energía, coeficientes de conversión) | hecho |
| Resultados sobre la geometría (ADR-014): el mapa de dosis con isolíneas en los cortes 2D, perfiles en cualquier recta del corte y las trayectorias de LTRACK en 2D y 3D | hecho |
| Qué resultados pide el `.in` (ADR-014): por objetivos (dosis en 3D, espectro, fluencia, espacio de fases, energía depositada, ángulos, trayectorias, seguir la simulación), con las reglas de penhmain | hecho |
| Simulaciones en paralelo y campañas (ADR-014): carpetas con semillas independientes (como MultiRuns), variantes que cambian uno o dos parámetros, órdenes para lanzarlas en Windows, Linux, macOS y WSL, y la ventana de resultados que las sigue, las combina y compara las variantes (una medida frente al parámetro, con su ajuste) | hecho, verificado con penhmain y penhmain-sum reales (Linux); WSL sin probar en Windows |
| Quinta revisión (ADR-016), con un entorno igual al del PC (Python 3.12, numpy 2.5, matplotlib 3.11) y las 87 carpetas de resultados de `SOBP`: casos distintos en una carpeta ya no se combinan como réplicas (se comprueba que sea el mismo caso con semillas distintas), copias renombradas de un resultado, medias del resumen como penhmain-sum, ficheros a medio escribir en un volcado, resultados por cuerpo con una geometría cambiada después de simular, gamma exacto en 1D y 2D, una fuente extensa cuya caja no toca los cuerpos activos (penhmain no terminaría), la energía exacta del SOBP al importar `psource.dat`, campañas que no cambiaban lo que decían, relanzar un lote encima de sus resultados, rutas de Windows (espacios, paréntesis, red) | hecho, cada fallo con su prueba |
| Ejecutar penhmain (ADR-017): «Ejecutar» (F9) copia el penhmain elegido a la carpeta del `.in` (sin sustituir otro sin preguntar) y lo que falte (ENDF…), guarda los resultados anteriores en «anterior-…», no ejecuta con errores del `.in`, abre un terminal con `penhmain < caso.in` (en Windows, el de Linux con WSL o la consola de Windows; Linux; macOS) y la ventana de resultados, que la sigue | hecho, verificado con el penhmain real (Linux); WSL sin probar en Windows |
| Cascada de partículas (ADR-017): las primeras lluvias (1000 por defecto) simuladas con el mismo penhmain que la simulación en ejecuciones de 50 (lo que guarda penhmain) en paralelo, la primera idéntica a la simulación; también con una fuente radiactiva (`SRNUCL`) o un espacio de fases (`IPSFN`); reconstrucción de cada lluvia (madre, generación, tiempo de vuelo); visor 3D sobre la geometría translúcida (GPU o CPU), lluvias, partículas, colores, animación en el tiempo real de vuelo y exportar PNG, MP4 y GIF | hecho, verificado con el penhmain real (electrones, fotones, protones, Co-60 y espacios de fases) |
| La lluvia de cada trayectoria, exacta (ADR-017, 0.13.2): sin adivinar nada; cuando los `.trk` no lo dicen (sobre todo con una fuente radiactiva puntual), la misma ejecución repetida con las mismas semillas parando tras b lluvias («cortes»: sus `.trk` son el principio exacto de los de la ejecución entera); la vista «Trayectorias» de la simulación separa también lo que penhmain pega | hecho, comparado con un penhmain que escribe la lluvia de cada trayectoria: 11 574 de 11 574 en 10 casos (Co-60 puntual y extensa, en la materia y en el vacío, I-125, espacios de fases, haces de electrones, fotones y protones) |
| Materiales con composición propia (ADR-018, 0.14.0): además de un material de la lista de PENELOPE (opción 2 de penhmat), la composición que se le dé (opción 1): fórmula química o elementos con su cantidad (átomos por molécula o fracción en masa), densidad y energía media de excitación (regla de ARGUS, la que calcula PENELOPE u otro valor); se guarda en el `.in` junto a su `MFNAME` (comentarios que penhmain salta), «Generar .mat y ENDF…» la usa, `materiales.txt` da las respuestas y se avisa si el `.mat` que hay no la tiene | hecho, verificado con el penhmat real: elementos, proporciones, densidad e I idénticos en 9 composiciones |
| El terminal al ejecutar (ADR-018, 0.14.0): la simulación se ve al momento en el terminal de cada sistema; en Windows se elige el terminal de Linux (WSL) o la consola de Windows: un penhmain de Linux, en el de Linux; uno de Windows, en los dos (en el de Linux, WSL lo ejecuta y la salida queda también en `penhmain.out`) | hecho; WSL sin probar en Windows |
| «Trabajo» (ADR-018, 0.14.0): «Partículas» (`NSIMSH`), el tiempo máximo con unidades (s, min, h, días, semanas; hasta 10⁹ s) y, al ejecutar, «Lluvias»: las primeras que se siguen para verlas en 3D (la cascada, como mucho 1000) | hecho |
| «Geometrías complejas» (ADR-019, 0.15.0): una pestaña con el formato de las «Edition Tabs» de PenGeomJar (Surface, Surface (implicit), Body, Module, Clone, Include y CT, con Delete, Modify, New, Add/Duplicate y Cancel, Relabel y Manual edit) junto al texto del `.geo` y a la geometría en 2D y 3D, que se ve en tiempo real (también lo que se escribe, antes de aplicarlo); cada cambio, un paso de deshacer, solo si PENGEOM lo aceptaría | hecho, verificado con PENGEOM real (tablas y `LOCATE` idénticos tras cadenas de cambios al azar); Modify sin cambios deja el fichero igual byte a byte en todo el corpus |
| Séptima revisión (ADR-019, 0.15.1): la pestaña «Geometrías complejas» (los cuerpos de un fichero incluido dos veces o con `INCLUDE*`, Relabel y mover bloques que cambiarían la geometría, valores y nombres que se reescribían sin tocarlos, Manual edit frente a las ediciones de otros sitios y a «Seguir editando», lo escrito sin aplicar que se perdía sin preguntar, las líneas del `.in` que se quitan al reescribir o borrar, PARINP sin `.in`, el File Viewer como en PenGeomJar, tablas, atajos y mensajes, y la velocidad en geometrías de miles de líneas) | hecho, cada fallo con su prueba (58, 45 de ellas fallan con 0.15.0); PENGEOM real |
| Materiales (ADR-020, 0.16.0): el campo del material despliega los 280 de la lista de PENELOPE y los acota al escribir (en español o en inglés, sin acentos, por número o símbolo), con los nombres en español en el `.in` («Agua líquida (PENELOPE 278)»; los de antes se siguen reconociendo); **materiales personalizados** de cada usuario (en su carpeta de datos: los guarda, edita, duplica para variar una aleación, borra, exporta e importa); **«Asignar materiales…»** para los de un `.in` que ARGUS no identifica (con lo que sugiere el nombre del fichero, ya propuesto); y corregido el fallo al pasar de «lista de PENELOPE» a «composición propia» y volver | hecho, con sus pruebas (46) |
| ENDF y textos (ADR-020, 0.16.0): la carpeta de los ENDF se elige en «Programas de penhan» (por defecto, `database/ENDF_data`) y desde «Generar .mat y ENDF…» si faltan; se copian de ella a donde se simula (también la cascada, las simulaciones en paralelo y la carpeta de simulación). «Trayectorias» (`LTRACK`: las primeras lluvias de la simulación en sus `.trk`; penhmain escribe una más, como mucho 51) y «Lluvias» (la cascada para verla en 3D, en tandas de las que guarda el penhmain, 50 si no se ha cambiado), explicadas donde se eligen | hecho |
| El visor de la cascada, más rápido sin cambiar nada de lo que se ve (ADR-020, 0.16.0): abrir 1000 lluvias de electrones de 10 MeV (541 217 tramos) pasa de 8–11 s a 1,4 s (volver a abrirla, 0,3 s); al girar no se recorren los datos; cada punto se sube una vez a la GPU; a la GPU solo van los tramos de lo que se ve; las partículas en vuelo de cada imagen de la animación, 6 veces más deprisa | hecho, las mismas imágenes píxel a píxel (19 estados, con GPU y sin ella) y los mismos datos en todo el corpus (62 cascadas y 340 carpetas con `.trk`) |
| La fuente extensa en un cuerpo (ADR-020, 0.16.1): con el botón derecho, en el corte o en 3D, «Fuente extensa en ▸» un cuerpo, su parte del órgano (un pulmón) o todo el órgano (los dos; los cuerpos con su misma descripción y material, como los siete trozos «BODY LUNGS» de los maniquíes): la caja justo alrededor (su caja exacta, en números redondos) y ellos, los cuerpos activos, de modo que las partículas salen solo de ellos; al añadir un cuerpo que la caja no cubre entero, la caja se ajusta; el panel «Fuente» avisa si la caja corta algún cuerpo activo y la ajusta con un clic | hecho, comprobado con el penhmain de Rafael en todos los órganos del maniquí adulto (antes, en el pulmón, salían del 45 %; ahora de todo lo elegido y solo de ahí, uniforme) |
| Octava revisión (ADR-020, 0.16.2): la caja de la fuente extensa con LOCATE, como sortea penhmain (con rayos, en los maniquíes infantiles salían cajas de metros o que dejaban fuera parte del órgano), y el límite de 200 cuerpos activos; la cascada de protones sin cortes (una alfa lenta que no se mueve en sus primeros pasos ya no pasa por madre de sus hermanas) y su firma sin depender de dónde están los ENDF; la fuente radiactiva pide los ENDF solo si el nucleido emite alfas de más de 100 keV; la carpeta de los ENDF sigue a la base de datos y se encuentran con otras mayúsculas; el `.mat` propuesto nunca es el de otro material que ya está en la carpeta; Entrar no elige la primera fila de la lista; los materiales personalizados conservan lo que no entiende esta versión y lo guardado por otro ARGUS abierto; propuestas con fórmulas químicas, códigos y unidades; «Composición propia…» dentro de «Asignar materiales…» en el mismo paso de deshacer; el visor de la cascada a salvo entre hilos. En una segunda pasada (revisores de lo cambiado y prueba de mono): la caja de cuerpos lejos de la escena o que salen de ella y de los extremos finos, el menú de los órganos de muchas piezas al momento, la firma de las cascadas de 0.16.0–0.16.1 con fuentes radiactivas sin alfas, las tandas con el penhmain que sustituye al de la carpeta, dos composiciones propias con el mismo `.mat` al asignar, la biblioteca escrita a mano o por otra versión (Z infinito, lo que no entiende, cambios del mismo tamaño, permisos al exportar), el `.mat` de otro material con un nombre sin identificar y los textos | hecho, cada fallo con su prueba; la cascada de protones del caso 2, exacta sin cortes y frente a las lluvias de la propia simulación; la caja de la fuente frente a LOCATE en los 789 cuerpos de los seis maniquíes del ORNL |
| Novena revisión, de todo ARGUS (ADR-021, 0.16.3): resultados (el material del TAC en `geometry.rep`, la inicialización que no cuenta en TIME, las medidas sin multiplicar por N, el pico de Bragg en los dos sentidos, la energía exacta de la fuente, el CSV); el TAC (vóxeles de vacío al promediar, los materiales del `.in` que reutiliza, el diálogo y las curvas de Excel); el editor (clon de un clon, convertir en módulo y envolver con un INCLUDE*, las etiquetas con sus cuatro columnas, los avisos de PARINP al duplicar); el `.in` (nombres con un blanco delante o con tildes que penhmain no encontraría, SRNUCL sin nombre, mensajes de parada, la fuente que no se lee no se edita, el editor de espectros, marcar un cuerpo sin reescribir los demás); ejecutar en Windows con WSL (un PID viejo no se mata, dos sesiones, los guiones retocados, WSL fuera del hilo de la interfaz, `combinar.bat`, la subcarpeta de resultados, penhmat), y la ventana («Recargar» solo lo cambiado, «Manual edit» con el `.in` al paso, lo escrito en la pestaña compleja, los errores al guardar) | hecho, cada uno de los 51 fallos con su prueba (113 nuevas); el editor frente a PENGEOM real (54 cadenas al azar, 0 problemas) |
| Matriz de dosis y resultados que pedir (ADR-017): el diálogo de resultados sin cerrar la ventana, cada resultado con su editor; la matriz por tamaño de vóxel (con los límites de penhmain), los vóxeles en el corte y en 3D, y estirarla o moverla con el ratón en 2D y 3D sin cambiar el vóxel | hecho |
| Sexta revisión (ADR-017, 0.13.1): la cascada (semillas repetidas, NPSHOW, fuentes radiactivas y espacios de fases, ejecuciones que mueren o siguen de otra sesión), el visor (sin GPU, exportar, números de lluvia), «Ejecutar» (consola de Windows, volcados vacíos, copias canceladas o fallidas, el mismo penhmain para la cascada, el `.bat` con tildes), la matriz (deshacer, arrastres, `RESUME` con otra rejilla, «Seguir la simulación», la rejilla en la superficie) y los textos | hecho, cada fallo con su prueba; penhmain real |
| Pantallas pequeñas (ADR-017): con la pantalla al 125 o 150 % (un portátil de 1920 × 1080 al 150 % tiene 1280 × 720 puntos) las ventanas y los diálogos caben: las filas de controles siguen en otra línea si no caben y el tamaño inicial no pasa del sitio libre de la pantalla | hecho, con una prueba que simula esa pantalla |
| Ejecutables (ADR-022, 0.17.0): Windows (instalador; por omisión solo para el usuario, sin permisos de administrador), macOS (`.dmg`, Apple Silicon e Intel) y Linux (`.tar.gz`), sin instalar Python ni uv; con PyInstaller y las versiones de `uv.lock`; `ARGUS --comprobar` abre ARGUS sin preguntar nada, prueba Numba, la ventana con una geometría, la lectura de un TAC, las gráficas (PNG, PDF, SVG, EPS...), los GIF, el vídeo y OpenGL, y escribe un informe; paquetes con contraseña si se quiere (instalador con los ficheros cifrados, `.dmg` y `.7z` cifrados); el flujo de GitHub que construye y publica los cuatro paquetes, y la revisión de que en el repositorio no haya fuentes de PENELOPE, PenGeomJar, DICOM ni compilados | hecho: Windows y Linux construidos y comprobados aquí (Windows con Wine: el ejecutable, el instalador y el de contraseña); los cuatro, construidos, comprobados y publicados en GitHub (0.17.0, 18-10-2026) |
| El desplegable, la vista 3D y «Lluvias» (ADR-023, 0.17.1): sin la carpeta de penhan (el ejecutable sin «Programas de penhan»), el desplegable del material usa la lista de PENELOPE que trae ARGUS (la misma); en 3D, la rueda acerca hacia lo que hay bajo el cursor (una superficie o una trayectoria) sin pasarse, y se gira alrededor de lo que se coge y se desplaza a su escala: lo pequeño no se pierde; «Lluvias», un solo número (de 1 a 1000) sin tandas ni carpetas, y «Trayectorias» (`LTRACK`) ya no se elige aparte; Guardar ya no pierde el nombre del material recién escrito con su lista desplegada | hecho, con sus pruebas; falta probarlo en Windows (con GPU) |
| Décima revisión, búsqueda aleatoria de fallos (ADR-024, 0.17.2): con PENGEOM y PMRDR reales (1407 `.geo` mutados, 1297 `.in` generados, cadenas del editor al azar), ficheros dañados (unos 16 000 resultados, volcados, espectros, bibliotecas, DICOM, `.ct`), una prueba de mono de la ventana y la navegación 3D: los errores de GEOMIN sin END, con un PARINP que falta y en el primer elemento; «Eliminar» ya no deja un módulo solo con una hija que asoma; fórmulas con caracteres que no son ASCII, cabeceras sin sentido, PixelSpacing 0, un NUL en el volcado, espectros que se desbordan o van al revés y un GRIDR enorme se dicen en lugar de fallar; «Ejecutar» guarda también la geometría; los diálogos del editor paran su comprobación al cerrarse; en «Trabajo», las semillas que admite RANECU y los campos que se pueden vaciar; en 3D solo se coge lo que se dibuja | hecho, cada uno de los 21 fallos con su prueba (49 nuevas; salvo tres controles, todas fallan con el código de 0.17.1) |

## Uso rápido

* **Abrir** (`Ctrl+O`): un `.in` abre también su geometría (`GEOMFN`, con sus
  `PARINP`). Un `.geo` se abre con el `.in` de su carpeta cuyo `GEOMFN` es ella
  (el que penhmain usaría con ella); si hay varios, ARGUS pregunta con cuál
  (título, partícula y energía, `PARINP` y fecha de cada uno) o si se abre sola,
  y la vez siguiente propone lo elegido. **Archivo → Abrir solo la geometría**
  (`Ctrl+Mayús+O`) la abre sin ningún `.in`. Si la geometría toma medidas de
  `PARINP` y se abre sin `.in`, se muestra con los números escritos en el propio
  `.geo`, con un aviso (penhmain usará los del `.in`); «Nuevo .in» los copia en
  las líneas `PARINP` del `.in` nuevo.
* **Vistas**: `F2` corte 2D, `F3` vista 3D, `F4` las dos lado a lado.
* **Cortes**: teclas `1`, `2`, `3` (planos XY, XZ, YZ), rueda para acercar,
  arrastrar para mover, `F` para encuadrar, `G` para ir a la fuente,
  `RePág`/`AvPág` para cambiar la profundidad del corte.
* **Fuente**: arrastre la estrella (posición), la punta de la flecha (dirección
  del haz) o el cuadrado de la esquina de la caja (tamaño de la fuente
  extensa). `Mayús` da pasos grandes y `Ctrl` pasos finos; `Esc` cancela el
  arrastre. Con el botón derecho: colocar la fuente en un punto, apuntar el haz
  hacia él o poner una **fuente extensa** («Fuente extensa en ▸»): solo en ese
  cuerpo, en la parte de su órgano a la que pertenece o en todo el órgano. Un
  órgano son los cuerpos con la misma descripción y el mismo material (en los
  maniquíes del ORNL, los siete «BODY LUNGS» de los pulmones) y sus partes, los
  que se tocan entre sí: «Este «BODY LUNGS» (4 cuerpos unidos)» es un pulmón y
  «Los 2 «BODY LUNGS» (7 cuerpos)», los dos (igual con los riñones, los brazos,
  las costillas…). La caja `SBOX` queda justo alrededor de lo elegido, que pasa
  a ser los cuerpos activos (`SBODY`): penhmain sortea puntos en la caja y solo
  acepta los que caen en ellos, así que las partículas salen de todo lo elegido
  y solo de ahí, no del resto de la caja (lo rayado en el corte). Con la fuente
  ya extensa, se marca o se quita el cuerpo como activo y «Añadir a los cuerpos
  activos ▸» añade su parte o todo su órgano; si la caja no cubre lo añadido,
  se ajusta (Deshacer la deja como estaba).
* **Vista 3D**: arrastrar para girar (alrededor de lo que se coge),
  `Mayús`+arrastrar o botón derecho para desplazar (lo cogido sigue al ratón),
  rueda para acercar hacia lo que hay bajo el cursor (una superficie o una
  trayectoria: se queda en su sitio y nunca se pasa, así que lo pequeño no se
  pierde), doble clic para girar alrededor de un punto, clic para seleccionar
  un cuerpo, `Inicio` para encuadrar. En la barra de la vista: orientaciones
  (±X, ±Y, ±Z, isométrica), proyección ortográfica y **corte** (ocultar x >
  x₀, y > y₀, z > z₀, una esquina o todo lo que hay delante del corte 2D). Con
  el botón derecho sobre un cuerpo: apuntar el haz o colocar la fuente allí
  (en la superficie o a mitad de su espesor), la fuente extensa en el cuerpo,
  en su parte del órgano o en todo él (como en el corte), llevar el corte 2D a
  ese punto, ocultar el cuerpo o hacer su material translúcido. La columna
  «3D» del panel «Materiales» cambia cada material entre opaco, translúcido y
  oculto. La fuente, su caja, el cono de emisión, la rejilla de dosis y el
  plano del corte 2D se dibujan encima; lo que queda tapado por la geometría,
  punteado. Si la tarjeta gráfica no tiene OpenGL 4.1 (escritorio remoto,
  máquina virtual), la vista 3D se calcula en la CPU; `argus --cpu3d` o Ver →
  «Vista 3D sin GPU» lo fuerzan.
* **Editar la geometría** (panel «Editar»): material, nombre, parámetros de
  las superficies del cuerpo seleccionado (escalas, ángulos, desplazamientos)
  y giro y desplazamiento de los módulos. Con el botón derecho en el corte, en
  3D o en el árbol, o con `Ctrl+B`: **añadir un cuerpo** (esfera, cilindro,
  caja o lámina) dentro del cuerpo pulsado; ARGUS comprueba antes que cabe
  entero (PENGEOM no admite cuerpos que se solapen) y lo escribe con las reglas
  de PENGEOM (cuerpo limitante, hija del módulo...). **Eliminar** (`Supr`)
  devuelve el volumen al cuerpo que lo contenía. Cada cambio se comprueba con
  el compilador verificado: si PENGEOM no lo aceptaría, no se hace. `Ctrl+Z`
  deshace el último cambio, sea del `.in` o del `.geo`.
* **Dónde va lo nuevo**: un cuerpo nuevo tiene que quedar entero dentro del
  cuerpo o módulo en el que se crea («Dentro de», que se puede cambiar por
  cualquiera de los que lo contienen, hasta la envolvente). Si no cabe, el
  diálogo dice qué parte cae en qué cuerpos y, si cabría en otro contenedor,
  lo propone.
* **Módulos**: «Añadir un módulo…» crea una esfera, un cilindro o una caja que
  después puede tener cuerpos dentro y se mueve con todo lo que tiene.
  «Añadir un módulo pegado a…» (botón derecho sobre una cara en 3D, o en el
  corte) lo crea tocando esa cara, centrado en ella. «Pegar a otro…» mueve un
  módulo existente hasta tocar la cara de otro (o, con el módulo
  seleccionado, botón derecho sobre la cara del otro: «Pegar … aquí»).
  «Convertir en módulo» hace de un cuerpo un módulo con el mismo volumen, para
  poder moverlo y pegarlo.
* **Geometría nueva** (Archivo → Nueva geometría…, `Ctrl+Mayús+N`): título,
  fichero (penhmain solo lee 20 caracteres en `GEOMFN`), y vacía o con un
  primer cuerpo o módulo (caja, cilindro o esfera, con su material). Se guarda
  al crearla y, si se pide, se crea también su `.in`.
* **Geometrías complejas** (pestaña «Geometrías complejas (PenGeomJar)» encima de
  las vistas, o Ver → Geometrías complejas, `F6`): el `.geo` con el formato de
  PenGeomJar.
  Se ocultan los paneles (vuelven al salir) y quedan el texto con los números de
  línea, las pestañas de PenGeomJar y la geometría en 2D y 3D. Al pulsar una
  línea se elige su bloque y su pestaña lo enseña: **Modify** lo reescribe,
  **New** + **Add** añade uno nuevo detrás (con un bloque elegido, «Duplicate»
  copia lo de la pestaña), **Delete** lo borra (no si otro bloque lo usa) y
  **Cancel** descarta lo escrito. Si al borrar o reescribir un bloque se
  quitarían líneas del `.in` (las de sus cuerpos), se enseñan y se pregunta; lo
  escrito en una pestaña sin aplicar tampoco se pierde sin preguntar (al elegir
  otro bloque, New, mover…). Las tablas de Body y Module (superficies con su
  lado, cuerpos y módulos) se llenan con el campo y Add; pulsar «SURFACE :» o
  «INCLUDE :» pasa a la forma con asterisco. Cada cambio es un paso de deshacer y
  solo se hace si PENGEOM aceptaría la geometría (si no, debajo del texto dice
  por qué). Con «Vista previa al escribir», lo que se escribe se ve ya en el
  texto (en naranja) y en la geometría, antes de aplicarlo. Una superficie
  elegida se dibuja en el corte (discontinua) y se resaltan los cuerpos que
  limita. **Manual edit** deja escribir el texto a mano (la geometría se ve si
  PENGEOM la acepta; «End manual edit» lo aplica, «Cancel edit» lo descarta;
  mientras tanto, la geometría no se edita desde otros sitios); también sirve
  para arreglar un `.geo` que no se puede leer. **Relabel** pone
  como etiquetas los números de PENGEOM (los de `geometry.rep` y los del `.in`).
  Bloques: `Alt+F`, `Alt+P`, `Alt+N`, `Alt+L` (primero, anterior, siguiente,
  último) y `Alt+↑`/`Alt+↓` (subirlo o bajarlo; si eso cambiara la geometría,
  lo dice y pregunta). «File Viewer», como en PenGeomJar: «Update the File
  Viewer» (`Alt+U`) copia allí el texto tal como está, una copia fija para
  tenerla delante mientras se cambia.
* **Girar y mover módulos con el ratón**: con un módulo o un clon seleccionado,
  en el corte 2D aparece un anillo morado; la cruz del centro lo mueve en el
  plano del corte y la bola del anillo lo gira alrededor del eje perpendicular
  al corte que pasa por su centro (`Mayús`: pasos de 15°; `Ctrl`: finos; `Esc`
  cancela). Se ve en vivo y, al soltar, si se solaparía con algo vuelve a su
  sitio. En el panel «Editar», «Girar alrededor de su centro» (eje y ángulo) y,
  con el botón derecho, giros de ±90°. Si un módulo es lo único que hay en la
  geometría (como el `disc.geo` de los ejemplos de penhan), es toda la
  geometría: PENGEOM no le pone envolvente y fuera de él no hay nada, así que
  se puede mover y girar libremente. Para tener un medio alrededor (aire, por
  ejemplo), «Envolver en un módulo…» con un margen y ese material.
* **Duplicar** (`Ctrl+D` o botón derecho): N copias desplazadas o pegadas por
  una cara (±x, ±y, ±z, con o sin hueco), dentro del mismo contenedor o de
  otro. **Copias independientes** (cada una con sus etiquetas, se editan por
  separado) o **clones enlazados** (`CLONE` de PENGEOM: repiten el módulo
  original; si cambia el original cambian todos; los cuerpos de un clon no
  tienen etiqueta y en el `.in` se nombran por número). Un clon se mueve, se
  gira, se pega y se elimina como un módulo, y «Convertir en copia
  independiente» lo cambia por una copia normal en el mismo sitio.
* **Envolver en un módulo**: un módulo nuevo (caja, cilindro o esfera, ajustado
  a los elementos, con margen) alrededor de uno o varios cuerpos o módulos que
  están dentro del mismo contenedor; pasan a ser sus hijas y el hueco del
  módulo se llena del material del contenedor, así que la simulación no cambia.
  Sirve para mover, girar o duplicar varios elementos juntos. Con margen 0 deja
  1e-5 cm entre las caras planas y la pared: una pared en el mismo sitio que la
  cara de una hija, con otra superficie, puede confundir a PENGEOM al
  transportar (ADR-012).
* **Añadir un detector** (Editar → «Añadir un detector…» o botón derecho): un
  cuerpo con forma de cuadrado, rectángulo, círculo, triángulo, pentágono o
  hexágono, con su altura a lo largo de x, y o z, su giro y su material, y en
  el `.in`, en el mismo paso de deshacer, su detector de penhmain: uno nuevo de
  energía depositada (`ENDETC`) o de impacto (`IMPDET`), con su ventana de
  energía y sus canales, o uno de los que ya hay. penhmain admite 5 detectores
  de cada tipo, de hasta 100 cuerpos los de energía y 50 los de impacto; el
  diálogo no deja pasar de ahí y comprueba el `.in` resultante como penhmain
  (p. ej. un detector de energía no se puede combinar con el forzado de
  interacciones). Aunque un cuerpo no esté en ningún detector, penhmain da la
  energía media depositada en él en `penhmain-res.dat`.
* **Añadir una celda de detectores** (Editar → «Añadir una celda de
  detectores…»): una caja (un módulo) llena de detectores iguales en filas,
  columnas y capas, en cuadrícula o con las filas desplazadas (en panal los
  hexágonos, al tresbolillo los círculos, alternos los triángulos, como
  ladrillos los demás), con el hueco entre detectores y entre capas, la pared
  de la caja y los materiales de los detectores y de la caja. Se pueden poner
  en detectores de penhmain: toda la celda en uno, uno por fila, por columna o
  por capa, o uno por detector. Cada detector lleva en su descripción su fila,
  su columna y su capa. ARGUS organiza la celda en módulos (por capas o por
  filas) cuando hace falta para no pasar de los límites de PENGEOM (248 hijas
  por módulo, 248 superficies en un elemento del nivel superior, 5000 cuerpos,
  10000 superficies).
* **Etiquetas, lados y superficies** (panel «Editar»): la etiqueta de cada
  cuerpo, módulo, clon o superficie (se cambian también sus referencias en el
  `.geo` y en el `.in`); el lado (−1/+1) de cada superficie de un cuerpo
  («Cambiar el lado» o doble clic); quitar una superficie o **añadir** otra:
  una que ya existe, un plano nuevo (punto y normal) o una cuádrica en forma
  implícita (el diálogo dice qué parte del cuerpo se queda). Las superficies
  en forma implícita se editan coeficiente a coeficiente (`AXX`…`A0`, y su
  giro y desplazamiento), y cualquier superficie se puede pasar a forma
  implícita sin cambiarla. De cada superficie se dice qué cuádrica es (esfera,
  elipsoide, cilindro, cono, hiperboloide, paraboloide...). Los cambios que
  harían que dos cuerpos se solapen no se hacen.
* **El `.in` sigue a la geometría**: PENGEOM numera los cuerpos por el orden
  en que se definen y un cuerpo nuevo va antes que su contenedor, así que
  añadir uno renumera los que van detrás. Las referencias del `.in` a cuerpos
  por número (`DSMAX 2`, `EDBODY 3`, `IFORCE 1`...) se cambian a los números
  nuevos; al cambiar una etiqueta se cambian las referencias por etiqueta; al
  eliminar un cuerpo se quitan sus líneas. Todo en el mismo paso de deshacer
  que el cambio de la geometría. Al guardar la geometría con otro nombre en la
  misma carpeta, el `GEOMFN` del `.in` pasa a ser el nuevo.
* **Buscar solapes** (Editar → Buscar solapes en la geometría): cuerpos que
  comparten volumen sin ser uno limitante del otro e hijas que asoman fuera de
  su módulo. PENGEOM no lo comprueba, pero en esos sitios una partícula cruza
  la frontera sin verla.
* **Panel «Fuente»**: partícula, energía (admite `100 MeV`, `15 keV`...),
  posición, caja, cuerpos activos y tipo de emisión. Cada cambio se ve al
  momento en el corte y en el panel «Texto .in» (líneas cambiadas en amarillo).
  La lista de cuerpos activos enseña la descripción de cada cuerpo (y se filtra
  por ella: «lungs»); si la caja no cubre enteros los cuerpos activos (de ellos
  solo saldría lo que queda dentro), el panel lo avisa y «Ajustar la caja a los
  cuerpos activos» la pone justo alrededor de ellos.
* **Panel «Materiales»**: número, fichero `.mat` y nombre de cada material
  (doble clic para cambiarlos). El campo del nombre despliega con ▾ los 280
  materiales de la lista de PENELOPE (su `database/material-list.txt`) y sus
  materiales personalizados (★), y los acota al escribir: en español o en
  inglés, sin acentos, en cualquier orden, por número o por símbolo
  («agua», «hueso cort», «pmma», «278», «Pb»). El nombre se
  guarda como comentario de la línea `MFNAME` del `.in`; penhmain no lo lee.
  Doble clic en el color para cambiarlo en la vista. **＋ Material** (o la
  última fila de la tabla, o el botón derecho) declara un material nuevo sin
  tener que crear antes un cuerpo; el último, si no lo usa nadie, se puede
  quitar. El `.mat` de cada material se genera con penhmat de una de sus dos
  maneras (la ayuda emergente del nombre dice cuál):
  * **de la lista de PENELOPE** (opción 2): el elegido queda como «Agua
    líquida (PENELOPE 278)» y ese número es el que se da a penhmat (los
    nombres de antes, «Water, liquid (PENELOPE 278)» o el de la lista en
    inglés, se siguen reconociendo). El fichero se propone con la primera
    palabra del nombre, sin acentos (`agua.mat`, `agua2.mat` si ya está), y se
    puede cambiar (20 caracteres, lo que lee penhmain);
  * **composición propia** (opción 1; al añadirlo o con el botón derecho →
    «Composición del material N…»): una fórmula química (`C5H8O2`, `H2O`,
    `Ca10(PO4)6(OH)2`) o los elementos con su cantidad (`H 0.111894 O
    0.888106`, también con número atómico o en %), en átomos por molécula o en
    fracción en masa, la densidad y la energía media de excitación (la regla
    de ARGUS, la misma que para los tejidos de un TAC; la que calcula
    PENELOPE; u otro valor, p. ej. el de ICRU). Se ve cada elemento con su
    fracción en masa. Se guarda en el `.in`, en comentarios justo detrás de su
    `MFNAME` que penhmain salta (`[ARGUS pmma.mat: composicion por atomos;
    densidad 1.19 g/cm3; I segun ARGUS]` y `[ARGUS pmma.mat: C 5 H 8 O 2]`).
    Si el `.mat` que ya está en la carpeta no tiene esa composición, ARGUS lo
    dice (no lo sustituye: se cambia el nombre del fichero y se genera).
    «Guardarlo también en mis materiales personalizados» la guarda con su
    nombre para elegirla después en cualquier caso, como uno de la lista;
  * **materiales personalizados** (Opciones → Materiales personalizados…, o el
    botón derecho en el panel): las composiciones propias que se guardan, de
    cada usuario (en su carpeta de datos: `%APPDATA%\ARGUS` en Windows,
    `~/Library/Application Support/ARGUS` en macOS, `~/.local/share/ARGUS` en
    Linux). Nuevo, Editar, **Duplicar y editar** (una copia para variar la
    proporción de una aleación; el original no se toca), Borrar, Exportar e
    Importar (para pasarlos a otro ordenador o a un compañero; lo que ya se
    tiene no se duplica); desde el diálogo del material, también «Guardar el
    que estoy escribiendo»;
  * **«Asignar materiales…»** (el enlace del panel, el botón derecho o
    «Generar .mat y ENDF…»): si el `.in` declara materiales que ARGUS no
    identifica (una geometría y un `.in` que se traen hechos, sin el nombre del
    material en su `MFNAME`), se elige cada uno de la lista o de los materiales
    personalizados, o se escribe su composición; lo que sugiere el nombre del
    fichero (`water.mat`, `hueso_cort.mat`) ya viene propuesto. Todo, un solo
    paso de deshacer.
* **Espectro de la fuente** (panel «Fuente» → «Editar el espectro…»; con una
  energía única, el botón «Espectro…» pregunta si escribirlo a mano o leerlo de
  un fichero): tabla de energías y probabilidades que se puede escribir
  (admite `91,2 MeV`), pegar de una hoja de cálculo o de otro `.in`, importar
  de un fichero y guardar para otros casos. Ficheros que se leen, con una vista
  previa de cómo se han entendido (se puede cambiar):
  * el **formato de ARGUS** (lo escribe «Guardar…»): texto con una cabecera
    que dice cómo leer los números, p. ej.
    ```
    # Espectro de la fuente (ARGUS)
    # unidad: MeV
    # tipo: continuo
    # energía: borde
    # valor: probabilidad
    90      0.10
    95      0.25
    100     -1
    ```
    `unidad`: eV, keV, MeV o GeV; `tipo`: continuo (`SPECTR`) o líneas
    (`SLINES`); `energía`: borde inferior de cada intervalo, centro o bordes
    (tres columnas: Emin, Emax, valor); `valor`: probabilidad de cada
    intervalo, densidad (por unidad de energía) o cuentas. En un espectro
    continuo la última fila es la energía máxima;
  * **tablas** de dos o tres columnas, con o sin títulos (la unidad sale de
    ellos: `E (keV)`, `Energy [MeV]`; un valor por keV se toma como densidad en
    el centro de cada intervalo), con coma o punto decimal;
  * **líneas `SPECTR`/`SLINES`** de otro `.in`;
  * **un espectro de penhmain** (detector de impacto, partículas que salen,
    fuente…), de la partícula que se elija: lo que llega a un detector en una
    simulación puede ser la fuente de otra.

  penhmain admite como mucho 250 filas: si hay más, se juntan intervalos
  vecinos de poca probabilidad (los picos conservan su detalle). penhmain
  admite una sola fuente por simulación (una partícula, una energía o un
  espectro, una posición y una dirección).
  La gráfica muestra el espectro **como lo muestreará penhmain**: ordenado, con
  las probabilidades negativas a cero y normalizado. En un espectro continuo
  (`SPECTR`) cada línea es un intervalo: la energía es uniforme entre su E y la
  de la línea siguiente, y la probabilidad de la última no se usa. Una energía
  repetida da un intervalo de anchura cero: una fracción de las partículas sale
  exactamente con esa energía (raya roja). Para energías exactas (picos
  discretos) están las líneas (`SLINES`); «Convertir en líneas» lo hace.
* **Panel «Simulación»**: el resto del `.in` en secciones plegables, cada una
  con un resumen y un aviso si penhmain se quejaría de algo:
  * *Materiales*: `MSIMPA`, `MSIMPH` y `MSIMPN` de cada material, con los
    valores por defecto en gris y lo que penhmain usará de verdad cuando recorta
    un valor (p. ej. `C1` > 0,2 o `EABS` < 50 eV); «Aplicar a todos».
  * *Geometría*: los `PARINP` (al cambiarlos se recalcula la geometría) y las
    listas `DSMAX`, `EABSB` y `EABSBH` por cuerpo.
  * *Campos* (`PFIELD`, `EFIELD`, `BFIELD`) y *reducción de varianza*
    (`IFORCE` con las interacciones que admite cada partícula, `IBRSPL`,
    `IXRSPL`; por cuerpo o por material).
  * *Partículas que salen* (`NBE`, `NBANGL`).
  * *Detectores* de impacto, angulares y de energía depositada: ventana de
    energía, intervalos (lineales o logarítmicos), partículas, espacio de
    fases, edades y cuerpos. Los cuerpos del detector se **resaltan en magenta**
    en el corte y en 3D; con el botón derecho sobre un cuerpo, «Detectores con
    …» lo pone o lo quita de un detector.
  * *Distribución de dosis*: caja, cilindro o esfera, con el tamaño de vóxel;
    «Ajustar a la geometría», «Ver en el corte» y, en el corte, tiradores para
    arrastrarla (esquinas y centro de la caja; radio y extremos del cilindro).
  * *Proceso externo* (`EPROF`) y *trabajo*: partículas (`NSIMSH`), tiempo
    (con unidades, hasta 10⁹ s), semillas (o par precalculado), `RESUME`,
    `DUMPTO` y `DUMPP`, con lo que hay en los ficheros de volcado de la
    carpeta. (`LTRACK` ya no se elige aquí: las lluvias para verlas en 3D son
    «Lluvias», al ejecutar; si el `.in` lo trae, se respeta.)
* **Comprobaciones de penhmain**: además de las de la fuente y los materiales,
  ARGUS reproduce todas las de la subrutina PMRDR con sus códigos `IRETRN`
  (cuerpos que no existen o vacíos, campos en el vacío, forzado incompatible,
  `IBRSPL` sin forzado, detectores, rejilla de dosis...) y avisa de lo que
  penhmain cambia sin decir nada: valores que recorta, `TIME` < 1 (100 s),
  `IDFLNC` (penhmain 2025 no lo usa), un fichero `EPROF` que no existe (lo crea
  vacío y registra la fluencia)... Con `RESUME` lee la cabecera del volcado: si
  está vacío se empieza desde cero; si es de otra simulación (otro título)
  penhmain se detendría (3112); y si es de esta, dice cuántas lluvias lleva y si
  se hizo con otro espectro o con otra geometría (penhmain solo compara el
  título y mezclaría las dos simulaciones).
* **Importar un TAC** (Archivo → Importar un TAC (DICOM)…): se elige la carpeta
  con los ficheros DICOM, la serie, el recorte («Ajustar al cuerpo»), el tamaño
  de vóxel (con el número de vóxeles, el tamaño del `.ct` y la memoria que
  reservará penhmain; penhmain admite como mucho 512 cortes), la calibración y
  dónde va el CT (centrado en el origen, en las coordenadas del TAC o con un
  punto del TAC, p. ej. el isocentro, en el origen; y después desplazado lo que
  se quiera) y dentro de qué cuerpo o módulo. El `.ct` (12 caracteres como mucho)
  y su ficha `.ct.json` (serie sin datos del paciente, recorte, calibración y
  composición de cada tejido) se escriben donde PENGEOM los buscará: en la carpeta
  del `.in` abierto (la de trabajo de penhmain) o, sin `.in`, junto a la geometría; en la geometría
  se añade el `CTBOX` y en el `.in`, los materiales de los tejidos (un solo paso
  de deshacer). La calibración por defecto es la curva de Schneider et al.
  (2000) con cinco tejidos (aire, pulmón, adiposo, tejido blando y hueso) con
  solo H, C, N, O, P y Ca (y Ar en el aire): 17 isótopos ENDF, por debajo del
  límite de 30 de penhan con protones. Sus `.mat` se generan con `penhmat`,
  opción 1; `materiales.txt` da las respuestas exactas, con la densidad media
  de los vóxeles de cada material y la energía media de excitación de la regla
  general de ARGUS (ADR-011). penhmain (2025) resta la pérdida continua con la
  densidad del `.mat` (CTTRACK, ADR-010): por eso, al importar, cada tejido cuyos
  vóxeles tienen densidades muy distintas (el pulmón, el hueso) se parte en
  varios materiales por densidad (`ct_pulmon_1.mat`…), con los materiales que
  quedan libres en el `.in` (penhmain admite 10), y se dice el error de alcance
  que queda. Si un tejido que haría falta partir no se puede (el `.in` ya tiene
  su `.mat`, p. ej. de un TAC importado antes, o no quedan materiales libres),
  ARGUS dice por qué y qué hacer. Si ha corregido CTTRACK en su penhmain,
  márquelo en Opciones: los tejidos no se parten y no se avisa. En la vista 3D, el aire
  del TAC empieza oculto (panel Materiales para mostrarlo u ocultar otros
  tejidos). Ver → Encuadrar el TAC. Las series comprimidas (JPEG) hay que
  exportarlas sin compresión.
* **Preparar la carpeta de simulación** (Archivo → Preparar la carpeta de
  simulación…): copia en una carpeta nueva junto al `.in` (`caso_sim`) todo lo
  que necesita penhmain; los `.mat` y los ENDF se buscan junto al `.in` y en la
  base de datos de penhan (`database/ENDF_data`), y el ejecutable (con las DLL
  de MinGW que tenga al lado), donde se elija la primera vez. No sobrescribe ni
  borra nada; «Cancelar» para la copia y borra lo que estaba a medias. Si
  falta algún `.mat`, «Generar los .mat que faltan…» los genera con su
  penhmat. Después, en esa carpeta, el diálogo dice cómo lanzar su penhmain:
  `penhmain < caso.in` en cmd o `cmd /c "penhmain < caso.in"` en PowerShell
  con uno de Windows; `./penhmain.x < caso.in` en un terminal de WSL (Linux)
  con uno de Linux.
* **Programas de penhan** (Opciones → Programas de penhan…): dónde están su
  carpeta de penhan (su `database`), penhmat y penhmain, compilados por usted
  en su ordenador (ARGUS no los incluye). Pueden ser de Windows, de Linux o de
  macOS: ARGUS los reconoce por su contenido y, en Windows, ejecuta los de
  Linux con WSL. «Probar penhmat» comprueba que funciona. Si prefiere generar
  los `.mat` usted, elija «No hace falta». **Ficheros ENDF**: la carpeta de
  donde se copian los ENDF que necesitan los `.mat` con protones o alfas (por
  defecto, la `ENDF_data` de la base de datos; dice cuántos tiene).
* **Generar .mat y ENDF** (panel Materiales, en negrita cuando falta algo):
  genera con su penhmat los `.mat` que faltan junto al `.in` (los materiales de
  la lista de PENELOPE, por su nombre en la columna «Material»; los que tienen
  composición propia; y los tejidos de un TAC importado, con su composición y
  la densidad media de sus vóxeles) y copia allí los ficheros ENDF que listan
  (con protones o alfas: penhmain los lee de la carpeta donde simula), desde
  la carpeta de los ENDF. Si falta alguno, «Carpeta de los ENDF…» la elige; si
  hay materiales que ARGUS no identifica, «Asignar materiales…». No sustituye
  ningún fichero.
* **Qué resultados pedir** (Simulación → Elegir qué resultados pide el .in…):
  la dosis (matriz de vóxeles: caja, cilindro o esfera), lo que entra en un
  cuerpo (espectro, fluencia, espacio de fases), la energía depositada, los
  detectores angulares, lo que sale de la geometría y seguir la simulación.
  Marcar uno lo pide con valores razonables (respetando las reglas
  de penhmain) y enseña debajo su editor: la matriz con su tamaño de vóxel (Δx,
  Δy, Δz: se calcula el número de intervalos, impar en X e Y si se quiere, con
  los límites de penhmain y lo que ocupará `3d-dose-map.dat`), las ventanas de
  energía y los intervalos de los detectores, los cuerpos… Desmarcarlo lo quita
  del `.in`. Cada cambio se deshace. La ventana no se cierra: la matriz se ve en
  el corte (con sus vóxeles, cuando miden al menos 4 píxeles) y en 3D (en sus
  caras del fondo), y se estira por sus asas o se mueve con el ratón en 2D y en
  3D (al estirarla el vóxel no cambia; con `Ctrl`, sí).
* **Ejecutar** (Simulación → Ejecutar penhmain…, `F9`, o «Ejecutar» en la
  barra): dice antes qué va a hacer (el penhmain que se copia a la carpeta del
  `.in` y en qué terminal, los ficheros que faltan o se copian, los resultados
  que pide, el trabajo, qué pasa con los resultados anteriores) y lo hace:
  copia, guarda los resultados anteriores en «anterior-AAAA-MM-DD_HH-MM-SS» (o
  deja que se sobrescriban), abre un terminal con `penhmain < caso.in`, donde
  se ve la simulación al momento, y la ventana de resultados, que la sigue. En
  Linux se abre el terminal que haya; en macOS, el Terminal; en Windows se
  elige **«Terminal: Linux (WSL) o Windows (cmd)»**: un penhmain de Linux (el
  `penhmain.x` compilado en WSL) va en el terminal de Linux; uno de Windows,
  en la consola o, si se prefiere, también en el de Linux (WSL lo ejecuta). En
  el terminal de Linux la salida queda además en `penhmain.out`, donde ARGUS
  ve los errores; en la consola de Windows, solo en ella. En «Trabajo»:
  **Partículas** (`NSIMSH`: las partículas primarias que se simulan, cada una
  con su lluvia), el **tiempo máximo** en s, min, h, días o semanas (como
  mucho 10⁹ s; vacío, sin límite) y **Lluvias**: cuántas de las primeras se
  ven en 3D (de 1 a 1000; «ninguna», sin ellas). Es lo único que hay que
  elegir: ARGUS las simula aparte, a la vez que la simulación (la cascada).
  Con `RESUME`, penhmain sigue la
  simulación guardada con sus rejillas e intervalos: si el `.in` pide otros,
  ARGUS lo dice. Con errores que pararían a penhmain, no ejecuta (salvo que se
  diga); los `.mat` que faltan se generan desde el mismo diálogo.
* **Cascada de partículas** (al ejecutar, «Lluvias» en «Trabajo», o en la
  ventana de resultados, «Cascada de partículas (3D)»): las primeras lluvias
  del caso (1000 por defecto y como mucho) simuladas con su penhmain en la carpeta «cascada» junto al `.in`, en
  segundo plano, con el mismo penhmain que la simulación. Cada trayectoria va en
  su lluvia sin adivinar nada: cuando los `.trk` no bastan para saberlo (una
  fuente radiactiva puntual), ARGUS repite esa ejecución con las mismas semillas
  parando antes (unos segundos por corte, en paralelo). En 3D sobre la
  geometría translúcida: las primeras N lluvias o
  una sola, qué partículas, color por partícula, energía, lluvia o generación,
  qué materiales se ven, y la animación con el tiempo real de vuelo (velocidad,
  tiempo logarítmico, lluvias escalonadas, estela, partículas en vuelo).
  «Imagen…» y «Animación…» las guardan (PNG, MP4 o GIF) para clase.
* **Ver los resultados** (Resultados → Ver los resultados de este caso,
  `Ctrl+R`, o Abrir los resultados de una carpeta…, `Ctrl+Mayús+R`): todas las
  salidas de penhmain de la carpeta, en un árbol. Mientras penhmain simula, se
  vuelven a leer solas. A la derecha: «Medidas» (las de la curva que se ve,
  con su incertidumbre), «Comparar» (otras carpetas o ejecuciones, datos
  medidos en texto o CSV, índice gamma), «Espectro» (resolución del detector,
  ventanas de energía, coeficientes de conversión) y «Unidades». El resumen da
  la masa y la dosis media de cada cuerpo. «Ver sobre la geometría» pone el
  mapa de dosis, con isolíneas, en los cortes de la ventana principal, o las
  trayectorias en 2D y 3D; con el botón derecho en el corte, «Perfil del mapa
  de resultados…». Exportar figura (PDF, SVG, PNG) o datos (CSV).
* **Simulaciones en paralelo** (Resultados → Simulaciones en paralelo…): el
  mismo `.in` en varias carpetas (`run-01`…) con semillas independientes y las
  lluvias repartidas, como MultiRuns. ARGUS no las lanza: en esa carpeta, doble
  clic en `lanzar.bat` (Windows; con un penhmain de Linux usa WSL) o
  `sh lanzar.sh` (Linux, macOS o WSL), que las lanza de N en N (las que ya se
  lanzaron no se vuelven a lanzar). «Seguirlas en Resultados» enseña cada
  ejecución y todas combinadas. `combinar.sh` o `combinar.ps1` las combinan con
  penhmain-sum, si lo quiere (para verlas en ARGUS no hace falta). Una carpeta
  con subcarpetas de resultados que no tiene el índice de ARGUS se combina solo
  si son el mismo caso con semillas distintas; si son casos distintos, se ven
  uno a uno («Cada caso por separado»).
* **Campañas** (Resultados → Campaña de simulaciones…): variantes del caso
  cambiando uno o dos parámetros (energía, posición, apertura, un `PARINP`, un
  material o cualquier palabra clave), con valores como `50 100 150`,
  `50:150:25` o `50-150/5`, y, si quiere, varias ejecuciones de cada una. En
  Resultados: «Comparar las N variantes» (cada resultado de todas a la vez) y
  «Medidas frente al parámetro» (p. ej. el alcance frente a la energía, con un
  ajuste lineal, parabólico o potencial).
* **Revisar muchos `.in` a la vez** (sin abrir la ventana):
  `uv run python tools/revisar_in.py ..\SOBP --avisos` recorre la carpeta y
  sus subcarpetas y lista, para cada `.in`, lo que penhmain rechazaría y (con
  `--avisos`) lo que acepta pero probablemente no es lo que se quiere.
* **Icono**: Ayuda → Icono de ARGUS (trébol radiactivo, átomo u ojo).
* **Guardar** (`Ctrl+S`): guarda el `.in` y la geometría si han cambiado; solo
  se reescriben las líneas (o los elementos del `.geo`) cambiados y el resto
  queda igual byte a byte. La versión anterior queda en `nombre.in.bak` y
  `nombre.geo.bak`. Junto al `.in` se escribe `materiales.txt` con los ficheros
  `.mat` que necesita, los que faltan y cómo generarlos con `penhmat` (o con
  «Generar .mat y ENDF…», si ARGUS conoce su composición). Si ya hay un
  `materiales.txt` que no es de ARGUS, no se toca y se escribe
  `materiales_ARGUS.txt`.
* **Comprobación de los `.mat` existentes**: ARGUS lee el final de cada `.mat`
  (la lista de ficheros ENDF) y avisa de lo que haría fallar a penhmain: fichero
  truncado, más de 30 isótopos con datos ENDF (MNIS de penhan), ficheros ENDF
  que no están en la carpeta (IRETRN 40028) o más de 30 isótopos distintos entre
  todos los materiales (IRETRN 40029).

## Requisitos

* Windows 10/11, macOS 13 o posterior (el mínimo de Qt 6.11), o Linux.
* Python 3.11 o posterior (se recomienda 3.12).
* Gráficos: el corte 2D funciona en la CPU. La vista 3D usa OpenGL 4.1
  (Intel UHD Graphics, GPU NVIDIA/AMD y Mac); sin él, se calcula en la CPU.

## Ejecutables (sin instalar Python)

Para quien solo quiere usar ARGUS: un paquete por sistema (ADR-022), con todo dentro.

| Sistema | Paquete | Primera vez |
|---|---|---|
| Windows 10/11 | `ARGUS-<versión>-Windows-instalador.exe` | si sale «Windows protegió su PC»: «Más información» → «Ejecutar de todas formas» |
| macOS 13+ (chip Apple) | `ARGUS-<versión>-macOS-AppleSilicon.dmg` | arrastrarlo a Aplicaciones; Ajustes del Sistema → Privacidad y seguridad → «Abrir igualmente» |
| macOS 13+ (Intel) | `ARGUS-<versión>-macOS-Intel.dmg` | lo mismo; va sin Numba (no hay versiones para Intel): más lento en la CPU |
| Linux (glibc 2.35+: Ubuntu 22.04, Debian 12...) | `ARGUS-<versión>-Linux-x86_64.tar.gz` | `tar xzf …` y `./instalar.sh` (menú de aplicaciones) |

`packaging/LEEME.txt` (va en cada paquete) lo explica paso a paso. Si algo falla,
`ARGUS --comprobar` escribe un informe (`argus_comprobacion.txt`) con las versiones y el
resultado de cada prueba.

**Construirlos.** Cada sistema construye el suyo (PyInstaller no hace ejecutables de
otro sistema). El flujo de GitHub hace los cuatro, los comprueba y los publica en
«Releases»: se lanza en la pestaña Actions → «Ejecutables» → «Run workflow», o al subir
una etiqueta `vX.Y.Z`. Su copia está en `packaging/github/ejecutables.yml`; en el
repositorio va en `.github/workflows/`. A mano, en el ordenador de cada sistema:

```
uv sync --frozen --no-dev
uv pip install pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.8
uv run --no-sync python packaging/construir.py     # deja el paquete en dist/paquetes
```

En Windows hace falta además [Inno Setup](https://jrsoftware.org/isinfo.php) 6.4 o
posterior. Con la variable de entorno `ARGUS_CLAVE` los paquetes llevan esa contraseña.
En GitHub es un secreto del repositorio; sin él, el flujo se para antes de construir
nada (salvo con la variable `ARGUS_SIN_CLAVE` = `si`).
`python packaging/revisar_repo.py` comprueba que no se vaya a subir lo que no debe
(fuentes de PENELOPE, PenGeomJar, DICOM, compilados).

## Instalación en Windows (desde el código)

**La forma fácil:** doble clic en `instalar_argus.bat` (solo la primera vez).
Instala [uv](https://docs.astral.sh/uv/) si no está, descarga Python 3.12 y las
bibliotecas (PySide6, NumPy, Numba, pydicom, Matplotlib) en la carpeta `.venv` de ARGUS; tarda unos
minutos. Después, para abrir ARGUS, doble clic en `ARGUS.bat`, o arrastre un
`.in` o un `.geo` encima de `ARGUS.bat`.

**A mano, en PowerShell:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# cierre y vuelva a abrir PowerShell para que encuentre uv
cd C:\Users\Rafa\Desktop\SOBP_Investigacion\ARGUS
uv sync --extra dev
uv run argus ..\SOBP\SOBP_Program\100Mev15Final\100Mev15-174\maniqui.in
```

(también se puede instalar uv con `winget install --id=astral-sh.uv -e`).

Sin uv, con un Python 3.11 o 3.12 ya instalado:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
argus
```

En macOS y Linux los comandos son los mismos (`source .venv/bin/activate`).

## Pruebas

```powershell
uv run pytest
```

Las pruebas marcadas como *oracle* comparan ARGUS con PENGEOM y con penhmain
reales. Para ello se compilan tres pequeñas sondas Fortran con **tu** código
(hace falta `gfortran`): la de PENGEOM enlaza `pengeom.f`; la del lector, las
subrutinas de lectura de `penhmain.f` sin modificarlas; y la de las
comprobaciones (`argus_simprobe`) compila `penhmain.f` con las bibliotecas que
incluye (`penelope.f`, `penhan.f`...) sin su programa principal y sin leer los
materiales (PEINIT y PHINIT se sustituyen por rutinas vacías), de modo que
penhmain hace todas sus comprobaciones del `.in` sin simular nada:

```powershell
uv run python tools/build_probe.py          # usa ..\ProgramasBase\penhan\programs\
uv run python tools/compare_oracle.py --points 20000
uv run python tools/fuzz_in_reader.py --n 2000
uv run python tests/editor_fuzz.py 200      # cadenas de ediciones al azar leídas por PENGEOM
```

Si no hay `gfortran`, esas pruebas se omiten y el resto se ejecuta igual.
Las pruebas no tocan los ajustes de ARGUS del usuario (colores, recientes...):
usan un fichero `.ini` temporal (variable `ARGUS_SETTINGS`).
Las pruebas de la GPU (el *shader* frente al trazador de la CPU) necesitan una
pantalla con OpenGL 4.1; por defecto las pruebas usan una pantalla virtual sin
OpenGL y se omiten. Para hacerlas en Windows:

```powershell
$env:ARGUS_TEST_GPU = "1"; uv run pytest tests/test_view3d.py
```

Las que usan el penhmat de verdad (las respuestas de `materiales.txt` y «Generar
.mat y ENDF») necesitan `ARGUS_PENHMAT` (el ejecutable) y `ARGUS_PENHAN_DB` (la
carpeta `database`); las del penhmat de mentira se omiten en Windows.
ARGUS no necesita la sonda para funcionar.

En el repositorio público no van los ejemplos de PENELOPE, PenGeomJar y penhan de
`tests/corpus` (no se pueden redistribuir) ni los casos del usuario (`.gitignore`): en un
clon del repositorio, las pruebas que los usan se omiten.

## Estructura

```
ARGUS/
  src/argus/
    app.py       arranque (y --comprobar: comprobar la instalación, comprobar.py)
    core/        E/S con formato Fortran, diagnósticos y ficheros (copias y escrituras que nunca
                 sobrescriben ni quedan a medias) y la carpeta de datos de cada usuario (userdata)
    pengeom/     modelo, lector, escritor, cuádricas, compilador, LOCATE, límites, CT, edición,
                 cajas exactas de los elementos (para pegar), PARINP, colocación de módulos y
                 clones (placement), duplicar (duplicate), envolver (wrap), superficies, lados y
                 etiquetas (surfaces), solapes (overlap), identidad de los cuerpos (identity),
                 geometría nueva (newgeo), celdas de detectores (detcell) y los bloques de
                 la pestaña «Geometrías complejas» como los edita PenGeomJar (blocks)
    penhan/      fichero de entrada de penhmain: lector (PMRDR0), secciones (materiales, cuerpos,
                 detectores, dosis, trabajo, espectro), comprobaciones (PMRDR), volcados,
                 programas de penhan del usuario (programs) y .mat generados con penhmat (matgen),
                 composiciones propias de los materiales (composition), los nombres en español
                 de la lista de PENELOPE y su búsqueda (material_names), los materiales
                 personalizados (library), ejecutar penhmain (run) y
                 la cascada (cascade), edición de la fuente, materiales, plantilla de .in nuevo, el .in que sigue
                 a la geometría (follow), la carpeta de simulación (simfolder), qué resultados
                 pedir (goals), las simulaciones en paralelo y campañas (batch) y los espectros
                 de la fuente desde ficheros (spectrum_file)
    ct/          importar TACs: series DICOM, calibración HU → material y densidad, rejilla y
                 colocación, fichero .ct y su ficha, tejidos partidos por densidad (subdivide)
                 y datos de los elementos y energía media de excitación (elements)
    results/     resultados de penhmain: reconocer y leer las salidas, resumen, combinar ejecuciones,
                 la carpeta y su estado, unidades, medidas, perfiles, comparación y gamma, dosis por
                 cuerpo (bodies), histogramas dosis-volumen (dvh), espectrometría (spectro) y ajustes
    oracle/      sondas Fortran (PENGEOM y lector de penhmain) y comparaciones
    render/      cortes 2D, trazado de rayos 3D (CPU con Numba y shaders GLSL), escena 3D, el mapa
                 de dosis sobre los cortes (dose_overlay) y la traza de una superficie (quadric_trace)
    ui/          interfaz Qt (PySide6); la ventana de resultados, con Matplotlib (ui/results), y la
                 pestaña «Geometrías complejas» con las pestañas de PenGeomJar (ui/complex_geo)
  tests/         pruebas y corpus (copias de ejemplos de PENELOPE y PenGeomJar y de geometrías
                 del usuario, que no van en el repositorio público, y salidas de penhmain)
  tools/         compilar las sondas, comparar con PENGEOM y revisar carpetas de .in
  packaging/     los ejecutables: receta de PyInstaller, iconos, instalador de Windows, construir
                 y comprobar cada paquete, revisar el contenido del repositorio y el flujo de
                 GitHub que los construye y publica (github/; ADR-022)
  docs/adr/      decisiones de arquitectura
```

## Principios

1. **Fidelidad a PENGEOM.** Lo que ARGUS muestra es lo que PENGEOM va a
   simular: el lector, el compilador y el localizador reproducen el código
   Fortran y se verifican contra él.
2. **No destruir el trabajo del usuario.** Un fichero abierto y guardado sin
   cambios queda idéntico; los cambios solo tocan los elementos editados y
   conservan los comentarios.
3. **Errores con nombre.** Cada problema se muestra en su línea, con el código
   `IRETRN` con el que abortaría PENGEOM o penhmain.
4. **Funcionar en un portátil con Intel UHD.** Todo lo que no requiere GPU se
   hace en la CPU con NumPy; la GPU se usa con un perfil OpenGL moderado.
