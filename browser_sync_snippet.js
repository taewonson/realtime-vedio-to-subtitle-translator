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

  // 기존 interval 제거
  if (window.__ytSyncIntervalId) {
    clearInterval(window.__ytSyncIntervalId);
    window.__ytSyncIntervalId = null;
  }

  if (window.__ytSyncHealthIntervalId) {
    clearInterval(window.__ytSyncHealthIntervalId);
    window.__ytSyncHealthIntervalId = null;
  }

  window.__ytSyncServerOnline = false;
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

  async function sendState() {
    const video = getVideoElement();
    if (!video) {
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
      await fetch(SERVER_URL, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
    } catch (err) {
      window.__ytSyncServerOnline = false;
      window.__ytSyncSendBlockedUntil = Date.now() + 10000;
    }
  }

  // 동기화 시작
  void checkServerHealth();

  window.__ytSyncHealthIntervalId = setInterval(async () => {
    await checkServerHealth();
  }, HEALTH_CHECK_MS);

  window.__ytSyncIntervalId = setInterval(sendState, INTERVAL_MS);
})();