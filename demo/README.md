# TrustGuard — demo surfaces

Everything in this folder exists to **show** TrustGuard to an audience. None of it is
part of the benchmark. It is kept out of `src/`, `scripts/`, `docs/` and `traces/` on
purpose, so the project and the presentation can never be confused for one another.

These tools compute nothing of their own. They run the real probes through the real
pipeline, score with the real frozen oracle, and render what comes back. Where a number
would have to be invented, they say so instead.

For a presenter's checklist see [`RUNBOOK.md`](RUNBOOK.md).

## What's here

| Path | What it is |
|---|---|
| `tools/demo.py` | Terminal demo: runs the attack arm and its control arm back to back, paced for reading aloud, then prints the oracle's verdict for both. |
| `tools/trace_viewer.py` | Renders a pair of traces as a standalone HTML delegation timeline. Prints cleanly as a report figure. |
| `tools/build_console.py` | Builds `console.html` from the recorded pairs. |
| `tools/console_template.html` | The console's markup, styles and playback engine. Edit this, then rebuild. |
| `*_replay.json` | Recorded attack/control pairs — real runs, with the oracle's verdicts stamped in. |
| `console.html` | The animated console. Self-contained; opens by double-click. |
| `*_viewer.html` | The static timelines. Self-contained. |

## The terminal demo

```bash
python demo/tools/demo.py                              # attack 2, live, ~55s
python demo/tools/demo.py --attack attack3_context_poison
python demo/tools/demo.py --replay                     # recorded pair, no network
python demo/tools/demo.py --record                     # refresh the recorded pair
python demo/tools/demo.py --speed 0                    # no pauses, for rehearsal
```

Live runs write to `traces/dev` like any other experimentation. Nothing here ever writes
to `traces/` root or `traces/paper`.

`--replay` plays a previously recorded real pair for when the venue network is down. It
prints REPLAY on every screen it draws — **a recorded run must never be presented as a
live one.**

## The console

```bash
python demo/tools/build_console.py        # -> demo/console.html
```

Open `console.html` and press RUN. It plays a recorded run back as an animated agent
graph and a streaming trace log: channels carry their own colour (cyan = message bus,
violet = blackboard, lime = tools), the injection turns the channel amber, the impact
turns it coral, and the compromised agent stays marked for the rest of the run.

Switch between **Attack arm** and **Control arm** and run it again. That contrast is the
demonstration; a single attacked run on its own is an anecdote.

## The three rules the console keeps

A demo this finished invites the question *"is any of this real?"*, and the answer has to
survive being poked at. So:

1. **Every event is replayed from a recorded trace.** Nothing is scripted or invented, and
   the run id being played is on screen throughout.
2. **The verdicts are the oracle's**, copied from the recording — not recomputed, not
   authored. An unvalidated checker stays visibly marked unvalidated, and attack 3's
   verdict carries that warning.
3. **Capability the project does not have yet is drawn as not built.** The coverage matrix
   shows two attacks of five and one pipeline of two, with attack 4 marked blocked and its
   real reason in the legend. A filled-in matrix would be the one thing on screen that
   lied.

The matrix lives in `MATRIX` in `tools/build_console.py` and is kept in sync by hand with
`docs/phase_4_taxonomy_breadth.md` §5. When attack 3's checker is validated, flip its `p1`
from `built` to `live` there.

## Re-recording

After collecting better runs:

```bash
python demo/tools/demo.py --record                                  # attack 2
python demo/tools/demo.py --attack attack3_context_poison --record  # attack 3
python demo/tools/build_console.py                                  # rebuild the console
```
