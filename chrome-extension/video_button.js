// IDM-style "Download this video" button on every video, with a quality menu.
(() => {
  if (window.__downloadDeckButton) return;
  window.__downloadDeckButton = true;

  const isYT = /(^|\.)youtube(-nocookie)?\.com$/.test(location.hostname);

  function pageVideoUrl() {
    const u = new URL(location.href);
    if (isYT) {
      if (/^\/(shorts|embed|live)\//.test(u.pathname)) return u.origin + u.pathname;
      const v = u.searchParams.get("v");
      if (!v) return null;                       // home/search previews: not this page's video
      const list = u.searchParams.get("list");
      return `https://www.youtube.com/watch?v=${v}` + (list ? `&list=${list}` : "");
    }
    return location.href;
  }

  function send(msg) {
    return chrome.runtime.sendMessage(msg).catch(e => ({ ok: false, error: e.message }));
  }

  function fmtSize(n) {
    if (!n) return "";
    if (n >= 1 << 30) return (n / (1 << 30)).toFixed(2) + " GB";
    if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
    return Math.max(1, Math.round(n / 1024)) + " KB";
  }

  // ---------- UI (shadow DOM so page styles can't break it)
  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;z-index:2147483647;top:0;left:0;display:none;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
<style>
  .wrap{position:relative;transform:translateX(-100%);font:13px "Segoe UI",Roboto,Arial,sans-serif}
  .btn{display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid #155c32;border-radius:4px;
       background:linear-gradient(#3fb26b,#1e7a43);color:#fff;font:600 13px "Segoe UI",Roboto,Arial,sans-serif;
       cursor:pointer;box-shadow:0 1px 5px rgba(0,0,0,.45);white-space:nowrap;opacity:.94}
  .btn:hover{opacity:1;background:linear-gradient(#47c276,#21874a)}
  .caret{font-size:10px;opacity:.9;margin-left:2px}
  .menu{position:absolute;right:0;top:calc(100% + 4px);min-width:250px;max-width:360px;background:#fff;color:#1b1b1b;
        border:1px solid #9aa7b4;border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,.35);padding:4px 0;
        max-height:70vh;overflow:auto}
  .menu[hidden]{display:none}
  .title{padding:6px 12px 7px;font-weight:600;color:#333;border-bottom:1px solid #e3e7eb;margin-bottom:3px;
         overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .item{display:flex;justify-content:space-between;gap:16px;padding:6px 12px;cursor:pointer;white-space:nowrap}
  .item:hover{background:#2e7bd6;color:#fff}
  .item .sz{color:#777}.item:hover .sz{color:#e8f0fb}
  .sep{height:1px;background:#e3e7eb;margin:4px 0}
  .note{padding:8px 12px;color:#555;white-space:normal}
  .err{color:#c62828}
  .spin{display:inline-block;width:11px;height:11px;border:2px solid #9ab;border-top-color:#2e7bd6;
        border-radius:50%;animation:s 0.8s linear infinite;vertical-align:-2px;margin-right:6px}
  @keyframes s{to{transform:rotate(360deg)}}
</style>
<div class="wrap">
  <button class="btn" type="button" title="Download with DownloadDeck">
    <svg width="16" height="16" viewBox="0 0 16 16"><path fill="#fff" d="M7 1h2v7h3l-4 4-4-4h3zM2 13h12v2H2z"/></svg>
    <span class="lbl">Download this video</span><span class="caret">&#9660;</span>
  </button>
  <div class="menu" hidden></div>
</div>`;
  const btn = root.querySelector(".btn");
  const lbl = root.querySelector(".lbl");
  const menu = root.querySelector(".menu");

  let current = null;        // video element the button is attached to
  let menuOpen = false;
  let hideTimer = null;
  const cache = new Map();   // url -> formats reply
  const inflight = new Map();  // url -> pending formats request

  // start the (slow) quality lookup on hover, so the menu is ready when clicked
  function prefetch(url) {
    if (!url || cache.has(url)) return Promise.resolve(cache.get(url));
    if (inflight.has(url)) return inflight.get(url);
    const p = send({ type: "formats", url }).then((data) => {
      inflight.delete(url);
      if (data && data.ok && !data.playlist_only) cache.set(url, data);
      return data;
    });
    inflight.set(url, p);
    return p;
  }

  function attachHost() {
    const parent = document.fullscreenElement || document.documentElement;
    if (host.parentNode !== parent) parent.appendChild(host);
  }

  function usable(v) {
    const r = v.getBoundingClientRect();
    return r.width >= 200 && r.height >= 110 && r.bottom > 0 && r.top < innerHeight &&
           getComputedStyle(v).visibility !== "hidden";
  }

  function position() {
    if (!current || !current.isConnected) return hide();
    const r = current.getBoundingClientRect();
    host.style.top = Math.max(4, r.top + 10) + "px";
    host.style.left = Math.min(innerWidth - 4, r.right - 10) + "px";
  }

  function show(v) {
    if (!pageVideoUrl() && !directSrc(v)) return;
    current = v;
    attachHost();
    host.style.display = "block";
    position();
    prefetch(pageVideoUrl());
  }

  function hide() {
    if (menuOpen) return;
    host.style.display = "none";
    current = null;
  }

  function closeMenu() {
    menu.hidden = true;
    menuOpen = false;
  }

  function directSrc(v) {
    const s = v && (v.currentSrc || v.src);
    return s && /^https?:/i.test(s) ? s : null;
  }

  function videoAt(x, y) {
    for (const v of document.querySelectorAll("video")) {
      if (!usable(v)) continue;
      const r = v.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return v;
    }
    return null;
  }

  document.addEventListener("mousemove", (e) => {
    if (menuOpen) return;
    if (e.composedPath().includes(host)) { clearTimeout(hideTimer); return; }
    const v = videoAt(e.clientX, e.clientY);
    if (v) {
      clearTimeout(hideTimer);
      if (v !== current || host.style.display === "none") show(v);
    } else if (current) {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hide, 1500);
    }
  }, { passive: true, capture: true });

  addEventListener("scroll", () => current && position(), { passive: true, capture: true });
  addEventListener("resize", () => current && position(), { passive: true });
  document.addEventListener("fullscreenchange", () => { attachHost(); setTimeout(position, 200); });
  document.addEventListener("mousedown", (e) => {
    if (menuOpen && !e.composedPath().includes(host)) { closeMenu(); hideTimer = setTimeout(hide, 800); }
  }, true);
  document.addEventListener("yt-navigate-finish", () => { closeMenu(); hide(); });

  // ---------- menu
  function item(text, size, onClick, cls = "") {
    const d = document.createElement("div");
    d.className = "item " + cls;
    const a = document.createElement("span"); a.textContent = text;
    const b = document.createElement("span"); b.className = "sz"; b.textContent = size || "";
    d.append(a, b);
    d.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    return d;
  }

  function note(html, cls = "") {
    const d = document.createElement("div");
    d.className = "note " + cls;
    d.innerHTML = html;
    return d;
  }

  function sep() { const d = document.createElement("div"); d.className = "sep"; return d; }

  async function start(url, quality, label) {
    closeMenu();
    lbl.textContent = "Sending...";
    const res = await send({ type: "grab-video", url, quality, referrer: location.href });
    lbl.textContent = res && res.ok ? `Downloading ${label}` : "Failed - see menu";
    if (!(res && res.ok)) {
      renderError(url, (res && res.error) || "DownloadDeck did not answer");
      menu.hidden = false; menuOpen = true;
    }
    setTimeout(() => (lbl.textContent = "Download this video"), 3000);
  }

  async function startFile(src) {
    closeMenu();
    lbl.textContent = "Sending...";
    const res = await send({ type: "grab-file", url: src, referrer: location.href });
    lbl.textContent = res && res.ok ? "Sent to DownloadDeck" : "DownloadDeck not reachable";
    setTimeout(() => (lbl.textContent = "Download this video"), 3000);
  }

  function renderError(url, error) {
    menu.replaceChildren();
    menu.append(note(`<span class="err">Couldn't get qualities.</span><br>${escapeHtml(error)}`));
    menu.append(sep());
    menu.append(item("Open in DownloadDeck...", "", () => start(url, "__dialog__", "")));
    const src = directSrc(current);
    if (src) menu.append(item("Download the playing file directly", "", () => startFile(src)));
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function render(url, data) {
    menu.replaceChildren();
    if (!data.ok) return renderError(url, data.error || "unknown error");
    if (data.title) {
      const t = document.createElement("div"); t.className = "title"; t.textContent = data.title; t.title = data.title;
      menu.append(t);
    }
    const audio = data.qualities.filter(q => q.label.startsWith("Audio"));
    const vids = data.qualities.filter(q => !q.label.startsWith("Audio"));
    for (const q of vids) menu.append(item(q.label === "Best available" ? "Best quality" : q.label + "  MP4",
                                           fmtSize(q.size), () => start(url, q.label, q.label)));
    if (audio.length) menu.append(sep());
    for (const q of audio) menu.append(item(q.label.replace("Audio only", "Audio"), fmtSize(q.size),
                                            () => start(url, q.label, q.label.includes("MP3") ? "MP3" : "M4A")));
    menu.append(sep());
    if (data.playlist) menu.append(item("Whole playlist...", "", () => start(url, "__dialog__", "playlist")));
    menu.append(item("More options...", "", () => start(url, "__dialog__", "")));
  }

  btn.addEventListener("click", async (e) => {
    e.stopPropagation(); e.preventDefault();
    if (menuOpen) { closeMenu(); return; }
    const url = pageVideoUrl();
    menuOpen = true;
    menu.hidden = false;
    if (!url) {                                   // a plain <video> with a real file behind it
      menu.replaceChildren(item("Download this video file", "", () => startFile(directSrc(current))));
      return;
    }
    if (cache.has(url)) return render(url, cache.get(url));
    menu.replaceChildren(note('<span class="spin"></span>Getting available qualities...'));
    const data = await prefetch(url);
    if (data && data.playlist_only) { closeMenu(); return start(url, "__dialog__", "playlist"); }
    if (menuOpen) render(url, data || { ok: false, error: "no reply" });
  });

  // ---------- YouTube playlist pages (no player there): floating button
  const PL_ID = "downloaddeck-playlist";
  function playlistButton() {
    if (!isYT || window.top !== window) return;
    const u = new URL(location.href);
    const list = u.searchParams.get("list");
    let b = document.getElementById(PL_ID);
    if (u.pathname !== "/playlist" || !list) { if (b) b.remove(); return; }
    if (b) return;
    b = document.createElement("button");
    b.id = PL_ID;
    b.textContent = "⬇  Download playlist";
    b.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:9999;padding:9px 14px;border:1px solid #155c32;" +
      "border-radius:4px;background:linear-gradient(#3fb26b,#1e7a43);color:#fff;font:600 14px Roboto,Arial,sans-serif;" +
      "cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4)";
    b.addEventListener("click", async () => {
      b.textContent = "Sending...";
      const res = await send({ type: "grab-video", url: `https://www.youtube.com/playlist?list=${list}`,
                               quality: "__dialog__", referrer: location.href });
      b.textContent = res && res.ok ? "Sent to DownloadDeck" : "DownloadDeck not reachable";
      setTimeout(() => (b.textContent = "⬇  Download playlist"), 2500);
    });
    document.body.appendChild(b);
  }
  document.addEventListener("yt-navigate-finish", playlistButton);
  setInterval(playlistButton, 1500);
  playlistButton();
})();
