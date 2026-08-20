"""Credential pooling and rotation.

Free tiers are rate-limited per key, so a provider may hold several. These tests
use a temporary keys file - they never touch the real one and never make a
network call.
"""

from __future__ import annotations

import json

import pytest

from jarvis.config import settings
from jarvis.models.providers import _is_quota_error


@pytest.fixture
def keys_file(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    monkeypatch.setattr(settings, "KEYS_FILE", path)
    # Environment variables would override the file and break these tests.
    for env in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY",
                "SARVAM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)

    def write(payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")
        settings.reload_keys()

    yield write
    settings.reload_keys()


class TestKeyLoading:
    def test_single_key_as_a_string(self, keys_file):
        keys_file({"nvidia": "nvapi-abc"})
        assert settings.get_key("nvidia") == "nvapi-abc"
        assert settings.key_pool_size("nvidia") == 1

    def test_pool_of_keys_as_a_list(self, keys_file):
        keys_file({"openrouter": ["k1", "k2", "k3"]})
        assert settings.key_pool_size("openrouter") == 3
        assert settings.get_key("openrouter") == "k1"

    def test_blank_values_are_ignored(self, keys_file):
        keys_file({"gemini": "", "sarvam": "", "nvidia": "real"})
        assert settings.get_key("gemini") is None
        assert settings.get_key("nvidia") == "real"

    def test_comment_fields_are_skipped(self, keys_file):
        keys_file({"_comment": "docs", "nvidia": "real"})
        assert settings.get_key("_comment") is None

    def test_missing_file_is_not_an_error(self, keys_file, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "KEYS_FILE", tmp_path / "absent.json")
        settings.reload_keys()
        assert settings.get_key("gemini") is None


class TestRotation:
    def test_rotation_advances_through_the_pool(self, keys_file):
        keys_file({"openrouter": ["k1", "k2", "k3"]})
        assert settings.get_key("openrouter") == "k1"
        assert settings.rotate_key("openrouter") is True
        assert settings.get_key("openrouter") == "k2"
        assert settings.rotate_key("openrouter") is True
        assert settings.get_key("openrouter") == "k3"

    def test_rotation_stops_at_the_end_so_the_caller_fails_over(self, keys_file):
        keys_file({"openrouter": ["k1", "k2"]})
        assert settings.rotate_key("openrouter") is True
        assert settings.rotate_key("openrouter") is False, \
            "an exhausted pool must report failure so the router tries another provider"

    def test_single_key_never_rotates(self, keys_file):
        keys_file({"nvidia": "only"})
        assert settings.rotate_key("nvidia") is False

    def test_reload_resets_the_active_index(self, keys_file):
        keys_file({"openrouter": ["k1", "k2"]})
        settings.rotate_key("openrouter")
        assert settings.get_key("openrouter") == "k2"
        settings.reload_keys()
        assert settings.get_key("openrouter") == "k1"


class TestQuotaDetection:
    """Rotation should trigger on exhaustion, not on every failure."""

    @pytest.mark.parametrize("message", [
        "HTTP 429: rate limit exceeded",
        "free-models-per-day limit reached",
        "insufficient credit",
        "HTTP 402: payment required",
        "Too Many Requests",
    ])
    def test_quota_errors_are_recognised(self, message):
        assert _is_quota_error(Exception(message))

    @pytest.mark.parametrize("message", [
        "Read timed out",
        "Connection refused",
        "HTTP 500: internal server error",
        "no candidates in response",
    ])
    def test_other_failures_do_not_rotate(self, message):
        assert not _is_quota_error(Exception(message))
