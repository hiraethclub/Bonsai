"""M3U playlists as a channel source (files or URLs, e.g. the iptv-org lists).

A playlist is read into the same Category / Channel objects the Xtream
source produces, so the tree, filters, profiles and export work unchanged.

- Each distinct group-title becomes a category. "Animation;Kids" puts the
  stream in both categories (iptv-org does this in index.m3u).
- A stream's id is derived from its URL and group, so profiles survive the
  list being reordered. The same URL in two groups gets two ids; export
  writes each URL once.
- Extra attributes and #EXTVLCOPT lines (needed by streams that require a
  particular User-Agent or Referer) are kept and written back on export.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import __version__
from .models import Category, Channel

# Attributes Bonsai writes itself; everything else is carried through as-is.
STANDARD_ATTRS = {"tvg-id", "tvg-name", "tvg-logo", "group-title"}
UNGROUPED = "Undefined"
MAX_DOWNLOAD = 64 * 1024 * 1024
DOWNLOAD_TIMEOUT = (10, 60)

_ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
_URL_START = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


class PlaylistError(Exception):
    pass


@dataclass
class ParsedEntry:
    name: str
    url: str
    attrs: dict[str, str] = field(default_factory=dict)
    options: list[str] = field(default_factory=list)


@dataclass
class ParsedPlaylist:
    header: dict[str, str]
    entries: list[ParsedEntry]


def _split_extinf(line: str) -> tuple[dict[str, str], str]:
    """'#EXTINF:-1 a="x" b="y, z",Title' -> ({a: x, b: 'y, z'}, 'Title')."""
    body = line.split(":", 1)[1] if ":" in line else ""
    # The title starts after the first comma that is not inside quotes.
    in_quotes = False
    cut = -1
    for i, ch in enumerate(body):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            cut = i
            break
    head, title = (body[:cut], body[cut + 1:]) if cut >= 0 else (body, "")
    attrs = {k.lower(): v for k, v in _ATTR.findall(head)}
    return attrs, title.strip()


def parse_m3u(text: str) -> ParsedPlaylist:
    text = text.lstrip("﻿")
    header: dict[str, str] = {}
    entries: list[ParsedEntry] = []
    attrs: dict[str, str] | None = None
    title = ""
    options: list[str] = []
    group_hint = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXTM3U"):
            header.update({k.lower(): v for k, v in _ATTR.findall(line)})
        elif upper.startswith("#EXTINF"):
            attrs, title = _split_extinf(line)
            options = []
            group_hint = ""
        elif upper.startswith("#EXTGRP:"):
            group_hint = line.split(":", 1)[1].strip()
        elif line.startswith("#"):
            # Player options for the next stream (#EXTVLCOPT, #KODIPROP, ...). Plain
            # comments and other directives are dropped.
            if upper.startswith(("#EXTVLCOPT", "#KODIPROP")):
                options.append(line)
        else:
            if attrs is None and not _URL_START.match(line):
                continue  # stray text (or an HTML page): only take a bare line if it is a URL
            entry_attrs = attrs or {}
            if group_hint and "group-title" not in entry_attrs:
                entry_attrs["group-title"] = group_hint
            name = title or entry_attrs.get("tvg-name") or _name_from_url(line)
            entries.append(ParsedEntry(name, line, entry_attrs, options))
            attrs, title, options, group_hint = None, "", [], ""
    return ParsedPlaylist(header, entries)


def _name_from_url(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or url


def stream_id_for(url: str, group: str) -> int:
    """Stable positive 62-bit id for a stream in a group."""
    digest = hashlib.blake2b(f"{url}\n{group}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") >> 2


def to_channels(parsed: ParsedPlaylist) -> tuple[list[Category], list[Channel]]:
    categories: dict[str, Category] = {}
    channels: list[Channel] = []
    seen: set[int] = set()
    for e in parsed.entries:
        groups = [g.strip() for g in e.attrs.get("group-title", "").split(";") if g.strip()] or [UNGROUPED]
        extra = tuple((k, v) for k, v in e.attrs.items() if k not in STANDARD_ATTRS)
        for group in groups:
            if group not in categories:
                categories[group] = Category(group, group)
            sid = stream_id_for(e.url, group)
            if sid in seen:  # exact duplicate line
                continue
            seen.add(sid)
            channels.append(
                Channel(
                    stream_id=sid,
                    name=e.name,
                    category_id=group,
                    icon=e.attrs.get("tvg-logo", ""),
                    epg_channel_id=e.attrs.get("tvg-id", ""),
                    url=e.url,
                    attrs=extra,
                    options=tuple(e.options),
                )
            )
    return list(categories.values()), channels


# -- reading -------------------------------------------------------------------


def is_url(location: str) -> bool:
    return location.strip().lower().startswith(("http://", "https://"))


def read_location(location: str, session: requests.Session | None = None) -> str:
    """Text of a playlist from a URL or a local file."""
    location = location.strip()
    if not location:
        raise PlaylistError("No playlist address given.")
    if is_url(location):
        return _download(location, session or requests.Session())
    path = Path(location).expanduser()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PlaylistError(f"Could not read {path}: {exc.strerror or exc}") from None
    return data.decode("utf-8", errors="replace")


def _download(url: str, session: requests.Session) -> str:
    try:
        resp = session.get(
            url, timeout=DOWNLOAD_TIMEOUT, stream=True, headers={"User-Agent": f"Bonsai/{__version__}"}
        )
    except requests.Timeout:
        raise PlaylistError(f"Timed out downloading {url}") from None
    except requests.RequestException as exc:
        raise PlaylistError(f"Could not download {url}: {type(exc).__name__}") from None
    with resp:
        if resp.status_code >= 400:
            raise PlaylistError(f"Downloading the playlist failed: HTTP {resp.status_code}")
        chunks, size = [], 0
        for chunk in resp.iter_content(1 << 16):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise PlaylistError("The playlist is larger than 64 MB; that does not look like an M3U file.")
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def load_playlist(location: str, session: requests.Session | None = None):
    """(categories, channels, header) for a playlist URL or file."""
    text = read_location(location, session)
    parsed = parse_m3u(text)
    if not parsed.entries:
        if "<html" in text[:2000].lower():
            raise PlaylistError("That address returned a web page, not an M3U playlist.")
        raise PlaylistError("No streams found in that playlist.")
    cats, chans = to_channels(parsed)
    return cats, chans, parsed.header


def display_name(location: str) -> str:
    """Short label for a playlist location: 'index.country.m3u' or the file name."""
    location = location.strip()
    if is_url(location):
        parts = urlsplit(location)
        tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
        return f"{tail or parts.netloc} ({parts.netloc})"
    return Path(location).name or location


# iptv-org presets offered in the window.
IPTV_ORG = [
    ("All channels", "https://iptv-org.github.io/iptv/index.m3u"),
    ("Grouped by category", "https://iptv-org.github.io/iptv/index.category.m3u"),
    ("Grouped by country", "https://iptv-org.github.io/iptv/index.country.m3u"),
    ("Grouped by language", "https://iptv-org.github.io/iptv/index.language.m3u"),
]
