#!/usr/bin/env python
"""Create the Python-owned flow schema in a scratch DB for the Rust collector.

`collectors/flow_rs` writes rows only — `options_flow_events`,
`bus_ingest_sequences`, `index_pressure_minutes`, `index_pressure_days` — and
refuses to start against a DB that lacks them. Run:
  .venv/bin/python tools/flow_db_init.py /tmp/hmt_flow_ab/rs/trading.db
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    db = Path(args[0])
    db.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from db.index_pressure import init_index_pressure_table
    from db.options_flow import init_db

    init_db(db)
    init_index_pressure_table(db)
    print(f"initialized {db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
