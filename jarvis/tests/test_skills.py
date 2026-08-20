"""Skill selection tests.

The negative cases matter as much as the positive ones: injecting the wrong
playbook actively misleads the planner, so a conservative selector that stays
silent on ambiguous input is the correct behaviour.
"""

from __future__ import annotations

import pytest

from jarvis.skills import registry as skills


class TestLibrary:
    def test_library_loads(self):
        found = skills.all_skills(refresh=True)
        assert found, "no skills discovered in the library"
        for s in found:
            assert s.triggers, f"{s.name} has no triggers"
            assert s.body.strip(), f"{s.name} has an empty body"

    def test_every_skill_has_a_description(self):
        for s in skills.all_skills():
            assert s.description.strip(), f"{s.name} has no description"


class TestSelection:
    @pytest.mark.parametrize("text,expected", [
        ("why is my python script failing with a traceback", "debugging"),
        ("the build is broken and throws an exception", "debugging"),
        ("my pc is slow, cpu and memory are high", "system-admin"),
        ("research the latest news and compare the sources", "research"),
        ("organise these files and rename the folder", "file-work"),
    ])
    def test_routes_to_the_right_expertise(self, text, expected):
        chosen = skills.select(text)
        assert chosen, f"nothing selected for {text!r}"
        assert chosen[0].name == expected

    @pytest.mark.parametrize("text", [
        "hello how are you",
        "what is 2 plus 2",
        "open notepad",
        "remember that my roll number is 21CS1234",
        "what time is it",
    ])
    def test_ordinary_requests_load_no_playbook(self, text):
        assert skills.select(text) == [], \
            "a playbook was injected for a request that does not need one"

    def test_a_single_keyword_is_not_enough(self):
        # "error" alone is incidental - it appears in plenty of non-debugging
        # requests. Two independent signals are required.
        assert skills.select("read me the error article") == []

    def test_injection_is_capped(self):
        text = ("debug the error traceback, my cpu and memory are slow, "
                "research the latest sources, and organise these files folder")
        assert len(skills.select(text)) <= skills.MAX_SKILLS_INJECTED

    def test_prompt_is_empty_when_nothing_matches(self):
        assert skills.prompt_for("hello there") == ""

    def test_prompt_contains_the_playbook_when_matched(self):
        out = skills.prompt_for("my script is failing with an exception traceback")
        assert "debugging" in out.lower()
        assert "EXPERTISE" in out
