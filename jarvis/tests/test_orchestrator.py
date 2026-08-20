"""Orchestrator-level tests covering paths not exercised by test_routing.py."""

from jarvis.core.orchestrator import Orchestrator




class TestCallAbility:
    """orch.call_ability() is the voice path's entry point into the same
    guardrails/confirm/verification pipeline the CLI uses. A real bug here
    (calling a guardrails function that does not exist) went undetected
    because nothing exercised this method directly - every voice tool call
    was silently failing with an AttributeError before this was added."""

    def test_low_risk_ability_executes_without_confirmation(self):
        orch = Orchestrator(confirm=lambda *a: False)  # would refuse if asked
        try:
            result = orch.call_ability("system_state", {})
        finally:
            orch.close()
        assert "AttributeError" not in result
        assert "no attribute" not in result

    def test_guardrails_still_block_a_dangerous_command(self):
        orch = Orchestrator(confirm=lambda *a: True)  # would approve if asked
        try:
            result = orch.call_ability(
                "run_command", {"command": "rm -rf /"})
        finally:
            orch.close()
        assert "Blocked" in result or "blocked" in result.lower()
