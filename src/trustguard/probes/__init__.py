"""TrustGuard probe engine — the attack library.

One probe per taxonomy attack. Phase 2 shipped attack 2; Phase 4 added the
other four against this same interface. All five now have probes and oracle
checkers, and **every checker except attack 2 on research_assistant is
``validated=False``** — no number from one of them may be reported.

Attack 4 is the one asymmetry in the library: it is staged on
``task_delegation`` only, because pipeline #1's delegation is recorded rather
than enacted, so §3's "chain of delegations" clause has nothing to bite on
there, and adding conditional routing would bump ``PIPELINE_VERSION`` and break
the frozen Phase 3 validation set's trace shape (decision log, 2026-08-20 and
2026-09-09). Its empty cell in the 5x2 matrix is a finding about pipeline #1's
architecture, not missing data; ``priv_escalation.UNSUPPORTED_PIPELINES``
carries the reason as data so a caller gets it rather than a ``KeyError``.
"""

from __future__ import annotations

from .base import Probe
from .context_poison import ContextPoisonProbe
from .goal_hijack import GoalHijackProbe
from .priv_escalation import PrivEscalationProbe
from .role_spoof import RoleSpoofProbe
from .tool_laundering import ToolLaunderingProbe

#: Registry keyed by ``attack_id``. Scripts resolve probes through this so a new
#: attack becomes available to every runner by being added here once.
PROBES: dict[str, type[Probe]] = {
    GoalHijackProbe.attack_id: GoalHijackProbe,
    RoleSpoofProbe.attack_id: RoleSpoofProbe,
    ContextPoisonProbe.attack_id: ContextPoisonProbe,
    PrivEscalationProbe.attack_id: PrivEscalationProbe,
    ToolLaunderingProbe.attack_id: ToolLaunderingProbe,
}

__all__ = [
    "PROBES",
    "ContextPoisonProbe",
    "GoalHijackProbe",
    "Probe",
    "PrivEscalationProbe",
    "RoleSpoofProbe",
    "ToolLaunderingProbe",
]
