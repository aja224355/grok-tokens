#!/usr/bin/env bash
#
# grok-tokens installer (Rust binary)
#
# Remote:
#   curl -fsSL https://raw.githubusercontent.com/OWNER/grok-tokens/main/install.sh | bash
#
# Local checkout:
#   ./install.sh
#
# Preference order:
#   1) Local cargo build (dev checkout)
#   2) Prebuilt native binary from GitHub Releases (musl/gnu/mac)
#
set -euo pipefail

REPO_SLUG="${GROK_TOKENS_REPO:-alientek/grok-tokens}"
INSTALL_DIR="${HOME}/.local/bin"
BINARY_NAME="grok-tokens"

echo "Installing grok-tokens..."
mkdir -p "$INSTALL_DIR"
DEST="${INSTALL_DIR}/${BINARY_NAME}"

# ── helpers ──────────────────────────────────────────────────────────────
need_curl() {
  command -v curl >/dev/null 2>&1 || {
    echo "Error: curl is required."
    exit 1
  }
}

detect_target() {
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  arch=$(uname -m)
  case "$os-$arch" in
    linux-x86_64|linux-amd64)   echo "x86_64-unknown-linux-musl" ;;
    linux-aarch64|linux-arm64)  echo "aarch64-unknown-linux-musl" ;;
    darwin-arm64|darwin-aarch64) echo "aarch64-apple-darwin" ;;
    darwin-x86_64)              echo "x86_64-apple-darwin" ;;
    # glibc fallback names (older releases)
    *) echo "" ;;
  esac
}

install_file() {
  local src="$1"
  install -m 0755 "$src" "$DEST"
  echo "Installed: ${DEST}"
}

# ── Mode A: local git checkout ───────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/Cargo.toml" ]]; then
  echo "Local checkout detected: ${SCRIPT_DIR}"

  # Prefer release binary if present or cargo available
  if [[ -x "${SCRIPT_DIR}/target/release/${BINARY_NAME}" ]]; then
    install_file "${SCRIPT_DIR}/target/release/${BINARY_NAME}"
  elif command -v cargo >/dev/null 2>&1; then
    echo "Building release binary with cargo..."
    (cd "${SCRIPT_DIR}" && cargo build --release)
    install_file "${SCRIPT_DIR}/target/release/${BINARY_NAME}"
  else
    echo "Error: cargo is required to build from a local checkout."
    echo "Install Rust: https://rustup.rs"
    echo "Or install a release binary:"
    echo "  curl -fsSL https://raw.githubusercontent.com/${REPO_SLUG}/main/install.sh | bash"
    exit 1
  fi

  echo ""
  echo "Add to PATH if needed:  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "Verify:  grok-tokens --version && grok-tokens daily"
  exit 0
fi

# ── Mode B: remote install ───────────────────────────────────────────────
need_curl
TARGET="$(detect_target)"
TMP="$(mktemp)"
trap 'rm -f "$TMP" "$TMP.tgz"' EXIT

LATEST_TAG=$(
  curl -fsSL "https://api.github.com/repos/${REPO_SLUG}/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -1 || true
)
[[ -z "${LATEST_TAG}" ]] && LATEST_TAG="v0.1.0"

download_ok=0

# 1) Preferred: musl/native tarball  grok-tokens-<target>.tar.gz
if [[ -n "$TARGET" ]]; then
  for name in \
    "grok-tokens-${TARGET}.tar.gz" \
    "grok-tokens-${TARGET}"
  do
    URL="https://github.com/${REPO_SLUG}/releases/download/${LATEST_TAG}/${name}"
    echo "Trying ${URL} ..."
    if curl -fsSL -o "$TMP" "$URL"; then
      if [[ "$name" == *.tar.gz ]]; then
        mkdir -p "${TMP}.dir"
        if tar -xzf "$TMP" -C "${TMP}.dir" 2>/dev/null; then
          BIN=$(find "${TMP}.dir" -type f -name "${BINARY_NAME}" | head -1)
          if [[ -n "$BIN" && -f "$BIN" ]]; then
            install_file "$BIN"
            download_ok=1
          fi
        fi
        rm -rf "${TMP}.dir"
      else
        # raw binary
        if file "$TMP" 2>/dev/null | grep -qiE 'executable|ELF|Mach-O' \
          || head -c 4 "$TMP" | grep -q $'\x7fELF' \
          || head -c 4 "$TMP" | grep -q $'\xcf\xfa\xed\xfe'; then
          install_file "$TMP"
          download_ok=1
        fi
      fi
    fi
    [[ "$download_ok" -eq 1 ]] && break
  done
fi

if [[ "$download_ok" -ne 1 ]]; then
  echo "Download failed: no native binary for this platform."
  echo "Manual options:"
  echo "  cargo install --git https://github.com/${REPO_SLUG} --locked"
  echo "  git clone https://github.com/${REPO_SLUG}.git && cd grok-tokens && ./install.sh"
  exit 1
fi

echo ""
echo "Add to PATH if needed:  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "Verify:  grok-tokens --version && grok-tokens daily"
echo "Done."
