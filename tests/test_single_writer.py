"""trading/single_writer.py — the backend's one-process-per-ledger fence.

Must be checked ACROSS processes: flock is per open-file-description, so a
same-process second acquire() proves nothing.
"""

import subprocess
import sys

from trading import single_writer

_HOLDER = """
import sys, time
sys.path.insert(0, {root!r})
from trading import single_writer
try:
    single_writer.acquire({db!r})
    print("held", flush=True)
except RuntimeError as e:
    print("refused:" + str(e), flush=True)
    sys.exit(3)
sys.stdin.readline()  # hold until parent closes stdin
"""


def _spawn(root, db):
    return subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(root=str(root), db=str(db))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


def test_second_process_refused_while_first_holds(tmp_path):
    root = str(single_writer.__file__).rsplit("/trading/", 1)[0]
    db = tmp_path / "trading.db"

    first = _spawn(root, db)
    assert first.stdout.readline().strip() == "held"
    assert single_writer.lock_path(db).read_text().strip() == str(first.pid)

    second = _spawn(root, db)
    out, _ = second.communicate(input="\n", timeout=30)
    assert second.returncode == 3
    assert out.startswith("refused:") and str(first.pid) in out

    first.communicate(input="\n", timeout=30)  # release
    third = _spawn(root, db)
    assert third.stdout.readline().strip() == "held"
    third.communicate(input="\n", timeout=30)


def test_acquire_is_idempotent_in_process(tmp_path):
    db = tmp_path / "trading.db"
    try:
        p1 = single_writer.acquire(db)
        p2 = single_writer.acquire(db)
        assert p1 == p2 == single_writer.lock_path(db)
    finally:
        single_writer.release()
