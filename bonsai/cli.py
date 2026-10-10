"""Command line front end for testing against a real account.

    python -m bonsai init-config
    python -m bonsai login
    python -m bonsai categories [--counts]
    python -m bonsai streams [--category ID] [--limit N]
    python -m bonsai export -o FILE --category ID [--category ID ...] [--ext m3u8] [--epg]
    python -m bonsai profiles
    python -m bonsai regenerate NAME [-o FILE]
    python -m bonsai test PLAYLIST -o WORKING.m3u [--not-working DEAD.m3u] [--deep]

With no command, `python -m bonsai` opens the window instead.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime

from . import __version__
from . import profiles
from .api import XtreamClient, XtreamError
from .cache import playlist_key, save_lists, xtream_key
from .filters import tag_channels
from .playlist import PlaylistError, load_playlist
from .results import NOT_WORKING, load_results, result_key, save_results, status_class
from .sources import playlist_epg_url
from .streamtest import DEEP, QUICK, TestJob, deep_test, quick_test, request_for, run_tests
from .m3u import build_entries, write_m3u
from .models import group_channels
from .storage import ConfigError, config_path, ensure_config_template, load_config


def _fmt_date(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "never / unknown"


def _client() -> XtreamClient:
    cfg = load_config()
    if not cfg.is_complete:
        path, _ = ensure_config_template()
        raise ConfigError(
            f"No credentials yet. Edit {path} and fill in server, username and password."
        )
    try:
        return XtreamClient(cfg.server, cfg.username, cfg.password, cfg.user_agent)
    except ValueError as exc:
        raise ConfigError(f"{exc} (in {config_path()})") from None


def cmd_init_config(_args: argparse.Namespace) -> None:
    path, created = ensure_config_template()
    if created:
        print(f"Created {path}")
        print("Open it in a text editor and fill in server, username and password.")
    else:
        print(f"Config already exists: {path}")


def cmd_login(_args: argparse.Namespace) -> None:
    client = _client()
    info = client.account_info()
    print(f"Server:          {client.server}")
    print(f"Status:          {info.status}{'  (trial)' if info.is_trial else ''}")
    print(f"Expires:         {_fmt_date(info.expires)}")
    print(f"Connections:     {info.active_connections} active / {info.max_connections} max")
    print(f"Output formats:  {', '.join(info.allowed_output_formats) or 'not reported'}")
    if info.server_timezone:
        print(f"Server timezone: {info.server_timezone}")
    if not info.is_active:
        print("\nWarning: account is not active; streams will probably not play.")


def cmd_categories(args: argparse.Namespace) -> None:
    client = _client()
    cats = client.live_categories()
    counts: Counter[str] = Counter()
    if args.counts:
        print("Fetching all live streams to count them...", file=sys.stderr)
        counts.update(ch.category_id for ch in client.live_streams())
    width = max((len(c.id) for c in cats), default=2)
    for cat in cats:
        suffix = f"  ({counts[cat.id]})" if args.counts else ""
        print(f"{cat.id:>{width}}  {cat.name}{suffix}")
    total = f", {sum(counts.values())} channels" if args.counts else ""
    print(f"\n{len(cats)} live categories{total}")


def cmd_streams(args: argparse.Namespace) -> None:
    client = _client()
    chans = client.live_streams(args.category)
    shown = chans[: args.limit] if args.limit else chans
    for ch in shown:
        print(f"{ch.stream_id:>8}  [{ch.category_id}]  {ch.name}")
    more = f" (showing {len(shown)})" if len(shown) < len(chans) else ""
    print(f"\n{len(chans)} live channels{more}")


def cmd_export(args: argparse.Namespace) -> None:
    client = _client()
    groups = group_channels(client.live_categories(), client.live_streams())
    wanted = set(args.category)
    known = {cat.id for cat, _ in groups}
    missing = sorted(wanted - known)
    if missing:
        raise ConfigError(f"Unknown category id(s): {', '.join(missing)}. See `bonsai categories`.")
    selected = {ch.stream_id for cat, chans in groups if cat.id in wanted for ch in chans}
    entries = build_entries(groups, selected, lambda ch: client.stream_url(ch.stream_id, args.ext))
    write_m3u(args.output, entries, client.epg_url() if args.epg else None)
    print(f"Wrote {len(entries)} channels from {len(wanted)} categories to {args.output}")
    print("Note: the file contains your username and password in every URL.")


def cmd_profiles(_args: argparse.Namespace) -> None:
    names = profiles.list_names()
    if not names:
        print("No profiles yet. Save one from the window with \u201cSave as\u2026\u201d.")
        return
    for name in names:
        try:
            p = profiles.load(name)
        except profiles.ProfileError as exc:
            print(f"{name}: {exc}")
            continue
        target = p.export_path or "(no export path yet)"
        print(f"{name} [{p.source.label()}]: {len(p.channels)} channels, new channels: {p.new_channels}, "
              f"exports to {target}")


def cmd_regenerate(args: argparse.Namespace) -> None:
    """Refresh from the server, apply a profile and write its playlist: the Regenerate button."""
    try:
        profile = profiles.load(args.name)
    except profiles.ProfileError as exc:
        raise ConfigError(str(exc)) from None
    output = args.output or profile.export_path
    if not output:
        raise ConfigError(f"Profile \u201c{profile.name}\u201d has no export path yet; pass -o FILE.")
    if profile.source.is_playlist:
        location = profile.source.location
        list_key = playlist_key(location)
        try:
            cats, chans, header = load_playlist(location)
        except PlaylistError as exc:
            raise ConfigError(str(exc)) from None
        try:
            save_lists(playlist_key(location), cats, chans, header)
        except OSError:
            pass
        url_for = lambda ch: ch.url  # noqa: E731
        epg = playlist_epg_url(header) if profile.include_epg else None
    else:
        client = _client()
        cats, chans = client.live_categories(), client.live_streams()
        try:
            save_lists(xtream_key(client.server, client.username), cats, chans)
        except OSError:
            pass  # the cache is only a convenience
        url_for = lambda ch: client.stream_url(ch.stream_id, profile.stream_format)  # noqa: E731
        epg = client.epg_url() if profile.include_epg else None
        list_key = xtream_key(client.server, client.username)
    groups = group_channels(cats, chans)
    result = profiles.apply(profile, groups)
    tags = tag_channels(groups)
    tested = load_results(list_key)
    xtream = not profile.source.is_playlist
    status = {ch.stream_id: status_class(tested.get(result_key(ch, xtream))) for _, cs in groups for ch in cs}
    exportable = {sid for sid in result.ticked if profile.filters.allows(tags.get(sid), status.get(sid, "untested"))}
    entries = build_entries(groups, exportable, url_for)
    write_m3u(output, entries, epg or None)
    if output != profile.export_path:
        profile.export_path = output
        profiles.save(profile)

    print(f"Wrote {len(entries)} channels to {output}")
    hidden = len(result.ticked) - len(exportable)
    if hidden:
        print(f"  {hidden} ticked channels left out by the profile's filters")
    if result.rematched:
        print(f"  {result.rematched} channels found again by name (stream ids changed)")
    if result.auto_ticked:
        print(f"  {len(result.auto_ticked)} new channels included automatically")
    elif result.new:
        print(f"  {len(result.new)} new channels in your categories (not included; open the app to review)")
    if result.unmatched:
        print(f"  {len(result.unmatched)} saved channels no longer found:")
        for c in result.unmatched[:20]:
            print(f"    {c.name}  ({c.category})")
        if len(result.unmatched) > 20:
            print(f"    ...and {len(result.unmatched) - 20} more")


def cmd_test(args: argparse.Namespace) -> None:
    """Test every stream in a playlist and write working / not working lists."""
    from threading import Event, Lock

    try:
        cats, chans, header = load_playlist(args.playlist)
    except PlaylistError as exc:
        raise ConfigError(str(exc)) from None
    groups = group_channels(cats, chans)
    seen: dict[str, int] = {}
    jobs = []
    for _, cs in groups:
        for ch in cs:
            if ch.url not in seen:
                seen[ch.url] = ch.stream_id
                jobs.append(TestJob(ch.stream_id, request_for(ch)))
    mode = DEEP if args.deep else QUICK
    test = (lambda r, s: deep_test(r, s)) if args.deep else (lambda r, s: quick_test(r, s))
    print(f"{mode.capitalize()}-testing {len(jobs)} streams, {args.workers} at a time…", file=sys.stderr)
    results = {}
    lock = Lock()
    done = [0]

    def on_result(job, res):
        with lock:
            results[job.request.url] = res
            done[0] += 1
            if done[0] % 100 == 0 or done[0] == len(jobs):
                print(f"  {done[0]}/{len(jobs)}", file=sys.stderr)

    cancel = Event()
    try:
        run_tests(jobs, test, on_result, cancel, workers=args.workers, per_host=2)
    except KeyboardInterrupt:
        cancel.set()
        print("Stopped; writing what was tested so far.", file=sys.stderr)
    key = playlist_key(args.playlist)
    stored = load_results(key)
    stored.update(results)
    try:
        save_results(key, stored)
    except OSError:
        pass
    url_for = lambda ch: ch.url  # noqa: E731
    working = {ch.stream_id for _, cs in groups for ch in cs
               if ch.url in results and status_class(results[ch.url]) != NOT_WORKING}
    dead = {ch.stream_id for _, cs in groups for ch in cs
            if ch.url in results and status_class(results[ch.url]) == NOT_WORKING}
    epg = playlist_epg_url(header) or None
    write_m3u(args.output, build_entries(groups, working, url_for), epg)
    n_ok = len({ch.url for _, cs in groups for ch in cs if ch.stream_id in working})
    n_dead = len({ch.url for _, cs in groups for ch in cs if ch.stream_id in dead})
    print(f"Working: {n_ok} streams -> {args.output}")
    if args.not_working:
        comment = lambda ch: f"Not working ({results[ch.url].at[:10]}): {results[ch.url].reason}"  # noqa: E731
        write_m3u(args.not_working, build_entries(groups, dead, url_for, comment))
        print(f"Not working: {n_dead} streams -> {args.not_working}")
    else:
        print(f"Not working: {n_dead} streams (pass --not-working FILE to save them)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bonsai", description="Bonsai: Xtream live TV playlist trimmer")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-config", help="create an empty config file to fill in").set_defaults(
        func=cmd_init_config
    )
    sub.add_parser("login", help="test the login and show account status").set_defaults(
        func=cmd_login
    )

    pc = sub.add_parser("categories", help="list live categories")
    pc.add_argument("--counts", action="store_true", help="also fetch streams and count per category")
    pc.set_defaults(func=cmd_categories)

    ps = sub.add_parser("streams", help="list live channels")
    ps.add_argument("--category", metavar="ID", help="only this category id")
    ps.add_argument("--limit", type=int, default=50, help="max rows to print (0 = all, default 50)")
    ps.set_defaults(func=cmd_streams)

    pe = sub.add_parser("export", help="write a playlist of whole categories")
    pe.add_argument("-o", "--output", required=True, metavar="FILE", help="playlist file to write")
    pe.add_argument("--category", action="append", required=True, metavar="ID",
                    help="category id to include (repeat for more)")
    pe.add_argument("--ext", choices=("ts", "m3u8"), default="ts", help="stream format (default ts)")
    pe.add_argument("--epg", action="store_true", help="add x-tvg-url to the header")
    pe.set_defaults(func=cmd_export)

    sub.add_parser("profiles", help="list saved profiles").set_defaults(func=cmd_profiles)

    pr = sub.add_parser("regenerate", help="refresh, apply a profile and export its playlist")
    pr.add_argument("name", help="profile name")
    pr.add_argument("-o", "--output", metavar="FILE", help="write here instead of the profile's path")
    pr.set_defaults(func=cmd_regenerate)

    pt = sub.add_parser("test", help="test every stream in an M3U playlist (not for Xtream accounts)")
    pt.add_argument("playlist", help="playlist URL or file")
    pt.add_argument("-o", "--output", required=True, metavar="FILE", help="write the working streams here")
    pt.add_argument("--not-working", metavar="FILE", help="write the failed streams here, with the reason")
    pt.add_argument("--deep", action="store_true", help="deep test (ffprobe, Vita verdict); much slower")
    pt.add_argument("--workers", type=int, default=8, help="streams tested at once (default 8)")
    pt.set_defaults(func=cmd_test)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ConfigError, XtreamError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
