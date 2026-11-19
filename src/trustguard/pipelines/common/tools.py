"""Tool execution with per-agent permission scope, shared by all pipelines.

This module holds the *enforcement model*, not the tools. Which tools exist is
a property of a pipeline; how a call is authorised, executed and traced is a
property of the benchmark, and it has to be identical everywhere or a
cross-pipeline comparison is measuring the harness instead of the attack.

Enforcement model (unchanged from pipeline #1, decision log 2026-08-20):

* an agent may only *directly* invoke tools in its own ``allowed_tools``;
* a denied call is logged as a tool_result carrying an error, never silently
  dropped, because the denial is evidence the oracle reads;
* delegation is deliberately **not** restricted. An agent that cannot call a
  tool can still ask one that can, and that gap is what attacks 4 and 5 are.

A tool raising is surfaced to the calling agent as text rather than propagated,
for the same reason: an agent's reaction to a failed call is behaviour worth
recording, and a crashed run records nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ...tracing.logger import TraceLogger


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., str]
    side_effecting: bool = False


class ToolRegistry:
    """Executes tools, enforcing per-agent permission scope and tracing both.

    Subclasses supply the tool table for their pipeline and are otherwise
    expected to add nothing: the invoke path is the measured surface.
    """

    def __init__(self, logger: TraceLogger, tools: dict[str, Tool] | None = None) -> None:
        self.logger = logger
        self._tools: dict[str, Tool] = dict(tools or {})

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def describe_for(self, allowed_tools: list[str]) -> str:
        lines = [
            f"- {t.name}: {t.description}"
            for name, t in self._tools.items()
            if name in allowed_tools
        ]
        return "\n".join(lines) if lines else "- (no tools available to you)"

    def invoke(
        self,
        agent: str,
        tool_name: str,
        arguments: dict[str, Any],
        allowed_tools: list[str],
        delegation_id: str | None = None,
    ) -> str:
        """Run a tool on behalf of ``agent``, logging call and result."""
        call_id = self.logger.log_tool_call(
            agent=agent,
            tool_name=tool_name,
            arguments=arguments,
            agent_allowed_tools=allowed_tools,
            delegation_id=delegation_id,
        )

        tool = self._tools.get(tool_name)
        if tool is None:
            error = f"No such tool: {tool_name}"
            self.logger.log_tool_result(
                call_id, agent, tool_name, error=error, side_effecting=False
            )
            return error

        if tool_name not in allowed_tools:
            error = (
                f"Permission denied: agent '{agent}' is not authorised to call "
                f"'{tool_name}'. Its allowed tools are {allowed_tools}."
            )
            self.logger.log_tool_result(
                call_id,
                agent,
                tool_name,
                error=error,
                side_effecting=tool.side_effecting,
            )
            return error

        try:
            result = tool.fn(**arguments)
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
            error = f"{type(exc).__name__}: {exc}"
            self.logger.log_tool_result(
                call_id,
                agent,
                tool_name,
                error=error,
                side_effecting=tool.side_effecting,
            )
            return error

        self.logger.log_tool_result(
            call_id,
            agent,
            tool_name,
            result=result,
            side_effecting=tool.side_effecting,
        )
        return result
