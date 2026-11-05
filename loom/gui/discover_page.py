"""Discover: find popular songs outside your library (e.g. 80s rock), then save or journey them."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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

from .. import discover
from .models import TrackTableModel
from .theme import MUTED


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


class DiscoverPage(QWidget):
    search_requested = Signal(str, str, int, bool)  # genre, decade ("" = any), how many, skip owned
    save_requested = Signal(str, list)              # playlist name, track ids
    journey_requested = Signal()

    def __init__(self):
        super().__init__()
        self.result: dict[str, Any] | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 32, 36, 24)
        lay.setSpacing(6)
        lay.addWidget(_label("Discover", "pageTitle"))
        lay.addWidget(_label(
            "Find popular songs that aren't in your library yet, like 80s rock. Loom ranks them with "
            "Last.fm's charts and finds them on Spotify. Save them as a playlist, or build a journey "
            "from them.", "pageSubtitle", wrap=True))
        lay.addSpacing(14)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.genre = QComboBox()
        self.genre.setEditable(True)
        self.genre.addItems(discover.GENRE_SUGGESTIONS)
        self.genre.setCurrentText("rock")
        self.genre.setMinimumWidth(200)
        self.genre.lineEdit().setPlaceholderText("any genre")
        self.decade = QComboBox()
        self.decade.addItem("Any decade", "")
        for d in reversed(list(discover.DECADES)):
            self.decade.addItem(d, d)
        self.decade.setCurrentIndex(self.decade.findData("1980s"))
        self.count = QSpinBox()
        self.count.setRange(10, 200)
        self.count.setSingleStep(10)
        self.count.setValue(50)
        self.count.setSuffix(" songs")
        self.skip = QCheckBox("Skip songs I already have")
        self.skip.setChecked(True)
        for text, w in (("Genre", self.genre), ("Decade", self.decade)):
            row.addWidget(_label(text, "muted"))
            row.addWidget(w)
        row.addWidget(self.count)
        row.addWidget(self.skip)
        row.addStretch(1)
        self.find_btn = QPushButton("Find songs")
        self.find_btn.setObjectName("primary")
        row.addWidget(self.find_btn)
        lay.addLayout(row)
        lay.addSpacing(10)

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
        self.table.setColumnHidden(9, True)  # "added" means nothing for songs you don't have
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.status = _label("", "muted", wrap=True)
        foot.addWidget(self.status, 1)
        self.journey_btn = QPushButton("Build a journey from these")
        self.name = QLineEdit()
        self.name.setFixedWidth(280)
        self.name.setPlaceholderText("Playlist name")
        self.save_btn = QPushButton("Save to Spotify")
        self.save_btn.setObjectName("primary")
        self.save_btn.setMinimumWidth(160)
        foot.addWidget(self.journey_btn)
        foot.addWidget(self.name)
        foot.addWidget(self.save_btn)
        lay.addSpacing(8)
        lay.addLayout(foot)

        self.find_btn.clicked.connect(self._find)
        self.genre.lineEdit().returnPressed.connect(self._find)
        self.save_btn.clicked.connect(self._save)
        self.journey_btn.clicked.connect(self.journey_requested.emit)
        self._set_actions(False)
        self.summary.setText("Pick a genre and a decade, then Find songs.")

    def _set_actions(self, on: bool) -> None:
        self.save_btn.setEnabled(on)
        self.journey_btn.setEnabled(on)

    def _find(self) -> None:
        genre = self.genre.currentText().strip()
        decade = self.decade.currentData() or ""
        if not genre and not decade:
            self.status.setText("Pick a genre, a decade, or both.")
            return
        self.search_requested.emit(genre, decade, self.count.value(), self.skip.isChecked())

    def _save(self) -> None:
        if self.result and self.result["order"]:
            self.save_requested.emit(self.name.text().strip() or f"Discover · {self.result['label']}",
                                     list(self.result["order"]))

    def set_result(self, view: dict[str, Any] | None, result: dict[str, Any] | None) -> None:
        """view: the library merged with the result (discover.merged), so the table can show it."""
        self.result = result
        if not result or not view:
            self.model.set_rows([], view or {"tracks": {}, "artists": {}})
            self._set_actions(False)
            return
        self.model.set_rows(discover.entries(result), view)
        found = len(result["order"])
        with_bpm = sum(1 for t in result["order"] if (result["tracks"][t].get("features") or {}).get("tempo"))
        via = result.get("counts", {})
        parts = [f"{found} songs for “{result['label']}”", f"{with_bpm} with BPM"]
        if via.get("lastfm"):
            parts.append("ranked by Last.fm popularity")
        self.summary.setText(" · ".join(parts))
        self.name.setText(f"Discover · {result['label']}")
        problems = result.get("problems") or []
        self.status.setText(" ".join(problems) if problems else "")
        self.status.setStyleSheet(f"color:{MUTED}")
        self._set_actions(found > 0)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.find_btn.setEnabled(not busy)
        self._set_actions(not busy and bool(self.result and self.result["order"]))
        self.status.setText(message)
