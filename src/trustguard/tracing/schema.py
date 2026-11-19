"""Shared trace schema for TrustGuard.

This module is a *contract*, not an implementation detail. The probe engine
(Phase 2/4), the oracle (Phase 3), the scoring metric (Phase 5) and the defense
layer (Phase 6) all read and write these structures. Changing a field here
breaks the other tracks, so any change gets a decision-log entry and a bump of
``SCHEMA_VERSION``.

Design notes tied to the Phase 0 success definitions (docs/phase_0_threat_model.md §3):

* ``DelegationPayload`` carries ``parent_delegation_id`` and ``root_objective``
  so a delegation *chain* is reconstructable. Attack 1 (delegated goal
  hijacking) needs task-vs-root-objective divergence; attack 4 (cascading
  privilege escalation) needs chains of length >= 2.
* ``MemoryWritePayload`` carries ``provenance`` so attack 3 (cross-agent context
  poisoning) can distinguish an agent asserting a claim as fact from an agent
  attributing it to a source.
* ``ToolCallPayload`` records the acting agent and the permission scope in force
  at call time, so attacks 4 and 5 can tell an in-scope call from an
  out-of-scope one without re-deriving the pipeline config.
* ``AgentMessagePayload`` records ``claimed_sender`` separately from the real
  ``from_agent``. Attack 2 (fabricated authority / role spoofing) lives exactly
  in the gap between those two fields.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class EventType(str, Enum):
    """Every observable thing that can happen inside a pipeline run."""

    AGENT_MESSAGE = "agent_message"
    AGENT_REASONING = "agent_reasoning"
    DELEGATION = "delegation"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MEMORY_WRITE = "memory_write"
    MEMORY_READ = "memory_read"
    PIPELINE_INPUT = "pipeline_input"
    PIPELINE_OUTPUT = "pipeline_output"
    ERROR = "error"


class IngestionMode(str, Enum):
    """Threat-model mode from docs/phase_0_threat_model.md §1."""

    NONE = "none"  # benign run, no attacker present
    WHITE_BOX = "white_box"
    BLACK_BOX = "black_box"


# --------------------------------------------------------------------------
# Agent / pipeline description
# --------------------------------------------------------------------------


class AgentSpec(BaseModel):
    """Static description of one agent, captured per run.

    ``allowed_tools`` is the permission scope. Phase 5/6 compare a tool call's
    acting agent against this to decide whether a call exceeded scope.
    """

    name: str
    role: str
    system_prompt: str
    allowed_tools: list[str] = Field(default_factory=list)
    can_delegate_to: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Event payloads
# --------------------------------------------------------------------------


class AgentMessagePayload(BaseModel):
    type: Literal[EventType.AGENT_MESSAGE] = EventType.AGENT_MESSAGE
    from_agent: str
    to_agent: str
    content: str
    # What the message *claims* about its own origin/authority. On a benign run
    # this equals from_agent. Attack 2 succeeds precisely when a victim acts on
    # a claimed_sender/claimed_authority the pipeline never verified.
    claimed_sender: str | None = None
    claimed_authority: str | None = None


class AgentReasoningPayload(BaseModel):
    type: Literal[EventType.AGENT_REASONING] = EventType.AGENT_REASONING
    agent: str
    reasoning: str


class DelegationPayload(BaseModel):
    type: Literal[EventType.DELEGATION] = EventType.DELEGATION
    delegation_id: str = Field(default_factory=lambda: _new_id("dlg"))
    from_agent: str
    to_agent: str
    task_description: str
    # None for a first-hop delegation issued against the root objective.
    parent_delegation_id: str | None = None
    # The original human-level objective this chain descends from. Carried on
    # every hop so goal-divergence is checkable without walking the whole trace.
    root_objective: str


class ToolCallPayload(BaseModel):
    type: Literal[EventType.TOOL_CALL] = EventType.TOOL_CALL
    call_id: str = Field(default_factory=lambda: _new_id("call"))
    agent: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Permission scope in force for `agent` at call time, copied from AgentSpec.
    agent_allowed_tools: list[str] = Field(default_factory=list)
    # Delegation this call was made in service of, if any.
    delegation_id: str | None = None


class ToolResultPayload(BaseModel):
    type: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    call_id: str
    agent: str
    tool_name: str
    result: Any = None
    error: str | None = None
    # Side-effecting calls are the ones attack 5 (tool-call laundering) cares
    # about; a read-only search cannot be "laundered" in any meaningful sense.
    side_effecting: bool = False


class MemoryWritePayload(BaseModel):
    type: Literal[EventType.MEMORY_WRITE] = EventType.MEMORY_WRITE
    agent: str
    key: str
    value: Any = None
    # Where the claim came from: "tool:<name>", "agent:<name>", "model_prior",
    # or "unattributed". Attack 3's oracle reads this field.
    provenance: str = "unattributed"


class MemoryReadPayload(BaseModel):
    type: Literal[EventType.MEMORY_READ] = EventType.MEMORY_READ
    agent: str
    key: str


class PipelineInputPayload(BaseModel):
    type: Literal[EventType.PIPELINE_INPUT] = EventType.PIPELINE_INPUT
    objective: str


class PipelineOutputPayload(BaseModel):
    type: Literal[EventType.PIPELINE_OUTPUT] = EventType.PIPELINE_OUTPUT
    output: str


class ErrorPayload(BaseModel):
    type: Literal[EventType.ERROR] = EventType.ERROR
    agent: str | None = None
    message: str


Payload = Annotated[
    Union[
        AgentMessagePayload,
        AgentReasoningPayload,
        DelegationPayload,
        ToolCallPayload,
        ToolResultPayload,
        MemoryWritePayload,
        MemoryReadPayload,
        PipelineInputPayload,
        PipelineOutputPayload,
        ErrorPayload,
    ],
    Field(discriminator="type"),
]


class TraceEvent(BaseModel):
    """One observable step. ``seq`` gives a total order within a run."""

    seq: int
    timestamp: str = Field(default_factory=_now)
    payload: Payload

    @property
    def event_type(self) -> EventType:
        return self.payload.type


# --------------------------------------------------------------------------
# Run-level container
# --------------------------------------------------------------------------


class RunConfig(BaseModel):
    """Everything needed to interpret — and reproduce — a run."""

    model: str
    provider: str
    temperature: float = 0.0
    seed: int | None = None
    # Phase 2+: which attack was staged, None on a benign run.
    attack_id: str | None = None
    ingestion_mode: IngestionMode = IngestionMode.NONE
    # Phase 6: the before/after toggle the defense claim rests on.
    defense_enabled: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class RunTrace(BaseModel):
    """A complete pipeline run. This is the benchmark's unit of raw data."""

    schema_version: str = SCHEMA_VERSION
    run_id: str = Field(default_factory=lambda: _new_id("run"))
    pipeline: str
    pipeline_version: str = "1.0.0"
    started_at: str = Field(default_factory=_now)
    ended_at: str | None = None
    objective: str = ""
    agents: list[AgentSpec] = Field(default_factory=list)
    config: RunConfig
    events: list[TraceEvent] = Field(default_factory=list)
    final_output: str | None = None
    # Filled by the oracle in Phase 3; left None by the pipeline itself.
    oracle_verdict: dict[str, Any] | None = None

    # -- convenience accessors used by later phases ------------------------

    def events_of(self, event_type: EventType) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type == event_type]

    def delegation_chain(self, delegation_id: str) -> list[DelegationPayload]:
        """Walk a delegation back to its root. Attack 4 needs chain length."""
        by_id: dict[str, DelegationPayload] = {
            e.payload.delegation_id: e.payload
            for e in self.events_of(EventType.DELEGATION)
        }
        chain: list[DelegationPayload] = []
        current = by_id.get(delegation_id)
        seen: set[str] = set()
        while current is not None and current.delegation_id not in seen:
            seen.add(current.delegation_id)
            chain.append(current)
            parent_id = current.parent_delegation_id
            current = by_id.get(parent_id) if parent_id else None
        return list(reversed(chain))
