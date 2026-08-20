"""Machine identity and per-machine defaults (vision packs 47, 92, 93).

JARVIS runs on two very different machines and must not be configured for the
smaller one everywhere:

    OMEN 16 laptop     Ryzen 7 7840HS, 16 GB RAM, RTX 4060 Laptop (8 GB)
    Workstation        i7-13700K, 32 GB RAM, RTX 3060 Ti (8 GB)

The difference that actually matters is host RAM, not the GPU. A 7B model in
Ollama holds roughly 6 GB of *host* memory even with layers offloaded, and the
laptop routinely has ~5 GB free once a browser and an IDE are open - which is
why local inference was measured as unusable there and switched off. The
workstation has 32 GB and can hold a local model without starving anything.

So "local models off" is a fact about the laptop, not a fact about JARVIS.
Encoding it per machine means the same checkout behaves correctly on both
without a hand-edited config, which is the point of a device fabric: one
identity, different bodies.

Detection is by measured hardware rather than machine name, so a rebuild, a
rename, or a third machine still lands on the right profile.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

import psutil

# A local 7B needs ~6 GB resident plus headroom for the user's own work.
# Below this, local inference competes with the browser and loses.
LOCAL_MODEL_RAM_GB = 24.0


@dataclass(frozen=True)
class Machine:
    name: str
    role: str                 # "laptop" | "workstation" | "unknown"
    cpu: str
    cpu_threads: int
    ram_gb: float
    gpu: str
    vram_gb: float | None
    has_battery: bool
    edition: str
    local_models_viable: bool
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        vram = f", {self.vram_gb:.0f} GB VRAM" if self.vram_gb else ""
        return (f"{self.name} ({self.role}): {self.cpu_threads} threads, "
                f"{self.ram_gb:.0f} GB RAM, {self.gpu}{vram}")


def _gpu() -> tuple[str, float | None]:
    if not shutil.which("nvidia-smi"):
        return ("no NVIDIA GPU", None)
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return ("NVIDIA GPU (not queryable)", None)
        name, _, mib = out.stdout.strip().splitlines()[0].partition(",")
        return (name.strip(), float(mib.strip()) / 1024)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ("NVIDIA GPU (query failed)", None)


@lru_cache(maxsize=1)
def current() -> Machine:
    ram_gb = psutil.virtual_memory().total / 1e9
    has_battery = psutil.sensors_battery() is not None
    gpu_name, vram = _gpu()
    edition = f"{platform.system()} {platform.release()}"

    # Role from hardware, not from hostname: a battery means portable, and
    # thread count separates a desktop CPU from a mobile one.
    if has_battery:
        role = "laptop"
    elif psutil.cpu_count(logical=True) and psutil.cpu_count(logical=True) >= 16:
        role = "workstation"
    else:
        role = "unknown"

    viable = ram_gb >= LOCAL_MODEL_RAM_GB
    notes = []
    if not viable:
        notes.append(
            f"local inference off: {ram_gb:.0f} GB RAM is below the "
            f"{LOCAL_MODEL_RAM_GB:.0f} GB a 7B model needs alongside normal "
            f"work. Measured on this class of machine, the 8B route produced "
            f"invalid plans while a cloud route did not.")
    else:
        notes.append(
            f"local inference viable: {ram_gb:.0f} GB RAM can hold a 7B model "
            f"without starving the desktop.")
    if vram and vram < 12:
        notes.append(
            f"{vram:.0f} GB VRAM rules out running a vision model alongside a "
            f"language model; they would have to be swapped.")

    return Machine(
        name=socket.gethostname(), role=role,
        cpu=platform.processor() or "unknown",
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        ram_gb=ram_gb, gpu=gpu_name, vram_gb=vram,
        has_battery=has_battery, edition=edition,
        local_models_viable=viable, notes=notes,
    )


def local_enabled() -> bool:
    """Whether local inference should be used on this machine.

    An explicit environment variable always wins, so a deliberate override is
    never second-guessed. Otherwise the answer comes from measured RAM.
    """
    override = os.environ.get("JARVIS_LOCAL_ENABLED")
    if override is not None:
        return override not in ("0", "", "false", "False")
    return current().local_models_viable
