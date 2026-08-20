"""Hold the machine awake while long work runs.

Uses the OS's own "I am busy" signal (SetThreadExecutionState) rather than
changing the user's power plan. That matters: the request is temporary, and a
process-scoped lock releases automatically when this exits or is killed, so it
cannot leave the laptop permanently unable to sleep.

    python -m jarvis.keepawake            # hold until Ctrl-C
    python -m jarvis.keepawake --minutes 90
"""

from __future__ import annotations

import argparse
import ctypes
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def hold(*, keep_display: bool = False) -> bool:
    """Ask Windows not to sleep. Returns False if the call was refused."""
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if keep_display:
        flags |= ES_DISPLAY_REQUIRED
    return ctypes.windll.kernel32.SetThreadExecutionState(flags) != 0


def release() -> None:
    """Restore normal power behaviour."""
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def main() -> int:
    parser = argparse.ArgumentParser(prog="jarvis.keepawake")
    parser.add_argument("--minutes", type=float, default=0,
                        help="release after this long (0 = until stopped)")
    parser.add_argument("--display", action="store_true",
                        help="also keep the screen on")
    args = parser.parse_args()

    if not hold(keep_display=args.display):
        print("could not acquire the keep-awake lock")
        return 1
    label = f"{args.minutes:g} min" if args.minutes else "until stopped"
    print(f"sleep blocked ({label}). Screen "
          f"{'on' if args.display else 'may still turn off'}.")
    deadline = time.time() + args.minutes * 60 if args.minutes else None
    try:
        while deadline is None or time.time() < deadline:
            # Re-assert periodically: some drivers reset the request, and a
            # single call at startup has been observed to lapse on long runs.
            hold(keep_display=args.display)
            time.sleep(30)
    except KeyboardInterrupt:
        pass
    finally:
        release()
        print("keep-awake released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
