"""Native messaging host: the browser extension talks to DownloadDeck through this.

Chrome/Edge/Brave start this program and exchange length-prefixed JSON on
stdin/stdout. Each message {"path": "/download", "body": {...}} is forwarded to
the running app on 127.0.0.1; if the app isn't running it is started first.
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
APP = "DownloadDeck"


def port():
    try:
        p = os.path.join(os.environ.get("APPDATA", ""), APP, "settings.json")
        return int(json.load(open(p, encoding="utf-8")).get("browser_port", 9614))
    except (OSError, ValueError):
        return 9614


def call(path, body, timeout):
    req = urllib.request.Request(f"http://127.0.0.1:{port()}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-DownloadDeck": "1"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}") | {"ok": False}
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}"}


def app_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port()}/ping", timeout=1) as r:
            return json.loads(r.read()).get("app") == APP
    except (OSError, ValueError):
        return False


def start_app():
    if getattr(sys, "frozen", False):
        cmd = [os.path.join(HERE, "DownloadDeck.exe")]
    else:
        cmd = [os.path.join(HERE, ".venv", "Scripts", "pythonw.exe"), os.path.join(HERE, "app.py")]
    subprocess.Popen(cmd, cwd=HERE,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    for _ in range(40):
        time.sleep(0.5)
        if app_running():
            return True
    return False


def read_msg():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    n = struct.unpack("<I", raw)[0]
    return json.loads(sys.stdin.buffer.read(n))


def send_msg(obj):
    data = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
    sys.stdout.buffer.flush()


def handle(msg):
    path = msg.get("path", "/download")
    if path not in ("/download", "/formats", "/show", "/ping"):
        return {"ok": False, "error": "unknown request"}
    if path == "/ping":                      # status check: never starts the app
        return {"ok": True, "app": APP, "running": app_running()}
    if not app_running() and not start_app():
        return {"ok": False, "error": "Could not start DownloadDeck"}
    return call(path, msg.get("body") or {}, timeout=90 if path == "/formats" else 15)


def main():
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    while True:
        msg = read_msg()
        if msg is None:
            return
        try:
            send_msg(handle(msg))
        except Exception as e:  # noqa: BLE001
            send_msg({"ok": False, "error": str(e)[:200]})


if __name__ == "__main__":
    main()
