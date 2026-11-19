#!/usr/bin/env python
"""Turn a judge-decided sweep's cells into RANGES over >=3 realisations.

    python scripts/judge_stability_attack1.py --repeats 3
    python scripts/judge_stability_attack1.py --attack attack5_tool_laundering

Covers attacks **1 and 5**, which sit in the same position: neither has a human
label set, so neither can report agreement, and what both need from this script
is the per-cell range the 5x2 matrix consumes. Generalised 2026-09-18 rather
than forked, on the same reasoning that generalised `sweep_attack1.py` — a
second copy of 380 lines would drift, and the two attacks' ranges have to be
produced by identical conventions to sit in one matrix. The filename is kept
because the decision log and phase doc reference it; the default output path is
per attack, so neither attack overwrites the other's artefact.

Attacks 2 and 3 keep their own scripts (`judge_stability.py`,
`judge_stability_attack3.py`, `judge_stability_td.py`) because those report
**agreement against a human label set**, which is a different deliverable —
see "How this DIFFERS" below.

It exists because
`sweep_attack1.py` leaves the Phase 4 matrix unfinishable on its own: on
pipeline #1, 6 of 27 scored runs (22.2%) are decided by `Attack1Judge`, and a
judge verdict is itself a model output. A count over judge-decided runs is one
draw from a distribution, not a rate. The project's rule since the 2026-08-21
decision-log entry: **judge-dependent numbers are reported as a range over >=3
realisations**, because Groq accepts a `seed` and ignores it.

## How this DIFFERS from the attack-2 and attack-3 scripts — read this first

Those two report **agreement with a human label set**, because attack 2 has a
validated one and attack 3 has a hand-labelled set to score against. Attacks 1
and 5 have **neither**. All four of their checkers are registered
`validated=False` and no label set exists for any, so there is nothing for this
script to agree *with*, and it does not pretend otherwise: it reports no
agreement number at all.

What it reports instead is the figure the 5x2 matrix actually consumes: the
**per-cell success rate, as a range across judge realisations**. That is the
honest deliverable available without human labels. It does not make any
attack-1 number reportable -- `validated=False` still blocks that, and the
output carries the block as a field. It tells you how wide the cell is, which
is what decides whether a single-count cell was ever going to mean anything.

## What it deliberately does NOT do

- **It collects no runs.** It re-scores traces already on disk. The 2026-09-12
  `unrelated_answerable` episode (3/3 on the first batch, 1/9 on nine further
  trials of byte-identical input) is about *run* variance; this script is about
  *judge* variance on a fixed run. They are separate sources of spread and
  mixing them would confound both.
- **It does not merge cells across sweep files.** `unrelated_answerable`
  appears in both `sweep_attack1.json` and `sweep_attack1_ua.json`, and the
  hard rule is never to aggregate across a version bump or an arm-text edit.
  Cells are keyed by (source file, pipeline, arm). `--merge` overrides this and
  records that it did; do not pass it without checking the arm text.
- **It does not turn a judge error into a verdict.** `Attack1Judge` returns
  `verdict=None` on an unreachable backend or an unparseable reply. A draw
  containing any such run has **no** rate -- that draw is reported as unusable
  rather than quietly counting the run as a failure.

## The instrument check it performs on the way past

Four of this project's defects signalled with silence, so this script does not
trust the sweep's recorded `heuristic_decisive`: it recomputes
`judge_attack1_heuristically` from each trace and **fails loudly** if the
recomputation disagrees with what the sweep recorded. A disagreement means the
heuristic layer moved since the sweep, which would make the sweep's judge share
-- and therefore this script's own choice of which runs to re-judge -- wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from typing import NamedTuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from trustguard.oracle import Oracle  # noqa: E402
from trustguard.oracle.oracle import CHECKERS_BY_PIPELINE  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

VALIDATION_DIR = Path("docs/validation_set")
DEFAULT_ATTACK = "attack1_goal_hijack"


class _Spec(NamedTuple):
    """One attack's artefact paths. The CHECKERS come from the oracle.

    Generalised 2026-09-22. This used to carry its own `heuristic`, `extract`
    and judge bindings, one set per attack -- which meant a *second* copy of
    the oracle's dispatch table living in a script, and it had already drifted:
    a single heuristic per attack was applied to BOTH pipelines' traces, so
    every pipeline #2 row was being checked with pipeline #1's heuristic. The
    rows were classified deterministic anyway, so the disagreement never
    surfaced and the instrument check silently verified nothing for half the
    corpus.

    Everything now resolves through `CHECKERS_BY_PIPELINE[(attack, pipeline)]`
    -- the same dispatch the oracle itself uses, keyed the same way, so it
    cannot drift from it again.
    """

    default_sweeps: list[Path]
    default_out: Path


SPECS: dict[str, _Spec] = {
    "attack1_goal_hijack": _Spec(
        default_sweeps=[
            VALIDATION_DIR / "sweep_attack1.json",
            VALIDATION_DIR / "sweep_attack1_ua.json",
        ],
        default_out=VALIDATION_DIR / "judge_stability_attack1.json",
    ),
    "attack5_tool_laundering": _Spec(
        # Only the multi-trial sweep. The 2026-09-13 single-trial artefact was
        # overwritten and its verdicts are unrecoverable (decision log,
        # 2026-09-18); its traces are indexed but carry no verdicts, so there
        # is nothing there for this script to check itself against.
        default_sweeps=[VALIDATION_DIR / "sweep_attack5.json"],
        default_out=VALIDATION_DIR / "judge_stability_attack5.json",
    ),
    # Added 2026-09-22, when attacks 2 and 3 were swept for the first time and
    # their new cells arrived with 19 judge-decided runs and no ranges.
    #
    # These do NOT replace `judge_stability.py` and `judge_stability_attack3.py`.
    # Those report **agreement against a hand-labelled set** and read the
    # validation manifests; this reports the **per-cell range over judge
    # realisations** and reads sweeps. Two different deliverables over two
    # different corpora, which is why both exist rather than one being deleted.
    "attack2_role_spoof": _Spec(
        default_sweeps=[
            VALIDATION_DIR / "sweep_attack2_ra_2026-09-22.json",
            VALIDATION_DIR / "sweep_attack2_td_2026-09-22.json",
        ],
        default_out=VALIDATION_DIR / "judge_stability_sweep_attack2.json",
    ),
    "attack3_context_poison": _Spec(
        default_sweeps=[
            VALIDATION_DIR / "sweep_attack3_ra_2026-09-22.json",
            VALIDATION_DIR / "sweep_attack3_td_2026-09-22.json",
        ],
        default_out=VALIDATION_DIR / "judge_stability_sweep_attack3.json",
    ),
}


def _checker(attack_id: str, pipeline: str):
    """The oracle's own checker for this cell. Never a parallel table."""
    spec = CHECKERS_BY_PIPELINE.get((attack_id, pipeline))
    if spec is None:
        raise SystemExit(
            f"({attack_id}, {pipeline}) is not registered in the oracle. A "
            "sweep row for an unregistered cell means the sweep and the oracle "
            "disagree about what is staged; fix that before re-judging."
        )
    return spec


def _load_sweep(path: Path, attack_id: str) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("attack_id") != attack_id:
        raise SystemExit(
            f"{path} is a sweep for {data.get('attack_id')!r}, not {attack_id!r}."
        )
    return data["rows"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attack", default=DEFAULT_ATTACK, choices=sorted(SPECS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--sweep",
        action="append",
        default=None,
        help="sweep JSON to re-score; repeatable. Defaults to the attack's own sweeps.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="aggregate same-named cells across sweep files (see module docstring).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Where the ranges are written. Default: per attack (see SPECS).",
    )
    args = parser.parse_args()

    spec = SPECS[args.attack]
    ATTACK_ID = args.attack
    out_path = args.out or str(spec.default_out)

    if args.repeats < 3:
        raise SystemExit(
            f"--repeats {args.repeats} is below the project minimum of 3. "
            "A judge-dependent number is reported as a range over >=3 "
            "realisations (decision log, 2026-08-21); two draws is not a range."
        )

    sweeps = [Path(p) for p in (args.sweep or spec.default_sweeps)]
    for p in sweeps:
        if not p.exists():
            raise SystemExit(f"no such sweep: {p}")

    # ---- collect rows, recompute decisiveness, split judged vs deterministic
    cells: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"deterministic": [], "judged": [], "pipeline_version": set()}
    )
    mismatches: list[str] = []
    rotated_runs: list[str] = []
    n_rows = 0

    for sweep in sweeps:
        src = "merged" if args.merge else sweep.name
        for row in _load_sweep(sweep, ATTACK_ID):
            if row.get("status") != "ok":
                continue
            n_rows += 1
            trace = RunTrace.model_validate_json(
                Path(row["trace"]).read_text(encoding="utf-8")
            )
            extra = trace.config.extra or {}
            if extra.get("rotated") or extra.get("retired_backends"):
                rotated_runs.append(row["run_id"])

            checker = _checker(ATTACK_ID, row["pipeline"])
            h = checker.heuristic(trace)
            recorded = row.get("heuristic_decisive")
            if recorded is not None and bool(recorded) != bool(h.decisive):
                mismatches.append(
                    f"  {row['run_id']} ({row['pipeline']}/{row['arm']}): "
                    f"sweep recorded heuristic_decisive={recorded}, "
                    f"recomputation says {h.decisive}"
                )

            cell = cells[(src, row["pipeline"], row["arm"])]
            cell["pipeline_version"].add(row.get("pipeline_version"))
            entry = {"row": row, "trace": trace, "heuristic": h}
            # Judged iff this row's OWN checker could not settle it. The old
            # rule also required pipeline == research_assistant, which was true
            # of attacks 1 and 5 by accident of their pipeline #2 checkers being
            # mechanical -- and false for attack 3, whose pipeline #2 checker
            # has a real judge. A pipeline that was never judge-backed simply
            # produces no non-decisive rows.
            if h.decisive:
                cell["deterministic"].append(entry)
            else:
                cell["judged"].append(entry)

    if mismatches:
        print(
            "FAIL -- the heuristic layer no longer agrees with the sweep it is "
            "being compared against:\n" + "\n".join(mismatches)
        )
        print(
            "\nThat makes the sweep's judge share -- and this script's choice "
            "of which runs to re-judge -- wrong. Fix the divergence or "
            "re-collect the sweep before reading any range out of it."
        )
        return 1

    for key, cell in cells.items():
        if len(cell["pipeline_version"]) > 1:
            raise SystemExit(
                f"cell {key} spans pipeline versions "
                f"{sorted(cell['pipeline_version'])}. Never aggregate across a "
                "version bump (hard rule, phase 4)."
            )

    n_judged = sum(len(c["judged"]) for c in cells.values())
    print(f"{len(cells)} cells, {n_rows} scored runs, {n_judged} judge-dependent")
    print(f"{n_judged} x {args.repeats} = {n_judged * args.repeats} judge calls\n")
    if rotated_runs:
        print(
            f"NOTE: {len(rotated_runs)} run(s) carry rotated/retired_backends "
            f"in config.extra: {', '.join(rotated_runs[:5])}"
            f"{' ...' if len(rotated_runs) > 5 else ''}"
        )
        print(
            "Do not attribute spread in those cells to the judge without "
            "ruling out the backend change first.\n"
        )
    if not n_judged:
        print(
            "Nothing to re-judge: every scored run was settled "
            "deterministically. These cells are counts, not ranges."
        )

    # ---- re-judge
    # One Oracle instance, whose lazy judge properties are exactly the ones the
    # registry lambdas close over. Calling `checker.judge(oracle, trace, facts)`
    # is the same code path `Oracle.evaluate` takes, so a judge swapped in the
    # registry is picked up here with no change.
    oracle = Oracle() if n_judged else None
    runs: dict[str, dict] = {}

    for (src, pipeline, arm), cell in sorted(cells.items()):
        if not cell["judged"]:
            continue
        print(f"{src}  {pipeline}/{arm}")
        for entry in cell["judged"]:
            row, trace = entry["row"], entry["trace"]
            checker = _checker(ATTACK_ID, pipeline)
            facts = checker.extract(trace)
            verdicts, backends, errors = [], [], []
            for _ in range(args.repeats):
                # A cell with no real judge returns verdict=None from its
                # `*_unavailable` stand-in, which the unusable-draw path below
                # already handles -- so no separate "has a judge?" test is
                # needed, and none can go stale.
                res = checker.judge(oracle, trace, facts)
                verdicts.append(res.get("verdict"))
                backends.append(res.get("backend"))
                if res.get("verdict") is None:
                    errors.append(res.get("error"))
            entry["verdicts"] = verdicts
            counts = Counter(v for v in verdicts if v)
            stable = len(counts) == 1 and not errors
            runs[row["run_id"]] = {
                "source": src,
                "pipeline": pipeline,
                "arm": arm,
                "expects": row.get("expects"),
                "sweep_verdict": row.get("verdict"),
                "verdicts": verdicts,
                "errors": errors,
                "stable": stable,
                "modal": counts.most_common(1)[0][0] if counts else None,
                "backends": backends,
            }
            mark = ""
            if errors:
                mark = "   <-- JUDGE ERROR"
            elif not stable:
                mark = "   <-- FLIPPED"
            print(
                f"  {row['run_id']}  sweep={str(row.get('verdict')):<8} "
                f"judge={verdicts}{mark}"
            )
        print()

    # ---- per-cell success rate, per draw
    cell_report: dict[str, dict] = {}
    for (src, pipeline, arm), cell in sorted(cells.items()):
        det = cell["deterministic"]
        # Pipeline #2 rows land in `deterministic` by routing, not by the
        # attack-1 pipeline #1 heuristic being decisive, so their verdict comes
        # from the sweep (their own checker produced it) rather than from `h`.
        # `h` is now computed with each row's OWN checker, so it is the right
        # verdict for every pipeline and the sweep's recorded verdict is no
        # longer needed as a stand-in for the rows the old rule mis-keyed.
        det_success = sum(1 for e in det if e["heuristic"].verdict == "success")
        total = len(det) + len(cell["judged"])
        per_draw: list[float | None] = []
        for i in range(args.repeats):
            draw = [e["verdicts"][i] for e in cell["judged"]]
            if any(v is None for v in draw):
                per_draw.append(None)  # unusable draw; never counted as failure
                continue
            per_draw.append(
                (det_success + sum(1 for v in draw if v == "success")) / total
            )
        usable = [p for p in per_draw if p is not None]
        cell_report[f"{src}|{pipeline}|{arm}"] = {
            "source": src,
            "pipeline": pipeline,
            "arm": arm,
            "pipeline_version": next(iter(cell["pipeline_version"])),
            "expects": (det + cell["judged"])[0]["row"].get("expects"),
            "n_runs": total,
            "n_judge_dependent": len(cell["judged"]),
            "deterministic_successes": det_success,
            "success_rate_per_draw": per_draw,
            "success_rate_range": [min(usable), max(usable)] if usable else None,
            "unusable_draws": sum(1 for p in per_draw if p is None),
        }

    # ---- print the matrix-facing table
    print(
        "per-cell success rate as a RANGE over "
        f"{args.repeats} judge realisations -- dev lane, NOT reportable"
    )
    print(f"{'cell':<58} {'n':>3} {'judged':>7}  range")
    print("-" * 98)
    for rep in cell_report.values():
        rng = rep["success_rate_range"]
        if rng is None:
            shown = "UNUSABLE (judge error in every draw)"
        elif rep["n_judge_dependent"] == 0:
            shown = f"{rng[0] * 100:.1f}% (deterministic -- a count, not a range)"
        elif abs(rng[1] - rng[0]) < 1e-9:
            shown = f"{rng[0] * 100:.1f}% (judge-decided but stable across draws)"
        else:
            shown = f"{rng[0] * 100:.1f}%-{rng[1] * 100:.1f}%"
        name = f"{rep['source']}  {rep['pipeline']}/{rep['arm']}"
        print(
            f"{name:<58} {rep['n_runs']:>3} "
            f"{rep['n_judge_dependent']:>7}  {shown}"
        )

    n_stable = sum(1 for r in runs.values() if r["stable"])
    if runs:
        print(
            f"\n{n_stable}/{len(runs)} judge-dependent runs returned the same "
            f"verdict on all {args.repeats} attempts."
        )
        if n_stable < len(runs):
            print(
                    f"{ATTACK_ID}'s checker is NOT deterministic on the runs its "
                "judge decides. That is a property of the instrument and "
                "belongs in the deliverable, not in a footnote."
            )

    flags = {
        p: CHECKERS_BY_PIPELINE[(ATTACK_ID, p)].validated
        for p in sorted({pl for _, pl, _ in cells})
        if (ATTACK_ID, p) in CHECKERS_BY_PIPELINE
    }
    print(
        "\nThese ranges are NOT reportable. Registered validated flags for "
        f"{ATTACK_ID}: {flags}. A range only says how wide the cell is; it "
        "says nothing about whether the checker's branches carve the "
        "definition up correctly, and that is what the human label set decides."
    )

    Path(out_path).write_text(
        json.dumps(
            {
                "attack_id": ATTACK_ID,
                "repeats": args.repeats,
                "sweeps": [str(p) for p in sweeps],
                "merged_cells": args.merge,
                "lane": "dev",
                "reportable": False,
                "reportable_blocked_because": (
                    f"the {ATTACK_ID} checkers are registered "
                    "validated=False and the attack has no human label set"
                ),
                "agreement_reported": False,
                "agreement_absent_because": (
                    f"no hand-labelled {ATTACK_ID} validation set exists, so "
                    "there is nothing to measure agreement against"
                ),
                "rotated_runs": rotated_runs,
                "cells": cell_report,
                "runs": runs,
                "judge_dependent": len(runs),
                "judge_stable": n_stable,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
