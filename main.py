import threading
from whisper_engine import extract_original_subtitles 
from translator_engine import translate_subtitles 
from flask_server import run_server, state
from ui_app import SubtitleUI

def start_background_work(url, status_callback, on_complete_callback):
    def worker():
        try:
            # 1. 오디오 다운 및 원본 텍스트 추출 (Whisper)
            segments = extract_original_subtitles(url, status_callback)
            
            # 2. 다국어 일괄 번역 수행 (Translator)
            subtitles_data = translate_subtitles(segments, status_callback)
            
            # 3. Flask 서버 실행 및 데이터 주입
            flask_thread = threading.Thread(target=run_server, args=(subtitles_data,), daemon=True)
            flask_thread.start()
            
            # 4. UI를 자막 모드로 전환
            on_complete_callback()
        except Exception as e:
            print(f"❌ 오류 발생: {e}")
            status_callback(f"오류 발생: {e}", 0)

    threading.Thread(target=worker, daemon=True).start()

def get_current_texts():
    return state.current_texts

if __name__ == '__main__':
    ui = SubtitleUI(on_start_callback=start_background_work, 
                    get_texts_callback=get_current_texts)
    ui.run()