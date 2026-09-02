#!/usr/bin/env python3
"""
DEPRECATED. Use the Rust `grok-tokens` binary. This script is frozen and
will be removed; new commands (including `account`) are Rust-only.

grok-tokens — count real Grok Build usage tokens from local session logs.

Pure Python 3 (stdlib only). Do not add features here.

Unlike peak-context tools that sum cumulative _meta.totalTokens, this tool
sums per-turn usage fields:

  usage.inputTokens / outputTokens / totalTokens
  usage.cachedReadTokens / reasoningTokens / costUsdTicks

Commands:
  daily    Aggregate by UTC calendar day
  session  Per-session totals

Examples:
  grok-tokens daily
  grok-tokens session --limit 20
  grok-tokens daily --since 2026-07-27 --json
  grok-tokens session --cwd /path/to/project
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

__version__ = "0.1.0"
VERSION = __version__


# ---------------------------------------------------------------------------
# Pricing (public xAI API rates, USD per 1M tokens)
# https://docs.x.ai/developers/pricing
#
# Cached read is a *subset* of inputTokens (not extra). Correct bill is:
#   fresh = input - cachedRead
#   cost  = fresh * input_rate + cached * cached_rate + output * output_rate
#
# reasoningTokens is already included in outputTokens in Grok Build logs
# (output >= reasoning always), so we do NOT add it again.
#
# costUsdTicks in logs: empirically ≈ 5 × always-long-context grok-4.5 rates
# expressed in nano-USD (ticks / 1e9). That is CLI-internal, not list price.
# ---------------------------------------------------------------------------

# model_id substring → (short_rates, long_rates, long_threshold)
# each rates tuple: (input, cached_input, output) $/MTok
PRICE_TABLE = {
    "grok-4.5": ((2.0, 0.3, 6.0), (4.0, 0.6, 12.0), 200_000),
    "grok-4.5-build": ((2.0, 0.3, 6.0), (4.0, 0.6, 12.0), 200_000),
    "grok-build-0.1": ((1.0, 0.2, 2.0), (2.0, 0.4, 4.0), 200_000),
    "grok-4.3": ((1.25, 0.2, 2.5), (2.5, 0.4, 5.0), 200_000),
}
DEFAULT_RATES = PRICE_TABLE["grok-4.5"]


def rates_for_model(model: str) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], int]:
    m = (model or "").lower()
    for key, val in PRICE_TABLE.items():
        if key in m:
            return val
    return DEFAULT_RATES


def estimate_api_cost_usd(u: dict) -> float:
    """
    Public-API-style estimate for one usage event.
    Applies cache discount and long-context tier based on prompt size (inputTokens).
    """
    inp = int(u.get("inputTokens") or 0)
    out = int(u.get("outputTokens") or 0)
    cache = int(u.get("cachedReadTokens") or 0)
    fresh = max(0, inp - cache)

    model = "grok-4.5"
    mu = u.get("modelUsage")
    if isinstance(mu, dict) and mu:
        model = next(iter(mu.keys()))

    short, long, threshold = rates_for_model(model)
    rin, rcache, rout = long if inp >= threshold else short
    return (fresh * rin + cache * rcache + out * rout) / 1_000_000.0


def estimate_api_cost_no_cache_usd(u: dict) -> float:
    """Naive (wrong) estimate: charge full input at input rate — no cache discount."""
    inp = int(u.get("inputTokens") or 0)
    out = int(u.get("outputTokens") or 0)
    model = "grok-4.5"
    mu = u.get("modelUsage")
    if isinstance(mu, dict) and mu:
        model = next(iter(mu.keys()))
    short, long, threshold = rates_for_model(model)
    rin, _rcache, rout = long if inp >= threshold else short
    return (inp * rin + out * rout) / 1_000_000.0


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def grok_home() -> Path:
    home = os.environ.get("GROK_HOME", "").strip()
    if home:
        return Path(home)
    return Path.home() / ".grok"


def sessions_root() -> Path:
    env = os.environ.get("GROK_DATA_DIR", "").strip()
    if env:
        return Path(env)
    p = grok_home() / "sessions"
    if p.is_dir():
        return p
    return Path.home() / ".grok" / "sessions"


# ---------------------------------------------------------------------------
# Account quota (Weekly/Monthly limit from Grok CLI /usage)
# ---------------------------------------------------------------------------
# Grok CLI fetches account credits and logs:
#   msg = "billing: fetched credits config"
#   ctx.config.creditUsagePercent + currentPeriod.type WEEKLY|MONTHLY
# This is the same source as the TUI `/usage` "Weekly limit: 24%" line.
# Live HTTP endpoint is internal (`/billing?format=credits`); we read the
# latest successful fetch from ~/.grok/logs/unified.jsonl (updated often
# while the CLI runs).


@dataclass
class AccountLimit:
    percent: Optional[float]
    period_label: str  # Weekly / Monthly / Period
    period_type: str
    period_start: Optional[str]
    period_end: Optional[str]
    subscription: Optional[str]
    fetched_at: Optional[str]
    source: str = "log"

    def headline(self) -> str:
        if self.percent is None:
            return f"{self.period_label} limit: n/a"
        # Match Grok TUI: "Weekly limit: 24%"
        pct = int(self.percent) if float(self.percent).is_integer() else self.percent
        return f"{self.period_label} limit: {pct}%"

    def as_public_dict(self) -> dict:
        return {
            "percent": self.percent,
            "period_label": self.period_label,
            "period_type": self.period_type,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "subscription": self.subscription,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "headline": self.headline(),
        }


def load_account_limit(max_bytes: int = 4_000_000) -> Optional[AccountLimit]:
    """Latest account quota from Grok CLI billing log (same as /usage)."""
    log = grok_home() / "logs" / "unified.jsonl"
    if not log.is_file():
        # Weak fallback: last clipboard text from TUI copy
        lc = grok_home() / "last-copy.txt"
        if lc.is_file():
            text = lc.read_text(encoding="utf-8", errors="replace").strip()
            m = re.search(
                r"(Weekly|Monthly)\s+limit:\s*([0-9]+(?:\.[0-9]+)?)\s*%",
                text,
                re.I,
            )
            if m:
                return AccountLimit(
                    percent=float(m.group(2)),
                    period_label=m.group(1).capitalize(),
                    period_type="",
                    period_start=None,
                    period_end=None,
                    subscription=None,
                    fetched_at=None,
                    source="last-copy",
                )
        return None

    try:
        size = log.stat().st_size
        with log.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    last: Optional[dict] = None
    for line in data.splitlines():
        if "billing: fetched credits config" not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("msg") != "billing: fetched credits config":
            continue
        last = obj
    if not last:
        return None

    ctx = last.get("ctx") or {}
    cfg = ctx.get("config") or {}
    period = cfg.get("currentPeriod") or {}
    ptype = str(period.get("type") or "")
    if "WEEKLY" in ptype.upper():
        label = "Weekly"
    elif "MONTHLY" in ptype.upper():
        label = "Monthly"
    else:
        label = "Period"
    pct = cfg.get("creditUsagePercent")
    try:
        pct_f = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct_f = None
    sub = ctx.get("subscriptionTiers") or ctx.get("subscription_tier") or cfg.get(
        "subscription_tier"
    )
    if isinstance(sub, list):
        sub = ",".join(str(x) for x in sub)
    return AccountLimit(
        percent=pct_f,
        period_label=label,
        period_type=ptype,
        period_start=period.get("start") or cfg.get("billingPeriodStart"),
        period_end=period.get("end") or cfg.get("billingPeriodEnd"),
        subscription=str(sub) if sub else None,
        fetched_at=last.get("ts"),
        source="unified.jsonl",
    )


def discover_sessions(root: Path, limit: int) -> List[Path]:
    """Return session dirs that contain updates.jsonl, newest first."""
    found: List[Tuple[float, Path]] = []
    if not root.is_dir():
        return []
    for dirpath, _dirnames, filenames in os.walk(root):
        if "updates.jsonl" not in filenames:
            continue
        p = Path(dirpath)
        try:
            mtime = (p / "updates.jsonl").stat().st_mtime
        except OSError:
            mtime = 0.0
        found.append((mtime, p))
    found.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in found[:limit]]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _walk_usage_dicts(obj: Any, out: List[dict]) -> None:
    """Collect dicts that look like Grok usage payloads (top-level preferred)."""
    if isinstance(obj, dict):
        if (
            "inputTokens" in obj
            and "outputTokens" in obj
            and "totalTokens" in obj
        ):
            out.append(obj)
            # Do not double-count nested modelUsage.* — skip children of usage-like dicts
            for k, v in obj.items():
                if k == "modelUsage":
                    continue
                _walk_usage_dicts(v, out)
            return
        for v in obj.values():
            _walk_usage_dicts(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _walk_usage_dicts(x, out)


def _agent_ts_ms(obj: dict) -> Optional[int]:
    params = obj.get("params") or {}
    meta = params.get("_meta") or {}
    ts = meta.get("agentTimestampMs")
    if ts is not None:
        try:
            return int(ts)
        except (TypeError, ValueError):
            pass
    upd = params.get("update") or {}
    umeta = upd.get("_meta") or {}
    ts = umeta.get("agentTimestampMs")
    if ts is not None:
        try:
            return int(ts)
        except (TypeError, ValueError):
            pass
    return None


def _ms_to_day(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return "unknown"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date().isoformat()


def _ms_to_rfc3339(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return "unknown"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


def _read_cwd(session_dir: Path) -> str:
    summary = session_dir / "summary.json"
    if summary.is_file():
        try:
            data = json.loads(summary.read_text(encoding="utf-8", errors="replace"))
            info = data.get("info") or {}
            cwd = info.get("cwd") or data.get("cwd") or ""
            if cwd:
                return str(cwd)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    # Parent dir name is often URL-encoded cwd: %2Fhome%2F...
    parent = session_dir.parent.name
    if parent.startswith("%"):
        try:
            from urllib.parse import unquote

            return unquote(parent)
        except Exception:
            pass
    return ""


@dataclass
class TokenBucket:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_read_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd_ticks: int = 0
    # Recomputed from token breakdown with public rates + cache discount
    cost_api_usd: float = 0.0
    # Same but charging full input (no cache discount) — for comparison only
    cost_api_no_cache_usd: float = 0.0
    events: int = 0
    model_calls: int = 0
    models: Set[str] = field(default_factory=set)

    def add_usage(self, u: dict) -> None:
        self.input_tokens += int(u.get("inputTokens") or 0)
        self.output_tokens += int(u.get("outputTokens") or 0)
        self.total_tokens += int(u.get("totalTokens") or 0)
        self.cached_read_tokens += int(u.get("cachedReadTokens") or 0)
        self.reasoning_tokens += int(u.get("reasoningTokens") or 0)
        self.cost_usd_ticks += int(u.get("costUsdTicks") or 0)
        self.cost_api_usd += estimate_api_cost_usd(u)
        self.cost_api_no_cache_usd += estimate_api_cost_no_cache_usd(u)
        self.events += 1
        self.model_calls += int(u.get("modelCalls") or 0)
        mu = u.get("modelUsage")
        if isinstance(mu, dict) and mu:
            for m in mu:
                self.models.add(str(m))
        else:
            self.models.add("unknown")

    @property
    def fresh_input_tokens(self) -> int:
        """inputTokens minus cached hits (non-cache billable input)."""
        return max(0, self.input_tokens - self.cached_read_tokens)

    @property
    def total_without_cache(self) -> int:
        """Fresh input + output (excludes cached input tokens)."""
        return self.fresh_input_tokens + self.output_tokens

    @property
    def cost_cli_usd(self) -> float:
        """
        costUsdTicks / 1e9 — CLI-reported internal dollars.
        Empirically ≈ 5 × always-long-context grok-4.5 list rates (not public API bill).
        """
        return self.cost_usd_ticks / 1_000_000_000.0

    # Back-compat alias used by older display code
    @property
    def cost_usd_approx(self) -> float:
        return self.cost_cli_usd

    @property
    def cache_savings_usd(self) -> float:
        """How much the cache discount saves vs billing all input at full rate."""
        return max(0.0, self.cost_api_no_cache_usd - self.cost_api_usd)

    def as_public_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_read_tokens": self.cached_read_tokens,
            "fresh_input_tokens": self.fresh_input_tokens,
            "total_without_cache": self.total_without_cache,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd_ticks": self.cost_usd_ticks,
            "cost_cli_usd": round(self.cost_cli_usd, 4),
            "cost_api_usd": round(self.cost_api_usd, 4),
            "cost_api_no_cache_usd": round(self.cost_api_no_cache_usd, 4),
            "cache_savings_usd": round(self.cache_savings_usd, 4),
            "events": self.events,
            "model_calls": self.model_calls,
            "models": sorted(self.models),
        }


@dataclass
class SessionStat:
    session_id: str
    path: str
    cwd: str
    tokens: TokenBucket = field(default_factory=TokenBucket)
    last_activity: str = "unknown"
    last_activity_day: str = "unknown"
    # Best peak-context proxy: max usage.inputTokens (request-time context size)
    peak_input_tokens: int = 0
    meta_peak_context: int = 0
    meta_final_context: int = 0
    meta_first_context: int = 0
    has_usage: bool = False

    @property
    def peak_context(self) -> int:
        """Prefer max inputTokens; fall back to _meta.totalTokens snapshots."""
        return max(self.peak_input_tokens, self.meta_peak_context)

    def as_public_dict(self) -> dict:
        d = {
            "session_id": self.session_id,
            "path": self.path,
            "cwd": self.cwd,
            "last_activity": self.last_activity,
            "last_activity_day": self.last_activity_day,
            "peak_context": self.peak_context,
            "peak_input_tokens": self.peak_input_tokens,
            "meta_peak_context": self.meta_peak_context,
            "meta_final_context": self.meta_final_context,
            "meta_first_context": self.meta_first_context,
            "has_usage": self.has_usage,
        }
        d.update(self.tokens.as_public_dict())
        return d


def load_session(path: Path) -> Optional[SessionStat]:
    updates = path / "updates.jsonl"
    if not updates.is_file():
        return None

    bucket = TokenBucket()
    last_ts: Optional[int] = None
    meta_vals: List[int] = []
    peak_input = 0

    try:
        with updates.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                ts = _agent_ts_ms(obj)
                if ts is not None:
                    last_ts = ts if last_ts is None else max(last_ts, ts)

                # Context-window snapshots (for reference only; often underestimates)
                try:
                    mt = (obj.get("params") or {}).get("_meta") or {}
                    tot = mt.get("totalTokens")
                    if isinstance(tot, int):
                        meta_vals.append(tot)
                except Exception:
                    pass

                if "inputTokens" not in line and "usage" not in line:
                    continue

                found: List[dict] = []
                _walk_usage_dicts(obj, found)
                # Prefer full usage records (with cost or modelUsage)
                top = [u for u in found if "costUsdTicks" in u or "modelUsage" in u]
                if not top:
                    top = found
                seen: Set[Tuple] = set()
                for u in top:
                    key = (
                        u.get("inputTokens"),
                        u.get("outputTokens"),
                        u.get("totalTokens"),
                        u.get("cachedReadTokens"),
                        u.get("costUsdTicks"),
                        u.get("modelCalls"),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    peak_input = max(peak_input, int(u.get("inputTokens") or 0))
                    bucket.add_usage(u)
    except OSError:
        return None

    sid = path.name
    meta_peak = max(meta_vals) if meta_vals else 0
    meta_first = meta_vals[0] if meta_vals else 0
    meta_last = meta_vals[-1] if meta_vals else 0

    return SessionStat(
        session_id=sid,
        path=str(path),
        cwd=_read_cwd(path),
        tokens=bucket,
        last_activity=_ms_to_rfc3339(last_ts),
        last_activity_day=_ms_to_day(last_ts),
        peak_input_tokens=peak_input,
        meta_peak_context=meta_peak,
        meta_final_context=meta_last,
        meta_first_context=meta_first,
        has_usage=bucket.events > 0,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass
class DailyStat:
    date: str
    tokens: TokenBucket = field(default_factory=TokenBucket)
    sessions: Set[str] = field(default_factory=set)

    def as_public_dict(self) -> dict:
        d = {"date": self.date, "sessions": len(self.sessions)}
        d.update(self.tokens.as_public_dict())
        return d


def daily_from_raw(root: Path, session_dirs: List[Path], since: Optional[str]) -> List[DailyStat]:
    """
    Aggregate usage events by the calendar day of each usage line's timestamp.
    (More accurate than attributing entire session growth to last_activity day.)
    """
    by_day: Dict[str, DailyStat] = {}

    for path in session_dirs:
        updates = path / "updates.jsonl"
        if not updates.is_file():
            continue
        sid = path.name
        try:
            with updates.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "inputTokens" not in line and "usage" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    found: List[dict] = []
                    _walk_usage_dicts(obj, found)
                    top = [u for u in found if "costUsdTicks" in u or "modelUsage" in u]
                    if not top:
                        top = found
                    if not top:
                        continue
                    day = _ms_to_day(_agent_ts_ms(obj))
                    if since and day != "unknown" and day < since:
                        continue
                    if day not in by_day:
                        by_day[day] = DailyStat(date=day)
                    ds = by_day[day]
                    ds.sessions.add(sid)
                    seen: Set[Tuple] = set()
                    for u in top:
                        key = (
                            u.get("inputTokens"),
                            u.get("outputTokens"),
                            u.get("totalTokens"),
                            u.get("cachedReadTokens"),
                            u.get("costUsdTicks"),
                            u.get("modelCalls"),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        ds.tokens.add_usage(u)
        except OSError:
            continue

    return [by_day[k] for k in sorted(by_day.keys())]


def filter_sessions(
    stats: List[SessionStat],
    since: Optional[str],
    cwd: Optional[str],
    usage_only: bool,
) -> List[SessionStat]:
    out = stats
    if since:
        out = [
            s
            for s in out
            if s.last_activity_day == "unknown" or s.last_activity_day >= since
        ]
    if cwd:
        cwd_n = os.path.normpath(cwd)
        out = [
            s
            for s in out
            if s.cwd and os.path.normpath(s.cwd) == cwd_n
        ]
    if usage_only:
        out = [s for s in out if s.has_usage]
    return out


# ---------------------------------------------------------------------------
# Output (colors + tables)
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Force / disable via env or --no-color
_USE_COLOR: Optional[bool] = None


def _color_enabled() -> bool:
    if _USE_COLOR is not None:
        return _USE_COLOR
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") in ("1", "true", "yes"):
        return True
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    """Wrap text in ANSI SGR code when color is on."""
    if not _color_enabled() or not text:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def cyan(t: str) -> str:
    return _c("36", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def magenta(t: str) -> str:
    return _c("35", t)


def blue(t: str) -> str:
    return _c("34", t)


def white(t: str) -> str:
    return _c("37", t)


def _visible_len(s: str) -> int:
    return len(_ANSI_RE.sub("", s))


def _fmt_tokens(n: int) -> str:
    """
    Human-readable token counts for table output.
      < 1_000      → plain integer
      < 1_000_000  → K
      < 1e9        → M
      else         → B
    JSON keeps full integers.
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 1_000:
        return f"{sign}{n}"
    if n < 1_000_000:
        v = n / 1_000.0
        s = f"{v:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{s}K"
    if n < 1_000_000_000:
        v = n / 1_000_000.0
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{s}M"
    v = n / 1_000_000_000.0
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{sign}{s}B"


def _fmt_int(n: int) -> str:
    return _fmt_tokens(n)


def _fmt_money(x: float) -> str:
    return f"${x:.2f}"


def _cache_hit_pct(t: TokenBucket) -> float:
    if t.input_tokens <= 0:
        return 0.0
    return 100.0 * t.cached_read_tokens / t.input_tokens


def _sum_bucket(parts: List[TokenBucket]) -> TokenBucket:
    tot = TokenBucket()
    for t in parts:
        tot.input_tokens += t.input_tokens
        tot.output_tokens += t.output_tokens
        tot.total_tokens += t.total_tokens
        tot.cached_read_tokens += t.cached_read_tokens
        tot.reasoning_tokens += t.reasoning_tokens
        tot.cost_usd_ticks += t.cost_usd_ticks
        tot.cost_api_usd += t.cost_api_usd
        tot.cost_api_no_cache_usd += t.cost_api_no_cache_usd
        tot.events += t.events
        tot.model_calls += t.model_calls
        tot.models |= t.models
    return tot


def _pad(s: str, width: int, align: str = "left") -> str:
    vis = _visible_len(s)
    pad = max(0, width - vis)
    if align == "right":
        return " " * pad + s
    return s + " " * pad


def print_table(
    headers: List[str],
    rows: List[List[str]],
    *,
    aligns: Optional[List[str]] = None,
    header_color: str = "1;36",  # bold cyan
    total_row: bool = True,
) -> None:
    """
    Print a box-drawn table. Cells may already contain ANSI codes.
    Last row is treated as TOTAL (bold) when total_row=True and first cell is TOTAL.
    """
    if not headers:
        return
    n = len(headers)
    aligns = aligns or ["left"] + ["right"] * (n - 1)
    while len(aligns) < n:
        aligns.append("right")

    # Column content widths (visible chars, no ANSI)
    widths = [_visible_len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < n:
                widths[i] = max(widths[i], _visible_len(cell))

    # Box-drawing pieces (dim borders so data stays readable)
    # ╭─┬─╮  ┌─┬─┐ style: use rounded corners
    tl, tr, bl, br = "╭", "╮", "╰", "╯"
    h, v = "─", "│"
    t_down, t_up, t_left, t_right, cross = "┬", "┴", "┤", "├", "┼"

    def _hline(left: str, mid: str, right: str) -> str:
        parts = [h * (w + 2) for w in widths]  # +2 padding spaces
        return dim(left + mid.join(parts) + right)

    def _row_line(cells: List[str], *, emphasize: bool = False) -> str:
        out = []
        for i in range(n):
            cell = cells[i] if i < len(cells) else ""
            padded = _pad(cell, widths[i], aligns[i])
            if emphasize:
                padded = bold(padded)
            out.append(" " + padded + " ")
        return dim(v) + dim(v).join(out) + dim(v)

    # Top border
    print(_hline(tl, t_down, tr))
    # Header
    hcells = [_c(header_color, headers[i]) for i in range(n)]
    print(_row_line(hcells))
    # Header / body separator
    print(_hline(t_right, cross, t_left))

    for row in rows:
        is_total = (
            total_row
            and row
            and _ANSI_RE.sub("", row[0]).strip().upper() == "TOTAL"
        )
        if is_total:
            # separator above TOTAL
            print(_hline(t_right, cross, t_left))
        # ensure row has n cells
        cells = list(row) + [""] * max(0, n - len(row))
        print(_row_line(cells[:n], emphasize=is_total))

    # Bottom border
    print(_hline(bl, t_up, br))


def print_account_limit_banner(limit: Optional["AccountLimit"]) -> None:
    if not limit:
        return
    head = limit.headline()
    # color by pressure
    pct = limit.percent
    if pct is not None and pct >= 95:
        head_s = _c("1;31", head)  # bold red
    elif pct is not None and pct >= 80:
        head_s = yellow(bold(head))
    else:
        head_s = green(bold(head))
    bits = [head_s]
    if limit.period_end:
        bits.append(dim(f"reset {str(limit.period_end)[:10]}"))
    if limit.subscription:
        bits.append(cyan(str(limit.subscription)))
    if limit.fetched_at:
        bits.append(dim(f"as of {str(limit.fetched_at)[:19].replace('T', ' ')} UTC"))
    print("  ·  ".join(bits))
    print()


def print_limit_detail(limit: Optional[AccountLimit], as_json: bool) -> None:
    if as_json:
        print(json.dumps(limit.as_public_dict() if limit else None, indent=2))
        return
    if not limit:
        print(yellow("No account limit data found."))
        print(dim("Open Grok CLI and run /usage once, or keep a session running so billing is logged."))
        print(dim(f"Expected log: {grok_home() / 'logs' / 'unified.jsonl'}"))
        return
    print(bold(cyan("Grok Account Limit")) + dim("  ·  same source as /usage"))
    print()
    print_account_limit_banner(limit)
    rows = [
        ["Field", "Value"],
    ]
    # manual simple print without full table headers conflict
    print(f"  Limit        {limit.headline()}")
    print(f"  Period       {limit.period_label} ({limit.period_type or '—'})")
    print(f"  Start        {limit.period_start or '—'}")
    print(f"  End / reset  {limit.period_end or '—'}")
    print(f"  Plan         {limit.subscription or '—'}")
    print(f"  Fetched      {limit.fetched_at or '—'}")
    print(f"  Source       {limit.source}")
    print()
    print(dim("Note: This is account quota %, not local session token totals."))


def print_daily(
    dailies: List[DailyStat],
    as_json: bool,
    verbose: bool = False,
    account_limit: Optional[AccountLimit] = None,
) -> None:
    if as_json:
        payload = {
            "account_limit": account_limit.as_public_dict() if account_limit else None,
            "daily": [d.as_public_dict() for d in dailies],
        }
        print(json.dumps(payload, indent=2))
        return
    if not dailies:
        print(bold(cyan("Grok Tokens")) + dim("  ·  daily (UTC)"))
        print()
        print_account_limit_banner(account_limit)
        print(yellow("No usage events found."))
        return

    print(bold(cyan("Grok Tokens")) + dim("  ·  daily (UTC)"))
    print()
    print_account_limit_banner(account_limit)

    headers = [
        "Date",
        "Reqs",
        "Sess",
        "Input",
        "Cache",
        "Hit%",
        "Fresh",
        "Output",
        "Total",
        "NoCache",
        "Cost",
    ]
    if verbose:
        headers += ["Log$", "Saved"]

    rows: List[List[str]] = []
    for d in dailies:
        t = d.tokens
        hit = _cache_hit_pct(t)
        row = [
            cyan(d.date),
            str(t.events),
            str(len(d.sessions)),
            green(_fmt_tokens(t.input_tokens)),
            dim(_fmt_tokens(t.cached_read_tokens)),
            dim(f"{hit:.0f}%"),
            yellow(_fmt_tokens(t.fresh_input_tokens)),
            magenta(_fmt_tokens(t.output_tokens)),
            bold(_fmt_tokens(t.total_tokens)),
            yellow(_fmt_tokens(t.total_without_cache)),
            green(_fmt_money(t.cost_api_usd)),
        ]
        if verbose:
            row += [
                dim(_fmt_money(t.cost_cli_usd)),
                cyan(_fmt_money(t.cache_savings_usd)),
            ]
        rows.append(row)

    tot = _sum_bucket([d.tokens for d in dailies])
    all_sess: Set[str] = set()
    for d in dailies:
        all_sess |= d.sessions
    hit = _cache_hit_pct(tot)
    total_row = [
        "TOTAL",
        str(tot.events),
        str(len(all_sess)),
        _fmt_tokens(tot.input_tokens),
        _fmt_tokens(tot.cached_read_tokens),
        f"{hit:.0f}%",
        _fmt_tokens(tot.fresh_input_tokens),
        _fmt_tokens(tot.output_tokens),
        _fmt_tokens(tot.total_tokens),
        _fmt_tokens(tot.total_without_cache),
        _fmt_money(tot.cost_api_usd),
    ]
    if verbose:
        total_row += [
            _fmt_money(tot.cost_cli_usd),
            _fmt_money(tot.cache_savings_usd),
        ]
    rows.append(total_row)

    print_table(headers, rows)
    print()
    print(dim("Cost = public list price with cache discount (not final invoice)."))
    print(dim("Total = Input+Output · NoCache = Fresh+Output (excludes cached input)."))
    print(dim("Cache ⊂ Input · Fresh = Input − Cache · Input accumulates across turns."))
    if not verbose:
        print(dim("Tip: -v shows Log$ (CLI internal) and cache Saved$."))


def print_sessions(
    stats: List[SessionStat],
    as_json: bool,
    sort: str,
    verbose: bool = False,
    account_limit: Optional[AccountLimit] = None,
) -> None:
    ordered = list(stats)
    if sort == "recent":
        ordered.sort(key=lambda s: s.last_activity, reverse=True)
    else:
        ordered.sort(key=lambda s: s.tokens.total_tokens, reverse=True)

    if as_json:
        payload = {
            "account_limit": account_limit.as_public_dict() if account_limit else None,
            "sessions": [s.as_public_dict() for s in ordered],
        }
        print(json.dumps(payload, indent=2))
        return
    if not ordered:
        print(bold(cyan("Grok Tokens")) + dim("  ·  session (UTC)"))
        print()
        print_account_limit_banner(account_limit)
        print(yellow("No sessions found."))
        return

    print(bold(cyan("Grok Tokens")) + dim("  ·  session (UTC)"))
    print()
    print_account_limit_banner(account_limit)

    headers = [
        "Session",
        "Reqs",
        "Input",
        "Cache",
        "Hit%",
        "Fresh",
        "Output",
        "Total",
        "NoCache",
        "Cost",
        "Peak",
        "Last",
    ]
    if verbose:
        headers += ["Log$", "Cwd"]

    rows: List[List[str]] = []
    for s in ordered:
        t = s.tokens
        hit = _cache_hit_pct(t) if t.events else 0.0
        last = s.last_activity[:16].replace("T", " ") if s.last_activity != "unknown" else "-"
        row = [
            cyan(s.session_id[:10]),
            str(t.events) if t.events else dim("0"),
            green(_fmt_tokens(t.input_tokens)) if t.events else dim("-"),
            dim(_fmt_tokens(t.cached_read_tokens)) if t.events else dim("-"),
            dim(f"{hit:.0f}%") if t.events else dim("-"),
            yellow(_fmt_tokens(t.fresh_input_tokens)) if t.events else dim("-"),
            magenta(_fmt_tokens(t.output_tokens)) if t.events else dim("-"),
            bold(_fmt_tokens(t.total_tokens)) if t.events else dim("-"),
            yellow(_fmt_tokens(t.total_without_cache)) if t.events else dim("-"),
            green(_fmt_money(t.cost_api_usd)) if t.events else dim("-"),
            blue(_fmt_tokens(s.peak_context)) if s.peak_context else dim("-"),
            dim(last),
        ]
        if verbose:
            cwd = s.cwd or "-"
            if len(cwd) > 28:
                cwd = "…" + cwd[-27:]
            row += [
                dim(_fmt_money(t.cost_cli_usd)) if t.events else dim("-"),
                dim(cwd),
            ]
        rows.append(row)

    print_table(headers, rows, total_row=False)
    n_usage = sum(1 for s in ordered if s.has_usage)
    print()
    print(dim(f"{len(ordered)} sessions · {n_usage} with usage · NoCache=Fresh+Out · Peak=max input"))
    if not verbose:
        print(dim("Tip: -v shows Log$ and project path."))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Shared options are attached to each subcommand so both of these work:
    #   grok-tokens --cwd /path daily
    #   grok-tokens daily --cwd /path
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--root",
        default=None,
        help="Sessions root (default: $GROK_DATA_DIR or ~/.grok/sessions)",
    )
    shared.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only include data on/after this UTC date",
    )
    shared.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max session directories to scan (newest first, default 200)",
    )
    shared.add_argument(
        "--cwd",
        default=None,
        help="Only sessions whose project cwd matches this path",
    )
    shared.add_argument(
        "--json",
        action="store_true",
        help="JSON output",
    )
    shared.add_argument(
        "--usage-only",
        action="store_true",
        help="session: hide sessions with zero usage events",
    )
    shared.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show Log$ (CLI internal cost) and cache Saved$",
    )
    shared.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors",
    )
    shared.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"grok-tokens {__version__}",
    )

    p = argparse.ArgumentParser(
        prog="grok-tokens",
        description=(
            "Count real Grok Build usage tokens from local session logs "
            "(usage.inputTokens / outputTokens / …)."
        ),
        parents=[shared],
    )

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "daily",
        parents=[shared],
        help="Token totals aggregated by UTC day",
    )

    sp = sub.add_parser(
        "session",
        parents=[shared],
        help="Per-session token totals",
    )
    sp.add_argument(
        "--sort",
        choices=("total", "recent"),
        default="total",
        help="Sort by total tokens (default) or last activity",
    )

    sub.add_parser(
        "limit",
        parents=[shared],
        help="Account Weekly/Monthly limit (same source as Grok /usage)",
    )

    return p


def main(argv: Optional[List[str]] = None) -> int:
    global _USE_COLOR
    print(
        "warning: grok_tokens.py is deprecated; install the Rust binary "
        "(./install.sh). This script will be removed.",
        file=sys.stderr,
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "no_color", False):
        _USE_COLOR = False

    account_limit = load_account_limit()

    if args.command == "limit":
        print_limit_detail(account_limit, args.json)
        return 0 if account_limit else 1

    root = Path(args.root) if args.root else sessions_root()
    if not root.is_dir():
        print(f"No sessions root at {root}", file=sys.stderr)
        print("Set GROK_DATA_DIR or GROK_HOME, or pass --root.", file=sys.stderr)
        return 1

    session_dirs = discover_sessions(root, args.limit)
    if not session_dirs:
        print(f"No session directories with updates.jsonl under {root}", file=sys.stderr)
        return 1

    # Optional cwd pre-filter on directory path (encoded parent) for speed
    if args.cwd:
        cwd_n = os.path.normpath(args.cwd)
        filtered = []
        for p in session_dirs:
            # quick check via summary / encoded name
            c = _read_cwd(p)
            if c and os.path.normpath(c) == cwd_n:
                filtered.append(p)
        session_dirs = filtered
        if not session_dirs:
            print(f"No sessions for cwd={args.cwd} under {root}", file=sys.stderr)
            return 1

    if args.command == "daily":
        dailies = daily_from_raw(root, session_dirs, args.since)
        print_daily(
            dailies, args.json, verbose=args.verbose, account_limit=account_limit
        )
        return 0

    if args.command == "session":
        stats: List[SessionStat] = []
        for p in session_dirs:
            s = load_session(p)
            if s:
                stats.append(s)
        stats = filter_sessions(
            stats,
            since=args.since,
            cwd=None,  # already filtered dirs
            usage_only=args.usage_only,
        )
        print_sessions(
            stats,
            args.json,
            sort=args.sort,
            verbose=args.verbose,
            account_limit=account_limit,
        )
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
