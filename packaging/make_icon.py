"""Render the application icon as PNG and ICO without extra dependencies.

Qt draws the pixmap; an ICO file is just a small header followed by a PNG
payload, which is why no imaging library is needed.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def render_png(size: int) -> bytes:
    """Draw the cp2kbuild mark and return PNG bytes."""
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#1f4e79"))
    radius = size * 0.22
    inset = max(1.0, size * 0.02)
    painter.drawRoundedRect(int(inset), int(inset),
                            int(size - 2 * inset), int(size - 2 * inset),
                            radius, radius)
    painter.setPen(QColor("#ffffff"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.44))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "CP")
    painter.end()

    # the QByteArray must outlive the QBuffer: passing a temporary here crashes
    # the interpreter with an access violation
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.WriteOnly)
    if not pixmap.save(buffer, "PNG"):
        raise RuntimeError("could not encode the icon as PNG")
    buffer.close()
    return bytes(storage)


def write_ico(path: Path, sizes: tuple[int, ...] = (16, 32, 48, 64, 128, 256)) -> None:
    """Write a multi-size ICO whose entries embed PNG data."""
    images = [(size, render_png(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    payload = b""
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        payload += data
    path.write_bytes(header + entries + payload)


def count_text_pixels(size: int = 256) -> int:
    """How many near-white pixels the mark has; zero means no font was found."""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    storage = QByteArray(render_png(size))
    buffer = QBuffer(storage)
    buffer.open(QBuffer.ReadOnly)
    image = QImage()
    image.loadFromData(storage, "PNG")
    buffer.close()
    if image.isNull():
        return -1
    total = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            colour = image.pixelColor(x, y)
            if colour.red() > 200 and colour.green() > 200 and colour.blue() > 200:
                total += 1
    return total


def main() -> int:
    # a real platform plugin has fonts; offscreen usually does not, which would
    # silently produce a blank icon, so try the native one first
    import os

    here = Path(__file__).resolve().parent
    from PySide6.QtGui import QGuiApplication

    app = None
    for platform in (os.environ.get("QT_QPA_PLATFORM"), None, "offscreen"):
        if platform:
            os.environ["QT_QPA_PLATFORM"] = platform
        else:
            os.environ.pop("QT_QPA_PLATFORM", None)
        try:
            app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        except Exception:  # noqa: BLE001 - try the next plugin
            continue
        if count_text_pixels() > 0:
            break
    if app is None:
        raise SystemExit("could not start a Qt GUI application")

    text_pixels = count_text_pixels()
    write_ico(here / "icon.ico")
    (here / "icon.png").write_bytes(render_png(256))
    print(f"wrote {here / 'icon.ico'} and {here / 'icon.png'}")
    if text_pixels <= 0:
        print("warning: no glyphs were rendered (no font available); "
              "the icon is a plain rounded square")
    else:
        print(f"icon mark verified: {text_pixels} light pixels sampled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
