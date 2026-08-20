"""Plan critic tests.

The bar is 'obviously wrong', not 'could be better'. A critic that rejects
merely-imperfect plans burns the whole route chain and ends up worse than the
plan it rejected, so the false-positive cases matter most.
"""

from __future__ import annotations

import pytest

from jarvis.core import critic
from jarvis.core.contracts import Plan, Step


def plan_of(*abilities):
    return Plan(steps=[Step(n=i + 1, ability=a, args={}, why="")
                       for i, a in enumerate(abilities)])


class TestRejects:
    def test_multi_part_request_with_one_step(self):
        v = critic.review("create a folder and then move the files into it",
                          plan_of("create_folder"))
        assert not v and "more than one action" in v.reason

    def test_diagnostic_request_answered_with_one_reading(self):
        """The measured failure: reports numbers, never reaches a cause."""
        v = critic.review("why is my pc slow, work out what is causing it",
                          plan_of("system_state"))
        assert not v and "cause" in v.reason

    def test_repeated_observation_is_a_loop(self):
        v = critic.review("check the system", plan_of("system_state",
                                                      "system_state"))
        assert not v and "repeated" in v.reason


class TestAccepts:
    def test_diagnostic_with_a_follow_up_step(self):
        assert critic.review("why is my pc slow",
                             plan_of("system_state", "list_processes"))

    def test_multi_part_request_with_matching_steps(self):
        assert critic.review("create a folder and then a file",
                             plan_of("create_folder", "create_file"))

    def test_simple_request_with_one_step(self):
        assert critic.review("what is my cpu usage", plan_of("system_state"))

    def test_answer_only_plan_is_fine(self):
        assert critic.review("what is the capital of Japan", Plan())

    def test_and_inside_a_noun_phrase_is_not_a_sequence(self):
        """'bread and butter' must not read as two actions."""
        assert critic.review("find the bread and butter recipe",
                             plan_of("find_files"))

    def test_single_action_that_writes_is_not_diagnostic(self):
        assert critic.review("why not just create the folder",
                             plan_of("create_folder"))

    def test_repeated_action_ability_is_allowed(self):
        """Two writes in a row is a normal plan; two identical readings is not."""
        assert critic.review("make two folders",
                             plan_of("create_folder", "create_folder"))
