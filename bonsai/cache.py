"""On-disk cache of the live category and channel lists, so the app opens instantly.

One gzip'd JSON file per account. The file name is a hash of server and
username so the cache folder does not reveal which account it belongs to.
No credentials or stream URLs are stored.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import Category, Channel
from .storage import cache_dir, write_private

CACHE_VERSION = 1


@dataclass
class CachedLists:
    categories: list[Category]
    channels: list[Channel]
    fetched_at: datetime


def cache_path(server: str, username: str) -> Path:
    digest = hashlib.sha256(f"{server}\n{username}".encode()).hexdigest()[:16]
    return cache_dir() / f"live-{digest}.json.gz"


def _channel_row(ch: Channel) -> list:
    added = int(ch.added.timestamp()) if ch.added else None
    return [ch.stream_id, ch.name, ch.category_id, ch.num, ch.icon, ch.epg_channel_id, added, ch.is_adult]


def _channel_from_row(row: list) -> Channel:
    sid, name, cat, num, icon, epg, added, adult = row
    return Channel(
        stream_id=int(sid),
        name=name,
        category_id=cat,
        num=num,
        icon=icon,
        epg_channel_id=epg,
        added=datetime.fromtimestamp(added, tz=timezone.utc) if added else None,
        is_adult=bool(adult),
    )


def save_lists(server: str, username: str, categories: list[Category], channels: list[Channel]) -> CachedLists:
    fetched_at = datetime.now(timezone.utc)
    data = {
        "version": CACHE_VERSION,
        "fetched_at": fetched_at.isoformat(),
        "categories": [[c.id, c.name] for c in categories],
        "channels": [_channel_row(ch) for ch in channels],
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    write_private(cache_path(server, username), gzip.compress(raw, compresslevel=5))
    return CachedLists(categories, channels, fetched_at)


def load_lists(server: str, username: str) -> CachedLists | None:
    """The cached lists, or None if there is no usable cache for this account."""
    path = cache_path(server, username)
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
        if data.get("version") != CACHE_VERSION:
            return None
        return CachedLists(
            categories=[Category(str(i), n) for i, n in data["categories"]],
            channels=[_channel_from_row(r) for r in data["channels"]],
            fetched_at=datetime.fromisoformat(data["fetched_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None
