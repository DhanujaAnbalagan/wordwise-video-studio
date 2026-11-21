"""Time parsing and formatting utilities for the VideoFlow AI engine.
Mirrors `src/engine/time.ts`.
"""

from __future__ import annotations

import re
from typing import Optional


def format_tc(ms: Optional[int], with_ms: bool = True) -> str:
    """Formats milliseconds as mm:ss.mmm (or h:mm:ss.mmm)."""
    if ms is None:
        return "--:--"
    neg = ms < 0
    t = round(abs(ms))
    h = t // 3_600_000
    t %= 3_600_000
    m = t // 60_000
    t %= 60_000
    s = t // 1000
    r = t % 1000
    mm = f"{m:02d}"
    ss = f"{s:02d}"
    ms_str = f".{r:03d}" if with_ms else ""
    h_str = f"{h}:" if h > 0 else ""
    core = f"{h_str}{mm}:{ss}{ms_str}"
    return f"-{core}" if neg else core


TIME_PATTERN = (
    r"(\d+:\d{1,2}(?::\d{1,2})?(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?)?(?![\dx:]))"
)

RANGE_PATTERN = (
    r"(?:from|between)?\s*" + TIME_PATTERN + r"\s*(?:to|-|–|and|until|through)\s*" + TIME_PATTERN
)

TIME_RE = re.compile(TIME_PATTERN, re.IGNORECASE)
RANGE_RE = re.compile(RANGE_PATTERN, re.IGNORECASE)


def parse_time(raw: str) -> Optional[int]:
    """Parses '1:20', '00:30.5', '1:02:03', '90s', '1.5 minutes', '42' into integer milliseconds."""
    tok = raw.strip().lower()
    if not tok:
        return None
    if ":" in tok:
        parts = tok.split(":")
        if len(parts) > 3 or any(p == "" for p in parts):
            return None
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        secs = 0.0
        for p in nums:
            secs = secs * 60.0 + p
        return int(round(secs * 1000))

    m = re.match(r"^(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?)?$", tok)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2) or "s"
    if unit.startswith("ms") or unit.startswith("milli"):
        return int(round(n))
    if unit.startswith("m"):
        return int(round(n * 60_000))
    return int(round(n * 1000))
