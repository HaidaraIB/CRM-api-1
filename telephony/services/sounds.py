"""
Tenant audio for the PBX (custom IVR greetings; CRM-pbx contract section 2, sounds).

A greeting is uploaded as a WAV file: 16-bit PCM, mono, 8 kHz (the CRM-project
UI converts whatever the admin picks in the browser, so nothing is transcoded on
the CRM VPS). It is stored as raw G.711 A-law, the codec calls already use, so
Asterisk plays it with no transcoding at all. A-law is 8000 bytes a second; a
greeting is at most 60 s.

The compiler ships every greeting an IVR menu uses inside the tenant's artifact
(``sound_t<key>_<id8>.alaw``), sha256-checked by the node like any other file,
and the dialplan plays it by absolute path.
"""

from __future__ import annotations

import hashlib
import io
import sys
import wave
from array import array

SAMPLE_RATE = 8000
MIN_SECONDS = 0.5
MAX_SECONDS = 60
MAX_UPLOAD_BYTES = 44 + MAX_SECONDS * SAMPLE_RATE * 2 + 4096  # PCM16 WAV of the longest greeting, plus headers
MAX_SOUNDS_PER_TENANT = 20


class SoundError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- G.711 A-law (ITU-T G.711, as the reference g711.c) ------------------------

_SEG_END = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)


def _linear_to_alaw(sample: int) -> int:
    pcm = sample >> 3
    if pcm >= 0:
        mask = 0xD5
    else:
        mask, pcm = 0x55, -pcm - 1
    seg = next((i for i, end in enumerate(_SEG_END) if pcm <= end), 8)
    if seg >= 8:
        return 0x7F ^ mask
    aval = seg << 4
    aval |= (pcm >> 1) & 0x0F if seg < 2 else (pcm >> seg) & 0x0F
    return aval ^ mask


def _alaw_to_linear(value: int) -> int:
    value ^= 0x55
    t = (value & 0x0F) << 4
    seg = (value & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if value & 0x80 else -t


# Lookup tables: every 16-bit sample (indexed as unsigned) and every A-law byte.
_ENCODE = bytes(_linear_to_alaw(s - 65536 if s >= 32768 else s) for s in range(65536))
_DECODE = array("h", (_alaw_to_linear(b) for b in range(256)))


def pcm16_to_alaw(frames: bytes) -> bytes:
    samples = array("H")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":  # WAV is little-endian
        samples.byteswap()
    return bytes(_ENCODE[s] for s in samples)


def alaw_to_pcm16(data: bytes) -> bytes:
    out = array("h", (_DECODE[b] for b in data))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


# ---- upload / preview -------------------------------------------------------------


def parse_wav(raw: bytes) -> bytes:
    """
    A greeting upload → A-law bytes. Only 16-bit PCM, mono, 8 kHz is accepted;
    the error says exactly that, so an API caller knows what to send.
    """
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise SoundError("sound_too_long", f"A greeting is at most {MAX_SECONDS} seconds.")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            fmt = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype())
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError, ValueError) as exc:
        raise SoundError("sound_format", "Upload a WAV file (16-bit PCM, mono, 8 kHz).") from exc
    if fmt != (1, 2, SAMPLE_RATE, "NONE"):
        raise SoundError("sound_format", "Upload a WAV file (16-bit PCM, mono, 8 kHz).")
    seconds = len(frames) / 2 / SAMPLE_RATE
    if seconds < MIN_SECONDS:
        raise SoundError("sound_too_short", "The greeting is too short.")
    if seconds > MAX_SECONDS:
        raise SoundError("sound_too_long", f"A greeting is at most {MAX_SECONDS} seconds.")
    return pcm16_to_alaw(frames)


def to_wav(alaw: bytes) -> bytes:
    """A stored greeting as a PCM WAV any browser plays (admin preview)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(alaw_to_pcm16(alaw))
    return buf.getvalue()


def duration_ms(alaw: bytes) -> int:
    return len(alaw) * 1000 // SAMPLE_RATE


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
