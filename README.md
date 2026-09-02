# grok-tokens

**Grok Build token usage CLI** — accurate per-turn totals from local session logs.

Native **Rust** binary. musl Linux builds avoid glibc issues.  
Inspired by the install UX of [grok-usage](https://github.com/simnova/grok-usage).

`grok_tokens.py` is **deprecated** (no new features; will be removed). Use the Rust binary.

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

### Manual

```bash
# Rust (from source)
cargo install --git https://github.com/alientek/grok-tokens --locked

# Or clone
git clone https://github.com/alientek/grok-tokens.git
cd grok-tokens
cargo build --release
./install.sh
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

# Account quota (same as Grok CLI /usage "Weekly limit: 24%")
grok-tokens limit

# Local login profiles (Grok CLI itself has no multi-account switch)
grok-tokens account whoami
grok-tokens account save              # name = email local-part
grok-tokens account save work
grok-tokens account list
grok-tokens account switch work
```

Account limit is read from `~/.grok/logs/unified.jsonl` (`billing: fetched credits config`), which the Grok CLI refreshes while sessions run. It is **not** derived from local token sums.

`daily` / `session` / `limit` label that quota with the current `auth.json` email (and profile name, if saved). With multiple saved profiles, they list each account’s last-known limit. Local token totals stay machine-wide — session logs are not tagged by account.

### Account profiles

Grok stores a single login in `~/.grok/auth.json`. `account` snapshots that file as named profiles (like `gcloud auth` / AWS profiles):

| Command | Action |
|---------|--------|
| `account whoami` | Current `email` / `user_id` |
| `account save [name]` | Copy live `auth.json` to a profile |
| `account list` | Saved profiles (`*` = matches live login) |
| `account switch <name>` | Atomically replace `auth.json` |
| `account remove <name>` | Delete a snapshot (does not log out) |

Profiles: `$GROK_TOKENS_PROFILES` → `$XDG_DATA_HOME/grok-tokens/accounts` → `~/.local/share/grok-tokens/accounts`. Auth file: `$GROK_AUTH_PATH` → `$GROK_HOME/auth.json` → `~/.grok/auth.json`.

`switch` first writes the live login back to the matching profile (so refreshed tokens are not lost). Grok picks up the new file on the next API call; restart a running session if it does not.

Do **not** copy profile directories into git or chat — they contain refresh tokens.


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

## Why Rust

**musl** Linux binaries are static-friendly — avoids the “prebuilt needs GLIBC 2.3x” trap. One executable, no interpreter.

---

## Publish (maintainers)

```bash
# bump version in Cargo.toml
git tag v0.1.0
git push origin v0.1.0
# Actions builds musl/gnu/mac tarballs and attaches them to the Release
```

---

## Development

```bash
cargo run -- daily --no-color
cargo run -- session --usage-only
cargo build --release
./install.sh
```

```text
src/main.rs           # Rust CLI
install.sh            # installer (native binary)
.github/workflows/    # CI + multi-target release
grok_tokens.py        # deprecated; do not extend
```

## License

MIT — see [LICENSE](LICENSE).
