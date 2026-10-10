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

# Tags are whole words: a "word" is a run of letters and digits, so "|UK| X (FHD)"
# splits into uk, x, fhd and "HD" can never match inside "FHD", "UHD" or "HDTV".
_WORD = re.compile(r"[^\W_]+")

_TAGS: list[tuple[Tier, set[str]]] = [
    (Tier.UHD, {"4k", "uhd", "2160p", "3840p", "3840"}),
    (Tier.FHD, {"fhd", "fullhd", "1080p", "1080i", "1080"}),
    (Tier.HD, {"hd", "720p", "720"}),
    (Tier.SD, {"sd", "576p", "576i", "480p"}),
]
_TIER_OF_WORD = {word: tier for tier, words in _TAGS for word in words}
# Any other "<height>p" or "<height>i", as in iptv-org names: "Channel (540p)", "(396p)".
_HEIGHT_WORD = re.compile(r"^(\d{3,4})[pi]$")


def _tier_for_height(height: int) -> Tier:
    if height >= 2000:
        return Tier.UHD
    if height > 720:
        return Tier.FHD
    if height > 576:
        return Tier.HD
    return Tier.SD
_RANK = {tier: i for i, (tier, _) in enumerate(_TAGS)}  # lower is more specific


def words(plain: str) -> list[str]:
    """Lower-case words of an already normalised name."""
    return _WORD.findall(plain.lower())


# Small capitals have no Unicode decomposition (NFKC leaves "ʜᴅ" alone), so map them by hand.
_SMALL_CAPS = str.maketrans({
    "ᴀ": "A", "ʙ": "B", "ᴄ": "C", "ᴅ": "D", "ᴇ": "E", "ꜰ": "F", "ɢ": "G", "ʜ": "H", "ɪ": "I",
    "ᴊ": "J", "ᴋ": "K", "ʟ": "L", "ᴍ": "M", "ɴ": "N", "ᴏ": "O", "ᴘ": "P", "ꞯ": "Q", "ʀ": "R",
    "ꜱ": "S", "ᴛ": "T", "ᴜ": "U", "ᴠ": "V", "ᴡ": "W", "ʏ": "Y", "ᴢ": "Z",
})
_SMALL_CAP_CHARS = frozenset(chr(c) for c in _SMALL_CAPS)


@lru_cache(maxsize=4096)
def _is_raised(ch: str) -> bool:
    """Superscript, subscript, small-form or small-capital character, e.g. ᴴ, ³, ᶠ, ⁽, ʜ."""
    return ch in _SMALL_CAP_CHARS or unicodedata.decomposition(ch).startswith(("<super>", "<sub>", "<small>"))


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
    return unicodedata.normalize("NFKC", "".join(out).translate(_SMALL_CAPS))


def tier_from_words(ws: list[str]) -> Tier:
    best = Tier.UNTAGGED
    best_rank = len(_TAGS)
    prev = ""
    for w in ws:
        tier = _TIER_OF_WORD.get(w)
        if tier is None and w[-1] in "pi" and (m := _HEIGHT_WORD.match(w)):
            tier = _tier_for_height(int(m.group(1)))
        if tier is Tier.HD and prev == "full":  # "Full HD", "Full-HD"
            tier = Tier.FHD
        if tier is not None and _RANK[tier] < best_rank:
            best, best_rank = tier, _RANK[tier]
        prev = w
    return best


def tier_from_plain(plain: str) -> Tier:
    """Tier from a name that has already been through normalise_name()."""
    return tier_from_words(words(plain))


def tier_from_name(name: str) -> Tier:
    return tier_from_plain(normalise_name(name))


def classify(
    channel_name: str, category_tier: Tier = Tier.UNTAGGED, ws: list[str] | None = None
) -> tuple[Tier, bool]:
    """Tier for a channel, falling back to its category's tag. Returns (tier, inherited)."""
    tier = tier_from_words(ws if ws is not None else words(normalise_name(channel_name)))
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
