"""Environment and memory tests.

These run against the real filesystem (in a pytest tmp_path) and a real SQLite
file, not mocks: the point of the verification layer is that it observes actual
state, so mocking it away would test nothing.
"""

from __future__ import annotations

import pytest

from jarvis.core.contracts import IMPLEMENTED_MEMORY_TYPES, MemoryType
from jarvis.environments.local_os import LocalOSEnvironment
from jarvis.memory.store import MemoryStore, WorkingMemory


@pytest.fixture
def env():
    return LocalOSEnvironment()


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(path=str(tmp_path / "test.db"))
    yield s
    s.close()


class TestSystemState:
    def test_reports_plausible_live_values(self, env):
        state = env.state()
        assert 0 <= state["cpu_percent"] <= 100
        assert state["ram_total_gb"] > 0
        assert state["disk_free_gb"] > 0
        assert state["process_count"] > 0


class TestFileOperations:
    def test_create_folder_and_verify(self, env, tmp_path):
        target = tmp_path / "reports"
        result = env.act("create_folder", {"path": str(target)})
        assert result.ok and target.is_dir()
        v = env.verify("create_folder", {"path": str(target)}, result)
        assert v.verified

    def test_create_file_with_content(self, env, tmp_path):
        target = tmp_path / "a.txt"
        args = {"path": str(target), "content": "hello jarvis"}
        result = env.act("create_file", args)
        assert result.ok
        assert target.read_text(encoding="utf-8") == "hello jarvis"
        assert env.verify("create_file", args, result).verified

    def test_verification_fails_when_file_is_absent(self, env, tmp_path):
        """The verifier must observe reality, not trust the reported result."""
        args = {"path": str(tmp_path / "never_written.txt"), "content": "x"}
        fake = env.act("read_file", {"path": str(tmp_path / "nope.txt")})
        v = env.verify("create_file", args, fake)
        assert not v.verified

    def test_move_and_rename(self, env, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("data", encoding="utf-8")
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()

        moved = env.act("move_path", {"source": str(src),
                                      "destination": str(dest_dir)})
        assert moved.ok and (dest_dir / "a.txt").exists() and not src.exists()

        renamed = env.act("rename_path", {"source": str(dest_dir / "a.txt"),
                                          "new_name": "b.txt"})
        assert renamed.ok and (dest_dir / "b.txt").exists()

    def test_copy_leaves_the_source_in_place(self, env, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("data", encoding="utf-8")
        result = env.act("copy_path", {"source": str(src),
                                       "destination": str(tmp_path / "b.txt")})
        assert result.ok and src.exists() and (tmp_path / "b.txt").exists()

    def test_missing_source_fails_cleanly(self, env, tmp_path):
        result = env.act("move_path", {"source": str(tmp_path / "ghost.txt"),
                                       "destination": str(tmp_path / "x.txt")})
        assert not result.ok and result.error == "not_found"


class TestFileSearch:
    def test_find_by_partial_name(self, env, tmp_path):
        (tmp_path / "quarterly_report.txt").write_text("x", encoding="utf-8")
        (tmp_path / "unrelated.log").write_text("x", encoding="utf-8")
        result = env.act("find_files", {"name": "report", "root": str(tmp_path)})
        names = [m["name"] for m in result.evidence["matches"]]
        assert "quarterly_report.txt" in names
        assert "unrelated.log" not in names

    def test_search_text_inside_files(self, env, tmp_path):
        (tmp_path / "a.txt").write_text("the mitochondria is the powerhouse",
                                        encoding="utf-8")
        (tmp_path / "b.txt").write_text("nothing relevant", encoding="utf-8")
        result = env.act("search_in_files", {"text": "mitochondria",
                                             "root": str(tmp_path)})
        assert result.evidence["match_count"] == 1
        assert result.evidence["matches"][0]["path"].endswith("a.txt")

    def test_absent_text_reports_zero_not_an_error(self, env, tmp_path):
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        result = env.act("search_in_files", {"text": "zzzz_not_here",
                                             "root": str(tmp_path)})
        assert result.ok and result.evidence["match_count"] == 0


class TestShell:
    def test_exit_code_is_reported_honestly(self, env):
        result = env.act("run_command", {"command": "exit 3"})
        assert result.evidence["exit_code"] == 3
        assert not result.ok
        assert not env.verify("run_command", {}, result).verified

    def test_successful_command(self, env):
        result = env.act("run_command", {"command": "Write-Output hello"})
        assert result.ok and "hello" in result.evidence["stdout"]


class TestMemory:
    def test_fact_survives_a_new_store_instance(self, tmp_path):
        """Persistence means surviving process restart, not just the session."""
        path = str(tmp_path / "mem.db")
        first = MemoryStore(path=path)
        first.remember("the guide for this project is Prateek Sharma")
        first.close()

        second = MemoryStore(path=path)
        hits = second.recall("who is the guide")
        second.close()
        assert hits and "Prateek Sharma" in hits[0].content

    def test_recall_ranks_the_relevant_fact_first(self, store):
        store.remember("my favourite language is Python")
        store.remember("the deadline for the report is Friday")
        hits = store.recall("when is the report due")
        assert hits and "deadline" in hits[0].content

    def test_unimplemented_memory_types_raise(self, store):
        """Declared but unbuilt memory types must fail loudly, not silently drop."""
        with pytest.raises(NotImplementedError):
            store.remember("x", memory_type=MemoryType.PROCEDURAL)

    def test_implemented_types_are_exactly_what_is_claimed(self):
        """The set is asserted explicitly so enabling a type is a deliberate
        act with a test change, never an accident that silently claims a
        capability the store cannot really serve."""
        assert IMPLEMENTED_MEMORY_TYPES == {
            MemoryType.WORKING, MemoryType.EPISODIC, MemoryType.SEMANTIC,
            MemoryType.FAILURE, MemoryType.COMMITMENT, MemoryType.DECISION}

    def test_the_majority_of_declared_types_remain_unimplemented(self):
        """Guards the honesty claim: most of the vision's 20 memory types are
        still not built, and the store must keep saying so."""
        assert len(IMPLEMENTED_MEMORY_TYPES) < len(list(MemoryType)) / 2

    def test_working_memory_is_bounded(self):
        wm = WorkingMemory(max_items=3)
        for i in range(10):
            wm.add(f"note {i}")
        assert wm.context().count("\n") == 2  # exactly 3 lines retained
        assert "note 9" in wm.context() and "note 0" not in wm.context()
