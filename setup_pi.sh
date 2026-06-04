#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv_pi"
PYTHON_BIN="python3"
APP_NAME="VocalogPiSubtitleLCD"
BUILD_DIR="$ROOT_DIR/build_pi"
DIST_DIR="$ROOT_DIR/dist_pi"
PYTHON_DEPS=(PySide6)

install_system_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-pip python3-venv
    fi
}

create_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
}

install_python_packages() {
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/python" -m pip install "${PYTHON_DEPS[@]}"
}

build_executable() {
    rm -rf "$BUILD_DIR" "$DIST_DIR"
    "$VENV_DIR/bin/python" -m PyInstaller \
        --noconfirm \
        --clean \
        --onefile \
        --noconsole \
        --name "$APP_NAME" \
        --distpath "$DIST_DIR" \
        --workpath "$BUILD_DIR" \
        pi_lcd.py
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "python3를 찾을 수 없습니다. 먼저 Raspberry Pi에 Python 3를 설치하세요."
    exit 1
fi

install_system_packages
create_venv
install_python_packages
"$VENV_DIR/bin/python" -m pip install pyinstaller
build_executable

echo "완료: $DIST_DIR/$APP_NAME"