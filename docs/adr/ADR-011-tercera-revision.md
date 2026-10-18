# ADR-011 · Tercera revisión y soluciones generales para CTTRACK y la energía de excitación

**Estado:** aceptada (versión 0.9.0)

## Contexto

Tras la revisión de la 0.8 (ADR-010) quedaban dos decisiones para el usuario:
qué hacer con el fallo de CTTRACK de penhmain y qué energía media de excitación
dar a los tejidos. El usuario pidió que las dos soluciones fueran **generales**,
para cualquier TAC y cualquier geometría, sin casos particulares (nada de
«partir el pulmón»). Pidió también otra revisión de todo el programa, sobre todo
de la lectura y la escritura de los .in y .geo, de la interfaz y de añadir y
quitar geometría.

La revisión se hizo en cinco partes independientes: lectura del .geo frente a
GEOMIN, lectura del .in frente a PMRDR0/PMRDR, escritura, añadir y quitar
geometría frente a PENGEOM y la interfaz. Se hicieron además un estudio de la
energía de excitación y un diseño de la subdivisión por densidad. Cada fallo se
reprodujo con una prueba antes de corregirlo.

## Decisión 1: tejidos partidos por densidad (CTTRACK)

Dentro del CT, penhmain (2025) resta la parte blanda de la pérdida de energía
(un 70 % del frenado de los protones) con la densidad del .mat, no con la del
vóxel (ADR-010). La única corrección completa está en penhmain. Mientras el
usuario no la haga, ARGUS lo mitiga así, para cualquier TAC:

* **Al importar, cada tejido se parte en varios materiales por densidad**
  (`ct/subdivide.py`):
  * Cada trozo tiene la misma composición que su tejido y, como densidad del
    .mat, la media de sus vóxeles.
  * Se parte por densidad y no por HU, porque la curva de Schneider no es
    monótona entre 100 y 101 HU.
  * Dentro de cada tejido, los cortes son la partición óptima de un histograma
    de 1024 intervalos (k-medias en 1D exacta, por programación dinámica).
  * Los materiales libres del .in (MAXMAT = 10 menos los que ya tiene) se
    reparten entre los tejidos donde más reducen Σ S²·J (S: poder de frenado
    relativo al agua).
  * No se parten los tejidos homogéneos, los que usan un material que ya tiene
    el .in, ni los de composición desconocida.
  * «Automático» se detiene cuando un material más apenas reduce el error.
    «Todos los que quepan» y «No partir» se eligen en el diálogo.
* **Error de alcance estimado.** Con el modelo de rayos que reproduce las
  simulaciones (72 % del frenado con la densidad del .mat), ARGUS calcula el
  error que queda en los puntos del paciente donde puede pararse una partícula
  (haces ±x, ±y). Lo guarda en la ficha y lo muestra en la barra de estado, en
  `materiales.txt` y en los mensajes.
* **Ajuste «Mi penhmain tiene corregido CTTRACK».** Está en el menú Opciones y
  en el diálogo. Con él no se parte nada y no se avisa.
* **Ficha v2** (`.ct.json`):
  * «calibracion» sigue siendo la calibración de los tejidos, con los totales;
  * «materiales» tiene un material por trozo: tejido, densidades, fichero .mat
    (`ct_pulmon_2.mat`, 20 caracteres como mucho), vóxeles, masa, densidad
    media y la I que se da a penhmat;
  * «subdivision» tiene los trozos, el error antes y después y la preparación
    extra;
  * las fichas anteriores se siguen leyendo.
* **Vista.** Los trozos de un tejido llevan el color de su primer material. En
  2D se sombrean con el intervalo del tejido entero, así que el tejido se ve
  continuo.
* **Otros cambios al importar.** Los tejidos sin vóxeles ya no llevan línea
  MFNAME. Los tejidos de la lista de PENELOPE cuya composición se conoce se
  generan con la opción 1 de penhmat, con la densidad de sus vóxeles.

Resultado en tres TAC de tórax reales y públicos (LCTSC), con el error
estimado sin corregir penhmain:

| TAC | Sin partir (mediana / p95) | Recomendado | p95 |
| --- | --- | --- | --- |
| S1-006 | 6,0 / 18,8 mm | 7 materiales (pulmón en 3) | 3,5 mm |
| S3-009 | 3,9 / 25,5 mm | 8 materiales (pulmón en 4) | 2,9 mm |
| S2-103 | 2,3 / 12,6 mm | 8 materiales (pulmón en 4) | 2,4 mm |

Con protones, cada material de más cuesta unos 33 s de CPU por elemento en la
preparación de penhmain: unos 5,5 minutos con el pulmón en 3 y unos 8 minutos
con el pulmón en 4. El análisis tarda 1–2,5 s con 10 millones de vóxeles.

## Decisión 2: energía media de excitación (regla general)

ARGUS da a penhmat la I que sale de una sola regla, válida para cualquier
composición (`ct/elements.py`):

* **Sólidos y líquidos** (todos los tejidos):
  * aditividad de Bragg con los valores atómicos de fase condensada de Berger y
    Seltzer (NBSIR 82-2550-A, tabla 5.1; es el esquema de ICRU 37 y 49): H 19,2,
    C 81, N 82, O 106, F 112 y Cl 180 eV, y 1,13 × el valor del elemento para
    los demás;
  * multiplicada por el factor que da al agua 78 eV (ICRU 90, como el water.mat
    de PENELOPE), unos 1,036.
* **Gases** (densidad por debajo de 0,02 g/cm³) y **elementos**: la que calcula
  PENELOPE (el aire da 85,7 eV, el valor de ICRU).

Con esta regla, los tejidos de ICRU salen a menos de un 0,3 % de sus valores
tabulados escalados a 78/75. En cambio:

* la I por defecto de PENELOPE (valores atómicos de gas) sale un 2–8 % baja en
  los tejidos (un 11 % en el agua): alcances hasta un 1,6 % cortos;
* la I de ICRU de un tejido parecido (lo que proponía la 0.8.1) es específica
  de cada tejido y deja los alcances un 0,3–0,9 % cortos.

Schneider da, por ejemplo: pulmón 77,2, adiposo 65,3, tejido blando 76,7 y
hueso 98,4 eV.

## Otras decisiones de la revisión

* **Retornos de carro sueltos (CR).** gfortran termina un registro en un CR que
  no va seguido de LF si cae dentro de las columnas que lee: 72 para GEOMIN y el
  TITLE del .in, 127 para el resto del .in. ARGUS lee los mismos registros:
  * los mensajes cuentan registros;
  * la vista del texto enseña uno por línea (un CR que Fortran ignora se ve
    como «␍»);
  * se avisa de que hay CR sueltos;
  * un fichero sin cambios se escribe byte a byte igual.
* **INCLUDE y .ct.** penhmain los abre en su carpeta de trabajo (la del .in), no
  junto al .geo. Con un .in abierto, ARGUS los lee de allí y avisa si GEOMFN
  tiene carpeta. La carpeta de simulación y «Guardar como» siguen la misma
  regla.
* **Guardar la pareja.** Cuando el .in ha seguido a la geometría (renumeración),
  los dos se guardan o se descartan juntos, con una sola pregunta:
  * «Guardar» escribe primero los dos en temporales y los sustituye;
  * si el segundo falla, el primero vuelve a su contenido;
  * antes de escribir se comprueba que otro programa no haya cambiado el
    fichero (Recargar / Sobrescribir / Cancelar).
* **El .in sigue a la geometría aunque tenga errores de lectura.** Se lee GEOMFN
  línea a línea. Si hay líneas de cuerpos que no se entienden, se pregunta antes
  de la edición.
* **Referencias a cuerpos como PMRDR.**
  * Un 0 o una etiqueta en blanco es el último cuerpo sin etiqueta (copias de un
    clon, la envolvente de PENGEOM, cuerpos de un INCLUDE); IDBODY no lo busca
    (3082).
  * ARGUS nunca escribe una etiqueta en blanco: escribe el número.
* **Mover un módulo que comparte superficies** con cuerpos de fuera (lo crean las
  láminas y «Envolver»):
  * primero recibe copias propias de esas superficies (GEOMIN une las
    idénticas, así que la geometría no cambia hasta moverlo);
  * después se comprueba con LOCATE que nada de fuera ha cambiado;
  * vale para el arrastre, el panel, «Girar» y «Pegar».
* **Añadir y copiar.** Se rechaza:
  * un cuerpo que rodea a otros;
  * lo que quedaría fuera de la envolvente de PENGEOM (r = 1e7 cm).
  
  Además:
  * se comprueban los clones que se mueven con su original;
  * se pueden duplicar los módulos raíz de una geometría sin envolvente;
  * las láminas finas se ven al quitar superficies;
  * los limitantes sin caja analítica se muestrean.
* **Sondas y valores no finitos.**
  * Las sondas se compilan con `-Os`, como indica penhmain.f. Con `-O2`, una
    superficie con todos los coeficientes a 0 se lee de otra forma; ARGUS avisa
    de ella.
  * Inf, NaN y los desbordamientos en el .geo se rechazan sin IRETRN: GEOMIN los
    lee sin pararse.
* **Nombres de fichero (A20) y SRNUCL (45 caracteres)** se leen como gfortran:
  * solo se quitan los espacios finales; un tabulador al final es un error;
  * nunca se truncan al escribir;
  * «Guardar la geometría como…» con más de 20 caracteres no cambia GEOMFN.
* **Errores inesperados.** Se anotan en `argus_errores.log` y se avisan una vez,
  en lugar de perderse en una consola que en Windows no se ve.

## Revisión final (después de unir las partes)

Dos revisiones más, del núcleo y de la interfaz, sobre el conjunto:

* **Por qué no se parte un tejido.** La ficha guarda, para cada tejido, por qué tiene los trozos que tiene
  («limites»):
  * no hace falta partirlo más: homogéneo, pocos valores distintos o trozos demasiado pequeños;
  * «Automático» no gana casi nada con otro trozo;
  * no se puede: usa un material que ya tiene el .in (p. ej. de un TAC importado antes), comparte su .mat con
    otro tejido, no tiene composición conocida o no quedan materiales libres en el .in.

  El texto de CTTRACK (mensajes y `materiales.txt`) es informativo si está partido todo lo que hace falta, y un
  aviso, con el motivo y qué hacer, si no. Un tejido bloqueado pero homogéneo no cuenta como problema.
* **Importar.** Si ni sin partir caben los materiales en el .in, no se escribe el .ct. La I de los trozos de un
  tejido es la misma: gas o condensado lo decide la densidad nominal del tejido, no la de sus vóxeles.
* **Lectura como gfortran.**
  * Un factor de repetición que pasa de un entero a un real (o al revés) es un error, en el .in y en el .ct
    («Read type INTEGER where REAL was expected», comprobado con gfortran y con la sonda de penhmain).
  * El aviso de un valor nulo que conserva el valor anterior se da en cada análisis, no solo en el primero.
  * Una ficha `.ct.json` que no es la que escribe ARGUS se ignora.
* **Guardar.**
  * Un guardado que falla deja también las copias `.bak` como estaban.
  * Guardar el .in, o todo, cuando nombraría otros cuerpos de la geometría de su GEOMFN (y guardar los dos no
    lo arregla: p. ej. tras deshacer el cambio de GEOMFN de «Guardar la geometría como…») pregunta antes
    («Guardar de todos modos»).
  * Al guardar una geometría renumerada, se avisa de los otros .in de su carpeta que la usan y nombrarían otros
    cuerpos, con las líneas afectadas. Incluye el .in original tras «Guardar .in como…» una copia.
  * «Recargar» vuelve a abrir lo que ha cambiado en el disco. Un .in y la geometría que usa, juntos; un .in que
    no la usa, o uno nuevo sin guardar, se conserva (si el nuevo sigue cambios que se descartan, se pregunta).
  * Guardar el .in en otra carpeta cambia la carpeta de trabajo de penhmain: los INCLUDE y el .ct se copian allí
    si faltan (como con «Guardar la geometría como…») y ARGUS los lee de allí.
* **Interfaz.**
  * El .ct importado va a la carpeta donde PENGEOM lo buscará: la del .in abierto.
  * Los trozos de un tejido partido llevan el color de su primer material también como color por defecto (tras
    «Colores por defecto», al volver a abrir el caso y en la tabla de materiales).
  * F2–F4 cambian la vista también desde un campo que no las usa (la profundidad del corte); F2 sigue editando
    una celda y F4 abriendo un desplegable.
  * Un error inesperado se identifica por su tipo y el sitio del código (no por el mensaje), se avisa fuera del
    evento que ha fallado y como mucho cinco veces por sesión. El registro no pasa de 1 MB.
  * Se puede arrastrar a la ventana una geometría con otra extensión (se reconoce por su contenido).

*Nota (0.16.3, ADR-021 §2):* la ficha del TAC dice qué materiales eran del `.in`
(`"material_del_in": true`): no se toman por tejidos al escribir `materiales.txt`
ni al generar los `.mat`.

## Consecuencias

* **Módulos nuevos:** `ct/subdivide.py`, `ct/elements.py` y `ui/errors.py`.
* **Pruebas nuevas:** `test_review3_read.py`, `test_review3_checks.py`,
  `test_review3_editor.py`, `test_review3_files.py`, `test_review3_ui.py`,
  `test_review3_integration.py`, `test_ct_elements.py`, `test_ct_subdivide.py`
  y `test_ct_split.py`.
* **`tests/editor_fuzz.py`** también borra cuerpos, módulos, clones, el CT y
  superficies, y comprueba con LOCATE que lo que no se toca no cambia.
* **Queda:**
  * `edit.reparse` reutiliza los INCLUDE ya leídos: si uno cambia en el disco,
    se ve con «Recargar» (F5);
  * el modelo de rayos del error de alcance es una estimación; la corrección
    completa sigue siendo la de penhmain.
