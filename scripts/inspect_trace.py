#!/usr/bin/env python
"""Pretty-print a saved run trace.

    python scripts/inspect_trace.py                     # most recent trace
    python scripts/inspect_trace.py traces/run_abc.json
    python scripts/inspect_trace.py --full              # no truncation

Phase 1's definition of done is a *readable* trace of who-said-what-to-whom.
This is the reader.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from trustguard.tracing.schema import EventType, RunTrace  # noqa: E402


def _fit(value: object, limit: int, full: bool) -> str:
    text = " ".join(str(value).split())
    if full or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def render(trace: RunTrace, full: bool) -> None:
    width = 100
    print("=" * width)
    print(f"run {trace.run_id}   schema v{trace.schema_version}")
    print("=" * width)
    print(f"pipeline   : {trace.pipeline} v{trace.pipeline_version}")
    print(f"objective  : {_fit(trace.objective, 200, full)}")
    print(f"model      : {trace.config.provider}:{trace.config.model} (temp={trace.config.temperature})")
    print(f"attack     : {trace.config.attack_id or '(none — benign run)'}")
    print(f"mode       : {trace.config.ingestion_mode.value}   defense_enabled={trace.config.defense_enabled}")
    print(f"started    : {trace.started_at}")
    print(f"events     : {len(trace.events)}")

    print("\nAGENTS")
    for agent in trace.agents:
        print(f"  {agent.name:<14} tools={agent.allowed_tools or '[]'}  can_delegate_to={agent.can_delegate_to or '[]'}")

    print("\nTIMELINE")
    for event in trace.events:
        p = event.payload
        kind = event.event_type
        label = kind.value.replace("_", " ")
        if kind is EventType.AGENT_MESSAGE:
            sender = p.from_agent
            if p.claimed_sender and p.claimed_sender != p.from_agent:
                sender = f"{p.from_agent}!claims:{p.claimed_sender}"
            if p.claimed_authority:
                sender += f"!authority:{p.claimed_authority}"
            body = f"{sender} -> {p.to_agent}: {_fit(p.content, 150, full)}"
        elif kind is EventType.AGENT_REASONING:
            body = f"{p.agent}: {_fit(p.reasoning, 150, full)}"
        elif kind is EventType.DELEGATION:
            body = f"{p.from_agent} -> {p.to_agent}: {_fit(p.task_description, 130, full)}"
        elif kind is EventType.TOOL_CALL:
            body = f"{p.agent} calls {p.tool_name} {_fit(json.dumps(p.arguments, default=str), 110, full)}"
        elif kind is EventType.TOOL_RESULT:
            status = f"ERROR {p.error}" if p.error else _fit(p.result, 110, full)
            flag = " [SIDE-EFFECTING]" if p.side_effecting else ""
            body = f"{p.tool_name}{flag} -> {status}"
        elif kind is EventType.MEMORY_WRITE:
            body = f"{p.agent} write {p.key} <{p.provenance}> = {_fit(p.value, 110, full)}"
        elif kind is EventType.MEMORY_READ:
            body = f"{p.agent} read {p.key}"
        elif kind is EventType.PIPELINE_INPUT:
            body = _fit(p.objective, 150, full)
        elif kind is EventType.PIPELINE_OUTPUT:
            body = _fit(p.output, 150, full)
        else:
            body = _fit(p.message, 150, full)
        print(f"  [{event.seq:>3}] {label:<15} {body}")

    delegations = trace.events_of(EventType.DELEGATION)
    if delegations:
        print("\nDELEGATION CHAINS")
        # Only leaves — a chain printed from its deepest hop shows the whole path.
        parents = {
            e.payload.parent_delegation_id
            for e in delegations
            if e.payload.parent_delegation_id
        }
        for event in delegations:
            if event.payload.delegation_id in parents:
                continue
            chain = trace.delegation_chain(event.payload.delegation_id)
            print(f"  chain of {len(chain)} hop(s):")
            for depth, hop in enumerate(chain):
                print(f"    {'  ' * depth}{hop.from_agent} -> {hop.to_agent}: {_fit(hop.task_description, 90, full)}")
            print(f"    root objective: {_fit(chain[0].root_objective, 90, full)}")

    calls = trace.events_of(EventType.TOOL_CALL)
    results = {e.payload.call_id: e.payload for e in trace.events_of(EventType.TOOL_RESULT)}
    if calls:
        print("\nTOOL CALLS")
        for event in calls:
            p = event.payload
            result = results.get(p.call_id)
            in_scope = p.tool_name in p.agent_allowed_tools
            status = "denied" if (result and result.error) else "ok"
            print(
                f"  {p.agent:<12} {p.tool_name:<14} in_scope={str(in_scope):<5} "
                f"side_effecting={str(bool(result and result.side_effecting)):<5} {status}"
            )

    if trace.oracle_verdict:
        print("\nORACLE VERDICT")
        print(json.dumps(trace.oracle_verdict, indent=2)[:2000])

    print("\nFINAL OUTPUT")
    print(_fit(trace.final_output or "(none)", 600, full))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Trace JSON. Defaults to the most recent.")
    parser.add_argument("--trace-dir", default="traces")
    parser.add_argument("--full", action="store_true", help="Do not truncate long text.")
    args = parser.parse_args()

    path = args.path
    if not path:
        candidates = glob.glob(os.path.join(args.trace_dir, "*.json"))
        if not candidates:
            print(f"no traces found in {args.trace_dir}/", file=sys.stderr)
            return 1
        path = max(candidates, key=os.path.getmtime)

    trace = RunTrace.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    render(trace, full=args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
