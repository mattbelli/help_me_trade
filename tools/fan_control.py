#!/usr/bin/env python3
"""Temperature-driven fan control for serv's NZXT H1 V2, plus thermal logging.

Background: the H1 V2 case controller (USB 1e71:2015) owns both case fans AND
the AIO pump. Nothing on the motherboard touches them, so BIOS fan curves are
irrelevant to CPU cooling on this box. Before this daemon existed, both fans sat
at a fixed 60% forever with no temperature feedback at all. The pump is
fixed-speed in firmware and is monitored here but never written to.

Design constraints that matter:

  * FLOOR = 60%, the duty the box has run at for three years. The curve only
    ever adds cooling, never removes it, so this daemon cannot regress the
    thermal status quo — the worst case is that it does nothing.
  * The device is stateless-persistent: it holds the last duty written to it
    forever. If this process dies, the fan stays where it was left. Hence the
    60% floor plus the FAILSAFE_DUTY written on shutdown — failure is loud,
    never silent-and-hot.
  * Zen 3 Tctl swings ~10 C sub-second on single-thread boost. Reacting to raw
    samples makes the fan hunt continuously and buys nothing, because a liquid
    loop's thermal mass responds over ~1-2 minutes. So the curve reads a
    smoothed mean and only writes when the band actually changes.

Usage:  fan_control.py [--dry-run] [--interval SECONDS]
Needs root for hidraw access.
"""

from __future__ import annotations

import argparse
import csv
import logging
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from liquidctl import find_liquidctl_devices

# (upper_bound_celsius, duty_percent) — first band whose bound is not exceeded wins.
CURVE = [(65, 60), (70, 70), (75, 80), (80, 90), (999, 100)]
FLOOR_DUTY = 60
FAILSAFE_DUTY = 80  # written on exit; loud but safe if the daemon is gone
HYSTERESIS_C = 4.0  # must fall this far below a band edge before stepping down
SMOOTH_WINDOW_S = 30
LOG_CSV = Path("/home/mbelli/dev/help_me_trade/data/thermal.csv")
CSV_FIELDS = [
    "ts", "tctl_c", "tctl_smooth_c", "tccd1_c", "nvme_c", "gpu_c",
    "load1", "fan1_duty", "fan2_duty", "fan1_rpm", "fan2_rpm", "pump_rpm",
]

log = logging.getLogger("fan_control")
_running = True


def _stop(signum, _frame):
    global _running
    log.info("signal %s — shutting down", signum)
    _running = False


def _hwmon(name: str) -> Path | None:
    for p in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (p / "name").read_text().strip() == name:
                return p
        except OSError:
            continue
    return None


K10 = _hwmon("k10temp")
NVME = _hwmon("nvme")


def _millideg(path: Path | None, leaf: str) -> float | None:
    if path is None:
        return None
    try:
        return int((path / leaf).read_text()) / 1000.0
    except (OSError, ValueError):
        return None


def duty_for(temp: float, current_duty: int) -> int:
    """Curve lookup with downward hysteresis, floored at FLOOR_DUTY."""
    target = FLOOR_DUTY
    for bound, duty in CURVE:
        if temp <= bound:
            target = duty
            break
    if target < current_duty:
        # Only step down once we are clearly inside the lower band, so that a
        # temperature sitting on a boundary does not oscillate the fan.
        lowered = FLOOR_DUTY
        for bound, duty in CURVE:
            if temp <= bound - HYSTERESIS_C:
                lowered = duty
                break
        else:
            lowered = current_duty
        target = min(current_duty, max(lowered, FLOOR_DUTY))
    return max(FLOOR_DUTY, min(100, target))


def read_status(dev) -> dict:
    out = {}
    for key, value, _unit in dev.get_status():
        k = key.lower()
        if "fan 1 duty" in k:
            out["fan1_duty"] = value
        elif "fan 2 duty" in k:
            out["fan2_duty"] = value
        elif "fan 1 speed" in k:
            out["fan1_rpm"] = value
        elif "fan 2 speed" in k:
            out["fan2_rpm"] = value
        elif "pump speed" in k:
            out["pump_rpm"] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="log and compute targets but never write to the device")
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout
    )
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    dev = next(
        (d for d in find_liquidctl_devices() if "H1 V2" in d.description), None
    )
    if dev is None:
        log.error("no NZXT H1 V2 found (need root for hidraw?)")
        return 1

    dev.connect()
    log.info("connected: %s%s", dev.description, " [DRY RUN]" if args.dry_run else "")

    window = deque(maxlen=max(1, int(SMOOTH_WINDOW_S / args.interval)))
    applied = {"fan1": None, "fan2": None}

    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_CSV.exists() or LOG_CSV.stat().st_size == 0

    try:
        with LOG_CSV.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if new_file:
                writer.writeheader()

            while _running:
                tctl = _millideg(K10, "temp1_input")
                if tctl is None:
                    log.warning("k10temp unreadable; holding last duty")
                    time.sleep(args.interval)
                    continue
                window.append(tctl)
                smooth = sum(window) / len(window)

                st = read_status(dev)
                cur2 = applied["fan2"] or int(st.get("fan2_duty") or FLOOR_DUTY)
                want2 = duty_for(smooth, cur2)

                # fan1 exhausts the GPU chamber; hold it at the floor until the
                # GPU is actually doing work, then follow the same curve.
                gpu = _gpu_temp()
                cur1 = applied["fan1"] or int(st.get("fan1_duty") or FLOOR_DUTY)
                want1 = duty_for(gpu, cur1) if gpu is not None else FLOOR_DUTY

                for ch, want, cur in (("fan2", want2, cur2), ("fan1", want1, cur1)):
                    if want != cur or applied[ch] is None:
                        if args.dry_run:
                            log.info("[dry-run] %s -> %d%% (was %d%%)", ch, want, cur)
                        else:
                            dev.set_fixed_speed(ch, want)
                            log.info("%s -> %d%% (was %d%%, tctl_smooth %.1fC)",
                                     ch, want, cur, smooth)
                        applied[ch] = want

                # st was read before this cycle's writes, so its duty fields are
                # one cycle stale. Record what we actually applied; the rpm
                # fields stay as-read because the fan genuinely lags the duty.
                st["fan1_duty"] = applied["fan1"]
                st["fan2_duty"] = applied["fan2"]

                writer.writerow({
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "tctl_c": round(tctl, 1),
                    "tctl_smooth_c": round(smooth, 1),
                    "tccd1_c": _fmt(_millideg(K10, "temp3_input")),
                    "nvme_c": _fmt(_millideg(NVME, "temp1_input")),
                    "gpu_c": _fmt(gpu),
                    "load1": open("/proc/loadavg").read().split()[0],
                    **st,
                })
                fh.flush()
                time.sleep(args.interval)
    finally:
        if not args.dry_run:
            try:
                for ch in ("fan1", "fan2"):
                    dev.set_fixed_speed(ch, FAILSAFE_DUTY)
                log.info("failsafe: fans set to %d%%", FAILSAFE_DUTY)
            except Exception as exc:  # noqa: BLE001 - best effort on the way out
                log.error("failsafe write failed: %s", exc)
        dev.disconnect()
    return 0


def _fmt(v):
    return "" if v is None else round(v, 1)


def _gpu_temp() -> float | None:
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        return float(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001 - GPU telemetry is optional
        return None


if __name__ == "__main__":
    sys.exit(main())
