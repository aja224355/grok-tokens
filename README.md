# grok-tokens

**Grok Build token usage CLI** — accurate per-turn totals from local session logs.

Single portable executable. **Python 3.8+ stdlib only** (no `pip`, no `cargo`, no glibc binary matrix).

Inspired by the install UX of [grok-usage](https://github.com/simnova/grok-usage), but focused on **real usage tokens** (`input` / `output` / `cache`) instead of peak-context snapshots.

---

## Why this exists

Grok Build logs two different things:

| Source | What it is | Good for |
|--------|------------|----------|
| `_meta.totalTokens` | Cumulative **context size** snapshots | Peak window / growth |
| `usage.inputTokens` etc. | Per-turn **API usage** | Token counts & cost estimate |

`grok-tokens` reads **`usage.*`** and reports:

- Input / Cache hit / Fresh / Output / Total  
- **NoCache** = Fresh + Output  
- **Cost** = public list price with cache discount (+ long-context tier)

---

## Install (one line)

> Replace `alientek` with your GitHub user/org if the repo is under another name.

```bash
curl -fsSL https://raw.githubusercontent.com/alientek/grok-tokens/main/install.sh | bash
```

Or pin a fork:

```bash
GROK_TOKENS_REPO=yourname/grok-tokens \
  curl -fsSL https://raw.githubusercontent.com/yourname/grok-tokens/main/install.sh | bash
```

This installs:

```text
~/.local/bin/grok-tokens
```

Add to `PATH` if needed:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Verify:

```bash
grok-tokens --version
grok-tokens daily
```

### What the installer does

1. Requires **python3 ≥ 3.8**
2. Downloads the **release asset** `grok-tokens` (versioned tag), **or**
3. Falls back to `grok_tokens.py` from the `main` branch
4. Installs a single executable into `~/.local/bin`

No platform-specific native binary — one file works on Linux / macOS / WSL as long as `python3` is present (avoids the glibc / wrong asset-name issues common with prebuilt ELF downloads).

### Dev install (from a git clone)

```bash
git clone https://github.com/alientek/grok-tokens.git
cd grok-tokens
./install.sh
# ~/.local/bin/grok-tokens → runs this checkout live
```

---

## Usage

```bash
# By UTC day
grok-tokens daily
grok-tokens daily --cwd /path/to/project
grok-tokens daily --since 2026-07-27
grok-tokens daily -v                 # Log$ + cache Saved$
grok-tokens daily --json

# Per session
grok-tokens session
grok-tokens session --usage-only
grok-tokens session --sort recent
```

### Options

| Flag | Description |
|------|-------------|
| `--cwd PATH` | Only sessions for this project directory |
| `--since YYYY-MM-DD` | Filter (UTC) |
| `--limit N` | Max session dirs to scan (default 200) |
| `--root DIR` | Override sessions root |
| `--json` | Full integers, machine-readable |
| `--usage-only` | Hide sessions with no usage events |
| `-v` / `--verbose` | Show Log$ (CLI internal) and cache Saved$ |
| `--no-color` | Disable ANSI colors |
| `-V` / `--version` | Print version |

### Data location

```text
$GROK_DATA_DIR
  or $GROK_HOME/sessions
  or ~/.grok/sessions
```

---

## Columns

| Column | Meaning |
|--------|---------|
| **Input** | Σ `inputTokens` |
| **Cache** | Σ `cachedReadTokens` (subset of Input) |
| **Hit%** | Cache / Input |
| **Fresh** | Input − Cache |
| **Output** | Σ `outputTokens` |
| **Total** | ≈ Input + Output |
| **NoCache** | Fresh + Output |
| **Cost** | Public list price with cache discount |
| **Log$** (`-v`) | `costUsdTicks / 1e9` (Grok CLI internal units) |
| **Saved** (`-v`) | Savings vs billing full Input at input rate |

### Cost formula (list price)

```text
fresh = inputTokens - cachedReadTokens
Cost  = fresh * input_rate + cached * cached_rate + output * output_rate
```

Default rates follow [xAI public pricing](https://docs.x.ai/developers/pricing) for **grok-4.5** (short vs ≥200k long-context tier).  
`reasoningTokens` is already included in `outputTokens` in Grok Build logs — not double-counted.

**Cost is an estimate, not a final invoice.** Subscription / seat pricing may differ.  
**Log$** is whatever the Grok Build CLI stored in `costUsdTicks` (empirically ~5× always-long list rates).

---

## Publish to GitHub (maintainers)

```bash
# 1. Create empty repo on GitHub: yourname/grok-tokens
# 2. Point install.sh + README REPO_SLUG at that name (default: alientek/grok-tokens)

git remote add origin git@github.com:yourname/grok-tokens.git
git push -u origin main

# 3. Cut a release (triggers .github/workflows/release.yml)
git tag v0.1.0
git push origin v0.1.0
```

Release workflow uploads a single asset named **`grok-tokens`** (the Python script).  
Users then install with the one-liner above.

Update version in `grok_tokens.py` (`__version__`) when tagging.

---

## Development

```bash
cd ~/git/grok-tokens   # or your clone
# edit grok_tokens.py
./bin/grok-tokens daily --no-color
python3 grok_tokens.py session --usage-only
```

Layout:

```text
grok_tokens.py              # implementation (also the release asset)
bin/grok-tokens             # local launcher
install.sh                  # one-liner + local install
.github/workflows/ci.yml
.github/workflows/release.yml
```

---

## License

MIT — see [LICENSE](LICENSE).
