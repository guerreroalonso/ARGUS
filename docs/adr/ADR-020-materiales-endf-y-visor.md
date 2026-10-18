# ADR-020 · Materiales (lista que busca, personalizados, asignar), ENDF, textos de la cascada y un visor más rápido

**Estado:** aceptada (versión 0.16.0; la fuente extensa en un cuerpo, 0.16.1; la revisión, 0.16.2)

## Contexto

Rafael (13-10-2026), probando 0.15.1:

1. Al cambiar en el diálogo del material de «lista de PENELOPE» a «composición propia» y volver salía un error (no
   había guardado nada en la composición propia porque solo estaba cambiando de botón).
2. Quiere elegir el material de la lista de PENELOPE en un desplegable con todos, que se acote al escribir, para no
   escribir un nombre que penhmat no puede generar.
3. Quiere guardar los materiales de composición propia en unos **materiales personalizados**, para no volver a
   escribirlos, editarlos y hacer copias (una aleación con otra proporción).
4. Si el usuario trae su geometría y su `.in` y ARGUS no identifica un material, asignarle fácilmente uno de la lista
   o de los personalizados para generarlo.
5. Que los ENDF que hacen falta se copien a la carpeta donde se simula, y poder decir dónde está la carpeta de los ENDF.
6. No se entendía «Trayectorias 30 (como mucho 50)» ni por qué la cascada va «en paquetes».
7. El visor de la cascada va algo lento al abrirlo: optimizarlo **sin cambiar nada de lo que se ve** («me encanta tal
   y como está»).

Con los materiales personalizados, pidió que fuesen **de cada usuario**, para cualquiera que se descargue ARGUS.

## Decisiones

### 1. El fallo al cambiar de «lista de PENELOPE» a «composición propia»

El campo del nombre tenía un `QCompleter` hijo del propio `QLineEdit`; al pasar a composición propia se le quitaba
con `setCompleter(None)` y Qt lo borraba (es su dueño). Al volver a la lista, Python seguía usando el objeto borrado:
«Internal C++ object (QCompleter) already deleted». El campo nuevo (`ui/material_picker.py`, `MaterialPicker`) tiene
su lista como hija suya y nunca se la quita: se puede cambiar de modo las veces que se quiera sin guardar nada.

### 2. El campo del material: todos los de la lista, acotados al escribir

* `MaterialPicker`: un campo con ▾ que despliega los 280 materiales de `database/material-list.txt` (y, delante, los
  personalizados, con ★); al escribir se acotan. Busca por palabras en cualquier orden, en español o en inglés, sin
  acentos, por número de la lista o por símbolo del elemento, con sinónimos («agua», «hueso cort», «pmma», «278»,
  «Pb»). Se elige con el ratón o con las flechas y Entrar. Lo mismo en el panel «Materiales» (al editar el nombre) y en
  «Asignar materiales…».
* `penhan/material_names.py`: el nombre en español de los 280 y algunos de uso corriente (PMMA, metacrilato, teflón,
  PVC…), y la búsqueda. El número es lo que cuenta (el que se le da a penhmat); el nombre en español solo se usa si
  el inglés de la lista del usuario es el que se espera para ese número (otra lista con otra numeración no recibiría
  un nombre equivocado). El `MFNAME` queda como «Agua líquida
  (PENELOPE 278)»; los `.in` de antes («Water, liquid (PENELOPE 278)» o el nombre de la lista en inglés) se siguen
  reconociendo. El fichero se propone con la primera palabra sin acentos (`agua.mat`).

### 3. Materiales personalizados, de cada usuario

* `penhan/library.py`: `materiales_personalizados.json` en la carpeta de datos del usuario
  (`core/userdata.py`: `%APPDATA%\ARGUS` en Windows, `~/Library/Application Support/ARGUS` en macOS,
  `$XDG_DATA_HOME/ARGUS` o `~/.local/share/ARGUS` en Linux; `ARGUS_DATA_DIR` la cambia, para las pruebas). No va en la
  carpeta del programa: vale igual descargado, instalado o ejecutado desde el código, y cada usuario del ordenador
  tiene los suyos. Cada material: nombre, composición (elementos y cantidades, por átomos o por masa; densidad; energía
  media de excitación), color y fecha. Se escribe entero o nada (un temporal que sustituye al anterior); si el
  fichero no se puede leer no se pisa: se aparta como `….dañado-<fecha>`.
* En el diálogo del material, con composición propia: «Guardarlo también en mis materiales personalizados» (si ya hay
  uno con ese nombre y otra composición, pregunta: sustituirlo, guardarlo con otro nombre o cancelar). Elegir uno
  personalizado en el campo trae su composición y su color.
* «Materiales personalizados…» (Opciones, el botón derecho del panel «Materiales» y el diálogo del material): la lista,
  Nuevo, Editar, **Duplicar y editar** (una copia, «X (copia)», para variar la aleación sin tocar el original), Borrar,
  «Guardar el que estoy escribiendo» y Exportar/Importar (lo que ya se tiene con la misma composición no se duplica; con
  el mismo nombre y otra composición, «X (importado)»).

### 4. «Asignar materiales…»

`penhan/materials.unidentified`: los materiales del `.in` sin `.mat` en la carpeta, sin composición propia, sin tejido
de TAC y cuyo nombre no es de la lista. Para ellos, en el panel (enlace en el aviso y botón derecho) y en «Generar .mat
y ENDF…», el diálogo `ui/assign_materials_dialog.py`: una fila por material con el campo que busca, ya propuesto lo que
sugiere el nombre del fichero o la descripción (`water.mat` → «Agua líquida (PENELOPE 278)»; se ve y se puede cambiar
o borrar), los personalizados y «Composición propia…». Todo lo asignado es un solo paso de deshacer.

### 5. Los ENDF

Solo los necesitan los `.mat` con datos de protones o alfas: penhmain los abre por su nombre en la carpeta donde
simula. ARGUS ya los copiaba (de junto al `.mat`, de la carpeta del caso y de `database/ENDF_data`) al ejecutar, en la
carpeta de simulación, en la cascada y en las simulaciones en paralelo. Ahora la carpeta de los ENDF se elige en
Opciones → Programas de penhan («Ficheros ENDF», por defecto la `ENDF_data` de la base de datos, con cuántos tiene)
y, si falta alguno, desde «Generar .mat y ENDF…» («Carpeta de los ENDF…»); se usa en todos esos sitios
(`PenhanSetup.endf`, ajuste `sim/endf`).

### 6. «Trayectorias» y «Lluvias»

* **Trayectorias** (`LTRACK`, en los resultados que pide el `.in`): cuántas de las primeras lluvias de la simulación
  escribe penhmain en sus `.trk`. penhmain guarda como mucho 50 (`NPSHOW = MIN(LTRACK, 50)`, fijo en `penhmain.f`):
  «Trayectorias 30» son las 30 primeras lluvias; «como mucho 50», ese límite. (0.16.2: escribe una más, LTRACK + 1 y
  como mucho 51, porque mira `N.GT.NPSHOW` después de cada lluvia; ver el punto 9.)
* **Lluvias** (al ejecutar, en «Trabajo»): la cascada para verla en 3D. Por ese mismo límite se simula aparte en
  tandas de 50 con otras semillas (1000 lluvias, 20 tandas; la primera, idéntica a las 50 primeras de la simulación).
* Las ayudas y las notas lo dicen donde se eligen («Ejecutar», resultados que pedir, panel de trabajo), con cuántas
  tandas serán; las etiquetas no cambian.

### 7. El visor de la cascada, más rápido sin cambiar nada de lo que se ve

Medido con la cascada de Rafael (`1_electrones_agua`: 1000 lluvias de electrones de 10 MeV en agua, 20 ejecuciones,
6 569 trayectorias, 547 786 puntos, 541 217 tramos), en el ordenador de pruebas:

* **Abrirla tardaba 8–11 s** (y lo mismo cada vez que se volvía a elegir): cada ejecución se reconstruía **dos veces**
  —una para saber si está terminada (sin la partícula primaria) y otra para sus lluvias (con ella)— porque lo guardado
  de cada ejecución dependía de la primaria aunque su fuente salga de su `.in`; y la mayor parte de cada
  reconstrucción era `_hall` (que las lluvias de distintas partículas de la fuente sean distintas), que miraba los
  intervalos [a, b] uno a uno con miles de operaciones pequeñas. Ahora la primaria solo cuenta si el `.in` no se puede
  leer (cada ejecución se reconstruye una vez y, al volver a abrirla, ninguna; lo guardado tiene un límite de memoria
  y se va lo más antiguo), y `_hall` calcula de una vez, con una tabla acumulada, cuántas caben en cada [a, b]. Sus
  reglas solo estrechan los intervalos y estrechan más cuanto más estrechos son: aplicadas en cualquier orden hasta
  que ninguna cambia nada se llega siempre al mismo sitio, así que el resultado es el mismo (el de antes se conserva,
  `_hall_loop`, y se comparan). Las líneas de los `.trk` se cuentan con numpy (`block_lengths`, lo mismo que línea a
  línea). **Abrir: 1,4 s; volver a abrirla: 0,3 s.**
* **Al verla:** los límites de las trayectorias se recalculaban en cada movimiento de la cámara (37 ms con 548 000
  puntos): ahora se guardan en los datos de la capa (`TrackLayerData`, que no cambian), como los datos de cada tramo y
  lo que se ve con cada filtro. Lo que se sube a la GPU se prepara al leer, en otro hilo (la primera imagen, de 190 a
  25 ms en la ventana) y cada punto se sube una vez (índices `GL_LINES`: la mitad de memoria y de trabajo de vértices).
  A la GPU solo van los tramos de las partículas y lluvias que se ven, en el mismo orden (`draw_ranges`; el shader ya
  descartaba los demás, así que la imagen es la misma): con una sola lluvia, mil veces menos. Las partículas en vuelo
  de cada imagen de la animación se calculan con los datos de cada tramo ya guardados (de 20 a 3,4 ms por imagen).
* **Lo mismo que antes:** 19 estados del visor (los cuatro colores, una lluvia, las 37 primeras, sin electrones, líneas
  finas, animación con estela, escalonada y con tiempo logarítmico, geometría oculta, opaca y tenue) dan imágenes
  idénticas píxel a píxel antes y después, con GPU (la cascada de Rafael) y sin ella (protones, `2_protones_SOBP`); y
  las mismas lluvias, madres, generaciones, tiempos y capas en todo el corpus: 62 cascadas y 340 carpetas con `.trk`
  (haces, fuentes radiactivas puntuales y extensas, espacios de fases, cortes), 402 entradas y 2 021 resultados, y el
  flujo del visor en 124 casos. `_hall` frente a `_hall_loop`: 85 000 casos al azar idénticos.
* Lo que no se ha tocado: el trazado de rayos de la geometría, la cadencia con que se refina la imagen y el dibujo de
  las 541 217 líneas con grosor cuando se ven todas las lluvias (eso es lo que cuesta ahora en cada imagen; con menos
  lluvias o sin una partícula, menos).

### 8. La fuente extensa en un cuerpo (0.16.1)

Rafael (14-10-2026), con 0.16.0 en el maniquí adulto del ORNL (`adult.geo`): la fuente extensa en el pulmón no salía
de todo el pulmón. Tres causas:

* «Fuente extensa limitada a…» (botón derecho en el corte) ponía una caja cúbica de una décima de la escena (20 cm en
  el adulto) **centrada en el punto pulsado**, no alrededor del cuerpo. penhmain sortea puntos en la caja y acepta los
  que caen en un cuerpo activo: del cuerpo solo salía lo que quedaba dentro de la caja.
* Marcar más cuerpos activos (en el panel o con el botón derecho) no cambiaba la caja: un cuerpo fuera de ella no
  emitía nada.
* En los maniquíes un órgano son varios cuerpos: el pulmón son siete «BODY LUNGS» (124 a 130), con la misma
  descripción y el mismo material; marcar uno es marcar un trozo.

Después, ese mismo día: «muy importante» que la caja que incluye una figura compleja emita solo en lo seleccionado y no
en toda la caja, para cualquier geometría extraña (en el maniquí hay muchas) y no solo los pulmones, y **poder coger
uno o los dos**.

Lo primero es lo que hace penhmain con `SBODY` (penhmain.f, SOURC0/SOURCE): sortea puntos uniformes en la caja y solo
acepta los que LOCATE sitúa en un cuerpo activo; «la actividad es uniforme en la intersección de los cuerpos activos y
la caja». La caja solo dice dónde sortea: con ella justo alrededor de lo elegido, las partículas salen de todo lo
elegido y solo de ahí (lo rayado en el corte; en 3D, los cuerpos activos se tiñen).

Ahora:

* Botón derecho sobre un cuerpo (en el corte y, nuevo, también en 3D): **«Fuente extensa en ▸»** con el cuerpo
  («Cuerpo «126» (BODY LUNGS)»), **su parte del órgano** («Este «BODY LUNGS» (4 cuerpos unidos)»: un pulmón) y **todo
  el órgano** («Los 2 «BODY LUNGS» (7 cuerpos)»: los dos; «Todo «BODY THYROID» (5 cuerpos)» si es de una pieza). La
  caja `SBOX` queda justo alrededor de lo elegido, con su caja exacta (la de `pengeom/extent.py`, la misma que usan
  pegar y alinear), y eso son los cuerpos activos; un mensaje lo dice. La caja se redondea a una rejilla de cuatro
  cifras de su tamaño con un margen de una o dos marcas por lado (27,02 × 15,02 × 24,02 cm en los pulmones): llega a
  todo y penhmain apenas pierde intentos.
* El órgano de un cuerpo (`source_edit.body_group`) son los cuerpos con su misma descripción y material (sin distinguir
  mayúsculas; un módulo solo con módulos); si no tiene descripción, el cuerpo solo. Sus **partes**
  (`source_edit.connected_parts`, `MainWindow.organ_parts`) son los cuerpos que se tocan: dos cuerpos van juntos si sus
  cajas exactas se tocan o se solapan (1e-6 del tamaño), y una parte es todo lo que se alcanza así. Dos cuerpos que se
  tocan nunca quedan en partes distintas. Las cotas analíticas (al momento, siempre mayores) separan antes lo que está
  lejos y solo las partes de más de un cuerpo necesitan las cajas exactas; el submenú se rellena al abrirlo, para que
  el menú no espere. En el adulto: los pulmones (124, 126, 128 y 130; 125, 127 y 129), los riñones, los brazos (cuatro
  trozos cada uno), las mamas, los ovarios, los ojos, las doce costillas; el tiroides, el corazón o la columna, de una
  pieza.
* Con la fuente ya extensa: «Cuerpo activo de la fuente: …» y **«Añadir a los cuerpos activos ▸»** su parte o todo su
  órgano. **Si se añade algo que la caja no cubre entero, la caja se ajusta** a todos los activos (un paso de deshacer,
  con un mensaje que lo dice); quitar uno no la cambia. Si se quiere de verdad solo una parte de un cuerpo, se deshace
  o se cambia la caja a mano después.
* El panel «Fuente» enseña la descripción de cada cuerpo en la lista (y se filtra por ella: «lungs»), **avisa** (⚠)
  si la caja no cubre enteros los cuerpos activos y tiene «Ajustar la caja a los cuerpos activos»; el botón derecho
  también la ofrece cuando hace falta. Un `.in` hecho con 0.16.0 se arregla así con un clic. La envolvente que añade
  PENGEOM (`SBODY 0`) no cuenta: ninguna caja la cubre.
* Para avisar no hace falta calcular la caja exacta con rayos si la caja de la fuente cubre la cota analítica del
  cuerpo (la intersección de las cajas de sus superficies, que siempre lo contiene; al momento). Si no, se calcula la
  exacta (en el adulto, 0,2–0,5 s por cuerpo en el ordenador de pruebas, una vez: se guarda con la geometría).
* Con una capa fina (la piel, el líquido cefalorraquídeo) la caja justa sigue siendo casi toda hueca: penhmain acierta
  en un 2 % de los intentos y, desde el intento 200 de una partícula, escribe un aviso por intento. La simulación es
  correcta; el aviso de ARGUS (de 0.12.2) dice ahora cómo ajustar la caja y que, si ya está ajustada, es por la forma.

Comprobado con el penhmain de Rafael (electrones de 10 keV, que se quedan donde salen; los materiales del `.geo` de
prueba pasados a uno, porque su penhmain admite menos de 12):

* En el tórax, con un mapa de dosis de 1 cm³: con la caja de antes salían del 45 % del pulmón (solo del de x < 0, entre
  z = 45 y 65 cm): energía en 1 254 de los 2 592 vóxeles enteramente de pulmón. Con los dos pulmones: en los 2 592,
  uniforme (13 % de dispersión, la estadística de unas 60 partículas por vóxel), 54 % / 46 % entre los dos y el mismo
  perfil en z que el volumen de pulmón; nada en el resto de la caja.
* **Todos los órganos del adulto**: los 68 (cuerpos con la misma descripción y material) y las 32 partes de los que
  tienen dos, tres o cuatro, 100 casos con la caja y los cuerpos que pone «Fuente extensa en ▸» y la energía de cada
  cuerpo de `penhmain-res.dat`: la caja cubre todos los cuerpos, **el 100 % de la energía queda en los cuerpos
  elegidos** (nada en el resto de la caja) y se reparte entre ellos según su volumen (LOCATE en 200 000 puntos de la
  caja; la mayor diferencia, 2,7 σ en 219 cuerpos). En 11 casos de capas finas o piezas separadas (piel, líquido
  cefalorraquídeo, costillas, escápulas) penhmain acierta en un 1,7–4 % de los intentos y escribe sus avisos.

### 9. Revisión de 0.16 (0.16.2)

Una vuelta en busca de fallos de 0.16.0 y 0.16.1 (análisis estático, cuatro revisores en paralelo —materiales, ENDF y
ejecutar, fuente extensa, visor— y uso real); cada fallo, con su prueba.

**Fuente extensa en un cuerpo.** La caja salía de la caja exacta del punto 8, hecha con rayos (lo que hace STEP), pero
penhmain sortea con LOCATE, y en los maniquíes infantiles del ORNL, donde los cuerpos se solapan, no coinciden: en el
de 1 año, «Todo «BODY THYROID»» daba 29 × 24 × 80 cm con un 0,0015 % de tiroides (303 467 avisos de penhmain en 20
lluvias), y en el de 10 años la caja del bazo dejaba fuera un 5,7 % (sin dosis a partir de z = 30 cm, con penhmain).
Ahora la caja de la fuente es la de lo que LOCATE sitúa en el cuerpo (`extent.source_extent`): muestreo en su cota
analítica cortada con la de sus módulos, crecimiento por los bordes y ajuste fino de cada cara. Un cuerpo vacío (que
LOCATE no encuentra) se dice y no cuenta; uno sin cota en algún eje, también. Lo usan el menú, el aviso del panel y
«Ajustar la caja…»; comprobado con 0 puntos fuera en todos los órganos del adulto y del recién nacido, y con penhmain
en el bazo del de 10 años (la dosis más allá de 30 cm, la del elipsoide entero). Además: más de 200 cuerpos activos
(`MKBS`) no se ofrecen; el botón derecho conserva las referencias de `SBODY` que no son de la geometría; sin geometría,
desmarcar un cuerpo ya no falla; con una fuente radiactiva el aviso de eficiencia cuenta desde el intento 100 (no 200).

**Visor de la cascada.** Lo guardado de cada ejecución se usa desde varios hilos (la cascada que se simula, el visor,
la ventana de resultados) y un cambio a la vez podía parar la cascada: ahora con un cerrojo. Al abrir, lo que se está
leyendo no se expulsa (con el límite de memoria se reconstruía tres veces), la firma de los `.trk` incluye el nodo y
la fecha de cambio, y sin GPU se sueltan los datos preparados para ella.

**La cascada de protones.** En el caso 2 de Rafael ninguna tanda se reconstruía: una alfa lenta de una reacción nuclear
repite su primer punto en sus primeros pasos (las seis cifras de penhmain) y esos puntos contaban como vértices; la
alfa era madre posible de sus hermanas, sobraba un primario y hacían falta los 49 cortes (unas 4 h por tanda). Ahora
son vértices «débiles» (solo cuentan si no hay otro en ese punto): la tanda sale exacta sin cortes, igual que el
principio exacto de 30 lluvias que da la propia simulación. La firma de la cascada ya no depende de dónde están los
ENDF (sus nombres los dan los `.mat`): copiarlos junto al `.in` no obliga a simularla otra vez (las cascadas de 0.16.0
y 0.16.1 se siguen reconociendo).

**ENDF.**

* Fuente radiactiva: penhmain los lee si el nucleido emite alfas de más de 100 keV (`1,01·EHMAX`, PENNUC0 y SOURCE0),
  y ARGUS lo mira en el `.nuc` (`hadron_data_needed`); antes los daba por opcionales y, con el At-211, «Ejecutar»
  dejaba simular y penhmain se paraba (IRETRN 40028). Sin alfas no se copian, y se dice por qué.
* La carpeta de los ENDF sigue a la base de datos: la `ENDF_data` de la base de datos se guarda como «la de la base de
  datos», al elegir una base de datos sin `ENDF_data` el campo se vacía y al cambiar «Carpeta de penhan» cambia lo que
  estaba dentro de la vieja. Con el campo vacío dice que se usan los de la base de datos.
* En Linux se encuentran con otras mayúsculas («PO16_ICRU63.ENDF») y se copian con el nombre que da el `.mat`.
* «Ejecutar» con ENDF que faltan dice dónde elegir su carpeta y tiene el botón del diálogo de los ENDF; con un espacio
  de fases, «Generar .mat y ENDF…» da el motivo bueno.

**«Lluvias» y «Trayectorias».** Las tandas de «Lluvias» son las del penhmain que se usará (lo aprendido con su prueba:
con uno que guarda 99, 11 tandas para 1000 lluvias, no 20). penhmain escribe en los `.trk` LTRACK + 1 lluvias (como
mucho 51), y los textos lo dicen.

**Materiales.**

* El `.mat` que se propone ya no es nunca uno de la carpeta de otro material (de otro `.in`, de uno que se quitó): con
  la lista se compara el número de su cabecera, con composición propia su composición; el que ya es ese material se
  aprovecha y, si se escribe a mano el de otro, avisa. La tabla y «Generar .mat y ENDF…» también lo ven (`stale`).
* Entrar o Tab con la lista desplegada elegían la primera fila sin haberla señalado (con composición propia, el nombre
  «Agua» y dos Entrar añadían el n.º 278): lo escrito se queda; con ↓ y Entrar, o el ratón, se elige.
* La lista elige por la fila pulsada, y los personalizados no llevan «(PENELOPE n)» (se quita al guardarlos y al leer
  los de 0.16.0; al pasar a composición propia, el nombre deja de llevarlo y al volver a la lista lo recupera): un
  personalizado ya no ocupa el lugar de uno de la lista.
* Materiales personalizados: lo que esta versión no entiende (un material que no se puede leer, campos o valores de
  otra versión, la nota) se escribe tal cual y la versión del fichero no baja; antes de cada cambio se relee (otro
  ARGUS abierto a la vez); los nombres son únicos sin distinguir mayúsculas y se limpian también al importar. El
  diálogo se abre con un material mal escrito a mano (Z fuera de 1-99, densidad 0 o NaN) y deja arreglarlo.
* Propuestas de «Asignar materiales…»: «H2O.mat» proponía el hidrógeno, «CO2.mat» el cobalto, «water_1cm.mat» el
  curio. Ahora se reconocen las fórmulas químicas con la composición de cada material de la lista
  (`pdfiles/pdcompos.pen` de la base de datos; con dos estados del mismo compuesto, el más denso), los códigos enteros
  («C-552», «A150»), no cuentan las unidades tras un número, los símbolos pesan menos que las palabras y, con un
  empate («hueso.mat»), no se rellena nada. Una fórmula que no es de la lista no se toma por sus símbolos. La búsqueda
  encuentra la etiqueta entera y «penelope 278»; los personalizados se buscan por su nombre y sus elementos.
* «Composición propia…» dentro de «Asignar materiales…» no cambia nada hasta «Asignar» (un solo paso de deshacer;
  «Cancelar» no deja nada) y «Asignar materiales…» sigue abierto al cerrarla (la ventana lo liberaba al mostrar el
  siguiente diálogo y se cerraba con él). Una fila con un fichero que no vale para una composición propia (en otra
  carpeta) lo dice y no impide asignar las demás.
* Sin la lista de PENELOPE, «… (PENELOPE 278)» se reconoce por el número; las columnas de `materiales.txt` se
  ensanchan con los nombres largos.

**Segunda pasada.** Dos revisores más, sobre lo cambiado en esta revisión (por si los arreglos habían roto algo), y
la prueba de mono con los casos de Rafael (127 casos, sin fallos):

* La caja de la fuente se buscaba en la cota del cuerpo cortada con la escena (la rejilla de 40³ de las vistas): un
  detector pequeño lejos de lo demás salía «vacío» (también la base y el pie de la copa de PenGeomJar) y una varilla
  que sale de la escena se quedaba en su borde (la búsqueda solo se ampliaba si lo encontrado llegaba a 1e-6 de él, y
  el muestreo no llega). Ahora se busca en toda la cota donde es finita y, donde no, en la escena ampliada mientras el
  cuerpo llegue a su borde (con la tolerancia del muestreo); por fuera de cada cara, también en franjas finas de toda
  su sección (los extremos estrechos de algunos huesos de los maniquíes de 1, 5, 10 y 15 años quedaban fuera). Sin la
  envolvente de PENGEOM, el módulo raíz no corta la caja (LOCATE no mira sus superficies). En los 789 cuerpos de los
  seis maniquíes del ORNL, 500 000 puntos de LOCATE por cuerpo: todos dentro, salvo uno de una punta finísima del
  corazón del de 1 año (una centésima de cm más allá; una parte del orden de 1e-6 del cuerpo).
* El menú «Fuente extensa en ▸» de un órgano de muchas piezas separadas medía la caja de cada una para saber si
  estaba vacía (205 cuerpos: 68 s); ahora, unos puntos al azar en su cota (0,9 s).
* La firma «de antes» de la cascada no era la de 0.16.0–0.16.1 con una fuente radiactiva sin alfas (entonces se
  contaban los ENDF que hubiera junto al `.in`): esas cascadas se simulaban otra vez.
* «Ejecutar» con otro penhmain en la carpeta y «Sustituirlo por el elegido» contaba las tandas con el de la carpeta.
* Lo guardado de una cascada que se acaba de leer se expulsa al acabar si no cabe, y dos lecturas a la vez no se
  sueltan una a otra.
* «Asignar materiales…»: dos «Composición propia…» con el mismo `.mat` nuevo se pisaban al asignar (ahora el segundo
  dice que ese fichero ya es de otro material), y una que no identifica nada dejaba asignado lo de antes.
* Materiales personalizados: un Z escrito a mano como 1e999 impedía arrancar ARGUS; lo de fuera de la lista de una
  versión más nueva y un color de otra forma se perdían; un cambio de otro ARGUS del mismo tamaño, con fechas poco
  finas, no se notaba (ahora también el nodo); «Exportar…» escribía el fichero solo legible por su dueño.
* Un nombre que ARGUS no identifica ya no se queda con el `.mat` de otro material que está en la carpeta (se propone
  otro y, si se escribe ese, se dice qué es); un nombre propio con «(PENELOPE n)» recupera su número al volver a la
  lista; un «(PENELOPE n)» que no está en la lista del usuario se puede asignar; materiales.txt da el motivo bueno
  con una fuente radiactiva sin alfas (y no pide datos de hadrones); «air.mat» propone otra vez el aire seco.
* Textos: «2 materiales se quedan sin asignar», «Asignado 1 material: ya se puede generar su .mat», «añadirlo
  después», «falta 1 fichero ENDF…: diga dónde está», sin dos puntos seguidos en «no se copian».

## Verificación

* `tests/test_review016.py` y `tests/test_gui_review016.py` (0.16.2): la alfa lenta, la fuente radiactiva con y sin
  alfas (también en el límite de 100 keV y con el `.nuc` en la base de datos), la firma de la cascada con los ENDF en
  otro sitio, los ENDF con otras mayúsculas, lo que dice «Ejecutar» de lo que falta, la carpeta de los ENDF en
  «Programas de penhan», el motivo con un espacio de fases, las tandas con el penhmain que guarda 99 y los plurales;
  de la segunda pasada, la caja de un cuerpo lejos de la escena, de una varilla que sale de ella, de una hija que
  asoma del módulo raíz, de la copa y la cuádrica de PenGeomJar y del hueso del brazo del maniquí de 10 años (las
  cinco fallan con lo de antes), la firma de antes con un nucleido sin alfas, lo guardado de una cascada al soltarla,
  la biblioteca (Z infinito, lo de otra versión, el nodo, los permisos), un «(PENELOPE n)» que no está en la lista,
  materials.txt con un nucleido sin alfas, «air.mat», el singular y las tandas con el penhmain que sustituirá al de
  la carpeta. Las de los materiales, en las dos de abajo (también las de la segunda pasada: dos composiciones con el
  mismo `.mat`, una que no identifica nada, el número de un nombre propio, un nombre sin identificar con el `.mat` de
  otro material y los textos de «Asignar»); las del menú de la fuente, en `tests/test_extended_source.py`.
* `tests/test_material_library.py` (34 en 0.16.0; 66 en 0.16.2): nombres en español, búsqueda y propuestas, la carpeta de datos del usuario,
  la biblioteca (guardar, nombres libres, duplicar, importar, fichero ilegible), `unidentified`, los ENDF de una
  carpeta elegida, la carpeta de simulación y los ajustes.
* `tests/test_gui_material_library.py` (12 en 0.16.0; 32 en 0.16.2): el fallo de cambiar de modo (falla con 0.15.1), el campo que despliega y
  acota, un personalizado con su composición y color, guardar con conflictos, el diálogo de materiales personalizados
  (también con un material mal escrito a mano en el fichero: se lista con su problema y se puede abrir y arreglar),
  asignar desde la ventana (propuesta, deshacer, personalizado, guardar), la tabla, «Generar .mat y ENDF…» con
  asignar y la carpeta de los ENDF, «Programas de penhan» y los textos de «Ejecutar».
* `tests/test_cascade_speed.py` (10 en 0.16.0; 14 en 0.16.2): `_hall` frente a `_hall_loop` (4000 casos), `block_lengths` frente a línea a
  línea, una sola reconstrucción por ejecución (y ninguna al volver a abrir; lo mismo que con `_hall_loop`), la
  primaria solo sin `.in`, el límite de lo guardado, los datos guardados de la capa y las partículas en vuelo iguales
  que antes, lo que se manda a la GPU y, con GPU, la misma imagen mandando solo lo que se ve que mandándolo todo.
* `tests/test_extended_source.py` (14 en 0.16.1; 29 en 0.16.2): la caja alrededor de unos cuerpos (redonda, con su margen, también de
  un punto), `box_contains` y `set_box_around`, las partes de un órgano (`connected_parts`), el órgano de un cuerpo en
  `adult.geo`, que todo punto que LOCATE sitúa en cada trozo del pulmón cae en su caja (40 000 puntos por trozo) y, con
  `ARGUS_SLOW_TESTS`, en la de cada uno de los 100 órganos y partes del adulto; en la ventana: «Fuente extensa en ▸»
  en un trozo, en un pulmón y en los dos (deshacer y quitar un cuerpo), «Añadir a los cuerpos activos ▸», las partes de
  los riñones, los brazos, las costillas y el tiroides, añadir un cuerpo que la caja no cubre (se ajusta; deshacer en un
  paso), el aviso del panel con la caja de 0.16.0 y su botón, la lista con la descripción, una caja holgada sin rayos,
  la envolvente de PENGEOM (`SBODY 0`), que no cuenta para la caja, y el menú de la vista 3D.

## Consecuencias

* Elegir un material es elegirlo de la lista (o de los propios), no escribir un nombre que penhmat no conoce; las
  composiciones propias se escriben una vez y se reutilizan, también entre ordenadores (exportar e importar).
* Un `.in` con materiales que ARGUS no conoce se completa desde ARGUS sin escribir nada a mano.
* El visor de la cascada se abre en poco más de un segundo y se mueve con menos trabajo en cada imagen; lo que se ve es
  lo mismo. Con todas las lluvias, lo que más cuesta en cada imagen es dibujar sus tramos en la GPU.
* La fuente extensa en un cuerpo sale de todo él y solo de él, o de su parte del órgano, o de todo el órgano, en
  cualquier geometría; y la caja no se queda corta en silencio al cambiar los cuerpos activos (0.16.1).
