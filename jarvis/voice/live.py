"""Spoken interface built on the Gemini Live API.

This is not text-to-speech bolted onto a chat loop. The Live API carries audio
in both directions over one websocket: microphone PCM goes up continuously, the
model's own voice comes back down as PCM, and the model can interrupt itself
when the user starts talking. That is what makes it sound like a conversation
rather than a walkie-talkie.

The part that matters for JARVIS: every ability in the registry is declared to
the session as a callable function. When the model decides an action is needed
it emits a function call, this module executes it through the *same*
orchestrator path the CLI uses -- same guardrails, same verification, same
audit trail -- and sends the real result back. So speech triggers real work on
this machine, not a description of work.

Audio format is fixed by the API: 16 kHz signed 16-bit mono up, 24 kHz down.
"""

from __future__ import annotations

import asyncio
import base64
import json
import queue
import threading
from typing import Any, AsyncIterator, Callable

from jarvis.abilities import registry as ability_registry
from jarvis.config import settings
from jarvis.core.contracts import Risk
from jarvis.core.events import emit
from jarvis.voice.gain import InputGain

# Audio constants dictated by the Live API.
INPUT_RATE = 16_000
OUTPUT_RATE = 24_000
CHANNELS = 1
BLOCK = 1600                      # 100 ms at 16 kHz: small enough to feel live

LIVE_HOST = "generativelanguage.googleapis.com"
LIVE_PATH = ("/ws/google.ai.generativelanguage.v1beta."
             "GenerativeService.BidiGenerateContent")

# Charon is the steadiest of the prebuilt voices; the alternatives (Puck, Kore,
# Fenrir, Aoede) are selectable through settings if a different character suits.
DEFAULT_VOICE = "Charon"

PERSONA = """You are JARVIS, a capable assistant with direct control of this
Windows machine. You are speaking aloud, so keep replies short and natural --
one or two sentences unless detail is genuinely requested. Never read out file
paths character by character.

You have real tools. When the user asks for something actionable, call the tool
rather than describing what could be done. After a tool returns, state plainly
what actually happened, using the result you were given. If a tool reports a
failure, say so; never claim success you were not told about.

Do not narrate your reasoning or announce which tool you are about to use.
Act, then report.

CRITICAL - the microphone is always open, so you will hear things that are not
addressed to you: background conversation, video or music playback, typing, and
half-caught fragments of speech.

- If what you heard is not clearly an instruction directed at you, say nothing
  at all and take no action. Silence is the correct response to ambient noise.
- Never guess at an instruction from a fragment. If you caught only part of
  something and it might matter, ask a single short question rather than
  inventing a task ("Sorry, what was that?").
- Never invent a subject the user did not mention. If you did not hear which
  files, which app, or which folder, ask - do not assume one.
- A single word, a filler sound, or punctuation alone is not an instruction.

The user may speak English, Hindi, or a mix of both. Understand either, and
reply in whichever language they used."""


def voice_available() -> tuple[bool, str]:
    """Can a voice session start right now? Returns (ok, reason-if-not)."""
    try:
        import sounddevice        # noqa: F401
        import websockets         # noqa: F401
    except ImportError as exc:
        return False, f"missing dependency: {exc.name}"
    key = settings.get_key("gemini_voice") or settings.get_key("gemini")
    if not key:
        return False, "no Gemini key configured for voice"
    try:
        import sounddevice as sd
        sd.query_devices(kind="input")
    except Exception as exc:      # noqa: BLE001
        return False, f"no usable microphone: {exc}"
    return True, ""


def _json_schema_for(ability) -> dict[str, Any]:
    """Turn an Ability's parameter description into a Gemini function schema."""
    properties: dict[str, Any] = {}
    for name, description in (ability.params or {}).items():
        # Every JARVIS parameter is a scalar today; booleans are described as
        # such in the ability text, so detect them rather than guessing.
        kind = "boolean" if description.strip().lower().startswith("true to") \
            else "string"
        properties[name] = {"type": kind, "description": description}
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if ability.required:
        schema["required"] = list(ability.required)
    return schema


def tool_declarations() -> list[dict[str, Any]]:
    """Every ability, described so the model can call it by name."""
    declarations = []
    for ability in ability_registry.ABILITIES:
        declarations.append({
            "name": ability.id,
            "description": ability.objective,
            "parameters": _json_schema_for(ability),
        })
    return declarations


def _rms_level(pcm: bytes) -> float:
    """Amplitude of a 16-bit PCM block, 0.0-1.0.

    Used to drive the visualiser from the real signal rather than animating
    something that merely looks like audio. Pure stdlib so no numpy dependency
    is forced on the voice path.
    """
    if not pcm:
        return 0.0
    count = len(pcm) // 2
    if count == 0:
        return 0.0
    total = 0
    # Sampling every 8th frame is plenty for a level meter and keeps this cheap
    # enough to run on the audio callback thread.
    step = max(1, count // 256)
    taken = 0
    for i in range(0, count, step):
        sample = int.from_bytes(pcm[i * 2:i * 2 + 2], "little", signed=True)
        total += sample * sample
        taken += 1
    if not taken:
        return 0.0
    return min(1.0, ((total / taken) ** 0.5) / 32768.0 * 4.0)


class _Speaker:
    """Plays model audio, and can be silenced instantly for barge-in."""

    def __init__(self, on_level=None) -> None:
        import sounddevice as sd
        self._on_level = on_level
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._stream = sd.RawOutputStream(
            samplerate=OUTPUT_RATE, channels=CHANNELS, dtype="int16",
            blocksize=0)
        self._stream.start()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                continue
            try:
                self._stream.write(chunk)
            except Exception:      # noqa: BLE001 - device can vanish mid-call
                break

    def play(self, pcm: bytes) -> None:
        if self._on_level:
            self._on_level(_rms_level(pcm))
        self._q.put(pcm)

    def interrupt(self) -> None:
        """Drop everything still queued. Used when the user starts speaking."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def close(self) -> None:
        self._stop.set()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:          # noqa: BLE001
            pass


class VoiceSession:
    """One spoken conversation, with tool calls executed for real.

    `execute` is injected rather than imported so the session runs against the
    same orchestrator the CLI uses, and so tests can substitute a recorder.
    """

    def __init__(
        self,
        execute: Callable[[str, dict[str, Any]], str],
        *,
        on_event: Callable[[str, str], None] | None = None,
        voice: str | None = None,
        mic_chunks: AsyncIterator[bytes] | None = None,
        audio_out: Callable[[bytes], None] | None = None,
        on_interrupt: Callable[[], None] | None = None,
        on_input_level: Callable[[float], None] | None = None,
        on_output_level: Callable[[float], None] | None = None,
    ) -> None:
        """
        `mic_chunks` / `audio_out` / `on_interrupt` let a non-native transport
        (the browser, over a websocket) plug into the exact same Gemini Live
        session and tool-execution path used by the desktop mic/speaker mode,
        instead of a second implementation of the Live protocol. When they are
        omitted, behaviour is unchanged: this machine's own microphone and
        speakers are used, as before.
        """
        self._execute = execute
        self._on_event = on_event or (lambda kind, text: None)
        self._voice = voice or settings.VOICE_NAME
        self._speaker: _Speaker | None = None
        self._mic_queue: asyncio.Queue[bytes] | None = None
        self._stop = threading.Event()
        self._external_mic = mic_chunks
        self._audio_out = audio_out
        self._on_interrupt = on_interrupt
        # Optional amplitude taps for a visualiser. Called from the audio
        # callback thread, so implementations must be non-blocking.
        self._on_input_level = on_input_level
        self._on_output_level = on_output_level
        # This machine's microphone captures very quietly; without conditioning
        # the model receives a near-silent stream and invents speech. Applied to
        # both transports so browser and native behave identically.
        self._gain = InputGain()

    # -- public -----------------------------------------------------------

    def run(self) -> None:
        """Block until the user stops the session. Opens its own event loop -
        for the native desktop mic/speaker transport, called from plain code."""
        asyncio.run(self._main())

    async def run_async(self) -> None:
        """Same as `run()`, but awaited from inside an existing event loop -
        for the browser transport, called from a FastAPI websocket handler."""
        await self._main()

    def stop(self) -> None:
        self._stop.set()

    # -- internals --------------------------------------------------------

    def _setup_message(self) -> dict[str, Any]:
        return {
            "setup": {
                "model": f"models/{settings.GEMINI_VOICE_MODEL}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": self._voice}
                        }
                    },
                },
                "systemInstruction": {"parts": [{"text": PERSONA}]},
                "tools": [{"functionDeclarations": tool_declarations()}],
                # Let the API detect speech boundaries; doing VAD locally would
                # duplicate work the model already does better.
                # Ask for both transcripts so the on-screen captions match what
                # was actually heard and said, rather than being guessed.
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
                "realtimeInputConfig": {
                    # Tighter than the defaults on purpose. With an always-open
                    # microphone the default sensitivity treats room noise and
                    # brief sounds as the start of a turn, which is what made
                    # JARVIS answer things nobody said. Requiring a higher start
                    # threshold and a longer silence before ending a turn means
                    # it waits for an actual sentence.
                    "automaticActivityDetection": {
                        "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
                        "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
                        "prefixPaddingMs": 300,
                        "silenceDurationMs": 900,
                    },
                    "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
                },
            }
        }

    async def _main(self) -> None:
        import websockets

        key = settings.get_key("gemini_voice") or settings.get_key("gemini")
        url = f"wss://{LIVE_HOST}{LIVE_PATH}?key={key}"

        loop = asyncio.get_running_loop()
        self._mic_queue = asyncio.Queue(maxsize=50)

        mic = None
        mic_pump_task = None
        if self._external_mic is not None:
            # Browser transport: chunks arrive already decoded, pushed in by a
            # FastAPI websocket handler. No local audio device is touched.
            async def _pump_external() -> None:
                assert self._external_mic is not None
                async for chunk in self._external_mic:
                    try:
                        self._mic_queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        pass       # a dropped 100 ms block is better than lag
            mic_pump_task = asyncio.create_task(_pump_external())
        else:
            import sounddevice as sd

            def on_mic(indata, _frames, _time, status) -> None:
                if status:
                    emit("voice.mic_status", status=str(status))
                if self._on_input_level:
                    self._on_input_level(_rms_level(bytes(indata)))
                # Called on the audio thread: hand off without blocking it.
                try:
                    loop.call_soon_threadsafe(
                        self._mic_queue.put_nowait, bytes(indata))
                except (asyncio.QueueFull, RuntimeError):
                    pass           # a dropped 100 ms block is better than lag

            mic = sd.RawInputStream(samplerate=INPUT_RATE, channels=CHANNELS,
                                    dtype="int16", blocksize=BLOCK,
                                    callback=on_mic)
            mic.start()

        if self._audio_out is None:
            self._speaker = _Speaker(on_level=self._on_output_level)

        emit("voice.session_start", model=settings.GEMINI_VOICE_MODEL,
             voice=self._voice, transport="browser" if mic is None else "native")
        self._on_event("status", "listening")

        try:
            async with websockets.connect(url, max_size=None,
                                          ping_interval=20) as ws:
                await ws.send(json.dumps(self._setup_message()))
                await self._await_setup(ws)
                tasks = {asyncio.create_task(self._send_audio(ws)),
                        asyncio.create_task(self._receive(ws))}
                if mic_pump_task is not None:
                    tasks.add(mic_pump_task)
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
        finally:
            if mic is not None:
                mic.stop()
                mic.close()
            if self._speaker:
                self._speaker.close()
            emit("voice.session_end")
            self._on_event("status", "stopped")

    @staticmethod
    async def _await_setup(ws) -> None:
        """The API answers setup before it will accept audio."""
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        message = json.loads(raw) if isinstance(raw, str) else json.loads(
            raw.decode("utf-8"))
        if "setupComplete" not in message:
            raise RuntimeError(f"voice setup refused: {str(message)[:300]}")

    async def _send_audio(self, ws) -> None:
        assert self._mic_queue is not None
        frames = 0
        while not self._stop.is_set():
            chunk = await self._mic_queue.get()
            chunk = self._gain.process(chunk)
            frames += 1
            if frames % 50 == 0:      # roughly every 5 s of audio
                emit("voice.input_gain", gain=round(self._gain.gain, 2),
                     rms_in=round(self._gain.last_rms_in, 5),
                     rms_out=round(self._gain.last_rms_out, 5))
            await ws.send(json.dumps({
                "realtimeInput": {
                    "audio": {
                        "mimeType": f"audio/pcm;rate={INPUT_RATE}",
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }
                }
            }))

    async def _receive(self, ws) -> None:
        async for raw in ws:
            if self._stop.is_set():
                return
            message = json.loads(raw) if isinstance(raw, str) else \
                json.loads(raw.decode("utf-8"))
            await self._handle(ws, message)

    async def _handle(self, ws, message: dict[str, Any]) -> None:
        content = message.get("serverContent") or {}

        if content.get("interrupted"):
            # The user talked over the reply: stop speaking immediately.
            if self._on_interrupt:
                self._on_interrupt()
            elif self._speaker:
                self._speaker.interrupt()
            self._on_event("status", "listening")
            return

        turn = content.get("modelTurn") or {}
        for part in turn.get("parts", []):
            inline = part.get("inlineData") or {}
            if inline.get("data"):
                pcm = base64.b64decode(inline["data"])
                if self._audio_out:
                    self._audio_out(pcm)
                elif self._speaker:
                    self._speaker.play(pcm)
            if part.get("text"):
                self._on_event("jarvis", part["text"])

        # Transcripts arrive separately from audio; surface them for the UI.
        if content.get("inputTranscription", {}).get("text"):
            self._on_event("you", content["inputTranscription"]["text"])
        if content.get("outputTranscription", {}).get("text"):
            self._on_event("jarvis", content["outputTranscription"]["text"])

        calls = (message.get("toolCall") or {}).get("functionCalls") or []
        if calls:
            await self._run_tools(ws, calls)

    async def _run_tools(self, ws, calls: list[dict[str, Any]]) -> None:
        """Execute requested abilities and return their real results."""
        responses = []
        for call in calls:
            name = call.get("name", "")
            args = call.get("args") or {}
            self._on_event("action", f"{name} {json.dumps(args, default=str)[:90]}")
            emit("voice.tool_call", ability=name, args=args)
            try:
                # Executed off the event loop: file and shell work is blocking.
                result = await asyncio.to_thread(self._execute, name, args)
            except Exception as exc:                      # noqa: BLE001
                result = f"failed: {type(exc).__name__}: {exc}"
            self._on_event("result", result[:200])
            responses.append({
                "id": call.get("id"),
                "name": name,
                "response": {"result": result},
            })
        await ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))
