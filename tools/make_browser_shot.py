r"""Renders the in-page quality menu (video_button.js) in headless Chrome with demo data.

Run:  .venv\Scripts\python.exe tools\make_browser_shot.py   -> docs/screenshots/in-page-menu.png
"""
import base64
import json
import os
import subprocess
import tempfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots", "in-page-menu.png")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Big Buck Bunny (c) Blender Foundation, CC BY 3.0 - poster frame for the demo video
poster = base64.b64encode(requests.get("https://i.ytimg.com/vi/aqz-KE-bpKQ/maxresdefault.jpg", timeout=20).content).decode()
script = open(os.path.join(ROOT, "chrome-extension", "video_button.js"), encoding="utf-8").read()
script = script.replace('attachShadow({ mode: "closed" })', 'attachShadow({ mode: "open" })')  # demo only

GB = 1 << 30
MB = 1 << 20
fake = {"ok": True, "title": "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film",
        "playlist": False, "qualities": [
            {"label": "Best available", "size": int(2.1 * GB)}, {"label": "2160p", "size": int(2.1 * GB)},
            {"label": "1440p", "size": int(1.07 * GB)}, {"label": "1080p", "size": 590 * MB},
            {"label": "720p", "size": 374 * MB}, {"label": "480p", "size": 127 * MB},
            {"label": "360p", "size": 90 * MB}, {"label": "Audio only (MP3)", "size": 29 * MB},
            {"label": "Audio only (M4A)", "size": 29 * MB}]}

html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#0f0f0f;font:14px Roboto,Arial,sans-serif;color:#f1f1f1}}
.top{{height:56px;display:flex;align-items:center;padding:0 24px;font-weight:700;font-size:18px;color:#fff}}
.top b{{background:#e03;color:#fff;border-radius:6px;padding:2px 8px;margin-right:8px}}
.wrap{{padding:0 24px}}
video{{width:1040px;height:585px;background:#000;border-radius:12px;display:block;object-fit:cover}}
h1{{font-size:20px;margin:14px 0 4px}} .sub{{color:#aaa}}
</style></head><body>
<div class="top"><b>&#9654;</b> VideoSite</div>
<div class="wrap"><video id="v" poster="data:image/jpeg;base64,{poster}"></video>
<h1>Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film</h1>
<div class="sub">Blender Foundation &middot; Creative Commons Attribution</div></div>
<script>
window.chrome = {{ runtime: {{ sendMessage: async (m) => m.type === "formats" ? {json.dumps(fake)} : {{ok:true}} }} }};
</script>
<script>{script}</script>
<script>
// demo page is not YouTube: give the button a URL to work with
setTimeout(() => {{
  const v = document.getElementById("v"), r = v.getBoundingClientRect();
  document.dispatchEvent(new MouseEvent("mousemove", {{clientX: r.left + 300, clientY: r.top + 200, bubbles: true}}));
  setTimeout(() => {{
    const host = [...document.documentElement.children].find(e => e.shadowRoot);
    host.shadowRoot.querySelector(".btn").click();
  }}, 300);
}}, 300);
</script></body></html>"""

tmp = os.path.join(tempfile.mkdtemp(prefix="dd-browser-"), "demo.html")
open(tmp, "w", encoding="utf-8").write(html)
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--screenshot={OUT}",
                "--window-size=1088,720", "--virtual-time-budget=4000", "--user-data-dir=" + os.path.dirname(tmp),
                "file:///" + tmp.replace("\\", "/")], check=True, timeout=60,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("saved", OUT)
