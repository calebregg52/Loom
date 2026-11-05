from __future__ import annotations

import json
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
from .genre_page import GenrePage
from .models import TrackFilter, TrackTableModel
from .theme import STYLESHEET

DASHBOARD_URL = "https://developer.spotify.com/dashboard"


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


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

    cancelled = Signal()
    library_ready = Signal(dict)

    def __init__(self, client_id: str):
        super().__init__()
        self.client_id = client_id
        self.sp = None
        self._stop = False

    def cancel(self) -> None:
        self._stop = True
        if self.sp is not None:
            self.sp.cancel_event.set()

    def _progress(self, message: str, done: int, total: int) -> None:
        from ..spotify import Cancelled

        if self._stop:
            raise Cancelled()
        self.progress.emit(message, done, total)

    def run(self) -> None:
        from ..spotify import Cancelled, make_client

        try:
            self.sp = sp = make_client(self.client_id)
            if self._stop:
                raise Cancelled()
            self.progress.emit("Waiting for Spotify sign-in in your browser…", 0, 0)
            sp.current_user()  # triggers the browser login the first time
            data = library.fetch_library(sp, self._progress)
            library.save(data, config.library_path())
            self.library_ready.emit(json.loads(json.dumps(data)))  # show playlists right away
            from .. import enrich

            self.progress.emit("Playlists done. Now adding genres and BPM…", 0, 0)
            enrich.enrich(data, sp, config.enrich_cache_path(), config.load_lastfm_key(), self._progress)
            library.save(data, config.library_path())
            self.finished.emit(data)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:  # shown to the user, never crash the window
            self.failed.emit(str(exc) if type(exc).__name__ == "RateLimited" else f"{type(exc).__name__}: {exc}")


class PublishWorker(QObject):
    progress = Signal(str, int, int)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, client_id: str, data: dict, groups: list, source_label: str):
        super().__init__()
        self.args = (client_id, data, groups, source_label)

    def run(self) -> None:
        try:
            from .. import publish
            from ..spotify import make_client

            client_id, data, groups, source_label = self.args
            sp = make_client(client_id)
            self.finished.emit(publish.publish(sp, data, groups, source_label, self.progress.emit))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- setup dialog


class SetupDialog(QDialog):
    def __init__(self, parent=None, client_id: str = "", lastfm_key: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Loom settings")
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
        self.client_id.setPlaceholderText("Spotify Client ID")
        lay.addWidget(self.client_id)

        lay.addSpacing(6)
        extra = label(
            "<b>Optional:</b> a free <a style='color:#9286ff' href='https://www.last.fm/api/account/create'>"
            "Last.fm API key</a> fills in genres for artists Spotify has none for.",
            "muted", wrap=True,
        )
        extra.setOpenExternalLinks(True)
        extra.setTextFormat(Qt.RichText)
        lay.addWidget(extra)
        self.lastfm = QLineEdit(lastfm_key)
        self.lastfm.setPlaceholderText("Last.fm API key (optional)")
        test_row = QHBoxLayout()
        test_row.addWidget(self.lastfm, 1)
        self.test_btn = QPushButton("Test key")
        self.test_btn.clicked.connect(self.test_lastfm)
        test_row.addWidget(self.test_btn)
        lay.addLayout(test_row)
        self.test_result = label("", "muted", wrap=True)
        self.test_result.hide()
        lay.addWidget(self.test_result)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Save")
        self.ok.setObjectName("primary")
        self.ok.clicked.connect(self.accept)
        self.ok.setEnabled(bool(client_id.strip()))
        self.client_id.textChanged.connect(lambda t: self.ok.setEnabled(len(t.strip()) >= 16))
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        lay.addLayout(buttons)

    def value(self) -> str:
        return self.client_id.text().strip()

    def lastfm_value(self) -> str:
        return self.lastfm.text().strip()

    def test_lastfm(self) -> None:
        from ..enrich import check_lastfm_key

        key = self.lastfm_value()
        if not key:
            ok, message = False, "Paste a key first."
        else:
            self.test_btn.setEnabled(False)
            self.test_result.setText("Checking with Last.fm…")
            self.test_result.show()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            QApplication.processEvents()
            try:
                ok, message = check_lastfm_key(key)
            finally:
                QApplication.restoreOverrideCursor()
                self.test_btn.setEnabled(True)
        color = "#4ade80" if ok else "#f87171"
        self.test_result.setText(f"<span style='color:{color}'>{'✓' if ok else '✕'}</span> {message}")
        self.test_result.setTextFormat(Qt.RichText)
        self.test_result.show()


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
                ("genres", "genres"),
                ("avg_bpm", "average BPM"),
                ("hours", "hours of music"),
            ]
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, 0, i)
        lay.addLayout(grid)

        tops = QHBoxLayout()
        tops.setSpacing(14)
        self.top_card, self.top_list = self._ranked_card("MOST COLLECTED ARTISTS")
        self.genre_card, self.genre_list = self._ranked_card("TOP GENRES")
        tops.addWidget(self.top_card, 1)
        tops.addWidget(self.genre_card, 1)
        lay.addLayout(tops)

        self.sources_card = QFrame()
        self.sources_card.setObjectName("card")
        src = QVBoxLayout(self.sources_card)
        src.setContentsMargins(20, 16, 20, 16)
        src.setSpacing(8)
        src.addWidget(label("WHERE GENRES CAME FROM", "sectionLabel"))
        self.sources_line = label("", wrap=True)
        self.lastfm_line = label("", wrap=True)
        self.lastfm_line.setTextFormat(Qt.RichText)
        src.addWidget(self.sources_line)
        src.addWidget(self.lastfm_line)
        lay.addWidget(self.sources_card)

        self.note = label("", "muted", wrap=True)
        lay.addWidget(self.note)
        lay.addStretch(1)

    @staticmethod
    def _ranked_card(title: str):
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        box.addWidget(label(title, "sectionLabel"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        box.addStretch(1)
        return card, grid

    @staticmethod
    def _fill_ranked(grid: QGridLayout, rows, empty: str) -> None:
        while grid.count():
            grid.takeAt(0).widget().deleteLater()
        if not rows:
            grid.addWidget(label(empty, "muted", wrap=True), 0, 0, 1, 3)
            return
        biggest = rows[0][1]
        for r, (name, count) in enumerate(rows):
            num = label(f"{r + 1}", "muted")
            num.setFixedWidth(22)
            grid.addWidget(num, r, 0)
            grid.addWidget(label(name), r, 1)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setRange(0, biggest)
            bar.setValue(count)
            bar.setFixedWidth(110)
            bar.setToolTip(f"{count} songs")
            grid.addWidget(bar, r, 2)

    def show_library(self, data: dict[str, Any]) -> None:
        s = library.summary(data)
        when = datetime.fromisoformat(data["fetched_at"]).astimezone().strftime("%b %d, %Y at %H:%M")
        self.title.setText(f"{data['user']['name']}'s library")
        self.subtitle.setText(f"Last synced {when}")
        for key, card in self.cards.items():
            v = s[key]
            if v is None:
                card.value.setText("–")
            elif key == "hours":
                card.value.setText(f"{v:,.1f}")
            elif key == "avg_bpm":
                card.value.setText(f"{v:.0f}")
                card.setToolTip(f"BPM found for {s['bpm_coverage']:.0%} of your songs")
            else:
                card.value.setText(f"{v:,}")
        self._fill_ranked(self.top_list, s["top_artists"], "No artists yet.")
        self._fill_ranked(self.genre_list, s["top_genres"], "No genres yet. Sync again to add them.")
        self._show_sources(data)
        notes = list(data.get("enrich_problems") or [])
        skipped = data.get("skipped_playlists") or []
        if skipped:
            notes.append(f"Spotify didn't let Loom read {len(skipped)} playlist(s): {', '.join(skipped)}.")
        if data.get("enriched_at"):
            src, total = s["bpm_sources"], max(s["tracks"], 1)
            missing = s["tracks"] - sum(src.values())
            notes.append(
                f"BPM: {src['reccobeats'] / total:.0%} from ReccoBeats, {src['deezer'] / total:.0%} from Deezer, "
                f"{src['artist average'] / total:.0%} estimated from the artist's other songs (shown as ~), "
                f"{missing / total:.0%} unknown. Unknown songs are checked again on later syncs."
            )
        self.note.setText("\n".join(notes))

    def _show_sources(self, data: dict[str, Any]) -> None:
        artists = data.get("artists") or {}
        if not data.get("enriched_at"):
            self.sources_card.hide()
            return
        self.sources_card.show()
        counts = {"spotify": 0, "lastfm": 0, "estimated": 0, None: 0}
        for a in artists.values():
            counts[a.get("genre_source")] = counts.get(a.get("genre_source"), 0) + 1
        self.sources_line.setText(
            f"Spotify: {_n(counts['spotify'], 'artist')}   ·   Last.fm: {_n(counts['lastfm'], 'artist')}   ·   "
            f"Estimated from your playlists (~): {_n(counts['estimated'], 'artist')}   ·   "
            f"No genre: {_n(counts[None], 'artist')}"
        )
        ok, bad = "<span style='color:#4ade80'>●</span>", "<span style='color:#f87171'>●</span>"
        idle = "<span style='color:#8b91a3'>●</span>"
        problem = data.get("lastfm_problem")
        if problem:
            text = f"{bad} Last.fm isn't working: {problem}"
        elif not data.get("lastfm_used"):
            text = (f"{idle} Last.fm is off. Add a free key in Settings to fill in the "
                    f"{counts[None]:,} artists with no genre." if counts[None] else f"{idle} Last.fm is off.")
        elif counts["lastfm"]:
            text = f"{ok} Last.fm is working: it filled in genres for {_n(counts['lastfm'], 'artist')}."
        elif counts[None]:
            text = (f"{bad} Last.fm is set up but found no genres. Use Test key in Settings to check the key.")
        else:
            text = f"{ok} Last.fm is set up. Spotify already had genres for every artist."
        self.lastfm_line.setText(text)


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
        self.search.setPlaceholderText("Search title, artist or genre")
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
            [QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.Stretch, QHeaderView.Stretch]
            + [QHeaderView.ResizeToContents] * 6
        ):
            h.setSectionResizeMode(col, mode)
        lay.addWidget(self.table, 1)

    def show_tracks(self, title: str, subtitle: str, entries, data) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.search.clear()
        self.model.set_rows(entries, data)
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
        self.genre_page = GenrePage()
        self.genre_page.publish_requested.connect(self.publish_groups)
        for page in (self.empty, self.overview, self.tracks, self.genre_page):
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
        self.nav_genres = self._nav_button("Genre Playlists", self.show_genres)
        lay.addWidget(self.nav_overview)
        lay.addWidget(self.nav_liked)
        lay.addWidget(self.nav_genres)
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
        account = QPushButton("Settings")
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
            self.nav_genres.setEnabled(False)
            return
        self.nav_liked.setEnabled(True)
        self.nav_overview.setEnabled(True)
        self.nav_genres.setEnabled(True)
        self.genre_page.set_library(data)
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
        self.tracks.show_tracks("Liked Songs", self._subtitle(liked), liked, self.data)
        self.stack.setCurrentWidget(self.tracks)

    def show_genres(self) -> None:
        if not self.data:
            return
        self.nav_genres.setChecked(True)
        self.playlist_list.setCurrentRow(-1)
        self.stack.setCurrentWidget(self.genre_page)

    def publish_groups(self, groups: list, source_label: str) -> None:
        if self._thread is not None or not self.data:
            return
        names = "\n".join(f"•  Loom · {g['name']}  ({len(g['track_ids'])} songs)" for g in groups[:12])
        more = f"\n…and {len(groups) - 12} more" if len(groups) > 12 else ""
        answer = QMessageBox.question(
            self, "Create playlists",
            f"Loom will save these private playlists to your Spotify account:\n\n{names}{more}\n\n"
            "Playlists Loom made before with the same name are refilled. Nothing else is changed.",
        )
        if answer != QMessageBox.Yes:
            return
        self.genre_page.set_busy(True, "Saving playlists to Spotify…")
        self._start(PublishWorker(config.load_client_id(), self.data, groups, source_label),
                    self._on_published, self._on_publish_failed)

    def _on_published(self, result: dict) -> None:
        made, refreshed = len(result["created"]), len(result["updated"])
        bits = []
        if made:
            bits.append(f"created {made}")
        if refreshed:
            bits.append(f"updated {refreshed}")
        self.genre_page.set_busy(False, f"Done: {' and '.join(bits)} playlist(s). They're in Spotify now; "
                                        "sync to see them in the sidebar.")
        self._restore_status()

    def _on_publish_failed(self, message: str) -> None:
        self.genre_page.set_busy(False, "Couldn't save the playlists.")
        QMessageBox.warning(self, "Couldn't create playlists", message)
        self._restore_status()

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
        self.tracks.show_tracks(p["name"], sub, p["tracks"], self.data)
        self.stack.setCurrentWidget(self.tracks)

    def _subtitle(self, entries) -> str:
        tracks = self.data["tracks"]
        ms = sum(tracks[e["id"]]["duration_ms"] for e in entries if e["id"] in tracks)
        hours, minutes = divmod(ms // 60000, 60)
        length = f"{hours} hr {minutes} min" if hours else f"{minutes} min"
        return f"{len(entries):,} songs · {length}"

    # ---- sync

    def edit_settings(self) -> bool:
        dlg = SetupDialog(self, config.load_client_id() or "", config.load_lastfm_key() or "")
        if dlg.exec() != QDialog.Accepted:
            return False
        if dlg.value() != (config.load_client_id() or ""):
            config.forget_login()
        config.save_client_id(dlg.value())
        config.save_lastfm_key(dlg.lastfm_value())
        return True

    def sync(self) -> None:
        if self._thread is not None:
            if isinstance(self._worker, SyncWorker):
                self._worker.cancel()
                self.sync_btn.setEnabled(False)
                self.sync_btn.setText("Cancelling…")
            return
        client_id = config.load_client_id()
        if not client_id:
            if not self.edit_settings():
                return
            client_id = config.load_client_id()
        self.status.setText("Connecting to Spotify…")
        worker = SyncWorker(client_id)
        worker.cancelled.connect(self._on_cancelled)
        worker.library_ready.connect(self._on_library_ready)
        worker.cancelled.connect(lambda: self._thread and self._thread.quit())
        self._start(worker, self._on_done, self._on_failed)
        self.sync_btn.setText("Cancel sync")
        self.sync_btn.setEnabled(True)

    def _start(self, worker: QObject, on_done, on_failed) -> None:
        self.sync_btn.setEnabled(False)
        self.bar.setRange(0, 0)
        self.bar.show()
        self._thread = QThread(self)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(on_done)
        worker.failed.connect(on_failed)
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _restore_status(self) -> None:
        if self.data:
            when = datetime.fromisoformat(self.data["fetched_at"]).astimezone().strftime("%b %d, %H:%M")
            self.status.setText(f"Signed in as {self.data['user']['name']}\nSynced {when}")

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
        self._restore_status()

    def _on_library_ready(self, data: dict) -> None:
        status = self.status.text()
        self.set_library(data)
        self.status.setText(status)

    def _on_cancelled(self) -> None:
        # The playlists may already be saved; show whatever made it to disk.
        saved = library.load(config.library_path())
        if saved:
            self.set_library(saved)
        self._restore_status()
        self.status.setText(self.status.text() + "\nSync cancelled. Next sync picks up where it stopped.")

    def _cleanup(self) -> None:
        self.bar.hide()
        self.sync_btn.setText("Sync library")
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
