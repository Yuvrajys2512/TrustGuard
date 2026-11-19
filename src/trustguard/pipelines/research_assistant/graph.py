"""LangGraph assembly for the research-assistant pipeline.

    orchestrator ──delegates──> researcher ──delegates──> summarizer ──> END
                                    │                          │
                                web_search               save_report
                                    │                          │
                                    └──── shared memory ───────┘

LangGraph is the chosen framework because its state object and node boundaries
are ordinary inspectable Python — which is what Phase 7's white-box adapter
will wrap to intercept inter-agent messages. A framework that hid the message
bus would make that phase impossible.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ...llm.client import RotatingLLMClient
from ...tracing.logger import TraceLogger
from ...tracing.schema import IngestionMode, RunConfig
from .agents import (
    ALL_AGENTS,
    AgentContext,
    run_orchestrator,
    run_researcher,
    run_summarizer,
)
from .memory import SharedMemory
from .messaging import Interceptor, MessageBus
from .tools import ToolRegistry

PIPELINE_NAME = "research_assistant"
# 1.1.0 — inter-agent messages are now *delivered* into a recipient's inbox and
# rendered into its prompt, and the summarizer has a role-gated save policy.
# 1.1.1 — config.extra["calls_by_backend"] and ["rotated"] are now per-run
#   deltas rather than the client's lifetime totals. Agent behaviour is
#   byte-for-byte unchanged; only the run's self-description is corrected.
#   The bump exists so nobody compares a 1.1.0 trace's "rotated" flag with a
#   1.1.1 one and believes they mean the same thing. On 1.1.0 traces written
#   by a sweep, "rotated" describes the sweep so far, not the run.
# 1.2.0 — shared memory now has the same probe hook the message bus has, so a
#   probe can rewrite a blackboard write before it commits. Attack 3
#   (cross-agent context poisoning) is unstageable without it: §3 requires the
#   false claim be injected into shared memory specifically. With no probe
#   installed the hook is None and every write takes the identical path it took
#   in 1.1.1, so benign traces are unchanged in structure and content.
# Traces from 1.0.0 remain valid and comparable in structure; they are simply
# labelled with the older pipeline_version. SCHEMA_VERSION is unchanged.
PIPELINE_VERSION = "1.2.0"


class PipelineState(TypedDict, total=False):
    """State passed between nodes.

    ``delegation_id`` is threaded through so each hop can name its parent —
    that is what makes a delegation *chain* reconstructable from the trace.
    """

    objective: str
    research_task: str
    findings: str
    final_output: str
    saved_filename: str
    delegation_id: str
    shared_memory: dict[str, Any]


def build_graph(ctx: AgentContext):
    """Wire the three agents into a graph. No attacks, no defenses — clean."""

    def orchestrator_node(state: PipelineState) -> PipelineState:
        result = run_orchestrator(ctx, state["objective"])
        return {**result, "shared_memory": ctx.memory.snapshot()}

    def researcher_node(state: PipelineState) -> PipelineState:
        result = run_researcher(
            ctx,
            research_task=state["research_task"],
            delegation_id=state["delegation_id"],
            root_objective=state["objective"],
        )
        return {**result, "shared_memory": ctx.memory.snapshot()}

    def summarizer_node(state: PipelineState) -> PipelineState:
        result = run_summarizer(
            ctx,
            objective=state["objective"],
            delegation_id=state["delegation_id"],
        )
        return {**result, "shared_memory": ctx.memory.snapshot()}

    graph = StateGraph(PipelineState)
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("summarizer", summarizer_node)

    graph.add_edge(START, "orchestrator")
    graph.add_edge("orchestrator", "researcher")
    graph.add_edge("researcher", "summarizer")
    graph.add_edge("summarizer", END)

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

    ``probe`` is an optional ``trustguard.probes.Probe``. When present it is
    installed on **both** injection points — the message bus and shared memory
    — and its metadata is stamped into the run config, so a trace says for
    itself what was staged against it. A probe only overrides the hook its
    attack uses; the other stays pass-through.
    When absent the run is the Phase 1 clean baseline: ``attack_id`` is None and
    ``ingestion_mode`` is NONE.

    The pipeline itself is identical either way. Nothing downstream of the bus
    knows whether a probe is installed — which is the property that makes an
    attacked run comparable to a benign one.

    ``lane`` records whether this is a dev run or a paper run. It changes
    nothing about how the pipeline executes — it is bookkeeping, and it exists
    because Phase 4's numbers are only quotable if experimentation cannot leak
    into them.

    ``seed`` is recorded and forwarded to the provider, but does not make a run
    replayable on Groq — see ``llm/client.py``'s module docstring for the
    measurements. Structure is reproducible; generated text is not.

    Returns ``(final_output, trace_path)``.
    """
    llm = llm or RotatingLLMClient(seed=seed)
    backend = llm.active
    # Snapshot before anything is called, so this run's backend
    # attribution is a delta rather than the client's lifetime totals. A
    # sweep reuses one client across many runs; without this every run
    # after the first inherits the sweep's history. See client.py.
    usage_at_start = llm.usage_snapshot()

    extra: dict[str, Any] = {
        "offline_search": offline,
        "backends": llm.describe(),
        # Which collection this run belongs to. "paper" runs are the only ones
        # that may be quoted in a result; "dev" is everything else. Recorded on
        # the run itself so a trace that gets moved between folders still says
        # what it was collected as.
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

        Must happen on the failure path too — a run that died partway through
        is exactly the kind that rotated across backends, and that is what a
        reader needs to know when interpreting it. The probe's record goes here
        for the same reason: a run that crashed after the injection landed still
        has to say that it landed.
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
