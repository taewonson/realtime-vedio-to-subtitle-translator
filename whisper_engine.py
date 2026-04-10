from faster_whisper import WhisperModel
import yt_dlp
import os
import glob

def extract_original_subtitles(youtube_url, status_callback=None):
    def update_status(msg, percent):
        print(msg)
        if status_callback:
            status_callback(msg, percent)

    update_status("\n[1/2] 유튜브 오디오 다운로드 중 (초고속 모드)...", 10)
    
    # 💡 최적화 1: mp3 변환(postprocessors)을 아예 삭제하여 시간 단축
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'temp_audio.%(ext)s', 
        'quiet': True
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])
        
    # 다운로드된 원본 파일명 찾기 (확장자가 m4a 또는 webm일 수 있음)
    audio_file = glob.glob("temp_audio.*")[0]
    
    update_status("[2/2] Whisper 모델 추출 중...", 30)
    model = WhisperModel("small", device="cpu", compute_type="int8")
    # gpu 사용 시 device="cuda"로 변경 
    # model = WhisperModel("tiny", device="cuda", compute_type="int8")
    
    # 💡 최적화 2: beam_size를 1로 낮춰서 연산 속도 2~3배 향상!
    segments, info = model.transcribe(audio_file, beam_size=1, vad_filter=True)
    
    segments = list(segments)
    
    # 작업이 끝난 파일 삭제
    if os.path.exists(audio_file):
        os.remove(audio_file)
        
    update_status("✅ 원본 자막 추출 초고속 완료!", 50)
    return segments