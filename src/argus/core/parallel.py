"""Los núcleos paralelos de Numba, de uno en uno.

La capa de hilos «workqueue» de Numba (la que queda en Windows si no están ni
TBB ni el OpenMP de Microsoft, ``vcomp140.dll``) no admite que dos hilos lancen
a la vez funciones ``parallel=True``: el proceso se cierra de golpe con
«Numba workqueue threading layer is terminating: Concurrent access has been
detected». ARGUS lanza esos núcleos desde el hilo del corte 2D, desde el de la
compilación inicial y desde el de la ventana (vista 3D en la CPU, puntos de la
vista 3D que tapa la geometría), así que todos pasan por este cerrojo. Cada
núcleo ya reparte el trabajo entre todos los núcleos de la CPU: esperar al otro
no cuesta tiempo.
"""

from __future__ import annotations

import threading

PARALLEL_LOCK = threading.RLock()
