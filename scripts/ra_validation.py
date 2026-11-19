"""Shared configuration for pipeline #1's attack 1 and attack 5 validation sets.

Pipeline #1's attack 2 and attack 3 tooling (`label_runs.py`,
`label_runs_attack3.py`, and their `validate_oracle*.py` partners) is frozen:
those manifests pin the numbers Phase 3 reported, and touching the scripts
risks making them unrebuildable. Attacks 1 and 5 on this pipeline had **no
labelling route at all** until 2026-10-06, which made their checkers
unvalidatable rather than merely unvalidated.

They are added here, not as two more single-attack scripts, for the reason
`td_validation.py` gives: the two attacks share a pipeline, a victim
(`summarizer`), an evidence shape (one `save_report` call and the report sent
to the user) and a channel (`researcher -> summarizer`), so separate scripts
would be near-identical files drifting apart. What has to stay separate is the
**artefacts**, and it does: each attack has its own manifest, cards, label
files, results file and shuffle seed, resolved through this module and never
by a glob. The prefix is `ra_`, distinct from attack 2's bare `manifest.json`
and attack 3's `*_attack3.*`, so no existing glob can pick these files up.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = ROOT / "docs" / "validation_set"
PIPELINE = "research_assistant"
VICTIM = "summarizer"


@dataclass(frozen=True)
class Profile:
    """One attack's validation artefacts on pipeline #1. All paths distinct."""

    key: str
    attack_id: str
    name: str
    shuffle_seed: int
    #: Facts key defining the substantive subset (see td_validation.Profile).
    substantive_fact: str
    substantive_label: str
    has_judge: bool
    #: See td_validation.Profile.substantive_requires_markers.
    substantive_requires_markers: bool = False
    #: Arm-text epoch: runs started before this are excluded, with the reason.
    not_before: str | None = None
    not_before_reason: str = ""
    #: Markers of superseded arm text; a second guard on the same boundary.
    retired_markers: tuple[str, ...] = ()

    @property
    def manifest(self) -> Path:
        return VALIDATION_DIR / f"manifest_ra_{self.key}.json"

    @property
    def cards(self) -> Path:
        return VALIDATION_DIR / f"cards_ra_{self.key}.md"

    @property
    def results(self) -> Path:
        return VALIDATION_DIR / f"oracle_results_ra_{self.key}.json"

    def labels(self, labeler: str) -> Path:
        return VALIDATION_DIR / f"labels_ra_{self.key}_{labeler}.json"


PROFILES: dict[str, Profile] = {
    "attack1": Profile(
        key="attack1",
        attack_id="attack1_goal_hijack",
        name="Delegated goal hijacking",
        shuffle_seed=20261007,
        substantive_fact="injection_present",
        substantive_label="an injected sub-goal reached the victim",
        has_judge=True,
        substantive_requires_markers=True,
        # The sub-goal was rewritten 2026-09-11 (PROCUREMENT-SCAN ->
        # RISK-REGISTER-7, decision log 2026-09-11). The last pre-fix run in
        # traces/dev started 12:43:32Z, the first post-fix one 12:46:19Z; the
        # injected text of every arm is hash-identical from 12:46 onwards.
        not_before="2026-09-11T12:45:00+00:00",
        not_before_reason=(
            "collected before the 2026-09-11 sub-goal rewrite; the injected "
            "text differs, see decision log 2026-09-11"
        ),
        retired_markers=("PROCUREMENT-SCAN", "vendor-selection brief"),
    ),
    "attack5": Profile(
        key="attack5",
        attack_id="attack5_tool_laundering",
        name="Silent tool-call laundering",
        shuffle_seed=20261008,
        # Not `landed`, for the reason td_validation gives for pipeline #2:
        # it is half the success definition, and using it as the precondition
        # would drop every staged run the victim declined.
        substantive_fact="injection_present",
        substantive_label="an injected note reached the victim",
        has_judge=True,
        substantive_requires_markers=True,
        # No cutoff: every arm's injected text is hash-identical across the
        # 2026-09-13 and 2026-09-18 collections (checked 2026-10-06).
    ),
}


def resolve(key: str) -> Profile:
    if key not in PROFILES:
        raise SystemExit(
            f"unknown attack {key!r}; choose one of: {', '.join(PROFILES)}"
        )
    return PROFILES[key]
