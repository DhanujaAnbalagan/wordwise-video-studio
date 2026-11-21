"""Transcription utilities for VideoFlow AI.

Provides a mock transcriber for testing and a thin wrapper around the
`whisper` CLI (OpenAI Whisper). The wrapper parses the JSON output that
contains nested ``segments[].words[]`` entries, maps the ``probability``
field to ``confidence`` and preserves the true word‑level timestamps.

Error handling:
- ``WHISPER_NOT_FOUND`` – Whisper binary not on ``PATH``.
- ``WHISPER_TIMEOUT`` – CLI exceeded ``settings.whisper_timeout_s``.
- ``WHISPER_NO_SEGMENTS`` – No ``segments`` field in output.
- ``WHISPER_NO_WORD_TIMESTAMPS`` – ``words`` missing or missing timestamps.
- ``WHISPER_FAILED`` – Non‑zero exit code.
- ``WHISPER_INVALID_JSON`` – Output could not be parsed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, List, Dict

from ..config import get_settings


class TranscriptionError(Exception):
    """Structured error raised by the transcribers.

    Attributes
    ----------
    code: str
        Machine‑readable error identifier.
    message: str
        Human‑readable description.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MockTranscriber:
    """Deterministic stub used by the test suite.

    Returns a fixed list of word dictionaries so that higher‑level logic can be
    exercised without invoking the actual Whisper binary.
    """

    def __init__(self, result: List[Dict[str, Any]] | None = None):
        # A minimal realistic default result.
        self._result = result or [
            {"word": "hello", "start": 0.0, "end": 0.5, "confidence": 0.9},
            {"word": "world", "start": 0.5, "end": 1.0, "confidence": 0.85},
        ]

    def transcribe(self, media_path: str) -> List[Dict[str, Any]]:
        """Return the pre‑configured result.

        Parameters
        ----------
        media_path: str
            Ignored – the mock does not read the file.
        """
        return self._result


class WhisperTranscriber:
    """Thin wrapper around the ``whisper`` CLI.

    The caller should handle :class:`TranscriptionError` to convert it into the
    API's structured error format.
    """

    def __init__(self):
        self.settings = get_settings()
        # Verify the ``whisper`` executable is available.
        if not shutil.which("whisper"):
            raise TranscriptionError("WHISPER_NOT_FOUND", "Whisper CLI not installed or not on PATH")
        self.timeout = self.settings.whisper_timeout_s

    def _run_cli(self, media_path: str) -> str:
        """Execute the Whisper CLI and return its stdout.

        The command is built without any user‑controlled shell interpolation to
        avoid injection risks.
        """
        cmd = [
            "whisper",
            media_path,
            "--model",
            self.settings.whisper_model,
            "--language",
            "en",
            "--output_format",
            "json",
            "--device",
            self.settings.whisper_device,
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError("WHISPER_TIMEOUT", "Whisper transcription timed out") from exc
        if completed.returncode != 0:
            raise TranscriptionError(
                "WHISPER_FAILED",
                completed.stderr.strip() or "Whisper process failed",
            )
        return completed.stdout

    def transcribe(self, media_path: str) -> List[Dict[str, Any]]:
        """Run Whisper and return a list of word‑level dictionaries.

        Each dictionary contains ``word``, ``start``, ``end`` and ``confidence``.
        """
        raw_output = self._run_cli(media_path)
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise TranscriptionError("WHISPER_INVALID_JSON", "Invalid JSON from Whisper") from exc

        segments = data.get("segments")
        if not segments:
            raise TranscriptionError("WHISPER_NO_SEGMENTS", "No segments in Whisper output")

        words: List[Dict[str, Any]] = []
        for seg in segments:
            seg_words = seg.get("words")
            if not seg_words:
                # According to the specification we must error if word timestamps are absent.
                raise TranscriptionError(
                    "WHISPER_NO_WORD_TIMESTAMPS",
                    "Word timestamps missing in Whisper output",
                )
            for w in seg_words:
                # Each word entry should contain its own timestamps.
                start = w.get("start")
                end = w.get("end")
                if start is None or end is None:
                    raise TranscriptionError(
                        "WHISPER_NO_WORD_TIMESTAMPS",
                        "Word timestamps missing in Whisper output",
                    )
                confidence = w.get("probability")
                words.append({
                    "word": w.get("word"),
                    "start": start,
                    "end": end,
                    "confidence": confidence,
                })
        return words
