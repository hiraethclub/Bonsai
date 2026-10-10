"""Help > About Bonsai (in the same style as ByteNoise's About box, with the app icon)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from .. import __version__
from .icon import icon_pixmap

AUTHOR = "Aisling de Gr&aacute;s"
EMAIL = "aisling@hiraeth.club"
THREADS = "hiraeth.clwb"


def about_text() -> str:
    return (
        f"<b>Bonsai {__version__}</b><br><br>"
        "Trim a big IPTV channel list down to a small playlist.<br><br>"
        "Xtream Codes accounts and M3U playlists, filters for resolution, "
        "content and region, named profiles, and stream testing with "
        "a PS Vita compatibility check.<br><br>"
        f"Created by {AUTHOR}<br>"
        f'<a href="mailto:{EMAIL}">{EMAIL}</a><br>'
        f'Threads: <a href="https://www.threads.com/@{THREADS}">@{THREADS}</a><br><br>'
        "Free software under the GNU General Public License v3."
    )


def make_about_box(parent: QWidget | None = None) -> QMessageBox:
    msg = QMessageBox(parent)
    msg.setWindowTitle("About Bonsai")
    msg.setIconPixmap(icon_pixmap(96))
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setText(about_text())
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    # Make the links clickable.
    inner_label = msg.findChild(QLabel, "qt_msgbox_label")
    if inner_label is not None:
        inner_label.setOpenExternalLinks(True)
        inner_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return msg


def show_about(parent: QWidget | None = None) -> None:
    make_about_box(parent).exec()
