from __future__ import annotations

import math
import random
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
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

from .. import config, genres, journey, publish, wavefit
from .models import TrackTableModel
from .theme import ACCENT, BORDER, MUTED, TEXT


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


class CurveEditor(QWidget):
    """The journey graph, which is also the wave designer.

    The dashed curve is the target (drag its dots, double-click to add one, right-click a dot to
    remove it). After building, the solid line shows each chosen song's real BPM.
    """

    points_changed = Signal()   # while dragging
    points_committed = Signal()  # on release / add / remove

    PAD_L, PAD_R, PAD_T, PAD_B = 46, 64, 22, 26

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.points: list[list[float]] = [[0.0, 0.85], [1.0, 0.05]]
        self.scale = _Linear(70.0, 150.0)
        self.songs: list[dict[str, Any]] = []  # {x, bpm, est, color}
        self.thin: list[tuple[float, float]] = []
        self.album: dict[str, Any] | None = None
        self._drag: int | None = None
        self._hover: int | None = None
        self.setToolTip("Drag the dots to reshape the journey. Double-click to add a dot, right-click one to remove it.")

    # ---- data in/out

    @property
    def low(self) -> float:
        return self.scale.low

    @property
    def high(self) -> float:
        return self.scale.high

    def set_points(self, points) -> None:
        self.points = [list(p) for p in journey.clean_points(points)]
        self.update()

    def get_points(self) -> list[tuple[float, float]]:
        return [(round(x, 3), round(y, 3)) for x, y in self.points]

    def set_scale(self, scale) -> None:
        self.scale = scale or _Linear(70.0, 150.0)
        self.update()

    def set_range(self, low: float, high: float) -> None:
        self.set_scale(_Linear(low, max(high, low + 1)))

    def set_songs(self, songs, album, thin=()) -> None:
        self.songs, self.album, self.thin = list(songs), album, list(thin)
        self.update()

    # ---- geometry (vertical axis is wave height: 0 = slowest, 1 = fastest)

    def _plot(self) -> QRectF:
        return QRectF(self.PAD_L, self.PAD_T, self.width() - self.PAD_L - self.PAD_R,
                      self.height() - self.PAD_T - self.PAD_B)

    def _view(self) -> tuple[float, float]:
        ys = [-0.1, 1.1] + [self.scale.to_y(s["bpm"]) for s in self.songs]
        if self.album:
            ys.append(self.scale.to_y(self.album["avg_bpm"]))
        return max(min(ys), -0.6), min(max(ys), 1.6)

    def _to_screen(self, x: float, y: float) -> QPointF:
        r = self._plot()
        lo, hi = self._view()
        return QPointF(r.left() + x * r.width(), r.bottom() - (y - lo) / (hi - lo) * r.height())

    def _norm_to_bpm(self, y: float) -> float:
        return self.scale.to_bpm(y)

    def _from_screen(self, pos) -> tuple[float, float]:
        r = self._plot()
        lo, hi = self._view()
        x = (pos.x() - r.left()) / r.width()
        y = lo + (r.bottom() - pos.y()) / r.height() * (hi - lo)
        return min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)

    def _hit(self, pos) -> int | None:
        for i, (x, y) in enumerate(self.points):
            q = self._to_screen(x, y)
            if (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2 <= 81:
                return i
        return None

    # ---- mouse

    def mousePressEvent(self, e) -> None:
        i = self._hit(e.position())
        if e.button() == Qt.RightButton and i is not None and 0 < i < len(self.points) - 1:
            del self.points[i]
            self.update()
            self.points_committed.emit()
        elif e.button() == Qt.LeftButton and i is not None:
            self._drag = i

    def mouseDoubleClickEvent(self, e) -> None:
        if self._hit(e.position()) is None:
            x, y = self._from_screen(e.position())
            self.points.append([x, y])
            self.points.sort(key=lambda p: p[0])
            self.update()
            self.points_committed.emit()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            hover = self._hit(e.position())
            if hover != self._hover:
                self._hover = hover
                self.setCursor(Qt.OpenHandCursor if hover is not None else Qt.CrossCursor)
                self.update()
            return
        x, y = self._from_screen(e.position())
        i = self._drag
        if i == 0:
            x = 0.0
        elif i == len(self.points) - 1:
            x = 1.0
        else:
            x = min(max(x, self.points[i - 1][0] + 0.02), self.points[i + 1][0] - 0.02)
        self.points[i] = [x, y]
        self.update()
        self.points_changed.emit()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is not None:
            self._drag = None
            self.points_committed.emit()

    # ---- drawing

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._plot()
        lo, hi = self._view()
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1.5))
        p.setFont(small)

        # stretches where your library has too few songs to follow the wave
        widest = max(self.thin, key=lambda s: s[1] - s[0]) if self.thin else None
        for x0, x1 in self.thin:
            a, b = self._to_screen(x0, hi), self._to_screen(x1, lo)
            p.fillRect(QRectF(a, b), QColor(251, 191, 36, 26))
            if (x0, x1) == widest:
                p.setPen(QColor("#d6a234"))
                p.drawText(QRectF(a.x() + 4, a.y() + 2, max(b.x() - a.x(), 90), 16), Qt.AlignLeft,
                           "few songs here")

        # BPM gridlines: evenly spaced in BPM, so where the scale bends they bunch up or spread out
        b_lo, b_hi = self.scale.to_bpm(lo), self.scale.to_bpm(hi)
        step = next(s for s in (5, 10, 20, 25, 40, 50) if (b_hi - b_lo) / s <= 7)
        last_y = None
        bpm = math.ceil(b_lo / step) * step
        while bpm <= b_hi:
            y = self._to_screen(0, self.scale.to_y(bpm)).y()
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            if last_y is None or abs(last_y - y) >= 14:
                p.setPen(QColor(MUTED))
                p.drawText(QRectF(0, y - 8, self.PAD_L - 8, 16), Qt.AlignRight | Qt.AlignVCenter, f"{bpm:.0f}")
                last_y = y
            bpm += step
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(r.left(), r.bottom() + 6, 80, 16), Qt.AlignLeft, "start")
        p.drawText(QRectF(r.right() - 80, r.bottom() + 6, 80, 16), Qt.AlignRight, "end")
        p.drawText(QRectF(r.left(), 2, r.width(), 16), Qt.AlignRight,
                   "drag dots · double-click to add · right-click to remove")

        # target curve
        path = QPainterPath()
        for k in range(121):
            x = k / 120
            q = self._to_screen(x, journey.curve_at(self.points, x))
            path.moveTo(q) if k == 0 else path.lineTo(q)
        p.setPen(QPen(QColor(MUTED), 1.6, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # songs, placed by play time, colored by how well each one sits on the wave
        if self.songs:
            line = [self._to_screen(s["x"], self.scale.to_y(s["bpm"])) for s in self.songs]
            p.setPen(QPen(QColor(ACCENT), 2.0))
            path = QPainterPath(line[0])
            for q in line[1:]:
                path.lineTo(q)
            p.drawPath(path)
            for s, q in zip(self.songs, line):
                color = QColor(s.get("color") or ACCENT)
                p.setPen(QPen(color, 1.6))
                p.setBrush(QColor("#151821") if s.get("est") else color)
                p.drawEllipse(q, 3.6, 3.6)
            if self.album:
                q = self._to_screen(1.0, self.scale.to_y(self.album["avg_bpm"]))
                a = QPointF(r.right() + 34, q.y())
                p.setPen(QPen(QColor(ACCENT), 1.4, Qt.DotLine))
                p.drawLine(line[-1], a)
                p.setBrush(QColor(124, 108, 255, 60))
                p.drawRoundedRect(QRectF(a.x() - 16, a.y() - 10, 32, 20), 6, 6)
                p.setPen(QColor(TEXT))
                p.drawText(QRectF(a.x() - 16, a.y() - 10, 32, 20), Qt.AlignCenter, "LP")

        # handles
        for i, (x, y) in enumerate(self.points):
            q = self._to_screen(x, y)
            active = i in (self._hover, self._drag)
            p.setPen(QPen(QColor(TEXT), 1.6))
            p.setBrush(QColor(TEXT) if active else QColor("#151821"))
            p.drawEllipse(q, 6.5 if active else 5.5, 6.5 if active else 5.5)
            if active:
                p.setPen(QColor(TEXT))
                p.drawText(QRectF(q.x() - 40, q.y() - 26, 80, 16), Qt.AlignCenter,
                           f"{self._norm_to_bpm(y):.0f} BPM")


class _Linear:
    """A plain straight BPM scale, used before there is a library to fit."""

    def __init__(self, low: float, high: float):
        self.low, self.high = low, high

    def to_bpm(self, y: float) -> float:
        return self.low + (self.high - self.low) * y

    def to_y(self, bpm: float) -> float:
        return (bpm - self.low) / (self.high - self.low)


GOOD, OK, OFF = "#4ade80", "#fbbf24", "#f87171"


def fit_color(off_pct: float) -> str:
    return GOOD if off_pct < 5 else OK if off_pct < 12 else OFF


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
        self.shape.setMinimumWidth(230)
        self.length = QSpinBox()
        self.length.setRange(8, 120)
        self.length.setValue(25)
        self.length.setSuffix(" songs")
        self.album = QCheckBox("End with a full album")
        self.album.setChecked(True)
        self.priority = QComboBox()
        for key, label in wavefit.PRIORITY_LABELS.items():
            self.priority.addItem(label, key)
        self.priority.setCurrentIndex(max(self.priority.findData(config.load_ui("journey_priority", "balanced")), 0))
        self.priority.setToolTip("Tight to the wave: follow the line closely.\n"
                                 "Balanced: a mix of both.\n"
                                 "Smooth flow: never jolt between songs, even if the line is followed loosely.")
        for text, w in (("From", self.source), ("Genre", self.family), ("Shape", self.shape),
                        ("Priority", self.priority)):
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
        self.chart = CurveEditor()
        cv.addWidget(self.chart)
        shape_tools = QHBoxLayout()
        self.shape_note = _label("", "muted")
        shape_tools.addWidget(self.shape_note, 1)
        self.save_shape_btn = QPushButton("Save shape…")
        self.delete_shape_btn = QPushButton("Delete shape")
        self.flip_btn = QPushButton("Flip")
        self.flip_btn.setToolTip("Mirror the shape so it runs backwards")
        self.invert_btn = QPushButton("Invert")
        self.invert_btn.setToolTip("Turn peaks into valleys")
        for b in (self.flip_btn, self.invert_btn, self.save_shape_btn, self.delete_shape_btn):
            shape_tools.addWidget(b)
        cv.addLayout(shape_tools)
        lay.addWidget(card)
        lay.addSpacing(8)

        self.summary = _label("", "pageSubtitle", wrap=True)
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setToolTip("Fit: how closely the songs follow your wave (60%) and how smoothly each one hands off to the next (40%).\nHover a song for why it was picked.")
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

        self._fill_shapes()
        self.shape.currentIndexChanged.connect(self._shape_picked)
        self.chart.points_changed.connect(self._mark_custom)
        self.chart.points_committed.connect(self._on_curve_edited)
        self.save_shape_btn.clicked.connect(self._save_shape)
        self.delete_shape_btn.clicked.connect(self._delete_shape)
        self.flip_btn.clicked.connect(lambda: self._transform(lambda x, y: (1 - x, y)))
        self.invert_btn.clicked.connect(lambda: self._transform(lambda x, y: (x, 1 - y)))
        self.family.currentIndexChanged.connect(self._update_range)
        self.build_btn.clicked.connect(lambda: self.rebuild(seed=None))
        self.again_btn.clicked.connect(lambda: self.rebuild(seed=random.randint(1, 10_000)))
        self.source.currentIndexChanged.connect(self._fill_families)
        self.source.currentIndexChanged.connect(self._update_range)
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setEnabled(False)
        self.priority.currentIndexChanged.connect(self._priority_picked)
        self._live = QTimer(self)  # wait for a pause in editing before re-planning
        self._live.setSingleShot(True)
        self._live.setInterval(200)
        self._live.timeout.connect(lambda: self.rebuild(seed=None))
        self.again_btn.setEnabled(False)

    # ---- shapes

    CUSTOM = "__custom__"

    def _saved_shapes(self) -> dict[str, list]:
        from .. import config

        return dict(config.load_ui("custom_shapes", {}) or {})

    def _fill_shapes(self, select: str | None = None) -> None:
        self.shape.blockSignals(True)
        self.shape.clear()
        for key, text in journey.SHAPES.items():
            self.shape.addItem(text, key)
        saved = self._saved_shapes()
        if saved:
            self.shape.insertSeparator(self.shape.count())
            for name in sorted(saved):
                self.shape.addItem(f"★ {name}", f"saved:{name}")
        self.shape.insertSeparator(self.shape.count())
        self.shape.addItem("Custom (drawn)", self.CUSTOM)
        index = self.shape.findData(select) if select else 0
        self.shape.setCurrentIndex(max(index, 0))
        self.shape.blockSignals(False)
        self._shape_picked()

    def _shape_picked(self) -> None:
        key = self.shape.currentData()
        if key == self.CUSTOM:
            pass  # keep whatever is drawn
        elif isinstance(key, str) and key.startswith("saved:"):
            self.chart.set_points(self._saved_shapes().get(key[6:], [(0, 0.5), (1, 0.5)]))
        elif key in journey.PRESETS:
            self.chart.set_points(journey.PRESETS[key]["points"])
        self.delete_shape_btn.setVisible(isinstance(key, str) and key.startswith("saved:"))
        self._update_note()
        if self.result is not None:
            self.rebuild(seed=None)

    def _mark_custom(self) -> None:
        if self.shape.currentData() != self.CUSTOM:
            self.shape.blockSignals(True)
            self.shape.setCurrentIndex(self.shape.findData(self.CUSTOM))
            self.shape.blockSignals(False)
            self.delete_shape_btn.setVisible(False)
        self._update_note()

    def _on_curve_edited(self) -> None:
        self._mark_custom()
        if self.data:
            self._live.start()

    def _priority_picked(self) -> None:
        config.save_ui("journey_priority", self.priority.currentData())
        if self.result is not None:
            self.rebuild(seed=None)

    def _transform(self, fn) -> None:
        self.chart.set_points([fn(x, y) for x, y in self.chart.get_points()])
        self._on_curve_edited()

    def _update_note(self) -> None:
        n = len(self.chart.points)
        self.shape_note.setText(f"{n} points · {self.chart.low:.0f}–{self.chart.high:.0f} BPM range")

    def _save_shape(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        from .. import config

        current = self.shape.currentText().lstrip("★ ").split(" (")[0]
        name, ok = QInputDialog.getText(self, "Save shape", "Name for this shape:",
                                        text="" if self.shape.currentData() == self.CUSTOM else f"My {current}")
        if not ok or not name.strip():
            return
        saved = self._saved_shapes()
        saved[name.strip()] = [list(p) for p in self.chart.get_points()]
        config.save_ui("custom_shapes", saved)
        self._fill_shapes(select=f"saved:{name.strip()}")
        self.status.setText(f"Saved shape “{name.strip()}”.")

    def _delete_shape(self) -> None:
        from .. import config

        key = self.shape.currentData()
        if isinstance(key, str) and key.startswith("saved:"):
            saved = self._saved_shapes()
            saved.pop(key[6:], None)
            config.save_ui("custom_shapes", saved)
            points = self.chart.get_points()
            self._fill_shapes(select=self.CUSTOM)
            self.chart.set_points(points)

    def _update_range(self) -> None:
        if self.data:
            scale = wavefit.scale_for(self.data, self._entries(), self.family.currentData())
            if scale:
                self.chart.set_scale(scale)
        self._update_note()

    def set_library(self, data: dict[str, Any] | None) -> None:
        self.data = data
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        if data:
            self.source.addItem("All my songs", "all")
            self.source.addItem("Liked Songs", "liked")
            if data.get("discover") and data["discover"].get("order"):
                label = data["discover"].get("label") or "songs"
                self.source.addItem(f"Discover: {label}", "discover")
                self.source.addItem(f"My songs + Discover: {label}", "all+discover")
            for p in data["playlists"]:
                if not p["name"].startswith(publish.PREFIX):
                    self.source.addItem(f"Playlist: {p['name']}", p["id"])
            self.source.setCurrentIndex(max(self.source.findData(current), 0))
        self.source.blockSignals(False)
        self._fill_families()
        self._update_range()

    def use_source(self, key: str) -> None:
        i = self.source.findData(key)
        if i >= 0:
            self.source.setCurrentIndex(i)

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
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.result = j = journey.build(
                self.data, self._entries(), self.chart.get_points(), self.length.value(),
                genre_family=self.family.currentData(), end_with_album=self.album.isChecked(), seed=seed,
                priority=self.priority.currentData() or "balanced",
            )
        finally:
            QApplication.restoreOverrideCursor()
        tracks = self.data["tracks"]
        chain = [tracks[t] for t in j["track_ids"]]
        if j.get("scale") is not None:
            self.chart.set_scale(j["scale"])
        songs, notes = [], []
        for i, (t, d) in enumerate(zip(chain, j["details"])):
            bpm = journey.tempo(t)
            if d["folded"]:  # drawn where it grooves: at half or double time
                bpm = min((bpm * 2, bpm / 2), key=lambda b: abs(b - d["target_bpm"]))
            color = fit_color(d["off_pct"])
            songs.append({"x": j["xs"][i], "bpm": bpm, "color": color,
                          "est": bool((t.get("features") or {}).get("estimated"))})
            notes.append((wavefit.why(d, chain[i - 1]["name"] if i else None), color))
        self.chart.set_songs(songs, j["album"], j.get("thin", []))
        self.model.set_rows([{"id": t} for t in j["track_ids"]], self.data)
        self.model.set_notes(notes)
        self._update_note()
        self.again_btn.setEnabled(bool(chain))
        self.save_btn.setEnabled(bool(chain))
        if not chain:
            self.summary.setText("No songs with BPM here yet. Sync, or pick a bigger source.")
            self.album_line.setText("")
            return
        ms = sum(t["duration_ms"] for t in chain)
        first, last = journey.tempo(chain[0]), journey.tempo(chain[-1])
        extra = []
        if j.get("short_pool"):
            extra.append(f"only {len(chain)} songs fit here, so it's shorter than you asked")
        if j.get("thin"):
            extra.append("the shaded parts of the wave have few songs in your library")
        if j["skipped"]:
            extra.append(f"{j['skipped']:,} songs left out because they have no BPM")
        self.summary.setText(
            f"<b style='color:{TEXT}'>Fit {j['fit_score']}/100</b> &nbsp;·&nbsp; "
            f"{len(chain)} songs · {ms // 60000} min · {first:.0f} → {last:.0f} BPM"
            + "".join(f" · {e}" for e in extra)
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
        shape_name = self.shape.currentText().lstrip("★ ").split(" (")[0]
        if self.shape.currentData() == self.CUSTOM:
            shape_name = "Custom"
        self.name.setText(f"Journey · {shape_name}" + (f" · {fam}" if fam else ""))
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
