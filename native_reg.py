"""Registers the native messaging host for Chrome, Edge and Brave (current user only).

allowed_origins gets the fixed ID from manifest.json's key *and* whatever ID each
browser actually gave the unpacked extension (a reload can keep an older ID).
"""
import glob
import json
import os
import sys

HOST = "com.downloaddeck.bridge"
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
EXT_DIR = os.path.join(HERE, "chrome-extension")
BROWSER_KEYS = [r"Software\Google\Chrome\NativeMessagingHosts",
                r"Software\Microsoft\Edge\NativeMessagingHosts",
                r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
                r"Software\Chromium\NativeMessagingHosts"]
PROFILE_ROOTS = [r"Google\Chrome\User Data", r"Microsoft\Edge\User Data",
                 r"BraveSoftware\Brave-Browser\User Data", r"Chromium\User Data"]


def installed_ids():
    """IDs of our unpacked extension in every local browser profile."""
    ids = set()
    base = os.environ.get("LOCALAPPDATA", "")
    target = os.path.normcase(os.path.normpath(EXT_DIR))
    for rel in PROFILE_ROOTS:
        for pref in glob.glob(os.path.join(base, rel, "*", "*Preferences")):
            try:
                with open(pref, encoding="utf-8") as f:
                    settings = json.load(f).get("extensions", {}).get("settings", {}) or {}
            except (OSError, ValueError):
                continue
            for ext_id, e in settings.items():
                p = e.get("path") if isinstance(e, dict) else None
                if p and os.path.normcase(os.path.normpath(p)) == target:
                    ids.add(ext_id)
    return ids


def register():
    if sys.platform != "win32":
        return False
    import winreg
    ids = installed_ids()
    try:
        with open(os.path.join(HERE, "extension_id.txt"), encoding="utf-8") as f:
            ids.add(f.read().strip())
    except OSError:
        pass
    # keep the host manifest in AppData so the app folder can be read-only / anywhere
    folder = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "DownloadDeck")
    os.makedirs(folder, exist_ok=True)
    manifest = os.path.join(folder, f"{HOST}.json")
    host = os.path.join(HERE, "native_host.exe" if FROZEN else "native_host.bat")
    data = {"name": HOST, "description": "DownloadDeck browser bridge",
            "path": host, "type": "stdio",
            "allowed_origins": [f"chrome-extension://{i}/" for i in sorted(ids)]}
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    for base in BROWSER_KEYS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + HOST) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, manifest)
    return sorted(ids)


if __name__ == "__main__":
    print(register())
