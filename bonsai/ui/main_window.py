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
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, profiles
from ..api import XtreamClient
from ..cache import CachedLists, load_lists, save_lists
from ..filters import FilterState, parse_flags
from ..m3u import build_entries, write_m3u
from ..models import AccountInfo, group_channels
from ..profiles import NEW_HIGHLIGHT, NEW_INCLUDE, ApplyResult, Profile, ProfileError
from ..quality import parse_tiers
from ..storage import (
    Config,
    ConfigError,
    load_config,
    load_settings,
    save_config,
    save_settings,
)
from .channel_tree import COL_ID, COL_LABELS, COL_NAME, COL_TICKED, COL_TIER, ChannelTreeModel
from .filter_panel import FilterPanel
from .workers import TaskRunner

# Expanding every category is cheap for a search hit list but slow for the whole catalogue.
AUTO_EXPAND_LIMIT = 2000
# After loading a profile, expand categories with new channels if there are only a few.
EXPAND_NEW_LIMIT = 15
NO_PROFILE = "(no profile)"
UNMATCHED_SHOWN = 25


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone().strftime("%d %b %Y %H:%M")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1000, 860)

        self.client: XtreamClient | None = None
        self.account: AccountInfo | None = None
        self.loaded_account: tuple[str, str] | None = None  # whose lists are in the tree
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
        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        # Login panel
        login_box = QGroupBox("Account")
        form = QFormLayout(login_box)
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

        self.refresh_btn = QPushButton("Refresh from server")
        self.refresh_btn.setToolTip("Download the live categories and channels again.")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(lambda: self.load_channels())
        self.cache_label = QLabel("No channel list yet")
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.refresh_btn)
        cache_row.addWidget(self.cache_label, 1)
        form.addRow(cache_row)
        for edit in (self.server_edit, self.user_edit, self.pass_edit):
            edit.returnPressed.connect(self.test_login)
        root.addWidget(login_box)

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
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(expand_btn)
        filter_row.addWidget(collapse_btn)
        filter_row.addWidget(clear_btn)
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
        for col, width in ((COL_TIER, 60), (COL_LABELS, 130), (COL_TICKED, 90), (COL_ID, 90)):
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
        bottom.addWidget(self.progress)
        bottom.addWidget(self.count_label, 1)
        bottom.addWidget(QLabel("Format"))
        bottom.addWidget(self.format_combo)
        bottom.addWidget(self.epg_check)
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
        )
        self._show_filter_state(state)
        self._pending_profile = self.settings.last_profile

    # -- status helpers ----------------------------------------------------

    def _set_status(self, text: str, kind: str = "info") -> None:
        colours = {"error": "#c0392b", "warn": "#b9770e", "ok": "#1e8449"}
        colour = colours.get(kind)
        self.status_label.setStyleSheet(f"color: {colour};" if colour else "")
        self.status_label.setText(text)

    def _on_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.login_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy and self.client is not None)
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
        new = self.model.new_shown_count()
        if new:
            text += f"  · {new} new (highlighted)"
        self.count_label.setText(text)
        self.export_btn.setEnabled(n > 0 and self.client is not None)

    def _update_filter_counts(self) -> None:
        m = self.model
        self.filters.update_counts(m.tier_counts(), m.flag_counts(), m.region_counts())

    def _show_filter_state(self, state: FilterState) -> None:
        self.filters.set_hidden(set(state.hidden_tiers), set(state.hidden_flags), set(state.hidden_regions))
        if self.model.has_data():
            self._update_filter_counts()
        self.model.set_filter_state(state)

    def _filters_changed(self) -> None:
        state = FilterState(
            frozenset(self.filters.hidden_tiers()),
            frozenset(self.filters.hidden_flags()),
            frozenset(self.filters.hidden_regions()),
        )
        self.model.set_filter_state(state)
        self._apply_filter()
        self.settings.hidden_tiers = sorted(t.value for t in state.hidden_tiers)
        self.settings.hidden_flags = sorted(f.value for f in state.hidden_flags)
        self.settings.hidden_regions = sorted(state.hidden_regions)
        self._save_settings()
        self._update_dirty()

    def _update_format_options(self) -> None:
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

    def _make_client(self) -> XtreamClient | None:
        try:
            client = XtreamClient(
                self.server_edit.text(),
                self.user_edit.text().strip(),
                self.pass_edit.text(),
                self.config.user_agent,
            )
        except ValueError as exc:
            self._set_status(str(exc), "error")
            return None
        if not client.username or not client.password:
            self._set_status("Username and password are required.", "error")
            return None
        return client

    def _startup(self) -> None:
        """Show the cached list straight away if there is one; otherwise log in and download."""
        client = self._make_client()
        if client is None:
            return
        cached = load_lists(client.server, client.username)
        if cached is None:
            self.test_login()
            return
        self.client = client
        self._show_lists(client, cached, from_cache=True)
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
        if self.tasks.busy:
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

        if not load or self.loaded_account == (client.server, client.username):
            return
        cached = load_lists(client.server, client.username)
        if cached is not None:
            self._show_lists(client, cached, from_cache=True)
        else:
            self.load_channels()

    def load_channels(self, then=None) -> None:
        """Download fresh lists (and cache them). `then` runs after they are shown."""
        client = self.client
        if client is None or self.tasks.busy:
            return
        self.statusBar().showMessage("Downloading live categories and channels…")

        def fetch() -> CachedLists:
            cats, chans = client.live_categories(), client.live_streams()
            try:
                return save_lists(client.server, client.username, cats, chans)
            except OSError:
                return CachedLists(cats, chans, datetime.now().astimezone())

        def done(cached: CachedLists) -> None:
            self._show_lists(client, cached, from_cache=False)
            if then:
                then()

        self.tasks.run(fetch, done, self._load_failed, client.redact)

    def _show_lists(self, client: XtreamClient, cached: CachedLists, from_cache: bool) -> None:
        groups = group_channels(cached.categories, cached.channels)
        first_load = not self.model.has_data()
        self.model.set_groups(groups)
        self.loaded_account = (client.server, client.username)
        self._update_filter_counts()
        self._apply_filter()
        when = _fmt_time(cached.fetched_at)
        self.cache_label.setText(
            f"Channel list from {when}" + (" (saved copy)" if from_cache else "")
        )
        self.statusBar().showMessage(
            f"{self.model.total_channels()} live channels in {sum(1 for _, c in groups if c)} categories"
            + (" — from the saved copy; use Refresh from server for the latest" if from_cache else "")
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
        return profiles.snapshot(
            name,
            self.model.groups(),
            self.model.all_ticked_ids(),
            self.model.full_category_ids(),
            new_channels=self.new_mode.currentData(),
            export_path=export_path or self.settings.last_export_path,
            stream_format=self.format_combo.currentText(),
            include_epg=self.epg_check.isChecked(),
            filters=self.model.filter_state,
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
        if self.tasks.busy:
            return
        if self.profile is None:
            QMessageBox.information(
                self, "Regenerate", "Choose a profile first, or save your current ticks with “Save as…”."
            )
            return
        if self.client is None:
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
        self.load_channels(then=lambda: self._finish_regenerate(name, path))

    def _finish_regenerate(self, name: str, path: str) -> None:
        try:
            profile = profiles.load(name)
        except ProfileError as exc:
            QMessageBox.warning(self, "Regenerate", str(exc))
            return
        self.profile = profile
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
        if self.client is None or not self.model.ticked_ids():
            return
        path = self._ask_export_path()
        if not path:
            return
        written = self._write_playlist(path)
        if written is not None:
            self.statusBar().showMessage(f"Wrote {written} channels to {path}")

    def _write_playlist(self, path: str) -> int | None:
        """Write the exportable ticks to `path`. Returns the channel count, or None on failure."""
        client = self.client
        ext = self.format_combo.currentText()
        epg = self.epg_check.isChecked()
        entries = build_entries(
            self.model.groups(), self.model.ticked_ids(), lambda ch: client.stream_url(ch.stream_id, ext)
        )
        try:
            write_m3u(path, entries, client.epg_url() if epg else None)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write {path}:\n{exc}")
            return None
        self._remember_export(path, ext, epg)
        return len(entries)

    # -- closing -------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard("quit"):
            event.accept()
        else:
            event.ignore()


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Bonsai")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
