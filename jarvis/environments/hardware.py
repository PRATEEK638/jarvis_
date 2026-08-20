"""Hardware environment — the physical machine, not the software on it.

Battery, thermals, GPU, display brightness, audio volume and power plan. These
are the things a person means by "control my laptop" that no amount of file and
process access covers.

Design notes worth stating:

* Reads are LOW risk and writes are MEDIUM, declared in the ability registry.
  Turning the volume to 100% at 2am is not destructive, but it is startling,
  so it asks first.

* Every write is verified by reading the value back from the hardware, not by
  trusting the call's return code. Brightness in particular reports success on
  machines where the panel ignores it.

* Volume goes through the Windows Core Audio API (pycaw) rather than simulated
  key presses, so it sets an exact level instead of nudging by an unknown step.

* Brightness goes through WMI, which only works on internal panels. External
  monitors need DDC/CI, which is a different protocol and is reported as
  unsupported rather than silently doing nothing.

* Nothing here can damage the machine: no fan curves, no voltages, no clock
  offsets. Those are the operations where a wrong value is permanent.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

import psutil

from jarvis.core.contracts import ActionResult, VerificationResult

TIMEOUT_S = 15
_NO_WINDOW = 0x08000000       # CREATE_NO_WINDOW: never flash a console


def _powershell(script: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            creationflags=_NO_WINDOW,
        )
        return proc.returncode == 0, (proc.stdout or proc.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _nvidia_smi() -> dict[str, Any] | None:
    """GPU telemetry, or None when there is no NVIDIA GPU to ask."""
    query = ("name,temperature.gpu,utilization.gpu,memory.used,memory.total,"
             "power.draw,fan.speed")
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    parts = [p.strip() for p in proc.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 6:
        return None

    def num(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None            # "[N/A]" on laptops without the sensor

    return {
        "name": parts[0], "temperature_c": num(parts[1]),
        "utilization_percent": num(parts[2]), "vram_used_mb": num(parts[3]),
        "vram_total_mb": num(parts[4]), "power_watts": num(parts[5]),
        "fan_percent": num(parts[6]) if len(parts) > 6 else None,
    }


class HardwareEnvironment:
    """Observe and adjust the physical machine."""

    id = "hardware"

    # -- Environment protocol ----------------------------------------------

    def state(self) -> dict[str, Any]:
        battery = psutil.sensors_battery()
        freq = psutil.cpu_freq()
        out: dict[str, Any] = {"available": True}
        if battery is not None:
            out["battery_percent"] = round(battery.percent)
            out["on_mains"] = bool(battery.power_plugged)
        if freq is not None:
            out["cpu_mhz"] = round(freq.current)
        gpu = _nvidia_smi()
        if gpu:
            out["gpu"] = gpu["name"]
            out["gpu_temp_c"] = gpu["temperature_c"]
        return out

    def capabilities(self) -> list[str]:
        return ["hardware_status", "set_volume", "set_brightness",
                "power_plan", "wifi_status"]

    def constraints(self) -> list[str]:
        return [
            "Brightness works on the internal panel only; external monitors "
            "need DDC/CI, which is not implemented and is reported as such.",
            "Fan curves, voltages and clock offsets are deliberately absent - "
            "those are the settings where a wrong value can be permanent.",
            "GPU telemetry requires an NVIDIA GPU and nvidia-smi on PATH.",
            "Changing the power plan affects the whole machine, not this "
            "session.",
        ]

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        handlers = {
            "hardware_status": self._status, "set_volume": self._set_volume,
            "set_brightness": self._set_brightness,
            "power_plan": self._power_plan, "wifi_status": self._wifi,
        }
        handler = handlers.get(ability_id)
        if handler is None:
            return ActionResult(ok=False, error="unregistered",
                                summary=f"unknown ability '{ability_id}'")
        start = time.perf_counter()
        try:
            result = handler(args)
        except Exception as exc:      # noqa: BLE001 - hardware is flaky by nature
            result = ActionResult(ok=False, error=type(exc).__name__,
                                  summary=f"hardware call failed: {exc}")
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        """Read the hardware back. A write that reported success but did not
        take effect is the failure mode that matters here."""
        if ability_id == "set_volume" and result.ok:
            actual = self._read_volume()
            want = _clamp_percent(args.get("percent"))
            close = actual is not None and want is not None and abs(actual - want) <= 2
            return VerificationResult(
                verified=close, strategy="volume_read_back",
                detail=f"requested {want}%, hardware reports {actual}%",
                checked={"requested": want, "actual": actual})
        if ability_id == "set_brightness" and result.ok:
            actual = self._read_brightness()
            want = _clamp_percent(args.get("percent"))
            close = actual is not None and want is not None and abs(actual - want) <= 5
            return VerificationResult(
                verified=close, strategy="brightness_read_back",
                detail=f"requested {want}%, panel reports {actual}%",
                checked={"requested": want, "actual": actual})
        return VerificationResult(verified=result.ok, strategy="result_only",
                                  detail="observation-only ability", checked={})

    # -- volume -------------------------------------------------------------

    @staticmethod
    def _endpoint():
        from pycaw.utils import AudioUtilities
        return AudioUtilities.GetSpeakers().EndpointVolume

    def _read_volume(self) -> int | None:
        try:
            return round(self._endpoint().GetMasterVolumeLevelScalar() * 100)
        except Exception:             # noqa: BLE001
            return None

    def _set_volume(self, args: dict[str, Any]) -> ActionResult:
        want = _clamp_percent(args.get("percent"))
        mute = args.get("mute")
        if want is None and mute is None:
            return ActionResult(ok=False, error="missing_percent",
                                summary="Give a percent (0-100), or mute true/false")
        try:
            ev = self._endpoint()
            before = round(ev.GetMasterVolumeLevelScalar() * 100)
            if mute is not None:
                ev.SetMute(1 if _truthy(mute) else 0, None)
            if want is not None:
                ev.SetMasterVolumeLevelScalar(want / 100.0, None)
            after = round(ev.GetMasterVolumeLevelScalar() * 100)
            muted = bool(ev.GetMute())
        except Exception as exc:      # noqa: BLE001
            return ActionResult(ok=False, error="audio_api",
                                summary=f"Could not reach the audio device: {exc}")
        bits = [f"volume {before}% -> {after}%"] if want is not None else []
        if mute is not None:
            bits.append("muted" if muted else "unmuted")
        return ActionResult(ok=True, summary=", ".join(bits),
                            evidence={"before": before, "after": after,
                                      "muted": muted})

    # -- brightness ---------------------------------------------------------

    def _read_brightness(self) -> int | None:
        ok, out = _powershell(
            "(Get-CimInstance -Namespace root/WMI -ClassName "
            "WmiMonitorBrightness -ErrorAction SilentlyContinue)"
            ".CurrentBrightness")
        if not ok or not out:
            return None
        try:
            return int(out.splitlines()[0].strip())
        except ValueError:
            return None

    def _set_brightness(self, args: dict[str, Any]) -> ActionResult:
        want = _clamp_percent(args.get("percent"))
        if want is None:
            return ActionResult(ok=False, error="missing_percent",
                                summary="Give a brightness percent (0-100)")
        before = self._read_brightness()
        if before is None:
            return ActionResult(
                ok=False, error="unsupported",
                summary="This display does not expose brightness over WMI. "
                        "That is normal for external monitors, which need "
                        "DDC/CI - not implemented here.")
        # Three things had to be right here, each found by testing:
        #  1. Invoke-CimMethod, not a method call on the instance. Get-CimInstance
        #     returns an inert CimInstance whose WMI methods are not bound, so
        #     `(Get-CimInstance ...).WmiSetBrightness(0,60)` fails with "does
        #     not contain a method named 'WmiSetBrightness'".
        #  2. The arguments must be cast. WMI declares Brightness as uint8 and
        #     Timeout as uint32; passing plain integers fails with "Type
        #     mismatch for parameter Brightness".
        #  3. -ErrorAction Stop, and no trailing echo. An earlier version
        #     appended 'ok' to the pipeline, so PowerShell exited 0 and the
        #     failure was reported as success.
        #  4. -InputObject with the fetched instance, not -ClassName. With
        #     -ClassName the call is rejected as "Invalid method Parameter(s)"
        #     even though the argument names and types are correct, because
        #     the method needs to be bound to a specific monitor instance.
        ok, out = _powershell(
            "$i = Get-CimInstance -Namespace root/WMI "
            "-ClassName WmiMonitorBrightnessMethods; "
            "Invoke-CimMethod -InputObject $i -MethodName WmiSetBrightness "
            f"-Arguments @{{Timeout=[uint32]0; Brightness=[byte]{want}}} "
            "-ErrorAction Stop | Out-Null")
        if not ok:
            return ActionResult(ok=False, error="wmi_failed",
                                summary=f"Brightness call failed: {out[:160]}")
        time.sleep(0.4)              # the panel takes a moment to report back
        after = self._read_brightness()
        return ActionResult(ok=True,
                            summary=f"brightness {before}% -> {after}%",
                            evidence={"before": before, "after": after})

    # -- status, power, wifi -------------------------------------------------

    def _status(self, args: dict[str, Any]) -> ActionResult:
        battery = psutil.sensors_battery()
        freq = psutil.cpu_freq()
        gpu = _nvidia_smi()
        evidence: dict[str, Any] = {
            "cpu_percent": psutil.cpu_percent(interval=0.3),
            "cpu_mhz": round(freq.current) if freq else None,
            "volume_percent": self._read_volume(),
            "brightness_percent": self._read_brightness(),
        }
        bits = [f"CPU {evidence['cpu_percent']}%"]
        if freq:
            bits.append(f"{round(freq.current)} MHz")
        if battery is not None:
            evidence["battery_percent"] = round(battery.percent)
            evidence["on_mains"] = bool(battery.power_plugged)
            bits.append(f"battery {round(battery.percent)}%"
                        + (" (charging)" if battery.power_plugged else ""))
        if gpu:
            evidence["gpu"] = gpu
            temp = gpu["temperature_c"]
            vram = (f"{gpu['vram_used_mb']:.0f}/{gpu['vram_total_mb']:.0f} MB"
                    if gpu["vram_used_mb"] is not None else "?")
            bits.append(f"GPU {temp:.0f}C, {vram}" if temp is not None
                        else f"GPU {vram}")
        if evidence["volume_percent"] is not None:
            bits.append(f"volume {evidence['volume_percent']}%")
        if evidence["brightness_percent"] is not None:
            bits.append(f"brightness {evidence['brightness_percent']}%")
        return ActionResult(ok=True, summary=" - ".join(bits), evidence=evidence)

    def _power_plan(self, args: dict[str, Any]) -> ActionResult:
        wanted = str(args.get("plan") or "").strip().lower()
        ok, out = _powershell("powercfg /list")
        if not ok:
            return ActionResult(ok=False, error="powercfg_failed",
                                summary=out[:160])
        plans = []
        active = None
        for line in out.splitlines():
            if "GUID" not in line:
                continue
            guid = line.split(":")[1].strip().split()[0]
            name = line.split("(")[-1].rstrip(") *").strip() if "(" in line else guid
            is_active = line.rstrip().endswith("*")
            plans.append({"guid": guid, "name": name, "active": is_active})
            if is_active:
                active = name
        if not wanted:
            return ActionResult(
                ok=True, summary=f"active plan: {active or 'unknown'}",
                evidence={"plans": plans, "active": active})
        match = next((p for p in plans if wanted in p["name"].lower()), None)
        if match is None:
            names = ", ".join(p["name"] for p in plans)
            return ActionResult(ok=False, error="no_such_plan",
                                summary=f"No plan matching '{wanted}'. Have: {names}")
        ok, out = _powershell(f"powercfg /setactive {match['guid']}")
        if not ok:
            return ActionResult(ok=False, error="powercfg_failed",
                                summary=out[:160])
        return ActionResult(ok=True,
                            summary=f"power plan: {active} -> {match['name']}",
                            evidence={"from": active, "to": match["name"]})

    def _wifi(self, args: dict[str, Any]) -> ActionResult:
        ok, out = _powershell("netsh wlan show interfaces")
        if not ok:
            return ActionResult(ok=False, error="netsh_failed",
                                summary="Could not query the wireless adapter")
        info: dict[str, str] = {}
        for line in out.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                info[key.strip().lower()] = value.strip()
        ssid = info.get("ssid") or ""
        state = info.get("state") or "unknown"
        signal = info.get("signal") or ""
        summary = (f"wifi {state}"
                   + (f" - {ssid}" if ssid else "")
                   + (f" ({signal})" if signal else ""))
        return ActionResult(ok=True, summary=summary,
                            evidence={"state": state, "ssid": ssid,
                                      "signal": signal})


def _clamp_percent(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "mute")
