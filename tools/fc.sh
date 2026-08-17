#!/usr/bin/env bash
# Firecrawl reader (self-hosted, http://127.0.0.1:3002). Prints markdown to stdout.
#   bash tools/fc.sh get <url> [--full]        # scrape one page (--full keeps nav/header chrome)
#   bash tools/fc.sh search "<query>" [n]      # search + scrape top n pages in ONE call (default 3)
# Falls back is manual: if this errors, use the built-in WebFetch/WebSearch tools.
set -euo pipefail
FC=${FIRECRAWL_URL:-http://127.0.0.1:3002}

case "${1:-}" in
  get)
    url=$2; main=true; [[ "${3:-}" == "--full" ]] && main=false
    curl -sf -m 180 -X POST "$FC/v2/scrape" -H 'Content-Type: application/json' \
      -d "$(jq -nc --arg u "$url" --argjson m "$main" \
            '{url:$u,formats:["markdown"],onlyMainContent:$m}')" \
    | jq -r 'if .success then .data.markdown else "FIRECRAWL ERROR: \(.error // .)" end'
    ;;
  search)
    q=$2; n=${3:-3}
    curl -sf -m 300 -X POST "$FC/v2/search" -H 'Content-Type: application/json' \
      -d "$(jq -nc --arg q "$q" --argjson n "$n" \
            '{query:$q,limit:$n,scrapeOptions:{formats:["markdown"],onlyMainContent:true}}')" \
    | jq -r 'if .success then (.data.web[] | "\n=== \(.title)\n=== \(.url)\n\(.markdown // .description)")
             else "FIRECRAWL ERROR: \(.error // .)" end'
    ;;
  *) sed -n '2,5p' "$0"; exit 2 ;;
esac
