"""Plain data types built from Xtream Player API responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _to_int(value: Any) -> int | None:
    """Xtream panels return numbers as ints, strings, or null. Normalise to int or None."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _to_datetime(value: Any) -> datetime | None:
    ts = _to_int(value)
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


@dataclass(frozen=True)
class Category:
    id: str
    name: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Category | None:
        cat_id = _to_str(data.get("category_id"))
        if not cat_id:
            return None
        return cls(id=cat_id, name=_to_str(data.get("category_name")) or f"Category {cat_id}")


@dataclass(frozen=True)
class Channel:
    stream_id: int
    name: str
    category_id: str
    num: int | None = None
    icon: str = ""
    epg_channel_id: str = ""
    added: datetime | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Channel | None:
        stream_id = _to_int(data.get("stream_id"))
        if stream_id is None:
            return None
        category_id = _to_str(data.get("category_id"))
        if not category_id:
            # Some newer panels send a list instead of (or as well as) a single id.
            ids = data.get("category_ids") or []
            if isinstance(ids, list) and ids:
                category_id = _to_str(ids[0])
        return cls(
            stream_id=stream_id,
            name=_to_str(data.get("name")) or f"Stream {stream_id}",
            category_id=category_id,
            num=_to_int(data.get("num")),
            icon=_to_str(data.get("stream_icon")),
            epg_channel_id=_to_str(data.get("epg_channel_id")),
            added=_to_datetime(data.get("added")),
        )


UNCATEGORISED = Category(id="", name="Uncategorised")


def is_divider(channel: Channel) -> bool:
    """Providers insert fake "channels" like '####### SOCCER PPV #######' as section headings."""
    return channel.name.lstrip().startswith("###")


def group_channels(
    categories: list[Category], channels: list[Channel]
) -> list[tuple[Category, list[Channel]]]:
    """Pair each category with its channels, both in the provider's order.

    Channels whose category id is unknown go into a trailing "Uncategorised"
    group. Categories with no channels are kept (with an empty list).
    Divider rows ('### SPORTS ###') are dropped: they are not real streams.
    """
    buckets: dict[str, list[Channel]] = {c.id: [] for c in categories}
    orphans: list[Channel] = []
    for ch in channels:
        if is_divider(ch):
            continue
        bucket = buckets.get(ch.category_id)
        (orphans if bucket is None else bucket).append(ch)
    groups = [(c, buckets[c.id]) for c in categories]
    if orphans:
        groups.append((UNCATEGORISED, orphans))
    return groups


@dataclass(frozen=True)
class AccountInfo:
    username: str
    status: str
    expires: datetime | None
    is_trial: bool
    active_connections: int | None
    max_connections: int | None
    created: datetime | None
    allowed_output_formats: list[str] = field(default_factory=list)
    server_timezone: str = ""

    @property
    def is_active(self) -> bool:
        return self.status.lower() == "active"

    @property
    def supports_m3u8(self) -> bool:
        return "m3u8" in self.allowed_output_formats

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> AccountInfo:
        user = data.get("user_info") or {}
        server = data.get("server_info") or {}
        formats = user.get("allowed_output_formats") or []
        if not isinstance(formats, list):
            formats = []
        return cls(
            username=_to_str(user.get("username")),
            status=_to_str(user.get("status")) or "Unknown",
            expires=_to_datetime(user.get("exp_date")),
            is_trial=_to_str(user.get("is_trial")) in ("1", "true", "True"),
            active_connections=_to_int(user.get("active_cons")),
            max_connections=_to_int(user.get("max_connections")),
            created=_to_datetime(user.get("created_at")),
            allowed_output_formats=[_to_str(f).lower() for f in formats if _to_str(f)],
            server_timezone=_to_str(server.get("timezone")),
        )
