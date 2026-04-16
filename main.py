import threading
from google_cloud_stt import extract_original_subtitles 
from google_cloud_translator import translate_subtitles 
from flask_server import run_server, update_subtitles_data, state
from ui_app import SubtitleUI

from dotenv import load_dotenv

# 💡 환경변수 로드
load_dotenv()


def _run_server_safe():
    try:
        run_server()
    except OSError as e:
        # Another instance may already own port 5000.
        if getattr(e, "winerror", None) != 10048:
            raise

def start_background_work(url, status_callback, on_complete_callback):
    def worker():
        try:
            # Immediately reset timeline for new target video to avoid stale subtitle blinking.
            update_subtitles_data([], 0.1, source_url=url)
            stt_result = extract_original_subtitles(url, status_callback)
            if isinstance(stt_result, tuple) and len(stt_result) == 2:
                segments, actual_duration = stt_result
            else:
                segments = stt_result
                actual_duration = segments[-1].end if segments else 0.1
            
            subtitles_data = translate_subtitles(segments, status_callback)
            update_subtitles_data(subtitles_data, actual_duration, source_url=url)
            
            on_complete_callback()
        except Exception as e:
            print(f"❌ 오류 발생: {e}")
            status_callback(f"오류 발생: {e}", 0)

    threading.Thread(target=worker, daemon=True).start()

def get_current_state():
    return {
        "texts": state.current_texts,
        "curr": state.current_time,
        "total": state.total_time,
        "title": state.current_video_title,
        "cue_start": state.current_cue_start,
        "cue_end": state.current_cue_end,
        "playback_mismatch": state.playback_mismatch,
    }

if __name__ == '__main__':
    # URL detection endpoint must be available before subtitle extraction starts.
    threading.Thread(target=_run_server_safe, daemon=True).start()

    try:
        ui = SubtitleUI(on_start_callback=start_background_work,
                        get_state_callback=get_current_state)
    except OSError as e:
        if getattr(e, "winerror", None) == 10048:
            print("이미 실행 중인 인스턴스가 있어 포트(5006)를 사용할 수 없습니다. 기존 프로그램을 종료한 뒤 다시 실행하세요.")
            raise SystemExit(0)
        raise

    ui.run()