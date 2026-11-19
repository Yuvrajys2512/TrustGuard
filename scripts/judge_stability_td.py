#!/usr/bin/env python
"""Measure how stable a pipeline #2 judge is when asked the same question twice.

    python scripts/judge_stability_td.py --attack attack3 --repeats 3

Every judge-dependent number in this project is reported as a **range over at
least three judge realisations**, never a point estimate (decision log,
2026-08-21). Groq accepts a `seed` and does not honour it, so a scoring pass is
a draw from a distribution and a single run of it is one sample dressed up as a
measurement.

This script takes the same labelled set, re-scores it N times, and reports:

* the agreement range across realisations — the number that may be quoted;
* per-run stability: how many runs got the same verdict every time. A judge
  that is 90% accurate and 100% stable is a different instrument from one that
  is 90% accurate by averaging two opposite answers, and only the second is a
  reason to distrust individual verdicts.

Attack 2 on this pipeline has **no judge** — every branch of its checker is
decisive — so this script refuses to run for it rather than producing a
meaningless range of identical numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from td_validation import PIPELINE, resolve  # noqa: E402
from validate_oracle_td import evaluate, is_success, summarise  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attack", required=True, choices=["attack2", "attack3"])
    parser.add_argument("--labeler", default="assistant")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    profile = resolve(args.attack)

    if not profile.has_judge:
        print(
            f"{profile.attack_id} on {PIPELINE} has no judge: every branch of "
            "its checker is decisive, so re-scoring it would produce the same "
            "numbers every time.\n\n"
            "That is not a stability result, it is a tautology. Its agreement "
            "number is a point estimate legitimately, and needs no range - "
            "which is one of the few advantages of a fully deterministic "
            "checker and should be stated as such rather than hidden behind a "
            "range of identical values.",
            file=sys.stderr,
        )
        return 1

    if args.repeats < 3:
        print("refusing: the reporting rule requires at least 3 realisations "
              "(decision log 2026-08-21).", file=sys.stderr)
        return 1

    realisations: list[dict[str, Any]] = []
    per_run: dict[str, list[str]] = {}

    for i in range(args.repeats):
        print(f"[{i + 1}/{args.repeats}] scoring ... ", end="", flush=True)
        rows = evaluate(profile, args.labeler, heuristics_only=False, judge_always=False)
        summary = summarise(profile, rows)
        sub = summary["substantive_hybrid"]
        agreement = sub["agreement"] if sub else 0.0
        print(f"substantive agreement {agreement:.1%}")
        realisations.append(
            {
                "agreement_substantive": agreement,
                "kappa_substantive": sub["cohens_kappa"] if sub else None,
                "agreement_all": summary["all_hybrid"]["agreement"],
                "judge_share_substantive": summary["counts"][
                    "judge_share_substantive"
                ],
                "judge_errors": summary["counts"]["judge_errors"],
            }
        )
        for r in rows:
            per_run.setdefault(r["run_id"], []).append(str(r["oracle_verdict"]))

    agreements = [r["agreement_substantive"] for r in realisations]
    kappas = [r["kappa_substantive"] for r in realisations if r["kappa_substantive"]]

    unstable = {
        rid: Counter(v).most_common()
        for rid, v in per_run.items()
        if len(set(v)) > 1
    }

    print("\n" + "=" * 70)
    print(f"{profile.attack_id} on {PIPELINE} — {args.repeats} judge realisations")
    print("=" * 70)
    print(f"substantive agreement RANGE : {min(agreements):.1%} – {max(agreements):.1%}")
    if kappas:
        print(f"kappa RANGE                 : {min(kappas):.3f} – {max(kappas):.3f}")
    print(f"judge share (substantive)   : "
          f"{realisations[0]['judge_share_substantive']:.1%}")
    print(f"per-run stability           : "
          f"{len(per_run) - len(unstable)}/{len(per_run)} runs identical every time")
    if unstable:
        print("\nruns whose verdict moved between realisations:")
        for rid, counts in unstable.items():
            print(f"  {rid}: {counts}")
    else:
        print("\nno run changed verdict across realisations.")

    print(
        "\nQuote the RANGE, never one endpoint, and never a mean. "
        "Groq ignores `seed`."
    )

    out = profile.judge_stability
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "attack_id": profile.attack_id,
                "pipeline": PIPELINE,
                "labeler": args.labeler,
                "repeats": args.repeats,
                "realisations": realisations,
                "agreement_range": [min(agreements), max(agreements)],
                "kappa_range": [min(kappas), max(kappas)] if kappas else None,
                "per_run_verdicts": per_run,
                "unstable_runs": {k: dict(Counter(v)) for k, v in per_run.items()
                                  if len(set(v)) > 1},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"written -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
