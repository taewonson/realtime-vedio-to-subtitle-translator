// YouTube Subtitle Sync - Content Script (Improved detection)
const API = "http://localhost:5000";
const INTERVAL = 500;
let lastSentSignature = "";
const SENDER_ID = `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

function getYoutubeTitle() {
    const heading = document.querySelector("h1.ytd-watch-metadata yt-formatted-string");
    if (heading && heading.textContent) {
        const t = heading.textContent.trim();
        if (t && t.toLowerCase() !== "youtube") return t;
    }

    const ogTitle = document.querySelector('meta[property="og:title"]');
    if (ogTitle && ogTitle.content) {
        const t = ogTitle.content.trim();
        if (t && t.toLowerCase() !== "youtube") return t;
    }

    const fallback = document.title.replace(" - YouTube", "").trim();
    if (fallback && fallback.toLowerCase() !== "youtube") return fallback;
    return "";
}

function notify(url) {
    if (document.hidden) return;

    const isYoutubeWatch = url.includes("youtube.com/watch") || url.includes("youtube.com/shorts/") || url.includes("youtu.be/");
    if (!isYoutubeWatch) return;
    const title = getYoutubeTitle();
    const signature = `${url}|${title}`;
    if (signature === lastSentSignature) return;

    fetch(API + "/detect_url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, title })
    }).then((res) => {
        if (res && res.ok) {
            lastSentSignature = signature;
        }
    }).catch(() => {}); // Retry will happen on next interval/history/visibility events
}

// Try initial notify with retry
let notifyAttempts = 0;
function tryNotify() {
    notify(location.href);
    if (++notifyAttempts < 5) {
        setTimeout(tryNotify, 1000);
    }
}
tryNotify();

// Intercept history changes
const p = history.pushState, r = history.replaceState;
history.pushState = function(...a) { p.apply(this, a); notify(location.href); };
history.replaceState = function(...a) { r.apply(this, a); notify(location.href); };
window.addEventListener("popstate", () => notify(location.href));

// Monitor visibility changes (tab focus)
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) notify(location.href);
});

// Retry once when title is likely to be updated after initial SPA render.
setTimeout(() => notify(location.href), 1500);

// Keep a lightweight heartbeat so detection recovers even if server starts late.
setInterval(() => notify(location.href), 3000);

// Main sync loop
setInterval(() => {
    if (document.hidden) return;

    const v = document.querySelector("video");
    if (!v) return;
    
    Promise.all([
        fetch(API + "/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ time: v.currentTime, url: location.href, sender_id: SENDER_ID })
        }).catch(() => null),
        fetch(API + "/get_command").catch(() => null)
    ]).then(([,c]) => c?.ok ? c.json() : null).then(d => {
        if (!d?.command) return;
        switch(d.command) {
            case "seek":
                if (typeof d.time === "number" && Math.abs(v.currentTime - d.time) > 0.35) {
                    v.currentTime = d.time;
                }
                break;
            case "play": v.play(); break;
            case "pause": v.pause(); break;
        }
    }).catch(() => {});
}, INTERVAL);
