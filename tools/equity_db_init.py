#!/usr/bin/env python
"""Create the equity-tape tables in a (scratch) trading.db — for the Rust
collector, which writes rows but never DDL (schema is owned by Python).
Run: .venv/bin/python tools/equity_db_init.py /tmp/rs.db"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    from db.equity_tape import init_db
    from db.fusion import init_fusion_tables

    init_db(path)
    init_fusion_tables(path)
    print(f"initialized {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
