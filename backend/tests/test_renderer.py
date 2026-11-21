"""Integration tests for the FFmpeg renderer.

These tests create synthetic media files (video+audio, audio-only, video-only)
using ffmpeg, then invoke ``render_job`` with various RenderPlans (cuts, speed
changes, reordering, captions, overlays). Output stream types and durations
are verified with ffprobe. Failure cleanup and error handling are also verified.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from backend.app.config import get_settings
from backend.app.media.renderer import probe_media, render_job, RenderingError
from backend.app.media.render_plan import CaptionOverlay, FilterStep, RenderPlan


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure test renders are contained in pytest temporary directory."""
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path)



def _make_synthetic_video(path: Path, duration: float = 1.0) -> None:
    """Create a 320x240 color video with a tone audio track."""
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


def _make_synthetic_video_only(path: Path, duration: float = 1.0) -> None:
    """Create a 320x240 color video without any audio stream."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available on PATH")
    args = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s=320x240:d={duration}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_synthetic_audio(path: Path, duration: float = 1.0) -> None:
    """Create an audio-only AAC/M4A file."""
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


def _simple_plan(duration_ms: int, has_video: bool = True, has_audio: bool = True) -> RenderPlan:
    """A single filter step keeping the specified duration."""
    v_filter = (
        "trim=start=0:end={:.3f},setpts=PTS-STARTPTS".format(duration_ms / 1000.0)
        if has_video
        else None
    )
    a_filter = (
        "atrim=start=0:end={:.3f},asetpts=PTS-STARTPTS".format(duration_ms / 1000.0)
        if has_audio
        else None
    )
    step = FilterStep(
        video_filter=v_filter,
        audio_filter=a_filter,
        start_ms=0,
        end_ms=duration_ms,
        speed=1.0,
        tl_start_ms=0,
        tl_end_ms=duration_ms,
        source_id="src1",
    )
    return RenderPlan(
        filter_steps=[step],
        captions=[],
        overlays=[],
        expected_duration_ms=duration_ms,
        has_video=has_video,
        has_audio=has_audio,
    )


def test_renderer_video_with_audio(tmp_path: Path):
    source = tmp_path / "src.mp4"
    _make_synthetic_video(source, duration=2.0)
    plan = _simple_plan(duration_ms=2000, has_video=True, has_audio=True)
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()

    # Verify with ffprobe that we have both video and audio streams
    ffprobe = shutil.which("ffprobe")
    args = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(out)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    types = {s["codec_type"] for s in data["streams"]}
    assert "video" in types and "audio" in types


def test_renderer_audio_only(tmp_path: Path):
    source = tmp_path / "audio.m4a"
    _make_synthetic_audio(source, duration=1.5)
    plan = _simple_plan(duration_ms=1500, has_video=False, has_audio=True)
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()

    ffprobe = shutil.which("ffprobe")
    args = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(out)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    types = {s["codec_type"] for s in data["streams"]}
    assert "audio" in types and "video" not in types


def test_renderer_video_only(tmp_path: Path):
    source = tmp_path / "video_only.mp4"
    _make_synthetic_video_only(source, duration=1.2)
    plan = _simple_plan(duration_ms=1200, has_video=True, has_audio=False)
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()

    ffprobe = shutil.which("ffprobe")
    args = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(out)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    types = {s["codec_type"] for s in data["streams"]}
    assert "video" in types and "audio" not in types


def test_renderer_cuts_and_speed_changes(tmp_path: Path):
    source = tmp_path / "src.mp4"
    _make_synthetic_video(source, duration=3.0)

    # Segment 1: 0.0s - 1.0s at 1.0x speed -> 1.0s on timeline
    step1 = FilterStep(
        video_filter="trim=start=0.000:end=1.000,setpts=(PTS-STARTPTS)/1.0",
        audio_filter="atrim=start=0.000:end=1.000,asetpts=PTS-STARTPTS",
        start_ms=0,
        end_ms=1000,
        speed=1.0,
        tl_start_ms=0,
        tl_end_ms=1000,
    )
    # Segment 2: 2.0s - 3.0s (1.0s source cut out) at 2.0x speed -> 0.5s on timeline
    step2 = FilterStep(
        video_filter="trim=start=2.000:end=3.000,setpts=(PTS-STARTPTS)/2.0",
        audio_filter="atrim=start=2.000:end=3.000,asetpts=PTS-STARTPTS,atempo=2.0",
        start_ms=2000,
        end_ms=3000,
        speed=2.0,
        tl_start_ms=1000,
        tl_end_ms=1500,
    )
    plan = RenderPlan(filter_steps=[step1, step2], captions=[], overlays=[])
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()

    # Check duration is ~1.5s (within 0.2s tolerance)
    ffprobe = shutil.which("ffprobe")
    args = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    dur = float(json.loads(completed.stdout)["format"]["duration"])
    assert 1.3 <= dur <= 1.7


def test_renderer_reordering(tmp_path: Path):
    source = tmp_path / "src.mp4"
    _make_synthetic_video(source, duration=2.0)

    # First render segment 2 (1.0s - 2.0s), then segment 1 (0.0s - 1.0s)
    step1 = FilterStep(
        video_filter="trim=start=1.000:end=2.000,setpts=(PTS-STARTPTS)/1.0",
        audio_filter="atrim=start=1.000:end=2.000,asetpts=PTS-STARTPTS",
        start_ms=1000,
        end_ms=2000,
        speed=1.0,
        tl_start_ms=0,
        tl_end_ms=1000,
    )
    step2 = FilterStep(
        video_filter="trim=start=0.000:end=1.000,setpts=(PTS-STARTPTS)/1.0",
        audio_filter="atrim=start=0.000:end=1.000,asetpts=PTS-STARTPTS",
        start_ms=0,
        end_ms=1000,
        speed=1.0,
        tl_start_ms=1000,
        tl_end_ms=2000,
    )
    plan = RenderPlan(filter_steps=[step1, step2], captions=[], overlays=[])
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()

    ffprobe = shutil.which("ffprobe")
    args = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    dur = float(json.loads(completed.stdout)["format"]["duration"])
    assert 1.8 <= dur <= 2.2


def test_renderer_captions_and_overlays(tmp_path: Path):
    source = tmp_path / "src.mp4"
    _make_synthetic_video(source, duration=2.0)

    step = FilterStep(
        video_filter="trim=start=0.000:end=2.000,setpts=PTS-STARTPTS",
        audio_filter="atrim=start=0.000:end=2.000,asetpts=PTS-STARTPTS",
        start_ms=0,
        end_ms=2000,
        speed=1.0,
        tl_start_ms=0,
        tl_end_ms=2000,
    )
    cap = CaptionOverlay(
        text="Hello World",
        start_ms=100,
        end_ms=900,
        x="(w-text_w)/2",
        y="h-text_h-40",
        font_size=24,
    )
    overlay = CaptionOverlay(
        text="Special: Note & %",
        start_ms=1000,
        end_ms=1800,
        x="(w-text_w)/2",
        y="40",
        font_size=24,
    )
    plan = RenderPlan(filter_steps=[step], captions=[cap], overlays=[overlay])
    job_id = uuid.uuid4().hex
    out = render_job(job_id, source, plan)
    assert out.is_file()

    probe_data = probe_media(out)
    types = {s["codec_type"] for s in probe_data["streams"]}
    assert "video" in types and "audio" in types


def test_renderer_invalid_output_raises(tmp_path: Path):
    # Pass a plan with an invalid filter to force ffmpeg failure
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


def test_renderer_cleanup_on_failure(tmp_path: Path):
    source = tmp_path / "src.mp4"
    _make_synthetic_video(source, duration=1.0)
    settings = get_settings()
    tmp_dir = settings.data_dir / "tmp_render"

    bad_step = FilterStep(
        video_filter="nonexistent_filter_error", audio_filter=None, start_ms=0, end_ms=1000
    )
    plan = RenderPlan(filter_steps=[bad_step], captions=[], overlays=[])
    job_id = uuid.uuid4().hex

    with pytest.raises(RenderingError):
        render_job(job_id, source, plan)

    # Verify no dangling temporary file in tmp_dir
    if tmp_dir.is_dir():
        dangling = list(tmp_dir.glob(f"*.mp4"))
        assert len(dangling) == 0


def test_renderer_missing_source():
    fake_source = Path("does_not_exist_file.mp4")
    plan = RenderPlan(filter_steps=[], captions=[], overlays=[])
    with pytest.raises(RenderingError) as excinfo:
        render_job("job123", fake_source, plan)
    assert excinfo.value.code == "SOURCE_MISSING"
