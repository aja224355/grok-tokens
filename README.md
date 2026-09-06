# grok-tokens

**Grok Build token usage CLI** — accurate per-turn totals from local session logs.

Native **Rust** binary. musl Linux builds avoid glibc issues.  
Inspired by the install UX of [grok-usage](https://github.com/simnova/grok-usage).

`grok_tokens.py` is **deprecated** (no new features; will be removed). Use the Rust binary.

---

## Install (other computers)

No Rust, no git clone. The installer downloads a native binary from GitHub Releases.

**Linux / macOS / WSL:**

```bash
curl -fsSL https://github.com/aja224355/grok-tokens/releases/latest/download/install.sh | sh
```

If `raw.githubusercontent.com` works better on that machine:

```bash
curl -fsSL https://cdn.jsdelivr.net/gh/aja224355/grok-tokens@main/install.sh | sh
```

**Windows (PowerShell):**

```powershell
irm https://github.com/aja224355/grok-tokens/releases/latest/download/install.ps1 | iex
```

Installs to `~/.local/bin/grok-tokens` (Unix) or `%LOCALAPPDATA%\grok-tokens\grok-tokens.exe` (Windows). The script retries public GitHub mirrors if github.com is slow.

```bash
export PATH="$HOME/.local/bin:$PATH"
grok-tokens --version
grok-tokens daily
```

Env vars must be on the **right** side of the pipe:

```bash
curl -fsSL https://github.com/aja224355/grok-tokens/releases/latest/download/install.sh \
  | GROK_TOKENS_REPO=yourname/grok-tokens sh
```

### What the installer does

1. **`curl | sh` / `irm | iex`** → GitHub Release asset for this OS/arch  
   - Linux: **`x86_64-unknown-linux-musl`** / `aarch64-unknown-linux-musl` (gnu fallback)
   - macOS: `aarch64-apple-darwin` / `x86_64-apple-darwin`
   - Windows: `x86_64-pc-windows-msvc`
2. **`./install.sh` in a clone** → `cargo build --release` (or existing `target/release`)

Force a Release download from a clone: `GROK_TOKENS_FORCE_DOWNLOAD=1 ./install.sh`

### Manual

Download a tarball from [Releases](https://github.com/aja224355/grok-tokens/releases/latest) and copy `grok-tokens` onto `PATH`.

```bash
# Rust (from source)
cargo install --git https://github.com/aja224355/grok-tokens --locked

# Or clone
git clone https://github.com/aja224355/grok-tokens.git
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
| `account export [name] -o FILE` | Write a portable file (auth + metadata) |
| `account import FILE [--name] [--no-switch]` | Load that file; default also replaces live `auth.json` |

Profiles: `$GROK_TOKENS_PROFILES` → `$XDG_DATA_HOME/grok-tokens/accounts` → `~/.local/share/grok-tokens/accounts`. Auth file: `$GROK_AUTH_PATH` → `$GROK_HOME/auth.json` → `~/.grok/auth.json`.

`switch` first writes the live login back to the matching profile (so refreshed tokens are not lost). Grok picks up the new file on the next API call; restart a running session if it does not.

Do **not** copy profile directories or export files into git or chat — they contain refresh tokens.

### WSL ↔ Windows (no re-login)

Export on the side that is already signed in, copy the file, import on the other. Same file also works as raw `auth.json`.

```bash
# In WSL (already logged in)
grok-tokens account export -o /mnt/c/Users/YOU/grok-account.json

# Point at the Windows Grok home and import (still from WSL — no Win32 binary needed)
GROK_HOME="/mnt/c/Users/YOU/.grok" grok-tokens account import /mnt/c/Users/YOU/grok-account.json
```

Or copy the file to Windows and import with the native binary:

```powershell
grok-tokens account import $env:USERPROFILE\grok-account.json
```

Grok CLI on that side picks up `auth.json` on the next API call. Refresh tokens expire; if import is rejected, run `grok login` once on that side.


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
git tag v0.1.1
git push origin v0.1.1
# Actions builds linux/mac/windows tarballs and attaches install.sh + install.ps1
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
install.sh            # Unix installer (curl | sh)
install.ps1           # Windows installer (irm | iex)
.github/workflows/    # CI + multi-target release
grok_tokens.py        # deprecated; do not extend
```

## License

MIT — see [LICENSE](LICENSE).
