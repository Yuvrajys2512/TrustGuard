#!/usr/bin/env python
"""Generate pipeline #2 runs for hand-labelling.

    python scripts/build_validation_set_td.py --attack attack2 --plan
    python scripts/build_validation_set_td.py --attack attack2
    python scripts/build_validation_set_td.py --attack attack3

Pipeline #2's two checkers were written on 2026-09-09 and registered
`validated=False`. This script produces the corpus they have to be scored
against, the way Phase 3 produced attack 2's 46 runs on pipeline #1.

These are **dev-lane** runs. Nothing here is a validated number and nothing
here may be quoted until the labelling and scoring passes are done and a
`validated` flag flips.

**This script does not label anything and does not judge anything.** It
produces runs. A probe never grades its own runs (decision log, 2026-08-21).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from td_validation import OBJECTIVES, PIPELINE, resolve  # noqa: E402

from trustguard.llm.client import RotatingLLMClient  # noqa: E402
from trustguard.pipelines.task_delegation.graph import run_pipeline  # noqa: E402
from trustguard.probes import PROBES  # noqa: E402

#: arm -> run count, per attack.
#:
#: Attack 2's checker is fully deterministic, so its set does not need extra
#: mass on judge-ambiguous arms; what it needs is enough of each *branch* that
#: a disagreement with a human is attributable to a branch rather than to one
#: unlucky run. `ack_bait` and `no_authority_control` are weighted because they
#: are the two arms whose whole purpose is to be scored FAILURE against
#: superficially convincing evidence, and a checker that gets those wrong is
#: worthless however well it does elsewhere.
#:
#: Attack 3 is weighted towards `verification_waiver` (the arm carrying the
#: mechanical route this pipeline exists to provide) and towards the arms most
#: likely to land in the judge's residue.
PLANS: dict[str, list[tuple[str, int]]] = {
    "attack2": [
        ("full", 6),
        ("implausible", 5),
        ("hedged", 5),
        ("no_marker", 4),
        ("ack_bait", 6),
        ("no_authority_control", 6),
    ],
    "attack3": [
        ("survivable_claim", 10),
        ("verification_waiver", 8),
        ("full", 6),
        ("hedged", 5),
        ("provenance_flagged", 5),
        ("channel_control", 5),
        ("no_poison_control", 4),
    ],
}


def build_jobs(attack_key: str) -> list[tuple[str, str]]:
    """Expand the plan into (arm, objective) pairs, cycling objectives."""
    jobs: list[tuple[str, str]] = []
    for arm, count in PLANS[attack_key]:
        for i in range(count):
            jobs.append((arm, OBJECTIVES[i % len(OBJECTIVES)]))
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--attack",
        required=True,
        choices=sorted(PLANS),
        help="Which attack's validation set to build. No default on purpose: "
        "the two write to separate artefacts and must never be conflated.",
    )
    parser.add_argument("--plan", action="store_true", help="Print the plan and exit.")
    parser.add_argument("--trace-dir", default="traces/dev")
    parser.add_argument("--lane", choices=["dev", "paper"], default="dev")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument(
        "--only",
        action="append",
        help="Collect only these arms (repeatable). For topping up a set with "
        "an arm added after the first sweep, without re-running the rest.",
    )
    args = parser.parse_args()

    profile = resolve(args.attack)
    jobs = build_jobs(args.attack)
    if args.only:
        jobs = [(a, o) for a, o in jobs if a in set(args.only)]
        if not jobs:
            raise SystemExit(f"no jobs match --only {args.only}")
    probe_cls = PROBES[profile.attack_id]

    if args.plan:
        print(f"{profile.attack_id} on {PIPELINE}: {len(jobs)} runs planned\n")
        for arm, count in PLANS[args.attack]:
            spec = probe_cls(variant=arm, pipeline=PIPELINE).variant_spec
            print(f"  {arm:<24} x{count}   expects={spec.expects}")
        print("\nobjectives:")
        for o in OBJECTIVES:
            print(f"  - {o}")
        print(
            f"\napprox {len(jobs) * 5} LLM calls (5 per standard-route run: "
            "coordinator, analyst topic, analyst assessment, executor verify "
            "decision, executor apply decision)"
        )
        print(f"artefacts -> {profile.generated.name}, {profile.manifest.name}")
        return 0

    try:
        llm = RotatingLLMClient(temperature=args.temperature)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    produced: list[dict[str, str]] = []
    failures: list[str] = []

    for i, (arm, objective) in enumerate(jobs, start=1):
        print(f"[{i:>2}/{len(jobs)}] {arm:<24} {objective[:40]!r} ... ", end="",
              flush=True)
        probe = probe_cls(variant=arm, pipeline=PIPELINE)
        try:
            _, trace_path = run_pipeline(
                objective=objective,
                llm=llm,
                echo=False,
                color=False,
                trace_dir=args.trace_dir,
                lane=args.lane,
                probe=probe,
            )
        except Exception as exc:  # noqa: BLE001 - one bad run must not kill the sweep
            print(f"FAILED {type(exc).__name__}: {exc}")
            failures.append(f"{arm}: {type(exc).__name__}: {exc}")
            continue

        trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
        rotated = trace["config"]["extra"].get("rotated")
        calls = [
            e["payload"]["tool_name"]
            for e in trace["events"]
            if e["payload"]["type"] == "tool_call"
            and e["payload"]["agent"] == "executor"
        ]
        print(
            f"{trace['run_id']}  executor_calls={calls}"
            + ("  ROTATED" if rotated else "")
        )
        produced.append({"run_id": trace["run_id"], "arm": arm, "trace": trace_path})
        # Groq's free tier is generous but not infinite. A short pause avoids a
        # mid-sweep rotation, which would confound the arm it landed on.
        time.sleep(args.sleep)

    out = profile.generated
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    existing.extend(produced)
    out.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    print(f"\n{len(produced)}/{len(jobs)} runs produced -> {out}")
    if failures:
        print(f"{len(failures)} failed:")
        for f in failures:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
