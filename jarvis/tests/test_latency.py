"""Latency reporting tests."""

from __future__ import annotations

import json

import pytest

from jarvis.latency import collect, percentile


class TestPercentile:
    def test_every_reported_value_is_a_real_observation(self):
        """Nearest-rank, not interpolated: with a few hundred samples,
        interpolation invents precision the data does not support."""
        values = [1.0, 2.0, 3.0, 4.0, 100.0]
        for p in (50, 90, 99):
            assert percentile(values, p) in values

    def test_p50_is_the_middle(self):
        assert percentile([1, 2, 3, 4, 5], 50) == 3

    def test_p99_catches_the_outlier(self):
        """The whole point: an average of these is 20, which hides the 100."""
        assert percentile([1] * 99 + [100], 99) == 100

    def test_empty_is_zero_not_an_error(self):
        assert percentile([], 90) == 0.0

    def test_single_sample(self):
        assert percentile([7], 99) == 7


class TestCollect:
    def _log(self, tmp_path, events):
        p = tmp_path / "events.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        return p

    def test_reads_each_stage_from_its_own_field(self, tmp_path):
        log = self._log(tmp_path, [
            {"kind": "model.call", "latency_ms": 500, "route": "r"},
            {"kind": "ability.executed", "duration_ms": 20},
            {"kind": "goal.finished", "total_ms": 900},
        ])
        got = collect(log)
        assert got["model call"] == [500.0]
        assert got["tool execution"] == [20.0]
        assert got["whole request"] == [900.0]

    def test_model_calls_are_also_broken_down_per_route(self, tmp_path):
        """One average hides that a provider is three times slower."""
        log = self._log(tmp_path, [
            {"kind": "model.call", "latency_ms": 100, "route": "fast"},
            {"kind": "model.call", "latency_ms": 900, "route": "slow"},
        ])
        got = collect(log)
        assert got["  via fast"] == [100.0]
        assert got["  via slow"] == [900.0]

    def test_corrupt_lines_are_skipped(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text('{"kind":"model.call","latency_ms":10}\n'
                     'not json\n'
                     '{"kind":"model.call","latency_ms":20}\n', encoding="utf-8")
        assert len(collect(p)["model call"]) == 2

    def test_missing_or_negative_durations_are_ignored(self, tmp_path):
        log = self._log(tmp_path, [
            {"kind": "model.call", "route": "r"},
            {"kind": "model.call", "latency_ms": None, "route": "r"},
            {"kind": "model.call", "latency_ms": 50, "route": "r"},
        ])
        assert collect(log)["model call"] == [50.0]

    def test_missing_log_is_not_an_error(self, tmp_path):
        assert collect(tmp_path / "nope.jsonl") == {}
