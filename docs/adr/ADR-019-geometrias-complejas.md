# ADR-019 · La pestaña «Geometrías complejas» con el formato de PenGeomJar

**Estado:** aceptada (versión 0.15.0; revisada en 0.15.1)

## Contexto

Rafael pidió (11-10-2026) una forma de crear geometrías muy complejas como la del programa anterior, PenGeomJar:
sus «Edition Tabs» (Surface, Surface (implicit), Body, Module, Clone, Include y CT, con sus campos y botones) le
gustan a mucha gente. Quiere una pestaña de «geometrías complejas» con ese formato; al usarla, otra pestaña o la
vista de geometría de siempre fuera de la vista, para centrarse en construir la geometría, y ver a la vez y en tiempo
real el texto, el formato del programa antiguo y la geometría que se va construyendo. Más adelante habrá otra
pestaña de geometrías complejas con otro formato (no es de esta versión).

Para copiar el formato se estudió PenGeomJar 1.21 (`pengeom.jar` descompilado, solo como referencia: nada suyo va en
ARGUS) y se capturaron sus siete pestañas con el ejemplo `chair`.

## Decisiones

### 1. Dónde: una pestaña de la ventana principal

* La zona central de la ventana tiene dos pestañas: **«Geometría»** (lo de siempre) y **«Geometrías complejas
  (PenGeomJar)»** (también Ver → «Geometrías complejas (formato PenGeomJar)», `F6`). Al entrar se ocultan todos los
  paneles (se recuerda cómo estaban y vuelven igual al salir) y la pestaña tiene tres columnas: el texto del `.geo`,
  las pestañas de PenGeomJar y la geometría en 2D y 3D (las mismas vistas de la ventana, que se traen aquí, una encima
  de la otra; F2, F3 y F4 siguen eligiendo corte, 3D o las dos). Las columnas se pueden ensanchar y se recuerda su
  tamaño.
* Se trabaja sobre la geometría abierta (el mismo documento, el mismo deshacer, el mismo guardar, y el `.in` sigue a
  los cuerpos). Sin geometría, «No geometry loaded» con «Nueva geometría…» (el diálogo de siempre, con «Nada
  (geometría vacía)» elegido) y «Abrir…». Al entrar se elige el primer bloque (PenGeomJar no elige nada hasta que se
  pulsa una línea; así la pestaña enseña algo desde el principio).
* Ver la pestaña de PenGeomJar dentro de ARGUS en vez de una ventana aparte evita dos vistas 3D (dos contextos de
  OpenGL en una Intel UHD) y que la geometría de una ventana y la de la otra se desincronicen.

### 2. El formato, como PenGeomJar

* **Texto** a la izquierda con los números de línea; al pulsar una línea se elige su bloque (de los comentarios que
  lo preceden a su línea de ceros), que se resalta en azul y queda seleccionado como texto (`Ctrl+C` lo copia), como
  en PenGeomJar. Encima,
  **Relabel**, **Manual edit** y **End manual edit**; debajo, elegir el primer, anterior, siguiente o último bloque y
  subirlo o bajarlo (los atajos de PenGeomJar: `Alt+F`, `Alt+P`, `Alt+N`, `Alt+L`, `Alt+↑`, `Alt+↓`) y «Block starts:
  line N».
* **«File Viewer» / «Edition Tabs»**: como en PenGeomJar, una copia fija del texto que se edita, tomada al pulsar
  «Update the File Viewer» (`Alt+U`), para tenerla delante mientras se cambia; y las siete pestañas, con los
  nombres, campos y botones de PenGeomJar (las palabras clave de PENGEOM; las ayudas emergentes y los mensajes, en
  español):
  * **Surface**: «SURFACE :» (pulsando la etiqueta pasa a SURFACE*), etiqueta y descripción, los cinco **INDICES**
    en listas de -1, 0 y 1, el tipo de cuádrica (las diez de PenGeomJar —Plane, Sphere, Cylinder, Hyperbolic
    cylinder, Cone, One-sheet y Two-sheet hyperboloid, Paraboloid, Parabolic cylinder, Hyperbolic paraboloid— o
    «User-selected indices») y X/Y/Z-SCALE, OMEGA/THETA/PHI (con DEG o RAD) y X/Y/Z-SHIFT, cada uno con su valor y
    su índice PARINP. Los valores se enseñan tal como están escritos en el fichero; un campo vacío (rosado, como en
    PenGeomJar) es una línea que no se escribe.
  * **Surface (implicit)**: AXX…A0 y, tras la línea de unos, OMEGA…Z-SHIFT.
  * **Body**: etiqueta, descripción, MATERIAL (con el nombre del material del `.in` al lado, en rojo si el `.in` no
    lo declara; un bloque nuevo propone el 0, como PenGeomJar) y las tablas LIMITING
    SURFACE (con SIDE POINTER), LIMITING BODY y LIMITING MODULE, cada una con su campo, Add y Delete (doble clic en el
    lado lo cambia).
  * **Module**: lo mismo con INNER BODY e INNER MODULE (las hijas) y la transformación del módulo.
  * **Clone**: el clon, ORIG. MODULE (con el texto que va detrás de su etiqueta) y la transformación.
  * **Include**: «INCLUDE :» (pulsando, INCLUDE*) y el fichero (en la carpeta de trabajo).
  * **CT**: la caja del CT, «CT File Name» y su colocación.
* **Botones** (los de PenGeomJar): **Delete** borra el bloque elegido; **Modify** lo reescribe con lo que hay en su
  pestaña; **New** vacía la pestaña (con una etiqueta libre propuesta) y bloquea las otras hasta **Add** o
  **Cancel**; **Add** añade el bloque detrás del elegido (mientras hay un bloque elegido se llama **Duplicate**,
  como en PenGeomJar); **Cancel** descarta lo escrito. Lo que dicen los botones (lo hecho o por qué no) se ve en la
  pestaña, junto a ellos, y debajo del texto.

### 3. Lo que añade ARGUS (`pengeom/blocks.py`)

* Cada botón es **un paso de deshacer** de la ventana, y **solo se hace si PENGEOM aceptaría la geometría**; si no,
  la razón de GEOMIN aparece debajo del texto («La superficie '9' no está definida (antes de este cuerpo)») y el
  cursor va al campo. Así la geometría que se ve es siempre la que leería PENGEOM. **Manual edit** es la salida para
  lo que no se puede hacer bloque a bloque: el texto se escribe libremente y, si PENGEOM no lo aceptaría, se
  pregunta («Seguir editando» o «Aplicar así»); también sirve para arreglar un `.geo` que se abrió con errores.
* Un bloque reescrito conserva sus comentarios y, línea a línea, el texto de lo que no cambia (los valores tal como
  estaban escritos, el «(DEFAULT=…)» de detrás, el orden y las repeticiones de los parámetros, cómo se escribió el
  lado; lo que va detrás de la columna 70 de la cabecera si solo cambia la etiqueta; lo de detrás de un parámetro
  que cambia): **Modify sin cambiar nada deja el fichero igual byte a byte** (comprobado en los 43 `.geo` del
  corpus).
* Un valor que no se toca vale lo que leyó GEOMIN: «250» sin punto decimal, en un campo E22.15, es 2.5E-13 (no 250);
  un índice PARINP negativo es «ninguno» (geometry.rep los escribe así) y se queda; un nombre de fichero, tal cual.
  Lo que se escribe tiene que ser un número («-» o «.» solos no son 0; «1.0-100», como lo escribe gfortran, sí) y
  lo que se guarda es lo que leerá PENGEOM de su E22.15. Los campos no cortan lo que se pega: lo que no cabe se dice.
* Sin `.in`, los PARINP son los números escritos en el propio `.geo`, también los de las líneas que se añaden o
  cambian (un índice nuevo trae su valor; «Nuevo .in» los escribe en sus líneas PARINP).
* Las etiquetas se comparan con sus cuatro columnas, como hace PENGEOM («1   » y «   1» son etiquetas distintas). Lo
  que se escribe en un campo es la etiqueta que ya existe si coincide sin contar los blancos («S1» → «S1  »); si
  no, se alinea a la derecha, como las escribe PenGeomJar.
* **Modify** de una etiqueta cambia también sus referencias (y, si es un cuerpo, las del `.in`). **Duplicate** con
  una etiqueta que ya existe le da una libre (lo dice); **Add** de un bloque nuevo (New) usa la que se ha escrito y,
  si ya existe, lo dice. El bloque baja, si hace falta, hasta después de lo que nombra (PENGEOM exige que esté
  definido antes); si tiene las mismas superficies, cuerpos y módulos que otro, se avisa (ocuparían el mismo sitio).
  **Delete** no borra lo que otro bloque usa (dice quién) y deja elegido el bloque de antes. Si al borrar o
  reescribir un bloque desaparecen cuerpos que el `.in` nombra (el suyo, los de un módulo, las copias de un clon,
  los de un fichero incluido; también un CLONE que pasa a copiar otro módulo), se enseñan antes las líneas del `.in`
  que se quitarían (con las de los detectores que se quedan sin cuerpos) y se pregunta.
* **Relabel**: en PenGeomJar sustituía el fichero por la copia de `geometry.rep` (clones e `INCLUDE` desplegados).
  Aquí las etiquetas pasan a ser **los números que les da PENGEOM** (los de `geometry.rep` y los que usa penhmain en
  el `.in`), con todas sus referencias, sin desplegar clones ni `INCLUDE` (sus copias cuentan en la numeración: puede
  haber saltos); el `.in` sigue a los cuerpos. Se pregunta antes y se puede deshacer. No se hace si cambiaría la
  geometría (se comprueba con `identity.geometry_change`) ni si un fichero incluido con `INCLUDE*` (que comparte
  las etiquetas con el principal) nombra etiquetas que cambian o tiene una de las nuevas.
* **En tiempo real**: cada cambio se ve al momento en el texto y en las vistas 2D y 3D. Con **«Vista previa al
  escribir»** (marcada por defecto; se recuerda) lo que se escribe en una pestaña se ve ya, antes de pulsar Modify o
  Add: el bloque en el texto, en naranja, y la geometría en las vistas; si PENGEOM no lo aceptaría, se dice por qué.
  Lo mismo al escribir a mano. La vista previa no cambia nada: guardar, deshacer, abrir o cambiar de bloque la
  quitan (lo escrito en la pestaña sigue ahí), y un clic en las vistas mientras se ve elige el cuerpo de verdad.
* Si la geometría cambia por debajo (deshacer, rehacer, otra edición), la pestaña vuelve a leer su bloque; si en ella
  había algo escrito, se queda y se avisa («El bloque ha cambiado…»), para no aplicar después valores de antes. Lo
  escrito en una pestaña sin aplicar no se pierde sin preguntar: elegir otro bloque (en el texto, en las vistas o con
  los atajos), New, mover, Relabel, Manual edit o cerrar el caso preguntan antes.
* **Move block** (`Alt+↑`/`Alt+↓`, desde la columna del texto: en las pestañas, `Alt+↓` abre las listas): PENGEOM
  aplica las transformaciones de los módulos en el orden en que se definen y, con superficies compartidas, el
  orden puede cambiar dónde quedan; si mover el bloque cambiaría la geometría (no solo la numeración), se dice qué
  cambia y se pregunta.
* **Manual edit** conserva byte a byte lo que no se toca (los finales de línea —también el de una línea cambiada—,
  los caracteres especiales) y dice en qué línea y columna está el cursor. Si se guarda, se deshace, se abre otro
  fichero o se cierra mientras se escribe a mano, lo escrito se aplica (un paso de deshacer); si PENGEOM no lo
  aceptaría, se pregunta antes y, con «Seguir editando», lo pedido no se hace. Mientras se escribe a mano la
  geometría no se edita desde otros sitios (menús, `Supr`, el botón derecho, el panel «Editar», arrastrar módulos):
  al terminar, el texto escrito desharía ese cambio.
* Lo elegido se ve en la geometría: un cuerpo, módulo, clon o CT, seleccionado; una superficie, los cuerpos que limita
  resaltados y **su traza en el corte 2D** (discontinua, con su nombre; `render/quadric_trace.py`), que PenGeomJar no
  tenía: ayuda a ver dónde queda una superficie antes de usarla. Un cuerpo pulsado en las vistas elige su bloque.
* Las pestañas van en áreas con barras de desplazamiento: caben en pantallas pequeñas; la columna del centro se
  ensancha al entrar si sus siete pestañas no caben (sin fijar un mínimo a la ventana).
* Los errores de PENGEOM dicen la línea y, si son de un fichero incluido, el fichero («inc.geo, línea 8: …»). Un
  cambio que PENGEOM acepta pero con el que penhmain no podría simular (p. ej. un material que el `.in` no declara)
  se hace y se dice en la pestaña («Atención: …»), porque el panel de mensajes no se ve en ella.
* En geometrías grandes (la `mlc`, de 16 000 líneas) la vista previa espera más a que se deje de escribir si la
  anterior tardó (hasta 3 s), y la caja de un módulo (para sus asas en el corte) se calcula un momento después de
  elegirlo, solo la del último elegido.

### 4. Cambios de paso

* `CloneElement.module_note`: el texto que va detrás de la etiqueta en la línea `MODULE` de un `CLONE` (PENGEOM no
  lo lee; PenGeomJar lo enseña en «ORIG. MODULE»).
* El escritor conserva el formato de una referencia cuando solo cambia su etiqueta (`SIDE POINTER=( 1)` sigue así).
* `edit.reparse` lee de la carpeta de trabajo un `INCLUDE` que no estaba (añadido desde la pestaña Include).
* `GeoController.replace_text` (Manual edit) y `preview_edit`/`preview_text` (vista previa sin aplicar).
* La identidad de los cuerpos de un fichero incluido (`pengeom/identity.py`, con `BodyEntry.include`) es el fichero,
  la vez que aparece y su etiqueta, con «*» o sin él: las etiquetas de un `INCLUDE` sin «*» llevan un sufijo que
  depende de cuántos `INCLUDE` van delante, así que mover o borrar un bloque `INCLUDE` las cambiaba y el `.in` seguía
  al cuerpo equivocado; y el mismo fichero incluido dos veces da cuerpos con la misma etiqueta (0.15.1: cada
  `INCLUDE` tiene su documento en `edit.reparse`). Pasar de `INCLUDE*` a `INCLUDE` deja los cuerpos sin etiqueta
  para penhmain: las líneas del `.in` que los nombraban por etiqueta pasan a su número
  (`follow.labels_still_resolve`).
* `identity.geometry_change`: si dos versiones son la misma geometría con otra numeración (Relabel, mover bloques).
* `MainWindow.lost_in_lines`: las líneas del `.in` que quitaría un cambio, sin hacerlo (para preguntar);
  `follow_geometry(..., dropped=)` las devuelve. `MainWindow._settle` dice si se puede seguir («Seguir editando»).

## Verificación

* `tests/test_blocks.py`: bloques y líneas, pestañas y valores tal como están escritos, Modify sin cambios en todo el
  corpus (byte a byte), cambios, etiquetas con sus referencias, tablas, errores de cada campo, Add/Duplicate,
  Delete, mover, Relabel, `INCLUDE`, nota del clon; con PENGEOM real (tablas y `LOCATE` idénticos): una geometría
  construida bloque a bloque desde vacía, Modify, Relabel y seis cadenas de cambios al azar. Además, a mano, 50
  cadenas de 40 cambios al azar sobre el corpus (1467 cambios hechos y 178 rechazados por PENGEOM): las 50
  geometrías finales, idénticas en PENGEOM y en ARGUS.
* `tests/test_gui_complex.py`: la pestaña y los paneles, elegir bloques, Modify, New y Add con las tablas, Delete,
  Duplicate, la vista previa, Manual edit (también una geometría que se abrió con errores, y byte a byte), Relabel,
  navegar y mover, construir una geometría desde «No geometry loaded», Include y CT, el `.in` que sigue a los
  cuerpos (también al mover y borrar bloques `INCLUDE`), deshacer con la pestaña a medias y la traza; con y sin GPU.
* Revisión de la versión por revisores aparte (núcleo, interfaz y uso), con cada hallazgo reproducido y corregido con
  su prueba: la identidad de los cuerpos de un `INCLUDE` sin «*», las etiquetas con sus cuatro columnas, deshacer con
  la pestaña a medias, los clics en el texto mientras se escribe, borrar un clon o un `INCLUDE` que el `.in` nombra,
  Manual edit byte a byte, la línea de unos vacía, los valores con formato de Fortran que no se tocan, descripciones
  de hasta 55 caracteres, la vista previa en los clics de las vistas, la traza tras deshacer y `F6`. Además, en la
  ventana: Modify sin cambios bloque a bloque en los 43 `.geo` del corpus (922 bloques, hasta 60 por fichero; los
  ficheros, idénticos byte a byte) y una prueba de mono (750 acciones al azar en la pestaña, sin un error).
* Revisión de 0.15.0 (0.15.1) por cinco revisores aparte —el núcleo (bloques, identidad, escritor), la interfaz, los
  campos y el texto, la paridad con PenGeomJar 1.21 y el uso con los `.geo` de Rafael y geometrías grandes (`mlc`, de
  16 224 líneas; `adult`)—, con cada hallazgo reproducido y corregido con su prueba: `tests/test_complex_review.py`
  (37, con PENGEOM real donde hace falta) y `tests/test_gui_complex_review.py` (21); 45 de las 58 fallan con 0.15.0.
  Lo corregido:
  * en el núcleo: los cuerpos de un fichero incluido dos veces o que pasa de `INCLUDE*` a `INCLUDE` (el `.in` seguía
    al cuerpo equivocado o se quedaba con etiquetas que penhmain ya no encuentra); Relabel con un `INCLUDE*` que
    nombra etiquetas del principal; mover un bloque que cambiaba la geometría sin decirlo (Relabel también lo
    comprueba ahora); índices PARINP negativos, valores sin punto decimal («250» = 2.5E-13), «-» o «10-2» tomados por
    números y nombres de fichero reescritos al hacer Modify sin tocarlos; lo de detrás de la columna 70 o de un
    parámetro, perdido al reescribir; una segunda caja CT; Add de un bloque nuevo que cambiaba en silencio una
    etiqueta que ya existe; dos cuerpos iguales en el mismo sitio sin aviso; los comentarios de un bloque, sin
    resaltar; el error de dos madres, con los números internos de PENGEOM en vez de los bloques;
  * en la ventana: «Seguir editando» no paraba deshacer, rehacer, guardar, «Nuevo .in» ni «Nueva geometría»; con
    Manual edit abierto se podía editar la geometría desde los menús, `Supr` o el botón derecho (al terminar se
    deshacía); lo escrito sin aplicar se perdía al elegir otro bloque, navegar, New o mover; Modify quitaba líneas del
    `.in` sin preguntar; sin `.in`, cambiar el número de una línea con índice PARINP no cambiaba la geometría y un
    índice nuevo no tenía valor; la traza de una superficie seguía al elegir un cuerpo; el File Viewer leía el disco
    (ahora, la copia fija de PenGeomJar); los errores de un fichero incluido apuntaban a una línea del principal; un
    material que el `.in` no declara no se decía; `Alt+↓` en una lista movía el bloque; elegir de la lista de
    etiquetas no contaba como escrito; un segundo Delete en las tablas se llevaba la fila que Qt elige sola y el doble
    clic cambiaba el lado en una pestaña bloqueada; «…» de Include activo en una pestaña bloqueada; la columna del
    centro, sin sitio para sus pestañas en ventanas estrechas;
  * en geometrías grandes: Manual edit tardaba segundos en comparar el texto (2,4 s en la `mlc`; ahora 0,04 s), la
    caja de cada módulo se calculaba al pasar por él y la vista previa interrumpía al escribir.

## Consecuencias

* Quien prefiera el formato de PenGeomJar puede construir cualquier geometría bloque a bloque sin salir de ARGUS,
  viendo a la vez el texto, sus pestañas y la geometría.
* Lo que PENGEOM no aceptaría no se puede hacer con las pestañas (sí escribiéndolo a mano); hay que construir en el
  orden de PENGEOM (lo que se nombra, antes), como en PenGeomJar.
* Queda de PenGeomJar, por decidir: copiar, cortar y pegar bloques (su Ctrl+clic), y editar con las pestañas una
  geometría que PENGEOM no acepta (ahora, con Manual edit). En la `mlc`, Modify, deshacer y la vista previa tardan
  unos segundos (leer, compilar y encuadrar la geometría entera en cada cambio).
* La otra pestaña de geometrías complejas que quiere Rafael queda para más adelante; la estructura (pestañas
  centrales, `ui/complex_geo/`) está pensada para añadirla al lado.
