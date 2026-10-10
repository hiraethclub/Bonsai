import pytest

from bonsai.m3u import build_entries, render_m3u
from bonsai.models import group_channels
from bonsai.playlist import (
    UNGROUPED,
    PlaylistError,
    load_playlist,
    parse_m3u,
    stream_id_for,
    to_channels,
)
from bonsai.sources import PLAYLIST, Source, playlist_epg_url

SAMPLE = """﻿#EXTM3U x-tvg-url="https://example.com/guide.xml.gz"\r
#EXTINF:-1 tvg-id="00sReplay.us@SD" tvg-logo="https://e.com/a.png" group-title="Movies",00s Replay\r
https://jmp2.uk/plu-62ba.m3u8\r
#EXTINF:-1 tvg-id="1Plus1.ua@SD" tvg-logo="https://e.com/b.png" http-user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)" group-title="General",1+1 International\r
#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r
http://158.101.222.193:88/georgia_play.php?id=1plus1\r
#EXTINF:-1 tvg-id="3ABNKids.us@SD" group-title="Animation;Kids",3ABN Kids, the channel (720p) [Not 24/7]\r
https://3abn.example.com/kids/playlist.m3u8\r
# a plain comment that must be ignored
#EXTINF:-1,No Group At All
http://example.com/nogroup.ts
#EXTINF:-1 tvg-id="X.uk" http-referrer="https://site.example/" group-title="News",Refer TV
#EXTVLCOPT:http-referrer=https://site.example/
#KODIPROP:inputstream=inputstream.adaptive
https://cdn.example.com/x.m3u8
#EXTINF:-1,Grouped by EXTGRP
#EXTGRP:Music
https://example.com/music.m3u8
https://example.com/bare-url-without-extinf.m3u8
"""


def test_parse_header_entries_and_options():
    p = parse_m3u(SAMPLE)
    assert p.header == {"x-tvg-url": "https://example.com/guide.xml.gz"}
    assert [e.name for e in p.entries] == [
        "00s Replay",
        "1+1 International",
        "3ABN Kids, the channel (720p) [Not 24/7]",
        "No Group At All",
        "Refer TV",
        "Grouped by EXTGRP",
        "bare-url-without-extinf.m3u8",
    ]
    ua = p.entries[1]
    assert ua.attrs["http-user-agent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"  # comma-free but spaces
    assert ua.options == ["#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)"]
    assert p.entries[4].options == ["#EXTVLCOPT:http-referrer=https://site.example/",
                                    "#KODIPROP:inputstream=inputstream.adaptive"]
    assert p.entries[5].attrs["group-title"] == "Music"
    assert all(not e.url.endswith("\r") for e in p.entries)


def test_comma_inside_quoted_attribute():
    p = parse_m3u('#EXTM3U\n#EXTINF:-1 tvg-name="A, B" group-title="X, Y",Title, with comma\nhttp://e.com/1.ts\n')
    e = p.entries[0]
    assert e.attrs == {"tvg-name": "A, B", "group-title": "X, Y"}
    assert e.name == "Title, with comma"


def test_to_channels_groups_multi_and_ungrouped():
    cats, chans = to_channels(parse_m3u(SAMPLE))
    assert [c.name for c in cats] == ["Movies", "General", "Animation", "Kids", UNGROUPED, "News", "Music"]
    kids = [c for c in chans if c.name.startswith("3ABN")]
    assert [c.category_id for c in kids] == ["Animation", "Kids"]
    assert kids[0].stream_id != kids[1].stream_id
    assert kids[0].url == kids[1].url
    ua = next(c for c in chans if c.name == "1+1 International")
    assert ua.attrs == (("http-user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),)
    assert ua.epg_channel_id == "1Plus1.ua@SD"


def test_ids_are_stable_and_positive():
    a = stream_id_for("https://e.com/1.m3u8", "News")
    assert a == stream_id_for("https://e.com/1.m3u8", "News")
    assert a != stream_id_for("https://e.com/1.m3u8", "Kids")
    assert 0 < a < 2**62


def test_exact_duplicate_lines_collapse():
    text = "#EXTM3U\n#EXTINF:-1 group-title=\"A\",X\nhttp://e/1\n#EXTINF:-1 group-title=\"A\",X\nhttp://e/1\n"
    _, chans = to_channels(parse_m3u(text))
    assert len(chans) == 1


def test_round_trip_keeps_options_and_writes_each_url_once():
    cats, chans = to_channels(parse_m3u(SAMPLE))
    groups = group_channels(cats, chans)
    everything = {c.stream_id for c in chans}
    entries = build_entries(groups, everything, lambda ch: ch.url)
    assert len(entries) == 7  # 8 channels, the Animation/Kids stream written once
    text = render_m3u(entries, "https://example.com/guide.xml.gz")
    again = parse_m3u(text)
    assert again.header["x-tvg-url"] == "https://example.com/guide.xml.gz"
    by_url = {e.url: e for e in again.entries}
    ua = by_url["http://158.101.222.193:88/georgia_play.php?id=1plus1"]
    assert ua.attrs["http-user-agent"].startswith("Mozilla/5.0")
    assert ua.options == ["#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)"]
    assert by_url["https://3abn.example.com/kids/playlist.m3u8"].attrs["group-title"] == "Animation"


def test_comment_lines_written_and_ignored_by_parser():
    cats, chans = to_channels(parse_m3u(SAMPLE))
    groups = group_channels(cats, chans)
    entries = build_entries(groups, {chans[0].stream_id}, lambda ch: ch.url, lambda ch: "Not working: HTTP 404")
    text = render_m3u(entries)
    assert "# Not working: HTTP 404\n#EXTINF" in text
    assert len(parse_m3u(text).entries) == 1


def test_load_playlist_from_file(tmp_path):
    f = tmp_path / "list.m3u"
    f.write_text(SAMPLE, encoding="utf-8")
    cats, chans, header = load_playlist(str(f))
    assert len(chans) == 8 and header["x-tvg-url"]


@pytest.mark.parametrize("content, message", [
    ("<!DOCTYPE html><html><body>Not found</body></html>", "web page"),
    ("#EXTM3U\n", "No streams"),
])
def test_load_playlist_rejects_non_playlists(tmp_path, content, message):
    f = tmp_path / "bad.m3u"
    f.write_text(content)
    with pytest.raises(PlaylistError, match=message):
        load_playlist(str(f))


def test_missing_file():
    with pytest.raises(PlaylistError):
        load_playlist("/no/such/file.m3u")


def test_source_json_and_epg():
    s = Source(PLAYLIST, "https://iptv-org.github.io/iptv/index.m3u")
    assert Source.from_json(s.to_json()) == s
    assert s.is_remote and s.label() == "index.m3u (iptv-org.github.io)"
    assert Source.from_json({"kind": "m3u", "location": "  "}) == Source()
    assert Source.from_json(None) == Source()
    assert not Source(PLAYLIST, "/home/a/list.m3u").is_remote
    assert playlist_epg_url({"url-tvg": "g"}) == "g"
