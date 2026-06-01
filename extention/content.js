// YouTube 페이지의 URL, 제목, 재생 시간을 로컬 Flask 서버와 동기화하고
// 서버에서 내려온 PC 제어 명령을 현재 영상에 반영합니다.

// ==========================================
// YouTube 감지 및 서버 동기화용 콘텐츠 스크립트
// ==========================================
const API = "http://localhost:5000";
const INTERVAL = 500;
let lastSentSignature = "";
const SENDER_ID = `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

function getYoutubeTitle() {
    // SPA 전환 직후에도 잡히도록 DOM 제목, og:title, 문서 제목 순서로 탐색한다.
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
    // 백그라운드 탭은 감지 대상에서 제외해 중복 전송을 줄인다.
    if (document.hidden) return;

    // 일반 시청 페이지와 쇼츠만 동기화한다.
    const isYoutubeWatch = url.includes("youtube.com/watch") || url.includes("youtube.com/shorts/") || url.includes("youtu.be/");
    if (!isYoutubeWatch) return;
    const title = getYoutubeTitle();
    const signature = `${url}|${title}`;
    if (signature === lastSentSignature) return;

    // 같은 URL/제목 조합은 한 번만 전송한다.
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

// 초기 로드 시 서버가 늦게 떠 있어도 잡히도록 몇 번 재시도한다.
let notifyAttempts = 0;
function tryNotify() {
    notify(location.href);
    if (++notifyAttempts < 5) {
        setTimeout(tryNotify, 1000);
    }
}
tryNotify();

// SPA 라우팅으로 바뀌는 주소를 놓치지 않기 위해 history 변화를 가로챈다.
const p = history.pushState, r = history.replaceState;
history.pushState = function(...a) { p.apply(this, a); notify(location.href); };
history.replaceState = function(...a) { r.apply(this, a); notify(location.href); };
window.addEventListener("popstate", () => notify(location.href));

// 탭이 다시 활성화되면 최신 제목과 URL을 재전송한다.
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) notify(location.href);
});

// 초기 렌더 이후 제목이 갱신되는 타이밍을 한 번 더 보정한다.
setTimeout(() => notify(location.href), 1500);

// 서버가 늦게 시작되더라도 감지가 복구되도록 가벼운 heartbeat를 유지한다.
setInterval(() => notify(location.href), 3000);

// ==========================================
// 재생 시간 동기화 및 원격 제어 루프
// ==========================================
setInterval(() => {
    // 현재 탭이 숨겨져 있으면 재생 제어 충돌을 줄이기 위해 건너뛴다.
    if (document.hidden) return;

    const v = document.querySelector("video");
    if (!v) return;

    // 현재 재생 시간은 서버로 보내고, 서버 명령은 즉시 반영한다.
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
                // 현재 재생 위치와 충분히 다를 때만 점프한다.
                if (typeof d.time === "number" && Math.abs(v.currentTime - d.time) > 0.35) {
                    v.currentTime = d.time;
                }
                break;
            case "play":
                v.play();
                break;
            case "pause":
                v.pause();
                break;
        }
    }).catch(() => {});

}, INTERVAL);
