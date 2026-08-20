# Security model

JARVIS runs with the user's own privileges on their own machine, drives real
applications, and executes shell commands. The threat model must therefore be
stated plainly, including what is *not* defended against.

---

## Trust boundaries

| Zone | Trust | Notes |
|---|---|---|
| The user's typed request | trusted | The user is the principal |
| Local model output | **untrusted** | A plan is data to be validated, not an instruction to obey |
| Cloud model output | **untrusted** | Same, plus it crossed a network |
| Fetched web page text | **untrusted** | Attacker-controlled; see the open gap below |
| Filesystem / process state | trusted observation | Read directly, not via a model |
| API keys | secret | Never logged, never in a prompt body |

The central principle: **a model never executes anything directly**. It emits a
JSON plan, which is validated against the ability registry — unknown ability
names and missing required arguments are rejected before execution — and each
step then passes guardrails and the risk gate.

---

## Layer 1 — Guardrails (unconditional)

`policy/guardrails.py`. Evaluated **before** the confirmation gate, so no
autonomy setting, `--yes` flag, prompt wording, or model output can route around
it. Two standing rules set by the system owner:

**1. Never delete the user's files.**
- Deletion abilities are not registered at all (`delete_file`, `delete_folder`,
  `remove_path`, `empty_recycle_bin` are explicitly refused if ever proposed).
- Destructive command patterns are refused: `rm -rf`, `Remove-Item -Recurse`,
  `shutil.rmtree`, `del /s`, `rmdir /s`, `cipher /w`, shadow-copy deletion.
- The benchmark harness holds itself to the same rule: a previous run directory
  is *moved aside*, never removed.

**2. Never damage the operating system.**
- Writes into `C:\Windows`, `Program Files`, `Program Files (x86)`,
  `ProgramData\Microsoft\Windows` and the boot directory are refused.
- Refused outright: `format`, `mkfs`, `dd of=/dev/*`, `diskpart`, `bcdedit`,
  registry deletion, disabling Defender or the firewall, `shutdown` /
  `Restart-Computer`, ownership and ACL changes, account add/delete.

Both are covered by tests in `tests/test_policy.py`, including negative cases so
the guard cannot be weakened silently.

---

## Layer 2 — Risk tiers

Each `Ability` declares a tier:

| Tier | Behaviour | Examples |
|---|---|---|
| `low` | Runs automatically | `read_file`, `find_files`, `system_state`, `web_search` |
| `medium` | Confirmation required | `create_file`, `move_path`, `rename_path`, `click_ui`, `type_text` |
| `high` | Confirmation required, logged prominently | `run_command` |
| `blocked` | Never runs | reserved for guardrail-level refusals |

`--yes` skips the *prompt* only. It is recorded in the event log, and it has no
effect on Layer 1.

---

## Layer 3 — Audit

`data/events.jsonl`, one JSON object per line: model calls (route, latency,
bytes sent), plan creation and rejection, every ability execution, every
verification result, every guardrail refusal, every auto-approval.

Before anything is written, values are scrubbed against credential patterns
(Google `AIza…`, `sk-…`, `sk-ant-…`, `nvapi-…`, and `api_key`/`authorization`/
`bearer` assignments), so a key echoed into an error message does not reach disk.

---

## Secret handling

- Keys live in `jarvis/config/keys.json`, which is gitignored; a committed
  `keys.example.json` documents the shape.
- Environment variables override the file.
- Keys are injected at the HTTP layer only — as a query parameter (Gemini), a
  bearer header (OpenRouter, NVIDIA), or a custom header (Sarvam).
- Keys are never interpolated into a system or user prompt.

**Gap:** no OS keystore (DPAPI / Credential Manager) integration. The file is
protected only by filesystem permissions.

---

## Privacy guarantee, and its exact scope

For a request classified `local_only`, cloud routes are **removed from the
candidate list**, not merely deprioritised. The request text, file paths and file
contents reach only a loopback socket. The route trace reports `0 bytes left the
machine`, and that figure is measured, not asserted: local adapters record
`bytes_sent = 0`, cloud adapters record the serialised payload length.

Scope limits, stated honestly:
- Classification is rule-based. A request that references local data in wording
  the rules do not recognise could be classified `shareable`. The rules are
  deliberately broad (any path, filename, or local-resource word pins it local),
  and local-reference signals override web signals — but this is a heuristic, not
  a proof.
- The web environment necessarily sends the search query to a search engine.
  Requests routed there were classified as not referencing local data.
- Nothing prevents the *user* from pasting sensitive text into a request that is
  then classified shareable.

---

## Open gaps

These are real and unmitigated. They are listed rather than glossed over.

| Gap | Risk | Why not yet addressed |
|---|---|---|
| **No sandboxing** | `run_command` executes with the user's full privileges. Guardrails block known-destructive patterns, but a novel harmful command could pass | Containers/VMs are substantial work and change the deployment story |
| **No prompt-injection defence** | A fetched web page could contain text instructing the model; that text is currently passed to synthesis as evidence | Requires treating retrieved content as data with provenance tags throughout |
| **Guardrails are pattern-based** | Blocklists can be evaded by obfuscation (encoded commands, indirection) | An allowlist of permitted commands would be stronger but far more restrictive |
| **No OS keystore** | Keys sit in a plaintext file | Filesystem permissions only |
| **Single user, no RBAC** | Every capability is available to whoever runs it | Out of scope at this stage |
| **GUI automation is powerful** | `type_text` and `click_ui` can drive any application | Mitigated by requiring a verified foreground window before typing, so keystrokes cannot land in the wrong app |

---

## What "verified" means

Success is never taken from the model's word. Each ability declares a strategy:

| Ability | Verified by |
|---|---|
| `create_file` | Reading the file back and checking the content is present |
| `create_folder` | `is_dir()` on the path |
| `move_path` / `rename_path` | Destination exists **and** source no longer does |
| `open_app` | A matching entry in the real process table |
| `run_command` | Actual exit code |
| `type_text` | Foreground window confirmed before the keystrokes were sent |
| `remember` | Reading the fact back out of SQLite |
| `web_search` / `fetch_page` | Non-zero results / extracted character count |

A step that reports success but fails verification is marked `FAILED`. This is
the property that makes the audit trail meaningful.
