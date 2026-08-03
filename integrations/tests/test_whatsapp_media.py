"""Unit tests for WhatsApp voice-note media preparation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.services import whatsapp_media as wa_media


def test_prepare_bytes_ogg_voice_passthrough():
    data = b"OggS-fake-opus"
    out, mime, name, is_voice = wa_media.prepare_bytes_for_meta(
        data=data,
        mime="audio/ogg; codecs=opus",
        filename="voice-1.ogg",
        is_voice_note=True,
    )
    assert out == data
    assert mime == "audio/ogg"
    assert name == "voice-1.ogg"
    assert is_voice is True


def test_prepare_bytes_non_voice_unchanged():
    data = b"not-a-voice"
    out, mime, name, is_voice = wa_media.prepare_bytes_for_meta(
        data=data,
        mime="audio/mp4",
        filename="clip.m4a",
        is_voice_note=False,
    )
    assert out == data
    assert mime == "audio/mp4"
    assert name == "clip.m4a"
    assert is_voice is False


@patch("integrations.services.whatsapp_media.convert_audio_to_ogg_opus")
def test_prepare_bytes_webm_converts_when_ffmpeg_ok(mock_convert, tmp_path):
    ogg = tmp_path / "out.ogg"
    ogg.write_bytes(b"converted-ogg")
    mock_convert.return_value = str(ogg)

    out, mime, name, is_voice = wa_media.prepare_bytes_for_meta(
        data=b"fake-webm",
        mime="audio/webm",
        filename="voice-2.webm",
        is_voice_note=True,
    )
    assert out == b"converted-ogg"
    assert mime == "audio/ogg"
    assert name == "voice-2.ogg"
    assert is_voice is True
    mock_convert.assert_called_once()


@patch("integrations.services.whatsapp_media.convert_audio_to_ogg_opus")
def test_prepare_bytes_mp4_converts_when_ffmpeg_ok(mock_convert, tmp_path):
    ogg = tmp_path / "out.ogg"
    ogg.write_bytes(b"converted-from-m4a")
    mock_convert.return_value = str(ogg)

    out, mime, name, is_voice = wa_media.prepare_bytes_for_meta(
        data=b"fake-m4a",
        mime="audio/mp4",
        filename="voice-safari.m4a",
        is_voice_note=True,
    )
    assert out == b"converted-from-m4a"
    assert mime == "audio/ogg"
    assert name == "voice-safari.ogg"
    assert is_voice is True


@patch("integrations.services.whatsapp_media.convert_audio_to_ogg_opus", return_value=None)
def test_prepare_bytes_webm_raises_without_ffmpeg(mock_convert):
    with pytest.raises(ValueError, match="Voice notes require OGG/Opus"):
        wa_media.prepare_bytes_for_meta(
            data=b"fake-webm",
            mime="audio/webm",
            filename="voice-3.webm",
            is_voice_note=True,
        )
    mock_convert.assert_called_once()


@patch("integrations.services.whatsapp_media.convert_audio_to_ogg_opus", return_value=None)
def test_prepare_bytes_mp4_raises_without_ffmpeg(mock_convert):
    with pytest.raises(ValueError, match="Voice notes require OGG/Opus"):
        wa_media.prepare_bytes_for_meta(
            data=b"fake-m4a",
            mime="audio/mp4",
            filename="voice-safari.m4a",
            is_voice_note=True,
        )
    mock_convert.assert_called_once()


def test_temp_suffix_for_audio_mime():
    assert wa_media._temp_suffix_for_audio_mime("audio/webm", "a.webm") == ".webm"
    assert wa_media._temp_suffix_for_audio_mime("audio/mp4", "voice.m4a") == ".m4a"
    assert wa_media._temp_suffix_for_audio_mime("audio/aac", "x.aac") == ".aac"
    assert wa_media._temp_suffix_for_audio_mime("audio/mpeg", "x.mp3") == ".mp3"


@patch("integrations.services.whatsapp_media.shutil.which", return_value=None)
def test_convert_audio_returns_none_without_ffmpeg(mock_which):
    assert wa_media.convert_audio_to_ogg_opus("/tmp/missing.webm") is None
    mock_which.assert_called_once_with("ffmpeg")
