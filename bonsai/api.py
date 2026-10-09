"""Read-only client for the Xtream Codes Player API, live TV only.

Every call here is a GET against player_api.php. Nothing in this module
changes anything on the provider side.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import requests

from . import __version__
from .models import AccountInfo, Category, Channel

# (connect, read) timeouts in seconds. The full live stream list can be several
# MB on big providers, so it gets a longer read timeout.
DEFAULT_TIMEOUT = (10, 30)
STREAMS_TIMEOUT = (10, 120)

DEFAULT_USER_AGENT = f"Bonsai/{__version__}"


class XtreamError(Exception):
    """Base class for anything that goes wrong talking to the server."""


class ServerUnreachable(XtreamError):
    pass


class AuthFailed(XtreamError):
    pass


class BadResponse(XtreamError):
    pass


def normalise_server(url: str) -> str:
    """Turn whatever the user typed into 'scheme://host[:port][/path]' with no trailing slash.

    Accepts 'example.com:8080', 'http://example.com:8080/', or a pasted
    'http://example.com:8080/player_api.php?...' or get.php link.
    """
    url = url.strip()
    if not url:
        raise ValueError("Server URL is empty")
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"Not a valid server URL: {url!r}")
    path = parts.path.rstrip("/")
    for tail in ("/player_api.php", "/get.php", "/xmltv.php"):
        if path.endswith(tail):
            path = path[: -len(tail)]
    return f"{parts.scheme}://{parts.netloc}{path}"


class XtreamClient:
    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        user_agent: str = "",
        session: requests.Session | None = None,
    ) -> None:
        self.server = normalise_server(server)
        self.username = username
        self.password = password
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent or DEFAULT_USER_AGENT

    # -- helpers -----------------------------------------------------------

    def redact(self, text: str) -> str:
        """Strip the credentials out of a message before it is shown or logged."""
        for secret in (self.password, self.username):
            if not secret:
                continue
            for form in {secret, quote(secret, safe=""), quote(secret)}:
                text = text.replace(form, "***")
        return text

    def _get(self, action: str | None = None, timeout=DEFAULT_TIMEOUT, **extra: Any) -> Any:
        params: dict[str, Any] = {"username": self.username, "password": self.password}
        if action:
            params["action"] = action
        params.update({k: v for k, v in extra.items() if v is not None})
        url = f"{self.server}/player_api.php"
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
        except requests.Timeout:
            raise ServerUnreachable(
                f"Timed out talking to {self.server}. The server may be down or slow."
            ) from None
        except requests.exceptions.SSLError as exc:
            raise ServerUnreachable(
                f"TLS/SSL error connecting to {self.server}: {self.redact(str(exc))}"
            ) from None
        except requests.ConnectionError:
            raise ServerUnreachable(
                f"Could not connect to {self.server}. Check the URL and port, "
                "and that you are online."
            ) from None
        except requests.RequestException as exc:
            raise XtreamError(self.redact(str(exc))) from None

        if resp.status_code in (401, 403):
            raise AuthFailed(
                f"Server refused the login (HTTP {resp.status_code}). "
                "Check the username and password."
            )
        if resp.status_code >= 400:
            raise BadResponse(f"Server returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            snippet = self.redact(resp.text[:200].strip())
            raise BadResponse(
                "Server did not return JSON. Is this an Xtream Codes server URL? "
                f"Response started with: {snippet!r}"
            ) from None

    # -- API calls ---------------------------------------------------------

    def account_info(self) -> AccountInfo:
        """Test the login and return account status. Raises AuthFailed on bad credentials."""
        data = self._get()
        # Bad logins come back as [], {}, or {"user_info": {"auth": 0}} depending on the panel.
        if not isinstance(data, dict) or not isinstance(data.get("user_info"), dict):
            raise AuthFailed("Login failed. Check the username and password.")
        if str(data["user_info"].get("auth", "1")) == "0":
            raise AuthFailed("Login failed. Check the username and password.")
        return AccountInfo.from_api(data)

    def live_categories(self) -> list[Category]:
        """Live categories in the provider's order."""
        data = self._get("get_live_categories")
        if not isinstance(data, list):
            raise BadResponse("Unexpected reply to get_live_categories")
        cats = (Category.from_api(d) for d in data if isinstance(d, dict))
        return [c for c in cats if c is not None]

    def live_streams(self, category_id: str | None = None) -> list[Channel]:
        """Live channels in the provider's order, optionally for one category only."""
        data = self._get("get_live_streams", timeout=STREAMS_TIMEOUT, category_id=category_id)
        if not isinstance(data, list):
            raise BadResponse("Unexpected reply to get_live_streams")
        chans = (Channel.from_api(d) for d in data if isinstance(d, dict))
        return [c for c in chans if c is not None]

    # -- URLs for the playlist ---------------------------------------------

    def stream_url(self, stream_id: int, ext: str = "ts") -> str:
        user = quote(self.username, safe="")
        pw = quote(self.password, safe="")
        return f"{self.server}/live/{user}/{pw}/{stream_id}.{ext}"

    def epg_url(self) -> str:
        query = urlencode({"username": self.username, "password": self.password})
        return f"{self.server}/xmltv.php?{query}"
