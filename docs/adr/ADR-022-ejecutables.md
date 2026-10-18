# ADR-022 · Los ejecutables de Windows, macOS y Linux, y cómo se reparten

**Estado:** aceptada (versión 0.17.0)

## Contexto

Con ARGUS estable, el usuario pidió la fase F8: ejecutables para Windows, macOS y Linux que no necesiten
instalar Python ni uv, y subirlos a un repositorio privado de GitHub de su cuenta donde quien reciba el enlace
y una clave pueda descargar el de su sistema. El repositorio se actualiza solo cuando él lo diga; mientras,
se sigue trabajando en la copia local.

El plan de la F8 (en el estado del proyecto) decía: PyInstaller + Inno Setup, `.app` para Mac, sin uv ni
Python, con las traducciones de Qt, la caché de Numba y pydicom.

## Decisiones

### 1. Un ejecutable de carpeta con PyInstaller (`packaging/argus.spec`)

* **Carpeta, no un único fichero:** arranca mucho antes (no se descomprime cada vez) y los antivirus
  desconfían menos. Windows y Linux: la carpeta `ARGUS` con `ARGUS.exe` / `ARGUS`; macOS: `ARGUS.app`
  (firma «ad hoc», sin certificado de Apple). En Windows, sin ventana de consola.
* **Las versiones de `uv.lock`:** las mismas con las que se prueba ARGUS (Python 3.12, PySide6 6.11.2,
  NumPy 2.5.3, Numba 0.67.0, pydicom 3.0.2, Matplotlib 3.11.2), con PyInstaller 6.22.3 y
  pyinstaller-hooks-contrib 2026.8.
* **Lo que no va:**
  * la sonda de pruebas (`argus.oracle`) ni sus fuentes de Fortran;
  * los programas de penhan (ARGUS los pide, ADR-013);
  * los complementos de Qt que ARGUS no usa: el teclado virtual, ver PDF como imagen y el tema de GTK de
    Linux (ARGUS usa el estilo Fusion);
  * las bibliotecas que solo ellos usaban. Una biblioteca se quita solo si ninguna otra la importa: Qt Quick
    se queda porque en Linux la usa el complemento de vídeo;
  * las traducciones de Qt a otros idiomas (quedan las españolas);
  * en Windows, el OpenGL por software de Qt (ARGUS ya traza en la CPU si no hay OpenGL 4.1).
* **Lo que PyInstaller no ve y se le dice:** Matplotlib carga al guardar el módulo de cada formato. Sin
  `backend_pdf`, `backend_svg` y `backend_ps`, «Guardar la gráfica» en PDF, SVG o EPS fallaba solo en el
  ejecutable. Lo encontró la comprobación del punto 2.
* **Numba:** los dos módulos con núcleos `cache=True` (`locate_fast`, `raytrace`) van como código fuente
  (`.py`) y no en el archivo PYZ.
  * Así Numba sabe de qué fichero sale cada núcleo; si no, la caché dependía de la carpeta desde la que se
    abre ARGUS.
  * Una prueba exige que todo módulo nuevo con `cache=True` esté en esa lista.
  * La caché va a la carpeta de cachés del usuario (`NUMBA_CACHE_DIR`, `core.userdata.user_cache_dir`:
    `%LOCALAPPDATA%\ARGUS`, `~/Library/Caches/ARGUS`, `~/.cache/ARGUS`).
  * Python no escribe `__pycache__` dentro del programa. En macOS, escribir dentro de ARGUS.app rompe su
    firma; instalado para todos, la carpeta no admite escritura.
  * La primera vez se compila (15–20 s); después, 0,3 s.
* **macOS:** «Abrir con» ARGUS para los `.geo` y los `.in` (sin quitárselos a otra aplicación). Los ficheros
  que se abren desde el Finder llegan como `QFileOpenEvent` y no en la línea de órdenes. Los recoge
  `ui.file_open.ArgusApplication` en su `event()`, no con un filtro, que vería todos los eventos. Lo que
  llega antes de haber ventana espera a que la haya.
  * Qt ya manda eventos a la aplicación mientras se construye y al darle un hijo. La primera versión de
    `event()` contaba con su atributo y el ejecutable no arrancaba; lo encontró la comprobación del punto 2.
    Ahora `event()` no cuenta con él, y una prueba crea la aplicación de verdad en otro proceso.

### 2. `ARGUS --comprobar [INFORME]` (`argus/comprobar.py`)

ARGUS se comprueba a sí mismo sin preguntar nada:

* las traducciones de Qt;
* Numba: compila LOCATE y el trazador, y LOCATE da los cuerpos que tocan;
* la ventana con una geometría de prueba (una esfera en una caja, escrita por ARGUS);
* un TAC sintético escrito y leído con pydicom;
* una gráfica en PNG, PDF, SVG, EPS, JPG y TIF;
* un GIF;
* el vídeo MP4;
* OpenGL 4.1 (INFO: depende del ordenador).

Escribe un informe de texto y sale con 0 si todo está bien.

* **Sin quedarse esperando:** los diálogos que aparezcan se anotan como AVISO y se cierran. Si tarda más de
  15 minutos, escribe lo que haya y sale.
* **Sin tocar al usuario:** usa unos ajustes aparte, así que la geometría de prueba no queda en sus ficheros
  recientes.
* **Quién la usa:** la construcción la lanza en cada sistema antes de empaquetar, y quien tenga un problema
  puede enviar el informe.

### 3. Los paquetes (`packaging/construir.py`)

Los pasos: iconos (el trébol dibujado por `ui.icons`), PyInstaller, la comprobación y el paquete en
`dist/paquetes`. Cada paquete lleva `packaging/LEEME.txt`.

* **Windows:** un instalador de Inno Setup (`packaging/windows/argus.iss`).
  * Por omisión solo para el usuario y sin permisos de administrador (`%LOCALAPPDATA%\Programs\ARGUS`);
    se puede elegir para todos.
  * Siempre el mismo AppId, así que cada versión sustituye a la anterior y antes se quita su `_internal`.
  * Menú Inicio y «Abrir con» para `.geo` y `.in`; en español.
* **macOS:** un `.dmg` con ARGUS.app y un acceso a Aplicaciones.
  * Hay uno para Apple Silicon y otro para Intel, mínimo macOS 13 (el de Qt 6.11).
  * Numba 0.67 y llvmlite 0.49 ya no publican versiones para los Mac con Intel. Ese paquete va sin Numba:
    ARGUS usa NumPy, más lento en la CPU, y la comprobación lo da como INFO.
  * Sin la firma de Apple, la primera vez hay que abrirlo desde Privacidad y seguridad → «Abrir igualmente».
* **Linux:** un `.tar.gz` hecho en Ubuntu 22.04, que vale para glibc 2.35 y posteriores.
  * Lleva `instalar.sh`, que pone ARGUS en el menú de aplicaciones del usuario sin ser administrador.
* **Con contraseña (`ARGUS_CLAVE`):**
  * el instalador de Windows la pide y lleva los ficheros cifrados (XChaCha20);
  * el `.dmg` va cifrado con AES-256 (el Finder la pide);
  * en Linux sale un `.7z` cifrado, con los nombres también cifrados, que lleva el `.tar.gz` dentro.
  * Solo letras, números y `._,;:!?@#%+=*/-`, de 8 a 64 caracteres: así no da problemas en ninguno de los
    tres.

### 4. GitHub (`packaging/github/ejecutables.yml`)

* **Dónde está:** GitHub solo ejecuta los flujos de `.github/workflows`, y en el repositorio el fichero va
  allí. La copia de ARGUS está en `packaging/github`, porque la herramienta que escribe en la carpeta del PC
  no puede escribir en `.github` (es una carpeta protegida). `revisar_repo.py` comprueba que las dos sean
  iguales.
* **Cuándo:** a mano (Actions → «Ejecutables» → «Run workflow») o al subir una etiqueta `vX.Y.Z`, que tiene
  que coincidir con la versión de `pyproject.toml`. Subir cambios no construye nada, como pidió el usuario.
* **Pasos:**
  1. `packaging/revisar_repo.py`: nada de fuentes de PENELOPE, penhan o PENGEOM, ni PenGeomJar, ni DICOM, ni
     compilados. Y la contraseña: sin el secreto `ARGUS_CLAVE`, o con una que no vale, el flujo se para ahí,
     antes de construir nada, porque el repositorio es público y los paquetes irían sin contraseña. Para
     publicarlos sin ella a propósito, la variable `ARGUS_SIN_CLAVE` = `si`.
  2. Las pruebas en Linux.
  3. Los cuatro paquetes (Windows, macOS Apple Silicon, macOS Intel y Linux), cada uno comprobado con
     `--comprobar` en su sistema.
* **La publicación:** queda en borrador hasta que todo sale bien y lleva las sumas SHA-256.
  * Los paquetes no pasan por los artefactos de Actions: un repositorio privado gratuito tiene 500 MB para
    ellos, y los cuatro paquetes ocupan más. Se suben a la publicación.
* **Los ficheros, byte a byte (`.gitattributes`, `* -text`):** Git para Windows cambia los finales de línea al
  subir. Los `.geo` y `.in` del corpus llevan CRLF y las pruebas comparan bytes, y los `.bat` necesitan CRLF.
* **Coste:** en un repositorio público, nada. En uno privado gratuito hay 2000 minutos al mes, y los de macOS
  cuestan mucho más que los de Linux.
* **Ajustes del repositorio** (Settings → Secrets and variables → Actions):
  * secreto `ARGUS_CLAVE`: la contraseña de los paquetes (sin él, el flujo se para);
  * opcional, variable `REPO_DESCARGAS` y secreto `TOKEN_DESCARGAS`: los paquetes se publican también en otro
    repositorio, sin el código.

### 5. El enlace con clave: un repositorio público con los paquetes cerrados

Un repositorio privado de GitHub no se abre con un enlace y una contraseña. Sus publicaciones solo las ve
quien está añadido como colaborador, cada uno con su cuenta de GitHub, y en una cuenta personal los
colaboradores tienen siempre permiso de escritura.

**Decisión del usuario (18-10-2026):** el repositorio de ARGUS es público y los paquetes llevan contraseña
(`ARGUS_CLAVE`, un secreto del repositorio que no está en ningún fichero de ARGUS). El enlace que se da es
el de «Releases».

* **Lo que protege la contraseña:** los paquetes listos para instalar, no el código. El código es público, y
  quien sepa usar Python puede ejecutar ARGUS desde él sin contraseña.
* **Lo que no va en el repositorio público (`.gitignore`):**
  * los ejemplos de PENELOPE, PenGeomJar y penhan de `tests/corpus` (`pengeomjar`, `ornl`, `penhan`), que
    no se pueden redistribuir;
  * los casos del usuario (`tests/corpus/usuario`).
  * Siguen en la carpeta local. Sin ellos, `tests/conftest.py` omite las pruebas de los ficheros que los
    nombran y el manifiesto del corpus solo da lo que está; `revisar_repo.py` los señala si se subieran.
* **Ventajas:** en un repositorio público las ejecuciones de GitHub Actions son gratis y no hace falta ningún
  token ni un segundo repositorio. La copia en otro repositorio (`REPO_DESCARGAS`) queda como opción.

### 6. Construidos aquí, sin GitHub

* **Por qué:** esta sesión no tiene acceso a ningún repositorio de GitHub del usuario (solo a los que se le
  asignan), así que aún no se puede subir ni lanzar el flujo.
* **Windows:** se construyó en la nube con Wine 11 y el Python 3.12.10 de Windows, con las mismas versiones
  de `uv.lock`.
  * El Qt 6.11 de Windows usa la ICU del sistema (`icuuc.dll`), que Wine no tiene. Para construir y probar
    se le dio una que reenvía a la ICU 72 de Microsoft; no va en el ejecutable, porque es del sistema.
  * Comprobado con Wine: el ejecutable, el instalador (instalación silenciosa, «Abrir con» en el registro) y
    el de contraseña, que sin ella se niega.
  * Es para probarlo en un Windows de verdad; los oficiales salen del flujo de GitHub.
* **Linux:** se construyó en Ubuntu 24.04 y se comprobó, con la plataforma offscreen y en X11 con OpenGL.
  Por su glibc 2.39 vale solo para distribuciones recientes; el oficial sale de Ubuntu 22.04.
* **macOS:** solo se puede construir en un Mac, así que lo hará el flujo de GitHub.

## Lo que queda

* **Firmas:** sin un certificado, Windows avisa (SmartScreen) y macOS pide «Abrir igualmente». Firmar
  necesita un certificado de firma de código para Windows, y para macOS la cuenta de desarrollador de Apple
  con la notarización (de pago). No se ha hecho.
* **macOS:** los paquetes se construyen y se comprueban en GitHub (`--comprobar` en el ejecutor); nadie los ha
  abierto aún en un Mac.
* **Mac con Intel:** va sin Numba mientras Numba no vuelva a publicar versiones para él.
* **Linux:** el paquete lleva las bibliotecas del sistema donde se construye (Ubuntu 22.04), como todo
  ejecutable de PyInstaller; también `libpulse.so.0`, que Qt Multimedia necesita (por eso se instala
  `libpulse0` en el ejecutor).
* **Tamaño:** el instalador de Windows ocupa unos 80 MB y el paquete de Linux unos 170 MB. llvmlite, de
  Numba, es lo que más pesa.

## Verificación

* **Pruebas:** 40 nuevas en `tests/test_distribucion.py`.
  * `user_cache_dir` en los tres sistemas, los ajustes del ejecutable, los argumentos del Finder y
    `--comprobar`;
  * los ficheros que pide abrir macOS, antes y después de haber ventana;
  * el informe; la comprobación entera desde el código; un diálogo que aparece y se cierra solo; una
    comprobación que no termina y otra que falla;
  * la receta: los núcleos de Numba, el instalador, el LEEME, la contraseña, el paquete de Linux y el flujo,
    que se para sin la contraseña o con una que no vale (el paso del flujo, ejecutado con bash);
  * la revisión del repositorio con cada cosa que no debe ir, y con ARGUS;
  * los iconos.
* **Ejecutable de Linux:** `--comprobar` correcto en offscreen y en X11 (Xvfb, OpenGL 4.5 con llvmpipe).
  La segunda vez, Numba sale de la caché (0,3 s frente a 16 s).
* **Ejecutable de Windows con Wine:** `--comprobar` correcto, con OpenGL y MP4 con H.264. El instalador
  instala solo para el usuario (menú Inicio y «Abrir con»), lo instalado pasa la comprobación y el
  desinstalador lo quita todo, también del registro. El de contraseña se niega sin ella e instala con ella.
* **Flujo:** actionlint (con shellcheck) sin avisos.
* **Primera ejecución en GitHub (18-10-2026,
  [guerreroalonso/ARGUS](https://github.com/guerreroalonso/ARGUS)):** el contenido, la contraseña, Windows y los
  dos Mac, bien, cada uno con su `--comprobar`. Fallaron el de Linux y las pruebas por lo mismo: Qt Multimedia no
  se carga sin `libpulse.so.0`, que los ejecutores de GitHub no traen (en la nube estaba). Se instala `libpulse0`
  (y `libxrandr2`, que pide el complemento de FFmpeg) en los dos trabajos. Además, si la publicación sigue en
  borrador de una ejecución anterior, el flujo la pasa al commit nuevo (la etiqueta se crea al publicar).
* **Paquete con contraseña de Linux:** el `.7z` no se abre con otra contraseña.
* **El resto:** la batería completa de pruebas y ruff.

## Consecuencias

* ARGUS se puede dar a quien no tiene Python. El código no cambia de forma de trabajar: se sigue probando
  desde el código con `uv`, y `ARGUS.bat` sigue valiendo.
* Cada cambio de versión de una biblioteca en `uv.lock` se nota en los ejecutables. La comprobación de cada
  sistema lo vigila, y conviene mirar su informe en cada publicación.
* Un módulo nuevo con núcleos de Numba en caché tiene que ir en `NUMBA_MODULES` de la receta (lo exige una
  prueba). Un formato de gráfica nuevo puede necesitar su módulo de Matplotlib en la receta.
