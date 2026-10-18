# ADR-007 · Editor completo de geometrías y el .in que sigue a la geometría

**Estado:** aceptada (versión 0.7)

## Contexto

Hasta la versión 0.6 el editor añadía cuerpos y módulos, los pegaba, los
convertía y los eliminaba, y editaba las superficies en forma reducida. Faltaba
empezar una geometría desde cero, duplicar (copias o clones de PENGEOM), girar
módulos con el ratón, las superficies en forma implícita, los lados y las
etiquetas, y agrupar cuerpos que ya existen en un módulo («envolver»).

Además, al revisar el editor se vio un problema de coherencia: PENGEOM numera
los cuerpos por el orden en que se definen, un cuerpo nuevo va antes que su
contenedor y todos los que van detrás cambian de número. Los `.in` pueden
nombrar los cuerpos por número (`DSMAX 1`, `EDBODY 2`, `IFORCE 1`...), así que
editar la geometría podía dejar el `.in` apuntando a otros cuerpos sin aviso.

## Decisión

1. **Identidad de los cuerpos (`pengeom.identity`).** Cada cuerpo se identifica
   por su etiqueta completa (con el sufijo del fichero), la envolvente, o, en
   las copias de un `CLONE` (que no tienen etiqueta), por la etiqueta del clon y
   la identidad del cuerpo copiado. Tras cada edición se calcula qué número
   tiene ahora cada cuerpo de antes; `penhan.follow` cambia los números del
   `.in` (tocando solo esa palabra de la línea), las etiquetas cambiadas y
   quita las líneas de los cuerpos eliminados. El cambio del `.in` va en el
   mismo **grupo de deshacer** que el del `.geo` (la línea de tiempo común
   guarda un número de grupo y deshace o rehace el grupo entero).
2. **Colocación de módulos y clones (`pengeom.placement`).** Un movimiento
   global G (desplazar, girar alrededor de un eje que pasa por el centro del
   elemento) se escribe como T' = F⁻¹·G·F·T, con F el sistema de la madre.
   Vale igual para un `CLONE` (su transformación se aplica a las superficies
   del original tal como están al clonarlo). Los ángulos de Euler se obtienen
   con sin θ de la tercera columna (con `acos` de m₃₃, cerca de la identidad el
   ruido de redondeo daba ω y φ sin sentido). Arrastrar en el corte es un
   cambio «en vivo» (se compila una vez la geometría del principio y en cada
   paso solo cambia la transformación de un elemento; los movimientos del
   ratón se agrupan) que al soltar es un paso de deshacer, o vuelve atrás si
   se solaparía.
3. **Duplicar (`pengeom.duplicate`).** Copias independientes: se copian el
   elemento y lo que tiene dentro con etiquetas nuevas; el de arriba se coloca
   componiendo su transformación (módulo) o moviendo sus superficies (cuerpo);
   lo de dentro de un módulo se copia tal cual. Clones: T_clon = B_clon⁻¹·G·B_orig,
   donde B_orig son los módulos del original que se leen después del clon. Un
   clon se puede convertir en copia independiente en el mismo sitio (con la
   misma etiqueta; el `.in` sigue a sus cuerpos).
4. **Envolver (`pengeom.wrap`).** El módulo nuevo lista como hijas a los
   elementos envueltos y a sus limitantes (como al convertir en módulo), se
   define antes del primer elemento que los nombraba (dentro de un módulo gana
   la hija con el número más bajo) y todos esos pasan a nombrar al módulo. Su
   hueco es del material del contenedor.
5. **Superficies, lados y etiquetas (`pengeom.surfaces`).** Etiquetas de 4
   caracteres sin apóstrofos, paréntesis ni comas, alineadas a la derecha.
   Pasar de forma reducida a implícita escribe los coeficientes de la forma
   reducida y la misma transformación tras la línea de unos (GEOMIN obtiene los
   mismos coeficientes). Clasificación de las cuádricas por autovalores.
6. **Solapes (`pengeom.overlap`).** Dentro de un módulo, LOCATE se queda con la
   primera hija cuya envoltura contiene el punto y STEP solo vigila las
   superficies del cuerpo en el que está la partícula: dos hijas solo pueden
   compartir volumen si una es limitante (directa o indirecta) de la otra. Los
   cambios de forma (lados, superficies, coeficientes, colocación en el panel)
   se rechazan si crean solapes o salientes que no había (se comparan con
   puntos de muestra antes y después). «Buscar solapes» lo mira en toda la
   geometría; en el corpus encuentra problemas reales (`well` de PenGeomJar,
   los maniquíes de ORNL), no en las geometrías del usuario.

## Consecuencias

* Verificación: más de 2200 ediciones al azar encadenadas (añadir, convertir,
  mover, girar, copiar, clonar, convertir clones, envolver, renombrar, pasar a
  implícita) sobre cinco geometrías: PENGEOM real lee cada resultado con las
  mismas tablas bit a bit y el mismo LOCATE que ARGUS. El PMRDR real lee el
  `.in` renumerado con la geometría nueva y aplica `DSMAX` y los detectores a
  los mismos cuerpos que antes.
* Las copias de un clon no tienen etiqueta: si el `.in` las nombra, lo hace por
  número (ARGUS lo mantiene al día mientras se edita en ARGUS).
* Los módulos con superficies `SURFACE*` no se mueven, ni giran, ni se duplican
  (PENGEOM no las mueve con el módulo).
