"""Classifier, fast path and registry tests.

The routing decision is the system's central claim, so it is tested as pure
logic: no model is called anywhere in this file.
"""

from __future__ import annotations

import pytest

from jarvis.core import fastpath
from jarvis.core.contracts import Difficulty, Privacy, Tier
from jarvis.models import registry
from jarvis.models.router import classify


class TestPrivacyClassification:
    """Privacy is a hard constraint: anything touching this machine stays local."""

    @pytest.mark.parametrize("objective", [
        r"read C:\Users\heman\Desktop\notes.txt",
        "create a folder on my desktop",
        "what is my cpu usage",
        "find report.pdf in my documents",
        "how much disk space is free",
        "move the file into the archive folder",
        "remember that my guide is Prateek",
    ])
    def test_local_references_are_pinned_local(self, objective):
        assert classify(objective).privacy is Privacy.LOCAL_ONLY

    @pytest.mark.parametrize("objective", [
        "what is the capital of France",
        "explain what a binary search tree is",
        "who won the world cup",
    ])
    def test_generic_questions_are_shareable(self, objective):
        assert classify(objective).privacy is Privacy.SHAREABLE

    def test_local_reference_outranks_web_keyword(self):
        # "current" is a web signal, but CPU usage is local machine state.
        c = classify("what is my current cpu usage")
        assert c.privacy is Privacy.LOCAL_ONLY
        assert c.needs_web is False

    def test_memory_write_is_not_a_web_request(self):
        # "today" is a web signal; storing a fact is not a web task.
        c = classify("remember that my demo is today")
        assert c.needs_web is False
        assert c.privacy is Privacy.LOCAL_ONLY


class TestDifficulty:
    def test_web_question_is_hard(self):
        c = classify("search the web for the latest python release")
        assert c.needs_web is True
        assert c.difficulty is Difficulty.HARD

    def test_sequential_phrasing_is_composite(self):
        c = classify("create a folder called x and then move a.txt into it")
        assert c.difficulty in (Difficulty.MODERATE, Difficulty.HARD)

    def test_gui_request_detected(self):
        assert classify("click the Save button in Notepad").needs_gui is True


class TestFastPath:
    """Unambiguous intents must resolve without a model call."""

    def test_remember_maps_to_remember_ability(self):
        plan = fastpath.match("remember that my guide is Prateek Sharma")
        assert plan is not None
        assert plan.steps[0].ability == "remember"
        assert "Prateek Sharma" in plan.steps[0].args["content"]

    def test_recall_maps_to_recall_ability(self):
        plan = fastpath.match("what do you remember about my project")
        assert plan is not None
        assert plan.steps[0].ability == "recall"

    def test_recall_is_matched_before_remember(self):
        # "remind me what ..." contains neither trap, but must not store a fact.
        plan = fastpath.match("remind me what my deadline is")
        assert plan is not None and plan.steps[0].ability == "recall"

    @pytest.mark.parametrize("text,ability", [
        ("open notepad", "open_app"),
        ("launch calculator", "open_app"),
        ("what is my cpu usage", "system_state"),
        ("disk usage", "system_state"),
        ("which windows are open", "list_windows"),
    ])
    def test_direct_intents(self, text, ability):
        plan = fastpath.match(text)
        assert plan is not None, f"no fast path for {text!r}"
        assert plan.steps[0].ability == ability

    @pytest.mark.parametrize("text", [
        "create a folder called reports and then move the txt files into it",
        "find every pdf mentioning neural networks and summarise them",
        "open the file and tell me what is wrong with the code",
        "search the web for python 3.13 release notes",
    ])
    def test_ambiguous_requests_go_to_the_planner(self, text):
        assert fastpath.match(text) is None, \
            "genuinely ambiguous requests must not be shortcut"


class TestFileOperationFastPaths:
    """Common file requests must resolve exactly, instantly, and on-device."""

    @pytest.mark.parametrize("text,ability,expected", [
        ("make a folder called notes on my desktop", "create_folder",
         {"path": "desktop/notes"}),
        ("can you create a new folder named ideas in C:/tmp", "create_folder",
         {"path": "C:/tmp/ideas"}),
        ("put a file called todo.txt in C:/tmp that says buy milk", "create_file",
         {"path": "C:/tmp/todo.txt", "content": "buy milk"}),
        ("write 'hello world' into C:/tmp/greeting.txt", "create_file",
         {"path": "C:/tmp/greeting.txt", "content": "hello world"}),
        ("rename C:/tmp/greeting.txt to welcome.txt", "rename_path",
         {"source": "C:/tmp/greeting.txt", "new_name": "welcome.txt"}),
        ("copy C:/tmp/a.txt to C:/tmp/b.txt", "copy_path",
         {"source": "C:/tmp/a.txt", "destination": "C:/tmp/b.txt"}),
        ("move C:/tmp/a.txt to C:/tmp/archive", "move_path",
         {"source": "C:/tmp/a.txt", "destination": "C:/tmp/archive"}),
        ("make a copy of C:/tmp/todo.txt called todo_backup.txt", "copy_path",
         {"source": "C:/tmp/todo.txt", "destination": "C:/tmp/todo_backup.txt"}),
        ("where is todo.txt", "find_files", {"name": "todo.txt"}),
        ("which file in C:/tmp contains the words buy milk", "search_in_files",
         {"query": "buy milk", "root": "C:/tmp"}),
    ])
    def test_arguments_are_extracted_exactly(self, text, ability, expected):
        plan = fastpath.match(text)
        assert plan is not None, f"no fast path matched: {text!r}"
        step = plan.steps[0]
        assert step.ability == ability
        assert step.args == expected

    @pytest.mark.parametrize("text", [
        "search the web for python 3.13 release notes",
        "search the web for what HTTP 429 means",
        "look up the latest news online",
    ])
    def test_web_phrasing_is_not_mistaken_for_a_file_search(self, text):
        """'search the web for X' has the same shape as 'search DIR for X'."""
        plan = fastpath.match(text)
        assert plan is None or plan.steps[0].ability != "search_in_files"

    def test_home_shorthand_is_preserved_for_the_environment(self):
        plan = fastpath.match("make a folder called notes on my desktop")
        # LocalOSEnvironment resolves "desktop/x" against the real home folder,
        # so the fast path must not guess an absolute path itself.
        assert plan.steps[0].args["path"].startswith("desktop/")


class TestRegistry:
    def test_every_route_has_a_unique_id(self):
        ids = [r.id for r in registry.all_routes()]
        assert len(ids) == len(set(ids))

    def test_local_routes_are_marked_private_and_free(self):
        for route in registry.all_routes():
            if route.tier is Tier.LOCAL:
                assert route.private, f"{route.id} runs locally but is not private"
                assert route.free, f"{route.id} runs locally but is not free"

    def test_cloud_routes_require_a_key(self):
        for route in registry.all_routes():
            if route.tier is Tier.CLOUD:
                assert route.needs_key(), f"{route.id} is remote but needs no key"

    def test_metered_route_sorts_last(self):
        ordered = sorted(registry.all_routes(), key=registry.preference_key)
        assert ordered[-1].last_resort, \
            "a metered last-resort route must sort to the very end"

    def test_free_private_route_outranks_paid(self):
        ordered = sorted(registry.all_routes(), key=registry.preference_key)
        assert ordered[0].private, \
            "a free on-device route should be preferred by default"
