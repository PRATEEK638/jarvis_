"""Voice preamble tests.

Silence during a slow tool call reads as a hang in a spoken conversation. The
tests below pin both halves of the behaviour: slow calls get acknowledged, and
fast ones are not padded with a pointless "one moment" that would make them
feel slower than they are.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.voice.live import VoiceSession


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    def spoken(self):
        out = []
        for msg in self.sent:
            for turn in (msg.get("clientContent") or {}).get("turns", []):
                for part in turn.get("parts", []):
                    out.append(part.get("text", ""))
        return out

    def tool_results(self):
        return [m for m in self.sent if "toolResponse" in m]


def session(execute, **kw):
    return VoiceSession(execute, on_event=lambda *_a: None, **kw)


class TestSlowCalls:
    def test_slow_call_is_acknowledged_aloud(self):
        import time

        def slow(_name, _args):
            time.sleep(1.2)
            return "done"

        s = session(slow)
        ws = FakeWS()
        asyncio.run(s._run_tools(ws, [{"id": "1", "name": "research",
                                       "args": {}}]))
        assert any("look that up" in t.lower() for t in ws.spoken())
        assert ws.tool_results()

    def test_the_preamble_names_the_actual_activity(self):
        import time

        def slow(_name, _args):
            time.sleep(1.2)
            return "done"

        ws = FakeWS()
        asyncio.run(session(slow)._run_tools(
            ws, [{"id": "1", "name": "find_files", "args": {}}]))
        assert any("files" in t.lower() for t in ws.spoken())


class TestFastCalls:
    def test_fast_call_is_not_padded(self):
        ws = FakeWS()
        asyncio.run(session(lambda *_a: "instant")._run_tools(
            ws, [{"id": "1", "name": "research", "args": {}}]))
        assert ws.spoken() == []
        assert ws.tool_results()

    def test_quick_ability_never_gets_a_preamble(self):
        """system_state is not in the slow set, so it must not wait at all."""
        import time

        def slowish(_name, _args):
            time.sleep(1.0)
            return "cpu 5%"

        ws = FakeWS()
        asyncio.run(session(slowish)._run_tools(
            ws, [{"id": "1", "name": "system_state", "args": {}}]))
        assert ws.spoken() == []


class TestRobustness:
    def test_a_failing_tool_still_returns_a_result(self):
        def boom(_name, _args):
            raise RuntimeError("exploded")

        ws = FakeWS()
        asyncio.run(session(boom)._run_tools(
            ws, [{"id": "1", "name": "research", "args": {}}]))
        results = ws.tool_results()
        assert results
        payload = results[0]["toolResponse"]["functionResponses"][0]
        assert "exploded" in payload["response"]["result"]

    def test_a_broken_socket_during_the_preamble_does_not_lose_the_result(self):
        """The preamble is cosmetic; the tool result is not."""
        import time

        class BrokenOnFirstSend(FakeWS):
            def __init__(self):
                super().__init__()
                self._failed = False

            async def send(self, payload):
                if not self._failed and "clientContent" in payload:
                    self._failed = True
                    raise ConnectionError("socket died")
                await super().send(payload)

        def slow(_name, _args):
            time.sleep(1.2)
            return "done"

        ws = BrokenOnFirstSend()
        asyncio.run(session(slow)._run_tools(
            ws, [{"id": "1", "name": "research", "args": {}}]))
        assert ws.tool_results()
