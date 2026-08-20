"""Input gain conditioning for speech recognition.

Why this exists: this machine's AMD microphone array captures very quietly -
measured ambient peak around 0.005 (-46 dBFS). Normalising toward a sane speech
level gives the recogniser a cleaner signal and makes the on-screen level meter
meaningful.

Measured honestly: Gemini Live turns out to be *robust* to quiet input - in an
A/B test it transcribed the same phrase correctly at 0.00036 RMS with and
without conditioning. So this is a quality improvement, not the fix for
misrecognition; the dominant cause of nonsense transcripts was an always-open
microphone interpreting ambient sound. See VoiceSession for that side.

The important subtlety: naive "always multiply by N" gain makes things *worse*,
because between words it amplifies the noise floor into something the model
tries to interpret as speech. So the gain is gated - it only lifts audio that
is plausibly speech, and leaves genuine silence alone.
"""

from __future__ import annotations

import array

TARGET_RMS = 0.09        # comfortable speech level for ASR, well short of clipping
# Measured on this machine: ambient silence sits near 0.0004 RMS and quiet
# speech only a little above it. An aggressive gate (0.0018 was tried) never
# engaged at all and left the gain pinned at its initial value - so the floor
# sits just above true digital silence instead.
NOISE_FLOOR = 0.00025    # below this, treat the block as silence and do not lift
MAX_GAIN = 24.0
MIN_GAIN = 1.0
ATTACK = 0.55            # drop gain quickly when a block gets loud (avoid clipping)
RELEASE = 0.06           # raise gain slowly, so levels do not pump between words
PEAK_CEILING = 0.97      # leave a little headroom before hard clip


class InputGain:
    """Gated automatic gain control over 16-bit mono PCM blocks."""

    def __init__(self) -> None:
        self._gain = 4.0
        self.last_rms_in = 0.0
        self.last_rms_out = 0.0

    def process(self, pcm: bytes) -> bytes:
        if not pcm:
            return pcm
        samples = array.array("h")
        samples.frombytes(pcm)
        if not samples:
            return pcm

        total = 0
        for s in samples:
            total += s * s
        rms = (total / len(samples)) ** 0.5 / 32768.0
        self.last_rms_in = rms

        # Silence: pass through untouched. Amplifying the noise floor here is
        # what makes a naive AGC hurt recognition rather than help it.
        if rms < NOISE_FLOOR:
            self.last_rms_out = rms
            return pcm

        desired = max(MIN_GAIN, min(MAX_GAIN, TARGET_RMS / rms))
        # Asymmetric smoothing: react fast to loud, ease into quiet.
        coeff = ATTACK if desired < self._gain else RELEASE
        self._gain += (desired - self._gain) * coeff

        peak = max(abs(s) for s in samples) / 32768.0
        if peak * self._gain > PEAK_CEILING:
            self._gain = PEAK_CEILING / max(peak, 1e-6)

        g = self._gain
        out = array.array("h", bytes(len(pcm)))
        for i, s in enumerate(samples):
            v = int(s * g)
            out[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)

        total_o = 0
        for s in out:
            total_o += s * s
        self.last_rms_out = (total_o / len(out)) ** 0.5 / 32768.0
        return out.tobytes()

    @property
    def gain(self) -> float:
        return self._gain
