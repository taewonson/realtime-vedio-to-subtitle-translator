import threading
from whisper_engine import extract_original_subtitles 
from translator_engine import translate_subtitles 
from flask_server import run_server, state
from ui_app import SubtitleUI

def start_background_work(url, status_callback, on_complete_callback):
    def worker():
        try:
            # 💡 엔진에서 실제 영상 길이를 받아옵니다.
            segments, actual_duration = extract_original_subtitles(url, status_callback)
            subtitles_data = translate_subtitles(segments, status_callback)
            
            # 💡 플라스크 서버를 켤 때 실제 영상 길이도 같이 넘겨줍니다.
            flask_thread = threading.Thread(target=run_server, args=(subtitles_data, actual_duration), daemon=True)
            flask_thread.start()
            
            on_complete_callback()
        except Exception as e:
            print(f"❌ 오류 발생: {e}")
            status_callback(f"오류 발생: {e}", 0)

    threading.Thread(target=worker, daemon=True).start()

def get_current_state():
    return {
        "texts": state.current_texts,
        "curr": state.current_time,
        "total": state.total_time
    }

if __name__ == '__main__':
    ui = SubtitleUI(on_start_callback=start_background_work, 
                    get_state_callback=get_current_state)
    ui.run()