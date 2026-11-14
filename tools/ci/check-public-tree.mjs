import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

// Inspect Git's index, not arbitrary files in a maintainer's private working tree.
const paths = execFileSync("git", ["ls-files", "-z"], { encoding: "utf8" }).split("\0").filter(Boolean);
const privatePath = path =>
  /^(?:data|documents|\.runtime)(?:\/|$)/.test(path) ||
  /(^|\/)(?:\.cache|\.venv|\.agents|\.codex|\.pytest_cache|\.ruff_cache|\.mypy_cache|\.playwright-mcp|node_modules|__pycache__)(?:\/|$)/.test(path) ||
  /(^|\/)(?:\.env(?:\..*)?|CLAUDE\.md)$/.test(path) && !path.endsWith(".env.example") ||
  path.startsWith("config/") && /\.(?:json|txt|dpapi)$/.test(path) && !path.endsWith(".example.json") ||
  path.startsWith("memory/") && path.endsWith(".json") ||
  path.startsWith("models/") && !["models/README.md", "models/manifest.json"].includes(path) ||
  /\.(?:log|sqlite|sqlite3|dpapi|bundle)$/.test(path);
const rejected = paths.filter(privatePath);
if (rejected.length) throw new Error(`Private/generated files are tracked:\n${rejected.join("\n")}`);
if (execFileSync("git", ["rev-parse", "--is-shallow-repository"], { encoding: "utf8", timeout: 10000 }).trim() !== "false")
  throw new Error("Complete reachable HEAD history is required; unshallow the repository before checking public paths");
// Deleted files still ship with history. Include root/merge diffs and disable
// rename detection so every historical pathname receives the same policy.
const historicalPaths = new Set(execFileSync("git", ["log", "--format=", "--name-only", "-z", "--no-renames", "--full-history", "--root", "-m", "HEAD"],
  { encoding: "utf8", timeout: 60000, maxBuffer: 64 * 1024 * 1024 }).split("\0").filter(Boolean));
const historicalRejected = [...historicalPaths].filter(privatePath);
if (historicalRejected.length) throw new Error(`Private/generated paths remain in reachable HEAD history:\n${historicalRejected.join("\n")}`);
if (!paths.includes("README.md") || !paths.includes("CONTRIBUTING.md") || !paths.includes("SECURITY.md"))
  throw new Error("Public repository entrypoints are missing");
if (paths.includes("desktop-bin/manifest.json")) {
  const manifest = JSON.parse(readFileSync("desktop-bin/manifest.json", "utf8").replace(/^\uFEFF/, ""));
  const portables = paths.filter(path => /^desktop-bin\/jarvis-desktop-.*\.exe$/.test(path));
  if (portables.length !== 1 || portables[0] !== `desktop-bin/${manifest.file}`)
    throw new Error("Track only the manifest-selected portable");
}
console.log(`Public tree: ${paths.length} tracked paths checked`);
