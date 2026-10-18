# ADR-009 · Importar TACs (DICOM) y preparar la carpeta de simulación

**Estado:** aceptada (versión 0.8.0)

## Contexto

PENGEOM 2024 admite una estructura de vóxeles (`CTBOX`) por geometría, leída de
un fichero `.ct`, y penhmain la simula con sus propias rutinas (CTTRACK, con
Woodcock para fotones). Rafael pidió el importador de TAC y preparar la carpeta
de simulación (copiar los `.mat` y los ENDF). Lo que hacen PENGEOM y penhmain
se ha leído en `pengeom.f` y `penhmain.f` (2025) y se ha comprobado con las
sondas.

## Datos verificados

* `.ct`: título (A72), `XM YM ZM`, `NX NY NZ` y una lectura dirigida por lista
  por vóxel (`MC DC`), con IX variando más rápido. DC es la **densidad de masa**
  (g/cm³): penhmain escala con ella los recorridos (`DEN(MAT)/DC`) y el ejemplo
  de penhan pone 0,99821 en el agua. MC = 0 es vacío (se cruza sin parar; con un
  campo EM en el CT, IRETRN 3145). El manual de PENELOPE 2024 escribe los vóxeles
  con `(I2,1P,E11.4)`; ARGUS escribe exactamente eso (idéntico byte a byte al
  ejemplo) y la cabecera con 16 cifras.
* Colocación: r_lab = R(ω,θ,φ)·r_CT + D (SHPRD); el CT tiene que estar en su
  posición final: un módulo transformado que lo contenga movería sus caras pero
  no sus vóxeles. Puede ser hija de un módulo o módulo limitante de un cuerpo
  sin transformar (probado con PENGEOM real). El nombre, 12 caracteres, en la
  carpeta de la geometría.
* penhmain: NZ ≤ 512 (CTDOSE0, IRETRN 3148); los materiales del CT cuentan para
  NMATG ≤ número de MFNAME (3054); DSMAX no se aplica al CT (solo a MATER de 1 a
  9998); el CT no puede ser detector de impacto; las partículas de un PSF tienen
  que empezar fuera (3118); reserva unos 40 bytes por vóxel.
* penhan: como mucho **MNIS = 30 isótopos ENDF entre todos los materiales**
  (READNFH, 40029). Los tejidos ICRP de la lista de PENELOPE (Na, Mg, S, Cl, K,
  Fe, Zn...) pasan de 30 juntos.

## Decisiones

1. **Lectura DICOM con pydicom** (dependencia nueva). Series de un corte por
   fichero y TAC «mejorados» (marcos con grupos funcionales), con
   RescaleSlope/Intercept. Se ordenan los cortes a lo largo de la normal
   (fila × columna); si los ejes de la matriz son los del paciente salvo orden y
   signo (axial, coronal, sagital, pies primero, prono), se reordena la matriz y
   el CTBOX no lleva giro; si son oblicuos, el CTBOX lleva el giro del TAC. Se
   rechazan series con cortes desplazados (gantry inclinado), repetidos o que no
   son TAC. No se guarda ningún dato del paciente.
2. **Rejilla**: recorte (por defecto, lo que pasa de −400 HU con 5 mm de margen)
   y tamaño de vóxel; cada vóxel nuevo es la media de los HU que cubre pesada por
   volumen (exacta si la rejilla coincide con la del TAC); fuera del TAC, aire.
   Se propone el tamaño del TAC si caben menos de 10 millones de vóxeles y
   512 cortes. Se promedian HU y no densidades: en cada tramo recto de la curva
   es lo mismo.
3. **Calibración por defecto** (la eligió Rafael): la curva de Schneider,
   Bortfeld y Schlegel (2000) tal como la distribuye GATE y cinco tejidos con las
   composiciones de Schneider sin elementos traza (solo H, C, N, O, P y Ca; N, O y
   Ar en el aire): **17 isótopos ENDF**. Límites: aire < −950 ≤ pulmón < −120 ≤
   adiposo < −22 ≤ tejido blando < 120 ≤ hueso. El límite adiposo/blando se puso
   en −22 HU (y no en 19, como se propuso al principio) porque los intervalos de
   Schneider entre −22 y 18 ya tienen casi la composición del tejido blando: con
   19, el agua (0 HU) se tomaba por grasa. Cada tejido tiene un HU representativo
   (−1000, −750, −100, 40, 800) del que salen su composición (el intervalo de
   Schneider que lo contiene) y su densidad nominal para penhmat. Otras
   calibraciones: tejidos ICRP de la lista de PENELOPE (penhmat opción 2; 39
   isótopos) y agua con aire; todo editable y una curva propia en CSV.
4. **Materiales**: los tejidos van al `.in` como materiales nuevos (o reutilizan
   uno con el mismo `.mat`); `materiales.txt` da las respuestas a penhmat
   (opción 1, elementos y fracciones, densidad) a partir de la **ficha**
   `<nombre>.ct.json` que se escribe junto al `.ct`.
   *Nota (0.16.3, ADR-021 §2):* un material que ya estaba en el `.in` y que el TAC
   reutiliza (el agua, el aire de la sala) va marcado en la ficha con
   `"material_del_in": true` y sigue siendo el del `.in` (no un tejido con la
   densidad y la composición de la calibración); dos tejidos no pueden compartir
   un `.mat` nuevo (se rechaza la importación).
5. **Vista**: en 2D, cada vóxel con el color de su material, más claro cuanto
   más denso dentro de su material; en 3D, recorrido de vóxeles (DDA, como
   CTSTEP) en la GPU (textura 3D R8UI) y en la CPU, una capa por tramo de vóxeles
   del mismo material, con la visibilidad por material del panel Materiales; el
   aire del CT empieza oculto. Los `.ct` leídos se guardan en caché (cada edición
   recompila la geometría).
6. **Carpeta de simulación**: por defecto una carpeta nueva junto al `.in`
   (`caso_sim`); se copian el `.in`, la geometría, sus INCLUDE, el `.ct` y su
   ficha, los `.mat` (de la carpeta del `.in` o de otra que se indique), los ENDF
   que listan (si se simulan protones o alfas; primero junto al `.mat`, luego en
   `database/ENDF_data`), el `.nuc` y `pdrelax.p11`/`pdatconf.p14` de una fuente
   radiactiva, los PSF, la tabla de EPROF, el volcado de RESUME y el ejecutable de
   penhmain (su ruta se recuerda). **Nunca se sobrescribe ni se borra nada**: si
   en el destino hay otro fichero con el mismo nombre, se deja y se avisa.
7. «Guardar la geometría como…» en otra carpeta copia allí el `.ct`, su ficha y
   los INCLUDE que falten.

## Consecuencias

* Pruebas: `tests/test_ct_import.py` (series en todas las orientaciones,
  calibración, rejilla, importación y, con la sonda, `CTLOCATE` frente a ARGUS:
  mismo vóxel, material y densidad, también con el CT girado y en puntos sobre
  los planos entre vóxeles), `tests/test_ct_view.py` (2D, CPU, GPU frente a CPU,
  vista 3D) y `tests/test_simfolder.py`, `tests/test_gui_ct.py`.
* La sonda PENGEOM tiene una orden más, `CTLOC` (LOCATE + SHPRD + CTLOCATE).
* Al probar la textura 3D apareció un fallo de segmentación al azar que ya existía: si una ventana con la
  vista 3D se borraba sin cerrarla (sin `release_gl`), sus `QOpenGLTexture` sobrevivían al contexto y su
  destructor usaba después un puntero colgante. Ahora el `GpuTracer` escucha `aboutToBeDestroyed` y libera
  sus texturas con el contexto activo, como hace Qt con sus VAO (`test_gpu_textures_are_freed_when_the_context_goes_first`).
* Limitaciones: no se corrige la inclinación del gantry; las series comprimidas
  (JPEG) necesitan exportarse sin compresión (o instalar pylibjpeg); la mesa del
  TAC entra en el recorte automático (se puede recortar a mano).
