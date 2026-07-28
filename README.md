# grok-tokens

Standalone CLI to sum **real Grok Build usage tokens** from local session logs
(`usage.inputTokens` / `outputTokens` / `cachedReadTokens` / …).

Not the same as peak-context tools: this aggregates per-turn usage.

## Layout

```text
~/git/grok-tokens/
  grok_tokens.py     # implementation
  bin/grok-tokens    # executable entry (PATH target)
  install.sh         # symlink into ~/.local/bin
  README.md
```

## Install

```bash
cd ~/git/grok-tokens
./install.sh
# ensures: ~/.local/bin/grok-tokens -> ~/git/grok-tokens/bin/grok-tokens
```

Or run from the checkout without installing:

```bash
./bin/grok-tokens daily
python3 grok_tokens.py daily
```

## Usage

```bash
grok-tokens daily
grok-tokens daily --cwd /path/to/project
grok-tokens daily -v                 # Log$ + cache Saved$
grok-tokens daily --no-color
grok-tokens daily --json

grok-tokens session --usage-only
grok-tokens session --sort recent
```

| Flag | Description |
|------|-------------|
| `--cwd PATH` | Only sessions for this project directory |
| `--since YYYY-MM-DD` | Filter (UTC) |
| `--limit N` | Max session dirs (default 200) |
| `--root DIR` | Override sessions root |
| `--json` | Full integers, machine-readable |
| `--usage-only` | Hide sessions with no usage events |
| `-v` / `--verbose` | Show Log$ and cache Saved$ |
| `--no-color` | Disable ANSI colors |

Data root: `$GROK_DATA_DIR` → `$GROK_HOME/sessions` → `~/.grok/sessions`.

## Columns

| Column | Meaning |
|--------|---------|
| **Input** | Sum of `inputTokens` |
| **Cache** | Sum of `cachedReadTokens` (⊂ Input) |
| **Hit%** | Cache / Input |
| **Fresh** | Input − Cache |
| **Output** | Sum of `outputTokens` |
| **Total** | ≈ Input + Output |
| **NoCache** | Fresh + Output |
| **Cost** | Public list price with cache discount |
| **Log$** (`-v`) | `costUsdTicks/1e9` (CLI-internal) |
| **Saved** (`-v`) | Savings vs full-price input |

## Development

```bash
cd ~/git/grok-tokens
# edit grok_tokens.py
./bin/grok-tokens daily --no-color   # test immediately (no reinstall)
```

`~/.local/bin/grok-tokens` is a symlink into this repo, so edits apply live.

## License

MIT (same spirit as typical small CLI tools).
