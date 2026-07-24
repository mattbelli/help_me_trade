"""Probe CLI.

    .venv/bin/python        -m research.kronos_probe.run fetch --window primary
    .venv-kronos/bin/python -m research.kronos_probe.run probe --window primary

`fetch` deliberately does not import torch, so the data pull never depends on the
experimental venv.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import numpy as np
import pandas as pd

from research.kronos_probe import barriers, baseline, config, data, score

logger = logging.getLogger(__name__)


def _context_frame(bars: list[dict], end_idx: int) -> pd.DataFrame:
    window = bars[end_idx - config.CONTEXT_BARS + 1 : end_idx + 1]
    frame = pd.DataFrame(
        {
            "open": [b["o"] for b in window],
            "high": [b["h"] for b in window],
            "low": [b["l"] for b in window],
            "close": [b["c"] for b in window],
            "volume": [b["v"] for b in window],
            # ET, tz-naive: the model's time features should see 09:30 as the open.
            "timestamps": [b["et"].replace(tzinfo=None) for b in window],
        }
    )
    frame["amount"] = frame["volume"] * frame[["open", "high", "low", "close"]].mean(
        axis=1
    )
    return frame


def _future_timestamps(bars: list[dict], end_idx: int) -> pd.Series:
    forward = bars[end_idx + 1 : end_idx + 1 + config.HORIZON_BARS]
    return pd.Series([b["et"].replace(tzinfo=None) for b in forward])


def _sampled_closes(rollouts, context, future_ts) -> np.ndarray:
    """Chunk the draws so a 200-path batch never OOMs the 8 GB card."""
    chunks = []
    remaining = config.N_PATHS
    while remaining > 0:
        take = min(config.PATH_BATCH, remaining)
        chunks.append(
            rollouts.sample_closes(
                context, future_ts, n_paths=take, horizon=config.HORIZON_BARS
            )
        )
        remaining -= take
    return np.concatenate(chunks, axis=0)


def cmd_fetch(window: str) -> int:
    counts = data.fetch(window)
    for symbol, n in counts.items():
        print(f"{symbol}: {n} bars")
    thin = [s for s, n in counts.items() if n == 0]
    if thin:
        print(f"WARNING: no bars for {', '.join(thin)}", file=sys.stderr)
        return 1
    return 0


def _write_summary(window: str, records: list[dict]) -> dict:
    summary = {
        "path_test": score.summarize(records),
        "volatility_test": score.summarize_volatility(records),
    }
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / f"{window}_summary.json").write_text(
        json.dumps(summary, indent=2)
    )
    return summary


def cmd_score(window: str) -> int:
    """Re-score saved records without re-running the rollouts.

    The records hold every raw input the scorers need, so a scoring change never
    costs another GPU day — and a long run in flight can't be silently scored by a
    stale copy of score.py that was imported at its start.
    """
    path = config.OUTPUT_DIR / f"{window}_records.json"
    if not path.exists():
        print(f"No records at {path}", file=sys.stderr)
        return 1
    print(json.dumps(_write_summary(window, json.loads(path.read_text())), indent=2))
    return 0


def cmd_probe(window: str, limit: int | None) -> int:
    from research.kronos_probe.kronos import Rollouts

    rollouts = Rollouts()
    rng = np.random.default_rng(20260724)
    records: list[dict] = []

    for symbol in config.SYMBOLS:
        bars = data.load_rth(symbol, window)
        indices = data.eval_indices(bars, window)
        if limit is not None:
            indices = indices[:limit]
        logger.info("%s: %d evaluation points", symbol, len(indices))

        for i in indices:
            context = _context_frame(bars, i)
            closes = context["close"].to_numpy(dtype=float)
            session_dates = [
                b["et"].date() for b in bars[i - config.CONTEXT_BARS + 1 : i + 1]
            ]
            returns = barriers.intraday_log_returns(closes, session_dates)
            sigma = barriers.ewma_sigma(returns, config.EWMA_LAMBDA)
            if not np.isfinite(sigma) or sigma <= 0:
                continue

            move = config.BARRIER_SIGMA_MULT * sigma * np.sqrt(config.HORIZON_BARS)
            anchor = float(closes[-1])
            upper, lower = anchor * np.exp(move), anchor * np.exp(-move)

            forward = np.array(
                [b["c"] for b in bars[i + 1 : i + 1 + config.HORIZON_BARS]], dtype=float
            )
            actual = int(barriers.resolve(forward, upper, lower)[0])

            future_ts = _future_timestamps(bars, i)
            kronos_closes = _sampled_closes(rollouts, context, future_ts)
            boot_closes = baseline.bootstrap_paths(
                returns,
                anchor,
                horizon=config.HORIZON_BARS,
                n_paths=config.N_PATHS,
                sigma=sigma,
                rng=rng,
            )

            # Volatility leg — free from the same rollouts. Kronos's sigma is the
            # dispersion of its own terminal log returns; the realized proxy is the
            # actual horizon log return (squared inside the scorer).
            realized_return = float(np.log(forward[-1] / anchor))
            garch = baseline.garch_sigma_h(returns, config.HORIZON_BARS)

            records.append(
                {
                    "symbol": symbol,
                    "ts": bars[i]["t"],
                    "session_date": bars[i]["et"].date().isoformat(),
                    "actual": actual,
                    "realized_return": realized_return,
                    "kronos_sigma": float(np.log(kronos_closes[:, -1] / anchor).std()),
                    "ewma_sigma": float(sigma * np.sqrt(config.HORIZON_BARS)),
                    "garch_sigma": garch,
                    "kronos_counts": np.bincount(
                        barriers.resolve(kronos_closes, upper, lower),
                        minlength=barriers.N_OUTCOMES,
                    ).tolist(),
                    "bootstrap_counts": np.bincount(
                        barriers.resolve(boot_closes, upper, lower),
                        minlength=barriers.N_OUTCOMES,
                    ).tolist(),
                }
            )

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / f"{window}_records.json").write_text(json.dumps(records))
    print(json.dumps(_write_summary(window, records), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Kronos vs bootstrap path probe.")
    parser.add_argument("command", choices=("fetch", "probe", "score"))
    parser.add_argument("--window", default="primary", choices=tuple(config.WINDOWS))
    parser.add_argument(
        "--limit", type=int, default=None, help="Max eval points per symbol (smoke run)"
    )
    args = parser.parse_args(argv)
    if args.command == "fetch":
        return cmd_fetch(args.window)
    if args.command == "score":
        return cmd_score(args.window)
    return cmd_probe(args.window, args.limit)


if __name__ == "__main__":
    sys.exit(main())
