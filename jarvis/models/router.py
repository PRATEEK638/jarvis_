"""Hybrid request-level router.

The baseline study concluded that pure-local and pure-cloud desktop agents each
win different task categories, and recommended request-level hybrid routing as
future work. This module is that router, generalised from two tiers to a
registry of providers.

Design commitments:

1. Classification is rule-based first. Deciding "does this mention a local path"
   is a string problem, not a reasoning problem; an LLM call would add latency
   and a failure mode for no accuracy gain.

2. Privacy is a hard constraint, not a preference. A request referencing local
   paths, filenames or machine state is pinned to on-device inference and its
   content never reaches a remote API - the same structural guarantee the
   local-only baseline had.

3. Cost-awareness is explicit. Among routes that can serve a request, free and
   private ones are preferred; a metered provider with a small prepaid balance
   is ordered last so credits are never spent on work something else can do.

4. Failure is survivable. Each request gets an ordered candidate chain, and a
   route that errors is skipped for the rest of the chain. Losing the cloud
   costs knowledge coverage but leaves every local capability working - unlike
   the cloud-only baseline, which was entirely non-functional offline.

5. Local models are only chosen when the machine can actually host them. A
   runtime holds its weights in host RAM even when layers are GPU-offloaded, so
   a route whose footprint would starve the machine is skipped rather than
   allowed to thrash it.
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

import psutil
import requests

from jarvis.config import settings
from jarvis.core.contracts import (
    Category,
    Classification,
    Difficulty,
    Privacy,
    RouteTrace,
    Tier,
)
from jarvis.core.events import emit
from jarvis.models import registry
from jarvis.models.providers import ModelUnavailable, Provider, for_route
from jarvis.models.registry import ModelRoute

# --- signals ---------------------------------------------------------------

_PATH_RE = re.compile(
    r"([a-zA-Z]:[\\/][^\s\"']*)"           # C:\... or C:/...
    r"|(~[\\/][^\s\"']*)"                   # ~/...
    r"|(%[A-Za-z_]+%)"                      # %USERPROFILE%
    r"|(\b[\w\-. ]+\.(?:txt|md|py|js|ts|json|csv|log|pdf|docx?|xlsx?|pptx?|"
    r"png|jpe?g|zip|tex|ini|yml|yaml|html?|bat|ps1)\b)"   # a filename
)

_LOCAL_WORDS = {
    "file", "files", "folder", "folders", "directory", "desktop", "documents",
    "downloads", "disk", "drive", "my pc", "this pc", "cpu", "ram", "memory usage",
    "process", "processes", "rename", "move", "copy", "create a file",
    "create a folder", "system", "storage", "local", "path", "machine",
}

_WEB_WORDS = {
    "search the web", "google", "look up", "latest", "current", "news", "today",
    "who is", "what is the price", "weather", "release date", "how much does",
    "recent", "trending", "documentation for", "official site",
    "stock", "score", "when did", "when was", "population of",
}

_GUI_WORDS = {
    "click", "button", "window", "type into", "on screen", "menu",
    "checkbox", "dialog", "focus", "foreground", "text box", "textbox",
}

_APP_WORDS = {"open", "launch", "start", "run", "close"}

_COMPOSITE_MARKERS = (
    " then ", " after that", " and then ", "; ", " next, ", " followed by ",
    " also ", " finally ",
)

_MEMORY_WRITE = ("remember", "keep in mind", "note that", "don't forget",
                 "make a note")
_MEMORY_READ = ("what did i", "do you remember", "recall",
                "what do you know about", "what did you", "remind me what")


def _contains_any(text: str, needles) -> bool:
    return any(n in text for n in needles)


def classify(objective: str) -> Classification:
    """Rule-based classification. Cheap, deterministic, explainable."""
    text = objective.lower().strip()
    reasons: list[str] = []

    has_path = bool(_PATH_RE.search(objective))
    if has_path:
        reasons.append("references a local path or filename")

    local_hits = [w for w in _LOCAL_WORDS if w in text]
    web_hits = [w for w in _WEB_WORDS if w in text]
    gui_hits = [w for w in _GUI_WORDS if w in text]
    memory_write = _contains_any(text, _MEMORY_WRITE)
    memory_read = _contains_any(text, _MEMORY_READ)

    # A word like "current" or "today" also appears in "current CPU usage" and
    # "remember my demo is today", so any local or memory reference outranks a
    # web signal.
    needs_web = (bool(web_hits) and not has_path and not local_hits
                 and not memory_write and not memory_read)
    needs_gui = bool(gui_hits)

    composite = _contains_any(text, _COMPOSITE_MARKERS)
    if composite:
        reasons.append("phrased as several sequential actions")

    if has_path or local_hits or memory_write or memory_read:
        privacy = Privacy.LOCAL_ONLY
        if local_hits and not has_path:
            reasons.append(f"mentions local resources ({', '.join(local_hits[:3])})")
    else:
        privacy = Privacy.SHAREABLE

    if needs_web or (composite and needs_gui):
        difficulty = Difficulty.HARD
        if needs_web:
            reasons.append("needs information not on this machine")
    elif composite or needs_gui:
        difficulty = Difficulty.MODERATE
        if needs_gui:
            reasons.append("requires on-screen interaction")
    elif memory_write or memory_read or len(text.split()) <= 6:
        difficulty = Difficulty.TRIVIAL
    else:
        difficulty = Difficulty.SIMPLE

    cats: list[Category] = []
    if memory_write or memory_read:
        cats.append(Category.MEMORY)
    if has_path or local_hits:
        cats.append(Category.FILE_OPS)
    if any(w in text for w in ("find", "search for", "locate", "where is")):
        cats.append(Category.FILE_SEARCH)
    if _contains_any(text, _APP_WORDS):
        cats.append(Category.APP_CONTROL)
    if needs_gui:
        cats.append(Category.GUI_AUTOMATION)
    if needs_web:
        cats.append(Category.WEB_INFO)
    if composite:
        cats.append(Category.COMPOSITE)

    return Classification(
        difficulty=difficulty, privacy=privacy, needs_web=needs_web,
        needs_gui=needs_gui, likely_categories=cats, by="rules",
        rationale="; ".join(reasons) or "short direct request with no local references",
    )


def free_ram_gb() -> float:
    return psutil.virtual_memory().available / 1e9


# Short TTL on purpose. A model loads during the first request that needs it, so
# a long-lived cache says "nothing is resident" for the whole window right after
# a load - which made the resource guard reject a model already sitting in VRAM.
# The probe is two localhost GETs, so refreshing often costs nothing meaningful.
_RESIDENT_TTL_S = 2.0
_resident_cache: tuple[float, set[str]] = (0.0, set())


def invalidate_residency() -> None:
    """Force the next residency probe to hit the runtimes."""
    global _resident_cache
    _resident_cache = (0.0, set())


def resident_models() -> set[str]:
    """Local model ids currently held in memory by LM Studio or Ollama.

    An already-resident model costs nothing further to call, so the footprint
    check below must not penalise it. Cached briefly because this is consulted
    on every routing decision.
    """
    global _resident_cache
    now = time.time()
    if now - _resident_cache[0] < _RESIDENT_TTL_S:
        return _resident_cache[1]
    loaded: set[str] = set()
    try:
        base = settings.LMSTUDIO_URL.rstrip("/").removesuffix("/v1")
        # A short CONNECT timeout matters here: when LM Studio's server is not
        # started (the app can run without it), the port measurably takes
        # ~4 s to fail on this machine at a 3 s combined timeout - so a
        # dashboard polling this every few seconds could never get ahead of
        # it. 0.4 s to connect is generous for localhost; 2 s to read a small
        # JSON response once connected is still generous.
        resp = requests.get(f"{base}/api/v0/models", timeout=(0.4, 2))
        resp.raise_for_status()
        for entry in resp.json().get("data", []):
            if entry.get("state") == "loaded":
                loaded.add(entry.get("id", ""))
    except (requests.RequestException, ValueError):
        pass
    try:
        resp = requests.get(f"{settings.OLLAMA_URL.rstrip('/')}/api/ps", timeout=3)
        resp.raise_for_status()
        for entry in resp.json().get("models", []):
            name = entry.get("name") or entry.get("model") or ""
            if name:
                loaded.add(name)
    except (requests.RequestException, ValueError):
        pass
    _resident_cache = (now, loaded)
    return loaded


def free_vram_gb() -> float | None:
    """Free GPU memory, or None when no NVIDIA GPU is queryable.

    Ollama offloads weights to the GPU, so VRAM rather than host RAM is the
    binding constraint for that runtime. WMI misreports this card's size, so
    nvidia-smi is the authority.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return int(out.stdout.strip().splitlines()[0]) / 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class Router:
    """Builds an ordered candidate chain of providers for each request."""

    # A failing route is benched temporarily, never permanently. Permanent
    # benching was a real defect: one 120 s read-timeout on the only local model
    # took the whole session down, and every later request reported "no model
    # available" on a machine where the model was fine seconds afterwards.
    BREAKER_S = 90.0
    BREAKER_MAX_S = 600.0

    def __init__(self) -> None:
        self._benched: dict[str, float] = {}   # route id -> expiry timestamp
        self._strikes: dict[str, int] = {}

    # -- availability ------------------------------------------------------

    def _usable(self, route: ModelRoute) -> tuple[bool, str]:
        until = self._benched.get(route.id)
        if until is not None:
            remaining = until - time.time()
            if remaining > 0:
                return False, f"benched after a failure, retry in {remaining:.0f}s"
            del self._benched[route.id]   # cooldown elapsed: give it another go
        if route.needs_key() and not route.key():
            return False, "no API key configured"
        if route.tier is Tier.LOCAL and (route.ram_gb or route.vram_gb):
            # An already-resident model costs nothing further to call. One that
            # still has to be loaded must fit twice over: its host-RAM footprint
            # with headroom left for the user's own work, and its weights in GPU
            # memory if that is where the runtime puts them.
            if route.model not in resident_models():
                free = free_ram_gb()
                needed = route.ram_gb + settings.MIN_FREE_RAM_GB
                if route.ram_gb and free < needed:
                    return False, (
                        f"needs ~{route.ram_gb:.1f} GB host RAM but only "
                        f"{free:.1f} GB free (want {needed:.1f} GB)")
                vram = free_vram_gb()
                if route.vram_gb and vram is not None and vram < route.vram_gb:
                    return False, (
                        f"needs ~{route.vram_gb:.1f} GB VRAM but only "
                        f"{vram:.1f} GB free on the GPU")
        provider = for_route(route)
        if not provider.available():
            return False, "not reachable"
        return True, "ready"

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for route in sorted(registry.all_routes(), key=registry.preference_key):
            ok, why = self._usable(route)
            rows.append({
                "id": route.id, "tier": route.tier.value, "model": route.model,
                "available": ok, "why": why, "private": route.private,
                "free": route.free, "quality": route.quality,
            })
        return rows

    # -- decision ----------------------------------------------------------

    def candidates(self, classification: Classification) -> list[ModelRoute]:
        """Ordered routes allowed to serve this request, best first."""
        pool = registry.all_routes()

        # Hard privacy constraint: local-only requests may use on-device
        # inference exclusively.
        if classification.privacy is Privacy.LOCAL_ONLY:
            allowed = [r for r in pool if r.private]
        else:
            allowed = list(pool)

        usable = [r for r in allowed if self._usable(r)[0]]

        hard = (classification.needs_web
                or classification.difficulty is Difficulty.HARD)
        if hard:
            # Capability matters more than thrift here, but metered routes still
            # sort last.
            usable.sort(key=lambda r: (r.last_resort, not r.free, -r.quality))
        else:
            usable.sort(key=registry.preference_key)
        return usable

    def choose(self, classification: Classification,
               trace: RouteTrace) -> tuple[Provider, Tier, str, list[ModelRoute]]:
        chain = self.candidates(classification)
        if not chain:
            if classification.privacy is Privacy.LOCAL_ONLY:
                raise ModelUnavailable(
                    "This request touches local files or machine state, so it may "
                    "only run on an on-device model - and none is currently "
                    "available (check that Ollama or LM Studio is running).")
            raise ModelUnavailable(
                "No model is available: no local runtime is reachable and no "
                "cloud API key is configured.")

        chosen = chain[0]
        provider = for_route(chosen)
        reason = self._explain(chosen, classification, chain)

        # Note when the preferred kind of route was unavailable.
        if classification.needs_web and chosen.tier is Tier.LOCAL:
            trace.degraded = True
        if (classification.privacy is Privacy.LOCAL_ONLY
                and chosen.tier is Tier.CLOUD):
            trace.degraded = True

        return provider, chosen.tier, reason, chain

    def _explain(self, route: ModelRoute, classification: Classification,
                 chain: list[ModelRoute]) -> str:
        bits: list[str] = []
        if classification.privacy is Privacy.LOCAL_ONLY:
            bits.append("request references local files or machine state, so it "
                        "is pinned to on-device inference and nothing is sent off "
                        "the machine")
        elif classification.needs_web:
            bits.append("needs current external information, which the stronger "
                        "remote model handles more reliably")
        elif classification.difficulty is Difficulty.HARD:
            bits.append("planning complexity is high")
        else:
            bits.append("within the local model's reliable range, so it runs "
                        "on-device at no cost")
        if route.metered:
            bits.append("only a metered provider was available")
        if len(chain) > 1:
            bits.append(f"{len(chain) - 1} fallback route(s) behind it")
        return "; ".join(bits)

    # -- failure handling ---------------------------------------------------

    def mark_failed(self, route_id: str, reason: str) -> None:
        """Bench a route temporarily; repeated failures extend the cooldown."""
        strikes = self._strikes.get(route_id, 0) + 1
        self._strikes[route_id] = strikes
        cooldown = min(self.BREAKER_S * (2 ** (strikes - 1)), self.BREAKER_MAX_S)
        self._benched[route_id] = time.time() + cooldown
        emit("route.benched", route=route_id, seconds=round(cooldown),
             strikes=strikes, reason=reason[:200])

    def mark_ok(self, route_id: str) -> None:
        """A success clears the strike count so one bad minute is not permanent."""
        self._strikes.pop(route_id, None)
        self._benched.pop(route_id, None)

    def _row(self, r: ModelRoute) -> dict[str, Any]:
        # _usable() can shell out / hit a local HTTP endpoint per route, so it
        # is evaluated once per route here rather than once per field.
        ok, why = self._usable(r)
        return {"id": r.id, "model": r.model, "available": ok, "why": why}

    def describe(self) -> dict[str, Any]:
        return {
            "local": [self._row(r) for r in registry.local_routes()],
            "cloud": [self._row(r) for r in registry.cloud_routes()],
            "free_ram_gb": round(free_ram_gb(), 2),
        }
