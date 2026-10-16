#!/bin/bash
# DJ MAOS para macOS  ->  dist/DJMAOS.app  y  DJMAOS.dmg
# Uso:   chmod +x build_mac.sh && ./build_mac.sh
set -e
cd "$(dirname "$0")"

command -v python3  >/dev/null || { echo "Falta Python 3 (python.org)"; exit 1; }
command -v codesign >/dev/null || { echo "Faltan las herramientas de Xcode: xcode-select --install"; exit 1; }

echo "==> Entorno de Python y dependencias"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pyinstaller PySide6

echo "==> Compilando DJMAOS.app"
pyinstaller --noconfirm --windowed --onedir --name DJMAOS \
  --icon DJMAOS.icns --osx-bundle-identifier com.djmaos.music \
  --add-data "logo.png:." \
  --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick \
  --exclude-module PySide6.QtPdf --exclude-module PySide6.QtSvg --exclude-module PySide6.QtOpenGL \
  --exclude-module tkinter --exclude-module unittest --exclude-module pydoc \
  DJMAOS.py

APP="dist/DJMAOS.app"
MACOS="$APP/Contents/MacOS"
cp logo.png "$MACOS/logo.png"

echo "==> yt-dlp"
mkdir -p tools
if [ ! -f tools/yt-dlp ]; then
  curl -L --fail -o tools/yt-dlp https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos
fi
chmod +x tools/yt-dlp
cp tools/yt-dlp "$MACOS/yt-dlp"

echo "==> ffmpeg y ffprobe estáticos (así no hace falta instalar nada más)"
case "$(uname -m)" in arm64) SUF=arm ;; *) SUF=intel ;; esac
for t in ffmpeg ffprobe; do
  if [ ! -f "tools/$t" ]; then
    echo "   bajando $t ($SUF)..."
    if curl -L --fail -s -o "tools/$t.zip" "https://www.osxexperts.net/${t}80${SUF}.zip" \
       && unzip -o -q "tools/$t.zip" -d tools; then
      rm -f "tools/$t.zip"
    else
      rm -f "tools/$t.zip"
      echo "   (no pude bajar $t; la app va a usar el de Homebrew si está: brew install ffmpeg)"
    fi
  fi
done
if [ -f tools/ffmpeg ] && [ -f tools/ffprobe ]; then
  cp tools/ffmpeg tools/ffprobe "$MACOS/"
  chmod +x "$MACOS/ffmpeg" "$MACOS/ffprobe"
  echo "   ffmpeg y ffprobe incluidos en la app"
fi

echo "==> Firma ad-hoc (hace falta en Mac con chip Apple)"
codesign --force --deep --sign - "$APP"

echo "==> Creando DJMAOS.dmg"
rm -rf dmgroot && mkdir dmgroot
cp -R "$APP" dmgroot/
ln -s /Applications dmgroot/Applications
cat > "dmgroot/LEEME - primera vez.txt" <<'TXT'
DJ MAOS - cómo abrirlo la primera vez (macOS Sequoia)

1) Arrastrá DJMAOS a la carpeta Aplicaciones.
2) Abrí la app Terminal, pegá esta línea y apretá Enter:

   xattr -dr com.apple.quarantine /Applications/DJMAOS.app

3) Abrí DJMAOS desde Aplicaciones. Solo hace falta hacerlo una vez.

Alternativa: intentá abrirla, andá a Ajustes del Sistema > Privacidad y seguridad,
y tocá "Abrir igualmente" (ese botón aparece solo por un rato después del primer intento).
TXT
rm -f DJMAOS.dmg
hdiutil create -volname "DJ MAOS" -srcfolder dmgroot -ov -format UDZO DJMAOS.dmg
rm -rf dmgroot

echo
echo "Listo:  dist/DJMAOS.app   y   DJMAOS.dmg"
