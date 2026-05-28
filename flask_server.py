# Chrome 확장과 PC/LCD 프로그램이 공유하는 재생 상태, 자막, 제어 명령을 Flask API로 관리합니다.
from flask import Flask, request, jsonify
from flask_cors import CORS
from bisect import bisect_right
import logging
import time
from urllib.parse import parse_qs, urlparse

# Flask 웹 서버 앱 생성 및 CORS(교차 출처 리소스 공유) 허용 설정
app = Flask(__name__)
CORS(app)

class SharedState:
    """
    백그라운드로 돌아가는 Flask 스레드와 메인 UI 스레드 간에 데이터를 공유하기 위한 상태 저장소입니다.
    데이터베이스 대신 메모리 상에서 현재 재생 상태와 자막 데이터를 들고 있습니다.
    """
    def __init__(self):
        self.subtitles = []
        self.subtitle_starts = []
        self.current_texts = {}
        self.current_time = 0.0
        self.total_time = 0.1
        self.pending_command = None
        self.detected_youtube_url = None
        self.detected_youtube_title = None
        self.current_video_title = ""
        self.active_video_url = ""
        self.active_video_key = ""
        self.current_cue_start = None
        self.current_cue_end = None
        self.playback_mismatch = False
        self.consecutive_mismatch = 0
        self.consecutive_match = 0
        self.active_sync_sender = ""
        self.last_sync_received_at = 0.0

    def reset_display(self):
        self.current_texts = {}
        self.current_cue_start = None
        self.current_cue_end = None

    def reset_sync(self):
        self.playback_mismatch = False
        self.consecutive_mismatch = 0
        self.consecutive_match = 0
        self.active_sync_sender = ""
        self.last_sync_received_at = 0.0

# 전역 상태 객체 초기화
state = SharedState()

# 자막 타이밍 병합 및 보정을 위한 설정값들
MERGE_GAP_SECONDS = 0.35           # 이 시간 내에 인접한 동일 자막은 하나로 병합 (깜빡임 방지)
GAP_BRIDGE_SECONDS = 0.20          # 자막 사이의 미세한 간격을 메울 시간 (깜빡임 방지)
NEXT_CUE_EPSILON = 0.02            # 다음 자막 시작 직전까지 이전 자막을 유지하기 위한 여유값
MISMATCH_THRESHOLD = 3             # 3번 연속 다른 영상으로 감지되면 다른 영상 재생 중으로 간주
MATCH_RECOVERY_THRESHOLD = 2       # 2번 연속 원래 영상으로 감지되면 정상 재생 상태로 복구
SYNC_SENDER_STALE_SECONDS = 2.5    # 이 시간 동안 신호가 없으면 다른 탭(sender)의 신호를 수락함


def _clear_current_display_state():
    """현재 화면에 표시될 자막 관련 상태를 초기화(화면에서 지움)합니다."""
    state.reset_display()


def _extract_video_key(url):
    """
    유튜브 URL에서 고유 영상 ID(Video Key)를 추출합니다.
    일반 watch 링크, shorts, embed, youtu.be 단축 링크 등을 모두 처리합니다.
    """
    if not isinstance(url, str) or not url.strip():
        return ""

    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")

    if "youtu.be" in host:
        return path.split("/")[0] if path else ""

    if "youtube.com" in host:
        if path == "watch":
            return parse_qs(parsed.query).get("v", [""])[0]
        if path.startswith("shorts/"):
            return path.split("/", 1)[1]
        if path.startswith("embed/"):
            return path.split("/", 1)[1]

    return ""


def _normalize_subtitles(subtitles_data):
    """
    STT 및 번역에서 넘어온 자막 데이터의 시간축을 정규화합니다.
    짧게 끊긴 자막을 병합하고, 자막 사이의 미세한 틈을 없애 화면 깜빡임을 줄입니다.
    """
    if not isinstance(subtitles_data, list):
        return []

    # 1단계: 유효하지 않은 데이터 제거 및 시작 시간순 정렬
    cleaned = []
    for item in subtitles_data:
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", 0.0))
            texts = item.get("texts", {})
            if end <= start or not isinstance(texts, dict):
                continue
        except (TypeError, ValueError, AttributeError):
            continue

        cleaned.append({"start": start, "end": end, "texts": texts})

    cleaned.sort(key=lambda cue: (cue["start"], cue["end"]))

    # 2단계: 내용이 같고 시간 간격이 좁은 자막 병합
    normalized = []
    for cue in cleaned:
        if not normalized:
            normalized.append(cue)
            continue

        prev = normalized[-1]
        gap = cue["start"] - prev["end"]
        same_text = prev["texts"] == cue["texts"]

        # 텍스트가 완전히 동일할 때만 병합
        if same_text and gap <= MERGE_GAP_SECONDS:
            prev["end"] = max(prev["end"], cue["end"])
            prev["start"] = min(prev["start"], cue["start"])
        else:
            # STT 타이밍 오류로 자막이 겹치는 경우 새 자막 시작 시간을 뒤로 미룸
            if cue["start"] < prev["end"]:
                cue = {"start": prev["end"], "end": cue["end"], "texts": cue["texts"]}
                if cue["end"] <= cue["start"]:
                    continue
            normalized.append(cue)

    # 3단계: 자막 사이의 미세한 공백을 메꿔 UI 깜빡임 방지
    for i in range(len(normalized) - 1):
        current = normalized[i]
        nxt = normalized[i + 1]
        gap = float(nxt["start"]) - float(current["end"])
        if 0 < gap <= GAP_BRIDGE_SECONDS:
            bridged_end = float(nxt["start"]) - NEXT_CUE_EPSILON
            if bridged_end > current["end"]:
                current["end"] = bridged_end

    return normalized

@app.route('/sync', methods=['POST'])
def sync_time():
    """
    크롬 확장 프로그램에서 주기적으로 보내는 현재 재생 시간 및 영상 정보를 수신합니다.
    """
    try:
        data = request.get_json() or {}
        current_time = data.get('time')
        sync_url = data.get('url')
        sender_id = data.get('sender_id')
        
        if current_time is not None:
            active_key = state.active_video_key
            sync_key = _extract_video_key(sync_url)
            safe_sender_id = sender_id.strip() if isinstance(sender_id, str) else ""
            now = time.monotonic()

            # 영상 불일치 여부 판단 로직 (디바운싱 기법 적용)
            if active_key:
                if not sync_key:
                    # 유효하지 않은 URL 수신 시 카운트 초기화 후 무시
                    state.consecutive_mismatch = 0
                    state.consecutive_match = 0
                    return jsonify({"status": "success"})

                if sync_key == active_key:
                    # 일치하는 영상일 경우
                    if safe_sender_id:
                        # 여러 탭에서 신호가 올 경우, 현재 활성화된 탭(sender)의 신호만 수용
                        if state.active_sync_sender and safe_sender_id != state.active_sync_sender:
                            owner_alive = (now - state.last_sync_received_at) <= SYNC_SENDER_STALE_SECONDS
                            if owner_alive:
                                return jsonify({"status": "success"})
                        state.active_sync_sender = safe_sender_id

                    state.last_sync_received_at = now
                    state.consecutive_match += 1
                    state.consecutive_mismatch = 0
                    if state.playback_mismatch and state.consecutive_match >= MATCH_RECOVERY_THRESHOLD:
                        state.playback_mismatch = False
                else:
                    # 다른 영상일 경우
                    state.consecutive_mismatch += 1
                    state.consecutive_match = 0
                    if state.consecutive_mismatch >= MISMATCH_THRESHOLD:
                        state.playback_mismatch = True

                    # 다른 영상이면 현재 자막을 화면에서 지움
                    if state.playback_mismatch:
                        _clear_current_display_state()
                    return jsonify({"status": "success"})
            else:
                # 아직 분석 대상 영상이 지정되지 않은 경우 초기화 상태 유지
                state.reset_sync()
                return jsonify({"status": "success"})

            # 다른 영상 재생 중이면 자막 탐색을 하지 않음
            if state.playback_mismatch:
                _clear_current_display_state()
                return jsonify({"status": "success"})

            # 안전한 시간 범위 내로 조정
            safe_time = float(current_time)
            if safe_time < 0:
                safe_time = 0.0
            if state.total_time > 0:
                safe_time = min(safe_time, state.total_time)

            state.current_time = safe_time
            
            # bisect를 사용하여 현재 시간에 해당하는 자막 인덱스를 빠르게 탐색
            idx = bisect_right(state.subtitle_starts, safe_time) - 1
            if idx >= 0:
                sub = state.subtitles[idx]
                if safe_time <= sub["end"]:
                    # 현재 시간이 자막 구간 안에 있으면 표시 텍스트 및 시간 업데이트
                    state.current_texts = sub["texts"]
                    state.current_cue_start = float(sub["start"])
                    state.current_cue_end = float(sub["end"])
                else:
                    # 자막 공백 구간
                    _clear_current_display_state()
            else:
                _clear_current_display_state()
    except (TypeError, KeyError, ValueError):
        pass
    
    return jsonify({"status": "success"})

@app.route('/get_command')
def get_command():
    """
    라즈베리파이(LCD)에서 입력받은 제어 명령(재생/정지/탐색)을
    크롬 확장 프로그램이 가져갈 수 있도록 제공합니다.
    가져가면 명령 큐를 비웁니다.
    """
    if state.pending_command:
        cmd = state.pending_command
        state.pending_command = None 
        return jsonify(cmd)
    return jsonify({"command": None})

@app.route('/detect_url', methods=['POST'])
def detect_url():
    """
    크롬 확장 프로그램이 감지한 사용자의 유튜브 URL과 제목을 수신하여 저장합니다.
    (UI에서 자동 감지 기능에 활용됨)
    """
    try:
        data = request.get_json() or {}
        url = data.get('url')
        title = data.get('title')
        if url and isinstance(url, str):
            state.detected_youtube_url = url
            state.detected_youtube_title = title if isinstance(title, str) else None
            if isinstance(title, str) and title.strip():
                state.current_video_title = title.strip()
    except (TypeError, ValueError):
        pass
    
    return jsonify({"status": "success"})

@app.route('/get_detected_url')
def get_detected_url():
    """
    PC UI 프로그램이 주기적으로(polling) 호출하여 감지된 URL이 있는지 확인합니다.
    중복 호출을 막기 위해 가져간 후에는 URL 상태를 초기화합니다.
    """
    url = state.detected_youtube_url
    title = state.detected_youtube_title
    if url:
        state.detected_youtube_url = None  
        state.detected_youtube_title = None
    return jsonify({"url": url, "title": title})

def update_subtitles_data(subtitles_data, actual_duration=0.1, source_url=""):
    """
    main.py 백그라운드 워커에서 번역이 완료되면 이 함수를 호출하여
    Flask 서버의 메모리 상태(자막 데이터, 동기화 정보)를 업데이트합니다.
    """
    state.subtitles = _normalize_subtitles(subtitles_data)
    state.subtitle_starts = [sub["start"] for sub in state.subtitles]
    normalized_duration = state.subtitles[-1]["end"] if state.subtitles else 0.1
    state.total_time = actual_duration if actual_duration > 0 else normalized_duration
    state.current_time = 0.0
    _clear_current_display_state()
    state.active_video_url = source_url if isinstance(source_url, str) else ""
    state.active_video_key = _extract_video_key(state.active_video_url)
    
    # 동기화 상태 변수들 초기화
    state.reset_sync()


def run_server():
    """
    Flask 서버를 5000 포트에서 실행합니다. 
    로깅 레벨을 ERROR로 낮춰 콘솔창을 깨끗하게 유지합니다.
    """
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(port=5000, use_reloader=False)
