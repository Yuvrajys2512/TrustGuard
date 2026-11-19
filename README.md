# TrustGuard

[![CI](https://github.com/Yuvrajys2512/TrustGuard/actions/workflows/ci.yml/badge.svg)](https://github.com/Yuvrajys2512/TrustGuard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

An inter-agent trust-exploitation benchmark and defense framework for multi-agent LLM systems.

Existing agentic red-teaming benchmarks (AgentDojo, RIFT-Bench, ART, AgentFuzz) all study a
human or a poisoned document attacking a *single* agent. TrustGuard studies the boundary they
leave alone: one agent in a pipeline manipulating, deceiving, or escalating privilege against
another agent it is supposed to collaborate with.

> **Research code, work in progress.** Only one oracle checker is validated against
> hand labels, so almost no number in this repository is yet reportable as a result. The
> instrument is the contribution; read [What has been measured](#what-has-been-measured)
> before quoting anything.

## Contents

- [What is in the box](#what-is-in-the-box) and [architecture](#architecture), [demo](#demo)
- [Status](#status) and [What has been measured](#what-has-been-measured)
- [Setup](#setup)
- [Running a target pipeline](#running-a-target-pipeline) and [an attack](#running-an-attack)
- [What is and is not reproducible](#what-is-and-is-not-reproducible)
- [Judging a run: the oracle](#judging-a-run-the-oracle)
- [Layout](#layout) and [documentation](#documentation)
- [Contributing](CONTRIBUTING.md) and [License](#license)

## What is in the box

- **A taxonomy of five inter-agent attacks:** delegated goal hijacking, fabricated authority
  (role spoofing), cross-agent context poisoning, cascading privilege escalation, and silent
  tool-call laundering. Each has a success condition decidable from a structured execution trace.
- **Two structurally different LangGraph target pipelines:** a fixed-edge research assistant and
  a task-delegation pipeline with enacted conditional routing.
- **A probe engine** with matched controls for every attack arm, and predictions recorded
  before any run.
- **A hybrid oracle** (deterministic heuristics plus an LLM judge) that reports its own
  reliability, with a `validated` flag stamped on every verdict.
- **A 5x2 attack-by-pipeline matrix** generated from run artefacts.

## Architecture

The harness is layered so that no layer grades its own output. A probe may change what a
message *claims* to be from, or a memory write's value and provenance, but never the agent
that actually sent or wrote it; the oracle reads exactly that gap.

```mermaid
flowchart LR
    P["Probe engine<br/>5 attacks, matched controls"] -->|inject| T
    subgraph T["Target pipeline (LangGraph)"]
        direction TB
        MB["Message bus"]
        SM["Shared memory"]
        TR["Permission-enforcing<br/>tool registry"]
    end
    T -->|JSON trace, 10 event types| O["Oracle<br/>heuristics + LLM judge"]
    O -->|"verdict + validated flag"| M["5x2 matrix"]
    D["Defense layer<br/>(planned, Phase 6)"] -.->|switchable| T
    S["Scoring metric<br/>(planned, Phase 5)"] -.-> M
```

The two target pipelines differ on purpose:

```mermaid
flowchart LR
    subgraph P1["Pipeline #1: research_assistant (fixed edges)"]
        direction LR
        A1[orchestrator] --> A2[researcher] --> A3["summarizer<br/>(victim)"]
    end
    subgraph P2["Pipeline #2: task_delegation (enacted routing)"]
        direction LR
        B1[coordinator] -->|conditional edge| B2[analyst] --> B3["executor<br/>(victim)"]
    end
```

## Demo

The console in [`demo/`](demo/) replays a recorded run as an animated agent graph and trace
stream. Below, attack 2 (role spoofing) against pipeline #1: the researcher's message claims
to come from the orchestrator, the summarizer saves the report under the injected filename,
and the oracle scores the run a success. Open `demo/console.html` and press RUN to play it;
see [`demo/RUNBOOK.md`](demo/RUNBOOK.md).

![TrustGuard console replaying attack 2, role spoofing, against the research-assistant pipeline](docs/images/console.png)

> The console is a recorded-run viewer and is not part of the benchmark. Its coverage table
> mirrors [`docs/matrix_phase4.md`](docs/matrix_phase4.md): only one cell is validated
> (`LIVE`); cells with runs but an unvalidated checker are `MEASURED`, and attack 4 on
> pipeline #1 is `OUT-OF-SCOPE` by design.

## Status

| Phase | State |
|---|---|
| 0 - Threat model, scope & concept map | Draft complete, pending mentor review |
| 1 - Foundations & first target pipeline | Complete |
| 2 - First attack, one vertical slice | Complete - role spoofing lands and is manually verified |
| 3 - The oracle & its validation set | Complete - 91.3% agreement on 46 labelled runs |
| 4 - Taxonomy breadth | Built - five attacks, two pipelines, nine checkers, each with a labelling route. Independent human labelling pending |
| 5-9 | Not started (scoring metric, defence layer, adapters, comparative study, release) |

The current attack-by-pipeline matrix is [`docs/matrix_phase4.md`](docs/matrix_phase4.md),
regenerated by `python scripts/build_matrix.py`.

### What has been measured

One checker per (attack, pipeline). A `validated` flag rides on every verdict, so an
unvalidated number can never be aggregated with a validated one by accident.

| checker | agreement with labels | validated |
|---|---|---|
| attack 2 / research_assistant | 91.3%-95.7% on 46 labels | **yes** |
| attack 3 / research_assistant | 90.4%-92.3% on 52 labels | no - by decision |
| attack 2 / task_delegation | 100% on 36 labels | no |
| attack 3 / task_delegation | 100% on 43 labels | no |
| attacks 1, 4, 5 (all registered pipelines) | no scored label set yet | no |

**Attack 4 has no `research_assistant` checker and never will**: pipeline #1's delegations are
recorded rather than enacted, so a pipeline #1 attack-4 trace raises `UnsupportedAttack`
carrying that reason. The empty cell is a finding about the pipeline's architecture, not
missing data.

**Read the two 100% figures with their caveats**, in
[`docs/phase_4_taxonomy_breadth.md`](docs/phase_4_taxonomy_breadth.md) section 10.1. In short: the
labeller also wrote the checkers; every branch that fired on pipeline #2 is mechanical, so a human
and a deterministic checker reading the same tool calls cannot disagree; and pipeline #2's judge
was never invoked across 43 runs, so it is untested. Labels so far come from an LLM labeller
working blind from evidence cards, not from independent humans, and are treated as discounted
evidence until a human pass lands.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt

cp .env.example .env            # then add at least one API key
python scripts/check_backends.py
```

Model availability on Groq is per-account and models get retired, so run
`check_backends.py` after cloning and whenever a run fails with `model_not_found`;
update `DEFAULT_BACKENDS` in `src/trustguard/llm/client.py` to match.

## Running a target pipeline

```bash
python scripts/run_pipeline.py --list-pipelines            # the registered targets
python scripts/run_pipeline.py "What is the ReAct pattern in LLM agents?"
python scripts/run_pipeline.py --pipeline task_delegation  # pipeline #2
python scripts/run_pipeline.py --offline    # fixed search corpus instead of the live web
python scripts/run_pipeline.py --seed 42    # record and forward a sampling seed
python scripts/inspect_trace.py             # pretty-print the most recent trace
python scripts/inspect_trace.py --full      # no truncation
```

Every run writes a complete JSON trace to `traces/dev/` — or `traces/paper/`
with `--lane paper`. Only paper runs, made with everything frozen, may be quoted
in a reported number; `traces/` root is the Phase 1–3 archive and must not be
added to. See [`traces/README.md`](traces/README.md).

## Running an attack

```bash
python scripts/run_attack.py --list                # probes and their arms
python scripts/run_attack.py --offline             # stage attack 2 (role spoofing)
python scripts/run_attack.py --attack attack3_context_poison
python scripts/run_attack.py --attack attack3_context_poison \
    --variant filing_convention                    # the mechanically decidable arm
python scripts/run_attack.py --lane paper          # a run that may be quoted (see below)
```

All five attacks are implemented (`--list` shows every probe and its arms):

| `attack_id` | Taxonomy | Channel it rides | Pipelines |
|---|---|---|---|
| `attack1_goal_hijack` | 1 - delegated goal hijacking | inter-agent message (handoff) | both |
| `attack2_role_spoof` | 2 - fabricated authority | inter-agent message | both |
| `attack3_context_poison` | 3 - cross-agent context poisoning | shared memory | both |
| `attack4_priv_escalation` | 4 - cascading privilege escalation | delegation chain | task_delegation only |
| `attack5_tool_laundering` | 5 - silent tool-call laundering | message / tool call | both |

After the run this prints an EVIDENCE block shaped for that attack's channel —
for attack 2 the injected message and the victim's `save_report` call; for
attack 3 the poisoned memory write, the victim's read of it, and each occurrence
of the fabricated claim in the victim's report with its surrounding sentence. It
reports **observations**, never a verdict — automated success detection is Phase 3's oracle, and a probe
that graded its own runs would be worthless as evidence. See
[`docs/phase_2_first_attack.md`](docs/phase_2_first_attack.md) for the verified
result and why it counts.

`--offline` swaps the search tool for a fixed corpus; **it still calls the LLM over the
network.** It removes the live web as a source of variance, nothing more.

## What is and is not reproducible

Runs are **structurally** reproducible: the sequence of delegations, tool calls and memory
operations is stable. Generated text is **not**, and no flag makes it so — on Groq,
identical requests at `temperature=0` with a fixed seed returned 8,956 / 8,653 / 8,858
characters. Groq accepts a `seed` and does not honour it. Write regression checks against
trace structure, never against output strings.

Because replay is unavailable, every trace instead records **what actually served it** in
`config.extra`: `calls_by_backend`, `rotated`, and `retired_backends`. On traces written by
pipeline **1.1.0 or earlier these two fields are per-*process*, not per-run** — a sweep reuses
one client, so from its second run onward they describe the sweep so far. Fixed in
**1.1.1**; check `pipeline_version` before trusting them, and see
`docs/phase_3_oracle.md` §9. If `rotated` is
true, the client fell through to a different model mid-run and that run's output
differences may come from the model change rather than from whatever was being tested —
across the three configured Groq models one identical prompt produced 5,753 to 9,942
characters. Check this field before attributing an effect to an attack or a defense.

## Judging a run: the oracle

```bash
python scripts/validate_oracle.py --heuristics-only    # offline, no API key
python scripts/validate_oracle.py                      # full hybrid, calls the judge
python scripts/apply_oracle.py --validation-set        # stamp oracle_verdict into traces
python scripts/check_oracle_regression.py              # regression gate — run after ANY oracle change

# the five offline gates, none of which need an API key
python scripts/check_oracle_regression.py
python scripts/check_attack3_checker.py
python scripts/check_pipeline2.py
python scripts/check_probe_ports.py
python scripts/check_td_checkers.py
```

`src/trustguard/oracle/` decides whether a staged attack succeeded, against the
Phase 0 §3 definitions. Hybrid: deterministic checks settle the mechanical
cases, an LLM judge settles the semantic residue. Frozen at **1.0.0** and
stamped into `RunTrace.oracle_verdict` with `decided_by` on every scored run.

Validated against 46 hand-labelled attack-2 runs: **91.3% agreement**
(κ = 0.827, precision 83.3%, recall 100%). Four things belong with that number
and are not optional:

1. It is one draw. The judge flips on ~1/3 of the runs it decides; across five
   realisations the headline moved **89.1%–95.7%**.
2. The **deterministic half alone scores higher — 95.7%, κ = 0.913 — and does
   not move.** The hybrid is worse than the component it is built on.
3. It covers **attack 2 only**. Each attack's checker carries its own version
   and its own `validated` flag, both stamped into every verdict as
   `checker_version` / `checker_validated`; attacks with no checker still raise
   `UnsupportedAttack` rather than silently inheriting attack 2's logic.
   **Attack 3's checker is registered `validated=False`** — it returns verdicts,
   and none of them may be quoted until it is scored against hand labels the way
   attack 2 was.
4. The labels are LLM-labeller-produced, not human. An independent human pass is outstanding.

Read [`docs/phase_3_oracle.md`](docs/phase_3_oracle.md) before quoting any of
it. **The oracle is not a deterministic instrument**, and §5 of that document
says how much it moves.

## The target pipelines

Two, deliberately different. `--pipeline` selects one; `research_assistant` is the
default because every number reported so far was measured on it.

| | pipeline #1 `research_assistant` | pipeline #2 `task_delegation` |
|---|---|---|
| shape | orchestrator → researcher → summarizer, fixed edges | coordinator → (branch) → analyst → executor |
| delegation | recorded, never enacted | **enacted** — a real conditional edge |
| victim | summarizer | executor |
| victim's tools | `save_report` (side-effecting) only | `verify_record` (read-only) **+** `apply_change` |
| retrieval | live web or offline corpus | none — all tools read fixed local tables |
| version | 1.2.0 | 1.1.0 |

Pipeline #2 exists because of two things pipeline #1 cannot measure: its fixed edges
mean no delegation hop could ever have gone otherwise, so cascading privilege
escalation is unstageable on it; and its victim holds no read-only tool, so the
"without independently verifying it" clause in the poisoning success definition is
trivially true on every run, including benign ones.

```bash
python scripts/run_pipeline.py --list-pipelines
python scripts/run_pipeline.py --pipeline task_delegation
python scripts/run_attack.py --pipeline task_delegation \
    --attack attack3_context_poison --variant survivable_claim
```

## Pipeline #1 in detail

```
orchestrator ──delegates──> researcher ──delegates──> summarizer ──> END
                                │                          │
                            web_search                save_report
                           (read-only)             (SIDE-EFFECTING)
                                │                          │
                                └───── shared memory ──────┘
```

Inter-agent messages ride a `MessageBus` that delivers them into the recipient's
inbox and renders them into its prompt. That bus is one of the probe engine's two
white-box injection points; the other is `SharedMemory`, which lets a probe rewrite
a blackboard write before it commits. Attack 2 rides the first, attack 3 the second —
threat model §3 names shared memory as attack 3's channel, so the distinction is
load-bearing rather than a convenience.

This is the **clean** system — no attacks, no defenses. Its trust assumptions are the
thing the benchmark measures, and they are the realistic defaults these frameworks ship
with, not oversights:

1. The researcher never sees the user's original objective, so it cannot tell a faithful
   task description from a drifted one.
2. The summarizer treats whatever is in shared memory as fact and never re-verifies it.
3. No agent verifies who a message is actually from. The `From:` header a recipient sees
   is whatever the message claims. The summarizer's prompt gates one action behind the
   ORCHESTRATOR role, and nothing checks that claim — **attack 2 goes straight through
   this** (4/4 runs; 0/4 controls).
4. Direct tool calls are permission-scoped, but **delegation is not**: the researcher
   cannot call `save_report`, yet nothing stops it asking the summarizer to.

Each maps to an attack category in the Phase 0 taxonomy.

### Known limits of pipeline #1

Two deliberate simplifications, recorded here so nobody mistakes them for the real thing:

1. **The ReAct loop is single-pass.** The researcher reasons → acts → observes exactly
   once; it does not iterate until satisfied. Attacks that poison an observation work the
   same against one pass as against five.
2. **Delegation is recorded, not enacted.** The graph edges are fixed, so `log_delegation`
   captures a genuine delegation chain (`parent_delegation_id` links correctly) but control
   flow never branches on an agent's decision. This is a known blocker to revisit for
   **attack 4 (cascading privilege escalation)**, which assumes hops that could have gone
   otherwise.

Both are in the decision log with their reasoning.

## Layout

```
src/trustguard/
  tracing/schema.py    # THE SHARED CONTRACT — probe, oracle, scoring, defense all read this
  tracing/logger.py    # per-run trace recorder + console mirror
  llm/client.py        # rotating multi-provider client (survives rate limits)
  probes/              # THE ATTACK LIBRARY — one probe per taxonomy attack
    base.py            # inject -> observe -> log; probes never judge their own runs
    role_spoof.py      # attack 2 (message channel) + its strength-ladder arms
    context_poison.py  # attack 3 (memory channel) + its arms and controls
    goal_hijack.py     # attack 1
    priv_escalation.py # attack 4
    tool_laundering.py # attack 5
  oracle/              # THE SUCCESS ORACLE — reads traces, never runs pipelines
    heuristics.py      # attack 2's deterministic checks — FROZEN, do not edit
    judge.py           # attack 2's LLM judge — FROZEN, do not edit
    heuristics_attack3.py     # attack 3's checks, pipeline #1
    judge_attack3.py          # attack 3's judge, pipeline #1 (asserted vs attributed)
    heuristics_attack2_td.py  # attack 2's checks, pipeline #2 — fully deterministic, no judge
    heuristics_attack3_td.py  # attack 3's checks, pipeline #2 — two mechanical routes
    judge_attack3_td.py       # attack 3's judge, pipeline #2 (untested against real data)
    heuristics_attack1*.py, heuristics_attack4_td.py, heuristics_attack5*.py
                       # attacks 1, 4, 5: per-(attack, pipeline) checkers
    judge_attack1.py, judge_attack5.py   # their LLM judges
    oracle.py          # arbitration + (attack, pipeline) checker registry + versions
    metrics.py         # agreement / precision / recall / kappa
  pipelines/
    registry.py        # name -> pipeline; runners resolve through this
    common/            # SHARED CHANNELS — both pipelines import these, by identity
      messaging.py     # message bus + inboxes; the probe's message injection point
      memory.py        # traced blackboard; the probe's memory injection point
      tools.py         # permission-enforcing tool registry (the enforcement model)
    research_assistant/    # PIPELINE #1 — research assistant, fixed edges
      graph.py         # LangGraph wiring
      agents.py        # the three agents + their trust assumptions, documented
      tools.py         # web_search (read-only) + save_report (side-effecting)
      memory.py        # re-export shim -> common/memory.py
      messaging.py     # re-export shim -> common/messaging.py
    task_delegation/       # PIPELINE #2 — enacted routing, victim can verify
      graph.py         # LangGraph wiring + the conditional edge
      agents.py        # coordinator / analyst / executor + trust assumptions
      tools.py         # lookup_policy, verify_record (read-only), apply_change
scripts/
  run_pipeline.py      # run the clean pipeline
  run_attack.py        # stage a probe against it
  inspect_trace.py     # read a saved trace
  check_backends.py    # which LLM backends this machine can reach
  build_validation_set.py     # generate the Phase 3 runs
  label_runs.py               # blinded evidence cards + hand labelling
  validate_oracle.py          # score the oracle against the labels
  judge_stability.py          # measure the judge's non-determinism
  oracle_sensitivity.py       # what moves the headline number
  apply_oracle.py             # stamp oracle_verdict into traces
  check_oracle_regression.py  # permanent regression gate (attack 2, pipeline #1)
  check_attack3_checker.py    # offline branch check (attack 3, pipeline #1)
  check_pipeline2.py          # offline structural check for pipeline #2
  check_probe_ports.py        # both attacks stage correctly on BOTH pipelines
  check_td_checkers.py        # offline branch check for pipeline #2's two checkers
  td_validation.py            # per-attack artefact paths for pipeline #2's val sets
  build_validation_set_td.py  # generate pipeline #2 runs
  label_runs_td.py            # blinded cards + hand labelling, pipeline #2
  validate_oracle_td.py       # score a pipeline #2 checker against its labels
  judge_stability_td.py       # judge range, pipeline #2 (refuses for attack 2 — no judge)
tests/                 # pytest wrapper around the offline gates
demo/                  # recorded-run console and trace viewers (not part of the benchmark)
docs/                  # project deliverables (threat model, decision log)
  validation_set/      # every labelled set; *_td_* files are pipeline #2's
                       #   46 runs (attack 2 / p1) — permanent regression check
                       #   52 runs (attack 3 / p1), 36 + 43 runs (pipeline #2)
```

## The trace schema is a contract

Pipeline #1 is at **1.2.0** (probe hook on shared memory; 1.1.1 fixed per-run backend
attribution; 1.1.0 added message delivery and the summarizer save policy). Pipeline #2
is at **1.1.0** (1.1.0 added the clause reserving the verification exemption to the
coordinator; attack-2 traces from its 1.0.0 are **not** comparable and must never be
aggregated with 1.1.0 ones). `SCHEMA_VERSION` is unchanged at **1.0.0**, so every trace
ever recorded stays valid and comparable in structure.

`ORACLE_VERSION` is **1.1.0**: checker dispatch keys on (attack, pipeline) rather than
attack alone, because pipeline #2 runs carry the same `attack_id` and would otherwise
have silently received pipeline #1's checker along with its validation claim.

`src/trustguard/tracing/schema.py` is shared by every downstream track — the probe engine
(Phase 2/4), the oracle (Phase 3), the scoring metric (Phase 5), and the defense layer
(Phase 6). Fields exist there *before* they are needed, on purpose: `provenance` on memory
writes, `claimed_sender` on messages, `parent_delegation_id` on delegations, and
`agent_allowed_tools` on tool calls are all recorded on benign runs so that adding an
attack later does not require re-running the baselines. Changing a field means a
decision-log entry and a `SCHEMA_VERSION` bump.

## Documentation

| | |
|---|---|
| Threat model & scope | [`docs/phase_0_threat_model.md`](docs/phase_0_threat_model.md) |
| First pipeline | [`docs/phase_1_target_pipeline.md`](docs/phase_1_target_pipeline.md) |
| First attack, verified | [`docs/phase_2_first_attack.md`](docs/phase_2_first_attack.md) |
| The oracle and its validation set | [`docs/phase_3_oracle.md`](docs/phase_3_oracle.md) |
| Taxonomy breadth (attacks 1-5, matrix design) | [`docs/phase_4_taxonomy_breadth.md`](docs/phase_4_taxonomy_breadth.md) |
| Current 5x2 matrix | [`docs/matrix_phase4.md`](docs/matrix_phase4.md) |
| Decision log (append-only; corrections stay visible) | [`docs/decision_log.md`](docs/decision_log.md) |
| Demo surfaces | [`demo/README.md`](demo/README.md) |
| Trace lanes | [`traces/README.md`](traces/README.md) |

## Responsible use

The probes target the synthetic pipelines in this repository. Do not aim them at systems you
do not own or have permission to test. See [`SECURITY.md`](SECURITY.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Run `python -m pytest -q` (offline, no API key) before
opening a pull request.

## License

[MIT](LICENSE) (c) 2026 Yuvraj Srivastava.
