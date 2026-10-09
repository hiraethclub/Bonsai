"""Content labels (PPV, RAW, HEVC, ...) and regions, read from channel and category names.

Like the resolution tiers these are guesses from provider labels. A channel
gets a label if its own name or its category's name carries it, so a channel
in "US| ENTERTAINMENT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ" is RAW and 50/60 fps.
"""

from __future__ import annotations

import re
from enum import Enum

from .quality import normalise_name


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

# Same token boundary as the resolution tags: anything that is not a letter or digit.
_S = r"(?<![^\W_])"
_E = r"(?![^\W_])"


def _word(alternatives: str) -> re.Pattern:
    return re.compile(f"{_S}(?:{alternatives}){_E}", re.IGNORECASE)


# Patterns checked against both the channel name and its category name.
_NAME_PATTERNS: list[tuple[Flag, re.Pattern]] = [
    (Flag.PPV, _word(r"ppv")),
    (Flag.RAW, _word(r"raw")),
    (Flag.HEVC, _word(r"hevc|h\.?265|x265")),
    (Flag.HIGH_FPS, re.compile(r"(?<!\d)(?:50|60)\s*fps", re.IGNORECASE)),
    (Flag.LOOP_247, re.compile(r"(?<!\d)24\s*[/-]\s*7(?!\d)")),
    (Flag.RADIO, _word(r"radio|radyo|fm")),
    (Flag.ADULT, _word(r"xxx|adults?|18\+")),
    (Flag.VARIANT, re.compile(f"{_S}(?:vip|gold){_E}|\\(bk\\)", re.IGNORECASE)),
]

# Patterns that only make sense on channel names (event slots).
_EVENT_PATTERNS = [
    re.compile(r"\svs\.?\s", re.IGNORECASE),
    re.compile(r"no event streaming", re.IGNORECASE),
]


def flags_from_name(name: str) -> set[Flag]:
    name = normalise_name(name)
    return {flag for flag, pattern in _NAME_PATTERNS if pattern.search(name)}


def channel_flags(
    name: str,
    category_flags: frozenset[Flag] | set[Flag] = frozenset(),
    has_epg: bool = True,
    is_adult: bool = False,
) -> frozenset[Flag]:
    flags = flags_from_name(name) | set(category_flags)
    plain = normalise_name(name)
    if any(p.search(plain) for p in _EVENT_PATTERNS):
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
