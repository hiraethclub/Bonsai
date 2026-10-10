"""Stream tests: is a stream alive (quick test), and will VitaIPTV play it (deep test).

Quick test: fetch the stream like a player would. For HLS, read the playlist,
choose the variant VitaIPTV would choose (the best one up to 720 lines), read
its media playlist and the start of one segment, and check it is real video.
About a second per stream, no ffprobe needed.

Deep test: the quick test, then ffprobe on what the Vita would actually play
(the chosen variant's segment, or the stream itself) and VitaIPTV's rules:
H.264 8-bit 4:2:0, at most 1280x720, progressive, AAC/MP2/MP3 audio, MPEG-TS.

Requests send the stream's own User-Agent / Referer when the playlist gives
one, otherwise VitaIPTV's User-Agent.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import requests

VITA_USER_AGENT = "VLC/3.0.20 LibVLC/3.0.20"  # what VitaIPTV sends (src/netua.h)
VITA_MAX_HEIGHT = 720
QUICK_TIMEOUT = (6, 10)  # connect, read
DEEP_TIMEOUT = 25
PLAYLIST_LIMIT = 512 * 1024
PEEK_LIMIT = 64 * 1024

OK, FAIL, SKIP = "ok", "fail", "skip"
VITA_YES, VITA_NO = "yes", "no"
QUICK, DEEP = "quick", "deep"

# HTTP codes Xtream panels use for "this account is already streaming".
ACCOUNT_BUSY_CODES = {407, 458, 509, 511}


@dataclass
class TestResult:
    status: str  # OK, FAIL or SKIP (could not be tested this way)
    reason: str = ""  # short: "HTTP 404", "HLS 1280x720, TS"
    detail: str = ""  # longer: codecs etc.
    vita: str = ""  # VITA_YES / VITA_NO / "" unknown
    vita_reason: str = ""
    mode: str = QUICK
    at: str = ""
    account_busy: bool = False

    def to_json(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in ("", False)}

    @classmethod
    def from_json(cls, data: dict) -> TestResult:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class StreamRequest:
    url: str
    user_agent: str = ""
    referrer: str = ""

    def headers(self) -> dict[str, str]:
        h = {"User-Agent": self.user_agent or VITA_USER_AGENT}
        if self.referrer:
            h["Referer"] = self.referrer
        return h


def request_for(channel, url: str | None = None) -> StreamRequest:
    """A StreamRequest from a Channel (playlist attributes / #EXTVLCOPT lines give UA and Referer)."""
    attrs = dict(channel.attrs)
    ua = attrs.get("http-user-agent", "")
    ref = attrs.get("http-referrer", "") or attrs.get("http-referer", "")
    for opt in channel.options:
        key, _, value = opt.partition(":")[2].partition("=")
        key = key.strip().lower()
        if key == "http-user-agent" and not ua:
            ua = value.strip()
        elif key in ("http-referrer", "http-referer") and not ref:
            ref = value.strip()
    return StreamRequest(url or channel.url, ua, ref)


# -- HLS -------------------------------------------------------------------------


@dataclass
class Variant:
    uri: str
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    hevc: bool = False
    audio_only: bool = False
    avc_level: int = 0


_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^",]*)')


def parse_master(text: str, base: str) -> list[Variant]:
    variants: list[Variant] = []
    pending: Variant | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = {k: v.strip('"') for k, v in _ATTR.findall(line[18:])}
            v = Variant("")
            v.bandwidth = int(attrs.get("BANDWIDTH", "0") or 0) if attrs.get("BANDWIDTH", "").isdigit() else 0
            if m := re.match(r"(\d+)x(\d+)", attrs.get("RESOLUTION", "")):
                v.width, v.height = int(m.group(1)), int(m.group(2))
            codecs = attrs.get("CODECS", "").lower()
            v.hevc = "hvc1" in codecs or "hev1" in codecs
            v.audio_only = bool(codecs) and "avc" not in codecs and not v.hevc and "mp4a" in codecs
            if m := re.search(r"avc[13]\.[0-9a-f]{4}([0-9a-f]{2})", codecs):
                v.avc_level = int(m.group(1), 16)
            pending = v
        elif line.startswith("#"):
            continue
        elif pending is not None:
            pending.uri = urljoin(base, line)
            variants.append(pending)
            pending = None
    return variants


def _fits(v: Variant, max_h: int) -> int:
    """1 fits the decoder, 0 too big, -1 unknown (VitaIPTV's variant_fits)."""
    max_w = 1920 if max_h > 720 else 1280
    if v.height > 0:
        return int(v.height <= max_h and v.width <= max_w)
    if v.avc_level > 0:
        return int(v.avc_level <= (42 if max_h > 720 else 32))
    return -1


def pick_variant(variants: list[Variant], max_h: int = VITA_MAX_HEIGHT) -> Variant | None:
    """The variant VitaIPTV plays (a port of hls_pick_variant)."""
    best = unsized = unsized_small = fallback = None
    cap = 6_000_000 if max_h > 720 else 2_600_000
    for v in variants:
        if v.hevc or v.audio_only:
            continue
        fits = _fits(v, max_h)
        if fits == 1 and v.height > 0:
            if best is None or v.height > best.height or (v.height == best.height and v.bandwidth > best.bandwidth):
                best = v
        elif fits != 0:
            if unsized_small is None or v.bandwidth < unsized_small.bandwidth:
                unsized_small = v
            if (fits == 1 or v.bandwidth <= cap) and (unsized is None or v.bandwidth > unsized.bandwidth):
                unsized = v
        else:
            if fallback is None or (v.height and fallback.height and v.height < fallback.height) or (
                v.height == fallback.height and v.bandwidth < fallback.bandwidth
            ):
                fallback = v
    for choice in (best, unsized, unsized_small, fallback):
        if choice is not None:
            return choice
    return next((v for v in variants if not v.audio_only), variants[0] if variants else None)


@dataclass
class MediaPlaylist:
    segments: list[str] = field(default_factory=list)
    fmp4: bool = False
    encrypted: bool = False


def parse_media(text: str, base: str) -> MediaPlaylist:
    m = MediaPlaylist()
    have_inf = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#EXT-X-MAP"):
                m.fmp4 = True
            elif line.startswith("#EXT-X-KEY:"):
                method = dict((k, v.strip('"')) for k, v in _ATTR.findall(line[11:])).get("METHOD", "NONE")
                if method.upper() != "NONE":
                    m.encrypted = True
            elif line.startswith("#EXTINF:"):
                have_inf = True
            continue
        if have_inf:
            m.segments.append(urljoin(base, line))
            have_inf = False
    return m


# -- content sniffing --------------------------------------------------------------


def sniff(data: bytes) -> str:
    """'ts', 'fmp4', 'audio', 'html', 'hls', 'dash' or '' for unknown."""
    head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"#EXTM3U"):
        return "hls"
    if head[:5].lower() in (b"<?xml", b"<mpd ") and b"<MPD" in data[:4096]:
        return "dash"
    if head[:1] == b"<":
        return "html"
    if len(data) >= 189 and data[0] == 0x47 and data[188] == 0x47:
        return "ts"
    for off in range(min(188, max(0, len(data) - 377))):
        if data[off] == 0x47 and data[off + 188] == 0x47 and data[off + 376] == 0x47:
            return "ts"
    if data[4:8] in (b"ftyp", b"styp", b"moof", b"sidx"):
        return "fmp4"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xF0 == 0xF0):
        return "audio"
    return ""


# -- quick test ----------------------------------------------------------------------


class _Failed(Exception):
    def __init__(self, reason: str, busy: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.busy = busy


def _http_reason(code: int, xtream: bool) -> tuple[str, bool]:
    if code in ACCOUNT_BUSY_CODES and xtream:
        return f"HTTP {code}: account already in use (connection limit)", True
    if code in (401, 403):
        return f"HTTP {code}: forbidden (geo-blocked or needs a login)", False
    if code == 404:
        return "HTTP 404: gone", False
    if code == 410:
        return "HTTP 410: gone", False
    if code == 511:
        return "HTTP 511: needs a login", False
    if code >= 500:
        return f"HTTP {code}: server error", False
    return f"HTTP {code}", False


def _fetch(session: requests.Session, url: str, headers: dict, limit: int, xtream: bool) -> tuple[bytes, str]:
    """First `limit` bytes of a URL and the final URL after redirects."""
    try:
        with session.get(url, headers=headers, timeout=QUICK_TIMEOUT, stream=True, allow_redirects=True) as r:
            if r.status_code >= 400:
                raise _Failed(*_http_reason(r.status_code, xtream))
            data = bytearray()
            for chunk in r.iter_content(16384):
                data += chunk
                if len(data) >= limit:
                    break
            return bytes(data[:limit]), r.url
    except _Failed:
        raise
    except requests.exceptions.SSLError:
        raise _Failed("TLS / certificate error") from None
    except requests.Timeout:
        raise _Failed("timed out") from None
    except requests.TooManyRedirects:
        raise _Failed("too many redirects") from None
    except requests.ConnectionError as exc:
        text = str(exc)
        if "Name or service not known" in text or "getaddrinfo" in text or "NameResolution" in text:
            raise _Failed("server not found (DNS)") from None
        if "refused" in text.lower():
            raise _Failed("connection refused") from None
        raise _Failed("connection failed") from None
    except requests.RequestException as exc:
        raise _Failed(f"request failed ({type(exc).__name__})") from None


@dataclass
class _Analysis:
    result: TestResult
    probe_url: str = ""  # what ffprobe should look at for the deep test


def _analyse(req: StreamRequest, session: requests.Session, xtream: bool) -> _Analysis:
    scheme = urlsplit(req.url).scheme.lower()
    if scheme not in ("http", "https"):
        return _Analysis(
            TestResult(SKIP, f"{scheme}:// can't be quick-tested; try the deep test", vita=VITA_NO,
                       vita_reason=f"{scheme}:// is not supported by VitaIPTV"),
            probe_url=req.url,
        )
    headers = req.headers()
    try:
        data, final = _fetch(session, req.url, headers, PLAYLIST_LIMIT if _looks_like_playlist(req.url) else PEEK_LIMIT, xtream)
        kind = sniff(data)
        if kind == "hls":
            return _analyse_hls(data.decode("utf-8", "replace"), final, session, headers, xtream)
        if kind == "dash":
            return _Analysis(TestResult(OK, "DASH manifest (segments not checked)", vita=VITA_NO,
                                        vita_reason="DASH is not supported by VitaIPTV"), req.url)
        if kind == "ts":
            return _Analysis(TestResult(OK, "MPEG-TS"), req.url)
        if kind == "fmp4":
            return _Analysis(TestResult(OK, "MP4 stream"), req.url)
        if kind == "audio":
            return _Analysis(TestResult(OK, "audio only"), req.url)
        if kind == "html":
            return _Analysis(TestResult(FAIL, "web page, not a stream"))
        if not data:
            return _Analysis(TestResult(FAIL, "empty response"))
        return _Analysis(TestResult(FAIL, "not a recognised stream"))
    except _Failed as exc:
        return _Analysis(TestResult(FAIL, exc.reason, account_busy=exc.busy))


def _looks_like_playlist(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith((".m3u8", ".m3u", ".mpd")) or "." not in path.rsplit("/", 1)[-1]


def _analyse_hls(text: str, base: str, session, headers, xtream: bool) -> _Analysis:
    vita_no: list[str] = []
    label = "HLS"
    if "#EXT-X-STREAM-INF" in text:
        variants = parse_master(text, base)
        chosen = pick_variant(variants)
        if chosen is None:
            return _Analysis(TestResult(FAIL, "HLS playlist without video variants"))
        if chosen.hevc:
            vita_no.append("only HEVC variants")
        elif _fits(chosen, VITA_MAX_HEIGHT) == 0:
            vita_no.append(f"smallest variant is {chosen.width}x{chosen.height}")
        if chosen.height:
            label = f"HLS {chosen.width}x{chosen.height}"
            if len(variants) > 1:
                label += f" (of {len(variants)} variants)"
        try:
            data, base = _fetch(session, chosen.uri, headers, PLAYLIST_LIMIT, xtream)
        except _Failed as exc:
            return _Analysis(TestResult(FAIL, f"variant playlist: {exc.reason}", account_busy=exc.busy))
        text = data.decode("utf-8", "replace")
        if "#EXT-X-STREAM-INF" in text:
            return _Analysis(TestResult(FAIL, "nested HLS master playlists"))
    media = parse_media(text, base)
    if not media.segments:
        return _Analysis(TestResult(FAIL, "HLS playlist has no segments (off air?)"))
    if media.encrypted:
        vita_no.append("encrypted HLS")
    if media.fmp4:
        vita_no.append("fMP4 segments")
    # The newest segments are the ones still on the server; take one a little back from the end.
    segment = media.segments[max(0, len(media.segments) - 3)]
    try:
        peek, _ = _fetch(session, segment, headers, 8192, xtream)
    except _Failed as exc:
        return _Analysis(TestResult(FAIL, f"segment: {exc.reason}", account_busy=exc.busy))
    kind = sniff(peek)
    if media.encrypted:
        kind = kind or "encrypted"
    if kind not in ("ts", "fmp4", "audio", "encrypted"):
        return _Analysis(TestResult(FAIL, "HLS segment is not video" if kind != "html" else "HLS segment is a web page"))
    label += {"ts": ", TS", "fmp4": ", fMP4", "audio": ", audio only", "encrypted": ", encrypted"}[kind]
    result = TestResult(OK, label)
    if vita_no:
        result.vita, result.vita_reason = VITA_NO, "; ".join(vita_no)
    return _Analysis(result, segment)


def quick_test(req: StreamRequest, session: requests.Session | None = None, xtream: bool = False) -> TestResult:
    result = _analyse(req, session or requests.Session(), xtream).result
    result.mode = QUICK
    result.at = _now()
    return result


# -- deep test ---------------------------------------------------------------------------


def vita_verdict(streams: list[dict]) -> tuple[str, str, str]:
    """(VITA_YES/VITA_NO, reasons, description) from ffprobe streams, using VitaIPTV's rules."""
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s.get("codec_name", "?") for s in streams if s.get("codec_type") == "audio"]
    reasons: list[str] = []
    notes: list[str] = []
    parts: list[str] = []
    if video is None:
        parts.append("no video")
        if not audio:
            reasons.append("no video or audio found")
    else:
        codec = video.get("codec_name", "?")
        w, h = video.get("width") or 0, video.get("height") or 0
        field = video.get("field_order") or "unknown"
        fps = _fps(video)
        parts.append(f"{codec} {w}x{h}" + (" interlaced" if field not in ("progressive", "unknown") else "")
                     + (f" {fps:g}fps" if fps else ""))
        if codec != "h264":
            reasons.append("HEVC video" if codec == "hevc" else f"{codec} video")
        if video.get("pix_fmt") not in (None, "yuv420p", "yuvj420p"):
            reasons.append(f"{video.get('pix_fmt')} (needs 8-bit 4:2:0)")
        if w > 1280 or h > 720:
            reasons.append(f"{w}x{h} is above 720p")
        if field not in ("progressive", "unknown"):
            reasons.append("interlaced")
        if fps > 31:
            notes.append(f"{fps:g} fps may not play smoothly")
    if audio:
        parts.append("/".join(audio))
        if not any(a in ("aac", "mp2", "mp3") for a in audio):
            reasons.append(f"{'/'.join(sorted(set(audio)))} audio (needs AAC, MP2 or MP3)")
    if reasons:
        return VITA_NO, "; ".join(reasons), ", ".join(parts)
    return VITA_YES, "; ".join(notes), ", ".join(parts)


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = str(stream.get(key, "0/0")).partition("/")
        try:
            if float(den or 0):
                return round(float(num) / float(den), 2)
        except ValueError:
            continue
    return 0.0


FFPROBE_HELP = (
    "Download FFmpeg (e.g. from gyan.dev or with: winget install Gyan.FFmpeg) and put ffprobe.exe "
    "next to Bonsai.exe or on your PATH."
    if sys.platform == "win32"
    else "On Debian: sudo apt install ffmpeg"
)


def find_ffprobe() -> str | None:
    """ffprobe on PATH, or next to the program (handy for the packaged Windows app)."""
    found = shutil.which("ffprobe")
    if found:
        return found
    exe_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0] or "."))
    for name in ("ffprobe.exe", "ffprobe"):
        candidate = os.path.join(exe_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return None


# Keep a console window from flashing up for every probe in the windowed Windows app.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def run_ffprobe(url: str, req: StreamRequest, ffprobe: str = "", redact=lambda s: s) -> tuple[list[dict], str]:
    ffprobe = ffprobe or find_ffprobe() or "ffprobe"
    cmd = [ffprobe, "-v", "error", "-of", "json", "-rw_timeout", "10000000",
           "-analyzeduration", "6000000", "-probesize", "6000000",
           "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height,field_order,avg_frame_rate,r_frame_rate",
           "-user_agent", req.user_agent or VITA_USER_AGENT]
    if req.referrer:
        cmd += ["-headers", f"Referer: {req.referrer}\r\n"]
    try:
        p = subprocess.run(cmd + [url], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=DEEP_TIMEOUT, **_NO_WINDOW)
    except FileNotFoundError:
        return [], f"ffprobe is not installed. {FFPROBE_HELP}"
    except subprocess.TimeoutExpired:
        return [], "ffprobe timed out"
    err = [redact(line.replace(url, "<stream>")) for line in p.stderr.strip().splitlines()]
    try:
        streams = json.loads(p.stdout or "{}").get("streams", [])
    except ValueError:
        streams = []
    return streams, (err[-1] if err else "")


def deep_test(req: StreamRequest, session: requests.Session | None = None, xtream: bool = False,
              ffprobe: str = "", redact=lambda s: s) -> TestResult:
    analysis = _analyse(req, session or requests.Session(), xtream)
    result = analysis.result
    result.mode, result.at = DEEP, _now()
    if result.status == FAIL or not analysis.probe_url:
        return result
    streams, err = run_ffprobe(analysis.probe_url, req, ffprobe, redact)
    if not streams:
        if result.status == SKIP:
            result.status, result.reason = FAIL, err or "no streams found"
        else:
            result.detail = f"ffprobe: {err or 'no streams found'}"
        return result
    verdict, why, description = vita_verdict(streams)
    result.status = OK
    result.detail = description
    if result.vita != VITA_NO:  # a "no" from the playlist (fMP4, encrypted, DASH) stands
        result.vita, result.vita_reason = verdict, why
    elif verdict == VITA_NO:
        result.vita_reason += "; " + why
    if result.reason.startswith(("rtmp", "mmsh", "srt", "udp", "rtsp")) or result.reason.endswith("deep test"):
        result.reason = description
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- running many tests -------------------------------------------------------------------------


@dataclass
class TestJob:
    key: int  # stream_id in the tree
    request: StreamRequest


def interleave_by_host(jobs: Iterable[TestJob]) -> list[TestJob]:
    """Spread jobs so consecutive ones hit different servers."""
    by_host: dict[str, deque[TestJob]] = defaultdict(deque)
    for job in jobs:
        by_host[urlsplit(job.request.url).netloc].append(job)
    queues = list(by_host.values())
    random.Random(0).shuffle(queues)
    out: list[TestJob] = []
    while queues:
        queues = [q for q in queues if q]
        for q in queues:
            out.append(q.popleft())
    return out


def run_tests(
    jobs: list[TestJob],
    test: Callable[[StreamRequest, requests.Session], TestResult],
    on_result: Callable[[TestJob, TestResult], None],
    cancel: threading.Event,
    workers: int = 8,
    per_host: int = 2,
    delay: float = 0.0,
) -> None:
    """Run `test` over `jobs` on `workers` threads, at most `per_host` at once per server.

    `delay` is a pause after each test on a worker (used for Xtream accounts, which
    are tested one at a time). Stops early when `cancel` is set.
    """
    queue = deque(interleave_by_host(jobs))
    lock = threading.Lock()
    host_slots: dict[str, threading.Semaphore] = defaultdict(lambda: threading.Semaphore(per_host))

    def worker() -> None:
        session = requests.Session()
        while not cancel.is_set():
            with lock:
                if not queue:
                    return
                job = queue.popleft()
                slot = host_slots[urlsplit(job.request.url).netloc]
            with slot:
                if cancel.is_set():
                    return
                try:
                    result = test(job.request, session)
                except Exception as exc:  # a bug in one test must not stop the batch
                    result = TestResult(FAIL, f"test error: {type(exc).__name__}", at=_now())
            on_result(job, result)
            if delay:
                cancel.wait(delay)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(workers, len(jobs))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def estimate_seconds(count: int, mode: str, workers: int, delay: float) -> float:
    per = (8.0 if mode == DEEP else 1.5) + delay
    return count * per / max(1, workers)
