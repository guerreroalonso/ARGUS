#!/bin/sh
# Pone ARGUS en el menú de aplicaciones de este usuario (sin permisos de administrador; 0.17.0).
# ARGUS queda donde se ha descomprimido: si se mueve la carpeta, hay que volver a ejecutar este guion.
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
EXE="$DIR/ARGUS/ARGUS"
if [ ! -x "$EXE" ]; then
    echo "No se encuentra $EXE (¿se ha descomprimido el paquete entero?)" >&2
    exit 1
fi
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/256x256/apps"
mkdir -p "$APPS" "$ICONS"
cp "$DIR/argus.png" "$ICONS/argus.png"
cat > "$APPS/argus.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ARGUS
Comment=Geometrías PENGEOM y ficheros de entrada de penhan (PENELOPE)
Exec="$EXE" %f
Icon=argus
Terminal=false
Categories=Science;Physics;Education;
StartupWMClass=ARGUS
EOF
chmod +x "$APPS/argus.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi
echo "ARGUS ya está en el menú de aplicaciones."
echo "Para quitarlo de allí: rm \"$APPS/argus.desktop\" \"$ICONS/argus.png\""
