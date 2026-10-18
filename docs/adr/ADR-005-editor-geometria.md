# ADR-005 · Editor de geometría (F6)

**Estado:** aceptada (versión 0.4; módulos y pegar en la 0.5)

## Contexto

El usuario quiere construir y modificar geometrías PENGEOM desde ARGUS sin
romper nada de lo que ya funciona en PENELOPE. PENGEOM no comprueba que los
cuerpos no se solapen: una geometría que GEOMIN acepta puede simularse mal.
Los `.geo` tienen comentarios y formatos propios que no se deben perder.

## Decisión

1. **Operaciones con significado, no texto suelto** (`pengeom/edit.py`):
   material y nombre de un cuerpo, parámetros de una superficie en forma
   reducida (escalas, ángulos, desplazamientos), giro y desplazamiento de un
   módulo, añadir un cuerpo sencillo (esfera, cilindro, caja o lámina) y
   eliminar un cuerpo.
2. **Reglas de GEOMIN al añadir** un cuerpo N dentro de un contenedor:
   * **cuerpo C:** N se define justo antes de C y C lo nombra como cuerpo
     limitante (`BODY (N)`), así deja de ocupar ese volumen; si C es hija de un
     módulo M, N también se declara hija de M (si no, `LOCATE` no lo
     encontraría: GEOMIN asigna la madre pero no lo añade a las hijas);
   * **módulo M:** N se declara hija de M (en la parte que no ocupan sus
     hijas);
   * **envolvente automática:** N es un cuerpo de primer nivel.
   * La **lámina** es la parte de C entre dos planos: copia las superficies y
     los cuerpos limitantes de C y añade un par de planos.
   * Si hay módulos con giro o desplazamiento por encima, las superficies
     nuevas se escriben en su sistema local (x_l = Rᵀ(x − d), ángulos de
     Euler de PENGEOM) para que queden donde se pidieron. No se usa
     `SURFACE*`: un clon del módulo compartiría la superficie en vez de
     moverla.
   * Etiquetas nuevas: la primera numérica libre (también frente a las de los
     `INCLUDE*`).
3. **Sin solapes.** Antes de añadir se comprueba con `LOCATE` (20 000 puntos
   del volumen, una parte sobre su superficie) que todo el cuerpo nuevo está
   en el contenedor; si no, se dice qué fracción cae en qué cuerpos y no se
   permite añadirlo.
4. **Eliminar** un cuerpo devuelve su volumen a quien lo contenía; los cuerpos
   que lo tenían como limitante heredan sus limitantes (si no, se solaparían
   con los cuerpos que había dentro) y se borran las superficies que solo usaba
   él. Los módulos no se eliminan (hay que vaciarlos antes).
5. **Siempre válido para PENGEOM.** Cada cambio se compila con el compilador
   verificado de ARGUS; si GEOMIN no lo aceptaría, se deshace y se explica por
   qué. Solo se edita el fichero principal: los elementos de `INCLUDE`, los
   clones, la caja CT y la envolvente se muestran como no editables (desde la
   versión 0.7 los clones sí se colocan, se renombran y se eliminan: véase
   ADR-007). Los valores que vienen de `PARINP` se cambian en el `.in`.
6. **Módulos (versión 0.5).** Se puede crear un **módulo** (esfera, cilindro o
   caja; la lámina es infinita y solo puede ser cuerpo) con las mismas reglas
   que un cuerpo pero con `MODULE (N)`: dentro de un cuerpo C, C lo nombra como
   módulo limitante y, si C es hija de un módulo M, M lo declara como hija
   (comprobado con PENGEOM real: si M no lo declara, LOCATE no lo encuentra).
   Un módulo recién creado no tiene hijas; se le añaden cuerpos dentro después.
   * **Convertir un cuerpo en módulo**: mismo volumen, material y etiqueta;
     sus cuerpos limitantes, y los limitantes de estos, pasan a ser hijas del
     módulo y dejan de declararse en el módulo madre (PENGEOM no admite dos
     madres: IRETRN 2042); quien lo nombraba con `BODY` lo nombra con
     `MODULE`. Solo si esos limitantes están enteros dentro del cuerpo (en un
     módulo las hijas tienen que estar dentro). LOCATE da los mismos
     materiales antes y después.
   * **Pegar**: la caja exacta de cada elemento (`pengeom/extent.py`) sale de
     sus superficies propias (cotas analíticas de elipsoides, cilindros y
     planos) y se afina con el trazador de rayos (rayos paralelos a cada eje,
     con rejillas cada vez más finas alrededor del punto más avanzado). Un
     módulo nuevo se puede crear pegado a una cara (±x, ±y, ±z) de otro
     elemento (su centro en ese eje se calcula para que toque la cara) y un
     módulo existente se puede mover hasta tocar la cara de otro, centrado en
     ella o sin moverse en los otros ejes. Mover un módulo es cambiar su
     desplazamiento en el sistema de su módulo madre (x_l = Rᵀ Δ). Antes se
     comprueba con LOCATE que el volumen desplazado solo cae en el propio
     módulo o en lo que lo aloja (el cuerpo que lo excluye o su madre).
   * Un módulo vacío se puede eliminar como un cuerpo.
7. **Dónde va lo nuevo.** El diálogo ofrece como contenedor el elemento
   pulsado y todos los que lo contienen hasta la envolvente («Dentro de»). Si
   el cuerpo nuevo no cabe entero, dice qué fracción cae en qué elementos y,
   si cabría en otro contenedor de esa cadena, lo propone.
8. **Texto como estado.** Tras cada cambio el `.geo` se reescribe (los
   elementos no tocados, byte a byte) y se vuelve a leer; el paso de deshacer
   guarda el texto antes y después. El deshacer es común con el del `.in`
   (`Ctrl+Z` deshace el último cambio, sea del fichero que sea). Guardar deja
   la versión anterior en `.geo.bak`.

## Verificación

* Pruebas de cada operación (LOCATE en puntos de dentro y de fuera del cuerpo
  nuevo y del contenedor), también dentro de un módulo girado y desplazado.
* Prueba *oracle*: un `.geo` editado con esfera, cilindro y lámina se pasa por
  PENGEOM real: las tablas (coeficientes, KSURF, KFLAG, materiales) y `LOCATE`
  en 3000 puntos coinciden con ARGUS.
* Módulos: las reglas se comprobaron antes con PENGEOM real (cuerpo limitado
  por un módulo, módulo sin hijas, módulo dentro de un cuerpo hija de otro
  módulo con y sin declararlo, conversión con y sin los limitantes de los
  limitantes, dos madres). Prueba *oracle* con un módulo pegado, un cuerpo
  dentro de él, un cuerpo convertido y un módulo movido: mismas tablas y
  LOCATE en 6000 puntos. Las cajas de `extent` contienen todos los puntos que
  LOCATE sitúa en cada elemento (60 000 puntos por geometría del corpus).

## Consecuencias

* Tras añadir cuerpos con materiales nuevos hay que declararlos en el `.in`
  (`MFNAME`); ARGUS lo avisa (IRETRN 3054) y el panel «Materiales» lo permite.
* Las superficies nuevas quedan en forma reducida (`INDICES` y escalas), como
  las escribiría el usuario, con un comentario que dice qué son.
