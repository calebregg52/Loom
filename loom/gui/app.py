from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config, library
from .models import TrackFilter, TrackTableModel
from .theme import STYLESHEET

DASHBOARD_URL = "https://developer.spotify.com/dashboard"


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


# ---------------------------------------------------------------- background sync


class SyncWorker(QObject):
    progress = Signal(str, int, int)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, client_id: str):
        super().__init__()
        self.client_id = client_id

    def run(self) -> None:
        try:
            from ..spotify import make_client

            sp = make_client(self.client_id)
            self.progress.emit("Waiting for Spotify sign-in in your browser…", 0, 0)
            sp.current_user()  # triggers the browser login the first time
            data = library.fetch_library(sp, self.progress.emit)
            library.save(data, config.library_path())
            self.finished.emit(data)
        except Exception as exc:  # shown to the user, never crash the window
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- setup dialog


class SetupDialog(QDialog):
    def __init__(self, parent=None, client_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Connect Spotify")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(label("Connect your Spotify", "pageTitle"))
        steps = (
            f"1. Open the <a style='color:#9286ff' href='{DASHBOARD_URL}'>Spotify developer dashboard</a> "
            "and create an app (any name).<br>"
            "2. Under <b>Redirect URIs</b>, add the address below and tick <b>Web API</b>.<br>"
            "3. Copy the app's <b>Client ID</b> and paste it here. No client secret is needed."
        )
        info = label(steps, "muted", wrap=True)
        info.setOpenExternalLinks(True)
        info.setTextFormat(Qt.RichText)
        lay.addWidget(info)

        row = QHBoxLayout()
        redirect = QLineEdit(config.REDIRECT_URI)
        redirect.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(config.REDIRECT_URI))
        row.addWidget(redirect, 1)
        row.addWidget(copy)
        lay.addLayout(row)

        self.client_id = QLineEdit(client_id)
        self.client_id.setPlaceholderText("Client ID")
        lay.addWidget(self.client_id)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Connect")
        self.ok.setObjectName("primary")
        self.ok.clicked.connect(self.accept)
        self.ok.setEnabled(bool(client_id.strip()))
        self.client_id.textChanged.connect(lambda t: self.ok.setEnabled(len(t.strip()) >= 16))
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        lay.addLayout(buttons)

    def value(self) -> str:
        return self.client_id.text().strip()


# ---------------------------------------------------------------- pages


class StatCard(QFrame):
    def __init__(self, caption: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        self.value = label("–", "statValue")
        lay.addWidget(self.value)
        lay.addWidget(label(caption, "statLabel"))


class OverviewPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 32, 36, 32)
        lay.setSpacing(20)
        self.title = label("Your library", "pageTitle")
        self.subtitle = label("", "pageSubtitle")
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {
            key: StatCard(caption)
            for key, caption in [
                ("tracks", "unique songs"),
                ("playlists", "playlists"),
                ("liked", "liked songs"),
                ("artists", "artists"),
                ("hours", "hours of music"),
            ]
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, 0, i)
        lay.addLayout(grid)

        self.top_card = QFrame()
        self.top_card.setObjectName("card")
        top = QVBoxLayout(self.top_card)
        top.setContentsMargins(20, 18, 20, 18)
        top.addWidget(label("MOST COLLECTED ARTISTS", "sectionLabel"))
        self.top_list = QGridLayout()
        self.top_list.setHorizontalSpacing(24)
        self.top_list.setVerticalSpacing(8)
        top.addLayout(self.top_list)
        lay.addWidget(self.top_card)

        self.note = label("", "muted", wrap=True)
        lay.addWidget(self.note)
        lay.addStretch(1)

    def show_library(self, data: dict[str, Any]) -> None:
        s = library.summary(data)
        when = datetime.fromisoformat(data["fetched_at"]).astimezone().strftime("%b %d, %Y at %H:%M")
        self.title.setText(f"{data['user']['name']}'s library")
        self.subtitle.setText(f"Last synced {when}")
        for key, card in self.cards.items():
            v = s[key]
            card.value.setText(f"{v:,.1f}" if key == "hours" else f"{v:,}")
        while self.top_list.count():
            self.top_list.takeAt(0).widget().deleteLater()
        biggest = s["top_artists"][0][1] if s["top_artists"] else 1
        for i, (name, count) in enumerate(s["top_artists"]):
            col = (i // 5) * 3
            r = i % 5
            num = label(f"{i + 1}", "muted")
            num.setFixedWidth(22)
            self.top_list.addWidget(num, r, col)
            self.top_list.setColumnStretch(col + 1, 1)
            self.top_list.addWidget(label(name), r, col + 1)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setRange(0, biggest)
            bar.setValue(count)
            bar.setFixedWidth(120)
            bar.setToolTip(f"{count} songs")
            self.top_list.addWidget(bar, r, col + 2)
        skipped = data.get("skipped_playlists") or []
        self.note.setText(
            f"Spotify didn't let Loom read {len(skipped)} playlist(s): {', '.join(skipped)}."
            if skipped
            else ""
        )


class TrackPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 32, 36, 16)
        lay.setSpacing(6)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.title = label("", "pageTitle")
        self.subtitle = label("", "pageSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist or album")
        self.search.setFixedWidth(280)
        self.search.setClearButtonEnabled(True)
        head.addWidget(self.search, 0, Qt.AlignBottom)
        lay.addLayout(head)
        lay.addSpacing(14)

        self.model = TrackTableModel(self)
        self.proxy = TrackFilter(self)
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(self.proxy.set_needle)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setWordWrap(False)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col, mode in enumerate(
            [QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.Stretch, QHeaderView.Stretch,
             QHeaderView.ResizeToContents, QHeaderView.ResizeToContents, QHeaderView.ResizeToContents]
        ):
            h.setSectionResizeMode(col, mode)
        lay.addWidget(self.table, 1)

    def show_tracks(self, title: str, subtitle: str, entries, tracks) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.search.clear()
        self.model.set_rows(entries, tracks)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.scrollToTop()


class EmptyPage(QWidget):
    connect_clicked = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addWidget(label("Nothing woven yet", "pageTitle"), 0, Qt.AlignHCenter)
        sub = label("Sync your Spotify library to see your playlists and liked songs here.", "pageSubtitle")
        lay.addWidget(sub, 0, Qt.AlignHCenter)
        lay.addSpacing(18)
        btn = QPushButton("Connect Spotify")
        btn.setObjectName("primary")
        btn.setFixedWidth(200)
        btn.clicked.connect(self.connect_clicked)
        lay.addWidget(btn, 0, Qt.AlignHCenter)
        lay.addStretch(2)


# ---------------------------------------------------------------- main window


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Loom")
        self.resize(1240, 780)
        self.data: dict[str, Any] | None = None
        self._thread: QThread | None = None

        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        self.empty = EmptyPage()
        self.empty.connect_clicked.connect(self.sync)
        self.overview = OverviewPage()
        self.tracks = TrackPage()
        for page in (self.empty, self.overview, self.tracks):
            self.stack.addWidget(page)
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.set_library(library.load(config.library_path()))

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(272)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 22, 16, 16)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(label("loom", "wordmark"))
        brand.addWidget(label(".", "wordmarkDot"))
        brand.addStretch(1)
        brand.addWidget(label(f"v{__version__}", "muted"), 0, Qt.AlignBottom)
        lay.addLayout(brand)
        lay.addSpacing(18)

        self.nav = QButtonGroup(self)
        self.nav_overview = self._nav_button("Overview", self.show_overview)
        self.nav_liked = self._nav_button("Liked Songs", self.show_liked)
        lay.addWidget(self.nav_overview)
        lay.addWidget(self.nav_liked)
        lay.addSpacing(14)
        lay.addWidget(label("  PLAYLISTS", "sectionLabel"))
        lay.addSpacing(4)

        self.playlist_list = QListWidget()
        self.playlist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist_list.currentRowChanged.connect(self.show_playlist)
        lay.addWidget(self.playlist_list, 1)

        lay.addSpacing(8)
        self.status = label("Not synced yet", "muted", wrap=True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.sync_btn = QPushButton("Sync library")
        self.sync_btn.setObjectName("primary")
        self.sync_btn.clicked.connect(self.sync)
        account = QPushButton("Spotify app settings")
        account.clicked.connect(self.edit_settings)
        lay.addWidget(self.status)
        lay.addWidget(self.bar)
        lay.addSpacing(6)
        lay.addWidget(self.sync_btn)
        lay.addWidget(account)
        return side

    def _nav_button(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("nav")
        btn.setCheckable(True)
        btn.clicked.connect(slot)
        self.nav.addButton(btn)
        return btn

    # ---- data

    def set_library(self, data: dict[str, Any] | None) -> None:
        self.data = data
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        if not data:
            self.playlist_list.blockSignals(False)
            self.stack.setCurrentWidget(self.empty)
            self.status.setText("Not synced yet")
            self.nav_liked.setEnabled(False)
            self.nav_overview.setEnabled(False)
            return
        self.nav_liked.setEnabled(True)
        self.nav_overview.setEnabled(True)
        for p in data["playlists"]:
            item = QListWidgetItem(f"{p['name']}")
            item.setToolTip(f"{p['name']} · {len(p['tracks'])} songs · by {p['owner']}")
            self.playlist_list.addItem(item)
        self.playlist_list.blockSignals(False)
        when = datetime.fromisoformat(data["fetched_at"]).astimezone().strftime("%b %d, %H:%M")
        self.status.setText(f"Signed in as {data['user']['name']}\nSynced {when}")
        self.overview.show_library(data)
        self.show_overview()

    def show_overview(self) -> None:
        if not self.data:
            return
        self.nav_overview.setChecked(True)
        self.playlist_list.clearSelection()
        self.playlist_list.setCurrentRow(-1)
        self.stack.setCurrentWidget(self.overview)

    def show_liked(self) -> None:
        if not self.data:
            return
        self.nav_liked.setChecked(True)
        self.playlist_list.setCurrentRow(-1)
        liked = self.data["liked"]
        self.tracks.show_tracks("Liked Songs", self._subtitle(liked), liked, self.data["tracks"])
        self.stack.setCurrentWidget(self.tracks)

    def show_playlist(self, row: int) -> None:
        if not self.data or row < 0:
            return
        self.nav.setExclusive(False)
        for b in self.nav.buttons():
            b.setChecked(False)
        self.nav.setExclusive(True)
        p = self.data["playlists"][row]
        sub = self._subtitle(p["tracks"])
        sub = f"By {p['owner']} · {sub}" if p["owner"] else sub
        self.tracks.show_tracks(p["name"], sub, p["tracks"], self.data["tracks"])
        self.stack.setCurrentWidget(self.tracks)

    def _subtitle(self, entries) -> str:
        tracks = self.data["tracks"]
        ms = sum(tracks[e["id"]]["duration_ms"] for e in entries if e["id"] in tracks)
        hours, minutes = divmod(ms // 60000, 60)
        length = f"{hours} hr {minutes} min" if hours else f"{minutes} min"
        return f"{len(entries):,} songs · {length}"

    # ---- sync

    def edit_settings(self) -> bool:
        dlg = SetupDialog(self, config.load_client_id() or "")
        if dlg.exec() != QDialog.Accepted:
            return False
        if dlg.value() != (config.load_client_id() or ""):
            config.forget_login()
        config.save_client_id(dlg.value())
        return True

    def sync(self) -> None:
        if self._thread is not None:
            return
        client_id = config.load_client_id()
        if not client_id:
            if not self.edit_settings():
                return
            client_id = config.load_client_id()
        self.sync_btn.setEnabled(False)
        self.bar.setRange(0, 0)
        self.bar.show()
        self.status.setText("Connecting to Spotify…")

        self._thread = QThread(self)
        self._worker = SyncWorker(client_id)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _on_progress(self, message: str, done: int, total: int) -> None:
        self.status.setText(message if not total else f"{message}\n{done:,} of {total:,}")
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)

    def _on_done(self, data: dict) -> None:
        self.set_library(data)

    def _on_failed(self, message: str) -> None:
        self.status.setText("Sync failed")
        QMessageBox.warning(self, "Sync failed", f"Loom couldn't read your library.\n\n{message}")
        if self.data:
            when = datetime.fromisoformat(self.data["fetched_at"]).astimezone().strftime("%b %d, %H:%M")
            self.status.setText(f"Signed in as {self.data['user']['name']}\nSynced {when}")

    def _cleanup(self) -> None:
        self.bar.hide()
        self.sync_btn.setEnabled(True)
        self._worker.deleteLater()
        self._thread.deleteLater()
        self._thread = None


def asset(name: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return str(base / "assets" / name)


def main() -> None:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Loom")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(QIcon(asset("loom.png")))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
