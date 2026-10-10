"""Run blocking network calls on Qt's thread pool and report back on the UI thread."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

from ..api import XtreamError
from ..streamtest import run_tests


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], redact: Callable[[str], str]) -> None:
        super().__init__()
        self.fn = fn
        self.redact = redact
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except XtreamError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # anything unexpected still must not kill the UI
            self.signals.failed.emit(self.redact(f"Unexpected error: {type(exc).__name__}: {exc}"))
        else:
            self.signals.done.emit(result)


class TaskRunner(QObject):
    """Keeps tasks alive until they finish; callbacks run on the UI thread."""

    busyChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._running: set[_Task] = set()

    @property
    def busy(self) -> bool:
        return bool(self._running)

    def run(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_failed: Callable[[str], None],
        redact: Callable[[str], str] = lambda s: s,
    ) -> None:
        task = _Task(fn, redact)
        task.setAutoDelete(False)

        def finish(callback, value):
            self._running.discard(task)
            if not self._running:
                self.busyChanged.emit(False)
            callback(value)

        task.signals.done.connect(lambda v: finish(on_done, v))
        task.signals.failed.connect(lambda m: finish(on_failed, m))
        was_idle = not self._running
        self._running.add(task)
        if was_idle:
            self.busyChanged.emit(True)
        self._pool.start(task)


class TestThread(QThread):
    """Runs a batch of stream tests off the UI thread; results arrive via `result` as they finish."""

    result = Signal(object, object)  # TestJob, TestResult

    def __init__(self, jobs, test, workers: int, per_host: int, delay: float, parent=None) -> None:
        super().__init__(parent)
        self.jobs = jobs
        self.test = test
        self.workers = workers
        self.per_host = per_host
        self.delay = delay
        self.cancel_event = threading.Event()

    def run(self) -> None:
        run_tests(
            self.jobs, self.test, lambda job, res: self.result.emit(job, res), self.cancel_event,
            workers=self.workers, per_host=self.per_host, delay=self.delay,
        )

    def stop(self) -> None:
        self.cancel_event.set()
