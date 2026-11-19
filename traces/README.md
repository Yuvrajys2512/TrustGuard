# traces/

Three collections. Which one a run lands in is decided **before** it runs, by
`--lane`, and recorded inside the trace as `config.extra["lane"]` so a file that
gets moved still says what it was collected as.

| Folder | What is in it | May it be quoted in a result? |
|---|---|---|
| `traces/*.json` (root) | The Phase 1–3 archive: baselines, the attack-2 vertical slice, and the 46 runs of the validation set. Referenced by path from `docs/validation_set/manifest.json`. | Yes, as Phase 3 reported them. **Do not add to it** — `label_runs.py` globs this folder, and a new file here silently enlarges the validation set. |
| `traces/dev/` | Everything written while building, debugging, or trying something. The default lane. | **No.** Never aggregated, never counted, never in a table. |
| `traces/paper/` | Runs made with everything frozen — oracle version, model IDs, prompts, probe variants — for a number that will appear in the paper. | Yes, and only these. |

```bash
python scripts/run_attack.py --attack attack3_context_poison            # -> traces/dev/
python scripts/run_attack.py --attack attack3_context_poison --lane paper
```

The split exists because of Phase 4's tip, which is worth restating as the rule
it is: *only runs made with everything frozen count toward the reported
numbers.* A single trace produced while tuning a prompt, left in the same folder
as the real sweep and later swept up by a glob, is enough to make a published
success rate wrong in a way nobody can reconstruct afterwards. Sorting them at
write time costs nothing; sorting them afterwards is guesswork.

A run whose lane you are unsure of is a dev run.
