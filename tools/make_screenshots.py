r"""Renders README screenshots with demo data (no personal paths or real download history).

Run:  .venv\\Scripts\\python.exe tools\\make_screenshots.py
Writes docs/screenshots/*.png
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "docs", "screenshots")
os.makedirs(OUT, exist_ok=True)
TMP = tempfile.mkdtemp(prefix="dd-shots-")
os.environ["APPDATA"] = TMP          # throwaway profile

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qa = QApplication(sys.argv)
qa.styleHints().setColorScheme(Qt.ColorScheme.Light)
qa.setStyle("windowsvista")
qa.setStyleSheet("QProgressBar { border: 1px solid #8a99a8; background: #fff; }"
                 "QProgressBar::chunk { background: #3d7fd6; }")

import app as A  # noqa: E402
import engine  # noqa: E402
from dialogs import OptionsDialog, ProgressDialog, SchedulerDialog, VideoDialog  # noqa: E402

mgr = engine.Manager(os.path.join(TMP, "DownloadDeck"))
mgr.settings.update(download_dir=r"D:\Downloads", clipboard_monitor=False)
now = time.time()


def fake(name, size, status, pct=0.0, speed=0.0, queue=False, desc="", ago=0, kind="file", vf=""):
    d = engine.Download("https://example.com/" + name, os.path.join(r"D:\Downloads", name), name,
                        size=size, resumable=True, status=status, in_queue=queue, description=desc,
                        kind=kind, video_format=vf)
    d.last_try = d.added = now - ago
    if status == "Complete":
        d._done_bytes = size
    else:
        done = int(size * pct / 100)
        n = 8
        part = size // n
        d.segments = []
        for i in range(n):                       # spread progress over 8 connections
            s, e = i * part, (size - 1 if i == n - 1 else (i + 1) * part - 1)
            seg = engine.Segment(s, e, min(e - s + 1, int(done / n)))
            seg.owner = i if status == "Downloading" else None
            d.segments.append(seg)
        if status == "Downloading":
            d.conns = []
            for i, seg in enumerate(d.segments):
                c = engine.ConnInfo(seg)
                c.downloaded, c.status = seg.pos, "Receiving data..."
                d.conns.append(c)
    if kind == "video" and status != "Complete":
        d.vfiles = {"stream": [int(size * pct / 100), size]}
    d.speed = speed
    mgr.downloads.append(d)
    return d


fake("ubuntu-24.04.3-desktop-amd64.iso", 6_340_000_000, "Downloading", 43.27, 11.8e6, ago=60)
fake("Big Buck Bunny 4K - Blender Open Movie.mp4", 642_000_000, "Downloading", 71.9, 6.2e6, ago=120,
     kind="video", vf="2160p")
fake("Blender Open Movies/01 - Sintel.mp4", 412_000_000, "Queued", queue=True, desc="Playlist: Blender Open Movies",
     ago=200, kind="video", vf="1080p")
fake("Blender Open Movies/02 - Tears of Steel.mp4", 538_000_000, "Queued", queue=True,
     desc="Playlist: Blender Open Movies", ago=200, kind="video", vf="1080p")
fake("project-assets.zip", 1_250_000_000, "Paused", 18.4, ago=3600)
fake("lecture-notes.pdf", 8_400_000, "Complete", ago=7200)
fake("podcast-episode-42.mp3", 61_000_000, "Complete", ago=86400, kind="video", vf="Audio only (MP3)")
fake("VSCodeUserSetup-x64.exe", 104_000_000, "Complete", ago=90000)
fake("holiday-photos.7z", 2_100_000_000, "Error", 64.0, ago=100000).error = "Connection lost"

# downloads are fake: make "active" report True for the two running ones
engine.Download.active = property(lambda self: self.status == "Downloading")

win = A.MainWindow(mgr, A.Bridge())
win.timer.stop()
win.resize(1180, 520)
win.model.rebuild()
win.tray.hide()


def shoot(widget, name):
    widget.grab().save(os.path.join(OUT, name))
    print("saved", name)


def step1():
    win._on_timer()
    win.status_lbl.setText("2 active   17.42 MB/sec")
    shoot(win, "main-window.png")
    d = mgr.downloads[0]
    pw = ProgressDialog(mgr, d)
    pw.timer.stop()
    pw.show(); pw.refresh(); pw.adjustSize()
    QTimer.singleShot(300, lambda: (shoot(pw, "progress-connections.png"), pw.close(), step2()))


def step2():
    sd = SchedulerDialog(mgr, win)
    sd.show()
    QTimer.singleShot(300, lambda: (shoot(sd, "scheduler.png"), sd.close(), step3()))


def step3():
    od = OptionsDialog(mgr, r"D:\Apps\DownloadDeck\chrome-extension", win)
    od.show()
    tabs = od.findChild(__import__("PySide6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget)
    tabs.setCurrentIndex(1)
    QTimer.singleShot(300, lambda: (shoot(od, "options.png"), od.close(), step4()))


def step4():
    # real lookup of a Creative Commons film (Big Buck Bunny, Blender Foundation)
    vd = VideoDialog(mgr, "https://www.youtube.com/watch?v=aqz-KE-bpKQ", win)
    vd.show()

    def wait():
        if vd.single_info is None:
            QTimer.singleShot(300, wait)
            return
        vd.path.setText(r"D:\Downloads\Video\Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film.mp4")
        QTimer.singleShot(300, lambda: (shoot(vd, "video-quality-dialog.png"), vd.close(), step5()))
    wait()


def step5():
    vd = VideoDialog(mgr, "https://www.youtube.com/playlist?list=PLav47HAVZMjnTdm25KnxGkL8e1sPRt8A2", win)
    vd.show()

    def wait():
        if vd.playlist is None:
            QTimer.singleShot(300, wait)
            return
        vd.path.setText(r"D:\Downloads\Video\Project Gold")
        vd.quality.setCurrentIndex(vd.quality.findData("1080p"))
        QTimer.singleShot(400, lambda: (shoot(vd, "playlist-dialog.png"), vd.close(), qa.quit()))
    wait()


QTimer.singleShot(500, step1)
qa.exec()
