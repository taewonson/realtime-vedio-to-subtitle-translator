# Chrome Extension 설정 및 테스트 가이드

## 1. Extension 로드 방법

### Step 1: Chrome 개발자 모드 활성화
1. Chrome에서 `chrome://extensions/` 이동
2. 우측 상단의 "개발자 모드" 토글 ON

### Step 2: Extension 폴더 로드
1. "확장 프로그램 로드" 클릭
2. `youtube_sync_extension` 폴더 선택
3. Extension이 목록에 표시됨 (ID 확인)

### Step 3: 권한 확인
- Extension이 `127.0.0.1:8765` 포트에 접근 가능한지 확인
- manifest.json의 host_permissions 설정 확인

---

## 2. Python 앱 실행

```bash
pip install numpy whisper yt-dlp
python browser_sync_whisper_test.py
```

### 콘솔 출력 확인
```
[Server] 브라우저 동기화 서버 시작: http://127.0.0.1:8765/sync
```

---

## 3. Extension-Python 통신 검증

### A. Python 콘솔 로그 확인
프로그램 실행 중 다음과 같은 메시지가 보이면 정상:

```
[Server] 브라우저 동기화 서버 시작: http://127.0.0.1:8765/sync
[Extension] GET /health
[Extension] OPTIONS /sync
[Extension] POST /sync - url=https://www.youtube.com/watch?v=... currentTime=12.34s
```

### B. Chrome 개발자 창 로그 확인
YouTube 페이지에서 우클릭 → 검사 → Console 탭

정상 메시지:
```
[yt-sync-extension] Initialized on: https://www.youtube.com/watch?v=...
[yt-sync-extension] Local sync server connected.
[yt-sync-extension] Sent state: https://www.youtube.com/watch?v=... at 12.3s
```

오류 메시지:
```
[yt-sync-extension] Local sync server is not responding.
Make sure the Python application is running on 127.0.0.1:8765.
```

---

## 4. 문제 해결

### 1️⃣ Extension이 로드되지 않음
- manifest.json 문법 오류 확인
- Path: `youtube_sync_extension/manifest.json`
- 구문 검증: `chrome://extensions/` 에서 오류 메시지 확인

### 2️⃣ "[yt-sync-extension] Local sync server is not responding"
- **원인**: Python 앱이 실행되지 않음
- **해결**: 
  ```bash
  python browser_sync_whisper_test.py
  ```
  콘솔에서 `[Server] 브라우저 동기화 서버 시작...` 메시지 확인

### 3️⃣ Python 콘솔에 "[Extension]" 메시지가 안 보임
- **원인**: Extension이 YouTube 페이지에서 활성화되지 않음
- **확인**:
  1. YouTube 페이지 방문 (https://www.youtube.com)
  2. 개발자 창(F12) → Console 탭 확인
  3. `[yt-sync-extension]` 로그 확인
  
### 4️⃣ OPTIONS/CORS 오류
- manifest.json에서 host_permissions 확인:
  ```json
  "host_permissions": [
    "http://127.0.0.1:8765/*",
    "http://localhost:8765/*"
  ]
  ```

---

## 5. 디버깅 모드

### Extension 콘솔 메시지 필터링
DevTools Console에서 필터: `yt-sync-extension`

### Python 서버 메시지
콘솔에서 `[Server]`, `[Extension]` 프리픽스로 필터링 가능

### 통신 흐름
```
1. Extension 로드 → [yt-sync-extension] Initialized
2. 500ms마다 health check → [Extension] GET /health
3. server 온라인 확인 → [yt-sync-extension] Local sync server connected
4. 500ms마다 상태 전송 → [Extension] POST /sync
```

---

## 6. 설정 변경

### Extension → Python 포트 변경
**file**: `youtube_sync_extension/content.js` (라인 4-5)
```javascript
const SERVER_URL = "http://127.0.0.1:8765/sync";    // port 변경 가능
const HEALTH_URL = "http://127.0.0.1:8765/health";  // 동일 포트
```

### Python 서버 포트 변경
**file**: `browser_sync_whisper_test.py` (라인 60-61)
```python
SYNC_SERVER_HOST = "127.0.0.1"
SYNC_SERVER_PORT = 8765  # 변경 가능
```

두 곳을 동일 포트로 변경해야 함.

---

## 7. 정상 작동 확인

✅ Python 앱이 실행 중
```bash
[Server] 브라우저 동기화 서버 시작: http://127.0.0.1:8765/sync
```

✅ Extension이 로드됨
- `chrome://extensions/` 에서 "YouTube Playback Sync Sender" 표시

✅ YouTube 페이지에서 Console 메시지 확인
```
[yt-sync-extension] Initialized on: https://www.youtube.com/watch?v=...
[yt-sync-extension] Local sync server connected.
```

✅ Python 콘솔에 Extension 요청 표시
```
[Extension] POST /sync - url=https://www.youtube.com/watch?v=... currentTime=...
```

모두 확인되면 **정상 작동 중**입니다! 🎉
