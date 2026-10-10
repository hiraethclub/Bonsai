"""On-disk cache of category and channel lists, so the app opens instantly.

One gzip'd JSON file per source (an Xtream account or a playlist URL). The
file name is a hash, so the cache folder does not reveal which account or
playlist it belongs to. For Xtream accounts no credentials or stream URLs
are stored; for playlists the stream URLs are part of the list itself.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import Category, Channel
from .storage import cache_dir, write_private

CACHE_VERSION = 2
READABLE_VERSIONS = (1, 2)  # version 1 rows simply lack the playlist fields


@dataclass
class CachedLists:
    categories: list[Category]
    channels: list[Channel]
    fetched_at: datetime
    header: dict = field(default_factory=dict)  # playlist #EXTM3U attributes (x-tvg-url, ...)


def xtream_key(server: str, username: str) -> str:
    return f"{server}\n{username}"


def playlist_key(location: str) -> str:
    return f"m3u\n{location.strip()}"


def cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return cache_dir() / f"live-{digest}.json.gz"


def _channel_row(ch: Channel) -> list:
    added = int(ch.added.timestamp()) if ch.added else None
    row = [ch.stream_id, ch.name, ch.category_id, ch.num, ch.icon, ch.epg_channel_id, added, ch.is_adult]
    if ch.url or ch.attrs or ch.options:
        row += [ch.url, [list(a) for a in ch.attrs], list(ch.options)]
    return row


def _channel_from_row(row: list) -> Channel:
    sid, name, cat, num, icon, epg, added, adult = row[:8]
    url, attrs, options = row[8:11] if len(row) >= 11 else ("", [], [])
    return Channel(
        stream_id=int(sid),
        name=name,
        category_id=cat,
        num=num,
        icon=icon,
        epg_channel_id=epg,
        added=datetime.fromtimestamp(added, tz=timezone.utc) if added else None,
        is_adult=bool(adult),
        url=url or "",
        attrs=tuple((str(k), str(v)) for k, v in attrs),
        options=tuple(str(o) for o in options),
    )


def save_lists(
    key: str, categories: list[Category], channels: list[Channel], header: dict | None = None
) -> CachedLists:
    fetched_at = datetime.now(timezone.utc)
    data = {
        "version": CACHE_VERSION,
        "fetched_at": fetched_at.isoformat(),
        "header": header or {},
        "categories": [[c.id, c.name] for c in categories],
        "channels": [_channel_row(ch) for ch in channels],
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    write_private(cache_path(key), gzip.compress(raw, compresslevel=5))
    return CachedLists(categories, channels, fetched_at, dict(header or {}))


def load_lists(key: str) -> CachedLists | None:
    """The cached lists, or None if there is no usable cache for this source."""
    path = cache_path(key)
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
        if data.get("version") not in READABLE_VERSIONS:
            return None
        return CachedLists(
            categories=[Category(str(i), n) for i, n in data["categories"]],
            channels=[_channel_from_row(r) for r in data["channels"]],
            fetched_at=datetime.fromisoformat(data["fetched_at"]),
            header=dict(data.get("header") or {}),
        )
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None
