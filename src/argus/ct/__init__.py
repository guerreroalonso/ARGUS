"""Importar TACs (DICOM) como estructuras CT (CTBOX) de PENGEOM.

* ``dicom``: buscar series en una carpeta y leerlas como un volumen de HU.
* ``calibration``: curva HU → densidad y rangos de HU → material.
* ``grid``: recorte, tamaño de vóxel (remuestreo) y colocación en la geometría.
* ``importer``: todo junto: el fichero .ct, su ficha y los cambios en el .geo y el .in.

ARGUS no guarda datos del paciente: de la serie solo anota datos técnicos
(descripción, fecha, equipo, tamaño de los cortes).
"""


class Cancelled(Exception):
    """El usuario ha cancelado una operación larga."""
