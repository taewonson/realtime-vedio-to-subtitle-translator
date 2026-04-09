(() => {
  "use strict";

  const SERVER_URL = "http://127.0.0.1:8765/sync";
  const HEALTH_URL = "http://127.0.0.1:8765/health";

  const SYNC_INTERVAL_MS = 500;
  const HEALTH_CHECK_MS = 3000;

  let syncIntervalId = null;
  let healthIntervalId = null;
  let navigationIntervalId = null;
  let serverOnline = false;
  let sendBlockedUntil = 0;
  let lastKnownUrl = location.href;

  function getVideoElement() {
    return document.querySelector("video");
  }

  async function checkServerHealth() {
    try {
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
      url: location.href,
      currentTime: Number(video.currentTime || 0),
      paused: Boolean(video.paused),
      title: document.title || ""
    };
  }

  async function sendState() {
    const video = getVideoElement();

    if (!video) {
      return;
    }

    if (!serverOnline) {
      return;
    }

    if (Date.now() < sendBlockedUntil) {
      return;
    }

    try {
      const payload = buildPayload(video);
      await fetch(SERVER_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });
    } catch (error) {
      serverOnline = false;
      sendBlockedUntil = Date.now() + 10000;
    }
  }

  function stopSync() {
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

    healthIntervalId = setInterval(async () => {
      await checkServerHealth();
    }, HEALTH_CHECK_MS);

    syncIntervalId = setInterval(() => {
      void sendState();
    }, SYNC_INTERVAL_MS);
  }

  function startNavigationWatcher() {
    if (navigationIntervalId !== null) {
      return;
    }

    navigationIntervalId = setInterval(() => {
      if (location.href !== lastKnownUrl) {
        lastKnownUrl = location.href;
        startSync();
      }
    }, 1000);
  }

  function main() {
    startSync();
    startNavigationWatcher();
  }

  main();
})();