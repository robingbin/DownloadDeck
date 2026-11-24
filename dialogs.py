"""All DownloadDeck dialogs."""
from __future__ import annotations

import base64
import os
import subprocess

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

import engine
from ui_common import fmt_date, fmt_rate, fmt_size, fmt_time, tool_icon


def open_path(path):
    try:
        os.startfile(path)  # noqa: S606 - user-requested open
    except OSError as e:
        QMessageBox.warning(None, "DownloadDeck", f"Cannot open file:\n{e}")


def open_folder(path):
    if os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            os.startfile(folder)  # noqa: S606


# ----------------------------------------------------------------- probe
class ProbeThread(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, url, headers):
        super().__init__()
        self.url, self.headers = url, headers

    def run(self):
        try:
            self.done.emit(engine.probe(self.url, self.headers))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(engine._err(e))


# ----------------------------------------------------------------- Add URL
class AddUrlDialog(QDialog):
    def __init__(self, parent=None, url=""):
        super().__init__(parent)
        self.setWindowTitle("Enter new address to download")
        self.setWindowIcon(tool_icon("add"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        icon = QLabel(); icon.setPixmap(tool_icon("add").pixmap(40, 40))
        row.addWidget(icon)
        form = QFormLayout()
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://")
        form.addRow("Address:", self.url)
        row.addLayout(form, 1)
        lay.addLayout(row)
        self.video_chk = QCheckBox("Grab the video from this page (YouTube, Vimeo, Facebook, X, TikTok ...)")
        lay.addWidget(self.video_chk)
        self.url.textChanged.connect(self._url_changed)
        self.auth = QCheckBox("Use authorization")
        lay.addWidget(self.auth)
        auth_box = QWidget(); af = QFormLayout(auth_box); af.setContentsMargins(24, 0, 0, 0)
        self.user, self.pwd = QLineEdit(), QLineEdit()
        self.pwd.setEchoMode(QLineEdit.Password)
        af.addRow("Login:", self.user); af.addRow("Password:", self.pwd)
        auth_box.setEnabled(False)
        self.auth.toggled.connect(auth_box.setEnabled)
        lay.addWidget(auth_box)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if not url:
            clip = QGuiApplication.clipboard().text().strip()
            if clip.startswith(("http://", "https://", "ftp://")) and " " not in clip:
                self.url.setText(clip)
        self.url.selectAll()

    def _url_changed(self, text):
        import video
        self.video_chk.setChecked(video.is_video_url(text.strip()))

    def _ok(self):
        u = self.url.text().strip()
        if not u.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "DownloadDeck", "Please enter a valid http:// or https:// address.")
            return
        self.accept()

    def headers(self):
        if self.auth.isChecked() and self.user.text():
            tok = base64.b64encode(f"{self.user.text()}:{self.pwd.text()}".encode()).decode()
            return {"Authorization": f"Basic {tok}"}
        return {}


# ----------------------------------------------------------------- File info
class FileInfoDialog(QDialog):
    """'Download File Info' - choose category / folder, start now or later."""

    def __init__(self, mgr: engine.Manager, url, headers=None, filename=None, parent=None):
        super().__init__(parent)
        self.mgr, self.headers = mgr, headers or {}
        self.result_info = None
        self.choice = None
        self.setWindowTitle("Download File Info")
        self.setWindowIcon(tool_icon("app"))
        self.setMinimumWidth(620)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        lay = QVBoxLayout(self)
        grid = QGridLayout()
        grid.addWidget(QLabel("URL"), 0, 0)
        self.url = QLineEdit(url); self.url.setReadOnly(True)
        grid.addWidget(self.url, 0, 1, 1, 3)
        grid.addWidget(QLabel("Category"), 1, 0)
        self.cat = QComboBox()
        self.cat.addItems(["General", "Compressed", "Documents", "Music", "Programs", "Video"])
        grid.addWidget(self.cat, 1, 1)
        grid.addWidget(QLabel("Save As"), 2, 0)
        self.path = QLineEdit()
        grid.addWidget(self.path, 2, 1, 1, 2)
        browse = QPushButton("..."); browse.setFixedWidth(34)
        browse.clicked.connect(self._browse)
        grid.addWidget(browse, 2, 3)
        self.remember = QCheckBox("Remember this path for \"General\" category")
        grid.addWidget(self.remember, 3, 1, 1, 3)
        grid.addWidget(QLabel("Description"), 4, 0)
        self.desc = QLineEdit()
        grid.addWidget(self.desc, 4, 1, 1, 3)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)

        info = QHBoxLayout()
        self.icon = QLabel(); self.icon.setPixmap(tool_icon("app").pixmap(36, 36))
        info.addWidget(self.icon)
        self.size_lbl = QLabel("Getting file information...")
        f = self.size_lbl.font(); f.setBold(True); self.size_lbl.setFont(f)
        info.addWidget(self.size_lbl, 1)
        lay.addLayout(info)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.later = QPushButton("Download Later")
        self.start = QPushButton("Start Download")
        self.start.setDefault(True)
        cancel = QPushButton("Cancel")
        for b in (self.later, self.start, cancel):
            b.setMinimumWidth(120)
            btns.addWidget(b)
        lay.addLayout(btns)
        self.later.clicked.connect(lambda: self._finish("later"))
        self.start.clicked.connect(lambda: self._finish("start"))
        cancel.clicked.connect(self.reject)

        self._user_edited = False
        self.path.textEdited.connect(lambda *_: setattr(self, "_user_edited", True))
        name = engine.sanitize(filename) if filename else engine.sanitize(
            os.path.basename(url.split("?")[0].split("#")[0])) or "download"
        self._set_name(name)
        self.cat.currentTextChanged.connect(self._cat_changed)

        self.thread = ProbeThread(url, self.headers)
        self.thread.done.connect(self._probed)
        self.thread.failed.connect(self._failed)
        self.thread.start()

    def _set_name(self, name):
        cat = engine.category_for(name)
        self.cat.blockSignals(True)
        self.cat.setCurrentText(cat)
        self.cat.blockSignals(False)
        self.remember.setText(f'Remember this path for "{cat}" category')
        folder = self.mgr.folder_for(cat)
        self.path.setText(os.path.join(folder, name))

    def _cat_changed(self, cat):
        name = os.path.basename(self.path.text())
        self.path.setText(os.path.join(self.mgr.folder_for(cat), name))
        self.remember.setText(f'Remember this path for "{cat}" category')

    def _browse(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save As", self.path.text())
        if p:
            self.path.setText(os.path.normpath(p)); self._user_edited = True

    def _probed(self, info):
        self.result_info = info
        if not self._user_edited and info.filename:
            self._set_name(info.filename)
        resume = "Yes" if info.resumable else "No"
        self.size_lbl.setText(f"{fmt_size(info.size)}" + ("" if info.size >= 0 else "")
                              + f"      Resume capability: {resume}")

    def _failed(self, msg):
        self.size_lbl.setText(f"Could not get file information ({msg}). You can still try.")

    def _finish(self, choice):
        p = self.path.text().strip()
        if not p or not os.path.basename(p):
            return
        if os.path.exists(p):
            r = QMessageBox.question(
                self, "File exists",
                f"{os.path.basename(p)} already exists.\n\nYes = add a number to the name\nNo = overwrite",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                p = engine.unique_path(p)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass
        if self.remember.isChecked():
            folder = os.path.dirname(p)
            self.mgr.settings.setdefault("category_dirs", {})[self.cat.currentText()] = folder
        self.final_path = p
        self.choice = choice
        self.accept()


# ----------------------------------------------------------------- progress
class SegmentBar(QWidget):
    """Shows which parts of the file are downloaded and where each connection is."""

    def __init__(self, dl):
        super().__init__()
        self.dl = dl
        self.setMinimumHeight(22)

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(r, QColor("#ffffff"))
        dl = self.dl
        if dl.kind == "video":
            p.fillRect(QRectF(r.x(), r.y() + 1, r.width() * dl.progress / 100, r.height() - 1),
                       QColor("#3d7fd6"))
        elif dl.size > 0:
            w = r.width() / dl.size
            with dl.lock:
                segs = [(s.start, s.pos, s.owner) for s in dl.segments]
            for start, pos, owner in segs:
                x = r.x() + start * w
                p.fillRect(QRectF(x, r.y() + 1, max(1.0, pos * w), r.height() - 1), QColor("#3d7fd6"))
                if owner is not None:
                    p.fillRect(QRectF(x + pos * w - 1, r.y(), 2, r.height()), QColor("#e8a317"))
        elif dl.status == "Complete":
            p.fillRect(r, QColor("#3d7fd6"))
        p.setPen(QPen(QColor("#7a8a99")))
        p.drawRect(r)


class ProgressDialog(QDialog):
    def __init__(self, mgr, dl, parent=None):
        super().__init__(parent)
        self.mgr, self.dl = mgr, dl
        self.setWindowIcon(tool_icon("app"))
        self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- status tab
        st = QWidget(); sl = QVBoxLayout(st)
        self.url_lbl = QLabel(); self.url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.url_lbl.setWordWrap(False)
        sl.addWidget(self.url_lbl)
        self.status_lbl = QLabel()
        sl.addWidget(self.status_lbl)
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken)
        sl.addWidget(line)
        g = QGridLayout()
        self.vals = {}
        for i, k in enumerate(["File size", "Downloaded", "Transfer rate", "Time left", "Resume capability"]):
            g.addWidget(QLabel(k), i, 0)
            v = QLabel(); self.vals[k] = v
            g.addWidget(v, i, 1)
        g.setColumnStretch(1, 1)
        sl.addLayout(g)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setTextVisible(False)
        self.bar.setFixedHeight(18)
        sl.addWidget(self.bar)
        btns = QHBoxLayout()
        self.details_btn = QPushButton("<< Hide details")
        self.pause_btn = QPushButton("Pause")
        self.cancel_btn = QPushButton("Cancel")
        btns.addWidget(self.details_btn); btns.addStretch(1)
        btns.addWidget(self.pause_btn); btns.addWidget(self.cancel_btn)
        sl.addLayout(btns)
        tabs.addTab(st, "Download status")

        # --- speed limiter
        sp = QWidget(); spl = QVBoxLayout(sp)
        self.limit_chk = QCheckBox("Use speed limiter")
        row = QHBoxLayout()
        row.addWidget(QLabel("Maximum download speed")); self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 10_000_000); self.limit_spin.setValue(max(1, dl.limiter.rate // 1024 or 500))
        row.addWidget(self.limit_spin); row.addWidget(QLabel("KB/sec")); row.addStretch(1)
        self.limit_chk.setChecked(dl.limiter.rate > 0)
        spl.addWidget(self.limit_chk); spl.addLayout(row); spl.addStretch(1)
        self.limit_chk.toggled.connect(self._apply_limit)
        self.limit_spin.valueChanged.connect(self._apply_limit)
        tabs.addTab(sp, "Speed Limiter")

        # --- on completion
        oc = QWidget(); ol = QVBoxLayout(oc)
        self.show_done = QCheckBox("Show download complete dialog")
        self.show_done.setChecked(mgr.settings.get("show_complete_dialog", True))
        self.open_done = QCheckBox("Open file when done")
        self.open_done.setChecked(dl.open_when_done)
        self.off_done = QCheckBox("Turn off computer when done (60 second warning)")
        self.off_done.setChecked(dl.shutdown_when_done)
        for w in (self.show_done, self.open_done, self.off_done):
            ol.addWidget(w)
        ol.addStretch(1)
        self.show_done.toggled.connect(lambda v: mgr.settings.__setitem__("show_complete_dialog", v))
        self.open_done.toggled.connect(lambda v: setattr(dl, "open_when_done", v))
        self.off_done.toggled.connect(lambda v: setattr(dl, "shutdown_when_done", v))
        tabs.addTab(oc, "Options on completion")

        # --- details
        self.details = QWidget(); dl_lay = QVBoxLayout(self.details); dl_lay.setContentsMargins(0, 0, 0, 0)
        dl_lay.addWidget(QLabel("Start positions and download progress by connections"))
        self.segbar = SegmentBar(dl)
        dl_lay.addWidget(self.segbar)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["N.", "Downloaded", "Info"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setMinimumHeight(150)
        dl_lay.addWidget(self.table)
        lay.addWidget(self.details)

        self.details_btn.clicked.connect(self._toggle_details)
        self.pause_btn.clicked.connect(self._pause)
        self.cancel_btn.clicked.connect(self._cancel)
        self.timer = QTimer(self, interval=500, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def _apply_limit(self):
        self.dl.limiter.rate = self.limit_spin.value() * 1024 if self.limit_chk.isChecked() else 0

    def _toggle_details(self):
        vis = not self.details.isVisible()
        self.details.setVisible(vis)
        self.details_btn.setText("<< Hide details" if vis else "Show details >>")
        QTimer.singleShot(0, self.adjustSize)

    def _pause(self):
        if self.dl.active:
            self.mgr.stop(self.dl)
        else:
            self.mgr.start(self.dl)

    def _cancel(self):
        self.mgr.stop(self.dl)
        self.close()

    def refresh(self):
        dl = self.dl
        pct = dl.progress
        self.setWindowTitle(f"{int(pct)}% {dl.filename}")
        self.url_lbl.setText(dl.url if len(dl.url) < 90 else dl.url[:87] + "...")
        self.url_lbl.setToolTip(dl.url)
        if dl.status == "Downloading":
            st = "Receiving data..."
        elif dl.status == "Error":
            st = f"Error: {dl.error}"
        elif dl.status == "Complete":
            st = "Complete"
        elif dl.status == "Paused":
            st = "Paused"
        else:
            st = dl.status + "..."
        self.status_lbl.setText(f"<b>Status</b>&nbsp;&nbsp;&nbsp;{st}")
        self.vals["File size"].setText(fmt_size(dl.size))
        self.vals["Downloaded"].setText(f"{fmt_size(dl.downloaded)} ( {pct:.2f} % )")
        self.vals["Transfer rate"].setText(fmt_rate(dl.speed) or "-")
        self.vals["Time left"].setText(fmt_time(dl.time_left) or "-")
        self.vals["Resume capability"].setText("Yes" if dl.resumable else "No")
        self.bar.setValue(int(pct * 10))
        self.pause_btn.setText("Pause" if dl.active else "Resume")
        self.pause_btn.setEnabled(dl.status != "Complete")
        conns = list(dl.conns)
        self.table.setRowCount(len(conns))
        for i, c in enumerate(conns):
            for col, text in enumerate([str(i + 1), fmt_size(c.downloaded), c.status]):
                it = self.table.item(i, col)
                if it is None:
                    it = QTableWidgetItem(); self.table.setItem(i, col, it)
                it.setText(text)
        self.segbar.update()
        if dl.status == "Complete":
            self.timer.stop()
            self.close()

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)


# ----------------------------------------------------------------- complete
class CompleteDialog(QDialog):
    def __init__(self, mgr, dl, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download complete")
        self.setWindowIcon(tool_icon("app"))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        head = QLabel("<b>Download complete</b>")
        lay.addWidget(head)
        g = QGridLayout()
        rows = [("Downloaded", f"{fmt_size(dl.size)}"), ("Address", dl.url), ("The file saved as", dl.path)]
        for i, (k, v) in enumerate(rows):
            g.addWidget(QLabel(k), i, 0)
            e = QLineEdit(v); e.setReadOnly(True); e.setCursorPosition(0)
            g.addWidget(e, i, 1)
        lay.addLayout(g)
        btn = QHBoxLayout()
        ob = QPushButton("Open"); ow = QPushButton("Open with..."); of = QPushButton("Open folder")
        cb = QPushButton("Close")
        for b in (ob, ow, of, cb):
            b.setMinimumWidth(100); btn.addWidget(b)
        lay.addLayout(btn)
        self.dont = QCheckBox("Don't show this dialog again")
        lay.addWidget(self.dont)
        ob.clicked.connect(lambda: (open_path(dl.path), self.accept()))
        ow.clicked.connect(lambda: (subprocess.Popen(["rundll32", "shell32.dll,OpenAs_RunDLL", dl.path]),
                                    self.accept()))
        of.clicked.connect(lambda: (open_folder(dl.path), self.accept()))
        cb.clicked.connect(self.accept)
        cb.setDefault(True)
        self.finished.connect(lambda *_: self.dont.isChecked()
                              and mgr.settings.__setitem__("show_complete_dialog", False))


# ----------------------------------------------------------------- delete
class DeleteDialog(QDialog):
    def __init__(self, count, any_complete, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm deletion of downloads")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Are you sure you want to delete {count} selected download(s) from the list?"))
        self.disk = QCheckBox("Also delete the downloaded file(s) from disk")
        self.disk.setVisible(any_complete)
        lay.addWidget(self.disk)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# ----------------------------------------------------------------- options
class OptionsDialog(QDialog):
    def __init__(self, mgr, ext_dir, parent=None):
        super().__init__(parent)
        self.mgr = mgr
        s = mgr.settings
        self.setWindowTitle("DownloadDeck Configuration")
        self.setWindowIcon(tool_icon("options"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget(); lay.addWidget(tabs)

        gen = QWidget(); gl = QVBoxLayout(gen)
        self.clip = QCheckBox("Monitor clipboard for downloadable links")
        self.startdlg = QCheckBox("Show start download dialog")
        self.donedlg = QCheckBox("Show download complete dialog")
        self.tray = QCheckBox("Keep running in the system tray when the window is closed")
        self.clip.setChecked(s["clipboard_monitor"]); self.startdlg.setChecked(s["show_start_dialog"])
        self.donedlg.setChecked(s["show_complete_dialog"]); self.tray.setChecked(s["minimize_to_tray"])
        for w in (self.clip, self.startdlg, self.donedlg, self.tray):
            gl.addWidget(w)
        gl.addStretch(1)
        tabs.addTab(gen, "General")

        con = QWidget(); cf = QFormLayout(con)
        self.conns = QSpinBox(); self.conns.setRange(1, 32); self.conns.setValue(s["connections"])
        self.maxsim = QSpinBox(); self.maxsim.setRange(1, 20); self.maxsim.setValue(s["max_simultaneous"])
        self.limit = QSpinBox(); self.limit.setRange(0, 10_000_000); self.limit.setValue(s["speed_limit_kb"])
        self.limit.setSuffix(" KB/sec"); self.limit.setSpecialValueText("Unlimited")
        cf.addRow("Max. connections per download:", self.conns)
        cf.addRow("Max. simultaneous downloads in queue:", self.maxsim)
        cf.addRow("Global speed limit:", self.limit)
        tabs.addTab(con, "Connection")

        sv = QWidget(); sl = QVBoxLayout(sv)
        row = QHBoxLayout()
        self.folder = QLineEdit(s["download_dir"])
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse)
        row.addWidget(QLabel("Default download directory:")); row.addWidget(self.folder, 1); row.addWidget(b)
        sl.addLayout(row)
        self.subf = QCheckBox("Save each category to its own subfolder (Video, Music, Programs ...)")
        self.subf.setChecked(s["category_subfolders"])
        sl.addWidget(self.subf); sl.addStretch(1)
        tabs.addTab(sv, "Save To")

        br = QWidget(); bl = QVBoxLayout(br)
        pf = QFormLayout()
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(s["browser_port"])
        pf.addRow("Local port for browser extension:", self.port)
        bl.addLayout(pf)
        info = QLabel(
            "<b>Capture downloads from Chrome / Edge / Brave</b><ol>"
            "<li>Open <code>chrome://extensions</code> (or <code>edge://extensions</code>)</li>"
            "<li>Turn on <b>Developer mode</b></li>"
            "<li>Click <b>Load unpacked</b> and pick the extension folder below</li></ol>"
            "Downloads then go to DownloadDeck automatically, and links get a "
            "<i>Download with DownloadDeck</i> right-click item.")
        info.setWordWrap(True)
        bl.addWidget(info)
        e = QLineEdit(ext_dir); e.setReadOnly(True)
        ob = QPushButton("Open extension folder")
        ob.clicked.connect(lambda: os.startfile(ext_dir))  # noqa: S606
        r2 = QHBoxLayout(); r2.addWidget(e, 1); r2.addWidget(ob)
        bl.addLayout(r2); bl.addStretch(1)
        tabs.addTab(br, "Browser Integration")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Download directory", self.folder.text())
        if d:
            self.folder.setText(os.path.normpath(d))

    def _ok(self):
        s = self.mgr.settings
        s.update(clipboard_monitor=self.clip.isChecked(), show_start_dialog=self.startdlg.isChecked(),
                 show_complete_dialog=self.donedlg.isChecked(), minimize_to_tray=self.tray.isChecked(),
                 connections=self.conns.value(), max_simultaneous=self.maxsim.value(),
                 speed_limit_kb=self.limit.value(), download_dir=self.folder.text(),
                 category_subfolders=self.subf.isChecked(), browser_port=self.port.value())
        self.mgr.apply_settings()
        self.mgr.save()
        self.accept()


# ----------------------------------------------------------------- scheduler
class SchedulerDialog(QDialog):
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def __init__(self, mgr, parent=None):
        super().__init__(parent)
        self.mgr = mgr
        sc = mgr.settings["schedule"]
        self.setWindowTitle("Scheduler - Main download queue")
        self.setWindowIcon(tool_icon("scheduler"))
        self.setMinimumWidth(600)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        tabs = QTabWidget()
        left.addWidget(tabs)

        sch = QWidget(); sl = QVBoxLayout(sch)
        self.start_chk = QCheckBox("Start download at")
        self.start_time = QTimeEdit(QTime.fromString(sc["start"], "HH:mm")); self.start_time.setDisplayFormat("HH:mm")
        self.start_chk.setChecked(sc["enabled"])
        r = QHBoxLayout(); r.addWidget(self.start_chk); r.addWidget(self.start_time); r.addStretch(1)
        sl.addLayout(r)
        days = QGroupBox("Every day / on these days"); dlay = QHBoxLayout(days)
        self.day_chks = []
        for i, d in enumerate(self.DAYS):
            c = QCheckBox(d); c.setChecked(i in sc["days"]); dlay.addWidget(c); self.day_chks.append(c)
        sl.addWidget(days)
        self.stop_chk = QCheckBox("Stop download at")
        self.stop_time = QTimeEdit(QTime.fromString(sc["stop"], "HH:mm")); self.stop_time.setDisplayFormat("HH:mm")
        self.stop_chk.setChecked(sc["stop_enabled"])
        r = QHBoxLayout(); r.addWidget(self.stop_chk); r.addWidget(self.stop_time); r.addStretch(1)
        sl.addLayout(r)
        done = QGroupBox("When the queue is done"); dl = QVBoxLayout(done)
        self.rb = {}
        for k, t in (("nothing", "Do nothing"), ("exit", "Exit DownloadDeck"),
                     ("shutdown", "Turn off computer (60 second warning, cancellable)")):
            b = QRadioButton(t); b.setChecked(sc["when_done"] == k); dl.addWidget(b); self.rb[k] = b
        sl.addWidget(done)
        sl.addStretch(1)
        tabs.addTab(sch, "Schedule")

        fl = QWidget(); fll = QVBoxLayout(fl)
        r = QHBoxLayout()
        r.addWidget(QLabel("Download")); self.maxsim = QSpinBox(); self.maxsim.setRange(1, 20)
        self.maxsim.setValue(mgr.settings["max_simultaneous"]); r.addWidget(self.maxsim)
        r.addWidget(QLabel("files at the same time")); r.addStretch(1)
        fll.addLayout(r)
        self.files = QListWidget()
        self._fill()
        fll.addWidget(self.files)
        rm = QPushButton("Remove from queue")
        rm.clicked.connect(self._remove)
        fll.addWidget(rm, 0, Qt.AlignLeft)
        tabs.addTab(fl, "Files in the queue")
        lay.addLayout(left, 1)

        right = QVBoxLayout()
        for text, fn in (("Start now", self._start), ("Stop", self._stop), ("Apply", self._apply),
                         ("Close", self._close)):
            b = QPushButton(text); b.setMinimumWidth(100); b.clicked.connect(fn); right.addWidget(b)
        right.addStretch(1)
        lay.addLayout(right)

    def _fill(self):
        self.files.clear()
        for d in self.mgr.downloads:
            if d.in_queue:
                it = d.filename + ("   (running)" if d.active else "")
                self.files.addItem(it)
                self.files.item(self.files.count() - 1).setData(Qt.UserRole, d.id)

    def _remove(self):
        for it in self.files.selectedItems():
            for d in self.mgr.downloads:
                if d.id == it.data(Qt.UserRole):
                    d.in_queue = False
        self._fill()

    def _apply(self):
        sc = self.mgr.settings["schedule"]
        sc.update(enabled=self.start_chk.isChecked(), start=self.start_time.time().toString("HH:mm"),
                  stop_enabled=self.stop_chk.isChecked(), stop=self.stop_time.time().toString("HH:mm"),
                  days=[i for i, c in enumerate(self.day_chks) if c.isChecked()],
                  when_done=next(k for k, b in self.rb.items() if b.isChecked()))
        self.mgr.settings["max_simultaneous"] = self.maxsim.value()
        self.mgr.save()

    def _start(self):
        self._apply(); self.mgr.start_queue(); self._fill()

    def _stop(self):
        self.mgr.stop_queue(); self._fill()

    def _close(self):
        self._apply(); self.accept()



# ----------------------------------------------------------------- video
def _clean_err(e) -> str:
    return str(e).replace("ERROR: ", "").replace("\x1b[0;31m", "").replace("\x1b[0m", "")[:300]


class VideoInfoThread(QThread):
    done = Signal(object, bytes)
    failed = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        import requests
        import video
        try:
            info = video.fetch_info(self.url)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(_clean_err(e))
            return
        thumb = b""
        thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
        jpgs = [t for t in thumbs if ".jpg" in t["url"]] or thumbs
        small = [t for t in jpgs if 0 < (t.get("width") or 0) <= 640]
        pick = (max(small, key=lambda t: t["width"]) if small else (jpgs[-1] if jpgs else None))
        url = pick["url"] if pick else info.get("thumbnail")
        if url:
            try:
                thumb = requests.get(url, timeout=10).content
            except requests.RequestException:
                pass
        self.done.emit(info, thumb)


class PlaylistThread(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        import video
        try:
            self.done.emit(video.fetch_playlist(self.url))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(_clean_err(e))


class VideoDialog(QDialog):
    """Pick quality / location for one video, or for the videos of a playlist."""

    def __init__(self, mgr, url, parent=None):
        super().__init__(parent)
        import video
        self.video = video
        self.mgr, self.url = mgr, url
        self.choice = None
        self.title = ""
        self.playlist = None              # fetched playlist info
        self.single_info = None
        self.category = "Video"
        self.pl_url = video.playlist_url(url)
        self.pl_only = bool(self.pl_url) and video.is_playlist_only(url)
        self.setWindowTitle("Download playlist" if self.pl_only else "Download video")
        self.setWindowIcon(tool_icon("app"))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(700)
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.thumb = QLabel("Loading...")
        self.thumb.setFixedSize(240, 135)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:#20262c; color:#ccc; border:1px solid #8a99a8;")
        top.addWidget(self.thumb)
        right = QVBoxLayout()
        self.title_lbl = QLabel("Getting playlist..." if self.pl_only else "Getting video information...")
        f = self.title_lbl.font(); f.setBold(True); f.setPointSize(f.pointSize() + 1)
        self.title_lbl.setFont(f); self.title_lbl.setWordWrap(True)
        self.meta_lbl = QLabel(url); self.meta_lbl.setWordWrap(True)
        self.meta_lbl.setStyleSheet("color:#555")
        right.addWidget(self.title_lbl); right.addWidget(self.meta_lbl)
        # "this video" vs "whole playlist" (only for a video opened from inside a playlist)
        self.rb_single = QRadioButton("This video only")
        self.rb_list = QRadioButton("Whole playlist (loading...)")
        self.rb_single.setChecked(True)
        self.rb_list.setEnabled(False)
        mode = QHBoxLayout(); mode.addWidget(self.rb_single); mode.addWidget(self.rb_list); mode.addStretch(1)
        self.mode_box = QWidget(); self.mode_box.setLayout(mode)
        mode.setContentsMargins(0, 6, 0, 0)
        self.mode_box.setVisible(bool(self.pl_url) and not self.pl_only)
        right.addWidget(self.mode_box)
        right.addStretch(1)
        top.addLayout(right, 1)
        if self.pl_only:
            self.thumb.hide()
        lay.addLayout(top)

        # playlist entries
        self.pl_box = QWidget(); pll = QVBoxLayout(self.pl_box); pll.setContentsMargins(0, 0, 0, 0)
        self.pl_list = QListWidget()
        self.pl_list.setMinimumHeight(220)
        pll.addWidget(self.pl_list)
        row = QHBoxLayout()
        sa = QPushButton("Select all"); sn = QPushButton("Select none")
        sa.clicked.connect(lambda: self._check_all(True)); sn.clicked.connect(lambda: self._check_all(False))
        self.pl_count = QLabel()
        row.addWidget(sa); row.addWidget(sn); row.addStretch(1); row.addWidget(self.pl_count)
        pll.addLayout(row)
        self.number = QCheckBox("Number the files in playlist order (01 - , 02 - ...)")
        self.number.setChecked(True)
        self.skip = QCheckBox("Skip videos that are already in the folder")
        self.skip.setChecked(True)
        pll.addWidget(self.number); pll.addWidget(self.skip)
        self.pl_list.itemChanged.connect(lambda *_: self._update_count())
        self.pl_box.setVisible(False)
        lay.addWidget(self.pl_box)

        grid = QGridLayout()
        grid.addWidget(QLabel("Quality"), 0, 0)
        self.quality = QComboBox(); self.quality.setEnabled(False)
        grid.addWidget(self.quality, 0, 1, 1, 2)
        self.path_lbl = QLabel("Save As")
        grid.addWidget(self.path_lbl, 1, 0)
        self.path = QLineEdit(); grid.addWidget(self.path, 1, 1)
        b = QPushButton("..."); b.setFixedWidth(34); b.clicked.connect(self._browse)
        grid.addWidget(b, 1, 2)
        grid.addWidget(QLabel("Description"), 2, 0)
        self.desc = QLineEdit(); grid.addWidget(self.desc, 2, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        btns = QHBoxLayout(); btns.addStretch(1)
        self.later = QPushButton("Download Later"); self.start = QPushButton("Start Download")
        cancel = QPushButton("Cancel")
        for w in (self.later, self.start, cancel):
            w.setMinimumWidth(120); btns.addWidget(w)
        self.later.setEnabled(False); self.start.setEnabled(False); self.start.setDefault(True)
        lay.addLayout(btns)
        self.later.clicked.connect(lambda: self._finish("later"))
        self.start.clicked.connect(lambda: self._finish("start"))
        cancel.clicked.connect(self.reject)
        self.quality.currentIndexChanged.connect(self._quality_changed)
        self.rb_list.toggled.connect(lambda *_: self._apply_mode())

        self.threads = []
        if not self.pl_only:
            t = VideoInfoThread(url); t.done.connect(self._loaded); t.failed.connect(self._failed)
            self.threads.append(t)
        if self.pl_url:
            t = PlaylistThread(self.pl_url); t.done.connect(self._pl_loaded); t.failed.connect(self._pl_failed)
            self.threads.append(t)
        for t in self.threads:
            t.start()

    # ---- state
    @property
    def playlist_mode(self):
        return self.pl_only or (self.rb_list.isChecked() and self.playlist is not None)

    def _fill_qualities(self, items, default="1080p"):
        cur = self.quality.currentData()
        self.quality.blockSignals(True)
        self.quality.clear()
        for label, size in items:
            self.quality.addItem(f"{label}    (~{fmt_size(size)})" if size else label, label)
        idx = self.quality.findData(cur) if cur else -1
        if idx < 0:
            idx = self.quality.findData(default)
        self.quality.setCurrentIndex(max(idx, 0))
        self.quality.blockSignals(False)
        self.quality.setEnabled(True)

    def _apply_mode(self):
        pl = self.playlist_mode
        self.pl_box.setVisible(pl)
        self.path_lbl.setText("Save to folder" if pl else "Save As")
        self.setWindowTitle("Download playlist" if pl else "Download video")
        if pl:
            self._fill_qualities([(q, 0) for q in self.video.GENERIC_QUALITIES])
            self.title_lbl.setText(self.playlist["title"])
            n = len(self.playlist["entries"])
            up = self.playlist.get("uploader")
            self.meta_lbl.setText(f"Playlist   •   {n} videos" + (f"   •   {up}" if up else ""))
        elif self.single_info:
            self._fill_qualities(self.video.quality_choices(self.single_info))
            self.title_lbl.setText(self.title)
            self.meta_lbl.setText(self._single_meta)
        self.path.clear()
        self._quality_changed()
        ready = (pl and self.playlist is not None) or (not pl and self.single_info is not None)
        self.later.setEnabled(ready); self.start.setEnabled(ready)
        QTimer.singleShot(0, self.adjustSize)

    # ---- single video
    def _loaded(self, info, thumb):
        from PySide6.QtGui import QPixmap
        self.single_info = info
        self.title = info.get("title") or "video"
        dur = info.get("duration") or 0
        bits = [info.get("uploader") or info.get("channel") or "", fmt_time(dur) if dur else "",
                info.get("extractor_key") or ""]
        self._single_meta = "   •   ".join(x for x in bits if x)
        pm = QPixmap()
        if thumb and pm.loadFromData(thumb):
            self.thumb.setPixmap(pm.scaled(240, 135, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.thumb.setText("No preview")
        if not self.playlist_mode:
            self._apply_mode()

    def _failed(self, msg):
        self.thumb.setText("Error")
        if not self.playlist_mode:
            self.title_lbl.setText("Could not read this video")
            self.meta_lbl.setText(msg)

    # ---- playlist
    def _pl_loaded(self, pl):
        self.playlist = pl
        entries = pl["entries"]
        self.pl_list.blockSignals(True)
        self.pl_list.clear()
        for i, e in enumerate(entries, 1):
            dur = f"   ({fmt_time(e['duration'])})" if e["duration"] else ""
            it = QListWidgetItem(f"{i:02d}   {e['title']}{dur}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if e["ok"] else Qt.Unchecked)
            if not e["ok"]:
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
            it.setData(Qt.UserRole, i - 1)
            self.pl_list.addItem(it)
        self.pl_list.blockSignals(False)
        self._update_count()
        self.rb_list.setText(f"Whole playlist ({len(entries)} videos)")
        self.rb_list.setEnabled(bool(entries))
        if self.pl_only:
            self._apply_mode()

    def _pl_failed(self, msg):
        self.rb_list.setText("Playlist unavailable")
        if self.pl_only:
            self.title_lbl.setText("Could not read this playlist")
            self.meta_lbl.setText(msg)

    def _check_all(self, on):
        for i in range(self.pl_list.count()):
            it = self.pl_list.item(i)
            if it.flags() & Qt.ItemIsEnabled:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def selected_entries(self):
        out = []
        for i in range(self.pl_list.count()):
            it = self.pl_list.item(i)
            if it.checkState() == Qt.Checked:
                idx = it.data(Qt.UserRole)
                out.append((idx + 1, self.playlist["entries"][idx]))
        return out

    def _update_count(self):
        if self.playlist:
            self.pl_count.setText(f"{len(self.selected_entries())} of {len(self.playlist['entries'])} selected")

    # ---- paths
    def _quality_changed(self, *_):
        label = self.quality.currentData() or "Best available"
        self.ext = "mp3" if "MP3" in label else "m4a" if "M4A" in label else "mp4"
        cat = "Music" if label.startswith("Audio") else "Video"
        other = "Video" if cat == "Music" else "Music"
        cur = self.path.text().strip()
        if self.playlist_mode:
            name = engine.sanitize(self.playlist["title"]) if self.playlist else "Playlist"
            if cur:
                parent = os.path.dirname(cur)
                if os.path.normcase(parent) == os.path.normcase(self.mgr.folder_for(other)):
                    parent = self.mgr.folder_for(cat)
                self.path.setText(os.path.join(parent, os.path.basename(cur)))
            else:
                self.path.setText(os.path.join(self.mgr.folder_for(cat), name))
        elif cur:
            folder, name = os.path.split(cur)
            if os.path.normcase(folder) == os.path.normcase(self.mgr.folder_for(other)):
                folder = self.mgr.folder_for(cat)
            self.path.setText(os.path.join(folder, f"{os.path.splitext(name)[0]}.{self.ext}"))
        else:
            self.path.setText(os.path.join(self.mgr.folder_for(cat), f"{engine.sanitize(self.title)}.{self.ext}"))
        self.category = cat

    def _browse(self):
        if self.playlist_mode:
            d = QFileDialog.getExistingDirectory(self, "Save playlist to", os.path.dirname(self.path.text()))
            if d:
                name = engine.sanitize(self.playlist["title"]) if self.playlist else "Playlist"
                self.path.setText(os.path.join(os.path.normpath(d), name))
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save As", self.path.text())
        if p:
            self.path.setText(os.path.normpath(p))

    def _finish(self, choice):
        p = self.path.text().strip()
        if not p:
            return
        self.quality_label = self.quality.currentData()
        if self.playlist_mode:
            sel = self.selected_entries()
            if not sel:
                QMessageBox.information(self, "DownloadDeck", "Tick at least one video.")
                return
            width = max(2, len(str(len(self.playlist["entries"]))))
            self.items = []
            for num, e in sel:
                base = engine.sanitize(e["title"])
                name = f"{num:0{width}d} - {base}" if self.number.isChecked() else base
                self.items.append((e["url"], os.path.join(p, f"{name}.{self.ext}"), e["title"]))
            self.folder = p
        else:
            if os.path.exists(p):
                p = engine.unique_path(p)
            self.final_path = p
        self.choice = choice
        self.accept()
