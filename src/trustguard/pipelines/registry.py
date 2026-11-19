"""Registry of target pipelines, keyed by name.

Scripts resolve a pipeline through here so that adding a third target makes it
available to every runner by being registered once, rather than by editing each
script's imports. The same reason ``probes/__init__.py`` keeps a ``PROBES``
table.

Every entry exposes the same ``run_pipeline(objective, llm, offline, echo,
color, trace_dir, seed, probe, lane) -> (output, trace_path)`` signature. A
runner should not need to know which pipeline it was handed -- that is what
makes a 5x2 sweep one loop instead of two.

Note the asymmetry a caller *does* need to know about: ``offline`` selects a
fixed search corpus on pipeline #1, and is inert on pipeline #2, which has no
network tool at all. ``DESCRIPTIONS`` says so rather than leaving a runner to
imply a live/offline distinction that does not exist there.
"""

from __future__ import annotations

from typing import Callable, NamedTuple


class PipelineEntry(NamedTuple):
    name: str
    version: str
    description: str
    run: Callable[..., tuple[str, str]]
    #: The agent whose behaviour most attacks in the taxonomy target.
    default_victim: str
    #: Whether ``offline`` changes anything for this pipeline.
    honours_offline: bool


def _entries() -> dict[str, PipelineEntry]:
    # Imported lazily so importing the registry does not drag LangGraph in for
    # callers that only want the names.
    from .research_assistant import graph as research_assistant
    from .task_delegation import graph as task_delegation

    return {
        research_assistant.PIPELINE_NAME: PipelineEntry(
            name=research_assistant.PIPELINE_NAME,
            version=research_assistant.PIPELINE_VERSION,
            description=(
                "Research assistant. Fixed three-agent line "
                "(orchestrator -> researcher -> summarizer); delegation is "
                "recorded but not enacted; the victim holds no read-only tool."
            ),
            run=research_assistant.run_pipeline,
            default_victim="summarizer",
            honours_offline=True,
        ),
        task_delegation.PIPELINE_NAME: PipelineEntry(
            name=task_delegation.PIPELINE_NAME,
            version=task_delegation.PIPELINE_VERSION,
            description=(
                "Operations task delegation. The coordinator's route is an "
                "enacted branch (standard -> analyst -> executor, or expedited "
                "-> executor); the victim holds verify_record, a free read-only "
                "check, alongside its side-effecting apply_change."
            ),
            run=task_delegation.run_pipeline,
            default_victim="executor",
            honours_offline=False,
        ),
    }


def get(name: str) -> PipelineEntry:
    entries = _entries()
    try:
        return entries[name]
    except KeyError:
        raise KeyError(
            f"No such pipeline: {name!r}. Known pipelines: {sorted(entries)}"
        ) from None


def all_entries() -> dict[str, PipelineEntry]:
    return _entries()


def names() -> list[str]:
    return sorted(_entries())


#: The pipeline a runner uses when none is named. Pipeline #1, because every
#: number reported so far was measured on it and a silent default change would
#: make two incomparable sets of runs look like one.
DEFAULT_PIPELINE = "research_assistant"


def describe_all() -> str:
    lines = []
    for entry in _entries().values():
        default = "  (default)" if entry.name == DEFAULT_PIPELINE else ""
        lines.append(f"{entry.name} {entry.version}{default}")
        lines.append(f"    {entry.description}")
        lines.append(f"    victim: {entry.default_victim}")
        if not entry.honours_offline:
            lines.append("    --offline: inert (no network tool in this pipeline)")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_PIPELINE",
    "PipelineEntry",
    "all_entries",
    "describe_all",
    "get",
    "names",
]
