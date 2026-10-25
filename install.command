#!/usr/bin/env bash
# macOS Finder double-clickable installer wrapper
cd "$(dirname "$0")" || exit 1
bash ./install.sh "$@"
echo
read -r -p "Press Enter to close this window..." _ || true
