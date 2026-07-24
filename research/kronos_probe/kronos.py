"""Kronos rollout sampling — the only module that needs torch and the GPU.

Runs under `.venv-kronos`, against an upstream clone at $KRONOS_REPO (default
`.venv-kronos/src/Kronos`). Upstream code is NOT vendored into this repo.

Why we don't call `KronosPredictor.predict()`: it averages its samples
(`auto_regressive_inference` ends with `np.mean(preds, axis=1)`), which collapses
exactly the path distribution the probe is measuring. Instead we replicate that
method's normalization and call `generate()` with the context repeated across the
BATCH dimension at sample_count=1, so every returned row is one independent path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.kronos_probe import config

_DEFAULT_REPO = config.ROOT / ".venv-kronos" / "src" / "Kronos"


def _upstream() -> Path:
    repo = Path(os.environ.get("KRONOS_REPO", _DEFAULT_REPO))
    if not (repo / "model" / "kronos.py").exists():
        raise RuntimeError(
            f"Kronos upstream not found at {repo}. Clone it:\n"
            f"  git clone --depth 1 https://github.com/shiyu-coder/Kronos {repo}"
        )
    return repo


class Rollouts:
    """Loads the model once, then samples path matrices per evaluation point."""

    def __init__(self) -> None:
        repo = _upstream()
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        import torch
        from model import Kronos, KronosPredictor, KronosTokenizer
        from model.kronos import calc_time_stamps

        self._torch = torch
        self._calc_time_stamps = calc_time_stamps
        tokenizer = KronosTokenizer.from_pretrained(config.TOKENIZER_NAME)
        model = Kronos.from_pretrained(config.MODEL_NAME)
        self.predictor = KronosPredictor(
            model, tokenizer, max_context=config.CONTEXT_BARS
        )
        self.predictor.tokenizer.eval()
        self.predictor.model.eval()

    def sample_closes(
        self,
        context: pd.DataFrame,
        future_timestamps: pd.Series,
        *,
        n_paths: int,
        horizon: int,
    ) -> np.ndarray:
        """Return (n_paths, horizon) sampled close prices.

        ``context`` needs columns open/high/low/close/volume/amount and a
        ``timestamps`` column of tz-naive datetimes.
        """
        price_cols = self.predictor.price_cols
        feature_cols = price_cols + [self.predictor.vol_col, self.predictor.amt_vol]

        x = context[feature_cols].to_numpy(dtype=np.float32)
        x_mean, x_std = x.mean(axis=0), x.std(axis=0)
        x_norm = np.clip(
            (x - x_mean) / (x_std + 1e-5), -self.predictor.clip, self.predictor.clip
        )

        x_stamp = self._calc_time_stamps(context["timestamps"]).to_numpy(np.float32)
        y_stamp = self._calc_time_stamps(future_timestamps).to_numpy(np.float32)

        # Batch dimension carries the independent draws; sample_count stays 1 so
        # upstream's internal mean is a no-op.
        x_batch = np.repeat(x_norm[np.newaxis, :], n_paths, axis=0)
        x_stamp_batch = np.repeat(x_stamp[np.newaxis, :], n_paths, axis=0)
        y_stamp_batch = np.repeat(y_stamp[np.newaxis, :], n_paths, axis=0)

        with self._torch.no_grad():
            preds = self.predictor.generate(
                x_batch,
                x_stamp_batch,
                y_stamp_batch,
                horizon,
                config.SAMPLE_TEMPERATURE,
                0,
                config.SAMPLE_TOP_P,
                1,
                False,
            )

        preds = preds * (x_std + 1e-5) + x_mean
        close_idx = price_cols.index("close")
        return np.asarray(preds[:, :, close_idx], dtype=float)
