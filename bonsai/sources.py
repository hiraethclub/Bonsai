"""Where a channel list comes from: the Xtream account, or an M3U playlist (URL or file)."""

from __future__ import annotations

from dataclasses import dataclass

from .playlist import display_name, is_url

XTREAM = "xtream"
PLAYLIST = "m3u"


@dataclass(frozen=True)
class Source:
    kind: str = XTREAM
    location: str = ""  # playlist URL or file path; unused for Xtream (the account is in config.json)

    @property
    def is_playlist(self) -> bool:
        return self.kind == PLAYLIST

    @property
    def is_remote(self) -> bool:
        return self.is_playlist and is_url(self.location)

    def label(self) -> str:
        return display_name(self.location) if self.is_playlist else "Xtream account"

    def to_json(self) -> dict:
        return {"kind": PLAYLIST, "location": self.location} if self.is_playlist else {"kind": XTREAM}

    @classmethod
    def from_json(cls, data: dict | None) -> Source:
        data = data or {}
        if data.get("kind") == PLAYLIST and str(data.get("location") or "").strip():
            return cls(PLAYLIST, str(data["location"]).strip())
        return cls()


def playlist_epg_url(header: dict) -> str:
    """The guide address a playlist advertises in its #EXTM3U line, if any."""
    return header.get("x-tvg-url") or header.get("url-tvg") or ""
