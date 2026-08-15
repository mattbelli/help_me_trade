"""Vol/volume abnormality scanner — perception source (magnitude tell, not a verdict)."""

from signals.abnormality.constants import CAVEAT, KIND_ABNORMALITY, KIND_PAIRED, LABEL
from signals.abnormality.detector import AbnormalityDetector
from signals.abnormality.engine import FireThresholds, evaluate_boundary

__all__ = [
    "AbnormalityDetector",
    "CAVEAT",
    "FireThresholds",
    "KIND_ABNORMALITY",
    "KIND_PAIRED",
    "LABEL",
    "evaluate_boundary",
]
