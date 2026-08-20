"""Core data contracts for JARVIS.

Every subsystem speaks these types. Adding a capability means adding an Ability
and a handler; adding an environment means implementing the Environment protocol.
Neither requires changing the orchestrator.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


class Category(str, Enum):
    """The six benchmark task categories (Table II of the baseline study).

    Abilities declare which category they serve so evaluation results map
    directly onto the same taxonomy used for the ARIA / Mark-XXXIX baselines.
    """

    FILE_OPS = "file_operations"
    FILE_SEARCH = "file_search"
    APP_CONTROL = "app_launch_control"
    COMPOSITE = "multi_step_composite"
    GUI_AUTOMATION = "gui_automation"
    WEB_INFO = "web_information_retrieval"
    MEMORY = "memory"
    SYSTEM = "system_state"


class Risk(str, Enum):
    LOW = "low"          # auto-execute
    MEDIUM = "medium"    # confirm unless --yes
    HIGH = "high"        # confirm always unless --yes, logged prominently
    BLOCKED = "blocked"  # never executes; guardrails reject before the gate


class Tier(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    DETERMINISTIC = "deterministic"  # no model needed at all


class Privacy(str, Enum):
    """Whether the request content may leave the machine."""

    LOCAL_ONLY = "local_only"   # references local paths / system state
    SHAREABLE = "shareable"     # generic question, safe to send to a cloud API


class Difficulty(str, Enum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    HARD = "hard"


# --------------------------------------------------------------------------
# Abilities
# --------------------------------------------------------------------------

class Ability(BaseModel):
    """A capability JARVIS can perform.

    Broader than a bare tool: carries prerequisites, risk, how to verify it
    actually happened, known failure modes, and whether it can be rolled back.
    """

    id: str
    category: Category
    objective: str
    params: dict[str, str] = Field(default_factory=dict)  # name -> description
    required: list[str] = Field(default_factory=list)     # required param names
    environment: str                                       # environment id it runs in
    risk: Risk = Risk.LOW
    verification: str = "none"        # verifier strategy id
    failure_modes: list[str] = Field(default_factory=list)
    rollback: str | None = None
    version: str = "1"

    def signature(self) -> str:
        args = ", ".join(
            f"{k}{'' if k in self.required else '?'}" for k in self.params
        )
        return f"{self.id}({args})"


class ActionResult(BaseModel):
    """Outcome of executing one ability. `evidence` must be real observed state."""

    ok: bool
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0


class VerificationResult(BaseModel):
    verified: bool
    strategy: str
    detail: str
    checked: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Task grammar
# --------------------------------------------------------------------------

class Step(BaseModel):
    """One unit of a plan. A composite task is simply a plan with several steps."""

    n: int
    ability: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)
    why: str = ""

    result: ActionResult | None = None
    verification: VerificationResult | None = None
    status: str = "pending"  # pending | running | done | failed | skipped | denied


class Plan(BaseModel):
    steps: list[Step] = Field(default_factory=list)
    reasoning: str = ""
    answer: str | None = None   # set when the goal needs no action, just a reply
    unsupported: str | None = None  # set when no registered ability can do this

    @property
    def is_composite(self) -> bool:
        return len(self.steps) > 1


class Goal(BaseModel):
    """What the user asked for, plus everything needed to judge completion."""

    id: str = Field(default_factory=lambda: _new_id("goal"))
    objective: str
    created_at: float = Field(default_factory=now)
    constraints: list[str] = Field(default_factory=list)
    status: str = "open"  # open | done | partial | failed | unsupported
    completed_at: float | None = None


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

class Classification(BaseModel):
    difficulty: Difficulty
    privacy: Privacy
    needs_web: bool = False
    needs_gui: bool = False
    likely_categories: list[Category] = Field(default_factory=list)
    by: str = "rules"  # rules | llm
    rationale: str = ""


class ModelCall(BaseModel):
    tier: Tier
    model: str
    purpose: str          # plan | classify | answer | synthesize
    latency_ms: int
    ok: bool
    prompt_chars: int = 0
    output_chars: int = 0
    bytes_sent: int = 0   # 0 for local — the privacy claim, measured
    error: str | None = None


class RouteTrace(BaseModel):
    """Per-request record of how the hybrid router decided. The paper's dataset."""

    goal_id: str
    objective: str
    classification: Classification | None = None
    tier_chosen: Tier | None = None
    reason: str = ""
    calls: list[ModelCall] = Field(default_factory=list)
    escalated: bool = False
    escalation_reason: str | None = None
    degraded: bool = False       # cloud wanted but unavailable
    total_ms: int = 0

    @property
    def bytes_sent(self) -> int:
        return sum(c.bytes_sent for c in self.calls)


class TaskRecord(BaseModel):
    """Everything about one completed run — persisted for evaluation."""

    goal: Goal
    plan: Plan
    trace: RouteTrace
    ok: bool
    message: str


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------

class MemoryType(str, Enum):
    """The vision defines 20 memory types. Three are implemented in this MVP.

    The rest are declared here so the schema is stable, and the store raises
    NotImplementedError rather than silently pretending to save them.
    """

    WORKING = "working"        # implemented
    EPISODIC = "episodic"      # implemented
    SEMANTIC = "semantic"      # implemented
    PROCEDURAL = "procedural"          # NOT IMPLEMENTED
    PREFERENCE = "preference"          # NOT IMPLEMENTED
    EVENT = "event"                    # NOT IMPLEMENTED
    DECISION = "decision"              # NOT IMPLEMENTED
    FAILURE = "failure"                # NOT IMPLEMENTED
    EXPERIENCE = "experience"          # NOT IMPLEMENTED
    SKILL = "skill"                    # NOT IMPLEMENTED
    COMMITMENT = "commitment"          # NOT IMPLEMENTED
    PROJECT = "project"                # NOT IMPLEMENTED
    RELATIONSHIP = "relationship"      # NOT IMPLEMENTED
    ENVIRONMENTAL = "environmental"    # NOT IMPLEMENTED
    ORGANIZATIONAL = "organizational"  # NOT IMPLEMENTED
    TEMPORAL = "temporal"              # NOT IMPLEMENTED
    POLICY = "policy"                  # NOT IMPLEMENTED
    CAPABILITY = "capability"          # NOT IMPLEMENTED
    TOOL = "tool"                      # NOT IMPLEMENTED
    IDENTITY = "identity"              # NOT IMPLEMENTED


IMPLEMENTED_MEMORY_TYPES = {
    MemoryType.WORKING,
    MemoryType.EPISODIC,
    MemoryType.SEMANTIC,
}


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("mem"))
    type: MemoryType
    content: str
    created_at: float = Field(default_factory=now)
    source: str = "user"
    confidence: float = 1.0
    provenance: str = ""
    tags: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Environments
# --------------------------------------------------------------------------

@runtime_checkable
class Environment(Protocol):
    """One uniform shape for every place JARVIS can act.

    LocalOS, WindowsGUI and Web all implement this. Future environments
    (databases, cloud, remote machines) slot in without touching the core.
    """

    id: str

    def state(self) -> dict[str, Any]:
        """Cheap snapshot of current environment state."""

    def capabilities(self) -> list[str]:
        """Ability ids this environment can execute."""

    def constraints(self) -> list[str]:
        """Known hard limits, surfaced to planning and to the user."""

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        """Perform an ability. Must return real evidence, never a claim."""

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        """Re-observe the world to confirm the action truly took effect."""


Handler = Callable[[dict[str, Any]], ActionResult]
