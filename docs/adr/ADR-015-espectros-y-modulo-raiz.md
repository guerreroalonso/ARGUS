# ADR-015 · Espectro de la fuente desde un fichero y mover el módulo raíz

**Estado:** aceptada (versión 0.12.1)

## Contexto

Rafael pidió (04-10-2026):

* cambiar la fuente de una energía única a un espectro, a mano o desde un fichero de espectro, y que ARGUS pregunte
  cuál de las dos al hacer el cambio;
* saber si penhmain admite varias fuentes;
* por qué no se puede girar el módulo de `examples/disc` de penhan («caería fuera de la geometría»).

## Decisión

### 1. Varias fuentes: no

penhmain admite una sola fuente por simulación: una partícula (`SKPAR`), una energía o un espectro (`SENERG`,
`SPECTR` o `SLINES`, hasta 250 líneas), una posición o una caja (`SPOSIT`, `SBOX`, con varios cuerpos activos de la
misma actividad) y una dirección (`SCONE` o `SRECTA`); o, en su lugar, ficheros de espacio de fases (`IPSFN`, hasta
100). Se le propuso simularlas por separado y juntarlas con su peso; Rafael prefirió dejarlo como está.

### 2. De energía única a espectro

* El botón «Espectro…» del panel «Fuente», con energía única, pregunta: escribir las energías a mano o leerlas de un
  fichero (`ui/spectrum_import.SpectrumStartDialog`). Después, como siempre, el editor del espectro, donde se revisa
  y se aplica al .in.
* El editor tiene «Importar…» (con la misma vista previa) y «Guardar…» (formato de ARGUS).

### 3. Ficheros de espectro (`penhan/spectrum_file.py`)

* **Formato de ARGUS:** texto con una cabecera `# clave: valor`:
  * `unidad`: eV, keV, MeV o GeV;
  * `tipo`: continuo (`SPECTR`) o líneas (`SLINES`);
  * `energía`: borde inferior de cada intervalo, centro, o bordes (tres columnas: Emin, Emax, valor);
  * `valor`: probabilidad de cada intervalo, densidad (por unidad de energía) o cuentas.

  Cuenta la primera palabra de cada valor (el ejemplo de la ayuda, copiado con sus explicaciones, se lee bien).
* **Tablas** de dos o tres columnas, de una hoja de cálculo o de texto (coma o punto decimal, como al pegar). Sin
  cabecera de ARGUS:
  * la unidad sale de los títulos («E (keV)», «Energy [MeV]», «E/MeV»);
  * un valor por unidad de energía («1/keV», «keV^-1», «por MeV») es una densidad en el centro de cada intervalo
    (como exportan los programas de espectros de rayos X);
  * tres columnas cuyos intervalos no se solapan son «Emin Emax valor» (un hueco es un intervalo de probabilidad 0);
  * sin unidad: eV si los números pasan de 10⁴; si no, MeV (protones, alfas, o hasta 20 con fotones y electrones) o
    keV.
* **Líneas `SPECTR`/`SLINES`** de otro .in, en eV, como las lee penhmain (las energías iguales conservan su orden:
  un intervalo de anchura cero es una energía exacta).
* **Salidas de penhmain** reconocidas por su contenido (`spc-impdet`, `spc-angdet`, `energy-up`/`energy-down`,
  `psource`, `usource`…), con la serie de la partícula de la fuente o la que se elija. La probabilidad de cada
  intervalo es la densidad por su anchura. Se avisa si es la fluencia o la energía depositada, y si la partícula
  no es la de la fuente.
* **Siempre:**
  * en un espectro continuo, la última fila es la energía máxima (si tiene valor, su intervalo mide como el
    anterior, y se dice);
  * los valores negativos cuentan como 0;
  * se quitan los intervalos vacíos de los extremos;
  * se normaliza.
* **Más de 250 filas** (MISPEC, MLINE): se juntan los intervalos vecinos de menos probabilidad (los picos
  conservan su detalle y la probabilidad de cada zona se conserva); las líneas, las más próximas, en su energía
  media.
* La vista previa enseña qué es el fichero, cómo se ha entendido (unidad, tipo, columnas, serie: se puede cambiar)
  y el espectro que da, con lo que se ha hecho para llegar a él.

### 4. El módulo raíz se mueve libremente

Con un solo elemento en el nivel superior, GEOMIN no añade la envolvente: ese módulo es toda la geometría. ARGUS solo
dejaba mover un módulo dentro de lo que lo aloja y, sin envolvente, lo de fuera contaba como prohibido. Así no se
podía ni girar el disco del ejemplo `disc` de penhan.

Ahora `placement.check_motion` admite lo de fuera para la raíz, porque allí no hay nada. La comprobación después de
mover (`motion_problems`) ya lo admitía. Un medio alrededor (aire) se pone con «Envolver en un módulo…», con margen
y material.

## Consecuencias

* Pruebas en `test_spectrum_file.py`, `test_spectrum_ui.py`, `test_placement.py` (con PENGEOM real: el módulo raíz
  girado da las mismas tablas y LOCATE) y `test_gui_root_module.py`.
