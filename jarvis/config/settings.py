"""Configuration and secret loading.

Keys live in config/keys.json (gitignored). They are injected at the HTTP layer
only and never written to the event log or into a prompt body.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # .../jarvis
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"
KEYS_FILE = CONFIG_DIR / "keys.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

EVENT_LOG = DATA_DIR / "events.jsonl"
TRACE_LOG = DATA_DIR / "traces.jsonl"
DB_FILE = DATA_DIR / "jarvis.db"

# Local tier -----------------------------------------------------------------
OLLAMA_URL = os.environ.get("JARVIS_OLLAMA_URL", "http://localhost:11434")
LOCAL_MODEL = os.environ.get("JARVIS_LOCAL_MODEL", "llama3:8b")
LMSTUDIO_URL = os.environ.get("JARVIS_LMSTUDIO_URL", "http://localhost:1234/v1")
EMBED_MODEL = os.environ.get("JARVIS_EMBED_MODEL",
                             "text-embedding-nomic-embed-text-v1.5")

# Whether local inference is used is decided per machine, not globally: the
# laptop has 16 GB and cannot hold a 7B model alongside normal work, while the
# workstation has 32 GB and can. See config/machine.py for the measurement.
# JARVIS_LOCAL_ENABLED=1/0 overrides it explicitly.
def _local_enabled() -> bool:
    from jarvis.config import machine
    return machine.local_enabled()


class _LazyLocalFlag:
    """Evaluated on use so importing settings never probes the hardware."""

    def __bool__(self) -> bool:
        return _local_enabled()


LOCAL_ENABLED = _LazyLocalFlag()

# Cloud tier -----------------------------------------------------------------
CLOUD_PROVIDERS = ("gemini", "openrouter", "nvidia", "sarvam", "openai", "anthropic")
GEMINI_MODEL = os.environ.get("JARVIS_GEMINI_MODEL", "gemini-3.5-flash")
OPENROUTER_MODEL = os.environ.get(
    "JARVIS_OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
NVIDIA_MODEL = os.environ.get("JARVIS_NVIDIA_MODEL",
                              "nvidia/nemotron-3-ultra-550b-a55b")
SARVAM_MODEL = os.environ.get("JARVIS_SARVAM_MODEL",
                              "sarvam-105b-conversations")
# Gemini Live native-audio model used by the voice interface.
GEMINI_TRANSCRIBE_MODEL = os.environ.get(
    "JARVIS_GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-flash")
GEMINI_VOICE_MODEL = os.environ.get(
    "JARVIS_GEMINI_VOICE_MODEL",
    "gemini-2.5-flash-native-audio-preview-09-2025")
# Prebuilt Live voices: Charon, Puck, Kore, Fenrir, Aoede.
VOICE_NAME = os.environ.get("JARVIS_VOICE_NAME", "Charon")

OPENAI_MODEL = os.environ.get("JARVIS_OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.environ.get("JARVIS_ANTHROPIC_MODEL", "claude-sonnet-4-5")

# Local runtimes hold their weights in host RAM even when layers are offloaded to
# the GPU, so a model is only pinned when there is genuine headroom. Pinning at
# the wrong moment has previously starved the machine.
MIN_FREE_RAM_GB = float(os.environ.get("JARVIS_MIN_FREE_RAM_GB", "2.5"))

# Behaviour ------------------------------------------------------------------
# A planner that has not answered in 90 s is not going to produce a useful plan;
# waiting longer only delays failover to a route that will.
MODEL_TIMEOUT_S = int(os.environ.get("JARVIS_MODEL_TIMEOUT", "90"))
WEB_TIMEOUT_S = 20
MAX_STEPS = 8

_keys_cache: dict[str, list[str]] | None = None
# Which key in each provider's pool is currently active. Free tiers are capped
# per key, so several keys for one provider are rotated rather than merged.
_active_index: dict[str, int] = {}


def _load_keys() -> dict[str, list[str]]:
    """Load credentials. A provider may hold one key or a pool of them."""
    global _keys_cache
    if _keys_cache is not None:
        return _keys_cache
    data: dict[str, list[str]] = {}
    if KEYS_FILE.exists():
        try:
            raw = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
            for name, value in raw.items():
                if name.startswith("_"):
                    continue
                if isinstance(value, list):
                    pool = [str(v).strip() for v in value if str(v).strip()]
                elif value:
                    pool = [str(value).strip()]
                else:
                    pool = []
                if pool:
                    data[str(name)] = pool
        except (json.JSONDecodeError, OSError):
            data = {}
    # Environment variables win over the file.
    for name, env in (
        ("gemini", "GEMINI_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("nvidia", "NVIDIA_API_KEY"),
        ("sarvam", "SARVAM_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
    ):
        if os.environ.get(env):
            data[name] = [os.environ[env]]
    _keys_cache = data
    return data


def get_key(provider: str) -> str | None:
    """The currently active key for a provider."""
    pool = _load_keys().get(provider)
    if not pool:
        return None
    return pool[_active_index.get(provider, 0) % len(pool)]


def key_pool_size(provider: str) -> int:
    return len(_load_keys().get(provider, []))


def rotate_key(provider: str) -> bool:
    """Advance to the next key in a provider's pool.

    Free tiers are rate-limited per key, so when one is exhausted the next can
    carry on. Returns False when the pool holds only one key or has been fully
    cycled, which tells the caller to fail over to a different provider instead.
    """
    pool = _load_keys().get(provider, [])
    if len(pool) <= 1:
        return False
    current = _active_index.get(provider, 0)
    if current + 1 >= len(pool):
        return False          # exhausted every key in the pool
    _active_index[provider] = current + 1
    return True


def active_key_index(provider: str) -> int:
    return _active_index.get(provider, 0)


def active_cloud_provider() -> str | None:
    """First configured cloud provider, or None if the cloud tier is unavailable."""
    keys = _load_keys()
    for p in CLOUD_PROVIDERS:
        if keys.get(p):
            return p
    return None


def reload_keys() -> None:
    global _keys_cache
    _keys_cache = None
    _active_index.clear()
