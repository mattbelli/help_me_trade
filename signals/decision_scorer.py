"""Live decision scorer — calibrated win% + EV from offline-trained artifacts."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from research.bars import get_daily_bars
from research.dataset_io import compute_raw_score, featurize_dict
from research.features import compute_features
from signals.trade_context import MIN_BARS_FOR_CONTEXT

logger = logging.getLogger(__name__)

DEFAULT_ARTIFACT_DIR = Path("research/artifacts")
_ARTIFACT_FILES = ("model.json", "calibrator.json", "ev_table.json", "meta.json")
_ARTIFACT_CACHE: dict[str, tuple[float, dict]] = {}


def _artifact_mtime_key(artifact_dir: Path) -> float:
    mtimes = [artifact_dir.joinpath(name).stat().st_mtime for name in _ARTIFACT_FILES]
    return max(mtimes)


def load_artifacts(artifact_dir: str | Path = DEFAULT_ARTIFACT_DIR) -> dict | None:
    """Load model/calibrator/ev_table/meta JSON; cache keyed by dir + max file mtime."""
    path = Path(artifact_dir)
    missing = [name for name in _ARTIFACT_FILES if not path.joinpath(name).is_file()]
    if missing:
        return None

    cache_key = str(path.resolve())
    mtime = _artifact_mtime_key(path)
    cached = _ARTIFACT_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        model = json.loads(path.joinpath("model.json").read_text())
        calibrator = json.loads(path.joinpath("calibrator.json").read_text())
        ev_table = json.loads(path.joinpath("ev_table.json").read_text())
        meta = json.loads(path.joinpath("meta.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load decision artifacts from %s: %s", path, exc)
        return None

    artifacts = {
        "model": model,
        "calibrator": calibrator,
        "ev_table": ev_table,
        "meta": meta,
    }
    _ARTIFACT_CACHE[cache_key] = (mtime, artifacts)
    return artifacts


def _apply_platt(raw: float, calibrator: dict) -> float:
    if "A" in calibrator and "B" in calibrator:
        logit = calibrator["A"] * raw + calibrator["B"]
    elif "coef" in calibrator and "intercept" in calibrator:
        coef = calibrator["coef"]
        if isinstance(coef, list):
            coef = coef[0]
        logit = float(coef) * raw + float(calibrator["intercept"])
    else:
        msg = f"Unknown calibrator keys: {sorted(calibrator.keys())}"
        raise ValueError(msg)
    return float(np.clip(1.0 / (1.0 + np.exp(-logit)), 0.0, 1.0))


def _find_ev_bucket(win_prob: float, ev_table: list[dict]) -> dict | None:
    if not ev_table:
        return None
    for i, bucket in enumerate(ev_table):
        lo = float(bucket["bin_lo"])
        hi = float(bucket["bin_hi"])
        if i < len(ev_table) - 1:
            if lo <= win_prob < hi:
                return bucket
        elif lo <= win_prob <= hi:
            return bucket
    # Out-of-range win_prob has no validated EV — never inherit another
    # bucket's numbers (they flow into phone proposals).
    return None


def _top_factors(
    features: dict,
    model: dict,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    x = featurize_dict(features, model["preprocessing"])
    names = model["feature_names"]
    coefs = model["coefficients"]
    contributions: list[tuple[str, float]] = []
    for i, name in enumerate(names):
        contribution = float(coefs[name]) * float(x[i])
        if contribution != 0.0:
            contributions.append((name, contribution))
    contributions.sort(key=lambda item: abs(item[1]), reverse=True)
    out: list[dict[str, Any]] = []
    for name, contribution in contributions[:limit]:
        out.append(
            {
                "name": name,
                "direction": "bullish" if contribution > 0 else "bearish",
                "contribution": round(contribution, 4),
            }
        )
    return out


def score_features(features: dict, artifacts: dict) -> dict:
    """Score a raw feature dict using loaded artifacts."""
    model = artifacts["model"]
    calibrator = artifacts["calibrator"]
    ev_table = artifacts["ev_table"]
    meta = artifacts["meta"]

    raw = compute_raw_score(features, model)
    win_prob = _apply_platt(raw, calibrator)

    bucket = _find_ev_bucket(win_prob, ev_table) or {}
    ev_raw = bucket.get("ev")
    ci_lo_raw = bucket.get("wilson_ci_lo")
    ci_hi_raw = bucket.get("wilson_ci_hi")

    honesty = meta.get("honesty") or {}
    base_rate_raw = meta.get("base_rate")
    brier_raw = meta.get("brier")

    return {
        "win_prob": round(win_prob, 4),
        "ev_pct": round(float(ev_raw), 2) if ev_raw is not None else None,
        "ci_low": round(float(ci_lo_raw), 4) if ci_lo_raw is not None else None,
        "ci_high": round(float(ci_hi_raw), 4) if ci_hi_raw is not None else None,
        "sufficient": bool(bucket.get("sufficient", False)),
        "base_rate": round(float(base_rate_raw), 4) if base_rate_raw is not None else None,
        "low_confidence": bool(honesty.get("low_confidence", True)),
        "brier": round(float(brier_raw), 4) if brier_raw is not None else None,
        "n_samples": int(meta.get("n_samples", 0)),
        "data_source": meta.get("data_source"),
        "top_factors": _top_factors(features, model),
    }


def score_symbol(
    symbol: str,
    *,
    side: str = "buy",
    artifact_dir: str | Path = DEFAULT_ARTIFACT_DIR,
    db_path: Path | None = None,
) -> dict | None:
    """Score a symbol's current setup; best-effort — never raises."""
    if str(side).strip().lower() != "buy":
        return None

    try:
        artifacts = load_artifacts(artifact_dir)
        if artifacts is None:
            return None

        today = date.today()
        start = today - timedelta(days=800)
        bars_kwargs: dict[str, Any] = {}
        if db_path is not None:
            bars_kwargs["db_path"] = db_path

        bars = get_daily_bars(symbol, start, today, **bars_kwargs)
        if len(bars) < MIN_BARS_FOR_CONTEXT:
            return None

        spy = get_daily_bars("SPY", start, today, **bars_kwargs)
        features = compute_features(
            bars, len(bars) - 1, benchmark_bars=spy or None
        )
        if features is None:
            return None

        return score_features(features, artifacts)
    except Exception:
        logger.exception("Decision score failed for %s", symbol)
        return None
