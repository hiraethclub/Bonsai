import pytest

from bonsai import cli, profiles
from bonsai.filters import FilterState, Flag
from bonsai.models import Category, Channel
from bonsai.profiles import NEW_INCLUDE, snapshot
from bonsai.quality import Tier


class FakeClient:
    server = "http://s:80"
    username = "alice"

    def __init__(self, cats, chans):
        self.cats, self.chans = cats, chans

    def live_categories(self):
        return self.cats

    def live_streams(self, category_id=None):
        return self.chans

    def stream_url(self, sid, ext="ts"):
        return f"http://s:80/live/alice/pw/{sid}.{ext}"

    def epg_url(self):
        return "http://s:80/xmltv.php"


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setenv("BONSAI_CONFIG_DIR", str(tmp_path / "cfg"))


def test_regenerate(tmp_path, monkeypatch, capsys):
    cats = [Category("1", "UK: GENERAL"), Category("2", "UK: SPORT")]
    v1 = [Channel(1, "BBC One", "1"), Channel(2, "BBC One FHD", "1"), Channel(10, "Sky Sports", "2"),
          Channel(11, "Sky Sports PPV", "2")]
    groups = [(cats[0], v1[:2]), (cats[1], v1[2:])]
    out = tmp_path / "vita.m3u"
    profiles.save(snapshot(
        "Vita", groups, {1, 2, 10, 11}, {"2"}, new_channels=NEW_INCLUDE, export_path=str(out),
        filters=FilterState(frozenset({Tier.FHD}), frozenset({Flag.PPV}), frozenset()),
    ))

    v2 = v1 + [Channel(12, "Sky Sports News", "2")]  # new in a fully ticked category
    monkeypatch.setattr(cli, "_client", lambda: FakeClient(cats, v2))
    assert cli.main(["regenerate", "Vita"]) == 0

    text = out.read_text()
    urls = [line for line in text.splitlines() if line.startswith("http")]
    # FHD and PPV filtered out by the profile; the new channel included automatically
    assert urls == ["http://s:80/live/alice/pw/1.ts", "http://s:80/live/alice/pw/10.ts",
                    "http://s:80/live/alice/pw/12.ts"]
    msg = capsys.readouterr().out
    assert "Wrote 3 channels" in msg
    assert "1 new channels included automatically" in msg


def test_regenerate_needs_a_path(monkeypatch, capsys):
    profiles.save(profiles.Profile(name="Empty"))
    assert cli.main(["regenerate", "Empty"]) == 1
    assert "no export path" in capsys.readouterr().err


def test_regenerate_unknown_profile(capsys):
    assert cli.main(["regenerate", "Nope"]) == 1
    assert "No profile" in capsys.readouterr().err


def test_test_command_splits_working_and_dead(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    ts = bytes([0x47] + [0] * 187) * 20

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/ok.ts":
                self.send_response(200)
                self.send_header("Content-Length", str(len(ts)))
                self.end_headers()
                self.wfile.write(ts)
            else:
                self.send_response(404)
                self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    pl = tmp_path / "in.m3u"
    pl.write_text(f'#EXTM3U\n#EXTINF:-1 group-title="A;B",Good\n{base}/ok.ts\n'
                  f'#EXTINF:-1 group-title="A",Bad\n{base}/gone.ts\n')
    good, bad = tmp_path / "good.m3u", tmp_path / "bad.m3u"
    try:
        assert cli.main(["test", str(pl), "-o", str(good), "--not-working", str(bad)]) == 0
    finally:
        srv.shutdown()
    assert good.read_text().count("#EXTINF") == 1 and "ok.ts" in good.read_text()  # written once despite 2 groups
    assert "# Not working (" in bad.read_text() and "HTTP 404: gone" in bad.read_text()
