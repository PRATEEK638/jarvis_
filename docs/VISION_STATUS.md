# Vision status — honest assessment

The vision document (Part 85) requires every capability to be classified, and
explicitly forbids presenting one level as another:

| | meaning |
|---|---|
| **A** | fully implemented and verified |
| **B** | partially implemented |
| **C** | architectural foundation exists, little behaviour yet |
| **D** | future integration required — nothing built |
| **E** | research limitation — not solvable by building harder |

Nothing below is marked higher than what has actually been run and observed.

## A — implemented and verified

| Capability | Vision part | Evidence |
|---|---|---|
| Ability engine (typed, risk-tiered, verifiable) | 2 | 23 abilities, each with declared risk + verification strategy |
| Environment abstraction | 10, 70, 71 | 3 environments behind one observe/act/verify protocol |
| Model router (local + 4 cloud, replaceable) | 41, 42 | 7 routes; live failover and key-pool rotation observed |
| Policy / guardrails | 12, 54 | Blocks catastrophic ops *before* the risk gate; tested |
| Verification from real state | 34 | Every step re-observed; "model says done" is never accepted |
| Capability-gap honesty | 13 | Deterministic coverage table; refuses instead of substituting |
| Event sourcing | 12 | Every decision/call/result appended to `events.jsonl` |
| Voice (bidirectional, barge-in, tool-calling) | 43 | Gemini Live; real actions executed from speech |
| Memory: working / episodic / semantic | 19 | SQLite; survives restart; verified across processes |
| Semantic recall via local embeddings | 18 | nomic-embed-text; paraphrase recall verified |
| Skill system (domain expertise as data) | 25 | 4 playbooks; 7/7 routing incl. negative cases |
| Autonomy gate with human approval | 36 | Blocking confirmation; denial genuinely prevents execution |
| Multi-surface (desktop, web, CLI) | 35 | Three surfaces, one orchestrator |
| Observability / route trace | 56 | Per-request trace: tier, latency, bytes, verification |

## B — partially implemented

| Capability | Vision part | What exists / what is missing |
|---|---|---|
| Planning | 32 | Multi-step DAG-less plans with retry. No parallel execution, no explicit rollback plans. |
| Recovery | 35 | One retry with the gap made explicit. No rollback journal, no resume-after-crash. |
| Web research | 16 | Search → fetch → extract → cite, 3 backends. No cross-source contradiction detection or evidence graph. |
| Computer control hierarchy | 14 | API → CLI → PowerShell → UI Automation implemented. No vision fallback. |
| Self model | 3 | Capability list + measured availability. No confidence-per-capability or learned failure modes. |
| Personality | 4, 61 | Persona is explicit and separate from policy. Not configurable at runtime. |

## C — foundation only

Goal hierarchy (6) · Temporal reasoning (11) · Proactivity/attention budget (37) ·
Commitment tracking (39) · Evaluation harness (57)

## D — not built

Capability *discovery* of unknown apps (4, 12) · Capability acquisition (6) ·
Multi-agent orchestration (40) · Knowledge graph (21) · Process mining (27) ·
Learning from demonstration (26) · Simulation before risky action (33) ·
Organisational memory (25, 65) · Device fabric (52) · Robotics (38, 66) ·
Skill marketplace + sandbox (60, 61) · Fine-tuning (59)

## E — research limitations

- **Reliable GUI automation of arbitrary applications.** The accessibility tree
  covers well-behaved apps; anything canvas-rendered needs vision, which is not
  reliable enough today for unattended action.
- **Unsupervised self-improvement.** The vision (59) already constrains this
  correctly: personalise through memory/skills/policy, not weight updates.
- **Local model planning quality.** Measured directly: `llama3:8b` substitutes a
  plausible different action rather than refusing, and mis-plans routine
  requests. Mitigated with deterministic fast paths and the coverage table, not
  solved. A larger local model needs more VRAM than this machine has.

## The honest summary

The **architecture** of the vision is real: abilities, environments, routing,
policy, verification, memory and skills are all first-class and extensible —
adding a capability is a file, not a refactor.

The **breadth** is a fraction of the document. That is expected: Part 70 of the
vision itself says not to pretend the whole system can be built in one pass, and
to grow capability-by-capability on a foundation that actually runs. That is
what this is.

---

## Third-party attribution

**Mark-L** by FatihMakes — https://github.com/FatihMakes/Mark-L — licensed
**CC BY-NC 4.0**.

The desktop HUD's *visual design* is derived from Mark-L: the composition of
dot grid, layered halo, expanding pulse rings, counter-rotating segmented arc
rings, dual scanners, graduated bezel, crosshair, corner brackets, particle
emission and bar spectrum. The `MetricBar` layout and its 65%/85% warning
thresholds follow theirs as well.

The implementation was written against that design rather than copied — a line
comparison shows 24 of 138 substantive lines in `hud_canvas.py` matching, and
those are near-unavoidable Qt boilerplate (`setRenderHint`, `setPen(NoPen)`,
standard circle geometry). The animation state model, easing, colour system and
data bindings are different, and one behaviour is deliberately different: their
spectrum is `random.randint(3, 20)` while speaking, whereas ours is driven by
the real RMS of the audio stream.

**Two consequences of the licence that matter:**

- **BY** — attribution is required. It is given here and in the source headers
  of `jarvis/desktop/widgets/hud_canvas.py` and `metricbar.py`.
- **NC** — non-commercial use only. This project must not be sold or used
  commercially while that design remains.

**If this is submitted for academic assessment, disclose the derivation.**
Reusing an open-source design is normal and legitimate engineering; presenting
it as wholly original is not, and institutions treat that as plagiarism. State
plainly that the HUD visual design is derived from Mark-L under CC BY-NC 4.0,
and that the backend (orchestrator, router, abilities, memory, policy,
verification) is original work.
