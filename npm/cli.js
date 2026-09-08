#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const { vendorBinaryPath, detectTarget, REPO } = require("./lib");

const bin = vendorBinaryPath();
if (!bin || !fs.existsSync(bin)) {
  const t = detectTarget();
  console.error("grok-tokens: native binary missing.");
  console.error("Reinstall:  npm install -g grok-tokens");
  console.error(`Or from git: npm install -g github:${REPO}`);
  if (t) console.error(`Expected: ${t.triple} (${t.bin})`);
  process.exit(1);
}

const result = spawnSync(bin, process.argv.slice(2), { stdio: "inherit" });
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
