# ADR-010 · Revisión de fallos de la versión 0.8

**Estado:** aceptada (versión 0.8.1)

## Contexto

Tras el importador de TAC y la carpeta de simulación (ADR-009) se revisó todo lo
hecho hasta entonces: revisión por partes (importador, carpeta de simulación y
diálogos, vista de vóxeles, el TAC con el editor, paneles anteriores), series
DICOM públicas y anonimizadas de cinco fabricantes (Philips, Siemens, Toshiba,
GE, Hitachi), una simulación completa con penhmat y penhmain reales, análisis
estático, la prueba «de mono» con los 126 `.in` del usuario y un caso con TAC, y
los fuzzers del editor y del lector del `.in`. Cada sospecha se reprodujo con
una prueba antes de corregirla.

## Hallazgo sobre penhmain (no es de ARGUS)

En `CTTRACK` (penhmain.f 2025, líneas 11082–11084), tras `CTSTEP` la energía se
recalcula como `E = EORIG − SSOFT·DSEF`, con la pérdida continua de la densidad
**nominal** del `.mat`, mientras que la dosis se deposita con la densidad del
vóxel: en vóxeles con otra densidad, la profundidad del pico de Bragg sale
desplazada y la energía no se conserva. La rama con campo (`LFIELD`) ya usa
`DEE = EORIG − E`; esa es la corrección candidata. **No se toca penhmain**: es
decisión del usuario (y conviene comunicarlo a los autores de penhan). Mientras
tanto, ARGUS lo mitiga: la densidad que `materiales.txt` da a penhmat para cada
tejido es la **media de sus vóxeles** (y no la nominal), y se guarda en la ficha
(`densidad_media`).

La mitigación es **parcial**. Con el modelo que reproduce las dos simulaciones
(el 72 % del frenado, la parte blanda, con la densidad del `.mat`; 8,09 y 7,40 cm
frente a 8,10 y 7,43 medidos) se estimó el error de alcance en tres TAC de tórax
reales y públicos (LCTSC, de tres hospitales), para todos los puntos del paciente
en que un protón podría pararse con haces laterales y anteroposteriores:

| Densidad del `.mat` | Mediana | p95 | Puntos con más de 5 mm |
| --- | --- | --- | --- |
| nominal (0.8.0) | 4,0 mm | 22 mm | 43 % |
| media de los vóxeles (0.8.1) | 3,6 mm | 19 mm | 40 % |
| media, con el pulmón en 3 tejidos por densidad | 0,8 mm | 3,9 mm | 3 % |
| penhmain corregido | ≈ 0 | ≈ 0 | 0 % |

La media arregla casi todo el hueso (p95 de su parte: de 4–8 mm a menos de
3 mm) y los tejidos blandos, pero no el pulmón, cuya densidad va de 0,06 a
0,9 g/cm³ dentro del mismo tejido. Partir el pulmón en tres tejidos (misma
composición; cortes en 0,2 y 0,5 g/cm³, unos −810 y −520 HU) lo reduce mucho, a
costa de dos materiales más (MAXMAT = 10) y unos 5 minutos más de preparación en
cada simulación. La corrección completa es la de penhmain (con ella, el alcance
medido fue 8,89 cm frente a 8,90 esperado).

## Decisiones

1. **penhmat**: los pasos de `materiales.txt` siguen el diálogo real de PEMATW
   (incluida la pregunta del plasmón, Fcb/Wcb, y el «Press enter» final). Los
   pasos y las respuestas salen de una sola lista (`penhmat_input`), y una prueba
   los pasa por el penhmat real si está disponible (`ARGUS_PENHMAT`,
   `ARGUS_PENHAN_DB`).
2. **El TAC y el editor**: los módulos que contienen el CT no se mueven, giran,
   pegan ni copian (PENGEOM movería las caras de la caja pero no los vóxeles:
   IRETRN 2113), tampoco a través de `PARINP`; `MATERIAL(9999)` queda reservado
   a la caja CT; el CT se puede eliminar (para reemplazarlo); con una geometría
   sin envolvente, el «nivel superior» es fuera de todo; la caja del CT ocupa
   toda su caja aunque tenga vóxeles vacíos; las comprobaciones de «¿cabe?» ven
   cuerpos de 1 mm dentro de la caja.
3. **DICOM**: se dejan fuera los localizadores y los ficheros repetidos
   (SOPInstanceUID); se rechazan RescaleType distinto de HU y clases SOP que no
   son de TAC; se leen ficheros sin preámbulo y TAC mejorados de un marco; una
   serie de un corte toma su grosor de SliceThickness; un fichero ilegible no
   para la búsqueda; ejes «casi» del paciente (hasta 1e-5) se toman como ejes.
4. **Remuestreo y calibración**: los HU por debajo del aire (relleno fuera del
   campo, −2048, −3024) valen −1000 antes de promediar; una capa que el TAC
   cubre a medias en z es la media de lo cubierto (el paciente sigue); «Como el
   TAC» reproduce la rejilla exacta (los cuadros guardan el valor sin redondear,
   también con el redondeo de Qt); las curvas de una hoja de cálculo admiten
   comillas, separadores de miles y el signo menos tipográfico, y una lectura
   ambigua o sin sentido (p. ej. 1964 g/cm³) es un error, nunca se adivina.
5. **Nada escrito a medias**: el `.ct` y su ficha se escriben con nombres
   temporales y se renombran al final (cerrar el diálogo mientras se escriben no
   deja nada); el `.ct` se escribe por bloques (antes, unos 82 bytes de memoria
   por vóxel); el `.geo` y el `.in` se escriben enteros o nada (temporal y
   `os.replace`); un carácter que no cabe en Latin-1 se avisa (los guiones y
   comillas tipográficos se cambian por los normales); «Guardar la geometría
   como…» copia el `.ct` y los INCLUDE sin sobrescribir nunca.
6. **Carpeta de simulación**: los INCLUDE y el `.ct` se leen del `.geo` guardado
   que cita GEOMFN (el que usará penhmain), no de la geometría que se esté
   viendo; un nombre con ruta absoluta lo lee penhmain de allí (se copian los ENDF
   de un `.mat` así) y uno que sube de carpeta (`..`), o que depende de la unidad
   actual en Windows, se avisa; cada fichero se publica sin sobrescribir (enlace
   duro, o `rename` en Windows, que fallan si el destino existe), también si el
   fichero aparece mientras se copia; «Cancelar» para de verdad y borra la copia
   a medias; se copian las DLL de MinGW que haya junto a `penhmain.exe`; con
   espacios de fases penhmain no inicializa PENHAN (ni ENDF ni datos de
   hadrones: se avisa si el fichero tiene protones o alfas).
7. **Vista**: la proyección ortográfica de la GPU dibuja los vóxeles delante del
   objetivo; un corte justo en un plano entre vóxeles pinta la cara del corte con
   el vóxel que se ve (CPU y GPU); un CT con más vóxeles por eje que
   `GL_MAX_3D_TEXTURE_SIZE` se muestra con menos resolución en 3D; la información
   del vóxel bajo el cursor y el menú contextual usan el vóxel que atraviesa el
   rayo; el resaltado de detectores y de la fuente tiñe los vóxeles; el aire del
   CT se oculta también con datos reales; la compilación de Numba al arrancar es
   la que usan los CT; y el fallo de segmentación de las texturas GL (ADR-009).
8. **Paneles**: lo que se confirma al perder el foco (panel Simulación, tabla de
   Materiales, lista de espacios de fases) se aplica antes de guardar, cerrar,
   abrir o deshacer, en su orden (`ui/deferred.py`); `Inf`, `NaN` y `1e999` en el
   `.in` se muestran y se avisan; SENERG por debajo de 50 eV (penhmain usaría
   1 GeV) se rechaza en el panel y se avisa en el `.in`; los cuadros de números
   con decimales leen «12.5» y «12,5» igual con el sistema en español
   (`ui/spinbox.py`); los mensajes de la geometría no se atribuyen a un `.in`
   nuevo; los errores del panel Simulación se ven; las acciones que no cambian
   nada no tocan el `.in`; el arrastre de la rejilla escribe números limpios; los
   espectros de miles de filas no congelan el editor y las filas pegadas que no
   se entienden se avisan; 0 intervalos no se admite; los cuerpos vacíos no se
   pueden marcar en un detector; el ajuste de la rejilla cilíndrica o esférica
   cabe dentro de la geometría aunque no esté centrada; el lector del `.ct` es
   tan estricto como gfortran («!», «/», un material «1.0»); con RESUME y un TAC
   más nuevo que el volcado se avisa (penhmain toma del volcado las densidades de
   los vóxeles).

*Nota (0.16.3, ADR-021 §2):* con materiales del `.in` reutilizados, solo es vacío lo
que se pide (un tejido que aparece al remuestrear tiene su material, o no se
importa y se dice por qué) y esos materiales del `.in` se marcan en la ficha
(`"material_del_in": true`).

## Consecuencias

* Pruebas nuevas: `tests/test_dicom_review.py`, `tests/test_ct_review.py`,
  `tests/test_ct_editor.py`, `tests/test_ct_import_dialog.py`,
  `tests/test_sim_folder_dialog.py`, `tests/test_gui_review08.py`, y casos nuevos
  en `test_ct_import.py`, `test_ct_view.py` (GPU ortográfica, corte en un plano
  de vóxeles, límite de la textura 3D), `test_simfolder.py`, `test_review_in.py`
  y `test_gui_ct.py`. Se comprobó que las pruebas principales fallan con el
  código anterior.
* Módulos nuevos: `core/files.py` (copias y escrituras que nunca sobrescriben ni
  dejan ficheros a medias), `ui/deferred.py` y `ui/spinbox.py`.
* Queda para el usuario: la corrección de CTTRACK en penhmain (o, si no, partir
  el pulmón en varios tejidos por densidad) y el valor de la energía media de
  excitación de los tejidos (la de ICRU de un tejido parecido, que propone
  `materiales.txt`, o la que calcula PENELOPE con sus valores atómicos: el
  alcance de los protones cambia alrededor de un 1 %).
* El panel Simulación (información de la caja CT, con cualquier fuente) y
  `materiales.txt` dicen que la densidad media no basta en el pulmón.
