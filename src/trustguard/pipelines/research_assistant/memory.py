"""Shared memory (blackboard) for the research-assistant pipeline.

The implementation moved to ``pipelines/common/memory.py`` in Phase 4 so
pipeline #2 shares one ``MemoryWrite`` class with pipeline #1 — the probe
engine annotates its hooks with that type, and two separate definitions would
make every probe quietly pipeline-specific. Nothing about the behaviour
changed; this module re-exports the same objects it used to define, so
``from .memory import SharedMemory`` and
``from ..pipelines.research_assistant.memory import MemoryWrite`` both still
resolve to exactly what they resolved to before.

See ``pipelines/common/memory.py`` for the interceptor contract and the trust
assumption it exists to leave unenforced.
"""

from __future__ import annotations

from ..common.memory import MemoryInterceptor, MemoryWrite, SharedMemory

__all__ = ["MemoryInterceptor", "MemoryWrite", "SharedMemory"]
