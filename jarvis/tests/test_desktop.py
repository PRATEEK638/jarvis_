"""Desktop bridge tests.

Runs headless (offscreen Qt platform) so these are safe in CI and never open a
window. The point of these tests is the seam between Qt and the core: widgets
must paint without exceptions, and - critically - the confirmation gate must
still genuinely block, because a UI bug here would be a security bug.
"""

from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="desktop app requires PyQt6")

from PyQt6.QtGui import QPixmap          # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestWidgetsPaint:
    """A paint exception in a custom widget is invisible until it crashes the
    app at runtime, so every widget is rendered once here."""

    def _render(self, widget) -> QPixmap:
        pm = QPixmap(max(widget.width(), 10), max(widget.height(), 10))
        pm.fill()
        widget.render(pm)
        return pm

    @pytest.mark.parametrize("state", ["idle", "listening", "thinking",
                                       "speaking", "acting", "error"])
    def test_reactor_paints_in_every_state(self, qapp, state):
        from jarvis.desktop.widgets.reactor import ArcReactor
        r = ArcReactor(diameter=120)
        r.set_state(state)
        r.set_level(0.6)
        assert not self._render(r).isNull()

    def test_waveform_paints(self, qapp):
        from jarvis.desktop.widgets.waveform import Waveform
        w = Waveform()
        w.resize(240, 52)
        w.push_input(0.8)
        w.push_output(0.4)
        w._sample()
        assert not self._render(w).isNull()

    @pytest.mark.parametrize("value", [0, 40, 70, 99])
    def test_gauge_paints(self, qapp, value):
        from jarvis.desktop.widgets.gauge import Gauge
        g = Gauge("CPU")
        g.set_value(value)
        assert not self._render(g).isNull()

    def test_gauge_clamps_out_of_range_values(self, qapp):
        from jarvis.desktop.widgets.gauge import Gauge
        g = Gauge("CPU")
        g.set_value(150)
        assert g._value == 100
        g.set_value(-20)
        assert g._value == 0


class TestConfirmationGate:
    """The UI must not be able to weaken the policy layer."""

    def test_denied_action_does_not_execute(self, qapp):
        from jarvis.desktop.bridge import Core

        core = Core()
        seen = {}

        def on_confirm(req):
            seen["req"] = req
            core.resolve_confirmation(req, False)

        core.confirm_requested.connect(on_confirm)
        result = {}

        def worker():
            result["out"] = core.orch.call_ability(
                "run_command", {"command": "echo should-not-run"})

        t = threading.Thread(target=worker)
        t.start()
        # The gate blocks the worker until the signal is delivered and answered;
        # pumping the event loop is what lets that happen in a test.
        for _ in range(200):
            qapp.processEvents()
            t.join(timeout=0.05)
            if not t.is_alive():
                break
        t.join(timeout=5)

        try:
            assert "req" in seen, "no confirmation was ever requested"
            assert seen["req"].ability_id == "run_command"
            assert "not approved" in result.get("out", "").lower()
        finally:
            core.shutdown()

    def test_guardrails_block_before_any_confirmation(self, qapp):
        """A catastrophic command must be refused outright - the user should
        never even be offered the chance to approve it."""
        from jarvis.desktop.bridge import Core

        core = Core()
        offered = []
        core.confirm_requested.connect(lambda req: offered.append(req))
        try:
            out = core.orch.call_ability("run_command",
                                         {"command": "rm -rf /"})
            assert "blocked" in out.lower()
            assert not offered, "a blocked command must not reach the user"
        finally:
            core.shutdown()
