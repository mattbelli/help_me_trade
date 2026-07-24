"""Backfill model2vec embeddings for existing copilot memories (Phase 2 hybrid retriever).

Idempotent + re-runnable: embeds ACTIVE rows that lack a current-model embedding, in
batches. Fail-soft — if the embedder is unavailable it reports and exits non-zero
without touching any row. New memories get embedded at write time; this is for the
rows that predate the feature (or a model swap).

Usage:
  .venv/bin/python tools/memory_backfill_embeddings.py [--db PATH] [--batch 128]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="DB path (default: app default DB)")
    ap.add_argument("--batch", type=int, default=128, help="embed batch size")
    args = ap.parse_args()

    import db.base as db_base
    from ai.memories.embedding import EMBED_MODEL_ID, embed_documents, is_available
    from db.ai_tables import memories_needing_embedding, set_memory_embedding

    db_path = Path(args.db) if args.db else db_base.DEFAULT_DB_PATH

    if not is_available():
        print(
            "embedder unavailable (model2vec/model not loaded, or "
            "MEMORY_EMBEDDINGS_ENABLED=0). Nothing backfilled."
        )
        return 1

    todo = memories_needing_embedding(EMBED_MODEL_ID, db_path=db_path)
    if not todo:
        print(f"nothing to backfill — all active rows carry a {EMBED_MODEL_ID} embedding.")
        return 0

    print(f"backfilling {len(todo)} rows with {EMBED_MODEL_ID} ...")
    done = 0
    for i in range(0, len(todo), args.batch):
        chunk = todo[i : i + args.batch]
        blobs = embed_documents([content for _, content in chunk])
        if blobs is None:
            print("embedder returned no vectors mid-run; aborting.")
            return 1
        for (mid, _), blob in zip(chunk, blobs):
            set_memory_embedding(mid, blob, EMBED_MODEL_ID, db_path=db_path)
            done += 1
        print(f"  {done}/{len(todo)}")

    print(f"done: {done} rows embedded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
