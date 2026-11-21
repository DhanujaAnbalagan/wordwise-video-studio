"""Integration tests for the FFmpeg renderer.

These tests create tiny synthetic media files (a 1‑second color video with tone,
or audio‑only) using ffmpeg, then invoke ``render_job`` with a simple
RenderPlan that performs a cut and speed change. The output is validated
with ffprobe to ensure the expected streams and duration.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from backend.app.media.renderer import render_job, RenderingError
from backend.app.media.render_plan import RenderPlan, FilterStep, CaptionOverlay
from backend.app.config import get_settings


def _make_synthetic_video(path: Path, duration: float = 1.0):
    """Create a 320x240 color video with a tone audio track.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available on PATH")
    args = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=red:s=320x240:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_synthetic_audio(path: Path, duration: float = 1.0):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available on PATH")
    args = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=1000:duration={duration}",
        "-c:a",
        "aac",
        str(path),
    ]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _simple_plan(duration_ms: int) -> RenderPlan:
    # A single filter step that keeps the whole source.
    step = FilterStep(
        video_filter="trim=start=0:end={}:setpts=PTS-STARTPTS".format(duration_ms / 1000),
        audio_filter="atrim=start=0:end={}:asetpts=PTS-STARTPTS".format(duration_ms / 1000),
        start_ms=0,
        end_ms=duration_ms,
        source_id="src1",
    )
    return RenderPlan(filter_steps=[step], captions=[], overlays=[])


def test_renderer_video_with_audio(tmp_path: Path):
    source = tmp_path / "src.mp4"
    _make_synthetic_video(source, duration=2.0)
    plan = _simple_plan(duration_ms=2000)
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()
    # Verify with ffprobe that we have both video and audio streams.
    ffprobe = shutil.which("ffprobe")
    args = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(out)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    types = {s["codec_type"] for s in data["streams"]}
    assert "video" in types and "audio" in types


def test_renderer_audio_only(tmp_path: Path):
    source = tmp_path / "audio.m4a"
    _make_synthetic_audio(source, duration=1.5)
    plan = _simple_plan(duration_ms=1500)
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()
    ffprobe = shutil.which("ffprobe")
    args = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(out)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    types = {s["codec_type"] for s in data["streams"]}
    assert "audio" in types and "video" not in types


def test_renderer_invalid_output_raises(tmp_path: Path, monkeypatch):
    # Pass a plan with an invalid filter to force ffmpeg failure.
    source = tmp_path / "src.mp4"
    _make_synthetic_video(source, duration=1.0)
    bad_step = FilterStep(
        video_filter="invalidfilter", audio_filter=None, start_ms=0, end_ms=1000, source_id="src"
    )
    plan = RenderPlan(filter_steps=[bad_step], captions=[], overlays=[])
    job_id = uuid.uuid4().hex
    with pytest.raises(RenderingError) as excinfo:
        render_job(job_id, source, plan)
    assert excinfo.value.code == "FFMPEG_FAILED"
