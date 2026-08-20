"""Document reading tests. Read-only, so no cleanup needed."""

from __future__ import annotations

import pytest

from jarvis.environments.documents import DocumentEnvironment, _page_range


@pytest.fixture
def env():
    return DocumentEnvironment()


class TestProtocol:
    def test_conforms_to_the_environment_protocol(self, env):
        for m in ("state", "capabilities", "constraints", "act", "verify"):
            assert callable(getattr(env, m))
        assert env.id == "documents"

    def test_is_declared_read_only(self, env):
        assert "read-only" in " ".join(env.constraints()).lower()

    def test_no_write_abilities_exist(self, env):
        caps = set(env.capabilities())
        for never in ("write_document", "edit_document", "delete_document"):
            assert never not in caps

    def test_unknown_ability_refused(self, env):
        r = env.act("write_document", {"path": "x"})
        assert not r.ok and r.error == "unregistered"


class TestInputHandling:
    def test_missing_path_is_asked_for(self, env):
        r = env.act("read_document", {})
        assert not r.ok and r.error == "missing_path"

    def test_missing_file_is_reported(self, env, tmp_path):
        r = env.act("read_document", {"path": str(tmp_path / "nope.pdf")})
        assert not r.ok and r.error == "not_found"

    def test_a_directory_is_not_a_document(self, env, tmp_path):
        r = env.act("read_document", {"path": str(tmp_path)})
        assert not r.ok and r.error == "is_a_directory"

    def test_unsupported_format_lists_what_is_possible(self, env, tmp_path):
        f = tmp_path / "thing.dwg"
        f.write_bytes(b"\x00\x01")
        r = env.act("read_document", {"path": str(f)})
        assert not r.ok and r.error == "unsupported_format"
        assert ".pdf" in r.summary


class TestReading:
    def test_plain_text(self, env, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello world", encoding="utf-8")
        r = env.act("read_document", {"path": str(f)})
        assert r.ok and r.evidence["text"] == "hello world"

    def test_latex_is_readable(self, env, tmp_path):
        """The user's own papers are .tex; an earlier version refused them."""
        f = tmp_path / "paper.tex"
        f.write_text(r"\section{Results}", encoding="utf-8")
        r = env.act("read_document", {"path": str(f)})
        assert r.ok and "Results" in r.evidence["text"]

    def test_csv_becomes_readable_rows(self, env, tmp_path):
        f = tmp_path / "d.csv"
        f.write_text("a,b\n1,2\n", encoding="utf-8")
        r = env.act("read_document", {"path": str(f)})
        assert r.ok and "a | b" in r.evidence["text"]

    def test_truncation_is_reported_never_silent(self, env, tmp_path):
        from jarvis.environments.documents import MAX_CHARS
        f = tmp_path / "big.txt"
        f.write_text("x" * (MAX_CHARS + 500), encoding="utf-8")
        r = env.act("read_document", {"path": str(f)})
        assert r.ok
        assert r.evidence["truncated"] is True
        # An answer drawn from part of a document while implying it read all of
        # it is a lie, so the summary must say so too.
        assert "truncated" in r.summary

    def test_empty_file_is_not_claimed_as_success(self, env, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        r = env.act("read_document", {"path": str(f)})
        assert not r.ok

    def test_binary_masquerading_as_text_does_not_crash(self, env, tmp_path):
        f = tmp_path / "weird.txt"
        f.write_bytes(b"\xff\xfe\x00\x00binary")
        r = env.act("read_document", {"path": str(f)})
        assert r.ok is not None      # must not raise


class TestVerification:
    def test_extraction_is_verified_by_character_count(self, env, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("some real content", encoding="utf-8")
        r = env.act("read_document", {"path": str(f)})
        v = env.verify("read_document", {"path": str(f)}, r)
        assert v.verified and v.checked["chars"] > 0


class TestPageRanges:
    @pytest.mark.parametrize("spec,total,expected", [
        ("1", 10, [0]),
        ("1-3", 10, [0, 1, 2]),
        ("2,4", 10, [1, 3]),
        ("1-100", 3, [0, 1, 2]),      # clamped to the real page count
        ("garbage", 3, [0, 1, 2]),     # falls back to everything
    ])
    def test_parsing(self, spec, total, expected):
        assert _page_range(spec, total) == expected
