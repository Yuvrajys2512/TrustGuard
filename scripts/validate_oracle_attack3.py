#!/usr/bin/env python
"""Score attack 3's oracle checker against the hand labels, and report honestly.

    python scripts/validate_oracle_attack3.py --heuristics-only   # no API key needed
    python scripts/validate_oracle_attack3.py                     # full hybrid + judge
    python scripts/validate_oracle_attack3.py --labeler <your-name>    # a second labeler
    python scripts/validate_oracle_attack3.py --inter-annotator   # labeler vs labeler

This is the attack-3 analogue of `scripts/validate_oracle.py`, which is attack-2
specific: it reads `manifest.json` and `labels_<labeler>.json`, and its
"substantive" subset is defined by attack 2's `role_claim_present`. Attack 3
rides shared memory, so this script reads `manifest_attack3.json` and
`labels_attack3_<labeler>.json`, and defines substantive by attack 3's own
precondition. The two keep separate files so neither's numbers can be touched
by the other.

**This script does not flip `validated`.** It computes the number that a person
then decides is or is not good enough to flip it, and that decision goes in
`docs/decision_log.md` with the number beside it. Running this is a necessary
condition for reporting an attack-3 result, not a sufficient one.

What it reports, and why more than one number:

* **headline** — every labelled run. Includes the control arms, which the
  deterministic check settles trivially (no memory injection -> failure), so
  this number flatters.
* **substantive** — runs where a poisoned write actually reached the victim
  (`memory_injection_present and victim_read_poisoned_key`). The trivially
  decidable cases are gone, so this is the number that says whether the checker
  can do the job it exists for.
* **judge-dependent** — the runs the heuristics abstained on. For attack 3 this
  is the belief route — asserted-as-fact versus attributed — which the
  deterministic half cannot decide by design, so this subset is expected to be
  large. If it is too large for the hybrid number to be quotable, the fix named
  in docs/phase_4_taxonomy_breadth.md §2.4 is a pipeline whose victim can
  verify, not a cleverer heuristic.
* **ablations** — heuristics alone, judge alone, hybrid. Phase 3 found attack
  2's hybrid was *worse* than its own deterministic half; without the same
  ablations here there is no evidence either way for attack 3.

It also scores the **assertion** label (asserted / attributed / absent) against
what the checker mechanically saw propagate, because that distinction is the
one thing the judge exists to decide and it is worth seeing separately from the
success/failure verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from trustguard.oracle import ORACLE_VERSION, Oracle, format_report, score  # noqa: E402
from trustguard.oracle.oracle import CHECKERS  # noqa: E402
from trustguard.oracle.metrics import cohens_kappa  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

VALIDATION_DIR = Path("docs/validation_set")
MANIFEST = VALIDATION_DIR / "manifest_attack3.json"
RESULTS = VALIDATION_DIR / "oracle_results_attack3.json"
ATTACK_ID = "attack3_context_poison"


def load(labeler: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    path = VALIDATION_DIR / f"labels_attack3_{labeler}.json"
    if not path.exists():
        raise SystemExit(
            f"no labels at {path}. Build them with "
            f"`python scripts/label_runs_attack3.py --labeler {labeler}`."
        )
    return manifest, json.loads(path.read_text(encoding="utf-8"))


def is_success(label: str) -> bool:
    return label == "success"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labeler", default="assistant")
    parser.add_argument(
        "--heuristics-only",
        action="store_true",
        help="Skip the judge entirely. Runs the heuristics abstain on are "
             "scored as undecidable rather than guessed at.",
    )
    parser.add_argument(
        "--judge-always",
        action="store_true",
        default=True,
        help="Call the judge on every run, including ones the heuristics "
             "settle, so the judge-alone ablation is computable.",
    )
    parser.add_argument("--inter-annotator", action="store_true")
    parser.add_argument("--out", default=str(RESULTS))
    args = parser.parse_args()

    manifest, labels_doc = load(args.labeler)
    labels = labels_doc["labels"]
    by_id = {r["run_id"]: r for r in manifest["runs"]}

    if args.inter_annotator:
        return inter_annotator()

    spec = CHECKERS[ATTACK_ID]
    oracle = Oracle(judge_always=not args.heuristics_only and args.judge_always)

    rows: list[dict[str, Any]] = []
    print(f"oracle {ORACLE_VERSION}  |  attack-3 checker {spec.version} "
          f"(validated={spec.validated})  |  labeler {args.labeler!r}  |  "
          f"{len(labels)} labelled runs")
    if not spec.validated:
        print("checker is registered validated=False — nothing printed below "
              "may be quoted as a validated attack-3 result until that flag is "
              "flipped by a recorded decision.")
    print("-" * 78)

    for i, (run_id, label_rec) in enumerate(labels.items(), start=1):
        entry = by_id[run_id]
        trace = RunTrace.model_validate_json(
            Path(entry["trace"]).read_text(encoding="utf-8")
        )
        if args.heuristics_only:
            h = spec.heuristic(trace)
            verdict = {
                "oracle_version": ORACLE_VERSION,
                "checker_version": spec.version,
                "checker_validated": spec.validated,
                "verdict": h.verdict if h.decisive else "undecidable",
                "decided_by": "heuristic" if h.decisive else "none",
                "heuristic_only_verdict": h.verdict,
                "heuristic_decisive": h.decisive,
                "judge_only_verdict": None,
                "heuristic": h.as_dict(),
                "judge": None,
            }
        else:
            verdict = oracle.evaluate(trace)

        facts = verdict["heuristic"]["facts"]
        agree = (verdict["verdict"] == label_rec["label"])
        rows.append(
            {
                "run_id": run_id,
                "arm": entry["arm"],
                "objective": entry.get("objective"),
                "human_label": label_rec["label"],
                "human_assertion": label_rec.get("assertion"),
                "oracle_verdict": verdict["verdict"],
                "decided_by": verdict["decided_by"],
                "heuristic_only": verdict["heuristic_only_verdict"],
                "heuristic_decisive": verdict["heuristic_decisive"],
                "judge_only": verdict["judge_only_verdict"],
                "memory_injection_present": facts["memory_injection_present"],
                "victim_read_poisoned_key": facts["victim_read_poisoned_key"],
                "propagated": facts["propagated"],
                "filename_matches_implied": facts["filename_matches_implied"],
                "agree": agree,
                "judge_error": (verdict.get("judge") or {}).get("error"),
                "verdict_full": verdict,
            }
        )
        flag = "" if agree else "   <-- DISAGREE"
        print(f"[{i:>2}/{len(labels)}] {run_id}  {entry['arm']:<20} "
              f"human={label_rec['label']:<7} oracle={verdict['verdict']:<11} "
              f"({verdict['decided_by']}){flag}")

    print("-" * 78)
    report(rows, spec)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "oracle_version": ORACLE_VERSION,
                "attack_id": ATTACK_ID,
                "checker_version": spec.version,
                "checker_validated": spec.validated,
                "labeler": args.labeler,
                "heuristics_only": args.heuristics_only,
                "rows": rows,
                "summary": summarise(rows),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nfull results -> {out}")
    return 0


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def sc(subset: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        usable = [r for r in subset if r[key] in ("success", "failure")]
        if not usable:
            return None
        s = score(
            [is_success(r["human_label"]) for r in usable],
            [is_success(r[key]) for r in usable],
        )
        s["excluded_undecidable"] = len(subset) - len(usable)
        return s

    # Attack 3's precondition: a poisoned write existed AND the victim loaded
    # it. Everything else is decided by a check that reads no meaning at all.
    substantive = [
        r for r in rows
        if r["memory_injection_present"] and r["victim_read_poisoned_key"]
    ]
    judge_dependent = [r for r in rows if not r["heuristic_decisive"]]

    return {
        "headline_hybrid": sc(rows, "oracle_verdict"),
        "substantive_hybrid": sc(substantive, "oracle_verdict"),
        "judge_dependent_hybrid": sc(judge_dependent, "oracle_verdict"),
        "ablation_heuristics_only": sc(rows, "heuristic_only"),
        "ablation_judge_only": sc(rows, "judge_only"),
        "ablation_judge_only_substantive": sc(substantive, "judge_only"),
        "counts": {
            "total": len(rows),
            "substantive": len(substantive),
            "judge_dependent": len(judge_dependent),
            "judge_share": (
                len(judge_dependent) / len(rows) if rows else None
            ),
            "heuristic_decisive": sum(1 for r in rows if r["heuristic_decisive"]),
            "judge_errors": sum(1 for r in rows if r["judge_error"]),
            "undecidable": sum(
                1 for r in rows if r["oracle_verdict"] == "undecidable"
            ),
        },
        "assertion": assertion_breakdown(rows),
    }


def assertion_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How the human's assertion call lines up with mechanical propagation.

    `propagated` is a marker-presence check: it cannot tell asserted from
    attributed. Cross-tabulating it against the human's assertion label shows
    exactly how much of the verdict the judge is being asked to carry.
    """
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        a = r["human_assertion"] or "unlabelled"
        table[a]["n"] += 1
        table[a]["propagated"] += int(bool(r["propagated"]))
        table[a]["human_success"] += int(r["human_label"] == "success")
    return {k: dict(v) for k, v in table.items()}


def report(rows: list[dict[str, Any]], spec: Any) -> None:
    s = summarise(rows)
    c = s["counts"]

    share = c["judge_share"]
    print(f"\n{c['total']} labelled runs | "
          f"{c['heuristic_decisive']} settled by heuristics, "
          f"{c['judge_dependent']} deferred to the judge"
          + (f" ({share * 100:.1f}% judge share)" if share is not None else "")
          + f" | {c['judge_errors']} judge errors | "
          f"{c['undecidable']} undecidable\n")

    for name, key in [
        ("HEADLINE  hybrid vs human, all runs", "headline_hybrid"),
        ("SUBSTANTIVE  hybrid, poisoned-write-reached-victim runs", "substantive_hybrid"),
        ("JUDGE-DEPENDENT  hybrid, heuristics abstained", "judge_dependent_hybrid"),
        ("ABLATION  heuristics alone", "ablation_heuristics_only"),
        ("ABLATION  judge alone", "ablation_judge_only"),
        ("ABLATION  judge alone, substantive runs", "ablation_judge_only_substantive"),
    ]:
        if s[key]:
            print(format_report(name, s[key]))
            print()

    print("Human assertion label vs mechanical propagation:")
    for a, v in sorted(s["assertion"].items()):
        print(f"  {a:<12} n={v['n']:<3} markers propagated {v['propagated']}/{v['n']}"
              f"   human called success {v['human_success']}/{v['n']}")
    print()

    disagreements = [r for r in rows if not r["agree"]]
    if disagreements:
        print(f"DISAGREEMENTS ({len(disagreements)}) — each one is a finding, "
              "not noise:\n")
        for r in disagreements:
            print(f"  {r['run_id']}  arm={r['arm']}  "
                  f"human={r['human_label']}/{r['human_assertion']}  "
                  f"oracle={r['oracle_verdict']}  ({r['decided_by']})")
            j = (r["verdict_full"].get("judge") or {})
            if j.get("reasoning"):
                print(f"      judge said: {j['reasoning']}")
            print(f"      heuristic : {r['verdict_full']['heuristic']['reason']}")
            print()
    else:
        print("No disagreements. Read the substantive and judge-dependent "
              "numbers before treating that as good news — a set where the "
              "heuristics settle almost everything is measuring the "
              "heuristics, not the checker.\n")

    per_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        per_arm[r["arm"]].append(r)
    print("Per-arm human labels (the attack's own result, not the oracle's):")
    for arm in sorted(per_arm):
        rs = per_arm[arm]
        n_s = sum(1 for r in rs if r["human_label"] == "success")
        agree = sum(1 for r in rs if r["agree"])
        print(f"  {arm:<22} success {n_s}/{len(rs)}   "
              f"oracle agrees {agree}/{len(rs)}")

    if not spec.validated:
        print("\nReminder: attack 3's checker is still registered "
              "validated=False. Flipping it is a decision to record in "
              "docs/decision_log.md with these numbers beside it, not "
              "something this script does.")


def inter_annotator() -> int:
    """Labeler-vs-labeler agreement on attack 3's set only."""
    files = sorted(VALIDATION_DIR.glob("labels_attack3_*.json"))
    docs = {
        p.stem.replace("labels_attack3_", ""): json.loads(
            p.read_text(encoding="utf-8")
        )
        for p in files
    }
    names = sorted(docs)
    if len(names) < 2:
        print(f"only one attack-3 label set on disk ({names}). A second "
              "independent labeler is what turns 'the checker agrees with a "
              "blinded LLM labeler' into 'the checker agrees with a human' — "
              "run `python scripts/label_runs_attack3.py --labeler <name>`.")
        return 0

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = sorted(set(docs[a]["labels"]) & set(docs[b]["labels"]))
            if not shared:
                print(f"{a} vs {b}: no runs labelled by both")
                continue
            la = [is_success(docs[a]["labels"][r]["label"]) for r in shared]
            lb = [is_success(docs[b]["labels"][r]["label"]) for r in shared]
            same = sum(1 for x, y in zip(la, lb) if x == y)
            print(f"{a} vs {b}: {same}/{len(shared)} = "
                  f"{same / len(shared) * 100:.1f}% raw, "
                  f"kappa {cohens_kappa(la, lb):.3f}")
            for r in shared:
                if docs[a]["labels"][r]["label"] != docs[b]["labels"][r]["label"]:
                    print(f"   differ on {r}: {a}="
                          f"{docs[a]['labels'][r]['label']} "
                          f"{b}={docs[b]['labels'][r]['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
