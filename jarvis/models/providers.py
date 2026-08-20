"""Wire adapters.

Three adapters serve every model in the registry:

  OllamaProvider        Ollama's native /api/generate
  OpenAICompatProvider  /v1/chat/completions - LM Studio, OpenRouter,
                        NVIDIA NIM and Sarvam all speak this, differing only in
                        base URL and how the key is presented
  GeminiProvider        Google's generateContent

`for_route()` builds the right adapter from a registry entry, so the router
never branches on vendor.

Privacy accounting is literal: local adapters record bytes_sent = 0 because the
request goes to a loopback socket, and cloud adapters record the real serialised
payload size. That number is what makes the privacy claim measurable instead of
rhetorical.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from jarvis.config import settings
from jarvis.core.contracts import ModelCall, Tier
from jarvis.core.events import emit
from jarvis.models.registry import Auth, ModelRoute, Wire


class ModelError(RuntimeError):
    """Model produced output we could not use."""


class ModelUnavailable(ModelError):
    """Provider unreachable or unconfigured - the router should fall back."""


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first well-formed JSON object out of raw model output.

    Small local models wrap JSON in prose or code fences even when instructed
    not to, so bracket matching is more reliable than json.loads on the whole
    string.
    """
    if not text or not text.strip():
        raise ModelError("empty model output")
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        raise ModelError(f"no JSON object in output: {text[:200]}")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ModelError(f"malformed JSON: {exc}") from exc
    raise ModelError("unterminated JSON object in model output")


_QUOTA_MARKERS = (
    "429", "quota", "rate limit", "rate-limit", "too many requests",
    "insufficient", "credit", "exceeded", "free-models-per-day", "402",
)


def _is_quota_error(exc: Exception) -> bool:
    """Does this failure look like an exhausted key rather than a dead service?"""
    text = str(exc).lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


class Provider:
    """Base adapter. One instance wraps one registry route."""

    def __init__(self, route: ModelRoute) -> None:
        self.route = route
        self.name = route.model
        self.tier: Tier = route.tier
        self._alive: bool | None = None

    # -- availability ------------------------------------------------------

    def available(self) -> bool:
        if self.route.needs_key() and not self.route.key():
            return False
        if self._alive is None:
            self._alive = self._probe()
        return self._alive

    def _probe(self) -> bool:
        """Cheap liveness check. Cloud routes are assumed reachable if keyed."""
        return True

    def invalidate(self) -> None:
        self._alive = None

    # -- generation --------------------------------------------------------

    def generate(self, system: str, user: str, *, json_mode: bool = False,
                 purpose: str = "plan") -> tuple[str, ModelCall]:
        start = time.perf_counter()
        try:
            text, sent = self._call(system, user, json_mode)
        except requests.RequestException as exc:
            # Free tiers are capped per key. If this provider has more keys in
            # its pool and the failure looks like a quota problem, move to the
            # next key and try once more before giving up on the provider.
            if _is_quota_error(exc) and settings.rotate_key(self.route.key_name):
                emit("model.key_rotated", route=self.route.id,
                     index=settings.active_key_index(self.route.key_name),
                     of=settings.key_pool_size(self.route.key_name))
                try:
                    text, sent = self._call(system, user, json_mode)
                except requests.RequestException as retry_exc:
                    self._record(purpose, start, False, system, user, "", 0,
                                 error=str(retry_exc))
                    self._alive = None
                    raise ModelUnavailable(
                        f"{self.route.id} unreachable: {retry_exc}") from retry_exc
                return text, self._record(purpose, start, True, system, user,
                                          text, sent)
            self._record(purpose, start, False, system, user, "", 0,
                         error=str(exc))
            self._alive = None
            raise ModelUnavailable(f"{self.route.id} unreachable: {exc}") from exc

        if self.tier is Tier.LOCAL:
            # A successful local call may have just loaded the weights, which
            # changes what the resource guard should conclude next time.
            from jarvis.models import router as _router
            _router.invalidate_residency()

        return text, self._record(purpose, start, True, system, user, text, sent)

    def _call(self, system: str, user: str,
              json_mode: bool) -> tuple[str, int]:
        raise NotImplementedError

    def _record(self, purpose: str, start: float, ok: bool, system: str,
                user: str, text: str, sent: int, *,
                error: str | None = None) -> ModelCall:
        call = ModelCall(
            tier=self.tier, model=f"{self.route.provider}:{self.name}",
            purpose=purpose,
            latency_ms=int((time.perf_counter() - start) * 1000), ok=ok,
            prompt_chars=len(system) + len(user), output_chars=len(text),
            bytes_sent=sent, error=error,
        )
        emit("model.call", route=self.route.id, **call.model_dump())
        return call

    # -- shared helpers ----------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.route.key()
        if self.route.auth is Auth.BEARER and key:
            headers["Authorization"] = f"Bearer {key}"
        elif self.route.auth is Auth.HEADER and key:
            headers[self.route.auth_header] = key
        return headers


class OllamaProvider(Provider):
    """Ollama native API. Loopback only: nothing leaves the machine."""

    def _probe(self) -> bool:
        try:
            resp = requests.get(f"{self.route.base_url.rstrip('/')}/api/tags",
                                timeout=4)
            resp.raise_for_status()
            names = {m.get("name", "") for m in resp.json().get("models", [])}
            base = self.name.split(":")[0]
            return self.name in names or any(n.split(":")[0] == base for n in names)
        except (requests.RequestException, ValueError):
            return False

    def _call(self, system: str, user: str, json_mode: bool) -> tuple[str, int]:
        payload: dict[str, Any] = {
            "model": self.name, "prompt": user, "system": system,
            "stream": False,
            "options": {"temperature": 0.1 if json_mode else 0.4,
                        "num_predict": 1024},
        }
        if json_mode:
            payload["format"] = "json"
        resp = requests.post(f"{self.route.base_url.rstrip('/')}/api/generate",
                             json=payload, timeout=settings.MODEL_TIMEOUT_S)
        resp.raise_for_status()
        return resp.json().get("response", ""), 0


class OpenAICompatProvider(Provider):
    """/v1/chat/completions - LM Studio, OpenRouter, NVIDIA NIM, Sarvam."""

    def _probe(self) -> bool:
        if self.route.tier is Tier.CLOUD:
            # Do not spend a request probing a metered or rate-limited API;
            # a configured key is treated as available and failure triggers
            # failover at call time.
            return True
        try:
            resp = requests.get(f"{self.route.base_url.rstrip('/')}/models",
                                headers=self._auth_headers(), timeout=4)
            resp.raise_for_status()
            ids = {m.get("id", "") for m in resp.json().get("data", [])}
            return self.name in ids
        except (requests.RequestException, ValueError):
            return False

    def _call(self, system: str, user: str, json_mode: bool) -> tuple[str, int]:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.1 if json_mode else 0.4,
            "max_tokens": 1600,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        raw = json.dumps(body)
        resp = requests.post(f"{self.route.base_url.rstrip('/')}/chat/completions",
                             data=raw, headers=self._auth_headers(),
                             timeout=settings.MODEL_TIMEOUT_S)
        if resp.status_code >= 400:
            raise requests.RequestException(
                f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise requests.RequestException(f"non-JSON response: {exc}") from exc
        choices = data.get("choices")
        if not choices:
            # Some gateways return HTTP 200 with an error body and no choices.
            raise requests.RequestException(
                f"no choices in response: {str(data)[:300]}")
        content = choices[0].get("message", {}).get("content") or ""
        sent = 0 if self.route.tier is Tier.LOCAL else len(raw.encode("utf-8"))
        return content, sent


class GeminiProvider(Provider):
    """Google generateContent."""

    def _call(self, system: str, user: str, json_mode: bool) -> tuple[str, int]:
        key = self.route.key()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.name}:generateContent?key={key}")
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.1 if json_mode else 0.4,
                                 "maxOutputTokens": 2048},
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        raw = json.dumps(body)
        resp = requests.post(url, data=raw,
                             headers={"Content-Type": "application/json"},
                             timeout=settings.MODEL_TIMEOUT_S)
        if resp.status_code >= 400:
            raise requests.RequestException(
                f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise requests.RequestException(
                f"no candidates: {str(data)[:300]}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        return text, len(raw.encode("utf-8"))


_ADAPTERS = {
    Wire.OLLAMA: OllamaProvider,
    Wire.OPENAI_COMPAT: OpenAICompatProvider,
    Wire.GEMINI: GeminiProvider,
}

_cache: dict[str, Provider] = {}


def for_route(route: ModelRoute) -> Provider:
    """Build (and cache) the adapter for a registry route."""
    if route.id not in _cache:
        _cache[route.id] = _ADAPTERS[route.wire](route)
    return _cache[route.id]


def reset_cache() -> None:
    _cache.clear()
