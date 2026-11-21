"""Transcript search, sentence boundary and word-snapping helpers.
Mirrors `src/engine/transcript.ts`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from .types import Interval, Transcript, Word

LOW_CONFIDENCE = 0.6


def norm_token(t: str) -> str:
    """Normalize a word for matching: lowercase, standardize apostrophes, strip punctuation."""
    return re.sub(r"[^a-z0-9']", "", t.lower().replace("’", "'"))


def ends_sentence(t: str) -> bool:
    """Check if token ends with punctuation indicating the end of a sentence."""
    return bool(re.search(r'[.?!]["\')\]]*$', t))


def sentences(words: List[Word]) -> List[Tuple[int, int]]:
    """Return sentences as (first_index, last_index) word index pairs, splitting on sentence-ending punctuation or speaker changes."""
    if not words:
        return []
    out: List[Tuple[int, int]] = []
    start = 0
    for i, w in enumerate(words):
        next_w = words[i + 1] if i + 1 < len(words) else None
        if ends_sentence(w.text) or next_w is None or (next_w.speaker or "") != (w.speaker or ""):
            out.append((start, i))
            start = i + 1
    return out


def sentence_of(words: List[Word], index: int) -> Tuple[int, int]:
    """Find the sentence (first_index, last_index) containing the given word index."""
    for a, b in sentences(words):
        if a <= index <= b:
            return (a, b)
    return (index, index)


@dataclass(frozen=True)
class PhraseMatch:
    word_ids: List[str]
    start: int
    end: int
    first_index: int
    last_index: int
    kind: Literal["exact", "related"]
    matched: str


RELATED_CLUSTERS: List[List[str]] = [
    ["pricing", "price", "prices", "priced", "cost", "costs", "charge", "charged", "fee", "fees", "dollars", "pay", "paid", "plan", "plans"],
    ["introduction", "intro", "welcome", "begin", "start", "opening"],
    ["conclusion", "conclude", "summary", "summarize", "ending", "end", "wrap up", "finally"],
    ["customer", "customers", "client", "clients", "teachers", "users"],
    ["confidential", "secret", "private"],
    ["thanks", "thank", "grateful"],
    ["machine learning", "ml", "ai", "artificial intelligence", "deep learning", "neural network", "neural networks"],
]


def related_terms(term: str) -> List[str]:
    """Return synonyms and related search keywords for a term."""
    t = norm_token(term)
    for group in RELATED_CLUSTERS:
        norm_group = [norm_token(x) for x in group]
        if t in norm_group:
            return [x for x in group if norm_token(x) != t]
    return []


def search_phrase(words: List[Word], phrase: str, include_related: bool = False) -> List[PhraseMatch]:
    """Search for an exact phrase across consecutive words, and optionally related single terms."""
    toks = [norm_token(x) for x in phrase.split() if norm_token(x)]
    if not toks:
        return []
    out: List[PhraseMatch] = []
    # 1. Exact sequence matching
    for i in range(len(words) - len(toks) + 1):
        ok = True
        for j in range(len(toks)):
            if norm_token(words[i + j].text) != toks[j]:
                ok = False
                break
        if ok:
            matched_words = words[i : i + len(toks)]
            out.append(
                PhraseMatch(
                    word_ids=[w.id for w in matched_words],
                    start=matched_words[0].start,
                    end=matched_words[-1].end,
                    first_index=i,
                    last_index=i + len(toks) - 1,
                    kind="exact",
                    matched=" ".join(w.text for w in matched_words),
                )
            )

    # 2. Related single-word terms if requested
    if include_related and len(toks) == 1:
        related = {norm_token(r) for r in related_terms(toks[0])}
        for i, w in enumerate(words):
            if norm_token(w.text) in related:
                # avoid duplicate if already matched as exact
                if not any(m.first_index == i for m in out):
                    out.append(
                        PhraseMatch(
                            word_ids=[w.id],
                            start=w.start,
                            end=w.end,
                            first_index=i,
                            last_index=i,
                            kind="related",
                            matched=w.text,
                        )
                    )

    out.sort(key=lambda m: m.start)
    return out


def context_of(words: List[Word], first: int, last: int, pad: int = 4) -> str:
    """Format a transcript excerpt with padding words on each side."""
    if not words:
        return ""
    a = max(0, first - pad)
    b = min(len(words) - 1, last + pad)
    prefix = "… " if a > 0 else ""
    suffix = " …" if b < len(words) - 1 else ""
    text = " ".join(words[i].text for i in range(a, b + 1))
    return f"{prefix}{text}{suffix}"


ALWAYS_FILLERS = {"um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm"}


def is_filler(words: List[Word], i: int) -> bool:
    """Check if words[i] is a filler word.
    um/uh/erm/er/ah/hmm are always fillers. 'like' counts only when set off by commas on both sides.
    """
    t = norm_token(words[i].text)
    if t in ALWAYS_FILLERS:
        return True
    if t == "like":
        has_comma = words[i].text.endswith(",")
        prev_comma = i > 0 and words[i - 1].text.endswith(",")
        return has_comma and prev_comma
    return False


LEAD_IN_TOKENS = {"so", "um", "uh", "and", "well", "okay", "ok", "right", "now"}


def lead_ins(words: List[Word]) -> List[Dict[str, object]]:
    """Sentence-initial discourse markers ('So, uh, ...') as word index runs."""
    out: List[Dict[str, object]] = []
    for a, b in sentences(words):
        idx: List[int] = []
        for i in range(a, b):
            raw = words[i].text
            t = norm_token(raw)
            if t in LEAD_IN_TOKENS and (raw.endswith(",") or t in ALWAYS_FILLERS):
                idx.append(i)
            else:
                break
        if idx:
            out.append({"indices": idx, "speaker": words[a].speaker})
    return out


def words_in_range(words: List[Word], r: Interval) -> List[Word]:
    """Return words that overlap with interval r by at least 50% of the word's duration."""
    out: List[Word] = []
    for w in words:
        ov = min(w.end, r.end) - max(w.start, r.start)
        if ov > 0 and ov >= (w.end - w.start) * 0.5:
            out.append(w)
    return out


def snap_to_words(words: List[Word], r: Interval) -> Tuple[Interval, List[str]]:
    """Move cut boundaries that fall inside a word to the nearer edge to avoid clipping words."""
    moved: List[str] = []

    def snap(t: int, label: str) -> int:
        w = next((x for x in words if x.start < t < x.end), None)
        if not w:
            return t
        to = w.start if (t - w.start <= w.end - t) else w.end
        moved.append(f"{label} moved from {t / 1000:.3f}s to {to / 1000:.3f}s to avoid clipping “{w.text}”")
        return to

    new_start = snap(r.start, "Start")
    new_end = snap(r.end, "End")
    return Interval(start=new_start, end=new_end), moved
