#!/usr/bin/env python
"""Measure how stable the LLM judge is when asked the same question repeatedly.

    python scripts/judge_stability.py --repeats 3

The oracle is supposed to be a frozen measuring instrument. A deterministic
heuristic trivially is one. An LLM judge at ``temperature=0`` looks like one
and is not: this project has already measured Groq accepting a ``seed`` and
ignoring it (README; decision log, 2026-08-20), and the same non-determinism
that moves generated text can move a verdict.

This was not a hypothetical. During Phase 3 the judge returned *failure* on
`run_945de6f30d69` in a smoke test and *success* on the same trace, same code,
same prompt, minutes later in the scoring pass. A measuring instrument whose
reading changes between two looks at the same object needs its variance
reported, not assumed away.

Only the judge-dependent runs are re-run — the ones the heuristics settle have
no judge in the loop and are deterministic by construction.
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

from trustguard.oracle.heuristics import (  # noqa: E402
    extract_attack2_facts,
    judge_attack2_heuristically,
)
from trustguard.oracle.judge import Judge  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

VALIDATION_DIR = Path("docs/validation_set")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default=str(VALIDATION_DIR / "judge_stability.json"))
    args = parser.parse_args()

    manifest = json.loads(
        (VALIDATION_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    labels = json.loads(
        (VALIDATION_DIR / "labels_assistant.json").read_text(encoding="utf-8")
    )["labels"]

    judge = Judge()
    results: dict[str, dict] = {}

    targets = []
    for entry in manifest["runs"]:
        trace = RunTrace.model_validate_json(
            Path(entry["trace"]).read_text(encoding="utf-8")
        )
        if not judge_attack2_heuristically(trace).decisive:
            targets.append((entry, trace))

    print(f"{len(targets)} judge-dependent runs x {args.repeats} repeats "
          f"= {len(targets) * args.repeats} judge calls\n")

    for entry, trace in targets:
        facts = extract_attack2_facts(trace)
        verdicts = []
        for _ in range(args.repeats):
            verdicts.append(judge.judge_attack2(trace, facts).get("verdict"))
        counts = Counter(verdicts)
        stable = len(counts) == 1
        human = labels[entry["run_id"]]["label"]
        results[entry["run_id"]] = {
            "arm": entry["arm"],
            "human_label": human,
            "verdicts": verdicts,
            "stable": stable,
            "modal": counts.most_common(1)[0][0],
        }
        mark = "" if stable else "   <-- FLIPPED"
        print(f"  {entry['run_id']}  {entry['arm']:<21} human={human:<7} "
              f"judge={verdicts}{mark}")

    n_stable = sum(1 for r in results.values() if r["stable"])
    print(f"\n{n_stable}/{len(results)} judge-dependent runs returned the same "
          f"verdict on all {args.repeats} attempts.")
    if n_stable < len(results):
        print("The oracle is NOT deterministic on the runs its judge decides. "
              "That is a property of the instrument and belongs in the "
              "deliverable, not in a footnote.")

    Path(args.out).write_text(
        json.dumps(
            {"repeats": args.repeats, "runs": results,
             "stable": n_stable, "total": len(results)},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
