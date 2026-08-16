// pi extension: the ONLY tool a local-model book seat gets.
//
// Exposes tools/perception_query.py (read-only perception layer) as a single
// `perception_query` tool. Loaded with `pi --no-builtin-tools -e tools/pi_perception_ext.ts`
// so the seat has no bash / edit / write — the same deny-by-default shape as the
// --allowedTools list in tools/book_session.sh, enforced structurally instead of by prompt.
// Runs the CLI via execFile (argv, no shell) from the repo root.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { execFile } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON = resolve(REPO, ".venv/bin/python");
const CLI = resolve(REPO, "tools/perception_query.py");
const MAX_CHARS_CAP = 20_000;
const TIMEOUT_MS = 180_000;

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "perception_query",
    label: "Perception query",
    description:
      "Read the firm's own market data (read-only). command='capabilities' lists every resource; " +
      "command='context' with symbol gives everything known about one ticker (quote, RVOL, technicals, " +
      "session levels, earnings, news, decision score); command='query' with resource (+ optional " +
      "filters like ['symbol=NVDA','limit=5','order=unusual']) reads one resource. Never go to the web " +
      "for prices, OHLC, technicals or levels — this is current and free.",
    parameters: Type.Object({
      command: Type.Union([
        Type.Literal("capabilities"),
        Type.Literal("context"),
        Type.Literal("query"),
      ]),
      symbol: Type.Optional(Type.String({ description: "ticker, for command='context'" })),
      resource: Type.Optional(Type.String({ description: "resource name, for command='query'" })),
      filters: Type.Optional(
        Type.Array(Type.String({ description: "k=v" }), { description: "query filters as k=v strings" }),
      ),
      max_chars: Type.Optional(
        Type.Integer({ description: "truncate the result (default 6000, max 20000)", minimum: 200 }),
      ),
    }),
    async execute(_toolCallId, params) {
      const maxChars = Math.min(params.max_chars ?? 6000, MAX_CHARS_CAP);
      const argv = [CLI, "--max-chars", String(maxChars)];
      if (params.command === "capabilities") {
        argv.push("capabilities");
      } else if (params.command === "context") {
        if (!params.symbol) return fail("context needs symbol");
        argv.push("context", params.symbol);
      } else {
        if (!params.resource) return fail("query needs resource");
        argv.push("query", params.resource);
        for (const f of params.filters ?? []) {
          if (!/^[A-Za-z0-9_]+=[^\s]+$/.test(f)) return fail(`bad filter ${JSON.stringify(f)} (want k=v)`);
          argv.push("--filter", f);
        }
      }
      const text = await new Promise<string>((done) => {
        execFile(
          PYTHON,
          argv,
          { cwd: REPO, timeout: TIMEOUT_MS, maxBuffer: 4 * MAX_CHARS_CAP },
          (err, stdout, stderr) => {
            if (err && !stdout) done(`perception_query failed: ${(stderr || err.message).slice(0, 2000)}`);
            else done(String(stdout));
          },
        );
      });
      return { content: [{ type: "text", text }], details: {} };
    },
  });
}

function fail(msg: string) {
  return { content: [{ type: "text" as const, text: `error: ${msg}` }], details: {} };
}
