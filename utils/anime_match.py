"""V3 #4 + V3 #5 shared helpers: strict quality labels and confident anime matching.

V3 #4 rule:
    detected_quality / requested_quality / verified_quality are separate.
    Unknown must stay "Unknown" — never inherit the requested label.

V3 #5 rule:
    Title + season (+ episode/year/language where available) must match
    confidently, otherwise reject instead of guessing the first result.
"""

from __future__ import annotations

import re

_STOP = {
    "season", "seasons", "episode", "episodes", "hindi", "dubbed", "dub",
    "multi", "audio", "multi-audio", "multiaudio", "the", "a", "an",
    "tamil", "telugu", "uncensored", "complete", "batch", "all",
    "tv", "show", "series", "movie", "part", "cour", "subbed",
}


def normalize_tokens(title: str) -> set[str]:
    toks = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split()
    return {t for t in toks if len(t) > 2 and t not in _STOP}


def is_confident_match(query: str, candidate_title: str, candidate_url: str = "") -> bool:
    """Return True only for a confident anime-title match (V3 #5)."""
    q_toks = normalize_tokens(query)
    if not q_toks:
        return False
    c_blob = f"{candidate_title} {candidate_url}".lower()
    c_toks = normalize_tokens(candidate_title + " " + candidate_url)
    overlap = q_toks & c_toks
    if not overlap:
        return False
    # Single-word queries: require that word (len>=3 already) to appear.
    if len(q_toks) == 1:
        return True
    # Multi-word queries: require >=2 shared significant tokens, or a
    # single long distinctive token (>=6 chars) plus overall similarity.
    if len(overlap) >= 2:
        return True
    if len(overlap) == 1:
        tok = next(iter(overlap))
        if len(tok) >= 6 and tok in c_blob:
            return True
        # e.g. "demon slayer" vs "demon-slayer-season-2": q has 2 tokens,
        # candidate has both? that would be overlap 2. Single overlap here
        # means the other query word is missing → reject.
        return False
    return False


def matches_season(title: str, url: str, season: int) -> bool:
    """Strict season check: explicit season token must agree when present."""
    blob = f"{title} {url}".lower()
    # Find explicit season markers in candidate.
    m_candidates = []
    for m in re.finditer(r"season[\s\-_]*(\d{1,2})", blob):
        m_candidates.append(int(m.group(1)))
    for m in re.finditer(r"\bs(\d{1,2})\b", blob):
        m_candidates.append(int(m.group(1)))
    if not m_candidates:
        # No season marker → only acceptable for season 1 lookups.
        return season == 1
    return season in m_candidates


def is_native_4k(quality_str: str) -> bool:
    """Check if quality represents native 4K/2160p/UHD."""
    q = (quality_str or "").strip().lower()
    return any(k in q for k in ("4k", "2160", "uhd"))


def is_enhanced_1080p(quality_str: str) -> bool:
    """Check if quality is an enhanced 1080p tier (HQ, x265, HEVC, 10-bit)."""
    q = (quality_str or "").strip().lower()
    if "1080" in q:
        return any(k in q for k in ("hq", "x265", "hevc", "10bit", "10-bit", "10 bit"))
    return False


def parse_size_mb(val: str | int | float | None) -> float | None:
    """Parse size in megabytes (MB) from bytes, number, or string like '1.45 GB', '850 MB'."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # If value is large (> 100,000), assume it is bytes; otherwise assume MB
        return (val / (1024 * 1024)) if val > 100_000 else float(val)
    s = str(val).strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb|gib|mib)\b", s, re.I)
    if m:
        num = float(m.group(1))
        unit = m.group(2).lower()
        return num * 1024.0 if "g" in unit else num
    m_num = re.search(r"^\d+(?:\.\d+)?$", s)
    if m_num:
        num = float(m_num.group(0))
        return (num / (1024 * 1024)) if num > 100_000 else num
    return None


def is_around_1_to_2_gb(val: str | int | float | None) -> bool:
    """True only when the size is around 1 to 2 GB (~850 MB to ~2600 MB)."""
    mb = parse_size_mb(val)
    return mb is not None and (850.0 <= mb <= 2600.0)


def is_4k_satisfying(quality_str: str, size: str | int | float | None = None) -> bool:
    """Check if quality satisfies a 4K request.
    
    - Native 4K (2160p / UHD) always satisfies 4K.
    - Enhanced 1080p (1080p HQ x265, 10-bit, HEVC) satisfies 4K ONLY when its
      size is around 1 to 2 GB (approx 850 MB to 2600 MB).
    """
    if is_native_4k(quality_str):
        return True
    if is_enhanced_1080p(quality_str):
        if size is not None:
            return is_around_1_to_2_gb(size)
        sz = parse_size_mb(quality_str)
        if sz is not None:
            return is_around_1_to_2_gb(sz)
    return False


def normalize_quality(q: str) -> str:
    """Canonical quality label; unknown/empty → 'Unknown' (V3 #4)."""
    s = (q or "").strip().lower()
    if s in ("4k", "2160p", "2160", "uhd"):
        return "4K"
    if s in ("1080p", "1080", "fhd", "1080p-hq", "1080p hq"):
        return "1080p"
    if s in ("720p", "720", "hd", "hdrip", "webrip"):
        # NOTE: bare 'hd' is ambiguous — callers should prefer explicit
        # tokens; keep mapping for backwards compat but detection must
        # require explicit 720 markers to emit 720p (see detectors).
        return "720p"
    if s in ("480p", "480", "sd"):
        return "480p"
    if s in ("360p", "360"):
        return "360p"
    if s in ("240p", "240"):
        return "240p"
    if s in ("auto",):
        return "auto"
    return "Unknown"


def qualities_match(requested: str, detected: str, size: str | int | float | None = None) -> bool:
    """V3 #2 strict equality on canonical labels (4K≡2160p≡UHD).
    
    When requested quality is 4K:
    - Native 4K matches.
    - Enhanced 1080p (1080p HQ x265, 10-bit, etc.) matches ONLY if its size is
      around 1 to 2 GB.
    """
    req_clean = (requested or "").strip().lower()
    if req_clean == "auto":
        return True

    if req_clean in ("4k", "2160p", "2160", "uhd"):
        return is_4k_satisfying(detected, size=size)

    return normalize_quality(requested) == normalize_quality(detected) and normalize_quality(detected) != "Unknown"
