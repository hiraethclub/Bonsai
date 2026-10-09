"""Guess a channel's resolution tier from quality tags in its name.

The Xtream API has no resolution field, so this reads labels like "FHD" or
"720p". Tags must be whole tokens: "HD" does not match inside "FHD", "UHD"
or "HDTV". Tiers are checked most specific first, so "BBC One HD 1080" is FHD.
These are provider labels, not measurements: some "HD" channels are 1080i.

Some providers write tags in Unicode superscript ("ᵁᴴᴰ ³⁸⁴⁰ᴾ", "ᴴᴰ/ᴿᴬᵂ"), so
names are normalised to plain text before matching.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from functools import lru_cache


class Tier(str, Enum):
    UHD = "UHD"
    FHD = "FHD"
    HD = "HD"
    SD = "SD"
    UNTAGGED = "Untagged"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    Tier.UHD: "4K / UHD",
    Tier.FHD: "FHD / 1080",
    Tier.HD: "HD / 720",
    Tier.SD: "SD",
    Tier.UNTAGGED: "Untagged",
}

# Hidden by default: anything above 720p.
DEFAULT_HIDDEN_TIERS = frozenset({Tier.UHD, Tier.FHD})

# A token boundary is anything that is not a letter or digit (spaces, |, :, (, [, -, _, + ...).
_START = r"(?<![^\W_])"
_END = r"(?![^\W_])"

_TAGS: list[tuple[Tier, list[str]]] = [
    (Tier.UHD, [r"4k", r"uhd", r"2160p", r"3840p", r"3840"]),
    (Tier.FHD, [r"fhd", r"full[\s_-]*hd", r"1080p", r"1080i", r"1080"]),
    (Tier.HD, [r"hd", r"720p", r"720"]),
    (Tier.SD, [r"sd", r"576p", r"576i", r"480p"]),
]

_PATTERNS = [
    (tier, re.compile(_START + "(?:" + "|".join(tags) + ")" + _END, re.IGNORECASE))
    for tier, tags in _TAGS
]


@lru_cache(maxsize=4096)
def _is_raised(ch: str) -> bool:
    """Superscript, subscript or small-form character, e.g. ᴴ, ³, ᶠ, ⁽."""
    return unicodedata.decomposition(ch).startswith(("<super>", "<sub>", "<small>"))


def normalise_name(name: str) -> str:
    """Plain-text form of a name: superscripts become normal letters and digits.

    A space is inserted wherever raised text meets normal text, so a tag glued
    onto a word ("RELAXᵁᴴᴰ") still reads as a separate token ("RELAX UHD").
    """
    if name.isascii():
        return name
    out = []
    prev_raised = None
    for ch in name:
        raised = _is_raised(ch)
        if prev_raised is not None and raised != prev_raised and ch.isalnum() and out and out[-1].isalnum():
            out.append(" ")
        out.append(ch)
        prev_raised = raised
    return unicodedata.normalize("NFKC", "".join(out))


def tier_from_name(name: str) -> Tier:
    name = normalise_name(name)
    for tier, pattern in _PATTERNS:
        if pattern.search(name):
            return tier
    return Tier.UNTAGGED


def classify(channel_name: str, category_tier: Tier = Tier.UNTAGGED) -> tuple[Tier, bool]:
    """Tier for a channel, falling back to its category's tag. Returns (tier, inherited)."""
    tier = tier_from_name(channel_name)
    if tier is Tier.UNTAGGED and category_tier is not Tier.UNTAGGED:
        return category_tier, True
    return tier, False


def parse_tiers(values) -> set[Tier]:
    """Tiers from saved strings, ignoring anything unknown."""
    out = set()
    for v in values or ():
        try:
            out.add(Tier(v))
        except ValueError:
            pass
    return out
