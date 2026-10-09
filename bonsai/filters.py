"""Content labels (PPV, RAW, HEVC, ...) and regions, read from channel and category names.

Like the resolution tiers these are guesses from provider labels. A channel
gets a label if its own name or its category's name carries it, so a channel
in "US| ENTERTAINMENT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ" is RAW and 50/60 fps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .models import Category, Channel
from .quality import DEFAULT_HIDDEN_TIERS, Tier, classify, normalise_name, parse_tiers, tier_from_name, words


class Flag(str, Enum):
    PPV = "PPV"
    RAW = "RAW"
    HEVC = "HEVC"
    HIGH_FPS = "FPS"
    LOOP_247 = "24/7"
    RADIO = "RADIO"
    ADULT = "ADULT"
    VARIANT = "VARIANT"
    NO_EPG = "NO_EPG"

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def short(self) -> str:
        return _SHORT[self]


_LABELS = {
    Flag.PPV: "PPV / events",
    Flag.RAW: "RAW",
    Flag.HEVC: "HEVC / H.265",
    Flag.HIGH_FPS: "50/60 fps",
    Flag.LOOP_247: "24/7",
    Flag.RADIO: "Radio",
    Flag.ADULT: "Adult",
    Flag.VARIANT: "VIP / Gold / backup",
    Flag.NO_EPG: "No TV guide",
}

# Shown in the Labels column; NO_EPG is left out there because it would be on most rows.
_SHORT = {
    Flag.PPV: "PPV",
    Flag.RAW: "RAW",
    Flag.HEVC: "HEVC",
    Flag.HIGH_FPS: "50/60",
    Flag.LOOP_247: "24/7",
    Flag.RADIO: "Radio",
    Flag.ADULT: "Adult",
    Flag.VARIANT: "VIP",
    Flag.NO_EPG: "",
}

_TOOLTIPS = {
    Flag.PPV: "Pay-per-view and live event slots: 'PPV' in the name or category, "
    "'Team vs Team' names, and 'NO EVENT STREAMING NOW' placeholders.",
    Flag.RAW: "Passed through from the source without re-encoding. Often 1080i or high bitrate, "
    "so harder for weak devices to play.",
    Flag.HEVC: "H.265 video. The PS Vita has no HEVC decoder.",
    Flag.HIGH_FPS: "Tagged 50 or 60 fps. Smoother, but more work to decode.",
    Flag.LOOP_247: "24/7 channels that loop shows or films.",
    Flag.RADIO: "Radio stations (audio only).",
    Flag.ADULT: "Flagged as adult by the provider.",
    Flag.VARIANT: "Channels labelled VIP, GOLD or (BK). Usually alternative or backup feeds "
    "of channels that also exist without the label.",
    Flag.NO_EPG: "Channels with no TV guide id. Untick to keep only channels with a guide.",
}


def flag_tooltip(flag: Flag) -> str:
    return _TOOLTIPS[flag]


# Hidden by default: things a Vita playlist almost never wants.
DEFAULT_HIDDEN_FLAGS = frozenset({Flag.PPV, Flag.HEVC, Flag.RADIO, Flag.ADULT})

# Single-word labels, matched against the lower-case words of a name (see quality.words).
_FLAG_OF_WORD: dict[str, Flag] = {
    "ppv": Flag.PPV,
    "raw": Flag.RAW,
    "hevc": Flag.HEVC, "h265": Flag.HEVC, "x265": Flag.HEVC,
    "50fps": Flag.HIGH_FPS, "60fps": Flag.HIGH_FPS,
    "radio": Flag.RADIO, "radyo": Flag.RADIO, "fm": Flag.RADIO,
    "xxx": Flag.ADULT, "adult": Flag.ADULT, "adults": Flag.ADULT,
    "vip": Flag.VARIANT, "gold": Flag.VARIANT,
}
# Two-word labels: "H.265", "50 fps".
_FLAG_OF_PAIR: dict[tuple[str, str], Flag] = {
    ("h", "265"): Flag.HEVC,
    ("50", "fps"): Flag.HIGH_FPS,
    ("60", "fps"): Flag.HIGH_FPS,
}
# Labels that depend on punctuation, checked only when a cheap substring test passes.
_LOOP_247 = re.compile(r"(?<!\d)24\s*[/-]\s*7(?!\d)")
_ADULT_18 = re.compile(r"(?<!\d)18\+")
_EVENT_VS = re.compile(r"\svs\.?\s")


def _flags_from(plain: str, ws: list[str]) -> set[Flag]:
    flags = set()
    prev = ""
    for w in ws:
        flag = _FLAG_OF_WORD.get(w) or _FLAG_OF_PAIR.get((prev, w))
        if flag is not None:
            flags.add(flag)
        prev = w
    lower = plain.lower()
    if "24" in lower and _LOOP_247.search(lower):
        flags.add(Flag.LOOP_247)
    if "18+" in lower and _ADULT_18.search(lower):
        flags.add(Flag.ADULT)
    if "(bk)" in lower:
        flags.add(Flag.VARIANT)
    return flags


def flags_from_name(name: str) -> set[Flag]:
    plain = normalise_name(name)
    return _flags_from(plain, words(plain))


def channel_flags(
    name: str,
    category_flags: frozenset[Flag] | set[Flag] = frozenset(),
    has_epg: bool = True,
    is_adult: bool = False,
    plain: str | None = None,
    ws: list[str] | None = None,
) -> frozenset[Flag]:
    if plain is None:
        plain = normalise_name(name)
    flags = _flags_from(plain, ws if ws is not None else words(plain)) | set(category_flags)
    lower = plain.lower()
    if ("vs" in lower and _EVENT_VS.search(lower)) or "no event streaming" in lower:
        flags.add(Flag.PPV)
    if is_adult:
        flags.add(Flag.ADULT)
    if not has_epg:
        flags.add(Flag.NO_EPG)
    return frozenset(flags)


OTHER_REGION = "Other"
_REGION = re.compile(r"\s*([A-Za-z0-9]{2,4})\s*[|:]")


def region_of(category_name: str) -> str:
    """The prefix providers put on category names ('US| NEWS', 'UK: SPORTS'), or 'Other'."""
    m = _REGION.match(normalise_name(category_name))
    return m.group(1).upper() if m else OTHER_REGION


def parse_flags(values) -> set[Flag]:
    out = set()
    for v in values or ():
        try:
            out.add(Flag(v))
        except ValueError:
            pass
    return out


# -- tagging and filter state shared by the window and the command line ------


@dataclass(frozen=True, slots=True)
class ChannelTags:
    tier: Tier
    tier_inherited: bool  # tier came from the category name
    flags: frozenset[Flag]
    region: str


def tag_channels(groups: list[tuple[Category, list[Channel]]]) -> dict[int, ChannelTags]:
    """Tier, labels and region for every channel, keyed by stream_id."""
    tags: dict[int, ChannelTags] = {}
    for cat, channels in groups:
        cat_tier = tier_from_name(cat.name)
        cat_flags = frozenset(flags_from_name(cat.name))
        region = region_of(cat.name)
        for ch in channels:
            plain = normalise_name(ch.name)
            ws = words(plain)
            tier, inherited = classify(ch.name, cat_tier, ws)
            flags = channel_flags(ch.name, cat_flags, bool(ch.epg_channel_id), ch.is_adult, plain, ws)
            tags[ch.stream_id] = ChannelTags(tier, inherited, flags, region)
    return tags


@dataclass(frozen=True)
class FilterState:
    hidden_tiers: frozenset[Tier] = DEFAULT_HIDDEN_TIERS
    hidden_flags: frozenset[Flag] = DEFAULT_HIDDEN_FLAGS
    hidden_regions: frozenset[str] = field(default_factory=frozenset)

    def allows(self, tags: ChannelTags | None) -> bool:
        return (
            tags is not None
            and tags.region not in self.hidden_regions
            and tags.tier not in self.hidden_tiers
            and not (tags.flags & self.hidden_flags)
        )

    def to_json(self) -> dict:
        return {
            "hidden_tiers": sorted(t.value for t in self.hidden_tiers),
            "hidden_flags": sorted(f.value for f in self.hidden_flags),
            "hidden_regions": sorted(self.hidden_regions),
        }

    @classmethod
    def from_json(cls, data: dict | None) -> FilterState:
        data = data or {}
        default = cls()
        return cls(
            hidden_tiers=frozenset(parse_tiers(data["hidden_tiers"])) if "hidden_tiers" in data else default.hidden_tiers,
            hidden_flags=frozenset(parse_flags(data["hidden_flags"])) if "hidden_flags" in data else default.hidden_flags,
            hidden_regions=frozenset(str(r) for r in data.get("hidden_regions") or ()),
        )
