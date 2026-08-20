# Capability ontology and honest status

Every capability is labelled with what is actually true of it today:

| Label | Meaning |
|-------|---------|
| **A** | Fully implemented and verified on real hardware |
| **B** | Partially implemented — works, with stated limits |
| **C** | Architectural foundation exists; the capability itself does not |
| **D** | Future integration required; nothing built |
| **E** | Research limitation — not solvable by engineering alone right now |

Nothing below is labelled higher than it is. Where a capability is absent, the
system says so at runtime rather than substituting something else.

---

## 1. Computer / filesystem

| Capability | Status | Notes |
|---|---|---|
| Create / read files and folders | **A** | Verified by reading state back from disk |
| Copy, move, rename | **A** | Verified: destination present, source consumed |
| Delete | **D (deliberate)** | Permanently blocked by policy, not missing by accident |
| Search by filename | **A** | Capped at 40,000 files scanned per query |
| Search by file content | **B** | Text formats only; no PDF/DOCX extraction |
| Live system state (CPU/RAM/disk/processes) | **A** | Read from `psutil`, never estimated |
| Shell execution | **B** | PowerShell, guardrailed; no sandbox or resource limits |
| Scheduled / unattended tasks | **D** | No scheduler |

## 2. Applications and GUI

| Capability | Status | Notes |
|---|---|---|
| Launch an application | **A** | Verified against the real process table |
| Enumerate open windows | **A** | UI Automation |
| Read a window's control tree | **A** | Named controls, types, positions, enabled state |
| Click a named control | **A** | InvokePattern / SelectionItem / Toggle, click as fallback |
| Type into a window | **A** | Refuses unless that window is verified in the foreground |
| Per-application knowledge | **D** | No model of what any specific app's controls *mean* |
| Screen vision / OCR | **D** | No pixel-level understanding; accessibility tree only |

## 3. Web

| Capability | Status | Notes |
|---|---|---|
| Web search | **A** | Via `ddgs`; raw HTML endpoint as fallback |
| Fetch and extract page text | **B** | HTTP only — JavaScript-rendered pages yield little |
| Multi-source research with citations | **B** | Searches, reads top pages, synthesises with sources |
| Browser automation (click/fill/login) | **D** | No Playwright tier; `Environment` interface is ready for one |
| Authenticated / logged-in web tasks | **D** | No session handling |

## 4. Reasoning and orchestration

| Capability | Status | Notes |
|---|---|---|
| Intent classification | **A** | Rule-based, deterministic, explainable |
| Multi-step planning | **A** | JSON plan validated against the ability registry |
| Deterministic fast paths | **A** | Unambiguous intents bypass the model entirely |
| Hybrid per-request model routing | **A** | 7 routes, privacy as a hard constraint |
| Provider failover | **A** | Ordered candidate chain; failed routes benched |
| Independent verification | **A** | Per-ability strategies re-observing real state |
| Recovery | **B** | One retry for transient failures; no rollback journal |
| Simulation before risky actions | **D** | No sandbox, VM or dry-run |
| Multi-agent orchestration | **D** | Single agent by design at this stage |

## 5. Memory

| Capability | Status | Notes |
|---|---|---|
| Working memory | **A** | Bounded in-session context |
| Episodic memory | **A** | Every task persisted to SQLite |
| Semantic memory (facts) | **A** | Survives process restart — tested |
| Semantic retrieval | **B** | Token-overlap scoring with recency; **not** vector search |
| Consolidation / deduplication / forgetting | **D** | No pipeline |
| The other 17 declared memory types | **C** | Schema exists; writing one raises `NotImplementedError` |
| Knowledge graph | **D** | Not built |

## 6. Safety and governance

| Capability | Status | Notes |
|---|---|---|
| Hard destructive-action guardrails | **A** | Run before the risk gate; unreachable from it |
| Protected system paths | **A** | Windows / Program Files / boot |
| Risk tiers with confirmation | **A** | Per-ability; `--yes` auto-approves and logs |
| Capability-gap honesty | **A** | Deterministic; refuses instead of substituting |
| Audit / event log | **A** | JSONL, credential patterns scrubbed |
| Secret handling | **B** | Gitignored file, never logged or prompted; no OS keystore |
| Sandboxing / least privilege | **D** | Runs with the user's full privileges |
| Prompt-injection defence | **D** | Fetched web text is not yet treated as hostile input |
| RBAC / multi-user | **D** | Single user |

## 7. Domains from the wider vision

| Domain | Status | What exists |
|---|---|---|
| Software engineering | **C** | File and shell primitives; no AST, LSP, test-runner or Git integration |
| Data engineering | **D** | — |
| AI/ML engineering | **D** | — |
| DevOps / SRE / cloud | **D** | — |
| Office documents (DOCX/XLSX/PPTX) | **D** | Plain text only |
| Communication (email/chat) | **D (deliberate)** | Explicitly refused with an explanation of what is required |
| Voice / wake word | **D** | — |
| Robotics / physical devices | **D** | The `Environment` abstraction is the intended seam |
| Organisational memory | **D** | Single-user only |

---

## Summary

- **A (fully implemented): 24 capabilities** — the six benchmark categories, routing, verification, guardrails, memory persistence.
- **B (partial, limits stated): 8**
- **C (foundation only): 3**
- **D (not built): 24**
- **E: 0** — nothing here is blocked by a research limitation; the gaps are engineering work not yet done.

The architecture is designed so that D items become A items by adding an
`Ability`, an `Environment`, or a registry row — not by rewriting the core. That
claim is what the current build is meant to demonstrate, and it is the honest
scope of the contribution.
