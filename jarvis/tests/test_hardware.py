"""Hardware environment tests.

Deliberately non-destructive: reads are exercised for real, and every write
test records the current value and restores it afterwards. A test suite that
leaves the user's brightness at 10% is a bad test suite.
"""

from __future__ import annotations

import pytest

from jarvis.environments.hardware import (
    HardwareEnvironment,
    _clamp_percent,
    _truthy,
)


@pytest.fixture
def env():
    return HardwareEnvironment()


class TestInputHandling:
    @pytest.mark.parametrize("value,expected", [
        (50, 50), ("50", 50), (50.7, 50), (-10, 0), (200, 100),
        (None, None), ("abc", None), (True, None), (False, None),
    ])
    def test_percent_is_clamped_and_validated(self, value, expected):
        # True/False must not become 1/0: "set_volume mute=true" would
        # otherwise silently set the volume to 1%.
        assert _clamp_percent(value) == expected

    @pytest.mark.parametrize("value", [True, "true", "yes", "on", "1", "mute"])
    def test_truthy_accepts_the_obvious_forms(self, value):
        assert _truthy(value)

    @pytest.mark.parametrize("value", [False, "false", "no", "off", "0", ""])
    def test_falsey_accepts_the_obvious_forms(self, value):
        assert not _truthy(value)


class TestProtocol:
    def test_conforms_to_the_environment_protocol(self, env):
        for method in ("state", "capabilities", "constraints", "act", "verify"):
            assert callable(getattr(env, method))
        assert env.id == "hardware"

    def test_declares_what_it_will_not_touch(self, env):
        limits = " ".join(env.constraints()).lower()
        # Fan curves, voltages and clocks are the settings where a wrong value
        # is permanent, so their absence must be stated rather than assumed.
        assert "fan" in limits and "volt" in limits

    def test_unknown_ability_is_refused(self, env):
        result = env.act("overclock_gpu", {})
        assert not result.ok and result.error == "unregistered"

    def test_dangerous_abilities_are_absent(self, env):
        caps = set(env.capabilities())
        for never in ("set_fan_speed", "set_voltage", "overclock_gpu",
                      "set_clock_offset"):
            assert never not in caps


class TestReads:
    def test_status_reports_real_values(self, env):
        result = env.act("hardware_status", {})
        assert result.ok
        assert "cpu_percent" in result.evidence

    def test_state_is_cheap_and_shaped(self, env):
        state = env.state()
        assert state.get("available") is True

    def test_wifi_status_does_not_raise(self, env):
        assert env.act("wifi_status", {}).ok is not None

    def test_power_plan_reports_without_changing_anything(self, env):
        result = env.act("power_plan", {})
        assert result.ok
        assert "active" in result.evidence


class TestWrites:
    def test_volume_round_trips_and_is_verified(self, env):
        before = env._read_volume()
        if before is None:
            pytest.skip("no audio endpoint on this machine")
        try:
            target = 30 if before > 40 else 60
            result = env.act("set_volume", {"percent": target})
            assert result.ok
            check = env.verify("set_volume", {"percent": target}, result)
            assert check.verified, check.detail
        finally:
            env.act("set_volume", {"percent": before})
        assert env._read_volume() == before

    def test_brightness_round_trips_or_reports_unsupported(self, env):
        before = env._read_brightness()
        if before is None:
            # External monitors legitimately cannot do this; the ability must
            # say so rather than silently succeeding.
            result = env.act("set_brightness", {"percent": 50})
            assert not result.ok and result.error == "unsupported"
            return
        try:
            target = 40 if before > 55 else 75
            result = env.act("set_brightness", {"percent": target})
            assert result.ok, result.summary
            check = env.verify("set_brightness", {"percent": target}, result)
            assert check.verified, check.detail
        finally:
            env.act("set_brightness", {"percent": before})

    def test_missing_percent_is_refused_not_guessed(self, env):
        result = env.act("set_brightness", {})
        assert not result.ok and result.error == "missing_percent"

    def test_volume_needs_percent_or_mute(self, env):
        result = env.act("set_volume", {})
        assert not result.ok and result.error == "missing_percent"

    def test_nonexistent_power_plan_lists_the_real_ones(self, env):
        result = env.act("power_plan", {"plan": "hyperdrive"})
        assert not result.ok
        assert "Have:" in result.summary
