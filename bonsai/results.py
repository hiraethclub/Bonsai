"""Stream test results, saved per channel list so tests don't have to be repeated.

Playlist streams are keyed by URL (so results survive the list being refreshed
or regrouped). Xtream channels are keyed by stream id: their URLs contain the
account password, which is never written to the cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .storage import cache_dir, write_private
from .streamtest import FAIL, OK, VITA_NO, TestResult

WORKING = "ok"
NOT_ON_VITA = "novita"
NOT_WORKING = "fail"
UNTESTED = "untested"
STATUS_CLASSES = (WORKING, NOT_ON_VITA, NOT_WORKING, UNTESTED)
STATUS_LABELS = {
    WORKING: "Working",
    NOT_ON_VITA: "Works, not on Vita",
    NOT_WORKING: "Not working",
    UNTESTED: "Untested",
}
STATUS_TOOLTIPS = {
    WORKING: "Passed a stream test.",
    NOT_ON_VITA: "Works, but VitaIPTV can't play it (found by a deep test, or by the quick test for "
                 "encrypted / fMP4 / DASH streams).",
    NOT_WORKING: "Failed a stream test. Left out of the export by default; use “Export not working…” "
                 "to save these to their own list.",
    UNTESTED: "Not tested yet.",
}
DEFAULT_HIDDEN_STATUS = frozenset({NOT_WORKING})


def status_class(result: TestResult | None) -> str:
    if result is None or result.status not in (OK, FAIL):
        return UNTESTED
    if result.status == FAIL:
        return NOT_WORKING
    return NOT_ON_VITA if result.vita == VITA_NO else WORKING


def result_key(channel, xtream: bool) -> str:
    return f"id:{channel.stream_id}" if xtream or not channel.url else channel.url


def results_path(list_key: str) -> Path:
    digest = hashlib.sha256(list_key.encode()).hexdigest()[:16]
    return cache_dir() / f"tests-{digest}.json"


def load_results(list_key: str) -> dict[str, TestResult]:
    try:
        data = json.loads(results_path(list_key).read_text(encoding="utf-8"))
        return {k: TestResult.from_json(v) for k, v in data.get("results", {}).items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_results(list_key: str, results: dict[str, TestResult]) -> None:
    data = {"version": 1, "results": {k: r.to_json() for k, r in results.items()}}
    write_private(results_path(list_key), json.dumps(data, ensure_ascii=False, separators=(",", ":")))
