"""Bandit routing tests.

The properties that matter are the ones that separate this from a lookup
table: it must converge on the genuinely better arm, keep exploring an
under-tried one, and never be able to reach past a hard constraint.
"""

from __future__ import annotations

import random

import pytest

from jarvis.learning.bandit import Arm, RouteBandit


@pytest.fixture
def bandit(tmp_path):
    return RouteBandit(path=tmp_path / "stats.json", seed=7)


class TestLearning:
    def test_converges_on_the_better_arm(self, bandit):
        routes = ["good", "bad"]
        rng = random.Random(1)
        truth = {"good": 0.9, "bad": 0.2}
        for _ in range(120):
            pick = bandit.order(routes, "hard")[0]
            bandit.record(pick, "hard", success=rng.random() < truth[pick],
                          latency_ms=1000)
        report = {r["route"]: r for r in bandit.report()}
        # Most of the budget should have gone to the better arm.
        assert report["good"]["trials"] > report["bad"]["trials"] * 3
        assert bandit.order(routes, "hard")[0] == "good"

    def test_still_explores_an_untried_arm(self, bandit):
        """A route with no history must not be permanently ignored - that is
        the failure of a greedy strategy."""
        for _ in range(25):
            bandit.record("known", "simple", success=True, latency_ms=1000)
        picks = {bandit.order(["known", "fresh"], "simple")[0]
                 for _ in range(40)}
        assert "fresh" in picks

    def test_statistics_are_kept_per_task_kind(self, bandit):
        """A route can be good at simple work and useless at diagnosis; one
        average hides exactly that."""
        for _ in range(20):
            bandit.record("r", "simple", success=True, latency_ms=500)
            bandit.record("r", "hard", success=False, latency_ms=500)
        by = {(x["route"], x["kind"]): x for x in bandit.report()}
        assert by[("r", "simple")]["success_rate"] == 1.0
        assert by[("r", "hard")]["success_rate"] == 0.0

    def test_decay_lets_it_notice_the_world_changed(self, bandit):
        for _ in range(50):
            bandit.record("r", "simple", success=True, latency_ms=500)
        before = bandit.report()[0]["trials"]
        for _ in range(40):
            bandit.decay()
        assert bandit.report()[0]["trials"] < before

    def test_low_evidence_is_flagged_not_hidden(self, bandit):
        bandit.record("r", "simple", success=True, latency_ms=500)
        assert bandit.report()[0]["confident"] is False


class TestSafety:
    def test_only_reorders_what_it_is_given(self, bandit):
        """It must never introduce a route the router did not permit."""
        allowed = ["a", "b"]
        for _ in range(30):
            bandit.record("c", "simple", success=True, latency_ms=100)
        assert set(bandit.order(allowed, "simple")) == set(allowed)

    def test_single_candidate_is_returned_unchanged(self, bandit):
        assert bandit.order(["only"], "simple") == ["only"]

    def test_empty_candidates_are_safe(self, bandit):
        assert bandit.order([], "simple") == []

    def test_corrupt_stats_file_does_not_break_routing(self, tmp_path):
        p = tmp_path / "stats.json"
        p.write_text("{ this is not json", encoding="utf-8")
        b = RouteBandit(path=p)
        assert b.order(["a", "b"], "simple")

    def test_speed_cannot_outrank_correctness(self, bandit):
        """A fast-but-wrong route must not beat a slow-but-right one."""
        for _ in range(40):
            bandit.record("fast_wrong", "simple", success=False, latency_ms=50)
            bandit.record("slow_right", "simple", success=True, latency_ms=9000)
        wins = sum(bandit.order(["fast_wrong", "slow_right"], "simple")[0]
                   == "slow_right" for _ in range(40))
        assert wins > 30


class TestArm:
    def test_untried_arm_is_neutral_not_optimistic(self):
        assert Arm().rate == 0.5
