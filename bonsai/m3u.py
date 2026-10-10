"""m3u_plus playlist writer."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .models import Category, Channel


@dataclass(frozen=True)
class PlaylistEntry:
    name: str
    url: str
    group: str = ""
    tvg_id: str = ""
    logo: str = ""
    attrs: tuple[tuple[str, str], ...] = ()  # extra #EXTINF attributes, e.g. http-user-agent
    options: tuple[str, ...] = ()  # lines written between #EXTINF and the URL (#EXTVLCOPT:...)
    comment: str = ""  # written as a "# ..." line before the entry (players ignore it)


def _one_line(text: str) -> str:
    """Line breaks or tabs inside a name would split the entry; turn them into spaces."""
    for ch in "\r\n\t":
        text = text.replace(ch, " ")
    return text.strip()


def _attr(text: str) -> str:
    """Value safe to put inside attr="...": no line breaks, no double quotes."""
    return _one_line(text).replace('"', "'")


_ATTR_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def render_m3u(entries: Iterable[PlaylistEntry], epg_url: str | None = None, newline: str = "\n") -> str:
    header = "#EXTM3U"
    if epg_url:
        header += f' x-tvg-url="{_attr(epg_url)}"'
    lines = [header]
    for e in entries:
        if e.comment:
            lines.append(f"# {_one_line(e.comment)}")
        extra = "".join(f' {k}="{_attr(v)}"' for k, v in e.attrs if _ATTR_NAME.match(k))
        lines.append(
            f'#EXTINF:-1 tvg-id="{_attr(e.tvg_id)}" tvg-name="{_attr(e.name)}" '
            f'tvg-logo="{_attr(e.logo)}"{extra} group-title="{_attr(e.group)}",{_one_line(e.name)}'
        )
        lines.extend(_one_line(o) for o in e.options if o.startswith("#"))
        lines.append(_one_line(e.url))
    return newline.join(lines) + newline


def write_m3u(
    path: str | os.PathLike,
    entries: Iterable[PlaylistEntry],
    epg_url: str | None = None,
    newline: str = "\n",
) -> Path:
    """Write UTF-8 (no BOM). Written to a temp file first so a failed export never leaves half a playlist."""
    path = Path(path)
    text = render_m3u(entries, epg_url, newline)
    fd, tmp = tempfile.mkstemp(prefix=".bonsai-", suffix=".m3u.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def build_entries(
    groups: Iterable[tuple[Category, list[Channel]]],
    selected: set[int] | frozenset[int],
    url_for: Callable[[Channel], str],
    comment_for: Callable[[Channel], str] | None = None,
) -> list[PlaylistEntry]:
    """Entries for the selected channels, keeping category and channel order from `groups`.

    A URL is written once even if it is ticked in several categories (playlist
    sources list a stream under each of its groups); the first one wins.
    """
    entries = []
    written: set[str] = set()
    for cat, channels in groups:
        for ch in channels:
            if ch.stream_id not in selected:
                continue
            url = url_for(ch)
            if url in written:
                continue
            written.add(url)
            entries.append(
                PlaylistEntry(
                    name=ch.name,
                    url=url,
                    group=cat.name,
                    tvg_id=ch.epg_channel_id,
                    logo=ch.icon,
                    attrs=ch.attrs,
                    options=ch.options,
                    comment=comment_for(ch) if comment_for else "",
                )
            )
    return entries
