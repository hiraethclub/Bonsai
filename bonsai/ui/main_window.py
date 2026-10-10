"""Bonsai main window."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, profiles
from ..api import XtreamClient
from ..cache import CachedLists, load_lists, playlist_key, save_lists, xtream_key
from ..filters import FilterState, parse_flags
from ..m3u import build_entries, write_m3u
from ..models import AccountInfo, group_channels
from ..playlist import IPTV_ORG, is_url, load_playlist
from ..profiles import NEW_HIGHLIGHT, NEW_INCLUDE, ApplyResult, Profile, ProfileError
from ..quality import parse_tiers
from ..results import NOT_WORKING, load_results, result_key, save_results
from ..streamtest import (
    DEEP,
    FFPROBE_HELP,
    QUICK,
    TestJob,
    TestResult,
    deep_test,
    estimate_seconds,
    find_ffprobe,
    quick_test,
    request_for,
)
from ..sources import PLAYLIST, Source, playlist_epg_url
from ..storage import (
    Config,
    ConfigError,
    load_config,
    load_settings,
    save_config,
    save_settings,
)
from .about import show_about
from .channel_tree import COL_ID, COL_LABELS, COL_NAME, COL_STATUS, COL_TICKED, COL_TIER, ChannelTreeModel
from .filter_panel import FilterPanel
from .workers import TaskRunner, TestThread

# Expanding every category is cheap for a search hit list but slow for the whole catalogue.
AUTO_EXPAND_LIMIT = 2000
# After loading a profile, expand categories with new channels if there are only a few.
EXPAND_NEW_LIMIT = 15
NO_PROFILE = "(no profile)"
UNMATCHED_SHOWN = 25


def _fmt_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{max(5, round(seconds / 5) * 5):.0f} seconds"
    minutes = round(seconds / 60)
    return f"{minutes} minutes" if minutes < 120 else f"{minutes / 60:.1f} hours"


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone().strftime("%d %b %Y %H:%M")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1000, 860)

        self.client: XtreamClient | None = None
        self.account: AccountInfo | None = None
        self.source: Source | None = None  # what the tree shows
        self.loaded_key = ""  # cache key of the list in the tree (identifies account / playlist)
        self.playlist_header: dict = {}  # #EXTM3U attributes of a loaded playlist
        self._last_export: tuple[str, str] = ("", "")  # (list key, path) of the last export this session
        self.results: dict[str, TestResult] = {}  # test results of the list in the tree, by result_key
        self.tester: TestThread | None = None
        self._test_progress = (0, 0, 0)  # done, total, failed
        self.profile: Profile | None = None
        self._clean_state = None  # snapshot of the profile as loaded/saved, for "unsaved changes"
        self._pending_profile = ""  # profile to apply once the channel list is available
        self.tasks = TaskRunner(self)
        self.tasks.busyChanged.connect(self._on_busy)

        try:
            self.config = load_config()
            config_problem = ""
        except ConfigError as exc:
            self.config = Config()
            config_problem = str(exc)
        self.settings = load_settings()

        self._build_ui()
        self._load_fields()
        self._update_title()

        if config_problem:
            self._set_status(config_problem, "error")
        elif self.config.is_complete:
            QTimer.singleShot(0, self._startup)
        else:
            self._set_status("Enter your server, username and password, then Test login.")

    # -- layout ------------------------------------------------------------

    def _build_ui(self) -> None:
        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About Bonsai", self)
        about_action.triggered.connect(lambda: show_about(self))
        help_menu.addAction(about_action)

        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        # Source: Xtream account or M3U playlist
        source_box = QGroupBox("Source")
        source_layout = QVBoxLayout(source_box)
        self.source_tabs = QTabWidget()
        source_layout.addWidget(self.source_tabs)
        xtream_tab = QWidget()
        form = QFormLayout(xtream_tab)
        self.server_edit = QLineEdit(placeholderText="http://provider.example:8080")
        self.user_edit = QLineEdit()
        self.pass_edit = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.show_pass = QCheckBox("Show")
        self.show_pass.toggled.connect(
            lambda on: self.pass_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        pass_row = QHBoxLayout()
        pass_row.addWidget(self.pass_edit, 1)
        pass_row.addWidget(self.show_pass)
        form.addRow("Server", self.server_edit)
        form.addRow("Username", self.user_edit)
        form.addRow("Password", pass_row)

        self.login_btn = QPushButton("Test login")
        self.login_btn.clicked.connect(self.test_login)
        self.status_label = QLabel(wordWrap=True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.login_btn)
        btn_row.addWidget(self.status_label, 1)
        form.addRow(btn_row)
        for edit in (self.server_edit, self.user_edit, self.pass_edit):
            edit.returnPressed.connect(self.test_login)
        self.source_tabs.addTab(xtream_tab, "Xtream account")

        playlist_tab = QWidget()
        pform = QFormLayout(playlist_tab)
        self.playlist_edit = QLineEdit(placeholderText="https://… .m3u link, or a file on this computer")
        self.playlist_edit.returnPressed.connect(self.load_playlist_from_field)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_playlist)
        presets_btn = QToolButton(text="iptv-org")
        presets_btn.setToolTip("Lists from github.com/iptv-org/iptv (publicly available channels)")
        presets_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        presets_menu = QMenu(presets_btn)
        for label, url in IPTV_ORG:
            presets_menu.addAction(label, lambda u=url: self._use_preset(u))
        presets_btn.setMenu(presets_menu)
        loc_row = QHBoxLayout()
        loc_row.addWidget(self.playlist_edit, 1)
        loc_row.addWidget(browse_btn)
        loc_row.addWidget(presets_btn)
        pform.addRow("Playlist", loc_row)
        self.playlist_load_btn = QPushButton("Load playlist")
        self.playlist_load_btn.clicked.connect(self.load_playlist_from_field)
        self.playlist_status = QLabel(wordWrap=True)
        prow2 = QHBoxLayout()
        prow2.addWidget(self.playlist_load_btn)
        prow2.addWidget(self.playlist_status, 1)
        pform.addRow(prow2)
        self.source_tabs.addTab(playlist_tab, "M3U playlist")

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Download the list shown below again.")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(lambda: self.refresh_source())
        self.cache_label = QLabel("No channel list yet")
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.refresh_btn)
        cache_row.addWidget(self.cache_label, 1)
        source_layout.addLayout(cache_row)
        root.addWidget(source_box)

        # Profile bar
        profile_box = QGroupBox("Profile")
        prow = QHBoxLayout(profile_box)
        self.profile_combo = QComboBox(minimumWidth=180)
        self.profile_combo.activated.connect(self._profile_chosen)
        self.save_btn = QPushButton("Save")
        self.save_btn.setToolTip("Save ticks, filters and export options to this profile.\n"
                                 "Saving also marks highlighted new channels as seen.")
        self.save_btn.clicked.connect(self.save_profile)
        save_as_btn = QPushButton("Save as…")
        save_as_btn.clicked.connect(self.save_profile_as)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_profile)
        self.new_mode = QComboBox()
        self.new_mode.addItem("Highlight only", NEW_HIGHLIGHT)
        self.new_mode.addItem("Tick automatically", NEW_INCLUDE)
        self.new_mode.setToolTip(
            "What to do with channels that appear later in categories that were fully ticked\n"
            "when the profile was saved. New channels in partly ticked categories are always\n"
            "just highlighted."
        )
        self.new_mode.currentIndexChanged.connect(self._update_dirty)
        self.regen_btn = QPushButton("Regenerate")
        self.regen_btn.setToolTip("Refresh from the server, apply this profile and export to its playlist path.")
        self.regen_btn.clicked.connect(self.regenerate)
        prow.addWidget(self.profile_combo)
        prow.addWidget(self.save_btn)
        prow.addWidget(save_as_btn)
        prow.addWidget(self.delete_btn)
        prow.addSpacing(16)
        prow.addWidget(QLabel("New channels:"))
        prow.addWidget(self.new_mode)
        prow.addStretch(1)
        prow.addWidget(self.regen_btn)
        root.addWidget(profile_box)

        # Search row
        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit(placeholderText="Filter by channel or category name")
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self, singleShot=True, interval=250)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.filter_edit.textChanged.connect(lambda _: self._filter_timer.start())
        expand_btn = QPushButton("Expand all")
        collapse_btn = QPushButton("Collapse all")
        clear_btn = QPushButton("Untick all")
        self.test_btn = QToolButton(text="Test streams")
        self.test_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.test_btn.setToolTip("Check which streams work (quick test) or will play on the Vita (deep test).")
        test_menu = QMenu(self.test_btn)
        for title, mode in (("Quick test (does it work?)", QUICK), ("Deep test (will it play on the Vita?)", DEEP)):
            sub = test_menu.addMenu(title)
            sub.addAction("Ticked channels", lambda m=mode: self.start_test(m, "ticked"))
            sub.addAction("Channels shown in the list", lambda m=mode: self.start_test(m, "shown"))
            sub.addAction("Selected rows", lambda m=mode: self.start_test(m, "selected"))
        test_menu.addSeparator()
        test_menu.addAction("Clear test results for this list", self.clear_results)
        self.test_btn.setMenu(test_menu)
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(expand_btn)
        filter_row.addWidget(collapse_btn)
        filter_row.addWidget(clear_btn)
        filter_row.addWidget(self.test_btn)
        root.addLayout(filter_row)

        # Filters
        self.filters = FilterPanel()
        self.filters.changed.connect(self._filters_changed)
        root.addWidget(self.filters)

        # Tree
        self.model = ChannelTreeModel(self)
        self.model.tickedChanged.connect(self._update_count)
        self.model.tickedChanged.connect(self._update_dirty)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((COL_TIER, 60), (COL_LABELS, 120), (COL_STATUS, 200), (COL_TICKED, 80), (COL_ID, 90)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, width)
        root.addWidget(self.tree, 1)

        expand_btn.clicked.connect(self.tree.expandAll)
        collapse_btn.clicked.connect(self.tree.collapseAll)
        clear_btn.clicked.connect(self.model.clear_ticked)

        tick_action = QAction("Tick selected rows", self, shortcut=QKeySequence("Ctrl+T"))
        tick_action.triggered.connect(lambda: self._tick_selection(True))
        untick_action = QAction("Untick selected rows", self, shortcut=QKeySequence("Ctrl+U"))
        untick_action.triggered.connect(lambda: self._tick_selection(False))
        self.tree.addActions([tick_action, untick_action])
        self._tick_action, self._untick_action = tick_action, untick_action

        # Bottom row: progress, count, export
        bottom = QHBoxLayout()
        self.progress = QProgressBar(maximumWidth=140, textVisible=False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.count_label = QLabel(wordWrap=True)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["ts", "m3u8"])
        self.format_combo.setToolTip("Stream URL extension. m3u8 is only offered if the account allows it.")
        self.format_combo.currentIndexChanged.connect(self._update_dirty)
        self.epg_check = QCheckBox("Include EPG URL")
        self.epg_check.setToolTip("Adds x-tvg-url pointing at the provider's xmltv.php to the header line.")
        self.epg_check.toggled.connect(self._update_dirty)
        self.export_btn = QPushButton("Export playlist…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_playlist)
        self.export_dead_btn = QPushButton("Export not working…")
        self.export_dead_btn.setToolTip("Save the channels that failed a test to their own playlist, with the reason.")
        self.export_dead_btn.setEnabled(False)
        self.export_dead_btn.clicked.connect(self.export_not_working)
        self.stop_test_btn = QPushButton("Stop test")
        self.stop_test_btn.clicked.connect(self.stop_test)
        self.stop_test_btn.hide()
        bottom.addWidget(self.progress)
        bottom.addWidget(self.stop_test_btn)
        bottom.addWidget(self.count_label, 1)
        bottom.addWidget(QLabel("Format"))
        bottom.addWidget(self.format_combo)
        bottom.addWidget(self.epg_check)
        bottom.addWidget(self.export_dead_btn)
        bottom.addWidget(self.export_btn)
        root.addLayout(bottom)

        self.statusBar().showMessage(f"Bonsai {__version__}")
        self._refresh_profile_list()
        self._update_count()

    def _load_fields(self) -> None:
        self.server_edit.setText(self.config.server)
        self.user_edit.setText(self.config.username)
        self.pass_edit.setText(self.config.password)
        self.epg_check.setChecked(self.settings.include_epg)
        self.format_combo.setCurrentText(
            self.settings.stream_format if self.settings.stream_format in ("ts", "m3u8") else "ts"
        )
        self._update_format_options()
        state = FilterState(
            frozenset(parse_tiers(self.settings.hidden_tiers)),
            frozenset(parse_flags(self.settings.hidden_flags)),
            frozenset(str(r) for r in self.settings.hidden_regions),
            frozenset(str(v) for v in self.settings.hidden_status),
        )
        self._show_filter_state(state)
        self._pending_profile = self.settings.last_profile
        last = Source.from_json(self.settings.last_source)
        if last.is_playlist:
            self.playlist_edit.setText(last.location)
            self.source_tabs.setCurrentIndex(1)

    # -- status helpers ----------------------------------------------------

    def _set_status(self, text: str, kind: str = "info") -> None:
        colours = {"error": "#c0392b", "warn": "#b9770e", "ok": "#1e8449"}
        colour = colours.get(kind)
        self.status_label.setStyleSheet(f"color: {colour};" if colour else "")
        self.status_label.setText(text)

    def _on_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.login_btn.setEnabled(not busy)
        self.playlist_load_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy and self.source is not None)
        self.regen_btn.setEnabled(not busy)

    def _update_count(self) -> None:
        n, cats = self.model.ticked_summary()
        total = self.model.total_channels()
        ch = "channel" if n == 1 else "channels"
        cat = "category" if cats == 1 else "categories"
        text = f"{n} {ch} selected in {cats} {cat}"
        if total:
            text += f"  (of {total})"
        hidden = self.model.hidden_ticked_count()
        if hidden:
            text += f"  · {hidden} ticked but hidden by filters"
        if n and self.source is not None and self.source.is_playlist:
            ticked = self.model.ticked_ids()
            unique = len({ch.url for _, chans in self.model.groups() for ch in chans if ch.stream_id in ticked})
            if unique != n:
                text += f"  · {unique} unique streams"
        new = self.model.new_shown_count()
        if new:
            text += f"  · {new} new (highlighted)"
        if self.tester is not None:
            done, total, failed = self._test_progress
            text = f"Testing {done} / {total} \u00b7 {done - failed} working \u00b7 {failed} not working   |   " + text
        self.count_label.setText(text)
        self.export_btn.setEnabled(n > 0 and self._can_export())
        self.export_dead_btn.setEnabled(self._can_export() and any(r.status == "fail" for r in self.results.values()))

    def _update_filter_counts(self) -> None:
        m = self.model
        self.filters.update_counts(m.tier_counts(), m.flag_counts(), m.region_counts())
        self.filters.update_status_counts(m.status_counts())

    def _show_filter_state(self, state: FilterState) -> None:
        self.filters.set_hidden(
            set(state.hidden_tiers), set(state.hidden_flags), set(state.hidden_regions), set(state.hidden_status)
        )
        if self.model.has_data():
            self._update_filter_counts()
        self.model.set_filter_state(state)

    def _filters_changed(self) -> None:
        state = FilterState(
            frozenset(self.filters.hidden_tiers()),
            frozenset(self.filters.hidden_flags()),
            frozenset(self.filters.hidden_regions()),
            frozenset(self.filters.hidden_status()),
        )
        self.model.set_filter_state(state)
        self._apply_filter()
        self.settings.hidden_tiers = sorted(t.value for t in state.hidden_tiers)
        self.settings.hidden_flags = sorted(f.value for f in state.hidden_flags)
        self.settings.hidden_regions = sorted(state.hidden_regions)
        self.settings.hidden_status = sorted(state.hidden_status)
        self._save_settings()
        self._update_dirty()

    def _can_export(self) -> bool:
        if self.source is None:
            return False
        return self.source.is_playlist or self.client is not None

    def _update_format_options(self) -> None:
        playlist = self.source is not None and self.source.is_playlist
        self.format_combo.setEnabled(not playlist)
        self.format_combo.setToolTip(
            "Playlist streams keep their own addresses." if playlist
            else "Stream URL extension. m3u8 is only offered if the account allows it."
        )
        self.epg_check.setToolTip(
            "Keep the playlist's own guide link (x-tvg-url) in the header." if playlist
            else "Adds x-tvg-url pointing at the provider's xmltv.php to the header line."
        )
        m3u8_item = self.format_combo.model().item(1)
        allowed = self.account is None or self.account.supports_m3u8
        m3u8_item.setEnabled(allowed)
        if not allowed and self.format_combo.currentText() == "m3u8":
            self.format_combo.setCurrentText("ts")

    def _save_settings(self) -> None:
        try:
            save_settings(self.settings)
        except OSError:
            pass

    # -- startup, login and loading ----------------------------------------

    def _make_client(self, quiet: bool = False) -> XtreamClient | None:
        try:
            client = XtreamClient(
                self.server_edit.text(),
                self.user_edit.text().strip(),
                self.pass_edit.text(),
                self.config.user_agent,
            )
        except ValueError as exc:
            if not quiet:
                self._set_status(str(exc), "error")
            return None
        if not client.username or not client.password:
            if not quiet:
                self._set_status("Username and password are required.", "error")
            return None
        return client

    def _startup(self) -> None:
        """Show the cached list straight away if there is one; otherwise log in and download."""
        last = Source.from_json(self.settings.last_source)
        if last.is_playlist:
            client = self._make_client(quiet=True)
            if client is not None:
                self.client = client  # ready for Xtream profiles; no network call yet
            self.load_playlist(last.location)
            return
        client = self._make_client()
        if client is None:
            return
        key = xtream_key(client.server, client.username)
        cached = load_lists(key)
        if cached is None:
            self.test_login()
            return
        self.client = client
        self._show_lists(Source(), key, cached, from_cache=True)
        self.refresh_btn.setEnabled(True)
        # Still check the account in the background so the status line is current.
        self._set_status(f"Checking account on {client.server}…")
        self.tasks.run(
            client.account_info,
            lambda info: self._login_ok(client, info, load=False),
            lambda msg: self._set_status(msg, "error"),
            client.redact,
        )

    def test_login(self) -> None:
        if self.tasks.busy or self._busy_testing():
            return
        client = self._make_client()
        if client is None:
            return
        self._set_status(f"Logging in to {client.server}…")
        self.tasks.run(
            client.account_info,
            lambda info: self._login_ok(client, info, load=True),
            lambda msg: self._set_status(msg, "error"),
            client.redact,
        )

    def _login_ok(self, client: XtreamClient, info: AccountInfo, load: bool) -> None:
        self.client = client
        self.account = info
        parts = [
            info.status + (" (trial)" if info.is_trial else ""),
            f"expires {info.expires.astimezone():%Y-%m-%d}" if info.expires else "no expiry date",
            f"{info.active_connections}/{info.max_connections} connections",
        ]
        if info.allowed_output_formats:
            parts.append("formats " + ", ".join(info.allowed_output_formats))
        self._set_status(" · ".join(parts), "ok" if info.is_active else "warn")
        self._update_format_options()
        self.refresh_btn.setEnabled(not self.tasks.busy)

        self.config = Config(client.server, client.username, client.password, self.config.user_agent)
        try:
            save_config(self.config)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save config: {exc}")
        self.server_edit.setText(client.server)

        key = xtream_key(client.server, client.username)
        if not load or self.loaded_key == key:
            return
        if self.source is not None and self.loaded_key != key:
            if not self._confirm_discard("switch to another list"):
                return
            self._leave_profile()
        cached = load_lists(key)
        if cached is not None:
            self._show_lists(Source(), key, cached, from_cache=True)
        else:
            self.load_channels()

    def load_channels(self, then=None) -> None:
        """Download fresh lists (and cache them). `then` runs after they are shown."""
        client = self.client
        if client is None or self.tasks.busy:
            return
        self.statusBar().showMessage("Downloading live categories and channels…")

        key = xtream_key(client.server, client.username)

        def fetch() -> CachedLists:
            cats, chans = client.live_categories(), client.live_streams()
            try:
                return save_lists(key, cats, chans)
            except OSError:
                return CachedLists(cats, chans, datetime.now().astimezone())

        def done(cached: CachedLists) -> None:
            self._show_lists(Source(), key, cached, from_cache=False)
            if then:
                then()

        self.tasks.run(fetch, done, self._load_failed, client.redact)

    # -- playlists -----------------------------------------------------------

    def _browse_playlist(self) -> None:
        start = self.playlist_edit.text().strip()
        start = start if start and not is_url(start) else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Open playlist", start, "M3U playlists (*.m3u *.m3u8);;All files (*)"
        )
        if path:
            self.playlist_edit.setText(path)
            self.load_playlist_from_field()

    def _use_preset(self, url: str) -> None:
        self.playlist_edit.setText(url)
        self.load_playlist_from_field()

    def load_playlist_from_field(self) -> None:
        if self._busy_testing():
            return
        location = self.playlist_edit.text().strip()
        if not location:
            self.playlist_status.setText("Enter a playlist link or choose a file.")
            return
        if self.source is not None and self.source != Source(PLAYLIST, location):
            if not self._confirm_discard("switch to another list"):
                return
            self._leave_profile()
        self.load_playlist(location)

    def load_playlist(self, location: str, force: bool = False, then=None) -> None:
        """Show a playlist: the saved copy of a URL if there is one (unless `force`), else read it."""
        source = Source(PLAYLIST, location)
        key = playlist_key(location)
        if not force and source.is_remote:
            cached = load_lists(key)
            if cached is not None:
                self._show_lists(source, key, cached, from_cache=True)
                if then:
                    then()
                return
        if self.tasks.busy:
            return
        self.playlist_status.setStyleSheet("")
        self.playlist_status.setText("Reading playlist…")
        self.statusBar().showMessage(f"Reading {source.label()}…")

        def fetch() -> CachedLists:
            cats, chans, header = load_playlist(location)
            if source.is_remote:
                try:
                    return save_lists(key, cats, chans, header)
                except OSError:
                    pass
            return CachedLists(cats, chans, datetime.now().astimezone(), header)

        def done(cached: CachedLists) -> None:
            self._show_lists(source, key, cached, from_cache=False)
            if then:
                then()

        def failed(msg: str) -> None:
            self.playlist_status.setStyleSheet("color: #c0392b;")
            self.playlist_status.setText(msg)
            self.statusBar().showMessage("Reading the playlist failed")

        self.tasks.run(fetch, done, failed)

    def refresh_source(self, then=None) -> None:
        if self.source is None or self._busy_testing():
            return
        if self.source.is_playlist:
            self.load_playlist(self.source.location, force=True, then=then)
        else:
            self.load_channels(then=then)

    def _leave_profile(self) -> None:
        """Close the open profile (its list is about to be replaced by another source)."""
        if self.profile is None:
            return
        self.profile = None
        self.settings.last_profile = ""
        self._save_settings()
        self._refresh_profile_list("")
        self._mark_clean()

    def _show_lists(self, source: Source, key: str, cached: CachedLists, from_cache: bool) -> None:
        groups = group_channels(cached.categories, cached.channels)
        same_list = key == self.loaded_key
        first_load = not self.model.has_data() or not same_list
        if not same_list:
            self.model.replace_ticked(())  # ticks belong to the previous list
        self.model.set_groups(groups)
        if not same_list:
            self.results = load_results(key)
        self.model.set_results(self._results_by_sid(source))
        self.tree.header().resizeSection(COL_ID, 170 if source.is_playlist else 90)
        self.source = source
        self.loaded_key = key
        self.playlist_header = dict(cached.header) if source.is_playlist else {}
        self.settings.last_source = source.to_json()
        self._save_settings()
        self._update_format_options()
        self.refresh_btn.setEnabled(not self.tasks.busy)
        self._update_filter_counts()
        self._apply_filter()
        when = _fmt_time(cached.fetched_at)
        if source.is_playlist:
            self.playlist_status.setStyleSheet("")
            self.playlist_status.setText(f"{self.model.total_channels()} streams")
        label = source.label()
        if source.is_playlist and not source.is_remote:
            self.cache_label.setText(f"{label} · read {when}")
        else:
            self.cache_label.setText(f"{label} · list from {when}" + (" (saved copy)" if from_cache else ""))
        self.statusBar().showMessage(
            f"{self.model.total_channels()} channels in {sum(1 for _, c in groups if c)} categories"
            + (" — from the saved copy; use Refresh for the latest" if from_cache else "")
        )
        if self.profile is not None and not first_load:
            # Fresh list under an open profile: keep the ticks, re-mark new channels.
            result = profiles.apply(self.profile, groups)
            self.model.replace_ticked(self.model.all_ticked_ids() | result.auto_ticked, result.new)
            self._expand_new()
        elif self._pending_profile:
            name, self._pending_profile = self._pending_profile, ""
            self.load_profile(name, quiet_if_missing=True)
        self._update_count()

    def _load_failed(self, msg: str) -> None:
        self.statusBar().showMessage("Downloading channels failed")
        self._set_status(msg, "error")

    # -- profiles ----------------------------------------------------------

    def _refresh_profile_list(self, select: str | None = None) -> None:
        current = select if select is not None else (self.profile.name if self.profile else "")
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem(NO_PROFILE, "")
        for name in profiles.list_names():
            self.profile_combo.addItem(name, name)
        idx = self.profile_combo.findData(current)
        self.profile_combo.setCurrentIndex(max(idx, 0))
        self.profile_combo.blockSignals(False)
        has = self.profile is not None
        self.save_btn.setEnabled(True)
        self.delete_btn.setEnabled(has)

    def _current_state(self):
        return (
            self.model.all_ticked_ids(),
            self.model.filter_state,
            self.format_combo.currentText(),
            self.epg_check.isChecked(),
            self.new_mode.currentData(),
        )

    def _mark_clean(self) -> None:
        self._clean_state = self._current_state() if self.profile else None
        self._update_title()

    def is_dirty(self) -> bool:
        return self.profile is not None and self._current_state() != self._clean_state

    def _update_dirty(self, *_args) -> None:
        self._update_title()

    def _update_title(self) -> None:
        if self.profile is None:
            self.setWindowTitle("Bonsai")
        else:
            star = " *" if self.is_dirty() else ""
            self.setWindowTitle(f"Bonsai — {self.profile.name}{star}")

    def _confirm_discard(self, action: str) -> bool:
        """If the open profile has unsaved changes, offer to save. False means cancel."""
        if not self.is_dirty():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"The profile “{self.profile.name}” has unsaved changes.")
        box.setInformativeText(f"Save them before you {action}?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        choice = box.exec()
        if choice == QMessageBox.StandardButton.Save:
            return self.save_profile()
        return choice == QMessageBox.StandardButton.Discard

    def _profile_chosen(self, index: int) -> None:
        name = self.profile_combo.itemData(index) or ""
        if self.profile and name == self.profile.name:
            return
        if self._busy_testing():
            self._refresh_profile_list()
            return
        if not self._confirm_discard("switch profiles"):
            self._refresh_profile_list()
            return
        if name:
            self.load_profile(name)
        else:
            self.profile = None
            self.model.clear_new()
            self.settings.last_profile = ""
            self._save_settings()
            self._refresh_profile_list("")
            self._mark_clean()
            self._update_count()

    def load_profile(self, name: str, quiet_if_missing: bool = False) -> None:
        try:
            profile = profiles.load(name)
        except ProfileError as exc:
            if not quiet_if_missing:
                QMessageBox.warning(self, "Profile", str(exc))
            self._refresh_profile_list("")
            return
        if self.source is None or profile.source != self.source:
            # The profile belongs to another list: load that first; _show_lists then applies it.
            # (Set pending before opening: a saved copy is shown straight away.)
            if not self._open_source(profile.source, pending=profile.name):
                self._refresh_profile_list()
            return
        self.profile = profile
        self.settings.last_profile = profile.name
        self._save_settings()

        self._show_filter_state(profile.filters)
        self.format_combo.setCurrentText(profile.stream_format)
        self._update_format_options()
        self.epg_check.setChecked(profile.include_epg)
        self.new_mode.setCurrentIndex(max(self.new_mode.findData(profile.new_channels), 0))
        self._refresh_profile_list(profile.name)

        if not self.model.has_data():
            # Applied once the channel list arrives.
            self._pending_profile = profile.name
            self.profile = None
            self._mark_clean()
            return
        result = profiles.apply(profile, self.model.groups())
        self.model.replace_ticked(result.ticked, result.new)
        self._apply_filter()
        self._mark_clean()
        self._expand_new()
        self._report_applied(profile, result)

    def _open_source(self, source: Source, then=None, force: bool = False, pending: str = "") -> bool:
        """Load `source` into the tree (saved copy first unless `force`). False if it cannot be opened.

        `pending` names a profile to apply once the list is shown.
        """
        if source.is_playlist:
            self.playlist_edit.setText(source.location)
            self.source_tabs.setCurrentIndex(1)
            self._leave_profile()
            self._pending_profile = pending
            self.load_playlist(source.location, force=force, then=then)
            return True
        self.source_tabs.setCurrentIndex(0)
        client = self.client or self._make_client()
        if client is None:
            QMessageBox.information(self, "Profile", "This profile uses the Xtream account. Log in first.")
            return False
        self.client = client
        key = xtream_key(client.server, client.username)
        cached = None if force else load_lists(key)
        self._leave_profile()
        self._pending_profile = pending
        if cached is not None:
            self._show_lists(Source(), key, cached, from_cache=True)
            if then:
                then()
        else:
            self.load_channels(then=then)
        return True

    def _expand_new(self) -> None:
        rows = self.model.rows_with_new()
        if 0 < len(rows) <= EXPAND_NEW_LIMIT:
            for row in rows:
                self.tree.expand(self.model.index(row, 0))

    def _report_applied(self, profile: Profile, result: ApplyResult, prefix: str = "") -> None:
        parts = [f"{prefix}Profile “{profile.name}”: {len(result.ticked)} channels ticked"]
        if result.rematched:
            parts.append(f"{result.rematched} found again by name")
        if result.auto_ticked:
            parts.append(f"{len(result.auto_ticked)} new channels ticked automatically")
        elif result.new:
            parts.append(f"{len(result.new)} new channels highlighted")
        if result.unmatched:
            parts.append(f"{len(result.unmatched)} no longer found")
        self.statusBar().showMessage(", ".join(parts))
        if result.unmatched:
            lines = [f"• {c.name}  ({c.category})" for c in result.unmatched[:UNMATCHED_SHOWN]]
            more = len(result.unmatched) - UNMATCHED_SHOWN
            if more > 0:
                lines.append(f"…and {more} more")
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Some channels are gone")
            box.setText(
                f"{len(result.unmatched)} channel(s) saved in “{profile.name}” could not be found "
                "by stream id or by name. The provider may have removed or renamed them."
            )
            box.setDetailedText("\n".join(lines))
            box.setInformativeText(
                "They stay in the profile file until you save the profile again. "
                "Click “Show Details” for the list."
            )
            box.exec()

    def _snapshot(self, name: str) -> Profile:
        export_path = self.profile.export_path if self.profile else ""
        if not export_path and self._last_export[0] == self.loaded_key:
            # A new profile adopts the path you just exported this list to, never another list's.
            export_path = self._last_export[1]
        return profiles.snapshot(
            name,
            self.model.groups(),
            self.model.all_ticked_ids(),
            self.model.full_category_ids(),
            new_channels=self.new_mode.currentData(),
            export_path=export_path,
            stream_format=self.format_combo.currentText(),
            include_epg=self.epg_check.isChecked(),
            filters=self.model.filter_state,
            source=self.source or Source(),
        )

    def save_profile(self) -> bool:
        if self.profile is None:
            return self.save_profile_as()
        return self._write_profile(self._snapshot(self.profile.name))

    def save_profile_as(self) -> bool:
        suggestion = self.profile.name if self.profile else "Vita"
        name, ok = QInputDialog.getText(self, "Save profile as", "Profile name:", text=suggestion)
        if not ok:
            return False
        try:
            name = profiles.validate_name(name)
        except ProfileError as exc:
            QMessageBox.warning(self, "Profile", str(exc))
            return False
        exists = name in profiles.list_names()
        if exists and (self.profile is None or self.profile.name != name):
            reply = QMessageBox.question(self, "Profile", f"Replace the existing profile “{name}”?")
            if reply != QMessageBox.StandardButton.Yes:
                return False
        return self._write_profile(self._snapshot(name))

    def _write_profile(self, profile: Profile) -> bool:
        if not self.model.has_data():
            QMessageBox.information(self, "Profile", "Load the channel list before saving a profile.")
            return False
        try:
            path = profiles.save(profile)
        except OSError as exc:
            QMessageBox.critical(self, "Profile", f"Could not save the profile:\n{exc}")
            return False
        self.profile = profile
        self.settings.last_profile = profile.name
        self._save_settings()
        self.model.clear_new()
        self._refresh_profile_list(profile.name)
        self._mark_clean()
        self._update_count()
        self.statusBar().showMessage(f"Saved profile “{profile.name}” ({len(profile.channels)} channels) to {path}")
        return True

    def delete_profile(self) -> None:
        if self.profile is None:
            return
        name = self.profile.name
        reply = QMessageBox.question(
            self, "Delete profile", f"Delete the profile “{name}”? Your current ticks stay as they are."
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        profiles.delete(name)
        self.profile = None
        self.model.clear_new()
        self.settings.last_profile = ""
        self._save_settings()
        self._refresh_profile_list("")
        self._mark_clean()
        self._update_count()
        self.statusBar().showMessage(f"Deleted profile “{name}”")

    def _remember_export(self, path: str, ext: str, epg: bool) -> None:
        self._last_export = (self.loaded_key, path)
        self.settings.last_export_path = path
        self.settings.stream_format = ext
        self.settings.include_epg = epg
        self._save_settings()
        if self.profile is None:
            return
        # Store the path in the profile file without saving any unsaved tick changes.
        self.profile.export_path = path
        try:
            on_disk = profiles.load(self.profile.name)
            on_disk.export_path = path
            profiles.save(on_disk)
        except (ProfileError, OSError):
            pass

    # -- regenerate ----------------------------------------------------------

    def regenerate(self) -> None:
        if self.tasks.busy or self._busy_testing():
            return
        if self.profile is None:
            QMessageBox.information(
                self, "Regenerate", "Choose a profile first, or save your current ticks with “Save as…”."
            )
            return
        if not self.profile.source.is_playlist and self.client is None:
            QMessageBox.information(self, "Regenerate", "Log in first.")
            return
        if not self._confirm_discard("regenerate"):
            return
        path = self.profile.export_path
        if not path:
            path = self._ask_export_path(f"{self.profile.name}.m3u")
            if not path:
                return
        name = self.profile.name
        source = self.profile.source
        finish = lambda: self._finish_regenerate(name, path)  # noqa: E731
        if source == self.source:
            self.refresh_source(then=finish)
        else:
            self._open_source(source, then=finish, force=True)

    def _finish_regenerate(self, name: str, path: str) -> None:
        try:
            profile = profiles.load(name)
        except ProfileError as exc:
            QMessageBox.warning(self, "Regenerate", str(exc))
            return
        self.profile = profile
        self.settings.last_profile = profile.name
        self._save_settings()
        self._refresh_profile_list(profile.name)
        self._show_filter_state(profile.filters)
        self.format_combo.setCurrentText(profile.stream_format)
        self._update_format_options()
        self.epg_check.setChecked(profile.include_epg)
        self.new_mode.setCurrentIndex(max(self.new_mode.findData(profile.new_channels), 0))
        result = profiles.apply(profile, self.model.groups())
        self.model.replace_ticked(result.ticked, result.new)
        self._apply_filter()
        self._mark_clean()
        self._expand_new()
        written = self._write_playlist(path)
        if written is None:
            return
        self._report_applied(profile, result, prefix=f"Regenerated {path} with {written} channels. ")

    # -- tree interaction --------------------------------------------------

    def _apply_filter(self) -> None:
        self.model.set_filter(self.filter_edit.text())
        if self.model.is_filtered and self.model.visible_channel_count() <= AUTO_EXPAND_LIMIT:
            self.tree.expandAll()

    def _tick_selection(self, ticked: bool) -> None:
        rows = [i for i in self.tree.selectionModel().selectedIndexes() if i.column() == COL_NAME]
        self.model.set_ticked_for_indexes(rows, ticked)

    def _tree_menu(self, pos) -> None:
        menu = QMenu(self)
        has_sel = self.tree.selectionModel().hasSelection()
        self._tick_action.setEnabled(has_sel)
        self._untick_action.setEnabled(has_sel)
        menu.addAction(self._tick_action)
        menu.addAction(self._untick_action)
        menu.addSeparator()
        menu.addAction("Expand all", self.tree.expandAll)
        menu.addAction("Collapse all", self.tree.collapseAll)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
        self._tick_action.setEnabled(True)
        self._untick_action.setEnabled(True)

    # -- export ------------------------------------------------------------

    def _ask_export_path(self, default_name: str = "bonsai.m3u") -> str:
        default = (
            (self.profile.export_path if self.profile else "")
            or self.settings.last_export_path
            or str(Path.home() / default_name)
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export playlist", default, "M3U playlist (*.m3u);;All files (*)"
        )
        if path and not Path(path).suffix:
            path += ".m3u"
        return path

    def export_playlist(self) -> None:
        if not self._can_export() or not self.model.ticked_ids():
            return
        path = self._ask_export_path()
        if not path:
            return
        written = self._write_playlist(path)
        if written is not None:
            self.statusBar().showMessage(f"Wrote {written} channels to {path}")

    def _write_playlist(self, path: str) -> int | None:
        """Write the exportable ticks to `path`. Returns the channel count, or None on failure."""
        ext = self.format_combo.currentText()
        epg = self.epg_check.isChecked()
        if self.source is not None and self.source.is_playlist:
            url_for = lambda ch: ch.url  # noqa: E731
            epg_url = playlist_epg_url(self.playlist_header) if epg else ""
        else:
            client = self.client
            url_for = lambda ch: client.stream_url(ch.stream_id, ext)  # noqa: E731
            epg_url = client.epg_url() if epg else ""
        entries = build_entries(self.model.groups(), self.model.ticked_ids(), url_for)
        try:
            write_m3u(path, entries, epg_url or None)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write {path}:\n{exc}")
            return None
        self._remember_export(path, ext, epg)
        return len(entries)

    # -- stream testing --------------------------------------------------------

    def _is_xtream(self) -> bool:
        return self.source is not None and not self.source.is_playlist

    def _results_by_sid(self, source: Source) -> dict[int, TestResult]:
        xtream = not source.is_playlist
        out = {}
        for _, chans in self.model.groups():
            for ch in chans:
                r = self.results.get(result_key(ch, xtream))
                if r is not None:
                    out[ch.stream_id] = r
        return out

    def _busy_testing(self) -> bool:
        if self.tester is None:
            return False
        QMessageBox.information(self, "Stream test", "A stream test is running. Stop it first.")
        return True

    def _channels_for(self, scope: str):
        groups = self.model.groups()
        if scope == "ticked":
            ids = self.model.all_ticked_ids()
        elif scope == "shown":
            ids = self.model.visible_ids()
        else:
            ids = self.model.ids_for_indexes(
                [i for i in self.tree.selectionModel().selectedIndexes() if i.column() == COL_NAME]
            )
        return [ch for _, chans in groups for ch in chans if ch.stream_id in ids]

    def start_test(self, mode: str, scope: str) -> None:
        if self.tester is not None or self.tasks.busy:
            return
        if self.source is None:
            QMessageBox.information(self, "Stream test", "Load a channel list first.")
            return
        if mode == DEEP and find_ffprobe() is None:
            QMessageBox.warning(self, "Stream test", f"The deep test needs ffprobe (part of FFmpeg).\n\n{FFPROBE_HELP}")
            return
        channels = self._channels_for(scope)
        if not channels:
            what = {"ticked": "No channels are ticked.", "shown": "No channels are shown.",
                    "selected": "No rows are selected."}[scope]
            QMessageBox.information(self, "Stream test", what)
            return
        if self._is_xtream():
            if self.client is None:
                QMessageBox.information(self, "Stream test", "Log in first.")
                return
            # Never collide with another device: check the connection count first.
            self.statusBar().showMessage("Checking that your account's connection is free…")
            client = self.client
            self.tasks.run(
                client.account_info,
                lambda info: self._confirm_xtream_test(mode, channels, info),
                lambda msg: self._set_status(msg, "error"),
                client.redact,
            )
        else:
            self._confirm_playlist_test(mode, channels)

    def _confirm_xtream_test(self, mode: str, channels, info: AccountInfo) -> None:
        self.account = info
        active, limit = info.active_connections or 0, info.max_connections or 1
        if active >= limit:
            QMessageBox.warning(
                self, "Stream test",
                f"Your account is using {active} of {limit} connection(s) right now, so tests would be refused "
                "(your provider answers with HTTP 511).\n\nStop playback on your other devices, wait a minute, "
                "and try again.",
            )
            self.statusBar().showMessage("Account busy: test not started")
            return
        reply = QMessageBox.question(
            self, "Stream test",
            f"Test {len(channels)} channel(s) one at a time? About "
            f"{_fmt_duration(estimate_seconds(len(channels), mode, 1, 3.0))}.\n\n"
            f"Each test uses one of your account's {limit} connection(s), so don't watch anything on this "
            "account until it finishes. You can stop at any time.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._run_test(mode, channels, workers=1, per_host=1, delay=3.0)

    def _confirm_playlist_test(self, mode: str, channels) -> None:
        workers = 8 if mode == QUICK else 4
        unique = len({ch.url for ch in channels})
        if unique > 300:
            reply = QMessageBox.question(
                self, "Stream test",
                f"Test {unique} streams? About {_fmt_duration(estimate_seconds(unique, mode, workers, 0))}, "
                f"{workers} at a time "
                "(at most 2 per server). You can stop at any time; finished results are kept.",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._run_test(mode, channels, workers=workers, per_host=2, delay=0.0)

    def _run_test(self, mode: str, channels, workers: int, per_host: int, delay: float) -> None:
        xtream = self._is_xtream()
        client = self.client
        ext = self.format_combo.currentText()
        # One job per distinct stream; its result is copied to every row showing that stream.
        self._test_rows: dict[str, list[int]] = {}  # result key -> rows (stream ids) showing that stream
        self._test_keys: dict[int, str] = {}  # job key (first row's stream id) -> result key
        jobs = []
        for ch in channels:
            key = result_key(ch, xtream)
            if key in self._test_rows:
                self._test_rows[key].append(ch.stream_id)
                continue
            self._test_rows[key] = [ch.stream_id]
            self._test_keys[ch.stream_id] = key
            url = client.stream_url(ch.stream_id, ext) if xtream else ch.url
            jobs.append(TestJob(ch.stream_id, request_for(ch, url)))
        redact = client.redact if (xtream and client) else (lambda t: t)
        if mode == DEEP:
            test = lambda req, session: deep_test(req, session, xtream, redact=redact)  # noqa: E731
        else:
            test = lambda req, session: quick_test(req, session, xtream)  # noqa: E731
        self._test_mode = mode
        self._test_progress = (0, len(jobs), 0)
        self._test_busy_stop = False
        self.model.mark_testing([sid for sids in self._test_rows.values() for sid in sids])
        self.tester = TestThread(jobs, test, workers, per_host, delay, self)
        self.tester.result.connect(self._on_test_result)
        self.tester.finished.connect(self._test_finished)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.show()
        self.stop_test_btn.show()
        self.test_btn.setEnabled(False)
        self.statusBar().showMessage(f"{'Deep' if mode == DEEP else 'Quick'}-testing {len(jobs)} stream(s)…")
        self._update_count()
        self.tester.start()

    def _on_test_result(self, job: TestJob, result: TestResult) -> None:
        key = self._test_keys.get(job.key)
        if key is None:
            return
        self.results[key] = result
        for sid in self._test_rows.get(key, [job.key]):
            self.model.update_result(sid, result)
        done, total, failed = self._test_progress
        self._test_progress = (done + 1, total, failed + (result.status == "fail"))
        self.progress.setValue(done + 1)
        if result.account_busy and self.tester is not None and not self._test_busy_stop:
            # Another device took the connection: stop rather than keep failing.
            self._test_busy_stop = True
            self.tester.stop()
        if (done + 1) % 50 == 0:
            self._save_results()
        self._update_count()

    def _test_finished(self) -> None:
        done, total, failed = self._test_progress
        self.tester = None
        self._save_results()
        self.model.end_testing()
        self._update_filter_counts()
        self._apply_filter()
        self.progress.hide()
        self.progress.setRange(0, 0)
        self.stop_test_btn.hide()
        self.test_btn.setEnabled(True)
        counts = self.model.status_counts()
        summary = f"Tested {done} of {total}: {done - failed} working, {failed} not working"
        if counts["novita"]:
            summary += f" \u00b7 {counts['novita']} in this list won't play on the Vita"
        self.statusBar().showMessage(summary)
        self._update_count()
        if self._test_busy_stop:
            QMessageBox.warning(
                self, "Stream test stopped",
                "Your provider refused a test because the account's connection is in use (another device "
                "started watching?). The test was stopped; results so far are kept.",
            )

    def stop_test(self) -> None:
        if self.tester is not None:
            self.tester.stop()
            self.stop_test_btn.setEnabled(False)
            self.statusBar().showMessage("Stopping after the tests in progress…")
            QTimer.singleShot(0, lambda: self.stop_test_btn.setEnabled(True))

    def _save_results(self) -> None:
        if not self.loaded_key:
            return
        try:
            save_results(self.loaded_key, self.results)
        except OSError:
            pass

    def clear_results(self) -> None:
        if self._busy_testing() or not self.results:
            return
        reply = QMessageBox.question(self, "Stream test", f"Forget {len(self.results)} test result(s) for this list?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.results = {}
        self._save_results()
        self.model.set_results({})
        self._update_filter_counts()
        self._apply_filter()

    def export_not_working(self) -> None:
        if not self._can_export():
            return
        failed = {ch.stream_id for _, chans in self.model.groups() for ch in chans
                  if self.model.status_of(ch.stream_id) == NOT_WORKING}
        ticked_failed = failed & self.model.all_ticked_ids()
        chosen = ticked_failed or failed
        if not chosen:
            return
        base = (self.profile.export_path if self.profile else "") or self.settings.last_export_path
        default = (str(Path(base).with_suffix("")) + ".not-working.m3u") if base else str(Path.home() / "not-working.m3u")
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {len(chosen)} not working channel(s)", default, "M3U playlist (*.m3u);;All files (*)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".m3u"
        if self.source.is_playlist:
            url_for = lambda ch: ch.url  # noqa: E731
        else:
            client, ext = self.client, self.format_combo.currentText()
            url_for = lambda ch: client.stream_url(ch.stream_id, ext)  # noqa: E731

        def comment(ch) -> str:
            r = self.model.result_of(ch.stream_id)
            when = r.at[:10] if r and r.at else ""
            return f"Not working ({when}): {r.reason}" if r else "Not working"

        entries = build_entries(self.model.groups(), chosen, url_for, comment)
        try:
            write_m3u(path, entries)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write {path}:\n{exc}")
            return
        which = "ticked " if ticked_failed else ""
        self.statusBar().showMessage(f"Wrote {len(entries)} not working {which}channel(s) to {path}")

    # -- closing -------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard("quit"):
            event.ignore()
            return
        if self.tester is not None:
            self.tester.stop()
            self.tester.wait(15000)
            self._save_results()
        event.accept()


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Bonsai")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
