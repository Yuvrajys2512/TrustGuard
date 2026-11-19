"""Agreement statistics for the Phase 3 validation set.

Pure statistics over two aligned lists of binary labels. Nothing in this module
knows what an oracle is, what an attack is, or which label is "the answer" —
that separation is deliberate, because these functions were written *before*
the validation set was labelled and must not be able to influence a label.

The phase spec suggests pandas for this analysis. A 46-row, two-by-two
confusion matrix does not need a dataframe library, and the project's
dependency list is short on purpose, so this is stdlib. The deviation is
recorded in `docs/phase_3_oracle.md`.

**On Cohen's kappa with skewed marginals.** Kappa corrects raw agreement for
the agreement expected by chance, which is the right instinct here: a
validation set that is 70% one class makes 70% raw agreement worthless. But
kappa is itself unstable when the marginals are very skewed — the *kappa
paradox*, where high raw agreement produces a low kappa because chance
agreement is nearly as high. Both numbers are therefore reported side by side
with the confusion matrix, and none of the three is quoted alone.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Sequence


@dataclass
class ConfusionMatrix:
    """Counts for a binary decision, with the reference label as truth.

    Naming follows the attack-detection reading: "positive" means the run is
    an attack success. So a false positive is the oracle crying wolf — it
    reported a success where the labeller saw none, which is the error mode
    that would inflate every downstream number in the benchmark.
    """

    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn


def confusion(
    reference: Sequence[bool], predicted: Sequence[bool]
) -> ConfusionMatrix:
    if len(reference) != len(predicted):
        raise ValueError(
            f"length mismatch: {len(reference)} reference vs "
            f"{len(predicted)} predicted"
        )
    tp = sum(1 for r, p in zip(reference, predicted) if r and p)
    fp = sum(1 for r, p in zip(reference, predicted) if not r and p)
    tn = sum(1 for r, p in zip(reference, predicted) if not r and not p)
    fn = sum(1 for r, p in zip(reference, predicted) if r and not p)
    return ConfusionMatrix(tp=tp, fp=fp, tn=tn, fn=fn)


def cohens_kappa(reference: Sequence[bool], predicted: Sequence[bool]) -> float:
    """Chance-corrected agreement.

    Returns 0.0 rather than NaN in the degenerate case where both raters used
    exactly one class for everything: observed and expected agreement are then
    both 1.0, and 0/0 is conventionally reported as no measurable
    chance-corrected agreement. That case is itself a finding — it means the
    validation set had no class balance to measure against — so callers should
    read the confusion matrix, not this number, when it happens.
    """
    cm = confusion(reference, predicted)
    n = cm.n
    if n == 0:
        return 0.0
    observed = (cm.tp + cm.tn) / n
    p_ref_pos = (cm.tp + cm.fn) / n
    p_pred_pos = (cm.tp + cm.fp) / n
    expected = p_ref_pos * p_pred_pos + (1 - p_ref_pos) * (1 - p_pred_pos)
    if math.isclose(expected, 1.0):
        return 0.0
    return (observed - expected) / (1 - expected)


def wilson_interval(
    successes: int, total: int, z: float = 1.96
) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Reported because a headline agreement number computed on ~46 runs carries
    an interval wide enough to change how it should be read, and quoting the
    point estimate alone would overstate what this sample can support. Wilson
    rather than the normal approximation because the latter misbehaves badly
    near 0 and 1, which is exactly where a good agreement number sits.
    """
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = (
        z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def score(
    reference: Sequence[bool], predicted: Sequence[bool]
) -> dict[str, Any]:
    """Every number Phase 3 reports for one predictor, in one dict."""
    cm = confusion(reference, predicted)
    n = cm.n
    correct = cm.tp + cm.tn
    agreement = correct / n if n else 0.0
    lo, hi = wilson_interval(correct, n)

    precision = cm.tp / (cm.tp + cm.fp) if (cm.tp + cm.fp) else None
    recall = cm.tp / (cm.tp + cm.fn) if (cm.tp + cm.fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall
        else None
    )
    
    return {
        "n": n,
        "agreement": agreement,
        "agreement_ci95": [lo, hi],
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "cohens_kappa": cohens_kappa(reference, predicted),
        "confusion": asdict(cm),
        "reference_positive_rate": (cm.tp + cm.fn) / n if n else 0.0,
        "predicted_positive_rate": (cm.tp + cm.fp) / n if n else 0.0,
    }


def format_report(name: str, s: dict[str, Any]) -> str:
    """One predictor's numbers as a readable block."""

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x * 100:5.1f}%"

    cm = s["confusion"]
    lo, hi = s["agreement_ci95"]
    return "\n".join(
        [
            f"{name}  (n={s['n']})",
            f"  agreement   {pct(s['agreement'])}   "
            f"95% CI [{lo * 100:.1f}%, {hi * 100:.1f}%]",
            f"  precision   {pct(s['precision'])}",
            f"  recall      {pct(s['recall'])}",
            f"  F1          {pct(s['f1'])}",
            f"  kappa       {s['cohens_kappa']:.3f}",
            f"  confusion   tp={cm['tp']} fp={cm['fp']} "
            f"tn={cm['tn']} fn={cm['fn']}",
        ]
    )
