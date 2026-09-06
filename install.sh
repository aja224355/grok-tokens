#!/usr/bin/env bash
#
# grok-tokens installer — download a native binary from GitHub Releases.
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/aja224355/grok-tokens/main/install.sh | bash
#
# Local checkout (optional, skipped when piped through curl):
#   ./install.sh
#
set -euo pipefail

REPO_SLUG="${GROK_TOKENS_REPO:-aja224355/grok-tokens}"
INSTALL_DIR="${GROK_TOKENS_INSTALL_DIR:-${HOME}/.local/bin}"
BINARY_NAME="grok-tokens"

echo "Installing grok-tokens..."
mkdir -p "$INSTALL_DIR"
DEST="${INSTALL_DIR}/${BINARY_NAME}"

need_curl() {
  command -v curl >/dev/null 2>&1 || {
    echo "Error: curl is required."
    exit 1
  }
}

# curl | bash lands BASH_SOURCE at /dev/fd/N (or empty). Never treat CWD as a
# checkout in that case — otherwise a clone directory would cargo-build instead
# of downloading the release binary.
is_piped_install() {
  local src="${BASH_SOURCE[0]:-}"
  [[ -z "$src" || "$src" == "-" || "$src" == /dev/fd/* || "$src" == /proc/self/fd/* ]]
}

# Preferred target first, then a glibc fallback for Linux.
detect_targets() {
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  arch=$(uname -m)
  case "$os-$arch" in
    linux-x86_64|linux-amd64)
      echo "x86_64-unknown-linux-musl"
      echo "x86_64-unknown-linux-gnu"
      ;;
    linux-aarch64|linux-arm64)
      echo "aarch64-unknown-linux-musl"
      echo "aarch64-unknown-linux-gnu"
      ;;
    darwin-arm64|darwin-aarch64)
      echo "aarch64-apple-darwin"
      ;;
    darwin-x86_64)
      echo "x86_64-apple-darwin"
      ;;
    *)
      return 1
      ;;
  esac
}

looks_like_binary() {
  local path="$1"
  local magic
  magic="$(head -c 4 "$path" 2>/dev/null || true)"
  [[ "$magic" == $'\x7fELF' || "$magic" == $'\xcf\xfa\xed\xfe' || "$magic" == $'\xfe\xed\xfa\xce' || "$magic" == $'\xfe\xed\xfa\xcf' ]]
}

install_file() {
  local src="$1"
  install -m 0755 "$src" "$DEST"
  echo "Installed: ${DEST}"
}

finish() {
  echo ""
  echo "Add to PATH if needed:  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "Verify:  grok-tokens --version && grok-tokens daily"
}

# ── Mode A: real file next to Cargo.toml (git clone / ./install.sh) ──────
SCRIPT_DIR=""
if ! is_piped_install && [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/Cargo.toml" && -z "${GROK_TOKENS_FORCE_DOWNLOAD:-}" ]]; then
  echo "Local checkout detected: ${SCRIPT_DIR}"

  if [[ -x "${SCRIPT_DIR}/target/release/${BINARY_NAME}" ]]; then
    install_file "${SCRIPT_DIR}/target/release/${BINARY_NAME}"
    finish
    exit 0
  elif command -v cargo >/dev/null 2>&1; then
    echo "Building release binary with cargo..."
    (cd "${SCRIPT_DIR}" && cargo build --release)
    install_file "${SCRIPT_DIR}/target/release/${BINARY_NAME}"
    finish
    exit 0
  else
    echo "No cargo / local binary — falling back to GitHub Release download."
  fi
fi

# ── Mode B: GitHub Release binary ────────────────────────────────────────
need_curl

TARGETS=()
while IFS= read -r target; do
  TARGETS+=("$target")
done < <(detect_targets) || true
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "Error: unsupported platform: $(uname -s) $(uname -m)"
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ARCHIVE="${TMP_DIR}/grok-tokens.tar.gz"
EXTRACT_DIR="${TMP_DIR}/extract"

download_ok=0

try_url() {
  local url="$1"
  echo "Trying ${url} ..."
  if ! curl -fsSL -o "$ARCHIVE" "$url"; then
    return 1
  fi

  if [[ "$url" == *.tar.gz ]]; then
    mkdir -p "$EXTRACT_DIR"
    rm -rf "${EXTRACT_DIR:?}/"*
    if ! tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR" 2>/dev/null; then
      return 1
    fi
    local bin
    bin="$(find "$EXTRACT_DIR" -type f -name "${BINARY_NAME}" | head -1)"
    if [[ -n "$bin" && -f "$bin" ]]; then
      install_file "$bin"
      return 0
    fi
    return 1
  fi

  if looks_like_binary "$ARCHIVE"; then
    install_file "$ARCHIVE"
    return 0
  fi
  return 1
}

# /releases/latest/download follows the newest tag without calling the API.
for target in "${TARGETS[@]}"; do
  for name in \
    "grok-tokens-${target}.tar.gz" \
    "grok-tokens-${target}"
  do
    if try_url "https://github.com/${REPO_SLUG}/releases/latest/download/${name}"; then
      download_ok=1
      break 2
    fi
  done
done

if [[ "$download_ok" -ne 1 ]]; then
  echo "Download failed: no native binary for this platform."
  echo "Wait for the GitHub Release assets, or build from source:"
  echo "  cargo install --git https://github.com/${REPO_SLUG} --locked"
  echo "  git clone https://github.com/${REPO_SLUG}.git && cd grok-tokens && ./install.sh"
  exit 1
fi

finish
echo "Done."
