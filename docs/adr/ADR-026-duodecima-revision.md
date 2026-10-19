# ADR-026 · Duodécima revisión: los volcados de penhmain (0.17.4)

**Estado:** aceptada (versión 0.17.4)

## Contexto

Rafael pidió (19-10-2026) tres cosas. Las casillas «Escalonadas» y «En vuelo» del visor de la cascada se pueden marcar
a la vez: se le enseñó que se complementan (una sale de la otra) y decidió dejarlas así. Las otras dos: revisar los
volcados (RESUME, DUMPTO, DUMPP), por si había erratas y por si se hacen bien, y su opinión sobre el visor de la
cascada y el programa (respondida aparte, sin cambios en el código).

Revisaron los volcados tres revisores en paralelo, comparando con `penhmain.f` y `penhmain-sum.f` (2025) y con el
`penhmain.x` real de Rafael (gfortran) y un penhmain-sum compilado:

* **el núcleo** (`dumpfile`, `simcheck`, `run`, `batch`, `simfolder`, la cascada, el estado de la carpeta): 12
  hallazgos;
* **el penhmain de verdad**: 16 volcados reales de todas las clases, 31 cambios del `.in` con RESUME, 14 volcados
  dañados, 9 variantes de DUMPP y DUMPTO, «Ejecutar», simulaciones en paralelo y campañas: 14 hallazgos;
* **los textos y la interfaz** (21 ejecuciones reales y 15 estados del volcado en la ventana): 28 hallazgos y una nota
  sobre la cascada.

Juntos, quitados los repetidos, 41 fallos de ARGUS y uno de penhmain; al comprobar las correcciones con el penhmain real salió uno más (la carpeta «vacía» mientras se inicializa). Cada fallo de ARGUS se reprodujo antes de corregirlo y tiene su prueba,
que falla con el código de 0.17.3.

Lo que estaba bien, comprobado con penhmain real: la lectura de los 16 volcados reales; los 31 cambios con RESUME, en
los que ARGUS avisa de lo que hace penhmain; DUMPP y DUMPTO (también DUMPP 0, 1e6 y 1e12 y sin ninguno); que
«Ejecutar» nunca deja a penhmain sin el volcado que pide; las ejecuciones en paralelo y las campañas con penhmain-sum;
«Seguir la simulación».

## Decisiones

### 1. Un solo sitio decide qué hará penhmain con RESUME

Lo decide ahora `penhan/resume.py` (`resume_outcome`) y lo usan las comprobaciones del `.in`, «Ejecutar», «Trabajo»,
la nota de la dosis, «Elegir resultados…» y las vistas (la rejilla del volcado). Antes cada uno lo decidía a su manera
y se contradecían:

* con un volcado dañado, «Ejecutar» decía «penhmain empieza desde cero» (y ofrecía mover los resultados); la lista de
  errores del mismo diálogo, «penhmain se detendría»;
* con un nombre con un blanco delante, la comprobación del `.in` decía «continuará la simulación guardada» y otra,
  «empezaría desde cero», que es lo que hace penhmain;
* con el volcado de otro título, «Ejecutar» decía «penhmain sigue la simulación guardada»: penhmain se detiene (3112).

Hay cinco desenlaces:

| Desenlace | Cuándo (penhmain.f) |
|---|---|
| empieza desde cero | no existe, está vacío o es lo que deja penhmain («Empty.») |
| sigue la simulación | un volcado de este `.in` que se puede leer entero |
| ya terminada | las lluvias de NSIMSH, o ya se leyeron los espacios de fases (JOBEND) |
| se detiene | dañado, cortado, de otro título, una carpeta, en una carpeta que no existe o con IPSF 1 |
| destruye el fichero | otra cosa (un `.mat`, el `.in`, un espacio de fases): penhmain escribe «Empty.» encima |

### 2. Lo que penhmain destruiría (3)

* **RESUME con otro fichero.** penhmain no lo puede leer como volcado, escribe «Empty.» encima y empieza desde cero:
  se perdían un `.mat` de 4,9 MB, un espacio de fases o el propio `.in` (comprobado). ARGUS decía «no contiene una
  simulación guardada». Ahora es un error, y «Ejecutar» no deja ejecutar ni forzándolo. Lo que deja penhmain
  («Empty.», vacío, «Empty file. Initialisation of MultiRuns.») y una primera línea a medias siguen siendo «vacío»; un
  fichero binario no se lee por líneas.
* **DUMPTO con el nombre de otro fichero.** penhmain lo sustituye por el volcado al primer volcado: `DUMPTO
  tanque.geo` dejaba la geometría convertida en un volcado, sin ningún aviso. Ahora es un error si es uno de los
  ficheros que lee penhmain (y «Ejecutar» no lo deja forzar), uno de sus resultados de nombre fijo (`penhmain-res.dat`,
  `depth-dose.dat`…) u otro fichero que no es un volcado.
* **DUMPTO con un volcado bueno de esta simulación y sin RESUME.** penhmain empieza desde cero y lo sustituye: salía en
  verde («con esta configuración»). Ahora lo dice (ámbar en «Trabajo» e información en los mensajes).

### 3. Volcados con los que penhmain se detiene (6)

* **Dañado, cortado, una carpeta, de otro título o solo la primera línea.** Ahora, en «Ejecutar», en rojo y con el
  mismo motivo que los errores («RESUME: dump.dmp está incompleto: penhmain se detendría»), sin ofrecer mover los
  resultados (penhmain no llega a simular). Los mensajes ya no llevan texto de Python en inglés ni la ruta entera («Is
  a directory: '/…'»), y una carpeta es una carpeta, no «un volcado dañado».
* **RESUME en una carpeta que no existe.** penhmain no puede abrirlo («Cannot open file»): ARGUS decía que lo creaba y
  empezaba de cero. «Preparar la carpeta de simulación» enseña ahora el volcado que falta (también cuando falta su
  carpeta).
* **DUMPTO en una carpeta que no existe.** penhmain simula entera la simulación y se detiene al escribir el primer
  volcado, sin ningún resultado: era solo un aviso. Ahora es un error.
* **Un valor que no es un número en los resultados.** HENANR, IMDETR, ANDETR, ENDETR, DOSER… leen sin `ERR=`: penhmain
  se detiene («Bad integer», «Bad real number»). ARGUS lo daba por bueno (se callaba al no poder leerlos). Ahora se
  mira que cada valor sea un número, también los contadores de la dosis (hasta 16 MB; sin «*** END ***», hasta 64 MB).
* **Sin la línea «*** END ***».** penhmain no la lee: un volcado completo sin ella se reanuda (comprobado: 1200
  lluvias). Era un error («está incompleto»). Ahora se comprueba que estén todos los datos; si faltan, sí es un error.

### 4. RESUME con un detector de impacto que escribe espacio de fases (IPSF 1)

Es el fallo más grave, y es de penhmain. Al reanudar con RWRITE > 0, PMRDR relee el espacio de fases con RDPSF
(4392-4404) para seguir escribiéndolo. RDPSF lee bloques de 1000 registros y, como el bucle lo llama siempre con
KODEPS = 0, vuelve a empezar en cada vuelta: llega al final del fichero y la unidad queda tras la marca de fin. gfortran
no deja escribir después («Sequential READ or WRITE not allowed after EOF marker») y penhmain se detiene al escribir
los primeros resultados tras reanudar. Como WRPSF va al principio de PMWRT, antes del volcado y del resumen, se pierde
todo lo simulado desde que reanuda.

Afecta al caso 3 de Rafael (`IMPDET 0 1.4E6 140 1 1`) y al corpus `caja`. ARGUS decía «continuará la simulación
guardada». Ahora es un error, con la salida: poner IPSF 0 en ese detector (el espacio de fases se queda como estaba) o
empezar sin RESUME. Con IPSF 0, penhmain reanuda bien (comprobado). La tarjeta «Seguir la simulación» lo dice.

Una prueba de 0.13.1 (`test_with_a_real_penhmain_resume_keeps_the_grid_of_the_dump`) pasaba por casualidad: penhmain se
detenía y el mapa de dosis que miraba era el de la primera ejecución, con las mismas capas. Ahora usa IPSF 0 y mira
que el resumen tenga las 600 lluvias.

### 5. Espacios de fases de entrada (NPSN, LPSF)

penhmain toma del volcado por qué fichero de IPSFN va y si la fuente era un espacio de fases (4305). ARGUS no lo leía y
decía «continuará» en los tres casos:

* con todos los ficheros ya leídos, penhmain la da por terminada;
* con un volcado sin IPSFN y un `.in` con ellos, falla (Segmentation fault);
* con un volcado con IPSFN y un `.in` sin ellos, la da por terminada. Además, ARGUS daba una diferencia falsa
  («energía: 0 eV en el volcado»).

### 6. Lo demás que penhmain toma del volcado

Al reanudar, penhmain no solo toma del volcado las rejillas y los intervalos: también estas cosas, que ARGUS no
comparaba (comprobado con el penhmain real):

* **IDCUT** de cada detector de impacto. penhmain transporta con el del `.in` y escribe con el del volcado: de 1 a 2,
  cuenta la fluencia y no la escribe; de 2 a 0, absorbe lo que entra y sigue escribiendo una fluencia que ya no crece.
* **La ventana y la escala de las edades** (IDAGEL).
* **Las partículas de un detector angular** (ADKPAR, KKAD).
* **Los nombres de los ficheros de los detectores** (IDSPC, IDAGEF, ADSPC, EDSPC y el de la fluencia, que penhmain
  llama como el de IDPSF): penhmain sigue escribiendo en los del volcado.

### 7. Lo que dicen los mensajes (6)

* **Ya terminada.** «Solo volverá a escribir los mismos resultados» no era cierto: con JOBEND, penhmain se salta
  `TIMET = TSEC + TIMETA` y reescribe los resultados y el volcado con el tiempo de cálculo a 0 (comprobado), y las
  reanudaciones siguientes cuentan desde 0. Ahora lo dice. Si la simulación ya está terminada, tampoco dice que
  «mezclaría las dos simulaciones».
* **Cada diferencia de la fuente, con lo que hará penhmain.** Con otra partícula sigue con la del volcado (comprobado:
  sigue simulando fotones). Con otro número de intervalos del espectro sortea un tercer espectro: toma del volcado el
  número y del `.in` las energías. Con otra energía o con otro espectro, mezcla los dos. Antes decía siempre «mezclaría
  las dos simulaciones».
* **«1 partículas»** y «partículas» con una fuente radiactiva: ahora «1 partícula» y «desintegraciones», también en la
  cabecera de «Trabajo».
* **RESUME con LTRACK.** Al reanudar (N ≠ 0), penhmain no escribe trayectorias: ahora se avisa.
* **«(penhmain usa 30)»**, sin unidad. Y «Los resultados que hay en la carpeta (dump.dmp…) (título)», que hablaba del
  volcado: ahora «El volcado dump.dmp (300 partículas) no es de este .in (otro título)».

### 8. «Ejecutar», los guiones y penhmain-sum (2)

* **El guion `argus-ejecutar.*` tras un IRETRN.** penhmain acaba con el código 0 aunque se detenga con un IRETRN (3112,
  3113…), y el guion decía «penhmain ha terminado; los resultados están en esta carpeta». Ahora busca «IRETRN =» en la
  salida en pantalla (en Windows, en `penhmain.dat`) y dice que se ha detenido. El guion de 0.17.3 se reconoce como de
  ARGUS sin retocar: se sustituye sin dejar un `.bak`.
* **`combinar.sh` y `combinar.ps1`** decían «Combinadas en suma» aunque penhmain-sum no hubiera combinado nada
  («Inconsistent dump file format»: acaba con el código 0), y luego no dejaban repetir («Ya existe suma»). Además,
  penhmain-sum crea vacío el volcado que falta. Ahora:
  * antes de llamarlo, se mira que cada ejecución tenga su volcado completo;
  * después, que haya escrito el resumen y su «***  END  ***»;
  * si falla, la carpeta `suma`, que crea el propio guion, se renombra (`suma-fallida-…`) y se puede volver a combinar.

### 9. El estado de la carpeta (4)

* **Una simulación que reanuda y aún no ha volcado.** Con RESUME siempre hay resultados de antes: la carpeta salía
  «parada» («¿Se ha detenido penhmain?») y «Ejecutar» no veía que ya había un penhmain en ella. El registro
  (`penhmain.dat`) y el resumen llevan la fecha en que empezó su ejecución (PDATET al empezar), pero gfortran no
  escribe el registro al disco hasta llenar su búfer o acabar: mientras simula suele estar vacío o con la cabecera, sin
  la fecha, y con la fecha (de modificación) de cuando se abrió. Si es posterior al resumen y de otra ejecución, o sin
  su fecha aún, es una ejecución nueva que empieza. Por lo mismo, una simulación nueva que se inicializa ya no sale
  «No hay resultados de penhmain en esta carpeta».
* **«Faltan unos 4,2 días»** con TIME 50 y RESUME: TIME cuenta desde que empieza esta ejecución. Ahora lo que falta se
  acota por TIME desde que se abrió el registro (comprobado con el penhmain real: «faltan unos 10 s» a los 40 s).
* **«Resultados cada 2 min»** para DUMPP 90 y 150: ahora «cada 90 s» y «cada 150 s».

### 10. «Trabajo», «Elegir resultados…» y la dosis (10)

* «Volcar en: (sin volcado)» era falso: sin DUMPTO, penhmain vuelca en `dump.dmp` (salvo con DUMPP ≥ 1e10). Y «Cada:
  no s» decía lo contrario que su nota («una vez al día»): ahora «86400».
* Las filas de estado vacías dejaban huecos; la cabecera decía «reanuda de dump.dmp» aunque penhmain fuera a empezar
  de cero o a detenerse.
* El mismo cambio se llamaba «Volcado final» y «Volcado»: ahora «Fichero del volcado».
* Un nombre pegado de más de 20 caracteres se cortaba sin avisar: ahora llega al `.in`, que dice por qué no vale.
* «Seguir la simulación»: vaciar «Cada» era ««» no es un número»; 0 dejaba DUMPTO; el mensaje verde de cuando se marcó
  se quedaba al desmarcarse sola. Ahora vaciar y 0 son como desmarcarla, y el mensaje se va. «El valor mínimo es 0»,
  sin «0.0».
* La nota de la dosis, con el `.in` sin rejilla, decía «no con esta (…)… Para usar esta».
* «Para que cuenten los cambios, quite RESUME»: los de «Seguir la simulación» sí cuentan (DUMPP y DUMPTO se leen del
  `.in`).
* La ayuda de «Elegir qué resultados pide el .in…» ofrecía «trayectorias», que ya no tiene.

### 11. El `.in` (3)

* **Las líneas nuevas** (RESUME, DUMPTO, DUMPP) se escribían encima de «>>>>>>>> Job properties.», en la sección
  anterior. Ahora, si tras la palabra anterior vienen comentarios y luego una palabra de la sección de la nueva, la
  nueva va con ella.
* **Un nombre de salida con un blanco delante** («DUMPTO  dump.dmp»): «no encontrará el fichero». penhmain no lo busca,
  lo crea: ahora «escribirá « dump.dmp» (con el blanco delante)».
* **Escribir el nombre sin el blanco** en «Reanudar de» no arreglaba la línea: se comparaba sin blancos.

### 12. Otros (5)

* **«RSEED −2 0»**, lo que escribe ARGUS en paralelo y en la cascada, daba un aviso falso: con un primero negativo, la
  segunda semilla no cuenta.
* **Las fracciones al combinar ejecuciones.** penhmain escribe la incertidumbre de la que sale como la suma de la de
  las primarias y la de las secundarias (ERR1 + ERR2), y ARGUS la combinaba como la de una sola magnitud: un 8 % menos
  que penhmain-sum. Ahora se rehacen las dos partes; queda un 3 %, por el redondeo de lo que escribe penhmain.
* **La cascada vuelca.** Sin DUMPP, penhmain vuelca igual en `dump.dmp` una vez al día y al acabar: cada ejecución
  dejaba su volcado (con un TAC, cientos de MB). Ahora lleva DUMPP 1e12, que no vuelca; la firma de las cascadas no
  cambia.
* Textos: «RESUME lo pone ARGUS en cada ejecución (…, sin RESUME)» y la lista de lo que no cuenta al comparar
  ejecuciones, que olvidaba los volcados.

## Datos de penhmain verificados en esta revisión

* Al reanudar, penhmain toma del volcado KPARP, NSEB/LSPEC/LLINE, NPSN, RLREAD y LPSF; ESRC y PSRC se leen y se
  descartan (las tres lecturas van a SHIST): las energías salen del `.in`.
* También toma del volcado IDCUT, NAGE/AGEL/AGEU/LLAGE, KKAD y los nombres de los ficheros de los detectores (el de la
  fluencia lleva el nombre de IDPSF). IPSF sale del `.in`.
* El resumen y el registro llevan la misma fecha, la del arranque (DATE23, PDATET en PMRDR0).
* Con un IRETRN, penhmain escribe el motivo y «IRETRN = n» y acaba con el código 0.
* penhmain-sum acaba con el código 0 tras «Inconsistent dump file format» o con otro título (STOP), y crea vacío el
  volcado que no existe (OPEN sin STATUS).
* Sin DUMPP, DUMPP vale 2e9: penhmain vuelca en `dump.dmp` una vez al día y al acabar. Con DUMPP ≥ 1e10, no vuelca.
* El DUMPTO se abre con IOSTAT al empezar (lo crea vacío y lo cierra) y se sobrescribe en el primer volcado (sin
  IOSTAT).
* Con RESUME (N ≠ 0), LTRACK no escribe trayectorias.
* `penhmain.dat` se escribe al empezar y al acabar (y con algunos avisos de la fuente).

## Para los autores de penhan

* **RESUME con IPSF 1** (el apartado 4): en PMRDR, leer el espacio de fases registro a registro hasta pasar RWRITE
  partículas y seguir escribiendo desde ahí (o reabrirlo con `POSITION='APPEND'`), en lugar del bucle de RDPSF con
  KODEPS = 0 en cada vuelta.
* **El tiempo de cálculo** de una simulación ya terminada: con JOBEND, `TIMET` se queda en 0 y se pierde el acumulado.
* **Los códigos de salida**: penhmain con un IRETRN y penhmain-sum tras «Inconsistent dump file format» acaban con 0;
  con `STOP 1` (o `ERROR STOP`) los guiones podrían saberlo.
* Lo de CTLOCATE de ADR-025.

## Lo que queda

* Lo que quedaba en ADR-025, igual.
* Si otra versión de penhmain escribiera el volcado en otro orden, ARGUS lo daría por dañado (antes no decía nada).
* El tiempo que falta con RESUME se cuenta desde la fecha del registro; un aviso de la fuente que penhmain escribiera
  ahí durante la simulación lo alargaría un poco.
* Las cascadas hechas antes conservan su `dump.dmp`: ARGUS no borra nada.

## Verificación

* 92 pruebas nuevas en tres ficheros:

  | Fichero | Pruebas |
  |---|---|
  | `tests/test_review12_dumps.py` | 66 |
  | `tests/test_review12_gui.py` | 18 |
  | `tests/test_review12_penhmain.py` (con el penhmain y el penhmain-sum de verdad) | 8 |

  Cubren:
  * los cinco desenlaces de RESUME, en los mensajes, en «Ejecutar», en «Trabajo» y en la cabecera;
  * los ficheros que no son volcados (un `.mat`, el `.in`, un espacio de fases, uno binario) y DUMPTO sobre una
    entrada, un resultado u otro fichero;
  * los volcados dañados, cortados, carpetas, en carpetas que no existen, sin «*** END ***» y con contadores dañados;
  * IPSF 1, los espacios de fases de entrada, IDCUT, las edades, ADKPAR y los nombres de los ficheros;
  * los textos (ya terminada, cada diferencia de la fuente, «1 partícula», LTRACK, RSEED, 30 s);
  * el `.in` (los blancos delante y dónde van las líneas nuevas);
  * el guion de «Ejecutar» con un IRETRN y `combinar.sh` con penhmain-sum (de mentira y de verdad);
  * el estado de la carpeta al reanudar, el tiempo que falta, el periodo, las fracciones combinadas y la cascada sin
    volcado;
  * con el penhmain de verdad: IPSF 1 (se detiene y pierde lo simulado; con IPSF 0 sigue), ya terminada (el tiempo a
    0), un fichero que no es un volcado (queda «Empty.»), un volcado cortado y uno sin «*** END ***», la cascada sin
    volcado, el guion con 3112 y la combinación con penhmain-sum.
* Cada prueba nueva falla con el código de 0.17.3, salvo siete controles: lo que debe seguir igual.
* Pruebas reescritas:
  * los volcados de mentira de `test_simcheck.py` y `test_matrix_review6.py`, que no eran como los de penhmain
    («(resto del volcado)»; KKAD, RWRITE y el proceso externo);
  * las que esperaban lo de antes: el volcado sin «*** END ***», un valor dañado que se callaba, «está vacío», DUMPTO
    en una carpeta que no existe (ahora un error), la cascada con DUMPP y el texto de las campañas;
  * `test_with_a_real_penhmain_resume_keeps_the_grid_of_the_dump`, que pasaba por casualidad (apartado 4).
* Con las correcciones, los guiones de los tres revisores ya no reproducen ningún fallo; se leyeron también los 908
  volcados que había en las pruebas de esta y otras revisiones: solo salen dañados los que se dañaron a propósito.
* La batería completa de pruebas y ruff; con la GPU (Mesa), las de la vista 3D, las trayectorias y el visor de la
  cascada.

## Consecuencias

* Lo que dice ARGUS de un volcado es lo mismo en todas partes y es lo que hace penhmain.
* ARGUS no deja que penhmain destruya un fichero del usuario por un nombre equivocado en RESUME o en DUMPTO.
* Con el espacio de fases de un detector, una simulación no se puede reanudar con penhmain 2025 sin perderla: ARGUS lo
  dice antes de ejecutar y propone la salida.
