"""Resultados de penhmain: leerlos, combinarlos y analizarlos (ADR-014).

* ``formats``: lectura rápida de los ficheros .dat de penhmain (formato de gnuplot).
* ``data``: modelo de datos (curvas, rejillas, resumen, trayectorias, espacio de fases).
* ``catalog``: qué es cada fichero, reconocido por su contenido (aunque se haya renombrado).
* ``readers``: un lector por tipo de salida.
* ``summary``: el resumen de ``penhmain-res.dat``.
* ``folder``: las salidas de una carpeta, su estado y su progreso.
* ``combine``: juntar ejecuciones independientes (semillas distintas) como penhmain-sum.
* ``units``: unidades y normalizaciones.

Todas las incertidumbres se guardan como 1 sigma; penhmain las escribe como 3 sigma.
"""
