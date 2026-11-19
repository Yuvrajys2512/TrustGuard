"""Shared memory (blackboard) for the research-assistant pipeline.

Every read and write is traced. This is the channel that cross-agent context
poisoning (attack 3) rides on: the researcher writes a claim, the summarizer
reads it and — by default — treats it as fact without re-verifying it.

``provenance`` is recorded on every write from day one, even though nothing
enforces it yet. Phase 6's defense turns that recorded field into a checked
one; Phase 3's oracle uses it to tell "asserted as fact" from "attributed to a
source". Adding the field later would have meant re-running every benign trace.

## The interceptor (added Phase 4, pipeline 1.2.0)

``MessageBus`` has carried a probe hook since 1.1.0 because attack 2 rides the
message channel. Attack 3 does not: threat model §3 requires the false claim be
injected *into shared memory*, and a probe that could only rewrite messages
could not stage it at all — it could only stage something adjacent and call it
attack 3. So memory gets the same hook, on the same terms as the bus:

* the probe sees every write before it lands and may rewrite the value, the
  provenance, or both;
* the trace records the write **as it landed**, because that is what the victim
  later read;
* what was *staged* lives in ``RunConfig.extra['attack']``, so the trace never
  has to lie about the wire truth to describe the attack.

Like the bus, this verifies nothing. Any agent may write any key, including one
another agent wrote, and no reader can tell which agent wrote what it is
reading. That is trust assumption 2, and it is the property under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ...tracing.logger import TraceLogger


@dataclass
class MemoryWrite:
    """One write to the blackboard, before it lands.

    Mirrors ``messaging.Message``: a small mutable-by-replacement record that a
    probe can rewrite, so both injection points have the same shape and the
    probe base class can treat them the same way.
    """

    agent: str
    key: str
    value: Any
    provenance: str = "unattributed"


MemoryInterceptor = Callable[[MemoryWrite], "MemoryWrite | None"]
"""Probe hook. Returns the write to commit, or None to drop it entirely."""


class SharedMemory:
    def __init__(
        self, logger: TraceLogger, interceptor: MemoryInterceptor | None = None
    ) -> None:
        self._store: dict[str, Any] = {}
        self._logger = logger
        self._interceptor = interceptor

    def write(self, agent: str, key: str, value: Any, provenance: str = "unattributed") -> None:
        write = MemoryWrite(agent=agent, key=key, value=value, provenance=provenance)

        if self._interceptor is not None:
            intercepted = self._interceptor(write)
            if intercepted is None:
                return
            write = intercepted

        self._store[write.key] = write.value
        self._logger.log_memory_write(
            agent=write.agent,
            key=write.key,
            value=write.value,
            provenance=write.provenance,
        )

    def read(self, agent: str, key: str, default: Any = None) -> Any:
        self._logger.log_memory_read(agent=agent, key=key)
        return self._store.get(key, default)

    def keys(self) -> list[str]:
        return list(self._store.keys())

    def snapshot(self) -> dict[str, Any]:
        """Untraced copy, for putting into the LangGraph state object."""
        return dict(self._store)
