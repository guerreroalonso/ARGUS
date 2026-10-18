# ADR-003: verificación contra PENGEOM (oráculo)

Fecha: 2026-09-23. Estado: aceptada.

## Contexto

Si ARGUS interpreta una geometría de forma distinta a PENGEOM, el usuario
vería una cosa y simularía otra. La única referencia fiable es el propio
código Fortran.

## Decisión

* `src/argus/oracle/fortran/argus_probe.f90` es un programa que enlaza el
  `pengeom.f` del usuario **sin modificarlo** (los módulos `TRACK_mod` y
  `PENERROR_mod` se aportan en `argus_modules.f90`). Lee la geometría con
  `GEOMIN` y vuelca sus tablas internas, localiza puntos con `LOCATE` y sigue
  rayos con `STEP`.
* `tools/build_probe.py` la compila con gfortran; `argus.oracle.probe` la
  ejecuta en la carpeta del `.geo` y lee sus resultados desde un directorio
  temporal (nunca escribe en las carpetas del usuario).
* Las pruebas `oracle` exigen igualdad **bit a bit** de los coeficientes de
  las superficies y de todas las tablas del árbol (`KSURF`, `KFLAG`, `KDGHT`,
  `KBODY`, `KMOTH`, `MATER`, `KPLANE`), y el mismo cuerpo y material en
  `LOCATE` para puntos aleatorios y para puntos sobre superficies.
* Corpus: 25 geometrías de PenGeomJar, 6 maniquíes ORNL, 7 ejemplos de penhan
  y 5 geometrías del usuario (`tests/corpus/manifest.json`, con los valores
  `PARINP` de su `.in`).

## Consecuencias

* Detalles del Fortran que parecen erratas se reproducen a propósito (por
  ejemplo, que un módulo añada otra vez a la hija cuando uno de sus cuerpos
  limitantes no está entre las hijas, o que los clones de planos tengan
  `KPLANE=0`).
* La lectura de reales debe redondear como `strtod`: multiplicar la mantisa
  por potencias de diez daba diferencias de 1 ulp.
