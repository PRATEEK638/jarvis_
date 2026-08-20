"""Guardrails and capability-gap tests.

These are the safety-critical paths: they must hold regardless of what any model
proposes, so they are tested directly rather than through the orchestrator.
"""

from __future__ import annotations

import pytest

from jarvis.core import coverage
from jarvis.policy import guardrails


class TestDestructiveCommands:
    """The two standing rules: never delete the user's files, never damage the OS."""

    @pytest.mark.parametrize("command", [
        "Remove-Item -Recurse -Force C:\\Users\\heman\\Documents",
        "rm -rf /",
        "rm -rf ~/Desktop",
        "format C:",
        "del /s /q C:\\data",
        "rmdir /s C:\\data",
        "vssadmin delete shadows /all",
        "bcdedit /set safeboot minimal",
        "diskpart",
        "Set-MpPreference -DisableRealtimeMonitoring $true",
        "reg delete HKLM\\Software\\Foo",
        "shutdown /s /t 0",
        "Restart-Computer",
        "cipher /w:C",
        "import shutil; shutil.rmtree('C:/Windows')",
    ])
    def test_destructive_command_is_refused(self, command):
        with pytest.raises(guardrails.Blocked):
            guardrails.check_command(command)

    @pytest.mark.parametrize("command", [
        "Get-Process | Select-Object -First 5",
        "Get-ChildItem C:\\Users\\heman\\Desktop",
        "echo hello",
        "python -c \"print(2+2)\"",
        "Get-Date",
    ])
    def test_harmless_command_is_allowed(self, command):
        guardrails.check_command(command)  # must not raise


class TestProtectedPaths:
    @pytest.mark.parametrize("path", [
        r"C:\Windows\System32\drivers\etc\hosts",
        r"C:\Windows\notepad.exe",
        r"C:\Program Files\something\x.dll",
    ])
    def test_writing_to_system_location_is_refused(self, path):
        with pytest.raises(guardrails.Blocked):
            guardrails.check_path(path, writing=True)

    def test_user_paths_are_allowed(self, tmp_path):
        guardrails.check_path(tmp_path / "report.txt", writing=True)

    def test_reads_are_not_blocked(self):
        guardrails.check_path(r"C:\Windows\System32\drivers\etc\hosts",
                              writing=False)


class TestAbilityGate:
    def test_deletion_abilities_are_refused(self):
        with pytest.raises(guardrails.Blocked):
            guardrails.check_ability("delete_file", {"path": "x.txt"})

    def test_destructive_argument_is_caught(self):
        with pytest.raises(guardrails.Blocked):
            guardrails.check_ability("run_command",
                                     {"command": "Remove-Item -Recurse C:\\x"})

    def test_normal_ability_passes(self, tmp_path):
        guardrails.check_ability("create_file",
                                 {"path": str(tmp_path / "a.txt"),
                                  "content": "hello"})


class TestCapabilityGap:
    """A model that cannot do something must not substitute something else.

    Regression guard: llama3:8b answered "send an email to my professor" by
    planning create_file. Coverage is therefore checked deterministically,
    before any model sees the request.
    """

    @pytest.mark.parametrize("request_text,expected", [
        ("send an email to my professor", "sending email"),
        ("email it to hr@example.com", "sending email"),
        ("send a whatsapp message to mom", "sending chat"),
        ("post this on linkedin", "posting to social media"),
        ("schedule a meeting with the team", "calendar"),
        ("delete all files in downloads", "deleting files"),
        ("print this document", "printing"),
    ])
    def test_out_of_scope_requests_are_named(self, request_text, expected):
        gap = coverage.detect_gap(request_text)
        assert gap is not None, f"should have been flagged: {request_text}"
        assert expected in gap.lower()

    @pytest.mark.parametrize("request_text", [
        "create a folder called reports on my desktop",
        "what is my cpu usage",
        "find files with notes in the name",
        "search the web for the latest python release",
        "open notepad",
        "move report.txt into the archive folder",
        "remember that my demo is today",
    ])
    def test_supported_requests_pass_through(self, request_text):
        assert coverage.detect_gap(request_text) is None
