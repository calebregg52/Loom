"""Small pictures of a journey's wave: in the sidebar next to each journey, and above its songs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStyledItemDelegate, QVBoxLayout, QWidget

from .. import journey
from .theme import ACCENT, BORDER, MUTED, TEXT

SHAPE_ROLE = Qt.UserRole + 2  # sidebar item data: {"points": [...] or None, "bpms": [...]}


# ---------------------------------------------------------------- the saved waves


def load_shapes(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_shape(path: Path, playlist_id: str, info: dict[str, Any]) -> None:
    """Keep the wave a journey was built from, keyed by its Spotify playlist id."""
    shapes = load_shapes(path)
    shapes[playlist_id] = info
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(shapes), encoding="utf-8")
    tmp.replace(path)


def playlist_bpms(library: dict[str, Any], playlist: dict[str, Any]) -> list[float]:
    tracks = library["tracks"]
    return [journey.tempo(tracks[e["id"]]) for e in playlist.get("tracks", [])
            if e["id"] in tracks and journey.tempo(tracks[e["id"]])]


def is_journey(playlist: dict[str, Any], shapes: dict[str, Any]) -> bool:
    from .. import publish

    return playlist["id"] in shapes or playlist["name"].startswith(publish.PREFIX + "Journey")


def _smooth(values: list[float], k: int) -> list[float]:
    if len(values) < 3 or k < 2:
        return list(values)
    half = k // 2
    return [sum(values[max(0, i - half):i + half + 1]) / len(values[max(0, i - half):i + half + 1])
            for i in range(len(values))]


# ---------------------------------------------------------------- drawing


def paint_wave(p: QPainter, rect: QRectF, points, bpms, *, smooth: int = 3, line_width: float = 1.6,
               dots: bool = False, faint: bool = False) -> None:
    """The drawn wave (dashed) and the songs' real BPM (solid), scaled to fit `rect`.

    The wave's 0..1 is mapped onto the songs' own BPM range, so the two line up."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    vals = _smooth([float(b) for b in bpms], smooth) if bpms else []
    lo = min(vals) if vals else 0.0
    hi = max(vals) if vals else 1.0
    if hi - lo < 4:
        lo, hi = lo - 2, hi + 2

    def at(x: float, y01: float) -> QPointF:
        return QPointF(rect.left() + x * rect.width(), rect.bottom() - y01 * rect.height())

    if points:
        path = QPainterPath()
        pts = journey.clean_points(points)
        for k in range(49):
            x = k / 48
            q = at(x, journey.curve_at(pts, x))
            path.moveTo(q) if k == 0 else path.lineTo(q)
        pen = QPen(QColor(MUTED if vals else ACCENT), 1.1 if vals else line_width, Qt.DashLine if vals else Qt.SolidLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    if len(vals) >= 2:
        n = len(vals)
        line = [at(i / (n - 1), (v - lo) / (hi - lo)) for i, v in enumerate(vals)]
        color = QColor(ACCENT)
        if faint:
            color.setAlpha(150)
        p.setPen(QPen(color, line_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(line[0])
        for q in line[1:]:
            path.lineTo(q)
        p.drawPath(path)
        if dots:
            p.setBrush(color)
            for q in line:
                p.drawEllipse(q, 2.2, 2.2)
    p.restore()


class SidebarDelegate(QStyledItemDelegate):
    """Draws a tiny wave at the right of each journey in the sidebar."""

    W, H = 54, 14

    def paint(self, painter, option, index) -> None:
        shape = index.data(SHAPE_ROLE)
        if not shape:
            super().paint(painter, option, index)
            return
        r = option.rect
        thumb = QRectF(r.right() - self.W - 8, r.center().y() - self.H / 2, self.W, self.H)
        option.rect = r.adjusted(0, 0, -(self.W + 14), 0)  # keep the name clear of the wave
        super().paint(painter, option, index)
        option.rect = r
        paint_wave(painter, thumb, shape.get("points"), shape.get("bpms"), smooth=3, line_width=1.4)

    def sizeHint(self, option, index) -> QSize:
        return super().sizeHint(option, index)


class WaveBanner(QWidget):
    """The journey's wave above its song list, with a button to edit it in Journeys."""

    edit_requested = Signal(list, str)  # points, journey name

    def __init__(self):
        super().__init__()
        self.points: list | None = None
        self.bpms: list[float] = []
        self.name = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.canvas = _Canvas(self)
        lay.addWidget(self.canvas, 1)
        side = QVBoxLayout()
        side.setSpacing(6)
        self.caption = QLabel("")
        self.caption.setObjectName("muted")
        self.caption.setWordWrap(True)
        self.caption.setFixedWidth(200)
        self.edit_btn = QPushButton("Edit this wave")
        self.edit_btn.setToolTip("Open this wave in Journeys to tweak it and build again")
        self.edit_btn.clicked.connect(self._edit)
        side.addWidget(self.caption)
        side.addWidget(self.edit_btn)
        side.addStretch(1)
        lay.addLayout(side)
        self.setFixedHeight(96)

    def set_wave(self, points, bpms, name: str, info: dict[str, Any] | None = None) -> None:
        self.points, self.bpms, self.name = points, list(bpms), name
        bits = []
        if bpms:
            bits.append(f"{bpms[0]:.0f} → {bpms[-1]:.0f} BPM, from {min(bpms):.0f} to {max(bpms):.0f}")
        if info and info.get("shape_label"):
            bits.append(f"Shape: {info['shape_label']}")
        if info and info.get("fit_score") is not None:
            bits.append(f"Fit {info['fit_score']}/100")
        if not points:
            bits.append("Drawn from the songs' BPM (made before Loom kept waves).")
        self.caption.setText("\n".join(bits))
        self.edit_btn.setEnabled(bool(points or bpms))
        self.canvas.update()

    def _edit(self) -> None:
        pts = self.points
        if not pts and self.bpms:  # rebuild a wave from the songs: 7 points along their BPM
            vals = _smooth(self.bpms, 5)
            lo, hi = min(vals), max(vals)
            span = (hi - lo) or 1.0
            n = len(vals)
            pts = [(k / 6, (vals[round(k / 6 * (n - 1))] - lo) / span) for k in range(7)]
        if pts:
            self.edit_requested.emit([list(p) for p in pts], self.name)


class _Canvas(QWidget):
    def __init__(self, banner: WaveBanner):
        super().__init__()
        self.banner = banner

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor("#151821"))
        p.drawRoundedRect(r, 10, 10)
        inner = r.adjusted(14, 12, -14, -12)
        b = self.banner
        if not b.points and not b.bpms:
            p.setPen(QColor(MUTED))
            p.drawText(r, Qt.AlignCenter, "No BPM data for these songs yet")
            return
        paint_wave(p, inner, b.points, b.bpms, smooth=1, line_width=2.0, dots=True)
        p.setPen(QColor(TEXT))
