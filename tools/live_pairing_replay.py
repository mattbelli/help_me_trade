"""Replay a captured session through the streaming matcher, diff vs batch.

The validation rig for LIVE_FLOW_PAIRING_SPEC_2026-07-28: both live inputs
already exist on disk for every corpus session — the Databento tcbbo DBN file
IS the spine's tape and the Alpaca condition npz IS the sidecar's tape. This
tool replays them into `StreamingCondMatcher` in simulated arrival order
(arrival = participant ts, plus an optional artificial Alpaca lag to stress
the pending buffers the way a real open-burst queue would), then runs the
frozen batch join on the same arrays and reports per-print agreement.

    python tools/live_pairing_replay.py --date 2026-07-24 \
        --data-dir ~/dev/help_me_trade/data/databento \
        --npz-dir ~/dev/help_me_trade/data/alpaca_conditions
    # buffer stress: add --alpaca-lag-s 300

What replay cannot measure — real wire-arrival lag and live wildcard
coverage per root — is the live shadow's job (spec Inc 1). Everything else
about the matcher is settled here, deterministically.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signals.options_flow.live_pairing import StreamingCondMatcher  # noqa: E402
from tools.condition_labels import capture_complete, match_prints  # noqa: E402

PX = 1e9  # DBN fixed-point scale
CHUNK = 1_000_000


def load_session(session: date, data_dir: Path, npz_dir: Path):
    """Both tapes in the canonical shared contract-id space — the defs-based
    mapping copied from `join_condition_codes` (C/P filter, space-stripped
    OSI), so replay and batch label the identical print population."""
    import databento as dbn

    arr = dbn.DBNStore.from_file(
        data_dir / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    d_ts = arr["ts_event"].astype(np.int64)
    d_iid = arr["instrument_id"].astype(np.int64)
    d_cents = np.round(arr["price"].astype(np.float64) / PX * 100).astype(np.int64)
    d_size = arr["size"].astype(np.int64)
    del arr

    npz = np.load(npz_dir / f"{session.isoformat()}.npz")
    a_sym_names = npz["symbols"].astype("U24")
    a_t = npz["t_ns"].astype(np.int64)
    a_cents = npz["price_cents"].astype(np.int64)
    a_size = npz["size"].astype(np.int64)
    a_cond = npz["cond"].astype("U1")
    a_symi = npz["sym_idx"].astype(np.int64)

    defs = dbn.DBNStore.from_file(
        data_dir / "opra_defs" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    cls = defs["instrument_class"]
    defs = defs[(cls == b"C") | (cls == b"P")]
    def_ids = defs["instrument_id"].astype(np.int64)
    def_syms = np.char.replace(defs["raw_symbol"].astype("U24"), " ", "")
    del defs

    order = np.argsort(def_ids, kind="stable")
    def_ids, def_syms = def_ids[order], def_syms[order]
    pos = np.clip(np.searchsorted(def_ids, d_iid), 0, len(def_ids) - 1)
    d_cid = np.where(def_ids[pos] == d_iid, pos, -1)
    sym_order = np.argsort(def_syms, kind="stable")
    sorted_syms = def_syms[sym_order]
    spos = np.clip(np.searchsorted(sorted_syms, a_sym_names), 0,
                   len(sorted_syms) - 1)
    a_sym_cid = np.where(sorted_syms[spos] == a_sym_names, sym_order[spos], -1)
    a_cid = a_sym_cid[a_symi]

    return (d_cid, d_cents, d_size, d_ts), (a_cid, a_cents, a_size, a_t, a_cond)


def replay(d, a, *, lag_ns: int, sweep_interval_ns: int, horizon_ns: int,
           limit_events: int | None = None):
    """Feed the merged tapes; return (cond_stream, matcher, events/s).

    Stable sort on arrival keeps each side's file order for ties, which is the
    tie-break the batch lexsort applies — rank assignment is identical.
    """
    d_cid, d_cents, d_size, d_ts = d
    a_cid, a_cents, a_size, a_t, a_cond = a
    dk = np.flatnonzero(d_cid >= 0)
    ak = np.flatnonzero(a_cid >= 0)

    n_d, n_a = len(dk), len(ak)
    side = np.concatenate([np.zeros(n_d, np.int8), np.ones(n_a, np.int8)])
    cid = np.concatenate([d_cid[dk], a_cid[ak]])
    cents = np.concatenate([d_cents[dk], a_cents[ak]])
    size = np.concatenate([d_size[dk], a_size[ak]])
    ts = np.concatenate([d_ts[dk], a_t[ak]])
    arrival = np.concatenate([d_ts[dk], a_t[ak] + lag_ns])
    cond = np.concatenate([np.full(n_d, "", "U1"), a_cond[ak]])
    pid = np.concatenate([np.arange(n_d, dtype=np.int64),
                          np.full(n_a, -1, np.int64)])
    order = np.argsort(arrival, kind="stable")
    if limit_events:
        order = order[:limit_events]

    m = StreamingCondMatcher(horizon_ns=horizon_ns)
    cond_stream = np.full(n_d, "", dtype="U1")
    reasons = {"matched": 0, "tolerance": 0, "expired": 0, "evicted": 0}
    next_sweep = int(arrival[order[0]]) + sweep_interval_ns if len(order) else 0
    t0 = time.time()
    fed = 0
    for lo in range(0, len(order), CHUNK):
        sel = order[lo:lo + CHUNK]
        for s, c, p, z, t, ar, cc, i in zip(
            side[sel].tolist(), cid[sel].tolist(), cents[sel].tolist(),
            size[sel].tolist(), ts[sel].tolist(), arrival[sel].tolist(),
            cond[sel].tolist(), pid[sel].tolist(),
        ):
            while ar >= next_sweep:
                for rid, rc, why in m.sweep(next_sweep):
                    cond_stream[rid] = rc
                    reasons[why] += 1
                next_sweep += sweep_interval_ns
            key = (c, p, z)
            if s == 0:
                out = m.on_dbn_print(key, t, i, now_ns=ar)
            else:
                out = m.on_alpaca_trade(key, t, cc, now_ns=ar)
            for rid, rc, why in out:
                cond_stream[rid] = rc
                reasons[why] += 1
        fed += len(sel)
        print(f"  fed {fed:,}/{len(order):,} events "
              f"(pending d={m.pending_dbn:,} a={m.pending_alpaca:,})",
              flush=True)
    if len(order):
        last = int(arrival[order[-1]])
        for rid, rc, why in m.sweep(last + 2 * horizon_ns + sweep_interval_ns):
            cond_stream[rid] = rc
            reasons[why] += 1
    rate = fed / max(time.time() - t0, 1e-9)
    return cond_stream, dk, reasons, m, rate


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--npz-dir", required=True)
    p.add_argument("--alpaca-lag-s", type=float, default=0.0,
                   help="artificial sidecar arrival delay (buffer stress)")
    p.add_argument("--sweep-interval-s", type=float, default=5.0)
    p.add_argument("--horizon-s", type=float, default=600.0)
    p.add_argument("--limit-events", type=int, default=None,
                   help="smoke runs: stop after N merged events")
    a = p.parse_args()

    session = date.fromisoformat(a.date)
    data_dir, npz_dir = Path(a.data_dir).expanduser(), Path(a.npz_dir).expanduser()
    if not capture_complete(session, npz_dir):
        print(f"{session}: no completed Alpaca capture — nothing to replay")
        return 2

    print(f"{session}: loading tapes…", flush=True)
    d, alp = load_session(session, data_dir, npz_dir)
    n_d_res = int((d[0] >= 0).sum())
    n_a_res = int((alp[0] >= 0).sum())
    print(f"  dbn prints {len(d[0]):,} ({n_d_res:,} resolved) | "
          f"alpaca trades {len(alp[0]):,} ({n_a_res:,} resolved)", flush=True)

    cond_stream, dk, reasons, m, rate = replay(
        d, alp,
        lag_ns=int(a.alpaca_lag_s * 1e9),
        sweep_interval_ns=int(a.sweep_interval_s * 1e9),
        horizon_ns=int(a.horizon_s * 1e9),
        limit_events=a.limit_events,
    )
    print(f"streaming: {rate:,.0f} events/s | matched={m.matched:,} "
          f"tolerance={m.tolerance_rejects:,} expired={m.expired:,} "
          f"evicted={m.evicted:,} late_twin={m.cond_after_expiry:,} "
          f"orphans={m.alpaca_orphans:,}", flush=True)
    print(f"  peaks: pending_dbn={m.pending_dbn_peak:,} "
          f"pending_alpaca={m.pending_alpaca_peak:,} keys={m.keys_peak:,}",
          flush=True)
    if a.limit_events:
        print("  (limit-events set — batch comparison skipped: the tapes are "
              "truncated at different points per side)")
        return 0

    d_cid, d_cents, d_size, d_ts = d
    a_cid, a_cents, a_size, a_t, a_cond = alp
    d_rows, a_rows = match_prints(d_cid[dk], d_cents[dk], d_size[dk], d_ts[dk],
                                  a_cid, a_cents, a_size, a_t)
    cond_batch = np.full(len(dk), "", dtype="U1")
    cond_batch[d_rows] = a_cond[a_rows]

    agree = float((cond_stream == cond_batch).mean()) if len(dk) else 1.0
    n_batch = len(d_rows)
    diff = cond_stream != cond_batch
    extra = int((diff & (cond_batch == "")).sum())  # stream matched, batch didn't
    miss = int((diff & (cond_stream == "")).sum())  # batch matched, stream didn't
    clash = int(diff.sum()) - extra - miss  # both matched, different codes
    print(f"batch:     matched={n_batch:,} join_rate={n_batch / max(len(dk), 1):.4f}")
    print(f"AGREEMENT: {agree:.6f} per print "
          f"({int(diff.sum()):,} of {len(dk):,} differ: "
          f"stream-extra={extra:,} stream-miss={miss:,} code-clash={clash:,}) | "
          f"stream match rate {m.matched / max(len(dk), 1):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
