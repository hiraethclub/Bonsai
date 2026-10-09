import pytest

from bonsai.filters import (
    DEFAULT_HIDDEN_FLAGS,
    OTHER_REGION,
    Flag,
    channel_flags,
    flags_from_name,
    parse_flags,
    region_of,
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("US| NBA PPV ᴴᴰ", {Flag.PPV}),
        ("US| AT&T ᴿᴬᵂ ⁶⁰ᶠᵖˢ", {Flag.RAW, Flag.HIGH_FPS}),
        ("US| ENTERTAINMENT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ", {Flag.RAW, Flag.HIGH_FPS}),
        ("DE: SKY SPORT RAW 50fps", {Flag.RAW, Flag.HIGH_FPS}),
        ("DE: SKY SPORT RAW 50 fps", {Flag.RAW, Flag.HIGH_FPS}),
        ("ES: LALIGA ʰᵉᵛᶜ", {Flag.HEVC}),
        ("UK: BBC ONE H.265", {Flag.HEVC}),
        ("UK: BBC ONE x265", {Flag.HEVC}),
        ("US| 24/7 COMEDY", {Flag.LOOP_247}),
        ("US| 24-7 COMEDY", {Flag.LOOP_247}),
        ("TR: RADYO VESAIRE", {Flag.RADIO}),
        ("UK: CAPITAL FM", {Flag.RADIO}),
        ("FOR ADULTS", {Flag.ADULT}),
        ("XXX: SOMETHING", {Flag.ADULT}),
        ("US| SPORTS ᵛᴵᴾ", {Flag.VARIANT}),
        ("UK: SKY SPORTS ᴳᴼᴸᴰ", {Flag.VARIANT}),
        ("BE| DAZN PPV ⁽ᴮᴷ⁾", {Flag.PPV, Flag.VARIANT}),
        # things that must not match
        ("UK: BBC ONE", set()),
        ("US| DRAWN TOGETHER", set()),  # raw inside a word
        ("US| PPVISION", set()),
        ("UK: GOLDEN OLDIES", set()),
        ("US| 247 NEWS", set()),
        ("US| 124/78 CHANNEL", set()),
        ("UK: FMTV", set()),
        ("UK: 150fps", set()),
        ("RADIOHEAD LIVE", set()),
    ],
)
def test_flags_from_name(name, expected):
    assert flags_from_name(name) == expected


def test_event_slots_are_ppv():
    assert Flag.PPV in channel_flags("End | Shelton vs. Altmaier | 8K EXCLUSIVE | US: SOCCER PPV 3")
    assert Flag.PPV in channel_flags("Tennis: Anna Pushkareva vs Shristi Selvan @ Sep 6 19:30 PM")
    assert Flag.PPV in channel_flags("NO EVENT STREAMING NOW - | NL: DAZN 56")
    assert Flag.PPV not in channel_flags("UK: VERSUS TV")


def test_channel_inherits_category_flags_and_api_fields():
    flags = channel_flags("CNN", frozenset({Flag.RAW}), has_epg=False, is_adult=True)
    assert flags == {Flag.RAW, Flag.NO_EPG, Flag.ADULT}
    assert channel_flags("CNN", has_epg=True) == frozenset()


@pytest.mark.parametrize(
    "category, region",
    [
        ("US| AT&T ᴿᴬᵂ ⁶⁰ᶠᵖˢ", "US"),
        ("UK: SPORTS", "UK"),
        ("ASIA| CHINA", "ASIA"),
        (" de : NEWS", "DE"),
        ("4K| RELAX ᵁᴴᴰ", "4K"),
        ("FOR ADULTS", OTHER_REGION),
        ("SPORTS | EXTRA", OTHER_REGION),  # prefix longer than 4 characters
        ("", OTHER_REGION),
    ],
)
def test_region_of(category, region):
    assert region_of(category) == region


def test_defaults_and_parse():
    assert DEFAULT_HIDDEN_FLAGS == {Flag.PPV, Flag.HEVC, Flag.RADIO, Flag.ADULT}
    assert parse_flags(["PPV", "24/7", "bogus"]) == {Flag.PPV, Flag.LOOP_247}
