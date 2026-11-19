#!/usr/bin/env python
"""Score a pipeline #1 checker (attacks 1 and 5) against hand labels.

    python scripts/validate_oracle_ra.py --attack attack1 --heuristics-only   # no API key
    python scripts/validate_oracle_ra.py --attack attack5                     # hybrid; judge calls
    python scripts/validate_oracle_ra.py --attack attack5 --inter-annotator

The pipeline #1 counterpart of `validate_oracle_td.py`, derived from it
2026-10-06 with the profile module and label-file prefix swapped
(`ra_validation`, `labels_ra_*`); the scoring and reporting logic is the same,
so the two pipelines' agreement numbers are computed the same way. Pipeline
#1's attack 2 and attack 3 keep their own frozen scripts.

Both checkers here HAVE a judge (pipeline #1 is the judge-heavy side of the
observability asymmetry), so the hybrid number is a draw from a distribution:
report it as a range over at least three realisations, never one run. The
`--heuristics-only` pass is the reproducible half; on these checkers it
abstains on most substantive runs by design.

The rest of this docstring is inherited from the pipeline #2 script and
applies unchanged.

## What is reported, and why more than one number

* **all runs** — every labelled run, including the trivially-decidable ones
  (no attack staged, no role claim, nothing injected). A headline agreement
  number computed over these is inflated: the trivial cases are free, and a
  set with many of them flatters any checker.
* **substantive** — the runs where the attack's precondition actually held, so
  the victim genuinely had the chance to fall for it. This is the number that
  means something, and it is the one to quote when there is anything to quote.
* **ablations** — what the deterministic half alone would have said, and what
  the judge alone would have said. Without these there is no evidence that a
  hybrid is worth its complexity over either half. Phase 3 found attack 2's
  hybrid scoring *below* its own deterministic half, which is exactly the kind
  of thing an ablation exists to catch.

Attack 2 on this pipeline has **no judge**: every branch of its checker is
decisive. Its judge-alone column is therefore reported as undefined rather than
as a number, and its hybrid equals its heuristics-only pass by construction.
That also means it needs no range over judge realisations — there is no
sampling to average over.

## What this script cannot tell you

Whether the labels are right. The first label set is the assistant's, which is
discounted evidence for the reason Phase 3 recorded: it is not an independent
human. `--inter-annotator` compares two labellers and is the check that
matters; until a second human passes, no `validated` flag may move.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from ra_validation import PIPELINE, PROFILES, Profile, resolve  # noqa: E402

from trustguard.oracle import Oracle, cohens_kappa, format_report, score  # noqa: E402
from trustguard.oracle.oracle import CHECKERS_BY_PIPELINE  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402


def load(profile: Profile, labeler: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not profile.manifest.exists():
        raise SystemExit(f"no manifest at {profile.manifest}")
    manifest = json.loads(profile.manifest.read_text(encoding="utf-8"))
    path = profile.labels(labeler)
    if not path.exists():
        raise SystemExit(
            f"no labels at {path} — run label_runs_ra.py --attack "
            f"{profile.key} --labeler {labeler} first"
        )
    return manifest, json.loads(path.read_text(encoding="utf-8"))


def is_success(label: str) -> bool:
    return label == "success"


def evaluate(
    profile: Profile, labeler: str, heuristics_only: bool, judge_always: bool
) -> list[dict[str, Any]]:
    manifest, labels = load(profile, labeler)
    by_id = {r["run_id"]: r for r in manifest["runs"]}
    oracle = Oracle(judge_always=judge_always)
    spec = CHECKERS_BY_PIPELINE[(profile.attack_id, PIPELINE)]

    rows: list[dict[str, Any]] = []
    for run_id, label in labels["labels"].items():
        entry = by_id.get(run_id)
        if entry is None:
            continue
        trace = RunTrace.model_validate(
            json.loads(Path(entry["trace"]).read_text(encoding="utf-8"))
        )

        if heuristics_only:
            heuristic = spec.heuristic(trace)
            verdict = {
                "verdict": heuristic.verdict,
                "decided_by": "heuristic",
                "heuristic_only_verdict": heuristic.verdict,
                "heuristic_decisive": heuristic.decisive,
                "judge_only_verdict": None,
                "heuristic": heuristic.as_dict(),
                "judge": None,
            }
        else:
            verdict = oracle.evaluate(trace)

        facts = verdict["heuristic"]["facts"]
        rows.append(
            {
                "run_id": run_id,
                "arm": entry.get("arm"),
                "pipeline_version": entry.get("pipeline_version"),
                "label": label,
                "oracle_verdict": verdict["verdict"],
                "decided_by": verdict["decided_by"],
                "heuristic_only": verdict["heuristic_only_verdict"],
                "heuristic_decisive": verdict["heuristic_decisive"],
                "judge_only": verdict["judge_only_verdict"],
                "substantive": bool(facts.get(profile.substantive_fact))
                and (
                    not profile.substantive_requires_markers
                    or bool(facts.get("declared_markers"))
                ),
                "judge_error": (verdict.get("judge") or {}).get("error"),
                "facts": facts,
            }
        )
    return rows


def summarise(profile: Profile, rows: list[dict[str, Any]]) -> dict[str, Any]:
    def sc(subset: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        pairs = [
            (is_success(r["label"]), is_success(str(r[key])))
            for r in subset
            if r[key] is not None
        ]
        if not pairs:
            return None
        return score([a for a, _ in pairs], [b for _, b in pairs])

    substantive = [r for r in rows if r["substantive"]]
    judge_share = (
        sum(1 for r in substantive if r["decided_by"] == "judge") / len(substantive)
        if substantive
        else 0.0
    )
    return {
        "all_hybrid": sc(rows, "oracle_verdict"),
        "all_heuristics_only": sc(rows, "heuristic_only"),
        "substantive_hybrid": sc(substantive, "oracle_verdict"),
        "substantive_heuristics_only": sc(substantive, "heuristic_only"),
        "substantive_judge_only": sc(substantive, "judge_only"),
        "counts": {
            "n": len(rows),
            "substantive": len(substantive),
            "trivial": len(rows) - len(substantive),
            "judge_decided_substantive": sum(
                1 for r in substantive if r["decided_by"] == "judge"
            ),
            "judge_share_substantive": judge_share,
            "judge_errors": sum(1 for r in rows if r["judge_error"]),
            "undecidable": sum(1 for r in rows if r["oracle_verdict"] == "undecidable"),
        },
        "by_arm": arm_breakdown(rows),
    }


def arm_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for r in rows:
        arm = r["arm"] or "(none)"
        b = out.setdefault(arm, {"n": 0, "labelled_success": 0, "oracle_success": 0,
                                 "agree": 0})
        b["n"] += 1
        b["labelled_success"] += int(is_success(r["label"]))
        b["oracle_success"] += int(is_success(str(r["oracle_verdict"])))
        b["agree"] += int(is_success(r["label"]) == is_success(str(r["oracle_verdict"])))
    return out


def report(profile: Profile, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    spec = CHECKERS_BY_PIPELINE[(profile.attack_id, PIPELINE)]
    print("=" * 78)
    print(f"{profile.attack_id} on {PIPELINE}")
    print(f"checker version {spec.version}   validated={spec.validated}")
    print("=" * 78)

    for title, key in [
        ("ALL RUNS      hybrid", "all_hybrid"),
        ("ALL RUNS      heuristics alone", "all_heuristics_only"),
        (f"SUBSTANTIVE   hybrid ({profile.substantive_label})", "substantive_hybrid"),
        ("SUBSTANTIVE   heuristics alone", "substantive_heuristics_only"),
        ("ABLATION      judge alone, substantive", "substantive_judge_only"),
    ]:
        s = summary[key]
        if s is None:
            if key == "substantive_judge_only" and not profile.has_judge:
                print(f"\n{title}")
                print("  UNDEFINED — this checker has no judge. Every branch is")
                print("  decisive, so the hybrid equals the heuristics-only pass")
                print("  by construction and there is no judge opinion to ablate.")
            else:
                print(f"\n{title}\n  (no data)")
            continue
        print()
        print(format_report(title, s))

    c = summary["counts"]
    print(f"\ncounts: n={c['n']}  substantive={c['substantive']}  "
          f"trivial={c['trivial']}")
    print(f"judge-decided (substantive): {c['judge_decided_substantive']} "
          f"({c['judge_share_substantive']:.1%})")
    if c["judge_errors"]:
        print(f"judge errors: {c['judge_errors']}")
    if c["undecidable"]:
        print(f"UNDECIDABLE runs: {c['undecidable']}")

    print("\nper arm (n / labelled-success / oracle-success / agreements):")
    for arm, b in sorted(summary["by_arm"].items()):
        print(f"  {arm:<24} {b['n']:>3}  {b['labelled_success']:>3}  "
              f"{b['oracle_success']:>3}  {b['agree']:>3}"
              + ("" if b["agree"] == b["n"] else "   <-- disagreements"))

    disagreements = [
        r for r in rows
        if is_success(r["label"]) != is_success(str(r["oracle_verdict"]))
    ]
    print(f"\n{len(disagreements)} disagreement(s):")
    for r in disagreements:
        print(f"  {r['run_id']}  arm={r['arm']}  label={r['label']}  "
              f"oracle={r['oracle_verdict']}  by={r['decided_by']}")
        print(f"    heuristic said: {r['facts'] and ''}"
              f"{_reason(r)}")
    if not disagreements:
        print("  none. Read the substantive rows and the arm table before")
        print("  concluding anything: a set whose arms are easy agrees easily.")


def _reason(row: dict[str, Any]) -> str:
    f = row["facts"]
    bits = []
    for k in (
        "role_claim_present", "route", "applied", "verified",
        "applied_without_verifying", "applied_fabricated_record",
        "applied_despite_refutation", "verified_and_refused", "propagated",
    ):
        if k in f:
            bits.append(f"{k}={f[k]}")
    return ", ".join(bits)


def inter_annotator(profile: Profile) -> int:
    paths = sorted(
        profile.manifest.parent.glob(f"labels_ra_{profile.key}_*.json")
    )
    if len(paths) < 2:
        print(f"only {len(paths)} label set(s) for {profile.attack_id} on "
              f"{PIPELINE}; need 2 to compare.")
        print("The assistant's labels are discounted evidence on their own — an")
        print("independent human pass is required before `validated` can move.")
        return 1

    sets = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            a, b = sets[i], sets[j]
            shared = sorted(set(a["labels"]) & set(b["labels"]))
            if not shared:
                print(f"{a['labeler']} vs {b['labeler']}: no runs in common")
                continue
            ref = [is_success(a["labels"][r]) for r in shared]
            pred = [is_success(b["labels"][r]) for r in shared]
            agree = sum(x == y for x, y in zip(ref, pred))
            print(f"{a['labeler']} vs {b['labeler']}: n={len(shared)}  "
                  f"agreement={agree / len(shared):.1%}  "
                  f"kappa={cohens_kappa(ref, pred):.3f}")
            for r in shared:
                if a["labels"][r] != b["labels"][r]:
                    print(f"    {r}: {a['labeler']}={a['labels'][r]} "
                          f"{b['labeler']}={b['labels'][r]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Derived from PROFILES, never a second hardcoded list: adding attacks 4
    # and 5 to td_validation.py on 2026-09-22 left this literal behind and
    # the new profiles were unreachable from the CLI while looking present.
    parser.add_argument("--attack", required=True, choices=sorted(PROFILES))
    parser.add_argument("--labeler", default="assistant")
    parser.add_argument(
        "--heuristics-only",
        action="store_true",
        help="Score the deterministic half alone. Needs no API key and is the "
        "reproducible half — everything with a judge in it is a draw from a "
        "distribution.",
    )
    parser.add_argument(
        "--judge-always",
        action="store_true",
        help="Force a judge call even on runs the heuristics settle, so the "
        "judge-alone ablation covers every run rather than only the residue.",
    )
    parser.add_argument("--inter-annotator", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    profile = resolve(args.attack)

    if args.inter_annotator:
        return inter_annotator(profile)

    if args.judge_always and not profile.has_judge:
        print(f"note: {profile.attack_id} on {PIPELINE} has no judge; "
              "--judge-always has nothing to call.", file=sys.stderr)

    rows = evaluate(profile, args.labeler, args.heuristics_only, args.judge_always)
    if not rows:
        raise SystemExit("no labelled runs found")
    summary = summarise(profile, rows)
    report(profile, rows, summary)

    out = Path(args.out) if args.out else profile.results
    if args.heuristics_only:
        out = out.with_name(out.stem + "_heuristics_only.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "attack_id": profile.attack_id,
                "pipeline": PIPELINE,
                "labeler": args.labeler,
                "heuristics_only": args.heuristics_only,
                "judge_always": args.judge_always,
                "summary": summary,
                "rows": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
