"""Tests for learning from JARVIS's own event log.

The critical property is abstention. A classifier that answers everything is
worse than useless here, because a confidently wrong ability runs the wrong
action on a real machine.
"""

from __future__ import annotations

import json

import pytest

from jarvis.learning import dataset


def _log(tmp_path, events):
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return p


class TestDataset:
    def test_pairs_a_goal_with_the_ability_that_ran(self, tmp_path):
        log = _log(tmp_path, [
            {"kind": "goal.received", "objective": "make a folder called x"},
            {"kind": "ability.executed", "ability": "create_folder", "ok": True},
        ])
        examples = dataset.build(log)
        assert len(examples) == 1
        assert examples[0].ability == "create_folder"
        assert examples[0].ok

    def test_only_the_first_ability_of_a_goal_is_labelled(self, tmp_path):
        """A multi-step plan must not produce a second, mislabelled example:
        the goal describes the whole task, not its second step."""
        log = _log(tmp_path, [
            {"kind": "goal.received", "objective": "make a folder then a file"},
            {"kind": "ability.executed", "ability": "create_folder", "ok": True},
            {"kind": "ability.executed", "ability": "create_file", "ok": True},
        ])
        assert len(dataset.build(log)) == 1

    def test_executions_before_any_goal_are_ignored(self, tmp_path):
        log = _log(tmp_path, [
            {"kind": "ability.executed", "ability": "create_folder", "ok": True},
        ])
        assert dataset.build(log) == []

    def test_memory_abilities_are_excluded(self, tmp_path):
        """'remember X' is already handled deterministically, so training on
        it teaches the model nothing."""
        log = _log(tmp_path, [
            {"kind": "goal.received", "objective": "remember my id is 7"},
            {"kind": "ability.executed", "ability": "remember", "ok": True},
        ])
        assert dataset.build(log) == []

    def test_failures_are_dropped_from_training(self, tmp_path):
        """A goal whose ability failed is not evidence it was the right one."""
        log = _log(tmp_path, [
            {"kind": "goal.received", "objective": "open spotify"},
            {"kind": "ability.executed", "ability": "open_app", "ok": False},
        ])
        kept, _ = dataset.usable(dataset.build(log))
        assert kept == []

    def test_rare_classes_are_dropped(self, tmp_path):
        events = []
        for i in range(5):
            events += [{"kind": "goal.received", "objective": f"make folder {i}"},
                       {"kind": "ability.executed", "ability": "create_folder",
                        "ok": True}]
        events += [{"kind": "goal.received", "objective": "one off thing"},
                   {"kind": "ability.executed", "ability": "exotic", "ok": True}]
        kept, counts = dataset.usable(dataset.build(_log(tmp_path, events)))
        assert {e.ability for e in kept} == {"create_folder"}
        assert counts["exotic"] == 1

    def test_missing_log_is_not_an_error(self, tmp_path):
        assert dataset.build(tmp_path / "nope.jsonl") == []

    def test_corrupt_lines_are_skipped(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text('{"kind": "goal.received", "objective": "a folder"}\n'
                     'not json at all\n'
                     '{"kind": "ability.executed", "ability": "create_folder",'
                     ' "ok": true}\n', encoding="utf-8")
        assert len(dataset.build(p)) == 1


class TestTraining:
    def test_refuses_to_train_on_too_little_data(self, tmp_path):
        rm = pytest.importorskip("jarvis.learning.router_model")
        log = _log(tmp_path, [
            {"kind": "goal.received", "objective": "make a folder"},
            {"kind": "ability.executed", "ability": "create_folder", "ok": True},
        ])
        report = rm.train(log=log)
        assert not report.trained
        assert "usable examples" in report.reason

    def test_classifier_abstains_on_nonsense(self):
        rm = pytest.importorskip("jarvis.learning.router_model")
        clf = rm.AbilityClassifier()
        if not clf.available:
            pytest.skip("no trained model on this machine yet")
        for text in ("write a haiku about the sea",
                     "deploy my app to kubernetes",
                     "qqqq zzzz xxxx"):
            assert clf.predict(text) is None, f"should abstain on {text!r}"

    def test_prediction_is_a_plain_string(self):
        """sklearn returns numpy.str_, which leaks into JSON as
        "np.str_('open_app')" if not cast."""
        rm = pytest.importorskip("jarvis.learning.router_model")
        clf = rm.AbilityClassifier()
        if not clf.available:
            pytest.skip("no trained model on this machine yet")
        hit = clf.predict("open calculator")
        if hit is not None:
            assert type(hit[0]) is str
