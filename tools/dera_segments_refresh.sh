#!/usr/bin/env bash
# Weekly DERA segment top-up. Idempotent and nearly free: already-downloaded
# quarters are skipped by file check, and SEC publishes a new one only ~4x a
# year, so this does real work on about four runs out of fifty.
#
# Runs in WAL mode by design — segment facts share edgar_facts.db with the
# 124M-fact corpus the backend reads, and a MEMORY-journalled write would lock
# every reader out for the length of the run.
set -euo pipefail
cd "$(dirname "$0")/.."

.venv/bin/python -m research.edgar_dera --backfill
# sub.txt carries each filer's SIC, which is how tickers added by the bulk
# ticker sync get a sector without 20k submissions requests.
.venv/bin/python -m research.edgar_dera --sync-sic
