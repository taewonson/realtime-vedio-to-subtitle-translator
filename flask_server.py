from flask import Flask, request, jsonify
from flask_cors import CORS
from bisect import bisect_right
import logging
import time
from urllib.parse import parse_qs, urlparse

app = Flask(__name__)
CORS(app)

class SharedState:
    subtitles = []
    subtitle_starts = []
    current_texts = {}
    current_time = 0.0
    total_time = 0.1
    pending_command = None
    detected_youtube_url = None
    detected_youtube_title = None
    current_video_title = ""
    active_video_url = ""
    active_video_key = ""
    current_cue_start = None
    current_cue_end = None
    playback_mismatch = False
    consecutive_mismatch = 0
    consecutive_match = 0
    active_sync_sender = ""
    last_sync_received_at = 0.0

state = SharedState()
MERGE_GAP_SECONDS = 0.35
GAP_BRIDGE_SECONDS = 0.20
NEXT_CUE_EPSILON = 0.02
MISMATCH_THRESHOLD = 3
MATCH_RECOVERY_THRESHOLD = 2
SYNC_SENDER_STALE_SECONDS = 2.5


def _clear_current_display_state():
    state.current_texts = {}
    state.current_cue_start = None
    state.current_cue_end = None


def _extract_video_key(url):
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
    """Normalize subtitle timeline to reduce tiny-gap blinking and simplify runtime lookup."""
    if not isinstance(subtitles_data, list):
        return []

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

    normalized = []
    for cue in cleaned:
        if not normalized:
            normalized.append(cue)
            continue

        prev = normalized[-1]
        gap = cue["start"] - prev["end"]
        same_text = prev["texts"] == cue["texts"]

        # Merge only when texts are actually the same.
        # Merging different texts can swallow later subtitles.
        if same_text and gap <= MERGE_GAP_SECONDS:
            prev["end"] = max(prev["end"], cue["end"])
            prev["start"] = min(prev["start"], cue["start"])
        else:
            # If different cues overlap because of STT timing jitter, clamp the new start.
            if cue["start"] < prev["end"]:
                cue = {"start": prev["end"], "end": cue["end"], "texts": cue["texts"]}
                if cue["end"] <= cue["start"]:
                    continue
            normalized.append(cue)

    # Bridge tiny gaps in the normalized timeline to avoid flicker without
    # runtime subtitle hold heuristics near cue boundaries.
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

            # Debounced mismatch detection:
            # - Treat missing keys as neutral and reset debounce counters
            # - Require consecutive mismatches to enter overlay
            # - Require consecutive matches to recover
            if active_key:
                if not sync_key:
                    # Ignore transient/no-url sync inputs while a target video is active.
                    state.consecutive_mismatch = 0
                    state.consecutive_match = 0
                    return jsonify({"status": "success"})

                if sync_key == active_key:
                    # Once a sender is selected, ignore other senders until the owner goes stale.
                    if safe_sender_id:
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
                    state.consecutive_mismatch += 1
                    state.consecutive_match = 0
                    if state.consecutive_mismatch >= MISMATCH_THRESHOLD:
                        state.playback_mismatch = True

                    # Never apply timeline updates from another video source.
                    if state.playback_mismatch:
                        _clear_current_display_state()
                    return jsonify({"status": "success"})
            else:
                # No active target video yet: ignore sync updates.
                state.consecutive_mismatch = 0
                state.consecutive_match = 0
                state.playback_mismatch = False
                state.active_sync_sender = ""
                state.last_sync_received_at = 0.0
                return jsonify({"status": "success"})

            if state.playback_mismatch:
                _clear_current_display_state()
                return jsonify({"status": "success"})

            safe_time = float(current_time)
            if safe_time < 0:
                safe_time = 0.0
            if state.total_time > 0:
                safe_time = min(safe_time, state.total_time)

            state.current_time = safe_time
            idx = bisect_right(state.subtitle_starts, safe_time) - 1
            if idx >= 0:
                sub = state.subtitles[idx]
                if safe_time <= sub["end"]:
                    state.current_texts = sub["texts"]
                    state.current_cue_start = float(sub["start"])
                    state.current_cue_end = float(sub["end"])
                else:
                    _clear_current_display_state()
            else:
                _clear_current_display_state()
    except (TypeError, KeyError, ValueError):
        pass
    
    return jsonify({"status": "success"})

@app.route('/get_command')
def get_command():
    if state.pending_command:
        cmd = state.pending_command
        state.pending_command = None 
        return jsonify(cmd)
    return jsonify({"command": None})

@app.route('/detect_url', methods=['POST'])
def detect_url():
    """Receive YouTube URL from Chrome extension"""
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
    """Retrieve detected YouTube URL for UI polling"""
    url = state.detected_youtube_url
    title = state.detected_youtube_title
    if url:
        state.detected_youtube_url = None  # Reset after retrieving to avoid duplicate
        state.detected_youtube_title = None
    return jsonify({"url": url, "title": title})

def update_subtitles_data(subtitles_data, actual_duration=0.1, source_url=""):
    """Update subtitle timeline/state while Flask server keeps running."""
    state.subtitles = _normalize_subtitles(subtitles_data)
    state.subtitle_starts = [sub["start"] for sub in state.subtitles]
    state.total_time = actual_duration if actual_duration > 0 else (subtitles_data[-1]['end'] if subtitles_data else 0.1)
    state.current_time = 0.0
    _clear_current_display_state()
    state.active_video_url = source_url if isinstance(source_url, str) else ""
    state.active_video_key = _extract_video_key(state.active_video_url)
    state.playback_mismatch = False
    state.consecutive_mismatch = 0
    state.consecutive_match = 0
    state.active_sync_sender = ""
    state.last_sync_received_at = 0.0


def run_server():
    """Run Flask server once at app startup."""
    
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(port=5000, use_reloader=False)