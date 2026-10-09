"""Command line front end for testing against a real account.

    python -m bonsai init-config
    python -m bonsai login
    python -m bonsai categories [--counts]
    python -m bonsai streams [--category ID] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime

from . import __version__
from .api import XtreamClient, XtreamError
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ConfigError, XtreamError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
