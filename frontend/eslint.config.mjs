import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import betterTailwind from "eslint-plugin-better-tailwindcss";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // Catch class names and arbitrary values Tailwind never registered — the
    // class-name twin of scripts/check-tokens.mjs (typo → silently no styling).
    // WARN on purpose: this is drift we clean up deliberately, not a build stop.
    files: ["components/**/*.tsx", "app/**/*.tsx"],
    plugins: { "better-tailwindcss": betterTailwind },
    settings: {
      // Tailwind v4 has no JS config — the plugin reads the real utility set by
      // compiling the css entry point.
      "better-tailwindcss": { entryPoint: "app/globals.css" },
    },
    rules: { "better-tailwindcss/no-unknown-classes": "warn" },
  },
  {
    files: ["components/workspace/widgets/**/*.tsx"],
    rules: {
      "no-restricted-syntax": [
        "warn",
        {
          selector: 'JSXOpeningElement[name.name="table"]',
          message:
            "use the DataTable primitive (components/workspace/primitives/) — see frontend/CLAUDE.md",
        },
      ],
    },
  },
]);

export default eslintConfig;
