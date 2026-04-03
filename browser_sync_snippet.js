/*
브라우저 동기화 스니펫
====================

사용 목적
---------
- 유튜브 페이지에서 현재 video.currentTime, paused, url, title 을
  Python 앱(localhost:8765)으로 주기적으로 보낸다.

사용 방법
---------
1. 유튜브 영상 페이지를 연다.
2. 개발자 도구 콘솔(F12)에서 이 코드를 붙여넣고 실행한다.
3. Python 앱이 실행 중이어야 한다.
4. 다시 실행하면 기존 동기화를 중단하고 새로 시작한다.

주의
----
- 페이지 새로고침 시 다시 실행해야 한다.
- 정식 구조로 갈 때는 이 코드를 브라우저 확장의 content script로 옮기면 된다.
*/

(() => {
  const SERVER_URL = "http://127.0.0.1:8765/sync";
  const HEALTH_URL = "http://127.0.0.1:8765/health";
  const INTERVAL_MS = 500;
  const HEALTH_CHECK_MS = 3000;
  const HEALTH_WARN_INTERVAL_MS = 10000;

  // 기존 interval 제거
  if (window.__ytSyncIntervalId) {
    clearInterval(window.__ytSyncIntervalId);
    window.__ytSyncIntervalId = null;
    console.log("[yt-sync] 기존 동기화 중단");
  }

  if (window.__ytSyncHealthIntervalId) {
    clearInterval(window.__ytSyncHealthIntervalId);
    window.__ytSyncHealthIntervalId = null;
  }

  window.__ytSyncServerOnline = false;
  window.__ytSyncLastWarnAt = 0;
  window.__ytSyncSendBlockedUntil = 0;

  // YouTube 비디오 요소를 찾기 (다양한 선택자 시도)
  function getVideoElement() {
    // 방법 1: HTML5 video 태그 직접 선택
    let video = document.querySelector("video");
    if (video) return video;
    
    // 방법 2: ytInitialData에서 비디오 ID 확인 (로드 지연 시)
    if (!video && window.unsafeWindow) {
      video = window.unsafeWindow.document.querySelector("video");
    }
    
    return video;
  }

  async function checkServerHealth() {
    try {
      const response = await fetch(HEALTH_URL, {
        method: "GET",
        cache: "no-store",
      });
      window.__ytSyncServerOnline = response.ok;
      if (response.ok) {
        window.__ytSyncSendBlockedUntil = 0;
      }
      return response.ok;
    } catch (err) {
      window.__ytSyncServerOnline = false;
      return false;
    }
  }

  function maybeWarnServerOffline() {
    const now = Date.now();
    if (now - window.__ytSyncLastWarnAt >= HEALTH_WARN_INTERVAL_MS) {
      console.warn("[yt-sync] Python 서버가 응답하지 않습니다. 프로그램이 실행 중인지 확인하세요.");
      window.__ytSyncLastWarnAt = now;
    }
  }

  async function sendState() {
    const video = getVideoElement();
    if (!video) {
      console.debug("[yt-sync] 비디오 요소 미발견 (아직 로드 중?)");
      return;
    }

    if (!window.__ytSyncServerOnline) {
      return;
    }

    if (Date.now() < window.__ytSyncSendBlockedUntil) {
      return;
    }

    const payload = {
      url: location.href,
      currentTime: Number(video.currentTime || 0),
      paused: Boolean(video.paused),
      title: document.title || "",
    };

    try {
      const response = await fetch(SERVER_URL, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      
      if (!response.ok) {
        console.warn(`[yt-sync] 서버 응답 오류: ${response.status}`);
      }
    } catch (err) {
      window.__ytSyncServerOnline = false;
      window.__ytSyncSendBlockedUntil = Date.now() + 10000;
      maybeWarnServerOffline();
    }
  }

  // 동기화 시작
  checkServerHealth().then((online) => {
    if (!online) {
      maybeWarnServerOffline();
    } else {
      console.log("[yt-sync] Python 서버 연결됨");
    }
  });

  window.__ytSyncHealthIntervalId = setInterval(async () => {
    const online = await checkServerHealth();
    if (online) {
      window.__ytSyncLastWarnAt = 0;
    }
  }, HEALTH_CHECK_MS);

  window.__ytSyncIntervalId = setInterval(sendState, INTERVAL_MS);
  
  console.log("[yt-sync] 동기화 시작됨 (Python 앱: localhost:8765)");
  console.log("[yt-sync] 중단하려면: clearInterval(window.__ytSyncIntervalId); window.__ytSyncIntervalId = null;");
})();