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
    assert DEFAULT_HIDDEN_FLAGS == {Flag.PPV, Flag.HEVC, Flag.RADIO, Flag.ADULT, Flag.INTERLACED, Flag.FORMAT}
    assert parse_flags(["PPV", "24/7", "bogus"]) == {Flag.PPV, Flag.LOOP_247}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Ahlulbayt TV (1080p) [Not 24/7]", {Flag.PART_TIME}),  # not the 24/7 loop label
        ("Akaal Channel (396p) [Geo-blocked]", {Flag.GEO}),
        ("News (576i)", {Flag.INTERLACED}),
        ("Sport (1080i) [Geo-blocked] [Not 24/7]", {Flag.INTERLACED, Flag.GEO, Flag.PART_TIME}),
        ("Classic Movies 24/7", {Flag.LOOP_247}),
    ],
)
def test_iptv_org_labels(name, expected):
    assert flags_from_name(name) == expected


@pytest.mark.parametrize(
    "url, unsupported",
    [
        ("https://example.com/live/playlist.m3u8", False),
        ("http://example.com/live/1.ts", False),
        ("https://example.com/manifest.mpd", True),
        ("https://example.com/manifest.MPD?token=1", True),
        ("rtmp://example.com/live/stream", True),
        ("mmsh://example.com/stream", True),
        ("srt://example.com:9000", True),
    ],
)
def test_unsupported_format_flag(url, unsupported):
    assert (Flag.FORMAT in channel_flags("X", url=url)) is unsupported


def test_region_from_tvg_id():
    from bonsai.filters import region_from_epg_id, tag_channels
    from bonsai.models import Category, Channel

    assert region_from_epg_id("AfghanistanInternational.uk@SD") == "UK"
    assert region_from_epg_id("BBCOne.uk") == "UK"
    assert region_from_epg_id("nothing") == ""
    tags = tag_channels([
        (Category("News", "News"), [Channel(1, "A", "News", epg_channel_id="A.fr@SD"), Channel(2, "B", "News")]),
        (Category("1", "US| NEWS"), [Channel(3, "C", "1", epg_channel_id="C.uk")]),  # prefix wins
    ])
    assert [tags[i].region for i in (1, 2, 3)] == ["FR", OTHER_REGION, "US"]
