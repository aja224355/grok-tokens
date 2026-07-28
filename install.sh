#!/usr/bin/env bash
#
# grok-tokens one-line installer
#
# Remote (recommended):
#   curl -fsSL https://raw.githubusercontent.com/OWNER/grok-tokens/main/install.sh | bash
#
# Local checkout (dev):
#   ./install.sh
#
# Installs a single executable to ~/.local/bin/grok-tokens
# (pure Python 3 + stdlib — no pip, no cargo, no glibc binary issues).
#
set -euo pipefail

# ── configure when publishing ──────────────────────────────────────────
# Override: GROK_TOKENS_REPO=myuser/grok-tokens curl ... | bash
REPO_SLUG="${GROK_TOKENS_REPO:-alientek/grok-tokens}"
# ───────────────────────────────────────────────────────────────────────

INSTALL_DIR="${HOME}/.local/bin"
BINARY_NAME="grok-tokens"
SCRIPT_NAME="grok_tokens.py"

echo "Installing grok-tokens..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found in PATH."
  echo "Install Python 3.8+ and re-run."
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
# Optional soft check
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' || {
  echo "Error: Python 3.8+ required (found ${PY_VER})."
  exit 1
}

mkdir -p "$INSTALL_DIR"
DEST="${INSTALL_DIR}/${BINARY_NAME}"

# ── Mode A: local checkout (./install.sh from the repo) ────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/${SCRIPT_NAME}" ]]; then
  echo "Local checkout detected — installing from ${SCRIPT_DIR}"
  # Prefer a stable launcher that always tracks the repo file (dev-friendly)
  cat > "$DEST" <<EOF
#!/usr/bin/env bash
exec python3 "${SCRIPT_DIR}/${SCRIPT_NAME}" "\$@"
EOF
  chmod +x "$DEST" "${SCRIPT_DIR}/${SCRIPT_NAME}"
  if [[ -f "${SCRIPT_DIR}/bin/grok-tokens" ]]; then
    chmod +x "${SCRIPT_DIR}/bin/grok-tokens"
  fi
  echo "Installed: ${DEST}"
  echo "  (runs ${SCRIPT_DIR}/${SCRIPT_NAME})"
  echo ""
  echo "Add to PATH if needed:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo ""
  echo "Verify:"
  echo "  grok-tokens --version"
  echo "  grok-tokens daily"
  exit 0
fi

# ── Mode B: remote install (curl | bash) ───────────────────────────────
# Prefer versioned GitHub Release asset (single portable script named grok-tokens).
# Fallback: raw main branch grok_tokens.py

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Error: '$1' is required to download grok-tokens."
    exit 1
  }
}
need_cmd curl

TMP="$(mktemp)"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

download_ok=0

# 1) Latest release asset
LATEST_TAG=$(
  curl -fsSL "https://api.github.com/repos/${REPO_SLUG}/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -1 || true
)
if [[ -n "${LATEST_TAG}" ]]; then
  # Asset name is just "grok-tokens" (one file for all platforms)
  URL="https://github.com/${REPO_SLUG}/releases/download/${LATEST_TAG}/${BINARY_NAME}"
  echo "Downloading release ${LATEST_TAG} ..."
  if curl -fsSL -o "$TMP" "$URL"; then
    download_ok=1
  else
    echo "Release asset not found at ${URL}, trying raw main..."
  fi
fi

# 2) Fallback: raw main
if [[ "$download_ok" -ne 1 ]]; then
  URL="https://raw.githubusercontent.com/${REPO_SLUG}/main/${SCRIPT_NAME}"
  echo "Downloading ${URL} ..."
  if curl -fsSL -o "$TMP" "$URL"; then
    download_ok=1
  fi
fi

if [[ "$download_ok" -ne 1 ]]; then
  echo "Download failed."
  echo "Clone and install manually:"
  echo "  git clone https://github.com/${REPO_SLUG}.git"
  echo "  cd grok-tokens && ./install.sh"
  exit 1
fi

# Ensure shebang + executable
if ! head -1 "$TMP" | grep -q '^#!'; then
  {
    echo '#!/usr/bin/env python3'
    cat "$TMP"
  } > "${TMP}.new"
  mv "${TMP}.new" "$TMP"
fi

# Quick smoke: file must look like our script
if ! grep -q 'grok-tokens' "$TMP" 2>/dev/null && ! grep -q 'totalTokens\|inputTokens' "$TMP" 2>/dev/null; then
  echo "Downloaded file does not look like grok-tokens. Aborting."
  exit 1
fi

install -m 0755 "$TMP" "$DEST"

echo ""
echo "Installed: ${DEST}"
echo ""
echo "Add to PATH if needed:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo ""
echo "Verify:"
echo "  grok-tokens --version"
echo "  grok-tokens daily"
echo ""
echo "Done."
