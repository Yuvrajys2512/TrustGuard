#!/usr/bin/env python
"""Score the oracle against the frozen hand labels, and report honestly.

    python scripts/validate_oracle.py --heuristics-only   # no API key needed
    python scripts/validate_oracle.py                     # full hybrid + judge
    python scripts/validate_oracle.py --labeler <your-name>    # score vs a second labeler
    python scripts/validate_oracle.py --inter-annotator   # labeler vs labeler

This is also the **permanent regression check**. Any later change to the oracle
gets re-run against these same labels; if the agreement number drops, the
change broke something. That is the whole reason the validation set is kept
rather than discarded after the phase.

What it reports, and why more than one number:

* **headline** — every labelled run. Includes benign runs and runs with no role
  claim, which any correct oracle gets right trivially, so this number flatters.
* **substantive** — runs where a role claim was actually delivered to the
  victim. The trivially-decidable cases are removed, so this is the number that
  says whether the oracle can do the job it exists for.
* **judge-dependent** — the runs the heuristics abstained on. Small, and the
  only place the LLM judge's quality shows up at all.
* **ablations** — heuristics alone, judge alone, hybrid. Without these there is
  no evidence the hybrid earns its complexity.

Reporting the flattering number alone would be the easiest and least honest
thing this script could do, which is why it prints all of them.
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
from trustguard.oracle.metrics import cohens_kappa  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

VALIDATION_DIR = Path("docs/validation_set")
MANIFEST = VALIDATION_DIR / "manifest.json"
RESULTS = VALIDATION_DIR / "oracle_results.json"


def load(labeler: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    path = VALIDATION_DIR / f"labels_{labeler}.json"
    if not path.exists():
        raise SystemExit(f"no labels at {path}")
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
             "settle, so the judge-alone ablation is computable. On by "
             "default for the validation pass.",
    )
    parser.add_argument("--inter-annotator", action="store_true")
    parser.add_argument("--out", default=str(RESULTS))
    args = parser.parse_args()

    manifest, labels_doc = load(args.labeler)
    labels = labels_doc["labels"]
    by_id = {r["run_id"]: r for r in manifest["runs"]}

    if args.inter_annotator:
        return inter_annotator(manifest)

    oracle = Oracle(judge_always=not args.heuristics_only and args.judge_always)

    rows: list[dict[str, Any]] = []
    print(f"oracle {ORACLE_VERSION}  |  labeler {args.labeler!r}  |  "
          f"{len(labels)} labelled runs")
    print("-" * 78)

    for i, (run_id, label_rec) in enumerate(labels.items(), start=1):
        entry = by_id[run_id]
        trace = RunTrace.model_validate_json(
            Path(entry["trace"]).read_text(encoding="utf-8")
        )
        if args.heuristics_only:
            from trustguard.oracle.heuristics import judge_attack2_heuristically

            h = judge_attack2_heuristically(trace)
            verdict = {
                "oracle_version": ORACLE_VERSION,
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

        agree = (verdict["verdict"] == label_rec["label"])
        rows.append(
            {
                "run_id": run_id,
                "arm": entry["arm"],
                "victim_backend": entry.get("victim_backend"),
                "human_label": label_rec["label"],
                "human_gate_crossed": label_rec["gate_crossed"],
                "oracle_verdict": verdict["verdict"],
                "decided_by": verdict["decided_by"],
                "heuristic_only": verdict["heuristic_only_verdict"],
                "heuristic_decisive": verdict["heuristic_decisive"],
                "judge_only": verdict["judge_only_verdict"],
                "role_claim_present": verdict["heuristic"]["facts"][
                    "role_claim_present"
                ],
                "agree": agree,
                "judge_error": (verdict.get("judge") or {}).get("error"),
                "verdict_full": verdict,
            }
        )
        flag = "" if agree else "   <-- DISAGREE"
        print(f"[{i:>2}/{len(labels)}] {run_id}  {entry['arm']:<21} "
              f"human={label_rec['label']:<7} oracle={verdict['verdict']:<11} "
              f"({verdict['decided_by']}){flag}")

    print("-" * 78)
    report(rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "oracle_version": ORACLE_VERSION,
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

    substantive = [r for r in rows if r["role_claim_present"]]
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
            "heuristic_decisive": sum(1 for r in rows if r["heuristic_decisive"]),
            "judge_errors": sum(1 for r in rows if r["judge_error"]),
            "undecidable": sum(
                1 for r in rows if r["oracle_verdict"] == "undecidable"
            ),
        },
    }


def report(rows: list[dict[str, Any]]) -> None:
    s = summarise(rows)
    c = s["counts"]

    print(f"\n{c['total']} labelled runs | "
          f"{c['heuristic_decisive']} settled by heuristics, "
          f"{c['judge_dependent']} deferred to the judge | "
          f"{c['judge_errors']} judge errors | "
          f"{c['undecidable']} undecidable\n")

    for name, key in [
        ("HEADLINE  hybrid vs human, all runs", "headline_hybrid"),
        ("SUBSTANTIVE  hybrid, role-claim runs only", "substantive_hybrid"),
        ("JUDGE-DEPENDENT  hybrid, heuristics abstained", "judge_dependent_hybrid"),
        ("ABLATION  heuristics alone", "ablation_heuristics_only"),
        ("ABLATION  judge alone", "ablation_judge_only"),
        ("ABLATION  judge alone, role-claim runs", "ablation_judge_only_substantive"),
    ]:
        if s[key]:
            print(format_report(name, s[key]))
            print()

    disagreements = [r for r in rows if not r["agree"]]
    if disagreements:
        print(f"DISAGREEMENTS ({len(disagreements)}) — each one is a finding, "
              "not noise:\n")
        for r in disagreements:
            print(f"  {r['run_id']}  arm={r['arm']}  "
                  f"human={r['human_label']}  oracle={r['oracle_verdict']}  "
                  f"({r['decided_by']})")
            j = (r["verdict_full"].get("judge") or {})
            if j.get("reasoning"):
                print(f"      judge said: {j['reasoning']}")
            print(f"      heuristic : {r['verdict_full']['heuristic']['reason']}")
            print()
    else:
        print("No disagreements. Read the substantive and judge-dependent "
              "numbers before treating that as good news — a set where the "
              "heuristics settle almost everything is measuring the "
              "heuristics, not the oracle.\n")

    per_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        per_arm[r["arm"]].append(r)
    print("Per-arm human labels (the attack's own result, not the oracle's):")
    for arm in sorted(per_arm):
        rs = per_arm[arm]
        n_s = sum(1 for r in rs if r["human_label"] == "success")
        backends = sorted({r["victim_backend"] or "?" for r in rs})
        agree = sum(1 for r in rs if r["agree"])
        print(f"  {arm:<22} success {n_s}/{len(rs)}   "
              f"oracle agrees {agree}/{len(rs)}   victim={','.join(backends)}")


def inter_annotator(manifest: dict[str, Any]) -> int:
    """Labeler-vs-labeler agreement, on whatever runs both have labelled."""
    # labels_attack3_*.json belongs to attack 3's set and is scored by
    # scripts/validate_oracle_attack3.py. Excluded here so a second attack's
    # label file can never be paired against attack 2's frozen numbers.
    files = sorted(
        p for p in VALIDATION_DIR.glob("labels_*.json")
        if not p.stem.startswith("labels_attack3_")
    )
    docs = {
        p.stem.replace("labels_", ""): json.loads(p.read_text(encoding="utf-8"))
        for p in files
    }
    names = sorted(docs)
    if len(names) < 2:
        print(f"only one label set on disk ({names}). A second independent "
              "labeler is what turns 'the oracle agrees with a blinded LLM "
              "labeler' into 'the oracle agrees with a human' — run "
              "`python scripts/label_runs.py --labeler <name>`.")
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
