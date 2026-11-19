"""TrustGuard's attack-success oracle (Phase 3).

Reads a ``RunTrace`` and decides whether the staged attack succeeded, against
the frozen success definitions in `docs/phase_0_threat_model.md` §3. Hybrid by
design: deterministic checks settle the mechanical cases, an LLM judge settles
the semantic residue.

The oracle is a separate module from the probes on purpose. A probe stages an
attack and records what it staged; it never grades its own run (decision log,
2026-08-21). That separation is what lets the oracle be validated against
independent human labels on the same traces.

Each attack has its own checker, its own version and its own ``validated``
flag, all stamped into the verdict. Phase 3's agreement number is a measurement
of **attack 2's** checker and nothing else; a checker added in Phase 4 inherits
none of it until it is validated the same way.

Validation, scope, and the agreement number: `docs/phase_3_oracle.md`.
The validation set itself: `docs/validation_set/`.
"""

from __future__ import annotations

from .heuristics import Attack2Facts, extract_attack2_facts
from .heuristics_attack2_td import Attack2TdFacts, extract_attack2_td_facts
from .heuristics_attack3 import Attack3Facts, extract_attack3_facts
from .heuristics_attack3_td import Attack3TdFacts, extract_attack3_td_facts
from .judge import Judge
from .judge_attack3 import Attack3Judge
from .judge_attack3_td import Attack3TdJudge
from .metrics import cohens_kappa, confusion, format_report, score
from .oracle import (
    CHECKERS,
    CHECKERS_BY_PIPELINE,
    CHECKERS_TASK_DELEGATION,
    ORACLE_VERSION,
    Oracle,
    UnsupportedAttack,
)

__all__ = [
    "CHECKERS",
    "CHECKERS_BY_PIPELINE",
    "CHECKERS_TASK_DELEGATION",
    "ORACLE_VERSION",
    "Attack2Facts",
    "Attack2TdFacts",
    "Attack3Facts",
    "Attack3TdFacts",
    "Attack3Judge",
    "Attack3TdJudge",
    "Judge",
    "Oracle",
    "UnsupportedAttack",
    "cohens_kappa",
    "confusion",
    "extract_attack2_facts",
    "extract_attack2_td_facts",
    "extract_attack3_facts",
    "extract_attack3_td_facts",
    "format_report",
    "score",
]
