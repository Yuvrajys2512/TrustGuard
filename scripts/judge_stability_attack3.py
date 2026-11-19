#!/usr/bin/env python
"""Measure how stable attack 3's judge is when asked the same question twice.

    python scripts/judge_stability_attack3.py --repeats 3

The attack-3 analogue of `scripts/judge_stability.py`, which is attack-2
specific (it reads `manifest.json` / `labels_assistant.json` and calls
`judge_attack2`). Same reason for existing, and a stronger one: the decision
log's 2026-08-21 entry established that a judge-dependent agreement number is a
**range**, not a point estimate, because Groq accepts a `seed` and ignores it.
Attack 3 defers ~69% of its runs to the judge against attack 2's ~22%, so
whatever variance the judge has, attack 3's headline inherits about three times
as much of it. Quoting a bare attack-3 agreement number without running this
would be quoting one draw from a distribution.

Only the judge-dependent runs are re-run — the ones the deterministic half
settles have no judge in the loop and are stable by construction.

Reports both the per-run flip count and, more usefully, the **spread in the
headline agreement number** that those flips produce, since that is the figure
a reader of the Phase 4 matrix actually consumes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from trustguard.oracle.heuristics_attack3 import (  # noqa: E402
    extract_attack3_facts,
    judge_attack3_heuristically,
)
from trustguard.oracle.judge_attack3 import Attack3Judge  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

VALIDATION_DIR = Path("docs/validation_set")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--labeler", default="assistant")
    parser.add_argument(
        "--out", default=str(VALIDATION_DIR / "judge_stability_attack3.json")
    )
    args = parser.parse_args()

    manifest = json.loads(
        (VALIDATION_DIR / "manifest_attack3.json").read_text(encoding="utf-8")
    )
    labels = json.loads(
        (VALIDATION_DIR / f"labels_attack3_{args.labeler}.json").read_text(
            encoding="utf-8"
        )
    )["labels"]

    judge = Attack3Judge()
    results: dict[str, dict] = {}

    targets = []
    decisive_correct = 0
    decisive_total = 0
    for entry in manifest["runs"]:
        trace = RunTrace.model_validate_json(
            Path(entry["trace"]).read_text(encoding="utf-8")
        )
        h = judge_attack3_heuristically(trace)
        if h.decisive:
            decisive_total += 1
            decisive_correct += int(h.verdict == labels[entry["run_id"]]["label"])
        else:
            targets.append((entry, trace))

    print(f"{len(targets)} judge-dependent runs x {args.repeats} repeats "
          f"= {len(targets) * args.repeats} judge calls")
    print(f"({decisive_total} runs settled deterministically, "
          f"{decisive_correct} of them agreeing with the human — these do not "
          f"move between draws)\n")

    for entry, trace in targets:
        facts = extract_attack3_facts(trace)
        verdicts = []
        for _ in range(args.repeats):
            verdicts.append(judge.judge_attack3(trace, facts).get("verdict"))
        counts = Counter(v for v in verdicts if v)
        stable = len(counts) == 1
        human = labels[entry["run_id"]]["label"]
        results[entry["run_id"]] = {
            "arm": entry["arm"],
            "human_label": human,
            "human_assertion": labels[entry["run_id"]].get("assertion"),
            "verdicts": verdicts,
            "stable": stable,
            "modal": counts.most_common(1)[0][0] if counts else None,
        }
        mark = "" if stable else "   <-- FLIPPED"
        print(f"  {entry['run_id']}  {entry['arm']:<20} human={human:<7} "
              f"judge={verdicts}{mark}")

    n_stable = sum(1 for r in results.values() if r["stable"])
    total = decisive_total + len(results)

    # The number a reader actually consumes: headline agreement per draw.
    per_draw = []
    for i in range(args.repeats):
        agree = decisive_correct + sum(
            1 for r in results.values() if r["verdicts"][i] == r["human_label"]
        )
        per_draw.append(agree / total)

    print(f"\n{n_stable}/{len(results)} judge-dependent runs returned the same "
          f"verdict on all {args.repeats} attempts.")
    print("headline agreement per draw: "
          + ", ".join(f"{p * 100:.1f}%" for p in per_draw))
    print(f"=> report as {min(per_draw) * 100:.1f}%-{max(per_draw) * 100:.1f}% "
          f"over {args.repeats} judge realisations, never as a point estimate "
          "(decision log, 2026-08-21).")
    if n_stable < len(results):
        print("The attack-3 checker is NOT deterministic on the runs its judge "
              "decides, and it defers most of its set to that judge. That is a "
              "property of the instrument and belongs in the deliverable, not "
              "in a footnote.")

    Path(args.out).write_text(
        json.dumps(
            {
                "repeats": args.repeats,
                "labeler": args.labeler,
                "runs": results,
                "stable": n_stable,
                "judge_dependent": len(results),
                "deterministic_total": decisive_total,
                "deterministic_correct": decisive_correct,
                "headline_agreement_per_draw": per_draw,
                "headline_agreement_range": [min(per_draw), max(per_draw)],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
