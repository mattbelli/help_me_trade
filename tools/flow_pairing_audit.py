#!/usr/bin/env python
"""flow_pairing_audit.py — the nightly live-vs-batch pairing audit + the
same-day degradation check (LIVE_FLOW_PAIRING_SPEC "corpus firewall": each
night the REST join doubles as the audit — live match rate vs batch match
rate per session = the daemon's measured drop rate, logged, alarmed).

Two modes, one parser:

  --nightly   Step 6 of tools/nightly_flow_capture.sh. For each recent
              session in join_ledger.json not yet audited: reconstruct the
              LIVE match rate from the collector's journald stats lines
              (per-epoch deltas — matched/resolved reset on every matcher
              reconnect; dbn/alp are process-cumulative), compare against
              the batch join, append the row to pairing_audit.json, and
              post one ntfy alarm per breached session.

  --intraday  Cron during RTH. Reads the last ~50 min of stats lines; if
              the a-side wire-lag p50 is degraded or the Alpaca sidecar is
              starved vs the dbn spine, pushes a same-day "codes degraded"
              alert (deduped per day per alarm kind via a state file) — so
              a label-blind day (Fri 2026-08-07 pattern) is visible on the
              phone while it is happening, not the morning after.

Thresholds are calibrated on measured ground truth, recorded inline below.
Empty NTFY_TOPIC or --dry-run prints alarms instead of posting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NPZ_DIR = ROOT / "data" / "alpaca_conditions"
JOIN_LEDGER = NPZ_DIR / "join_ledger.json"
AUDIT_LEDGER = NPZ_DIR / "pairing_audit.json"
INTRADAY_STATE = NPZ_DIR / "pairing_intraday_state.json"
UNIT = "hmt-flow-collector"

# Live enrichment wired 2026-08-03; earlier sessions have no live lane.
LIVE_SINCE = date(2026, 8, 4)

# Calibration (all MEASURED, 2026-08-09):
#   live per-epoch matched on healthy days 08-04..06: 96.6-98.4%; Fri 08-07
#   degraded: 74.3%. Batch join_rate_dbn ledger (260 sessions): median 97.9%,
#   p10 96.2%, degraded tail < 95% (6 sessions incl. 07-31 @85.5%, 08-07
#   @47.8%) — the Addendum-E JOIN_FLOOR (98%) is a corpus-migration gate,
#   141/260 below it, useless as a health alarm. Healthy live-vs-batch gap
#   1-3pts (the meta-cold open blind + reconnect resets). Per-minute a-side
#   wire-lag p50: 12-13ms midday, ~3s for 1-2 min at the open, 10-25s ALL
#   session on 08-07 → share-of-active-minutes cleanly separates (<1% vs
#   ~100%) where a single median would still work but with less margin.
LIVE_FLOOR = 0.90
BATCH_FLOOR = 0.95
GAP_MAX = 0.05
LAG_DEGRADED_MS = 2_000.0
LAG_SHARE_MAX = 0.10
ACTIVE_DBN_PER_MIN = 1_000
INTRADAY_MIN_ACTIVE = 12          # winter cron lands 15 min after the bell
STARVED_ALP_RATIO = 0.5           # Fri 08-07 open ran ~0.33; healthy ~1.0

STATS_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:[+-]\d{2}:?\d{2}|Z)?"
    r".*?python\[(?P<pid>\d+)\].*?pairing shadow(?: FINAL)?: "
    r"dbn=(?P<dbn>[\d,]+) alp=(?P<alp>[\d,]+) "
    r"matched=(?P<matched>[\d,]+) \([\d.]+% of (?P<resolved>[\d,]+) resolved\)"
    r".*? a p50=(?P<a_p50>-?\d+) "
    r".*? resets=(?P<resets>\d+)"
)


@dataclass
class Sample:
    ts: datetime
    pid: int
    dbn: int
    alp: int
    matched: int
    resolved: int
    a_p50: float
    resets: int


def _n(s: str) -> int:
    return int(s.replace(",", ""))


def parse_stats_lines(text: str) -> list[Sample]:
    out = []
    for line in text.splitlines():
        m = STATS_RE.search(line)
        if not m:
            continue
        out.append(Sample(
            ts=datetime.fromisoformat(m["ts"]).replace(tzinfo=timezone.utc),
            pid=int(m["pid"]),
            dbn=_n(m["dbn"]), alp=_n(m["alp"]),
            matched=_n(m["matched"]), resolved=_n(m["resolved"]),
            a_p50=float(m["a_p50"]), resets=int(m["resets"]),
        ))
    return out


def session_summary(samples: list[Sample]) -> dict | None:
    """Collapse a session's stats lines into live totals.

    matched/resolved reset on every matcher reconnect (epoch = consecutive
    run of one (pid, resets) value). Only the epoch containing the window's
    FIRST sample can predate the window, so only it subtracts its first
    line as baseline; every later epoch — a same-pid reconnect or a new
    pid (process restart, all counters back to 0) — started inside the
    window and baselines at 0. dbn/alp only reset with the process, so
    they difference per pid segment with the same first-segment rule.
    Wire-lag is per-minute; only minutes where the spine actually flowed
    count toward the lag stats.
    """
    if not samples:
        return None
    first = samples[0]
    matched = resolved = dbn = alp = 0
    epochs = 1
    lags: list[float] = []
    active = degraded = 0
    epoch_first = pid_first = first
    prev: Sample | None = None

    def flush_epoch(last: Sample) -> None:
        nonlocal matched, resolved
        truncated = (epoch_first.pid, epoch_first.resets) == \
                    (first.pid, first.resets)
        matched += last.matched - (epoch_first.matched if truncated else 0)
        resolved += last.resolved - (epoch_first.resolved if truncated else 0)

    def flush_pid(last: Sample) -> None:
        nonlocal dbn, alp
        truncated = pid_first.pid == first.pid
        dbn += last.dbn - (pid_first.dbn if truncated else 0)
        alp += last.alp - (pid_first.alp if truncated else 0)

    for s in samples:
        if prev is not None:
            if s.pid != prev.pid:
                flush_epoch(prev)
                flush_pid(prev)
                epoch_first = pid_first = s
                epochs += 1
            elif s.resets != prev.resets:
                flush_epoch(prev)
                epoch_first = s
                epochs += 1
            elif s.dbn - prev.dbn >= ACTIVE_DBN_PER_MIN:
                active += 1
                lags.append(s.a_p50)
                if s.a_p50 > LAG_DEGRADED_MS:
                    degraded += 1
        prev = s
    flush_epoch(prev)
    flush_pid(prev)
    return {
        "live_matched": matched,
        "live_resolved": resolved,
        "live_rate": matched / resolved if resolved else None,
        "dbn_prints": dbn,
        "alp_prints": alp,
        "active_minutes": active,
        "lag_p50_median_ms": statistics.median(lags) if lags else None,
        "lag_share_degraded": degraded / active if active else None,
        "epochs": epochs,
    }


def verdict(session: str, summ: dict | None, batch: dict | None,
            *, recent: bool) -> tuple[dict, list[str]]:
    alarms: list[str] = []
    batch_rate = batch.get("join_rate_dbn") if batch else None
    live_rate = summ.get("live_rate") if summ else None

    if summ is None or live_rate is None:
        if recent:
            alarms.append(
                f"no live pairing stats in the journal for {session} — "
                f"collector down or enrichment dead?")
    else:
        if live_rate < LIVE_FLOOR:
            alarms.append(
                f"live match rate {live_rate:.1%} < {LIVE_FLOOR:.0%} floor "
                f"({summ['live_matched']:,}/{summ['live_resolved']:,}) — "
                f"session is partially label-blind")
        share = summ.get("lag_share_degraded")
        if share is not None and share > LAG_SHARE_MAX:
            alarms.append(
                f"wire lag degraded: a-side p50 > {LAG_DEGRADED_MS / 1000:.0f}s "
                f"in {share:.0%} of active minutes "
                f"(median {summ['lag_p50_median_ms']:.0f}ms)")
        if batch_rate is not None:
            gap = batch_rate - live_rate
            if gap > GAP_MAX:
                alarms.append(
                    f"live {live_rate:.1%} vs batch {batch_rate:.1%}: the "
                    f"daemon dropped {gap:.1%} of labels the batch join got")
            elif -gap > GAP_MAX:
                alarms.append(
                    f"batch {batch_rate:.1%} vs live {live_rate:.1%}: Alpaca "
                    f"HISTORICAL tape hole — corpus labels worse than live "
                    f"(08-07 pattern)")
    if batch_rate is not None and batch_rate < BATCH_FLOOR:
        alarms.append(
            f"batch join rate {batch_rate:.1%} < {BATCH_FLOOR:.0%} — "
            f"degraded vendor session")

    row = {
        "live_rate": round(live_rate, 6) if live_rate is not None else None,
        "batch_rate": batch_rate,
        "gap": (round(batch_rate - live_rate, 6)
                if live_rate is not None and batch_rate is not None else None),
        "alarms": alarms,
    }
    if summ is not None:
        row.update({k: summ[k] for k in (
            "live_matched", "live_resolved", "dbn_prints", "alp_prints",
            "active_minutes", "lag_p50_median_ms", "lag_share_degraded",
            "epochs")})
    return row, alarms


def intraday_alarms(samples: list[Sample]) -> list[str]:
    """Same-day degradation check over the trailing stats window.

    Self-gates on tape activity (no calendar): a holiday or pre-open window
    never has INTRADAY_MIN_ACTIVE active minutes, so it stays silent.
    """
    lags: list[float] = []
    dbn = alp = 0
    prev: Sample | None = None
    for s in samples:
        if prev is not None and s.pid == prev.pid:
            d = s.dbn - prev.dbn
            if d >= ACTIVE_DBN_PER_MIN:
                lags.append(s.a_p50)
                dbn += d
                alp += s.alp - prev.alp
        prev = s
    if len(lags) < INTRADAY_MIN_ACTIVE:
        return []
    alarms = []
    med = statistics.median(lags)
    if med > LAG_DEGRADED_MS:
        alarms.append(
            f"lag: Alpaca condition-code wire lag p50 {med / 1000:.1f}s over "
            f"the last {len(lags)} active minutes (healthy ~13ms) — today's "
            f"flow labels are degraded")
    if alp < STARVED_ALP_RATIO * dbn:
        alarms.append(
            f"starved: Alpaca sidecar delivered {alp:,} prints vs "
            f"{dbn:,} on the dbn spine ({alp / dbn:.0%}) — condition codes "
            f"are missing for most of today's tape")
    return alarms


# ---------------------------------------------------------------- plumbing

def _journal(since: str, until: str) -> str:
    res = subprocess.run(
        ["journalctl", "-u", UNIT, "--since", since, "--until", until,
         "-o", "short-iso", "-q", "--no-pager"],
        capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"journalctl failed: {res.stderr.strip()}")
    return res.stdout


def _notify(title: str, body: str, dry_run: bool) -> None:
    topic = os.environ.get("NTFY_TOPIC", "")
    if dry_run or not topic:
        print(f"[alarm] {title}\n{body}")
        return
    url = f"{os.environ.get('NTFY_URL', 'https://ntfy.sh')}/{topic}"
    req = urllib.request.Request(
        url, data=body.encode(),
        headers={"Title": title, "Priority": "high"})
    urllib.request.urlopen(req, timeout=15).read()


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _store(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.rename(path)


def run_nightly(dry_run: bool, force_session: str | None) -> None:
    join = _load(JOIN_LEDGER)
    audit = _load(AUDIT_LEDGER)
    today = datetime.now(timezone.utc).date()
    todo = [force_session] if force_session else sorted(
        s for s in join
        if date.fromisoformat(s) >= max(LIVE_SINCE, today - timedelta(days=7))
        and s not in audit)
    if not todo:
        print("pairing audit: nothing to audit")
        return
    for session in todo:
        # ET session D lives inside UTC day D: 12:00-23:00 UTC covers
        # pre-open through late resolutions in both DST regimes.
        samples = parse_stats_lines(
            _journal(f"{session} 12:00:00 UTC", f"{session} 23:00:00 UTC"))
        recent = (today - date.fromisoformat(session)).days <= 2
        row, alarms = verdict(session, session_summary(samples),
                              join.get(session), recent=recent)
        audit[session] = row
        _store(AUDIT_LEDGER, audit)
        state = "DEGRADED" if alarms else "ok"
        print(f"pairing audit {session}: live={row['live_rate']} "
              f"batch={row['batch_rate']} {state}")
        if alarms:
            _notify(f"flow pairing audit {session}: DEGRADED",
                    "\n".join(f"- {a}" for a in alarms), dry_run)


def run_intraday(dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S UTC")
    alarms = intraday_alarms(parse_stats_lines(_journal(since, "now")))
    if not alarms:
        print("pairing intraday: ok")
        return
    # one push per alarm kind per day — a degraded day must not re-ping
    # on the second cron firing
    state = _load(INTRADAY_STATE)
    day = now.date().isoformat()
    if state.get("date") != day:
        state = {"date": day, "posted": []}
    fresh = [a for a in alarms if a.split(":")[0] not in state["posted"]]
    for a in alarms:
        print(f"pairing intraday: {a}")
    if fresh:
        _notify("flow codes DEGRADED today",
                "\n".join(f"- {a}" for a in fresh), dry_run)
        if not dry_run:
            state["posted"] += [a.split(":")[0] for a in fresh]
            _store(INTRADAY_STATE, state)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--nightly", action="store_true")
    mode.add_argument("--intraday", action="store_true")
    ap.add_argument("--session", help="re-audit one session (overwrites row)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print alarms instead of posting to ntfy")
    args = ap.parse_args()
    if args.nightly:
        run_nightly(args.dry_run, args.session)
    else:
        run_intraday(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
