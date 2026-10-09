from bonsai.m3u import PlaylistEntry, build_entries, render_m3u, write_m3u
from bonsai.models import UNCATEGORISED, Category, Channel, group_channels


def entry(name, **kw):
    return PlaylistEntry(name=name, url=kw.pop("url", "http://s/live/u/p/1.ts"), **kw)


def test_basic_render():
    text = render_m3u([entry("BBC One", group="UK", tvg_id="bbc1.uk", logo="http://x/l.png")])
    assert text == (
        "#EXTM3U\n"
        '#EXTINF:-1 tvg-id="bbc1.uk" tvg-name="BBC One" tvg-logo="http://x/l.png" '
        'group-title="UK",BBC One\n'
        "http://s/live/u/p/1.ts\n"
    )


def test_epg_header():
    text = render_m3u([], epg_url="http://s/xmltv.php?username=u&password=p")
    assert text == '#EXTM3U x-tvg-url="http://s/xmltv.php?username=u&password=p"\n'


def test_quotes_in_names_do_not_break_attributes():
    text = render_m3u([entry('The "Best" Channel', group='Say "hi"')])
    line = text.splitlines()[1]
    assert 'tvg-name="The \'Best\' Channel"' in line
    assert 'group-title="Say \'hi\'"' in line
    # Every attribute's quotes still balance
    attrs = line.split(",", 1)[0] if '",' not in line else line[: line.rindex('",') + 1]
    assert attrs.count('"') % 2 == 0
    assert line.endswith(',The "Best" Channel')


def test_commas_in_names_kept():
    line = render_m3u([entry("News, Weather & Sport", group="UK, General")]).splitlines()[1]
    assert 'tvg-name="News, Weather & Sport"' in line
    assert 'group-title="UK, General"' in line
    assert line.endswith('",News, Weather & Sport')


def test_newlines_in_names_flattened():
    text = render_m3u([entry("Line\none\r\ntwo\tthree")])
    assert len(text.splitlines()) == 3
    assert "Line one  two three" in text


def test_non_latin_names_and_utf8_file(tmp_path):
    names = ["ТВ Центр", "قناة الجزيرة", "日本テレビ", "Ελληνικά", "Ça va – télé 🎬"]
    path = write_m3u(tmp_path / "out.m3u", [entry(n, group="Ünïcode") for n in names])
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "no BOM"
    assert b"\r\n" not in raw, "LF line endings"
    decoded = raw.decode("utf-8")
    for n in names:
        assert f",{n}\n" in decoded


def test_crlf_option(tmp_path):
    path = write_m3u(tmp_path / "out.m3u", [entry("A")], newline="\r\n")
    assert path.read_bytes().count(b"\r\n") == 3


def test_write_replaces_atomically(tmp_path):
    target = tmp_path / "out.m3u"
    target.write_text("old")
    write_m3u(target, [entry("A")])
    assert target.read_text().startswith("#EXTM3U")
    assert [p.name for p in tmp_path.iterdir()] == ["out.m3u"]


def test_build_entries_keeps_provider_order():
    cats = [Category("2", "Second"), Category("1", "First")]
    chans = [
        Channel(30, "C", "1"),
        Channel(10, "A", "2"),
        Channel(20, "B", "2"),
        Channel(40, "D", "99"),
    ]
    groups = group_channels(cats, chans)
    assert [c for c, _ in groups] == [cats[0], cats[1], UNCATEGORISED]
    entries = build_entries(groups, {40, 30, 20, 10}, lambda ch: f"u/{ch.stream_id}")
    assert [(e.group, e.name, e.url) for e in entries] == [
        ("Second", "A", "u/10"),
        ("Second", "B", "u/20"),
        ("First", "C", "u/30"),
        ("Uncategorised", "D", "u/40"),
    ]


def test_build_entries_only_selected():
    groups = [(Category("1", "X"), [Channel(1, "a", "1"), Channel(2, "b", "1")])]
    assert [e.name for e in build_entries(groups, {2}, str)] == ["b"]


def test_divider_rows_dropped():
    cats = [Category("1", "Sports")]
    chans = [
        Channel(1, "####### SOCCER PPV #######", "1"),
        Channel(2, "  ### 4K UHD ###", "1"),
        Channel(3, "Sky Sports #1", "1"),
        Channel(4, "No. 1 ## Hits", "1"),
    ]
    [(_, kept)] = group_channels(cats, chans)
    assert [c.stream_id for c in kept] == [3, 4]
