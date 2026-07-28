#!/usr/bin/env bash
# Install grok-tokens into ~/.local/bin (symlink to this checkout)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_SRC="$ROOT/bin/grok-tokens"
INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"
chmod +x "$ROOT/grok_tokens.py" "$BIN_SRC"
ln -sfn "$BIN_SRC" "$INSTALL_DIR/grok-tokens"
echo "Installed: $INSTALL_DIR/grok-tokens -> $BIN_SRC"
echo "Ensure PATH includes: export PATH=\"\$HOME/.local/bin:\$PATH\""
"$INSTALL_DIR/grok-tokens" --help | head -5
