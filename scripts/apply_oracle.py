#!/usr/bin/env python
"""Write the oracle's verdict into every trace's ``oracle_verdict`` field.

    python scripts/apply_oracle.py --validation-set     # the 46 labelled runs
    python scripts/apply_oracle.py --all                # every current-version trace
    python scripts/apply_oracle.py --all --trace-dir traces/dev
    python scripts/apply_oracle.py --all --heuristics-only
    python scripts/apply_oracle.py --validation-set --dry-run

``RunTrace.oracle_verdict`` has existed since ``SCHEMA_VERSION`` 1.0.0 with
nothing populating it — the field was written into the contract before it was
needed, on purpose (decision log, 2026-08-20). This fills it in. No schema
change, no version bump.

The stamped verdict carries ``oracle_version``, so every run says which
instrument measured it. That is the phase spec's "tag the frozen oracle version
and log it on every run", and it is what makes a Phase 6 before/after
comparison legible: if two runs were scored by different oracle versions, the
comparison is between instruments as much as between conditions.

**Only ``oracle_verdict`` is touched.** Events, config and outputs are rewritten
byte-identically from the parsed model. The oracle never edits the evidence it
reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from trustguard.oracle import ORACLE_VERSION, Oracle, UnsupportedAttack  # noqa: E402
from trustguard.oracle.heuristics import judge_attack2_heuristically  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

MANIFEST = Path("docs/validation_set/manifest.json")


def current_versions() -> dict[str, str]:
    """The pipeline version this tree builds, per pipeline name."""
    from trustguard.pipelines import registry

    return {name: registry.get(name).version for name in registry.names()}


def targets(args: argparse.Namespace) -> tuple[list[Path], list[str]]:
    """Every trace in the lane whose pipeline_version matches the tree's.

    A stale-version run is **excluded and named**, never silently mixed in nor
    silently dropped — the same rule `label_runs_td.py`'s manifest builder
    follows, and for the same reason: a pipeline whose version moved is a
    different victim, so stamping both with one oracle version produces a
    comparison between instruments dressed up as a comparison between runs.

    This used to read ``startswith("1.1.")``, hard-coded. Pipeline #1 is at
    1.2.0 and pipeline #2 at 1.1.0, so ``--all`` silently matched nothing on
    pipeline #1 and reported "stamped 0 trace(s)" as though there had been
    nothing to do. A filter that answers "no traces" when it means "I do not
    know this version" is worse than no filter; the version is now resolved
    per pipeline from the registry, so adding a pipeline or bumping one cannot
    leave this stale again.
    """
    if args.validation_set:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return [Path(r["trace"]) for r in manifest["runs"]], []

    expected = current_versions()
    paths: list[Path] = []
    excluded: list[str] = []
    for p in sorted(Path(args.trace_dir).glob("*.json")):
        trace = json.loads(p.read_text(encoding="utf-8"))
        name = str(trace.get("pipeline", ""))
        version = str(trace.get("pipeline_version", ""))
        want = expected.get(name)
        if want is None:
            excluded.append(
                f"{trace.get('run_id', p.name)}: unknown pipeline {name!r} "
                "(no entry in the registry)"
            )
            continue
        if version != want:
            excluded.append(
                f"{trace.get('run_id', p.name)}: {name} pipeline_version "
                f"{version!r} != {want!r} in this tree; not comparable"
            )
            continue
        paths.append(p)
    return paths, excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-set", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--heuristics-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--trace-dir", default="traces")
    args = parser.parse_args()

    if not (args.validation_set or args.all):
        parser.error("pass --validation-set or --all")

    paths, excluded = targets(args)
    if excluded:
        print(f"excluded {len(excluded)} trace(s) on pipeline_version:")
        for line in excluded:
            print(f"  - {line}")
        print()
    if not paths:
        print(
            f"no traces to stamp in {args.trace_dir!r}. "
            "If that is a surprise, read the exclusions above: a run is left "
            "out only when its pipeline_version does not match the tree's."
        )
        return 0
    oracle = Oracle()
    stamped = skipped = 0

    for path in paths:
        trace = RunTrace.model_validate_json(path.read_text(encoding="utf-8"))
        try:
            if args.heuristics_only:
                h = judge_attack2_heuristically(trace)
                verdict = {
                    "oracle_version": ORACLE_VERSION,
                    "attack_id": trace.config.attack_id,
                    "verdict": h.verdict if h.decisive else "undecidable",
                    "decided_by": "heuristic" if h.decisive else "none",
                    "heuristic_only_verdict": h.verdict,
                    "heuristic_decisive": h.decisive,
                    "judge_only_verdict": None,
                    "heuristic": h.as_dict(),
                    "judge": None,
                }
                trace.oracle_verdict = verdict
            else:
                verdict = oracle.evaluate_and_stamp(trace)
        except UnsupportedAttack as exc:
            print(f"  SKIP {trace.run_id}: {exc}")
            skipped += 1
            continue

        print(f"  {trace.run_id}  {verdict['verdict']:<11} "
              f"({verdict['decided_by']})")
        if not args.dry_run:
            path.write_text(
                trace.model_dump_json(indent=2, exclude_none=False),
                encoding="utf-8",
            )
        stamped += 1

    verb = "would stamp" if args.dry_run else "stamped"
    print(f"\n{verb} {stamped} trace(s) with oracle {ORACLE_VERSION}"
          + (f", {skipped} skipped" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
