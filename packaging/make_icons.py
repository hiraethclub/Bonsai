"""Regenerate the app icons from the two sources in packaging/icon/.

    bonsai-1024.png   the full artwork (transparent corners), used at 48 px and up
    bonsai-small.svg  a simplified drawing for 16-32 px, where the artwork's detail is lost

Writes bonsai/resources/icon-<size>.png (loaded by the app at runtime) and
packaging/icon/bonsai.ico (embedded in Bonsai.exe). Needs PySide6 and Pillow:

    pip install pillow
    python packaging/make_icons.py
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "packaging" / "icon"
OUT = ROOT / "bonsai" / "resources"
SMALL_SIZES = (16, 24, 32)
LARGE_SIZES = (48, 64, 128, 256, 512)


def _to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def render_small(svg: QSvgRenderer, size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    svg.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def main() -> int:
    QGuiApplication(sys.argv)
    art = QImage(str(SRC / "bonsai-1024.png"))
    svg = QSvgRenderer(str(SRC / "bonsai-small.svg"))
    if art.isNull() or not svg.isValid():
        print("Could not read the icon sources in packaging/icon/", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    images: dict[int, QImage] = {}
    for size in SMALL_SIZES:
        images[size] = render_small(svg, size)
    for size in LARGE_SIZES:
        images[size] = art.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
    for size, img in images.items():
        img.save(str(OUT / f"icon-{size}.png"))

    # Windows .ico with every size, so Explorer and the taskbar use the right one.
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [_to_pil(images[s]) for s in ico_sizes]
    frames[-1].save(SRC / "bonsai.ico", format="ICO", sizes=[(s, s) for s in ico_sizes],
                    append_images=frames[:-1])
    print(f"Wrote {len(images)} PNGs to {OUT.relative_to(ROOT)} and packaging/icon/bonsai.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
