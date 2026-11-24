"""DownloadDeck download engine.

Multi-connection (segmented) HTTP downloader with dynamic segment splitting,
pause/resume, retry, global speed limiting and a simple queue.
The GUI polls Download objects; nothing here touches Qt.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
import uuid
from collections import deque

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
CHUNK = 64 * 1024
MIN_SPLIT = 256 * 1024          # never split a segment below this
MAX_RETRIES = 6
PART_EXT = ".ddpart"

CATEGORIES = {
    "Compressed": "zip rar 7z gz tar bz2 xz tgz iso cab arj lzh z ace sit sitx".split(),
    "Documents": "pdf doc docx xls xlsx ppt pptx txt rtf odt ods odp epub csv md pps".split(),
    "Music": "mp3 wav flac aac ogg m4a wma opus aif aiff mid ra".split(),
    "Programs": "exe msi msix appx apk dmg pkg deb rpm bat jar".split(),
    "Video": "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts m2ts vob ogv rm rmvb".split(),
}
ALL_EXTS = {e for v in CATEGORIES.values() for e in v}


def category_for(name: str) -> str:
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "General"


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    name = _BAD.sub("_", name).strip().strip(".")
    return name[:200] or "download"


def filename_from_response(resp: requests.Response, url: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    name = None
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
    if m:
        name = urllib.parse.unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8")
    if not name:
        m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
        if m:
            name = m.group(1).strip()
    if not name:
        path = urllib.parse.urlparse(resp.url or url).path
        name = urllib.parse.unquote(os.path.basename(path))
    name = sanitize(name or "download")
    if not os.path.splitext(name)[1]:
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        ext = mimetypes.guess_extension(ctype) if ctype and ctype != "application/octet-stream" else None
        if ext:
            name += ext
    return name


def unique_path(path: str) -> str:
    if not os.path.exists(path) and not os.path.exists(path + PART_EXT):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while True:
        cand = f"{base} ({i}){ext}"
        if not os.path.exists(cand) and not os.path.exists(cand + PART_EXT):
            return cand
        i += 1


def build_headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    if extra:
        h.update({k: v for k, v in extra.items() if v})
    return h


class ProbeResult:
    def __init__(self, url, size, resumable, filename, content_type):
        self.url, self.size, self.resumable = url, size, resumable
        self.filename, self.content_type = filename, content_type


def probe(url: str, headers: dict | None = None, timeout: float = 20) -> ProbeResult:
    h = build_headers(headers)
    h["Range"] = "bytes=0-"
    with requests.get(url, headers=h, stream=True, timeout=timeout, allow_redirects=True) as r:
        r.raise_for_status()
        size, resumable = -1, False
        if r.status_code == 206:
            m = re.search(r"/(\d+)", r.headers.get("content-range", ""))
            if m:
                size, resumable = int(m.group(1)), True
        if size < 0 and r.headers.get("content-length", "").isdigit():
            size = int(r.headers["content-length"])
        if r.status_code == 200 and r.headers.get("accept-ranges", "").lower() == "bytes" and size > 0:
            resumable = True
        return ProbeResult(r.url, size, resumable, filename_from_response(r, url),
                           r.headers.get("content-type", ""))


class RateLimiter:
    """Global token bucket shared by every connection. 0 = unlimited."""

    def __init__(self):
        self.rate = 0
        self._lock = threading.Lock()
        self._allow = 0.0
        self._last = time.monotonic()

    def consume(self, n: int):
        while self.rate > 0:
            with self._lock:
                now = time.monotonic()
                self._allow = min(self.rate, self._allow + (now - self._last) * self.rate)
                self._last = now
                if self._allow >= n or self._allow >= self.rate:
                    self._allow -= n
                    return
                wait = (n - self._allow) / self.rate
            time.sleep(min(wait, 0.25))


class Segment:
    __slots__ = ("start", "end", "pos", "owner")

    def __init__(self, start, end, pos=0):
        self.start, self.end, self.pos, self.owner = start, end, pos, None

    @property
    def cur(self):
        return self.start + self.pos

    @property
    def remaining(self):
        return -1 if self.end < 0 else self.end - self.cur + 1


class ConnInfo:
    __slots__ = ("seg", "status", "downloaded")

    def __init__(self, seg):
        self.seg, self.status, self.downloaded = seg, "Connecting...", 0


class Download:
    def __init__(self, url, save_dir, filename, **kw):
        self.id = kw.get("id") or uuid.uuid4().hex[:12]
        self.url = url
        self.save_dir = save_dir
        self.filename = filename
        self.size = kw.get("size", -1)
        self.resumable = kw.get("resumable", False)
        self.status = kw.get("status", "Queued")
        self.category = kw.get("category") or category_for(filename)
        self.description = kw.get("description", "")
        self.headers = kw.get("headers") or {}
        self.added = kw.get("added") or time.time()
        self.last_try = kw.get("last_try", 0)
        self.finished_at = kw.get("finished_at", 0)
        self.in_queue = kw.get("in_queue", False)
        self.error = kw.get("error", "")
        self.connections = kw.get("connections", 8)
        self.segments = [Segment(*s) for s in kw.get("segments", [])]
        self.lock = threading.RLock()
        self.speed = 0.0
        self.conns: list[ConnInfo] = []
        self.task: DownloadTask | None = None
        self._done_bytes = kw.get("done_bytes", 0)
        self.limiter = RateLimiter()          # per-download limit (0 = off)
        self.open_when_done = False
        self.auto_name = kw.get("auto_name", False)   # take name/folder from server on first start
        self.shutdown_when_done = False
        self.kind = kw.get("kind", "file")            # "file" or "video" (yt-dlp)
        self.video_format = kw.get("video_format", "")  # quality label for videos
        self.vfiles: dict = {}                        # video: stream file -> [done, total]

    # ---- derived ----
    @property
    def path(self):
        return os.path.join(self.save_dir, self.filename)

    @property
    def part_path(self):
        return self.path + PART_EXT

    @property
    def downloaded(self):
        if self.status == "Complete":
            return self.size if self.size > 0 else self._done_bytes
        with self.lock:
            if self.kind == "video":
                return sum(v[0] for v in self.vfiles.values())
            return sum(s.pos for s in self.segments)

    @property
    def progress(self):
        if self.status == "Complete":
            return 100.0
        return 100.0 * self.downloaded / self.size if self.size > 0 else 0.0

    @property
    def time_left(self):
        if self.speed <= 1 or self.size <= 0:
            return -1
        return (self.size - self.downloaded) / self.speed

    @property
    def active(self):
        return self.task is not None and self.task.is_alive()

    def to_dict(self):
        with self.lock:
            segs = [[s.start, s.end, s.pos] for s in self.segments]
        status = self.status
        if status in ("Connecting", "Downloading", "Merging", "Converting"):
            status = "Paused"
        return dict(id=self.id, url=self.url, save_dir=self.save_dir, filename=self.filename,
                    size=self.size, resumable=self.resumable, status=status,
                    category=self.category, description=self.description, headers=self.headers,
                    added=self.added, last_try=self.last_try, finished_at=self.finished_at,
                    in_queue=self.in_queue, error=self.error, connections=self.connections,
                    segments=segs, auto_name=self.auto_name, kind=self.kind,
                    video_format=self.video_format, done_bytes=self.downloaded if self.status == "Complete" else 0)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        return cls(d.pop("url"), d.pop("save_dir"), d.pop("filename"), **d)


class DownloadTask(threading.Thread):
    def __init__(self, dl: Download, manager: "Manager"):
        super().__init__(daemon=True, name=f"dl-{dl.id}")
        self.dl, self.mgr = dl, manager
        self.stop_evt = threading.Event()
        self.workers: list[threading.Thread] = []
        self.fatal: str | None = None

    def stop(self):
        self.stop_evt.set()

    # ----- main -----
    def run(self):
        dl = self.dl
        dl.status, dl.error, dl.last_try = "Connecting", "", time.time()
        try:
            self._prepare()
        except Exception as e:  # noqa: BLE001
            dl.status, dl.error = "Error", _err(e)
            self.mgr._event("error", dl)
            return
        if self.stop_evt.is_set():
            dl.status = "Paused"
            return
        dl.status = "Downloading"
        with dl.lock:
            dl.conns = []
            pending = [s for s in dl.segments if s.remaining != 0]
            n = max(1, dl.connections if dl.resumable else 1)
            # with fewer segments than connections, split as workers start
            for i in range(n):
                seg = pending.pop(0) if pending else (self._steal() if dl.resumable and i > 0 else None)
                if seg is None:
                    break
                seg.owner = i
                ci = ConnInfo(seg)
                dl.conns.append(ci)
                t = threading.Thread(target=self._worker, args=(ci,), daemon=True)
                self.workers.append(t)
        for t in self.workers:
            t.start()
            time.sleep(0.05)

        samples = deque()
        last_save = time.monotonic()
        while any(t.is_alive() for t in self.workers):
            time.sleep(0.4)
            now = time.monotonic()
            samples.append((now, dl.downloaded))
            while len(samples) > 2 and now - samples[0][0] > 4:
                samples.popleft()
            if len(samples) >= 2:
                dt = samples[-1][0] - samples[0][0]
                dl.speed = (samples[-1][1] - samples[0][1]) / dt if dt > 0 else 0
            if now - last_save > 3:
                self.mgr.save()
                last_save = now
        dl.speed = 0

        if self.stop_evt.is_set():
            dl.status = "Paused"
            self.mgr.save()
            return
        complete = all(s.remaining == 0 for s in dl.segments) if dl.size > 0 else self.fatal is None
        if not complete:
            dl.status, dl.error = "Error", self.fatal or "Connection lost"
            self.mgr.save()
            self.mgr._event("error", dl)
            return
        try:
            final = dl.path if not os.path.exists(dl.path) else unique_path(dl.path)
            os.replace(dl.part_path, final)
            dl.filename = os.path.basename(final)
            if dl.size <= 0:
                dl._done_bytes = os.path.getsize(final)
                dl.size = dl._done_bytes
        except OSError as e:
            dl.status, dl.error = "Error", _err(e)
            self.mgr._event("error", dl)
            return
        dl.status, dl.finished_at = "Complete", time.time()
        dl.in_queue = False
        self.mgr.save()
        self.mgr._event("complete", dl)

    def _prepare(self):
        dl = self.dl
        info = probe(dl.url, dl.headers)
        size_changed = dl.size > 0 and info.size > 0 and info.size != dl.size
        dl.resumable = info.resumable and info.size > 0
        os.makedirs(dl.save_dir, exist_ok=True)
        fresh = size_changed or not dl.resumable or not dl.segments or not os.path.exists(dl.part_path)
        if fresh and dl.auto_name and info.filename:
            dl.category = category_for(info.filename)
            dl.save_dir = self.mgr.folder_for(dl.category)
            os.makedirs(dl.save_dir, exist_ok=True)
            dl.filename = os.path.basename(unique_path(os.path.join(dl.save_dir, info.filename)))
        dl.auto_name = False
        if fresh:
            dl.size = info.size
            with dl.lock:
                if dl.resumable:
                    dl.segments = [Segment(0, dl.size - 1)]
                else:
                    dl.segments = [Segment(0, dl.size - 1 if dl.size > 0 else -1)]
            with open(dl.part_path, "wb") as f:
                if dl.resumable:
                    f.truncate(dl.size)
        else:
            dl.size = info.size

    def _steal(self):
        """Split the segment with the most remaining bytes; return the new tail."""
        dl = self.dl
        with dl.lock:
            idle = [s for s in dl.segments if s.owner is None and s.remaining > 0]
            if idle:
                return idle[0]
            best = max(dl.segments, key=lambda s: s.remaining, default=None)
            if best is None or best.remaining < 2 * MIN_SPLIT:
                return None
            mid = best.cur + best.remaining // 2
            new = Segment(mid, best.end)
            best.end = mid - 1
            dl.segments.append(new)
            dl.segments.sort(key=lambda s: s.start)
            return new

    def _peers_alive(self):
        me = threading.current_thread()
        return any(t.is_alive() for t in self.workers if t is not me)

    def _worker(self, ci: ConnInfo):
        dl = self.dl
        idx = dl.conns.index(ci)
        seg = ci.seg
        retries = 0
        session = requests.Session()
        try:
            while not self.stop_evt.is_set():
                if seg.remaining == 0:
                    with dl.lock:
                        seg.owner = None
                        seg = self._steal()
                        if seg is None:
                            ci.status = "Complete"
                            return
                        seg.owner = idx
                        ci.seg = seg
                    continue
                ci.status = "Connecting..."
                h = build_headers(dl.headers)
                if dl.resumable:
                    h["Range"] = f"bytes={seg.cur}-{seg.end}"
                try:
                    with session.get(dl.url, headers=h, stream=True, timeout=(15, 30)) as r:
                        if r.status_code in (429, 503) and self._peers_alive():
                            # server caps connections: drop this one, peers take its part
                            ci.status = "Closed (server limits connections)"
                            return
                        r.raise_for_status()
                        if dl.resumable and r.status_code != 206:
                            raise IOError(f"Server refused range request (HTTP {r.status_code})")
                        mode = "r+b" if dl.resumable else "wb"
                        if not dl.resumable:
                            seg.pos = 0
                        with open(dl.part_path, mode) as f:
                            f.seek(seg.cur)
                            ci.status = "Receiving data..."
                            for chunk in r.iter_content(CHUNK):
                                if self.stop_evt.is_set():
                                    break
                                if seg.end >= 0:
                                    rem = seg.remaining
                                    if rem <= 0:
                                        break
                                    if len(chunk) > rem:
                                        chunk = chunk[:rem]
                                dl.limiter.consume(len(chunk))
                                self.mgr.limiter.consume(len(chunk))
                                f.write(chunk)
                                with dl.lock:
                                    seg.pos += len(chunk)
                                ci.downloaded += len(chunk)
                                retries = 0
                    if not dl.resumable and not self.stop_evt.is_set():
                        if seg.end < 0 or seg.remaining == 0:
                            seg.end = seg.start + seg.pos - 1
                            ci.status = "Complete"
                            return
                        raise IOError("Connection closed early")
                except Exception as e:  # noqa: BLE001
                    retries += 1
                    ci.status = f"Retry {retries}: {_err(e)}"
                    if retries > MAX_RETRIES or not dl.resumable and retries > 2:
                        ci.status = f"Failed: {_err(e)}"
                        self.fatal = _err(e)
                        return
                    self.stop_evt.wait(min(2 ** retries, 20))
        finally:
            with dl.lock:
                if seg is not None and seg.owner == idx:
                    seg.owner = None
            session.close()
            # a peer that died leaves its segment idle; another worker steals it
            if ci.status.startswith("Failed") and any(
                    t.is_alive() for t in self.workers if t is not threading.current_thread()):
                self.fatal = None


def _err(e: Exception) -> str:
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code} {e.response.reason}"
    if isinstance(e, requests.ConnectionError):
        return "Cannot connect to server"
    if isinstance(e, requests.Timeout):
        return "Timed out"
    return str(e)[:160] or e.__class__.__name__


def default_settings():
    dl_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    return {
        "download_dir": dl_dir,
        "category_subfolders": True,
        "connections": 8,
        "max_simultaneous": 3,
        "speed_limit_kb": 0,
        "clipboard_monitor": True,
        "show_complete_dialog": True,
        "show_start_dialog": True,
        "minimize_to_tray": True,
        "browser_port": 9614,
        "schedule": {"enabled": False, "start": "02:00", "stop_enabled": False, "stop": "07:00",
                     "days": [0, 1, 2, 3, 4, 5, 6], "when_done": "nothing"},
    }


class Manager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "downloads.json")
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.downloads: list[Download] = []
        self.settings = default_settings()
        self.limiter = RateLimiter()
        self.queue_running = False
        self.events = deque()
        self._save_lock = threading.Lock()
        self.load()

    # ---- persistence ----
    def load(self):
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                s = json.load(f)
            sched = {**self.settings["schedule"], **s.pop("schedule", {})}
            self.settings.update(s)
            self.settings["schedule"] = sched
        except (OSError, ValueError):
            pass
        try:
            with open(self.db_path, encoding="utf-8") as f:
                self.downloads = [Download.from_dict(d) for d in json.load(f)]
        except (OSError, ValueError):
            self.downloads = []
        self.apply_settings()

    def save(self):
        with self._save_lock:
            data = [d.to_dict() for d in list(self.downloads)]
            tmp = self.db_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.db_path)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=1)

    def apply_settings(self):
        self.limiter.rate = int(self.settings.get("speed_limit_kb", 0)) * 1024

    def folder_for(self, category):
        custom = self.settings.get("category_dirs", {}).get(category)
        if custom:
            return custom
        base = self.settings["download_dir"]
        if self.settings.get("category_subfolders") and category != "General":
            return os.path.join(base, category)
        return base

    # ---- operations ----
    def add(self, url, save_path, start=True, description="", headers=None, size=-1,
            resumable=False, in_queue=False, category=None, auto_name=False, kind="file",
            video_format=""):
        save_dir, filename = os.path.split(save_path)
        dl = Download(url, save_dir, filename, description=description, headers=headers or {},
                      size=size, resumable=resumable, in_queue=in_queue, category=category,
                      auto_name=auto_name, kind=kind, video_format=video_format,
                      connections=int(self.settings["connections"]))
        self.downloads.append(dl)
        if start:
            self.start(dl)
        self.save()
        return dl

    def start(self, dl: Download):
        if dl.active or dl.status == "Complete":
            return
        dl.connections = int(self.settings["connections"])
        if dl.kind == "video":
            from video import VideoTask
            dl.task = VideoTask(dl, self)
        else:
            dl.task = DownloadTask(dl, self)
        dl.status = "Connecting"
        dl.task.start()

    def stop(self, dl: Download):
        if dl.task and dl.task.is_alive():
            dl.task.stop()

    def remove(self, dl: Download, delete_file=False):
        self.stop(dl)
        if dl.task:
            dl.task.join(timeout=3)
        if dl in self.downloads:
            self.downloads.remove(dl)
        try:
            if os.path.exists(dl.part_path):
                os.remove(dl.part_path)
            if dl.kind == "video":
                from video import remove_partials
                remove_partials(dl)
            if delete_file and dl.status == "Complete" and os.path.exists(dl.path):
                os.remove(dl.path)
        except OSError:
            pass
        self.save()

    def stop_all(self):
        self.queue_running = False
        for d in self.downloads:
            self.stop(d)

    def active_count(self):
        return sum(1 for d in self.downloads if d.active)

    def start_queue(self):
        self.queue_running = True
        self.tick()

    def stop_queue(self):
        self.queue_running = False
        for d in self.downloads:
            if d.in_queue:
                self.stop(d)

    def tick(self):
        """Called about once a second by the GUI."""
        if not self.queue_running:
            return
        active_q = [d for d in self.downloads if d.in_queue and d.active]
        waiting = [d for d in self.downloads if d.in_queue and not d.active
                   and d.status in ("Queued", "Paused", "Error")]
        free = int(self.settings["max_simultaneous"]) - len(active_q)
        for d in waiting[:max(0, free)]:
            self.start(d)
        if not active_q and not waiting:
            self.queue_running = False
            self._event("queue_done", None)

    def _event(self, kind, dl):
        self.events.append((kind, dl))

    def shutdown(self):
        for d in self.downloads:
            self.stop(d)
        for d in self.downloads:
            if d.task:
                d.task.join(timeout=2)
        self.save()
