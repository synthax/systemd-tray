from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui

try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover - optional component
    QSvgRenderer = None

def create_svg_icon(path: Path) -> Optional[QtGui.QIcon]:
    if QSvgRenderer is None or not path.exists():
        return None
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return None

    icon = QtGui.QIcon()
    for size in (16, 20, 24, 32, 48, 64, 96, 128):
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        renderer.render(painter, QtCore.QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pix)
    return icon

def icon_has_pixmaps(icon: Optional[QtGui.QIcon]) -> bool:
    if icon is None or icon.isNull():
        return False
    if icon.availableSizes():
        return True
    pix = icon.pixmap(16, 16)
    return not pix.isNull()
