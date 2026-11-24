"""Video-site downloads (YouTube and others) via yt-dlp + bundled ffmpeg/deno."""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
import urllib.parse

import engine

VIDEO_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com", "dailymotion.com",
               "facebook.com", "fb.watch", "instagram.com", "twitter.com", "x.com", "tiktok.com",
               "reddit.com", "twitch.tv", "soundcloud.com", "bilibili.com", "streamable.com")


def is_video_url(url: str) -> bool:
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
        return False
    if "youtube" in host:
        return (bool(re.search(r"/(watch|shorts/|live/|embed/|v/|playlist)", u.path + "/"))
                or "v=" in u.query or "list=" in u.query)
    return True


def _tool(name):
    here = os.path.dirname(sys.executable)
    for cand in (os.path.join(here, name + ".exe"), os.path.join(here, "Scripts", name + ".exe")):
        if os.path.exists(cand):
            return cand
    return shutil.which(name)


class _QuietLog:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def base_opts() -> dict:
    ffmpeg = _tool("ffmpeg")
    if not ffmpeg:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    o = {"logger": _QuietLog(), "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
         "ffmpeg_location": ffmpeg}
    deno = _tool("deno")
    if deno:
        o["js_runtimes"] = {"deno": {"path": deno}}
    return o


INFO_CACHE: dict = {}      # url -> (time, raw info, display info); stream links stay valid for hours
CACHE_SECS = 1800


def cached_info(url: str):
    """Display-safe (sanitized) info, for titles and quality lists."""
    hit = INFO_CACHE.get(url)
    return hit[2] if hit and time.time() - hit[0] < CACHE_SECS else None


def cached_raw(url: str):
    """Original yt-dlp info, needed to download without a second lookup
    (sanitizing drops internals some YouTube streams rely on -> HTTP 403)."""
    hit = INFO_CACHE.get(url)
    return hit[1] if hit and time.time() - hit[0] < CACHE_SECS else None


def fetch_info(url: str, use_cache: bool = True) -> dict:
    import yt_dlp
    if use_cache and (info := cached_info(url)):
        return info
    with yt_dlp.YoutubeDL(base_opts()) as y:
        raw = y.extract_info(url, download=False)
        info = y.sanitize_info(raw)
    INFO_CACHE[url] = (time.time(), raw, info)
    return info


def playlist_url(url: str) -> str | None:
    """Playlist address if the link belongs to one (YouTube list=, or other sites' playlists)."""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    lid = urllib.parse.parse_qs(u.query).get("list", [None])[0]
    if "youtube" in host or host == "youtu.be":
        # RD... lists are endless auto-generated "mixes"; treat as a single video
        if lid and not lid.startswith("RD"):
            return f"https://www.youtube.com/playlist?list={lid}"
        return None
    if re.search(r"/(playlist|playlists|sets|album|showcase|channels)/", u.path + "/"):
        return url
    return None


def is_playlist_only(url: str) -> bool:
    """True when the link is a playlist page rather than one video inside it."""
    u = urllib.parse.urlparse(url)
    if "youtube" in (u.hostname or ""):
        return u.path.rstrip("/").endswith("/playlist")
    return playlist_url(url) == url


def fetch_playlist(url: str) -> dict:
    """Fast listing (no per-video lookups): {'title', 'entries': [{url,title,duration,ok}]}."""
    import yt_dlp
    opts = base_opts()
    opts.update(extract_flat="in_playlist", noplaylist=False)
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.sanitize_info(y.extract_info(url, download=False))
    entries = []
    for e in info.get("entries") or []:
        if not e:
            continue
        eu = e.get("url") or e.get("webpage_url")
        if eu and not eu.startswith("http") and e.get("id"):
            eu = f"https://www.youtube.com/watch?v={e['id']}"
        title = e.get("title") or e.get("id") or "video"
        ok = bool(eu) and title not in ("[Private video]", "[Deleted video]")
        entries.append({"url": eu, "title": title, "duration": e.get("duration") or 0, "ok": ok})
    return {"title": info.get("title") or "Playlist", "uploader": info.get("uploader") or info.get("channel") or "",
            "entries": entries}


GENERIC_QUALITIES = ["Best available", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p",
                     "Audio only (MP3)", "Audio only (M4A)"]


def _fsize(f, duration):
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and duration:
        s = f["tbr"] * 125 * duration
    return int(s or 0)


def quality_choices(info: dict) -> list[tuple[str, int]]:
    """[(label, estimated bytes)] best first, then audio-only options."""
    fmts = info.get("formats") or []
    dur = info.get("duration") or 0
    vids = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height")]
    auds = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best_a = max(auds, key=lambda f: (f.get("ext") == "m4a", f.get("abr") or 0), default=None)
    a_size = _fsize(best_a, dur) if best_a else 0
    out = []
    heights = sorted({f["height"] for f in vids}, reverse=True)
    for h in heights:
        cands = [f for f in vids if f["height"] == h]
        f = max(cands, key=lambda f: (str(f.get("vcodec", "")).startswith("avc"), f.get("tbr") or 0))
        size = _fsize(f, dur) + (a_size if f.get("acodec") in (None, "none") else 0)
        out.append((f"{h}p", size))
    if out:
        out.insert(0, ("Best available", out[0][1]))
    else:
        out.append(("Best available", _fsize(info, dur)))
    if best_a or not vids:
        out.append(("Audio only (MP3)", a_size))
        out.append(("Audio only (M4A)", a_size))
    return out


def format_opts(label: str):
    """yt-dlp options for a quality label, plus the final file extension."""
    if label.startswith("Audio only"):
        codec = "mp3" if "MP3" in label else "m4a"
        return {"format": "ba[ext=m4a]/ba/b",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": codec,
                                    "preferredquality": "192"}]}, codec
    if label == "Best available" or not label[:-1].isdigit():
        fmt = "bv*+ba/b"
    else:
        h = int(label[:-1])
        fmt = f"bv*[height<={h}]+ba/b[height<={h}]/b"
    # prefer H.264/AAC where the resolution allows it, so the MP4 plays everywhere
    return {"format": fmt, "merge_output_format": "mp4",
            "format_sort": ["res", "fps", "vcodec:h264", "acodec:aac"]}, "mp4"


_PARTIAL = re.compile(r"^(\.f[\w-]+)?\.\w+(\.part(-Frag\d+)?|\.ytdl)$|^\.f[\w-]+\.\w+$|^\.temp\.\w+$")


def remove_partials(dl):
    stem = os.path.splitext(os.path.basename(dl.path))[0]
    try:
        names = os.listdir(dl.save_dir)
    except OSError:
        return
    for n in names:
        if n.startswith(stem + ".") and _PARTIAL.match(n[len(stem):]):
            try:
                os.remove(os.path.join(dl.save_dir, n))
            except OSError:
                pass


class VideoTask(threading.Thread):
    def __init__(self, dl, manager):
        super().__init__(daemon=True, name=f"vid-{dl.id}")
        self.dl, self.mgr = dl, manager
        self.stop_evt = threading.Event()
        self.planned = 0

    def stop(self):
        self.stop_evt.set()

    def _hook(self, d):
        from yt_dlp.utils import DownloadCancelled
        if self.stop_evt.is_set():
            raise DownloadCancelled("Paused")
        dl = self.dl
        fn = d.get("filename") or "?"
        info = d.get("info_dict") or {}
        label = info.get("format_note") or info.get("format_id") or "stream"
        kind = "audio" if info.get("vcodec") == "none" else "video"
        with dl.lock:
            if d["status"] == "downloading":
                done = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                dl.vfiles[fn] = [done, total]
                dl.speed = d.get("speed") or 0
                dl.status = "Downloading"
                st = f"Receiving data... ({label} {kind})"
            elif d["status"] == "finished":
                v = dl.vfiles.get(fn, [0, 0])
                tot = d.get("total_bytes") or v[1] or v[0]
                dl.vfiles[fn] = [tot, tot]
                st = f"Complete ({label} {kind})"
            else:
                return
            dl.size = max(self.planned, sum(v[1] for v in dl.vfiles.values()))
            names = list(dl.vfiles)
            while len(dl.conns) < len(names):
                dl.conns.append(engine.ConnInfo(None))
            ci = dl.conns[names.index(fn)]
            ci.status, ci.downloaded = st, dl.vfiles[fn][0]

    def _pp_hook(self, d):
        if d.get("status") == "started":
            self.dl.status = "Merging" if d.get("postprocessor") == "Merger" else "Converting"
            self.dl.speed = 0

    def run(self):
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled, DownloadError
        dl = self.dl
        dl.status, dl.error, dl.last_try = "Connecting", "", time.time()
        dl.resumable = True
        dl.conns = []
        try:
            fopts, ext = format_opts(dl.video_format or "Best available")
            stem = os.path.splitext(dl.path)[0].replace("%", "%%")
            os.makedirs(dl.save_dir, exist_ok=True)
            opts = base_opts()
            opts.update(fopts)
            rates = [r for r in (dl.limiter.rate, self.mgr.limiter.rate) if r > 0]
            opts.update(outtmpl={"default": stem + ".%(ext)s"}, progress_hooks=[self._hook],
                        postprocessor_hooks=[self._pp_hook], continuedl=True, retries=10,
                        fragment_retries=10, concurrent_fragment_downloads=max(1, dl.connections),
                        http_chunk_size=10 * 1024 * 1024, ratelimit=min(rates) if rates else None)
            with yt_dlp.YoutubeDL(opts) as y:
                # always a fresh lookup: YouTube ties stream links to the session that fetched
                # them, so reusing the quality menu's lookup gives HTTP 403
                info = y.extract_info(dl.url, download=False)
                if self.stop_evt.is_set():
                    dl.status = "Paused"
                    return
                parts = info.get("requested_formats") or [info]
                self.planned = sum(_fsize(f, info.get("duration")) for f in parts)
                if self.planned:
                    dl.size = self.planned
                info = y.process_ie_result(info, download=True)
            final = None
            for rd in info.get("requested_downloads") or []:
                final = rd.get("filepath") or rd.get("_filename")
            if not final or not os.path.exists(final):
                final = os.path.splitext(dl.path)[0] + "." + ext
            dl.save_dir, dl.filename = os.path.split(final)
            dl.size = dl._done_bytes = os.path.getsize(final)
            dl.status, dl.finished_at, dl.in_queue = "Complete", time.time(), False
            for c in dl.conns:
                c.status = "Complete"
            self.mgr._event("complete", dl)
        except DownloadCancelled:
            dl.status = "Paused"
        except (DownloadError, OSError, Exception) as e:  # noqa: BLE001
            if self.stop_evt.is_set():
                dl.status = "Paused"
            else:
                msg = re.sub(r"^ERROR:\s*", "", str(e)).replace("\x1b[0;31m", "").replace("\x1b[0m", "")
                dl.status, dl.error = "Error", msg.strip()[:240]
                self.mgr._event("error", dl)
        finally:
            dl.speed = 0
            self.mgr.save()
