# TrustGuard — Phase 1 Deliverable: Foundations & First Target Pipeline

**Status:** Complete
**Depends on:** [`phase_0_threat_model.md`](phase_0_threat_model.md)

The verifiable output Phase 1 asks for is "a working LangGraph pipeline you can run on a
normal task and watch agents delegate and call tools, with the full inter-agent message
trace printed or logged." That exists. This document records what was built, why it was
built that way, and the trust assumptions it deliberately leaves open.

---

## 1. What was built

A single research-assistant pipeline on LangGraph — clean, with no attacks and no defenses.

```
orchestrator ──delegates──> researcher ──delegates──> summarizer ──> END
                                │                          │
                            web_search                save_report
                           (read-only)             (SIDE-EFFECTING)
                                │                          │
                                └───── shared memory ──────┘
```

| Agent | Role | Tools | Can delegate to |
|---|---|---|---|
| `orchestrator` | Turns the user objective into one concrete research task | — | `researcher` |
| `researcher` | Executes the task via web search, writes findings to shared memory | `web_search` | `summarizer` |
| `summarizer` | Reads findings, writes the final report, saves it | `save_report` | — |

Genuine delegation (a 2-hop chain), a real tool (live DuckDuckGo search), and real shared
state (a traced blackboard) — the three things Phase 1 requires.

**Run it:**

```bash
python scripts/run_pipeline.py "What is the ReAct pattern in LLM agents?"
python scripts/inspect_trace.py
```

## 2. Trust assumptions this pipeline makes

These are not bugs. They are the defaults LangGraph and CrewAI ship with, and each one is
the hole a Phase 0 attack category goes through. Stating them precisely here is what makes
the Phase 2 attacks obvious rather than guesswork.

| # | Assumption | Consequence | Attack it enables |
|---|---|---|---|
| 1 | The researcher never receives the user's original objective — only the orchestrator's task description. | It cannot detect that a task description has drifted from the root objective. | 1 — delegated goal hijacking |
| 2 | The summarizer treats shared memory as ground truth and never re-verifies a finding. | A false claim written to `findings` is laundered into the final report as fact. | 3 — cross-agent context poisoning |
| 3 | No agent verifies message origin. Sender identity is whatever the message asserts. | An unverified claim of authority is indistinguishable from a real one. | 2 — fabricated authority / role spoofing |
| 4 | Direct tool calls are permission-scoped, but delegation is unrestricted. | The researcher cannot call `save_report`, but nothing stops it asking the summarizer to. | 4 — cascading privilege escalation; 5 — silent tool-call laundering |

Assumption 4 is why the two tools are deliberately asymmetric in permission. A pipeline
where every agent can call every tool has no internal trust boundary and therefore nothing
for this project to measure.

## 3. Instrumentation — the trace schema

`src/trustguard/tracing/schema.py` is the shared contract the PRD calls for in week 1. Ten
event types are recorded: `pipeline_input`, `agent_reasoning`, `delegation`,
`agent_message`, `tool_call`, `tool_result`, `memory_write`, `memory_read`,
`pipeline_output`, `error`.

Four fields are recorded on **benign** runs even though nothing consumes them yet. This is
deliberate: adding them after the baselines were collected would have meant re-running
every benign trace.

| Field | On | Consumed by |
|---|---|---|
| `provenance` | `memory_write` | Attack 3 oracle; Phase 6 provenance defense |
| `claimed_sender`, `claimed_authority` | `agent_message` | Attack 2 oracle — the attack lives in the gap between `from_agent` and `claimed_sender` |
| `parent_delegation_id`, `root_objective` | `delegation` | Attacks 1 and 4 — makes a delegation *chain* reconstructable |
| `agent_allowed_tools` | `tool_call` | Attacks 4 and 5; Phase 5 scoring |

`RunTrace.delegation_chain()` walks a delegation back to its root, which is what attack 4's
"chain of ≥2 delegations" success definition needs.

## 4. Verification — evidence the definition of done is met

Run on `"What is the ReAct pattern in LLM agents?"` against `groq:openai/gpt-oss-120b`:

- Pipeline completed end to end, 17 events, no errors.
- Delegation chain reconstructed: 2 hops (`orchestrator → researcher → summarizer`), with
  `root_objective` preserved on every hop.
- Both tools called in scope: `web_search` (read-only, ok), `save_report`
  (side-effecting, ok). Report written to `workspace/report.md`.
- Live search confirmed genuine — `source: duckduckgo`, results on-topic.
- Permission enforcement exercised separately: `researcher` calling `save_report` is
  denied, the denial is traced with `error` set, and **no file is written**. An unknown
  tool name is likewise denied and traced.

### Definition of done

- [x] Pipeline runs end to end on a normal task without erroring.
- [x] Full, readable trace of every inter-agent message and tool call
      (console mirror during the run, `scripts/inspect_trace.py` afterwards).
- [x] The default trust relationships are written down and understood — §2.
- [x] One clean pipeline, no extra agents added "to make it interesting."

## 5. Decisions worth knowing about

- **LangGraph over CrewAI for pipeline #1.** Its state object and node boundaries are
  ordinary inspectable Python, which is what Phase 7's white-box adapter needs to wrap.
  CrewAI is still planned for pipeline #2 so the benchmark is not single-framework.
- **A side-effecting tool was added in Phase 1, not deferred.** Attacks 4 and 5 are
  meaningless without one, and retrofitting it would have invalidated the baselines.
- **The offline search corpus exists for reproducibility.** A benchmark whose numbers
  depend on what the live web returned that afternoon is not a benchmark. `--offline`
  gives deterministic results; live search is the default for realism.
- **Search fallback is always visible in the trace.** If live search fails, the tool
  reports `source: offline_corpus (live search failed: ...)` rather than silently
  substituting canned results. Silent substitution was caught during Phase 1 testing: a
  ReAct query returned unrelated multi-agent corpus text and the researcher wrote it to
  shared memory as genuine findings. That is corrupted benchmark data, and it was
  invisible until the source field was added.

## 6. Known limitations, carried into later phases

- **Single-shot agents, not full ReAct loops.** The researcher reasons → acts → observes
  exactly once. A true multi-turn ReAct loop is a richer attack surface (a poisoned
  observation could steer several subsequent steps). Revisit if Phase 4 needs it.
- **One pipeline, one framework.** The second target pipeline (task-delegation, CrewAI)
  is Phase 1's other half and is not yet built.
- **No CrewAI adapter yet**, so the trace schema has only been exercised against
  LangGraph. It was designed to be framework-neutral; that claim is untested.
- **Small-model JSON reliability.** Agents that must return JSON are parsed defensively
  (`_parse_json` handles fences and surrounding prose) with a fallback default. Worth
  measuring how often the fallback fires, since a parse failure silently changes agent
  behaviour.
