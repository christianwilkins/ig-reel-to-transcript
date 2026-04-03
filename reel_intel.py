#!/usr/bin/env python3
"""
reel_intel.py

Ingest Instagram reel URLs and generate practical analysis:
- Pull visible text (title, caption, comments) via r.jina.ai mirror
- Optionally attempt audio transcript (yt-dlp + whisper/OpenAI API)
- Score hype/bullshit risk for side-income claims
- Produce action checklist + content repurposing angles
- Add 21st.dev UI integration notes when topic is web/UI

Usage:
  python3 reel_intel.py https://www.instagram.com/reel/XXXX/
  python3 reel_intel.py URL1 URL2 --save-dir ./outputs
  python3 reel_intel.py URL --try-transcript
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

JINA_PREFIX = "https://r.jina.ai/http://"


@dataclass
class ReelResult:
    url: str
    source_url: str
    title: str
    caption: str
    hashtags: List[str]
    top_comments: List[str]
    explicit_comment_candidates: int
    kept_comment_count: int
    comment_context_source: str
    filtered_low_signal_comments: int
    low_signal_primary_pattern: str
    low_signal_primary_pattern_share: float
    low_signal_pattern_counts: Dict[str, int]
    signal_lines: List[str]
    claim_lines: List[str]
    substantive_claim_lines: int
    cta_only_claim_lines: int
    extracted_text: str
    transcript: Optional[str]
    transcript_method: Optional[str]
    transcript_error: Optional[str]
    looks_like_access_wall: bool
    looks_like_placeholder_payload: bool
    extraction_confidence: int
    confidence_notes: List[str]
    niche: str
    hype_risk_score: int
    verdict: str
    red_flags: List[str]
    green_flags: List[str]
    due_diligence: List[str]
    action: str
    content_hooks: List[str]
    content_script_outline: List[str]
    research_notes: List[str]
    ui_prompt_pack: Optional[List[str]]


LOW_CONTEXT_DOMINANT_PATTERNS = {
    "empty_or_symbol",
    "thread_metadata",
    "numeric_cheer",
    "cta_keyword_echo",
    "intent_only",
    "algorithm_chatter",
    "year_check_nostalgia",
    "location_rollcall",
    "day_streak_chatter",
    "generic_reaction",
    "meta_routing",
    "dm_logistics",
    "contact_handoff",
    "help_solicitation",
    "mention_only",
    "mention_filler",
    "engagement_task_completion",
    "giveaway_entry",
    "self_promo_solicitation",
    "followback_reciprocity",
    "growth_loop_exchange",
    "tag_referral",
    "story_share_repost",
    "manifestation_affirmation",
    "testimonial_vouch",
    "save_for_later",
    "gratitude_only",
}


# Keep this shared so low-signal filtering and intent-only detection preserve
# the same core execution/skepticism vocabulary.
SUBSTANTIVE_COMMENT_TOKENS = {
    "scam",
    "fake",
    "proof",
    "results",
    "client",
    "clients",
    "legal",
    "contract",
    "license",
    "compliance",
    "title",
    "escrow",
    "earnest",
    "assignment",
    "closing",
    "buyer",
    "buyers",
    "dispo",
    "cost",
    "price",
    "margin",
    "fees",
    "tax",
    "taxes",
    "roi",
    "how",
    "why",
    "where",
    "when",
}


INSTAGRAM_ACCESS_WALL_COPY_RE = re.compile(
    r"\b("
    r"log in|sign up|mobile number, username or email|phone number, username or email|"
    r"password|forgot password|see everyday moments from your close friends|"
    r"see instagram photos and videos from your friends|"
    r"content unavailable|the link you followed may be broken|"
    r"sorry,?\s*this page (?:isn['’]?t|is not) available|"
    r"page (?:isn['’]?t|is not) available|this account is private"
    r")\b",
    re.I,
)


INSTAGRAM_PLACEHOLDER_LINE_RE = re.compile(
    r"^(?:#\s*)?(?:https?://)?(?:www\.)?instagram\.com/?$"
    r"|^instagram$"
    r"|^instagram\s+photos?\s+and\s+videos?$"
    r"|^see\s+instagram\s+photos?\s+and\s+videos?(?:\s+from\s+your\s+friends)?$"
    r"|^(?:log\s*in|login|sign\s*up)\s*[·•|:\-]?\s*instagram$"
    r"|^create\s+an\s+account\s+or\s+log\s+in\s+to\s+instagram$"
    r"|^watch\s+this\s+(?:reel|post|story|video)\b.*\bon\s+instagram\.?$"
    r"|^(?:see|view)\s+this\s+(?:reel|post|story|video)\b.*\bon\s+instagram\.?$"
    r"|^check\s+out\s+this\s+(?:reel|post|story|video)\b.*\bon\s+instagram\.?$"
    r"|^(?:shared|sent)\s+(?:a|this)\s+(?:reel|post|story|video)\b.*\bon\s+instagram\.?$"
    r"|^watch\s+more\s+(?:reels?|videos?)\s+on\s+instagram\.?$",
    re.I,
)


def has_instagram_access_wall_copy(text: str) -> bool:
    if not text:
        return False
    return bool(INSTAGRAM_ACCESS_WALL_COPY_RE.search(text))


def is_instagram_placeholder_line(text: str) -> bool:
    if not text:
        return False
    t = re.sub(r"\s+", " ", text.strip().lower())
    t = t.strip("`'\"“”‘’")
    t = re.sub(r"[\s\u00b7•|:;,.!\-]+$", "", t)
    return bool(INSTAGRAM_PLACEHOLDER_LINE_RE.fullmatch(t))


def http_get_text(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_reel_markdown(url: str) -> str:
    clean = url.strip()
    if clean.startswith("http://"):
        clean = clean[len("http://") :]
    elif clean.startswith("https://"):
        clean = clean[len("https://") :]
    jina_url = JINA_PREFIX + clean
    return http_get_text(jina_url)


def extract_instagram_shortcode(url: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    for i, part in enumerate(parts):
        if part in {"reel", "reels", "p"} and i + 1 < len(parts):
            code = parts[i + 1].strip()
            if code and re.fullmatch(r"[A-Za-z0-9_-]{6,}", code):
                return code

    # Fallback for URLs that already pass only the shortcode-like path.
    for part in parts:
        if re.fullmatch(r"[A-Za-z0-9_-]{6,}", part):
            return part

    return None


def load_cached_transcript(url: str) -> tuple[Optional[str], Optional[str]]:
    shortcode = extract_instagram_shortcode(url)
    if not shortcode:
        return None, None

    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "research" / "reels" / "transcripts" / f"{shortcode}.txt"
    if not candidate.exists():
        return None, None

    text = candidate.read_text(errors="replace").strip()
    if not text:
        return None, None

    return text, "local-transcript-cache"


def extract_signal_lines(md: str, limit: int = 30) -> List[str]:
    body_start = md.find("Markdown Content:")
    body = md[body_start + len("Markdown Content:") :] if body_start >= 0 else md

    lines: List[str] = []
    seen = set()
    for raw in body.splitlines():
        t = raw.strip().strip("-*• ")
        if not (18 <= len(t) <= 240):
            continue
        if is_probable_handle_line(t):
            continue
        low_t = t.lower()
        # Keep substantive platform-specific lines (for example
        # "Instagram will push your videos") while filtering common title/UI
        # chrome such as "X on Instagram: ..." and standalone "Instagram".
        if low_t == "instagram":
            continue
        if "on instagram:" in low_t:
            continue
        if is_instagram_placeholder_line(t):
            continue
        # Skip markdown link-only lines from footer/nav chrome.
        if re.fullmatch(r"(?:\[[^\]]{0,120}\]\(https?://[^)]+\)\s*)+", t):
            continue
        if re.search(r"\]\(https?://(?:www\.)?instagram\.com/", t, re.I):
            continue
        if re.search(
            r"title:|url source:|warning:|profile picture|log in|sign up|meta|threads|cookie|privacy|terms|view all \d+ repl|view all \d+ comment|view \d+ comments|view all comments|view more comments|view previous comments|view \d+ repl(?:y|ies)|view all repl(?:y|ies)|hide repl(?:y|ies)|view repl(?:y|ies)|keep up with what'?s new|never miss a post from|audio muted|add comment|see translation|view translation|liked by|liked by creator|creator liked this|followed by|pinned comment|\bpinned\b|see more|see less|original audio|reels remix|blob:http://localhost|mobile number, username or email|phone number, username or email|password|forgot password",
            t,
            re.I,
        ):
            continue
        if has_instagram_access_wall_copy(t):
            continue
        if t.lower() in {"reply", "like", "share", "follow", "author"}:
            continue
        if is_replying_to_line(t):
            continue
        if is_hashtag_heavy_text(t):
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(t)
        if len(lines) >= limit:
            break
    return lines


def is_reply_marker(line: str) -> bool:
    t = line.strip().lower()
    t = re.sub(r"[·•|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    time_unit = r"(?:s|m|h|d|w|mo|y)"

    if t == "reply":
        return True
    if re.search(r"\bview\s+repl(?:y|ies)\b", t):
        return True
    if re.search(r"\bview\s+\d+\s+repl(?:y|ies)\b", t):
        return True
    if re.search(r"\bview\s+all\s+repl(?:y|ies)\b", t):
        return True
    if re.fullmatch(r"\d+\s*repl(?:y|ies)", t):
        return True

    # Common IG/Jina marker variants such as:
    # "Reply 2d", "2d Reply", "Reply 3h"
    return bool(
        re.fullmatch(rf"(?:reply\s*)?(?:\d+\s*{time_unit})\s*reply", t)
        or re.fullmatch(rf"reply\s*(?:\d+\s*{time_unit})", t)
    )


def is_relative_time_line(line: str) -> bool:
    t = line.strip().lower()
    t = re.sub(r"[·•|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    time_unit = r"(?:s|m|h|d|w|mo|y)"

    if re.fullmatch(rf"\d+\s*{time_unit}(?:\s+ago)?", t):
        return True
    if re.fullmatch(rf"\d+\s*{time_unit}\s+edited(?:\s+ago)?", t):
        return True
    if re.fullmatch(rf"\d+\s*{time_unit}\s+author(?:\s+ago)?", t):
        return True
    return False


def is_replying_to_line(line: str) -> bool:
    t = line.strip().lower()
    return bool(re.fullmatch(r"replying to\s+@[a-z0-9._]{2,30}[:\-]?", t))


def normalized_text_key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def normalize_cta_token(token: str) -> str:
    t = re.sub(r"[^a-z0-9_]+", "", token.lower())
    # Collapse repeated letters so CTA echoes like freeee/nowww map to free/now.
    return re.sub(r"(.)\1+", r"\1", t)


def transcript_segments(transcript: str, max_segments: int = 120) -> List[str]:
    if not transcript or not transcript.strip():
        return []

    raw_parts = re.split(r"(?:\n+|(?<=[.!?])\s+)", transcript)
    out: List[str] = []
    seen = set()
    for part in raw_parts:
        cleaned = re.sub(r"\s+", " ", part).strip()
        if len(cleaned) < 3:
            continue
        key = normalized_text_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= max_segments:
            break
    return out


def transcript_cta_keyword_context(transcript: str, max_lines: int = 24) -> List[str]:
    segments = transcript_segments(transcript, max_segments=160)
    if not segments:
        return []

    cta_like = re.compile(
        r"\b(comment|dm|message me|reply|respond|drop|type|keyword|word|link in bio|free guide|join)\b"
    )

    prioritized: List[str] = []
    # Keep a few leading lines for local context plus any CTA-like lines found
    # across the full transcript so late audio prompts are not missed.
    prioritized.extend([x.lower() for x in segments[:6]])
    for seg in segments:
        if cta_like.search(seg.lower()):
            prioritized.append(seg.lower())

    out: List[str] = []
    seen = set()
    for line in prioritized:
        key = normalized_text_key(line)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= max_lines:
            break
    return out


def strip_leading_mentions(text: str) -> str:
    t = text.strip()
    if not t:
        return ""

    # Remove one or more leading @handle mentions so repeated reply-thread tags
    # do not fragment dedupe keys (for example "@john @jane interested" -> "interested").
    cleaned = re.sub(r"^(?:@[A-Za-z0-9._]{2,30}[,:;\-]?\s+)+", "", t).strip()
    if cleaned:
        return cleaned

    # Mention-only comments are usually tag noise and should not be treated as
    # audience context evidence.
    if re.fullmatch(r"(?:@[A-Za-z0-9._]{2,30}[,:;\-]?\s*)+", t):
        return ""

    return t


def is_probable_handle_line(text: str) -> bool:
    t = text.strip()
    if not t:
        return False

    token = t[1:] if t.startswith("@") else t
    if " " in token:
        return False
    if not (4 <= len(token) <= 30):
        return False
    if not re.fullmatch(r"[A-Za-z0-9._]+", token):
        return False
    if not re.search(r"[A-Za-z]", token):
        return False

    if t.startswith("@"):
        return True

    # Without an @ prefix, require common handle structure to avoid filtering
    # substantive single-word comments (for example "scam").
    return "_" in token or "." in token or any(ch.isdigit() for ch in token)


def is_hashtag_heavy_text(text: str) -> bool:
    t = text.strip()
    if not t:
        return False

    tokens = re.findall(r"#?[A-Za-z0-9_]+", t)
    if len(tokens) < 4:
        return False

    hashtag_tokens = [tok for tok in tokens if tok.startswith("#")]
    non_hashtag_tokens = [tok for tok in tokens if not tok.startswith("#")]

    hashtag_ratio = len(hashtag_tokens) / max(1, len(tokens))
    if len(hashtag_tokens) >= 4 and len(non_hashtag_tokens) <= 4:
        return True
    if hashtag_ratio >= 0.65 and len(hashtag_tokens) >= 3:
        return True

    return False


def is_metadata_or_control_line(line: str) -> bool:
    t = line.strip()
    if not t:
        return True

    low = t.lower()
    if low in {"like", "reply", "follow", "share", "author", "pinned"}:
        return True
    if is_probable_handle_line(t):
        return True
    if re.search(r"^view all \d+ (?:repl|comment)", low):
        return True
    if re.search(r"\bview\s+repl(?:y|ies)\b", low):
        return True
    if re.search(r"\bview\s+\d+\s+repl(?:y|ies)\b", low):
        return True
    if re.search(r"\bview\s+all\s+repl(?:y|ies)\b", low):
        return True
    if re.search(r"\bhide\s+repl(?:y|ies)\b", low):
        return True
    if re.search(r"\bview\s+\d+\s+comments?\b", low):
        return True
    if re.search(r"\bview\s+(?:all|more|previous)\s+comments?\b", low):
        return True
    if is_reply_marker(t):
        return True
    if is_replying_to_line(t):
        return True
    if re.search(r"(see|view) translation", low):
        return True
    if re.search(r"^liked by\b", low):
        return True
    if re.search(r"liked by creator|creator liked this", low):
        return True
    if re.search(r"^followed by\b", low):
        return True
    if re.search(r"^pinned(?:\s+comment)?\b", low):
        return True
    if re.search(r"see more|see less", low):
        return True
    if re.search(r"original audio|reels remix", low):
        return True
    if is_relative_time_line(t):
        return True
    if re.search(r"^\d[\d,\.]*\s+likes?\b", low):
        return True
    if re.search(r"^\d[\d,\.]*\s+views?\b", low):
        return True
    if "http" in low:
        return True
    if "blob:http://localhost" in low:
        return True
    if t.startswith("[") and "](" in t:
        return True
    if re.search(r"profile picture|instagram|log in|sign up", low):
        return True
    if has_instagram_access_wall_copy(t):
        return True

    return False


def is_creator_cta_line(line: str) -> bool:
    low = line.strip().lower()
    if not low:
        return False

    if re.search(r"follow\s*(?:&|and)\s*comment", low):
        return True
    if re.search(r"\bcomment\s+[\"'“”]?[a-z0-9_]{2,24}[\"'“”]?\s+(?:below|to|for)\b", low):
        return True
    if re.search(r"\b(comment|dm)\b.*\b(link in bio|book a call|join my|guide|free course)\b", low):
        return True
    if re.search(r"\bdm\s+[\"'“”]?[a-z0-9_]{2,24}[\"'“”]?\b", low):
        return True
    if re.search(r"\blink in bio\b", low):
        return True

    return False


def is_contact_handoff_line(line: str) -> bool:
    low = line.strip().lower()
    if not low:
        return False

    if re.search(r"\b(?:whatsapp|telegram|t\.me|wa\.me|signal|snapchat|discord)\b", low):
        return True

    if re.search(r"\b(?:text|call|email|message|msg)\s+me\b", low):
        return True

    if re.search(r"\b(?:reach|contact)\s+me\b", low):
        return True

    if re.search(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", low):
        return True

    # Lightweight phone-pattern detection for public number drops in comments.
    if re.search(r"\+?\d[\d\s().-]{6,}\d", low):
        return True

    return False


def looks_like_instagram_access_wall(title: str, caption: str, signal_lines: List[str]) -> bool:
    low_parts = [title.lower().strip(), caption.lower().strip(), *[x.lower().strip() for x in signal_lines[:8]]]
    blob_hit = any("blob:http://localhost" in x for x in low_parts)
    wall_copy_hits = sum(1 for x in low_parts if has_instagram_access_wall_copy(x))
    instagram_title = title.strip().lower() == "instagram"

    if blob_hit and wall_copy_hits >= 1:
        return True
    if instagram_title and wall_copy_hits >= 1:
        return True
    if wall_copy_hits >= 2:
        return True

    return False


def looks_like_placeholder_payload(title: str, caption: str, signal_lines: List[str]) -> bool:
    sample_lines = [
        title.strip().lower(),
        caption.strip().lower(),
        *[x.strip().lower() for x in signal_lines[:6]],
    ]
    sample_lines = [x for x in sample_lines if x]
    if not sample_lines:
        return False

    if not any("instagram" in x for x in sample_lines):
        return False

    placeholder_hits = sum(1 for x in sample_lines if is_instagram_placeholder_line(x))
    if placeholder_hits == len(sample_lines):
        return True

    # Allow one mildly off-pattern line in tiny payloads where most lines are
    # still obvious Instagram shell/preview placeholders.
    return len(sample_lines) >= 3 and placeholder_hits >= len(sample_lines) - 1


def nearest_comment_before_reply(lines: List[str], idx: int, max_back: int = 5) -> str:
    for step in range(1, max_back + 1):
        j = idx - step
        if j < 0:
            break

        cand = lines[j].strip()
        if not (3 <= len(cand) <= 280):
            continue
        if is_metadata_or_control_line(cand):
            continue
        if is_probable_handle_line(cand):
            continue
        if is_creator_cta_line(cand):
            continue

        return cand

    return ""


def infer_comment_like_lines(lines: List[str], caption: str, limit: int = 8) -> List[str]:
    caption_key = caption.lower().strip()
    out: List[str] = []
    seen = set()

    for raw in lines:
        t = raw.strip()
        if not (4 <= len(t) <= 220):
            continue
        if is_metadata_or_control_line(t):
            continue
        if is_probable_handle_line(t):
            continue
        if is_creator_cta_line(t):
            continue
        if is_contact_handoff_line(t):
            continue

        low = t.lower()
        if low == caption_key:
            continue
        if re.search(r"#\w+", t):
            continue
        if re.search(r"\b(comment|dm|follow|link in bio|book a call|join my|reply|respond)\b", low):
            continue
        if re.search(r"\b(drop|type)\b", low) and re.search(r"\b(comment|below)\b", low):
            continue

        audience_signal = bool(
            "?" in t
            or re.search(r"\b(i|you|this|that|same|why|how|scam|fake|bro|lol|wtf|proof|legal|cost|price|roi)\b", low)
        )
        if not audience_signal:
            continue

        # Keep short inferred comments only when they carry skepticism or execution signal.
        if len(t) < 10 and not (
            "?" in t
            or re.search(r"\b(why|how|scam|fake|proof|legal|cost|price|roi|where|when)\b", low)
        ):
            continue

        key = re.sub(r"\s+", " ", low).strip()
        if key in seen:
            continue

        seen.add(key)
        out.append(t)
        if len(out) >= limit:
            break

    return out


def extract_comment_cta_keywords(
    caption: str,
    signal_lines: Optional[List[str]] = None,
    transcript: Optional[str] = None,
) -> set[str]:
    low = caption.lower()
    if signal_lines:
        low = "\n".join([low, *[x.lower() for x in signal_lines[:6]]])
    if transcript:
        transcript_lines = transcript_cta_keyword_context(transcript, max_lines=24)
        if transcript_lines:
            low = "\n".join([low, *transcript_lines])
        if len(low) > 12000:
            low = low[:12000]

    kws: set[str] = set()
    blocked = {
        "below",
        "for",
        "to",
        "the",
        "this",
        "that",
        "your",
        "you",
        "if",
        "and",
        "with",
        "in",
        "on",
        "of",
        "it",
    }

    patterns = [
        r"comment\s+[\"'“”]([a-z0-9_]{2,24})[\"'“”]",
        r"comment\s+the\s+word\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?",
        r"comment\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?(?:\s|$)",
        r"comment\s+below\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?",
        r"comment\s+below\s+with\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?",
        r"comment\s+([a-z0-9_]{2,24})\s+below",
        r"comment\s+([a-z0-9_]{2,24})\s+for",
        r"comment\s+([a-z0-9_]{2,24})\s+to\s+(?:get|receive|steal|learn|unlock|build|join|start)",
        r"(?:type|drop)\s+the\s+word\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?",
        r"(?:type|drop)\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?\s+in\s+(?:the\s+)?comments",
        r"(?:type|drop)\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?\s+below",
        r"(?:reply|respond)\s+with\s+the\s+word\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?",
        r"(?:reply|respond)\s+(?:with\s+)?[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?(?:\s|$)",
        r"(?:dm|message)\s+me\s+the\s+word\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?",
        r"(?:dm|message)\s+me\s+[\"'“”]?([a-z0-9_]{2,24})[\"'“”]?\s+(?:for|to|get)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, low):
            kw = m.group(1).strip().lower()
            if kw and kw not in blocked:
                kws.add(kw)
                norm_kw = normalize_cta_token(kw)
                if norm_kw and norm_kw not in blocked:
                    kws.add(norm_kw)

    return kws


def is_low_signal_comment(comment: str, cta_keywords: set[str]) -> bool:
    t = comment.strip()
    if not t:
        return True

    low = t.lower()

    if is_replying_to_line(t):
        return True

    normalized = re.sub(r"[^a-z0-9_]+", " ", t.lower()).strip()
    tokens = [x for x in normalized.split() if x]

    # Emoji-only / symbol-only line
    if not tokens:
        return True

    norm_tokens = [normalize_cta_token(tok) for tok in tokens if normalize_cta_token(tok)]

    mention_count = len(re.findall(r"@[A-Za-z0-9._]{2,30}", t))
    without_mentions = re.sub(r"@[A-Za-z0-9._]{2,30}", " ", t)
    normalized_non_mentions = re.sub(r"[^a-z0-9_]+", " ", without_mentions.lower()).strip()
    non_mention_tokens = [
        normalize_cta_token(tok)
        for tok in normalized_non_mentions.split()
        if normalize_cta_token(tok)
    ]

    # Mention-only comments are usually tag-for-tag noise.
    if mention_count >= 1 and not non_mention_tokens:
        return True

    filler_tokens = {
        "pls",
        "plz",
        "please",
        "bro",
        "bruh",
        "yo",
        "hey",
        "sir",
        "maam",
        "boss",
        "me",
        "dm",
        "check",
        "sent",
        "done",
        "now",
        "rn",
        "inbox",
        "link",
        "info",
        "interested",
        "interest",
    }
    norm_filler_tokens = {
        normalize_cta_token(tok) for tok in filler_tokens if normalize_cta_token(tok)
    }

    # Generic acknowledgement-only replies add little analytical context even when
    # a specific CTA keyword was not recovered from caption/signal extraction.
    if len(norm_tokens) <= 3 and norm_tokens and all(tok in norm_filler_tokens for tok in norm_tokens):
        return True
    if mention_count >= 1 and len(non_mention_tokens) <= 2:
        if non_mention_tokens and all(tok in norm_filler_tokens for tok in non_mention_tokens):
            return True

    if cta_keywords:
        norm_keywords = {normalize_cta_token(k) for k in cta_keywords if normalize_cta_token(k)}

        if len(norm_tokens) == 1 and norm_tokens[0] in norm_keywords:
            return True
        if len(norm_tokens) <= 2 and all(tok in norm_keywords for tok in norm_tokens):
            return True
        if len(set(norm_tokens)) == 1 and norm_tokens[0] in norm_keywords and len(norm_tokens) <= 3:
            return True
        if len(norm_tokens) <= 4 and any(tok in norm_keywords for tok in norm_tokens):
            if all(tok in norm_keywords or tok in norm_filler_tokens for tok in norm_tokens):
                return True

        # Tag-heavy replies with only lightweight tails are usually giveaway/funnel noise.
        if mention_count >= 2 and len(non_mention_tokens) <= 2:
            if all(tok in norm_keywords or tok in norm_filler_tokens for tok in non_mention_tokens):
                return True

    low_context_reaction_tokens = {
        "wow",
        "crazy",
        "insane",
        "fire",
        "facts",
        "real",
        "true",
        "fr",
        "frfr",
        "fax",
        "bro",
        "bruh",
        "nice",
        "good",
        "great",
        "love",
        "amazing",
        "goat",
        "goated",
        "legend",
        "valid",
        "yes",
        "yess",
        "same",
        "ugh",
        "meh",
        "nah",
        "nope",
        "lol",
        "lmao",
        "lmfao",
        "omg",
        "sheesh",
        "damn",
        "yup",
        "yep",
        "ok",
        "okay",
        "cap",
        "capped",
        "sus",
        "mid",
        "cringe",
        "wild",
        "wildin",
        "w",
        "l",
        "algo",
        "algorithm",
        "fyp",
        "cfbr",
        "bump",
        "boost",
        "boosting",
        "visibility",
        "first",
        "early",
    }
    norm_low_context_reaction_tokens = {
        normalize_cta_token(tok)
        for tok in low_context_reaction_tokens
        if normalize_cta_token(tok)
    }

    low_context_stopword_tokens = {
        "this",
        "that",
        "it",
        "its",
        "is",
        "was",
        "are",
        "be",
        "to",
        "so",
        "very",
        "really",
        "just",
        "literally",
        "tho",
        "though",
    }
    norm_low_context_stopword_tokens = {
        normalize_cta_token(tok)
        for tok in low_context_stopword_tokens
        if normalize_cta_token(tok)
    }

    norm_substantive_tokens = {
        normalize_cta_token(tok)
        for tok in SUBSTANTIVE_COMMENT_TOKENS
        if normalize_cta_token(tok)
    }

    # Public contact-handoff chatter is usually funnel logistics, not audience
    # evidence (for example "whatsapp me", "text me at ...", "email me").
    if len(norm_tokens) <= 10 and is_contact_handoff_line(t):
        if not any(tok in norm_substantive_tokens for tok in norm_tokens):
            return True

    # Year-check nostalgia chatter (for example "who's here in 2026") is
    # usually low-context engagement noise rather than execution evidence.
    if len(norm_tokens) <= 12 and re.search(r"\b20\d{2}\b", low):
        if re.search(
            r"\b(?:who(?:'s| is)?\s+here|anyone\s+(?:else\s+)?(?:here|watching)|still\s+here|watching\s+in|here\s+in)\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Location roll-call chatter (for example "who's here from Nigeria") is
    # usually engagement-bait noise rather than execution-grade audience signal.
    if len(norm_tokens) <= 12:
        if re.search(
            r"\b(?:who(?:'s| is)?\s+(?:here|watching)\s+from|watching\s+from|anyone\s+from)\s+[a-z][a-z\s.'-]{1,30}\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Day-streak engagement chatter (for example "day 12 of asking" or
    # "until he notices") is usually algorithmic persistence noise rather
    # than decision-grade audience evidence.
    if len(norm_tokens) <= 12:
        if re.search(
            r"\bday\s+\d+\s+of\s+(?:asking|commenting|trying|posting|spamming|waiting)\b|\bpart\s+\d+\s+of\s+(?:asking|commenting)\b|\buntil\s+(?:he|she|they|you|u)\s+notice(?:s|d)?\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Numeric-only cheers (for example "100", "100%", "10/10") are usually
    # low-context approval noise rather than execution-relevant evidence.
    if len(norm_tokens) <= 3 and norm_tokens:
        if all(re.fullmatch(r"\d+(?:k|m|x)?", tok) for tok in norm_tokens):
            return True

    # Catch short reaction phrases where the only non-stopword tokens are generic
    # hype/skeptic reaction tokens (for example "this is crazy bro" or
    # "that is cap").
    if len(norm_tokens) <= 6:
        if any(tok in norm_substantive_tokens for tok in norm_tokens):
            return False
        informative_tokens = [
            tok for tok in norm_tokens if tok not in norm_low_context_stopword_tokens
        ]
        if informative_tokens and len(informative_tokens) <= 3:
            if all(tok in norm_low_context_reaction_tokens for tok in informative_tokens):
                return True

    # Common algorithm-bump comments (for example "algo", "boost", "fyp")
    # are usually distribution-noise rather than audience validation.
    if len(norm_tokens) <= 8:
        if re.search(
            r"\b(?:for|4)\s+the\s+algo(?:rithm)?\b|\bcomment(?:ing)?\s+for\s+reach\b|\bfor\s+reach\b|\bpush\s+(?:this|it|post)\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Visibility-push chatter (for example "for visibility", "bumping this",
    # "commenting to boost") is usually algorithm-gaming noise.
    if len(norm_tokens) <= 8:
        if re.search(
            r"\b(?:for|4)\s+visibility\b|\bvisibility\s+bump\b|\bbump(?:ing)?\s+(?:this|it|post)\b|\bcomment(?:ing)?\s+to\s+(?:boost|bump|push)\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Generic help-solicitation comments (for example "who need help?")
    # are usually lead-funnel noise and rarely add decision-grade context.
    if len(norm_tokens) <= 6:
        if re.search(r"\bwho\s+(?:need|needs)\s+help\b|\bneed\s+help\b", low):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Follow-back reciprocity chatter is typically growth-loop noise rather than
    # evidence about execution quality or claim validity.
    if len(norm_tokens) <= 8:
        if re.search(
            r"\b(?:follow\s*back|follow\s*for\s*follow|f4f|fb\s*please|mutuals?|follow\s*me\s*back)\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Growth-loop exchange chatter (for example "sub4sub", "support for support",
    # "let's grow together") is usually reciprocity noise, not decision-grade
    # audience evidence.
    if len(norm_tokens) <= 12:
        if re.search(
            r"\b(?:sub4sub|s4s|support\s*for\s*support|lets?\s+grow\s+together|grow\s+together|follow\s+train|engagement\s+train|engage\s+with\s+me)\b",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Story-share/repost acknowledgements are usually distribution chatter,
    # not decision-grade audience evidence (for example "shared to my story").
    if len(norm_tokens) <= 10:
        if re.search(
            r"\b(?:share(?:d|s|ing)?\s+(?:to|on)\s+(?:my\s+)?story|shared\s+story|sent\s+to\s+(?:my\s+)?story|repost(?:ed|ing)?|posted\s+(?:this|it))\b",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Short manifestation/faith-affirmation snippets are typically social
    # agreement noise in funnel-heavy threads, not decision-grade execution
    # evidence (for example "amen", "claiming this", "manifesting").
    if len(norm_tokens) <= 10:
        if re.search(
            r"\b(?:amen|claim(?:ing|ed)?(?:\s+(?:it|this))?|i\s+claim\s+this|manifest(?:ing|ed)?|in\s+jesus\s+name|god\s+did|bless(?:ed)?|praying\s+for\s+this)\b",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Giveaway-entry chatter is usually low-context participation noise
    # (for example "pick me", "hope I win", "I need this") rather than
    # execution-grade audience evidence.
    if len(norm_tokens) <= 10:
        if re.search(
            r"\b(?:pick|choose|select)\s+me\b|\blet\s+me\s+win\b|\bhope\s+i\s+win\b|\bi\s+(?:really\s+)?need\s+this\b|\bi\s+need\s+to\s+win\b|\bmy\s+turn\s+to\s+win\b",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Self-promo solicitation comments (for example "dm me I can help") are
    # usually offer-funnel noise from other commenters, not evidence about the
    # original claim quality.
    if len(norm_tokens) <= 12:
        if re.search(
            r"\b(?:dm|message|inbox|text)\s+me\b.*\b(?:help|service|agency|client|clients|work)\b|\b(?:i|we)\s+(?:can|could|will)?\s*help\s+(?:you|u)\b|\bi\s+do\s+this\s+too\b",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Tag-a-friend referral chatter (for example "@name check this", "tag @name")
    # is usually social distribution noise, not decision-grade audience evidence.
    if len(norm_tokens) <= 10 and mention_count >= 1:
        if re.search(
            r"\b(tag|tagging|tagged)\b.*@[a-z0-9._]{2,30}|@[a-z0-9._]{2,30}.*\b(check|look|watch|see)\b.*\b(this|it|here|out)\b|\b(sent|sending)\s+(?:this|it)\s+to\s+@[a-z0-9._]{2,30}",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Delivery-acknowledgement thread noise (for example "check your DM",
    # "DM sent", "sent you a DM") is usually funnel logistics, not evidence.
    if len(norm_tokens) <= 7:
        if re.search(
            r"\b(check|checked)\s+(?:your\s+)?(?:dms?|pms?)\b|\b(?:dms?|pms?)\s+(?:sent|send|check(?:ed)?)\b|\bsent\s+(?:you\s+)?(?:a\s+)?(?:dms?|pms?)\b|\bcheck\s+(?:inbox|pm)\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Meta instruction comments (for example "check pinned", "read caption",
    # "details in bio") are often funnel-routing chatter, not claim evidence.
    if len(norm_tokens) <= 10:
        if re.search(
            r"\b(check|read|see)\s+(?:the\s+)?(?:pinned|caption|bio|profile|story)\b|\b(?:details?|info)\s+(?:are\s+)?(?:in|on)\s+(?:the\s+)?(?:bio|profile|caption)\b|\b(?:in|on)\s+(?:my\s+)?(?:bio|profile)\b|\b(?:please\s+)?pin\s+(?:this|me|mine|my\s+comment|comment)\b|\bcan\s+you\s+pin\b|\bpinned?\s+comment\s+pls\b",
            low,
        ):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Short availability/logistics comments are typically funnel intent noise
    # (for example "still available?", "where link?", "any spots left?").
    if len(norm_tokens) <= 10:
        if is_intent_only_comment(t):
            if not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Short testimonial-style vouch comments are frequently social-proof noise
    # in funnel-heavy threads (for example "vouch", "he sent", "got mine",
    # "I can't stop winning").
    if len(norm_tokens) <= 8:
        if re.search(
            r"\b(vouch|legit|real\s+one|he\s+sent|she\s+sent|they\s+sent|got\s+(?:mine|it)|received\s+(?:mine|it)|works\s+for\s+me|worked\s+for\s+me|i\s+can(?:'|’)?t\s+stop\s+winning|still\s+winning|winning\s+daily)\b",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Short agreement-only snippets are usually low-context social proof
    # (for example "me too", "same here") unless they add concrete detail.
    if len(norm_tokens) <= 6:
        if re.search(r"\b(me too|same here|same bro|same lol)\b", low):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Short gratitude-only replies are usually acknowledgement noise
    # (for example "thanks bro", "appreciate it") unless they include
    # substantive execution or skepticism detail.
    if len(norm_tokens) <= 8:
        if re.search(r"\b(thanks|thank you|thx|ty|appreciate(?:\s+it|\s+you)?)\b", low):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    # Engagement-task completion chatter (for example "liked and followed",
    # "done shared") is usually giveaway/funnel logistics, not decision-grade
    # audience evidence.
    if len(norm_tokens) <= 10:
        engagement_action_tokens = {
            normalize_cta_token(tok)
            for tok in {
                "like",
                "liked",
                "follow",
                "followed",
                "share",
                "shared",
                "comment",
                "commented",
                "save",
                "saved",
                "repost",
                "reposted",
            }
            if normalize_cta_token(tok)
        }
        completion_tokens = {
            normalize_cta_token(tok)
            for tok in {"done", "completed", "finished"}
            if normalize_cta_token(tok)
        }
        action_hits = {tok for tok in norm_tokens if tok in engagement_action_tokens}
        has_completion_token = any(tok in completion_tokens for tok in norm_tokens)
        if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
            if len(action_hits) >= 2:
                return True
            if has_completion_token and len(action_hits) >= 1:
                return True

    # Save-for-later bookmark chatter is usually reminder noise, not execution
    # evidence (for example "saving this", "bookmarking", "for later").
    if len(norm_tokens) <= 10:
        if re.search(
            r"\b(?:save(?:d)?\s+(?:this|it|post)|saving\s+(?:this|it|for\s+later)|for\s+later|bookmark(?:ed|ing)?|come\s+back\s+(?:to|for)\s+this|coming\s+back\s+to\s+this|remind\s+me)\b",
            low,
        ):
            if "?" not in t and not any(tok in norm_substantive_tokens for tok in norm_tokens):
                return True

    if len(norm_tokens) <= 4:
        algo_bump_tokens = {
            normalize_cta_token(tok)
            for tok in {
                "algo",
                "algorithm",
                "fyp",
                "cfbr",
                "bump",
                "boost",
                "boosting",
                "reach",
                "engage",
                "engagement",
                "push",
                "pushing",
            }
            if normalize_cta_token(tok)
        }
        if any(tok in algo_bump_tokens for tok in norm_tokens):
            if all(
                tok in algo_bump_tokens
                or tok in norm_low_context_reaction_tokens
                or tok in norm_low_context_stopword_tokens
                for tok in norm_tokens
            ):
                return True

    if mention_count >= 2 and len(non_mention_tokens) <= 2:
        if any(tok in norm_substantive_tokens for tok in non_mention_tokens):
            return False
        if all(tok in norm_filler_tokens or tok in norm_low_context_reaction_tokens for tok in non_mention_tokens):
            return True

    if len(norm_tokens) <= 4:
        if any(tok in norm_substantive_tokens for tok in norm_tokens):
            return False
        if norm_tokens and all(tok in norm_low_context_reaction_tokens for tok in norm_tokens):
            return True
        if len(set(norm_tokens)) == 1 and norm_tokens[0] in norm_low_context_reaction_tokens:
            return True

    return False


def classify_low_signal_pattern(comment: str, cta_keywords: set[str]) -> str:
    t = comment.strip()
    low = t.lower()

    if not t:
        return "empty_or_symbol"

    if is_replying_to_line(t):
        return "thread_metadata"

    if is_contact_handoff_line(t):
        return "contact_handoff"

    normalized = re.sub(r"[^a-z0-9_]+", " ", low).strip()
    tokens = [normalize_cta_token(tok) for tok in normalized.split() if normalize_cta_token(tok)]
    mention_count = len(re.findall(r"@[A-Za-z0-9._]{2,30}", t))
    without_mentions = re.sub(r"@[A-Za-z0-9._]{2,30}", " ", low)
    normalized_non_mentions = re.sub(r"[^a-z0-9_]+", " ", without_mentions).strip()
    non_mention_tokens = [
        normalize_cta_token(tok)
        for tok in normalized_non_mentions.split()
        if normalize_cta_token(tok)
    ]

    if not tokens:
        return "empty_or_symbol"

    if len(tokens) <= 3 and all(re.fullmatch(r"\d+(?:k|m|x)?", tok) for tok in tokens):
        return "numeric_cheer"

    if len(tokens) <= 6 and re.search(r"\bwho\s+(?:need|needs)\s+help\b|\bneed\s+help\b", low):
        return "help_solicitation"

    if mention_count >= 1 and not non_mention_tokens:
        return "mention_only"

    if mention_count >= 2 and len(non_mention_tokens) <= 2:
        mention_filler_tokens = {
            normalize_cta_token(tok)
            for tok in {
                "pls",
                "plz",
                "please",
                "bro",
                "bruh",
                "yo",
                "hey",
                "sir",
                "maam",
                "boss",
                "me",
                "dm",
                "check",
                "sent",
                "done",
                "now",
                "rn",
                "inbox",
                "link",
                "info",
                "interested",
                "interest",
            }
            if normalize_cta_token(tok)
        }
        if non_mention_tokens and all(tok in mention_filler_tokens for tok in non_mention_tokens):
            return "mention_filler"

    if len(tokens) <= 12 and re.search(
        r"\b(?:who(?:'s| is)?\s+(?:here|watching)\s+from|watching\s+from|anyone\s+from)\s+[a-z][a-z\s.'-]{1,30}\b",
        low,
    ):
        return "location_rollcall"

    if len(tokens) <= 12 and re.search(r"\b20\d{2}\b", low):
        if re.search(
            r"\b(?:who(?:'s| is)?\s+here|anyone\s+(?:else\s+)?(?:here|watching)|still\s+here|watching\s+in|here\s+in)\b",
            low,
        ):
            return "year_check_nostalgia"

    if len(tokens) <= 12 and re.search(
        r"\bday\s+\d+\s+of\s+(?:asking|commenting|trying|posting|spamming|waiting)\b|\bpart\s+\d+\s+of\s+(?:asking|commenting)\b|\buntil\s+(?:he|she|they|you|u)\s+notice(?:s|d)?\b",
        low,
    ):
        return "day_streak_chatter"

    if len(tokens) <= 8 and re.search(
        r"\b(?:for|4)\s+the\s+algo(?:rithm)?\b|\bcomment(?:ing)?\s+for\s+reach\b|\bfor\s+reach\b|\bpush\s+(?:this|it|post)\b|\b(?:for|4)\s+visibility\b|\bvisibility\s+bump\b|\bbump(?:ing)?\s+(?:this|it|post)\b|\bcomment(?:ing)?\s+to\s+(?:boost|bump|push)\b",
        low,
    ):
        return "algorithm_chatter"

    if len(tokens) <= 10 and re.search(
        r"\b(check|read|see)\s+(?:the\s+)?(?:pinned|caption|bio|profile|story)\b|\b(?:details?|info)\s+(?:are\s+)?(?:in|on)\s+(?:the\s+)?(?:bio|profile|caption)\b|\b(?:in|on)\s+(?:my\s+)?(?:bio|profile)\b|\b(?:please\s+)?pin\s+(?:this|me|mine|my\s+comment|comment)\b|\bcan\s+you\s+pin\b|\bpinned?\s+comment\s+pls\b",
        low,
    ):
        return "meta_routing"

    if len(tokens) <= 10 and re.search(
        r"\b(?:pick|choose|select)\s+me\b|\blet\s+me\s+win\b|\bhope\s+i\s+win\b|\bi\s+(?:really\s+)?need\s+this\b|\bi\s+need\s+to\s+win\b|\bmy\s+turn\s+to\s+win\b",
        low,
    ):
        return "giveaway_entry"

    if len(tokens) <= 12 and re.search(
        r"\b(?:dm|message|inbox|text)\s+me\b.*\b(?:help|service|agency|client|clients|work)\b|\b(?:i|we)\s+(?:can|could|will)?\s*help\s+(?:you|u)\b|\bi\s+do\s+this\s+too\b",
        low,
    ):
        return "self_promo_solicitation"

    if len(tokens) <= 8 and re.search(
        r"\b(?:follow\s*back|follow\s*for\s*follow|f4f|fb\s*please|mutuals?|follow\s*me\s*back)\b",
        low,
    ):
        return "followback_reciprocity"

    if len(tokens) <= 12 and re.search(
        r"\b(?:sub4sub|s4s|support\s*for\s*support|lets?\s+grow\s+together|grow\s+together|follow\s+train|engagement\s+train|engage\s+with\s+me)\b",
        low,
    ):
        return "growth_loop_exchange"

    if len(tokens) <= 10 and mention_count >= 1 and re.search(
        r"\b(tag|tagging|tagged)\b.*@[a-z0-9._]{2,30}|@[a-z0-9._]{2,30}.*\b(check|look|watch|see)\b.*\b(this|it|here|out)\b|\b(sent|sending)\s+(?:this|it)\s+to\s+@[a-z0-9._]{2,30}",
        low,
    ):
        return "tag_referral"

    if len(tokens) <= 10 and re.search(
        r"\b(?:share(?:d|s|ing)?\s+(?:to|on)\s+(?:my\s+)?story|shared\s+story|sent\s+to\s+(?:my\s+)?story|repost(?:ed|ing)?|posted\s+(?:this|it))\b",
        low,
    ):
        return "story_share_repost"

    if len(tokens) <= 10 and re.search(
        r"\b(?:amen|claim(?:ing|ed)?(?:\s+(?:it|this))?|i\s+claim\s+this|manifest(?:ing|ed)?|in\s+jesus\s+name|god\s+did|bless(?:ed)?|praying\s+for\s+this)\b",
        low,
    ):
        return "manifestation_affirmation"

    if len(tokens) <= 8 and re.search(
        r"\b(vouch|legit|real\s+one|he\s+sent|she\s+sent|they\s+sent|got\s+(?:mine|it)|received\s+(?:mine|it)|works\s+for\s+me|worked\s+for\s+me|i\s+can(?:'|’)?t\s+stop\s+winning|still\s+winning|winning\s+daily)\b",
        low,
    ):
        return "testimonial_vouch"

    if len(tokens) <= 10 and re.search(
        r"\b(?:save(?:d)?\s+(?:this|it|post)|saving\s+(?:this|it|for\s+later)|for\s+later|bookmark(?:ed|ing)?|come\s+back\s+(?:to|for)\s+this|coming\s+back\s+to\s+this|remind\s+me)\b",
        low,
    ):
        return "save_for_later"

    if len(tokens) <= 8 and re.search(r"\b(thanks|thank\s+you|thx|ty|appreciate(?:\s+it|\s+you)?)\b", low):
        return "gratitude_only"

    if is_intent_only_comment(t):
        return "intent_only"

    norm_filler_tokens = {
        normalize_cta_token(tok)
        for tok in {
            "pls",
            "plz",
            "please",
            "bro",
            "bruh",
            "yo",
            "hey",
            "sir",
            "maam",
            "boss",
            "me",
            "dm",
            "check",
            "sent",
            "done",
            "now",
            "rn",
            "inbox",
            "link",
            "info",
            "interested",
            "interest",
        }
        if normalize_cta_token(tok)
    }
    if cta_keywords:
        norm_keywords = {
            normalize_cta_token(k)
            for k in cta_keywords
            if normalize_cta_token(k)
        }
        if tokens and len(tokens) <= 4 and any(tok in norm_keywords for tok in tokens):
            if all(tok in norm_keywords or tok in norm_filler_tokens for tok in tokens):
                return "cta_keyword_echo"

    if len(tokens) <= 10 and re.search(
        r"\b(?:like|liked|follow|followed|share|shared|comment|commented|save|saved|repost|reposted)\b",
        low,
    ):
        if re.search(r"\b(done|completed|finished)\b", low) or len(re.findall(
            r"\b(?:like|liked|follow|followed|share|shared|comment|commented|save|saved|repost|reposted)\b",
            low,
        )) >= 2:
            return "engagement_task_completion"

    if len(tokens) <= 7 and re.search(
        r"\b(check|checked)\s+(?:your\s+)?(?:dms?|pms?)\b|\b(?:dms?|pms?)\s+(?:sent|send|check(?:ed)?)\b|\bsent\s+(?:you\s+)?(?:a\s+)?(?:dms?|pms?)\b|\bcheck\s+(?:inbox|pm)\b",
        low,
    ):
        return "dm_logistics"

    if len(tokens) <= 6 and re.search(
        r"\b(wow|crazy|insane|fire|facts|real|true|fr|frfr|fax|nice|great|amazing|goat|goated|legend|valid|yes|same|nah|nope|lol|lmao|lmfao|omg|sheesh|damn|cap|sus|mid|cringe)\b",
        low,
    ):
        return "generic_reaction"

    return "other_low_signal"


def dominant_low_signal_pattern(pattern_counts: Dict[str, int]) -> str:
    if not pattern_counts:
        return "none"
    return max(sorted(pattern_counts.items()), key=lambda kv: kv[1])[0]


def dominant_low_signal_pattern_share(pattern_counts: Dict[str, int]) -> float:
    if not pattern_counts:
        return 0.0
    total = sum(max(0, int(v)) for v in pattern_counts.values())
    if total <= 0:
        return 0.0
    dominant = max(max(0, int(v)) for v in pattern_counts.values())
    return round(dominant / total, 2)


def is_intent_only_comment(comment: str) -> bool:
    t = comment.strip().lower()
    if not t:
        return True

    if re.search(r"\d", t):
        return False

    normalized = re.sub(r"[^a-z0-9_]+", " ", t).strip()
    tokens = [normalize_cta_token(tok) for tok in normalized.split() if normalize_cta_token(tok)]
    if not tokens:
        return True

    norm_substantive_tokens = {
        normalize_cta_token(tok)
        for tok in SUBSTANTIVE_COMMENT_TOKENS
        if normalize_cta_token(tok)
    }
    if any(tok in norm_substantive_tokens for tok in tokens):
        return False

    intent_tokens = {
        "interest",
        "interested",
        "info",
        "detail",
        "details",
        "link",
        "links",
        "dm",
        "sent",
        "check",
        "please",
        "pls",
        "plz",
        "me",
        "inbox",
        "book",
        "ready",
        "available",
        "availability",
        "join",
        "start",
        "started",
        "where",
        "how",
        "still",
        "spot",
        "spots",
    }
    norm_intent_tokens = {
        normalize_cta_token(tok)
        for tok in intent_tokens
        if normalize_cta_token(tok)
    }

    if len(tokens) <= 5 and all(tok in norm_intent_tokens for tok in tokens):
        return True

    # Catch short CTA-like replies such as "interested dm me" or "link pls".
    cta_intent_seeds = {"interested", "interest", "info", "link", "dm"}
    norm_cta_intent_seeds = {
        normalize_cta_token(tok)
        for tok in cta_intent_seeds
        if normalize_cta_token(tok)
    }
    if len(tokens) <= 4 and any(tok in norm_cta_intent_seeds for tok in tokens):
        if all(tok in norm_intent_tokens for tok in tokens):
            return True

    # Availability/logistics question variants are usually funnel intent
    # rather than substantive audience validation.
    if len(tokens) <= 8 and "?" in t:
        if all(tok in norm_intent_tokens for tok in tokens):
            return True

    return False


def is_question_prompt_line(line: str) -> bool:
    low = line.strip().lower()
    if not low:
        return False

    if low.endswith("?"):
        return True

    question_starts = [
        r"^(how|why|what|when|where|who|which)\b",
        r"^(can|could|would|should|do|does|did|is|are|am|will)\b",
        r"^(anyone|thoughts|help|advice)\b",
    ]
    if any(re.search(pat, low) for pat in question_starts):
        return True

    if re.search(r"\b(can someone|anyone know|is this|does this|how do i)\b", low):
        return True

    return False


def is_numeric_self_report_question(line: str) -> bool:
    low = line.strip().lower()
    if not low or not is_question_prompt_line(line):
        return False

    if not re.search(r"\$\d|\b\d+\s*(k|m|%|/|per|mo|month|months|day|days)\b", low):
        return False

    self_report_patterns = [
        r"\bhow\s+i\s+(made|make|earned|earn|generated|generate|booked|book|closed|close|hit|grow|grew)\b",
        r"\b(i|we)\s+(made|make|earned|earn|generated|generate|booked|book|closed|close|hit)\b",
        r"\bfrom\s+\$?\d[\d,\.]*\s*(?:k|m)?\s+to\s+\$?\d[\d,\.]*\s*(?:k|m)?\b",
        r"\b(i|we)\s+(scaled|scale|grew|grow)\b.*\bto\b.*\$\d",
    ]
    return any(re.search(pat, low) for pat in self_report_patterns)


def extract_claim_lines(
    caption: str,
    signal_lines: List[str],
    comments: List[str],
    transcript: Optional[str] = None,
    limit: int = 12,
) -> List[str]:
    creator_candidates = [caption, *signal_lines]
    transcript_candidates: List[str] = []
    if transcript:
        transcript_candidates = [
            x.strip() for x in re.split(r"(?:\n+|(?<=[.!?])\s+)", transcript) if x.strip()
        ]

    # Prioritize creator-origin signals (caption/signal/transcript) and only then
    # consider audience comments as supplemental claim evidence.
    candidate_groups = [
        ("creator", creator_candidates),
        ("transcript", transcript_candidates),
        ("comment", comments),
    ]

    out: List[str] = []
    seen = set()
    comment_claim_count = 0
    cta_only_claim_count = 0
    substantive_claim_added = 0
    max_comment_claims = max(2, limit // 3)

    for source, group in candidate_groups:
        for raw in group:
            t = raw.strip()
            if not t:
                continue
            low = t.lower()

            if source == "transcript" and len(t) < 24:
                continue

            if re.search(r"view all|what if|wtf|disgusting|sorry but|congratulations|my good heart", low):
                continue
            if is_hashtag_heavy_text(t):
                continue

            # Skip common access-wall/login copy so it does not get misclassified as a business claim.
            if has_instagram_access_wall_copy(low):
                continue

            numeric_signal = bool(re.search(r"\$\d|\b\d+\s*(k|m|%|/|per|mo|month|months|day|days)\b", low))
            funnel_signal = bool(re.search(r"\b(comment|dm|link in bio|book|appointment|call)\b", low))

            if is_question_prompt_line(t):
                if not numeric_signal:
                    continue
                if not is_numeric_self_report_question(t):
                    continue

            business_patterns = [
                r"\bmake money\b",
                r"\bincome\b",
                r"\brevenue\b",
                r"\bprofit\b",
                r"\bclient\b",
                r"\boffer\b",
                r"\bagency\b",
                r"\bautomation\b",
                r"\bwholesale\b",
                r"\bcontract\b",
                r"\bservice\b",
                r"\blead\b",
                r"\bclosing\b",
                r"\bclose\s+rate\b",
                r"\bclose\s+deals?\b",
                r"\bclosed\s+deals?\b",
                r"\bsystem\b",
                r"\bmethod\b",
            ]
            business_signal = any(re.search(pat, low) for pat in business_patterns)

            if not (numeric_signal or funnel_signal or business_signal):
                continue

            if source == "comment":
                # Comments can be useful, but we cap and require stronger self-report
                # language so audience chatter does not dominate claim evidence.
                if comment_claim_count >= max_comment_claims and len(out) >= max(3, limit // 2):
                    continue
                if is_intent_only_comment(t):
                    continue
                if not numeric_signal and not re.search(r"\b(i|we|my|our|me|us)\b", low):
                    continue

            cta_only_claim = is_cta_prompt_dominant_line(t)
            if cta_only_claim:
                # Keep CTA-dominant lines only as light context. Prioritize
                # substantive claim evidence and avoid comment/transcript CTA
                # prompts crowding out claim quality.
                if source != "creator":
                    continue
                if substantive_claim_added >= 2:
                    continue
                if cta_only_claim_count >= 1 and substantive_claim_added >= 1:
                    continue
                if cta_only_claim_count >= 2 and len(out) >= 2:
                    continue

            key = normalized_text_key(t)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(t)
            if source == "comment":
                comment_claim_count += 1
            if cta_only_claim:
                cta_only_claim_count += 1
            elif is_substantive_claim_line(t):
                substantive_claim_added += 1
            if len(out) >= limit:
                return out

    return out


def is_cta_prompt_dominant_line(line: str) -> bool:
    low = line.strip().lower()
    if not low:
        return False

    cta_patterns = [
        r"\b(comment|dm|reply|respond|follow|link in bio|book a call|join my|free course|free guide|drop the word|type the word)\b",
        r"\bcomment\s+[\"'“”]?[a-z0-9_]{2,24}[\"'“”]?\s+(?:below|for|to)\b",
        r"\bdm\s+[\"'“”]?[a-z0-9_]{2,24}[\"'“”]?\b",
    ]
    if not any(re.search(pat, low) for pat in cta_patterns):
        return False

    if is_numeric_self_report_question(line):
        return False

    substantive_anchor_patterns = [
        r"\b(i|we|my|our)\b.*\b(made|make|earned|earn|generated|generate|booked|book|closed|close|scaled|scale|profit|revenue|clients?)\b",
        r"\b(revenue|profit|margin|roi|close\s+rate|conversion\s+rate)\b",
        r"\$\d|\b\d+\s*(k|m|%)\b",
    ]
    if any(re.search(pat, low) for pat in substantive_anchor_patterns):
        return False

    return True


def is_substantive_claim_line(line: str) -> bool:
    low = line.strip().lower()
    if not low:
        return False

    if is_cta_prompt_dominant_line(line):
        return False

    numeric_signal = bool(re.search(r"\$\d|\b\d+\s*(k|m|%|/|per|mo|month|months|day|days)\b", low))
    if numeric_signal:
        if is_question_prompt_line(line) and not is_numeric_self_report_question(line):
            return False
        return True

    if is_question_prompt_line(line):
        return False

    business_patterns = [
        r"\bmake money\b",
        r"\bincome\b",
        r"\brevenue\b",
        r"\bprofit\b",
        r"\bclient\b",
        r"\boffer\b",
        r"\bagency\b",
        r"\bautomation\b",
        r"\bwholesale\b",
        r"\bcontract\b",
        r"\bservice\b",
        r"\blead\b",
        r"\bclosing\b",
        r"\bclose\s+rate\b",
        r"\bclose\s+deals?\b",
        r"\bclosed\s+deals?\b",
        r"\bsystem\b",
        r"\bmethod\b",
    ]
    return any(re.search(pat, low) for pat in business_patterns)


def transcript_information_metrics(transcript: str) -> tuple[int, float]:
    tokens = re.findall(r"[a-z0-9_']+", transcript.lower())
    norm_tokens = [normalize_cta_token(tok) for tok in tokens if normalize_cta_token(tok)]
    if not norm_tokens:
        return 0, 0.0

    unique_ratio = len(set(norm_tokens)) / len(norm_tokens)
    return len(norm_tokens), unique_ratio


def transcript_opening_repetition_artifact(transcript: str) -> tuple[bool, str, int]:
    tokens = [
        normalize_cta_token(tok)
        for tok in re.findall(r"[a-z0-9_']+", transcript.lower())
        if normalize_cta_token(tok)
    ]
    if len(tokens) < 10:
        return False, "", 0

    # Repeated opener artifacts (for example "nine to five" repeated several
    # times) are common in noisy ASR and can overstate transcript quality.
    opening = tokens[:45]
    for n in range(2, 6):
        if len(opening) < n * 3:
            continue
        for i in range(0, len(opening) - n * 3 + 1):
            phrase = opening[i : i + n]
            if not phrase:
                continue

            repeat = 1
            j = i + n
            while j + n <= len(opening) and opening[j : j + n] == phrase:
                repeat += 1
                j += n

            if repeat >= 3:
                return True, " ".join(phrase), repeat

    return False, "", 0


def parse_jina_markdown(
    md: str,
) -> tuple[str, str, str, List[str], List[str], int, str, int, Dict[str, int], List[str], str]:
    title = ""
    source_url = ""
    caption = ""

    m_title = re.search(r"^Title:\s*(.+)$", md, re.MULTILINE)
    if m_title:
        title = m_title.group(1).strip()

    m_source = re.search(r"^URL Source:\s*(.+)$", md, re.MULTILINE)
    if m_source:
        source_url = m_source.group(1).strip()

    # Extract quoted caption from title format:
    # "X on Instagram: \"...\""
    m_caption_title = re.search(r'on Instagram:\s*"?(.*)$', title)
    if m_caption_title:
        maybe = m_caption_title.group(1).strip().strip('"')
        if maybe:
            caption = maybe

    hashtags = sorted({h.lower() for h in re.findall(r"#([A-Za-z0-9_]+)", md)})

    # Heuristic for comment lines: capture nearest plausible comment text before each Reply marker
    comments: List[str] = []
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if is_reply_marker(ln):
            cand = nearest_comment_before_reply(lines, i, max_back=5)
            if cand:
                comments.append(cand)

    explicit_comment_candidates = len(comments)

    signal_lines = extract_signal_lines(md, limit=25)

    cta_keywords = extract_comment_cta_keywords(caption, signal_lines=signal_lines)

    # Deduplicate keep order
    dedup_comments = []
    seen = set()
    filtered_low_signal_comments = 0
    low_signal_pattern_counts: Counter[str] = Counter()
    for c in comments:
        cleaned = strip_leading_mentions(c)
        if not cleaned:
            continue
        if is_creator_cta_line(cleaned):
            continue
        if is_low_signal_comment(cleaned, cta_keywords):
            filtered_low_signal_comments += 1
            low_signal_pattern_counts[classify_low_signal_pattern(cleaned, cta_keywords)] += 1
            continue
        key = normalized_text_key(cleaned)
        if key in seen:
            continue
        seen.add(key)
        dedup_comments.append(cleaned)

    explicit_count = len(dedup_comments)
    inferred_count = 0

    # Only infer comment-like lines when explicit reply blocks were not captured.
    # If explicit comments were found but filtered as low-signal, keep context
    # conservative instead of backfilling inferred lines from body text.
    if len(dedup_comments) < 2 and explicit_comment_candidates == 0:
        inferred = infer_comment_like_lines(signal_lines, caption=caption, limit=8)
        for c in inferred:
            cleaned = strip_leading_mentions(c)
            if not cleaned:
                continue
            if is_creator_cta_line(cleaned):
                continue
            if is_low_signal_comment(cleaned, cta_keywords):
                filtered_low_signal_comments += 1
                low_signal_pattern_counts[classify_low_signal_pattern(cleaned, cta_keywords)] += 1
                continue
            key = normalized_text_key(cleaned)
            if key in seen:
                continue
            seen.add(key)
            dedup_comments.append(cleaned)
            inferred_count += 1

    if explicit_count > 0 and inferred_count > 0:
        comment_context_source = "mixed"
    elif explicit_count > 0:
        comment_context_source = "explicit"
    elif inferred_count > 0:
        comment_context_source = "inferred"
    else:
        comment_context_source = "none"

    if not caption and signal_lines:
        caption = signal_lines[0]

    extracted_text = "\n\n".join(
        [
            part
            for part in [
                title,
                caption,
                "\n".join(signal_lines[:10]),
                "\n".join(dedup_comments[:25]),
            ]
            if part
        ]
    )

    return (
        title,
        source_url,
        caption,
        hashtags,
        dedup_comments[:25],
        explicit_comment_candidates,
        comment_context_source,
        filtered_low_signal_comments,
        dict(low_signal_pattern_counts),
        signal_lines,
        extracted_text,
    )


def command_exists(name: str) -> bool:
    return subprocess.call(
        ["bash", "-lc", f"command -v {shlex.quote(name)} >/dev/null 2>&1"]
    ) == 0


def run_cmd(cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-lc", cmd],
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def try_transcript(url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns: transcript, method, error
    Methods tried:
      1) yt-dlp + local whisper CLI (if installed)
      2) yt-dlp + whisper-cpp (whisper-cli + local model)
      3) yt-dlp + OpenAI transcription API (if OPENAI_API_KEY exists)
    """
    if not command_exists("yt-dlp"):
        return None, None, "yt-dlp not installed"

    with tempfile.TemporaryDirectory(prefix="reel-intel-") as td:
        td_path = Path(td)
        out_tmpl = td_path / "input.%(ext)s"

        dl = run_cmd(
            f"yt-dlp --no-playlist -f ba -o {shlex.quote(str(out_tmpl))} {shlex.quote(url)}",
            timeout=240,
        )
        if dl.returncode != 0:
            err = (dl.stderr or dl.stdout or "download failed").strip()
            return None, None, f"yt-dlp failed: {err[:400]}"

        files = list(td_path.glob("input.*"))
        if not files:
            return None, None, "downloaded media file not found"
        media = files[0]

        # local whisper cli path
        if command_exists("whisper"):
            out_dir = td_path / "whisper_out"
            out_dir.mkdir(exist_ok=True)
            wh = run_cmd(
                " ".join(
                    [
                        "whisper",
                        shlex.quote(str(media)),
                        "--model base",
                        "--output_format txt",
                        "--output_dir",
                        shlex.quote(str(out_dir)),
                        "--language en",
                    ]
                ),
                timeout=900,
            )
            if wh.returncode == 0:
                txt_files = list(out_dir.glob("*.txt"))
                if txt_files:
                    return txt_files[0].read_text(errors="replace").strip(), "local-whisper-cli", None

        # whisper-cpp fallback (homebrew whisper-cli)
        if command_exists("whisper-cli"):
            default_models = [
                Path("/home/chris/.openclaw/workspace/tools/reel-intel/models/ggml-tiny.en.bin"),
                Path("/home/chris/.openclaw/workspace/tools/reel-intel/models/ggml-base.en.bin"),
                Path("/home/chris/.openclaw/workspace/tools/reel-intel/models/ggml-small.en.bin"),
            ]
            model_path = next((m for m in default_models if m.exists()), None)
            if model_path:
                wav_path = td_path / "input.wav"
                ff = run_cmd(
                    f"ffmpeg -y -i {shlex.quote(str(media))} -ac 1 -ar 16000 {shlex.quote(str(wav_path))}",
                    timeout=180,
                )
                if ff.returncode == 0 and wav_path.exists():
                    out_prefix = td_path / "whispercpp"
                    wc = run_cmd(
                        " ".join(
                            [
                                "whisper-cli",
                                "-m",
                                shlex.quote(str(model_path)),
                                "-f",
                                shlex.quote(str(wav_path)),
                                "-l en",
                                "-otxt",
                                "-of",
                                shlex.quote(str(out_prefix)),
                            ]
                        ),
                        timeout=1200,
                    )
                    out_txt = td_path / "whispercpp.txt"
                    if wc.returncode == 0 and out_txt.exists():
                        return out_txt.read_text(errors="replace").strip(), "whisper-cpp-local", None

        # openai API via curl if key present
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if api_key and command_exists("curl"):
            # Use a strong speech model name fallback chain handled by API if available
            curl_cmd = " ".join(
                [
                    "curl -sS https://api.openai.com/v1/audio/transcriptions",
                    f"-H {shlex.quote('Authorization: Bearer ' + api_key)}",
                    "-F model=gpt-4o-mini-transcribe",
                    f"-F file=@{shlex.quote(str(media))}",
                ]
            )
            api = run_cmd(curl_cmd, timeout=300)
            if api.returncode == 0 and api.stdout.strip():
                try:
                    payload = json.loads(api.stdout)
                    if isinstance(payload, dict) and payload.get("text"):
                        return str(payload["text"]).strip(), "openai-transcription-api", None
                    # error body from API
                    if isinstance(payload, dict) and payload.get("error"):
                        return None, None, f"openai api error: {payload.get('error')}"
                except json.JSONDecodeError:
                    pass

        return None, None, "no transcription backend available (install whisper/whisper-cpp or set OPENAI_API_KEY)"


def detect_niche(text: str, hashtags: List[str]) -> str:
    t = (text + " " + " ".join(hashtags)).lower()

    if any(k in t for k in ["onlyfans", "ai girl", "ai girls", "nsfw", "adult", "fansly", "creator agency"]):
        return "ai-onlyfans"
    if any(k in t for k in ["wholesal", "foreclosure", "zillow", "fixandflip", "realestate", "rentalproperty"]):
        return "real-estate-wholesale"
    if any(k in t for k in ["websitebuilding", "vibecoding", "21st", "cursor", "landing page", "ui component", "saas"]):
        return "ui-build"
    return "general-side-hustle"


def score_hype_risk(text: str, hashtags: List[str], comments: List[str]) -> tuple[int, List[str], List[str]]:
    t = text.lower()
    joined_comments = "\n".join(comments).lower()

    score = 20
    red_flags: List[str] = []
    green_flags: List[str] = []

    def add(points: int, reason: str):
        nonlocal score
        score += points
        red_flags.append(reason)

    def subtract(points: int, reason: str):
        nonlocal score
        score -= points
        green_flags.append(reason)

    if re.search(r"comment\s+[a-z0-9_]+\s+to", t):
        add(12, "Keyword-comment funnel pattern detected")
    if (
        any(
            k in t
            for k in [
                "follow and comment",
                "comment and",
                "comment jobs",
                "comment to steal",
                "steal my strategy",
            ]
        )
        or re.search(r"follow\s*(?:&|and)\s*comment", t)
        or re.search(r"comment\s+[\"'“”]?[a-z0-9_]+[\"'“”]?\s+below", t)
    ):
        add(10, "Engagement-bait CTA detected")
    if any(k in t for k in ["dm me", "link in bio", "academy", "course", "mentorship", "join my"]):
        add(12, "Likely lead funnel into offer/course")
    if any(k in t for k in ["quit your job", "free money", "being broke is a decision", "easy money", "literally free"]):
        add(10, "High-hype framing / oversimplified outcome claims")
    if any(k in t for k in ["passive income", "make money online", "faceless", "repost"]):
        add(6, "Generic passive-income framing")
    if any(k in t for k in ["same payout screenshot", "same video", "copied video", "another video"]):
        add(12, "Potential recycled or misleading proof signals")
    if len(hashtags) >= 12:
        add(8, "Heavy hashtag spray suggests distribution over depth")
    if any(k in t for k in ["crypto", "forex", "dropship", "shopify"]) and any(
        k in t for k in ["realestate", "wholesale", "fixandflip"]
    ):
        add(10, "Cross-niche money hashtags can indicate broad hype targeting")

    # Counter signals
    if re.search(r"\$\d{2,}", t):
        subtract(4, "Contains concrete dollar figures")
    if any(k in t for k in ["contract", "title", "closing", "assignment", "earnest"]):
        subtract(6, "Mentions operational mechanics")
    if any(k in t for k in ["here are the links", "site list", "job board", "step by step"]):
        subtract(4, "Contains at least one concrete implementation cue")
    if any(
        k in (joined_comments + "\n" + t)
        for k in [
            "what contract",
            "license",
            "can bypass",
            "too good to be true",
            "course",
            "legal",
            "same payout screenshot",
            "scam",
        ]
    ):
        add(8, "Audience skepticism highlights credibility risk")

    score = max(0, min(100, score))
    return score, red_flags, green_flags


def extraction_confidence(
    title: str,
    caption: str,
    comments: List[str],
    comment_context_source: str,
    explicit_comment_candidates: int,
    filtered_low_signal_comments: int,
    low_signal_primary_pattern: str,
    low_signal_primary_pattern_share: float,
    signal_lines: List[str],
    claim_lines: List[str],
    transcript: Optional[str],
    transcript_error: Optional[str],
    looks_like_access_wall: bool,
    looks_like_placeholder_payload: bool,
) -> tuple[int, List[str]]:
    score = 5
    notes: List[str] = []

    if len(title.strip()) >= 20:
        score += 15
        notes.append("Strong title extraction")
    elif title.strip():
        score += 8
        notes.append("Short title extracted")
    else:
        notes.append("No reliable title extracted")

    if caption.strip():
        if is_hashtag_heavy_text(caption):
            score += 6
            notes.append("Caption extracted but mostly hashtag-heavy, so semantic detail is limited")
        elif len(caption.strip()) >= 60:
            score += 20
            notes.append("Caption length is sufficient for analysis")
        else:
            score += 10
            notes.append("Caption is present but short")
    else:
        notes.append("Caption not confidently extracted")

    caption_key = normalized_text_key(caption) if caption.strip() else ""
    comment_keys = {normalized_text_key(c) for c in comments if c.strip()}

    effective_signal_count = 0
    overlapping_signal_count = 0
    cta_or_handle_signal_count = 0
    for line in signal_lines:
        key = normalized_text_key(line)
        if not key:
            continue
        if is_creator_cta_line(line) or is_probable_handle_line(line):
            cta_or_handle_signal_count += 1
            continue
        if key == caption_key or key in comment_keys:
            overlapping_signal_count += 1
            continue
        effective_signal_count += 1

    if effective_signal_count >= 5:
        score += 20
        notes.append("Multiple body signal lines captured")
    elif effective_signal_count >= 2:
        score += 12
        notes.append("Some body signal lines captured")
    elif effective_signal_count == 1:
        score += 6
        notes.append("Only one body signal line captured")
    else:
        notes.append("No independent body signal lines captured")

    if overlapping_signal_count >= 2:
        notes.append("Some signal lines overlap with caption/comments and were down-weighted")

    if cta_or_handle_signal_count >= 2:
        score -= 4
        notes.append("Many captured body lines look like CTA or handle-style text, so independent evidence is limited")
    elif cta_or_handle_signal_count == 1 and effective_signal_count <= 1:
        score -= 2
        notes.append("One captured body line looked like CTA or handle-style text, slightly reducing confidence")

    substantive_claim_count = sum(1 for line in claim_lines if is_substantive_claim_line(line))
    cta_only_claim_count = len(claim_lines) - substantive_claim_count

    if substantive_claim_count >= 3:
        score += 15
        notes.append("Multiple substantive claim lines extracted for analysis")
    elif substantive_claim_count >= 1:
        score += 7
        notes.append("At least one substantive claim line extracted")
    elif cta_only_claim_count >= 1:
        score -= 3
        notes.append("Claim lines are mostly CTA prompts with limited economic detail")
    else:
        score -= 8
        notes.append("No clear claim lines extracted; analysis may be reaction-heavy")

    if cta_only_claim_count >= 3 and substantive_claim_count == 0:
        score -= 3
        notes.append("Most claim lines are CTA-only prompts, so claim evidence depth is limited")
    elif len(claim_lines) >= 3 and cta_only_claim_count > substantive_claim_count:
        score -= 2
        notes.append(
            "CTA-style claim lines outnumber substantive claims, so claim-evidence quality is mixed"
        )

    if len(claim_lines) >= 3:
        claim_keys = [normalized_text_key(x) for x in claim_lines if normalized_text_key(x)]
        if claim_keys:
            claim_unique_ratio = len(set(claim_keys)) / len(claim_keys)
            if claim_unique_ratio <= 0.6:
                score -= 4
                notes.append("Claim lines are highly repetitive, so independent claim evidence is limited")

    normalized_comment_keys = {
        normalized_text_key(strip_leading_mentions(c))
        for c in comments
        if normalized_text_key(strip_leading_mentions(c))
    }
    claim_from_comment_count = sum(
        1 for line in claim_lines if normalized_text_key(line) in normalized_comment_keys
    )
    if claim_lines and comments:
        claim_from_comment_ratio = claim_from_comment_count / len(claim_lines)
        if claim_from_comment_count >= 3 and claim_from_comment_ratio >= 0.6:
            score -= 4
            notes.append(
                "Most claim lines came from audience comments instead of creator/transcript text, reducing claim-evidence reliability"
            )
        elif len(claim_lines) >= 2 and claim_from_comment_ratio >= 0.5:
            score -= 2
            notes.append(
                "Claim evidence leans on audience comments more than creator/transcript text, slightly reducing reliability"
            )

    creator_claim_keys = {
        normalized_text_key(x)
        for x in [caption, *signal_lines]
        if normalized_text_key(x) and not is_creator_cta_line(x) and not is_probable_handle_line(x)
    }
    transcript_claim_keys: set[str] = set()
    if transcript:
        for part in re.split(r"(?:\n+|(?<=[.!?])\s+)", transcript):
            key = normalized_text_key(part)
            if key:
                transcript_claim_keys.add(key)

    substantive_claim_keys = [
        normalized_text_key(line)
        for line in claim_lines
        if is_substantive_claim_line(line) and normalized_text_key(line)
    ]
    substantive_claims_from_creator_or_transcript = sum(
        1
        for key in substantive_claim_keys
        if key in creator_claim_keys or key in transcript_claim_keys
    )

    if substantive_claim_keys and substantive_claims_from_creator_or_transcript == 0:
        score -= 5
        notes.append(
            "Substantive claim lines were not found in creator/transcript text, so confidence is reduced until primary-source evidence appears"
        )

    if comment_context_source == "inferred" and substantive_claim_count <= 1:
        score -= 5
        notes.append(
            "Substantive claim evidence is thin and comment context is inferred; confidence reduced until explicit comments/transcript are captured"
        )

    if len(comments) >= 4:
        if comment_context_source == "explicit":
            score += 18
            notes.append("Comment context captured from explicit reply blocks")
        elif comment_context_source == "mixed":
            score += 14
            notes.append("Mixed comment context captured (explicit + inferred)")
        elif comment_context_source == "inferred":
            score += 10
            notes.append("Inferred comment context captured (no explicit reply blocks)")
        else:
            notes.append("No comments captured")
    elif len(comments) >= 1:
        if comment_context_source == "explicit":
            score += 8
            notes.append("Limited explicit comment context captured")
        elif comment_context_source == "mixed":
            score += 6
            notes.append("Limited mixed comment context captured")
        elif comment_context_source == "inferred":
            score += 5
            notes.append("Limited inferred comment context captured")
        else:
            notes.append("No comments captured")
    else:
        notes.append("No comments captured")

    if (
        comment_context_source in {"inferred", "none"}
        and explicit_comment_candidates == 0
        and len(comments) <= 1
        and not (transcript and transcript.strip())
    ):
        score -= 3
        notes.append(
            "Comment context is inferred with minimal comments and no explicit reply blocks, so audience evidence is thin"
        )

    if explicit_comment_candidates > 0:
        kept_comment_ratio = len(comments) / explicit_comment_candidates
        notes.append(
            f"Explicit comment retention: {len(comments)}/{explicit_comment_candidates} ({int(round(kept_comment_ratio * 100))}%)"
        )

        if not comments:
            score -= 4
            notes.append(
                "Explicit reply blocks were detected, but all comment candidates were filtered as low-signal"
            )

        if explicit_comment_candidates >= 4 and kept_comment_ratio <= 0.25:
            score -= 3
            notes.append(
                "Most explicit comment candidates were filtered as low-signal, so audience-context reliability is reduced"
            )

        if explicit_comment_candidates >= 6 and kept_comment_ratio <= 0.15:
            score -= 2
            notes.append(
                "Explicit comment retention is extremely low, suggesting extracted audience context is fragile"
            )

        if explicit_comment_candidates >= 8 and len(comments) <= 2:
            score -= 2
            notes.append(
                "Very few comments survived from many explicit candidates, so audience-context confidence is reduced"
            )

    if comments:
        short_comment_count = 0
        intent_only_count = 0
        for c in comments:
            word_count = len([x for x in re.sub(r"[^a-z0-9_]+", " ", c.lower()).split() if x])
            if word_count <= 2:
                short_comment_count += 1
            if is_intent_only_comment(c):
                intent_only_count += 1
        if short_comment_count == len(comments):
            score -= 4
            notes.append("Comment context is short/low-depth across all captured comments")
        elif short_comment_count / len(comments) >= 0.6:
            score -= 2
            notes.append("Most captured comments are very short; confidence slightly reduced")

        if len(comments) >= 2 and intent_only_count == len(comments):
            score -= 6
            notes.append("Captured comments look like intent-only CTA replies, so audience context depth is limited")
        elif len(comments) >= 3 and intent_only_count / len(comments) >= 0.6:
            score -= 3
            notes.append("Most captured comments look intent-only (for example link/info requests), reducing confidence")

        normalized_comments: List[str] = []
        for c in comments:
            key = normalized_text_key(strip_leading_mentions(c))
            if key:
                normalized_comments.append(key)
        if len(normalized_comments) >= 4:
            counts = Counter(normalized_comments)
            dominant_ratio = max(counts.values()) / len(normalized_comments)
            unique_comment_ratio = len(counts) / len(normalized_comments)
            if dominant_ratio >= 0.6:
                score -= 4
                notes.append(
                    "Captured comments are highly repetitive around one phrase, so audience-context diversity is limited"
                )
            elif unique_comment_ratio <= 0.5:
                score -= 2
                notes.append("Captured comments have low diversity, slightly reducing confidence")

    if filtered_low_signal_comments >= 6 and len(comments) <= 1:
        score -= 7
        notes.append(
            "Many recovered comments were low-signal CTA echoes and got filtered out, so usable audience context is thin"
        )
    elif filtered_low_signal_comments >= 3 and len(comments) <= 2:
        score -= 4
        notes.append(
            "Several recovered comments were filtered as low-signal CTA echoes, reducing confidence in comment-derived context"
        )

    if comments and filtered_low_signal_comments >= 8 and filtered_low_signal_comments >= len(comments) * 2:
        score -= 5
        notes.append(
            "Low-signal filtered comment volume is much higher than kept comments, so audience-context confidence is reduced"
        )

    if comment_context_source == "inferred" and filtered_low_signal_comments >= 5 and len(comments) <= 2:
        score -= 4
        notes.append(
            "Comment context is inferred and many recovered comments were filtered as low-signal, so audience evidence remains fragile"
        )

    if filtered_low_signal_comments > 0 and low_signal_primary_pattern != "none":
        notes.append(
            f"Primary filtered low-signal pattern: {low_signal_primary_pattern}"
        )
        if low_signal_primary_pattern_share > 0:
            notes.append(
                f"Dominant low-signal pattern share: {int(round(low_signal_primary_pattern_share * 100))}%"
            )
        if (
            filtered_low_signal_comments >= 4
            and low_signal_primary_pattern in LOW_CONTEXT_DOMINANT_PATTERNS
        ):
            score -= 2
            notes.append(
                "Filtered-noise mix is dominated by low-context chatter, so audience-context confidence is reduced"
            )

        if (
            filtered_low_signal_comments >= 5
            and low_signal_primary_pattern_share >= 0.70
            and low_signal_primary_pattern in LOW_CONTEXT_DOMINANT_PATTERNS
        ):
            score -= 2
            notes.append(
                "Filtered low-signal comments are heavily concentrated in one low-context pattern, reducing audience-context confidence"
            )

    if transcript and transcript.strip():
        transcript_tokens, transcript_unique_ratio = transcript_information_metrics(transcript)
        transcript_parts = transcript_segments(transcript, max_segments=80)
        has_repetitive_opening, repetitive_phrase, repetitive_count = transcript_opening_repetition_artifact(
            transcript
        )

        if len(transcript.strip()) >= 120:
            if transcript_tokens >= 40 and transcript_unique_ratio >= 0.45:
                score += 22
                notes.append("Transcript captured with useful length")
            elif transcript_tokens >= 25 and transcript_unique_ratio >= 0.35:
                score += 16
                notes.append("Transcript captured with moderate detail")
            else:
                score += 10
                notes.append("Transcript captured but appears repetitive, so confidence boost was reduced")
        else:
            if transcript_tokens >= 20 and transcript_unique_ratio >= 0.40:
                score += 12
                notes.append("Transcript captured but short")
            else:
                score += 7
                notes.append("Short transcript captured with limited information density")

        if transcript_tokens >= 25 and transcript_unique_ratio < 0.35:
            score -= 3
            notes.append("Transcript token diversity is low, suggesting repetitive CTA-heavy audio")

        if has_repetitive_opening:
            score -= 4
            notes.append(
                f"Transcript starts with repeated phrase artifact ('{repetitive_phrase}' x{repetitive_count}), so transcript confidence was reduced"
            )

        if len(transcript_parts) >= 4:
            cta_dominant_transcript_parts = sum(
                1 for part in transcript_parts if is_cta_prompt_dominant_line(part)
            )
            cta_dominant_ratio = cta_dominant_transcript_parts / len(transcript_parts)
            if cta_dominant_transcript_parts >= 4 and cta_dominant_ratio >= 0.50:
                score -= 4
                notes.append(
                    "Transcript appears CTA-dominant across many lines, reducing confidence in substantive evidence depth"
                )
                if cta_dominant_ratio >= 0.75:
                    score -= 2
                    notes.append(
                        "Most transcript lines are CTA prompts, so transcript evidence is treated as low depth"
                    )
    elif transcript_error:
        notes.append(f"Transcript unavailable: {transcript_error}")

    combined_text = " ".join([caption] + claim_lines + signal_lines)
    if len(combined_text.strip()) < 80:
        score -= 10
        notes.append("Text payload is sparse, confidence reduced")

    payload_lines = [x.strip() for x in [caption, *claim_lines, *signal_lines, *comments] if x.strip()]
    if len(payload_lines) >= 3:
        normalized = [re.sub(r"\W+", " ", x.lower()).strip() for x in payload_lines]
        unique_ratio = len(set(normalized)) / len(normalized)
        if unique_ratio < 0.55:
            score -= 8
            notes.append("Captured text is repetitive across fields; confidence reduced")
        elif unique_ratio > 0.80:
            score += 4
            notes.append("Captured text has diverse non-duplicate signals")

    if looks_like_access_wall:
        score -= 25
        notes.append("Payload appears to be an Instagram access-wall or unavailable surface, not a reel payload")

    if looks_like_placeholder_payload:
        placeholder_penalty = 8 if transcript else 12
        score -= placeholder_penalty
        notes.append(
            "Payload is mostly instagram-domain placeholder text, so non-transcript extraction quality is limited"
        )

    score = max(0, min(100, score))
    return score, notes


def apply_confidence_penalty(base_score: int, confidence_score: int) -> tuple[int, int]:
    if confidence_score < 10:
        penalty = 35
    elif confidence_score < 20:
        penalty = 25
    elif confidence_score < 30:
        penalty = 18
    elif confidence_score < 45:
        penalty = 10
    elif confidence_score < 60:
        penalty = 5
    else:
        penalty = 0

    return min(100, base_score + penalty), penalty


def verdict_from_score(score: int) -> str:
    if score >= 70:
        return "High hype risk. Treat as funnel-first until proven otherwise."
    if score >= 45:
        return "Mixed. Core idea may be real, but pitch likely omits key difficulty/legal detail."
    return "Lower hype risk, but still validate economics and legal constraints before acting."


def research_notes_for_niche(niche: str) -> List[str]:
    if niche == "real-estate-wholesale":
        return [
            "Wholesaling is real but execution-heavy: lead gen, contracts, title, buyer list, and fast dispo.",
            "State law varies. Some states treat parts of wholesaling as brokerage activity without a license.",
            "Main failure points: no assignable contract, unclear title, buyer bypass, bad ARV/repair estimates, low buyer spread.",
            "If a reel skips legal structure and disposition process, assume missing complexity.",
        ]
    if niche == "ai-onlyfans":
        return [
            "AI creator models can produce revenue but policy risk is significant across platforms and processors.",
            "Deepfake/non-consensual likeness use creates major legal and ethical exposure.",
            "Content supply is highly saturated. Distribution, retention, and funnel ops matter more than generation alone.",
            "Treat this as a media business with compliance overhead, not passive income.",
        ]
    if niche == "ui-build":
        return [
            "Fast UI content can work, but quality moat is in distribution and repeatable productized outcomes.",
            "Component cloning is easy. Positioning and system-level implementation is where paid value exists.",
        ]
    return [
        "Most side-hustle reels compress effort and ignore failure modes. Verify channel economics before committing.",
    ]


def due_diligence_for_niche(niche: str) -> List[str]:
    common = [
        "Run a 30-day pilot with hard metrics: cost, conversion, close rate, and hours spent.",
        "Never prepay large mentorship/course fees before independent validation.",
        "Ask for one fully documented deal/case study with timestamps and constraints.",
    ]

    if niche == "real-estate-wholesale":
        return common + [
            "Have a real estate attorney validate contracts and assignment language for your state.",
            "Build buyer list first, then source deals. Avoid locking deals without clear exit liquidity.",
            "Model your minimum viable spread after taxes, marketing, and fallout rate.",
        ]
    if niche == "ai-onlyfans":
        return common + [
            "Define strict consent/likeness policy and ban deceptive impersonation.",
            "Verify ToS for each platform and payment rail before publishing at scale.",
            "Track churn and CAC weekly. If unit economics fail after 6-8 weeks, kill it.",
        ]
    return common


def recommended_action(niche: str, score: int) -> str:
    if niche == "real-estate-wholesale":
        if score >= 60:
            return "Do not buy courses yet. Shadow 1-2 local operators, lawyer-check contracts, and test a tiny lead batch first."
        return "Run a controlled 30-day wholesaling pilot with legal review and strict spread thresholds."
    if niche == "ai-onlyfans":
        if score >= 60:
            return "Treat as high-risk hype unless you can run compliant content ops with clear consent rules and real distribution edge."
        return "Test only with strict compliance guardrails and a small-budget funnel experiment."
    return "Treat as hypothesis, not truth. Pilot small, measure hard, and drop fast if metrics are weak."


def content_hooks(niche: str, verdict: str) -> List[str]:
    base = [
        "I tested this viral side-hustle claim for 7 days. Here is what actually happened.",
        "This reel sounds great, but here are the 3 things they never tell you.",
        "Before you buy a course, run this 5-point reality check.",
    ]
    if niche == "real-estate-wholesale":
        base.append("Wholesaling is not fake, but this is why most beginners get bypassed.")
    if niche == "ai-onlyfans":
        base.append("AI creator money is real for some, but compliance risk kills most setups.")
    base.append(f"Verdict: {verdict}")
    return base


def content_script_outline(niche: str) -> List[str]:
    outline = [
        "Hook: show the bold claim in one sentence.",
        "Reality: name the hidden constraint no one mentions.",
        "Proof: one quick calculation or process step.",
        "Action: give viewers a safe pilot checklist.",
        "CTA: ask for next reel to audit.",
    ]
    if niche == "ui-build":
        outline.insert(3, "Demo: before/after UI using component source and prompt scaffold.")
    return outline


def maybe_21st_pack(niche: str, text: str) -> Optional[List[str]]:
    t = text.lower()
    ui_signals = ["component", "website", "landing page", "vibecoding", "web app", "frontend", "tailwind", "shadcn", "21st.dev"]
    signal_hits = sum(1 for k in ui_signals if k in t)
    if niche != "ui-build" and signal_hits < 2:
        return None

    return [
        "21st.dev integration quickstart:",
        "1) Generate API key: https://21st.dev/magic/console",
        "2) Install MCP in Cursor: npx @21st-dev/cli@latest install --api-key <KEY>",
        "3) In agent prompt require: 'Use 21st.dev components only and cite slugs used.'",
        "4) Enforce quality gate: mobile-first, AA contrast, focus rings, 40px touch targets.",
        "Prompt scaffold:",
        "- Context: screen goal + audience",
        "- Constraints: Next.js, Tailwind, shadcn, TS only",
        "- Request: 3 variants then final implementation",
        "- Output: code + rationale + accessibility checks",
    ]


def build_result(url: str, try_audio_transcript: bool) -> ReelResult:
    md = fetch_reel_markdown(url)
    (
        title,
        source_url,
        caption,
        hashtags,
        comments,
        explicit_comment_candidates,
        comment_context_source,
        filtered_low_signal_comments,
        low_signal_pattern_counts,
        signal_lines,
        extracted,
    ) = parse_jina_markdown(md)

    low_signal_pattern_counter: Counter[str] = Counter(low_signal_pattern_counts)

    transcript = None
    transcript_method = None
    transcript_error = None

    cached_transcript, cached_method = load_cached_transcript(url)
    if cached_transcript:
        transcript = cached_transcript
        transcript_method = cached_method

    if try_audio_transcript and not transcript:
        transcript, transcript_method, transcript_error = try_transcript(url)

    transcript_cta_keywords = extract_comment_cta_keywords(
        caption,
        signal_lines=signal_lines,
        transcript=transcript,
    )
    if transcript and comments:
        transcript_refined_comments: List[str] = []
        for c in comments:
            if is_low_signal_comment(c, transcript_cta_keywords):
                filtered_low_signal_comments += 1
                low_signal_pattern_counter[
                    classify_low_signal_pattern(c, transcript_cta_keywords)
                ] += 1
                continue
            transcript_refined_comments.append(c)
        if len(transcript_refined_comments) != len(comments):
            comments = transcript_refined_comments
            if not comments:
                comment_context_source = "none"

    low_signal_pattern_counts = dict(low_signal_pattern_counter)
    low_signal_primary_pattern = dominant_low_signal_pattern(low_signal_pattern_counts)
    low_signal_primary_pattern_share = dominant_low_signal_pattern_share(low_signal_pattern_counts)

    claim_lines = extract_claim_lines(
        caption=caption,
        signal_lines=signal_lines,
        comments=comments,
        transcript=transcript,
    )
    substantive_claim_lines = sum(1 for line in claim_lines if is_substantive_claim_line(line))
    cta_only_claim_lines = len(claim_lines) - substantive_claim_lines

    text_for_analysis = "\n\n".join(
        [
            part
            for part in [
                title,
                caption,
                "\n".join(claim_lines),
                "\n".join(signal_lines),
                transcript or "",
                "\n".join(comments),
            ]
            if part
        ]
    )

    niche = detect_niche(text_for_analysis, hashtags)
    base_score, red_flags, green_flags = score_hype_risk(text_for_analysis, hashtags, comments)

    access_wall = looks_like_instagram_access_wall(title=title, caption=caption, signal_lines=signal_lines)
    placeholder_payload = looks_like_placeholder_payload(
        title=title,
        caption=caption,
        signal_lines=signal_lines,
    )

    confidence, confidence_notes = extraction_confidence(
        title=title,
        caption=caption,
        comments=comments,
        comment_context_source=comment_context_source,
        explicit_comment_candidates=explicit_comment_candidates,
        filtered_low_signal_comments=filtered_low_signal_comments,
        low_signal_primary_pattern=low_signal_primary_pattern,
        low_signal_primary_pattern_share=low_signal_primary_pattern_share,
        signal_lines=signal_lines,
        claim_lines=claim_lines,
        transcript=transcript,
        transcript_error=transcript_error,
        looks_like_access_wall=access_wall,
        looks_like_placeholder_payload=placeholder_payload,
    )

    kept_comment_count = len(comments)

    score, confidence_penalty = apply_confidence_penalty(base_score, confidence)
    if confidence_penalty:
        red_flags.append(
            f"Low extraction confidence ({confidence}/100). Added +{confidence_penalty} uncertainty buffer to hype score."
        )

    if confidence < 20 and score < 50:
        score = 50
        red_flags.append(
            "Extraction evidence is very thin; applied neutral-risk floor (50/100) to avoid false low-hype classification."
        )

    if access_wall:
        red_flags.append("Source appears to be Instagram access-wall/unavailable copy; reel extraction likely incomplete.")
    elif placeholder_payload:
        red_flags.append(
            "Source payload is mostly instagram-domain placeholder text; non-transcript extraction may be incomplete."
        )

    verdict = verdict_from_score(score)

    research_notes = research_notes_for_niche(niche)
    if access_wall:
        research_notes = [
            "The fetched page looks like an Instagram access-wall or unavailable surface, so content claims may be missing.",
            *research_notes,
        ]
    elif placeholder_payload:
        research_notes = [
            "The fetched page payload is mostly instagram-domain placeholder text, so transcript evidence should carry more weight than caption/body extraction.",
            *research_notes,
        ]
    if confidence < 45:
        research_notes = [
            "Extraction confidence is low. Treat this result as provisional and prefer manual transcript/comment capture.",
            *research_notes,
        ]
    due_diligence = due_diligence_for_niche(niche)
    action = recommended_action(niche, score)
    hooks = content_hooks(niche, verdict)
    outline = content_script_outline(niche)
    ui_pack = maybe_21st_pack(niche, text_for_analysis)

    return ReelResult(
        url=url,
        source_url=source_url,
        title=title,
        caption=caption,
        hashtags=hashtags,
        top_comments=comments,
        explicit_comment_candidates=explicit_comment_candidates,
        kept_comment_count=kept_comment_count,
        comment_context_source=comment_context_source,
        filtered_low_signal_comments=filtered_low_signal_comments,
        low_signal_primary_pattern=low_signal_primary_pattern,
        low_signal_primary_pattern_share=low_signal_primary_pattern_share,
        low_signal_pattern_counts=low_signal_pattern_counts,
        signal_lines=signal_lines,
        claim_lines=claim_lines,
        substantive_claim_lines=substantive_claim_lines,
        cta_only_claim_lines=cta_only_claim_lines,
        extracted_text=extracted,
        transcript=transcript,
        transcript_method=transcript_method,
        transcript_error=transcript_error,
        looks_like_access_wall=access_wall,
        looks_like_placeholder_payload=placeholder_payload,
        extraction_confidence=confidence,
        confidence_notes=confidence_notes,
        niche=niche,
        hype_risk_score=score,
        verdict=verdict,
        red_flags=red_flags,
        green_flags=green_flags,
        due_diligence=due_diligence,
        action=action,
        content_hooks=hooks,
        content_script_outline=outline,
        research_notes=research_notes,
        ui_prompt_pack=ui_pack,
    )


def to_markdown(result: ReelResult) -> str:
    lines = []
    lines.append(f"# Reel Intel Report")
    lines.append("")
    lines.append(f"- URL: {result.url}")
    lines.append(f"- Source: {result.source_url or 'n/a'}")
    lines.append(f"- Niche: {result.niche}")
    lines.append(f"- Extraction confidence: {result.extraction_confidence}/100")
    lines.append(f"- Access-wall payload detected: {'yes' if result.looks_like_access_wall else 'no'}")
    lines.append(f"- Placeholder payload detected: {'yes' if result.looks_like_placeholder_payload else 'no'}")
    lines.append(f"- Comment context source: {result.comment_context_source}")
    lines.append(f"- Explicit comment candidates: {result.explicit_comment_candidates}")
    lines.append(f"- Kept comments: {result.kept_comment_count}")
    lines.append(f"- Low-signal comments filtered: {result.filtered_low_signal_comments}")
    lines.append(f"- Substantive claim lines: {result.substantive_claim_lines}")
    lines.append(f"- CTA-only claim lines: {result.cta_only_claim_lines}")
    lines.append(f"- Primary low-signal pattern: {result.low_signal_primary_pattern}")
    lines.append(f"- Primary low-signal pattern share: {int(round(result.low_signal_primary_pattern_share * 100))}%")
    lines.append(f"- Hype risk score: {result.hype_risk_score}/100")
    lines.append(f"- Verdict: {result.verdict}")
    lines.append("")

    lines.append("## Extracted text")
    lines.append("")
    lines.append(textwrap.indent((result.transcript or result.extracted_text or result.caption or "n/a"), "> "))
    lines.append("")

    lines.append("## Extraction confidence details")
    lines.append("")
    for x in result.confidence_notes:
        lines.append(f"- {x}")
    lines.append("")

    if result.claim_lines:
        lines.append("## Claim lines used for analysis")
        lines.append("")
        for s in result.claim_lines[:10]:
            lines.append(f"- {s}")
        lines.append("")

    if result.signal_lines:
        lines.append("## Signal lines captured")
        lines.append("")
        for s in result.signal_lines[:10]:
            lines.append(f"- {s}")
        lines.append("")

    if result.transcript_method or result.transcript_error:
        lines.append("## Transcript status")
        lines.append("")
        lines.append(f"- Method: {result.transcript_method or 'none'}")
        lines.append(f"- Error: {result.transcript_error or 'none'}")
        lines.append("")

    lines.append("## Red flags")
    lines.append("")
    for x in result.red_flags or ["None detected"]:
        lines.append(f"- {x}")
    lines.append("")

    lines.append("## Green flags")
    lines.append("")
    for x in result.green_flags or ["None detected"]:
        lines.append(f"- {x}")
    lines.append("")

    lines.append("## Reality check notes")
    lines.append("")
    for x in result.research_notes:
        lines.append(f"- {x}")
    lines.append("")

    lines.append("## Due diligence before spending money")
    lines.append("")
    for x in result.due_diligence:
        lines.append(f"- {x}")
    lines.append("")

    lines.append("## Recommended action")
    lines.append("")
    lines.append(f"- {result.action}")
    lines.append("")

    lines.append("## Content creation hooks")
    lines.append("")
    for x in result.content_hooks:
        lines.append(f"- {x}")
    lines.append("")

    lines.append("## Content script outline")
    lines.append("")
    for x in result.content_script_outline:
        lines.append(f"- {x}")
    lines.append("")

    if result.ui_prompt_pack:
        lines.append("## 21st.dev UI prompt pack")
        lines.append("")
        for x in result.ui_prompt_pack:
            lines.append(f"- {x}")
        lines.append("")

    if result.top_comments:
        lines.append("## Top audience comments captured")
        lines.append("")
        for c in result.top_comments[:10]:
            lines.append(f"- {c}")
        lines.append("")

    if result.low_signal_pattern_counts:
        lines.append("## Low-signal pattern counts")
        lines.append("")
        for name, count in sorted(
            result.low_signal_pattern_counts.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"- {name}: {count}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def save_outputs(results: List[ReelResult], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    paths: List[Path] = []

    json_path = out_dir / f"reel-intel-{stamp}.json"
    json_path.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    paths.append(json_path)

    for i, r in enumerate(results, start=1):
        safe = re.sub(r"[^a-zA-Z0-9]+", "-", r.title.lower()).strip("-")[:50] or f"reel-{i}"
        md_path = out_dir / f"{stamp}-{i:02d}-{safe}.md"
        md_path.write_text(to_markdown(r), encoding="utf-8")
        paths.append(md_path)

    return paths


def print_human_summary(results: List[ReelResult]) -> None:
    for r in results:
        print("=" * 72)
        print(f"URL: {r.url}")
        print(f"Niche: {r.niche}")
        print(f"Extraction confidence: {r.extraction_confidence}/100")
        print(f"Access-wall payload: {'yes' if r.looks_like_access_wall else 'no'}")
        print(f"Placeholder payload: {'yes' if r.looks_like_placeholder_payload else 'no'}")
        print(f"Comment context source: {r.comment_context_source}")
        print(f"Explicit comment candidates: {r.explicit_comment_candidates}")
        print(f"Kept comments: {r.kept_comment_count}")
        print(f"Low-signal comments filtered: {r.filtered_low_signal_comments}")
        print(f"Substantive claim lines: {r.substantive_claim_lines}")
        print(f"CTA-only claim lines: {r.cta_only_claim_lines}")
        print(f"Primary low-signal pattern: {r.low_signal_primary_pattern}")
        print(f"Primary low-signal pattern share: {int(round(r.low_signal_primary_pattern_share * 100))}%")
        print(f"Score: {r.hype_risk_score}/100")
        print(f"Verdict: {r.verdict}")
        print(f"Action: {r.action}")
        if r.transcript_method or r.transcript_error:
            print(f"Transcript: {r.transcript_method or 'none'}")
            if r.transcript_error:
                print(f"Transcript error: {r.transcript_error}")
        print("Top red flags:")
        for rf in (r.red_flags[:3] or ["None"]):
            print(f"  - {rf}")
        print("Top hooks:")
        for h in r.content_hooks[:3]:
            print(f"  - {h}")
    print("=" * 72)


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Instagram reel side-hustle analyzer")
    p.add_argument("urls", nargs="+", help="Instagram reel URLs")
    p.add_argument("--try-transcript", action="store_true", help="Attempt audio transcript via yt-dlp + whisper/API")
    p.add_argument("--save-dir", default="", help="Directory to save JSON+markdown reports")
    p.add_argument("--json-only", action="store_true", help="Print JSON to stdout only")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    results: List[ReelResult] = []
    for url in args.urls:
        try:
            results.append(build_result(url, try_audio_transcript=args.try_transcript))
        except Exception as e:
            results.append(
                ReelResult(
                    url=url,
                    source_url="",
                    title="",
                    caption="",
                    hashtags=[],
                    top_comments=[],
                    explicit_comment_candidates=0,
                    kept_comment_count=0,
                    comment_context_source="none",
                    filtered_low_signal_comments=0,
                    low_signal_primary_pattern="none",
                    low_signal_primary_pattern_share=0.0,
                    low_signal_pattern_counts={},
                    signal_lines=[],
                    claim_lines=[],
                    substantive_claim_lines=0,
                    cta_only_claim_lines=0,
                    extracted_text="",
                    transcript=None,
                    transcript_method=None,
                    transcript_error=f"pipeline error: {e}",
                    looks_like_access_wall=False,
                    looks_like_placeholder_payload=False,
                    extraction_confidence=0,
                    confidence_notes=["Pipeline failed before extraction"],
                    niche="unknown",
                    hype_risk_score=100,
                    verdict="Failed to analyze. Treat as unverified.",
                    red_flags=[f"Analysis error: {e}"],
                    green_flags=[],
                    due_diligence=["Retry with a public reel URL or provide manual transcript."],
                    action="Do not act on this idea until re-analyzed.",
                    content_hooks=["I audited a reel that failed extraction. Here is how to verify claims anyway."],
                    content_script_outline=["Hook", "Problem", "Reality", "Checklist", "CTA"],
                    research_notes=["Extraction failed; no confidence in claim analysis."],
                    ui_prompt_pack=None,
                )
            )

    if args.save_dir:
        saved = save_outputs(results, Path(args.save_dir))
        print("Saved files:")
        for p in saved:
            print(f"- {p}")

    if args.json_only:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_human_summary(results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
