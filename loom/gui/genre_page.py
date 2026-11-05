from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import genres, publish
from .models import TrackFilter, TrackTableModel


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


class GenrePage(QWidget):
    """Preview genre groups and turn the ticked ones into Spotify playlists."""

    publish_requested = Signal(list, str)  # groups, source label

    def __init__(self):
        super().__init__()
        self.data: dict[str, Any] | None = None
        self.groups: list[dict[str, Any]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 32, 36, 24)
        lay.setSpacing(6)
        lay.addWidget(_label("Genre playlists", "pageTitle"))
        lay.addWidget(_label(
            "Loom sorts your songs by genre. Tick the groups you want and Loom makes a private "
            "playlist for each one. Your existing playlists are never changed.", "pageSubtitle", wrap=True))
        lay.addSpacing(14)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.source = QComboBox()
        self.mode = QComboBox()
        self.mode.addItem("Broad genres (Country, Rock…)", "broad")
        self.mode.addItem("Exact genres (Red Dirt, Texas Country…)", "exact")
        self.min_songs = QSpinBox()
        self.min_songs.setRange(1, 500)
        self.min_songs.setValue(8)
        self.min_songs.setPrefix("At least ")
        self.min_songs.setSuffix(" songs")
        for text, w in (("From", self.source), ("Group by", self.mode), ("", self.min_songs)):
            if text:
                controls.addWidget(_label(text, "muted"))
            controls.addWidget(w)
        controls.addStretch(1)
        lay.addLayout(controls)
        lay.addSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(16)

        left = QFrame()
        left.setObjectName("card")
        left.setFixedWidth(330)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 12, 12, 12)
        head = QHBoxLayout()
        head.addWidget(_label("GROUPS", "sectionLabel"))
        head.addStretch(1)
        self.all_btn = QPushButton("All")
        self.none_btn = QPushButton("None")
        for b in (self.all_btn, self.none_btn):
            b.setObjectName("nav")
            head.addWidget(b)
        lv.addLayout(head)
        self.list = QListWidget()
        self.list.setSpacing(1)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        lv.addWidget(self.list, 1)
        self.unsorted = _label("", "muted", wrap=True)
        lv.addWidget(self.unsorted)
        body.addWidget(left)

        right = QVBoxLayout()
        self.preview_title = _label("", "pageTitle")
        self.preview_title.setStyleSheet("font-size: 20px;")
        self.preview_sub = _label("", "pageSubtitle")
        right.addWidget(self.preview_title)
        right.addWidget(self.preview_sub)
        right.addSpacing(6)
        self.model = TrackTableModel(self)
        self.proxy = TrackFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col in range(self.model.columnCount()):
            h.setSectionResizeMode(col, QHeaderView.Stretch if col in (1, 2) else QHeaderView.ResizeToContents)
        h.setMinimumSectionSize(48)
        for col in (6, 7, 8, 9):  # key, year, length, added: less useful here
            self.table.setColumnHidden(col, True)
        right.addWidget(self.table, 1)
        body.addLayout(right, 1)
        lay.addLayout(body, 1)

        foot = QHBoxLayout()
        self.status = _label("", "muted", wrap=True)
        foot.addWidget(self.status, 1)
        self.create_btn = QPushButton("Create playlists")
        self.create_btn.setObjectName("primary")
        self.create_btn.setMinimumWidth(220)
        foot.addWidget(self.create_btn)
        lay.addSpacing(10)
        lay.addLayout(foot)

        self.source.currentIndexChanged.connect(self.rebuild)
        self.mode.currentIndexChanged.connect(self.rebuild)
        self.min_songs.valueChanged.connect(self.rebuild)
        self.list.currentRowChanged.connect(self._preview)
        self.list.itemChanged.connect(lambda _: self._update_button())
        self.all_btn.clicked.connect(lambda: self._tick_all(True))
        self.none_btn.clicked.connect(lambda: self._tick_all(False))
        self.create_btn.clicked.connect(self._create)

    # ---- data

    def set_library(self, data: dict[str, Any] | None) -> None:
        self.data = data
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        if data:
            self.source.addItem("Liked Songs", "liked")
            self.source.addItem("All my songs", "all")
            for p in data["playlists"]:
                if not p["name"].startswith(publish.PREFIX):
                    self.source.addItem(f"Playlist: {p['name']}", p["id"])
            index = self.source.findData(current)
            self.source.setCurrentIndex(max(index, 0))
        self.source.blockSignals(False)
        self.rebuild()

    def rebuild(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.groups = []
        if self.data:
            entries = genres.source_entries(self.data, self.source.currentData())
            self.groups, unsorted = genres.group_songs(
                self.data, entries, self.mode.currentData(), self.min_songs.value()
            )
            existing = {p["name"] for p in self.data["playlists"] if p.get("owned")}
            for g in self.groups:
                bpm = f" · ~{g['avg_bpm']:.0f} BPM" if g["avg_bpm"] else ""
                tag = "  ↻" if publish.playlist_name(g["name"]) in existing else ""
                item = QListWidgetItem(f"{g['name']}  ·  {len(g['track_ids'])} songs{bpm}{tag}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setToolTip("Top artists: " + ", ".join(g["top_artists"])
                                + ("\n↻ Refills the Loom playlist you already have" if tag else ""))
                self.list.addItem(item)
            grouped = sum(len(g["track_ids"]) for g in self.groups)
            small = len(entries) - grouped - unsorted
            parts = []
            if unsorted:
                parts.append(f"{unsorted:,} songs have no genre")
            if small > 0:
                parts.append(f"{small:,} are in groups smaller than {self.min_songs.value()}")
            self.unsorted.setText(("Left out: " + "; ".join(parts) + ".") if parts else "")
        self.list.blockSignals(False)
        if self.groups:
            self.list.setCurrentRow(0)
        else:
            self.preview_title.setText("No groups yet")
            self.preview_sub.setText("Sync your library so Loom can read genres, or lower the minimum size.")
            self.model.set_rows([], {"tracks": {}})
        self._update_button()

    def _preview(self, row: int) -> None:
        if row < 0 or row >= len(self.groups) or not self.data:
            return
        g = self.groups[row]
        self.preview_title.setText(publish.playlist_name(g["name"]))
        self.preview_sub.setText(f"{len(g['track_ids'])} songs · top artists: {', '.join(g['top_artists'])}")
        self.model.set_rows([{"id": i} for i in g["track_ids"]], self.data)
        self.table.sortByColumn(0, Qt.AscendingOrder)

    def _tick_all(self, on: bool) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def chosen(self) -> list[dict[str, Any]]:
        return [g for i, g in enumerate(self.groups) if self.list.item(i).checkState() == Qt.Checked]

    def _update_button(self) -> None:
        n = len(self.chosen()) if self.groups else 0
        self.create_btn.setEnabled(n > 0)
        self.create_btn.setText(f"Create {n} playlist{'s' if n != 1 else ''} on Spotify" if n else "Create playlists")

    def _create(self) -> None:
        groups = self.chosen()
        if groups:
            self.publish_requested.emit(groups, self.source.currentText().replace("Playlist: ", ""))

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.create_btn.setEnabled(not busy and bool(self.chosen()))
        for w in (self.source, self.mode, self.min_songs, self.list):
            w.setEnabled(not busy)
        self.status.setText(message)
