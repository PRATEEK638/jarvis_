# JARVIS architecture

## Why this shape

The system exists to answer one question left open by the ARIA (local-only) vs.
Mark-XXXIX (cloud-only) study: *can request-level hybrid routing capture the
privacy of on-device inference and the capability of a remote model at the same
time?* Everything below serves that, plus a second requirement the baseline
systems did not meet — **never report success that did not happen**.

It is a modular monolith: one Python process, hard internal seams. Splitting into
services would add deployment cost and buy nothing at this scale, but each module
depends only on the contracts in `core/contracts.py`, so a boundary can become a
process boundary later without a rewrite.

---

## Components

```mermaid
flowchart TB
    U[User request<br/>CLI] --> O[Orchestrator]

    O --> CL[Classifier<br/>rules, no model]
    O --> CV[Coverage check<br/>capability gaps]
    O --> FP[Fast path<br/>unambiguous intents]
    O --> R[Router]
    O --> P[Planner]
    O --> G[Guardrails]
    O --> V[Verification]
    O --> M[(Memory<br/>SQLite)]
    O --> E[(Event log<br/>JSONL)]

    R --> REG[Model registry<br/>7 routes]
    REG --> A1[Ollama adapter]
    REG --> A2[OpenAI-compat adapter]
    REG --> A3[Gemini adapter]

    A1 --> L1[llama3:8b<br/>local, GPU]
    A2 --> L2[LM Studio<br/>gemma-4 / qwythos-9b]
    A2 --> C1[OpenRouter]
    A2 --> C2[NVIDIA NIM]
    A2 --> C3[Sarvam · metered]
    A3 --> C4[Gemini 2.5 Flash]

    P --> AR[Ability registry<br/>23 abilities]
    AR --> EN1[LocalOS environment]
    AR --> EN2[Windows GUI environment]
    AR --> EN3[Web environment]

    EN1 --> FS[Filesystem · processes · PowerShell]
    EN2 --> UIA[UI Automation tree]
    EN3 --> NET[Search · fetch · extract]
```

---

## Request lifecycle

```mermaid
flowchart TD
    S([request]) --> C[classify<br/>privacy · difficulty · web/GUI]
    C --> GAP{capability<br/>gap?}
    GAP -->|yes| REFUSE[refuse and name<br/>what is missing]
    GAP -->|no| FAST{unambiguous<br/>intent?}
    FAST -->|yes| EXEC
    FAST -->|no| ROUTE[route: pick candidate chain]
    ROUTE --> PLAN[plan as JSON]
    PLAN --> VAL{valid against<br/>registry?}
    VAL -->|no| NEXT[next route in chain] --> PLAN
    VAL -->|yes| EXEC[execute step]
    EXEC --> GUARD{guardrail<br/>violation?}
    GUARD -->|yes| BLOCK[refuse permanently]
    GUARD -->|no| RISK{medium/high<br/>risk?}
    RISK -->|yes| ASK[ask for confirmation]
    RISK -->|no| ACT
    ASK --> ACT[act in environment]
    ACT --> VER{verify by<br/>re-observing}
    VER -->|verified| MORE{more steps?}
    VER -->|not verified| RETRY[one retry if transient]
    RETRY --> MORE
    MORE -->|yes| EXEC
    MORE -->|no| ANS[answer + persist trace]
```

The important edge is `VER`. The model's claim never sets the outcome; the
outcome is set by reading the filesystem, the process table, or the window tree
back afterwards.

---

## Routing

```mermaid
flowchart TD
    REQ[request] --> PRIV{mentions local files,<br/>paths or machine state?}
    PRIV -->|yes| LOCAL[on-device routes ONLY<br/>cloud is not a candidate]
    PRIV -->|no| NEED{needs current<br/>external info, or<br/>hard planning?}
    NEED -->|yes| CAP[order by capability<br/>metered still last]
    NEED -->|no| CHEAP[order by:<br/>not-metered, free,<br/>private, quality]
    LOCAL --> FIT{fits in RAM/VRAM,<br/>or already resident?}
    FIT -->|no| SKIP[skip with a readable reason]
    FIT -->|yes| RUN[run]
    CAP --> RUN
    CHEAP --> RUN
    RUN --> FAIL{failed?}
    FAIL -->|yes| BENCH[bench route for 90s<br/>doubling on repeat] --> NEXTR[next in chain]
    FAIL -->|no| DONE([done])
```

**Privacy is a filter, not a weight.** For a local-only request the cloud routes
are removed from the candidate list entirely, so no ranking accident can send a
filename to a remote API.

**Cost order is deliberate.** `preference_key` sorts by
`(last_resort, not free, not private, -quality)`. A metered provider with a small
prepaid balance therefore sorts last even though its quality score is high — it
is never spent on work another route can do.

**The circuit breaker is time-boxed.** Permanent benching was a real defect: one
120-second read timeout on the only local model took down every subsequent
request in a benchmark run. Cooldown now starts at 90 s and doubles per
consecutive failure, and any success clears it.

---

## Memory

```mermaid
flowchart LR
    subgraph Implemented
        W[Working<br/>in-process, bounded]
        EP[Episodic<br/>every task, SQLite]
        SEM[Semantic<br/>facts, SQLite]
    end
    subgraph Declared but NOT IMPLEMENTED
        X[17 further types<br/>procedural, decision,<br/>failure, commitment, ...]
    end
    SEM -->|token overlap<br/>+ recency| RECALL[recall]
    X -.->|raises<br/>NotImplementedError| ERR[loud failure,<br/>not silent loss]
```

Semantic recall is token-overlap scoring, not vector search. That is a stated
limitation, isolated behind `MemoryStore.recall()` so an embedding backend can
replace it without touching callers — LM Studio already serves a nomic embedding
model locally for exactly that upgrade.

---

## Security model

Three layers, in this order, and the first cannot be reached around:

1. **Guardrails** (`policy/guardrails.py`) — destructive commands, deletion
   abilities, and writes to protected system roots are refused. Evaluated before
   any confirmation logic, so no autonomy setting or model output bypasses them.
2. **Risk tiers** — each `Ability` declares low/medium/high. Medium and high
   prompt for confirmation; `--yes` auto-approves and logs that it did.
3. **Audit** — every model call, ability execution, verification result and
   refusal is appended to `data/events.jsonl`, with credential-shaped strings
   scrubbed by pattern before writing.

Keys live in a gitignored `config/keys.json`, are read at the HTTP layer only,
and never enter a prompt body.

**Known gaps** (see `CAPABILITY_ONTOLOGY.md`): no sandboxing — abilities run with
the user's full privileges; no prompt-injection defence — text fetched from the
web is not yet treated as hostile; no OS keystore integration.

---

## Extension points

| To add | Do this | Core changes |
|---|---|---|
| A capability | One `Ability` in `abilities/registry.py` + a handler in an environment | none |
| An environment | Implement `state / capabilities / constraints / act / verify` | none |
| A model provider | One row in `models/registry.py` | none, unless its wire format is new |
| A wire protocol | One `Provider` subclass | one dict entry |
| A verification strategy | A branch in that environment's `verify()` | none |

Four of the seven registered providers already share a single OpenAI-compatible
adapter, which is the clearest evidence that the seam works.
