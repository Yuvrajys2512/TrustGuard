#!/usr/bin/env python
"""Check which configured LLM backends this machine can actually reach.

Model availability on Groq is per-account and changes as models are retired,
so run this after cloning or when a sweep starts failing with model_not_found:

    python scripts/check_backends.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustguard.llm.client import DEFAULT_BACKENDS, Backend, RotatingLLMClient  # noqa: E402


def probe(backend: Backend) -> tuple[bool, str]:
    if not backend.available:
        return False, f"no {backend.api_key_env} in environment"
    try:
        client = RotatingLLMClient(backends=[backend], max_attempts=1)
        reply = client.complete(system="Reply with exactly: ok", user="ping")
        return True, reply[:40].replace("\n", " ")
    except Exception as exc:  # noqa: BLE001 - reporting tool
        return False, f"{type(exc).__name__}: {str(exc)[:100]}"


def main() -> int:
    print(f"{'backend':<45} {'status':<8} detail")
    print("-" * 100)
    any_ok = False
    for backend in DEFAULT_BACKENDS:
        ok, detail = probe(backend)
        any_ok |= ok
        print(f"{backend.provider + ':' + backend.model:<45} {'OK' if ok else 'FAIL':<8} {detail}")
    print()
    if not any_ok:
        print("No usable backend. Set an API key in .env (see .env.example).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
