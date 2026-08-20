# JARVIS — a hybrid local/cloud desktop agent

JARVIS interprets a natural-language request, plans it as a sequence of
registered abilities, decides **per request** whether to think on-device or in
the cloud, executes through a real environment, and then **re-observes the
machine to confirm the work actually happened** before reporting success.

It is the system the earlier ARIA (local-only) vs. Mark-XXXIX (cloud-only) study
concluded should be built: that paper found neither architecture dominated, and
recommended request-level hybrid routing as future work. This is that router,
inside an extensible ability/environment architecture.

---

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r jarvis/requirements.txt
```

Nothing else is required — with [Ollama](https://ollama.ai) running and a model
pulled (`ollama pull llama3:8b`), JARVIS is fully functional with **no API key
and no internet connection**.

```bash
.venv/Scripts/python.exe -m jarvis --status              # what is available
.venv/Scripts/python.exe -m jarvis                       # interactive
.venv/Scripts/python.exe -m jarvis "create a folder called reports on my desktop"
```

To add cloud routes, copy `config/keys.example.json` to `config/keys.json` and
fill in whichever providers you have. Each key present simply adds a route. A
provider may hold a **pool of keys** — free tiers are capped per key, so JARVIS
rotates to the next one when it hits a quota error:

```json
{
  "gemini": ["key1", "key2"],
  "gemini_voice": "key-reserved-for-speech",
  "openrouter": ["key1", "key2", "key3"],
  "nvidia": "key",
  "sarvam": ["key1", "key2"]
}
```

### Talking to it

```bash
.venv/Scripts/python.exe -m jarvis --voice     # speak; JARVIS speaks back
.venv/Scripts/python.exe -m jarvis --speak     # type, but hear the answers
```

Speech in is captured from the microphone with silence detection and transcribed
by Gemini (which takes audio natively, so no separate speech service is needed).
Speech out uses Windows SAPI — built into the OS, no dependency, no network.
Voice runs through the *same* orchestrator as text, so it inherits every
capability, guardrail and verification step.

This is half-duplex: listen, act, speak. It is **not** the full Gemini Live
bidirectional stream with mid-sentence barge-in, which would need a persistent
websocket audio session and is not implemented.

---

## What it can do

Capabilities map onto the six task categories of the baseline benchmark, so
results are directly comparable.

| # | Category | Abilities |
|---|----------|-----------|
| 1 | File operations | `create_folder` `create_file` `read_file` `list_dir` `copy_path` `move_path` `rename_path` |
| 2 | File search | `find_files` (by name) `search_in_files` (by content) |
| 3 | Application launch & control | `open_app` `run_command` `list_processes` |
| 4 | Multi-step composite | any plan with more than one step |
| 5 | GUI automation | `list_windows` `focus_window` `read_ui` `click_ui` `type_text` |
| 6 | Web / information retrieval | `web_search` `fetch_page` `research` |
| — | Memory | `remember` `recall` (persist across restarts) |
| — | System state | `system_state` |

`python -m jarvis --abilities` prints the live registry.

---

## How a request flows

```
request
  -> classify            rule-based: privacy, difficulty, web/GUI need
  -> capability gap?     deterministic check; refuse honestly if unsupported
  -> fast path?          unambiguous intents run with no model call at all
  -> route               pick a model from the registry (privacy is a hard limit)
  -> plan                model returns JSON, validated against the registry
  -> guardrails          destructive actions blocked, before any risk gate
  -> risk gate           medium/high abilities ask for confirmation
  -> execute             through the environment that owns the ability
  -> verify              re-observe the machine; the claim is not the evidence
  -> recover             one retry when the failure looks transient
  -> answer + remember
```

Every run prints a **route trace** showing which model was used, why, how long
it took, and how many bytes left the machine.

---

## The design decisions that matter

**Privacy is a constraint, not a preference.** A request mentioning a local path,
filename or machine state is pinned to on-device inference. It is not "preferred"
— no cloud route is even a candidate. The trace reports `0 bytes left the
machine`, and that is literally true: the request went to a loopback socket.

**Verification is independent of the model.** Every ability declares how to check
it. `create_file` is verified by reading the file back; `open_app` by finding the
process; `run_command` by its exit code. A step whose action reported success but
whose verification fails is marked `FAILED`, not `done`.

**Deterministic beats probabilistic where it can.** Classification is regex-based,
and unambiguous intents (`remember X`, `open notepad`, `what is my cpu usage`)
skip the model entirely — **~10 ms instead of ~2,500 ms, at zero cost, with
higher accuracy**. This was not a premature optimisation: llama3:8b planned
`create_folder` for "remember that my demo is today".

**Capability gaps are reported, never substituted.** Asked to send an email — an
ability it does not have — llama3:8b created a file instead. Prompt instructions
did not prevent it, so coverage is now checked deterministically before planning.
Unsupported requests get a refusal that names what is missing.

**Resource limits are modelled honestly.** Ollama offloads weights to the GPU;
LM Studio holds roughly 6 GB of host RAM even when layers are offloaded. Those
are tracked as separate budgets, and a model that will not fit is skipped with a
readable reason rather than allowed to thrash the machine.

**Failure is survivable.** Each request gets an ordered chain of candidate
routes. Losing the cloud costs knowledge coverage but leaves every local
capability working — unlike a cloud-only agent, which is entirely non-functional
offline.

---

## Safety

Two rules hold regardless of configuration, autonomy setting, or model output.
They are enforced in `policy/guardrails.py`, which runs **before** the
confirmation gate and cannot be reached around:

1. **Never delete the user's files.** Deletion abilities are unregistered, and
   destructive command patterns (`rm -rf`, `Remove-Item -Recurse`, `shutil.rmtree`,
   `del /s`, format, `diskpart`, shadow-copy deletion) are refused.
2. **Never damage the OS.** Writes into `C:\Windows`, `Program Files` and other
   system roots are refused, as are firewall/Defender changes, `bcdedit`,
   registry deletion and shutdown commands.

Medium- and high-risk abilities require confirmation (`--yes` to auto-approve,
which is logged). API keys live in a gitignored file, are scrubbed from the event
log by pattern, and are never placed in a prompt body.

---

## Layout

```
jarvis/
  core/          contracts, planner, orchestrator, coverage, fastpath, events
  models/        registry (7 routes), 3 wire adapters, hybrid router
  environments/  local_os, windows_gui, web  - one uniform interface
  abilities/     the capability registry
  policy/        guardrails
  memory/        SQLite store: working, episodic, semantic
  interface/     CLI with the route-trace panel
  tests/         91 tests
  benchmark.py   six-category harness with independent verification
```

**Extension points.** A new capability is one `Ability` entry plus a handler. A
new environment implements `state/capabilities/constraints/act/verify`. A new
model provider is one registry row (and only a new adapter if its wire format is
genuinely new — four providers already share one). None of these require
touching the orchestrator.

---

## Measured results

`python -m jarvis.benchmark`, 16 prompts across the six categories, every pass
independently re-checked against the filesystem / process table afterwards:

| Category | n | Success | Median latency |
|---|---:|---:|---:|
| File operations | 4 | 100% | 8.9 s |
| File search | 2 | 100% | 10.2 s |
| Application launch & control | 2 | 100% | 0.34 s |
| Multi-step composite | 2 | 100% | 10.2 s |
| GUI automation | 2 | 100% | 7.5 s |
| Web / information retrieval | 2 | 100% | 14.1 s |
| Memory | 2 | 100% | 0.02 s |
| **Overall** | **16** | **100%** | **9.4 s** |

Routing: **9 local, 5 deterministic, 2 cloud** — and **13.4 KB total** left the
machine, all of it from the two tasks that genuinely needed the open web. A
cloud-only agent would have transmitted all sixteen requests; a local-only agent
could not have answered the two knowledge questions.

Two caveats stated plainly. Sixteen prompts is a smoke-scale benchmark, not the
280-prompt set used for the ARIA/Mark-XXXIX baselines, so the percentages are not
yet comparable to them — the harness scales by adding rows to `build_tasks()`.
And local planning latency (~9 s) is dominated by llama3:8b on an 8 GB laptop
GPU; the cloud tier plans in ~3 s.

## Honest status

Implemented and verified on real hardware: all six benchmark categories, memory
persistence across process restarts, verification, guardrails, hybrid routing
with failover.

**Not implemented** (declared in the architecture, deliberately not faked): 17 of
the 20 memory types raise `NotImplementedError` rather than silently discarding
data; there is no GUI, no voice, no multi-agent orchestration, no automatic
capability discovery, no vector RAG, no sandboxed execution. `docs/CAPABILITY_ONTOLOGY.md`
labels every capability A–E so nothing reads as more finished than it is.

---

## Testing

```bash
.venv/Scripts/python.exe -m pytest jarvis/tests -q      # 91 tests
.venv/Scripts/python.exe -m jarvis.benchmark            # six-category benchmark
.venv/Scripts/python.exe -m jarvis.benchmark --no-gui   # skip focus-stealing tasks
```

The benchmark does not trust the agent's own report: each task re-checks the
filesystem or process table afterwards, and a task whose evidence is missing is
scored as a failure even if every step claimed success.
