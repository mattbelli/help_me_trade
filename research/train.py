"""Train calibrated score model and export live-scorer artifacts."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from db.base import now_iso
from research.calibration import (
    calibrate,
    calibration_metrics,
    fit_platt,
    fit_score_model,
    out_of_fold_scores,
)
from research.dataset import DEFAULT_LABELED_DB_PATH
from research.dataset_io import load_labeled_dataset
from research.ev import build_ev_table
from research.validation import purged_kfold_indices

logger = logging.getLogger(__name__)

DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"


def train_and_export(
    labeled_db: Path = DEFAULT_LABELED_DB_PATH,
    artifact_dir: Path = DEFAULT_ARTIFACT_DIR,
    *,
    n_splits: int = 5,
    embargo_frac: float = 0.01,
    min_bucket: int = 30,
    created_at: str | None = None,
) -> dict:
    """Full pipeline: load → purged CV → OOF → Platt → EV table → JSON artifacts."""
    data = load_labeled_dataset(labeled_db)
    x, y = data["X"], data["y"]
    returns = data["returns"]
    entry_dates = data["entry_dates"]
    holding_days = data["holding_days"]
    feature_names = data["feature_names"]
    preprocessing = data["preprocessing"]

    folds = purged_kfold_indices(
        entry_dates,
        holding_days,
        n_splits=n_splits,
        embargo_frac=embargo_frac,
    )
    oof_scores = out_of_fold_scores(x, y, folds)
    platt = fit_platt(oof_scores, y)
    calibrated_probs = calibrate(oof_scores, platt)
    metrics = calibration_metrics(calibrated_probs, y)
    ev_table = build_ev_table(calibrated_probs, y, returns, min_bucket=min_bucket)

    final_model = fit_score_model(x, y)
    coefficients = {
        name: float(coef)
        for name, coef in zip(feature_names, final_model.coef_[0], strict=True)
    }

    timestamp = created_at or now_iso()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    model_artifact = {
        "intercept": float(final_model.intercept_[0]),
        "coefficients": coefficients,
        "feature_names": feature_names,
        "preprocessing": preprocessing,
    }
    calibrator_artifact = {"A": platt["A"], "B": platt["B"]}

    n_samples = int(len(y))
    low_confidence = n_samples < 1000
    meta_artifact = {
        "n_samples": n_samples,
        "n_features": len(feature_names),
        "base_rate": metrics["base_rate"],
        "brier": metrics["brier"],
        "reliability": metrics["reliability"],
        "date_range": {
            "start": str(entry_dates[0]),
            "end": str(entry_dates[-1]),
        },
        "data_source": str(labeled_db),
        "created_at": timestamp,
        "honesty": {
            "low_confidence": low_confidence,
            "note": (
                "Win rates are provisional with wide confidence intervals until "
                "the labeled sample reaches ~1,000–10,000 setups."
                if low_confidence
                else "Sample size meets provisional minimum; continue monitoring calibration."
            ),
        },
    }

    (artifact_dir / "model.json").write_text(json.dumps(model_artifact, indent=2))
    (artifact_dir / "calibrator.json").write_text(json.dumps(calibrator_artifact, indent=2))
    (artifact_dir / "ev_table.json").write_text(json.dumps(ev_table, indent=2))
    (artifact_dir / "meta.json").write_text(json.dumps(meta_artifact, indent=2))

    summary = {
        "n_samples": n_samples,
        "base_rate": metrics["base_rate"],
        "brier": metrics["brier"],
        "ev_table": ev_table,
        "low_confidence": low_confidence,
        "artifact_dir": str(artifact_dir),
    }
    return summary


def _print_summary(summary: dict) -> None:
    print(f"Samples: {summary['n_samples']}")
    print(f"Base rate: {summary['base_rate']:.3f}")
    print(f"Brier score: {summary['brier']:.4f}")
    print(f"Low confidence: {summary['low_confidence']}")
    print("\nEV table:")
    for row in summary["ev_table"]:
        if row["n"] == 0:
            continue
        ci = ""
        if row["wilson_ci_lo"] is not None:
            ci = f" CI=[{row['wilson_ci_lo']:.2f},{row['wilson_ci_hi']:.2f}]"
        flag = f" [{row['flag']}]" if row["flag"] else ""
        ev_str = f"{row['ev']:.2f}%" if row["ev"] is not None else "n/a"
        print(
            f"  [{row['bin_lo']:.1f},{row['bin_hi']:.1f}) "
            f"n={row['n']} emp={row['empirical_winrate']:.2f}{ci} ev={ev_str}{flag}"
        )
    print(f"\nArtifacts written to {summary['artifact_dir']}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train calibrated score model")
    parser.add_argument(
        "--labeled-db",
        type=Path,
        default=DEFAULT_LABELED_DB_PATH,
        help="Path to labeled_setups SQLite database",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help="Directory for JSON artifacts",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    summary = train_and_export(
        labeled_db=args.labeled_db,
        artifact_dir=args.artifact_dir,
    )
    _print_summary(summary)


if __name__ == "__main__":
    main()
