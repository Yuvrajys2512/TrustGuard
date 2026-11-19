"""Inter-agent message delivery for the research-assistant pipeline.

The implementation moved to ``pipelines/common/messaging.py`` in Phase 4 so
both target pipelines deliver messages over one ``Message`` class — see the
note in ``memory.py`` for why identity rather than shape is what matters here.
Behaviour is unchanged and this module re-exports the same objects.

See ``pipelines/common/messaging.py`` for the bus contract, the ``From:``
header the victim actually sees, and the probe interceptor hook.
"""

from __future__ import annotations

from ..common.messaging import Interceptor, Message, MessageBus

__all__ = ["Interceptor", "Message", "MessageBus"]
