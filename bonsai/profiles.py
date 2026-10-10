"""Named selections ("Vita") saved as JSON in the config folder.

A profile stores each ticked channel by stream_id, with its channel and
category names as a fallback in case the provider renumbers streams. It also
remembers, for every category that had something ticked, which channels
existed at save time, so channels added later can be spotted as new.

New channels are only ever ticked automatically in categories that were
*fully* ticked when the profile was saved (and only when the profile asks
for it). In partly ticked categories they are just highlighted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .filters import FilterState
from .models import Category, Channel
from .quality import normalise_name
from .sources import Source
from .storage import config_dir, write_private

PROFILE_VERSION = 1
NEW_HIGHLIGHT = "highlight"
NEW_INCLUDE = "include"


class ProfileError(Exception):
    pass


@dataclass
class SavedChannel:
    stream_id: int
    name: str
    category: str


@dataclass
class SavedCategory:
    id: str
    name: str
    full: bool  # every shown channel was ticked when saved
    known: list[int]  # all stream_ids in the category when saved


@dataclass
class Profile:
    name: str
    channels: list[SavedChannel] = field(default_factory=list)
    categories: list[SavedCategory] = field(default_factory=list)
    new_channels: str = NEW_HIGHLIGHT
    export_path: str = ""
    stream_format: str = "ts"
    include_epg: bool = False
    filters: FilterState = field(default_factory=FilterState)
    saved_at: str = ""
    source: Source = field(default_factory=Source)

    def to_json(self) -> dict:
        return {
            "version": PROFILE_VERSION,
            "name": self.name,
            "saved_at": self.saved_at,
            "new_channels": self.new_channels,
            "export_path": self.export_path,
            "stream_format": self.stream_format,
            "include_epg": self.include_epg,
            "filters": self.filters.to_json(),
            "source": self.source.to_json(),
            "channels": [[c.stream_id, c.name, c.category] for c in self.channels],
            "categories": [
                {"id": c.id, "name": c.name, "full": c.full, "known": c.known} for c in self.categories
            ],
        }

    @classmethod
    def from_json(cls, data: dict) -> Profile:
        try:
            return cls(
                name=str(data["name"]),
                channels=[SavedChannel(int(i), str(n), str(c)) for i, n, c in data.get("channels", [])],
                categories=[
                    SavedCategory(str(c["id"]), str(c["name"]), bool(c.get("full")), [int(k) for k in c.get("known", [])])
                    for c in data.get("categories", [])
                ],
                new_channels=NEW_INCLUDE if data.get("new_channels") == NEW_INCLUDE else NEW_HIGHLIGHT,
                export_path=str(data.get("export_path") or ""),
                stream_format="m3u8" if data.get("stream_format") == "m3u8" else "ts",
                include_epg=bool(data.get("include_epg")),
                filters=FilterState.from_json(data.get("filters")),
                saved_at=str(data.get("saved_at") or ""),
                source=Source.from_json(data.get("source")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileError(f"Profile file is damaged: {exc}") from None


# -- building and applying -----------------------------------------------------


def _key(text: str) -> str:
    return " ".join(normalise_name(text).casefold().split())


def snapshot(
    name: str,
    groups: list[tuple[Category, list[Channel]]],
    ticked: set[int] | frozenset[int],
    full_category_ids: set[str],
    **options,
) -> Profile:
    """A profile from the current ticks. `options` are the other Profile fields."""
    channels: list[SavedChannel] = []
    categories: list[SavedCategory] = []
    for cat, chans in groups:
        picked = [ch for ch in chans if ch.stream_id in ticked]
        if not picked:
            continue
        channels.extend(SavedChannel(ch.stream_id, ch.name, cat.name) for ch in picked)
        categories.append(
            SavedCategory(cat.id, cat.name, cat.id in full_category_ids, [ch.stream_id for ch in chans])
        )
    return Profile(
        name=name,
        channels=channels,
        categories=categories,
        saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **options,
    )


@dataclass
class ApplyResult:
    ticked: set[int] = field(default_factory=set)
    unmatched: list[SavedChannel] = field(default_factory=list)
    rematched: int = 0  # found by name because the stream_id had gone
    new: set[int] = field(default_factory=set)  # in a profile category, not there when saved
    auto_ticked: set[int] = field(default_factory=set)  # new channels ticked because of NEW_INCLUDE


def apply(profile: Profile, groups: list[tuple[Category, list[Channel]]]) -> ApplyResult:
    result = ApplyResult()
    by_id: dict[int, Channel] = {}
    by_cat_and_name: dict[tuple[str, str], int] = {}
    by_name: dict[str, list[int]] = {}
    cats_by_id: dict[str, tuple[Category, list[Channel]]] = {}
    cats_by_name: dict[str, tuple[Category, list[Channel]]] = {}
    for cat, chans in groups:
        cats_by_id[cat.id] = (cat, chans)
        cats_by_name.setdefault(_key(cat.name), (cat, chans))
        ck = _key(cat.name)
        for ch in chans:
            by_id[ch.stream_id] = ch
            nk = _key(ch.name)
            by_cat_and_name.setdefault((ck, nk), ch.stream_id)
            by_name.setdefault(nk, []).append(ch.stream_id)

    rematched_ids: set[int] = set()
    for saved in profile.channels:
        if saved.stream_id in by_id:
            result.ticked.add(saved.stream_id)
            continue
        sid = by_cat_and_name.get((_key(saved.category), _key(saved.name)))
        if sid is None:
            candidates = by_name.get(_key(saved.name), [])
            sid = candidates[0] if len(candidates) == 1 else None
        if sid is None:
            result.unmatched.append(saved)
        else:
            result.ticked.add(sid)
            rematched_ids.add(sid)
            result.rematched += 1

    for saved_cat in profile.categories:
        found = cats_by_id.get(saved_cat.id) or cats_by_name.get(_key(saved_cat.name))
        if not found:
            continue
        _, chans = found
        known = set(saved_cat.known)
        current = {ch.stream_id for ch in chans}
        # If none of the old ids survive, the provider renumbered everything:
        # every channel would look new, which is useless, so skip this category.
        if not (known & current):
            continue
        # A saved channel that came back under a new id is not "new".
        new = current - known - rematched_ids
        result.new |= new
        if saved_cat.full and profile.new_channels == NEW_INCLUDE:
            result.auto_ticked |= new

    result.ticked |= result.auto_ticked
    return result


# -- files -----------------------------------------------------------------------


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._")
    return slug[:80] or "profile"


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{_slug(name)}.json"


def validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ProfileError("Profile name is empty.")
    if len(name) > 80:
        raise ProfileError("Profile name is too long (80 characters max).")
    path = profile_path(name)
    if path.exists():
        try:
            existing = load(name)
        except ProfileError:
            existing = None
        if existing is not None and existing.name != name:
            raise ProfileError(f"That name clashes with the existing profile “{existing.name}”.")
    return name


def list_names() -> list[str]:
    names = []
    for path in sorted(profiles_dir().glob("*.json")):
        try:
            names.append(str(json.loads(path.read_text(encoding="utf-8"))["name"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(names, key=str.casefold)


def load(name: str) -> Profile:
    path = profile_path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"No profile called “{name}”.") from None
    except (OSError, ValueError) as exc:
        raise ProfileError(f"Could not read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ProfileError(f"{path} is not a profile.")
    return Profile.from_json(data)


def save(profile: Profile) -> Path:
    path = profile_path(profile.name)
    write_private(path, json.dumps(profile.to_json(), ensure_ascii=False, indent=1) + "\n")
    return path


def delete(name: str) -> None:
    profile_path(name).unlink(missing_ok=True)
