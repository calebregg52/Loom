from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from ..enrich import key_label, track_genres
from .theme import MUTED

COLUMNS = ["#", "TITLE", "ARTIST", "GENRE", "BPM", "ENERGY", "KEY", "YEAR", "LENGTH", "ADDED"]
SORT_ROLE = Qt.UserRole + 1


def fmt_duration(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _genre_estimated(library: dict[str, Any], track: dict[str, Any]) -> bool:
    artists = library.get("artists", {})
    sources = [(artists.get(a.get("id")) or {}).get("genre_source") for a in track["artists"]]
    real = [s for s in sources if s and s != "estimated"]
    return bool(sources) and not real and "estimated" in sources


class TrackTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[int, dict[str, Any], str, str, bool]] = []
        self._notes: list[tuple[str, str]] = []  # per row: (tooltip, color for the # column)

    def set_notes(self, notes: list[tuple[str, str]]) -> None:
        self._notes = notes
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, len(COLUMNS) - 1))

    def set_rows(self, entries: list[dict[str, Any]], library: dict[str, Any]) -> None:
        tracks = library["tracks"]
        self.beginResetModel()
        self._notes = []
        self._rows = [
            (i, tracks[e["id"]], (e.get("added_at") or "")[:10],
             ", ".join(track_genres(library, tracks[e["id"]])),
             _genre_estimated(library, tracks[e["id"]]))
            for i, e in enumerate(entries, start=1)
            if e["id"] in tracks
        ]
        self.endResetModel()

    def track_id(self, row: int) -> str:
        return self._rows[row][1]["id"]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        n, t, added, genre, genre_guess = self._rows[index.row()]
        f = t.get("features") or {}
        col = index.column()
        if role == Qt.DisplayRole:
            return [
                str(n),
                t["name"],
                ", ".join(a["name"] for a in t["artists"]),
                ("~" if genre_guess and genre else "") + genre.split(", ")[0],
                (("~" if f.get("estimated") else "") + f"{f['tempo']:.0f}") if f.get("tempo") else "",
                f"{f['energy'] * 100:.0f}" if f.get("energy") is not None else "",
                key_label(f),
                t["album"]["release_date"][:4],
                fmt_duration(t["duration_ms"]),
                added,
            ][col]
        if role == SORT_ROLE:
            numeric = {0: n, 4: f.get("tempo"), 5: f.get("energy"), 8: t["duration_ms"]}
            if col in numeric:
                return numeric[col] if numeric[col] is not None else -1
            return (self.data(index, Qt.DisplayRole) or "").lower()
        note = self._notes[index.row()] if index.row() < len(self._notes) else None
        if note and role == Qt.ToolTipRole and col in (0, 1, 2):
            return note[0]
        if note and role == Qt.ForegroundRole and col == 0:
            return QColor(note[1])
        if role == Qt.ToolTipRole and col == 1:
            return f"{t['name']} · {t['album']['name']}"
        if role == Qt.ToolTipRole and col == 3 and genre:
            return genre
        if role == Qt.ToolTipRole and col == 5 and f:
            return (f"Energy {f.get('energy', 0):.2f} · Danceability {f.get('danceability', 0):.2f} · "
                    f"Mood (valence) {f.get('valence', 0):.2f} · Acoustic {f.get('acousticness', 0):.2f}")
        if role == Qt.ToolTipRole and col == 4 and f.get("tempo"):
            return {"reccobeats": "BPM from ReccoBeats", "deezer": "BPM from Deezer",
                    "artist average": "Estimate: average BPM of this artist's other songs"}.get(f.get("source"), "")
        if role == Qt.ToolTipRole and col == 3 and genre_guess:
            return f"Estimate from your playlists: {genre}"
        if role == Qt.ForegroundRole and col == 4 and f.get("estimated"):
            return QColor(MUTED)
        if role == Qt.ForegroundRole and col in (0, 3, 6, 7, 8, 9):
            return QColor(MUTED)
        if role == Qt.TextAlignmentRole and col in (0, 4, 5, 7, 8):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class TrackFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self._needle = ""

    def set_needle(self, text: str) -> None:
        self._needle = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self._needle:
            return True
        model = self.sourceModel()
        return any(
            self._needle in (model.index(row, c, parent).data() or "").lower() for c in (1, 2, 3)  # title, artist, genre
        )
