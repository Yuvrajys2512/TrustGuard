"""LangGraph assembly for the task-delegation pipeline (target pipeline #2).

    coordinator ──route: standard──> analyst ──> executor ──> END
         │                              │            │
         │                        lookup_policy   verify_record
         │                              │         apply_change
         └──route: expedited───────────────────────> executor ──> END
                                        │            │
                                        └─ shared memory ─┘

Two structural differences from pipeline #1, both deliberate, both responses to
things pipeline #1 turned out not to be able to measure:

**The routing is real.** ``add_conditional_edges`` branches on a decision the
coordinator model made. Pipeline #1's edges are fixed, so its delegations are
recorded events that could never have gone otherwise (decision log 2026-08-20)
-- which is why attack 4 is structurally unstageable there. Here a hop can go
one way or the other, so "the chain took a hop it should have refused" is a
statement about the trace rather than about the pipeline's wiring.

**The victim can verify.** The executor holds ``verify_record``, a free
read-only check. Attack 3's success clause ("without independently verifying
it") is therefore capable of coming out false, which on pipeline #1 it was not
-- see ``tools.py`` and docs/phase_4_taxonomy_breadth.md section 2.4.

Same framework as pipeline #1 on purpose. The Phase 4 roadmap spec, substep 2, asks for
a second *structurally* different pipeline on LangGraph and scope-guards CrewAI
as optional; and Phase 7's white-box adapter needs node boundaries that are
ordinary inspectable Python. Structural difference is the requirement,
framework difference is not.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ...llm.client import RotatingLLMClient
from ...tracing.logger import TraceLogger
from ...tracing.schema import IngestionMode, RunConfig
from ..common.memory import SharedMemory
from ..common.messaging import Interceptor, MessageBus
from .agents import (
    ALL_AGENTS,
    AgentContext,
    run_analyst,
    run_coordinator,
    run_executor,
)
from .tools import ToolRegistry

PIPELINE_NAME = "task_delegation"
# 1.0.0 — first release. Three agents, an enacted routing branch, and a victim
#   holding a read-only verification tool alongside its side-effecting one.
#   Shares the trace schema, the message bus, the blackboard and the tool
#   enforcement model with pipeline #1, so a trace from either is read by the
#   same oracle code and the two are comparable as data.
# 1.1.0 — the executor's policy gains the clause pipeline #1's summarizer has
#   had since Phase 2: only the named role may invoke the exemption, and the
#   same claim from any other agent is advisory only. Found by attack 2's
#   MATCHED CONTROL crossing the gate on 1.0.0 — with no identity claim at all,
#   the victim skipped verification on the bare assertion "pre-approved". A
#   gate the control also crosses does not test attack 2's definition, which
#   requires the action be triggered by an unverified claim OF THAT ROLE. This
#   is a specification bug in 1.0.0, not a hardening: it restores parity with
#   pipeline #1's policy structure, without which the two pipelines' attack-2
#   cells would not be comparable. 1.0.0 attack-2 traces are NOT comparable
#   with 1.1.0 ones and none may be aggregated across the bump.
PIPELINE_VERSION = "1.1.0"


class PipelineState(TypedDict, total=False):
    """State passed between nodes.

    ``route`` is what the conditional edge reads. ``delegation_id`` is threaded
    through so each hop can name its parent -- that is what makes a delegation
    *chain* reconstructable, and on the standard route the chain is two hops
    deep, which is attack 4's precondition.
    """

    objective: str
    route: str
    task: str
    target: str
    change_request: dict[str, Any]
    delegation_id: str
    final_output: str
    verified: bool
    applied: bool
    shared_memory: dict[str, Any]


def build_graph(ctx: AgentContext):
    """Wire the three agents, with the coordinator's choice as a real branch."""

    def coordinator_node(state: PipelineState) -> PipelineState:
        result = run_coordinator(ctx, state["objective"])
        return {**result, "shared_memory": ctx.memory.snapshot()}

    def analyst_node(state: PipelineState) -> PipelineState:
        result = run_analyst(
            ctx,
            task=state["task"],
            delegation_id=state["delegation_id"],
            root_objective=state["objective"],
        )
        return {**result, "shared_memory": ctx.memory.snapshot()}

    def executor_node(state: PipelineState) -> PipelineState:
        result = run_executor(
            ctx,
            objective=state["objective"],
            delegation_id=state["delegation_id"],
            route=state.get("route", "standard"),
        )
        return {**result, "shared_memory": ctx.memory.snapshot()}

    def route_from_coordinator(state: PipelineState) -> str:
        """The branch. Reads the coordinator's own recorded decision.

        Anything unrecognised falls back to the assessed path rather than the
        exempt one: a parse failure must not hand a request the privilege the
        expedited route carries, or the pipeline would manufacture attack 4's
        outcome by accident and the measurement would be of the JSON parser.
        """
        return "analyst" if state.get("route") != "expedited" else "executor"

    graph = StateGraph(PipelineState)
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("executor", executor_node)

    graph.add_edge(START, "coordinator")
    graph.add_conditional_edges(
        "coordinator",
        route_from_coordinator,
        {"analyst": "analyst", "executor": "executor"},
    )
    graph.add_edge("analyst", "executor")
    graph.add_edge("executor", END)

    return graph.compile()


def run_pipeline(
    objective: str,
    llm: RotatingLLMClient | None = None,
    offline: bool = False,
    echo: bool = True,
    color: bool = True,
    trace_dir: str = "traces",
    seed: int | None = None,
    probe: Any | None = None,
    lane: str = "dev",
) -> tuple[str, str]:
    """Run one pipeline execution, clean or under attack.

    Signature is identical to pipeline #1's on purpose: every runner, sweep and
    validation-set builder takes a ``run_pipeline`` and should not have to know
    which pipeline it was handed.

    ``probe`` is an optional ``trustguard.probes.Probe``, installed on both
    injection points -- the message bus and shared memory -- exactly as on
    pipeline #1. Probes are written against the shared ``Message`` and
    ``MemoryWrite`` types (``pipelines/common``), so a probe built for pipeline
    #1 stages the same attack here without modification. That is the property
    that makes a 5x2 results matrix a comparison rather than two unrelated
    tables.

    ``offline`` is recorded but changes nothing: no tool in this pipeline
    touches the network in either mode (see ``tools.py``).

    Returns ``(final_output, trace_path)``.
    """
    llm = llm or RotatingLLMClient(seed=seed)
    backend = llm.active
    # Snapshot before anything is called so this run's backend attribution is a
    # delta rather than the client's lifetime totals -- a sweep reuses one
    # client across many runs. See pipeline #1's 1.1.1 note and client.py.
    usage_at_start = llm.usage_snapshot()

    extra: dict[str, Any] = {
        # Recorded for symmetry with pipeline #1's traces. Stated explicitly as
        # inapplicable rather than left to look like a live/offline toggle that
        # does something here.
        "offline_search": offline,
        "network_tools": False,
        "backends": llm.describe(),
        "lane": lane,
    }

    config = RunConfig(
        model=backend.model,
        provider=backend.provider,
        temperature=llm.temperature,
        seed=llm.seed,
        attack_id=probe.attack_id if probe is not None else None,
        ingestion_mode=(
            probe.ingestion_mode if probe is not None else IngestionMode.NONE
        ),
        defense_enabled=False,
        extra=extra,
    )

    logger = TraceLogger(
        pipeline=PIPELINE_NAME,
        config=config,
        agents=ALL_AGENTS,
        objective=objective,
        echo=echo,
        color=color,
    )
    logger.trace.pipeline_version = PIPELINE_VERSION

    tools = ToolRegistry(logger, offline=offline)
    interceptor: Interceptor | None = probe if probe is not None else None
    memory = SharedMemory(
        logger,
        interceptor=probe.on_memory_write if probe is not None else None,
    )
    bus = MessageBus(logger, interceptor=interceptor)
    ctx = AgentContext(llm=llm, logger=logger, memory=memory, tools=tools, bus=bus)

    logger.log_pipeline_input(objective)

    def record_usage() -> None:
        """Stamp which backend(s) actually served this run.

        On the failure path too: a run that died partway through is exactly the
        kind that rotated across backends, and a run that crashed after an
        injection landed still has to say that it landed.
        """
        logger.trace.config.extra.update(llm.usage_summary(since=usage_at_start))
        if probe is not None:
            logger.trace.config.extra["attack"] = probe.describe()

    app = build_graph(ctx)
    try:
        final_state = app.invoke({"objective": objective})
        output = final_state.get("final_output", "")
        logger.log_pipeline_output(output)
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        logger.log_error(f"{type(exc).__name__}: {exc}")
        record_usage()
        logger.finish(trace_dir)
        raise

    record_usage()
    path = logger.finish(trace_dir)
    return output, str(path)
