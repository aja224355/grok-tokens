# grok-tokens

**Grok Build token usage CLI** — accurate per-turn totals from local session logs.

Native **Rust** binary (preferred) + **pure Python** fallback.  
Inspired by the install UX of [grok-usage](https://github.com/simnova/grok-usage).

| Stack | Role |
|-------|------|
| **Rust** | Default executable — no interpreter, musl Linux builds avoid glibc issues |
| **Python 3.8+** | Portable fallback (`grok_tokens.py`, stdlib only) |

Same CLI surface and metrics on both implementations.

---

## Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/alientek/grok-tokens/main/install.sh | bash
```

Override repo:

```bash
GROK_TOKENS_REPO=yourname/grok-tokens \
  curl -fsSL https://raw.githubusercontent.com/yourname/grok-tokens/main/install.sh | bash
```

Installs to `~/.local/bin/grok-tokens`.

```bash
export PATH="$HOME/.local/bin:$PATH"
grok-tokens --version
grok-tokens daily
```

### What install.sh does

1. **Checkout present** → `cargo build --release` (or use existing `target/release`)
2. Else **GitHub Release** → download `grok-tokens-<target>.tar.gz`  
   - Linux: **`x86_64-unknown-linux-musl`** / `aarch64-unknown-linux-musl` (static-friendly)
   - macOS: `aarch64-apple-darwin` / `x86_64-apple-darwin`
3. Else **Python** asset / raw `grok_tokens.py` from `main`

### Manual

```bash
# Rust (from source)
cargo install --git https://github.com/alientek/grok-tokens --locked

# Or clone
git clone https://github.com/alientek/grok-tokens.git
cd grok-tokens
cargo build --release
./install.sh

# Python only
python3 grok_tokens.py daily
```

---

## Usage

```bash
grok-tokens daily
grok-tokens daily --cwd /path/to/project
grok-tokens daily --since 2026-07-27 -v
grok-tokens daily --json

grok-tokens session --usage-only
grok-tokens session --sort recent
```

| Flag | Description |
|------|-------------|
| `--cwd PATH` | Filter by project directory |
| `--since YYYY-MM-DD` | UTC date filter |
| `--limit N` | Max sessions scanned (default 200) |
| `--root DIR` | Sessions root override |
| `--json` | Machine-readable |
| `--usage-only` | Hide empty sessions |
| `-v` | Log$ + cache Saved$ |
| `--no-color` | Disable colors |

Data: `$GROK_DATA_DIR` → `$GROK_HOME/sessions` → `~/.grok/sessions`.

---

## Columns

| Column | Meaning |
|--------|---------|
| **Input** | Σ `inputTokens` |
| **Cache** | Σ `cachedReadTokens` (⊂ Input) |
| **Hit%** | Cache / Input |
| **Fresh** | Input − Cache |
| **Output** | Σ `outputTokens` |
| **Total** | ≈ Input + Output |
| **NoCache** | Fresh + Output |
| **Cost** | Public list price + cache discount + long-context tier |
| **Log$** (`-v`) | `costUsdTicks/1e9` (CLI internal) |
| **Saved** (`-v`) | vs full-price input |

```text
fresh = input - cachedRead
Cost  = fresh×input_rate + cached×cached_rate + output×output_rate
```

Rates follow [xAI pricing](https://docs.x.ai/developers/pricing) for grok-4.5 (and friends). Estimates only.

---

## Why Rust + Python

- **Rust musl**: one static-friendly Linux binary — avoids the “prebuilt needs GLIBC 2.3x” trap.
- **Python fallback**: any machine with `python3`, no compiler.
- **Same numbers**: both read `usage.*` the same way.

---

## Publish (maintainers)

```bash
# bump version in Cargo.toml + grok_tokens.py __version__
git tag v0.1.0
git push origin v0.1.0
# Actions builds musl/gnu/mac tarballs + grok-tokens.py and attaches to the Release
```

---

## Development

```bash
cargo run -- daily --no-color
cargo run -- session --usage-only
python3 grok_tokens.py daily --no-color   # parity check
cargo build --release
./install.sh
```

```text
src/main.rs           # Rust CLI
grok_tokens.py        # Python fallback (parity)
install.sh            # dual-stack installer
.github/workflows/    # CI + multi-target release
```

## License

MIT — see [LICENSE](LICENSE).
