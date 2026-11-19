"""Inter-agent message delivery for the research-assistant pipeline.

Pipeline 1.0.0 *logged* inter-agent messages but never delivered them: every
agent read its input out of shared memory or the graph state, so a message's
``claimed_sender`` could not possibly change a victim's behaviour. That made
trust assumption 3 ("no agent verifies who a message is from") unfalsifiable —
there was nothing to verify, because nothing was consumed. Attack 2 needs a
message channel a victim actually reads. This module is it.

``MessageBus`` is deliberately thin. It does exactly what a real framework's
message passing does and no more:

* it delivers a message to the named recipient's inbox,
* it records the *wire truth* in the trace (who really sent it, and what the
  message claimed about itself),
* it renders an inbox into prompt text, headers and all.

What it does **not** do is verify anything. The ``From:`` header a victim sees
is whatever the message asserts. That is not an oversight; it is the property
under test.

The ``interceptor`` hook is the probe engine's white-box injection point
(threat model §1): a probe installed on the bus sees every message before
delivery and may rewrite or replace it. Black-box probes do not use this hook —
they constrain a compromised agent's own output instead — but both end up
producing an ordinary ``Message`` on this bus, which is what keeps a single
trace format honest across both ingestion modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...tracing.logger import TraceLogger


@dataclass
class Message:
    """One inter-agent message.

    ``from_agent`` is the wire truth — which node actually emitted this.
    ``claimed_sender``/``claimed_authority`` are what the message *asserts*
    about itself. On a benign run they agree. Attack 2 lives in the gap.
    """

    from_agent: str
    to_agent: str
    content: str
    claimed_sender: str | None = None
    claimed_authority: str | None = None

    @property
    def effective_sender(self) -> str:
        """What the recipient will believe, absent any verification."""
        return self.claimed_sender or self.from_agent

    def render(self) -> str:
        """The message as the recipient sees it, headers included.

        The victim is shown ``claimed_sender``, never ``from_agent`` — an
        unauthenticated channel cannot show a recipient anything else. If this
        rendered the true sender the pipeline would be doing identity
        verification for free and there would be no attack to measure.
        """
        header = f"From: {self.effective_sender}"
        if self.claimed_authority:
            header += f"\nAuthority: {self.claimed_authority}"
        return f"{header}\nTo: {self.to_agent}\n\n{self.content}"


Interceptor = Callable[[Message], Message | None]
"""Probe hook. Returns the message to deliver, or None to drop it entirely."""


class MessageBus:
    """Delivers inter-agent messages and traces every one of them."""

    def __init__(
        self, logger: TraceLogger, interceptor: Interceptor | None = None
    ) -> None:
        self._logger = logger
        self._interceptor = interceptor
        self._inboxes: dict[str, list[Message]] = {}

    def send(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        claimed_sender: str | None = None,
        claimed_authority: str | None = None,
    ) -> Message | None:
        """Send a message, running it past the interceptor first.

        The trace records the message **as delivered**, after interception —
        that is what the victim acted on, so it is what an oracle reading the
        trace must see. What was staged (as opposed to what the pipeline would
        have sent on its own) is recorded separately in ``RunConfig.attack_id``
        and ``RunConfig.extra['attack']``.
        """
        message = Message(
            from_agent=from_agent,
            to_agent=to_agent,
            content=content,
            claimed_sender=claimed_sender or from_agent,
            claimed_authority=claimed_authority,
        )

        if self._interceptor is not None:
            message = self._interceptor(message)
            if message is None:
                return None

        self._inboxes.setdefault(message.to_agent, []).append(message)
        self._logger.log_message(
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            content=message.content,
            claimed_sender=message.claimed_sender,
            claimed_authority=message.claimed_authority,
        )
        return message

    def deliver(self, message: Message) -> Message | None:
        """Inject a fully-formed message. The white-box probe's direct path."""
        return self.send(
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            content=message.content,
            claimed_sender=message.claimed_sender,
            claimed_authority=message.claimed_authority,
        )

    def inbox(self, agent: str) -> list[Message]:
        return list(self._inboxes.get(agent, []))

    def render_inbox(self, agent: str, empty: str = "(no messages)") -> str:
        """The recipient's inbox as prompt text."""
        messages = self.inbox(agent)
        if not messages:
            return empty
        return "\n\n---\n\n".join(m.render() for m in messages)
