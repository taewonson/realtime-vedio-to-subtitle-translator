# Flask 서버, STT/번역 파이프라인, PC UI를 연결해 전체 자막 번역 시스템을 실행합니다.
import threading
from google_cloud_stt import extract_original_subtitles 
from google_cloud_translator import translate_subtitles 
from flask_server import run_server, update_subtitles_data, state
from ui_pyside import SubtitleUI

from dotenv import load_dotenv

# .env 파일에서 환경변수를 로드함 (GCP 인증키 경로, 포트 설정 등)
load_dotenv()


def _safe_print(message):
    """
    Windows 터미널 환경에서 백그라운드 스레드 실행 시 발생할 수 있는 
    UnicodeEncodeError를 방지하기 위한 안전한 출력 함수.
    """
    try:
        print(message)
    except UnicodeEncodeError:
        # 인코딩 오류 발생 시 ASCII로 강제 변환하여 출력 (프로세스 중단 방어)
        print(str(message).encode("ascii", errors="replace").decode("ascii"))


def _run_server_safe():
    """
    Flask 웹 서버를 안전하게 실행하는 래퍼 함수.
    이미 5000번 포트를 다른 프로세스가 사용 중일 경우의 예외 처리를 포함함.
    """
    try:
        run_server()
    except OSError as e:
        # 10048은 Windows 소켓에서 발생하는 '포트 이미 사용 중(Address already in use)' 에러 코드임
        if getattr(e, "winerror", None) != 10048:
            raise

def start_background_work(url, status_callback, on_complete_callback):
    """
    자막 추출(STT) 및 번역 작업을 UI 블로킹 없이 백그라운드 스레드에서 실행함.
    """
    def worker():
        try:
            # 1. 새 영상을 처리할 때 이전 영상의 자막이 남아 화면이 깜빡이는 현상을 방지하기 위해 타임라인 초기화
            update_subtitles_data([], 0.1, source_url=url)
            
            # 2. STT를 통해 오디오에서 원본(주로 영어) 자막 데이터 및 총 재생 시간 추출
            stt_result = extract_original_subtitles(url, status_callback)
            if isinstance(stt_result, tuple) and len(stt_result) == 2:
                segments, actual_duration = stt_result
            else:
                segments = stt_result
                actual_duration = segments[-1].end if segments else 0.1
            
            # 3. 추출된 원본 자막을 다국어로 번역 진행
            subtitles_data = translate_subtitles(segments, status_callback)
            
            # 4. 번역된 전체 데이터를 Flask 서버의 공유 상태값(State)으로 업데이트
            update_subtitles_data(subtitles_data, actual_duration, source_url=url)
            
            # 작업 성공 시 콜백 호출하여 UI 원상 복구 및 UDP 전송 루프 시작
            on_complete_callback(success=True)
        except Exception as e:
            # 작업 실패 시 오류 메시지를 출력하고 UI 알림창을 띄우기 위해 False 반환
            error_message = f"오류 발생: {e}"
            _safe_print(f"Error: {error_message}")
            status_callback(error_message, 0)
            on_complete_callback(success=False, message=error_message)

    # 데몬 스레드로 지정하여 메인 UI 종료 시 백그라운드 작업도 함께 강제 종료되도록 설정
    threading.Thread(target=worker, daemon=True).start()

def get_current_state():
    """
    PC UI 및 라즈베리파이(LCD)로의 UDP 전송에 필요한 현재 재생 상태 및 자막 데이터를 반환함.
    """
    return {
        "texts": state.current_texts,                 # 현재 재생 시간에 맞는 다국어 자막 딕셔너리
        "curr": state.current_time,                   # 현재 재생 시간(초)
        "total": state.total_time,                    # 영상의 전체 길이(초)
        "title": state.current_video_title,           # 현재 영상 제목
        "cue_start": state.current_cue_start,         # 현재 출력 중인 자막의 시작 시간
        "cue_end": state.current_cue_end,             # 현재 출력 중인 자막의 종료 시간
        "playback_mismatch": state.playback_mismatch, # 타겟 영상과 현재 재생 중인 영상이 다른지 여부
    }

if __name__ == '__main__':
    # 자막 추출 작업을 시작하기 전, Chrome 확장 프로그램으로부터 URL을 감지받을 수 있도록 Flask 서버를 먼저 띄움
    threading.Thread(target=_run_server_safe, daemon=True).start()

    try:
        # PC 자막 엔진 메인 UI 객체 생성 및 백그라운드 워커/상태 조회 콜백 연결
        ui = SubtitleUI(on_start_callback=start_background_work,
                        get_state_callback=get_current_state)
    except OSError as e:
        # UI 앱 내부의 UDP 명령 수신 포트(5006)가 이미 점유된 경우의 예외 처리
        if getattr(e, "winerror", None) == 10048:
            print("이미 실행 중인 인스턴스가 있어 포트(5006)를 사용할 수 없습니다. 기존 프로그램을 종료한 뒤 다시 실행하세요.")
            raise SystemExit(0)
        raise

    # UI 이벤트 메인 루프 실행
    ui.run()
