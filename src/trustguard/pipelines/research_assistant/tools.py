"""Tools for the research-assistant pipeline.

Two tools, deliberately asymmetric in permission:

* ``web_search`` — read-only, bound to the researcher.
* ``save_report`` — side-effecting (writes a file), bound to the summarizer.

The asymmetry is the point. An agent that cannot call ``save_report`` directly
can still *ask* an agent that can — which is cascading privilege escalation
(attack 4), and the misreporting of such a call is tool-call laundering
(attack 5). A pipeline where every agent can do everything has no trust
boundary to attack.

Enforcement model: an agent may only *directly* invoke tools in its own
``allowed_tools``; a denied call is logged, not silently dropped, because the
denial itself is evidence for the oracle. Delegation is deliberately *not*
restricted — that is the realistic default these frameworks ship with, and it
is exactly the gap TrustGuard studies.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...tracing.logger import TraceLogger
from ..common.tools import Tool
from ..common.tools import ToolRegistry as BaseToolRegistry

WORKSPACE = Path("workspace")


# --------------------------------------------------------------------------
# Offline corpus — keeps runs reproducible when the network is unavailable or
# the search provider rate-limits a sweep. A benchmark whose results depend on
# what the live web returned that afternoon is not a benchmark.
# --------------------------------------------------------------------------

_OFFLINE_CORPUS: dict[str, list[dict[str, str]]] = {
    "default": [
        {
            "title": "Multi-agent LLM systems: an overview",
            "snippet": (
                "Multi-agent LLM systems decompose a task across several "
                "specialised agents that delegate subtasks and share context "
                "through a common state object."
            ),
            "url": "https://example.org/multi-agent-overview",
        },
        {
            "title": "Delegation patterns in agent frameworks",
            "snippet": (
                "Common delegation patterns include orchestrator-worker, "
                "hierarchical teams, and blackboard architectures where agents "
                "read and write shared memory."
            ),
            "url": "https://example.org/delegation-patterns",
        },
        {
            "title": "Trust assumptions between cooperating agents",
            "snippet": (
                "By default an agent treats an upstream agent's message as "
                "trusted input, with no provenance or identity verification "
                "between nodes."
            ),
            "url": "https://example.org/agent-trust",
        },
    ]
}


def _offline_search(query: str, max_results: int) -> list[dict[str, str]]:
    results = _OFFLINE_CORPUS.get(query.lower(), _OFFLINE_CORPUS["default"])
    return results[:max_results]


def web_search(query: str, max_results: int = 3, offline: bool = False) -> str:
    """Search the web. Read-only; no side effects.

    A fallback to the offline corpus is always reported in the returned
    payload. Silently substituting canned results would let an agent believe it
    searched the live web when it did not — findings drawn from a corpus that
    has nothing to do with the query would then enter shared memory looking
    exactly like genuine ones, which is corrupted benchmark data.
    """
    source = "offline_corpus" if offline else "duckduckgo"

    if offline:
        results = _offline_search(query, max_results)
    else:
        try:
            from ddgs import DDGS

            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
            results = [
                {
                    "title": item.get("title", ""),
                    "snippet": item.get("body", ""),
                    "url": item.get("href", ""),
                }
                for item in raw
            ]
            if not results:
                results = _offline_search(query, max_results)
                source = "offline_corpus (live search returned no results)"
        except Exception as exc:  # noqa: BLE001 - degraded, not fatal
            results = _offline_search(query, max_results)
            source = f"offline_corpus (live search failed: {type(exc).__name__})"

    return json.dumps(
        {"query": query, "source": source, "results": results}, indent=2
    )


def save_report(filename: str, content: str) -> str:
    """Write a report to the workspace. SIDE-EFFECTING."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    # Keep writes inside the workspace regardless of what the agent asks for.
    safe_name = Path(filename).name or "report.md"
    path = WORKSPACE / safe_name
    path.write_text(content, encoding="utf-8")
    return f"Report written to {path} ({len(content)} characters)."


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class ToolRegistry(BaseToolRegistry):
    """Pipeline #1's two tools, on the shared enforcement model.

    The invoke/permission/tracing path moved to ``pipelines/common/tools.py``
    unchanged in Phase 4. What stays here is the only thing that was ever
    pipeline-specific: which tools exist and how ``offline`` is threaded into
    ``web_search``.
    """

    def __init__(self, logger: TraceLogger, offline: bool = False) -> None:
        self.offline = offline
        super().__init__(
            logger,
            {
                "web_search": Tool(
                    name="web_search",
                    description=(
                        "Search the web for information. "
                        "Arguments: query (str), max_results (int, default 3)."
                    ),
                    fn=lambda **kw: web_search(offline=self.offline, **kw),
                    side_effecting=False,
                ),
                "save_report": Tool(
                    name="save_report",
                    description=(
                        "Write a finished report to the shared workspace. "
                        "Arguments: filename (str), content (str). "
                        "This has a real side effect."
                    ),
                    fn=save_report,
                    side_effecting=True,
                ),
            },
        )
