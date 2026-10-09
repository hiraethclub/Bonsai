import pytest
import requests

from bonsai.api import (
    AuthFailed,
    BadResponse,
    ServerUnreachable,
    XtreamClient,
    normalise_server,
)


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Stands in for requests.Session; returns queued responses or raises."""

    def __init__(self, result):
        self.result = result
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_client(result, password="s3cr#t/pw"):
    return XtreamClient("example.com:8080/", "alice", password, session=FakeSession(result))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("example.com:8080", "http://example.com:8080"),
        ("http://example.com:8080/", "http://example.com:8080"),
        ("https://example.com", "https://example.com"),
        ("http://example.com:8080/player_api.php?username=a&password=b", "http://example.com:8080"),
        ("http://example.com/sub/get.php", "http://example.com/sub"),
        ("  http://example.com:80  ", "http://example.com:80"),
    ],
)
def test_normalise_server(raw, expected):
    assert normalise_server(raw) == expected


@pytest.mark.parametrize("raw", ["", "ftp://example.com", "http://"])
def test_normalise_server_rejects_junk(raw):
    with pytest.raises(ValueError):
        normalise_server(raw)


def test_account_info_parses_fields():
    payload = {
        "user_info": {
            "username": "alice",
            "auth": 1,
            "status": "Active",
            "exp_date": "1893456000",
            "is_trial": "0",
            "active_cons": "0",
            "max_connections": "2",
            "allowed_output_formats": ["m3u8", "ts", "rtmp"],
        },
        "server_info": {"timezone": "Europe/London"},
    }
    client = make_client(FakeResponse(payload))
    info = client.account_info()
    assert info.is_active
    assert info.max_connections == 2
    assert info.active_connections == 0
    assert info.supports_m3u8
    assert info.expires is not None and info.expires.year == 2030
    url, params, _ = client.session.calls[0]
    assert url == "http://example.com:8080/player_api.php"
    assert "action" not in params


@pytest.mark.parametrize("payload", [[], {}, {"user_info": {"auth": 0}}])
def test_account_info_bad_login(payload):
    with pytest.raises(AuthFailed):
        make_client(FakeResponse(payload)).account_info()


def test_http_403_is_auth_failure():
    with pytest.raises(AuthFailed):
        make_client(FakeResponse(status=403)).account_info()


def test_unreachable_server_message_has_no_credentials():
    exc = requests.ConnectionError(
        "HTTPConnectionPool: /player_api.php?username=alice&password=s3cr%23t%2Fpw"
    )
    with pytest.raises(ServerUnreachable) as err:
        make_client(exc).account_info()
    assert "s3cr" not in str(err.value)
    assert "example.com:8080" in str(err.value)


def test_timeout_is_unreachable():
    with pytest.raises(ServerUnreachable):
        make_client(requests.Timeout()).account_info()


def test_non_json_is_redacted():
    page = "<html>error for user alice password s3cr#t/pw</html>"
    with pytest.raises(BadResponse) as err:
        make_client(FakeResponse(text=page)).live_categories()
    assert "s3cr" not in str(err.value)
    assert "alice" not in str(err.value)


def test_live_categories_keep_order_and_skip_bad_rows():
    payload = [
        {"category_id": "10", "category_name": "UK | General"},
        {"category_id": "", "category_name": "broken"},
        "junk",
        {"category_id": 3, "category_name": "Sports"},
    ]
    cats = make_client(FakeResponse(payload)).live_categories()
    assert [(c.id, c.name) for c in cats] == [("10", "UK | General"), ("3", "Sports")]


def test_live_streams_parsing_and_category_param():
    payload = [
        {"num": 1, "name": "BBC One HD", "stream_id": "101", "stream_icon": "http://x/a.png",
         "epg_channel_id": "bbc1.uk", "added": "1700000000", "category_id": "10"},
        {"num": None, "name": "No cat id", "stream_id": 102, "category_ids": [7]},
        {"name": "missing id"},
    ]
    client = make_client(FakeResponse(payload))
    chans = client.live_streams("10")
    assert [c.stream_id for c in chans] == [101, 102]
    assert chans[0].epg_channel_id == "bbc1.uk"
    assert chans[0].added is not None
    assert chans[1].category_id == "7"
    _, params, _ = client.session.calls[0]
    assert params["action"] == "get_live_streams"
    assert params["category_id"] == "10"


def test_live_streams_without_category_omits_param():
    client = make_client(FakeResponse([]))
    client.live_streams()
    _, params, _ = client.session.calls[0]
    assert "category_id" not in params


def test_stream_and_epg_urls():
    client = make_client(FakeResponse([]), password="plainpw")
    assert client.stream_url(101) == "http://example.com:8080/live/alice/plainpw/101.ts"
    assert client.stream_url(101, "m3u8").endswith("/101.m3u8")
    assert client.epg_url() == "http://example.com:8080/xmltv.php?username=alice&password=plainpw"


def test_stream_url_escapes_awkward_password():
    client = make_client(FakeResponse([]), password="a/b#c")
    assert client.stream_url(5) == "http://example.com:8080/live/alice/a%2Fb%23c/5.ts"
