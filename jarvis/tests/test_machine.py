"""Machine identity tests.

The property that matters: the same checkout must behave correctly on a 16 GB
laptop and a 32 GB workstation without a hand-edited config.
"""

from __future__ import annotations

import pytest

from jarvis.config import machine


class TestDetection:
    def test_reports_this_machine(self):
        m = machine.current()
        assert m.name and m.cpu_threads > 0 and m.ram_gb > 0

    def test_role_comes_from_hardware_not_hostname(self):
        """A rebuild or rename must still land on the right profile."""
        m = machine.current()
        assert m.role in ("laptop", "workstation", "unknown")
        if m.has_battery:
            assert m.role == "laptop"

    def test_every_machine_explains_its_local_model_decision(self):
        m = machine.current()
        assert m.notes
        assert any("local inference" in n for n in m.notes)


class TestLocalModelPolicy:
    def test_decision_follows_ram_not_gpu(self):
        """VRAM is not the binding constraint: a 7B holds ~6 GB of host RAM
        even with layers offloaded, which is what starves a 16 GB laptop."""
        m = machine.current()
        assert m.local_models_viable == (m.ram_gb >= machine.LOCAL_MODEL_RAM_GB)

    def test_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("JARVIS_LOCAL_ENABLED", "1")
        assert machine.local_enabled() is True
        monkeypatch.setenv("JARVIS_LOCAL_ENABLED", "0")
        assert machine.local_enabled() is False

    def test_without_override_it_follows_the_machine(self, monkeypatch):
        monkeypatch.delenv("JARVIS_LOCAL_ENABLED", raising=False)
        assert machine.local_enabled() == machine.current().local_models_viable

    def test_settings_flag_reflects_the_machine(self, monkeypatch):
        monkeypatch.delenv("JARVIS_LOCAL_ENABLED", raising=False)
        from jarvis.config import settings
        assert bool(settings.LOCAL_ENABLED) == machine.current().local_models_viable
