#!/usr/bin/env python
"""Offline gate: every registered checker can be validated, and the
validation sets are not quietly broken.

    python scripts/check_label_routes.py

Needs no API key and collects nothing. Added 2026-10-06 after two defects of
the kind §6.1 of the handoff catalogues — "present, therefore checked" — were
found in the validation layer itself:

1. **Unvalidatable checkers.** Three registered checkers (attack 1 on both
   pipelines, attack 5 on pipeline #1) had no labelling route at all, and
   attacks 4 and 5 on pipeline #2 had been in the same state until
   2026-09-22. A checker with no route reads as "waiting on a labeller" when
   it is in fact waiting on code. Check 1 fails if any key of
   ``CHECKERS_BY_PIPELINE`` has no manifest and cards on disk.

2. **A substantive split that splits nothing.** ``injection_present`` is True
   on every matched-control run of attacks 1, 4 and 5, so a "substantive"
   subset defined by it alone was the whole set, controls included. Check 3
   recomputes each profile's substantive subset from the checker's own facts
   and fails if it still contains a control arm. Attack 4 is a KNOWN open
   case (no fact separates its control; decision log 2026-10-06) and is
   listed in ``KNOWN_VACUOUS`` so the gate stays meaningful: it fails if a new
   vacuous split appears, and also if attack 4 gets fixed without this list
   being updated.

Check 2 is a leak check on the blinded cards: no ``variant``/``expects``/
verdict field names, and no arm name containing an underscore (single-word arm
names such as ``full`` or ``hedged`` occur in ordinary prose and are not
checked; the manifests are what tie arms to runs, and labellers never see
them).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import ra_validation  # noqa: E402
import td_validation  # noqa: E402

from trustguard.oracle.oracle import CHECKERS_BY_PIPELINE  # noqa: E402
from trustguard.tracing.schema import RunTrace  # noqa: E402

VALIDATION_DIR = ROOT / "docs" / "validation_set"

#: Pipeline #1's attack 2 and attack 3 routes predate the profile modules and
#: keep their own frozen scripts and file names.
FROZEN_ROUTES = {
    ("attack2_role_spoof", "research_assistant"): (
        VALIDATION_DIR / "manifest.json", None,
    ),
    ("attack3_context_poison", "research_assistant"): (
        VALIDATION_DIR / "manifest_attack3.json", VALIDATION_DIR / "cards_attack3.md",
    ),
}

#: (pipeline, profile key) whose substantive split is known to be vacuous.
KNOWN_VACUOUS = {("task_delegation", "attack4")}

#: Field names only. Bare words like "variant" or "expects" occur in the
#: victims' own report prose ("a blackboard variant") and are not leaks.
LEAK_TOKENS = re.compile(
    r"\b(variant_expects|variant_note|oracle_verdict|decided_by|"
    r"heuristic_only|judge_only)\b|['\"](variant|expects|arm)['\"]\s*:"
)


def profiles():
    for mod in (td_validation, ra_validation):
        for profile in mod.PROFILES.values():
            yield mod.PIPELINE, profile


def check_routes() -> list[str]:
    routed = dict(FROZEN_ROUTES)
    for pipeline, profile in profiles():
        routed[(profile.attack_id, pipeline)] = (profile.manifest, profile.cards)
    problems = []
    for key in sorted(CHECKERS_BY_PIPELINE):
        if key not in routed:
            problems.append(f"{key}: registered checker with NO labelling route")
            continue
        manifest, cards = routed[key]
        if not manifest.exists():
            problems.append(f"{key}: route declared but no manifest at {manifest.name}")
        if cards is not None and not cards.exists():
            problems.append(f"{key}: route declared but no cards at {cards.name}")
    return problems


def check_leaks() -> list[str]:
    problems = []
    for pipeline, profile in profiles():
        if not profile.cards.exists() or not profile.manifest.exists():
            continue
        text = profile.cards.read_text(encoding="utf-8")
        manifest = json.loads(profile.manifest.read_text(encoding="utf-8"))
        arms = {r.get("arm") for r in manifest["runs"]} - {None}
        hits = sorted(set(LEAK_TOKENS.findall(text)))
        hits += sorted(a for a in arms if "_" in a and a in text)
        if hits:
            problems.append(f"{profile.cards.name}: leaks {hits}")
    return problems


def check_substantive() -> list[str]:
    problems = []
    for pipeline, profile in profiles():
        if not profile.manifest.exists():
            continue
        spec = CHECKERS_BY_PIPELINE[(profile.attack_id, pipeline)]
        manifest = json.loads(profile.manifest.read_text(encoding="utf-8"))
        controls_in = []
        for run in manifest["runs"]:
            trace = RunTrace.model_validate(
                json.loads(Path(run["trace"]).read_text(encoding="utf-8"))
            )
            facts = spec.heuristic(trace).facts
            substantive = bool(facts.get(profile.substantive_fact)) and (
                not getattr(profile, "substantive_requires_markers", False)
                or bool(facts.get("declared_markers"))
            )
            if substantive and "control" in str(run.get("arm") or ""):
                controls_in.append(run["run_id"])
        vacuous = bool(controls_in)
        known = (pipeline, profile.key) in KNOWN_VACUOUS
        if vacuous and not known:
            problems.append(
                f"{pipeline}/{profile.key}: {len(controls_in)} control run(s) "
                "counted substantive — the split does not exclude its controls"
            )
        if known and not vacuous:
            problems.append(
                f"{pipeline}/{profile.key}: listed in KNOWN_VACUOUS but its split "
                "now excludes the controls — update the list and the decision log"
            )
        if known and vacuous:
            print(f"  known open: {pipeline}/{profile.key} substantive split "
                  f"includes {len(controls_in)} control run(s)")
    return problems


def main() -> int:
    failed = False
    for name, fn in (
        ("labelling routes", check_routes),
        ("card leaks", check_leaks),
        ("substantive splits", check_substantive),
    ):
        problems = fn()
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for p in problems:
            print(f"  {p}")
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
