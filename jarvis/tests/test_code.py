"""Code execution tests.

This is the most dangerous ability in the system, so the safety properties are
tested harder than the happy path.
"""

from __future__ import annotations

import pytest

from jarvis.environments.code import CodeEnvironment


@pytest.fixture
def env(tmp_path):
    return CodeEnvironment(workspace=tmp_path)


class TestSafety:
    def test_destructive_source_is_blocked_before_running(self, env):
        r = env.act("run_python",
                    {"code": 'import shutil; shutil.rmtree("C:/Windows")'})
        assert not r.ok and r.error == "blocked"

    def test_shell_destructive_source_is_blocked(self, env):
        r = env.act("run_powershell", {"code": "Remove-Item -Recurse -Force C:/"})
        assert not r.ok and r.error == "blocked"

    def test_infinite_loop_is_stopped_by_timeout(self, env):
        r = env.act("run_python", {"code": "while True: pass", "timeout": 2})
        assert not r.ok and r.error == "timeout"

    def test_timeout_is_capped(self, env):
        from jarvis.environments.code import MAX_TIMEOUT_S
        assert env._timeout({"timeout": 99999}) == MAX_TIMEOUT_S
        assert env._timeout({"timeout": "nonsense"}) > 0

    def test_suspect_calls_are_surfaced_for_the_human(self, env):
        # Not blocked - deleting a temp file it just made is legitimate - but
        # the human approving must be able to see it.
        r = env.act("run_python",
                    {"code": "import os\np='x.txt'\nopen(p,'w').close()\n"
                             "os.remove(p)\nprint('ok')"})
        assert "flagged_calls" in r.evidence

    def test_no_sandbox_claim_is_made(self, env):
        limits = " ".join(env.constraints()).lower()
        # Claiming a sandbox that does not exist would be the dangerous lie
        # here, so the constraint must say the opposite plainly.
        assert "not a security sandbox" in limits

    def test_empty_code_is_refused(self, env):
        assert env.act("run_python", {"code": "   "}).error == "missing_code"

    def test_unknown_ability_refused(self, env):
        assert env.act("run_ruby", {"code": "x"}).error == "unregistered"


class TestExecution:
    def test_python_returns_real_output(self, env):
        r = env.act("run_python", {"code": "print(sum(range(101)))"})
        assert r.ok and "5050" in r.summary

    def test_nonzero_exit_is_a_failure_not_a_success(self, env):
        r = env.act("run_python", {"code": "import sys; sys.exit(3)"})
        assert not r.ok
        assert r.evidence["exit_code"] == 3

    def test_exception_reports_the_real_error(self, env):
        r = env.act("run_python", {"code": "raise ValueError('boom')"})
        assert not r.ok and "boom" in r.evidence["stderr"]

    def test_powershell_runs(self, env):
        r = env.act("run_powershell", {"code": "Write-Output 42"})
        assert r.ok and "42" in r.summary

    def test_scratch_file_is_cleaned_up(self, env, tmp_path):
        env.act("run_python", {"code": "print(1)"})
        assert not list(tmp_path.glob("snippet_*.py"))


class TestVerification:
    def test_exit_zero_verifies(self, env):
        r = env.act("run_python", {"code": "print('hi')"})
        assert env.verify("run_python", {}, r).verified

    def test_a_cheerful_message_before_a_bad_exit_still_fails(self, env):
        """Output must not be mistaken for success."""
        r = env.act("run_python",
                    {"code": "print('all good!')\nimport sys; sys.exit(1)"})
        assert not env.verify("run_python", {}, r).verified
