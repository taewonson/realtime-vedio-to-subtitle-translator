(() => {
  "use strict";

  // 이 content script는 YouTube 플레이어 상태를 주기적으로 수집해
  // 로컬 Python 게이트웨이로 전송합니다. 브라우저 내부의 무거운 처리는 피합니다.

  /**
    * 로컬 게이트웨이 엔드포인트.
    * Python 앱은 이 주소로 브라우저 재생 메타데이터를 수신합니다.
    * 음성 인식/번역은 확장 프로그램이 아니라 Python 쪽에서 수행합니다.
   */
  const SERVER_URL = "http://127.0.0.1:8765/sync";
  const HEALTH_URL = "http://127.0.0.1:8765/health";

  /**
    * 폴링 주기.
    * 동기화 간격을 짧게 유지해 자막 타이밍을 실제 재생 상태에 가깝게 맞춥니다.
   */
  const SYNC_INTERVAL_MS = 500;
  const HEALTH_CHECK_MS = 3000;
  const NAVIGATION_CHECK_MS = 1000;

  let syncIntervalId = null;
  let healthIntervalId = null;
  let navigationIntervalId = null;
  let serverOnline = false;
  let sendBlockedUntil = 0;
  let lastKnownUrl = location.href;

  /**
    * 각 content-script 컨텍스트는 하나의 런타임 세션 ID를 가집니다.
    * Python 앱은 이 값을 사용해 페이지 단위 재생 세션을 구분합니다.
    * 특히 전체 새로고침 없이 다른 영상으로 이동하는 경우에 중요합니다.
   */
  let extensionSessionId = createSessionId();

  function createSessionId() {
    const randomPart = Math.random().toString(36).slice(2, 10);
    const tsPart = Date.now().toString(36);
    return `yt-${tsPart}-${randomPart}`;
  }

  function getVideoElement() {
    return document.querySelector("video");
  }

  function extractVideoId() {
    try {
      const url = new URL(location.href);
      return url.searchParams.get("v") || url.pathname || "unknown-video";
    } catch (error) {
      return "unknown-video";
    }
  }

  async function checkServerHealth() {
    try {
      // Python 앱이 꺼져 있을 때 불필요한 동기화 실패 로그를 줄이기 위해 health 체크를 수행합니다.
      const response = await fetch(HEALTH_URL, {
        method: "GET",
        cache: "no-store"
      });
      serverOnline = response.ok;
      if (response.ok) {
        sendBlockedUntil = 0;
      }
      return response.ok;
    } catch (error) {
      serverOnline = false;
      return false;
    }
  }

  function buildPayload(video) {
    return {
      // sessionId에 content-script 세션 + video id를 함께 넣어,
      // 탭 전체 새로고침 없이 발생한 내비게이션 변경도 구분합니다.
      sessionId: `${extensionSessionId}:${extractVideoId()}`,
      url: location.href,
      currentTime: Number(video.currentTime || 0),
      paused: Boolean(video.paused),
      title: document.title || "",
      playbackRate: Number(video.playbackRate || 1.0),
      sentAt: Date.now()
    };
  }

  async function sendState() {
    const video = getVideoElement();
    if (!video) return;
    if (!serverOnline) return;
    if (Date.now() < sendBlockedUntil) return;

    try {
      const response = await fetch(SERVER_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildPayload(video))
      });

      if (!response.ok) {
        console.warn(`[yt-sync-extension] Sync server returned HTTP ${response.status}`);
      }
    } catch (error) {
      serverOnline = false;
      sendBlockedUntil = Date.now() + 10000;
    }
  }

  function stopSync() {
    // 재초기화 전에 호출해 중복 interval 생성을 방지합니다.
    if (syncIntervalId !== null) {
      clearInterval(syncIntervalId);
      syncIntervalId = null;
    }
    if (healthIntervalId !== null) {
      clearInterval(healthIntervalId);
      healthIntervalId = null;
    }
  }

  function startSync() {
    stopSync();
    void checkServerHealth();

    healthIntervalId = setInterval(() => {
      void checkServerHealth();
    }, HEALTH_CHECK_MS);

    syncIntervalId = setInterval(() => {
      void sendState();
    }, SYNC_INTERVAL_MS);
  }

  function startNavigationWatcher() {
    if (navigationIntervalId !== null) return;

    navigationIntervalId = setInterval(() => {
      if (location.href !== lastKnownUrl) {
        // YouTube SPA의 소프트 내비게이션에서도 새 세션 ID를 발급합니다.
        lastKnownUrl = location.href;
        extensionSessionId = createSessionId();
        startSync();
      }
    }, NAVIGATION_CHECK_MS);
  }

  function main() {
    startSync();
    startNavigationWatcher();
  }

  main();
})();
