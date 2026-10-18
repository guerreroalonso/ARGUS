@echo off
rem Sin cambiar de carpeta (uv --project): un fichero con ruta relativa se encuentra donde se da, y funciona
rem tambien desde una carpeta de red, donde cmd no puede entrar con cd.
where uv >nul 2>nul
if errorlevel 1 set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run --project "%~dp0." argus %*
if errorlevel 1 pause
