"""DownloadDeck - a multi-connection download manager for Windows."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QLabel, QMainWindow,
                               QMenu, QMessageBox, QSplitter, QStyle, QSystemTrayIcon, QTableView,
                               QToolBar, QTreeWidget, QTreeWidgetItem)

import engine
import video
from dialogs import (AddUrlDialog, CompleteDialog, DeleteDialog, FileInfoDialog, OptionsDialog,
                     ProgressDialog, SchedulerDialog, VideoDialog, open_folder, open_path)
from ui_common import fmt_date, fmt_rate, fmt_size, fmt_time, file_icon, tool_icon

APP = "DownloadDeck"
VERSION = "1.0.0"
HERE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
EXT_DIR = os.path.join(HERE, "chrome-extension")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

LOG_PATH = os.path.join(DATA_DIR, "bridge.log")


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def _refresh_native_host():
    try:
        import native_reg
        native_reg.register()
    except Exception as e:  # noqa: BLE001
        log(f"native host registration failed: {e}")


COLS = ["File Name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last Try Date", "Description"]


# ======================================================================= model
class DownloadModel(QAbstractTableModel):
    def __init__(self, mgr: engine.Manager):
        super().__init__()
        self.mgr = mgr
        self.rows: list[engine.Download] = []
        self.filter = ("all", None)
        self.sort_col, self.sort_order = 6, Qt.DescendingOrder

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[s]
        return None

    def _match(self, d):
        kind, val = self.filter
        if kind == "all":
            return True
        if kind == "cat":
            return d.category == val
        if kind == "unfinished":
            return d.status != "Complete" and (val is None or d.category == val)
        if kind == "finished":
            return d.status == "Complete" and (val is None or d.category == val)
        if kind == "queue":
            return d.in_queue
        return True

    def _key(self, d):
        c = self.sort_col
        return [d.filename.lower(), d.in_queue, d.size, d.progress, d.time_left, d.speed,
                d.last_try or d.added, d.description.lower()][c]

    def wanted(self):
        rows = [d for d in self.mgr.downloads if self._match(d)]
        rows.sort(key=self._key, reverse=self.sort_order == Qt.DescendingOrder)
        return rows

    def rebuild(self):
        self.beginResetModel()
        self.rows = self.wanted()
        self.endResetModel()

    def refresh(self) -> bool:
        """Repaint; returns True if the row set changed (caller restores selection)."""
        want = [d for d in self.mgr.downloads if self._match(d)]
        if {id(d) for d in want} != {id(d) for d in self.rows}:
            self.rebuild()
            return True
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLS) - 1))
        return False

    def sort(self, col, order=Qt.AscendingOrder):
        self.sort_col, self.sort_order = col, order
        self.rebuild()

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        d = self.rows[idx.row()]
        c = idx.column()
        if role == Qt.DisplayRole:
            if c == 0:
                return d.filename
            if c == 1:
                return "●" if d.in_queue else ""
            if c == 2:
                return fmt_size(d.size) if d.size >= 0 else ""
            if c == 3:
                if d.status == "Complete":
                    return "Complete"
                if d.status == "Error":
                    return "Error"
                if d.status in ("Connecting", "Merging", "Converting"):
                    return d.status + "..."
                if d.status == "Queued" and d.downloaded == 0:
                    return "Queued" if d.in_queue else ""
                return f"{d.progress:.2f}%" if d.size > 0 else fmt_size(d.downloaded)
            if c == 4:
                return fmt_time(d.time_left) if d.active else ""
            if c == 5:
                return fmt_rate(d.speed) if d.active else ""
            if c == 6:
                return fmt_date(d.last_try or d.added)
            if c == 7:
                return d.description or (d.error if d.status == "Error" else "")
        if role == Qt.DecorationRole and c == 0:
            return file_icon(d.category, d.status == "Complete")
        if role == Qt.ForegroundRole:
            if d.status == "Error":
                return QColor("#c62828")
            if d.active:
                return QColor("#0b4f9c")
        if role == Qt.ToolTipRole and c in (0, 7):
            return f"{d.url}\n{d.path}" + (f"\n{d.error}" if d.error else "")
        if role == Qt.TextAlignmentRole and c == 1:
            return Qt.AlignCenter
        return None


# ======================================================================= browser bridge
class Bridge(QObject):
    request = Signal(dict)
    show = Signal()


def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = APP

        def log_message(self, *a):
            pass

        def _cors(self):
            origin = self.headers.get("Origin", "")
            if origin.startswith(("chrome-extension://", "moz-extension://", "extension://")):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-DownloadDeck")
                self.send_header("Access-Control-Allow-Methods", "GET, POST")

        def _reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204); self._cors(); self.end_headers()

        def do_GET(self):
            if self.path == "/ping":
                self._reply(200, {"app": APP, "ok": True})
            else:
                self._reply(404, {"ok": False})

        def do_POST(self):
            # custom header => browsers force a CORS preflight, so ordinary web
            # pages cannot push downloads into the app
            if self.headers.get("X-DownloadDeck") != "1":
                return self._reply(403, {"ok": False})
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._reply(400, {"ok": False})
            if self.path == "/show":
                bridge.show.emit()
                return self._reply(200, {"ok": True})
            if self.path == "/formats":
                url = data.get("url")
                log(f"formats {url}")
                if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
                    return self._reply(400, {"ok": False, "error": "bad url"})
                if video.playlist_url(url) and video.is_playlist_only(url):
                    return self._reply(200, {"ok": True, "playlist_only": True, "qualities": []})
                try:
                    info = video.fetch_info(url)
                except Exception as e:  # noqa: BLE001
                    msg = str(e).replace("ERROR: ", "").replace("\x1b[0;31m", "").replace("\x1b[0m", "")
                    log(f"formats failed: {msg[:200]}")
                    return self._reply(200, {"ok": False, "error": msg[:200]})
                quals = [{"label": l, "size": sz} for l, sz in video.quality_choices(info)]
                return self._reply(200, {"ok": True, "title": info.get("title") or "",
                                         "duration": info.get("duration") or 0, "qualities": quals,
                                         "playlist": bool(video.playlist_url(url))})
            if self.path == "/download":
                urls = data.get("urls") or [data.get("url")]
                good = [u for u in urls if isinstance(u, str) and u.lower().startswith(("http://", "https://"))]
                if not good:
                    return self._reply(400, {"ok": False})
                log(f"download {good[0]} video={data.get('video')} quality={data.get('quality')}")
                for u in good:
                    bridge.request.emit({**data, "url": u})
                return self._reply(200, {"ok": True})
            self._reply(404, {"ok": False})

    return Handler


# ======================================================================= main window
class MainWindow(QMainWindow):
    def __init__(self, mgr: engine.Manager, bridge: Bridge):
        super().__init__()
        self.mgr, self.bridge = mgr, bridge
        self.progress_windows: dict[str, ProgressDialog] = {}
        self.open_dialogs = []
        self._quitting = False
        self._last_clip = ""
        self._sched_minute = ""
        self._tick = 0
        self.setWindowTitle(APP)
        self.setWindowIcon(tool_icon("app"))
        self.resize(1100, 600)
        self.setAcceptDrops(True)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        # categories tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Categories")
        folder = self.style().standardIcon(QStyle.SP_DirIcon)
        open_folder_icon = self.style().standardIcon(QStyle.SP_DirOpenIcon)
        root = QTreeWidgetItem(["All Downloads"]); root.setIcon(0, open_folder_icon)
        root.setData(0, Qt.UserRole, ("all", None))
        self.tree.addTopLevelItem(root)
        for cat in ["Compressed", "Documents", "Music", "Programs", "Video"]:
            it = QTreeWidgetItem([cat]); it.setIcon(0, file_icon(cat, True))
            it.setData(0, Qt.UserRole, ("cat", cat)); root.addChild(it)
        for label, kind in (("Unfinished", "unfinished"), ("Finished", "finished")):
            it = QTreeWidgetItem([label]); it.setIcon(0, folder)
            it.setData(0, Qt.UserRole, (kind, None))
            self.tree.addTopLevelItem(it)
            for cat in ["Compressed", "Documents", "Music", "Programs", "Video"]:
                c = QTreeWidgetItem([cat]); c.setIcon(0, file_icon(cat, True))
                c.setData(0, Qt.UserRole, (kind, cat)); it.addChild(c)
        q = QTreeWidgetItem(["Queues"]); q.setIcon(0, folder); q.setData(0, Qt.UserRole, ("queue", None))
        qm = QTreeWidgetItem(["Main download queue"]); qm.setIcon(0, tool_icon("startq"))
        qm.setData(0, Qt.UserRole, ("queue", "main")); q.addChild(qm)
        self.tree.addTopLevelItem(q)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        self.tree.currentItemChanged.connect(self._filter_changed)

        # download table
        self.model = DownloadModel(mgr)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(6, Qt.DescendingOrder)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(18, 18))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._double_click)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, w in enumerate([330, 26, 85, 90, 110, 110, 150, 200]):
            self.table.setColumnWidth(i, w)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_actions())

        split = QSplitter()
        split.addWidget(self.tree); split.addWidget(self.table)
        split.setStretchFactor(1, 1); split.setSizes([190, 910])
        self.setCentralWidget(split)

        self.status_lbl = QLabel()
        self.statusBar().addPermanentWidget(self.status_lbl)

        self._build_tray()
        QGuiApplication.clipboard().dataChanged.connect(self._clipboard)
        self._last_clip = QGuiApplication.clipboard().text().strip()
        bridge.request.connect(self._browser_request)
        bridge.show.connect(self.bring_to_front)

        self.model.rebuild()
        self.timer = QTimer(self, interval=500, timeout=self._on_timer)
        self.timer.start()
        self._update_actions()

    # ---------------------------------------------------------------- building
    def _act(self, text, icon=None, slot=None, shortcut=None, tip=None):
        a = QAction(text, self)
        if icon:
            a.setIcon(tool_icon(icon) if isinstance(icon, str) else icon)
        if slot:
            a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
        return a

    def _build_actions(self):
        self.a_add = self._act("Add URL", "add", self.add_url, "Ctrl+N", "Add a new download")
        self.a_resume = self._act("Resume", "resume", self.resume_selected, None, "Resume selected downloads")
        self.a_stop = self._act("Stop", "stop", self.stop_selected, None, "Stop selected downloads")
        self.a_stopall = self._act("Stop All", "stopall", self.stop_all, None, "Stop all downloads")
        self.a_delete = self._act("Delete", "delete", self.delete_selected, "Del", "Delete selected downloads")
        self.a_delcomp = self._act("Delete Completed", "delcomplete", self.delete_completed, None,
                                   "Delete all completed downloads from the list")
        self.a_options = self._act("Options", "options", self.options, None, "Configuration")
        self.a_sched = self._act("Scheduler", "scheduler", self.scheduler, None, "Scheduler")
        self.a_startq = self._act("Start Queue", "startq", self.start_queue, None, "Start main queue")
        self.a_stopq = self._act("Stop Queue", "stopq", self.stop_queue, None, "Stop main queue")
        self.a_batch = self._act("Add batch download from clipboard", None, self.add_batch)
        self.a_exit = self._act("Exit", None, self.quit_app, "Ctrl+Q")
        self.a_open = self._act("Open", None, self.open_selected)
        self.a_openf = self._act("Open folder", None, self.open_selected_folder)
        self.a_redl = self._act("Redownload", None, self.redownload_selected)
        self.a_props = self._act("Properties / Progress", None, self.props_selected)
        self.a_copy = self._act("Copy address", None, self.copy_url)
        self.a_toq = self._act("Add to queue", None, lambda: self._set_queue(True))
        self.a_fromq = self._act("Remove from queue", None, lambda: self._set_queue(False))

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Tasks")
        m.addActions([self.a_add, self.a_batch]); m.addSeparator(); m.addAction(self.a_exit)
        m = mb.addMenu("&File")
        m.addActions([self.a_open, self.a_openf, self.a_redl, self.a_props]); m.addSeparator()
        m.addAction(self.a_delete)
        m = mb.addMenu("&Downloads")
        m.addActions([self.a_resume, self.a_stop, self.a_stopall]); m.addSeparator()
        m.addActions([self.a_delcomp]); m.addSeparator()
        m.addActions([self.a_startq, self.a_stopq, self.a_sched]); m.addSeparator()
        m.addAction(self.a_options)
        self.view_menu = mb.addMenu("&View")
        m = mb.addMenu("&Help")
        m.addAction(self._act("About DownloadDeck", None, self.about))

    def _build_toolbar(self):
        tb = QToolBar("Toolbar")
        tb.setIconSize(QSize(38, 38))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setMovable(False)
        tb.setStyleSheet("QToolButton { min-width: 72px; padding: 2px 6px; }")
        for a in [self.a_add, self.a_resume, self.a_stop, self.a_stopall, self.a_delete, self.a_delcomp,
                  self.a_options, self.a_sched, self.a_startq, self.a_stopq]:
            tb.addAction(a)
        self.addToolBar(tb)
        self.view_menu.addAction(tb.toggleViewAction())

    def _build_tray(self):
        self.tray = QSystemTrayIcon(tool_icon("app"), self)
        menu = QMenu()
        menu.addAction(self._act("Show DownloadDeck", None, self.bring_to_front))
        menu.addAction(self.a_add); menu.addSeparator()
        menu.addActions([self.a_startq, self.a_stopq, self.a_stopall]); menu.addSeparator()
        menu.addAction(self.a_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.bring_to_front() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.setToolTip(APP)
        self.tray.show()

    # ---------------------------------------------------------------- helpers
    def selected(self) -> list[engine.Download]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.rows[r] for r in rows if r < len(self.model.rows)]

    def _update_actions(self):
        sel = self.selected()
        self.a_resume.setEnabled(any(not d.active and d.status != "Complete" for d in sel))
        self.a_stop.setEnabled(any(d.active for d in sel))
        self.a_delete.setEnabled(bool(sel))
        self.a_stopall.setEnabled(any(d.active for d in self.mgr.downloads))
        self.a_startq.setEnabled(not self.mgr.queue_running)
        self.a_stopq.setEnabled(self.mgr.queue_running)
        complete = bool(sel) and all(d.status == "Complete" for d in sel)
        self.a_open.setEnabled(complete); self.a_openf.setEnabled(bool(sel))

    def _restore_selection(self, ids):
        sm = self.table.selectionModel()
        for r, d in enumerate(self.model.rows):
            if d.id in ids:
                sm.select(self.model.index(r, 0),
                          sm.SelectionFlag.Select | sm.SelectionFlag.Rows)

    def bring_to_front(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    # ---------------------------------------------------------------- timer
    def _on_timer(self):
        ids = {d.id for d in self.selected()}
        if self.model.refresh():
            self._restore_selection(ids)
        self._tick += 1
        if self._tick % 30 == 0:
            # pick up a newly loaded/reloaded browser extension's ID without a restart
            threading.Thread(target=_refresh_native_host, daemon=True).start()
        if self._tick % 2 == 0:
            self.mgr.tick()
            self._check_schedule()
        while self.mgr.events:
            kind, dl = self.mgr.events.popleft()
            self._handle_event(kind, dl)
        self._update_actions()
        active = [d for d in self.mgr.downloads if d.active]
        speed = sum(d.speed for d in active)
        txt = f"{len(active)} active   {fmt_rate(speed)}" if active else "Ready"
        if self.mgr.queue_running:
            txt += "    |    Main queue running"
        self.status_lbl.setText(txt)
        self.tray.setToolTip(f"{APP}\n{txt}")

    def _handle_event(self, kind, dl):
        if kind == "complete":
            w = self.progress_windows.pop(dl.id, None)
            if w:
                w.close()
            if dl.open_when_done:
                open_path(dl.path)
            elif self.mgr.settings.get("show_complete_dialog", True):
                d = CompleteDialog(self.mgr, dl, self)
                d.show(); d.raise_(); d.activateWindow()
                self.open_dialogs.append(d)
            else:
                self.tray.showMessage("Download complete", dl.filename, QSystemTrayIcon.Information, 4000)
            if dl.shutdown_when_done:
                self._shutdown_countdown()
        elif kind == "error":
            self.tray.showMessage("Download failed", f"{dl.filename}\n{dl.error}", QSystemTrayIcon.Warning, 5000)
        elif kind == "queue_done":
            self.tray.showMessage("Queue finished", "All files in the main queue are processed.",
                                  QSystemTrayIcon.Information, 4000)
            action = self.mgr.settings["schedule"].get("when_done", "nothing")
            if action == "exit":
                QTimer.singleShot(1500, self.quit_app)
            elif action == "shutdown":
                self._shutdown_countdown()

    def _shutdown_countdown(self):
        subprocess.run(["shutdown", "/s", "/t", "60", "/c", "DownloadDeck: downloads finished"], check=False)
        box = QMessageBox(QMessageBox.Warning, APP, "The computer will turn off in 60 seconds.",
                          QMessageBox.Cancel, self)
        box.button(QMessageBox.Cancel).setText("Cancel shutdown")
        box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if box.exec() == QMessageBox.Cancel:
            subprocess.run(["shutdown", "/a"], check=False)

    def _check_schedule(self):
        sc = self.mgr.settings["schedule"]
        now = time.localtime()
        minute = time.strftime("%Y%m%d%H%M", now)
        if minute == self._sched_minute:
            return
        self._sched_minute = minute
        hm = time.strftime("%H:%M", now)
        if now.tm_wday not in sc.get("days", []):
            return
        if sc.get("enabled") and hm == sc.get("start") and not self.mgr.queue_running:
            self.mgr.start_queue()
            self.tray.showMessage("Scheduler", "Main download queue started.", QSystemTrayIcon.Information, 3000)
        if sc.get("stop_enabled") and hm == sc.get("stop") and self.mgr.queue_running:
            self.mgr.stop_queue()

    # ---------------------------------------------------------------- adding
    def add_url(self):
        d = AddUrlDialog(self)
        if d.exec():
            self.new_download(d.url.text().strip(), d.headers(), force_video=d.video_chk.isChecked())

    def add_batch(self):
        text = QGuiApplication.clipboard().text()
        urls = list(dict.fromkeys(URL_RE.findall(text)))
        if not urls:
            QMessageBox.information(self, APP, "No links found in the clipboard.")
            return
        r = QMessageBox.question(self, APP, f"Add {len(urls)} link(s) from the clipboard to the main queue?")
        if r != QMessageBox.Yes:
            return
        for u in urls:
            name = engine.sanitize(os.path.basename(u.split("?")[0])) or "download"
            cat = engine.category_for(name)
            self.mgr.add(u, os.path.join(self.mgr.folder_for(cat), name), start=False, in_queue=True,
                         auto_name=True)
        self.model.rebuild()

    def new_download(self, url, headers=None, filename=None, force_dialog=False, force_video=False):
        headers = headers or {}
        if force_video or video.is_video_url(url):
            vd = VideoDialog(self.mgr, url, self)
            vd.finished.connect(lambda res, d=vd: self._video_done(d, res))
            self.open_dialogs.append(vd)
            vd.show(); vd.raise_(); vd.activateWindow()
            return
        if self.mgr.settings.get("show_start_dialog", True) or force_dialog:
            dlg = FileInfoDialog(self.mgr, url, headers, filename, self)
            dlg.finished.connect(lambda res, d=dlg: self._file_info_done(d, res))
            self.open_dialogs.append(dlg)
            dlg.show(); dlg.raise_(); dlg.activateWindow()
        else:
            name = engine.sanitize(filename or os.path.basename(url.split("?")[0])) or "download"
            path = os.path.join(self.mgr.folder_for(engine.category_for(name)), name)
            dl = self.mgr.add(url, path, start=True, headers=headers, auto_name=not filename)
            self.model.rebuild()
            self.show_progress(dl)

    def _file_info_done(self, dlg, res):
        if dlg in self.open_dialogs:
            self.open_dialogs.remove(dlg)
        if not res or not dlg.choice:
            return
        info = dlg.result_info
        dl = self.mgr.add(dlg.url.text(), dlg.final_path, start=dlg.choice == "start",
                          description=dlg.desc.text(), headers=dlg.headers,
                          size=info.size if info else -1, resumable=bool(info and info.resumable),
                          in_queue=dlg.choice == "later", category=dlg.cat.currentText())
        self.model.rebuild()
        if dlg.choice == "start":
            self.show_progress(dl)

    def quick_video(self, url, quality):
        """Quality picked in the page's menu: start straight away, like IDM."""
        info = video.cached_info(url)
        title = (info.get("title") if info else None) or "video"
        ext = "mp3" if "MP3" in quality else "m4a" if "M4A" in quality else "mp4"
        cat = "Music" if quality.startswith("Audio") else "Video"
        path = engine.unique_path(os.path.join(self.mgr.folder_for(cat), f"{engine.sanitize(title)}.{ext}"))
        dl = self.mgr.add(url, path, start=True, kind="video", video_format=quality, category=cat)
        self.model.rebuild()
        self.show_progress(dl)

    def _video_done(self, dlg, res):
        if dlg in self.open_dialogs:
            self.open_dialogs.remove(dlg)
        if not res or not dlg.choice:
            return
        if dlg.playlist_mode:
            added = skipped = 0
            known = {os.path.normcase(d.path) for d in self.mgr.downloads}
            for url, path, title in dlg.items:
                if dlg.skip.isChecked() and (os.path.exists(path) or os.path.normcase(path) in known):
                    skipped += 1
                    continue
                self.mgr.add(url, path, start=False, kind="video", video_format=dlg.quality_label,
                             category=dlg.category, in_queue=True,
                             description=dlg.desc.text() or f"Playlist: {dlg.playlist['title']}")
                added += 1
            self.model.rebuild()
            msg = f"Added {added} video(s) from \"{dlg.playlist['title']}\" to the main queue"
            if skipped:
                msg += f" ({skipped} already there, skipped)"
            self.statusBar().showMessage(msg, 10000)
            if dlg.choice == "start" and added:
                self.mgr.start_queue()
            return
        dl = self.mgr.add(dlg.url, dlg.final_path, start=dlg.choice == "start", kind="video",
                          video_format=dlg.quality_label, category=dlg.category,
                          description=dlg.desc.text(), in_queue=dlg.choice == "later")
        self.model.rebuild()
        if dlg.choice == "start":
            self.show_progress(dl)

    def _browser_request(self, data):
        headers = {}
        if data.get("referrer"):
            headers["Referer"] = data["referrer"]
        if data.get("cookies"):
            headers["Cookie"] = data["cookies"]
        if data.get("userAgent"):
            headers["User-Agent"] = data["userAgent"]
        fname = data.get("filename") or None
        if fname:
            fname = os.path.basename(fname.replace("\\", "/"))
        quality = data.get("quality")
        if quality and quality != "__dialog__":
            self.quick_video(data["url"], quality)
            return
        self.new_download(data["url"], headers, fname, force_video=bool(data.get("video")))

    def _clipboard(self):
        if not self.mgr.settings.get("clipboard_monitor", True):
            return
        text = QGuiApplication.clipboard().text().strip()
        if not text or text == self._last_clip:
            return
        self._last_clip = text
        if "\n" in text or " " in text or not URL_RE.fullmatch(text):
            return
        if video.is_video_url(text):
            self.new_download(text)
            return
        ext = os.path.splitext(text.split("?")[0].split("#")[0])[1].lower().lstrip(".")
        if ext in engine.ALL_EXTS and not any(d.url == text and d.active for d in self.mgr.downloads):
            self.new_download(text)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        urls = [u.toString() for u in md.urls() if u.scheme() in ("http", "https")] if md.hasUrls() else []
        if not urls and md.hasText():
            urls = URL_RE.findall(md.text())
        for u in urls[:20]:
            self.new_download(u)

    # ---------------------------------------------------------------- actions
    def show_progress(self, dl):
        w = self.progress_windows.get(dl.id)
        if w is None or not w.isVisible():
            w = ProgressDialog(self.mgr, dl, None)
            w.setAttribute(Qt.WA_DeleteOnClose)
            w.destroyed.connect(lambda *_, i=dl.id: self.progress_windows.pop(i, None))
            self.progress_windows[dl.id] = w
        w.show(); w.raise_(); w.activateWindow()

    def _double_click(self, idx):
        d = self.model.rows[idx.row()]
        if d.status == "Complete":
            if os.path.exists(d.path):
                open_path(d.path)
            else:
                QMessageBox.warning(self, APP, f"The file was moved or deleted:\n{d.path}")
        else:
            self.show_progress(d)

    def resume_selected(self):
        for d in self.selected():
            if d.status != "Complete":
                self.mgr.start(d)
                if len(self.selected()) == 1:
                    self.show_progress(d)

    def stop_selected(self):
        for d in self.selected():
            self.mgr.stop(d)

    def stop_all(self):
        self.mgr.stop_all()

    def delete_selected(self):
        sel = self.selected()
        if not sel:
            return
        dlg = DeleteDialog(len(sel), any(d.status == "Complete" for d in sel), self)
        if not dlg.exec():
            return
        for d in sel:
            w = self.progress_windows.pop(d.id, None)
            if w:
                w.close()
            threading.Thread(target=self.mgr.remove, args=(d, dlg.disk.isChecked()), daemon=True).start()
            if d in self.mgr.downloads:
                self.mgr.downloads.remove(d)
        self.model.rebuild()

    def delete_completed(self):
        done = [d for d in self.mgr.downloads if d.status == "Complete"]
        if not done:
            return
        if QMessageBox.question(self, APP, f"Remove {len(done)} completed download(s) from the list?\n"
                                           "(The files stay on disk.)") != QMessageBox.Yes:
            return
        for d in done:
            self.mgr.downloads.remove(d)
        self.mgr.save()
        self.model.rebuild()

    def open_selected(self):
        for d in self.selected()[:5]:
            if d.status == "Complete":
                open_path(d.path)

    def open_selected_folder(self):
        sel = self.selected()
        if sel:
            open_folder(sel[0].path if sel[0].status == "Complete" else sel[0].part_path)

    def redownload_selected(self):
        for d in self.selected():
            self.mgr.stop(d)
            if d.task:
                d.task.join(timeout=2)
            try:
                if os.path.exists(d.part_path):
                    os.remove(d.part_path)
            except OSError:
                pass
            if d.kind == "video":
                video.remove_partials(d)
            d.segments, d.vfiles, d.status, d.error = [], {}, "Queued", ""
            if os.path.exists(d.path):
                d.filename = os.path.basename(engine.unique_path(d.path))
            self.mgr.start(d)
            self.show_progress(d)

    def props_selected(self):
        for d in self.selected()[:3]:
            self.show_progress(d)

    def copy_url(self):
        sel = self.selected()
        if sel:
            txt = "\n".join(d.url for d in sel)
            self._last_clip = txt
            QGuiApplication.clipboard().setText(txt)

    def _set_queue(self, on):
        for d in self.selected():
            if d.status != "Complete":
                d.in_queue = on
        self.mgr.save()

    def start_queue(self):
        if not any(d.in_queue and d.status != "Complete" for d in self.mgr.downloads):
            QMessageBox.information(self, APP, "The main queue is empty.\n\nUse \"Download Later\" or "
                                               "right-click a download > Add to queue.")
            return
        self.mgr.start_queue()

    def stop_queue(self):
        self.mgr.stop_queue()

    def options(self):
        OptionsDialog(self.mgr, EXT_DIR, self).exec()

    def scheduler(self):
        SchedulerDialog(self.mgr, self).exec()

    def about(self):
        QMessageBox.about(self, f"About {APP}",
                          f"<b>{APP} {VERSION}</b><br>Multi-connection download manager.<br><br>"
                          f"Data folder:<br>{DATA_DIR}")

    def _context_menu(self, pos):
        sel = self.selected()
        if not sel:
            return
        m = QMenu(self)
        m.addActions([self.a_open, self.a_openf]); m.addSeparator()
        m.addActions([self.a_resume, self.a_stop, self.a_redl]); m.addSeparator()
        m.addActions([self.a_toq, self.a_fromq]); m.addSeparator()
        m.addActions([self.a_copy, self.a_props]); m.addSeparator()
        m.addAction(self.a_delete)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _filter_changed(self, cur, _prev):
        if cur:
            self.model.filter = cur.data(0, Qt.UserRole)
            self.model.rebuild()

    # ---------------------------------------------------------------- closing
    def closeEvent(self, e):
        if not self._quitting and self.mgr.settings.get("minimize_to_tray", True) and self.tray.isVisible():
            e.ignore()
            self.hide()
            if not self.mgr.settings.get("_tray_hint_shown"):
                self.tray.showMessage(APP, "Still running in the tray. Right-click the icon to exit.",
                                      QSystemTrayIcon.Information, 3000)
                self.mgr.settings["_tray_hint_shown"] = True
            return
        self.quit_app()

    def quit_app(self):
        self._quitting = True
        self.timer.stop()
        self.mgr.shutdown()
        self.tray.hide()
        QApplication.quit()


# ======================================================================= first run
def make_shortcut(lnk_path, target, args=""):
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:DD_LNK);"
          "$s.TargetPath=$env:DD_TARGET;$s.Arguments=$env:DD_ARGS;"
          "$s.WorkingDirectory=(Split-Path $env:DD_TARGET);$s.IconLocation=$env:DD_TARGET+',0';$s.Save()")
    env = {**os.environ, "DD_LNK": lnk_path, "DD_TARGET": target, "DD_ARGS": args}
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=False,
                   creationflags=0x08000000)  # CREATE_NO_WINDOW


def first_run_setup(win, mgr):
    mgr.settings["_setup_done"] = True
    mgr.save()
    r = QMessageBox.question(win, f"Welcome to {APP}",
                             "Create a desktop shortcut and start DownloadDeck with Windows?\n\n"
                             "(It then waits quietly in the tray, ready for browser downloads.)")
    if r != QMessageBox.Yes:
        return
    exe = sys.executable
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    try:
        import ctypes.wintypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)   # real Desktop (OneDrive too)
        desktop = buf.value or desktop
    except Exception:  # noqa: BLE001
        pass
    startup = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")
    make_shortcut(os.path.join(desktop, f"{APP}.lnk"), exe)
    make_shortcut(os.path.join(startup, f"{APP}.lnk"), exe, "--tray")
    win.statusBar().showMessage("Shortcuts created on the desktop and in Startup.", 8000)


# ======================================================================= main
def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    mgr = engine.Manager(DATA_DIR)
    port = int(mgr.settings.get("browser_port", 9614))

    # single instance: if already running, bring it forward and pass along URLs
    try:
        r = requests.get(f"http://127.0.0.1:{port}/ping", timeout=0.7)
        if r.ok and r.json().get("app") == APP:
            h = {"X-DownloadDeck": "1"}
            urls = [a for a in sys.argv[1:] if a.lower().startswith(("http://", "https://"))]
            if urls:
                requests.post(f"http://127.0.0.1:{port}/download", json={"urls": urls}, headers=h, timeout=2)
            requests.post(f"http://127.0.0.1:{port}/show", json={}, headers=h, timeout=2)
            return 0
    except (requests.RequestException, ValueError):
        pass

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DownloadDeck.App")
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(tool_icon("app"))
    # classic light look regardless of the Windows dark-mode setting
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("windowsvista")
    app.setStyleSheet("QProgressBar { border: 1px solid #8a99a8; background: #fff; }"
                      "QProgressBar::chunk { background: #3d7fd6; }")

    try:
        import native_reg
        native_reg.register()        # keeps the browser bridge pointing at this folder
    except Exception as e:  # noqa: BLE001
        log(f"native host registration failed: {e}")
    bridge = Bridge()
    win = MainWindow(mgr, bridge)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(bridge))
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except OSError:
        win.statusBar().showMessage(f"Browser integration off: port {port} is in use (change it in Options).")
    if "--tray" not in sys.argv:
        win.show()
    if getattr(sys, "frozen", False) and not mgr.settings.get("_setup_done"):
        QTimer.singleShot(600, lambda: first_run_setup(win, mgr))
    for a in sys.argv[1:]:
        if a.lower().startswith(("http://", "https://")):
            win.new_download(a)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
