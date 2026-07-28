//! grok-tokens — real Grok Build usage tokens from local session logs.
//!
//! Pure native binary (no Python runtime). Parity with `grok_tokens.py`.

use anyhow::Result;
use chrono::{TimeZone, Utc};
use clap::{Parser, Subcommand, ValueEnum};
use regex::Regex;
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, IsTerminal};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use walkdir::WalkDir;

const VERSION: &str = env!("CARGO_PKG_VERSION");

// ── Pricing (USD per 1M tokens) — https://docs.x.ai/developers/pricing ────

type Rates = (f64, f64, f64); // input, cached, output

fn rates_for_model(model: &str) -> (Rates, Rates, u64) {
    let m = model.to_ascii_lowercase();
    if m.contains("grok-build-0.1") {
        return ((1.0, 0.2, 2.0), (2.0, 0.4, 4.0), 200_000);
    }
    if m.contains("grok-4.3") {
        return ((1.25, 0.2, 2.5), (2.5, 0.4, 5.0), 200_000);
    }
    // grok-4.5 / grok-4.5-build / default
    ((2.0, 0.3, 6.0), (4.0, 0.6, 12.0), 200_000)
}

fn model_from_usage(u: &Value) -> String {
    if let Some(mu) = u.get("modelUsage").and_then(|v| v.as_object()) {
        if let Some(k) = mu.keys().next() {
            return k.clone();
        }
    }
    "grok-4.5".into()
}

fn estimate_api_cost_usd(u: &Value) -> f64 {
    let inp = json_u64(u, "inputTokens");
    let out = json_u64(u, "outputTokens");
    let cache = json_u64(u, "cachedReadTokens");
    let fresh = inp.saturating_sub(cache);
    let model = model_from_usage(u);
    let (short, long, thr) = rates_for_model(&model);
    let (rin, rcache, rout) = if inp >= thr { long } else { short };
    (fresh as f64 * rin + cache as f64 * rcache + out as f64 * rout) / 1_000_000.0
}

fn estimate_api_cost_no_cache_usd(u: &Value) -> f64 {
    let inp = json_u64(u, "inputTokens");
    let out = json_u64(u, "outputTokens");
    let model = model_from_usage(u);
    let (short, long, thr) = rates_for_model(&model);
    let (rin, _rc, rout) = if inp >= thr { long } else { short };
    (inp as f64 * rin + out as f64 * rout) / 1_000_000.0
}

fn json_u64(v: &Value, key: &str) -> u64 {
    v.get(key)
        .and_then(|x| x.as_u64().or_else(|| x.as_i64().map(|i| i.max(0) as u64)))
        .unwrap_or(0)
}

// ── Data ──────────────────────────────────────────────────────────────────

#[derive(Debug, Default, Clone, Serialize)]
struct TokenBucket {
    input_tokens: u64,
    output_tokens: u64,
    total_tokens: u64,
    cached_read_tokens: u64,
    reasoning_tokens: u64,
    cost_usd_ticks: u64,
    cost_api_usd: f64,
    cost_api_no_cache_usd: f64,
    events: u64,
    model_calls: u64,
    models: BTreeSet<String>,
}

impl TokenBucket {
    fn add_usage(&mut self, u: &Value) {
        self.input_tokens += json_u64(u, "inputTokens");
        self.output_tokens += json_u64(u, "outputTokens");
        self.total_tokens += json_u64(u, "totalTokens");
        self.cached_read_tokens += json_u64(u, "cachedReadTokens");
        self.reasoning_tokens += json_u64(u, "reasoningTokens");
        self.cost_usd_ticks += json_u64(u, "costUsdTicks");
        self.cost_api_usd += estimate_api_cost_usd(u);
        self.cost_api_no_cache_usd += estimate_api_cost_no_cache_usd(u);
        self.events += 1;
        self.model_calls += json_u64(u, "modelCalls");
        if let Some(mu) = u.get("modelUsage").and_then(|v| v.as_object()) {
            for k in mu.keys() {
                self.models.insert(k.clone());
            }
        } else {
            self.models.insert("unknown".into());
        }
    }

    fn merge(&mut self, o: &TokenBucket) {
        self.input_tokens += o.input_tokens;
        self.output_tokens += o.output_tokens;
        self.total_tokens += o.total_tokens;
        self.cached_read_tokens += o.cached_read_tokens;
        self.reasoning_tokens += o.reasoning_tokens;
        self.cost_usd_ticks += o.cost_usd_ticks;
        self.cost_api_usd += o.cost_api_usd;
        self.cost_api_no_cache_usd += o.cost_api_no_cache_usd;
        self.events += o.events;
        self.model_calls += o.model_calls;
        self.models.extend(o.models.iter().cloned());
    }

    fn fresh_input_tokens(&self) -> u64 {
        self.input_tokens.saturating_sub(self.cached_read_tokens)
    }
    fn total_without_cache(&self) -> u64 {
        self.fresh_input_tokens() + self.output_tokens
    }
    fn cost_cli_usd(&self) -> f64 {
        self.cost_usd_ticks as f64 / 1_000_000_000.0
    }
    fn cache_savings_usd(&self) -> f64 {
        (self.cost_api_no_cache_usd - self.cost_api_usd).max(0.0)
    }
    fn hit_pct(&self) -> f64 {
        if self.input_tokens == 0 {
            0.0
        } else {
            100.0 * self.cached_read_tokens as f64 / self.input_tokens as f64
        }
    }

    fn as_public(&self) -> serde_json::Value {
        serde_json::json!({
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_read_tokens": self.cached_read_tokens,
            "fresh_input_tokens": self.fresh_input_tokens(),
            "total_without_cache": self.total_without_cache(),
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd_ticks": self.cost_usd_ticks,
            "cost_cli_usd": (self.cost_cli_usd() * 10000.0).round() / 10000.0,
            "cost_api_usd": (self.cost_api_usd * 10000.0).round() / 10000.0,
            "cost_api_no_cache_usd": (self.cost_api_no_cache_usd * 10000.0).round() / 10000.0,
            "cache_savings_usd": (self.cache_savings_usd() * 10000.0).round() / 10000.0,
            "events": self.events,
            "model_calls": self.model_calls,
            "models": self.models.iter().cloned().collect::<Vec<_>>(),
        })
    }
}

#[derive(Debug, Clone, Serialize)]
struct SessionStat {
    session_id: String,
    path: String,
    cwd: String,
    tokens: TokenBucket,
    last_activity: String,
    last_activity_day: String,
    peak_input_tokens: u64,
    meta_peak_context: u64,
    meta_final_context: u64,
    meta_first_context: u64,
    has_usage: bool,
}

impl SessionStat {
    fn peak_context(&self) -> u64 {
        self.peak_input_tokens.max(self.meta_peak_context)
    }
    fn as_public(&self) -> serde_json::Value {
        let mut m = serde_json::Map::new();
        m.insert("session_id".into(), self.session_id.clone().into());
        m.insert("path".into(), self.path.clone().into());
        m.insert("cwd".into(), self.cwd.clone().into());
        m.insert("last_activity".into(), self.last_activity.clone().into());
        m.insert(
            "last_activity_day".into(),
            self.last_activity_day.clone().into(),
        );
        m.insert("peak_context".into(), self.peak_context().into());
        m.insert("peak_input_tokens".into(), self.peak_input_tokens.into());
        m.insert("meta_peak_context".into(), self.meta_peak_context.into());
        m.insert("meta_final_context".into(), self.meta_final_context.into());
        m.insert("meta_first_context".into(), self.meta_first_context.into());
        m.insert("has_usage".into(), self.has_usage.into());
        if let Value::Object(t) = self.tokens.as_public() {
            m.extend(t);
        }
        Value::Object(m)
    }
}

#[derive(Debug, Clone)]
struct DailyStat {
    date: String,
    tokens: TokenBucket,
    sessions: BTreeSet<String>,
}

impl DailyStat {
    fn as_public(&self) -> serde_json::Value {
        let mut m = serde_json::Map::new();
        m.insert("date".into(), self.date.clone().into());
        m.insert("sessions".into(), self.sessions.len().into());
        if let Value::Object(t) = self.tokens.as_public() {
            m.extend(t);
        }
        Value::Object(m)
    }
}

// ── Paths / discovery ─────────────────────────────────────────────────────

fn sessions_root() -> PathBuf {
    if let Ok(d) = env::var("GROK_DATA_DIR") {
        if !d.trim().is_empty() {
            return PathBuf::from(d);
        }
    }
    if let Ok(h) = env::var("GROK_HOME") {
        let p = PathBuf::from(h).join("sessions");
        if p.is_dir() {
            return p;
        }
    }
    dirs_home().join(".grok").join("sessions")
}

fn dirs_home() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn discover_sessions(root: &Path, limit: usize) -> Vec<PathBuf> {
    let mut found: Vec<(std::time::SystemTime, PathBuf)> = Vec::new();
    if !root.is_dir() {
        return found.into_iter().map(|(_, p)| p).collect();
    }
    for e in WalkDir::new(root).into_iter().filter_map(|e| e.ok()) {
        if !e.file_type().is_dir() {
            continue;
        }
        let updates = e.path().join("updates.jsonl");
        if !updates.is_file() {
            continue;
        }
        let mtime = fs::metadata(&updates)
            .and_then(|m| m.modified())
            .unwrap_or(std::time::UNIX_EPOCH);
        found.push((mtime, e.path().to_path_buf()));
    }
    found.sort_by(|a, b| b.0.cmp(&a.0));
    found.into_iter().take(limit).map(|(_, p)| p).collect()
}

fn read_cwd(session_dir: &Path) -> String {
    let summary = session_dir.join("summary.json");
    if summary.is_file() {
        if let Ok(txt) = fs::read_to_string(&summary) {
            if let Ok(v) = serde_json::from_str::<Value>(&txt) {
                if let Some(cwd) = v
                    .pointer("/info/cwd")
                    .or_else(|| v.get("cwd"))
                    .and_then(|x| x.as_str())
                {
                    if !cwd.is_empty() {
                        return cwd.to_string();
                    }
                }
            }
        }
    }
    if let Some(parent) = session_dir.parent().and_then(|p| p.file_name()) {
        let name = parent.to_string_lossy();
        if name.starts_with('%') {
            return urlencoding_decode(&name);
        }
    }
    String::new()
}

fn urlencoding_decode(s: &str) -> String {
    // minimal: %2F → / etc.
    let bytes = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let h = || {
                let a = bytes[i + 1];
                let b = bytes[i + 2];
                let n = |c: u8| match c {
                    b'0'..=b'9' => Some(c - b'0'),
                    b'a'..=b'f' => Some(c - b'a' + 10),
                    b'A'..=b'F' => Some(c - b'A' + 10),
                    _ => None,
                };
                Some(n(a)? * 16 + n(b)?)
            };
            if let Some(v) = h() {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

// ── JSON walk ─────────────────────────────────────────────────────────────

fn walk_usage_dicts(v: &Value, out: &mut Vec<Value>) {
    match v {
        Value::Object(map) => {
            if map.contains_key("inputTokens")
                && map.contains_key("outputTokens")
                && map.contains_key("totalTokens")
            {
                out.push(v.clone());
                for (k, child) in map {
                    if k == "modelUsage" {
                        continue;
                    }
                    walk_usage_dicts(child, out);
                }
                return;
            }
            for child in map.values() {
                walk_usage_dicts(child, out);
            }
        }
        Value::Array(arr) => {
            for child in arr {
                walk_usage_dicts(child, out);
            }
        }
        _ => {}
    }
}

fn agent_ts_ms(obj: &Value) -> Option<i64> {
    obj.pointer("/params/_meta/agentTimestampMs")
        .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)))
        .or_else(|| {
            obj.pointer("/params/update/_meta/agentTimestampMs")
                .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)))
        })
}

fn ms_to_day(ts: Option<i64>) -> String {
    match ts {
        Some(ms) if ms > 0 => Utc
            .timestamp_millis_opt(ms)
            .single()
            .map(|d| d.format("%Y-%m-%d").to_string())
            .unwrap_or_else(|| "unknown".into()),
        _ => "unknown".into(),
    }
}

fn ms_to_rfc3339(ts: Option<i64>) -> String {
    match ts {
        Some(ms) if ms > 0 => Utc
            .timestamp_millis_opt(ms)
            .single()
            .map(|d| d.to_rfc3339())
            .unwrap_or_else(|| "unknown".into()),
        _ => "unknown".into(),
    }
}

fn usage_key(u: &Value) -> (u64, u64, u64, u64, u64, u64) {
    (
        json_u64(u, "inputTokens"),
        json_u64(u, "outputTokens"),
        json_u64(u, "totalTokens"),
        json_u64(u, "cachedReadTokens"),
        json_u64(u, "costUsdTicks"),
        json_u64(u, "modelCalls"),
    )
}

fn prefer_usage(found: Vec<Value>) -> Vec<Value> {
    let top: Vec<Value> = found
        .iter()
        .filter(|u| u.get("costUsdTicks").is_some() || u.get("modelUsage").is_some())
        .cloned()
        .collect();
    if top.is_empty() {
        found
    } else {
        top
    }
}

fn load_session(path: &Path) -> Option<SessionStat> {
    let updates = path.join("updates.jsonl");
    if !updates.is_file() {
        return None;
    }
    let file = File::open(&updates).ok()?;
    let reader = BufReader::new(file);

    let mut bucket = TokenBucket::default();
    let mut last_ts: Option<i64> = None;
    let mut meta_vals: Vec<u64> = Vec::new();
    let mut peak_input: u64 = 0;
    let mut seen_keys: HashSet<(u64, u64, u64, u64, u64, u64)> = HashSet::new();

    for line in reader.lines().map_while(Result::ok) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let obj: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if !obj.is_object() {
            continue;
        }

        if let Some(ts) = agent_ts_ms(&obj) {
            last_ts = Some(last_ts.map_or(ts, |p| p.max(ts)));
        }

        if let Some(tot) = obj
            .pointer("/params/_meta/totalTokens")
            .and_then(|v| v.as_u64().or_else(|| v.as_i64().map(|i| i.max(0) as u64)))
        {
            meta_vals.push(tot);
        }

        if !line.contains("inputTokens") && !line.contains("usage") {
            continue;
        }

        let mut found = Vec::new();
        walk_usage_dicts(&obj, &mut found);
        let top = prefer_usage(found);
        for u in top {
            let key = usage_key(&u);
            if !seen_keys.insert(key) {
                continue;
            }
            peak_input = peak_input.max(json_u64(&u, "inputTokens"));
            bucket.add_usage(&u);
        }
    }

    let meta_peak = meta_vals.iter().copied().max().unwrap_or(0);
    let meta_first = meta_vals.first().copied().unwrap_or(0);
    let meta_last = meta_vals.last().copied().unwrap_or(0);
    let sid = path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();

    Some(SessionStat {
        session_id: sid,
        path: path.display().to_string(),
        cwd: read_cwd(path),
        has_usage: bucket.events > 0,
        tokens: bucket,
        last_activity: ms_to_rfc3339(last_ts),
        last_activity_day: ms_to_day(last_ts),
        peak_input_tokens: peak_input,
        meta_peak_context: meta_peak,
        meta_final_context: meta_last,
        meta_first_context: meta_first,
    })
}

fn daily_from_raw(session_dirs: &[PathBuf], since: Option<&str>) -> Vec<DailyStat> {
    let mut by_day: BTreeMap<String, DailyStat> = BTreeMap::new();

    for path in session_dirs {
        let updates = path.join("updates.jsonl");
        if !updates.is_file() {
            continue;
        }
        let sid = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let Ok(file) = File::open(&updates) else {
            continue;
        };
        for line in BufReader::new(file).lines().map_while(Result::ok) {
            if !line.contains("inputTokens") && !line.contains("usage") {
                continue;
            }
            let obj: Value = match serde_json::from_str(line.trim()) {
                Ok(v) => v,
                Err(_) => continue,
            };
            let mut found = Vec::new();
            walk_usage_dicts(&obj, &mut found);
            let top = prefer_usage(found);
            if top.is_empty() {
                continue;
            }
            let day = ms_to_day(agent_ts_ms(&obj));
            if let Some(s) = since {
                if day != "unknown" && day.as_str() < s {
                    continue;
                }
            }
            let entry = by_day.entry(day.clone()).or_insert_with(|| DailyStat {
                date: day,
                tokens: TokenBucket::default(),
                sessions: BTreeSet::new(),
            });
            entry.sessions.insert(sid.clone());
            let mut seen = HashSet::new();
            for u in top {
                let key = usage_key(&u);
                if !seen.insert(key) {
                    continue;
                }
                entry.tokens.add_usage(&u);
            }
        }
    }

    by_day.into_values().collect()
}

// ── Formatting / table ────────────────────────────────────────────────────

static ANSI_RE: OnceLock<Regex> = OnceLock::new();

fn ansi_re() -> &'static Regex {
    ANSI_RE.get_or_init(|| Regex::new(r"\x1b\[[0-9;]*m").unwrap())
}

fn use_color(no_color_flag: bool) -> bool {
    if no_color_flag {
        return false;
    }
    if env::var_os("NO_COLOR").is_some() {
        return false;
    }
    if matches!(
        env::var("FORCE_COLOR").as_deref(),
        Ok("1") | Ok("true") | Ok("yes")
    ) {
        return true;
    }
    atty_stdout()
}

fn atty_stdout() -> bool {
    std::io::stdout().is_terminal()
}

fn c(enabled: bool, code: &str, text: &str) -> String {
    if !enabled || text.is_empty() {
        return text.to_string();
    }
    format!("\x1b[{code}m{text}\x1b[0m")
}

fn visible_len(s: &str) -> usize {
    ansi_re().replace_all(s, "").chars().count()
}

fn fmt_tokens(n: u64) -> String {
    if n < 1_000 {
        return n.to_string();
    }
    if n < 1_000_000 {
        let v = n as f64 / 1_000.0;
        let s = format!("{v:.1}");
        return format!("{}K", s.trim_end_matches('0').trim_end_matches('.'));
    }
    if n < 1_000_000_000 {
        let v = n as f64 / 1_000_000.0;
        let s = format!("{v:.2}");
        return format!("{}M", s.trim_end_matches('0').trim_end_matches('.'));
    }
    let v = n as f64 / 1_000_000_000.0;
    let s = format!("{v:.2}");
    format!("{}B", s.trim_end_matches('0').trim_end_matches('.'))
}

fn fmt_money(x: f64) -> String {
    format!("${x:.2}")
}

fn pad(s: &str, width: usize, right: bool) -> String {
    let vis = visible_len(s);
    let padn = width.saturating_sub(vis);
    if right {
        format!("{}{}", " ".repeat(padn), s)
    } else {
        format!("{}{}", s, " ".repeat(padn))
    }
}

fn print_table(headers: &[String], rows: &[Vec<String>], color: bool, total_row: bool) {
    if headers.is_empty() {
        return;
    }
    let n = headers.len();
    let mut widths: Vec<usize> = headers.iter().map(|h| visible_len(h)).collect();
    for row in rows {
        for (i, cell) in row.iter().enumerate().take(n) {
            widths[i] = widths[i].max(visible_len(cell));
        }
    }

    let dim = |s: &str| c(color, "2", s);
    let bold = |s: &str| c(color, "1", s);
    let hcyan = |s: &str| c(color, "1;36", s);

    let hline = |left: &str, mid: &str, right: &str| {
        let parts: Vec<String> = widths.iter().map(|w| "─".repeat(w + 2)).collect();
        dim(&format!("{left}{}{right}", parts.join(mid)))
    };

    let row_line = |cells: &[String], emphasize: bool| {
        let mut out = Vec::new();
        for i in 0..n {
            let cell = cells.get(i).map(|s| s.as_str()).unwrap_or("");
            let right = i > 0;
            let mut padded = pad(cell, widths[i], right);
            if emphasize {
                padded = bold(&padded);
            }
            out.push(format!(" {padded} "));
        }
        format!(
            "{}{}{}",
            dim("│"),
            out.join(&dim("│")),
            dim("│")
        )
    };

    println!("{}", hline("╭", "┬", "╮"));
    let hcells: Vec<String> = headers.iter().map(|h| hcyan(h)).collect();
    println!("{}", row_line(&hcells, false));
    println!("{}", hline("├", "┼", "┤"));

    for row in rows {
        let plain0 = ansi_re().replace_all(row.first().map(|s| s.as_str()).unwrap_or(""), "");
        let is_total = total_row && plain0.trim().eq_ignore_ascii_case("TOTAL");
        if is_total {
            println!("{}", hline("├", "┼", "┤"));
        }
        let mut cells = row.clone();
        while cells.len() < n {
            cells.push(String::new());
        }
        println!("{}", row_line(&cells[..n], is_total));
    }
    println!("{}", hline("╰", "┴", "╯"));
}

fn print_daily(dailies: &[DailyStat], json: bool, verbose: bool, color: bool) {
    if json {
        let arr: Vec<_> = dailies.iter().map(|d| d.as_public()).collect();
        println!("{}", serde_json::to_string_pretty(&arr).unwrap());
        return;
    }
    if dailies.is_empty() {
        println!("{}", c(color, "33", "No usage events found."));
        return;
    }

    println!(
        "{}{}",
        c(color, "1;36", "Grok Tokens"),
        c(color, "2", "  ·  daily (UTC)")
    );
    println!();

    let mut headers = vec![
        "Date", "Reqs", "Sess", "Input", "Cache", "Hit%", "Fresh", "Output", "Total", "NoCache",
        "Cost",
    ]
    .into_iter()
    .map(String::from)
    .collect::<Vec<_>>();
    if verbose {
        headers.push("Log$".into());
        headers.push("Saved".into());
    }

    let mut rows: Vec<Vec<String>> = Vec::new();
    let mut tot = TokenBucket::default();
    let mut all_sess: BTreeSet<String> = BTreeSet::new();

    for d in dailies {
        let t = &d.tokens;
        tot.merge(t);
        all_sess.extend(d.sessions.iter().cloned());
        let mut row = vec![
            c(color, "36", &d.date),
            t.events.to_string(),
            d.sessions.len().to_string(),
            c(color, "32", &fmt_tokens(t.input_tokens)),
            c(color, "2", &fmt_tokens(t.cached_read_tokens)),
            c(color, "2", &format!("{:.0}%", t.hit_pct())),
            c(color, "33", &fmt_tokens(t.fresh_input_tokens())),
            c(color, "35", &fmt_tokens(t.output_tokens)),
            c(color, "1", &fmt_tokens(t.total_tokens)),
            c(color, "33", &fmt_tokens(t.total_without_cache())),
            c(color, "32", &fmt_money(t.cost_api_usd)),
        ];
        if verbose {
            row.push(c(color, "2", &fmt_money(t.cost_cli_usd())));
            row.push(c(color, "36", &fmt_money(t.cache_savings_usd())));
        }
        rows.push(row);
    }

    let mut total_row = vec![
        "TOTAL".into(),
        tot.events.to_string(),
        all_sess.len().to_string(),
        fmt_tokens(tot.input_tokens),
        fmt_tokens(tot.cached_read_tokens),
        format!("{:.0}%", tot.hit_pct()),
        fmt_tokens(tot.fresh_input_tokens()),
        fmt_tokens(tot.output_tokens),
        fmt_tokens(tot.total_tokens),
        fmt_tokens(tot.total_without_cache()),
        fmt_money(tot.cost_api_usd),
    ];
    if verbose {
        total_row.push(fmt_money(tot.cost_cli_usd()));
        total_row.push(fmt_money(tot.cache_savings_usd()));
    }
    rows.push(total_row);

    print_table(&headers, &rows, color, true);
    println!();
    println!(
        "{}",
        c(
            color,
            "2",
            "Cost = public list price with cache discount (not final invoice)."
        )
    );
    println!(
        "{}",
        c(
            color,
            "2",
            "Total = Input+Output · NoCache = Fresh+Output (excludes cached input)."
        )
    );
    println!(
        "{}",
        c(
            color,
            "2",
            "Cache ⊂ Input · Fresh = Input − Cache · Input accumulates across turns."
        )
    );
    if !verbose {
        println!(
            "{}",
            c(color, "2", "Tip: -v shows Log$ (CLI internal) and cache Saved$.")
        );
    }
}

fn print_sessions(stats: &[SessionStat], json: bool, sort: SortMode, verbose: bool, color: bool) {
    let mut ordered = stats.to_vec();
    match sort {
        SortMode::Recent => ordered.sort_by(|a, b| b.last_activity.cmp(&a.last_activity)),
        SortMode::Total => ordered.sort_by(|a, b| b.tokens.total_tokens.cmp(&a.tokens.total_tokens)),
    }

    if json {
        let arr: Vec<_> = ordered.iter().map(|s| s.as_public()).collect();
        println!("{}", serde_json::to_string_pretty(&arr).unwrap());
        return;
    }
    if ordered.is_empty() {
        println!("{}", c(color, "33", "No sessions found."));
        return;
    }

    println!(
        "{}{}",
        c(color, "1;36", "Grok Tokens"),
        c(color, "2", "  ·  session (UTC)")
    );
    println!();

    let mut headers = vec![
        "Session", "Reqs", "Input", "Cache", "Hit%", "Fresh", "Output", "Total", "NoCache",
        "Cost", "Peak", "Last",
    ]
    .into_iter()
    .map(String::from)
    .collect::<Vec<_>>();
    if verbose {
        headers.push("Log$".into());
        headers.push("Cwd".into());
    }

    let mut rows = Vec::new();
    for s in &ordered {
        let t = &s.tokens;
        let last = if s.last_activity == "unknown" {
            "-".into()
        } else {
            s.last_activity
                .chars()
                .take(16)
                .collect::<String>()
                .replace('T', " ")
        };
        let sid: String = s.session_id.chars().take(10).collect();
        let mut row = if t.events > 0 {
            vec![
                c(color, "36", &sid),
                t.events.to_string(),
                c(color, "32", &fmt_tokens(t.input_tokens)),
                c(color, "2", &fmt_tokens(t.cached_read_tokens)),
                c(color, "2", &format!("{:.0}%", t.hit_pct())),
                c(color, "33", &fmt_tokens(t.fresh_input_tokens())),
                c(color, "35", &fmt_tokens(t.output_tokens)),
                c(color, "1", &fmt_tokens(t.total_tokens)),
                c(color, "33", &fmt_tokens(t.total_without_cache())),
                c(color, "32", &fmt_money(t.cost_api_usd)),
                c(color, "34", &fmt_tokens(s.peak_context())),
                c(color, "2", &last),
            ]
        } else {
            vec![
                c(color, "36", &sid),
                c(color, "2", "0"),
                c(color, "2", "-"),
                c(color, "2", "-"),
                c(color, "2", "-"),
                c(color, "2", "-"),
                c(color, "2", "-"),
                c(color, "2", "-"),
                c(color, "2", "-"),
                c(color, "2", "-"),
                if s.peak_context() > 0 {
                    c(color, "34", &fmt_tokens(s.peak_context()))
                } else {
                    c(color, "2", "-")
                },
                c(color, "2", &last),
            ]
        };
        if verbose {
            let mut cwd = if s.cwd.is_empty() {
                "-".into()
            } else {
                s.cwd.clone()
            };
            if cwd.chars().count() > 28 {
                let tail: String = cwd
                    .chars()
                    .rev()
                    .take(27)
                    .collect::<String>()
                    .chars()
                    .rev()
                    .collect();
                cwd = format!("…{tail}");
            }
            row.push(if t.events > 0 {
                c(color, "2", &fmt_money(t.cost_cli_usd()))
            } else {
                c(color, "2", "-")
            });
            row.push(c(color, "2", &cwd));
        }
        rows.push(row);
    }

    print_table(&headers, &rows, color, false);
    let n_usage = ordered.iter().filter(|s| s.has_usage).count();
    println!();
    println!(
        "{}",
        c(
            color,
            "2",
            &format!(
                "{} sessions · {} with usage · NoCache=Fresh+Out · Peak=max input",
                ordered.len(),
                n_usage
            )
        )
    );
    if !verbose {
        println!(
            "{}",
            c(color, "2", "Tip: -v shows Log$ and project path.")
        );
    }
}

// ── CLI ───────────────────────────────────────────────────────────────────

#[derive(Parser, Debug)]
#[command(
    name = "grok-tokens",
    version = VERSION,
    about = "Count real Grok Build usage tokens from local session logs"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    /// Sessions root (default: $GROK_DATA_DIR or ~/.grok/sessions)
    #[arg(long, global = true)]
    root: Option<PathBuf>,

    /// Only include data on/after this UTC date (YYYY-MM-DD)
    #[arg(long, global = true)]
    since: Option<String>,

    /// Max session directories to scan (newest first)
    #[arg(long, global = true, default_value_t = 200)]
    limit: usize,

    /// Only sessions whose project cwd matches this path
    #[arg(long, global = true)]
    cwd: Option<String>,

    /// JSON output
    #[arg(long, global = true)]
    json: bool,

    /// session: hide sessions with zero usage events
    #[arg(long, global = true)]
    usage_only: bool,

    /// Show Log$ (CLI internal cost) and cache Saved$
    #[arg(short = 'v', long, global = true)]
    verbose: bool,

    /// Disable ANSI colors
    #[arg(long, global = true)]
    no_color: bool,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Token totals aggregated by UTC day
    Daily,
    /// Per-session token totals
    Session {
        /// Sort by total tokens (default) or last activity
        #[arg(long, value_enum, default_value_t = SortMode::Total)]
        sort: SortMode,
    },
}

#[derive(ValueEnum, Clone, Copy, Debug, Default)]
enum SortMode {
    #[default]
    Total,
    Recent,
}

fn path_norm(p: &str) -> PathBuf {
    // lightweight normalize
    PathBuf::from(p)
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let color = use_color(cli.no_color);

    let root = cli.root.clone().unwrap_or_else(sessions_root);
    if !root.is_dir() {
        eprintln!("No sessions root at {}", root.display());
        eprintln!("Set GROK_DATA_DIR or GROK_HOME, or pass --root.");
        std::process::exit(1);
    }

    let mut session_dirs = discover_sessions(&root, cli.limit);
    if session_dirs.is_empty() {
        eprintln!(
            "No session directories with updates.jsonl under {}",
            root.display()
        );
        std::process::exit(1);
    }

    if let Some(ref cwd) = cli.cwd {
        let want = path_norm(cwd);
        session_dirs.retain(|p| {
            let c = read_cwd(p);
            !c.is_empty() && path_norm(&c) == want
        });
        if session_dirs.is_empty() {
            eprintln!("No sessions for cwd={cwd} under {}", root.display());
            std::process::exit(1);
        }
    }

    match cli.command {
        Commands::Daily => {
            let dailies = daily_from_raw(&session_dirs, cli.since.as_deref());
            print_daily(&dailies, cli.json, cli.verbose, color);
        }
        Commands::Session { sort } => {
            let mut stats: Vec<SessionStat> = session_dirs
                .iter()
                .filter_map(|p| load_session(p))
                .collect();
            if let Some(ref since) = cli.since {
                stats.retain(|s| {
                    s.last_activity_day == "unknown" || s.last_activity_day.as_str() >= since.as_str()
                });
            }
            if cli.usage_only {
                stats.retain(|s| s.has_usage);
            }
            print_sessions(&stats, cli.json, sort, cli.verbose, color);
        }
    }

    Ok(())
}
