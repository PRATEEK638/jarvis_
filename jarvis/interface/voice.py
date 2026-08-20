"""Voice interface.

Speech in, speech out, wrapped around the same orchestrator the CLI uses — so
voice inherits every capability, guardrail and verification step rather than
being a parallel implementation.

  Listening   sounddevice captures 16 kHz mono PCM, with silence detection so
              the user simply stops talking instead of pressing a key.
  Transcription
              The audio is sent to Gemini as inline WAV. Gemini accepts audio
              natively, so no separate speech-to-text service or key is needed.
  Speaking    Windows SAPI via COM. Built into the OS, no dependency, no network,
              and it can be interrupted.

Honest scope: this is half-duplex — JARVIS listens, then thinks, then speaks.
It is NOT the full Gemini Live bidirectional audio stream (continuous listening
with barge-in mid-sentence); that needs a persistent websocket session and an
audio playback pipeline, and is not implemented here.
"""

from __future__ import annotations

import base64
import io
import json
import queue
import sys
import threading
import time
import wave
from typing import Any

import numpy as np
import requests

from jarvis.config import settings
from jarvis.core.events import emit

SAMPLE_RATE = 16_000
CHANNELS = 1
BLOCK_MS = 100
SILENCE_RMS = 0.012          # below this counts as silence
SILENCE_HANG_S = 1.4         # stop after this much continuous quiet
MAX_UTTERANCE_S = 30
MIN_UTTERANCE_S = 0.4

_SD_ERROR: str | None = None
try:
    import sounddevice as sd
    _HAVE_MIC = True
except Exception as exc:  # noqa: BLE001
    _HAVE_MIC = False
    _SD_ERROR = str(exc)

_TTS_ERROR: str | None = None
try:
    import win32com.client  # type: ignore
    _HAVE_TTS = True
except Exception as exc:  # noqa: BLE001
    _HAVE_TTS = False
    _TTS_ERROR = str(exc)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

class Speaker:
    """Windows SAPI text-to-speech, on a worker thread so it can be interrupted."""

    # SAPI flags: 1 = async, 2 = purge anything already queued.
    _ASYNC = 1
    _PURGE = 2

    def __init__(self, voice_hint: str = "Zira", rate: int = 1) -> None:
        self.available = _HAVE_TTS
        self.error = _TTS_ERROR
        self._voice = None
        if not _HAVE_TTS:
            return
        try:
            # COM objects are apartment-bound, so the handle is created lazily
            # on whichever thread actually speaks.
            self._voice_hint = voice_hint
            self._rate = rate
            self._voice = self._make_voice()
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self.error = str(exc)

    def _make_voice(self):
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voice.Rate = self._rate
        for candidate in voice.GetVoices():
            if self._voice_hint.lower() in candidate.GetDescription().lower():
                voice.Voice = candidate
                break
        return voice

    def say(self, text: str, *, block: bool = True) -> None:
        if not self.available or not text.strip():
            return
        clean = _speakable(text)
        try:
            flags = 0 if block else self._ASYNC
            self._voice.Speak(clean, flags)
        except Exception as exc:  # noqa: BLE001 - speech must never break a run
            emit("voice.tts_failed", error=str(exc))

    def stop(self) -> None:
        if self.available:
            try:
                self._voice.Speak("", self._ASYNC | self._PURGE)
            except Exception:  # noqa: BLE001
                pass


def _speakable(text: str) -> str:
    """Strip things that sound wrong when read aloud."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Drop the CLI's status markers and long absolute paths.
        for marker in ("[OK]", "[FAILED]", "[REFUSED]", "[SKIPPED]"):
            line = line.replace(marker, "")
        out.append(line.strip())
    spoken = ". ".join(out)
    return spoken[:1200]


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

class Microphone:
    """Record one utterance, ending when the speaker falls silent."""

    def __init__(self) -> None:
        self.available = _HAVE_MIC
        self.error = _SD_ERROR

    def record_utterance(self, *, on_level=None) -> bytes | None:
        """Capture until silence. Returns 16-bit PCM WAV bytes, or None."""
        if not self.available:
            return None
        frames: list[np.ndarray] = []
        q: queue.Queue[np.ndarray] = queue.Queue()

        def callback(indata, _frames, _t, status):  # noqa: ANN001
            if status:
                emit("voice.input_status", status=str(status))
            q.put(indata.copy())

        block = int(SAMPLE_RATE * BLOCK_MS / 1000)
        started = time.time()
        last_loud = None
        heard_anything = False

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                            dtype="float32", blocksize=block,
                            callback=callback):
            while True:
                try:
                    chunk = q.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - started > MAX_UTTERANCE_S:
                        break
                    continue
                frames.append(chunk)
                rms = float(np.sqrt(np.mean(np.square(chunk))))
                if on_level:
                    on_level(rms)
                now = time.time()
                if rms >= SILENCE_RMS:
                    heard_anything = True
                    last_loud = now
                elif heard_anything and last_loud and \
                        now - last_loud >= SILENCE_HANG_S:
                    break
                if now - started > MAX_UTTERANCE_S:
                    break

        if not heard_anything or not frames:
            return None
        audio = np.concatenate(frames, axis=0)
        if len(audio) / SAMPLE_RATE < MIN_UTTERANCE_S:
            return None
        return _to_wav(audio)


def _to_wav(audio: "np.ndarray") -> bytes:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


class Transcriber:
    """Speech to text via Gemini's native audio input.

    Uses the dedicated voice key when one is configured, so heavy voice traffic
    does not consume the quota the planning tier relies on.
    """

    def __init__(self) -> None:
        self.key = settings.get_key("gemini_voice") or settings.get_key("gemini")
        self.model = settings.GEMINI_TRANSCRIBE_MODEL

    @property
    def available(self) -> bool:
        return bool(self.key)

    def transcribe(self, wav_bytes: bytes) -> str:
        if not self.key:
            raise RuntimeError("no Gemini key configured for transcription")
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Transcribe this audio exactly. Reply with only the "
                             "transcription, no commentary. If there is no "
                             "intelligible speech, reply with an empty string."},
                    {"inline_data": {"mime_type": "audio/wav",
                                     "data": base64.b64encode(wav_bytes).decode()}},
                ],
            }],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 512},
        }
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.key}")
        resp = requests.post(url, data=json.dumps(body),
                             headers={"Content-Type": "application/json"},
                             timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        candidates = resp.json().get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip()


def status() -> dict[str, Any]:
    t = Transcriber()
    return {
        "microphone": {"available": _HAVE_MIC, "error": _SD_ERROR},
        "speech_out": {"available": _HAVE_TTS, "error": _TTS_ERROR},
        "transcription": {"available": t.available, "model": t.model,
                          "dedicated_key": bool(settings.get_key("gemini_voice"))},
    }
