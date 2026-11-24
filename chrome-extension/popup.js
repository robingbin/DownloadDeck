const DEFAULTS = { enabled: true, port: 9614, minSizeKB: 0 };
const $ = id => document.getElementById(id);
(async () => {
  const s = { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
  $("enabled").checked = s.enabled; $("port").value = s.port; $("minSizeKB").value = s.minSizeKB;
  for (const id of ["enabled", "port", "minSizeKB"]) {
    $(id).addEventListener("change", () => chrome.storage.sync.set({
      enabled: $("enabled").checked, port: +$("port").value || 9614, minSizeKB: +$("minSizeKB").value || 0 }));
  }
  $("grab").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url?.startsWith("http")) { $("grab").textContent = "No web page in this tab"; return; }
    const res = await chrome.runtime.sendMessage({ type: "grab-video", url: tab.url, quality: "__dialog__", referrer: tab.url });
    $("grab").textContent = res?.ok ? "Sent to DownloadDeck" : "DownloadDeck is not running";
  });
  const res = await chrome.runtime.sendMessage({ type: "ping" }).catch(() => null);
  if (res && res.ok && res.running) {
    $("st").textContent = "● DownloadDeck is running"; $("st").className = "ok";
  } else if (res && res.ok) {
    $("st").textContent = "● Connected - DownloadDeck starts when you download"; $("st").className = "ok";
  } else {
    $("st").textContent = "● " + ((res && res.error) || "Cannot reach DownloadDeck"); $("st").className = "bad";
  }
})();
