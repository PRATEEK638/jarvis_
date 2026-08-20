"""Personality tests.

The load-bearing property is separation: character shapes wording, and must be
structurally unable to influence what JARVIS is permitted to do.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.core import persona


class TestSeparationFromPolicy:
    def test_guardrails_do_not_import_persona(self):
        """A witty assistant that talks itself past a guardrail is a defect,
        not a personality. The policy layer must not even see this module."""
        src = Path("jarvis/policy/guardrails.py").read_text(encoding="utf-8")
        assert "persona" not in src

    def test_prompt_states_that_character_cannot_override_safety(self):
        text = persona.system_prompt().lower()
        assert "never changes what you are permitted to do" in text

    def test_prompt_carries_the_honesty_rules(self):
        text = persona.system_prompt().lower()
        assert "never say an action succeeded unless the verification" in text
        assert "do not know" in text


class TestCharacter:
    def test_addresses_the_user_as_asked(self):
        assert "sir" in persona.system_prompt()

    def test_disagreement_offers_an_alternative_then_complies(self):
        line = persona.DEFAULT.disagree("those files are in use",
                                        "I can archive them instead.")
        assert "archive them instead" in line
        # It must not become nagging: obedience is built into the phrasing.
        assert "proceed anyway" in line

    def test_failures_carry_no_humour(self):
        """A joke attached to a failure reads as indifference to it."""
        line = persona.DEFAULT.failed("The folder was locked.")
        assert "locked" in line
        for quip in ("!", "haha", "oops", "whoops"):
            assert quip not in line.lower()

    def test_traits_are_configurable_not_hardcoded(self):
        plain = persona.Persona(dry_wit=False, will_disagree=False,
                                brevity=False, address="boss")
        text = plain.system_prompt()
        assert "boss" in text
        assert "Dry wit" not in text
        assert "You may disagree" not in text

    def test_disabling_wit_does_not_disable_honesty(self):
        plain = persona.Persona(dry_wit=False, will_disagree=False)
        assert "verification" in plain.system_prompt().lower()
