"""Capability pack schema.

The vision document specifies 101 capability packs and is explicit about two
things (line 7152 onward):

  - "ALL PACKS ABOVE ARE PART OF THE TARGET JARVIS SYSTEM. They are not
    examples. They are not optional."
  - "Some capabilities may initially be NOT IMPLEMENTED but the corresponding
    extension point must exist."

and about how they must be organised (line 7200):

  - "The PACK structure is an architectural ontology, not a microservice list.
    Do NOT create 101 microservices. Group related packs into coherent
    subsystems... The architecture should therefore form a dependency graph,
    not a flat list."

So this is data, not directories. Every pack is registered with the fields the
document requires, an honest status, and - where it is not built - the concrete
extension point where it would attach. That makes the ontology queryable, which
is what lets the self model answer "can you do X?" from fact rather than from
the model's opinion of itself.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Implementation status, using the vision's own A-E scale (Part 85)."""

    IMPLEMENTED = "A"          # built and verified running
    PARTIAL = "B"              # built, with named gaps
    FOUNDATION = "C"           # interface/extension point exists, little behaviour
    PLANNED = "D"              # not built; integration strategy defined
    RESEARCH = "E"             # blocked on a research limitation, not on effort


class Maturity(str, Enum):
    """The A/B/C/D honesty scale the document demands (line 4809).

    "Do not call something 'autonomous' merely because an LLM can generate a
    plan." A capability only counts as reliable when perception, state,
    planning, action, verification, recovery, permissions and evaluation are
    all genuinely present.
    """

    POSSIBLE_TODAY = "possible_today"
    ENGINEERING_READY = "engineering_ready"
    NEEDS_RESEARCH = "needs_research"
    UNSOLVED = "unsolved"


class Pack(BaseModel):
    """One capability domain, with everything the vision requires declared."""

    id: int
    name: str
    subsystem: str
    purpose: str

    status: Status = Status.PLANNED
    maturity: Maturity = Maturity.ENGINEERING_READY

    # Where this attaches. For an unbuilt pack this is the single most useful
    # field: it names the seam a future implementation plugs into, which is what
    # makes "NOT IMPLEMENTED" an architectural position rather than an omission.
    extension_point: str = ""

    # Dependency graph, not a flat list - pack ids this one builds on.
    depends_on: list[int] = Field(default_factory=list)

    interfaces: list[str] = Field(default_factory=list)
    data_structures: list[str] = Field(default_factory=list)
    required_models: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_storage: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)

    security: str = ""
    verification: str = ""
    failure_modes: list[str] = Field(default_factory=list)
    recovery: str = ""
    learning: str = ""
    evaluation: str = ""

    # Free-text, only where there is something honest to say.
    notes: str = ""

    @property
    def implemented(self) -> bool:
        return self.status in (Status.IMPLEMENTED, Status.PARTIAL)


class Subsystem(BaseModel):
    """A coherent grouping. Several packs; one owner; one seam."""

    name: str
    purpose: str
    module: str            # where it lives, or "" when not yet built
    pack_ids: list[int] = Field(default_factory=list)
