"""Trace recorder.

One ``TraceLogger`` per pipeline run. Agents call the ``log_*`` helpers; the
logger keeps the sequence numbering, mirrors events to the console when asked,
and writes the finished ``RunTrace`` to disk as JSON.

The console mirror exists because Phase 1's definition of done is "you can
produce a full, readable trace of every inter-agent message and tool call" — a
human has to be able to read who-said-what-to-whom without opening a JSON file.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import (
    AgentMessagePayload,
    AgentReasoningPayload,
    AgentSpec,
    DelegationPayload,
    ErrorPayload,
    EventType,
    MemoryReadPayload,
    MemoryWritePayload,
    PipelineInputPayload,
    PipelineOutputPayload,
    Payload,
    RunConfig,
    RunTrace,
    ToolCallPayload,
    ToolResultPayload,
    TraceEvent,
)

# ANSI colours, one per event family, so a live trace is skimmable.
_COLORS = {
    EventType.PIPELINE_INPUT: "\033[1;37m",
    EventType.PIPELINE_OUTPUT: "\033[1;37m",
    EventType.DELEGATION: "\033[1;35m",
    EventType.AGENT_MESSAGE: "\033[1;36m",
    EventType.AGENT_REASONING: "\033[0;90m",
    EventType.TOOL_CALL: "\033[1;33m",
    EventType.TOOL_RESULT: "\033[0;33m",
    EventType.MEMORY_WRITE: "\033[1;32m",
    EventType.MEMORY_READ: "\033[0;32m",
    EventType.ERROR: "\033[1;31m",
}
_RESET = "\033[0m"


def _truncate(text: str, limit: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_print(line: str) -> None:
    """Print without letting the console's encoding kill a run.

    Windows terminals default to cp1252, and models routinely emit characters
    outside it (non-breaking hyphens, em dashes, arrows). Losing a whole
    experiment to a console codec is not acceptable — the JSON trace is written
    as UTF-8 regardless, so degrading the *console* view is the right trade.
    """
    try:
        print(line)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(encoding, errors="replace").decode(encoding, errors="replace"))


class TraceLogger:
    def __init__(
        self,
        pipeline: str,
        config: RunConfig,
        agents: list[AgentSpec] | None = None,
        objective: str = "",
        echo: bool = True,
        color: bool = True,
    ) -> None:
        self.trace = RunTrace(
            pipeline=pipeline,
            config=config,
            agents=agents or [],
            objective=objective,
        )
        self.echo = echo
        self.color = color
        self._seq = 0

    # -- core -------------------------------------------------------------

    def _emit(self, payload: Payload) -> TraceEvent:
        event = TraceEvent(seq=self._seq, payload=payload)
        self._seq += 1
        self.trace.events.append(event)
        if self.echo:
            _safe_print(self._render(event))
        return event

    def _render(self, event: TraceEvent) -> str:
        p = event.payload
        kind = event.event_type
        label = kind.value.upper().replace("_", " ")

        if kind is EventType.AGENT_MESSAGE:
            sender = p.from_agent
            if p.claimed_sender and p.claimed_sender != p.from_agent:
                sender = f"{p.from_agent} (claims to be {p.claimed_sender})"
            body = f"{sender} -> {p.to_agent}: {_truncate(p.content)}"
        elif kind is EventType.AGENT_REASONING:
            body = f"{p.agent} thinks: {_truncate(p.reasoning, 300)}"
        elif kind is EventType.DELEGATION:
            body = f"{p.from_agent} -> {p.to_agent}: {_truncate(p.task_description)}"
        elif kind is EventType.TOOL_CALL:
            body = f"{p.agent} calls {p.tool_name}({_truncate(json.dumps(p.arguments, default=str), 160)})"
        elif kind is EventType.TOOL_RESULT:
            outcome = f"ERROR: {p.error}" if p.error else _truncate(p.result)
            body = f"{p.tool_name} -> {outcome}"
        elif kind is EventType.MEMORY_WRITE:
            body = f"{p.agent} writes '{p.key}' [provenance: {p.provenance}]: {_truncate(p.value)}"
        elif kind is EventType.MEMORY_READ:
            body = f"{p.agent} reads '{p.key}'"
        elif kind is EventType.PIPELINE_INPUT:
            body = _truncate(p.objective, 400)
        elif kind is EventType.PIPELINE_OUTPUT:
            body = _truncate(p.output, 400)
        else:
            body = _truncate(p.message)

        line = f"[{event.seq:>3}] {label:<15} | {body}"
        if self.color:
            return f"{_COLORS.get(kind, '')}{line}{_RESET}"
        return line

    # -- typed helpers ----------------------------------------------------

    def log_pipeline_input(self, objective: str) -> None:
        self.trace.objective = objective
        self._emit(PipelineInputPayload(objective=objective))

    def log_pipeline_output(self, output: str) -> None:
        self.trace.final_output = output
        self._emit(PipelineOutputPayload(output=output))

    def log_message(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        claimed_sender: str | None = None,
        claimed_authority: str | None = None,
    ) -> None:
        self._emit(
            AgentMessagePayload(
                from_agent=from_agent,
                to_agent=to_agent,
                content=content,
                claimed_sender=claimed_sender or from_agent,
                claimed_authority=claimed_authority,
            )
        )

    def log_reasoning(self, agent: str, reasoning: str) -> None:
        self._emit(AgentReasoningPayload(agent=agent, reasoning=reasoning))

    def log_delegation(
        self,
        from_agent: str,
        to_agent: str,
        task_description: str,
        root_objective: str,
        parent_delegation_id: str | None = None,
    ) -> str:
        """Returns the new delegation_id so callers can chain child hops off it."""
        payload = DelegationPayload(
            from_agent=from_agent,
            to_agent=to_agent,
            task_description=task_description,
            root_objective=root_objective,
            parent_delegation_id=parent_delegation_id,
        )
        self._emit(payload)
        return payload.delegation_id

    def log_tool_call(
        self,
        agent: str,
        tool_name: str,
        arguments: dict[str, Any],
        agent_allowed_tools: list[str] | None = None,
        delegation_id: str | None = None,
    ) -> str:
        payload = ToolCallPayload(
            agent=agent,
            tool_name=tool_name,
            arguments=arguments,
            agent_allowed_tools=agent_allowed_tools or [],
            delegation_id=delegation_id,
        )
        self._emit(payload)
        return payload.call_id

    def log_tool_result(
        self,
        call_id: str,
        agent: str,
        tool_name: str,
        result: Any = None,
        error: str | None = None,
        side_effecting: bool = False,
    ) -> None:
        self._emit(
            ToolResultPayload(
                call_id=call_id,
                agent=agent,
                tool_name=tool_name,
                result=result,
                error=error,
                side_effecting=side_effecting,
            )
        )

    def log_memory_write(
        self, agent: str, key: str, value: Any, provenance: str = "unattributed"
    ) -> None:
        self._emit(
            MemoryWritePayload(agent=agent, key=key, value=value, provenance=provenance)
        )

    def log_memory_read(self, agent: str, key: str) -> None:
        self._emit(MemoryReadPayload(agent=agent, key=key))

    def log_error(self, message: str, agent: str | None = None) -> None:
        self._emit(ErrorPayload(agent=agent, message=message))

    # -- persistence ------------------------------------------------------

    def finish(self, output_dir: str | Path = "traces") -> Path:
        self.trace.ended_at = datetime.now(timezone.utc).isoformat()
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.trace.run_id}.json"
        path.write_text(
            json.dumps(self.trace.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
        return path
