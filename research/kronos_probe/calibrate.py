"""Instrument calibration: does Kronos's path dispersion match realized volatility?

Run BEFORE the probe, and never after seeing outcome scores. This measures scale
only — it compares the standard deviation of Kronos's terminal log return against
the causal EWMA sigma, and never touches realized outcomes, barriers, or scoring.
A sampler whose paths are 7x too narrow would fail the probe for the wrong reason,
so picking T/top_p on dispersion match is calibrating the instrument, not tuning
for a win.

    .venv-kronos/bin/python -m research.kronos_probe.calibrate [--window nightly_primary]

Result on 2026-07-24 (AAPL, 5 points spread across the primary window), as ratios
of Kronos dispersion to realized sigma — 1.0 is a perfect scale match:

    top_p=0.90 -> 0.61x   (nucleus truncation compounds over 12 steps)
    top_p=0.99 -> 0.91x
    top_p=1.00 -> 0.97x   <- chosen
    T=1.2/p=1.0 -> 1.32x

This run also exposed a bug in the probe's own volatility estimate: 512 RTH 5-minute
bars span ~6.6 sessions, so a naive diff() included 7 overnight gaps and inflated
sigma by up to 2.46x. See barriers.context_log_returns.

Result on 2026-07-25 for the NIGHTLY profile (AAPL, 20 points spread across
nightly_primary, 1-hour bars, H=7). The 5-minute answer does NOT transfer — at this
frequency T=1.0 samples far too WIDE, the opposite of the 5-minute failure mode:

    T=1.0 /p=0.80 -> 0.97x     T=0.85/p=1.00 -> 1.21x
    T=1.0 /p=0.90 -> 1.12x     T=0.80/p=1.00 -> 1.13x
    T=1.0 /p=1.00 -> 1.38x     T=0.75/p=1.00 -> 1.07x
                               T=0.70/p=1.00 -> 1.00x   <- chosen
                               T=0.60/p=1.00 -> 0.86x

top_p=0.80 matches on scale too (0.97x), but nucleus truncation buys that match by
deleting support, and the scored outcome is a barrier touch — a tail event. So scale
is corrected with temperature, which re-weights the full distribution instead.
A 5-point sample first read 1.73x for T=1.0/p=1.0; 20 points read 1.38x. Calibrate on
enough points that the estimate is stable, or the sampler gets chosen by noise.
"""

from __future__ import annotations

import argparse

import numpy as np

from research.kronos_probe import barriers, config, data
from research.kronos_probe.run import _context_frame, _future_timestamps

SETTINGS = (
    (1.0, 0.80),
    (1.0, 0.90),
    (1.0, 1.00),
    (0.85, 1.00),
    (0.8, 1.00),
    (0.75, 1.00),
    (0.7, 1.00),
    (0.6, 1.00),
)
N_PATHS = 100
SYMBOL = "AAPL"
N_CALIBRATION_POINTS = 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sampler dispersion calibration.")
    parser.add_argument("--window", default="primary", choices=tuple(config.WINDOWS))
    window = parser.parse_args(argv).window
    config.activate(window)

    from research.kronos_probe.kronos import Rollouts

    bars = data.load_rth(SYMBOL, window)
    # Spread the sample across the whole window rather than clustering at its start:
    # the nightly profile has ~200 points where the 5-minute one has ~6,300, so a
    # fixed stride would sample two adjacent sessions.
    candidates = data.eval_indices(bars, window)
    indices = candidates[:: max(1, len(candidates) // N_CALIBRATION_POINTS)][
        :N_CALIBRATION_POINTS
    ]
    rollouts = Rollouts()

    header = "  ".join(f"T={t}/p={p:.2f}" for t, p in SETTINGS)
    print(f"date        realized_sigmaH  {header}")
    ratios: dict[tuple[float, float], list[float]] = {s: [] for s in SETTINGS}

    for i in indices:
        context = _context_frame(bars, i)
        future_ts = _future_timestamps(bars, i)
        closes = context["close"].to_numpy(dtype=float)
        session_dates = [
            b["et"].date() for b in bars[i - config.CONTEXT_BARS + 1 : i + 1]
        ]
        sigma_h = barriers.ewma_sigma(
            barriers.context_log_returns(
                closes, session_dates, keep_gaps=config.KEEP_GAP_RETURNS
            ),
            config.EWMA_LAMBDA,
        ) * np.sqrt(config.HORIZON_BARS)

        cells = []
        for temperature, top_p in SETTINGS:
            config.SAMPLE_TEMPERATURE, config.SAMPLE_TOP_P = temperature, top_p
            paths = rollouts.sample_closes(
                context, future_ts, n_paths=N_PATHS, horizon=config.HORIZON_BARS
            )
            implied = float(np.log(paths[:, -1] / closes[-1]).std())
            ratios[(temperature, top_p)].append(implied / sigma_h)
            cells.append(f"{implied:.5f}({implied / sigma_h:.2f}x)")
        print(f"{bars[i]['et'].date()}  {sigma_h:.5f}          {'  '.join(cells)}")

    print("\nmean dispersion ratio (1.0 = matches realized):")
    for setting, values in ratios.items():
        print(f"  T={setting[0]}/p={setting[1]:.2f}: {np.mean(values):.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
