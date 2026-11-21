"""Tests for the transcription utilities.

The mock transcriber is exercised directly. For the Whisper transcriber we
simulate the CLI output by monkey‑patching ``subprocess.run`` – this avoids the need
for the real binary or model files.
"""

import json
import types
from unittest import mock

import pytest

from backend.app.media.transcription import (
    MockTranscriber,
    WhisperTranscriber,
    TranscriptionError,
)


def test_mock_transcriber_returns_default_structure():
    transcriber = MockTranscriber()
    result = transcriber.transcribe("unused_path.wav")
    assert isinstance(result, list)
    assert all(isinstance(w, dict) for w in result)
    # Check required keys
    for w in result:
        assert {"word", "start", "end", "confidence"}.issubset(w.keys())


def test_mock_transcriber_custom_result():
    custom = [{"word": "test", "start": 0.1, "end": 0.3, "confidence": 0.99}]
    transcriber = MockTranscriber(result=custom)
    assert transcriber.transcribe("x") == custom


def _make_fake_whisper_output(segments):
    return json.dumps({"segments": segments})


def test_whisper_transcriber_parses_nested_words():
    # Simulated Whisper JSON with nested words and probabilities.
    segments = [
        {
            "id": 0,
            "seek": 0,
            "start": 0.0,
            "end": 2.0,
            "text": "hello world",
            "words": [
                {"word": "hello", "start": 0.0, "end": 1.0, "probability": 0.92},
                {"word": "world", "start": 1.0, "end": 2.0, "probability": 0.88},
            ],
        }
    ]
    fake_output = _make_fake_whisper_output(segments)

    # Patch subprocess.run to return the fake output
    fake_completed = types.SimpleNamespace(returncode=0, stdout=fake_output, stderr="")
    with mock.patch("shutil.which", return_value="whisper"), \
         mock.patch("subprocess.run", return_value=fake_completed):
        transcriber = WhisperTranscriber()
        words = transcriber.transcribe("dummy.wav")

    assert len(words) == 2
    hello, world = words
    assert hello["word"] == "hello"
    assert hello["start"] == 0.0
    assert hello["end"] == 1.0
    assert hello["confidence"] == 0.92
    assert world["word"] == "world"
    assert world["confidence"] == 0.88


def test_whisper_transcriber_missing_words_raises():
    # Whisper output where a segment lacks the "words" array.
    segments = [{"id": 0, "start": 0.0, "end": 1.0, "text": "no words"}]
    fake_output = _make_fake_whisper_output(segments)
    fake_completed = types.SimpleNamespace(returncode=0, stdout=fake_output, stderr="")
    with mock.patch("shutil.which", return_value="whisper"), \
         mock.patch("subprocess.run", return_value=fake_completed):
        transcriber = WhisperTranscriber()
        with pytest.raises(TranscriptionError) as excinfo:
            transcriber.transcribe("dummy.wav")
    assert excinfo.value.code == "WHISPER_NO_WORD_TIMESTAMPS"


def test_whisper_transcriber_missing_binary_raises():
    with mock.patch("shutil.which", return_value=None):
        with pytest.raises(TranscriptionError) as excinfo:
            WhisperTranscriber()
    assert excinfo.value.code == "WHISPER_NOT_FOUND"
