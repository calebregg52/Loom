from __future__ import annotations

import random
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import genres, journey, publish
from .models import TrackTableModel
from .theme import ACCENT, BORDER, MUTED, TEXT


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


class BpmChart(QWidget):
    """Line of each song's BPM along the journey, over the dashed target curve."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(170)
        self.bpms: list[float] = []
        self.targets: list[float] = []
        self.estimated: list[bool] = []
        self.album: dict[str, Any] | None = None

    def set_data(self, bpms, targets, estimated, album) -> None:
        self.bpms, self.targets, self.estimated, self.album = bpms, targets, estimated, album
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(44, 14, -16, -26)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.NoBrush)
        if not self.bpms:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Build a journey to see its tempo curve")
            return
        values = self.bpms + self.targets + ([self.album["avg_bpm"]] if self.album else [])
        lo, hi = min(values) - 8, max(values) + 8
        slots = len(self.bpms) + (1 if self.album else 0)

        def pt(i: int, v: float) -> QPointF:
            x = r.left() + (r.width() * i / max(slots - 1, 1))
            y = r.bottom() - (v - lo) / (hi - lo) * r.height()
            return QPointF(x, y)

        small = QFont(self.font())
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1.5))
        p.setFont(small)
        for frac in (0, 0.5, 1):  # gridlines + BPM labels
            v = lo + (hi - lo) * frac
            y = r.bottom() - frac * r.height()
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y - 8, 38, 16), Qt.AlignRight | Qt.AlignVCenter, f"{v:.0f}")

        if self.album:  # shaded album zone at the end
            x0 = pt(len(self.bpms) - 0.5, lo).x()
            p.fillRect(QRectF(x0, r.top(), r.right() - x0 + 8, r.height()), QColor(124, 108, 255, 28))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(x0, r.bottom() + 4, r.right() - x0 + 16, 18), Qt.AlignCenter, "album")

        if self.targets:
            pen = QPen(QColor(MUTED), 1.4, Qt.DashLine)
            p.setPen(pen)
            path = QPainterPath(pt(0, self.targets[0]))
            for i, v in enumerate(self.targets[1:], start=1):
                path.lineTo(pt(i, v))
            p.drawPath(path)

        line = [pt(i, v) for i, v in enumerate(self.bpms)]
        if self.album:
            line.append(pt(len(self.bpms), self.album["avg_bpm"]))
        p.setPen(QPen(QColor(ACCENT), 2.4))
        path = QPainterPath(line[0])
        for q in line[1:]:
            path.lineTo(q)
        p.drawPath(path)
        for i, q in enumerate(line):
            est = i < len(self.estimated) and self.estimated[i]
            p.setPen(QPen(QColor(ACCENT), 1.6))
            p.setBrush(QColor("#151821") if est else QColor(ACCENT))
            p.drawEllipse(q, 3.6, 3.6)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(r.left(), r.bottom() + 4, 80, 18), Qt.AlignLeft, "start")
        p.drawText(QRectF(r.left(), r.top() - 14, r.width(), 14), Qt.AlignRight,
                   "— your songs   - - target   ○ estimated BPM")


class JourneyPage(QWidget):
    save_requested = Signal(dict, str)  # journey, playlist name

    def __init__(self):
        super().__init__()
        self.data: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 32, 36, 24)
        lay.setSpacing(6)
        lay.addWidget(_label("Journeys", "pageTitle"))
        lay.addWidget(_label(
            "A playlist that moves gradually: each song sits close to the last in tempo, genre and key, "
            "following the shape you pick, then opens into a full album.", "pageSubtitle", wrap=True))
        lay.addSpacing(14)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.source = QComboBox()
        self.family = QComboBox()
        self.shape = QComboBox()
        for key, text in journey.SHAPES.items():
            self.shape.addItem(text, key)
        self.length = QSpinBox()
        self.length.setRange(8, 120)
        self.length.setValue(25)
        self.length.setSuffix(" songs")
        self.album = QCheckBox("End with a full album")
        self.album.setChecked(True)
        for text, w in (("From", self.source), ("Genre", self.family), ("Shape", self.shape)):
            row.addWidget(_label(text, "muted"))
            row.addWidget(w)
        row.addWidget(self.length)
        row.addWidget(self.album)
        row.addStretch(1)
        self.build_btn = QPushButton("Build journey")
        self.build_btn.setObjectName("primary")
        self.again_btn = QPushButton("Try another")
        row.addWidget(self.again_btn)
        row.addWidget(self.build_btn)
        lay.addLayout(row)
        lay.addSpacing(10)

        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 8)
        self.chart = BpmChart()
        cv.addWidget(self.chart)
        lay.addWidget(card)
        lay.addSpacing(8)

        self.summary = _label("", "pageSubtitle", wrap=True)
        lay.addWidget(self.summary)
        self.model = TrackTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setMinimumSectionSize(48)
        for col in range(self.model.columnCount()):
            h.setSectionResizeMode(col, QHeaderView.Stretch if col in (1, 2) else QHeaderView.ResizeToContents)
        for col in (7, 9):
            self.table.setColumnHidden(col, True)
        lay.addWidget(self.table, 1)
        self.album_line = _label("", wrap=True)
        self.album_line.setTextFormat(Qt.RichText)
        lay.addWidget(self.album_line)

        foot = QHBoxLayout()
        self.status = _label("", "muted", wrap=True)
        foot.addWidget(self.status, 1)
        self.name = QLineEdit()
        self.name.setFixedWidth(300)
        self.name.setPlaceholderText("Playlist name")
        self.save_btn = QPushButton("Save to Spotify")
        self.save_btn.setObjectName("primary")
        self.save_btn.setMinimumWidth(160)
        foot.addWidget(self.name)
        foot.addWidget(self.save_btn)
        lay.addSpacing(8)
        lay.addLayout(foot)

        self.build_btn.clicked.connect(lambda: self.rebuild(seed=None))
        self.again_btn.clicked.connect(lambda: self.rebuild(seed=random.randint(1, 10_000)))
        self.source.currentIndexChanged.connect(self._fill_families)
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setEnabled(False)
        self.again_btn.setEnabled(False)

    def set_library(self, data: dict[str, Any] | None) -> None:
        self.data = data
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        if data:
            self.source.addItem("All my songs", "all")
            self.source.addItem("Liked Songs", "liked")
            for p in data["playlists"]:
                if not p["name"].startswith(publish.PREFIX):
                    self.source.addItem(f"Playlist: {p['name']}", p["id"])
            self.source.setCurrentIndex(max(self.source.findData(current), 0))
        self.source.blockSignals(False)
        self._fill_families()

    def _entries(self):
        return genres.source_entries(self.data, self.source.currentData()) if self.data else []

    def _fill_families(self) -> None:
        current = self.family.currentData()
        self.family.clear()
        self.family.addItem("Any genre", None)
        if self.data:
            for fam, n in journey.family_options(self.data, self._entries()):
                if n >= 8:
                    self.family.addItem(f"{fam} ({n})", fam)
        self.family.setCurrentIndex(max(self.family.findData(current), 0))

    def rebuild(self, seed: int | None) -> None:
        if not self.data:
            return
        self.result = j = journey.build(
            self.data, self._entries(), self.shape.currentData(), self.length.value(),
            genre_family=self.family.currentData(), end_with_album=self.album.isChecked(), seed=seed,
        )
        tracks = self.data["tracks"]
        chain = [tracks[t] for t in j["track_ids"]]
        self.chart.set_data([journey.tempo(t) for t in chain], j["targets"],
                            [bool((t.get("features") or {}).get("estimated")) for t in chain], j["album"])
        self.model.set_rows([{"id": t} for t in j["track_ids"]], self.data)
        self.again_btn.setEnabled(bool(chain))
        self.save_btn.setEnabled(bool(chain))
        if not chain:
            self.summary.setText("No songs with BPM here yet. Sync, or pick a bigger source.")
            self.album_line.setText("")
            return
        ms = sum(t["duration_ms"] for t in chain)
        first, last = journey.tempo(chain[0]), journey.tempo(chain[-1])
        self.summary.setText(
            f"{len(chain)} songs · {ms // 60000} min · {first:.0f} → {last:.0f} BPM"
            + (f" · {j['skipped']:,} songs left out because they have no BPM" if j["skipped"] else "")
        )
        if j["album"]:
            a = j["album"]
            self.album_line.setText(
                f"<span style='color:{MUTED}'>THEN THE FULL ALBUM</span>&nbsp;&nbsp;"
                f"<b style='color:{TEXT}'>{a['name']}</b> by {a['artist']} "
                f"<span style='color:{MUTED}'>· ~{a['avg_bpm']:.0f} BPM · you have "
                f"{len(a['known_track_ids'])} of its songs</span>")
        else:
            self.album_line.setText("")
        fam = self.family.currentData()
        self.name.setText(f"Journey · {self.shape.currentText().split(' (')[0]}" + (f" · {fam}" if fam else ""))
        self.status.setText("")

    def _save(self) -> None:
        if self.result and self.result["track_ids"]:
            name = self.name.text().strip() or "Journey"
            if not name.startswith(publish.PREFIX):
                name = publish.playlist_name(name)
            self.save_requested.emit(self.result, name)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.save_btn.setEnabled(not busy and bool(self.result and self.result["track_ids"]))
        self.build_btn.setEnabled(not busy)
        self.again_btn.setEnabled(not busy)
        self.status.setText(message)
