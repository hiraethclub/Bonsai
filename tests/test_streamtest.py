import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bonsai import streamtest as st
from bonsai.models import Channel
from bonsai.results import (
    NOT_ON_VITA,
    NOT_WORKING,
    UNTESTED,
    WORKING,
    load_results,
    result_key,
    save_results,
    status_class,
)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_ts(path, size="1280x720", codec="libx264", rate=25, extra=()):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:v", codec, *extra,
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts", str(path)],
        check=True,
    )
    return path.read_bytes()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    if not HAVE_FFMPEG:
        fake = bytes([0x47] + [0] * 187) * 50
        return {"720": fake, "1080": fake, "mpeg2": fake}
    return {
        "720": make_ts(d / "a.ts"),
        "1080": make_ts(d / "b.ts", size="1920x1080"),
        "mpeg2": make_ts(d / "c.ts", codec="mpeg2video"),
    }


class Server:
    def __init__(self, routes):
        self.routes = routes
        self.hits = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                with outer.lock:
                    outer.hits.append((self.path, dict(self.headers)))
                    outer.active += 1
                    outer.max_active = max(outer.max_active, outer.active)
                try:
                    route = outer.routes.get(self.path.split("?")[0])
                    if route is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    status, body, ctype = route(self) if callable(route) else route
                    if isinstance(body, str):
                        body = body.encode()
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                finally:
                    with outer.lock:
                        outer.active -= 1

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path):
        return self.base + path

    def close(self):
        self.httpd.shutdown()


M3U = "application/vnd.apple.mpegurl"
TS = "video/mp2t"


def media_playlist(seg, extra=""):
    return f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n{extra}#EXTINF:1,\n{seg}\n#EXTINF:1,\n{seg}\n"


@pytest.fixture
def server(media):
    def needs_ua(h):
        ok = h.headers.get("User-Agent") == "Special/1.0"
        return (200, media_playlist("seg720.ts"), M3U) if ok else (403, "no", "text/plain")

    def needs_ref(h):
        ok = h.headers.get("Referer") == "https://site.example/"
        return (200, media["720"], TS) if ok else (403, "no", "text/plain")

    def slow(h):
        time.sleep(0.3)
        return 200, media["720"], TS

    routes = {
        "/direct.ts": (200, media["720"], TS),
        "/seg720.ts": (200, media["720"], TS),
        "/seg1080.ts": (200, media["1080"], TS),
        "/segmpeg2.ts": (200, media["mpeg2"], TS),
        "/v720.m3u8": (200, media_playlist("seg720.ts"), M3U),
        "/v1080.m3u8": (200, media_playlist("seg1080.ts"), M3U),
        "/master.m3u8": (200, "#EXTM3U\n"
                         '#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"\n'
                         "v1080.m3u8\n"
                         '#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2"\n'
                         "v720.m3u8\n", M3U),
        "/only1080.m3u8": (200, "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080\nv1080.m3u8\n", M3U),
        "/mpeg2.m3u8": (200, media_playlist("segmpeg2.ts"), M3U),
        "/fmp4.m3u8": (200, media_playlist("init.mp4", '#EXT-X-MAP:URI="init.mp4"\n'), M3U),
        "/init.mp4": (200, b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64, "video/mp4"),
        "/enc.m3u8": (200, media_playlist("seg720.ts", '#EXT-X-KEY:METHOD=AES-128,URI="k"\n'), M3U),
        "/empty.m3u8": (200, "#EXTM3U\n#EXT-X-TARGETDURATION:2\n", M3U),
        "/page": (200, "<!DOCTYPE html><html>offline</html>", "text/html"),
        "/ua.m3u8": needs_ua,
        "/ref.ts": needs_ref,
        "/busy.ts": (511, "", "text/html"),
        "/dash.mpd": (200, '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011"></MPD>', "application/dash+xml"),
        "/slow.ts": slow,
    }
    s = Server(routes)
    yield s
    s.close()


def req(server, path, **kw):
    return st.StreamRequest(server.url(path), **kw)


# -- pure functions ---------------------------------------------------------------


def test_sniff():
    assert st.sniff(b"#EXTM3U\n") == "hls"
    assert st.sniff(b"\xef\xbb\xbf#EXTM3U") == "hls"
    assert st.sniff(bytes([0x47] + [0] * 187) * 3) == "ts"
    assert st.sniff(b"\x00" * 5 + bytes([0x47] + [0] * 187) * 3) == "ts"  # sync not at offset 0
    assert st.sniff(b"\x00\x00\x00\x18ftypisom") == "fmp4"
    assert st.sniff(b"<html><body>") == "html"
    assert st.sniff(b'<?xml version="1.0"?><MPD>') == "dash"
    assert st.sniff(b"ID3\x03") == "audio"
    assert st.sniff(b"hello") == ""


def V(h, w=None, bw=1000, **kw):
    return st.Variant(f"v{h}", bandwidth=bw, width=w or h * 16 // 9, height=h, **kw)


def test_pick_variant_like_vitaiptv():
    assert st.pick_variant([V(1080), V(720), V(480)]).height == 720
    assert st.pick_variant([V(720, bw=1), V(720, bw=9)]).bandwidth == 9  # same height: higher bitrate
    assert st.pick_variant([V(1080, bw=5), V(2160, bw=9)]).height == 1080  # nothing fits: smallest
    assert st.pick_variant([V(720, hevc=True), V(1080)]).height == 1080  # HEVC skipped
    unsized = [st.Variant("a", bandwidth=5_000_000), st.Variant("b", bandwidth=2_000_000), st.Variant("c", bandwidth=800_000)]
    assert st.pick_variant(unsized).uri == "b"  # highest bitrate under the 720p cap
    assert st.pick_variant([st.Variant("x", avc_level=0x20), st.Variant("y", avc_level=0x28)]).uri == "x"
    assert st.pick_variant([]) is None


def test_parse_master_and_media():
    text = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"\n'
            'low/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=64000,CODECS="mp4a.40.2"\naudio.m3u8\n')
    vs = st.parse_master(text, "https://e.com/live/master.m3u8")
    assert vs[0].uri == "https://e.com/live/low/index.m3u8" and vs[0].height == 360 and vs[0].avc_level == 0x1E
    assert vs[1].audio_only
    m = st.parse_media('#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:2,\n/a/1.ts\n#EXTINF:2,\n2.ts\n', "https://e.com/x/p.m3u8")
    assert m.segments == ["https://e.com/a/1.ts", "https://e.com/x/2.ts"] and not m.encrypted and not m.fmp4


def test_request_for_reads_attrs_and_vlcopt():
    ch = Channel(1, "X", "c", url="http://e/1", attrs=(("http-user-agent", "UA/1"),),
                 options=("#EXTVLCOPT:http-referrer=https://r.example/",))
    r = st.request_for(ch)
    assert (r.user_agent, r.referrer) == ("UA/1", "https://r.example/")
    assert r.headers() == {"User-Agent": "UA/1", "Referer": "https://r.example/"}
    assert st.request_for(Channel(2, "Y", "c", url="http://e/2")).headers() == {"User-Agent": st.VITA_USER_AGENT}


def test_vita_verdict_rules():
    ok = [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "pix_fmt": "yuv420p",
           "field_order": "progressive", "avg_frame_rate": "25/1"}, {"codec_type": "audio", "codec_name": "aac"}]
    assert st.vita_verdict(ok)[0] == st.VITA_YES
    hi = [dict(ok[0], width=1920, height=1080), ok[1]]
    assert st.vita_verdict(hi)[:2] == (st.VITA_NO, "1920x1080 is above 720p")
    il = [dict(ok[0], field_order="tt"), ok[1]]
    assert "interlaced" in st.vita_verdict(il)[1]
    ac3 = [ok[0], {"codec_type": "audio", "codec_name": "ac3"}]
    assert "ac3 audio" in st.vita_verdict(ac3)[1]
    fps60 = [dict(ok[0], avg_frame_rate="60000/1001"), ok[1]]
    verdict, note, _ = st.vita_verdict(fps60)
    assert verdict == st.VITA_YES and "59.94 fps" in note
    assert st.vita_verdict([dict(ok[0], codec_name="hevc"), ok[1]])[1] == "HEVC video"


# -- quick test ---------------------------------------------------------------------


def test_quick_direct_ts(server):
    r = st.quick_test(req(server, "/direct.ts"))
    assert (r.status, r.reason) == (st.OK, "MPEG-TS")
    assert server.hits[0][1]["User-Agent"] == st.VITA_USER_AGENT


def test_quick_master_follows_the_variant_the_vita_would_play(server):
    r = st.quick_test(req(server, "/master.m3u8"))
    assert r.status == st.OK
    assert r.reason == "HLS 1280x720 (of 2 variants), TS"
    paths = [p for p, _ in server.hits]
    assert "/v720.m3u8" in paths and "/v1080.m3u8" not in paths
    assert r.vita == ""  # unknown until a deep test


def test_quick_flags_vita_problems(server):
    assert st.quick_test(req(server, "/only1080.m3u8")).vita == st.VITA_NO
    fm = st.quick_test(req(server, "/fmp4.m3u8"))
    assert fm.status == st.OK and fm.vita == st.VITA_NO and "fMP4" in fm.vita_reason
    enc = st.quick_test(req(server, "/enc.m3u8"))
    assert enc.status == st.OK and "encrypted" in enc.vita_reason
    dash = st.quick_test(req(server, "/dash.mpd"))
    assert dash.status == st.OK and dash.vita == st.VITA_NO


@pytest.mark.parametrize("path, reason", [
    ("/missing.ts", "HTTP 404: gone"),
    ("/page", "web page, not a stream"),
    ("/empty.m3u8", "HLS playlist has no segments (off air?)"),
])
def test_quick_failures(server, path, reason):
    r = st.quick_test(req(server, path))
    assert (r.status, r.reason) == (st.FAIL, reason)


def test_quick_uses_stream_user_agent_and_referrer(server):
    assert st.quick_test(req(server, "/ua.m3u8")).status == st.FAIL
    assert st.quick_test(req(server, "/ua.m3u8", user_agent="Special/1.0")).status == st.OK
    assert st.quick_test(req(server, "/ref.ts")).status == st.FAIL
    assert st.quick_test(req(server, "/ref.ts", referrer="https://site.example/")).status == st.OK


def test_account_busy_only_for_xtream(server):
    r = st.quick_test(req(server, "/busy.ts"), xtream=True)
    assert r.status == st.FAIL and r.account_busy and "account already in use" in r.reason
    r = st.quick_test(req(server, "/busy.ts"))
    assert not r.account_busy and r.reason == "HTTP 511: needs a login"


def test_connection_refused_and_non_http():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = st.quick_test(st.StreamRequest(f"http://127.0.0.1:{port}/x.ts"))
    assert (r.status, r.reason) == (st.FAIL, "connection refused")
    r = st.quick_test(st.StreamRequest("rtmp://example.com/live/x"))
    assert r.status == st.SKIP and r.vita == st.VITA_NO


# -- deep test ------------------------------------------------------------------------


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg/ffprobe")
def test_deep_verdicts(server):
    good = st.deep_test(req(server, "/master.m3u8"))
    assert good.status == st.OK and good.vita == st.VITA_YES, good
    assert good.detail.startswith("h264 1280x720") and "aac" in good.detail
    big = st.deep_test(req(server, "/v1080.m3u8"))
    assert big.vita == st.VITA_NO and "above 720p" in big.vita_reason
    mp2v = st.deep_test(req(server, "/mpeg2.m3u8"))
    assert mp2v.vita == st.VITA_NO and "mpeg2video video" in mp2v.vita_reason
    direct = st.deep_test(req(server, "/direct.ts"))
    assert direct.vita == st.VITA_YES and direct.mode == st.DEEP
    dead = st.deep_test(req(server, "/missing.ts"))
    assert dead.status == st.FAIL


def test_deep_without_ffprobe(server):
    r = st.deep_test(req(server, "/direct.ts"), ffprobe="/no/such/ffprobe")
    assert r.status == st.OK and "ffprobe is not installed" in r.detail


# -- batches ----------------------------------------------------------------------------


def test_interleave_by_host():
    jobs = [st.TestJob(i, st.StreamRequest(f"http://{h}/{i}")) for i, h in enumerate("aaaabbc")]
    hosts = [j.request.url.split("/")[2] for j in st.interleave_by_host(jobs)]
    assert sorted(hosts) == sorted("aaaabbc")
    assert hosts[:3].count("a") == 1  # spread out, not aaaa first


def test_run_tests_respects_per_host_limit(server):
    jobs = [st.TestJob(i, req(server, "/slow.ts")) for i in range(8)]
    got = {}
    st.run_tests(jobs, lambda r, s: st.quick_test(r, s), lambda j, r: got.__setitem__(j.key, r),
                 threading.Event(), workers=8, per_host=2)
    assert len(got) == 8 and all(r.status == st.OK for r in got.values())
    assert server.max_active <= 2


def test_run_tests_cancel(server):
    cancel = threading.Event()
    got = []

    def on_result(job, res):
        got.append(job.key)
        cancel.set()

    jobs = [st.TestJob(i, req(server, "/slow.ts")) for i in range(10)]
    st.run_tests(jobs, lambda r, s: st.quick_test(r, s), on_result, cancel, workers=1, per_host=1)
    assert got == [jobs[0].key] or len(got) <= 2


def test_run_tests_survives_a_crashing_test():
    def boom(r, s):
        raise RuntimeError("bug")

    got = []
    st.run_tests([st.TestJob(1, st.StreamRequest("http://x/1"))], boom, lambda j, r: got.append(r), threading.Event())
    assert got[0].status == st.FAIL and "RuntimeError" in got[0].reason


# -- results ------------------------------------------------------------------------------


def test_status_classes():
    assert status_class(None) == UNTESTED
    assert status_class(st.TestResult(st.SKIP)) == UNTESTED
    assert status_class(st.TestResult(st.FAIL, "x")) == NOT_WORKING
    assert status_class(st.TestResult(st.OK)) == WORKING
    assert status_class(st.TestResult(st.OK, vita=st.VITA_NO)) == NOT_ON_VITA


def test_results_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("BONSAI_CACHE_DIR", str(tmp_path))
    data = {"https://e/1": st.TestResult(st.OK, "MPEG-TS", at="2026-10-10T10:00:00+00:00"),
            "id:7": st.TestResult(st.FAIL, "HTTP 404: gone", account_busy=True)}
    save_results("list-a", data)
    assert load_results("list-a") == data
    assert load_results("list-b") == {}


def test_result_keys():
    assert result_key(Channel(7, "X", "c", url="https://e/1"), xtream=False) == "https://e/1"
    assert result_key(Channel(7, "X", "c"), xtream=True) == "id:7"
