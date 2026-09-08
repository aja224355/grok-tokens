"use strict";

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const {
  REPO,
  PKG,
  PKG_DIR,
  VENDOR_DIR,
  detectTarget,
  vendorBinaryPath,
  localReleaseBinary,
  hasCargoToml,
  githubHeaders,
  latestTag,
  triplesFor,
  tmpDir,
} = require("./lib");

function log(msg) {
  console.log(`grok-tokens: ${msg}`);
}

function copyToVendor(src) {
  fs.mkdirSync(VENDOR_DIR, { recursive: true });
  const dest = path.join(VENDOR_DIR, path.basename(src));
  fs.copyFileSync(src, dest);
  if (process.platform !== "win32") {
    fs.chmodSync(dest, 0o755);
  }
  log(`installed ${dest}`);
  return dest;
}

function which(cmd) {
  const dirs = (process.env.PATH || "").split(path.delimiter);
  const exts =
    process.platform === "win32"
      ? (process.env.PATHEXT || ".EXE;.CMD;.BAT").split(";").concat("")
      : [""];
  for (const dir of dirs) {
    for (const ext of exts) {
      if (fs.existsSync(path.join(dir, cmd + ext))) return true;
    }
  }
  return false;
}

function cargoBuild() {
  if (!hasCargoToml() || !which("cargo")) return false;
  log("building release binary with cargo...");
  execFileSync("cargo", ["build", "--release"], {
    cwd: PKG_DIR,
    stdio: "inherit",
  });
  const built = localReleaseBinary();
  if (!built) return false;
  copyToVendor(built);
  return true;
}

async function downloadTo(url, dest) {
  const res = await fetch(url, {
    headers: githubHeaders(),
    redirect: "follow",
  });
  if (!res.ok) return false;
  const buf = Buffer.from(await res.arrayBuffer());
  if (buf.length < 16) return false;
  fs.writeFileSync(dest, buf);
  return true;
}

function extractTarGz(archive, destDir, binName) {
  fs.mkdirSync(destDir, { recursive: true });
  execFileSync("tar", ["-xzf", archive, "-C", destDir], { stdio: "ignore" });
  const found = walkFind(destDir, binName);
  return found;
}

function walkFind(root, name) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) stack.push(p);
      else if (ent.name === name) return p;
    }
  }
  return null;
}

async function downloadRelease(target) {
  const tags = [];
  const pinned = process.env.GROK_TOKENS_TAG || (PKG.version ? `v${PKG.version}` : "");
  if (pinned) tags.push(pinned);
  try {
    const latest = await latestTag();
    if (latest && !tags.includes(latest)) tags.push(latest);
  } catch (err) {
    log(`could not query GitHub releases: ${err.message}`);
  }
  if (tags.length === 0) return false;

  const scratch = tmpDir();
  try {
    for (const tag of tags) {
      for (const triple of triplesFor(target)) {
        const name = `grok-tokens-${triple}.tar.gz`;
        const url = `https://github.com/${REPO}/releases/download/${tag}/${name}`;
        const archive = path.join(scratch, name);
        log(`trying ${url}`);
        try {
          if (!(await downloadTo(url, archive))) continue;
          const extracted = extractTarGz(archive, path.join(scratch, triple), target.bin);
          if (extracted) {
            copyToVendor(extracted);
            return true;
          }
        } catch (err) {
          log(`${name}: ${err.message}`);
        }
      }
    }
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
  return false;
}

async function main() {
  if (process.env.GROK_TOKENS_SKIP_DOWNLOAD === "1") {
    log("GROK_TOKENS_SKIP_DOWNLOAD=1 — skipping");
    return;
  }

  const existing = vendorBinaryPath();
  if (existing && fs.existsSync(existing)) {
    log(`already present: ${existing}`);
    return;
  }

  const local = localReleaseBinary();
  if (local) {
    copyToVendor(local);
    return;
  }

  const target = detectTarget();
  if (!target) {
    console.error(
      `grok-tokens: unsupported platform ${process.platform}-${process.arch}`
    );
    console.error("Supported: Windows/Linux/macOS on x64 or arm64.");
    process.exit(1);
  }

  if (await downloadRelease(target)) return;
  if (cargoBuild()) return;

  console.error("grok-tokens: no native binary for this platform.");
  console.error("Options:");
  console.error(`  npm install -g github:${REPO}`);
  console.error(`  cargo install --git https://github.com/${REPO} --locked`);
  console.error("  git clone the repo and run: cargo build --release");
  process.exit(1);
}

main().catch((err) => {
  console.error(`grok-tokens: ${err.stack || err.message}`);
  process.exit(1);
});
