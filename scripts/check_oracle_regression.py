#!/usr/bin/env python
"""Permanent regression check for the oracle. Run this after ANY oracle change.

    python scripts/check_oracle_regression.py            # check against baseline
    python scripts/check_oracle_regression.py --freeze    # write a new baseline

The phase spec's tip, made executable: "Keep the validation set aside as a
permanent regression check. Any time you change the oracle later, re-run it
against these labels and confirm the agreement number didn't drop."

**The gate is on the deterministic half only, and that is deliberate.** The
judge flips its verdict on roughly a third of the runs it decides (see
`judge_stability.py`), so a gate that included it would fail at random and be
switched off within a week — the classic flaky-test death spiral, except the
thing going quiet would be the project's central reliability claim. What is
pinned instead is:

* the per-run heuristic verdict, for all 46 runs;
* which runs the heuristics consider themselves entitled to decide;
* the heuristics-only agreement, precision, recall and kappa.

All of it is computable with no API key and no network, so this check runs
anywhere, in CI, in seconds.

A change that moves any pinned value is not necessarily wrong — it may be a
genuine improvement — but it must be *noticed*, explained, and re-frozen with a
decision-log entry. Silent movement is the failure mode this exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from trustguard.oracle import ORACLE_VERSION  # noqa: E402
from trustguard.oracle.heuristics import judge_attack2_heuristically  # noqa: E402
from trustguard.oracle.metrics import score  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

VD = Path("docs/validation_set")
BASELINE = VD / "frozen_baseline.json"


def compute() -> dict[str, Any]:
    manifest = json.loads((VD / "manifest.json").read_text(encoding="utf-8"))
    labels = json.loads(
        (VD / "labels_assistant.json").read_text(encoding="utf-8")
    )["labels"]

    per_run: dict[str, dict[str, Any]] = {}
    human, predicted = [], []
    for entry in manifest["runs"]:
        trace = RunTrace.model_validate_json(
            Path(entry["trace"]).read_text(encoding="utf-8")
        )
        h = judge_attack2_heuristically(trace)
        per_run[entry["run_id"]] = {
            "verdict": h.verdict,
            "decisive": h.decisive,
        }
        human.append(labels[entry["run_id"]]["label"] == "success")
        predicted.append(h.verdict == "success")

    s = score(human, predicted)
    return {
        "oracle_version": ORACLE_VERSION,
        "n": len(per_run),
        "heuristics_only": {
            "agreement": s["agreement"],
            "precision": s["precision"],
            "recall": s["recall"],
            "cohens_kappa": s["cohens_kappa"],
            "confusion": s["confusion"],
        },
        "decisive_count": sum(1 for v in per_run.values() if v["decisive"]),
        "per_run": per_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()

    current = compute()

    if args.freeze or not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(current, indent=2), encoding="utf-8")
        print(f"baseline frozen at oracle {current['oracle_version']} -> "
              f"{BASELINE}")
        print(f"  heuristics-only agreement "
              f"{current['heuristics_only']['agreement'] * 100:.1f}%, "
              f"kappa {current['heuristics_only']['cohens_kappa']:.3f}, "
              f"{current['decisive_count']}/{current['n']} decisive")
        return 0

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    problems: list[str] = []

    if baseline["n"] != current["n"]:
        problems.append(
            f"validation set size changed: {baseline['n']} -> {current['n']}. "
            "The set is meant to be permanent; adding or removing runs makes "
            "the agreement number incomparable to every previously reported "
            "one."
        )

    for run_id, was in baseline["per_run"].items():
        now = current["per_run"].get(run_id)
        if now is None:
            problems.append(f"{run_id}: present in baseline, missing now")
        elif now != was:
            problems.append(
                f"{run_id}: heuristic {was['verdict']}"
                f"/decisive={was['decisive']} -> {now['verdict']}"
                f"/decisive={now['decisive']}"
            )

    old_a = baseline["heuristics_only"]["agreement"]
    new_a = current["heuristics_only"]["agreement"]
    old_k = baseline["heuristics_only"]["cohens_kappa"]
    new_k = current["heuristics_only"]["cohens_kappa"]

    print(f"oracle in tree: {current['oracle_version']}   "
          f"baseline frozen at: {baseline['oracle_version']}")
    print(f"heuristics-only agreement  {old_a * 100:.1f}% -> {new_a * 100:.1f}%")
    print(f"heuristics-only kappa      {old_k:.3f} -> {new_k:.3f}")
    print(f"decisive runs              {baseline['decisive_count']} -> "
          f"{current['decisive_count']}")

    if new_a < old_a:
        problems.append(
            f"AGREEMENT DROPPED: {old_a * 100:.1f}% -> {new_a * 100:.1f}%"
        )

    if problems:
        print(f"\nFAIL — {len(problems)} regression(s):\n")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nIf this change is deliberate and correct, re-freeze with "
            "--freeze and add a decision-log entry saying what moved and why. "
            "Do not re-freeze to make the check quiet."
        )
        return 1

    print("\nPASS — no per-run verdict changed and agreement did not drop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
