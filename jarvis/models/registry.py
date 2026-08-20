"""Model registry.

Every model JARVIS can call is one entry here, described by its capabilities,
where it runs, what it costs and how reliable it is. The router reads this table;
it does not know about individual vendors. Adding a provider is a new entry plus,
at most, a new wire adapter — six providers currently share three adapters.

Wire protocols in use:
  ollama         - Ollama's native /api/generate (local)
  openai_compat  - /v1/chat/completions (LM Studio, OpenRouter, NVIDIA NIM, Sarvam)
  gemini         - Google's generateContent

Ordering is cost- and privacy-aware, not quality-only: a free local model that
can do the job is preferred over a paid remote one, and a metered provider with a
small credit balance is placed last so it is never spent on easy work.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from jarvis.config import settings
from jarvis.core.contracts import Tier


class Wire(str, Enum):
    OLLAMA = "ollama"
    OPENAI_COMPAT = "openai_compat"
    GEMINI = "gemini"


class Auth(str, Enum):
    NONE = "none"                # local servers need no key
    BEARER = "bearer"            # Authorization: Bearer <key>
    QUERY = "query"              # ?key=<key>
    HEADER = "header"            # a custom header, named by auth_header


class ModelRoute(BaseModel):
    """One callable model, with everything the router needs to decide."""

    id: str
    provider: str                     # human label: ollama, lmstudio, openrouter...
    wire: Wire
    tier: Tier
    model: str
    base_url: str = ""
    auth: Auth = Auth.NONE
    key_name: str = ""                # which entry in keys.json
    auth_header: str = ""             # used when auth is HEADER

    # Selection signals
    quality: int = 50                 # rough capability rank, 0-100
    supports_json: bool = True        # can be constrained to JSON output
    context_tokens: int = 8_192
    ram_gb: float = 0.0               # host RAM the local runtime holds resident
    vram_gb: float = 0.0              # GPU memory the weights occupy when offloaded
    private: bool = False             # True only when inference is on-device
    free: bool = True
    metered: bool = False             # consumes a small prepaid balance
    last_resort: bool = False         # never chosen unless nothing else works
    notes: str = ""

    def needs_key(self) -> bool:
        return self.auth is not Auth.NONE

    def key(self) -> str | None:
        return settings.get_key(self.key_name) if self.key_name else None


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

ROUTES: list[ModelRoute] = [
    # -- local, on-device: free, private, no network -----------------------
    ModelRoute(
        id="ollama:llama3-8b", provider="ollama", wire=Wire.OLLAMA,
        tier=Tier.LOCAL, model="llama3:8b",
        base_url=settings.OLLAMA_URL, auth=Auth.NONE,
        quality=45, ram_gb=1.0, vram_gb=5.5, private=True, free=True,
        context_tokens=8_192,
        notes="Llama 3.0: no native tool-calling, so plans are produced by "
              "JSON-constrained prompting.",
    ),
    ModelRoute(
        id="lmstudio:qwythos-9b", provider="lmstudio", wire=Wire.OPENAI_COMPAT,
        tier=Tier.LOCAL, model="qwythos-9b-claude-mythos-5-1m",
        base_url=settings.LMSTUDIO_URL, auth=Auth.NONE,
        quality=58, ram_gb=6.5, vram_gb=6.0, private=True, free=True,
        context_tokens=32_768,
        notes="Qwen3.5-based 9B; stronger planner than llama3:8b but needs "
              "roughly 7 GB resident, so it is skipped under RAM pressure.",
    ),
    ModelRoute(
        id="lmstudio:gemma-4-e4b", provider="lmstudio", wire=Wire.OPENAI_COMPAT,
        tier=Tier.LOCAL, model="google/gemma-4-e4b",
        base_url=settings.LMSTUDIO_URL, auth=Auth.NONE,
        quality=54, ram_gb=6.0, vram_gb=5.5, private=True, free=True,
        context_tokens=16_384,
        notes="Gemma 4 7.5B, local.",
    ),

    # -- cloud, free tier --------------------------------------------------
    ModelRoute(
        id="gemini:2.5-flash", provider="gemini", wire=Wire.GEMINI,
        tier=Tier.CLOUD, model=settings.GEMINI_MODEL,
        auth=Auth.QUERY, key_name="gemini",
        quality=88, free=True, context_tokens=1_000_000,
        notes="Primary cloud tier: strong planning, native JSON mode, "
              "generous free quota.",
    ),
    ModelRoute(
        id="openrouter:free", provider="openrouter", wire=Wire.OPENAI_COMPAT,
        tier=Tier.CLOUD, model=settings.OPENROUTER_MODEL,
        base_url="https://openrouter.ai/api/v1", auth=Auth.BEARER,
        key_name="openrouter",
        quality=80, free=True, context_tokens=131_072,
        notes="Free-tier open-weight models; daily request cap.",
    ),
    ModelRoute(
        id="nvidia:nim", provider="nvidia", wire=Wire.OPENAI_COMPAT,
        tier=Tier.CLOUD, model=settings.NVIDIA_MODEL,
        base_url="https://integrate.api.nvidia.com/v1", auth=Auth.BEARER,
        key_name="nvidia",
        quality=86, free=True, context_tokens=131_072,
        notes="NVIDIA NIM free tier running Nemotron 3 Ultra 550B-A55B; "
              "measured ~1.1 s, the fastest cloud route here.",
    ),

    # -- cloud, metered: kept last so credits are never spent on easy work --
    ModelRoute(
        id="sarvam:105b", provider="sarvam", wire=Wire.OPENAI_COMPAT,
        tier=Tier.CLOUD, model=settings.SARVAM_MODEL,
        base_url="https://api.sarvam.ai/v1", auth=Auth.HEADER,
        auth_header="api-subscription-key", key_name="sarvam",
        quality=72, free=False, metered=True, last_resort=True,
        context_tokens=32_768,
        notes="Small prepaid credit balance: ordered last and never used for "
              "work another route can do.",
    ),
]

_BY_ID = {r.id: r for r in ROUTES}


def get(route_id: str) -> ModelRoute | None:
    return _BY_ID.get(route_id)


def all_routes() -> list[ModelRoute]:
    return list(ROUTES)


def configured() -> list[ModelRoute]:
    """Routes that have whatever credential they require (no liveness probe)."""
    return [r for r in ROUTES if not r.needs_key() or r.key()]


def local_routes() -> list[ModelRoute]:
    return [r for r in configured() if r.tier is Tier.LOCAL]


def cloud_routes() -> list[ModelRoute]:
    return [r for r in configured() if r.tier is Tier.CLOUD]


def preference_key(route: ModelRoute) -> tuple:
    """Sort key: never-metered first, then privacy, then quality.

    Used when several routes could serve a request. `last_resort` routes sort to
    the very end regardless of quality.
    """
    return (route.last_resort, not route.free, not route.private, -route.quality)
