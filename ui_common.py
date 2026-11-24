"""Formatting helpers and painted icons (no image files needed)."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)


def fmt_size(n) -> str:
    if n is None or n < 0:
        return "Unknown"
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{n} Bytes"


def fmt_rate(bps) -> str:
    if not bps or bps < 1:
        return ""
    return fmt_size(bps) + "/sec"


def fmt_time(sec) -> str:
    if sec is None or sec < 0:
        return ""
    sec = int(sec)
    if sec < 60:
        return f"{sec} sec"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} min {s} sec"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} hour(s) {m} min"
    d, h = divmod(h, 24)
    return f"{d} day(s) {h} hour(s)"


def fmt_date(ts) -> str:
    if not ts:
        return ""
    return time.strftime("%b %d %H:%M:%S %Y", time.localtime(ts))


# ---------------------------------------------------------------- icons
CAT_COLORS = {
    "Compressed": "#c98a1b", "Documents": "#2f6fc7", "Music": "#9b3fc4",
    "Programs": "#3c8f3c", "Video": "#c73b3b", "General": "#6b7785",
}

_cache: dict = {}


def _canvas(size=48):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def _disc(p, color, size=48):
    g = QLinearGradient(0, 0, 0, size)
    c = QColor(color)
    g.setColorAt(0, c.lighter(135))
    g.setColorAt(1, c.darker(115))
    p.setBrush(QBrush(g))
    p.setPen(QPen(c.darker(140), 1.5))
    p.drawEllipse(QRectF(3, 3, size - 6, size - 6))


def tool_icon(kind: str) -> QIcon:
    if kind in _cache:
        return _cache[kind]
    pm, p = _canvas()
    white = QColor("white")
    if kind == "add":
        _disc(p, "#2e8b3d")
        p.setPen(QPen(white, 6))
        p.drawLine(24, 13, 24, 35)
        p.drawLine(13, 24, 35, 24)
    elif kind == "resume":
        _disc(p, "#2e7bd6")
        p.setBrush(white); p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(19, 13), QPointF(36, 24), QPointF(19, 35)]))
    elif kind == "stop":
        _disc(p, "#d38a12")
        p.setBrush(white); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(16, 16, 16, 16), 2, 2)
    elif kind == "stopall":
        _disc(p, "#c4621a")
        p.setBrush(white); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(12, 15, 10, 18), 2, 2)
        p.drawRoundedRect(QRectF(26, 15, 10, 18), 2, 2)
    elif kind == "delete":
        _disc(p, "#cc3333")
        p.setPen(QPen(white, 6))
        p.drawLine(16, 16, 32, 32)
        p.drawLine(32, 16, 16, 32)
    elif kind == "delcomplete":
        _disc(p, "#8a3fb0")
        p.setPen(QPen(white, 5)); p.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(13, 25)); path.lineTo(21, 33); path.lineTo(35, 15)
        p.drawPath(path)
    elif kind == "options":
        _disc(p, "#566573")
        p.setPen(QPen(white, 5))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(24 + 9 * math.cos(a), 24 + 9 * math.sin(a)),
                       QPointF(24 + 15 * math.cos(a), 24 + 15 * math.sin(a)))
        p.setPen(QPen(white, 4)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(24, 24), 8, 8)
    elif kind == "scheduler":
        _disc(p, "#1f8f8f")
        p.setPen(QPen(white, 3)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(24, 24), 13, 13)
        p.setPen(QPen(white, 4))
        p.drawLine(24, 24, 24, 15)
        p.drawLine(24, 24, 31, 28)
    elif kind == "startq":
        _disc(p, "#2a9d55")
        p.setBrush(white); p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(24, 14), QPointF(36, 24), QPointF(24, 34)]))
        p.drawRect(QRectF(12, 15, 8, 4)); p.drawRect(QRectF(12, 22, 8, 4)); p.drawRect(QRectF(12, 29, 8, 4))
    elif kind == "stopq":
        _disc(p, "#b8462e")
        p.setBrush(white); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(24, 16, 12, 16), 2, 2)
        p.drawRect(QRectF(12, 15, 8, 4)); p.drawRect(QRectF(12, 22, 8, 4)); p.drawRect(QRectF(12, 29, 8, 4))
    elif kind == "app":
        g = QLinearGradient(0, 0, 0, 48)
        g.setColorAt(0, QColor("#3fb26b")); g.setColorAt(1, QColor("#1e7a43"))
        p.setBrush(g); p.setPen(QPen(QColor("#155c32"), 1.5))
        p.drawRoundedRect(QRectF(3, 3, 42, 42), 10, 10)
        p.setBrush(white); p.setPen(Qt.NoPen)
        p.drawRect(QRectF(20, 9, 8, 15))
        p.drawPolygon(QPolygonF([QPointF(12, 23), QPointF(36, 23), QPointF(24, 36)]))
        p.drawRect(QRectF(11, 37, 26, 3))
    p.end()
    icon = QIcon(pm)
    _cache[kind] = icon
    return icon


def file_icon(category: str, complete: bool) -> QIcon:
    key = ("file", category, complete)
    if key in _cache:
        return _cache[key]
    pm, p = _canvas(32)
    color = QColor(CAT_COLORS.get(category, "#6b7785"))
    path = QPainterPath()
    path.moveTo(6, 2); path.lineTo(20, 2); path.lineTo(27, 9); path.lineTo(27, 30); path.lineTo(6, 30)
    path.closeSubpath()
    p.setBrush(QColor("white")); p.setPen(QPen(QColor("#8592a0"), 1.2))
    p.drawPath(path)
    p.setBrush(color); p.setPen(Qt.NoPen)
    p.drawRect(QRectF(6.6, 17, 19.8, 9))
    p.setPen(QColor("white"))
    f = QFont("Segoe UI", 6); f.setBold(True); p.setFont(f)
    p.drawText(QRectF(6.6, 17, 19.8, 9), Qt.AlignCenter, category[:3].upper() if category != "General" else "FILE")
    if not complete:
        p.setBrush(QColor("#2e8b3d")); p.setPen(QPen(QColor("white"), 1))
        p.drawEllipse(QRectF(0, 0, 14, 14))
        p.setPen(Qt.NoPen); p.setBrush(QColor("white"))
        p.drawRect(QRectF(5.5, 2.5, 3, 5))
        p.drawPolygon(QPolygonF([QPointF(3, 7), QPointF(11, 7), QPointF(7, 11.5)]))
    p.end()
    icon = QIcon(pm)
    _cache[key] = icon
    return icon
