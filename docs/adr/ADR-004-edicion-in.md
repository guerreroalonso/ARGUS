# ADR-004 · Edición del fichero de entrada (.in) de penhmain

**Estado:** aceptada (versión 0.3)

## Contexto

El objetivo principal de ARGUS es ver la fuente del `.in` sobre la geometría y
cambiarla con facilidad, de forma que el cambio quede en el `.in` al guardar.
Los `.in` los escribe el usuario a mano: tienen comentarios, alineaciones y
formatos numéricos propios (`100.0E+6`, `1.E6`) que no se deben perder.

## Decisión

1. **Documento por líneas.** El `.in` se lee como una lista de líneas
   (`InLine`). Cada línea conserva su texto original; al guardar, las líneas no
   tocadas se copian byte a byte y solo las cambiadas se escriben de nuevo, con
   el comentario alineado como en `penhmain-layout.in` (el `]` final en la
   columna 69).
2. **El lector es PMRDR0.** La lectura reproduce la máquina de estados de
   `PMRDR0` de penhmain 2025 (orden fijo de palabras clave, formato libre de
   Fortran, `FWORD` para las etiquetas de cuerpo) y las comprobaciones de
   `PMRDR` que no necesitan los datos de los materiales. Tras cada edición se
   vuelve a analizar todo el documento, así que lo que muestra ARGUS es siempre
   lo que leería penhmain.
3. **Operaciones con significado.** La interfaz no escribe líneas sueltas, sino
   operaciones (`source_edit`, `materials`) que dejan el fichero coherente:
   quitar `SRNUCL` al elegir otra partícula (fuerza KPARP = 0), quitar
   `SPECTR`/`SLINES` al poner una energía única, quitar `SBODY` al quitar la
   caja, quitar `SRECTA` al elegir un cono (si están las dos, penhmain usa
   `SCONE`), escribir una etiqueta de cuerpo solo si la identifica (penhmain toma
   la última coincidencia de `BALIAS`) y siempre con un comentario detrás (sin
   él, `FWORD` entra en un bucle infinito).
4. **Deshacer por instantáneas.** Cada paso de deshacer guarda la lista de
   líneas antes y después (un `.in` tiene pocas decenas de líneas). Un
   arrastre en la vista es un solo paso; `Esc` lo cancela.
5. **Materiales.** El material N es la N-ésima línea `MFNAME`. El nombre
   descriptivo se guarda como comentario de esa línea (`[Water, liquid
   (PENELOPE 278)]`), que penhmain no lee: el `.in` sigue siendo la única
   fuente de verdad y no hacen falta ficheros auxiliares. Al guardar se escribe
   `materiales.txt` junto al `.in`.
6. **Verificación.** Las pruebas *oracle* pasan por el `PMRDR0` real (extraído
   sin cambios de `penhmain.f`) los ficheros del corpus, miles de variantes con
   errores típicos y ficheros editados al azar con las operaciones de ARGUS; en
   todos, penhmain y ARGUS leen lo mismo (valores, errores y línea de parada).

## Consecuencias

* Abrir y guardar sin cambios deja el fichero idéntico; un cambio de posición
  cambia solo la línea `SPOSIT`.
* Los números nuevos se escriben en la forma más corta que Fortran lee con el
  mismo valor (`1e8`, `-100.0`, `2.5e-5`).
* Lo que no se edita desde la interfaz se conserva tal cual y se puede editar
  a mano; ARGUS lo vuelve a comprobar al abrirlo. Desde la versión 0.6 todas
  las secciones del `.in` tienen formulario (ADR-006).
