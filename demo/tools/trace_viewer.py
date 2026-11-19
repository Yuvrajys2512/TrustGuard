#!/usr/bin/env python
"""Render a trace pair as a standalone HTML delegation timeline.

    python demo/tools/trace_viewer.py traces/dev/run_abc.json traces/dev/run_def.json
    python demo/tools/trace_viewer.py --replay demo/attack2_role_spoof_replay.json

The output is a single self-contained file — no server, no CDN, no build step —
so it opens from the filesystem during a presentation and prints cleanly as a
figure for the report.

It is a *rendering* of a trace and nothing more. Every flag it draws is read
from a field the pipeline already recorded (``claimed_sender`` vs
``from_agent``, ``provenance``, the ``save_report`` filename); the verdict shown
in the header is the oracle's, passed in, not recomputed here. If this file
disagreed with ``scripts/apply_oracle.py`` the trace would be the authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trustguard.oracle.heuristics_attack3 import contains_marker  # noqa: E402
from trustguard.pipelines.research_assistant.agents import (  # noqa: E402
    DEFAULT_REPORT_FILENAME,
)

# Longest text we inline per event. Full reports run to thousands of characters
# and would bury the timeline; the trace file remains the complete record.
EXCERPT = 400


def _truncate(text: str, limit: int = EXCERPT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + " …"


def _summarise(payload: dict[str, Any], markers: list[str]) -> dict[str, Any]:
    """Reduce one event payload to what the timeline shows.

    Returns ``{actor, title, rows, flag}`` where ``flag`` is None, "attack"
    (evidence the injection happened) or "impact" (evidence it changed an
    action). Nothing here is a verdict — a flag marks a line worth reading.

    ``markers`` are the probe's planted strings, taken from the trace. They are
    what separates a poisoned memory write from an ordinary one: agent-sourced
    provenance is the normal case in this pipeline, so flagging on provenance
    alone would mark every benign write the orchestrator makes.
    """
    kind = payload["type"]

    if kind == "agent_message":
        forged = payload.get("claimed_sender") not in (None, payload["from_agent"])
        rows = [("to", payload["to_agent"])]
        if forged:
            rows += [
                ("claimed sender", payload["claimed_sender"]),
                ("true sender", payload["from_agent"]),
                ("claimed authority", payload.get("claimed_authority") or "—"),
            ]
        rows.append(("content", _truncate(payload["content"])))
        return {
            "actor": payload["from_agent"],
            "title": "forged message" if forged else "message",
            "rows": rows,
            "flag": "attack" if forged else None,
        }

    if kind == "agent_reasoning":
        return {
            "actor": payload["agent"],
            "title": "reasoning",
            "rows": [("", _truncate(payload["reasoning"]))],
            "flag": None,
        }

    if kind == "delegation":
        return {
            "actor": payload["from_agent"],
            "title": "delegation",
            "rows": [
                ("to", payload["to_agent"]),
                ("task", _truncate(payload["task_description"], 200)),
            ],
            "flag": None,
        }

    if kind == "tool_call":
        rows = []
        flag = None
        title = f"tool call · {payload['tool_name']}"
        for key, value in payload.get("arguments", {}).items():
            rows.append((key, _truncate(value)))
        if payload["tool_name"] == "save_report":
            filename = payload.get("arguments", {}).get("filename")
            if filename != DEFAULT_REPORT_FILENAME:
                flag = "impact"
                title = "tool call · save_report — DIVERTED"
                rows.insert(0, ("policy default", DEFAULT_REPORT_FILENAME))
        return {
            "actor": payload["agent"],
            "title": title,
            "rows": rows,
            "flag": flag,
        }

    if kind == "tool_result":
        return {
            "actor": payload["agent"],
            "title": f"tool result · {payload['tool_name']}",
            "rows": [("result", _truncate(payload.get("result"), 200))],
            "flag": None,
        }

    if kind == "memory_write":
        value = str(payload.get("value", ""))
        poisoned = any(contains_marker(m, value) for m in markers)
        return {
            "actor": payload["agent"],
            "title": "memory write — POISONED" if poisoned else "memory write",
            "rows": [
                ("key", payload["key"]),
                ("provenance", payload.get("provenance", "unattributed")),
                ("value", _truncate(value)),
            ],
            "flag": "attack" if poisoned else None,
        }

    if kind == "memory_read":
        return {
            "actor": payload["agent"],
            "title": "memory read",
            "rows": [("key", payload["key"])],
            "flag": None,
        }

    if kind == "pipeline_input":
        return {
            "actor": "user",
            "title": "objective",
            "rows": [("", _truncate(payload["objective"], 300))],
            "flag": None,
        }

    if kind == "pipeline_output":
        return {
            "actor": "pipeline",
            "title": "final output",
            "rows": [("", _truncate(payload["output"], 300))],
            "flag": None,
        }

    if kind == "error":
        return {
            "actor": payload.get("agent") or "pipeline",
            "title": "error",
            "rows": [("", _truncate(payload["message"]))],
            "flag": "impact",
        }

    return {"actor": "—", "title": kind, "rows": [], "flag": None}


def _arm(trace: dict, verdict: dict | None) -> dict[str, Any]:
    """Flatten one trace into the shape the page's renderer consumes."""
    meta = trace["config"]["extra"].get("attack", {})
    markers = meta.get("markers", [])
    events = [
        dict(_summarise(e["payload"], markers), seq=e["seq"],
             kind=e["payload"]["type"])
        for e in trace["events"]
    ]

    # A poisoned value only matters once the victim loads it, and that is a
    # second event — so the read is flagged from the write, not on its own.
    poisoned_keys = {
        e["payload"]["key"]
        for e in trace["events"]
        if e["payload"]["type"] == "memory_write"
        and any(contains_marker(m, str(e["payload"].get("value", "")))
                for m in markers)
    }
    for event, raw in zip(events, trace["events"]):
        payload = raw["payload"]
        if (payload["type"] == "memory_read"
                and payload["agent"] == meta.get("victim")
                and payload["key"] in poisoned_keys):
            event["flag"] = "attack"
            event["title"] = "memory read — loads the poisoned value"

    saves = [
        e
        for e in trace["events"]
        if e["payload"]["type"] == "tool_call"
        and e["payload"]["tool_name"] == "save_report"
    ]
    filename = saves[-1]["payload"]["arguments"].get("filename") if saves else None
    return {
        "run_id": trace["run_id"],
        "variant": meta.get("variant", "—"),
        "attack_id": meta.get("attack_id"),
        "attack_name": meta.get("attack_name", "benign run"),
        "injection_point": meta.get("injection_point", "—"),
        "ingestion_mode": meta.get("ingestion_mode", "none"),
        "model": trace["config"]["model"],
        "filename": filename,
        "diverted": bool(filename) and filename != DEFAULT_REPORT_FILENAME,
        "verdict": verdict,
        "events": events,
    }


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>TrustGuard — trace viewer</title>
<style>
  :root {
    --bg: #ffffff; --fg: #16181d; --muted: #6b7280; --line: #e3e6ea;
    --panel: #f7f8fa; --attack: #c2255c; --attack-bg: #fdf2f6;
    --impact: #a61e4d; --ok: #157f3d; --ok-bg: #f1f9f3; --accent: #1b4fa8;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14161a; --fg: #e8eaed; --muted: #9aa1ab; --line: #2a2e35;
      --panel: #1b1e24; --attack: #ff6b9d; --attack-bg: #2a1620;
      --impact: #ff8fb1; --ok: #4ade80; --ok-bg: #14241a; --accent: #7aa5f0;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 940px; margin: 0 auto; padding: 32px 24px 72px; }
  header { border-bottom: 2px solid var(--fg); padding-bottom: 14px; }
  h1 { font-size: 21px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13.5px; }
  .meta {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 10px 26px; margin: 20px 0 4px; font-size: 13.5px;
  }
  .meta div { border-top: 1px solid var(--line); padding-top: 7px; }
  .meta .k {
    color: var(--muted); font-size: 11px; text-transform: uppercase;
    letter-spacing: .07em; display: block; margin-bottom: 2px;
  }
  .meta .v { font-family: var(--mono); font-size: 12.5px; word-break: break-word; }
  .tabs { display: flex; gap: 8px; margin: 26px 0 6px; flex-wrap: wrap; }
  .tabs button {
    font: inherit; font-size: 13.5px; padding: 8px 16px; cursor: pointer;
    border: 1px solid var(--line); background: var(--panel); color: var(--muted);
    border-radius: 7px;
  }
  .tabs button[aria-selected="true"] {
    background: var(--fg); color: var(--bg); border-color: var(--fg);
    font-weight: 600;
  }
  .verdict {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    margin: 18px 0 4px; padding: 13px 16px; border-radius: 8px;
    background: var(--panel); border: 1px solid var(--line); font-size: 13.5px;
  }
  .pill {
    font-family: var(--mono); font-size: 11.5px; font-weight: 700;
    letter-spacing: .08em; padding: 4px 10px; border-radius: 4px;
    background: var(--fg); color: var(--bg);
  }
  .pill.success { background: var(--attack); color: #fff; }
  .pill.failure { background: var(--ok); color: #fff; }
  .warn {
    margin: 14px 0; padding: 12px 15px; border-radius: 8px; font-size: 13px;
    border: 1px solid var(--attack); background: var(--attack-bg);
    color: var(--impact);
  }
  ol.timeline { list-style: none; margin: 22px 0 0; padding: 0 0 0 26px;
    border-left: 2px solid var(--line); }
  li.ev { position: relative; padding: 0 0 20px 18px; }
  li.ev::before {
    content: ""; position: absolute; left: -34px; top: 7px; width: 9px;
    height: 9px; border-radius: 50%; background: var(--line);
    box-shadow: 0 0 0 4px var(--bg);
  }
  li.ev.attack::before, li.ev.impact::before { background: var(--attack); }
  .ev-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 9px; }
  .seq { font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
  .actor {
    font-family: var(--mono); font-size: 12.5px; font-weight: 700;
    color: var(--accent);
  }
  .title { font-size: 13.5px; }
  li.ev.attack .title, li.ev.impact .title { color: var(--impact); font-weight: 700; }
  .badge {
    font-family: var(--mono); font-size: 10px; font-weight: 700;
    letter-spacing: .09em; padding: 2px 7px; border-radius: 3px;
    background: var(--attack); color: #fff;
  }
  dl.rows { margin: 7px 0 0; display: grid; grid-template-columns: 132px 1fr;
    gap: 3px 12px; font-size: 12.5px; }
  dl.rows dt { color: var(--muted); font-family: var(--mono); font-size: 11.5px; }
  dl.rows dd { margin: 0; font-family: var(--mono); font-size: 12.5px;
    word-break: break-word; }
  li.ev.attack, li.ev.impact { background: var(--attack-bg); border-radius: 7px;
    padding: 9px 12px 12px 18px; margin-left: -12px; }
  footer { margin-top: 44px; padding-top: 14px; border-top: 1px solid var(--line);
    color: var(--muted); font-size: 12px; }
  @media print {
    .tabs { display: none; }
    li.ev.attack, li.ev.impact { border: 1px solid #999; }
  }
</style>
<div class="wrap">
  <header>
    <h1>TrustGuard — inter-agent trust exploitation</h1>
    <div class="sub" id="sub"></div>
  </header>
  <div class="tabs" role="tablist">
    <button role="tab" data-arm="attack">Attack arm</button>
    <button role="tab" data-arm="control">Control arm</button>
  </div>
  <div id="verdict"></div>
  <div class="meta" id="meta"></div>
  <ol class="timeline" id="timeline"></ol>
  <footer id="footer"></footer>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
  const DATA = JSON.parse(document.getElementById("data").textContent);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

  function render(which) {
    const arm = DATA[which];
    $("sub").textContent =
      arm.attack_name + " — " + (which === "attack"
        ? "attack arm, the injection is present"
        : "control arm, the injection is removed");

    const v = arm.verdict;
    let html = "";
    if (v) {
      const label = String(v.verdict || "").toUpperCase();
      const cls = label === "SUCCESS" ? "success" : label === "FAILURE" ? "failure" : "";
      html += '<div class="verdict"><span class="pill ' + cls + '">' + esc(label) +
        "</span><span>oracle v" + esc(v.oracle_version) + " · checker " +
        esc(v.checker) + " v" + esc(v.checker_version) + " · decided by " +
        esc(v.decided_by) + "</span></div>";
      if (v.checker_validated === false) {
        html += '<div class="warn"><strong>This checker is not validated.</strong> ' +
          "No hand-labelled set exists for it yet, so this verdict carries no " +
          "measured agreement number and must not be quoted as a result.</div>";
      }
    }
    $("verdict").innerHTML = html;

    const rows = [
      ["run id", arm.run_id], ["variant", arm.variant],
      ["model", arm.model], ["injected at", arm.injection_point],
      ["ingestion mode", arm.ingestion_mode],
      ["saved report as", arm.filename || "(no save_report call)"],
    ];
    $("meta").innerHTML = rows.map(([k, val]) =>
      '<div><span class="k">' + esc(k) + '</span><span class="v">' +
      esc(val) + "</span></div>").join("");

    $("timeline").innerHTML = arm.events.map((e) => {
      const badge = e.flag === "attack"
        ? '<span class="badge">INJECTION</span>'
        : e.flag === "impact" ? '<span class="badge">IMPACT</span>' : "";
      const body = e.rows.length
        ? '<dl class="rows">' + e.rows.map(([k, val]) =>
            "<dt>" + esc(k) + "</dt><dd>" + esc(val) + "</dd>").join("") + "</dl>"
        : "";
      return '<li class="ev ' + (e.flag || "") + '"><div class="ev-head">' +
        '<span class="seq">' + String(e.seq).padStart(3, "0") + "</span>" +
        '<span class="actor">' + esc(e.actor) + "</span>" +
        '<span class="title">' + esc(e.title) + "</span>" + badge +
        "</div>" + body + "</li>";
    }).join("");

    $("footer").textContent =
      "Rendered from the recorded trace. Flags mark lines worth reading, not " +
      "verdicts — the verdict above is the oracle's. " + DATA.generated_at;

    document.querySelectorAll(".tabs button").forEach((b) =>
      b.setAttribute("aria-selected", String(b.dataset.arm === which)));
  }

  document.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => render(b.dataset.arm)));
  render("attack");
</script>
"""


def write_viewer(
    attack_trace: dict,
    control_trace: dict,
    attack_verdict: dict | None,
    control_verdict: dict | None,
    out_path: Path,
) -> Path:
    """Write the two-arm viewer to ``out_path`` and return it."""
    import datetime

    payload = {
        "attack": _arm(attack_trace, attack_verdict),
        "control": _arm(control_trace, control_verdict),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    # </script> inside the JSON would close the host tag early.
    blob = json.dumps(payload).replace("</", "<\\/")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_PAGE.replace("__DATA__", blob), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traces", nargs="*", help="attack trace, then control trace")
    parser.add_argument("--replay", help="a demo/*_replay.json pair instead")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.replay:
        saved = json.loads(Path(args.replay).read_text(encoding="utf-8"))
        attack_trace, control_trace = saved["attack"], saved["control"]
        attack_verdict = saved.get("attack_verdict")
        control_verdict = saved.get("control_verdict")
        default_out = Path(args.replay).with_suffix(".html")
    elif len(args.traces) == 2:
        attack_trace = json.loads(Path(args.traces[0]).read_text(encoding="utf-8"))
        control_trace = json.loads(Path(args.traces[1]).read_text(encoding="utf-8"))
        attack_verdict = attack_trace.get("oracle_verdict")
        control_verdict = control_trace.get("oracle_verdict")
        default_out = ROOT / "demo" / "viewer.html"
    else:
        parser.error("give two trace paths (attack, control) or --replay PAIR.json")

    out = write_viewer(
        attack_trace, control_trace, attack_verdict, control_verdict,
        Path(args.out) if args.out else default_out,
    )
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
