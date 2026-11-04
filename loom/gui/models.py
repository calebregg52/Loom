from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

COLUMNS = ["#", "TITLE", "ARTIST", "ALBUM", "YEAR", "LENGTH", "ADDED"]
SORT_ROLE = Qt.UserRole + 1


def fmt_duration(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class TrackTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[int, dict[str, Any], str]] = []

    def set_rows(self, entries: list[dict[str, Any]], tracks: dict[str, dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = [
            (i, tracks[e["id"]], (e.get("added_at") or "")[:10])
            for i, e in enumerate(entries, start=1)
            if e["id"] in tracks
        ]
        self.endResetModel()

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
        n, t, added = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return [
                str(n),
                t["name"],
                ", ".join(a["name"] for a in t["artists"]),
                t["album"]["name"],
                t["album"]["release_date"][:4],
                fmt_duration(t["duration_ms"]),
                added,
            ][col]
        if role == SORT_ROLE:
            if col == 0:
                return n
            if col == 5:
                return t["duration_ms"]
            return (self.data(index, Qt.DisplayRole) or "").lower()
        if role == Qt.ForegroundRole and col in (0, 4, 5, 6):
            from PySide6.QtGui import QColor

            from .theme import MUTED

            return QColor(MUTED)
        if role == Qt.TextAlignmentRole and col in (0, 4, 5):
            return int(Qt.AlignRight | Qt.AlignVCenter) if col != 4 else int(Qt.AlignCenter)
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
            self._needle in (model.index(row, c, parent).data() or "").lower() for c in (1, 2, 3)
        )
