# JARVIS Desktop — SDLC record

Living document. Updated as each phase completes, not written up front and abandoned.

## 1. Requirements

### Why a native application, not a web page
The browser control interface built earlier worked, but is the wrong container for
this product, for reasons that are structural rather than cosmetic:

| Requirement | Browser | Native (PyQt6) |
|---|---|---|
| Continuous microphone with barge-in | Sandboxed; needs a permission gesture per session; `ScriptProcessorNode` is deprecated and adds latency | Direct `sounddevice` stream, already measured working |
| Low-latency audio playback | `AudioContext` scheduling drift, no true interrupt | Direct `RawOutputStream`, instant flush on barge-in |
| Always-available HUD | A tab among tabs | Frameless always-on-top overlay |
| Screen / window awareness | Impossible from a page | Already implemented (`windows_gui` environment) |
| Feels like a product | A localhost URL | An application you launch |

The Gemini Live *native* transport was already verified end to end earlier
(handshake, transcription, real tool execution, spoken reply). The browser bridge
was the part fighting the platform. So the native path becomes primary.

### Functional requirements
- **FR1** Voice-first: press-and-hold or click-to-toggle a live conversation with
  barge-in; visible listening / thinking / speaking / acting states.
- **FR2** Text entry for when speaking is inappropriate.
- **FR3** Live execution trace: every plan, tool call, and verification visible as
  it happens — sourced from the real event bus, never simulated.
- **FR4** System telemetry (CPU/RAM/disk) and model-registry health at a glance.
- **FR5** Risk confirmation surfaced in-app; nothing medium/high-risk runs unapproved.
- **FR6** Same brain for voice and text — one Orchestrator, one guardrail path.
- **FR7** Interrupt: stop speech and cancel the current turn immediately.

### Non-functional requirements
- **NFR1** UI thread never blocks. All model/tool/audio work is off-thread.
- **NFR2** A failure in the UI must not corrupt or bypass the guardrail layer.
- **NFR3** Start-up under ~3 s to an interactive window.
- **NFR4** No fabricated state anywhere in the interface.

## 2. Design

```
              PyQt6 main thread (UI only)
                        |
                signals / slots  (thread-safe marshalling)
                        |
   +--------------------+---------------------+
   |                                          |
GoalWorker (QThread)                  VoiceWorker (QThread)
   |                                          |
Orchestrator.run()                    VoiceSession (asyncio loop)
   |                                          |
   +--------------------+---------------------+
                        |
        abilities / environments / guardrails / memory
                        |
                 events.emit()  -> EventRelay -> UI
```

Key decisions:
- **One Orchestrator instance** shared by both workers, constructed on the UI
  thread at start-up. Goals are serialized through a single worker so working
  memory is never mutated concurrently.
- **`events.subscribe()`** (already built for the web UI) is reused verbatim as
  the UI's live feed — the desktop app needs no new instrumentation.
- **Voice audio levels** are tapped from the existing mic/speaker streams via
  optional callbacks, so the visualiser shows real amplitude, not an animation
  pretending to be one.
- **Confirmation** blocks the orchestrator thread on a `threading.Event`, exactly
  as the web bridge does; the UI thread sets it. Same policy, different surface.

## 3. Implementation phases
1. Audio-level hooks in the existing `VoiceSession` (additive, native path only).
2. Theme + reusable HUD widgets (reactor, waveform, telemetry, trace).
3. Threading bridge (workers + event relay).
4. Main HUD window assembling the above.
5. Entry point + launcher wiring.

## 4. Test plan
- Widget smoke test: construct every widget offscreen, verify no paint exceptions.
- Bridge test: submit a goal headlessly through the worker, assert the finished
  signal carries a real TaskRecord.
- Guardrail test: confirm the desktop confirmation path still refuses a blocked
  command (reuses `test_orchestrator.py` contract).
- Manual: launch, speak, observe state changes and a real action executing.

## 5. Status
Phase 1-5 implemented. See git history for per-phase commits.
