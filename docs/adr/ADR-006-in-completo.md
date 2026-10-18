# ADR-006 · Todo el .in en formularios y las comprobaciones de PMRDR

**Estado:** aceptada (versión 0.6)

## Contexto

Hasta la versión 0.5 solo la fuente y los materiales del `.in` se editaban
desde la interfaz; el resto (parámetros de simulación de los materiales,
parámetros por cuerpo, campos, reducción de varianza, detectores, rejilla de
dosis, proceso externo y trabajo) había que escribirlo a mano. Muchos
problemas de las simulaciones no vienen de errores que detengan a penhmain,
sino de cosas que penhmain acepta y cambia sin avisar, o que interpreta de
otra forma de la que se pretendía (un espectro con la energía máxima repetida,
un `TIME` menor que 1, un `RESUME` que reanuda una simulación antigua...).

## Decisión

1. **Secciones como datos (`penhan.sections`).** Cada sección se lee de las
   líneas del documento a objetos sencillos (`BodyValue`, `ImpactDetector`,
   `DoseGrid`...) y se escribe **en la posición y en el orden en que las lee
   PMRDR0**: las líneas que no cambian se conservan byte a byte; las nuevas
   llevan el comentario canónico de `penhmain-layout.in`; los bloques de
   detectores se reescriben enteros en el orden del bloque. Los valores por
   defecto son los del módulo `PENHMAIN_input` (no siempre los del encabezado de
   penhmain.f). Las etiquetas de cuerpo tienen que medir 4 caracteres (PMRDR0
   solo reconoce esas) y, al elegir un cuerpo en la interfaz, se escribe su
   etiqueta si la tiene (no cambia al editar la geometría).
2. **Comprobaciones de PMRDR (`penhan.simcheck`, `penhan.dumpfile`).** Se
   reproducen todas las comprobaciones de las secciones con los mismos códigos
   `IRETRN` (3052 a 3139), incluidas sus rarezas (con `KP` = 1 o 3 un `ICOL`
   inválido no detiene la lectura de la línea; `IBRSPL` exige forzado en el
   cuerpo; cualquier `IXRSPL` impide los detectores de energía; `IDFLNC` se lee
   pero no se usa). Los valores que penhmain recorta se calculan
   (`effective_material`) y se muestran. Los volcados se leen con la misma
   secuencia de lecturas dirigidas por lista que penhmain; un volcado completo
   termina en `*** END ***`. Los errores que penhmain no comprueba pero que le
   harían fallar o comportarse mal (campo en un cuerpo que no existe, `GRIDZ`
   sin `GRIDR`, `FORCEN` = 0...) se marcan como error sin código.
3. **Espectros fieles a SORT2.** El análisis del espectro ordena con una copia
   exacta de `SORT2` de penelope.f (selección con reordenación de abscisas
   iguales y ordenadas mínimas de 1e-75), de modo que los intervalos, sus
   probabilidades y los de anchura cero son los mismos que usa penhmain.
4. **Interfaz.** Panel «Simulación» con secciones plegables (resumen y aviso
   en la cabecera); editor de espectros con gráfica; cuerpos de los detectores
   resaltados en 2D y 3D; rejilla de dosis arrastrable en el corte. Cada cambio
   es un paso de deshacer del `.in` y se aplica fuera de la señal del control
   que lo pide. Si cambian los `PARINP`, la geometría se recalcula.
5. **Verificación con el PMRDR real.** Además de la sonda del lector
   (PMRDR0), la sonda `argus_simprobe` compila el `penhmain.f` del usuario con
   sus bibliotecas, sin el programa principal y con `PEINIT`/`PHINIT`
   sustituidas por rutinas vacías (mismos caracteres, para no pasar de la
   columna 72), y ejecuta `PMRDR0` + `PMRDR` con `GEOMIN` real. Las pruebas
   generan variantes con valores válidos e inválidos en todas las secciones y
   exigen que ARGUS dé el mismo `IRETRN` (o ninguno) y los mismos parámetros
   efectivos de los materiales; con volcados reales se comprueba que penhmain
   reanuda (o da la simulación por terminada) cuando ARGUS lo dice.

## Consecuencias

* Todo lo que se puede escribir en un `.in` de penhmain 2025 se puede ver y
  editar en ARGUS, salvo las fuentes radiactivas por nucleido (ya en el panel
  «Fuente») y el `.mat` en sí.
* La sonda `argus_simprobe` necesita las bibliotecas de PENELOPE junto a
  `penhmain.f`; si no están, esas pruebas se omiten.
* Los mensajes nuevos (avisos e informaciones) pueden ser muchos en ficheros
  antiguos; los de nivel «información» no impiden nada.
