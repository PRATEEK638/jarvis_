"""Post-setup check: what this machine can actually do.

Run by SETUP.bat, and useful on its own after moving to a new machine or
changing keys. Reports what genuinely works rather than what is installed:
a key that is present but rejected is not a working provider.
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    from jarvis.config import machine, settings

    m = machine.current()
    print(f"  Machine: {m.describe()}")
    print(f"  Edition: {m.edition}")
    for note in m.notes:
        print(f"    - {note}")

    problems = 0

    # -- model routes
    keyed = [p for p in settings.CLOUD_PROVIDERS if settings.get_key(p)]
    if keyed:
        pools = ", ".join(
            f"{p}({settings.key_pool_size(p)})" if settings.key_pool_size(p) > 1
            else p for p in keyed)
        print(f"  Cloud providers configured: {pools}")
    else:
        print("  [!] No cloud API keys found in jarvis/config/keys.json.")
        if not m.local_models_viable:
            print("      This machine also cannot run local models, so JARVIS "
                  "will only manage deterministic requests.")
            problems += 1

    print(f"  Local inference: "
          f"{'enabled' if bool(settings.LOCAL_ENABLED) else 'disabled'}")

    # -- environments
    from jarvis.core.orchestrator import Orchestrator
    orch = Orchestrator(on_progress=lambda _m: None)
    try:
        for env_id, env in orch.environments.items():
            try:
                state = env.state()
                ok = state.get("available") is not False
                print(f"  {'[ok]' if ok else '[--]'} environment: {env_id}"
                      + ("" if ok else f"  ({state.get('why', 'unavailable')})"))
            except Exception as exc:      # noqa: BLE001
                print(f"  [X] environment {env_id} failed: {exc}")
                problems += 1

        from jarvis.abilities import registry as abilities
        from jarvis.skills import registry as skills
        print(f"  {len(abilities.all_abilities())} abilities, "
              f"{len(skills.all_skills())} skill playbooks")
    finally:
        orch.close()

    # -- voice
    try:
        from jarvis.interface.voice import status as vstatus
        v = vstatus()
        mic = v["microphone"]["available"]
        stt = v["transcription"]["available"]
        print(f"  {'[ok]' if mic and stt else '[--]'} voice: "
              f"mic {'yes' if mic else 'no'}, "
              f"transcription {'yes' if stt else 'no key'}")
    except Exception as exc:              # noqa: BLE001
        print(f"  [--] voice unavailable: {exc}")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
