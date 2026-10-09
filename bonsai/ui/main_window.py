"""Bonsai main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
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

from .. import __version__
from ..api import XtreamClient
from ..m3u import build_entries, write_m3u
from ..models import AccountInfo, group_channels
from ..storage import (
    Config,
    ConfigError,
    load_config,
    load_settings,
    save_config,
    save_settings,
)
from .channel_tree import COL_ID, COL_NAME, COL_TICKED, ChannelTreeModel
from .workers import TaskRunner

# Expanding every category is cheap for a search hit list but slow for the whole catalogue.
AUTO_EXPAND_LIMIT = 2000


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Bonsai")
        self.resize(900, 720)

        self.client: XtreamClient | None = None
        self.account: AccountInfo | None = None
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

        if config_problem:
            self._set_status(config_problem, "error")
        elif self.config.is_complete:
            QTimer.singleShot(0, self.test_login)
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
        self.reload_btn = QPushButton("Reload channels")
        self.reload_btn.setEnabled(False)
        self.reload_btn.clicked.connect(self.load_channels)
        self.status_label = QLabel(wordWrap=True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.login_btn)
        btn_row.addWidget(self.reload_btn)
        btn_row.addWidget(self.status_label, 1)
        form.addRow(btn_row)
        for edit in (self.server_edit, self.user_edit, self.pass_edit):
            edit.returnPressed.connect(self.test_login)
        root.addWidget(login_box)

        # Filter row
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

        # Tree
        self.model = ChannelTreeModel(self)
        self.model.tickedChanged.connect(self._update_count)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TICKED, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_ID, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TICKED, 90)
        header.resizeSection(COL_ID, 90)
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
        self.count_label = QLabel()
        self.format_combo = QComboBox()
        self.format_combo.addItems(["ts", "m3u8"])
        self.format_combo.setToolTip("Stream URL extension. m3u8 is only offered if the account allows it.")
        self.epg_check = QCheckBox("Include EPG URL")
        self.epg_check.setToolTip("Adds x-tvg-url pointing at the provider's xmltv.php to the header line.")
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

    # -- status helpers ----------------------------------------------------

    def _set_status(self, text: str, kind: str = "info") -> None:
        colours = {"error": "#c0392b", "warn": "#b9770e", "ok": "#1e8449"}
        colour = colours.get(kind)
        self.status_label.setStyleSheet(f"color: {colour};" if colour else "")
        self.status_label.setText(text)

    def _on_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.login_btn.setEnabled(not busy)
        self.reload_btn.setEnabled(not busy and self.client is not None)

    def _update_count(self) -> None:
        n, cats = self.model.ticked_summary()
        total = self.model.total_channels()
        ch = "channel" if n == 1 else "channels"
        cat = "category" if cats == 1 else "categories"
        text = f"{n} {ch} selected in {cats} {cat}"
        if total:
            text += f"  (of {total})"
        self.count_label.setText(text)
        self.export_btn.setEnabled(n > 0 and self.client is not None)

    def _update_format_options(self) -> None:
        m3u8_item = self.format_combo.model().item(1)
        allowed = self.account is None or self.account.supports_m3u8
        m3u8_item.setEnabled(allowed)
        if not allowed and self.format_combo.currentText() == "m3u8":
            self.format_combo.setCurrentText("ts")

    # -- login and loading -------------------------------------------------

    def test_login(self) -> None:
        if self.tasks.busy:
            return
        try:
            client = XtreamClient(
                self.server_edit.text(),
                self.user_edit.text().strip(),
                self.pass_edit.text(),
                self.config.user_agent,
            )
        except ValueError as exc:
            self._set_status(str(exc), "error")
            return
        if not client.username or not client.password:
            self._set_status("Username and password are required.", "error")
            return
        self._set_status(f"Logging in to {client.server}…")
        self.tasks.run(
            client.account_info,
            lambda info: self._login_ok(client, info),
            lambda msg: self._set_status(msg, "error"),
            client.redact,
        )

    def _login_ok(self, client: XtreamClient, info: AccountInfo) -> None:
        self.client = client
        self.account = info
        expires = info.expires.astimezone().strftime("%Y-%m-%d") if info.expires else "no expiry"
        parts = [
            info.status + (" (trial)" if info.is_trial else ""),
            f"expires {expires}",
            f"{info.active_connections}/{info.max_connections} connections",
        ]
        if info.allowed_output_formats:
            parts.append("formats " + ", ".join(info.allowed_output_formats))
        self._set_status(" · ".join(parts), "ok" if info.is_active else "warn")
        self._update_format_options()

        self.config = Config(client.server, client.username, client.password, self.config.user_agent)
        try:
            save_config(self.config)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save config: {exc}")
        self.server_edit.setText(client.server)
        self.load_channels()

    def load_channels(self) -> None:
        client = self.client
        if client is None or self.tasks.busy:
            return
        self.statusBar().showMessage("Loading live categories and channels…")

        def fetch():
            return group_channels(client.live_categories(), client.live_streams())

        self.tasks.run(fetch, self._channels_loaded, self._load_failed, client.redact)

    def _channels_loaded(self, groups) -> None:
        self.model.set_groups(groups)
        self._apply_filter()
        n_cats = sum(1 for _, chans in groups if chans)
        self.statusBar().showMessage(
            f"Loaded {self.model.total_channels()} live channels in {n_cats} categories"
        )
        self._update_count()

    def _load_failed(self, msg: str) -> None:
        self.statusBar().showMessage("Loading channels failed")
        self._set_status(msg, "error")

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

    def export_playlist(self) -> None:
        client = self.client
        ticked = self.model.ticked_ids()
        if client is None or not ticked:
            return
        default = self.settings.last_export_path or str(Path.home() / "bonsai.m3u")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export playlist", default, "M3U playlist (*.m3u);;All files (*)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".m3u"

        ext = self.format_combo.currentText()
        epg = client.epg_url() if self.epg_check.isChecked() else None
        entries = build_entries(
            self.model.groups(), ticked, lambda ch: client.stream_url(ch.stream_id, ext)
        )
        try:
            write_m3u(path, entries, epg)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write {path}:\n{exc}")
            return

        self.settings.last_export_path = path
        self.settings.stream_format = ext
        self.settings.include_epg = self.epg_check.isChecked()
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self.statusBar().showMessage(f"Wrote {len(entries)} channels to {path}")


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Bonsai")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
