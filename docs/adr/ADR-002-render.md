# ADR-002: estrategia de visualización

Fecha: 2026-09-23. Estado: aceptada (2D en la versión 0.2; 3D en la versión 0.4).

## Decisión

### Cortes 2D

Cada píxel del corte se localiza con el `LOCATE` vectorizado de ARGUS, que
reproduce exactamente el de PENGEOM (mismo recorrido del árbol, mismas
superficies difusas). La imagen se colorea por material o por cuerpo y se
dibujan las fronteras entre cuerpos. Es exacto por construcción y no necesita
GPU. El render es progresivo: resolución reducida mientras se arrastra o se
hace zoom y resolución completa al soltar, en un hilo de trabajo.

### Vista 3D: trazado de rayos sobre las cuádricas

No se triangula nada: cada píxel es un rayo que recorre la geometría como una
partícula en `STEP` de PENGEOM (`render/raytrace.py` en la CPU y
`render/shaders.py` en la GPU, con la misma lógica):

1. `LOCATE` en el origen del rayo (bajando por los módulos);
2. cortes con las superficies `KSURF` (`KFLAG <= 4`) del cuerpo actual, con
   las reglas de `STEPSI`: si el punto está sobre una superficie solo cuenta el
   corte en el sentido del movimiento;
3. tras cruzar una superficie se impone su lado en `LOCATE` (STEP no la
   vuelve a evaluar, cambia `KSP`) y se recuerda su otro corte (no se
   recalcula desde el punto de cruce, donde F ≈ 0).

**Verificación.** El trazador de la CPU se compara con `STEP` real (sonda
`RAYS`, paso infinito, sin detectores): 12 900 rayos en las 43 geometrías del
corpus, 99,84 % idénticos (puntos de parada, cuerpos y materiales); los 20
restantes pasan a menos de 10⁻¹² de una superficie (esferas tangentes de
`spheres`, láminas de `mlc`) o muy lejos del origen (`tball`), donde el propio
PENGEOM no es coherente entre `STEP` y `LOCATE`. Los tramos del trazador
coinciden con `LOCATE` en el 99,98 %. El *shader* se compara píxel a píxel con
el trazador de la CPU (imagen de índices de cuerpo, perspectiva y ortográfica,
con y sin corte): más del 99,9 % de los píxeles coinciden en cada geometría
(99,995 % en total) salvo `tshell` vista entera (véase «Precisión»); las
diferencias están en bordes y tangencias.

**GPU (OpenGL 4.1 core).** Coeficientes, árbol de cuerpos, listas de
superficies y colores van en texturas (`RGBA32F`, `RGBA32I`, `R32I`,
`RGBA8`). Se pide 4.1 core porque es lo máximo en macOS y basta con lo que dan
Intel UHD (4.6), NVIDIA y AMD. Si no hay OpenGL 4.1 (escritorio remoto,
máquina virtual) o el *shader* no compila, la vista usa el mismo trazador en la
CPU con Numba (más lento, misma imagen). `argus --cpu3d` o el menú Ver lo
fuerzan.

**Precisión en `float32`.** La GPU trabaja en simple precisión:

* **origen flotante:** todo se expresa respecto al punto que mira la cámara;
* **cada superficie centrada en su propio punto** (centro de la cuádrica o pie
  de la perpendicular del plano) y normalizada por su coeficiente mayor. Sin
  esto, una lámina fina definida como par de planos (`z² = a²`) lejos del
  centro de la vista es ruido (cancelación de términos de 10⁷);
* **franja difusa** de anchura 10⁻⁵ × distancia de la cámara más el error de
  redondeo de `o + t·d` a esa distancia;
* **pasos de aproximación:** si un corte está tan lejos que su error en
  `float32` supera la franja, el rayo avanza casi hasta él sin cruzarlo y lo
  recalcula desde allí;
* en proyección ortográfica, `t = 0` está en el plano del objetivo (los rayos
  salen de delante de lo visible con `t < 0`).

Con esto `tball` (bola de 1 cm a 5·10⁶ cm) se ve igual que en `float64`. El
límite que queda: capas mucho más finas que 10⁻⁷ veces la distancia al centro
de la vista (`tshell`: 0,1 cm a 10⁶ cm vista entera) solo se resuelven al
acercarse a ellas.

**Rendimiento en Intel UHD.** Mientras se gira la cámara se dibuja una vista
previa de 1 píxel de cada 2×2 a 16×16 (se adapta para no pasar de ~45 ms);
al parar se completa la imagen por bloques de 32 a 512 píxeles, del centro
hacia fuera, midiendo cada bloque para que ninguna orden a la GPU dure más de
unas décimas de segundo (en Windows, una orden de más de ~2 s reinicia el
controlador, TDR).

**Presentación.** Luz desde la cámara más una luz principal arriba a la
izquierda; cada material puede ser opaco, translúcido (una capa por cuerpo
atravesado) u oculto, y cada cuerpo se puede ocultar; fronteras entre cuerpos
dibujadas a partir de una huella del cuerpo visible; planos de corte (x, y, z,
una esquina o el plano del corte 2D) con la cara del corte sombreada. La
fuente del `.in`, la rejilla de dosis y el plano del corte 2D se dibujan
encima con QPainter; lo que queda detrás de la geometría se dibuja punteado
(oclusión calculada con el trazador de la CPU). La selección y el menú
contextual usan también el trazador de la CPU (exacto, en `float64`).

## Alternativas descartadas

* Mallar las cuádricas (VTK/marching cubes): bordes inexactos, mucha memoria
  en geometrías grandes y resultados distintos de lo que simula PENGEOM.
* Render 3D solo en CPU: demasiado lento para girar con fluidez (se mantiene
  como alternativa sin GPU).
* `float64` en el *shader*: Intel UHD y macOS no tienen `double` útil en
  GLSL; el origen flotante y el centrado por superficie bastan.
