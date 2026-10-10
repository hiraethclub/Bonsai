import gzip
import os
import stat
from datetime import datetime, timezone

import pytest

from bonsai.cache import cache_path, load_lists, playlist_key, save_lists, xtream_key
from bonsai.models import Category, Channel


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("BONSAI_CACHE_DIR", str(tmp_path / "cache"))


CATS = [Category("1", "US| NEWS ᴴᴰ"), Category("2", "UK: SPORT")]
CHANS = [
    Channel(1, "CNN ᴴᴰ", "1", num=1, icon="http://x/cnn.png", epg_channel_id="cnn.us",
            added=datetime(2024, 1, 2, tzinfo=timezone.utc), is_adult=False),
    Channel(2, "Late", "2", is_adult=True),
]


def test_round_trip():
    saved = save_lists(xtream_key("http://s:80", "alice"), CATS, CHANS)
    loaded = load_lists(xtream_key("http://s:80", "alice"))
    assert loaded.categories == CATS
    assert loaded.channels == CHANS
    assert loaded.fetched_at == saved.fetched_at


def test_per_account_and_no_names_in_file_name():
    save_lists(xtream_key("http://s:80", "alice"), CATS, CHANS)
    assert load_lists(xtream_key("http://s:80", "bob")) is None
    assert load_lists(xtream_key("http://other:80", "alice")) is None
    name = cache_path(xtream_key("http://s:80", "alice")).name
    assert "alice" not in name and "s:80" not in name


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_cache_file_private():
    save_lists(xtream_key("http://s:80", "alice"), CATS, CHANS)
    assert stat.S_IMODE(cache_path(xtream_key("http://s:80", "alice")).stat().st_mode) == 0o600


def test_corrupt_or_old_cache_ignored():
    path = cache_path(xtream_key("http://s:80", "alice"))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not gzip")
    assert load_lists(xtream_key("http://s:80", "alice")) is None
    path.write_bytes(gzip.compress(b'{"version": 0}'))
    assert load_lists(xtream_key("http://s:80", "alice")) is None


def test_cache_follows_config_dir_in_tests(monkeypatch, tmp_path):
    from bonsai.storage import cache_dir

    monkeypatch.delenv("BONSAI_CACHE_DIR")
    monkeypatch.setenv("BONSAI_CONFIG_DIR", str(tmp_path / "cfg"))
    assert cache_dir() == tmp_path / "cfg" / "cache"


def test_playlist_channels_round_trip_with_header():
    chans = [Channel(9, "X", "News", url="https://e.com/a.m3u8",
                     attrs=(("http-user-agent", "Mozilla/5.0"),), options=("#EXTVLCOPT:http-user-agent=Mozilla/5.0",))]
    save_lists(playlist_key("https://e.com/list.m3u"), [Category("News", "News")], chans, {"x-tvg-url": "https://e.com/g.xml"})
    loaded = load_lists(playlist_key("https://e.com/list.m3u"))
    assert loaded.channels == chans
    assert loaded.header == {"x-tvg-url": "https://e.com/g.xml"}
    assert load_lists(playlist_key("https://e.com/other.m3u")) is None


def test_version_1_cache_still_readable():
    import json
    path = cache_path(xtream_key("http://s:80", "alice"))
    path.parent.mkdir(parents=True)
    old = {"version": 1, "fetched_at": "2026-10-09T10:00:00+00:00", "categories": [["1", "A"]],
           "channels": [[1, "CNN", "1", None, "", "", None, False]]}
    path.write_bytes(gzip.compress(json.dumps(old).encode()))
    loaded = load_lists(xtream_key("http://s:80", "alice"))
    assert loaded.channels == [Channel(1, "CNN", "1")]
