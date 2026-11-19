"""Probe engine base classes.

A **probe** stages one attack from the Phase 0 taxonomy against a target
pipeline. The pattern is the same for all five and is fixed here so Phase 4
adds attacks rather than rewriting plumbing:

    inject  ->  observe  ->  log

* **inject** — the probe is installed on the pipeline's injection points and
  sees traffic before it lands. There are two, because the taxonomy needs two:
  the **message bus** (every inter-agent message, before delivery — attacks 1,
  2, 5) and **shared memory** (every write, before it commits — attack 3,
  whose §3 definition requires the false claim be injected into the blackboard
  specifically). A probe may rewrite what it sees, replace it, or leave it
  alone; the default for both hooks is to leave it alone, so a probe only
  implements the point it actually uses. Where it injects is its
  ``injection_point``.
* **observe** — the pipeline runs normally. The probe does not watch the
  victim; the tracing layer already records everything the victim does.
* **log** — the resulting ``RunTrace`` carries ``config.attack_id``,
  ``config.ingestion_mode`` and ``config.extra['attack']``, so a trace is
  self-describing: a reader can tell what was staged without the probe code.

Deliberately *not* here: any judgment about whether the attack worked. That is
the oracle's job (Phase 3). A probe stages an attack and records what it staged;
it never marks its own homework. Keeping the two apart is what stops a probe
from defining success as "the thing I did happened".

The trace records the message **as delivered**, with ``from_agent`` set to the
node that really emitted it and ``claimed_sender`` to whatever the message
asserts. A probe must never falsify ``from_agent`` — the wire truth is the
ground truth an oracle checks the victim's belief against, and a trace that
lies about it cannot be scored.

The same rule binds the memory hook: a probe may rewrite a write's ``value``
and its ``provenance``, but never its ``agent``. ``agent`` is which node really
made the write. ``provenance`` is a *claim about where the value came from* —
it is the memory channel's ``claimed_sender``, and attack 3's more interesting
arms live in the gap between the two exactly as attack 2 lives in the message
channel's.
"""

from __future__ import annotations

from abc import ABC
from typing import Any

from ..pipelines.common.memory import MemoryWrite
from ..pipelines.common.messaging import Message
from ..tracing.schema import IngestionMode


class Probe(ABC):
    """One staged attack against one pipeline.

    Subclasses set the class-level metadata and override at least one of
    ``on_message`` / ``on_memory_write`` — whichever channel their attack uses.
    """

    #: Stable identifier written to ``RunConfig.attack_id``. Format:
    #: ``attack<N>_<slug>`` where N is the taxonomy number (threat model §2).
    attack_id: str = ""
    #: Human-readable taxonomy name.
    attack_name: str = ""
    #: Which target pipeline this instance is realised against. One attack
    #: definition, two sets of coordinates: the agents, the channel key and the
    #: gate an attack aims at are all pipeline-specific, while the definition
    #: being tested is not. Recorded in ``describe()`` so a trace says which
    #: realisation was staged rather than leaving it to be inferred from the
    #: agent names — the two pipelines are meant to be compared, and a cell of
    #: the 5x2 matrix that cannot name its own pipeline is not evidence.
    pipeline: str = ""
    #: Which threat-model ingestion mode this probe realises (§1).
    ingestion_mode: IngestionMode = IngestionMode.WHITE_BOX
    #: The agent whose behaviour decides success.
    victim: str = ""
    #: The agent the injected message purports to come from, or is emitted by.
    attacker: str = ""
    #: Where the injection lands, as ``"<from> -> <to>"``.
    injection_point: str = ""
    #: Distinctive strings this probe plants that cannot occur on a benign run.
    #: Finding one in a victim's tool-call arguments or output is *evidence*
    #: that the injected content propagated — it is not a verdict, and an
    #: oracle still has to decide whether propagation amounts to success under
    #: the attack's own definition. Exposed here so generic runners can gather
    #: evidence without importing attack-specific constants.
    markers: list[str] = []

    def __init__(self) -> None:
        #: Messages this probe actually modified, for the run summary. Note
        #: this records the probe's *action*, never the victim's response.
        self.injections: list[dict[str, Any]] = []

    # -- the hooks --------------------------------------------------------
    #
    # Both default to pass-through. A probe overrides the one its attack
    # actually uses: attack 2 rewrites messages and never touches memory,
    # attack 3 the reverse. Making either abstract would force every probe to
    # write an inert override of the other, and an inert override is a place
    # for a bug to hide.

    def on_message(self, message: Message) -> Message | None:
        """Inspect a message in flight; return the one to deliver.

        Return ``message`` unchanged to let it through. Returning ``None``
        drops it, which no attack in the current taxonomy needs but the bus
        supports.
        """
        return message

    def on_memory_write(self, write: MemoryWrite) -> MemoryWrite | None:
        """Inspect a blackboard write before it commits; return the one to land.

        Return ``write`` unchanged to let it through, or ``None`` to drop it.
        A probe may rewrite ``value`` and ``provenance``; it must not rewrite
        ``agent``, which is the wire truth.
        """
        return write

    def __call__(self, message: Message) -> Message | None:
        """Message-bus entry point. The bus installs the probe itself as its
        interceptor, so the callable protocol stays the message one; memory
        goes through ``on_memory_write`` by name."""
        return self.on_message(message)

    # -- bookkeeping ------------------------------------------------------

    def _record_memory_injection(
        self, original: MemoryWrite, injected: MemoryWrite
    ) -> None:
        """Record a rewritten blackboard write.

        ``injection_point`` reads ``memory:<key>`` so a reader can tell at a
        glance which channel an attack used without knowing the probe.
        """
        self.injections.append(
            {
                "injection_point": f"memory:{original.key}",
                "channel": "memory",
                "writing_agent": original.agent,
                "original_value": original.value,
                "original_provenance": original.provenance,
                "injected_value": injected.value,
                "injected_provenance": injected.provenance,
            }
        )

    def _record_injection(self, original: Message, injected: Message) -> None:
        self.injections.append(
            {
                "injection_point": f"{original.from_agent} -> {original.to_agent}",
                "channel": "message",
                "original_content": original.content,
                "original_claimed_sender": original.claimed_sender,
                "injected_content": injected.content,
                "injected_claimed_sender": injected.claimed_sender,
                "injected_claimed_authority": injected.claimed_authority,
            }
        )

    def describe(self) -> dict[str, Any]:
        """Self-describing record for ``RunConfig.extra['attack']``."""
        return {
            "attack_id": self.attack_id,
            "attack_name": self.attack_name,
            "pipeline": self.pipeline,
            "ingestion_mode": self.ingestion_mode.value,
            "attacker": self.attacker,
            "victim": self.victim,
            "injection_point": self.injection_point,
            "markers": list(self.markers),
            "fired": bool(self.injections),
            "injections": self.injections,
        }
