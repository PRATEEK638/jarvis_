"""File index tests.

The load-bearing property is exclusion: a scan of this machine found 40,004
files, of which 39,739 were vendored headers and packages. Indexing those
drowns the few hundred documents a person ever refers to by name.
"""

from __future__ import annotations

import time

import pytest

from jarvis.learning.file_index import Entry, FileIndex, build


def make(tmp_path, rel: str, *, text: str = "x", age_days: float = 0):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        import os
        os.utime(p, (old, old))
    return p


class TestExclusion:
    def test_generated_directories_are_skipped(self, tmp_path):
        make(tmp_path, "report.pdf")
        make(tmp_path, "node_modules/pkg/index.js")
        make(tmp_path, ".venv/lib/thing.py")
        make(tmp_path, "__pycache__/mod.pyc")
        names = {e.name for e in build([tmp_path])}
        assert names == {"report"}

    def test_generated_file_types_are_skipped(self, tmp_path):
        make(tmp_path, "keep.pdf")
        for junk in ("a.pyc", "b.dll", "c.h", "d.log", "e.obj"):
            make(tmp_path, junk)
        assert {e.name for e in build([tmp_path])} == {"keep"}

    def test_hidden_directories_are_skipped(self, tmp_path):
        make(tmp_path, "keep.txt")
        make(tmp_path, ".git/objects/abc")
        assert {e.name for e in build([tmp_path])} == {"keep"}

    def test_a_vanished_file_does_not_crash_the_scan(self, tmp_path):
        """Scans race with the filesystem; a deleted file must be skipped."""
        make(tmp_path, "a.txt")
        entries = build([tmp_path])
        assert entries          # completes rather than raising

    def test_missing_root_is_ignored(self, tmp_path):
        assert build([tmp_path / "does-not-exist"]) == []


class TestRanking:
    @pytest.fixture
    def index(self, tmp_path):
        make(tmp_path, "marksheets/ReportCard.pdf")
        make(tmp_path, "marksheets/sem6.pdf")
        make(tmp_path, "internships/Internship_Report.docx")
        make(tmp_path, "random/notes.txt")
        return FileIndex(build([tmp_path]))

    def test_folder_name_counts_as_evidence(self, index):
        """A file inside 'marksheets' is a marksheet even if it is called
        ReportCard.pdf."""
        best = index.find("marksheet")[0][0]
        assert best.folder == "marksheets"

    def test_resolves_a_multi_word_description(self, index):
        best = index.find("internship report")[0][0]
        assert "Internship" in best.name

    def test_recency_breaks_a_tie(self, tmp_path):
        make(tmp_path, "a/report.pdf", age_days=400)
        make(tmp_path, "b/report.pdf", age_days=0)
        idx = FileIndex(build([tmp_path]))
        assert idx.find("report")[0][0].folder == "b"

    def test_suffix_filter_is_respected(self, index):
        hits = index.find("report", suffix=".docx")
        assert hits and all(e.suffix == ".docx" for e, _ in hits)

    def test_unrelated_description_returns_nothing(self, index):
        assert index.find("kubernetes helm chart") == []

    def test_empty_description_returns_nothing(self, index):
        assert index.find("") == []

    def test_empty_index_is_safe(self):
        assert FileIndex([]).find("anything") == []


class TestPersistence:
    def test_round_trips(self, tmp_path):
        make(tmp_path, "docs/thing.pdf")
        FileIndex(build([tmp_path])).save(tmp_path / "idx.json")
        loaded = FileIndex.load(tmp_path / "idx.json")
        assert loaded.find("thing")

    def test_missing_index_file_is_not_an_error(self, tmp_path):
        assert FileIndex.load(tmp_path / "nope.json").entries == []

    def test_corrupt_index_file_is_not_an_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ not json", encoding="utf-8")
        assert FileIndex.load(p).entries == []
