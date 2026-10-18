@echo off
setlocal
rem pushd (y no cd): tambien desde una carpeta de red (\\servidor\...), a la que le da una letra.
pushd "%~dp0"
echo === Instalacion de ARGUS ===
where uv >nul 2>nul
if %errorlevel%==0 goto sync
if exist "%USERPROFILE%\.local\bin\uv.exe" goto addpath
echo.
echo No se encuentra uv (gestor de Python). Se instala con el instalador oficial:
echo   https://docs.astral.sh/uv/
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto fail
:addpath
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
:sync
echo.
echo Descargando Python 3.12 y las bibliotecas de ARGUS (PySide6, NumPy, Numba, pydicom, Matplotlib)...
echo La primera vez tarda unos minutos.
uv sync --extra dev
if errorlevel 1 goto fail
echo.
echo Listo. Para abrir ARGUS: doble clic en ARGUS.bat
echo (o arrastre un fichero .in o .geo sobre ARGUS.bat).
pause
popd
exit /b 0
:fail
echo.
echo Algo ha fallado. Copie el mensaje de arriba.
pause
popd
exit /b 1
