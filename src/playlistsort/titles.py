"""Parse Spotify track titles into base title, mix name, remixers and featured artists.

Spotify writes mix information in several shapes:
  "Title - Artist Remix"            (dash form, the most common)
  "Title (Artist Remix)"            (parenthesised)
  "Title (feat. X) - Y Remix"       (featuring + remix)
  "Title - Extended Mix"            (no remixer: an alternate version by the original artist)
  "Title (Hot Creations)"           (a parenthetical that is NOT a mix name)
  "Love - Hate Thing"               (a dash that is NOT a mix separator)
The parser is deliberately conservative: a segment only counts as a mix descriptor when it
contains a recognised keyword, and everything else stays in the base title.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

MixType = Literal[
    "original",
    "extended",
    "radio",
    "remix",
    "edit",
    "dub",
    "rework",
    "bootleg",
    "instrumental",
    "vip",
    "other",
]


@dataclass(frozen=True)
class ParsedTitle:
    base_title: str
    mix_name: str | None
    mix_type: MixType | None
    remixers: tuple[str, ...]
    featuring: tuple[str, ...]
    is_remix: bool
    confidence: float


# keyword token -> mix type it implies
_KEYWORDS: dict[str, MixType] = {
    "remix": "remix",
    "remixes": "remix",
    "rmx": "remix",
    "remode": "remix",
    "refix": "remix",
    "re-fix": "remix",
    "rework": "rework",
    "re-work": "rework",
    "bootleg": "bootleg",
    "booty": "bootleg",
    "edit": "edit",
    "re-edit": "edit",
    "reedit": "edit",
    "flip": "edit",
    "dub": "dub",
    "instrumental": "instrumental",
    "vip": "vip",
    "radio": "radio",
    "extended": "extended",
    "original": "original",
    "mix": "other",
    "version": "other",
    "mixed": "other",
    "acapella": "other",
    "acappella": "other",
    "remaster": "other",
    "remastered": "other",
    "club": "other",
    "live": "other",
    "mastered": "other",
}

# words that qualify a version but never name a remixer
_QUALIFIERS = {
    "extended",
    "radio",
    "club",
    "original",
    "instrumental",
    "vip",
    "dub",
    "vocal",
    "short",
    "long",
    "full",
    "album",
    "single",
    "main",
    "alternative",
    "alt",
    "acoustic",
    "live",
    "version",
    "mix",
    "edit",
    "remaster",
    "remastered",
    "mixed",
    "acapella",
    "acappella",
    "stereo",
    "mono",
    "bonus",
    "deluxe",
    "intro",
    "outro",
    "inch",
    "the",
}

_TYPE_PRIORITY: list[MixType] = [
    "remix",
    "rework",
    "bootleg",
    "edit",
    "dub",
    "instrumental",
    "vip",
    "radio",
    "extended",
    "original",
    "other",
]
_REMIX_LIKE = {"remix", "rework", "bootleg"}
_WEAK_WITHOUT_NAME: set[MixType] = {"edit", "other"}

_DASH_SPLIT = re.compile(r"\s+[-‒–—]\s+")
_PAREN = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]")
_FEAT_INLINE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+(.+)$", re.IGNORECASE)
_FEAT_PAREN = re.compile(r"^(?:feat\.?|ft\.?|featuring|with)\s+(.+)$", re.IGNORECASE)
_NAME_SPLIT = re.compile(r"\s*(?:,|&|\+|\bx\b|\bvs\.?\b|\band\b)\s*", re.IGNORECASE)
_YEAR = re.compile(r"^\d{4}$")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class _Segment:
    text: str
    position: int
    mix_type: MixType
    remixers: tuple[str, ...]


def _norm(token: str) -> str:
    return token.lower().strip(".,;:!?\"'()[]")


def _has_keyword(text: str) -> bool:
    return any(_norm(t) in _KEYWORDS for t in text.split())


def _split_names(text: str) -> tuple[str, ...]:
    return tuple(n.strip() for n in _NAME_SPLIT.split(text) if n.strip())


def _analyse(text: str, position: int) -> _Segment:
    tokens = text.split()
    norm = [_norm(t) for t in tokens]
    first_kw = next(i for i, n in enumerate(norm) if n in _KEYWORDS)
    kinds = [_KEYWORDS[n] for n in norm if n in _KEYWORDS]

    name_tokens = tokens[:first_kw]
    while name_tokens and (
        _norm(name_tokens[-1]) in _QUALIFIERS or _YEAR.match(_norm(name_tokens[-1]))
    ):
        name_tokens.pop()
    remixers = _split_names(" ".join(name_tokens)) if name_tokens else ()

    if remixers:
        best = min(kinds, key=_TYPE_PRIORITY.index)
        if best == "other" or best in {"extended", "radio", "original", "vip"}:
            best = "remix"
    else:
        strong = [k for k in kinds if k not in _WEAK_WITHOUT_NAME]
        best = (
            min(strong, key=_TYPE_PRIORITY.index)
            if strong
            else min(kinds, key=_TYPE_PRIORITY.index)
        )
    return _Segment(text=text, position=position, mix_type=best, remixers=remixers)


def parse_title(title: str, artists: Sequence[str] = ()) -> ParsedTitle:
    """Split a Spotify title into its base title and mix descriptors.

    `artists` is accepted for future cross-checks (Spotify often lists the remixer as an
    artist); it does not change the parse today.
    """
    del artists  # reserved for remixer cross-checks
    original = title
    featuring: list[str] = []
    segments: list[_Segment] = []
    confidence = 1.0

    def _paren(match: re.Match[str]) -> str:
        nonlocal confidence
        inner = match.group(1).strip()
        feat = _FEAT_PAREN.match(inner)
        if feat:
            featuring.extend(_split_names(feat.group(1)))
            return ""
        if inner and _has_keyword(inner):
            segments.append(_analyse(inner, original.find(inner)))
            return ""
        confidence = min(confidence, 0.7)
        return match.group(0)

    remaining = _PAREN.sub(_paren, title)

    parts = [p.strip() for p in _DASH_SPLIT.split(remaining) if p.strip()]
    base_parts: list[str] = parts[:1]
    in_mix = False
    for part in parts[1:]:
        if in_mix or _has_keyword(part):
            in_mix = True
            segments.append(_analyse(part, original.find(part)))
        else:
            base_parts.append(part)
            if len(parts) > 1:
                confidence = min(confidence, 0.9)

    base = " - ".join(base_parts)
    feat_inline = _FEAT_INLINE.search(base)
    if feat_inline:
        featuring.extend(_split_names(feat_inline.group(1)))
        base = base[: feat_inline.start()]
    base = _WS.sub(" ", base).strip()

    if not segments:
        return ParsedTitle(base, None, None, (), tuple(featuring), False, confidence)

    segments.sort(key=lambda s: s.position)
    remixers: list[str] = []
    for seg in segments:
        for name in seg.remixers:
            if name not in remixers:
                remixers.append(name)
    named = [s for s in segments if s.remixers]
    primary = min((s.mix_type for s in (named or segments)), key=_TYPE_PRIORITY.index)
    is_remix = bool(remixers) or primary in _REMIX_LIKE
    if is_remix and not remixers:
        confidence = min(confidence, 0.8)

    return ParsedTitle(
        base_title=base,
        mix_name=" - ".join(s.text for s in segments),
        mix_type=primary,
        remixers=tuple(remixers),
        featuring=tuple(featuring),
        is_remix=is_remix,
        confidence=confidence,
    )
