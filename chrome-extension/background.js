// DownloadDeck browser integration: hands downloads and videos to the desktop app.
// Talks to the app through the native messaging host (like IDM), falling back to
// http://127.0.0.1 with a timeout if the host isn't registered.
const HOST = "com.downloaddeck.bridge";
const DEFAULTS = { enabled: true, port: 9614, minSizeKB: 0 };
const SKIP_SCHEMES = ["blob:", "data:", "file:", "chrome:", "edge:", "about:", "filesystem:"];
const handled = new Set();

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
}

function withTimeout(promise, ms, what) {
  return Promise.race([promise, new Promise((_, rej) => setTimeout(() => rej(new Error(what + " timed out")), ms))]);
}

async function viaNative(path, body, ms) {
  const res = await withTimeout(chrome.runtime.sendNativeMessage(HOST, { path, body }), ms, "DownloadDeck");
  if (!res) throw new Error("no reply from DownloadDeck helper");
  return res;
}

async function viaHttp(path, body, ms) {
  const { port } = await settings();
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: "POST", signal: ctl.signal,
      headers: { "Content-Type": "application/json", "X-DownloadDeck": "1" },
      body: JSON.stringify(body)
    });
    return await r.json();
  } finally { clearTimeout(t); }
}

// returns the app's JSON reply, or {ok:false, error}
async function callApp(path, body) {
  const ms = path === "/formats" ? 100000 : 20000;
  let nativeErr;
  try { return await viaNative(path, body, ms); } catch (e) { nativeErr = e; }
  try { return await viaHttp(path, body, Math.min(ms, 8000)); } catch (e) {
    return { ok: false, error: `DownloadDeck not reachable (${nativeErr?.message || nativeErr}; ${e.message})` };
  }
}

async function cookiesFor(url) {
  try {
    const list = await chrome.cookies.getAll({ url });
    return list.map(c => `${c.name}=${c.value}`).join("; ");
  } catch { return ""; }
}

async function sendDownload(payload) {
  const body = { ...payload, userAgent: navigator.userAgent };
  if (!payload.video) body.cookies = await cookiesFor(payload.url);
  return callApp("/download", body);
}

chrome.downloads.onCreated.addListener(async (item) => {
  const s = await settings();
  const url = item.finalUrl || item.url;
  if (!s.enabled || !url || SKIP_SCHEMES.some(p => url.startsWith(p))) return;
  if (handled.has(item.id)) return;
  if (s.minSizeKB > 0 && item.totalBytes > 0 && item.totalBytes < s.minSizeKB * 1024) return;
  handled.add(item.id);
  try { await chrome.downloads.pause(item.id); } catch {}
  const res = await sendDownload({ url, filename: item.filename ? item.filename.split(/[\\/]/).pop() : "",
                                   referrer: item.referrer || "" });
  if (res.ok) {
    try { await chrome.downloads.cancel(item.id); await chrome.downloads.erase({ id: item.id }); } catch {}
  } else {
    try { await chrome.downloads.resume(item.id); } catch {}  // app unavailable: browser keeps it
  }
});

const VIDEO_SITES = ["*://*.youtube.com/*", "*://youtu.be/*", "*://*.vimeo.com/*", "*://*.dailymotion.com/*",
  "*://*.facebook.com/*", "*://*.instagram.com/*", "*://*.x.com/*", "*://*.twitter.com/*",
  "*://*.tiktok.com/*", "*://*.reddit.com/*", "*://*.twitch.tv/*", "*://*.soundcloud.com/*",
  "*://*.bilibili.com/*", "*://streamable.com/*"];

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "dd-link", title: "Download with DownloadDeck", contexts: ["link"] });
    chrome.contextMenus.create({ id: "dd-page-video", title: "Download video on this page with DownloadDeck",
                                 contexts: ["page", "video"], documentUrlPatterns: VIDEO_SITES });
    chrome.contextMenus.create({ id: "dd-link-video", title: "Download linked video with DownloadDeck",
                                 contexts: ["link"], targetUrlPatterns: VIDEO_SITES });
    chrome.contextMenus.create({ id: "dd-media", title: "Download media with DownloadDeck",
                                 contexts: ["video", "audio", "image"] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "dd-page-video" || info.menuItemId === "dd-link-video") {
    const url = info.menuItemId === "dd-link-video" ? info.linkUrl : (info.frameUrl || info.pageUrl);
    await sendDownload({ url, video: true, quality: "__dialog__", referrer: tab?.url || "" });
    return;
  }
  const url = info.menuItemId === "dd-link" ? info.linkUrl : info.srcUrl;
  if (!url || SKIP_SCHEMES.some(p => url.startsWith(p))) return;
  const res = await sendDownload({ url, referrer: tab?.url || "" });
  if (!res.ok) chrome.downloads.download({ url });
});

// messages from the in-page video button and the popup
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.type === "formats" && typeof msg.url === "string") {
    callApp("/formats", { url: msg.url }).then(reply);
    return true;
  }
  if (msg?.type === "grab-video" && typeof msg.url === "string") {
    sendDownload({ url: msg.url, video: true, quality: msg.quality || "__dialog__",
                   referrer: msg.referrer || "" }).then(reply);
    return true;
  }
  if (msg?.type === "grab-file" && typeof msg.url === "string") {
    sendDownload({ url: msg.url, referrer: msg.referrer || "" }).then(reply);
    return true;
  }
  if (msg?.type === "ping") {
    callApp("/ping", {}).then(reply);
    return true;
  }
});
