#!/usr/bin/env node
// Dispatch a Composer 2.5 coding agent against this repo via the official
// Cursor SDK. Invoked by the orchestrator (Claude Code) as the "coder" in a
// plan -> brief -> code -> review loop. Streams progress live and prints a
// clearly-delimited final result so the caller can capture status + output.
//
// Usage:
//   node dispatch.mjs --brief ../../References/PHASE_X_BRIEF.md
//   node dispatch.mjs --prompt "Fix the channel selector bug in ..."
//   echo "do the thing" | node dispatch.mjs --cwd /path/to/repo
//
// Flags:
//   --brief <file>   Read the brief/prompt from a file (UTF-8).
//   --prompt <text>  Inline prompt (overrides --brief).
//   --cwd <dir>      Working directory the agent edits. Default: $REPO_ROOT
//                    (two levels up from this script), else process.cwd().
//   --model <id>     Model id. Default: composer-2.5.
//   --review         Route the agent's local tool calls through Cursor
//                    Auto-review (safer; may block some Shell/Fetch/MCP calls).
//   --no-stream      Wait silently and only print the final result.
//
// Auth: requires CURSOR_API_KEY in the environment.
//       Get one at https://cursor.com/dashboard/api

import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(SCRIPT_DIR, "..", "..");

// Load KEY=VALUE pairs from a local .env (gitignored) into process.env without
// overwriting anything already set. Avoids cross-process/session env inheritance
// pain: the orchestrator's shells don't see a terminal's `$env:` var, but they
// all see this file. Checked next to the script, then at the repo root.
function loadDotenv() {
  for (const dir of [SCRIPT_DIR, REPO_ROOT]) {
    const path = resolve(dir, ".env");
    if (!existsSync(path)) continue;
    for (const line of readFileSync(path, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      let val = trimmed.slice(eq + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) ||
          (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      if (key && process.env[key] === undefined) process.env[key] = val;
    }
  }
}
loadDotenv();

function parseArgs(argv) {
  const args = { stream: true, model: "composer-2.5", review: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--brief": args.brief = argv[++i]; break;
      case "--prompt": args.prompt = argv[++i]; break;
      case "--cwd": args.cwd = argv[++i]; break;
      case "--model": args.model = argv[++i]; break;
      case "--effort": args.effort = argv[++i]; break;   // low|medium|high (models that expose it, e.g. grok-4.5)
      case "--fast": args.fast = true; break;             // fast variant toggle (models that expose it)
      case "--review": args.review = true; break;
      case "--no-stream": args.stream = false; break;
      case "-h":
      case "--help": args.help = true; break;
      default:
        console.error(`Unknown flag: ${a}`);
        process.exit(2);
    }
  }
  return args;
}

function readStdin() {
  try {
    return readFileSync(0, "utf8").trim();
  } catch {
    return "";
  }
}

function fail(msg) {
  console.error(`\n[composer-bridge] ${msg}`);
  process.exit(2);
}

const args = parseArgs(process.argv.slice(2));

if (args.help) {
  console.log(readFileSync(fileURLToPath(import.meta.url), "utf8")
    .split("\n").filter((l) => l.startsWith("//")).map((l) => l.slice(3)).join("\n"));
  process.exit(0);
}

const apiKey = process.env.CURSOR_API_KEY;
if (!apiKey) {
  fail("CURSOR_API_KEY is not set. Get a key at https://cursor.com/dashboard/api " +
       "and export it (PowerShell: $env:CURSOR_API_KEY=\"...\").");
}

let prompt = args.prompt;
if (!prompt && args.brief) {
  try {
    prompt = readFileSync(resolve(process.cwd(), args.brief), "utf8");
  } catch (e) {
    fail(`Could not read brief file '${args.brief}': ${e.message}`);
  }
}
if (!prompt) prompt = readStdin();
if (!prompt || !prompt.trim()) {
  fail("No prompt provided. Use --brief <file>, --prompt <text>, or pipe via stdin.");
}

const cwd = resolve(args.cwd ?? REPO_ROOT);

// Import lazily so --help and arg errors don't require the dep to be installed.
let Agent;
try {
  ({ Agent } = await import("@cursor/sdk"));
} catch (e) {
  fail("@cursor/sdk is not installed. Run: npm install (in tools/composer-bridge). " +
       `Original error: ${e.message}`);
}

const started = Date.now();
console.error(`[composer-bridge] model=${args.model} cwd=${cwd} review=${args.review}`);
console.error(`[composer-bridge] dispatching (${prompt.length} chars of brief)...\n`);

let status = "finished";
let result = "";
let durationMs = Date.now() - started;
let branches = [];
let agent;

try {
  agent = await Agent.create({
    apiKey,
    model: (() => {
      // Pass effort/fast as model params (ModelSelection) when given; else id-only
      // (unchanged default). Params are ignored by models that don't expose them.
      const params = [];
      if (args.effort) params.push({ id: "effort", value: String(args.effort) });
      if (args.fast) params.push({ id: "fast", value: "true" });
      return params.length ? { id: args.model, params } : { id: args.model };
    })(),
    local: { cwd, autoReview: args.review },
  });

  const run = await agent.send(prompt);
  let accumulated = "";

  if (args.stream) {
    for await (const event of run.stream()) {
      switch (event.type) {
        case "assistant":
          for (const block of event.message?.content ?? []) {
            if (block.type === "text") {
              process.stdout.write(block.text);
              accumulated += block.text;
            }
          }
          break;
        case "thinking":
          if (event.text) process.stderr.write(`\x1b[2m${event.text}\x1b[0m`);
          break;
        case "tool_call":
          console.error(`\n[tool] ${event.name}: ${event.status ?? ""}`);
          break;
      }
    }
  }

  let summary = null;
  try {
    summary = await run.wait();
  } catch {
    // Some runtimes resolve the run once streamed; fall back to accumulated text.
  }

  status = summary?.status ?? "finished";
  result = summary?.result ?? accumulated;
  durationMs = summary?.durationMs ?? (Date.now() - started);
  branches = summary?.git?.branches ?? [];
} catch (err) {
  // Print only the message + cause; never let the error reach Node's top level,
  // which would dump the SDK's multi-MB minified bundle source.
  console.error(`\n[composer-bridge] dispatch failed: ${err?.message ?? err}`);
  if (err?.cause?.message) console.error(`  cause: ${err.cause.message}`);
  if (err?.operation) console.error(`  operation: ${err.operation}`);
  process.exitCode = 1;
} finally {
  if (agent) await agent[Symbol.asyncDispose]?.();
}

if (process.exitCode === 1) process.exit(1);

console.log(`\n\n=== COMPOSER RESULT (${status}, ${Math.round(durationMs / 1000)}s) ===`);
if (!args.stream && result) console.log(result);
if (branches.length) {
  console.log(`branches: ${branches.map((b) => b.branch ?? "?").join(", ")}`);
}

process.exit(status === "finished" ? 0 : 1);
