







#!/usr/bin/env python
"""Offline check of the sweep path, aimed at attacks 2 and 3.

    python scripts/check_sweep_path.py      # no API key needed

Written 2026-10-07 to close the gap the handoff's §2.5 records: *"There is no
gate for attacks 2 and 3's sweep path."* Their probes are gated by
`check_probe_ports.py` and their checkers by `check_oracle_regression.py`,
`check_attack3_checker.py` and `check_td_checkers.py`, but nothing exercised
the code that turns those into matrix rows — `sweep_attack1.py` — and that is
where two of the 2026-09-22 defects lived (handoff §6.1 entry 5 and §6.2's
fifth entry). Each of them would have produced a plausible-looking artefact,
not an error.

One script rather than a `check_attack2.py` / `check_attack3.py` pair: the
thing under test is a single shared sweeper, and attacks 2 and 3 meet it at
the same four places. Attacks 1, 4 and 5 are checked only where the sweeper's
docstring makes a claim about them (that the arm rule is a no-op there).

What it asserts:

* **Plumbing** — every attack is sweepable to its own default file; attacks 2
  and 3 are staged on both pipelines, attack 4 on pipeline #2 only; every
  staged cell resolves a checker from the oracle's registry.
* **Arm set** — `_arms_for` returns every staged variant exactly once, ladder
  first; for attacks 2 and 3 that includes the controls and, on attack 3 /
  pipeline #2, `survivable_claim`; for attacks 1, 4 and 5 it equals the ladder
  (the "cannot move an existing artefact" claim); every cell has an arm
  predicted to land and a control predicted not to.
* **Evidence columns** — every `EVIDENCE_FIELDS` key is a fact the attack's
  own checker emits on at least one pipeline, read off the checker's real
  output rather than a list in this file. A key no checker emits is a column
  that is null on every row forever and reads as "never observed".
* **Rows** — `run_cell` driven end to end with the pipeline, LLM client and
  oracle stubbed: a `mixed` arm records `matches_expectation=None`, never
  False; the evidence sub-dict carries exactly the declared keys; a crashed
  run is recorded once and not retried; scoring errors and `UnsupportedAttack`
  are their own statuses, never a verdict.
* **Table** — a `mixed` arm is never reported as contradicting its prediction
  (`ALL trials contradict` / `split against`; its own note reads "no prediction
  to contradict", so matching the bare word would be a check that cannot pass).
* **CLI guards** — `--lane paper`, an existing `--out`, an arm that exists
  nowhere and a pipeline an attack is not staged on are all refused before any
  run starts; the default plan for attack 3 / pipeline #2 is `_arms_for`, not
  the ladder. `run_cell` is replaced throughout, so a broken guard here fails
  the check instead of spending API quota.
* **Matrix agreement** — `build_matrix.py`'s staged-arm table and the
  sweeper's arm set agree for attacks 2 and 3, and each cell's declared
  `promoted_by` file exists and carries that attack, that pipeline and that arm
  set. Only arm names and ids are read from those files, never verdicts.

Like the sibling check_*.py scripts this measures **intent, not correctness**.
Attacks 2 and 3's checkers are `validated=False` on three of four cells, and
nothing here changes that.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import build_matrix  # noqa: E402
import sweep_attack1 as sweep  # noqa: E402
from trustguard.oracle import UnsupportedAttack  # noqa: E402
from trustguard.oracle.oracle import CHECKERS_BY_PIPELINE, OUT_OF_SCOPE  # noqa: E402
from trustguard.probes import PROBES  # noqa: E402
from trustguard.tracing.schema import IngestionMode, RunConfig, RunTrace  # noqa: E402

A2, A3 = "attack2_role_spoof", "attack3_context_poison"
OLD = (A2, A3)
NEW = ("attack1_goal_hijack", "attack4_priv_escalation", "attack5_tool_laundering")
RA, TD = "research_assistant", "task_delegation"
EXPECTS = {"success", "failure", "mixed"}
FAILURES: list[str] = []
NOTES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f": {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def note(text: str) -> None:
    print(f"  [note] {text}")
    NOTES.append(text)


def synthetic_trace(attack: str, pipeline: str, arm: str) -> RunTrace:
    """A trace carrying the probe's own describe() record and no events."""
    record = PROBES[attack](arm, pipeline=pipeline).describe()
    record["fired"] = False
    return RunTrace(
        pipeline=pipeline,
        config=RunConfig(
            model="synthetic",
            provider="none",
            attack_id=attack,
            ingestion_mode=IngestionMode.WHITE_BOX,
            extra={"attack": record},
        ),
    )


# --------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------


def check_plumbing() -> None:
    print("\nplumbing — what the sweeper can reach")
    check("all five attacks are sweepable", set(sweep.SWEEPABLE) == set(PROBES),
          f"SWEEPABLE={sorted(sweep.SWEEPABLE)}")
    paths = list(sweep.SWEEPABLE.values())
    check("each attack has its own default output file", len(set(paths)) == len(paths))
    for attack in OLD:
        check(f"{attack}: staged on both pipelines",
              sweep._pipelines_for(attack) == [RA, TD],
              f"{sweep._pipelines_for(attack)}")
    check("attack 4: staged on pipeline #2 only, and #1 is out of scope by decision",
          sweep._pipelines_for("attack4_priv_escalation") == [TD]
          and ("attack4_priv_escalation", RA) in OUT_OF_SCOPE)
    for attack in sweep.SWEEPABLE:
        for pipeline in sweep._pipelines_for(attack):
            check(f"{attack} / {pipeline}: checker resolves from the oracle registry",
                  (attack, pipeline) in CHECKERS_BY_PIPELINE)
    check("only attack 2 / pipeline #1 is validated among the staged cells",
          [k for k in CHECKERS_BY_PIPELINE if CHECKERS_BY_PIPELINE[k].validated]
          == [(A2, RA)],
          "a flag moving is a decision-log event; this check failing means one moved")


# --------------------------------------------------------------------------
# Arm set
# --------------------------------------------------------------------------


def check_arms() -> None:
    print("\narm set — every staged variant, ladder first")
    for attack in sweep.SWEEPABLE:
        for pipeline in sweep._pipelines_for(attack):
            target = sweep._targets(attack)[pipeline]
            arms = sweep._arms_for(attack, pipeline)
            ladder = [a for a in target.ladder if a in target.variants]
            tag = f"{attack} / {pipeline}"
            check(f"{tag}: every variant exactly once",
                  sorted(arms) == sorted(target.variants) and len(arms) == len(set(arms)),
                  f"missing {sorted(set(target.variants) - set(arms))}"
                  if set(arms) != set(target.variants) else "")
            check(f"{tag}: ladder order kept at the front", arms[: len(ladder)] == ladder)
            expects = {a: target.variants[a].expects for a in arms}
            check(f"{tag}: every expects is success/failure/mixed",
                  set(expects.values()) <= EXPECTS, f"{sorted(set(expects.values()))}")
            check(f"{tag}: has an arm predicted to land AND a control predicted not to",
                  "success" in expects.values() and "failure" in expects.values())
            if attack in NEW:
                check(f"{tag}: arm rule is a no-op (equals the ladder)", arms == list(target.ladder))
                check(f"{tag}: no mixed arms (matches_expectation stays a bool)",
                      "mixed" not in expects.values())
            else:
                off = [a for a in arms if a not in ladder]
                check(f"{tag}: the ladder alone would omit {off}", bool(off),
                      "if this fails the ladder now carries every arm; re-read _arms_for's docstring")
                controls = [a for a in arms if "control" in a]
                check(f"{tag}: both controls swept", len(controls) >= 1
                      and all(target.variants[c].expects == "failure" for c in controls),
                      f"{controls}")
    arms = sweep._arms_for(A3, TD)
    check("attack 3 / pipeline #2: survivable_claim is swept (handoff §6.2, fifth entry)",
          "survivable_claim" in arms
          and "survivable_claim" not in sweep._targets(A3)[TD].ladder)


# --------------------------------------------------------------------------
# Evidence columns
# --------------------------------------------------------------------------


def check_evidence_fields() -> None:
    print("\nevidence columns — every key is a fact some checker emits")
    check("EVIDENCE_FIELDS covers exactly attacks 2 and 3",
          set(sweep.EVIDENCE_FIELDS) == set(OLD), f"{sorted(sweep.EVIDENCE_FIELDS)}")
    for attack in OLD:
        emitted: dict[str, set[str]] = {}
        for pipeline in sweep._pipelines_for(attack):
            arm = sweep._arms_for(attack, pipeline)[0]
            spec = CHECKERS_BY_PIPELINE[(attack, pipeline)]
            facts = spec.heuristic(synthetic_trace(attack, pipeline, arm)).as_dict()["facts"]
            emitted[pipeline] = set(facts)
        fields = sweep.EVIDENCE_FIELDS[attack]
        anywhere = set().union(*emitted.values())
        dead = [f for f in fields if f not in anywhere]
        check(f"{attack}: no column is null by construction on every pipeline",
              not dead, f"never emitted: {dead}" if dead else f"{len(fields)} keys")
        check(f"{attack}: no duplicate keys", len(fields) == len(set(fields)))
        for pipeline, keys in emitted.items():
            missing = [f for f in fields if f not in keys]
            print(f"         {pipeline}: {len(fields) - len(missing)}/{len(fields)} keys emitted"
                  + (f" (null here by design: {', '.join(missing)})" if missing else ""))

    # The two mechanical observables check_probe_ports.py names for attack 3 on
    # pipeline #2. Reported, not failed: adding a key changes the shape of the
    # rows 
    td_keys = set(CHECKERS_BY_PIPELINE[(A3, TD)].heuristic(
        synthetic_trace(A3, TD, "survivable_claim")).as_dict()["facts"])
    for observable in ("applied_without_verifying", "applied_despite_refutation"):
        if observable in td_keys and observable not in sweep.EVIDENCE_FIELDS[A3]:
            note(f"attack 3 / pipeline #2 emits {observable!r} but sweep rows do not "
                 "carry it; the row's verdict is still recorded, only this column is absent")


# --------------------------------------------------------------------------
# Rows — run_cell with every live dependency stubbed
# --------------------------------------------------------------------------


class _Entry:
    version = "stub"

    def __init__(self, pipeline: str, attack: str, tmp: Path, crash: bool = False):
        self.pipeline, self.attack, self.tmp, self.crash = pipeline, attack, tmp, crash
        self.calls: list[dict] = []

    def run(self, **kw):
        self.calls.append(kw)
        if self.crash:
            raise RuntimeError("synthetic crash")
        trace = synthetic_trace(self.attack, self.pipeline, kw["probe"].variant)
        path = self.tmp / f"{trace.run_id}.json"
        path.write_text(trace.model_dump_json(), encoding="utf-8")
        return "", path


class _Registry:
    def __init__(self, entry):
        self.entry = entry

    def get(self, name):
        return self.entry


def _oracle_returning(verdict: str = "success", raises: Exception | None = None):
    class _Oracle:
        def evaluate(self, trace):
            if raises:
                raise raises
            facts = {k: f"<{k}>" for k in sweep.EVIDENCE_FIELDS.get(trace.config.attack_id, ())}
            return {"verdict": verdict, "decided_by": "heuristic", "judge": None,
                    "heuristic": {"decisive": True, "facts": facts}}
    return _Oracle


@contextlib.contextmanager
def patched(**attrs):
    saved = {k: getattr(sweep, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(sweep, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(sweep, k, v)


def _cell(attack, pipeline, arm, tmp, *, crash=False, oracle=None):
    entry = _Entry(pipeline, attack, tmp, crash=crash)
    with patched(registry=_Registry(entry), RotatingLLMClient=lambda **kw: None,
                 Oracle=oracle or _oracle_returning()):
        row = sweep.run_cell(pipeline, arm, 1, offline=True, trace_dir=str(tmp),
                             temperature=0.0, attack=attack)
    return row, entry


def check_rows(tmp: Path) -> None:
    print("\nrows — run_cell end to end, pipeline / LLM / oracle stubbed")
    for attack in OLD:
        for pipeline in sweep._pipelines_for(attack):
            target = sweep._targets(attack)[pipeline]
            for arm in sweep._arms_for(attack, pipeline):
                expects = target.variants[arm].expects
                row, entry = _cell(attack, pipeline, arm, tmp)
                want = None if expects == "mixed" else ("success" == expects)
                ok = (row.get("status") == "ok" and row.get("expects") == expects
                      and row.get("matches_expectation") is want
                      and list(row.get("evidence") or {}) == list(sweep.EVIDENCE_FIELDS[attack])
                      and entry.calls[0]["lane"] == "dev"
                      and entry.calls[0]["probe"].variant == arm)
                check(f"{attack} / {pipeline} / {arm} ({expects})", ok,
                      "" if ok else f"{ {k: row.get(k) for k in ('status', 'expects', 'matches_expectation')} }")

    arm = "full"
    row, entry = _cell(A3, TD, arm, tmp, crash=True)
    check("a crashed run is recorded as status=error and run exactly once",
          row.get("status") == "error" and len(entry.calls) == 1 and "verdict" not in row)
    row, _ = _cell(A3, TD, arm, tmp, oracle=_oracle_returning(raises=ValueError("bad")))
    check("a scoring exception is status=score_error, never a verdict",
          row.get("status") == "score_error" and "verdict" not in row)
    row, _ = _cell(A2, RA, arm, tmp, oracle=_oracle_returning(raises=UnsupportedAttack("x")))
    check("UnsupportedAttack is status=unsupported, never a verdict",
          row.get("status") == "unsupported" and "verdict" not in row)


# --------------------------------------------------------------------------
# Table
# --------------------------------------------------------------------------


def check_table() -> None:
    print("\ntable — a mixed arm has no prediction to contradict")

    def rows(arm, expects, verdicts):
        return [{"pipeline": TD, "arm": arm, "expects": expects, "status": "ok",
                 "verdict": v, "decided_by": "heuristic",
                 "matches_expectation": None if expects == "mixed" else v == expects}
                for v in verdicts]

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sweep.table(rows("hedged", "mixed", ["success", "failure", "failure"])
                    + rows("full", "success", ["failure"] * 3)
                    + [{"pipeline": TD, "arm": "full", "expects": "success", "status": "error"}])
    out = {line.split()[0]: line for line in buf.getvalue().splitlines()
           if line.startswith("  ") and line.split() and line.split()[0] in ("hedged", "full")}
    hedged = out.get("hedged", "")
    check("mixed arm: reported as no prediction, never as contradicting one",
          "no prediction" in hedged
          and "ALL trials contradict" not in hedged and "split against" not in hedged)
    check("success arm that never lands: flagged ALL trials contradict",
          "ALL trials contradict" in out.get("full", ""))
    check("a failed run is counted beside the cell, not in its denominator",
          "1 run(s) failed" in out.get("full", "") and "0/3" in out.get("full", ""))


# --------------------------------------------------------------------------
# CLI guards — run_cell replaced, so a broken guard cannot reach the API
# --------------------------------------------------------------------------


def _main(argv: list[str]) -> tuple[int | None, list, str]:
    calls: list = []

    def fake_run_cell(pipeline, arm, trial, **kw):
        calls.append((pipeline, arm, trial))
        return {"attack_id": kw["attack"], "pipeline": pipeline, "arm": arm, "trial": trial,
                "expects": sweep._targets(kw["attack"])[pipeline].variants[arm].expects,
                "status": "ok", "verdict": "failure", "decided_by": "heuristic",
                "matches_expectation": None}

    old_argv, err = sys.argv, io.StringIO()
    sys.argv = ["sweep_attack1.py", *argv]
    code: int | None = None
    try:
        with patched(run_cell=fake_run_cell, RotatingLLMClient=None), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = sweep.main()
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.argv = old_argv
    return code, calls, err.getvalue()


def check_cli(tmp: Path) -> None:
    print("\nCLI guards — refused before any run starts")
    for attack in OLD:
        code, calls, err = _main(["--attack", attack, "--lane", "paper",
                                  "--out", str(tmp / "never.json")])
        check(f"{attack}: --lane paper refused", code == 2 and not calls
              and "only --lane dev" in err)

    existing = tmp / "cited_artefact.json"
    existing.write_text('{"cited": true}', encoding="utf-8")
    code, calls, err = _main(["--attack", A3, "--out", str(existing)])
    check("an existing --out is refused and left byte-identical",
          code == 2 and not calls and existing.read_text(encoding="utf-8") == '{"cited": true}')

    code, calls, err = _main(["--attack", A2, "--arm", "no_such_arm", "--out", str(tmp / "n.json")])
    check("an arm that exists on no pipeline is refused", code == 2 and not calls
          and "no runnable cells" in err)

    code, calls, err = _main(["--attack", "attack4_priv_escalation", "--pipeline", RA,
                              "--out", str(tmp / "a4.json")])
    check("attack 4 on pipeline #1 is refused as not staged", code == 2 and not calls
          and "not staged" in err)

    out = tmp / "plan.json"
    code, calls, _ = _main(["--attack", A3, "--pipeline", TD, "--trials", "2", "--out", str(out)])
    planned = [arm for _, arm, t in calls if t == 1]
    check("attack 3 / pipeline #2 default plan is _arms_for, not the ladder",
          planned == sweep._arms_for(A3, TD) and len(calls) == 2 * len(planned),
          f"{planned}")
    written = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    check("the written artefact says lane=dev, reportable=False, and why",
          written.get("lane") == "dev" and written.get("reportable") is False
          and "validated=False" in written.get("reportable_blocked_because", ""))


# --------------------------------------------------------------------------
# Matrix agreement
# --------------------------------------------------------------------------


def check_matrix() -> None:
    print("\nmatrix — build_matrix.py and the sweeper agree on attacks 2 and 3")
    for attack in OLD:
        for pipeline in (RA, TD):
            tag = f"{attack} / {pipeline}"
            swept = set(sweep._arms_for(attack, pipeline))
            check(f"{tag}: matrix's staged arms == sweeper's arm set",
                  set(build_matrix._staged_arms(attack, pipeline)) == swept)
            src = build_matrix.SOURCES[(attack, pipeline)]
            check(f"{tag}: promoted_by names a sweep artefact", bool(src.promoted_by))
            for name in src.promoted_by:
                path = ROOT / build_matrix.VALIDATION_DIR / name
                if not path.exists():
                    check(f"{tag}: {name} exists", False)
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                rows = [r for r in data.get("rows", []) if r.get("pipeline") == pipeline]
                check(f"{tag}: {name} is this attack, this pipeline, every arm",
                      data.get("attack_id") == attack and bool(rows)
                      and {r.get("arm") for r in rows} == swept
                      and data.get("reportable") is False)


def main() -> int:
    print("=" * 78)
    print("sweep path (sweep_attack1.py) for attacks 2 and 3 — offline check")
    print("=" * 78)

    check_plumbing()
    check_arms()
    check_evidence_fields()
    with tempfile.TemporaryDirectory(prefix="trustguard_sweepcheck_") as tmp:
        check_rows(Path(tmp))
        check_table()
        check_cli(Path(tmp))
    check_matrix()

    print("\n" + "-" * 78)
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed" + (f" ({len(NOTES)} note(s) above)" if NOTES else ""))
    print(
        "\nReminder: this measures INTENT, not correctness. It says the sweeper "
        "builds\nattacks 2 and 3's rows the way its docstring says. Three of their "
        "four checkers\nare validated=False and no rate from them may be quoted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
