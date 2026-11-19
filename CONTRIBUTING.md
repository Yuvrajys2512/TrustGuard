# Contributing to TrustGuard

Thanks for your interest. TrustGuard is a research benchmark, so a few rules
exist to keep its numbers trustworthy.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
python -m pytest -q                # offline gates, no API key needed
```

An API key is only needed to run pipelines and attacks live (see `.env.example`).

## Ground rules

- **Run the gates.** `python -m pytest -q` must pass before you open a pull
  request. If you touch the oracle, `scripts/check_oracle_regression.py` is the
  gate that matters.
- **Never aggregate a `validated=False` checker.** Every verdict carries a
  `checker_validated` flag. Numbers from unvalidated checkers must not be
  quoted as results.
- **Write checks against trace structure, never against generated text.** LLM
  output is not reproducible; delegation order, tool calls and memory writes are.
- **Record decisions.** A change to the oracle, a probe or the trace schema
  needs an entry in [`docs/decision_log.md`](docs/decision_log.md); schema changes
  also bump `SCHEMA_VERSION`.
- **Keep the lanes apart.** Scratch runs go to `traces/dev/`; only frozen runs
  belong in `traces/paper/`. See [`traces/README.md`](traces/README.md).
- **No secrets.** Never commit `.env` or any API key.

## Responsible use

The attack probes target synthetic pipelines built for this benchmark. Do not
point them at systems you do not own or have permission to test.
