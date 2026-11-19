"""Channel primitives shared by every TrustGuard target pipeline.

Pipeline #1 (`research_assistant`) defined the traced message bus, the traced
blackboard and the permission-enforcing tool registry inline, because it was
the only pipeline. Phase 4 adds pipeline #2 (`task_delegation`), and the probe
engine binds to these types *by identity*: `probes/base.py` annotates its hooks
with `Message` and `MemoryWrite`, and a probe written against one pipeline must
stage the same attack against the other without modification. Two structurally
identical but separately-defined `Message` classes would make every probe
silently pipeline-specific — duck-typing would paper over it right up until an
`isinstance` check somewhere decided the attack had not fired.

So the primitives live here once and both pipelines import them. The classes are
byte-for-byte the ones pipeline #1 shipped with in 1.2.0; `research_assistant`
re-exports them so its own imports, and everything importing through it, keep
working unchanged. No agent behaviour changes, which is why pipeline #1's
PIPELINE_VERSION does not move.
"""

from __future__ import annotations

from .memory import MemoryInterceptor, MemoryWrite, SharedMemory
from .messaging import Interceptor, Message, MessageBus
from .tools import Tool, ToolRegistry

__all__ = [
    "Interceptor",
    "MemoryInterceptor",
    "MemoryWrite",
    "Message",
    "MessageBus",
    "SharedMemory",
    "Tool",
    "ToolRegistry",
]
