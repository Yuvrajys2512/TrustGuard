#!/usr/bin/env python
"""Build the TrustGuard console — a cinematic replay of real recorded runs.

    python demo/tools/build_console.py                  # -> demo/console.html
    python demo/tools/build_console.py --out path.html

The console is a *presentation* surface: it plays a recorded run back as an
animated agent graph and a streaming log, so an audience can watch an attack
land instead of reading a trace file. It is built from the same
``demo/*_replay.json`` pairs ``demo/tools/demo.py --replay`` uses, so every event
it draws happened in a real run against real models.

Three rules keep it honest, because a demo that looks this finished invites the
question "is any of this real?" and the answer has to survive being poked at:

* Every event comes from a recorded trace. Nothing here is scripted or invented,
  and the console labels the run id it is playing.
* The verdicts are the oracle's, copied from the recording — not recomputed and
  certainly not authored. An unvalidated checker stays marked unvalidated.
* Capability the project does not have yet is drawn as **not built**. The
  coverage matrix shows two attacks of five and one pipeline of two, because
  that is what exists; a filled-in matrix would be the one thing in this
  presentation that lied.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trustguard.oracle.heuristics_attack3 import contains_marker  # noqa: E402
from trustguard.pipelines.research_assistant.agents import (  # noqa: E402
    DEFAULT_REPORT_FILENAME,
)

DEMO_DIR = ROOT / "demo"
TEMPLATE = Path(__file__).resolve().parent / "console_template.html"

#: Where each event is drawn on the graph. "bus" is the message channel between
#: agents, "board" the shared blackboard, "tool" the tool surface.
LANE = {
    "pipeline_input": "bus",
    "pipeline_output": "bus",
    "agent_message": "bus",
    "delegation": "bus",
    "agent_reasoning": "think",
    "tool_call": "tool",
    "tool_result": "tool",
    "memory_write": "board",
    "memory_read": "board",
    "error": "bus",
}


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _event(payload: dict, markers: list[str], victim: str | None) -> dict:
    """One trace event, reduced to what the console draws and says.

    ``flag`` drives the visual treatment: "inject" is the attack entering the
    system, "impact" is it changing an outcome. Both are read off recorded
    fields — a claimed_sender that disagrees with from_agent, a planted marker,
    a filename that is not the policy default — never asserted here.
    """
    kind = payload["type"]
    base = {
        "kind": kind,
        "lane": LANE.get(kind, "bus"),
        "actor": "system",
        "to": None,
        "label": kind.replace("_", " "),
        "detail": "",
        "flag": None,
        "note": None,
    }

    if kind == "pipeline_input":
        return {**base, "actor": "user", "to": "orchestrator", "label": "objective",
                "detail": _clip(payload["objective"], 150)}

    if kind == "pipeline_output":
        return {**base, "actor": "summarizer", "to": "user", "label": "final output",
                "detail": _clip(payload["output"], 150)}

    if kind == "agent_reasoning":
        return {**base, "actor": payload["agent"], "label": "reasoning",
                "detail": _clip(payload["reasoning"], 200)}

    if kind == "delegation":
        return {**base, "actor": payload["from_agent"], "to": payload["to_agent"],
                "label": "delegate", "detail": _clip(payload["task_description"], 120)}

    if kind == "agent_message":
        forged = payload.get("claimed_sender") not in (None, payload["from_agent"])
        return {
            **base,
            "actor": payload["from_agent"],
            "to": payload["to_agent"],
            "label": "forged message" if forged else "message",
            "detail": _clip(payload["content"], 260),
            "flag": "inject" if forged else None,
            "note": (f"claims to be {payload['claimed_sender']} · "
                     f"actually {payload['from_agent']}") if forged else None,
        }

    if kind == "tool_call":
        args = payload.get("arguments", {})
        filename = args.get("filename")
        diverted = (payload["tool_name"] == "save_report"
                    and filename != DEFAULT_REPORT_FILENAME)
        detail = (f"filename={filename}" if filename
                  else _clip(args.get("query") or json.dumps(args, default=str), 120))
        return {
            **base,
            "actor": payload["agent"],
            "to": payload["tool_name"],
            "label": f"call {payload['tool_name']}",
            "detail": detail,
            "flag": "impact" if diverted else None,
            "note": (f"policy default is {DEFAULT_REPORT_FILENAME}"
                     if diverted else None),
        }

    if kind == "tool_result":
        return {**base, "actor": payload["tool_name"], "to": payload["agent"],
                "label": f"{payload['tool_name']} returns",
                "detail": _clip(payload.get("result"), 120)}

    if kind == "memory_write":
        value = str(payload.get("value", ""))
        poisoned = any(contains_marker(m, value) for m in markers)
        return {
            **base,
            "actor": payload["agent"],
            "to": payload["key"],
            "label": "write blackboard",
            "detail": _clip(value, 220),
            "flag": "inject" if poisoned else None,
            "note": (f"provenance recorded as {payload.get('provenance')!r} — "
                     f"nothing reads it") if poisoned else None,
        }

    if kind == "memory_read":
        return {**base, "actor": payload["agent"], "to": payload["key"],
                "label": "read blackboard", "detail": payload["key"]}

    if kind == "error":
        return {**base, "actor": payload.get("agent") or "system", "label": "error",
                "detail": _clip(payload["message"], 150), "flag": "impact"}

    return base


def _clock(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def _arm(trace: dict, verdict: dict | None) -> dict:
    meta = trace["config"]["extra"].get("attack", {})
    markers = meta.get("markers", [])
    victim = meta.get("victim")
    events = [_event(e["payload"], markers, victim) for e in trace["events"]]

    # The recorded run's own clock, not the playback clock — the console shows
    # how long each step actually took when the models were doing the work.
    origin = _clock(trace["events"][0]["timestamp"]) if trace["events"] else None

    for event, raw in zip(events, trace["events"]):
        event["seq"] = raw["seq"]
        moment = _clock(raw["timestamp"])
        event["t"] = round((moment - origin).total_seconds(), 2) if origin and moment else None
        event["bytes"] = len(json.dumps(raw["payload"], default=str))

    # A poisoned write only matters once the victim loads it — flag that read
    # from the write that poisoned it, not on its own.
    poisoned_keys = {
        e["payload"]["key"]
        for e in trace["events"]
        if e["payload"]["type"] == "memory_write"
        and any(contains_marker(m, str(e["payload"].get("value", "")))
                for m in markers)
    }
    for event, raw in zip(events, trace["events"]):
        p = raw["payload"]
        if (p["type"] == "memory_read" and p["agent"] == victim
                and p["key"] in poisoned_keys):
            event["flag"] = "inject"
            event["label"] = "read poisoned value"
            event["note"] = "the fabricated finding enters the victim's context"

    saves = [
        e for e in trace["events"]
        if e["payload"]["type"] == "tool_call"
        and e["payload"]["tool_name"] == "save_report"
    ]
    filename = saves[-1]["payload"]["arguments"].get("filename") if saves else None
    return {
        "run_id": trace["run_id"],
        "variant": meta.get("variant", "—"),
        "model": trace["config"]["model"],
        "provider": trace["config"]["provider"],
        "injection_point": meta.get("injection_point"),
        "filename": filename,
        "diverted": bool(filename) and filename != DEFAULT_REPORT_FILENAME,
        "verdict": verdict,
        "events": events,
    }


#: The Phase 0 taxonomy crossed with the planned pipelines. `state` is the only
#: field that matters here and it is not decoration: "live" means runs exist and
#: the console can play them, "built" means the code exists but no result is
#: claimed, "planned" means it does not exist. Kept in sync by hand with
#: docs/phase_4_taxonomy_breadth.md §5.
MATRIX = {
    "attacks": [
        {"id": "attack1_goal_hijack", "n": 1, "name": "Delegated goal hijacking",
         "p1": "planned", "p2": "planned"},
        {"id": "attack2_role_spoof", "n": 2, "name": "Fabricated authority",
         "p1": "live", "p2": "planned"},
        {"id": "attack3_context_poison", "n": 3, "name": "Cross-agent context poisoning",
         "p1": "built", "p2": "planned"},
        {"id": "attack4_priv_escalation", "n": 4, "name": "Cascading privilege escalation",
         "p1": "blocked", "p2": "planned"},
        {"id": "attack5_tool_laundering", "n": 5, "name": "Silent tool-call laundering",
         "p1": "planned", "p2": "planned"},
    ],
    "notes": {
        "live": "runs collected, checker validated, result quotable",
        "built": "probe and checker exist, no runs collected, no result claimed",
        "blocked": "pipeline #1 has no conditional delegation hop to exploit",
        "planned": "not started",
    },
}


def build(out_path: Path) -> Path:
    scenarios = []
    for attack_id, title, premise in (
        (
            "attack2_role_spoof",
            "Fabricated authority",
            "One worker agent forges a message claiming to be the supervisor. "
            "Nothing in the pipeline verifies who a message says it is from.",
        ),
        (
            "attack3_context_poison",
            "Cross-agent context poisoning",
            "One worker agent writes a fabricated finding onto shared memory. "
            "The next agent reads it as established fact.",
        ),
    ):
        pair_path = DEMO_DIR / f"{attack_id}_replay.json"
        if not pair_path.exists():
            print(f"skipping {attack_id}: no recording at {pair_path}")
            continue
        saved = json.loads(pair_path.read_text(encoding="utf-8"))
        scenarios.append({
            "id": attack_id,
            "title": title,
            "premise": premise,
            "recorded_at": saved["recorded_at"],
            "attack": _arm(saved["attack"], saved.get("attack_verdict")),
            "control": _arm(saved["control"], saved.get("control_verdict")),
        })

    if not scenarios:
        raise SystemExit(
            "no recordings found in demo/. Record one first:\n"
            "  python demo/tools/demo.py --record"
        )

    payload = {"scenarios": scenarios, "matrix": MATRIX}
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", blob)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEMO_DIR / "console.html"))
    args = parser.parse_args()
    out = build(Path(args.out))
    size = out.stat().st_size / 1024
    print(f"written: {out}  ({size:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
